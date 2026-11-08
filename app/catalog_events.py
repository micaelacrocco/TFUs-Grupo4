"""Validación de activos para el flujo de compras por eventos."""

from uuid import NAMESPACE_URL, uuid5

from app.events import Event, PermanentError, contract, publish_event
from app.routers.catalog_router import assets_db


def validate_asset(event: Event) -> None:
    if event.event_type != contract.ORDER_PLACED:
        raise PermanentError(f"Catalog no consume {event.event_type}")

    asset_id = event.payload["asset_id"]
    if type(asset_id) is not int or asset_id <= 0:
        raise PermanentError("asset_id debe ser un entero positivo")

    asset = assets_db.get(asset_id)
    if asset is None:
        reason = "Asset not found in catalog"
    elif asset["status"] != "active":
        reason = "Asset is inactive. Trading blocked."
    else:
        reason = None

    event_type = contract.ASSET_VALIDATED
    payload = event.payload
    if reason is not None:
        event_type = contract.ORDER_REJECTED
        payload = {
            "order_id": event.payload["order_id"],
            "user_id": event.payload["user_id"],
            "reason": reason,
        }

    # Si se reentrega la orden y la decisión es la misma, conserva el ID del resultado.
    result = Event(
        event_type=event_type,
        correlation_id=event.correlation_id,
        payload=payload,
        event_id=str(uuid5(NAMESPACE_URL, f"investnow:catalog:{event.event_id}:{event_type}")),
    )
    publish_event(result)
