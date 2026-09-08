"""Discriminating P1 boundaries and reducer checks; no retained outcomes."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
import torch

from experiments.r4_p1_learnability.analysis import (
    append_event,
    comparison,
    complete_table,
    context_design,
    require_freeze,
    sufficient_statistics,
)
from experiments.r4_p1_learnability.data import Panel, load_development, training_keys
from experiments.r4_p1_learnability.training import Batch, Policy, TemporalModel, train
from experiments.r4_residual_graph.graph import build_fixed_economic_graph, shuffle_economic_graph


def test_terminal_rejected_before_io() -> None:
    with (
        patch("pathlib.Path.open", side_effect=AssertionError("filesystem touched")),
        pytest.raises(ValueError, match="only DEV"),
    ):
        load_development(("TERMINAL_FORMER_HOLDOUT",))


def test_freeze_order_and_final_failure(tmp_path: Path) -> None:
    register = tmp_path / "register.jsonl"
    with pytest.raises(ValueError, match="POLICY_FREEZE"):
        require_freeze(register)
    append_event(register, "R4P1_POLICY_FREEZE", decision="TRAINING_CAPABILITY_NOT_ESTABLISHED")
    assert require_freeze(register)["decision"] == "TRAINING_CAPABILITY_NOT_ESTABLISHED"
    with pytest.raises(ValueError, match="requires capability"):
        require_freeze(register, neural=True)
    with pytest.raises(ValueError, match="already frozen"):
        append_event(register, "R4P1_POLICY_FREEZE", decision="CAPABILITY_ESTABLISHED")


def test_exact_context_and_availability() -> None:
    values = np.arange(40, dtype=float).reshape(2, 20, 1)
    observed = np.ones_like(values, dtype=bool)
    observed[:, 3] = False
    values[:, 3] = 0
    graph = build_fixed_economic_graph()
    for family, weights in [
        ("fixed", graph.normalized_adjacency),
        ("shuffled", shuffle_economic_graph(graph).normalized_adjacency),
        ("pooled", np.ones((20, 20)) - np.eye(20)),
    ]:
        result = context_design(values, observed, family)
        expected = weights @ values[0, :, 0] / (weights @ observed[0, :, 0])
        np.testing.assert_allclose(result[0, :, 1], expected)
        np.testing.assert_array_equal(result[..., :1], values)
    changed = values.copy()
    changed[:, 0] += 100
    np.testing.assert_array_equal(
        context_design(changed, observed, "pooled")[:, 0, 1],
        context_design(values, observed, "pooled")[:, 0, 1],
    )


def test_equal_instrument_union_and_concentration() -> None:
    blocks = {}
    for block, n in [("DEV_2", 2), ("DEV_3", 5)]:
        counts = np.arange(1, 21) * n
        blocks[block] = {
            "zero": {
                "counts": counts.tolist(),
                "sse": (counts * 4).tolist(),
                "residual_sse": (counts * 4).tolist(),
            },
            "candidate": {
                "counts": counts.tolist(),
                "sse": (counts * (1 if n == 2 else 2)).tolist(),
                "residual_sse": counts.tolist(),
            },
        }
    table = complete_table(blocks)
    assert table["DEV_COMBINED"]["candidate"]["mse"] == pytest.approx(12 / 7)
    result = comparison(table, "candidate", "zero")["DEV_COMBINED"]
    assert result["delta"] > 0
    assert result["breadth"] == 20
    assert result["best_instrument_share"] == pytest.approx(0.05)
    negative = comparison(table, "zero", "candidate")["DEV_COMBINED"]
    assert negative["breadth"] == 0
    assert negative["best_instrument_share"] == 1


def test_candidate_omission_fails() -> None:
    values = np.zeros((2, 20))
    mask = np.ones_like(values, dtype=bool)
    with pytest.raises(ValueError, match="support mismatch"):
        sufficient_statistics(values, values[:1], values, values, mask)
    bad = values.copy()
    bad[0, 0] = np.nan
    with pytest.raises(ValueError, match="non-finite"):
        sufficient_statistics(values, bad, values, values, mask)


def test_actual_updates_scale_and_initialisation() -> None:
    torch.set_num_threads(2)
    torch.manual_seed(9)
    policy = Policy(device="cpu", updates=8, hidden=8, batch_timestamps=4)
    x = torch.randn(8, 3, 20, 2)
    mask = torch.ones((8, 20), dtype=torch.bool)
    y = x[:, -1, :, 0] * 0.003
    batch = Batch(x, mask, y, mask)
    model = TemporalModel("local", 2, policy)
    assert torch.count_nonzero(model(x, mask)).item() == 0
    _, rms, diagnostics = train("local", batch, policy)
    assert diagnostics["updates"] == 8
    assert diagnostics["max_gradient_norm"] > 0
    assert diagnostics["parameter_change_l2"] > 0
    assert rms == pytest.approx(float(y.square().mean().sqrt()))
    assert diagnostics["initial_max_correction"] == 0


def test_training_maturity_is_strict() -> None:
    values = np.ones((3, 20, 1))
    target = np.ones((3, 20))
    panel = Panel(
        np.array([10, 20, 30], dtype=np.int64),
        values,
        values.astype(bool),
        target,
        target,
        target,
        target.astype(bool),
        np.array([[19] * 20, [30] * 20, [40] * 20], dtype=np.int64),
        np.array([1, 1, 2], dtype=np.int64),
    )
    _, mask = training_keys(panel, np.array([0, 1], dtype=np.int64), np.array([2], dtype=np.int64))
    assert mask[0].all()
    assert not mask[1].any()


def test_register_json_is_finite(tmp_path: Path) -> None:
    register = tmp_path / "register.jsonl"
    append_event(register, "TEST", value=3)
    assert json.loads(register.read_text())["value"] == 3
