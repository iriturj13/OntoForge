import os
import shutil
import pytest
import duckdb
from src.branch_manager import OntoForgeBranchManager
from src.validator import ASTSemanticValidator
from src.config import settings

@pytest.fixture(scope="module")
def setup_data_dir():
    old_data_dir = settings.DATA_DIR
    temp_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data_test")
    settings.DATA_DIR = temp_dir.replace("\\", "/")
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)
        
    yield temp_dir
    
    if os.path.exists(temp_dir):
        try:
            shutil.rmtree(temp_dir)
        except Exception:
            pass
    settings.DATA_DIR = old_data_dir

def test_ast_validator_intercept_drop(setup_data_dir):
    validator = ASTSemanticValidator()
    query = "ALTER TABLE orders DROP COLUMN status;"
    is_valid, err = validator.validate_query(query)
    assert not is_valid
    assert "Semantic Violation" in err
    assert "status" in err

def test_ast_validator_allow_select(setup_data_dir):
    validator = ASTSemanticValidator()
    query = "SELECT amount FROM orders WHERE id = 101;"
    is_valid, err = validator.validate_query(query)
    assert is_valid
    assert err is None

def test_branch_isolation(setup_data_dir):
    bm = OntoForgeBranchManager()
    
    branch_id = "test_branch_iso"
    bm.create_branch(branch_id, "Test Isolation Branch")
    
    branch_db_path = bm.get_branch_db_path(branch_id)
    assert os.path.exists(branch_db_path)
    
    bm.execute_query(branch_id, "UPDATE orders SET amount = 999.99 WHERE id = 101;")
    
    primary_conn = duckdb.connect(bm.primary_db_path)
    primary_amount = primary_conn.execute("SELECT amount FROM orders WHERE id = 101;").fetchone()[0]
    primary_conn.close()
    
    branch_conn = duckdb.connect(branch_db_path)
    branch_amount = branch_conn.execute("SELECT amount FROM orders WHERE id = 101;").fetchone()[0]
    branch_conn.close()
    
    assert float(primary_amount) == 150.00
    assert float(branch_amount) == 999.99
    
    bm.merge_branch(branch_id)
    
    primary_conn = duckdb.connect(bm.primary_db_path)
    primary_amount_after = primary_conn.execute("SELECT amount FROM orders WHERE id = 101;").fetchone()[0]
    primary_conn.close()
    
    assert float(primary_amount_after) == 999.99
    assert not os.path.exists(branch_db_path)
