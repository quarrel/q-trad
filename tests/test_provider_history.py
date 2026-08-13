from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

import qtrad.__main__ as qtrad_main
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
    MAX_IBKR_RESULT_BYTES,
    MAX_IBKR_RESULT_REQUEST_BYTES,
    REQUEST_RESULT_CONTRACT,
    IbkrHistoricalAttemptEvidence,
    IbkrHistoricalCallbackEvidence,
    IbkrHistoricalChildReference,
    IbkrHistoricalCompletionEvidence,
    IbkrHistoricalExecutionSnapshot,
    IbkrHistoricalPlanSnapshot,
    IbkrHistoricalRequestSnapshot,
    IbkrHistoricalResultArtifact,
    canonical_json_bytes,
    sha256_bytes,
)
from qtrad.domain.identifiers import InstrumentId
from qtrad.domain.provider_history import (
    ProviderHistoricalAvailabilityPolicy,
    ProviderHistoricalObservation,
)
from qtrad.runtime import ibkr_foundation_bounded as bounded_foundation_runtime
from qtrad.runtime import provider_history as provider_history_runtime
from qtrad.runtime.ibkr_results import (
    IbkrHistoricalResultStream,
    verify_ibkr_historical_result_stream,
    write_ibkr_historical_result,
)
from qtrad.runtime.provider_history import (
    authenticate_provider_history,
    publish_provider_history,
    read_provider_history_source_evidence,
    verify_provider_history,
)
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


def _published_provider_history(
    tmp_path: Path,
    *,
    artifact: IbkrHistoricalResultArtifact | None = None,
):
    if artifact is None:
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
    instrument: InstrumentId = _INSTRUMENT,
    fingerprint: IbkrContractFingerprint = _FINGERPRINT,
) -> IbkrHistoricalRequest:
    bar_size = "1 min" if kind is IbkrHistoricalRequestKind.MIDPOINT_BARS else "1 day"
    what_to_show = "MIDPOINT" if kind is IbkrHistoricalRequestKind.MIDPOINT_BARS else "SCHEDULE"
    identity: dict[str, JsonValue] = {
        "instrument_id": str(instrument),
        "fingerprint": fingerprint.as_json_value(),
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
        instrument_id=instrument,
        fingerprint=fingerprint,
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
    no_data_instruments: frozenset[InstrumentId] = frozenset(),
    minute_span: bool = False,
    bars_per_request: int = 1,
    session_weekdays_only: bool = False,
    instruments: tuple[tuple[InstrumentId, IbkrContractFingerprint], ...] = (
        (_INSTRUMENT, _FINGERPRINT),
    ),
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
            instrument=instrument,
            fingerprint=fingerprint,
        )
        for instrument, fingerprint in instruments
        for day in range(day_count)
    )
    if minute_span:
        schedule_requests = tuple(
            _request(
                _START,
                end,
                IbkrHistoricalRequestKind.SCHEDULE,
                duration="1 D",
                instrument=instrument,
                fingerprint=fingerprint,
            )
            for instrument, fingerprint in instruments
        )
    else:
        schedule_requests_list: list[IbkrHistoricalRequest] = []
        for instrument, fingerprint in instruments:
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
                        instrument=instrument,
                        fingerprint=fingerprint,
                    )
                )
                schedule_start = schedule_end
        schedule_requests = tuple(schedule_requests_list)
    requests = bar_requests + schedule_requests
    eligible = tuple(
        IbkrPlannedContract(instrument, fingerprint) for instrument, fingerprint in instruments
    )
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
        "eligible_contracts": [
            contract.as_json_value()
            for contract in sorted(eligible, key=lambda value: str(value.instrument_id))
        ],
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
            if request.instrument_id in no_data_instruments or day in no_data_bar_days:
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
            session_values: list[JsonValue] = []
            if session_weekdays_only:
                session_day = request.interval_start
                while session_day < request.interval_end:
                    if session_day.weekday() < 5:
                        session_values.append(
                            {
                                "start": int((session_day - timedelta(hours=1)).timestamp()),
                                "end": int(
                                    (session_day + timedelta(hours=1, minutes=1)).timestamp()
                                ),
                                "active": True,
                            }
                        )
                    session_day += timedelta(days=1)
            callbacks.append(
                _callback(
                    callback_id=ordinal * (bars_per_request + 2) + 1,
                    attempt=attempt,
                    sequence=1,
                    kind=IbkrHistoricalCallbackKind.SCHEDULE,
                    payload={"sessions": session_values},
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


def test_provider_history_receipt_is_create_only_and_authentication_is_cheap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, snapshot = _build_fixture()
    artifact = build_ibkr_historical_result_artifact(plan, snapshot)
    result_manifest = write_ibkr_historical_result(tmp_path / "result", artifact)
    dataset = build_provider_history_dataset(
        artifact,
        availability_delay=timedelta(minutes=5),
    )
    receipt = tmp_path / "provider-verification-receipt.json"
    original_verify = provider_history_runtime._verify_provider_history
    verification_count = 0

    def count_verification(path: Path):
        nonlocal verification_count
        verification_count += 1
        return original_verify(path)

    monkeypatch.setattr(provider_history_runtime, "_verify_provider_history", count_verification)
    manifest = publish_provider_history(
        tmp_path / "provider",
        source_manifest=result_manifest,
        source_artifact=artifact,
        dataset=dataset,
    )
    assert verification_count == 0
    verify_provider_history(manifest, receipt_output=receipt)
    assert verification_count == 1

    def reject_deep_work(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("deep provider-history verification reached")

    monkeypatch.setattr(provider_history_runtime, "_read_parquet_rows", reject_deep_work)
    monkeypatch.setattr(
        provider_history_runtime,
        "_start_provider_history_source_replay",
        reject_deep_work,
    )
    evidence = authenticate_provider_history(manifest, receipt=receipt)
    assert evidence.dataset == dataset
    with pytest.raises(FileExistsError):
        verify_provider_history(manifest, receipt_output=receipt)


def test_provider_history_receipt_rejects_changed_verifier_version(tmp_path: Path) -> None:
    _, _, manifest = _published_provider_history(tmp_path)
    receipt = tmp_path / "provider-verification-receipt.json"
    verify_provider_history(manifest, receipt_output=receipt)
    document = json.loads(receipt.read_bytes())
    document["verifier_version"] += 1
    identity = dict(document)
    identity.pop("receipt_sha256")
    document["receipt_sha256"] = provider_history_runtime._sha256_json(identity)
    receipt.write_bytes(canonical_json_bytes(document))

    with pytest.raises(ValueError, match="verifier is unsupported"):
        authenticate_provider_history(manifest, receipt=receipt)


def test_provider_history_receipt_write_failure_leaves_published_closure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, snapshot = _build_fixture()
    artifact = build_ibkr_historical_result_artifact(plan, snapshot)
    result_manifest = write_ibkr_historical_result(tmp_path / "result", artifact)
    dataset = build_provider_history_dataset(
        artifact,
        availability_delay=timedelta(minutes=5),
    )
    manifest = publish_provider_history(
        tmp_path / "provider",
        source_manifest=result_manifest,
        source_artifact=artifact,
        dataset=dataset,
    )
    receipt = tmp_path / "provider-verification-receipt.json"
    original_write = provider_history_runtime._write_create_only

    def reject_receipt_write(path: Path, payload: bytes) -> None:
        if path == receipt:
            raise OSError("receipt write failed")
        original_write(path, payload)

    monkeypatch.setattr(provider_history_runtime, "_write_create_only", reject_receipt_write)
    with pytest.raises(OSError, match="receipt write failed"):
        verify_provider_history(manifest, receipt_output=receipt)
    assert manifest.is_file()
    assert not receipt.exists()


def test_provider_history_staging_failure_publishes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, snapshot = _build_fixture()
    artifact = build_ibkr_historical_result_artifact(plan, snapshot)
    result_manifest = write_ibkr_historical_result(tmp_path / "result", artifact)
    dataset = build_provider_history_dataset(
        artifact,
        availability_delay=timedelta(minutes=5),
    )

    def reject_staging(_path: Path) -> None:
        raise ValueError("staging closure failed")

    monkeypatch.setattr(
        provider_history_runtime,
        "verify_provider_history_file_only",
        reject_staging,
    )
    with pytest.raises(ValueError, match="staging closure failed"):
        publish_provider_history(
            tmp_path / "provider",
            source_manifest=result_manifest,
            source_artifact=artifact,
            dataset=dataset,
        )
    assert not (tmp_path / "provider").exists()


def test_provider_history_cli_reports_publication_then_single_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan, snapshot = _build_fixture()
    artifact = build_ibkr_historical_result_artifact(plan, snapshot)
    result_manifest = write_ibkr_historical_result(tmp_path / "result", artifact)
    original_verify = provider_history_runtime._verify_provider_history
    verification_count = 0

    def count_verification(path: Path):
        nonlocal verification_count
        verification_count += 1
        return original_verify(path)

    monkeypatch.setattr(provider_history_runtime, "_verify_provider_history", count_verification)
    qtrad_main._build_provider_history(
        historical_result_path=result_manifest,
        availability_delay=timedelta(minutes=5),
        output_path=tmp_path / "provider",
        verification_receipt_path=tmp_path / "provider-verification-receipt.json",
    )

    statuses = [json.loads(line)["status"] for line in capsys.readouterr().out.splitlines()]
    assert statuses == ["PUBLISHED_UNVERIFIED", "VERIFIED"]
    assert verification_count == 1


def test_provider_history_verification_failure_retains_published_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan, snapshot = _build_fixture()
    artifact = build_ibkr_historical_result_artifact(plan, snapshot)
    result_manifest = write_ibkr_historical_result(tmp_path / "result", artifact)
    receipt = tmp_path / "provider-verification-receipt.json"

    def reject_verification(_path: Path) -> None:
        raise ValueError("semantic verification failed")

    monkeypatch.setattr(
        provider_history_runtime,
        "_verify_provider_history",
        reject_verification,
    )
    with pytest.raises(ValueError, match="semantic verification failed"):
        qtrad_main._build_provider_history(
            historical_result_path=result_manifest,
            availability_delay=timedelta(minutes=5),
            output_path=tmp_path / "provider",
            verification_receipt_path=receipt,
        )

    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "PUBLISHED_UNVERIFIED"
    assert (tmp_path / "provider" / "manifest.json").is_file()
    assert not receipt.exists()


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
            "--verification-receipt",
            "/tmp/provider-verification-receipt.json",
        ]
    )
    verify_args = parser.parse_args(
        [
            "research",
            "observations",
            "verify-provider-history",
            "--manifest",
            "/tmp/provider/manifest.json",
            "--receipt-output",
            "/tmp/provider-verification-receipt.json",
        ]
    )
    authenticate_args = parser.parse_args(
        [
            "research",
            "observations",
            "authenticate-provider-history",
            "--manifest",
            "/tmp/provider/manifest.json",
            "--receipt",
            "/tmp/provider-verification-receipt.json",
        ]
    )

    assert build_args.observations_command == "build-provider-history"
    assert build_args.availability_delay == timedelta(minutes=5)
    assert build_args.verification_receipt == Path("/tmp/provider-verification-receipt.json")
    assert verify_args.observations_command == "verify-provider-history"
    assert verify_args.receipt_output == Path("/tmp/provider-verification-receipt.json")
    assert authenticate_args.observations_command == "authenticate-provider-history"


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
    artifact = _build_stage6_artifact(day_count=26 * 7, bars_per_request=1)
    result_manifest = write_ibkr_historical_result(tmp_path / "result", artifact)

    dataset = build_provider_history_dataset(
        artifact,
        availability_delay=timedelta(minutes=5),
    )
    assert dataset.row_count == 26 * 7
    assert len(dataset.partitions) == 26 * 7
    assert all(partition.row_count == 1 for partition in dataset.partitions)

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


def test_source_evidence_decodes_request_result_children_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, manifest = _published_provider_history(tmp_path)
    result_iterations = 0
    original_results = IbkrHistoricalResultStream.iter_request_results

    def tracked_results(
        stream: IbkrHistoricalResultStream,
        *,
        request_order: tuple[IbkrHistoricalRequest, ...] | None = None,
    ):
        nonlocal result_iterations
        result_iterations += 1
        yield from original_results(stream, request_order=request_order)

    monkeypatch.setattr(IbkrHistoricalResultStream, "iter_request_results", tracked_results)

    evidence = read_provider_history_source_evidence(manifest)

    assert evidence.dataset.row_count > 0
    assert result_iterations == 1


def test_source_evidence_parallel_replay_matches_serial(tmp_path: Path) -> None:
    _, _, manifest = _published_provider_history(tmp_path)

    serial = read_provider_history_source_evidence(manifest, source_replay_workers=1)
    parallel = read_provider_history_source_evidence(manifest, source_replay_workers=2)

    assert parallel.dataset == serial.dataset
    assert parallel.request_evidence == serial.request_evidence
    assert parallel.observation_summary == serial.observation_summary
    assert tuple(parallel.observations) == tuple(serial.observations)


def test_provider_history_publishes_without_prebuilt_dataset(tmp_path: Path) -> None:
    artifact = _build_stage6_artifact(day_count=2)
    result_manifest = write_ibkr_historical_result(tmp_path / "result", artifact)
    source_artifact = verify_ibkr_historical_result_stream(result_manifest)

    manifest = publish_provider_history(
        tmp_path / "provider",
        source_manifest=result_manifest,
        source_artifact=source_artifact,
        availability_delay=timedelta(minutes=5),
    )
    verified = verify_provider_history(manifest)

    assert verified.row_count == 2
    assert manifest.exists()


def test_provider_history_replays_source_before_parquet_decoding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, manifest = _published_provider_history(tmp_path)
    child = next((manifest.parent / "source-result" / "requests").glob("*.json"))
    child.write_bytes(child.read_bytes() + b"mutation")

    def fail_if_decoded(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Parquet decoding must not begin before Stage 6 replay")

    monkeypatch.setattr(provider_history_runtime, "_read_parquet_rows", fail_if_decoded)

    with pytest.raises(ValueError, match="child bytes digest"):
        verify_provider_history(manifest)


def test_provider_history_build_replays_source_before_bounds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, snapshot = _build_fixture()
    artifact = build_ibkr_historical_result_artifact(plan, snapshot)
    result_manifest = write_ibkr_historical_result(tmp_path / "result", artifact)
    child = next((result_manifest.parent / "requests").glob("*.json"))
    child.write_bytes(child.read_bytes() + b"mutation")

    def fail_if_bounds_derived(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("partition bounds must not be derived before Stage 6 replay")

    monkeypatch.setattr(
        provider_history_application,
        "_partition_row_bounds",
        fail_if_bounds_derived,
    )
    with pytest.raises(ValueError, match="child bytes digest"):
        qtrad_main._build_provider_history(
            historical_result_path=result_manifest,
            availability_delay=timedelta(minutes=5),
            output_path=tmp_path / "provider",
            verification_receipt_path=tmp_path / "provider-verification-receipt.json",
        )


def test_provider_history_orders_hashed_partition_paths(tmp_path: Path) -> None:
    second_instrument = InstrumentId("fx:eur-usd")
    second_fingerprint = replace(
        _FINGERPRINT,
        con_id=43,
        symbol="EUR",
        local_symbol="EUR.USD",
        trading_class="EUR.USD",
    )
    artifact = _build_stage6_artifact(
        day_count=1,
        instruments=(
            (_INSTRUMENT, _FINGERPRINT),
            (second_instrument, second_fingerprint),
        ),
    )
    result_manifest = write_ibkr_historical_result(tmp_path / "result", artifact)
    dataset = build_provider_history_dataset(
        artifact,
        availability_delay=timedelta(minutes=5),
    )
    dataset_paths = [
        provider_history_runtime._partition_path(
            (str(partition.instrument_id), partition.partition_date)
        )
        for partition in dataset.partitions
    ]
    assert dataset_paths != sorted(dataset_paths)

    provider_manifest = publish_provider_history(
        tmp_path / "provider",
        source_manifest=result_manifest,
        source_artifact=artifact,
        dataset=dataset,
    )
    verify_provider_history(provider_manifest)

    rows = read_provider_history_source_evidence(provider_manifest).observations
    assert isinstance(rows, provider_history_runtime.VerifiedProviderHistoryRows)
    offset = 0
    expected_start: dict[str, int] = {}
    for _path, partition in sorted(zip(dataset_paths, dataset.partitions, strict=True)):
        expected_start.setdefault(partition.instrument_id, offset + 1)
        offset += partition.row_count
    for instrument in rows.instruments:
        first = next(
            bounded_foundation_runtime._adapted_instrument_rows(
                rows,
                None,
                instrument,
                dataset.dataset_sha256,
            )
        )
        assert first.global_position == expected_start[instrument]
        assert first.stream_version == expected_start[instrument]


def test_provider_history_accepts_maximum_stage6_child_bound() -> None:
    artifact = _build_stage6_artifact(day_count=1)
    request_references = tuple(
        IbkrHistoricalChildReference(
            path=f"requests/{index:05d}.json",
            contract=REQUEST_RESULT_CONTRACT,
            semantic_sha256=f"{index:064x}",
            bytes_sha256=f"{index + 20_000:064x}",
        )
        for index in range(20_000)
    )
    aggregate_identity = artifact.aggregate.identity_payload()
    aggregate_identity["request_results"] = [
        reference.as_json_value() for reference in request_references
    ]
    aggregate = replace(
        artifact.aggregate,
        request_results=request_references,
        aggregate_sha256=sha256_json(aggregate_identity),
    )
    source = replace(artifact, aggregate=aggregate)

    digests = provider_history_runtime._source_file_digests(source, b"manifest")

    assert len(digests) == 20_002


def test_provider_history_copies_request_child_at_request_result_bound(tmp_path: Path) -> None:
    payload = b"x" * (MAX_IBKR_RESULT_BYTES + 1)
    assert len(payload) < MAX_IBKR_RESULT_REQUEST_BYTES
    source_root = tmp_path / "source"
    destination_root = tmp_path / "destination"
    child_path = source_root / "requests" / "child.json"
    child_path.parent.mkdir(parents=True)
    child_path.write_bytes(payload)

    provider_history_runtime._copy_source_file(
        source_root,
        destination_root,
        "requests/child.json",
        expected_digest=sha256_bytes(payload),
    )

    assert (destination_root / "requests" / "child.json").read_bytes() == payload
