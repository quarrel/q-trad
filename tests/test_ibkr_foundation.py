from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

import qtrad.runtime.ibkr_foundation as foundation_runtime
import qtrad.runtime.ibkr_foundation_bounded as bounded_foundation_runtime
import qtrad.runtime.provider_history as provider_history_runtime
import qtrad.runtime.r2_verification as r2_verification_runtime
from qtrad import __main__ as cli
from qtrad.__main__ import build_parser
from qtrad.application.ibkr_foundation import (
    IBKRFoundationBuild,
    _ibkr_opportunity_coverage,
    _provider_evidence,
    build_ibkr_foundation,
    evaluate_ibkr_foundation_readiness,
)
from qtrad.application.provider_history import ProviderHistorySourceEvidence
from qtrad.application.r2_ibkr_historical import (
    _availability_dataset_id,
    ibkr_availability_evidence,
)
from qtrad.domain.events import JsonValue
from qtrad.domain.folds import Fold, membership_hash
from qtrad.domain.foundation import (
    InstrumentRole,
    PanelDataset,
    ReturnDisposition,
    TargetDataset,
)
from qtrad.domain.ibkr_foundation import (
    IBKR_CONFIRMATORY_CANDIDATES,
    IBKR_CONFIRMATORY_GROUPS,
    IBKR_CONFIRMATORY_INSTRUMENTS,
    IBKRFoundationReadiness,
    IBKRFoundationReadinessCause,
    IBKRFoundationReadinessState,
)
from qtrad.domain.ibkr_historical import IbkrHistoricalRequestKind
from qtrad.domain.ibkr_results import IbkrHistoricalEvidenceDisposition
from qtrad.domain.identifiers import InstrumentId
from qtrad.domain.market_data import BarProvenance
from qtrad.domain.provider_history import ProviderHistoricalObservation
from qtrad.domain.r2_holdout import R2HoldoutTargetSource, R2OutcomeBlindTargetView
from qtrad.domain.research import ObservationDataset
from qtrad.runtime.ibkr_foundation import (
    foundation_config_payload,
    load_ibkr_foundation_outcome_blind_with_identity,
    preflight_ibkr_foundation,
    verify_ibkr_foundation,
    write_ibkr_foundation,
)
from qtrad.runtime.provider_history import (
    VerifiedProviderHistoryRows,
    read_provider_history_source_evidence,
)
from tests.test_provider_history import _FINGERPRINT, _published_provider_history, _request
from tests.test_r1_foundation import _config

_SHORT_STAGE8_END = datetime(2026, 2, 1, tzinfo=UTC) + timedelta(minutes=30)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _variable_fold_rows() -> tuple[dict[str, JsonValue], ...]:
    start = datetime(2026, 2, 1, tzinfo=UTC)
    rows: list[dict[str, JsonValue]] = []
    for fold_index in range(3):
        training_ids = tuple(
            hashlib.sha256(f"training:{fold_index}:{index}".encode()).hexdigest()
            for index in range(300)
        )
        validation_ids = tuple(
            hashlib.sha256(f"validation:{fold_index}:{index}".encode()).hexdigest()
            for index in range(100)
        )
        fold = Fold(
            fold_id=f"fold-{fold_index}",
            training_start=start,
            training_cutoff=start + timedelta(days=fold_index + 1),
            validation_start=start + timedelta(days=fold_index + 1),
            validation_end=start + timedelta(days=fold_index + 2),
            embargo_end=start + timedelta(days=fold_index + 1),
            training_target_ids=training_ids,
            validation_target_ids=validation_ids,
            holdout_excluded=True,
            membership_hash=membership_hash(training_ids, validation_ids),
        )
        rows.append(fold.as_json())
    return tuple(rows)


def test_variable_fold_payloads_split_before_child_byte_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = _variable_fold_rows()
    payloads = tuple(foundation_runtime._canonical_row(row) for row in rows)
    single_sizes = tuple(len(foundation_runtime._parquet_bytes((payload,))) for payload in payloads)
    combined_size = len(foundation_runtime._parquet_bytes(payloads))
    file_limit = (max(single_sizes) + combined_size) // 2
    assert max(single_sizes) < file_limit < combined_size
    monkeypatch.setattr(foundation_runtime, "_MAX_CHILD_FILE_BYTES", file_limit)
    monkeypatch.setattr(
        foundation_runtime,
        "_MAX_CHILD_PAYLOAD_BYTES",
        max(foundation_runtime._payload_byte_count(payload) for payload in payloads),
    )
    dataset_id = hashlib.sha256(b"fold-dataset").hexdigest()
    lineage = {
        "provider_manifest_sha256": "1" * 64,
        "provider_dataset_sha256": "2" * 64,
        "plan_sha256": "3" * 64,
        "aggregate_sha256": "4" * 64,
    }

    for mode in ("generic", "bounded"):
        bundle_root = tmp_path / mode
        bundle_root.mkdir()
        child_root = bundle_root / "foundation.children"
        if mode == "generic":
            references = foundation_runtime._write_child_parts(
                child_root,
                bundle_root,
                "folds",
                rows,
                dataset_id,
                lineage,
            )
        else:
            writer = bounded_foundation_runtime._DeferredChildWriter(
                child_root=child_root,
                bundle_root=bundle_root,
                child_name=child_root.name,
                kind="folds",
                lineage=lineage,
            )
            for payload in payloads:
                writer.add(payload)
            references = list(writer.finalize(dataset_id))
        assert len(references) == len(rows)
        reference_files = tuple(
            cast(str, cast(Mapping[str, object], reference)["file"]) for reference in references
        )
        assert all(
            (bundle_root / reference_file).stat().st_size <= file_limit
            for reference_file in reference_files
        )
        foundation_runtime._verify_children(
            bundle_root,
            {"folds": references},
            {"folds": rows},
            {"folds": dataset_id},
            lineage,
            child_kinds=("folds",),
        )

    monkeypatch.setattr(
        foundation_runtime,
        "_MAX_CHILD_FILE_BYTES",
        min(single_sizes) - 1,
    )
    oversized_root = tmp_path / "oversized"
    oversized_root.mkdir()
    with pytest.raises(ValueError, match="Parquet child exceeds its byte bound"):
        foundation_runtime._write_child_parts(
            oversized_root / "foundation.children",
            oversized_root,
            "folds",
            rows[:1],
            dataset_id,
            lineage,
        )


def _rewrite_payload_reference(bundle: Path, field: str, value: object) -> None:
    document = cast(dict[str, object], json.loads(bundle.read_text(encoding="utf-8")))
    payload = cast(dict[str, object], document["payload"])
    children = cast(dict[str, object], payload["children"])
    references = cast(list[object], children["observations"])
    reference = cast(dict[str, object], references[0])
    reference[field] = value
    document["build_sha256"] = hashlib.sha256(_canonical_json(payload)).hexdigest()
    bundle.write_bytes(_canonical_json(document) + b"\n")


def _rewrite_child_manifest(bundle: Path, field: str, value: object) -> None:
    document = cast(dict[str, object], json.loads(bundle.read_text(encoding="utf-8")))
    payload = cast(dict[str, object], document["payload"])
    children = cast(dict[str, object], payload["children"])
    references = cast(list[object], children["observations"])
    reference = cast(dict[str, object], references[0])
    manifest_reference = cast(str, reference["manifest_path"])
    old_path = bundle.parent / Path(manifest_reference)
    manifest = cast(dict[str, object], json.loads(old_path.read_text(encoding="utf-8")))
    manifest[field] = value
    identity = dict(manifest)
    identity.pop("manifest_sha256")
    manifest_sha256 = hashlib.sha256(_canonical_json(identity)).hexdigest()
    manifest["manifest_sha256"] = manifest_sha256
    new_path = old_path.with_name(f"part-000000-{manifest_sha256[:24]}.json")
    new_path.write_bytes(_canonical_json(manifest) + b"\n")
    old_path.unlink()
    reference["manifest_id"] = manifest_sha256[:24]
    reference["manifest_path"] = new_path.relative_to(bundle.parent).as_posix()
    reference["manifest_sha256"] = manifest_sha256
    document["build_sha256"] = hashlib.sha256(_canonical_json(payload)).hexdigest()
    bundle.write_bytes(_canonical_json(document) + b"\n")


def _foundation_bundle_fixture(tmp_path: Path) -> Path:
    _, _, provider_manifest = _published_provider_history(tmp_path)
    configuration = _config(
        cast(ObservationDataset, SimpleNamespace(dataset_id="0" * 64)),
        start=datetime(2026, 2, 1, tzinfo=UTC),
        end=datetime(2026, 2, 1, tzinfo=UTC) + timedelta(minutes=30),
    )
    bundle = tmp_path / "foundation.json"
    write_ibkr_foundation(
        bundle,
        provider_manifest=provider_manifest,
        configuration=configuration,
    )
    return bundle


@pytest.fixture(scope="module")
def _authenticated_foundation_template(
    tmp_path_factory: pytest.TempPathFactory,
) -> Path:
    template_root = tmp_path_factory.mktemp("authenticated-stage8-foundation")
    bundle = _foundation_bundle_fixture(template_root)
    verify_ibkr_foundation(bundle)
    return template_root


def _holdout_source_for_build(build: IBKRFoundationBuild) -> R2HoldoutTargetSource:
    availability = ibkr_availability_evidence(build)
    targets = cast(TargetDataset, build.targets)
    panel = cast(PanelDataset, build.panel)
    gaps = tuple(
        (
            str(gap["instrument_id"]),
            datetime.fromisoformat(str(gap["interval_start"])),
            datetime.fromisoformat(str(gap["interval_end"])),
        )
        for gap in build.provider_gaps
    )
    return R2HoldoutTargetSource.create_from_target_dataset(
        targets,
        holdout_range=build.configuration.holdout_range,
        primary_horizon_seconds=900,
        target_instruments=tuple(str(item) for item in IBKR_CONFIRMATORY_INSTRUMENTS),
        panel=panel,
        source_active_intervals=build.active_intervals,
        data_gaps=gaps,
        availability_evidence_id=_availability_dataset_id(
            cast(ObservationDataset, build.observations).dataset_id,
            availability,
        ),
    )


def test_stage8_writer_preserves_racing_output_on_child_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, provider_manifest = _published_provider_history(tmp_path)
    configuration = _config(
        cast(ObservationDataset, SimpleNamespace(dataset_id="0" * 64)),
        start=datetime(2026, 2, 1, tzinfo=UTC),
        end=_SHORT_STAGE8_END,
    )
    output = tmp_path / "racing-foundation.json"
    child_root = tmp_path / "racing-foundation.json.children"

    def fail_after_output_race(*_args: object, **_kwargs: object) -> dict[str, JsonValue]:
        output.write_text("competitor", encoding="utf-8")
        raise RuntimeError("simulated output race")

    monkeypatch.setattr(foundation_runtime, "_write_children", fail_after_output_race)

    with pytest.raises(RuntimeError, match="simulated output race"):
        write_ibkr_foundation(
            output,
            provider_manifest=provider_manifest,
            configuration=configuration,
        )

    assert output.read_text(encoding="utf-8") == "competitor"
    assert not child_root.exists()


def test_stage8_declarations_are_fixed_and_model_independent() -> None:
    assert tuple(str(instrument) for instrument, _ in IBKR_CONFIRMATORY_CANDIDATES) == (
        "fx:aud-usd",
        "fx:eur-usd",
        "index:australia-200",
        "index:us-500",
        "commodity:spot-gold",
        "commodity:us-crude",
    )
    assert (
        tuple(instrument for instrument, _ in IBKR_CONFIRMATORY_CANDIDATES)
        == IBKR_CONFIRMATORY_INSTRUMENTS
    )
    assert IBKR_CONFIRMATORY_GROUPS == ("FX", "indices", "commodities")


def test_provider_history_foundation_round_trips_and_replays_children(
    tmp_path: Path,
) -> None:
    _, _, provider_manifest = _published_provider_history(tmp_path)
    configuration = _config(
        cast(ObservationDataset, SimpleNamespace(dataset_id="0" * 64)),
        start=datetime(2026, 2, 1, tzinfo=UTC),
        end=_SHORT_STAGE8_END,
    )

    source_evidence = read_provider_history_source_evidence(provider_manifest)
    build = build_ibkr_foundation(source_evidence, configuration)

    assert (
        build.readiness.state
        is IBKRFoundationReadinessState.INSUFFICIENT_HISTORY_FOR_MODEL_CONCLUSION
    )
    assert set(build.readiness.rows_by_candidate) == {
        str(instrument) for instrument in IBKR_CONFIRMATORY_INSTRUMENTS
    }
    assert build.observations.rows[0].provenance is BarProvenance.IBKR_HISTORICAL
    assert build.observations.rows[0].source_external_id
    assert len(build.panel.rows) > 0
    assert len(build.targets.rows) > 0
    assert tuple(
        instrument_id
        for instrument_id in build.configuration.ordered_instruments
        if build.configuration.instrument_roles[instrument_id] is InstrumentRole.TARGET
    ) == tuple(sorted(str(instrument) for instrument in IBKR_CONFIRMATORY_INSTRUMENTS))

    bundle = tmp_path / "foundation.json"
    write_ibkr_foundation(
        bundle,
        provider_manifest=provider_manifest,
        configuration=configuration,
    )
    verified = verify_ibkr_foundation(bundle)

    assert verified.readiness.as_json() == build.readiness.as_json()
    assert verified.panel.dataset_id == build.panel.dataset_id
    with pytest.raises(FileExistsError):
        write_ibkr_foundation(
            bundle,
            provider_manifest=provider_manifest,
            configuration=configuration,
        )

    document = json.loads(bundle.read_text(encoding="utf-8"))
    assert not Path(document["provider_history_manifest"]).is_absolute()
    assert set(document["payload"]["children"]) == {
        "observations",
        "panel",
        "targets",
        "folds",
        "target-index",
        "causal-metadata",
        "blind-observations",
        "blind-panel",
        "g2-observations",
        "g2-panel",
        "pre-holdout-target",
    }
    assert all(
        "rows" not in child
        for children in document["payload"]["children"].values()
        for child in children
    )
    assert document["payload"]["readiness"]["evidence"]["fold_count"] == 0
    assert "INSUFFICIENT_COMMON_SUPPORT" in document["payload"]["readiness"]["causes"]
    document["payload"]["readiness"]["evidence"]["coverage_cells"][0]["threshold"] = "89/100"
    bundle.write_bytes(_canonical_json(document) + b"\n")
    with pytest.raises(ValueError, match="payload identity"):
        verify_ibkr_foundation(bundle)


def test_ibkr_outcome_blind_loader_does_not_decode_full_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, provider_manifest = _published_provider_history(tmp_path)
    configuration = _config(
        cast(ObservationDataset, SimpleNamespace(dataset_id="0" * 64)),
        start=datetime(2026, 2, 1, tzinfo=UTC),
        end=_SHORT_STAGE8_END,
    )
    source_evidence = read_provider_history_source_evidence(provider_manifest)
    full_build = build_ibkr_foundation(source_evidence, configuration)
    qualifying_readiness, _provider_gaps = _evaluate_session_aware_readiness()
    monkeypatch.setattr(
        foundation_runtime,
        "build_ibkr_foundation",
        lambda *_args, **_kwargs: replace(full_build, readiness=qualifying_readiness),
    )
    bundle = tmp_path / "foundation.json"
    write_ibkr_foundation(
        bundle,
        provider_manifest=provider_manifest,
        configuration=configuration,
    )
    holdout_source = _holdout_source_for_build(full_build)
    original_read_child_rows = foundation_runtime._read_child_rows

    def guarded_read_child_rows(
        path: Path, *, expected_row_count: int
    ) -> tuple[dict[str, JsonValue], ...]:
        forbidden = {"observations", "panel", "targets"}
        assert not forbidden.intersection(path.parts)
        return original_read_child_rows(path, expected_row_count=expected_row_count)

    monkeypatch.setattr(foundation_runtime, "_read_child_rows", guarded_read_child_rows)

    def reject_full_build(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("blind loading rebuilt the full foundation")

    monkeypatch.setattr(
        foundation_runtime,
        "build_ibkr_foundation",
        reject_full_build,
    )

    def reject_provider_rows(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("blind loading decoded provider rows")

    monkeypatch.setattr(
        provider_history_runtime,
        "_read_parquet_rows",
        reject_provider_rows,
    )
    blind_build, build_id = load_ibkr_foundation_outcome_blind_with_identity(
        bundle,
        holdout_target_source=holdout_source,
    )

    assert build_id
    assert isinstance(blind_build.targets, R2OutcomeBlindTargetView)
    assert blind_build.targets.rows == holdout_source.pre_holdout_target_dataset.rows

    _blind_build, build_id, g2_authority = (
        foundation_runtime._load_ibkr_foundation_outcome_blind_with_g2_authority(
            bundle,
            holdout_target_source=holdout_source,
        )
    )
    assert g2_authority.foundation_bundle_id == build_id
    assert not hasattr(foundation_runtime, "verify_ibkr_g2_feature_source")
    with pytest.raises(TypeError, match="requires VerifiedConfirmatoryG1"):
        r2_verification_runtime.verify_confirmatory_g2_feature_source(cast(Any, g2_authority))


def test_ibkr_full_verifier_accepts_legacy_four_child_bundle(tmp_path: Path) -> None:
    _, _, provider_manifest = _published_provider_history(tmp_path)
    configuration = _config(
        cast(ObservationDataset, SimpleNamespace(dataset_id="0" * 64)),
        start=datetime(2026, 2, 1, tzinfo=UTC),
        end=_SHORT_STAGE8_END,
    )
    bundle = tmp_path / "foundation.json"
    write_ibkr_foundation(
        bundle,
        provider_manifest=provider_manifest,
        configuration=configuration,
    )
    document = json.loads(bundle.read_text(encoding="utf-8"))
    payload = document["payload"]
    base_kinds = {"observations", "panel", "targets", "folds"}
    payload["children"] = {
        kind: value for kind, value in payload["children"].items() if kind in base_kinds
    }
    encoded_payload = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    document["build_sha256"] = hashlib.sha256(encoded_payload).hexdigest()
    document["payload"] = payload
    child_root = bundle.parent / f"{bundle.name}.children"
    extension_kinds = {
        "target-index",
        "causal-metadata",
        "blind-observations",
        "blind-panel",
        "g2-observations",
        "g2-panel",
        "pre-holdout-target",
    }
    for kind in extension_kinds:
        for directory in ("parquet", "manifests"):
            shutil.rmtree(child_root / directory / kind)
    bundle.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n")

    verified = verify_ibkr_foundation(bundle)
    assert verified.targets.rows


def test_stage8_source_evidence_keeps_request_results_streaming(
    tmp_path: Path,
) -> None:
    artifact, _, provider_manifest = _published_provider_history(tmp_path)

    source_evidence = read_provider_history_source_evidence(provider_manifest)

    assert isinstance(source_evidence.observations, VerifiedProviderHistoryRows)
    assert len(source_evidence.observations) == source_evidence.dataset.row_count
    instrument = source_evidence.observations.instruments[0]
    positioned = tuple(source_evidence.observations.iter_instrument_with_positions(instrument))
    assert positioned
    assert all(row.instrument_id == instrument for row, _ in positioned)
    assert [position for _, position in positioned] == sorted(
        position for _, position in positioned
    )
    assert not hasattr(source_evidence.source_artifact, "request_results")
    assert len(source_evidence.request_evidence) == len(artifact.request_results)
    assert all(not hasattr(item, "accepted_rows") for item in source_evidence.request_evidence)
    assert sum(item.accepted_row_count for item in source_evidence.request_evidence) == sum(
        len(result.accepted_rows) for result in artifact.request_results
    )


def test_stage8_forces_non_confirmatory_targets_to_context(tmp_path: Path) -> None:
    _, _, provider_manifest = _published_provider_history(tmp_path)
    configuration = _config(
        cast(ObservationDataset, SimpleNamespace(dataset_id="0" * 64)),
        start=datetime(2026, 2, 1, tzinfo=UTC),
        end=_SHORT_STAGE8_END,
    )
    extra_instrument = "fx:nzd-usd"
    extra_configuration = replace(
        configuration,
        ordered_instruments=(*configuration.ordered_instruments, extra_instrument),
        instrument_roles={
            **configuration.instrument_roles,
            extra_instrument: InstrumentRole.TARGET,
        },
    )

    source_evidence = read_provider_history_source_evidence(provider_manifest)
    build = build_ibkr_foundation(source_evidence, extra_configuration)

    assert build.configuration.instrument_roles[extra_instrument] is InstrumentRole.CONTEXT
    assert all(row.instrument_id != extra_instrument for row in build.targets.rows)
    assert (
        IBKRFoundationReadinessCause.INSUFFICIENT_COMMON_SUPPORT in build.readiness.causes
        or build.readiness.evidence["fold_count"] == 0
    )


def test_stage8_zero_valid_folds_remains_insufficient(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, provider_manifest = _published_provider_history(tmp_path)
    configuration = _config(
        cast(ObservationDataset, SimpleNamespace(dataset_id="0" * 64)),
        start=datetime(2026, 2, 1, tzinfo=UTC),
        end=_SHORT_STAGE8_END,
    )

    def no_valid_folds(*args: object, **kwargs: object) -> object:
        raise ValueError("no scientifically valid expanding folds are available")

    monkeypatch.setattr(
        "qtrad.application.ibkr_foundation.build_expanding_folds",
        no_valid_folds,
    )
    build = build_ibkr_foundation(
        read_provider_history_source_evidence(provider_manifest),
        configuration,
    )

    assert build.folds.folds == ()
    assert IBKRFoundationReadinessCause.INSUFFICIENT_COMMON_SUPPORT in build.readiness.causes
    assert (
        build.readiness.state
        is IBKRFoundationReadinessState.INSUFFICIENT_HISTORY_FOR_MODEL_CONCLUSION
    )


def _session_aware_source_evidence(
    *,
    context_failure: bool = False,
    missing_active_bar: bool = False,
) -> tuple[ProviderHistorySourceEvidence, TargetDataset, datetime, datetime]:
    source_start = datetime(2026, 2, 1, tzinfo=UTC)
    source_end = datetime(2026, 3, 5, tzinfo=UTC)
    requests: list[object] = []
    results: list[object] = []
    observations: list[SimpleNamespace] = []
    target_rows: list[object] = []
    contracts: list[object] = []

    def add_instrument(instrument: str, con_id: int, *, no_data: bool = False) -> None:
        instrument_id = InstrumentId(instrument)
        fingerprint = replace(_FINGERPRINT, con_id=con_id)
        contracts.append(SimpleNamespace(instrument_id=instrument_id, fingerprint=fingerprint))
        day = source_start
        while day < source_end:
            chunk_end = min(day + timedelta(days=1), source_end)
            bar_request = _request(
                day,
                chunk_end,
                IbkrHistoricalRequestKind.MIDPOINT_BARS,
                instrument=instrument_id,
                fingerprint=fingerprint,
            )
            schedule_request = _request(
                day,
                chunk_end,
                IbkrHistoricalRequestKind.SCHEDULE,
                instrument=instrument_id,
                fingerprint=fingerprint,
            )
            requests.extend((bar_request, schedule_request))

            sessions: list[dict[str, object]] = []
            accepted_rows: list[dict[str, object]] = []
            if day.weekday() < 5:
                session_start = day + timedelta(minutes=1)
                session_end = session_start + timedelta(minutes=65)
                sessions.append(
                    {
                        "interval_start": session_start.isoformat(),
                        "interval_end": session_end.isoformat(),
                        "active": True,
                    }
                )
                if not no_data:
                    for minute in range(65):
                        bar_start = session_start + timedelta(minutes=minute)
                        missing = (
                            missing_active_bar
                            and instrument == str(IBKR_CONFIRMATORY_INSTRUMENTS[0])
                            and day == source_start + timedelta(days=1)
                            and minute == 10
                        )
                        if not missing:
                            accepted_rows.append({"bar_start": bar_start.isoformat()})
                            observations.append(SimpleNamespace(instrument_id=instrument))
                        target_rows.append(
                            SimpleNamespace(
                                instrument_id=instrument,
                                decision_time=bar_start,
                                target_start_time=bar_start,
                                target_end_time=bar_start + timedelta(minutes=15),
                                horizon=timedelta(minutes=15),
                                return_disposition=(
                                    ReturnDisposition.MISSING_START
                                    if missing
                                    else ReturnDisposition.VALID
                                ),
                            )
                        )
            else:
                sessions.append(
                    {
                        "interval_start": day.isoformat(),
                        "interval_end": chunk_end.isoformat(),
                        "active": False,
                    }
                )

            result_index = len(results) + 1
            results.append(
                SimpleNamespace(
                    request_sha256=bar_request.request_sha256,
                    result_sha256=f"{result_index:064x}",
                    evidence_disposition=(
                        IbkrHistoricalEvidenceDisposition.NO_DATA_RETURNED
                        if no_data or not accepted_rows
                        else IbkrHistoricalEvidenceDisposition.SUCCEEDED
                    ),
                    accepted_rows=tuple(accepted_rows),
                    sessions=(),
                )
            )
            results.append(
                SimpleNamespace(
                    request_sha256=schedule_request.request_sha256,
                    result_sha256=f"{result_index + 1:064x}",
                    evidence_disposition=IbkrHistoricalEvidenceDisposition.SUCCEEDED,
                    accepted_rows=(),
                    sessions=tuple(sessions),
                )
            )
            day = chunk_end

    for index, instrument in enumerate(IBKR_CONFIRMATORY_INSTRUMENTS):
        add_instrument(str(instrument), 42 + index)
    if context_failure:
        add_instrument("fx:nzd-usd", 99, no_data=True)

    source_plan = SimpleNamespace(
        contract_selection_sha256="c" * 64,
        plan_sha256="p" * 64,
        runtime_sha256="r" * 64,
        requests=tuple(requests),
        eligible_contracts=tuple(contracts),
    )
    aggregate = SimpleNamespace(
        aggregate_sha256="a" * 64,
        coverage_summary={"planned_request_count": len(requests)},
        entitlement_summary={
            "provider_history_eligible_instruments": [
                str(instrument) for instrument in IBKR_CONFIRMATORY_INSTRUMENTS
            ]
        },
    )
    source_artifact = SimpleNamespace(
        plan=source_plan,
        aggregate=aggregate,
        request_results=tuple(results),
    )
    partition_instruments = sorted({str(row.instrument_id) for row in observations})
    source_evidence = cast(
        ProviderHistorySourceEvidence,
        SimpleNamespace(
            observations=tuple(observations),
            observation_summary=None,
            source_artifact=source_artifact,
            dataset=SimpleNamespace(
                row_count=len(observations),
                partitions=tuple(
                    SimpleNamespace(
                        instrument_id=instrument,
                        row_count=sum(
                            1 for row in observations if str(row.instrument_id) == instrument
                        ),
                    )
                    for instrument in partition_instruments
                ),
            ),
        ),
    )
    targets = cast(TargetDataset, SimpleNamespace(rows=tuple(target_rows)))
    return source_evidence, targets, source_start, source_end


def _evaluate_session_aware_readiness(
    *,
    context_failure: bool = False,
    missing_active_bar: bool = False,
    wide_confirmatory_gap: bool = False,
) -> tuple[IBKRFoundationReadiness, tuple[Mapping[str, JsonValue], ...]]:
    source_evidence, targets, source_start, source_end = _session_aware_source_evidence(
        context_failure=context_failure,
        missing_active_bar=missing_active_bar,
    )
    active_intervals, provider_gaps = _provider_evidence(source_evidence)
    if wide_confirmatory_gap:
        instrument = str(IBKR_CONFIRMATORY_INSTRUMENTS[0])
        gap_start = source_start + timedelta(days=1)
        gap_end = source_start + timedelta(days=6)
        for row in targets.rows:
            if (
                row.instrument_id == instrument
                and gap_start < row.target_end_time
                and row.target_start_time < gap_end
            ):
                cast(Any, row).return_disposition = ReturnDisposition.MISSING_START
        provider_gaps = (
            *provider_gaps,
            {
                "instrument_id": instrument,
                "interval_start": gap_start.isoformat(),
                "interval_end": gap_end.isoformat(),
                "disposition": "MISSING_BAR",
                "request_sha256": "1" * 64,
                "result_sha256": "2" * 64,
            },
        )
    validation_start = source_start + timedelta(days=14)
    validation_end = validation_start + timedelta(days=7)
    folds = (
        Fold(
            fold_id="fold-1",
            training_start=source_start,
            training_cutoff=validation_start,
            validation_start=validation_start,
            validation_end=validation_end,
            embargo_end=validation_start,
            training_target_ids=(),
            validation_target_ids=(),
            holdout_excluded=True,
            membership_hash=membership_hash((), ()),
        ),
    )
    readiness = evaluate_ibkr_foundation_readiness(
        source_evidence,
        targets,
        source_start=source_start,
        source_end=source_end,
        active_intervals=active_intervals,
        provider_gaps=provider_gaps,
        primary_horizon=timedelta(minutes=15),
        folds=folds,
        holdout_range=(validation_end, source_end),
    )
    return readiness, tuple(provider_gaps)


def test_stage8_session_aware_gaps_allow_qualifying_history() -> None:
    readiness, provider_gaps = _evaluate_session_aware_readiness()

    assert readiness.state is IBKRFoundationReadinessState.QUALIFYING_HISTORY_READY
    assert provider_gaps == ()


def test_stage8_isolated_active_session_gap_uses_block_coverage() -> None:
    readiness, provider_gaps = _evaluate_session_aware_readiness(missing_active_bar=True)

    assert readiness.state is IBKRFoundationReadinessState.QUALIFYING_HISTORY_READY
    assert IBKRFoundationReadinessCause.INSUFFICIENT_BLOCK_COVERAGE not in readiness.causes
    assert readiness.evidence["blocking_coverage_cells"] == []
    assert any(gap["disposition"] == "MISSING_BAR" for gap in provider_gaps)
    assert readiness.evidence["raw_provider_gaps"]


def test_stage8_context_instrument_gaps_do_not_block_confirmatory_readiness() -> None:
    readiness, provider_gaps = _evaluate_session_aware_readiness(context_failure=True)

    assert readiness.state is IBKRFoundationReadinessState.QUALIFYING_HISTORY_READY
    assert readiness.evidence["provider_gap_count"] == 0
    assert cast(int, readiness.evidence["total_provider_gap_count"]) > 0
    assert any(gap["instrument_id"] == "fx:nzd-usd" for gap in provider_gaps)
    assert readiness.evidence["raw_provider_gaps"]


def test_stage8_below_block_coverage_uses_specific_cause() -> None:
    readiness, _ = _evaluate_session_aware_readiness(wide_confirmatory_gap=True)

    assert readiness.state is IBKRFoundationReadinessState.INSUFFICIENT_HISTORY_FOR_MODEL_CONCLUSION
    assert IBKRFoundationReadinessCause.INSUFFICIENT_BLOCK_COVERAGE in readiness.causes
    assert IBKRFoundationReadinessCause.INSUFFICIENT_COMMON_SUPPORT not in readiness.causes
    assert readiness.evidence["blocking_coverage_cells"]


def _synthetic_coverage(
    gap_count: int,
    *,
    duplicate_gap: bool = False,
    reverse_inputs: bool = False,
    active_count: int = 100,
    row_count: int = 100,
    other_ineligible_index: int | None = None,
    interior_gap_index: int | None = None,
    missing_end_gap_index: int | None = None,
) -> tuple[
    dict[str, JsonValue],
    tuple[dict[str, JsonValue], ...],
    tuple[str, ...],
]:
    start = datetime(2026, 2, 1, tzinfo=UTC)
    instrument = str(IBKR_CONFIRMATORY_INSTRUMENTS[0])
    rows = [
        SimpleNamespace(
            instrument_id=instrument,
            decision_time=start + timedelta(minutes=5 * index),
            target_start_time=start + timedelta(minutes=5 * index),
            target_end_time=start + timedelta(minutes=5 * index + 1),
            horizon=timedelta(minutes=1),
            return_disposition=(
                ReturnDisposition.MISSING_END
                if index == missing_end_gap_index
                else (
                    ReturnDisposition.MISSING_START
                    if index < gap_count or index == other_ineligible_index
                    else ReturnDisposition.VALID
                )
            ),
        )
        for index in range(row_count)
    ]
    intervals = tuple((row.target_start_time, row.target_end_time) for row in rows[:active_count])
    gaps: list[Mapping[str, JsonValue]] = [
        {
            "instrument_id": instrument,
            "interval_start": (row.target_start_time - timedelta(minutes=1)).isoformat(),
            "interval_end": row.target_start_time.isoformat(),
            "disposition": "MISSING_BAR",
            "request_sha256": f"{index + 1:064x}",
            "result_sha256": f"{index + 101:064x}",
        }
        for index, row in enumerate(rows[:gap_count])
    ]
    if interior_gap_index is not None:
        row = rows[interior_gap_index]
        gaps.append(
            {
                "instrument_id": instrument,
                "interval_start": (row.target_start_time + timedelta(seconds=20)).isoformat(),
                "interval_end": (row.target_start_time + timedelta(seconds=40)).isoformat(),
                "disposition": "MISSING_BAR",
                "request_sha256": "f" * 64,
                "result_sha256": "e" * 64,
            }
        )
    if missing_end_gap_index is not None:
        row = rows[missing_end_gap_index]
        gaps.append(
            {
                "instrument_id": instrument,
                "interval_start": (row.target_end_time - timedelta(minutes=1)).isoformat(),
                "interval_end": row.target_end_time.isoformat(),
                "disposition": "MISSING_BAR",
                "request_sha256": "d" * 64,
                "result_sha256": "c" * 64,
            }
        )
    if duplicate_gap and gaps:
        gaps.append(dict(gaps[0]))
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
    candidates = [str(item) for item in IBKR_CONFIRMATORY_INSTRUMENTS]
    active = {
        candidate: intervals if candidate == instrument else ()
        for candidate in (reversed(candidates) if reverse_inputs else candidates)
    }
    cells, blocking, _ = _ibkr_opportunity_coverage(
        targets=cast(
            TargetDataset,
            SimpleNamespace(rows=tuple(reversed(rows)) if reverse_inputs else tuple(rows)),
        ),
        active_intervals=active,
        provider_gaps=tuple(reversed(gaps)) if reverse_inputs else tuple(gaps),
        primary_horizon=timedelta(minutes=1),
        folds=(fold,),
        holdout_range=(start + timedelta(days=2), start + timedelta(days=3)),
    )
    return cells[0], cells, blocking


@pytest.mark.parametrize(
    ("gap_count", "coverage", "passed"),
    ((9, "91/100", True), (10, "9/10", True), (11, "89/100", False)),
)
def test_stage8_block_coverage_threshold_is_exact(
    gap_count: int, coverage: str, passed: bool
) -> None:
    cell, cells, blocking = _synthetic_coverage(gap_count)

    assert cell["coverage"] == coverage
    assert cell["passed"] is passed
    assert (cell["key"] in blocking) is not passed
    assert cells[3]["coverage"] is None
    assert cells[3]["passed"] is False


def test_stage8_other_ineligible_opportunity_follows_amended_denominator() -> None:
    cell, _, blocking = _synthetic_coverage(
        10,
        active_count=101,
        row_count=101,
        other_ineligible_index=100,
    )

    assert cell["opportunity_counts"] == {
        "ELIGIBLE": 90,
        "GAP": 10,
        "INACTIVE": 0,
        "OTHER_INELIGIBLE": 1,
    }
    assert cell["coverage"] == "9/10"
    assert cell["passed"] is True
    assert cell["key"] not in blocking


def test_stage8_valid_target_with_interior_provider_gap_remains_eligible() -> None:
    cell, _, blocking = _synthetic_coverage(0, interior_gap_index=50)

    assert cell["opportunity_counts"] == {
        "ELIGIBLE": 100,
        "GAP": 0,
        "INACTIVE": 0,
        "OTHER_INELIGIBLE": 0,
    }
    assert cell["coverage"] == "1"
    assert cell["passed"] is True
    assert cell["key"] not in blocking


@pytest.mark.parametrize(
    ("gap_count", "missing_end_gap_index"),
    ((1, None), (0, 0)),
    ids=("missing-start", "missing-end"),
)
def test_stage8_missing_endpoint_uses_preceding_provider_bar_gap(
    gap_count: int,
    missing_end_gap_index: int | None,
) -> None:
    cell, _, blocking = _synthetic_coverage(
        gap_count,
        active_count=1,
        row_count=1,
        missing_end_gap_index=missing_end_gap_index,
    )

    assert cell["opportunity_counts"] == {
        "ELIGIBLE": 0,
        "GAP": 1,
        "INACTIVE": 0,
        "OTHER_INELIGIBLE": 0,
    }
    assert cell["coverage"] == "0"
    assert cell["passed"] is False
    assert cell["key"] in blocking


def test_stage8_other_ineligible_ignores_unrelated_interior_gap() -> None:
    cell, _, blocking = _synthetic_coverage(
        0,
        active_count=1,
        row_count=1,
        other_ineligible_index=0,
        interior_gap_index=0,
    )

    assert cell["opportunity_counts"] == {
        "ELIGIBLE": 0,
        "GAP": 0,
        "INACTIVE": 0,
        "OTHER_INELIGIBLE": 1,
    }
    assert cell["coverage"] is None
    assert cell["passed"] is False
    assert cell["key"] in blocking


def test_stage8_coverage_deduplicates_gaps_excludes_inactive_and_is_deterministic() -> None:
    cell, cells, blocking = _synthetic_coverage(
        10, duplicate_gap=True, active_count=99, other_ineligible_index=98
    )
    _, reversed_cells, reversed_blocking = _synthetic_coverage(
        10,
        duplicate_gap=True,
        reverse_inputs=True,
        active_count=99,
        other_ineligible_index=98,
    )

    assert cell["opportunity_counts"] == {
        "ELIGIBLE": 88,
        "GAP": 10,
        "INACTIVE": 1,
        "OTHER_INELIGIBLE": 1,
    }
    assert cell["coverage"] == "44/49"
    assert cells == reversed_cells
    assert blocking == reversed_blocking


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("contract", "unsupported-child-contract", "child contract is unsupported"),
        ("schema_version", 2, "child schema is unsupported"),
        ("part_index", 1, "not contiguous"),
        (
            "lineage",
            {
                "provider_manifest_sha256": "1" * 64,
                "provider_dataset_sha256": "2" * 64,
                "plan_sha256": "3" * 64,
                "aggregate_sha256": "4" * 64,
            },
            "lineage differs from replay",
        ),
    ),
)
def test_stage8_rejects_child_manifest_lineage_and_part_drift(
    tmp_path: Path,
    _authenticated_foundation_template: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    clone_root = tmp_path / "authenticated-stage8-foundation"
    shutil.copytree(_authenticated_foundation_template, clone_root)
    bundle = clone_root / "foundation.json"
    _rewrite_child_manifest(bundle, field, value)

    with pytest.raises(ValueError, match=message):
        verify_ibkr_foundation(bundle)


def test_stage8_cli_requires_one_foundation_source() -> None:
    parser = build_parser()

    provider_args = parser.parse_args(
        [
            "research",
            "foundation",
            "build",
            "--provider-history-manifest",
            "provider.json",
            "--configuration",
            "configuration.json",
            "--output",
            "foundation.json",
        ]
    )
    assert provider_args.provider_history_manifest == Path("provider.json")
    assert provider_args.observations_manifest is None

    readiness_args = parser.parse_args(
        ["research", "foundation", "readiness", "--bundle", "foundation.json"]
    )
    assert readiness_args.bundle == Path("foundation.json")

    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "research",
                "foundation",
                "build",
                "--observations-manifest",
                "observations.json",
                "--provider-history-manifest",
                "provider.json",
                "--configuration",
                "configuration.json",
                "--output",
                "foundation.json",
            ]
        )


def test_stage8_cli_build_and_verify_round_trip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, _, provider_manifest = _published_provider_history(tmp_path)
    configuration = _config(
        cast(ObservationDataset, SimpleNamespace(dataset_id="0" * 64)),
        start=datetime(2026, 2, 1, tzinfo=UTC),
        end=_SHORT_STAGE8_END,
    )
    configuration_path = tmp_path / "configuration.json"
    configuration_path.write_text(
        json.dumps(foundation_config_payload(configuration)),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "Settings", lambda: SimpleNamespace(log_level="INFO"))
    monkeypatch.setattr(cli, "configure_logging", lambda _: None)
    bundle = tmp_path / "cli-foundation.json"
    common_args = [
        "--provider-history-manifest",
        str(provider_manifest),
        "--configuration",
        str(configuration_path),
        "--output",
        str(bundle),
    ]
    cli.main(["research", "foundation", "preflight", *common_args])
    preflight_output = json.loads(capsys.readouterr().out)
    assert preflight_output["contract"] == "qtrad-stage8-foundation-preflight-v1"
    assert preflight_output["output"] == str(bundle.resolve())
    assert not bundle.exists()
    assert not bundle.with_name(f"{bundle.name}.children").exists()

    cli.main(["research", "foundation", "build", *common_args])
    build_output = json.loads(capsys.readouterr().out)
    assert build_output["contract"] == "qtrad-stage8-foundation-publication-v1"
    assert build_output["output"] == str(bundle.resolve())
    assert build_output["build_sha256"]
    assert build_output["readiness_state"] == "INSUFFICIENT_HISTORY_FOR_MODEL_CONCLUSION"
    assert build_output["readiness_causes"]
    assert build_output["coverage_summary"]["threshold"] == "9/10"
    assert bundle.is_file()

    nonqualifying_build = build_ibkr_foundation(
        read_provider_history_source_evidence(provider_manifest), configuration
    )
    with pytest.raises(ValueError, match="nonqualifying IBKR foundation"):
        foundation_runtime._load_ibkr_foundation_outcome_blind_with_g2_authority(
            bundle,
            holdout_target_source=_holdout_source_for_build(nonqualifying_build),
        )

    cli.main(["research", "foundation", "verify", "--bundle", str(bundle)])
    verify_output = json.loads(capsys.readouterr().out)
    assert verify_output["source_class"] == "IBKR_HISTORICAL_RESEARCH"
    cli.main(["research", "foundation", "readiness", "--bundle", str(bundle)])
    readiness_output = json.loads(capsys.readouterr().out)
    assert readiness_output["state"] == "INSUFFICIENT_HISTORY_FOR_MODEL_CONCLUSION"


def test_provider_history_foundation_bounded_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, provider_manifest = _published_provider_history(tmp_path)
    source_evidence = read_provider_history_source_evidence(provider_manifest)
    configuration = _config(
        cast(ObservationDataset, SimpleNamespace(dataset_id="0" * 64)),
        start=datetime(2026, 2, 1, tzinfo=UTC),
        end=_SHORT_STAGE8_END,
    )
    full = build_ibkr_foundation(source_evidence, configuration)
    import qtrad.runtime.ibkr_foundation as foundation_runtime

    monkeypatch.setattr(foundation_runtime, "_BOUNDED_PROVIDER_HISTORY_ROWS", 0)
    bundle = tmp_path / "bounded-foundation.json"
    write_ibkr_foundation(
        bundle,
        provider_manifest=provider_manifest,
        configuration=configuration,
    )
    verified = verify_ibkr_foundation(bundle)
    assert verified.readiness.as_json() == full.readiness.as_json()
    assert verified.observations.dataset_id == full.observations.dataset_id
    assert verified.panel.dataset_id == full.panel.dataset_id
    assert verified.targets.dataset_id == full.targets.dataset_id
    assert verified.folds.dataset_id == full.folds.dataset_id
    assert verified.observations.dataset_id
    assert verified.target_index is None
    assert verified.causal_metadata is None
    document = json.loads(bundle.read_text(encoding="utf-8"))
    assert set(document["payload"]["children"]) == {
        "observations",
        "panel",
        "targets",
        "folds",
    }


def test_bounded_foundation_parallel_derivation_matches_generic_across_chunk_seam(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, provider_manifest = _published_provider_history(tmp_path)
    source_evidence = read_provider_history_source_evidence(provider_manifest)
    monkeypatch.setattr(
        bounded_foundation_runtime,
        "_DERIVATION_CHUNK",
        timedelta(minutes=15),
    )
    configuration = _config(
        cast(ObservationDataset, SimpleNamespace(dataset_id="0" * 64)),
        start=datetime(2026, 2, 1, tzinfo=UTC),
        end=_SHORT_STAGE8_END + timedelta(minutes=1),
    )
    generic = build_ibkr_foundation(source_evidence, configuration)
    monkeypatch.setattr(foundation_runtime, "_BOUNDED_PROVIDER_HISTORY_ROWS", 0)

    single = write_ibkr_foundation(
        tmp_path / "single-worker.json",
        provider_manifest=provider_manifest,
        configuration=configuration,
        workers=1,
    )
    parallel = write_ibkr_foundation(
        tmp_path / "parallel-workers.json",
        provider_manifest=provider_manifest,
        configuration=configuration,
        workers=2,
    )

    expected_ids = (
        generic.observations.dataset_id,
        generic.panel.dataset_id,
        generic.targets.dataset_id,
        generic.folds.dataset_id,
    )
    assert (
        single.observations.dataset_id,
        single.panel.dataset_id,
        single.targets.dataset_id,
        single.folds.dataset_id,
    ) == expected_ids
    assert (
        parallel.observations.dataset_id,
        parallel.panel.dataset_id,
        parallel.targets.dataset_id,
        parallel.folds.dataset_id,
    ) == expected_ids


def test_bounded_replay_reuses_verified_parts_and_reports_exact_divergence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, provider_manifest = _published_provider_history(tmp_path)
    configuration = _config(
        cast(ObservationDataset, SimpleNamespace(dataset_id="0" * 64)),
        start=datetime(2026, 2, 1, tzinfo=UTC),
        end=_SHORT_STAGE8_END,
    )
    monkeypatch.setattr(foundation_runtime, "_BOUNDED_PROVIDER_HISTORY_ROWS", 0)
    bundle = tmp_path / "replay-checkpoint-foundation.json"
    write_ibkr_foundation(
        bundle,
        provider_manifest=provider_manifest,
        configuration=configuration,
        workers=1,
    )
    replay_checkpoint = tmp_path / "replay-checkpoint"
    first = verify_ibkr_foundation(
        bundle,
        replay_checkpoint_root=replay_checkpoint,
        workers=1,
    )

    def reject_parquet_rebuild(_rows: object) -> bytes:
        raise AssertionError("verified replay Parquet part was rebuilt")

    monkeypatch.setattr(foundation_runtime, "_parquet_bytes", reject_parquet_rebuild)
    second = verify_ibkr_foundation(
        bundle,
        replay_checkpoint_root=replay_checkpoint,
        workers=1,
    )
    assert second.readiness.as_json() == first.readiness.as_json()

    document = json.loads(bundle.read_text(encoding="utf-8"))
    panel_reference = document["payload"]["children"]["panel"][0]
    panel_path = bundle.parent / panel_reference["file"]
    panel_path.write_bytes(panel_path.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="child panel part 0 file bytes diverge from replay"):
        verify_ibkr_foundation(
            bundle,
            replay_checkpoint_root=replay_checkpoint,
            workers=1,
        )


def test_compact_provider_observation_summary_matches_row_replay(tmp_path: Path) -> None:
    _, _, provider_manifest = _published_provider_history(tmp_path)
    source_evidence = read_provider_history_source_evidence(provider_manifest)

    assert source_evidence.observation_summary is not None
    summarized = _provider_evidence(source_evidence, include_bounds=True)
    replayed = _provider_evidence(
        replace(source_evidence, observation_summary=None), include_bounds=True
    )

    assert summarized == replayed


def test_stage8_preflight_authenticates_checkpoint_without_decoding_or_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, provider_manifest = _published_provider_history(tmp_path)
    configuration = _config(
        cast(ObservationDataset, SimpleNamespace(dataset_id="0" * 64)),
        start=datetime(2026, 2, 1, tzinfo=UTC),
        end=_SHORT_STAGE8_END,
    )
    monkeypatch.setattr(foundation_runtime, "_BOUNDED_PROVIDER_HISTORY_ROWS", 0)
    checkpoint_root = tmp_path / "checkpoint"
    output = tmp_path / "foundation.json"

    def reject_expensive_work(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("preflight started provider decoding or foundation derivation")

    monkeypatch.setattr(
        foundation_runtime, "read_provider_history_source_evidence", reject_expensive_work
    )
    monkeypatch.setattr(foundation_runtime, "build_ibkr_foundation", reject_expensive_work)
    monkeypatch.setattr(foundation_runtime.pl, "read_parquet", reject_expensive_work)

    manifest_document = json.loads(provider_manifest.read_bytes())
    noncanonical_manifest = tmp_path / "noncanonical-provider-history.json"
    noncanonical_manifest.write_text(json.dumps(manifest_document, indent=2), encoding="utf-8")

    semantic_document = dict(manifest_document)
    semantic_document["selector_contract"] = "unsupported-selector"
    semantic_identity = dict(semantic_document)
    semantic_identity.pop("manifest_sha256")
    semantic_document["manifest_sha256"] = provider_history_runtime._sha256_json(semantic_identity)
    semantic_manifest = tmp_path / "mutated-provider-history.json"
    semantic_manifest.write_bytes(_canonical_json(semantic_document))

    for invalid_manifest, message, invalid_checkpoint in (
        (
            noncanonical_manifest,
            "manifest bytes are not canonical",
            tmp_path / "noncanonical-checkpoint",
        ),
        (
            semantic_manifest,
            "availability selector contract is unsupported",
            tmp_path / "semantic-checkpoint",
        ),
    ):
        with pytest.raises(ValueError, match=message):
            preflight_ibkr_foundation(
                output,
                provider_manifest=invalid_manifest,
                configuration=configuration,
                checkpoint_root=invalid_checkpoint,
                workers=1,
            )
        assert not invalid_checkpoint.exists()

    result = preflight_ibkr_foundation(
        output,
        provider_manifest=provider_manifest,
        configuration=configuration,
        checkpoint_root=checkpoint_root,
        workers=1,
    )

    assert result["checkpoint_status"] == "INITIALIZED"
    assert result["configuration_id"] == configuration.configuration_id
    assert result["provider_history_dataset_sha256"]
    assert not output.exists()
    assert not output.with_name(f"{output.name}.children").exists()

    identity_path = checkpoint_root / "identity.json"
    legacy_identity = json.loads(identity_path.read_bytes())
    legacy_identity["implementation_sha256"] = (
        bounded_foundation_runtime._LEGACY_CHECKPOINT_IMPLEMENTATION_SHA256
    )
    identity_path.write_bytes(_canonical_json(legacy_identity) + b"\n")
    retained_identity = identity_path.read_bytes()

    reused = preflight_ibkr_foundation(
        output,
        provider_manifest=provider_manifest,
        configuration=configuration,
        checkpoint_root=checkpoint_root,
        workers=1,
    )
    assert reused["checkpoint_status"] == "REUSED"
    assert identity_path.read_bytes() == retained_identity

    junk_root = tmp_path / "junk-checkpoint"
    junk_root.mkdir()
    (junk_root / "unexpected").write_text("junk", encoding="utf-8")
    with pytest.raises(ValueError, match="checkpoint identity is missing"):
        preflight_ibkr_foundation(
            output,
            provider_manifest=provider_manifest,
            configuration=configuration,
            checkpoint_root=junk_root,
            workers=1,
        )
    assert not output.exists()


def test_stage8_observation_capture_aborts_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, provider_manifest = _published_provider_history(tmp_path)
    configuration = _config(
        cast(ObservationDataset, SimpleNamespace(dataset_id="0" * 64)),
        start=datetime(2026, 2, 1, tzinfo=UTC),
        end=_SHORT_STAGE8_END,
    )
    monkeypatch.setattr(foundation_runtime, "_BOUNDED_PROVIDER_HISTORY_ROWS", 0)
    original_verifier = foundation_runtime.read_provider_history_source_evidence

    def fail_after_verification(
        path: Path,
        *,
        verified_partition_callback: Callable[
            [tuple[ProviderHistoricalObservation, ...], int], None
        ]
        | None = None,
        source_replay_workers: int = 1,
    ) -> ProviderHistorySourceEvidence:
        original_verifier(
            path,
            verified_partition_callback=verified_partition_callback,
            source_replay_workers=source_replay_workers,
        )
        raise ValueError("forced failure after source verification")

    monkeypatch.setattr(
        foundation_runtime,
        "read_provider_history_source_evidence",
        fail_after_verification,
    )
    checkpoint_root = tmp_path / "aborted-checkpoint"
    output = tmp_path / "aborted-foundation.json"

    with pytest.raises(ValueError, match="forced failure after source verification"):
        write_ibkr_foundation(
            output,
            provider_manifest=provider_manifest,
            configuration=configuration,
            checkpoint_root=checkpoint_root,
            workers=1,
        )

    assert {
        path.relative_to(checkpoint_root) for path in checkpoint_root.rglob("*") if path.is_file()
    } == {Path("identity.json")}
    assert not output.exists()
    assert not output.with_name(f"{output.name}.children").exists()


def test_bounded_foundation_reuses_identity_bound_checkpoints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, provider_manifest = _published_provider_history(tmp_path)
    configuration = _config(
        cast(ObservationDataset, SimpleNamespace(dataset_id="0" * 64)),
        start=datetime(2026, 2, 1, tzinfo=UTC),
        end=_SHORT_STAGE8_END,
    )
    monkeypatch.setattr(foundation_runtime, "_BOUNDED_PROVIDER_HISTORY_ROWS", 0)
    original_source_verifier = foundation_runtime.read_provider_history_source_evidence

    global_iterations = 0

    def reject_global_provider_row_iteration(_rows: VerifiedProviderHistoryRows):
        nonlocal global_iterations
        global_iterations += 1
        raise AssertionError("checkpointed Stage 8 rescanned all provider rows")

    monkeypatch.setattr(
        VerifiedProviderHistoryRows,
        "__iter__",
        reject_global_provider_row_iteration,
    )
    checkpoint_root = tmp_path / "stage8-checkpoint"
    first_progress: list[Mapping[str, object]] = []
    first = write_ibkr_foundation(
        tmp_path / "first-foundation.json",
        provider_manifest=provider_manifest,
        configuration=configuration,
        checkpoint_root=checkpoint_root,
        progress_callback=first_progress.append,
    )
    assert global_iterations == 0
    global_iterations = 0
    assert any(
        event.get("phase") == "source-verification" and event.get("event") == "completed"
        for event in first_progress
    )
    assert any(
        event.get("phase") == "observations" and event.get("event") == "reused"
        for event in first_progress
    )
    assert any(
        event.get("phase") == "derived" and event.get("event") == "completed"
        for event in first_progress
    )

    identity_path = checkpoint_root / "identity.json"
    legacy_identity = json.loads(identity_path.read_bytes())
    legacy_identity["implementation_sha256"] = (
        bounded_foundation_runtime._LEGACY_CHECKPOINT_IMPLEMENTATION_SHA256
    )
    identity_path.write_bytes(_canonical_json(legacy_identity) + b"\n")

    def checkpoint_snapshot() -> dict[Path, tuple[int, int, str]]:
        return {
            path.relative_to(checkpoint_root): (
                path.stat().st_ino,
                path.stat().st_mtime_ns,
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
            for path in checkpoint_root.rglob("*")
            if path.is_file()
        }

    retained_checkpoint = checkpoint_snapshot()

    def reject_repeated_semantic_verification(
        _path: Path, **_kwargs: object
    ) -> ProviderHistorySourceEvidence:
        raise AssertionError("completed Stage 8 source verification was repeated")

    monkeypatch.setattr(
        foundation_runtime,
        "read_provider_history_source_evidence",
        reject_repeated_semantic_verification,
    )
    second_progress: list[Mapping[str, object]] = []
    second_bundle = tmp_path / "second-foundation.json"
    second = write_ibkr_foundation(
        second_bundle,
        provider_manifest=provider_manifest,
        configuration=configuration,
        checkpoint_root=checkpoint_root,
        progress_callback=second_progress.append,
    )
    assert global_iterations == 0
    assert any(
        event.get("phase") == "source-verification" and event.get("event") == "reused"
        for event in second_progress
    )
    assert any(
        event.get("phase") == "observations" and event.get("event") == "reused"
        for event in second_progress
    )
    assert any(
        event.get("phase") == "derived" and event.get("event") == "reused"
        for event in second_progress
    )
    assert checkpoint_snapshot() == retained_checkpoint
    assert second.observations.dataset_id == first.observations.dataset_id
    assert second.panel.dataset_id == first.panel.dataset_id
    assert second.targets.dataset_id == first.targets.dataset_id
    assert second.folds.dataset_id == first.folds.dataset_id
    global_iterations = 0
    monkeypatch.setattr(
        foundation_runtime,
        "read_provider_history_source_evidence",
        original_source_verifier,
    )
    verified = verify_ibkr_foundation(second_bundle)
    assert global_iterations == 0
    assert verified.readiness.as_json() == second.readiness.as_json()

    receipt_path = checkpoint_root / "source-verification.json"
    assert receipt_path.is_file()
    receipt_path.write_bytes(receipt_path.read_bytes() + b" ")
    tampered_output = tmp_path / "tampered-receipt-foundation.json"
    with pytest.raises(ValueError, match="source-verification checkpoint is not canonical"):
        write_ibkr_foundation(
            tampered_output,
            provider_manifest=provider_manifest,
            configuration=configuration,
            checkpoint_root=checkpoint_root,
        )
    assert not tampered_output.exists()
    assert not tampered_output.with_name(f"{tampered_output.name}.children").exists()

    changed = replace(configuration, name="changed-checkpoint-identity")
    rejected_output = tmp_path / "rejected-foundation.json"
    with pytest.raises(ValueError, match="checkpoint identity does not match"):
        write_ibkr_foundation(
            rejected_output,
            provider_manifest=provider_manifest,
            configuration=changed,
            checkpoint_root=checkpoint_root,
        )
    assert not rejected_output.exists()
    assert not rejected_output.with_name(f"{rejected_output.name}.children").exists()


def test_stage8_post_publication_failure_retains_foundation(
    tmp_path: Path,
) -> None:
    _, _, provider_manifest = _published_provider_history(tmp_path)
    configuration = _config(
        cast(ObservationDataset, SimpleNamespace(dataset_id="0" * 64)),
        start=datetime(2026, 2, 1, tzinfo=UTC),
        end=_SHORT_STAGE8_END,
    )
    output = tmp_path / "retained-foundation.json"

    def fail_after_publication(payload: Mapping[str, object]) -> None:
        if payload.get("phase") == "publication":
            raise RuntimeError("simulated terminal rendering failure")

    with pytest.raises(RuntimeError, match="simulated terminal rendering failure"):
        write_ibkr_foundation(
            output,
            provider_manifest=provider_manifest,
            configuration=configuration,
            progress_callback=fail_after_publication,
        )

    assert output.is_file()
    assert output.with_name(f"{output.name}.children").is_dir()
    assert verify_ibkr_foundation(output).readiness.state is (
        IBKRFoundationReadinessState.INSUFFICIENT_HISTORY_FOR_MODEL_CONCLUSION
    )
