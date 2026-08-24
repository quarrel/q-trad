from datetime import UTC, datetime, timedelta

import polars as pl

from experiments.r2_historical_lab.universe import (
    _choose_finalists,
    _training_and_validation,
)


def test_outer_training_is_chronological_and_horizon_mature() -> None:
    start = datetime(2026, 5, 1, tzinfo=UTC)
    rows = pl.DataFrame(
        {
            "instrument_id": ["fx:eur-usd"] * 4,
            "decision_time": [
                start,
                start + timedelta(minutes=1),
                start + timedelta(minutes=2),
                start + timedelta(minutes=3),
            ],
            "target_end": [
                start + timedelta(minutes=1),
                start + timedelta(minutes=4),
                start + timedelta(minutes=3),
                start + timedelta(minutes=4),
            ],
            "target_available_at": [
                start + timedelta(minutes=1),
                start + timedelta(minutes=5),
                start + timedelta(minutes=3),
                start + timedelta(minutes=4),
            ],
            "block": ["TRAINING_ONLY", "TRAINING_ONLY", "DEV_1", "DEV_1"],
        }
    )

    training, validation = _training_and_validation(rows, "DEV_1", ("fx:eur-usd",))

    assert training["decision_time"].to_list() == [start]
    assert validation.height == 2


def test_finalist_selection_is_bounded_and_uses_declared_metrics() -> None:
    rows = [
        {
            "configuration_id": "core-core",
            "training_universe": "CORE_6",
            "evaluation_universe": "CORE_6",
            "model": "FULLY_POOLED_LOCAL_RIDGE",
            "skill_versus_zero": -0.2,
            "equal_group_then_instrument_skill": -0.1,
        },
        {
            "configuration_id": "all-core",
            "training_universe": "ALL_20",
            "evaluation_universe": "CORE_6",
            "model": "FULLY_POOLED_LOCAL_RIDGE",
            "skill_versus_zero": 0.1,
            "equal_group_then_instrument_skill": 0.0,
        },
        {
            "configuration_id": "all-local",
            "training_universe": "ALL_20",
            "evaluation_universe": "ALL_20",
            "model": "LOCAL_RIDGE",
            "skill_versus_zero": 0.01,
            "equal_group_then_instrument_skill": 0.02,
        },
        {
            "configuration_id": "all-pooled",
            "training_universe": "ALL_20",
            "evaluation_universe": "ALL_20",
            "model": "FULLY_POOLED_LOCAL_RIDGE",
            "skill_versus_zero": 0.03,
            "equal_group_then_instrument_skill": 0.04,
        },
        {
            "configuration_id": "all-group",
            "training_universe": "ALL_20",
            "evaluation_universe": "ALL_20",
            "model": "GROUP_POOLED_RIDGE",
            "skill_versus_zero": 0.05,
            "equal_group_then_instrument_skill": 0.06,
        },
    ]

    assert _choose_finalists(rows) == ["all-core", "all-pooled", "all-group"]
