-- Se ejecuta una única vez, la primera vez que se crea el volumen de Postgres
-- (ver docker-entrypoint-initdb.d en la imagen oficial de postgres).

CREATE TABLE IF NOT EXISTS wallets (
    user_id INTEGER PRIMARY KEY,
    balance NUMERIC(14, 2) NOT NULL CHECK (balance >= 0)
);

INSERT INTO wallets (user_id, balance) VALUES
    (1, 10000.00),
    (2, 500.00)
ON CONFLICT (user_id) DO NOTHING;

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

CREATE TABLE IF NOT EXISTS wallet_transactions (
    transaction_id VARCHAR(128) PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES wallets(user_id),
    amount NUMERIC(14, 2) NOT NULL,
    previous_balance NUMERIC(14, 2) NOT NULL,
    new_balance NUMERIC(14, 2) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
