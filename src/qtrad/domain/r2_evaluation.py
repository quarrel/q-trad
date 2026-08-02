"""Immutable R2.F1 evaluation and pre-holdout selection contracts."""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from math import isfinite
from typing import ClassVar

from qtrad.domain.events import JsonValue, to_json_value
from qtrad.domain.market_data import MarketDataSourceClass
from qtrad.domain.r2_readiness import EvidenceClass, ModelFamily
from qtrad.domain.time import require_utc

R2_LOCAL_COMPARATOR_CONTRACT = "qtrad-r2-local-comparator-v1"
R2_EVALUATION_CONTRACT = "qtrad-r2-evaluation-v1"
R2_SELECTION_CONTRACT = "qtrad-r2-selection-v1"
HOLDOUT_STATE_VERIFICATION_PENDING = "PENDING_R2_H_INTEGRATION"


class ComparisonSupport(StrEnum):
    OWN = "OWN_SUPPORT"
    COMMON = "COMMON_SUPPORT"


class MetricAvailability(StrEnum):
    DEFINED = "DEFINED"
    NOT_DEFINED = "NOT_DEFINED"


class ConfigurationDisposition(StrEnum):
    EVALUATED = "EVALUATED"
    RETAINED_CONTROL = "RETAINED_CONTROL"
    SELECTED_CANDIDATE = "SELECTED_CANDIDATE"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class MetricValue:
    availability: MetricAvailability
    value: float | None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.availability is MetricAvailability.DEFINED:
            if self.value is None or not isfinite(self.value) or self.reason is not None:
                raise ValueError("defined metrics require one finite value and no reason")
        elif self.value is not None or not self.reason:
            raise ValueError("undefined metrics require an explicit reason and no value")

    @classmethod
    def defined(cls, value: float) -> "MetricValue":
        return cls(MetricAvailability.DEFINED, value)

    @classmethod
    def not_defined(cls, reason: str) -> "MetricValue":
        return cls(MetricAvailability.NOT_DEFINED, None, reason)

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "availability": self.availability.value,
            "value": self.value,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class PredictiveMetrics:
    eligible_target_count: int
    forecast_count: int
    coverage: float
    mae: MetricValue
    rmse: MetricValue
    pearson: MetricValue
    spearman: MetricValue
    directional_accuracy: MetricValue
    exact_zero_excluded_count: int
    forecast_mean: MetricValue
    forecast_standard_deviation: MetricValue
    target_mean: MetricValue
    target_standard_deviation: MetricValue
    mean_forecast_error: MetricValue
    calibration_intercept: MetricValue
    calibration_slope: MetricValue

    def __post_init__(self) -> None:
        if not 0 <= self.forecast_count <= self.eligible_target_count:
            raise ValueError("metric counts are invalid")
        expected = (
            self.forecast_count / self.eligible_target_count if self.eligible_target_count else 0.0
        )
        if not isfinite(self.coverage) or abs(self.coverage - expected) > 1e-15:
            raise ValueError("metric coverage does not reconcile to counts")
        if not 0 <= self.exact_zero_excluded_count <= self.forecast_count:
            raise ValueError("directional exclusion count is invalid")

    def as_json(self) -> dict[str, JsonValue]:
        metric_values = (
            ("mae", self.mae),
            ("rmse", self.rmse),
            ("pearson", self.pearson),
            ("spearman", self.spearman),
            ("directional_accuracy", self.directional_accuracy),
            ("forecast_mean", self.forecast_mean),
            ("forecast_standard_deviation", self.forecast_standard_deviation),
            ("target_mean", self.target_mean),
            ("target_standard_deviation", self.target_standard_deviation),
            ("mean_forecast_error", self.mean_forecast_error),
            ("calibration_intercept", self.calibration_intercept),
            ("calibration_slope", self.calibration_slope),
        )
        return {
            "eligible_target_count": self.eligible_target_count,
            "forecast_count": self.forecast_count,
            "coverage": self.coverage,
            "exact_zero_excluded_count": self.exact_zero_excluded_count,
            **{name: value.as_json() for name, value in metric_values},
        }


@dataclass(frozen=True, slots=True)
class MetricSlice:
    model_family: ModelFamily
    comparator_model_family: ModelFamily | None
    support: ComparisonSupport
    breakdown: str
    bucket: str
    horizon: timedelta
    target_ids: tuple[str, ...]
    metrics: PredictiveMetrics

    def __post_init__(self) -> None:
        if not self.breakdown or not self.bucket or self.horizon <= timedelta(0):
            raise ValueError("metric slice scope must be non-empty and positive")
        if tuple(sorted(set(self.target_ids))) != self.target_ids:
            raise ValueError("metric slice target IDs must be unique and ordered")
        if len(self.target_ids) != self.metrics.eligible_target_count:
            raise ValueError("metric slice target IDs differ from eligible count")
        if self.support is ComparisonSupport.OWN and self.comparator_model_family is not None:
            raise ValueError("own-support metrics cannot name a comparator")
        if self.support is ComparisonSupport.COMMON and self.comparator_model_family is None:
            raise ValueError("common-support metrics require the pairwise comparator")

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "model_family": self.model_family.value,
            "comparator_model_family": (
                self.comparator_model_family.value
                if self.comparator_model_family is not None
                else None
            ),
            "support": self.support.value,
            "breakdown": self.breakdown,
            "bucket": self.bucket,
            "horizon_seconds": self.horizon.total_seconds(),
            "target_ids": list(self.target_ids),
            "metrics": self.metrics.as_json(),
        }


@dataclass(frozen=True, slots=True)
class TrainingBucketDefinition:
    model_family: ModelFamily
    outer_fold_id: str
    horizon: timedelta
    training_target_ids: tuple[str, ...]
    thresholds: tuple[float, ...]
    training_prediction_evidence_id: str
    bucket_definition_id: str

    def __post_init__(self) -> None:
        if not self.outer_fold_id or self.horizon <= timedelta(0) or not self.training_target_ids:
            raise ValueError("training bucket scope is invalid")
        if tuple(sorted(set(self.training_target_ids))) != self.training_target_ids:
            raise ValueError("bucket training target IDs must be unique and ordered")
        if tuple(sorted(set(self.thresholds))) != self.thresholds:
            raise ValueError("bucket thresholds must be unique and ordered")
        if any(not isfinite(value) for value in self.thresholds):
            raise ValueError("bucket thresholds must be finite")
        _require_sha256(self.training_prediction_evidence_id, "training-prediction evidence ID")
        if self.bucket_definition_id != semantic_id(self.semantic_json()):
            raise ValueError("bucket definition ID does not match its content")

    @classmethod
    def create(
        cls,
        *,
        model_family: ModelFamily,
        outer_fold_id: str,
        horizon: timedelta,
        training_target_ids: Sequence[str],
        thresholds: Sequence[float],
        training_prediction_evidence_id: str,
    ) -> "TrainingBucketDefinition":
        ids = tuple(sorted(training_target_ids))
        limits = tuple(sorted(set(thresholds)))
        payload = _bucket_json(
            model_family,
            outer_fold_id,
            horizon,
            ids,
            limits,
            training_prediction_evidence_id,
        )
        return cls(
            model_family,
            outer_fold_id,
            horizon,
            ids,
            limits,
            training_prediction_evidence_id,
            semantic_id(payload),
        )

    def semantic_json(self) -> dict[str, JsonValue]:
        return _bucket_json(
            self.model_family,
            self.outer_fold_id,
            self.horizon,
            self.training_target_ids,
            self.thresholds,
            self.training_prediction_evidence_id,
        )

    def as_json(self) -> dict[str, JsonValue]:
        return {**self.semantic_json(), "bucket_definition_id": self.bucket_definition_id}


@dataclass(frozen=True, slots=True)
class BucketMetrics:
    model_family: ModelFamily
    comparator_model_family: ModelFamily | None
    support: ComparisonSupport
    outer_fold_id: str
    bucket_definition_id: str
    bucket_index: int
    row_count: int
    instrument_count: int
    forecast_mean: MetricValue
    realised_mean: MetricValue

    def __post_init__(self) -> None:
        _require_sha256(self.bucket_definition_id, "bucket definition ID")
        if not self.outer_fold_id or self.bucket_index < 0 or self.row_count < 0:
            raise ValueError("forecast bucket scope is invalid")
        if not 0 <= self.instrument_count <= self.row_count:
            raise ValueError("forecast bucket instrument count is invalid")
        if self.support is ComparisonSupport.OWN and self.comparator_model_family is not None:
            raise ValueError("own-support buckets cannot name a comparator")
        if self.support is ComparisonSupport.COMMON and self.comparator_model_family is None:
            raise ValueError("common-support buckets require the pairwise comparator")

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "model_family": self.model_family.value,
            "comparator_model_family": (
                self.comparator_model_family.value
                if self.comparator_model_family is not None
                else None
            ),
            "support": self.support.value,
            "outer_fold_id": self.outer_fold_id,
            "bucket_definition_id": self.bucket_definition_id,
            "bucket_index": self.bucket_index,
            "row_count": self.row_count,
            "instrument_count": self.instrument_count,
            "forecast_mean": self.forecast_mean.as_json(),
            "realised_mean": self.realised_mean.as_json(),
        }


@dataclass(frozen=True, slots=True)
class BucketOrderingSummary:
    model_family: ModelFamily
    comparator_model_family: ModelFamily | None
    support: ComparisonSupport
    outer_fold_id: str
    bucket_definition_id: str
    bucket_order_spearman: MetricValue
    monotonicity_failure_count: int

    def __post_init__(self) -> None:
        _require_sha256(self.bucket_definition_id, "bucket definition ID")
        if not self.outer_fold_id or self.monotonicity_failure_count < 0:
            raise ValueError("bucket-ordering scope is invalid")

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "model_family": self.model_family.value,
            "comparator_model_family": (
                self.comparator_model_family.value
                if self.comparator_model_family is not None
                else None
            ),
            "support": self.support.value,
            "outer_fold_id": self.outer_fold_id,
            "bucket_definition_id": self.bucket_definition_id,
            "bucket_order_spearman": self.bucket_order_spearman.as_json(),
            "monotonicity_failure_count": self.monotonicity_failure_count,
        }


@dataclass(frozen=True, slots=True)
class StabilitySummary:
    candidate: ModelFamily
    comparator: ModelFamily
    fold_mse_deltas: tuple[tuple[str, float], ...]
    instrument_mse_deltas: tuple[tuple[str, float], ...]
    improving_fold_proportion: MetricValue
    improving_instrument_proportion: MetricValue
    best_instrument_contribution: MetricValue
    best_period_contribution: MetricValue
    fold_delta_median: MetricValue
    fold_delta_range: MetricValue
    fold_delta_dispersion: MetricValue
    forecast_scale_stability: MetricValue

    def __post_init__(self) -> None:
        for values in (self.fold_mse_deltas, self.instrument_mse_deltas):
            keys = tuple(key for key, _ in values)
            if tuple(sorted(set(keys))) != keys:
                raise ValueError("stability keys must be unique and ordered")
            if any(not isfinite(value) for _, value in values):
                raise ValueError("stability deltas must be finite")

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "candidate": self.candidate.value,
            "comparator": self.comparator.value,
            "fold_mse_deltas": [[key, value] for key, value in self.fold_mse_deltas],
            "instrument_mse_deltas": [[key, value] for key, value in self.instrument_mse_deltas],
            "improving_fold_proportion": self.improving_fold_proportion.as_json(),
            "improving_instrument_proportion": self.improving_instrument_proportion.as_json(),
            "best_instrument_contribution": self.best_instrument_contribution.as_json(),
            "best_period_contribution": self.best_period_contribution.as_json(),
            "fold_delta_median": self.fold_delta_median.as_json(),
            "fold_delta_range": self.fold_delta_range.as_json(),
            "fold_delta_dispersion": self.fold_delta_dispersion.as_json(),
            "forecast_scale_stability": self.forecast_scale_stability.as_json(),
        }


@dataclass(frozen=True, slots=True)
class EvaluatedModelManifest:
    model_family: ModelFamily
    feature_set_id: str | None
    forecast_dataset_id: str | None
    feature_dataset_id: str | None
    coverage_dataset_ids: tuple[str, ...]
    fold_fit_ids: tuple[str, ...]
    expected_fold_target_keys: tuple[tuple[str, str], ...]
    training_prediction_evidence_ids: tuple[str, ...]
    coefficient_stability_summary_id: str | None
    manifest_id: str
    market_data_source_class: MarketDataSourceClass = MarketDataSourceClass.IG_NATIVE_CAPTURE

    def __post_init__(self) -> None:
        optional_ids = (
            self.feature_set_id,
            self.forecast_dataset_id,
            self.feature_dataset_id,
            self.coefficient_stability_summary_id,
        )
        for value in optional_ids:
            if value is not None:
                _require_sha256(value, "evaluated-model child ID")
        for group in (
            self.coverage_dataset_ids,
            self.fold_fit_ids,
            self.training_prediction_evidence_ids,
        ):
            if tuple(sorted(set(group))) != group:
                raise ValueError("evaluated-model child IDs must be unique and ordered")
            for value in group:
                _require_sha256(value, "evaluated-model child ID")
        if tuple(sorted(set(self.expected_fold_target_keys))) != self.expected_fold_target_keys:
            raise ValueError("evaluated-model expected keys must be unique and ordered")
        if self.model_family is ModelFamily.ZERO_RETURN:
            if any(
                (
                    self.feature_set_id,
                    self.forecast_dataset_id,
                    self.feature_dataset_id,
                    self.coverage_dataset_ids,
                    self.fold_fit_ids,
                    self.training_prediction_evidence_ids,
                    self.coefficient_stability_summary_id,
                )
            ):
                raise ValueError("zero comparator cannot reference fitted-model evidence")
        elif not all(
            (
                self.feature_set_id,
                self.forecast_dataset_id,
                self.feature_dataset_id,
                self.coverage_dataset_ids,
                self.fold_fit_ids,
                self.expected_fold_target_keys,
                self.coefficient_stability_summary_id,
            )
        ):
            raise ValueError("fitted evaluated models require complete child evidence")
        _require_sha256(self.manifest_id, "evaluated-model manifest ID")
        if self.manifest_id != semantic_id(self.semantic_json()):
            raise ValueError("evaluated-model manifest ID does not authenticate its content")

    @classmethod
    def create(
        cls,
        *,
        model_family: ModelFamily,
        feature_set_id: str | None,
        forecast_dataset_id: str | None,
        feature_dataset_id: str | None,
        coverage_dataset_ids: Sequence[str],
        fold_fit_ids: Sequence[str],
        expected_fold_target_keys: Sequence[tuple[str, str]],
        training_prediction_evidence_ids: Sequence[str],
        coefficient_stability_summary_id: str | None,
        market_data_source_class: MarketDataSourceClass = MarketDataSourceClass.IG_NATIVE_CAPTURE,
    ) -> "EvaluatedModelManifest":
        coverage_ids = tuple(coverage_dataset_ids)
        fit_ids = tuple(fold_fit_ids)
        expected_keys = tuple(expected_fold_target_keys)
        training_ids = tuple(training_prediction_evidence_ids)
        provisional = object.__new__(cls)
        for field, value in (
            ("model_family", model_family),
            ("feature_set_id", feature_set_id),
            ("forecast_dataset_id", forecast_dataset_id),
            ("feature_dataset_id", feature_dataset_id),
            ("coverage_dataset_ids", coverage_ids),
            ("fold_fit_ids", fit_ids),
            ("expected_fold_target_keys", expected_keys),
            ("training_prediction_evidence_ids", training_ids),
            ("coefficient_stability_summary_id", coefficient_stability_summary_id),
            ("market_data_source_class", market_data_source_class),
        ):
            object.__setattr__(provisional, field, value)
        identity = semantic_id(provisional.semantic_json())
        return cls(
            model_family=model_family,
            feature_set_id=feature_set_id,
            forecast_dataset_id=forecast_dataset_id,
            feature_dataset_id=feature_dataset_id,
            coverage_dataset_ids=coverage_ids,
            fold_fit_ids=fit_ids,
            expected_fold_target_keys=expected_keys,
            training_prediction_evidence_ids=training_ids,
            coefficient_stability_summary_id=coefficient_stability_summary_id,
            manifest_id=identity,
            market_data_source_class=market_data_source_class,
        )

    def semantic_json(self) -> dict[str, JsonValue]:
        return {
            "contract": R2_EVALUATION_CONTRACT,
            "schema_version": 1,
            "model_family": self.model_family.value,
            "feature_set_id": self.feature_set_id,
            "forecast_dataset_id": self.forecast_dataset_id,
            "feature_dataset_id": self.feature_dataset_id,
            "coverage_dataset_ids": list(self.coverage_dataset_ids),
            "fold_fit_ids": list(self.fold_fit_ids),
            "expected_fold_target_keys": [
                [fold_id, target_id] for fold_id, target_id in self.expected_fold_target_keys
            ],
            "training_prediction_evidence_ids": list(self.training_prediction_evidence_ids),
            "coefficient_stability_summary_id": self.coefficient_stability_summary_id,
            "market_data_source_class": self.market_data_source_class.value,
        }

    def as_json(self) -> dict[str, JsonValue]:
        return {**self.semantic_json(), "manifest_id": self.manifest_id}


@dataclass(frozen=True, slots=True)
class PairwiseComparison:
    candidate: ModelFamily
    comparator: ModelFamily
    candidate_own_target_ids: tuple[str, ...]
    comparator_own_target_ids: tuple[str, ...]
    common_expected_target_ids: tuple[str, ...]
    common_target_ids: tuple[str, ...]
    common_support_coverage: MetricValue
    candidate_instrument_balanced_mse: MetricValue
    comparator_instrument_balanced_mse: MetricValue

    def __post_init__(self) -> None:
        for values in (
            self.candidate_own_target_ids,
            self.comparator_own_target_ids,
            self.common_expected_target_ids,
            self.common_target_ids,
        ):
            if tuple(sorted(set(values))) != values:
                raise ValueError("comparison support must be unique and ordered")
        expected_common = tuple(
            sorted(set(self.candidate_own_target_ids) & set(self.comparator_own_target_ids))
        )
        if self.common_expected_target_ids != expected_common:
            raise ValueError(
                "pairwise common-support denominator differs from expected opportunities"
            )
        if not set(self.common_target_ids) <= set(expected_common):
            raise ValueError("pairwise common forecast support exceeds expected common support")
        expected_coverage = (
            MetricValue.defined(len(self.common_target_ids) / len(expected_common))
            if expected_common
            else MetricValue.not_defined("pairwise comparison has no common expected opportunities")
        )
        if self.common_support_coverage != expected_coverage:
            raise ValueError("pairwise common-support coverage does not reconcile")

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "candidate": self.candidate.value,
            "comparator": self.comparator.value,
            "candidate_own_target_ids": list(self.candidate_own_target_ids),
            "comparator_own_target_ids": list(self.comparator_own_target_ids),
            "common_expected_target_ids": list(self.common_expected_target_ids),
            "common_target_ids": list(self.common_target_ids),
            "common_support_coverage": self.common_support_coverage.as_json(),
            "candidate_instrument_balanced_mse": (self.candidate_instrument_balanced_mse.as_json()),
            "comparator_instrument_balanced_mse": (
                self.comparator_instrument_balanced_mse.as_json()
            ),
        }


@dataclass(frozen=True, slots=True)
class ConfigurationRecord:
    configuration_id: str
    model_family: ModelFamily
    feature_set_id: str | None
    disposition: ConfigurationDisposition
    reason: str
    forecast_dataset_id: str | None
    evaluated_model_manifest_id: str | None
    market_data_source_class: MarketDataSourceClass = MarketDataSourceClass.IG_NATIVE_CAPTURE

    def __post_init__(self) -> None:
        _require_sha256(self.configuration_id, "evaluated configuration ID")
        if self.evaluated_model_manifest_id is not None:
            _require_sha256(self.evaluated_model_manifest_id, "evaluated-model manifest ID")
        if self.feature_set_id is not None:
            _require_sha256(self.feature_set_id, "evaluated feature-set ID")
        if self.forecast_dataset_id is not None:
            _require_sha256(self.forecast_dataset_id, "evaluated forecast dataset ID")
        if not self.reason:
            raise ValueError("configuration disposition requires a reason")
        if self.disposition not in (
            ConfigurationDisposition.EVALUATED,
            ConfigurationDisposition.FAILED,
        ):
            raise ValueError("evaluation records cannot assert selection dispositions")

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "configuration_id": self.configuration_id,
            "model_family": self.model_family.value,
            "feature_set_id": self.feature_set_id,
            "disposition": self.disposition.value,
            "reason": self.reason,
            "forecast_dataset_id": self.forecast_dataset_id,
            "evaluated_model_manifest_id": self.evaluated_model_manifest_id,
            "market_data_source_class": self.market_data_source_class.value,
        }


@dataclass(frozen=True, slots=True)
class LocalComparatorManifest:
    experiment_configuration_id: str
    target_dataset_id: str
    fold_dataset_id: str
    feature_set_id: str
    forecast_dataset_id: str
    coverage_dataset_ids: tuple[str, ...]
    fold_fit_ids: tuple[str, ...]
    coverage_rows: tuple[tuple[str, str, str, str | None], ...]
    evidence_class: EvidenceClass
    manifest_id: str
    market_data_source_class: MarketDataSourceClass = MarketDataSourceClass.IG_NATIVE_CAPTURE

    CONTRACT: ClassVar[str] = R2_LOCAL_COMPARATOR_CONTRACT

    def __post_init__(self) -> None:
        for value, field in (
            (self.experiment_configuration_id, "comparator experiment ID"),
            (self.target_dataset_id, "comparator target dataset ID"),
            (self.fold_dataset_id, "comparator fold dataset ID"),
            (self.feature_set_id, "comparator feature-set ID"),
            (self.forecast_dataset_id, "comparator forecast dataset ID"),
            (self.manifest_id, "local-comparator manifest ID"),
        ):
            _require_sha256(value, field)
        for group in (self.coverage_dataset_ids, self.fold_fit_ids):
            if not group or tuple(sorted(set(group))) != group:
                raise ValueError("local-comparator child IDs must be non-empty, unique and ordered")
            for value in group:
                _require_sha256(value, "local-comparator child ID")
        if not self.coverage_rows or tuple(sorted(set(self.coverage_rows))) != self.coverage_rows:
            raise ValueError("local-comparator coverage rows must be non-empty, unique and ordered")
        if self.manifest_id != semantic_id(self.semantic_json()):
            raise ValueError("local-comparator manifest ID does not authenticate its content")

    @classmethod
    def create(
        cls,
        *,
        experiment_configuration_id: str,
        target_dataset_id: str,
        fold_dataset_id: str,
        feature_set_id: str,
        forecast_dataset_id: str,
        coverage_dataset_ids: Sequence[str],
        fold_fit_ids: Sequence[str],
        coverage_rows: Sequence[tuple[str, str, str, str | None]],
        evidence_class: EvidenceClass,
        market_data_source_class: MarketDataSourceClass = MarketDataSourceClass.IG_NATIVE_CAPTURE,
    ) -> "LocalComparatorManifest":
        coverage_ids = tuple(sorted(coverage_dataset_ids))
        fit_ids = tuple(sorted(fold_fit_ids))
        rows = tuple(sorted(coverage_rows))
        payload = _local_comparator_json(
            experiment_configuration_id,
            target_dataset_id,
            fold_dataset_id,
            feature_set_id,
            forecast_dataset_id,
            coverage_ids,
            fit_ids,
            rows,
            evidence_class,
            market_data_source_class,
        )
        return cls(
            experiment_configuration_id,
            target_dataset_id,
            fold_dataset_id,
            feature_set_id,
            forecast_dataset_id,
            coverage_ids,
            fit_ids,
            rows,
            evidence_class,
            semantic_id(payload),
            market_data_source_class,
        )

    def semantic_json(self) -> dict[str, JsonValue]:
        return _local_comparator_json(
            self.experiment_configuration_id,
            self.target_dataset_id,
            self.fold_dataset_id,
            self.feature_set_id,
            self.forecast_dataset_id,
            self.coverage_dataset_ids,
            self.fold_fit_ids,
            self.coverage_rows,
            self.evidence_class,
            self.market_data_source_class,
        )

    def as_json(self) -> dict[str, JsonValue]:
        return {**self.semantic_json(), "manifest_id": self.manifest_id}


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    experiment_configuration_id: str
    target_dataset_id: str
    fold_dataset_id: str
    local_comparator_manifest_id: str
    evidence_class: EvidenceClass
    metric_policy: str
    forecast_bucket_policy: str
    minimum_correlation_rows: int
    forecast_bucket_count: int
    all_model_common_target_ids: tuple[str, ...]
    evaluated_models: tuple[EvaluatedModelManifest, ...]
    comparisons: tuple[PairwiseComparison, ...]
    metric_slices: tuple[MetricSlice, ...]
    bucket_definitions: tuple[TrainingBucketDefinition, ...]
    bucket_metrics: tuple[BucketMetrics, ...]
    bucket_ordering: tuple[BucketOrderingSummary, ...]
    stability: tuple[StabilitySummary, ...]
    configurations: tuple[ConfigurationRecord, ...]
    unavailable_diagnostics: tuple[tuple[str, str], ...]
    report_id: str
    market_data_source_class: MarketDataSourceClass = MarketDataSourceClass.IG_NATIVE_CAPTURE

    CONTRACT: ClassVar[str] = R2_EVALUATION_CONTRACT

    def __post_init__(self) -> None:
        for value, field in (
            (self.experiment_configuration_id, "evaluation experiment ID"),
            (self.target_dataset_id, "evaluation target dataset ID"),
            (self.fold_dataset_id, "evaluation fold dataset ID"),
            (self.local_comparator_manifest_id, "evaluation local-comparator ID"),
            (self.report_id, "evaluation report ID"),
        ):
            _require_sha256(value, field)
        if (
            not self.metric_policy
            or not self.forecast_bucket_policy
            or self.minimum_correlation_rows < 2
            or self.forecast_bucket_count < 2
        ):
            raise ValueError("evaluation policies must be complete and valid")
        if tuple(sorted(set(self.all_model_common_target_ids))) != self.all_model_common_target_ids:
            raise ValueError("all-model common support must be unique and ordered")
        if not self.metric_slices or not self.configurations or not self.comparisons:
            raise ValueError("evaluation requires metrics, comparisons and configurations")
        model_ids = tuple(item.manifest_id for item in self.evaluated_models)
        if tuple(sorted(set(model_ids))) != model_ids:
            raise ValueError("evaluated-model manifests must be unique and ordered")
        config_ids = tuple(item.configuration_id for item in self.configurations)
        if tuple(sorted(set(config_ids))) != config_ids:
            raise ValueError("configuration register must be unique and ordered")
        if {item.evaluated_model_manifest_id for item in self.configurations} != set(model_ids):
            raise ValueError("configuration register differs from evaluated-model evidence")
        if tuple(sorted(set(self.unavailable_diagnostics))) != self.unavailable_diagnostics:
            raise ValueError("unavailable diagnostics must be unique and ordered")
        if any(not name or not reason for name, reason in self.unavailable_diagnostics):
            raise ValueError("unavailable diagnostics require names and reasons")
        if self.report_id != semantic_id(self.semantic_json()):
            raise ValueError("evaluation report ID does not authenticate its content")

    @classmethod
    def create(
        cls,
        *,
        experiment_configuration_id: str,
        target_dataset_id: str,
        fold_dataset_id: str,
        local_comparator_manifest_id: str,
        evidence_class: EvidenceClass,
        metric_policy: str,
        forecast_bucket_policy: str,
        minimum_correlation_rows: int,
        forecast_bucket_count: int,
        all_model_common_target_ids: Sequence[str],
        evaluated_models: Sequence[EvaluatedModelManifest],
        comparisons: Sequence[PairwiseComparison],
        metric_slices: Sequence[MetricSlice],
        bucket_definitions: Sequence[TrainingBucketDefinition],
        bucket_metrics: Sequence[BucketMetrics],
        bucket_ordering: Sequence[BucketOrderingSummary],
        stability: Sequence[StabilitySummary],
        configurations: Sequence[ConfigurationRecord],
        unavailable_diagnostics: Sequence[tuple[str, str]],
        market_data_source_class: MarketDataSourceClass = MarketDataSourceClass.IG_NATIVE_CAPTURE,
    ) -> "EvaluationReport":
        common_ids = tuple(sorted(all_model_common_target_ids))
        ordered_models = tuple(sorted(evaluated_models, key=lambda item: item.manifest_id))
        ordered_configurations = tuple(
            sorted(configurations, key=lambda item: item.configuration_id)
        )
        diagnostics = tuple(sorted(unavailable_diagnostics))
        provisional = object.__new__(cls)
        for field, value in (
            ("experiment_configuration_id", experiment_configuration_id),
            ("target_dataset_id", target_dataset_id),
            ("fold_dataset_id", fold_dataset_id),
            ("local_comparator_manifest_id", local_comparator_manifest_id),
            ("evidence_class", evidence_class),
            ("metric_policy", metric_policy),
            ("forecast_bucket_policy", forecast_bucket_policy),
            ("minimum_correlation_rows", minimum_correlation_rows),
            ("forecast_bucket_count", forecast_bucket_count),
            ("all_model_common_target_ids", common_ids),
            ("evaluated_models", ordered_models),
            ("comparisons", tuple(comparisons)),
            ("metric_slices", tuple(metric_slices)),
            ("bucket_definitions", tuple(bucket_definitions)),
            ("bucket_metrics", tuple(bucket_metrics)),
            ("bucket_ordering", tuple(bucket_ordering)),
            ("stability", tuple(stability)),
            ("configurations", ordered_configurations),
            ("unavailable_diagnostics", diagnostics),
            ("market_data_source_class", market_data_source_class),
        ):
            object.__setattr__(provisional, field, value)
        identity = semantic_id(provisional.semantic_json())
        return cls(
            experiment_configuration_id=experiment_configuration_id,
            target_dataset_id=target_dataset_id,
            fold_dataset_id=fold_dataset_id,
            local_comparator_manifest_id=local_comparator_manifest_id,
            evidence_class=evidence_class,
            metric_policy=metric_policy,
            forecast_bucket_policy=forecast_bucket_policy,
            minimum_correlation_rows=minimum_correlation_rows,
            forecast_bucket_count=forecast_bucket_count,
            all_model_common_target_ids=common_ids,
            evaluated_models=ordered_models,
            comparisons=tuple(comparisons),
            metric_slices=tuple(metric_slices),
            bucket_definitions=tuple(bucket_definitions),
            bucket_metrics=tuple(bucket_metrics),
            bucket_ordering=tuple(bucket_ordering),
            stability=tuple(stability),
            configurations=ordered_configurations,
            unavailable_diagnostics=diagnostics,
            report_id=identity,
            market_data_source_class=market_data_source_class,
        )

    def semantic_json(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": 1,
            "experiment_configuration_id": self.experiment_configuration_id,
            "target_dataset_id": self.target_dataset_id,
            "fold_dataset_id": self.fold_dataset_id,
            "local_comparator_manifest_id": self.local_comparator_manifest_id,
            "evidence_class": self.evidence_class.value,
            "metric_policy": self.metric_policy,
            "forecast_bucket_policy": self.forecast_bucket_policy,
            "minimum_correlation_rows": self.minimum_correlation_rows,
            "forecast_bucket_count": self.forecast_bucket_count,
            "all_model_common_target_ids": list(self.all_model_common_target_ids),
            "evaluated_models": [item.as_json() for item in self.evaluated_models],
            "comparisons": [item.as_json() for item in self.comparisons],
            "metric_slices": [item.as_json() for item in self.metric_slices],
            "bucket_definitions": [item.as_json() for item in self.bucket_definitions],
            "bucket_metrics": [item.as_json() for item in self.bucket_metrics],
            "bucket_ordering": [item.as_json() for item in self.bucket_ordering],
            "stability": [item.as_json() for item in self.stability],
            "configurations": [item.as_json() for item in self.configurations],
            "configuration_count": len(self.configurations),
            "unavailable_diagnostics": [
                {"name": name, "reason": reason} for name, reason in self.unavailable_diagnostics
            ],
            "market_data_source_class": self.market_data_source_class.value,
        }

    def as_json(self) -> dict[str, JsonValue]:
        return {**self.semantic_json(), "report_id": self.report_id}


@dataclass(frozen=True, slots=True)
class SelectionGateOutcome:
    configuration_id: str
    name: str
    passed: bool
    observed: MetricValue
    threshold: MetricValue
    reason: str

    def __post_init__(self) -> None:
        _require_sha256(self.configuration_id, "selection-gate configuration ID")
        if not self.name or not self.reason:
            raise ValueError("selection gate requires a name and reason")

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "configuration_id": self.configuration_id,
            "name": self.name,
            "passed": self.passed,
            "observed": self.observed.as_json(),
            "threshold": self.threshold.as_json(),
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class SelectionDecision:
    configuration_id: str
    disposition: ConfigurationDisposition
    reason: str
    gates: tuple[SelectionGateOutcome, ...]

    def __post_init__(self) -> None:
        _require_sha256(self.configuration_id, "selection-decision configuration ID")
        if not self.reason:
            raise ValueError("selection decision requires a reason")
        if any(gate.configuration_id != self.configuration_id for gate in self.gates):
            raise ValueError("selection gates differ from their configuration")

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "configuration_id": self.configuration_id,
            "disposition": self.disposition.value,
            "reason": self.reason,
            "gates": [gate.as_json() for gate in self.gates],
        }


@dataclass(frozen=True, slots=True)
class SelectionManifest:
    experiment_configuration_id: str
    evidence_class: EvidenceClass
    evaluation_report_id: str
    local_comparator_manifest_id: str
    evaluated_configuration_ids: tuple[str, ...]
    predeclared_comparators: tuple[ModelFamily, ...]
    primary_metric: str
    secondary_metrics: tuple[str, ...]
    acceptance_thresholds: tuple[tuple[str, float], ...]
    decisions: tuple[SelectionDecision, ...]
    selected_configuration_ids: tuple[str, ...]
    holdout_comparator_configuration_ids: tuple[str, ...]
    final_fitting_procedure: str
    holdout_range: tuple[datetime, datetime]
    holdout_state_verification: str
    application_image_identity: str
    frozen_at: datetime
    frozen_by: str
    manifest_id: str
    market_data_source_class: MarketDataSourceClass | None = None
    foundation_bundle_id: str | None = None
    oof_bundle_id: str | None = None

    CONTRACT: ClassVar[str] = R2_SELECTION_CONTRACT

    def __post_init__(self) -> None:
        identity_values = [
            (self.experiment_configuration_id, "selection experiment ID"),
            (self.evaluation_report_id, "selection evaluation ID"),
            (self.local_comparator_manifest_id, "selection local-comparator ID"),
            (self.manifest_id, "selection manifest ID"),
        ]
        if self.foundation_bundle_id is not None and self.oof_bundle_id is not None:
            identity_values.extend(
                (
                    (self.foundation_bundle_id, "selection foundation ID"),
                    (self.oof_bundle_id, "selection OOF ID"),
                )
            )
        for value, field in identity_values:
            _require_sha256(value, field)
        for values in (
            self.evaluated_configuration_ids,
            self.selected_configuration_ids,
            self.holdout_comparator_configuration_ids,
        ):
            if tuple(sorted(set(values))) != values:
                raise ValueError("selection configuration IDs must be unique and ordered")
            for value in values:
                _require_sha256(value, "selection configuration ID")
        evaluated = set(self.evaluated_configuration_ids)
        if not evaluated:
            raise ValueError("selection must retain every evaluated configuration")
        if not set(self.selected_configuration_ids) <= evaluated:
            raise ValueError("selected configurations were not evaluated")
        if not set(self.holdout_comparator_configuration_ids) <= evaluated:
            raise ValueError("holdout comparators were not evaluated")
        if tuple(item.configuration_id for item in self.decisions) != tuple(sorted(evaluated)):
            raise ValueError("selection decisions must exactly cover evaluated configurations")
        expected_selected = tuple(
            item.configuration_id
            for item in self.decisions
            if item.disposition is ConfigurationDisposition.SELECTED_CANDIDATE
        )
        if expected_selected != self.selected_configuration_ids:
            raise ValueError("selected IDs differ from verified selection decisions")
        expected_comparators = tuple(
            item.configuration_id
            for item in self.decisions
            if item.disposition
            in (
                ConfigurationDisposition.RETAINED_CONTROL,
                ConfigurationDisposition.SELECTED_CANDIDATE,
            )
        )
        if expected_comparators != self.holdout_comparator_configuration_ids:
            raise ValueError("holdout comparator IDs differ from verified selection decisions")
        if self.holdout_state_verification != HOLDOUT_STATE_VERIFICATION_PENDING:
            raise ValueError(
                "R2.F1 holdout-state verification must remain pending R2.H integration"
            )
        if len(set(self.predeclared_comparators)) != len(self.predeclared_comparators):
            raise ValueError("selection comparator set must be unique")
        if tuple(sorted(set(self.acceptance_thresholds))) != self.acceptance_thresholds or any(
            not name or not isfinite(value) for name, value in self.acceptance_thresholds
        ):
            raise ValueError("selection thresholds must be unique, ordered and finite")
        if not all(
            (
                self.primary_metric,
                self.secondary_metrics,
                self.final_fitting_procedure,
                self.application_image_identity,
                self.frozen_by,
            )
        ):
            raise ValueError("selection rationale and replay identity must be complete")
        require_utc(self.holdout_range[0], "selection holdout start")
        require_utc(self.holdout_range[1], "selection holdout end")
        require_utc(self.frozen_at, "selection freeze time")
        if self.holdout_range[1] <= self.holdout_range[0]:
            raise ValueError("selection holdout range must be positive")
        if self.manifest_id != semantic_id(self.semantic_json()):
            raise ValueError("selection manifest ID does not authenticate its content")

    @classmethod
    def create(
        cls,
        *,
        experiment_configuration_id: str,
        evidence_class: EvidenceClass,
        evaluation_report_id: str,
        local_comparator_manifest_id: str,
        evaluated_configuration_ids: Sequence[str],
        predeclared_comparators: Sequence[ModelFamily],
        primary_metric: str,
        secondary_metrics: Sequence[str],
        acceptance_thresholds: Sequence[tuple[str, float]],
        decisions: Sequence[SelectionDecision],
        selected_configuration_ids: Sequence[str],
        holdout_comparator_configuration_ids: Sequence[str],
        final_fitting_procedure: str,
        holdout_range: tuple[datetime, datetime],
        application_image_identity: str,
        frozen_at: datetime,
        frozen_by: str,
        market_data_source_class: MarketDataSourceClass | None = None,
        foundation_bundle_id: str | None = None,
        oof_bundle_id: str | None = None,
    ) -> "SelectionManifest":
        evaluated_ids = tuple(sorted(evaluated_configuration_ids))
        selected_ids = tuple(sorted(selected_configuration_ids))
        comparator_ids = tuple(sorted(holdout_comparator_configuration_ids))
        thresholds = tuple(sorted(acceptance_thresholds))
        ordered_decisions = tuple(sorted(decisions, key=lambda item: item.configuration_id))
        provisional = object.__new__(cls)
        for field, value in (
            ("experiment_configuration_id", experiment_configuration_id),
            ("evidence_class", evidence_class),
            ("evaluation_report_id", evaluation_report_id),
            ("local_comparator_manifest_id", local_comparator_manifest_id),
            ("evaluated_configuration_ids", evaluated_ids),
            ("predeclared_comparators", tuple(predeclared_comparators)),
            ("primary_metric", primary_metric),
            ("secondary_metrics", tuple(secondary_metrics)),
            ("acceptance_thresholds", thresholds),
            ("decisions", ordered_decisions),
            ("selected_configuration_ids", selected_ids),
            ("holdout_comparator_configuration_ids", comparator_ids),
            ("final_fitting_procedure", final_fitting_procedure),
            ("holdout_range", holdout_range),
            ("holdout_state_verification", HOLDOUT_STATE_VERIFICATION_PENDING),
            ("application_image_identity", application_image_identity),
            ("frozen_at", frozen_at),
            ("frozen_by", frozen_by),
            ("market_data_source_class", market_data_source_class),
            ("foundation_bundle_id", foundation_bundle_id),
            ("oof_bundle_id", oof_bundle_id),
        ):
            object.__setattr__(provisional, field, value)
        identity = semantic_id(provisional.semantic_json())
        return cls(
            experiment_configuration_id=experiment_configuration_id,
            evidence_class=evidence_class,
            evaluation_report_id=evaluation_report_id,
            local_comparator_manifest_id=local_comparator_manifest_id,
            evaluated_configuration_ids=evaluated_ids,
            predeclared_comparators=tuple(predeclared_comparators),
            primary_metric=primary_metric,
            secondary_metrics=tuple(secondary_metrics),
            acceptance_thresholds=thresholds,
            decisions=ordered_decisions,
            selected_configuration_ids=selected_ids,
            holdout_comparator_configuration_ids=comparator_ids,
            final_fitting_procedure=final_fitting_procedure,
            holdout_range=holdout_range,
            holdout_state_verification=HOLDOUT_STATE_VERIFICATION_PENDING,
            application_image_identity=application_image_identity,
            frozen_at=frozen_at,
            frozen_by=frozen_by,
            market_data_source_class=market_data_source_class,
            foundation_bundle_id=foundation_bundle_id,
            oof_bundle_id=oof_bundle_id,
            manifest_id=identity,
        )

    def semantic_json(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "contract": self.CONTRACT,
            "schema_version": 1,
            "experiment_configuration_id": self.experiment_configuration_id,
            "evidence_class": self.evidence_class.value,
            "evaluation_report_id": self.evaluation_report_id,
            "local_comparator_manifest_id": self.local_comparator_manifest_id,
            "evaluated_configuration_ids": list(self.evaluated_configuration_ids),
            "predeclared_comparators": [item.value for item in self.predeclared_comparators],
            "primary_metric": self.primary_metric,
            "secondary_metrics": list(self.secondary_metrics),
            "acceptance_thresholds": {name: value for name, value in self.acceptance_thresholds},
            "decisions": [item.as_json() for item in self.decisions],
            "selected_configuration_ids": list(self.selected_configuration_ids),
            "holdout_comparator_configuration_ids": list(self.holdout_comparator_configuration_ids),
            "final_fitting_procedure": self.final_fitting_procedure,
            "holdout_range": [item.isoformat() for item in self.holdout_range],
            "holdout_state_verification": self.holdout_state_verification,
            "application_image_identity": self.application_image_identity,
            "frozen_at": self.frozen_at.isoformat(),
            "frozen_by": self.frozen_by,
        }
        if self.market_data_source_class is not None:
            if self.foundation_bundle_id is None or self.oof_bundle_id is None:
                raise ValueError("source-bound selections require foundation and OOF IDs")
            payload.update(
                {
                    "source_class": self.market_data_source_class.value,
                    "foundation_bundle_id": self.foundation_bundle_id,
                    "oof_bundle_id": self.oof_bundle_id,
                }
            )
        elif self.foundation_bundle_id is not None or self.oof_bundle_id is not None:
            raise ValueError("selection lineage IDs require a source class")
        return payload

    def as_json(self) -> dict[str, JsonValue]:
        return {**self.semantic_json(), "manifest_id": self.manifest_id}


def _bucket_json(
    model_family: ModelFamily,
    outer_fold_id: str,
    horizon: timedelta,
    training_target_ids: Sequence[str],
    thresholds: Sequence[float],
    training_prediction_evidence_id: str,
) -> dict[str, JsonValue]:
    return {
        "contract": R2_EVALUATION_CONTRACT,
        "schema_version": 1,
        "model_family": model_family.value,
        "outer_fold_id": outer_fold_id,
        "horizon_seconds": horizon.total_seconds(),
        "training_target_ids": list(training_target_ids),
        "thresholds": list(thresholds),
        "training_prediction_evidence_id": training_prediction_evidence_id,
    }


def _local_comparator_json(
    experiment_configuration_id: str,
    target_dataset_id: str,
    fold_dataset_id: str,
    feature_set_id: str,
    forecast_dataset_id: str,
    coverage_dataset_ids: Sequence[str],
    fold_fit_ids: Sequence[str],
    coverage_rows: Sequence[tuple[str, str, str, str | None]],
    evidence_class: EvidenceClass,
    market_data_source_class: MarketDataSourceClass = MarketDataSourceClass.IG_NATIVE_CAPTURE,
) -> dict[str, JsonValue]:
    return {
        "contract": R2_LOCAL_COMPARATOR_CONTRACT,
        "schema_version": 1,
        "experiment_configuration_id": experiment_configuration_id,
        "target_dataset_id": target_dataset_id,
        "fold_dataset_id": fold_dataset_id,
        "feature_set_id": feature_set_id,
        "forecast_dataset_id": forecast_dataset_id,
        "coverage_dataset_ids": list(coverage_dataset_ids),
        "fold_fit_ids": list(fold_fit_ids),
        "coverage_rows": [
            {
                "target_id": target_id,
                "outer_fold_id": fold_id,
                "disposition": disposition,
                "forecast_id": forecast_id,
            }
            for target_id, fold_id, disposition, forecast_id in coverage_rows
        ],
        "evidence_class": evidence_class.value,
        "market_data_source_class": market_data_source_class.value,
    }


def semantic_id(payload: Mapping[str, object]) -> str:
    canonical = to_json_value(payload)
    return sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _require_sha256(value: str, field: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} must be a lowercase SHA-256 hex digest")
