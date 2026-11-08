"""Conexión con RabbitMQ, declaración de topología y publicación confiable."""

import os
from typing import Any

import pika

from app.events import contract
from app.events.envelope import Event

DELIVERY_EXCHANGE = "investnow.delivery"


def connect(url: str | None = None) -> pika.BlockingConnection:
    url = url or os.getenv("EVENTS_RABBITMQ_URL") or os.getenv(
        "RABBITMQ_URL", "amqp://investnow:investnow@rabbitmq:5672/"
    )
    return pika.BlockingConnection(pika.URLParameters(url))


def declare_topology(channel: Any) -> None:
    """Inicializa la topología con las credenciales de administración del broker."""
    channel.exchange_declare(
        exchange=contract.EXCHANGE, exchange_type="topic", durable=True
    )
    channel.exchange_declare(
        exchange=DELIVERY_EXCHANGE, exchange_type="topic", durable=True
    )
    for queue, binding_keys in contract.QUEUE_BINDINGS.items():
        channel.queue_declare(queue=queue, durable=True)
        for key in binding_keys:
            channel.queue_bind(queue=queue, exchange=contract.EXCHANGE, routing_key=key)

        # Al vencer el TTL, el mensaje vuelve a la cola principal por el
        # exchange por defecto: así se implementa la espera entre reintentos.
        channel.queue_declare(
            queue=contract.retry_queue(queue),
            durable=True,
            arguments={
                "x-message-ttl": contract.RETRY_DELAY_MS,
                "x-dead-letter-exchange": "",
                "x-dead-letter-routing-key": queue,
            },
        )
        dlq = contract.dead_letter_queue(queue)
        channel.queue_declare(queue=dlq, durable=True)
        for destination in (contract.retry_queue(queue), dlq):
            channel.queue_bind(
                queue=destination, exchange=DELIVERY_EXCHANGE, routing_key=destination
            )


def open_publish_channel(connection: pika.BlockingConnection) -> Any:
    """Canal con publisher confirms: basic_publish bloquea hasta que el
    broker confirma y lanza excepción si el mensaje no se pudo rutear."""
    channel = connection.channel()
    channel.confirm_delivery()
    return channel


def publish(channel: Any, event: Event) -> None:
    """Publica un evento en el exchange. `channel` debe venir de
    open_publish_channel (con confirms)."""
    channel.basic_publish(
        exchange=contract.EXCHANGE,
        routing_key=event.routing_key,
        body=event.to_json(),
        properties=pika.BasicProperties(
            content_type="application/json",
            delivery_mode=pika.DeliveryMode.Persistent,
            message_id=event.event_id,
            correlation_id=event.correlation_id,
            type=event.event_type,
        ),
        mandatory=True,
    )


def publish_event(event: Event) -> None:
    """Publicación directa, abriendo y cerrando conexión. Solo para
    servicios sin base de datos (Catalog); el resto usa el outbox."""
    connection = connect()
    try:
        channel = open_publish_channel(connection)
        publish(channel, event)
    finally:
        connection.close()
