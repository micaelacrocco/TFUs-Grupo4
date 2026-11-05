"""Librería de eventos compartida (UT5). Contrato y reglas de uso en
docs/UT5/PLAN_DE_IMPLEMENTACION.md, sección "Paso 0"."""

from app.events import contract
from app.events.broker import declare_topology, publish_event
from app.events.consumer import PermanentError, start_consumer
from app.events.envelope import Event, InvalidEventError
from app.events.outbox import (
    ensure_inbox_schema,
    ensure_outbox_schema,
    mark_processed,
    save_to_outbox,
    start_outbox_relay,
)

__all__ = [
    "contract",
    "Event",
    "InvalidEventError",
    "PermanentError",
    "declare_topology",
    "publish_event",
    "start_consumer",
    "save_to_outbox",
    "mark_processed",
    "ensure_outbox_schema",
    "ensure_inbox_schema",
    "start_outbox_relay",
]
