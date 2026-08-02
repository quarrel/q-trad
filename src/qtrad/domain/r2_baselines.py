"""Immutable R2 Ridge fit, forecast-coverage and stability contracts."""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from math import isfinite
from typing import ClassVar, TypedDict, cast

from qtrad.domain.events import JsonValue, to_json_value
from qtrad.domain.market_data import MarketDataSourceClass
from qtrad.domain.r2_models import FitDisposition, PreprocessingFit
from qtrad.domain.r2_readiness import EvidenceClass, ModelFamily
from qtrad.domain.time import require_utc

R2_FOLD_FIT_CONTRACT = "qtrad-r2-fold-fit-v2"
R2_FORECAST_COVERAGE_CONTRACT = "qtrad-r2-forecast-coverage-v2"
R2_COEFFICIENT_STABILITY_CONTRACT = "qtrad-r2-coefficient-stability-v2"


class ForecastCoverageDisposition(StrEnum):
    FORECASTED = "FORECASTED"
    FEATURES_UNAVAILABLE = "FEATURES_UNAVAILABLE"
    INSUFFICIENT_TRAINING = "INSUFFICIENT_TRAINING"
    INSUFFICIENT_INNER_VALIDATION = "INSUFFICIENT_INNER_VALIDATION"
    DEGENERATE_TARGET = "DEGENERATE_TARGET"
    DEGENERATE_FEATURE_MATRIX = "DEGENERATE_FEATURE_MATRIX"
    NUMERICAL_FAILURE = "NUMERICAL_FAILURE"
    MODEL_NOT_ELIGIBLE = "MODEL_NOT_ELIGIBLE"


class _R2FoldFitArguments(TypedDict):
    r2_feature_dataset_id: str
    target_dataset_id: str
    fold_dataset_id: str
    experiment_configuration_id: str
    preprocessing_selection_id: str
    model_family: ModelFamily
    horizon: timedelta
    outer_fold_id: str
    outer_fold_membership_hash: str
    target_instrument_id: str
    feature_set_id: str
    feature_schema_id: str
    preprocessing_schema_id: str
    evidence_class: EvidenceClass
    market_data_source_class: MarketDataSourceClass
    application_image_identity: str
    numpy_library_identity: str
    sklearn_library_identity: str
    training_cutoff: datetime
    selected_alpha: float | None
    preprocessing: PreprocessingFit | None
    coefficient_feature_names: tuple[str, ...]
    intercept: float | None
    coefficients: tuple[float, ...]
    fit_row_count: int
    excluded_row_count: int
    outer_validation_opportunity_count: int
    fit_warnings: tuple[str, ...]
    disposition: FitDisposition
    failure: str | None
    diagnostics: "FoldFitDiagnostics | None"


class _ForecastCoverageArguments(TypedDict):
    target_id: str
    target_instrument_id: str
    decision_time: datetime
    horizon: timedelta
    outer_fold_id: str
    fold_fit_id: str
    feature_data_asof: datetime | None
    disposition: ForecastCoverageDisposition
    forecast_id: str | None
    reason: str | None
    market_data_source_class: MarketDataSourceClass


@dataclass(frozen=True, slots=True)
class FoldFitDiagnostics:
    iteration_count: int | None
    training_target_mean: float
    training_target_standard_deviation: float
    training_prediction_mse: float
    coefficient_l2_norm: float
    maximum_absolute_coefficient: float
    prediction_replay_maximum_absolute_error: float

    def __post_init__(self) -> None:
        if self.iteration_count is not None and self.iteration_count < 0:
            raise ValueError("fit iteration count cannot be negative")
        values = (
            self.training_target_mean,
            self.training_target_standard_deviation,
            self.training_prediction_mse,
            self.coefficient_l2_norm,
            self.maximum_absolute_coefficient,
            self.prediction_replay_maximum_absolute_error,
        )
        if any(not isfinite(value) for value in values):
            raise ValueError("fold-fit diagnostics must be finite")
        if any(value < 0 for value in values[1:]):
            raise ValueError("non-mean fold-fit diagnostics cannot be negative")

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "iteration_count": self.iteration_count,
            "training_target_mean": self.training_target_mean,
            "training_target_standard_deviation": self.training_target_standard_deviation,
            "training_prediction_mse": self.training_prediction_mse,
            "coefficient_l2_norm": self.coefficient_l2_norm,
            "maximum_absolute_coefficient": self.maximum_absolute_coefficient,
            "prediction_replay_maximum_absolute_error": (
                self.prediction_replay_maximum_absolute_error
            ),
        }


@dataclass(frozen=True, slots=True)
class R2FoldFit:
    """Canonical final Ridge state for one local target or declared target pool."""

    r2_feature_dataset_id: str
    target_dataset_id: str
    fold_dataset_id: str
    experiment_configuration_id: str
    preprocessing_selection_id: str
    model_family: ModelFamily
    horizon: timedelta
    outer_fold_id: str
    outer_fold_membership_hash: str
    target_instrument_id: str
    feature_set_id: str
    feature_schema_id: str
    preprocessing_schema_id: str
    evidence_class: EvidenceClass
    application_image_identity: str
    numpy_library_identity: str
    sklearn_library_identity: str
    training_cutoff: datetime
    selected_alpha: float | None
    preprocessing: PreprocessingFit | None
    coefficient_feature_names: tuple[str, ...]
    intercept: float | None
    coefficients: tuple[float, ...]
    fit_row_count: int
    excluded_row_count: int
    outer_validation_opportunity_count: int
    fit_warnings: tuple[str, ...]
    disposition: FitDisposition
    failure: str | None
    diagnostics: FoldFitDiagnostics | None
    artifact_id: str
    market_data_source_class: MarketDataSourceClass = MarketDataSourceClass.IG_NATIVE_CAPTURE

    CONTRACT: ClassVar[str] = R2_FOLD_FIT_CONTRACT
    SCHEMA_VERSION: ClassVar[int] = 1

    def __post_init__(self) -> None:
        for value, field in (
            (self.r2_feature_dataset_id, "feature dataset ID"),
            (self.target_dataset_id, "target dataset ID"),
            (self.fold_dataset_id, "fold dataset ID"),
            (self.experiment_configuration_id, "experiment configuration ID"),
            (self.preprocessing_selection_id, "preprocessing-selection ID"),
            (self.outer_fold_membership_hash, "outer membership hash"),
            (self.feature_set_id, "feature-set ID"),
            (self.feature_schema_id, "feature-schema ID"),
            (self.preprocessing_schema_id, "preprocessing-schema ID"),
            (self.artifact_id, "fold-fit artifact ID"),
        ):
            _require_sha256(value, field)
        require_utc(self.training_cutoff, "fold-fit training cutoff")
        if self.model_family not in (
            ModelFamily.LOCAL_RIDGE,
            ModelFamily.POOLED_LOCAL_RIDGE,
            ModelFamily.POOLED_CROSS_ASSET_RIDGE,
        ):
            raise ValueError("unsupported R2 Ridge fold-fit model family")
        if self.horizon <= timedelta(0):
            raise ValueError("fold-fit horizon must be positive")
        if not all(
            (
                self.outer_fold_id,
                self.target_instrument_id,
                self.application_image_identity,
                self.numpy_library_identity,
                self.sklearn_library_identity,
            )
        ):
            raise ValueError("fold-fit scope and replay identities must be non-empty")
        if (
            min(
                self.fit_row_count,
                self.excluded_row_count,
                self.outer_validation_opportunity_count,
            )
            < 0
        ):
            raise ValueError("fold-fit row counts cannot be negative")
        if len(set(self.fit_warnings)) != len(self.fit_warnings) or any(
            not warning for warning in self.fit_warnings
        ):
            raise ValueError("fit warnings must be unique and non-empty")
        if self.selected_alpha is not None and (
            not isfinite(self.selected_alpha) or self.selected_alpha <= 0
        ):
            raise ValueError("selected alpha must be finite and positive")
        if len(set(self.coefficient_feature_names)) != len(self.coefficient_feature_names):
            raise ValueError("coefficient feature names must be unique")
        if len(self.coefficients) != len(self.coefficient_feature_names) or any(
            not isfinite(value) for value in self.coefficients
        ):
            raise ValueError("coefficients must be finite and aligned with feature names")
        if self.disposition is FitDisposition.READY:
            if (
                self.selected_alpha is None
                or self.preprocessing is None
                or self.intercept is None
                or not isfinite(self.intercept)
                or self.diagnostics is None
                or self.failure is not None
                or self.fit_row_count == 0
            ):
                raise ValueError("ready fold fits require complete finite model evidence")
            local_names = self.preprocessing.active_feature_names
            if self.model_family is ModelFamily.LOCAL_RIDGE:
                if self.coefficient_feature_names != local_names:
                    raise ValueError("local coefficients must follow preprocessing feature order")
            else:
                identity_names = self.coefficient_feature_names[len(local_names) :]
                if (
                    self.target_instrument_id != "__POOLED__"
                    or self.coefficient_feature_names[: len(local_names)] != local_names
                    or len(identity_names) < 2
                    or any(not name.startswith("instrument_identity::") for name in identity_names)
                    or self.intercept != 0.0
                ):
                    raise ValueError(
                        "pooled coefficients require local features followed by fixed "
                        "instrument effects"
                    )
            if self.preprocessing.training_target_ids.__len__() != self.fit_row_count:
                raise ValueError("fold-fit row count differs from preprocessing membership")
        elif (
            self.intercept is not None
            or self.coefficients
            or self.coefficient_feature_names
            or self.diagnostics is not None
            or not self.failure
        ):
            raise ValueError("failed fold fits must retain only failure and pre-fit evidence")
        if self.preprocessing is not None:
            if self.preprocessing_schema_id == "":
                raise ValueError("preprocessing schema identity is required")
            if len(self.preprocessing.training_target_ids) != self.fit_row_count:
                raise ValueError("preprocessing membership differs from fold-fit rows")
        if self.artifact_id != fold_fit_id(self.semantic_json()):
            raise ValueError("fold-fit artifact ID does not match its semantic content")

    @classmethod
    def create(cls, **values: object) -> "R2FoldFit":
        arguments = cast(_R2FoldFitArguments, values)
        payload = _fold_fit_json(arguments)
        return cls(**arguments, artifact_id=fold_fit_id(payload))

    def semantic_json(self) -> dict[str, JsonValue]:
        return _fold_fit_json(
            _R2FoldFitArguments(
                r2_feature_dataset_id=self.r2_feature_dataset_id,
                target_dataset_id=self.target_dataset_id,
                fold_dataset_id=self.fold_dataset_id,
                experiment_configuration_id=self.experiment_configuration_id,
                preprocessing_selection_id=self.preprocessing_selection_id,
                model_family=self.model_family,
                horizon=self.horizon,
                outer_fold_id=self.outer_fold_id,
                outer_fold_membership_hash=self.outer_fold_membership_hash,
                target_instrument_id=self.target_instrument_id,
                feature_set_id=self.feature_set_id,
                feature_schema_id=self.feature_schema_id,
                preprocessing_schema_id=self.preprocessing_schema_id,
                evidence_class=self.evidence_class,
                market_data_source_class=self.market_data_source_class,
                application_image_identity=self.application_image_identity,
                numpy_library_identity=self.numpy_library_identity,
                sklearn_library_identity=self.sklearn_library_identity,
                training_cutoff=self.training_cutoff,
                selected_alpha=self.selected_alpha,
                preprocessing=self.preprocessing,
                coefficient_feature_names=self.coefficient_feature_names,
                intercept=self.intercept,
                coefficients=self.coefficients,
                fit_row_count=self.fit_row_count,
                excluded_row_count=self.excluded_row_count,
                outer_validation_opportunity_count=self.outer_validation_opportunity_count,
                fit_warnings=self.fit_warnings,
                disposition=self.disposition,
                failure=self.failure,
                diagnostics=self.diagnostics,
            )
        )

    def as_json(self) -> dict[str, JsonValue]:
        return {**self.semantic_json(), "artifact_id": self.artifact_id}


@dataclass(frozen=True, slots=True)
class ForecastCoverageRow:
    target_id: str
    target_instrument_id: str
    decision_time: datetime
    horizon: timedelta
    outer_fold_id: str
    fold_fit_id: str
    feature_data_asof: datetime | None
    disposition: ForecastCoverageDisposition
    forecast_id: str | None
    reason: str | None
    coverage_id: str
    market_data_source_class: MarketDataSourceClass = MarketDataSourceClass.IG_NATIVE_CAPTURE

    def __post_init__(self) -> None:
        require_utc(self.decision_time, "forecast-coverage decision time")
        if self.feature_data_asof is not None:
            require_utc(self.feature_data_asof, "forecast-coverage feature cutoff")
            if self.feature_data_asof > self.decision_time:
                raise ValueError("forecast-coverage feature cutoff follows the decision")
        _require_sha256(self.fold_fit_id, "coverage fold-fit ID")
        _require_sha256(self.coverage_id, "forecast-coverage row ID")
        if not self.target_id or not self.target_instrument_id or not self.outer_fold_id:
            raise ValueError("forecast-coverage scope must be non-empty")
        if self.horizon <= timedelta(0):
            raise ValueError("forecast-coverage horizon must be positive")
        if self.disposition is ForecastCoverageDisposition.FORECASTED:
            if (
                self.forecast_id is None
                or self.feature_data_asof is None
                or self.reason is not None
            ):
                raise ValueError("forecasted coverage requires forecast lineage and no failure")
            _require_sha256(self.forecast_id, "coverage forecast ID")
        elif self.forecast_id is not None or not self.reason:
            raise ValueError("unforecasted coverage requires an explicit reason and no forecast")
        if self.coverage_id != forecast_coverage_row_id(self.semantic_json()):
            raise ValueError("forecast-coverage row ID does not match its semantic content")

    @classmethod
    def create(cls, **values: object) -> "ForecastCoverageRow":
        arguments = cast(_ForecastCoverageArguments, values)
        payload = _coverage_row_json(arguments)
        return cls(**arguments, coverage_id=forecast_coverage_row_id(payload))

    def semantic_json(self) -> dict[str, JsonValue]:
        return _coverage_row_json(
            _ForecastCoverageArguments(
                target_id=self.target_id,
                target_instrument_id=self.target_instrument_id,
                decision_time=self.decision_time,
                horizon=self.horizon,
                outer_fold_id=self.outer_fold_id,
                fold_fit_id=self.fold_fit_id,
                feature_data_asof=self.feature_data_asof,
                disposition=self.disposition,
                forecast_id=self.forecast_id,
                reason=self.reason,
                market_data_source_class=self.market_data_source_class,
            )
        )

    def as_json(self) -> dict[str, JsonValue]:
        return {**self.semantic_json(), "coverage_id": self.coverage_id}


@dataclass(frozen=True, slots=True)
class ForecastCoverageDataset:
    rows: tuple[ForecastCoverageRow, ...]
    experiment_configuration_id: str
    target_dataset_id: str
    fold_dataset_id: str
    r2_feature_dataset_id: str
    dataset_id: str
    market_data_source_class: MarketDataSourceClass = MarketDataSourceClass.IG_NATIVE_CAPTURE

    CONTRACT: ClassVar[str] = R2_FORECAST_COVERAGE_CONTRACT

    def __post_init__(self) -> None:
        for value, field in (
            (self.experiment_configuration_id, "coverage experiment ID"),
            (self.target_dataset_id, "coverage target dataset ID"),
            (self.fold_dataset_id, "coverage fold dataset ID"),
            (self.r2_feature_dataset_id, "coverage feature dataset ID"),
            (self.dataset_id, "coverage dataset ID"),
        ):
            _require_sha256(value, field)
        expected = tuple(sorted(self.rows, key=_coverage_key))
        if expected != self.rows or len({row.coverage_id for row in self.rows}) != len(self.rows):
            raise ValueError("forecast-coverage rows must have unique deterministic ordering")
        if self.dataset_id != forecast_coverage_dataset_id(
            self.rows,
            experiment_configuration_id=self.experiment_configuration_id,
            target_dataset_id=self.target_dataset_id,
            fold_dataset_id=self.fold_dataset_id,
            r2_feature_dataset_id=self.r2_feature_dataset_id,
            market_data_source_class=self.market_data_source_class,
        ):
            raise ValueError("forecast-coverage dataset ID does not match its semantic content")

    @classmethod
    def create(
        cls,
        rows: Sequence[ForecastCoverageRow],
        *,
        experiment_configuration_id: str,
        target_dataset_id: str,
        fold_dataset_id: str,
        r2_feature_dataset_id: str,
        market_data_source_class: MarketDataSourceClass = MarketDataSourceClass.IG_NATIVE_CAPTURE,
    ) -> "ForecastCoverageDataset":
        ordered = tuple(sorted(rows, key=_coverage_key))
        return cls(
            ordered,
            experiment_configuration_id,
            target_dataset_id,
            fold_dataset_id,
            r2_feature_dataset_id,
            forecast_coverage_dataset_id(
                ordered,
                experiment_configuration_id=experiment_configuration_id,
                target_dataset_id=target_dataset_id,
                fold_dataset_id=fold_dataset_id,
                r2_feature_dataset_id=r2_feature_dataset_id,
                market_data_source_class=market_data_source_class,
            ),
            market_data_source_class,
        )

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": 1,
            "experiment_configuration_id": self.experiment_configuration_id,
            "target_dataset_id": self.target_dataset_id,
            "fold_dataset_id": self.fold_dataset_id,
            "r2_feature_dataset_id": self.r2_feature_dataset_id,
            "rows": [row.as_json() for row in self.rows],
            "dataset_id": self.dataset_id,
            "market_data_source_class": self.market_data_source_class.value,
        }


@dataclass(frozen=True, slots=True)
class CoefficientStabilityRow:
    target_instrument_id: str
    horizon: timedelta
    feature_set_id: str
    coefficient_name: str
    ready_fit_count: int
    expected_fit_count: int
    mean: float
    standard_deviation: float
    minimum: float
    maximum: float
    positive_count: int
    negative_count: int
    zero_count: int

    def __post_init__(self) -> None:
        if not self.target_instrument_id or not self.feature_set_id or not self.coefficient_name:
            raise ValueError("coefficient-stability scope must be non-empty")
        if self.horizon <= timedelta(0):
            raise ValueError("coefficient-stability horizon must be positive")
        if not 0 < self.ready_fit_count <= self.expected_fit_count:
            raise ValueError("coefficient-stability fit counts are invalid")
        if self.positive_count + self.negative_count + self.zero_count != self.ready_fit_count:
            raise ValueError("coefficient sign counts must reconcile to ready fits")
        if (
            any(
                not isfinite(value)
                for value in (self.mean, self.standard_deviation, self.minimum, self.maximum)
            )
            or self.standard_deviation < 0
            or self.minimum > self.maximum
        ):
            raise ValueError("coefficient-stability statistics are invalid")

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "target_instrument_id": self.target_instrument_id,
            "horizon_seconds": self.horizon.total_seconds(),
            "feature_set_id": self.feature_set_id,
            "coefficient_name": self.coefficient_name,
            "ready_fit_count": self.ready_fit_count,
            "expected_fit_count": self.expected_fit_count,
            "mean": self.mean,
            "standard_deviation": self.standard_deviation,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "positive_count": self.positive_count,
            "negative_count": self.negative_count,
            "zero_count": self.zero_count,
        }


@dataclass(frozen=True, slots=True)
class CoefficientStabilitySummary:
    rows: tuple[CoefficientStabilityRow, ...]
    fold_fit_ids: tuple[str, ...]
    summary_id: str
    market_data_source_class: MarketDataSourceClass = MarketDataSourceClass.IG_NATIVE_CAPTURE

    CONTRACT: ClassVar[str] = R2_COEFFICIENT_STABILITY_CONTRACT

    def __post_init__(self) -> None:
        if not self.fold_fit_ids or len(set(self.fold_fit_ids)) != len(self.fold_fit_ids):
            raise ValueError("stability summary requires unique fold-fit identities")
        for value in (*self.fold_fit_ids, self.summary_id):
            _require_sha256(value, "stability identity")
        expected = tuple(
            sorted(
                self.rows,
                key=lambda row: (
                    row.target_instrument_id,
                    row.horizon,
                    row.feature_set_id,
                    row.coefficient_name,
                ),
            )
        )
        if expected != self.rows:
            raise ValueError("coefficient-stability rows must use deterministic ordering")
        if self.summary_id != coefficient_stability_id(
            self.rows, self.fold_fit_ids, self.market_data_source_class
        ):
            raise ValueError("coefficient-stability ID does not match its semantic content")

    @classmethod
    def create(
        cls,
        rows: Sequence[CoefficientStabilityRow],
        fold_fit_ids: Sequence[str],
        market_data_source_class: MarketDataSourceClass = MarketDataSourceClass.IG_NATIVE_CAPTURE,
    ) -> "CoefficientStabilitySummary":
        ordered_rows = tuple(
            sorted(
                rows,
                key=lambda row: (
                    row.target_instrument_id,
                    row.horizon,
                    row.feature_set_id,
                    row.coefficient_name,
                ),
            )
        )
        ordered_fit_ids = tuple(sorted(fold_fit_ids))
        return cls(
            ordered_rows,
            ordered_fit_ids,
            coefficient_stability_id(ordered_rows, ordered_fit_ids, market_data_source_class),
            market_data_source_class,
        )

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": 1,
            "fold_fit_ids": list(self.fold_fit_ids),
            "rows": [row.as_json() for row in self.rows],
            "summary_id": self.summary_id,
            "market_data_source_class": self.market_data_source_class.value,
        }


def fold_fit_id(payload: dict[str, JsonValue]) -> str:
    """Hash the complete fit lineage, including its market-data source."""
    return _semantic_id(payload)


def forecast_coverage_row_id(payload: dict[str, JsonValue]) -> str:
    return _semantic_id(payload)


def forecast_coverage_dataset_id(
    rows: Sequence[ForecastCoverageRow],
    *,
    experiment_configuration_id: str,
    target_dataset_id: str,
    fold_dataset_id: str,
    r2_feature_dataset_id: str,
    market_data_source_class: MarketDataSourceClass = MarketDataSourceClass.IG_NATIVE_CAPTURE,
) -> str:
    return _semantic_id(
        {
            "contract": R2_FORECAST_COVERAGE_CONTRACT,
            "schema_version": 1,
            "experiment_configuration_id": experiment_configuration_id,
            "target_dataset_id": target_dataset_id,
            "fold_dataset_id": fold_dataset_id,
            "r2_feature_dataset_id": r2_feature_dataset_id,
            "market_data_source_class": market_data_source_class.value,
            "rows": [row.as_json() for row in rows],
        }
    )


def coefficient_stability_id(
    rows: Sequence[CoefficientStabilityRow],
    fold_fit_ids: Sequence[str],
    market_data_source_class: MarketDataSourceClass = MarketDataSourceClass.IG_NATIVE_CAPTURE,
) -> str:
    return _semantic_id(
        {
            "contract": R2_COEFFICIENT_STABILITY_CONTRACT,
            "schema_version": 1,
            "fold_fit_ids": list(fold_fit_ids),
            "market_data_source_class": market_data_source_class.value,
            "rows": [row.as_json() for row in rows],
        }
    )


def coverage_disposition_for_fit(disposition: FitDisposition) -> ForecastCoverageDisposition:
    mapping = {
        FitDisposition.READY: ForecastCoverageDisposition.FORECASTED,
        FitDisposition.INSUFFICIENT_TRAINING: (ForecastCoverageDisposition.INSUFFICIENT_TRAINING),
        FitDisposition.INSUFFICIENT_INNER_VALIDATION: (
            ForecastCoverageDisposition.INSUFFICIENT_INNER_VALIDATION
        ),
        FitDisposition.DEGENERATE_TARGET: ForecastCoverageDisposition.DEGENERATE_TARGET,
        FitDisposition.DEGENERATE_FEATURE_MATRIX: (
            ForecastCoverageDisposition.DEGENERATE_FEATURE_MATRIX
        ),
        FitDisposition.NON_FINITE_MATRIX: ForecastCoverageDisposition.NUMERICAL_FAILURE,
        FitDisposition.NUMERICAL_FAILURE: ForecastCoverageDisposition.NUMERICAL_FAILURE,
    }
    return mapping[disposition]


def _fold_fit_json(values: _R2FoldFitArguments) -> dict[str, JsonValue]:
    preprocessing = values["preprocessing"]
    diagnostics = values["diagnostics"]
    return cast(
        dict[str, JsonValue],
        to_json_value(
            {
                "contract": R2_FOLD_FIT_CONTRACT,
                "schema_version": 1,
                "r2_feature_dataset_id": values["r2_feature_dataset_id"],
                "target_dataset_id": values["target_dataset_id"],
                "fold_dataset_id": values["fold_dataset_id"],
                "experiment_configuration_id": values["experiment_configuration_id"],
                "preprocessing_selection_id": values["preprocessing_selection_id"],
                "model_family": values["model_family"].value,
                "horizon_seconds": values["horizon"].total_seconds(),
                "outer_fold_id": values["outer_fold_id"],
                "outer_fold_membership_hash": values["outer_fold_membership_hash"],
                "target_instrument_id": values["target_instrument_id"],
                "feature_set_id": values["feature_set_id"],
                "feature_schema_id": values["feature_schema_id"],
                "preprocessing_schema_id": values["preprocessing_schema_id"],
                "evidence_class": values["evidence_class"].value,
                "market_data_source_class": values.get(
                    "market_data_source_class", MarketDataSourceClass.IG_NATIVE_CAPTURE
                ).value,
                "application_image_identity": values["application_image_identity"],
                "numpy_library_identity": values["numpy_library_identity"],
                "sklearn_library_identity": values["sklearn_library_identity"],
                "training_cutoff": values["training_cutoff"].isoformat(),
                "selected_alpha": values["selected_alpha"],
                "preprocessing": preprocessing.as_json() if preprocessing is not None else None,
                "coefficient_feature_names": list(values["coefficient_feature_names"]),
                "intercept": values["intercept"],
                "coefficients": list(values["coefficients"]),
                "fit_row_count": values["fit_row_count"],
                "excluded_row_count": values["excluded_row_count"],
                "outer_validation_opportunity_count": values["outer_validation_opportunity_count"],
                "fit_warnings": list(values["fit_warnings"]),
                "disposition": values["disposition"].value,
                "failure": values["failure"],
                "diagnostics": diagnostics.as_json() if diagnostics is not None else None,
            }
        ),
    )


def _coverage_row_json(values: _ForecastCoverageArguments) -> dict[str, JsonValue]:
    feature_data_asof = values["feature_data_asof"]
    return cast(
        dict[str, JsonValue],
        to_json_value(
            {
                "contract": R2_FORECAST_COVERAGE_CONTRACT,
                "schema_version": 1,
                "target_id": values["target_id"],
                "target_instrument_id": values["target_instrument_id"],
                "decision_time": values["decision_time"].isoformat(),
                "horizon_seconds": values["horizon"].total_seconds(),
                "outer_fold_id": values["outer_fold_id"],
                "fold_fit_id": values["fold_fit_id"],
                "feature_data_asof": (
                    feature_data_asof.isoformat()
                    if isinstance(feature_data_asof, datetime)
                    else None
                ),
                "disposition": values["disposition"].value,
                "forecast_id": values["forecast_id"],
                "reason": values["reason"],
                "market_data_source_class": values.get(
                    "market_data_source_class", MarketDataSourceClass.IG_NATIVE_CAPTURE
                ).value,
            }
        ),
    )


def _coverage_key(row: ForecastCoverageRow) -> tuple[object, ...]:
    return row.decision_time, row.target_instrument_id, row.target_id, row.outer_fold_id


def _semantic_id(payload: object) -> str:
    canonical = to_json_value(payload)
    return sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _require_sha256(value: str, field: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} must be a lowercase SHA-256 identifier")
