"""Data-only IG demo adapter.

The `trading-ig` package is intentionally contained in this module. No order
methods are exposed.
"""

import asyncio
import hashlib
import json
import logging
import random
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from threading import Thread
from typing import Protocol, TypeVar, cast, runtime_checkable

from qtrad.adapters.ig.lightstreamer_compat import install_lightstreamer_compatibility
from qtrad.domain.audit import RawPayloadRepresentation
from qtrad.domain.events import JsonValue, to_json_value
from qtrad.domain.identifiers import InstrumentId, ProviderListingId
from qtrad.domain.instruments import (
    INSTRUMENTS_BY_ID,
    AssetClass,
    Instrument,
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
from qtrad.domain.modes import BrokerEnvironment
from qtrad.domain.operations import AdapterHealth, HealthStatus
from qtrad.ports.clock import Clock
from qtrad.ports.market_data import (
    BackfillRequest,
    InstrumentListingReview,
    ListingExpiryKind,
    ListingMarketState,
    ListingReviewCandidate,
    ListingReviewRejection,
    MarketDataRecord,
)

LOGGER = logging.getLogger(__name__)
_T = TypeVar("_T")
_ROLLING_EXPIRIES = {"-", "DFB", "DAILY", "CASH", "ROLLING"}
_UNAVAILABLE_MARKET_STATES = {"CLOSED", "OFFLINE", "EDITS_ONLY"}
_MAX_LISTING_REVIEW_CANDIDATES = 100
_MAX_LISTING_REVIEW_SEARCH_REQUESTS = 100
_MAX_LISTING_REVIEW_DETAIL_REQUESTS = 200
_HEARTBEAT_ITEM = "TRADE:HB.U.HEARTBEAT.IP"
_TRADING_IG_RATE_LIMIT_SAFETY_MARGIN = 2
_PRICE_FIELDS = (
    "TIMESTAMP",
    "BIDPRICE1",
    "ASKPRICE1",
    "BIDSIZE1",
    "ASKSIZE1",
    "DLG_FLAG",
    "DELAY",
)
_PRICE_FIELDS_BY_POSITION = tuple(enumerate(_PRICE_FIELDS, start=1))
_HEARTBEAT_FIELD_POSITION = 1
_PROVIDER_ERROR_CODE = re.compile(r"\b(error\.[a-z0-9._-]+|endpoint\.[a-z0-9._-]+)\b")
_FATAL_PROVIDER_ERRORS = {
    "endpoint.unavailable.for.api-key",
    "error.security.api-key-disabled",
    "error.security.api-key-invalid",
    "error.security.api-key-restricted",
    "error.security.api-key-revoked",
    "error.security.too-many-failed-attempts",
    "OPERATION_TIMEOUT",
}
_PREFERRED_EPICS = {
    InstrumentId("fx:aud-usd"): "CS.D.AUDUSD.CFD.IP",
    InstrumentId("fx:eur-usd"): "CS.D.EURUSD.CFD.IP",
    InstrumentId("fx:usd-jpy"): "CS.D.USDJPY.CFD.IP",
    InstrumentId("fx:gbp-usd"): "CS.D.GBPUSD.CFD.IP",
    InstrumentId("index:australia-200"): "IX.D.ASX.IFD.IP",
    InstrumentId("index:us-500"): "IX.D.SPTRD.IFD.IP",
    InstrumentId("index:ftse-100"): "IX.D.FTSE.CFD.IP",
}


class _HttpSession(Protocol):
    @property
    def headers(self) -> Mapping[str, str]: ...

    def close(self) -> None: ...


class _ConfigurableHttpSession(_HttpSession, Protocol):
    request: Callable[..., object]


@runtime_checkable
class _RateLimiterControl(Protocol):
    def _exit_bucket_threads(self) -> None: ...


@runtime_checkable
class _RateLimiterEvidence(Protocol):
    _trading_requests_per_minute: int
    _non_trading_requests_per_minute: int


@runtime_checkable
class _PublishedRateLimiterEvidence(Protocol):
    _qtrad_published_trading_requests_per_minute: int
    _qtrad_published_non_trading_requests_per_minute: int


class _IgRestService(Protocol):
    @property
    def session(self) -> _HttpSession: ...

    def create_session(self) -> object: ...

    def search_markets(self, search_term: str) -> object: ...

    def fetch_market_by_epic(self, epic: str) -> object: ...

    def fetch_historical_prices_by_epic_and_date_range(
        self,
        epic: str,
        resolution: str,
        start_date: str,
        end_date: str,
        /,
    ) -> object: ...

    def logout(self) -> object: ...


class _ItemUpdate(Protocol):
    def getValue(self, field: str | int, /) -> object | None: ...

    def isValueChanged(self, field: str | int, /) -> bool: ...


@runtime_checkable
class _ToDict(Protocol):
    def to_dict(self, *args: object, **kwargs: object) -> object: ...


class _ConnectionDetails(Protocol):
    def setUser(self, user: str) -> None: ...

    def setPassword(self, password: str) -> None: ...


class _Subscription(Protocol):
    def setDataAdapter(self, data_adapter: str) -> None: ...

    def addListener(self, listener: object) -> None: ...


class _StreamClient(Protocol):
    @property
    def connectionDetails(self) -> _ConnectionDetails: ...

    def addListener(self, listener: object) -> None: ...

    def connect(self) -> None: ...

    def subscribe(self, subscription: _Subscription) -> None: ...

    def unsubscribe(self, subscription: _Subscription) -> None: ...

    def disconnect(self) -> None: ...

    def getStatus(self) -> str: ...


@dataclass(frozen=True, slots=True)
class IgDemoConfig:
    username: str
    password: str
    api_key: str
    account_id: str | None = None
    queue_capacity: int = 10_000
    connect_attempts: int = 3
    reconnect_attempts: int = 6
    initial_backoff_seconds: float = 5.0
    maximum_backoff_seconds: float = 120.0
    reconnect_cooldown_seconds: float = 300.0
    maximum_reconnect_cycles: int = 3
    stale_after_seconds: float = 300.0
    stale_reconnect_after_seconds: float = 1800.0
    readiness_timeout_seconds: float = 60.0
    retry_watchdog_seconds: float = 60.0
    shutdown_timeout_seconds: float = 10.0
    provider_operation_timeout_seconds: float = 30.0
    http_connect_timeout_seconds: float = 5.0
    http_read_timeout_seconds: float = 15.0
    historical_request_interval_seconds: float = 3.0

    @property
    def account_type(self) -> str:
        return "DEMO"

    def __post_init__(self) -> None:
        if self.queue_capacity <= 0:
            raise ValueError("queue capacity must be positive")
        if self.connect_attempts <= 0 or self.reconnect_attempts <= 0:
            raise ValueError("connection attempts must be positive")
        if self.initial_backoff_seconds <= 0 or self.maximum_backoff_seconds <= 0:
            raise ValueError("backoff intervals must be positive")
        if self.reconnect_cooldown_seconds <= 0:
            raise ValueError("reconnect cooldown must be positive")
        if self.maximum_reconnect_cycles <= 0:
            raise ValueError("maximum reconnect cycles must be positive")
        if (
            self.stale_after_seconds <= 0
            or self.stale_reconnect_after_seconds <= self.stale_after_seconds
            or self.readiness_timeout_seconds <= 0
            or self.retry_watchdog_seconds <= 0
            or self.shutdown_timeout_seconds <= 0
            or self.provider_operation_timeout_seconds <= 0
            or self.http_connect_timeout_seconds <= 0
            or self.http_read_timeout_seconds <= 0
            or self.historical_request_interval_seconds < 0
        ):
            raise ValueError(
                "lifecycle timeouts must be positive and stale reconnect must exceed staleness"
            )


class _ConnectionState(StrEnum):
    STOPPED = "STOPPED"
    AUTHENTICATING = "AUTHENTICATING"
    AUTHENTICATED = "AUTHENTICATED"
    CONNECTING = "CONNECTING"
    SUBSCRIBING = "SUBSCRIBING"
    READY = "READY"
    DEGRADED = "DEGRADED"
    BACKING_OFF = "BACKING_OFF"
    FAILED = "FAILED"
    STOPPING = "STOPPING"


class _ProviderOperationTimeout(TimeoutError):
    def __init__(self, operation: str) -> None:
        self.operation = operation
        super().__init__(f"IG provider operation timed out: {operation}")


@dataclass(frozen=True, slots=True)
class _Candidate:
    epic: str
    name: str
    instrument_type: str
    expiry: str
    market_status: str
    currency: str
    minimum_deal_size: Decimal
    metadata: Mapping[str, JsonValue]


class IgDemoMarketDataAdapter:
    provider = "ig"
    environment = "demo"
    version = "0.1.0"

    def __init__(
        self,
        config: IgDemoConfig,
        clock: Clock,
        *,
        instruments_by_id: Mapping[InstrumentId, Instrument] = INSTRUMENTS_BY_ID,
        preferred_epics: Mapping[InstrumentId, str] = _PREFERRED_EPICS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[float, float], float] = random.uniform,
        service_factory: Callable[[IgDemoConfig], object] | None = None,
    ) -> None:
        self._config = config
        self._clock = clock
        self._instruments_by_id = instruments_by_id
        self._preferred_epics = preferred_epics
        self._sleep = sleep
        self._jitter = jitter
        self._service_factory = service_factory
        self._service: _IgRestService | None = None
        self._rest_reauthentication_lock = asyncio.Lock()
        self._session_details: Mapping[str, object] | None = None
        self._stream_client: _StreamClient | None = None
        self._stream_account_id: str | None = None
        self._subscriptions: list[_Subscription] = []
        self._queue: asyncio.Queue[MarketDataRecord] = asyncio.Queue(maxsize=config.queue_capacity)
        self._listings_by_epic: dict[str, ProviderListing] = {}
        self._field_state: dict[str, dict[str, str]] = {}
        self._side_times: dict[str, dict[str, datetime]] = {}
        self._last_message_at: datetime | None = None
        self._stream_connected_at: datetime | None = None
        self._status = HealthStatus.STOPPED
        self._connection_state = _ConnectionState.STOPPED
        self._loop: asyncio.AbstractEventLoop | None = None
        self._desired_listings: tuple[ProviderListing, ...] = ()
        self._reconnecting = False
        self._reconnect_task: asyncio.Task[None] | None = None
        self._retry_watchdog_task: asyncio.Task[None] | None = None
        self._stopping = False
        self._reconnect_count = 0
        self._dropped_records = 0
        self._lightstreamer_lost_updates = 0
        self._subscription_events = 0
        self._unsubscription_events = 0
        self._subscription_errors = 0
        self._server_errors = 0
        self._last_server_error_code: int | None = None
        self._last_stream_status = "UNOBSERVED"
        self._last_stream_status_at: datetime | None = None
        self._real_max_frequency_by_epic: dict[str, str] = {}
        self._heartbeat_subscribed = False
        self._heartbeat_events = 0
        self._last_heartbeat_at: datetime | None = None
        self._last_heartbeat_value: str | None = None
        self._heartbeat_real_max_frequency: str | None = None
        self._heartbeat_stale = False
        self._heartbeat_current_for_transport = False
        self._first_drop_at: datetime | None = None
        self._last_drop_at: datetime | None = None
        self._queue_high_water = 0
        self._historical_allowance_remaining: int | None = None
        self._published_trading_requests_per_minute: int | None = None
        self._published_non_trading_requests_per_minute: int | None = None
        self._effective_trading_requests_per_minute: int | None = None
        self._effective_non_trading_requests_per_minute: int | None = None
        self._rest_reauthentications = 0
        self._allowance_errors = 0
        self._last_allowance_error: str | None = None
        self._generation = 0
        self._expected_epics: set[str] = set()
        self._subscribed_epics: set[str] = set()
        self._updated_epics: set[str] = set()
        self._quote_received_times: dict[str, datetime] = {}
        self._stale_epics: set[str] = set()
        self._transport_connected = False
        self._ready_event = asyncio.Event()
        self._readiness_error: Exception | None = None
        self._fatal_error: Exception | None = None
        self._provider_threads: dict[Thread, str] = {}
        self._abandoned_provider_operation = False

    async def connect(self) -> None:
        if self._status in {
            HealthStatus.STARTING,
            HealthStatus.HEALTHY,
            HealthStatus.DEGRADED,
        }:
            return
        self._stopping = False
        self._fatal_error = None
        self._status = HealthStatus.STARTING
        self._connection_state = _ConnectionState.AUTHENTICATING
        self._loop = asyncio.get_running_loop()
        await self._establish_rest_session()

    async def disconnect(self) -> None:
        self._stopping = True
        self._connection_state = _ConnectionState.STOPPING
        close_error: Exception | None = None
        watchdog_task = self._retry_watchdog_task
        if watchdog_task is not None and watchdog_task is not asyncio.current_task():
            watchdog_task.cancel()
            with suppress(asyncio.CancelledError):
                await watchdog_task
        reconnect_task = self._reconnect_task
        if reconnect_task is not None and reconnect_task is not asyncio.current_task():
            reconnect_task.cancel()
            with suppress(asyncio.CancelledError):
                await reconnect_task
        try:
            await self._wait_for_provider_operations()
        except Exception as error:
            close_error = error
        try:
            if close_error is None:
                await self._close_stream()
        except Exception as error:
            close_error = error
        try:
            if self._active_provider_operation_names():
                service = self._service
                if service is not None:
                    self._stop_rate_limiter(service)
            else:
                await self._logout_rest_session()
        except Exception as error:
            close_error = close_error or error
        finally:
            self._desired_listings = ()
            self._reconnecting = False
            self._reconnect_task = None
            self._retry_watchdog_task = None
            self._fatal_error = None
            self._status = (
                HealthStatus.STOPPED if close_error is None else HealthStatus.DISCONNECTED
            )
            self._connection_state = (
                _ConnectionState.STOPPED if close_error is None else _ConnectionState.FAILED
            )
        if close_error is not None:
            raise close_error

    async def discover_listings(
        self, instrument_ids: Sequence[InstrumentId]
    ) -> Sequence[ProviderListing]:
        listings: list[ProviderListing] = []
        for instrument_id in instrument_ids:
            try:
                instrument = self._instruments_by_id[instrument_id]
                preferred_epic = self._preferred_epics[instrument_id]
            except KeyError as error:
                raise RuntimeError(
                    f"capture universe has no explicit IG listing preference for {instrument_id}"
                ) from error
            by_epic: dict[str, _Candidate] = {}
            for alias in instrument.search_aliases:
                response = await self._run_rest_read(
                    "search_markets",
                    lambda service, alias=alias: service.search_markets(alias),
                )
                for search_row in _records(response):
                    epic = _string(search_row, "epic")
                    if (
                        not epic
                        or epic in by_epic
                        or not _search_row_can_match(search_row, instrument)
                    ):
                        continue
                    detail_response = await self._run_rest_read(
                        "fetch_market",
                        lambda service, epic=epic: service.fetch_market_by_epic(epic),
                    )
                    detail = _single_record(detail_response)
                    candidate = _candidate(search_row, detail)
                    if candidate is not None:
                        by_epic[epic] = candidate

            candidate = _select_candidate(
                tuple(by_epic.values()),
                instrument,
                preferred_epic=preferred_epic,
            )
            economics = _bounded_economics(candidate.metadata)
            metadata_version = _listing_metadata_version(candidate, economics)
            listing = ProviderListing(
                listing_id=ProviderListingId("ig", "demo", candidate.epic),
                instrument_id=instrument_id,
                display_name=candidate.name,
                product_type=ProductType.ROLLING_CFD,
                currency=candidate.currency or instrument.quote_currency,
                minimum_deal_size=candidate.minimum_deal_size,
                price_increment=None,
                valid_from=self._clock.now(),
                valid_to=None,
                metadata_version=metadata_version,
                economics=economics,
            )
            listings.append(listing)
        return tuple(listings)

    async def review_listings(
        self, instrument_ids: Sequence[InstrumentId]
    ) -> Sequence[InstrumentListingReview]:
        """Return bounded listing evidence without selecting or persisting a candidate."""

        if not instrument_ids or len(instrument_ids) > 100:
            raise ValueError("listing review requires between one and 100 instruments")
        if len(set(instrument_ids)) != len(instrument_ids):
            raise ValueError("listing review instrument IDs must be unique")
        reviews: list[InstrumentListingReview] = []
        search_request_count = 0
        detail_request_count = 0
        for instrument_id in instrument_ids:
            try:
                instrument = self._instruments_by_id[instrument_id]
            except KeyError as error:
                raise RuntimeError(
                    f"listing review catalogue has no instrument definition for {instrument_id}"
                ) from error
            by_epic: dict[str, ListingReviewCandidate] = {}
            for alias in instrument.search_aliases:
                if search_request_count >= _MAX_LISTING_REVIEW_SEARCH_REQUESTS:
                    raise RuntimeError(
                        "IG listing review exceeded the global search-request budget of "
                        f"{_MAX_LISTING_REVIEW_SEARCH_REQUESTS}"
                    )
                search_request_count += 1
                response = await self._run_rest_read(
                    "search_markets",
                    lambda service, alias=alias: service.search_markets(alias),
                )
                for search_row in _records(response):
                    epic = _string(search_row, "epic")
                    if not epic:
                        raise RuntimeError(
                            f"IG listing search returned a row without an epic for {instrument_id}"
                        )
                    if epic in by_epic or not _search_row_is_review_relevant(
                        search_row, instrument
                    ):
                        continue
                    if len(by_epic) >= _MAX_LISTING_REVIEW_CANDIDATES:
                        raise RuntimeError(
                            f"IG listing review exceeded {_MAX_LISTING_REVIEW_CANDIDATES} "
                            f"candidates for {instrument_id}"
                        )
                    detail: Mapping[str, object] | None = None
                    if _search_row_needs_detail(search_row, instrument):
                        if detail_request_count >= _MAX_LISTING_REVIEW_DETAIL_REQUESTS:
                            raise RuntimeError(
                                "IG listing review exceeded the global detail-request budget of "
                                f"{_MAX_LISTING_REVIEW_DETAIL_REQUESTS}"
                            )
                        detail_request_count += 1
                        detail_response = await self._run_rest_read(
                            "fetch_market",
                            lambda service, epic=epic: service.fetch_market_by_epic(epic),
                        )
                        detail = _single_record(detail_response)
                    by_epic[epic] = _listing_review_candidate(
                        search_row,
                        detail,
                        instrument,
                    )
            reviews.append(
                InstrumentListingReview(
                    instrument_id=instrument_id,
                    candidates=tuple(
                        sorted(by_epic.values(), key=lambda item: item.listing_id.external_id)
                    ),
                )
            )
        return tuple(reviews)

    async def subscribe(self, listings: Sequence[ProviderListing]) -> None:
        self._require_connected()
        if not listings:
            raise ValueError("at least one listing is required")
        self._desired_listings = tuple(listings)
        await self._open_stream(self._desired_listings)

    async def force_reconnect(self) -> None:
        """Refresh the REST session and rebuild the single desired stream."""
        if not self._desired_listings:
            raise RuntimeError("subscribe before forcing a reconnect")
        if self._reconnecting:
            raise RuntimeError("IG stream reconnect is already in progress")
        self._reconnecting = True
        await self._reconnect_stream()

    async def _open_stream(self, listings: Sequence[ProviderListing]) -> None:
        install_lightstreamer_compatibility()
        from lightstreamer.client import (
            ClientListener,
            ItemUpdate,
            LightstreamerClient,
            Subscription,
            SubscriptionListener,
        )

        service = self._require_connected()
        adapter = self
        generation = self._generation + 1

        class PriceListener(SubscriptionListener):
            def __init__(self, epic: str) -> None:
                self._epic = epic

            def onItemUpdate(self, update: ItemUpdate) -> None:
                adapter._on_update(self._epic, update, generation=generation)

            def onSubscription(self) -> None:
                adapter._on_subscription(self._epic, generation)

            def onUnsubscription(self) -> None:
                adapter._on_unsubscription(self._epic, generation)

            def onSubscriptionError(self, code: int, message: str | None) -> None:
                adapter._on_subscription_error(self._epic, code, generation)

            def onItemLostUpdates(
                self,
                itemName: str | None,
                itemPos: int,
                lostUpdates: int,
            ) -> None:
                del itemName, itemPos
                adapter._on_item_lost_updates(self._epic, lostUpdates, generation)

            def onRealMaxFrequency(self, frequency: str | None) -> None:
                adapter._on_real_max_frequency(self._epic, frequency, generation)

        class HeartbeatListener(SubscriptionListener):
            def onItemUpdate(self, update: ItemUpdate) -> None:
                adapter._on_heartbeat(update, generation)

            def onSubscription(self) -> None:
                adapter._on_heartbeat_subscription(generation)

            def onUnsubscription(self) -> None:
                adapter._on_heartbeat_unsubscription(generation)

            def onSubscriptionError(self, code: int, message: str | None) -> None:
                del message
                adapter._on_heartbeat_subscription_error(code, generation)

            def onItemLostUpdates(
                self,
                itemName: str | None,
                itemPos: int,
                lostUpdates: int,
            ) -> None:
                del itemName, itemPos
                adapter._on_item_lost_updates(_HEARTBEAT_ITEM, lostUpdates, generation)

            def onRealMaxFrequency(self, frequency: str | None) -> None:
                adapter._on_heartbeat_real_max_frequency(frequency, generation)

        class StatusListener(ClientListener):
            def onStatusChange(self, status: str) -> None:
                adapter._on_stream_status(status, generation)

            def onServerError(self, code: int, message: str | None) -> None:
                del message
                adapter._on_server_error(code, generation)

        if self._stream_client is not None:
            raise RuntimeError("refusing to create a concurrent IG stream connection")
        if self._session_details is None:
            raise RuntimeError("IG REST session details are unavailable")
        endpoint = _string(self._session_details, "lightstreamerEndpoint")
        account_id = _string(self._session_details, "currentAccountId") or self._config.account_id
        cst = service.session.headers.get("CST")
        security_token = service.session.headers.get("X-SECURITY-TOKEN")
        if not endpoint or not account_id or not cst or not security_token:
            raise RuntimeError("IG REST session lacks Lightstreamer connection details")
        self._generation = generation
        self._expected_epics = {listing.listing_id.external_id for listing in listings}
        self._subscribed_epics.clear()
        self._updated_epics.clear()
        self._quote_received_times.clear()
        self._stale_epics.clear()
        self._real_max_frequency_by_epic.clear()
        self._heartbeat_subscribed = False
        self._heartbeat_events = 0
        self._last_heartbeat_at = None
        self._last_heartbeat_value = None
        self._heartbeat_real_max_frequency = None
        self._heartbeat_stale = False
        self._heartbeat_current_for_transport = False
        self._transport_connected = False
        self._ready_event = asyncio.Event()
        self._readiness_error = None
        self._last_message_at = None
        self._field_state.clear()
        self._side_times.clear()
        self._connection_state = _ConnectionState.CONNECTING
        self._status = HealthStatus.STARTING
        self._stream_account_id = account_id
        client = cast(_StreamClient, LightstreamerClient(endpoint, None))
        self._stream_client = client
        client.connectionDetails.setUser(account_id)
        client.connectionDetails.setPassword(f"CST-{cst}|XST-{security_token}")
        client.addListener(StatusListener())
        await self._run_provider_operation("stream_connect", client.connect)

        self._connection_state = _ConnectionState.SUBSCRIBING
        heartbeat = cast(
            _Subscription,
            Subscription(
                mode="MERGE",
                items=[_HEARTBEAT_ITEM],
                fields=["HEARTBEAT"],
            ),
        )
        heartbeat.addListener(HeartbeatListener())
        await self._run_provider_operation(
            "stream_subscribe_heartbeat",
            lambda: client.subscribe(heartbeat),
        )
        self._subscriptions.append(heartbeat)
        for listing in listings:
            epic = listing.listing_id.external_id
            self._listings_by_epic[epic] = listing
            subscription = cast(
                _Subscription,
                Subscription(
                    mode="MERGE",
                    items=[f"PRICE:{self._stream_account_id}:{epic}"],
                    fields=[
                        "TIMESTAMP",
                        "BIDPRICE1",
                        "ASKPRICE1",
                        "BIDSIZE1",
                        "ASKSIZE1",
                        "DLG_FLAG",
                        "DELAY",
                    ],
                ),
            )
            subscription.setDataAdapter("Pricing")
            subscription.addListener(PriceListener(epic))
            await self._run_provider_operation(
                "stream_subscribe",
                lambda subscription=subscription: client.subscribe(subscription),
            )
            self._subscriptions.append(subscription)
        self._stream_connected_at = self._clock.now()
        try:
            async with asyncio.timeout(self._config.readiness_timeout_seconds):
                await self._ready_event.wait()
            if self._readiness_error is not None:
                raise self._readiness_error
        except TimeoutError as error:
            self._connection_state = _ConnectionState.DEGRADED
            self._status = HealthStatus.DEGRADED
            raise TimeoutError(
                "IG stream did not establish all-subscription data readiness"
            ) from error

    async def records(self) -> AsyncIterator[MarketDataRecord]:
        self._require_connected()
        while not self._stopping:
            if self._fatal_error is not None:
                raise self._fatal_error
            try:
                record = await asyncio.wait_for(self._queue.get(), timeout=1)
                yield record
            except TimeoutError:
                self._check_staleness()
        if self._fatal_error is not None:
            raise self._fatal_error

    async def backfill(self, request: BackfillRequest) -> AsyncIterator[MarketBar]:
        if self._config.historical_request_interval_seconds > 0:
            await self._sleep(self._config.historical_request_interval_seconds)
        response = await self._run_rest_read(
            "fetch_historical_prices",
            lambda service: service.fetch_historical_prices_by_epic_and_date_range(
                request.listing.listing_id.external_id,
                request.resolution.value,
                _historical_query_time(request.start),
                _historical_query_time(request.end - timedelta(seconds=1)),
            ),
        )
        allowance = _mapping(_mapping(response).get("allowance"))
        remaining = _integer_or_none(allowance.get("remainingAllowance"))
        if remaining is not None:
            self._historical_allowance_remaining = remaining
        count = 0
        for row in _historical_rows(response):
            if count >= request.maximum_points:
                break
            interval_start = _historical_time(row)
            if interval_start is None or not (request.start <= interval_start < request.end):
                continue
            for bar in _historical_bars(request.listing, interval_start, row):
                yield bar
            count += 1

    @property
    def historical_allowance_remaining(self) -> int | None:
        return self._historical_allowance_remaining

    async def health(self) -> AdapterHealth:
        self._check_staleness()
        status = self._status
        now = self._clock.now()
        heartbeat_transport_current = str(self._heartbeat_current_for_transport).lower()
        return AdapterHealth(
            adapter_name="ig-market-data",
            environment=BrokerEnvironment.IG_DEMO,
            status=status,
            observed_at=now,
            last_message_at=self._last_message_at,
            detail=(
                "data only; production and order surfaces are unavailable; "
                f"state={self._connection_state}; generation={self._generation}; "
                f"subscriptions={len(self._subscribed_epics)}/{len(self._expected_epics)}; "
                f"updates={len(self._updated_epics)}/{len(self._expected_epics)}; "
                f"reconnects={self._reconnect_count}; dropped_records={self._dropped_records}; "
                f"lightstreamer_lost_updates={self._lightstreamer_lost_updates}; "
                f"subscription_events={self._subscription_events}; "
                f"unsubscription_events={self._unsubscription_events}; "
                f"subscription_errors={self._subscription_errors}; "
                f"server_errors={self._server_errors}; "
                f"last_server_error_code={self._last_server_error_code}; "
                f"stream_status={self._last_stream_status}; "
                f"stream_status_at={_health_time(self._last_stream_status_at)}; "
                f"heartbeat_subscribed={str(self._heartbeat_subscribed).lower()}; "
                f"heartbeat_events={self._heartbeat_events}; "
                f"last_heartbeat_at={_health_time(self._last_heartbeat_at)}; "
                f"last_heartbeat_value={self._last_heartbeat_value}; "
                f"heartbeat_stale={str(self._heartbeat_stale).lower()}; "
                f"heartbeat_transport_current={heartbeat_transport_current}; "
                f"heartbeat_frequency={self._heartbeat_real_max_frequency}; "
                f"frequency_evidence={len(self._real_max_frequency_by_epic)}/"
                f"{len(self._expected_epics)}; "
                f"first_drop_at={_health_time(self._first_drop_at)}; "
                f"last_drop_at={_health_time(self._last_drop_at)}; "
                f"queue={self._queue.qsize()}/{self._queue.maxsize}; "
                f"queue_high_water={self._queue_high_water}; "
                f"provider_operations={len(self._provider_threads)}; "
                f"rest_reauthentications={self._rest_reauthentications}; "
                f"published_rest_rates={self._published_trading_requests_per_minute}/"
                f"{self._published_non_trading_requests_per_minute}; "
                f"effective_rest_rates={self._effective_trading_requests_per_minute}/"
                f"{self._effective_non_trading_requests_per_minute}; "
                f"allowance_errors={self._allowance_errors}; "
                f"last_allowance_error={self._last_allowance_error}"
            ),
        )

    def _on_update(self, epic: str, update: _ItemUpdate, *, generation: int | None = None) -> None:
        observed_generation = self._generation if generation is None else generation
        if observed_generation != self._generation:
            return
        received = self._clock.now()
        raw = {
            field: update.getValue(position)
            for position, field in _PRICE_FIELDS_BY_POSITION
            if update.isValueChanged(position)
        }
        loop = self._loop
        if loop is None:
            raise RuntimeError("IG callback received before event loop registration")
        loop.call_soon_threadsafe(
            self._handle_update,
            epic,
            raw,
            received,
            observed_generation,
        )

    def _handle_update(
        self,
        epic: str,
        raw: Mapping[str, object | None],
        received: datetime,
        generation: int,
    ) -> None:
        """Normalise one callback on the event-loop thread.

        Lightstreamer invokes listeners on its dispatch thread. Subscription renewal clears
        merged field state on the event-loop thread, so updates must cross the same ordered
        boundary before reading or changing that state.
        """

        if generation != self._generation or self._stopping:
            return
        state = self._field_state.setdefault(epic, {})
        for field, value in raw.items():
            if value is None:
                state.pop(field, None)
            else:
                state[field] = str(value)
        timestamp = state.get("TIMESTAMP")
        digest = hashlib.sha256(
            json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:16]
        deduplication_key = f"{epic}:{timestamp or 'missing'}:{digest}"
        quote: MarketQuote | None = None
        error_code: str | None = None
        error_detail: str | None = None
        try:
            if timestamp is None:
                raise ValueError("IG PRICE update has no TIMESTAMP")
            event_time = datetime.fromtimestamp(int(timestamp) / 1000, tz=UTC)
            side_times = self._side_times.setdefault(epic, {})
            if "BIDPRICE1" in raw:
                if raw["BIDPRICE1"] is None:
                    side_times.pop("BID", None)
                else:
                    side_times["BID"] = event_time
            if "ASKPRICE1" in raw:
                if raw["ASKPRICE1"] is None:
                    side_times.pop("OFFER", None)
                else:
                    side_times["OFFER"] = event_time
            bid = _decimal_or_none(state.get("BIDPRICE1"))
            ask = _decimal_or_none(state.get("ASKPRICE1"))
            listing = self._listings_by_epic[epic]
            dealing_flag = state.get("DLG_FLAG", "").strip().upper()
            quality = (
                DataQuality.HEALTHY
                if dealing_flag in {"DEAL", "DEALNOEDIT"}
                else DataQuality.PARTIAL
            )
            quote = MarketQuote(
                instrument_id=listing.instrument_id,
                listing_id=listing.listing_id,
                event_time=event_time,
                received_time=received,
                bid=bid,
                ask=ask,
                bid_size=_decimal_or_none(state.get("BIDSIZE1")),
                ask_size=_decimal_or_none(state.get("ASKSIZE1")),
                bid_time=side_times.get("BID"),
                ask_time=side_times.get("OFFER"),
                quality=quality,
                source_sequence=timestamp,
            )
        except (KeyError, TypeError, ValueError) as error:
            error_code = "IG_NORMALISATION_FAILED"
            error_detail = str(error)

        serialised_raw = to_json_value(raw)
        if not isinstance(serialised_raw, dict):
            raise TypeError("IG update did not serialise to an object")
        record = MarketDataRecord(
            provider="ig",
            environment="demo",
            subscription=f"PRICE:{epic}",
            deduplication_key=deduplication_key,
            received_time=received,
            raw_payload=serialised_raw,
            payload_representation=RawPayloadRepresentation.CHANGED_FIELDS,
            quote=quote,
            error_code=error_code,
            error_detail=error_detail,
        )
        self._accept_update(record, epic, generation)

    def _on_heartbeat(self, update: _ItemUpdate, generation: int) -> None:
        value = update.getValue(_HEARTBEAT_FIELD_POSITION)
        received = self._clock.now()
        loop = self._loop
        if loop is None:
            raise RuntimeError("IG heartbeat received before event loop registration")
        loop.call_soon_threadsafe(self._handle_heartbeat, value, received, generation)

    def _handle_heartbeat(
        self,
        value: object | None,
        received: datetime,
        generation: int,
    ) -> None:
        if generation != self._generation or self._stopping:
            return
        if value is None or not str(value).strip():
            self._status = HealthStatus.DEGRADED
            LOGGER.warning(
                "ig_heartbeat_invalid",
                extra={"generation": generation},
            )
            return
        self._heartbeat_events += 1
        self._last_heartbeat_at = received
        self._last_heartbeat_value = str(value).strip()[:32]
        self._heartbeat_stale = False
        self._heartbeat_current_for_transport = True
        self._mark_ready_if_complete(generation)

    def _accept_update(self, record: MarketDataRecord, epic: str, generation: int) -> None:
        if generation != self._generation or self._stopping:
            return
        self._last_message_at = record.received_time
        quote = record.quote
        if quote is not None and quote.quality is DataQuality.HEALTHY:
            self._quote_received_times[epic] = record.received_time
            self._updated_epics.add(epic)
        self._mark_ready_if_complete(generation)
        self._enqueue_record(record, epic)

    def _enqueue_record(self, record: MarketDataRecord, epic: str) -> None:
        try:
            self._queue.put_nowait(record)
            self._queue_high_water = max(self._queue_high_water, self._queue.qsize())
        except asyncio.QueueFull:
            self._dropped_records += 1
            if self._first_drop_at is None:
                self._first_drop_at = record.received_time
            self._last_drop_at = record.received_time
            self._status = HealthStatus.DEGRADED
            if self._dropped_records == 1 or self._dropped_records % 1_000 == 0:
                LOGGER.error(
                    "ig_queue_saturated",
                    extra={
                        "epic": epic,
                        "dropped_records": self._dropped_records,
                        "queue_size": self._queue.qsize(),
                    },
                )

    def _check_staleness(self) -> None:
        if self._stopping or self._reconnecting or not self._desired_listings:
            return
        now = self._clock.now()
        stale_channels = ""
        stale_epics: set[str] = set()
        reconnect_required = False
        previous_heartbeat_stale = self._heartbeat_stale
        heartbeat_current = (
            self._heartbeat_subscribed
            and self._last_heartbeat_at is not None
            and now - self._last_heartbeat_at <= timedelta(seconds=self._config.stale_after_seconds)
        )
        if self._expected_epics:
            current_epics = {
                epic
                for epic, received_time in self._quote_received_times.items()
                if now - received_time <= timedelta(seconds=self._config.stale_after_seconds)
            }
            if current_epics == self._expected_epics and heartbeat_current:
                self._stale_epics.clear()
                self._heartbeat_stale = False
                return
            channel_evidence: list[str] = []
            stale_epics = self._expected_epics - current_epics
            for epic in sorted(stale_epics):
                listing = self._listings_by_epic.get(epic)
                label = str(listing.instrument_id) if listing is not None else epic
                received_time = self._quote_received_times.get(epic)
                age = f"{(now - received_time).total_seconds():.1f}" if received_time else "missing"
                channel_evidence.append(f"{label}:{age}")
                if received_time is None or now - received_time > timedelta(
                    seconds=self._config.stale_reconnect_after_seconds
                ):
                    reconnect_required = True
            if not heartbeat_current:
                heartbeat_age = (
                    f"{(now - self._last_heartbeat_at).total_seconds():.1f}"
                    if self._last_heartbeat_at is not None
                    else "missing"
                )
                channel_evidence.append(f"heartbeat:{heartbeat_age}")
                if self._last_heartbeat_at is None or now - self._last_heartbeat_at > timedelta(
                    seconds=self._config.stale_reconnect_after_seconds
                ):
                    reconnect_required = True
            stale_channels = ",".join(channel_evidence)
        else:
            anchor = self._last_message_at or self._stream_connected_at
            if anchor is None:
                return
            if now - anchor <= timedelta(seconds=self._config.stale_after_seconds):
                return
            reconnect_required = True
        self._status = HealthStatus.DEGRADED
        self._connection_state = _ConnectionState.DEGRADED
        self._heartbeat_stale = not heartbeat_current
        if (
            stale_epics != self._stale_epics
            or self._heartbeat_stale != previous_heartbeat_stale
            or reconnect_required
        ):
            LOGGER.warning("ig_stream_stale", extra={"stale_channels": stale_channels})
        self._stale_epics = stale_epics
        if reconnect_required or not self._expected_epics:
            self._schedule_reconnect()

    def _on_stream_status(self, status: str, generation: int | None = None) -> None:
        observed_generation = self._generation if generation is None else generation
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._handle_stream_status, status, observed_generation)

    def _handle_stream_status(self, status: str, generation: int | None = None) -> None:
        observed_generation = self._generation if generation is None else generation
        if observed_generation != self._generation or self._stopping:
            return
        normalised = status.upper()
        self._last_stream_status = normalised[:64]
        self._last_stream_status_at = self._clock.now()
        LOGGER.info(
            "ig_stream_status",
            extra={"generation": observed_generation, "status": normalised[:64]},
        )
        if normalised.startswith("CONNECTED:"):
            self._transport_connected = True
            self._stream_connected_at = self._clock.now()
            self._mark_ready_if_complete(observed_generation)
        elif normalised in {
            "STALLED",
            "DISCONNECTED:WILL-RETRY",
            "DISCONNECTED:TRYING-RECOVERY",
        }:
            self._mark_transport_degraded(clear_channel_evidence=False)
            self._start_retry_watchdog(observed_generation)
        elif normalised == "DISCONNECTED" and not self._stopping:
            self._mark_transport_degraded()
            self._schedule_reconnect()

    def _mark_transport_degraded(self, *, clear_channel_evidence: bool = True) -> None:
        self._transport_connected = False
        self._status = HealthStatus.DEGRADED
        self._connection_state = _ConnectionState.DEGRADED
        self._updated_epics.clear()
        self._heartbeat_current_for_transport = False
        if clear_channel_evidence:
            self._quote_received_times.clear()
            self._stale_epics.clear()
            self._field_state.clear()
            self._side_times.clear()
            self._last_message_at = None
            self._heartbeat_subscribed = False
            self._last_heartbeat_at = None
            self._last_heartbeat_value = None
            self._heartbeat_real_max_frequency = None
            self._heartbeat_stale = False

    def _on_heartbeat_subscription(self, generation: int) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(
                self._handle_heartbeat_subscription,
                generation,
            )

    def _handle_heartbeat_subscription(self, generation: int) -> None:
        if generation != self._generation or self._stopping:
            return
        self._subscription_events += 1
        self._heartbeat_subscribed = True
        self._heartbeat_current_for_transport = False
        self._last_heartbeat_at = None
        self._last_heartbeat_value = None
        self._heartbeat_stale = False
        LOGGER.info(
            "ig_heartbeat_subscription_established",
            extra={"generation": generation},
        )

    def _on_heartbeat_unsubscription(self, generation: int) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(
                self._handle_heartbeat_unsubscription,
                generation,
            )

    def _handle_heartbeat_unsubscription(self, generation: int) -> None:
        if generation != self._generation or self._stopping:
            return
        self._unsubscription_events += 1
        self._heartbeat_subscribed = False
        self._heartbeat_current_for_transport = False
        self._last_heartbeat_at = None
        self._last_heartbeat_value = None
        self._heartbeat_stale = True
        self._status = HealthStatus.DEGRADED
        self._connection_state = _ConnectionState.DEGRADED
        LOGGER.warning(
            "ig_heartbeat_subscription_ended",
            extra={"generation": generation},
        )

    def _on_heartbeat_subscription_error(self, code: int, generation: int) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(
                self._handle_heartbeat_subscription_error,
                code,
                generation,
            )

    def _handle_heartbeat_subscription_error(self, code: int, generation: int) -> None:
        if generation != self._generation or self._stopping:
            return
        self._subscription_errors += 1
        self._heartbeat_subscribed = False
        self._heartbeat_current_for_transport = False
        self._heartbeat_stale = True
        self._status = HealthStatus.DEGRADED
        self._connection_state = _ConnectionState.DEGRADED
        self._readiness_error = RuntimeError(f"IG heartbeat subscription failed with code {code}")
        self._ready_event.set()
        LOGGER.error(
            "ig_heartbeat_subscription_error",
            extra={"code": code, "generation": generation},
        )

    def _on_heartbeat_real_max_frequency(
        self,
        frequency: str | None,
        generation: int,
    ) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(
                self._handle_heartbeat_real_max_frequency,
                frequency,
                generation,
            )

    def _handle_heartbeat_real_max_frequency(
        self,
        frequency: str | None,
        generation: int,
    ) -> None:
        if generation != self._generation or self._stopping:
            return
        bounded_frequency = "UNDETERMINED" if frequency is None else str(frequency).strip()[:32]
        if not bounded_frequency:
            raise ValueError("Lightstreamer heartbeat frequency must not be empty")
        self._heartbeat_real_max_frequency = bounded_frequency
        LOGGER.info(
            "ig_heartbeat_frequency",
            extra={"generation": generation, "real_max_frequency": bounded_frequency},
        )

    def _on_subscription(self, epic: str, generation: int) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._handle_subscription, epic, generation)

    def _handle_subscription(self, epic: str, generation: int) -> None:
        if generation != self._generation or self._stopping:
            return
        self._subscription_events += 1
        self._invalidate_subscription_evidence(epic)
        self._subscribed_epics.add(epic)
        LOGGER.info(
            "ig_subscription_established",
            extra={"epic": epic, "generation": generation},
        )
        self._mark_ready_if_complete(generation)

    def _on_unsubscription(self, epic: str, generation: int) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._handle_unsubscription, epic, generation)

    def _handle_unsubscription(self, epic: str, generation: int) -> None:
        if generation != self._generation:
            return
        self._unsubscription_events += 1
        self._subscribed_epics.discard(epic)
        self._invalidate_subscription_evidence(epic)
        LOGGER.info(
            "ig_subscription_ended",
            extra={"epic": epic, "generation": generation},
        )
        if not self._stopping:
            self._status = HealthStatus.DEGRADED
            self._connection_state = _ConnectionState.DEGRADED

    def _invalidate_subscription_evidence(self, epic: str) -> None:
        """Discard state that the SDK declares invalid after a subscription lifecycle change."""

        self._updated_epics.discard(epic)
        self._quote_received_times.pop(epic, None)
        self._stale_epics.discard(epic)
        self._field_state.pop(epic, None)
        self._side_times.pop(epic, None)

    def _on_subscription_error(self, epic: str, code: int, generation: int | None = None) -> None:
        observed_generation = self._generation if generation is None else generation
        if self._loop is not None:
            self._loop.call_soon_threadsafe(
                self._handle_subscription_error, epic, code, observed_generation
            )

    def _handle_subscription_error(
        self, epic: str, code: int, generation: int | None = None
    ) -> None:
        observed_generation = self._generation if generation is None else generation
        if observed_generation != self._generation or self._stopping:
            return
        self._subscription_errors += 1
        establishing = self._connection_state in {
            _ConnectionState.CONNECTING,
            _ConnectionState.SUBSCRIBING,
        }
        self._status = HealthStatus.DEGRADED
        self._connection_state = _ConnectionState.DEGRADED
        LOGGER.error(
            "ig_subscription_error",
            extra={"epic": epic, "code": code, "generation": observed_generation},
        )
        if establishing:
            self._readiness_error = RuntimeError(f"IG subscription failed with bounded code {code}")
            self._ready_event.set()
        else:
            self._schedule_reconnect()

    def _on_item_lost_updates(self, epic: str, count: int, generation: int) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(
                self._handle_item_lost_updates,
                epic,
                count,
                generation,
            )

    def _handle_item_lost_updates(self, epic: str, count: int, generation: int) -> None:
        if generation != self._generation or self._stopping:
            return
        if count <= 0:
            raise ValueError("Lightstreamer lost-update count must be positive")
        self._lightstreamer_lost_updates += count
        self._status = HealthStatus.DEGRADED
        LOGGER.error(
            "ig_lightstreamer_updates_lost",
            extra={
                "epic": epic,
                "generation": generation,
                "lost_updates": count,
                "lost_updates_total": self._lightstreamer_lost_updates,
            },
        )

    def _on_real_max_frequency(
        self,
        epic: str,
        frequency: str | None,
        generation: int,
    ) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(
                self._handle_real_max_frequency,
                epic,
                frequency,
                generation,
            )

    def _handle_real_max_frequency(
        self,
        epic: str,
        frequency: str | None,
        generation: int,
    ) -> None:
        if generation != self._generation or self._stopping:
            return
        bounded_frequency = "UNDETERMINED" if frequency is None else str(frequency).strip()[:32]
        if not bounded_frequency:
            raise ValueError("Lightstreamer real maximum frequency must not be empty")
        self._real_max_frequency_by_epic[epic] = bounded_frequency
        LOGGER.info(
            "ig_subscription_frequency",
            extra={
                "epic": epic,
                "generation": generation,
                "real_max_frequency": bounded_frequency,
            },
        )

    def _on_server_error(self, code: int, generation: int) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._handle_server_error, code, generation)

    def _handle_server_error(self, code: int, generation: int) -> None:
        if generation != self._generation or self._stopping:
            return
        self._server_errors += 1
        self._last_server_error_code = code
        self._status = HealthStatus.DEGRADED
        self._connection_state = _ConnectionState.DEGRADED
        LOGGER.error(
            "ig_stream_server_error",
            extra={"code": code, "generation": generation},
        )
        self._schedule_reconnect()

    def _mark_ready_if_complete(self, generation: int) -> None:
        if generation != self._generation or self._stopping:
            return
        if (
            not self._transport_connected
            or not self._heartbeat_subscribed
            or not self._heartbeat_current_for_transport
            or self._last_heartbeat_at is None
            or self._subscribed_epics != self._expected_epics
            or self._updated_epics != self._expected_epics
            or any(
                self._clock.now() - self._quote_received_times[epic]
                > timedelta(seconds=self._config.stale_after_seconds)
                for epic in self._expected_epics
            )
        ):
            return
        watchdog_task = self._retry_watchdog_task
        if watchdog_task is not None and watchdog_task is not asyncio.current_task():
            watchdog_task.cancel()
        self._retry_watchdog_task = None
        self._connection_state = _ConnectionState.READY
        self._status = (
            HealthStatus.HEALTHY
            if self._dropped_records == 0 and self._lightstreamer_lost_updates == 0
            else HealthStatus.DEGRADED
        )
        self._ready_event.set()

    def _start_retry_watchdog(self, generation: int) -> None:
        watchdog_task = self._retry_watchdog_task
        if watchdog_task is not None and not watchdog_task.done():
            return

        async def watchdog() -> None:
            try:
                await self._sleep(self._config.retry_watchdog_seconds)
                if (
                    generation == self._generation
                    and self._connection_state is not _ConnectionState.READY
                    and not self._stopping
                ):
                    LOGGER.warning(
                        "ig_stream_retry_watchdog_expired",
                        extra={"generation": generation},
                    )
                    self._schedule_reconnect()
            finally:
                if self._retry_watchdog_task is asyncio.current_task():
                    self._retry_watchdog_task = None

        self._retry_watchdog_task = asyncio.create_task(watchdog())

    def _schedule_reconnect(self) -> None:
        if self._stopping or self._reconnecting or not self._desired_listings:
            return
        self._reconnecting = True
        self._connection_state = _ConnectionState.DEGRADED
        task = asyncio.create_task(self._reconnect_stream())
        task.add_done_callback(self._reconnect_done)
        self._reconnect_task = task

    def _reconnect_done(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            self._fatal_error = (
                error if isinstance(error, Exception) else RuntimeError(type(error).__name__)
            )
            self._status = HealthStatus.DISCONNECTED
            self._connection_state = _ConnectionState.FAILED
            LOGGER.error(
                "ig_reconnect_exhausted",
                extra={
                    "error_type": type(error).__name__,
                    "error_code": _safe_error_code(error),
                },
            )

    async def _reconnect_stream(self) -> None:
        last_error: Exception | None = None
        cycle = 0
        try:
            while not self._stopping:
                cycle += 1
                for attempt in range(1, self._config.reconnect_attempts + 1):
                    try:
                        await self._close_stream()
                        await self._logout_rest_session()
                        self._connection_state = _ConnectionState.AUTHENTICATING
                        await self._establish_rest_session(attempts=1)
                        await self._open_stream(self._desired_listings)
                        self._reconnect_count += 1
                        self._fatal_error = None
                        return
                    except asyncio.CancelledError:
                        raise
                    except Exception as error:
                        last_error = error
                        self._status = HealthStatus.DEGRADED
                        self._connection_state = _ConnectionState.BACKING_OFF
                        error_code = _safe_error_code(error)
                        if _is_fatal_provider_error(error_code):
                            raise
                        if attempt == self._config.reconnect_attempts:
                            break
                        delay = self._jittered_backoff(attempt)
                        LOGGER.warning(
                            "ig_reconnect_retry",
                            extra={
                                "attempt": attempt,
                                "cycle": cycle,
                                "delay_seconds": delay,
                                "error_type": type(error).__name__,
                                "error_code": error_code,
                            },
                        )
                        await self._sleep(delay)
                if cycle >= self._config.maximum_reconnect_cycles:
                    if last_error is not None:
                        raise last_error
                    raise RuntimeError("IG reconnect attempts exhausted")
                self._connection_state = _ConnectionState.BACKING_OFF
                cooldown = max(
                    1.0,
                    self._jitter(
                        0.0,
                        self._config.reconnect_cooldown_seconds,
                    ),
                )
                LOGGER.warning(
                    "ig_reconnect_cooldown",
                    extra={
                        "cycle": cycle,
                        "delay_seconds": cooldown,
                        "error_type": type(last_error).__name__ if last_error else None,
                        "error_code": _safe_error_code(last_error) if last_error else None,
                    },
                )
                await self._sleep(cooldown)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._fatal_error = error
            self._status = HealthStatus.DISCONNECTED
            self._connection_state = _ConnectionState.FAILED
            raise
        finally:
            self._reconnecting = False
            self._reconnect_task = None

    def _jittered_backoff(self, failed_attempt: int) -> float:
        maximum = _backoff_seconds(self._config, failed_attempt)
        return max(1.0, self._jitter(0.0, maximum))

    async def _establish_rest_session(self, *, attempts: int | None = None) -> None:
        maximum_attempts = attempts or self._config.connect_attempts
        last_error: Exception | None = None
        for attempt in range(1, maximum_attempts + 1):
            try:
                service = self._new_service()
                self._service = service
                session_details = await self._run_provider_operation(
                    "create_session",
                    service.create_session,
                )
                self._session_details = _single_record(session_details)
                self._record_rate_limiter_evidence(service)
                self._status = HealthStatus.STARTING
                self._connection_state = _ConnectionState.AUTHENTICATED
                return
            except asyncio.CancelledError:
                raise
            except Exception as error:
                last_error = error
                if isinstance(error, _ProviderOperationTimeout):
                    current_service = self._service
                    if current_service is not None:
                        self._stop_rate_limiter(current_service)
                    self._status = HealthStatus.DISCONNECTED
                    self._connection_state = _ConnectionState.FAILED
                    raise
                await self._logout_rest_session()
                if _is_fatal_provider_error(_safe_error_code(error)):
                    break
                if attempt == maximum_attempts:
                    break
                self._connection_state = _ConnectionState.BACKING_OFF
                delay = self._jittered_backoff(attempt)
                LOGGER.warning(
                    "ig_connect_retry",
                    extra={
                        "attempt": attempt,
                        "delay_seconds": delay,
                        "error_type": type(error).__name__,
                        "error_code": _safe_error_code(error),
                    },
                )
                await self._sleep(delay)
        self._status = HealthStatus.DISCONNECTED
        self._connection_state = _ConnectionState.FAILED
        if last_error is not None:
            raise last_error
        raise RuntimeError("IG session establishment failed")

    def _new_service(self) -> _IgRestService:
        if self._service_factory is not None:
            return cast(_IgRestService, self._service_factory(self._config))
        from trading_ig.rest import IGService

        configured_api_key = self._config.api_key

        class ValidatedRateLimitedIGService(IGService):  # type: ignore[misc]
            _qtrad_published_trading_requests_per_minute: int
            _qtrad_published_non_trading_requests_per_minute: int

            def get_client_apps(self, session: object | None = None) -> object:
                response = cast(
                    object,
                    super().get_client_apps(session),  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]
                )
                trading, non_trading = _validated_client_app_allowances(
                    response,
                    configured_api_key,
                )
                self._qtrad_published_trading_requests_per_minute = trading
                self._qtrad_published_non_trading_requests_per_minute = non_trading
                return response

        service = cast(
            _IgRestService,
            ValidatedRateLimitedIGService(
                self._config.username,
                self._config.password,
                self._config.api_key,
                acc_type=self._config.account_type,
                acc_number=self._config.account_id,
                use_rate_limiter=True,
            ),
        )
        _install_default_http_timeout(
            cast(_ConfigurableHttpSession, service.session),
            (
                self._config.http_connect_timeout_seconds,
                self._config.http_read_timeout_seconds,
            ),
        )
        return service

    async def _run_rest_read(
        self,
        operation_name: str,
        operation: Callable[[_IgRestService], _T],
    ) -> _T:
        """Run one idempotent REST read with one controlled invalid-token recovery."""

        service = self._require_connected()
        try:
            return await self._run_provider_operation(
                operation_name,
                lambda: operation(service),
            )
        except Exception as error:
            self._record_allowance_error(error)
            if not _is_token_invalid_exception(error):
                raise

        async with self._rest_reauthentication_lock:
            if self._service is service:
                LOGGER.warning(
                    "ig_rest_session_invalid",
                    extra={"operation": operation_name, "generation": self._generation},
                )
                await self._reauthenticate_after_invalid_token()
                self._rest_reauthentications += 1
        retry_service = self._require_connected()
        try:
            return await self._run_provider_operation(
                operation_name,
                lambda: operation(retry_service),
            )
        except Exception as error:
            self._record_allowance_error(error)
            raise

    async def _reauthenticate_after_invalid_token(self) -> None:
        if self._reconnecting:
            raise RuntimeError("cannot reauthenticate REST while stream recovery is active")
        if self._desired_listings:
            self._reconnecting = True
            await self._reconnect_stream()
            return
        await self._logout_rest_session()
        self._connection_state = _ConnectionState.AUTHENTICATING
        await self._establish_rest_session()

    def _record_allowance_error(self, error: Exception) -> None:
        error_code = _safe_error_code(error)
        if not error_code.startswith("error.public-api.exceeded-"):
            return
        self._allowance_errors += 1
        self._last_allowance_error = error_code
        LOGGER.warning(
            "ig_rest_allowance_exceeded",
            extra={"error_code": error_code, "generation": self._generation},
        )

    def _record_rate_limiter_evidence(self, service: _IgRestService) -> None:
        if not isinstance(service, _RateLimiterEvidence) or not isinstance(
            service, _PublishedRateLimiterEvidence
        ):
            self._published_trading_requests_per_minute = None
            self._published_non_trading_requests_per_minute = None
            self._effective_trading_requests_per_minute = None
            self._effective_non_trading_requests_per_minute = None
            return
        published_trading = service._qtrad_published_trading_requests_per_minute  # pyright: ignore[reportPrivateUsage]
        published_non_trading = service._qtrad_published_non_trading_requests_per_minute  # pyright: ignore[reportPrivateUsage]
        trading = service._trading_requests_per_minute  # pyright: ignore[reportPrivateUsage]
        non_trading = service._non_trading_requests_per_minute  # pyright: ignore[reportPrivateUsage]
        expected_trading = published_trading - _TRADING_IG_RATE_LIMIT_SAFETY_MARGIN
        expected_non_trading = published_non_trading - _TRADING_IG_RATE_LIMIT_SAFETY_MARGIN
        if trading != expected_trading or non_trading != expected_non_trading:
            raise RuntimeError(
                "trading-ig effective REST request rates do not match the reviewed safety margin"
            )
        if trading <= 0 or non_trading <= 0:
            raise RuntimeError("trading-ig configured a non-positive effective REST request rate")
        self._published_trading_requests_per_minute = published_trading
        self._published_non_trading_requests_per_minute = published_non_trading
        self._effective_trading_requests_per_minute = trading
        self._effective_non_trading_requests_per_minute = non_trading
        LOGGER.info(
            "ig_rest_rate_limiter_configured",
            extra={
                "effective_trading_requests_per_minute": trading,
                "effective_non_trading_requests_per_minute": non_trading,
                "published_trading_requests_per_minute": published_trading,
                "published_non_trading_requests_per_minute": published_non_trading,
            },
        )

    async def _close_stream(self) -> None:
        client = self._stream_client
        subscriptions = tuple(self._subscriptions)
        if client is not None:
            self._generation += 1
        self._transport_connected = False
        self._updated_epics.clear()
        watchdog_task = self._retry_watchdog_task
        if watchdog_task is not None and watchdog_task is not asyncio.current_task():
            watchdog_task.cancel()
            with suppress(asyncio.CancelledError):
                await watchdog_task
        self._retry_watchdog_task = None
        if client is None:
            self._clear_stream_state()
            return
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._config.shutdown_timeout_seconds
        for subscription in subscriptions:
            try:
                await self._run_provider_operation(
                    "stream_unsubscribe",
                    lambda subscription=subscription: client.unsubscribe(subscription),
                    timeout=self._remaining_shutdown_time(deadline),
                )
            except _ProviderOperationTimeout:
                raise
            except Exception:
                LOGGER.warning("ig_unsubscribe_failed")
        try:
            await self._run_provider_operation(
                "stream_disconnect",
                client.disconnect,
                timeout=self._remaining_shutdown_time(deadline),
            )
        except Exception:
            LOGGER.warning("ig_stream_disconnect_failed")
            raise
        while loop.time() < deadline:
            status = await self._run_provider_operation(
                "stream_status",
                client.getStatus,
                timeout=self._remaining_shutdown_time(deadline),
            )
            if status.upper() == "DISCONNECTED":
                self._clear_stream_state()
                return
            await asyncio.sleep(0.05)
        raise TimeoutError("IG Lightstreamer client did not confirm disconnect")

    async def _logout_rest_session(self) -> None:
        service = self._service
        if service is None:
            return
        try:
            await self._run_provider_operation("logout", service.logout)
        except _ProviderOperationTimeout:
            raise
        except Exception:
            LOGGER.warning("ig_logout_failed")
        finally:
            self._stop_rate_limiter(service)
        await self._run_provider_operation("http_session_close", service.session.close)
        self._service = None
        self._session_details = None

    async def _run_provider_operation(
        self,
        operation_name: str,
        operation: Callable[[], _T],
        *,
        timeout: float | None = None,
    ) -> _T:
        """Run one synchronous provider call without owning a non-daemon executor thread."""
        loop = asyncio.get_running_loop()
        result: asyncio.Future[_T] = loop.create_future()
        started = loop.time()

        def run() -> None:
            try:
                value = operation()
            except BaseException as error:

                def complete_error(captured_error: BaseException = error) -> None:
                    self._provider_threads.pop(thread, None)
                    if not result.done():
                        result.set_exception(captured_error)

                completion = complete_error
            else:

                def complete_value() -> None:
                    self._provider_threads.pop(thread, None)
                    if not result.done():
                        result.set_result(value)

                completion = complete_value
            try:
                loop.call_soon_threadsafe(completion)
            except RuntimeError:
                return

        thread = Thread(
            target=run,
            name=f"qtrad-ig-{operation_name}",
            daemon=True,
        )
        self._provider_threads[thread] = operation_name
        LOGGER.info(
            "ig_provider_operation_started",
            extra={"operation": operation_name, "generation": self._generation},
        )
        thread.start()
        maximum_seconds = timeout or self._config.provider_operation_timeout_seconds
        try:
            async with asyncio.timeout(maximum_seconds):
                value = await asyncio.shield(result)
        except TimeoutError as error:
            result.cancel()
            self._abandoned_provider_operation = True
            LOGGER.error(
                "ig_provider_operation_timed_out",
                extra={
                    "operation": operation_name,
                    "generation": self._generation,
                    "duration_seconds": loop.time() - started,
                },
            )
            raise _ProviderOperationTimeout(operation_name) from error
        except asyncio.CancelledError:
            result.cancel()
            if thread.is_alive():
                self._abandoned_provider_operation = True
                LOGGER.error(
                    "ig_provider_operation_cancelled",
                    extra={"operation": operation_name, "generation": self._generation},
                )
            raise
        LOGGER.info(
            "ig_provider_operation_completed",
            extra={
                "operation": operation_name,
                "generation": self._generation,
                "duration_seconds": loop.time() - started,
            },
        )
        return value

    async def _wait_for_provider_operations(self) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._config.shutdown_timeout_seconds
        while (
            any(thread.is_alive() for thread in self._provider_threads) and loop.time() < deadline
        ):
            await asyncio.sleep(0.05)
        active = self._active_provider_operation_names()
        if active:
            raise RuntimeError(
                "IG provider operations did not stop before shutdown: " + ",".join(active)
            )
        self._abandoned_provider_operation = False

    def _active_provider_operation_names(self) -> list[str]:
        return sorted(
            {operation for thread, operation in self._provider_threads.items() if thread.is_alive()}
        )

    def _remaining_shutdown_time(self, deadline: float) -> float:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError("IG Lightstreamer shutdown deadline expired")
        return remaining

    def _clear_stream_state(self) -> None:
        self._stream_client = None
        self._stream_account_id = None
        self._stream_connected_at = None
        self._last_message_at = None
        self._transport_connected = False
        self._expected_epics.clear()
        self._subscribed_epics.clear()
        self._updated_epics.clear()
        self._quote_received_times.clear()
        self._stale_epics.clear()
        self._heartbeat_subscribed = False
        self._last_heartbeat_at = None
        self._last_heartbeat_value = None
        self._heartbeat_real_max_frequency = None
        self._heartbeat_stale = False
        self._heartbeat_current_for_transport = False
        self._ready_event = asyncio.Event()
        self._readiness_error = None
        self._subscriptions.clear()

    @staticmethod
    def _stop_rate_limiter(service: _IgRestService) -> None:
        if not isinstance(service, _RateLimiterControl):
            return
        try:
            # trading-ig exposes no public local-only rate-limiter shutdown hook.
            service._exit_bucket_threads()  # pyright: ignore[reportPrivateUsage]
        except Exception:
            LOGGER.warning("ig_rate_limiter_stop_failed")

    def _require_connected(self) -> _IgRestService:
        if self._service is None or self._connection_state in {
            _ConnectionState.STOPPED,
            _ConnectionState.FAILED,
            _ConnectionState.STOPPING,
        }:
            raise RuntimeError("IG demo adapter is not connected")
        return self._service


def _install_default_http_timeout(
    session: _ConfigurableHttpSession,
    timeout: tuple[float, float],
) -> None:
    """Bound trading-ig HTTP calls while preserving explicit request timeouts."""
    request = session.request

    def request_with_timeout(method: str, url: str, **kwargs: object) -> object:
        kwargs.setdefault("timeout", timeout)
        return request(method, url, **kwargs)

    session.request = request_with_timeout


def _backoff_seconds(config: IgDemoConfig, failed_attempt: int) -> float:
    return min(
        config.initial_backoff_seconds * (2 ** (failed_attempt - 1)),
        config.maximum_backoff_seconds,
    )


def _safe_error_code(error: BaseException) -> str:
    if isinstance(error, _ProviderOperationTimeout):
        return "OPERATION_TIMEOUT"
    if isinstance(error, TimeoutError):
        return "TIMEOUT"
    if isinstance(error, ConnectionError):
        return "CONNECTION_ERROR"
    match = _PROVIDER_ERROR_CODE.search(str(error).lower())
    if match is not None:
        return match.group(1)[:96]
    return type(error).__name__.upper()[:96]


def _is_token_invalid_exception(error: BaseException) -> bool:
    from trading_ig.rest import TokenInvalidException

    return isinstance(error, TokenInvalidException)


def _is_fatal_provider_error(error_code: str) -> bool:
    return error_code in _FATAL_PROVIDER_ERRORS


def _records(value: object) -> list[Mapping[str, object]]:
    if value is None:
        return []
    if isinstance(value, _ToDict):
        try:
            converted = value.to_dict(orient="records")
        except TypeError:
            converted = value.to_dict()
        return _records(converted)
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        for key in ("markets", "prices"):
            nested = mapping.get(key)
            if isinstance(nested, Sequence) and not isinstance(nested, (str, bytes)):
                sequence = cast(Sequence[object], nested)
                return [cast(Mapping[str, object], item) for item in sequence]
        return [cast(Mapping[str, object], mapping)]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        sequence = cast(Sequence[object], value)
        return [cast(Mapping[str, object], item) for item in sequence]
    raise TypeError(f"unsupported IG response type: {type(value).__name__}")


def _validated_client_app_allowances(value: object, api_key: str) -> tuple[int, int]:
    """Return published rates for exactly one current-key client-app response."""

    matches: list[Mapping[str, object]] = []
    for row in _records(value):
        if "apiKey" not in row:
            raise RuntimeError("IG client-app response has no apiKey")
        if row["apiKey"] == api_key:
            matches.append(row)
    if len(matches) != 1:
        raise RuntimeError(
            "IG client-app response must contain exactly one entry for the configured API key"
        )
    match = matches[0]
    try:
        trading = match["allowanceAccountTrading"]
        non_trading = match["allowanceAccountOverall"]
    except KeyError as error:
        raise RuntimeError("IG client-app response lacks a required published allowance") from error
    for name, allowance in (
        ("allowanceAccountTrading", trading),
        ("allowanceAccountOverall", non_trading),
    ):
        if (
            not isinstance(allowance, int)
            or isinstance(allowance, bool)
            or allowance <= _TRADING_IG_RATE_LIMIT_SAFETY_MARGIN
        ):
            raise RuntimeError(f"IG client-app {name} is not a usable integer allowance")
    return cast(int, trading), cast(int, non_trading)


def _single_record(value: object) -> Mapping[str, object]:
    records = _records(value)
    if len(records) != 1:
        raise ValueError(f"expected one IG record, received {len(records)}")
    return records[0]


def _candidate(search_row: Mapping[str, object], detail: Mapping[str, object]) -> _Candidate | None:
    instrument = _mapping(detail.get("instrument"))
    snapshot = _mapping(detail.get("snapshot"))
    dealing_rules = _mapping(detail.get("dealingRules"))
    epic = _string(search_row, "epic") or _string(instrument, "epic")
    if not epic:
        return None
    minimum = _nested_decimal(dealing_rules, "minDealSize", "value")
    if minimum is None or minimum <= 0:
        return None
    currency = _currency(instrument)
    metadata = to_json_value(detail)
    if not isinstance(metadata, dict):
        return None
    return _Candidate(
        epic=epic,
        name=(_string(search_row, "instrumentName") or _string(instrument, "name") or epic),
        instrument_type=(
            _string(search_row, "instrumentType") or _string(instrument, "type") or ""
        ),
        expiry=_string(search_row, "expiry") or _string(instrument, "expiry") or "",
        market_status=(
            _string(search_row, "marketStatus") or _string(snapshot, "marketStatus") or ""
        ),
        currency=currency,
        minimum_deal_size=minimum,
        metadata=metadata,
    )


def _search_row_can_match(search_row: Mapping[str, object], instrument: Instrument) -> bool:
    expected_type = "CURRENCIES" if instrument.asset_class is AssetClass.FX else "INDICES"
    instrument_type = (_string(search_row, "instrumentType") or "").upper()
    expiry = (_string(search_row, "expiry") or "").upper()
    market_status = (_string(search_row, "marketStatus") or "").upper()
    return (
        (not instrument_type or instrument_type == expected_type)
        and (not expiry or expiry in _ROLLING_EXPIRIES)
        and market_status not in _UNAVAILABLE_MARKET_STATES
    )


def _search_row_is_review_relevant(
    search_row: Mapping[str, object], instrument: Instrument
) -> bool:
    """Exclude unrelated product families while retaining dated and unavailable evidence."""

    expected_type = "CURRENCIES" if instrument.asset_class is AssetClass.FX else "INDICES"
    instrument_type = (_string(search_row, "instrumentType") or "").upper()
    return not instrument_type or instrument_type == expected_type


def _search_row_needs_detail(search_row: Mapping[str, object], instrument: Instrument) -> bool:
    """Fetch detail only where a search row could still be an eligible listing."""

    return _search_row_can_match(search_row, instrument)


def _listing_review_candidate(
    search_row: Mapping[str, object],
    detail: Mapping[str, object] | None,
    instrument: Instrument,
) -> ListingReviewCandidate:
    detail_instrument: Mapping[str, object] = (
        _mapping(detail.get("instrument")) if detail is not None else {}
    )
    snapshot: Mapping[str, object] = _mapping(detail.get("snapshot")) if detail is not None else {}
    dealing_rules: Mapping[str, object] = (
        _mapping(detail.get("dealingRules")) if detail is not None else {}
    )
    epic = _string(search_row, "epic") or _string(detail_instrument, "epic")
    if not epic:
        raise RuntimeError(f"IG listing review detail has no epic for {instrument.instrument_id}")

    raw_product_type = (
        _string(search_row, "instrumentType") or _string(detail_instrument, "type") or ""
    ).upper()
    product_type = _review_product_type(raw_product_type)
    raw_expiry = (
        _string(search_row, "expiry") or _string(detail_instrument, "expiry") or ""
    ).upper()
    expiry_kind = _review_expiry_kind(raw_expiry)
    raw_market_state = (
        _string(search_row, "marketStatus") or _string(snapshot, "marketStatus") or ""
    ).upper()
    market_state = _review_market_state(raw_market_state)
    currency = _currency(detail_instrument).upper() or None
    minimum_deal_size = _nested_decimal(dealing_rules, "minDealSize", "value")
    expected_product_type = (
        ProductType.SPOT_FX if instrument.asset_class is AssetClass.FX else ProductType.ROLLING_CFD
    )

    rejections: list[ListingReviewRejection] = []
    if product_type is not expected_product_type:
        rejections.append(ListingReviewRejection.WRONG_PRODUCT_TYPE)
    if expiry_kind is not ListingExpiryKind.ROLLING:
        rejections.append(ListingReviewRejection.NON_ROLLING_EXPIRY)
    if market_state is ListingMarketState.UNAVAILABLE:
        rejections.append(ListingReviewRejection.UNAVAILABLE_MARKET)
    elif market_state is ListingMarketState.UNKNOWN:
        rejections.append(ListingReviewRejection.UNKNOWN_MARKET_STATE)

    economics: Mapping[str, JsonValue] = {}
    metadata_version: str | None = None
    if detail is not None:
        if not currency:
            rejections.append(ListingReviewRejection.MISSING_CURRENCY)
        elif currency != instrument.quote_currency.upper():
            rejections.append(ListingReviewRejection.WRONG_CURRENCY)
        if minimum_deal_size is None:
            rejections.append(ListingReviewRejection.MISSING_MINIMUM_DEAL_SIZE)
        elif minimum_deal_size <= 0:
            rejections.append(ListingReviewRejection.INVALID_MINIMUM_DEAL_SIZE)

        bounded_detail = to_json_value(detail)
        if not isinstance(bounded_detail, dict):
            raise TypeError("IG listing review detail did not serialise to an object")
        economics = _bounded_economics(bounded_detail)
        if minimum_deal_size is not None and minimum_deal_size > 0:
            metadata_version = _listing_metadata_version(
                _Candidate(
                    epic=epic,
                    name=(
                        _string(search_row, "instrumentName")
                        or _string(detail_instrument, "name")
                        or epic
                    ),
                    instrument_type=raw_product_type,
                    expiry=raw_expiry,
                    market_status=raw_market_state,
                    currency=currency or "",
                    minimum_deal_size=minimum_deal_size,
                    metadata=bounded_detail,
                ),
                economics,
            )

    return ListingReviewCandidate(
        instrument_id=instrument.instrument_id,
        listing_id=ProviderListingId("ig", "demo", epic),
        display_name=(
            _string(search_row, "instrumentName") or _string(detail_instrument, "name") or epic
        ),
        product_type=product_type,
        expiry_kind=expiry_kind,
        market_state=market_state,
        currency=currency,
        minimum_deal_size=minimum_deal_size,
        economics=economics,
        metadata_version=metadata_version,
        rejection_reasons=tuple(rejections),
    )


def _review_product_type(raw_product_type: str) -> ProductType:
    if raw_product_type == "CURRENCIES":
        return ProductType.SPOT_FX
    if raw_product_type == "INDICES":
        return ProductType.ROLLING_CFD
    return ProductType.UNKNOWN


def _review_expiry_kind(raw_expiry: str) -> ListingExpiryKind:
    if raw_expiry in _ROLLING_EXPIRIES:
        return ListingExpiryKind.ROLLING
    if raw_expiry:
        return ListingExpiryKind.DATED
    return ListingExpiryKind.UNKNOWN


def _review_market_state(raw_market_state: str) -> ListingMarketState:
    if raw_market_state == "TRADEABLE":
        return ListingMarketState.TRADEABLE
    if raw_market_state in _UNAVAILABLE_MARKET_STATES:
        return ListingMarketState.UNAVAILABLE
    return ListingMarketState.UNKNOWN


def _select_candidate(
    candidates: Sequence[_Candidate],
    instrument: Instrument,
    *,
    preferred_epic: str | None = None,
) -> _Candidate:
    expected_type = "CURRENCIES" if instrument.asset_class is AssetClass.FX else "INDICES"
    matches = [
        candidate
        for candidate in candidates
        if candidate.instrument_type.upper() == expected_type
        and candidate.expiry.upper() in _ROLLING_EXPIRIES
        and candidate.market_status.upper() not in _UNAVAILABLE_MARKET_STATES
        and candidate.currency.upper() == instrument.quote_currency.upper()
    ]
    if not matches:
        raise RuntimeError(f"no tradeable rolling IG demo listing for {instrument.instrument_id}")
    if preferred_epic is not None:
        preferred = [candidate for candidate in matches if candidate.epic == preferred_epic]
        if len(preferred) != 1:
            raise RuntimeError(
                f"preferred IG demo listing {preferred_epic} was not returned "
                f"for {instrument.instrument_id}"
            )
        return preferred[0]
    smallest = min(item.minimum_deal_size for item in matches)
    selected = [item for item in matches if item.minimum_deal_size == smallest]
    if len(selected) != 1:
        epics = ", ".join(sorted(item.epic for item in selected))
        raise RuntimeError(f"ambiguous IG demo listings for {instrument.instrument_id}: {epics}")
    return selected[0]


def _historical_rows(value: object) -> list[Mapping[str, object]]:
    return _records(value)


def _historical_query_time(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _historical_time(row: Mapping[str, object]) -> datetime | None:
    raw = row.get("snapshotTimeUTC") or row.get("snapshotTime")
    if raw is None:
        return None
    text = str(raw)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        for pattern in ("%Y/%m/%d %H:%M:%S", "%Y:%m:%d-%H:%M:%S"):
            try:
                return datetime.strptime(text, pattern).replace(tzinfo=UTC)
            except ValueError:
                continue
    return None


def _historical_bars(
    listing: ProviderListing, interval_start: datetime, row: Mapping[str, object]
) -> tuple[MarketBar, ...]:
    values: dict[PriceBasis, tuple[Decimal, Decimal, Decimal, Decimal]] = {}
    for basis, key in ((PriceBasis.BID, "bid"), (PriceBasis.ASK, "ask")):
        points: list[Decimal] = []
        for field in ("openPrice", "highPrice", "lowPrice", "closePrice"):
            price = _decimal_or_none(_mapping(row.get(field)).get(key))
            if price is None:
                break
            points.append(price)
        if len(points) == 4:
            values[basis] = cast(tuple[Decimal, Decimal, Decimal, Decimal], tuple(points))
    if PriceBasis.BID in values and PriceBasis.ASK in values:
        bid = values[PriceBasis.BID]
        ask = values[PriceBasis.ASK]
        values[PriceBasis.MID] = cast(
            tuple[Decimal, Decimal, Decimal, Decimal],
            tuple((left + right) / Decimal(2) for left, right in zip(bid, ask, strict=True)),
        )
    return tuple(
        MarketBar(
            instrument_id=listing.instrument_id,
            basis=basis,
            interval_start=interval_start,
            interval_end=interval_start + timedelta(minutes=1),
            open=ohlc[0],
            high=ohlc[1],
            low=ohlc[2],
            close=ohlc[3],
            sample_count=1,
            revision=1,
            provenance=BarProvenance.IG_HISTORICAL,
            source_listing_id=listing.listing_id,
            quality=DataQuality.HEALTHY,
        )
        for basis, ohlc in values.items()
    )


def _mapping(value: object) -> Mapping[str, object]:
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else {}


def _string(value: Mapping[str, object], key: str) -> str | None:
    item = value.get(key)
    return str(item) if item not in (None, "") else None


def _nested_decimal(value: Mapping[str, object], outer: str, inner: str) -> Decimal | None:
    return _decimal_or_none(_mapping(value.get(outer)).get(inner))


def _decimal_or_none(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    return Decimal(str(value))


def _integer_or_none(value: object) -> int | None:
    if value in (None, ""):
        return None
    return int(str(value))


def _health_time(value: datetime | None) -> str:
    return "none" if value is None else value.isoformat()


def _bounded_economics(metadata: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """Keep only paper-relevant market detail; never persist the raw response."""

    instrument = _mapping(metadata.get("instrument"))
    dealing_rules = _mapping(metadata.get("dealingRules"))
    return {
        "quantity_unit": _string(instrument, "unit"),
        "contract_size": _decimal_text(instrument.get("contractSize")),
        "lot_size": _decimal_text(instrument.get("lotSize")),
        # IG supplies this as a bounded semantic label for some markets (for
        # example, a currency-qualified amount), not consistently as a bare
        # decimal. Preserve the provider meaning without attempting a
        # dimensionally unsafe numeric conversion.
        "one_pip_means": _string(instrument, "onePipMeans"),
        "value_of_one_pip": _decimal_text(instrument.get("valueOfOnePip")),
        "minimum_quantity": _decimal_text(_nested_decimal(dealing_rules, "minDealSize", "value")),
        "price_increment": _decimal_text(instrument.get("scalingFactor")),
    }


def _listing_metadata_version(candidate: _Candidate, economics: Mapping[str, JsonValue]) -> str:
    """Hash stable listing facts, excluding volatile market snapshots."""

    identity: dict[str, JsonValue] = {
        "epic": candidate.epic,
        "name": candidate.name,
        "instrument_type": candidate.instrument_type,
        "expiry": candidate.expiry,
        "currency": candidate.currency,
        "minimum_deal_size": str(candidate.minimum_deal_size),
        "economics": dict(economics),
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()[:16]


def _decimal_text(value: object) -> str | None:
    decimal = _decimal_or_none(value)
    return str(decimal) if decimal is not None else None


def _currency(instrument: Mapping[str, object]) -> str:
    currencies = instrument.get("currencies")
    if isinstance(currencies, Sequence) and currencies:
        first = _mapping(cast(Sequence[object], currencies)[0])
        return _string(first, "code") or _string(first, "name") or ""
    return _string(instrument, "currency") or ""
