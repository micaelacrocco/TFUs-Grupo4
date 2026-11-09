"""Manejo idempotente de órdenes validadas por Catalog."""

import os
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import psycopg2

from app.events import (
    Event,
    PermanentError,
    contract,
    mark_processed,
    save_to_outbox,
)

DATABASE_URL = os.getenv("DATABASE_URL")
ORDER_AMOUNT = Decimal("0.01")


def get_connection():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL must be configured for Wallet Service")
    return psycopg2.connect(DATABASE_URL)


def _positive_integer(value: object, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise PermanentError(f"{field} debe ser un entero positivo")
    return value


def _order_total(value: object) -> Decimal:
    try:
        total = Decimal(str(value))
        total = total.quantize(ORDER_AMOUNT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise PermanentError("total_price debe ser un monto válido") from None
    if not total.is_finite() or total <= 0:
        raise PermanentError("total_price debe ser un monto positivo y finito")
    if total <= 0:
        raise PermanentError("total_price debe ser al menos 0.01")
    return total


def handle_wallet_event(event: Event) -> None:
    if event.event_type != contract.ASSET_VALIDATED:
        raise PermanentError(f"Wallet no consume {event.event_type}")

    payload = event.payload
    user_id = _positive_integer(payload.get("user_id"), "user_id")
    order_id = _positive_integer(payload.get("order_id"), "order_id")
    total_price = _order_total(payload.get("total_price"))
    transaction_id = f"order:{order_id}"

    connection = get_connection()
    try:
        with connection:
            with connection.cursor() as cursor:
                if not mark_processed(cursor, "wallet", event.event_id):
                    return

                cursor.execute(
                    "SELECT balance FROM wallets WHERE user_id = %s FOR UPDATE",
                    (user_id,),
                )
                wallet = cursor.fetchone()
                if wallet is None:
                    raise PermanentError(f"Wallet not found for user_id {user_id}")

                cursor.execute(
                    """
                    SELECT user_id, amount, previous_balance, new_balance
                    FROM wallet_transactions
                    WHERE transaction_id = %s
                    """,
                    (transaction_id,),
                )
                existing = cursor.fetchone()
                debit = -total_price
                if existing is not None:
                    if (
                        existing[0] != user_id
                        or Decimal(existing[1]) != debit
                    ):
                        raise PermanentError(
                            f"Order {order_id} was already processed with different data"
                        )
                    return

                previous_balance = Decimal(wallet[0])
                if previous_balance < total_price:
                    result = Event.new(
                        contract.FUNDS_REJECTED,
                        correlation_id=order_id,
                        payload={
                            "order_id": order_id,
                            "user_id": user_id,
                            "reason": "Insufficient funds",
                        },
                    )
                else:
                    new_balance = previous_balance - total_price
                    cursor.execute(
                        "UPDATE wallets SET balance = %s WHERE user_id = %s",
                        (new_balance, user_id),
                    )
                    cursor.execute(
                        """
                        INSERT INTO wallet_transactions (
                            transaction_id, user_id, amount, previous_balance, new_balance
                        )
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (
                            transaction_id,
                            user_id,
                            debit,
                            previous_balance,
                            new_balance,
                        ),
                    )
                    result = Event.new(
                        contract.FUNDS_DEBITED,
                        correlation_id=order_id,
                        payload={
                            "order_id": order_id,
                            "user_id": user_id,
                            "amount": float(total_price),
                            "new_balance": float(new_balance),
                        },
                    )

                save_to_outbox(cursor, result)
    finally:
        connection.close()
