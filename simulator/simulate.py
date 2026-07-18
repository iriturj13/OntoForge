import asyncio
import httpx
import subprocess
import time
import sys
import os
import shutil

API_URL = "http://localhost:8000"

async def run_select_agent(client, agent_id):
    print(f"[Agent {agent_id}] Starting read-only traffic...")
    for i in range(5):
        resp = await client.post(f"{API_URL}/queries", json={
            "query_text": "SELECT * FROM orders LIMIT 2;",
            "branch_id": "main"
        })
        if resp.status_code != 200:
            print(f"[Agent {agent_id}] SELECT QUERY ERROR DETAIL: {resp.text}")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        print(f"[Agent {agent_id}] Read query {i+1} completed.")
        await asyncio.sleep(0.2)

async def run_malicious_main_agent(client, agent_id):
    print(f"[Agent {agent_id}] Attempting destructive DDL directly on main...")
    resp = await client.post(f"{API_URL}/queries", json={
        "query_text": "ALTER TABLE orders DROP COLUMN status;",
        "branch_id": "main"
    })
    print(f"[Agent {agent_id}] Main mutation response code: {resp.status_code} (Expected 400)")
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}"
    detail = resp.json()["detail"]
    print(f"[Agent {agent_id}] Rejection details: {detail['reason']}")

async def run_safe_mutation_branch_agent(client, agent_id, branch_id):
    print(f"[Agent {agent_id}] Creating branch '{branch_id}'...")
    resp = await client.post(f"{API_URL}/branches", json={
        "branch_id": branch_id,
        "name": f"Safe Mutation Branch {agent_id}"
    })
    if resp.status_code != 200:
        print(f"[Agent {agent_id}] SAFE BRANCH CREATION ERROR DETAIL: {resp.text}")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    
    print(f"[Agent {agent_id}] Running safe DML UPDATE on branch '{branch_id}'...")
    resp = await client.post(f"{API_URL}/queries", json={
        "query_text": "UPDATE orders SET amount = amount * 1.10 WHERE id = 101;",
        "branch_id": branch_id
    })
    if resp.status_code != 200:
        print(f"[Agent {agent_id}] SAFE UPDATE ERROR DETAIL: {resp.text}")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()
    print(f"[Agent {agent_id}] DML completed. Bytes written: {data['bytes_written_delta']} bytes.")
    
    print(f"[Agent {agent_id}] Requesting merge for branch '{branch_id}'...")
    resp = await client.post(f"{API_URL}/branches/{branch_id}/merge")
    if resp.status_code != 200:
        print(f"[Agent {agent_id}] SAFE MERGE ERROR DETAIL: {resp.text}")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    print(f"[Agent {agent_id}] Branch '{branch_id}' merged successfully.")

async def run_unsafe_mutation_branch_agent(client, agent_id, branch_id):
    print(f"[Agent {agent_id}] Creating branch '{branch_id}'...")
    resp = await client.post(f"{API_URL}/branches", json={
        "branch_id": branch_id,
        "name": f"Unsafe Mutation Branch {agent_id}"
    })
    if resp.status_code != 200:
        print(f"[Agent {agent_id}] UNSAFE BRANCH CREATION ERROR DETAIL: {resp.text}")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    
    print(f"[Agent {agent_id}] Running destructive DDL on branch '{branch_id}'...")
    resp = await client.post(f"{API_URL}/queries", json={
        "query_text": "ALTER TABLE orders DROP COLUMN status;",
        "branch_id": branch_id
    })
    if resp.status_code != 200:
        print(f"[Agent {agent_id}] UNSAFE DDL ERROR DETAIL: {resp.text}")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()
    print(f"[Agent {agent_id}] DDL completed in sandbox. Validated: {data['is_validated']} (Expected False)")
    print(f"[Agent {agent_id}] Validation warning: {data['validation_error']}")
    
    print(f"[Agent {agent_id}] Attempting to merge unsafe branch '{branch_id}'...")
    resp = await client.post(f"{API_URL}/branches/{branch_id}/merge")
    print(f"[Agent {agent_id}] Merge response code: {resp.status_code} (Expected 400)")
    if resp.status_code != 400:
        print(f"[Agent {agent_id}] UNSAFE MERGE REJECTION ERROR DETAIL: {resp.text}")
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}"
    detail = resp.json()["detail"]
    print(f"[Agent {agent_id}] Merge rejected reason: {detail['reason']}")

async def main():
    print("======================================================================")
    print("           Starting OntoForge Simulation & Telemetry Test              ")
    print("======================================================================")
    
    # Check if data directory exists and clean it up to ensure clean metrics
    data_dir = "data"
    if os.path.exists(data_dir):
        # Clean branch databases, primary database, and metadata database to reset state
        branches_dir = os.path.join(data_dir, "branches")
        if os.path.exists(branches_dir):
            shutil.rmtree(branches_dir)
        primary_dir = os.path.join(data_dir, "primary")
        if os.path.exists(primary_dir):
            shutil.rmtree(primary_dir)
        metadata_file = os.path.join(data_dir, "metadata.db")
        if os.path.exists(metadata_file):
            try:
                os.remove(metadata_file)
            except Exception:
                pass
            
    # Ensure data directory exists before opening log files
    os.makedirs(data_dir, exist_ok=True)
    out_log = open(os.path.join(data_dir, "uvicorn_stdout.log"), "w", encoding="utf-8")
    err_log = open(os.path.join(data_dir, "uvicorn_stderr.log"), "w", encoding="utf-8")

    # Start the server as a subprocess, avoiding deadlocks by writing to logs instead of pipes
    server_process = subprocess.Popen(
        [".venv/Scripts/python", "-m", "uvicorn", "src.app:app", "--port", "8000"],
        stdout=out_log,
        stderr=err_log
    )
    
    try:
        print("Waiting for OntoForge server to start...")
        # Poll until server is ready
        server_ready = False
        for _ in range(20):
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(f"{API_URL}/")
                    if resp.status_code == 200:
                        server_ready = True
                        break
            except Exception:
                pass
            await asyncio.sleep(0.5)
            
        if not server_ready:
            print("Error: Could not start the OntoForge server.")
            sys.exit(1)
            
        print("OntoForge server started successfully. Beginning agent traffic...\n")
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Launch agents concurrently
            await asyncio.gather(
                run_select_agent(client, 1),
                run_select_agent(client, 2),
                run_malicious_main_agent(client, 3),
                run_safe_mutation_branch_agent(client, 4, "agent_branch_safe"),
                run_unsafe_mutation_branch_agent(client, 5, "agent_branch_unsafe")
            )
            
            # Give a small sleep to ensure db commits are flushed
            await asyncio.sleep(0.5)
            
            # Retrieve and print final telemetry metrics
            print("\nFetching final telemetry metrics...")
            telemetry_resp = await client.get(f"{API_URL}/telemetry")
            assert telemetry_resp.status_code == 200
            telemetry = telemetry_resp.json()
            
    finally:
        print("Terminating OntoForge server...")
        server_process.terminate()
        try:
            server_process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            server_process.kill()
        
    # Print metrics report
    metrics = telemetry["metrics"]
    print("\n======================================================================")
    print("                       ACADEMIC BENCHMARK REPORT                       ")
    print("======================================================================")
    print(f"Timestamp:                      {telemetry['timestamp']}")
    print(f"Active Sandbox Branches:        {metrics['active_branches']}")
    print(f"Total Branches Merged:          {metrics['total_branches_merged']}")
    print(f"DECS (Convergence Speed):       {metrics['decs_branches_per_hour']:.4f} branches/hour")
    print(f"ZCBE (Branching Efficiency):    {metrics['zcbe_efficiency']:.6f}")
    
    # Assert efficiency targets
    print("----------------------------------------------------------------------")
    if metrics["zcbe_efficiency"] >= 0.98:
        print(f"PASSED: ZCBE of {metrics['zcbe_efficiency']:.6f} satisfies target (>= 0.98)")
    else:
        print(f"FAILED: ZCBE of {metrics['zcbe_efficiency']:.6f} does not satisfy target (>= 0.98)")
    print("======================================================================\n")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
