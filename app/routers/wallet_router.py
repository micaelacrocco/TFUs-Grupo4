import os

import psycopg2
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1", tags=["Wallet Service"])

DATABASE_URL = os.getenv("DATABASE_URL")


def get_connection():
    return psycopg2.connect(DATABASE_URL)


class TransactionRequest(BaseModel):
    user_id: int
    amount: float  # Positivo para depósito, negativo para débito/compra


@router.get("/wallets")
def get_wallet(x_user_id: str = Header(...)):
    """I-WAL-01: Consulta de saldo del usuario, leído directamente de Postgres."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT balance FROM wallets WHERE user_id = %s", (x_user_id,))
            row = cur.fetchone()
    finally:
        conn.close()

    if row is None:
        raise HTTPException(status_code=404, detail="Wallet not found")
    return {"user_id": x_user_id, "balance": float(row[0])}


@router.post("/wallets/transact")
def process_transaction(tx: TransactionRequest, x_user_id: str = Header(...)):
    """I-WAL-02: Transacción ACID real: lock de fila (SELECT ... FOR UPDATE),
    validación y update dentro de una única transacción de Postgres. Ante un
    error la transacción se revierte por completo (rollback), sin dejar el
    saldo a mitad de camino."""
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Contexto de usuario ausente")

    authenticated_user_id = int(x_user_id)

    # Si el usuario intenta operar sobre otra billetera que no es la suya
    if tx.user_id != authenticated_user_id:
        raise HTTPException(
            status_code=403, 
            detail="Acceso denegado: No puedes realizar transacciones en la billetera de otro usuario"
        )
    
    conn = get_connection()
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
                cur.execute(
                    "SELECT balance FROM wallets WHERE user_id = %s FOR UPDATE",
                    (authenticated_user_id,),
                )
                row = cur.fetchone()
                if row is None:
                    raise HTTPException(status_code=404, detail="Wallet not found")

                current_balance = float(row[0])
                new_balance = current_balance + tx.amount

                if new_balance < 0:
                    raise HTTPException(
                        status_code=400,
                        detail="Insufficient funds. Transaction aborted (ACID violation prevention)",
                    )

                cur.execute(
                    "UPDATE wallets SET balance = %s WHERE user_id = %s",
                    (new_balance, authenticated_user_id),
                )
    finally:
        conn.close()

    return {
        "status": "success",
        "user_id": authenticated_user_id,
        "previous_balance": current_balance,
        "new_balance": new_balance,
    }
