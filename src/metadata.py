import os
import sqlite3
import psycopg2
from psycopg2.extras import RealDictCursor
import logging
import threading
from src.config import settings

logger = logging.getLogger("ontoforge.metadata")
logging.basicConfig(level=logging.INFO)

class MetadataStore:
    def __init__(self):
        self.is_sqlite = False
        self.conn = None
        self.lock = threading.Lock()
        self._init_db()

    def _get_pg_connection(self):
        return psycopg2.connect(
            host=settings.POSTGRES_HOST,
            port=settings.POSTGRES_PORT,
            database=settings.POSTGRES_DB,
            user=settings.POSTGRES_USER,
            password=settings.POSTGRES_PASSWORD,
            connect_timeout=3
        )

    def _get_sqlite_connection(self):
        db_path = os.path.join(settings.DATA_DIR, "metadata.db")
        os.makedirs(settings.DATA_DIR, exist_ok=True)
        return sqlite3.connect(db_path, check_same_thread=False)

    def _init_db(self):
        if not settings.USE_SQLITE_FALLBACK:
            try:
                self.conn = self._get_pg_connection()
                self.is_sqlite = False
                logger.info("Connected to PostgreSQL metadata store.")
            except Exception as e:
                logger.error(f"Failed to connect to PostgreSQL: {e}")
                raise e
        else:
            try:
                self.conn = self._get_pg_connection()
                self.is_sqlite = False
                logger.info("Connected to PostgreSQL metadata store.")
            except Exception as e:
                logger.warning(f"PostgreSQL connection failed ({e}). Falling back to SQLite.")
                self.conn = self._get_sqlite_connection()
                self.is_sqlite = True
                logger.info("Connected to SQLite metadata store.")
        
        self._create_tables()

    def _create_tables(self):
        cursor = self.conn.cursor()
        if self.is_sqlite:
            # SQLite schemas
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS branches (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                status TEXT DEFAULT 'active' CHECK (status IN ('active', 'validated', 'merged', 'aborted')),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                merged_at TIMESTAMP,
                parent_branch_id TEXT,
                bytes_written INTEGER DEFAULT 0,
                total_dataset_bytes INTEGER NOT NULL,
                FOREIGN KEY (parent_branch_id) REFERENCES branches(id)
            );
            """)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS query_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                branch_id TEXT,
                query_text TEXT NOT NULL,
                query_type TEXT NOT NULL,
                is_validated INTEGER DEFAULT 1,
                validation_error TEXT,
                execution_time_ms REAL,
                bytes_written_delta INTEGER DEFAULT 0,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (branch_id) REFERENCES branches(id)
            );
            """)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS telemetry_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                decs REAL NOT NULL,
                zcbe REAL NOT NULL,
                active_branches INTEGER NOT NULL,
                total_branches_merged INTEGER NOT NULL
            );
            """)
            # Seed main branch in branches
            cursor.execute("""
            INSERT OR IGNORE INTO branches (id, name, status, total_dataset_bytes)
            VALUES ('main', 'Main Branch', 'validated', 0);
            """)
        else:
            # PostgreSQL schemas
            cursor.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'branch_status') THEN
                    CREATE TYPE branch_status AS ENUM ('active', 'validated', 'merged', 'aborted');
                END IF;
            END$$;
            """)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS branches (
                id VARCHAR(64) PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                status branch_status DEFAULT 'active',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                merged_at TIMESTAMP WITH TIME ZONE,
                parent_branch_id VARCHAR(64) REFERENCES branches(id),
                bytes_written BIGINT DEFAULT 0,
                total_dataset_bytes BIGINT NOT NULL
            );
            """)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS query_log (
                id SERIAL PRIMARY KEY,
                branch_id VARCHAR(64) REFERENCES branches(id),
                query_text TEXT NOT NULL,
                query_type VARCHAR(32) NOT NULL,
                is_validated BOOLEAN DEFAULT TRUE,
                validation_error TEXT,
                execution_time_ms DOUBLE PRECISION,
                bytes_written_delta BIGINT DEFAULT 0,
                timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
            """)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS telemetry_metrics (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                decs DOUBLE PRECISION NOT NULL,
                zcbe DOUBLE PRECISION NOT NULL,
                active_branches INTEGER NOT NULL,
                total_branches_merged INTEGER NOT NULL
            );
            """)
            cursor.execute("""
            INSERT INTO branches (id, name, status, total_dataset_bytes)
            VALUES ('main', 'Main Branch', 'validated', 0)
            ON CONFLICT (id) DO NOTHING;
            """)
        self.conn.commit()

    def execute(self, query, params=None):
        with self.lock:
            cursor = None
            try:
                if not self.is_sqlite:
                    try:
                        cursor = self.conn.cursor()
                        cursor.execute("SELECT 1")
                        cursor.close()
                    except Exception:
                        logger.warning("PostgreSQL connection stale. Reconnecting...")
                        self.conn = self._get_pg_connection()
                
                if self.is_sqlite:
                    cursor = self.conn.cursor()
                    if params:
                        cursor.execute(query, params)
                    else:
                        cursor.execute(query)
                    self.conn.commit()
                    if query.strip().upper().startswith("SELECT"):
                        columns = [d[0] for d in cursor.description]
                        return [dict(zip(columns, row)) for row in cursor.fetchall()]
                    return cursor.lastrowid
                else:
                    cursor = self.conn.cursor(cursor_factory=RealDictCursor)
                    if params:
                        cursor.execute(query, params)
                    else:
                        cursor.execute(query)
                    self.conn.commit()
                    if query.strip().upper().startswith("SELECT"):
                        return [dict(row) for row in cursor.fetchall()]
                    return cursor.rowcount
            except Exception as e:
                if self.conn:
                    self.conn.rollback()
                logger.error(f"Database error executing query: {query} with params {params}. Error: {e}")
                raise e
            finally:
                if cursor:
                    cursor.close()

    def register_branch(self, branch_id, name, parent_branch_id, total_dataset_bytes):
        # Clean up existing entries to allow clean re-registration in tests/restarts
        del_queries = [
            ("DELETE FROM query_log WHERE branch_id = %s", "DELETE FROM query_log WHERE branch_id = ?"),
            ("DELETE FROM branches WHERE id = %s", "DELETE FROM branches WHERE id = ?")
        ]
        for pg_q, sq_q in del_queries:
            q = sq_q if self.is_sqlite else pg_q
            self.execute(q, (branch_id,))

        query = """
        INSERT INTO branches (id, name, status, parent_branch_id, total_dataset_bytes)
        VALUES (%s, %s, 'active', %s, %s)
        """ if not self.is_sqlite else """
        INSERT INTO branches (id, name, status, parent_branch_id, total_dataset_bytes)
        VALUES (?, ?, 'active', ?, ?)
        """
        self.execute(query, (branch_id, name, parent_branch_id, total_dataset_bytes))

    def update_branch_status(self, branch_id, status, bytes_written=None):
        if bytes_written is not None:
            query = """
            UPDATE branches SET status = %s, bytes_written = %s, merged_at = CURRENT_TIMESTAMP WHERE id = %s
            """ if not self.is_sqlite else """
            UPDATE branches SET status = ?, bytes_written = ?, merged_at = CURRENT_TIMESTAMP WHERE id = ?
            """
            self.execute(query, (status, bytes_written, branch_id))
        else:
            query = """
            UPDATE branches SET status = %s WHERE id = %s
            """ if not self.is_sqlite else """
            UPDATE branches SET status = ?, id = ?
            """
            if self.is_sqlite:
                self.execute("UPDATE branches SET status = ? WHERE id = ?", (status, branch_id))
            else:
                self.execute("UPDATE branches SET status = %s WHERE id = %s", (status, branch_id))

    def log_query(self, branch_id, query_text, query_type, is_validated=True, validation_error=None, execution_time_ms=0.0, bytes_written_delta=0):
        validated_val = is_validated if not self.is_sqlite else (1 if is_validated else 0)
        query = """
        INSERT INTO query_log (branch_id, query_text, query_type, is_validated, validation_error, execution_time_ms, bytes_written_delta)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """ if not self.is_sqlite else """
        INSERT INTO query_log (branch_id, query_text, query_type, is_validated, validation_error, execution_time_ms, bytes_written_delta)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """
        self.execute(query, (branch_id, query_text, query_type, validated_val, validation_error, execution_time_ms, bytes_written_delta))

    def log_telemetry(self, decs, zcbe, active_branches, total_branches_merged):
        query = """
        INSERT INTO telemetry_metrics (decs, zcbe, active_branches, total_branches_merged)
        VALUES (%s, %s, %s, %s)
        """ if not self.is_sqlite else """
        INSERT INTO telemetry_metrics (decs, zcbe, active_branches, total_branches_merged)
        VALUES (?, ?, ?, ?)
        """
        self.execute(query, (decs, zcbe, active_branches, total_branches_merged))

    def get_latest_metrics(self):
        query = "SELECT * FROM telemetry_metrics ORDER BY timestamp DESC LIMIT 1"
        res = self.execute(query)
        return res[0] if res else None

# Singleton metadata store instance
db = MetadataStore()
