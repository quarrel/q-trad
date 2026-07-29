"""Authenticated R2.C fold-local preprocessing and chronological alpha selection."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite
from typing import Any, Protocol, cast

import numpy as np
from sklearn.linear_model import Ridge  # type: ignore[reportMissingTypeStubs]

from qtrad.application.r2_features import feature_schema_for_set
from qtrad.application.r2_readiness import R1FoundationBindings, verify_exact_r1_bindings
from qtrad.domain.folds import Fold, membership_hash
from qtrad.domain.foundation import ReturnDisposition, TargetDataset
from qtrad.domain.r2_features import (
    FeatureDefinition,
    R2FeatureDataset,
    RawFeatureRow,
    feature_schema_id,
    feature_set_id,
)
from qtrad.domain.r2_models import (
    AlphaCandidateScore,
    AlphaSelection,
    FitDisposition,
    PreprocessingFeatureDefinition,
    PreprocessingFeatureKind,
    PreprocessingFit,
    R2PreprocessingSchema,
    R2PreprocessingSelection,
    derive_r2_preprocessing_schema,
)
from qtrad.domain.r2_readiness import FeatureFamily, ModelFamily, R2ExperimentConfig
from qtrad.domain.time import require_utc

_SUPPORTED_PREPROCESSING = "TRAINING_MEDIAN_STANDARDISE_V1"
_SUPPORTED_INNER_SPLIT = "CHRONOLOGICAL_TAIL_PURGED_V1"
_SUPPORTED_LOSS = "OOF_PRIMARY_MSE_V1"
_SUPPORTED_WEIGHTING = "EQUAL_INSTRUMENT_TOTAL_WEIGHT_MEAN_ONE"


@dataclass(frozen=True, slots=True)
class TrainingRow:
    target_id: str
    decision_time: datetime
    target_end_time: datetime
    target_available_at: datetime
    target_instrument_id: str
    features: tuple[float | None, ...]
    target: float

    def __post_init__(self) -> None:
        require_utc(self.decision_time, "R2 training decision_time")
        require_utc(self.target_end_time, "R2 training target_end_time")
        require_utc(self.target_available_at, "R2 training target_available_at")
        if not self.target_id or not self.target_instrument_id or not isfinite(self.target):
            raise ValueError("training row requires identities and a finite target")
        if self.target_end_time < self.decision_time:
            raise ValueError("training target endpoint cannot precede its decision")
        if any(value is not None and not isfinite(value) for value in self.features):
            raise ValueError("features must be finite or null")


class FeatureVector(Protocol):
    @property
    def features(self) -> tuple[float | None, ...]: ...


def build_r2_preprocessing_selection(
    verified: R1FoundationBindings,
    feature_dataset: R2FeatureDataset,
    experiment: R2ExperimentConfig,
    *,
    model_family: ModelFamily,
    horizon: timedelta,
    outer_fold_id: str,
    target_instruments: Sequence[str] | None = None,
    application_image_identity: str,
    sklearn_library_identity: str,
) -> R2PreprocessingSelection:
    """Build one replayable preprocessing selection from authenticated R1 and R2.B children."""
    _verify_selection_policies(experiment, model_family, horizon)
    verify_exact_r1_bindings(verified, experiment)
    authenticated_raw_schema = _verify_feature_bindings(
        verified, feature_dataset, experiment, model_family
    )
    preprocessing_schema = derive_r2_preprocessing_schema(authenticated_raw_schema)
    fold = _outer_fold(verified, outer_fold_id)
    if not fold.holdout_excluded:
        raise ValueError("R2.C cannot consume a fold containing holdout membership")
    if fold.membership_hash != membership_hash(
        fold.training_target_ids, fold.validation_target_ids
    ):
        raise ValueError("outer fold membership authentication failed")

    scope = _target_scope(experiment, target_instruments)
    rows = join_training_rows(
        verified.targets,
        fold,
        feature_dataset,
        experiment,
        scope,
        horizon,
    )
    selection = select_chronological_alpha(
        rows,
        preprocessing_schema=preprocessing_schema,
        alpha_grid=experiment.alpha_grid,
        minimum_training_rows=experiment.minimum_training_rows,
        minimum_inner_validation_rows=experiment.minimum_inner_validation_rows,
        ridge_solver=experiment.ridge_solver,
        ridge_tolerance=experiment.ridge_tolerance,
        ridge_max_iterations=experiment.ridge_max_iterations,
        loss_policy=experiment.model_selection_policy,
        pooled_weighting_policy=experiment.pooled_weighting_policy,
    )
    validation_start, validation_end = _inner_validation_bounds(
        rows, experiment.minimum_inner_validation_rows
    )
    return R2PreprocessingSelection.create(
        r2_feature_dataset_id=feature_dataset.dataset_id,
        target_dataset_id=verified.targets.dataset_id,
        fold_dataset_id=verified.folds.dataset_id,
        experiment_configuration_id=experiment.configuration_id,
        model_family=model_family,
        horizon=horizon,
        outer_fold_id=fold.fold_id,
        outer_fold_membership_hash=fold.membership_hash,
        target_instruments=scope,
        inner_validation_start=validation_start,
        inner_validation_end=validation_end,
        purge_boundary=validation_start,
        feature_schema_id=feature_dataset.raw_feature_schema_id,
        feature_set_id=feature_dataset.feature_set_id,
        preprocessing_schema_id=preprocessing_schema.preprocessing_schema_id,
        preprocessing_schema=preprocessing_schema,
        evidence_class=feature_dataset.evidence_class,
        application_image_identity=application_image_identity,
        sklearn_library_identity=sklearn_library_identity,
        preprocessing_policy=experiment.preprocessing_policy,
        inner_validation_policy=experiment.inner_validation_policy,
        alpha_grid=experiment.alpha_grid,
        ridge_solver=experiment.ridge_solver,
        ridge_tolerance=experiment.ridge_tolerance,
        ridge_max_iterations=experiment.ridge_max_iterations,
        loss_policy=experiment.model_selection_policy,
        pooled_weighting_policy=experiment.pooled_weighting_policy,
        holdout_excluded=True,
        selection=selection,
    )


def select_chronological_alpha(
    rows: Sequence[TrainingRow],
    *,
    preprocessing_schema: R2PreprocessingSchema,
    alpha_grid: Sequence[float],
    minimum_training_rows: int,
    minimum_inner_validation_rows: int,
    ridge_solver: str,
    ridge_tolerance: float,
    ridge_max_iterations: int,
    loss_policy: str,
    pooled_weighting_policy: str,
) -> AlphaSelection:
    """Evaluate the exact configured alpha grid on a purged chronological tail."""
    schema = preprocessing_schema.features
    grid = tuple(alpha_grid)
    ordered = tuple(
        sorted(rows, key=lambda row: (row.decision_time, row.target_instrument_id, row.target_id))
    )
    _validate_selection_inputs(
        ordered,
        schema,
        grid,
        minimum_training_rows,
        minimum_inner_validation_rows,
        ridge_solver,
        ridge_tolerance,
        ridge_max_iterations,
        loss_policy,
        pooled_weighting_policy,
    )
    outer_ids = tuple(row.target_id for row in ordered)
    validation_start, _ = _inner_validation_bounds(ordered, minimum_inner_validation_rows)
    inner_validation = tuple(row for row in ordered if row.decision_time >= validation_start)
    prefix = tuple(row for row in ordered if row.decision_time < validation_start)
    inner_fit = tuple(
        row
        for row in prefix
        if row.target_end_time <= validation_start and row.target_available_at <= validation_start
    )
    purged = tuple(
        row
        for row in prefix
        if row.target_end_time > validation_start or row.target_available_at > validation_start
    )

    if len(inner_validation) < minimum_inner_validation_rows:
        return _failed_selection(
            FitDisposition.INSUFFICIENT_INNER_VALIDATION,
            outer_ids,
            inner_fit,
            inner_validation,
            purged,
        )
    if not inner_fit:
        return _failed_selection(
            FitDisposition.INSUFFICIENT_TRAINING,
            outer_ids,
            inner_fit,
            inner_validation,
            purged,
        )

    inner_weights = equal_instrument_total_weights(inner_fit, pooled_weighting_policy)
    inner_preprocessing = fit_preprocessing(
        inner_fit, preprocessing_schema, sample_weights=inner_weights
    )
    if len(inner_fit) < minimum_training_rows:
        failure_scores = _failure_scores(
            grid,
            FitDisposition.INSUFFICIENT_TRAINING,
            "inner fit has fewer than minimum_training_rows after purge",
            inner_fit,
            inner_validation,
        )
        return _failed_selection(
            FitDisposition.INSUFFICIENT_TRAINING,
            outer_ids,
            inner_fit,
            inner_validation,
            purged,
            inner_preprocessing=inner_preprocessing,
            candidate_scores=failure_scores,
        )
    if np.ptp(np.array([row.target for row in inner_fit], dtype=float)) == 0:
        failure_scores = _failure_scores(
            grid,
            FitDisposition.DEGENERATE_TARGET,
            "inner fit target has zero variance",
            inner_fit,
            inner_validation,
        )
        return _failed_selection(
            FitDisposition.DEGENERATE_TARGET,
            outer_ids,
            inner_fit,
            inner_validation,
            purged,
            inner_preprocessing=inner_preprocessing,
            candidate_scores=failure_scores,
        )
    if not inner_preprocessing.active_feature_names:
        failure_scores = _failure_scores(
            grid,
            FitDisposition.DEGENERATE_FEATURE_MATRIX,
            "all inner-fit features were explicitly dropped",
            inner_fit,
            inner_validation,
        )
        return _failed_selection(
            FitDisposition.DEGENERATE_FEATURE_MATRIX,
            outer_ids,
            inner_fit,
            inner_validation,
            purged,
            inner_preprocessing=inner_preprocessing,
            candidate_scores=failure_scores,
        )

    x_fit = transform(inner_fit, inner_preprocessing)
    x_validation = transform(inner_validation, inner_preprocessing)
    if not np.isfinite(x_fit).all() or not np.isfinite(x_validation).all():
        failure_scores = _failure_scores(
            grid,
            FitDisposition.NON_FINITE_MATRIX,
            "non-finite transformed matrix",
            inner_fit,
            inner_validation,
        )
        return _failed_selection(
            FitDisposition.NON_FINITE_MATRIX,
            outer_ids,
            inner_fit,
            inner_validation,
            purged,
            inner_preprocessing=inner_preprocessing,
            candidate_scores=failure_scores,
        )

    y_fit = np.array([row.target for row in inner_fit], dtype=float)
    y_validation = np.array([row.target for row in inner_validation], dtype=float)
    scores: list[AlphaCandidateScore] = []
    for alpha in grid:
        try:
            predictions = _ridge_predictions(
                alpha,
                x_fit,
                y_fit,
                x_validation,
                inner_weights,
                solver=ridge_solver,
                tolerance=ridge_tolerance,
                max_iterations=ridge_max_iterations,
            )
            loss = _loss(predictions, y_validation, loss_policy)
            if not isfinite(loss):
                raise FloatingPointError("non-finite configured loss")
            scores.append(
                AlphaCandidateScore(
                    alpha,
                    FitDisposition.READY,
                    loss,
                    None,
                    tuple(row.target_id for row in inner_fit),
                    tuple(row.target_id for row in inner_validation),
                )
            )
        except (ArithmeticError, ValueError) as error:
            scores.append(
                AlphaCandidateScore(
                    alpha,
                    FitDisposition.NUMERICAL_FAILURE,
                    None,
                    type(error).__name__,
                    tuple(row.target_id for row in inner_fit),
                    tuple(row.target_id for row in inner_validation),
                )
            )

    ready = tuple(score for score in scores if score.disposition is FitDisposition.READY)
    if not ready:
        return _failed_selection(
            FitDisposition.NUMERICAL_FAILURE,
            outer_ids,
            inner_fit,
            inner_validation,
            purged,
            inner_preprocessing=inner_preprocessing,
            candidate_scores=tuple(scores),
        )
    winner = min(ready, key=lambda score: (cast(float, score.loss), -score.alpha))
    outer_weights = equal_instrument_total_weights(ordered, pooled_weighting_policy)
    outer_preprocessing = fit_preprocessing(
        ordered, preprocessing_schema, sample_weights=outer_weights
    )
    if not outer_preprocessing.active_feature_names:
        return _failed_selection(
            FitDisposition.DEGENERATE_FEATURE_MATRIX,
            outer_ids,
            inner_fit,
            inner_validation,
            purged,
            inner_preprocessing=inner_preprocessing,
            candidate_scores=tuple(scores),
        )
    return AlphaSelection(
        FitDisposition.READY,
        outer_ids,
        tuple(row.target_id for row in inner_fit),
        tuple(row.target_id for row in inner_validation),
        tuple(row.target_id for row in purged),
        inner_preprocessing,
        tuple(scores),
        winner.alpha,
        outer_preprocessing,
    )


def fit_preprocessing(
    rows: Sequence[TrainingRow],
    preprocessing_schema: R2PreprocessingSchema,
    *,
    sample_weights: Sequence[float] | None = None,
) -> PreprocessingFit:
    schema = preprocessing_schema.features
    if not rows:
        raise ValueError("preprocessing requires training rows")
    if any(len(row.features) != len(schema) for row in rows):
        raise ValueError("row features must align with the feature schema")
    weights = (
        tuple(float(value) for value in sample_weights)
        if sample_weights is not None
        else tuple(1.0 for _ in rows)
    )
    if len(weights) != len(rows) or any(not isfinite(value) or value <= 0 for value in weights):
        raise ValueError("sample weights must be finite, positive and row-aligned")
    normalizer = len(weights) / sum(weights)
    weights = tuple(value * normalizer for value in weights)
    matrix = np.array(
        [[np.nan if value is None else value for value in row.features] for row in rows],
        dtype=float,
    )
    if not np.isfinite(matrix[~np.isnan(matrix)]).all():
        raise ValueError("preprocessing matrix contains non-finite values")

    medians: list[float | None] = []
    means: list[float | None] = []
    scales: list[float | None] = []
    active: list[str] = []
    unscaled: list[str] = []
    all_null: list[str] = []
    zero_variance: list[str] = []
    indicators = tuple(
        item.name for item in schema if item.kind is PreprocessingFeatureKind.BINARY_INDICATOR
    )
    for position, definition in enumerate(schema):
        column = matrix[:, position]
        if definition.kind is PreprocessingFeatureKind.BINARY_INDICATOR:
            observed = column[~np.isnan(column)]
            if np.any((observed != 0.0) & (observed != 1.0)):
                raise ValueError(f"binary indicator is not binary: {definition.name}")
            binary = np.where(np.isnan(column), 0.0, column)
            medians.append(None)
            means.append(None)
            scales.append(None)
            if np.ptp(binary) == 0:
                zero_variance.append(definition.name)
            else:
                active.append(definition.name)
                unscaled.append(definition.name)
            continue
        observed_mask = ~np.isnan(column)
        if not observed_mask.any():
            medians.append(None)
            means.append(None)
            scales.append(None)
            all_null.append(definition.name)
            continue
        median = _weighted_median(column[observed_mask], np.array(weights)[observed_mask])
        filled = np.where(np.isnan(column), median, column)
        mean = float(np.average(filled, weights=weights))
        variance = float(np.average((filled - mean) ** 2, weights=weights))
        medians.append(median)
        if variance <= 0:
            means.append(None)
            scales.append(None)
            zero_variance.append(definition.name)
        else:
            means.append(mean)
            scales.append(float(np.sqrt(variance)))
            active.append(definition.name)
    return PreprocessingFit(
        tuple(item.name for item in schema),
        indicators,
        tuple(medians),
        tuple(means),
        tuple(scales),
        tuple(active),
        tuple(unscaled),
        tuple(all_null),
        tuple(zero_variance),
        tuple(row.target_id for row in rows),
        weights,
    )


def transform(rows: Sequence[FeatureVector], fit: PreprocessingFit) -> np.ndarray:
    positions = {name: index for index, name in enumerate(fit.feature_names)}
    active = set(fit.active_feature_names)
    indicators = set(fit.indicator_feature_names)
    columns: list[np.ndarray] = []
    for name in fit.feature_names:
        if name not in active:
            continue
        position = positions[name]
        column = np.array(
            [np.nan if row.features[position] is None else row.features[position] for row in rows],
            dtype=float,
        )
        if name in indicators:
            observed = column[~np.isnan(column)]
            if np.any((observed != 0.0) & (observed != 1.0)):
                raise ValueError(f"binary indicator is not binary: {name}")
            columns.append(np.where(np.isnan(column), 0.0, column))
        else:
            median = fit.medians[position]
            mean = fit.means[position]
            scale = fit.scales[position]
            if median is None or mean is None or scale is None:
                raise ValueError("active continuous feature has incomplete fit state")
            columns.append((np.where(np.isnan(column), median, column) - mean) / scale)
    if not columns:
        return np.empty((len(rows), 0), dtype=float)
    return np.column_stack(columns)


def equal_instrument_total_weights(
    rows: Sequence[TrainingRow], policy: str = _SUPPORTED_WEIGHTING
) -> tuple[float, ...]:
    if policy != _SUPPORTED_WEIGHTING:
        raise ValueError("unsupported pooled weighting policy")
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.target_instrument_id] = counts.get(row.target_instrument_id, 0) + 1
    if not counts:
        return ()
    total = len(rows)
    instrument_total = total / len(counts)
    return tuple(instrument_total / counts[row.target_instrument_id] for row in rows)


def _verify_selection_policies(
    experiment: R2ExperimentConfig, model_family: ModelFamily, horizon: timedelta
) -> None:
    if model_family is not ModelFamily.LOCAL_RIDGE:
        raise ValueError("R2.C selection supports only LOCAL_RIDGE")
    if horizon != experiment.primary_horizon:
        raise ValueError("R2.C selection supports only the experiment primary horizon")
    if experiment.preprocessing_policy != _SUPPORTED_PREPROCESSING:
        raise ValueError("unsupported preprocessing policy")
    if experiment.inner_validation_policy != _SUPPORTED_INNER_SPLIT:
        raise ValueError("unsupported inner-validation policy")


def _verify_feature_bindings(
    verified: R1FoundationBindings,
    features: R2FeatureDataset,
    experiment: R2ExperimentConfig,
    model_family: ModelFamily,
) -> tuple[FeatureDefinition, ...]:
    expected = {
        "observation_dataset_id": (
            features.observation_dataset_id,
            verified.observations.dataset_id,
        ),
        "panel_dataset_id": (features.panel_dataset_id, verified.panel.dataset_id),
        "target_dataset_id": (features.target_dataset_id, verified.targets.dataset_id),
        "fold_dataset_id": (features.fold_dataset_id, verified.folds.dataset_id),
        "experiment_configuration_id": (
            features.experiment_configuration_id,
            experiment.configuration_id,
        ),
        "evidence_class": (features.evidence_class, experiment.evidence_class),
    }
    mismatches = [name for name, (actual, wanted) in expected.items() if actual != wanted]
    if mismatches:
        raise ValueError(f"R2 feature binding mismatch: {', '.join(mismatches)}")
    if not features.holdout_excluded:
        raise ValueError("R2.C cannot consume holdout feature rows")

    authenticated_schema = feature_schema_for_set(experiment, features.feature_set_name)
    declared_set = next(
        item for item in experiment.feature_sets if item.name == features.feature_set_name
    )
    if features.feature_set_name != declared_set.name:
        raise ValueError("R2 feature binding mismatch: feature_set_name")
    if model_family is ModelFamily.LOCAL_RIDGE and (
        FeatureFamily.POOLED_CROSS_ASSET in declared_set.families
    ):
        raise ValueError("LOCAL_RIDGE cannot consume a pooled-context feature set")
    authenticated_set_id = feature_set_id(
        experiment.configuration_id, declared_set.name, authenticated_schema
    )
    if features.feature_set_id != authenticated_set_id:
        raise ValueError("R2 feature binding mismatch: feature_set_id")
    authenticated_schema_id = feature_schema_id(authenticated_schema)
    if features.raw_feature_schema_id != authenticated_schema_id:
        raise ValueError("R2 feature binding mismatch: raw_feature_schema_id")
    if features.feature_schema != authenticated_schema:
        raise ValueError("R2 feature binding mismatch: feature_schema")
    return authenticated_schema


def _outer_fold(verified: R1FoundationBindings, fold_id: str) -> Fold:
    matches = tuple(fold for fold in verified.folds.folds if fold.fold_id == fold_id)
    if len(matches) != 1:
        raise ValueError("outer fold ID must identify exactly one authenticated fold")
    return matches[0]


def _target_scope(
    experiment: R2ExperimentConfig, requested: Sequence[str] | None
) -> tuple[str, ...]:
    scope = tuple(experiment.confirmatory_target_instruments if requested is None else requested)
    if len(scope) != 1 or scope[0] not in experiment.target_instruments:
        raise ValueError("R2.C target scope must contain exactly one eligible target")
    return scope


def join_training_rows(
    targets: TargetDataset,
    fold: Fold,
    features: R2FeatureDataset,
    experiment: R2ExperimentConfig,
    target_scope: tuple[str, ...],
    horizon: timedelta,
) -> tuple[TrainingRow, ...]:
    target_by_id = {row.target_id: row for row in targets.rows}
    if len(target_by_id) != len(targets.rows):
        raise ValueError("target dataset contains duplicate identities")
    missing_targets = tuple(
        target_id for target_id in fold.training_target_ids if target_id not in target_by_id
    )
    if missing_targets:
        raise ValueError(
            "outer fold contains target identities absent from the authenticated dataset"
        )
    feature_by_key: dict[tuple[datetime, str], RawFeatureRow] = {}
    for row in features.rows:
        key = (row.decision_time, row.target_instrument_id)
        if key in feature_by_key:
            raise ValueError("feature dataset has duplicate stable target join keys")
        if row.latest_feature_bar_end > row.feature_data_asof:
            raise ValueError("feature evidence follows its causal cutoff")
        feature_by_key[key] = row

    joined: list[TrainingRow] = []
    for target_id in fold.training_target_ids:
        target = target_by_id[target_id]
        if target.instrument_id not in target_scope:
            continue
        if target.horizon != horizon:
            continue
        if target.return_disposition is not ReturnDisposition.VALID or target.log_return is None:
            raise ValueError(
                "authenticated outer training membership contains an invalid selected target"
            )
        if not (fold.training_start <= target.decision_time < fold.training_cutoff):
            raise ValueError(
                "selected target chronology differs from authenticated outer membership"
            )
        if experiment.holdout_range[0] <= target.decision_time < experiment.holdout_range[1]:
            raise ValueError("outer training membership contains a locked-holdout target")
        feature = feature_by_key.get((target.decision_time, target.instrument_id))
        if feature is None:
            raise ValueError("selected target has no exact feature row")
        joined.append(
            TrainingRow(
                target.target_id,
                target.decision_time,
                target.target_end_time,
                target.target_available_at,
                target.instrument_id,
                tuple(value.value for value in feature.values),
                target.log_return,
            )
        )
    if not joined:
        raise ValueError("outer fold has no selected target membership")
    return tuple(
        sorted(joined, key=lambda row: (row.decision_time, row.target_instrument_id, row.target_id))
    )


def _inner_validation_bounds(
    rows: Sequence[TrainingRow], minimum_inner_validation_rows: int
) -> tuple[datetime, datetime]:
    if not rows:
        raise ValueError("inner split requires non-empty outer membership")
    ordered = tuple(
        sorted(rows, key=lambda row: (row.decision_time, row.target_instrument_id, row.target_id))
    )
    position = max(0, len(ordered) - minimum_inner_validation_rows)
    start = ordered[position].decision_time
    end = max(row.decision_time for row in ordered) + timedelta(microseconds=1)
    if end <= start:
        end = start + timedelta(microseconds=1)
    return start, end


def _validate_selection_inputs(
    rows: tuple[TrainingRow, ...],
    schema: tuple[PreprocessingFeatureDefinition, ...],
    grid: tuple[float, ...],
    minimum_training_rows: int,
    minimum_inner_validation_rows: int,
    solver: str,
    tolerance: float,
    max_iterations: int,
    loss_policy: str,
    weighting_policy: str,
) -> None:
    if not rows or len({row.target_id for row in rows}) != len(rows):
        raise ValueError("training rows must be non-empty with unique target identities")
    if not schema or len({item.name for item in schema}) != len(schema):
        raise ValueError("feature schema must be non-empty with unique names")
    if any(len(row.features) != len(schema) for row in rows):
        raise ValueError("row features must align with the feature schema")
    if tuple(sorted(set(grid))) != grid or any(not isfinite(alpha) or alpha <= 0 for alpha in grid):
        raise ValueError("alpha grid must be unique, ascending, finite and positive")
    if minimum_training_rows <= 0 or minimum_inner_validation_rows <= 0:
        raise ValueError("row thresholds must be positive")
    if solver != "lsqr" or not isfinite(tolerance) or tolerance <= 0 or max_iterations <= 0:
        raise ValueError("unsupported or invalid configured Ridge parameters")
    if loss_policy != _SUPPORTED_LOSS:
        raise ValueError("unsupported configured alpha-selection loss")
    if weighting_policy != _SUPPORTED_WEIGHTING:
        raise ValueError("unsupported configured pooled weighting policy")


def _weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    order = np.argsort(values, kind="stable")
    ordered_values = values[order]
    ordered_weights = weights[order]
    cutoff = float(ordered_weights.sum()) / 2
    return float(ordered_values[np.searchsorted(np.cumsum(ordered_weights), cutoff, side="left")])


def _ridge_predictions(
    alpha: float,
    x_fit: np.ndarray,
    y_fit: np.ndarray,
    x_validation: np.ndarray,
    sample_weights: Sequence[float],
    *,
    solver: str,
    tolerance: float,
    max_iterations: int,
) -> np.ndarray:
    model: Any = Ridge(
        alpha=alpha,
        solver=solver,
        tol=tolerance,
        max_iter=max_iterations,
    )
    return np.asarray(
        model.fit(x_fit, y_fit, sample_weight=np.asarray(sample_weights)).predict(x_validation),
        dtype=float,
    )


def _loss(predictions: np.ndarray, targets: np.ndarray, policy: str) -> float:
    if policy != _SUPPORTED_LOSS:
        raise ValueError("unsupported configured alpha-selection loss")
    return float(np.mean((predictions - targets) ** 2))


def _failure_scores(
    grid: tuple[float, ...],
    disposition: FitDisposition,
    failure: str,
    inner_fit: Sequence[TrainingRow],
    inner_validation: Sequence[TrainingRow],
) -> tuple[AlphaCandidateScore, ...]:
    if not inner_fit or not inner_validation:
        return ()
    return tuple(
        AlphaCandidateScore(
            alpha,
            disposition,
            None,
            failure,
            tuple(row.target_id for row in inner_fit),
            tuple(row.target_id for row in inner_validation),
        )
        for alpha in grid
    )


def _failed_selection(
    disposition: FitDisposition,
    outer: tuple[str, ...],
    inner_fit: Sequence[TrainingRow],
    inner_validation: Sequence[TrainingRow],
    purged: Sequence[TrainingRow],
    *,
    inner_preprocessing: PreprocessingFit | None = None,
    candidate_scores: tuple[AlphaCandidateScore, ...] = (),
) -> AlphaSelection:
    return AlphaSelection(
        disposition,
        outer,
        tuple(row.target_id for row in inner_fit),
        tuple(row.target_id for row in inner_validation),
        tuple(row.target_id for row in purged),
        inner_preprocessing,
        candidate_scores,
        None,
        None,
    )
