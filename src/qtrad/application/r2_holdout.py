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
from qtrad.domain.foundation import TargetDataset
from qtrad.domain.market_data import MarketDataSourceClass
from qtrad.domain.r2_bundles import R2OofBundle
from qtrad.domain.r2_evaluation import SelectionManifest
from qtrad.domain.r2_features import R2FeatureDataset
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


def _authenticated_final_training_rows(
    selection: R2HoldoutSelectionManifest,
    *,
    feature_dataset: R2FeatureDataset,
    target_dataset: TargetDataset,
) -> tuple[FinalTrainingRow, ...]:
    if not feature_dataset.holdout_excluded:
        raise ValueError("final-fit feature evidence must exclude the holdout")
    if feature_dataset.experiment_configuration_id != selection.experiment_configuration_id:
        raise ValueError("final-fit feature evidence differs from the frozen experiment")
    if feature_dataset.target_dataset_id != target_dataset.dataset_id:
        raise ValueError("final-fit feature evidence differs from the target dataset")
    if feature_dataset.observation_dataset_id != target_dataset.observation_dataset_id:
        raise ValueError("final-fit feature and target observations differ")
    if feature_dataset.evidence_class is not selection.evidence_class:
        raise ValueError("final-fit feature evidence differs from the frozen evidence class")
    expected_target = selection.evaluation_policy.get("target_dataset_id")
    if expected_target is not None and expected_target != target_dataset.dataset_id:
        raise ValueError("final-fit target evidence differs from the frozen target dataset")
    expected_observation = selection.evaluation_policy.get("observation_dataset_id")
    if (
        expected_observation is not None
        and expected_observation != target_dataset.observation_dataset_id
    ):
        raise ValueError("final-fit target evidence differs from the frozen observations")
    expected_foundation = selection.evaluation_policy.get("foundation_configuration_id")
    if (
        expected_foundation is not None
        and expected_foundation != target_dataset.foundation_configuration_id
    ):
        raise ValueError("final-fit target evidence differs from the frozen foundation")

    targets = {(row.instrument_id, row.decision_time): row for row in target_dataset.rows}
    rows: list[FinalTrainingRow] = []
    seen_target_ids: set[str] = set()
    for raw_row in feature_dataset.rows:
        target = targets.get((raw_row.target_instrument_id, raw_row.decision_time))
        if target is None:
            raise ValueError("final-fit feature evidence has no authenticated target row")
        if target.return_disposition.value != "VALID" or target.log_return is None:
            continue
        if target.target_id in seen_target_ids:
            raise ValueError("final-fit feature evidence repeats a target")
        seen_target_ids.add(target.target_id)
        rows.append(
            FinalTrainingRow(
                target_id=target.target_id,
                instrument_id=target.instrument_id,
                decision_time=target.decision_time,
                target_available_at=target.target_available_at,
                features=tuple(item.value for item in raw_row.values),
                target=target.log_return,
                target_end_time=target.target_end_time,
            )
        )
    if not rows:
        raise ValueError("final-fit authenticated training evidence is empty")
    return tuple(rows)


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
    configuration_registry: Sequence[tuple[str, ModelFamily, str | None, str | None]] | None = None,
    evaluation_policy: Mapping[str, JsonValue] | None = None,
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
        if configuration_registry is None or not configuration_registry:
            raise ValueError(
                "verified OOF freeze requires its authenticated configuration registry"
            )
        frozen_configuration_registry = tuple(configuration_registry)
        registry_ids = tuple(item[0] for item in frozen_configuration_registry)
        if registry_ids != evaluated:
            raise ValueError(
                "verified OOF configuration registry differs from evaluation decisions"
            )
        if not set(selected + controls) <= set(registry_ids):
            raise ValueError("verified OOF selection references an unknown configuration")
        if evaluation_policy is None:
            raise ValueError("verified OOF freeze requires its authenticated evaluation policy")
        frozen_evaluation_policy = dict(evaluation_policy)
        authenticated_metric = frozen_evaluation_policy.get("metric_policy")
        authenticated_forecast_buckets = frozen_evaluation_policy.get("forecast_bucket_policy")
        authenticated_minimum_rows = frozen_evaluation_policy.get("minimum_correlation_rows")
        if (
            authenticated_metric != verified_experiment.metric_policy
            or authenticated_forecast_buckets != verified_experiment.forecast_bucket_policy
        ):
            raise ValueError("OOF evaluation policy differs from the authenticated experiment")
        if not isinstance(authenticated_metric, str) or not isinstance(
            authenticated_forecast_buckets, str
        ):
            raise ValueError("OOF evaluation policies are not authenticated strings")
        if not isinstance(authenticated_minimum_rows, int) or authenticated_minimum_rows < 2:
            raise ValueError("OOF evaluation minimum support is invalid")
        if verified_experiment.forecast_bucket_policy not in {
            "TRAINING_ONLY",
            "TRAINING_QUANTILES_V1",
        }:
            raise ValueError("holdout requires the canonical forecast bucket policy")
        if verified_experiment.state_bucket_policy not in {
            "TRAINING_ONLY",
            "TRAINING_THRESHOLDS_V1",
        }:
            raise ValueError("holdout requires the canonical state bucket policy")
        frozen_evaluation_policy.update(
            {
                "target_dataset_id": verified_experiment.target_dataset_id,
                "observation_dataset_id": verified_experiment.observation_dataset_id,
                "panel_dataset_id": verified_experiment.panel_dataset_id,
                "preprocessing_policy": verified_experiment.preprocessing_policy,
                "alpha_grid": list(verified_experiment.alpha_grid),
                "inner_validation_policy": verified_experiment.inner_validation_policy,
                "ridge_solver": verified_experiment.ridge_solver,
                "ridge_tolerance": verified_experiment.ridge_tolerance,
                "ridge_max_iterations": verified_experiment.ridge_max_iterations,
                "pooled_weighting_policy": verified_experiment.pooled_weighting_policy,
                "minimum_training_rows": verified_experiment.minimum_training_rows,
                "minimum_inner_validation_rows": verified_experiment.minimum_inner_validation_rows,
                "minimum_outer_validation_rows": verified_experiment.minimum_outer_validation_rows,
                "ordered_instruments": list(verified_experiment.ordered_instruments),
                "target_instruments": list(verified_experiment.target_instruments),
                "pre_holdout_membership_policy": final_fitting_policy.pre_holdout_membership_policy,
                "maturity_purge_policy": final_fitting_policy.maturity_purge_policy,
                "instrument_intercept_policy": final_fitting_policy.instrument_intercept_policy,
                "pooled_membership_policy": final_fitting_policy.pooled_membership_policy,
                "alpha_tie_break_policy": final_fitting_policy.alpha_tie_break_policy,
                "metric_policy": authenticated_metric,
                "forecast_bucket_policy": authenticated_forecast_buckets,
                "state_bucket_policy": verified_experiment.state_bucket_policy,
                "model_selection_policy": verified_experiment.model_selection_policy,
                "loss_policy": "OOF_PRIMARY_MSE_V1",
                "instrument_identity_policy": "ORDERED_EXPERIMENT_UNIVERSE",
                "purged_target_ids": [],
            }
        )
        metric_policy = {
            "name": authenticated_metric,
            "primary_metric": prior_selection.primary_metric,
            "secondary_metrics": list(prior_selection.secondary_metrics),
        }
        threshold_policy = {
            "acceptance_thresholds": [
                [key, value] for key, value in prior_selection.acceptance_thresholds
            ],
        }
        frozen_evaluation_policy["seal_policy"] = cast(
            JsonValue,
            {
                "metric_policy": metric_policy,
                "comparison_support": {
                    "rule": "COMMON_ELIGIBLE",
                    "minimum_rows": authenticated_minimum_rows,
                },
                "forecast_buckets": {
                    "source": "TRAINING_ONLY",
                    "count": frozen_evaluation_policy["forecast_bucket_count"],
                },
                "state_buckets": {"source": "TRAINING_ONLY"},
                "coverage_rules": {"minimum": 0.0},
            },
        )
        runtime_identities = {
            "application_image_identity": prior_selection.application_image_identity,
            "foundation_bundle_id": foundation_bundle_id,
            "oof_bundle_id": oof_bundle_id,
            "final_fitting_policy_id": final_fitting_policy.policy_id,
        }
    else:
        frozen_configuration_registry = tuple(configuration_registry or ())
        frozen_evaluation_policy = dict(evaluation_policy or {})

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
        evaluation_policy=frozen_evaluation_policy,
        final_fitting_policy=final_fitting_policy,
        questions=questions,
        holdout_range=prior_selection.holdout_range,
        experiment_count=len(evaluated),
        configuration_registry=frozen_configuration_registry,
        runtime_identities=runtime_identities,
        frozen_metadata=frozen_metadata,
        frozen_at=frozen_at,
        frozen_by=frozen_by,
    )


def _authenticated_holdout_rows(
    *,
    selection: R2HoldoutSelectionManifest,
    opportunities: Sequence[HoldoutTargetOpportunity],
    feature_schema_id: str,
    feature_set_id: str,
    observation_dataset_id: str,
    panel_dataset_id: str,
    raw_feature_dataset: R2FeatureDataset,
    target_dataset: TargetDataset,
) -> dict[str, R2HoldoutFeatureRow | None]:
    if (
        raw_feature_dataset.experiment_configuration_id != selection.experiment_configuration_id
        or raw_feature_dataset.feature_set_id != feature_set_id
        or raw_feature_dataset.raw_feature_schema_id != feature_schema_id
        or raw_feature_dataset.observation_dataset_id != observation_dataset_id
        or raw_feature_dataset.panel_dataset_id != panel_dataset_id
        or raw_feature_dataset.target_dataset_id != target_dataset.dataset_id
    ):
        raise ValueError("authenticated R2.B feature child differs from the frozen feature sources")
    target_by_identity = {
        (row.instrument_id, row.decision_time): row for row in target_dataset.rows
    }
    raw_by_identity = {
        (row.target_instrument_id, row.decision_time): row for row in raw_feature_dataset.rows
    }
    result: dict[str, R2HoldoutFeatureRow | None] = {}
    for opportunity in opportunities:
        target = target_by_identity.get((opportunity.instrument_id, opportunity.decision_time))
        if target is None or target.target_id != opportunity.target_id:
            raise ValueError(
                "authenticated target child differs from the holdout opportunity identity"
            )
        raw = raw_by_identity.get((opportunity.instrument_id, opportunity.decision_time))
        if raw is None or any(item.value is None for item in raw.values):
            result[opportunity.opportunity_id] = None
            continue
        result[opportunity.opportunity_id] = R2HoldoutFeatureRow.create(
            opportunity_id=opportunity.opportunity_id,
            target_id=target.target_id,
            instrument_id=opportunity.instrument_id,
            decision_time=opportunity.decision_time,
            feature_cutoff=raw.feature_data_asof,
            latest_feature_bar_end=raw.latest_feature_bar_end,
            feature_schema_id=feature_schema_id,
            values=tuple(cast(float, item.value) for item in raw.values),
        )
    return result


def materialise_r2_holdout_features(
    *,
    selection: R2HoldoutSelectionManifest,
    opportunities: Sequence[HoldoutTargetOpportunity],
    feature_schema_id: str,
    feature_set_id: str,
    observation_dataset_id: str,
    panel_dataset_id: str,
    target_dataset_id: str | None = None,
    projection: Callable[[HoldoutTargetOpportunity], R2HoldoutFeatureRow | None] | None = None,
    raw_feature_dataset: R2FeatureDataset | None = None,
    target_dataset: TargetDataset | None = None,
    allow_disposable_projection: bool = False,
) -> R2HoldoutFeatureDataset:
    """Materialise R2.B features, with projection callbacks limited to disposable fixtures."""

    _ensure_selection_lineage(selection)
    if selection.configuration_registry:
        frozen_target_dataset_id = selection.evaluation_policy.get("target_dataset_id")
        frozen_observation_dataset_id = selection.evaluation_policy.get("observation_dataset_id")
        frozen_panel_dataset_id = selection.evaluation_policy.get("panel_dataset_id")
        if (
            (
                frozen_target_dataset_id is not None
                and (
                    not isinstance(frozen_target_dataset_id, str)
                    or target_dataset_id != frozen_target_dataset_id
                )
            )
            or (frozen_target_dataset_id is None and target_dataset_id is not None)
            or observation_dataset_id != frozen_observation_dataset_id
            or panel_dataset_id != frozen_panel_dataset_id
        ):
            raise ValueError("holdout feature datasets differ from the frozen experiment sources")
        registry_feature_sets = {
            registry_feature_set_id
            for (
                _configuration_id,
                _model_family,
                registry_feature_set_id,
                _manifest_id,
            ) in selection.configuration_registry
            if registry_feature_set_id is not None
        }
        if registry_feature_sets and feature_set_id not in registry_feature_sets:
            raise ValueError("holdout feature set is absent from the authenticated OOF registry")
    if selection.holdout_scope is HoldoutScope.CONFIRMATORY:
        raise ValueError(
            "confirmatory holdout features require an independently verified R2.B feature child"
        )
    if raw_feature_dataset is not None:
        if projection is not None or target_dataset is None:
            raise ValueError(
                "authenticated R2.B materialisation requires its feature and target children"
            )
        projected_rows = _authenticated_holdout_rows(
            selection=selection,
            opportunities=opportunities,
            feature_schema_id=feature_schema_id,
            feature_set_id=feature_set_id,
            observation_dataset_id=observation_dataset_id,
            panel_dataset_id=panel_dataset_id,
            raw_feature_dataset=raw_feature_dataset,
            target_dataset=target_dataset,
        )
    else:
        if projection is None:
            raise ValueError("holdout feature materialisation requires an authenticated R2.B child")
        if (
            not allow_disposable_projection
            and selection.holdout_scope is not HoldoutScope.DISPOSABLE_FIXTURE
        ):
            raise ValueError(
                "arbitrary feature projections are permitted only for explicit disposable fixtures"
            )
        projected_rows = {item.opportunity_id: projection(item) for item in opportunities}
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
        row = projected_rows[opportunity.opportunity_id]
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
        target_dataset_id=target_dataset_id,
        feature_schema_id=feature_schema_id,
        feature_set_id=feature_set_id,
        source_class=selection.source_class,
        evidence_class=selection.evidence_class,
        holdout_scope=selection.holdout_scope,
        holdout_range=selection.holdout_range,
        expected_opportunity_ids=expected,
        opportunity_target_ids=tuple((item.opportunity_id, item.target_id) for item in ordered),
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
    training_feature_dataset_id: str | None = None,
    training_target_dataset_id: str | None = None,
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
    if training_feature_dataset_id is None or training_target_dataset_id is None:
        raise ValueError("final fit is missing authenticated training child IDs")
    fit_preprocessing.update(
        {
            "training_feature_dataset_id": training_feature_dataset_id,
            "training_target_dataset_id": training_target_dataset_id,
        }
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
    training_feature_dataset_id: str,
    training_target_dataset_id: str,
) -> dict[str, JsonValue]:
    return {
        "policy_id": policy.policy_id,
        "schema": schema.as_json(),
        "inner": inner.as_json() if inner is not None else None,
        "outer": outer.as_json() if outer is not None else None,
        "training_rows": [_training_row_json(row) for row in rows],
        "training_feature_dataset_id": training_feature_dataset_id,
        "training_target_dataset_id": training_target_dataset_id,
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
    training_feature_dataset: R2FeatureDataset,
    training_target_dataset: TargetDataset,
    policy: R2FinalFittingPolicy,
    training_cutoff: datetime | None = None,
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
    if selection.holdout_scope is HoldoutScope.CONFIRMATORY:
        raise ValueError(
            "confirmatory final fits require independently verified pre-holdout "
            "feature and target children"
        )
    training_rows = _authenticated_final_training_rows(
        selection,
        feature_dataset=training_feature_dataset,
        target_dataset=training_target_dataset,
    )
    if policy.policy_id != selection.final_fitting_policy.policy_id:
        raise ValueError("final fitting policy differs from the frozen selection")
    frozen_policy_values = {
        "pre_holdout_membership_policy": policy.pre_holdout_membership_policy,
        "maturity_purge_policy": policy.maturity_purge_policy,
        "inner_validation_policy": policy.inner_validation_policy,
        "alpha_grid": list(policy.alpha_grid),
        "alpha_tie_break_policy": policy.alpha_tie_break_policy,
        "preprocessing_policy": policy.preprocessing_policy,
        "pooled_membership_policy": policy.pooled_membership_policy,
        "pooled_weighting_policy": policy.pooled_weighting_policy,
        "instrument_intercept_policy": policy.instrument_intercept_policy,
        "solver_identity": dict(policy.solver_identity),
    }
    for key, value in frozen_policy_values.items():
        frozen_value = selection.evaluation_policy.get(key)
        if frozen_value is not None and frozen_value != value:
            raise ValueError(f"final fit {key} differs from the frozen selection")
    registry_family = dict(
        (configuration, family)
        for configuration, family, _feature_set, _manifest in selection.configuration_registry
    ).get(configuration_id)
    if selection.configuration_registry and registry_family is None:
        raise ValueError("final fit configuration is absent from the authenticated OOF registry")
    if registry_family is not None and registry_family is not model_family:
        raise ValueError("final fit model family differs from the authenticated OOF registry")
    if model_family is ModelFamily.ZERO_RETURN:
        raise ValueError("ZERO_RETURN is a control and must not be fitted")
    frozen_minimum_training_rows = selection.evaluation_policy.get("minimum_training_rows")
    frozen_minimum_inner_rows = selection.evaluation_policy.get("minimum_inner_validation_rows")
    if selection.configuration_registry and (
        frozen_minimum_training_rows is None or frozen_minimum_inner_rows is None
    ):
        raise ValueError("verified final fit is missing authenticated row minima")
    minimum_training_rows = (
        frozen_minimum_training_rows if frozen_minimum_training_rows is not None else 2
    )
    minimum_inner_validation_rows = (
        frozen_minimum_inner_rows if frozen_minimum_inner_rows is not None else 1
    )
    if (
        not isinstance(minimum_training_rows, int)
        or minimum_training_rows <= 0
        or not isinstance(minimum_inner_validation_rows, int)
        or minimum_inner_validation_rows <= 0
    ):
        raise ValueError("selection frozen row minima are invalid")
    cutoff = training_cutoff or selection.holdout_range[0]
    require_utc(cutoff, "final-fit training cutoff")
    if cutoff != selection.holdout_range[0]:
        raise ValueError("final-fit training cutoff must equal the frozen holdout boundary")
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
    if selection.configuration_registry:
        frozen_purged = selection.evaluation_policy.get("purged_target_ids", [])
        if not isinstance(frozen_purged, list):
            raise ValueError("verified final-fit purge membership is not authenticated")
        if purged != tuple(sorted(str(item) for item in frozen_purged)):
            raise ValueError("final-fit purge membership differs from the frozen evidence")
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
            training_feature_dataset_id=training_feature_dataset.dataset_id,
            training_target_dataset_id=training_target_dataset.dataset_id,
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
            training_feature_dataset_id=training_feature_dataset.dataset_id,
            training_target_dataset_id=training_target_dataset.dataset_id,
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
    model_selection_policy = selection.evaluation_policy.get(
        "model_selection_policy", "OOF_PRIMARY_MSE_V1"
    )
    if model_selection_policy != "OOF_PRIMARY_MSE_V1":
        raise ValueError("final fit requires the frozen OOF model-selection policy")
    loss_policy_value = selection.evaluation_policy.get("loss_policy", "OOF_PRIMARY_MSE_V1")
    if loss_policy_value != "OOF_PRIMARY_MSE_V1" or not isinstance(loss_policy_value, str):
        raise ValueError("final fit requires the frozen OOF loss policy")
    loss_policy = loss_policy_value
    if (
        preprocessing_policy != "TRAINING_MEDIAN_STANDARDISE_V1"
        or inner_policy != "CHRONOLOGICAL_TAIL_PURGED_V1"
    ):
        raise ValueError("holdout final fit uses the established R2 preprocessing policies")
    if policy.alpha_tie_break_policy not in {"LOSS_THEN_ALPHA", "LOSS_THEN_LARGER_ALPHA"}:
        raise ValueError("holdout final fit requires the established larger-alpha tie break")
    frozen_instruments = selection.evaluation_policy.get("target_instruments")
    identity_order = tuple(
        cast(Sequence[str], policy.runtime_identities.get("instrument_identity_order", ()))
    )
    if frozen_instruments is not None:
        if not isinstance(frozen_instruments, list) or not all(
            isinstance(item, str) for item in frozen_instruments
        ):
            raise ValueError("selection frozen target instrument universe is invalid")
        frozen_order = tuple(cast(list[str], frozen_instruments))
        if identity_order and identity_order != frozen_order:
            raise ValueError("final fit identity order differs from the frozen target universe")
        identity_order = frozen_order
    if (
        model_family in (ModelFamily.POOLED_LOCAL_RIDGE, ModelFamily.POOLED_CROSS_ASSET_RIDGE)
        and not identity_order
    ):
        raise ValueError("pooled final fit requires the frozen target instrument universe")
    if model_family is ModelFamily.LOCAL_RIDGE:
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
            training_feature_dataset_id=training_feature_dataset.dataset_id,
            training_target_dataset_id=training_target_dataset.dataset_id,
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
        training_feature_dataset_id=training_feature_dataset.dataset_id,
        training_target_dataset_id=training_target_dataset.dataset_id,
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
            training_feature_dataset_id=training_feature_dataset.dataset_id,
            training_target_dataset_id=training_target_dataset.dataset_id,
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
            training_feature_dataset_id=training_feature_dataset.dataset_id,
            training_target_dataset_id=training_target_dataset.dataset_id,
            training_evidence=ordered,
            candidate_scores=scores or None,
            preprocessing=preprocessing,
        )
    alpha = selected.selected_alpha
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
            training_feature_dataset_id=training_feature_dataset.dataset_id,
            training_target_dataset_id=training_target_dataset.dataset_id,
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


def _feature_dataset_by_configuration(
    selection: R2HoldoutSelectionManifest,
    *,
    feature_dataset: R2HoldoutFeatureDataset | None,
    feature_datasets: Mapping[str, R2HoldoutFeatureDataset] | None,
) -> dict[str, R2HoldoutFeatureDataset | None]:
    candidates = dict(feature_datasets or {})
    registry_entries = selection.configuration_registry
    registry = {
        configuration_id: (model_family, feature_set_id)
        for configuration_id, model_family, feature_set_id, _manifest_id in registry_entries
    }
    configurations = tuple(
        selection.holdout_configuration_ids
        or tuple(candidates)
        or tuple(configuration_id for configuration_id, *_ in registry_entries)
    )
    result: dict[str, R2HoldoutFeatureDataset | None] = {}
    for configuration_id in configurations:
        model_family, feature_set_id = registry.get(configuration_id, (None, None))
        if model_family is ModelFamily.ZERO_RETURN:
            result[configuration_id] = None
            continue
        dataset = candidates.get(configuration_id)
        if dataset is None and feature_set_id is not None:
            dataset = next(
                (
                    item
                    for key, item in candidates.items()
                    if key == feature_set_id or item.feature_set_id == feature_set_id
                ),
                None,
            )
        if dataset is None:
            dataset = feature_dataset
        if dataset is not None and dataset.selection_manifest_id != selection.manifest_id:
            raise ValueError("holdout feature dataset is not bound to selection")
        if (
            dataset is not None
            and feature_set_id is not None
            and dataset.feature_set_id != feature_set_id
        ):
            raise ValueError(
                "holdout feature dataset differs from the authenticated configuration registry"
            )
        result[configuration_id] = dataset
    return result


def build_holdout_forecasts(
    *,
    selection: R2HoldoutSelectionManifest,
    feature_dataset: R2HoldoutFeatureDataset | None = None,
    feature_datasets: Mapping[str, R2HoldoutFeatureDataset] | None = None,
    final_fits: Sequence[R2FinalFit],
    opportunities: Sequence[HoldoutTargetOpportunity] = (),
) -> tuple[R2HoldoutForecastDataset, ...]:
    _ensure_selection_lineage(selection)
    feature_by_configuration = _feature_dataset_by_configuration(
        selection,
        feature_dataset=feature_dataset,
        feature_datasets=feature_datasets,
    )
    result: list[R2HoldoutForecastDataset] = []
    fits_by_configuration = {fit.configuration_id: fit for fit in final_fits}
    registry = {
        configuration_id: model_family
        for (
            configuration_id,
            model_family,
            _feature_set_id,
            _manifest_id,
        ) in selection.configuration_registry
    }
    configurations = tuple(
        sorted(selection.holdout_configuration_ids if registry else fits_by_configuration)
    )
    for configuration_id in configurations:
        feature_dataset = feature_by_configuration.get(configuration_id)
        fit = fits_by_configuration.get(configuration_id)
        model_family = registry.get(configuration_id)
        if model_family is ModelFamily.ZERO_RETURN and registry:
            if fit is not None:
                raise ValueError("ZERO_RETURN control must not have a final-fit child")
            feature_by_target = (
                {row.target_id: row for row in feature_dataset.rows}
                if feature_dataset is not None
                else {}
            )
            zero_targets = {
                opportunity.target_id
                for opportunity in opportunities
                if opportunity.disposition is HoldoutOpportunityDisposition.ELIGIBLE
            }
            if not opportunities:
                zero_targets.update(feature_by_target)
            result.append(
                R2HoldoutForecastDataset.create(
                    selection_manifest_id=selection.manifest_id,
                    feature_dataset_id=None,
                    configuration_id=configuration_id,
                    final_fit_id=None,
                    rows=tuple(
                        R2HoldoutForecastRow.create(
                            configuration_id=configuration_id,
                            target_id=target_id,
                            feature_row_id=None,
                            forecast=0.0,
                            model_family=ModelFamily.ZERO_RETURN,
                        )
                        for target_id in sorted(zero_targets)
                    ),
                    expected_opportunity_ids=(
                        tuple(sorted(item.opportunity_id for item in opportunities))
                        if opportunities
                        else (
                            feature_dataset.expected_opportunity_ids
                            if feature_dataset is not None
                            else tuple()
                        )
                    ),
                    opportunity_target_ids=(
                        tuple(
                            sorted((item.opportunity_id, item.target_id) for item in opportunities)
                        )
                        if opportunities
                        else (
                            feature_dataset.opportunity_target_ids
                            if feature_dataset is not None
                            else ()
                        )
                    ),
                    source_class=selection.source_class,
                    evidence_class=selection.evidence_class,
                    holdout_scope=selection.holdout_scope,
                )
            )
            continue
        if fit is None:
            raise ValueError("frozen fitted configuration has no final-fit child")
        if feature_dataset is None:
            raise ValueError("fitted configuration has no authenticated holdout feature dataset")
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
                SimpleNamespace(
                    features=feature.values,
                    target_instrument_id=feature.instrument_id,
                )
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
            for feature, value in zip(
                feature_dataset.rows,
                matrix @ coefficients + intercept,
                strict=True,
            ):
                rows.append(
                    R2HoldoutForecastRow.create(
                        configuration_id=fit.configuration_id,
                        target_id=feature.target_id,
                        feature_row_id=feature.row_id,
                        forecast=float(value),
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
                opportunity_target_ids=feature_dataset.opportunity_target_ids,
                source_class=selection.source_class,
                evidence_class=selection.evidence_class,
                holdout_scope=selection.holdout_scope,
            )
        )
    return tuple(result)


def build_holdout_coverage(
    *,
    selection: R2HoldoutSelectionManifest,
    feature_dataset: R2HoldoutFeatureDataset | None = None,
    feature_datasets: Mapping[str, R2HoldoutFeatureDataset] | None = None,
    final_fit: R2FinalFit | None,
    forecast_dataset: R2HoldoutForecastDataset,
    opportunities: Sequence[HoldoutTargetOpportunity],
) -> R2HoldoutCoverageDataset:
    _ensure_selection_lineage(selection)
    resolved_configuration_id = (
        final_fit.configuration_id if final_fit is not None else forecast_dataset.configuration_id
    )
    if final_fit is not None and final_fit.configuration_id != forecast_dataset.configuration_id:
        raise ValueError("coverage fit and forecast configuration differ")
    feature_dataset = _feature_dataset_by_configuration(
        selection,
        feature_dataset=feature_dataset,
        feature_datasets=feature_datasets,
    ).get(resolved_configuration_id)
    registry_family = {
        configuration_id: model_family
        for (
            configuration_id,
            model_family,
            _feature_set_id,
            _manifest_id,
        ) in selection.configuration_registry
    }.get(resolved_configuration_id)
    if final_fit is None and registry_family is not ModelFamily.ZERO_RETURN:
        raise ValueError("a fitless coverage child is only valid for ZERO_RETURN")
    expected_feature_dataset_id = (
        feature_dataset.dataset_id if feature_dataset is not None else None
    )
    if forecast_dataset.feature_dataset_id != expected_feature_dataset_id:
        raise ValueError("coverage forecast is not bound to its configuration features")
    opportunity_map = {item.opportunity_id: item for item in opportunities}
    expected_opportunity_ids = (
        feature_dataset.expected_opportunity_ids
        if feature_dataset is not None
        else tuple(sorted(opportunity_map))
    )
    if set(opportunity_map) != set(expected_opportunity_ids):
        raise ValueError("coverage opportunities differ from feature preparation")
    forecast_by_target = {item.target_id: item for item in forecast_dataset.rows}
    feature_by_opportunity = (
        {item.opportunity_id: item for item in feature_dataset.rows}
        if feature_dataset is not None
        else {}
    )
    unavailable: set[str] = set()
    if feature_dataset is not None:
        unavailable.update(feature_dataset.unavailable_opportunity_ids)
    rows: list[R2HoldoutCoverageRow] = []
    for opportunity_id in expected_opportunity_ids:
        opportunity = opportunity_map[opportunity_id]
        if final_fit is None:
            forecast = forecast_by_target.get(opportunity.target_id)
            if opportunity.disposition is not HoldoutOpportunityDisposition.ELIGIBLE:
                disposition = opportunity.disposition
                reason = "opportunity is not eligible for the zero-return control"
            elif forecast is None:
                disposition = HoldoutOpportunityDisposition.GAP
                reason = "eligible opportunity has no zero-return forecast row"
            else:
                disposition = HoldoutOpportunityDisposition.ELIGIBLE
                reason = "zero-return coverage is independent of feature availability"
            forecast_row_id = forecast.row_id if forecast is not None else None
        elif opportunity_id in unavailable:
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
            forecast = forecast_by_target.get(feature.target_id) if feature else None
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
                configuration_id=resolved_configuration_id,
                opportunity_id=opportunity_id,
                disposition=disposition,
                forecast_row_id=forecast_row_id,
                reason=reason,
            )
        )
    return R2HoldoutCoverageDataset.create(
        selection_manifest_id=selection.manifest_id,
        feature_dataset_id=expected_feature_dataset_id,
        configuration_id=resolved_configuration_id,
        expected_opportunity_ids=expected_opportunity_ids,
        rows=tuple(rows),
        source_class=selection.source_class,
        evidence_class=selection.evidence_class,
        holdout_scope=selection.holdout_scope,
    )


def seal_holdout_forecasts(
    *,
    selection: R2HoldoutSelectionManifest,
    feature_dataset: R2HoldoutFeatureDataset | None = None,
    feature_datasets: Mapping[str, R2HoldoutFeatureDataset] | None = None,
    final_fits: Sequence[R2FinalFit],
    forecast_datasets: Sequence[R2HoldoutForecastDataset],
    coverage_datasets: Sequence[R2HoldoutCoverageDataset],
    prepared_at: datetime,
    prepared_by: str,
) -> R2HoldoutForecastSeal:
    _ensure_selection_lineage(selection)
    inherited_seal_policy = selection.evaluation_policy.get("seal_policy")
    if not isinstance(inherited_seal_policy, dict):
        raise ValueError("selection must freeze the complete seal policy before preparation")
    required_policy_keys = {
        "metric_policy",
        "comparison_support",
        "forecast_buckets",
        "state_buckets",
        "coverage_rules",
    }
    if set(inherited_seal_policy) != required_policy_keys or any(
        not isinstance(inherited_seal_policy[key], dict) for key in required_policy_keys
    ):
        raise ValueError("selection frozen seal policy is incomplete")
    metric_policy = cast(Mapping[str, JsonValue], inherited_seal_policy["metric_policy"])
    comparison_support = cast(Mapping[str, JsonValue], inherited_seal_policy["comparison_support"])
    forecast_buckets = cast(Mapping[str, JsonValue], inherited_seal_policy["forecast_buckets"])
    state_buckets = cast(Mapping[str, JsonValue], inherited_seal_policy["state_buckets"])
    coverage_rules = cast(Mapping[str, JsonValue], inherited_seal_policy["coverage_rules"])
    feature_by_configuration = _feature_dataset_by_configuration(
        selection,
        feature_dataset=feature_dataset,
        feature_datasets=feature_datasets,
    )
    fits = tuple(sorted(final_fits, key=lambda item: item.fit_id))
    forecasts = tuple(sorted(forecast_datasets, key=lambda item: item.dataset_id))
    coverage = tuple(sorted(coverage_datasets, key=lambda item: item.coverage_id))
    if len(forecasts) != len(coverage):
        raise ValueError("seal requires one coverage child per forecast")
    expected_configurations = set(selection.holdout_configuration_ids)
    registry_by_configuration = {
        configuration_id: model_family
        for (
            configuration_id,
            model_family,
            _feature_set_id,
            _manifest_id,
        ) in selection.configuration_registry
    }
    if set(registry_by_configuration) != set(selection.evaluated_configuration_ids):
        raise ValueError("selection configuration registry is not complete")
    if not expected_configurations.issubset(registry_by_configuration):
        raise ValueError("selection holdout configurations are absent from the registry")
    feature_mapping: list[tuple[str, str | None]] = []
    for configuration_id in expected_configurations:
        dataset = feature_by_configuration.get(configuration_id)
        feature_mapping.append(
            (configuration_id, dataset.dataset_id if dataset is not None else None)
        )
    expected_feature_mapping = tuple(sorted(feature_mapping))
    expected_fit_configurations = {
        configuration_id
        for configuration_id in expected_configurations
        if registry_by_configuration[configuration_id] is not ModelFamily.ZERO_RETURN
    }
    fit_by_configuration: dict[str, R2FinalFit] = {}
    for fit in fits:
        if fit.selection_manifest_id != selection.manifest_id:
            raise ValueError("seal final-fit selection lineage differs")
        if fit.configuration_id not in expected_fit_configurations:
            raise ValueError("seal final fit is not in the frozen configuration registry")
        expected_dataset = feature_by_configuration.get(fit.configuration_id)
        if expected_dataset is None or fit.feature_dataset_id != expected_dataset.dataset_id:
            raise ValueError("seal final fit is not bound to its configuration features")
        expected_family = registry_by_configuration[fit.configuration_id]
        if fit.model_family is not expected_family:
            raise ValueError(
                "seal final fit model family differs from the authenticated OOF registry"
            )
        if fit.configuration_id in fit_by_configuration:
            raise ValueError("seal has duplicate final-fit configuration IDs")
        fit_by_configuration[fit.configuration_id] = fit
    if set(fit_by_configuration) != expected_fit_configurations:
        raise ValueError("seal final fits do not exactly cover frozen configurations")
    forecast_by_configuration: dict[str, R2HoldoutForecastDataset] = {}
    for forecast in forecasts:
        configuration_id = forecast.configuration_id
        if configuration_id not in expected_configurations:
            raise ValueError("seal forecast is not in the frozen configuration registry")
        expected_family = registry_by_configuration[configuration_id]
        expected_dataset = feature_by_configuration.get(configuration_id)
        expected_dataset_id = expected_dataset.dataset_id if expected_dataset else None
        fit = fit_by_configuration.get(configuration_id)
        if (
            forecast.selection_manifest_id != selection.manifest_id
            or forecast.feature_dataset_id != expected_dataset_id
        ):
            raise ValueError("seal forecast is not bound to its configuration features")
        if expected_family is ModelFamily.ZERO_RETURN:
            if fit is not None or forecast.final_fit_id is not None:
                raise ValueError("ZERO_RETURN forecast must not bind a final fit")
        elif fit is None or forecast.final_fit_id != fit.fit_id:
            raise ValueError("seal forecast does not reconcile to its final fit registry")
        if configuration_id in forecast_by_configuration:
            raise ValueError("seal has duplicate forecast configuration IDs")
        if any(row.model_family is not expected_family for row in forecast.rows):
            raise ValueError("seal forecast row model families differ from its registry")
        forecast_by_configuration[configuration_id] = forecast
    if set(forecast_by_configuration) != expected_configurations:
        raise ValueError("seal forecasts do not exactly cover frozen configurations")
    coverage_by_configuration: dict[str, R2HoldoutCoverageDataset] = {}
    for item in coverage:
        configuration_id = item.configuration_id
        if configuration_id not in expected_configurations:
            raise ValueError("seal coverage is not in the frozen configuration registry")
        expected_dataset = feature_by_configuration.get(configuration_id)
        expected_dataset_id = expected_dataset.dataset_id if expected_dataset else None
        if (
            item.selection_manifest_id != selection.manifest_id
            or item.feature_dataset_id != expected_dataset_id
            or (
                registry_by_configuration[configuration_id] is not ModelFamily.ZERO_RETURN
                and configuration_id not in fit_by_configuration
            )
            or configuration_id in coverage_by_configuration
        ):
            raise ValueError("seal coverage does not reconcile to its configuration registry")
        coverage_by_configuration[configuration_id] = item
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
        configuration_feature_dataset_ids=expected_feature_mapping,
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


def _validate_frozen_evaluation_policies(
    seal: R2HoldoutForecastSeal,
) -> tuple[float, int]:
    metric_name = seal.metric_policy.get(
        "name",
        seal.metric_policy.get(
            "primary_metric",
            seal.metric_policy.get("metric"),
        ),
    )
    if not isinstance(metric_name, str) or metric_name.upper() not in {
        "MSE",
        "RMSE",
        "MEAN_RETURN",
        "MEAN_FORECAST",
    }:
        raise ValueError("unsupported frozen metric policy")
    support_keys = set(seal.comparison_support)
    if support_keys not in ({"rule"}, {"rule", "minimum_rows"}):
        raise ValueError("unsupported frozen comparison-support policy")
    if seal.comparison_support.get("rule") != "COMMON_ELIGIBLE":
        raise ValueError("unsupported frozen comparison-support policy")
    support_minimum = seal.comparison_support.get("minimum_rows", 0)
    if not isinstance(support_minimum, int) or support_minimum < 0:
        raise ValueError("frozen comparison support minimum is invalid")
    forecast_keys = set(seal.forecast_buckets)
    if forecast_keys not in ({"source"}, {"source", "count"}):
        raise ValueError("unsupported frozen forecast-bucket policy")
    if seal.forecast_buckets.get("source") != "TRAINING_ONLY":
        raise ValueError("unsupported frozen forecast-bucket policy")
    if "count" in seal.forecast_buckets and (
        not isinstance(seal.forecast_buckets["count"], int) or seal.forecast_buckets["count"] < 2
    ):
        raise ValueError("frozen forecast bucket count is invalid")
    if seal.state_buckets != {"source": "TRAINING_ONLY"}:
        raise ValueError("unsupported frozen state-bucket policy")
    if set(seal.coverage_rules) != {"minimum"}:
        raise ValueError("unsupported frozen coverage policy")
    coverage_minimum = seal.coverage_rules["minimum"]
    if not isinstance(coverage_minimum, (int, float)) or not 0.0 <= coverage_minimum <= 1.0:
        raise ValueError("frozen coverage minimum must be between zero and one")
    for question in seal.questions:
        if question.metric.upper() != metric_name.upper():
            raise ValueError("question metric differs from frozen metric policy")
        if question.support_policy != "COMMON_ELIGIBLE":
            raise ValueError("unsupported frozen question support policy")
        if question.conclusion_policy != "THRESHOLD_OR_INCONCLUSIVE":
            raise ValueError("unsupported frozen question conclusion policy")
    return float(coverage_minimum), support_minimum


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
    coverage_minimum, support_minimum = _validate_frozen_evaluation_policies(seal)

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
        if support_count < max(question.minimum_support, support_minimum) or coverage < max(
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
