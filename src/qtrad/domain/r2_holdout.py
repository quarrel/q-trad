"""Typed locked-holdout contracts for the R2.G2 disposable workflow.

This module deliberately does not depend on a provider, database, model runtime, or
holdout outcome.  The only contract that can carry realised holdout values is the
evaluation function in the application layer, after an opened marker exists.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from math import isfinite
from typing import ClassVar, cast
from uuid import UUID

from qtrad.domain.events import JsonValue, to_json_value
from qtrad.domain.foundation import (
    TARGET_DATASET_CONTRACT,
    ExcursionDisposition,
    PanelAuditDisposition,
    PanelDataset,
    PanelRow,
    PanelStatus,
    ReturnDisposition,
    TargetDataset,
    TargetRow,
    target_identity,
)
from qtrad.domain.market_data import MarketDataSourceClass, PriceBasis
from qtrad.domain.r2_bundles import ArtifactReference
from qtrad.domain.r2_readiness import EvidenceClass, ModelFamily
from qtrad.domain.research import ObservationDataset, ObservationRow
from qtrad.domain.time import require_utc

R2_HOLDOUT_SELECTION_CONTRACT = "qtrad-r2-selection-v3"
R2_HOLDOUT_FEATURES_CONTRACT = "qtrad-r2-holdout-features-v1"
R2_FINAL_FIT_CONTRACT = "qtrad-r2-final-fit-v1"
R2_HOLDOUT_FORECAST_CONTRACT = "qtrad-r2-holdout-forecast-v1"
R2_HOLDOUT_COVERAGE_CONTRACT = "qtrad-r2-holdout-coverage-v1"
R2_HOLDOUT_FORECAST_SEAL_CONTRACT = "qtrad-r2-holdout-forecast-seal-v1"
R2_HOLDOUT_OPENED_CONTRACT = "qtrad-r2-holdout-opened-v1"
R2_HOLDOUT_CONSUMED_CONTRACT = "qtrad-r2-holdout-consumed-v1"
R2_CONFIRMATORY_OPENED_CONTRACT = "qtrad-r2-confirmatory-opened-v1"
R2_HOLDOUT_EVALUATION_CONTRACT = "qtrad-r2-holdout-evaluation-v1"
R2_HOLDOUT_OUTCOME_EVIDENCE_CONTRACT = "qtrad-r2-holdout-outcome-evidence-v1"
R2_HOLDOUT_BUNDLE_CONTRACT = "qtrad-r2-holdout-bundle-v1"
R2_HOLDOUT_TARGET_PROJECTION_CONTRACT = "qtrad-r2-holdout-target-projection-v2"
R2_PRE_HOLDOUT_TARGET_PROJECTION_CONTRACT = "qtrad-r2-pre-holdout-target-projection-v1"
R2_HOLDOUT_OPPORTUNITY_REGISTRY_CONTRACT = "qtrad-r2-holdout-opportunity-registry-v2"
R2_HOLDOUT_TARGET_SOURCE_CONTRACT = "qtrad-r2-holdout-target-source-v1"

HOLDOUT_ACKNOWLEDGEMENT = "I_ACKNOWLEDGE_THIS_IRREVERSIBLY_CONSUMES_THE_FROZEN_HOLDOUT"


def _semantic_id(value: object) -> str:
    encoded = to_json_value(value)
    return sha256(
        __import__("json").dumps(encoded, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _require_id(value: str, field: str) -> None:
    if len(value) not in (24, 64) or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{field} must be a lowercase SHA-256 identifier")


def _require_text(value: str, field: str) -> None:
    if not value.strip():
        raise ValueError(f"{field} must be non-empty")


def _json_object(value: Mapping[str, object]) -> dict[str, JsonValue]:
    converted = to_json_value(value)
    if not isinstance(converted, dict):
        raise TypeError("contract metadata must serialise to an object")
    return converted


def _contract_json(value: object) -> JsonValue:
    if isinstance(value, datetime):
        return value.isoformat()
    serializer = getattr(value, "as_json", None)
    if callable(serializer):
        return _contract_json(cast(Callable[[], object], serializer)())
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        return {str(key): _contract_json(item) for key, item in mapping.items()}
    if isinstance(value, (list, tuple)):
        sequence = cast(Sequence[object], value)
        return [_contract_json(item) for item in sequence]
    if isinstance(value, StrEnum):
        return value.value
    return to_json_value(value)


def _ordered_ids(values: Sequence[str], field: str) -> tuple[str, ...]:
    result = tuple(values)
    if tuple(sorted(set(result))) != result:
        raise ValueError(f"{field} must be unique and deterministically ordered")
    for value in result:
        _require_id(value, field)
    return result


def _positive_range(value: tuple[datetime, datetime], field: str) -> None:
    require_utc(value[0], f"{field} start")
    require_utc(value[1], f"{field} end")
    if value[1] <= value[0]:
        raise ValueError(f"{field} must be positive")


def _finite(value: float, field: str) -> None:
    if not isfinite(value):
        raise ValueError(f"{field} must be finite")


class HoldoutScope(StrEnum):
    DISPOSABLE_FIXTURE = "DISPOSABLE_FIXTURE"
    CONFIRMATORY = "CONFIRMATORY"


class HoldoutSelectionState(StrEnum):
    SEALED_UNOPENED = "SEALED_UNOPENED"


class HoldoutPreparationState(StrEnum):
    PREPARED_UNOPENED = "PREPARED_UNOPENED"


class HoldoutMarkerState(StrEnum):
    OPENED = "OPENED"
    CONSUMED = "CONSUMED"


class HoldoutConclusion(StrEnum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    INCONCLUSIVE = "INCONCLUSIVE"


class HoldoutDirection(StrEnum):
    HIGHER_IS_BETTER = "HIGHER_IS_BETTER"
    LOWER_IS_BETTER = "LOWER_IS_BETTER"


class HoldoutOpportunityDisposition(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    UNAVAILABLE_FEATURE = "UNAVAILABLE_FEATURE"
    INACTIVE = "INACTIVE"
    GAP = "GAP"
    FAILED_CONFIGURATION = "FAILED_CONFIGURATION"


class FinalFitDisposition(StrEnum):
    READY = "READY"
    INSUFFICIENT_TRAINING = "INSUFFICIENT_TRAINING"
    INSUFFICIENT_INNER_VALIDATION = "INSUFFICIENT_INNER_VALIDATION"
    DEGENERATE_TARGET = "DEGENERATE_TARGET"
    DEGENERATE_FEATURE_MATRIX = "DEGENERATE_FEATURE_MATRIX"
    NON_FINITE_MATRIX = "NON_FINITE_MATRIX"
    NUMERICAL_FAILURE = "NUMERICAL_FAILURE"


@dataclass(frozen=True, slots=True)
class R2FinalFittingPolicy:
    """The complete predeclared policy for each final fit."""

    pre_holdout_membership_policy: str
    maturity_purge_policy: str
    inner_validation_policy: str
    alpha_grid: tuple[float, ...]
    alpha_tie_break_policy: str
    preprocessing_policy: str
    pooled_membership_policy: str
    pooled_weighting_policy: str
    instrument_intercept_policy: str
    solver_identity: Mapping[str, JsonValue]
    training_prediction_threshold: float
    failure_disposition_policy: str
    runtime_identities: Mapping[str, JsonValue]
    policy_id: str

    CONTRACT: ClassVar[str] = "qtrad-r2-final-fitting-policy-v1"
    SCHEMA_VERSION: ClassVar[int] = 1

    def __post_init__(self) -> None:
        for value, field in (
            (self.pre_holdout_membership_policy, "pre-holdout membership policy"),
            (self.maturity_purge_policy, "maturity/purge policy"),
            (self.inner_validation_policy, "inner validation policy"),
            (self.alpha_tie_break_policy, "alpha tie-break policy"),
            (self.preprocessing_policy, "preprocessing policy"),
            (self.pooled_membership_policy, "pooled membership policy"),
            (self.pooled_weighting_policy, "pooled weighting policy"),
            (self.instrument_intercept_policy, "instrument/intercept policy"),
            (self.failure_disposition_policy, "failure disposition policy"),
            (self.policy_id, "final-fitting policy ID"),
        ):
            _require_text(value, field)
        if not self.alpha_grid or tuple(sorted(set(self.alpha_grid))) != self.alpha_grid:
            raise ValueError("final-fitting alpha grid must be ordered and unique")
        if any(value <= 0 or not isfinite(value) for value in self.alpha_grid):
            raise ValueError("final-fitting alpha grid must contain finite positive values")
        _finite(self.training_prediction_threshold, "training-prediction threshold")
        if self.training_prediction_threshold < 0:
            raise ValueError("training-prediction threshold must not be negative")
        _require_id(self.policy_id, "final-fitting policy ID")
        if self.policy_id != _semantic_id(self.semantic_json()):
            raise ValueError("final-fitting policy ID does not authenticate its content")

    @classmethod
    def create(
        cls,
        *,
        pre_holdout_membership_policy: str,
        maturity_purge_policy: str,
        inner_validation_policy: str,
        alpha_grid: Sequence[float],
        alpha_tie_break_policy: str,
        preprocessing_policy: str,
        pooled_membership_policy: str,
        pooled_weighting_policy: str,
        instrument_intercept_policy: str,
        solver_identity: Mapping[str, JsonValue],
        training_prediction_threshold: float,
        failure_disposition_policy: str,
        runtime_identities: Mapping[str, JsonValue],
    ) -> R2FinalFittingPolicy:
        normalised_alpha_grid = tuple(float(value) for value in alpha_grid)
        normalised_training_prediction_threshold = float(training_prediction_threshold)
        semantic = {
            "contract": cls.CONTRACT,
            "schema_version": cls.SCHEMA_VERSION,
            "pre_holdout_membership_policy": pre_holdout_membership_policy,
            "maturity_purge_policy": maturity_purge_policy,
            "inner_validation_policy": inner_validation_policy,
            "alpha_grid": list(normalised_alpha_grid),
            "alpha_tie_break_policy": alpha_tie_break_policy,
            "preprocessing_policy": preprocessing_policy,
            "pooled_membership_policy": pooled_membership_policy,
            "pooled_weighting_policy": pooled_weighting_policy,
            "instrument_intercept_policy": instrument_intercept_policy,
            "solver_identity": solver_identity,
            "training_prediction_threshold": normalised_training_prediction_threshold,
            "failure_disposition_policy": failure_disposition_policy,
        }
        return cls(
            pre_holdout_membership_policy=pre_holdout_membership_policy,
            maturity_purge_policy=maturity_purge_policy,
            inner_validation_policy=inner_validation_policy,
            alpha_grid=normalised_alpha_grid,
            alpha_tie_break_policy=alpha_tie_break_policy,
            preprocessing_policy=preprocessing_policy,
            pooled_membership_policy=pooled_membership_policy,
            pooled_weighting_policy=pooled_weighting_policy,
            instrument_intercept_policy=instrument_intercept_policy,
            solver_identity=solver_identity,
            training_prediction_threshold=normalised_training_prediction_threshold,
            failure_disposition_policy=failure_disposition_policy,
            runtime_identities=runtime_identities,
            policy_id=_semantic_id(semantic),
        )

    def semantic_json(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": self.SCHEMA_VERSION,
            "pre_holdout_membership_policy": self.pre_holdout_membership_policy,
            "maturity_purge_policy": self.maturity_purge_policy,
            "inner_validation_policy": self.inner_validation_policy,
            "alpha_grid": list(self.alpha_grid),
            "alpha_tie_break_policy": self.alpha_tie_break_policy,
            "preprocessing_policy": self.preprocessing_policy,
            "pooled_membership_policy": self.pooled_membership_policy,
            "pooled_weighting_policy": self.pooled_weighting_policy,
            "instrument_intercept_policy": self.instrument_intercept_policy,
            "solver_identity": _json_object(self.solver_identity),
            "training_prediction_threshold": self.training_prediction_threshold,
            "failure_disposition_policy": self.failure_disposition_policy,
        }

    def as_json(self) -> dict[str, JsonValue]:
        return {
            **self.semantic_json(),
            "runtime_identities": _json_object(self.runtime_identities),
            "policy_id": self.policy_id,
        }

    @classmethod
    def from_json(cls, value: object) -> R2FinalFittingPolicy:
        return _policy_from_json_impl(value)


FinalFittingPolicy = R2FinalFittingPolicy


@dataclass(frozen=True, slots=True)
class R2HoldoutQuestion:
    """One predeclared comparison question; its ID is part of the selection freeze."""

    question: str
    candidate_configuration_id: str
    comparator_configuration_id: str
    metric: str
    support_policy: str
    direction: HoldoutDirection
    threshold: float
    minimum_support: int
    minimum_coverage: float
    conclusion_policy: str
    question_id: str

    CONTRACT: ClassVar[str] = "qtrad-r2-holdout-question-v1"

    def __post_init__(self) -> None:
        _require_text(self.question, "holdout question")
        for value, field in (
            (self.candidate_configuration_id, "question candidate configuration ID"),
            (self.comparator_configuration_id, "question comparator configuration ID"),
            (self.question_id, "holdout question ID"),
            (self.metric, "question metric"),
            (self.support_policy, "question support policy"),
            (self.conclusion_policy, "question conclusion policy"),
        ):
            if field.endswith("ID"):
                _require_id(value, field)
            else:
                _require_text(value, field)
        if self.candidate_configuration_id == self.comparator_configuration_id:
            raise ValueError("holdout question candidate and comparator must differ")
        _finite(self.threshold, "question threshold")
        if self.threshold < 0:
            raise ValueError("question threshold must not be negative")
        if self.minimum_support <= 0:
            raise ValueError("question minimum support must be positive")
        if not 0 < self.minimum_coverage <= 1:
            raise ValueError("question minimum coverage must be in (0, 1]")
        _require_id(self.question_id, "holdout question ID")
        if self.question_id != _semantic_id(self.semantic_json()):
            raise ValueError("holdout question ID does not authenticate its content")

    @classmethod
    def create(
        cls,
        *,
        question: str,
        candidate_configuration_id: str,
        comparator_configuration_id: str,
        metric: str,
        support_policy: str,
        direction: HoldoutDirection,
        threshold: float,
        minimum_support: int,
        minimum_coverage: float,
        conclusion_policy: str,
    ) -> R2HoldoutQuestion:
        semantic = {
            "contract": cls.CONTRACT,
            "schema_version": 1,
            "question": question,
            "candidate_configuration_id": candidate_configuration_id,
            "comparator_configuration_id": comparator_configuration_id,
            "metric": metric,
            "support_policy": support_policy,
            "direction": direction.value,
            "threshold": threshold,
            "minimum_support": minimum_support,
            "minimum_coverage": minimum_coverage,
            "conclusion_policy": conclusion_policy,
        }
        return cls(
            question=question,
            candidate_configuration_id=candidate_configuration_id,
            comparator_configuration_id=comparator_configuration_id,
            metric=metric,
            support_policy=support_policy,
            direction=direction,
            threshold=threshold,
            minimum_support=minimum_support,
            minimum_coverage=minimum_coverage,
            conclusion_policy=conclusion_policy,
            question_id=_semantic_id(semantic),
        )

    def semantic_json(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": 1,
            "question": self.question,
            "candidate_configuration_id": self.candidate_configuration_id,
            "comparator_configuration_id": self.comparator_configuration_id,
            "metric": self.metric,
            "support_policy": self.support_policy,
            "direction": self.direction.value,
            "threshold": self.threshold,
            "minimum_support": self.minimum_support,
            "minimum_coverage": self.minimum_coverage,
            "conclusion_policy": self.conclusion_policy,
        }

    def as_json(self) -> dict[str, JsonValue]:
        return {**self.semantic_json(), "question_id": self.question_id}

    @classmethod
    def from_json(cls, value: object) -> R2HoldoutQuestion:
        if not isinstance(value, dict):
            raise ValueError("holdout question must be an object")
        raw = cast(dict[str, object], value)
        required = {
            "contract",
            "schema_version",
            "question",
            "candidate_configuration_id",
            "comparator_configuration_id",
            "metric",
            "support_policy",
            "direction",
            "threshold",
            "minimum_support",
            "minimum_coverage",
            "conclusion_policy",
            "question_id",
        }
        if set(raw) != required or raw["contract"] != cls.CONTRACT or raw["schema_version"] != 1:
            raise ValueError("holdout question has unknown or unsupported fields")
        return cls(
            question=str(raw["question"]),
            candidate_configuration_id=str(raw["candidate_configuration_id"]),
            comparator_configuration_id=str(raw["comparator_configuration_id"]),
            metric=str(raw["metric"]),
            support_policy=str(raw["support_policy"]),
            direction=HoldoutDirection(str(raw["direction"])),
            threshold=float(cast(float | int | str, raw["threshold"])),
            minimum_support=int(cast(float | int | str, raw["minimum_support"])),
            minimum_coverage=float(cast(float | int | str, raw["minimum_coverage"])),
            conclusion_policy=str(raw["conclusion_policy"]),
            question_id=str(raw["question_id"]),
        )


@dataclass(frozen=True, slots=True)
class R2HoldoutSelectionManifest:
    """PR A's immutable selection and question freeze."""

    experiment_configuration_id: str
    foundation_bundle_id: str
    oof_id: str
    evaluation_report_id: str
    prior_selection_manifest_id: str
    source_class: MarketDataSourceClass
    evidence_class: EvidenceClass
    holdout_scope: HoldoutScope
    evaluated_configuration_ids: tuple[str, ...]
    selected_configuration_ids: tuple[str, ...]
    control_configuration_ids: tuple[str, ...]
    holdout_configuration_ids: tuple[str, ...]
    comparator_families: tuple[ModelFamily, ...]
    metric_policy: Mapping[str, JsonValue]
    threshold_policy: Mapping[str, JsonValue]
    final_fitting_policy: R2FinalFittingPolicy
    questions: tuple[R2HoldoutQuestion, ...]
    holdout_range: tuple[datetime, datetime]
    experiment_count: int
    runtime_identities: Mapping[str, JsonValue]
    frozen_metadata: Mapping[str, JsonValue]
    frozen_at: datetime
    frozen_by: str
    state: HoldoutSelectionState
    holdout_outcomes_accessed: bool
    manifest_id: str
    holdout_opportunity_registry: tuple[
        tuple[str, str, str, datetime, int, HoldoutOpportunityDisposition], ...
    ] = ()
    configuration_registry: tuple[
        tuple[str, ModelFamily, str | None, str | None, str | None], ...
    ] = ()
    evaluation_policy: Mapping[str, JsonValue] = dataclass_field(
        default_factory=lambda: cast(dict[str, JsonValue], {})
    )

    CONTRACT: ClassVar[str] = R2_HOLDOUT_SELECTION_CONTRACT
    SCHEMA_VERSION: ClassVar[int] = 1

    def __post_init__(self) -> None:
        for value, field in (
            (self.experiment_configuration_id, "experiment configuration ID"),
            (self.foundation_bundle_id, "foundation bundle ID"),
            (self.oof_id, "OOF semantic ID"),
            (self.evaluation_report_id, "evaluation report ID"),
            (self.prior_selection_manifest_id, "prior selection manifest ID"),
            (self.manifest_id, "holdout selection manifest ID"),
        ):
            _require_id(value, field)
        if self.holdout_scope is HoldoutScope.DISPOSABLE_FIXTURE and (
            self.evidence_class is not EvidenceClass.IMPLEMENTATION
        ):
            raise ValueError("disposable holdout scope requires implementation evidence")
        if self.state is not HoldoutSelectionState.SEALED_UNOPENED:
            raise ValueError("selection freeze must be SEALED_UNOPENED")
        if self.holdout_outcomes_accessed:
            raise ValueError("selection freeze cannot access holdout outcomes")
        evaluated = _ordered_ids(self.evaluated_configuration_ids, "evaluated configuration IDs")
        selected = _ordered_ids(self.selected_configuration_ids, "selected configuration IDs")
        controls = _ordered_ids(self.control_configuration_ids, "control configuration IDs")
        holdout = _ordered_ids(self.holdout_configuration_ids, "holdout configuration IDs")
        if not set(selected) <= set(evaluated) or not set(controls) <= set(evaluated):
            raise ValueError("selected and control configurations must be evaluated")
        if set(selected) & set(controls):
            raise ValueError("selected and control configuration sets must be disjoint")
        if holdout != tuple(sorted((*selected, *controls))):
            raise ValueError("holdout configurations must be exactly selected plus controls")
        if self.experiment_count != len(evaluated) or self.experiment_count <= 0:
            raise ValueError("experiment count must cover the evaluated configurations")
        if not self.comparator_families or len(set(self.comparator_families)) != len(
            self.comparator_families
        ):
            raise ValueError("comparator families must be unique and non-empty")
        opportunity_registry = self.holdout_opportunity_registry
        if not opportunity_registry:
            raise ValueError("holdout opportunity registry must be non-empty")
        if tuple(sorted(opportunity_registry)) != opportunity_registry:
            raise ValueError("holdout opportunity registry must be ordered")
        opportunity_ids = tuple(item[0] for item in opportunity_registry)
        target_ids = tuple(item[1] for item in opportunity_registry)
        if len(set(opportunity_ids)) != len(opportunity_ids):
            raise ValueError("holdout opportunity registry IDs must be unique")
        if len(set(target_ids)) != len(target_ids):
            raise ValueError("holdout opportunity registry target IDs must be unique")
        for (
            opportunity_id,
            target_id,
            instrument_id,
            decision_time,
            horizon_seconds,
            _disposition,
        ) in opportunity_registry:
            _require_id(opportunity_id, "holdout opportunity ID")
            _require_id(target_id, "holdout opportunity target ID")
            _require_text(instrument_id, "holdout opportunity instrument ID")
            require_utc(decision_time, "holdout opportunity decision time")
            if horizon_seconds <= 0:
                raise ValueError("holdout opportunity horizon must be positive")
        primary_horizon = self.evaluation_policy.get("primary_horizon_seconds")
        if not isinstance(primary_horizon, int) or primary_horizon <= 0:
            raise ValueError("selection must freeze the primary horizon")
        if any(item[4] != primary_horizon for item in opportunity_registry):
            raise ValueError("holdout opportunity registry differs from the primary horizon")
        pre_holdout_target_id = self.evaluation_policy.get("pre_holdout_target_dataset_id")
        if not isinstance(pre_holdout_target_id, str):
            raise ValueError("selection must freeze the pre-holdout target dataset")
        _require_id(pre_holdout_target_id, "pre-holdout target dataset ID")
        pre_holdout_projection_id = self.evaluation_policy.get("pre_holdout_projection_id")
        opportunity_registry_id = self.evaluation_policy.get("holdout_opportunity_registry_id")
        target_source_id = self.evaluation_policy.get("holdout_target_source_id")
        if not isinstance(pre_holdout_projection_id, str):
            raise ValueError("selection must bind the pre-holdout target projection")
        if not isinstance(opportunity_registry_id, str):
            raise ValueError("selection must bind the holdout opportunity registry")
        if not isinstance(target_source_id, str):
            raise ValueError("selection must bind the outcome-blind target source")
        _require_id(pre_holdout_projection_id, "pre-holdout target projection ID")
        _require_id(opportunity_registry_id, "holdout opportunity registry ID")
        _require_id(target_source_id, "outcome-blind target source ID")
        if not isinstance(self.evaluation_policy.get("holdout_target_source_artifact"), Mapping):
            raise ValueError("selection must retain the outcome-blind target source artifact")
        if not isinstance(self.evaluation_policy.get("pre_holdout_projection"), Mapping):
            raise ValueError("selection must retain the pre-holdout target projection")
        if not isinstance(
            self.evaluation_policy.get("holdout_opportunity_registry_artifact"), Mapping
        ):
            raise ValueError("selection must retain the holdout opportunity registry artifact")
        if self.configuration_registry:
            registry_ids = tuple(item[0] for item in self.configuration_registry)
            _ordered_ids(registry_ids, "configuration registry IDs")
            if set(registry_ids) != set(evaluated):
                raise ValueError("configuration registry must cover the evaluated configurations")
            for (
                configuration_id,
                _model_family,
                feature_set_id,
                feature_dataset_id,
                manifest_id,
            ) in self.configuration_registry:
                _require_id(configuration_id, "configuration registry configuration ID")
                if feature_set_id is not None:
                    _require_id(feature_set_id, "configuration registry feature-set ID")
                if feature_dataset_id is not None:
                    _require_id(feature_dataset_id, "configuration registry feature-dataset ID")
                if manifest_id is not None:
                    _require_id(manifest_id, "configuration registry model manifest ID")
        if not self.questions or len({item.question_id for item in self.questions}) != len(
            self.questions
        ):
            raise ValueError("question register must be unique and non-empty")
        for question in self.questions:
            if not {
                question.candidate_configuration_id,
                question.comparator_configuration_id,
            } <= set(holdout):
                raise ValueError("question references a configuration outside the holdout set")
        if self.holdout_scope is HoldoutScope.CONFIRMATORY:
            raw_comparisons = self.evaluation_policy.get("comparison_registry")
            if not isinstance(raw_comparisons, (list, tuple)) or not raw_comparisons:
                raise ValueError(
                    "confirmatory holdout requires an authenticated comparison registry"
                )
            allowed_comparisons: set[tuple[ModelFamily, ModelFamily]] = set()
            for raw_comparison in raw_comparisons:
                if (
                    not isinstance(raw_comparison, (list, tuple))
                    or len(raw_comparison) != 2
                    or not all(isinstance(item, str) for item in raw_comparison)
                ):
                    raise ValueError("confirmatory comparison registry entry is invalid")
                try:
                    comparison = (
                        ModelFamily(raw_comparison[0]),
                        ModelFamily(raw_comparison[1]),
                    )
                except ValueError as exc:
                    raise ValueError(
                        "confirmatory comparison registry has an invalid model family"
                    ) from exc
                if comparison in allowed_comparisons:
                    raise ValueError("confirmatory comparison registry contains duplicate pairs")
                allowed_comparisons.add(comparison)
            family_by_configuration = {
                configuration_id: family
                for configuration_id, family, *_ in self.configuration_registry
            }
            for question in self.questions:
                comparison = (
                    family_by_configuration[question.candidate_configuration_id],
                    family_by_configuration[question.comparator_configuration_id],
                )
                if comparison not in allowed_comparisons:
                    raise ValueError(
                        "confirmatory question is not an authenticated immediate comparison"
                    )
        _positive_range(self.holdout_range, "holdout range")
        require_utc(self.frozen_at, "selection freeze time")
        _require_text(self.frozen_by, "selection frozen-by")
        if self.manifest_id != _semantic_id(self.semantic_json()):
            raise ValueError("holdout selection manifest ID does not authenticate its content")

    @classmethod
    def create(cls, **values: object) -> R2HoldoutSelectionManifest:
        raw = dict(values)
        raw.pop("manifest_id", None)
        raw.setdefault("state", HoldoutSelectionState.SEALED_UNOPENED)
        raw.setdefault("holdout_outcomes_accessed", False)
        raw.setdefault("holdout_opportunity_registry", ())
        raw.setdefault("configuration_registry", ())
        raw.setdefault("evaluation_policy", {})
        raw["evaluated_configuration_ids"] = tuple(
            sorted(cast(Sequence[str], raw["evaluated_configuration_ids"]))
        )
        raw["selected_configuration_ids"] = tuple(
            sorted(cast(Sequence[str], raw["selected_configuration_ids"]))
        )
        raw["control_configuration_ids"] = tuple(
            sorted(cast(Sequence[str], raw["control_configuration_ids"]))
        )
        raw["holdout_configuration_ids"] = tuple(
            sorted(cast(Sequence[str], raw["holdout_configuration_ids"]))
        )
        raw["comparator_families"] = tuple(cast(Sequence[ModelFamily], raw["comparator_families"]))
        raw["holdout_opportunity_registry"] = tuple(
            cast(
                Sequence[tuple[str, str, str, datetime, int, HoldoutOpportunityDisposition]],
                raw["holdout_opportunity_registry"],
            )
        )
        raw["configuration_registry"] = tuple(
            cast(
                Sequence[tuple[str, ModelFamily, str | None, str | None, str | None]],
                raw["configuration_registry"],
            )
        )
        raw["questions"] = tuple(cast(Sequence[R2HoldoutQuestion], raw["questions"]))
        provisional = object.__new__(cls)
        for field, value in raw.items():
            object.__setattr__(provisional, field, value)
        constructor = cast(Callable[..., R2HoldoutSelectionManifest], cls)
        return constructor(
            **raw,
            manifest_id=_semantic_id(provisional.semantic_json()),
        )

    def semantic_json(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": self.SCHEMA_VERSION,
            "experiment_configuration_id": self.experiment_configuration_id,
            "oof_id": self.oof_id,
            "evaluation_report_id": self.evaluation_report_id,
            "prior_selection_manifest_id": self.prior_selection_manifest_id,
            "source_class": self.source_class.value,
            "evidence_class": self.evidence_class.value,
            "holdout_scope": self.holdout_scope.value,
            "evaluated_configuration_ids": list(self.evaluated_configuration_ids),
            "selected_configuration_ids": list(self.selected_configuration_ids),
            "control_configuration_ids": list(self.control_configuration_ids),
            "holdout_configuration_ids": list(self.holdout_configuration_ids),
            "comparator_families": [item.value for item in self.comparator_families],
            "holdout_opportunity_registry": [
                [
                    opportunity_id,
                    target_id,
                    instrument_id,
                    decision_time.isoformat(),
                    horizon_seconds,
                    disposition.value,
                ]
                for (
                    opportunity_id,
                    target_id,
                    instrument_id,
                    decision_time,
                    horizon_seconds,
                    disposition,
                ) in self.holdout_opportunity_registry
            ],
            "configuration_registry": [
                [
                    configuration_id,
                    model_family.value,
                    feature_set_id,
                    feature_dataset_id,
                    manifest_id,
                ]
                for (
                    configuration_id,
                    model_family,
                    feature_set_id,
                    feature_dataset_id,
                    manifest_id,
                ) in self.configuration_registry
            ],
            "metric_policy": _json_object(self.metric_policy),
            "threshold_policy": _json_object(self.threshold_policy),
            "evaluation_policy": _json_object(self.evaluation_policy),
            "final_fitting_policy": self.final_fitting_policy.semantic_json(),
            "questions": [item.as_json() for item in self.questions],
            "holdout_range": [item.isoformat() for item in self.holdout_range],
            "experiment_count": self.experiment_count,
            "state": self.state.value,
            "holdout_outcomes_accessed": self.holdout_outcomes_accessed,
        }

    def as_json(self) -> dict[str, JsonValue]:
        return {
            **self.semantic_json(),
            "foundation_bundle_id": self.foundation_bundle_id,
            "runtime_identities": _json_object(self.runtime_identities),
            "frozen_metadata": _json_object(self.frozen_metadata),
            "frozen_at": self.frozen_at.isoformat(),
            "frozen_by": self.frozen_by,
            "final_fitting_policy": self.final_fitting_policy.as_json(),
            "manifest_id": self.manifest_id,
        }

    @classmethod
    def from_json(cls, value: object) -> R2HoldoutSelectionManifest:
        if not isinstance(value, dict):
            raise ValueError("holdout selection manifest must be an object")
        raw = cast(dict[str, object], value)
        expected = {
            "contract",
            "schema_version",
            "experiment_configuration_id",
            "foundation_bundle_id",
            "oof_id",
            "evaluation_report_id",
            "prior_selection_manifest_id",
            "source_class",
            "evidence_class",
            "holdout_scope",
            "evaluated_configuration_ids",
            "selected_configuration_ids",
            "control_configuration_ids",
            "holdout_configuration_ids",
            "comparator_families",
            "holdout_opportunity_registry",
            "configuration_registry",
            "metric_policy",
            "threshold_policy",
            "evaluation_policy",
            "final_fitting_policy",
            "questions",
            "holdout_range",
            "experiment_count",
            "runtime_identities",
            "frozen_metadata",
            "frozen_at",
            "frozen_by",
            "state",
            "holdout_outcomes_accessed",
            "manifest_id",
        }
        if set(raw) != expected or raw["contract"] != cls.CONTRACT or raw["schema_version"] != 1:
            raise ValueError("holdout selection manifest has unknown or unsupported fields")
        policy = R2FinalFittingPolicy.from_json(raw["final_fitting_policy"])
        question_values = raw["questions"]
        registry_values = raw["configuration_registry"]
        if not isinstance(question_values, list):
            raise ValueError("holdout selection question register must be an array")
        if not isinstance(registry_values, list):
            raise ValueError("holdout configuration registry must be an array")
        opportunity_values = raw["holdout_opportunity_registry"]
        if not isinstance(opportunity_values, list):
            raise ValueError("holdout opportunity registry must be an array")
        opportunity_registry: list[
            tuple[str, str, str, datetime, int, HoldoutOpportunityDisposition]
        ] = []
        for raw_item in cast(list[object], opportunity_values):
            if not isinstance(raw_item, list) or len(cast(list[object], raw_item)) != 6:
                raise ValueError("holdout opportunity registry entry is invalid")
            item = cast(list[object], raw_item)
            opportunity_registry.append(
                (
                    str(item[0]),
                    str(item[1]),
                    str(item[2]),
                    datetime.fromisoformat(str(item[3])),
                    int(cast(float | int | str, item[4])),
                    HoldoutOpportunityDisposition(str(item[5])),
                )
            )
        registry: list[tuple[str, ModelFamily, str | None, str | None, str | None]] = []
        for raw_item in cast(list[object], registry_values):
            if not isinstance(raw_item, list):
                raise ValueError("holdout configuration registry entry is invalid")
            item = cast(list[object], raw_item)
            if len(item) != 5:
                raise ValueError("holdout configuration registry entry is invalid")
            registry.append(
                (
                    str(item[0]),
                    ModelFamily(str(item[1])),
                    None if item[2] is None else str(item[2]),
                    None if item[3] is None else str(item[3]),
                    None if item[4] is None else str(item[4]),
                )
            )
        return cls(
            experiment_configuration_id=str(raw["experiment_configuration_id"]),
            foundation_bundle_id=str(raw["foundation_bundle_id"]),
            oof_id=str(raw["oof_id"]),
            evaluation_report_id=str(raw["evaluation_report_id"]),
            prior_selection_manifest_id=str(raw["prior_selection_manifest_id"]),
            source_class=MarketDataSourceClass(str(raw["source_class"])),
            evidence_class=EvidenceClass(str(raw["evidence_class"])),
            holdout_scope=HoldoutScope(str(raw["holdout_scope"])),
            evaluated_configuration_ids=tuple(
                str(item) for item in cast(list[object], raw["evaluated_configuration_ids"])
            ),
            selected_configuration_ids=tuple(
                str(item) for item in cast(list[object], raw["selected_configuration_ids"])
            ),
            control_configuration_ids=tuple(
                str(item) for item in cast(list[object], raw["control_configuration_ids"])
            ),
            holdout_configuration_ids=tuple(
                str(item) for item in cast(list[object], raw["holdout_configuration_ids"])
            ),
            comparator_families=tuple(
                ModelFamily(str(item)) for item in cast(list[object], raw["comparator_families"])
            ),
            holdout_opportunity_registry=tuple(opportunity_registry),
            configuration_registry=tuple(registry),
            metric_policy=cast(Mapping[str, JsonValue], raw["metric_policy"]),
            threshold_policy=cast(Mapping[str, JsonValue], raw["threshold_policy"]),
            evaluation_policy=cast(Mapping[str, JsonValue], raw["evaluation_policy"]),
            final_fitting_policy=policy,
            questions=tuple(
                R2HoldoutQuestion.from_json(item) for item in cast(list[object], question_values)
            ),
            holdout_range=(
                datetime.fromisoformat(str(cast(list[object], raw["holdout_range"])[0])),
                datetime.fromisoformat(str(cast(list[object], raw["holdout_range"])[1])),
            ),
            experiment_count=int(cast(float | int | str, raw["experiment_count"])),
            runtime_identities=cast(Mapping[str, JsonValue], raw["runtime_identities"]),
            frozen_metadata=cast(Mapping[str, JsonValue], raw["frozen_metadata"]),
            frozen_at=datetime.fromisoformat(str(raw["frozen_at"])),
            frozen_by=str(raw["frozen_by"]),
            state=HoldoutSelectionState(str(raw["state"])),
            holdout_outcomes_accessed=bool(raw["holdout_outcomes_accessed"]),
            manifest_id=str(raw["manifest_id"]),
        )


def _policy_from_json_impl(value: object) -> R2FinalFittingPolicy:
    if not isinstance(value, dict):
        raise ValueError("final-fitting policy must be an object")
    raw = cast(dict[str, object], value)
    expected = {
        "contract",
        "schema_version",
        "pre_holdout_membership_policy",
        "maturity_purge_policy",
        "inner_validation_policy",
        "alpha_grid",
        "alpha_tie_break_policy",
        "preprocessing_policy",
        "pooled_membership_policy",
        "pooled_weighting_policy",
        "instrument_intercept_policy",
        "solver_identity",
        "training_prediction_threshold",
        "failure_disposition_policy",
        "runtime_identities",
        "policy_id",
    }
    if set(raw) != expected or raw["contract"] != R2FinalFittingPolicy.CONTRACT:
        raise ValueError("final-fitting policy has unknown or unsupported fields")
    alpha = raw["alpha_grid"]
    if not isinstance(alpha, list):
        raise ValueError("final-fitting alpha grid must be an array")
    return R2FinalFittingPolicy(
        pre_holdout_membership_policy=str(raw["pre_holdout_membership_policy"]),
        maturity_purge_policy=str(raw["maturity_purge_policy"]),
        inner_validation_policy=str(raw["inner_validation_policy"]),
        alpha_grid=tuple(float(item) for item in cast(list[float | int | str], alpha)),
        alpha_tie_break_policy=str(raw["alpha_tie_break_policy"]),
        preprocessing_policy=str(raw["preprocessing_policy"]),
        pooled_membership_policy=str(raw["pooled_membership_policy"]),
        pooled_weighting_policy=str(raw["pooled_weighting_policy"]),
        instrument_intercept_policy=str(raw["instrument_intercept_policy"]),
        solver_identity=cast(Mapping[str, JsonValue], raw["solver_identity"]),
        training_prediction_threshold=float(
            cast(float | int | str, raw["training_prediction_threshold"])
        ),
        failure_disposition_policy=str(raw["failure_disposition_policy"]),
        runtime_identities=cast(Mapping[str, JsonValue], raw["runtime_identities"]),
        policy_id=str(raw["policy_id"]),
    )


@dataclass(frozen=True, slots=True)
class HoldoutTargetOpportunity:
    """Outcome-blind projection of one target opportunity."""

    target_id: str
    instrument_id: str
    decision_time: datetime
    target_horizon_seconds: int
    feature_data_asof: datetime
    latest_feature_bar_end: datetime
    dependency_start: datetime
    dependency_end: datetime
    disposition: HoldoutOpportunityDisposition
    opportunity_id: str

    CONTRACT: ClassVar[str] = "qtrad-r2-holdout-outcome-blind-opportunity-v1"

    def __post_init__(self) -> None:
        for value, field in (
            (self.target_id, "target ID"),
            (self.opportunity_id, "opportunity ID"),
        ):
            _require_id(value, field)
        _require_text(self.instrument_id, "opportunity instrument")
        for value, field in (
            (self.decision_time, "opportunity decision time"),
            (self.feature_data_asof, "opportunity feature as-of"),
            (self.latest_feature_bar_end, "opportunity latest feature bar end"),
            (self.dependency_start, "opportunity dependency start"),
            (self.dependency_end, "opportunity dependency end"),
        ):
            require_utc(value, field)
        if self.target_horizon_seconds <= 0:
            raise ValueError("opportunity horizon must be positive")
        if self.dependency_end <= self.dependency_start:
            raise ValueError("opportunity dependency interval must be positive")
        if self.latest_feature_bar_end > self.feature_data_asof:
            raise ValueError("latest feature bar end exceeds feature data as-of")
        if self.opportunity_id != _semantic_id(self.semantic_json()):
            raise ValueError("opportunity ID does not authenticate its content")

    @classmethod
    def create(
        cls,
        *,
        target_id: str,
        instrument_id: str,
        decision_time: datetime,
        target_horizon_seconds: int,
        feature_data_asof: datetime,
        latest_feature_bar_end: datetime,
        dependency_start: datetime,
        dependency_end: datetime,
        disposition: HoldoutOpportunityDisposition = HoldoutOpportunityDisposition.ELIGIBLE,
    ) -> HoldoutTargetOpportunity:
        semantic = {
            "contract": cls.CONTRACT,
            "schema_version": 1,
            "target_id": target_id,
            "instrument_id": instrument_id,
            "decision_time": decision_time.isoformat(),
            "target_horizon_seconds": target_horizon_seconds,
            "feature_data_asof": feature_data_asof.isoformat(),
            "latest_feature_bar_end": latest_feature_bar_end.isoformat(),
            "dependency_start": dependency_start.isoformat(),
            "dependency_end": dependency_end.isoformat(),
            "disposition": disposition.value,
        }
        return cls(
            target_id=target_id,
            instrument_id=instrument_id,
            decision_time=decision_time,
            target_horizon_seconds=target_horizon_seconds,
            feature_data_asof=feature_data_asof,
            latest_feature_bar_end=latest_feature_bar_end,
            dependency_start=dependency_start,
            dependency_end=dependency_end,
            disposition=disposition,
            opportunity_id=_semantic_id(semantic),
        )

    def semantic_json(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": 1,
            "target_id": self.target_id,
            "instrument_id": self.instrument_id,
            "decision_time": self.decision_time.isoformat(),
            "target_horizon_seconds": self.target_horizon_seconds,
            "feature_data_asof": self.feature_data_asof.isoformat(),
            "latest_feature_bar_end": self.latest_feature_bar_end.isoformat(),
            "dependency_start": self.dependency_start.isoformat(),
            "dependency_end": self.dependency_end.isoformat(),
            "disposition": self.disposition.value,
        }

    def as_json(self) -> dict[str, JsonValue]:
        return {**self.semantic_json(), "opportunity_id": self.opportunity_id}

    @classmethod
    def from_json(cls, value: object) -> HoldoutTargetOpportunity:
        if not isinstance(value, Mapping):
            raise ValueError("holdout opportunity must be an object")
        raw = cast(Mapping[str, object], value)
        expected = {
            "contract",
            "schema_version",
            "target_id",
            "instrument_id",
            "decision_time",
            "target_horizon_seconds",
            "feature_data_asof",
            "latest_feature_bar_end",
            "dependency_start",
            "dependency_end",
            "disposition",
            "opportunity_id",
        }
        if set(raw) != expected:
            raise ValueError("holdout opportunity has unknown or missing fields")
        if raw["contract"] != cls.CONTRACT or raw["schema_version"] != 1:
            raise ValueError("holdout opportunity contract is unsupported")
        return cls(
            target_id=str(raw["target_id"]),
            instrument_id=str(raw["instrument_id"]),
            decision_time=datetime.fromisoformat(str(raw["decision_time"])),
            target_horizon_seconds=int(cast(float | int | str, raw["target_horizon_seconds"])),
            feature_data_asof=datetime.fromisoformat(str(raw["feature_data_asof"])),
            latest_feature_bar_end=datetime.fromisoformat(str(raw["latest_feature_bar_end"])),
            dependency_start=datetime.fromisoformat(str(raw["dependency_start"])),
            dependency_end=datetime.fromisoformat(str(raw["dependency_end"])),
            disposition=HoldoutOpportunityDisposition(str(raw["disposition"])),
            opportunity_id=str(raw["opportunity_id"]),
        )


def _project_pre_holdout_target(
    source: TargetDataset,
    *,
    holdout_start: datetime,
    primary_horizon_seconds: int,
    target_instruments: Sequence[str] | None = None,
) -> TargetDataset:
    allowed_instruments = None if target_instruments is None else set(target_instruments)
    rows = tuple(
        row
        for row in source.rows
        if row.horizon.total_seconds() == primary_horizon_seconds
        and (allowed_instruments is None or row.instrument_id in allowed_instruments)
        and row.decision_time < holdout_start
        and row.target_available_at <= holdout_start
    )
    return TargetDataset.create(
        rows,
        observation_dataset_id=source.observation_dataset_id,
        foundation_configuration_id=source.foundation_configuration_id,
    )


def _target_dataset_from_json(value: Mapping[str, object]) -> TargetDataset:
    expected_fields = {
        "contract",
        "schema_version",
        "dataset_id",
        "observation_dataset_id",
        "foundation_configuration_id",
        "rows",
    }
    if set(value) != expected_fields:
        raise ValueError("target dataset has unknown or missing fields")
    if value["contract"] != TARGET_DATASET_CONTRACT or value["schema_version"] != 1:
        raise ValueError("target dataset contract is unsupported")
    raw_rows = value["rows"]
    if not isinstance(raw_rows, list):
        raise ValueError("target dataset rows must be a list")
    rows: list[TargetRow] = []
    row_fields = {
        "instrument_id",
        "decision_time",
        "horizon_seconds",
        "target_basis",
        "target_revision_policy",
        "target_start_time",
        "target_end_time",
        "target_freeze_at",
        "target_available_at",
        "label_start_close",
        "label_end_close",
        "log_return",
        "return_disposition",
        "start_event_id",
        "end_event_id",
        "upper_log_excursion",
        "lower_log_excursion",
        "excursion_disposition",
    }
    for raw_row in cast(list[object], raw_rows):
        if not isinstance(raw_row, Mapping):
            raise ValueError("target dataset row must be an object")
        row = cast(Mapping[str, object], raw_row)
        if set(row) != row_fields:
            raise ValueError("target dataset row has unknown or missing fields")
        start_event = row["start_event_id"]
        end_event = row["end_event_id"]
        rows.append(
            TargetRow(
                instrument_id=str(row["instrument_id"]),
                decision_time=datetime.fromisoformat(str(row["decision_time"])),
                horizon=timedelta(seconds=float(cast(float | int | str, row["horizon_seconds"]))),
                target_basis=PriceBasis(str(row["target_basis"])),
                target_revision_policy=str(row["target_revision_policy"]),
                target_start_time=datetime.fromisoformat(str(row["target_start_time"])),
                target_end_time=datetime.fromisoformat(str(row["target_end_time"])),
                target_freeze_at=datetime.fromisoformat(str(row["target_freeze_at"])),
                target_available_at=datetime.fromisoformat(str(row["target_available_at"])),
                label_start_close=(
                    Decimal(str(row["label_start_close"]))
                    if row["label_start_close"] is not None
                    else None
                ),
                label_end_close=(
                    Decimal(str(row["label_end_close"]))
                    if row["label_end_close"] is not None
                    else None
                ),
                log_return=(
                    float(cast(float | int | str, row["log_return"]))
                    if row["log_return"] is not None
                    else None
                ),
                return_disposition=ReturnDisposition(str(row["return_disposition"])),
                start_event_id=UUID(str(start_event)) if start_event is not None else None,
                end_event_id=UUID(str(end_event)) if end_event is not None else None,
                upper_log_excursion=(
                    float(cast(float | int | str, row["upper_log_excursion"]))
                    if row["upper_log_excursion"] is not None
                    else None
                ),
                lower_log_excursion=(
                    float(cast(float | int | str, row["lower_log_excursion"]))
                    if row["lower_log_excursion"] is not None
                    else None
                ),
                excursion_disposition=ExcursionDisposition(str(row["excursion_disposition"])),
            )
        )
    dataset = TargetDataset.create(
        rows,
        observation_dataset_id=str(value["observation_dataset_id"]),
        foundation_configuration_id=str(value["foundation_configuration_id"]),
    )
    if dataset.dataset_id != str(value["dataset_id"]):
        raise ValueError("target dataset ID does not authenticate its rows")
    return dataset


@dataclass(frozen=True, slots=True)
class R2HoldoutTargetIdentity:
    """Outcome-blind identity and causal availability metadata for one target row."""

    target_id: str
    instrument_id: str
    decision_time: datetime
    target_horizon_seconds: int
    target_basis: PriceBasis
    target_revision_policy: str
    target_start_time: datetime
    target_end_time: datetime
    target_freeze_at: datetime
    target_available_at: datetime
    target_availability_disposition: ReturnDisposition

    CONTRACT: ClassVar[str] = "qtrad-r2-holdout-target-identity-v1"

    def __post_init__(self) -> None:
        _require_id(self.target_id, "holdout target identity")
        _require_text(self.instrument_id, "holdout target instrument")
        _require_text(self.target_revision_policy, "holdout target revision policy")
        for value, field in (
            (self.decision_time, "holdout target decision time"),
            (self.target_start_time, "holdout target start time"),
            (self.target_end_time, "holdout target end time"),
            (self.target_freeze_at, "holdout target freeze time"),
            (self.target_available_at, "holdout target availability time"),
        ):
            require_utc(value, field)
        if self.target_horizon_seconds <= 0:
            raise ValueError("holdout target identity horizon must be positive")
        horizon = timedelta(seconds=self.target_horizon_seconds)
        if self.target_end_time != self.target_start_time + horizon:
            raise ValueError("holdout target identity interval differs from its horizon")
        if self.target_available_at != self.target_freeze_at:
            raise ValueError("holdout target identity availability differs from its freeze time")
        expected = target_identity(
            instrument_id=self.instrument_id,
            decision_time=self.decision_time,
            horizon=horizon,
            target_basis=self.target_basis,
            target_revision_policy=self.target_revision_policy,
        )
        if self.target_id != expected:
            raise ValueError("holdout target identity does not authenticate its target key")

    @classmethod
    def from_row(cls, row: TargetRow) -> R2HoldoutTargetIdentity:
        return cls(
            target_id=row.target_id,
            instrument_id=row.instrument_id,
            decision_time=row.decision_time,
            target_horizon_seconds=int(row.horizon.total_seconds()),
            target_basis=row.target_basis,
            target_revision_policy=row.target_revision_policy,
            target_start_time=row.target_start_time,
            target_end_time=row.target_end_time,
            target_freeze_at=row.target_freeze_at,
            target_available_at=row.target_available_at,
            target_availability_disposition=row.return_disposition,
        )

    def matches_row(self, row: TargetRow) -> bool:
        return self == self.from_row(row)

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": 1,
            "target_id": self.target_id,
            "instrument_id": self.instrument_id,
            "decision_time": self.decision_time.isoformat(),
            "target_horizon_seconds": self.target_horizon_seconds,
            "target_basis": self.target_basis.value,
            "target_revision_policy": self.target_revision_policy,
            "target_start_time": self.target_start_time.isoformat(),
            "target_end_time": self.target_end_time.isoformat(),
            "target_freeze_at": self.target_freeze_at.isoformat(),
            "target_available_at": self.target_available_at.isoformat(),
            "target_availability_disposition": self.target_availability_disposition.value,
        }

    @classmethod
    def from_json(cls, value: object) -> R2HoldoutTargetIdentity:
        if not isinstance(value, Mapping):
            raise ValueError("holdout target identity must be an object")
        raw = cast(Mapping[str, object], value)
        expected = {
            "contract",
            "schema_version",
            "target_id",
            "instrument_id",
            "decision_time",
            "target_horizon_seconds",
            "target_basis",
            "target_revision_policy",
            "target_start_time",
            "target_end_time",
            "target_freeze_at",
            "target_available_at",
            "target_availability_disposition",
        }
        if set(raw) != expected or raw["contract"] != cls.CONTRACT or raw["schema_version"] != 1:
            raise ValueError("holdout target identity has unknown or unsupported fields")
        return cls(
            target_id=str(raw["target_id"]),
            instrument_id=str(raw["instrument_id"]),
            decision_time=datetime.fromisoformat(str(raw["decision_time"])),
            target_horizon_seconds=int(cast(float | int | str, raw["target_horizon_seconds"])),
            target_basis=PriceBasis(str(raw["target_basis"])),
            target_revision_policy=str(raw["target_revision_policy"]),
            target_start_time=datetime.fromisoformat(str(raw["target_start_time"])),
            target_end_time=datetime.fromisoformat(str(raw["target_end_time"])),
            target_freeze_at=datetime.fromisoformat(str(raw["target_freeze_at"])),
            target_available_at=datetime.fromisoformat(str(raw["target_available_at"])),
            target_availability_disposition=ReturnDisposition(
                str(raw["target_availability_disposition"])
            ),
        )


@dataclass(frozen=True, slots=True)
class R2HoldoutTargetIndex:
    """Outcome-blind target identity projection authenticated at R1."""

    source_target_dataset_id: str
    observation_dataset_id: str
    foundation_configuration_id: str
    targets: tuple[R2HoldoutTargetIdentity, ...]
    dataset_id: str

    CONTRACT: ClassVar[str] = "qtrad-r2-holdout-target-index-v1"
    SCHEMA_VERSION: ClassVar[int] = 1

    def __post_init__(self) -> None:
        _require_id(self.source_target_dataset_id, "holdout target index source dataset ID")
        _require_id(self.observation_dataset_id, "holdout target index observation ID")
        _require_id(self.foundation_configuration_id, "holdout target index configuration ID")
        _require_id(self.dataset_id, "holdout target index ID")
        if tuple(sorted(self.targets, key=lambda item: item.target_id)) != self.targets:
            raise ValueError("holdout target index identities must be ordered")
        if self.dataset_id != self._identity(
            self.targets,
            source_target_dataset_id=self.source_target_dataset_id,
            observation_dataset_id=self.observation_dataset_id,
            foundation_configuration_id=self.foundation_configuration_id,
        ):
            raise ValueError("holdout target index ID does not authenticate its identities")

    @classmethod
    def _identity(
        cls,
        targets: Sequence[R2HoldoutTargetIdentity],
        *,
        source_target_dataset_id: str | None = None,
        observation_dataset_id: str | None = None,
        foundation_configuration_id: str | None = None,
    ) -> str:
        return _semantic_id(
            {
                "contract": cls.CONTRACT,
                "schema_version": cls.SCHEMA_VERSION,
                "source_target_dataset_id": source_target_dataset_id,
                "observation_dataset_id": observation_dataset_id,
                "foundation_configuration_id": foundation_configuration_id,
                "targets": [item.as_json() for item in targets],
            }
        )

    @classmethod
    def create(cls, target_dataset: TargetDataset) -> R2HoldoutTargetIndex:
        targets = tuple(
            sorted(
                (R2HoldoutTargetIdentity.from_row(row) for row in target_dataset.rows),
                key=lambda item: item.target_id,
            )
        )
        return cls(
            source_target_dataset_id=target_dataset.dataset_id,
            observation_dataset_id=target_dataset.observation_dataset_id,
            foundation_configuration_id=target_dataset.foundation_configuration_id,
            targets=targets,
            dataset_id=cls._identity(
                targets,
                source_target_dataset_id=target_dataset.dataset_id,
                observation_dataset_id=target_dataset.observation_dataset_id,
                foundation_configuration_id=target_dataset.foundation_configuration_id,
            ),
        )

    @classmethod
    def from_rows(
        cls,
        *,
        source_target_dataset_id: str,
        observation_dataset_id: str,
        foundation_configuration_id: str,
        rows: Sequence[Mapping[str, object]],
    ) -> R2HoldoutTargetIndex:
        targets = tuple(
            sorted(
                (R2HoldoutTargetIdentity.from_json(row) for row in rows),
                key=lambda item: item.target_id,
            )
        )
        return cls(
            source_target_dataset_id=source_target_dataset_id,
            observation_dataset_id=observation_dataset_id,
            foundation_configuration_id=foundation_configuration_id,
            targets=targets,
            dataset_id=cls._identity(
                targets,
                source_target_dataset_id=source_target_dataset_id,
                observation_dataset_id=observation_dataset_id,
                foundation_configuration_id=foundation_configuration_id,
            ),
        )


@dataclass(frozen=True, slots=True)
class R2HoldoutCausalMetadataRow:
    """OHLC-free panel metadata used to derive holdout opportunities."""

    instrument_id: str
    basis: PriceBasis
    decision_time: datetime
    feature_data_asof: datetime
    latest_feature_bar_end: datetime
    status: PanelStatus
    audit_disposition: PanelAuditDisposition | None
    interval_start: datetime | None
    interval_end: datetime | None

    CONTRACT: ClassVar[str] = "qtrad-r2-holdout-causal-metadata-row-v1"

    def __post_init__(self) -> None:
        _require_text(self.instrument_id, "holdout causal metadata instrument")
        for value, field in (
            (self.decision_time, "causal metadata decision time"),
            (self.feature_data_asof, "causal metadata feature cutoff"),
            (self.latest_feature_bar_end, "causal metadata latest bar"),
        ):
            require_utc(value, field)
        for value, field in (
            (self.interval_start, "causal metadata interval start"),
            (self.interval_end, "causal metadata interval end"),
        ):
            if value is not None:
                require_utc(value, field)
        if (self.interval_start is None) != (self.interval_end is None):
            raise ValueError("causal metadata interval bounds must be paired")

    @classmethod
    def from_panel(cls, row: PanelRow) -> R2HoldoutCausalMetadataRow:
        return cls(
            instrument_id=row.instrument_id,
            basis=row.basis,
            decision_time=row.decision_time,
            feature_data_asof=row.feature_data_asof,
            latest_feature_bar_end=row.latest_feature_bar_end,
            status=row.status,
            audit_disposition=row.audit_disposition,
            interval_start=row.interval_start,
            interval_end=row.interval_end,
        )

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": 1,
            "instrument_id": self.instrument_id,
            "basis": self.basis.value,
            "decision_time": self.decision_time.isoformat(),
            "feature_data_asof": self.feature_data_asof.isoformat(),
            "latest_feature_bar_end": self.latest_feature_bar_end.isoformat(),
            "status": self.status.value,
            "audit_disposition": (
                self.audit_disposition.value if self.audit_disposition is not None else None
            ),
            "interval_start": (
                self.interval_start.isoformat() if self.interval_start is not None else None
            ),
            "interval_end": self.interval_end.isoformat()
            if self.interval_end is not None
            else None,
        }

    @classmethod
    def from_json(cls, value: object) -> R2HoldoutCausalMetadataRow:
        if not isinstance(value, Mapping):
            raise ValueError("holdout causal metadata row must be an object")
        raw = cast(Mapping[str, object], value)
        expected = {
            "contract",
            "schema_version",
            "instrument_id",
            "basis",
            "decision_time",
            "feature_data_asof",
            "latest_feature_bar_end",
            "status",
            "audit_disposition",
            "interval_start",
            "interval_end",
        }
        if set(raw) != expected or raw["contract"] != cls.CONTRACT or raw["schema_version"] != 1:
            raise ValueError("holdout causal metadata row has unknown or unsupported fields")
        return cls(
            instrument_id=str(raw["instrument_id"]),
            basis=PriceBasis(str(raw["basis"])),
            decision_time=datetime.fromisoformat(str(raw["decision_time"])),
            feature_data_asof=datetime.fromisoformat(str(raw["feature_data_asof"])),
            latest_feature_bar_end=datetime.fromisoformat(str(raw["latest_feature_bar_end"])),
            status=PanelStatus(str(raw["status"])),
            audit_disposition=(
                None
                if raw["audit_disposition"] is None
                else PanelAuditDisposition(str(raw["audit_disposition"]))
            ),
            interval_start=(
                None
                if raw["interval_start"] is None
                else datetime.fromisoformat(str(raw["interval_start"]))
            ),
            interval_end=(
                None
                if raw["interval_end"] is None
                else datetime.fromisoformat(str(raw["interval_end"]))
            ),
        )


@dataclass(frozen=True, slots=True)
class R2HoldoutCausalMetadata:
    """Authenticated, outcome- and OHLC-free projection of an R1 panel."""

    source_panel_dataset_id: str
    rows: tuple[R2HoldoutCausalMetadataRow, ...]
    dataset_id: str

    CONTRACT: ClassVar[str] = "qtrad-r2-holdout-causal-metadata-v1"
    SCHEMA_VERSION: ClassVar[int] = 1

    def __post_init__(self) -> None:
        _require_id(self.source_panel_dataset_id, "causal metadata panel ID")
        _require_id(self.dataset_id, "causal metadata dataset ID")
        if self.dataset_id != self._identity(
            self.rows, source_panel_dataset_id=self.source_panel_dataset_id
        ):
            raise ValueError("causal metadata ID does not authenticate its rows")

    @classmethod
    def _identity(
        cls,
        rows: Sequence[R2HoldoutCausalMetadataRow],
        *,
        source_panel_dataset_id: str | None = None,
    ) -> str:
        return _semantic_id(
            {
                "contract": cls.CONTRACT,
                "schema_version": cls.SCHEMA_VERSION,
                "source_panel_dataset_id": source_panel_dataset_id,
                "rows": [row.as_json() for row in rows],
            }
        )

    @classmethod
    def create(cls, panel: PanelDataset) -> R2HoldoutCausalMetadata:
        rows = tuple(R2HoldoutCausalMetadataRow.from_panel(row) for row in panel.rows)
        return cls(
            source_panel_dataset_id=panel.dataset_id,
            rows=rows,
            dataset_id=cls._identity(rows, source_panel_dataset_id=panel.dataset_id),
        )

    @classmethod
    def from_rows(
        cls, *, source_panel_dataset_id: str, rows: Sequence[Mapping[str, object]]
    ) -> R2HoldoutCausalMetadata:
        ordered = tuple(R2HoldoutCausalMetadataRow.from_json(row) for row in rows)
        return cls(
            source_panel_dataset_id=source_panel_dataset_id,
            rows=ordered,
            dataset_id=cls._identity(ordered, source_panel_dataset_id=source_panel_dataset_id),
        )


@dataclass(frozen=True, slots=True)
class R2OutcomeBlindObservationView:
    """Pre-holdout observation rows retaining the authenticated R1 identity."""

    dataset_id: str
    rows: tuple[ObservationRow, ...]
    configuration: Mapping[str, JsonValue]
    source_dataset_ids: tuple[str, ...]
    selection_policies: Mapping[str, JsonValue]
    projection_id: str

    CONTRACT: ClassVar[str] = "qtrad-r2-outcome-blind-observations-v1"

    @classmethod
    def compute_projection_id(
        cls,
        *,
        source_dataset_id: str,
        holdout_start: datetime,
        rows: Sequence[ObservationRow],
    ) -> str:
        return _semantic_id(
            {
                "contract": cls.CONTRACT,
                "schema_version": 1,
                "source_dataset_id": source_dataset_id,
                "holdout_start": holdout_start.isoformat(),
                "rows": [row.as_json() for row in rows],
            }
        )

    @classmethod
    def from_dataset(
        cls, dataset: ObservationDataset, *, holdout_start: datetime
    ) -> R2OutcomeBlindObservationView:
        rows = tuple(row for row in dataset.rows if row.interval_end <= holdout_start)
        projection_id = cls.compute_projection_id(
            source_dataset_id=dataset.dataset_id, holdout_start=holdout_start, rows=rows
        )
        return cls(
            dataset_id=dataset.dataset_id,
            rows=rows,
            configuration=dataset.configuration,
            source_dataset_ids=dataset.source_dataset_ids,
            selection_policies=dataset.selection_policies,
            projection_id=projection_id,
        )


@dataclass(frozen=True, slots=True)
class R2OutcomeBlindPanelView:
    """Pre-holdout panel rows retaining the authenticated R1 identity."""

    dataset_id: str
    observation_dataset_id: str
    foundation_configuration_id: str
    rows: tuple[PanelRow, ...]
    projection_id: str

    CONTRACT: ClassVar[str] = "qtrad-r2-outcome-blind-panel-v1"

    @classmethod
    def compute_projection_id(
        cls,
        *,
        source_dataset_id: str,
        holdout_start: datetime,
        rows: Sequence[PanelRow],
    ) -> str:
        return _semantic_id(
            {
                "contract": cls.CONTRACT,
                "schema_version": 1,
                "source_dataset_id": source_dataset_id,
                "holdout_start": holdout_start.isoformat(),
                "rows": [row.as_json() for row in rows],
            }
        )

    @classmethod
    def from_dataset(
        cls, dataset: PanelDataset, *, holdout_start: datetime
    ) -> R2OutcomeBlindPanelView:
        rows = tuple(row for row in dataset.rows if row.decision_time < holdout_start)
        projection_id = cls.compute_projection_id(
            source_dataset_id=dataset.dataset_id, holdout_start=holdout_start, rows=rows
        )
        return cls(
            dataset_id=dataset.dataset_id,
            observation_dataset_id=dataset.observation_dataset_id,
            foundation_configuration_id=dataset.foundation_configuration_id,
            rows=rows,
            projection_id=projection_id,
        )


@dataclass(frozen=True, slots=True)
class R2G2ObservationView:
    """Outcome-free market observations available to causal G2 feature building."""

    dataset_id: str
    rows: tuple[ObservationRow, ...]
    configuration: Mapping[str, JsonValue]
    source_dataset_ids: tuple[str, ...]
    selection_policies: Mapping[str, JsonValue]
    holdout_range: tuple[datetime, datetime]
    projection_id: str

    CONTRACT: ClassVar[str] = "qtrad-r2-g2-observations-v1"
    SCHEMA_VERSION: ClassVar[int] = 1

    @classmethod
    def compute_projection_id(
        cls,
        *,
        source_dataset_id: str,
        holdout_range: tuple[datetime, datetime],
        rows: Sequence[ObservationRow],
    ) -> str:
        return _semantic_id(
            {
                "contract": cls.CONTRACT,
                "schema_version": cls.SCHEMA_VERSION,
                "source_dataset_id": source_dataset_id,
                "holdout_range": [item.isoformat() for item in holdout_range],
                "rows": [row.as_json() for row in rows],
            }
        )

    @classmethod
    def from_dataset(
        cls,
        dataset: ObservationDataset,
        *,
        holdout_range: tuple[datetime, datetime],
    ) -> R2G2ObservationView:
        rows = tuple(row for row in dataset.rows if row.interval_end <= holdout_range[1])
        return cls(
            dataset_id=dataset.dataset_id,
            rows=rows,
            configuration=dataset.configuration,
            source_dataset_ids=dataset.source_dataset_ids,
            selection_policies=dataset.selection_policies,
            holdout_range=holdout_range,
            projection_id=cls.compute_projection_id(
                source_dataset_id=dataset.dataset_id,
                holdout_range=holdout_range,
                rows=rows,
            ),
        )


@dataclass(frozen=True, slots=True)
class R2G2PanelView:
    """Outcome-free causal panel rows at frozen holdout decision times."""

    dataset_id: str
    observation_dataset_id: str
    foundation_configuration_id: str
    rows: tuple[PanelRow, ...]
    holdout_range: tuple[datetime, datetime]
    projection_id: str

    CONTRACT: ClassVar[str] = "qtrad-r2-g2-panel-v1"
    SCHEMA_VERSION: ClassVar[int] = 1

    @classmethod
    def compute_projection_id(
        cls,
        *,
        source_dataset_id: str,
        holdout_range: tuple[datetime, datetime],
        rows: Sequence[PanelRow],
    ) -> str:
        return _semantic_id(
            {
                "contract": cls.CONTRACT,
                "schema_version": cls.SCHEMA_VERSION,
                "source_dataset_id": source_dataset_id,
                "holdout_range": [item.isoformat() for item in holdout_range],
                "rows": [row.as_json() for row in rows],
            }
        )

    @classmethod
    def from_dataset(
        cls,
        dataset: PanelDataset,
        *,
        holdout_range: tuple[datetime, datetime],
    ) -> R2G2PanelView:
        rows = tuple(
            row for row in dataset.rows if holdout_range[0] <= row.decision_time < holdout_range[1]
        )
        return cls(
            dataset_id=dataset.dataset_id,
            observation_dataset_id=dataset.observation_dataset_id,
            foundation_configuration_id=dataset.foundation_configuration_id,
            rows=rows,
            holdout_range=holdout_range,
            projection_id=cls.compute_projection_id(
                source_dataset_id=dataset.dataset_id,
                holdout_range=holdout_range,
                rows=rows,
            ),
        )


@dataclass(frozen=True, slots=True)
class R2HoldoutTargetSource:
    """Authenticated outcome-blind source evidence used before holdout opening.

    The target identities contain no realised values.  The nested target dataset is
    restricted to mature pre-holdout training rows; it is the independently persisted
    child used by final fitting and never contains locked-holdout outcomes.
    """

    source_target_dataset_id: str
    observation_dataset_id: str
    foundation_configuration_id: str
    holdout_range: tuple[datetime, datetime]
    primary_horizon_seconds: int
    target_instruments: tuple[str, ...]
    targets: tuple[R2HoldoutTargetIdentity, ...]
    pre_holdout_target_dataset: TargetDataset
    opportunities: tuple[HoldoutTargetOpportunity, ...]
    source_id: str
    causal_panel_dataset_id: str | None = None
    availability_evidence_id: str | None = None
    target_index_dataset_id: str | None = None
    causal_metadata_dataset_id: str | None = None

    CONTRACT: ClassVar[str] = R2_HOLDOUT_TARGET_SOURCE_CONTRACT
    SCHEMA_VERSION: ClassVar[int] = 1
    # These are part of the source-level, outcome-blind derivation policy.  They
    # intentionally do not come from the holdout-opportunity JSON supplied by a
    # caller.  The source builder derives the values below from the authenticated
    # target child and the primary horizon before it writes the source child.
    FEATURE_LOOKBACK_SECONDS: ClassVar[int] = 60
    OPPORTUNITY_DERIVATION_POLICY: ClassVar[str] = "R1_PANEL_AVAILABILITY_AND_TARGET_MATURITY_V2"

    def __post_init__(self) -> None:
        _require_id(self.source_target_dataset_id, "holdout target source dataset ID")
        _require_id(self.observation_dataset_id, "holdout target source observation ID")
        _require_id(self.foundation_configuration_id, "holdout target source foundation ID")
        _require_id(self.source_id, "holdout target source ID")
        _positive_range(self.holdout_range, "holdout target source holdout range")
        if self.primary_horizon_seconds <= 0:
            raise ValueError("holdout target source horizon must be positive")
        if (self.causal_panel_dataset_id is None) != (self.availability_evidence_id is None):
            raise ValueError("holdout target source causal evidence references must be paired")
        if self.causal_panel_dataset_id is not None:
            _require_id(self.causal_panel_dataset_id, "holdout target source panel ID")
            _require_id(
                self.availability_evidence_id or "", "holdout target source availability ID"
            )
        if self.target_index_dataset_id is not None:
            _require_id(self.target_index_dataset_id, "holdout target source target index ID")
        if self.causal_metadata_dataset_id is not None:
            _require_id(self.causal_metadata_dataset_id, "holdout target source causal metadata ID")
        if not self.target_instruments or len(set(self.target_instruments)) != len(
            self.target_instruments
        ):
            raise ValueError("holdout target source instruments must be unique and ordered")
        if (
            self.pre_holdout_target_dataset.observation_dataset_id != self.observation_dataset_id
            or self.pre_holdout_target_dataset.foundation_configuration_id
            != self.foundation_configuration_id
        ):
            raise ValueError("pre-holdout target source lineage differs from its source")
        if any(
            row.horizon.total_seconds() != self.primary_horizon_seconds
            or row.decision_time >= self.holdout_range[0]
            or row.target_available_at > self.holdout_range[0]
            or row.instrument_id not in self.target_instruments
            for row in self.pre_holdout_target_dataset.rows
        ):
            raise ValueError("pre-holdout target source contains a non-training row")
        ordered_targets = tuple(sorted(self.targets, key=lambda item: item.target_id))
        if ordered_targets != self.targets:
            raise ValueError("holdout target source identities must be ordered")
        if len({item.target_id for item in self.targets}) != len(self.targets):
            raise ValueError("holdout target source identities must be unique")
        ordered_opportunities = tuple(
            sorted(self.opportunities, key=lambda item: item.opportunity_id)
        )
        if ordered_opportunities != self.opportunities:
            raise ValueError("holdout target source opportunities must be ordered")
        target_by_id = {item.target_id: item for item in self.targets}
        expected_holdout_ids = {
            item.target_id
            for item in self.targets
            if item.target_horizon_seconds == self.primary_horizon_seconds
            and item.instrument_id in self.target_instruments
            and self.holdout_range[0] <= item.decision_time < self.holdout_range[1]
        }
        actual_holdout_ids = {item.target_id for item in self.opportunities}
        if actual_holdout_ids != expected_holdout_ids:
            raise ValueError("holdout target source opportunities are not complete")
        for opportunity in self.opportunities:
            if opportunity.disposition in {
                HoldoutOpportunityDisposition.UNAVAILABLE_FEATURE,
                HoldoutOpportunityDisposition.FAILED_CONFIGURATION,
            }:
                raise ValueError(
                    "holdout target source cannot contain configuration-specific dispositions"
                )
            identity = target_by_id.get(opportunity.target_id)
            if identity is None or not (
                identity.instrument_id == opportunity.instrument_id
                and identity.decision_time == opportunity.decision_time
                and identity.target_horizon_seconds == opportunity.target_horizon_seconds
            ):
                raise ValueError("holdout opportunity is not derived from target source identity")
        if self.source_id != _semantic_id(self.semantic_json()):
            raise ValueError("holdout target source ID does not authenticate its content")

    @classmethod
    def create_from_target_dataset(
        cls,
        source_target_dataset: TargetDataset,
        *,
        holdout_range: tuple[datetime, datetime],
        primary_horizon_seconds: int,
        target_instruments: Sequence[str],
        opportunities: Sequence[HoldoutTargetOpportunity] | None = None,
        panel: PanelDataset | None = None,
        source_active_intervals: Mapping[str, Sequence[tuple[datetime, datetime]]] | None = None,
        data_gaps: Sequence[tuple[str, datetime, datetime]] | None = None,
        availability_evidence_id: str | None = None,
    ) -> R2HoldoutTargetSource:
        identities = tuple(
            sorted(
                (R2HoldoutTargetIdentity.from_row(row) for row in source_target_dataset.rows),
                key=lambda item: item.target_id,
            )
        )
        target_index = R2HoldoutTargetIndex.create(source_target_dataset)
        pre_holdout = _project_pre_holdout_target(
            source_target_dataset,
            holdout_start=holdout_range[0],
            primary_horizon_seconds=primary_horizon_seconds,
            target_instruments=target_instruments,
        )
        causal_metadata_dataset_id: str | None = None
        if panel is None:
            if source_active_intervals is not None or data_gaps is not None:
                raise ValueError("causal holdout evidence requires a panel")
            derived_opportunities = cls._derive_opportunities(
                source_target_dataset,
                holdout_range=holdout_range,
                primary_horizon_seconds=primary_horizon_seconds,
                target_instruments=target_instruments,
            )
            causal_panel_dataset_id = None
        else:
            if availability_evidence_id is None:
                raise ValueError("causal holdout evidence requires an availability ID")
            derived_opportunities = cls._derive_opportunities_from_r1_evidence(
                identities,
                holdout_range=holdout_range,
                primary_horizon_seconds=primary_horizon_seconds,
                target_instruments=target_instruments,
                panel=panel,
                source_active_intervals=source_active_intervals or {},
                data_gaps=data_gaps or (),
            )
            causal_panel_dataset_id = panel.dataset_id
            causal_metadata_dataset_id = R2HoldoutCausalMetadata.create(panel).dataset_id
        if panel is None:
            causal_metadata_dataset_id = None
        if opportunities is not None:
            supplied_opportunities = tuple(
                sorted(opportunities, key=lambda item: item.opportunity_id)
            )
            if any(
                item.disposition
                in {
                    HoldoutOpportunityDisposition.UNAVAILABLE_FEATURE,
                    HoldoutOpportunityDisposition.FAILED_CONFIGURATION,
                }
                for item in supplied_opportunities
            ):
                raise ValueError(
                    "holdout target source cannot contain configuration-specific dispositions"
                )
            if {item.target_id for item in supplied_opportunities} != {
                item.target_id for item in derived_opportunities
            }:
                raise ValueError("holdout opportunities are not complete")
            if supplied_opportunities != derived_opportunities:
                raise ValueError(
                    "holdout opportunities differ from authenticated source derivation"
                )
        return cls.create(
            source_target_dataset_id=source_target_dataset.dataset_id,
            observation_dataset_id=source_target_dataset.observation_dataset_id,
            foundation_configuration_id=source_target_dataset.foundation_configuration_id,
            holdout_range=holdout_range,
            primary_horizon_seconds=primary_horizon_seconds,
            target_instruments=tuple(target_instruments),
            targets=identities,
            pre_holdout_target_dataset=pre_holdout,
            opportunities=derived_opportunities,
            causal_panel_dataset_id=causal_panel_dataset_id,
            availability_evidence_id=availability_evidence_id,
            target_index_dataset_id=target_index.dataset_id,
            causal_metadata_dataset_id=causal_metadata_dataset_id,
        )

    def verify_target_index(self, target_index: R2HoldoutTargetIndex) -> None:
        """Bind source identities to the independently persisted R1 index."""
        if self.target_index_dataset_id != target_index.dataset_id:
            raise ValueError("holdout target source target-index binding differs from R1 evidence")
        if (
            target_index.source_target_dataset_id != self.source_target_dataset_id
            or target_index.observation_dataset_id != self.observation_dataset_id
            or target_index.foundation_configuration_id != self.foundation_configuration_id
        ):
            raise ValueError("holdout target source target-index lineage differs from R1 evidence")
        if target_index.targets != self.targets:
            raise ValueError("holdout target source identities differ from R1 target index")

    @classmethod
    def _derive_opportunities_from_r1_evidence(
        cls,
        target_identities: Sequence[R2HoldoutTargetIdentity],
        *,
        holdout_range: tuple[datetime, datetime],
        primary_horizon_seconds: int,
        target_instruments: Sequence[str],
        panel: PanelDataset | None,
        source_active_intervals: Mapping[str, Sequence[tuple[datetime, datetime]]],
        data_gaps: Sequence[tuple[str, datetime, datetime]],
        causal_metadata: R2HoldoutCausalMetadata | None = None,
    ) -> tuple[HoldoutTargetOpportunity, ...]:
        """Derive the registry from authenticated R1 causal evidence.

        Target rows contribute only the outcome-blind target identity and its
        availability disposition.  Feature timing comes from the authenticated
        panel; inactive/gap classification is recomputed from source activity,
        recorded gaps, and the target maturity boundary.
        """
        _positive_range(holdout_range, "holdout target source holdout range")
        if causal_metadata is None:
            if panel is None:
                raise ValueError("R1 opportunity derivation requires causal metadata")
            causal_metadata = R2HoldoutCausalMetadata.create(panel)
        metadata = causal_metadata
        panel_by_key = {
            (row.instrument_id, row.basis, row.decision_time): row for row in metadata.rows
        }
        opportunities: list[HoldoutTargetOpportunity] = []
        for identity in target_identities:
            if not (
                identity.target_horizon_seconds == primary_horizon_seconds
                and identity.instrument_id in target_instruments
                and holdout_range[0] <= identity.decision_time < holdout_range[1]
            ):
                continue
            panel_row = panel_by_key.get(
                (identity.instrument_id, identity.target_basis, identity.decision_time)
            )
            if panel_row is None:
                raise ValueError("R1 panel lacks a holdout target opportunity row")
            dependency_start = identity.decision_time - timedelta(seconds=primary_horizon_seconds)
            dependency_end = identity.decision_time + timedelta(seconds=primary_horizon_seconds)
            active = any(
                start <= dependency_start and end >= dependency_end
                for start, end in source_active_intervals.get(identity.instrument_id, ())
            )
            has_gap = any(
                instrument_id == identity.instrument_id
                and gap_start < dependency_end
                and gap_end > dependency_start
                for instrument_id, gap_start, gap_end in data_gaps
            )
            if identity.target_available_at > holdout_range[1] or not active:
                disposition = HoldoutOpportunityDisposition.INACTIVE
            elif panel_row.status is PanelStatus.MISSING_AS_OF_CUTOFF:
                disposition = (
                    HoldoutOpportunityDisposition.INACTIVE
                    if panel_row.audit_disposition is PanelAuditDisposition.SOURCE_NOT_ACTIVE
                    else HoldoutOpportunityDisposition.GAP
                )
            elif has_gap or identity.target_availability_disposition is not ReturnDisposition.VALID:
                disposition = HoldoutOpportunityDisposition.GAP
            else:
                disposition = HoldoutOpportunityDisposition.ELIGIBLE
            opportunities.append(
                HoldoutTargetOpportunity.create(
                    target_id=identity.target_id,
                    instrument_id=identity.instrument_id,
                    decision_time=identity.decision_time,
                    target_horizon_seconds=primary_horizon_seconds,
                    feature_data_asof=panel_row.feature_data_asof,
                    latest_feature_bar_end=panel_row.latest_feature_bar_end,
                    dependency_start=dependency_start,
                    dependency_end=dependency_end,
                    disposition=disposition,
                )
            )
        return tuple(sorted(opportunities, key=lambda item: item.opportunity_id))

    def verify_r1_causal_evidence(
        self,
        *,
        panel: PanelDataset | None = None,
        causal_metadata: R2HoldoutCausalMetadata | None = None,
        source_active_intervals: Mapping[str, Sequence[tuple[datetime, datetime]]],
        data_gaps: Sequence[tuple[str, datetime, datetime]],
        availability_evidence_id: str,
    ) -> None:
        """Verify source semantics against authenticated R1 panel/evidence."""
        if causal_metadata is None:
            if panel is None:
                raise ValueError("R1 causal verification requires a metadata projection")
            causal_metadata = R2HoldoutCausalMetadata.create(panel)
        if self.causal_panel_dataset_id != causal_metadata.source_panel_dataset_id:
            raise ValueError("holdout target source panel binding differs from R1 evidence")
        if (
            self.causal_metadata_dataset_id is not None
            and self.causal_metadata_dataset_id != causal_metadata.dataset_id
        ):
            raise ValueError("holdout target source metadata binding differs from R1 evidence")
        if self.availability_evidence_id != availability_evidence_id:
            raise ValueError("holdout target source availability binding differs from R1 evidence")
        expected = self._derive_opportunities_from_r1_evidence(
            self.targets,
            holdout_range=self.holdout_range,
            primary_horizon_seconds=self.primary_horizon_seconds,
            target_instruments=self.target_instruments,
            panel=panel,
            causal_metadata=causal_metadata,
            source_active_intervals=source_active_intervals,
            data_gaps=data_gaps,
        )
        if expected != self.opportunities:
            raise ValueError("holdout target source causal registry differs from R1 evidence")

    @classmethod
    def _derive_opportunities(
        cls,
        target_dataset: TargetDataset,
        *,
        holdout_range: tuple[datetime, datetime],
        primary_horizon_seconds: int,
        target_instruments: Sequence[str],
    ) -> tuple[HoldoutTargetOpportunity, ...]:
        """Derive the shared outcome-blind registry from authenticated target rows.

        Target rows are read only at the upstream source-building boundary.  The
        resulting registry carries timing and availability classifications into
        F2/G1/G2, so those fields are no longer selected by an independent JSON
        argument.  ``return_disposition`` is availability metadata here; the
        realised return itself is never copied into an opportunity.
        """
        _positive_range(holdout_range, "holdout target source holdout range")
        if primary_horizon_seconds <= 0:
            raise ValueError("holdout target source horizon must be positive")
        instruments = tuple(target_instruments)
        rows = {
            row.target_id: row
            for row in target_dataset.rows
            if row.horizon.total_seconds() == primary_horizon_seconds
            and row.instrument_id in instruments
            and holdout_range[0] <= row.decision_time < holdout_range[1]
        }
        opportunities: list[HoldoutTargetOpportunity] = []
        for row in rows.values():
            if row.target_available_at > holdout_range[1]:
                disposition = HoldoutOpportunityDisposition.INACTIVE
            elif row.return_disposition is ReturnDisposition.VALID:
                disposition = HoldoutOpportunityDisposition.ELIGIBLE
            else:
                disposition = HoldoutOpportunityDisposition.GAP
            opportunities.append(
                HoldoutTargetOpportunity.create(
                    target_id=row.target_id,
                    instrument_id=row.instrument_id,
                    decision_time=row.decision_time,
                    target_horizon_seconds=primary_horizon_seconds,
                    feature_data_asof=row.decision_time
                    - timedelta(seconds=cls.FEATURE_LOOKBACK_SECONDS),
                    latest_feature_bar_end=row.decision_time
                    - timedelta(seconds=cls.FEATURE_LOOKBACK_SECONDS),
                    dependency_start=row.decision_time - timedelta(seconds=primary_horizon_seconds),
                    dependency_end=row.decision_time + timedelta(seconds=primary_horizon_seconds),
                    disposition=disposition,
                )
            )
        return tuple(sorted(opportunities, key=lambda item: item.opportunity_id))

    @classmethod
    def create(cls, **values: object) -> R2HoldoutTargetSource:
        raw = dict(values)
        raw.pop("source_id", None)
        raw["target_instruments"] = tuple(cast(Sequence[str], raw["target_instruments"]))
        raw["targets"] = tuple(
            sorted(
                cast(Sequence[R2HoldoutTargetIdentity], raw["targets"]),
                key=lambda item: item.target_id,
            )
        )
        raw["opportunities"] = tuple(
            sorted(
                cast(Sequence[HoldoutTargetOpportunity], raw["opportunities"]),
                key=lambda item: item.opportunity_id,
            )
        )
        raw.setdefault("causal_panel_dataset_id", None)
        raw.setdefault("availability_evidence_id", None)
        raw.setdefault("target_index_dataset_id", None)
        raw.setdefault("causal_metadata_dataset_id", None)
        semantic = {
            "contract": cls.CONTRACT,
            "schema_version": cls.SCHEMA_VERSION,
            "opportunity_derivation_policy": cls.OPPORTUNITY_DERIVATION_POLICY,
            **{key: _contract_json(value) for key, value in raw.items()},
        }
        constructor = cast(Callable[..., R2HoldoutTargetSource], cls)
        return constructor(**raw, source_id=_semantic_id(semantic))

    def verify_target_dataset(self, target_dataset: TargetDataset) -> None:
        if (
            target_dataset.dataset_id != self.source_target_dataset_id
            or target_dataset.observation_dataset_id != self.observation_dataset_id
            or target_dataset.foundation_configuration_id != self.foundation_configuration_id
        ):
            raise ValueError("holdout target source differs from the retained target dataset")
        actual_targets = tuple(
            sorted(
                (R2HoldoutTargetIdentity.from_row(row) for row in target_dataset.rows),
                key=lambda item: item.target_id,
            )
        )
        if actual_targets != self.targets:
            raise ValueError("holdout target source identities differ from the retained target")
        expected_pre = _project_pre_holdout_target(
            target_dataset,
            holdout_start=self.holdout_range[0],
            primary_horizon_seconds=self.primary_horizon_seconds,
            target_instruments=self.target_instruments,
        )
        if expected_pre != self.pre_holdout_target_dataset:
            raise ValueError("holdout target source pre-holdout child is not source-derived")
        expected_source = self.create(
            source_target_dataset_id=target_dataset.dataset_id,
            observation_dataset_id=target_dataset.observation_dataset_id,
            foundation_configuration_id=target_dataset.foundation_configuration_id,
            holdout_range=self.holdout_range,
            primary_horizon_seconds=self.primary_horizon_seconds,
            target_instruments=self.target_instruments,
            targets=actual_targets,
            pre_holdout_target_dataset=expected_pre,
            opportunities=self.opportunities,
            causal_panel_dataset_id=self.causal_panel_dataset_id,
            availability_evidence_id=self.availability_evidence_id,
            target_index_dataset_id=self.target_index_dataset_id,
            causal_metadata_dataset_id=self.causal_metadata_dataset_id,
        )
        if expected_source.source_id != self.source_id:
            raise ValueError("holdout target source does not authenticate the retained target")

    def semantic_json(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": self.SCHEMA_VERSION,
            "source_target_dataset_id": self.source_target_dataset_id,
            "observation_dataset_id": self.observation_dataset_id,
            "foundation_configuration_id": self.foundation_configuration_id,
            "causal_panel_dataset_id": self.causal_panel_dataset_id,
            "availability_evidence_id": self.availability_evidence_id,
            "target_index_dataset_id": self.target_index_dataset_id,
            "causal_metadata_dataset_id": self.causal_metadata_dataset_id,
            "holdout_range": [item.isoformat() for item in self.holdout_range],
            "primary_horizon_seconds": self.primary_horizon_seconds,
            "opportunity_derivation_policy": self.OPPORTUNITY_DERIVATION_POLICY,
            "target_instruments": list(self.target_instruments),
            "targets": [item.as_json() for item in self.targets],
            "pre_holdout_target_dataset": self.pre_holdout_target_dataset.as_json(),
            "opportunities": [item.as_json() for item in self.opportunities],
        }

    def as_json(self) -> dict[str, JsonValue]:
        return {**self.semantic_json(), "source_id": self.source_id}

    @classmethod
    def from_json(cls, value: object) -> R2HoldoutTargetSource:
        if not isinstance(value, Mapping):
            raise ValueError("holdout target source must be an object")
        raw = cast(Mapping[str, object], value)
        expected = {
            "contract",
            "schema_version",
            "source_target_dataset_id",
            "observation_dataset_id",
            "foundation_configuration_id",
            "causal_panel_dataset_id",
            "availability_evidence_id",
            "target_index_dataset_id",
            "causal_metadata_dataset_id",
            "holdout_range",
            "primary_horizon_seconds",
            "opportunity_derivation_policy",
            "target_instruments",
            "targets",
            "pre_holdout_target_dataset",
            "opportunities",
            "source_id",
        }
        if set(raw) != expected or raw["contract"] != cls.CONTRACT or raw["schema_version"] != 1:
            raise ValueError("holdout target source has unknown or unsupported fields")
        if raw["opportunity_derivation_policy"] != cls.OPPORTUNITY_DERIVATION_POLICY:
            raise ValueError("holdout target source opportunity policy is unsupported")
        raw_range_value = raw["holdout_range"]
        if not isinstance(raw_range_value, list):
            raise ValueError("holdout target source holdout range is invalid")
        raw_range = cast(list[object], raw_range_value)
        if len(raw_range) != 2:
            raise ValueError("holdout target source holdout range is invalid")
        raw_instruments = raw["target_instruments"]
        raw_targets = raw["targets"]
        raw_opportunities = raw["opportunities"]
        if not all(
            isinstance(item, list) for item in (raw_instruments, raw_targets, raw_opportunities)
        ):
            raise ValueError("holdout target source arrays are invalid")
        raw_instruments = cast(list[object], raw_instruments)
        raw_targets = cast(list[object], raw_targets)
        raw_opportunities = cast(list[object], raw_opportunities)
        pre = raw["pre_holdout_target_dataset"]
        if not isinstance(pre, Mapping):
            raise ValueError("holdout target source pre-holdout child is invalid")
        return cls(
            source_target_dataset_id=str(raw["source_target_dataset_id"]),
            observation_dataset_id=str(raw["observation_dataset_id"]),
            foundation_configuration_id=str(raw["foundation_configuration_id"]),
            causal_panel_dataset_id=(
                str(raw["causal_panel_dataset_id"])
                if raw["causal_panel_dataset_id"] is not None
                else None
            ),
            availability_evidence_id=(
                str(raw["availability_evidence_id"])
                if raw["availability_evidence_id"] is not None
                else None
            ),
            target_index_dataset_id=(
                str(raw["target_index_dataset_id"])
                if raw["target_index_dataset_id"] is not None
                else None
            ),
            causal_metadata_dataset_id=(
                str(raw["causal_metadata_dataset_id"])
                if raw["causal_metadata_dataset_id"] is not None
                else None
            ),
            holdout_range=(
                datetime.fromisoformat(str(raw_range[0])),
                datetime.fromisoformat(str(raw_range[1])),
            ),
            primary_horizon_seconds=int(cast(float | int | str, raw["primary_horizon_seconds"])),
            target_instruments=tuple(str(item) for item in raw_instruments),
            targets=tuple(R2HoldoutTargetIdentity.from_json(item) for item in raw_targets),
            pre_holdout_target_dataset=_target_dataset_from_json(cast(Mapping[str, object], pre)),
            opportunities=tuple(
                HoldoutTargetOpportunity.from_json(item) for item in raw_opportunities
            ),
            source_id=str(raw["source_id"]),
        )


@dataclass(frozen=True, slots=True)
class R2OutcomeBlindTargetView:
    """The pre-holdout rows exposed to F2 under the full target identity.

    ``dataset_id`` remains the authenticated R1 target-child identity so feature
    and fold bindings cannot be rewritten around a filtered child.  ``rows`` are
    supplied only by the source's independently persisted pre-holdout projection;
    no post-boundary target value is available through this view.
    """

    dataset_id: str
    observation_dataset_id: str
    foundation_configuration_id: str
    rows: tuple[TargetRow, ...]

    @classmethod
    def from_source(cls, source: R2HoldoutTargetSource) -> R2OutcomeBlindTargetView:
        return cls(
            dataset_id=source.source_target_dataset_id,
            observation_dataset_id=source.observation_dataset_id,
            foundation_configuration_id=source.foundation_configuration_id,
            rows=source.pre_holdout_target_dataset.rows,
        )


@dataclass(frozen=True, slots=True)
class R2PreHoldoutTargetProjection:
    """R1-authoritative projection of mature primary-horizon target rows."""

    source_target_dataset_id: str
    observation_dataset_id: str
    foundation_configuration_id: str
    holdout_start: datetime
    primary_horizon_seconds: int
    target_instruments: tuple[str, ...]
    projection_policy: str
    projected_target_dataset: TargetDataset
    projected_target_dataset_id: str
    projection_id: str

    CONTRACT: ClassVar[str] = R2_PRE_HOLDOUT_TARGET_PROJECTION_CONTRACT
    SCHEMA_VERSION: ClassVar[int] = 1
    POLICY: ClassVar[str] = "PRIMARY_HORIZON_MATURE_BEFORE_HOLDOUT_V1"

    def __post_init__(self) -> None:
        _require_id(self.source_target_dataset_id, "pre-holdout projection source target ID")
        _require_id(self.observation_dataset_id, "pre-holdout projection observation ID")
        _require_id(self.foundation_configuration_id, "pre-holdout projection configuration ID")
        _require_id(self.projected_target_dataset_id, "pre-holdout projected target ID")
        _require_id(self.projection_id, "pre-holdout projection ID")
        require_utc(self.holdout_start, "pre-holdout projection holdout start")
        if self.primary_horizon_seconds <= 0:
            raise ValueError("pre-holdout projection horizon must be positive")
        if self.projection_policy != self.POLICY:
            raise ValueError("pre-holdout projection policy is unsupported")
        if tuple(sorted(set(self.target_instruments))) != self.target_instruments:
            raise ValueError("pre-holdout projection instruments are not canonical")
        if (
            self.projected_target_dataset.dataset_id != self.projected_target_dataset_id
            or self.projected_target_dataset.observation_dataset_id != self.observation_dataset_id
            or self.projected_target_dataset.foundation_configuration_id
            != self.foundation_configuration_id
        ):
            raise ValueError("pre-holdout projection child lineage is invalid")
        if self.projection_id != _semantic_id(self.semantic_json()):
            raise ValueError("pre-holdout projection ID does not authenticate its content")

    @classmethod
    def create_from_target_dataset(
        cls,
        source: TargetDataset,
        *,
        holdout_start: datetime,
        primary_horizon_seconds: int,
        target_instruments: Sequence[str],
    ) -> R2PreHoldoutTargetProjection:
        instruments = tuple(sorted(set(target_instruments)))
        projected = _project_pre_holdout_target(
            source,
            holdout_start=holdout_start,
            primary_horizon_seconds=primary_horizon_seconds,
            target_instruments=instruments,
        )
        unbound = {
            "contract": cls.CONTRACT,
            "schema_version": cls.SCHEMA_VERSION,
            "source_target_dataset_id": source.dataset_id,
            "observation_dataset_id": source.observation_dataset_id,
            "foundation_configuration_id": source.foundation_configuration_id,
            "holdout_start": holdout_start.isoformat(),
            "primary_horizon_seconds": primary_horizon_seconds,
            "target_instruments": list(instruments),
            "projection_policy": cls.POLICY,
            "projected_target_dataset": projected.as_json(),
            "projected_target_dataset_id": projected.dataset_id,
        }
        return cls(
            source_target_dataset_id=source.dataset_id,
            observation_dataset_id=source.observation_dataset_id,
            foundation_configuration_id=source.foundation_configuration_id,
            holdout_start=holdout_start,
            primary_horizon_seconds=primary_horizon_seconds,
            target_instruments=instruments,
            projection_policy=cls.POLICY,
            projected_target_dataset=projected,
            projected_target_dataset_id=projected.dataset_id,
            projection_id=_semantic_id(unbound),
        )

    def verify_source(self, source: TargetDataset) -> None:
        if (
            source.dataset_id != self.source_target_dataset_id
            or source.observation_dataset_id != self.observation_dataset_id
            or source.foundation_configuration_id != self.foundation_configuration_id
        ):
            raise ValueError("pre-holdout projection source differs from its authority")
        expected = self.create_from_target_dataset(
            source,
            holdout_start=self.holdout_start,
            primary_horizon_seconds=self.primary_horizon_seconds,
            target_instruments=self.target_instruments,
        )
        if expected.projected_target_dataset != self.projected_target_dataset:
            raise ValueError("pre-holdout projection labels differ from the source dataset")
        if expected.projection_id != self.projection_id:
            raise ValueError("pre-holdout projection identity differs from the source dataset")

    def semantic_json(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": self.SCHEMA_VERSION,
            "source_target_dataset_id": self.source_target_dataset_id,
            "observation_dataset_id": self.observation_dataset_id,
            "foundation_configuration_id": self.foundation_configuration_id,
            "holdout_start": self.holdout_start.isoformat(),
            "primary_horizon_seconds": self.primary_horizon_seconds,
            "target_instruments": list(self.target_instruments),
            "projection_policy": self.projection_policy,
            "projected_target_dataset": self.projected_target_dataset.as_json(),
            "projected_target_dataset_id": self.projected_target_dataset_id,
        }

    def as_json(self) -> dict[str, JsonValue]:
        return {**self.semantic_json(), "projection_id": self.projection_id}

    @classmethod
    def from_json(cls, value: object) -> R2PreHoldoutTargetProjection:
        if not isinstance(value, Mapping):
            raise ValueError("pre-holdout projection must be an object")
        raw = cast(Mapping[str, object], value)
        expected = {
            "contract",
            "schema_version",
            "source_target_dataset_id",
            "observation_dataset_id",
            "foundation_configuration_id",
            "holdout_start",
            "primary_horizon_seconds",
            "target_instruments",
            "projection_policy",
            "projected_target_dataset",
            "projected_target_dataset_id",
            "projection_id",
        }
        if set(raw) != expected or raw["contract"] != cls.CONTRACT or raw["schema_version"] != 1:
            raise ValueError("pre-holdout projection has unknown or unsupported fields")
        raw_instruments_value = raw["target_instruments"]
        projected_raw = raw["projected_target_dataset"]
        raw_horizon = raw["primary_horizon_seconds"]
        if (
            not isinstance(raw_instruments_value, list)
            or not isinstance(projected_raw, Mapping)
            or not isinstance(raw_horizon, (int, float, str))
        ):
            raise ValueError("pre-holdout projection child is malformed")
        raw_instruments = cast(list[object], raw_instruments_value)
        return cls(
            source_target_dataset_id=str(raw["source_target_dataset_id"]),
            observation_dataset_id=str(raw["observation_dataset_id"]),
            foundation_configuration_id=str(raw["foundation_configuration_id"]),
            holdout_start=datetime.fromisoformat(str(raw["holdout_start"])),
            primary_horizon_seconds=int(raw_horizon),
            target_instruments=tuple(str(item) for item in raw_instruments),
            projection_policy=str(raw["projection_policy"]),
            projected_target_dataset=_target_dataset_from_json(
                cast(Mapping[str, object], projected_raw)
            ),
            projected_target_dataset_id=str(raw["projected_target_dataset_id"]),
            projection_id=str(raw["projection_id"]),
        )


@dataclass(frozen=True, slots=True)
class R2HoldoutTargetProjection:
    """Authenticated source-to-pre-holdout target projection evidence."""

    source_target_dataset_id: str
    source_evidence_id: str
    observation_dataset_id: str
    foundation_configuration_id: str
    holdout_start: datetime
    primary_horizon_seconds: int
    projection_policy: str
    projected_target_dataset: TargetDataset
    projected_target_dataset_id: str
    projection_id: str

    CONTRACT: ClassVar[str] = R2_HOLDOUT_TARGET_PROJECTION_CONTRACT
    SCHEMA_VERSION: ClassVar[int] = 1
    POLICY: ClassVar[str] = "PRIMARY_HORIZON_MATURE_BEFORE_HOLDOUT_V1"

    def __post_init__(self) -> None:
        _require_id(self.source_target_dataset_id, "projection source target dataset ID")
        _require_id(self.source_evidence_id, "projection source evidence ID")
        _require_id(self.projected_target_dataset.dataset_id, "projected target dataset ID")
        _require_id(self.projected_target_dataset_id, "projected target dataset ID")
        if self.projected_target_dataset_id != self.projected_target_dataset.dataset_id:
            raise ValueError("projection projected-target identity differs from its child")
        _require_id(self.projection_id, "target projection ID")
        require_utc(self.holdout_start, "target projection holdout start")
        if self.primary_horizon_seconds <= 0:
            raise ValueError("target projection horizon must be positive")
        if self.projection_policy != self.POLICY:
            raise ValueError("target projection policy is unsupported")
        if (
            self.projected_target_dataset.observation_dataset_id != self.observation_dataset_id
            or self.projected_target_dataset.foundation_configuration_id
            != self.foundation_configuration_id
        ):
            raise ValueError("projected target sources differ from the projection artifact")
        if self.projection_id != _semantic_id(self.semantic_json()):
            raise ValueError("target projection ID does not authenticate its content")

    @classmethod
    def create_from_source(
        cls,
        source: R2HoldoutTargetSource,
    ) -> R2HoldoutTargetProjection:
        projected = source.pre_holdout_target_dataset
        semantic = {
            "contract": cls.CONTRACT,
            "schema_version": cls.SCHEMA_VERSION,
            "source_target_dataset_id": source.source_target_dataset_id,
            "source_evidence_id": source.source_id,
            "observation_dataset_id": source.observation_dataset_id,
            "foundation_configuration_id": source.foundation_configuration_id,
            "holdout_start": source.holdout_range[0].isoformat(),
            "primary_horizon_seconds": source.primary_horizon_seconds,
            "projection_policy": cls.POLICY,
            "projected_target_dataset": projected.as_json(),
            "projected_target_dataset_id": projected.dataset_id,
        }
        return cls(
            source_target_dataset_id=source.source_target_dataset_id,
            source_evidence_id=source.source_id,
            observation_dataset_id=source.observation_dataset_id,
            foundation_configuration_id=source.foundation_configuration_id,
            holdout_start=source.holdout_range[0],
            primary_horizon_seconds=source.primary_horizon_seconds,
            projection_policy=cls.POLICY,
            projected_target_dataset=projected,
            projected_target_dataset_id=projected.dataset_id,
            projection_id=_semantic_id(semantic),
        )

    def verify_source(self, source: R2HoldoutTargetSource) -> None:
        if (
            source.source_target_dataset_id != self.source_target_dataset_id
            or source.source_id != self.source_evidence_id
            or source.observation_dataset_id != self.observation_dataset_id
            or source.foundation_configuration_id != self.foundation_configuration_id
            or source.holdout_range[0] != self.holdout_start
            or source.primary_horizon_seconds != self.primary_horizon_seconds
        ):
            raise ValueError("target projection source differs from the frozen source evidence")
        expected = self.create_from_source(source)
        if expected.projected_target_dataset != self.projected_target_dataset:
            raise ValueError("target projection child is not derived from its source dataset")
        if expected.projection_id != self.projection_id:
            raise ValueError("target projection evidence does not authenticate its source")

    def semantic_json(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": self.SCHEMA_VERSION,
            "source_target_dataset_id": self.source_target_dataset_id,
            "source_evidence_id": self.source_evidence_id,
            "observation_dataset_id": self.observation_dataset_id,
            "foundation_configuration_id": self.foundation_configuration_id,
            "holdout_start": self.holdout_start.isoformat(),
            "primary_horizon_seconds": self.primary_horizon_seconds,
            "projection_policy": self.projection_policy,
            "projected_target_dataset": self.projected_target_dataset.as_json(),
            "projected_target_dataset_id": self.projected_target_dataset_id,
        }

    def as_json(self) -> dict[str, JsonValue]:
        return {**self.semantic_json(), "projection_id": self.projection_id}

    @classmethod
    def from_json(cls, value: object) -> R2HoldoutTargetProjection:
        if not isinstance(value, Mapping):
            raise ValueError("target projection must be an object")
        raw = cast(Mapping[str, object], value)
        expected = {
            "contract",
            "schema_version",
            "source_target_dataset_id",
            "source_evidence_id",
            "observation_dataset_id",
            "foundation_configuration_id",
            "holdout_start",
            "primary_horizon_seconds",
            "projection_policy",
            "projected_target_dataset",
            "projected_target_dataset_id",
            "projection_id",
        }
        if set(raw) != expected or raw["contract"] != cls.CONTRACT or raw["schema_version"] != 1:
            raise ValueError("target projection has unknown or unsupported fields")
        projected_raw = raw["projected_target_dataset"]
        if not isinstance(projected_raw, Mapping):
            raise ValueError("target projection child must be an object")
        projected = _target_dataset_from_json(cast(Mapping[str, object], projected_raw))
        return cls(
            source_target_dataset_id=str(raw["source_target_dataset_id"]),
            source_evidence_id=str(raw["source_evidence_id"]),
            observation_dataset_id=str(raw["observation_dataset_id"]),
            foundation_configuration_id=str(raw["foundation_configuration_id"]),
            holdout_start=datetime.fromisoformat(str(raw["holdout_start"])),
            primary_horizon_seconds=int(cast(float | int | str, raw["primary_horizon_seconds"])),
            projection_policy=str(raw["projection_policy"]),
            projected_target_dataset=projected,
            projected_target_dataset_id=str(raw["projected_target_dataset_id"]),
            projection_id=str(raw["projection_id"]),
        )


@dataclass(frozen=True, slots=True)
class R2HoldoutOpportunityRegistry:
    """Source-bound, outcome-blind holdout opportunity registry."""

    source_target_dataset_id: str
    source_evidence_id: str
    observation_dataset_id: str
    foundation_configuration_id: str
    holdout_range: tuple[datetime, datetime]
    primary_horizon_seconds: int
    opportunities: tuple[HoldoutTargetOpportunity, ...]
    registry_id: str

    CONTRACT: ClassVar[str] = R2_HOLDOUT_OPPORTUNITY_REGISTRY_CONTRACT
    SCHEMA_VERSION: ClassVar[int] = 1

    def __post_init__(self) -> None:
        _require_id(self.source_target_dataset_id, "opportunity registry source target ID")
        _require_id(self.source_evidence_id, "opportunity registry source evidence ID")
        _require_id(self.observation_dataset_id, "opportunity registry observation ID")
        _require_id(self.foundation_configuration_id, "opportunity registry foundation ID")
        _require_id(self.registry_id, "opportunity registry ID")
        _positive_range(self.holdout_range, "opportunity registry holdout range")
        if self.primary_horizon_seconds <= 0:
            raise ValueError("opportunity registry horizon must be positive")
        if not self.opportunities:
            raise ValueError("opportunity registry must not be empty")
        ordered = tuple(sorted(self.opportunities, key=lambda item: item.opportunity_id))
        if ordered != self.opportunities:
            raise ValueError("opportunity registry must be deterministically ordered")
        if len({item.opportunity_id for item in ordered}) != len(ordered):
            raise ValueError("opportunity registry opportunity IDs must be unique")
        if len({item.target_id for item in ordered}) != len(ordered):
            raise ValueError("opportunity registry target IDs must be unique")
        for item in ordered:
            if item.disposition in {
                HoldoutOpportunityDisposition.UNAVAILABLE_FEATURE,
                HoldoutOpportunityDisposition.FAILED_CONFIGURATION,
            }:
                raise ValueError(
                    "opportunity registry cannot contain configuration-specific dispositions"
                )
            if item.target_horizon_seconds != self.primary_horizon_seconds:
                raise ValueError("opportunity registry differs from the primary horizon")
            if not self.holdout_range[0] <= item.decision_time < self.holdout_range[1]:
                raise ValueError("opportunity registry contains a row outside the holdout range")
        if self.registry_id != _semantic_id(self.semantic_json()):
            raise ValueError("opportunity registry ID does not authenticate its content")

    @classmethod
    def create_from_source(
        cls,
        source: R2HoldoutTargetSource,
    ) -> R2HoldoutOpportunityRegistry:
        return cls.create(
            source_target_dataset_id=source.source_target_dataset_id,
            source_evidence_id=source.source_id,
            observation_dataset_id=source.observation_dataset_id,
            foundation_configuration_id=source.foundation_configuration_id,
            holdout_range=source.holdout_range,
            primary_horizon_seconds=source.primary_horizon_seconds,
            opportunities=source.opportunities,
        )

    @classmethod
    def create(cls, **values: object) -> R2HoldoutOpportunityRegistry:
        raw = dict(values)
        raw.pop("registry_id", None)
        raw["opportunities"] = tuple(
            sorted(
                cast(Sequence[HoldoutTargetOpportunity], raw["opportunities"]),
                key=lambda item: item.opportunity_id,
            )
        )
        semantic = {
            "contract": cls.CONTRACT,
            "schema_version": cls.SCHEMA_VERSION,
            **{key: _contract_json(value) for key, value in raw.items()},
        }
        constructor = cast(Callable[..., R2HoldoutOpportunityRegistry], cls)
        return constructor(**raw, registry_id=_semantic_id(semantic))

    def verify_source(self, source: R2HoldoutTargetSource) -> None:
        if (
            source.source_target_dataset_id != self.source_target_dataset_id
            or source.source_id != self.source_evidence_id
            or source.observation_dataset_id != self.observation_dataset_id
            or source.foundation_configuration_id != self.foundation_configuration_id
            or source.holdout_range != self.holdout_range
            or source.primary_horizon_seconds != self.primary_horizon_seconds
        ):
            raise ValueError("opportunity registry source differs from frozen source evidence")
        expected = self.create_from_source(source)
        if expected.registry_id != self.registry_id:
            raise ValueError("opportunity registry evidence does not authenticate its source")

    def semantic_json(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": self.SCHEMA_VERSION,
            "source_target_dataset_id": self.source_target_dataset_id,
            "source_evidence_id": self.source_evidence_id,
            "observation_dataset_id": self.observation_dataset_id,
            "foundation_configuration_id": self.foundation_configuration_id,
            "holdout_range": [item.isoformat() for item in self.holdout_range],
            "primary_horizon_seconds": self.primary_horizon_seconds,
            "opportunities": [item.as_json() for item in self.opportunities],
        }

    def as_json(self) -> dict[str, JsonValue]:
        return {**self.semantic_json(), "registry_id": self.registry_id}

    @classmethod
    def from_json(cls, value: object) -> R2HoldoutOpportunityRegistry:
        if not isinstance(value, Mapping):
            raise ValueError("opportunity registry must be an object")
        raw = cast(Mapping[str, object], value)
        expected = {
            "contract",
            "schema_version",
            "source_target_dataset_id",
            "source_evidence_id",
            "observation_dataset_id",
            "foundation_configuration_id",
            "holdout_range",
            "primary_horizon_seconds",
            "opportunities",
            "registry_id",
        }
        if set(raw) != expected or raw["contract"] != cls.CONTRACT or raw["schema_version"] != 1:
            raise ValueError("opportunity registry has unknown or unsupported fields")
        raw_range = raw["holdout_range"]
        if not isinstance(raw_range, list):
            raise ValueError("opportunity registry holdout range is invalid")
        raw_range = cast(list[object], raw_range)
        if len(raw_range) != 2:
            raise ValueError("opportunity registry holdout range is invalid")
        raw_opportunities = raw["opportunities"]
        if not isinstance(raw_opportunities, list):
            raise ValueError("opportunity registry opportunities must be an array")
        return cls(
            source_target_dataset_id=str(raw["source_target_dataset_id"]),
            source_evidence_id=str(raw["source_evidence_id"]),
            observation_dataset_id=str(raw["observation_dataset_id"]),
            foundation_configuration_id=str(raw["foundation_configuration_id"]),
            holdout_range=(
                datetime.fromisoformat(str(raw_range[0])),
                datetime.fromisoformat(str(raw_range[1])),
            ),
            primary_horizon_seconds=int(cast(float | int | str, raw["primary_horizon_seconds"])),
            opportunities=tuple(
                HoldoutTargetOpportunity.from_json(item)
                for item in cast(list[object], raw_opportunities)
            ),
            registry_id=str(raw["registry_id"]),
        )


@dataclass(frozen=True, slots=True)
class R2HoldoutFeatureRow:
    """Causal features with no realised target, price, or excursion field."""

    opportunity_id: str
    target_id: str
    instrument_id: str
    decision_time: datetime
    feature_cutoff: datetime
    latest_feature_bar_end: datetime
    feature_schema_id: str
    values: tuple[float, ...]
    row_id: str

    CONTRACT: ClassVar[str] = "qtrad-r2-holdout-feature-row-v1"

    def __post_init__(self) -> None:
        _require_id(self.opportunity_id, "feature opportunity ID")
        _require_id(self.target_id, "feature target ID")
        _require_id(self.feature_schema_id, "feature schema ID")
        _require_id(self.row_id, "feature row ID")
        _require_text(self.instrument_id, "feature instrument")
        require_utc(self.decision_time, "feature decision time")
        require_utc(self.feature_cutoff, "feature cutoff")
        require_utc(self.latest_feature_bar_end, "latest feature bar end")
        if self.feature_cutoff > self.decision_time:
            raise ValueError("feature cutoff cannot be after decision time")
        if self.latest_feature_bar_end > self.feature_cutoff:
            raise ValueError("latest feature bar end exceeds feature cutoff")
        if not self.values or any(not isfinite(value) for value in self.values):
            raise ValueError("feature row values must be finite and non-empty")
        if self.row_id != _semantic_id(self.semantic_json()):
            raise ValueError("feature row ID does not authenticate its content")

    @classmethod
    def create(
        cls,
        *,
        opportunity_id: str,
        target_id: str,
        instrument_id: str,
        decision_time: datetime,
        feature_cutoff: datetime,
        latest_feature_bar_end: datetime,
        feature_schema_id: str,
        values: Sequence[float],
    ) -> R2HoldoutFeatureRow:
        semantic = {
            "contract": cls.CONTRACT,
            "schema_version": 1,
            "opportunity_id": opportunity_id,
            "target_id": target_id,
            "instrument_id": instrument_id,
            "decision_time": decision_time.isoformat(),
            "feature_cutoff": feature_cutoff.isoformat(),
            "latest_feature_bar_end": latest_feature_bar_end.isoformat(),
            "feature_schema_id": feature_schema_id,
            "values": list(values),
        }
        return cls(
            opportunity_id=opportunity_id,
            target_id=target_id,
            instrument_id=instrument_id,
            decision_time=decision_time,
            feature_cutoff=feature_cutoff,
            latest_feature_bar_end=latest_feature_bar_end,
            feature_schema_id=feature_schema_id,
            values=tuple(values),
            row_id=_semantic_id(semantic),
        )

    def semantic_json(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": 1,
            "opportunity_id": self.opportunity_id,
            "target_id": self.target_id,
            "instrument_id": self.instrument_id,
            "decision_time": self.decision_time.isoformat(),
            "feature_cutoff": self.feature_cutoff.isoformat(),
            "latest_feature_bar_end": self.latest_feature_bar_end.isoformat(),
            "feature_schema_id": self.feature_schema_id,
            "values": list(self.values),
        }

    def as_json(self) -> dict[str, JsonValue]:
        return {**self.semantic_json(), "row_id": self.row_id}


@dataclass(frozen=True, slots=True)
class R2HoldoutFeatureDataset:
    selection_manifest_id: str
    experiment_configuration_id: str
    foundation_bundle_id: str
    observation_dataset_id: str
    panel_dataset_id: str
    feature_schema_id: str
    feature_set_id: str
    source_class: MarketDataSourceSourceClass
    evidence_class: EvidenceClass
    holdout_scope: HoldoutScope
    holdout_range: tuple[datetime, datetime]
    expected_opportunity_ids: tuple[str, ...]
    unavailable_opportunity_ids: tuple[str, ...]
    rows: tuple[R2HoldoutFeatureRow, ...]
    outcome_blind_projection: str
    holdout_outcomes_accessed: bool
    dataset_id: str
    target_dataset_id: str | None = None
    opportunity_target_ids: tuple[tuple[str, str], ...] = ()

    CONTRACT: ClassVar[str] = R2_HOLDOUT_FEATURES_CONTRACT
    SCHEMA_VERSION: ClassVar[int] = 1

    def __post_init__(self) -> None:
        for value, field in (
            (self.selection_manifest_id, "feature selection manifest ID"),
            (self.experiment_configuration_id, "feature experiment ID"),
            (self.foundation_bundle_id, "feature foundation ID"),
            (self.observation_dataset_id, "feature observation dataset ID"),
            (self.panel_dataset_id, "feature panel dataset ID"),
            (self.feature_schema_id, "feature schema ID"),
            (self.feature_set_id, "feature set ID"),
            (self.dataset_id, "holdout feature dataset ID"),
        ):
            _require_id(value, field)
        if self.target_dataset_id is None and self.holdout_scope is HoldoutScope.CONFIRMATORY:
            raise ValueError(
                "confirmatory holdout features require authenticated target-dataset lineage"
            )
        if self.target_dataset_id is not None:
            _require_id(self.target_dataset_id, "holdout target dataset ID")
        if self.holdout_scope is HoldoutScope.DISPOSABLE_FIXTURE and (
            self.evidence_class is not EvidenceClass.IMPLEMENTATION
        ):
            raise ValueError("disposable holdout features require implementation evidence")
        _positive_range(self.holdout_range, "holdout feature range")
        if self.outcome_blind_projection != "TARGET_OUTCOME_BLIND_V1":
            raise ValueError("holdout features require the dedicated outcome-blind projection")
        if self.holdout_outcomes_accessed:
            raise ValueError("holdout feature preparation cannot access outcomes")
        expected = _ordered_ids(self.expected_opportunity_ids, "expected opportunity IDs")
        unavailable = _ordered_ids(self.unavailable_opportunity_ids, "unavailable opportunity IDs")
        if not set(unavailable) <= set(expected):
            raise ValueError("unavailable opportunities must be expected opportunities")
        target_pairs = tuple(sorted(self.opportunity_target_ids))
        if target_pairs != self.opportunity_target_ids:
            raise ValueError("opportunity target bindings must be ordered")
        if len({opportunity_id for opportunity_id, _target_id in target_pairs}) != len(
            target_pairs
        ):
            raise ValueError("opportunity target bindings must be unique")
        if {opportunity_id for opportunity_id, _target_id in target_pairs} != set(expected):
            raise ValueError(
                "opportunity target bindings must exactly cover expected opportunities"
            )
        if len({target_id for _opportunity_id, target_id in target_pairs}) != len(target_pairs):
            raise ValueError("opportunity target bindings must have unique targets")
        rows = tuple(sorted(self.rows, key=lambda item: (item.decision_time, item.row_id)))
        if rows != self.rows:
            raise ValueError("holdout feature rows must be deterministically ordered")
        if len({row.row_id for row in rows}) != len(rows):
            raise ValueError("holdout feature rows must be unique")
        if not set(row.opportunity_id for row in rows) <= set(expected) - set(unavailable):
            raise ValueError("holdout feature row is not an eligible expected opportunity")
        if any(
            not self.holdout_range[0] <= row.decision_time < self.holdout_range[1] for row in rows
        ):
            raise ValueError("holdout feature row lies outside the locked holdout range")
        if self.dataset_id != _semantic_id(self.semantic_json()):
            raise ValueError("holdout feature dataset ID does not authenticate its content")

    @classmethod
    def create(cls, **values: object) -> R2HoldoutFeatureDataset:
        raw = dict(values)
        raw.pop("dataset_id", None)
        raw.setdefault("outcome_blind_projection", "TARGET_OUTCOME_BLIND_V1")
        raw.setdefault("holdout_outcomes_accessed", False)
        raw.setdefault("target_dataset_id", None)
        raw["opportunity_target_ids"] = tuple(
            sorted(cast(Sequence[tuple[str, str]], raw.get("opportunity_target_ids", ())))
        )
        raw["expected_opportunity_ids"] = tuple(
            sorted(cast(Sequence[str], raw["expected_opportunity_ids"]))
        )
        raw["unavailable_opportunity_ids"] = tuple(
            sorted(cast(Sequence[str], raw["unavailable_opportunity_ids"]))
        )
        raw["rows"] = tuple(
            sorted(
                cast(Sequence[R2HoldoutFeatureRow], raw["rows"]),
                key=lambda item: (item.decision_time, item.row_id),
            )
        )
        semantic: dict[str, JsonValue] = {
            "contract": cls.CONTRACT,
            "schema_version": 1,
            **{key: _contract_json(value) for key, value in raw.items()},
        }
        constructor = cast(Callable[..., R2HoldoutFeatureDataset], cls)
        return constructor(**raw, dataset_id=_semantic_id(semantic))

    def semantic_json(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": self.SCHEMA_VERSION,
            "selection_manifest_id": self.selection_manifest_id,
            "experiment_configuration_id": self.experiment_configuration_id,
            "foundation_bundle_id": self.foundation_bundle_id,
            "observation_dataset_id": self.observation_dataset_id,
            "panel_dataset_id": self.panel_dataset_id,
            "feature_schema_id": self.feature_schema_id,
            "feature_set_id": self.feature_set_id,
            "source_class": self.source_class.value,
            "evidence_class": self.evidence_class.value,
            "holdout_scope": self.holdout_scope.value,
            "holdout_range": [item.isoformat() for item in self.holdout_range],
            "expected_opportunity_ids": list(self.expected_opportunity_ids),
            "unavailable_opportunity_ids": list(self.unavailable_opportunity_ids),
            "opportunity_target_ids": [list(item) for item in self.opportunity_target_ids],
            "target_dataset_id": self.target_dataset_id,
            "rows": [item.as_json() for item in self.rows],
            "outcome_blind_projection": self.outcome_blind_projection,
            "holdout_outcomes_accessed": self.holdout_outcomes_accessed,
        }

    def as_json(self) -> dict[str, JsonValue]:
        return {**self.semantic_json(), "dataset_id": self.dataset_id}


# Alias kept intentionally close to the plan's source-class wording.
MarketDataSourceSourceClass = MarketDataSourceClass


@dataclass(frozen=True, slots=True)
class R2AlphaCandidateScore:
    alpha: float
    validation_loss: float | None
    disposition: FinalFitDisposition
    failure_reason: str | None

    def __post_init__(self) -> None:
        if self.alpha <= 0 or not isfinite(self.alpha):
            raise ValueError("alpha candidate must be finite and positive")
        if self.validation_loss is not None:
            _finite(self.validation_loss, "alpha validation loss")
        if self.disposition is FinalFitDisposition.READY and self.validation_loss is None:
            raise ValueError("ready alpha candidate requires a validation loss")
        if self.disposition is not FinalFitDisposition.READY and not (
            self.failure_reason and self.failure_reason.strip()
        ):
            raise ValueError("failed alpha candidate requires a failure reason")

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "alpha": self.alpha,
            "validation_loss": self.validation_loss,
            "disposition": self.disposition.value,
            "failure_reason": self.failure_reason,
        }


@dataclass(frozen=True, slots=True)
class R2FinalFit:
    selection_manifest_id: str
    configuration_id: str
    model_family: ModelFamily
    target_instrument_id: str | None
    feature_dataset_id: str
    feature_schema_id: str
    training_cutoff: datetime
    training_target_ids: tuple[str, ...]
    purged_target_ids: tuple[str, ...]
    inner_fit_target_ids: tuple[str, ...]
    inner_validation_target_ids: tuple[str, ...]
    preprocessing: Mapping[str, JsonValue]
    alpha_candidate_scores: tuple[R2AlphaCandidateScore, ...]
    selected_alpha: float | None
    sample_weights: tuple[tuple[str, float], ...]
    coefficients: tuple[float, ...] | None
    intercept: float | None
    disposition: FinalFitDisposition
    failure_reason: str | None
    diagnostics: Mapping[str, JsonValue]
    runtime_identities: Mapping[str, JsonValue]
    evidence_class: EvidenceClass
    holdout_scope: HoldoutScope
    fit_id: str

    CONTRACT: ClassVar[str] = R2_FINAL_FIT_CONTRACT
    SCHEMA_VERSION: ClassVar[int] = 1

    def __post_init__(self) -> None:
        for value, field in (
            (self.selection_manifest_id, "final-fit selection ID"),
            (self.configuration_id, "final-fit configuration ID"),
            (self.feature_dataset_id, "final-fit feature dataset ID"),
            (self.feature_schema_id, "final-fit feature schema ID"),
            (self.fit_id, "final-fit ID"),
        ):
            _require_id(value, field)
        if self.target_instrument_id is not None and not self.target_instrument_id.strip():
            raise ValueError("final-fit target instrument cannot be blank")
        require_utc(self.training_cutoff, "final-fit training cutoff")
        for values, field in (
            (self.training_target_ids, "final-fit training target IDs"),
            (self.purged_target_ids, "final-fit purged target IDs"),
            (self.inner_fit_target_ids, "final-fit inner-fit target IDs"),
            (self.inner_validation_target_ids, "final-fit inner-validation target IDs"),
        ):
            _ordered_ids(values, field)
        training = set(self.training_target_ids)
        if (
            not set(self.inner_fit_target_ids) <= training
            or not set(self.inner_validation_target_ids) <= training
        ):
            raise ValueError("inner memberships must be subsets of pre-holdout training")
        if set(self.inner_fit_target_ids) & set(self.inner_validation_target_ids):
            raise ValueError("inner fit and validation memberships must be disjoint")
        if set(self.purged_target_ids) & training:
            raise ValueError("purged targets must be excluded from training")
        if not self.alpha_candidate_scores:
            raise ValueError("final fit must retain every alpha candidate outcome")
        if self.selected_alpha is not None and self.selected_alpha not in {
            item.alpha for item in self.alpha_candidate_scores
        }:
            raise ValueError("selected alpha is not one of the frozen candidate outcomes")
        if self.disposition is FinalFitDisposition.READY:
            if self.selected_alpha is None or self.coefficients is None or self.intercept is None:
                raise ValueError("ready final fit requires selected alpha and model parameters")
            if not self.coefficients or any(not isfinite(item) for item in self.coefficients):
                raise ValueError("ready final-fit coefficients must be finite")
            _finite(self.intercept, "final-fit intercept")
        elif not (self.failure_reason and self.failure_reason.strip()):
            raise ValueError("failed final fit requires a failure reason")
        if any(weight <= 0 or not isfinite(weight) for _, weight in self.sample_weights):
            raise ValueError("final-fit sample weights must be finite and positive")
        if len({target_id for target_id, _ in self.sample_weights}) != len(self.sample_weights):
            raise ValueError("final-fit sample weights must be unique by target")
        if self.holdout_scope is HoldoutScope.DISPOSABLE_FIXTURE and (
            self.evidence_class is not EvidenceClass.IMPLEMENTATION
        ):
            raise ValueError("disposable final fits require implementation evidence")
        if self.fit_id != _semantic_id(self.semantic_json()):
            raise ValueError("final-fit ID does not authenticate its content")

    @classmethod
    def create(cls, **values: object) -> R2FinalFit:
        raw = dict(values)
        raw.pop("fit_id", None)
        raw["training_target_ids"] = tuple(sorted(cast(Sequence[str], raw["training_target_ids"])))
        raw["purged_target_ids"] = tuple(sorted(cast(Sequence[str], raw["purged_target_ids"])))
        raw["inner_fit_target_ids"] = tuple(
            sorted(cast(Sequence[str], raw["inner_fit_target_ids"]))
        )
        raw["inner_validation_target_ids"] = tuple(
            sorted(cast(Sequence[str], raw["inner_validation_target_ids"]))
        )
        raw["alpha_candidate_scores"] = tuple(
            cast(Sequence[R2AlphaCandidateScore], raw["alpha_candidate_scores"])
        )
        raw["sample_weights"] = tuple(
            sorted(cast(Sequence[tuple[str, float]], raw["sample_weights"]))
        )
        semantic: dict[str, JsonValue] = {
            "contract": cls.CONTRACT,
            "schema_version": 1,
            **{
                key: _contract_json(value)
                for key, value in raw.items()
                if key != "runtime_identities"
            },
        }
        constructor = cast(Callable[..., R2FinalFit], cls)
        return constructor(**raw, fit_id=_semantic_id(semantic))

    def semantic_json(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": self.SCHEMA_VERSION,
            "selection_manifest_id": self.selection_manifest_id,
            "configuration_id": self.configuration_id,
            "model_family": self.model_family.value,
            "target_instrument_id": self.target_instrument_id,
            "feature_dataset_id": self.feature_dataset_id,
            "feature_schema_id": self.feature_schema_id,
            "training_cutoff": self.training_cutoff.isoformat(),
            "training_target_ids": list(self.training_target_ids),
            "purged_target_ids": list(self.purged_target_ids),
            "inner_fit_target_ids": list(self.inner_fit_target_ids),
            "inner_validation_target_ids": list(self.inner_validation_target_ids),
            "preprocessing": _json_object(self.preprocessing),
            "alpha_candidate_scores": [item.as_json() for item in self.alpha_candidate_scores],
            "selected_alpha": self.selected_alpha,
            "sample_weights": [[target_id, weight] for target_id, weight in self.sample_weights],
            "coefficients": list(self.coefficients) if self.coefficients is not None else None,
            "intercept": self.intercept,
            "disposition": self.disposition.value,
            "failure_reason": self.failure_reason,
            "diagnostics": _json_object(self.diagnostics),
            "evidence_class": self.evidence_class.value,
            "holdout_scope": self.holdout_scope.value,
        }

    def as_json(self) -> dict[str, JsonValue]:
        return {
            **self.semantic_json(),
            "runtime_identities": _json_object(self.runtime_identities),
            "fit_id": self.fit_id,
        }


@dataclass(frozen=True, slots=True)
class R2HoldoutForecastRow:
    configuration_id: str
    target_id: str
    target_instrument_id: str
    feature_row_id: str | None
    forecast: float
    model_family: ModelFamily
    row_id: str

    CONTRACT: ClassVar[str] = "qtrad-r2-holdout-forecast-row-v1"

    def __post_init__(self) -> None:
        for value, field in (
            (self.configuration_id, "forecast configuration ID"),
            (self.target_id, "forecast target ID"),
            (self.row_id, "forecast row ID"),
        ):
            _require_id(value, field)
        if not self.target_instrument_id.strip():
            raise ValueError("forecast target instrument cannot be blank")
        if self.feature_row_id is not None:
            _require_id(self.feature_row_id, "forecast feature row ID")
        _finite(self.forecast, "holdout forecast")
        if self.row_id != _semantic_id(self.semantic_json()):
            raise ValueError("forecast row ID does not authenticate its content")

    @classmethod
    def create(
        cls,
        *,
        configuration_id: str,
        target_id: str,
        target_instrument_id: str,
        feature_row_id: str | None,
        forecast: float,
        model_family: ModelFamily,
    ) -> R2HoldoutForecastRow:
        semantic = {
            "contract": cls.CONTRACT,
            "schema_version": 1,
            "configuration_id": configuration_id,
            "target_id": target_id,
            "target_instrument_id": target_instrument_id,
            "feature_row_id": feature_row_id,
            "forecast": forecast,
            "model_family": model_family.value,
        }
        return cls(
            configuration_id=configuration_id,
            target_id=target_id,
            target_instrument_id=target_instrument_id,
            feature_row_id=feature_row_id,
            forecast=forecast,
            model_family=model_family,
            row_id=_semantic_id(semantic),
        )

    def semantic_json(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": 1,
            "configuration_id": self.configuration_id,
            "target_id": self.target_id,
            "target_instrument_id": self.target_instrument_id,
            "feature_row_id": self.feature_row_id,
            "forecast": self.forecast,
            "model_family": self.model_family.value,
        }

    def as_json(self) -> dict[str, JsonValue]:
        return {**self.semantic_json(), "row_id": self.row_id}


@dataclass(frozen=True, slots=True)
class R2HoldoutForecastDataset:
    selection_manifest_id: str
    feature_dataset_id: str | None
    configuration_id: str
    final_fit_id: str | None
    rows: tuple[R2HoldoutForecastRow, ...]
    expected_opportunity_ids: tuple[str, ...]
    opportunity_target_ids: tuple[tuple[str, str], ...]
    source_class: MarketDataSourceClass
    evidence_class: EvidenceClass
    holdout_scope: HoldoutScope
    holdout_outcomes_accessed: bool
    dataset_id: str
    final_fit_ids: tuple[str, ...] = ()

    CONTRACT: ClassVar[str] = R2_HOLDOUT_FORECAST_CONTRACT
    SCHEMA_VERSION: ClassVar[int] = 1

    def __post_init__(self) -> None:
        _require_id(self.selection_manifest_id, "forecast selection ID")
        if self.feature_dataset_id is not None:
            _require_id(self.feature_dataset_id, "forecast feature dataset ID")
        _require_id(self.configuration_id, "forecast configuration ID")
        _require_id(self.dataset_id, "forecast dataset ID")
        if self.final_fit_id is not None:
            _require_id(self.final_fit_id, "forecast final-fit ID")
        _ordered_ids(self.final_fit_ids, "forecast final-fit IDs")
        _ordered_ids(self.expected_opportunity_ids, "forecast expected opportunities")
        opportunity_ids = tuple(item[0] for item in self.opportunity_target_ids)
        target_ids = tuple(item[1] for item in self.opportunity_target_ids)
        if tuple(sorted(self.opportunity_target_ids)) != self.opportunity_target_ids:
            raise ValueError("forecast opportunity bindings must be ordered")
        if len(set(opportunity_ids)) != len(opportunity_ids):
            raise ValueError("forecast opportunity bindings must be unique")
        if len(set(target_ids)) != len(target_ids):
            raise ValueError("forecast target bindings must have unique targets")
        if set(opportunity_ids) != set(self.expected_opportunity_ids):
            raise ValueError(
                "forecast opportunity bindings must exactly cover expected opportunities"
            )
        if self.holdout_outcomes_accessed:
            raise ValueError("holdout forecast generation cannot access outcomes")
        rows = tuple(sorted(self.rows, key=lambda item: item.row_id))
        if rows != self.rows or len({item.row_id for item in rows}) != len(rows):
            raise ValueError("forecast rows must be unique and ordered")
        if self.dataset_id != _semantic_id(self.semantic_json()):
            raise ValueError("forecast dataset ID does not authenticate its content")

    @classmethod
    def create(cls, **values: object) -> R2HoldoutForecastDataset:
        raw = dict(values)
        raw.pop("dataset_id", None)
        raw.setdefault("holdout_outcomes_accessed", False)
        raw["rows"] = tuple(
            sorted(cast(Sequence[R2HoldoutForecastRow], raw["rows"]), key=lambda item: item.row_id)
        )
        raw["expected_opportunity_ids"] = tuple(
            sorted(cast(Sequence[str], raw["expected_opportunity_ids"]))
        )
        raw["opportunity_target_ids"] = tuple(
            sorted(
                cast(Sequence[tuple[str, str]], raw["opportunity_target_ids"]),
                key=lambda item: item[0],
            )
        )
        if "final_fit_ids" not in raw:
            raw["final_fit_ids"] = (
                (raw["final_fit_id"],) if raw.get("final_fit_id") is not None else ()
            )
        raw["final_fit_ids"] = tuple(sorted(cast(Sequence[str], raw["final_fit_ids"])))
        semantic: dict[str, JsonValue] = {
            "contract": cls.CONTRACT,
            "schema_version": 1,
            **{key: _contract_json(value) for key, value in raw.items()},
        }
        constructor = cast(Callable[..., R2HoldoutForecastDataset], cls)
        return constructor(**raw, dataset_id=_semantic_id(semantic))

    def semantic_json(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": self.SCHEMA_VERSION,
            "selection_manifest_id": self.selection_manifest_id,
            "feature_dataset_id": self.feature_dataset_id,
            "configuration_id": self.configuration_id,
            "final_fit_id": self.final_fit_id,
            "final_fit_ids": list(self.final_fit_ids),
            "rows": [item.as_json() for item in self.rows],
            "expected_opportunity_ids": list(self.expected_opportunity_ids),
            "opportunity_target_ids": [list(item) for item in self.opportunity_target_ids],
            "source_class": self.source_class.value,
            "evidence_class": self.evidence_class.value,
            "holdout_scope": self.holdout_scope.value,
            "holdout_outcomes_accessed": self.holdout_outcomes_accessed,
        }

    def as_json(self) -> dict[str, JsonValue]:
        return {**self.semantic_json(), "dataset_id": self.dataset_id}


@dataclass(frozen=True, slots=True)
class R2HoldoutCoverageRow:
    configuration_id: str
    opportunity_id: str
    disposition: HoldoutOpportunityDisposition
    forecast_row_id: str | None
    reason: str

    def __post_init__(self) -> None:
        _require_id(self.configuration_id, "coverage configuration ID")
        _require_id(self.opportunity_id, "coverage opportunity ID")
        if self.forecast_row_id is not None:
            _require_id(self.forecast_row_id, "coverage forecast row ID")
        _require_text(self.reason, "coverage reason")

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "configuration_id": self.configuration_id,
            "opportunity_id": self.opportunity_id,
            "disposition": self.disposition.value,
            "forecast_row_id": self.forecast_row_id,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class R2HoldoutCoverageDataset:
    selection_manifest_id: str
    feature_dataset_id: str | None
    configuration_id: str
    expected_opportunity_ids: tuple[str, ...]
    rows: tuple[R2HoldoutCoverageRow, ...]
    source_class: MarketDataSourceClass
    evidence_class: EvidenceClass
    holdout_scope: HoldoutScope
    holdout_outcomes_accessed: bool
    coverage_id: str

    CONTRACT: ClassVar[str] = R2_HOLDOUT_COVERAGE_CONTRACT
    SCHEMA_VERSION: ClassVar[int] = 1

    def __post_init__(self) -> None:
        _require_id(self.selection_manifest_id, "coverage selection ID")
        if self.feature_dataset_id is not None:
            _require_id(self.feature_dataset_id, "coverage feature dataset ID")
        _require_id(self.configuration_id, "coverage configuration ID")
        _require_id(self.coverage_id, "coverage ID")
        expected = _ordered_ids(self.expected_opportunity_ids, "coverage expected opportunities")
        if self.holdout_outcomes_accessed:
            raise ValueError("coverage generation cannot access outcomes")
        keys = tuple((item.configuration_id, item.opportunity_id) for item in self.rows)
        if len(keys) != len(set(keys)) or tuple(sorted(keys)) != keys:
            raise ValueError("coverage rows must uniquely cover ordered opportunities")
        if set(item.opportunity_id for item in self.rows) != set(expected):
            raise ValueError("coverage must retain one row for every expected opportunity")
        if any(item.configuration_id != self.configuration_id for item in self.rows):
            raise ValueError("coverage row configuration differs from its dataset")
        if self.coverage_id != _semantic_id(self.semantic_json()):
            raise ValueError("coverage ID does not authenticate its content")

    @classmethod
    def create(cls, **values: object) -> R2HoldoutCoverageDataset:
        raw = dict(values)
        raw.pop("coverage_id", None)
        raw.setdefault("holdout_outcomes_accessed", False)
        raw["expected_opportunity_ids"] = tuple(
            sorted(cast(Sequence[str], raw["expected_opportunity_ids"]))
        )
        raw["rows"] = tuple(
            sorted(
                cast(Sequence[R2HoldoutCoverageRow], raw["rows"]),
                key=lambda item: (item.configuration_id, item.opportunity_id),
            )
        )
        semantic: dict[str, JsonValue] = {
            "contract": cls.CONTRACT,
            "schema_version": 1,
            **{key: _contract_json(value) for key, value in raw.items()},
        }
        constructor = cast(Callable[..., R2HoldoutCoverageDataset], cls)
        return constructor(**raw, coverage_id=_semantic_id(semantic))

    def semantic_json(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": self.SCHEMA_VERSION,
            "selection_manifest_id": self.selection_manifest_id,
            "feature_dataset_id": self.feature_dataset_id,
            "configuration_id": self.configuration_id,
            "expected_opportunity_ids": list(self.expected_opportunity_ids),
            "rows": [item.as_json() for item in self.rows],
            "source_class": self.source_class.value,
            "evidence_class": self.evidence_class.value,
            "holdout_scope": self.holdout_scope.value,
            "holdout_outcomes_accessed": self.holdout_outcomes_accessed,
        }

    def as_json(self) -> dict[str, JsonValue]:
        return {**self.semantic_json(), "coverage_id": self.coverage_id}


@dataclass(frozen=True, slots=True)
class R2HoldoutForecastSeal:
    selection_manifest_id: str
    configuration_feature_dataset_ids: tuple[tuple[str, str | None], ...]
    final_fit_ids: tuple[str, ...]
    forecast_dataset_ids: tuple[str, ...]
    coverage_ids: tuple[str, ...]
    metric_policy: Mapping[str, JsonValue]
    comparison_support: Mapping[str, JsonValue]
    forecast_buckets: Mapping[str, JsonValue]
    state_buckets: Mapping[str, JsonValue]
    configuration_pairs: tuple[tuple[str, str], ...]
    coverage_rules: Mapping[str, JsonValue]
    questions: tuple[R2HoldoutQuestion, ...]
    source_class: MarketDataSourceClass
    evidence_class: EvidenceClass
    holdout_scope: HoldoutScope
    runtime_identities: Mapping[str, JsonValue]
    prepared_at: datetime
    prepared_by: str
    state: HoldoutPreparationState
    holdout_outcomes_accessed: bool
    seal_id: str

    CONTRACT: ClassVar[str] = R2_HOLDOUT_FORECAST_SEAL_CONTRACT
    SCHEMA_VERSION: ClassVar[int] = 1

    def __post_init__(self) -> None:
        _require_id(self.selection_manifest_id, "seal selection ID")
        _require_id(self.seal_id, "holdout forecast seal ID")
        mapping_keys = [
            configuration_id for configuration_id, _ in self.configuration_feature_dataset_ids
        ]
        if any(not configuration_id for configuration_id in mapping_keys):
            raise ValueError("seal configuration feature mapping has an empty configuration ID")
        if tuple(sorted(mapping_keys)) != tuple(mapping_keys) or len(set(mapping_keys)) != len(
            mapping_keys
        ):
            raise ValueError("seal configuration feature mapping must be ordered and unique")
        for configuration_id, feature_dataset_id in self.configuration_feature_dataset_ids:
            if feature_dataset_id is not None:
                _require_id(feature_dataset_id, f"seal feature dataset for {configuration_id}")
        for values, field in (
            (self.final_fit_ids, "seal final-fit IDs"),
            (self.forecast_dataset_ids, "seal forecast IDs"),
            (self.coverage_ids, "seal coverage IDs"),
        ):
            _ordered_ids(values, field)
        if not self.forecast_dataset_ids or not self.coverage_ids:
            raise ValueError("seal must bind forecasts and coverage")
        if len(self.forecast_dataset_ids) != len(self.coverage_ids):
            raise ValueError("seal forecast and coverage arrays must have matching cardinality")
        if len(self.configuration_pairs) == 0:
            raise ValueError("seal must freeze at least one configuration pair")
        if len(set(self.questions)) != len(self.questions):
            raise ValueError("seal question register must be unique")
        if self.state is not HoldoutPreparationState.PREPARED_UNOPENED:
            raise ValueError("holdout forecast seal must be PREPARED_UNOPENED")
        if self.holdout_outcomes_accessed:
            raise ValueError("holdout forecast seal cannot access outcomes")
        require_utc(self.prepared_at, "holdout preparation time")
        _require_text(self.prepared_by, "holdout prepared-by")
        if self.holdout_scope is HoldoutScope.DISPOSABLE_FIXTURE and (
            self.evidence_class is not EvidenceClass.IMPLEMENTATION
        ):
            raise ValueError("disposable holdout seals require implementation evidence")
        if self.seal_id != _semantic_id(self.semantic_json()):
            raise ValueError("holdout forecast seal ID does not authenticate its content")

    @classmethod
    def create(cls, **values: object) -> R2HoldoutForecastSeal:
        raw = dict(values)
        raw.pop("seal_id", None)
        raw.setdefault("state", HoldoutPreparationState.PREPARED_UNOPENED)
        raw.setdefault("holdout_outcomes_accessed", False)
        raw["configuration_feature_dataset_ids"] = tuple(
            sorted(
                (
                    str(configuration_id),
                    None if feature_dataset_id is None else str(feature_dataset_id),
                )
                for configuration_id, feature_dataset_id in cast(
                    Sequence[tuple[str, str | None]],
                    raw["configuration_feature_dataset_ids"],
                )
            )
        )
        for key in ("final_fit_ids", "forecast_dataset_ids", "coverage_ids"):
            raw[key] = tuple(sorted(cast(Sequence[str], raw[key])))
        raw["questions"] = tuple(cast(Sequence[R2HoldoutQuestion], raw["questions"]))
        semantic: dict[str, JsonValue] = {
            "contract": cls.CONTRACT,
            "schema_version": 1,
            **{
                key: _contract_json(value)
                for key, value in raw.items()
                if key not in {"runtime_identities", "prepared_at", "prepared_by"}
            },
        }
        constructor = cast(Callable[..., R2HoldoutForecastSeal], cls)
        return constructor(**raw, seal_id=_semantic_id(semantic))

    def semantic_json(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": self.SCHEMA_VERSION,
            "selection_manifest_id": self.selection_manifest_id,
            "configuration_feature_dataset_ids": [
                [configuration_id, feature_dataset_id]
                for configuration_id, feature_dataset_id in self.configuration_feature_dataset_ids
            ],
            "final_fit_ids": list(self.final_fit_ids),
            "forecast_dataset_ids": list(self.forecast_dataset_ids),
            "coverage_ids": list(self.coverage_ids),
            "metric_policy": _json_object(self.metric_policy),
            "comparison_support": _json_object(self.comparison_support),
            "forecast_buckets": _json_object(self.forecast_buckets),
            "state_buckets": _json_object(self.state_buckets),
            "configuration_pairs": [list(pair) for pair in self.configuration_pairs],
            "coverage_rules": _json_object(self.coverage_rules),
            "questions": [item.as_json() for item in self.questions],
            "source_class": self.source_class.value,
            "evidence_class": self.evidence_class.value,
            "holdout_scope": self.holdout_scope.value,
            "state": self.state.value,
            "holdout_outcomes_accessed": self.holdout_outcomes_accessed,
        }

    def as_json(self) -> dict[str, JsonValue]:
        return {
            **self.semantic_json(),
            "runtime_identities": _json_object(self.runtime_identities),
            "prepared_at": self.prepared_at.isoformat(),
            "prepared_by": self.prepared_by,
            "seal_id": self.seal_id,
        }

    @classmethod
    def from_json(cls, value: object) -> R2HoldoutForecastSeal:
        if not isinstance(value, dict):
            raise ValueError("holdout forecast seal must be an object")
        raw = cast(dict[str, object], value)
        expected = {
            "contract",
            "schema_version",
            "selection_manifest_id",
            "configuration_feature_dataset_ids",
            "final_fit_ids",
            "forecast_dataset_ids",
            "coverage_ids",
            "metric_policy",
            "comparison_support",
            "forecast_buckets",
            "state_buckets",
            "configuration_pairs",
            "coverage_rules",
            "questions",
            "source_class",
            "evidence_class",
            "holdout_scope",
            "runtime_identities",
            "prepared_at",
            "prepared_by",
            "state",
            "holdout_outcomes_accessed",
            "seal_id",
        }
        if set(raw) != expected or raw["contract"] != cls.CONTRACT or raw["schema_version"] != 1:
            raise ValueError("holdout forecast seal has unknown or unsupported fields")
        feature_mapping_value = raw["configuration_feature_dataset_ids"]
        if not isinstance(feature_mapping_value, list):
            raise ValueError("seal configuration feature mapping must be an array")
        feature_mapping = cast(list[object], feature_mapping_value)
        pairs = raw["configuration_pairs"]
        if not isinstance(pairs, list):
            raise ValueError("seal configuration pairs must be an array")
        questions = raw["questions"]
        if not isinstance(questions, list):
            raise ValueError("seal questions must be an array")
        mapping: list[tuple[str, str | None]] = []
        for raw_item in feature_mapping:
            if not isinstance(raw_item, list):
                raise ValueError("seal configuration feature mapping entries must be pairs")
            item = cast(list[object], raw_item)
            if len(item) != 2:
                raise ValueError("seal configuration feature mapping entries must be pairs")
            mapping.append((str(item[0]), None if item[1] is None else str(item[1])))
        return cls(
            selection_manifest_id=str(raw["selection_manifest_id"]),
            configuration_feature_dataset_ids=tuple(mapping),
            final_fit_ids=tuple(str(item) for item in cast(list[object], raw["final_fit_ids"])),
            forecast_dataset_ids=tuple(
                str(item) for item in cast(list[object], raw["forecast_dataset_ids"])
            ),
            coverage_ids=tuple(str(item) for item in cast(list[object], raw["coverage_ids"])),
            metric_policy=cast(Mapping[str, JsonValue], raw["metric_policy"]),
            comparison_support=cast(Mapping[str, JsonValue], raw["comparison_support"]),
            forecast_buckets=cast(Mapping[str, JsonValue], raw["forecast_buckets"]),
            state_buckets=cast(Mapping[str, JsonValue], raw["state_buckets"]),
            configuration_pairs=tuple(
                (str(cast(list[object], pair)[0]), str(cast(list[object], pair)[1]))
                for pair in cast(list[object], pairs)
            ),
            coverage_rules=cast(Mapping[str, JsonValue], raw["coverage_rules"]),
            questions=tuple(
                R2HoldoutQuestion.from_json(item) for item in cast(list[object], questions)
            ),
            source_class=MarketDataSourceClass(str(raw["source_class"])),
            evidence_class=EvidenceClass(str(raw["evidence_class"])),
            holdout_scope=HoldoutScope(str(raw["holdout_scope"])),
            runtime_identities=cast(Mapping[str, JsonValue], raw["runtime_identities"]),
            prepared_at=datetime.fromisoformat(str(raw["prepared_at"])),
            prepared_by=str(raw["prepared_by"]),
            state=HoldoutPreparationState(str(raw["state"])),
            holdout_outcomes_accessed=bool(raw["holdout_outcomes_accessed"]),
            seal_id=str(raw["seal_id"]),
        )


@dataclass(frozen=True, slots=True)
class R2HoldoutOpenedMarker:
    selection_manifest_id: str
    seal_id: str
    opened_at: datetime
    opened_by: str
    acknowledgement: str
    expected_selection_manifest_id: str
    expected_seal_id: str
    state: HoldoutMarkerState
    marker_id: str

    CONTRACT: ClassVar[str] = R2_HOLDOUT_OPENED_CONTRACT
    SCHEMA_VERSION: ClassVar[int] = 1

    def __post_init__(self) -> None:
        for value, field in (
            (self.selection_manifest_id, "opened selection ID"),
            (self.seal_id, "opened seal ID"),
            (self.expected_selection_manifest_id, "opened expected selection ID"),
            (self.expected_seal_id, "opened expected seal ID"),
            (self.marker_id, "opened marker ID"),
        ):
            _require_id(value, field)
        if (
            self.selection_manifest_id != self.expected_selection_manifest_id
            or self.seal_id != self.expected_seal_id
        ):
            raise ValueError("opened marker acknowledgement IDs do not match its children")
        require_utc(self.opened_at, "holdout opened time")
        _require_text(self.opened_by, "holdout opened-by")
        if self.acknowledgement != HOLDOUT_ACKNOWLEDGEMENT:
            raise ValueError("exact holdout acknowledgement is required")
        if self.state is not HoldoutMarkerState.OPENED:
            raise ValueError("opened marker must be OPENED")
        if self.marker_id != _semantic_id(self.semantic_json()):
            raise ValueError("opened marker ID does not authenticate its content")

    @classmethod
    def create(
        cls,
        *,
        selection_manifest_id: str,
        seal_id: str,
        opened_at: datetime,
        opened_by: str,
        acknowledgement: str,
        expected_selection_manifest_id: str,
        expected_seal_id: str,
    ) -> R2HoldoutOpenedMarker:
        semantic = {
            "contract": cls.CONTRACT,
            "schema_version": 1,
            "selection_manifest_id": selection_manifest_id,
            "seal_id": seal_id,
            "opened_at": opened_at.isoformat(),
            "opened_by": opened_by,
            "acknowledgement": acknowledgement,
            "expected_selection_manifest_id": expected_selection_manifest_id,
            "expected_seal_id": expected_seal_id,
            "state": HoldoutMarkerState.OPENED.value,
        }
        return cls(
            selection_manifest_id=selection_manifest_id,
            seal_id=seal_id,
            opened_at=opened_at,
            opened_by=opened_by,
            acknowledgement=acknowledgement,
            expected_selection_manifest_id=expected_selection_manifest_id,
            expected_seal_id=expected_seal_id,
            state=HoldoutMarkerState.OPENED,
            marker_id=_semantic_id(semantic),
        )

    def semantic_json(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": self.SCHEMA_VERSION,
            "selection_manifest_id": self.selection_manifest_id,
            "seal_id": self.seal_id,
            "opened_at": self.opened_at.isoformat(),
            "opened_by": self.opened_by,
            "acknowledgement": self.acknowledgement,
            "expected_selection_manifest_id": self.expected_selection_manifest_id,
            "expected_seal_id": self.expected_seal_id,
            "state": self.state.value,
        }

    def as_json(self) -> dict[str, JsonValue]:
        return {**self.semantic_json(), "marker_id": self.marker_id}


@dataclass(frozen=True, slots=True)
class R2ConfirmatoryOpenedMarker:
    """Direct confirmatory lineage persisted after irreversible OPENED."""

    selection_manifest_id: str
    seal_id: str
    opened_marker_id: str
    oof_id: str
    foundation_bundle_id: str
    experiment_configuration_id: str
    evaluation_report_id: str
    prior_selection_manifest_id: str
    holdout_target_source_id: str
    target_dataset_id: str
    target_manifest_id: str
    holdout_range: tuple[datetime, datetime]
    source_class: MarketDataSourceClass
    evidence_class: EvidenceClass
    holdout_scope: HoldoutScope
    runtime_identities: Mapping[str, JsonValue]
    opened_at: datetime
    opened_by: str
    acknowledgement: str
    state: HoldoutMarkerState
    marker_id: str

    CONTRACT: ClassVar[str] = R2_CONFIRMATORY_OPENED_CONTRACT
    SCHEMA_VERSION: ClassVar[int] = 1

    def __post_init__(self) -> None:
        for value, field in (
            (self.selection_manifest_id, "confirmatory selection ID"),
            (self.seal_id, "confirmatory seal ID"),
            (self.opened_marker_id, "confirmatory opened marker ID"),
            (self.oof_id, "confirmatory OOF semantic ID"),
            (self.foundation_bundle_id, "confirmatory foundation bundle ID"),
            (self.experiment_configuration_id, "confirmatory experiment ID"),
            (self.evaluation_report_id, "confirmatory F2 evaluation ID"),
            (self.prior_selection_manifest_id, "confirmatory F2 selection ID"),
            (self.holdout_target_source_id, "confirmatory target source ID"),
            (self.target_dataset_id, "confirmatory target dataset ID"),
            (self.target_manifest_id, "confirmatory target manifest ID"),
            (self.marker_id, "confirmatory opened identity"),
        ):
            _require_id(value, field)
        require_utc(self.holdout_range[0], "confirmatory holdout start")
        require_utc(self.holdout_range[1], "confirmatory holdout end")
        if self.holdout_range[0] >= self.holdout_range[1]:
            raise ValueError("confirmatory holdout range must be increasing")
        if self.evidence_class is not EvidenceClass.CONFIRMATORY:
            raise ValueError("confirmatory opened marker requires confirmatory evidence")
        if self.holdout_scope is not HoldoutScope.CONFIRMATORY:
            raise ValueError("confirmatory opened marker requires confirmatory scope")
        if self.state is not HoldoutMarkerState.OPENED:
            raise ValueError("confirmatory opened marker must be OPENED")
        require_utc(self.opened_at, "confirmatory opened time")
        _require_text(self.opened_by, "confirmatory opened-by")
        if self.acknowledgement != HOLDOUT_ACKNOWLEDGEMENT:
            raise ValueError("exact holdout acknowledgement is required")
        if not self.runtime_identities:
            raise ValueError("confirmatory opened marker requires runtime identities")
        if self.marker_id != _semantic_id(self.semantic_json()):
            raise ValueError("confirmatory opened marker ID does not authenticate its content")

    @classmethod
    def create(
        cls,
        *,
        selection_manifest_id: str,
        seal_id: str,
        opened_marker_id: str,
        oof_id: str,
        foundation_bundle_id: str,
        experiment_configuration_id: str,
        evaluation_report_id: str,
        prior_selection_manifest_id: str,
        holdout_target_source_id: str,
        target_dataset_id: str,
        target_manifest_id: str,
        holdout_range: tuple[datetime, datetime],
        source_class: MarketDataSourceClass,
        runtime_identities: Mapping[str, JsonValue],
        opened_at: datetime,
        opened_by: str,
        acknowledgement: str,
    ) -> R2ConfirmatoryOpenedMarker:
        semantic: dict[str, JsonValue] = {
            "contract": cls.CONTRACT,
            "schema_version": cls.SCHEMA_VERSION,
            "selection_manifest_id": selection_manifest_id,
            "seal_id": seal_id,
            "opened_marker_id": opened_marker_id,
            "oof_id": oof_id,
            "foundation_bundle_id": foundation_bundle_id,
            "experiment_configuration_id": experiment_configuration_id,
            "evaluation_report_id": evaluation_report_id,
            "prior_selection_manifest_id": prior_selection_manifest_id,
            "holdout_target_source_id": holdout_target_source_id,
            "target_dataset_id": target_dataset_id,
            "target_manifest_id": target_manifest_id,
            "holdout_range": [value.isoformat() for value in holdout_range],
            "source_class": source_class.value,
            "evidence_class": EvidenceClass.CONFIRMATORY.value,
            "holdout_scope": HoldoutScope.CONFIRMATORY.value,
            "runtime_identities": _json_object(runtime_identities),
            "opened_at": opened_at.isoformat(),
            "opened_by": opened_by,
            "acknowledgement": acknowledgement,
            "state": HoldoutMarkerState.OPENED.value,
        }
        return cls(
            selection_manifest_id=selection_manifest_id,
            seal_id=seal_id,
            opened_marker_id=opened_marker_id,
            oof_id=oof_id,
            foundation_bundle_id=foundation_bundle_id,
            experiment_configuration_id=experiment_configuration_id,
            evaluation_report_id=evaluation_report_id,
            prior_selection_manifest_id=prior_selection_manifest_id,
            holdout_target_source_id=holdout_target_source_id,
            target_dataset_id=target_dataset_id,
            target_manifest_id=target_manifest_id,
            holdout_range=holdout_range,
            source_class=source_class,
            evidence_class=EvidenceClass.CONFIRMATORY,
            holdout_scope=HoldoutScope.CONFIRMATORY,
            runtime_identities=_json_object(runtime_identities),
            opened_at=opened_at,
            opened_by=opened_by,
            acknowledgement=acknowledgement,
            state=HoldoutMarkerState.OPENED,
            marker_id=_semantic_id(semantic),
        )

    def semantic_json(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": self.SCHEMA_VERSION,
            "selection_manifest_id": self.selection_manifest_id,
            "seal_id": self.seal_id,
            "opened_marker_id": self.opened_marker_id,
            "oof_id": self.oof_id,
            "foundation_bundle_id": self.foundation_bundle_id,
            "experiment_configuration_id": self.experiment_configuration_id,
            "evaluation_report_id": self.evaluation_report_id,
            "prior_selection_manifest_id": self.prior_selection_manifest_id,
            "holdout_target_source_id": self.holdout_target_source_id,
            "target_dataset_id": self.target_dataset_id,
            "target_manifest_id": self.target_manifest_id,
            "holdout_range": [value.isoformat() for value in self.holdout_range],
            "source_class": self.source_class.value,
            "evidence_class": self.evidence_class.value,
            "holdout_scope": self.holdout_scope.value,
            "runtime_identities": _json_object(self.runtime_identities),
            "opened_at": self.opened_at.isoformat(),
            "opened_by": self.opened_by,
            "acknowledgement": self.acknowledgement,
            "state": self.state.value,
        }

    def as_json(self) -> dict[str, JsonValue]:
        return {**self.semantic_json(), "marker_id": self.marker_id}

    @classmethod
    def from_json(cls, value: Mapping[str, object]) -> R2ConfirmatoryOpenedMarker:
        raw_range = cast(list[object], value["holdout_range"])
        return cls(
            selection_manifest_id=str(value["selection_manifest_id"]),
            seal_id=str(value["seal_id"]),
            opened_marker_id=str(value["opened_marker_id"]),
            oof_id=str(value["oof_id"]),
            foundation_bundle_id=str(value["foundation_bundle_id"]),
            experiment_configuration_id=str(value["experiment_configuration_id"]),
            evaluation_report_id=str(value["evaluation_report_id"]),
            prior_selection_manifest_id=str(value["prior_selection_manifest_id"]),
            holdout_target_source_id=str(value["holdout_target_source_id"]),
            target_dataset_id=str(value["target_dataset_id"]),
            target_manifest_id=str(value["target_manifest_id"]),
            holdout_range=(
                datetime.fromisoformat(str(raw_range[0])),
                datetime.fromisoformat(str(raw_range[1])),
            ),
            source_class=MarketDataSourceClass(str(value["source_class"])),
            evidence_class=EvidenceClass(str(value["evidence_class"])),
            holdout_scope=HoldoutScope(str(value["holdout_scope"])),
            runtime_identities=cast(Mapping[str, JsonValue], value["runtime_identities"]),
            opened_at=datetime.fromisoformat(str(value["opened_at"])),
            opened_by=str(value["opened_by"]),
            acknowledgement=str(value["acknowledgement"]),
            state=HoldoutMarkerState(str(value["state"])),
            marker_id=str(value["marker_id"]),
        )


@dataclass(frozen=True, slots=True)
class R2HoldoutConsumedMarker:
    selection_manifest_id: str
    seal_id: str
    opened_marker_id: str
    consumed_at: datetime
    consumed_by: str
    evaluation_id: str
    outcome_accessed: bool
    state: HoldoutMarkerState
    marker_id: str

    CONTRACT: ClassVar[str] = R2_HOLDOUT_CONSUMED_CONTRACT
    SCHEMA_VERSION: ClassVar[int] = 1

    def __post_init__(self) -> None:
        for value, field in (
            (self.selection_manifest_id, "consumed selection ID"),
            (self.seal_id, "consumed seal ID"),
            (self.opened_marker_id, "consumed opened marker ID"),
            (self.evaluation_id, "consumed evaluation ID"),
            (self.marker_id, "consumed marker ID"),
        ):
            _require_id(value, field)
        require_utc(self.consumed_at, "holdout consumed time")
        _require_text(self.consumed_by, "holdout consumed-by")
        if not self.outcome_accessed:
            raise ValueError("consumed marker must record outcome access")
        if self.state is not HoldoutMarkerState.CONSUMED:
            raise ValueError("consumed marker must be CONSUMED")
        if self.marker_id != _semantic_id(self.semantic_json()):
            raise ValueError("consumed marker ID does not authenticate its content")

    @classmethod
    def create(
        cls,
        *,
        selection_manifest_id: str,
        seal_id: str,
        opened_marker_id: str,
        consumed_at: datetime,
        consumed_by: str,
        evaluation_id: str,
    ) -> R2HoldoutConsumedMarker:
        semantic = {
            "contract": cls.CONTRACT,
            "schema_version": 1,
            "selection_manifest_id": selection_manifest_id,
            "seal_id": seal_id,
            "opened_marker_id": opened_marker_id,
            "consumed_at": consumed_at.isoformat(),
            "consumed_by": consumed_by,
            "evaluation_id": evaluation_id,
            "outcome_accessed": True,
            "state": HoldoutMarkerState.CONSUMED.value,
        }
        return cls(
            selection_manifest_id=selection_manifest_id,
            seal_id=seal_id,
            opened_marker_id=opened_marker_id,
            consumed_at=consumed_at,
            consumed_by=consumed_by,
            evaluation_id=evaluation_id,
            outcome_accessed=True,
            state=HoldoutMarkerState.CONSUMED,
            marker_id=_semantic_id(semantic),
        )

    def semantic_json(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": self.SCHEMA_VERSION,
            "selection_manifest_id": self.selection_manifest_id,
            "seal_id": self.seal_id,
            "opened_marker_id": self.opened_marker_id,
            "consumed_at": self.consumed_at.isoformat(),
            "consumed_by": self.consumed_by,
            "evaluation_id": self.evaluation_id,
            "outcome_accessed": self.outcome_accessed,
            "state": self.state.value,
        }

    def as_json(self) -> dict[str, JsonValue]:
        return {**self.semantic_json(), "marker_id": self.marker_id}


@dataclass(frozen=True, slots=True)
class R2HoldoutQuestionResult:
    question_id: str
    metric: str
    candidate_value: float | None
    comparator_value: float | None
    delta: float | None
    support_count: int
    coverage: float
    conclusion: HoldoutConclusion
    reason: str

    def __post_init__(self) -> None:
        _require_id(self.question_id, "question result ID")
        if self.candidate_value is not None:
            _finite(self.candidate_value, "candidate metric")
        if self.comparator_value is not None:
            _finite(self.comparator_value, "comparator metric")
        if self.delta is not None:
            _finite(self.delta, "question metric delta")
        if self.support_count < 0:
            raise ValueError("question support cannot be negative")
        if not 0 <= self.coverage <= 1 or not isfinite(self.coverage):
            raise ValueError("question coverage must be in [0, 1]")
        _require_text(self.reason, "question result reason")

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "question_id": self.question_id,
            "metric": self.metric,
            "candidate_value": self.candidate_value,
            "comparator_value": self.comparator_value,
            "delta": self.delta,
            "support_count": self.support_count,
            "coverage": self.coverage,
            "conclusion": self.conclusion.value,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class R2HoldoutOutcomeEvidence:
    """Authenticated post-open outcomes; never constructed during preparation."""

    selection_manifest_id: str
    seal_id: str
    opened_marker_id: str
    experiment_configuration_id: str
    foundation_bundle_id: str
    feature_dataset_id: str
    holdout_range: tuple[datetime, datetime]
    expected_target_ids: tuple[str, ...]
    source_row_ids: tuple[tuple[str, str], ...]
    outcomes: tuple[tuple[str, float], ...]
    source_class: MarketDataSourceClass
    evidence_class: EvidenceClass
    holdout_scope: HoldoutScope
    outcome_evidence_id: str
    target_dataset_id: str | None = None

    CONTRACT: ClassVar[str] = R2_HOLDOUT_OUTCOME_EVIDENCE_CONTRACT
    SCHEMA_VERSION: ClassVar[int] = 1

    def __post_init__(self) -> None:
        for value, field in (
            (self.selection_manifest_id, "outcome selection ID"),
            (self.seal_id, "outcome seal ID"),
            (self.opened_marker_id, "outcome opened marker ID"),
            (self.experiment_configuration_id, "outcome experiment ID"),
            (self.foundation_bundle_id, "outcome foundation ID"),
            (self.feature_dataset_id, "outcome feature dataset ID"),
            (self.outcome_evidence_id, "outcome evidence ID"),
        ):
            _require_id(value, field)
        if self.target_dataset_id is None and self.holdout_scope is HoldoutScope.CONFIRMATORY:
            raise ValueError("confirmatory outcomes require authenticated target-dataset lineage")
        if self.target_dataset_id is not None:
            _require_id(self.target_dataset_id, "outcome target dataset ID")
        _positive_range(self.holdout_range, "outcome holdout range")
        if tuple(sorted(self.expected_target_ids)) != self.expected_target_ids:
            raise ValueError("outcome expected target IDs must be ordered")
        expected_sources = tuple(sorted(self.source_row_ids))
        if expected_sources != self.source_row_ids:
            raise ValueError("outcome source rows must be deterministically ordered")
        if tuple(target_id for target_id, _ in expected_sources) != self.expected_target_ids:
            raise ValueError("outcome source rows must cover every expected target")
        if len({row_id for _, row_id in expected_sources}) != len(expected_sources):
            raise ValueError("outcome source rows must be unique")
        for target_id, row_id in expected_sources:
            _require_id(target_id, "outcome source target ID")
            _require_id(row_id, "outcome source row ID")
        outcome_target_ids = tuple(target_id for target_id, _ in self.outcomes)
        if (
            tuple(sorted(self.outcomes)) != self.outcomes
            or len(set(outcome_target_ids)) != len(outcome_target_ids)
            or outcome_target_ids != self.expected_target_ids
        ):
            raise ValueError("holdout outcomes must exactly cover unique expected targets")
        for target_id, outcome in self.outcomes:
            _require_id(target_id, "holdout outcome target ID")
            _finite(outcome, "holdout outcome")
        if self.holdout_scope is HoldoutScope.DISPOSABLE_FIXTURE and (
            self.evidence_class is not EvidenceClass.IMPLEMENTATION
        ):
            raise ValueError("disposable outcome evidence requires implementation evidence")
        if self.outcome_evidence_id != _semantic_id(self.semantic_json()):
            raise ValueError("outcome evidence ID does not authenticate its content")

    @classmethod
    def create(cls, **values: object) -> R2HoldoutOutcomeEvidence:
        raw = dict(values)
        raw.pop("outcome_evidence_id", None)
        raw.setdefault("target_dataset_id", None)
        raw["expected_target_ids"] = tuple(sorted(cast(Sequence[str], raw["expected_target_ids"])))
        raw["source_row_ids"] = tuple(
            sorted(cast(Sequence[tuple[str, str]], raw["source_row_ids"]))
        )
        raw["outcomes"] = tuple(sorted(cast(Sequence[tuple[str, float]], raw["outcomes"])))
        semantic: dict[str, JsonValue] = {
            "contract": cls.CONTRACT,
            "schema_version": cls.SCHEMA_VERSION,
            **{key: _contract_json(value) for key, value in raw.items()},
        }
        constructor = cast(Callable[..., R2HoldoutOutcomeEvidence], cls)
        return constructor(**raw, outcome_evidence_id=_semantic_id(semantic))

    def semantic_json(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": self.SCHEMA_VERSION,
            "selection_manifest_id": self.selection_manifest_id,
            "seal_id": self.seal_id,
            "opened_marker_id": self.opened_marker_id,
            "experiment_configuration_id": self.experiment_configuration_id,
            "foundation_bundle_id": self.foundation_bundle_id,
            "feature_dataset_id": self.feature_dataset_id,
            "target_dataset_id": self.target_dataset_id,
            "holdout_range": [item.isoformat() for item in self.holdout_range],
            "expected_target_ids": list(self.expected_target_ids),
            "source_row_ids": [[target_id, row_id] for target_id, row_id in self.source_row_ids],
            "outcomes": [[target_id, outcome] for target_id, outcome in self.outcomes],
            "source_class": self.source_class.value,
            "evidence_class": self.evidence_class.value,
            "holdout_scope": self.holdout_scope.value,
        }

    def as_json(self) -> dict[str, JsonValue]:
        return {**self.semantic_json(), "outcome_evidence_id": self.outcome_evidence_id}


@dataclass(frozen=True, slots=True)
class R2HoldoutEvaluation:
    selection_manifest_id: str
    seal_id: str
    opened_marker_id: str
    questions: tuple[R2HoldoutQuestionResult, ...]
    source_class: MarketDataSourceClass
    evidence_class: EvidenceClass
    holdout_scope: HoldoutScope
    holdout_outcomes_accessed: bool
    evaluation_id: str

    CONTRACT: ClassVar[str] = R2_HOLDOUT_EVALUATION_CONTRACT
    SCHEMA_VERSION: ClassVar[int] = 1

    def __post_init__(self) -> None:
        for value, field in (
            (self.selection_manifest_id, "evaluation selection ID"),
            (self.seal_id, "evaluation seal ID"),
            (self.opened_marker_id, "evaluation opened marker ID"),
            (self.evaluation_id, "holdout evaluation ID"),
        ):
            _require_id(value, field)
        if not self.questions or len({item.question_id for item in self.questions}) != len(
            self.questions
        ):
            raise ValueError("holdout evaluation must cover unique frozen questions")
        if not self.holdout_outcomes_accessed:
            raise ValueError("holdout evaluation must record outcome access")
        if self.evaluation_id != _semantic_id(self.semantic_json()):
            raise ValueError("holdout evaluation ID does not authenticate its content")

    @classmethod
    def create(cls, **values: object) -> R2HoldoutEvaluation:
        raw = dict(values)
        raw.pop("evaluation_id", None)
        raw.setdefault("holdout_outcomes_accessed", True)
        raw["questions"] = tuple(cast(Sequence[R2HoldoutQuestionResult], raw["questions"]))
        semantic: dict[str, JsonValue] = {
            "contract": cls.CONTRACT,
            "schema_version": 1,
            **{key: _contract_json(value) for key, value in raw.items()},
        }
        constructor = cast(Callable[..., R2HoldoutEvaluation], cls)
        return constructor(**raw, evaluation_id=_semantic_id(semantic))

    def semantic_json(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": self.SCHEMA_VERSION,
            "selection_manifest_id": self.selection_manifest_id,
            "seal_id": self.seal_id,
            "opened_marker_id": self.opened_marker_id,
            "questions": [item.as_json() for item in self.questions],
            "source_class": self.source_class.value,
            "evidence_class": self.evidence_class.value,
            "holdout_scope": self.holdout_scope.value,
            "holdout_outcomes_accessed": self.holdout_outcomes_accessed,
        }

    def as_json(self) -> dict[str, JsonValue]:
        return {**self.semantic_json(), "evaluation_id": self.evaluation_id}


@dataclass(frozen=True, slots=True)
class R2HoldoutBundle:
    selection: ArtifactReference
    forecast_seal: ArtifactReference
    opened_marker: ArtifactReference
    consumed_marker: ArtifactReference
    evaluation: ArtifactReference
    replay_evidence: tuple[ArtifactReference, ...]
    source_class: MarketDataSourceClass
    evidence_class: EvidenceClass
    holdout_scope: HoldoutScope
    bundle_id: str

    CONTRACT: ClassVar[str] = R2_HOLDOUT_BUNDLE_CONTRACT
    SCHEMA_VERSION: ClassVar[int] = 1

    def __post_init__(self) -> None:
        refs = (
            self.selection,
            self.forecast_seal,
            self.opened_marker,
            self.consumed_marker,
            self.evaluation,
            *self.replay_evidence,
        )
        if len({ref.path for ref in refs}) != len(refs):
            raise ValueError("holdout bundle child paths must be unique")
        if any(ref.path == "manifest.json" for ref in refs):
            raise ValueError("holdout bundle child path manifest.json is reserved")
        if self.bundle_id != _semantic_id(self.semantic_json()):
            raise ValueError("holdout bundle ID does not authenticate its content")

    @classmethod
    def create(cls, **values: object) -> R2HoldoutBundle:
        raw = dict(values)
        raw.pop("bundle_id", None)
        raw["replay_evidence"] = tuple(
            sorted(
                cast(Sequence[ArtifactReference], raw["replay_evidence"]),
                key=lambda item: item.path,
            )
        )
        semantic: dict[str, JsonValue] = {
            "contract": cls.CONTRACT,
            "schema_version": 1,
            **{key: _contract_json(value) for key, value in raw.items()},
        }
        constructor = cast(Callable[..., R2HoldoutBundle], cls)
        return constructor(**raw, bundle_id=_semantic_id(semantic))

    def semantic_json(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": self.SCHEMA_VERSION,
            "selection": self.selection.as_json(),
            "forecast_seal": self.forecast_seal.as_json(),
            "opened_marker": self.opened_marker.as_json(),
            "consumed_marker": self.consumed_marker.as_json(),
            "evaluation": self.evaluation.as_json(),
            "replay_evidence": [item.as_json() for item in self.replay_evidence],
            "source_class": self.source_class.value,
            "evidence_class": self.evidence_class.value,
            "holdout_scope": self.holdout_scope.value,
        }

    def as_json(self) -> dict[str, JsonValue]:
        return {**self.semantic_json(), "bundle_id": self.bundle_id}
