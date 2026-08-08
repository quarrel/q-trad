"""Central PostgreSQL fixture for native capture lineage and reconciliation."""

import os
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from qtrad.adapters.postgres.queries import OperatorQueries
from qtrad.adapters.postgres.store import PostgresAuditStore
from qtrad.api.app import create_app, engine_from_app
from qtrad.application.ingestion import IngestionService
from qtrad.domain.audit import RawPayloadRepresentation
from qtrad.domain.identifiers import InstrumentId, ProviderListingId
from qtrad.domain.instruments import ProductType, ProviderListing
from qtrad.domain.market_data import MarketDataSourceClass, MarketQuote
from qtrad.domain.modes import BrokerEnvironment, RunKind
from qtrad.ports.capture_feed import CaptureIdentity
from qtrad.ports.market_data import MarketDataRecord
from qtrad.runtime.settings import Settings

DATABASE_URL = os.getenv("QTRAD_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    DATABASE_URL is None,
    reason="QTRAD_TEST_DATABASE_URL is required for PostgreSQL integration",
)

_NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)


def _listing() -> ProviderListing:
    return ProviderListing(
        listing_id=ProviderListingId("ibkr", "IBKR_PAPER", "eur-usd-b2"),
        instrument_id=InstrumentId("fx:eur-usd"),
        display_name="EUR/USD B2",
        product_type=ProductType.SPOT_FX,
        currency="USD",
        minimum_deal_size=Decimal("1"),
        price_increment=Decimal("0.00005"),
        valid_from=_NOW,
        valid_to=None,
        metadata_version="b2-fixture-v1",
    )


def _record(
    listing: ProviderListing,
    *,
    key: str,
    sequence: int,
    quote: MarketQuote | None,
    payload: dict[str, str],
) -> MarketDataRecord:
    return MarketDataRecord(
        provider="ibkr",
        environment="IBKR_PAPER",
        subscription=str(listing.listing_id),
        deduplication_key=key,
        received_time=_NOW,
        raw_payload=payload,
        payload_representation=RawPayloadRepresentation.CHANGED_FIELDS,
        quote=quote,
        error_code=None if quote is not None else "IBKR_UNSUPPORTED_MARKET_DATA_TICK",
        error_detail=None if quote is not None else "tick_size:5",
        connection_generation=1,
        arrival_sequence=sequence,
    )


@pytest.mark.asyncio
async def test_native_lineage_duplicate_identity_and_reconciliation() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    store = PostgresAuditStore(engine)
    listing = _listing()
    identity = CaptureIdentity(
        provider="ibkr",
        environment="IBKR_PAPER",
        source_class=MarketDataSourceClass.IBKR_NATIVE_CAPTURE,
        capture_source_id="ibkr-paper-v1",
        universe_id="capture-ibkr-v1",
        configuration_hash="b" * 64,
    )
    run_id = await store.start_run(
        kind=RunKind.INGESTION,
        environment=BrokerEnvironment.IBKR_PAPER,
        configuration_hash=identity.configuration_hash,
        started_at=_NOW,
    )
    try:
        await store.seed_native_capture_instruments((listing,))
        await store.validate_provider_listing(
            listing,
            universe_hash=identity.configuration_hash,
            observed_at=_NOW,
            producer="ibkr-native-capture",
            producer_version="b2-test",
        )
        service = IngestionService(
            store,
            producer="ibkr-native-capture",
            producer_version="b2-test",
            capture_identity=identity,
            capture_session_id=str(run_id),
        )
        bid = MarketQuote(
            instrument_id=listing.instrument_id,
            listing_id=listing.listing_id,
            event_time=_NOW,
            received_time=_NOW,
            bid=Decimal("1.10000"),
            ask=None,
        )
        ask = MarketQuote(
            instrument_id=listing.instrument_id,
            listing_id=listing.listing_id,
            event_time=_NOW,
            received_time=_NOW,
            bid=None,
            ask=Decimal("1.10020"),
        )
        await service.process(
            _record(listing, key="callback-1", sequence=1, quote=bid, payload={"value": "same"})
        )
        await service.process(
            _record(listing, key="callback-2", sequence=2, quote=ask, payload={"value": "same"})
        )
        await service.process(
            _record(listing, key="callback-3", sequence=3, quote=None, payload={"value": "bad"})
        )
        duplicate = await service.process(
            _record(listing, key="callback-1", sequence=4, quote=bid, payload={"value": "same"})
        )
        assert duplicate.duplicate is True

        raw = await store.query(
            """
            SELECT capture_session_id, source_class, capture_source_id, universe_id,
                   configuration_hash, connection_generation, arrival_sequence
            FROM raw.market_messages
            WHERE capture_session_id = CAST(:session_id AS uuid)
            ORDER BY arrival_sequence
            """,
            {"session_id": str(run_id)},
        )
        assert len(raw) == 3
        assert [row["arrival_sequence"] for row in raw] == [1, 2, 3]
        assert all(row["source_class"] == "IBKR_NATIVE_CAPTURE" for row in raw)
        assert all(row["capture_source_id"] == "ibkr-paper-v1" for row in raw)

        reconciliation = await OperatorQueries(store).capture_reconciliation(
            provider="ibkr",
            environment="IBKR_PAPER",
            capture_session_id=str(run_id),
        )
        assert reconciliation["adapter_accepted"] == 3
        assert reconciliation["raw_persisted"] == 3
        assert reconciliation["canonical_persisted"] == 2
        assert reconciliation["quarantined"] == 1
        assert reconciliation["loss"] == 0
        capture_quotes = await store.query(
            """
            SELECT source_class, provider, environment, bid, ask
            FROM read_model.capture_latest_quotes
            WHERE instrument_id = :instrument_id
            """,
            {"instrument_id": str(listing.instrument_id)},
        )
        assert len(capture_quotes) == 1
        assert capture_quotes[0]["source_class"] == "IBKR_NATIVE_CAPTURE"
        assert capture_quotes[0]["provider"] == "ibkr"
        assert capture_quotes[0]["environment"] == "IBKR_PAPER"
        assert capture_quotes[0]["bid"] is None
        assert capture_quotes[0]["ask"] == Decimal("1.10020")
        assert not await store.query(
            "SELECT 1 FROM read_model.latest_quotes WHERE instrument_id = :instrument_id",
            {"instrument_id": str(listing.instrument_id)},
        )

        app = create_app(
            Settings(
                database_url=DATABASE_URL,
                provider="ibkr",
                ibkr_capture_configuration_hash=identity.configuration_hash,
            )
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/capture/identity")
            assert response.status_code == 200
            assert response.json()["source_class"] == "IBKR_NATIVE_CAPTURE"
            assert response.json()["latest_raw_identity"]["capture_session_id"] == str(run_id)
            instruments_response = await client.get("/api/v1/instruments")
            assert instruments_response.status_code == 200
            assert instruments_response.json()[0]["provider"] == "ibkr"
            assert instruments_response.json()[0]["environment"] == "IBKR_PAPER"
        await engine_from_app(app).dispose()
    finally:
        await store.finish_run(
            run_id,
            status="STOPPED",
            finished_at=_NOW,
            detail={"fixture": "b2"},
        )
        await engine.dispose()
