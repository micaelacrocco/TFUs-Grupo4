import os
from decimal import Decimal, ROUND_HALF_UP

import psycopg2
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/v1", tags=["Wallet Service"])

DATABASE_URL = os.getenv("DATABASE_URL")


def get_connection():
    return psycopg2.connect(DATABASE_URL)


def initialize_wallet_schema() -> None:
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS wallet_transactions (
                        transaction_id VARCHAR(128) PRIMARY KEY,
                        user_id INTEGER NOT NULL REFERENCES wallets(user_id),
                        amount NUMERIC(14, 2) NOT NULL,
                        previous_balance NUMERIC(14, 2) NOT NULL,
                        new_balance NUMERIC(14, 2) NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
    finally:
        conn.close()


class TransactionRequest(BaseModel):
    user_id: int
    amount: float = Field(allow_inf_nan=False)  # Positivo para depósito, negativo para débito/compra
    transaction_id: str | None = Field(default=None, min_length=1, max_length=128)


@router.get("/wallets/{user_id}")
def get_wallet(user_id: int):
    """I-WAL-01: Consulta de saldo del usuario, leído directamente de Postgres."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT balance FROM wallets WHERE user_id = %s", (user_id,))
            row = cur.fetchone()
    finally:
        conn.close()

    if row is None:
        raise HTTPException(status_code=404, detail="Wallet not found")
    return {"user_id": user_id, "balance": float(row[0])}


@router.post("/wallets/transact")
def process_transaction(tx: TransactionRequest):
    """I-WAL-02: Transacción ACID real: lock de fila (SELECT ... FOR UPDATE),
    validación y update dentro de una única transacción de Postgres. Ante un
    error la transacción se revierte por completo (rollback), sin dejar el
    saldo a mitad de camino."""
    conn = get_connection()
    transaction_amount = Decimal(str(tx.amount)).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    try:
        # `with conn:` (no `with conn.cursor():`) es lo que delimita la
        # transacción: psycopg2 abre una transacción implícita en la primera
        # query (autocommit=False por default) y este bloque la cierra con
        # conn.commit() al salir normalmente, o conn.rollback() si se lanza
        # una excepción (ej. HTTPException por fondos insuficientes). El lock
        # de fila que toma el FOR UPDATE de abajo se mantiene durante TODO
        # este bloque, hasta ese commit/rollback: por eso una segunda
        # transacción concurrente sobre el mismo user_id queda bloqueada en
        # vez de leer un balance desactualizado.
        with conn:
            with conn.cursor() as cur:
                if tx.transaction_id is not None:
                    cur.execute(
                        """
                        SELECT user_id, amount, previous_balance, new_balance
                        FROM wallet_transactions
                        WHERE transaction_id = %s
                        """,
                        (tx.transaction_id,),
                    )
                    existing = cur.fetchone()
                    if existing is not None:
                        if (
                            existing[0] != tx.user_id
                            or Decimal(existing[1]) != transaction_amount
                        ):
                            raise HTTPException(
                                status_code=409,
                                detail="Transaction ID was already used with different data",
                            )
                        current_balance = float(existing[2])
                        new_balance = float(existing[3])
                        return {
                            "status": "success",
                            "user_id": tx.user_id,
                            "previous_balance": float(current_balance),
                            "new_balance": float(new_balance),
                        }

                cur.execute(
                    "SELECT balance FROM wallets WHERE user_id = %s FOR UPDATE",
                    (tx.user_id,),
                )
                row = cur.fetchone()
                if row is None:
                    raise HTTPException(status_code=404, detail="Wallet not found")

                if tx.transaction_id is not None:
                    cur.execute(
                        """
                        SELECT user_id, amount, previous_balance, new_balance
                        FROM wallet_transactions
                        WHERE transaction_id = %s
                        """,
                        (tx.transaction_id,),
                    )
                    existing = cur.fetchone()
                    if existing is not None:
                        if (
                            existing[0] != tx.user_id
                            or Decimal(existing[1]) != transaction_amount
                        ):
                            raise HTTPException(
                                status_code=409,
                                detail="Transaction ID was already used with different data",
                            )
                        current_balance = float(existing[2])
                        new_balance = float(existing[3])
                        return {
                            "status": "success",
                            "user_id": tx.user_id,
                            "previous_balance": current_balance,
                            "new_balance": new_balance,
                        }

                current_balance = Decimal(row[0])
                new_balance = current_balance + transaction_amount

                if new_balance < 0:
                    raise HTTPException(
                        status_code=400,
                        detail="Insufficient funds. Transaction aborted (ACID violation prevention)",
                    )

                cur.execute(
                    "UPDATE wallets SET balance = %s WHERE user_id = %s",
                    (new_balance, tx.user_id),
                )
                if tx.transaction_id is not None:
                    cur.execute(
                        """
                        INSERT INTO wallet_transactions (
                            transaction_id, user_id, amount, previous_balance, new_balance
                        )
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (
                            tx.transaction_id,
                            tx.user_id,
                            transaction_amount,
                            current_balance,
                            new_balance,
                        ),
                    )
    finally:
        conn.close()

    return {
        "status": "success",
        "user_id": tx.user_id,
        "previous_balance": current_balance,
        "new_balance": new_balance,
    }
