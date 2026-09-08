"""Lagged common components cannot contain another node's immature outcome."""

from dataclasses import replace
from pathlib import Path

import numpy as np

from experiments.r4_p1_learnability.analysis import append_event, residual_diagnostics
from experiments.r4_p1_learnability.data import Panel
from experiments.r4_residual_graph.foundation import ALL_INSTRUMENTS


def test_source_common_component_uses_only_mature_residuals(tmp_path: Path) -> None:
    rng = np.random.default_rng(8)
    minutes = np.array([0, 1, 2, 3, 4, 15, 16, 17, 18, 19], dtype=np.int64)
    times = minutes * 60_000_000
    residual = rng.normal(size=(10, 20))
    values = np.ones((10, 20, 26))
    maturity = np.broadcast_to(times[:, None] + 15 * 60_000_000, (10, 20)).copy()
    maturity[:5, 3] = 100 * 60_000_000
    panel = Panel(
        times,
        values,
        values.astype(bool),
        residual,
        residual * 0,
        residual,
        np.ones((10, 20), dtype=bool),
        maturity,
        np.ones(10, dtype=np.int64),
    )
    register = tmp_path / "register.jsonl"
    append_event(register, "R4P1_POLICY_FREEZE", decision="TRAINING_CAPABILITY_NOT_ESTABLISHED")
    original = residual_diagnostics(panel, register)
    changed = residual.copy()
    changed[:5, 3] += rng.normal(size=5) * 1000
    altered = residual_diagnostics(replace(panel, residual=changed), register)
    pairs = [
        (first, second)
        for first, second in zip(original["records"], altered["records"], strict=True)
        if first["lag_minutes"] == 15
        and first["view"] == "common_removed"
        and first["source"] != ALL_INSTRUMENTS[3]
        and first["target"] != ALL_INSTRUMENTS[3]
        and first["correlation"] is not None
    ]
    assert pairs
    assert all(first["correlation"] == second["correlation"] for first, second in pairs)
