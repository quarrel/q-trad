"""Causal foundation configuration, panels and frozen midpoint targets."""

import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import InitVar, dataclass
from dataclasses import field as dataclass_field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from math import isfinite
from typing import ClassVar, cast
from uuid import UUID

from qtrad.domain.events import JsonValue, to_json_value
from qtrad.domain.market_data import DataQuality, PriceBasis
from qtrad.domain.time import require_utc


class InstrumentRole(StrEnum):
    TARGET = "TARGET"
    CONTEXT = "CONTEXT"


class AvailabilityBasis(StrEnum):
    RECEIVED_AT = "received_at"
    PERSISTED_AT = "persisted_at"


class PanelStatus(StrEnum):
    OBSERVED = "OBSERVED"
    MISSING_AS_OF_CUTOFF = "MISSING_AS_OF_CUTOFF"


class PanelAuditDisposition(StrEnum):
    EVENTUALLY_OBSERVED_LATE = "EVENTUALLY_OBSERVED_LATE"
    RECORDED_GAP_KNOWN_BY_CUTOFF = "RECORDED_GAP_KNOWN_BY_CUTOFF"
    RECORDED_GAP_DETECTED_LATER = "RECORDED_GAP_DETECTED_LATER"
    SOURCE_NOT_ACTIVE = "SOURCE_NOT_ACTIVE"
    NO_NATIVE_EVIDENCE = "NO_NATIVE_EVIDENCE"
    AMBIGUOUS_OR_INVALID_SOURCE = "AMBIGUOUS_OR_INVALID_SOURCE"


class ReturnDisposition(StrEnum):
    VALID = "VALID"
    MISSING_START = "MISSING_START"
    MISSING_END = "MISSING_END"
    NON_POSITIVE_START = "NON_POSITIVE_START"
    NON_POSITIVE_END = "NON_POSITIVE_END"
    AMBIGUOUS_SOURCE = "AMBIGUOUS_SOURCE"
    UNAVAILABLE_BY_FREEZE = "UNAVAILABLE_BY_FREEZE"


class ExcursionDisposition(StrEnum):
    VALID = "VALID"
    INCOMPLETE_PATH = "INCOMPLETE_PATH"
    MISSING_START = "MISSING_START"
    MISSING_END = "MISSING_END"
    AMBIGUOUS_SOURCE = "AMBIGUOUS_SOURCE"


FOUNDATION_CONFIG_CONTRACT = "qtrad-research-foundation-config-v1"
PANEL_DATASET_CONTRACT = "qtrad-research-panel-v1"
TARGET_DATASET_CONTRACT = "qtrad-research-targets-v1"
_MINUTE = timedelta(minutes=1)
_HORIZONS = frozenset(timedelta(minutes=minutes) for minutes in (5, 15, 30, 60))
_PANEL_DATASET_VERIFIED = object()
_TARGET_DATASET_VERIFIED = object()


@dataclass(frozen=True, slots=True)
class FoundationConfig:
    """Strict, identity-bearing configuration for the first causal vertical path."""

    name: str
    schema_version: int
    observation_dataset_id: str
    ordered_instruments: tuple[str, ...]
    instrument_roles: Mapping[str, InstrumentRole]
    range_start: datetime
    range_end: datetime
    grid_resolution: timedelta
    availability_basis: AvailabilityBasis
    feature_lag_policy: str
    feature_lag_calibration_range: tuple[datetime, datetime]
    feature_lag_percentile: float
    feature_lag_safety_margin: timedelta
    selected_feature_lag: timedelta
    target_horizons: tuple[timedelta, ...]
    primary_vertical_horizon: timedelta
    target_revision_delay: timedelta
    target_revision_policy: str
    target_revision_policy_reason: str | None
    required_feature_bases: tuple[PriceBasis, ...]
    target_basis: PriceBasis
    fold_policy: str
    holdout_range: tuple[datetime, datetime]
    embargo: timedelta
    minimum_training_duration: timedelta
    minimum_validation_duration: timedelta

    CONTRACT: ClassVar[str] = FOUNDATION_CONFIG_CONTRACT
    SCHEMA_VERSION: ClassVar[int] = 1

    def __post_init__(self) -> None:
        if not self.name or self.schema_version != 1:
            raise ValueError("foundation configuration name and schema version are invalid")
        _require_sha256(self.observation_dataset_id, "observation dataset ID")
        if not self.ordered_instruments:
            raise ValueError("foundation configuration requires instruments")
        if len(set(self.ordered_instruments)) != len(self.ordered_instruments):
            raise ValueError("foundation instruments must be unique")
        if set(self.instrument_roles) != set(self.ordered_instruments):
            raise ValueError("foundation roles must exactly match the instrument universe")
        for instrument_id in self.ordered_instruments:
            if (
                not instrument_id
                or ":" not in instrument_id
                or instrument_id != instrument_id.lower()
            ):
                raise ValueError("foundation instrument IDs must be canonical lower-case values")
        if (
            "index:volatility" in self.instrument_roles
            and InstrumentRole(self.instrument_roles["index:volatility"]) is InstrumentRole.TARGET
        ):
            raise ValueError("VIX must remain a CONTEXT instrument")
        _require_interval(self.range_start, self.range_end, "foundation range")
        if self.grid_resolution != _MINUTE:
            raise ValueError("R1 foundation grid resolution must be one minute")
        _require_interval(
            self.feature_lag_calibration_range[0],
            self.feature_lag_calibration_range[1],
            "feature lag calibration range",
        )
        if self.feature_lag_calibration_range[1] > self.range_start:
            raise ValueError("feature lag calibration must end by the decision range start")
        _require_interval(self.holdout_range[0], self.holdout_range[1], "holdout range")
        if self.holdout_range[0] < self.range_start or self.holdout_range[1] != self.range_end:
            raise ValueError("locked holdout must be the final interval of the foundation range")
        if not 0 <= self.feature_lag_percentile <= 1:
            raise ValueError("feature lag percentile must be between zero and one")
        if self.feature_lag_policy not in {"MEASURED", "PROVISIONAL_CONSERVATIVE"}:
            raise ValueError("feature lag policy is unsupported")
        if self.target_revision_policy not in {"MEASURED", "PROVISIONAL_CONSERVATIVE"}:
            raise ValueError("target revision policy is unsupported")
        if (
            self.target_revision_policy == "MEASURED"
            and self.target_revision_policy_reason is not None
        ):
            raise ValueError("measured target revision policy cannot have a provisional reason")
        if self.target_revision_policy == "PROVISIONAL_CONSERVATIVE" and (
            self.target_revision_policy_reason is None
            or not self.target_revision_policy_reason.strip()
        ):
            raise ValueError("provisional target revision policy requires a reason")
        for value, field in (
            (self.feature_lag_safety_margin, "feature lag safety margin"),
            (self.selected_feature_lag, "selected feature lag"),
            (self.target_revision_delay, "target revision delay"),
            (self.embargo, "embargo"),
        ):
            if value < timedelta(0):
                raise ValueError(f"{field} must not be negative")
        for value, field in (
            (self.selected_feature_lag, "selected feature lag"),
            (self.target_revision_delay, "target revision delay"),
        ):
            if value.total_seconds() % self.grid_resolution.total_seconds() != 0:
                raise ValueError(f"{field} must use whole grid units")
        if not self.target_horizons or any(
            horizon not in _HORIZONS for horizon in self.target_horizons
        ):
            raise ValueError("target horizons must use the configured 5/15/30/60-minute grid")
        if len(set(self.target_horizons)) != len(self.target_horizons):
            raise ValueError("target horizons must be unique")
        if self.target_horizons != tuple(sorted(self.target_horizons)):
            raise ValueError("target horizons must use canonical ascending order")
        if self.primary_vertical_horizon != timedelta(minutes=15):
            raise ValueError("R1.B primary vertical horizon must be 15 minutes")
        if self.primary_vertical_horizon not in self.target_horizons:
            raise ValueError("primary vertical horizon must be configured")
        if (
            self.required_feature_bases != (PriceBasis.MID,)
            or self.target_basis is not PriceBasis.MID
        ):
            raise ValueError("R1.B requires MID-only features and targets")
        if self.fold_policy != "EXPANDING_WALK_FORWARD":
            raise ValueError("foundation fold policy is unsupported")
        if self.minimum_training_duration <= timedelta(0):
            raise ValueError("minimum training duration must be positive")
        if self.minimum_validation_duration <= timedelta(0):
            raise ValueError("minimum validation duration must be positive")

    @property
    def configuration_id(self) -> str:
        return _hash_json(self.as_json())

    @property
    def required_observation_start(self) -> datetime:
        """Earliest bar start needed by the configured decision range."""

        return min(
            self.feature_lag_calibration_range[0],
            self.range_start - self.selected_feature_lag - self.grid_resolution,
        )

    @property
    def required_observation_end(self) -> datetime:
        """Conservative source bound covering labels and their revision freeze."""

        return self.range_end + max(self.target_horizons) + self.target_revision_delay

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": self.schema_version,
            "name": self.name,
            "observation_dataset_id": self.observation_dataset_id,
            "ordered_instruments": list(self.ordered_instruments),
            "instrument_roles": {
                instrument_id: _role(self.instrument_roles[instrument_id])
                for instrument_id in self.ordered_instruments
            },
            "range_start": self.range_start.isoformat(),
            "range_end": self.range_end.isoformat(),
            "grid_resolution_seconds": self.grid_resolution.total_seconds(),
            "availability_basis": _availability_basis(self.availability_basis),
            "feature_lag_policy": self.feature_lag_policy,
            "feature_lag_calibration_range": [
                self.feature_lag_calibration_range[0].isoformat(),
                self.feature_lag_calibration_range[1].isoformat(),
            ],
            "feature_lag_percentile": self.feature_lag_percentile,
            "feature_lag_safety_margin_seconds": self.feature_lag_safety_margin.total_seconds(),
            "selected_feature_lag_seconds": self.selected_feature_lag.total_seconds(),
            "target_horizons_seconds": [
                horizon.total_seconds() for horizon in self.target_horizons
            ],
            "primary_vertical_horizon_seconds": self.primary_vertical_horizon.total_seconds(),
            "target_revision_delay_seconds": self.target_revision_delay.total_seconds(),
            "target_revision_policy": self.target_revision_policy,
            "target_revision_policy_reason": self.target_revision_policy_reason,
            "required_feature_bases": [basis.value for basis in self.required_feature_bases],
            "target_basis": self.target_basis.value,
            "fold_policy": self.fold_policy,
            "holdout_range": [self.holdout_range[0].isoformat(), self.holdout_range[1].isoformat()],
            "embargo_seconds": self.embargo.total_seconds(),
            "minimum_training_duration_seconds": self.minimum_training_duration.total_seconds(),
            "minimum_validation_duration_seconds": self.minimum_validation_duration.total_seconds(),
        }


@dataclass(frozen=True, slots=True)
class PanelRow:
    """One causal panel cell; audit disposition is retrospective metadata."""

    decision_time: datetime
    instrument_id: str
    basis: PriceBasis
    feature_data_asof: datetime
    latest_feature_bar_end: datetime
    status: PanelStatus
    audit_disposition: PanelAuditDisposition | None
    selected_event_id: UUID | None
    selected_stream_version: int | None
    selected_global_position: int | None
    selected_availability_time: datetime | None
    selected_revision: int | None
    interval_start: datetime | None
    interval_end: datetime | None
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    sample_count: int | None
    quality: DataQuality | None

    CONTRACT: ClassVar[str] = PANEL_DATASET_CONTRACT

    def __post_init__(self) -> None:
        require_utc(self.decision_time, "panel decision_time")
        require_utc(self.feature_data_asof, "panel feature_data_asof")
        require_utc(self.latest_feature_bar_end, "panel latest_feature_bar_end")
        if self.latest_feature_bar_end > self.feature_data_asof:
            raise ValueError("panel latest bar end cannot follow its feature cutoff")
        if self.status is PanelStatus.OBSERVED:
            if self.selected_event_id is None or self.selected_availability_time is None:
                raise ValueError("observed panel rows require selected lineage")
            if (
                self.selected_availability_time > self.feature_data_asof
                or self.interval_end != self.latest_feature_bar_end
            ):
                raise ValueError("observed panel row violates its causal feature cutoff")
        if self.status is PanelStatus.MISSING_AS_OF_CUTOFF:
            if any(value is not None for value in (self.open, self.high, self.low, self.close)):
                raise ValueError("missing panel rows cannot contain prices")
            if self.audit_disposition is None:
                raise ValueError("missing panel rows require retrospective disposition")

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "decision_time": self.decision_time.isoformat(),
            "instrument_id": self.instrument_id,
            "basis": self.basis.value,
            "feature_data_asof": self.feature_data_asof.isoformat(),
            "latest_feature_bar_end": self.latest_feature_bar_end.isoformat(),
            "status": self.status.value,
            "audit_disposition": self.audit_disposition.value if self.audit_disposition else None,
            "selected_event_id": str(self.selected_event_id) if self.selected_event_id else None,
            "selected_stream_version": self.selected_stream_version,
            "selected_global_position": self.selected_global_position,
            "selected_availability_time": (
                self.selected_availability_time.isoformat()
                if self.selected_availability_time
                else None
            ),
            "selected_revision": self.selected_revision,
            "interval_start": self.interval_start.isoformat() if self.interval_start else None,
            "interval_end": self.interval_end.isoformat() if self.interval_end else None,
            "open": str(self.open) if self.open is not None else None,
            "high": str(self.high) if self.high is not None else None,
            "low": str(self.low) if self.low is not None else None,
            "close": str(self.close) if self.close is not None else None,
            "sample_count": self.sample_count,
            "quality": self.quality.value if self.quality else None,
        }


@dataclass(frozen=True, slots=True)
class PanelDataset:
    rows: tuple[PanelRow, ...]
    observation_dataset_id: str
    foundation_configuration_id: str
    dataset_id: str
    _verified: InitVar[object | None] = None

    def __post_init__(self, _verified: object | None) -> None:
        if _verified is _PANEL_DATASET_VERIFIED:
            return
        _validate_panel_rows(self.rows)
        expected = _panel_dataset_identity(
            self.rows,
            observation_dataset_id=self.observation_dataset_id,
            foundation_configuration_id=self.foundation_configuration_id,
        )
        if self.dataset_id != expected:
            raise ValueError("panel dataset ID does not match its semantic rows")

    @classmethod
    def create(
        cls,
        rows: Sequence[PanelRow],
        *,
        observation_dataset_id: str,
        foundation_configuration_id: str,
    ) -> "PanelDataset":
        ordered = tuple(sorted(rows, key=_panel_key))
        _validate_panel_rows(ordered)
        dataset_id = _panel_dataset_identity(
            ordered,
            observation_dataset_id=observation_dataset_id,
            foundation_configuration_id=foundation_configuration_id,
        )
        return cls(
            rows=ordered,
            observation_dataset_id=observation_dataset_id,
            foundation_configuration_id=foundation_configuration_id,
            dataset_id=dataset_id,
            _verified=_PANEL_DATASET_VERIFIED,
        )


@dataclass(frozen=True, slots=True)
class TargetRow:
    """One frozen midpoint endpoint label and independent path excursion state."""

    instrument_id: str
    decision_time: datetime
    horizon: timedelta
    target_basis: PriceBasis
    target_revision_policy: str
    target_start_time: datetime
    target_end_time: datetime
    target_freeze_at: datetime
    target_available_at: datetime
    label_start_close: Decimal | None
    label_end_close: Decimal | None
    log_return: float | None
    return_disposition: ReturnDisposition
    start_event_id: UUID | None
    end_event_id: UUID | None
    upper_log_excursion: float | None
    lower_log_excursion: float | None
    excursion_disposition: ExcursionDisposition
    _target_id: str = dataclass_field(init=False, repr=False, compare=False)

    CONTRACT: ClassVar[str] = TARGET_DATASET_CONTRACT

    @property
    def target_id(self) -> str:
        return self._target_id

    def __post_init__(self) -> None:
        for value, field in (
            (self.decision_time, "target decision_time"),
            (self.target_start_time, "target start_time"),
            (self.target_end_time, "target end_time"),
            (self.target_freeze_at, "target freeze_at"),
            (self.target_available_at, "target availability"),
        ):
            require_utc(value, field)
        if (
            self.horizon <= timedelta(0)
            or self.target_end_time != self.target_start_time + self.horizon
        ):
            raise ValueError("target horizon and endpoint interval are inconsistent")
        if self.target_available_at != self.target_freeze_at:
            raise ValueError("target availability must equal its freeze time")
        if self.return_disposition is ReturnDisposition.VALID and (
            self.log_return is None or not isfinite(self.log_return)
        ):
            raise ValueError("valid targets require a finite log return")
        if self.excursion_disposition is ExcursionDisposition.VALID:
            if self.upper_log_excursion is None or self.lower_log_excursion is None:
                raise ValueError("valid excursions require both path values")
            if not isfinite(self.upper_log_excursion) or not isfinite(self.lower_log_excursion):
                raise ValueError("excursions must be finite")
        object.__setattr__(
            self,
            "_target_id",
            target_identity(
                instrument_id=self.instrument_id,
                decision_time=self.decision_time,
                horizon=self.horizon,
                target_basis=self.target_basis,
                target_revision_policy=self.target_revision_policy,
            ),
        )

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "instrument_id": self.instrument_id,
            "decision_time": self.decision_time.isoformat(),
            "horizon_seconds": self.horizon.total_seconds(),
            "target_basis": self.target_basis.value,
            "target_revision_policy": self.target_revision_policy,
            "target_start_time": self.target_start_time.isoformat(),
            "target_end_time": self.target_end_time.isoformat(),
            "target_freeze_at": self.target_freeze_at.isoformat(),
            "target_available_at": self.target_available_at.isoformat(),
            "label_start_close": (
                str(self.label_start_close) if self.label_start_close is not None else None
            ),
            "label_end_close": (
                str(self.label_end_close) if self.label_end_close is not None else None
            ),
            "log_return": self.log_return,
            "return_disposition": self.return_disposition.value,
            "start_event_id": str(self.start_event_id) if self.start_event_id else None,
            "end_event_id": str(self.end_event_id) if self.end_event_id else None,
            "upper_log_excursion": self.upper_log_excursion,
            "lower_log_excursion": self.lower_log_excursion,
            "excursion_disposition": self.excursion_disposition.value,
        }


def _json_bytes(value: object) -> bytes:
    return json.dumps(to_json_value(value), sort_keys=True, separators=(",", ":")).encode()


def _target_dataset_chunks(
    rows: Sequence[TargetRow],
    *,
    observation_dataset_id: str,
    foundation_configuration_id: str,
) -> Iterator[bytes]:
    yield b'{"contract":'
    yield _json_bytes(TARGET_DATASET_CONTRACT)
    yield b',"foundation_configuration_id":'
    yield _json_bytes(foundation_configuration_id)
    yield b',"observation_dataset_id":'
    yield _json_bytes(observation_dataset_id)
    yield b',"rows":['
    for index, row in enumerate(rows):
        if index:
            yield b","
        yield _json_bytes(row.as_json())
    yield b'],"schema_version":'
    yield _json_bytes(1)
    yield b"}"


def _target_dataset_identity(
    rows: Sequence[TargetRow],
    *,
    observation_dataset_id: str,
    foundation_configuration_id: str,
) -> str:
    digest = sha256()
    for chunk in _target_dataset_chunks(
        rows,
        observation_dataset_id=observation_dataset_id,
        foundation_configuration_id=foundation_configuration_id,
    ):
        digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class TargetDataset:
    rows: tuple[TargetRow, ...]
    observation_dataset_id: str
    foundation_configuration_id: str
    dataset_id: str
    _verified: InitVar[object | None] = None

    def __post_init__(self, _verified: object | None) -> None:
        if tuple(sorted(self.rows, key=_target_key)) != self.rows:
            raise ValueError("target rows must use deterministic ordering")
        if _verified is _TARGET_DATASET_VERIFIED:
            return
        expected = _target_dataset_identity(
            self.rows,
            observation_dataset_id=self.observation_dataset_id,
            foundation_configuration_id=self.foundation_configuration_id,
        )
        if self.dataset_id != expected:
            raise ValueError("target dataset ID does not match its semantic rows")

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "contract": TARGET_DATASET_CONTRACT,
            "schema_version": 1,
            "dataset_id": self.dataset_id,
            "observation_dataset_id": self.observation_dataset_id,
            "foundation_configuration_id": self.foundation_configuration_id,
            "rows": [row.as_json() for row in self.rows],
        }

    @classmethod
    def _from_verified_rows(
        cls,
        rows: Sequence[TargetRow],
        *,
        observation_dataset_id: str,
        foundation_configuration_id: str,
        dataset_id: str,
    ) -> "TargetDataset":
        expected = _target_dataset_identity(
            rows,
            observation_dataset_id=observation_dataset_id,
            foundation_configuration_id=foundation_configuration_id,
        )
        if dataset_id != expected:
            raise ValueError("target dataset ID does not authenticate its rows")
        return cls(
            rows=tuple(rows),
            observation_dataset_id=observation_dataset_id,
            foundation_configuration_id=foundation_configuration_id,
            dataset_id=dataset_id,
            _verified=_TARGET_DATASET_VERIFIED,
        )

    @classmethod
    def create(
        cls,
        rows: Sequence[TargetRow],
        *,
        observation_dataset_id: str,
        foundation_configuration_id: str,
    ) -> "TargetDataset":
        ordered = tuple(sorted(rows, key=_target_key))
        dataset_id = _target_dataset_identity(
            ordered,
            observation_dataset_id=observation_dataset_id,
            foundation_configuration_id=foundation_configuration_id,
        )
        return cls(
            rows=ordered,
            observation_dataset_id=observation_dataset_id,
            foundation_configuration_id=foundation_configuration_id,
            dataset_id=dataset_id,
            _verified=_TARGET_DATASET_VERIFIED,
        )


@dataclass(frozen=True, slots=True)
class HorizonCoverageSummary:
    """Immutable target and excursion coverage counts for one horizon."""

    horizon: timedelta
    total_target_count: int
    valid_return_count: int
    valid_excursion_count: int
    unavailable_by_freeze_count: int
    return_coverage: float
    excursion_coverage: float
    return_disposition_counts: tuple[tuple[str, int], ...]
    excursion_disposition_counts: tuple[tuple[str, int], ...]

    def __post_init__(self) -> None:
        if self.horizon <= timedelta(0):
            raise ValueError("coverage horizon must be positive")
        for value, field in (
            (self.total_target_count, "total target count"),
            (self.valid_return_count, "valid return count"),
            (self.valid_excursion_count, "valid excursion count"),
            (self.unavailable_by_freeze_count, "unavailable-by-freeze count"),
        ):
            if value < 0:
                raise ValueError(f"{field} must not be negative")
        if self.valid_return_count > self.total_target_count:
            raise ValueError("valid return count exceeds total target count")
        if self.valid_excursion_count > self.total_target_count:
            raise ValueError("valid excursion count exceeds total target count")
        for value, field in (
            (self.return_coverage, "return coverage"),
            (self.excursion_coverage, "excursion coverage"),
        ):
            if not isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"{field} must be finite and between zero and one")
        if sum(count for _, count in self.return_disposition_counts) != self.total_target_count:
            raise ValueError("return disposition counts do not cover all targets")
        if sum(count for _, count in self.excursion_disposition_counts) != self.total_target_count:
            raise ValueError("excursion disposition counts do not cover all targets")

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "horizon_seconds": self.horizon.total_seconds(),
            "total_target_count": self.total_target_count,
            "valid_return_count": self.valid_return_count,
            "valid_excursion_count": self.valid_excursion_count,
            "unavailable_by_freeze_count": self.unavailable_by_freeze_count,
            "return_coverage": self.return_coverage,
            "excursion_coverage": self.excursion_coverage,
            "return_disposition_counts": to_json_value(
                [[name, count] for name, count in self.return_disposition_counts]
            ),
            "excursion_disposition_counts": to_json_value(
                [[name, count] for name, count in self.excursion_disposition_counts]
            ),
        }


def _validate_panel_rows(rows: Sequence[PanelRow]) -> None:
    if not isinstance(rows, tuple):
        raise ValueError("panel rows must use deterministic ordering")
    previous_key: tuple[datetime, str, str] | None = None
    seen_keys: set[tuple[object, ...]] = set()
    for row in rows:
        key = cast(tuple[datetime, str, str], _panel_key(row))
        if previous_key is not None and key < previous_key:
            raise ValueError("panel rows must use deterministic ordering")
        if key in seen_keys:
            raise ValueError("panel rows must have unique semantic keys")
        seen_keys.add(key)
        previous_key = key


def _panel_key(row: PanelRow) -> tuple[object, ...]:
    return row.decision_time, row.instrument_id, row.basis.value


def _panel_dataset_chunks(
    rows: Sequence[PanelRow],
    *,
    observation_dataset_id: str,
    foundation_configuration_id: str,
) -> Iterator[bytes]:
    """Yield the legacy canonical payload without materialising its row array."""
    yield b'{"contract":'
    yield _json_bytes(PANEL_DATASET_CONTRACT)
    yield b',"foundation_configuration_id":'
    yield _json_bytes(foundation_configuration_id)
    yield b',"observation_dataset_id":'
    yield _json_bytes(observation_dataset_id)
    yield b',"rows":['
    for index, row in enumerate(rows):
        if index:
            yield b","
        yield _json_bytes(row.as_json())
    yield b'],"schema_version":'
    yield _json_bytes(1)
    yield b"}"


def _panel_dataset_identity(
    rows: Sequence[PanelRow],
    *,
    observation_dataset_id: str,
    foundation_configuration_id: str,
) -> str:
    digest = sha256()
    for chunk in _panel_dataset_chunks(
        rows,
        observation_dataset_id=observation_dataset_id,
        foundation_configuration_id=foundation_configuration_id,
    ):
        digest.update(chunk)
    return digest.hexdigest()


def _target_key(row: TargetRow) -> tuple[object, ...]:
    return row.instrument_id, row.decision_time, row.horizon.total_seconds(), row.target_basis.value


def target_identity(
    *,
    instrument_id: str,
    decision_time: datetime,
    horizon: timedelta,
    target_basis: PriceBasis,
    target_revision_policy: str,
) -> str:
    """Return the stable identity of one frozen target key."""

    return _hash_json(
        {
            "contract": TARGET_DATASET_CONTRACT,
            "instrument_id": instrument_id,
            "decision_time": decision_time.isoformat(),
            "horizon_seconds": horizon.total_seconds(),
            "target_basis": target_basis.value,
            "target_revision_policy": target_revision_policy,
        }
    )


def _hash_json(value: object) -> str:
    canonical = to_json_value(value)
    return sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _require_sha256(value: str, field: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} must be a lower-case SHA-256")


def _require_interval(start: datetime, end: datetime, field: str) -> None:
    require_utc(start, f"{field} start")
    require_utc(end, f"{field} end")
    if end <= start:
        raise ValueError(f"{field} must be positive")


def _role(value: InstrumentRole) -> str:
    return InstrumentRole(value).value


def _availability_basis(value: AvailabilityBasis) -> str:
    return AvailabilityBasis(value).value
