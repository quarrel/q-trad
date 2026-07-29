"""Immutable contracts for authenticated R2.C preprocessing selection."""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from math import isfinite
from typing import ClassVar, TypedDict, cast

from qtrad.domain.events import JsonValue, to_json_value
from qtrad.domain.r2_features import FeatureDefinition
from qtrad.domain.r2_readiness import EvidenceClass, ModelFamily
from qtrad.domain.time import require_utc

R2_PREPROCESSING_SCHEMA_CONTRACT = "qtrad-r2-preprocessing-schema-v1"
R2_PREPROCESSING_SELECTION_CONTRACT = "qtrad-r2-preprocessing-selection-v1"

LOCAL_INSTRUMENT_IDENTITY_POLICY = "NO_INSTRUMENT_IDENTITY_V1"
POOLED_INSTRUMENT_IDENTITY_POLICY = "FULL_ONE_HOT_V1"
LOCAL_INTERCEPT_POLICY = "FIT_GLOBAL_INTERCEPT_V1"
POOLED_INTERCEPT_POLICY = "NO_GLOBAL_INTERCEPT_V1"
LOCAL_INSTRUMENT_MEMBERSHIP_POLICY = "SINGLE_INSTRUMENT_MEMBERSHIP_V1"
POOLED_INSTRUMENT_MEMBERSHIP_POLICY = "FIXED_UNIVERSE_OUTER_INNER_FIT_VALIDATION_V1"

_BINARY_PREPROCESSING_FEATURE_NAMES = frozenset(
    {"source_active", "quality_healthy", "gap_known_by_cutoff"}
)


class PreprocessingFeatureKind(StrEnum):
    CONTINUOUS = "CONTINUOUS"
    BINARY_INDICATOR = "BINARY_INDICATOR"


@dataclass(frozen=True, slots=True)
class PreprocessingFeatureDefinition:
    """One immutable preprocessing interpretation of an authenticated raw feature."""

    name: str
    kind: PreprocessingFeatureKind

    def __post_init__(self) -> None:
        if not self.name or not self.name.isascii():
            raise ValueError("preprocessing feature name must be non-empty ASCII")

    def as_json(self) -> dict[str, JsonValue]:
        return {"name": self.name, "kind": self.kind.value}


@dataclass(frozen=True, slots=True)
class R2PreprocessingSchema:
    """Semantic R2.C feature-kind schema derived from an authenticated R2.B schema."""

    features: tuple[PreprocessingFeatureDefinition, ...]
    preprocessing_schema_id: str

    CONTRACT: ClassVar[str] = R2_PREPROCESSING_SCHEMA_CONTRACT
    SCHEMA_VERSION: ClassVar[int] = 1

    def __post_init__(self) -> None:
        if not self.features or len({item.name for item in self.features}) != len(self.features):
            raise ValueError("preprocessing schema must have non-empty unique feature names")
        _require_sha256(self.preprocessing_schema_id, "preprocessing schema ID")
        if self.preprocessing_schema_id != preprocessing_schema_id(self.features):
            raise ValueError("preprocessing schema ID does not match its semantic content")

    @classmethod
    def create(cls, features: Sequence[PreprocessingFeatureDefinition]) -> "R2PreprocessingSchema":
        definitions = tuple(features)
        return cls(definitions, preprocessing_schema_id(definitions))

    def semantic_json(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": self.SCHEMA_VERSION,
            "features": [item.as_json() for item in self.features],
        }

    def as_json(self) -> dict[str, JsonValue]:
        return {
            **self.semantic_json(),
            "preprocessing_schema_id": self.preprocessing_schema_id,
        }


def derive_r2_preprocessing_schema(
    raw_feature_schema: Sequence[FeatureDefinition],
) -> R2PreprocessingSchema:
    """Derive the canonical R2.C kinds without changing the authenticated R2.B contract."""

    schema = R2PreprocessingSchema.create(
        tuple(_preprocessing_definition(item) for item in raw_feature_schema)
    )
    validate_preprocessing_schema_correspondence(raw_feature_schema, schema)
    return schema


def validate_preprocessing_schema_correspondence(
    raw_feature_schema: Sequence[FeatureDefinition],
    preprocessing_schema: R2PreprocessingSchema,
) -> None:
    """Require one preprocessing definition for every raw feature in exact order."""

    expected = tuple(_preprocessing_definition(item) for item in raw_feature_schema)
    if preprocessing_schema.features != expected:
        raise ValueError("preprocessing schema differs from the authenticated raw-feature schema")


def preprocessing_schema_id(features: Sequence[PreprocessingFeatureDefinition]) -> str:
    payload = {
        "contract": R2_PREPROCESSING_SCHEMA_CONTRACT,
        "schema_version": 1,
        "features": [item.as_json() for item in features],
    }
    return _semantic_id(payload)


def _preprocessing_definition(raw: FeatureDefinition) -> PreprocessingFeatureDefinition:
    kind = (
        PreprocessingFeatureKind.BINARY_INDICATOR
        if raw.availability_indicator or raw.name in _BINARY_PREPROCESSING_FEATURE_NAMES
        else PreprocessingFeatureKind.CONTINUOUS
    )
    return PreprocessingFeatureDefinition(raw.name, kind)


class FitDisposition(StrEnum):
    READY = "READY"
    INSUFFICIENT_TRAINING = "INSUFFICIENT_TRAINING"
    INSUFFICIENT_INNER_VALIDATION = "INSUFFICIENT_INNER_VALIDATION"
    DEGENERATE_TARGET = "DEGENERATE_TARGET"
    DEGENERATE_FEATURE_MATRIX = "DEGENERATE_FEATURE_MATRIX"
    NON_FINITE_MATRIX = "NON_FINITE_MATRIX"
    NUMERICAL_FAILURE = "NUMERICAL_FAILURE"


@dataclass(frozen=True, slots=True)
class PreprocessingFit:
    """Training-only transformation parameters, including explicit feature exclusions."""

    feature_names: tuple[str, ...]
    indicator_feature_names: tuple[str, ...]
    medians: tuple[float | None, ...]
    means: tuple[float | None, ...]
    scales: tuple[float | None, ...]
    active_feature_names: tuple[str, ...]
    unscaled_feature_names: tuple[str, ...]
    dropped_all_null_feature_names: tuple[str, ...]
    dropped_zero_variance_feature_names: tuple[str, ...]
    training_target_ids: tuple[str, ...]
    sample_weights: tuple[float, ...]

    def __post_init__(self) -> None:
        names = self.feature_names
        if not names or len(set(names)) != len(names):
            raise ValueError("feature names must be non-empty and unique")
        if any(len(values) != len(names) for values in (self.medians, self.means, self.scales)):
            raise ValueError("preprocessing vectors must align with the feature schema")
        if len(set(self.indicator_feature_names)) != len(self.indicator_feature_names) or not set(
            self.indicator_feature_names
        ) <= set(names):
            raise ValueError("indicator features must be a unique schema subset")
        groups = (
            self.active_feature_names,
            self.dropped_all_null_feature_names,
            self.dropped_zero_variance_feature_names,
        )
        if any(len(set(group)) != len(group) for group in groups):
            raise ValueError("preprocessing feature groups must be unique")
        if set().union(*(set(group) for group in groups)) != set(names) or any(
            set(left) & set(right)
            for index, left in enumerate(groups)
            for right in groups[index + 1 :]
        ):
            raise ValueError("active and dropped features must exactly partition the schema")
        indicators = set(self.indicator_feature_names)
        if set(self.unscaled_feature_names) != indicators & set(self.active_feature_names):
            raise ValueError("exactly active binary indicators must be unscaled")
        if len(self.training_target_ids) == 0 or len(set(self.training_target_ids)) != len(
            self.training_target_ids
        ):
            raise ValueError("preprocessing training membership must be non-empty and unique")
        if len(self.sample_weights) != len(self.training_target_ids) or any(
            not isfinite(value) or value <= 0 for value in self.sample_weights
        ):
            raise ValueError("sample weights must be finite, positive and row-aligned")
        if abs(sum(self.sample_weights) / len(self.sample_weights) - 1.0) > 1e-12:
            raise ValueError("sample weights must have total mean one")
        all_null = set(self.dropped_all_null_feature_names)
        zero_variance = set(self.dropped_zero_variance_feature_names)
        active = set(self.active_feature_names)
        for index, name in enumerate(names):
            median, mean, scale = self.medians[index], self.means[index], self.scales[index]
            if name in indicators:
                if median is not None or mean is not None or scale is not None:
                    raise ValueError("binary indicators cannot be imputed or scaled")
            elif name in active:
                if median is None or mean is None or scale is None:
                    raise ValueError("active continuous features require complete parameters")
                if not all(isfinite(value) for value in (median, mean, scale)) or scale <= 0:
                    raise ValueError(
                        "active continuous parameters must be finite with positive scale"
                    )
            elif name in all_null:
                if median is not None or mean is not None or scale is not None:
                    raise ValueError("all-null features cannot contain fitted parameters")
            elif name in zero_variance and (
                median is None or not isfinite(median) or mean is not None or scale is not None
            ):
                raise ValueError("zero-variance features retain only their finite imputation value")

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "feature_names": list(self.feature_names),
            "indicator_feature_names": list(self.indicator_feature_names),
            "medians": list(self.medians),
            "means": list(self.means),
            "scales": list(self.scales),
            "active_feature_names": list(self.active_feature_names),
            "unscaled_feature_names": list(self.unscaled_feature_names),
            "dropped_all_null_feature_names": list(self.dropped_all_null_feature_names),
            "dropped_zero_variance_feature_names": list(self.dropped_zero_variance_feature_names),
            "training_target_ids": list(self.training_target_ids),
            "sample_weights": list(self.sample_weights),
        }


@dataclass(frozen=True, slots=True)
class AlphaCandidateScore:
    alpha: float
    disposition: FitDisposition
    loss: float | None
    failure: str | None
    inner_fit_target_ids: tuple[str, ...]
    inner_validation_target_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isfinite(self.alpha) or self.alpha <= 0:
            raise ValueError("alpha must be finite and positive")
        if not self.inner_fit_target_ids or not self.inner_validation_target_ids:
            raise ValueError("candidate memberships must be non-empty")
        if set(self.inner_fit_target_ids) & set(self.inner_validation_target_ids):
            raise ValueError("candidate memberships must be disjoint")
        if self.disposition is FitDisposition.READY:
            if (
                self.loss is None
                or not isfinite(self.loss)
                or self.loss < 0
                or self.failure is not None
            ):
                raise ValueError(
                    "ready candidates require a finite non-negative loss and no failure"
                )
        elif self.loss is not None or not self.failure:
            raise ValueError("failed candidates must retain a failure and no loss")

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "alpha": self.alpha,
            "disposition": self.disposition.value,
            "loss": self.loss,
            "failure": self.failure,
            "inner_fit_target_ids": list(self.inner_fit_target_ids),
            "inner_validation_target_ids": list(self.inner_validation_target_ids),
        }


@dataclass(frozen=True, slots=True)
class AlphaSelection:
    disposition: FitDisposition
    outer_training_target_ids: tuple[str, ...]
    inner_fit_target_ids: tuple[str, ...]
    inner_validation_target_ids: tuple[str, ...]
    purged_target_ids: tuple[str, ...]
    inner_preprocessing: PreprocessingFit | None
    candidate_scores: tuple[AlphaCandidateScore, ...]
    selected_alpha: float | None
    outer_preprocessing: PreprocessingFit | None

    def __post_init__(self) -> None:
        outer = self.outer_training_target_ids
        if not outer or len(set(outer)) != len(outer):
            raise ValueError("outer training membership must be non-empty and unique")
        groups = (
            self.inner_fit_target_ids,
            self.inner_validation_target_ids,
            self.purged_target_ids,
        )
        if any(len(set(group)) != len(group) for group in groups) or any(
            set(left) & set(right)
            for index, left in enumerate(groups)
            for right in groups[index + 1 :]
        ):
            raise ValueError(
                "inner fit, validation and purge memberships must be unique and disjoint"
            )
        if set().union(*(set(group) for group in groups)) != set(outer):
            raise ValueError(
                "inner memberships and purge evidence must exactly partition outer training"
            )
        if self.candidate_scores and tuple(score.alpha for score in self.candidate_scores) != tuple(
            sorted({score.alpha for score in self.candidate_scores})
        ):
            raise ValueError("candidate evidence must be unique and alpha ordered")
        if self.disposition is FitDisposition.READY:
            if (
                self.selected_alpha is None
                or self.inner_preprocessing is None
                or self.outer_preprocessing is None
            ):
                raise ValueError(
                    "ready selection requires the selected alpha and both preprocessing fits"
                )
            ready_alphas = {
                score.alpha
                for score in self.candidate_scores
                if score.disposition is FitDisposition.READY
            }
            if self.selected_alpha not in ready_alphas:
                raise ValueError("selected alpha must identify a ready candidate")
        elif self.selected_alpha is not None:
            raise ValueError("failed selection cannot select an alpha")

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "disposition": self.disposition.value,
            "outer_training_target_ids": list(self.outer_training_target_ids),
            "inner_fit_target_ids": list(self.inner_fit_target_ids),
            "inner_validation_target_ids": list(self.inner_validation_target_ids),
            "purged_target_ids": list(self.purged_target_ids),
            "inner_preprocessing": self.inner_preprocessing.as_json()
            if self.inner_preprocessing
            else None,
            "candidate_scores": [score.as_json() for score in self.candidate_scores],
            "selected_alpha": self.selected_alpha,
            "outer_preprocessing": self.outer_preprocessing.as_json()
            if self.outer_preprocessing
            else None,
        }


class _R2PreprocessingSelectionArguments(TypedDict):
    r2_feature_dataset_id: str
    target_dataset_id: str
    fold_dataset_id: str
    experiment_configuration_id: str
    model_family: ModelFamily
    horizon: timedelta
    outer_fold_id: str
    outer_fold_membership_hash: str
    target_instruments: tuple[str, ...]
    inner_validation_start: datetime
    inner_validation_end: datetime
    purge_boundary: datetime
    feature_schema_id: str
    feature_set_id: str
    preprocessing_schema_id: str
    preprocessing_schema: R2PreprocessingSchema
    evidence_class: EvidenceClass
    application_image_identity: str
    sklearn_library_identity: str
    preprocessing_policy: str
    inner_validation_policy: str
    alpha_grid: tuple[float, ...]
    ridge_solver: str
    ridge_tolerance: float
    ridge_max_iterations: int
    loss_policy: str
    pooled_weighting_policy: str
    instrument_identity_policy: str
    intercept_policy: str
    instrument_membership_policy: str
    holdout_excluded: bool
    selection: AlphaSelection


@dataclass(frozen=True, slots=True)
class R2PreprocessingSelection:
    """Identity-bearing replay contract for authenticated fold-local model selection."""

    r2_feature_dataset_id: str
    target_dataset_id: str
    fold_dataset_id: str
    experiment_configuration_id: str
    model_family: ModelFamily
    horizon: timedelta
    outer_fold_id: str
    outer_fold_membership_hash: str
    target_instruments: tuple[str, ...]
    inner_validation_start: datetime
    inner_validation_end: datetime
    purge_boundary: datetime
    feature_schema_id: str
    feature_set_id: str
    preprocessing_schema_id: str
    preprocessing_schema: R2PreprocessingSchema
    evidence_class: EvidenceClass
    application_image_identity: str
    sklearn_library_identity: str
    preprocessing_policy: str
    inner_validation_policy: str
    alpha_grid: tuple[float, ...]
    ridge_solver: str
    ridge_tolerance: float
    ridge_max_iterations: int
    loss_policy: str
    pooled_weighting_policy: str
    instrument_identity_policy: str
    intercept_policy: str
    instrument_membership_policy: str
    holdout_excluded: bool
    selection: AlphaSelection
    artifact_id: str

    CONTRACT: ClassVar[str] = R2_PREPROCESSING_SELECTION_CONTRACT
    SCHEMA_VERSION: ClassVar[int] = 1

    def __post_init__(self) -> None:
        for value, field in (
            (self.r2_feature_dataset_id, "feature dataset ID"),
            (self.target_dataset_id, "target dataset ID"),
            (self.fold_dataset_id, "fold dataset ID"),
            (self.experiment_configuration_id, "experiment configuration ID"),
            (self.outer_fold_membership_hash, "outer membership hash"),
            (self.feature_schema_id, "feature schema ID"),
            (self.feature_set_id, "feature set ID"),
            (self.preprocessing_schema_id, "preprocessing schema ID"),
            (self.artifact_id, "preprocessing-selection artifact ID"),
        ):
            _require_sha256(value, field)
        if self.preprocessing_schema.preprocessing_schema_id != self.preprocessing_schema_id:
            raise ValueError("preprocessing schema binding is invalid")
        schema_names = tuple(item.name for item in self.preprocessing_schema.features)
        indicator_names = tuple(
            item.name
            for item in self.preprocessing_schema.features
            if item.kind is PreprocessingFeatureKind.BINARY_INDICATOR
        )
        for fit in (
            self.selection.inner_preprocessing,
            self.selection.outer_preprocessing,
        ):
            if fit is None:
                continue
            if fit.feature_names != schema_names or fit.indicator_feature_names != indicator_names:
                raise ValueError("preprocessing fit differs from its bound preprocessing schema")
        if self.model_family not in (
            ModelFamily.LOCAL_RIDGE,
            ModelFamily.POOLED_LOCAL_RIDGE,
            ModelFamily.POOLED_CROSS_ASSET_RIDGE,
        ):
            raise ValueError("unsupported Ridge preprocessing-selection model family")
        if self.horizon != timedelta(minutes=15):
            raise ValueError("R2 preprocessing selection supports only the primary horizon")
        if not self.application_image_identity or not self.sklearn_library_identity:
            raise ValueError("application image and sklearn library identities are required")
        if not self.outer_fold_id or not self.target_instruments:
            raise ValueError("R2 fold and target scope must be non-empty")
        if len(set(self.target_instruments)) != len(self.target_instruments):
            raise ValueError("R2 target scope must contain unique instruments")
        if self.model_family is ModelFamily.LOCAL_RIDGE and len(self.target_instruments) != 1:
            raise ValueError("local selection must identify exactly one eligible target")
        if self.model_family is not ModelFamily.LOCAL_RIDGE and len(self.target_instruments) < 2:
            raise ValueError("pooled selection requires at least two eligible targets")
        expected_policies = (
            (
                LOCAL_INSTRUMENT_IDENTITY_POLICY,
                LOCAL_INTERCEPT_POLICY,
                LOCAL_INSTRUMENT_MEMBERSHIP_POLICY,
            )
            if self.model_family is ModelFamily.LOCAL_RIDGE
            else (
                POOLED_INSTRUMENT_IDENTITY_POLICY,
                POOLED_INTERCEPT_POLICY,
                POOLED_INSTRUMENT_MEMBERSHIP_POLICY,
            )
        )
        if (
            self.instrument_identity_policy,
            self.intercept_policy,
            self.instrument_membership_policy,
        ) != expected_policies:
            raise ValueError("selection model policies differ from the declared model family")
        for value, field in (
            (self.inner_validation_start, "inner validation start"),
            (self.inner_validation_end, "inner validation end"),
            (self.purge_boundary, "inner purge boundary"),
        ):
            require_utc(value, field)
        if (
            self.inner_validation_end <= self.inner_validation_start
            or self.purge_boundary != self.inner_validation_start
        ):
            raise ValueError("inner validation chronology or purge boundary is invalid")
        if not self.holdout_excluded:
            raise ValueError("an R2 preprocessing selection must exclude the locked holdout")
        if self.preprocessing_policy != "TRAINING_MEDIAN_STANDARDISE_V1":
            raise ValueError("unsupported preprocessing policy")
        if self.inner_validation_policy != "CHRONOLOGICAL_TAIL_PURGED_V1":
            raise ValueError("unsupported inner-validation policy")
        if not self.loss_policy or not self.pooled_weighting_policy:
            raise ValueError("preprocessing-selection policies must be explicit")
        if tuple(sorted(set(self.alpha_grid))) != self.alpha_grid or any(
            not isfinite(alpha) or alpha <= 0 for alpha in self.alpha_grid
        ):
            raise ValueError("alpha grid must be unique, ascending, finite and positive")
        if tuple(score.alpha for score in self.selection.candidate_scores) not in (
            (),
            self.alpha_grid,
        ):
            raise ValueError("candidate evidence must retain the complete configured alpha grid")
        if not self.ridge_solver or not isfinite(self.ridge_tolerance) or self.ridge_tolerance <= 0:
            raise ValueError("ridge solver and tolerance are invalid")
        if self.ridge_max_iterations <= 0:
            raise ValueError("ridge maximum iterations must be positive")
        if self.artifact_id != preprocessing_selection_id(self.semantic_json()):
            raise ValueError(
                "preprocessing-selection artifact ID does not match its semantic content"
            )

    @classmethod
    def create(cls, **values: object) -> "R2PreprocessingSelection":
        semantic = _preprocessing_selection_json(values)
        arguments = cast(_R2PreprocessingSelectionArguments, values)
        return cls(**arguments, artifact_id=preprocessing_selection_id(semantic))

    def semantic_json(self) -> dict[str, JsonValue]:
        return _preprocessing_selection_json(
            {
                "r2_feature_dataset_id": self.r2_feature_dataset_id,
                "target_dataset_id": self.target_dataset_id,
                "fold_dataset_id": self.fold_dataset_id,
                "experiment_configuration_id": self.experiment_configuration_id,
                "model_family": self.model_family,
                "horizon": self.horizon,
                "outer_fold_id": self.outer_fold_id,
                "outer_fold_membership_hash": self.outer_fold_membership_hash,
                "target_instruments": self.target_instruments,
                "inner_validation_start": self.inner_validation_start,
                "inner_validation_end": self.inner_validation_end,
                "purge_boundary": self.purge_boundary,
                "feature_schema_id": self.feature_schema_id,
                "feature_set_id": self.feature_set_id,
                "preprocessing_schema_id": self.preprocessing_schema_id,
                "preprocessing_schema": self.preprocessing_schema,
                "evidence_class": self.evidence_class,
                "application_image_identity": self.application_image_identity,
                "sklearn_library_identity": self.sklearn_library_identity,
                "preprocessing_policy": self.preprocessing_policy,
                "inner_validation_policy": self.inner_validation_policy,
                "alpha_grid": self.alpha_grid,
                "ridge_solver": self.ridge_solver,
                "ridge_tolerance": self.ridge_tolerance,
                "ridge_max_iterations": self.ridge_max_iterations,
                "loss_policy": self.loss_policy,
                "pooled_weighting_policy": self.pooled_weighting_policy,
                "instrument_identity_policy": self.instrument_identity_policy,
                "intercept_policy": self.intercept_policy,
                "instrument_membership_policy": self.instrument_membership_policy,
                "holdout_excluded": self.holdout_excluded,
                "selection": self.selection,
            }
        )

    def as_json(self) -> dict[str, JsonValue]:
        return {**self.semantic_json(), "artifact_id": self.artifact_id}


def _preprocessing_selection_json(values: dict[str, object]) -> dict[str, JsonValue]:
    selection = values["selection"]
    if not isinstance(selection, AlphaSelection):
        raise TypeError("preprocessing selection must contain an AlphaSelection")
    preprocessing_schema = values["preprocessing_schema"]
    if not isinstance(preprocessing_schema, R2PreprocessingSchema):
        raise TypeError("preprocessing selection must contain an R2PreprocessingSchema")
    return {
        "contract": R2_PREPROCESSING_SELECTION_CONTRACT,
        "schema_version": 1,
        "r2_feature_dataset_id": str(values["r2_feature_dataset_id"]),
        "target_dataset_id": str(values["target_dataset_id"]),
        "fold_dataset_id": str(values["fold_dataset_id"]),
        "experiment_configuration_id": str(values["experiment_configuration_id"]),
        "model_family": ModelFamily(cast(ModelFamily, values["model_family"])).value,
        "horizon_seconds": cast(timedelta, values["horizon"]).total_seconds(),
        "outer_fold_id": str(values["outer_fold_id"]),
        "outer_fold_membership_hash": str(values["outer_fold_membership_hash"]),
        "target_instruments": list(cast(tuple[str, ...], values["target_instruments"])),
        "inner_validation_start": cast(datetime, values["inner_validation_start"]).isoformat(),
        "inner_validation_end": cast(datetime, values["inner_validation_end"]).isoformat(),
        "purge_boundary": cast(datetime, values["purge_boundary"]).isoformat(),
        "feature_schema_id": str(values["feature_schema_id"]),
        "feature_set_id": str(values["feature_set_id"]),
        "preprocessing_schema_id": str(values["preprocessing_schema_id"]),
        "preprocessing_schema": preprocessing_schema.as_json(),
        "evidence_class": EvidenceClass(cast(EvidenceClass, values["evidence_class"])).value,
        "application_image_identity": str(values["application_image_identity"]),
        "sklearn_library_identity": str(values["sklearn_library_identity"]),
        "preprocessing_policy": str(values["preprocessing_policy"]),
        "inner_validation_policy": str(values["inner_validation_policy"]),
        "alpha_grid": list(cast(tuple[float, ...], values["alpha_grid"])),
        "ridge_solver": str(values["ridge_solver"]),
        "ridge_tolerance": float(cast(float, values["ridge_tolerance"])),
        "ridge_max_iterations": int(cast(int, values["ridge_max_iterations"])),
        "loss_policy": str(values["loss_policy"]),
        "pooled_weighting_policy": str(values["pooled_weighting_policy"]),
        "instrument_identity_policy": str(values["instrument_identity_policy"]),
        "intercept_policy": str(values["intercept_policy"]),
        "instrument_membership_policy": str(values["instrument_membership_policy"]),
        "holdout_excluded": bool(values["holdout_excluded"]),
        "selection": selection.as_json(),
    }


def preprocessing_selection_id(payload: dict[str, JsonValue]) -> str:
    return _semantic_id(payload)


def _semantic_id(payload: object) -> str:
    canonical = to_json_value(payload)
    return sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _require_sha256(value: str, field: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} must be a lowercase SHA-256 identifier")
