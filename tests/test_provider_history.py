from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

import qtrad.application.provider_history as provider_history_application
from qtrad.__main__ import build_parser
from qtrad.application.ibkr_results import build_ibkr_historical_result_artifact
from qtrad.application.provider_history import (
    build_provider_history_dataset,
    iter_provider_history_partitions,
    provider_history_partition_row_bounds,
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
    IbkrHistoricalAttemptEvidence,
    IbkrHistoricalCallbackEvidence,
    IbkrHistoricalCompletionEvidence,
    IbkrHistoricalExecutionSnapshot,
    IbkrHistoricalPlanSnapshot,
    IbkrHistoricalRequestSnapshot,
    canonical_json_bytes,
    sha256_bytes,
)
from qtrad.domain.identifiers import InstrumentId
from qtrad.domain.provider_history import (
    ProviderHistoricalAvailabilityPolicy,
    ProviderHistoricalObservation,
)
from qtrad.runtime import provider_history as provider_history_runtime
from qtrad.runtime.ibkr_results import (
    IbkrHistoricalResultStream,
    verify_ibkr_historical_result_stream,
    write_ibkr_historical_result,
)
from qtrad.runtime.provider_history import publish_provider_history, verify_provider_history
from tests.test_ibkr_historical_results import _build_fixture

_START = datetime(2026, 2, 1, tzinfo=UTC)
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


def _published_provider_history(tmp_path: Path):
    plan, snapshot = _build_fixture()
    artifact = build_ibkr_historical_result_artifact(plan, snapshot)
    result_manifest = write_ibkr_historical_result(tmp_path / "result", artifact)
    dataset = build_provider_history_dataset(
        artifact,
        availability_delay=timedelta(minutes=5),
    )
    provider_manifest = publish_provider_history(
        tmp_path / "provider",
        source_manifest=result_manifest,
        source_artifact=artifact,
        dataset=dataset,
    )
    return artifact, dataset, provider_manifest


def _request(
    start: datetime,
    end: datetime,
    kind: IbkrHistoricalRequestKind,
    *,
    duration: str = "1 D",
) -> IbkrHistoricalRequest:
    bar_size = "1 min" if kind is IbkrHistoricalRequestKind.MIDPOINT_BARS else "1 day"
    what_to_show = "MIDPOINT" if kind is IbkrHistoricalRequestKind.MIDPOINT_BARS else "SCHEDULE"
    identity: dict[str, JsonValue] = {
        "instrument_id": str(_INSTRUMENT),
        "fingerprint": _FINGERPRINT.as_json_value(),
        "kind": kind.value,
        "interval_start": utc_text(start),
        "interval_end": utc_text(end),
        "end_date_time": ibkr_end_date_time(end),
        "duration": duration,
        "bar_size": bar_size,
        "what_to_show": what_to_show,
        "use_rth": False,
        "format_date": 2,
        "keep_up_to_date": False,
    }
    return IbkrHistoricalRequest(
        instrument_id=_INSTRUMENT,
        fingerprint=_FINGERPRINT,
        kind=kind,
        interval_start=start,
        interval_end=end,
        end_date_time=ibkr_end_date_time(end),
        duration=duration,
        bar_size=bar_size,
        what_to_show=what_to_show,
        use_rth=False,
        format_date=2,
        keep_up_to_date=False,
        request_sha256=sha256_json(identity),
    )


def _attempt(
    plan: IbkrHistoricalPlan,
    request: IbkrHistoricalRequest,
    ordinal: int,
    *,
    terminal_delay_seconds: int = 10,
) -> IbkrHistoricalAttemptEvidence:
    started_at = request.interval_start + timedelta(seconds=ordinal)
    return IbkrHistoricalAttemptEvidence(
        attempt_id=UUID(int=ordinal),
        plan_sha256=plan.plan_sha256,
        request_sha256=request.request_sha256,
        attempt_ordinal=1,
        connection_session_id=UUID(int=100_000 + ordinal),
        provider_request_id=ordinal,
        connection_generation=1,
        started_at=started_at,
        status=IbkrAttemptStatus.SUCCEEDED,
        terminal_at=started_at + timedelta(seconds=terminal_delay_seconds),
        terminal_disposition=IbkrTerminalDisposition.SUCCEEDED,
        detail=None,
    )


def _callback(
    *,
    callback_id: int,
    attempt: IbkrHistoricalAttemptEvidence,
    sequence: int,
    kind: IbkrHistoricalCallbackKind,
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
        received_at=attempt.started_at + timedelta(seconds=sequence + 1),
        payload=payload,
        closure_eligible=True,
    )


def _completion(
    *,
    marker_id: int,
    attempt: IbkrHistoricalAttemptEvidence,
    sequence: int,
    midpoint_count: int,
    schedule_count: int,
) -> IbkrHistoricalCompletionEvidence:
    return IbkrHistoricalCompletionEvidence(
        marker_id=marker_id,
        attempt_id=attempt.attempt_id,
        connection_session_id=attempt.connection_session_id,
        provider_request_id=attempt.provider_request_id,
        connection_generation=1,
        sequence=sequence,
        completed_at=attempt.started_at + timedelta(seconds=sequence + 1),
        raw_midpoint_bar_callback_count=midpoint_count,
        raw_schedule_callback_count=schedule_count,
        closure_eligible=True,
        payload={},
    )


def _build_stage6_artifact(
    *,
    day_count: int,
    no_data_bar_days: frozenset[int] = frozenset(),
    minute_span: bool = False,
    bars_per_request: int = 1,
):
    if bars_per_request <= 0:
        raise ValueError("bars_per_request must be positive")
    span = timedelta(minutes=1) if minute_span else timedelta(days=1)
    end = _START + day_count * span
    bar_requests = tuple(
        _request(
            _START + day * span,
            _START + (day + 1) * span,
            IbkrHistoricalRequestKind.MIDPOINT_BARS,
        )
        for day in range(day_count)
    )
    if minute_span:
        schedule_requests = (
            _request(
                _START,
                end,
                IbkrHistoricalRequestKind.SCHEDULE,
                duration="1 D",
            ),
        )
    else:
        schedule_requests_list: list[IbkrHistoricalRequest] = []
        schedule_start = _START
        while schedule_start < end:
            schedule_end = min(schedule_start + timedelta(days=28), end)
            schedule_days = (schedule_end - schedule_start).days
            schedule_requests_list.append(
                _request(
                    schedule_start,
                    schedule_end,
                    IbkrHistoricalRequestKind.SCHEDULE,
                    duration=f"{schedule_days} D",
                )
            )
            schedule_start = schedule_end
        schedule_requests = tuple(schedule_requests_list)
    requests = bar_requests + schedule_requests
    eligible = (IbkrPlannedContract(_INSTRUMENT, _FINGERPRINT),)
    plan_identity: dict[str, JsonValue] = {
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
        "end": utc_text(_START + day_count * span),
        "eligible_contracts": [eligible[0].as_json_value()],
        "requests": [
            request.as_json_value()
            for request in sorted(
                requests,
                key=lambda item: (
                    str(item.instrument_id),
                    item.kind.value,
                    item.interval_start,
                    item.request_sha256,
                ),
            )
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
        end=_START + day_count * span,
        eligible_contracts=eligible,
        requests=requests,
        plan_sha256=sha256_json(plan_identity),
    )
    attempts: list[IbkrHistoricalAttemptEvidence] = []
    callbacks: list[IbkrHistoricalCallbackEvidence] = []
    markers: list[IbkrHistoricalCompletionEvidence] = []
    snapshots: list[IbkrHistoricalRequestSnapshot] = []
    for ordinal, request in enumerate(requests, start=1):
        attempt = _attempt(
            plan,
            request,
            ordinal,
            terminal_delay_seconds=max(10, bars_per_request + 3),
        )
        attempts.append(attempt)
        day = (request.interval_start - _START) // span
        if request.kind is IbkrHistoricalRequestKind.MIDPOINT_BARS:
            if day in no_data_bar_days:
                bar_times = (request.interval_end,)
            elif minute_span:
                bar_times = (request.interval_start,)
            else:
                bar_times = tuple(
                    request.interval_start + timedelta(minutes=index + 1)
                    for index in range(bars_per_request)
                )
            for bar_index, bar_time in enumerate(bar_times, start=1):
                callbacks.append(
                    _callback(
                        callback_id=ordinal * (bars_per_request + 2) + bar_index,
                        attempt=attempt,
                        sequence=bar_index,
                        kind=IbkrHistoricalCallbackKind.MIDPOINT_BAR,
                        payload={
                            "date": int(bar_time.timestamp()),
                            "open": "1.1000",
                            "high": "1.1010",
                            "low": "1.0990",
                            "close": "1.1005",
                            "volume": 7,
                            "wap": "1.1001",
                            "count": 3,
                        },
                    )
                )
            completion_sequence = len(bar_times) + 1
            callbacks.append(
                _callback(
                    callback_id=ordinal * (bars_per_request + 2) + completion_sequence,
                    attempt=attempt,
                    sequence=completion_sequence,
                    kind=IbkrHistoricalCallbackKind.COMPLETION,
                    payload={},
                )
            )
            markers.append(
                _completion(
                    marker_id=ordinal,
                    attempt=attempt,
                    sequence=completion_sequence,
                    midpoint_count=len(bar_times),
                    schedule_count=0,
                )
            )
        else:
            callbacks.append(
                _callback(
                    callback_id=ordinal * (bars_per_request + 2) + 1,
                    attempt=attempt,
                    sequence=1,
                    kind=IbkrHistoricalCallbackKind.SCHEDULE,
                    payload={"sessions": []},
                )
            )
            callbacks.append(
                _callback(
                    callback_id=ordinal * (bars_per_request + 2) + 2,
                    attempt=attempt,
                    sequence=2,
                    kind=IbkrHistoricalCallbackKind.COMPLETION,
                    payload={},
                )
            )
            markers.append(
                _completion(
                    marker_id=ordinal,
                    attempt=attempt,
                    sequence=2,
                    midpoint_count=0,
                    schedule_count=1,
                )
            )
        snapshots.append(
            IbkrHistoricalRequestSnapshot(
                plan_sha256=plan.plan_sha256,
                request_sha256=request.request_sha256,
                request_payload=request.as_json_value(),
                instrument_id=str(_INSTRUMENT),
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
        )
    plan_bytes = ibkr_historical_plan_bytes(plan)
    plan_snapshot = IbkrHistoricalPlanSnapshot(
        plan_sha256=plan.plan_sha256,
        plan_bytes=plan_bytes,
        plan_bytes_sha256=sha256_bytes(plan_bytes),
        plan_payload=plan.as_json_value(),
        registered_at=_START,
        publication_status=IbkrPublicationStatus.PENDING,
    )
    snapshot = IbkrHistoricalExecutionSnapshot(
        plan=plan_snapshot,
        requests=tuple(snapshots),
        attempts=tuple(attempts),
        callbacks=tuple(callbacks),
        completion_markers=tuple(markers),
    )
    return build_ibkr_historical_result_artifact(plan, snapshot)


def test_provider_history_builds_declared_availability_and_distinct_lineage() -> None:
    plan, snapshot = _build_fixture()
    artifact = build_ibkr_historical_result_artifact(plan, snapshot)

    five_minutes = build_provider_history_dataset(
        artifact,
        availability_delay=timedelta(minutes=5),
    )
    six_minutes = build_provider_history_dataset(
        artifact,
        availability_delay=timedelta(minutes=6),
    )

    assert five_minutes.row_count == 1
    assert six_minutes.row_count == 1
    assert five_minutes.dataset_sha256 != six_minutes.dataset_sha256
    assert five_minutes.partitions[0].partition_sha256 != six_minutes.partitions[0].partition_sha256
    partition = next(
        iter_provider_history_partitions(artifact, policy=five_minutes.availability_policy)
    )
    row = partition.rows[0]
    assert row.available_at == row.interval_end + timedelta(minutes=5)
    assert row.availability_delay == "PT5M"
    assert "received_at" not in row.as_json_value()
    assert "persisted_at" not in row.as_json_value()


def test_provider_history_closure_replays_from_embedded_result(tmp_path: Path) -> None:
    _, dataset, manifest = _published_provider_history(tmp_path)

    verified = verify_provider_history(manifest)

    assert verified == dataset
    assert verified.row_count == 1
    assert not hasattr(verified, "rows")
    partition_path = next((manifest.parent / "observations").rglob("*.parquet"))
    partition = provider_history_runtime._read_parquet_rows(
        partition_path,
        expected_row_count=1,
        row_upper_bound=2,
    )
    assert partition.rows[0].schedule_evidence["schedule_state"] == "INACTIVE"


def test_provider_history_rejects_parquet_mutation_and_missing_child(tmp_path: Path) -> None:
    _, _, manifest = _published_provider_history(tmp_path)
    parquet_path = next((manifest.parent / "observations").rglob("*.parquet"))
    parquet_path.write_bytes(parquet_path.read_bytes() + b"mutation")
    with pytest.raises(ValueError, match=r"Parquet.*bytes"):
        verify_provider_history(manifest)

    second_root = tmp_path / "second"
    second_root.mkdir()
    _, _, second_manifest = _published_provider_history(second_root)
    child = second_manifest.parent / "source-result" / "plan.json"
    child.unlink()
    with pytest.raises((FileNotFoundError, ValueError), match=r"plan|closure|child"):
        verify_provider_history(second_manifest)


def test_provider_history_cli_round_trip_arguments() -> None:
    parser = build_parser()
    build_args = parser.parse_args(
        [
            "research",
            "observations",
            "build-provider-history",
            "--historical-result",
            "/tmp/result/manifest.json",
            "--availability-delay",
            "PT5M",
            "--output",
            "/tmp/provider",
        ]
    )
    verify_args = parser.parse_args(
        [
            "research",
            "observations",
            "verify-provider-history",
            "--manifest",
            "/tmp/provider/manifest.json",
        ]
    )

    assert build_args.observations_command == "build-provider-history"
    assert build_args.availability_delay == timedelta(minutes=5)
    assert verify_args.observations_command == "verify-provider-history"


def test_provider_history_eligibility_replays_two_chunks_and_excludes_incomplete_instrument() -> (
    None
):
    artifact = _build_stage6_artifact(
        day_count=2,
        no_data_bar_days=frozenset({1}),
    )

    assert artifact.aggregate.entitlement_summary["provider_history_eligible_instruments"] == []
    dataset = build_provider_history_dataset(
        artifact,
        availability_delay=timedelta(minutes=5),
    )

    assert dataset.row_count == 0
    assert dataset.partitions == ()
    assert provider_history_partition_row_bounds(artifact) == {}


def test_provider_history_publishes_26_week_partitions_through_real_replay(tmp_path: Path) -> None:
    artifact = _build_stage6_artifact(day_count=26 * 7, bars_per_request=60)
    result_manifest = write_ibkr_historical_result(tmp_path / "result", artifact)

    dataset = build_provider_history_dataset(
        artifact,
        availability_delay=timedelta(minutes=5),
    )
    assert dataset.row_count == 26 * 7 * 60
    assert len(dataset.partitions) == 26 * 7
    assert all(partition.row_count == 60 for partition in dataset.partitions)

    manifest = publish_provider_history(
        tmp_path / "provider",
        source_manifest=result_manifest,
        source_artifact=artifact,
        dataset=dataset,
    )
    verified = verify_provider_history(manifest)
    document = json.loads(manifest.read_text())

    assert verified == dataset
    assert not hasattr(dataset, "rows")
    assert document["dataset"]["partitions"] == [
        partition.as_json_value() for partition in dataset.partitions
    ]
    assert "observation_sha256" not in document["dataset"]
    assert document["source_plan_row_bound"] == 26 * 7 * 24 * 60
    assert manifest.stat().st_size < 2_000_000


def test_provider_history_rejects_footer_overbound_before_decoding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _build_stage6_artifact(day_count=1, minute_span=True)
    dataset = build_provider_history_dataset(
        artifact,
        availability_delay=timedelta(minutes=5),
    )
    partition = next(iter_provider_history_partitions(artifact, policy=dataset.availability_policy))
    malicious_path = tmp_path / "malicious.parquet"
    malicious_path.write_bytes(
        provider_history_runtime._parquet_bytes((partition.rows[0], partition.rows[0]))
    )
    bounds = provider_history_partition_row_bounds(artifact)
    assert bounds[partition.key] == 1
    assert provider_history_runtime._parquet_footer_row_count(malicious_path) == 2

    decoded = False

    def fail_if_decoded(_value: object) -> ProviderHistoricalObservation:
        nonlocal decoded
        decoded = True
        raise AssertionError("row decoding must not begin before footer validation")

    monkeypatch.setattr(ProviderHistoricalObservation, "from_json_value", fail_if_decoded)
    with pytest.raises(ValueError, match="footer row count"):
        provider_history_runtime._read_parquet_rows(
            malicious_path,
            expected_row_count=dataset.partitions[0].row_count,
            row_upper_bound=bounds[partition.key],
        )
    assert not decoded


def test_provider_history_rejects_declared_row_upper_bound_mutation(tmp_path: Path) -> None:
    _, _, manifest = _published_provider_history(tmp_path)
    document = json.loads(manifest.read_text())
    document["files"][0]["row_upper_bound"] += 1
    identity = dict(document)
    identity.pop("manifest_sha256")
    document["manifest_sha256"] = sha256_json(identity)
    manifest.write_bytes(canonical_json_bytes(document))

    with pytest.raises(ValueError, match="row upper bound"):
        verify_provider_history(manifest)


def test_provider_history_enforces_combined_closure_file_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, manifest = _published_provider_history(tmp_path)
    monkeypatch.setattr(provider_history_runtime, "_MAX_CLOSURE_FILES", 5)

    with pytest.raises(ValueError, match="closure exceeds its file bound"):
        verify_provider_history(manifest)


def test_provider_history_streams_one_result_and_partition_at_a_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _build_stage6_artifact(day_count=2)
    result_manifest = write_ibkr_historical_result(tmp_path / "result", artifact)
    source_stream = verify_ibkr_historical_result_stream(result_manifest)
    result_live = 0
    max_result_live = 0
    partition_live = 0
    max_partition_live = 0
    original_results = IbkrHistoricalResultStream.iter_request_results
    original_partitions = provider_history_application.iter_provider_history_partitions

    def tracked_results(
        stream: IbkrHistoricalResultStream,
        *,
        request_order: tuple[IbkrHistoricalRequest, ...] | None = None,
    ):
        nonlocal result_live, max_result_live
        for result in original_results(stream, request_order=request_order):
            result_live += 1
            max_result_live = max(max_result_live, result_live)
            yield result
            result_live -= 1

    def tracked_partitions(
        source: provider_history_application.ProviderHistorySource,
        *,
        policy: ProviderHistoricalAvailabilityPolicy,
    ):
        nonlocal partition_live, max_partition_live
        for partition in original_partitions(source, policy=policy):
            partition_live += 1
            max_partition_live = max(max_partition_live, partition_live)
            yield partition
            partition_live -= 1

    monkeypatch.setattr(IbkrHistoricalResultStream, "iter_request_results", tracked_results)
    monkeypatch.setattr(
        provider_history_application,
        "iter_provider_history_partitions",
        tracked_partitions,
    )
    monkeypatch.setattr(
        provider_history_runtime,
        "iter_provider_history_partitions",
        tracked_partitions,
    )
    dataset = build_provider_history_dataset(
        source_stream,
        availability_delay=timedelta(minutes=5),
    )
    manifest = publish_provider_history(
        tmp_path / "provider",
        source_manifest=result_manifest,
        source_artifact=source_stream,
        dataset=dataset,
    )
    verify_provider_history(manifest)

    assert max_result_live == 1
    assert max_partition_live == 1
