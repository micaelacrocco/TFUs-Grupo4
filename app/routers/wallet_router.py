import os
from decimal import Decimal, ROUND_HALF_UP

from pathlib import Path

import psycopg2
from fastapi import APIRouter, HTTPException, Header, Request, Response
from pydantic import BaseModel, Field

import xml.etree.ElementTree as ET
from app.soap.soap_handlers import parse_get_wallet_request, build_get_wallet_response, build_soap_fault

router = APIRouter(prefix="/api/v1/wallets", tags=["Wallet Service"])

DATABASE_URL = os.getenv("DATABASE_URL")

CURRENT_DIR = Path(__file__).resolve().parent
WSDL_FILE_PATH = CURRENT_DIR.parent / "soap" / "wallet.wsdl"


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

@router.get("/soap/wallet/wsdl")
def get_wallet_wsdl():
    """Expone el contrato WSDL del servicio SOAP de Wallet."""
    if not WSDL_FILE_PATH.exists():
        raise HTTPException(status_code=404, detail=f"WSDL contract not found at {WSDL_FILE_PATH}")
    
    with open(WSDL_FILE_PATH, "r", encoding="utf-8") as f:
        wsdl_content = f.read()
        
    return Response(content=wsdl_content, status_code=200, media_type="application/xml")

class TransactionRequest(BaseModel):
    user_id: int
    amount: float = Field(allow_inf_nan=False)  # Positivo para depósito, negativo para débito/compra
    transaction_id: str | None = Field(default=None, min_length=1, max_length=128)

### Se reemplaza HTTP Rquest por SOAP
""" @router.get("/")
def get_wallet(x_user_id: str = Header(...)):
    I-WAL-01: Consulta de saldo del usuario, leído directamente de Postgres.
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT balance FROM wallets WHERE user_id = %s", (x_user_id,))
            row = cur.fetchone()
    finally:
        conn.close()

    if row is None:
        raise HTTPException(status_code=404, detail="Wallet not found")
    return {"user_id": x_user_id, "balance": float(row[0])} """

@router.post("/soap/wallet")
async def soap_get_wallet(request: Request, x_user_id: str | None = Header(None)):
    body_bytes = await request.body()

    try:
        user_id = parse_get_wallet_request(body_bytes)
    except (ValueError, ET.ParseError):
        fault_xml = build_soap_fault("Invalid XML or missing user_id")
        return Response(content=fault_xml, status_code=400, media_type="application/xml")

    # Vía Gateway, X-User-Id lo fija el Gatekeeper a partir del JWT: un
    # usuario solo puede consultar su propia billetera. Las llamadas internas
    # directas (otro servicio en la red Docker) no traen ese header.
    if x_user_id is not None and x_user_id != str(user_id):
        fault_xml = build_soap_fault("Access denied: cannot read another user's wallet")
        return Response(content=fault_xml, status_code=403, media_type="application/xml")

    # Consulta a BD
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT balance FROM wallets WHERE user_id = %s", (user_id,))
            row = cur.fetchone()
    finally:
        conn.close()

    if row is None:
        fault_xml = build_soap_fault(f"Wallet not found for user_id {user_id}")
        return Response(content=fault_xml, status_code=404, media_type="application/xml")

    response_xml = build_get_wallet_response(user_id, float(row[0]))
    return Response(content=response_xml, status_code=200, media_type="application/xml")

@router.post("/transact")
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
                    (authenticated_user_id,),
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
