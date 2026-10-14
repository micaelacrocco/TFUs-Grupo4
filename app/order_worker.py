import json
import logging
import os
import time
from typing import Any

import pika
from pydantic import ValidationError

from app.order_messaging import ORDERS_QUEUE, open_orders_channel
from app.order_models import OrderRequest
from app.order_processing import OrderProcessingError, process_order
from app.order_store import initialize_order_schema, update_order_status

CATALOG_URL = os.getenv("CATALOG_SERVICE_URL", "http://catalog_service:8000")
WALLET_URL = os.getenv("WALLET_SERVICE_URL", "http://wallet_service:8000")
logger = logging.getLogger(__name__)


def handle_order(channel: Any, method: Any, _: Any, body: bytes) -> None:
    try:
        message = json.loads(body)
        order_id = int(message["order_id"])
        order = OrderRequest.model_validate(message["order"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, ValidationError):
        logger.exception("Discarding malformed message from %s", ORDERS_QUEUE)
        channel.basic_ack(delivery_tag=method.delivery_tag)
        return

    try:
        process_order(order_id, order, CATALOG_URL, WALLET_URL)
    except OrderProcessingError as exc:
        logger.warning("Order %s failed: %s", order_id, exc)
        try:
            update_order_status(order_id, "FAILED", str(exc))
        except Exception:
            logger.exception("Could not persist failure for order %s", order_id)
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
            return
    except Exception as exc:
        logger.exception("Unexpected processing error for order %s", order_id)
        try:
            update_order_status(order_id, "FAILED", str(exc))
        except Exception:
            logger.exception("Could not persist failure for order %s", order_id)
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
            return
    else:
        try:
            update_order_status(order_id, "COMPLETED")
        except Exception:
            logger.exception("Could not persist completion for order %s", order_id)
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
            return

    channel.basic_ack(delivery_tag=method.delivery_tag)


def run_worker() -> None:
    initialize_order_schema()
    while True:
        connection = None
        try:
            connection, channel = open_orders_channel()
            channel.basic_qos(prefetch_count=1)
            channel.basic_consume(
                queue=ORDERS_QUEUE,
                on_message_callback=handle_order,
                auto_ack=False,
            )
            logger.info("Waiting for orders on %s", ORDERS_QUEUE)
            channel.start_consuming()
        except (pika.exceptions.AMQPError, OSError):
            logger.exception("RabbitMQ connection failed; retrying in 5 seconds")
            time.sleep(5)
        finally:
            if connection is not None and connection.is_open:
                connection.close()


if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    run_worker()
