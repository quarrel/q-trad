"""Strict JSON boundary for offline capture-feed page verification."""

from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from qtrad.domain.events import EventEnvelope, JsonValue
from qtrad.ports.capture_feed import CaptureFeedIdentity, CaptureFeedPage

_MAX_FEED_PAGE_BYTES = 16 * 1024 * 1024


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _FeedEventModel(_StrictModel):
    global_position: int = Field(gt=0)
    event_id: UUID
    stream_id: str = Field(min_length=1, max_length=500)
    stream_version: int = Field(gt=0)
    event_type: str = Field(min_length=1, max_length=200)
    schema_version: int = Field(gt=0)
    event_time: datetime
    received_time: datetime
    persisted_time: datetime
    correlation_id: UUID
    causation_id: UUID | None
    producer: str = Field(min_length=1, max_length=200)
    producer_version: str = Field(min_length=1, max_length=100)
    payload: dict[str, JsonValue]


class _FeedPageModel(_StrictModel):
    feed_schema_version: Literal[1]
    source_id: str = Field(min_length=1, max_length=64)
    universe_name: str = Field(min_length=1, max_length=64)
    configuration_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    after_position: int = Field(ge=0)
    high_water_position: int = Field(ge=0)
    next_position: int = Field(ge=0)
    has_more: bool
    events: list[_FeedEventModel] = Field(max_length=1000)


def decode_capture_feed_page(value: str) -> CaptureFeedPage:
    """Decode one exact schema-v1 page and reject unknown or raw-record fields."""

    if len(value.encode("utf-8")) > _MAX_FEED_PAGE_BYTES:
        raise ValueError("capture feed page exceeds the 16 MiB consumer limit")
    model = _FeedPageModel.model_validate_json(value)
    identity = CaptureFeedIdentity(
        feed_schema_version=model.feed_schema_version,
        source_id=model.source_id,
        universe_name=model.universe_name,
        configuration_hash=model.configuration_hash,
    )
    events = tuple(
        EventEnvelope(
            event_id=event.event_id,
            stream_id=event.stream_id,
            stream_version=event.stream_version,
            event_type=event.event_type,
            schema_version=event.schema_version,
            event_time=event.event_time,
            received_time=event.received_time,
            persisted_time=event.persisted_time,
            correlation_id=event.correlation_id,
            causation_id=event.causation_id,
            producer=event.producer,
            producer_version=event.producer_version,
            payload=event.payload,
            global_position=event.global_position,
            raw_record_id=None,
        )
        for event in model.events
    )
    return CaptureFeedPage(
        identity=identity,
        after_position=model.after_position,
        high_water_position=model.high_water_position,
        next_position=model.next_position,
        has_more=model.has_more,
        events=events,
    )


def load_capture_feed_page(path: Path) -> CaptureFeedPage:
    return decode_capture_feed_page(path.read_text(encoding="utf-8"))
