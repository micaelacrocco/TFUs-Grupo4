"""Sobre común de todos los eventos de dominio."""

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.events import contract


class InvalidEventError(ValueError):
    """El mensaje no respeta el contrato (tipo desconocido o payload incorrecto)."""


@dataclass(frozen=True)
class Event:
    event_type: str
    correlation_id: str
    payload: dict[str, Any]
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    occurred_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @classmethod
    def new(cls, event_type: str, correlation_id: Any, payload: dict[str, Any]) -> "Event":
        """Crea un evento validando el payload contra el contrato."""
        event = cls(event_type=event_type, correlation_id=str(correlation_id), payload=payload)
        event.validate()
        return event

    @property
    def routing_key(self) -> str:
        return contract.ROUTING_KEYS[self.event_type]

    def validate(self) -> None:
        expected = contract.PAYLOAD_FIELDS.get(self.event_type)
        if expected is None:
            raise InvalidEventError(f"Unknown event type: {self.event_type!r}")
        actual = set(self.payload)
        if actual != expected:
            raise InvalidEventError(
                f"{self.event_type} payload mismatch: "
                f"missing={sorted(expected - actual)} extra={sorted(actual - expected)}"
            )

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, body: bytes | str) -> "Event":
        try:
            data = json.loads(body)
            event = cls(
                event_type=data["event_type"],
                correlation_id=str(data["correlation_id"]),
                payload=dict(data["payload"]),
                event_id=str(data["event_id"]),
                occurred_at=str(data["occurred_at"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidEventError(f"Malformed event: {exc}") from exc
        event.validate()
        return event
