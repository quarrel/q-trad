"""Stage 5 canary orchestration and conservative request-profile freezing."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from itertools import count
from typing import Protocol, cast
from uuid import UUID

from qtrad.application.ibkr_historical import build_ibkr_historical_request_profile
from qtrad.domain.events import JsonValue
from qtrad.domain.ibkr_execution import (
    IbkrHistoricalCallback,
    IbkrHistoricalCallbackKind,
    IbkrHistoricalDisconnected,
    IbkrHistoricalIncomplete,
    IbkrHistoricalRetryableError,
    IbkrHistoricalTerminalError,
)
from qtrad.domain.ibkr_historical import (
    IbkrContractFingerprint,
    IbkrHistoricalPacingPolicy,
    IbkrHistoricalRequest,
    IbkrHistoricalRequestKind,
    IbkrHistoricalRequestProfile,
    duration_timedelta,
    sha256_json,
    utc_text,
)
from qtrad.domain.identifiers import InstrumentId
from qtrad.domain.instruments import AssetClass
from qtrad.domain.time import require_utc
from qtrad.ports.ibkr_historical import (
    IbkrContractReauthentication,
    IbkrHistoricalDataPort,
)

IBKR_CANARY_CONTRACT = "qtrad-ibkr-historical-canary-v1"
IBKR_CANARY_SCHEMA_VERSION = 1
IBKR_CANARY_DURATIONS = ("1 D", "1 W", "2 W", "4 W")
IBKR_CANARY_GROUPS = (AssetClass.FX, AssetClass.INDEX, AssetClass.COMMODITY)
CallableClock = Callable[[], datetime]


class IbkrHistoricalCanaryPort(IbkrHistoricalDataPort, Protocol):
    """Historical transport plus the account-gated contract reauthentication operation."""

    async def reauthenticate_contracts(
        self,
        fingerprints: Sequence[IbkrContractFingerprint],
    ) -> tuple[IbkrContractReauthentication, ...]: ...


@dataclass(frozen=True, slots=True)
class IbkrHistoricalCanaryCase:
    """One adjacent request window for one representative product group."""

    group: AssetClass
    instrument_id: InstrumentId
    fingerprint: IbkrContractFingerprint
    duration: str
    interval_start: datetime
    interval_end: datetime

    def __post_init__(self) -> None:
        if self.group not in IBKR_CANARY_GROUPS:
            raise ValueError("IBKR canary group must be FX, INDEX or COMMODITY")
        if self.duration not in IBKR_CANARY_DURATIONS:
            raise ValueError("IBKR canary duration is not one of the frozen test durations")
        require_utc(self.interval_start, "IBKR canary interval start")
        require_utc(self.interval_end, "IBKR canary interval end")
        if self.interval_end <= self.interval_start:
            raise ValueError("IBKR canary interval must be non-empty")
        if self.interval_end - self.interval_start != duration_timedelta(self.duration):
            raise ValueError("IBKR canary interval must equal its provider duration")
        if any(
            value.second or value.microsecond for value in (self.interval_start, self.interval_end)
        ):
            raise ValueError("IBKR canary interval must align to UTC minutes")

    def identity_payload(self) -> dict[str, JsonValue]:
        return {
            "group": self.group.value,
            "instrument_id": str(self.instrument_id),
            "fingerprint": self.fingerprint.as_json_value(),
            "duration": self.duration,
            "interval_start": utc_text(self.interval_start),
            "interval_end": utc_text(self.interval_end),
        }

    def as_json_value(self) -> dict[str, JsonValue]:
        return self.identity_payload()


@dataclass(frozen=True, slots=True)
class IbkrHistoricalCanaryRequestResult:
    """Independently classified callback evidence for one canary request."""

    request: IbkrHistoricalRequest
    status: str
    callback_count: int
    bar_count: int
    schedule_session_count: int
    error_codes: tuple[int, ...]
    callbacks: tuple[dict[str, JsonValue], ...]
    detail: str | None = None
    stop_reason: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {
            "SUCCESS",
            "NO_DATA",
            "ERROR",
            "TIMEOUT",
            "DISCONNECTED",
            "RETRYABLE_FAILURE",
            "INVALID",
            "NOT_RUN",
        }:
            raise ValueError("IBKR canary request status is unsupported")
        if self.callback_count != len(self.callbacks):
            raise ValueError("IBKR canary callback count does not match retained callbacks")
        if self.bar_count < 0 or self.schedule_session_count < 0:
            raise ValueError("IBKR canary evidence counts cannot be negative")
        if any(code < 0 for code in self.error_codes):
            raise ValueError("IBKR canary provider error codes must be non-negative")
        for value in (self.detail, self.stop_reason):
            if value is not None and (not value or len(value) > 2_000):
                raise ValueError("IBKR canary diagnostic fields must be bounded")

    def as_json_value(self) -> dict[str, JsonValue]:
        return {
            "request": self.request.as_json_value(),
            "status": self.status,
            "callback_count": self.callback_count,
            "bar_count": self.bar_count,
            "schedule_session_count": self.schedule_session_count,
            "error_codes": list(self.error_codes),
            "callbacks": list(self.callbacks),
            "detail": self.detail,
            "stop_reason": self.stop_reason,
        }


@dataclass(frozen=True, slots=True)
class IbkrHistoricalCanaryCaseResult:
    """Both frozen request kinds for one canary case."""

    case: IbkrHistoricalCanaryCase
    requests: tuple[IbkrHistoricalCanaryRequestResult, ...]
    status: str
    stop_reason: str | None = None

    def __post_init__(self) -> None:
        if len(self.requests) != len(IbkrHistoricalRequestKind):
            raise ValueError("IBKR canary case must retain bar and schedule request results")
        if self.status not in {"SUCCESS", "BLOCKED", "FAILED", "NOT_RUN"}:
            raise ValueError("IBKR canary case status is unsupported")
        if self.stop_reason is not None and (not self.stop_reason or len(self.stop_reason) > 2_000):
            raise ValueError("IBKR canary case stop reason must be bounded")

    def as_json_value(self) -> dict[str, JsonValue]:
        return {
            "case": self.case.as_json_value(),
            "requests": [item.as_json_value() for item in self.requests],
            "status": self.status,
            "stop_reason": self.stop_reason,
        }


@dataclass(frozen=True, slots=True)
class IbkrHistoricalCanaryEvidence:
    """Create-only, hash-bound Stage 5 evidence used by the request profile."""

    runtime_sha256: str
    selection_sha256: str
    started_at: datetime
    completed_at: datetime
    reauthentication: tuple[IbkrContractReauthentication, ...]
    cases: tuple[IbkrHistoricalCanaryCaseResult, ...]
    stop_reason: str | None
    evidence_sha256: str

    def __post_init__(self) -> None:
        _require_sha256(self.runtime_sha256, "IBKR canary runtime hash")
        _require_sha256(self.selection_sha256, "IBKR canary selection hash")
        _require_sha256(self.evidence_sha256, "IBKR canary evidence hash")
        require_utc(self.started_at, "IBKR canary started_at")
        require_utc(self.completed_at, "IBKR canary completed_at")
        if self.completed_at < self.started_at:
            raise ValueError("IBKR canary completed_at cannot precede started_at")
        if self.stop_reason is not None and (not self.stop_reason or len(self.stop_reason) > 2_000):
            raise ValueError("IBKR canary stop reason must be bounded")
        if self.evidence_sha256 != sha256_json(self.identity_payload()):
            raise ValueError("IBKR canary evidence hash does not match canonical content")

    def identity_payload(self) -> dict[str, JsonValue]:
        return {
            "contract": IBKR_CANARY_CONTRACT,
            "schema_version": IBKR_CANARY_SCHEMA_VERSION,
            "runtime_sha256": self.runtime_sha256,
            "selection_sha256": self.selection_sha256,
            "started_at": utc_text(self.started_at),
            "completed_at": utc_text(self.completed_at),
            "reauthentication": [item.as_json_value() for item in self.reauthentication],
            "cases": [item.as_json_value() for item in self.cases],
            "stop_reason": self.stop_reason,
        }

    def as_json_value(self) -> dict[str, JsonValue]:
        return {**self.identity_payload(), "evidence_sha256": self.evidence_sha256}


def build_adjacent_ibkr_canary_cases(
    representatives: Mapping[AssetClass, tuple[InstrumentId, IbkrContractFingerprint]],
    *,
    anchor_end: datetime,
) -> tuple[IbkrHistoricalCanaryCase, ...]:
    """Build one adjacent, non-identical interval per group and test duration."""
    require_utc(anchor_end, "IBKR canary anchor end")
    if any(group not in representatives for group in IBKR_CANARY_GROUPS):
        raise ValueError("IBKR canary representatives must cover all product groups")
    cursor = anchor_end
    cases: list[IbkrHistoricalCanaryCase] = []
    for duration in IBKR_CANARY_DURATIONS:
        interval_start = cursor - duration_timedelta(duration)
        for group in IBKR_CANARY_GROUPS:
            instrument_id, fingerprint = representatives[group]
            cases.append(
                IbkrHistoricalCanaryCase(
                    group=group,
                    instrument_id=instrument_id,
                    fingerprint=fingerprint,
                    duration=duration,
                    interval_start=interval_start,
                    interval_end=cursor,
                )
            )
        cursor = interval_start
    return tuple(cases)


async def run_ibkr_historical_canary(
    adapter: IbkrHistoricalCanaryPort,
    cases: Sequence[IbkrHistoricalCanaryCase],
    *,
    runtime_sha256: str,
    selection_sha256: str,
    clock: CallableClock = lambda: datetime.now(UTC),
) -> IbkrHistoricalCanaryEvidence:
    """Reauthenticate representatives, run bounded canaries and stop on first unsafe result."""
    ordered_cases = tuple(cases)
    if not ordered_cases:
        raise ValueError("IBKR canary requires at least one case")
    _require_sha256(runtime_sha256, "IBKR canary runtime hash")
    _require_sha256(selection_sha256, "IBKR canary selection hash")
    _validate_case_set(ordered_cases)

    started_at = _clock_now(clock, "IBKR canary start time")
    connection = await adapter.connect()
    try:
        unique_fingerprints = tuple(dict.fromkeys(case.fingerprint for case in ordered_cases))
        reauthentication = await adapter.reauthenticate_contracts(unique_fingerprints)
        reauth_by_fingerprint = {item.expected: item for item in reauthentication}
        cases_out: list[IbkrHistoricalCanaryCaseResult] = []
        stop_reason: str | None = None
        request_ids = count(1_000_000)
        for case in ordered_cases:
            reauth = reauth_by_fingerprint[case.fingerprint]
            if stop_reason is not None:
                cases_out.append(_not_run_case(case, stop_reason))
                continue
            if reauth.status != "MATCH":
                stop_reason = f"CONTRACT_REAUTHENTICATION_{reauth.status}"
                cases_out.append(_blocked_case(case, stop_reason))
                continue
            result = await _run_case(
                adapter,
                case,
                connection_session_id=connection.connection_session_id,
                connection_generation=connection.connection_generation,
                request_ids=request_ids,
            )
            cases_out.append(result)
            if result.stop_reason is not None:
                stop_reason = result.stop_reason
        completed_at = _clock_now(clock, "IBKR canary completion time")
    finally:
        await adapter.disconnect()

    identity = {
        "contract": IBKR_CANARY_CONTRACT,
        "schema_version": IBKR_CANARY_SCHEMA_VERSION,
        "runtime_sha256": runtime_sha256,
        "selection_sha256": selection_sha256,
        "started_at": utc_text(started_at),
        "completed_at": utc_text(completed_at),
        "reauthentication": [item.as_json_value() for item in reauthentication],
        "cases": [item.as_json_value() for item in cases_out],
        "stop_reason": stop_reason,
    }
    return IbkrHistoricalCanaryEvidence(
        runtime_sha256=runtime_sha256,
        selection_sha256=selection_sha256,
        started_at=started_at,
        completed_at=completed_at,
        reauthentication=reauthentication,
        cases=tuple(cases_out),
        stop_reason=stop_reason,
        evidence_sha256=sha256_json(identity),
    )


def freeze_ibkr_request_profile_from_canary(
    evidence: IbkrHistoricalCanaryEvidence,
    *,
    canary_evidence_filename: str,
    frozen_by: str,
    frozen_at: datetime,
    maximum_in_flight_requests: int,
    request_timeout_seconds: int,
    retry_count: int,
    duplicate_request_protection: str,
    pacing_policy: IbkrHistoricalPacingPolicy,
) -> IbkrHistoricalRequestProfile:
    """Freeze the largest successful prefix independently demonstrated for each group."""
    if evidence.stop_reason is not None:
        raise ValueError("IBKR request profile cannot freeze from stopped canary evidence")
    require_utc(frozen_at, "IBKR request profile frozen_at")
    by_group_duration = {(item.case.group, item.case.duration): item for item in evidence.cases}
    bar_duration_by_group: dict[AssetClass, str] = {}
    for group in IBKR_CANARY_GROUPS:
        selected: str | None = None
        for duration in IBKR_CANARY_DURATIONS:
            result = by_group_duration.get((group, duration))
            if result is None or result.status != "SUCCESS":
                break
            request_statuses = {item.request.kind: item.status for item in result.requests}
            if any(request_statuses.get(kind) != "SUCCESS" for kind in IbkrHistoricalRequestKind):
                break
            selected = duration
        if selected is None:
            raise ValueError(f"IBKR canary has no reliable duration for {group.value}")
        bar_duration_by_group[group] = selected

    schedule_duration: str | None = None
    for duration in IBKR_CANARY_DURATIONS:
        if all(
            (result := by_group_duration.get((group, duration))) is not None
            and result.status == "SUCCESS"
            and next(
                item
                for item in result.requests
                if item.request.kind is IbkrHistoricalRequestKind.SCHEDULE
            ).status
            == "SUCCESS"
            for group in IBKR_CANARY_GROUPS
        ):
            schedule_duration = duration
        else:
            break
    if schedule_duration is None:
        raise ValueError("IBKR canary has no reliable common schedule duration")

    permitted_bar = tuple(
        duration
        for duration in IBKR_CANARY_DURATIONS
        if duration in set(bar_duration_by_group.values())
    )
    permitted_schedule = tuple(
        duration
        for duration in IBKR_CANARY_DURATIONS
        if duration_timedelta(duration) <= duration_timedelta(schedule_duration)
    )
    return build_ibkr_historical_request_profile(
        canary_evidence_filename=canary_evidence_filename,
        canary_evidence_sha256=evidence.evidence_sha256,
        frozen_by=frozen_by,
        frozen_at=frozen_at,
        permitted_bar_durations=permitted_bar,
        permitted_schedule_durations=permitted_schedule,
        bar_duration_by_asset_class=bar_duration_by_group,
        schedule_duration=schedule_duration,
        maximum_in_flight_requests=maximum_in_flight_requests,
        request_timeout_seconds=request_timeout_seconds,
        retry_count=retry_count,
        duplicate_request_protection=duplicate_request_protection,
        pacing_policy=pacing_policy,
    )


async def _run_case(
    adapter: IbkrHistoricalCanaryPort,
    case: IbkrHistoricalCanaryCase,
    *,
    connection_session_id: UUID,
    connection_generation: int,
    request_ids: count[int],
) -> IbkrHistoricalCanaryCaseResult:
    results: list[IbkrHistoricalCanaryRequestResult] = []
    for kind in IbkrHistoricalRequestKind:
        request = _request_for_case(case, kind)
        callbacks: list[IbkrHistoricalCallback] = []

        async def sink(
            item: IbkrHistoricalCallback,
            target: list[IbkrHistoricalCallback] = callbacks,
        ) -> None:
            target.append(item)

        try:
            await adapter.request_historical(
                request,
                request_id=next(request_ids),
                connection_session_id=connection_session_id,
                connection_generation=connection_generation,
                callback=sink,
            )
        except IbkrHistoricalTerminalError as error:
            result = _exception_result(
                request,
                callbacks,
                "ERROR",
                error.detail,
                f"PROVIDER_{error.disposition.value}",
            )
        except IbkrHistoricalDisconnected as error:
            result = _exception_result(
                request,
                callbacks,
                "DISCONNECTED",
                str(error),
                "DISCONNECT_OR_GENERATION_INVALIDATION",
            )
        except IbkrHistoricalRetryableError as error:
            result = _exception_result(
                request,
                callbacks,
                "RETRYABLE_FAILURE",
                str(error),
                "THROTTLING_OR_RETRYABLE_PROVIDER_FAILURE",
            )
        except TimeoutError as error:
            result = _exception_result(
                request,
                callbacks,
                "TIMEOUT",
                str(error),
                "TIMEOUT",
            )
        except IbkrHistoricalIncomplete as error:
            result = _exception_result(
                request,
                callbacks,
                "INVALID",
                str(error),
                "DETERMINISTIC_RESULT_VERIFY_FAILURE",
            )
        else:
            result = _verify_request_callbacks(request, callbacks)
        results.append(result)
        if result.stop_reason is not None:
            return IbkrHistoricalCanaryCaseResult(
                case=case,
                requests=tuple(results)
                + tuple(
                    _not_run_request(_request_for_case(case, remaining_kind), "CASE_STOPPED")
                    for remaining_kind in IbkrHistoricalRequestKind
                    if remaining_kind not in {item.request.kind for item in results}
                ),
                status="FAILED",
                stop_reason=result.stop_reason,
            )
    return IbkrHistoricalCanaryCaseResult(
        case=case,
        requests=tuple(results),
        status="SUCCESS",
    )


def _request_for_case(
    case: IbkrHistoricalCanaryCase,
    kind: IbkrHistoricalRequestKind,
) -> IbkrHistoricalRequest:
    bar_size = "1 min" if kind is IbkrHistoricalRequestKind.MIDPOINT_BARS else None
    what_to_show = "MIDPOINT" if kind is IbkrHistoricalRequestKind.MIDPOINT_BARS else None
    format_date = 2 if kind is IbkrHistoricalRequestKind.MIDPOINT_BARS else None
    identity: dict[str, JsonValue] = {
        "instrument_id": str(case.instrument_id),
        "fingerprint": case.fingerprint.as_json_value(),
        "kind": kind.value,
        "interval_start": utc_text(case.interval_start),
        "interval_end": utc_text(case.interval_end),
        "end_date_time": case.interval_end.strftime("%Y%m%d-%H:%M:%S UTC"),
        "duration": case.duration,
        "bar_size": bar_size,
        "what_to_show": what_to_show,
        "use_rth": False,
        "format_date": format_date,
        "keep_up_to_date": False,
    }
    return IbkrHistoricalRequest(
        instrument_id=case.instrument_id,
        fingerprint=case.fingerprint,
        kind=kind,
        interval_start=case.interval_start,
        interval_end=case.interval_end,
        end_date_time=case.interval_end.strftime("%Y%m%d-%H:%M:%S UTC"),
        duration=case.duration,
        bar_size=bar_size,
        what_to_show=what_to_show,
        use_rth=False,
        format_date=format_date,
        keep_up_to_date=False,
        request_sha256=sha256_json(identity),
    )


def _verify_request_callbacks(
    request: IbkrHistoricalRequest,
    callbacks: Sequence[IbkrHistoricalCallback],
) -> IbkrHistoricalCanaryRequestResult:
    error_codes = tuple(
        sorted(
            {
                int(cast(int, item.payload["error_code"]))
                for item in callbacks
                if item.kind is IbkrHistoricalCallbackKind.ERROR and "error_code" in item.payload
            }
        )
    )
    identity_error = _callback_identity_error(callbacks)
    if identity_error is not None:
        return _result(
            request,
            callbacks,
            "INVALID",
            identity_error,
            "DETERMINISTIC_RESULT_VERIFY_FAILURE",
            error_codes,
        )
    if any(item.kind is IbkrHistoricalCallbackKind.ERROR for item in callbacks):
        return _result(
            request,
            callbacks,
            "ERROR",
            "provider returned an error callback",
            "PROVIDER_ERROR",
            error_codes,
        )
    completions = [item for item in callbacks if item.kind is IbkrHistoricalCallbackKind.COMPLETION]
    if len(completions) != 1:
        return _result(
            request,
            callbacks,
            "INVALID",
            "canary completion callback count is not exactly one",
            "DETERMINISTIC_RESULT_VERIFY_FAILURE",
            error_codes,
        )
    if request.kind is IbkrHistoricalRequestKind.MIDPOINT_BARS:
        bars = [item for item in callbacks if item.kind is IbkrHistoricalCallbackKind.MIDPOINT_BAR]
        try:
            for item in bars:
                _verify_bar(item.payload)
        except ValueError as error:
            return _result(
                request,
                callbacks,
                "INVALID",
                str(error),
                "DETERMINISTIC_RESULT_VERIFY_FAILURE",
                error_codes,
            )
        if not bars:
            return _result(
                request,
                callbacks,
                "NO_DATA",
                "one-day canary returned no midpoint bars",
                "NO_DATA_RETURNED",
                error_codes,
            )
        return _result(request, callbacks, "SUCCESS", None, None, error_codes, len(bars), 0)
    schedules = [item for item in callbacks if item.kind is IbkrHistoricalCallbackKind.SCHEDULE]
    if len(schedules) != 1:
        return _result(
            request,
            callbacks,
            "INVALID",
            "canary schedule callback count is not exactly one",
            "DETERMINISTIC_RESULT_VERIFY_FAILURE",
            error_codes,
        )
    try:
        session_count = _verify_schedule(schedules[0].payload)
    except ValueError as error:
        return _result(
            request,
            callbacks,
            "INVALID",
            str(error),
            "INCONSISTENT_SCHEDULE",
            error_codes,
        )
    return _result(request, callbacks, "SUCCESS", None, None, error_codes, 0, session_count)


def _callback_identity_error(
    callbacks: Sequence[IbkrHistoricalCallback],
) -> str | None:
    if not callbacks:
        return None
    first = callbacks[0]
    if any(
        item.connection_session_id != first.connection_session_id
        or item.provider_request_id != first.provider_request_id
        or item.connection_generation != first.connection_generation
        for item in callbacks[1:]
    ):
        return "canary callbacks are not one correlated session/request/generation"
    return None


def _verify_bar(payload: Mapping[str, JsonValue]) -> None:
    required = ("date", "open", "high", "low", "close")
    if any(key not in payload for key in required):
        raise ValueError("canary midpoint bar lacks required OHLC evidence")
    timestamp = payload["date"]
    if isinstance(timestamp, bool) or not isinstance(timestamp, int):
        raise ValueError("canary midpoint bar date is not an epoch")
    values: dict[str, Decimal] = {}
    for key in ("open", "high", "low", "close"):
        value = payload[key]
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, ValueError) as error:
            raise ValueError(f"canary midpoint bar {key} is not numeric") from error
        if not parsed.is_finite():
            raise ValueError(f"canary midpoint bar {key} is not finite")
        values[key] = parsed
    if (
        values["low"] < 0
        or values["high"] < values["low"]
        or values["open"] < values["low"]
        or values["close"] < values["low"]
        or values["open"] > values["high"]
        or values["close"] > values["high"]
    ):
        raise ValueError("canary midpoint bar contains invalid OHLC")


def _verify_schedule(payload: Mapping[str, JsonValue]) -> int:
    sessions = payload.get("sessions")
    if not isinstance(sessions, list):
        raise ValueError("canary schedule has no structural sessions array")
    intervals: list[tuple[datetime, datetime, bool]] = []
    for value in sessions:
        if not isinstance(value, dict):
            raise ValueError("canary schedule session is not an object")
        start = _utc_datetime(value.get("start"))
        end = _utc_datetime(value.get("end"))
        active = value.get("active")
        if not isinstance(active, bool) or end <= start:
            raise ValueError("canary schedule session interval is invalid")
        intervals.append((start, end, active))
    for index, (start, end, active) in enumerate(intervals):
        for other_start, other_end, other_active in intervals[index + 1 :]:
            if other_start < end and other_end > start and other_active != active:
                raise ValueError("canary schedule has conflicting overlapping sessions")
    return len(intervals)


def _result(
    request: IbkrHistoricalRequest,
    callbacks: Sequence[IbkrHistoricalCallback],
    status: str,
    detail: str | None,
    stop_reason: str | None,
    error_codes: tuple[int, ...],
    bar_count: int = 0,
    schedule_session_count: int = 0,
) -> IbkrHistoricalCanaryRequestResult:
    return IbkrHistoricalCanaryRequestResult(
        request=request,
        status=status,
        callback_count=len(callbacks),
        bar_count=bar_count,
        schedule_session_count=schedule_session_count,
        error_codes=error_codes,
        callbacks=tuple(_callback_json(item) for item in callbacks),
        detail=detail,
        stop_reason=stop_reason,
    )


def _exception_result(
    request: IbkrHistoricalRequest,
    callbacks: Sequence[IbkrHistoricalCallback],
    status: str,
    detail: str,
    stop_reason: str,
) -> IbkrHistoricalCanaryRequestResult:
    error_codes = tuple(
        sorted(
            {
                int(cast(int, item.payload["error_code"]))
                for item in callbacks
                if item.kind is IbkrHistoricalCallbackKind.ERROR and "error_code" in item.payload
            }
        )
    )
    return _result(request, callbacks, status, detail, stop_reason, error_codes)


def _not_run_request(
    request: IbkrHistoricalRequest,
    reason: str,
) -> IbkrHistoricalCanaryRequestResult:
    return _result(request, (), "NOT_RUN", reason, reason, ())


def _blocked_case(
    case: IbkrHistoricalCanaryCase,
    reason: str,
) -> IbkrHistoricalCanaryCaseResult:
    return IbkrHistoricalCanaryCaseResult(
        case=case,
        requests=tuple(
            _not_run_request(_request_for_case(case, kind), reason)
            for kind in IbkrHistoricalRequestKind
        ),
        status="BLOCKED",
        stop_reason=reason,
    )


def _not_run_case(
    case: IbkrHistoricalCanaryCase,
    reason: str,
) -> IbkrHistoricalCanaryCaseResult:
    return IbkrHistoricalCanaryCaseResult(
        case=case,
        requests=tuple(
            _not_run_request(_request_for_case(case, kind), reason)
            for kind in IbkrHistoricalRequestKind
        ),
        status="NOT_RUN",
        stop_reason=reason,
    )


def _callback_json(item: IbkrHistoricalCallback) -> dict[str, JsonValue]:
    return {
        "connection_session_id": str(item.connection_session_id),
        "provider_request_id": item.provider_request_id,
        "connection_generation": item.connection_generation,
        "kind": item.kind.value,
        "received_at": utc_text(item.received_at),
        "payload": dict(item.payload),
    }


def _utc_datetime(value: JsonValue | None) -> datetime:
    if not isinstance(value, str):
        raise ValueError("canary schedule timestamp must be UTC text")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("canary schedule timestamp is not ISO-8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError("canary schedule timestamp is not UTC")
    return parsed.astimezone(UTC)


def _validate_case_set(cases: Sequence[IbkrHistoricalCanaryCase]) -> None:
    identities = [(case.group, case.duration) for case in cases]
    if len(set(identities)) != len(identities):
        raise ValueError("IBKR canary group and duration identities must be unique")
    if set(case.group for case in cases) != set(IBKR_CANARY_GROUPS):
        raise ValueError("IBKR canary cases must cover all product groups")
    for group in IBKR_CANARY_GROUPS:
        observed = {case.duration for case in cases if case.group is group}
        if observed != set(IBKR_CANARY_DURATIONS):
            raise ValueError("IBKR canary cases must cover all four durations per group")


def _clock_now(clock: CallableClock, label: str) -> datetime:
    value = clock()
    require_utc(value, label)
    return value.astimezone(UTC)


def _require_sha256(value: str, field: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} must be a lowercase SHA-256")


__all__ = [
    "IBKR_CANARY_CONTRACT",
    "IBKR_CANARY_DURATIONS",
    "IBKR_CANARY_GROUPS",
    "IbkrHistoricalCanaryCase",
    "IbkrHistoricalCanaryCaseResult",
    "IbkrHistoricalCanaryEvidence",
    "IbkrHistoricalCanaryRequestResult",
    "build_adjacent_ibkr_canary_cases",
    "freeze_ibkr_request_profile_from_canary",
    "run_ibkr_historical_canary",
]
