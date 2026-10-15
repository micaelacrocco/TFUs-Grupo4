import logging
import pika

from fastapi import APIRouter, HTTPException, Header, status

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
def create_order(
    order: OrderRequest,
    x_user_id: str = Header(...)
):
    """Valida la orden, la persiste como pendiente y la publica en RabbitMQ."""
    order_data = order.model_dump()
    order_data["user_id"] = x_user_id

    order_id = create_order_record(order_data)
    try:
        message = {
            "order_id": order_id,
            "user_id": x_user_id,
            "order": order.model_dump(),
        }
        publish_order(message)
    except (pika.exceptions.AMQPError, OSError, RuntimeError) as exc:
        logger.exception("Could not publish order %s to RabbitMQ", order_id)
        update_order_status(order_id, "FAILED", "Could not enqueue order")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Order could not be queued",
        ) from exc

    return {"order_id": order_id, "status": "PENDING"}

@router.get("/orders")
def get_user_orders(
    x_user_id: str = Header(...)
):
    """I-ORD-01: Consulta de historial de órdenes por usuario, persistido, incluyendo el estado actual."""
    current_user_id = int(x_user_id)

    return get_orders_for_user(current_user_id)

@router.get("/orders/admin/{target_user_id}")
def get_any_user_orders(
    target_user_id: int,
    x_user_roles: str = Header("")
):
    """Consulta exclusiva para administradores que requieren ver órdenes ajenas."""
    roles = [r.strip() for r in x_user_roles.split(",")]
    if "admin" not in roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tienes permisos para consultar órdenes de otros usuarios."
        )
    return get_orders_for_user(target_user_id)