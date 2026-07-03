import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from qtrad.__main__ import _append_bar
from qtrad.adapters.postgres.store import PostgresAuditStore, StreamVersionConflict
from qtrad.api.app import create_app, engine_from_app
from qtrad.application.ingestion import IngestionService
from qtrad.domain.events import EventEnvelope
from qtrad.domain.identifiers import InstrumentId, ProviderListingId
from qtrad.domain.market_data import (
    BarProvenance,
    DataQuality,
    MarketBar,
    MarketQuote,
    PriceBasis,
)
from qtrad.ports.market_data import MarketDataRecord
from qtrad.runtime.settings import Settings

DATABASE_URL = os.getenv("QTRAD_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="QTRAD_DATABASE_URL is required for PostgreSQL integration"
)


@pytest.mark.asyncio
async def test_atomic_ingestion_idempotency_projection_and_rebuild() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    store = PostgresAuditStore(engine)
    await store.seed_instruments()
    unique = uuid4().hex
    now = datetime(2026, 7, 2, 10, 0, 1, tzinfo=UTC)
    quote = MarketQuote(
        instrument_id=InstrumentId("fx:aud-usd"),
        listing_id=ProviderListingId("fixture", "integration", f"AUDUSD-{unique}"),
        event_time=now,
        received_time=now,
        bid=Decimal("0.65001"),
        ask=Decimal("0.65003"),
    )
    record = MarketDataRecord(
        provider="fixture",
        environment="integration",
        subscription=f"AUDUSD-{unique}",
        deduplication_key=unique,
        received_time=now,
        raw_payload={
            "bid": "0.65001",
            "ask": "0.65003",
            "api_key": "must-not-persist",
        },
        quote=quote,
    )
    service = IngestionService(store, producer="integration-test", producer_version="1")
    first = await service.process(record)
    duplicate = await service.process(record)
    bar_events = await service.advance_bars(now + timedelta(minutes=1, seconds=5))

    assert first.event is not None
    assert not first.duplicate
    assert duplicate.duplicate
    assert len(bar_events) == 3
    raw = await store.query(
        """
        SELECT payload FROM raw.market_messages
        WHERE provider = 'fixture' AND environment = 'integration'
          AND deduplication_key = :deduplication_key
        """,
        {"deduplication_key": unique},
    )
    assert raw[0]["payload"]["api_key"] == "[REDACTED]"

    rows = await store.query(
        """
        SELECT * FROM read_model.latest_quotes
        WHERE instrument_id = 'fx:aud-usd'
        """
    )
    assert rows[0]["bid"] == Decimal("0.65001")
    bars = await store.query(
        """
        SELECT * FROM read_model.market_bars
        WHERE instrument_id = 'fx:aud-usd'
          AND source_external_id = :external_id
        """,
        {"external_id": f"AUDUSD-{unique}"},
    )
    assert {row["basis"] for row in bars} == {"BID", "ASK", "MID"}

    projected = await store.rebuild_projections()
    assert projected >= 4
    rebuilt = await store.query(
        """
        SELECT * FROM read_model.market_bars
        WHERE instrument_id = 'fx:aud-usd'
          AND source_external_id = :external_id
        """,
        {"external_id": f"AUDUSD-{unique}"},
    )
    assert len(rebuilt) == 3
    await engine.dispose()


@pytest.mark.asyncio
async def test_stream_version_conflict_fails_closed() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    store = PostgresAuditStore(engine)
    now = datetime.now(UTC)
    stream = f"conflict:{uuid4().hex}"
    event = EventEnvelope.create(
        stream_id=stream,
        stream_version=1,
        event_type="TestObserved",
        event_time=now,
        received_time=now,
        producer="integration-test",
        producer_version="1",
        payload={"value": 1},
    )
    await store.append(event, expected_stream_version=0)
    with pytest.raises(StreamVersionConflict):
        await store.append(
            EventEnvelope.create(
                stream_id=stream,
                stream_version=1,
                event_type="TestObserved",
                event_time=now,
                received_time=now,
                producer="integration-test",
                producer_version="1",
                payload={"value": 2},
            ),
            expected_stream_version=0,
        )
    await engine.dispose()


@pytest.mark.asyncio
async def test_historical_bar_append_is_idempotent() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    store = PostgresAuditStore(engine)
    await store.seed_instruments()
    external_id = f"AUDUSD-HIST-{uuid4().hex}"
    interval_start = datetime(2026, 7, 2, 9, 0, tzinfo=UTC)
    bar = MarketBar(
        instrument_id=InstrumentId("fx:aud-usd"),
        basis=PriceBasis.BID,
        interval_start=interval_start,
        interval_end=interval_start + timedelta(minutes=1),
        open=Decimal("0.65000"),
        high=Decimal("0.65010"),
        low=Decimal("0.64990"),
        close=Decimal("0.65005"),
        sample_count=1,
        revision=1,
        provenance=BarProvenance.IG_HISTORICAL,
        source_listing_id=ProviderListingId("ig", "demo", external_id),
        quality=DataQuality.HEALTHY,
    )

    first = await _append_bar(store, bar, received_time=interval_start)
    duplicate = await _append_bar(store, bar, received_time=interval_start)

    assert first is not None
    assert duplicate is None
    rows = await store.query(
        """
        SELECT count(*) AS event_count
        FROM canonical.events
        WHERE stream_id LIKE :stream_prefix
        """,
        {"stream_prefix": f"historical-bar:fx:aud-usd:BID:ig:demo:{external_id}:%"},
    )
    assert rows[0]["event_count"] == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_read_only_api_reports_seeded_instruments() -> None:
    assert DATABASE_URL is not None
    settings = Settings(database_url=DATABASE_URL)
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/health")
        instruments = await client.get("/api/v1/instruments")
        production_probe = await client.post("/api/v1/orders")

    assert health.status_code == 200
    assert health.json()["mode"] == "data-only"
    assert instruments.status_code == 200
    assert len(instruments.json()) == 7
    assert production_probe.status_code == 404
    await engine_from_app(app).dispose()
