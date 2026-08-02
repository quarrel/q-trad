"""Authenticated R2.E pooled non-graph Ridge controls and ablation support."""

from collections.abc import Sequence
from dataclasses import dataclass

from qtrad.application.r2_baselines import (
    LocalRidgeOofResult,
    RidgeFoldResult,
    build_coefficient_stability_summary,
    fit_ridge,
    forecast_validation,
    preprocessing_selections_match,
    validation_targets_for_instrument,
    verify_coefficient_stability_summary,
    verify_local_ridge_forecast_coverage,
    verify_ridge_forecast_coverage,
)
from qtrad.application.r2_features import feature_schema_for_set
from qtrad.application.r2_preprocessing import (
    build_pooled_preprocessing_selection,
    join_training_rows,
)
from qtrad.application.r2_readiness import R1FoundationBindings, verify_exact_r1_bindings
from qtrad.domain.forecasts import ForecastDataset
from qtrad.domain.r2_baselines import CoefficientStabilitySummary
from qtrad.domain.r2_features import R2FeatureDataset, feature_set_id
from qtrad.domain.r2_models import R2PreprocessingSelection
from qtrad.domain.r2_readiness import FeatureFamily, ModelFamily, R2ExperimentConfig


@dataclass(frozen=True, slots=True)
class PooledAblationReport:
    """Exact own- and common-support lineage for local, P0 and P1 evaluation."""

    local_fold_fit_ids: tuple[str, ...]
    pooled_local_fold_fit_ids: tuple[str, ...]
    pooled_context_fold_fit_ids: tuple[str, ...]
    local_target_ids: tuple[str, ...]
    pooled_local_target_ids: tuple[str, ...]
    pooled_context_target_ids: tuple[str, ...]
    common_target_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        groups = (
            self.local_fold_fit_ids,
            self.pooled_local_fold_fit_ids,
            self.pooled_context_fold_fit_ids,
        )
        if any(not group or tuple(sorted(set(group))) != group for group in groups):
            raise ValueError("ablation report requires unique ordered fit identities")
        supports = (
            self.local_target_ids,
            self.pooled_local_target_ids,
            self.pooled_context_target_ids,
        )
        if any(tuple(sorted(set(group))) != group for group in supports):
            raise ValueError("ablation report supports must be unique and ordered")
        expected_common = tuple(
            sorted(
                set(self.local_target_ids)
                & set(self.pooled_local_target_ids)
                & set(self.pooled_context_target_ids)
            )
        )
        if self.common_target_ids != expected_common:
            raise ValueError("ablation common support does not reconcile to own supports")


@dataclass(frozen=True, slots=True)
class PooledRidgeOofResult:
    """Complete P0/P1 primary-horizon pooled controls over authenticated outer folds."""

    fold_results: tuple[RidgeFoldResult, ...]
    forecasts: ForecastDataset
    coefficient_stability: CoefficientStabilitySummary
    ablation: PooledAblationReport

    def __post_init__(self) -> None:
        if not self.fold_results:
            raise ValueError("pooled OOF result requires fold results")
        fit_ids = tuple(sorted(result.fit.artifact_id for result in self.fold_results))
        if fit_ids != self.coefficient_stability.fold_fit_ids:
            raise ValueError("pooled OOF stability does not bind every fold fit")
        verify_coefficient_stability_summary(
            self.coefficient_stability, tuple(result.fit for result in self.fold_results)
        )
        child_forecasts = {
            row.forecast_id for result in self.fold_results for row in result.forecasts.rows
        }
        if child_forecasts != {row.forecast_id for row in self.forecasts.rows}:
            raise ValueError("pooled OOF forecasts do not reconcile to fold children")
        pooled_local = tuple(
            result
            for result in self.fold_results
            if result.fit.model_family is ModelFamily.POOLED_LOCAL_RIDGE
        )
        pooled_context = tuple(
            result
            for result in self.fold_results
            if result.fit.model_family is ModelFamily.POOLED_CROSS_ASSET_RIDGE
        )
        if (
            self.ablation.pooled_local_fold_fit_ids
            != tuple(sorted(result.fit.artifact_id for result in pooled_local))
            or self.ablation.pooled_context_fold_fit_ids
            != tuple(sorted(result.fit.artifact_id for result in pooled_context))
            or self.ablation.pooled_local_target_ids != _target_support(pooled_local)
            or self.ablation.pooled_context_target_ids != _target_support(pooled_context)
        ):
            raise ValueError("pooled ablation lineage differs from pooled fold results")


def build_pooled_ridge_oof(
    verified: R1FoundationBindings,
    feature_datasets: Sequence[R2FeatureDataset],
    experiment: R2ExperimentConfig,
    selections: Sequence[R2PreprocessingSelection],
    local_result: LocalRidgeOofResult,
    local_comparator_feature_dataset: R2FeatureDataset,
    *,
    application_image_identity: str,
    numpy_library_identity: str,
    sklearn_library_identity: str,
) -> PooledRidgeOofResult:
    """Build P0 and P1 and bind their common support to the matching local comparator."""

    datasets = {dataset.feature_set_name: dataset for dataset in feature_datasets}
    if len(datasets) != len(feature_datasets) or set(datasets) != {"P0", "P1"}:
        raise ValueError("pooled OOF requires exactly one P0 and one P1 feature dataset")
    local_comparator_name = _ablation_definition(experiment)
    local_comparator_id = feature_set_id(
        experiment.configuration_id,
        local_comparator_name,
        feature_schema_for_set(experiment, local_comparator_name),
        experiment.market_data_source_class,
    )
    local_children = _verify_local_comparator(
        verified,
        local_result,
        local_comparator_feature_dataset,
        experiment,
        local_comparator_id,
    )
    family_by_name = {
        "P0": ModelFamily.POOLED_LOCAL_RIDGE,
        "P1": ModelFamily.POOLED_CROSS_ASSET_RIDGE,
    }
    expected_keys = {
        (family_by_name[name], fold.fold_id) for name in datasets for fold in verified.folds.folds
    }
    selection_by_key = {
        (selection.model_family, selection.outer_fold_id): selection for selection in selections
    }
    if len(selection_by_key) != len(selections) or set(selection_by_key) != expected_keys:
        raise ValueError("pooled selections do not exactly cover P0/P1 outer-fold scope")
    results = tuple(
        build_pooled_ridge_fold(
            verified,
            datasets["P0" if family is ModelFamily.POOLED_LOCAL_RIDGE else "P1"],
            experiment,
            selection_by_key[(family, fold_id)],
            application_image_identity=application_image_identity,
            numpy_library_identity=numpy_library_identity,
            sklearn_library_identity=sklearn_library_identity,
        )
        for family, fold_id in sorted(expected_keys, key=lambda item: (item[0].value, item[1]))
    )
    forecasts = ForecastDataset.create(
        tuple(row for result in results for row in result.forecasts.rows),
        observation_dataset_id=verified.observations.dataset_id,
        panel_dataset_id=verified.panel.dataset_id,
        target_dataset_id=verified.targets.dataset_id,
        fold_dataset_id=verified.folds.dataset_id,
    )
    return PooledRidgeOofResult(
        results,
        forecasts,
        build_coefficient_stability_summary(tuple(result.fit for result in results)),
        _build_ablation_report(
            local_children,
            results,
            local_comparator_id,
        ),
    )


def build_pooled_ridge_fold(
    verified: R1FoundationBindings,
    feature_dataset: R2FeatureDataset,
    experiment: R2ExperimentConfig,
    selection: R2PreprocessingSelection,
    *,
    application_image_identity: str,
    numpy_library_identity: str,
    sklearn_library_identity: str,
) -> RidgeFoldResult:
    """Fit one shared pooled model and forecast every eligible validation target."""

    verify_exact_r1_bindings(verified, experiment)
    if selection.model_family not in (
        ModelFamily.POOLED_LOCAL_RIDGE,
        ModelFamily.POOLED_CROSS_ASSET_RIDGE,
    ):
        raise ValueError("R2.E pooled fitting requires a pooled Ridge selection")
    rebuilt = build_pooled_preprocessing_selection(
        verified,
        feature_dataset,
        experiment,
        model_family=selection.model_family,
        horizon=selection.horizon,
        outer_fold_id=selection.outer_fold_id,
        target_instruments=selection.target_instruments,
        application_image_identity=selection.application_image_identity,
        sklearn_library_identity=selection.sklearn_library_identity,
    )
    if not preprocessing_selections_match(
        selection,
        rebuilt,
        relative_tolerance=experiment.numeric_replay_relative_tolerance,
        absolute_tolerance=experiment.numeric_replay_absolute_tolerance,
    ):
        raise ValueError("R2.E selection differs from its authenticated rebuild")
    if selection.target_instruments != experiment.target_instruments:
        raise ValueError("pooled fit target order differs from the declared eligible universe")
    fold = next(item for item in verified.folds.folds if item.fold_id == selection.outer_fold_id)
    training_rows = join_training_rows(
        verified.targets,
        fold,
        feature_dataset,
        experiment,
        selection.target_instruments,
        selection.horizon,
    )
    validation_targets = tuple(
        target
        for instrument in selection.target_instruments
        for target in validation_targets_for_instrument(
            verified,
            experiment,
            outer_fold_id=selection.outer_fold_id,
            target_instrument_id=instrument,
            horizon=selection.horizon,
        )
    )
    validation_targets = tuple(
        sorted(
            validation_targets,
            key=lambda item: (item.decision_time, item.instrument_id, item.target_id),
        )
    )
    fit, model = fit_ridge(
        feature_dataset,
        experiment,
        selection,
        fold.training_cutoff,
        len(fold.training_target_ids) - len(training_rows),
        len(validation_targets),
        training_rows,
        application_image_identity,
        numpy_library_identity,
        sklearn_library_identity,
    )
    forecasts, coverage = forecast_validation(
        verified,
        feature_dataset,
        experiment,
        fit,
        model,
        validation_targets,
    )
    result = RidgeFoldResult(fit, forecasts, coverage)
    verify_ridge_forecast_coverage(
        verified,
        feature_dataset,
        experiment,
        result,
        target_instruments=selection.target_instruments,
    )
    return result


def _ablation_definition(experiment: R2ExperimentConfig) -> str:
    by_name = {feature_set.name: feature_set.families for feature_set in experiment.feature_sets}
    if "P0" not in by_name or "P1" not in by_name:
        raise ValueError("experiment must declare P0 and P1 feature sets")
    p0, p1 = by_name["P0"], by_name["P1"]
    if FeatureFamily.POOLED_CROSS_ASSET in p0 or p1 != (*p0, FeatureFamily.POOLED_CROSS_ASSET):
        raise ValueError("P1 must add only pooled cross-asset context to P0")
    local_matches = tuple(
        name for name, families in by_name.items() if name not in {"P0", "P1"} and families == p0
    )
    if len(local_matches) != 1:
        raise ValueError("P0 must match exactly one declared local comparator feature set")
    return local_matches[0]


def _verify_local_comparator(
    verified: R1FoundationBindings,
    local_result: LocalRidgeOofResult,
    local_feature_dataset: R2FeatureDataset,
    experiment: R2ExperimentConfig,
    local_feature_set_id: str,
) -> tuple[RidgeFoldResult, ...]:
    local_children = tuple(
        result
        for result in local_result.fold_results
        if result.fit.model_family is ModelFamily.LOCAL_RIDGE
        and result.fit.feature_set_id == local_feature_set_id
    )
    expected_keys = {
        (local_feature_set_id, instrument, fold.fold_id, experiment.primary_horizon)
        for instrument in experiment.target_instruments
        for fold in verified.folds.folds
    }
    actual_keys = {
        (
            result.fit.feature_set_id,
            result.fit.target_instrument_id,
            result.fit.outer_fold_id,
            result.fit.horizon,
        )
        for result in local_children
    }
    if len(local_children) != len(actual_keys) or actual_keys != expected_keys:
        raise ValueError("local comparator does not exactly cover target and outer-fold scope")
    for result in local_children:
        verify_local_ridge_forecast_coverage(verified, local_feature_dataset, experiment, result)
    return tuple(
        sorted(
            local_children,
            key=lambda result: (
                result.fit.target_instrument_id,
                result.fit.outer_fold_id,
                result.fit.artifact_id,
            ),
        )
    )


def _build_ablation_report(
    local_children: Sequence[RidgeFoldResult],
    pooled_results: Sequence[RidgeFoldResult],
    local_feature_set_id: str,
) -> PooledAblationReport:
    if any(result.fit.feature_set_id != local_feature_set_id for result in local_children):
        raise ValueError("local comparator feature-set binding differs from P0")
    pooled_local = tuple(
        result
        for result in pooled_results
        if result.fit.model_family is ModelFamily.POOLED_LOCAL_RIDGE
    )
    pooled_context = tuple(
        result
        for result in pooled_results
        if result.fit.model_family is ModelFamily.POOLED_CROSS_ASSET_RIDGE
    )
    return PooledAblationReport(
        tuple(sorted(result.fit.artifact_id for result in local_children)),
        tuple(sorted(result.fit.artifact_id for result in pooled_local)),
        tuple(sorted(result.fit.artifact_id for result in pooled_context)),
        _target_support(local_children),
        _target_support(pooled_local),
        _target_support(pooled_context),
        tuple(
            sorted(
                set(_target_support(local_children))
                & set(_target_support(pooled_local))
                & set(_target_support(pooled_context))
            )
        ),
    )


def _target_support(results: Sequence[RidgeFoldResult]) -> tuple[str, ...]:
    return tuple(sorted({row.target_id for result in results for row in result.forecasts.rows}))
