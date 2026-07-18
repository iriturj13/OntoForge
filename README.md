# OntoForge: Context-Aware Database Branching & Governance Plane

OntoForge is an API gateway and database governance plane designed to validate, isolate, and benchmark database mutations under AI agent workloads. It safeguards production schemas by enforcing semantic compliance and providing lightweight, zero-copy sandboxes for untrusted agent operations.

---

## 🚀 Key Features

*   **Zero-Copy Branching (DuckDB)**: Spin up isolated sandboxes instantly. Development branches use read-only database views attached to the primary database, consuming virtually zero overhead initially.
*   **Copy-on-Write (CoW) Materialization**: When an agent executes writes, updates, or table modifications on a sandbox branch, OntoForge drops the corresponding view and materializes the table locally on the branch with the mutated state.
*   **AST-Based Semantic Validation**: Parses incoming queries using `sqlglot`. If a query attempts to drop or alter database structures currently used as dimensions or measures in the semantic reporting layer (e.g. Cube.js schemas), it is flagged.
*   **Governance Policies**:
    *   **Direct Mutations Blocked**: Direct schema changes on the `main` branch that violate the semantic layer are rejected automatically.
    *   **Merge Rejection**: Branches containing semantic violations are blocked from merging back into `main`.
*   **Academic Benchmarking**: Tracks runtime telemetry to evaluate:
    *   **ZCBE (Zero-Copy Branching Efficiency)**: Measures storage efficiency. Ensures `>98%` efficiency by avoiding data duplication for unchanged records.
    *   **DECS (Database Evolution Convergence Speed)**: Measures the rate of successful branch merges per hour.

---

## 📐 Architecture & Query Flow

```mermaid
graph TD
    User([AI Agent / User]) -->|SQL Query| API[OntoForge API Gateway]
    API -->|Validate Query| Val[AST Semantic Validator]
    Val -->|Checks against| Cube[Cube.js Semantic Layer]
    
    API -->|Execute| BM[OntoForge Branch Manager]
    BM -->|Branch ID: main| PrimDB[(Primary DuckDB)]
    BM -->|Branch ID: dev_branch| BranchDB[(Sandbox DuckDB)]
    
    BranchDB -->|Initially| Views[Read-only Views to Primary]
    BranchDB -->|On Mutation (CoW)| Mat[Materialized Local Table]
    
    API -->|Merge Request| Merge{Any semantic violations?}
    Merge -->|Yes| Reject[Block Merge]
    Merge -->|No| Integrate[Merge sandbox tables to Primary]
```

---

## 📁 Repository Structure

```
OntoForge/
├── src/
│   ├── app.py              # FastAPI Ingress Gate & Telemetry API
│   ├── branch_manager.py   # DuckDB workspace branch attachment & CoW
│   ├── validator.py        # AST parsing & Cube.js semantic validation
│   ├── config.py           # Application configurations & env settings
│   └── metadata.py         # Metadata logging & branch states store
├── simulator/
│   └── simulate.py         # Multi-agent traffic simulation & benchmark reporter
├── cube/
│   └── schema/             # Cube.js semantic schema models (customers, orders)
├── postgres/
│   └── init.sql            # Initial seeding script
├── tests/
│   └── test_ontoforge.py   # Unit testing suite
├── Dockerfile              # Docker container configuration
└── docker-compose.yml      # Service composition definition
```

---

## 🛠️ Installation & Setup

### Prerequisites

*   Python 3.10+
*   Git (MinGit is pre-configured in local PATH)

### 1. Initialize Virtual Environment

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configuration (`.env`)

Create a `.env` file in the root directory (based on `.env.example`):

```env
PORT=8000
CUBEJS_URL=http://localhost:4000
CUBEJS_API_SECRET=your_cubejs_secret
```

---

## 🧪 Simulation & Academic Benchmarking

OntoForge comes with a comprehensive multi-agent simulator that runs concurrent read agents, malicious DDL agents, and branch sandboxing workflows to produce a telemetry benchmark report.

### Running the Simulator

To run the simulator and generate the Academic Benchmark Report:

```bash
python simulator/simulate.py
```

### Sample Benchmark Report Output

```
======================================================================
                       ACADEMIC BENCHMARK REPORT
======================================================================
Timestamp:                      2026-07-18T05:40:00.000000+00:00
Active Sandbox Branches:        1
Total Branches Merged:          1
DECS (Convergence Speed):       527.3556 branches/hour
ZCBE (Branching Efficiency):    0.984139
----------------------------------------------------------------------
PASSED: ZCBE of 0.984139 satisfies target (>= 0.98)
======================================================================
```

---

## 🚦 Core API Endpoints

*   `POST /queries`: Execute a SQL query on a specific branch (sent via request body or `X-Branch-Id` header).
*   `POST /branches`: Create a new zero-copy branch.
*   `POST /branches/{branch_id}/merge`: Merge a branch into `main` (only allowed if the branch has 0 semantic validation failures).
*   `GET /telemetry`: Retrieve current DECS and ZCBE telemetry metrics.
