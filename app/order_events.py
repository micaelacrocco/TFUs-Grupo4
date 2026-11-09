"""Manejo de eventos de resultado recibidos por Order Service."""

from typing import Any

from app.events import Event, PermanentError, contract
from app.order_store import apply_order_result


def handle_order_event(event: Event) -> None:
    payload: dict[str, Any] = event.payload
    order_id = payload.get("order_id")
    if type(order_id) is not int or order_id <= 0:
        raise PermanentError("order_id debe ser un entero positivo")

    if event.event_type == contract.FUNDS_DEBITED:
        status, error_message = "COMPLETED", None
    elif event.event_type in (contract.ORDER_REJECTED, contract.FUNDS_REJECTED):
        reason = payload.get("reason")
        if not isinstance(reason, str) or not reason:
            raise PermanentError("reason debe ser un texto no vacío")
        status, error_message = "FAILED", reason
    else:
        raise PermanentError(f"Order no consume {event.event_type}")

    try:
        apply_order_result(order_id, status, error_message, event.event_id)
    except LookupError as exc:
        raise PermanentError(str(exc)) from exc
