"""Bounded, non-scientific CUDA qualification of the grouped production runtime."""

from __future__ import annotations

import argparse
import builtins
import hashlib
import io
import json
import os
import re
import resource
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, cast

import numpy as np
import torch
from torch.utils.checkpoint import checkpoint

from .attempt_artifacts import canonical_json, create_json_once, sha256_bytes
from .candidate_validation import authenticate_candidate_validation, candidate_head
from .cuda_boundary import CUBLAS_WORKSPACE_CONFIG, DETERMINISTIC_CUDA_POLICY
from .foundation import ALL_INSTRUMENTS
from .grouped import configure_deterministic_cuda
from .projection_evidence import authenticate_capacity_evidence
from .runtime import (
    FITTED_FAMILY_IDS,
    TIMESTAMP_BATCH_SIZE,
    FrozenRuntimeConfig,
    RuntimeTelemetry,
    _StreamingPredictionBatch,
    _StreamingResidualTrainingBatch,
    build_family_model,
    environment_identity,
    fit_one_model,
    predict_residual,
)
from .stage_cache import (
    CacheInput,
    cache_builder_semantic_closure,
    load_stage_cache_batches,
)

_F64_RTOL = 1e-12
_F64_ATOL = 1e-12
_F32_LOSS_ATOL = 1e-6
_F32_LOSS_RTOL = 5e-5
_F32_GRAD_ATOL = 1e-6
_F32_GRAD_RTOL = 1e-4
_PREDICTION_RTOL = 1e-5
_PREDICTION_ATOL = 1e-6
_QUALIFICATION_SEED = 17
_QUALIFICATION_OUTPUT_ROOT = Path("/data/q-trad/r4-p0/remediation-7/benchmark-17")


def _state_bytes(model: Any) -> bytes:
    buffer = io.BytesIO()
    torch.save(
        {name: value.detach().cpu() for name, value in sorted(model.state_dict().items())},
        buffer,
    )
    return buffer.getvalue()


def _array_identity(value: np.ndarray) -> str:
    array = np.ascontiguousarray(value)
    return sha256_bytes(
        canonical_json({"dtype": array.dtype.str, "shape": array.shape}) + array.tobytes(order="C")
    )


def semantic_model_hash(model: Any) -> str:
    digest = hashlib.sha256()
    for name, value in sorted(model.state_dict().items()):
        array = value.detach().cpu().contiguous().numpy()
        digest.update(
            canonical_json({"name": name, "dtype": array.dtype.str, "shape": array.shape})
        )
        digest.update(array.tobytes(order="C"))
    return digest.hexdigest()


def semantic_prediction_hash(prediction: torch.Tensor) -> str:
    return _array_identity(prediction.detach().cpu().contiguous().numpy())


def _primitive_masked_lstm(model: Any, sequence: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
    """Evaluate the LSTM primitive without the production grouped method."""
    count, steps, _ = sequence.shape
    hidden = torch.zeros(
        (1, count, model.training_config.hidden_width),
        dtype=sequence.dtype,
        device=sequence.device,
    )
    cell = torch.zeros_like(hidden)
    for step in range(steps):
        _, (next_hidden, next_cell) = model.lstm(sequence[:, step : step + 1, :], (hidden, cell))
        active = mask[:, step].reshape(1, count, 1).to(sequence.dtype)
        hidden = next_hidden * active + hidden * (1.0 - active)
        cell = next_cell * active + cell * (1.0 - active)
    return hidden[-1]


def _oracle_scalar_forward(
    model: Any,
    arrays: Mapping[str, np.ndarray],
    timestamp_index: int,
    target_node: int,
    *,
    dtype: torch.dtype,
) -> torch.Tensor:
    """Independent scalar reference reading one timestamp directly from cache arrays."""
    device = torch.device("cuda")
    values = torch.tensor(
        np.array(arrays["values"][timestamp_index], copy=True), dtype=dtype, device=device
    )
    value_mask = torch.tensor(
        np.array(arrays["value_mask"][timestamp_index], copy=True),
        dtype=torch.bool,
        device=device,
    )
    availability = torch.tensor(
        np.array(arrays["availability_mask"][timestamp_index], copy=True),
        dtype=torch.bool,
        device=device,
    )
    node_mask = torch.tensor(
        np.array(arrays["node_mask"][timestamp_index], copy=True),
        dtype=torch.bool,
        device=device,
    )
    observed = value_mask & availability & node_mask.unsqueeze(-1)
    gated = torch.where(observed, values, torch.zeros_like(values))
    gated = gated * observed.to(dtype).mean(dim=-1).unsqueeze(-1)
    if model.spec.graph_mode == "none":
        hidden = _primitive_masked_lstm(
            model,
            gated[:, target_node, :].unsqueeze(0),
            (node_mask[:, target_node] & observed[:, target_node, :].any(dim=-1)).unsqueeze(0),
        )[0]
        return model.head(hidden).reshape(())
    sequences = gated.permute(1, 0, 2)
    masks = node_mask.permute(1, 0) & observed.any(dim=-1).permute(1, 0)
    node_hidden = _primitive_masked_lstm(model, sequences, masks)
    present = node_mask.any(dim=0)
    node_hidden = node_hidden * present.unsqueeze(-1).to(dtype)
    if model.spec.graph_mode == "pooled":
        weights = present.to(dtype)
        context = (node_hidden * weights.unsqueeze(-1)).sum(dim=0)
        context = context / weights.sum().clamp_min(1.0)
        selected_context = context
    elif model.spec.graph_mode in {"fixed", "shuffled"}:
        selected_context = torch.matmul(model.adjacency[target_node].to(dtype), node_hidden)
    else:
        adjacency = torch.softmax(model.learned_adjacency_logits, dim=-1)
        selected_context = torch.matmul(adjacency[target_node], node_hidden)
    message = torch.tanh(model.message(selected_context))
    return model.head(torch.cat((node_hidden[target_node], message))).reshape(())


def _manual_counts(targets: Sequence[int]) -> tuple[int, ...]:
    counts = [0] * len(ALL_INSTRUMENTS)
    for target in targets:
        if target < 0 or target >= len(counts):
            raise ValueError("oracle target is outside canonical universe")
        counts[target] += 1
    if any(count == 0 for count in counts):
        raise ValueError("oracle requires every canonical instrument")
    return tuple(counts)


def _grouped_counts(targets: Sequence[int]) -> tuple[int, ...]:
    """Grouped-side weighting kept separate so the oracle can detect objective defects."""
    counts = [sum(target == node for target in targets) for node in range(len(ALL_INSTRUMENTS))]
    if any(count == 0 for count in counts):
        raise ValueError("grouped comparison requires every canonical instrument")
    return tuple(counts)


def _oracle_objective(
    model: Any,
    batch: _StreamingResidualTrainingBatch,
    *,
    dtype: torch.dtype,
    capture_outputs: bool = True,
    checkpoint_activations: bool = False,
) -> tuple[torch.Tensor, torch.Tensor | None]:
    if batch.cached_arrays is None:
        raise TypeError("oracle requires verified cached arrays")
    arrays = batch.cached_arrays
    counts = _manual_counts(batch.target_nodes)
    outputs: list[torch.Tensor] = []
    loss = torch.zeros((), dtype=dtype, device="cuda")
    width = FrozenRuntimeConfig().training.batch_size
    for start in range(0, len(batch.target_nodes), width):
        rows = range(start, min(start + width, len(batch.target_nodes)))
        if model.spec.graph_mode == "none":
            timestamp = int(batch.cached_arrays["row_timestamp"][start])
            values = torch.tensor(
                np.array(batch.cached_arrays["values"][timestamp], copy=True),
                dtype=dtype,
                device="cuda",
            )
            value_mask = torch.tensor(
                np.array(batch.cached_arrays["value_mask"][timestamp], copy=True),
                dtype=torch.bool,
                device="cuda",
            )
            availability = torch.tensor(
                np.array(batch.cached_arrays["availability_mask"][timestamp], copy=True),
                dtype=torch.bool,
                device="cuda",
            )
            node_mask = torch.tensor(
                np.array(batch.cached_arrays["node_mask"][timestamp], copy=True),
                dtype=torch.bool,
                device="cuda",
            )
            targets = [batch.target_nodes[row] for row in rows]
            observed = value_mask & availability & node_mask.unsqueeze(-1)
            gated = torch.where(observed, values, torch.zeros_like(values))
            gated = gated * observed.to(dtype).mean(dim=-1).unsqueeze(-1)
            sequences = gated[:, targets, :].permute(1, 0, 2)
            masks = (node_mask[:, targets] & observed[:, targets, :].any(dim=-1)).permute(1, 0)
            if checkpoint_activations:
                chunk_outputs = checkpoint(
                    lambda input_sequences, input_masks: model.head(
                        _primitive_masked_lstm(model, input_sequences, input_masks)
                    ).reshape(-1),
                    sequences,
                    masks,
                    use_reentrant=False,
                    preserve_rng_state=True,
                )
            else:
                chunk_outputs = model.head(_primitive_masked_lstm(model, sequences, masks)).reshape(
                    -1
                )
        else:
            scalar_outputs = []
            for row in rows:

                def scalar_forward(
                    _sentinel: torch.Tensor,
                    *,
                    row_index: int = row,
                ) -> torch.Tensor:
                    return _oracle_scalar_forward(
                        model,
                        arrays,
                        int(arrays["row_timestamp"][row_index]),
                        batch.target_nodes[row_index],
                        dtype=dtype,
                    )

                if checkpoint_activations:
                    scalar_outputs.append(
                        checkpoint(
                            scalar_forward,
                            next(model.parameters()),
                            use_reentrant=False,
                            preserve_rng_state=True,
                        )
                    )
                else:
                    scalar_outputs.append(scalar_forward(next(model.parameters())))
            chunk_outputs = torch.stack(scalar_outputs)
        for offset, row in enumerate(rows):
            target = batch.target_nodes[row]
            output = chunk_outputs[offset]
            residual = torch.tensor(batch.residuals[row], dtype=dtype, device="cuda")
            loss = loss + (output - residual).square() / (counts[target] * len(ALL_INSTRUMENTS))
            outputs.append(output)
    return loss, torch.stack(outputs) if capture_outputs else None


def _checkpointed_oracle_objective(
    model: Any,
    batch: _StreamingResidualTrainingBatch,
    *,
    dtype: torch.dtype,
    capture_outputs: bool = True,
) -> tuple[torch.Tensor, torch.Tensor | None]:
    return _oracle_objective(
        model,
        batch,
        dtype=dtype,
        capture_outputs=capture_outputs,
        checkpoint_activations=True,
    )


def _grouped_objective(
    model: Any,
    batch: _StreamingResidualTrainingBatch,
    *,
    dtype: torch.dtype,
    capture_outputs: bool = True,
) -> tuple[torch.Tensor, torch.Tensor | None]:
    """Qualification-side grouped evaluation using the production grouped forward."""
    if batch.cached_arrays is None:
        raise TypeError("grouped comparison requires verified cached arrays")
    arrays = batch.cached_arrays
    counts = _grouped_counts(batch.target_nodes)
    outputs: list[torch.Tensor | None] = [None] * len(batch.target_nodes)
    loss = torch.zeros((), dtype=dtype, device="cuda")
    timestamp_values = np.asarray(arrays["row_timestamp"])
    ordered_timestamps = tuple(dict.fromkeys(int(item) for item in timestamp_values))
    width = FrozenRuntimeConfig().training.batch_size
    for timestamp_start in range(0, len(ordered_timestamps), width):
        timestamps = ordered_timestamps[timestamp_start : timestamp_start + width]
        rows = [
            row
            for timestamp in timestamps
            for row in np.flatnonzero(timestamp_values == timestamp).tolist()
        ]
        target_batch_values = [timestamps.index(int(timestamp_values[row])) for row in rows]
        values = torch.tensor(
            np.array(arrays["values"][list(timestamps)], copy=True), dtype=dtype, device="cuda"
        )
        value_mask = torch.tensor(
            np.array(arrays["value_mask"][list(timestamps)], copy=True),
            dtype=torch.bool,
            device="cuda",
        )
        availability = torch.tensor(
            np.array(arrays["availability_mask"][list(timestamps)], copy=True),
            dtype=torch.bool,
            device="cuda",
        )
        node_mask = torch.tensor(
            np.array(arrays["node_mask"][list(timestamps)], copy=True),
            dtype=torch.bool,
            device="cuda",
        )
        targets = torch.tensor(
            [batch.target_nodes[row] for row in rows], dtype=torch.long, device="cuda"
        )
        predicted = model.forward_grouped(
            values,
            target_node=targets,
            target_batch=torch.tensor(target_batch_values, dtype=torch.long, device="cuda"),
            value_mask=value_mask,
            availability_mask=availability,
            node_mask=node_mask,
        )
        for offset, row in enumerate(rows):
            output = predicted[offset]
            target = batch.target_nodes[row]
            residual = torch.tensor(batch.residuals[row], dtype=dtype, device="cuda")
            loss = loss + (output - residual).square() / (counts[target] * len(ALL_INSTRUMENTS))
            outputs[row] = output
    if any(item is None for item in outputs):
        raise AssertionError("grouped comparison omitted a row")
    return (
        loss,
        torch.stack(cast(list[torch.Tensor], outputs)) if capture_outputs else None,
    )


def _gradient_arrays(model: Any) -> dict[str, np.ndarray]:
    gradients: dict[str, np.ndarray] = {}
    for name, parameter in model.named_parameters():
        if parameter.grad is None:
            raise AssertionError(f"missing gradient for {name}")
        gradients[name] = parameter.grad.detach().cpu().numpy().copy()
    return gradients


def _gradient_identity(gradients: Mapping[str, np.ndarray]) -> str:
    return sha256_bytes(
        canonical_json(
            [
                {"name": name, "identity": _array_identity(value)}
                for name, value in sorted(gradients.items())
            ]
        )
    )


def _compare_objective_and_gradients(
    family: str,
    initial_state: bytes,
    training: _StreamingResidualTrainingBatch,
    *,
    dtype: torch.dtype,
    release_callback: Callable[[], None],
    phase_callback: Callable[[str], None],
) -> dict[str, Any]:
    cpu_rng_state = torch.get_rng_state()
    cuda_rng_state = torch.cuda.get_rng_state()

    def evaluate(
        path: str,
        objective: Callable[..., tuple[torch.Tensor, object]],
    ) -> tuple[float, dict[str, np.ndarray]]:
        torch.set_rng_state(cpu_rng_state)
        torch.cuda.set_rng_state(cuda_rng_state)
        model = build_family_model(family).to(device="cuda", dtype=dtype)
        model.load_state_dict(torch.load(io.BytesIO(initial_state), weights_only=True))
        model.zero_grad(set_to_none=True)
        phase_callback(f"{path}_forward")
        loss, unused_outputs = objective(model, training, dtype=dtype, capture_outputs=False)
        if unused_outputs is not None:
            raise AssertionError("scalar-only objective unexpectedly returned outputs")
        phase_callback(f"{path}_backward")
        loss.backward()
        value = float(loss.detach().cpu())
        gradients = _gradient_arrays(model)
        del loss, model
        release_callback()
        return value, gradients

    grouped_value, grouped_gradients = evaluate("grouped", _grouped_objective)
    oracle_value, oracle_gradients = evaluate("oracle", _checkpointed_oracle_objective)
    torch.set_rng_state(cpu_rng_state)
    torch.cuda.set_rng_state(cuda_rng_state)

    loss_delta = abs(grouped_value - oracle_value)
    gradient_max = 0.0
    for name, expected in oracle_gradients.items():
        actual = grouped_gradients[name]
        gradient_max = max(gradient_max, float(np.max(np.abs(actual - expected))))
        if dtype == torch.float64:
            if not np.allclose(actual, expected, rtol=_F64_RTOL, atol=_F64_ATOL):
                raise AssertionError(f"float64 grouped/oracle gradient mismatch: {name}")
        elif not np.all(
            np.abs(actual - expected) <= _F32_GRAD_ATOL + _F32_GRAD_RTOL * np.abs(expected)
        ):
            component_delta = np.abs(actual - expected)
            component_limit = _F32_GRAD_ATOL + _F32_GRAD_RTOL * np.abs(expected)
            flat_index = int(component_delta.argmax())
            raise AssertionError(
                f"FP32 grouped/oracle gradient mismatch: {name}; "
                f"delta={float(component_delta.flat[flat_index])}; "
                f"limit={float(component_limit.flat[flat_index])}"
            )
    if dtype == torch.float64:
        if not np.isclose(grouped_value, oracle_value, rtol=_F64_RTOL, atol=_F64_ATOL):
            raise AssertionError("float64 grouped/oracle objective mismatch")
    elif loss_delta > _F32_LOSS_ATOL + _F32_LOSS_RTOL * abs(oracle_value):
        raise AssertionError("FP32 grouped/oracle objective mismatch")
    return {
        "dtype": str(dtype),
        "grouped_loss": grouped_value,
        "oracle_loss": oracle_value,
        "loss_abs_delta": loss_delta,
        "grouped_loss_identity": _array_identity(np.asarray([grouped_value])),
        "oracle_loss_identity": _array_identity(np.asarray([oracle_value])),
        "grouped_gradient_identity": _gradient_identity(grouped_gradients),
        "oracle_gradient_identity": _gradient_identity(oracle_gradients),
        "gradient_max_abs_delta": gradient_max,
    }


def predict_rowwise_oracle(model: Any, batch: _StreamingPredictionBatch) -> torch.Tensor:
    if batch.cached_arrays is None:
        raise TypeError("oracle requires verified cached arrays")
    model.eval()
    dtype = next(model.parameters()).dtype
    with torch.no_grad():
        return torch.stack(
            [
                _oracle_scalar_forward(
                    model,
                    batch.cached_arrays,
                    int(batch.cached_arrays["row_timestamp"][row]),
                    target,
                    dtype=dtype,
                )
                for row, target in enumerate(batch.target_nodes)
            ]
        )


def _oracle_one_step_state(
    family: str,
    initial_state: bytes,
    training: _StreamingResidualTrainingBatch,
    prediction: _StreamingPredictionBatch,
    *,
    checkpoint_activations: bool = True,
    phase_callback: Callable[[str], None] | None = None,
    release_callback: Callable[[], None] | None = None,
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], np.ndarray]:
    def phase(name: str) -> None:
        if phase_callback is not None:
            phase_callback(name)

    oracle = build_family_model(family).to(device="cuda", dtype=torch.float32)
    oracle.load_state_dict(torch.load(io.BytesIO(initial_state), weights_only=True))
    optimizer = torch.optim.Adam(
        oracle.parameters(),
        lr=oracle.training_config.learning_rate,
        weight_decay=oracle.training_config.weight_decay,
    )
    optimizer.zero_grad(set_to_none=True)
    phase("forward")
    loss, unused_outputs = _oracle_objective(
        oracle,
        training,
        dtype=torch.float32,
        capture_outputs=False,
        checkpoint_activations=checkpoint_activations,
    )
    if unused_outputs is not None:
        raise AssertionError("scalar-only objective unexpectedly returned outputs")
    phase("backward")
    loss.backward()
    phase("gradient_copy")
    gradients = _gradient_arrays(oracle)
    del loss
    phase("optimizer_step")
    optimizer.step()
    phase("parameter_copy")
    parameters = {
        name: parameter.detach().cpu().numpy().copy()
        for name, parameter in oracle.named_parameters()
    }
    optimizer.zero_grad(set_to_none=True)
    del optimizer
    if release_callback is not None:
        release_callback()
    phase("prediction")
    oracle_prediction = predict_rowwise_oracle(oracle, prediction).detach().cpu().numpy().copy()
    return gradients, parameters, oracle_prediction


def _maximum_mapping_delta(
    actual: Mapping[str, np.ndarray], expected: Mapping[str, np.ndarray]
) -> float:
    if set(actual) != set(expected):
        raise AssertionError("production/oracle parameter names differ")
    return max(float(np.max(np.abs(actual[name] - expected[name]))) for name in sorted(expected))


@dataclass(frozen=True)
class ReusableOracleEvidence:
    """Authenticated invariant evidence and deterministic state for one repeat/family."""

    family: str
    seed: int
    initial_state: bytes
    production_cpu_rng_state: torch.Tensor
    production_cuda_rng_state: torch.Tensor
    fit_cpu_rng_state: torch.Tensor
    fit_cuda_rng_state: torch.Tensor
    oracle_gradients: Mapping[str, np.ndarray]
    oracle_parameters: Mapping[str, np.ndarray]
    oracle_prediction: np.ndarray
    evidence: Mapping[str, Any]
    identity: str


def _oracle_evidence_identity(bundle: ReusableOracleEvidence) -> str:
    payload = {
        "family": bundle.family,
        "seed": bundle.seed,
        "initial_state_sha256": sha256_bytes(bundle.initial_state),
        "production_cpu_rng_identity": _array_identity(bundle.production_cpu_rng_state.numpy()),
        "production_cuda_rng_identity": _array_identity(
            bundle.production_cuda_rng_state.cpu().numpy()
        ),
        "fit_cpu_rng_identity": _array_identity(bundle.fit_cpu_rng_state.numpy()),
        "fit_cuda_rng_identity": _array_identity(bundle.fit_cuda_rng_state.cpu().numpy()),
        "oracle_gradient_identity": _gradient_identity(bundle.oracle_gradients),
        "oracle_parameter_identity": _gradient_identity(bundle.oracle_parameters),
        "oracle_prediction_identity": _array_identity(bundle.oracle_prediction),
        "evidence": bundle.evidence,
    }
    return sha256_bytes(canonical_json(payload))


def _authenticate_oracle_evidence(
    bundle: ReusableOracleEvidence, *, family: str, seed: int
) -> None:
    if bundle.family != family or bundle.seed != seed:
        raise ValueError("reusable oracle evidence family/seed binding mismatch")
    if _oracle_evidence_identity(bundle) != bundle.identity:
        raise ValueError("reusable oracle evidence identity mismatch")


def build_reusable_oracle_evidence(
    family: str,
    training: _StreamingResidualTrainingBatch,
    prediction: _StreamingPredictionBatch,
    *,
    seed: int,
    phase_callback: Callable[[str], None] | None = None,
    release_callback: Callable[[], None] | None = None,
) -> ReusableOracleEvidence:
    def phase(name: str) -> None:
        if phase_callback is not None:
            phase_callback(name)

    def release() -> None:
        if release_callback is not None:
            release_callback()

    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    initial = build_family_model(family).to("cuda")
    initial_state = _state_bytes(initial)
    del initial
    release()
    f64 = _compare_objective_and_gradients(
        family,
        initial_state,
        training,
        dtype=torch.float64,
        release_callback=release,
        phase_callback=lambda path: phase(f"float64_{path}"),
    )
    f32 = _compare_objective_and_gradients(
        family,
        initial_state,
        training,
        dtype=torch.float32,
        release_callback=release,
        phase_callback=lambda path: phase(f"float32_{path}"),
    )
    production_cpu_rng_state = torch.get_rng_state().clone()
    production_cuda_rng_state = torch.cuda.get_rng_state().clone()
    production_template = build_family_model(family).to("cuda")
    production_template.load_state_dict(torch.load(io.BytesIO(initial_state), weights_only=True))
    initial_model_identity = semantic_model_hash(production_template)
    del production_template
    release()
    oracle_gradients, oracle_parameters, oracle_prediction = _oracle_one_step_state(
        family,
        initial_state,
        training,
        prediction,
        phase_callback=lambda path: phase(f"oracle_one_step_{path}"),
        release_callback=release,
    )
    fit_cpu_rng_state = torch.get_rng_state().clone()
    fit_cuda_rng_state = torch.cuda.get_rng_state().clone()
    release()
    evidence = {
        "family": family,
        "seed": seed,
        "initial_model_identity": initial_model_identity,
        "float64": f64,
        "float32": f32,
        "oracle_pre_step_gradient_identity": _gradient_identity(oracle_gradients),
        "oracle_final_parameter_identity": _gradient_identity(oracle_parameters),
        "oracle_prediction_identity": _array_identity(oracle_prediction),
    }
    provisional = ReusableOracleEvidence(
        family=family,
        seed=seed,
        initial_state=initial_state,
        production_cpu_rng_state=production_cpu_rng_state,
        production_cuda_rng_state=production_cuda_rng_state,
        fit_cpu_rng_state=fit_cpu_rng_state,
        fit_cuda_rng_state=fit_cuda_rng_state,
        oracle_gradients=oracle_gradients,
        oracle_parameters=oracle_parameters,
        oracle_prediction=oracle_prediction,
        evidence=evidence,
        identity="",
    )
    return replace(provisional, identity=_oracle_evidence_identity(provisional))


def compare_production_to_oracle(
    bundle: ReusableOracleEvidence,
    training: _StreamingResidualTrainingBatch,
    prediction: _StreamingPredictionBatch,
    *,
    calibration_batch_size: int | None = None,
    phase_callback: Callable[[str], None] | None = None,
    release_callback: Callable[[], None] | None = None,
) -> dict[str, Any]:
    _authenticate_oracle_evidence(bundle, family=bundle.family, seed=bundle.seed)

    def phase(name: str) -> None:
        if phase_callback is not None:
            phase_callback(name)

    def release() -> None:
        if release_callback is not None:
            release_callback()

    torch.set_rng_state(bundle.production_cpu_rng_state)
    torch.cuda.set_rng_state(bundle.production_cuda_rng_state)
    production = build_family_model(bundle.family).to("cuda")
    production.load_state_dict(torch.load(io.BytesIO(bundle.initial_state), weights_only=True))
    fresh_initial_identity = semantic_model_hash(production)
    if fresh_initial_identity != bundle.evidence["initial_model_identity"]:
        raise AssertionError("production width did not load the reusable identical initial model")
    torch.set_rng_state(bundle.fit_cpu_rng_state)
    torch.cuda.set_rng_state(bundle.fit_cuda_rng_state)
    telemetry = RuntimeTelemetry()
    gradient_probe: dict[str, dict[str, np.ndarray]] = {}
    phase("production_fit")
    fit_one_model(
        production,
        training,
        _telemetry=telemetry,
        _gradient_probe=gradient_probe,
        batch_size=calibration_batch_size,
        _calibration_batch_size=calibration_batch_size,
    )
    gradient_delta = _maximum_mapping_delta(
        gradient_probe["pre_step_gradients"], bundle.oracle_gradients
    )
    parameter_delta = _maximum_mapping_delta(
        gradient_probe["final_parameters"], bundle.oracle_parameters
    )
    if gradient_delta > _F32_GRAD_ATOL + _F32_GRAD_RTOL * max(
        float(np.max(np.abs(value))) for value in bundle.oracle_gradients.values()
    ):
        raise AssertionError("chunked production gradients differ from unchunked oracle")
    parameter_tolerance = 1e-3
    if parameter_delta > parameter_tolerance:
        raise AssertionError(
            "chunked final parameters differ from one-step oracle: "
            f"delta={parameter_delta}, tolerance={parameter_tolerance}"
        )
    production_gradient_identity = _gradient_identity(gradient_probe["pre_step_gradients"])
    production_parameter_identity = _gradient_identity(gradient_probe["final_parameters"])
    del gradient_probe
    release()
    phase("production_prediction")
    production_prediction = predict_residual(
        production,
        prediction,
        _telemetry=telemetry,
        _calibration_batch_size=calibration_batch_size,
    )
    production_prediction_hash = semantic_prediction_hash(production_prediction)
    left = production_prediction.detach().cpu().numpy().copy()
    del production_prediction
    release()
    effective_width = calibration_batch_size or TIMESTAMP_BATCH_SIZE
    expected_timestamp_batches = sum(
        (len(batch.provider_tensor_identities) + effective_width - 1) // effective_width
        for batch in (training, prediction)
    )
    if (
        telemetry.timestamp_batch_calls != expected_timestamp_batches
        or telemetry.forward_calls != expected_timestamp_batches
        or telemetry.materialisation_calls != expected_timestamp_batches
        or telemetry.representation_timestamp_calls != telemetry.timestamps
    ):
        raise AssertionError("production timestamp-batch dispatch counters are inconsistent")
    if production.spec.graph_mode == "none":
        if telemetry.local_vectorised_calls != expected_timestamp_batches:
            raise AssertionError("local production dispatch was not vectorised by timestamp batch")
        representation_policy = "LOCAL_VECTORISED_ISOLATED_TARGET_SEQUENCES"
    else:
        if telemetry.shared_representation_calls != expected_timestamp_batches:
            raise AssertionError("shared representation was not built once per timestamp batch")
        representation_policy = "SHARED_REPRESENTATION_ONCE_PER_TIMESTAMP_BATCH"
    right = bundle.oracle_prediction
    if not np.allclose(left, right, rtol=_PREDICTION_RTOL, atol=_PREDICTION_ATOL):
        raise AssertionError("production/oracle prediction mismatch")
    prediction_max_abs_delta = float(np.max(np.abs(left - right)))
    model_hash = semantic_model_hash(production)
    del production, left, right
    release()
    telemetry_payload = asdict(telemetry)
    telemetry_payload["rows_per_second"] = telemetry.rows / telemetry.elapsed_seconds
    return {
        "family": bundle.family,
        "seed": bundle.seed,
        "fresh_initial_model_identity": fresh_initial_identity,
        "oracle_evidence_identity": bundle.identity,
        "oracle_evidence": dict(bundle.evidence),
        "model_hash": model_hash,
        "prediction_hash": production_prediction_hash,
        "oracle_prediction_identity": bundle.evidence["oracle_prediction_identity"],
        "prediction_max_abs_delta": prediction_max_abs_delta,
        "float64": bundle.evidence["float64"],
        "float32": bundle.evidence["float32"],
        "chunked_pre_step_gradient_max_abs_delta": gradient_delta,
        "production_gradient_identity": production_gradient_identity,
        "chunked_final_parameter_max_abs_delta": parameter_delta,
        "chunked_final_parameter_tolerance": parameter_tolerance,
        "production_final_parameter_identity": production_parameter_identity,
        "production_dispatch": {
            "timestamp_batch_size": effective_width,
            "representation_policy": representation_policy,
            "actual_runtime_functions": ["fit_one_model", "predict_residual"],
        },
        "telemetry": telemetry_payload,
    }


def compare_runtime_to_oracle(
    family: str,
    training: _StreamingResidualTrainingBatch,
    prediction: _StreamingPredictionBatch,
    *,
    seed: int,
    calibration_batch_size: int | None = None,
    phase_callback: Callable[[str], None] | None = None,
    release_callback: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Pre-split compatibility path used to prove reusable-evidence equivalence."""
    bundle = build_reusable_oracle_evidence(
        family,
        training,
        prediction,
        seed=seed,
        phase_callback=phase_callback,
        release_callback=release_callback,
    )
    return compare_production_to_oracle(
        bundle,
        training,
        prediction,
        calibration_batch_size=calibration_batch_size,
        phase_callback=phase_callback,
        release_callback=release_callback,
    )


def _read_cgroup(name: str) -> int | str:
    path = Path("/sys/fs/cgroup") / name
    try:
        value = path.read_text().strip()
    except (FileNotFoundError, PermissionError, OSError):
        return "UNOBSERVABLE"
    if not value or value == "max":
        return "UNOBSERVABLE"
    return int(value)


def _git_head() -> str:
    return candidate_head(Path(__file__).resolve().parents[2])


_DRIVER_QUERY = ("nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader")
_DRIVER_DIAGNOSTIC_LIMIT = 4096


def _driver_result(
    returncode: int | None, exception_class: str | None, stdout: str, stdout_truncated: bool
) -> tuple[str, str | None, str | None]:
    if exception_class is not None:
        exception_type = getattr(builtins, exception_class, None)
        if returncode is not None or not (
            exception_class == "TimeoutExpired"
            or (
                isinstance(exception_type, type)
                and issubclass(exception_type, OSError)
                and exception_type.__name__ == exception_class
            )
        ):
            raise ValueError("driver observation has an inconsistent query exception")
        return "UNAVAILABLE", exception_class, None
    if type(returncode) is not int:
        raise ValueError("driver observation requires an integer query exit code")
    if returncode != 0:
        failure = "WSL_NVML_TELEMETRY_UNAVAILABLE" if returncode == 3 else "NONZERO_EXIT"
        return "UNAVAILABLE", failure, None
    if stdout_truncated:
        return "INVALID", "TRUNCATED_OUTPUT", None
    version = stdout.strip()
    if re.fullmatch(r"[0-9]+(?:\.[0-9]+)+", version) is None:
        return "INVALID", "MALFORMED_OUTPUT" if version else "EMPTY_OUTPUT", None
    return "AVAILABLE", None, version


def validate_driver_observation(observation: Mapping[str, Any]) -> None:
    """Check sealed execution provenance after receipt authentication, without a live query."""
    if set(observation) != {
        "query",
        "timeout_seconds",
        "returncode",
        "exception_class",
        "status",
        "failure_class",
        "version",
        "stdout",
        "stderr",
        "stdout_truncated",
        "stderr_truncated",
    }:
        raise ValueError("driver observation fields differ from the contract")
    if (
        observation["query"] != list(_DRIVER_QUERY)
        or type(observation["timeout_seconds"]) is not int
        or observation["timeout_seconds"] != 2
        or (
            observation["exception_class"] is not None
            and not isinstance(observation["exception_class"], str)
        )
    ):
        raise ValueError("driver observation query metadata is invalid")
    for stream in ("stdout", "stderr"):
        value = observation[stream]
        truncated = observation[f"{stream}_truncated"]
        if (
            not isinstance(value, str)
            or len(value) > _DRIVER_DIAGNOSTIC_LIMIT
            or type(truncated) is not bool
            or (truncated and len(value) != _DRIVER_DIAGNOSTIC_LIMIT)
        ):
            raise ValueError("driver observation diagnostics are invalid")
    expected = _driver_result(
        observation["returncode"],
        observation["exception_class"],
        observation["stdout"],
        observation["stdout_truncated"],
    )
    if (observation["status"], observation["failure_class"], observation["version"]) != expected:
        raise ValueError("driver observation result is inconsistent with the query evidence")


def _observe_driver() -> dict[str, Any]:
    returncode: int | None = None
    exception_class: str | None = None
    stdout: str | bytes | None
    stderr: str | bytes | None
    try:
        completed = subprocess.run(
            _DRIVER_QUERY,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=2,
        )
        returncode, stdout, stderr = completed.returncode, completed.stdout, completed.stderr
    except subprocess.TimeoutExpired as exc:
        exception_class = type(exc).__name__
        stdout, stderr = exc.stdout, exc.stderr
    except OSError as exc:
        exception_class = type(exc).__name__
        stdout, stderr = None, str(exc)
    observation: dict[str, Any] = {
        "query": list(_DRIVER_QUERY),
        "timeout_seconds": 2,
        "returncode": returncode,
        "exception_class": exception_class,
    }
    for stream, value in (("stdout", stdout), ("stderr", stderr)):
        # TimeoutExpired may carry bytes even with text=True, or None before any output.
        diagnostic = (
            ""
            if value is None
            else (value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value)
        )
        observation[stream] = diagnostic[:_DRIVER_DIAGNOSTIC_LIMIT]
        observation[f"{stream}_truncated"] = len(diagnostic) > _DRIVER_DIAGNOSTIC_LIMIT
    status, failure, version = _driver_result(
        returncode, exception_class, observation["stdout"], observation["stdout_truncated"]
    )
    observation.update(status=status, failure_class=failure, version=version)
    validate_driver_observation(observation)
    return observation


def _application_identity() -> str:
    root = Path(__file__).parent
    paths = (
        root / "qualification.py",
        root / "runtime.py",
        root / "stage_cache.py",
        root / "synthetic_qualification.py",
        root / "disk_projection.py",
        root / "projection_evidence.py",
    )
    return sha256_bytes(b"".join(path.read_bytes() for path in paths))


def _repeat_identities(run: Sequence[Mapping[str, Any]]) -> list[tuple[str, ...]]:
    return [
        (
            cast(str, item["model_hash"]),
            cast(str, item["prediction_hash"]),
            cast(str, item["oracle_prediction_identity"]),
            cast(str, cast(Mapping[str, Any], item["float64"])["grouped_loss_identity"]),
            cast(str, cast(Mapping[str, Any], item["float64"])["oracle_loss_identity"]),
            cast(str, cast(Mapping[str, Any], item["float64"])["grouped_gradient_identity"]),
            cast(str, cast(Mapping[str, Any], item["float64"])["oracle_gradient_identity"]),
            cast(str, cast(Mapping[str, Any], item["float32"])["grouped_loss_identity"]),
            cast(str, cast(Mapping[str, Any], item["float32"])["oracle_loss_identity"]),
            cast(str, cast(Mapping[str, Any], item["float32"])["grouped_gradient_identity"]),
            cast(str, cast(Mapping[str, Any], item["float32"])["oracle_gradient_identity"]),
        )
        for item in run
    ]


def configure_qualification_determinism(seed: int) -> dict[str, Any]:
    """Establish the qualification CUDA policy before the first CUDA context."""
    if torch.cuda.is_initialized():
        raise RuntimeError(
            "qualification determinism must be configured before CUDA initialisation"
        )
    configure_deterministic_cuda(seed)
    return _require_qualification_determinism(seed)


def _observed_deterministic_policy(seed: int) -> dict[str, Any]:
    return {
        "cublas_workspace_config": os.environ.get("CUBLAS_WORKSPACE_CONFIG"),
        "deterministic_algorithms": torch.are_deterministic_algorithms_enabled(),
        "cudnn_deterministic": torch.backends.cudnn.deterministic,
        "cudnn_benchmark": torch.backends.cudnn.benchmark,
        "cuda_matmul_allow_tf32": torch.backends.cuda.matmul.allow_tf32,
        "cudnn_allow_tf32": torch.backends.cudnn.allow_tf32,
        "float32_matmul_precision": torch.get_float32_matmul_precision(),
        "cpu_seed": torch.initial_seed(),
        "cuda_seed": torch.cuda.initial_seed(),
        "bound_seed": seed,
        "config_identity": FrozenRuntimeConfig().identity,
    }


def _require_qualification_determinism(seed: int) -> dict[str, Any]:
    observed = _observed_deterministic_policy(seed)
    expected: dict[str, Any] = dict(DETERMINISTIC_CUDA_POLICY)
    expected.update({"cpu_seed": seed, "cuda_seed": seed, "bound_seed": seed})
    mismatches = {
        key: {"expected": value, "observed": observed[key]}
        for key, value in expected.items()
        if observed[key] != value
    }
    if os.environ.get("CUBLAS_WORKSPACE_CONFIG") != CUBLAS_WORKSPACE_CONFIG:
        mismatches["cublas_workspace_config"] = {
            "expected": CUBLAS_WORKSPACE_CONFIG,
            "observed": os.environ.get("CUBLAS_WORKSPACE_CONFIG"),
        }
    if mismatches:
        raise RuntimeError(f"qualification deterministic policy drift: {mismatches}")
    return observed


def qualify_cache(
    cache_root: Path,
    identity: CacheInput,
    output_root: Path,
    *,
    seed: int = 17,
    prebuild_projection: Path | None = None,
) -> Path:
    if output_root.is_symlink():
        raise ValueError("qualification output root must not be a symlink")
    output_root = output_root.resolve()
    projection_payload: dict[str, Any] | None = None
    if prebuild_projection is None and output_root.exists():
        raise FileExistsError("qualification output root already exists")
    if prebuild_projection is not None:
        if (
            prebuild_projection.is_symlink()
            or prebuild_projection.resolve() != output_root / "disk-projection.json"
        ):
            raise ValueError("prebuild projection must belong to the qualification output root")
        projection_payload = json.loads(prebuild_projection.read_text())
        projection_seal = json.loads((output_root / "disk-projection-seal.json").read_text())
        if (
            projection_payload["gate"] != "ACCEPTED"
            or projection_payload["candidate_identity"] != _git_head()
            or projection_seal["receipt_sha256"] != sha256_bytes(prebuild_projection.read_bytes())
        ):
            raise ValueError("prebuild projection receipt is invalid")
        capacity_receipt = output_root / "operator-capacity-evidence.json"
        capacity_payload = json.loads(capacity_receipt.read_text())
        capacity_seal = json.loads(
            (output_root / "operator-capacity-evidence-seal.json").read_text()
        )
        if (
            capacity_seal["schema"] != "R4-D-OPERATOR-CAPACITY-EVIDENCE-SEAL-V2"
            or capacity_seal["evidence_sha256"] != sha256_bytes(capacity_receipt.read_bytes())
            or capacity_seal["evidence_identity"] != capacity_payload["evidence_identity"]
            or capacity_payload["evidence_identity"]
            != projection_payload["inputs"]["operator_capacity_evidence_identity"]
            or capacity_payload["candidate_identity"] != _git_head()
        ):
            raise ValueError("operator-capacity evidence seal is invalid")
    training, prediction, verified = load_stage_cache_batches(cache_root, expected=identity)
    if training.cached_arrays is None or prediction.cached_arrays is None:
        raise TypeError("qualification cache loader did not return cached arrays")
    projected_bytes = sum(cast(int, item["size"]) for item in verified.files) * 3
    if projected_bytes >= 5_000_000_000:
        raise ValueError("qualification projected footprint must remain below 5 GB")
    candidate = _git_head()
    if identity.producer_head != candidate:
        raise ValueError("synthetic cache producer head does not match candidate")
    post_cache_capacity: dict[str, Any] | None = None
    if projection_payload is not None:
        observation = authenticate_capacity_evidence(capacity_payload, output_root=output_root)
        required_available = projection_payload["required_available_bytes"]
        if observation.available_bytes < required_available:
            raise ValueError("post-cache current available bytes are below projection and reserve")
        post_cache_capacity = {
            "operator_capacity_evidence_identity": capacity_payload["evidence_identity"],
            "observed_available_bytes": observation.available_bytes,
            "required_available_bytes": required_available,
            "projection_and_reserve_satisfied": True,
            "observation_identity": sha256_bytes(canonical_json(observation._asdict())),
        }
    deterministic_policy = _require_qualification_determinism(seed)
    started_wall = time.monotonic()
    started_usage = resource.getrusage(resource.RUSAGE_SELF)
    if prebuild_projection is None:
        output_root.mkdir(parents=True, exist_ok=False)
    runs = [
        [
            compare_runtime_to_oracle(family, training, prediction, seed=seed)
            for family in FITTED_FAMILY_IDS
        ]
        for _ in range(2)
    ]
    first = _repeat_identities(runs[0])
    second = _repeat_identities(runs[1])
    if first != second:
        raise AssertionError("same-runtime deterministic semantic hashes differ")
    usage = resource.getrusage(resource.RUSAGE_SELF)
    environment = environment_identity()
    cache_files = [
        {
            key: item[key]
            for key in ("path", "size", "container_sha256", "semantic_sha256", "dtype", "shape")
        }
        for item in verified.files
    ]
    payload: dict[str, Any] = {
        "schema": "R4-D-QUALIFICATION-V2",
        "classification": "BENCHMARK_NOT_SCIENTIFIC",
        "scientific_performance": "NOT_COMPUTED",
        "outcome_access": "OUTCOME_BLIND",
        "scientific_attempt": "NOT_CREATED",
        "scientific_result": "NOT_COMPUTED",
        "terminal_state": "NOT_OPENED",
        "candidate_identity": candidate,
        "post_cache_capacity": post_cache_capacity,
        "application_identity": _application_identity(),
        "device": {
            "name": torch.cuda.get_device_name(0),
            "identity": sha256_bytes(canonical_json(environment)),
            "driver": _observe_driver(),
            "cuda": torch.version.cuda,
            "torch": torch.__version__,
            "python": sys.version,
        },
        "deterministic_policy": deterministic_policy,
        "deterministic_policy_identity": sha256_bytes(canonical_json(deterministic_policy)),
        "cache": {
            "identity": verified.cache_identity,
            "manifest_identity": verified.manifest_identity,
            "builder_semantic_closure": cache_builder_semantic_closure(),
            "files": cache_files,
        },
        "fixture_identity": sha256_bytes(
            canonical_json(
                {
                    "semantic_inputs": identity.semantic_inputs(),
                    "training_rows": list(training.row_keys),
                    "prediction_rows": list(prediction.row_keys),
                }
            )
        ),
        "sample": {
            "training_rows": len(training.row_keys),
            "prediction_rows": len(prediction.row_keys),
            "training_unique_timestamps": len(set(training.cached_arrays["row_timestamp"])),
            "prediction_unique_timestamps": len(set(prediction.cached_arrays["row_timestamp"])),
        },
        "runtime_identity": identity.numerical_runtime_identity,
        "configuration_identity": identity.config_identity,
        "families": list(FITTED_FAMILY_IDS),
        "runs": runs,
        "deterministic_hash_match": True,
        "projected_bytes": projected_bytes,
        "additional_disk_projection": (
            {
                "receipt_identity": projection_payload["receipt_identity"],
                "projected_additional_bytes": projection_payload["projected_additional_bytes"],
                "physical_host_available_bytes": projection_payload["inputs"][
                    "physical_host_available_bytes"
                ],
                "gate": projection_payload["gate"],
            }
            if projection_payload is not None
            else "NOT_APPLICABLE_TO_IN_MEMORY_TEST_CALL"
        ),
        "resources": {
            "wall_seconds": time.monotonic() - started_wall,
            "cpu_user_seconds": usage.ru_utime - started_usage.ru_utime,
            "cpu_system_seconds": usage.ru_stime - started_usage.ru_stime,
            "max_rss_kib": usage.ru_maxrss,
            "cgroup_memory_current": _read_cgroup("memory.current"),
            "cgroup_memory_peak": _read_cgroup("memory.peak"),
            "cuda_allocated_peak": torch.cuda.max_memory_allocated(),
            "cuda_reserved_peak": torch.cuda.max_memory_reserved(),
        },
    }
    payload["receipt_identity"] = sha256_bytes(canonical_json(payload))
    receipt = output_root / "qualification.json"
    create_json_once(receipt, payload)
    seal = {
        "schema": "R4-D-QUALIFICATION-SEAL-V2",
        "receipt": receipt.name,
        "receipt_sha256": sha256_bytes(receipt.read_bytes()),
        "receipt_identity": payload["receipt_identity"],
    }
    create_json_once(output_root / "seal.json", seal)
    descriptor = os.open(output_root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_root", type=Path)
    parser.add_argument("--production-shape-root", required=True, type=Path)
    args = parser.parse_args(argv)
    requested_output = args.output_root
    if requested_output.is_symlink():
        raise ValueError("qualification output root must not be a symlink")
    output_root = requested_output.resolve()
    if output_root != _QUALIFICATION_OUTPUT_ROOT:
        raise ValueError(f"qualification output root must be {_QUALIFICATION_OUTPUT_ROOT}")
    cache_release = output_root.with_name(f"{output_root.name}-cache")
    if output_root.exists() and (
        not output_root.is_dir()
        or {entry.name for entry in output_root.iterdir()} != {"validation"}
    ):
        raise FileExistsError("qualification output root must be absent or contain only validation")
    if cache_release.exists() or cache_release.is_symlink():
        raise FileExistsError("qualification cache root already exists")
    working_root = Path(__file__).resolve().parents[2]
    candidate = candidate_head(working_root)
    authenticate_candidate_validation(
        output_root / "validation" / "validation.json",
        expected_head=candidate,
        expected_working_root=working_root,
    )
    configure_qualification_determinism(_QUALIFICATION_SEED)
    from .disk_projection import DiskProjectionRejected, create_additional_disk_projection
    from .prior_qualification_evidence import create_prior_qualification_evidence
    from .projection_evidence import (
        create_operator_capacity_evidence,
        create_production_shape_evidence,
    )
    from .synthetic_qualification import build_synthetic_cache, synthetic_qualification_inputs

    output_root.mkdir(parents=True, exist_ok=True)
    capacity_evidence = create_operator_capacity_evidence(output_root, candidate_identity=candidate)
    shape_evidence = create_production_shape_evidence(
        output_root,
        source_root=args.production_shape_root,
        candidate_identity=candidate,
    )
    prior_evidence = create_prior_qualification_evidence(output_root, candidate_identity=candidate)
    inputs = synthetic_qualification_inputs()
    _, _, identity = inputs
    projection = create_additional_disk_projection(
        output_root,
        shape_evidence=shape_evidence,
        capacity_evidence=capacity_evidence,
        prior_qualification_evidence=prior_evidence,
        identity=identity,
        candidate_identity=candidate,
    )
    if json.loads(projection.read_text())["gate"] == "REJECTED":
        raise DiskProjectionRejected(projection)
    cache_root, identity = build_synthetic_cache(cache_release, inputs=inputs)
    qualify_cache(cache_root, identity, output_root, prebuild_projection=projection)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
