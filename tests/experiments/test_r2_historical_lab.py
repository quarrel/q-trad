from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl

from experiments.r2_historical_lab import LABEL, SOURCE_CLASS
from experiments.r2_historical_lab.lab import _features, _fold_rows, _targets, _write_parquet


def _source() -> pl.DataFrame:
    ends = [
        datetime(2026, 2, 2, 0, minute, tzinfo=UTC)
        for minute in (1, 2, 3, 4, 5, 6, 8)
    ]
    return pl.DataFrame(
        {
            "instrument_id": ["fx:eur-usd"] * len(ends),
            "interval_start": [value - timedelta(minutes=1) for value in ends],
            "interval_end": ends,
            "available_at": [value + timedelta(minutes=5) for value in ends],
            "open": [1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6],
            "high": [1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7],
            "low": [0.9, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5],
            "close": [1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6],
        }
    )


def test_features_use_exact_causal_cutoff_and_do_not_bridge_gaps() -> None:
    start = datetime(2026, 2, 2, 0, 6, tzinfo=UTC)
    features = _features(
        _source(),
        ((start, start + timedelta(minutes=8)),),
        "fx:eur-usd",
    )

    row = features.filter(pl.col("decision_time") == start + timedelta(minutes=5)).row(
        0, named=True
    )
    assert row["latest_feature_bar_end"] == start
    assert row["feature_available_at"] == row["decision_time"]
    gap_row = features.filter(
        pl.col("decision_time") == datetime(2026, 2, 2, 0, 13, tzinfo=UTC)
    ).row(0, named=True)
    assert gap_row["return_60s"] is None


def test_targets_require_exact_endpoints_and_preserve_opportunities() -> None:
    start = datetime(2026, 2, 2, 0, 1, tzinfo=UTC)
    blocks = (("DEV_1", start, start + timedelta(minutes=10)),)
    targets = _targets(_source(), ((start, start + timedelta(minutes=7)),), 5, blocks)

    valid = targets.filter(pl.col("decision_time") == start).row(0, named=True)
    assert valid["target_end"] == start + timedelta(minutes=5)
    assert valid["target_available_at"] == start + timedelta(minutes=10)
    assert valid["target_valid"] is True
    missing = targets.filter(
        pl.col("decision_time") == start + timedelta(minutes=1)
    ).row(0, named=True)
    assert missing["target_valid"] is False
    assert targets.height == 7


def test_partition_round_trip_retains_lab_boundaries(tmp_path: Path) -> None:
    path = tmp_path / "features" / "instrument=fx--eur-usd" / "part.parquet"
    start = datetime(2026, 2, 2, 0, 6, tzinfo=UTC)
    reference = _write_parquet(
        _features(_source(), ((start, start + timedelta(minutes=1)),), "fx:eur-usd"),
        path,
    )
    loaded = pl.scan_parquet(path).collect()

    assert reference["row_count"] == loaded.height == 1
    assert loaded["evidence_label"].unique().to_list() == [LABEL]
    assert loaded["source_class"].unique().to_list() == [SOURCE_CLASS]


def test_fold_rows_use_retained_fold_boundaries() -> None:
    start = datetime(2026, 4, 1, tzinfo=UTC)
    retained = [
        {
            "fold_id": f"fold-{index}",
            "validation_start": (start + timedelta(days=index)).isoformat(),
            "validation_end": (start + timedelta(days=index + 1)).isoformat(),
        }
        for index in range(3)
    ]

    rows, blocks = _fold_rows(retained)

    assert [block[0] for block in blocks] == [
        "DEV_1",
        "DEV_2",
        "DEV_3",
        "TERMINAL_FORMER_HOLDOUT",
    ]
    assert rows.height == 16
    maturity = rows.filter(pl.col("horizon_minutes") == 60)[
        "target_maturity_seconds"
    ].unique()
    assert maturity.item() == 3900
