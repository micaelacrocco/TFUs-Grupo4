# app/wallet_events.py

import os
import psycopg2
from app.events import Event, contract, mark_processed, save_to_outbox, PermanentError

DATABASE_URL = os.getenv("DATABASE_URL")

def get_connection():
    return psycopg2.connect(DATABASE_URL)

def handle_wallet_event(event: Event) -> None:
    """Manejador del consumidor para la cola wallet.events.
    Procesa AssetValidated de forma idempotente y transaccional.
    """
    if event.event_type != contract.ASSET_VALIDATED:
        return

    payload = event.payload
    user_id = payload["user_id"]
    order_id = payload["order_id"]
    total_price = float(payload["total_price"])

    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                # 1. Idempotencia: si este event_id ya se procesó, salimos
                if not mark_processed(cur, "wallet", event.event_id):
                    return

                # 2. Lock de fila para asegurar consistencia ACID (igual que en tus transacciones REST)
                cur.execute("SELECT balance FROM wallets WHERE user_id = %s FOR UPDATE", (user_id,))
                row = cur.fetchone()

                if row is None:
                    # Error permanente: la wallet no existe -> va directo a DLQ sin reintentos
                    raise PermanentError(f"Wallet not found for user_id {user_id}")

                current_balance = float(row[0])

                # 3. Regla de negocio: ¿alcanzan los fondos?
                if current_balance >= total_price:
                    new_balance = current_balance - total_price

                    # Actualizar saldo en la BD
                    cur.execute(
                        "UPDATE wallets SET balance = %s WHERE user_id = %s",
                        (new_balance, user_id)
                    )

                    # Publicar éxito al outbox (misma transacción)
                    outbox_event = Event.new(
                        contract.FUNDS_DEBITED,
                        correlation_id=str(order_id),
                        payload={
                            "order_id": order_id,
                            "user_id": user_id,
                            "amount": total_price,
                            "new_balance": new_balance,
                        }
                    )
                    save_to_outbox(cur, outbox_event)
                else:
                    # Rechazo de negocio (fondos insuficientes) -> no es error técnico
                    outbox_event = Event.new(
                        contract.FUNDS_REJECTED,
                        correlation_id=str(order_id),
                        payload={
                            "order_id": order_id,
                            "user_id": user_id,
                            "reason": "Insufficient funds",
                        }
                    )
                    save_to_outbox(cur, outbox_event)
    finally:
        conn.close()