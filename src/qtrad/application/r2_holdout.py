"""Application services for the locked R2.G2 holdout workflow.

The preparation functions accept only typed outcome-blind opportunities and pre-holdout
training rows.  Realised outcomes appear only in evaluate_holdout, which
requires the immutable opened marker.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from math import isfinite, sqrt
from typing import cast

import numpy as np

from qtrad.domain.events import JsonValue
from qtrad.domain.market_data import MarketDataSourceClass
from qtrad.domain.r2_evaluation import SelectionManifest
from qtrad.domain.r2_holdout import (
    FinalFitDisposition,
    HoldoutConclusion,
    HoldoutDirection,
    HoldoutOpportunityDisposition,
    HoldoutScope,
    HoldoutTargetOpportunity,
    R2AlphaCandidateScore,
    R2FinalFit,
    R2FinalFittingPolicy,
    R2HoldoutCoverageDataset,
    R2HoldoutCoverageRow,
    R2HoldoutEvaluation,
    R2HoldoutFeatureDataset,
    R2HoldoutFeatureRow,
    R2HoldoutForecastDataset,
    R2HoldoutForecastRow,
    R2HoldoutForecastSeal,
    R2HoldoutOpenedMarker,
    R2HoldoutQuestion,
    R2HoldoutQuestionResult,
    R2HoldoutSelectionManifest,
)
from qtrad.domain.r2_readiness import EvidenceClass, ModelFamily
from qtrad.domain.time import require_utc


@dataclass(frozen=True, slots=True)
class FinalTrainingRow:
    """One pre-holdout target and its causal feature vector."""

    target_id: str
    instrument_id: str
    decision_time: datetime
    target_available_at: datetime
    features: tuple[float, ...]
    target: float

    def __post_init__(self) -> None:
        require_utc(self.decision_time, "final-training decision time")
        require_utc(self.target_available_at, "final-training target availability")
        if not self.features or any(not isfinite(value) for value in self.features):
            raise ValueError("final-training feature values must be finite and non-empty")
        if not isfinite(self.target):
            raise ValueError("final-training target must be finite")


def _ensure_selection_lineage(
    selection: R2HoldoutSelectionManifest,
    *,
    source_class: MarketDataSourceClass | None = None,
    evidence_class: EvidenceClass | None = None,
    holdout_scope: HoldoutScope | None = None,
) -> None:
    if selection.holdout_outcomes_accessed:
        raise ValueError("holdout selection is already outcome-tainted")
    if source_class is not None and selection.source_class is not source_class:
        raise ValueError("holdout source class differs from selection freeze")
    if evidence_class is not None and selection.evidence_class is not evidence_class:
        raise ValueError("holdout evidence class differs from selection freeze")
    if holdout_scope is not None and selection.holdout_scope is not holdout_scope:
        raise ValueError("holdout scope differs from selection freeze")


def _selection_values(prior_selection: SelectionManifest) -> tuple[str, ...]:
    state = prior_selection.holdout_state_verification
    if state != "PENDING_R2_H_INTEGRATION":
        raise ValueError("G2 selection freeze requires the untouched pending holdout state")
    return prior_selection.evaluated_configuration_ids


def freeze_holdout_selection(
    *,
    prior_selection: SelectionManifest,
    foundation_bundle_id: str,
    oof_bundle_id: str,
    source_class: MarketDataSourceClass,
    evidence_class: EvidenceClass,
    holdout_scope: HoldoutScope,
    final_fitting_policy: R2FinalFittingPolicy,
    questions: Sequence[R2HoldoutQuestion],
    metric_policy: Mapping[str, JsonValue],
    threshold_policy: Mapping[str, JsonValue],
    runtime_identities: Mapping[str, JsonValue],
    frozen_metadata: Mapping[str, JsonValue],
    frozen_at: datetime,
    frozen_by: str,
    control_configuration_ids: Sequence[str] | None = None,
) -> R2HoldoutSelectionManifest:
    """Create PR A from an independently verified, still-pending R2.F1 selection."""

    evaluated = _selection_values(prior_selection)
    selected = tuple(sorted(prior_selection.selected_configuration_ids))
    inferred_controls = tuple(
        sorted(set(prior_selection.holdout_comparator_configuration_ids) - set(selected))
    )
    controls = (
        tuple(sorted(control_configuration_ids))
        if control_configuration_ids is not None
        else inferred_controls
    )
    if tuple(sorted(set(controls))) != controls:
        raise ValueError("G2 control configuration IDs must be unique and ordered")
    if set(controls) != set(inferred_controls):
        raise ValueError("G2 controls differ from the independently replayed selection")
    if prior_selection.market_data_source_class is not None and (
        prior_selection.market_data_source_class is not source_class
    ):
        raise ValueError("G2 source class differs from the prior selection")
    if prior_selection.evidence_class is not evidence_class:
        raise ValueError("G2 evidence class differs from the prior selection")
    if tuple(sorted(selected + controls)) != tuple(
        sorted(prior_selection.holdout_comparator_configuration_ids)
    ):
        raise ValueError("G2 holdout configuration IDs differ from the prior selection")
    return R2HoldoutSelectionManifest.create(
        experiment_configuration_id=prior_selection.experiment_configuration_id,
        foundation_bundle_id=foundation_bundle_id,
        oof_bundle_id=oof_bundle_id,
        evaluation_report_id=prior_selection.evaluation_report_id,
        prior_selection_manifest_id=prior_selection.manifest_id,
        source_class=source_class,
        evidence_class=evidence_class,
        holdout_scope=holdout_scope,
        evaluated_configuration_ids=evaluated,
        selected_configuration_ids=selected,
        control_configuration_ids=controls,
        holdout_configuration_ids=tuple(sorted(selected + controls)),
        comparator_families=prior_selection.predeclared_comparators,
        metric_policy=metric_policy,
        threshold_policy=threshold_policy,
        final_fitting_policy=final_fitting_policy,
        questions=questions,
        holdout_range=prior_selection.holdout_range,
        experiment_count=len(evaluated),
        runtime_identities=runtime_identities,
        frozen_metadata=frozen_metadata,
        frozen_at=frozen_at,
        frozen_by=frozen_by,
    )


def materialise_r2_holdout_features(
    *,
    selection: R2HoldoutSelectionManifest,
    opportunities: Sequence[HoldoutTargetOpportunity],
    feature_schema_id: str,
    feature_set_id: str,
    observation_dataset_id: str,
    panel_dataset_id: str,
    projection: Callable[[HoldoutTargetOpportunity], R2HoldoutFeatureRow | None],
) -> R2HoldoutFeatureDataset:
    """Materialise causal features from a projection that cannot receive outcomes."""

    _ensure_selection_lineage(selection)
    ordered = tuple(
        sorted(opportunities, key=lambda item: (item.decision_time, item.opportunity_id))
    )
    if not ordered:
        raise ValueError("holdout feature preparation requires expected opportunities")
    if len({item.opportunity_id for item in ordered}) != len(ordered):
        raise ValueError("holdout opportunities must be unique")
    for opportunity in ordered:
        if not selection.holdout_range[0] <= opportunity.decision_time < selection.holdout_range[1]:
            raise ValueError("holdout opportunity lies outside the frozen range")
    expected = tuple(sorted(item.opportunity_id for item in ordered))
    unavailable: list[str] = []
    rows: list[R2HoldoutFeatureRow] = []
    for opportunity in ordered:
        if opportunity.disposition is not HoldoutOpportunityDisposition.ELIGIBLE:
            unavailable.append(opportunity.opportunity_id)
            continue
        row = projection(opportunity)
        if row is None:
            unavailable.append(opportunity.opportunity_id)
            continue
        if (
            row.opportunity_id != opportunity.opportunity_id
            or row.target_id != opportunity.target_id
            or row.instrument_id != opportunity.instrument_id
            or row.decision_time != opportunity.decision_time
        ):
            raise ValueError("outcome-blind feature projection changed opportunity identity")
        if row.feature_schema_id != feature_schema_id:
            raise ValueError("feature projection returned an unexpected schema")
        rows.append(row)
    return R2HoldoutFeatureDataset.create(
        selection_manifest_id=selection.manifest_id,
        experiment_configuration_id=selection.experiment_configuration_id,
        foundation_bundle_id=selection.foundation_bundle_id,
        observation_dataset_id=observation_dataset_id,
        panel_dataset_id=panel_dataset_id,
        feature_schema_id=feature_schema_id,
        feature_set_id=feature_set_id,
        source_class=selection.source_class,
        evidence_class=selection.evidence_class,
        holdout_scope=selection.holdout_scope,
        holdout_range=selection.holdout_range,
        expected_opportunity_ids=expected,
        unavailable_opportunity_ids=tuple(sorted(unavailable)),
        rows=tuple(rows),
    )


build_outcome_blind_holdout_features = materialise_r2_holdout_features


def _failed_final_fit(
    *,
    selection: R2HoldoutSelectionManifest,
    configuration_id: str,
    model_family: ModelFamily,
    feature_dataset_id: str,
    feature_schema_id: str,
    training_cutoff: datetime,
    training_ids: Sequence[str],
    purged_ids: Sequence[str],
    inner_fit_ids: Sequence[str],
    inner_validation_ids: Sequence[str],
    policy: R2FinalFittingPolicy,
    disposition: FinalFitDisposition,
    reason: str,
    feature_count: int,
) -> R2FinalFit:
    scores = tuple(
        R2AlphaCandidateScore(
            alpha=alpha,
            validation_loss=None,
            disposition=disposition,
            failure_reason=reason,
        )
        for alpha in policy.alpha_grid
    )
    return R2FinalFit.create(
        selection_manifest_id=selection.manifest_id,
        configuration_id=configuration_id,
        model_family=model_family,
        feature_dataset_id=feature_dataset_id,
        feature_schema_id=feature_schema_id,
        training_cutoff=training_cutoff,
        training_target_ids=tuple(sorted(training_ids)),
        purged_target_ids=tuple(sorted(purged_ids)),
        inner_fit_target_ids=tuple(sorted(inner_fit_ids)),
        inner_validation_target_ids=tuple(sorted(inner_validation_ids)),
        preprocessing={"feature_count": feature_count, "policy_id": policy.policy_id},
        alpha_candidate_scores=scores,
        selected_alpha=None,
        sample_weights=tuple((target_id, 1.0) for target_id in sorted(training_ids)),
        coefficients=None,
        intercept=None,
        disposition=disposition,
        failure_reason=reason,
        diagnostics={"failure": reason},
        runtime_identities=policy.runtime_identities,
        evidence_class=selection.evidence_class,
        holdout_scope=selection.holdout_scope,
    )


def _ridge_parameters(
    x: np.ndarray,
    y: np.ndarray,
    alpha: float,
) -> tuple[np.ndarray, float]:
    design = np.column_stack((np.ones(x.shape[0]), x))
    penalty = np.eye(design.shape[1], dtype=np.float64)
    penalty[0, 0] = 0.0
    lhs = design.T @ design + alpha * penalty
    rhs = design.T @ y
    solution = np.linalg.solve(lhs, rhs)
    return solution[1:], float(solution[0])


def fit_final_ridge(
    *,
    selection: R2HoldoutSelectionManifest,
    configuration_id: str,
    model_family: ModelFamily,
    feature_dataset_id: str,
    feature_schema_id: str,
    training_rows: Sequence[FinalTrainingRow],
    policy: R2FinalFittingPolicy,
    training_cutoff: datetime | None = None,
    minimum_training_rows: int = 2,
    minimum_inner_validation_rows: int = 1,
    purged_target_ids: Sequence[str] = (),
    forced_disposition: FinalFitDisposition | None = None,
    forced_failure_reason: str | None = None,
) -> R2FinalFit:
    """Fit one final model using only targets mature before the locked holdout."""

    _ensure_selection_lineage(selection)
    cutoff = training_cutoff or selection.holdout_range[0]
    require_utc(cutoff, "final-fit training cutoff")
    if cutoff > selection.holdout_range[0]:
        raise ValueError("final-fit training cutoff cannot enter the holdout")
    ordered = tuple(sorted(training_rows, key=lambda item: (item.decision_time, item.target_id)))
    if len({item.target_id for item in ordered}) != len(ordered):
        raise ValueError("final training targets must be unique")
    for row in ordered:
        if row.decision_time >= selection.holdout_range[0] or row.target_available_at > cutoff:
            raise ValueError("final fit received an immature or holdout target")
    training_ids = tuple(sorted(item.target_id for item in ordered))
    purged = tuple(sorted(purged_target_ids))
    if set(purged) & set(training_ids):
        raise ValueError("purged target is present in final training rows")
    feature_count = len(ordered[0].features) if ordered else 0
    inner_fit_count = max(0, len(ordered) - max(minimum_inner_validation_rows, len(ordered) // 3))
    inner_fit_rows = ordered[:inner_fit_count]
    inner_validation_rows = ordered[inner_fit_count:]
    if forced_disposition is not None:
        return _failed_final_fit(
            selection=selection,
            configuration_id=configuration_id,
            model_family=model_family,
            feature_dataset_id=feature_dataset_id,
            feature_schema_id=feature_schema_id,
            training_cutoff=cutoff,
            training_ids=training_ids,
            purged_ids=purged,
            inner_fit_ids=tuple(item.target_id for item in inner_fit_rows),
            inner_validation_ids=tuple(item.target_id for item in inner_validation_rows),
            policy=policy,
            disposition=forced_disposition,
            reason=forced_failure_reason or "forced fixture failure disposition",
            feature_count=feature_count,
        )
    if len(ordered) < minimum_training_rows:
        return _failed_final_fit(
            selection=selection,
            configuration_id=configuration_id,
            model_family=model_family,
            feature_dataset_id=feature_dataset_id,
            feature_schema_id=feature_schema_id,
            training_cutoff=cutoff,
            training_ids=training_ids,
            purged_ids=purged,
            inner_fit_ids=tuple(item.target_id for item in inner_fit_rows),
            inner_validation_ids=tuple(item.target_id for item in inner_validation_rows),
            policy=policy,
            disposition=FinalFitDisposition.INSUFFICIENT_TRAINING,
            reason="pre-holdout training membership is below the declared minimum",
            feature_count=feature_count,
        )
    if len(inner_validation_rows) < minimum_inner_validation_rows or not inner_fit_rows:
        return _failed_final_fit(
            selection=selection,
            configuration_id=configuration_id,
            model_family=model_family,
            feature_dataset_id=feature_dataset_id,
            feature_schema_id=feature_schema_id,
            training_cutoff=cutoff,
            training_ids=training_ids,
            purged_ids=purged,
            inner_fit_ids=tuple(item.target_id for item in inner_fit_rows),
            inner_validation_ids=tuple(item.target_id for item in inner_validation_rows),
            policy=policy,
            disposition=FinalFitDisposition.INSUFFICIENT_INNER_VALIDATION,
            reason="inner chronological validation membership is below the declared minimum",
            feature_count=feature_count,
        )
    matrix = np.asarray([row.features for row in ordered], dtype=np.float64)
    targets = np.asarray([row.target for row in ordered], dtype=np.float64)
    if not np.isfinite(matrix).all() or not np.isfinite(targets).all():
        return _failed_final_fit(
            selection=selection,
            configuration_id=configuration_id,
            model_family=model_family,
            feature_dataset_id=feature_dataset_id,
            feature_schema_id=feature_schema_id,
            training_cutoff=cutoff,
            training_ids=training_ids,
            purged_ids=purged,
            inner_fit_ids=tuple(item.target_id for item in inner_fit_rows),
            inner_validation_ids=tuple(item.target_id for item in inner_validation_rows),
            policy=policy,
            disposition=FinalFitDisposition.NON_FINITE_MATRIX,
            reason="pre-holdout training matrix contains a non-finite value",
            feature_count=feature_count,
        )
    if np.allclose(targets, targets[0]):
        return _failed_final_fit(
            selection=selection,
            configuration_id=configuration_id,
            model_family=model_family,
            feature_dataset_id=feature_dataset_id,
            feature_schema_id=feature_schema_id,
            training_cutoff=cutoff,
            training_ids=training_ids,
            purged_ids=purged,
            inner_fit_ids=tuple(item.target_id for item in inner_fit_rows),
            inner_validation_ids=tuple(item.target_id for item in inner_validation_rows),
            policy=policy,
            disposition=FinalFitDisposition.DEGENERATE_TARGET,
            reason="pre-holdout target is constant",
            feature_count=feature_count,
        )
    means = matrix.mean(axis=0)
    scales = matrix.std(axis=0)
    scales = np.where(scales == 0.0, 1.0, scales)
    normalised = (matrix - means) / scales
    inner_x = normalised[:inner_fit_count]
    inner_y = targets[:inner_fit_count]
    validation_x = normalised[inner_fit_count:]
    validation_y = targets[inner_fit_count:]
    scores: list[R2AlphaCandidateScore] = []
    for alpha in policy.alpha_grid:
        try:
            coefficients, intercept = _ridge_parameters(inner_x, inner_y, alpha)
            predicted = validation_x @ coefficients + intercept
            loss = float(np.mean((predicted - validation_y) ** 2))
            scores.append(
                R2AlphaCandidateScore(
                    alpha=alpha,
                    validation_loss=loss,
                    disposition=FinalFitDisposition.READY,
                    failure_reason=None,
                )
            )
        except np.linalg.LinAlgError:
            scores.append(
                R2AlphaCandidateScore(
                    alpha=alpha,
                    validation_loss=None,
                    disposition=FinalFitDisposition.NUMERICAL_FAILURE,
                    failure_reason="ridge solve failed during inner validation",
                )
            )
    ready_scores = [item for item in scores if item.disposition is FinalFitDisposition.READY]
    if not ready_scores:
        return _failed_final_fit(
            selection=selection,
            configuration_id=configuration_id,
            model_family=model_family,
            feature_dataset_id=feature_dataset_id,
            feature_schema_id=feature_schema_id,
            training_cutoff=cutoff,
            training_ids=training_ids,
            purged_ids=purged,
            inner_fit_ids=tuple(item.target_id for item in inner_fit_rows),
            inner_validation_ids=tuple(item.target_id for item in inner_validation_rows),
            policy=policy,
            disposition=FinalFitDisposition.NUMERICAL_FAILURE,
            reason="all frozen alpha candidates failed during inner validation",
            feature_count=feature_count,
        )
    selected = min(ready_scores, key=lambda item: (cast(float, item.validation_loss), item.alpha))
    if model_family is ModelFamily.ZERO_RETURN:
        coefficients = np.zeros(feature_count, dtype=np.float64)
        intercept = 0.0
        selected_alpha = selected.alpha
    else:
        try:
            coefficients, intercept = _ridge_parameters(normalised, targets, selected.alpha)
            selected_alpha = selected.alpha
        except np.linalg.LinAlgError:
            return _failed_final_fit(
                selection=selection,
                configuration_id=configuration_id,
                model_family=model_family,
                feature_dataset_id=feature_dataset_id,
                feature_schema_id=feature_schema_id,
                training_cutoff=cutoff,
                training_ids=training_ids,
                purged_ids=purged,
                inner_fit_ids=tuple(item.target_id for item in inner_fit_rows),
                inner_validation_ids=tuple(item.target_id for item in inner_validation_rows),
                policy=policy,
                disposition=FinalFitDisposition.NUMERICAL_FAILURE,
                reason="ridge solve failed during final fit",
                feature_count=feature_count,
            )
    return R2FinalFit.create(
        selection_manifest_id=selection.manifest_id,
        configuration_id=configuration_id,
        model_family=model_family,
        feature_dataset_id=feature_dataset_id,
        feature_schema_id=feature_schema_id,
        training_cutoff=cutoff,
        training_target_ids=training_ids,
        purged_target_ids=purged,
        inner_fit_target_ids=tuple(item.target_id for item in inner_fit_rows),
        inner_validation_target_ids=tuple(item.target_id for item in inner_validation_rows),
        preprocessing={
            "policy_id": policy.policy_id,
            "means": means.tolist(),
            "scales": scales.tolist(),
            "feature_count": feature_count,
            "training_prediction_threshold": policy.training_prediction_threshold,
        },
        alpha_candidate_scores=tuple(scores),
        selected_alpha=selected_alpha,
        sample_weights=tuple((item.target_id, 1.0) for item in ordered),
        coefficients=tuple(float(item) for item in coefficients),
        intercept=float(intercept),
        disposition=FinalFitDisposition.READY,
        failure_reason=None,
        diagnostics={
            "training_count": len(ordered),
            "inner_fit_count": len(inner_fit_rows),
            "inner_validation_count": len(inner_validation_rows),
            "selected_validation_loss": selected.validation_loss,
        },
        runtime_identities=policy.runtime_identities,
        evidence_class=selection.evidence_class,
        holdout_scope=selection.holdout_scope,
    )


build_final_fit = fit_final_ridge


def build_holdout_forecasts(
    *,
    selection: R2HoldoutSelectionManifest,
    feature_dataset: R2HoldoutFeatureDataset,
    final_fits: Sequence[R2FinalFit],
) -> tuple[R2HoldoutForecastDataset, ...]:
    _ensure_selection_lineage(selection)
    if feature_dataset.selection_manifest_id != selection.manifest_id:
        raise ValueError("holdout feature dataset is not bound to selection")
    result: list[R2HoldoutForecastDataset] = []
    for fit in sorted(final_fits, key=lambda item: item.configuration_id):
        if (
            fit.selection_manifest_id != selection.manifest_id
            or fit.feature_dataset_id != feature_dataset.dataset_id
        ):
            raise ValueError("final fit is not bound to the prepared holdout features")
        rows: list[R2HoldoutForecastRow] = []
        if fit.disposition is FinalFitDisposition.READY:
            means = np.asarray(cast(list[float], fit.preprocessing["means"]), dtype=np.float64)
            scales = np.asarray(cast(list[float], fit.preprocessing["scales"]), dtype=np.float64)
            coefficients = np.asarray(cast(tuple[float, ...], fit.coefficients), dtype=np.float64)
            intercept = cast(float, fit.intercept)
            for feature in feature_dataset.rows:
                values = (np.asarray(feature.values, dtype=np.float64) - means) / scales
                forecast = float(values @ coefficients + intercept)
                rows.append(
                    R2HoldoutForecastRow.create(
                        configuration_id=fit.configuration_id,
                        target_id=feature.target_id,
                        feature_row_id=feature.row_id,
                        forecast=forecast,
                        model_family=fit.model_family,
                    )
                )
        result.append(
            R2HoldoutForecastDataset.create(
                selection_manifest_id=selection.manifest_id,
                feature_dataset_id=feature_dataset.dataset_id,
                configuration_id=fit.configuration_id,
                final_fit_id=fit.fit_id,
                rows=tuple(rows),
                expected_opportunity_ids=feature_dataset.expected_opportunity_ids,
                source_class=selection.source_class,
                evidence_class=selection.evidence_class,
                holdout_scope=selection.holdout_scope,
            )
        )
    return tuple(result)


def build_holdout_coverage(
    *,
    selection: R2HoldoutSelectionManifest,
    feature_dataset: R2HoldoutFeatureDataset,
    final_fit: R2FinalFit,
    forecast_dataset: R2HoldoutForecastDataset,
    opportunities: Sequence[HoldoutTargetOpportunity],
) -> R2HoldoutCoverageDataset:
    _ensure_selection_lineage(selection)
    if final_fit.configuration_id != forecast_dataset.configuration_id:
        raise ValueError("coverage fit and forecast configuration differ")
    if forecast_dataset.feature_dataset_id != feature_dataset.dataset_id:
        raise ValueError("coverage forecast is not bound to features")
    opportunity_map = {item.opportunity_id: item for item in opportunities}
    if set(opportunity_map) != set(feature_dataset.expected_opportunity_ids):
        raise ValueError("coverage opportunities differ from feature preparation")
    forecast_by_target = {item.target_id: item for item in forecast_dataset.rows}
    feature_by_opportunity = {item.opportunity_id: item for item in feature_dataset.rows}
    rows: list[R2HoldoutCoverageRow] = []
    for opportunity_id in feature_dataset.expected_opportunity_ids:
        opportunity = opportunity_map[opportunity_id]
        if opportunity_id in set(feature_dataset.unavailable_opportunity_ids):
            disposition = (
                opportunity.disposition
                if opportunity.disposition is not HoldoutOpportunityDisposition.ELIGIBLE
                else HoldoutOpportunityDisposition.UNAVAILABLE_FEATURE
            )
            reason = "feature opportunity unavailable before forecast generation"
            forecast_row_id = None
        elif final_fit.disposition is not FinalFitDisposition.READY:
            disposition = HoldoutOpportunityDisposition.FAILED_CONFIGURATION
            reason = f"final fit disposition {final_fit.disposition.value}"
            forecast_row_id = None
        else:
            feature = feature_by_opportunity.get(opportunity_id)
            forecast = forecast_by_target.get(feature.target_id) if feature is not None else None
            if forecast is None:
                disposition = HoldoutOpportunityDisposition.GAP
                reason = "eligible opportunity has no sealed forecast row"
                forecast_row_id = None
            else:
                disposition = HoldoutOpportunityDisposition.ELIGIBLE
                reason = "forecast row sealed"
                forecast_row_id = forecast.row_id
        rows.append(
            R2HoldoutCoverageRow(
                configuration_id=final_fit.configuration_id,
                opportunity_id=opportunity_id,
                disposition=disposition,
                forecast_row_id=forecast_row_id,
                reason=reason,
            )
        )
    return R2HoldoutCoverageDataset.create(
        selection_manifest_id=selection.manifest_id,
        feature_dataset_id=feature_dataset.dataset_id,
        configuration_id=final_fit.configuration_id,
        expected_opportunity_ids=feature_dataset.expected_opportunity_ids,
        rows=tuple(rows),
        source_class=selection.source_class,
        evidence_class=selection.evidence_class,
        holdout_scope=selection.holdout_scope,
    )


def seal_holdout_forecasts(
    *,
    selection: R2HoldoutSelectionManifest,
    feature_dataset: R2HoldoutFeatureDataset,
    final_fits: Sequence[R2FinalFit],
    forecast_datasets: Sequence[R2HoldoutForecastDataset],
    coverage_datasets: Sequence[R2HoldoutCoverageDataset],
    metric_policy: Mapping[str, JsonValue],
    comparison_support: Mapping[str, JsonValue],
    forecast_buckets: Mapping[str, JsonValue],
    state_buckets: Mapping[str, JsonValue],
    coverage_rules: Mapping[str, JsonValue],
    prepared_at: datetime,
    prepared_by: str,
) -> R2HoldoutForecastSeal:
    _ensure_selection_lineage(selection)
    fits = tuple(sorted(final_fits, key=lambda item: item.fit_id))
    forecasts = tuple(sorted(forecast_datasets, key=lambda item: item.dataset_id))
    coverage = tuple(sorted(coverage_datasets, key=lambda item: item.coverage_id))
    if not fits or len(fits) != len(forecasts) or len(fits) != len(coverage):
        raise ValueError("seal requires one forecast and coverage child per final fit")
    if any(item.selection_manifest_id != selection.manifest_id for item in forecasts + coverage):
        raise ValueError("seal child selection lineage differs")
    pairs = tuple(
        sorted(
            (question.candidate_configuration_id, question.comparator_configuration_id)
            for question in selection.questions
        )
    )
    if not pairs:
        raise ValueError("selection question register has no configuration pairs")
    return R2HoldoutForecastSeal.create(
        selection_manifest_id=selection.manifest_id,
        feature_dataset_id=feature_dataset.dataset_id,
        final_fit_ids=tuple(item.fit_id for item in fits),
        forecast_dataset_ids=tuple(item.dataset_id for item in forecasts),
        coverage_ids=tuple(item.coverage_id for item in coverage),
        metric_policy=metric_policy,
        comparison_support=comparison_support,
        forecast_buckets=forecast_buckets,
        state_buckets=state_buckets,
        configuration_pairs=pairs,
        coverage_rules=coverage_rules,
        questions=selection.questions,
        source_class=selection.source_class,
        evidence_class=selection.evidence_class,
        holdout_scope=selection.holdout_scope,
        runtime_identities=selection.runtime_identities,
        prepared_at=prepared_at,
        prepared_by=prepared_by,
    )


def _metric(
    metric: str,
    predictions: Sequence[float],
    outcomes: Sequence[float],
) -> float:
    if not predictions:
        raise ValueError("metric requires non-empty support")
    if metric.upper() == "MSE":
        return float(np.mean((np.asarray(predictions) - np.asarray(outcomes)) ** 2))
    if metric.upper() == "RMSE":
        return sqrt(_metric("MSE", predictions, outcomes))
    if metric.upper() in {"MEAN_RETURN", "MEAN_FORECAST"}:
        return float(np.mean(np.asarray(predictions)))
    raise ValueError(f"unsupported frozen holdout metric: {metric}")


def evaluate_holdout(
    *,
    selection: R2HoldoutSelectionManifest,
    seal: R2HoldoutForecastSeal,
    opened_marker: R2HoldoutOpenedMarker,
    forecast_datasets: Sequence[R2HoldoutForecastDataset],
    coverage_datasets: Sequence[R2HoldoutCoverageDataset],
    outcomes: Mapping[str, float],
) -> R2HoldoutEvaluation:
    """Evaluate every frozen question; this is the only outcome-consuming function."""

    if opened_marker.seal_id != seal.seal_id:
        raise ValueError("holdout evaluation requires the exact opened seal")
    if opened_marker.selection_manifest_id != selection.manifest_id:
        raise ValueError("holdout evaluation selection differs from opened marker")
    if tuple(item.question_id for item in seal.questions) != tuple(
        item.question_id for item in selection.questions
    ):
        raise ValueError("holdout question policy changed after preparation")
    forecast_by_configuration = {item.configuration_id: item for item in forecast_datasets}
    coverage_by_configuration = {item.configuration_id: item for item in coverage_datasets}
    results: list[R2HoldoutQuestionResult] = []
    for question in seal.questions:
        candidate_forecast = forecast_by_configuration.get(question.candidate_configuration_id)
        comparator_forecast = forecast_by_configuration.get(question.comparator_configuration_id)
        candidate_coverage = coverage_by_configuration.get(question.candidate_configuration_id)
        comparator_coverage = coverage_by_configuration.get(question.comparator_configuration_id)
        if (
            candidate_forecast is None
            or comparator_forecast is None
            or candidate_coverage is None
            or comparator_coverage is None
        ):
            results.append(
                R2HoldoutQuestionResult(
                    question_id=question.question_id,
                    metric=question.metric,
                    candidate_value=None,
                    comparator_value=None,
                    delta=None,
                    support_count=0,
                    coverage=0.0,
                    conclusion=HoldoutConclusion.INCONCLUSIVE,
                    reason="frozen configuration pair has incomplete coverage children",
                )
            )
            continue
        candidate_rows = {item.target_id: item for item in candidate_forecast.rows}
        comparator_rows = {item.target_id: item for item in comparator_forecast.rows}
        candidate_coverage_rows = {item.opportunity_id: item for item in candidate_coverage.rows}
        comparator_coverage_rows = {item.opportunity_id: item for item in comparator_coverage.rows}
        expected = set(candidate_coverage.expected_opportunity_ids)
        expected &= set(comparator_coverage.expected_opportunity_ids)
        supported_targets: list[tuple[float, float, float]] = []
        for opportunity_id in sorted(expected):
            candidate_coverage_row = candidate_coverage_rows[opportunity_id]
            comparator_coverage_row = comparator_coverage_rows[opportunity_id]
            if (
                candidate_coverage_row.disposition is not HoldoutOpportunityDisposition.ELIGIBLE
                or comparator_coverage_row.disposition is not HoldoutOpportunityDisposition.ELIGIBLE
            ):
                continue
            candidate_feature_id = candidate_coverage_row.forecast_row_id
            comparator_feature_id = comparator_coverage_row.forecast_row_id
            if candidate_feature_id is None or comparator_feature_id is None:
                continue
            candidate = next(
                (item for item in candidate_rows.values() if item.row_id == candidate_feature_id),
                None,
            )
            comparator = next(
                (item for item in comparator_rows.values() if item.row_id == comparator_feature_id),
                None,
            )
            opportunity_target = next(
                (
                    item.target_id
                    for item in candidate_rows.values()
                    if item.row_id == candidate_feature_id
                ),
                None,
            )
            if candidate is None or comparator is None or opportunity_target is None:
                continue
            if opportunity_target not in outcomes:
                continue
            supported_targets.append(
                (candidate.forecast, comparator.forecast, float(outcomes[opportunity_target]))
            )
        support_count = len(supported_targets)
        coverage = support_count / len(expected) if expected else 0.0
        if support_count < question.minimum_support or coverage < question.minimum_coverage:
            results.append(
                R2HoldoutQuestionResult(
                    question_id=question.question_id,
                    metric=question.metric,
                    candidate_value=None,
                    comparator_value=None,
                    delta=None,
                    support_count=support_count,
                    coverage=coverage,
                    conclusion=HoldoutConclusion.INCONCLUSIVE,
                    reason="frozen support or coverage minimum was not met",
                )
            )
            continue
        candidate_values = [item[0] for item in supported_targets]
        comparator_values = [item[1] for item in supported_targets]
        target_values = [item[2] for item in supported_targets]
        candidate_metric = _metric(question.metric, candidate_values, target_values)
        comparator_metric = _metric(question.metric, comparator_values, target_values)
        delta = candidate_metric - comparator_metric
        if question.direction is HoldoutDirection.HIGHER_IS_BETTER:
            positive = delta >= question.threshold
            negative = delta <= -question.threshold
        else:
            positive = delta <= -question.threshold
            negative = delta >= question.threshold
        conclusion = (
            HoldoutConclusion.POSITIVE
            if positive
            else HoldoutConclusion.NEGATIVE
            if negative
            else HoldoutConclusion.INCONCLUSIVE
        )
        results.append(
            R2HoldoutQuestionResult(
                question_id=question.question_id,
                metric=question.metric,
                candidate_value=candidate_metric,
                comparator_value=comparator_metric,
                delta=delta,
                support_count=support_count,
                coverage=coverage,
                conclusion=conclusion,
                reason="frozen metric and direction policy applied",
            )
        )
    return R2HoldoutEvaluation.create(
        selection_manifest_id=selection.manifest_id,
        seal_id=seal.seal_id,
        opened_marker_id=opened_marker.marker_id,
        consumed_marker_id="0" * 64,
        questions=tuple(results),
        source_class=selection.source_class,
        evidence_class=selection.evidence_class,
        holdout_scope=selection.holdout_scope,
    )
