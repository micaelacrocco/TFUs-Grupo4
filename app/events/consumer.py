"""Consumidor base: ack manual, reintentos con espera y Dead Letter Queue.

Flujo ante cada mensaje:
- handler termina bien                 -> ack.
- handler lanza una excepción          -> se reencola en <cola>.retry con
  x-attempt + 1 (vuelve a la principal tras RETRY_DELAY_MS) -> ack.
- se agotan MAX_ATTEMPTS, PermanentError
  o el mensaje no respeta el contrato  -> <cola>.dlq -> ack.

Primero se publica en retry/DLQ y después se hace ack. Si el proceso cae
entre medio, el mensaje se reentrega y puede quedar duplicado: por eso los
handlers tienen que ser idempotentes (ver outbox.mark_processed).
"""

import logging
import threading
import time
from typing import Any, Callable

import pika

from app.events import broker, contract
from app.events.envelope import Event, InvalidEventError

logger = logging.getLogger(__name__)

ATTEMPT_HEADER = "x-attempt"
ERROR_HEADER = "x-last-error"
RECONNECT_DELAY_SECONDS = 5

Handler = Callable[[Event], None]


class PermanentError(Exception):
    """Falla que no se arregla reintentando: el mensaje va directo a la DLQ."""


def _attempt_of(properties: Any) -> int:
    headers = getattr(properties, "headers", None) or {}
    try:
        return max(1, int(headers.get(ATTEMPT_HEADER, 1)))
    except (TypeError, ValueError):
        return 1


def next_destination(queue: str, attempt: int, permanent: bool) -> tuple[str, int]:
    """Decide a qué cola va un mensaje fallido y con qué número de intento."""
    if permanent or attempt >= contract.MAX_ATTEMPTS:
        return contract.dead_letter_queue(queue), attempt
    return contract.retry_queue(queue), attempt + 1


def _forward(publish_channel: Any, target: str, body: bytes, properties: Any,
             attempt: int, error: str) -> None:
    headers = dict(getattr(properties, "headers", None) or {})
    headers[ATTEMPT_HEADER] = attempt
    headers[ERROR_HEADER] = error[:500]
    publish_channel.basic_publish(
        exchange=broker.DELIVERY_EXCHANGE,
        routing_key=target,
        body=body,
        properties=pika.BasicProperties(
            content_type="application/json",
            delivery_mode=pika.DeliveryMode.Persistent,
            message_id=getattr(properties, "message_id", None),
            correlation_id=getattr(properties, "correlation_id", None),
            type=getattr(properties, "type", None),
            headers=headers,
        ),
        mandatory=True,
    )


def handle_delivery(queue: str, handler: Handler, channel: Any, publish_channel: Any,
                    method: Any, properties: Any, body: bytes) -> None:
    attempt = _attempt_of(properties)
    error: str | None = None
    permanent = False

    try:
        event = Event.from_json(body)
        if method.exchange == contract.EXCHANGE and method.routing_key != event.routing_key:
            raise InvalidEventError("Event type does not match the routing key")
    except InvalidEventError as exc:
        error, permanent = f"invalid event: {exc}", True
    else:
        try:
            handler(event)
        except PermanentError as exc:
            error, permanent = f"permanent: {exc}", True
        except Exception as exc:  # noqa: BLE001 -- cualquier falla técnica se reintenta
            error = f"{type(exc).__name__}: {exc}"

    if error is not None:
        target, next_attempt = next_destination(queue, attempt, permanent)
        log = logger.error if target.endswith(".dlq") else logger.warning
        log("[%s] attempt %d failed (%s) -> %s", queue, attempt, error, target)
        _forward(publish_channel, target, body, properties, next_attempt, error)

    channel.basic_ack(delivery_tag=method.delivery_tag)


def run_consumer(queue: str, handler: Handler) -> None:
    """Consume `queue` indefinidamente, reconectando si se cae el broker."""
    if queue not in contract.QUEUE_BINDINGS:
        raise ValueError(f"Unknown queue: {queue!r}")

    while True:
        connection = None
        try:
            connection = broker.connect()
            channel = connection.channel()
            publish_channel = broker.open_publish_channel(connection)
            channel.basic_qos(prefetch_count=1)
            channel.basic_consume(
                queue=queue,
                on_message_callback=lambda ch, method, props, body: handle_delivery(
                    queue, handler, ch, publish_channel, method, props, body
                ),
                auto_ack=False,
            )
            logger.info("Consuming events from %s", queue)
            channel.start_consuming()
        except (pika.exceptions.AMQPError, OSError):
            logger.exception(
                "RabbitMQ connection lost on %s; retrying in %ss",
                queue, RECONNECT_DELAY_SECONDS,
            )
            time.sleep(RECONNECT_DELAY_SECONDS)
        finally:
            if connection is not None and connection.is_open:
                connection.close()


def start_consumer(queue: str, handler: Handler) -> threading.Thread:
    """Lanza run_consumer en un hilo daemon (para usar desde el lifespan de FastAPI)."""
    thread = threading.Thread(
        target=run_consumer, args=(queue, handler), name=f"consumer:{queue}", daemon=True
    )
    thread.start()
    return thread
