from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

import numpy as np
import polars as pl

from experiments.r2_historical_lab.sequence import (
    GROUPS,
    SEQUENCE_COLUMNS,
    SequenceIndex,
    Standardiser,
    chronological_fit_validation,
    screening_gate,
)


def _features() -> pl.DataFrame:
    times = [
        datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
        datetime(2026, 1, 1, 0, 6, tzinfo=UTC),
        datetime(2026, 1, 1, 0, 8, tzinfo=UTC),
    ]
    values: dict[str, object] = {
        "instrument_id": ["fx:eur-usd"] * 3,
        "decision_time": times,
    }
    for position, name in enumerate(SEQUENCE_COLUMNS):
        values[name] = [float(position + 1), float(position + 2), float(position + 3)]
    return pl.DataFrame(values)


def test_sequence_index_preserves_missing_minutes_and_never_reads_later_rows() -> None:
    index = SequenceIndex.build(
        _features(),
        ("fx:eur-usd",),
        {"fx:eur-usd": "FX"},
    )
    requested = pl.DataFrame(
        {
            "instrument_id": ["fx:eur-usd"],
            "decision_time": [datetime(2026, 1, 1, 0, 8, tzinfo=UTC)],
        }
    )

    matrix = index.matrix(requested, 4)

    assert matrix.shape == (1, 4, len(SEQUENCE_COLUMNS) + 1 + 1 + len(GROUPS))
    assert matrix[0, :, len(SEQUENCE_COLUMNS)].tolist() == [1.0, 1.0, 0.0, 1.0]
    assert np.all(matrix[0, 2, : len(SEQUENCE_COLUMNS)] == 0.0)
    assert matrix[0, -1, 0] == 3.0


def test_chronological_fit_validation_is_ordered_and_bounded() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    frame = pl.DataFrame(
        {
            "decision_time": [start + timedelta(minutes=value) for value in range(20)],
            "instrument_id": ["fx:eur-usd"] * 20,
            "target_return": [float(value) for value in range(20)],
        }
    )

    fit, validation = chronological_fit_validation(
        frame,
        validation_fraction=0.2,
        maximum_fit_rows=5,
        maximum_validation_rows=3,
    )

    assert fit.height == 5
    assert validation.height == 3
    assert cast(datetime, fit["decision_time"].max()) < cast(
        datetime, validation["decision_time"].min()
    )


def test_sequence_screening_requires_all_declared_conditions() -> None:
    mlp = {
        "model_instrument_balanced_mse": 0.9,
    }
    passing = {
        "skill_versus_zero": 0.05,
        "positive_chronological_block_count": 2,
        "positive_instrument_count": 2,
        "model_instrument_balanced_mse": 0.8,
    }
    failed = {
        **passing,
        "positive_instrument_count": 1,
        "model_instrument_balanced_mse": 1.0,
    }

    assert screening_gate(passing, mlp_evaluation=mlp) == (True, ())
    accepted, reasons = screening_gate(failed, mlp_evaluation=mlp)
    assert accepted is False
    assert len(reasons) == 2


def test_standardiser_handles_an_entirely_absent_channel() -> None:
    values = np.array([[1.0, np.nan], [3.0, np.nan]], dtype=np.float32)

    standardiser = Standardiser.fit(values)

    assert standardiser.mean.tolist() == [2.0, 0.0]
    assert standardiser.scale.tolist() == [1.0, 1.0]
    assert standardiser.transform(values).tolist() == [[-1.0, 0.0], [1.0, 0.0]]
