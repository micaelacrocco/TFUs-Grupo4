import os
from contextlib import closing
from typing import Any

import psycopg2

from app.events import Event, contract, mark_processed, save_to_outbox

ORDER_STATUSES = {"PENDING", "COMPLETED", "FAILED"}


def _connect():
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL must be configured for order storage")
    return psycopg2.connect(database_url)


def initialize_order_schema() -> None:
    with closing(_connect()) as connection:
        with connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
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
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS orders_user_id_order_id_idx
                    ON orders (user_id, order_id)
                    """
                )


def create_order(order: dict[str, Any]) -> int:
    with closing(_connect()) as connection:
        with connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO orders (user_id, asset_id, quantity, total_price, status)
                    VALUES (%s, %s, %s, %s, 'PENDING')
                    RETURNING order_id
                    """,
                    (
                        order["user_id"],
                        order["asset_id"],
                        order["quantity"],
                        order["total_price"],
                    ),
                )
                row = cursor.fetchone()
                if row is None:
                    raise RuntimeError("PostgreSQL did not return the new order ID")
                order_id = int(row[0])
                event = Event.new(
                    contract.ORDER_PLACED,
                    correlation_id=order_id,
                    payload={
                        "order_id": order_id,
                        "user_id": int(order["user_id"]),
                        "asset_id": order["asset_id"],
                        "quantity": order["quantity"],
                        "total_price": order["total_price"],
                    },
                )
                save_to_outbox(cursor, event)
                return order_id


def apply_order_result(
    order_id: int,
    status: str,
    error_message: str | None,
    event_id: str,
) -> bool:
    if status not in ORDER_STATUSES - {"PENDING"}:
        raise ValueError(f"Unsupported result status: {status}")

    with closing(_connect()) as connection:
        with connection:
            with connection.cursor() as cursor:
                if not mark_processed(cursor, "order", event_id):
                    return False

                cursor.execute(
                    """
                    UPDATE orders
                    SET status = %s, error_message = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE order_id = %s AND status = 'PENDING'
                    """,
                    (status, error_message, order_id),
                )
                if cursor.rowcount == 1:
                    return True

                cursor.execute(
                    "SELECT status FROM orders WHERE order_id = %s",
                    (order_id,),
                )
                if cursor.fetchone() is None:
                    raise LookupError(f"Order {order_id} does not exist")
                return False


def get_orders_for_user(user_id: int) -> list[dict[str, Any]]:
    with closing(_connect()) as connection:
        with connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT order_id, user_id, asset_id, quantity, total_price, status
                    FROM orders
                    WHERE user_id = %s
                    ORDER BY order_id
                    """,
                    (user_id,),
                )
                rows = cursor.fetchall()

    orders = []
    for row in rows:
        order = {
            "order_id": int(row[0]),
            "user_id": int(row[1]),
            "asset_id": int(row[2]),
            "quantity": int(row[3]),
            "total_price": float(row[4]),
            "status": row[5],
        }
        orders.append(order)
    return orders
