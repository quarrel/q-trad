"""Authenticated expected-active policy derived from IBKR contract evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from qtrad.domain.time import require_utc
from qtrad.ports.ibkr_capability import IbkrContractEvidence


class IbkrMarketActivity(StrEnum):
    """Operational interpretation of the finite authenticated liquid-hours schedule."""

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class _LiquidInterval:
    start_date: date
    start_minutes: int
    end_date: date
    end_minutes: int


@dataclass(frozen=True, slots=True)
class _LiquidHoursEntry:
    session_date: date
    intervals: tuple[_LiquidInterval, ...]
    valid: bool


def ibkr_contract_activity(
    evidence: IbkrContractEvidence,
    observed_at: datetime,
) -> IbkrMarketActivity:
    """Classify a reviewed contract at a UTC observation time.

    IBKR's ``liquidHours`` is finite, dated contract evidence.  A missing,
    malformed, invalid-timezone, or out-of-horizon schedule is UNKNOWN.  The
    caller must therefore refresh the reviewed evidence before continuous
    operation; UNKNOWN is never treated as a closed-market exemption.
    """

    observed_at = require_utc(observed_at, "observed_at")
    if not evidence.liquid_hours:
        return IbkrMarketActivity.UNKNOWN
    if not evidence.timezone:
        return IbkrMarketActivity.UNKNOWN
    try:
        timezone = ZoneInfo(evidence.timezone)
    except (ValueError, ZoneInfoNotFoundError):
        return IbkrMarketActivity.UNKNOWN

    entries = _parse_liquid_hours(evidence.liquid_hours)
    if not entries:
        return IbkrMarketActivity.UNKNOWN
    local_time = observed_at.astimezone(timezone)

    for entry in entries:
        if not entry.valid:
            continue
        for interval in entry.intervals:
            start = _local_datetime(interval.start_date, interval.start_minutes, timezone)
            end = _local_datetime(interval.end_date, interval.end_minutes, timezone)
            if interval.end_minutes == 24 * 60:
                end += timedelta(days=1)
            if end <= start:
                end += timedelta(days=1)
            if start <= local_time < end:
                return IbkrMarketActivity.ACTIVE

    current_entries = [entry for entry in entries if entry.session_date == local_time.date()]
    if not current_entries or any(not entry.valid for entry in current_entries):
        return IbkrMarketActivity.UNKNOWN
    return IbkrMarketActivity.INACTIVE


def ibkr_contract_is_expected_active(
    evidence: IbkrContractEvidence,
    observed_at: datetime,
) -> bool:
    """Return whether freshness is required for this contract now.

    UNKNOWN is conservatively expected-active.  Only a valid INACTIVE schedule
    can exempt a listing from bid/ask freshness checks.
    """

    return ibkr_contract_activity(evidence, observed_at) is not IbkrMarketActivity.INACTIVE


def _local_datetime(session_date: date, minutes: int, timezone: ZoneInfo) -> datetime:
    if minutes == 24 * 60:
        return datetime.combine(session_date, time(0), tzinfo=timezone)
    return datetime.combine(
        session_date,
        time(hour=minutes // 60, minute=minutes % 60),
        tzinfo=timezone,
    )


def _parse_liquid_hours(value: str) -> tuple[_LiquidHoursEntry, ...]:
    entries: list[_LiquidHoursEntry] = []
    for segment in value.split(";"):
        segment = segment.strip()
        if not segment or ":" not in segment:
            continue
        date_text, windows_text = segment.split(":", 1)
        try:
            session_date = datetime.strptime(date_text, "%Y%m%d").date()
        except ValueError:
            continue
        if windows_text.upper() == "CLOSED":
            entries.append(_LiquidHoursEntry(session_date, (), True))
            continue

        intervals: list[_LiquidInterval] = []
        valid = True
        for window in windows_text.split(","):
            bounds = window.split("-", 1)
            if len(bounds) != 2:
                valid = False
                continue
            start = _parse_endpoint(bounds[0], session_date)
            end = _parse_endpoint(bounds[1], session_date, allow_2400=True)
            if start is None or end is None:
                valid = False
                continue
            start_date, start_minutes, start_explicit = start
            end_date, end_minutes, end_explicit = end
            if end_date < start_date or (end_date == start_date and end_minutes <= start_minutes):
                if end_date == start_date and not start_explicit and not end_explicit:
                    end_date += timedelta(days=1)
                else:
                    valid = False
                    continue
            intervals.append(_LiquidInterval(start_date, start_minutes, end_date, end_minutes))
        entries.append(_LiquidHoursEntry(session_date, tuple(intervals), valid and bool(intervals)))
    return tuple(entries)


def _parse_endpoint(
    value: str,
    default_date: date,
    *,
    allow_2400: bool = False,
) -> tuple[date, int, bool] | None:
    parts = value.strip().split(":")
    if len(parts) == 1:
        endpoint_date = default_date
        has_explicit_date = False
        hhmm = parts[0]
    elif len(parts) == 2:
        try:
            endpoint_date = datetime.strptime(parts[0], "%Y%m%d").date()
        except ValueError:
            return None
        has_explicit_date = True
        hhmm = parts[1]
    else:
        return None
    minutes = _parse_hhmm(hhmm, allow_2400=allow_2400)
    return None if minutes is None else (endpoint_date, minutes, has_explicit_date)


def _parse_hhmm(value: str, *, allow_2400: bool = False) -> int | None:
    if len(value) != 4 or not value.isdigit():
        return None
    hour = int(value[:2])
    minute = int(value[2:])
    if minute >= 60 or hour > 24 or (hour == 24 and not allow_2400):
        return None
    if hour == 24 and minute != 0:
        return None
    return hour * 60 + minute
