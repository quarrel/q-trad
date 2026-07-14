"""Raw-to-canonical ingestion orchestration."""

from datetime import datetime
from uuid import uuid4

from qtrad.application.bars import OneMinuteBarBuilder
from qtrad.application.gaps import GapDetector
from qtrad.domain.events import EventEnvelope
from qtrad.domain.market_data import DataGap, MarketBar
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
    ) -> None:
        self._store = store
        self._producer = producer
        self._producer_version = producer_version
        self._bar_builder = bar_builder or OneMinuteBarBuilder()
        self._gap_detector = gap_detector or GapDetector()

    async def process(self, record: MarketDataRecord) -> AppendResult:
        raw = RawMessage(
            provider=record.provider,
            environment=record.environment,
            subscription=record.subscription,
            deduplication_key=record.deduplication_key,
            received_time=record.received_time,
            payload=record.raw_payload,
            payload_representation=record.payload_representation,
            adapter_version=self._producer_version,
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
        stream_id = f"market-quote:{quote.instrument_id}"
        previous_version = await self._store.latest_stream_version(stream_id)
        event = EventEnvelope.create(
            stream_id=stream_id,
            stream_version=previous_version + 1,
            event_type="MarketQuoteObserved",
            event_time=quote.event_time,
            received_time=quote.received_time,
            producer=self._producer,
            producer_version=self._producer_version,
            payload=quote,
            correlation_id=uuid4(),
        )
        result = await self._store.capture_and_append(
            raw, event, expected_stream_version=previous_version
        )
        if not result.duplicate:
            gap = self._gap_detector.observe(quote, detected_at=record.received_time)
            if gap is not None:
                await self._append_gap(gap)
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
        previous_version = await self._store.latest_stream_version(stream_id)
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
        return await self._store.append(event, expected_stream_version=previous_version)

    async def _append_gap(self, gap: DataGap) -> EventEnvelope:
        stream_id = f"data-gap:{gap.instrument_id}"
        previous_version = await self._store.latest_stream_version(stream_id)
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
        return await self._store.append(event, expected_stream_version=previous_version)
