import hashlib
import json
from datetime import UTC, datetime, timedelta
from math import log, pi, sin
from pathlib import Path

import polars as pl
import pytest

from experiments.r2_historical_lab import LABEL, SOURCE_CLASS
from experiments.r2_historical_lab.features import add_pooled_features
from experiments.r2_historical_lab.harness import (
    MANIFEST_CONTRACT,
    append_attempt,
    evaluate_against_zero,
    freeze_finalists,
    load_parts,
)
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
    assert gap_row["gap_known_by_cutoff"] == 0.0


def test_targets_require_exact_endpoints_and_preserve_opportunities() -> None:
    start = datetime(2026, 2, 2, 0, 1, tzinfo=UTC)
    blocks = (("DEV_1", start, start + timedelta(minutes=10)),)
    targets = _targets(_source(), ((start, start + timedelta(minutes=7)),), 5, blocks)

    valid = targets.filter(pl.col("decision_time") == start).row(0, named=True)
    assert valid["target_end"] == start + timedelta(minutes=5)
    assert valid["target_available_at"] == start + timedelta(minutes=10)
    assert valid["target_valid"] is True
    assert len(valid["target_id"]) == 64
    assert valid["target_revision_policy"] == "PROVISIONAL_CONSERVATIVE"
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


def test_features_use_production_time_population_and_activity_semantics() -> None:
    ends = [
        datetime(2026, 2, 2, 0, minute, tzinfo=UTC)
        for minute in range(1, 7)
    ]
    closes = [1.0, 2.0, 2.0, 4.0, 8.0, 8.0]
    source = pl.DataFrame(
        {
            "interval_end": ends,
            "available_at": [value + timedelta(minutes=5) for value in ends],
            "high": [value * 1.01 for value in closes],
            "low": [value * 0.99 for value in closes],
            "close": closes,
        }
    )
    features = _features(
        source,
        ((datetime(2026, 2, 2, 0, 0, tzinfo=UTC), datetime(2026, 2, 2, 0, 20, tzinfo=UTC)),),
        "fx:eur-usd",
    )
    row = features.filter(
        pl.col("decision_time") == datetime(2026, 2, 2, 0, 11, tzinfo=UTC)
    ).row(0, named=True)
    returns = [log(2.0), 0.0, log(2.0), log(2.0), 0.0]
    mean = sum(returns) / len(returns)
    population_std = (sum((value - mean) ** 2 for value in returns) / len(returns)) ** 0.5

    assert row["realised_std_300s"] == pytest.approx(population_std)
    assert row["utc_day_sin"] == pytest.approx(sin(2 * pi * 0 / 7))

    late = datetime(2026, 2, 2, 23, 0, tzinfo=UTC)
    off_session = _features(
        pl.DataFrame(
            {
                "interval_end": [late - timedelta(minutes=5)],
                "available_at": [late],
                "high": [1.1],
                "low": [0.9],
                "close": [1.0],
            }
        ),
        ((late, late + timedelta(minutes=1)),),
        "fx:eur-usd",
    ).row(0, named=True)
    assert off_session["utc_minute_sin"] == pytest.approx(sin(2 * pi * 1380 / 1440))
    assert off_session["source_active"] == 0.0
    assert off_session["latest_feature_bar_end"] == late - timedelta(minutes=5)


def test_pooled_features_exclude_self_and_use_market_group() -> None:
    decision = datetime(2026, 2, 2, 0, 11, tzinfo=UTC)
    local = _features(
        _source(),
        ((decision, decision + timedelta(minutes=1)),),
        "fx:eur-usd",
    )
    context = pl.DataFrame(
        {
            "instrument_id": ["fx:eur-usd", "fx:aud-usd", "index:us-500"],
            "market_group": ["FX", "FX", "INDEX"],
            "decision_time": [decision] * 3,
            "current_available": [1.0, 1.0, 1.0],
            "return_60s": [10.0, 1.0, 3.0],
            "return_300s": [20.0, 2.0, 4.0],
        }
    )

    row = add_pooled_features(local, context, "fx:eur-usd").row(0, named=True)

    assert row["loo_mean_return_60s"] == 2.0
    assert row["loo_market_group_mean_return_60s"] == 1.0
    assert row["loo_available_count_60s"] == 2.0
    assert row["cross_market_available_count"] == 2.0



def test_common_evaluator_compares_directly_with_zero() -> None:
    times = [
        datetime(2026, 6, 1, tzinfo=UTC),
        datetime(2026, 6, 2, tzinfo=UTC),
    ]
    instruments = ["fx:eur-usd", "fx:aud-usd"]
    targets = pl.DataFrame(
        {
            "instrument_id": [
                instruments[0], instruments[0], instruments[1], instruments[1]
            ],
            "decision_time": times * 2,
            "horizon_minutes": [15] * 4,
            "target_return": [1.0, 2.0, -1.0, -2.0],
            "target_valid": [True] * 4,
            "block": ["DEV_1", "DEV_2"] * 2,
        }
    )
    predictions = targets.select(
        "instrument_id", "decision_time", "horizon_minutes"
    ).with_columns(pl.Series("expected_return", [1.0, 1.0, -1.0, -1.0]))

    result = evaluate_against_zero(predictions, targets, model_name="TEST_MODEL")

    assert result["support"] == 4
    assert result["forecast_coverage"] == 1.0
    assert result["zero_return_instrument_balanced_mse"] == 2.5
    assert result["model_instrument_balanced_mse"] == 0.5
    assert result["direct_delta_mse_versus_zero"] == -2.0
    assert result["skill_versus_zero"] == 0.8
    assert result["positive_chronological_block_count"] == 2
    assert result["positive_instrument_count"] == 2
    assert result["calibration_slope"] == pytest.approx(2.0)
    spearman = result["spearman_correlation"]
    assert isinstance(spearman, float)
    assert spearman > 0.8
    assert result["best_instrument_contribution"] == 0.5
    assert result["best_period_contribution"] == 0.75

def test_terminal_loader_requires_authenticated_finalist_freeze(tmp_path: Path) -> None:
    dev_time = datetime(2026, 6, 15, tzinfo=UTC)
    terminal_time = datetime(2026, 7, 1, tzinfo=UTC)
    part = tmp_path / "targets.parquet"
    pl.DataFrame(
        {
            "instrument_id": ["fx:eur-usd", "fx:eur-usd"],
            "decision_time": [dev_time, terminal_time],
            "horizon_minutes": [15, 15],
            "block": ["DEV_1", "TERMINAL_FORMER_HOLDOUT"],
        }
    ).write_parquet(part)
    feature_part = tmp_path / "features.parquet"
    pl.DataFrame(
        {
            "instrument_id": ["fx:eur-usd", "fx:eur-usd"],
            "decision_time": [dev_time, terminal_time],
            "return_60s": [0.1, 0.2],
        }
    ).write_parquet(feature_part)
    part_sha = hashlib.sha256(part.read_bytes()).hexdigest()
    feature_sha = hashlib.sha256(feature_part.read_bytes()).hexdigest()
    manifest = {
        "contract": MANIFEST_CONTRACT,
        "evidence_label": LABEL,
        "source_class": SOURCE_CLASS,
        "status": "COMPLETE",
        "authoritative": False,
        "baseline_reconstruction": {
            "contract": "qtrad-r2-historical-lab-baseline-reconstruction-v2",
            "support_exact": True,
            "ordering_zero_pooled_local": True,
            "fit_count": 21,
            "maximum_metric_abs_delta": 0.0,
            "maximum_preprocessing_abs_delta": 0.0,
            "maximum_coefficient_abs_delta": 0.0,
            "maximum_intercept_abs_delta": 0.0,
            "tolerances": {
                "metric_abs": 1e-14,
                "preprocessing_abs": 1e-12,
                "coefficient_abs": 1e-8,
                "intercept_abs": 1e-9,
            },
        },
        "instruments": ["fx:eur-usd"],
        "horizons_minutes": [15],
        "fold_blocks": [
            {
                "name": "DEV_1",
                "start": datetime(2026, 6, 1, tzinfo=UTC).isoformat(),
                "end": datetime(2026, 6, 20, tzinfo=UTC).isoformat(),
                "selection_prohibited": False,
            },
            {
                "name": "DEV_2",
                "start": datetime(2026, 6, 20, tzinfo=UTC).isoformat(),
                "end": datetime(2026, 6, 23, tzinfo=UTC).isoformat(),
                "selection_prohibited": False,
            },
            {
                "name": "DEV_3",
                "start": datetime(2026, 6, 23, tzinfo=UTC).isoformat(),
                "end": datetime(2026, 6, 26, tzinfo=UTC).isoformat(),
                "selection_prohibited": False,
            },
            {
                "name": "TERMINAL_FORMER_HOLDOUT",
                "start": datetime(2026, 6, 26, 14, 6, tzinfo=UTC).isoformat(),
                "end": datetime(2026, 8, 1, 23, 36, tzinfo=UTC).isoformat(),
                "selection_prohibited": True,
            },
        ],
        "parts": [
            {
                "kind": "target",
                "instrument_id": "fx:eur-usd",
                "horizon_minutes": 15,
                "path": str(part),
                "sha256": part_sha,
            },
            {
                "kind": "feature",
                "instrument_id": "fx:eur-usd",
                "path": str(feature_part),
                "sha256": feature_sha,
            },
        ],
    }
    manifest_path = tmp_path / "lab-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()

    dev_features = load_parts(
        manifest_path,
        manifest_sha,
        kind="feature",
    ).collect()
    assert dev_features["decision_time"].to_list() == [dev_time]

    with pytest.raises(ValueError, match="authenticated finalist freeze"):
        load_parts(
            manifest_path,
            manifest_sha,
            kind="feature",
            blocks=("TERMINAL_FORMER_HOLDOUT",),
        ).collect()

    with pytest.raises(ValueError, match="authenticated finalist freeze"):
        load_parts(
            manifest_path,
            manifest_sha,
            kind="target",
            blocks=("TERMINAL_FORMER_HOLDOUT",),
        ).collect()

    register = tmp_path / "attempts.jsonl"
    configuration = {"alpha": 1.0}
    configuration_id = append_attempt(
        register,
        workstream="LAB-TEST",
        configuration=configuration,
        result={"skill": -0.1},
        manifest_sha256=manifest_sha,
    )
    freeze_path = tmp_path / "finalists.json"
    freeze_finalists(
        register,
        freeze_path,
        workstream="LAB-TEST",
        finalist_configuration_ids=[configuration_id],
        manifest_sha256=manifest_sha,
    )
    freeze_sha = hashlib.sha256(freeze_path.read_bytes()).hexdigest()

    loaded = load_parts(
        manifest_path,
        manifest_sha,
        kind="target",
        blocks=("TERMINAL_FORMER_HOLDOUT",),
        finalist_freeze=freeze_path,
        expected_finalist_freeze_sha256=freeze_sha,
        configuration_id=configuration_id,
    ).collect()
    assert loaded.height == 1
