# pyright: strict
"""Synthetic daily panel; no empirical input has been approved for this laboratory."""

from __future__ import annotations

import hashlib
import io
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import polars as pl

SOURCE = "SYNTHETIC_UNIT_TEST:U12:v1"


def sessions(start: date, end: date) -> tuple[date, ...]:
    """Inclusive artificial Mon-Fri calendar; never an empirical exchange calendar."""
    return tuple(
        start + timedelta(days=i)
        for i in range((end - start).days + 1)
        if (start + timedelta(days=i)).weekday() < 5
    )


def instant(day: date, hour: int = 21) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


@dataclass(frozen=True)
class Contract:
    identity: str
    first: date
    last: date
    known_at: datetime


@dataclass(frozen=True)
class Market:
    family: str
    group: str
    listed: date
    delisted: date | None
    known_at: datetime
    contracts: tuple[Contract, ...]
    multiplier: Decimal = Decimal("10")
    currency: str = "SYNTHETIC_USD"
    cost: Decimal | None = Decimal("0.10")
    commission: Decimal = Decimal("0.02")
    carry: Decimal = Decimal("0.01")
    minimum_scale: Decimal = Decimal("0.01")
    session_band: str = "SYNTHETIC_UTC_14_21"
    anchor: bool = False

    def __post_init__(self) -> None:
        for value in (self.multiplier, self.minimum_scale):
            if not value.is_finite() or value <= 0:
                raise ValueError("Multiplier and minimum risk scale must be finite and positive")
        for value in (self.cost, self.commission, self.carry):
            if value is not None and (not value.is_finite() or value < 0):
                raise ValueError("Fixture friction must be finite and nonnegative")
        if self.known_at.utcoffset() != timedelta(0):
            raise ValueError("Market availability must be aware UTC")
        for contract in self.contracts:
            if contract.first > contract.last or contract.known_at.utcoffset() != timedelta(0):
                raise ValueError("Invalid synthetic contract schedule")

    def contract(self, day: date, cutoff: datetime) -> Contract | None:
        matches = [c for c in self.contracts if c.first <= day <= c.last and c.known_at <= cutoff]
        if len(matches) > 1:
            raise ValueError(f"Ambiguous contract calendar for {self.family}")
        return matches[0] if matches else None


@dataclass(frozen=True)
class Bar:
    family: str
    session: date
    contract: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    open_at: datetime
    close_at: datetime
    available_at: datetime
    volume: Decimal | None
    volume_at: datetime
    open_interest: Decimal | None
    open_interest_at: datetime
    source: str = SOURCE
    price_basis: str = "SYNTHETIC_UNADJUSTED_CONTRACT"
    adjustment: str = "NONE"
    timestamp_basis: str = "SYNTHETIC_UTC_SESSION"

    def __post_init__(self) -> None:
        if self.source != SOURCE:
            raise PermissionError("NONE_APPROVED: empirical panel input is unavailable")
        if any(
            t.utcoffset() != timedelta(0)
            for t in (
                self.open_at,
                self.close_at,
                self.available_at,
                self.volume_at,
                self.open_interest_at,
            )
        ):
            raise ValueError("Panel timestamps must be aware UTC")
        if not self.open_at < self.close_at <= self.available_at:
            raise ValueError("Invalid price availability chronology")
        prices = (self.open, self.high, self.low, self.close)
        if not all(p.is_finite() for p in prices):
            raise ValueError("Non-finite contract price")
        if self.high < max(prices) or self.low > min(prices):
            raise ValueError("Invalid OHLC")
        if self.adjustment != "NONE" or self.price_basis != "SYNTHETIC_UNADJUSTED_CONTRACT":
            raise ValueError("Adjusted series have no authorised economic scoring semantics")
        if self.timestamp_basis != "SYNTHETIC_UTC_SESSION":
            raise ValueError("Unqualified session timestamp basis")
        for value, release in (
            (self.volume, self.volume_at),
            (self.open_interest, self.open_interest_at),
        ):
            if value is not None and (
                not value.is_finite() or value < 0 or release < self.close_at
            ):
                raise ValueError("Invalid daily liquidity value or availability")


@dataclass(frozen=True)
class SyntheticPartition:
    """Synthetic dates only. Loader denial is NOT actual research-role access isolation."""

    start: date
    end: date
    reserve_start: date
    source: str = SOURCE

    def check(self, start: date, end: date) -> None:
        if self.source != SOURCE:
            raise PermissionError("NONE_APPROVED: no empirical data or access isolation approved")
        if start > end or start < self.start or end > self.end or end >= self.reserve_start:
            raise PermissionError("Request outside synthetic development partition")


def write_panel(path: Path, bars: tuple[Bar, ...]) -> str:
    rows = [{k: None if v is None else str(v) for k, v in asdict(b).items()} for b in bars]
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation also prevents accidentally overwriting an earlier fixture attempt.
    with path.open("xb") as output:
        pl.DataFrame(rows).write_parquet(output)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _string(row: dict[str, object], field: str) -> str:
    value = row[field]
    if not isinstance(value, str):
        raise ValueError(f"Required string field {field} is absent or invalid")
    return value


def _decimal_or_none(row: dict[str, object], field: str) -> Decimal | None:
    return None if row[field] is None else Decimal(_string(row, field))


def load_panel(
    path: Path, partition: SyntheticPartition, start: date, end: date, checksum: str
) -> tuple[Bar, ...]:
    partition.check(start, end)  # Intentionally before opening, hashing or decoding.
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != checksum:
        raise ValueError("Panel snapshot checksum mismatch")
    result: list[Bar] = []
    for raw in pl.read_parquet(io.BytesIO(payload)).to_dicts():
        row: dict[str, object] = raw
        day = date.fromisoformat(_string(row, "session"))
        if not partition.start <= day <= partition.end or day >= partition.reserve_start:
            raise ValueError("Panel contains rows outside its synthetic development declaration")
        bar = Bar(
            family=_string(row, "family"),
            session=day,
            contract=_string(row, "contract"),
            open=Decimal(_string(row, "open")),
            high=Decimal(_string(row, "high")),
            low=Decimal(_string(row, "low")),
            close=Decimal(_string(row, "close")),
            open_at=datetime.fromisoformat(_string(row, "open_at")),
            close_at=datetime.fromisoformat(_string(row, "close_at")),
            available_at=datetime.fromisoformat(_string(row, "available_at")),
            volume=_decimal_or_none(row, "volume"),
            volume_at=datetime.fromisoformat(_string(row, "volume_at")),
            open_interest=_decimal_or_none(row, "open_interest"),
            open_interest_at=datetime.fromisoformat(_string(row, "open_interest_at")),
            source=_string(row, "source"),
            price_basis=_string(row, "price_basis"),
            adjustment=_string(row, "adjustment"),
            timestamp_basis=_string(row, "timestamp_basis"),
        )
        if start <= day <= end:
            result.append(bar)
    keys = [(b.family, b.session, b.contract) for b in result]
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate family/session/contract")
    return tuple(sorted(result, key=lambda b: (b.family, b.session, b.contract)))


def index_panel(bars: tuple[Bar, ...]) -> dict[str, tuple[Bar, ...]]:
    grouped: dict[str, list[Bar]] = {}
    for bar in bars:
        grouped.setdefault(bar.family, []).append(bar)
    return {
        family: tuple(sorted(values, key=lambda b: (b.session, b.contract)))
        for family, values in grouped.items()
    }
