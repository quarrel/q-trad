"""Raw-to-canonical ingestion orchestration."""

from datetime import datetime
from uuid import uuid4

from qtrad.application.bars import OneMinuteBarBuilder
from qtrad.application.gaps import GapDetector
from qtrad.domain.events import EventEnvelope, to_json_value
from qtrad.domain.market_data import DataGap, MarketBar
from qtrad.ports.capture_feed import CaptureIdentity
from qtrad.ports.market_data import MarketDataRecord
from qtrad.ports.storage import AppendResult, AuditStore, RawMessage


class IngestionService:
    def __init__(
        self,
        store: AuditStore,
        *,
        producer: str,
        producer_version: str,
        bar_builder: OneMinuteBarBuilder | None = None,
        gap_detector: GapDetector | None = None,
        capture_identity: CaptureIdentity | None = None,
        capture_session_id: str | None = None,
    ) -> None:
        self._store = store
        self._producer = producer
        self._producer_version = producer_version
        self._bar_builder = bar_builder or OneMinuteBarBuilder()
        self._gap_detector = gap_detector or GapDetector()
        self._stream_versions: dict[str, int] = {}
        if capture_identity is not None and capture_session_id is None:
            raise ValueError("capture session identity is required with a capture identity")
        self._capture_identity = capture_identity
        self._capture_session_id = capture_session_id

    async def process(self, record: MarketDataRecord) -> AppendResult:
        raw = RawMessage(
            provider=record.provider,
            environment=record.environment,
            subscription=record.subscription,
            deduplication_key=(
                f"{self._capture_session_id}:{record.deduplication_key}"
                if self._capture_session_id is not None
                else record.deduplication_key
            ),
            received_time=record.received_time,
            payload=record.raw_payload,
            payload_representation=record.payload_representation,
            adapter_version=self._producer_version,
            capture_session_id=self._capture_session_id,
            source_class=(
                self._capture_identity.source_class.value
                if self._capture_identity is not None
                else None
            ),
            capture_source_id=(
                self._capture_identity.capture_source_id
                if self._capture_identity is not None
                else None
            ),
            universe_id=(self._capture_identity.universe_id if self._capture_identity else None),
            configuration_hash=(
                self._capture_identity.configuration_hash
                if self._capture_identity is not None
                else None
            ),
            connection_generation=record.connection_generation,
            arrival_sequence=record.arrival_sequence,
        )
        if record.quote is None:
            return AppendResult(
                event=None,
                raw_record_id=await self._store.quarantine(
                    raw,
                    reason_code=record.error_code or "NORMALISATION_FAILED",
                    detail=record.error_detail or "adapter did not produce a quote",
                ),
                duplicate=False,
            )

        quote = record.quote
        if self._capture_identity is None:
            stream_id = f"market-quote:{quote.instrument_id}"
            payload = to_json_value(quote)
        else:
            stream_id = (
                f"market-quote:{self._capture_identity.source_class.value}:"
                f"{self._capture_identity.provider}:{self._capture_identity.environment}:"
                f"{quote.instrument_id}"
            )
            payload = to_json_value(quote)
            if not isinstance(payload, dict):
                raise TypeError("market quote did not serialise to an object")
            payload = {
                **payload,
                "capture_source_class": self._capture_identity.source_class.value,
                "capture_source_id": self._capture_identity.capture_source_id,
                "capture_universe_id": self._capture_identity.universe_id,
            }
        previous_version = await self._stream_version(stream_id)
        event = EventEnvelope.create(
            stream_id=stream_id,
            stream_version=previous_version + 1,
            event_type="MarketQuoteObserved",
            event_time=quote.event_time,
            received_time=quote.received_time,
            producer=self._producer,
            producer_version=self._producer_version,
            payload=payload,
            correlation_id=uuid4(),
        )
        result = await self._store.capture_and_append(
            raw, event, expected_stream_version=previous_version
        )
        if not result.duplicate:
            self._stream_versions[stream_id] = event.stream_version
            gap = self._gap_detector.observe(quote, detected_at=record.received_time)
            if gap is not None:
                await self._append_gap(gap)
            if self._bar_builder.correction_expired(quote):
                interval_start = quote.event_time.replace(second=0, microsecond=0)
                await self._append_gap(
                    DataGap(
                        instrument_id=quote.instrument_id,
                        interval_start=interval_start,
                        interval_end=interval_start + self._bar_builder.interval,
                        reason="BAR_CORRECTION_WINDOW_EXPIRED",
                        detected_at=record.received_time,
                    )
                )
            else:
                for bar in self._bar_builder.on_quote(quote):
                    await self._append_bar(bar, received_time=record.received_time)
        return result

    async def advance_bars(self, watermark: datetime) -> tuple[EventEnvelope, ...]:
        events: list[EventEnvelope] = []
        for bar in self._bar_builder.advance(watermark):
            events.append(await self._append_bar(bar, received_time=watermark))
        return tuple(events)

    async def _append_bar(self, bar: MarketBar, *, received_time: datetime) -> EventEnvelope:
        stream_id = f"market-bar:{bar.instrument_id}:{bar.basis}"
        previous_version = await self._stream_version(stream_id)
        event_type = "MarketBarClosed" if bar.revision == 1 else "MarketBarCorrected"
        event = EventEnvelope.create(
            stream_id=stream_id,
            stream_version=previous_version + 1,
            event_type=event_type,
            event_time=bar.interval_end,
            received_time=received_time,
            producer=self._producer,
            producer_version=self._producer_version,
            payload=bar,
        )
        persisted = await self._store.append(event, expected_stream_version=previous_version)
        self._stream_versions[stream_id] = event.stream_version
        return persisted

    async def _append_gap(self, gap: DataGap) -> EventEnvelope:
        stream_id = f"data-gap:{gap.instrument_id}"
        previous_version = await self._stream_version(stream_id)
        event = EventEnvelope.create(
            stream_id=stream_id,
            stream_version=previous_version + 1,
            event_type="MarketDataGapDetected",
            event_time=gap.detected_at,
            received_time=gap.detected_at,
            producer=self._producer,
            producer_version=self._producer_version,
            payload=gap,
        )
        persisted = await self._store.append(event, expected_stream_version=previous_version)
        self._stream_versions[stream_id] = event.stream_version
        return persisted

    async def _stream_version(self, stream_id: str) -> int:
        version = self._stream_versions.get(stream_id)
        if version is None:
            version = await self._store.latest_stream_version(stream_id)
            self._stream_versions[stream_id] = version
        return version
