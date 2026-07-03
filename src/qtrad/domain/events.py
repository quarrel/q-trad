"""Canonical immutable event envelope and serialisation."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import cast
from uuid import UUID, uuid4

from qtrad.domain.identifiers import InstrumentId, RunId
from qtrad.domain.time import require_utc

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]


def to_json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        require_utc(value, "serialised datetime")
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return to_json_value(value.value)
    if isinstance(value, (InstrumentId, RunId)):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return to_json_value(
            {field.name: getattr(value, field.name) for field in fields(value)}
        )
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        return {str(key): to_json_value(item) for key, item in mapping.items()}
    if isinstance(value, (list, tuple)):
        sequence = cast(Sequence[object], value)
        return [to_json_value(item) for item in sequence]
    raise TypeError(f"cannot serialise {type(value).__name__} as a canonical JSON value")


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    event_id: UUID
    stream_id: str
    stream_version: int
    event_type: str
    schema_version: int
    event_time: datetime
    received_time: datetime
    persisted_time: datetime | None
    correlation_id: UUID
    causation_id: UUID | None
    producer: str
    producer_version: str
    payload: dict[str, JsonValue]
    global_position: int | None = None
    raw_record_id: int | None = None

    def __post_init__(self) -> None:
        require_utc(self.event_time, "event_time")
        require_utc(self.received_time, "received_time")
        if self.persisted_time is not None:
            require_utc(self.persisted_time, "persisted_time")
        if not self.stream_id or self.stream_version <= 0:
            raise ValueError("stream identity and positive version are required")
        if not self.event_type or self.schema_version <= 0:
            raise ValueError("event type and positive schema version are required")
        if not self.producer or not self.producer_version:
            raise ValueError("producer identity and version are required")

    @classmethod
    def create(
        cls,
        *,
        stream_id: str,
        stream_version: int,
        event_type: str,
        event_time: datetime,
        received_time: datetime,
        producer: str,
        producer_version: str,
        payload: object,
        correlation_id: UUID | None = None,
        causation_id: UUID | None = None,
        schema_version: int = 1,
    ) -> "EventEnvelope":
        serialised = to_json_value(payload)
        if not isinstance(serialised, dict):
            raise TypeError("event payload must serialise to an object")
        return cls(
            event_id=uuid4(),
            stream_id=stream_id,
            stream_version=stream_version,
            event_type=event_type,
            schema_version=schema_version,
            event_time=event_time,
            received_time=received_time,
            persisted_time=None,
            correlation_id=correlation_id or uuid4(),
            causation_id=causation_id,
            producer=producer,
            producer_version=producer_version,
            payload=cast(dict[str, JsonValue], serialised),
        )
