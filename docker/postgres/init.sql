-- Se ejecuta al inicializar el volumen de UT5.
\getenv order_password ORDER_DB_PASSWORD
\getenv wallet_password WALLET_DB_PASSWORD
\getenv audit_password AUDIT_DB_PASSWORD

CREATE ROLE order_app LOGIN PASSWORD :'order_password';
CREATE ROLE wallet_app LOGIN PASSWORD :'wallet_password';
CREATE ROLE audit_app LOGIN PASSWORD :'audit_password';

CREATE DATABASE order_db OWNER order_app;
CREATE DATABASE wallet_db OWNER wallet_app;
CREATE DATABASE audit_db OWNER audit_app;

REVOKE CONNECT ON DATABASE order_db, wallet_db, audit_db FROM PUBLIC;

\connect order_db
SET ROLE order_app;

CREATE TABLE IF NOT EXISTS orders (
    order_id BIGSERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    asset_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    total_price NUMERIC(14, 2) NOT NULL CHECK (total_price > 0),
    status VARCHAR(16) NOT NULL
        CHECK (status IN ('PENDING', 'COMPLETED', 'FAILED')),
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS orders_user_id_order_id_idx
    ON orders (user_id, order_id);

\connect wallet_db
SET ROLE wallet_app;

CREATE TABLE IF NOT EXISTS wallets (
    user_id INTEGER PRIMARY KEY,
    balance NUMERIC(14, 2) NOT NULL CHECK (balance >= 0)
);

INSERT INTO wallets (user_id, balance) VALUES
    (1, 10000.00),
    (2, 500.00)
ON CONFLICT (user_id) DO NOTHING;

CREATE TABLE IF NOT EXISTS wallet_transactions (
    transaction_id VARCHAR(128) PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES wallets(user_id),
    amount NUMERIC(14, 2) NOT NULL,
    previous_balance NUMERIC(14, 2) NOT NULL,
    new_balance NUMERIC(14, 2) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

\connect audit_db
SET ROLE audit_app;

CREATE TABLE audit_events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    correlation_id TEXT NOT NULL,
    payload JSONB NOT NULL
);

CREATE INDEX audit_events_correlation_idx ON audit_events (correlation_id, occurred_at);
