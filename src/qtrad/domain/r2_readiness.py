"""R2 experiment identity and pre-holdout readiness contracts."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from math import isfinite
from typing import ClassVar

from qtrad.domain.events import JsonValue, to_json_value
from qtrad.domain.foundation import InstrumentRole
from qtrad.domain.time import require_utc

R2_EXPERIMENT_CONTRACT = "qtrad-r2-experiment-config-v1"
R2_READINESS_CONTRACT = "qtrad-r2-readiness-v1"
_ALLOWED_HORIZONS = frozenset(timedelta(minutes=value) for value in (5, 15, 30, 60))


class EvidenceClass(StrEnum):
    IMPLEMENTATION = "IMPLEMENTATION_EVIDENCE_ONLY"
    CONFIRMATORY = "CONFIRMATORY"


class ReadinessState(StrEnum):
    READY = "READY"
    PARTIALLY_READY = "PARTIALLY_READY"
    NOT_READY = "NOT_READY"


class FeatureFamily(StrEnum):
    LOCAL_RETURNS = "LOCAL_RETURNS"
    LOCAL_VOLATILITY_RANGE = "LOCAL_VOLATILITY_RANGE"
    TIME_AVAILABILITY = "TIME_AVAILABILITY"
    SPREAD = "SPREAD"
    QUOTE_IMBALANCE = "QUOTE_IMBALANCE"
    POOLED_CROSS_ASSET = "POOLED_CROSS_ASSET"


class FeatureEligibility(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    NOT_ELIGIBLE = "NOT_ELIGIBLE"
    PENDING = "PENDING"


@dataclass(frozen=True, slots=True)
class EligibilityDecision:
    """Authenticated pre-holdout eligibility evidence embedded in experiment identity."""

    subject: str
    state: FeatureEligibility
    evidence_start: datetime
    evidence_end: datetime
    reason: str
    evidence_id: str

    def __post_init__(self) -> None:
        if not self.subject or not self.reason.strip():
            raise ValueError("eligibility subject and reason must be non-empty")
        require_utc(self.evidence_start, "eligibility evidence start")
        require_utc(self.evidence_end, "eligibility evidence end")
        if self.evidence_end <= self.evidence_start:
            raise ValueError("eligibility evidence range must be positive")
        if self.evidence_id != _eligibility_id(
            self.subject, self.state, self.evidence_start, self.evidence_end, self.reason
        ):
            raise ValueError("eligibility evidence ID does not authenticate its content")

    @classmethod
    def create(
        cls,
        *,
        subject: str,
        state: FeatureEligibility,
        evidence_start: datetime,
        evidence_end: datetime,
        reason: str,
    ) -> "EligibilityDecision":
        return cls(
            subject=subject,
            state=state,
            evidence_start=evidence_start,
            evidence_end=evidence_end,
            reason=reason,
            evidence_id=_eligibility_id(subject, state, evidence_start, evidence_end, reason),
        )

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "subject": self.subject,
            "state": self.state.value,
            "evidence_start": self.evidence_start.isoformat(),
            "evidence_end": self.evidence_end.isoformat(),
            "reason": self.reason,
            "evidence_id": self.evidence_id,
        }


class ModelFamily(StrEnum):
    ZERO_RETURN = "ZERO_RETURN"
    LOCAL_RIDGE = "LOCAL_RIDGE"
    POOLED_LOCAL_RIDGE = "POOLED_LOCAL_RIDGE"
    POOLED_CROSS_ASSET_RIDGE = "POOLED_CROSS_ASSET_RIDGE"


@dataclass(frozen=True, slots=True)
class FeatureSet:
    """One ordered, ablatable and identity-bearing feature-set declaration."""

    name: str
    families: tuple[FeatureFamily, ...]

    def __post_init__(self) -> None:
        if not self.name or not self.families:
            raise ValueError("feature set name and families must be non-empty")
        if len(set(self.families)) != len(self.families):
            raise ValueError("feature set families must be unique")

    def as_json(self) -> dict[str, JsonValue]:
        return {"name": self.name, "families": [family.value for family in self.families]}


@dataclass(frozen=True, slots=True)
class R2ExperimentConfig:
    """Strict declaration binding every R2 decision to one verified R1 foundation."""

    name: str
    schema_version: int
    r1_bundle_id: str
    observation_dataset_id: str
    foundation_configuration_id: str
    panel_dataset_id: str
    target_dataset_id: str
    fold_dataset_id: str
    r1_application_version: str
    r1_image_identity: str
    ordered_instruments: tuple[str, ...]
    instrument_roles: Mapping[str, InstrumentRole]
    target_instrument_eligibility: Mapping[str, EligibilityDecision]
    target_instruments: tuple[str, ...]
    confirmatory_target_instruments: tuple[str, ...]
    market_groups: Mapping[str, str]
    horizons: tuple[timedelta, ...]
    primary_horizon: timedelta
    feature_sets: tuple[FeatureSet, ...]
    feature_windows: tuple[timedelta, ...]
    feature_coverage_thresholds: Mapping[FeatureFamily, float]
    feature_eligibility: Mapping[FeatureFamily, EligibilityDecision]
    preprocessing_policy: str
    alpha_grid: tuple[float, ...]
    inner_validation_policy: str
    ridge_solver: str
    ridge_tolerance: float
    ridge_max_iterations: int
    pooled_weighting_policy: str
    minimum_training_rows: int
    minimum_inner_validation_rows: int
    minimum_outer_validation_rows: int
    metric_policy: str
    forecast_bucket_policy: str
    state_bucket_policy: str
    model_selection_policy: str
    acceptance_thresholds: Mapping[str, float]
    holdout_range: tuple[datetime, datetime]
    numeric_replay_relative_tolerance: float
    numeric_replay_absolute_tolerance: float
    evidence_class: EvidenceClass
    model_families: tuple[ModelFamily, ...]

    CONTRACT: ClassVar[str] = R2_EXPERIMENT_CONTRACT
    SCHEMA_VERSION: ClassVar[int] = 1

    def __post_init__(self) -> None:
        if not self.name or self.schema_version != self.SCHEMA_VERSION:
            raise ValueError("R2 experiment name and schema version are invalid")
        for value, field in (
            (self.r1_bundle_id, "R1 bundle ID"),
            (self.observation_dataset_id, "observation dataset ID"),
            (self.foundation_configuration_id, "foundation configuration ID"),
            (self.panel_dataset_id, "panel dataset ID"),
            (self.target_dataset_id, "target dataset ID"),
            (self.fold_dataset_id, "fold dataset ID"),
        ):
            _require_sha256(value, field)
        if not self.r1_application_version or not self.r1_image_identity:
            raise ValueError("R1 application and image identities are required")
        if not self.ordered_instruments or len(set(self.ordered_instruments)) != len(
            self.ordered_instruments
        ):
            raise ValueError("ordered instrument universe must be non-empty and unique")
        if set(self.instrument_roles) != set(self.ordered_instruments):
            raise ValueError("instrument roles must exactly match the ordered universe")
        r1_targets = tuple(
            instrument
            for instrument in self.ordered_instruments
            if InstrumentRole(self.instrument_roles[instrument]) is InstrumentRole.TARGET
        )
        if set(self.target_instrument_eligibility) != set(r1_targets):
            raise ValueError("target eligibility must exactly cover the R1 TARGET universe")
        for instrument in r1_targets:
            decision = self.target_instrument_eligibility[instrument]
            if decision.subject != instrument:
                raise ValueError("target eligibility subject differs from its instrument")
            if decision.evidence_end > self.holdout_range[0]:
                raise ValueError("target eligibility evidence must end before the holdout")
        eligible_targets = tuple(
            instrument
            for instrument in r1_targets
            if self.target_instrument_eligibility[instrument].state is FeatureEligibility.ELIGIBLE
        )
        if self.target_instruments != eligible_targets:
            raise ValueError("target instruments must exactly follow ELIGIBLE target decisions")
        if (
            not self.confirmatory_target_instruments
            or any(
                item not in self.target_instruments for item in self.confirmatory_target_instruments
            )
            or len(set(self.confirmatory_target_instruments))
            != len(self.confirmatory_target_instruments)
        ):
            raise ValueError("confirmatory targets must be a unique non-empty eligible subset")
        if "index:volatility" in r1_targets or "index:volatility" in self.target_instruments:
            raise ValueError("VIX must not be an R2 target")
        if set(self.market_groups) != set(self.confirmatory_target_instruments) or any(
            not group for group in self.market_groups.values()
        ):
            raise ValueError("market groups must exactly cover confirmatory targets")
        if (
            not self.horizons
            or self.horizons != tuple(sorted(self.horizons))
            or len(set(self.horizons)) != len(self.horizons)
            or any(horizon not in _ALLOWED_HORIZONS for horizon in self.horizons)
        ):
            raise ValueError("R2 horizons must be unique ascending 5/15/30/60-minute values")
        if (
            self.primary_horizon != timedelta(minutes=15)
            or self.primary_horizon not in self.horizons
        ):
            raise ValueError("R2 primary horizon must be configured at 15 minutes")
        if not self.feature_sets or len({item.name for item in self.feature_sets}) != len(
            self.feature_sets
        ):
            raise ValueError("feature set names must be non-empty and unique")
        declared_families = set(FeatureFamily)
        if set(self.feature_coverage_thresholds) != declared_families:
            raise ValueError("coverage thresholds must cover every feature family")
        if set(self.feature_eligibility) != declared_families:
            raise ValueError("eligibility decisions must cover every feature family")
        for family in declared_families:
            threshold = self.feature_coverage_thresholds[family]
            decision = self.feature_eligibility[family]
            if not isfinite(threshold) or not 0 <= threshold <= 1:
                raise ValueError(f"invalid feature coverage threshold for {family.value}")
            if decision.subject != family.value:
                raise ValueError("feature eligibility subject differs from its family")
            if decision.evidence_end > self.holdout_range[0]:
                raise ValueError("feature eligibility evidence must end before the holdout")
        required_families = {
            FeatureFamily.LOCAL_RETURNS,
            FeatureFamily.LOCAL_VOLATILITY_RANGE,
            FeatureFamily.TIME_AVAILABILITY,
            FeatureFamily.POOLED_CROSS_ASSET,
        }
        if any(
            self.feature_eligibility[family].state is not FeatureEligibility.ELIGIBLE
            for family in required_families
        ):
            raise ValueError("core R2 feature families must be ELIGIBLE")
        _validate_feature_ladder(self.feature_sets, self.feature_eligibility)
        if (
            not self.feature_windows
            or self.feature_windows != tuple(sorted(self.feature_windows))
            or len(set(self.feature_windows)) != len(self.feature_windows)
            or any(window <= timedelta(0) for window in self.feature_windows)
        ):
            raise ValueError("feature windows must be unique, positive and ascending")
        if not self.alpha_grid or any(
            not isfinite(alpha) or alpha <= 0 for alpha in self.alpha_grid
        ):
            raise ValueError("alpha grid must contain finite positive values")
        if tuple(sorted(set(self.alpha_grid))) != self.alpha_grid:
            raise ValueError("alpha grid must be unique and ascending")
        if self.ridge_solver == "auto" or self.ridge_solver != "lsqr":
            raise ValueError(
                "the initial R2 numerical decision requires the deterministic lsqr solver"
            )
        if not isfinite(self.ridge_tolerance) or self.ridge_tolerance <= 0:
            raise ValueError("ridge tolerance must be finite and positive")
        if self.ridge_max_iterations <= 0:
            raise ValueError("ridge maximum iterations must be positive")
        if self.pooled_weighting_policy != "EQUAL_INSTRUMENT_TOTAL_WEIGHT_MEAN_ONE":
            raise ValueError("pooled weighting policy is unsupported")
        if any(
            value <= 0
            for value in (
                self.minimum_training_rows,
                self.minimum_inner_validation_rows,
                self.minimum_outer_validation_rows,
            )
        ):
            raise ValueError("minimum row thresholds must be positive")
        if not all(
            (
                self.preprocessing_policy,
                self.inner_validation_policy,
                self.metric_policy,
                self.forecast_bucket_policy,
                self.state_bucket_policy,
                self.model_selection_policy,
            )
        ):
            raise ValueError("named R2 policies must be non-empty")
        if not self.acceptance_thresholds or any(
            not key or not isfinite(value) for key, value in self.acceptance_thresholds.items()
        ):
            raise ValueError("acceptance thresholds must be named and finite")
        require_utc(self.holdout_range[0], "R2 holdout start")
        require_utc(self.holdout_range[1], "R2 holdout end")
        if self.holdout_range[1] <= self.holdout_range[0]:
            raise ValueError("R2 holdout range must be positive")
        if any(
            not isfinite(value) or value < 0
            for value in (
                self.numeric_replay_relative_tolerance,
                self.numeric_replay_absolute_tolerance,
            )
        ):
            raise ValueError("numerical replay tolerances must be finite and non-negative")
        required_models = tuple(ModelFamily)
        if self.model_families != required_models:
            raise ValueError("R2.A requires the complete ordered baseline model family")

    @property
    def configuration_id(self) -> str:
        return _hash_json(self.as_json())

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": self.schema_version,
            "name": self.name,
            "r1_bundle_id": self.r1_bundle_id,
            "observation_dataset_id": self.observation_dataset_id,
            "foundation_configuration_id": self.foundation_configuration_id,
            "panel_dataset_id": self.panel_dataset_id,
            "target_dataset_id": self.target_dataset_id,
            "fold_dataset_id": self.fold_dataset_id,
            "r1_application_version": self.r1_application_version,
            "r1_image_identity": self.r1_image_identity,
            "ordered_instruments": list(self.ordered_instruments),
            "instrument_roles": {
                item: InstrumentRole(self.instrument_roles[item]).value
                for item in self.ordered_instruments
            },
            "target_instrument_eligibility": {
                item: self.target_instrument_eligibility[item].as_json()
                for item in sorted(self.target_instrument_eligibility)
            },
            "target_instruments": list(self.target_instruments),
            "confirmatory_target_instruments": list(self.confirmatory_target_instruments),
            "market_groups": {
                item: self.market_groups[item] for item in self.confirmatory_target_instruments
            },
            "horizons_seconds": [item.total_seconds() for item in self.horizons],
            "primary_horizon_seconds": self.primary_horizon.total_seconds(),
            "feature_sets": [item.as_json() for item in self.feature_sets],
            "feature_windows_seconds": [item.total_seconds() for item in self.feature_windows],
            "feature_coverage_thresholds": _family_mapping(self.feature_coverage_thresholds),
            "feature_eligibility": {
                family.value: self.feature_eligibility[family].as_json() for family in FeatureFamily
            },
            "preprocessing_policy": self.preprocessing_policy,
            "alpha_grid": list(self.alpha_grid),
            "inner_validation_policy": self.inner_validation_policy,
            "ridge_solver": self.ridge_solver,
            "ridge_tolerance": self.ridge_tolerance,
            "ridge_max_iterations": self.ridge_max_iterations,
            "pooled_weighting_policy": self.pooled_weighting_policy,
            "minimum_training_rows": self.minimum_training_rows,
            "minimum_inner_validation_rows": self.minimum_inner_validation_rows,
            "minimum_outer_validation_rows": self.minimum_outer_validation_rows,
            "metric_policy": self.metric_policy,
            "forecast_bucket_policy": self.forecast_bucket_policy,
            "state_bucket_policy": self.state_bucket_policy,
            "model_selection_policy": self.model_selection_policy,
            "acceptance_thresholds": dict(sorted(self.acceptance_thresholds.items())),
            "holdout_range": [item.isoformat() for item in self.holdout_range],
            "numeric_replay_relative_tolerance": self.numeric_replay_relative_tolerance,
            "numeric_replay_absolute_tolerance": self.numeric_replay_absolute_tolerance,
            "evidence_class": self.evidence_class.value,
            "model_families": [item.value for item in self.model_families],
        }


@dataclass(frozen=True, slots=True)
class R2ReadinessReport:
    experiment_configuration_id: str
    r1_bundle_id: str
    software_contract_ready: ReadinessState
    representative_integration_ready: ReadinessState
    confirmatory_data_ready: ReadinessState
    inner_validation_rows_ready: ReadinessState
    confirmatory_oof_ready: ReadinessState
    locked_holdout_ready: ReadinessState
    feature_family_states: Mapping[FeatureFamily, ReadinessState]
    coverage_matrix: Mapping[str, tuple["CoverageCell", ...]]
    unmet_conditions: tuple[str, ...]
    evidence_class: EvidenceClass

    CONTRACT: ClassVar[str] = R2_READINESS_CONTRACT

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": 1,
            "experiment_configuration_id": self.experiment_configuration_id,
            "r1_bundle_id": self.r1_bundle_id,
            "software_contract_ready": self.software_contract_ready.value,
            "representative_integration_ready": self.representative_integration_ready.value,
            "confirmatory_data_ready": self.confirmatory_data_ready.value,
            "inner_validation_rows_ready": self.inner_validation_rows_ready.value,
            "confirmatory_oof_ready": self.confirmatory_oof_ready.value,
            "locked_holdout_ready": self.locked_holdout_ready.value,
            "feature_family_states": {
                family.value: self.feature_family_states[family].value for family in FeatureFamily
            },
            "coverage_matrix": {
                instrument: [cell.as_json() for cell in self.coverage_matrix[instrument]]
                for instrument in sorted(self.coverage_matrix)
            },
            "unmet_conditions": list(self.unmet_conditions),
            "evidence_class": self.evidence_class.value,
        }


@dataclass(frozen=True, slots=True)
class CoverageCell:
    instrument_id: str
    block: str
    block_start: datetime
    block_end: datetime
    expected_active_opportunities: int
    valid_targets: int

    def __post_init__(self) -> None:
        require_utc(self.block_start, "coverage block start")
        require_utc(self.block_end, "coverage block end")
        if self.block_end <= self.block_start:
            raise ValueError("coverage block range must be positive")
        if (
            not self.instrument_id
            or not self.block
            or self.expected_active_opportunities < 0
            or self.valid_targets < 0
            or self.valid_targets > self.expected_active_opportunities
        ):
            raise ValueError("coverage cell counts or identity are invalid")

    @property
    def coverage(self) -> float | None:
        if self.expected_active_opportunities == 0:
            return None
        return self.valid_targets / self.expected_active_opportunities

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "instrument_id": self.instrument_id,
            "block": self.block,
            "block_start": self.block_start.isoformat(),
            "block_end": self.block_end.isoformat(),
            "expected_active_opportunities": self.expected_active_opportunities,
            "valid_targets": self.valid_targets,
            "coverage": self.coverage,
        }


def _family_mapping(values: Mapping[FeatureFamily, object]) -> dict[str, JsonValue]:
    return {family.value: to_json_value(values[family]) for family in FeatureFamily}


def _validate_feature_ladder(
    feature_sets: tuple[FeatureSet, ...],
    decisions: Mapping[FeatureFamily, EligibilityDecision],
) -> None:
    local = (
        FeatureFamily.LOCAL_RETURNS,
        FeatureFamily.TIME_AVAILABILITY,
    )
    expected = [FeatureSet("L0", local)]
    local += (FeatureFamily.LOCAL_VOLATILITY_RANGE,)
    expected.append(FeatureSet("L1", local))
    if decisions[FeatureFamily.SPREAD].state is FeatureEligibility.ELIGIBLE:
        local += (FeatureFamily.SPREAD,)
        expected.append(FeatureSet("L2", local))
    if decisions[FeatureFamily.QUOTE_IMBALANCE].state is FeatureEligibility.ELIGIBLE:
        local += (FeatureFamily.QUOTE_IMBALANCE,)
        expected.append(FeatureSet("L3", local))
    expected.extend(
        (
            FeatureSet("P0", local),
            FeatureSet("P1", (*local, FeatureFamily.POOLED_CROSS_ASSET)),
        )
    )
    if feature_sets != tuple(expected):
        raise ValueError("feature sets must exactly follow the eligible cumulative R2 ladder")


def _eligibility_id(
    subject: str,
    state: FeatureEligibility,
    evidence_start: datetime,
    evidence_end: datetime,
    reason: str,
) -> str:
    return _hash_json(
        {
            "contract": "qtrad-r2-eligibility-evidence-v1",
            "subject": subject,
            "state": state.value,
            "evidence_start": evidence_start.isoformat(),
            "evidence_end": evidence_end.isoformat(),
            "reason": reason,
        }
    )


def _require_sha256(value: str, field: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} must be a lower-case SHA-256")


def _hash_json(value: object) -> str:
    import json

    return sha256(
        json.dumps(to_json_value(value), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
