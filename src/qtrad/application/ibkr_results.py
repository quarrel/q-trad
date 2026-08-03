"""Stage 4 IBKR result construction and independent replay logic."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import cast
from uuid import UUID

from qtrad.domain.events import JsonValue
from qtrad.domain.ibkr_execution import (
    IbkrAttemptStatus,
    IbkrHistoricalCallbackKind,
    IbkrRequestStatus,
    IbkrTerminalDisposition,
)
from qtrad.domain.ibkr_historical import (
    HISTORICAL_PLAN_CONTRACT,
    IbkrHistoricalPlan,
    IbkrHistoricalRequest,
    IbkrHistoricalRequestKind,
)
from qtrad.domain.ibkr_results import (
    HISTORICAL_RESULT_CONTRACT,
    REQUEST_RESULT_CONTRACT,
    IbkrHistoricalAggregateResult,
    IbkrHistoricalAttemptEvidence,
    IbkrHistoricalCallbackEvidence,
    IbkrHistoricalChildReference,
    IbkrHistoricalCompletionEvidence,
    IbkrHistoricalExecutionSnapshot,
    IbkrHistoricalRequestResult,
    IbkrHistoricalRequestSnapshot,
    IbkrHistoricalResultArtifact,
    canonical_json_bytes,
    sha256_bytes,
)
from qtrad.ports.ibkr_historical import IbkrHistoricalPublicationStore

_MINUTE = timedelta(minutes=1)
_EPOCH_KEYS = ("date", "time", "timestamp")
_OHLC_KEYS = ("open", "high", "low", "close")
_SESSION_START_KEYS = ("startDateTime", "start", "start_time", "interval_start")
_SESSION_END_KEYS = ("endDateTime", "end", "end_time", "interval_end")
_SESSION_ACTIVE_KEYS = ("isOpen", "is_open", "active")


def build_ibkr_historical_result_artifact(
    plan: IbkrHistoricalPlan,
    snapshot: IbkrHistoricalExecutionSnapshot,
) -> IbkrHistoricalResultArtifact:
    """Build request and aggregate evidence from one exact PostgreSQL snapshot."""

    _validate_snapshot_plan(plan, snapshot)
    request_by_hash = {item.request_sha256: item for item in snapshot.requests}
    expected_hashes = {item.request_sha256 for item in plan.requests}
    if set(request_by_hash) != expected_hashes:
        raise ValueError("IBKR publication snapshot request closure differs from the plan")
    attempts_by_request = _group_by(snapshot.attempts, key=lambda item: item.request_sha256)
    callbacks_by_attempt = _group_by(snapshot.callbacks, key=lambda item: item.attempt_id)
    markers_by_attempt = _group_by(snapshot.completion_markers, key=lambda item: item.attempt_id)
    results: list[IbkrHistoricalRequestResult] = []
    for request in sorted(plan.requests, key=lambda item: item.request_sha256):
        request_snapshot = request_by_hash[request.request_sha256]
        if request_snapshot.request_payload != request.as_json_value():
            raise ValueError("IBKR stored request payload differs from the authenticated plan")
        results.append(
            _build_request_result(
                request,
                request_snapshot,
                tuple(attempts_by_request.get(request.request_sha256, ())),
                callbacks_by_attempt,
                markers_by_attempt,
            )
        )
    aggregate = build_ibkr_historical_aggregate_result(
        plan,
        snapshot.plan.plan_bytes,
        tuple(results),
    )
    return IbkrHistoricalResultArtifact(
        plan=plan,
        plan_bytes=snapshot.plan.plan_bytes,
        request_results=tuple(results),
        aggregate=aggregate,
    )


def build_ibkr_historical_aggregate_result(
    plan: IbkrHistoricalPlan,
    plan_bytes: bytes,
    request_results: Sequence[IbkrHistoricalRequestResult],
) -> IbkrHistoricalAggregateResult:
    """Derive the aggregate child closure and summaries without database access."""

    if len(request_results) != len(plan.requests):
        raise ValueError("IBKR aggregate result count differs from the plan")
    results_by_hash = {item.request_sha256: item for item in request_results}
    if set(results_by_hash) != {item.request_sha256 for item in plan.requests}:
        raise ValueError("IBKR aggregate result identities differ from the plan")
    plan_ref = IbkrHistoricalChildReference(
        path="plan.json",
        contract=HISTORICAL_PLAN_CONTRACT,
        semantic_sha256=plan.plan_sha256,
        bytes_sha256=sha256_bytes(plan_bytes),
    )
    child_refs = tuple(
        IbkrHistoricalChildReference(
            path=f"requests/{result.request_sha256}.json",
            contract=REQUEST_RESULT_CONTRACT,
            semantic_sha256=result.result_sha256,
            bytes_sha256=sha256_bytes(canonical_json_bytes(result.as_json_value())),
        )
        for result in sorted(request_results, key=lambda item: item.request_sha256)
    )
    coverage, entitlement = _aggregate_summaries(plan, tuple(results_by_hash.values()))
    identity = {
        "contract": HISTORICAL_RESULT_CONTRACT,
        "schema_version": 1,
        "plan": plan_ref.as_json_value(),
        "runtime_sha256": plan.runtime_sha256,
        "request_results": [item.as_json_value() for item in child_refs],
        "coverage_summary": coverage,
        "entitlement_summary": entitlement,
    }
    return IbkrHistoricalAggregateResult(
        plan=plan_ref,
        runtime_sha256=plan.runtime_sha256,
        request_results=child_refs,
        coverage_summary=coverage,
        entitlement_summary=entitlement,
        aggregate_sha256=_sha256_json(cast(dict[str, JsonValue], identity)),
    )


def replay_ibkr_historical_request_result(
    request: IbkrHistoricalRequest,
    result: IbkrHistoricalRequestResult,
) -> None:
    """Reconstruct one request result from its published raw closure."""

    if result.plan_sha256 == "" or result.request_sha256 != request.request_sha256:
        raise ValueError("IBKR request-result identity does not match its plan request")
    if result.request_payload != request.as_json_value():
        raise ValueError("IBKR request-result request payload differs from its plan request")
    attempts = tuple(sorted(result.attempts, key=lambda item: item.attempt_ordinal))
    if tuple(item.attempt_ordinal for item in attempts) != tuple(range(1, len(attempts) + 1)):
        raise ValueError("IBKR request-result attempt ordinals are not contiguous")
    attempt_by_id = {item.attempt_id: item for item in attempts}
    if len(attempt_by_id) != len(attempts):
        raise ValueError("IBKR request-result attempt identities are duplicated")
    for callback in result.callbacks:
        attempt = attempt_by_id.get(callback.attempt_id)
        if attempt is None:
            raise ValueError("IBKR request-result contains an orphan callback")
        if callback.closure_eligible and not _callback_matches_attempt(callback, attempt):
            raise ValueError("eligible IBKR callback does not belong to its attempt")
    for marker in result.completion_markers:
        attempt = attempt_by_id.get(marker.attempt_id)
        if attempt is None:
            raise ValueError("IBKR request-result contains an orphan completion marker")
        if marker.closure_eligible and not _completion_matches_attempt(marker, attempt):
            raise ValueError("eligible IBKR completion does not belong to its attempt")
        if not any(
            callback.attempt_id == marker.attempt_id
            and callback.sequence == marker.sequence
            and callback.kind is IbkrHistoricalCallbackKind.COMPLETION
            for callback in result.callbacks
        ):
            raise ValueError("IBKR completion marker has no matching raw completion callback")
    selected = attempt_by_id.get(result.selected_attempt_id)
    if selected is None:
        raise ValueError("IBKR request-result selected attempt is absent")
    if selected.terminal_at is None:
        raise ValueError("IBKR request-result selected attempt is unfinished")
    if result.request_status is IbkrRequestStatus.SUCCEEDED:
        if (
            selected.status is not IbkrAttemptStatus.SUCCEEDED
            or selected.terminal_disposition is not IbkrTerminalDisposition.SUCCEEDED
        ):
            raise ValueError("successful IBKR request-result selected attempt is not successful")
    elif (
        selected.status is not IbkrAttemptStatus.TERMINAL_FAILURE
        or selected.terminal_disposition is None
        or selected.terminal_disposition is IbkrTerminalDisposition.SUCCEEDED
    ):
        raise ValueError("terminal IBKR request-result selected attempt is not terminal failure")
    selected_markers = _eligible_markers(result, selected)
    if result.request_status is IbkrRequestStatus.SUCCEEDED and len(selected_markers) != 1:
        raise ValueError("successful IBKR request-result requires one eligible completion")
    completion = selected_markers[0] if selected_markers else None
    accepted_callbacks = _accepted_callbacks(result.callbacks, selected, completion)
    if request.kind is IbkrHistoricalRequestKind.MIDPOINT_BARS:
        expected_rows = (
            _normalise_bars(request, accepted_callbacks)
            if result.request_status is IbkrRequestStatus.SUCCEEDED
            else ()
        )
        if result.request_status is IbkrRequestStatus.SUCCEEDED and not expected_rows:
            raise ValueError("successful IBKR bar result has no accepted rows")
        if result.sessions or result.session_state is not None:
            raise ValueError("IBKR bar result must not contain schedule state")
        if tuple(result.accepted_rows) != expected_rows:
            raise ValueError("IBKR request-result accepted rows do not replay")
    else:
        expected_sessions, expected_state = (
            _normalise_schedule(request, accepted_callbacks)
            if result.request_status is IbkrRequestStatus.SUCCEEDED
            else ((), "UNKNOWN")
        )
        if tuple(result.sessions) != expected_sessions:
            raise ValueError("IBKR request-result sessions do not replay")
        if result.session_state != expected_state:
            raise ValueError("IBKR request-result session state does not replay")
        if result.accepted_rows:
            raise ValueError("IBKR schedule result must not contain bar rows")
    expected_start = min(item.started_at for item in attempts)
    if result.acquisition_started_at != expected_start:
        raise ValueError("IBKR request-result acquisition start does not replay")
    if result.acquisition_completed_at != selected.terminal_at:
        raise ValueError("IBKR request-result acquisition completion does not replay")
    expected_retry_history = _retry_history(attempts)
    if tuple(result.retry_history) != expected_retry_history:
        raise ValueError("IBKR request-result retry history does not replay")
    expected_error = _error_classification(result.request_status, selected)
    if result.error_classification != expected_error:
        raise ValueError("IBKR request-result error classification does not replay")


def replay_ibkr_historical_aggregate_result(
    plan: IbkrHistoricalPlan,
    plan_bytes: bytes,
    request_results: Sequence[IbkrHistoricalRequestResult],
    aggregate: IbkrHistoricalAggregateResult,
) -> None:
    """Recompute the aggregate result and require exact equality."""

    expected_requests = tuple(sorted(plan.requests, key=lambda item: item.request_sha256))
    expected_hashes = {item.request_sha256 for item in expected_requests}
    actual_results = tuple(sorted(request_results, key=lambda item: item.request_sha256))
    if (
        len(actual_results) != len(expected_requests)
        or {item.request_sha256 for item in actual_results} != expected_hashes
    ):
        raise ValueError("IBKR aggregate replay request closure differs from the plan")
    for request, result in zip(expected_requests, actual_results, strict=True):
        replay_ibkr_historical_request_result(request, result)
    expected = build_ibkr_historical_aggregate_result(plan, plan_bytes, request_results)
    if expected.as_json_value() != aggregate.as_json_value():
        raise ValueError("IBKR aggregate result does not replay from its children")


async def publish_ibkr_historical_results(
    *,
    plan: IbkrHistoricalPlan,
    store: IbkrHistoricalPublicationStore,
    published_at: datetime,
) -> IbkrHistoricalExecutionSnapshot:
    """Read one database snapshot for callers that need a publisher orchestration hook."""

    snapshot = await store.read_ibkr_historical_execution(plan_sha256=plan.plan_sha256)
    if snapshot.plan.plan_sha256 != plan.plan_sha256:
        raise ValueError("IBKR publication snapshot plan identity differs")
    if published_at.tzinfo is None or published_at.utcoffset() != UTC.utcoffset(published_at):
        raise ValueError("IBKR publication time must be UTC")
    return snapshot


def _build_request_result(
    request: IbkrHistoricalRequest,
    request_snapshot: IbkrHistoricalRequestSnapshot,
    attempts: tuple[IbkrHistoricalAttemptEvidence, ...],
    callbacks_by_attempt: Mapping[UUID, Sequence[IbkrHistoricalCallbackEvidence]],
    markers_by_attempt: Mapping[UUID, Sequence[IbkrHistoricalCompletionEvidence]],
) -> IbkrHistoricalRequestResult:
    if request_snapshot.status not in {IbkrRequestStatus.SUCCEEDED, IbkrRequestStatus.TERMINAL}:
        raise ValueError("IBKR result publication requires a terminal request")
    attempts = tuple(sorted(attempts, key=lambda item: item.attempt_ordinal))
    if len(attempts) != request_snapshot.attempt_count:
        raise ValueError("IBKR request attempt count differs from its snapshot")
    selected_id = request_snapshot.selected_attempt_id
    if selected_id is None:
        raise ValueError("IBKR terminal request has no selected attempt")
    selected = next((item for item in attempts if item.attempt_id == selected_id), None)
    if selected is None:
        raise ValueError("IBKR selected attempt is absent from the publication snapshot")
    if selected.terminal_at is None:
        raise ValueError("IBKR selected attempt is unfinished")
    if request_snapshot.status is IbkrRequestStatus.SUCCEEDED:
        if (
            selected.status is not IbkrAttemptStatus.SUCCEEDED
            or selected.terminal_disposition is not IbkrTerminalDisposition.SUCCEEDED
        ):
            raise ValueError("successful IBKR request has an invalid selected attempt")
        disposition = IbkrTerminalDisposition.SUCCEEDED
    else:
        if (
            selected.status is not IbkrAttemptStatus.TERMINAL_FAILURE
            or selected.terminal_disposition in {None, IbkrTerminalDisposition.SUCCEEDED}
        ):
            raise ValueError("terminal IBKR request has an invalid selected attempt")
        disposition = cast(IbkrTerminalDisposition, selected.terminal_disposition)
    callbacks = tuple(
        sorted(
            (
                callback
                for attempt in attempts
                for callback in callbacks_by_attempt.get(attempt.attempt_id, ())
            ),
            key=lambda item: (str(item.attempt_id), item.sequence, item.callback_id),
        )
    )
    markers = tuple(
        sorted(
            (
                marker
                for attempt in attempts
                for marker in markers_by_attempt.get(attempt.attempt_id, ())
            ),
            key=lambda item: (str(item.attempt_id), item.sequence, item.marker_id),
        )
    )
    selected_markers = _eligible_markers_for_evidence(markers, selected)
    if request_snapshot.status is IbkrRequestStatus.SUCCEEDED and len(selected_markers) != 1:
        raise ValueError("successful IBKR request has no unique eligible completion marker")
    completion = selected_markers[0] if selected_markers else None
    accepted_callbacks = _accepted_callbacks(callbacks, selected, completion)
    if request.kind is IbkrHistoricalRequestKind.MIDPOINT_BARS:
        rows = (
            _normalise_bars(request, accepted_callbacks)
            if request_snapshot.status is IbkrRequestStatus.SUCCEEDED
            else ()
        )
        if request_snapshot.status is IbkrRequestStatus.SUCCEEDED and not rows:
            raise ValueError("successful IBKR bar request returned no accepted rows")
        sessions: tuple[dict[str, JsonValue], ...] = ()
        session_state = None
    else:
        rows = ()
        sessions, session_state = (
            _normalise_schedule(request, accepted_callbacks)
            if request_snapshot.status is IbkrRequestStatus.SUCCEEDED
            else ((), "UNKNOWN")
        )
    start = min(item.started_at for item in attempts)
    error = _error_classification(request_snapshot.status, selected)
    identity = _request_identity(
        plan_sha256=request_snapshot.plan_sha256,
        request_sha256=request_snapshot.request_sha256,
        request_payload=request_snapshot.request_payload,
        request_status=request_snapshot.status,
        terminal_disposition=disposition,
        selected_attempt_id=selected_id,
        attempts=attempts,
        callbacks=callbacks,
        completion_markers=markers,
        accepted_rows=rows,
        sessions=sessions,
        session_state=session_state,
        acquisition_started_at=start,
        acquisition_completed_at=selected.terminal_at,
        retry_history=_retry_history(attempts),
        error_classification=error,
    )
    return IbkrHistoricalRequestResult(
        plan_sha256=request_snapshot.plan_sha256,
        request_sha256=request_snapshot.request_sha256,
        request_payload=request_snapshot.request_payload,
        request_status=request_snapshot.status,
        terminal_disposition=disposition,
        selected_attempt_id=selected_id,
        attempts=attempts,
        callbacks=callbacks,
        completion_markers=markers,
        accepted_rows=rows,
        sessions=sessions,
        session_state=session_state,
        acquisition_started_at=start,
        acquisition_completed_at=selected.terminal_at,
        retry_history=_retry_history(attempts),
        error_classification=error,
        result_sha256=_sha256_json(identity),
    )


def _validate_snapshot_plan(
    plan: IbkrHistoricalPlan, snapshot: IbkrHistoricalExecutionSnapshot
) -> None:
    if snapshot.plan.plan_sha256 != plan.plan_sha256:
        raise ValueError("IBKR publication snapshot plan identity differs")
    try:
        stored_payload = json.loads(snapshot.plan.plan_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("IBKR stored plan bytes are not valid JSON") from error
    if stored_payload != plan.as_json_value() or snapshot.plan.plan_payload != stored_payload:
        raise ValueError("IBKR stored plan bytes do not match the authenticated plan")
    if snapshot.plan.plan_bytes_sha256 != sha256_bytes(snapshot.plan.plan_bytes):
        raise ValueError("IBKR stored plan bytes digest does not replay")
    if snapshot.plan.plan_payload.get("plan_sha256") != plan.plan_sha256:
        raise ValueError("IBKR stored plan payload has an invalid identity")


def _aggregate_summaries(
    plan: IbkrHistoricalPlan,
    results: Sequence[IbkrHistoricalRequestResult],
) -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
    result_by_hash = {item.request_sha256: item for item in results}
    per_instrument: dict[str, dict[str, JsonValue]] = {}
    disposition_counts: dict[str, int] = defaultdict(int)
    eligible: list[JsonValue] = []
    for request in plan.requests:
        result = result_by_hash[request.request_sha256]
        instrument = str(request.instrument_id)
        entry = per_instrument.setdefault(
            instrument,
            {
                "bar_request_status": None,
                "bar_disposition": None,
                "bar_rows": 0,
                "schedule_request_status": None,
                "schedule_disposition": None,
                "schedule_sessions": 0,
            },
        )
        if request.kind is IbkrHistoricalRequestKind.MIDPOINT_BARS:
            entry["bar_request_status"] = result.request_status.value
            entry["bar_disposition"] = result.terminal_disposition.value
            entry["bar_rows"] = len(result.accepted_rows)
        else:
            entry["schedule_request_status"] = result.request_status.value
            entry["schedule_disposition"] = result.terminal_disposition.value
            entry["schedule_sessions"] = len(result.sessions)
        disposition_counts[result.terminal_disposition.value] += 1
    for instrument, entry in sorted(per_instrument.items()):
        if (
            entry["bar_request_status"] == IbkrRequestStatus.SUCCEEDED.value
            and entry["schedule_request_status"] == IbkrRequestStatus.SUCCEEDED.value
            and int(cast(int, entry["bar_rows"])) > 0
        ):
            eligible.append(instrument)
    coverage: dict[str, JsonValue] = {
        "planned_request_count": len(plan.requests),
        "terminal_request_count": len(results),
        "successful_request_count": sum(
            item.request_status is IbkrRequestStatus.SUCCEEDED for item in results
        ),
        "accepted_bar_row_count": sum(len(item.accepted_rows) for item in results),
        "provider_session_count": sum(len(item.sessions) for item in results),
        "by_instrument": {
            instrument: value for instrument, value in sorted(per_instrument.items())
        },
    }
    entitlement: dict[str, JsonValue] = {
        "disposition_counts": dict(sorted(disposition_counts.items())),
        "provider_history_eligible_instruments": eligible,
    }
    return coverage, entitlement


def _accepted_callbacks(
    callbacks: Sequence[IbkrHistoricalCallbackEvidence],
    selected: IbkrHistoricalAttemptEvidence,
    completion: IbkrHistoricalCompletionEvidence | None,
) -> tuple[IbkrHistoricalCallbackEvidence, ...]:
    upper_bound = completion.sequence if completion is not None else None
    return tuple(
        callback
        for callback in callbacks
        if callback.attempt_id == selected.attempt_id
        and callback.closure_eligible
        and _callback_matches_attempt(callback, selected)
        and (upper_bound is None or callback.sequence <= upper_bound)
    )


def _eligible_markers_for_evidence(
    markers: Sequence[IbkrHistoricalCompletionEvidence],
    selected: IbkrHistoricalAttemptEvidence,
) -> tuple[IbkrHistoricalCompletionEvidence, ...]:
    return tuple(
        marker
        for marker in markers
        if marker.attempt_id == selected.attempt_id
        and marker.closure_eligible
        and _completion_matches_attempt(marker, selected)
    )


def _eligible_markers(
    result: IbkrHistoricalRequestResult,
    selected: IbkrHistoricalAttemptEvidence,
) -> tuple[IbkrHistoricalCompletionEvidence, ...]:
    return _eligible_markers_for_evidence(result.completion_markers, selected)


def _callback_matches_attempt(
    callback: IbkrHistoricalCallbackEvidence,
    attempt: IbkrHistoricalAttemptEvidence,
) -> bool:
    return (
        callback.connection_session_id == attempt.connection_session_id
        and callback.provider_request_id == attempt.provider_request_id
        and callback.connection_generation == attempt.connection_generation
    )


def _completion_matches_attempt(
    marker: IbkrHistoricalCompletionEvidence,
    attempt: IbkrHistoricalAttemptEvidence,
) -> bool:
    return (
        marker.connection_session_id == attempt.connection_session_id
        and marker.provider_request_id == attempt.provider_request_id
        and marker.connection_generation == attempt.connection_generation
    )


def _normalise_bars(
    request: IbkrHistoricalRequest,
    callbacks: Sequence[IbkrHistoricalCallbackEvidence],
) -> tuple[dict[str, JsonValue], ...]:
    by_start: dict[datetime, dict[str, JsonValue]] = {}
    for callback in callbacks:
        if callback.kind is not IbkrHistoricalCallbackKind.MIDPOINT_BAR:
            continue
        timestamp = _provider_epoch(callback.payload, "historical bar timestamp")
        if timestamp < request.interval_start or timestamp >= request.interval_end:
            continue
        if timestamp.second != 0 or timestamp.microsecond != 0:
            raise ValueError("IBKR historical bar timestamp is not minute-aligned")
        values = {
            key: _canonical_decimal(callback.payload[key], f"historical bar {key}")
            for key in _OHLC_KEYS
        }
        decimals = {key: Decimal(value) for key, value in values.items()}
        if (
            decimals["low"] < 0
            or decimals["high"] < decimals["low"]
            or decimals["open"] < decimals["low"]
            or decimals["close"] < decimals["low"]
            or decimals["open"] > decimals["high"]
            or decimals["close"] > decimals["high"]
        ):
            raise ValueError("IBKR historical callback contains invalid OHLC")
        row: dict[str, JsonValue] = {
            "bar_start": timestamp.isoformat().replace("+00:00", "Z"),
            "bar_end": min(timestamp + _MINUTE, request.interval_end)
            .isoformat()
            .replace("+00:00", "Z"),
            "open": values["open"],
            "high": values["high"],
            "low": values["low"],
            "close": values["close"],
            "volume": callback.payload.get("volume"),
            "wap": callback.payload.get("wap"),
            "count": callback.payload.get("count"),
            "callback_sequence": callback.sequence,
        }
        previous = by_start.get(timestamp)
        if previous is not None:
            if {key: value for key, value in previous.items() if key != "callback_sequence"} != {
                key: value for key, value in row.items() if key != "callback_sequence"
            }:
                raise ValueError("IBKR historical callback contains conflicting duplicate OHLC")
            continue
        by_start[timestamp] = row
    return tuple(by_start[key] for key in sorted(by_start))


def _normalise_schedule(
    request: IbkrHistoricalRequest,
    callbacks: Sequence[IbkrHistoricalCallbackEvidence],
) -> tuple[tuple[dict[str, JsonValue], ...], str]:
    sessions: list[dict[str, JsonValue]] = []
    declared_activity: list[bool] = []
    for callback in callbacks:
        if callback.kind is not IbkrHistoricalCallbackKind.SCHEDULE:
            continue
        payload = callback.payload
        session_values = payload.get("sessions")
        if session_values is None:
            active = _optional_bool(payload, _SESSION_ACTIVE_KEYS)
            if active is None:
                raise ValueError("IBKR schedule callback has no structural session evidence")
            declared_activity.append(active)
            continue
        if not isinstance(session_values, list):
            raise ValueError("IBKR schedule sessions must be an array")
        if not session_values:
            declared_activity.append(False)
            continue
        for raw_session in session_values:
            if not isinstance(raw_session, Mapping):
                raise ValueError("IBKR schedule session must be an object")
            session = cast(Mapping[str, JsonValue], raw_session)
            start_value = _first_present(session, _SESSION_START_KEYS)
            end_value = _first_present(session, _SESSION_END_KEYS)
            active_value = _first_present(session, _SESSION_ACTIVE_KEYS)
            if start_value is None or end_value is None or active_value is None:
                raise ValueError("IBKR schedule session lacks required fields")
            start = _provider_datetime(start_value, "schedule session start")
            end = _provider_datetime(end_value, "schedule session end")
            if end <= start:
                raise ValueError("IBKR schedule session interval is invalid")
            active = _bool_value(active_value, "schedule session active")
            clipped_start = max(start, request.interval_start)
            clipped_end = min(end, request.interval_end)
            if clipped_start >= clipped_end:
                continue
            declared_activity.append(active)
            sessions.append(
                {
                    "interval_start": clipped_start.isoformat().replace("+00:00", "Z"),
                    "interval_end": clipped_end.isoformat().replace("+00:00", "Z"),
                    "active": active,
                    "callback_sequence": callback.sequence,
                    "provider_session": dict(session),
                }
            )
    if not callbacks or not any(
        callback.kind is IbkrHistoricalCallbackKind.SCHEDULE for callback in callbacks
    ):
        raise ValueError("successful IBKR schedule request has no schedule callback")
    if not declared_activity:
        raise ValueError("IBKR schedule callback has no declared activity")
    state = "ACTIVE" if any(declared_activity) else "INACTIVE"
    sessions.sort(
        key=lambda item: (
            str(item["interval_start"]),
            cast(int, item["callback_sequence"]),
        )
    )
    return tuple(sessions), state


def _provider_epoch(payload: Mapping[str, JsonValue], field: str) -> datetime:
    value = _first_present(payload, _EPOCH_KEYS)
    if value is None:
        raise ValueError(f"{field} is missing")
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an epoch value")
    if isinstance(value, int):
        epoch = value
    elif isinstance(value, str) and value.isdigit():
        epoch = int(value)
    else:
        raise ValueError(f"{field} must be an integer epoch value")
    try:
        return datetime.fromtimestamp(epoch, tz=UTC)
    except (OverflowError, OSError, ValueError) as error:
        raise ValueError(f"{field} is outside the UTC timestamp range") from error


def _provider_datetime(value: JsonValue, field: str) -> datetime:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be UTC epoch or ISO-8601")
    if isinstance(value, int):
        try:
            return datetime.fromtimestamp(value, tz=UTC)
        except (OverflowError, OSError, ValueError) as error:
            raise ValueError(f"{field} is outside the UTC timestamp range") from error
    if not isinstance(value, str):
        raise ValueError(f"{field} must be UTC epoch or ISO-8601")
    if value.isdigit():
        return _provider_datetime(int(value), field)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} must be UTC epoch or ISO-8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError(f"{field} must use UTC")
    return parsed


def _canonical_decimal(value: JsonValue, field: str) -> str:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{field} must be a finite decimal")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{field} must be a finite decimal") from error
    if not parsed.is_finite():
        raise ValueError(f"{field} must be a finite decimal")
    text = format(parsed, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _retry_history(
    attempts: Sequence[IbkrHistoricalAttemptEvidence],
) -> tuple[dict[str, JsonValue], ...]:
    return tuple(
        {
            "attempt_id": str(item.attempt_id),
            "attempt_ordinal": item.attempt_ordinal,
            "status": item.status.value,
            "terminal_at": (
                None
                if item.terminal_at is None
                else item.terminal_at.isoformat().replace("+00:00", "Z")
            ),
            "terminal_disposition": (
                None if item.terminal_disposition is None else item.terminal_disposition.value
            ),
        }
        for item in sorted(attempts, key=lambda value: value.attempt_ordinal)
    )


def _error_classification(
    status: IbkrRequestStatus,
    selected: IbkrHistoricalAttemptEvidence,
) -> dict[str, JsonValue] | None:
    if status is IbkrRequestStatus.SUCCEEDED:
        return None
    if selected.terminal_disposition is None:
        raise ValueError("terminal IBKR attempt lacks an error disposition")
    return {
        "disposition": selected.terminal_disposition.value,
        "detail": selected.detail,
    }


def _request_identity(
    *,
    plan_sha256: str,
    request_sha256: str,
    request_payload: dict[str, JsonValue],
    request_status: IbkrRequestStatus,
    terminal_disposition: IbkrTerminalDisposition,
    selected_attempt_id: UUID,
    attempts: Sequence[IbkrHistoricalAttemptEvidence],
    callbacks: Sequence[IbkrHistoricalCallbackEvidence],
    completion_markers: Sequence[IbkrHistoricalCompletionEvidence],
    accepted_rows: Sequence[dict[str, JsonValue]],
    sessions: Sequence[dict[str, JsonValue]],
    session_state: str | None,
    acquisition_started_at: datetime,
    acquisition_completed_at: datetime,
    retry_history: Sequence[dict[str, JsonValue]],
    error_classification: dict[str, JsonValue] | None,
) -> dict[str, JsonValue]:
    return {
        "contract": REQUEST_RESULT_CONTRACT,
        "schema_version": 1,
        "plan_sha256": plan_sha256,
        "request_sha256": request_sha256,
        "request_payload": request_payload,
        "request_status": request_status.value,
        "terminal_disposition": terminal_disposition.value,
        "selected_attempt_id": str(selected_attempt_id),
        "attempts": [item.as_json_value() for item in attempts],
        "callbacks": [item.as_json_value() for item in callbacks],
        "completion_markers": [item.as_json_value() for item in completion_markers],
        "accepted_rows": [cast(JsonValue, item) for item in accepted_rows],
        "sessions": [cast(JsonValue, item) for item in sessions],
        "session_state": session_state,
        "acquisition_started_at": _utc_text(acquisition_started_at),
        "acquisition_completed_at": _utc_text(acquisition_completed_at),
        "retry_history": [cast(JsonValue, item) for item in retry_history],
        "error_classification": error_classification,
    }


def _group_by[T, K](values: Iterable[T], *, key: Callable[[T], K]) -> dict[K, tuple[T, ...]]:
    grouped: dict[K, list[T]] = defaultdict(list)
    for value in values:
        grouped[key(value)].append(value)
    return {identity: tuple(items) for identity, items in grouped.items()}


def _first_present(payload: Mapping[str, JsonValue], keys: Sequence[str]) -> JsonValue | None:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def _optional_bool(payload: Mapping[str, JsonValue], keys: Sequence[str]) -> bool | None:
    value = _first_present(payload, keys)
    return None if value is None else _bool_value(value, "schedule activity")


def _bool_value(value: JsonValue, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    raise ValueError(f"{field} must be boolean or zero/one")


def _sha256_json(value: dict[str, JsonValue]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("IBKR result timestamp must be UTC")
    return value.isoformat().replace("+00:00", "Z")


__all__ = [
    "build_ibkr_historical_aggregate_result",
    "build_ibkr_historical_result_artifact",
    "publish_ibkr_historical_results",
    "replay_ibkr_historical_aggregate_result",
    "replay_ibkr_historical_request_result",
]
