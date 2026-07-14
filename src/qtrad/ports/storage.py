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
    manifest_sha256: str | None
    created_at: datetime
    schema_version: int
    universe_name: str | None
    row_count: int
    minimum_event_time: datetime | None
    maximum_event_time: datetime | None
    content_sha256: str
    configuration_hash: str
    files: tuple[str, ...]
    file_sha256: Mapping[str, str]
    metadata: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        _require_hex(self.manifest_id, 24, "research manifest ID")
        _require_hex(self.content_sha256, 64, "research content hash")
        _require_hex(self.configuration_hash, 64, "research configuration hash")
        _require_utc(self.created_at, "research manifest creation time")
        if self.schema_version not in {1, 2}:
            raise ValueError("research manifest schema version is unsupported")
        if self.row_count < 0:
            raise ValueError("research manifest row count must not be negative")
        if (self.minimum_event_time is None) != (self.maximum_event_time is None):
            raise ValueError("research manifest time bounds must both be present or absent")
        if self.row_count == 0 and self.minimum_event_time is not None:
            raise ValueError("empty research manifest cannot have time bounds")
        if self.row_count > 0 and self.minimum_event_time is None:
            raise ValueError("non-empty research manifest requires time bounds")
        if self.minimum_event_time is not None and self.maximum_event_time is not None:
            _require_utc(self.minimum_event_time, "research manifest minimum event time")
            _require_utc(self.maximum_event_time, "research manifest maximum event time")
            if self.maximum_event_time <= self.minimum_event_time:
                raise ValueError("research manifest maximum event time must follow its minimum")
        if (self.row_count == 0) != (not self.files):
            raise ValueError("research manifest files must agree with its row count")
        if len(set(self.files)) != len(self.files):
            raise ValueError("research manifest files must be unique")
        if self.schema_version == 1:
            if self.manifest_id != self.content_sha256[:24]:
                raise ValueError("legacy research manifest ID must match its content hash")
            if self.manifest_sha256 is not None or self.universe_name is not None:
                raise ValueError("legacy research manifest has unexpected version-two identity")
            if self.file_sha256:
                raise ValueError("legacy research manifest has unexpected file hashes")
        else:
            if self.manifest_sha256 is None:
                raise ValueError("research manifest hash is required")
            _require_hex(self.manifest_sha256, 64, "research manifest hash")
            if self.manifest_id != self.manifest_sha256[:24]:
                raise ValueError("research manifest ID must match its manifest hash")
            if not self.universe_name or len(self.universe_name) > 64:
                raise ValueError("research manifest universe name is required")
            if set(self.file_sha256) != set(self.files):
                raise ValueError("research manifest requires one hash per file")
            for digest in self.file_sha256.values():
                _require_hex(digest, 64, "research file hash")


class ResearchStore(Protocol):
    async def write_bars(
        self,
        bars: Sequence[MarketBar],
        *,
        universe_name: str,
        configuration_hash: str,
        metadata: Mapping[str, JsonValue],
    ) -> ResearchManifest: ...

    async def read_bars(self, manifest_id: str) -> Sequence[MarketBar]: ...

    async def read_manifest(self, manifest_id: str) -> ResearchManifest: ...


def _require_hex(value: str, length: int, field: str) -> None:
    if len(value) != length or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} must be {length} lower-case hexadecimal characters")


def _require_utc(value: datetime, field: str) -> None:
    offset = value.utcoffset()
    if value.tzinfo is None or offset is None or offset.total_seconds() != 0:
        raise ValueError(f"{field} must use timezone-aware UTC")
