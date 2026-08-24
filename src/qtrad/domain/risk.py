"""Provider-neutral ordered portfolio risk contracts for R3.B.

The module deliberately keeps numerical covariance work at the domain boundary:
inputs are immutable, provider-free return observations and outputs are an immutable
ordered risk state. A state is never substituted with a fallback covariance or cap.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from math import isfinite, sqrt
from typing import ClassVar

from qtrad.domain.time import require_utc

RISK_STATE_CONTRACT = "qtrad-ordered-risk-state-v1"
RISK_ESTIMATOR_CONTRACT = "qtrad-ledoit-wolf-risk-v1"
SUPPORTED_ESTIMATOR = "LEDOIT_WOLF"
DEFAULT_ESTIMATOR_VERSION = "qtrad-ledoit-wolf-pure-python-v1"
DEFAULT_SYMMETRY_TOLERANCE = 1e-12
DEFAULT_PSD_TOLERANCE = 1e-12
DEFAULT_FINITE_TOLERANCE = 0.0

FloatMatrix = tuple[tuple[float, ...], ...]
Position = tuple[float, ...]


def canonical_asset_order(asset_ids: Sequence[str]) -> tuple[str, ...]:
    """Return the only canonical order used for matrix positions."""
    ordered = tuple(sorted(asset_ids))
    if not ordered or any(not asset for asset in ordered):
        raise ValueError("risk asset order must contain non-empty identifiers")
    if len(set(ordered)) != len(ordered):
        raise ValueError("risk asset order must contain unique identifiers")
    return ordered


@dataclass(frozen=True, slots=True)
class RiskObservation:
    """One horizon-return observation and its causal availability time."""

    observed_at: datetime
    values: tuple[float | None, ...]
    available_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", tuple(self.values))
        require_utc(self.observed_at, "risk observation observed_at")
        if self.available_at is not None:
            require_utc(self.available_at, "risk observation available_at")
            if self.available_at < self.observed_at:
                raise ValueError("risk observation available_at precedes observed_at")
        if not self.values:
            raise ValueError("risk observation values must be non-empty")
        for value in self.values:
            if value is not None and not isfinite(float(value)):
                raise ValueError("risk observation values must be finite or None")

    @property
    def observation_time(self) -> datetime:
        return self.observed_at

    @property
    def availability_time(self) -> datetime:
        return self.available_at or self.observed_at

    @property
    def returns(self) -> tuple[float | None, ...]:
        return self.values

    @classmethod
    def from_mapping(
        cls,
        *,
        observed_at: datetime,
        values: Mapping[str, float | None],
        asset_order: Sequence[str],
        available_at: datetime | None = None,
    ) -> RiskObservation:
        ordered = canonical_asset_order(asset_order)
        if set(values) != set(ordered) or len(values) != len(ordered):
            raise ValueError("risk observation mapping keys do not match canonical asset order")
        return cls(
            observed_at=observed_at,
            values=tuple(values[asset] for asset in ordered),
            available_at=available_at,
        )


@dataclass(frozen=True, slots=True)
class RiskEstimatorConfig:
    """Frozen horizon, lookback, estimator and numerical-boundary policy."""

    horizon: timedelta
    lookback: timedelta
    maximum_age: timedelta
    estimator: str = SUPPORTED_ESTIMATOR
    estimator_version: str = DEFAULT_ESTIMATOR_VERSION
    return_unit: str = "LOG_RETURN"
    availability_policy: str = "AVAILABLE_BY_CUTOFF"
    minimum_observations: int = 2
    symmetry_tolerance: float = DEFAULT_SYMMETRY_TOLERANCE
    psd_tolerance: float = DEFAULT_PSD_TOLERANCE
    finite_tolerance: float = DEFAULT_FINITE_TOLERANCE

    def __post_init__(self) -> None:
        if self.horizon <= timedelta(0):
            raise ValueError("risk horizon must be positive")
        if self.lookback <= timedelta(0):
            raise ValueError("risk lookback must be positive")
        if self.maximum_age <= timedelta(0):
            raise ValueError("risk maximum_age must be positive")
        if self.estimator != SUPPORTED_ESTIMATOR:
            raise ValueError("unsupported risk estimator")
        if self.estimator_version != DEFAULT_ESTIMATOR_VERSION:
            raise ValueError("unsupported risk estimator version")
        if self.return_unit != "LOG_RETURN":
            raise ValueError("risk return_unit must be LOG_RETURN")
        if self.availability_policy != "AVAILABLE_BY_CUTOFF":
            raise ValueError("unsupported risk availability policy")
        if self.minimum_observations < 2:
            raise ValueError("risk minimum_observations must be at least two")
        for value, name in (
            (self.symmetry_tolerance, "risk symmetry_tolerance"),
            (self.psd_tolerance, "risk psd_tolerance"),
            (self.finite_tolerance, "risk finite_tolerance"),
        ):
            if not isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")

    @property
    def shrinkage_method(self) -> str:
        return self.estimator

    @property
    def unit(self) -> str:
        return self.return_unit


@dataclass(frozen=True, slots=True)
class RiskCaps:
    """All numeric caps consumed by a later portfolio kernel."""

    asset_caps: tuple[float, ...]
    gross_cap: float
    net_cap: float
    concentration_cap: float
    portfolio_risk_cap: float
    group_caps: tuple[float, ...] = ()
    currency_caps: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "asset_caps", tuple(self.asset_caps))
        object.__setattr__(self, "group_caps", tuple(self.group_caps))
        object.__setattr__(self, "currency_caps", tuple(self.currency_caps))
        if not self.asset_caps:
            raise ValueError("risk asset caps must be explicit and non-empty")
        for value, name in (
            (self.gross_cap, "gross_cap"),
            (self.net_cap, "net_cap"),
            (self.concentration_cap, "concentration_cap"),
            (self.portfolio_risk_cap, "portfolio_risk_cap"),
            *[(cap, "asset cap") for cap in self.asset_caps],
            *[(cap, "group cap") for cap in self.group_caps],
            *[(cap, "currency cap") for cap in self.currency_caps],
        ):
            if not isfinite(value) or value < 0:
                raise ValueError(f"risk {name} must be finite and non-negative")
        if self.concentration_cap > 1:
            raise ValueError("risk concentration_cap must not exceed one")

    def as_json(self) -> dict[str, object]:
        return {
            "asset_caps": self.asset_caps,
            "gross_cap": self.gross_cap,
            "net_cap": self.net_cap,
            "concentration_cap": self.concentration_cap,
            "portfolio_risk_cap": self.portfolio_risk_cap,
            "group_caps": self.group_caps,
            "currency_caps": self.currency_caps,
        }


@dataclass(frozen=True, slots=True)
class ExposureMapping:
    """Ordered group/currency exposure matrices and their caps."""

    group_keys: tuple[str, ...] = ()
    group_exposure_matrix: FloatMatrix = ()
    group_caps: tuple[float, ...] = ()
    currency_keys: tuple[str, ...] = ()
    currency_exposure_matrix: FloatMatrix = ()
    currency_caps: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "group_keys", tuple(self.group_keys))
        object.__setattr__(
            self,
            "group_exposure_matrix",
            tuple(tuple(row) for row in self.group_exposure_matrix),
        )
        object.__setattr__(self, "group_caps", tuple(self.group_caps))
        object.__setattr__(self, "currency_keys", tuple(self.currency_keys))
        object.__setattr__(
            self,
            "currency_exposure_matrix",
            tuple(tuple(row) for row in self.currency_exposure_matrix),
        )
        object.__setattr__(self, "currency_caps", tuple(self.currency_caps))
        _validate_mapping(self.group_keys, self.group_exposure_matrix, self.group_caps, "group")
        _validate_mapping(
            self.currency_keys,
            self.currency_exposure_matrix,
            self.currency_caps,
            "currency",
        )

    @property
    def group_matrix(self) -> FloatMatrix:
        return self.group_exposure_matrix

    @property
    def currency_matrix(self) -> FloatMatrix:
        return self.currency_exposure_matrix

    @property
    def ordered_group_keys(self) -> tuple[str, ...]:
        return self.group_keys

    @property
    def ordered_currency_keys(self) -> tuple[str, ...]:
        return self.currency_keys

@dataclass(frozen=True, slots=True)
class RiskState:
    """Immutable, ordered covariance and exposure state for one horizon."""

    asset_order: tuple[str, ...]
    horizon: timedelta
    as_of: datetime
    observation_cutoff: datetime
    lookback: timedelta
    maximum_age: timedelta
    availability_policy: str
    return_unit: str
    estimator: str
    estimator_version: str
    shrinkage: float
    covariance: FloatMatrix
    sample_count: int
    raw_observation_count: int
    missing_observation_count: int
    excluded_observation_count: int
    effective_observations: int
    symmetry_tolerance: float
    psd_tolerance: float
    finite_tolerance: float
    group_keys: tuple[str, ...]
    group_exposure_matrix: FloatMatrix
    group_caps: tuple[float, ...]
    currency_keys: tuple[str, ...]
    currency_exposure_matrix: FloatMatrix
    currency_caps: tuple[float, ...]
    caps: RiskCaps
    provenance: str
    semantic_identity: str | None = None
    closure_identity: str | None = None
    provenance_identity: str | None = None

    CONTRACT: ClassVar[str] = RISK_STATE_CONTRACT

    def __post_init__(self) -> None:
        object.__setattr__(self, "asset_order", tuple(self.asset_order))
        object.__setattr__(
            self,
            "covariance",
            tuple(tuple(row) for row in self.covariance),
        )
        object.__setattr__(self, "group_keys", tuple(self.group_keys))
        object.__setattr__(
            self,
            "group_exposure_matrix",
            tuple(tuple(row) for row in self.group_exposure_matrix),
        )
        object.__setattr__(self, "group_caps", tuple(self.group_caps))
        object.__setattr__(self, "currency_keys", tuple(self.currency_keys))
        object.__setattr__(
            self,
            "currency_exposure_matrix",
            tuple(tuple(row) for row in self.currency_exposure_matrix),
        )
        object.__setattr__(self, "currency_caps", tuple(self.currency_caps))
        ordered = canonical_asset_order(self.asset_order)
        if ordered != self.asset_order:
            raise ValueError("risk asset_order is not canonical")
        require_utc(self.as_of, "risk as_of")
        require_utc(self.observation_cutoff, "risk observation_cutoff")
        if self.observation_cutoff > self.as_of:
            raise ValueError("risk observation_cutoff is after as_of")
        if (
            self.horizon <= timedelta(0)
            or self.lookback <= timedelta(0)
            or self.maximum_age <= timedelta(0)
        ):
            raise ValueError("risk horizon, lookback and maximum_age must be positive")
        if self.as_of - self.observation_cutoff > self.maximum_age:
            raise ValueError("risk observation cutoff exceeds maximum age")
        for value, name in (
            (self.shrinkage, "risk shrinkage"),
            (self.symmetry_tolerance, "risk symmetry_tolerance"),
            (self.psd_tolerance, "risk psd_tolerance"),
            (self.finite_tolerance, "risk finite_tolerance"),
        ):
            if not isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if self.estimator != SUPPORTED_ESTIMATOR:
            raise ValueError("unsupported risk estimator")
        if self.estimator_version != DEFAULT_ESTIMATOR_VERSION:
            raise ValueError("unsupported risk estimator version")
        if self.availability_policy != "AVAILABLE_BY_CUTOFF":
            raise ValueError("unsupported risk availability policy")
        if self.return_unit != "LOG_RETURN":
            raise ValueError("risk return_unit must be LOG_RETURN")
        n = len(self.asset_order)
        _validate_square_matrix(
            self.covariance,
            n,
            "risk covariance",
            self.symmetry_tolerance,
            self.psd_tolerance,
        )
        _validate_mapping(
            self.group_keys,
            self.group_exposure_matrix,
            self.group_caps,
            "group",
            asset_count=n,
        )
        _validate_mapping(
            self.currency_keys,
            self.currency_exposure_matrix,
            self.currency_caps,
            "currency",
            asset_count=n,
        )
        if len(self.caps.asset_caps) != n:
            raise ValueError("risk asset caps do not match asset order")
        if self.caps.group_caps != self.group_caps or self.caps.currency_caps != self.currency_caps:
            raise ValueError("risk caps do not match exposure mapping caps")
        if self.sample_count < 2 or self.effective_observations != self.sample_count:
            raise ValueError("risk state has insufficient or inconsistent observations")
        if self.raw_observation_count < self.sample_count:
            raise ValueError("risk raw observation count is below sample count")
        if (
            self.missing_observation_count < 0
            or self.excluded_observation_count < 0
            or self.missing_observation_count + self.sample_count > self.raw_observation_count
        ):
            raise ValueError("risk observation exclusion metadata is inconsistent")
        if not self.provenance:
            raise ValueError("risk provenance is required")

        expected_semantic = _hash_json(self._semantic_payload())
        expected_closure = _hash_json(
            {
                "contract": self.CONTRACT,
                "semantic_identity": expected_semantic,
                "closure": self._closure_payload(),
            }
        )
        expected_provenance = _hash_json(
            {
                "contract": self.CONTRACT,
                "provenance": self.provenance,
                "estimator_version": self.estimator_version,
            }
        )
        for provided, expected, name in (
            (self.semantic_identity, expected_semantic, "semantic"),
            (self.closure_identity, expected_closure, "closure"),
            (self.provenance_identity, expected_provenance, "provenance"),
        ):
            if provided is not None and provided != expected:
                raise ValueError(f"risk {name} identity does not match its content")
        object.__setattr__(self, "semantic_identity", expected_semantic)
        object.__setattr__(self, "closure_identity", expected_closure)
        object.__setattr__(self, "provenance_identity", expected_provenance)

    @property
    def semantic_id(self) -> str:
        return self.semantic_identity or ""

    @property
    def closure_id(self) -> str:
        return self.closure_identity or ""

    @property
    def provenance_id(self) -> str:
        return self.provenance_identity or ""

    @property
    def covariance_matrix(self) -> FloatMatrix:
        return self.covariance

    @property
    def risk_covariance(self) -> FloatMatrix:
        return self.covariance

    @property
    def missing_count(self) -> int:
        return self.missing_observation_count

    @property
    def n_samples(self) -> int:
        return self.sample_count

    def _semantic_payload(self) -> dict[str, object]:
        return {
            "contract": self.CONTRACT,
            "schema_version": 1,
            "asset_order": self.asset_order,
            "horizon_seconds": self.horizon.total_seconds(),
            "as_of": self.as_of,
            "observation_cutoff": self.observation_cutoff,
            "lookback_seconds": self.lookback.total_seconds(),
            "maximum_age_seconds": self.maximum_age.total_seconds(),
            "availability_policy": self.availability_policy,
            "return_unit": self.return_unit,
            "estimator": self.estimator,
            "estimator_version": self.estimator_version,
            "shrinkage": self.shrinkage,
            "covariance": self.covariance,
            "sample_count": self.sample_count,
            "raw_observation_count": self.raw_observation_count,
            "missing_observation_count": self.missing_observation_count,
            "excluded_observation_count": self.excluded_observation_count,
            "effective_observations": self.effective_observations,
            "symmetry_tolerance": self.symmetry_tolerance,
            "psd_tolerance": self.psd_tolerance,
            "finite_tolerance": self.finite_tolerance,
            "group_keys": self.group_keys,
            "group_exposure_matrix": self.group_exposure_matrix,
            "group_caps": self.group_caps,
            "currency_keys": self.currency_keys,
            "currency_exposure_matrix": self.currency_exposure_matrix,
            "currency_caps": self.currency_caps,
            "caps": self.caps.as_json(),
        }

    def _closure_payload(self) -> dict[str, object]:
        return {"semantic_payload": self._semantic_payload(), "provenance": self.provenance}

    def as_json(self) -> dict[str, object]:
        return {
            **self._semantic_payload(),
            "semantic_identity": self.semantic_id,
            "closure_identity": self.closure_id,
            "provenance": self.provenance,
            "provenance_identity": self.provenance_id,
        }

    def _position(self, position: Sequence[float]) -> Position:
        if len(position) != len(self.asset_order):
            raise ValueError("position length does not match risk asset order")
        values = tuple(float(value) for value in position)
        if any(not isfinite(value) for value in values):
            raise ValueError("position must contain finite values")
        return values

    def asset_exposure(self, position: Sequence[float]) -> Position:
        return self._position(position)

    def group_exposure(self, position: Sequence[float]) -> Position:
        values = self._position(position)
        return tuple(
            sum(row[index] * values[index] for index in range(len(values)))
            for row in self.group_exposure_matrix
        )

    def currency_exposure(self, position: Sequence[float]) -> Position:
        values = self._position(position)
        return tuple(
            sum(row[index] * values[index] for index in range(len(values)))
            for row in self.currency_exposure_matrix
        )

    def portfolio_variance(self, position: Sequence[float]) -> float:
        values = self._position(position)
        return float(
            sum(
                values[row] * self.covariance[row][column] * values[column]
                for row in range(len(values))
                for column in range(len(values))
            )
        )

    def portfolio_risk(self, position: Sequence[float]) -> float:
        variance = self.portfolio_variance(position)
        if variance < -self.psd_tolerance:
            raise ValueError("risk covariance produced a materially negative variance")
        return sqrt(max(variance, 0.0))

    def gross_exposure(self, position: Sequence[float]) -> float:
        return sum(abs(value) for value in self._position(position))

    def net_exposure(self, position: Sequence[float]) -> float:
        return abs(sum(self._position(position)))

    def concentration(self, position: Sequence[float]) -> float:
        values = self._position(position)
        gross = sum(abs(value) for value in values)
        return 0.0 if gross == 0 else max(abs(value) for value in values) / gross

    def validate_position(self, position: Sequence[float]) -> None:
        values = self._position(position)
        tolerance = self.finite_tolerance
        if any(
            abs(value) > cap + tolerance
            for value, cap in zip(values, self.caps.asset_caps, strict=True)
        ):
            raise ValueError("position exceeds an asset cap")
        if self.gross_exposure(values) > self.caps.gross_cap + tolerance:
            raise ValueError("position exceeds gross cap")
        if self.net_exposure(values) > self.caps.net_cap + tolerance:
            raise ValueError("position exceeds net cap")
        if self.concentration(values) > self.caps.concentration_cap + tolerance:
            raise ValueError("position exceeds concentration cap")
        if any(
            abs(exposure) > cap + tolerance
            for exposure, cap in zip(self.group_exposure(values), self.group_caps, strict=True)
        ):
            raise ValueError("position exceeds group cap")
        if any(
            abs(exposure) > cap + tolerance
            for exposure, cap in zip(
                self.currency_exposure(values), self.currency_caps, strict=True
            )
        ):
            raise ValueError("position exceeds currency cap")
        if self.portfolio_risk(values) > self.caps.portfolio_risk_cap + tolerance:
            raise ValueError("position exceeds portfolio risk cap")

    def position_is_valid(self, position: Sequence[float]) -> bool:
        try:
            self.validate_position(position)
        except (TypeError, ValueError, OverflowError):
            return False
        return True

    is_position_valid = position_is_valid


def estimate_ordered_risk_state(
    *,
    asset_order: Sequence[str],
    observations: Sequence[RiskObservation],
    as_of: datetime,
    observation_cutoff: datetime,
    config: RiskEstimatorConfig,
    exposure_mapping: ExposureMapping,
    caps: RiskCaps,
    provenance: str,
) -> RiskState:
    """Estimate a causal horizon-specific Ledoit-Wolf covariance state.

    Only complete observations whose observed and available times are within the
    configured lookback and at or before observation_cutoff are consumed.
    """
    ordered = tuple(asset_order)
    if canonical_asset_order(ordered) != ordered:
        raise ValueError("risk asset_order must be canonical")
    require_utc(as_of, "risk as_of")
    require_utc(observation_cutoff, "risk observation_cutoff")
    if observation_cutoff > as_of:
        raise ValueError("risk observation_cutoff is after as_of")
    if as_of - observation_cutoff > config.maximum_age:
        raise ValueError("risk observation cutoff exceeds maximum age")
    n = len(ordered)
    selected: list[RiskObservation] = []
    missing_count = 0
    for observation in observations:
        if len(observation.values) != n:
            raise ValueError("risk observation width does not match asset order")
        if (
            observation.observed_at > observation_cutoff
            or observation.availability_time > observation_cutoff
        ):
            continue
        if observation.observed_at < observation_cutoff - config.lookback:
            continue
        if any(value is None for value in observation.values):
            missing_count += 1
            continue
        selected.append(observation)
    selected.sort(key=lambda item: (item.availability_time, item.observed_at, item.values))
    if len(selected) < config.minimum_observations:
        raise ValueError("risk state has insufficient complete observations at the causal cutoff")
    matrix = tuple(_complete_values(item.values) for item in selected)
    if len(matrix) != len(selected) or any(
        len(row) != n or any(not isfinite(value) for value in row) for row in matrix
    ):
        raise ValueError("risk observations produce a non-finite numerical matrix")
    covariance, shrinkage = _ledoit_wolf(matrix)
    excluded_count = len(observations) - len(selected)
    return RiskState(
        asset_order=ordered,
        horizon=config.horizon,
        as_of=as_of,
        observation_cutoff=observation_cutoff,
        lookback=config.lookback,
        maximum_age=config.maximum_age,
        availability_policy=config.availability_policy,
        return_unit=config.return_unit,
        estimator=config.estimator,
        estimator_version=config.estimator_version,
        shrinkage=shrinkage,
        covariance=covariance,
        sample_count=len(selected),
        raw_observation_count=len(observations),
        missing_observation_count=missing_count,
        excluded_observation_count=excluded_count,
        effective_observations=len(selected),
        symmetry_tolerance=config.symmetry_tolerance,
        psd_tolerance=config.psd_tolerance,
        finite_tolerance=config.finite_tolerance,
        group_keys=exposure_mapping.group_keys,
        group_exposure_matrix=exposure_mapping.group_exposure_matrix,
        group_caps=exposure_mapping.group_caps,
        currency_keys=exposure_mapping.currency_keys,
        currency_exposure_matrix=exposure_mapping.currency_exposure_matrix,
        currency_caps=exposure_mapping.currency_caps,
        caps=caps,
        provenance=provenance,
    )


estimate_risk_state = estimate_ordered_risk_state
OrderedRiskState = RiskState



def _ledoit_wolf(matrix: FloatMatrix) -> tuple[FloatMatrix, float]:
    """Calculate a centered Ledoit-Wolf covariance without model libraries."""
    sample_count = len(matrix)
    if sample_count < 2:
        raise ValueError("risk estimator requires at least two observations")
    feature_count = len(matrix[0])
    if feature_count == 0 or any(len(row) != feature_count for row in matrix):
        raise ValueError("risk estimator matrix has invalid shape")
    means = tuple(
        sum(row[index] for row in matrix) / sample_count
        for index in range(feature_count)
    )
    centered = tuple(
        tuple(row[index] - means[index] for index in range(feature_count))
        for row in matrix
    )
    empirical = tuple(
        tuple(
            sum(row[row_index] * row[column_index] for row in centered)
            / sample_count
            for column_index in range(feature_count)
        )
        for row_index in range(feature_count)
    )
    if feature_count == 1:
        return empirical, 0.0
    squared = tuple(tuple(value * value for value in row) for row in centered)
    trace_by_feature = tuple(
        sum(row[index] for row in squared) / sample_count
        for index in range(feature_count)
    )
    mu = sum(trace_by_feature) / feature_count
    beta_sum = sum(
        squared[row_index][feature_index] * squared[row_index][column_index]
        for row_index in range(sample_count)
        for feature_index in range(feature_count)
        for column_index in range(feature_count)
    )
    gram_square_sum = sum(
        (
            sum(
                row[feature_index] * row[column_index]
                for row in centered
            )
        )
        ** 2
        for feature_index in range(feature_count)
        for column_index in range(feature_count)
    )
    delta_estimate = gram_square_sum / (sample_count**2)
    beta = (
        beta_sum / sample_count - delta_estimate
    ) / (feature_count * sample_count)
    beta = min(beta, delta_estimate)
    delta = (
        delta_estimate
        - 2.0 * mu * sum(trace_by_feature)
        + feature_count * mu * mu
    ) / feature_count
    shrinkage = 0.0 if beta <= 0.0 or delta <= 0.0 else min(1.0, beta / delta)
    covariance = tuple(
        tuple(
            (1.0 - shrinkage) * empirical[row_index][column_index]
            + (
                shrinkage * mu
                if row_index == column_index
                else 0.0
            )
            for column_index in range(feature_count)
        )
        for row_index in range(feature_count)
    )
    if any(
        not isfinite(value)
        for row in covariance
        for value in row
    ) or not isfinite(shrinkage):
        raise ValueError("risk estimator produced an invalid covariance")
    return covariance, shrinkage


def _is_positive_semidefinite(matrix: FloatMatrix, tolerance: float) -> bool:
    """Check PSD with a tolerance using a dependency-free Cholesky factor."""
    size = len(matrix)
    factor = [[0.0 for _ in range(size)] for _ in range(size)]
    for row in range(size):
        for column in range(row + 1):
            residual = matrix[row][column] - sum(
                factor[row][index] * factor[column][index]
                for index in range(column)
            )
            if row == column:
                if residual < -tolerance:
                    return False
                factor[row][column] = sqrt(max(residual, 0.0))
            elif factor[column][column] > tolerance:
                factor[row][column] = residual / factor[column][column]
            elif abs(residual) > tolerance:
                return False
    return True

def _validate_mapping(
    keys: tuple[str, ...],
    matrix: FloatMatrix,
    caps: tuple[float, ...],
    label: str,
    *,
    asset_count: int | None = None,
) -> None:
    if tuple(sorted(keys)) != keys or len(set(keys)) != len(keys) or any(not key for key in keys):
        raise ValueError(f"{label} keys must be unique and canonical")
    if len(matrix) != len(keys) or len(caps) != len(keys):
        raise ValueError(f"{label} mapping and caps must have matching lengths")
    width = asset_count if asset_count is not None else (len(matrix[0]) if matrix else None)
    for row in matrix:
        if width is None or len(row) != width:
            raise ValueError(f"{label} exposure matrix has inconsistent width")
        if any(not isfinite(float(value)) for value in row):
            raise ValueError(f"{label} exposure matrix must be finite")
    for cap in caps:
        if not isfinite(cap) or cap < 0:
            raise ValueError(f"{label} caps must be finite and non-negative")


def _validate_square_matrix(
    matrix: FloatMatrix,
    size: int,
    label: str,
    symmetry_tolerance: float,
    psd_tolerance: float,
) -> None:
    if len(matrix) != size or any(len(row) != size for row in matrix):
        raise ValueError(f"{label} shape does not match asset order")
    if any(not isfinite(float(value)) for row in matrix for value in row):
        raise ValueError(f"{label} must be finite")
    if any(
        abs(matrix[row][column] - matrix[column][row]) > symmetry_tolerance
        for row in range(size)
        for column in range(size)
    ):
        raise ValueError(f"{label} is not symmetric within tolerance")
    if not _is_positive_semidefinite(matrix, psd_tolerance):
        raise ValueError(f"{label} is not positive semidefinite within tolerance")



def _complete_values(values: tuple[float | None, ...]) -> tuple[float, ...]:
    if any(value is None for value in values):
        raise ValueError("risk observation is incomplete")
    return tuple(float(value) for value in values if value is not None)


def _hash_json(value: object) -> str:
    def default(item: object) -> str:
        if isinstance(item, datetime):
            require_utc(item, "risk identity datetime")
            return item.isoformat().replace("+00:00", "Z")
        raise TypeError(f"unsupported risk identity value: {type(item).__name__}")

    encoded = json.dumps(
        value,
        default=default,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return sha256(encoded.encode("utf-8")).hexdigest()
