"""Exact flat feature construction for the exploratory historical lab."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise
from math import cos, log, pi, sin, sqrt

import polars as pl

from experiments.r2_historical_lab import LABEL, SOURCE_CLASS

GRID = timedelta(minutes=1)
FEATURE_LAG = timedelta(minutes=5)
LOCAL_RETURN_NAMES = (
    "return_60s",
    "return_60s_available",
    "return_300s",
    "return_300s_available",
    "return_contrast_60s_300s",
)
LOCAL_VOLATILITY_NAMES = tuple(
    name
    for seconds in (60, 300)
    for name in (
        f"realised_std_{seconds}s",
        f"mean_absolute_return_{seconds}s",
        f"mean_log_range_{seconds}s",
        f"return_sign_balance_{seconds}s",
        f"available_interval_count_{seconds}s",
        f"window_coverage_{seconds}s",
    )
)
TIME_AVAILABILITY_NAMES = (
    "utc_minute_sin",
    "utc_minute_cos",
    "utc_day_sin",
    "utc_day_cos",
    "source_active",
    "target_feature_missing_fraction",
    "cross_market_available_count",
    "quality_healthy",
    "gap_known_by_cutoff",
)
POOLED_NAMES = (
    *(
        name
        for seconds in (60, 300)
        for name in (
            f"loo_mean_return_{seconds}s",
            f"loo_median_return_{seconds}s",
            f"loo_return_dispersion_{seconds}s",
            f"loo_positive_proportion_{seconds}s",
            f"loo_available_count_{seconds}s",
            f"loo_market_group_mean_return_{seconds}s",
            f"loo_market_group_dispersion_{seconds}s",
            f"loo_market_group_available_count_{seconds}s",
            f"vix_context_return_{seconds}s",
        )
    ),
    "cross_market_missing_count",
    "cross_market_source_active_count",
)
FEATURE_NAMES = (
    LOCAL_RETURN_NAMES
    + LOCAL_VOLATILITY_NAMES
    + TIME_AVAILABILITY_NAMES
    + POOLED_NAMES
)
METADATA_NAMES = (
    "instrument_id",
    "decision_time",
    "latest_feature_bar_end",
    "feature_data_asof",
    "feature_available_at",
)
MARKET_GROUPS = {
    "commodity": "COMMODITY",
    "fx": "FX",
    "index": "INDEX",
}


@dataclass(frozen=True, slots=True)
class SourcePoint:
    available_at: datetime
    high: float
    low: float
    close: float


class SourceIndex:
    """Causal exact-minute lookup over one retained Stage 7 instrument."""

    def __init__(
        self,
        source: pl.DataFrame,
        intervals: Sequence[tuple[datetime, datetime]],
    ) -> None:
        self.points = {
            row["interval_end"]: SourcePoint(
                available_at=row["available_at"],
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
            )
            for row in source.iter_rows(named=True)
        }
        self.intervals = tuple(sorted(intervals))
        self.starts = tuple(start for start, _ in self.intervals)

    def active(self, interval_end: datetime, cutoff: datetime) -> bool:
        if interval_end > cutoff:
            return False
        expected_start = interval_end - GRID
        position = bisect_right(self.starts, expected_start) - 1
        if position < 0:
            return False
        start, end = self.intervals[position]
        return start <= expected_start and interval_end <= end

    def selected(self, interval_end: datetime, cutoff: datetime) -> SourcePoint | None:
        point = self.points.get(interval_end)
        if point is None or point.available_at > cutoff:
            return None
        return point


    def direct_return(
        self,
        interval_end: datetime,
        seconds: int,
        cutoff: datetime,
    ) -> float | None:
        start = self.selected(interval_end - timedelta(seconds=seconds), cutoff)
        end = self.selected(interval_end, cutoff)
        if start is None or end is None or start.close <= 0 or end.close <= 0:
            return None
        return log(end.close / start.close)

    def rolling(
        self,
        interval_end: datetime,
        seconds: int,
        cutoff: datetime,
    ) -> dict[str, float | None]:
        minutes = seconds // 60
        endpoints = [
            interval_end - timedelta(minutes=offset)
            for offset in range(minutes, -1, -1)
        ]
        active = [self.active(value, cutoff) for value in endpoints]
        selected = [
            self.selected(value, cutoff) if is_active else None
            for value, is_active in zip(endpoints, active, strict=True)
        ]

        expected_ranges = sum(active[1:])
        ranges = [
            log(point.high / point.low)
            for point, is_active in zip(selected[1:], active[1:], strict=True)
            if is_active and point is not None and point.high > 0 and point.low > 0
        ]
        expected_returns = sum(
            left_active and right_active
            for left_active, right_active in pairwise(active)
        )
        returns = [
            log(right.close / left.close)
            for (left, left_active), (right, right_active) in pairwise(
                zip(selected, active, strict=True)
            )
            if left_active
            and right_active
            and left is not None
            and right is not None
            and left.close > 0
            and right.close > 0
        ]
        range_coverage = len(ranges) / expected_ranges if expected_ranges else None
        return_coverage = len(returns) / expected_returns if expected_returns else None
        mean_return = sum(returns) / len(returns) if returns else None
        realised_std = (
            sqrt(sum((value - mean_return) ** 2 for value in returns) / len(returns))
            if mean_return is not None
            else None
        )
        return {
            f"realised_std_{seconds}s": realised_std if return_coverage is not None else None,
            f"mean_absolute_return_{seconds}s": (
                sum(abs(value) for value in returns) / len(returns)
                if returns and return_coverage is not None
                else None
            ),
            f"mean_log_range_{seconds}s": (
                sum(ranges) / len(ranges) if ranges and range_coverage is not None else None
            ),
            f"return_sign_balance_{seconds}s": (
                sum(value > 0 for value in returns) / len(returns)
                if returns and return_coverage is not None
                else None
            ),
            f"available_interval_count_{seconds}s": float(len(ranges)),
            f"window_coverage_{seconds}s": range_coverage,
        }

    def missing_fraction(self, interval_end: datetime, cutoff: datetime) -> float | None:
        expected = 0
        observed = 0
        for offset in range(4, -1, -1):
            value = interval_end - timedelta(minutes=offset)
            if not self.active(value, cutoff):
                continue
            expected += 1
            observed += self.selected(value, cutoff) is not None
        return 1.0 - observed / expected if expected else None


def opportunity_times(
    intervals: Sequence[tuple[datetime, datetime]],
    *,
    start_at: datetime | None = None,
    end_before: datetime | None = None,
) -> tuple[datetime, ...]:
    values: set[datetime] = set()
    for start, end in intervals:
        cursor = max(start, start_at) if start_at is not None else start
        stop = min(end, end_before) if end_before is not None else end
        while cursor < stop:
            values.add(cursor)
            cursor += GRID
    return tuple(sorted(values))


def _local_row(
    index: SourceIndex,
    instrument_id: str,
    decision_time: datetime,
) -> dict[str, object]:
    interval_end = decision_time - FEATURE_LAG
    current = index.selected(interval_end, decision_time)
    return_60 = index.direct_return(interval_end, 60, decision_time)
    return_300 = index.direct_return(interval_end, 300, decision_time)
    minute = decision_time.hour * 60 + decision_time.minute
    day = decision_time.weekday()
    row: dict[str, object] = {
        "instrument_id": instrument_id,
        "decision_time": decision_time,
        "latest_feature_bar_end": interval_end,
        "feature_data_asof": decision_time,
        "feature_available_at": current.available_at if current is not None else None,
        "return_60s": return_60,
        "return_60s_available": float(return_60 is not None),
        "return_300s": return_300,
        "return_300s_available": float(return_300 is not None),
        "return_contrast_60s_300s": (
            return_60 - return_300 if return_60 is not None and return_300 is not None else None
        ),
        "utc_minute_sin": sin(2 * pi * minute / 1440),
        "utc_minute_cos": cos(2 * pi * minute / 1440),
        "utc_day_sin": sin(2 * pi * day / 7),
        "utc_day_cos": cos(2 * pi * day / 7),
        "source_active": float(index.active(interval_end, decision_time)),
        "target_feature_missing_fraction": index.missing_fraction(interval_end, decision_time),
        "quality_healthy": float(current is not None),
        "gap_known_by_cutoff": 0.0,
        "source_class": SOURCE_CLASS,
        "evidence_label": LABEL,
    }
    row.update(index.rolling(interval_end, 60, decision_time))
    row.update(index.rolling(interval_end, 300, decision_time))
    return row


def build_local_features(
    source: pl.DataFrame,
    intervals: Sequence[tuple[datetime, datetime]],
    instrument_id: str,
    *,
    start_at: datetime | None = None,
    end_before: datetime | None = None,
) -> pl.DataFrame:
    index = SourceIndex(source, intervals)
    rows = [
        _local_row(index, instrument_id, decision_time)
        for decision_time in opportunity_times(intervals, start_at=start_at, end_before=end_before)
    ]
    return pl.DataFrame(rows, infer_schema_length=None)


def build_context(
    source: pl.DataFrame,
    intervals: Sequence[tuple[datetime, datetime]],
    instrument_id: str,
) -> pl.DataFrame:
    index = SourceIndex(source, intervals)
    active_ends = opportunity_times(
        tuple((start + GRID, end + GRID) for start, end in intervals),
    )
    rows = []
    group = MARKET_GROUPS[instrument_id.split(":", maxsplit=1)[0]]
    for interval_end in active_ends:
        decision_time = interval_end + FEATURE_LAG
        current = index.selected(interval_end, decision_time)
        rows.append(
            {
                "instrument_id": instrument_id,
                "market_group": group,
                "decision_time": decision_time,
                "current_available": float(current is not None),
                "return_60s": index.direct_return(interval_end, 60, decision_time),
                "return_300s": index.direct_return(interval_end, 300, decision_time),
            }
        )
    return pl.DataFrame(rows, infer_schema_length=None)


def _peer_aggregates(context: pl.DataFrame, suffix: str, prefix: str = "") -> pl.DataFrame:
    value = f"return_{suffix}s"
    return context.group_by("decision_time").agg(
        pl.col(value).mean().alias(f"{prefix}mean_return_{suffix}s"),
        pl.col(value).median().alias(f"{prefix}median_return_{suffix}s"),
        pl.col(value).std(ddof=0).alias(f"{prefix}return_dispersion_{suffix}s"),
        (pl.col(value) > 0).mean().alias(f"{prefix}positive_proportion_{suffix}s"),
        pl.col(value).count().cast(pl.Float64).alias(f"{prefix}available_count_{suffix}s"),
    )


def add_pooled_features(
    features: pl.DataFrame,
    context: pl.DataFrame,
    instrument_id: str,
) -> pl.DataFrame:
    peers = context.filter(pl.col("instrument_id") != instrument_id)
    group = MARKET_GROUPS[instrument_id.split(":", maxsplit=1)[0]]
    group_peers = peers.filter(pl.col("market_group") == group)
    if peers.is_empty():
        result = features.with_columns(
            pl.lit(0.0).alias("cross_market_available_count"),
            pl.lit(0.0).alias("cross_market_missing_count"),
            pl.lit(0.0).alias("cross_market_source_active_count"),
        )
        for name in POOLED_NAMES:
            if name not in {
                "cross_market_missing_count",
                "cross_market_source_active_count",
            }:
                result = result.with_columns(pl.lit(None, dtype=pl.Float64).alias(name))
        return result

    availability = peers.group_by("decision_time").agg(
        pl.col("current_available").sum().alias("cross_market_available_count"),
        pl.len().cast(pl.Float64).alias("cross_market_source_active_count"),
    ).with_columns(
        (
            pl.col("cross_market_source_active_count")
            - pl.col("cross_market_available_count")
        ).alias("cross_market_missing_count")
    )
    result = features.join(availability, on="decision_time", how="left").with_columns(
        pl.col(
            "cross_market_available_count",
            "cross_market_source_active_count",
            "cross_market_missing_count",
        ).fill_null(0.0)
    )
    for suffix in ("60", "300"):
        global_values = _peer_aggregates(peers, suffix, "loo_")
        result = result.join(global_values, on="decision_time", how="left")
        if group_peers.is_empty():
            result = result.with_columns(
                pl.lit(None, dtype=pl.Float64).alias(
                    f"loo_market_group_mean_return_{suffix}s"
                ),
                pl.lit(None, dtype=pl.Float64).alias(
                    f"loo_market_group_dispersion_{suffix}s"
                ),
                pl.lit(0.0).alias(f"loo_market_group_available_count_{suffix}s"),
            )
        else:
            group_values = group_peers.group_by("decision_time").agg(
                pl.col(f"return_{suffix}s")
                .mean()
                .alias(f"loo_market_group_mean_return_{suffix}s"),
                pl.col(f"return_{suffix}s")
                .std(ddof=0)
                .alias(f"loo_market_group_dispersion_{suffix}s"),
                pl.col(f"return_{suffix}s")
                .count()
                .cast(pl.Float64)
                .alias(f"loo_market_group_available_count_{suffix}s"),
            )
            result = result.join(group_values, on="decision_time", how="left").with_columns(
                pl.col(f"loo_market_group_available_count_{suffix}s").fill_null(0.0)
            )
        result = result.with_columns(
            pl.lit(None, dtype=pl.Float64).alias(f"vix_context_return_{suffix}s")
        )
    return result.select(
        *METADATA_NAMES,
        *FEATURE_NAMES,
        "source_class",
        "evidence_label",
    )


def positive_contribution_share(values: Sequence[float]) -> float:
    positives = [value for value in values if value > 0]
    return max(positives) / sum(positives) if positives else 0.0
