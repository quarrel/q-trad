"""Data-only IG demo adapter.

The `trading-ig` package is intentionally contained in this module. No order
methods are exposed.
"""

import asyncio
import hashlib
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

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
_PREFERRED_EPICS = {
    InstrumentId("fx:aud-usd"): "CS.D.AUDUSD.CFD.IP",
    InstrumentId("fx:eur-usd"): "CS.D.EURUSD.CFD.IP",
    InstrumentId("fx:usd-jpy"): "CS.D.USDJPY.CFD.IP",
    InstrumentId("fx:gbp-usd"): "CS.D.GBPUSD.CFD.IP",
    InstrumentId("index:australia-200"): "IX.D.ASX.IFD.IP",
    InstrumentId("index:us-500"): "IX.D.SPTRD.IFD.IP",
    InstrumentId("index:ftse-100"): "IX.D.FTSE.CFD.IP",
}


@dataclass(frozen=True, slots=True)
class IgDemoConfig:
    username: str
    password: str
    api_key: str
    account_id: str | None = None
    queue_capacity: int = 10_000
    connect_attempts: int = 3
    reconnect_attempts: int = 4
    initial_backoff_seconds: float = 5.0
    maximum_backoff_seconds: float = 30.0
    stale_after_seconds: float = 30.0

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
        if self.stale_after_seconds <= 0:
            raise ValueError("stale threshold must be positive")


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
        service_factory: Callable[[IgDemoConfig], Any] | None = None,
    ) -> None:
        self._config = config
        self._clock = clock
        self._sleep = sleep
        self._service_factory = service_factory
        self._service: Any = None
        self._session_details: Mapping[str, Any] | None = None
        self._stream_client: Any = None
        self._stream_account_id: str | None = None
        self._subscriptions: list[Any] = []
        self._queue: asyncio.Queue[MarketDataRecord] = asyncio.Queue(maxsize=config.queue_capacity)
        self._listings_by_epic: dict[str, ProviderListing] = {}
        self._field_state: dict[str, dict[str, str]] = {}
        self._side_times: dict[str, dict[str, datetime]] = {}
        self._last_message_at: datetime | None = None
        self._stream_connected_at: datetime | None = None
        self._status = HealthStatus.STOPPED
        self._loop: asyncio.AbstractEventLoop | None = None
        self._desired_listings: tuple[ProviderListing, ...] = ()
        self._reconnecting = False
        self._reconnect_task: asyncio.Task[None] | None = None
        self._stopping = False
        self._reconnect_count = 0
        self._dropped_records = 0
        self._queue_saturated = False
        self._historical_allowance_remaining: int | None = None

    async def connect(self) -> None:
        if self._status in {
            HealthStatus.STARTING,
            HealthStatus.HEALTHY,
            HealthStatus.DEGRADED,
        }:
            return
        self._stopping = False
        self._status = HealthStatus.STARTING
        self._loop = asyncio.get_running_loop()
        await self._establish_rest_session()

    async def disconnect(self) -> None:
        self._stopping = True
        reconnect_task = self._reconnect_task
        if reconnect_task is not None and reconnect_task is not asyncio.current_task():
            reconnect_task.cancel()
            with suppress(asyncio.CancelledError):
                await reconnect_task
        await self._close_stream()
        await self._logout_rest_session()
        self._desired_listings = ()
        self._reconnecting = False
        self._reconnect_task = None
        self._status = HealthStatus.STOPPED

    async def discover_listings(
        self, instrument_ids: Sequence[InstrumentId]
    ) -> Sequence[ProviderListing]:
        self._require_connected()
        listings: list[ProviderListing] = []
        for instrument_id in instrument_ids:
            instrument = INSTRUMENTS_BY_ID[instrument_id]
            by_epic: dict[str, _Candidate] = {}
            for alias in instrument.search_aliases:
                response = await asyncio.to_thread(self._service.search_markets, alias)
                for search_row in _records(response):
                    epic = _string(search_row, "epic")
                    if (
                        not epic
                        or epic in by_epic
                        or not _search_row_can_match(search_row, instrument)
                    ):
                        continue
                    detail_response = await asyncio.to_thread(
                        self._service.fetch_market_by_epic, epic
                    )
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
        from lightstreamer.client import (
            ClientListener,
            LightstreamerClient,
            Subscription,
            SubscriptionListener,
        )

        adapter = self

        class PriceListener(SubscriptionListener):
            def __init__(self, epic: str) -> None:
                self._epic = epic

            def onItemUpdate(self, update: Any) -> None:
                adapter._on_update(self._epic, update)

            def onSubscriptionError(self, code: int, message: str) -> None:
                adapter._on_subscription_error(self._epic, code)

        class StatusListener(ClientListener):
            def onStatusChange(self, status: str) -> None:
                adapter._on_stream_status(status)

        if self._stream_client is not None:
            raise RuntimeError("refusing to create a concurrent IG stream connection")
        if self._session_details is None:
            raise RuntimeError("IG REST session details are unavailable")
        endpoint = _string(self._session_details, "lightstreamerEndpoint")
        account_id = _string(self._session_details, "currentAccountId") or self._config.account_id
        cst = self._service.session.headers.get("CST")
        security_token = self._service.session.headers.get("X-SECURITY-TOKEN")
        if not endpoint or not account_id or not cst or not security_token:
            raise RuntimeError("IG REST session lacks Lightstreamer connection details")
        self._stream_account_id = account_id
        client = LightstreamerClient(endpoint, None)
        self._stream_client = client
        client.connectionDetails.setUser(account_id)
        client.connectionDetails.setPassword(f"CST-{cst}|XST-{security_token}")
        client.addListener(StatusListener())
        await asyncio.to_thread(client.connect)

        for listing in listings:
            epic = listing.listing_id.external_id
            self._listings_by_epic[epic] = listing
            subscription = Subscription(
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
            )
            subscription.setDataAdapter("Pricing")
            subscription.addListener(PriceListener(epic))
            await asyncio.to_thread(client.subscribe, subscription)
            self._subscriptions.append(subscription)
        self._stream_connected_at = self._clock.now()
        self._status = HealthStatus.HEALTHY

    async def records(self) -> AsyncIterator[MarketDataRecord]:
        self._require_connected()
        while self._status not in {HealthStatus.STOPPED, HealthStatus.DISCONNECTED}:
            try:
                record = await asyncio.wait_for(self._queue.get(), timeout=1)
                if self._queue_saturated:
                    self._queue_saturated = False
                    if not self._reconnecting:
                        self._status = HealthStatus.HEALTHY
                yield record
            except TimeoutError:
                self._check_staleness()

    async def backfill(self, request: BackfillRequest) -> AsyncIterator[MarketBar]:
        self._require_connected()
        response = await asyncio.to_thread(
            self._service.fetch_historical_prices_by_epic_and_date_range,
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
                f"reconnects={self._reconnect_count}; dropped_records={self._dropped_records}"
            ),
        )

    def _on_update(self, epic: str, update: Any) -> None:
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
        self._last_message_at = received
        if self._loop is None:
            raise RuntimeError("IG callback received before event loop registration")
        self._loop.call_soon_threadsafe(self._enqueue_record, record, epic)

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
        if self._status is not HealthStatus.HEALTHY or not self._desired_listings:
            return
        anchor = self._last_message_at or self._stream_connected_at
        if anchor is None:
            return
        if self._clock.now() - anchor <= timedelta(seconds=self._config.stale_after_seconds):
            return
        self._status = HealthStatus.DEGRADED
        LOGGER.warning("ig_stream_stale")
        self._schedule_reconnect()

    def _on_stream_status(self, status: str) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._handle_stream_status, status)

    def _handle_stream_status(self, status: str) -> None:
        normalised = status.upper()
        if normalised.startswith("CONNECTED:"):
            self._status = HealthStatus.HEALTHY
        elif normalised == "DISCONNECTED:WILL-RETRY":
            self._status = HealthStatus.DEGRADED
        elif normalised == "DISCONNECTED" and not self._stopping:
            self._status = HealthStatus.DEGRADED
            self._schedule_reconnect()

    def _on_subscription_error(self, epic: str, code: int) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._handle_subscription_error, epic, code)

    def _handle_subscription_error(self, epic: str, code: int) -> None:
        self._status = HealthStatus.DEGRADED
        LOGGER.error("ig_subscription_error", extra={"epic": epic, "code": code})

    def _schedule_reconnect(self) -> None:
        if self._stopping or self._reconnecting or not self._desired_listings:
            return
        self._reconnecting = True
        task = asyncio.create_task(self._reconnect_stream())
        task.add_done_callback(self._reconnect_done)
        self._reconnect_task = task

    def _reconnect_done(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            LOGGER.error(
                "ig_reconnect_exhausted",
                extra={"error_type": type(error).__name__},
            )

    async def _reconnect_stream(self) -> None:
        last_error: Exception | None = None
        try:
            for attempt in range(1, self._config.reconnect_attempts + 1):
                try:
                    await self._close_stream()
                    await self._logout_rest_session()
                    await self._establish_rest_session(attempts=1)
                    await self._open_stream(self._desired_listings)
                    self._reconnect_count += 1
                    return
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    last_error = error
                    self._status = HealthStatus.DEGRADED
                    if attempt == self._config.reconnect_attempts:
                        break
                    delay = _backoff_seconds(self._config, attempt)
                    LOGGER.warning(
                        "ig_reconnect_retry",
                        extra={
                            "attempt": attempt,
                            "delay_seconds": delay,
                            "error_type": type(error).__name__,
                        },
                    )
                    await self._sleep(delay)
            self._status = HealthStatus.DISCONNECTED
            if last_error is not None:
                raise last_error
        finally:
            self._reconnecting = False
            self._reconnect_task = None

    async def _establish_rest_session(self, *, attempts: int | None = None) -> None:
        maximum_attempts = attempts or self._config.connect_attempts
        last_error: Exception | None = None
        for attempt in range(1, maximum_attempts + 1):
            try:
                self._service = self._new_service()
                session_details = await asyncio.to_thread(self._service.create_session)
                self._session_details = _single_record(session_details)
                self._status = HealthStatus.HEALTHY
                return
            except asyncio.CancelledError:
                raise
            except Exception as error:
                last_error = error
                await self._logout_rest_session()
                if attempt == maximum_attempts:
                    break
                delay = _backoff_seconds(self._config, attempt)
                LOGGER.warning(
                    "ig_connect_retry",
                    extra={
                        "attempt": attempt,
                        "delay_seconds": delay,
                        "error_type": type(error).__name__,
                    },
                )
                await self._sleep(delay)
        self._status = HealthStatus.DISCONNECTED
        if last_error is not None:
            raise last_error
        raise RuntimeError("IG session establishment failed")

    def _new_service(self) -> Any:
        if self._service_factory is not None:
            return self._service_factory(self._config)
        from trading_ig.rest import IGService

        return IGService(
            self._config.username,
            self._config.password,
            self._config.api_key,
            acc_type=self._config.account_type,
            acc_number=self._config.account_id,
            use_rate_limiter=True,
        )

    async def _close_stream(self) -> None:
        client = self._stream_client
        subscriptions = tuple(self._subscriptions)
        self._stream_client = None
        self._stream_account_id = None
        self._stream_connected_at = None
        self._subscriptions.clear()
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

    def _require_connected(self) -> None:
        if self._service is None or self._status not in {
            HealthStatus.HEALTHY,
            HealthStatus.DEGRADED,
        }:
            raise RuntimeError("IG demo adapter is not connected")


def _backoff_seconds(config: IgDemoConfig, failed_attempt: int) -> float:
    return min(
        config.initial_backoff_seconds * (2 ** (failed_attempt - 1)),
        config.maximum_backoff_seconds,
    )


def _records(value: Any) -> list[Mapping[str, Any]]:
    if value is None:
        return []
    if hasattr(value, "to_dict"):
        try:
            converted = value.to_dict(orient="records")
        except TypeError:
            converted = value.to_dict()
        return _records(converted)
    if isinstance(value, Mapping):
        for key in ("markets", "prices"):
            nested = value.get(key)
            if isinstance(nested, Sequence) and not isinstance(nested, (str, bytes)):
                return [cast(Mapping[str, Any], item) for item in nested]
        return [cast(Mapping[str, Any], value)]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [cast(Mapping[str, Any], item) for item in value]
    raise TypeError(f"unsupported IG response type: {type(value).__name__}")


def _single_record(value: Any) -> Mapping[str, Any]:
    records = _records(value)
    if len(records) != 1:
        raise ValueError(f"expected one IG record, received {len(records)}")
    return records[0]


def _candidate(search_row: Mapping[str, Any], detail: Mapping[str, Any]) -> _Candidate | None:
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


def _search_row_can_match(search_row: Mapping[str, Any], instrument: Instrument) -> bool:
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


def _historical_rows(value: Any) -> list[Mapping[str, Any]]:
    return _records(value)


def _historical_query_time(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _historical_time(row: Mapping[str, Any]) -> datetime | None:
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
    listing: ProviderListing, interval_start: datetime, row: Mapping[str, Any]
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


def _mapping(value: Any) -> Mapping[str, Any]:
    return cast(Mapping[str, Any], value) if isinstance(value, Mapping) else {}


def _string(value: Mapping[str, Any], key: str) -> str | None:
    item = value.get(key)
    return str(item) if item not in (None, "") else None


def _nested_decimal(value: Mapping[str, Any], outer: str, inner: str) -> Decimal | None:
    return _decimal_or_none(_mapping(value.get(outer)).get(inner))


def _decimal_or_none(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    return Decimal(str(value))


def _integer_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(str(value))


def _currency(instrument: Mapping[str, Any]) -> str:
    currencies = instrument.get("currencies")
    if isinstance(currencies, Sequence) and currencies:
        first = _mapping(currencies[0])
        return _string(first, "code") or _string(first, "name") or ""
    return _string(instrument, "currency") or ""
