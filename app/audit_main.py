"""Guarda una copia de los eventos de dominio para su auditoría."""

import logging
import os
from contextlib import closing
from datetime import datetime

import psycopg2
from psycopg2.extras import Json

from app.events import Event, PermanentError, contract
from app.events.consumer import run_consumer


def store_event(database_url: str, event: Event) -> None:
    try:
        occurred_at = datetime.fromisoformat(event.occurred_at)
    except ValueError as exc:
        raise PermanentError("occurred_at inválido") from exc
    if occurred_at.tzinfo is None:
        raise PermanentError("occurred_at requiere zona horaria")

    with closing(psycopg2.connect(database_url)) as connection:
        with connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO audit_events
                        (event_id, event_type, occurred_at, correlation_id, payload)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (event_id) DO NOTHING
                    """,
                    (
                        event.event_id,
                        event.event_type,
                        occurred_at,
                        event.correlation_id,
                        Json(event.payload),
                    ),
                )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    database_url = os.environ["DATABASE_URL"]

    def handle(event: Event) -> None:
        store_event(database_url, event)

    run_consumer(contract.QUEUE_AUDIT, handle)


if __name__ == "__main__":
    main()
