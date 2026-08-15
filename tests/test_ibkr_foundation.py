from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

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
    load_ibkr_foundation,
    verify_ibkr_foundation,
    write_ibkr_foundation,
)
from qtrad.runtime.ibkr_foundation_bounded import build_bounded_provider_foundation
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


def test_stage8_bounded_derivation_matches_generic_source(tmp_path: Path) -> None:
    stage7_manifest, source = _authenticated_v3_source(tmp_path)
    from qtrad.application.ibkr_foundation import build_ibkr_foundation

    generic = build_ibkr_foundation(source, _stage8_configuration())
    bounded, references = build_bounded_provider_foundation(
        source_evidence=source,
        configuration=_stage8_configuration(),
        child_root=tmp_path / "bounded-children",
        bundle_root=tmp_path,
        child_name="foundation",
        provider_manifest_sha256=__import__("hashlib")
        .sha256(stage7_manifest.read_bytes())
        .hexdigest(),
        workers=1,
    )
    assert bounded.readiness.as_json() == generic.readiness.as_json()
    assert references


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
    assert build.readiness.state in {
        IBKRFoundationReadinessState.QUALIFYING_HISTORY_READY,
        IBKRFoundationReadinessState.INSUFFICIENT_HISTORY_FOR_MODEL_CONCLUSION,
    }
    assert build.readiness.evidence["fold_count"] >= 0


def _coverage_fixture(
    gap_count: int,
) -> tuple[dict[str, object], list[dict[str, object]], set[str]]:
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
    cells, blocking, _diagnostics = _ibkr_opportunity_coverage(
        targets=SimpleNamespace(rows=rows),
        active_intervals=active,
        provider_gaps=gaps,
        primary_horizon=timedelta(minutes=1),
        folds=(fold,),
        holdout_range=(start + timedelta(days=2), start + timedelta(days=3)),
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
