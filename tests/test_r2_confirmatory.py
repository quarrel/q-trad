"""Fixture-only regressions for confirmatory F2 through unopened G2 preparation."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from shutil import copytree
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest

import qtrad.application.r2_holdout as holdout_application
import qtrad.runtime.foundation_bundle as foundation_runtime
import qtrad.runtime.ibkr_foundation as ibkr_foundation_runtime
import qtrad.runtime.r2_holdout as holdout_runtime
import qtrad.runtime.r2_verification as verification
from qtrad.__main__ import build_parser
from qtrad.adapters.parquet.foundation import ParquetFoundationArtifactStore
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
    HOLDOUT_ACKNOWLEDGEMENT,
    HoldoutScope,
    R2HoldoutQuestion,
    R2HoldoutSelectionManifest,
    R2HoldoutTargetSource,
    R2OutcomeBlindTargetView,
)
from qtrad.domain.r2_ibkr_historical import IBKRHistoricalAdapterIdentity
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
    AuthenticatedR2Foundation,
    ConfirmatoryR2HStatus,
    OpenedConfirmatoryHoldout,
    VerifiedConfirmatoryF2,
    VerifiedConfirmatoryG1,
    VerifiedConfirmatoryG2Preparation,
    _build_synthetic_oof,
    _materialise_synthetic_feature_manifests,
    _synthetic_pipeline_inputs,
    build_oof_bundle,
    freeze_confirmatory_selection,
    prepare_confirmatory_g2,
    reveal_confirmatory_g2,
    verify_confirmatory_f2,
    verify_confirmatory_g1,
    verify_confirmatory_g2_feature_source,
    verify_confirmatory_g2_preparation,
    verify_confirmatory_r2h,
)


def _fixture_blind_foundation(
    root: Path,
) -> tuple[
    Path,
    foundation_runtime.OutcomeBlindVerifiedFoundationBundle,
    foundation_runtime.G2FeatureSourceAuthority,
]:
    with pytest.MonkeyPatch.context() as fixture_patch:
        fixture_patch.setattr(
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
        bundle_path, _, _, _, _ = _build_confirmatory_fixture(
            root,
            qualifying=True,
            compact=True,
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
            root=root / "research",
            bundle_path=root / "research" / "foundation.json",
            clock=cast(Clock, SimpleNamespace(now=lambda: datetime.now(UTC))),
            receipt=root / "research" / "foundation-receipt.json",
            holdout_target_source=target_source,
        )
    )
    assert blind_foundation.g2_feature_source is not None
    return (
        root / "research",
        blind_foundation,
        blind_foundation.g2_feature_source,
    )


def _symlink_foundation_child(
    research_root: Path,
    manifest_id: str,
    outside_path: Path,
) -> None:
    clock = cast(Clock, SimpleNamespace(now=lambda: datetime.now(UTC)))
    manifest = asyncio.run(
        ParquetFoundationArtifactStore(research_root, clock).read_manifest(manifest_id)
    )
    child_path = research_root / manifest.file
    outside_path.write_bytes(child_path.read_bytes())
    child_path.unlink()
    child_path.symlink_to(outside_path)


@pytest.mark.parametrize("reference_name", ("observation_reference", "panel_reference"))
def test_g2_feature_source_rejects_same_byte_symlink_escape(
    tmp_path: Path,
    reference_name: str,
) -> None:
    research_root, _, authority = _fixture_blind_foundation(tmp_path)
    reference = getattr(authority, reference_name)
    _symlink_foundation_child(
        research_root,
        reference.manifest_id,
        tmp_path / f"outside-{reference_name}.parquet",
    )

    with pytest.raises(ValueError, match="symlink"):
        asyncio.run(
            foundation_runtime._verify_g2_feature_source(
                authority,
                clock=cast(Clock, SimpleNamespace(now=lambda: datetime.now(UTC))),
            )
        )


def test_confirmatory_target_rejects_same_byte_symlink_escape(tmp_path: Path) -> None:
    research_root, blind_foundation, authority = _fixture_blind_foundation(tmp_path)
    reference = blind_foundation.bundle.targets
    _symlink_foundation_child(
        research_root,
        reference.manifest_id,
        tmp_path / "outside-targets.parquet",
    )

    with pytest.raises(ValueError, match="symlink"):
        asyncio.run(
            foundation_runtime._verify_confirmatory_target_dataset(
                blind_foundation,
                authority,
                clock=cast(Clock, SimpleNamespace(now=lambda: datetime.now(UTC))),
            )
        )


def _build_confirmatory_fixture(
    root: Path,
    *,
    qualifying: bool = False,
    compact: bool = False,
    replay_foundation_path: Path | None = None,
    foundation_receipt_path: Path | None = None,
    foundation_promotion_path: Path | None = None,
    market_data_source_class: MarketDataSourceClass | None = None,
) -> tuple[
    Path,
    Any,
    TargetDataset,
    FoldDataset,
    dict[str, tuple[tuple[datetime, datetime], ...]],
]:
    qualifying_shape = qualifying and not compact
    source_class = market_data_source_class or (
        MarketDataSourceClass.IG_NATIVE_CAPTURE
        if qualifying
        else MarketDataSourceClass.IBKR_HISTORICAL_RESEARCH
    )
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
        include_holdout_target=compact or not qualifying,
        qualifying_confirmatory=qualifying_shape,
        market_data_source_class=source_class,
    )
    fixture_verified = cast(Any, verified)
    # Keep the qualifying selection hierarchy without its 16-week readiness shape.
    if compact:
        decision_order = {
            decision: index
            for index, decision in enumerate(
                sorted({row.decision_time for row in fixture_verified.targets.rows})
            )
        }
        instrument_order = {
            instrument: index for index, instrument in enumerate(experiment.target_instruments)
        }
        fixture_verified.targets = TargetDataset.create(
            tuple(
                replace(
                    row,
                    log_return=0.01
                    * (decision_order[row.decision_time] + 1)
                    * (instrument_order[row.instrument_id] + 1),
                )
                for row in fixture_verified.targets.rows
            ),
            observation_dataset_id=fixture_verified.targets.observation_dataset_id,
            foundation_configuration_id=fixture_verified.targets.foundation_configuration_id,
        )
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
        minimum_training_duration=(
            timedelta(minutes=75) if compact else timedelta(weeks=6) - experiment.primary_horizon
        ),
        minimum_validation_duration=(timedelta(minutes=30) if compact else timedelta(weeks=2)),
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
    research_root.mkdir(parents=True, exist_ok=True)
    foundation_path = research_root / (
        "fixture-foundation.json" if replay_foundation_path is not None else "foundation.json"
    )

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
    local_foundation_receipt_path = foundation_receipt_path
    if local_foundation_receipt_path is None:
        local_foundation_receipt_path = research_root / "foundation-receipt.json"
        receipt = foundation_runtime._foundation_receipt_for(
            foundation_path,
            foundation_bundle,
        )
        foundation_runtime.write_foundation_verification_receipt(
            local_foundation_receipt_path,
            receipt,
        )
    if replay_foundation_path is not None:
        foundation_bundle = type(foundation_bundle).create(
            configuration=foundation_bundle.configuration,
            observations=foundation_bundle.observations,
            availability=foundation_bundle.availability,
            panel=foundation_bundle.panel,
            targets=foundation_bundle.targets,
            folds=foundation_bundle.folds,
            forecasts=foundation_bundle.forecasts,
            ordered_instruments=foundation_bundle.ordered_instruments,
            range_start=foundation_bundle.range_start,
            range_end=foundation_bundle.range_end,
            coverage=foundation_bundle.coverage,
            build_summary=foundation_bundle.build_summary,
            market_data_source_class=experiment.market_data_source_class,
        )
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
        r1_bundle_id=foundation_bundle.foundation_id,
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
    foundation_path_for_authority = replay_foundation_path or foundation_path
    receipt_payload = cast(
        dict[str, object], json.loads(local_foundation_receipt_path.read_bytes())
    )
    promotion_id: str | None = None
    promotion_path = foundation_promotion_path
    closure_id = foundation_bundle.closure_id
    verification_id = receipt_payload.get("verification_id")
    if not isinstance(verification_id, str):
        raise ValueError("fixture receipt has no verification identity")
    if foundation_promotion_path is not None:
        promotion_payload = cast(
            dict[str, object], json.loads(foundation_promotion_path.read_bytes())
        )
        promotion_value = promotion_payload.get("promotion_sha256")
        if (
            not isinstance(promotion_value, str)
            or len(promotion_value) != 64
            or any(character not in "0123456789abcdef" for character in promotion_value)
        ):
            raise ValueError("fixture promotion has no promotion identity")
        promotion_id = promotion_value
    foundation_authority = AuthenticatedR2Foundation(
        foundation_id=foundation_bundle.foundation_id,
        closure_id=closure_id,
        verification_id=verification_id,
        source_class=experiment.market_data_source_class,
        evidence_class=experiment.evidence_class,
        semantic_inputs=verification._foundation_inputs(fixture_verified),
        bundle_path=foundation_path_for_authority,
        receipt_path=local_foundation_receipt_path,
        promotion_id=promotion_id,
        promotion_path=promotion_path,
    )
    bundle_path = build_oof_bundle(
        verified=fixture_verified,
        foundation_authority=foundation_authority,
        experiment=experiment,
        feature_manifest_paths=feature_paths,
        research_root=research_root,
        clock=cast(Clock, SimpleNamespace(now=lambda: experiment.holdout_range[0])),
        output=root / "oof",
        run_kind=CONFIRMATORY_RUN_KIND,
        representative_profile=(
            verification.IBKR_HISTORICAL_PROFILE
            if source_class is MarketDataSourceClass.IBKR_HISTORICAL_RESEARCH
            else None
        ),
        holdout_target_source=target_source,
        experiment_path=experiment_path,
    )
    return (
        bundle_path,
        fixture_verified,
        full_targets,
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
        (
            "confirmatory-g2-reveal",
            (
                "--selection",
                "selection.json",
                "--preparation",
                "prepared",
                "--expected-selection-id",
                "selection-id",
                "--expected-seal-id",
                "seal-id",
                "--acknowledgement",
                HOLDOUT_ACKNOWLEDGEMENT,
                "--opened-by",
                "fixture",
                "--consumed-by",
                "fixture",
            ),
        ),
        (
            "confirmatory-r2h-verify",
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
    bundle_path, _, fixture_targets, _, _ = _build_confirmatory_fixture(
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

    real_access = object.__getattribute__(verified_g1, "_g2_feature_access")
    forged_access = object.__new__(type(real_access))
    for field in ("_authority", "_selection_manifest_id", "_verified_f2"):
        object.__setattr__(
            forged_access,
            field,
            object.__getattribute__(real_access, field),
        )
    forged_g1 = object.__new__(VerifiedConfirmatoryG1)
    object.__setattr__(forged_g1, "_verified_f2", verified_g1.verified_f2)
    object.__setattr__(forged_g1, "_selection", verified_g1.selection)
    object.__setattr__(forged_g1, "_g2_feature_access", forged_access)
    with pytest.raises(TypeError, match="requires verified G1 provenance"):
        verify_confirmatory_g2_feature_source(forged_g1)

    object.__setattr__(
        forged_g1,
        "_verifier_provenance",
        object.__getattribute__(verified_g1, "_verifier_provenance"),
    )
    with pytest.raises(TypeError, match="requires verified G1 feature access"):
        verify_confirmatory_g2_feature_source(forged_g1)

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

    original_target_dataset = foundation_runtime.TargetDataset
    original_outcome_evidence = holdout_runtime._outcome_evidence_from_payload
    original_outcome_items = holdout_runtime._outcome_items_from_source

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

    def unexpected_g2_rebuild(**_: object) -> None:
        raise AssertionError("completed preparation must fail before rebuilding G2")

    with monkeypatch.context() as context:
        context.setattr(verification, "_build_confirmatory_g2", unexpected_g2_rebuild)
        with pytest.raises(FileExistsError):
            prepare_confirmatory_g2(
                verified_g1=verified_g1,
                output=preparation_root,
                prepared_by="fixture-operator",
            )

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

    def unexpected_child_replay(*_: object, **__: object) -> None:
        raise AssertionError("invalid closure must fail before numerical replay")

    with monkeypatch.context() as context:
        context.setattr(holdout_runtime, "_verify_prepare_children", unexpected_child_replay)
        with pytest.raises(ValueError, match="file closure differs"):
            verify_confirmatory_g2_preparation(verified_g1=verified_g1, path=extra_root)

    lifecycle_root = tmp_path / "lifecycle-preparation"
    copytree(preparation_root, lifecycle_root)
    (lifecycle_root / "opened.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="contains post-open lifecycle evidence"):
        verify_confirmatory_g2_preparation(verified_g1=verified_g1, path=lifecycle_root)

    monkeypatch.setattr(foundation_runtime, "TargetDataset", original_target_dataset)
    monkeypatch.setattr(
        holdout_runtime,
        "_outcome_evidence_from_payload",
        original_outcome_evidence,
    )
    monkeypatch.setattr(
        holdout_runtime,
        "_outcome_items_from_source",
        original_outcome_items,
    )

    # Failure cases mutate only lifecycle files; preparation bytes were verified above.
    def copied_verified_preparation(
        name: str,
    ) -> tuple[Path, VerifiedConfirmatoryG2Preparation]:
        root = tmp_path / name
        copytree(preparation_root, root)
        copied = object.__new__(VerifiedConfirmatoryG2Preparation)
        for attribute in VerifiedConfirmatoryG2Preparation.__slots__:
            object.__setattr__(
                copied,
                attribute,
                object.__getattribute__(verified_preparation, attribute),
            )
        object.__setattr__(copied, "_path", root)
        return root, copied

    real_holdout_verifier = holdout_runtime.verify_holdout_preparation
    real_lifecycle_verifier = verification._verify_confirmatory_holdout_preparation
    real_g2_builder = verification._build_confirmatory_g2
    real_decoder = verification._decode_confirmatory_target

    def verified_seal(*_: object, **__: object) -> Any:
        return verified_preparation.seal

    def replayed_g2(**_: object) -> Any:
        return SimpleNamespace(seal=verified_preparation.seal)

    def fixture_target_after_open(opened: object) -> TargetDataset:
        path = cast(Any, opened).preparation.path
        assert (path / "opened.json").is_file()
        assert (path / "confirmatory-opened.json").is_file()
        return fixture_targets

    monkeypatch.setattr(holdout_runtime, "verify_holdout_preparation", verified_seal)
    monkeypatch.setattr(verification, "_verify_confirmatory_holdout_preparation", verified_seal)
    monkeypatch.setattr(verification, "_build_confirmatory_g2", replayed_g2)
    monkeypatch.setattr(verification, "_decode_confirmatory_target", fixture_target_after_open)

    def terminal_clock(opened_at: datetime, *, seconds: int = 1) -> Clock:
        return cast(
            Clock,
            SimpleNamespace(now=lambda: opened_at + timedelta(seconds=seconds)),
        )

    base_marker_failure_root, base_marker_failure_preparation = copied_verified_preparation(
        "base-marker-failure"
    )
    claim_before = (base_marker_failure_root / ".preparation-claim.json").read_bytes()
    original_base_json_writer = cast(Any, holdout_runtime)._write_json

    def fail_base_opened(path: Path, payload: object) -> None:
        if path.name == "opened.json":
            raise RuntimeError("injected base OPENED failure")
        original_base_json_writer(path, payload)

    monkeypatch.setattr(holdout_runtime, "_write_json", fail_base_opened)
    base_opened_at = datetime.now(UTC)
    with pytest.raises(RuntimeError, match="injected base OPENED failure"):
        reveal_confirmatory_g2(
            preparation=base_marker_failure_preparation,
            expected_selection_manifest_id=verified_g1.selection.manifest_id,
            expected_seal_id=base_marker_failure_preparation.seal.seal_id,
            acknowledgement=HOLDOUT_ACKNOWLEDGEMENT,
            opened_by="fixture-operator",
            consumed_by="fixture-operator",
            opened_at=base_opened_at,
            clock=terminal_clock(base_opened_at),
        )
    assert (base_marker_failure_root / ".preparation-claim.json").read_bytes() == claim_before
    assert not (base_marker_failure_root / "opened.json").exists()
    assert not (base_marker_failure_root / "consumed.json").exists()
    monkeypatch.setattr(holdout_runtime, "_write_json", original_base_json_writer)

    marker_failure_root, marker_failure_preparation = copied_verified_preparation("marker-failure")
    original_verification_atomic_create = verification.atomic_create

    def fail_confirmatory_opened(path: Path, data: bytes) -> None:
        if path.name == "confirmatory-opened.json":
            raise RuntimeError("injected confirmatory OPENED failure")
        original_verification_atomic_create(path, data)

    monkeypatch.setattr(verification, "atomic_create", fail_confirmatory_opened)
    marker_opened_at = datetime.now(UTC)
    with pytest.raises(RuntimeError, match="injected confirmatory OPENED failure"):
        reveal_confirmatory_g2(
            preparation=marker_failure_preparation,
            expected_selection_manifest_id=verified_g1.selection.manifest_id,
            expected_seal_id=marker_failure_preparation.seal.seal_id,
            acknowledgement=HOLDOUT_ACKNOWLEDGEMENT,
            opened_by="fixture-operator",
            consumed_by="fixture-operator",
            opened_at=marker_opened_at,
            clock=terminal_clock(marker_opened_at),
        )
    assert (marker_failure_root / "opened.json").is_file()
    assert not (marker_failure_root / "confirmatory-opened.json").exists()
    assert (marker_failure_root / "consumed.json").is_file()
    assert (
        verify_confirmatory_r2h(
            verified_g1=verified_g1,
            path=marker_failure_root,
        ).status
        is ConfirmatoryR2HStatus.OPENED_INCOMPLETE
    )
    monkeypatch.setattr(verification, "atomic_create", original_verification_atomic_create)

    failure_root, failed_preparation = copied_verified_preparation("failed-reveal")
    original_decoder = verification._decode_confirmatory_target

    def fail_after_open(opened: object) -> TargetDataset:
        assert (failure_root / "opened.json").is_file()
        assert (failure_root / "confirmatory-opened.json").is_file()
        original_decoder(cast(Any, opened))
        raise RuntimeError("injected post-OPEN failure")

    monkeypatch.setattr(verification, "_decode_confirmatory_target", fail_after_open)
    failed_opened_at = datetime.now(UTC)
    with pytest.raises(RuntimeError, match="injected post-OPEN failure"):
        reveal_confirmatory_g2(
            preparation=failed_preparation,
            expected_selection_manifest_id=verified_g1.selection.manifest_id,
            expected_seal_id=failed_preparation.seal.seal_id,
            acknowledgement=HOLDOUT_ACKNOWLEDGEMENT,
            opened_by="fixture-operator",
            consumed_by="fixture-operator",
            opened_at=failed_opened_at,
            clock=terminal_clock(failed_opened_at),
        )
    assert (failure_root / "opened.json").is_file()
    assert (failure_root / "confirmatory-opened.json").is_file()
    assert (failure_root / "consumed.json").is_file()
    assert not (failure_root / "evaluation.json").exists()
    failed_report = verify_confirmatory_r2h(
        verified_g1=verified_g1,
        path=failure_root,
    )
    assert failed_report.status is ConfirmatoryR2HStatus.OPENED_INCOMPLETE
    with pytest.raises((FileExistsError, ValueError)):
        reveal_confirmatory_g2(
            preparation=failed_preparation,
            expected_selection_manifest_id=verified_g1.selection.manifest_id,
            expected_seal_id=failed_preparation.seal.seal_id,
            acknowledgement=HOLDOUT_ACKNOWLEDGEMENT,
            opened_by="fixture-operator",
            consumed_by="fixture-operator",
            opened_at=failed_opened_at,
            clock=terminal_clock(failed_opened_at, seconds=2),
        )

    monkeypatch.setattr(verification, "_decode_confirmatory_target", original_decoder)

    evaluation_failure_root, evaluation_failure_preparation = copied_verified_preparation(
        "evaluation-failure"
    )
    original_evaluator = holdout_application.evaluate_holdout

    def fail_during_evaluation(*_: object, **__: object) -> object:
        raise RuntimeError("injected evaluation failure")

    monkeypatch.setattr(holdout_application, "evaluate_holdout", fail_during_evaluation)
    evaluation_opened_at = datetime.now(UTC)
    with pytest.raises(RuntimeError, match="injected evaluation failure"):
        reveal_confirmatory_g2(
            preparation=evaluation_failure_preparation,
            expected_selection_manifest_id=verified_g1.selection.manifest_id,
            expected_seal_id=evaluation_failure_preparation.seal.seal_id,
            acknowledgement=HOLDOUT_ACKNOWLEDGEMENT,
            opened_by="fixture-operator",
            consumed_by="fixture-operator",
            opened_at=evaluation_opened_at,
            clock=terminal_clock(evaluation_opened_at),
        )
    assert (
        verify_confirmatory_r2h(
            verified_g1=verified_g1,
            path=evaluation_failure_root,
        ).status
        is ConfirmatoryR2HStatus.OPENED_INCOMPLETE
    )
    monkeypatch.setattr(holdout_application, "evaluate_holdout", original_evaluator)

    result_failure_root, result_failure_preparation = copied_verified_preparation(
        "result-persistence-failure"
    )
    original_atomic_create = holdout_runtime.atomic_create

    def fail_evaluation_persistence(path: Path, payload: bytes) -> None:
        if path.name == "evaluation.json":
            raise RuntimeError("injected result persistence failure")
        original_atomic_create(path, payload)

    monkeypatch.setattr(holdout_runtime, "atomic_create", fail_evaluation_persistence)
    result_opened_at = datetime.now(UTC)
    with pytest.raises(RuntimeError, match="injected result persistence failure"):
        reveal_confirmatory_g2(
            preparation=result_failure_preparation,
            expected_selection_manifest_id=verified_g1.selection.manifest_id,
            expected_seal_id=result_failure_preparation.seal.seal_id,
            acknowledgement=HOLDOUT_ACKNOWLEDGEMENT,
            opened_by="fixture-operator",
            consumed_by="fixture-operator",
            opened_at=result_opened_at,
            clock=terminal_clock(result_opened_at),
        )
    assert not (result_failure_root / "evaluation.json").exists()
    assert (result_failure_root / "consumed.json").is_file()
    assert (
        verify_confirmatory_r2h(
            verified_g1=verified_g1,
            path=result_failure_root,
        ).status
        is ConfirmatoryR2HStatus.OPENED_INCOMPLETE
    )
    monkeypatch.setattr(holdout_runtime, "atomic_create", original_atomic_create)

    consumed_failure_root, consumed_failure_preparation = copied_verified_preparation(
        "consumed-failure"
    )
    original_json_writer = cast(Any, holdout_runtime)._write_json

    def fail_before_consumed(path: Path, payload: object) -> None:
        if path.name == "consumed.json":
            raise RuntimeError("injected CONSUMED failure")
        original_json_writer(path, payload)

    monkeypatch.setattr(holdout_runtime, "_write_json", fail_before_consumed)
    consumed_opened_at = datetime.now(UTC)
    with pytest.raises(RuntimeError, match="injected CONSUMED failure"):
        reveal_confirmatory_g2(
            preparation=consumed_failure_preparation,
            expected_selection_manifest_id=verified_g1.selection.manifest_id,
            expected_seal_id=consumed_failure_preparation.seal.seal_id,
            acknowledgement=HOLDOUT_ACKNOWLEDGEMENT,
            opened_by="fixture-operator",
            consumed_by="fixture-operator",
            opened_at=consumed_opened_at,
            clock=terminal_clock(consumed_opened_at),
        )
    assert (consumed_failure_root / "evaluation.json").is_file()
    assert not (consumed_failure_root / "consumed.json").exists()
    assert (
        verify_confirmatory_r2h(
            verified_g1=verified_g1,
            path=consumed_failure_root,
        ).status
        is ConfirmatoryR2HStatus.OPENED_INCOMPLETE
    )
    monkeypatch.setattr(
        holdout_runtime,
        "_write_json",
        original_json_writer,
    )
    monkeypatch.setattr(holdout_runtime, "verify_holdout_preparation", real_holdout_verifier)
    monkeypatch.setattr(
        verification,
        "_verify_confirmatory_holdout_preparation",
        real_lifecycle_verifier,
    )
    monkeypatch.setattr(verification, "_build_confirmatory_g2", real_g2_builder)
    monkeypatch.setattr(verification, "_decode_confirmatory_target", real_decoder)

    with pytest.raises(TypeError, match="constructed only after durable OPENED"):
        OpenedConfirmatoryHoldout()
    opened_at = datetime.now(UTC)
    evaluation, consumed = reveal_confirmatory_g2(
        preparation=verified_preparation,
        expected_selection_manifest_id=verified_g1.selection.manifest_id,
        expected_seal_id=verified_preparation.seal.seal_id,
        acknowledgement=HOLDOUT_ACKNOWLEDGEMENT,
        opened_by="fixture-operator",
        consumed_by="fixture-operator",
        opened_at=opened_at,
        clock=terminal_clock(opened_at),
    )
    assert evaluation is not None
    assert consumed.evaluation_id == evaluation.evaluation_id
    assert {
        "opened.json",
        "confirmatory-opened.json",
        "outcome-target.json",
        "outcome-evidence.json",
        "evaluation.json",
        "consumed.json",
    }.issubset(path.name for path in preparation_root.iterdir())
    tampered_root = tmp_path / "tampered-r2h"
    copytree(preparation_root, tampered_root)
    evaluation_payload = cast(
        dict[str, object],
        json.loads((tampered_root / "evaluation.json").read_bytes()),
    )
    evaluation_payload["evaluation_id"] = "0" * 64
    (tampered_root / "evaluation.json").write_bytes(canonical_bytes(evaluation_payload))
    # Reveal verified the preparation; isolate evaluation tamper classification.
    with monkeypatch.context() as tamper_context:
        tamper_context.setattr(
            verification,
            "_verify_confirmatory_holdout_preparation",
            lambda *args, **kwargs: verified_preparation.seal,
        )
        tamper_context.setattr(
            verification,
            "_build_confirmatory_g2",
            lambda **kwargs: verified_preparation,
        )
        assert (
            verify_confirmatory_r2h(
                verified_g1=verified_g1,
                path=tampered_root,
            ).status
            is ConfirmatoryR2HStatus.INVALID
        )

    with pytest.raises(ValueError, match=r"owned and unopened|post-open lifecycle evidence"):
        verify_confirmatory_g2_preparation(
            verified_g1=verified_g1,
            path=preparation_root,
        )


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
    bundle_path, fixture_verified, _, fixture_folds, active_intervals = _build_confirmatory_fixture(
        tmp_path,
        qualifying=True,
        compact=True,
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
            receipt=tmp_path / "research" / "foundation-receipt.json",
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
            "oof_id": prior_selection.oof_id,
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


def test_ibkr_authority_replay_uses_only_immediate_parent_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identities = {
        "application_identity": "fixture-application",
        "image_identity": "sha256:" + "1" * 64,
        "python_identity": "fixture-python",
        "numpy_identity": "fixture-numpy",
        "sklearn_identity": "fixture-sklearn",
    }
    call_counts = {"feature_recompute": 0, "local_fit": 0, "pooled_fit": 0}
    original_verify_rows = verification.verify_raw_feature_rows
    original_local_fit = verification.build_local_ridge_oof
    original_pooled_fit = verification.build_pooled_ridge_oof

    def spy_verify_rows(*args: Any, **kwargs: Any) -> int:
        call_counts["feature_recompute"] += 1
        return original_verify_rows(*args, **kwargs)

    def spy_local_fit(*args: Any, **kwargs: Any) -> Any:
        call_counts["local_fit"] += 1
        return original_local_fit(*args, **kwargs)

    def spy_pooled_fit(*args: Any, **kwargs: Any) -> Any:
        call_counts["pooled_fit"] += 1
        return original_pooled_fit(*args, **kwargs)

    monkeypatch.setattr(verification, "runtime_identities", lambda: identities)
    monkeypatch.setattr(verification, "verify_raw_feature_rows", spy_verify_rows)
    monkeypatch.setattr(verification, "build_local_ridge_oof", spy_local_fit)
    monkeypatch.setattr(verification, "build_pooled_ridge_oof", spy_pooled_fit)
    replay_foundation_path = tmp_path / "replay-foundation.json"
    promotion_path = tmp_path / "promotion.json"
    promotion_path.write_bytes(canonical_bytes({"promotion_sha256": "c" * 64}))
    bundle_path, fixture_verified, _, _, _ = _build_confirmatory_fixture(
        tmp_path,
        qualifying=True,
        market_data_source_class=MarketDataSourceClass.IBKR_HISTORICAL_RESEARCH,
        replay_foundation_path=replay_foundation_path,
        foundation_promotion_path=promotion_path,
    )
    replay_foundation_path.write_bytes(
        (tmp_path / "research" / "fixture-foundation.json").read_bytes()
    )
    assert call_counts == {"feature_recompute": 4, "local_fit": 1, "pooled_fit": 1}

    experiment_path = tmp_path / "experiment.json"
    experiment_payload = cast(dict[str, object], json.loads(experiment_path.read_bytes()))
    adapter = IBKRHistoricalAdapterIdentity.create(
        foundation_bundle_id=fixture_verified.bundle.foundation_id,
        application_identity=identities["application_identity"],
        image_identity=identities["image_identity"],
    )
    experiment_payload["source_adapter_identity"] = adapter.as_json()
    experiment_path.write_bytes(canonical_bytes(experiment_payload))

    oof_bundle = verification.verify_r2_oof_bundle(bundle_path)
    descriptor = verification._oof_child_payload(
        bundle_path, oof_bundle, verification.OOF_DESCRIPTOR_CONTRACT
    )
    raw_authority = cast(dict[str, object], descriptor["foundation_authority"])
    authority = AuthenticatedR2Foundation(
        foundation_id=cast(str, raw_authority["foundation_id"]),
        closure_id=cast(str, raw_authority["closure_id"]),
        verification_id=cast(str, raw_authority["verification_id"]),
        source_class=MarketDataSourceClass.IBKR_HISTORICAL_RESEARCH,
        evidence_class=EvidenceClass.CONFIRMATORY,
        semantic_inputs=verification._foundation_inputs(fixture_verified),
        promotion_id=(
            None
            if raw_authority["promotion_id"] is None
            else cast(str, raw_authority["promotion_id"])
        ),
        bundle_path=replay_foundation_path,
        receipt_path=tmp_path / "research" / "foundation-receipt.json",
        promotion_path=promotion_path,
    )
    monkeypatch.setattr(verification, "authenticate_ibkr_foundation_for_r2", lambda **_: authority)

    parent_calls = {"semantic": 0, "folds": 0, "copies": 0}

    def reject_parent_semantic(*args: Any, **kwargs: Any) -> Any:
        parent_calls["semantic"] += 1
        raise AssertionError("R2 replay invoked a parent semantic verifier")

    def reject_parent_folds(*args: Any, **kwargs: Any) -> Any:
        parent_calls["folds"] += 1
        raise AssertionError("R2 replay rebuilt parent folds")

    def reject_parent_copy(*args: Any, **kwargs: Any) -> Any:
        parent_calls["copies"] += 1
        raise AssertionError("R2 replay copied parent files")

    with monkeypatch.context() as replay_patch:
        for module, name in (
            (foundation_runtime, "verify_foundation_bundle"),
            (foundation_runtime, "_verify_foundation_bundle"),
            (foundation_runtime, "verify_outcome_blind_foundation_bundle"),
            (foundation_runtime, "build_asof_panel"),
            (foundation_runtime, "build_frozen_targets"),
            (ibkr_foundation_runtime, "verify_ibkr_foundation"),
            (ibkr_foundation_runtime, "_verify_ibkr_foundation_v3"),
            (ibkr_foundation_runtime, "build_ibkr_foundation"),
        ):
            replay_patch.setattr(module, name, reject_parent_semantic)
        for module in (foundation_runtime, ibkr_foundation_runtime):
            replay_patch.setattr(module, "build_expanding_folds", reject_parent_folds)
        replay_patch.setattr(verification, "_copy_tree", reject_parent_copy)
        replay_patch.setattr(verification, "_copy_file", reject_parent_copy)
        asyncio.run(
            verification._replay_authority_oof_async(
                bundle_path, expected_run_kind=CONFIRMATORY_RUN_KIND
            )
        )

    assert parent_calls == {"semantic": 0, "folds": 0, "copies": 0}
    assert call_counts == {"feature_recompute": 8, "local_fit": 2, "pooled_fit": 2}
    assert not (bundle_path.parent / "replay-inputs").exists()
