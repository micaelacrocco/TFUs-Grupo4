import logging

import pika
from fastapi import APIRouter, HTTPException, status

from app.order_messaging import publish_order
from app.order_models import OrderRequest
from app.order_store import (
    create_order as create_order_record,
    get_orders_for_user,
    update_order_status,
)

router = APIRouter(prefix="/api/v1", tags=["Order Service"])
logger = logging.getLogger(__name__)


@router.post("/orders", status_code=status.HTTP_202_ACCEPTED)
def create_order(order: OrderRequest):
    """Valida la orden, la persiste como pendiente y la publica en RabbitMQ."""
    order_id = create_order_record(order.model_dump())
    try:
        publish_order(order_id, order)
    except (pika.exceptions.AMQPError, OSError, RuntimeError) as exc:
        logger.exception("Could not publish order %s to RabbitMQ", order_id)
        update_order_status(order_id, "FAILED", "Could not enqueue order")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Order could not be queued",
        ) from exc

    return {"order_id": order_id, "status": "PENDING"}


@router.get("/orders/{user_id}")
def get_user_orders(user_id: int):
    """Consulta el historial persistido, incluyendo el estado actual."""
    return get_orders_for_user(user_id)
