from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

import pytest

from qtrad.application.ingestion import IngestionService
from qtrad.domain.identifiers import InstrumentId, ProviderListingId
from qtrad.domain.market_data import MarketQuote
from qtrad.ports.market_data import MarketDataRecord
from qtrad.ports.storage import AuditStore


class InterruptedStore:
    async def latest_stream_version(self, stream_id: str) -> int:
        raise ConnectionError("database unavailable")


@pytest.mark.asyncio
async def test_database_interruption_propagates_without_acknowledging_record() -> None:
    now = datetime(2026, 7, 3, tzinfo=UTC)
    quote = MarketQuote(
        instrument_id=InstrumentId("fx:aud-usd"),
        listing_id=ProviderListingId("ig", "demo", "CS.D.AUDUSD.CFD.IP"),
        event_time=now,
        received_time=now,
        bid=Decimal("0.65000"),
        ask=Decimal("0.65002"),
    )
    record = MarketDataRecord(
        provider="ig",
        environment="demo",
        subscription="PRICE:CS.D.AUDUSD.CFD.IP",
        deduplication_key="database-interruption",
        received_time=now,
        raw_payload={"BIDPRICE1": "0.65000", "ASKPRICE1": "0.65002"},
        quote=quote,
    )
    service = IngestionService(
        cast(AuditStore, InterruptedStore()),
        producer="test",
        producer_version="1",
    )

    with pytest.raises(ConnectionError, match="database unavailable"):
        await service.process(record)
