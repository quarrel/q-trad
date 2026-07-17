import asyncio
from collections.abc import Awaitable, Callable, Generator, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest
from trading_ig.rest import TokenInvalidException

from qtrad.adapters.ig import lightstreamer_compat
from qtrad.adapters.ig import market_data as ig_market_data
from qtrad.adapters.ig.market_data import (
    IgDemoConfig,
    IgDemoMarketDataAdapter,
    _backoff_seconds,
    _ConfigurableHttpSession,
    _ConnectionState,
    _historical_query_time,
    _historical_time,
    _install_default_http_timeout,
    _is_fatal_provider_error,
    _ProviderOperationTimeout,
    _safe_error_code,
    _validated_client_app_allowances,
)
from qtrad.domain.audit import RawPayloadRepresentation
from qtrad.domain.identifiers import InstrumentId, ProviderListingId
from qtrad.domain.instruments import ProductType, ProviderListing
from qtrad.domain.market_data import DataQuality, MarketQuote
from qtrad.domain.operations import HealthStatus
from qtrad.ports.market_data import BackfillRequest, MarketDataRecord


class FakeSession:
    def __init__(self) -> None:
        self.headers = {
            "CST": "not-a-real-token",
            "X-SECURITY-TOKEN": "not-a-real-token",
        }
        self.closed = False

    def close(self) -> None:
        self.closed = True


class RecordingRequestSession(FakeSession):
    def __init__(self) -> None:
        super().__init__()
        self.requests: list[dict[str, object]] = []

    def request(self, method: str, url: str, **kwargs: object) -> object:
        self.requests.append({"method": method, "url": url, **kwargs})
        return object()


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
        self.historical_request: tuple[str, str, str, str] | None = None

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
        self.historical_request = (epic, resolution, start, end)
        return self.historical_response or {"prices": []}


class FailingLogoutService(FakeService):
    def __init__(self) -> None:
        super().__init__()
        self.rate_limiter_stopped = False

    def logout(self) -> None:
        raise ConnectionError("logout unavailable")

    def _exit_bucket_threads(self) -> None:
        self.rate_limiter_stopped = True


class TokenInvalidReadService(FakeService):
    def __init__(self, *, fail_reads: bool) -> None:
        super().__init__()
        self.fail_reads = fail_reads
        self.search_calls = 0

    def search_markets(self, search_term: str) -> object:
        assert search_term == "probe"
        self.search_calls += 1
        if self.fail_reads:
            raise TokenInvalidException("error.security.client-token-invalid")
        return {"status": "fresh-session"}


class AllowanceExceededService(FakeService):
    def search_markets(self, search_term: str) -> object:
        assert search_term == "probe"
        raise RuntimeError("error.public-api.exceeded-account-allowance")


class RateLimitedService(FakeService):
    def __init__(self) -> None:
        super().__init__()
        self._trading_requests_per_minute = 7
        self._non_trading_requests_per_minute = 23
        self._qtrad_published_trading_requests_per_minute = 9
        self._qtrad_published_non_trading_requests_per_minute = 25


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
    def __init__(
        self,
        values: Mapping[str, str | None],
        *,
        changed_fields: set[str] | None = None,
    ) -> None:
        self.values = dict(values)
        self.changed_fields = set(values) if changed_fields is None else changed_fields

    def getValue(self, field: str) -> str | None:
        return self.values.get(field)

    def isValueChanged(self, field: str) -> bool:
        return field in self.changed_fields


class FakeConnectionDetails:
    def setUser(self, user: str) -> None:
        del user

    def setPassword(self, password: str) -> None:
        del password


class FakeAwaitable:
    def __await__(self) -> Generator[None, None, object]:
        if False:
            yield
        return object()


class FakeResponse:
    def close(self) -> Awaitable[object]:
        return FakeAwaitable()


class FakeCancellationToken:
    def __init__(self) -> None:
        self.callback: Callable[[object], None] | None = None
        self.response = FakeResponse()

    def done(self) -> bool:
        return False

    def result(self) -> FakeResponse:
        return self.response

    def add_done_callback(self, callback: Callable[[object], None]) -> None:
        self.callback = callback


class FakeWebSocket:
    def __init__(self) -> None:
        self.isCanceled = False
        self.cancellationToken = FakeCancellationToken()


class FakeStreamClient:
    def __init__(self) -> None:
        self.disconnected = False
        self.connectionDetails = FakeConnectionDetails()

    def addListener(self, listener: object) -> None:
        del listener

    def connect(self) -> None:
        pass

    def subscribe(self, subscription: object) -> None:
        del subscription

    def unsubscribe(self, subscription: object) -> None:
        del subscription

    def disconnect(self) -> None:
        self.disconnected = True

    def getStatus(self) -> str:
        return "DISCONNECTED" if self.disconnected else "CONNECTED:WS-STREAMING"


class UnconfirmedDisconnectClient(FakeStreamClient):
    def disconnect(self) -> None:
        pass


def config(**overrides: Any) -> IgDemoConfig:
    values: dict[str, Any] = {
        "username": "demo",
        "password": "not-a-real-password",
        "api_key": "not-a-real-key",
        "account_id": "not-a-real-account",
        "initial_backoff_seconds": 1,
        "maximum_backoff_seconds": 2,
        "reconnect_cooldown_seconds": 3,
        "maximum_reconnect_cycles": 1,
        "stale_after_seconds": 5,
        "stale_reconnect_after_seconds": 120,
        "readiness_timeout_seconds": 1,
        "retry_watchdog_seconds": 1,
        "shutdown_timeout_seconds": 1,
        "historical_request_interval_seconds": 0,
    }
    values.update(overrides)
    return IgDemoConfig(**values)


def mark_heartbeat_current(adapter: IgDemoMarketDataAdapter, clock: MutableClock) -> None:
    adapter._heartbeat_subscribed = True
    adapter._last_heartbeat_at = clock.now()
    adapter._heartbeat_events = 1


def test_trading_ig_http_session_has_bounded_defaults_and_allows_override() -> None:
    session = RecordingRequestSession()
    _install_default_http_timeout(cast(_ConfigurableHttpSession, session), (5.0, 15.0))

    session.request("GET", "https://example.invalid/default")
    session.request("GET", "https://example.invalid/override", timeout=(1.0, 2.0))

    assert session.requests == [
        {
            "method": "GET",
            "url": "https://example.invalid/default",
            "timeout": (5.0, 15.0),
        },
        {
            "method": "GET",
            "url": "https://example.invalid/override",
            "timeout": (1.0, 2.0),
        },
    ]


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


def market_record(
    clock: MutableClock,
    *,
    quality: DataQuality = DataQuality.HEALTHY,
) -> MarketDataRecord:
    selected_listing = listing()
    quote = MarketQuote(
        instrument_id=selected_listing.instrument_id,
        listing_id=selected_listing.listing_id,
        event_time=clock.now(),
        received_time=clock.now(),
        bid=Decimal("0.65000"),
        ask=Decimal("0.65010") if quality is DataQuality.HEALTHY else None,
        quality=quality,
    )
    return MarketDataRecord(
        provider="ig",
        environment="demo",
        subscription="PRICE:CS.D.AUDUSD.CFD.IP",
        deduplication_key=f"record-{quality}",
        received_time=clock.now(),
        raw_payload={},
        payload_representation=RawPayloadRepresentation.CHANGED_FIELDS,
        quote=quote,
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
        jitter=lambda _minimum, maximum: maximum,
        service_factory=lambda _: services.pop(0),
    )
    await adapter.connect()

    assert sleeps == [1, 2]
    assert (await adapter.health()).status is HealthStatus.STARTING


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
        jitter=lambda _minimum, maximum: maximum,
        service_factory=lambda _: services.pop(0),
    )

    with pytest.raises(ConnectionError, match="last"):
        await adapter.connect()

    assert sleeps == [1]
    assert (await adapter.health()).status is HealthStatus.DISCONNECTED


@pytest.mark.asyncio
async def test_fatal_authentication_error_is_not_retried() -> None:
    services = [
        FakeService(failure=RuntimeError("error.security.api-key-invalid")),
        FakeService(),
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

    with pytest.raises(RuntimeError, match="api-key-invalid"):
        await adapter.connect()

    assert sleeps == []
    assert len(services) == 1
    assert (await adapter.health()).status is HealthStatus.DISCONNECTED


def test_provider_error_classification_is_bounded_and_secret_free() -> None:
    assert _safe_error_code(TimeoutError("sensitive detail")) == "TIMEOUT"
    assert _safe_error_code(_ProviderOperationTimeout("logout")) == "OPERATION_TIMEOUT"
    assert _safe_error_code(ConnectionError("sensitive detail")) == "CONNECTION_ERROR"
    code = _safe_error_code(RuntimeError("request failed: error.security.api-key-invalid"))
    assert code == "error.security.api-key-invalid"
    assert _is_fatal_provider_error(code) is True
    assert _is_fatal_provider_error("OPERATION_TIMEOUT") is True
    assert _is_fatal_provider_error("error.public-api.exceeded-api-key-allowance") is False


def test_lightstreamer_disposal_uses_a_synchronous_done_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    close_operations: list[Awaitable[object]] = []
    monkeypatch.setattr(lightstreamer_compat, "_close_response", close_operations.append)
    socket = FakeWebSocket()

    lightstreamer_compat._dispose_ws_client(cast(Any, socket))
    callback = socket.cancellationToken.callback

    assert socket.isCanceled is True
    assert callback is not None
    callback(socket.cancellationToken)
    assert len(close_operations) == 1


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
        jitter=lambda _minimum, maximum: maximum,
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
    adapter._connection_state = _ConnectionState.READY
    selected_listing = listing()
    epic = selected_listing.listing_id.external_id
    adapter._desired_listings = (selected_listing,)
    adapter._listings_by_epic[epic] = selected_listing
    adapter._expected_epics = {epic}
    adapter._quote_received_times[epic] = clock.now()
    adapter._stream_connected_at = clock.now()
    mark_heartbeat_current(adapter, clock)

    clock.current += timedelta(seconds=6)
    health = await adapter.health()

    assert health.status is HealthStatus.DEGRADED
    assert adapter.scheduled_reconnects == 0

    clock.current += timedelta(seconds=115)
    await adapter.health()

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


def test_connected_transport_is_not_ready_without_subscription_and_healthy_update() -> None:
    clock = MutableClock()
    adapter = IgDemoMarketDataAdapter(config(), clock)
    epic = listing().listing_id.external_id
    adapter._generation = 3
    adapter._expected_epics = {epic}
    adapter._connection_state = _ConnectionState.SUBSCRIBING
    adapter._status = HealthStatus.STARTING

    adapter._handle_stream_status("CONNECTED:WS-STREAMING", generation=3)
    adapter._handle_subscription(epic, generation=3)

    assert adapter._status is HealthStatus.STARTING
    assert adapter._connection_state is _ConnectionState.SUBSCRIBING

    adapter._accept_update(
        market_record(clock, quality=DataQuality.PARTIAL),
        epic,
        generation=3,
    )
    assert adapter._status is HealthStatus.STARTING

    adapter._accept_update(market_record(clock), epic, generation=3)
    assert adapter._status is HealthStatus.STARTING

    adapter._handle_heartbeat_subscription(generation=3)
    adapter._handle_heartbeat("1783065600000", clock.now(), generation=3)
    assert adapter._status is HealthStatus.HEALTHY
    assert adapter._connection_state is _ConnectionState.READY


@pytest.mark.asyncio
async def test_one_active_channel_cannot_mask_another_required_channel_staleness() -> None:
    clock = MutableClock()
    adapter = StaleAdapter(config(), clock)
    epic = listing().listing_id.external_id
    other_epic = "IX.D.ASX.IFD.IP"
    adapter._generation = 3
    adapter._desired_listings = (listing(),)
    adapter._expected_epics = {epic, other_epic}
    adapter._subscribed_epics = {epic, other_epic}
    adapter._updated_epics = {epic, other_epic}
    adapter._quote_received_times = {
        epic: clock.now(),
        other_epic: clock.now(),
    }
    adapter._transport_connected = True
    adapter._connection_state = _ConnectionState.READY
    adapter._status = HealthStatus.HEALTHY
    mark_heartbeat_current(adapter, clock)

    clock.current += timedelta(seconds=6)
    adapter._accept_update(market_record(clock), epic, generation=3)
    health = await adapter.health()

    assert health.status is HealthStatus.DEGRADED
    assert adapter.scheduled_reconnects == 0


@pytest.mark.asyncio
async def test_fresh_heartbeat_does_not_mask_stale_price_channel() -> None:
    clock = MutableClock()
    adapter = StaleAdapter(config(), clock)
    selected_listing = listing()
    epic = selected_listing.listing_id.external_id
    adapter._generation = 3
    adapter._desired_listings = (selected_listing,)
    adapter._listings_by_epic[epic] = selected_listing
    adapter._expected_epics = {epic}
    adapter._subscribed_epics = {epic}
    adapter._updated_epics = {epic}
    adapter._quote_received_times[epic] = clock.now()
    adapter._transport_connected = True
    adapter._connection_state = _ConnectionState.READY
    adapter._status = HealthStatus.HEALTHY
    mark_heartbeat_current(adapter, clock)

    clock.current += timedelta(seconds=6)
    adapter._handle_heartbeat("1783065606000", clock.now(), generation=3)
    health = await adapter.health()

    assert health.status is HealthStatus.DEGRADED
    assert adapter._heartbeat_stale is False
    assert adapter._stale_epics == {epic}
    assert adapter.scheduled_reconnects == 0
    assert "heartbeat_events=2" in (health.detail or "")

    clock.current += timedelta(seconds=115)
    adapter._handle_heartbeat("1783065721000", clock.now(), generation=3)
    await adapter.health()

    assert adapter._heartbeat_stale is False
    assert adapter.scheduled_reconnects == 1


@pytest.mark.asyncio
async def test_heartbeat_callback_crosses_event_loop_with_bounded_evidence() -> None:
    clock = MutableClock()
    adapter = IgDemoMarketDataAdapter(config(), clock)
    adapter._loop = asyncio.get_running_loop()
    adapter._generation = 7
    adapter._handle_heartbeat_subscription(generation=7)

    await asyncio.to_thread(
        adapter._on_heartbeat,
        FakeUpdate({"HEARTBEAT": "1783065600000"}),
        7,
    )
    await asyncio.sleep(0)

    assert adapter._heartbeat_subscribed is True
    assert adapter._heartbeat_events == 1
    assert adapter._last_heartbeat_at == clock.now()
    assert adapter._last_heartbeat_value == "1783065600000"


def test_heartbeat_subscription_error_fails_readiness_closed() -> None:
    adapter = IgDemoMarketDataAdapter(config(), MutableClock())
    adapter._generation = 2

    adapter._handle_heartbeat_subscription_error(code=41, generation=2)

    assert adapter._heartbeat_subscribed is False
    assert adapter._heartbeat_stale is True
    assert adapter._status is HealthStatus.DEGRADED
    assert adapter._connection_state is _ConnectionState.DEGRADED
    assert adapter._readiness_error is not None
    assert adapter._ready_event.is_set()


def test_superseded_generation_callbacks_cannot_change_current_readiness() -> None:
    clock = MutableClock()
    adapter = IgDemoMarketDataAdapter(config(), clock)
    epic = listing().listing_id.external_id
    adapter._generation = 8
    adapter._expected_epics = {epic}
    adapter._connection_state = _ConnectionState.SUBSCRIBING
    adapter._status = HealthStatus.STARTING

    adapter._handle_stream_status("CONNECTED:WS-STREAMING", generation=7)
    adapter._handle_subscription(epic, generation=7)
    adapter._accept_update(market_record(clock), epic, generation=7)

    assert adapter._transport_connected is False
    assert adapter._subscribed_epics == set()
    assert adapter._updated_epics == set()
    assert adapter._queue.empty()
    assert adapter._status is HealthStatus.STARTING


@pytest.mark.asyncio
async def test_will_retry_watchdog_escalates_stalled_sdk_recovery() -> None:
    adapter = StaleAdapter(
        config(retry_watchdog_seconds=0.001),
        MutableClock(),
    )
    adapter._generation = 2
    adapter._desired_listings = (listing(),)
    adapter._connection_state = _ConnectionState.READY
    adapter._status = HealthStatus.HEALTHY

    adapter._handle_stream_status("DISCONNECTED:WILL-RETRY", generation=2)
    await asyncio.sleep(0.01)

    assert adapter.scheduled_reconnects == 1
    assert adapter._status is HealthStatus.DEGRADED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    ["STALLED", "DISCONNECTED:WILL-RETRY", "DISCONNECTED:TRYING-RECOVERY"],
)
async def test_library_managed_recovery_requires_fresh_healthy_update(status: str) -> None:
    clock = MutableClock()
    adapter = IgDemoMarketDataAdapter(config(), clock)
    selected_listing = listing()
    epic = selected_listing.listing_id.external_id
    adapter._generation = 4
    adapter._expected_epics = {epic}
    adapter._subscribed_epics = {epic}
    adapter._updated_epics = {epic}
    adapter._transport_connected = True
    adapter._connection_state = _ConnectionState.READY
    adapter._status = HealthStatus.HEALTHY
    mark_heartbeat_current(adapter, clock)

    adapter._handle_stream_status(status, generation=4)
    adapter._handle_stream_status("CONNECTED:WS-STREAMING", generation=4)

    assert adapter._status is HealthStatus.DEGRADED
    assert adapter._updated_epics == set()

    adapter._accept_update(market_record(clock), epic, generation=4)

    assert adapter._status is HealthStatus.HEALTHY
    assert adapter._connection_state is _ConnectionState.READY
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_library_managed_recovery_preserves_staleness_grace_window() -> None:
    clock = MutableClock()
    adapter = StaleAdapter(config(), clock)
    selected_listing = listing()
    epic = selected_listing.listing_id.external_id
    adapter._generation = 4
    adapter._desired_listings = (selected_listing,)
    adapter._listings_by_epic[epic] = selected_listing
    adapter._expected_epics = {epic}
    adapter._subscribed_epics = {epic}
    adapter._updated_epics = {epic}
    adapter._quote_received_times[epic] = clock.now()
    adapter._transport_connected = True
    adapter._connection_state = _ConnectionState.READY
    adapter._status = HealthStatus.HEALTHY
    mark_heartbeat_current(adapter, clock)

    adapter._handle_stream_status("DISCONNECTED:TRYING-RECOVERY", generation=4)
    clock.current += timedelta(seconds=6)
    await adapter.health()

    assert adapter._status is HealthStatus.DEGRADED
    assert adapter._updated_epics == set()
    assert adapter._quote_received_times[epic] == datetime(2026, 7, 3, tzinfo=UTC)
    assert adapter.scheduled_reconnects == 0

    clock.current += timedelta(seconds=115)
    await adapter.health()

    assert adapter.scheduled_reconnects == 1


@pytest.mark.asyncio
async def test_records_propagates_terminal_adapter_failure() -> None:
    adapter = IgDemoMarketDataAdapter(config(), MutableClock())
    adapter._service = FakeService()
    adapter._connection_state = _ConnectionState.FAILED
    adapter._fatal_error = ConnectionError("stream recovery failed")

    with pytest.raises(RuntimeError, match="not connected"):
        await anext(adapter.records())

    adapter._connection_state = _ConnectionState.READY
    with pytest.raises(ConnectionError, match="stream recovery failed"):
        await anext(adapter.records())


@pytest.mark.asyncio
async def test_close_stream_waits_for_confirmed_disconnect() -> None:
    adapter = IgDemoMarketDataAdapter(config(), MutableClock())
    client = FakeStreamClient()
    adapter._stream_client = client
    original_generation = adapter._generation

    await adapter._close_stream()

    assert client.disconnected is True
    assert adapter._stream_client is None
    assert adapter._generation == original_generation + 1


@pytest.mark.asyncio
async def test_close_stream_retains_client_when_disconnect_is_not_confirmed() -> None:
    adapter = IgDemoMarketDataAdapter(
        config(shutdown_timeout_seconds=0.01),
        MutableClock(),
    )
    client = UnconfirmedDisconnectClient()
    adapter._stream_client = client

    with pytest.raises(TimeoutError, match="did not confirm disconnect"):
        await adapter.disconnect()

    assert adapter._stream_client is client
    assert adapter._connection_state is _ConnectionState.FAILED


@pytest.mark.asyncio
async def test_logout_failure_still_stops_rate_limiter_and_closes_session() -> None:
    service = FailingLogoutService()
    adapter = IgDemoMarketDataAdapter(
        config(),
        MutableClock(),
        service_factory=lambda _: service,
    )
    await adapter.connect()

    await adapter.disconnect()

    assert service.rate_limiter_stopped is True
    assert service.session.closed is True
    assert (await adapter.health()).status is HealthStatus.STOPPED


@pytest.mark.asyncio
async def test_invalid_v2_token_reauthenticates_and_replays_idempotent_read_once() -> None:
    stale_service = TokenInvalidReadService(fail_reads=True)
    fresh_service = TokenInvalidReadService(fail_reads=False)
    services = iter((stale_service, fresh_service))
    adapter = IgDemoMarketDataAdapter(
        config(),
        MutableClock(),
        service_factory=lambda _: next(services),
    )
    await adapter.connect()

    result = await adapter._run_rest_read(
        "probe",
        lambda service: service.search_markets("probe"),
    )

    assert result == {"status": "fresh-session"}
    assert stale_service.search_calls == 1
    assert stale_service.logged_out is True
    assert stale_service.session.closed is True
    assert fresh_service.search_calls == 1
    assert adapter._rest_reauthentications == 1
    await adapter.disconnect()


@pytest.mark.asyncio
async def test_invalid_v2_token_is_replayed_at_most_once() -> None:
    first_service = TokenInvalidReadService(fail_reads=True)
    second_service = TokenInvalidReadService(fail_reads=True)
    services = iter((first_service, second_service))
    adapter = IgDemoMarketDataAdapter(
        config(),
        MutableClock(),
        service_factory=lambda _: next(services),
    )
    await adapter.connect()

    with pytest.raises(TokenInvalidException, match="client-token-invalid"):
        await adapter._run_rest_read(
            "probe",
            lambda service: service.search_markets("probe"),
        )

    assert first_service.search_calls == 1
    assert second_service.search_calls == 1
    assert adapter._rest_reauthentications == 1
    await adapter.disconnect()


@pytest.mark.asyncio
async def test_rest_allowance_error_is_retained_without_automatic_retry() -> None:
    service = AllowanceExceededService()
    adapter = IgDemoMarketDataAdapter(
        config(),
        MutableClock(),
        service_factory=lambda _: service,
    )
    await adapter.connect()

    with pytest.raises(RuntimeError, match="exceeded-account-allowance"):
        await adapter._run_rest_read(
            "probe",
            lambda current: current.search_markets("probe"),
        )

    health_detail = (await adapter.health()).detail or ""
    assert "allowance_errors=1" in health_detail
    assert "last_allowance_error=error.public-api.exceeded-account-allowance" in health_detail
    await adapter.disconnect()


@pytest.mark.asyncio
async def test_published_and_effective_rate_limits_are_retained_without_api_key() -> None:
    service = RateLimitedService()
    adapter = IgDemoMarketDataAdapter(
        config(),
        MutableClock(),
        service_factory=lambda _: service,
    )

    await adapter.connect()

    health_detail = (await adapter.health()).detail or ""
    assert "published_rest_rates=9/25" in health_detail
    assert "effective_rest_rates=7/23" in health_detail
    assert "not-a-real" not in health_detail
    await adapter.disconnect()


def test_client_app_allowances_require_exactly_one_current_api_key() -> None:
    response = [
        {
            "apiKey": "different-key",
            "allowanceAccountTrading": 100,
            "allowanceAccountOverall": 100,
        },
        {
            "apiKey": "configured-key",
            "allowanceAccountTrading": 9,
            "allowanceAccountOverall": 25,
        },
    ]

    assert _validated_client_app_allowances(response, "configured-key") == (9, 25)


@pytest.mark.parametrize(
    "response",
    [
        [{"apiKey": "different-key", "allowanceAccountTrading": 9, "allowanceAccountOverall": 25}],
        [
            {
                "apiKey": "configured-key",
                "allowanceAccountTrading": 9,
                "allowanceAccountOverall": 25,
            },
            {
                "apiKey": "configured-key",
                "allowanceAccountTrading": 9,
                "allowanceAccountOverall": 25,
            },
        ],
        [{"allowanceAccountTrading": 9, "allowanceAccountOverall": 25}],
        [{"apiKey": "configured-key", "allowanceAccountOverall": 25}],
        [
            {
                "apiKey": "configured-key",
                "allowanceAccountTrading": True,
                "allowanceAccountOverall": 25,
            }
        ],
        [{"apiKey": "configured-key", "allowanceAccountTrading": 2, "allowanceAccountOverall": 25}],
    ],
)
def test_client_app_allowances_fail_closed(response: object) -> None:
    with pytest.raises(RuntimeError):
        _validated_client_app_allowances(response, "configured-key")


def test_subscription_error_degrades_health_without_exposing_message() -> None:
    adapter = IgDemoMarketDataAdapter(config(), MutableClock())
    adapter._status = HealthStatus.HEALTHY

    adapter._handle_subscription_error("CS.D.AUDUSD.CFD.IP", 19)

    assert adapter._status is HealthStatus.DEGRADED


def test_subscription_renewal_invalidates_prior_item_state_and_requires_fresh_update() -> None:
    clock = MutableClock()
    adapter = IgDemoMarketDataAdapter(config(), clock)
    epic = listing().listing_id.external_id
    adapter._generation = 4
    adapter._expected_epics = {epic}
    adapter._subscribed_epics = {epic}
    adapter._updated_epics = {epic}
    adapter._quote_received_times[epic] = clock.now()
    adapter._field_state[epic] = {"BIDPRICE1": "0.65"}
    adapter._side_times[epic] = {"BID": clock.now()}
    adapter._transport_connected = True
    adapter._connection_state = _ConnectionState.READY
    adapter._status = HealthStatus.HEALTHY

    adapter._handle_unsubscription(epic, generation=4)
    adapter._handle_subscription(epic, generation=4)

    assert adapter._subscribed_epics == {epic}
    assert adapter._updated_epics == set()
    assert adapter._quote_received_times == {}
    assert adapter._field_state == {}
    assert adapter._side_times == {}
    assert adapter._subscription_events == 1
    assert adapter._unsubscription_events == 1
    assert adapter._status is HealthStatus.DEGRADED


@pytest.mark.asyncio
async def test_lightstreamer_lost_updates_are_sticky_health_evidence() -> None:
    clock = MutableClock()
    adapter = IgDemoMarketDataAdapter(config(), clock)
    epic = listing().listing_id.external_id
    adapter._generation = 3
    adapter._expected_epics = {epic}
    adapter._subscribed_epics = {epic}
    adapter._updated_epics = {epic}
    adapter._quote_received_times[epic] = clock.now()
    adapter._transport_connected = True
    adapter._connection_state = _ConnectionState.READY
    adapter._status = HealthStatus.HEALTHY

    adapter._handle_item_lost_updates(epic, count=2, generation=3)
    adapter._mark_ready_if_complete(generation=3)

    health = await adapter.health()
    assert health.status is HealthStatus.DEGRADED
    assert "lightstreamer_lost_updates=2" in (health.detail or "")


def test_server_error_is_bounded_and_schedules_application_recovery() -> None:
    adapter = StaleAdapter(config(), MutableClock())
    adapter._generation = 5
    adapter._desired_listings = (listing(),)
    adapter._connection_state = _ConnectionState.READY
    adapter._status = HealthStatus.HEALTHY

    adapter._handle_server_error(code=68, generation=5)

    assert adapter._status is HealthStatus.DEGRADED
    assert adapter._connection_state is _ConnectionState.DEGRADED
    assert adapter._server_errors == 1
    assert adapter._last_server_error_code == 68
    assert adapter.scheduled_reconnects == 1


def test_real_max_frequency_is_retained_as_bounded_subscription_evidence() -> None:
    adapter = IgDemoMarketDataAdapter(config(), MutableClock())
    epic = listing().listing_id.external_id
    adapter._generation = 6

    adapter._handle_real_max_frequency(epic, "2.0", generation=6)

    assert adapter._real_max_frequency_by_epic == {epic: "2.0"}


@pytest.mark.asyncio
async def test_queue_saturation_is_sticky_and_rate_limits_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecordingLogger:
        def __init__(self) -> None:
            self.errors: list[dict[str, object]] = []

        def error(self, event: str, *, extra: dict[str, object]) -> None:
            assert event == "ig_queue_saturated"
            self.errors.append(extra)

    logger = RecordingLogger()
    monkeypatch.setattr(ig_market_data, "LOGGER", logger)
    clock = MutableClock()
    adapter = IgDemoMarketDataAdapter(config(queue_capacity=1), clock)
    adapter._service = FakeService()
    adapter._status = HealthStatus.HEALTHY
    adapter._connection_state = _ConnectionState.READY
    record = MarketDataRecord(
        provider="ig",
        environment="demo",
        subscription="PRICE:CS.D.AUDUSD.CFD.IP",
        deduplication_key="one",
        received_time=clock.now(),
        raw_payload={},
        payload_representation=RawPayloadRepresentation.CHANGED_FIELDS,
        quote=None,
    )

    adapter._enqueue_record(record, "CS.D.AUDUSD.CFD.IP")
    for _ in range(2_001):
        adapter._enqueue_record(record, "CS.D.AUDUSD.CFD.IP")
    assert (await adapter.health()).status is HealthStatus.DEGRADED
    health_detail = (await adapter.health()).detail or ""
    assert "dropped_records=2001" in health_detail
    assert f"first_drop_at={record.received_time.isoformat()}" in health_detail
    assert f"last_drop_at={record.received_time.isoformat()}" in health_detail
    assert "queue=1/1" in health_detail
    assert "queue_high_water=1" in health_detail
    assert [item["dropped_records"] for item in logger.errors] == [1, 1_000, 2_000]

    received = await anext(adapter.records())
    assert received is record
    assert (await adapter.health()).status is HealthStatus.DEGRADED


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
    assert service.historical_request == (
        listing().listing_id.external_id,
        "MINUTE",
        clock.now().strftime("%Y-%m-%d %H:%M:%S"),
        (clock.now() + timedelta(minutes=5) - timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S"),
    )
    await adapter.disconnect()


@pytest.mark.asyncio
async def test_backfill_paces_historical_requests_before_provider_access() -> None:
    clock = MutableClock()
    service = FakeService(historical_response={"prices": [], "allowance": {}})
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    adapter = IgDemoMarketDataAdapter(
        config(historical_request_interval_seconds=3),
        clock,
        sleep=sleep,
        service_factory=lambda _: service,
    )
    await adapter.connect()
    request = BackfillRequest(
        instrument_id=InstrumentId("fx:aud-usd"),
        listing=listing(),
        start=clock.now(),
        end=clock.now() + timedelta(minutes=1),
        maximum_points=1,
    )

    assert [bar async for bar in adapter.backfill(request)] == []
    assert sleeps == [3]
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


@pytest.mark.asyncio
async def test_price_callback_persists_only_changed_fields_and_preserves_explicit_null() -> None:
    adapter = IgDemoMarketDataAdapter(config(), MutableClock())
    adapter._loop = asyncio.get_running_loop()
    adapter._stream_account_id = "not-a-real-account"
    adapter._listings_by_epic["CS.D.AUDUSD.CFD.IP"] = listing()
    epic = "CS.D.AUDUSD.CFD.IP"

    initial = {
        "TIMESTAMP": "1783065600000",
        "BIDPRICE1": "0.65000",
        "ASKPRICE1": "0.65010",
        "BIDSIZE1": "12",
        "ASKSIZE1": "10",
        "DLG_FLAG": "DEAL",
        "DELAY": "0",
    }
    adapter._on_update(epic, FakeUpdate(initial))
    await asyncio.sleep(0)
    first = adapter._queue.get_nowait()
    assert first.raw_payload == initial
    assert first.payload_representation is RawPayloadRepresentation.CHANGED_FIELDS

    merged = {
        **initial,
        "TIMESTAMP": "1783065601000",
        "BIDPRICE1": "0.65001",
    }
    adapter._on_update(
        epic,
        FakeUpdate(merged, changed_fields={"TIMESTAMP", "BIDPRICE1"}),
    )
    await asyncio.sleep(0)
    second = adapter._queue.get_nowait()
    assert second.raw_payload == {
        "TIMESTAMP": "1783065601000",
        "BIDPRICE1": "0.65001",
    }
    assert second.quote is not None
    assert second.quote.bid == Decimal("0.65001")
    assert second.quote.ask == Decimal("0.65010")

    cleared = {**merged, "TIMESTAMP": "1783065602000", "ASKPRICE1": None}
    adapter._on_update(
        epic,
        FakeUpdate(cleared, changed_fields={"TIMESTAMP", "ASKPRICE1"}),
    )
    await asyncio.sleep(0)
    third = adapter._queue.get_nowait()
    assert third.raw_payload == {
        "TIMESTAMP": "1783065602000",
        "ASKPRICE1": None,
    }
    assert third.quote is not None
    assert third.quote.bid == Decimal("0.65001")
    assert third.quote.ask is None
    assert third.quote.ask_time is None


@pytest.mark.asyncio
async def test_price_callbacks_and_subscription_renewal_share_event_loop_ordering() -> None:
    adapter = IgDemoMarketDataAdapter(config(), MutableClock())
    adapter._loop = asyncio.get_running_loop()
    adapter._stream_account_id = "not-a-real-account"
    epic = "CS.D.AUDUSD.CFD.IP"
    adapter._listings_by_epic[epic] = listing()

    def dispatch_from_lightstreamer_thread() -> None:
        adapter._on_update(
            epic,
            FakeUpdate(
                {
                    "TIMESTAMP": "1783065600000",
                    "BIDPRICE1": "0.65000",
                    "ASKPRICE1": "0.65010",
                    "DLG_FLAG": "DEAL",
                }
            ),
        )
        adapter._on_subscription(epic, generation=0)
        adapter._on_update(
            epic,
            FakeUpdate(
                {
                    "TIMESTAMP": "1783065601000",
                    "BIDPRICE1": "0.65001",
                },
                changed_fields={"TIMESTAMP", "BIDPRICE1"},
            ),
        )

    await asyncio.to_thread(dispatch_from_lightstreamer_thread)
    await asyncio.sleep(0)

    first = adapter._queue.get_nowait().quote
    after_renewal = adapter._queue.get_nowait().quote
    assert first is not None
    assert first.ask == Decimal("0.65010")
    assert after_renewal is not None
    assert after_renewal.bid == Decimal("0.65001")
    assert after_renewal.ask is None


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
