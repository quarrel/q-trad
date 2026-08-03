"""Stage 5 canary orchestration and conservative request-profile freezing."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from itertools import count, pairwise
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

IBKR_CANARY_MAX_CALLBACKS_PER_REQUEST = 50_000
IBKR_CANARY_MAX_RETAINED_CALLBACK_BYTES = 4_000_000
IBKR_CANARY_EXCESSIVE_CLOSURE_REASON = "EXCESSIVE_OPERATIONAL_CLOSURE"


def _callback_encoded_upper_bound(item: IbkrHistoricalCallback) -> int:
    encoded = json.dumps(_callback_json(item), sort_keys=True, indent=2, ensure_ascii=True).encode(
        "utf-8"
    )
    return len(encoded) + (encoded.count(b"\n") * 8) + 64


def _callback_evidence_bytes(
    callbacks: Sequence[IbkrHistoricalCallback],
) -> int:
    return sum(_callback_encoded_upper_bound(item) for item in callbacks)


class _CanaryClosureLimit(Exception):
    """Raised before a canary request can make its evidence unwritable."""


@dataclass(slots=True)
class _CanaryEvidenceBudget:
    retained_callback_bytes: int = 0

    def reserve(self, item: IbkrHistoricalCallback) -> None:
        upper_bound = _callback_encoded_upper_bound(item)
        if self.retained_callback_bytes + upper_bound > IBKR_CANARY_MAX_RETAINED_CALLBACK_BYTES:
            raise _CanaryClosureLimit("IBKR canary retained callback evidence limit exceeded")
        self.retained_callback_bytes += upper_bound


@dataclass(slots=True)
class _CanaryCallbackCollector:
    budget: _CanaryEvidenceBudget
    callbacks: list[IbkrHistoricalCallback]

    def append(self, item: IbkrHistoricalCallback) -> None:
        if len(self.callbacks) >= IBKR_CANARY_MAX_CALLBACKS_PER_REQUEST:
            raise _CanaryClosureLimit("IBKR canary per-request callback count limit exceeded")
        self.budget.reserve(item)
        self.callbacks.append(item)


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
    expected_connection_session_id: UUID | None = None
    expected_provider_request_id: int | None = None
    expected_connection_generation: int | None = None
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
            "EXCESSIVE_CLOSURE",
            "INVALID",
            "NOT_RUN",
        }:
            raise ValueError("IBKR canary request status is unsupported")
        identity = (
            self.expected_connection_session_id,
            self.expected_provider_request_id,
            self.expected_connection_generation,
        )
        if any(value is not None for value in identity) and any(
            value is None for value in identity
        ):
            raise ValueError("IBKR canary expected transport identity must be complete")
        if self.status != "NOT_RUN" and any(value is None for value in identity):
            raise ValueError(
                "IBKR canary attempted request must retain expected transport identity"
            )
        if self.expected_provider_request_id is not None and self.expected_provider_request_id <= 0:
            raise ValueError("IBKR canary expected provider request ID must be positive")
        if (
            self.expected_connection_generation is not None
            and self.expected_connection_generation <= 0
        ):
            raise ValueError("IBKR canary expected connection generation must be positive")
        if self.callback_count != len(self.callbacks):
            raise ValueError("IBKR canary callback count does not match retained callbacks")
        if self.callbacks and any(value is None for value in identity):
            raise ValueError("IBKR canary callbacks require expected transport identity")
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
            "expected_connection_session_id": (
                str(self.expected_connection_session_id)
                if self.expected_connection_session_id is not None
                else None
            ),
            "expected_provider_request_id": self.expected_provider_request_id,
            "expected_connection_generation": self.expected_connection_generation,
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
    budget = _CanaryEvidenceBudget()
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
                budget=budget,
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
    evidence = replay_ibkr_historical_canary_evidence(evidence)
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
    budget: _CanaryEvidenceBudget,
) -> IbkrHistoricalCanaryCaseResult:
    results: list[IbkrHistoricalCanaryRequestResult] = []
    for kind in IbkrHistoricalRequestKind:
        request = _request_for_case(case, kind)
        provider_request_id = next(request_ids)
        collector = _CanaryCallbackCollector(budget=budget, callbacks=[])

        async def sink(
            item: IbkrHistoricalCallback,
            target: _CanaryCallbackCollector = collector,
        ) -> None:
            target.append(item)

        try:
            await adapter.request_historical(
                request,
                request_id=provider_request_id,
                connection_session_id=connection_session_id,
                connection_generation=connection_generation,
                callback=sink,
            )
        except _CanaryClosureLimit as error:
            result = _exception_result(
                request,
                collector.callbacks,
                "EXCESSIVE_CLOSURE",
                str(error),
                IBKR_CANARY_EXCESSIVE_CLOSURE_REASON,
                expected_connection_session_id=connection_session_id,
                expected_provider_request_id=provider_request_id,
                expected_connection_generation=connection_generation,
            )
        except IbkrHistoricalTerminalError as error:
            result = _exception_result(
                request,
                collector.callbacks,
                "ERROR",
                error.detail,
                f"PROVIDER_{error.disposition.value}",
                expected_connection_session_id=connection_session_id,
                expected_provider_request_id=provider_request_id,
                expected_connection_generation=connection_generation,
            )
        except IbkrHistoricalDisconnected:
            result = _exception_result(
                request,
                collector.callbacks,
                "DISCONNECTED",
                "IBKR_HISTORICAL_DISCONNECTED",
                "DISCONNECT_OR_GENERATION_INVALIDATION",
                expected_connection_session_id=connection_session_id,
                expected_provider_request_id=provider_request_id,
                expected_connection_generation=connection_generation,
            )
        except IbkrHistoricalRetryableError:
            result = _exception_result(
                request,
                collector.callbacks,
                "RETRYABLE_FAILURE",
                "IBKR_HISTORICAL_RETRYABLE_FAILURE",
                "THROTTLING_OR_RETRYABLE_PROVIDER_FAILURE",
                expected_connection_session_id=connection_session_id,
                expected_provider_request_id=provider_request_id,
                expected_connection_generation=connection_generation,
            )
        except TimeoutError:
            result = _exception_result(
                request,
                collector.callbacks,
                "TIMEOUT",
                "IBKR_HISTORICAL_REQUEST_TIMEOUT",
                "TIMEOUT",
                expected_connection_session_id=connection_session_id,
                expected_provider_request_id=provider_request_id,
                expected_connection_generation=connection_generation,
            )
        except IbkrHistoricalIncomplete:
            result = _exception_result(
                request,
                collector.callbacks,
                "INVALID",
                "IBKR_HISTORICAL_INCOMPLETE_RESPONSE",
                "DETERMINISTIC_RESULT_VERIFY_FAILURE",
                expected_connection_session_id=connection_session_id,
                expected_provider_request_id=provider_request_id,
                expected_connection_generation=connection_generation,
            )
        else:
            result = _verify_request_callbacks(
                request,
                collector.callbacks,
                expected_connection_session_id=connection_session_id,
                expected_provider_request_id=provider_request_id,
                expected_connection_generation=connection_generation,
            )
        results.append(result)
        stop_reason = result.stop_reason
        if stop_reason is not None:
            return IbkrHistoricalCanaryCaseResult(
                case=case,
                requests=tuple(results)
                + tuple(
                    _not_run_request(_request_for_case(case, remaining_kind), stop_reason)
                    for remaining_kind in IbkrHistoricalRequestKind
                    if remaining_kind not in {item.request.kind for item in results}
                ),
                status="FAILED",
                stop_reason=stop_reason,
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
    bar_size = "1 min" if kind is IbkrHistoricalRequestKind.MIDPOINT_BARS else "1 day"
    what_to_show = "MIDPOINT" if kind is IbkrHistoricalRequestKind.MIDPOINT_BARS else "SCHEDULE"
    format_date = 2
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
    *,
    expected_connection_session_id: UUID | None = None,
    expected_provider_request_id: int | None = None,
    expected_connection_generation: int | None = None,
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

    def finish(
        status: str,
        detail: str | None,
        stop_reason: str | None,
        *,
        bar_count: int = 0,
        schedule_session_count: int = 0,
    ) -> IbkrHistoricalCanaryRequestResult:
        return _result(
            request,
            callbacks,
            status,
            detail,
            stop_reason,
            error_codes,
            bar_count,
            schedule_session_count,
            expected_connection_session_id=expected_connection_session_id,
            expected_provider_request_id=expected_provider_request_id,
            expected_connection_generation=expected_connection_generation,
        )

    identity_error = _callback_identity_error(
        callbacks,
        expected_connection_session_id=expected_connection_session_id,
        expected_provider_request_id=expected_provider_request_id,
        expected_connection_generation=expected_connection_generation,
    )
    if identity_error is not None:
        return finish("INVALID", identity_error, "DETERMINISTIC_RESULT_VERIFY_FAILURE")
    if any(item.kind is IbkrHistoricalCallbackKind.ERROR for item in callbacks):
        return finish("ERROR", "provider returned an error callback", "PROVIDER_ERROR")
    completions = [item for item in callbacks if item.kind is IbkrHistoricalCallbackKind.COMPLETION]
    if len(completions) != 1:
        return finish(
            "INVALID",
            "canary completion callback count is not exactly one",
            "DETERMINISTIC_RESULT_VERIFY_FAILURE",
        )
    if request.kind is IbkrHistoricalRequestKind.MIDPOINT_BARS:
        if any(item.kind is IbkrHistoricalCallbackKind.SCHEDULE for item in callbacks):
            return finish(
                "INVALID",
                "midpoint request contains a schedule callback",
                "DETERMINISTIC_RESULT_VERIFY_FAILURE",
            )
        bars = [item for item in callbacks if item.kind is IbkrHistoricalCallbackKind.MIDPOINT_BAR]
        try:
            for item in bars:
                _verify_bar(item.payload)
        except ValueError as error:
            return finish("INVALID", str(error), "DETERMINISTIC_RESULT_VERIFY_FAILURE")
        if not bars:
            return finish("NO_DATA", "one-day canary returned no midpoint bars", "NO_DATA_RETURNED")
        return finish("SUCCESS", None, None, bar_count=len(bars))
    if any(item.kind is IbkrHistoricalCallbackKind.MIDPOINT_BAR for item in callbacks):
        return finish(
            "INVALID",
            "schedule request contains a midpoint bar callback",
            "DETERMINISTIC_RESULT_VERIFY_FAILURE",
        )
    schedules = [item for item in callbacks if item.kind is IbkrHistoricalCallbackKind.SCHEDULE]
    if len(schedules) != 1:
        return finish(
            "INVALID",
            "canary schedule callback count is not exactly one",
            "DETERMINISTIC_RESULT_VERIFY_FAILURE",
        )
    try:
        session_count = _verify_schedule(schedules[0].payload)
    except ValueError as error:
        return finish("INVALID", str(error), "INCONSISTENT_SCHEDULE")
    return finish("SUCCESS", None, None, schedule_session_count=session_count)


def _callback_identity_error(
    callbacks: Sequence[IbkrHistoricalCallback],
    *,
    expected_connection_session_id: UUID | None = None,
    expected_provider_request_id: int | None = None,
    expected_connection_generation: int | None = None,
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
    if (
        expected_connection_session_id is not None
        and first.connection_session_id != expected_connection_session_id
    ):
        return "canary callbacks do not match the expected connection session"
    if (
        expected_provider_request_id is not None
        and first.provider_request_id != expected_provider_request_id
    ):
        return "canary callbacks do not match the expected provider request"
    if (
        expected_connection_generation is not None
        and first.connection_generation != expected_connection_generation
    ):
        return "canary callbacks do not match the expected connection generation"
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
    *,
    expected_connection_session_id: UUID | None = None,
    expected_provider_request_id: int | None = None,
    expected_connection_generation: int | None = None,
) -> IbkrHistoricalCanaryRequestResult:
    if callbacks and expected_connection_session_id is None:
        first = callbacks[0]
        expected_connection_session_id = first.connection_session_id
        expected_provider_request_id = first.provider_request_id
        expected_connection_generation = first.connection_generation
    return IbkrHistoricalCanaryRequestResult(
        request=request,
        status=status,
        callback_count=len(callbacks),
        bar_count=bar_count,
        schedule_session_count=schedule_session_count,
        error_codes=error_codes,
        callbacks=tuple(_callback_json(item) for item in callbacks),
        expected_connection_session_id=expected_connection_session_id,
        expected_provider_request_id=expected_provider_request_id,
        expected_connection_generation=expected_connection_generation,
        detail=detail,
        stop_reason=stop_reason,
    )


def _exception_result(
    request: IbkrHistoricalRequest,
    callbacks: Sequence[IbkrHistoricalCallback],
    status: str,
    detail: str,
    stop_reason: str,
    *,
    expected_connection_session_id: UUID | None = None,
    expected_provider_request_id: int | None = None,
    expected_connection_generation: int | None = None,
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
    bar_count = sum(item.kind is IbkrHistoricalCallbackKind.MIDPOINT_BAR for item in callbacks)
    schedule_session_count = 0
    schedules = [item for item in callbacks if item.kind is IbkrHistoricalCallbackKind.SCHEDULE]
    if len(schedules) == 1:
        try:
            schedule_session_count = _verify_schedule(schedules[0].payload)
        except ValueError:
            schedule_session_count = 0
    return _result(
        request,
        callbacks,
        status,
        detail,
        stop_reason,
        error_codes,
        bar_count,
        schedule_session_count,
        expected_connection_session_id=expected_connection_session_id,
        expected_provider_request_id=expected_provider_request_id,
        expected_connection_generation=expected_connection_generation,
    )


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


def _callback_from_evidence(value: dict[str, JsonValue]) -> IbkrHistoricalCallback:
    expected_keys = {
        "connection_session_id",
        "provider_request_id",
        "connection_generation",
        "kind",
        "received_at",
        "payload",
    }
    if set(value) != expected_keys:
        raise ValueError("IBKR canary callback fields are not canonical")
    session_value = value["connection_session_id"]
    if not isinstance(session_value, str):
        raise ValueError("IBKR canary callback session ID is invalid")
    try:
        session_id = UUID(session_value)
    except ValueError as error:
        raise ValueError("IBKR canary callback session ID is invalid") from error
    if str(session_id) != session_value:
        raise ValueError("IBKR canary callback session ID is not canonical")
    provider_request_id = value["provider_request_id"]
    generation = value["connection_generation"]
    if (
        isinstance(provider_request_id, bool)
        or not isinstance(provider_request_id, int)
        or provider_request_id <= 0
        or isinstance(generation, bool)
        or not isinstance(generation, int)
        or generation <= 0
    ):
        raise ValueError("IBKR canary callback transport identity is invalid")
    kind_value = value["kind"]
    if not isinstance(kind_value, str):
        raise ValueError("IBKR canary callback kind is invalid")
    try:
        kind = IbkrHistoricalCallbackKind(kind_value)
    except ValueError as error:
        raise ValueError("IBKR canary callback kind is invalid") from error
    payload_value = value["payload"]
    if not isinstance(payload_value, dict):
        raise ValueError("IBKR canary callback payload is not an object")
    if any(key.lower() in {"message", "errorstring"} for key in payload_value):
        raise ValueError("IBKR canary callback contains raw provider diagnostic text")
    callback = IbkrHistoricalCallback(
        connection_session_id=session_id,
        provider_request_id=provider_request_id,
        connection_generation=generation,
        kind=kind,
        received_at=_utc_datetime(value["received_at"]),
        payload=payload_value,
    )
    if _callback_json(callback) != value:
        raise ValueError("IBKR canary callback is not canonical")
    return callback


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
    if len(cases) != len(IBKR_CANARY_GROUPS) * len(IBKR_CANARY_DURATIONS):
        raise ValueError("IBKR canary cases must contain exactly twelve cases")
    for group in IBKR_CANARY_GROUPS:
        group_cases = {case.duration: case for case in cases if case.group is group}
        if set(group_cases) != set(IBKR_CANARY_DURATIONS):
            raise ValueError("IBKR canary cases must cover all four durations per group")
        ordered = [group_cases[duration] for duration in IBKR_CANARY_DURATIONS]
        for previous, following in pairwise(ordered):
            if previous.interval_start != following.interval_end:
                raise ValueError("IBKR canary duration intervals must be adjacent")
    for duration in IBKR_CANARY_DURATIONS:
        intervals = {
            (case.interval_start, case.interval_end) for case in cases if case.duration == duration
        }
        if len(intervals) != 1:
            raise ValueError("IBKR canary duration intervals must be shared across groups")


def replay_ibkr_historical_canary_evidence(
    evidence: IbkrHistoricalCanaryEvidence,
) -> IbkrHistoricalCanaryEvidence:
    """Recompute canary claims from retained callbacks before any profile is frozen."""
    _validate_case_set(tuple(item.case for item in evidence.cases))
    reauthentication = _replay_reauthentication(evidence)
    retained_callback_bytes = 0
    next_provider_request_id = 1_000_000
    expected_session_id: UUID | None = None
    expected_generation: int | None = None
    expected_stop_reason: str | None = None
    for case_result in evidence.cases:
        reauth = reauthentication[case_result.case.fingerprint]
        _replay_case_result(case_result)
        for request_result in case_result.requests:
            retained_callback_bytes += _callback_evidence_bytes(
                tuple(_callback_from_evidence(value) for value in request_result.callbacks)
            )
            if request_result.status == "NOT_RUN":
                continue
            if request_result.expected_provider_request_id != next_provider_request_id:
                raise ValueError("IBKR canary provider request sequence is not replayable")
            if request_result.expected_connection_generation != reauth.connection_generation:
                raise ValueError("IBKR canary request generation is not replayable")
            if expected_session_id is None:
                expected_session_id = request_result.expected_connection_session_id
                expected_generation = request_result.expected_connection_generation
            elif (
                request_result.expected_connection_session_id != expected_session_id
                or request_result.expected_connection_generation != expected_generation
            ):
                raise ValueError("IBKR canary request transport session is not replayable")
            next_provider_request_id += 1
        if expected_stop_reason is None:
            if reauth.status != "MATCH":
                expected_stop_reason = f"CONTRACT_REAUTHENTICATION_{reauth.status}"
                if (
                    case_result.status != "BLOCKED"
                    or case_result.stop_reason != expected_stop_reason
                ):
                    raise ValueError("IBKR canary reauthentication stop propagation is invalid")
            elif case_result.stop_reason is not None:
                expected_stop_reason = case_result.stop_reason
                if case_result.status != "FAILED":
                    raise ValueError("IBKR canary failure status propagation is invalid")
            elif case_result.status != "SUCCESS":
                raise ValueError("IBKR canary successful case status is invalid")
        elif case_result.status != "NOT_RUN" or case_result.stop_reason != expected_stop_reason:
            raise ValueError("IBKR canary global stop propagation is invalid")
    if retained_callback_bytes > IBKR_CANARY_MAX_RETAINED_CALLBACK_BYTES:
        raise ValueError("IBKR canary retained callback evidence exceeds its bound")
    if evidence.stop_reason != expected_stop_reason:
        raise ValueError("IBKR canary evidence stop reason is not replayable")
    return evidence


def _replay_reauthentication(
    evidence: IbkrHistoricalCanaryEvidence,
) -> dict[IbkrContractFingerprint, IbkrContractReauthentication]:
    expected = tuple(dict.fromkeys(item.case.fingerprint for item in evidence.cases))
    observed = evidence.reauthentication
    if len(observed) != len(expected) or {item.expected for item in observed} != set(expected):
        raise ValueError("IBKR canary reauthentication coverage is invalid")
    result: dict[IbkrContractFingerprint, IbkrContractReauthentication] = {}
    for item in observed:
        if item.expected in result:
            raise ValueError("IBKR canary reauthentication identities are duplicated")
        if len(item.observed) == 1 and item.observed[0] == item.expected and not item.error_codes:
            expected_status = "MATCH"
        elif item.status == "TIMEOUT":
            expected_status = (
                "TIMEOUT"
                if (
                    not item.observed
                    and not item.error_codes
                    and item.diagnostics == ("IBKR_REAUTH_TIMEOUT",)
                )
                else "MISMATCH"
            )
        elif item.error_codes or item.diagnostics:
            expected_status = "ERROR"
        else:
            expected_status = "MISMATCH"
        if item.status != expected_status:
            raise ValueError("IBKR canary reauthentication status is not replayable")
        result[item.expected] = item
    return result


def _replay_case_result(result: IbkrHistoricalCanaryCaseResult) -> None:
    expected_requests = tuple(
        _request_for_case(result.case, kind) for kind in IbkrHistoricalRequestKind
    )
    if len(result.requests) != len(expected_requests):
        raise ValueError("IBKR canary case request coverage is invalid")
    for actual, expected in zip(result.requests, expected_requests, strict=True):
        if actual.request.as_json_value() != expected.as_json_value():
            raise ValueError("IBKR canary request identity does not match its case")
        _replay_request_result(actual, result.case)
    statuses = tuple(item.status for item in result.requests)
    if result.status == "SUCCESS":
        if statuses != ("SUCCESS", "SUCCESS") or result.stop_reason is not None:
            raise ValueError("IBKR canary successful case claim is not replayable")
    elif result.status == "FAILED":
        failures = [
            (index, item)
            for index, item in enumerate(result.requests)
            if item.status != "NOT_RUN" and item.stop_reason is not None
        ]
        if len(failures) != 1 or result.stop_reason != failures[0][1].stop_reason:
            raise ValueError("IBKR canary failed case stop reason is invalid")
        failure_index, failure = failures[0]
        if failure.status == "NOT_RUN" or any(
            item.stop_reason is not None for item in result.requests[:failure_index]
        ):
            raise ValueError("IBKR canary failed case request order is invalid")
        if any(
            item.status != "NOT_RUN" or item.stop_reason != result.stop_reason
            for item in result.requests[failure_index + 1 :]
        ):
            raise ValueError("IBKR canary failed case request propagation is invalid")
    elif result.status in {"BLOCKED", "NOT_RUN"}:
        if not result.stop_reason or any(
            item.status != "NOT_RUN" or item.stop_reason != result.stop_reason
            for item in result.requests
        ):
            raise ValueError("IBKR canary blocked case claim is not replayable")
    else:
        raise ValueError("IBKR canary case status is not replayable")


def _replay_request_result(
    result: IbkrHistoricalCanaryRequestResult,
    case: IbkrHistoricalCanaryCase,
) -> None:
    if result.status == "NOT_RUN":
        if (
            not result.stop_reason
            or result.callbacks
            or result.callback_count
            or result.bar_count
            or result.schedule_session_count
            or result.error_codes
            or any(
                value is not None
                for value in (
                    result.expected_connection_session_id,
                    result.expected_provider_request_id,
                    result.expected_connection_generation,
                )
            )
        ):
            raise ValueError("IBKR canary not-run request contains execution evidence")
        return
    identity = (
        result.expected_connection_session_id,
        result.expected_provider_request_id,
        result.expected_connection_generation,
    )
    if any(value is None for value in identity):
        raise ValueError("IBKR canary request is missing expected transport identity")
    callbacks = tuple(_callback_from_evidence(value) for value in result.callbacks)
    expected = _request_for_case(case, result.request.kind)
    if result.request.as_json_value() != expected.as_json_value():
        raise ValueError("IBKR canary request identity is not replayable")
    identity_error = _callback_identity_error(
        callbacks,
        expected_connection_session_id=result.expected_connection_session_id,
        expected_provider_request_id=result.expected_provider_request_id,
        expected_connection_generation=result.expected_connection_generation,
    )
    if identity_error is not None:
        raise ValueError(identity_error)
    bar_count, schedule_session_count, error_codes = _replay_callback_counts(
        result.request, callbacks
    )
    if (
        result.callback_count != len(callbacks)
        or result.bar_count != bar_count
        or result.schedule_session_count != schedule_session_count
        or result.error_codes != error_codes
    ):
        raise ValueError("IBKR canary callback counts or classifications are not replayable")
    if result.status in {"SUCCESS", "NO_DATA", "INVALID"}:
        derived = _verify_request_callbacks(
            result.request,
            callbacks,
            expected_connection_session_id=result.expected_connection_session_id,
            expected_provider_request_id=result.expected_provider_request_id,
            expected_connection_generation=result.expected_connection_generation,
        )
        if (
            derived.status != result.status
            or derived.bar_count != result.bar_count
            or derived.schedule_session_count != result.schedule_session_count
            or derived.error_codes != result.error_codes
            or derived.stop_reason != result.stop_reason
        ):
            raise ValueError("IBKR canary request status is not replayable")
        return
    if result.status == "ERROR" or result.status in {"DISCONNECTED", "RETRYABLE_FAILURE"}:
        if not any(item.kind is IbkrHistoricalCallbackKind.ERROR for item in callbacks):
            raise ValueError("IBKR canary provider outcome lacks an error callback")
        derived_status, derived_stop_reason = _replay_error_outcome(callbacks)
        if derived_status != result.status or derived_stop_reason != result.stop_reason:
            raise ValueError("IBKR canary provider outcome is not replayable")
        return
    if result.status == "TIMEOUT":
        if (
            result.stop_reason != "TIMEOUT"
            or any(item.kind is IbkrHistoricalCallbackKind.COMPLETION for item in callbacks)
            or any(item.kind is IbkrHistoricalCallbackKind.ERROR for item in callbacks)
        ):
            raise ValueError("IBKR canary timeout outcome is not replayable")
        return
    if result.status == "EXCESSIVE_CLOSURE":
        if (
            result.stop_reason != IBKR_CANARY_EXCESSIVE_CLOSURE_REASON
            or any(item.kind is IbkrHistoricalCallbackKind.COMPLETION for item in callbacks)
            or _callback_evidence_bytes(callbacks) > IBKR_CANARY_MAX_RETAINED_CALLBACK_BYTES
        ):
            raise ValueError("IBKR canary excessive closure outcome is not replayable")
        return
    raise ValueError("IBKR canary request status is not replayable")


def _replay_callback_counts(
    request: IbkrHistoricalRequest,
    callbacks: Sequence[IbkrHistoricalCallback],
) -> tuple[int, int, tuple[int, ...]]:
    if len(callbacks) > IBKR_CANARY_MAX_CALLBACKS_PER_REQUEST:
        raise ValueError("IBKR canary callback count exceeds its bound")
    error_codes: set[int] = set()
    for item in callbacks:
        if item.kind is not IbkrHistoricalCallbackKind.ERROR:
            continue
        code = item.payload.get("error_code")
        classification = item.payload.get("error_classification")
        if (
            isinstance(code, bool)
            or not isinstance(code, int)
            or code < 0
            or not isinstance(classification, str)
            or not classification
        ):
            raise ValueError("IBKR canary error callback classification is invalid")
        if any(key.lower() in {"message", "errorstring"} for key in item.payload):
            raise ValueError("IBKR canary error callback contains raw provider text")
        error_codes.add(code)
    if request.kind is IbkrHistoricalRequestKind.MIDPOINT_BARS:
        if any(item.kind is IbkrHistoricalCallbackKind.SCHEDULE for item in callbacks):
            raise ValueError("IBKR canary midpoint callback set contains a schedule")
        bars = [item for item in callbacks if item.kind is IbkrHistoricalCallbackKind.MIDPOINT_BAR]
        for item in bars:
            _verify_bar(item.payload)
        return len(bars), 0, tuple(sorted(error_codes))
    if any(item.kind is IbkrHistoricalCallbackKind.MIDPOINT_BAR for item in callbacks):
        raise ValueError("IBKR canary schedule callback set contains a midpoint bar")
    schedules = [item for item in callbacks if item.kind is IbkrHistoricalCallbackKind.SCHEDULE]
    if len(schedules) > 1:
        raise ValueError("IBKR canary schedule callback set contains multiple schedules")
    session_count = _verify_schedule(schedules[0].payload) if schedules else 0
    return 0, session_count, tuple(sorted(error_codes))


def _replay_error_outcome(
    callbacks: Sequence[IbkrHistoricalCallback],
) -> tuple[str, str]:
    connection_classifications = {
        "CONNECTION_LOST",
        "PORT_RESET",
        "CONNECTION_RESTORED_DATA_LOST",
    }
    for item in callbacks:
        if item.kind is not IbkrHistoricalCallbackKind.ERROR:
            continue
        classification = cast(str, item.payload["error_classification"])
        code = cast(int, item.payload["error_code"])
        if classification in connection_classifications:
            return "DISCONNECTED", "DISCONNECT_OR_GENERATION_INVALIDATION"
        if classification == "PACING_VIOLATION":
            return "RETRYABLE_FAILURE", "THROTTLING_OR_RETRYABLE_PROVIDER_FAILURE"
        if classification in {"INFORMATIONAL", "FARM_DISCONNECTED"}:
            continue
        if code in {200, 201}:
            return "ERROR", "PROVIDER_CONTRACT_IDENTITY_CHANGED"
        if code in {354, 10167, 10186}:
            return "ERROR", "PROVIDER_ENTITLEMENT_UNAVAILABLE"
        if code in {321, 322, 319}:
            return "ERROR", "PROVIDER_INVALID_REQUEST"
        return "ERROR", "PROVIDER_PROVIDER_REJECTED"
    return "ERROR", "PROVIDER_ERROR"


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
    "replay_ibkr_historical_canary_evidence",
    "run_ibkr_historical_canary",
]
