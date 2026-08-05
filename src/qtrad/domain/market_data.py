"""Canonical market-data values."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from qtrad.domain.identifiers import InstrumentId, ProviderListingId
from qtrad.domain.time import require_utc


class PriceBasis(StrEnum):
    BID = "BID"
    ASK = "ASK"
    MID = "MID"


class DataQuality(StrEnum):
    HEALTHY = "HEALTHY"
    DELAYED = "DELAYED"
    STALE = "STALE"
    GAPPED = "GAPPED"
    PARTIAL = "PARTIAL"
    QUARANTINED = "QUARANTINED"


class BarProvenance(StrEnum):
    QUOTE_DERIVED = "QUOTE_DERIVED"
    IG_HISTORICAL = "IG_HISTORICAL"
    IBKR_HISTORICAL = "IBKR_HISTORICAL"


@dataclass(frozen=True, slots=True)
class MarketQuote:
    instrument_id: InstrumentId
    listing_id: ProviderListingId
    event_time: datetime
    received_time: datetime
    bid: Decimal | None
    ask: Decimal | None
    bid_size: Decimal | None = None
    ask_size: Decimal | None = None
    bid_time: datetime | None = None
    ask_time: datetime | None = None
    quality: DataQuality = DataQuality.HEALTHY
    source_sequence: str | None = None
    global_position: int | None = None

    def __post_init__(self) -> None:
        require_utc(self.event_time, "event_time")
        require_utc(self.received_time, "received_time")
        if self.bid_time is not None:
            require_utc(self.bid_time, "bid_time")
        if self.ask_time is not None:
            require_utc(self.ask_time, "ask_time")
        if self.bid is None and self.ask is None:
            raise ValueError("a quote must contain a bid or ask")
        if self.bid is not None and self.bid <= 0:
            raise ValueError("bid must be positive")
        if self.ask is not None and self.ask <= 0:
            raise ValueError("ask must be positive")
        if self.bid is not None and self.ask is not None and self.bid > self.ask:
            raise ValueError("bid must not exceed ask")
        for name, size in (("bid_size", self.bid_size), ("ask_size", self.ask_size)):
            if size is not None and size < 0:
                raise ValueError(f"{name} must not be negative")
        if self.global_position is not None and self.global_position <= 0:
            raise ValueError("quote global position must be positive")

    @property
    def effective_bid_time(self) -> datetime:
        return self.bid_time or self.event_time

    @property
    def effective_ask_time(self) -> datetime:
        return self.ask_time or self.event_time


@dataclass(frozen=True, slots=True)
class MarketBar:
    instrument_id: InstrumentId
    basis: PriceBasis
    interval_start: datetime
    interval_end: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    sample_count: int
    revision: int
    provenance: BarProvenance
    source_listing_id: ProviderListingId
    quality: DataQuality = DataQuality.HEALTHY

    def __post_init__(self) -> None:
        require_utc(self.interval_start, "interval_start")
        require_utc(self.interval_end, "interval_end")
        if self.interval_end <= self.interval_start:
            raise ValueError("bar interval must be positive")
        if self.sample_count <= 0:
            raise ValueError("bar requires at least one sample")
        if self.revision <= 0:
            raise ValueError("bar revision must be positive")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("OHLC values are inconsistent")
        if self.low > self.high:
            raise ValueError("low must not exceed high")


@dataclass(frozen=True, slots=True)
class DataGap:
    instrument_id: InstrumentId
    interval_start: datetime
    interval_end: datetime
    reason: str
    detected_at: datetime
    repaired_at: datetime | None = None

    def __post_init__(self) -> None:
        require_utc(self.interval_start, "interval_start")
        require_utc(self.interval_end, "interval_end")
        require_utc(self.detected_at, "detected_at")
        if self.repaired_at is not None:
            require_utc(self.repaired_at, "repaired_at")
        if self.interval_end <= self.interval_start:
            raise ValueError("gap interval must be positive")


class MarketDataSourceClass(StrEnum):
    """Provider/data path that produced a research artefact."""

    IG_NATIVE_CAPTURE = "IG_NATIVE_CAPTURE"
    IBKR_HISTORICAL_RESEARCH = "IBKR_HISTORICAL_RESEARCH"
    IBKR_NATIVE_CAPTURE = "IBKR_NATIVE_CAPTURE"
