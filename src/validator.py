import os
import re
import httpx
import logging
import sqlglot
from sqlglot import exp
from src.config import settings

logger = logging.getLogger("ontoforge.validator")

class ASTSemanticValidator:
    def __init__(self):
        self.cubejs_url = settings.CUBEJS_URL
        self.cubejs_secret = settings.CUBEJS_API_SECRET
        self.schema_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cube", "schema")

    def _fetch_cube_meta_from_api(self) -> dict:
        url = f"{self.cubejs_url}/cubejs-api/v1/meta"
        headers = {"Authorization": self.cubejs_secret}
        try:
            logger.info(f"Fetching Cube.js metadata from {url}")
            response = httpx.get(url, headers=headers, timeout=2.0)
            if response.status_code == 200:
                return response.json()
            else:
                logger.warning(f"Cube.js API returned code {response.status_code}: {response.text}")
        except Exception as e:
            logger.warning(f"Could not connect to Cube.js API at {url}: {e}")
        return {}

    def _parse_schema_files_fallback(self) -> dict:
        logger.info("Scanning local Cube.js JS schema files for fallback metadata...")
        cubes = []
        if not os.path.exists(self.schema_dir):
            self.schema_dir = os.path.join(settings.DATA_DIR, "..", "cube", "schema")
            self.schema_dir = os.path.abspath(self.schema_dir)
            
        if not os.path.exists(self.schema_dir):
            return self._get_hardcoded_metadata()

        def extract_block(text: str, start_keyword: str) -> str:
            idx = text.find(start_keyword)
            if idx == -1:
                return ""
            brace_idx = text.find("{", idx + len(start_keyword))
            if brace_idx == -1:
                return ""
            count = 1
            i = brace_idx + 1
            while i < len(text) and count > 0:
                if text[i] == "{":
                    count += 1
                elif text[i] == "}":
                    count -= 1
                i += 1
            return text[brace_idx:i]

        try:
            for filename in os.listdir(self.schema_dir):
                if filename.endswith(".js"):
                    filepath = os.path.join(self.schema_dir, filename)
                    with open(filepath, "r", encoding="utf-8") as f:
                        content = f.read()
                        
                    cube_match = re.search(r"cube\(\s*[`'\"](\w+)[`'\"]\s*,", content)
                    if cube_match:
                        cube_name = cube_match.group(1)
                        dimensions = []
                        measures = []
                        
                        dim_block = extract_block(content, "dimensions:")
                        if dim_block:
                            for line in dim_block.splitlines():
                                match = re.match(r"^\s{4}(\w+)\s*:\s*\{", line)
                                if match:
                                    dimensions.append({"name": f"{cube_name}.{match.group(1)}"})
                                    
                        meas_block = extract_block(content, "measures:")
                        if meas_block:
                            for line in meas_block.splitlines():
                                match = re.match(r"^\s{4}(\w+)\s*:\s*\{", line)
                                if match:
                                    measures.append({"name": f"{cube_name}.{match.group(1)}"})
                                
                        cubes.append({
                            "name": cube_name,
                            "dimensions": dimensions,
                            "measures": measures
                        })
            return {"cubes": cubes}
        except Exception as e:
            logger.error(f"Failed to parse schema files: {e}")
            return self._get_hardcoded_metadata()

    def _get_hardcoded_metadata(self) -> dict:
        return {
            "cubes": [
                {
                    "name": "orders",
                    "dimensions": [
                        {"name": "orders.id"},
                        {"name": "orders.status"},
                        {"name": "orders.order_date"}
                    ],
                    "measures": [
                        {"name": "orders.count"},
                        {"name": "orders.total_amount"}
                    ]
                },
                {
                    "name": "customers",
                    "dimensions": [
                        {"name": "customers.id"},
                        {"name": "customers.name"},
                        {"name": "customers.email"},
                        {"name": "customers.country"}
                    ],
                    "measures": [
                        {"name": "customers.count"}
                    ]
                }
            ]
        }

    def get_semantic_schema(self) -> dict:
        meta = self._fetch_cube_meta_from_api()
        if not meta or "cubes" not in meta:
            meta = self._parse_schema_files_fallback()
        return meta

    def extract_mutations(self, query_text: str) -> list[tuple[str, str, str]]:
        mutations = []
        try:
            parsed = sqlglot.parse_one(query_text)
            if isinstance(parsed, exp.AlterTable):
                table_name = parsed.this.name
                actions = parsed.args.get("actions", [])
                for action in actions:
                    if isinstance(action, exp.Drop):
                        if action.args.get("kind") == "COLUMN" or "column" in str(action).lower():
                            col_name = action.this.name if hasattr(action.this, "name") else str(action.this)
                            mutations.append((table_name.lower(), col_name.lower(), "DROP_COLUMN"))
                    elif isinstance(action, exp.AlterColumn):
                        col_name = action.this.name if hasattr(action.this, "name") else str(action.this)
                        mutations.append((table_name.lower(), col_name.lower(), "ALTER_COLUMN"))
                    elif isinstance(action, exp.RenameColumn):
                        col_name = action.this.name if hasattr(action.this, "name") else str(action.this)
                        mutations.append((table_name.lower(), col_name.lower(), "RENAME_COLUMN"))
            elif isinstance(parsed, exp.Drop):
                if parsed.args.get("kind") == "TABLE" or "table" in str(parsed).lower():
                    table_name = parsed.this.name if hasattr(parsed.this, "name") else str(parsed.this)
                    mutations.append((table_name.lower(), "*", "DROP_TABLE"))
        except Exception as e:
            logger.warning(f"sqlglot couldn't parse mutation in query: {e}. Trying regex fallback.")
            
        if not mutations:
            drop_col_match = re.search(r"alter\s+table\s+(\w+)\s+drop\s+(?:column\s+)?(\w+)", query_text, re.IGNORECASE)
            if drop_col_match:
                mutations.append((drop_col_match.group(1).lower(), drop_col_match.group(2).lower(), "DROP_COLUMN"))
            
            rename_col_match = re.search(r"alter\s+table\s+(\w+)\s+rename\s+(?:column\s+)?(\w+)\s+to\s+(\w+)", query_text, re.IGNORECASE)
            if rename_col_match:
                mutations.append((rename_col_match.group(1).lower(), rename_col_match.group(2).lower(), "RENAME_COLUMN"))
                
            alter_col_match = re.search(r"alter\s+table\s+(\w+)\s+alter\s+(?:column\s+)?(\w+)", query_text, re.IGNORECASE)
            if alter_col_match:
                mutations.append((alter_col_match.group(1).lower(), alter_col_match.group(2).lower(), "ALTER_COLUMN"))

            drop_table_match = re.search(r"drop\s+table\s+(?:if\s+exists\s+)?(\w+)", query_text, re.IGNORECASE)
            if drop_table_match:
                mutations.append((drop_table_match.group(1).lower(), "*", "DROP_TABLE"))

        return mutations

    def validate_query(self, query_text: str) -> tuple[bool, str | None]:
        mutations = self.extract_mutations(query_text)
        if not mutations:
            return True, None

        meta = self.get_semantic_schema()
        cubes = meta.get("cubes", [])

        for table, col, m_type in mutations:
            if m_type == "DROP_TABLE":
                for cube in cubes:
                    if cube["name"].lower() == table:
                        return False, f"Semantic Violation: Table '{table}' is used by Cube '{cube['name']}' and cannot be dropped."
            else:
                target_field = f"{table}.{col}"
                for cube in cubes:
                    if cube["name"].lower() == table:
                        for dim in cube.get("dimensions", []):
                            if dim["name"].lower() == target_field:
                                return False, f"Semantic Violation: Column '{col}' in table '{table}' is a dimension in Cube.js ('{dim['name']}') and cannot be modified/dropped."
                        for meas in cube.get("measures", []):
                            if meas["name"].lower() == target_field:
                                return False, f"Semantic Violation: Column '{col}' in table '{table}' is used in measure '{meas['name']}' and cannot be modified/dropped."
                                
        return True, None
