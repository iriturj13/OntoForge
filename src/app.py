import time
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Header, Query, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from src.config import settings
from src.metadata import db
from src.branch_manager import OntoForgeBranchManager
from src.validator import ASTSemanticValidator

app = FastAPI(
    title="OntoForge: Context-Aware Database Branching & Governance Plane",
    description="API gateway to validate, isolate, and benchmark database mutations under AI agent workloads."
)

branch_manager = OntoForgeBranchManager()
validator = ASTSemanticValidator()

class QueryRequest(BaseModel):
    query_text: str
    branch_id: str = "main"

class BranchRequest(BaseModel):
    branch_id: str
    name: str
    parent_branch_id: str = "main"

def get_query_type(query_text: str) -> str:
    qt = query_text.strip().upper()
    if qt.startswith("SELECT"):
        return "SELECT"
    if qt.startswith("INSERT"):
        return "INSERT"
    if qt.startswith("UPDATE"):
        return "UPDATE"
    if qt.startswith("DELETE"):
        return "DELETE"
    if qt.startswith("ALTER") or qt.startswith("DROP") or qt.startswith("CREATE"):
        return "DDL"
    return "UNKNOWN"

def calculate_current_telemetry() -> tuple[float, float, int, int]:
    first_branch = db.execute("SELECT MIN(created_at) as first_time FROM branches WHERE id != 'main'")
    total_merged_res = db.execute("SELECT COUNT(*) as cnt FROM branches WHERE status = 'merged'")
    
    total_merged = total_merged_res[0]['cnt'] if total_merged_res else 0
    decs = 0.0
    
    if first_branch and first_branch[0]['first_time']:
        first_time_str = first_branch[0]['first_time']
        if isinstance(first_time_str, str):
            try:
                cleaned_time = first_time_str.split('+')[0].split('Z')[0]
                if '.' in cleaned_time:
                    first_time = datetime.strptime(cleaned_time, "%Y-%m-%d %H:%M:%S.%f")
                else:
                    first_time = datetime.strptime(cleaned_time, "%Y-%m-%d %H:%M:%S")
                first_time = first_time.replace(tzinfo=timezone.utc)
            except Exception:
                first_time = datetime.now(timezone.utc)
        else:
            first_time = first_time_str
            
        elapsed_seconds = (datetime.now(timezone.utc) - first_time).total_seconds()
        elapsed_hours = max(elapsed_seconds, 1.0) / 3600.0
        decs = total_merged / elapsed_hours
        
    stats = db.execute("""
        SELECT 
            COALESCE(SUM(bytes_written), 0) as total_written,
            COALESCE(SUM(total_dataset_bytes), 0) as total_dataset
        FROM branches 
        WHERE id != 'main'
    """)
    
    zcbe = 1.0
    if stats and stats[0]['total_dataset'] > 0:
        total_written = stats[0]['total_written']
        total_dataset = stats[0]['total_dataset']
        zcbe = 1.0 - (float(total_written) / float(total_dataset))
        
    active_res = db.execute("SELECT COUNT(*) as cnt FROM branches WHERE status = 'active'")
    active_branches = active_res[0]['cnt'] if active_res else 0
    
    db.log_telemetry(decs, zcbe, active_branches, total_merged)
    
    return decs, zcbe, active_branches, total_merged

@app.post("/queries")
def execute_sql(
    request: QueryRequest,
    x_branch_id: str = Header(default=None, alias="X-Branch-Id")
):
    branch_id = x_branch_id or request.branch_id
    query_text = request.query_text
    query_type = get_query_type(query_text)
    
    is_validated, validation_error = validator.validate_query(query_text)
    
    if not is_validated and branch_id == "main":
        db.log_query(
            branch_id=branch_id,
            query_text=query_text,
            query_type=query_type,
            is_validated=False,
            validation_error=validation_error,
            execution_time_ms=0.0,
            bytes_written_delta=0
        )
        raise HTTPException(
            status_code=400,
            detail={
                "status": "rejected",
                "reason": "Direct schema mutations on 'main' violating semantic layer are blocked.",
                "validation_error": validation_error
            }
        )
        
    start_time = time.perf_counter()
    try:
        results, bytes_written_delta = branch_manager.execute_query(branch_id, query_text)
        execution_time_ms = (time.perf_counter() - start_time) * 1000.0
        
        db.log_query(
            branch_id=branch_id,
            query_text=query_text,
            query_type=query_type,
            is_validated=is_validated,
            validation_error=validation_error,
            execution_time_ms=execution_time_ms,
            bytes_written_delta=bytes_written_delta
        )
        
        calculate_current_telemetry()
        
        return {
            "status": "success",
            "branch_id": branch_id,
            "query_type": query_type,
            "is_validated": is_validated,
            "validation_error": validation_error,
            "execution_time_ms": execution_time_ms,
            "bytes_written_delta": bytes_written_delta,
            "results": results
        }
    except Exception as e:
        execution_time_ms = (time.perf_counter() - start_time) * 1000.0
        db.log_query(
            branch_id=branch_id,
            query_text=query_text,
            query_type=query_type,
            is_validated=is_validated,
            validation_error=str(e),
            execution_time_ms=execution_time_ms,
            bytes_written_delta=0
        )
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/branches")
def create_branch(request: BranchRequest):
    try:
        branch_manager.create_branch(
            branch_id=request.branch_id,
            name=request.name,
            parent_branch_id=request.parent_branch_id
        )
        calculate_current_telemetry()
        return {
            "status": "success",
            "message": f"Branch '{request.branch_id}' initialized successfully.",
            "branch_id": request.branch_id
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/branches/{branch_id}/merge")
def merge_branch(branch_id: str):
    fail_query = "SELECT COUNT(*) as cnt FROM query_log WHERE branch_id = %s AND is_validated = FALSE" if not db.is_sqlite else "SELECT COUNT(*) as cnt FROM query_log WHERE branch_id = ? AND is_validated = 0"
    failures = db.execute(fail_query, (branch_id,))
    
    if failures and failures[0]['cnt'] > 0:
        raise HTTPException(
            status_code=400,
            detail={
                "status": "rejected",
                "reason": f"Cannot merge branch '{branch_id}' containing {failures[0]['cnt']} semantic validation violations."
            }
        )
        
    try:
        branch_manager.merge_branch(branch_id)
        calculate_current_telemetry()
        return {
            "status": "success",
            "message": f"Branch '{branch_id}' merged into main successfully."
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/telemetry")
def get_telemetry():
    decs, zcbe, active_branches, total_merged = calculate_current_telemetry()
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "metrics": {
            "decs_branches_per_hour": decs,
            "zcbe_efficiency": zcbe,
            "active_branches": active_branches,
            "total_branches_merged": total_merged
        }
    }

@app.get("/cubejs-api/v1/meta")
def mock_cubejs_meta(authorization: str = Header(default=None, alias="Authorization")):
    if authorization != settings.CUBEJS_API_SECRET:
         return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    return validator._get_hardcoded_metadata()

@app.get("/")
def read_root():
    return {"status": "ok", "message": "OntoForge Ingestion Gate running"}
