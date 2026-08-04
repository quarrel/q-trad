from __future__ import annotations

from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from qtrad.application.ibkr_results import (
    build_ibkr_historical_result_artifact,
    replay_ibkr_historical_aggregate_result,
    replay_ibkr_historical_request_result,
    verify_ibkr_historical_execution_snapshot,
)
from qtrad.domain.events import JsonValue
from qtrad.domain.ibkr_execution import (
    IbkrAttemptStatus,
    IbkrHistoricalCallbackKind,
    IbkrPublicationStatus,
    IbkrRequestStatus,
    IbkrTerminalDisposition,
    ibkr_historical_plan_bytes,
)
from qtrad.domain.ibkr_historical import (
    HISTORICAL_PLAN_CONTRACT,
    IbkrContractFingerprint,
    IbkrHistoricalPlan,
    IbkrHistoricalRequest,
    IbkrHistoricalRequestKind,
    IbkrPlannedContract,
    ibkr_end_date_time,
    sha256_json,
    utc_text,
)
from qtrad.domain.ibkr_results import (
    IbkrHistoricalAggregateResult,
    IbkrHistoricalAttemptEvidence,
    IbkrHistoricalCallbackEvidence,
    IbkrHistoricalCompletionEvidence,
    IbkrHistoricalEvidenceDisposition,
    IbkrHistoricalExecutionSnapshot,
    IbkrHistoricalPlanSnapshot,
    IbkrHistoricalRequestResult,
    IbkrHistoricalRequestSnapshot,
    IbkrHistoricalResultArtifact,
    canonical_json_bytes,
    sha256_bytes,
)
from qtrad.domain.identifiers import InstrumentId
from qtrad.runtime.ibkr_results import (
    publish_ibkr_historical_result,
    verify_ibkr_historical_result,
    write_ibkr_historical_result,
)

_START = datetime(2026, 2, 1, tzinfo=UTC)
_END = _START + timedelta(minutes=2)
_INSTRUMENT = InstrumentId("fx:aud-usd")
_FINGERPRINT = IbkrContractFingerprint(
    con_id=42,
    symbol="AUD",
    security_type="CASH",
    currency="USD",
    exchange="IDEALPRO",
    primary_exchange=None,
    local_symbol="AUD.USD",
    trading_class="AUD.USD",
    multiplier=None,
    underlying_con_id=None,
    contract_month=None,
)
_SESSIONS = (
    UUID("00000000-0000-0000-0000-000000000001"),
    UUID("00000000-0000-0000-0000-000000000002"),
)
_ATTEMPTS = (
    UUID("00000000-0000-0000-0000-000000000011"),
    UUID("00000000-0000-0000-0000-000000000012"),
)


def _request(kind: IbkrHistoricalRequestKind) -> IbkrHistoricalRequest:
    bar_size = "1 min" if kind is IbkrHistoricalRequestKind.MIDPOINT_BARS else "1 day"
    what_to_show = "MIDPOINT" if kind is IbkrHistoricalRequestKind.MIDPOINT_BARS else "SCHEDULE"
    format_date = 2
    identity: dict[str, JsonValue] = {
        "instrument_id": str(_INSTRUMENT),
        "fingerprint": _FINGERPRINT.as_json_value(),
        "kind": kind.value,
        "interval_start": utc_text(_START),
        "interval_end": utc_text(_END),
        "end_date_time": ibkr_end_date_time(_END),
        "duration": "1 D",
        "bar_size": bar_size,
        "what_to_show": what_to_show,
        "use_rth": False,
        "format_date": format_date,
        "keep_up_to_date": False,
    }
    return IbkrHistoricalRequest(
        instrument_id=_INSTRUMENT,
        fingerprint=_FINGERPRINT,
        kind=kind,
        interval_start=_START,
        interval_end=_END,
        end_date_time=ibkr_end_date_time(_END),
        duration="1 D",
        bar_size=bar_size,
        what_to_show=what_to_show,
        use_rth=False,
        format_date=format_date,
        keep_up_to_date=False,
        request_sha256=sha256_json(identity),
    )


def _plan() -> tuple[IbkrHistoricalPlan, tuple[IbkrHistoricalRequest, ...]]:
    requests = tuple(_request(kind) for kind in IbkrHistoricalRequestKind)
    eligible = (IbkrPlannedContract(_INSTRUMENT, _FINGERPRINT),)
    identity: dict[str, JsonValue] = {
        "contract": HISTORICAL_PLAN_CONTRACT,
        "schema_version": 1,
        "contract_selection_sha256": "b" * 64,
        "runtime_sha256": "c" * 64,
        "request_profile_sha256": "d" * 64,
        "provider": "ibkr",
        "environment": "paper",
        "planner_qtrad_commit": "e" * 40,
        "planner_qtrad_image_digest": "sha256:" + "f" * 64,
        "start": utc_text(_START),
        "end": utc_text(_END),
        "eligible_contracts": [eligible[0].as_json_value()],
        "requests": [
            request.as_json_value()
            for request in sorted(requests, key=lambda item: item.kind.value)
        ],
    }
    plan = IbkrHistoricalPlan(
        contract_selection_sha256="b" * 64,
        runtime_sha256="c" * 64,
        request_profile_sha256="d" * 64,
        provider="ibkr",
        environment="paper",
        planner_qtrad_commit="e" * 40,
        planner_qtrad_image_digest="sha256:" + "f" * 64,
        start=_START,
        end=_END,
        eligible_contracts=eligible,
        requests=requests,
        plan_sha256=sha256_json(identity),
    )
    return plan, requests


def _attempt(
    plan: IbkrHistoricalPlan,
    request: IbkrHistoricalRequest,
    slot: int,
    started_at: datetime,
) -> IbkrHistoricalAttemptEvidence:
    return IbkrHistoricalAttemptEvidence(
        attempt_id=_ATTEMPTS[slot - 1],
        plan_sha256=plan.plan_sha256,
        request_sha256=request.request_sha256,
        attempt_ordinal=1,
        connection_session_id=_SESSIONS[slot - 1],
        provider_request_id=100 + slot,
        connection_generation=1,
        started_at=started_at,
        status=IbkrAttemptStatus.SUCCEEDED,
        terminal_at=started_at + timedelta(seconds=10),
        terminal_disposition=IbkrTerminalDisposition.SUCCEEDED,
        detail=None,
    )


def _callback(
    *,
    callback_id: int,
    attempt: IbkrHistoricalAttemptEvidence,
    sequence: int,
    kind: IbkrHistoricalCallbackKind,
    received_at: datetime,
    payload: dict[str, JsonValue],
) -> IbkrHistoricalCallbackEvidence:
    return IbkrHistoricalCallbackEvidence(
        callback_id=callback_id,
        attempt_id=attempt.attempt_id,
        connection_session_id=attempt.connection_session_id,
        provider_request_id=attempt.provider_request_id,
        connection_generation=attempt.connection_generation,
        sequence=sequence,
        kind=kind,
        received_at=received_at,
        payload=payload,
        closure_eligible=True,
    )


def _marker(
    *,
    marker_id: int,
    attempt: IbkrHistoricalAttemptEvidence,
    sequence: int,
    completed_at: datetime,
    midpoint_count: int,
    schedule_count: int,
) -> IbkrHistoricalCompletionEvidence:
    return IbkrHistoricalCompletionEvidence(
        marker_id=marker_id,
        attempt_id=attempt.attempt_id,
        connection_session_id=attempt.connection_session_id,
        provider_request_id=attempt.provider_request_id,
        connection_generation=attempt.connection_generation,
        sequence=sequence,
        completed_at=completed_at,
        raw_midpoint_bar_callback_count=midpoint_count,
        raw_schedule_callback_count=schedule_count,
        closure_eligible=True,
        payload={},
    )


def _snapshot(
    plan: IbkrHistoricalPlan, requests: tuple[IbkrHistoricalRequest, ...]
) -> IbkrHistoricalExecutionSnapshot:
    plan_bytes = ibkr_historical_plan_bytes(plan)
    plan_snapshot = IbkrHistoricalPlanSnapshot(
        plan_sha256=plan.plan_sha256,
        plan_bytes=plan_bytes,
        plan_bytes_sha256=sha256_bytes(plan_bytes),
        plan_payload=plan.as_json_value(),
        registered_at=_START,
        publication_status=IbkrPublicationStatus.PENDING,
    )
    attempts = tuple(
        _attempt(plan, request, index, _START + timedelta(seconds=index - 1))
        for index, request in enumerate(requests, start=1)
    )
    bar_attempt, schedule_attempt = attempts
    callbacks = (
        _callback(
            callback_id=1,
            attempt=bar_attempt,
            sequence=1,
            kind=IbkrHistoricalCallbackKind.MIDPOINT_BAR,
            received_at=_START + timedelta(seconds=2),
            payload={
                "date": int((_START + timedelta(minutes=1)).timestamp()),
                "open": "1.1000",
                "high": "1.1010",
                "low": "1.0990",
                "close": "1.1005",
                "volume": 7,
                "wap": "1.1001",
                "count": 3,
            },
        ),
        _callback(
            callback_id=2,
            attempt=bar_attempt,
            sequence=2,
            kind=IbkrHistoricalCallbackKind.COMPLETION,
            received_at=_START + timedelta(seconds=3),
            payload={},
        ),
        _callback(
            callback_id=3,
            attempt=schedule_attempt,
            sequence=1,
            kind=IbkrHistoricalCallbackKind.SCHEDULE,
            received_at=_START + timedelta(seconds=4),
            payload={"sessions": []},
        ),
        _callback(
            callback_id=4,
            attempt=schedule_attempt,
            sequence=2,
            kind=IbkrHistoricalCallbackKind.COMPLETION,
            received_at=_START + timedelta(seconds=5),
            payload={},
        ),
    )
    markers = (
        _marker(
            marker_id=1,
            attempt=bar_attempt,
            sequence=2,
            completed_at=callbacks[1].received_at,
            midpoint_count=1,
            schedule_count=0,
        ),
        _marker(
            marker_id=2,
            attempt=schedule_attempt,
            sequence=2,
            completed_at=callbacks[3].received_at,
            midpoint_count=0,
            schedule_count=1,
        ),
    )
    request_snapshots = tuple(
        IbkrHistoricalRequestSnapshot(
            plan_sha256=plan.plan_sha256,
            request_sha256=request.request_sha256,
            request_payload=request.as_json_value(),
            instrument_id=str(request.instrument_id),
            request_kind=request.kind.value,
            interval_start=request.interval_start,
            interval_end=request.interval_end,
            status=IbkrRequestStatus.SUCCEEDED,
            attempt_count=1,
            selected_attempt_id=attempt.attempt_id,
            publication_status=IbkrPublicationStatus.PENDING,
            result_sha256=None,
            published_at=None,
        )
        for request, attempt in zip(requests, attempts, strict=True)
    )
    return IbkrHistoricalExecutionSnapshot(
        plan=plan_snapshot,
        requests=request_snapshots,
        attempts=attempts,
        callbacks=callbacks,
        completion_markers=markers,
    )


def _build_fixture() -> tuple[IbkrHistoricalPlan, IbkrHistoricalExecutionSnapshot]:
    plan, requests = _plan()
    return plan, _snapshot(plan, requests)


def test_execution_snapshot_preflight_rejects_database_mutations() -> None:
    plan, snapshot = _build_fixture()
    verify_ibkr_historical_execution_snapshot(plan, snapshot, maximum_attempts=2)

    deleted = replace(
        snapshot,
        requests=snapshot.requests[:1],
        attempts=snapshot.attempts[:1],
        callbacks=snapshot.callbacks[:2],
        completion_markers=snapshot.completion_markers[:1],
    )
    with pytest.raises(ValueError, match="request closure"):
        verify_ibkr_historical_execution_snapshot(plan, deleted, maximum_attempts=2)

    altered_request = replace(
        snapshot.requests[0],
        request_payload={**snapshot.requests[0].request_payload, "duration": "2 D"},
    )
    altered = replace(snapshot, requests=(altered_request, *snapshot.requests[1:]))
    with pytest.raises(ValueError, match="payload or canonical"):
        verify_ibkr_historical_execution_snapshot(plan, altered, maximum_attempts=2)

    extra_request = replace(snapshot.requests[0], request_sha256="f" * 64)
    extra = replace(snapshot, requests=(*snapshot.requests, extra_request))
    with pytest.raises(ValueError, match="request closure"):
        verify_ibkr_historical_execution_snapshot(plan, extra, maximum_attempts=2)

    fabricated_terminal = replace(
        snapshot.requests[0],
        status=IbkrRequestStatus.TERMINAL,
        selected_attempt_id=None,
    )
    fabricated = replace(
        snapshot,
        requests=(fabricated_terminal, *snapshot.requests[1:]),
    )
    with pytest.raises(ValueError, match="terminal IBKR request"):
        verify_ibkr_historical_execution_snapshot(plan, fabricated, maximum_attempts=2)


def _bar_and_schedule_results(
    artifact: IbkrHistoricalResultArtifact,
) -> tuple[IbkrHistoricalRequestResult, IbkrHistoricalRequestResult]:
    return (
        next(
            item
            for item in artifact.request_results
            if item.request_payload["kind"] == IbkrHistoricalRequestKind.MIDPOINT_BARS.value
        ),
        next(
            item
            for item in artifact.request_results
            if item.request_payload["kind"] == IbkrHistoricalRequestKind.SCHEDULE.value
        ),
    )


def test_result_builder_and_file_verifier_replay_a_create_only_closure(tmp_path: Path) -> None:
    plan, snapshot = _build_fixture()
    artifact = build_ibkr_historical_result_artifact(plan, snapshot)

    output = tmp_path / "result"
    manifest = write_ibkr_historical_result(output, artifact)
    verified = verify_ibkr_historical_result(manifest)

    bar_result = next(
        item
        for item in verified.request_results
        if item.request_status is IbkrRequestStatus.SUCCEEDED and item.accepted_rows
    )
    assert bar_result.accepted_rows[0]["bar_start"] == "2026-02-01T00:01:00Z"
    assert bar_result.accepted_rows[0]["bar_end"] == "2026-02-01T00:02:00Z"
    assert bar_result.accepted_rows[0]["volume"] == 7

    schedule_result = next(
        item
        for item in verified.request_results
        if item.request_payload["kind"] == IbkrHistoricalRequestKind.SCHEDULE.value
    )
    assert schedule_result.session_state == "INACTIVE"
    assert verified.aggregate.aggregate_sha256 == artifact.aggregate.aggregate_sha256
    with pytest.raises(FileExistsError):
        write_ibkr_historical_result(output, artifact)


def test_result_builder_is_invariant_to_callback_row_order() -> None:
    plan, snapshot = _build_fixture()
    reordered = replace(
        snapshot,
        requests=tuple(reversed(snapshot.requests)),
        attempts=tuple(reversed(snapshot.attempts)),
        callbacks=tuple(reversed(snapshot.callbacks)),
        completion_markers=tuple(reversed(snapshot.completion_markers)),
    )

    first = build_ibkr_historical_result_artifact(plan, snapshot)
    second = build_ibkr_historical_result_artifact(plan, reordered)

    assert first.aggregate.as_json_value() == second.aggregate.as_json_value()
    assert [item.as_json_value() for item in first.request_results] == [
        item.as_json_value() for item in second.request_results
    ]


def test_result_verifier_rejects_missing_and_orphan_children(tmp_path: Path) -> None:
    plan, snapshot = _build_fixture()
    artifact = build_ibkr_historical_result_artifact(plan, snapshot)
    output = tmp_path / "missing"
    manifest = write_ibkr_historical_result(output, artifact)
    child = next(output.joinpath("requests").glob("*.json"))
    child.unlink()
    with pytest.raises(ValueError, match=r"child closure|missing"):
        verify_ibkr_historical_result(manifest)

    output = tmp_path / "orphan"
    manifest = write_ibkr_historical_result(output, artifact)
    output.joinpath("requests", "orphan.json").write_text("{}")
    with pytest.raises(ValueError, match=r"child closure|unexpected"):
        verify_ibkr_historical_result(manifest)


def test_result_builder_classifies_conflicting_bar_duplicate() -> None:
    plan, snapshot = _build_fixture()
    bar_attempt = snapshot.attempts[0]
    duplicate = _callback(
        callback_id=99,
        attempt=bar_attempt,
        sequence=2,
        kind=IbkrHistoricalCallbackKind.MIDPOINT_BAR,
        received_at=_START + timedelta(seconds=2, microseconds=500_000),
        payload={
            "date": int((_START + timedelta(minutes=1)).timestamp()),
            "open": "1.1000",
            "high": "1.1010",
            "low": "1.0990",
            "close": "1.1006",
        },
    )
    shifted_completion = replace(snapshot.callbacks[1], sequence=3)
    shifted_marker = replace(
        snapshot.completion_markers[0],
        sequence=3,
        raw_midpoint_bar_callback_count=2,
    )
    conflicting = replace(
        snapshot,
        callbacks=(
            snapshot.callbacks[0],
            duplicate,
            shifted_completion,
            *snapshot.callbacks[2:],
        ),
        completion_markers=(shifted_marker, snapshot.completion_markers[1]),
    )

    artifact = build_ibkr_historical_result_artifact(plan, conflicting)
    bar_result = next(
        item
        for item in artifact.request_results
        if item.request_payload["kind"] == IbkrHistoricalRequestKind.MIDPOINT_BARS.value
    )
    assert (
        bar_result.evidence_disposition
        is IbkrHistoricalEvidenceDisposition.CONFLICTING_CALLBACK_EVIDENCE
    )
    assert bar_result.accepted_rows == ()


def test_result_builder_distinguishes_no_data_from_operational_success() -> None:
    plan, snapshot = _build_fixture()
    outside_bar = replace(
        snapshot.callbacks[0],
        payload={
            **snapshot.callbacks[0].payload,
            "date": int(_END.timestamp()),
        },
    )
    no_data = replace(snapshot, callbacks=(outside_bar, *snapshot.callbacks[1:]))

    artifact = build_ibkr_historical_result_artifact(plan, no_data)
    bar_result = next(
        item
        for item in artifact.request_results
        if item.request_payload["kind"] == IbkrHistoricalRequestKind.MIDPOINT_BARS.value
    )
    assert bar_result.request_status is IbkrRequestStatus.SUCCEEDED
    assert bar_result.terminal_disposition is IbkrTerminalDisposition.SUCCEEDED
    assert bar_result.evidence_disposition is IbkrHistoricalEvidenceDisposition.NO_DATA_RETURNED
    assert bar_result.accepted_rows == ()
    assert artifact.aggregate.coverage_summary["no_data_request_count"] == 1


def test_result_builder_classifies_unavailable_schedule_evidence() -> None:
    plan, snapshot = _build_fixture()
    outside_schedule = replace(
        snapshot.callbacks[2],
        payload={
            "sessions": [
                {
                    "start": utc_text(_END + timedelta(minutes=1)),
                    "end": utc_text(_END + timedelta(minutes=2)),
                    "active": True,
                }
            ]
        },
    )
    unavailable = replace(
        snapshot, callbacks=(*snapshot.callbacks[:2], outside_schedule, *snapshot.callbacks[3:])
    )

    artifact = build_ibkr_historical_result_artifact(plan, unavailable)
    schedule_result = next(
        item
        for item in artifact.request_results
        if item.request_payload["kind"] == IbkrHistoricalRequestKind.SCHEDULE.value
    )
    assert (
        schedule_result.evidence_disposition
        is IbkrHistoricalEvidenceDisposition.SESSION_EVIDENCE_UNAVAILABLE
    )
    assert schedule_result.session_state == "UNKNOWN"
    assert schedule_result.sessions == ()


def _rehash_result(
    result: IbkrHistoricalRequestResult,
    **changes: object,
) -> IbkrHistoricalRequestResult:
    mutated = object.__new__(IbkrHistoricalRequestResult)
    for field in fields(result):
        object.__setattr__(
            mutated,
            field.name,
            changes.get(field.name, getattr(result, field.name)),
        )
    object.__setattr__(mutated, "result_sha256", sha256_json(mutated.identity_payload()))
    return mutated


def _rehash_aggregate(
    aggregate: IbkrHistoricalAggregateResult,
    request_results: tuple[IbkrHistoricalRequestResult, ...],
) -> IbkrHistoricalAggregateResult:
    sorted_results = tuple(sorted(request_results, key=lambda item: item.request_sha256))
    child_refs = tuple(
        replace(
            child,
            semantic_sha256=result.result_sha256,
            bytes_sha256=sha256_bytes(canonical_json_bytes(result.as_json_value())),
        )
        for child, result in zip(aggregate.request_results, sorted_results, strict=True)
    )
    mutated = object.__new__(IbkrHistoricalAggregateResult)
    for field in fields(aggregate):
        object.__setattr__(mutated, field.name, getattr(aggregate, field.name))
    object.__setattr__(mutated, "request_results", child_refs)
    object.__setattr__(mutated, "aggregate_sha256", sha256_json(mutated.identity_payload()))
    return mutated


def test_result_replay_rejects_mutated_closure_and_marker_counts() -> None:
    plan, snapshot = _build_fixture()
    artifact = build_ibkr_historical_result_artifact(plan, snapshot)
    request = next(
        item for item in plan.requests if item.kind is IbkrHistoricalRequestKind.MIDPOINT_BARS
    )
    result = next(
        item for item in artifact.request_results if item.request_sha256 == request.request_sha256
    )

    mutated_callbacks = tuple(
        replace(callback, closure_eligible=False)
        if callback.kind is IbkrHistoricalCallbackKind.MIDPOINT_BAR
        else callback
        for callback in result.callbacks
    )
    mutated = _rehash_result(result, callbacks=mutated_callbacks)
    with pytest.raises(ValueError, match="eligibility"):
        replay_ibkr_historical_request_result(request, mutated)

    mutated_markers = tuple(
        replace(marker, raw_midpoint_bar_callback_count=0)
        if marker.attempt_id == result.selected_attempt_id
        else marker
        for marker in result.completion_markers
    )
    mutated = _rehash_result(result, completion_markers=mutated_markers)
    with pytest.raises(ValueError, match="midpoint count"):
        replay_ibkr_historical_request_result(request, mutated)


def test_result_publisher_stages_and_verifies_create_only_output(tmp_path: Path) -> None:
    plan, snapshot = _build_fixture()
    artifact = build_ibkr_historical_result_artifact(plan, snapshot)
    output = tmp_path / "staged-result"

    manifest = publish_ibkr_historical_result(output, artifact)

    assert verify_ibkr_historical_result(manifest).aggregate.aggregate_sha256 == (
        artifact.aggregate.aggregate_sha256
    )
    with pytest.raises(FileExistsError):
        publish_ibkr_historical_result(output, artifact)


def test_ibkr_result_cli_parser_exposes_build_and_file_only_verify() -> None:
    from qtrad.__main__ import build_parser

    parser = build_parser()
    build_args = parser.parse_args(
        ["historical", "ibkr", "result-build", "--plan", "plan.json", "--output", "result"]
    )
    verify_args = parser.parse_args(
        ["historical", "ibkr", "verify", "--result", "result/manifest.json"]
    )

    assert build_args.historical_ibkr_command == "result-build"
    assert build_args.plan == Path("plan.json")
    assert build_args.output == Path("result")
    assert verify_args.historical_ibkr_command == "verify"
    assert verify_args.result == Path("result/manifest.json")


def test_result_builder_and_file_verifier_accepts_invalidated_restart(tmp_path: Path) -> None:
    plan, snapshot = _build_fixture()
    first_attempt = snapshot.attempts[0]
    retry_id = UUID("00000000-0000-0000-0000-000000000013")
    retry_session = UUID("00000000-0000-0000-0000-000000000003")
    invalidated = replace(
        first_attempt,
        status=IbkrAttemptStatus.INVALIDATED,
        terminal_at=_START + timedelta(seconds=4),
        terminal_disposition=None,
        detail="connection generation disconnected before attempt completion",
    )
    retry = replace(
        first_attempt,
        attempt_id=retry_id,
        attempt_ordinal=2,
        connection_session_id=retry_session,
        provider_request_id=103,
        started_at=_START + timedelta(seconds=5),
        terminal_at=_START + timedelta(seconds=8),
        status=IbkrAttemptStatus.SUCCEEDED,
        terminal_disposition=IbkrTerminalDisposition.SUCCEEDED,
        detail=None,
    )
    retry_callbacks = tuple(
        replace(
            callback,
            attempt_id=retry_id,
            connection_session_id=retry_session,
            provider_request_id=retry.provider_request_id,
            received_at=callback.received_at + timedelta(seconds=5),
        )
        for callback in snapshot.callbacks[:2]
    )
    retry_marker = replace(
        snapshot.completion_markers[0],
        attempt_id=retry_id,
        connection_session_id=retry_session,
        provider_request_id=retry.provider_request_id,
        completed_at=retry_callbacks[1].received_at,
    )
    request_snapshots = tuple(
        replace(item, attempt_count=2, selected_attempt_id=retry_id)
        if item.request_sha256 == first_attempt.request_sha256
        else item
        for item in snapshot.requests
    )
    restarted = replace(
        snapshot,
        requests=request_snapshots,
        attempts=(invalidated, retry, *snapshot.attempts[1:]),
        callbacks=(*retry_callbacks, *snapshot.callbacks[2:]),
        completion_markers=(retry_marker, snapshot.completion_markers[1]),
    )

    artifact = build_ibkr_historical_result_artifact(plan, restarted)
    manifest = write_ibkr_historical_result(tmp_path / "restart", artifact)
    verified = verify_ibkr_historical_result(manifest)
    bar_result = next(
        item
        for item in verified.request_results
        if item.request_payload["kind"] == IbkrHistoricalRequestKind.MIDPOINT_BARS.value
    )
    assert bar_result.evidence_disposition is IbkrHistoricalEvidenceDisposition.SUCCEEDED
    assert [item["status"] for item in bar_result.retry_history] == [
        IbkrAttemptStatus.INVALIDATED.value,
        IbkrAttemptStatus.SUCCEEDED.value,
    ]
    assert bar_result.retry_history[0]["terminal_disposition"] is None


def test_result_builder_accepts_marker_before_attempt_finalization() -> None:
    plan, snapshot = _build_fixture()
    adjusted = replace(
        snapshot,
        attempts=(
            replace(snapshot.attempts[0], terminal_at=_START + timedelta(seconds=11)),
            snapshot.attempts[1],
        ),
    )

    artifact = build_ibkr_historical_result_artifact(plan, adjusted)
    bar_result = next(
        item
        for item in artifact.request_results
        if item.request_payload["kind"] == IbkrHistoricalRequestKind.MIDPOINT_BARS.value
    )
    assert bar_result.accepted_rows


def test_result_replay_rejects_completion_marker_callback_mismatch() -> None:
    plan, snapshot = _build_fixture()
    artifact = build_ibkr_historical_result_artifact(plan, snapshot)
    request = next(
        item for item in plan.requests if item.kind is IbkrHistoricalRequestKind.MIDPOINT_BARS
    )
    result = next(
        item for item in artifact.request_results if item.request_sha256 == request.request_sha256
    )
    mutated_markers = tuple(
        replace(marker, payload={"stale": True})
        if marker.attempt_id == result.selected_attempt_id
        else marker
        for marker in result.completion_markers
    )
    mutated = _rehash_result(result, completion_markers=mutated_markers)

    with pytest.raises(ValueError, match="raw completion callback"):
        replay_ibkr_historical_request_result(request, mutated)


def test_result_replay_rejects_valid_closure_labelled_retryable() -> None:
    plan, snapshot = _build_fixture()
    artifact = build_ibkr_historical_result_artifact(plan, snapshot)
    request = next(
        item for item in plan.requests if item.kind is IbkrHistoricalRequestKind.MIDPOINT_BARS
    )
    result = next(
        item for item in artifact.request_results if item.request_sha256 == request.request_sha256
    )
    first = replace(
        result.attempts[0],
        status=IbkrAttemptStatus.RETRYABLE_FAILURE,
        terminal_at=_START + timedelta(seconds=4),
        terminal_disposition=IbkrTerminalDisposition.PROVIDER_REJECTED,
        detail="transient provider failure",
    )
    second_id = UUID("00000000-0000-0000-0000-000000000014")
    second_session = UUID("00000000-0000-0000-0000-000000000004")
    second = replace(
        result.attempts[0],
        attempt_id=second_id,
        attempt_ordinal=2,
        connection_session_id=second_session,
        provider_request_id=104,
        started_at=_START + timedelta(seconds=5),
        terminal_at=_START + timedelta(seconds=10),
        status=IbkrAttemptStatus.SUCCEEDED,
        terminal_disposition=IbkrTerminalDisposition.SUCCEEDED,
        detail=None,
    )
    first_callbacks = tuple(
        replace(
            callback,
            received_at=callback.received_at,
        )
        for callback in result.callbacks
    )
    second_callbacks = tuple(
        replace(
            callback,
            callback_id=callback.callback_id + 10,
            attempt_id=second_id,
            connection_session_id=second_session,
            provider_request_id=second.provider_request_id,
            received_at=callback.received_at + timedelta(seconds=5),
        )
        for callback in result.callbacks
    )
    first_marker = replace(
        result.completion_markers[0],
        completed_at=first_callbacks[1].received_at,
    )
    second_marker = replace(
        result.completion_markers[0],
        marker_id=result.completion_markers[0].marker_id + 10,
        attempt_id=second_id,
        connection_session_id=second_session,
        provider_request_id=second.provider_request_id,
        completed_at=second_callbacks[1].received_at,
    )
    mutated = _rehash_result(
        result,
        attempts=(first, second),
        callbacks=(*first_callbacks, *second_callbacks),
        completion_markers=(first_marker, second_marker),
        selected_attempt_id=second_id,
    )

    with pytest.raises(ValueError, match="status/disposition"):
        replay_ibkr_historical_request_result(request, mutated)


def test_result_builder_classifies_missing_ohlc_evidence() -> None:
    plan, snapshot = _build_fixture()
    payload = dict(snapshot.callbacks[0].payload)
    payload.pop("close")
    malformed = replace(
        snapshot,
        callbacks=(replace(snapshot.callbacks[0], payload=payload), *snapshot.callbacks[1:]),
    )

    artifact = build_ibkr_historical_result_artifact(plan, malformed)
    bar_result = next(
        item
        for item in artifact.request_results
        if item.request_payload["kind"] == IbkrHistoricalRequestKind.MIDPOINT_BARS.value
    )
    assert (
        bar_result.evidence_disposition
        is IbkrHistoricalEvidenceDisposition.INVALID_CALLBACK_EVIDENCE
    )
    assert bar_result.accepted_rows == ()


def test_result_builder_classifies_overlapping_schedule_conflict() -> None:
    plan, snapshot = _build_fixture()
    schedule_payload = {
        "sessions": [
            {
                "start": utc_text(_START),
                "end": utc_text(_START + timedelta(seconds=90)),
                "active": True,
            },
            {
                "start": utc_text(_START + timedelta(minutes=1)),
                "end": utc_text(_END),
                "active": False,
            },
        ]
    }
    conflicting = replace(
        snapshot,
        callbacks=(
            *snapshot.callbacks[:2],
            replace(snapshot.callbacks[2], payload=schedule_payload),
            snapshot.callbacks[3],
        ),
    )

    artifact = build_ibkr_historical_result_artifact(plan, conflicting)
    schedule_result = next(
        item
        for item in artifact.request_results
        if item.request_payload["kind"] == IbkrHistoricalRequestKind.SCHEDULE.value
    )
    assert (
        schedule_result.evidence_disposition
        is IbkrHistoricalEvidenceDisposition.CONFLICTING_CALLBACK_EVIDENCE
    )
    assert schedule_result.session_state == "UNKNOWN"


def test_result_replay_rejects_missing_completion_marker() -> None:
    plan, snapshot = _build_fixture()
    artifact = build_ibkr_historical_result_artifact(plan, snapshot)
    request = next(
        item for item in plan.requests if item.kind is IbkrHistoricalRequestKind.MIDPOINT_BARS
    )
    result = next(
        item for item in artifact.request_results if item.request_sha256 == request.request_sha256
    )
    mutated = _rehash_result(result, completion_markers=())

    with pytest.raises(ValueError, match="one-to-one"):
        replay_ibkr_historical_request_result(request, mutated)


def test_result_replay_rejects_success_without_completion_callback_or_marker() -> None:
    plan, snapshot = _build_fixture()
    artifact = build_ibkr_historical_result_artifact(plan, snapshot)
    request = next(
        item for item in plan.requests if item.kind is IbkrHistoricalRequestKind.MIDPOINT_BARS
    )
    result = next(
        item for item in artifact.request_results if item.request_sha256 == request.request_sha256
    )
    callbacks = tuple(
        callback
        for callback in result.callbacks
        if callback.kind is not IbkrHistoricalCallbackKind.COMPLETION
    )
    mutated = _rehash_result(result, callbacks=callbacks, completion_markers=())

    with pytest.raises(ValueError, match="eligible completion marker"):
        replay_ibkr_historical_request_result(request, mutated)


def test_result_replay_rejects_duplicate_request_record_identities() -> None:
    plan, snapshot = _build_fixture()
    artifact = build_ibkr_historical_result_artifact(plan, snapshot)
    request = next(
        item for item in plan.requests if item.kind is IbkrHistoricalRequestKind.MIDPOINT_BARS
    )
    result = next(
        item for item in artifact.request_results if item.request_sha256 == request.request_sha256
    )

    duplicate_callback = replace(
        result.callbacks[1],
        callback_id=result.callbacks[0].callback_id,
    )
    mutated = _rehash_result(
        result,
        callbacks=(result.callbacks[0], duplicate_callback),
    )
    with pytest.raises(ValueError, match="callback identities"):
        replay_ibkr_historical_request_result(request, mutated)

    duplicate_marker = replace(
        result.completion_markers[0],
        marker_id=result.completion_markers[0].marker_id,
    )
    mutated = _rehash_result(
        result,
        completion_markers=(result.completion_markers[0], duplicate_marker),
    )
    with pytest.raises(ValueError, match="completion marker identities"):
        replay_ibkr_historical_request_result(request, mutated)


def test_result_builder_reconciles_schedule_activity_order_independently() -> None:
    plan, snapshot = _build_fixture()
    active_session: dict[str, JsonValue] = {
        "start": utc_text(_START),
        "end": utc_text(_END),
        "active": True,
    }
    inactive_session: dict[str, JsonValue] = {
        "start": utc_text(_START),
        "end": utc_text(_END),
        "active": False,
    }
    cases: tuple[tuple[dict[str, JsonValue], dict[str, JsonValue]], ...] = (
        ({"sessions": [active_session]}, {"active": False}),
        ({"active": False}, {"sessions": [active_session]}),
        ({"sessions": [active_session]}, {"sessions": []}),
        ({"sessions": []}, {"sessions": [active_session]}),
        ({"sessions": [inactive_session]}, {"active": True}),
        ({"active": True}, {"sessions": [inactive_session]}),
    )

    for first_payload, second_payload in cases:
        first_callback = replace(snapshot.callbacks[2], payload=first_payload)
        second_callback = replace(
            snapshot.callbacks[2],
            callback_id=5,
            sequence=2,
            received_at=_START + timedelta(seconds=5),
            payload=second_payload,
        )
        completion = replace(
            snapshot.callbacks[3],
            sequence=3,
            received_at=_START + timedelta(seconds=6),
        )
        marker = replace(
            snapshot.completion_markers[1],
            sequence=3,
            completed_at=completion.received_at,
            raw_schedule_callback_count=2,
        )
        mutated = replace(
            snapshot,
            callbacks=(
                snapshot.callbacks[0],
                snapshot.callbacks[1],
                first_callback,
                second_callback,
                completion,
            ),
            completion_markers=(snapshot.completion_markers[0], marker),
        )
        schedule_artifact = build_ibkr_historical_result_artifact(plan, mutated)
        schedule_result = next(
            item
            for item in schedule_artifact.request_results
            if item.request_payload["kind"] == IbkrHistoricalRequestKind.SCHEDULE.value
        )
        assert (
            schedule_result.evidence_disposition
            is IbkrHistoricalEvidenceDisposition.CONFLICTING_CALLBACK_EVIDENCE
        )
        assert schedule_result.session_state == "UNKNOWN"


def test_aggregate_replay_rejects_duplicate_callback_identity_across_children() -> None:
    plan, snapshot = _build_fixture()
    artifact = build_ibkr_historical_result_artifact(plan, snapshot)
    bar_result, schedule_result = _bar_and_schedule_results(artifact)
    duplicate_callback = replace(
        schedule_result.callbacks[0],
        callback_id=bar_result.callbacks[0].callback_id,
    )
    mutated_schedule = _rehash_result(
        schedule_result,
        callbacks=(duplicate_callback, *schedule_result.callbacks[1:]),
    )
    mutated_aggregate = _rehash_aggregate(
        artifact.aggregate,
        (bar_result, mutated_schedule),
    )

    with pytest.raises(ValueError, match="aggregate callback identities"):
        replay_ibkr_historical_aggregate_result(
            plan,
            snapshot.plan.plan_bytes,
            (bar_result, mutated_schedule),
            mutated_aggregate,
        )


def test_aggregate_replay_rejects_duplicate_marker_identity_across_children() -> None:
    plan, snapshot = _build_fixture()
    artifact = build_ibkr_historical_result_artifact(plan, snapshot)
    bar_result, schedule_result = _bar_and_schedule_results(artifact)
    duplicate_marker = replace(
        schedule_result.completion_markers[0],
        marker_id=bar_result.completion_markers[0].marker_id,
    )
    mutated_schedule = _rehash_result(
        schedule_result,
        completion_markers=(duplicate_marker,),
    )
    mutated_aggregate = _rehash_aggregate(
        artifact.aggregate,
        (bar_result, mutated_schedule),
    )

    with pytest.raises(ValueError, match="aggregate completion marker identities"):
        replay_ibkr_historical_aggregate_result(
            plan,
            snapshot.plan.plan_bytes,
            (bar_result, mutated_schedule),
            mutated_aggregate,
        )


def test_aggregate_replay_rejects_duplicate_attempt_identity_across_children() -> None:
    plan, snapshot = _build_fixture()
    artifact = build_ibkr_historical_result_artifact(plan, snapshot)
    bar_result, schedule_result = _bar_and_schedule_results(artifact)
    shared_attempt_id = bar_result.attempts[0].attempt_id
    mutated_attempt = replace(schedule_result.attempts[0], attempt_id=shared_attempt_id)
    mutated_callbacks = tuple(
        replace(callback, attempt_id=shared_attempt_id) for callback in schedule_result.callbacks
    )
    mutated_markers = tuple(
        replace(marker, attempt_id=shared_attempt_id)
        for marker in schedule_result.completion_markers
    )
    retry_history = ({**schedule_result.retry_history[0], "attempt_id": str(shared_attempt_id)},)
    mutated_schedule = _rehash_result(
        schedule_result,
        attempts=(mutated_attempt,),
        callbacks=mutated_callbacks,
        completion_markers=mutated_markers,
        selected_attempt_id=shared_attempt_id,
        retry_history=retry_history,
    )
    mutated_aggregate = _rehash_aggregate(
        artifact.aggregate,
        (bar_result, mutated_schedule),
    )

    with pytest.raises(ValueError, match="aggregate attempt identities"):
        replay_ibkr_historical_aggregate_result(
            plan,
            snapshot.plan.plan_bytes,
            (bar_result, mutated_schedule),
            mutated_aggregate,
        )


def test_aggregate_replay_rejects_duplicate_provider_request_identity_across_children() -> None:
    plan, snapshot = _build_fixture()
    artifact = build_ibkr_historical_result_artifact(plan, snapshot)
    bar_result, schedule_result = _bar_and_schedule_results(artifact)
    source_attempt = bar_result.attempts[0]
    mutated_attempt = replace(
        schedule_result.attempts[0],
        connection_session_id=source_attempt.connection_session_id,
        connection_generation=source_attempt.connection_generation,
        provider_request_id=source_attempt.provider_request_id,
    )
    mutated_callbacks = tuple(
        replace(
            callback,
            connection_session_id=source_attempt.connection_session_id,
            connection_generation=source_attempt.connection_generation,
            provider_request_id=source_attempt.provider_request_id,
        )
        for callback in schedule_result.callbacks
    )
    mutated_markers = tuple(
        replace(
            marker,
            connection_session_id=source_attempt.connection_session_id,
            connection_generation=source_attempt.connection_generation,
            provider_request_id=source_attempt.provider_request_id,
        )
        for marker in schedule_result.completion_markers
    )
    mutated_schedule = _rehash_result(
        schedule_result,
        attempts=(mutated_attempt,),
        callbacks=mutated_callbacks,
        completion_markers=mutated_markers,
    )
    mutated_aggregate = _rehash_aggregate(
        artifact.aggregate,
        (bar_result, mutated_schedule),
    )

    with pytest.raises(ValueError, match="provider request identities"):
        replay_ibkr_historical_aggregate_result(
            plan,
            snapshot.plan.plan_bytes,
            (bar_result, mutated_schedule),
            mutated_aggregate,
        )


def test_aggregate_replay_rejects_duplicate_provider_request_identity_between_retries() -> None:
    plan, snapshot = _build_fixture()
    artifact = build_ibkr_historical_result_artifact(plan, snapshot)
    bar_result, schedule_result = _bar_and_schedule_results(artifact)
    request = next(
        item for item in plan.requests if item.kind is IbkrHistoricalRequestKind.MIDPOINT_BARS
    )
    first_terminal_at = _START + timedelta(seconds=4)
    second_id = UUID("00000000-0000-0000-0000-000000000014")
    second_started_at = _START + timedelta(seconds=5)
    second_terminal_at = _START + timedelta(seconds=10)
    first = replace(
        bar_result.attempts[0],
        status=IbkrAttemptStatus.INVALIDATED,
        terminal_at=first_terminal_at,
        terminal_disposition=None,
        detail="connection invalidated",
    )
    second = replace(
        bar_result.attempts[0],
        attempt_id=second_id,
        attempt_ordinal=2,
        started_at=second_started_at,
        terminal_at=second_terminal_at,
        status=IbkrAttemptStatus.SUCCEEDED,
        terminal_disposition=IbkrTerminalDisposition.SUCCEEDED,
        detail=None,
    )
    second_callbacks = tuple(
        replace(
            callback,
            callback_id=callback.callback_id + 10,
            attempt_id=second_id,
            received_at=callback.received_at + timedelta(seconds=5),
        )
        for callback in bar_result.callbacks
    )
    second_marker = replace(
        bar_result.completion_markers[0],
        marker_id=bar_result.completion_markers[0].marker_id + 10,
        attempt_id=second_id,
        completed_at=second_callbacks[1].received_at,
    )
    retry_history = (
        {
            "attempt_id": str(first.attempt_id),
            "attempt_ordinal": first.attempt_ordinal,
            "status": first.status.value,
            "terminal_at": utc_text(first_terminal_at),
            "terminal_disposition": None,
        },
        {
            "attempt_id": str(second.attempt_id),
            "attempt_ordinal": second.attempt_ordinal,
            "status": second.status.value,
            "terminal_at": utc_text(second_terminal_at),
            "terminal_disposition": IbkrTerminalDisposition.SUCCEEDED.value,
        },
    )
    mutated_bar = _rehash_result(
        bar_result,
        attempts=(first, second),
        callbacks=second_callbacks,
        completion_markers=(second_marker,),
        selected_attempt_id=second_id,
        retry_history=retry_history,
    )
    replay_ibkr_historical_request_result(request, mutated_bar)
    mutated_aggregate = _rehash_aggregate(
        artifact.aggregate,
        (mutated_bar, schedule_result),
    )

    with pytest.raises(ValueError, match="provider request identities"):
        replay_ibkr_historical_aggregate_result(
            plan,
            snapshot.plan.plan_bytes,
            (mutated_bar, schedule_result),
            mutated_aggregate,
        )
