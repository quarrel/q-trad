"""Stage 4 IBKR result construction and independent replay logic."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from itertools import pairwise
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
    IbkrHistoricalEvidenceDisposition,
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


class _EvidenceInvalid(ValueError):
    """Provider payload is present but cannot be interpreted as evidence."""


class _EvidenceConflict(ValueError):
    """Provider emitted contradictory evidence for one owned value."""


class _EvidenceUnavailable(ValueError):
    """The provider did not establish schedule evidence for the requested interval."""


@dataclass(frozen=True, slots=True)
class _DerivedRequestEvidence:
    selected: IbkrHistoricalAttemptEvidence
    terminal_disposition: IbkrTerminalDisposition
    evidence_disposition: IbkrHistoricalEvidenceDisposition
    callbacks: tuple[IbkrHistoricalCallbackEvidence, ...]
    completion_markers: tuple[IbkrHistoricalCompletionEvidence, ...]
    accepted_rows: tuple[dict[str, JsonValue], ...]
    sessions: tuple[dict[str, JsonValue], ...]
    session_state: str | None
    error_classification: dict[str, JsonValue] | None


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


def verify_ibkr_historical_execution_snapshot(
    plan: IbkrHistoricalPlan,
    snapshot: IbkrHistoricalExecutionSnapshot,
    *,
    maximum_attempts: int,
) -> None:
    """Verify the exact durable execution closure before provider construction."""

    if maximum_attempts <= 0:
        raise ValueError("IBKR execution maximum attempts must be positive")
    _validate_snapshot_plan(plan, snapshot)
    expected_by_hash = {request.request_sha256: request for request in plan.requests}
    actual_hashes = {request.request_sha256 for request in snapshot.requests}
    if len(snapshot.requests) != len(expected_by_hash) or actual_hashes != set(expected_by_hash):
        raise ValueError("IBKR execution snapshot request closure differs from the plan")
    request_by_hash = {request.request_sha256: request for request in snapshot.requests}
    attempts_by_request = _group_by(snapshot.attempts, key=lambda item: item.request_sha256)
    callbacks_by_attempt = _group_by(snapshot.callbacks, key=lambda item: item.attempt_id)
    markers_by_attempt = _group_by(snapshot.completion_markers, key=lambda item: item.attempt_id)

    for request in plan.requests:
        stored = request_by_hash[request.request_sha256]
        expected_columns = (
            request.as_json_value(),
            str(request.instrument_id),
            request.kind.value,
            request.interval_start,
            request.interval_end,
        )
        stored_columns = (
            stored.request_payload,
            stored.instrument_id,
            stored.request_kind,
            stored.interval_start,
            stored.interval_end,
        )
        if stored_columns != expected_columns:
            raise ValueError(
                "IBKR stored request payload or canonical columns differ from the plan"
            )

        attempts = tuple(
            sorted(
                attempts_by_request.get(request.request_sha256, ()),
                key=lambda item: item.attempt_ordinal,
            )
        )
        if len(attempts) != stored.attempt_count:
            raise ValueError("IBKR stored attempt count does not match its attempt closure")
        if len(attempts) > maximum_attempts:
            raise ValueError("IBKR stored attempts exceed the frozen retry policy")
        if tuple(item.attempt_ordinal for item in attempts) != tuple(range(1, len(attempts) + 1)):
            raise ValueError("IBKR stored attempt ordinals are not contiguous")

        for attempt in attempts:
            callbacks = callbacks_by_attempt.get(attempt.attempt_id, ())
            markers = markers_by_attempt.get(attempt.attempt_id, ())
            _validate_callback_closure((attempt,), callbacks, markers)
            _validate_attempt_outcomes(request, (attempt,), callbacks, markers)

        selected = (
            None
            if stored.selected_attempt_id is None
            else next(
                (
                    attempt
                    for attempt in attempts
                    if attempt.attempt_id == stored.selected_attempt_id
                ),
                None,
            )
        )
        if stored.selected_attempt_id is not None and selected is None:
            raise ValueError("IBKR stored selected attempt is absent from its attempt closure")

        if stored.publication_status.value == "PUBLISHED":
            if stored.result_sha256 is None or stored.published_at is None:
                raise ValueError("published IBKR request lacks publication evidence")
        elif stored.result_sha256 is not None or stored.published_at is not None:
            raise ValueError("unpublished IBKR request contains publication evidence")

        if stored.status is IbkrRequestStatus.PENDING:
            if selected is not None or any(
                attempt.status
                not in {IbkrAttemptStatus.RETRYABLE_FAILURE, IbkrAttemptStatus.INVALIDATED}
                for attempt in attempts
            ):
                raise ValueError("pending IBKR request has an invalid attempt relationship")
        elif stored.status is IbkrRequestStatus.IN_FLIGHT:
            started = tuple(
                attempt for attempt in attempts if attempt.status is IbkrAttemptStatus.STARTED
            )
            if (
                selected is not None
                or len(started) != 1
                or not attempts
                or started[0].attempt_id != attempts[-1].attempt_id
                or any(
                    attempt.status
                    in {IbkrAttemptStatus.SUCCEEDED, IbkrAttemptStatus.TERMINAL_FAILURE}
                    for attempt in attempts
                )
            ):
                raise ValueError("in-flight IBKR request has an invalid attempt relationship")
        elif stored.status is IbkrRequestStatus.SUCCEEDED:
            if (
                selected is None
                or selected.status is not IbkrAttemptStatus.SUCCEEDED
                or selected.terminal_disposition is not IbkrTerminalDisposition.SUCCEEDED
                or not attempts
                or selected.attempt_id != attempts[-1].attempt_id
                or sum(
                    marker.closure_eligible
                    for marker in markers_by_attempt.get(selected.attempt_id, ())
                )
                != 1
            ):
                raise ValueError("successful IBKR request has invalid terminal evidence")
        elif stored.status is IbkrRequestStatus.TERMINAL:
            if (
                selected is None
                or selected.status is not IbkrAttemptStatus.TERMINAL_FAILURE
                or selected.terminal_disposition is None
                or selected.terminal_disposition is IbkrTerminalDisposition.SUCCEEDED
                or not attempts
                or selected.attempt_id != attempts[-1].attempt_id
                or any(attempt.status is IbkrAttemptStatus.STARTED for attempt in attempts)
            ):
                raise ValueError("terminal IBKR request has invalid terminal evidence")
        else:
            raise ValueError("IBKR stored request status is unsupported")


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
    _validate_aggregate_result_record_identities(request_results)
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
        "schema_version": IbkrHistoricalAggregateResult.SCHEMA_VERSION,
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


def _validate_request_result_record_identities(
    result: IbkrHistoricalRequestResult,
) -> None:
    if len({item.attempt_id for item in result.attempts}) != len(result.attempts):
        raise ValueError("IBKR request-result attempt identities are duplicated")
    if len({item.callback_id for item in result.callbacks}) != len(result.callbacks):
        raise ValueError("IBKR request-result callback identities are duplicated")
    if len({item.marker_id for item in result.completion_markers}) != len(
        result.completion_markers
    ):
        raise ValueError("IBKR request-result completion marker identities are duplicated")


def _validate_aggregate_result_record_identities(
    results: Sequence[IbkrHistoricalRequestResult],
) -> None:
    attempt_ids: set[UUID] = set()
    callback_ids: set[int] = set()
    marker_ids: set[int] = set()
    provider_request_identities: set[tuple[UUID, int, int]] = set()
    for result in results:
        _validate_request_result_record_identities(result)
        for attempt in result.attempts:
            if attempt.attempt_id in attempt_ids:
                raise ValueError("IBKR aggregate attempt identities are duplicated")
            attempt_ids.add(attempt.attempt_id)
            provider_request_identity = (
                attempt.connection_session_id,
                attempt.connection_generation,
                attempt.provider_request_id,
            )
            if provider_request_identity in provider_request_identities:
                raise ValueError("IBKR aggregate provider request identities are duplicated")
            provider_request_identities.add(provider_request_identity)
        for callback in result.callbacks:
            if callback.callback_id in callback_ids:
                raise ValueError("IBKR aggregate callback identities are duplicated")
            callback_ids.add(callback.callback_id)
        for marker in result.completion_markers:
            if marker.marker_id in marker_ids:
                raise ValueError("IBKR aggregate completion marker identities are duplicated")
            marker_ids.add(marker.marker_id)


def replay_ibkr_historical_request_result(
    request: IbkrHistoricalRequest,
    result: IbkrHistoricalRequestResult,
) -> None:
    """Reconstruct one request result from its published raw closure."""

    if result.plan_sha256 == "" or result.request_sha256 != request.request_sha256:
        raise ValueError("IBKR request-result identity does not match its plan request")
    if result.request_payload != request.as_json_value():
        raise ValueError("IBKR request-result request payload differs from its plan request")
    _validate_request_result_record_identities(result)
    derived = _derive_request_evidence(
        request=request,
        request_status=result.request_status,
        selected_attempt_id=result.selected_attempt_id,
        attempts=result.attempts,
        callbacks=result.callbacks,
        completion_markers=result.completion_markers,
    )
    if result.terminal_disposition is not derived.terminal_disposition:
        raise ValueError("IBKR request-result operational disposition does not replay")
    if result.evidence_disposition is not derived.evidence_disposition:
        raise ValueError("IBKR request-result evidence disposition does not replay")
    if tuple(result.accepted_rows) != derived.accepted_rows:
        raise ValueError("IBKR request-result accepted rows do not replay")
    if tuple(result.sessions) != derived.sessions:
        raise ValueError("IBKR request-result sessions do not replay")
    if result.session_state != derived.session_state:
        raise ValueError("IBKR request-result session state does not replay")
    expected_start = min(item.started_at for item in result.attempts)
    if result.acquisition_started_at != expected_start:
        raise ValueError("IBKR request-result acquisition start does not replay")
    if result.acquisition_completed_at != derived.selected.terminal_at:
        raise ValueError("IBKR request-result acquisition completion does not replay")
    expected_retry_history = _retry_history(result.attempts)
    if tuple(result.retry_history) != expected_retry_history:
        raise ValueError("IBKR request-result retry history does not replay")
    if result.error_classification != derived.error_classification:
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
    _validate_aggregate_result_record_identities(actual_results)
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
    derived = _derive_request_evidence(
        request=request,
        request_status=request_snapshot.status,
        selected_attempt_id=selected_id,
        attempts=attempts,
        callbacks=callbacks,
        completion_markers=markers,
    )
    selected_terminal_at = derived.selected.terminal_at
    if selected_terminal_at is None:
        raise ValueError("IBKR selected attempt is unfinished")
    start = min(item.started_at for item in attempts)
    identity = _request_identity(
        plan_sha256=request_snapshot.plan_sha256,
        request_sha256=request_snapshot.request_sha256,
        request_payload=request_snapshot.request_payload,
        request_status=request_snapshot.status,
        terminal_disposition=derived.terminal_disposition,
        evidence_disposition=derived.evidence_disposition,
        selected_attempt_id=selected_id,
        attempts=attempts,
        callbacks=callbacks,
        completion_markers=markers,
        accepted_rows=derived.accepted_rows,
        sessions=derived.sessions,
        session_state=derived.session_state,
        acquisition_started_at=start,
        acquisition_completed_at=selected_terminal_at,
        retry_history=_retry_history(attempts),
        error_classification=derived.error_classification,
    )
    return IbkrHistoricalRequestResult(
        plan_sha256=request_snapshot.plan_sha256,
        request_sha256=request_snapshot.request_sha256,
        request_payload=request_snapshot.request_payload,
        request_status=request_snapshot.status,
        terminal_disposition=derived.terminal_disposition,
        evidence_disposition=derived.evidence_disposition,
        selected_attempt_id=selected_id,
        attempts=attempts,
        callbacks=callbacks,
        completion_markers=markers,
        accepted_rows=derived.accepted_rows,
        sessions=derived.sessions,
        session_state=derived.session_state,
        acquisition_started_at=start,
        acquisition_completed_at=selected_terminal_at,
        retry_history=_retry_history(attempts),
        error_classification=derived.error_classification,
        result_sha256=_sha256_json(identity),
    )


def _derive_request_evidence(
    *,
    request: IbkrHistoricalRequest,
    request_status: IbkrRequestStatus,
    selected_attempt_id: UUID,
    attempts: Sequence[IbkrHistoricalAttemptEvidence],
    callbacks: Sequence[IbkrHistoricalCallbackEvidence],
    completion_markers: Sequence[IbkrHistoricalCompletionEvidence],
) -> _DerivedRequestEvidence:
    ordered_attempts = tuple(sorted(attempts, key=lambda item: item.attempt_ordinal))
    _validate_attempt_sequence(ordered_attempts)
    attempt_ids = {item.attempt_id for item in ordered_attempts}
    ordered_callbacks = tuple(
        sorted(callbacks, key=lambda item: (str(item.attempt_id), item.sequence, item.callback_id))
    )
    ordered_markers = tuple(
        sorted(
            completion_markers,
            key=lambda item: (str(item.attempt_id), item.sequence, item.marker_id),
        )
    )
    if any(item.attempt_id not in attempt_ids for item in ordered_callbacks):
        raise ValueError("IBKR request-result contains an orphan callback")
    if any(item.attempt_id not in attempt_ids for item in ordered_markers):
        raise ValueError("IBKR request-result contains an orphan completion marker")
    _validate_callback_closure(ordered_attempts, ordered_callbacks, ordered_markers)
    _validate_attempt_outcomes(request, ordered_attempts, ordered_callbacks, ordered_markers)

    successful_attempts = tuple(
        item
        for item in ordered_attempts
        if item.status is IbkrAttemptStatus.SUCCEEDED
        and item.terminal_disposition is IbkrTerminalDisposition.SUCCEEDED
    )
    if successful_attempts:
        if request_status is not IbkrRequestStatus.SUCCEEDED:
            raise ValueError("successful attempt is inconsistent with terminal request status")
        selected = successful_attempts[0]
        if any(item.attempt_ordinal > selected.attempt_ordinal for item in ordered_attempts):
            raise ValueError("IBKR attempt sequence continues after the first successful attempt")
        terminal_disposition = IbkrTerminalDisposition.SUCCEEDED
    else:
        if request_status is not IbkrRequestStatus.TERMINAL:
            raise ValueError("terminal request has no terminal failure attempt")
        selected = ordered_attempts[-1]
        if selected.status is not IbkrAttemptStatus.TERMINAL_FAILURE:
            raise ValueError("terminal request does not select its final terminal failure attempt")
        if selected.terminal_disposition in {None, IbkrTerminalDisposition.SUCCEEDED}:
            raise ValueError("terminal request selected attempt has no failure disposition")
        terminal_disposition = cast(IbkrTerminalDisposition, selected.terminal_disposition)
    if selected.attempt_id != selected_attempt_id:
        raise ValueError(
            "IBKR request-result selected attempt is not the first valid terminal outcome"
        )

    eligible_markers = _eligible_markers_for_evidence(ordered_markers, selected)
    if len(eligible_markers) > 1:
        raise ValueError("IBKR request-result has multiple eligible completion markers")
    completion = eligible_markers[0] if eligible_markers else None
    eligible_callbacks = _accepted_callbacks(ordered_callbacks, selected, completion)

    if request_status is IbkrRequestStatus.TERMINAL:
        evidence_disposition = _evidence_for_terminal(terminal_disposition)
        rows, sessions, session_state = _empty_result_values(request)
        error_detail = selected.detail
    elif any(callback.kind is IbkrHistoricalCallbackKind.ERROR for callback in eligible_callbacks):
        evidence_disposition = IbkrHistoricalEvidenceDisposition.PROVIDER_REJECTED
        rows, sessions, session_state = _empty_result_values(request)
        error_detail = "provider emitted an error callback before completion"
    elif completion is None:
        evidence_disposition = IbkrHistoricalEvidenceDisposition.INCOMPLETE_RESPONSE
        rows, sessions, session_state = _empty_result_values(request)
        error_detail = "successful request has no independently eligible completion marker"
    else:
        try:
            if request.kind is IbkrHistoricalRequestKind.MIDPOINT_BARS:
                rows = _normalise_bars(request, eligible_callbacks)
                sessions, session_state = (), None
                evidence_disposition = (
                    IbkrHistoricalEvidenceDisposition.SUCCEEDED
                    if rows
                    else IbkrHistoricalEvidenceDisposition.NO_DATA_RETURNED
                )
            else:
                rows = ()
                sessions, session_state = _normalise_schedule(request, eligible_callbacks)
                evidence_disposition = IbkrHistoricalEvidenceDisposition.SUCCEEDED
            error_detail = None
        except _EvidenceConflict as error:
            evidence_disposition = IbkrHistoricalEvidenceDisposition.CONFLICTING_CALLBACK_EVIDENCE
            rows, sessions, session_state = _empty_result_values(request)
            error_detail = str(error)
        except _EvidenceUnavailable as error:
            evidence_disposition = IbkrHistoricalEvidenceDisposition.SESSION_EVIDENCE_UNAVAILABLE
            rows, sessions, session_state = _empty_result_values(request)
            error_detail = str(error)
        except _EvidenceInvalid as error:
            evidence_disposition = IbkrHistoricalEvidenceDisposition.INVALID_CALLBACK_EVIDENCE
            rows, sessions, session_state = _empty_result_values(request)
            error_detail = str(error)
        except ValueError as error:
            evidence_disposition = IbkrHistoricalEvidenceDisposition.INVALID_CALLBACK_EVIDENCE
            rows, sessions, session_state = _empty_result_values(request)
            error_detail = str(error)
    return _DerivedRequestEvidence(
        selected=selected,
        terminal_disposition=terminal_disposition,
        evidence_disposition=evidence_disposition,
        callbacks=ordered_callbacks,
        completion_markers=ordered_markers,
        accepted_rows=rows,
        sessions=sessions,
        session_state=session_state,
        error_classification=_error_classification(evidence_disposition, selected, error_detail),
    )


def _validate_attempt_sequence(
    attempts: Sequence[IbkrHistoricalAttemptEvidence],
) -> None:
    if not attempts:
        raise ValueError("IBKR request-result requires attempt evidence")
    if tuple(item.attempt_ordinal for item in attempts) != tuple(range(1, len(attempts) + 1)):
        raise ValueError("IBKR request-result attempt ordinals are not contiguous")
    if len({item.attempt_id for item in attempts}) != len(attempts):
        raise ValueError("IBKR request-result attempt identities are duplicated")
    for index, attempt in enumerate(attempts):
        if attempt.status is IbkrAttemptStatus.STARTED or attempt.terminal_at is None:
            raise ValueError("published IBKR attempt is not a completed state transition")
        if attempt.status is IbkrAttemptStatus.INVALIDATED:
            if attempt.terminal_disposition is not None:
                raise ValueError("invalidated IBKR attempt has a terminal disposition")
        elif attempt.status is IbkrAttemptStatus.SUCCEEDED:
            if attempt.terminal_disposition is not IbkrTerminalDisposition.SUCCEEDED:
                raise ValueError("successful IBKR attempt has a failure disposition")
        elif attempt.status in {
            IbkrAttemptStatus.RETRYABLE_FAILURE,
            IbkrAttemptStatus.TERMINAL_FAILURE,
        }:
            if (
                attempt.terminal_disposition is None
                or attempt.terminal_disposition is IbkrTerminalDisposition.SUCCEEDED
            ):
                raise ValueError("failed IBKR attempt has an invalid disposition")
        else:
            raise ValueError("published IBKR attempt has an unknown status")
        if index:
            previous_terminal_at = attempts[index - 1].terminal_at
            if previous_terminal_at is None:
                raise ValueError("previous IBKR attempt transition is unfinished")
            if previous_terminal_at > attempt.started_at:
                raise ValueError("IBKR attempt transitions overlap or are out of order")


def _validate_callback_closure(
    attempts: Sequence[IbkrHistoricalAttemptEvidence],
    callbacks: Sequence[IbkrHistoricalCallbackEvidence],
    markers: Sequence[IbkrHistoricalCompletionEvidence],
) -> None:
    for attempt in attempts:
        attempt_callbacks = tuple(
            item for item in callbacks if item.attempt_id == attempt.attempt_id
        )
        sequences = tuple(item.sequence for item in attempt_callbacks)
        if sequences != tuple(range(1, len(attempt_callbacks) + 1)):
            raise ValueError("IBKR callback sequences are not unique and monotonic")
        if any(
            previous.received_at > current.received_at
            for previous, current in pairwise(attempt_callbacks)
        ):
            raise ValueError("IBKR callback received times are not monotonic")
        attempt_markers = tuple(item for item in markers if item.attempt_id == attempt.attempt_id)
        completion_callbacks = tuple(
            callback
            for callback in attempt_callbacks
            if callback.kind is IbkrHistoricalCallbackKind.COMPLETION
        )
        if len(completion_callbacks) != len(attempt_markers):
            raise ValueError("IBKR completion callbacks and markers are not one-to-one")
        matching_markers: list[IbkrHistoricalCompletionEvidence] = []
        for completion_callback in completion_callbacks:
            matching_markers_for_callback = tuple(
                marker
                for marker in attempt_markers
                if marker.sequence == completion_callback.sequence
            )
            if len(matching_markers_for_callback) != 1:
                raise ValueError("IBKR completion callbacks and markers are not one-to-one")
            marker = matching_markers_for_callback[0]
            if (
                marker.attempt_id != completion_callback.attempt_id
                or marker.connection_session_id != completion_callback.connection_session_id
                or marker.provider_request_id != completion_callback.provider_request_id
                or marker.connection_generation != completion_callback.connection_generation
                or marker.sequence != completion_callback.sequence
                or marker.completed_at != completion_callback.received_at
                or marker.payload != completion_callback.payload
                or marker.closure_eligible != completion_callback.closure_eligible
            ):
                raise ValueError(
                    "IBKR completion marker does not match its raw completion callback"
                )
            expected = (
                _completion_matches_attempt(marker, attempt)
                and attempt.terminal_at is not None
                and marker.completed_at <= attempt.terminal_at
            )
            if marker.closure_eligible != expected:
                raise ValueError("IBKR completion marker eligibility does not replay")
            if marker.closure_eligible:
                matching_markers.append(marker)
        if len(matching_markers) > 1:
            raise ValueError("IBKR attempt has multiple eligible completion markers")
        completion = matching_markers[0] if matching_markers else None
        for callback in attempt_callbacks:
            expected = _callback_matches_attempt(callback, attempt)
            if completion is not None:
                expected = (
                    expected
                    and callback.sequence <= completion.sequence
                    and callback.received_at <= completion.completed_at
                )
            elif attempt.terminal_at is not None:
                expected = expected and callback.received_at <= attempt.terminal_at
            if callback.closure_eligible != expected:
                raise ValueError("IBKR callback eligibility does not replay")
        for marker in attempt_markers:
            expected_midpoint = sum(
                callback.closure_eligible
                and callback.sequence < marker.sequence
                and callback.kind is IbkrHistoricalCallbackKind.MIDPOINT_BAR
                for callback in attempt_callbacks
            )
            expected_schedule = sum(
                callback.closure_eligible
                and callback.sequence < marker.sequence
                and callback.kind is IbkrHistoricalCallbackKind.SCHEDULE
                for callback in attempt_callbacks
            )
            if marker.raw_midpoint_bar_callback_count != expected_midpoint:
                raise ValueError("IBKR completion midpoint count does not replay")
            if marker.raw_schedule_callback_count != expected_schedule:
                raise ValueError("IBKR completion schedule count does not replay")


def _validate_attempt_outcomes(
    request: IbkrHistoricalRequest,
    attempts: Sequence[IbkrHistoricalAttemptEvidence],
    callbacks: Sequence[IbkrHistoricalCallbackEvidence],
    markers: Sequence[IbkrHistoricalCompletionEvidence],
) -> None:
    for attempt in attempts:
        eligible_markers = tuple(
            marker
            for marker in markers
            if marker.attempt_id == attempt.attempt_id
            and marker.closure_eligible
            and _completion_matches_attempt(marker, attempt)
        )
        if attempt.status is IbkrAttemptStatus.SUCCEEDED and len(eligible_markers) != 1:
            raise ValueError(
                "successful IBKR attempt requires exactly one eligible completion marker"
            )
        if not eligible_markers:
            continue
        if len(eligible_markers) > 1:
            raise ValueError("IBKR attempt has multiple eligible completion markers")
        marker = next(iter(eligible_markers))
        eligible_callbacks = tuple(
            callback
            for callback in callbacks
            if callback.attempt_id == attempt.attempt_id
            and callback.closure_eligible
            and _callback_matches_attempt(callback, attempt)
            and callback.sequence < marker.sequence
        )
        if any(
            callback.kind is IbkrHistoricalCallbackKind.ERROR for callback in eligible_callbacks
        ):
            expected_status = IbkrAttemptStatus.TERMINAL_FAILURE
            expected_disposition = IbkrTerminalDisposition.PROVIDER_REJECTED
        elif (
            request.kind is IbkrHistoricalRequestKind.SCHEDULE
            and marker.raw_schedule_callback_count == 0
        ):
            expected_status = IbkrAttemptStatus.TERMINAL_FAILURE
            expected_disposition = IbkrTerminalDisposition.SESSION_EVIDENCE_UNAVAILABLE
        else:
            expected_status = IbkrAttemptStatus.SUCCEEDED
            expected_disposition = IbkrTerminalDisposition.SUCCEEDED
        if (
            attempt.status is not expected_status
            or attempt.terminal_disposition is not expected_disposition
        ):
            raise ValueError("IBKR attempt status/disposition does not match its eligible closure")


def _empty_result_values(
    request: IbkrHistoricalRequest,
) -> tuple[
    tuple[dict[str, JsonValue], ...],
    tuple[dict[str, JsonValue], ...],
    str | None,
]:
    return (
        ((), (), None)
        if request.kind is IbkrHistoricalRequestKind.MIDPOINT_BARS
        else ((), (), "UNKNOWN")
    )


def _evidence_for_terminal(
    disposition: IbkrTerminalDisposition,
) -> IbkrHistoricalEvidenceDisposition:
    if disposition is IbkrTerminalDisposition.SUCCEEDED:
        raise ValueError("terminal request cannot have SUCCEEDED disposition")
    return IbkrHistoricalEvidenceDisposition(disposition.value)


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
    evidence_disposition_counts: dict[str, int] = defaultdict(int)
    operational_disposition_counts: dict[str, int] = defaultdict(int)
    for request in plan.requests:
        result = result_by_hash[request.request_sha256]
        instrument = str(request.instrument_id)
        entry = per_instrument.setdefault(
            instrument,
            {
                "bar_planned_request_count": 0,
                "bar_terminal_request_count": 0,
                "bar_successful_request_count": 0,
                "bar_no_data_request_count": 0,
                "bar_failed_request_count": 0,
                "bar_rows": 0,
                "schedule_planned_request_count": 0,
                "schedule_terminal_request_count": 0,
                "schedule_successful_request_count": 0,
                "schedule_no_data_request_count": 0,
                "schedule_failed_request_count": 0,
                "schedule_sessions": 0,
            },
        )
        prefix = "bar" if request.kind is IbkrHistoricalRequestKind.MIDPOINT_BARS else "schedule"
        planned_key = f"{prefix}_planned_request_count"
        terminal_key = f"{prefix}_terminal_request_count"
        successful_key = f"{prefix}_successful_request_count"
        no_data_key = f"{prefix}_no_data_request_count"
        failed_key = f"{prefix}_failed_request_count"
        entry[planned_key] = cast(int, entry[planned_key]) + 1
        if result.request_status in {IbkrRequestStatus.SUCCEEDED, IbkrRequestStatus.TERMINAL}:
            entry[terminal_key] = cast(int, entry[terminal_key]) + 1
        if result.evidence_disposition is IbkrHistoricalEvidenceDisposition.SUCCEEDED:
            entry[successful_key] = cast(int, entry[successful_key]) + 1
        elif result.evidence_disposition is IbkrHistoricalEvidenceDisposition.NO_DATA_RETURNED:
            entry[no_data_key] = cast(int, entry[no_data_key]) + 1
        else:
            entry[failed_key] = cast(int, entry[failed_key]) + 1
        if request.kind is IbkrHistoricalRequestKind.MIDPOINT_BARS:
            entry["bar_rows"] = cast(int, entry["bar_rows"]) + len(result.accepted_rows)
        else:
            entry["schedule_sessions"] = cast(int, entry["schedule_sessions"]) + len(
                result.sessions
            )
        evidence_disposition_counts[result.evidence_disposition.value] += 1
        operational_disposition_counts[result.terminal_disposition.value] += 1

    eligible: list[JsonValue] = []
    for instrument, entry in sorted(per_instrument.items()):
        bar_planned = cast(int, entry["bar_planned_request_count"])
        schedule_planned = cast(int, entry["schedule_planned_request_count"])
        if (
            bar_planned > 0
            and schedule_planned > 0
            and cast(int, entry["bar_terminal_request_count"]) == bar_planned
            and cast(int, entry["schedule_terminal_request_count"]) == schedule_planned
            and cast(int, entry["bar_successful_request_count"]) == bar_planned
            and cast(int, entry["schedule_successful_request_count"]) == schedule_planned
            and cast(int, entry["bar_rows"]) > 0
        ):
            eligible.append(instrument)
    coverage: dict[str, JsonValue] = {
        "planned_request_count": len(plan.requests),
        "terminal_request_count": len(results),
        "operational_successful_request_count": sum(
            item.request_status is IbkrRequestStatus.SUCCEEDED for item in results
        ),
        "successful_request_count": sum(
            item.evidence_disposition is IbkrHistoricalEvidenceDisposition.SUCCEEDED
            for item in results
        ),
        "no_data_request_count": sum(
            item.evidence_disposition is IbkrHistoricalEvidenceDisposition.NO_DATA_RETURNED
            for item in results
        ),
        "failed_request_count": sum(
            item.evidence_disposition
            not in {
                IbkrHistoricalEvidenceDisposition.SUCCEEDED,
                IbkrHistoricalEvidenceDisposition.NO_DATA_RETURNED,
            }
            for item in results
        ),
        "accepted_bar_row_count": sum(len(item.accepted_rows) for item in results),
        "provider_session_count": sum(len(item.sessions) for item in results),
        "by_instrument": {
            instrument: value for instrument, value in sorted(per_instrument.items())
        },
    }
    entitlement: dict[str, JsonValue] = {
        "disposition_counts": dict(sorted(evidence_disposition_counts.items())),
        "operational_disposition_counts": dict(sorted(operational_disposition_counts.items())),
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
            raise _EvidenceInvalid("IBKR historical bar timestamp is not minute-aligned")
        missing = tuple(key for key in _OHLC_KEYS if key not in callback.payload)
        if missing:
            raise _EvidenceInvalid(
                "IBKR historical callback is missing OHLC: " + ", ".join(missing)
            )
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
            raise _EvidenceInvalid("IBKR historical callback contains invalid OHLC")
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
                raise _EvidenceConflict(
                    "IBKR historical callback contains conflicting duplicate OHLC"
                )
            continue
        by_start[timestamp] = row
    return tuple(by_start[key] for key in sorted(by_start))


def _normalise_schedule(
    request: IbkrHistoricalRequest,
    callbacks: Sequence[IbkrHistoricalCallbackEvidence],
) -> tuple[tuple[dict[str, JsonValue], ...], str]:
    sessions_by_interval: dict[tuple[datetime, datetime], dict[str, JsonValue]] = {}
    activity_declarations: set[bool] = set()
    saw_nonempty_sessions = False
    saw_schedule_callback = False
    for callback in callbacks:
        if callback.kind is not IbkrHistoricalCallbackKind.SCHEDULE:
            continue
        saw_schedule_callback = True
        payload = callback.payload
        session_values = payload.get("sessions")
        if session_values is None:
            try:
                active = _optional_bool(payload, _SESSION_ACTIVE_KEYS)
            except ValueError as error:
                raise _EvidenceUnavailable(str(error)) from error
            if active is None:
                raise _EvidenceUnavailable(
                    "IBKR schedule callback has no structural session evidence"
                )
            if activity_declarations and active not in activity_declarations:
                raise _EvidenceConflict("IBKR schedule activity declarations conflict")
            activity_declarations.add(active)
            continue
        if not isinstance(session_values, list):
            raise _EvidenceUnavailable("IBKR schedule sessions must be an array")
        if not session_values:
            if activity_declarations and True in activity_declarations:
                raise _EvidenceConflict("IBKR schedule activity declarations conflict")
            activity_declarations.add(False)
            continue
        saw_nonempty_sessions = True
        for raw_session in session_values:
            if not isinstance(raw_session, Mapping):
                raise _EvidenceUnavailable("IBKR schedule session must be an object")
            session = cast(Mapping[str, JsonValue], raw_session)
            start_value = _first_present(session, _SESSION_START_KEYS)
            end_value = _first_present(session, _SESSION_END_KEYS)
            active_value = _first_present(session, _SESSION_ACTIVE_KEYS)
            if start_value is None or end_value is None or active_value is None:
                raise _EvidenceUnavailable("IBKR schedule session lacks required fields")
            try:
                start = _provider_datetime(start_value, "schedule session start")
                end = _provider_datetime(end_value, "schedule session end")
                active = _bool_value(active_value, "schedule session active")
            except ValueError as error:
                raise _EvidenceUnavailable(str(error)) from error
            if end <= start:
                raise _EvidenceUnavailable("IBKR schedule session interval is invalid")
            clipped_start = max(start, request.interval_start)
            clipped_end = min(end, request.interval_end)
            if clipped_start >= clipped_end:
                continue
            interval = (clipped_start, clipped_end)
            for (existing_start, existing_end), existing in sessions_by_interval.items():
                if (
                    existing_end > clipped_start
                    and clipped_end > existing_start
                    and bool(existing["active"]) is not active
                ):
                    raise _EvidenceConflict(
                        "IBKR schedule declarations overlap with conflicting activity"
                    )
            existing = sessions_by_interval.get(interval)
            if existing is not None:
                if bool(existing["active"]) is not active:
                    raise _EvidenceConflict("IBKR schedule declarations conflict for one interval")
                continue
            if activity_declarations and active not in activity_declarations:
                raise _EvidenceConflict("IBKR schedule activity declarations conflict")
            sessions_by_interval[interval] = {
                "interval_start": clipped_start.isoformat().replace("+00:00", "Z"),
                "interval_end": clipped_end.isoformat().replace("+00:00", "Z"),
                "active": active,
                "callback_sequence": callback.sequence,
                "provider_session": dict(session),
            }
    if not saw_schedule_callback:
        raise _EvidenceUnavailable("successful IBKR schedule request has no schedule callback")
    if saw_nonempty_sessions and not sessions_by_interval and not activity_declarations:
        raise _EvidenceUnavailable("IBKR schedule sessions do not overlap the requested interval")
    if not activity_declarations and not sessions_by_interval:
        raise _EvidenceUnavailable("IBKR schedule callback has no declared activity")
    structured_activity_values = {bool(item["active"]) for item in sessions_by_interval.values()}
    if (
        activity_declarations
        and structured_activity_values
        and len(activity_declarations | structured_activity_values) > 1
    ):
        raise _EvidenceConflict(
            "IBKR schedule activity declarations conflict with structured sessions"
        )
    activity_values = set(activity_declarations)
    activity_values.update(structured_activity_values)
    if len(activity_values) > 1 and not sessions_by_interval:
        raise _EvidenceConflict("IBKR schedule activity declarations conflict")
    state = "ACTIVE" if any(activity_values) else "INACTIVE"
    sessions = tuple(
        sorted(
            sessions_by_interval.values(),
            key=lambda item: (str(item["interval_start"]), cast(int, item["callback_sequence"])),
        )
    )
    return sessions, state


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
        raise _EvidenceInvalid(f"{field} must be a finite decimal")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise _EvidenceInvalid(f"{field} must be a finite decimal") from error
    if not parsed.is_finite():
        raise _EvidenceInvalid(f"{field} must be a finite decimal")
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
    evidence_disposition: IbkrHistoricalEvidenceDisposition,
    selected: IbkrHistoricalAttemptEvidence,
    detail: str | None,
) -> dict[str, JsonValue] | None:
    if evidence_disposition in {
        IbkrHistoricalEvidenceDisposition.SUCCEEDED,
        IbkrHistoricalEvidenceDisposition.NO_DATA_RETURNED,
    }:
        return None
    return {
        "disposition": evidence_disposition.value,
        "detail": detail if detail is not None else selected.detail,
    }


def _request_identity(
    *,
    plan_sha256: str,
    request_sha256: str,
    request_payload: dict[str, JsonValue],
    request_status: IbkrRequestStatus,
    terminal_disposition: IbkrTerminalDisposition,
    evidence_disposition: IbkrHistoricalEvidenceDisposition,
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
        "schema_version": IbkrHistoricalRequestResult.SCHEMA_VERSION,
        "plan_sha256": plan_sha256,
        "request_sha256": request_sha256,
        "request_payload": request_payload,
        "request_status": request_status.value,
        "terminal_disposition": terminal_disposition.value,
        "evidence_disposition": evidence_disposition.value,
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
    "verify_ibkr_historical_execution_snapshot",
]
