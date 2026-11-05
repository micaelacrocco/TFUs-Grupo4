"""Contrato de eventos compartido entre servicios (UT5, Paso 0).

Única fuente de verdad de nombres de eventos, routing keys, colas y campos
de payload. Cualquier cambio acá se acuerda entre todas las líneas de
implementación (ver docs/UT5/PLAN_DE_IMPLEMENTACION.md).
"""

EXCHANGE = "investnow.events"

# --- Tipos de evento ---
ORDER_PLACED = "OrderPlaced"
ASSET_VALIDATED = "AssetValidated"
ORDER_REJECTED = "OrderRejected"
FUNDS_DEBITED = "FundsDebited"
FUNDS_REJECTED = "FundsRejected"

ROUTING_KEYS = {
    ORDER_PLACED: "order.placed",
    ASSET_VALIDATED: "order.asset_validated",
    ORDER_REJECTED: "order.rejected",
    FUNDS_DEBITED: "wallet.funds_debited",
    FUNDS_REJECTED: "wallet.funds_rejected",
}

_ORDER_FIELDS = frozenset({"order_id", "user_id", "asset_id", "quantity", "total_price"})

PAYLOAD_FIELDS = {
    ORDER_PLACED: _ORDER_FIELDS,
    # Repite los datos de la orden para que Wallet no tenga que consultar a Order.
    ASSET_VALIDATED: _ORDER_FIELDS,
    ORDER_REJECTED: frozenset({"order_id", "user_id", "reason"}),
    FUNDS_DEBITED: frozenset({"order_id", "user_id", "amount", "new_balance"}),
    FUNDS_REJECTED: frozenset({"order_id", "user_id", "reason"}),
}

# --- Colas por consumidor ---
QUEUE_ORDER = "order.events"
QUEUE_CATALOG = "catalog.events"
QUEUE_WALLET = "wallet.events"
QUEUE_AUDIT = "audit.events"

QUEUE_BINDINGS = {
    QUEUE_ORDER: ("order.rejected", "wallet.funds_debited", "wallet.funds_rejected"),
    QUEUE_CATALOG: ("order.placed",),
    QUEUE_WALLET: ("order.asset_validated",),
    QUEUE_AUDIT: ("order.*", "wallet.*"),
}

# --- Reintentos (Retry + Dead Letter Queue) ---
MAX_ATTEMPTS = 5
RETRY_DELAY_MS = 5000


def retry_queue(queue: str) -> str:
    return f"{queue}.retry"


def dead_letter_queue(queue: str) -> str:
    return f"{queue}.dlq"
