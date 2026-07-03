"""Deterministic event ordering and semantic hashing."""

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from qtrad.domain.events import EventEnvelope, JsonValue
from qtrad.domain.market_data import MarketBar
from qtrad.domain.time import require_utc


@dataclass(slots=True)
class ReplayClock:
    _now: datetime

    def __post_init__(self) -> None:
        require_utc(self._now, "_now")

    def now(self) -> datetime:
        return self._now

    def advance_to(self, value: datetime) -> None:
        require_utc(value, "value")
        if value < self._now:
            raise ValueError("replay clock cannot move backwards")
        self._now = value


def ordered_events(events: Iterable[EventEnvelope]) -> tuple[EventEnvelope, ...]:
    return tuple(
        sorted(
            events,
            key=lambda event: (
                event.event_time,
                event.received_time,
                event.global_position or 0,
                event.stream_id,
                event.stream_version,
            ),
        )
    )


def semantic_event_hash(events: Sequence[EventEnvelope]) -> str:
    rows: list[dict[str, JsonValue]] = []
    for event in ordered_events(events):
        rows.append(
            {
                "stream_id": event.stream_id,
                "stream_version": event.stream_version,
                "event_type": event.event_type,
                "schema_version": event.schema_version,
                "event_time": event.event_time.isoformat(),
                "received_time": event.received_time.isoformat(),
                "producer": event.producer,
                "producer_version": event.producer_version,
                "payload": event.payload,
            }
        )
    encoded = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def semantic_bar_hash(bars: Sequence[MarketBar]) -> str:
    rows = [
        {
            "instrument_id": str(bar.instrument_id),
            "basis": bar.basis.value,
            "interval_start": bar.interval_start.isoformat(),
            "interval_end": bar.interval_end.isoformat(),
            "open": str(bar.open),
            "high": str(bar.high),
            "low": str(bar.low),
            "close": str(bar.close),
            "sample_count": bar.sample_count,
            "revision": bar.revision,
            "provenance": bar.provenance.value,
            "quality": bar.quality.value,
            "provider": bar.source_listing_id.provider,
            "environment": bar.source_listing_id.environment,
            "external_id": bar.source_listing_id.external_id,
        }
        for bar in sorted(
            bars,
            key=lambda item: (
                str(item.instrument_id),
                item.interval_start,
                item.basis.value,
                item.revision,
            ),
        )
    ]
    encoded = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
