"""Transactional Outbox y deduplicación de eventos consumidos (inbox).

- save_to_outbox: guarda el evento en la misma transacción que el cambio
  de negocio. Si la transacción hace rollback, el evento tampoco existe.
- run_outbox_relay: publica los eventos pendientes y los marca como
  publicados recién cuando el broker confirma. Si cae entre medio, el
  evento se publica de nuevo (at-least-once) y lo deduplica el consumidor.
- mark_processed: registra event_id por consumidor; devuelve False si ya
  se había procesado, para no repetir el efecto.
"""

import logging
import threading
import time
from contextlib import closing
from typing import Any

import pika

from app.events import broker
from app.events.envelope import Event

logger = logging.getLogger(__name__)

RELAY_POLL_SECONDS = 0.5
RELAY_BATCH_SIZE = 100
RELAY_ERROR_DELAY_SECONDS = 5

OUTBOX_DDL = """
CREATE TABLE IF NOT EXISTS outbox (
    id BIGSERIAL PRIMARY KEY,
    event_id VARCHAR(64) NOT NULL UNIQUE,
    event_type VARCHAR(64) NOT NULL,
    body TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    published_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS outbox_pending_idx ON outbox (id) WHERE published_at IS NULL;
"""

INBOX_DDL = """
CREATE TABLE IF NOT EXISTS processed_events (
    consumer VARCHAR(64) NOT NULL,
    event_id VARCHAR(64) NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (consumer, event_id)
);
"""


def _run_ddl(database_url: str, ddl: str) -> None:
    import psycopg2

    with closing(psycopg2.connect(database_url)) as connection:
        with connection:
            with connection.cursor() as cursor:
                cursor.execute(ddl)


def ensure_outbox_schema(database_url: str) -> None:
    _run_ddl(database_url, OUTBOX_DDL)


def ensure_inbox_schema(database_url: str) -> None:
    _run_ddl(database_url, INBOX_DDL)


def save_to_outbox(cursor: Any, event: Event) -> None:
    """Debe llamarse con el cursor de la transacción de negocio."""
    event.validate()
    cursor.execute(
        "INSERT INTO outbox (event_id, event_type, body) VALUES (%s, %s, %s)",
        (event.event_id, event.event_type, event.to_json()),
    )


def mark_processed(cursor: Any, consumer: str, event_id: str) -> bool:
    """True si es la primera vez que `consumer` procesa `event_id`.
    Debe llamarse dentro de la misma transacción que el efecto del handler."""
    cursor.execute(
        """
        INSERT INTO processed_events (consumer, event_id) VALUES (%s, %s)
        ON CONFLICT DO NOTHING
        """,
        (consumer, event_id),
    )
    return cursor.rowcount == 1


def relay_pending(db_connection: Any, publish_channel: Any) -> int:
    """Publica un lote de eventos pendientes. Devuelve cuántos publicó.
    SKIP LOCKED permite varias réplicas del relay sin publicar dos veces el mismo lote."""
    with db_connection:
        with db_connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, body FROM outbox
                WHERE published_at IS NULL
                ORDER BY id
                LIMIT %s
                FOR UPDATE SKIP LOCKED
                """,
                (RELAY_BATCH_SIZE,),
            )
            rows = cursor.fetchall()
            for row_id, body in rows:
                broker.publish(publish_channel, Event.from_json(body))
                cursor.execute(
                    "UPDATE outbox SET published_at = CURRENT_TIMESTAMP WHERE id = %s",
                    (row_id,),
                )
    return len(rows)


def run_outbox_relay(database_url: str) -> None:
    import psycopg2

    while True:
        amqp_connection = None
        db_connection = None
        try:
            amqp_connection = broker.connect()
            publish_channel = broker.open_publish_channel(amqp_connection)
            broker.declare_topology(publish_channel)
            db_connection = psycopg2.connect(database_url)
            logger.info("Outbox relay started")
            while True:
                if relay_pending(db_connection, publish_channel) == 0:
                    # Mantiene viva la conexión AMQP mientras espera.
                    amqp_connection.sleep(RELAY_POLL_SECONDS)
        except (pika.exceptions.AMQPError, psycopg2.Error, OSError):
            logger.exception(
                "Outbox relay failed; retrying in %ss", RELAY_ERROR_DELAY_SECONDS
            )
            time.sleep(RELAY_ERROR_DELAY_SECONDS)
        finally:
            if db_connection is not None:
                db_connection.close()
            if amqp_connection is not None and amqp_connection.is_open:
                amqp_connection.close()


def start_outbox_relay(database_url: str) -> threading.Thread:
    thread = threading.Thread(
        target=run_outbox_relay, args=(database_url,), name="outbox-relay", daemon=True
    )
    thread.start()
    return thread
