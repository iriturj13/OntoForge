import os
import shutil
import duckdb
import sqlglot
from sqlglot import exp
import logging
import threading
from src.config import settings
from src.metadata import db

logger = logging.getLogger("ontoforge.branch_manager")

class OntoForgeBranchManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.data_dir = settings.DATA_DIR
        self.primary_dir = os.path.join(self.data_dir, "primary")
        self.branches_dir = os.path.join(self.data_dir, "branches")
        os.makedirs(self.primary_dir, exist_ok=True)
        os.makedirs(self.branches_dir, exist_ok=True)
        self.primary_db_path = os.path.join(self.primary_dir, "primary.db")
        self._init_primary_db()

    def _init_primary_db(self):
        # Open connection to check for tables; this creates the file if it does not exist
        conn = duckdb.connect(self.primary_db_path)
        try:
            # Check if the 'orders' table is present in the main schema
            tables = conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' AND table_name = 'orders';").fetchall()
            if not tables:
                logger.info("Initializing primary database tables...")
                conn.execute("DROP TABLE IF EXISTS customers;")
                conn.execute("DROP TABLE IF EXISTS orders;")
                
                conn.execute("""
                CREATE TABLE customers (
                    id INTEGER PRIMARY KEY,
                    name VARCHAR,
                    email VARCHAR,
                    country VARCHAR
                );
                """)
                conn.execute("""
                CREATE TABLE orders (
                    id INTEGER PRIMARY KEY,
                    customer_id INTEGER,
                    order_date TIMESTAMP,
                    status VARCHAR,
                    amount DECIMAL(10, 2)
                );
                """)
                conn.execute("""
                CREATE TABLE large_historical_data (
                    id INTEGER PRIMARY KEY,
                    sensor_name VARCHAR,
                    reading DOUBLE,
                    timestamp BIGINT,
                    payload VARCHAR
                );
                """)
                
                conn.execute("""
                INSERT INTO customers VALUES 
                (1, 'Alice Smith', 'alice@example.com', 'USA'),
                (2, 'Bob Jones', 'bob@example.com', 'Canada');
                """)
                conn.execute("""
                INSERT INTO orders VALUES 
                (101, 1, '2026-07-01 10:00:00', 'completed', 150.00),
                (102, 2, '2026-07-02 11:30:00', 'pending', 45.50),
                (103, 1, '2026-07-03 14:15:00', 'completed', 99.99);
                """)
                # Generate 350,000 rows of mock readings with uncompressible MD5 payloads
                conn.execute("""
                INSERT INTO large_historical_data
                SELECT 
                    range AS id, 
                    'Sensor_' || (range % 10) AS sensor_name, 
                    random() * 100 AS reading, 
                    1770000000 + range AS timestamp,
                    md5(range::varchar) || md5((range+1)::varchar) || md5((range+2)::varchar) AS payload
                FROM range(350000);
                """)
                conn.execute("CHECKPOINT;")
                logger.info("Primary database initialized and seeded successfully with large historical datasets.")
        finally:
            conn.close()

    def get_primary_size(self) -> int:
        if os.path.exists(self.primary_db_path):
            return os.path.getsize(self.primary_db_path)
        return 0

    def create_branch(self, branch_id: str, name: str, parent_branch_id: str = "main") -> str:
        with self.lock:
            branch_db_path = os.path.join(self.branches_dir, f"{branch_id}.db")
            
            if os.path.exists(branch_db_path):
                os.remove(branch_db_path)
                
            conn = duckdb.connect(branch_db_path)
            
            # Attach primary read-only using 'primary_lake' to avoid reserved keyword conflicts
            conn.execute(f"ATTACH '{self.primary_db_path}' AS primary_lake (READ_ONLY);")
            
            # Discover primary tables using 'table_catalog' for attached database name
            tables = conn.execute("SELECT table_name FROM information_schema.tables WHERE table_catalog = 'primary_lake' AND table_type = 'BASE TABLE';").fetchall()
            
            # Create views in the branch pointing to primary tables
            for (table_name,) in tables:
                conn.execute(f"CREATE VIEW {table_name} AS SELECT * FROM primary_lake.{table_name};")
                
            conn.execute("CHECKPOINT;")
            conn.close()
            
            # Register branch in metadata store
            primary_size = self.get_primary_size()
            db.register_branch(branch_id, name, parent_branch_id, primary_size)
            logger.info(f"Branch '{branch_id}' created successfully.")
            return branch_db_path

    def get_branch_db_path(self, branch_id: str) -> str:
        if branch_id == "main":
            return self.primary_db_path
        return os.path.join(self.branches_dir, f"{branch_id}.db")

    def _get_mutated_tables(self, query_text: str) -> list[str]:
        try:
            parsed = sqlglot.parse_one(query_text)
            if isinstance(parsed, (exp.Update, exp.Delete, exp.Insert, exp.AlterTable, exp.Drop)):
                tables = [table.name for table in parsed.find_all(exp.Table)]
                return list(set(tables))
        except Exception as e:
            logger.error(f"Failed to parse query '{query_text}': {e}")
        return []

    def execute_query(self, branch_id: str, query_text: str) -> tuple[list[dict], int]:
        with self.lock:
            db_path = self.get_branch_db_path(branch_id)
            if not os.path.exists(db_path) and branch_id != "main":
                raise ValueError(f"Branch '{branch_id}' does not exist.")

            size_before = os.path.getsize(db_path) if branch_id != "main" else 0
            conn = duckdb.connect(db_path)
            
            try:
                if branch_id != "main":
                    # Ensure primary is attached
                    try:
                        conn.execute(f"ATTACH '{self.primary_db_path}' AS primary_lake (READ_ONLY);")
                    except Exception:
                        pass
                    
                    # Check for table mutations and trigger copy-on-write
                    mutated_tables = self._get_mutated_tables(query_text)
                    for table in mutated_tables:
                        is_view = conn.execute(f"""
                        SELECT view_name FROM duckdb_views 
                        WHERE lower(view_name) = '{table.lower()}'
                        """).fetchall()
                        
                        if is_view:
                            logger.info(f"Materializing copy-on-write table '{table}' for branch '{branch_id}'")
                            conn.execute(f"DROP VIEW {table};")
                            conn.execute(f"CREATE TABLE {table} AS SELECT * FROM primary_lake.{table};")
                
                cursor = conn.execute(query_text)
                
                results = []
                if cursor.description:
                    columns = [col[0] for col in cursor.description]
                    results = [dict(zip(columns, row)) for row in cursor.fetchall()]
                
                conn.execute("CHECKPOINT;")
                conn.close()
                
                size_after = os.path.getsize(db_path) if branch_id != "main" else 0
                bytes_written_delta = max(0, size_after - size_before)
                
                if branch_id != "main" and bytes_written_delta > 0:
                    db.execute(
                        "UPDATE branches SET bytes_written = bytes_written + %s WHERE id = %s" if not db.is_sqlite else
                        "UPDATE branches SET bytes_written = bytes_written + ? WHERE id = ?",
                        (bytes_written_delta, branch_id)
                    )
                    
                return results, bytes_written_delta
                
            except Exception as e:
                try:
                    conn.close()
                except Exception:
                    pass
                raise e

    def merge_branch(self, branch_id: str) -> None:
        with self.lock:
            if branch_id == "main":
                raise ValueError("Cannot merge main branch into itself.")
                
            branch_db_path = self.get_branch_db_path(branch_id)
            if not os.path.exists(branch_db_path):
                raise ValueError(f"Branch '{branch_id}' does not exist.")
                
            primary_conn = duckdb.connect(self.primary_db_path)
            primary_conn.execute(f"ATTACH '{branch_db_path}' AS branch;")
            
            # Discover tables in the branch database using table_catalog
            mutated_tables = primary_conn.execute("""
                SELECT table_name FROM information_schema.tables 
                WHERE table_catalog = 'branch' AND table_type = 'BASE TABLE';
            """).fetchall()
            
            try:
                for (table_name,) in mutated_tables:
                    logger.info(f"Merging table '{table_name}' from branch '{branch_id}' to primary...")
                    primary_conn.execute(f"DROP TABLE IF EXISTS {table_name};")
                    primary_conn.execute(f"CREATE TABLE {table_name} AS SELECT * FROM branch.{table_name};")
                    
                primary_conn.execute("CHECKPOINT;")
                primary_conn.close()
                
                bytes_written = os.path.getsize(branch_db_path)
                db.update_branch_status(branch_id, "merged", bytes_written)
                
                # Close the file, then remove
                os.remove(branch_db_path)
                logger.info(f"Branch '{branch_id}' merged and cleaned up successfully.")
                
            except Exception as e:
                try:
                    primary_conn.close()
                except Exception:
                    pass
                raise e
