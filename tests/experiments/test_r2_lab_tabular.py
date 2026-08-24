from __future__ import annotations

from datetime import UTC, datetime

import polars as pl
import pytest

from experiments.r2_historical_lab.tabular import (
    _development_targets,
    _qualifies,
    configuration_id,
    evaluate,
    select_finalists,
    select_secondary_p1_configurations,
)


def test_evaluate_compares_directly_with_zero_and_balances_instruments() -> None:
    times = [datetime(2026, 5, 16, tzinfo=UTC), datetime(2026, 5, 30, tzinfo=UTC)]
    targets = pl.DataFrame(
        {
            "instrument_id": ["a", "a", "b", "b"],
            "decision_time": times * 2,
            "horizon_minutes": [15] * 4,
            "target_return": [1.0, 2.0, -1.0, -2.0],
            "target_valid": [True] * 4,
            "block": ["DEV_1", "DEV_2"] * 2,
        }
    )
    predictions = targets.select(
        "instrument_id",
        "decision_time",
        "horizon_minutes",
    ).with_columns(pl.Series("expected_return", [1.0, 1.0, -1.0, -1.0]))

    result = evaluate(predictions, targets, model_name="TEST")

    assert result["support"] == 4
    assert result["forecast_coverage"] == 1.0
    assert result["zero_mse"] == 2.5
    assert result["model_mse"] == 0.5
    assert result["direct_delta_mse_versus_zero"] == -2.0
    assert result["skill_versus_zero"] == 0.8
    assert result["positive_block_count"] == 2
    assert result["positive_instrument_count"] == 2
    assert result["calibration_slope"] == pytest.approx(2.0)
    assert result["best_instrument_contribution"] == 0.5
    assert result["best_period_contribution"] == 0.75


def test_development_targets_use_retained_membership_not_adjacent_block_rows() -> None:
    times = [
        datetime(2026, 5, 16, tzinfo=UTC),
        datetime(2026, 5, 30, tzinfo=UTC),
        datetime(2026, 6, 13, tzinfo=UTC),
        datetime(2026, 6, 25, tzinfo=UTC),
    ]
    rows = pl.DataFrame(
        {
            "target_id": ["keep-1", "keep-2", "keep-3", "timestamp-only"],
            "instrument_id": ["commodity:spot-gold"] * 4,
            "decision_time": times,
            "target_available_at": times,
            "target_return": [0.1, 0.2, 0.3, 0.4],
            "target_valid": [True] * 4,
            "block": ["DEV_1", "DEV_2", "DEV_3", "DEV_3"],
        }
    )
    membership = {
        "DEV_1": ([], ["keep-1"]),
        "DEV_2": ([], ["keep-2"]),
        "DEV_3": ([], ["keep-3"]),
    }

    selected = _development_targets(
        rows,
        membership,
        ("commodity:spot-gold",),
        datetime(2026, 6, 26, 14, 6, tzinfo=UTC),
    )

    assert selected["target_id"].to_list() == ["keep-1", "keep-2", "keep-3"]


def test_advancement_requires_broad_positive_pre_holdout_evidence() -> None:
    good = {
        "skill_versus_zero": 0.01,
        "positive_block_count": 2,
        "positive_instrument_count": 3,
        "best_instrument_contribution": 0.6,
        "best_period_contribution": 0.7,
    }
    assert _qualifies(good, 0.8)
    assert not _qualifies({**good, "skill_versus_zero": 0.0}, 0.8)
    assert not _qualifies({**good, "positive_block_count": 1}, 0.8)
    assert not _qualifies({**good, "positive_instrument_count": 1}, 0.8)
    assert not _qualifies({**good, "best_instrument_contribution": 0.81}, 0.8)
    assert not _qualifies({**good, "best_period_contribution": 0.81}, 0.8)


def test_select_finalists_freezes_at_most_one_configuration_per_family() -> None:
    configurations = [
        {
            "model_family": family,
            "variant": variant,
            "feature_set": "P0",
        }
        for family in ("HISTOGRAM_GRADIENT_BOOSTING", "POOLED_MLP")
        for variant in ("A", "B")
    ]
    rows = []

    for index, configuration in enumerate(configurations):
        rows.append(
            {
                "configuration_id": configuration_id(configuration),
                "split": "PRE_HOLDOUT",
                "attempt_status": "SUCCEEDED",
                "skill_versus_zero": 0.01 + index * 0.001,
                "positive_block_count": 2,
                "positive_instrument_count": 3,
                "best_instrument_contribution": 0.5,
                "best_period_contribution": 0.5,
            }
        )

    finalists = select_finalists(rows, configurations, 0.8)

    assert len(finalists) == 2
    assert {item["model_family"] for item in finalists} == {
        "HISTOGRAM_GRADIENT_BOOSTING",
        "POOLED_MLP",
    }
    assert {item["variant"] for item in finalists} == {"B"}


def test_secondary_p1_comparison_uses_best_p0_variant_without_advancement() -> None:
    configurations = [
        {
            "model_family": family,
            "variant": variant,
            "feature_set": "P0",
        }
        for family in ("HISTOGRAM_GRADIENT_BOOSTING", "POOLED_MLP")
        for variant in ("A", "B")
    ]
    rows = [
        {
            "configuration_id": configuration_id(configuration),
            "split": "PRE_HOLDOUT",
            "attempt_status": "SUCCEEDED",
            "skill_versus_zero": -0.02 if configuration["variant"] == "A" else -0.01,
        }
        for configuration in configurations
    ]

    selected = select_secondary_p1_configurations(rows, configurations)

    assert len(selected) == 2
    assert {item["variant"] for item in selected} == {"B"}
