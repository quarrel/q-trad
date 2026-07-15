"""Strict JSON boundary for offline capture-feed page verification."""

import asyncio
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Literal, Self
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field

from qtrad.domain.events import EventEnvelope, JsonValue
from qtrad.ports.capture_feed import CaptureFeedIdentity, CaptureFeedPage

_MAX_FEED_PAGE_BYTES = 16 * 1024 * 1024
_FEED_PATH = "/api/v1/feed/events"


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


class HttpCaptureFeedClient:
    """Fetch bounded feed pages only through an explicit loopback tunnel endpoint."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("capture feed timeout must be positive")
        endpoint = urlsplit(base_url)
        if endpoint.scheme != "http" or endpoint.hostname not in {"127.0.0.1", "::1"}:
            raise ValueError("capture feed endpoint must be HTTP on a literal loopback address")
        if endpoint.username is not None or endpoint.password is not None:
            raise ValueError("capture feed endpoint must not contain credentials")
        if endpoint.path not in {"", "/"} or endpoint.query or endpoint.fragment:
            raise ValueError("capture feed endpoint must contain only scheme, address and port")
        try:
            port = endpoint.port
        except ValueError as error:
            raise ValueError("capture feed endpoint has an invalid port") from error
        if port is None or port == 0:
            raise ValueError("capture feed endpoint requires an explicit tunnel port")
        self._timeout_seconds = timeout_seconds
        self._client = httpx.AsyncClient(
            base_url=base_url,
            follow_redirects=False,
            headers={"Accept": "application/json"},
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
            trust_env=False,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch_page(self, *, after_position: int, limit: int = 500) -> CaptureFeedPage:
        if after_position < 0:
            raise ValueError("capture feed request cursor cannot be negative")
        if not 1 <= limit <= 1000:
            raise ValueError("capture feed request limit must be between 1 and 1000")

        body = bytearray()
        async with asyncio.timeout(self._timeout_seconds):
            async with self._client.stream(
                "GET",
                _FEED_PATH,
                params={"after_position": after_position, "limit": limit},
            ) as response:
                if response.status_code != 200:
                    raise RuntimeError(
                        f"capture feed returned unexpected HTTP status {response.status_code}"
                    )
                content_type = response.headers.get("content-type")
                if content_type is None or content_type.partition(";")[0].strip().lower() != (
                    "application/json"
                ):
                    raise RuntimeError("capture feed response is not application/json")
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > _MAX_FEED_PAGE_BYTES:
                        raise RuntimeError("capture feed response exceeds the 16 MiB client limit")

        try:
            encoded_page = body.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("capture feed response is not valid UTF-8") from error
        page = decode_capture_feed_page(encoded_page)
        if page.after_position != after_position:
            raise ValueError("capture feed response does not match the requested cursor")
        if len(page.events) > limit:
            raise ValueError("capture feed response exceeds the requested page limit")
        return page
