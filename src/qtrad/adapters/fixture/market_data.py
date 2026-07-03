"""In-memory adapter for contract and replay tests."""

from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime

from qtrad.domain.identifiers import InstrumentId
from qtrad.domain.instruments import ProviderListing
from qtrad.domain.market_data import MarketBar
from qtrad.domain.modes import BrokerEnvironment
from qtrad.domain.operations import AdapterHealth, HealthStatus
from qtrad.ports.market_data import BackfillRequest, MarketDataRecord


class FixtureMarketDataAdapter:
    def __init__(
        self,
        records: Sequence[MarketDataRecord],
        listings: Sequence[ProviderListing] = (),
        historical_bars: Sequence[MarketBar] = (),
    ) -> None:
        self._records = tuple(records)
        self._listings = tuple(listings)
        self._historical_bars = tuple(historical_bars)
        self._connected = False
        self._subscribed: set[str] = set()

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def discover_listings(
        self, instrument_ids: Sequence[InstrumentId]
    ) -> Sequence[ProviderListing]:
        wanted = set(instrument_ids)
        return tuple(item for item in self._listings if item.instrument_id in wanted)

    async def subscribe(self, listings: Sequence[ProviderListing]) -> None:
        self._subscribed.update(str(item.listing_id) for item in listings)

    async def records(self) -> AsyncIterator[MarketDataRecord]:
        if not self._connected:
            raise RuntimeError("fixture adapter is not connected")
        for record in self._records:
            yield record

    async def backfill(self, request: BackfillRequest) -> AsyncIterator[MarketBar]:
        count = 0
        for bar in self._historical_bars:
            if (
                bar.instrument_id == request.instrument_id
                and request.start <= bar.interval_start < request.end
                and count < request.maximum_points
            ):
                count += 1
                yield bar

    async def health(self) -> AdapterHealth:
        now = datetime.now(UTC)
        return AdapterHealth(
            adapter_name="fixture",
            environment=BrokerEnvironment.NONE,
            status=HealthStatus.HEALTHY if self._connected else HealthStatus.STOPPED,
            observed_at=now,
            last_message_at=max(
                (record.received_time for record in self._records), default=None
            ),
        )
