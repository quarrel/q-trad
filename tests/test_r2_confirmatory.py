"""Fixture-only regressions for confirmatory F2 through unopened G2 preparation."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import fields, replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from shutil import copytree
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest

import qtrad.runtime.foundation_bundle as foundation_runtime
import qtrad.runtime.r2_holdout as holdout_runtime
import qtrad.runtime.r2_verification as verification
from qtrad.__main__ import build_parser
from qtrad.adapters.parquet.observations import ParquetObservationStore
from qtrad.application.r2_features import build_raw_feature_rows, feature_schema_for_set
from qtrad.application.walk_forward import build_expanding_folds, build_zero_return_forecasts
from qtrad.domain.events import JsonValue
from qtrad.domain.folds import FoldDataset
from qtrad.domain.foundation import (
    AvailabilityBasis,
    FoundationConfig,
    PanelDataset,
    PanelRow,
    PanelStatus,
    TargetDataset,
)
from qtrad.domain.foundation import (
    InstrumentRole as FoundationInstrumentRole,
)
from qtrad.domain.market_data import BarProvenance, DataQuality, PriceBasis
from qtrad.domain.r2_evaluation import ConfigurationDisposition, SelectionManifest
from qtrad.domain.r2_features import R2FeatureDataset, feature_set_id
from qtrad.domain.r2_holdout import (
    HoldoutScope,
    R2HoldoutQuestion,
    R2HoldoutSelectionManifest,
    R2HoldoutTargetSource,
    R2OutcomeBlindTargetView,
)
from qtrad.domain.r2_readiness import (
    EvidenceClass,
    FeatureFamily,
    MarketDataSourceClass,
    ModelFamily,
    R2ReadinessReport,
    ReadinessState,
)
from qtrad.domain.research import (
    ObservationDataset,
    ObservationRow,
    build_availability_delay_report,
    build_revision_delay_report,
)
from qtrad.ports.clock import Clock
from qtrad.runtime.r2_bundles import canonical_bytes
from qtrad.runtime.r2_verification import (
    CONFIRMATORY_RUN_KIND,
    VerifiedConfirmatoryF2,
    VerifiedConfirmatoryG1,
    VerifiedConfirmatoryG2Preparation,
    _build_synthetic_oof,
    _materialise_synthetic_feature_manifests,
    _synthetic_pipeline_inputs,
    build_oof_bundle,
    freeze_confirmatory_selection,
    prepare_confirmatory_g2,
    verify_confirmatory_f2,
    verify_confirmatory_g1,
    verify_confirmatory_g2_feature_source,
    verify_confirmatory_g2_preparation,
)


def _build_confirmatory_fixture(
    root: Path,
    *,
    qualifying: bool = False,
) -> tuple[
    Path,
    Any,
    FoldDataset,
    dict[str, tuple[tuple[datetime, datetime], ...]],
]:
    target_names = (
        tuple(f"index:synthetic-{index}" for index in range(6))
        if qualifying
        else ("index:synthetic-a", "index:synthetic-b")
    )
    verified, experiment, datasets = _synthetic_pipeline_inputs(
        target_names=target_names,
        market_groups=(
            {name: f"group-{index // 2}" for index, name in enumerate(target_names)}
            if qualifying
            else None
        ),
        evidence_class=EvidenceClass.CONFIRMATORY,
        include_holdout_target=not qualifying,
        qualifying_confirmatory=qualifying,
        market_data_source_class=(
            MarketDataSourceClass.IG_NATIVE_CAPTURE
            if qualifying
            else MarketDataSourceClass.IBKR_HISTORICAL_RESEARCH
        ),
    )
    fixture_verified = cast(Any, verified)
    start: datetime = (
        cast(datetime, fixture_verified.bundle.range_start)
        if qualifying
        else experiment.holdout_range[0] - timedelta(hours=6)
    )
    foundation_start = start - timedelta(minutes=3)
    active_start = start - experiment.feature_windows[-1] - timedelta(minutes=3)
    active_end = experiment.holdout_range[0]
    if qualifying:
        additional_targets = []
        for instrument_index, instrument in enumerate(experiment.target_instruments):
            template = next(
                row for row in fixture_verified.targets.rows if row.instrument_id == instrument
            )
            for offset in (1, 2):
                decision_time = start - timedelta(minutes=offset)
                target_end_time = decision_time + experiment.primary_horizon
                event_seed = (1 << 127) + instrument_index * 4 + offset * 2
                additional_targets.append(
                    replace(
                        template,
                        decision_time=decision_time,
                        target_start_time=decision_time,
                        target_end_time=target_end_time,
                        target_freeze_at=target_end_time,
                        target_available_at=target_end_time,
                        start_event_id=UUID(int=event_seed),
                        end_event_id=UUID(int=event_seed + 1),
                    )
                )
        fixture_verified.targets = TargetDataset.create(
            (*fixture_verified.targets.rows, *additional_targets),
            observation_dataset_id=fixture_verified.targets.observation_dataset_id,
            foundation_configuration_id=(fixture_verified.targets.foundation_configuration_id),
        )
    observations: list[ObservationRow] = []
    position = 1
    interval_ends = (
        sorted(
            {
                target.decision_time - timedelta(minutes=offset)
                for target in fixture_verified.targets.rows
                for offset in range(6)
            }
            | {active_start + timedelta(minutes=offset) for offset in range(1, 7)}
        )
        if qualifying
        else [
            active_start + timedelta(minutes=offset + 1)
            for offset in range(int((active_end - active_start).total_seconds() // 60))
        ]
    )
    for interval_end in interval_ends:
        current = interval_end - timedelta(minutes=1)
        for instrument in experiment.ordered_instruments:
            close = Decimal("100") + Decimal(position) / Decimal("100")
            observations.append(
                ObservationRow(
                    event_id=UUID(int=position),
                    stream_id=f"market-bar:{instrument}:{PriceBasis.MID}",
                    stream_version=position,
                    event_type="MarketBarClosed",
                    event_time=interval_end,
                    received_at=interval_end,
                    persisted_at=interval_end,
                    global_position=position,
                    instrument_id=instrument,
                    basis=PriceBasis.MID,
                    interval_start=current,
                    interval_end=interval_end,
                    open=close,
                    high=close + Decimal("0.1"),
                    low=close - Decimal("0.1"),
                    close=close,
                    sample_count=1,
                    revision=1,
                    provenance=BarProvenance.QUOTE_DERIVED,
                    quality=DataQuality.HEALTHY,
                    source_provider="fixture",
                    source_environment="test",
                    source_external_id=instrument,
                )
            )
            position += 1
    by_key = {(row.instrument_id, row.interval_end): row for row in observations}
    panels = tuple(
        PanelRow(
            decision_time=target.decision_time,
            instrument_id=target.instrument_id,
            basis=PriceBasis.MID,
            feature_data_asof=target.decision_time,
            latest_feature_bar_end=target.decision_time,
            status=PanelStatus.OBSERVED,
            audit_disposition=None,
            selected_event_id=by_key[(target.instrument_id, target.decision_time)].event_id,
            selected_stream_version=by_key[
                (target.instrument_id, target.decision_time)
            ].stream_version,
            selected_global_position=by_key[
                (target.instrument_id, target.decision_time)
            ].global_position,
            selected_availability_time=target.decision_time,
            selected_revision=1,
            interval_start=by_key[(target.instrument_id, target.decision_time)].interval_start,
            interval_end=target.decision_time,
            open=by_key[(target.instrument_id, target.decision_time)].open,
            high=by_key[(target.instrument_id, target.decision_time)].high,
            low=by_key[(target.instrument_id, target.decision_time)].low,
            close=by_key[(target.instrument_id, target.decision_time)].close,
            sample_count=1,
            quality=DataQuality.HEALTHY,
        )
        for target in fixture_verified.targets.rows
    )
    active_intervals: dict[str, tuple[tuple[datetime, datetime], ...]] = {
        instrument: (
            (
                active_start,
                experiment.holdout_range[1],
            ),
        )
        for instrument in experiment.ordered_instruments
    }
    observation_dataset = ObservationDataset.create(
        observations,
        configuration={
            "universe_name": "confirmatory-fixture",
            "universe_configuration_hash": "2" * 64,
            "ordered_instruments": list(experiment.ordered_instruments),
            "interval_start": active_start.isoformat(),
            "interval_end": experiment.holdout_range[1].isoformat(),
        },
        source_dataset_ids=("1" * 64,),
        selection_policies={
            "provenance": "QUOTE_DERIVED",
            "availability_basis": "persisted_at",
            "canonical_lineage": "GLOBAL_POSITION_EXACT",
        },
    )
    configuration = FoundationConfig(
        name="r2-confirmatory-g2-fixture",
        schema_version=1,
        observation_dataset_id=observation_dataset.dataset_id,
        ordered_instruments=experiment.ordered_instruments,
        instrument_roles={
            instrument: (
                FoundationInstrumentRole.TARGET
                if instrument in experiment.target_instruments
                else FoundationInstrumentRole.CONTEXT
            )
            for instrument in experiment.ordered_instruments
        },
        range_start=foundation_start,
        range_end=experiment.holdout_range[1],
        grid_resolution=timedelta(minutes=1),
        availability_basis=AvailabilityBasis.PERSISTED_AT,
        feature_lag_policy="MEASURED",
        feature_lag_calibration_range=(active_start, foundation_start),
        feature_lag_percentile=0.95,
        feature_lag_safety_margin=timedelta(0),
        selected_feature_lag=timedelta(0),
        target_horizons=(experiment.primary_horizon,),
        primary_vertical_horizon=experiment.primary_horizon,
        target_revision_delay=timedelta(minutes=1),
        target_revision_policy="PROVISIONAL_CONSERVATIVE",
        target_revision_policy_reason="fixture has no observed corrections",
        required_feature_bases=(PriceBasis.MID,),
        target_basis=PriceBasis.MID,
        fold_policy="EXPANDING_WALK_FORWARD",
        holdout_range=experiment.holdout_range,
        embargo=timedelta(minutes=1),
        minimum_training_duration=timedelta(weeks=6) - experiment.primary_horizon,
        minimum_validation_duration=timedelta(weeks=2),
    )
    full_targets = TargetDataset.create(
        fixture_verified.targets.rows,
        observation_dataset_id=observation_dataset.dataset_id,
        foundation_configuration_id=configuration.configuration_id,
    )
    panel_dataset = PanelDataset.create(
        panels,
        observation_dataset_id=observation_dataset.dataset_id,
        foundation_configuration_id=configuration.configuration_id,
    )
    folds = build_expanding_folds(full_targets, configuration)
    forecasts = build_zero_return_forecasts(panel_dataset, full_targets, folds, configuration)
    calibration_start, calibration_end = configuration.feature_lag_calibration_range
    availability_report = build_availability_delay_report(
        observations,
        calibration_start=calibration_start,
        calibration_end=calibration_end,
        configured_percentile=configuration.feature_lag_percentile,
        safety_margin=configuration.feature_lag_safety_margin,
        grid_resolution=configuration.grid_resolution,
    )
    revision_report = build_revision_delay_report(
        observations,
        calibration_start=calibration_start,
        calibration_end=calibration_end,
    )
    availability_evidence: dict[str, JsonValue] = {
        "source_active_intervals": {
            instrument: [[start.isoformat(), end.isoformat()] for start, end in intervals]
            for instrument, intervals in active_intervals.items()
        },
        "data_gaps": [],
        "availability_delay_report": availability_report.as_json(),
        "revision_delay_report": revision_report.as_json(),
        "lineage_summary": {
            "row_count": len(observations),
            "event_type_counts": {"MarketBarClosed": len(observations)},
            "minimum_global_position": min(row.global_position for row in observations),
            "maximum_global_position": max(row.global_position for row in observations),
        },
        "observation_bounds": {
            "interval_start": active_start.isoformat(),
            "interval_end": experiment.holdout_range[1].isoformat(),
        },
    }
    research_root = root / "research"
    research_root.mkdir(parents=True)
    foundation_path = research_root / "foundation.json"

    async def persist_fixture_foundation() -> Any:
        clock = cast(Clock, SimpleNamespace(now=lambda: experiment.holdout_range[0]))
        observation_manifest = await ParquetObservationStore(
            research_root,
            clock,
        ).write_observations(
            observation_dataset,
            application_version="fixture-application",
            image_identity="fixture-image",
            source_snapshot={
                "kind": "verified-capture-snapshot",
                "import_sha256": "1" * 64,
                "universe_name": "confirmatory-fixture",
                "universe_hash": "2" * 64,
            },
            build_evidence=availability_evidence,
        )

        async def accept_fixture_replay(**_: object) -> Any:
            return fixture_verified

        with pytest.MonkeyPatch.context() as setup_patch:
            setup_patch.setattr(
                foundation_runtime,
                "verify_foundation_bundle",
                accept_fixture_replay,
            )
            await foundation_runtime.persist_foundation_bundle(
                root=research_root,
                clock=clock,
                output_path=foundation_path,
                observation_manifest=observation_manifest,
                configuration=configuration,
                observations=observation_dataset,
                panel=panel_dataset,
                targets=full_targets,
                folds=folds,
                forecasts=forecasts,
                availability_evidence=availability_evidence,
                application_version="fixture-application",
                image_identity="fixture-image",
            )
        return foundation_runtime.load_foundation_bundle(foundation_path)

    foundation_bundle = asyncio.run(persist_fixture_foundation())
    fixture_verified = cast(
        Any,
        SimpleNamespace(
            bundle=foundation_bundle,
            configuration=configuration,
            observations=observation_dataset,
            panel=panel_dataset,
            targets=full_targets,
            folds=folds,
            forecasts=forecasts,
            source_active_intervals=active_intervals,
            availability_evidence=availability_evidence,
        ),
    )
    experiment = replace(
        experiment,
        r1_bundle_id=foundation_bundle.bundle_id,
        r1_application_version="fixture-application",
        r1_image_identity="fixture-image",
        foundation_configuration_id=configuration.configuration_id,
        observation_dataset_id=observation_dataset.dataset_id,
        panel_dataset_id=panel_dataset.dataset_id,
        target_dataset_id=full_targets.dataset_id,
        fold_dataset_id=folds.dataset_id,
    )
    foundation = verification._foundation_inputs(fixture_verified)
    datasets = {}
    for feature_set in experiment.feature_sets:
        schema = feature_schema_for_set(experiment, feature_set.name)
        rows = build_raw_feature_rows(
            foundation,
            experiment,
            feature_set_name=feature_set.name,
        )
        datasets[feature_set.name] = R2FeatureDataset.create(
            rows,
            feature_schema=schema,
            feature_set_name=feature_set.name,
            feature_set_id=feature_set_id(
                experiment.configuration_id,
                feature_set.name,
                schema,
                experiment.market_data_source_class,
            ),
            observation_dataset_id=foundation.observations.dataset_id,
            panel_dataset_id=foundation.panel.dataset_id,
            target_dataset_id=foundation.targets.dataset_id,
            fold_dataset_id=foundation.folds.dataset_id,
            experiment_configuration_id=experiment.configuration_id,
            evidence_class=experiment.evidence_class,
            market_data_source_class=experiment.market_data_source_class,
        )
    feature_paths = _materialise_synthetic_feature_manifests(research_root, experiment, datasets)
    experiment_path = root / "experiment.json"
    experiment_path.write_bytes(canonical_bytes(cast(dict[str, object], experiment.as_json())))
    target_source = R2HoldoutTargetSource.create_from_target_dataset(
        full_targets,
        holdout_range=experiment.holdout_range,
        primary_horizon_seconds=int(experiment.primary_horizon.total_seconds()),
        target_instruments=experiment.target_instruments,
        panel=fixture_verified.panel,
        source_active_intervals=active_intervals,
        availability_evidence_id=fixture_verified.bundle.availability.dataset_id,
    )
    fixture_verified.targets = R2OutcomeBlindTargetView.from_source(target_source)
    bundle_path = build_oof_bundle(
        verified=fixture_verified,
        experiment=experiment,
        feature_manifest_paths=feature_paths,
        research_root=research_root,
        clock=cast(Clock, SimpleNamespace(now=lambda: experiment.holdout_range[0])),
        output=root / "oof",
        run_kind=CONFIRMATORY_RUN_KIND,
        representative_profile=(None if qualifying else verification.IBKR_HISTORICAL_PROFILE),
        replay_inputs={
            "foundation": foundation_path,
            "experiment": experiment_path,
            **{name: research_root / path for name, path in feature_paths.items()},
        },
        holdout_target_source=target_source,
    )
    return (
        bundle_path,
        fixture_verified,
        cast(FoldDataset, fixture_verified.folds),
        active_intervals,
    )


def _rebuild_g1_selection(
    selection: R2HoldoutSelectionManifest,
    **changes: object,
) -> R2HoldoutSelectionManifest:
    values = {
        item.name: getattr(selection, item.name)
        for item in fields(selection)
        if item.name != "manifest_id"
    }
    return R2HoldoutSelectionManifest.create(**{**values, **changes})


def test_confirmatory_run_and_evidence_classes_are_strict() -> None:
    verified, implementation, _ = _synthetic_pipeline_inputs()
    with pytest.raises(ValueError, match="CONFIRMATORY OOF runs require CONFIRMATORY evidence"):
        build_oof_bundle(
            verified=verified,
            experiment=implementation,
            feature_manifest_paths={},
            research_root=Path("."),
            clock=cast(Clock, SimpleNamespace(now=lambda: implementation.holdout_range[0])),
            output=Path("unused"),
            run_kind=CONFIRMATORY_RUN_KIND,
        )
    confirmatory_verified, confirmatory, _ = _synthetic_pipeline_inputs(
        evidence_class=EvidenceClass.CONFIRMATORY
    )
    with pytest.raises(ValueError, match="requires the CONFIRMATORY OOF run kind"):
        build_oof_bundle(
            verified=confirmatory_verified,
            experiment=confirmatory,
            feature_manifest_paths={},
            research_root=Path("."),
            clock=cast(Clock, SimpleNamespace(now=lambda: confirmatory.holdout_range[0])),
            output=Path("unused"),
            run_kind="REPRESENTATIVE",
        )


@pytest.mark.parametrize(
    ("command", "tail"),
    (
        ("confirmatory-g1-verify", ("--selection", "selection.json")),
        (
            "confirmatory-g2-prepare",
            ("--selection", "selection.json", "--prepared-by", "fixture", "--output", "prepared"),
        ),
        (
            "confirmatory-g2-preparation-verify",
            ("--selection", "selection.json", "--preparation", "prepared"),
        ),
    ),
)
def test_confirmatory_g2_cli_accepts_only_authority_and_operational_inputs(
    command: str,
    tail: tuple[str, ...],
) -> None:
    parsed = build_parser().parse_args(
        ("research", "baselines", command, "--f2-bundle", "f2", *tail)
    )

    assert parsed.baselines_command == command
    assert not {
        "questions",
        "metric_policy",
        "threshold_policy",
        "feature_set",
        "configuration",
        "alpha_grid",
    }.intersection(vars(parsed))


def test_qualifying_confirmatory_f2_runs_real_oof_replay_and_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identities = {
        "application_identity": "fixture-application",
        "image_identity": "sha256:" + "1" * 64,
        "python_identity": "fixture-python",
        "numpy_identity": "fixture-numpy",
        "sklearn_identity": "fixture-sklearn",
    }
    monkeypatch.setattr(verification, "runtime_identities", lambda: identities)
    bundle_path, _, _, _ = _build_confirmatory_fixture(
        tmp_path,
        qualifying=True,
    )

    authority = verify_confirmatory_f2(bundle_path)
    assert not hasattr(authority, "g2_feature_source_authority")
    assert not hasattr(foundation_runtime, "verify_g2_feature_source")
    with pytest.raises(TypeError, match="requires VerifiedConfirmatoryG1"):
        verify_confirmatory_g2_feature_source(cast(Any, authority))

    assert all(
        row.interval_end <= authority.experiment.holdout_range[0]
        for row in authority.outcome_blind_foundation.observations.rows
    )
    assert all(
        row.decision_time < authority.experiment.holdout_range[0]
        for row in authority.outcome_blind_foundation.panel.rows
    )

    assert authority.readiness_report.confirmatory_data_ready is ReadinessState.READY
    assert authority.readiness_report.inner_validation_rows_ready is ReadinessState.READY
    assert authority.readiness_report.confirmatory_oof_ready is ReadinessState.READY
    assert authority.readiness_report.usable_common_week_count == 16

    selection_path = tmp_path / "selection.json"
    freeze_confirmatory_selection(
        verified_f2=authority,
        output=selection_path,
        frozen_by="fixture-operator",
    )
    verified_g1 = verify_confirmatory_g1(
        verified_f2=authority,
        path=selection_path,
    )
    assert type(verified_g1) is VerifiedConfirmatoryG1
    g2_source = verify_confirmatory_g2_feature_source(verified_g1)
    assert any(
        authority.experiment.holdout_range[0]
        <= row.decision_time
        < authority.experiment.holdout_range[1]
        for row in g2_source.panel.rows
    )
    with pytest.raises(TypeError, match="constructed only by verify_confirmatory_g1"):
        VerifiedConfirmatoryG1()

    changed_policy = _rebuild_g1_selection(
        verified_g1.selection,
        metric_policy={
            **verified_g1.selection.metric_policy,
            "primary_metric": "RMSE",
        },
    )
    changed_selection_path = tmp_path / "changed-selection.json"
    changed_selection_path.write_bytes(
        canonical_bytes(cast(dict[str, object], changed_policy.as_json()))
    )
    with pytest.raises(ValueError, match="differs from independently replayed F2 authority"):
        verify_confirmatory_g1(
            verified_f2=authority,
            path=changed_selection_path,
        )

    def reject_outcome_decode(*_: object, **__: object) -> Any:
        raise AssertionError("C2b-1 must not decode holdout outcomes")

    monkeypatch.setattr(foundation_runtime, "TargetDataset", reject_outcome_decode)
    monkeypatch.setattr(holdout_runtime, "_outcome_evidence_from_payload", reject_outcome_decode)
    monkeypatch.setattr(holdout_runtime, "_outcome_items_from_source", reject_outcome_decode)

    preparation_root = tmp_path / "preparation"
    manifest_path = prepare_confirmatory_g2(
        verified_g1=verified_g1,
        output=preparation_root,
        prepared_by="fixture-operator",
    )
    verified_preparation = verify_confirmatory_g2_preparation(
        verified_g1=verified_g1,
        path=preparation_root,
    )
    with pytest.raises(ValueError, match="unsupported source-child workflow"):
        holdout_runtime.verify_holdout_preparation(preparation_root)
    assert manifest_path == preparation_root / "manifest.json"
    assert type(verified_preparation) is VerifiedConfirmatoryG2Preparation
    assert verified_preparation.seal.holdout_outcomes_accessed is False
    assert verified_preparation.seal.holdout_scope is HoldoutScope.CONFIRMATORY
    assert {item[0] for item in verified_preparation.seal.configuration_feature_dataset_ids} == set(
        verified_g1.selection.holdout_configuration_ids
    )
    persisted_forecasts = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((preparation_root / "forecasts").glob("*.json"))
    ]
    non_zero_ids = {
        configuration_id
        for configuration_id, family, *_ in authority.configuration_registry
        if family is not ModelFamily.ZERO_RETURN
    }
    assert any(
        payload["configuration_id"] in non_zero_ids and payload["rows"]
        for payload in persisted_forecasts
    )
    assert not {
        "opened.json",
        "consumed.json",
        "outcome-evidence.json",
        "outcome-target.json",
        "evaluation.json",
    }.intersection(path.name for path in preparation_root.iterdir())
    with pytest.raises(TypeError, match="constructed only by its verifier"):
        VerifiedConfirmatoryG2Preparation()
    with pytest.raises(AttributeError, match="immutable"):
        cast(Any, verified_preparation).seal = None
    with pytest.raises(FileExistsError):
        prepare_confirmatory_g2(
            verified_g1=verified_g1,
            output=preparation_root,
            prepared_by="fixture-operator",
        )

    repeated_root = tmp_path / "repeated-preparation"
    prepare_confirmatory_g2(
        verified_g1=verified_g1,
        output=repeated_root,
        prepared_by="fixture-operator",
    )
    repeated = verify_confirmatory_g2_preparation(
        verified_g1=verified_g1,
        path=repeated_root,
    )
    assert repeated.seal.seal_id == verified_preparation.seal.seal_id

    missing_root = tmp_path / "missing-preparation"
    copytree(preparation_root, missing_root)
    next((missing_root / "forecasts").iterdir()).unlink()
    with pytest.raises((FileNotFoundError, ValueError)):
        verify_confirmatory_g2_preparation(verified_g1=verified_g1, path=missing_root)

    altered_root = tmp_path / "altered-preparation"
    copytree(preparation_root, altered_root)
    fit_path = next((altered_root / "fits").iterdir())
    fit_payload = cast(dict[str, object], json.loads(fit_path.read_text(encoding="utf-8")))
    fit_payload["fit_id"] = "0" * 64
    fit_path.write_text(json.dumps(fit_payload), encoding="utf-8")
    with pytest.raises(ValueError):
        verify_confirmatory_g2_preparation(verified_g1=verified_g1, path=altered_root)

    extra_root = tmp_path / "extra-preparation"
    copytree(preparation_root, extra_root)
    (extra_root / "unexpected.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="file closure differs"):
        verify_confirmatory_g2_preparation(verified_g1=verified_g1, path=extra_root)

    lifecycle_root = tmp_path / "lifecycle-preparation"
    copytree(preparation_root, lifecycle_root)
    (lifecycle_root / "opened.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="contains post-open lifecycle evidence"):
        verify_confirmatory_g2_preparation(verified_g1=verified_g1, path=lifecycle_root)


def test_confirmatory_f2_is_constructed_by_verifier_and_freezes_without_full_target_decode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        verification,
        "runtime_identities",
        lambda: {
            "application_identity": "fixture-application",
            "image_identity": "sha256:" + "1" * 64,
            "python_identity": "fixture-python",
            "numpy_identity": "fixture-numpy",
            "sklearn_identity": "fixture-sklearn",
        },
    )
    bundle_path, fixture_verified, fixture_folds, active_intervals = _build_confirmatory_fixture(
        tmp_path,
        qualifying=True,
    )
    oof_bundle = verification.verify_r2_oof_bundle(bundle_path)
    assert oof_bundle.holdout_target_source is not None
    target_source = R2HoldoutTargetSource.from_json(
        cast(
            dict[str, object],
            json.loads(
                (bundle_path.parent / oof_bundle.holdout_target_source.path).read_text(
                    encoding="utf-8"
                )
            ),
        )
    )
    blind_foundation = asyncio.run(
        foundation_runtime.verify_outcome_blind_foundation_bundle(
            root=tmp_path / "research",
            bundle_path=tmp_path / "research" / "foundation.json",
            clock=cast(Clock, SimpleNamespace(now=lambda: datetime.now())),
            holdout_target_source=target_source,
        )
    )
    assert blind_foundation.g2_feature_source is not None
    monkeypatch.setattr(
        verification,
        "_replay_confirmatory_oof",
        lambda _: (
            fixture_folds,
            active_intervals,
            fixture_verified,
            blind_foundation.g2_feature_source,
        ),
    )

    def ready_report(**kwargs: object) -> R2ReadinessReport:
        experiment = cast(Any, kwargs["experiment"])
        return R2ReadinessReport(
            experiment_configuration_id=experiment.configuration_id,
            r1_bundle_id=experiment.r1_bundle_id,
            software_contract_ready=ReadinessState.READY,
            representative_integration_ready=ReadinessState.READY,
            confirmatory_data_ready=ReadinessState.READY,
            inner_validation_rows_ready=ReadinessState.PARTIALLY_READY,
            confirmatory_oof_ready=ReadinessState.NOT_READY,
            locked_holdout_ready=ReadinessState.NOT_READY,
            feature_family_states={family: ReadinessState.READY for family in FeatureFamily},
            coverage_matrix={},
            usable_common_week_count=16,
            active_source_duration_seconds={},
            unmet_conditions=(),
            evidence_class=experiment.evidence_class,
            market_data_source_class=experiment.market_data_source_class,
        )

    monkeypatch.setattr(
        verification,
        "evaluate_outcome_blind_confirmatory_readiness",
        lambda **kwargs: replace(
            ready_report(**kwargs),
            confirmatory_data_ready=ReadinessState.NOT_READY,
            unmet_conditions=("fixture readiness gate",),
        ),
    )
    with pytest.raises(ValueError, match="independently replayed outcome-blind readiness"):
        verify_confirmatory_f2(bundle_path)
    monkeypatch.setattr(verification, "evaluate_outcome_blind_confirmatory_readiness", ready_report)

    authority = verify_confirmatory_f2(bundle_path)

    assert type(authority) is VerifiedConfirmatoryF2
    with pytest.raises(TypeError, match="constructed only by verify_confirmatory_f2"):
        VerifiedConfirmatoryF2()
    assert authority.evidence_class is EvidenceClass.CONFIRMATORY
    assert authority.descriptor["run_kind"] == CONFIRMATORY_RUN_KIND
    with pytest.raises(TypeError):
        cast(Any, authority.descriptor)["run_kind"] = "REPRESENTATIVE"
    with pytest.raises(TypeError):
        cast(Any, authority.selection_policy)["primary_metric"] = "RMSE"
    with pytest.raises(TypeError):
        cast(Any, authority.selection_policy["acceptance_thresholds"])[0][0] = "tampered"
    with pytest.raises(TypeError):
        cast(Any, authority.experiment.acceptance_thresholds)["tampered"] = 0.0
    with pytest.raises(TypeError):
        cast(Any, authority.readiness_report.feature_family_states)[FeatureFamily.LOCAL_RETURNS] = (
            ReadinessState.NOT_READY
        )

    with pytest.raises(ValueError, match="require verify_confirmatory_f2"):
        verification.verify_oof_bundle(bundle_path)

    def reject_full_target_decoder(_: Path) -> dict[str, object]:
        raise AssertionError("confirmatory G1 decoded a holdout-bearing child")

    monkeypatch.setattr(verification, "_load_selection", reject_full_target_decoder)
    selection_path = tmp_path / "selection.json"
    captured_freeze: dict[str, Any] = {}
    shared_freeze = verification.freeze_holdout_selection

    def capture_freeze(**kwargs: Any) -> R2HoldoutSelectionManifest:
        captured_freeze.update(kwargs)
        return shared_freeze(**kwargs)

    monkeypatch.setattr(verification, "freeze_holdout_selection", capture_freeze)
    freeze_confirmatory_selection(
        verified_f2=authority,
        output=selection_path,
        frozen_by="fixture-operator",
    )
    selection = json.loads(selection_path.read_bytes())
    assert selection["holdout_scope"] == HoldoutScope.CONFIRMATORY.value
    assert selection["evidence_class"] == EvidenceClass.CONFIRMATORY.value
    assert selection["holdout_outcomes_accessed"] is False
    assert selection["questions"][0]["support_policy"] == "COMMON_ELIGIBLE"
    assert selection["questions"][0]["metric"] == "INSTRUMENT_BALANCED_COMMON_SUPPORT_MSE"
    family_by_configuration = {item[0]: item[1] for item in selection["configuration_registry"]}
    assert [
        (
            family_by_configuration[item["candidate_configuration_id"]],
            family_by_configuration[item["comparator_configuration_id"]],
        )
        for item in selection["questions"]
    ] == [
        ("LOCAL_RIDGE", "ZERO_RETURN"),
        ("POOLED_LOCAL_RIDGE", "LOCAL_RIDGE"),
        ("POOLED_CROSS_ASSET_RIDGE", "POOLED_LOCAL_RIDGE"),
    ]
    pooled_local_id = next(
        configuration_id
        for configuration_id, family in family_by_configuration.items()
        if family == "POOLED_LOCAL_RIDGE"
    )
    assert pooled_local_id not in selection["selected_configuration_ids"]
    assert selection["questions"][2]["comparator_configuration_id"] == pooled_local_id

    configuration_ids_by_family = {
        family: configuration_id for configuration_id, family in family_by_configuration.items()
    }
    frozen_questions = cast(tuple[R2HoldoutQuestion, ...], captured_freeze["questions"])
    source_question = frozen_questions[-1]
    invented_question = R2HoldoutQuestion.create(
        question="invented P1 versus zero comparison",
        candidate_configuration_id=configuration_ids_by_family["POOLED_CROSS_ASSET_RIDGE"],
        comparator_configuration_id=configuration_ids_by_family["ZERO_RETURN"],
        metric=source_question.metric,
        direction=source_question.direction,
        threshold=source_question.threshold,
        minimum_support=source_question.minimum_support,
        minimum_coverage=source_question.minimum_coverage,
        support_policy=source_question.support_policy,
        conclusion_policy=source_question.conclusion_policy,
    )
    with pytest.raises(
        ValueError,
        match="confirmatory question is not an authenticated immediate comparison",
    ):
        cast(Any, shared_freeze)(**{**captured_freeze, "questions": (invented_question,)})
    tampered_evaluation_policy = dict(
        cast(Mapping[str, JsonValue], captured_freeze["evaluation_policy"])
    )
    tampered_evaluation_policy["comparison_registry"] = [
        *cast(list[JsonValue], tampered_evaluation_policy["comparison_registry"]),
        ["POOLED_CROSS_ASSET_RIDGE", "ZERO_RETURN"],
    ]
    with pytest.raises(
        ValueError,
        match="evaluation policy differs from verifier-only confirmatory authority",
    ):
        cast(Any, shared_freeze)(
            **{
                **captured_freeze,
                "questions": (invented_question,),
                "evaluation_policy": tampered_evaluation_policy,
            }
        )
    tampered_configuration_registry = tuple(
        (
            configuration_id,
            (ModelFamily.ZERO_RETURN if family is ModelFamily.POOLED_CROSS_ASSET_RIDGE else family),
            feature_set_id,
            feature_dataset_id,
            manifest_id,
        )
        for (
            configuration_id,
            family,
            feature_set_id,
            feature_dataset_id,
            manifest_id,
        ) in cast(
            tuple[
                tuple[str, ModelFamily, str | None, str | None, str | None],
                ...,
            ],
            captured_freeze["configuration_registry"],
        )
    )
    with pytest.raises(
        ValueError,
        match="configuration registry differs from verifier-only confirmatory authority",
    ):
        cast(Any, shared_freeze)(
            **{
                **captured_freeze,
                "configuration_registry": tampered_configuration_registry,
            }
        )
    prior_selection = cast(SelectionManifest, captured_freeze["prior_selection"])

    def rebuild_prior_selection(**overrides: Any) -> SelectionManifest:
        values: dict[str, Any] = {
            "experiment_configuration_id": prior_selection.experiment_configuration_id,
            "evidence_class": prior_selection.evidence_class,
            "evaluation_report_id": prior_selection.evaluation_report_id,
            "local_comparator_manifest_id": prior_selection.local_comparator_manifest_id,
            "evaluated_configuration_ids": prior_selection.evaluated_configuration_ids,
            "predeclared_comparators": prior_selection.predeclared_comparators,
            "primary_metric": prior_selection.primary_metric,
            "secondary_metrics": prior_selection.secondary_metrics,
            "acceptance_thresholds": prior_selection.acceptance_thresholds,
            "decisions": prior_selection.decisions,
            "selected_configuration_ids": prior_selection.selected_configuration_ids,
            "holdout_comparator_configuration_ids": (
                prior_selection.holdout_comparator_configuration_ids
            ),
            "final_fitting_procedure": prior_selection.final_fitting_procedure,
            "holdout_range": prior_selection.holdout_range,
            "application_image_identity": prior_selection.application_image_identity,
            "frozen_at": prior_selection.frozen_at,
            "frozen_by": prior_selection.frozen_by,
            "market_data_source_class": prior_selection.market_data_source_class,
            "foundation_bundle_id": prior_selection.foundation_bundle_id,
            "oof_bundle_id": prior_selection.oof_bundle_id,
        }
        values.update(overrides)
        return cast(Any, SelectionManifest.create)(**values)

    promoted_decisions = tuple(
        (
            replace(decision, disposition=ConfigurationDisposition.SELECTED_CANDIDATE)
            if decision.configuration_id == pooled_local_id
            else decision
        )
        for decision in prior_selection.decisions
    )
    promoted_selection = rebuild_prior_selection(
        decisions=promoted_decisions,
        selected_configuration_ids=tuple(
            sorted((*prior_selection.selected_configuration_ids, pooled_local_id))
        ),
    )
    with pytest.raises(
        ValueError,
        match="prior selection differs from verifier-only confirmatory authority",
    ):
        cast(Any, shared_freeze)(**{**captured_freeze, "prior_selection": promoted_selection})

    changed_metric_selection = rebuild_prior_selection(primary_metric="RMSE")
    with pytest.raises(
        ValueError,
        match="prior selection differs from verifier-only confirmatory authority",
    ):
        cast(Any, shared_freeze)(**{**captured_freeze, "prior_selection": changed_metric_selection})

    threshold_name, threshold_value = prior_selection.acceptance_thresholds[0]
    changed_threshold_selection = rebuild_prior_selection(
        acceptance_thresholds=(
            (threshold_name, threshold_value + 0.01),
            *prior_selection.acceptance_thresholds[1:],
        )
    )
    with pytest.raises(
        ValueError,
        match="prior selection differs from verifier-only confirmatory authority",
    ):
        cast(Any, shared_freeze)(
            **{**captured_freeze, "prior_selection": changed_threshold_selection}
        )

    assert (
        selection["final_fitting_policy"]["pooled_membership_policy"]
        == "FIXED_UNIVERSE_OUTER_INNER_FIT_VALIDATION_V1"
    )
    assert selection["final_fitting_policy"]["instrument_intercept_policy"] == (
        "NO_GLOBAL_INTERCEPT_V1"
    )
    assert selection["final_fitting_policy"]["runtime_identities"][
        "instrument_identity_order"
    ] == list(authority.experiment.target_instruments)

    with pytest.raises(FileExistsError):
        freeze_confirmatory_selection(
            verified_f2=authority,
            output=selection_path,
            frozen_by="fixture-operator",
        )


def test_confirmatory_verifier_rejects_implementation_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        verification,
        "runtime_identities",
        lambda: {
            "application_identity": "fixture-application",
            "image_identity": "sha256:" + "1" * 64,
            "python_identity": "fixture-python",
            "numpy_identity": "fixture-numpy",
            "sklearn_identity": "fixture-sklearn",
        },
    )
    implementation = _build_synthetic_oof(tmp_path / "implementation" / "oof")
    with pytest.raises(ValueError, match="CONFIRMATORY evidence"):
        verify_confirmatory_f2(implementation)
