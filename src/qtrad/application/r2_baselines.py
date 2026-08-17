"""Authenticated R2 Ridge fitting, forecasting and numerical replay."""

import warnings
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isclose, isfinite
from typing import Any, cast

import numpy as np
from sklearn.linear_model import Ridge  # type: ignore[reportMissingTypeStubs]

from qtrad.application.r2_preprocessing import (
    TrainingRow,
    add_instrument_identity,
    build_r2_preprocessing_selection,
    join_training_rows,
    transform,
)
from qtrad.application.r2_readiness import R1FoundationBindings, verify_exact_r1_bindings
from qtrad.domain.forecasts import ForecastDataset, ForecastRow, ReturnUnit
from qtrad.domain.foundation import ReturnDisposition, TargetRow
from qtrad.domain.r2_baselines import (
    CoefficientStabilityRow,
    CoefficientStabilitySummary,
    FoldFitDiagnostics,
    ForecastCoverageDataset,
    ForecastCoverageDisposition,
    ForecastCoverageRow,
    R2FoldFit,
    coverage_disposition_for_fit,
)
from qtrad.domain.r2_features import R2FeatureDataset, RawFeatureRow
from qtrad.domain.r2_models import FitDisposition, R2PreprocessingSelection
from qtrad.domain.r2_readiness import FeatureFamily, ModelFamily, R2ExperimentConfig


@dataclass(frozen=True, slots=True)
class PredictionRow:
    target_id: str
    target_instrument_id: str
    features: tuple[float | None, ...]


@dataclass(frozen=True, slots=True)
class RidgeFoldResult:
    fit: R2FoldFit
    forecasts: ForecastDataset
    coverage: ForecastCoverageDataset

    def __post_init__(self) -> None:
        if (
            self.forecasts.target_dataset_id != self.fit.target_dataset_id
            or self.forecasts.fold_dataset_id != self.fit.fold_dataset_id
            or self.coverage.target_dataset_id != self.fit.target_dataset_id
            or self.coverage.fold_dataset_id != self.fit.fold_dataset_id
            or self.coverage.r2_feature_dataset_id != self.fit.r2_feature_dataset_id
            or self.coverage.experiment_configuration_id != self.fit.experiment_configuration_id
        ):
            raise ValueError("Ridge result child lineage differs from its fold fit")
        forecast_ids = {row.forecast_id for row in self.forecasts.rows}
        covered_ids = {
            row.forecast_id
            for row in self.coverage.rows
            if row.disposition is ForecastCoverageDisposition.FORECASTED
        }
        if forecast_ids != covered_ids or None in covered_ids:
            raise ValueError("Ridge forecast and coverage identities do not reconcile")
        if any(row.model_id != self.fit.artifact_id for row in self.forecasts.rows):
            raise ValueError("Ridge forecasts do not bind the fold-fit artifact")
        if any(row.fold_fit_id != self.fit.artifact_id for row in self.coverage.rows):
            raise ValueError("Ridge coverage does not bind the fold-fit artifact")


LocalRidgeFoldResult = RidgeFoldResult


@dataclass(frozen=True, slots=True)
class LocalRidgeOofResult:
    """Complete primary-horizon local-feature ablation over authenticated outer folds."""

    fold_results: tuple[LocalRidgeFoldResult, ...]
    forecasts: ForecastDataset
    coefficient_stability: CoefficientStabilitySummary

    def __post_init__(self) -> None:
        if not self.fold_results:
            raise ValueError("local OOF result requires fold results")
        fit_ids = tuple(sorted(result.fit.artifact_id for result in self.fold_results))
        if fit_ids != self.coefficient_stability.fold_fit_ids:
            raise ValueError("local OOF stability does not bind every fold fit")
        child_forecast_ids = {
            row.forecast_id for result in self.fold_results for row in result.forecasts.rows
        }
        if child_forecast_ids != {row.forecast_id for row in self.forecasts.rows}:
            raise ValueError("local OOF forecasts do not reconcile to fold children")
        verify_coefficient_stability_summary(
            self.coefficient_stability, tuple(result.fit for result in self.fold_results)
        )


def build_local_ridge_oof(
    verified: R1FoundationBindings,
    feature_datasets: Sequence[R2FeatureDataset],
    experiment: R2ExperimentConfig,
    selections: Sequence[R2PreprocessingSelection],
    *,
    application_image_identity: str,
    numpy_library_identity: str,
    sklearn_library_identity: str,
) -> LocalRidgeOofResult:
    """Build the exact local-feature ablation ladder for every eligible target and outer fold."""

    datasets = {dataset.feature_set_id: dataset for dataset in feature_datasets}
    if len(datasets) != len(feature_datasets):
        raise ValueError("local OOF feature datasets contain duplicate feature-set identities")
    declared_local_sets = tuple(
        item
        for item in experiment.feature_sets
        if item.name not in {"P0", "P1"} and FeatureFamily.POOLED_CROSS_ASSET not in item.families
    )
    if {dataset.feature_set_name for dataset in feature_datasets} != {
        item.name for item in declared_local_sets
    }:
        raise ValueError("local OOF feature datasets do not cover the declared ablation ladder")
    expected_keys = {
        (dataset.feature_set_id, instrument, fold.fold_id, experiment.primary_horizon)
        for dataset in feature_datasets
        for instrument in experiment.target_instruments
        for fold in verified.folds.folds
    }
    selection_by_key = {
        (
            selection.feature_set_id,
            selection.target_instruments[0],
            selection.outer_fold_id,
            selection.horizon,
        ): selection
        for selection in selections
    }
    if len(selection_by_key) != len(selections) or set(selection_by_key) != expected_keys:
        raise ValueError("local OOF selections do not exactly cover the declared ablation scope")
    if set(datasets) != {key[0] for key in expected_keys}:
        raise ValueError(
            "local OOF feature datasets do not exactly cover declared local feature sets"
        )
    results = tuple(
        build_local_ridge_fold(
            verified,
            datasets[key[0]],
            experiment,
            selection_by_key[key],
            application_image_identity=application_image_identity,
            numpy_library_identity=numpy_library_identity,
            sklearn_library_identity=sklearn_library_identity,
        )
        for key in sorted(expected_keys, key=lambda item: (item[0], item[1], item[2], item[3]))
    )
    forecasts = ForecastDataset.create(
        tuple(row for result in results for row in result.forecasts.rows),
        observation_dataset_id=verified.observations.dataset_id,
        panel_dataset_id=verified.panel.dataset_id,
        target_dataset_id=verified.targets.dataset_id,
        fold_dataset_id=verified.folds.dataset_id,
    )
    return LocalRidgeOofResult(
        results,
        forecasts,
        build_coefficient_stability_summary(tuple(result.fit for result in results)),
    )


def build_local_ridge_fold(
    verified: R1FoundationBindings,
    feature_dataset: R2FeatureDataset,
    experiment: R2ExperimentConfig,
    selection: R2PreprocessingSelection,
    *,
    application_image_identity: str,
    numpy_library_identity: str,
    sklearn_library_identity: str,
) -> LocalRidgeFoldResult:
    """Fit one authenticated final local model and forecast its outer validation membership."""

    verify_exact_r1_bindings(verified, experiment)
    _verify_selection_rebuild(verified, feature_dataset, experiment, selection)
    fold = next(item for item in verified.folds.folds if item.fold_id == selection.outer_fold_id)
    target_instrument_id = selection.target_instruments[0]
    training_rows = join_training_rows(
        verified.targets,
        fold,
        feature_dataset,
        experiment,
        selection.target_instruments,
        selection.horizon,
    )
    validation_targets = validation_targets_for_instrument(
        verified,
        experiment,
        outer_fold_id=selection.outer_fold_id,
        target_instrument_id=target_instrument_id,
        horizon=selection.horizon,
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
    result = LocalRidgeFoldResult(fit, forecasts, coverage)
    verify_local_ridge_forecast_coverage(verified, feature_dataset, experiment, result)
    return result


def verify_ridge_forecast_coverage(
    verified: R1FoundationBindings,
    feature_dataset: R2FeatureDataset,
    experiment: R2ExperimentConfig,
    result: RidgeFoldResult,
    *,
    target_instruments: Sequence[str],
) -> None:
    """Authenticate complete outer membership and every forecast/coverage lineage field."""

    verify_exact_r1_bindings(verified, experiment)
    fit = result.fit
    scope = tuple(target_instruments)
    if fit.model_family is ModelFamily.LOCAL_RIDGE:
        if scope != (fit.target_instrument_id,):
            raise ValueError("local coverage scope differs from its fold fit")
    elif fit.target_instrument_id != "__POOLED__" or scope != experiment.target_instruments:
        raise ValueError("pooled coverage scope differs from the fixed eligible universe")
    expected_targets = tuple(
        sorted(
            (
                target
                for instrument in scope
                for target in validation_targets_for_instrument(
                    verified,
                    experiment,
                    outer_fold_id=fit.outer_fold_id,
                    target_instrument_id=instrument,
                    horizon=fit.horizon,
                )
            ),
            key=lambda target: (target.decision_time, target.instrument_id, target.target_id),
        )
    )
    if len(expected_targets) != fit.outer_validation_opportunity_count:
        raise ValueError("fold-fit outer validation count differs from authenticated R1 membership")
    coverage_by_target = {row.target_id: row for row in result.coverage.rows}
    if len(coverage_by_target) != len(result.coverage.rows) or set(coverage_by_target) != {
        target.target_id for target in expected_targets
    }:
        raise ValueError("forecast coverage does not exactly cover authenticated R1 membership")
    feature_by_key = _feature_rows_by_key(feature_dataset)
    forecast_by_id = {row.forecast_id: row for row in result.forecasts.rows}
    if len(forecast_by_id) != len(result.forecasts.rows):
        raise ValueError("Ridge forecasts contain duplicate identities")
    reconciled_forecast_ids: set[str] = set()
    for target in expected_targets:
        coverage = coverage_by_target[target.target_id]
        feature = feature_by_key.get((target.decision_time, target.instrument_id))
        expected_feature_asof = feature.feature_data_asof if feature is not None else None
        if (
            coverage.target_instrument_id != target.instrument_id
            or coverage.decision_time != target.decision_time
            or coverage.horizon != target.horizon
            or coverage.outer_fold_id != fit.outer_fold_id
            or coverage.fold_fit_id != fit.artifact_id
            or coverage.feature_data_asof != expected_feature_asof
        ):
            raise ValueError("forecast coverage lineage differs from authenticated opportunity")
        if coverage.disposition is not ForecastCoverageDisposition.FORECASTED:
            continue
        if coverage.forecast_id is None or coverage.forecast_id not in forecast_by_id:
            raise ValueError("forecasted coverage does not bind an emitted forecast")
        forecast = forecast_by_id[coverage.forecast_id]
        reconciled_forecast_ids.add(coverage.forecast_id)
        if (
            forecast.target_id != target.target_id
            or forecast.instrument_id != target.instrument_id
            or forecast.decision_time != target.decision_time
            or forecast.horizon != target.horizon
            or forecast.feature_data_asof != expected_feature_asof
            or forecast.training_cutoff != fit.training_cutoff
            or forecast.observation_dataset_id != verified.observations.dataset_id
            or forecast.panel_dataset_id != verified.panel.dataset_id
            or forecast.target_dataset_id != verified.targets.dataset_id
            or forecast.fold_dataset_id != verified.folds.dataset_id
            or forecast.experiment_id != experiment.configuration_id
            or forecast.fold_id != fit.outer_fold_id
            or forecast.model_id != fit.artifact_id
            or forecast.model_contract != fit.CONTRACT
            or forecast.return_unit is not ReturnUnit.LOG_RETURN
        ):
            raise ValueError("Ridge forecast lineage differs from authenticated opportunity")
    if reconciled_forecast_ids != set(forecast_by_id):
        raise ValueError("emitted forecasts do not exactly reconcile to authenticated coverage")
    replay_ridge_forecasts(result, feature_dataset, experiment)


def verify_local_ridge_forecast_coverage(
    verified: R1FoundationBindings,
    feature_dataset: R2FeatureDataset,
    experiment: R2ExperimentConfig,
    result: LocalRidgeFoldResult,
) -> None:
    """Authenticate one local result against its exact R1 outer opportunities."""

    verify_ridge_forecast_coverage(
        verified,
        feature_dataset,
        experiment,
        result,
        target_instruments=(result.fit.target_instrument_id,),
    )


def replay_ridge_forecasts(
    result: RidgeFoldResult,
    feature_dataset: R2FeatureDataset,
    experiment: R2ExperimentConfig,
) -> None:
    """Independently reproduce forecasts without loading scikit-learn model code."""

    fit = result.fit
    if (
        feature_dataset.dataset_id != fit.r2_feature_dataset_id
        or feature_dataset.target_dataset_id != fit.target_dataset_id
        or feature_dataset.fold_dataset_id != fit.fold_dataset_id
        or feature_dataset.experiment_configuration_id != fit.experiment_configuration_id
    ):
        raise ValueError("Ridge replay feature lineage differs from the fold fit")
    if fit.disposition is not FitDisposition.READY:
        if result.forecasts.rows:
            raise ValueError("failed Ridge fold fit emitted forecasts")
        return
    preprocessing = fit.preprocessing
    intercept = fit.intercept
    if preprocessing is None or intercept is None:
        raise ValueError("ready Ridge fold fit has incomplete replay state")
    by_key = _feature_rows_by_key(feature_dataset)
    forecast_by_id = {row.forecast_id: row for row in result.forecasts.rows}
    for coverage in result.coverage.rows:
        if coverage.disposition is not ForecastCoverageDisposition.FORECASTED:
            continue
        if coverage.forecast_id is None:
            raise ValueError("forecasted coverage omitted its forecast identity")
        forecast = forecast_by_id[coverage.forecast_id]
        feature = by_key[(forecast.decision_time, forecast.instrument_id)]
        transformed = add_instrument_identity(
            transform(
                (
                    PredictionRow(
                        forecast.target_id,
                        forecast.instrument_id,
                        _feature_values(feature),
                    ),
                ),
                preprocessing,
            ),
            (
                PredictionRow(
                    forecast.target_id,
                    forecast.instrument_id,
                    _feature_values(feature),
                ),
            ),
            _instrument_identity_order(fit),
        )
        replayed = float(intercept + transformed[0] @ np.asarray(fit.coefficients, dtype=float))
        if not isclose(
            replayed,
            forecast.expected_return,
            rel_tol=experiment.numeric_replay_relative_tolerance,
            abs_tol=experiment.numeric_replay_absolute_tolerance,
        ):
            raise ValueError("stored Ridge coefficients do not independently reproduce forecast")


def replay_local_ridge_forecasts(
    result: LocalRidgeFoldResult,
    feature_dataset: R2FeatureDataset,
    experiment: R2ExperimentConfig,
) -> None:
    if result.fit.model_family is not ModelFamily.LOCAL_RIDGE:
        raise ValueError("local replay requires a LOCAL_RIDGE fold fit")
    replay_ridge_forecasts(result, feature_dataset, experiment)


def build_coefficient_stability_summary(
    fits: Sequence[R2FoldFit],
) -> CoefficientStabilitySummary:
    """Summarise coefficient scale and sign stability across ready outer-fold fits."""

    if not fits:
        raise ValueError("coefficient stability requires fold fits")
    grouped: dict[tuple[str, timedelta, str], list[R2FoldFit]] = defaultdict(list)
    for fit in fits:
        grouped[(fit.target_instrument_id, fit.horizon, fit.feature_set_id)].append(fit)
    rows: list[CoefficientStabilityRow] = []
    for (instrument, horizon, feature_set_id), group in sorted(grouped.items()):
        ready = tuple(item for item in group if item.disposition is FitDisposition.READY)
        names = sorted(
            {name for fit in ready for name in fit.coefficient_feature_names} | {"__INTERCEPT__"}
        )
        for name in names:
            values = tuple(
                value for fit in ready for value in _named_coefficient_or_empty(fit, name)
            )
            if not values:
                continue
            vector = np.asarray(values, dtype=float)
            rows.append(
                CoefficientStabilityRow(
                    target_instrument_id=instrument,
                    horizon=horizon,
                    feature_set_id=feature_set_id,
                    coefficient_name=name,
                    ready_fit_count=len(values),
                    expected_fit_count=len(group),
                    mean=float(np.mean(vector)),
                    standard_deviation=float(np.std(vector)),
                    minimum=float(np.min(vector)),
                    maximum=float(np.max(vector)),
                    positive_count=sum(value > 0 for value in values),
                    negative_count=sum(value < 0 for value in values),
                    zero_count=sum(value == 0 for value in values),
                )
            )
    return CoefficientStabilitySummary.create(
        rows,
        tuple(fit.artifact_id for fit in fits),
        fits[0].market_data_source_class,
    )


def verify_coefficient_stability_summary(
    summary: CoefficientStabilitySummary, fits: Sequence[R2FoldFit]
) -> None:
    """Recompute and authenticate every coefficient-stability statistic."""

    if summary != build_coefficient_stability_summary(fits):
        raise ValueError("coefficient-stability summary differs from authenticated fold-fit replay")


def _verify_selection_rebuild(
    verified: R1FoundationBindings,
    feature_dataset: R2FeatureDataset,
    experiment: R2ExperimentConfig,
    selection: R2PreprocessingSelection,
) -> None:
    if selection.model_family is not ModelFamily.LOCAL_RIDGE:
        raise ValueError("R2.D local fitting requires a LOCAL_RIDGE selection")
    if selection.horizon != experiment.primary_horizon:
        raise ValueError("R2.D v1 accepts only the selected primary horizon")
    rebuilt = build_r2_preprocessing_selection(
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
        raise ValueError("R2.D preprocessing selection differs from authenticated R2.C rebuild")


def preprocessing_selections_match(
    actual: R2PreprocessingSelection,
    expected: R2PreprocessingSelection,
    *,
    relative_tolerance: float,
    absolute_tolerance: float,
) -> bool:
    if _selection_structural_json(actual) != _selection_structural_json(expected):
        return False
    for actual_fit, expected_fit in (
        (actual.selection.inner_preprocessing, expected.selection.inner_preprocessing),
        (actual.selection.outer_preprocessing, expected.selection.outer_preprocessing),
    ):
        if actual_fit is None or expected_fit is None:
            if actual_fit is not expected_fit:
                return False
            continue
        for actual_values, expected_values in (
            (actual_fit.medians, expected_fit.medians),
            (actual_fit.means, expected_fit.means),
            (actual_fit.scales, expected_fit.scales),
            (actual_fit.sample_weights, expected_fit.sample_weights),
        ):
            if len(actual_values) != len(expected_values) or not all(
                _optional_close(
                    left,
                    right,
                    relative_tolerance=relative_tolerance,
                    absolute_tolerance=absolute_tolerance,
                )
                for left, right in zip(actual_values, expected_values, strict=True)
            ):
                return False
    return all(
        _optional_close(
            left.loss,
            right.loss,
            relative_tolerance=relative_tolerance,
            absolute_tolerance=absolute_tolerance,
        )
        for left, right in zip(
            actual.selection.candidate_scores,
            expected.selection.candidate_scores,
            strict=True,
        )
    )


def _selection_structural_json(selection: R2PreprocessingSelection) -> dict[str, object]:
    payload = cast(dict[str, object], cast(object, selection.as_json()))
    payload["artifact_id"] = ""
    nested = cast(dict[str, object], payload["selection"])
    for field in ("inner_preprocessing", "outer_preprocessing"):
        value = nested[field]
        if not isinstance(value, dict):
            continue
        mutable_value = cast(dict[str, object], value)
        for vector_field in ("medians", "means", "scales", "sample_weights"):
            vector = cast(list[object], mutable_value[vector_field])
            mutable_value[vector_field] = [None if item is None else 0.0 for item in vector]
    for value in cast(list[object], nested["candidate_scores"]):
        candidate = cast(dict[str, object], value)
        if candidate["loss"] is not None:
            candidate["loss"] = 0.0
    return payload


def _optional_close(
    left: float | None,
    right: float | None,
    *,
    relative_tolerance: float,
    absolute_tolerance: float,
) -> bool:
    if left is None or right is None:
        return left is right
    return isclose(left, right, rel_tol=relative_tolerance, abs_tol=absolute_tolerance)


def validation_targets_for_instrument(
    verified: R1FoundationBindings,
    experiment: R2ExperimentConfig,
    *,
    outer_fold_id: str,
    target_instrument_id: str,
    horizon: timedelta,
) -> tuple[TargetRow, ...]:
    fold = next(item for item in verified.folds.folds if item.fold_id == outer_fold_id)
    targets = {row.target_id: row for row in verified.targets.rows}
    if len(targets) != len(verified.targets.rows):
        raise ValueError("target dataset contains duplicate identities")
    selected: list[TargetRow] = []
    for target_id in fold.validation_target_ids:
        if target_id not in targets:
            raise ValueError("outer validation membership references an unknown target")
        target = targets[target_id]
        if not fold.validation_start <= target.decision_time < fold.validation_end:
            raise ValueError("outer validation target is outside its authenticated interval")
        if experiment.holdout_range[0] <= target.decision_time < experiment.holdout_range[1]:
            raise ValueError("outer validation membership contains a locked-holdout target")
        if target.return_disposition is not ReturnDisposition.VALID or target.log_return is None:
            continue
        if target.instrument_id == target_instrument_id and target.horizon == horizon:
            selected.append(target)
    return tuple(sorted(selected, key=lambda item: (item.decision_time, item.target_id)))


def fit_ridge(
    feature_dataset: R2FeatureDataset,
    experiment: R2ExperimentConfig,
    selection: R2PreprocessingSelection,
    training_cutoff: datetime,
    excluded_row_count: int,
    validation_count: int,
    training_rows: tuple[TrainingRow, ...],
    application_image_identity: str,
    numpy_library_identity: str,
    sklearn_library_identity: str,
) -> tuple[R2FoldFit, Any | None]:
    chosen = selection.selection
    common = _fold_fit_common(
        feature_dataset,
        experiment,
        selection,
        training_cutoff,
        excluded_row_count,
        validation_count,
        training_rows,
        application_image_identity,
        numpy_library_identity,
        sklearn_library_identity,
    )
    if chosen.disposition is not FitDisposition.READY:
        return (
            R2FoldFit.create(
                **common,
                selected_alpha=chosen.selected_alpha,
                preprocessing=chosen.outer_preprocessing,
                coefficient_feature_names=(),
                intercept=None,
                coefficients=(),
                fit_warnings=(),
                disposition=chosen.disposition,
                failure=f"R2 selection disposition: {chosen.disposition.value}",
                diagnostics=None,
            ),
            None,
        )
    preprocessing = chosen.outer_preprocessing
    alpha = chosen.selected_alpha
    if preprocessing is None or alpha is None:
        raise ValueError("ready R2 selection omitted final preprocessing or alpha")
    if preprocessing.training_target_ids != tuple(row.target_id for row in training_rows):
        raise ValueError("R2 final preprocessing membership differs from outer training rows")
    identity_order = (
        selection.target_instruments
        if selection.model_family is not ModelFamily.LOCAL_RIDGE
        else ()
    )
    observed_instruments = {row.target_instrument_id for row in training_rows}
    if identity_order and any(
        instrument not in observed_instruments for instrument in identity_order
    ):
        return _failed_final_fit(
            common,
            alpha,
            preprocessing,
            FitDisposition.INSUFFICIENT_TRAINING,
            "outer fit omits a declared pooled instrument",
        )
    if len(training_rows) < experiment.minimum_training_rows:
        return _failed_final_fit(
            common,
            alpha,
            preprocessing,
            FitDisposition.INSUFFICIENT_TRAINING,
            "outer fit has fewer than minimum_training_rows",
        )
    y = np.asarray([row.target for row in training_rows], dtype=np.float64)
    if np.ptp(y) == 0:
        return _failed_final_fit(
            common,
            alpha,
            preprocessing,
            FitDisposition.DEGENERATE_TARGET,
            "outer training target has zero variance",
        )
    local = transform(training_rows, preprocessing)
    x = add_instrument_identity(local, training_rows, identity_order)
    expected_columns = len(preprocessing.active_feature_names) + len(identity_order)
    if x.shape != (len(training_rows), expected_columns) or x.shape[1] == 0:
        return _failed_final_fit(
            common,
            alpha,
            preprocessing,
            FitDisposition.DEGENERATE_FEATURE_MATRIX,
            "outer transformed feature matrix has invalid dimensions",
        )
    if not np.isfinite(x).all():
        return _failed_final_fit(
            common,
            alpha,
            preprocessing,
            FitDisposition.NON_FINITE_MATRIX,
            "outer transformed feature matrix contains non-finite values",
        )
    model: Any = Ridge(
        alpha=alpha,
        solver=selection.ridge_solver,
        tol=selection.ridge_tolerance,
        max_iter=selection.ridge_max_iterations,
        fit_intercept=selection.model_family is ModelFamily.LOCAL_RIDGE,
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            model.fit(x, y, sample_weight=np.asarray(preprocessing.sample_weights, dtype=float))
        except (ArithmeticError, ValueError) as error:
            return _failed_final_fit(
                common,
                alpha,
                preprocessing,
                FitDisposition.NUMERICAL_FAILURE,
                f"{type(error).__name__}: {error}",
            )
    warning_names = tuple(
        sorted({f"{item.category.__module__}.{item.category.__name__}" for item in caught})
    )
    if warning_names:
        return _failed_final_fit(
            common,
            alpha,
            preprocessing,
            FitDisposition.NUMERICAL_FAILURE,
            "undeclared warning during final Ridge fit",
            warnings_seen=warning_names,
        )
    try:
        coefficients = np.asarray(model.coef_, dtype=np.float64)
        intercept_values = np.asarray(model.intercept_, dtype=np.float64).reshape(-1)
        predictions = np.asarray(model.predict(x), dtype=np.float64).reshape(-1)
    except (ArithmeticError, ValueError) as error:
        return _failed_final_fit(
            common,
            alpha,
            preprocessing,
            FitDisposition.NUMERICAL_FAILURE,
            f"{type(error).__name__}: {error}",
        )
    if (
        coefficients.shape != (x.shape[1],)
        or intercept_values.shape != (1,)
        or predictions.shape != (len(training_rows),)
        or not np.isfinite(coefficients).all()
        or not np.isfinite(intercept_values).all()
        or not np.isfinite(predictions).all()
    ):
        return _failed_final_fit(
            common,
            alpha,
            preprocessing,
            FitDisposition.NUMERICAL_FAILURE,
            "final Ridge parameters or predictions have invalid dimensions or values",
        )
    intercept = float(intercept_values[0])
    replay = intercept + x @ coefficients
    replay_error = float(np.max(np.abs(predictions - replay)))
    if not np.allclose(
        predictions,
        replay,
        rtol=experiment.numeric_replay_relative_tolerance,
        atol=experiment.numeric_replay_absolute_tolerance,
    ):
        return _failed_final_fit(
            common,
            alpha,
            preprocessing,
            FitDisposition.NUMERICAL_FAILURE,
            "final Ridge prediction replay mismatch",
        )
    diagnostics = FoldFitDiagnostics(
        iteration_count=_iteration_count(model),
        training_target_mean=float(np.mean(y)),
        training_target_standard_deviation=float(np.std(y)),
        training_prediction_mse=float(np.mean((predictions - y) ** 2)),
        coefficient_l2_norm=float(np.linalg.norm(coefficients)),
        maximum_absolute_coefficient=float(np.max(np.abs(coefficients))),
        prediction_replay_maximum_absolute_error=replay_error,
    )
    coefficient_names = (
        *preprocessing.active_feature_names,
        *(f"instrument_identity::{instrument}" for instrument in identity_order),
    )
    fit = R2FoldFit.create(
        **common,
        selected_alpha=alpha,
        preprocessing=preprocessing,
        coefficient_feature_names=coefficient_names,
        intercept=intercept,
        coefficients=tuple(float(value) for value in coefficients),
        fit_warnings=(),
        disposition=FitDisposition.READY,
        failure=None,
        diagnostics=diagnostics,
    )
    return fit, model


def _fold_fit_common(
    feature_dataset: R2FeatureDataset,
    experiment: R2ExperimentConfig,
    selection: R2PreprocessingSelection,
    training_cutoff: datetime,
    excluded_row_count: int,
    validation_count: int,
    training_rows: tuple[TrainingRow, ...],
    application_image_identity: str,
    numpy_library_identity: str,
    sklearn_library_identity: str,
) -> dict[str, object]:
    return {
        "r2_feature_dataset_id": feature_dataset.dataset_id,
        "target_dataset_id": selection.target_dataset_id,
        "fold_dataset_id": selection.fold_dataset_id,
        "experiment_configuration_id": experiment.configuration_id,
        "preprocessing_selection_id": selection.artifact_id,
        "model_family": selection.model_family,
        "horizon": selection.horizon,
        "outer_fold_id": selection.outer_fold_id,
        "outer_fold_membership_hash": selection.outer_fold_membership_hash,
        "target_instrument_id": (
            selection.target_instruments[0]
            if selection.model_family is ModelFamily.LOCAL_RIDGE
            else "__POOLED__"
        ),
        "feature_set_id": selection.feature_set_id,
        "feature_schema_id": selection.feature_schema_id,
        "preprocessing_schema_id": selection.preprocessing_schema_id,
        "evidence_class": selection.evidence_class,
        "market_data_source_class": experiment.market_data_source_class,
        "application_image_identity": application_image_identity,
        "numpy_library_identity": numpy_library_identity,
        "sklearn_library_identity": sklearn_library_identity,
        "training_cutoff": training_cutoff,
        "fit_row_count": len(training_rows),
        "excluded_row_count": excluded_row_count,
        "outer_validation_opportunity_count": validation_count,
    }


def _failed_final_fit(
    common: dict[str, object],
    alpha: float,
    preprocessing: object,
    disposition: FitDisposition,
    failure: str,
    *,
    warnings_seen: tuple[str, ...] = (),
) -> tuple[R2FoldFit, None]:
    return (
        R2FoldFit.create(
            **common,
            selected_alpha=alpha,
            preprocessing=preprocessing,
            coefficient_feature_names=(),
            intercept=None,
            coefficients=(),
            fit_warnings=warnings_seen,
            disposition=disposition,
            failure=failure,
            diagnostics=None,
        ),
        None,
    )


def forecast_validation(
    verified: R1FoundationBindings,
    feature_dataset: R2FeatureDataset,
    experiment: R2ExperimentConfig,
    fit: R2FoldFit,
    model: Any | None,
    targets: tuple[TargetRow, ...],
) -> tuple[ForecastDataset, ForecastCoverageDataset]:
    feature_by_key = _feature_rows_by_key(feature_dataset)
    forecasts: list[ForecastRow] = []
    coverage: list[ForecastCoverageRow] = []
    failed_disposition = coverage_disposition_for_fit(fit.disposition)
    for target in targets:
        feature = feature_by_key.get((target.decision_time, target.instrument_id))
        if fit.disposition is not FitDisposition.READY:
            coverage.append(
                _coverage_row(target, fit, feature, failed_disposition, None, fit.failure)
            )
            continue
        if model is None or fit.preprocessing is None or fit.intercept is None:
            raise ValueError("ready fit omitted the in-memory model or canonical replay state")
        if feature is None:
            coverage.append(
                _coverage_row(
                    target,
                    fit,
                    None,
                    ForecastCoverageDisposition.FEATURES_UNAVAILABLE,
                    None,
                    "no exact raw-feature row exists for the expected opportunity",
                )
            )
            continue
        try:
            prediction_row = PredictionRow(
                target.target_id, target.instrument_id, _feature_values(feature)
            )
            transformed = add_instrument_identity(
                transform((prediction_row,), fit.preprocessing),
                (prediction_row,),
                _instrument_identity_order(fit),
            )
            prediction = float(np.asarray(model.predict(transformed), dtype=float).reshape(-1)[0])
            replayed = float(
                fit.intercept + transformed[0] @ np.asarray(fit.coefficients, dtype=float)
            )
        except (ArithmeticError, ValueError) as error:
            coverage.append(
                _coverage_row(
                    target,
                    fit,
                    feature,
                    ForecastCoverageDisposition.NUMERICAL_FAILURE,
                    None,
                    f"{type(error).__name__}: {error}",
                )
            )
            continue
        if not isfinite(prediction) or not isclose(
            prediction,
            replayed,
            rel_tol=experiment.numeric_replay_relative_tolerance,
            abs_tol=experiment.numeric_replay_absolute_tolerance,
        ):
            coverage.append(
                _coverage_row(
                    target,
                    fit,
                    feature,
                    ForecastCoverageDisposition.NUMERICAL_FAILURE,
                    None,
                    "forecast is non-finite or differs from canonical coefficient replay",
                )
            )
            continue
        forecast = ForecastRow.create(
            instrument_id=target.instrument_id,
            decision_time=target.decision_time,
            horizon=target.horizon,
            expected_return=prediction,
            return_unit=ReturnUnit.LOG_RETURN,
            feature_data_asof=feature.feature_data_asof,
            training_cutoff=fit.training_cutoff,
            observation_dataset_id=verified.observations.dataset_id,
            panel_dataset_id=verified.panel.dataset_id,
            target_dataset_id=verified.targets.dataset_id,
            target_id=target.target_id,
            fold_dataset_id=verified.folds.dataset_id,
            experiment_id=experiment.configuration_id,
            fold_id=fit.outer_fold_id,
            model_id=fit.artifact_id,
            model_contract=fit.CONTRACT,
        )
        forecasts.append(forecast)
        coverage.append(
            _coverage_row(
                target,
                fit,
                feature,
                ForecastCoverageDisposition.FORECASTED,
                forecast.forecast_id,
                None,
            )
        )
    return (
        ForecastDataset.create(
            forecasts,
            observation_dataset_id=verified.observations.dataset_id,
            panel_dataset_id=verified.panel.dataset_id,
            target_dataset_id=verified.targets.dataset_id,
            fold_dataset_id=verified.folds.dataset_id,
        ),
        ForecastCoverageDataset.create(
            coverage,
            experiment_configuration_id=experiment.configuration_id,
            target_dataset_id=verified.targets.dataset_id,
            fold_dataset_id=verified.folds.dataset_id,
            r2_feature_dataset_id=feature_dataset.dataset_id,
            market_data_source_class=fit.market_data_source_class,
        ),
    )


def _coverage_row(
    target: TargetRow,
    fit: R2FoldFit,
    feature: RawFeatureRow | None,
    disposition: ForecastCoverageDisposition,
    forecast_id: str | None,
    reason: str | None,
) -> ForecastCoverageRow:
    return ForecastCoverageRow.create(
        target_id=target.target_id,
        target_instrument_id=target.instrument_id,
        decision_time=target.decision_time,
        horizon=target.horizon,
        outer_fold_id=fit.outer_fold_id,
        fold_fit_id=fit.artifact_id,
        feature_data_asof=feature.feature_data_asof if feature is not None else None,
        disposition=disposition,
        forecast_id=forecast_id,
        reason=reason,
        market_data_source_class=fit.market_data_source_class,
    )


def _feature_rows_by_key(
    feature_dataset: R2FeatureDataset,
) -> dict[tuple[datetime, str], RawFeatureRow]:
    rows: dict[tuple[datetime, str], RawFeatureRow] = {}
    for row in feature_dataset.rows:
        key = row.decision_time, row.target_instrument_id
        if key in rows:
            raise ValueError("feature dataset has duplicate stable target join keys")
        if row.feature_data_asof > row.decision_time:
            raise ValueError("feature cutoff follows its forecast decision time")
        rows[key] = row
    return rows


def _feature_values(row: RawFeatureRow) -> tuple[float | None, ...]:
    return tuple(value.value for value in row.values)


def _iteration_count(model: Any) -> int | None:
    raw = model.n_iter_
    if raw is None:
        return None
    values = np.asarray(raw).reshape(-1)
    if values.shape != (1,) or not np.isfinite(values).all():
        raise ValueError("Ridge iteration diagnostics have invalid dimensions or values")
    count = int(values[0])
    if count < 0:
        raise ValueError("Ridge iteration count cannot be negative")
    return count


def _named_coefficient_or_empty(fit: R2FoldFit, name: str) -> tuple[float, ...]:
    if name == "__INTERCEPT__":
        if fit.intercept is None:
            return ()
        return (fit.intercept,)
    by_name = dict(zip(fit.coefficient_feature_names, fit.coefficients, strict=True))
    return () if name not in by_name else (by_name[name],)


def _instrument_identity_order(fit: R2FoldFit) -> tuple[str, ...]:
    prefix = "instrument_identity::"
    return tuple(
        name.removeprefix(prefix)
        for name in fit.coefficient_feature_names
        if name.startswith(prefix)
    )
