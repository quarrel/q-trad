from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

import qtrad.runtime.ibkr_foundation as foundation_runtime
from qtrad import __main__ as cli
from qtrad.application.ibkr_foundation import _ibkr_opportunity_coverage
from qtrad.domain.folds import Fold, membership_hash
from qtrad.domain.foundation import ReturnDisposition
from qtrad.domain.ibkr_foundation import (
    IBKR_CONFIRMATORY_CANDIDATES,
    IBKR_CONFIRMATORY_GROUPS,
    IBKR_CONFIRMATORY_INSTRUMENTS,
    IBKRFoundationReadinessState,
)
from qtrad.runtime.ibkr_foundation import (
    authenticate_ibkr_foundation,
    build_ibkr_holdout_target_source,
    load_ibkr_foundation,
    verify_ibkr_foundation,
    write_ibkr_foundation,
)
from tests.test_provider_history_v3 import _authenticated_v3_source, _stage8_configuration


def _foundation_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    stage7_manifest, _source = _authenticated_v3_source(tmp_path)
    stage7_receipt = tmp_path / "stage7-receipt.json"
    foundation = tmp_path / "foundation.json"
    write_ibkr_foundation(
        foundation,
        stage7_manifest=stage7_manifest,
        stage7_receipt=stage7_receipt,
        configuration=_stage8_configuration(),
        workers=1,
    )
    return foundation, stage7_manifest, stage7_receipt


def _verified_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    foundation, stage7_manifest, stage7_receipt = _foundation_fixture(tmp_path)
    receipt = tmp_path / "foundation-receipt.json"
    verify_ibkr_foundation(
        foundation,
        stage7_manifest=stage7_manifest,
        stage7_receipt=stage7_receipt,
        receipt_output=receipt,
        workers=1,
    )
    return foundation, stage7_manifest, stage7_receipt, receipt


def test_stage8_declarations_are_fixed_and_model_independent() -> None:
    assert tuple(str(item) for item, _ in IBKR_CONFIRMATORY_CANDIDATES) == (
        "fx:aud-usd",
        "fx:eur-usd",
        "index:australia-200",
        "index:us-500",
        "commodity:spot-gold",
        "commodity:us-crude",
    )
    assert tuple(item for item, _ in IBKR_CONFIRMATORY_CANDIDATES) == IBKR_CONFIRMATORY_INSTRUMENTS
    assert IBKR_CONFIRMATORY_GROUPS == ("FX", "indices", "commodities")


def test_stage8_writer_preserves_racing_output_on_child_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage7_manifest, _source = _authenticated_v3_source(tmp_path)
    stage7_receipt = tmp_path / "stage7-receipt.json"
    output = tmp_path / "racing-foundation.json"

    def fail_after_output_race(*_args: object, **_kwargs: object) -> object:
        output.write_text("competitor", encoding="utf-8")
        raise RuntimeError("simulated output race")

    monkeypatch.setattr(foundation_runtime, "_write_children_v3", fail_after_output_race)
    with pytest.raises(RuntimeError, match="simulated output race"):
        write_ibkr_foundation(
            output,
            stage7_manifest=stage7_manifest,
            stage7_receipt=stage7_receipt,
            configuration=_stage8_configuration(),
            workers=1,
        )
    assert output.read_text(encoding="utf-8") == "competitor"
    assert not (tmp_path / "racing-foundation.json.children").exists()


def test_stage8_build_consumes_stage7_receipt_without_source_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage7_manifest, _source = _authenticated_v3_source(tmp_path)
    stage7_receipt = tmp_path / "stage7-receipt.json"
    import qtrad.runtime.provider_history_v3 as stage7_runtime

    monkeypatch.setattr(
        stage7_runtime,
        "verify_provider_history",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Stage 8 build reopened Stage 7 deep verification")
        ),
    )
    build = write_ibkr_foundation(
        tmp_path / "foundation.json",
        stage7_manifest=stage7_manifest,
        stage7_receipt=stage7_receipt,
        configuration=_stage8_configuration(),
        workers=1,
    )
    assert build.provider_history.dataset_sha256


def test_stage8_verification_receipt_authenticates_without_semantic_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    foundation, stage7_manifest, stage7_receipt, receipt = _verified_fixture(tmp_path)
    import qtrad.runtime.provider_history_v3 as stage7_runtime

    monkeypatch.setattr(
        stage7_runtime,
        "verify_provider_history",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Stage 8 verification reopened Stage 7 deep verification")
        ),
    )
    authenticated = authenticate_ibkr_foundation(foundation, receipt=receipt)
    assert authenticated["foundation_id"]
    assert load_ibkr_foundation(foundation, receipt=receipt).provider_history.dataset_sha256
    assert stage7_manifest.is_file() and stage7_receipt.is_file()


def test_stage8_receipt_and_closure_mutation_is_rejected(tmp_path: Path) -> None:
    foundation, _stage7_manifest, _stage7_receipt, receipt = _verified_fixture(tmp_path)
    original = json.loads(receipt.read_bytes())
    original["foundation_id"] = "0" * 64
    receipt.write_text(json.dumps(original) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="receipt"):
        authenticate_ibkr_foundation(foundation, receipt=receipt)


def test_stage8_outcome_blind_loader_does_not_decode_full_children(tmp_path: Path) -> None:
    foundation, _stage7_manifest, _stage7_receipt, receipt = _verified_fixture(tmp_path)
    loaded = load_ibkr_foundation(foundation, receipt=receipt)
    assert loaded.observations.rows
    assert loaded.targets.rows


def test_stage8_child_manifest_mutation_is_rejected(tmp_path: Path) -> None:
    foundation, stage7_manifest, stage7_receipt, receipt = _verified_fixture(tmp_path)
    document = json.loads(foundation.read_bytes())
    child = next(iter(document["payload"]["children"]["observations"]))
    child_manifest = foundation.parent / child["manifest_path"]
    child_document = json.loads(child_manifest.read_bytes())
    child_document["row_count"] += 1
    child_manifest.write_text(json.dumps(child_document) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        verify_ibkr_foundation(
            foundation,
            stage7_manifest=stage7_manifest,
            stage7_receipt=stage7_receipt,
            receipt_output=tmp_path / "second-receipt.json",
            workers=1,
        )
    assert receipt.is_file()


def test_stage8_cli_requires_current_stage7_parent() -> None:
    parsed = cli.build_parser().parse_args(
        [
            "research",
            "foundation",
            "verify",
            "--bundle",
            "foundation.json",
            "--stage7-manifest",
            "stage7/manifest.json",
            "--stage7-receipt",
            "stage7-receipt.json",
            "--receipt-output",
            "foundation-receipt.json",
        ]
    )
    assert parsed.stage7_manifest == Path("stage7/manifest.json")
    generic = cli.build_parser().parse_args(
        [
            "research",
            "foundation",
            "verify",
            "--bundle",
            "foundation.json",
            "--receipt-output",
            "foundation-receipt.json",
        ]
    )
    assert generic.stage7_manifest is None
    assert generic.stage7_receipt is None


def test_stage8_readiness_without_valid_folds_is_insufficient(tmp_path: Path) -> None:
    foundation, _stage7_manifest, _stage7_receipt, receipt = _verified_fixture(tmp_path)
    build = load_ibkr_foundation(foundation, receipt=receipt)
    assert (
        build.readiness.state
        is IBKRFoundationReadinessState.INSUFFICIENT_HISTORY_FOR_MODEL_CONCLUSION
    )
    assert tuple(cause.value for cause in build.readiness.causes) == (
        "SESSION_EVIDENCE_UNAVAILABLE",
        "INSUFFICIENT_COMMON_SUPPORT",
        "INSUFFICIENT_BLOCK_COVERAGE",
        "INSUFFICIENT_DURATION",
        "INSUFFICIENT_ROWS",
        "MISSING_CONFIRMATORY_TARGET",
    )
    evidence = cast(dict[str, Any], build.readiness.evidence)
    assert evidence["provider_gap_count"] == 0
    assert evidence["total_provider_gap_count"] == 0
    assert evidence["raw_provider_gaps"] == []
    assert evidence["request_evidence"]["fx:aud-usd"] == {
        "planned": True,
        "bar_dispositions": ["SUCCEEDED"],
        "schedule_dispositions": ["SUCCEEDED"],
        "eligible": True,
        "contract_ids": [42],
        "bar_row_count": 1,
        "schedule_session_count": 0,
    }
    assert evidence["source_coverage_summary"]["provider_session_count"] == 0
    assert evidence["source_coverage_summary"]["successful_request_count"] == 2
    assert evidence["coverage_threshold"] == "9/10"
    assert evidence["fold_count"] == 0
    assert evidence["coverage_diagnostics"] == {
        "authoritative": False,
        "blocking_cell_count": 6,
        "candidate_count": 6,
        "cell_count": 6,
        "context_gap_count": 0,
        "opportunity_counts": {
            "ELIGIBLE": 0,
            "GAP": 0,
            "INACTIVE": 6,
            "OTHER_INELIGIBLE": 0,
        },
    }
    assert evidence["blocking_coverage_cells"] == [
        f"{instrument}/holdout" for instrument in IBKR_CONFIRMATORY_INSTRUMENTS
    ]
    assert all(
        cell["opportunity_counts"]
        == {
            "ELIGIBLE": 0,
            "GAP": 0,
            "INACTIVE": 1,
            "OTHER_INELIGIBLE": 0,
        }
        and cell["coverage"] is None
        and cell["passed"] is False
        and cell["threshold"] == "9/10"
        for cell in evidence["coverage_cells"]
    )


def _coverage_fixture(
    gap_count: int,
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...], tuple[str, ...]]:
    start = datetime(2026, 2, 1, tzinfo=UTC)
    instrument = str(IBKR_CONFIRMATORY_INSTRUMENTS[0])
    rows = tuple(
        SimpleNamespace(
            instrument_id=instrument,
            decision_time=start + timedelta(minutes=index),
            target_start_time=start + timedelta(minutes=index),
            target_end_time=start + timedelta(minutes=index + 1),
            horizon=timedelta(minutes=1),
            return_disposition=(
                ReturnDisposition.MISSING_START if index < gap_count else ReturnDisposition.VALID
            ),
        )
        for index in range(20)
    )
    gaps: list[dict[str, object]] = [
        {
            "instrument_id": instrument,
            "interval_start": start.isoformat(),
            "interval_end": (start + timedelta(minutes=1)).isoformat(),
            "disposition": "MISSING_BAR",
            "request_sha256": f"{index:064x}",
            "result_sha256": f"{index + 1:064x}",
        }
        for index in range(gap_count)
    ]
    fold = Fold(
        fold_id="fold-1",
        training_start=start,
        training_cutoff=start + timedelta(days=1),
        validation_start=start + timedelta(days=1),
        validation_end=start + timedelta(days=2),
        embargo_end=start + timedelta(days=1),
        training_target_ids=(),
        validation_target_ids=(),
        holdout_excluded=True,
        membership_hash=membership_hash((), ()),
    )
    active = {
        str(item): ((start, start + timedelta(days=30)),) for item in IBKR_CONFIRMATORY_INSTRUMENTS
    }
    cells, blocking, _diagnostics = cast(
        Any,
        _ibkr_opportunity_coverage(
            targets=cast(Any, SimpleNamespace(rows=rows)),
            active_intervals=active,
            provider_gaps=cast(Any, gaps),
            primary_horizon=timedelta(minutes=1),
            folds=(fold,),
            holdout_range=(start + timedelta(days=2), start + timedelta(days=3)),
        ),
    )
    return cells[0], cells, blocking


@pytest.mark.parametrize(("gap_count", "passed"), ((0, True), (20, False)))
def test_stage8_coverage_threshold_is_deterministic(gap_count: int, passed: bool) -> None:
    cell, _cells, _blocking = _coverage_fixture(gap_count)
    assert cell["passed"] is passed


def test_stage8_coverage_deduplicates_and_orders_inputs() -> None:
    cell, cells, blocking = _coverage_fixture(1)
    reversed_cell, reversed_cells, reversed_blocking = _coverage_fixture(1)
    assert cell == reversed_cell
    assert cells == reversed_cells
    assert blocking == reversed_blocking


def test_stage8_outcome_blind_loader_spies_on_unconsumed_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import qtrad.runtime.provider_history_v3 as provider_history_v3_runtime
    from qtrad.application.ibkr_foundation import build_ibkr_foundation
    from qtrad.application.r2_ibkr_historical import (
        _availability_dataset_id,
        ibkr_availability_evidence,
    )
    from qtrad.domain.r2_holdout import R2HoldoutTargetSource
    from qtrad.runtime.ibkr_foundation import load_ibkr_foundation_outcome_blind_with_identity

    stage7_manifest, source = _authenticated_v3_source(tmp_path)
    configuration = _stage8_configuration()
    full_build = build_ibkr_foundation(source, configuration)
    foundation = tmp_path / "foundation.json"
    stage7_receipt = tmp_path / "stage7-receipt.json"
    write_ibkr_foundation(
        foundation,
        stage7_manifest=stage7_manifest,
        stage7_receipt=stage7_receipt,
        configuration=configuration,
        workers=1,
    )
    receipt = tmp_path / "foundation-receipt.json"
    verify_ibkr_foundation(
        foundation,
        stage7_manifest=stage7_manifest,
        stage7_receipt=stage7_receipt,
        receipt_output=receipt,
        workers=1,
    )
    availability = ibkr_availability_evidence(full_build)
    gaps = tuple(
        (
            str(item["instrument_id"]),
            datetime.fromisoformat(str(item["interval_start"])),
            datetime.fromisoformat(str(item["interval_end"])),
        )
        for item in full_build.provider_gaps
    )
    holdout_source = R2HoldoutTargetSource.create_from_target_dataset(
        cast(Any, full_build.targets),
        holdout_range=configuration.holdout_range,
        primary_horizon_seconds=900,
        target_instruments=tuple(str(item) for item in IBKR_CONFIRMATORY_INSTRUMENTS),
        panel=cast(Any, full_build.panel),
        source_active_intervals=full_build.active_intervals,
        data_gaps=gaps,
        availability_evidence_id=_availability_dataset_id(
            cast(Any, full_build.observations).dataset_id, availability
        ),
    )
    original_bounded_bytes = foundation_runtime._bounded_bytes
    parquet_reads: list[Path] = []

    def guarded_bounded_bytes(path: Path, limit: int, field: str) -> bytes:
        if path.suffix == ".parquet":
            parquet_reads.append(path)
        return original_bounded_bytes(path, limit, field)

    monkeypatch.setattr(foundation_runtime, "_bounded_bytes", guarded_bounded_bytes)
    authenticate_ibkr_foundation(foundation, receipt=receipt)
    assert parquet_reads == []

    original_read_child_rows = foundation_runtime._read_child_rows
    child_reads: list[Path] = []

    def guarded_read_child_rows(
        path: Path, *, expected_row_count: int
    ) -> tuple[dict[str, Any], ...]:
        child_reads.append(path)
        assert not {"observations", "panel", "targets"}.intersection(path.parts)
        return original_read_child_rows(path, expected_row_count=expected_row_count)

    def recording_read_child_rows(
        path: Path, *, expected_row_count: int
    ) -> tuple[dict[str, Any], ...]:
        child_reads.append(path)
        return original_read_child_rows(path, expected_row_count=expected_row_count)

    monkeypatch.setattr(foundation_runtime, "_read_child_rows", guarded_read_child_rows)
    monkeypatch.setattr(
        foundation_runtime,
        "build_ibkr_foundation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("outcome-blind loading rebuilt Stage 8")
        ),
    )
    monkeypatch.setattr(
        provider_history_v3_runtime,
        "_read_part",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("outcome-blind loading decoded Stage 7 provider rows")
        ),
    )
    blind_source = build_ibkr_holdout_target_source(
        foundation,
        receipt=receipt,
        target_instruments=tuple(str(item) for item in IBKR_CONFIRMATORY_INSTRUMENTS),
    )
    safe_kinds = {
        "target-index",
        "causal-metadata",
        "blind-observations",
        "blind-panel",
        "pre-holdout-target",
    }
    all_child_kinds = set(foundation_runtime._CHILD_KINDS)

    def assert_parquet_reads(expected: set[str]) -> None:
        read_kinds = {
            kind for path in parquet_reads for kind in all_child_kinds if kind in path.parts
        }
        assert read_kinds == expected
        assert len(parquet_reads) == len(set(parquet_reads))
        assert all(any(kind in path.parts for kind in all_child_kinds) for path in parquet_reads)

    assert_parquet_reads(safe_kinds)
    parquet_reads.clear()
    child_reads.clear()
    assert blind_source == holdout_source
    blind_build, build_id = load_ibkr_foundation_outcome_blind_with_identity(
        foundation,
        receipt=receipt,
        holdout_target_source=blind_source,
    )
    assert_parquet_reads(safe_kinds | {"folds"})
    assert {kind for path in child_reads for kind in all_child_kinds if kind in path.parts} == (
        safe_kinds | {"folds"}
    )
    assert build_id
    assert blind_build.targets.rows == holdout_source.pre_holdout_target_dataset.rows

    monkeypatch.setattr(foundation_runtime, "_read_child_rows", recording_read_child_rows)
    for decode_g2, decode_target, expected_extra in (
        (True, False, {"g2-observations", "g2-panel"}),
        (False, True, {"targets"}),
    ):
        parquet_reads.clear()
        child_reads.clear()
        foundation_runtime._load_ibkr_foundation_outcome_blind(
            foundation,
            receipt=receipt,
            holdout_target_source=blind_source,
            decode_g2=decode_g2,
            decode_target=decode_target,
        )
        expected = safe_kinds | {"folds"} | expected_extra
        assert_parquet_reads(expected)
        assert {
            kind for path in child_reads for kind in all_child_kinds if kind in path.parts
        } == expected

    parquet_reads.clear()
    child_reads.clear()
    load_ibkr_foundation(foundation, receipt=receipt)
    complete_kinds = set(foundation_runtime._BASE_CHILD_KINDS) | {
        "target-index",
        "causal-metadata",
    }
    assert_parquet_reads(complete_kinds)
    assert {
        kind for path in child_reads for kind in all_child_kinds if kind in path.parts
    } == complete_kinds


def test_stage8_readiness_records_zero_fold_insufficiency_and_coverage() -> None:
    cell, cells, blocking = _coverage_fixture(20)
    assert cell["passed"] is False
    assert cell["coverage"] == "0"
    assert cell["opportunity_counts"]["GAP"] >= 1
    assert cells and blocking


def test_stage8_cli_build_and_verify_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configuration = _stage8_configuration()
    stage7_manifest, _source = _authenticated_v3_source(tmp_path)
    stage7_receipt = tmp_path / "stage7-receipt.json"
    foundation = tmp_path / "foundation.json"
    config_path = tmp_path / "configuration.json"
    receipt = tmp_path / "foundation-receipt.json"
    config_path.write_text(
        json.dumps(foundation_runtime.foundation_config_payload(configuration)) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("QTRAD_RESEARCH_ROOT", str(tmp_path))
    cli.main(
        [
            "research",
            "foundation",
            "build",
            "--stage7-manifest",
            str(stage7_manifest),
            "--stage7-receipt",
            str(stage7_receipt),
            "--configuration",
            str(config_path),
            "--output",
            str(foundation),
            "--workers",
            "1",
        ]
    )
    cli.main(
        [
            "research",
            "foundation",
            "verify",
            "--bundle",
            str(foundation),
            "--stage7-manifest",
            str(stage7_manifest),
            "--stage7-receipt",
            str(stage7_receipt),
            "--receipt-output",
            str(receipt),
        ]
    )
    assert json.loads(foundation.read_bytes())["contract"] == "qtrad-ibkr-historical-foundation-v2"
    assert receipt.is_file()
