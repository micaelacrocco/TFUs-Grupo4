import json
import os
from typing import Any

import pika

from app.order_models import OrderRequest

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


def publish_order(order_id: int, order: OrderRequest) -> None:
    connection, channel = open_orders_channel()
    try:
        channel.confirm_delivery()
        channel.basic_publish(
            exchange="",
            routing_key=ORDERS_QUEUE,
            body=json.dumps(
                {"order_id": order_id, "order": order.model_dump()}
            ),
            properties=pika.BasicProperties(
                content_type="application/json",
                delivery_mode=pika.DeliveryMode.Persistent,
            ),
            mandatory=True,
        )
    finally:
        connection.close()
