"""Audit, event and research-store ports."""

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from qtrad.domain.events import EventEnvelope, JsonValue
from qtrad.domain.market_data import MarketBar


@dataclass(frozen=True, slots=True)
class RawMessage:
    provider: str
    environment: str
    subscription: str
    deduplication_key: str
    received_time: datetime
    payload: Mapping[str, JsonValue]
    adapter_version: str


@dataclass(frozen=True, slots=True)
class AppendResult:
    event: EventEnvelope | None
    raw_record_id: int | None
    duplicate: bool


@dataclass(frozen=True, slots=True)
class EventPage:
    """A bounded, ordered view of persisted canonical events."""

    events: tuple[EventEnvelope, ...]
    high_water_position: int

    def __post_init__(self) -> None:
        if self.high_water_position < 0:
            raise ValueError("event high-water position cannot be negative")
        positions: list[int] = []
        for event in self.events:
            if event.global_position is None or event.persisted_time is None:
                raise ValueError("event pages require persisted events")
            if event.global_position <= 0:
                raise ValueError("event page positions must be positive")
            positions.append(event.global_position)
        if positions != sorted(set(positions)):
            raise ValueError("event page positions must be strictly increasing")
        if positions and positions[-1] > self.high_water_position:
            raise ValueError("event page cannot exceed its high-water position")


class RawCapture(Protocol):
    async def capture(self, message: RawMessage) -> int: ...


class EventStore(Protocol):
    async def latest_stream_version(self, stream_id: str) -> int: ...

    async def append(
        self, event: EventEnvelope, *, expected_stream_version: int
    ) -> EventEnvelope: ...

    def read_all(self, *, after_position: int = 0) -> AsyncIterator[EventEnvelope]: ...

    async def read_page(self, *, after_position: int, limit: int) -> EventPage: ...


class AuditStore(RawCapture, EventStore, Protocol):
    async def capture_and_append(
        self,
        message: RawMessage,
        event: EventEnvelope,
        *,
        expected_stream_version: int,
    ) -> AppendResult: ...

    async def quarantine(self, message: RawMessage, *, reason_code: str, detail: str) -> int: ...


@dataclass(frozen=True, slots=True)
class ResearchManifest:
    manifest_id: str
    created_at: datetime
    schema_version: int
    row_count: int
    minimum_event_time: datetime | None
    maximum_event_time: datetime | None
    content_sha256: str
    configuration_hash: str
    files: tuple[str, ...]
    metadata: Mapping[str, JsonValue]


class ResearchStore(Protocol):
    async def write_bars(
        self,
        bars: Sequence[MarketBar],
        *,
        configuration_hash: str,
        metadata: Mapping[str, JsonValue],
    ) -> ResearchManifest: ...

    async def read_bars(self, manifest_id: str) -> Sequence[MarketBar]: ...
