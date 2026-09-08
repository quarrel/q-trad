"""Bounded command: capability, one policy freeze, then B and conditional C."""

from __future__ import annotations

import argparse
import json
import resource
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import torch

from .analysis import (
    append_event,
    comparison,
    complete_table,
    graph_gate,
    linear_probes,
    require_freeze,
    residual_diagnostics,
    sufficient_statistics,
)
from .capability import capability, tiny_batch
from .data import Panel, build_panel, load_development, training_keys
from .training import (
    Family,
    Policy,
    feature_scaling,
    install_numerics,
    predict,
    sequence_batch,
    train,
)


def write_json(path: Path, payload: Any) -> None:
    with path.open("x") as handle:
        json.dump(payload, handle, sort_keys=True, allow_nan=False, indent=2)
        handle.write("\n")


def neural_screen(
    panel: Panel, register: Path, policy: Policy, allow_graph: bool, root: Path
) -> dict[str, Any]:
    freeze = require_freeze(register, neural=True)
    if freeze["policy_identity"] != policy.identity:
        raise ValueError("empirical policy differs from freeze")
    families: tuple[Family, ...] = (
        ("local", "pooled", "fixed", "shuffled") if allow_graph else ("local", "pooled")
    )
    blocks: dict[str, dict[str, dict[str, Any]]] = {"DEV_2": {}, "DEV_3": {}}
    fits: list[dict[str, Any]] = []
    selections = {
        stage: (panel.selected(tuple(range(1, stage)), 5000), panel.selected((stage,), 2000))
        for stage in (2, 3)
    }
    write_json(
        root / "neural-sample-keys.json",
        {
            str(stage): {
                "training": panel.times[keys[0]].tolist(),
                "evaluation": panel.times[keys[1]].tolist(),
            }
            for stage, keys in selections.items()
        },
    )
    for stage in (2, 3):
        training, evaluation = selections[stage]
        training, train_mask = training_keys(panel, training, evaluation)
        mask = panel.eligible[evaluation]
        mean, scale = feature_scaling(panel, training)
        train_batch = sequence_batch(panel, training, train_mask, policy, mean, scale)
        evaluation_batch = sequence_batch(panel, evaluation, mask, policy, mean, scale)
        output = blocks[f"DEV_{stage}"]
        for name, prediction in [
            ("zero", np.zeros_like(panel.target[evaluation])),
            ("ridge", panel.local[evaluation]),
        ]:
            output[name] = sufficient_statistics(
                panel.target[evaluation],
                prediction,
                panel.residual[evaluation],
                prediction - panel.local[evaluation],
                mask,
            )
        for family in families:
            append_event(
                register,
                "NEURAL_FIT_STARTED",
                family=family,
                stage=stage,
                seed=17,
                policy_identity=policy.identity,
            )
            model, rms, diagnostics = train(family, train_batch, policy)
            correction = predict(model, evaluation_batch, rms)
            evaluation_correction = correction[mask]
            diagnostics["evaluation_correction_rms"] = float(
                np.sqrt(np.mean(np.square(evaluation_correction)))
            )
            diagnostics["evaluation_correction_quantiles"] = np.quantile(
                evaluation_correction, [0, 0.01, 0.5, 0.99, 1]
            ).tolist()
            if diagnostics["evaluation_correction_rms"] > 10 * rms:
                diagnostics["status"] = "TRAINING_FAILURE"
            output[family] = sufficient_statistics(
                panel.target[evaluation],
                panel.local[evaluation] + correction,
                panel.residual[evaluation],
                correction,
                mask,
            )
            fits.append({"stage": stage, **diagnostics})
            append_event(register, "NEURAL_FIT_COMPLETED", stage=stage, **diagnostics)
            print(
                f"neural DEV_{stage}/{family}: {diagnostics['status']} "
                f"in {diagnostics['seconds']:.1f}s",
                flush=True,
            )
            del model
        del train_batch, evaluation_batch
    table = complete_table(blocks)
    return {
        "fits": fits,
        "table": table,
        "vs_ridge": {family: comparison(table, family, "ridge") for family in families},
        "graph_gate": graph_gate(table)
        if allow_graph
        else {"passed": False, "reason": "LINEAR_GRAPH_GATE_FAILED"},
        "local_vs_pooled": comparison(table, "local", "pooled"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--capability-only", action="store_true")
    args = parser.parse_args()
    root: Path = args.output
    root.mkdir(parents=True, exist_ok=False)
    register = root / "run-register.jsonl"
    start = time.monotonic()
    numerical = install_numerics("cuda")
    rows, features, provenance = load_development()
    panel = build_panel(rows, features)
    del rows, features
    write_json(root / "input.json", provenance)
    write_json(root / "numerical-policy.json", numerical)
    append_event(
        register,
        "INPUT_PREPARED",
        seconds=time.monotonic() - start,
        eligible_rows=int(panel.eligible.sum()),
        feature_shape=list(panel.values.shape),
    )
    policies = [Policy(updates=updates) for updates in (2000, 4000, 8000)]
    tiny, sample = tiny_batch(panel, policies[0])
    write_json(root / "tiny-sample.json", sample)
    selected: Policy | None = None
    for index, policy in enumerate(policies, 1):
        result = capability(policy, tiny, register)
        write_json(
            root / f"capability-policy-{index}.json", {"policy": asdict(policy), "results": result}
        )
        if result["passed"]:
            selected = policy
            break
    append_event(
        register,
        "R4P1_POLICY_FREEZE",
        decision="CAPABILITY_ESTABLISHED" if selected else "TRAINING_CAPABILITY_NOT_ESTABLISHED",
        policy=asdict(selected) if selected else None,
        policy_identity=selected.identity if selected else None,
    )
    del tiny
    if args.capability_only:
        return
    diagnostics = residual_diagnostics(panel, register)
    write_json(root / "residual-diagnostics.json", diagnostics)
    linear = linear_probes(panel, register)
    write_json(root / "linear-probes.json", linear)
    neural = (
        neural_screen(panel, register, selected, linear["graph_gate"]["passed"], root)
        if selected
        else {"skipped": "TRAINING_CAPABILITY_NOT_ESTABLISHED"}
    )
    write_json(root / "neural-screen.json", neural)
    resources = {
        "seconds": time.monotonic() - start,
        "max_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "cuda_peak_allocated_bytes": torch.cuda.max_memory_allocated(),
        "output_bytes": sum(path.stat().st_size for path in root.iterdir() if path.is_file()),
    }
    write_json(root / "resources.json", resources)
    append_event(register, "RUN_COMPLETED", **resources)
    print(json.dumps({"completed": str(root), "resources": resources}), flush=True)


if __name__ == "__main__":
    main()
