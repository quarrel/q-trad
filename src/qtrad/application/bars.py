"""Deterministic one-minute quote-derived bar construction."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from qtrad.domain.identifiers import InstrumentId, ProviderListingId
from qtrad.domain.market_data import (
    BarProvenance,
    DataQuality,
    MarketBar,
    MarketQuote,
    PriceBasis,
)
from qtrad.domain.time import require_utc


@dataclass(slots=True)
class _Bucket:
    instrument_id: InstrumentId
    listing_id: ProviderListingId
    interval_start: datetime
    samples: dict[PriceBasis, list[tuple[datetime, Decimal]]] = field(
        default_factory=lambda: {}
    )
    revisions: dict[PriceBasis, int] = field(default_factory=lambda: {})
    closed: bool = False


class OneMinuteBarBuilder:
    """Build revisable UTC one-minute bars from canonical quotes."""

    interval = timedelta(minutes=1)

    def __init__(self, *, lateness: timedelta = timedelta(seconds=5)) -> None:
        if lateness < timedelta(0):
            raise ValueError("lateness must not be negative")
        self._lateness = lateness
        self._buckets: dict[tuple[InstrumentId, datetime], _Bucket] = {}

    def on_quote(self, quote: MarketQuote) -> tuple[MarketBar, ...]:
        interval_start = quote.event_time.replace(second=0, microsecond=0)
        key = (quote.instrument_id, interval_start)
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _Bucket(quote.instrument_id, quote.listing_id, interval_start)
            self._buckets[key] = bucket

        changed: set[PriceBasis] = set()
        if quote.bid is not None:
            self._add_sample(bucket, PriceBasis.BID, quote.effective_bid_time, quote.bid)
            changed.add(PriceBasis.BID)
        if quote.ask is not None:
            self._add_sample(bucket, PriceBasis.ASK, quote.effective_ask_time, quote.ask)
            changed.add(PriceBasis.ASK)
        if (
            quote.bid is not None
            and quote.ask is not None
            and abs(quote.effective_bid_time - quote.effective_ask_time) <= self._lateness
        ):
            midpoint = (quote.bid + quote.ask) / Decimal(2)
            self._add_sample(bucket, PriceBasis.MID, quote.event_time, midpoint)
            changed.add(PriceBasis.MID)

        if not bucket.closed:
            return ()
        return tuple(self._build(bucket, basis) for basis in sorted(changed, key=str))

    def advance(self, watermark: datetime) -> tuple[MarketBar, ...]:
        require_utc(watermark, "watermark")
        bars: list[MarketBar] = []
        for bucket in sorted(
            self._buckets.values(),
            key=lambda item: (str(item.instrument_id), item.interval_start),
        ):
            interval_start = bucket.interval_start
            if bucket.closed or interval_start + self.interval + self._lateness > watermark:
                continue
            bucket.closed = True
            for basis in sorted(bucket.samples, key=str):
                bars.append(self._build(bucket, basis))
        return tuple(bars)

    @staticmethod
    def _add_sample(
        bucket: _Bucket, basis: PriceBasis, sample_time: datetime, price: Decimal
    ) -> None:
        values = bucket.samples.setdefault(basis, [])
        sample = (sample_time, price)
        if sample not in values:
            values.append(sample)
            values.sort(key=lambda item: item[0])

    def _build(self, bucket: _Bucket, basis: PriceBasis) -> MarketBar:
        interval_start = bucket.interval_start
        samples = bucket.samples[basis]
        prices = [price for _, price in samples]
        revision = bucket.revisions.get(basis, 0) + 1
        bucket.revisions[basis] = revision
        return MarketBar(
            instrument_id=bucket.instrument_id,
            basis=basis,
            interval_start=interval_start,
            interval_end=interval_start + self.interval,
            open=prices[0],
            high=max(prices),
            low=min(prices),
            close=prices[-1],
            sample_count=len(prices),
            revision=revision,
            provenance=BarProvenance.QUOTE_DERIVED,
            source_listing_id=bucket.listing_id,
            quality=DataQuality.HEALTHY,
        )
