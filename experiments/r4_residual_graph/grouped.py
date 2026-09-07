"""Deterministic grouped-timestamp helpers and outcome-blind instrumentation."""

# ruff: noqa: I001 -- CUDA boundary import must precede Torch.
from __future__ import annotations

import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TypeVar

from .cuda_boundary import CUBLAS_WORKSPACE_CONFIG, DETERMINISTIC_CUDA_POLICY
import numpy as np
import torch
from torch import Tensor

T = TypeVar("T")


def configure_deterministic_cuda(seed: int) -> None:
    current = os.environ.get("CUBLAS_WORKSPACE_CONFIG")
    if current not in {None, CUBLAS_WORKSPACE_CONFIG}:
        raise ValueError("CUBLAS_WORKSPACE_CONFIG conflicts with deterministic contract")
    if torch.cuda.is_initialized() and current != CUBLAS_WORKSPACE_CONFIG:
        raise RuntimeError(
            "deterministic environment must be configured before CUDA initialisation"
        )
    os.environ["CUBLAS_WORKSPACE_CONFIG"] = CUBLAS_WORKSPACE_CONFIG
    policy = dict(DETERMINISTIC_CUDA_POLICY)
    torch.use_deterministic_algorithms(bool(policy["deterministic_algorithms"]))
    torch.backends.cudnn.deterministic = bool(policy["cudnn_deterministic"])
    torch.backends.cudnn.benchmark = bool(policy["cudnn_benchmark"])
    torch.backends.cuda.matmul.allow_tf32 = bool(policy["cuda_matmul_allow_tf32"])
    torch.backends.cudnn.allow_tf32 = bool(policy["cudnn_allow_tf32"])
    torch.set_float32_matmul_precision(str(policy["float32_matmul_precision"]))
    torch.manual_seed(seed)
    if not torch.cuda.is_available():
        raise RuntimeError("R4-P0 grouped runtime requires CUDA")
    torch.cuda.manual_seed_all(seed)


def canonical_timestamp_groups(timestamps: Sequence[int]) -> tuple[tuple[int, ...], ...]:
    groups: list[tuple[int, ...]] = []
    start = 0
    while start < len(timestamps):
        stop = start + 1
        while stop < len(timestamps) and timestamps[stop] == timestamps[start]:
            stop += 1
        if stop < len(timestamps) and timestamps[stop] < timestamps[start]:
            raise ValueError("timestamp rows are not in canonical chronological order")
        groups.append(tuple(range(start, stop)))
        start = stop
    return tuple(groups)


def grouped_weighted_loss(
    predictions: Tensor,
    targets: Tensor,
    target_nodes: Tensor,
    instrument_counts: Tensor,
    instrument_total: int,
) -> Tensor:
    if predictions.shape != targets.shape or predictions.ndim != 1:
        raise ValueError("grouped predictions and targets must be matching vectors")
    weights = 1.0 / (
        instrument_counts[target_nodes].to(device=predictions.device, dtype=torch.float32)
        * instrument_total
    )
    return ((predictions.float() - targets.float()).square() * weights).sum()


@dataclass(frozen=True)
class PerformanceSample:
    family_id: str
    path: str
    rows: int
    timestamps: int
    elapsed_seconds: float
    rows_per_second: float
    timestamps_per_second: float
    forward_calls: int
    backward_calls: int
    optimiser_steps: int
    h2d_bytes: int
    h2d_transfers: int
    loss: float
    prediction_sha256: str
    cuda_allocated: int
    cuda_reserved: int


def measure_path(
    family_id: str,
    path: str,
    *,
    rows: int,
    timestamps: int,
    run: Callable[[], tuple[Tensor, Tensor, dict[str, int]]],
) -> PerformanceSample:
    if path not in {"reference", "grouped"}:
        raise ValueError("instrumented path must be reference or grouped")
    torch.cuda.reset_peak_memory_stats()
    started = time.perf_counter()
    prediction, loss, counts = run()
    torch.cuda.synchronize()
    elapsed = time.perf_counter() - started
    ordered = prediction.detach().to(device="cpu", dtype=torch.float32).numpy()
    import hashlib

    return PerformanceSample(
        family_id,
        path,
        rows,
        timestamps,
        elapsed,
        rows / elapsed,
        timestamps / elapsed,
        counts["forward_calls"],
        counts["backward_calls"],
        counts["optimiser_steps"],
        counts["h2d_bytes"],
        counts["h2d_transfers"],
        float(loss.detach().cpu()),
        hashlib.sha256(np.ascontiguousarray(ordered).tobytes()).hexdigest(),
        torch.cuda.max_memory_allocated(),
        torch.cuda.max_memory_reserved(),
    )


def require_exact_repeat(left: PerformanceSample, right: PerformanceSample) -> None:
    semantic = ("loss", "prediction_sha256", "forward_calls", "backward_calls", "optimiser_steps")
    if any(getattr(left, name) != getattr(right, name) for name in semantic):
        raise ValueError("exact-device semantic repeatability failed")
