-- Create mock tables for Cube.js to bind to
CREATE TABLE IF NOT EXISTS orders (
    id SERIAL PRIMARY KEY,
    customer_id INTEGER,
    order_date TIMESTAMP,
    status VARCHAR(50),
    amount DECIMAL(10, 2)
);

CREATE TABLE IF NOT EXISTS customers (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100),
    email VARCHAR(100),
    country VARCHAR(50)
);

-- Seed mock data so Cube.js is fully functional out of the box
INSERT INTO customers (id, name, email, country) VALUES
(1, 'Alice Smith', 'alice@example.com', 'USA'),
(2, 'Bob Jones', 'bob@example.com', 'Canada')
ON CONFLICT (id) DO NOTHING;

INSERT INTO orders (id, customer_id, order_date, status, amount) VALUES
(101, 1, '2026-07-01 10:00:00', 'completed', 150.00),
(102, 2, '2026-07-02 11:30:00', 'pending', 45.50),
(103, 1, '2026-07-03 14:15:00', 'completed', 99.99)
ON CONFLICT (id) DO NOTHING;

-- Create OntoForge metadata schema
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'branch_status') THEN
        CREATE TYPE branch_status AS ENUM ('active', 'validated', 'merged', 'aborted');
    END IF;
END$$;

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

CREATE TABLE IF NOT EXISTS telemetry_metrics (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    decs DOUBLE PRECISION NOT NULL,
    zcbe DOUBLE PRECISION NOT NULL,
    active_branches INTEGER NOT NULL,
    total_branches_merged INTEGER NOT NULL
);

-- Seed main branch in branches table
INSERT INTO branches (id, name, status, total_dataset_bytes) 
VALUES ('main', 'Main Branch', 'validated', 0)
ON CONFLICT (id) DO NOTHING;
