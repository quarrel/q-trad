"""Corrected, bounded temporal optimisation shared by capability and empirical fits."""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass
from typing import Any, Literal

import numpy as np
import torch
from torch import Tensor, nn

from experiments.r4_residual_graph.graph import build_fixed_economic_graph, shuffle_economic_graph

from .data import Array, BoolArray, IntArray, Panel

Family = Literal["local", "pooled", "fixed", "shuffled"]


@dataclass(frozen=True)
class Policy:
    learning_rate: float = 0.003
    updates: int = 2000
    batch_timestamps: int = 32
    hidden: int = 64
    seed: int = 17
    device: str = "cuda"
    lookback: int = 60
    scaling: str = "TRAINING_RMS_NO_CENTRING"
    optimiser: str = "Adam"
    initialisation: str = "ZERO_FINAL_HEAD"
    accumulation: int = 1

    @property
    def identity(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()


def install_numerics(device: str) -> dict[str, Any]:
    if torch.cuda.is_initialized():
        raise RuntimeError("numerical policy must precede CUDA initialisation")
    os.environ["CUBLAS_WORKSPACE_CONFIG"] = ":4096:8"
    torch.set_num_threads(4)
    torch.use_deterministic_algorithms(True)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    torch.set_float32_matmul_precision("highest")
    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA required; no CPU fallback")
    return {
        "device": device,
        "torch": torch.__version__,
        "deterministic_algorithms": torch.are_deterministic_algorithms_enabled(),
        "cudnn_deterministic": torch.backends.cudnn.deterministic,
        "cudnn_benchmark": torch.backends.cudnn.benchmark,
        "matmul_tf32": torch.backends.cuda.matmul.allow_tf32,
        "cudnn_tf32": torch.backends.cudnn.allow_tf32,
        "matmul_precision": torch.get_float32_matmul_precision(),
        "cublas_workspace_config": os.environ["CUBLAS_WORKSPACE_CONFIG"],
    }


class TemporalModel(nn.Module):
    def __init__(self, family: Family, features: int, policy: Policy) -> None:
        super().__init__()
        self.family = family
        self.encoder = nn.LSTM(features, policy.hidden, batch_first=True)
        width = policy.hidden if family == "local" else 2 * policy.hidden
        self.head = nn.Linear(width, 1)
        nn.init.zeros_(self.head.weight)
        nn.init.zeros_(self.head.bias)
        graph = build_fixed_economic_graph()
        matrix = (
            shuffle_economic_graph(graph).normalized_adjacency
            if family == "shuffled"
            else graph.normalized_adjacency
        )
        self.register_buffer("adjacency", torch.tensor(matrix.copy(), dtype=torch.float32))

    def forward(self, values: Tensor, present: Tensor) -> Tensor:
        batch, steps, nodes, features = values.shape
        sequence = values.permute(0, 2, 1, 3).reshape(batch * nodes, steps, features)
        _, (hidden, _) = self.encoder(sequence)
        state = hidden[-1].reshape(batch, nodes, -1)
        if self.family == "local":
            return self.head(state).squeeze(-1)
        active = present.to(state.dtype)
        state = state * active.unsqueeze(-1)
        if self.family == "pooled":
            total = state.sum(dim=1, keepdim=True) - state
            count = active.sum(dim=1, keepdim=True) - active
            context = total / count.clamp_min(1).unsqueeze(-1)
        else:
            context = torch.einsum("ij,bjh->bih", self.adjacency, state)
        return self.head(torch.cat((state, context), dim=-1)).squeeze(-1)


@dataclass(frozen=True)
class Batch:
    values: Tensor
    present: Tensor
    targets: Tensor
    mask: Tensor

    def subset(self, indices: Tensor) -> Batch:
        return Batch(
            self.values[indices], self.present[indices], self.targets[indices], self.mask[indices]
        )


def sequence_batch(
    panel: Panel, keys: IntArray, target_mask: BoolArray, policy: Policy, mean: Array, scale: Array
) -> Batch:
    # Exact minute lookup; absent minutes are masked zeros, never forward-filled.
    requested = panel.times[keys, None] - np.arange(policy.lookback, -1, -1)[None, :] * 60_000_000
    positions = np.searchsorted(panel.times, requested)
    valid = positions < len(panel.times)
    positions = np.minimum(positions, len(panel.times) - 1)
    valid &= panel.times[positions] == requested
    observed = panel.observed[positions] & valid[..., None, None]
    values = np.where(observed, (panel.values[positions] - mean) / scale, 0).astype(np.float32)
    present = observed.any(axis=(1, 3))
    return Batch(
        torch.tensor(values, device=policy.device),
        torch.tensor(present, device=policy.device),
        torch.tensor(panel.residual[keys], dtype=torch.float32, device=policy.device),
        torch.tensor(target_mask, device=policy.device),
    )


def feature_scaling(panel: Panel, keys: IntArray) -> tuple[Array, Array]:
    values, mask = panel.values[keys], panel.observed[keys]
    count = mask.sum(axis=(0, 1))
    if np.any(count == 0):
        raise ValueError("training feature has no observed values")
    mean = (values * mask).sum(axis=(0, 1)) / count
    variance = (np.square(values - mean) * mask).sum(axis=(0, 1)) / count
    scale = np.sqrt(variance)
    scale[scale < 1e-12] = 1.0
    return mean, scale


def train(
    family: Family, batch: Batch, policy: Policy
) -> tuple[TemporalModel, float, dict[str, Any]]:
    torch.manual_seed(policy.seed)  # pyright: ignore[reportUnknownMemberType]
    model = TemporalModel(family, batch.values.shape[-1], policy).to(policy.device)
    optimiser = torch.optim.Adam(model.parameters(), lr=policy.learning_rate)
    target_rms = float(batch.targets[batch.mask].square().mean().sqrt().item())
    if not np.isfinite(target_rms) or target_rms <= 0:
        raise ValueError("training residual RMS must be positive and finite")
    scaled = batch.targets / target_rms
    initial_parameters = [parameter.detach().clone() for parameter in model.parameters()]
    initial = torch.tensor(predict(model, batch, 1.0), device=policy.device)
    initial_max = float(initial.abs().max().item())
    initial_loss = float((initial[batch.mask] - scaled[batch.mask]).square().mean().item())
    if initial_max != 0:
        raise ValueError("initial correction does not preserve comparator")
    generator = torch.Generator(device=policy.device).manual_seed(policy.seed)
    start = time.monotonic()
    max_gradient = 0.0
    completed = 0
    for _ in range(policy.updates):
        indices = torch.randperm(len(batch.values), generator=generator, device=policy.device)[
            : policy.batch_timestamps
        ]
        mini = batch.subset(indices)
        prediction = model(mini.values, mini.present)
        loss = (prediction[mini.mask] - scaled[indices][mini.mask]).square().mean()
        if not bool(torch.isfinite(loss)):
            raise FloatingPointError("non-finite optimisation loss")
        optimiser.zero_grad()
        loss.backward()
        gradient = float(
            torch.nn.utils.clip_grad_norm_(model.parameters(), 10.0, error_if_nonfinite=True).item()
        )
        max_gradient = max(max_gradient, gradient)
        optimiser.step()  # pyright: ignore[reportUnknownMemberType]
        completed += 1
    with torch.no_grad():
        final = torch.tensor(predict(model, batch, 1.0), device=policy.device)
        final_loss = float((final[batch.mask] - scaled[batch.mask]).square().mean().item())
        correction = final[batch.mask] * target_rms
        change = (
            sum(
                float((parameter - before).square().sum().item())
                for parameter, before in zip(model.parameters(), initial_parameters, strict=True)
            )
            ** 0.5
        )
    diagnostics: dict[str, Any] = {
        "family": family,
        "policy_identity": policy.identity,
        "updates": completed,
        "initial_loss": initial_loss,
        "final_loss": final_loss,
        "initial_max_correction": initial_max,
        "target_rms": target_rms,
        "correction_rms": float(correction.square().mean().sqrt().item()),
        "correction_quantiles": np.quantile(
            correction.cpu().numpy(), [0, 0.01, 0.5, 0.99, 1]
        ).tolist(),
        "max_gradient_norm": max_gradient,
        "parameter_change_l2": change,
        "parameters": sum(parameter.numel() for parameter in model.parameters()),
        "seconds": time.monotonic() - start,
    }
    diagnostics["status"] = (
        "TRAINING_FAILURE"
        if final_loss >= initial_loss * 0.99
        or max_gradient <= 0
        or diagnostics["correction_rms"] > 10 * target_rms
        else "TRAINED"
    )
    return model, target_rms, diagnostics


def predict(model: TemporalModel, batch: Batch, rms: float) -> Array:
    pieces: list[Array] = []
    with torch.no_grad():
        for start in range(0, len(batch.values), 64):
            prediction = (
                model(batch.values[start : start + 64], batch.present[start : start + 64]) * rms
            )
            pieces.append(prediction.cpu().numpy().astype(np.float64))
    result = np.concatenate(pieces)
    if not np.isfinite(result).all():
        raise FloatingPointError("non-finite residual prediction")
    return result
