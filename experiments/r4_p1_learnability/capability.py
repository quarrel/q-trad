"""Predeclared planted cases and one fixed real DEV_1 capacity sample."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import torch

from experiments.r4_residual_graph.graph import build_fixed_economic_graph

from .analysis import append_event
from .data import Panel
from .training import Batch, Family, Policy, feature_scaling, predict, sequence_batch, train


def planted(policy: Policy, task: str) -> tuple[Batch, Batch]:
    rng = np.random.default_rng(104)
    values = rng.normal(size=(320, 61, 20, 3)).astype(np.float32)
    signal = values[:, -1, :, 0]
    if task == "local":
        target = signal + 0.5 * values[:, -3, :, 1]
    elif task == "pooled":
        target = (signal.sum(axis=1, keepdims=True) - signal) / 19
    elif task == "fixed":
        target = np.einsum("ij,bj->bi", build_fixed_economic_graph().normalized_adjacency, signal)
    else:
        raise ValueError("unknown planted task")

    def batch(start: int, end: int) -> Batch:
        return Batch(
            torch.tensor(values[start:end], device=policy.device),
            torch.ones((end - start, 20), dtype=torch.bool, device=policy.device),
            torch.tensor(target[start:end], dtype=torch.float32, device=policy.device),
            torch.ones((end - start, 20), dtype=torch.bool, device=policy.device),
        )

    return batch(0, 192), batch(192, 320)


def capability(policy: Policy, tiny: Batch, register: Path) -> dict[str, Any]:
    append_event(
        register, "CAPABILITY_POLICY_STARTED", policy=asdict(policy), identity=policy.identity
    )
    results: dict[str, Any] = {}
    for task, families in [
        ("local", ("local",)),
        ("pooled", ("pooled", "local")),
        ("fixed", ("fixed", "pooled", "shuffled")),
    ]:
        training, evaluation = planted(policy, task)
        case: dict[str, Any] = {}
        for family in families:
            selected: Family
            if family == "local":
                selected = "local"
            elif family == "pooled":
                selected = "pooled"
            elif family == "fixed":
                selected = "fixed"
            else:
                selected = "shuffled"
            model, rms, diagnostics = train(selected, training, policy)
            prediction = predict(model, evaluation, rms)
            target = evaluation.targets.cpu().numpy()
            diagnostics["evaluation_mse"] = float(np.mean(np.square(prediction - target)))
            diagnostics["zero_mse"] = float(np.mean(np.square(target)))
            case[family] = diagnostics
            append_event(register, "CAPABILITY_FIT_COMPLETED", task=task, **diagnostics)
            print(
                f"capability {task}/{family}: "
                f"{diagnostics['evaluation_mse'] / diagnostics['zero_mse']:.6f} zero MSE; "
                f"{diagnostics['seconds']:.1f}s",
                flush=True,
            )
        capable = case[task]
        if task == "local":
            # Exact contemporaneous oracle lacks the independently planted lag-three term.
            incapable = float(
                (evaluation.targets - evaluation.values[:, -1, :, 0]).square().mean().item()
            )
            case["contemporaneous_oracle_mse"] = incapable
        elif task == "pooled":
            incapable = case["local"]["evaluation_mse"]
        else:
            incapable = min(case["pooled"]["evaluation_mse"], case["shuffled"]["evaluation_mse"])
        case["passed"] = (
            capable["evaluation_mse"] <= 0.1 * capable["zero_mse"]
            and capable["evaluation_mse"] <= 0.5 * incapable
        )
        results[task] = case
    _, _, overfit = train("local", tiny, policy)
    overfit["passed"] = overfit["final_loss"] <= min(0.01, 0.01 * overfit["initial_loss"])
    results["tiny_real"] = overfit
    append_event(register, "CAPABILITY_FIT_COMPLETED", task="tiny_real", **overfit)
    results["passed"] = all(
        results[name]["passed"] for name in ("local", "pooled", "fixed", "tiny_real")
    )
    append_event(
        register, "CAPABILITY_POLICY_COMPLETED", identity=policy.identity, passed=results["passed"]
    )
    return results


def tiny_batch(panel: Panel, policy: Policy) -> tuple[Batch, dict[str, Any]]:
    complete = np.flatnonzero((panel.block == 1) & panel.eligible.all(axis=1))
    if len(complete) < 8:
        raise ValueError("DEV_1 cannot provide eight complete all-instrument capacity timestamps")
    keys = complete[np.linspace(0, len(complete) - 1, 8, dtype=np.int64)]
    mean, scale = feature_scaling(panel, keys)
    batch = sequence_batch(panel, keys, panel.eligible[keys], policy, mean, scale)
    return batch, {
        "timestamps_microseconds": panel.times[keys].tolist(),
        "rows": int(panel.eligible[keys].sum()),
        "feature_mean": mean.tolist(),
        "feature_scale": scale.tolist(),
    }
