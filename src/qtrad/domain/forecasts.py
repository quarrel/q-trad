"""Model-independent forecast artefacts for the R1.C offline evaluator."""

import json
from collections.abc import Sequence
from dataclasses import InitVar, dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from json.encoder import encode_basestring_ascii
from math import isfinite
from typing import ClassVar

from qtrad.domain.events import JsonValue, to_json_value
from qtrad.domain.time import require_utc

FORECAST_DATASET_CONTRACT = "qtrad-research-forecasts-v1"
_IDENTITY_PREVALIDATED_TOKEN = object()


class ReturnUnit(StrEnum):
    """The unit used by a point forecast."""

    LOG_RETURN = "LOG_RETURN"


@dataclass(frozen=True, slots=True)
class ForecastRow:
    """One point forecast with complete fold and dataset lineage."""

    forecast_id: str
    instrument_id: str
    decision_time: datetime
    horizon: timedelta
    expected_return: float
    return_unit: ReturnUnit
    feature_data_asof: datetime
    training_cutoff: datetime
    observation_dataset_id: str
    panel_dataset_id: str
    target_dataset_id: str
    target_id: str
    fold_dataset_id: str
    experiment_id: str
    fold_id: str
    model_id: str
    model_contract: str
    _identity_prevalidated: InitVar[object | None] = None

    CONTRACT: ClassVar[str] = FORECAST_DATASET_CONTRACT

    def __post_init__(self, _identity_prevalidated: object | None) -> None:
        for value, field in (
            (self.decision_time, "forecast decision_time"),
            (self.feature_data_asof, "forecast feature_data_asof"),
            (self.training_cutoff, "forecast training_cutoff"),
        ):
            require_utc(value, field)
        if self.horizon <= timedelta(0):
            raise ValueError("forecast horizon must be positive")
        if not isfinite(self.expected_return):
            raise ValueError("forecast expected_return must be finite")
        if self.return_unit != "LOG_RETURN":
            raise ValueError("forecast return_unit must be LOG_RETURN")
        for value, field in (
            (self.forecast_id, "forecast ID"),
            (self.instrument_id, "forecast instrument ID"),
            (self.observation_dataset_id, "forecast observation dataset ID"),
            (self.panel_dataset_id, "forecast panel dataset ID"),
            (self.target_dataset_id, "forecast target dataset ID"),
            (self.target_id, "forecast target ID"),
            (self.fold_dataset_id, "forecast fold dataset ID"),
            (self.experiment_id, "forecast experiment ID"),
            (self.fold_id, "forecast fold ID"),
            (self.model_id, "forecast model ID"),
            (self.model_contract, "forecast model contract"),
        ):
            if not value:
                raise ValueError(f"{field} must be non-empty")
        if (
            _identity_prevalidated is not _IDENTITY_PREVALIDATED_TOKEN
            and self.forecast_id != _hash_json(_forecast_semantic(self))
        ):
            raise ValueError("forecast ID does not match its semantic content")

    @classmethod
    def create(
        cls,
        *,
        instrument_id: str,
        decision_time: datetime,
        horizon: timedelta,
        expected_return: float,
        return_unit: ReturnUnit,
        feature_data_asof: datetime,
        training_cutoff: datetime,
        observation_dataset_id: str,
        panel_dataset_id: str,
        target_dataset_id: str,
        target_id: str,
        fold_dataset_id: str,
        experiment_id: str,
        fold_id: str,
        model_id: str,
        model_contract: str,
    ) -> "ForecastRow":
        forecast_id = _forecast_id_from_values(
            instrument_id=instrument_id,
            decision_time=decision_time,
            horizon=horizon,
            expected_return=expected_return,
            return_unit=return_unit,
            feature_data_asof=feature_data_asof,
            training_cutoff=training_cutoff,
            observation_dataset_id=observation_dataset_id,
            panel_dataset_id=panel_dataset_id,
            target_dataset_id=target_dataset_id,
            target_id=target_id,
            fold_dataset_id=fold_dataset_id,
            experiment_id=experiment_id,
            fold_id=fold_id,
            model_id=model_id,
            model_contract=model_contract,
        )
        return cls(
            forecast_id=forecast_id,
            instrument_id=instrument_id,
            decision_time=decision_time,
            horizon=horizon,
            expected_return=expected_return,
            return_unit=return_unit,
            feature_data_asof=feature_data_asof,
            training_cutoff=training_cutoff,
            observation_dataset_id=observation_dataset_id,
            panel_dataset_id=panel_dataset_id,
            target_dataset_id=target_dataset_id,
            target_id=target_id,
            fold_dataset_id=fold_dataset_id,
            experiment_id=experiment_id,
            fold_id=fold_id,
            model_id=model_id,
            model_contract=model_contract,
            _identity_prevalidated=_IDENTITY_PREVALIDATED_TOKEN,
        )

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "forecast_id": self.forecast_id,
            "instrument_id": self.instrument_id,
            "decision_time": self.decision_time.isoformat(),
            "horizon_seconds": self.horizon.total_seconds(),
            "expected_return": self.expected_return,
            "return_unit": self.return_unit,
            "feature_data_asof": self.feature_data_asof.isoformat(),
            "training_cutoff": self.training_cutoff.isoformat(),
            "observation_dataset_id": self.observation_dataset_id,
            "panel_dataset_id": self.panel_dataset_id,
            "target_dataset_id": self.target_dataset_id,
            "target_id": self.target_id,
            "fold_dataset_id": self.fold_dataset_id,
            "experiment_id": self.experiment_id,
            "fold_id": self.fold_id,
            "model_id": self.model_id,
            "model_contract": self.model_contract,
        }


@dataclass(frozen=True, slots=True)
class ForecastDataset:
    """Immutable forecasts consumable without loading model code."""

    rows: tuple[ForecastRow, ...]
    observation_dataset_id: str
    panel_dataset_id: str
    target_dataset_id: str
    fold_dataset_id: str
    dataset_id: str

    CONTRACT: ClassVar[str] = FORECAST_DATASET_CONTRACT

    def __post_init__(self) -> None:
        expected_order = tuple(
            sorted(
                self.rows,
                key=lambda row: (row.decision_time, row.instrument_id, row.target_id, row.fold_id),
            )
        )
        if expected_order != self.rows:
            raise ValueError("forecast rows must use deterministic ordering")
        expected = _dataset_hash(
            self.rows,
            observation_dataset_id=self.observation_dataset_id,
            panel_dataset_id=self.panel_dataset_id,
            target_dataset_id=self.target_dataset_id,
            fold_dataset_id=self.fold_dataset_id,
        )
        if self.dataset_id != expected:
            raise ValueError("forecast dataset ID does not match its semantic rows")
        if any(
            (
                row.observation_dataset_id != self.observation_dataset_id
                or row.panel_dataset_id != self.panel_dataset_id
                or row.target_dataset_id != self.target_dataset_id
                or row.fold_dataset_id != self.fold_dataset_id
            )
            for row in self.rows
        ):
            raise ValueError("forecast row lineage does not match its dataset")

    @classmethod
    def create(
        cls,
        rows: Sequence[ForecastRow],
        *,
        observation_dataset_id: str,
        panel_dataset_id: str,
        target_dataset_id: str,
        fold_dataset_id: str,
    ) -> "ForecastDataset":
        ordered = tuple(
            sorted(
                rows,
                key=lambda row: (row.decision_time, row.instrument_id, row.target_id, row.fold_id),
            )
        )
        return cls(
            rows=ordered,
            observation_dataset_id=observation_dataset_id,
            panel_dataset_id=panel_dataset_id,
            target_dataset_id=target_dataset_id,
            fold_dataset_id=fold_dataset_id,
            dataset_id=_dataset_hash(
                ordered,
                observation_dataset_id=observation_dataset_id,
                panel_dataset_id=panel_dataset_id,
                target_dataset_id=target_dataset_id,
                fold_dataset_id=fold_dataset_id,
            ),
        )


def _dataset_hash(
    rows: Sequence[ForecastRow],
    *,
    observation_dataset_id: str,
    panel_dataset_id: str,
    target_dataset_id: str,
    fold_dataset_id: str,
) -> str:
    return _hash_json(
        {
            "contract": FORECAST_DATASET_CONTRACT,
            "schema_version": 1,
            "observation_dataset_id": observation_dataset_id,
            "panel_dataset_id": panel_dataset_id,
            "target_dataset_id": target_dataset_id,
            "fold_dataset_id": fold_dataset_id,
            "rows": [row.as_json() for row in rows],
        }
    )


def _forecast_semantic(row: ForecastRow) -> dict[str, object]:
    return {
        "contract": FORECAST_DATASET_CONTRACT,
        "schema_version": 1,
        "instrument_id": row.instrument_id,
        "decision_time": row.decision_time,
        "horizon_seconds": row.horizon.total_seconds(),
        "expected_return": row.expected_return,
        "return_unit": row.return_unit,
        "feature_data_asof": row.feature_data_asof,
        "training_cutoff": row.training_cutoff,
        "observation_dataset_id": row.observation_dataset_id,
        "panel_dataset_id": row.panel_dataset_id,
        "target_dataset_id": row.target_dataset_id,
        "target_id": row.target_id,
        "fold_dataset_id": row.fold_dataset_id,
        "experiment_id": row.experiment_id,
        "fold_id": row.fold_id,
        "model_id": row.model_id,
        "model_contract": row.model_contract,
    }


def _hash_json(value: object) -> str:
    canonical = to_json_value(value)
    return sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _json_scalar(value: object) -> bytes:
    if isinstance(value, str):
        return encode_basestring_ascii(value).encode("ascii")
    if value is None:
        return b"null"
    if isinstance(value, bool):
        return b"true" if value else b"false"
    if isinstance(value, (int, float)):
        return repr(value).encode("ascii")
    raise TypeError(f"unsupported forecast identity scalar: {type(value).__name__}")


def _forecast_id_from_values(
    *,
    instrument_id: str,
    decision_time: datetime,
    horizon: timedelta,
    expected_return: float,
    return_unit: ReturnUnit,
    feature_data_asof: datetime,
    training_cutoff: datetime,
    observation_dataset_id: str,
    panel_dataset_id: str,
    target_dataset_id: str,
    target_id: str,
    fold_dataset_id: str,
    experiment_id: str,
    fold_id: str,
    model_id: str,
    model_contract: str,
) -> str:
    digest = sha256()
    digest.update(b'{"contract":')
    digest.update(_json_scalar(FORECAST_DATASET_CONTRACT))
    digest.update(b',"decision_time":')
    digest.update(_json_scalar(decision_time.isoformat().replace("+00:00", "Z")))
    digest.update(b',"expected_return":')
    digest.update(_json_scalar(expected_return))
    digest.update(b',"experiment_id":')
    digest.update(_json_scalar(experiment_id))
    digest.update(b',"feature_data_asof":')
    digest.update(_json_scalar(feature_data_asof.isoformat().replace("+00:00", "Z")))
    digest.update(b',"fold_dataset_id":')
    digest.update(_json_scalar(fold_dataset_id))
    digest.update(b',"fold_id":')
    digest.update(_json_scalar(fold_id))
    digest.update(b',"horizon_seconds":')
    digest.update(_json_scalar(horizon.total_seconds()))
    digest.update(b',"instrument_id":')
    digest.update(_json_scalar(instrument_id))
    digest.update(b',"model_contract":')
    digest.update(_json_scalar(model_contract))
    digest.update(b',"model_id":')
    digest.update(_json_scalar(model_id))
    digest.update(b',"observation_dataset_id":')
    digest.update(_json_scalar(observation_dataset_id))
    digest.update(b',"panel_dataset_id":')
    digest.update(_json_scalar(panel_dataset_id))
    digest.update(b',"return_unit":')
    digest.update(_json_scalar(return_unit.value))
    digest.update(b',"schema_version":1,"target_dataset_id":')
    digest.update(_json_scalar(target_dataset_id))
    digest.update(b',"target_id":')
    digest.update(_json_scalar(target_id))
    digest.update(b',"training_cutoff":')
    digest.update(_json_scalar(training_cutoff.isoformat().replace("+00:00", "Z")))
    digest.update(b"}")
    return digest.hexdigest()
