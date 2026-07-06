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
from typing import Protocol, cast, runtime_checkable

from qtrad.adapters.ig.lightstreamer_compat import install_lightstreamer_compatibility
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
from qtrad.ports.market_data import BackfillRequest, MarketDataRecord

LOGGER = logging.getLogger(__name__)
_ROLLING_EXPIRIES = {"-", "DFB", "DAILY", "CASH", "ROLLING"}
_PROVIDER_ERROR_CODE = re.compile(r"\b(error\.[a-z0-9._-]+|endpoint\.[a-z0-9._-]+)\b")
_FATAL_PROVIDER_ERRORS = {
    "endpoint.unavailable.for.api-key",
    "error.security.api-key-disabled",
    "error.security.api-key-invalid",
    "error.security.api-key-restricted",
    "error.security.api-key-revoked",
    "error.security.too-many-failed-attempts",
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
    def getValue(self, field: str, /) -> object | None: ...


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
    maximum_reconnect_cycles: int | None = None
    stale_after_seconds: float = 30.0
    readiness_timeout_seconds: float = 60.0
    retry_watchdog_seconds: float = 60.0
    shutdown_timeout_seconds: float = 10.0

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
        if self.maximum_reconnect_cycles is not None and self.maximum_reconnect_cycles <= 0:
            raise ValueError("maximum reconnect cycles must be positive")
        if (
            self.stale_after_seconds <= 0
            or self.readiness_timeout_seconds <= 0
            or self.retry_watchdog_seconds <= 0
            or self.shutdown_timeout_seconds <= 0
        ):
            raise ValueError("lifecycle timeouts must be positive")


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
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[float, float], float] = random.uniform,
        service_factory: Callable[[IgDemoConfig], object] | None = None,
    ) -> None:
        self._config = config
        self._clock = clock
        self._sleep = sleep
        self._jitter = jitter
        self._service_factory = service_factory
        self._service: _IgRestService | None = None
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
        self._queue_saturated = False
        self._historical_allowance_remaining: int | None = None
        self._generation = 0
        self._expected_epics: set[str] = set()
        self._subscribed_epics: set[str] = set()
        self._updated_epics: set[str] = set()
        self._transport_connected = False
        self._ready_event = asyncio.Event()
        self._readiness_error: Exception | None = None
        self._fatal_error: Exception | None = None

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
            await self._close_stream()
        except Exception as error:
            close_error = error
        try:
            await self._logout_rest_session()
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
        service = self._require_connected()
        listings: list[ProviderListing] = []
        for instrument_id in instrument_ids:
            instrument = INSTRUMENTS_BY_ID[instrument_id]
            by_epic: dict[str, _Candidate] = {}
            for alias in instrument.search_aliases:
                response = await asyncio.to_thread(service.search_markets, alias)
                for search_row in _records(response):
                    epic = _string(search_row, "epic")
                    if (
                        not epic
                        or epic in by_epic
                        or not _search_row_can_match(search_row, instrument)
                    ):
                        continue
                    detail_response = await asyncio.to_thread(service.fetch_market_by_epic, epic)
                    detail = _single_record(detail_response)
                    candidate = _candidate(search_row, detail)
                    if candidate is not None:
                        by_epic[epic] = candidate

            candidate = _select_candidate(
                tuple(by_epic.values()),
                instrument,
                preferred_epic=_PREFERRED_EPICS[instrument_id],
            )
            metadata_json = json.dumps(candidate.metadata, sort_keys=True, separators=(",", ":"))
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
                metadata_version=hashlib.sha256(metadata_json.encode()).hexdigest()[:16],
            )
            listings.append(listing)
        return tuple(listings)

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

            def onSubscriptionError(self, code: int, message: str) -> None:
                adapter._on_subscription_error(self._epic, code, generation)

        class StatusListener(ClientListener):
            def onStatusChange(self, status: str) -> None:
                adapter._on_stream_status(status, generation)

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
        await asyncio.to_thread(client.connect)

        self._connection_state = _ConnectionState.SUBSCRIBING
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
            await asyncio.to_thread(client.subscribe, subscription)
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
                if self._queue_saturated:
                    self._queue_saturated = False
                    if not self._reconnecting and self._connection_state is _ConnectionState.READY:
                        self._status = HealthStatus.HEALTHY
                yield record
            except TimeoutError:
                self._check_staleness()
        if self._fatal_error is not None:
            raise self._fatal_error

    async def backfill(self, request: BackfillRequest) -> AsyncIterator[MarketBar]:
        service = self._require_connected()
        response = await asyncio.to_thread(
            service.fetch_historical_prices_by_epic_and_date_range,
            request.listing.listing_id.external_id,
            "MINUTE",
            _historical_query_time(request.start),
            _historical_query_time(request.end),
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
                f"reconnects={self._reconnect_count}; dropped_records={self._dropped_records}"
            ),
        )

    def _on_update(self, epic: str, update: _ItemUpdate, *, generation: int | None = None) -> None:
        observed_generation = self._generation if generation is None else generation
        if observed_generation != self._generation:
            return
        received = self._clock.now()
        raw = {
            field: update.getValue(field)
            for field in (
                "TIMESTAMP",
                "BIDPRICE1",
                "ASKPRICE1",
                "BIDSIZE1",
                "ASKSIZE1",
                "DLG_FLAG",
                "DELAY",
            )
            if update.getValue(field) is not None
        }
        state = self._field_state.setdefault(epic, {})
        state.update({key: str(value) for key, value in raw.items()})
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
                side_times["BID"] = event_time
            if "ASKPRICE1" in raw:
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
            quote=quote,
            error_code=error_code,
            error_detail=error_detail,
        )
        if self._loop is None:
            raise RuntimeError("IG callback received before event loop registration")
        self._loop.call_soon_threadsafe(
            self._accept_update,
            record,
            epic,
            observed_generation,
        )

    def _accept_update(self, record: MarketDataRecord, epic: str, generation: int) -> None:
        if generation != self._generation or self._stopping:
            return
        self._last_message_at = record.received_time
        if record.quote is not None and record.quote.quality is DataQuality.HEALTHY:
            self._updated_epics.add(epic)
        self._mark_ready_if_complete(generation)
        self._enqueue_record(record, epic)

    def _enqueue_record(self, record: MarketDataRecord, epic: str) -> None:
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            self._dropped_records += 1
            self._queue_saturated = True
            self._status = HealthStatus.DEGRADED
            LOGGER.error(
                "ig_queue_saturated",
                extra={"epic": epic, "dropped_records": self._dropped_records},
            )

    def _check_staleness(self) -> None:
        if self._stopping or self._reconnecting or not self._desired_listings:
            return
        anchor = self._last_message_at or self._stream_connected_at
        if anchor is None:
            return
        if self._clock.now() - anchor <= timedelta(seconds=self._config.stale_after_seconds):
            return
        self._status = HealthStatus.DEGRADED
        self._connection_state = _ConnectionState.DEGRADED
        LOGGER.warning("ig_stream_stale")
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
        LOGGER.info(
            "ig_stream_status",
            extra={"generation": observed_generation, "status": normalised[:64]},
        )
        if normalised.startswith("CONNECTED:"):
            self._transport_connected = True
            self._mark_ready_if_complete(observed_generation)
        elif normalised == "DISCONNECTED:WILL-RETRY":
            self._transport_connected = False
            self._status = HealthStatus.DEGRADED
            self._connection_state = _ConnectionState.DEGRADED
            self._start_retry_watchdog(observed_generation)
        elif normalised == "DISCONNECTED" and not self._stopping:
            self._transport_connected = False
            self._status = HealthStatus.DEGRADED
            self._connection_state = _ConnectionState.DEGRADED
            self._schedule_reconnect()

    def _on_subscription(self, epic: str, generation: int) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._handle_subscription, epic, generation)

    def _handle_subscription(self, epic: str, generation: int) -> None:
        if generation != self._generation or self._stopping:
            return
        self._subscribed_epics.add(epic)
        self._mark_ready_if_complete(generation)

    def _on_unsubscription(self, epic: str, generation: int) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._handle_unsubscription, epic, generation)

    def _handle_unsubscription(self, epic: str, generation: int) -> None:
        if generation != self._generation:
            return
        self._subscribed_epics.discard(epic)
        if not self._stopping:
            self._status = HealthStatus.DEGRADED
            self._connection_state = _ConnectionState.DEGRADED

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

    def _mark_ready_if_complete(self, generation: int) -> None:
        if generation != self._generation or self._stopping:
            return
        if (
            not self._transport_connected
            or self._subscribed_epics != self._expected_epics
            or self._updated_epics != self._expected_epics
        ):
            return
        watchdog_task = self._retry_watchdog_task
        if watchdog_task is not None and watchdog_task is not asyncio.current_task():
            watchdog_task.cancel()
        self._retry_watchdog_task = None
        self._connection_state = _ConnectionState.READY
        self._status = HealthStatus.HEALTHY
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
                if (
                    self._config.maximum_reconnect_cycles is not None
                    and cycle >= self._config.maximum_reconnect_cycles
                ):
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
                session_details = await asyncio.to_thread(service.create_session)
                self._session_details = _single_record(session_details)
                self._status = HealthStatus.STARTING
                self._connection_state = _ConnectionState.AUTHENTICATED
                return
            except asyncio.CancelledError:
                raise
            except Exception as error:
                last_error = error
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

        return cast(
            _IgRestService,
            IGService(
                self._config.username,
                self._config.password,
                self._config.api_key,
                acc_type=self._config.account_type,
                acc_number=self._config.account_id,
                use_rate_limiter=True,
            ),
        )

    async def _close_stream(self) -> None:
        client = self._stream_client
        subscriptions = tuple(self._subscriptions)
        if client is not None:
            self._generation += 1
        self._stream_client = None
        self._stream_account_id = None
        self._stream_connected_at = None
        self._last_message_at = None
        self._transport_connected = False
        self._expected_epics.clear()
        self._subscribed_epics.clear()
        self._updated_epics.clear()
        self._ready_event = asyncio.Event()
        self._readiness_error = None
        self._subscriptions.clear()
        watchdog_task = self._retry_watchdog_task
        if watchdog_task is not None and watchdog_task is not asyncio.current_task():
            watchdog_task.cancel()
            with suppress(asyncio.CancelledError):
                await watchdog_task
        self._retry_watchdog_task = None
        if client is None:
            return
        for subscription in subscriptions:
            try:
                await asyncio.to_thread(client.unsubscribe, subscription)
            except Exception:
                LOGGER.warning("ig_unsubscribe_failed")
        try:
            await asyncio.to_thread(client.disconnect)
        except Exception:
            LOGGER.warning("ig_stream_disconnect_failed")
            raise
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._config.shutdown_timeout_seconds
        while loop.time() < deadline:
            status = await asyncio.to_thread(client.getStatus)
            if status.upper() == "DISCONNECTED":
                return
            await asyncio.sleep(0.05)
        raise TimeoutError("IG Lightstreamer client did not confirm disconnect")

    async def _logout_rest_session(self) -> None:
        service = self._service
        self._service = None
        self._session_details = None
        if service is None:
            return
        try:
            await asyncio.to_thread(service.logout)
        except Exception:
            LOGGER.warning("ig_logout_failed")

    def _require_connected(self) -> _IgRestService:
        if self._service is None or self._connection_state in {
            _ConnectionState.STOPPED,
            _ConnectionState.FAILED,
            _ConnectionState.STOPPING,
        }:
            raise RuntimeError("IG demo adapter is not connected")
        return self._service


def _backoff_seconds(config: IgDemoConfig, failed_attempt: int) -> float:
    return min(
        config.initial_backoff_seconds * (2 ** (failed_attempt - 1)),
        config.maximum_backoff_seconds,
    )


def _safe_error_code(error: BaseException) -> str:
    if isinstance(error, TimeoutError):
        return "TIMEOUT"
    if isinstance(error, ConnectionError):
        return "CONNECTION_ERROR"
    match = _PROVIDER_ERROR_CODE.search(str(error).lower())
    if match is not None:
        return match.group(1)[:96]
    return type(error).__name__.upper()[:96]


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
    minimum = _nested_decimal(dealing_rules, "minDealSize", "value") or Decimal("1")
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
        and market_status not in {"CLOSED", "OFFLINE", "EDITS_ONLY"}
    )


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
        and candidate.market_status.upper() not in {"CLOSED", "OFFLINE", "EDITS_ONLY"}
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


def _currency(instrument: Mapping[str, object]) -> str:
    currencies = instrument.get("currencies")
    if isinstance(currencies, Sequence) and currencies:
        first = _mapping(cast(Sequence[object], currencies)[0])
        return _string(first, "code") or _string(first, "name") or ""
    return _string(instrument, "currency") or ""
