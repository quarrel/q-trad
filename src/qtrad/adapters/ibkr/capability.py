"""Official TWS API transport used only by the bounded Stage 1 capability probe."""

import asyncio
import hashlib
import importlib
import importlib.metadata
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from importlib.metadata import PackagePath
from itertools import count
from math import isfinite
from queue import Empty, Full, Queue
from threading import Event, Thread
from time import monotonic
from typing import Any, cast

from qtrad.adapters.ibkr.checkpoint import (
    IbkrCapabilityCheckpoint,
    checkpoint_query_key,
)
from qtrad.adapters.ibkr.session import (
    IbkrRecoveryAction,
    IbkrSession,
    IbkrSessionTimeouts,
    IbkrSystemCode,
)
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
_HISTORICAL_REQUEST_INTERVAL_SECONDS = 15.0
_LATEST_API_VERSION = "10.49"
_SUPPORTED_API_VERSIONS = frozenset({"10.49", "10.45"})
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
    version: str = _LATEST_API_VERSION

    def __post_init__(self) -> None:
        if self.version not in _SUPPORTED_API_VERSIONS:
            raise ValueError(
                "IBKR capability adapter requires official TWS API 10.49 or rollback 10.45"
            )
        if len(self.package_fingerprint) != 64 or any(
            character not in "0123456789abcdef" for character in self.package_fingerprint
        ):
            raise ValueError("IBKR API package fingerprint must be a lowercase SHA-256 digest")


@dataclass(frozen=True, slots=True)
class _Callback:
    kind: str
    request_id: int
    values: tuple[object, ...]
    generation: int = -1
    arrival_sequence: int = 0
    diagnostic: str | None = None
    message_sha256: str | None = None
    received_time: datetime | None = None


class IbkrConnectionIntegrityError(RuntimeError):
    """The Gateway session can no longer support trustworthy request evidence."""


class IbkrRequestTimeout(TimeoutError):
    """A bounded request timed out after retaining callbacks already observed for it."""

    def __init__(self, callbacks: Sequence[_Callback]) -> None:
        super().__init__("IBKR capability request timed out")
        self.callbacks = tuple(callbacks)


IBKR_CALLBACK_QUEUE_MAXSIZE = 50_000


class _CallbackQueue(Queue[_Callback]):
    def __init__(self, maxsize: int) -> None:
        super().__init__(maxsize=maxsize)
        self.overflowed = Event()


class OfficialIbkrCapabilityAdapter(IbkrCapabilityAdapter):
    """Direct API client with bounded requests and no account or order surface."""

    def __init__(
        self,
        endpoint: IbkrGatewayEndpoint,
        *,
        request_timeout_seconds: float = 10.0,
        upstream_recovery_timeout_seconds: float = 180.0,
        connect_timeout_seconds: float = 5.0,
        handshake_timeout_seconds: float = 15.0,
        server_time_timeout_seconds: float = 10.0,
        contract_timeout_seconds: float | None = None,
        historical_timeout_seconds: float | None = None,
        client_factory: Callable[[Queue[_Callback]], Any] | None = None,
        api_identity: IbkrApiIdentity | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        pacing_reserver: (Callable[[str, str, str, int], Awaitable[float]] | None) = None,
        checkpoint: IbkrCapabilityCheckpoint | None = None,
    ) -> None:
        if any(
            value <= 0
            for value in (
                request_timeout_seconds,
                upstream_recovery_timeout_seconds,
                connect_timeout_seconds,
                handshake_timeout_seconds,
                server_time_timeout_seconds,
                contract_timeout_seconds or request_timeout_seconds,
                historical_timeout_seconds or request_timeout_seconds,
            )
        ):
            raise ValueError("IBKR capability request timeouts must be positive")
        self._endpoint = endpoint
        self._request_timeout_seconds = request_timeout_seconds
        self._upstream_recovery_timeout_seconds = upstream_recovery_timeout_seconds
        self._connect_timeout_seconds = connect_timeout_seconds
        self._handshake_timeout_seconds = handshake_timeout_seconds
        self._server_time_timeout_seconds = server_time_timeout_seconds
        self._contract_timeout_seconds = contract_timeout_seconds or request_timeout_seconds
        self._historical_timeout_seconds = historical_timeout_seconds or request_timeout_seconds
        # Match the bounded historical callback budget; API reader bursts must not
        # overflow before the asyncio consumer can drain the queue.
        self._callbacks: _CallbackQueue = _CallbackQueue(maxsize=IBKR_CALLBACK_QUEUE_MAXSIZE)
        self._deferred_callbacks: deque[_Callback] = deque()
        if client_factory is None and api_identity is None:
            raise ValueError("IBKR capability adapter requires a verified official API identity")
        self._client_factory = client_factory or (
            lambda callbacks: _official_client(
                callbacks, cast(IbkrApiIdentity, api_identity), self._session.generation
            )
        )
        self._sleep = sleep
        self._pacing_reserver = pacing_reserver
        self._checkpoint = checkpoint
        self._last_historical_request_at: float | None = None
        self._arrival_sequence = 0
        self._client: Any | None = None
        self._thread: Thread | None = None
        self._next_request_id = 1
        self._session = IbkrSession(
            timeouts=IbkrSessionTimeouts(
                connect_seconds=connect_timeout_seconds,
                handshake_seconds=handshake_timeout_seconds,
                server_time_seconds=server_time_timeout_seconds,
                contract_seconds=self._contract_timeout_seconds,
                historical_seconds=self._historical_timeout_seconds,
                upstream_recovery_seconds=upstream_recovery_timeout_seconds,
            )
        )

    async def connect(self) -> None:
        if self._client is not None:
            raise RuntimeError("IBKR capability adapter is already connected")
        self._session.begin_connection()
        client = self._client_factory(self._callbacks)
        try:
            await asyncio.wait_for(
                asyncio.to_thread(
                    client.connect,
                    self._endpoint.host,
                    self._endpoint.port,
                    clientId=self._endpoint.client_id,
                ),
                timeout=self._connect_timeout_seconds,
            )
        except TimeoutError as error:
            await asyncio.to_thread(client.disconnect)
            self._session.stop()
            raise TimeoutError("IBKR Gateway socket connect timed out") from error
        try:
            self._session.mark_socket_connected()
            self._client = client
            self._thread = Thread(target=client.run, name="ibkr-capability-reader", daemon=True)
            self._thread.start()
            await self._wait_for(-1, {"next_valid_id"}, self._handshake_timeout_seconds)
            self._session.mark_handshake()
            client.reqCurrentTime()
            await self._wait_for(-1, {"current_time"}, self._server_time_timeout_seconds)
            self._session.mark_server_time()
        except BaseException:
            if self._client is not None:
                await self.disconnect()
            else:
                client.disconnect()
                self._session.stop()
            raise

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
        self._session.stop()

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
        existing = await self._checkpoint.load(queries) if self._checkpoint is not None else ()
        by_query = {checkpoint_query_key(result.query): result for result in existing}
        results: list[IbkrCandidateCapability] = []
        for query in queries:
            key = checkpoint_query_key(query)
            result = by_query.get(key)
            if result is None or not _candidate_complete(result):
                result = await self._probe_query(query, existing=result)
            results.append(result)
        return tuple(results)

    async def _probe_query(
        self,
        query: IbkrContractQuery,
        *,
        existing: IbkrCandidateCapability | None = None,
    ) -> IbkrCandidateCapability:
        if existing is None:
            client = self._require_client()
            request_id = self._request_id()
            await self._pace_request(
                "contract",
                f"{query.symbol}:{query.security_type}:{query.exchange}:{query.currency}",
                checkpoint_query_key(query),
            )
            started = monotonic()
            client.reqContractDetails(request_id, _contract(query))
            try:
                callbacks = await self._collect_until(
                    request_id, "contract_details_end", self._contract_timeout_seconds
                )
            except IbkrRequestTimeout as error:
                contract_request = IbkrRequestEvidence(
                    kind="CONTRACT_DETAILS",
                    status="TIMEOUT",
                    latency_milliseconds=_milliseconds(started),
                    error_codes=_error_codes(error.callbacks),
                    error_times=_error_times(error.callbacks),
                    returned_contract_count=sum(
                        callback.kind == "contract_details" for callback in error.callbacks
                    ),
                )
                result = IbkrCandidateCapability(
                    query=query, contracts=(), requests=(contract_request,)
                )
                await self._save_checkpoint(result)
                return result
            error_codes = _error_codes(callbacks)
            returned_callbacks = tuple(
                callback for callback in callbacks if callback.kind == "contract_details"
            )
            returned_count = len(returned_callbacks)
            contract_status = _status(callbacks, "contract_details_end")
            exact_match = contract_status == "SUCCESS" and returned_count == 1
            contracts = (
                (_contract_evidence(returned_callbacks[0].values[0]),) if exact_match else ()
            )
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
            result = IbkrCandidateCapability(
                query=query, contracts=contracts, requests=(contract_request,)
            )
            await self._save_checkpoint(result)
        else:
            result = existing
            if result.query != query:
                raise ValueError("IBKR checkpoint query does not match probe query")

        if not result.contracts:
            return result

        requests = list(result.requests)
        completed = {request.kind for request in requests}

        async def persist(evidence: IbkrRequestEvidence) -> None:
            requests.append(evidence)
            completed.add(evidence.kind)
            await self._save_checkpoint(
                IbkrCandidateCapability(
                    query=query, contracts=result.contracts, requests=tuple(requests)
                )
            )

        await self._probe_contract(result.contracts[0], completed=completed, persist=persist)
        return IbkrCandidateCapability(
            query=query, contracts=result.contracts, requests=tuple(requests)
        )

    async def _save_checkpoint(self, result: IbkrCandidateCapability) -> None:
        if self._checkpoint is not None:
            await self._checkpoint.save(result)

    async def _probe_contract(
        self,
        contract: IbkrContractEvidence,
        *,
        completed: set[str],
        persist: Callable[[IbkrRequestEvidence], Awaitable[None]],
    ) -> None:
        api_contract = _contract_from_evidence(contract)

        async def capture(kind: str, factory: Callable[[], Awaitable[IbkrRequestEvidence]]) -> None:
            if kind in completed:
                return
            evidence = await factory()
            if evidence.kind != kind:
                raise RuntimeError(
                    f"IBKR request evidence kind mismatch: {evidence.kind} != {kind}"
                )
            await persist(evidence)

        await capture(
            "LIVE_TOP_OF_BOOK",
            lambda: self._market_evidence(api_contract, contract.con_id, 1, "LIVE_TOP_OF_BOOK"),
        )
        await capture(
            "DELAYED_ENABLED_TOP_OF_BOOK",
            lambda: self._market_evidence(
                api_contract, contract.con_id, 3, "DELAYED_ENABLED_TOP_OF_BOOK"
            ),
        )
        for value in ("MIDPOINT", "BID", "ASK"):
            for use_rth in (False, True):
                kind = f"ONE_MINUTE_{value}_{'RTH' if use_rth else 'ALL'}"
                await capture(
                    kind,
                    lambda value=value, use_rth=use_rth: self._historical_evidence(
                        api_contract, contract.con_id, value, use_rth
                    ),
                )
        await capture(
            "ONE_SECOND_MIDPOINT_ALL",
            lambda: self._one_second_evidence(api_contract, contract.con_id),
        )
        await capture(
            "EARLIEST_MIDPOINT",
            lambda: self._earliest_evidence(api_contract, contract.con_id),
        )

    async def _market_evidence(
        self, api_contract: object, con_id: int, requested_type: int, kind: str
    ) -> IbkrRequestEvidence:
        client = self._require_client()
        market_request_id = self._request_id()
        await self._pace_request(
            "BID_ASK",
            str(con_id),
            f"{kind}:{requested_type}",
            2,
        )
        started = monotonic()
        client.reqMarketDataType(requested_type)
        client.reqMktData(market_request_id, api_contract, "", False, False, [])
        try:
            callbacks = await self._collect_for(market_request_id, self._contract_timeout_seconds)
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
        await self._pace_historical_request(
            str(con_id),
            f"one-minute:{what_to_show}:{int(use_rth)}",
            2 if what_to_show == "BID_ASK" else 1,
        )
        started = monotonic()
        client.reqHistoricalData(
            request_id, contract, "", "120 S", "1 min", what_to_show, int(use_rth), 2, False, []
        )
        try:
            callbacks = await self._collect_until(
                request_id, "historical_data_end", self._historical_timeout_seconds
            )
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
        await self._pace_historical_request(
            str(con_id),
            "one-second:MIDPOINT:0",
        )
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
            callbacks = await self._collect_until(
                request_id, "historical_data_end", self._historical_timeout_seconds
            )
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
        await self._pace_historical_request(
            str(con_id),
            "earliest:MIDPOINT:0",
        )
        started = monotonic()
        client.reqHeadTimeStamp(request_id, contract, "MIDPOINT", 0, 2)
        try:
            callbacks = await self._collect_until(
                request_id, "head_timestamp", self._historical_timeout_seconds
            )
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

    async def _pace_request(
        self,
        request_kind: str,
        contract_key: str,
        request_fingerprint: str,
        weight: int = 1,
    ) -> None:
        if not request_kind or not contract_key or not request_fingerprint:
            raise ValueError("IBKR pacing request identity is required")
        if self._pacing_reserver is not None:
            while True:
                delay = await self._pacing_reserver(
                    request_kind,
                    contract_key,
                    request_fingerprint,
                    weight,
                )
                if delay < 0:
                    raise ValueError("IBKR pacing reserver returned a negative delay")
                if delay == 0:
                    return
                await self._sleep(delay)
        if request_kind == "historical":
            now = monotonic()
            if self._last_historical_request_at is not None:
                remaining = _HISTORICAL_REQUEST_INTERVAL_SECONDS - (
                    now - self._last_historical_request_at
                )
                if remaining > 0:
                    await self._sleep(remaining)
            self._last_historical_request_at = monotonic()

    async def _pace_historical_request(
        self,
        contract_key: str,
        request_fingerprint: str,
        weight: int = 1,
    ) -> None:
        await self._pace_request("historical", contract_key, request_fingerprint, weight)

    async def _collect_until(
        self,
        request_id: int,
        terminal_kind: str,
        timeout_seconds: float | None = None,
    ) -> list[_Callback]:
        callbacks: list[_Callback] = []
        deadline = monotonic() + (timeout_seconds or self._request_timeout_seconds)
        while True:
            try:
                callback = await self._next_callback(deadline)
            except TimeoutError as error:
                raise IbkrRequestTimeout(callbacks) from error
            if not self._session.accept_callback(callback.generation):
                continue
            if _is_global_error(callback):
                await self._handle_global_error(callback)
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
            if not self._session.accept_callback(callback.generation):
                continue
            if _is_global_error(callback):
                await self._handle_global_error(callback)
                callbacks.append(callback)
                continue
            if callback.request_id == request_id:
                callbacks.append(callback)

    async def _wait_for(
        self,
        request_id: int,
        kinds: set[str],
        timeout_seconds: float | None = None,
    ) -> _Callback:
        deadline = monotonic() + (timeout_seconds or self._request_timeout_seconds)
        pending = list(self._deferred_callbacks)
        self._deferred_callbacks.clear()
        pending_index = 0
        stashed: list[_Callback] = []
        try:
            while True:
                if pending_index < len(pending):
                    callback = pending[pending_index]
                    pending_index += 1
                else:
                    callback = await self._next_queued_callback(deadline)
                if not self._session.accept_callback(callback.generation):
                    continue
                if _is_global_error(callback):
                    await self._handle_global_error(callback)
                    continue
                if callback.request_id != request_id:
                    stashed.append(callback)
                    continue
                if callback.kind in kinds:
                    return callback
                stashed.append(callback)
                if callback.kind == "error" and _error_disposition(callback) in {
                    "CONNECTION_LOST",
                    "PORT_RESET",
                    "REQUEST_ERROR",
                }:
                    code = _error_codes((callback,))[0]
                    raise RuntimeError(f"IBKR capability connection failed with {code}")
        finally:
            nested = list(self._deferred_callbacks)
            self._deferred_callbacks.clear()
            self._deferred_callbacks.extend(pending[pending_index:])
            self._deferred_callbacks.extend(stashed)
            self._deferred_callbacks.extend(nested)

    async def _next_callback(self, deadline: float) -> _Callback:
        if self._callbacks.overflowed.is_set():
            raise IbkrConnectionIntegrityError("IBKR capability callback queue overflowed")
        if self._deferred_callbacks:
            return self._deferred_callbacks.popleft()
        return await self._next_queued_callback(deadline)

    async def _next_queued_callback(self, deadline: float) -> _Callback:
        if self._callbacks.overflowed.is_set():
            raise IbkrConnectionIntegrityError("IBKR capability callback queue overflowed")
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError("IBKR capability request timed out")
        try:
            callback = await asyncio.to_thread(self._callbacks.get, True, remaining)
        except Empty as error:
            if self._callbacks.overflowed.is_set():
                raise IbkrConnectionIntegrityError(
                    "IBKR capability callback queue overflowed"
                ) from error
            raise TimeoutError("IBKR capability request timed out") from error
        self._arrival_sequence += 1
        return replace(
            callback,
            generation=(
                callback.generation if callback.generation >= 0 else self._session.generation
            ),
            arrival_sequence=self._arrival_sequence,
        )

    async def _handle_global_error(self, callback: _Callback) -> None:
        error_code = _error_code(callback)
        decision = self._session.on_system_message(
            error_code,
            generation=callback.generation,
        )
        if error_code == int(IbkrSystemCode.UPSTREAM_DISCONNECTED):
            await self._await_upstream_recovery(error_code)
            return
        if error_code == int(IbkrSystemCode.UPSTREAM_RESTORED_DATA_LOST):
            raise IbkrConnectionIntegrityError(
                "IBKR_1101 upstream recovered with market-data subscriptions lost"
            )
        if decision.revalidate_server_time:
            await self._revalidate_server_time()
            return
        if decision.action in {
            IbkrRecoveryAction.RESTART_ADAPTER,
            IbkrRecoveryAction.RESTART_GATEWAY,
            IbkrRecoveryAction.OPERATOR,
        }:
            raise IbkrConnectionIntegrityError(
                f"IBKR capability connection integrity failed with IBKR_{error_code}"
            )

    async def _revalidate_server_time(self) -> None:
        client = self._require_client()
        preserved = self._deferred_callbacks
        self._deferred_callbacks = deque()
        try:
            client.reqCurrentTime()
            deadline = monotonic() + self._server_time_timeout_seconds
            while True:
                callback = await self._next_queued_callback(deadline)
                if not self._session.accept_callback(callback.generation):
                    continue
                if _is_global_error(callback):
                    await self._handle_global_error(callback)
                    continue
                if callback.request_id != -1:
                    self._deferred_callbacks.append(callback)
                    continue
                if callback.kind == "current_time":
                    self._session.mark_server_time()
                    return
        finally:
            self._deferred_callbacks.extendleft(reversed(preserved))

    async def _await_upstream_recovery(self, disconnected_code: int) -> None:
        deadline = monotonic() + self._upstream_recovery_timeout_seconds
        while True:
            try:
                callback = await self._next_queued_callback(deadline)
            except TimeoutError as error:
                raise IbkrConnectionIntegrityError(
                    f"IBKR_{disconnected_code} upstream did not recover "
                    "within the bounded recovery window"
                ) from error
            if not self._session.accept_callback(callback.generation):
                continue
            if not _is_global_error(callback):
                self._deferred_callbacks.append(callback)
                continue
            error_code = _error_code(callback)
            decision = self._session.on_system_message(
                error_code,
                generation=callback.generation,
            )
            if error_code == int(IbkrSystemCode.UPSTREAM_RESTORED_DATA_MAINTAINED):
                if decision.revalidate_server_time:
                    await self._revalidate_server_time()
                return
            if error_code == int(IbkrSystemCode.UPSTREAM_RESTORED_DATA_LOST):
                raise IbkrConnectionIntegrityError(
                    f"IBKR_{disconnected_code} upstream recovered with "
                    "market-data subscriptions lost"
                )
            if error_code == int(IbkrSystemCode.UPSTREAM_DISCONNECTED):
                continue
            if decision.action != IbkrRecoveryAction.NONE:
                raise IbkrConnectionIntegrityError(
                    f"IBKR upstream recovery failed with IBKR_{error_code}"
                )


def _official_client(
    callbacks: Queue[_Callback],
    identity: IbkrApiIdentity,
    generation: int = 0,
) -> Any:
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
    sequence = count(1)

    class _Client(ewrapper, eclient):
        def __init__(self) -> None:
            eclient.__init__(self, self)

        def nextValidId(self, orderId: int) -> None:
            _emit(callbacks, _Callback("next_valid_id", -1, (orderId,)), generation, next(sequence))

        def currentTime(self, time: int) -> None:
            _emit(callbacks, _Callback("current_time", -1, (time,)), generation, next(sequence))

        def contractDetails(self, reqId: int, contractDetails: object) -> None:
            _emit(
                callbacks,
                _Callback("contract_details", reqId, (contractDetails,)),
                generation,
                next(sequence),
            )

        def contractDetailsEnd(self, reqId: int) -> None:
            _emit(
                callbacks, _Callback("contract_details_end", reqId, ()), generation, next(sequence)
            )

        def marketDataType(self, reqId: int, marketDataType: int) -> None:
            _emit(
                callbacks,
                _Callback("market_data_type", reqId, (marketDataType,)),
                generation,
                next(sequence),
            )

        def tickPrice(self, reqId: int, tickType: int, price: float, attrib: object) -> None:
            _emit(
                callbacks,
                _Callback("tick_price", reqId, (tickType, price)),
                generation,
                next(sequence),
            )

        def tickSize(self, reqId: int, tickType: int, size: object) -> None:
            _emit(
                callbacks,
                _Callback("tick_size", reqId, (tickType, size)),
                generation,
                next(sequence),
            )

        def historicalData(self, reqId: int, bar: object) -> None:
            _emit(
                callbacks, _Callback("historical_data", reqId, (bar,)), generation, next(sequence)
            )

        def historicalDataEnd(self, reqId: int, start: str, end: str) -> None:
            _emit(
                callbacks,
                _Callback("historical_data_end", reqId, (start, end)),
                generation,
                next(sequence),
            )

        def historicalSchedule(
            self,
            reqId: int,
            startDateTime: str,
            endDateTime: str,
            timeZone: str,
            sessions: Sequence[object],
        ) -> None:
            _emit(
                callbacks,
                _Callback(
                    "historical_schedule",
                    reqId,
                    (startDateTime, endDateTime, timeZone, tuple(sessions)),
                ),
                generation,
                next(sequence),
            )

        def headTimestamp(self, reqId: int, headTimestamp: str) -> None:
            _emit(
                callbacks,
                _Callback("head_timestamp", reqId, (headTimestamp,)),
                generation,
                next(sequence),
            )

        def error(
            self,
            reqId: int,
            errorTime: int,
            errorCode: int,
            errorString: str,
            advancedOrderRejectJson: str | None = None,
        ) -> None:
            classification = _error_classification(errorCode)
            _emit(
                callbacks,
                _Callback(
                    "error",
                    reqId,
                    (errorTime, errorCode, classification),
                    diagnostic=f"IBKR_{errorCode}_{classification}",
                    message_sha256=(
                        hashlib.sha256(errorString.encode("utf-8")).hexdigest()
                        if errorString
                        else None
                    ),
                ),
                generation,
                next(sequence),
            )

    return _Client()


def _emit(
    callbacks: Queue[_Callback],
    callback: _Callback,
    generation: int,
    arrival_sequence: int,
) -> None:
    tagged = replace(
        callback,
        generation=generation,
        arrival_sequence=arrival_sequence,
        received_time=(
            callback.received_time if callback.received_time is not None else datetime.now(UTC)
        ),
    )
    try:
        callbacks.put_nowait(tagged)
    except Full as error:
        overflowed = getattr(callbacks, "overflowed", None)
        if isinstance(overflowed, Event):
            overflowed.set()
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
    if evidence.primary_exchange is not None:
        contract.primaryExchange = evidence.primary_exchange
    if evidence.contract_month is not None:
        contract.lastTradeDateOrContractMonth = evidence.contract_month
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
        primary_exchange=_optional_text(getattr(contract, "primaryExchange", None)),
        contract_month=_optional_text(getattr(contract, "lastTradeDateOrContractMonth", None)),
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


_COMPLETE_REQUEST_KINDS = frozenset(
    {
        "CONTRACT_DETAILS",
        "LIVE_TOP_OF_BOOK",
        "DELAYED_ENABLED_TOP_OF_BOOK",
        "ONE_MINUTE_MIDPOINT_ALL",
        "ONE_MINUTE_MIDPOINT_RTH",
        "ONE_MINUTE_BID_ALL",
        "ONE_MINUTE_BID_RTH",
        "ONE_MINUTE_ASK_ALL",
        "ONE_MINUTE_ASK_RTH",
        "ONE_SECOND_MIDPOINT_ALL",
        "EARLIEST_MIDPOINT",
    }
)


def _candidate_complete(result: IbkrCandidateCapability) -> bool:
    request_kinds = {request.kind for request in result.requests}
    if not result.contracts:
        return "CONTRACT_DETAILS" in request_kinds
    return _COMPLETE_REQUEST_KINDS.issubset(request_kinds)


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
    if error_code in {
        int(IbkrSystemCode.MARKET_DATA_FARM_CONNECTED),
        int(IbkrSystemCode.HISTORICAL_FARM_CONNECTED),
        int(IbkrSystemCode.MARKET_DATA_FARM_INACTIVE),
        int(IbkrSystemCode.HISTORICAL_FARM_INACTIVE),
        int(IbkrSystemCode.MARKET_DATA_FARM_CONNECTING),
        int(IbkrSystemCode.SECURITY_DEFINITION_FARM_CONNECTED),
    }:
        return "INFORMATIONAL"
    if error_code in {
        int(IbkrSystemCode.MARKET_DATA_FARM_DISCONNECTED),
        int(IbkrSystemCode.HISTORICAL_FARM_DISCONNECTED),
        int(IbkrSystemCode.SECURITY_DEFINITION_FARM_DISCONNECTED),
    }:
        if error_code == int(IbkrSystemCode.SECURITY_DEFINITION_FARM_DISCONNECTED):
            return "SECURITY_DEFINITION_FARM_DISCONNECTED"
        return "FARM_DISCONNECTED"
    if error_code == int(IbkrSystemCode.UPSTREAM_DISCONNECTED):
        return "CONNECTION_LOST"
    if error_code == int(IbkrSystemCode.UPSTREAM_RESTORED_DATA_LOST):
        return "CONNECTION_RESTORED_DATA_LOST"
    if error_code == int(IbkrSystemCode.UPSTREAM_RESTORED_DATA_MAINTAINED):
        return "CONNECTION_RESTORED_DATA_MAINTAINED"
    if error_code == int(IbkrSystemCode.PORT_RESET):
        return "PORT_RESET"
    if error_code == int(IbkrSystemCode.PACING_VIOLATION):
        return "PACING_VIOLATION"
    return "REQUEST_ERROR"


def _is_global_error(callback: _Callback) -> bool:
    return callback.kind == "error" and callback.request_id == -1


def _market_availability(
    callbacks: Sequence[_Callback], data_type: str | None, bid_usable: bool, ask_usable: bool
) -> str:
    request_error = any(
        callback.kind == "error" and _error_disposition(callback) == "REQUEST_ERROR"
        for callback in callbacks
    )
    if request_error and not (data_type == "DELAYED" and bid_usable and ask_usable):
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
    if not (
        distribution.version == identity.version
        or distribution.version.startswith(f"{identity.version}.")
    ):
        raise RuntimeError("installed IBKR API version does not match the pinned release")
    digest = hashlib.sha256()
    for file in _source_manifest_files(distribution.files or ()):
        digest.update(str(file).encode())
        try:
            digest.update(distribution.locate_file(file).read_bytes())
        except OSError as error:
            raise RuntimeError("installed IBKR API distribution cannot be fingerprinted") from error
    if digest.hexdigest() != identity.package_fingerprint:
        raise RuntimeError(
            "installed IBKR API distribution does not match the controlled fingerprint"
        )


def _source_manifest_files(files: Sequence[PackagePath]) -> tuple[PackagePath, ...]:
    """Return deterministic source files, excluding installer-generated material."""
    excluded_names = {
        "RECORD",
        "METADATA",
        "WHEEL",
        "INSTALLER",
        "direct_url.json",
        "top_level.txt",
    }
    selected: list[PackagePath] = []
    for file in files:
        path = str(file)
        parts = path.split("/")
        name = parts[-1]
        if name in excluded_names or any(
            part.endswith((".dist-info", ".egg-info")) for part in parts
        ):
            continue
        if "__pycache__" in parts or name.endswith((".pyc", ".pyo")):
            continue
        selected.append(file)
    return tuple(sorted(selected, key=str))


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
