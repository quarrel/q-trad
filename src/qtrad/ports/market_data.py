"""Market-data adapter port."""

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from qtrad.domain.events import JsonValue
from qtrad.domain.identifiers import InstrumentId
from qtrad.domain.instruments import ProviderListing
from qtrad.domain.market_data import MarketBar, MarketQuote
from qtrad.domain.operations import AdapterHealth


@dataclass(frozen=True, slots=True)
class MarketDataRecord:
    provider: str
    environment: str
    subscription: str
    deduplication_key: str
    received_time: datetime
    raw_payload: Mapping[str, JsonValue]
    quote: MarketQuote | None
    error_code: str | None = None
    error_detail: str | None = None


@dataclass(frozen=True, slots=True)
class BackfillRequest:
    instrument_id: InstrumentId
    listing: ProviderListing
    start: datetime
    end: datetime
    maximum_points: int


class MarketDataAdapter(Protocol):
    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def discover_listings(
        self, instrument_ids: Sequence[InstrumentId]
    ) -> Sequence[ProviderListing]: ...

    async def subscribe(self, listings: Sequence[ProviderListing]) -> None: ...

    def records(self) -> AsyncIterator[MarketDataRecord]: ...

    def backfill(self, request: BackfillRequest) -> AsyncIterator[MarketBar]: ...

    async def health(self) -> AdapterHealth: ...
