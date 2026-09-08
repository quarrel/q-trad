"""Small fully shaped plumbing smoke; synthetic fixtures are not research capability evidence."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from experiments.r4_p1_learnability.analysis import (
    append_event,
    linear_probes,
    residual_diagnostics,
)
from experiments.r4_p1_learnability.capability import planted
from experiments.r4_p1_learnability.data import Panel
from experiments.r4_p1_learnability.ridge import ridge_prediction
from experiments.r4_p1_learnability.run import neural_screen
from experiments.r4_p1_learnability.training import Policy, train


def test_shaped_end_to_end_and_failure_branch(tmp_path: Path) -> None:
    rng = np.random.default_rng(7)
    values = rng.normal(size=(180, 20, 26))
    observed = np.ones_like(values, dtype=bool)
    residual = values[..., 0] * 0.003
    times = np.arange(180, dtype=np.int64) * 60_000_000
    panel = Panel(
        times,
        values,
        observed,
        residual,
        residual * 0,
        residual,
        np.ones((180, 20), dtype=bool),
        np.broadcast_to(times[:, None] + 15 * 60_000_000, (180, 20)).copy(),
        np.repeat(np.array([1, 2, 3], dtype=np.int64), 60),
    )
    policy = Policy(device="cpu", updates=8, hidden=8)
    training, _ = planted(policy, "local")
    _, _, capability_fit = train("local", training, policy)
    assert capability_fit["updates"] == 8
    assert capability_fit["initial_max_correction"] == 0
    failed = tmp_path / "failed.jsonl"
    append_event(failed, "R4P1_POLICY_FREEZE", decision="TRAINING_CAPABILITY_NOT_ESTABLISHED")
    diagnostics = residual_diagnostics(panel, failed)
    assert diagnostics["records"]
    linear = linear_probes(panel, failed)
    assert set(linear["table"]) == {"DEV_2", "DEV_3", "DEV_COMBINED"}
    # Successful-freeze fixture tests the downstream branch without claiming planted recovery.
    success = tmp_path / "success.jsonl"
    append_event(
        success,
        "R4P1_POLICY_FREEZE",
        decision="CAPABILITY_ESTABLISHED",
        policy_identity=policy.identity,
    )
    neural = neural_screen(panel, success, policy, False, tmp_path)
    assert set(neural["table"]["DEV_2"]) == {"zero", "ridge", "local", "pooled"}


def test_ridge_is_alpha_one_with_intercept() -> None:
    x = np.array([[0.0], [2.0]])
    y = np.array([1.0, 5.0])
    prediction = ridge_prediction(x, y, np.array([[1.0], [2.0]]))
    np.testing.assert_allclose(prediction, [3.0, 3 + 4 / 3])
    assert prediction[0] == pytest.approx(y.mean())
