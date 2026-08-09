import dataclasses
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from qtrad.__main__ import _append_bar
from qtrad.adapters.postgres.storage_measurement import PostgresStorageInspector
from qtrad.adapters.postgres.store import PostgresAuditStore, StreamVersionConflict
from qtrad.api.app import create_app, engine_from_app
from qtrad.application.backfill_planning import backfill_plan_payload, build_backfill_plan
from qtrad.application.ingestion import IngestionService
from qtrad.application.research_observations import build_observation_dataset
from qtrad.application.run_reconciliation import build_run_reconciliation_plan
from qtrad.domain.audit import RawPayloadRepresentation
from qtrad.domain.events import EventEnvelope
from qtrad.domain.identifiers import InstrumentId, ProviderListingId
from qtrad.domain.instruments import (
    INITIAL_INSTRUMENTS,
    ProductType,
    ProviderListing,
)
from qtrad.domain.market_data import (
    BarProvenance,
    DataQuality,
    MarketBar,
    MarketQuote,
    PriceBasis,
)
from qtrad.domain.modes import BrokerEnvironment, RunKind
from qtrad.ports.market_data import MarketDataRecord
from qtrad.ports.storage import ResearchManifest
from qtrad.runtime.capture_feed import HttpCaptureFeedClient, decode_capture_feed_page
from qtrad.runtime.settings import Settings

DATABASE_URL = os.getenv("QTRAD_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not DATABASE_URL,
        reason=(
            "QTRAD_TEST_DATABASE_URL is required for PostgreSQL integration; "
            "run ops/dev/verify.sh for the complete local gate"
        ),
    ),
]


@pytest.mark.asyncio
async def test_quote_derived_observation_query_joins_every_revision_to_canonical_lineage() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    store = PostgresAuditStore(engine)
    await store.seed_instruments()
    offset = int(uuid4().hex[:8], 16) % 1_000_000
    interval_start = datetime(2035, 1, 1, tzinfo=UTC) + timedelta(minutes=offset)
    instrument_id = InstrumentId("fx:aud-usd")
    listing_id = ProviderListingId("fixture", "integration", f"AUDUSD-{uuid4().hex}")
    stream_id = f"market-bar:{instrument_id}:{PriceBasis.MID}"
    previous = await store.latest_stream_version(stream_id)
    bars = (
        MarketBar(
            instrument_id=instrument_id,
            basis=PriceBasis.MID,
            interval_start=interval_start,
            interval_end=interval_start + timedelta(minutes=1),
            open=Decimal("1.00"),
            high=Decimal("1.01"),
            low=Decimal("0.99"),
            close=Decimal("1.00"),
            sample_count=2,
            revision=1,
            provenance=BarProvenance.QUOTE_DERIVED,
            quality=DataQuality.HEALTHY,
            source_listing_id=listing_id,
        ),
        MarketBar(
            instrument_id=instrument_id,
            basis=PriceBasis.MID,
            interval_start=interval_start,
            interval_end=interval_start + timedelta(minutes=1),
            open=Decimal("1.00"),
            high=Decimal("1.02"),
            low=Decimal("0.99"),
            close=Decimal("1.01"),
            sample_count=3,
            revision=2,
            provenance=BarProvenance.QUOTE_DERIVED,
            quality=DataQuality.HEALTHY,
            source_listing_id=listing_id,
        ),
    )
    for index, bar in enumerate(bars, start=1):
        event = EventEnvelope.create(
            stream_id=stream_id,
            stream_version=previous + index,
            event_type="MarketBarClosed" if bar.revision == 1 else "MarketBarCorrected",
            event_time=bar.interval_end,
            received_time=bar.interval_end + timedelta(seconds=index),
            producer="integration-test",
            producer_version="1",
            payload=bar,
        )
        await store.append(event, expected_stream_version=previous + index - 1)

    candidates = await store.read_quote_derived_bar_candidates(
        instrument_ids=(instrument_id,),
        interval_start=interval_start,
        interval_end=interval_start + timedelta(minutes=1),
    )
    dataset = build_observation_dataset(
        candidates,
        configuration={
            "interval_start": interval_start.isoformat(),
            "interval_end": (interval_start + timedelta(minutes=1)).isoformat(),
        },
    )

    assert tuple(row.revision for row in dataset.rows) == (1, 2)
    assert tuple(row.stream_version for row in dataset.rows) == (previous + 1, previous + 2)
    assert tuple(row.event_type for row in dataset.rows) == (
        "MarketBarClosed",
        "MarketBarCorrected",
    )
    await engine.dispose()


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
        payload_representation=RawPayloadRepresentation.FIXTURE,
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
        SELECT payload, payload_representation FROM raw.market_messages
        WHERE provider = 'fixture' AND environment = 'integration'
          AND deduplication_key = :deduplication_key
        """,
        {"deduplication_key": unique},
    )
    assert raw[0]["payload"]["api_key"] == "[REDACTED]"
    assert raw[0]["payload_representation"] == RawPayloadRepresentation.FIXTURE

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
async def test_raw_payload_representation_is_backward_compatible_and_bounded() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    unique = uuid4().hex
    async with engine.begin() as connection:
        legacy = await connection.execute(
            text(
                """
                INSERT INTO raw.market_messages (
                    provider, environment, subscription, deduplication_key,
                    received_time, payload, payload_sha256, adapter_version
                ) VALUES (
                    'legacy-writer', 'test', 'legacy', :deduplication_key,
                    :received_time, '{}'::jsonb, :payload_sha256, 'legacy'
                )
                RETURNING payload_representation
                """
            ),
            {
                "deduplication_key": f"legacy-{unique}",
                "received_time": datetime.now(UTC),
                "payload_sha256": "0" * 64,
            },
        )
        assert legacy.scalar_one() == RawPayloadRepresentation.LEGACY_UNCLASSIFIED

    with pytest.raises(DBAPIError):
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO raw.market_messages (
                        provider, environment, subscription, deduplication_key,
                        received_time, payload, payload_sha256,
                        payload_representation, adapter_version
                    ) VALUES (
                        'invalid-writer', 'test', 'invalid', :deduplication_key,
                        :received_time, '{}'::jsonb, :payload_sha256, 9, 'invalid'
                    )
                    """
                ),
                {
                    "deduplication_key": f"invalid-{unique}",
                    "received_time": datetime.now(UTC),
                    "payload_sha256": "0" * 64,
                },
            )
    await engine.dispose()


@pytest.mark.asyncio
async def test_listing_validation_atomically_supersedes_epics_and_rebuilds() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    store = PostgresAuditStore(engine)
    suffix = uuid4().hex
    instrument = INITIAL_INSTRUMENTS[0]
    second_observed_at = datetime.now(UTC) - timedelta(minutes=1)
    first_observed_at = second_observed_at - timedelta(minutes=1)
    first = ProviderListing(
        listing_id=ProviderListingId("ig", "demo", f"FIRST.{suffix}"),
        instrument_id=instrument.instrument_id,
        display_name="First listing",
        product_type=ProductType.SPOT_FX,
        currency="USD",
        minimum_deal_size=Decimal("1"),
        price_increment=Decimal("0.0001"),
        valid_from=first_observed_at - timedelta(days=1),
        valid_to=None,
        metadata_version="first-version",
        economics={"quantity_unit": "contracts"},
    )
    second = dataclasses.replace(
        first,
        listing_id=ProviderListingId("ig", "demo", f"SECOND.{suffix}"),
        display_name="Second listing",
        valid_from=second_observed_at - timedelta(days=1),
        metadata_version="second-version",
    )

    first_event = await store.validate_provider_listing(
        first, universe_hash="a" * 64, observed_at=first_observed_at
    )
    second_event = await store.validate_provider_listing(
        second, universe_hash="b" * 64, observed_at=second_observed_at
    )
    repeated = await store.validate_provider_listing(
        second,
        universe_hash="b" * 64,
        observed_at=second_observed_at + timedelta(seconds=1),
    )

    assert first_event is not None
    assert second_event is not None
    assert repeated is None
    expected_rows = [
        {
            "external_id": first.listing_id.external_id,
            "valid_from": first_observed_at,
            "valid_to": second_observed_at,
            "metadata_version": first.metadata_version,
            "universe_hash": "a" * 64,
        },
        {
            "external_id": second.listing_id.external_id,
            "valid_from": second_observed_at,
            "valid_to": None,
            "metadata_version": second.metadata_version,
            "universe_hash": "b" * 64,
        },
    ]
    rows = await store.query(
        """
        SELECT external_id, valid_from, valid_to, metadata_version, universe_hash
        FROM reference.provider_listings
        WHERE instrument_id = :instrument_id
        ORDER BY valid_from
        """,
        {"instrument_id": str(instrument.instrument_id)},
    )
    assert rows == expected_rows
    active = await store.active_provider_listings((instrument.instrument_id,))
    assert [item.listing_id for item in active] == [second.listing_id]
    event_rows = await store.query(
        """
        SELECT payload ->> 'universe_hash' AS universe_hash
        FROM canonical.events
        WHERE event_type = 'ProviderListingValidated'
          AND payload #>> '{listing,instrument_id}' = :instrument_id
        ORDER BY global_position
        """,
        {"instrument_id": str(instrument.instrument_id)},
    )
    assert event_rows == [{"universe_hash": "a" * 64}, {"universe_hash": "b" * 64}]
    invalid_event = EventEnvelope.create(
        stream_id=f"provider-listing:ig:demo:INVALID.{suffix}",
        stream_version=1,
        event_type="ProviderListingValidated",
        event_time=second_observed_at + timedelta(seconds=1),
        received_time=second_observed_at + timedelta(seconds=1),
        producer="integration-test",
        producer_version="1",
        payload={
            "listing": dataclasses.replace(
                second,
                listing_id=ProviderListingId("ig", "demo", f"INVALID.{suffix}"),
                valid_from=second_observed_at + timedelta(seconds=1),
            ),
            "universe_hash": "invalid",
        },
    )
    with pytest.raises(
        ValueError, match="provider listing event universe hash must be lower-case SHA-256"
    ):
        await store.append(invalid_event, expected_stream_version=0)
    assert await store.query(
        "SELECT count(*) AS count FROM canonical.events WHERE event_id = :event_id",
        {"event_id": invalid_event.event_id},
    ) == [{"count": 0}]
    indexes = await store.query(
        """
        SELECT indexdef FROM pg_indexes
        WHERE schemaname = 'reference'
          AND indexname = 'uq_provider_listings_active_instrument'
        """
    )
    assert len(indexes) == 1
    assert "UNIQUE INDEX" in indexes[0]["indexdef"]
    assert "WHERE (valid_to IS NULL)" in indexes[0]["indexdef"]

    await store.rebuild_projections()

    rebuilt = await store.query(
        """
        SELECT external_id, valid_from, valid_to, metadata_version, universe_hash
        FROM reference.provider_listings
        WHERE instrument_id = :instrument_id
        ORDER BY valid_from
        """,
        {"instrument_id": str(instrument.instrument_id)},
    )
    assert rebuilt == expected_rows
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
async def test_canonical_event_feed_is_bounded_and_cursor_driven() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    store = PostgresAuditStore(engine)
    now = datetime.now(UTC)
    suffix = uuid4().hex
    first = await store.append(
        EventEnvelope.create(
            stream_id=f"feed:{suffix}:1",
            stream_version=1,
            event_type="FeedTestObserved",
            event_time=now,
            received_time=now,
            producer="integration-test",
            producer_version="1",
            payload={"sequence": 1},
        ),
        expected_stream_version=0,
    )
    second = await store.append(
        EventEnvelope.create(
            stream_id=f"feed:{suffix}:2",
            stream_version=1,
            event_type="FeedTestObserved",
            event_time=now,
            received_time=now,
            producer="integration-test",
            producer_version="1",
            payload={"sequence": 2},
        ),
        expected_stream_version=0,
    )
    assert first.global_position is not None
    assert second.global_position is not None

    settings = Settings(database_url=DATABASE_URL, capture_source_id="integration-capture")
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first_page = await client.get(
            "/api/v1/feed/events",
            params={"after_position": first.global_position - 1, "limit": 1},
        )
        second_page = await client.get(
            "/api/v1/feed/events",
            params={"after_position": first.global_position, "limit": 1000},
        )
        empty_page = await client.get(
            "/api/v1/feed/events",
            params={"after_position": second.global_position},
        )
        invalid_limit = await client.get("/api/v1/feed/events", params={"limit": 1001})
        negative_cursor = await client.get("/api/v1/feed/events", params={"after_position": -1})
        invalid_cursor = await client.get(
            "/api/v1/feed/events",
            params={"after_position": second.global_position + 1},
        )
        write_probe = await client.post("/api/v1/feed/events")

    assert first_page.status_code == 200
    first_payload = first_page.json()
    assert first_payload["feed_schema_version"] == 1
    assert first_payload["source_id"] == "integration-capture"
    assert first_payload["universe_name"] == "capture-v1"
    assert first_payload["after_position"] == first.global_position - 1
    assert first_payload["next_position"] == first.global_position
    assert first_payload["has_more"] is True
    assert first_payload["events"][0]["event_id"] == str(first.event_id)
    assert "raw_record_id" not in first_payload["events"][0]
    decoded_first_page = decode_capture_feed_page(first_page.text)
    assert decoded_first_page.events[0].event_id == first.event_id
    assert decoded_first_page.identity.source_id == "integration-capture"

    assert second_page.status_code == 200
    second_payload = second_page.json()
    assert second_payload["events"][0]["event_id"] == str(second.event_id)
    assert second_payload["next_position"] >= second.global_position
    assert empty_page.status_code == 200
    assert empty_page.json()["events"] == []
    assert empty_page.json()["next_position"] == second.global_position
    assert empty_page.json()["has_more"] is False
    assert invalid_limit.status_code == 422
    assert negative_cursor.status_code == 422
    assert invalid_cursor.status_code == 409
    assert write_probe.status_code == 405

    async with HttpCaptureFeedClient(
        "http://127.0.0.1:18080",
        transport=httpx.ASGITransport(app=app),
    ) as feed_client:
        client_page = await feed_client.fetch_page(
            after_position=first.global_position - 1,
            limit=1,
        )
    assert client_page.identity.source_id == "integration-capture"
    assert client_page.events[0].event_id == first.event_id
    assert client_page.next_position == first.global_position

    await engine_from_app(app).dispose()
    await engine.dispose()


@pytest.mark.asyncio
async def test_historical_bar_append_is_idempotent_and_appends_corrections() -> None:
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
    corrected_bar = dataclasses.replace(bar, close=Decimal("0.65006"))
    correction = await _append_bar(
        store,
        corrected_bar,
        received_time=interval_start + timedelta(minutes=2),
    )
    corrected_duplicate = await _append_bar(
        store,
        corrected_bar,
        received_time=interval_start + timedelta(minutes=2),
    )

    assert first is not None
    assert duplicate is None
    assert correction is not None
    assert correction.event_type == "MarketBarCorrected"
    assert correction.stream_version == 2
    assert correction.payload["revision"] == 2
    assert corrected_duplicate is None
    rows = await store.query(
        """
        SELECT count(*) AS event_count, max(stream_version) AS latest_version
        FROM canonical.events
        WHERE stream_id LIKE :stream_prefix
        """,
        {"stream_prefix": f"historical-bar:fx:aud-usd:BID:ig:demo:{external_id}:%"},
    )
    assert rows[0] == {"event_count": 2, "latest_version": 2}
    projected = await store.query(
        """
        SELECT revision, close
        FROM read_model.market_bars
        WHERE instrument_id = :instrument_id AND basis = 'BID'
          AND interval_start = :interval_start AND provenance = 'IG_HISTORICAL'
          AND source_external_id = :external_id
        ORDER BY revision
        """,
        {
            "instrument_id": str(bar.instrument_id),
            "interval_start": interval_start,
            "external_id": external_id,
        },
    )
    assert projected == [
        {"revision": 1, "close": Decimal("0.65005")},
        {"revision": 2, "close": Decimal("0.65006")},
    ]
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
        instrument = await client.get("/api/v1/instruments/fx:aud-usd")
        production_probe = await client.post("/api/v1/orders")

    assert health.status_code == 200
    assert health.json()["mode"] == "data-only"
    assert instruments.status_code == 200
    assert len(instruments.json()) == 7
    assert instrument.status_code == 200
    assert instrument.json()["instrument"]["instrument_id"] == "fx:aud-usd"
    assert production_probe.status_code == 404
    await engine_from_app(app).dispose()


@pytest.mark.asyncio
async def test_capture_reader_can_query_approved_schemas_but_not_raw_or_write() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)

    async with engine.connect() as connection:
        attributes = (
            await connection.execute(
                text(
                    """
                    SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole,
                           rolreplication, rolbypassrls
                    FROM pg_roles
                    WHERE rolname = 'qtrad_capture_reader'
                    """
                )
            )
        ).one()
    assert tuple(attributes) == (False, False, False, False, False, False)

    async with engine.connect() as connection:
        transaction = await connection.begin()
        await connection.execute(text("SET LOCAL ROLE qtrad_capture_reader"))
        for table in (
            "canonical.events",
            "reference.instruments",
            "read_model.latest_quotes",
            "ops.runs",
        ):
            await connection.execute(text(f"SELECT count(*) FROM {table}"))
        await transaction.rollback()

    for prohibited_statement in (
        "SELECT count(*) FROM raw.market_messages",
        "UPDATE read_model.latest_quotes SET quality = quality WHERE false",
    ):
        async with engine.connect() as connection:
            transaction = await connection.begin()
            await connection.execute(text("SET LOCAL ROLE qtrad_capture_reader"))
            with pytest.raises(DBAPIError):
                await connection.execute(text(prohibited_statement))
            await transaction.rollback()

    await engine.dispose()


@pytest.mark.asyncio
async def test_version_two_manifests_are_immutable_and_legacy_writer_remains_compatible() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    store = PostgresAuditStore(engine)
    created_at = datetime(2026, 7, 14, tzinfo=UTC)
    manifest = ResearchManifest(
        manifest_id="c" * 24,
        manifest_sha256="c" * 64,
        created_at=created_at,
        schema_version=2,
        universe_name="integration-universe",
        row_count=0,
        minimum_event_time=None,
        maximum_event_time=None,
        content_sha256="d" * 64,
        configuration_hash="a" * 64,
        files=(),
        file_sha256={},
        metadata={"manifest_contract": "qtrad-research-bars-v2"},
    )

    await store.record_manifest(manifest)
    await store.record_manifest(manifest)
    with pytest.raises(RuntimeError, match="conflicts with its identity"):
        await store.record_manifest(dataclasses.replace(manifest, configuration_hash="b" * 64))

    legacy_content_hash = "e" * 64
    legacy_manifest_id = legacy_content_hash[:24]
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO ops.research_manifests (
                    manifest_id, created_at, schema_version, row_count,
                    minimum_event_time, maximum_event_time, content_sha256,
                    configuration_hash, files, metadata
                ) VALUES (
                    :manifest_id, :created_at, 1, 0,
                    NULL, NULL, :content_sha256, :configuration_hash,
                    '[]'::jsonb, '{}'::jsonb
                )
                """
            ),
            {
                "manifest_id": legacy_manifest_id,
                "created_at": created_at,
                "content_sha256": legacy_content_hash,
                "configuration_hash": "a" * 64,
            },
        )

    rows = await store.query(
        """
        SELECT manifest_id, schema_version, manifest_sha256, universe_name, file_sha256
        FROM ops.research_manifests
        WHERE manifest_id IN (:current_id, :legacy_id)
        ORDER BY schema_version
        """,
        {"current_id": manifest.manifest_id, "legacy_id": legacy_manifest_id},
    )
    assert rows == [
        {
            "manifest_id": legacy_manifest_id,
            "schema_version": 1,
            "manifest_sha256": None,
            "universe_name": None,
            "file_sha256": None,
        },
        {
            "manifest_id": manifest.manifest_id,
            "schema_version": 2,
            "manifest_sha256": manifest.manifest_sha256,
            "universe_name": manifest.universe_name,
            "file_sha256": {},
        },
    ]
    await engine.dispose()


@pytest.mark.asyncio
async def test_registered_backfill_plan_projects_and_closes_exact_historical_coverage() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    store = PostgresAuditStore(engine)
    await store.seed_instruments()
    instrument = INITIAL_INSTRUMENTS[0]
    suffix = uuid4().hex
    listing = ProviderListing(
        listing_id=ProviderListingId("ig", "demo", f"BACKFILL.{suffix}"),
        instrument_id=instrument.instrument_id,
        display_name="Backfill integration listing",
        product_type=ProductType.SPOT_FX,
        currency=instrument.quote_currency,
        minimum_deal_size=Decimal("0.5"),
        price_increment=Decimal("0.0001"),
        valid_from=datetime(2020, 1, 1, tzinfo=UTC),
        valid_to=datetime(2020, 1, 1, 1, tzinfo=UTC),
        metadata_version=f"fixture-{suffix}",
    )
    plan = build_backfill_plan(
        universe_name="integration-universe",
        universe_hash="a" * 64,
        instrument_ids=(instrument.instrument_id,),
        listings=(listing,),
        preferred_epics={instrument.instrument_id: listing.listing_id.external_id},
        start=datetime(2026, 7, 13, 22, tzinfo=UTC),
        end=datetime(2026, 7, 13, 23, tzinfo=UTC),
        remaining_allowance=1000,
        quota_observed_at=datetime(2026, 7, 14, tzinfo=UTC),
        created_at=datetime(2026, 7, 14, tzinfo=UTC),
    )
    live_gaps_before = await store.query("SELECT count(*) AS count FROM read_model.data_gaps")

    await store.register_backfill_plan(plan, backfill_plan_payload(plan))
    await store.register_backfill_plan(plan, backfill_plan_payload(plan))
    await store.upsert_provider_listing(listing, {}, universe_hash=plan.universe_hash)
    exact_listing = await store.provider_listing_version(plan.items[0])
    assert exact_listing == listing
    usage_run_id = await store.start_run(
        kind=RunKind.BACKFILL,
        environment=BrokerEnvironment.IG_DEMO,
        configuration_hash=plan.universe_hash,
        started_at=datetime(2026, 7, 14, 0, 0, tzinfo=UTC),
    )
    usage_request_id = uuid4()
    await store.start_historical_request_usage(
        request_id=usage_request_id,
        run_id=usage_run_id,
        plan_hash=plan.plan_hash,
        instrument_id=instrument.instrument_id,
        listing_id=listing.listing_id,
        interval_start=plan.start,
        interval_end=plan.end,
        requested_points=plan.requested_points,
        started_at=datetime(2026, 7, 14, 0, 0, 1, tzinfo=UTC),
    )
    await store.complete_historical_request_usage(
        usage_request_id,
        returned_points=60,
        provider_remaining=940,
        completed_at=datetime(2026, 7, 14, 0, 0, 2, tzinfo=UTC),
    )
    usage = await store.query(
        """
        SELECT plan_hash, requested_points, returned_points, provider_remaining, completed_at
        FROM ops.historical_request_usage
        WHERE request_id = :request_id
        """,
        {"request_id": usage_request_id},
    )
    assert usage == [
        {
            "plan_hash": plan.plan_hash,
            "requested_points": plan.requested_points,
            "returned_points": 60,
            "provider_remaining": 940,
            "completed_at": datetime(2026, 7, 14, 0, 0, 2, tzinfo=UTC),
        }
    ]
    with pytest.raises(RuntimeError, match="already completed"):
        await store.complete_historical_request_usage(
            usage_request_id,
            returned_points=60,
            provider_remaining=940,
            completed_at=datetime(2026, 7, 14, 0, 0, 3, tzinfo=UTC),
        )

    rows = await store.query(
        """
        SELECT provenance, basis, resolution, source_listing_valid_from,
               source_listing_metadata_version, detected_by_plan_hash,
               covered_at, covered_by_plan_hash, observed_points
        FROM read_model.historical_coverage_gaps
        WHERE detected_by_plan_hash = :plan_hash
        ORDER BY basis
        """,
        {"plan_hash": plan.plan_hash},
    )
    assert len(rows) == 3
    assert {row["provenance"] for row in rows} == {"IG_HISTORICAL"}
    assert {row["basis"] for row in rows} == {"ASK", "BID", "MID"}
    assert {row["resolution"] for row in rows} == {"MINUTE"}
    assert {row["source_listing_metadata_version"] for row in rows} == {listing.metadata_version}
    assert all(row["covered_at"] is None for row in rows)

    claimed = await store.claim_backfill_plan(plan.plan_hash)
    assert claimed == backfill_plan_payload(plan)
    with pytest.raises(RuntimeError, match="status EXECUTING"):
        await store.claim_backfill_plan(plan.plan_hash)
    await store.fail_backfill_plan(
        plan.plan_hash,
        executed_at=datetime(2026, 7, 14, 0, 1, tzinfo=UTC),
    )
    await store.claim_backfill_plan(plan.plan_hash)
    await store.complete_backfill_plan(
        plan,
        observed_points={
            (instrument.instrument_id, PriceBasis.BID): 60,
            (instrument.instrument_id, PriceBasis.ASK): 60,
            (instrument.instrument_id, PriceBasis.MID): 60,
        },
        executed_at=datetime(2026, 7, 14, 0, 2, tzinfo=UTC),
    )

    completed = await store.query(
        "SELECT status FROM ops.backfill_plans WHERE plan_hash = :plan_hash",
        {"plan_hash": plan.plan_hash},
    )
    assert completed == [{"status": "COMPLETED"}]
    covered = await store.query(
        """
        SELECT covered_by_plan_hash, observed_points
        FROM read_model.historical_coverage_gaps
        WHERE detected_by_plan_hash = :plan_hash
        """,
        {"plan_hash": plan.plan_hash},
    )
    assert len(covered) == 3
    assert {row["covered_by_plan_hash"] for row in covered} == {plan.plan_hash}
    assert {row["observed_points"] for row in covered} == {60}
    with pytest.raises(RuntimeError, match="status COMPLETED"):
        await store.claim_backfill_plan(plan.plan_hash)
    assert await store.query("SELECT count(*) AS count FROM read_model.data_gaps") == (
        live_gaps_before
    )

    repeat_plan = build_backfill_plan(
        universe_name="integration-universe",
        universe_hash="a" * 64,
        instrument_ids=(instrument.instrument_id,),
        listings=(listing,),
        preferred_epics={instrument.instrument_id: listing.listing_id.external_id},
        start=plan.start,
        end=plan.end,
        remaining_allowance=1000,
        quota_observed_at=datetime(2026, 7, 15, tzinfo=UTC),
        created_at=datetime(2026, 7, 15, tzinfo=UTC),
    )
    assert repeat_plan.plan_hash != plan.plan_hash
    await store.register_backfill_plan(repeat_plan, backfill_plan_payload(repeat_plan))
    coverage_attempts = await store.query(
        """
        SELECT detected_by_plan_hash, covered_by_plan_hash
        FROM read_model.historical_coverage_gaps
        WHERE source_external_id = :external_id
          AND interval_start = :interval_start AND interval_end = :interval_end
        """,
        {
            "external_id": listing.listing_id.external_id,
            "interval_start": plan.start,
            "interval_end": plan.end,
        },
    )
    assert len(coverage_attempts) == 6
    assert {
        (row["detected_by_plan_hash"], row["covered_by_plan_hash"]) for row in coverage_attempts
    } == {
        (plan.plan_hash, plan.plan_hash),
        (repeat_plan.plan_hash, None),
    }

    app = create_app(Settings(database_url=DATABASE_URL))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        bounded_response = await client.get("/api/v1/historical-coverage", params={"limit": 1})
        coverage_response = await client.get(
            "/api/v1/historical-coverage",
            params={"instrument_id": str(instrument.instrument_id)},
        )
        open_response = await client.get(
            "/api/v1/historical-coverage",
            params={"instrument_id": str(instrument.instrument_id), "only_open": True},
        )
        invalid_limit = await client.get("/api/v1/historical-coverage", params={"limit": 5001})

    assert bounded_response.status_code == 200
    assert len(bounded_response.json()) == 1
    assert coverage_response.status_code == 200
    matching_coverage = [
        row for row in coverage_response.json() if row["detected_by_plan_hash"] == plan.plan_hash
    ]
    assert len(matching_coverage) == 3
    assert {row["covered_by_plan_hash"] for row in matching_coverage} == {plan.plan_hash}
    assert not any(row["detected_by_plan_hash"] == plan.plan_hash for row in open_response.json())
    assert (
        sum(row["detected_by_plan_hash"] == repeat_plan.plan_hash for row in open_response.json())
        == 3
    )
    assert invalid_limit.status_code == 422

    await store.claim_backfill_plan(repeat_plan.plan_hash)
    await store.complete_backfill_plan(
        repeat_plan,
        observed_points={
            (instrument.instrument_id, PriceBasis.BID): 0,
            (instrument.instrument_id, PriceBasis.ASK): 0,
            (instrument.instrument_id, PriceBasis.MID): 0,
        },
        executed_at=datetime(2026, 7, 15, 0, 2, tzinfo=UTC),
        allow_empty=True,
    )
    empty_result = await store.query(
        """
        SELECT request_completed_at, returned_points, covered_at,
               covered_by_plan_hash, observed_points
        FROM read_model.historical_coverage_gaps
        WHERE detected_by_plan_hash = :plan_hash
        """,
        {"plan_hash": repeat_plan.plan_hash},
    )
    assert len(empty_result) == 3
    assert all(row["request_completed_at"] is not None for row in empty_result)
    assert {row["returned_points"] for row in empty_result} == {0}
    assert all(row["covered_at"] is None for row in empty_result)
    assert all(row["covered_by_plan_hash"] is None for row in empty_result)
    assert all(row["observed_points"] is None for row in empty_result)

    await engine_from_app(app).dispose()
    await engine.dispose()


@pytest.mark.asyncio
async def test_storage_inspector_is_bounded_and_reports_capture_relations() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)

    measurement = await PostgresStorageInspector(engine).measure()

    relation_names = {
        f"{relation.schema_name}.{relation.relation_name}" for relation in measurement.relations
    }
    assert {"raw.market_messages", "canonical.events"}.issubset(relation_names)
    assert measurement.database_bytes > 0
    assert measurement.raw_message_count >= 0
    assert measurement.canonical_event_count >= 0
    assert measurement.raw_payload_representation_column_present is True
    assert (
        sum(count.row_count for count in measurement.raw_payload_representation_counts)
        == measurement.raw_message_count
    )
    assert measurement.raw_payload_sample.sample_rows <= 10_000
    assert measurement.canonical_payload_sample.sample_rows <= 10_000
    await engine.dispose()


@pytest.mark.asyncio
async def test_configuration_identity_constraints_are_validated_and_reject_bad_new_rows() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    store = PostgresAuditStore(engine)
    await store.seed_instruments()

    constraints = await store.query(
        """
        SELECT conname, convalidated
        FROM pg_constraint
        WHERE conname IN (
            'ck_runs_configuration_hash',
            'ck_research_manifests_configuration_hash',
            'ck_backfill_plans_plan_hash',
            'ck_backfill_plans_universe_hash',
            'ck_provider_listings_universe_hash'
        )
        ORDER BY conname
        """
    )
    assert constraints == [
        {"conname": "ck_backfill_plans_plan_hash", "convalidated": True},
        {"conname": "ck_backfill_plans_universe_hash", "convalidated": True},
        {"conname": "ck_provider_listings_universe_hash", "convalidated": True},
        {"conname": "ck_research_manifests_configuration_hash", "convalidated": True},
        {"conname": "ck_runs_configuration_hash", "convalidated": True},
    ]

    suffix = uuid4().hex
    invalid_statements = (
        (
            """
            INSERT INTO ops.runs (
                run_id, kind, status, environment, started_at, configuration_hash, detail
            ) VALUES (
                :run_id, 'INGESTION', 'RUNNING', 'IG_DEMO', :now, 'invalid', '{}'::jsonb
            )
            """,
            {"run_id": uuid4(), "now": datetime(2026, 7, 14, tzinfo=UTC)},
        ),
        (
            """
            INSERT INTO ops.research_manifests (
                manifest_id, created_at, schema_version, row_count,
                content_sha256, configuration_hash, files, metadata
            ) VALUES (
                :manifest_id, :now, 1, 0, :content_sha256, 'invalid', '[]'::jsonb, '{}'::jsonb
            )
            """,
            {
                "manifest_id": suffix[:24],
                "now": datetime(2026, 7, 14, tzinfo=UTC),
                "content_sha256": "a" * 64,
            },
        ),
        (
            """
            INSERT INTO ops.backfill_plans (
                plan_id, plan_hash, universe_hash, status, plan, created_at
            ) VALUES (
                :plan_id, :plan_hash, 'invalid', 'PLANNED', '{}'::jsonb, :now
            )
            """,
            {
                "plan_id": uuid4(),
                "plan_hash": "b" * 64,
                "now": datetime(2026, 7, 14, tzinfo=UTC),
            },
        ),
        (
            """
            INSERT INTO ops.backfill_plans (
                plan_id, plan_hash, universe_hash, status, plan, created_at
            ) VALUES (
                :plan_id, 'invalid', :universe_hash, 'PLANNED', '{}'::jsonb, :now
            )
            """,
            {
                "plan_id": uuid4(),
                "universe_hash": "c" * 64,
                "now": datetime(2026, 7, 14, tzinfo=UTC),
            },
        ),
        (
            """
            INSERT INTO reference.provider_listings (
                provider, environment, external_id, instrument_id, display_name,
                product_type, currency, minimum_deal_size, price_increment,
                valid_from, valid_to, metadata_version, metadata, economics, universe_hash
            ) VALUES (
                'ig', 'demo', :external_id, :instrument_id, 'Invalid identity fixture',
                'SPOT_FX', 'USD', 1, 0.0001,
                :valid_from, :valid_to, 'fixture', '{}'::jsonb, '{}'::jsonb, 'invalid'
            )
            """,
            {
                "external_id": f"INVALID.{suffix}",
                "instrument_id": str(INITIAL_INSTRUMENTS[0].instrument_id),
                "valid_from": datetime(2026, 7, 14, tzinfo=UTC),
                "valid_to": datetime(2026, 7, 14, 1, tzinfo=UTC),
            },
        ),
    )
    for statement, parameters in invalid_statements:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            with pytest.raises(DBAPIError):
                await connection.execute(text(statement), parameters)
            await transaction.rollback()

    await engine.dispose()


@pytest.mark.asyncio
async def test_stale_run_reconciliation_is_exact_atomic_and_preserves_current_run() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    store = PostgresAuditStore(engine)
    cutoff = datetime(2026, 7, 14, 3, 5, 33, 653928, tzinfo=UTC)
    configuration_hash = uuid4().hex * 2
    mismatch_hash = uuid4().hex * 2
    created_run_ids = []
    try:
        first = await store.start_run(
            kind=RunKind.INGESTION,
            environment=BrokerEnvironment.IG_DEMO,
            configuration_hash=configuration_hash,
            started_at=cutoff - timedelta(minutes=2),
        )
        second = await store.start_run(
            kind=RunKind.INGESTION,
            environment=BrokerEnvironment.IG_DEMO,
            configuration_hash=configuration_hash,
            started_at=cutoff - timedelta(minutes=1),
        )
        current = await store.start_run(
            kind=RunKind.INGESTION,
            environment=BrokerEnvironment.IG_DEMO,
            configuration_hash=configuration_hash,
            started_at=cutoff + timedelta(seconds=1),
        )
        created_run_ids.extend((first.value, second.value, current.value))
        targets = await store.stale_running_ingestion_runs(
            cutoff=cutoff,
            environment=BrokerEnvironment.IG_DEMO,
            configuration_hash=configuration_hash,
        )
        plan = build_run_reconciliation_plan(
            targets=targets,
            created_at=cutoff + timedelta(hours=1),
            cutoff=cutoff,
            capture_source_id="integration-capture",
            database_name=await store.database_name(),
            universe_name="capture-v1",
            configuration_hash=configuration_hash,
            application_version="0.1.0",
            application_image="example.invalid/qtrad@sha256:" + "a" * 64,
            environment=BrokerEnvironment.IG_DEMO,
        )

        reconciled = await store.reconcile_stale_ingestion_runs(
            plan,
            reconciled_at=cutoff + timedelta(hours=2),
        )

        assert reconciled == 2
        rows = await store.query(
            """
            SELECT run_id, status, finished_at, detail
            FROM ops.runs
            WHERE configuration_hash = :configuration_hash
            ORDER BY started_at
            """,
            {"configuration_hash": configuration_hash},
        )
        assert [row["status"] for row in rows] == ["FAILED", "FAILED", "RUNNING"]
        for row in rows[:2]:
            assert row["finished_at"] == cutoff
            assert row["detail"] == {
                "previous_status": "RUNNING",
                "reason_code": "PRE_CANDIDATE_PROCESS_INTERRUPTED",
                "reconciliation_plan_hash": plan.plan_hash,
                "reconciled_at": "2026-07-14T05:05:33.653928Z",
                "finished_at_basis": "OPERATOR_ASSERTED_CUTOFF_UPPER_BOUND",
                "cutoff": "2026-07-14T03:05:33.653928Z",
            }
        assert rows[2]["finished_at"] is None
        assert rows[2]["detail"] == {}
        with pytest.raises(RuntimeError, match="already terminal"):
            await store.finish_run(
                first,
                status="STOPPED",
                finished_at=cutoff + timedelta(hours=3),
                detail={},
            )

        omitted = await store.start_run(
            kind=RunKind.INGESTION,
            environment=BrokerEnvironment.IG_DEMO,
            configuration_hash=mismatch_hash,
            started_at=cutoff - timedelta(minutes=3),
        )
        planned = await store.start_run(
            kind=RunKind.INGESTION,
            environment=BrokerEnvironment.IG_DEMO,
            configuration_hash=mismatch_hash,
            started_at=cutoff - timedelta(minutes=4),
        )
        created_run_ids.extend((omitted.value, planned.value))
        mismatch_targets = await store.stale_running_ingestion_runs(
            cutoff=cutoff,
            environment=BrokerEnvironment.IG_DEMO,
            configuration_hash=mismatch_hash,
        )
        incomplete = build_run_reconciliation_plan(
            targets=mismatch_targets[:1],
            created_at=cutoff + timedelta(hours=1),
            cutoff=cutoff,
            capture_source_id="integration-capture",
            database_name=await store.database_name(),
            universe_name="capture-v1",
            configuration_hash=mismatch_hash,
            application_version="0.1.0",
            application_image="example.invalid/qtrad@sha256:" + "a" * 64,
            environment=BrokerEnvironment.IG_DEMO,
        )
        with pytest.raises(ValueError, match="omitted an eligible stale run"):
            await store.reconcile_stale_ingestion_runs(
                incomplete,
                reconciled_at=cutoff + timedelta(hours=2),
            )
        untouched = await store.query(
            "SELECT status FROM ops.runs WHERE configuration_hash = :configuration_hash",
            {"configuration_hash": mismatch_hash},
        )
        assert {row["status"] for row in untouched} == {"RUNNING"}
    finally:
        if created_run_ids:
            async with engine.begin() as connection:
                for run_id in created_run_ids:
                    await connection.execute(
                        text("DELETE FROM ops.runs WHERE run_id = :run_id"),
                        {"run_id": run_id},
                    )
        await engine.dispose()
