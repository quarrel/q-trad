"""Official TWS API transport used only by the bounded Stage 1 capability probe."""

import asyncio
import hashlib
import importlib
import importlib.metadata
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from math import isfinite
from queue import Empty, Full, Queue
from threading import Thread
from time import monotonic
from typing import Any, cast

from qtrad.domain.identifiers import InstrumentId
from qtrad.ports.ibkr_capability import (
    IbkrCandidateCapability,
    IbkrCapabilityAdapter,
    IbkrContractEvidence,
    IbkrContractQuery,
    IbkrRequestEvidence,
)

_BID = 1
_ASK = 2
_BID_SIZE = 0
_ASK_SIZE = 3
_DELAYED_BID = 66
_DELAYED_ASK = 67
_DELAYED_BID_SIZE = 69
_DELAYED_ASK_SIZE = 70
_MAX_QUERIES = 24
_MAX_CONTRACTS_PER_QUERY = 1
_HISTORICAL_REQUESTS_PER_CONTRACT = 8
_MAX_HISTORICAL_REQUESTS_PER_RUN = 192
_HISTORICAL_REQUEST_INTERVAL_SECONDS = 12.5
_PINNED_API_VERSION = "10.33.1"
_MARKET_DATA_TYPES = {
    "1": "LIVE",
    "2": "FROZEN",
    "3": "DELAYED",
    "4": "DELAYED_FROZEN",
}


@dataclass(frozen=True, slots=True)
class IbkrGatewayEndpoint:
    host: str
    port: int
    client_id: int


@dataclass(frozen=True, slots=True)
class IbkrApiIdentity:
    """Controlled official API distribution identity required before socket construction."""

    package_fingerprint: str
    version: str = _PINNED_API_VERSION

    def __post_init__(self) -> None:
        if self.version != _PINNED_API_VERSION:
            raise ValueError(f"IBKR capability adapter requires TWS API {_PINNED_API_VERSION}")
        if len(self.package_fingerprint) != 64 or any(
            character not in "0123456789abcdef" for character in self.package_fingerprint
        ):
            raise ValueError("IBKR API package fingerprint must be a lowercase SHA-256 digest")


@dataclass(frozen=True, slots=True)
class _Callback:
    kind: str
    request_id: int
    values: tuple[object, ...]


class IbkrConnectionIntegrityError(RuntimeError):
    """The Gateway session can no longer support trustworthy request evidence."""


class IbkrRequestTimeout(TimeoutError):
    """A bounded request timed out after retaining callbacks already observed for it."""

    def __init__(self, callbacks: Sequence[_Callback]) -> None:
        super().__init__("IBKR capability request timed out")
        self.callbacks = tuple(callbacks)


class OfficialIbkrCapabilityAdapter(IbkrCapabilityAdapter):
    """Direct API client with bounded requests and no account or order surface."""

    def __init__(
        self,
        endpoint: IbkrGatewayEndpoint,
        *,
        request_timeout_seconds: float = 10.0,
        client_factory: Callable[[Queue[_Callback]], Any] | None = None,
        api_identity: IbkrApiIdentity | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if request_timeout_seconds <= 0:
            raise ValueError("IBKR capability request timeout must be positive")
        self._endpoint = endpoint
        self._request_timeout_seconds = request_timeout_seconds
        self._callbacks: Queue[_Callback] = Queue(maxsize=2_000)
        if client_factory is None and api_identity is None:
            raise ValueError("IBKR capability adapter requires a verified official API identity")
        self._client_factory = client_factory or (
            lambda callbacks: _official_client(callbacks, cast(IbkrApiIdentity, api_identity))
        )
        self._sleep = sleep
        self._last_historical_request_at: float | None = None
        self._client: Any | None = None
        self._thread: Thread | None = None
        self._next_request_id = 1

    async def connect(self) -> None:
        if self._client is not None:
            raise RuntimeError("IBKR capability adapter is already connected")
        client = self._client_factory(self._callbacks)
        client.connect(self._endpoint.host, self._endpoint.port, clientId=self._endpoint.client_id)
        self._client = client
        self._thread = Thread(target=client.run, name="ibkr-capability-reader", daemon=True)
        self._thread.start()
        await self._wait_for(-1, {"next_valid_id"})
        client.reqCurrentTime()
        await self._wait_for(-1, {"current_time"})

    async def disconnect(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            client.disconnect()
        thread = self._thread
        self._thread = None
        if thread is not None:
            await asyncio.to_thread(thread.join, self._request_timeout_seconds)
            if thread.is_alive():
                raise RuntimeError("IBKR capability reader did not stop after disconnect")

    async def probe(
        self, queries: Sequence[IbkrContractQuery]
    ) -> Sequence[IbkrCandidateCapability]:
        if self._client is None:
            raise RuntimeError("IBKR capability adapter is not connected")
        if not queries or len(queries) > _MAX_QUERIES:
            raise ValueError(
                f"IBKR capability probe requires between one and {_MAX_QUERIES} queries"
            )
        if (
            len(queries) * _MAX_CONTRACTS_PER_QUERY * _HISTORICAL_REQUESTS_PER_CONTRACT
            > _MAX_HISTORICAL_REQUESTS_PER_RUN
        ):
            raise ValueError("IBKR capability probe exceeds the historical request budget")
        results: list[IbkrCandidateCapability] = []
        for query in queries:
            results.append(await self._probe_query(query))
        return tuple(results)

    async def _probe_query(self, query: IbkrContractQuery) -> IbkrCandidateCapability:
        client = self._require_client()
        request_id = self._request_id()
        started = monotonic()
        client.reqContractDetails(request_id, _contract(query))
        try:
            callbacks = await self._collect_until(request_id, "contract_details_end")
        except IbkrRequestTimeout as error:
            return IbkrCandidateCapability(
                query=query,
                contracts=(),
                requests=(
                    IbkrRequestEvidence(
                        kind="CONTRACT_DETAILS",
                        status="TIMEOUT",
                        latency_milliseconds=_milliseconds(started),
                        error_codes=_error_codes(error.callbacks),
                        error_times=_error_times(error.callbacks),
                        returned_contract_count=sum(
                            callback.kind == "contract_details" for callback in error.callbacks
                        ),
                    ),
                ),
            )
        error_codes = _error_codes(callbacks)
        returned_callbacks = tuple(
            callback for callback in callbacks if callback.kind == "contract_details"
        )
        returned_count = len(returned_callbacks)
        contract_status = _status(callbacks, "contract_details_end")
        exact_match = contract_status == "SUCCESS" and returned_count == 1
        contracts = (_contract_evidence(returned_callbacks[0].values[0]),) if exact_match else ()
        contract_request = IbkrRequestEvidence(
            kind="CONTRACT_DETAILS",
            status=(
                contract_status
                if contract_status != "SUCCESS"
                else "AMBIGUOUS"
                if returned_count > 1
                else "SUCCESS"
            ),
            latency_milliseconds=_milliseconds(started),
            error_codes=error_codes,
            error_times=_error_times(callbacks),
            returned_contract_count=returned_count,
        )
        requests: list[IbkrRequestEvidence] = [contract_request]
        for contract in contracts:
            requests.extend(await self._probe_contract(contract))
        return IbkrCandidateCapability(query=query, contracts=contracts, requests=tuple(requests))

    async def _probe_contract(
        self, contract: IbkrContractEvidence
    ) -> tuple[IbkrRequestEvidence, ...]:
        api_contract = _contract_from_evidence(contract)
        market = await self._market_evidence(api_contract, contract.con_id, 1, "LIVE_TOP_OF_BOOK")
        delayed = await self._market_evidence(
            api_contract, contract.con_id, 3, "DELAYED_ENABLED_TOP_OF_BOOK"
        )
        historical: list[IbkrRequestEvidence] = []
        for value in ("MIDPOINT", "BID", "ASK"):
            for use_rth in (False, True):
                historical.append(
                    await self._historical_evidence(api_contract, contract.con_id, value, use_rth)
                )
        one_second = await self._one_second_evidence(api_contract, contract.con_id)
        earliest = await self._earliest_evidence(api_contract, contract.con_id)
        return (market, delayed, *historical, one_second, earliest)

    async def _market_evidence(
        self, api_contract: object, con_id: int, requested_type: int, kind: str
    ) -> IbkrRequestEvidence:
        client = self._require_client()
        market_request_id = self._request_id()
        started = monotonic()
        client.reqMarketDataType(requested_type)
        client.reqMktData(market_request_id, api_contract, "", False, False, [])
        try:
            callbacks = await self._collect_for(market_request_id, self._request_timeout_seconds)
        finally:
            client.cancelMktData(market_request_id)
        data_types = [
            str(callback.values[0]) for callback in callbacks if callback.kind == "market_data_type"
        ]
        data_type = _MARKET_DATA_TYPES.get(data_types[-1]) if data_types else None
        tick_family = _tick_family(data_type)
        bid_types, ask_types, bid_size_types, ask_size_types = tick_family
        tick_types = _tick_types(callbacks)
        bid_seen = bool(bid_types & tick_types)
        ask_seen = bool(ask_types & tick_types)
        bid_usable = _usable_price_seen(callbacks, bid_types)
        ask_usable = _usable_price_seen(callbacks, ask_types)
        return IbkrRequestEvidence(
            kind=kind,
            status=_window_status(callbacks),
            latency_milliseconds=_milliseconds(started),
            contract_con_id=con_id,
            market_data_type=data_type,
            availability=_market_availability(callbacks, data_type, bid_usable, ask_usable),
            bid_seen=bid_seen,
            ask_seen=ask_seen,
            bid_usable=bid_usable,
            ask_usable=ask_usable,
            bid_size_seen=bool(bid_size_types & tick_types),
            ask_size_seen=bool(ask_size_types & tick_types),
            error_codes=_error_codes(callbacks),
            error_times=_error_times(callbacks),
        )

    async def _historical_evidence(
        self, contract: object, con_id: int, what_to_show: str, use_rth: bool
    ) -> IbkrRequestEvidence:
        client = self._require_client()
        request_id = self._request_id()
        await self._pace_historical_request()
        started = monotonic()
        client.reqHistoricalData(
            request_id, contract, "", "120 S", "1 min", what_to_show, int(use_rth), 2, False, []
        )
        try:
            callbacks = await self._collect_until(request_id, "historical_data_end")
        except IbkrRequestTimeout as error:
            client.cancelHistoricalData(request_id)
            return IbkrRequestEvidence(
                kind=f"ONE_MINUTE_{what_to_show}_{'RTH' if use_rth else 'ALL'}",
                status="TIMEOUT",
                latency_milliseconds=_milliseconds(started),
                contract_con_id=con_id,
                use_rth=use_rth,
                error_codes=_error_codes(error.callbacks),
                error_times=_error_times(error.callbacks),
            )
        return IbkrRequestEvidence(
            kind=f"ONE_MINUTE_{what_to_show}_{'RTH' if use_rth else 'ALL'}",
            status=_status(callbacks, "historical_data_end"),
            latency_milliseconds=_milliseconds(started),
            contract_con_id=con_id,
            row_count=sum(callback.kind == "historical_data" for callback in callbacks),
            use_rth=use_rth,
            error_codes=_error_codes(callbacks),
            error_times=_error_times(callbacks),
        )

    async def _one_second_evidence(self, contract: object, con_id: int) -> IbkrRequestEvidence:
        client = self._require_client()
        request_id = self._request_id()
        await self._pace_historical_request()
        started = monotonic()
        client.reqHistoricalData(
            request_id,
            contract,
            "",
            "60 S",
            "1 secs",
            "MIDPOINT",
            0,
            2,
            False,
            [],
        )
        try:
            callbacks = await self._collect_until(request_id, "historical_data_end")
        except IbkrRequestTimeout as error:
            client.cancelHistoricalData(request_id)
            return IbkrRequestEvidence(
                kind="ONE_SECOND_MIDPOINT_ALL",
                status="TIMEOUT",
                latency_milliseconds=_milliseconds(started),
                contract_con_id=con_id,
                use_rth=False,
                error_codes=_error_codes(error.callbacks),
                error_times=_error_times(error.callbacks),
            )
        return IbkrRequestEvidence(
            kind="ONE_SECOND_MIDPOINT_ALL",
            status=_status(callbacks, "historical_data_end"),
            latency_milliseconds=_milliseconds(started),
            contract_con_id=con_id,
            row_count=sum(callback.kind == "historical_data" for callback in callbacks),
            use_rth=False,
            error_codes=_error_codes(callbacks),
            error_times=_error_times(callbacks),
        )

    async def _earliest_evidence(self, contract: object, con_id: int) -> IbkrRequestEvidence:
        client = self._require_client()
        request_id = self._request_id()
        await self._pace_historical_request()
        started = monotonic()
        client.reqHeadTimeStamp(request_id, contract, "MIDPOINT", 0, 2)
        try:
            callbacks = await self._collect_until(request_id, "head_timestamp")
        except IbkrRequestTimeout as error:
            client.cancelHeadTimeStamp(request_id)
            return IbkrRequestEvidence(
                kind="EARLIEST_MIDPOINT",
                status="TIMEOUT",
                latency_milliseconds=_milliseconds(started),
                contract_con_id=con_id,
                error_codes=_error_codes(error.callbacks),
                error_times=_error_times(error.callbacks),
            )
        client.cancelHeadTimeStamp(request_id)
        timestamps = [
            callback.values[0] for callback in callbacks if callback.kind == "head_timestamp"
        ]
        timestamp = _epoch_timestamp(timestamps[-1]) if timestamps else None
        return IbkrRequestEvidence(
            kind="EARLIEST_MIDPOINT",
            status=_status(callbacks, "head_timestamp"),
            latency_milliseconds=_milliseconds(started),
            contract_con_id=con_id,
            earliest_timestamp=timestamp,
            error_codes=_error_codes(callbacks),
            error_times=_error_times(callbacks),
        )

    def _require_client(self) -> Any:
        if self._client is None:
            raise RuntimeError("IBKR capability adapter is not connected")
        return self._client

    def _request_id(self) -> int:
        request_id = self._next_request_id
        self._next_request_id += 1
        return request_id

    async def _pace_historical_request(self) -> None:
        now = monotonic()
        if self._last_historical_request_at is not None:
            remaining = _HISTORICAL_REQUEST_INTERVAL_SECONDS - (
                now - self._last_historical_request_at
            )
            if remaining > 0:
                await self._sleep(remaining)
        self._last_historical_request_at = monotonic()

    async def _collect_until(self, request_id: int, terminal_kind: str) -> list[_Callback]:
        callbacks: list[_Callback] = []
        deadline = monotonic() + self._request_timeout_seconds
        while True:
            try:
                callback = await self._next_callback(deadline)
            except TimeoutError as error:
                raise IbkrRequestTimeout(callbacks) from error
            if _is_global_error(callback):
                _handle_global_error(callback)
                callbacks.append(callback)
                continue
            if callback.request_id != request_id:
                continue
            callbacks.append(callback)
            if callback.kind == terminal_kind or (
                callback.kind == "error" and _error_disposition(callback) == "REQUEST_ERROR"
            ):
                return callbacks

    async def _collect_for(self, request_id: int, seconds: float) -> list[_Callback]:
        callbacks: list[_Callback] = []
        deadline = monotonic() + seconds
        while True:
            try:
                callback = await self._next_callback(deadline)
            except TimeoutError:
                return callbacks
            if _is_global_error(callback):
                _handle_global_error(callback)
                callbacks.append(callback)
                continue
            if callback.request_id == request_id:
                callbacks.append(callback)

    async def _wait_for(self, request_id: int, kinds: set[str]) -> _Callback:
        deadline = monotonic() + self._request_timeout_seconds
        while True:
            callback = await self._next_callback(deadline)
            if _is_global_error(callback):
                _handle_global_error(callback)
                continue
            if callback.request_id == request_id and callback.kind in kinds:
                return callback
            if (
                callback.kind == "error"
                and callback.request_id == request_id
                and _error_disposition(callback)
                in {"CONNECTION_LOST", "PORT_RESET", "REQUEST_ERROR"}
            ):
                code = _error_codes((callback,))[0]
                raise RuntimeError(f"IBKR capability connection failed with {code}")

    async def _next_callback(self, deadline: float) -> _Callback:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError("IBKR capability request timed out")
        try:
            return await asyncio.to_thread(self._callbacks.get, True, remaining)
        except Empty as error:
            raise TimeoutError("IBKR capability request timed out") from error


def _official_client(callbacks: Queue[_Callback], identity: IbkrApiIdentity) -> Any:
    _verify_official_api_distribution(identity)
    try:
        client_module = importlib.import_module("ibapi.client")
        wrapper_module = importlib.import_module("ibapi.wrapper")
    except ImportError as error:
        raise RuntimeError(
            "IBKR capability probing requires the pinned wheel built from the official "
            "TWS API distribution"
        ) from error
    eclient = client_module.EClient
    ewrapper = wrapper_module.EWrapper

    class _Client(ewrapper, eclient):
        def __init__(self) -> None:
            eclient.__init__(self, self)

        def nextValidId(self, orderId: int) -> None:
            _emit(callbacks, _Callback("next_valid_id", -1, (orderId,)))

        def currentTime(self, time: int) -> None:
            _emit(callbacks, _Callback("current_time", -1, (time,)))

        def contractDetails(self, reqId: int, contractDetails: object) -> None:
            _emit(callbacks, _Callback("contract_details", reqId, (contractDetails,)))

        def contractDetailsEnd(self, reqId: int) -> None:
            _emit(callbacks, _Callback("contract_details_end", reqId, ()))

        def marketDataType(self, reqId: int, marketDataType: int) -> None:
            _emit(callbacks, _Callback("market_data_type", reqId, (marketDataType,)))

        def tickPrice(self, reqId: int, tickType: int, price: float, attrib: object) -> None:
            _emit(callbacks, _Callback("tick_price", reqId, (tickType, price)))

        def tickSize(self, reqId: int, tickType: int, size: object) -> None:
            _emit(callbacks, _Callback("tick_size", reqId, (tickType, size)))

        def historicalData(self, reqId: int, bar: object) -> None:
            _emit(callbacks, _Callback("historical_data", reqId, (bar,)))

        def historicalDataEnd(self, reqId: int, start: str, end: str) -> None:
            _emit(callbacks, _Callback("historical_data_end", reqId, (start, end)))

        def headTimestamp(self, reqId: int, headTimestamp: str) -> None:
            _emit(callbacks, _Callback("head_timestamp", reqId, (headTimestamp,)))

        def error(
            self,
            reqId: int,
            errorTime: int,
            errorCode: int,
            errorString: str,
            advancedOrderRejectJson: str,
        ) -> None:
            _emit(
                callbacks,
                _Callback("error", reqId, (errorTime, errorCode, _error_classification(errorCode))),
            )

    return _Client()


def _emit(callbacks: Queue[_Callback], callback: _Callback) -> None:
    try:
        callbacks.put_nowait(callback)
    except Full as error:
        raise RuntimeError("IBKR capability callback queue overflowed") from error


def _contract(query: IbkrContractQuery) -> object:
    try:
        contract_module = importlib.import_module("ibapi.contract")
    except ImportError as error:
        raise RuntimeError("official IBKR TWS API wheel is unavailable") from error
    contract = contract_module.Contract()
    contract.symbol = query.symbol
    contract.secType = query.security_type
    contract.exchange = query.exchange
    contract.currency = query.currency
    if query.local_symbol is not None:
        contract.localSymbol = query.local_symbol
    if query.trading_class is not None:
        contract.tradingClass = query.trading_class
    if query.multiplier is not None:
        contract.multiplier = query.multiplier
    return contract


def _contract_from_evidence(evidence: IbkrContractEvidence) -> object:
    query = IbkrContractQuery(
        instrument_id=InstrumentId("ibkr:probe"),
        symbol=evidence.symbol,
        security_type=evidence.security_type,
        exchange=evidence.exchange,
        currency=evidence.currency,
        local_symbol=evidence.local_symbol,
        trading_class=evidence.trading_class,
        multiplier=evidence.multiplier,
    )
    contract = cast(Any, _contract(query))
    contract.conId = evidence.con_id
    return contract


def _contract_evidence(value: object) -> IbkrContractEvidence:
    details = cast(Any, value)
    contract = details.contract
    return IbkrContractEvidence(
        con_id=int(contract.conId),
        symbol=str(contract.symbol),
        local_symbol=str(contract.localSymbol),
        security_type=str(contract.secType),
        exchange=str(contract.exchange),
        currency=str(contract.currency),
        trading_class=_optional_text(contract.tradingClass),
        multiplier=_optional_text(contract.multiplier),
        minimum_tick=_decimal_or_none(details.minTick),
        market_rule_ids=_csv(details.marketRuleIds),
        valid_exchanges=_csv(details.validExchanges),
        long_name=_optional_text(details.longName),
        underlier_con_id=_positive_int_or_none(details.underConId),
        timezone=_optional_text(details.timeZoneId),
        trading_hours=_optional_text(details.tradingHours),
        liquid_hours=_optional_text(details.liquidHours),
    )


def _status(callbacks: Sequence[_Callback], terminal_kind: str) -> str:
    if any(callback.kind == terminal_kind for callback in callbacks):
        return "SUCCESS"
    if any(
        callback.kind == "error" and _error_disposition(callback) == "REQUEST_ERROR"
        for callback in callbacks
    ):
        return "ERROR"
    return "TIMEOUT"


def _window_status(callbacks: Sequence[_Callback]) -> str:
    if any(
        callback.kind == "error" and _error_disposition(callback) == "REQUEST_ERROR"
        for callback in callbacks
    ):
        return "ERROR"
    if any(
        callback.kind in {"market_data_type", "tick_price", "tick_size"} for callback in callbacks
    ):
        return "SUCCESS"
    return "TIMEOUT"


def _error_codes(callbacks: Sequence[_Callback]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {f"IBKR_{_error_code(callback)}" for callback in callbacks if callback.kind == "error"}
        )
    )


def _error_times(callbacks: Sequence[_Callback]) -> tuple[int, ...]:
    return tuple(
        sorted(
            {
                int(cast(str | int, callback.values[0]))
                for callback in callbacks
                if callback.kind == "error"
            }
        )
    )


def _error_code(callback: _Callback) -> int:
    return int(cast(str | int, callback.values[1]))


def _error_disposition(callback: _Callback) -> str:
    return cast(str, callback.values[2])


def _error_classification(error_code: int) -> str:
    if error_code in {2104, 2106, 2107, 2108, 2119, 2158}:
        return "INFORMATIONAL"
    if error_code == 1100:
        return "CONNECTION_LOST"
    if error_code == 1101:
        return "CONNECTION_RESTORED_DATA_LOST"
    if error_code == 1102:
        return "CONNECTION_RESTORED_DATA_MAINTAINED"
    if error_code == 1300:
        return "PORT_RESET"
    return "REQUEST_ERROR"


def _is_global_error(callback: _Callback) -> bool:
    return callback.kind == "error" and callback.request_id == -1


def _handle_global_error(callback: _Callback) -> None:
    disposition = _error_disposition(callback)
    if disposition in {
        "CONNECTION_LOST",
        "CONNECTION_RESTORED_DATA_LOST",
        "PORT_RESET",
        "REQUEST_ERROR",
    }:
        raise IbkrConnectionIntegrityError(
            f"IBKR capability connection integrity failed with IBKR_{_error_code(callback)}"
        )


def _market_availability(
    callbacks: Sequence[_Callback], data_type: str | None, bid_usable: bool, ask_usable: bool
) -> str:
    if any(
        callback.kind == "error" and _error_disposition(callback) == "REQUEST_ERROR"
        for callback in callbacks
    ):
        return "UNAVAILABLE"
    if data_type is None:
        return "MARKET_DATA_TYPE_UNCONFIRMED"
    if data_type == "LIVE" and bid_usable and ask_usable:
        return "LIVE_AVAILABLE"
    if data_type == "DELAYED" and bid_usable and ask_usable:
        return "DELAYED_AVAILABLE"
    if data_type in {"FROZEN", "DELAYED_FROZEN"}:
        return "FROZEN_OR_DELAYED_FROZEN"
    return "UNAVAILABLE"


def _tick_family(data_type: str | None) -> tuple[set[int], set[int], set[int], set[int]]:
    live = ({_BID}, {_ASK}, {_BID_SIZE}, {_ASK_SIZE})
    delayed = ({_DELAYED_BID}, {_DELAYED_ASK}, {_DELAYED_BID_SIZE}, {_DELAYED_ASK_SIZE})
    if data_type in {"LIVE", "FROZEN"}:
        return live
    if data_type in {"DELAYED", "DELAYED_FROZEN"}:
        return delayed
    return (
        live[0] | delayed[0],
        live[1] | delayed[1],
        live[2] | delayed[2],
        live[3] | delayed[3],
    )


def _tick_types(callbacks: Sequence[_Callback]) -> set[int]:
    return {
        int(cast(str | int, callback.values[0]))
        for callback in callbacks
        if callback.kind in {"tick_price", "tick_size"}
    }


def _usable_price_seen(callbacks: Sequence[_Callback], tick_types: set[int]) -> bool:
    return any(
        callback.kind == "tick_price"
        and int(cast(str | int, callback.values[0])) in tick_types
        and isinstance(callback.values[1], (int, float))
        and not isinstance(callback.values[1], bool)
        and isfinite(float(callback.values[1]))
        and float(callback.values[1]) != -1.0
        for callback in callbacks
    )


def _verify_official_api_distribution(identity: IbkrApiIdentity) -> None:
    try:
        distribution = importlib.metadata.distribution("ibapi")
    except importlib.metadata.PackageNotFoundError as error:
        raise RuntimeError("pinned official IBKR TWS API wheel is unavailable") from error
    if distribution.version != identity.version:
        raise RuntimeError("installed IBKR API version does not match the pinned release")
    files = distribution.files or ()
    digest = hashlib.sha256()
    for file in sorted(files, key=str):
        if file.name == "RECORD":
            continue
        digest.update(str(file).encode())
        try:
            digest.update(distribution.locate_file(file).read_bytes())
        except OSError as error:
            raise RuntimeError("installed IBKR API distribution cannot be fingerprinted") from error
    if digest.hexdigest() != identity.package_fingerprint:
        raise RuntimeError(
            "installed IBKR API distribution does not match the controlled fingerprint"
        )


def _milliseconds(started: float) -> int:
    return round((monotonic() - started) * 1_000)


def _epoch_timestamp(value: object) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(str(value)), UTC)
    except (TypeError, ValueError, OSError):
        return None


def _optional_text(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _csv(value: object) -> tuple[str, ...]:
    text = _optional_text(value)
    return tuple(part for part in (text.split(",") if text else ()) if part)


def _decimal_or_none(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        decimal = Decimal(str(value))
    except InvalidOperation:
        return None
    return decimal if decimal > 0 else None


def _positive_int_or_none(value: object) -> int | None:
    try:
        number = int(cast(str | int, value))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None
