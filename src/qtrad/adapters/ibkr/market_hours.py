"""Authenticated expected-active policy derived from IBKR contract evidence."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from qtrad.domain.time import require_utc
from qtrad.ports.ibkr_capability import IbkrContractEvidence


def ibkr_contract_is_expected_active(
    evidence: IbkrContractEvidence,
    observed_at: datetime,
) -> bool:
    """Return whether the reviewed contract is expected to be trading now.

    IBKR's ``liquidHours`` is part of the exact contract evidence bound into the
    reviewed capture configuration. An absent or not-yet-covered schedule is
    treated as expected-active so an unknown calendar never creates a false
    exemption from freshness checks.
    """

    observed_at = require_utc(observed_at, "observed_at")
    if not evidence.liquid_hours:
        return True
    try:
        timezone = ZoneInfo(evidence.timezone or "UTC")
    except ZoneInfoNotFoundError:
        return True

    local_time = observed_at.astimezone(timezone)
    entries = _parse_liquid_hours(evidence.liquid_hours)
    if not entries:
        return True

    has_current_date = False
    for session_date, intervals in entries:
        if session_date == local_time.date():
            has_current_date = True
        for start_minutes, end_minutes in intervals:
            start = datetime.combine(
                session_date,
                time(hour=start_minutes // 60, minute=start_minutes % 60),
                tzinfo=timezone,
            )
            end = start + timedelta(
                minutes=(
                    end_minutes - start_minutes
                    if end_minutes > start_minutes
                    else 24 * 60 + end_minutes - start_minutes
                )
            )
            if start <= local_time < end:
                return True

    return not has_current_date


def _parse_liquid_hours(
    value: str,
) -> tuple[tuple[date, tuple[tuple[int, int], ...]], ...]:
    entries: list[tuple[date, tuple[tuple[int, int], ...]]] = []
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
            entries.append((session_date, ()))
            continue
        intervals: list[tuple[int, int]] = []
        for window in windows_text.split(","):
            bounds = window.split("-", 1)
            if len(bounds) != 2:
                continue
            start = _parse_hhmm(bounds[0])
            end = _parse_hhmm(bounds[1], allow_2400=True)
            if start is not None and end is not None:
                intervals.append((start, end))
        entries.append((session_date, tuple(intervals)))
    return tuple(entries)


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
