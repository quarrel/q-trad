import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from qtrad.adapters.ig.market_data import (
    IgDemoConfig,
    IgDemoMarketDataAdapter,
    _backoff_seconds,
    _historical_query_time,
    _historical_time,
)
from qtrad.domain.identifiers import InstrumentId, ProviderListingId
from qtrad.domain.instruments import ProductType, ProviderListing
from qtrad.domain.operations import HealthStatus
from qtrad.ports.market_data import BackfillRequest, MarketDataRecord


class FakeSession:
    def __init__(self) -> None:
        self.headers = {
            "CST": "not-a-real-token",
            "X-SECURITY-TOKEN": "not-a-real-token",
        }


class MutableClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 7, 3, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current


class FakeService:
    def __init__(
        self,
        *,
        failure: Exception | None = None,
        historical_response: dict[str, Any] | None = None,
    ) -> None:
        self.failure = failure
        self.historical_response = historical_response
        self.session = FakeSession()
        self.logged_out = False

    def create_session(self) -> dict[str, str]:
        if self.failure is not None:
            raise self.failure
        return {
            "lightstreamerEndpoint": "https://stream.invalid",
            "currentAccountId": "not-a-real-account",
        }

    def logout(self) -> None:
        self.logged_out = True

    def search_markets(self, search_term: str) -> object:
        raise AssertionError(f"unexpected search for {search_term}")

    def fetch_market_by_epic(self, epic: str) -> object:
        raise AssertionError(f"unexpected market fetch for {epic}")

    def fetch_historical_prices_by_epic_and_date_range(
        self, epic: str, resolution: str, start: str, end: str
    ) -> dict[str, Any]:
        assert epic and resolution == "MINUTE" and start < end
        return self.historical_response or {"prices": []}


class FakeStreamAdapter(IgDemoMarketDataAdapter):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.open_calls = 0
        self.close_calls = 0
        self.active_streams = 0
        self.maximum_active_streams = 0

    async def _open_stream(self, listings: Sequence[ProviderListing]) -> None:
        assert listings
        if self.active_streams:
            raise RuntimeError("concurrent stream")
        self.active_streams += 1
        self.maximum_active_streams = max(self.maximum_active_streams, self.active_streams)
        self.open_calls += 1
        self._stream_connected_at = self._clock.now()
        self._status = HealthStatus.HEALTHY

    async def _close_stream(self) -> None:
        self.close_calls += 1
        self.active_streams = 0


class StaleAdapter(IgDemoMarketDataAdapter):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.scheduled_reconnects = 0

    def _schedule_reconnect(self) -> None:
        self.scheduled_reconnects += 1


class FakeUpdate:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    def getValue(self, field: str) -> str | None:
        return self.values.get(field)


def config(**overrides: Any) -> IgDemoConfig:
    values: dict[str, Any] = {
        "username": "demo",
        "password": "not-a-real-password",
        "api_key": "not-a-real-key",
        "account_id": "not-a-real-account",
        "initial_backoff_seconds": 1,
        "maximum_backoff_seconds": 2,
        "stale_after_seconds": 5,
    }
    values.update(overrides)
    return IgDemoConfig(**values)


def listing() -> ProviderListing:
    return ProviderListing(
        listing_id=ProviderListingId("ig", "demo", "CS.D.AUDUSD.CFD.IP"),
        instrument_id=InstrumentId("fx:aud-usd"),
        display_name="AUD/USD",
        product_type=ProductType.ROLLING_CFD,
        currency="USD",
        minimum_deal_size=Decimal("0.5"),
        price_increment=None,
        valid_from=datetime(2026, 7, 3, tzinfo=UTC),
        valid_to=None,
        metadata_version="test",
    )


@pytest.mark.asyncio
async def test_connect_retries_with_bounded_exponential_backoff() -> None:
    services = [
        FakeService(failure=ConnectionError("first")),
        FakeService(failure=ConnectionError("second")),
        FakeService(),
    ]
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    adapter = IgDemoMarketDataAdapter(
        config(connect_attempts=3),
        MutableClock(),
        sleep=sleep,
        service_factory=lambda _: services.pop(0),
    )
    await adapter.connect()

    assert sleeps == [1, 2]
    assert (await adapter.health()).status is HealthStatus.HEALTHY


@pytest.mark.asyncio
async def test_connect_exhaustion_disconnects_and_reraises_last_failure() -> None:
    services = [
        FakeService(failure=ConnectionError("first")),
        FakeService(failure=ConnectionError("last")),
    ]
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    adapter = IgDemoMarketDataAdapter(
        config(connect_attempts=2),
        MutableClock(),
        sleep=sleep,
        service_factory=lambda _: services.pop(0),
    )

    with pytest.raises(ConnectionError, match="last"):
        await adapter.connect()

    assert sleeps == [1]
    assert (await adapter.health()).status is HealthStatus.DISCONNECTED


@pytest.mark.asyncio
async def test_reconnect_refreshes_session_without_concurrent_streams() -> None:
    created_services: list[FakeService] = []

    def service_factory(_: IgDemoConfig) -> FakeService:
        service = FakeService()
        created_services.append(service)
        return service

    adapter = FakeStreamAdapter(
        config(),
        MutableClock(),
        service_factory=service_factory,
    )
    await adapter.connect()
    await adapter.subscribe((listing(),))
    await adapter.force_reconnect()

    assert len(created_services) == 2
    assert created_services[0].logged_out
    assert adapter.open_calls == 2
    assert adapter.maximum_active_streams == 1
    assert "reconnects=1" in ((await adapter.health()).detail or "")
    await adapter.disconnect()


@pytest.mark.asyncio
async def test_forced_reconnect_requires_subscription_and_rejects_overlap() -> None:
    adapter = IgDemoMarketDataAdapter(config(), MutableClock())

    with pytest.raises(RuntimeError, match="subscribe before"):
        await adapter.force_reconnect()

    adapter._desired_listings = (listing(),)
    adapter._reconnecting = True
    with pytest.raises(RuntimeError, match="already in progress"):
        await adapter.force_reconnect()


@pytest.mark.asyncio
async def test_reconnect_exhaustion_marks_adapter_disconnected() -> None:
    services = [
        FakeService(),
        FakeService(failure=ConnectionError("retry one")),
        FakeService(failure=ConnectionError("retry two")),
    ]
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    adapter = FakeStreamAdapter(
        config(reconnect_attempts=2),
        MutableClock(),
        sleep=sleep,
        service_factory=lambda _: services.pop(0),
    )
    await adapter.connect()
    await adapter.subscribe((listing(),))

    with pytest.raises(ConnectionError, match="retry two"):
        await adapter.force_reconnect()

    assert sleeps == [1]
    assert adapter.active_streams == 0
    assert (await adapter.health()).status is HealthStatus.DISCONNECTED


@pytest.mark.asyncio
async def test_stale_stream_degrades_and_schedules_reconnect() -> None:
    clock = MutableClock()
    adapter = StaleAdapter(config(), clock)
    adapter._service = FakeService()
    adapter._status = HealthStatus.HEALTHY
    adapter._desired_listings = (listing(),)
    adapter._stream_connected_at = clock.now()

    clock.current += timedelta(seconds=6)
    health = await adapter.health()

    assert health.status is HealthStatus.DEGRADED
    assert adapter.scheduled_reconnects == 1


@pytest.mark.asyncio
async def test_lightstreamer_terminal_disconnect_schedules_one_reconnect() -> None:
    adapter = StaleAdapter(config(), MutableClock())
    adapter._desired_listings = (listing(),)
    adapter._status = HealthStatus.HEALTHY

    adapter._handle_stream_status("DISCONNECTED:WILL-RETRY")
    assert adapter._status is HealthStatus.DEGRADED
    assert adapter.scheduled_reconnects == 0

    adapter._handle_stream_status("DISCONNECTED")
    assert adapter.scheduled_reconnects == 1


def test_subscription_error_degrades_health_without_exposing_message() -> None:
    adapter = IgDemoMarketDataAdapter(config(), MutableClock())
    adapter._status = HealthStatus.HEALTHY

    adapter._handle_subscription_error("CS.D.AUDUSD.CFD.IP", 19)

    assert adapter._status is HealthStatus.DEGRADED


@pytest.mark.asyncio
async def test_queue_saturation_drops_new_record_and_recovers_after_drain() -> None:
    clock = MutableClock()
    adapter = IgDemoMarketDataAdapter(config(queue_capacity=1), clock)
    adapter._service = FakeService()
    adapter._status = HealthStatus.HEALTHY
    record = MarketDataRecord(
        provider="ig",
        environment="demo",
        subscription="PRICE:CS.D.AUDUSD.CFD.IP",
        deduplication_key="one",
        received_time=clock.now(),
        raw_payload={},
        quote=None,
    )

    adapter._enqueue_record(record, "CS.D.AUDUSD.CFD.IP")
    adapter._enqueue_record(record, "CS.D.AUDUSD.CFD.IP")
    assert (await adapter.health()).status is HealthStatus.DEGRADED
    assert "dropped_records=1" in ((await adapter.health()).detail or "")

    received = await anext(adapter.records())
    assert received is record
    assert (await adapter.health()).status is HealthStatus.HEALTHY


@pytest.mark.asyncio
async def test_backfill_captures_provider_reported_allowance() -> None:
    clock = MutableClock()
    service = FakeService(
        historical_response={
            "prices": [],
            "allowance": {"remainingAllowance": 9965},
        }
    )
    adapter = IgDemoMarketDataAdapter(
        config(),
        clock,
        service_factory=lambda _: service,
    )
    await adapter.connect()
    request = BackfillRequest(
        instrument_id=InstrumentId("fx:aud-usd"),
        listing=listing(),
        start=clock.now(),
        end=clock.now() + timedelta(minutes=5),
        maximum_points=5,
    )

    assert [bar async for bar in adapter.backfill(request)] == []
    assert adapter.historical_allowance_remaining == 9965
    await adapter.disconnect()


@pytest.mark.asyncio
async def test_malformed_price_update_is_quarantinable_without_callback_failure() -> None:
    adapter = IgDemoMarketDataAdapter(config(), MutableClock())
    adapter._loop = asyncio.get_running_loop()
    adapter._stream_account_id = "not-a-real-account"
    adapter._listings_by_epic["CS.D.AUDUSD.CFD.IP"] = listing()

    adapter._on_update(
        "CS.D.AUDUSD.CFD.IP",
        FakeUpdate({"BIDPRICE1": "0.65000", "DLG_FLAG": "DEAL"}),
    )
    await asyncio.sleep(0)
    record = adapter._queue.get_nowait()

    assert record.quote is None
    assert record.error_code == "IG_NORMALISATION_FAILED"
    assert record.subscription == "PRICE:CS.D.AUDUSD.CFD.IP"


def test_price_callback_before_loop_registration_fails_explicitly() -> None:
    adapter = IgDemoMarketDataAdapter(config(), MutableClock())
    adapter._listings_by_epic["CS.D.AUDUSD.CFD.IP"] = listing()

    with pytest.raises(RuntimeError, match="before event loop registration"):
        adapter._on_update(
            "CS.D.AUDUSD.CFD.IP",
            FakeUpdate(
                {
                    "TIMESTAMP": "1783065600000",
                    "BIDPRICE1": "0.65000",
                    "ASKPRICE1": "0.65010",
                }
            ),
        )


@pytest.mark.asyncio
async def test_partial_price_update_preserves_one_sided_quote() -> None:
    adapter = IgDemoMarketDataAdapter(config(), MutableClock())
    adapter._loop = asyncio.get_running_loop()
    adapter._stream_account_id = "not-a-real-account"
    adapter._listings_by_epic["CS.D.AUDUSD.CFD.IP"] = listing()

    adapter._on_update(
        "CS.D.AUDUSD.CFD.IP",
        FakeUpdate(
            {
                "TIMESTAMP": "1783065600000",
                "BIDPRICE1": "0.65000",
                "DLG_FLAG": "CLOSED",
            }
        ),
    )
    await asyncio.sleep(0)
    quote = adapter._queue.get_nowait().quote

    assert quote is not None
    assert quote.bid == Decimal("0.65000")
    assert quote.ask is None
    assert quote.quality.value == "PARTIAL"


def test_backoff_is_exponential_and_capped() -> None:
    settings = config(initial_backoff_seconds=2, maximum_backoff_seconds=5)
    assert [_backoff_seconds(settings, attempt) for attempt in range(1, 5)] == [
        2,
        4,
        5,
        5,
    ]


def test_historical_query_uses_trading_ig_v2_datetime_format() -> None:
    value = datetime(2026, 7, 3, 9, 10, 11, tzinfo=UTC)
    assert _historical_query_time(value) == "2026-07-03 09:10:11"


def test_historical_query_normalises_across_dst_boundary() -> None:
    local = datetime(2026, 10, 4, 3, 30, tzinfo=ZoneInfo("Australia/Sydney"))
    assert _historical_query_time(local) == "2026-10-03 16:30:00"
    assert _historical_time({"snapshotTimeUTC": "2026-10-03T16:30:00Z"}) == datetime(
        2026, 10, 3, 16, 30, tzinfo=UTC
    )
