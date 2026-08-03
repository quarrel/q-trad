"""Official TWS API transport for bounded IBKR historical acquisition."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from queue import Queue
from time import monotonic
from typing import Any, cast
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from qtrad.adapters.ibkr.capability import (
    IbkrApiIdentity,
    IbkrConnectionIntegrityError,
    IbkrGatewayEndpoint,
    IbkrRequestTimeout,
    OfficialIbkrCapabilityAdapter,
    _Callback,
    _contract,
    _contract_evidence,
    _error_code,
    _error_disposition,
)
from qtrad.domain.events import JsonValue
from qtrad.domain.ibkr_execution import (
    IbkrHistoricalCallback,
    IbkrHistoricalCallbackKind,
    IbkrHistoricalConnection,
    IbkrHistoricalDisconnected,
    IbkrHistoricalIncomplete,
    IbkrHistoricalRetryableError,
    IbkrHistoricalTerminalError,
    IbkrTerminalDisposition,
)
from qtrad.domain.ibkr_historical import (
    IbkrContractFingerprint,
    IbkrHistoricalRequest,
    IbkrHistoricalRequestKind,
)
from qtrad.domain.time import require_utc
from qtrad.ports.ibkr_historical import (
    IbkrContractReauthentication,
    IbkrHistoricalCallbackSink,
    IbkrHistoricalDataPort,
)

_HISTORICAL_SCHEDULE_FORMAT_DATE = 2
_CONNECTION_ERROR_DISPOSITIONS = frozenset(
    {"CONNECTION_LOST", "PORT_RESET", "CONNECTION_RESTORED_DATA_LOST"}
)
_ENTITLEMENT_ERROR_CODES = frozenset({162, 354, 10167, 10186})
_INVALID_REQUEST_ERROR_CODES = frozenset({321, 322, 319})
_CONTRACT_MISMATCH_ERROR_CODES = frozenset({200, 201})


class OfficialIbkrHistoricalAdapter(OfficialIbkrCapabilityAdapter, IbkrHistoricalDataPort):
    """Direct official API historical transport with no account or order surface."""

    def __init__(
        self,
        endpoint: IbkrGatewayEndpoint,
        *,
        request_timeout_seconds: float = 60.0,
        upstream_recovery_timeout_seconds: float = 180.0,
        connect_timeout_seconds: float = 5.0,
        handshake_timeout_seconds: float = 15.0,
        server_time_timeout_seconds: float = 10.0,
        contract_timeout_seconds: float | None = None,
        historical_timeout_seconds: float | None = None,
        client_factory: Callable[[Queue[_Callback]], Any] | None = None,
        api_identity: IbkrApiIdentity | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        pacing_reserver: Callable[[str, str, str, int], Awaitable[float]] | None = None,
        checkpoint: Any | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        super().__init__(
            endpoint,
            request_timeout_seconds=request_timeout_seconds,
            upstream_recovery_timeout_seconds=upstream_recovery_timeout_seconds,
            connect_timeout_seconds=connect_timeout_seconds,
            handshake_timeout_seconds=handshake_timeout_seconds,
            server_time_timeout_seconds=server_time_timeout_seconds,
            contract_timeout_seconds=contract_timeout_seconds,
            historical_timeout_seconds=historical_timeout_seconds,
            client_factory=client_factory,
            api_identity=api_identity,
            sleep=sleep,
            pacing_reserver=pacing_reserver,
            checkpoint=checkpoint,
        )
        self._clock = clock
        self._historical_connection: IbkrHistoricalConnection | None = None
        self._request_lock = asyncio.Lock()

    async def connect(self) -> IbkrHistoricalConnection:  # pyright: ignore[reportIncompatibleMethodOverride]  # ty: ignore[invalid-method-override]
        await super().connect()
        connection = IbkrHistoricalConnection(
            connection_session_id=uuid4(),
            connection_generation=self._session.generation,
        )
        self._historical_connection = connection
        return connection

    async def disconnect(self) -> None:
        try:
            await super().disconnect()
        finally:
            self._historical_connection = None

    async def reauthenticate_contract(
        self,
        fingerprint: IbkrContractFingerprint,
        *,
        timeout_seconds: float | None = None,
    ) -> IbkrContractReauthentication:
        """Request exact contract details and return immutable mismatch evidence."""
        connection = self._require_historical_connection()
        async with self._request_lock:
            request_id = self._request_id()
            await self._pace_request(
                "contract",
                str(fingerprint.con_id),
                f"reauth:{fingerprint.con_id}",
            )
            client = self._require_client()
            client.reqContractDetails(request_id, _api_contract(fingerprint))
            try:
                callbacks = await self._collect_raw(
                    request_id,
                    terminal_kind="contract_details_end",
                    timeout_seconds=timeout_seconds or self._contract_timeout_seconds,
                )
            except IbkrRequestTimeout as error:
                return IbkrContractReauthentication(
                    request_id=request_id,
                    connection_generation=connection.connection_generation,
                    expected=fingerprint,
                    observed=(),
                    status="TIMEOUT",
                    error_codes=_callback_error_codes(error.callbacks),
                    diagnostics=_callback_diagnostics(error.callbacks),
                )

            error_callbacks = tuple(item for item in callbacks if item.kind == "error")
            details = tuple(item.values[0] for item in callbacks if item.kind == "contract_details")
            observed = tuple(_fingerprint_from_details(item) for item in details)
            error_codes = _callback_error_codes(error_callbacks)
            diagnostics = _callback_diagnostics(error_callbacks)
            status = "MATCH" if len(observed) == 1 and observed[0] == fingerprint else "MISMATCH"
            if error_callbacks and not observed:
                status = "ERROR"
            return IbkrContractReauthentication(
                request_id=request_id,
                connection_generation=connection.connection_generation,
                expected=fingerprint,
                observed=observed,
                status=status,
                error_codes=error_codes,
                diagnostics=diagnostics,
            )

    async def reauthenticate_contracts(
        self,
        fingerprints: Sequence[IbkrContractFingerprint],
        *,
        timeout_seconds: float | None = None,
    ) -> tuple[IbkrContractReauthentication, ...]:
        """Reauthenticate every supplied fingerprint in deterministic input order."""
        if not fingerprints:
            raise ValueError("IBKR reauthentication requires at least one contract")
        results: list[IbkrContractReauthentication] = []
        for fingerprint in fingerprints:
            results.append(
                await self.reauthenticate_contract(
                    fingerprint,
                    timeout_seconds=timeout_seconds,
                )
            )
        return tuple(results)

    async def request_historical(
        self,
        request: IbkrHistoricalRequest,
        *,
        request_id: int,
        connection_session_id: UUID,
        connection_generation: int,
        callback: IbkrHistoricalCallbackSink,
    ) -> None:
        """Issue one frozen request and stream only correlated, JSON-safe callbacks."""
        connection = self._require_historical_connection()
        if (
            connection.connection_session_id != connection_session_id
            or connection.connection_generation != connection_generation
            or self._session.generation != connection_generation
        ):
            raise IbkrHistoricalDisconnected(
                "IBKR historical request used a superseded connection generation"
            )
        if request_id <= 0:
            raise ValueError("IBKR historical provider request ID must be positive")

        async with self._request_lock:
            client = self._require_client()
            await self._pace_historical_request(
                str(request.fingerprint.con_id),
                request.request_sha256,
            )
            api_contract = _api_contract(request.fingerprint)
            completed = False
            try:
                if request.kind is IbkrHistoricalRequestKind.MIDPOINT_BARS:
                    client.reqHistoricalData(
                        request_id,
                        api_contract,
                        request.end_date_time,
                        request.duration,
                        cast(str, request.bar_size),
                        cast(str, request.what_to_show),
                        int(request.use_rth),
                        cast(int, request.format_date),
                        request.keep_up_to_date,
                        [],
                    )
                    await self._collect_bars(
                        request,
                        request_id=request_id,
                        connection=connection,
                        callback=callback,
                    )
                elif request.kind is IbkrHistoricalRequestKind.SCHEDULE:
                    client.reqHistoricalData(
                        request_id,
                        api_contract,
                        request.end_date_time,
                        request.duration,
                        "",
                        "SCHEDULE",
                        int(request.use_rth),
                        _HISTORICAL_SCHEDULE_FORMAT_DATE,
                        request.keep_up_to_date,
                        [],
                    )
                    await self._collect_schedule(
                        request,
                        request_id=request_id,
                        connection=connection,
                        callback=callback,
                    )
                else:
                    raise ValueError(f"unsupported IBKR historical request kind: {request.kind}")
                completed = True
            except IbkrHistoricalIncomplete as error:
                disposition = (
                    IbkrTerminalDisposition.SESSION_EVIDENCE_UNAVAILABLE
                    if request.kind is IbkrHistoricalRequestKind.SCHEDULE
                    else IbkrTerminalDisposition.INCOMPLETE_RESPONSE
                )
                raise IbkrHistoricalTerminalError(disposition, str(error)) from error
            except asyncio.CancelledError:
                if not completed:
                    client.cancelHistoricalData(request_id)
                    completed = True
                raise
            finally:
                if not completed:
                    client.cancelHistoricalData(request_id)

    async def _collect_bars(
        self,
        request: IbkrHistoricalRequest,
        *,
        request_id: int,
        connection: IbkrHistoricalConnection,
        callback: IbkrHistoricalCallbackSink,
    ) -> None:
        deadline = monotonic() + self._historical_timeout_seconds
        callbacks = await self._begin_correlated_callbacks()
        try:
            while True:
                item = await self._next_correlated_callback(
                    request_id,
                    deadline,
                    callbacks,
                )
                if item.kind == "historical_data":
                    await callback(
                        self._domain_callback(
                            connection,
                            request_id,
                            IbkrHistoricalCallbackKind.MIDPOINT_BAR,
                            _bar_payload(item.values[0]),
                            item,
                        )
                    )
                elif item.kind == "error":
                    await callback(
                        self._domain_callback(
                            connection,
                            request_id,
                            IbkrHistoricalCallbackKind.ERROR,
                            _error_payload(item),
                            item,
                        )
                    )
                    await self._raise_for_error(item)
                elif item.kind == "historical_data_end":
                    await callback(
                        self._domain_callback(
                            connection,
                            request_id,
                            IbkrHistoricalCallbackKind.COMPLETION,
                            _completion_payload(item.values),
                            item,
                        )
                    )
                    return
        finally:
            self._end_correlated_callbacks(callbacks)

    async def _collect_schedule(
        self,
        request: IbkrHistoricalRequest,
        *,
        request_id: int,
        connection: IbkrHistoricalConnection,
        callback: IbkrHistoricalCallbackSink,
    ) -> None:
        deadline = monotonic() + self._historical_timeout_seconds
        callbacks = await self._begin_correlated_callbacks()
        try:
            while True:
                item = await self._next_correlated_callback(
                    request_id,
                    deadline,
                    callbacks,
                )
                if item.kind == "historical_schedule":
                    payload = _schedule_payload(item.values)
                    await callback(
                        self._domain_callback(
                            connection,
                            request_id,
                            IbkrHistoricalCallbackKind.SCHEDULE,
                            payload,
                            item,
                        )
                    )
                    await callback(
                        self._domain_callback(
                            connection,
                            request_id,
                            IbkrHistoricalCallbackKind.COMPLETION,
                            {
                                "start": payload["start"],
                                "end": payload["end"],
                                "time_zone": payload["time_zone"],
                            },
                            item,
                        )
                    )
                    return
                if item.kind == "error":
                    await callback(
                        self._domain_callback(
                            connection,
                            request_id,
                            IbkrHistoricalCallbackKind.ERROR,
                            _error_payload(item),
                            item,
                        )
                    )
                    await self._raise_for_error(item)
        finally:
            self._end_correlated_callbacks(callbacks)

    async def _begin_correlated_callbacks(self) -> list[_Callback]:
        pending = list(self._deferred_callbacks)
        self._deferred_callbacks.clear()
        return pending

    def _end_correlated_callbacks(self, pending: list[_Callback]) -> None:
        for item in pending:
            self._defer_callback(item)

    def _defer_callback(self, item: _Callback) -> None:
        if len(self._deferred_callbacks) >= 2000:
            raise IbkrHistoricalRetryableError("IBKR historical callback buffer exhausted")
        self._deferred_callbacks.append(item)

    async def _next_correlated_callback(
        self,
        request_id: int,
        deadline: float,
        pending: list[_Callback],
    ) -> _Callback:
        while True:
            if pending:
                item = pending.pop(0)
            else:
                item = await self._next_queued_callback(deadline)
            if not self._session.accept_callback(item.generation):
                continue
            if item.kind == "error" and item.request_id == -1:
                try:
                    await self._handle_global_error(item)
                except IbkrConnectionIntegrityError as error:
                    raise IbkrHistoricalDisconnected(str(error)) from error
                continue
            if item.request_id != request_id:
                self._defer_callback(item)
                continue
            return item

    async def _collect_raw(
        self,
        request_id: int,
        *,
        terminal_kind: str,
        timeout_seconds: float,
    ) -> list[_Callback]:
        if timeout_seconds <= 0:
            raise ValueError("IBKR contract reauthentication timeout must be positive")
        deadline = monotonic() + timeout_seconds
        pending = await self._begin_correlated_callbacks()
        collected: list[_Callback] = []
        try:
            while True:
                item = await self._next_correlated_callback(request_id, deadline, pending)
                collected.append(item)
                if item.kind == terminal_kind:
                    return collected
                if item.kind == "error" and _error_disposition(item) == "REQUEST_ERROR":
                    return collected
        except TimeoutError as error:
            if isinstance(error, IbkrRequestTimeout):
                raise
            raise IbkrRequestTimeout(collected) from error
        finally:
            self._end_correlated_callbacks(pending)

    async def _raise_for_error(self, item: _Callback) -> None:
        code = _error_code(item)
        disposition = _error_disposition(item)
        if disposition in _CONNECTION_ERROR_DISPOSITIONS:
            raise IbkrHistoricalDisconnected(_safe_error_detail(item))
        if disposition == "PACING_VIOLATION":
            raise IbkrHistoricalRetryableError(_safe_error_detail(item))
        if disposition in {"INFORMATIONAL", "FARM_DISCONNECTED"}:
            return
        if code in _CONTRACT_MISMATCH_ERROR_CODES:
            raise IbkrHistoricalTerminalError(
                IbkrTerminalDisposition.CONTRACT_IDENTITY_CHANGED,
                _safe_error_detail(item),
            )
        if code in _ENTITLEMENT_ERROR_CODES:
            raise IbkrHistoricalTerminalError(
                IbkrTerminalDisposition.ENTITLEMENT_UNAVAILABLE,
                _safe_error_detail(item),
            )
        if code in _INVALID_REQUEST_ERROR_CODES:
            raise IbkrHistoricalTerminalError(
                IbkrTerminalDisposition.INVALID_REQUEST,
                _safe_error_detail(item),
            )
        raise IbkrHistoricalTerminalError(
            IbkrTerminalDisposition.PROVIDER_REJECTED,
            _safe_error_detail(item),
        )

    def _domain_callback(
        self,
        connection: IbkrHistoricalConnection,
        request_id: int,
        kind: IbkrHistoricalCallbackKind,
        payload: Mapping[str, JsonValue],
        provider_callback: _Callback,
    ) -> IbkrHistoricalCallback:
        received_at = self._clock()
        require_utc(received_at, "IBKR historical callback clock")
        return IbkrHistoricalCallback(
            connection_session_id=connection.connection_session_id,
            provider_request_id=request_id,
            connection_generation=(
                provider_callback.generation
                if provider_callback.generation > 0
                else connection.connection_generation
            ),
            kind=kind,
            received_at=received_at.astimezone(UTC),
            payload=payload,
        )

    def _require_historical_connection(self) -> IbkrHistoricalConnection:
        if self._historical_connection is None:
            raise RuntimeError("IBKR historical adapter is not connected")
        return self._historical_connection

    def _require_client(self) -> Any:
        client = super()._require_client()
        return client


def _api_contract(fingerprint: IbkrContractFingerprint) -> object:
    contract = cast(Any, _contract(_query_for_fingerprint(fingerprint)))
    contract.conId = fingerprint.con_id
    if fingerprint.primary_exchange is not None:
        contract.primaryExchange = fingerprint.primary_exchange
    if fingerprint.contract_month is not None:
        contract.lastTradeDateOrContractMonth = fingerprint.contract_month
    if fingerprint.underlying_con_id is not None:
        contract.underConId = fingerprint.underlying_con_id
    return contract


def _query_for_fingerprint(fingerprint: IbkrContractFingerprint) -> Any:
    from qtrad.domain.identifiers import InstrumentId
    from qtrad.ports.ibkr_capability import IbkrContractQuery

    return IbkrContractQuery(
        instrument_id=InstrumentId("ibkr:historical"),
        symbol=fingerprint.symbol,
        security_type=fingerprint.security_type,
        exchange=fingerprint.exchange,
        currency=fingerprint.currency,
        local_symbol=fingerprint.local_symbol,
        trading_class=fingerprint.trading_class,
        multiplier=fingerprint.multiplier,
    )


def _fingerprint_from_details(value: object) -> IbkrContractFingerprint:
    evidence = _contract_evidence(value)
    return IbkrContractFingerprint(
        con_id=evidence.con_id,
        symbol=evidence.symbol,
        security_type=evidence.security_type,
        currency=evidence.currency,
        exchange=evidence.exchange,
        primary_exchange=evidence.primary_exchange,
        local_symbol=evidence.local_symbol,
        trading_class=evidence.trading_class,
        multiplier=evidence.multiplier,
        underlying_con_id=evidence.underlier_con_id,
        contract_month=evidence.contract_month,
    )


def _bar_payload(value: object) -> dict[str, JsonValue]:
    bar = cast(Any, value)
    timestamp = _epoch_value(bar.date, "IBKR historical bar date")
    payload: dict[str, JsonValue] = {
        "date": timestamp,
        "open": _decimal_text(bar.open, "IBKR historical bar open"),
        "high": _decimal_text(bar.high, "IBKR historical bar high"),
        "low": _decimal_text(bar.low, "IBKR historical bar low"),
        "close": _decimal_text(bar.close, "IBKR historical bar close"),
        "volume": _optional_number(getattr(bar, "volume", None), "IBKR historical bar volume"),
        "wap": _optional_decimal(getattr(bar, "wap", None), "IBKR historical bar WAP"),
        "count": _optional_number(getattr(bar, "count", None), "IBKR historical bar count"),
    }
    return payload


def _schedule_payload(values: tuple[object, ...]) -> dict[str, JsonValue]:
    if len(values) != 4:
        raise IbkrHistoricalIncomplete("IBKR historical schedule callback shape is invalid")
    start, end, time_zone, sessions = values
    zone_name = _safe_text(time_zone)
    payload_sessions: list[JsonValue] = []
    for value in cast(Sequence[object], sessions):
        session = cast(Any, value)
        raw_start = _member(session, "startDateTime")
        raw_end = _member(session, "endDateTime")
        raw_open = _member(session, "isOpen")
        entry: dict[str, JsonValue] = {
            "start": _schedule_time(raw_start, zone_name),
            "end": _schedule_time(raw_end, zone_name),
            "active": _bool_value(raw_open, "IBKR schedule isOpen"),
        }
        ref_date = _optional_member(session, "refDate")
        if ref_date is not None:
            entry["ref_date"] = _safe_text(ref_date)
        payload_sessions.append(entry)
    return {
        "start": _schedule_time(start, zone_name),
        "end": _schedule_time(end, zone_name),
        "time_zone": zone_name,
        "sessions": payload_sessions,
    }


def _member(value: object, name: str) -> object:
    if isinstance(value, Mapping):
        return cast(Mapping[str, object], value)[name]
    return getattr(value, name)


def _optional_member(value: object, name: str) -> object | None:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _epoch_value(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise IbkrHistoricalIncomplete(f"{field} must be an epoch")
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise IbkrHistoricalIncomplete(f"{field} must be timezone-aware")
        return int(value.timestamp())
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as error:
        raise IbkrHistoricalIncomplete(f"{field} must be an integer epoch") from error
    if parsed <= 0:
        raise IbkrHistoricalIncomplete(f"{field} must be positive")
    return parsed


def _schedule_time(value: object, time_zone: str) -> str:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = _safe_text(value)
        if text.isdigit():
            parsed = datetime.fromtimestamp(int(text), tz=UTC)
        else:
            parsed = _parse_schedule_text(text, time_zone)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise IbkrHistoricalIncomplete("IBKR schedule timestamp has no timezone")
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_schedule_text(value: str, time_zone: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
    if parsed is not None and parsed.tzinfo is not None and parsed.utcoffset() is not None:
        return parsed
    local_value = value.replace("T", " ")
    for pattern in ("%Y%m%d-%H:%M:%S", "%Y%m%d:%H:%M:%S", "%Y%m%d %H:%M:%S"):
        try:
            naive = datetime.strptime(local_value, pattern)
            return naive.replace(tzinfo=_zone(time_zone))
        except ValueError:
            continue
    raise IbkrHistoricalIncomplete("IBKR schedule timestamp is not parseable")


def _zone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as error:
        raise IbkrHistoricalIncomplete("IBKR schedule timezone is not recognized") from error


def _bool_value(value: object, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str) and value.lower() in {"true", "false"}:
        return value.lower() == "true"
    raise IbkrHistoricalIncomplete(f"{field} must be boolean")


def _decimal_text(value: object, field: str) -> str:
    if isinstance(value, bool) or value is None:
        raise IbkrHistoricalIncomplete(f"{field} must be a finite decimal")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise IbkrHistoricalIncomplete(f"{field} must be a finite decimal") from error
    if not parsed.is_finite():
        raise IbkrHistoricalIncomplete(f"{field} must be a finite decimal")
    text = format(parsed, "f").rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _optional_decimal(value: object, field: str) -> JsonValue:
    return None if value is None else _decimal_text(value, field)


def _optional_number(value: object, field: str) -> JsonValue:
    if value is None:
        return None
    if isinstance(value, bool):
        raise IbkrHistoricalIncomplete(f"{field} must be numeric")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise IbkrHistoricalIncomplete(f"{field} must be numeric") from error
    if not parsed.is_finite():
        raise IbkrHistoricalIncomplete(f"{field} must be numeric")
    if parsed == parsed.to_integral_value():
        return int(parsed)
    return format(parsed, "f")


def _safe_text(value: object) -> str:
    text = str(value)
    if not text or len(text) > 200 or any(character in text for character in "\r\n\x00"):
        raise IbkrHistoricalIncomplete("IBKR provider text field is not safely bounded")
    return text


def _completion_payload(values: tuple[object, ...]) -> dict[str, JsonValue]:
    if len(values) != 2:
        raise IbkrHistoricalIncomplete("IBKR historical completion callback shape is invalid")
    payload: dict[str, JsonValue] = {}
    if values[0] != "":
        payload["start"] = _safe_text(values[0])
    if values[1] != "":
        payload["end"] = _safe_text(values[1])
    return payload


def _error_payload(item: _Callback) -> dict[str, JsonValue]:
    code = _error_code(item)
    classification = _error_disposition(item)
    payload: dict[str, JsonValue] = {
        "error_code": code,
        "error_classification": classification,
        "diagnostic": _safe_error_diagnostic(item),
        "error_time": int(cast(int | str, item.values[0])),
    }
    if item.message_sha256 is not None:
        payload["message_sha256"] = item.message_sha256
    return payload


def _safe_error_diagnostic(item: _Callback) -> str:
    code = _error_code(item)
    classification = _error_disposition(item)
    expected = f"IBKR_{code}_{classification}"
    return expected


def _safe_error_detail(item: _Callback) -> str:
    return f"IBKR error code {_error_code(item)} classified {_error_disposition(item)}"


def _callback_error_codes(callbacks: Sequence[_Callback]) -> tuple[int, ...]:
    return tuple(sorted({_error_code(item) for item in callbacks if item.kind == "error"}))


def _callback_diagnostics(callbacks: Sequence[_Callback]) -> tuple[str, ...]:
    return tuple(
        sorted({_safe_error_diagnostic(item) for item in callbacks if item.kind == "error"})
    )


__all__ = [
    "OfficialIbkrHistoricalAdapter",
]
