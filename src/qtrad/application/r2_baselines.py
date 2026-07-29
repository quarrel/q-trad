"""Authenticated R2.D local Ridge fitting, forecasting and replay."""

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
    build_r2_preprocessing_selection,
    join_training_rows,
    transform,
)
from qtrad.application.r2_readiness import R1FoundationBindings, verify_exact_r1_bindings
from qtrad.domain.forecasts import ForecastDataset, ForecastRow, ReturnUnit
from qtrad.domain.foundation import TargetRow
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
    features: tuple[float | None, ...]


@dataclass(frozen=True, slots=True)
class LocalRidgeFoldResult:
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
            raise ValueError("local result child lineage differs from its fold fit")
        forecast_ids = {row.forecast_id for row in self.forecasts.rows}
        covered_ids = {
            row.forecast_id
            for row in self.coverage.rows
            if row.disposition is ForecastCoverageDisposition.FORECASTED
        }
        if forecast_ids != covered_ids or None in covered_ids:
            raise ValueError("local forecast and coverage identities do not reconcile")
        if any(row.model_id != self.fit.artifact_id for row in self.forecasts.rows):
            raise ValueError("local forecasts do not bind the fold-fit artifact")
        if any(row.fold_fit_id != self.fit.artifact_id for row in self.coverage.rows):
            raise ValueError("local coverage does not bind the fold-fit artifact")


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


def build_local_ridge_oof(
    verified: R1FoundationBindings,
    feature_datasets: Sequence[R2FeatureDataset],
    experiment: R2ExperimentConfig,
    selections: Sequence[R2PreprocessingSelection],
) -> LocalRidgeOofResult:
    """Build the exact local-feature ablation ladder for every eligible target and outer fold."""

    datasets = {dataset.feature_set_id: dataset for dataset in feature_datasets}
    if len(datasets) != len(feature_datasets):
        raise ValueError("local OOF feature datasets contain duplicate feature-set identities")
    declared_local_sets = tuple(
        item
        for item in experiment.feature_sets
        if FeatureFamily.POOLED_CROSS_ASSET not in item.families
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
    validation_targets = _validation_targets(
        verified,
        experiment,
        outer_fold_id=selection.outer_fold_id,
        target_instrument_id=target_instrument_id,
        horizon=selection.horizon,
    )
    fit, model = _fit_local_ridge(
        feature_dataset,
        experiment,
        selection,
        fold.training_cutoff,
        len(fold.training_target_ids) - len(training_rows),
        len(validation_targets),
        training_rows,
    )
    forecasts, coverage = _forecast_validation(
        verified,
        feature_dataset,
        experiment,
        fit,
        model,
        validation_targets,
    )
    result = LocalRidgeFoldResult(fit, forecasts, coverage)
    replay_local_ridge_forecasts(result, feature_dataset, experiment)
    return result


def replay_local_ridge_forecasts(
    result: LocalRidgeFoldResult,
    feature_dataset: R2FeatureDataset,
    experiment: R2ExperimentConfig,
) -> None:
    """Independently reproduce persisted forecasts without loading scikit-learn model code."""

    fit = result.fit
    if (
        feature_dataset.dataset_id != fit.r2_feature_dataset_id
        or feature_dataset.target_dataset_id != fit.target_dataset_id
        or feature_dataset.fold_dataset_id != fit.fold_dataset_id
        or feature_dataset.experiment_configuration_id != fit.experiment_configuration_id
    ):
        raise ValueError("local replay feature lineage differs from the fold fit")
    if fit.disposition is not FitDisposition.READY:
        if result.forecasts.rows:
            raise ValueError("failed local fold fit emitted forecasts")
        return
    preprocessing = fit.preprocessing
    intercept = fit.intercept
    if preprocessing is None or intercept is None:
        raise ValueError("ready local fold fit has incomplete replay state")
    by_key = _feature_rows_by_key(feature_dataset)
    forecast_by_id = {row.forecast_id: row for row in result.forecasts.rows}
    for coverage in result.coverage.rows:
        if coverage.disposition is not ForecastCoverageDisposition.FORECASTED:
            continue
        if coverage.forecast_id is None:
            raise ValueError("forecasted coverage omitted its forecast identity")
        forecast = forecast_by_id[coverage.forecast_id]
        feature = by_key[(forecast.decision_time, forecast.instrument_id)]
        transformed = transform(
            (PredictionRow(forecast.target_id, _feature_values(feature)),), preprocessing
        )
        replayed = float(intercept + transformed[0] @ np.asarray(fit.coefficients, dtype=float))
        if not isclose(
            replayed,
            forecast.expected_return,
            rel_tol=experiment.numeric_replay_relative_tolerance,
            abs_tol=experiment.numeric_replay_absolute_tolerance,
        ):
            raise ValueError("stored local coefficients do not independently reproduce forecast")


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
    return CoefficientStabilitySummary.create(rows, tuple(fit.artifact_id for fit in fits))


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
    if not _preprocessing_selections_match(
        selection,
        rebuilt,
        relative_tolerance=experiment.numeric_replay_relative_tolerance,
        absolute_tolerance=experiment.numeric_replay_absolute_tolerance,
    ):
        raise ValueError("R2.D preprocessing selection differs from authenticated R2.C rebuild")


def _preprocessing_selections_match(
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


def _validation_targets(
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
        if target.instrument_id == target_instrument_id and target.horizon == horizon:
            selected.append(target)
    return tuple(sorted(selected, key=lambda item: (item.decision_time, item.target_id)))


def _fit_local_ridge(
    feature_dataset: R2FeatureDataset,
    experiment: R2ExperimentConfig,
    selection: R2PreprocessingSelection,
    training_cutoff: datetime,
    excluded_row_count: int,
    validation_count: int,
    training_rows: tuple[TrainingRow, ...],
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
                failure=f"R2.C selection disposition: {chosen.disposition.value}",
                diagnostics=None,
            ),
            None,
        )
    preprocessing = chosen.outer_preprocessing
    alpha = chosen.selected_alpha
    if preprocessing is None or alpha is None:
        raise ValueError("ready R2.C selection omitted final preprocessing or alpha")
    if preprocessing.training_target_ids != tuple(row.target_id for row in training_rows):
        raise ValueError("R2.C final preprocessing membership differs from outer training rows")
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
    x = transform(training_rows, preprocessing)
    if x.shape != (len(training_rows), len(preprocessing.active_feature_names)) or x.shape[1] == 0:
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
    fit = R2FoldFit.create(
        **common,
        selected_alpha=alpha,
        preprocessing=preprocessing,
        coefficient_feature_names=preprocessing.active_feature_names,
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
) -> dict[str, object]:
    return {
        "r2_feature_dataset_id": feature_dataset.dataset_id,
        "target_dataset_id": selection.target_dataset_id,
        "fold_dataset_id": selection.fold_dataset_id,
        "experiment_configuration_id": experiment.configuration_id,
        "preprocessing_selection_id": selection.artifact_id,
        "model_family": ModelFamily.LOCAL_RIDGE,
        "horizon": selection.horizon,
        "outer_fold_id": selection.outer_fold_id,
        "outer_fold_membership_hash": selection.outer_fold_membership_hash,
        "target_instrument_id": selection.target_instruments[0],
        "feature_set_id": selection.feature_set_id,
        "feature_schema_id": selection.feature_schema_id,
        "preprocessing_schema_id": selection.preprocessing_schema_id,
        "evidence_class": selection.evidence_class,
        "application_image_identity": selection.application_image_identity,
        "sklearn_library_identity": selection.sklearn_library_identity,
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


def _forecast_validation(
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
            transformed = transform(
                (PredictionRow(target.target_id, _feature_values(feature)),), fit.preprocessing
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
