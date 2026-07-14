from datetime import UTC, datetime
from decimal import Decimal

import pytest

from qtrad.adapters.fixture import FixtureMarketDataAdapter
from qtrad.domain.audit import RawPayloadRepresentation
from qtrad.domain.identifiers import InstrumentId, ProviderListingId
from qtrad.domain.market_data import MarketQuote
from qtrad.ports.market_data import MarketDataRecord


@pytest.mark.asyncio
async def test_fixture_adapter_replays_records_in_order() -> None:
    now = datetime(2026, 7, 2, tzinfo=UTC)
    quote = MarketQuote(
        instrument_id=InstrumentId("fx:gbp-usd"),
        listing_id=ProviderListingId("fixture", "test", "GBPUSD"),
        event_time=now,
        received_time=now,
        bid=Decimal("1.3700"),
        ask=Decimal("1.3702"),
    )
    record = MarketDataRecord(
        provider="fixture",
        environment="test",
        subscription="GBPUSD",
        deduplication_key="1",
        received_time=now,
        raw_payload={"bid": "1.3700", "ask": "1.3702"},
        payload_representation=RawPayloadRepresentation.FIXTURE,
        quote=quote,
    )
    adapter = FixtureMarketDataAdapter((record,))
    await adapter.connect()
    received = [item async for item in adapter.records()]
    assert received == [record]
