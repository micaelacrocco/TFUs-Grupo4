"""Conexión con RabbitMQ, declaración de topología y publicación confiable."""

import os
from typing import Any

import pika

from app.events import contract
from app.events.envelope import Event

RABBITMQ_URL = os.getenv(
    "RABBITMQ_URL",
    "amqp://investnow:investnow@rabbitmq:5672/",
)


def connect() -> pika.BlockingConnection:
    return pika.BlockingConnection(pika.URLParameters(RABBITMQ_URL))


def declare_topology(channel: Any) -> None:
    """Declara exchange, colas, colas de retry y DLQ. Es idempotente:
    cada servicio la llama al arrancar, sin importar el orden."""
    channel.exchange_declare(
        exchange=contract.EXCHANGE, exchange_type="topic", durable=True
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
        channel.queue_declare(queue=contract.dead_letter_queue(queue), durable=True)


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
        declare_topology(channel)
        publish(channel, event)
    finally:
        connection.close()
