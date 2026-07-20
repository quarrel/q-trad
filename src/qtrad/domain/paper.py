"""Conservative shadow-paper execution and accounting values."""

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from qtrad.domain.identifiers import InstrumentId
from qtrad.domain.time import require_utc


@dataclass(frozen=True, slots=True)
class PaperSessionProfile:
    profile_version: str
    timezone_name: str
    local_open: time
    local_close: time
    weekdays: tuple[int, ...]
    holidays: tuple[date, ...] = ()

    def __post_init__(self) -> None:
        if not self.profile_version or not self.timezone_name:
            raise ValueError("paper session profile identity and timezone are required")
        try:
            ZoneInfo(self.timezone_name)
        except ZoneInfoNotFoundError as error:
            raise ValueError("paper session timezone is unknown") from error
        if self.local_open.tzinfo is not None or self.local_close.tzinfo is not None:
            raise ValueError("paper session local times must be timezone-naive")
        if self.local_close <= self.local_open:
            raise ValueError("paper session profile does not support overnight sessions")
        if not self.weekdays or tuple(sorted(set(self.weekdays))) != self.weekdays:
            raise ValueError("paper session weekdays must be unique and sorted")
        if any(day < 0 or day > 6 for day in self.weekdays):
            raise ValueError("paper session weekdays must use datetime weekday values")
        if tuple(sorted(set(self.holidays))) != self.holidays:
            raise ValueError("paper session holidays must be unique and sorted")

    def allows(self, received_time: datetime) -> bool:
        require_utc(received_time, "paper session received_time")
        local = received_time.astimezone(ZoneInfo(self.timezone_name))
        return (
            local.weekday() in self.weekdays
            and local.date() not in self.holidays
            and self.local_open <= local.time().replace(tzinfo=None) < self.local_close
        )

    @property
    def configuration_hash(self) -> str:
        encoded = json.dumps(
            {
                "holidays": [value.isoformat() for value in self.holidays],
                "local_close": self.local_close.isoformat(),
                "local_open": self.local_open.isoformat(),
                "profile_version": self.profile_version,
                "timezone_name": self.timezone_name,
                "weekdays": list(self.weekdays),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class PaperInstrumentEconomics:
    instrument_id: InstrumentId
    quantity: Decimal
    price_increment: Decimal
    value_per_price_unit: Decimal
    quote_currency: str
    reporting_currency: str
    quote_to_reporting_rate: Decimal
    session_profile: PaperSessionProfile

    def __post_init__(self) -> None:
        if (
            self.quantity <= 0
            or self.price_increment <= 0
            or self.value_per_price_unit <= 0
            or self.quote_to_reporting_rate <= 0
        ):
            raise ValueError("paper quantity and economics must be positive")
        if not self.quote_currency or not self.reporting_currency:
            raise ValueError("paper currencies are required")

    @property
    def configuration_hash(self) -> str:
        encoded = json.dumps(
            {
                "instrument_id": str(self.instrument_id),
                "price_increment": str(self.price_increment),
                "quantity": str(self.quantity),
                "quote_currency": self.quote_currency,
                "quote_to_reporting_rate": str(self.quote_to_reporting_rate),
                "reporting_currency": self.reporting_currency,
                "session_profile_hash": self.session_profile.configuration_hash,
                "value_per_price_unit": str(self.value_per_price_unit),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class PaperModel:
    model_version: int
    latency: timedelta
    adverse_slippage_increments: int

    def __post_init__(self) -> None:
        if self.model_version <= 0 or self.latency < timedelta(0):
            raise ValueError("paper model version must be positive and latency non-negative")
        if self.adverse_slippage_increments < 0:
            raise ValueError("paper slippage must not be negative")

    @property
    def configuration_hash(self) -> str:
        encoded = json.dumps(
            {
                "adverse_slippage_increments": self.adverse_slippage_increments,
                "latency_microseconds": int(self.latency.total_seconds() * 1_000_000),
                "model_version": self.model_version,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class PaperRoundTrip:
    forecast_id: str
    strategy_id: str
    instrument_id: InstrumentId
    direction: int
    entry_received_time: datetime
    exit_received_time: datetime
    entry_price: Decimal
    exit_price: Decimal
    entry_mid: Decimal
    exit_mid: Decimal
    quantity: Decimal
    gross_mid_pnl: Decimal
    execution_cost: Decimal
    net_pnl: Decimal
    turnover: Decimal
    reporting_currency: str
    model_configuration_hash: str
    economics_configuration_hash: str
    session_profile_version: str

    def __post_init__(self) -> None:
        require_utc(self.entry_received_time, "paper entry_received_time")
        require_utc(self.exit_received_time, "paper exit_received_time")
        if (
            len(self.forecast_id) != 64
            or len(self.model_configuration_hash) != 64
            or len(self.economics_configuration_hash) != 64
        ):
            raise ValueError("paper forecast and model IDs must be SHA-256")
        if self.direction not in {-1, 1} or self.exit_received_time <= self.entry_received_time:
            raise ValueError("paper round trip requires a direction and causal entry/exit")
        if min(self.entry_price, self.exit_price, self.entry_mid, self.exit_mid) <= 0:
            raise ValueError("paper prices must be positive")
        if self.quantity <= 0 or not self.reporting_currency or not self.session_profile_version:
            raise ValueError("paper quantity and reporting currency are required")
        if self.execution_cost < 0 or self.turnover <= 0:
            raise ValueError("paper execution cost must be non-negative and turnover positive")
        if self.net_pnl != self.gross_mid_pnl - self.execution_cost:
            raise ValueError("paper net P&L must equal gross midpoint P&L less execution cost")


@dataclass(frozen=True, slots=True)
class StrategyLedger:
    strategy_id: str
    reporting_currency: str
    round_trip_count: int
    gross_mid_pnl: Decimal
    execution_cost: Decimal
    net_pnl: Decimal
    maximum_drawdown: Decimal
    turnover: Decimal

    def __post_init__(self) -> None:
        if not self.strategy_id or not self.reporting_currency or self.round_trip_count < 0:
            raise ValueError("ledger identity, currency and non-negative count are required")
        if self.execution_cost < 0 or self.maximum_drawdown < 0 or self.turnover < 0:
            raise ValueError("ledger costs, drawdown and turnover must not be negative")
        if self.net_pnl != self.gross_mid_pnl - self.execution_cost:
            raise ValueError("ledger net P&L must reconcile")
