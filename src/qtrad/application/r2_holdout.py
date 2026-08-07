"""Application services for the locked R2.G2 holdout workflow.

The preparation functions accept only typed outcome-blind opportunities and pre-holdout
training rows.  Realised outcomes appear only in evaluate_holdout, which
requires the immutable opened marker.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from math import isfinite, sqrt
from typing import Any, cast

import numpy as np

from qtrad.domain.events import JsonValue
from qtrad.domain.market_data import MarketDataSourceClass
from qtrad.domain.r2_bundles import R2OofBundle
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
from qtrad.domain.r2_models import PreprocessingFit, R2PreprocessingSchema
from qtrad.domain.r2_readiness import EvidenceClass, ModelFamily, R2ExperimentConfig
from qtrad.domain.time import require_utc


@dataclass(frozen=True, slots=True)
class FinalTrainingRow:
    """One pre-holdout target and its causal feature vector."""

    target_id: str
    instrument_id: str
    decision_time: datetime
    target_available_at: datetime
    features: tuple[float | None, ...]
    target: float
    target_end_time: datetime | None = None

    def __post_init__(self) -> None:
        require_utc(self.decision_time, "final-training decision time")
        require_utc(self.target_available_at, "final-training target availability")
        if self.target_end_time is not None:
            require_utc(self.target_end_time, "final-training target endpoint")
            if self.target_end_time < self.decision_time:
                raise ValueError("final-training target endpoint cannot precede its decision")
        if not self.features or any(
            value is not None and not isfinite(value) for value in self.features
        ):
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


def _final_fit_lineage(
    selection: R2HoldoutSelectionManifest,
    *,
    configuration_id: str,
    model_family: ModelFamily,
    feature_dataset_id: str,
    feature_schema_id: str,
) -> dict[str, JsonValue]:
    return {
        "selection_manifest_id": selection.manifest_id,
        "experiment_configuration_id": selection.experiment_configuration_id,
        "foundation_bundle_id": selection.foundation_bundle_id,
        "configuration_id": configuration_id,
        "model_family": model_family.value,
        "feature_dataset_id": feature_dataset_id,
        "feature_schema_id": feature_schema_id,
    }


def _verify_final_policy_against_experiment(
    *,
    policy: R2FinalFittingPolicy,
    experiment: R2ExperimentConfig,
    verified_oof_bundle: R2OofBundle,
    prior_selection: SelectionManifest,
    foundation_bundle_id: str,
    source_class: MarketDataSourceClass,
    evidence_class: EvidenceClass,
) -> None:
    if (
        experiment.r1_bundle_id != foundation_bundle_id
        or experiment.holdout_range != prior_selection.holdout_range
        or experiment.market_data_source_class is not source_class
        or experiment.evidence_class is not evidence_class
    ):
        raise ValueError("verified experiment lineage differs from the OOF selection inputs")
    if tuple(policy.alpha_grid) != tuple(experiment.alpha_grid):
        raise ValueError("final-fitting alpha grid differs from the verified experiment")
    if policy.inner_validation_policy != experiment.inner_validation_policy:
        raise ValueError("final-fitting validation policy differs from the verified experiment")
    if (
        _policy_value(
            policy,
            "preprocessing_policy",
            {"TRAINING_MEDIAN_STANDARDISE": "TRAINING_MEDIAN_STANDARDISE_V1"},
        )
        != experiment.preprocessing_policy
    ):
        raise ValueError("final-fitting preprocessing differs from the verified experiment")
    if (
        _policy_value(
            policy,
            "pooled_weighting_policy",
            {"EQUAL_INSTRUMENT": "EQUAL_INSTRUMENT_TOTAL_WEIGHT_MEAN_ONE"},
        )
        != experiment.pooled_weighting_policy
    ):
        raise ValueError("final-fitting weighting differs from the verified experiment")
    solver_name = str(policy.solver_identity.get("name", ""))
    solver_name = {"numpy-ridge": "lsqr"}.get(solver_name, solver_name)
    if solver_name != experiment.ridge_solver:
        raise ValueError("final-fitting solver differs from the verified experiment")
    tolerance = float(cast(float | int | str, policy.solver_identity.get("tolerance", 1e-8)))
    max_iterations = int(
        cast(float | int | str, policy.solver_identity.get("max_iterations", 1000))
    )
    if tolerance != experiment.ridge_tolerance or max_iterations != experiment.ridge_max_iterations:
        raise ValueError("final-fitting solver controls differ from the verified experiment")
    if policy.alpha_tie_break_policy not in {"LOSS_THEN_LARGER_ALPHA"}:
        raise ValueError("verified final fitting requires the larger-alpha tie break")
    if (
        verified_oof_bundle.experiment_configuration_id
        != prior_selection.experiment_configuration_id
    ):
        raise ValueError("verified OOF bundle differs from the prior experiment configuration")


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
    verified_oof_bundle: R2OofBundle | None = None,
    verified_experiment: R2ExperimentConfig | None = None,
) -> R2HoldoutSelectionManifest:
    """Create PR A from an independently verified, still-pending R2.F1 selection."""
    if verified_oof_bundle is not None:
        if (
            verified_oof_bundle.experiment_configuration_id
            != prior_selection.experiment_configuration_id
            or verified_oof_bundle.source_class is not source_class
            or verified_oof_bundle.evidence_class is not evidence_class
        ):
            raise ValueError("verified OOF lineage differs from the prior selection inputs")
        if foundation_bundle_id != verified_oof_bundle.foundation_bundle_id:
            raise ValueError("foundation ID differs from the verified OOF bundle")
        if oof_bundle_id != verified_oof_bundle.bundle_id:
            raise ValueError("OOF ID differs from the verified OOF bundle")
        if (
            prior_selection.foundation_bundle_id is not None
            and prior_selection.foundation_bundle_id != verified_oof_bundle.foundation_bundle_id
        ):
            raise ValueError("verified foundation ID differs from the prior selection")
        if (
            prior_selection.oof_bundle_id is not None
            and prior_selection.oof_bundle_id != verified_oof_bundle.bundle_id
        ):
            raise ValueError("verified OOF ID differs from the prior selection")
        foundation_bundle_id = verified_oof_bundle.foundation_bundle_id
        oof_bundle_id = verified_oof_bundle.bundle_id
        if verified_experiment is None:
            raise ValueError("verified OOF selection freeze requires the verified experiment")
        _verify_final_policy_against_experiment(
            policy=final_fitting_policy,
            experiment=verified_experiment,
            verified_oof_bundle=verified_oof_bundle,
            prior_selection=prior_selection,
            foundation_bundle_id=foundation_bundle_id,
            source_class=source_class,
            evidence_class=evidence_class,
        )
        metric_policy = {
            "primary_metric": prior_selection.primary_metric,
            "secondary_metrics": list(prior_selection.secondary_metrics),
        }
        threshold_policy = {
            "acceptance_thresholds": [
                [key, value] for key, value in prior_selection.acceptance_thresholds
            ],
        }
        runtime_identities = {
            "application_image_identity": prior_selection.application_image_identity,
            "foundation_bundle_id": foundation_bundle_id,
            "oof_bundle_id": oof_bundle_id,
            "final_fitting_policy_id": final_fitting_policy.policy_id,
        }

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
    for question in questions:
        if (
            question.candidate_configuration_id not in selected
            or question.comparator_configuration_id not in controls
        ):
            raise ValueError(
                "holdout question references a configuration outside the frozen registry"
            )
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
    training_evidence: Sequence[FinalTrainingRow] = (),
    candidate_scores: Sequence[R2AlphaCandidateScore] | None = None,
    preprocessing: Mapping[str, JsonValue] | None = None,
    forced_failure: bool = False,
) -> R2FinalFit:
    scores = (
        tuple(candidate_scores)
        if candidate_scores is not None
        else tuple(
            R2AlphaCandidateScore(
                alpha=alpha,
                validation_loss=None,
                disposition=disposition,
                failure_reason=reason,
            )
            for alpha in policy.alpha_grid
        )
    )
    fit_preprocessing = dict(
        preprocessing or {"feature_count": feature_count, "policy_id": policy.policy_id}
    )
    fit_preprocessing.setdefault(
        "training_rows", [_training_row_json(row) for row in training_evidence]
    )
    fit_preprocessing.update(
        _final_fit_lineage(
            selection,
            configuration_id=configuration_id,
            model_family=model_family,
            feature_dataset_id=feature_dataset_id,
            feature_schema_id=feature_schema_id,
        )
    )
    if forced_failure:
        fit_preprocessing["failure_mode"] = "FORCED_FIXTURE"
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
        preprocessing=fit_preprocessing,
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


def _training_row_json(row: FinalTrainingRow) -> dict[str, JsonValue]:
    return {
        "target_id": row.target_id,
        "instrument_id": row.instrument_id,
        "decision_time": row.decision_time.isoformat(),
        "target_end_time": (row.target_end_time or row.decision_time).isoformat(),
        "target_available_at": row.target_available_at.isoformat(),
        "features": list(row.features),
        "target": row.target,
    }


def _final_disposition(value: object) -> FinalFitDisposition:
    try:
        return FinalFitDisposition(str(getattr(value, "value", value)))
    except ValueError:
        return FinalFitDisposition.NUMERICAL_FAILURE


def _holdout_schema(feature_count: int) -> R2PreprocessingSchema:
    from qtrad.domain.r2_models import PreprocessingFeatureDefinition, PreprocessingFeatureKind

    return R2PreprocessingSchema.create(
        tuple(
            PreprocessingFeatureDefinition(f"feature_{index}", PreprocessingFeatureKind.CONTINUOUS)
            for index in range(feature_count)
        )
    )


def _as_r2_training_row(row: FinalTrainingRow):
    from qtrad.application.r2_preprocessing import TrainingRow

    return TrainingRow(
        row.target_id,
        row.decision_time,
        row.target_end_time or row.decision_time,
        row.target_available_at,
        row.instrument_id,
        row.features,
        row.target,
    )


def _policy_value(policy: R2FinalFittingPolicy, key: str, aliases: Mapping[str, str]) -> str:
    value = str(getattr(policy, key))
    return aliases.get(value, value)


def _preprocessing_payload(
    *,
    policy: R2FinalFittingPolicy,
    schema: R2PreprocessingSchema,
    inner: PreprocessingFit | None,
    outer: PreprocessingFit | None,
    rows: Sequence[FinalTrainingRow],
    identity_order: Sequence[str],
    solver: str,
    weighting_policy: str,
    fit_intercept: bool,
    minimum_training_rows: int,
    minimum_inner_validation_rows: int,
) -> dict[str, JsonValue]:
    return {
        "policy_id": policy.policy_id,
        "schema": schema.as_json(),
        "inner": inner.as_json() if inner is not None else None,
        "outer": outer.as_json() if outer is not None else None,
        "training_rows": [_training_row_json(row) for row in rows],
        "instrument_identity_order": list(identity_order),
        "ridge_solver": solver,
        "ridge_tolerance": policy.solver_identity.get("tolerance", 1e-8),
        "ridge_max_iterations": policy.solver_identity.get("max_iterations", 1000),
        "pooled_weighting_policy": weighting_policy,
        "fit_intercept": fit_intercept,
        "minimum_training_rows": minimum_training_rows,
        "minimum_inner_validation_rows": minimum_inner_validation_rows,
    }


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
    """Fit through the authenticated R2 preprocessing and Ridge primitives."""
    from qtrad.application.r2_preprocessing import (
        add_instrument_identity,
        select_chronological_alpha,
        transform,
    )

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
    inner_fit_ids: tuple[str, ...] = ()
    inner_validation_ids: tuple[str, ...] = ()
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
            inner_fit_ids=inner_fit_ids,
            inner_validation_ids=inner_validation_ids,
            policy=policy,
            disposition=forced_disposition,
            reason=forced_failure_reason or "forced fixture failure disposition",
            feature_count=feature_count,
            training_evidence=ordered,
            forced_failure=True,
        )
    if not ordered:
        return _failed_final_fit(
            selection=selection,
            configuration_id=configuration_id,
            model_family=model_family,
            feature_dataset_id=feature_dataset_id,
            feature_schema_id=feature_schema_id,
            training_cutoff=cutoff,
            training_ids=(),
            purged_ids=purged,
            inner_fit_ids=(),
            inner_validation_ids=(),
            policy=policy,
            disposition=FinalFitDisposition.INSUFFICIENT_TRAINING,
            reason="pre-holdout training membership is empty",
            feature_count=feature_count,
            training_evidence=ordered,
        )
    schema = _holdout_schema(feature_count)
    r2_rows = tuple(_as_r2_training_row(row) for row in ordered)
    solver_name = str(policy.solver_identity.get("name", ""))
    solver = "lsqr" if solver_name == "numpy-ridge" else solver_name
    weighting_policy = _policy_value(
        policy,
        "pooled_weighting_policy",
        {"EQUAL_INSTRUMENT": "EQUAL_INSTRUMENT_TOTAL_WEIGHT_MEAN_ONE"},
    )
    preprocessing_policy = _policy_value(
        policy,
        "preprocessing_policy",
        {"TRAINING_MEDIAN_STANDARDISE": "TRAINING_MEDIAN_STANDARDISE_V1"},
    )
    inner_policy = _policy_value(
        policy,
        "inner_validation_policy",
        {"CHRONOLOGICAL_TAIL": "CHRONOLOGICAL_TAIL_PURGED_V1"},
    )
    loss_policy = "OOF_PRIMARY_MSE_V1"
    if (
        preprocessing_policy != "TRAINING_MEDIAN_STANDARDISE_V1"
        or inner_policy != "CHRONOLOGICAL_TAIL_PURGED_V1"
    ):
        raise ValueError("holdout final fit uses the established R2 preprocessing policies")
    if policy.alpha_tie_break_policy not in {"LOSS_THEN_ALPHA", "LOSS_THEN_LARGER_ALPHA"}:
        raise ValueError("holdout final fit requires the established larger-alpha tie break")
    identity_order = tuple(
        cast(Sequence[str], policy.runtime_identities.get("instrument_identity_order", ()))
    )
    if (
        model_family in (ModelFamily.POOLED_LOCAL_RIDGE, ModelFamily.POOLED_CROSS_ASSET_RIDGE)
        and not identity_order
    ):
        identity_order = tuple(sorted({row.instrument_id for row in ordered}))
    if model_family is ModelFamily.LOCAL_RIDGE or model_family is ModelFamily.ZERO_RETURN:
        identity_order = ()
    try:
        selected = select_chronological_alpha(
            r2_rows,
            preprocessing_schema=schema,
            alpha_grid=policy.alpha_grid,
            minimum_training_rows=minimum_training_rows,
            minimum_inner_validation_rows=minimum_inner_validation_rows,
            ridge_solver=solver,
            ridge_tolerance=float(
                cast(float | int | str, policy.solver_identity.get("tolerance", 1e-8))
            ),
            ridge_max_iterations=int(
                cast(float | int | str, policy.solver_identity.get("max_iterations", 1000))
            ),
            loss_policy=loss_policy,
            pooled_weighting_policy=weighting_policy,
            instrument_identity_order=identity_order,
        )
    except (ArithmeticError, TypeError, ValueError) as error:
        return _failed_final_fit(
            selection=selection,
            configuration_id=configuration_id,
            model_family=model_family,
            feature_dataset_id=feature_dataset_id,
            feature_schema_id=feature_schema_id,
            training_cutoff=cutoff,
            training_ids=training_ids,
            purged_ids=purged,
            inner_fit_ids=(),
            inner_validation_ids=(),
            policy=policy,
            disposition=FinalFitDisposition.NUMERICAL_FAILURE,
            reason=f"R2 preprocessing selection failed: {type(error).__name__}: {error}",
            feature_count=feature_count,
            training_evidence=ordered,
        )
    inner_fit_ids = tuple(selected.inner_fit_target_ids)
    inner_validation_ids = tuple(selected.inner_validation_target_ids)
    scores = tuple(
        R2AlphaCandidateScore(
            alpha=item.alpha,
            validation_loss=item.loss,
            disposition=_final_disposition(item.disposition),
            failure_reason=item.failure,
        )
        for item in selected.candidate_scores
    )
    preprocessing = _preprocessing_payload(
        policy=policy,
        schema=schema,
        inner=selected.inner_preprocessing,
        outer=selected.outer_preprocessing,
        rows=ordered,
        identity_order=identity_order,
        solver=solver,
        weighting_policy=weighting_policy,
        fit_intercept=model_family is ModelFamily.LOCAL_RIDGE,
        minimum_training_rows=minimum_training_rows,
        minimum_inner_validation_rows=minimum_inner_validation_rows,
    )
    preprocessing.update(
        _final_fit_lineage(
            selection,
            configuration_id=configuration_id,
            model_family=model_family,
            feature_dataset_id=feature_dataset_id,
            feature_schema_id=feature_schema_id,
        )
    )
    if (
        selected.disposition.value != "READY"
        or selected.selected_alpha is None
        or selected.outer_preprocessing is None
    ):
        return _failed_final_fit(
            selection=selection,
            configuration_id=configuration_id,
            model_family=model_family,
            feature_dataset_id=feature_dataset_id,
            feature_schema_id=feature_schema_id,
            training_cutoff=cutoff,
            training_ids=training_ids,
            purged_ids=purged,
            inner_fit_ids=inner_fit_ids,
            inner_validation_ids=inner_validation_ids,
            policy=policy,
            disposition=_final_disposition(selected.disposition),
            reason=f"R2 preprocessing selection disposition {selected.disposition.value}",
            feature_count=feature_count,
            training_evidence=ordered,
            candidate_scores=scores or None,
            preprocessing=preprocessing,
        )
    outer = selected.outer_preprocessing
    matrix = add_instrument_identity(transform(r2_rows, outer), r2_rows, identity_order)
    targets = np.asarray([row.target for row in r2_rows], dtype=np.float64)
    if matrix.shape[1] == 0 or not np.isfinite(matrix).all() or not np.isfinite(targets).all():
        return _failed_final_fit(
            selection=selection,
            configuration_id=configuration_id,
            model_family=model_family,
            feature_dataset_id=feature_dataset_id,
            feature_schema_id=feature_schema_id,
            training_cutoff=cutoff,
            training_ids=training_ids,
            purged_ids=purged,
            inner_fit_ids=inner_fit_ids,
            inner_validation_ids=inner_validation_ids,
            policy=policy,
            disposition=FinalFitDisposition.DEGENERATE_FEATURE_MATRIX,
            reason="final transformed feature matrix is empty or non-finite",
            feature_count=feature_count,
            training_evidence=ordered,
            candidate_scores=scores or None,
            preprocessing=preprocessing,
        )
    alpha = selected.selected_alpha
    if model_family is ModelFamily.ZERO_RETURN:
        coefficients = np.zeros(matrix.shape[1], dtype=np.float64)
        intercept = 0.0
    else:
        try:
            from sklearn.linear_model import Ridge  # type: ignore[reportMissingTypeStubs]

            model: Any = Ridge(
                alpha=alpha,
                solver=solver,
                tol=float(cast(float | int | str, policy.solver_identity.get("tolerance", 1e-8))),
                max_iter=int(
                    cast(float | int | str, policy.solver_identity.get("max_iterations", 1000))
                ),
                fit_intercept=model_family is ModelFamily.LOCAL_RIDGE,
            )
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                model.fit(
                    matrix,
                    targets,
                    sample_weight=np.asarray(outer.sample_weights, dtype=float),
                )
            if caught:
                raise ArithmeticError("undeclared warning during final Ridge fit")
            coefficients = np.asarray(model.coef_, dtype=np.float64).reshape(-1)
            intercept = float(np.asarray(model.intercept_, dtype=np.float64).reshape(-1)[0])
        except (ArithmeticError, TypeError, ValueError) as error:
            return _failed_final_fit(
                selection=selection,
                configuration_id=configuration_id,
                model_family=model_family,
                feature_dataset_id=feature_dataset_id,
                feature_schema_id=feature_schema_id,
                training_cutoff=cutoff,
                training_ids=training_ids,
                purged_ids=purged,
                inner_fit_ids=inner_fit_ids,
                inner_validation_ids=inner_validation_ids,
                policy=policy,
                disposition=FinalFitDisposition.NUMERICAL_FAILURE,
                reason=f"final Ridge fit failed: {type(error).__name__}: {error}",
                feature_count=feature_count,
                training_evidence=ordered,
                candidate_scores=scores or None,
                preprocessing=preprocessing,
            )
    replay = matrix @ coefficients + intercept
    if replay.shape != targets.shape or not np.isfinite(replay).all():
        raise ValueError("final Ridge replay produced invalid predictions")
    weights = tuple(
        (row.target_id, float(weight))
        for row, weight in zip(ordered, outer.sample_weights, strict=True)
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
        inner_fit_target_ids=inner_fit_ids,
        inner_validation_target_ids=inner_validation_ids,
        preprocessing=preprocessing,
        alpha_candidate_scores=scores,
        selected_alpha=alpha,
        sample_weights=weights,
        coefficients=tuple(float(value) for value in coefficients),
        intercept=intercept,
        disposition=FinalFitDisposition.READY,
        failure_reason=None,
        diagnostics={
            "training_count": len(ordered),
            "inner_fit_count": len(inner_fit_ids),
            "inner_validation_count": len(inner_validation_ids),
            "selected_validation_loss": next(
                item.validation_loss for item in scores if item.alpha == alpha
            ),
            "solver": solver,
            "larger_alpha_tie_break": True,
        },
        runtime_identities=policy.runtime_identities,
        evidence_class=selection.evidence_class,
        holdout_scope=selection.holdout_scope,
    )


build_final_fit = fit_final_ridge


def _preprocessing_fit_from_payload(value: object) -> PreprocessingFit:
    if not isinstance(value, Mapping):
        raise ValueError("final-fit preprocessing outer state must be an object")
    raw = cast(Mapping[str, object], value)
    return PreprocessingFit(
        feature_names=tuple(str(item) for item in cast(Sequence[object], raw["feature_names"])),
        indicator_feature_names=tuple(
            str(item) for item in cast(Sequence[object], raw["indicator_feature_names"])
        ),
        medians=tuple(
            None if item is None else float(cast(float | int | str, item))
            for item in cast(Sequence[object], raw["medians"])
        ),
        means=tuple(
            None if item is None else float(cast(float | int | str, item))
            for item in cast(Sequence[object], raw["means"])
        ),
        scales=tuple(
            None if item is None else float(cast(float | int | str, item))
            for item in cast(Sequence[object], raw["scales"])
        ),
        active_feature_names=tuple(
            str(item) for item in cast(Sequence[object], raw["active_feature_names"])
        ),
        unscaled_feature_names=tuple(
            str(item) for item in cast(Sequence[object], raw["unscaled_feature_names"])
        ),
        dropped_all_null_feature_names=tuple(
            str(item) for item in cast(Sequence[object], raw["dropped_all_null_feature_names"])
        ),
        dropped_zero_variance_feature_names=tuple(
            str(item) for item in cast(Sequence[object], raw["dropped_zero_variance_feature_names"])
        ),
        training_target_ids=tuple(
            str(item) for item in cast(Sequence[object], raw["training_target_ids"])
        ),
        sample_weights=tuple(
            float(cast(float | int | str, item))
            for item in cast(Sequence[object], raw["sample_weights"])
        ),
    )


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
            from types import SimpleNamespace

            from qtrad.application.r2_preprocessing import (
                FeatureVector,
                InstrumentFeatureVector,
                add_instrument_identity,
                transform,
            )

            preprocessing = _preprocessing_fit_from_payload(fit.preprocessing["outer"])
            vectors = tuple(
                SimpleNamespace(features=feature.values, target_instrument_id=feature.instrument_id)
                for feature in feature_dataset.rows
            )
            matrix = add_instrument_identity(
                transform(cast(Sequence[FeatureVector], vectors), preprocessing),
                cast(Sequence[InstrumentFeatureVector], vectors),
                tuple(
                    str(item)
                    for item in cast(
                        Sequence[object], fit.preprocessing["instrument_identity_order"]
                    )
                ),
            )
            coefficients = np.asarray(fit.coefficients, dtype=np.float64)
            if matrix.shape[1] != coefficients.shape[0]:
                raise ValueError(
                    "final-fit coefficient length differs from replayed feature matrix"
                )
            if fit.intercept is None:
                raise ValueError("ready final fit is missing an intercept")
            intercept = float(fit.intercept)
            for feature, values in zip(
                feature_dataset.rows,
                matrix @ coefficients + intercept,
                strict=True,
            ):
                rows.append(
                    R2HoldoutForecastRow.create(
                        configuration_id=fit.configuration_id,
                        target_id=feature.target_id,
                        feature_row_id=feature.row_id,
                        forecast=float(values),
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

    expected_configurations = set(selection.holdout_configuration_ids)
    declared_families = set(selection.comparator_families)
    fit_by_configuration: dict[str, R2FinalFit] = {}
    for fit in fits:
        if fit.selection_manifest_id != selection.manifest_id:
            raise ValueError("seal final-fit selection lineage differs")
        if fit.feature_dataset_id != feature_dataset.dataset_id:
            raise ValueError("seal final fit is not bound to the prepared features")
        if fit.configuration_id not in expected_configurations:
            raise ValueError("seal final fit is not in the frozen configuration registry")
        if fit.model_family not in declared_families:
            raise ValueError("seal final fit model family is not predeclared")
        if fit.configuration_id in fit_by_configuration:
            raise ValueError("seal has duplicate final-fit configuration IDs")
        fit_by_configuration[fit.configuration_id] = fit
    if set(fit_by_configuration) != expected_configurations:
        raise ValueError("seal final fits do not exactly cover frozen configurations")

    forecast_by_configuration: dict[str, R2HoldoutForecastDataset] = {}
    for forecast in forecasts:
        fit = fit_by_configuration.get(forecast.configuration_id)
        if (
            forecast.selection_manifest_id != selection.manifest_id
            or forecast.feature_dataset_id != feature_dataset.dataset_id
            or fit is None
            or forecast.final_fit_id != fit.fit_id
        ):
            raise ValueError("seal forecast does not reconcile to its final fit registry")
        if forecast.configuration_id in forecast_by_configuration:
            raise ValueError("seal has duplicate forecast configuration IDs")
        if any(row.model_family is not fit.model_family for row in forecast.rows):
            raise ValueError("seal forecast row model families differ from its final fit")
        forecast_by_configuration[forecast.configuration_id] = forecast
    if set(forecast_by_configuration) != expected_configurations:
        raise ValueError("seal forecasts do not exactly cover frozen configurations")

    coverage_by_configuration: dict[str, R2HoldoutCoverageDataset] = {}
    for item in coverage:
        fit = fit_by_configuration.get(item.configuration_id)
        if (
            item.selection_manifest_id != selection.manifest_id
            or item.feature_dataset_id != feature_dataset.dataset_id
            or fit is None
            or item.configuration_id in coverage_by_configuration
        ):
            raise ValueError("seal coverage does not reconcile to its final-fit registry")
        coverage_by_configuration[item.configuration_id] = item
    if set(coverage_by_configuration) != expected_configurations:
        raise ValueError("seal coverage does not exactly cover frozen configurations")

    pairs = tuple(
        sorted(
            (question.candidate_configuration_id, question.comparator_configuration_id)
            for question in selection.questions
        )
    )
    if not pairs:
        raise ValueError("selection question register has no configuration pairs")
    if len(pairs) != len(set(pairs)):
        raise ValueError("selection question register has duplicate configuration pairs")
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


def _validate_frozen_evaluation_policies(seal: R2HoldoutForecastSeal) -> float:
    if seal.comparison_support != {"rule": "COMMON_ELIGIBLE"}:
        raise ValueError("unsupported frozen comparison-support policy")
    if seal.forecast_buckets != {"source": "TRAINING_ONLY"}:
        raise ValueError("unsupported frozen forecast-bucket policy")
    if seal.state_buckets != {"source": "TRAINING_ONLY"}:
        raise ValueError("unsupported frozen state-bucket policy")
    if set(seal.coverage_rules) != {"minimum"}:
        raise ValueError("unsupported frozen coverage policy")
    coverage_minimum = seal.coverage_rules["minimum"]
    if not isinstance(coverage_minimum, (int, float)) or not 0.0 <= coverage_minimum <= 1.0:
        raise ValueError("frozen coverage minimum must be between zero and one")
    for question in seal.questions:
        if question.support_policy != "COMMON_ELIGIBLE":
            raise ValueError("unsupported frozen question support policy")
        if question.conclusion_policy != "THRESHOLD_OR_INCONCLUSIVE":
            raise ValueError("unsupported frozen question conclusion policy")
    return float(coverage_minimum)


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
    coverage_minimum = _validate_frozen_evaluation_policies(seal)

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
        if support_count < question.minimum_support or coverage < max(
            question.minimum_coverage, coverage_minimum
        ):
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
        questions=tuple(results),
        source_class=selection.source_class,
        evidence_class=selection.evidence_class,
        holdout_scope=selection.holdout_scope,
    )
