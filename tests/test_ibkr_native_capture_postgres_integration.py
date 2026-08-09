"""End-to-end PostgreSQL fixture for native capture composition."""

from __future__ import annotations

import os
from collections import deque
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from queue import Empty, Queue
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from qtrad.adapters.ibkr import capability
from qtrad.adapters.ibkr import market_data as native
from qtrad.adapters.postgres.queries import OperatorQueries
from qtrad.adapters.postgres.store import PostgresAuditStore
from qtrad.api.app import create_app, engine_from_app
from qtrad.application.ingestion import IngestionService
from qtrad.application.persistence import BoundedPersistenceWorker
from qtrad.domain.identifiers import InstrumentId, ProviderListingId
from qtrad.domain.instruments import ProductType, ProviderListing
from qtrad.domain.market_data import MarketDataSourceClass
from qtrad.domain.modes import BrokerEnvironment, RunKind
from qtrad.ports.capture_feed import CaptureIdentity
from qtrad.ports.ibkr_capability import IbkrContractEvidence
from qtrad.runtime.settings import Settings

DATABASE_URL = os.getenv("QTRAD_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        DATABASE_URL is None,
        reason="QTRAD_TEST_DATABASE_URL is required for PostgreSQL integration",
    ),
]

_NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
_CONFIGURATION_HASH = "b" * 64


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
        metadata_version="b2-fixture-v2",
    )


def _index_listing() -> ProviderListing:
    return ProviderListing(
        listing_id=ProviderListingId("ibkr", "IBKR_PAPER", "australia-200-b2"),
        instrument_id=InstrumentId("index:australia-200"),
        display_name="Australia 200 B2",
        product_type=ProductType.ROLLING_CFD,
        currency="AUD",
        minimum_deal_size=Decimal("1"),
        price_increment=Decimal("0.1"),
        valid_from=_NOW,
        valid_to=None,
        metadata_version="b2-index-fixture-v1",
    )


def _identity() -> CaptureIdentity:
    return CaptureIdentity(
        provider="ibkr",
        environment="IBKR_PAPER",
        source_class=MarketDataSourceClass.IBKR_NATIVE_CAPTURE,
        capture_source_id="ibkr-paper-v1",
        universe_id="capture-ibkr-v1",
        configuration_hash=_CONFIGURATION_HASH,
    )


class _FakeClient:
    def __init__(self, callbacks: Queue[capability._Callback]) -> None:
        self.callbacks = callbacks
        self.market_data_types: list[int] = []
        self.market_data_requests: list[tuple[int, object]] = []
        self.cancelled: list[int] = []
        self.disconnected = False
        self.on_market_data: deque[tuple[str, int, tuple[object, ...]]] = deque()
        self.callback_received_times: deque[datetime] = deque()

    def connect(self, host: str, port: int, *, clientId: int) -> None:
        assert (host, port, clientId) == ("127.0.0.1", 4002, 71)

    def run(self) -> None:
        self.callbacks.put(capability._Callback("next_valid_id", -1, (1,)))

    def disconnect(self) -> None:
        self.disconnected = True

    def reqCurrentTime(self) -> None:
        self.callbacks.put(capability._Callback("current_time", -1, (0,)))

    def reqMarketDataType(self, market_data_type: int) -> None:
        self.market_data_types.append(market_data_type)

    def reqMktData(
        self,
        request_id: int,
        contract: object,
        generic_tick_list: str,
        snapshot: bool,
        regulatory_snapshot: bool,
        options: list[object],
    ) -> None:
        assert (generic_tick_list, snapshot, regulatory_snapshot, options) == (
            "",
            False,
            False,
            [],
        )
        self.market_data_requests.append((request_id, contract))
        while self.on_market_data:
            kind, callback_request_id, values = self.on_market_data.popleft()
            self.callbacks.put(
                capability._Callback(
                    kind,
                    callback_request_id,
                    values,
                    received_time=self.callback_received_times.popleft(),
                )
            )

    def cancelMktData(self, request_id: int) -> None:
        self.cancelled.append(request_id)


def _adapter(
    monkeypatch: pytest.MonkeyPatch,
    client: _FakeClient,
    listings: tuple[ProviderListing, ...],
) -> native.IbkrNativeMarketDataAdapter:
    evidence = {
        listing.listing_id: IbkrContractEvidence(
            con_id=100 + index,
            symbol="EUR",
            local_symbol="EUR.USD",
            security_type="CASH",
            exchange="IDEALPRO",
            currency="USD",
            trading_class="",
            multiplier="",
            minimum_tick=Decimal("0.00005"),
            market_rule_ids=("26",),
            valid_exchanges=("IDEALPRO",),
            long_name="EUR.USD",
            underlier_con_id=None,
            timezone="UTC",
            trading_hours="20260808:0000-2400",
            liquid_hours="20260808:0000-2400",
        )
        for index, listing in enumerate(listings)
    }
    monkeypatch.setattr(
        native,
        "_contract_from_evidence",
        lambda item: SimpleNamespace(
            conId=item.con_id,
            symbol=item.symbol,
            localSymbol=item.local_symbol,
            secType=item.security_type,
            exchange=item.exchange,
            currency=item.currency,
            tradingClass=item.trading_class,
            multiplier=item.multiplier,
        ),
    )

    def client_factory(callbacks: Queue[capability._Callback]) -> _FakeClient:
        while True:
            try:
                callbacks.put(client.callbacks.get_nowait())
            except Empty:
                client.callbacks = callbacks
                return client

    return native.IbkrNativeMarketDataAdapter(
        capability.IbkrGatewayEndpoint(host="127.0.0.1", port=4002, client_id=71),
        pre_reviewed_listings=listings,
        contract_evidence=evidence,
        environment=BrokerEnvironment.IBKR_PAPER,
        client_factory=client_factory,
        clock=lambda: _NOW,
    )


@pytest.mark.asyncio
async def test_native_callbacks_worker_projection_health_and_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    store = PostgresAuditStore(engine)
    listing = _listing()
    identity = _identity()
    run_id = await store.start_run(
        kind=RunKind.INGESTION,
        environment=BrokerEnvironment.IBKR_PAPER,
        configuration_hash=identity.configuration_hash,
        started_at=_NOW,
    )
    client = _FakeClient(Queue())
    client.on_market_data.extend(
        (
            ("error", -1, (-1, 2104, "Market data farm connected")),
            ("error", -1, (-1, 2106, "Historical farm connected")),
            ("market_data_type", 1, (1,)),
            ("tick_price", 1, (1, 1.1000)),
            ("tick_price", 1, (2, 1.1002)),
        )
    )
    client.callback_received_times.extend(_NOW + timedelta(seconds=offset) for offset in range(5))
    adapter = _adapter(monkeypatch, client, (listing,))
    app = None
    try:
        await store.seed_native_capture_instruments((listing, _index_listing()))
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
        worker = BoundedPersistenceWorker(service, capacity=16)
        worker.start()
        await adapter.connect()
        await adapter.subscribe((listing,))
        records = [await anext(adapter.records()) for _ in range(5)]
        for record in records:
            worker.submit_nowait(record)
        await worker.drain_and_stop()
        assert await service.advance_bars(_NOW + timedelta(minutes=2)) == ()

        snapshot = worker.snapshot()
        assert snapshot.records_received == 5
        assert snapshot.persisted == 5
        assert snapshot.failed == 0
        assert snapshot.dropped == 0
        health = worker.compose_health(
            await adapter.health(),
            identity=identity,
            capture_session_id=str(run_id),
        )
        assert health.status.value == "HEALTHY"
        assert "PERSISTENCE_FAILURE" not in health.reason_codes
        await store.record_adapter_health(health)
        await store.record_capture_session_metrics(
            capture_session_id=run_id.value,
            provider=identity.provider,
            environment=identity.environment,
            source_class=identity.source_class.value,
            configuration_hash=identity.configuration_hash,
            observed_at=_NOW,
            records_received=snapshot.records_received,
            persisted=snapshot.persisted,
            failed=snapshot.failed,
            dropped=snapshot.dropped,
        )

        raw = await store.query(
            """
            SELECT source_class, capture_source_id, universe_id, configuration_hash,
                   connection_generation, arrival_sequence
            FROM raw.market_messages
            WHERE capture_session_id = CAST(:session_id AS uuid)
            ORDER BY arrival_sequence
            """,
            {"session_id": str(run_id)},
        )
        assert len(raw) == 5
        assert [row["arrival_sequence"] for row in raw] == [3, 4, 5, 6, 7]
        assert all(row["source_class"] == "IBKR_NATIVE_CAPTURE" for row in raw)
        assert all(row["configuration_hash"] == _CONFIGURATION_HASH for row in raw)

        reconciliation = await OperatorQueries(store).capture_reconciliation(
            provider=identity.provider,
            environment=identity.environment,
            source_class=identity.source_class.value,
            configuration_hash=identity.configuration_hash,
            capture_session_id=str(run_id),
        )
        assert reconciliation["adapter_accepted"] == 5
        assert reconciliation["raw_persisted"] == 5
        assert reconciliation["canonical_persisted"] == 2
        assert reconciliation["quarantined"] == 3
        assert reconciliation["records_dropped"] == 0
        assert reconciliation["records_failed"] == 0
        assert reconciliation["loss"] == 0
        assert reconciliation["reconciliation_source"] == "ops.capture_session_metrics"

        instrument = await store.query(
            """
            SELECT base_currency, quote_currency
            FROM reference.instruments
            WHERE instrument_id = 'index:australia-200'
            """
        )
        assert instrument == [{"base_currency": None, "quote_currency": "AUD"}]

        capture_quote = await store.query(
            """
            SELECT source_class, provider, environment, configuration_hash, bid, ask
            FROM read_model.capture_latest_quotes
            WHERE instrument_id = :instrument_id
            """,
            {"instrument_id": str(listing.instrument_id)},
        )
        assert len(capture_quote) == 1
        assert capture_quote[0]["configuration_hash"] == _CONFIGURATION_HASH
        assert capture_quote[0]["bid"] is None
        assert capture_quote[0]["ask"] == Decimal("1.1002")
        assert not await store.query(
            "SELECT 1 FROM read_model.latest_quotes WHERE instrument_id = :instrument_id",
            {"instrument_id": str(listing.instrument_id)},
        )

        await store.rebuild_projections()
        rebuilt_quote = await store.query(
            """
            SELECT configuration_hash, bid, ask
            FROM read_model.capture_latest_quotes
            WHERE instrument_id = :instrument_id
            """,
            {"instrument_id": str(listing.instrument_id)},
        )
        assert rebuilt_quote == [
            {
                "configuration_hash": _CONFIGURATION_HASH,
                "bid": None,
                "ask": Decimal("1.1002"),
            }
        ]

        app = create_app(
            Settings(
                database_url=DATABASE_URL,
                provider="ibkr",
                ibkr_capture_configuration_hash=_CONFIGURATION_HASH,
            )
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
            feed = await http_client.get("/api/v1/feed/events?limit=20")
            assert feed.status_code == 200
            feed_body = feed.json()
            assert feed_body["configuration_hash"] == _CONFIGURATION_HASH
            assert len(feed_body["events"]) == 2
            assert all(
                event["payload"]["capture_configuration_hash"] == _CONFIGURATION_HASH
                for event in feed_body["events"]
            )

            api_reconciliation = await http_client.get("/api/v1/capture/reconciliation")
            assert api_reconciliation.status_code == 200
            assert api_reconciliation.json()["adapter_accepted"] == 5

            qualification_evidence = await http_client.get(
                "/api/v1/capture/qualification-evidence",
                params={
                    "capture_session_id": str(run_id),
                    "started_at": _NOW.isoformat(),
                    "ended_at": (_NOW + timedelta(seconds=4)).isoformat(),
                    "generated_at": (_NOW + timedelta(seconds=5)).isoformat(),
                },
            )
            assert qualification_evidence.status_code == 200
            qualification_body = qualification_evidence.json()
            assert qualification_body["retained_row_count"] == 5
            assert len(qualification_body["retained_rows_sha256"]) == 64
            assert "retained_rows" not in qualification_body
            assert "operations" not in qualification_body

            instruments = await http_client.get("/api/v1/instruments")
            assert instruments.status_code == 200
            assert instruments.json()[0]["configuration_hash"] == _CONFIGURATION_HASH
            assert instruments.json()[0]["base_currency"] == "EUR"

            identity_response = await http_client.get("/api/v1/capture/identity")
            assert identity_response.status_code == 200
            assert identity_response.json()["latest_raw_identity"]["configuration_hash"] == (
                _CONFIGURATION_HASH
            )
    finally:
        if app is not None:
            await engine_from_app(app).dispose()
        await adapter.disconnect()
        await store.finish_run(
            run_id,
            status="STOPPED",
            finished_at=_NOW,
            detail={
                "fixture": "b2",
                "records_received": 5,
                "persisted": 5,
                "failed": 0,
                "dropped": 0,
            },
        )
        await engine.dispose()
