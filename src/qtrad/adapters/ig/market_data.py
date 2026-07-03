"""Data-only IG demo adapter.

The `trading-ig` package is intentionally contained in this module. No order
methods are exposed.
"""

import asyncio
import hashlib
import json
import logging
from collections.abc import AsyncIterator, Mapping, Sequence
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


@dataclass(frozen=True, slots=True)
class IgDemoConfig:
    username: str
    password: str
    api_key: str
    account_id: str | None = None
    queue_capacity: int = 10_000

    @property
    def account_type(self) -> str:
        return "DEMO"


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

    def __init__(self, config: IgDemoConfig, clock: Clock) -> None:
        self._config = config
        self._clock = clock
        self._service: Any = None
        self._stream_service: Any = None
        self._subscriptions: list[Any] = []
        self._queue: asyncio.Queue[MarketDataRecord] = asyncio.Queue(
            maxsize=config.queue_capacity
        )
        self._listings_by_epic: dict[str, ProviderListing] = {}
        self._field_state: dict[str, dict[str, str]] = {}
        self._side_times: dict[str, dict[str, datetime]] = {}
        self._last_message_at: datetime | None = None
        self._status = HealthStatus.STOPPED
        self._loop: asyncio.AbstractEventLoop | None = None

    async def connect(self) -> None:
        if self._status is not HealthStatus.STOPPED:
            return
        self._status = HealthStatus.STARTING
        self._loop = asyncio.get_running_loop()
        try:
            from trading_ig.rest import IGService

            self._service = IGService(
                self._config.username,
                self._config.password,
                self._config.api_key,
                acc_type=self._config.account_type,
                acc_number=self._config.account_id,
                use_rate_limiter=True,
            )
            await asyncio.to_thread(self._service.create_session)
            self._status = HealthStatus.HEALTHY
        except Exception:
            self._status = HealthStatus.DISCONNECTED
            raise

    async def disconnect(self) -> None:
        if self._stream_service is not None:
            client = getattr(self._stream_service, "ls_client", None)
            if client is not None:
                await asyncio.to_thread(client.disconnect)
        if self._service is not None:
            try:
                await asyncio.to_thread(self._service.logout)
            except Exception:
                LOGGER.warning("ig_logout_failed", exc_info=True)
        self._subscriptions.clear()
        self._service = None
        self._stream_service = None
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
                    if not epic or epic in by_epic:
                        continue
                    detail_response = await asyncio.to_thread(
                        self._service.fetch_market_by_epic, epic
                    )
                    detail = _single_record(detail_response)
                    candidate = _candidate(search_row, detail)
                    if candidate is not None:
                        by_epic[epic] = candidate

            candidate = _select_candidate(tuple(by_epic.values()), instrument)
            metadata_json = json.dumps(
                candidate.metadata, sort_keys=True, separators=(",", ":")
            )
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
        from trading_ig.lightstreamer import Subscription
        from trading_ig.stream import IGStreamService

        if self._stream_service is None:
            self._stream_service = IGStreamService(self._service)
            await asyncio.to_thread(self._stream_service.create_session)

        for listing in listings:
            epic = listing.listing_id.external_id
            self._listings_by_epic[epic] = listing
            subscription = Subscription(
                mode="MERGE",
                items=[f"MARKET:{epic}"],
                fields=["UTM", "BID", "OFFER", "MARKET_STATE"],
            )
            subscription.addlistener(_Listener(self, epic))
            await asyncio.to_thread(self._stream_service.ls_client.subscribe, subscription)
            self._subscriptions.append(subscription)

    async def records(self) -> AsyncIterator[MarketDataRecord]:
        self._require_connected()
        while self._status not in {HealthStatus.STOPPED, HealthStatus.DISCONNECTED}:
            yield await self._queue.get()

    async def backfill(self, request: BackfillRequest) -> AsyncIterator[MarketBar]:
        self._require_connected()
        response = await asyncio.to_thread(
            self._service.fetch_historical_prices_by_epic_and_date_range,
            request.listing.listing_id.external_id,
            "MINUTE",
            request.start.strftime("%Y-%m-%dT%H:%M:%S"),
            request.end.strftime("%Y-%m-%dT%H:%M:%S"),
        )
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

    async def health(self) -> AdapterHealth:
        status = self._status
        now = self._clock.now()
        if (
            status is HealthStatus.HEALTHY
            and self._last_message_at is not None
            and now - self._last_message_at > timedelta(seconds=30)
        ):
            status = HealthStatus.DEGRADED
        return AdapterHealth(
            adapter_name="ig-market-data",
            environment=BrokerEnvironment.IG_DEMO,
            status=status,
            observed_at=now,
            last_message_at=self._last_message_at,
            detail="data only; production and order surfaces are unavailable",
        )

    def _on_update(self, epic: str, update: Any) -> None:
        received = self._clock.now()
        raw = {
            field: update.getValue(field)
            for field in ("UTM", "BID", "OFFER", "MARKET_STATE")
            if update.getValue(field) is not None
        }
        state = self._field_state.setdefault(epic, {})
        state.update({key: str(value) for key, value in raw.items()})
        utm = state.get("UTM")
        digest = hashlib.sha256(
            json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:16]
        deduplication_key = f"{epic}:{utm or 'missing'}:{digest}"
        quote: MarketQuote | None = None
        error_code: str | None = None
        error_detail: str | None = None
        try:
            if utm is None:
                raise ValueError("IG streaming update has no UTM timestamp")
            event_time = datetime.fromtimestamp(int(utm) / 1000, tz=UTC)
            side_times = self._side_times.setdefault(epic, {})
            if "BID" in raw:
                side_times["BID"] = event_time
            if "OFFER" in raw:
                side_times["OFFER"] = event_time
            bid = _decimal_or_none(state.get("BID"))
            ask = _decimal_or_none(state.get("OFFER"))
            listing = self._listings_by_epic[epic]
            market_state = state.get("MARKET_STATE", "")
            quality = (
                DataQuality.HEALTHY
                if market_state.upper() in {"TRADEABLE", "OPEN"}
                else DataQuality.PARTIAL
            )
            quote = MarketQuote(
                instrument_id=listing.instrument_id,
                listing_id=listing.listing_id,
                event_time=event_time,
                received_time=received,
                bid=bid,
                ask=ask,
                bid_time=side_times.get("BID"),
                ask_time=side_times.get("OFFER"),
                quality=quality,
                source_sequence=utm,
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
            subscription=f"MARKET:{epic}",
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
        future = asyncio.run_coroutine_threadsafe(self._queue.put(record), self._loop)
        try:
            future.result(timeout=5)
        except TimeoutError:
            self._status = HealthStatus.DEGRADED
            LOGGER.error("ig_queue_blocked", extra={"epic": epic})

    def _require_connected(self) -> None:
        if self._service is None or self._status is not HealthStatus.HEALTHY:
            raise RuntimeError("IG demo adapter is not connected")


class _Listener:
    def __init__(self, adapter: IgDemoMarketDataAdapter, epic: str) -> None:
        self._adapter = adapter
        self._epic = epic

    def onItemUpdate(self, update: Any) -> None:
        self._adapter._on_update(self._epic, update)


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


def _candidate(
    search_row: Mapping[str, Any], detail: Mapping[str, Any]
) -> _Candidate | None:
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
        name=(
            _string(search_row, "instrumentName")
            or _string(instrument, "name")
            or epic
        ),
        instrument_type=(
            _string(search_row, "instrumentType")
            or _string(instrument, "type")
            or ""
        ),
        expiry=_string(search_row, "expiry") or _string(instrument, "expiry") or "",
        market_status=(
            _string(search_row, "marketStatus")
            or _string(snapshot, "marketStatus")
            or ""
        ),
        currency=currency,
        minimum_deal_size=minimum,
        metadata=metadata,
    )


def _select_candidate(
    candidates: Sequence[_Candidate], instrument: Instrument
) -> _Candidate:
    expected_type = "CURRENCIES" if instrument.asset_class is AssetClass.FX else "INDICES"
    matches = [
        candidate
        for candidate in candidates
        if candidate.instrument_type.upper() == expected_type
        and candidate.expiry.upper() in _ROLLING_EXPIRIES
        and candidate.market_status.upper()
        not in {"CLOSED", "OFFLINE", "EDITS_ONLY"}
    ]
    if not matches:
        raise RuntimeError(
            f"no tradeable rolling IG demo listing for {instrument.instrument_id}"
        )
    smallest = min(item.minimum_deal_size for item in matches)
    selected = [item for item in matches if item.minimum_deal_size == smallest]
    if len(selected) != 1:
        epics = ", ".join(sorted(item.epic for item in selected))
        raise RuntimeError(
            f"ambiguous IG demo listings for {instrument.instrument_id}: {epics}"
        )
    return selected[0]


def _historical_rows(value: Any) -> list[Mapping[str, Any]]:
    return _records(value)


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


def _currency(instrument: Mapping[str, Any]) -> str:
    currencies = instrument.get("currencies")
    if isinstance(currencies, Sequence) and currencies:
        first = _mapping(currencies[0])
        return _string(first, "code") or _string(first, "name") or ""
    return _string(instrument, "currency") or ""
