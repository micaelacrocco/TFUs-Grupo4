import json
import os
from typing import Any

import pika

ORDERS_QUEUE = "orders_queue"
RABBITMQ_URL = os.getenv(
    "RABBITMQ_URL",
    "amqp://investnow:investnow@rabbitmq:5672/",
)


def open_orders_channel() -> tuple[Any, Any]:
    connection = pika.BlockingConnection(pika.URLParameters(RABBITMQ_URL))
    try:
        channel = connection.channel()
        channel.queue_declare(queue=ORDERS_QUEUE, durable=True)
        return connection, channel
    except Exception:
        connection.close()
        raise

# Se desacopla la firma, si mañana cambia el contrato, no hay que modificar parámetros
def publish_order(message: dict[str, Any]) -> None:
    """Publica el diccionario de la orden directamente en RabbitMQ."""
    connection, channel = open_orders_channel()
    try:
        channel.confirm_delivery()
        channel.basic_publish(
            exchange="",
            routing_key=ORDERS_QUEUE,
            body=json.dumps(message),
            properties=pika.BasicProperties(
                content_type="application/json",
                delivery_mode=pika.DeliveryMode.Persistent,
            ),
            mandatory=True,
        )
    finally:
        connection.close()
