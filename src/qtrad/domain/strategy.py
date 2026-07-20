"""Versioned strategy forecast and realised-outcome contracts."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from qtrad.domain.identifiers import InstrumentId
from qtrad.domain.time import require_utc


class StrategyState(StrEnum):
    SHADOW = "SHADOW"
    SELECTED = "SELECTED"


class ForecastTarget(StrEnum):
    MID_CLOSE_RETURN = "MID_CLOSE_RETURN"


@dataclass(frozen=True, slots=True)
class ScoreContract:
    contract_version: int
    minimum_samples: int
    horizon: timedelta
    basis: str = "TIME_SERIES_SPEARMAN"
    sampling: str = "EVERY_COMPLETED_BAR"
    window: str = "FULL_DATASET"
    overlapping_observations: str = "INCLUDED"
    target: ForecastTarget = ForecastTarget.MID_CLOSE_RETURN

    def __post_init__(self) -> None:
        if self.contract_version <= 0 or self.minimum_samples <= 1 or self.horizon <= timedelta(0):
            raise ValueError(
                "score contract version/horizon must be positive and minimum_samples exceed one"
            )
        if self.basis != "TIME_SERIES_SPEARMAN":
            raise ValueError("unsupported score basis")
        if not self.sampling or not self.window or not self.overlapping_observations:
            raise ValueError("score sampling, window and overlap rules are required")

    @property
    def configuration_hash(self) -> str:
        encoded = json.dumps(
            {
                "basis": self.basis,
                "contract_version": self.contract_version,
                "horizon_seconds": int(self.horizon.total_seconds()),
                "minimum_samples": self.minimum_samples,
                "overlapping_observations": self.overlapping_observations,
                "sampling": self.sampling,
                "target": self.target.value,
                "window": self.window,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class StrategyDefinition:
    strategy_id: str
    strategy_version: int
    kind: str
    lookback_bars: int
    horizon: timedelta
    state: StrategyState = StrategyState.SHADOW
    threshold: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if not self.strategy_id or self.strategy_version <= 0 or not self.kind:
            raise ValueError("strategy identity, version and kind are required")
        if self.lookback_bars <= 0 or self.horizon <= timedelta(0):
            raise ValueError("strategy lookback and horizon must be positive")
        if self.threshold < 0:
            raise ValueError("strategy threshold must not be negative")

    @property
    def configuration_hash(self) -> str:
        encoded = json.dumps(
            {
                "horizon_seconds": int(self.horizon.total_seconds()),
                "kind": self.kind,
                "lookback_bars": self.lookback_bars,
                "strategy_id": self.strategy_id,
                "strategy_version": self.strategy_version,
                "threshold": str(self.threshold),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class Forecast:
    forecast_id: str
    strategy_id: str
    strategy_version: int
    strategy_configuration_hash: str
    strategy_state: StrategyState
    instrument_id: InstrumentId
    observation_end: datetime
    decision_time: datetime
    horizon: timedelta
    target: ForecastTarget
    strength: Decimal
    rationale: str

    def __post_init__(self) -> None:
        require_utc(self.observation_end, "forecast observation_end")
        require_utc(self.decision_time, "forecast decision_time")
        if len(self.forecast_id) != 64 or len(self.strategy_configuration_hash) != 64:
            raise ValueError("forecast and strategy configuration IDs must be SHA-256")
        if not self.strategy_id or self.strategy_version <= 0 or not self.rationale:
            raise ValueError("forecast strategy identity and rationale are required")
        if self.observation_end > self.decision_time:
            raise ValueError("forecast observation must not follow its decision")
        if self.horizon <= timedelta(0):
            raise ValueError("forecast horizon must be positive")
        if self.strength < Decimal("-1") or self.strength > Decimal("1"):
            raise ValueError("forecast strength must be between -1 and 1")

    @property
    def target_time(self) -> datetime:
        return self.decision_time + self.horizon


@dataclass(frozen=True, slots=True)
class RealisedOutcome:
    forecast_id: str
    instrument_id: InstrumentId
    decision_time: datetime
    target_time: datetime
    decision_mid: Decimal
    realised_mid: Decimal
    realised_return: Decimal

    def __post_init__(self) -> None:
        require_utc(self.decision_time, "outcome decision_time")
        require_utc(self.target_time, "outcome target_time")
        if len(self.forecast_id) != 64:
            raise ValueError("outcome forecast ID must be SHA-256")
        if self.target_time <= self.decision_time:
            raise ValueError("outcome must follow its forecast decision")
        if self.decision_mid <= 0 or self.realised_mid <= 0:
            raise ValueError("outcome prices must be positive")
        expected = self.realised_mid / self.decision_mid - Decimal("1")
        if self.realised_return != expected:
            raise ValueError("outcome return must match its prices")


def forecast_identity(
    definition: StrategyDefinition,
    instrument_id: InstrumentId,
    decision_time: datetime,
) -> str:
    require_utc(decision_time, "forecast identity decision_time")
    encoded = json.dumps(
        {
            "configuration_hash": definition.configuration_hash,
            "decision_time": decision_time.isoformat(),
            "instrument_id": str(instrument_id),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()
