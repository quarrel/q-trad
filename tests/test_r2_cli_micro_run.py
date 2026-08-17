"""Exact production-CLI micro-run gate for confirmatory Phases F--I."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest

from qtrad import __main__ as cli
from qtrad.application.ibkr_results import build_ibkr_historical_result_artifact
from qtrad.domain.events import JsonValue
from qtrad.domain.foundation import FoundationConfig
from qtrad.domain.ibkr_execution import (
    IbkrAttemptStatus,
    IbkrHistoricalCallbackKind,
    IbkrPublicationStatus,
    IbkrRequestStatus,
    IbkrTerminalDisposition,
)
from qtrad.domain.ibkr_foundation import IBKR_CONFIRMATORY_INSTRUMENTS
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
)
from qtrad.domain.identifiers import InstrumentId
from qtrad.domain.provider_history import (
    PROVIDER_HISTORY_DECLARED_DELAY,
    PROVIDER_HISTORY_POLICY,
    ProviderHistoricalAvailabilityPolicy,
)
from qtrad.domain.r2_ibkr_historical import IBKR_HISTORICAL_PROFILE_ARGUMENT
from qtrad.domain.research import ObservationDataset
from qtrad.runtime.ibkr_foundation import (
    verify_ibkr_foundation,
    write_ibkr_foundation,
)
from qtrad.runtime.ibkr_foundation_promotion import (
    create_ibkr_foundation_confirmatory_promotion,
)
from qtrad.runtime.ibkr_results import (
    verify_ibkr_historical_result,
    write_ibkr_historical_result,
)
from qtrad.runtime.provider_history_v3 import build_provider_history, verify_provider_history
from tests.test_r1_foundation import _config

_BASE = datetime(2026, 1, 1, tzinfo=UTC)
_GRID = timedelta(minutes=1)
_HORIZON = timedelta(minutes=15)
_FIXTURE_WEEKS = 20
_DECISIONS_PER_WEEK = 60
_FEATURE_BUFFER = timedelta(minutes=5)
_SOURCE_END = _BASE + timedelta(weeks=_FIXTURE_WEEKS, minutes=30)
_CANDIDATES = tuple(str(item) for item in IBKR_CONFIRMATORY_INSTRUMENTS)


def _fixture_decisions() -> tuple[datetime, ...]:
    return tuple(
        _BASE + timedelta(weeks=week, days=1, minutes=minute)
        for week in range(_FIXTURE_WEEKS)
        for minute in range(_DECISIONS_PER_WEEK)
    )


def _fixture_intervals() -> tuple[tuple[datetime, datetime], ...]:
    return tuple(
        (
            decision_start - _FEATURE_BUFFER,
            decision_start + _DECISIONS_PER_WEEK * _GRID + _HORIZON,
        )
        for week in range(_FIXTURE_WEEKS)
        for decision_start in (_BASE + timedelta(weeks=week, days=1),)
    )


def _fixture_request_intervals() -> tuple[tuple[datetime, datetime], ...]:
    intervals: list[tuple[datetime, datetime]] = []
    cursor = _BASE - timedelta(minutes=1)
    for _ in range(_FIXTURE_WEEKS - 1):
        end = cursor + timedelta(days=7)
        intervals.append((cursor, end))
        cursor = end
    intervals.append((cursor, _SOURCE_END))
    return tuple(intervals)


def _fixture_bar_times() -> tuple[datetime, ...]:
    return tuple(
        interval_start + offset * _GRID
        for interval_start, _interval_end in _fixture_intervals()
        for offset in range(
            int(_FEATURE_BUFFER / _GRID) + _DECISIONS_PER_WEEK + int(_HORIZON / _GRID)
        )
    )


_FIXTURE_POLICY = ProviderHistoricalAvailabilityPolicy(
    selector=PROVIDER_HISTORY_DECLARED_DELAY,
    policy=PROVIDER_HISTORY_POLICY,
    delay=timedelta(0),
)
_FIXTURE_IDENTITIES = {
    "application_identity": "qtrad-fixture+git:" + "0" * 40 + "+image:sha256:" + "1" * 64,
    "image_identity": "sha256:" + "1" * 64,
    "python_identity": "fixture-python",
    "numpy_identity": "fixture-numpy",
    "sklearn_identity": "fixture-sklearn",
}


def _fingerprint(instrument: str, index: int) -> IbkrContractFingerprint:
    symbol = instrument.split(":", 1)[1].replace("-", "").upper()
    return IbkrContractFingerprint(
        con_id=1000 + index,
        symbol=symbol,
        security_type="CASH" if instrument.startswith("fx:") else "CFD",
        currency="USD",
        exchange="SMART",
        primary_exchange=None,
        local_symbol=symbol,
        trading_class=symbol,
        multiplier=None,
        underlying_con_id=None,
        contract_month=None,
    )


def _request(
    instrument: str,
    fingerprint: IbkrContractFingerprint,
    kind: IbkrHistoricalRequestKind,
    start: datetime,
    end: datetime,
) -> IbkrHistoricalRequest:
    values: dict[str, JsonValue] = {
        "instrument_id": instrument,
        "fingerprint": fingerprint.as_json_value(),
        "kind": kind.value,
        "interval_start": utc_text(start),
        "interval_end": utc_text(end),
        "end_date_time": ibkr_end_date_time(end),
        "duration": "28 D",
        "bar_size": ("1 min" if kind is IbkrHistoricalRequestKind.MIDPOINT_BARS else "1 day"),
        "what_to_show": (
            "MIDPOINT" if kind is IbkrHistoricalRequestKind.MIDPOINT_BARS else "SCHEDULE"
        ),
        "use_rth": False,
        "format_date": 2,
        "keep_up_to_date": False,
    }
    return IbkrHistoricalRequest(
        instrument_id=InstrumentId(instrument),
        fingerprint=fingerprint,
        kind=kind,
        interval_start=start,
        interval_end=end,
        end_date_time=ibkr_end_date_time(end),
        duration="28 D",
        bar_size=cast(str, values["bar_size"]),
        what_to_show=cast(str, values["what_to_show"]),
        use_rth=False,
        format_date=2,
        keep_up_to_date=False,
        request_sha256=sha256_json(values),
    )


def _plan(requests: tuple[IbkrHistoricalRequest, ...]) -> IbkrHistoricalPlan:
    eligible = tuple(
        IbkrPlannedContract(instrument_id=InstrumentId(item), fingerprint=_fingerprint(item, index))
        for index, item in enumerate(_CANDIDATES, start=1)
    )
    identity: dict[str, JsonValue] = {
        "contract": HISTORICAL_PLAN_CONTRACT,
        "schema_version": 1,
        "contract_selection_sha256": "1" * 64,
        "runtime_sha256": "2" * 64,
        "request_profile_sha256": "3" * 64,
        "provider": "ibkr",
        "environment": "paper",
        "planner_qtrad_commit": "4" * 40,
        "planner_qtrad_image_digest": "sha256:" + "5" * 64,
        "start": utc_text(_BASE - timedelta(minutes=1)),
        "end": utc_text(_SOURCE_END),
        "eligible_contracts": [
            item.as_json_value()
            for item in sorted(eligible, key=lambda value: str(value.instrument_id))
        ],
        "requests": [
            item.as_json_value()
            for item in sorted(
                requests,
                key=lambda value: (
                    str(value.instrument_id),
                    value.kind.value,
                    value.interval_start,
                    value.request_sha256,
                ),
            )
        ],
    }
    return IbkrHistoricalPlan(
        contract_selection_sha256="1" * 64,
        runtime_sha256="2" * 64,
        request_profile_sha256="3" * 64,
        provider="ibkr",
        environment="paper",
        planner_qtrad_commit="4" * 40,
        planner_qtrad_image_digest="sha256:" + "5" * 64,
        start=_BASE - timedelta(minutes=1),
        end=_SOURCE_END,
        eligible_contracts=eligible,
        requests=requests,
        plan_sha256=sha256_json(identity),
    )


def _snapshot(plan: IbkrHistoricalPlan) -> IbkrHistoricalExecutionSnapshot:
    plan_bytes = (json.dumps(plan.as_json_value(), sort_keys=True, indent=2) + "\n").encode()
    attempts: list[IbkrHistoricalAttemptEvidence] = []
    callbacks: list[IbkrHistoricalCallbackEvidence] = []
    markers: list[IbkrHistoricalCompletionEvidence] = []
    requests: list[IbkrHistoricalRequestSnapshot] = []
    callback_id = 1
    marker_id = 1
    attempt_id = 1
    request_id = 1
    for request in sorted(plan.requests, key=lambda value: value.request_sha256):
        attempt_uuid = UUID(int=attempt_id)
        session_uuid = UUID(int=10_000 + attempt_id)
        started = _BASE + timedelta(seconds=attempt_id)
        terminal = started + timedelta(seconds=100_000)
        attempts.append(
            IbkrHistoricalAttemptEvidence(
                attempt_id=attempt_uuid,
                plan_sha256=plan.plan_sha256,
                request_sha256=request.request_sha256,
                attempt_ordinal=1,
                connection_session_id=session_uuid,
                provider_request_id=10_000 + request_id,
                connection_generation=1,
                started_at=started,
                status=IbkrAttemptStatus.SUCCEEDED,
                terminal_at=terminal,
                terminal_disposition=IbkrTerminalDisposition.SUCCEEDED,
                detail=None,
            )
        )
        if request.kind is IbkrHistoricalRequestKind.MIDPOINT_BARS:
            unique_bars = tuple(
                bar_time
                for bar_time in _fixture_bar_times()
                if request.interval_start <= bar_time < request.interval_end
            )
            sequence = 1
            instrument_index = _CANDIDATES.index(str(request.instrument_id))
            for bar_time in unique_bars:
                minute_index = int((bar_time - _BASE) // _GRID)
                price_cents = 10_000 + instrument_index * 37 + (minute_index * 7 % 1_000)
                payload: dict[str, JsonValue] = {
                    "date": int(bar_time.timestamp()),
                    "open": f"{price_cents / 100:.2f}",
                    "high": f"{(price_cents + 1) / 100:.2f}",
                    "low": f"{(price_cents - 1) / 100:.2f}",
                    "close": f"{price_cents / 100:.2f}",
                    "volume": 1,
                    "wap": f"{price_cents / 100:.2f}",
                    "count": 1,
                }
                callbacks.append(
                    IbkrHistoricalCallbackEvidence(
                        callback_id=callback_id,
                        attempt_id=attempt_uuid,
                        connection_session_id=session_uuid,
                        provider_request_id=10_000 + request_id,
                        connection_generation=1,
                        sequence=sequence,
                        kind=IbkrHistoricalCallbackKind.MIDPOINT_BAR,
                        received_at=started + timedelta(seconds=sequence),
                        payload=payload,
                        closure_eligible=True,
                    )
                )
                callback_id += 1
                sequence += 1
            bar_count = len(unique_bars)
            completion_time = started + timedelta(seconds=sequence)
            callbacks.append(
                IbkrHistoricalCallbackEvidence(
                    callback_id=callback_id,
                    attempt_id=attempt_uuid,
                    connection_session_id=session_uuid,
                    provider_request_id=10_000 + request_id,
                    connection_generation=1,
                    sequence=sequence,
                    kind=IbkrHistoricalCallbackKind.COMPLETION,
                    received_at=completion_time,
                    payload={},
                    closure_eligible=True,
                )
            )
            callback_id += 1
            markers.append(
                IbkrHistoricalCompletionEvidence(
                    marker_id=marker_id,
                    attempt_id=attempt_uuid,
                    connection_session_id=session_uuid,
                    provider_request_id=10_000 + request_id,
                    connection_generation=1,
                    sequence=sequence,
                    completed_at=completion_time,
                    raw_midpoint_bar_callback_count=bar_count,
                    raw_schedule_callback_count=0,
                    closure_eligible=True,
                    payload={},
                )
            )

        else:
            sessions = [
                {
                    "start": utc_text(interval_start),
                    "end": utc_text(interval_end),
                    "active": True,
                }
                for interval_start, interval_end in _fixture_intervals()
                if request.interval_start <= interval_start and interval_end <= request.interval_end
            ]
            if not sessions:
                raise AssertionError("fixture schedule request has no active session")
            callbacks.extend(
                (
                    IbkrHistoricalCallbackEvidence(
                        callback_id=callback_id,
                        attempt_id=attempt_uuid,
                        connection_session_id=session_uuid,
                        provider_request_id=10_000 + request_id,
                        connection_generation=1,
                        sequence=1,
                        kind=IbkrHistoricalCallbackKind.SCHEDULE,
                        received_at=started + timedelta(seconds=1),
                        payload={"sessions": cast(JsonValue, sessions)},
                        closure_eligible=True,
                    ),
                    IbkrHistoricalCallbackEvidence(
                        callback_id=callback_id + 1,
                        attempt_id=attempt_uuid,
                        connection_session_id=session_uuid,
                        provider_request_id=10_000 + request_id,
                        connection_generation=1,
                        sequence=2,
                        kind=IbkrHistoricalCallbackKind.COMPLETION,
                        received_at=started + timedelta(seconds=2),
                        payload={},
                        closure_eligible=True,
                    ),
                )
            )
            callback_id += 2
            markers.append(
                IbkrHistoricalCompletionEvidence(
                    marker_id=marker_id,
                    attempt_id=attempt_uuid,
                    connection_session_id=session_uuid,
                    provider_request_id=10_000 + request_id,
                    connection_generation=1,
                    sequence=2,
                    completed_at=started + timedelta(seconds=2),
                    raw_midpoint_bar_callback_count=0,
                    raw_schedule_callback_count=1,
                    closure_eligible=True,
                    payload={},
                )
            )
        requests.append(
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
                selected_attempt_id=attempt_uuid,
                publication_status=IbkrPublicationStatus.PENDING,
                result_sha256=None,
                published_at=None,
            )
        )
        attempt_id += 1
        request_id += 1
        marker_id += 1
    return IbkrHistoricalExecutionSnapshot(
        plan=IbkrHistoricalPlanSnapshot(
            plan_sha256=plan.plan_sha256,
            plan_bytes=plan_bytes,
            plan_bytes_sha256=__import__("hashlib").sha256(plan_bytes).hexdigest(),
            plan_payload=plan.as_json_value(),
            registered_at=_BASE,
            publication_status=IbkrPublicationStatus.PENDING,
        ),
        requests=tuple(requests),
        attempts=tuple(attempts),
        callbacks=tuple(callbacks),
        completion_markers=tuple(markers),
    )


def _foundation_config() -> FoundationConfig:
    empty = cast(ObservationDataset, SimpleNamespace(dataset_id="0" * 64))
    config = _config(empty, start=_BASE, end=_SOURCE_END)
    return replace(
        config,
        grid_resolution=_GRID,
        holdout_range=(_BASE + (_SOURCE_END - _BASE) * 0.8, _SOURCE_END),
        minimum_training_duration=timedelta(weeks=10),
        minimum_validation_duration=timedelta(weeks=2),
        selected_feature_lag=timedelta(minutes=1),
    )


def _stage8_fixture(root: Path) -> tuple[Path, Path, Path, Path, Path]:
    intervals = _fixture_intervals()
    assert len(intervals) == _FIXTURE_WEEKS
    assert all(start < end for start, end in intervals)
    assert all(left_end < right_start for (_, left_end), (right_start, _) in pairwise(intervals))
    active_minutes = sum(int((end - start) / _GRID) for start, end in intervals)
    expected_active_minutes = _FIXTURE_WEEKS * int(
        (_FEATURE_BUFFER + _DECISIONS_PER_WEEK * _GRID + _HORIZON) / _GRID
    )
    assert active_minutes == expected_active_minutes
    request_intervals = _fixture_request_intervals()
    assert request_intervals[0][0] == _BASE - timedelta(minutes=1)
    assert request_intervals[-1][1] == _SOURCE_END
    assert all(
        left_end == right_start for (_, left_end), (right_start, _) in pairwise(request_intervals)
    )
    assert all(end - start <= timedelta(days=28) for start, end in request_intervals)
    requests: list[IbkrHistoricalRequest] = []
    for index, instrument in enumerate(_CANDIDATES, start=1):
        fingerprint = _fingerprint(instrument, index)
        for kind in (IbkrHistoricalRequestKind.MIDPOINT_BARS, IbkrHistoricalRequestKind.SCHEDULE):
            for interval_start, interval_end in _fixture_request_intervals():
                requests.append(
                    _request(instrument, fingerprint, kind, interval_start, interval_end)
                )
    plan = _plan(tuple(requests))
    artifact = build_ibkr_historical_result_artifact(plan, _snapshot(plan))
    stage6 = root / "stage6"
    stage6_manifest = write_ibkr_historical_result(stage6, artifact)
    stage6_receipt = root / "stage6-receipt.json"
    verify_ibkr_historical_result(stage6_manifest, receipt_output=stage6_receipt)
    stage7 = root / "stage7"
    stage7_manifest = build_provider_history(
        stage6_manifest,
        stage6_receipt=stage6_receipt,
        output=stage7,
        availability_policy=_FIXTURE_POLICY,
    )
    stage7_receipt = root / "stage7-receipt.json"
    verify_provider_history(
        stage7_manifest,
        stage6_manifest=stage6_manifest,
        stage6_receipt=stage6_receipt,
        receipt_output=stage7_receipt,
        availability_policy=_FIXTURE_POLICY,
    )
    foundation = root / "foundation.json"
    config = _foundation_config()
    write_ibkr_foundation(
        foundation,
        stage7_manifest=stage7_manifest,
        stage7_receipt=stage7_receipt,
        configuration=config,
        workers=1,
    )
    foundation_receipt = root / "foundation-receipt.json"
    verify_ibkr_foundation(
        foundation,
        stage7_manifest=stage7_manifest,
        stage7_receipt=stage7_receipt,
        receipt_output=foundation_receipt,
        workers=1,
    )
    promotion = root / "foundation-promotion.json"
    import qtrad.runtime.ibkr_foundation_promotion as promotion_runtime
    import qtrad.runtime.r2_verification as verification

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(promotion_runtime, "_require_detached_source", lambda: None)
        patch.setattr(verification, "runtime_identities", lambda: _FIXTURE_IDENTITIES)
        create_ibkr_foundation_confirmatory_promotion(
            foundation,
            receipt=foundation_receipt,
            output=promotion,
            authorized_by="micro-run",
            authorized_at=datetime(2026, 8, 16, tzinfo=UTC),
            authorization_reference="micro-run-fixture",
        )
    return foundation, foundation_receipt, promotion, stage7_manifest, stage7_receipt


def test_stage8_micro_fixture_builds_and_qualifies(tmp_path: Path) -> None:
    foundation, receipt, promotion, _stage7, _stage7_receipt = _stage8_fixture(tmp_path)
    document = json.loads(foundation.read_bytes())
    assert document["contract"] == "qtrad-ibkr-historical-foundation-v2"
    assert document["source_class"] == "IBKR_HISTORICAL_RESEARCH"
    readiness = document["payload"]["readiness"]
    assert readiness["state"] == "QUALIFYING_HISTORY_READY"
    assert readiness["causes"] == []
    assert all(
        1_000 <= value <= _DECISIONS_PER_WEEK * _FIXTURE_WEEKS * 17
        for value in readiness["rows_by_candidate"].values()
    )
    configuration = document["payload"]["configuration"]
    active_intervals = document["payload"]["active_intervals"]
    assert all(len(active_intervals[instrument]) == _FIXTURE_WEEKS for instrument in _CANDIDATES)
    assert all(
        sum(
            int((datetime.fromisoformat(interval[1]) - datetime.fromisoformat(interval[0])) / _GRID)
            for interval in active_intervals[instrument]
        )
        == _FIXTURE_WEEKS * int((_FEATURE_BUFFER + _DECISIONS_PER_WEEK * _GRID + _HORIZON) / _GRID)
        for instrument in _CANDIDATES
    )
    child_references = document["payload"]["children"]
    target_rows = sum(int(part["row_count"]) for part in child_references["targets"])
    panel_rows = sum(int(part["row_count"]) for part in child_references["panel"])
    active_minutes_upper_bound = _FIXTURE_WEEKS * int(
        (_FEATURE_BUFFER + _DECISIONS_PER_WEEK * _GRID + _HORIZON) / _GRID
    )
    assert target_rows <= active_minutes_upper_bound * len(_CANDIDATES)
    assert panel_rows <= active_minutes_upper_bound * len(configuration["ordered_instruments"])
    assert document["payload"]["target_opportunity_policy_id"]
    assert configuration["minimum_training_duration_seconds"] == timedelta(weeks=10).total_seconds()
    assert (
        configuration["minimum_validation_duration_seconds"] == timedelta(weeks=2).total_seconds()
    )
    holdout_start, holdout_end = (
        datetime.fromisoformat(value) for value in configuration["holdout_range"]
    )
    assert holdout_end - holdout_start >= timedelta(weeks=4)
    coverage_cells = readiness["evidence"]["coverage_cells"]
    assert len(coverage_cells) == len(_CANDIDATES) * 5
    assert all(cell["passed"] for cell in coverage_cells)
    assert all(cell["opportunity_counts"]["ELIGIBLE"] >= 20 for cell in coverage_cells)
    assert foundation.stat().st_size < 4 * 1024 * 1024
    assert (
        sum(path.stat().st_size for path in tmp_path.rglob("*") if path.is_file())
        < 256 * 1024 * 1024
    )
    assert receipt.is_file() and promotion.is_file()


def test_confirmatory_exact_cli_micro_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the production parser/main branches with a bounded fixture only."""
    research_root = tmp_path / "research"
    monkeypatch.setenv("QTRAD_RESEARCH_ROOT", str(research_root))
    monkeypatch.setenv("QTRAD_IMAGE", _FIXTURE_IDENTITIES["image_identity"])

    import qtrad.runtime.r2_verification as verification

    monkeypatch.setattr(verification, "runtime_identities", lambda: _FIXTURE_IDENTITIES)
    monkeypatch.setattr(cli, "runtime_identities", lambda: _FIXTURE_IDENTITIES)

    foundation, foundation_receipt, foundation_promotion, _stage7, _stage7_receipt = (
        _stage8_fixture(tmp_path)
    )
    experiment = tmp_path / "experiment.json"
    cli.main(
        [
            "research",
            "baselines",
            "experiment-build",
            "--foundation",
            str(foundation),
            "--foundation-receipt",
            str(foundation_receipt),
            "--foundation-promotion",
            str(foundation_promotion),
            "--profile",
            IBKR_HISTORICAL_PROFILE_ARGUMENT,
            "--output",
            str(experiment),
        ]
    )
    holdout_source = tmp_path / "holdout-source.json"
    cli.main(
        [
            "research",
            "baselines",
            "holdout-target-source",
            "--foundation-bundle",
            str(foundation),
            "--foundation-receipt",
            str(foundation_receipt),
            "--foundation-promotion",
            str(foundation_promotion),
            "--experiment",
            str(experiment),
            "--output",
            str(holdout_source),
        ]
    )

    feature_dir = research_root / "features"
    feature_dir.mkdir(parents=True)
    feature_paths: dict[str, Path] = {}
    feature_receipt_paths: dict[str, Path] = {}
    for feature_set in ("L0", "L1", "P0", "P1"):
        manifest = feature_dir / f"{feature_set}.json"
        receipt = feature_dir / f"{feature_set}-receipt.json"
        cli.main(
            [
                "research",
                "baselines",
                "features",
                "--foundation-bundle",
                str(foundation),
                "--foundation-receipt",
                str(foundation_receipt),
                "--foundation-promotion",
                str(foundation_promotion),
                "--holdout-target-source",
                str(holdout_source),
                "--experiment",
                str(experiment),
                "--feature-set",
                feature_set,
                "--output",
                str(manifest),
            ]
        )
        cli.main(
            [
                "research",
                "baselines",
                "features-verify",
                "--foundation-bundle",
                str(foundation),
                "--foundation-receipt",
                str(foundation_receipt),
                "--foundation-promotion",
                str(foundation_promotion),
                "--holdout-target-source",
                str(holdout_source),
                "--experiment",
                str(experiment),
                "--feature-set",
                feature_set,
                "--manifest",
                str(manifest),
                "--receipt-output",
                str(receipt),
            ]
        )
        feature_paths[feature_set] = manifest
        feature_receipt_paths[feature_set] = receipt

    oof_root = research_root / "oof"
    feature_arguments = [
        value
        for name, path in feature_paths.items()
        for value in ("--feature-manifest", f"{name}={path}")
    ]
    feature_receipt_arguments = [
        value
        for name, path in feature_receipt_paths.items()
        for value in ("--feature-receipt", f"{name}={path}")
    ]
    cli.main(
        [
            "research",
            "baselines",
            "oof-build",
            "--foundation-bundle",
            str(foundation),
            "--foundation-receipt",
            str(foundation_receipt),
            "--foundation-promotion",
            str(foundation_promotion),
            "--experiment",
            str(experiment),
            *feature_arguments,
            *feature_receipt_arguments,
            "--holdout-target-source",
            str(holdout_source),
            "--output",
            str(oof_root),
        ]
    )
    oof_manifest = oof_root / "manifest.json"
    oof_receipt = tmp_path / "oof-receipt.json"
    cli.main(
        [
            "research",
            "baselines",
            "oof-verify",
            "--bundle",
            str(oof_manifest),
            "--receipt-output",
            str(oof_receipt),
        ]
    )

    promotion_root = tmp_path.parent / f"{tmp_path.name}-confirmatory"
    promotion_root.mkdir()
    f2_promotion = promotion_root / "f2-promotion.json"
    cli.main(
        [
            "research",
            "baselines",
            "confirmatory-f2-promote",
            "--oof-bundle",
            str(oof_manifest),
            "--oof-receipt",
            str(oof_receipt),
            "--authorized-by",
            "micro-run",
            "--authorized-at",
            "2026-08-16T00:00:00+00:00",
            "--output",
            str(f2_promotion),
        ]
    )
    selection = tmp_path / "selection.json"
    cli.main(
        [
            "research",
            "baselines",
            "confirmatory-selection-freeze",
            "--f2-promotion",
            str(f2_promotion),
            "--frozen-by",
            "micro-run",
            "--output",
            str(selection),
        ]
    )
    cli.main(
        [
            "research",
            "baselines",
            "confirmatory-g1-verify",
            "--f2-promotion",
            str(f2_promotion),
            "--selection",
            str(selection),
        ]
    )
    preparation_root = tmp_path / "preparation"
    cli.main(
        [
            "research",
            "baselines",
            "confirmatory-g2-prepare",
            "--f2-promotion",
            str(f2_promotion),
            "--selection",
            str(selection),
            "--prepared-by",
            "micro-run",
            "--output",
            str(preparation_root),
        ]
    )
    cli.main(
        [
            "research",
            "baselines",
            "confirmatory-g2-preparation-verify",
            "--f2-promotion",
            str(f2_promotion),
            "--selection",
            str(selection),
            "--preparation",
            str(preparation_root),
        ]
    )

    foundation_document = json.loads(foundation.read_bytes())
    assert foundation.stat().st_size < 4 * 1024 * 1024
    assert foundation_document["payload"]["provider_gaps"] == []
    assert foundation_document["payload"]["readiness"]["causes"] == []
    assert oof_manifest.stat().st_size < 4 * 1024 * 1024
    assert oof_receipt.is_file()
    oof_document = json.loads(oof_manifest.read_bytes())
    assert "rows" not in oof_document
    descriptor_document = json.loads((oof_root / "evaluation" / "run-descriptor.json").read_bytes())
    assert descriptor_document["feature_sets"] == ["L0", "L1", "P0", "P1"]
    assert all(
        path.stat().st_size < 4 * 1024 * 1024 for path in oof_root.rglob("*") if path.is_file()
    )

    preparation_document = json.loads((preparation_root / "manifest.json").read_bytes())

    def contains_unopened(value: object) -> bool:
        if isinstance(value, dict):
            return value.get("holdout_outcomes_accessed") is False or any(
                contains_unopened(item) for item in value.values()
            )
        if isinstance(value, list):
            return any(contains_unopened(item) for item in value)
        return False

    assert contains_unopened(preparation_document)
    forbidden = {
        "opened.json",
        "consumed.json",
        "outcome-evidence.json",
        "outcome-target.json",
        "evaluation.json",
    }
    assert not any(path.name in forbidden for path in preparation_root.rglob("*"))
    assert not any(path.name in {"OPENED", "CONSUMED"} for path in preparation_root.rglob("*"))
