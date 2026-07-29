"""R2.E pooled Ridge recovery, weighting, replay and invariance evidence."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest

from qtrad.application.r2_baselines import build_local_ridge_oof
from qtrad.application.r2_features import feature_schema_for_set
from qtrad.application.r2_pooled import build_pooled_ridge_fold, build_pooled_ridge_oof
from qtrad.application.r2_preprocessing import (
    build_pooled_preprocessing_selection,
    build_r2_preprocessing_selection,
)
from qtrad.application.r2_readiness import R1FoundationBindings, _availability_dataset_id
from qtrad.domain.events import JsonValue
from qtrad.domain.folds import Fold, FoldDataset, membership_hash
from qtrad.domain.foundation import (
    AvailabilityBasis,
    ExcursionDisposition,
    ReturnDisposition,
    TargetDataset,
    TargetRow,
)
from qtrad.domain.market_data import PriceBasis
from qtrad.domain.r2_features import (
    R2FeatureDataset,
    RawFeatureRow,
    RawFeatureValue,
    feature_set_id,
)
from qtrad.domain.r2_models import (
    FitDisposition,
    PreprocessingFeatureKind,
    derive_r2_preprocessing_schema,
)
from qtrad.domain.r2_readiness import FeatureFamily, ModelFamily, R2ExperimentConfig
from qtrad.runtime.r2_fold_fit import serialize_r2_fold_fit, verify_r2_fold_fit
from tests.test_r2_readiness import END, START
from tests.test_r2_readiness import experiment as base_experiment

_APPLICATION_IMAGE = "sha256:r2e-pooled-fixture"
_SKLEARN_IDENTITY = "scikit-learn==1.7.1"


def _pooled_fixture(
    *, context_scale: float = 0.8
) -> tuple[
    R1FoundationBindings,
    dict[str, R2FeatureDataset],
    R2ExperimentConfig,
    Fold,
]:
    base = base_experiment()
    target_rows: list[TargetRow] = []
    training_ids: list[str] = []
    validation_ids: list[str] = []
    signals: dict[tuple[object, str], tuple[float, float]] = {}
    sequence = 1
    for time_index in range(14):
        decision = START + timedelta(minutes=20 * time_index)
        local_signal = float((time_index % 5) - 2)
        context_signal = float((time_index * 3) % 7 - 3)
        for instrument_index, instrument in enumerate(base.target_instruments):
            endpoint = decision + base.primary_horizon
            target = TargetRow(
                instrument_id=instrument,
                decision_time=decision,
                horizon=base.primary_horizon,
                target_basis=PriceBasis.MID,
                target_revision_policy="LATEST_AVAILABLE_BEFORE_FREEZE",
                target_start_time=decision,
                target_end_time=endpoint,
                target_freeze_at=endpoint,
                target_available_at=endpoint,
                label_start_close=Decimal("100"),
                label_end_close=Decimal("101"),
                log_return=(
                    0.15 * local_signal + context_scale * context_signal + 0.05 * instrument_index
                ),
                return_disposition=ReturnDisposition.VALID,
                start_event_id=UUID(int=sequence),
                end_event_id=UUID(int=sequence + 1),
                upper_log_excursion=0.02,
                lower_log_excursion=-0.01,
                excursion_disposition=ExcursionDisposition.VALID,
            )
            sequence += 2
            target_rows.append(target)
            signals[(decision, instrument)] = (local_signal, context_signal)
            (training_ids if time_index < 12 else validation_ids).append(target.target_id)
    targets = TargetDataset.create(
        target_rows,
        observation_dataset_id=base.observation_dataset_id,
        foundation_configuration_id=base.foundation_configuration_id,
    )
    cutoff = START + timedelta(minutes=236)
    validation_start = START + timedelta(minutes=240)
    fold = Fold(
        fold_id="pooled-outer-1",
        training_start=START,
        training_cutoff=cutoff,
        validation_start=validation_start,
        validation_end=START + timedelta(minutes=280),
        embargo_end=validation_start,
        training_target_ids=tuple(training_ids),
        validation_target_ids=tuple(validation_ids),
        holdout_excluded=True,
        membership_hash=membership_hash(training_ids, validation_ids),
    )
    folds = FoldDataset.create(
        (fold,),
        target_dataset_id=targets.dataset_id,
        foundation_configuration_id=base.foundation_configuration_id,
    )
    config = replace(
        base,
        target_dataset_id=targets.dataset_id,
        fold_dataset_id=folds.dataset_id,
        minimum_training_rows=6,
        minimum_inner_validation_rows=2,
        minimum_outer_validation_rows=2,
    )
    datasets = {
        item.name: _feature_dataset(item.name, config, targets, signals)
        for item in config.feature_sets
    }
    active: dict[str, JsonValue] = {
        instrument: [[START.isoformat(), END.isoformat()]]
        for instrument in config.ordered_instruments
    }
    evidence: dict[str, JsonValue] = {
        "availability_delay_report": {},
        "revision_delay_report": {},
        "data_gaps": [],
        "source_active_intervals": active,
        "lineage_summary": {},
        "observation_bounds": {
            "interval_start": START.isoformat(),
            "interval_end": END.isoformat(),
        },
    }
    availability_id = _availability_dataset_id(config.observation_dataset_id, evidence)
    foundation_config = SimpleNamespace(
        configuration_id=config.foundation_configuration_id,
        observation_dataset_id=config.observation_dataset_id,
        ordered_instruments=config.ordered_instruments,
        instrument_roles=config.instrument_roles,
        target_horizons=config.horizons,
        holdout_range=config.holdout_range,
        range_start=START,
        range_end=END,
        availability_basis=AvailabilityBasis.PERSISTED_AT,
    )
    verified = cast(
        R1FoundationBindings,
        SimpleNamespace(
            bundle=SimpleNamespace(
                bundle_id=config.r1_bundle_id,
                ordered_instruments=config.ordered_instruments,
                range_start=START,
                range_end=END,
                configuration=SimpleNamespace(dataset_id=config.foundation_configuration_id),
                observations=SimpleNamespace(dataset_id=config.observation_dataset_id),
                availability=SimpleNamespace(dataset_id=availability_id),
                panel=SimpleNamespace(dataset_id=config.panel_dataset_id),
                targets=SimpleNamespace(dataset_id=targets.dataset_id),
                folds=SimpleNamespace(dataset_id=folds.dataset_id),
                build_summary={
                    "application_version": config.r1_application_version,
                    "image_identity": config.r1_image_identity,
                },
            ),
            configuration=foundation_config,
            observations=SimpleNamespace(
                dataset_id=config.observation_dataset_id,
                selection_policies={"availability_basis": AvailabilityBasis.PERSISTED_AT.value},
            ),
            panel=SimpleNamespace(
                dataset_id=config.panel_dataset_id,
                observation_dataset_id=config.observation_dataset_id,
                foundation_configuration_id=config.foundation_configuration_id,
            ),
            targets=targets,
            folds=folds,
            availability_evidence=evidence,
        ),
    )
    return verified, datasets, config, fold


def _feature_dataset(
    name: str,
    config: R2ExperimentConfig,
    targets: TargetDataset,
    signals: dict[tuple[object, str], tuple[float, float]],
) -> R2FeatureDataset:
    schema = feature_schema_for_set(config, name)
    preprocessing = derive_r2_preprocessing_schema(schema)
    set_id = feature_set_id(config.configuration_id, name, schema)
    rows = []
    for target in targets.rows:
        local_signal, context_signal = signals[(target.decision_time, target.instrument_id)]
        values = []
        local_written = False
        context_written = False
        for definition, transformed in zip(schema, preprocessing.features, strict=True):
            if transformed.kind is PreprocessingFeatureKind.BINARY_INDICATOR:
                value = 1.0
            elif definition.family is FeatureFamily.LOCAL_RETURNS and not local_written:
                value = local_signal
                local_written = True
            elif definition.family is FeatureFamily.POOLED_CROSS_ASSET and not context_written:
                value = context_signal
                context_written = True
            else:
                value = 0.0
            values.append(RawFeatureValue(definition.name, value))
        rows.append(
            RawFeatureRow(
                target.instrument_id,
                target.decision_time,
                target.decision_time,
                target.decision_time,
                set_id,
                tuple(values),
            )
        )
    return R2FeatureDataset.create(
        rows,
        feature_schema=schema,
        feature_set_name=name,
        observation_dataset_id=config.observation_dataset_id,
        panel_dataset_id=config.panel_dataset_id,
        target_dataset_id=targets.dataset_id,
        fold_dataset_id=config.fold_dataset_id,
        experiment_configuration_id=config.configuration_id,
        evidence_class=config.evidence_class,
    )


def _pooled_selection(
    verified: R1FoundationBindings,
    features: R2FeatureDataset,
    config: R2ExperimentConfig,
    fold: Fold,
    family: ModelFamily,
):
    return build_pooled_preprocessing_selection(
        verified,
        features,
        config,
        model_family=family,
        horizon=config.primary_horizon,
        outer_fold_id=fold.fold_id,
        target_instruments=config.target_instruments,
        application_image_identity=_APPLICATION_IMAGE,
        sklearn_library_identity=_SKLEARN_IDENTITY,
    )


def test_pooled_shared_fit_manifests_identity_weights_and_replays() -> None:
    verified, datasets, config, fold = _pooled_fixture()
    selection = _pooled_selection(
        verified, datasets["P0"], config, fold, ModelFamily.POOLED_LOCAL_RIDGE
    )

    result = build_pooled_ridge_fold(verified, datasets["P0"], config, selection)

    assert result.fit.disposition is FitDisposition.READY
    assert result.fit.model_family is ModelFamily.POOLED_LOCAL_RIDGE
    assert result.fit.target_instrument_id == "__POOLED__"
    assert result.fit.intercept == 0.0
    assert tuple(
        name
        for name in result.fit.coefficient_feature_names
        if name.startswith("instrument_identity::")
    ) == tuple(f"instrument_identity::{instrument}" for instrument in config.target_instruments)
    preprocessing = result.fit.preprocessing
    assert preprocessing is not None
    target_by_id = {row.target_id: row for row in verified.targets.rows}
    totals = {
        instrument: sum(
            weight
            for target_id, weight in zip(
                preprocessing.training_target_ids, preprocessing.sample_weights, strict=True
            )
            if target_by_id[target_id].instrument_id == instrument
        )
        for instrument in config.target_instruments
    }
    assert len(set(totals.values())) == 1
    assert len(result.forecasts.rows) == len(config.target_instruments) * 2
    assert (
        verify_r2_fold_fit(
            verified,
            datasets["P0"],
            config,
            selection,
            serialize_r2_fold_fit(result.fit),
        )
        == result.fit
    )


def test_pooled_local_recovers_shared_signal() -> None:
    verified, datasets, config, fold = _pooled_fixture(context_scale=0.0)
    selection = _pooled_selection(
        verified, datasets["P0"], config, fold, ModelFamily.POOLED_LOCAL_RIDGE
    )

    result = build_pooled_ridge_fold(verified, datasets["P0"], config, selection)

    targets = {row.target_id: cast(float, row.log_return) for row in verified.targets.rows}
    model_error = sum(
        (row.expected_return - targets[row.target_id]) ** 2 for row in result.forecasts.rows
    )
    zero_error = sum(targets[row.target_id] ** 2 for row in result.forecasts.rows)
    assert model_error < zero_error * 0.05


def test_pooled_context_recovers_cross_asset_signal_and_reports_common_support() -> None:
    verified, datasets, config, fold = _pooled_fixture()
    local_names = tuple(item.name for item in config.feature_sets if item.name not in {"P0", "P1"})
    local_selections = tuple(
        build_r2_preprocessing_selection(
            verified,
            datasets[name],
            config,
            model_family=ModelFamily.LOCAL_RIDGE,
            horizon=config.primary_horizon,
            outer_fold_id=fold.fold_id,
            target_instruments=(instrument,),
            application_image_identity=_APPLICATION_IMAGE,
            sklearn_library_identity=_SKLEARN_IDENTITY,
        )
        for name in local_names
        for instrument in config.target_instruments
    )
    local = build_local_ridge_oof(
        verified, tuple(datasets[name] for name in local_names), config, local_selections
    )
    pooled_selections = (
        _pooled_selection(verified, datasets["P0"], config, fold, ModelFamily.POOLED_LOCAL_RIDGE),
        _pooled_selection(
            verified,
            datasets["P1"],
            config,
            fold,
            ModelFamily.POOLED_CROSS_ASSET_RIDGE,
        ),
    )

    result = build_pooled_ridge_oof(
        verified,
        (datasets["P1"], datasets["P0"]),
        config,
        pooled_selections,
        local,
    )

    targets = {row.target_id: cast(float, row.log_return) for row in verified.targets.rows}
    forecasts = {child.fit.model_family: child.forecasts.rows for child in result.fold_results}
    p0_error = sum(
        (row.expected_return - targets[row.target_id]) ** 2
        for row in forecasts[ModelFamily.POOLED_LOCAL_RIDGE]
    )
    p1_error = sum(
        (row.expected_return - targets[row.target_id]) ** 2
        for row in forecasts[ModelFamily.POOLED_CROSS_ASSET_RIDGE]
    )
    assert p1_error < p0_error * 0.05
    assert len(result.ablation.common_target_ids) == len(config.target_instruments) * 2
    assert result.ablation.common_target_ids == result.ablation.pooled_context_target_ids


def test_pooled_fit_is_invariant_to_input_row_order() -> None:
    verified, datasets, config, fold = _pooled_fixture()
    original = datasets["P1"]
    reordered = R2FeatureDataset.create(
        tuple(reversed(original.rows)),
        feature_schema=original.feature_schema,
        feature_set_name=original.feature_set_name,
        observation_dataset_id=original.observation_dataset_id,
        panel_dataset_id=original.panel_dataset_id,
        target_dataset_id=original.target_dataset_id,
        fold_dataset_id=original.fold_dataset_id,
        experiment_configuration_id=original.experiment_configuration_id,
        evidence_class=original.evidence_class,
    )
    first_selection = _pooled_selection(
        verified, original, config, fold, ModelFamily.POOLED_CROSS_ASSET_RIDGE
    )
    second_selection = _pooled_selection(
        verified, reordered, config, fold, ModelFamily.POOLED_CROSS_ASSET_RIDGE
    )

    first = build_pooled_ridge_fold(verified, original, config, first_selection)
    second = build_pooled_ridge_fold(verified, reordered, config, second_selection)

    assert original.dataset_id == reordered.dataset_id
    assert first_selection.artifact_id == second_selection.artifact_id
    assert first.fit.artifact_id == second.fit.artifact_id
    assert first.forecasts == second.forecasts


def test_pooled_selection_rejects_partial_or_reordered_target_scope() -> None:
    verified, datasets, config, fold = _pooled_fixture()
    for scope in (config.target_instruments[:-1], tuple(reversed(config.target_instruments))):
        with pytest.raises(ValueError, match="exactly match eligible target order"):
            build_pooled_preprocessing_selection(
                verified,
                datasets["P0"],
                config,
                model_family=ModelFamily.POOLED_LOCAL_RIDGE,
                horizon=config.primary_horizon,
                outer_fold_id=fold.fold_id,
                target_instruments=scope,
                application_image_identity=_APPLICATION_IMAGE,
                sklearn_library_identity=_SKLEARN_IDENTITY,
            )


@pytest.mark.parametrize(
    ("dataset_name", "family"),
    (
        ("P1", ModelFamily.POOLED_LOCAL_RIDGE),
        ("P0", ModelFamily.POOLED_CROSS_ASSET_RIDGE),
    ),
)
def test_pooled_selection_rejects_model_feature_mismatch(
    dataset_name: str, family: ModelFamily
) -> None:
    verified, datasets, config, fold = _pooled_fixture()

    with pytest.raises(ValueError, match=r"requires the P[01] feature set"):
        _pooled_selection(verified, datasets[dataset_name], config, fold, family)
