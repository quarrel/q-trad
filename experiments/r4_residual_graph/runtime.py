"""Closed R4-P0 residual-family runtime and release contracts.

The module is deliberately bounded to implementation and non-scientific smoke use. It never
loads outcomes, contacts providers, or writes a scientific execution root.
"""

# ruff: noqa: I001 -- CUDA boundary import must precede Torch.
from __future__ import annotations

import hashlib
import json
import os
import platform
import resource
import time
from collections.abc import Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

from .cuda_boundary import DETERMINISTIC_CUDA_POLICY
import numpy as np
import torch
from torch import Tensor, nn

from .foundation import (
    ALL_INSTRUMENTS,
    DEVELOPMENT_BLOCKS,
    EXPECTED_COUNTS,
    AuthenticatedResidualFoundation,
)
from .graph import (
    FrozenEconomicGraph,
    ShuffledEconomicGraph,
    build_fixed_economic_graph,
    shuffle_economic_graph,
)
from .tensor import (
    FittedTrainingPreprocessor,
    MaskedTensor,
    SupportRecord,
    TensorContract,
    _masked_tensor_identity,
)

PARENT_IDENTITY = "284a11cd63cca67445007ffa42321a393536cea7"
MANIFEST_IDENTITY = "462e40fa84038156b16c68bde4b68d574ab7862c680657ebd1a0035b39bf0072"
CLOSURE_IDENTITY = "e40203bc493b05afb1d5c8202cb7d16985e54d702679d7890aae0902705c9b89"
PRIMARY_STAGES: tuple[str, ...] = (
    "DEV_2",
    "DEV_3",
    "TERMINAL_FORMER_HOLDOUT",
)
PRIMARY_SEEDS: tuple[int, ...] = (17, 29, 43)
PRIMARY_SLOT_COUNT = 45
MAX_RETRY_BUDGET = 5
DEVICE_REQUIRED = "cuda"
RUNTIME_VERSION = "R4.C-FROZEN-RUNTIME-V1"
SOURCE_CLASS = "IBKR_HISTORICAL_RESEARCH"
EXPERIMENT_CLASS = "POST_HOC_HISTORICAL_EXPLORATORY"
SUPPORT_IDENTITY = "R4-C-SUPPORT-POLICY-V1"
OUTPUT_POLICY = "APPEND_ONLY_ATTEMPTS_CREATE_ONLY_ARTIFACTS"
TERMINAL_PREDICATE = "OUTCOME_BLIND_COMPLETE_DEVELOPMENT_SUPPORT"
_VALIDATED_FOUNDATION_SEAL = object()
FAMILY_IDS: tuple[str, ...] = (
    "ZERO_RETURN",
    "LOCAL_RIDGE",
    "FULLY_POOLED_LOCAL_RIDGE",
    "LOCAL_TEMPORAL_RESIDUAL",
    "POOLED_NON_GRAPH_RESIDUAL",
    "FIXED_ECONOMIC_GRAPH_RESIDUAL",
    "LEARNED_STATIC_GRAPH_RESIDUAL",
    "SHUFFLED_FIXED_GRAPH_RESIDUAL",
)
FITTED_FAMILY_IDS: tuple[str, ...] = FAMILY_IDS[3:]


@dataclass
class RuntimeTelemetry:
    """Optional counters for non-scientific runtime qualification."""

    rows: int = 0
    timestamps: int = 0
    preprocessing_seconds: float = 0.0
    materialisation_seconds: float = 0.0
    h2d_seconds: float = 0.0
    forward_seconds: float = 0.0
    backward_seconds: float = 0.0
    optimiser_seconds: float = 0.0
    materialisation_calls: int = 0
    timestamp_batch_calls: int = 0
    representation_timestamp_calls: int = 0
    shared_representation_calls: int = 0
    local_vectorised_calls: int = 0
    preprocessing_calls: int = 0
    h2d_bytes: int = 0
    h2d_calls: int = 0
    prefetch_calls: int = 0
    overlapped_prefetch_calls: int = 0
    forward_calls: int = 0
    backward_calls: int = 0
    optimizer_steps: int = 0
    elapsed_seconds: float = 0.0
    peak_cuda_allocated_bytes: int = 0
    peak_cuda_reserved_bytes: int = 0
    max_rss_kib: int = 0
    _cuda_timings: list[tuple[str, Any, Any]] = field(default_factory=list, repr=False)

    def record_h2d_transfer(self, tensors: Sequence[Tensor]) -> None:
        self.h2d_calls += 1
        self.h2d_bytes += sum(item.numel() * item.element_size() for item in tensors)

    def record_cuda_timing(self, category: str, started: Any, finished: Any) -> None:
        self._cuda_timings.append((category, started, finished))

    def finish(self, device: Any, started: float) -> None:
        torch.cuda.synchronize(device)
        for category, phase_started, phase_finished in self._cuda_timings:
            elapsed = float(phase_started.elapsed_time(phase_finished)) / 1000.0
            setattr(self, f"{category}_seconds", getattr(self, f"{category}_seconds") + elapsed)
        self._cuda_timings.clear()
        self.elapsed_seconds += time.monotonic() - started
        self.peak_cuda_allocated_bytes = max(
            self.peak_cuda_allocated_bytes, int(torch.cuda.max_memory_allocated(device))
        )
        self.peak_cuda_reserved_bytes = max(
            self.peak_cuda_reserved_bytes, int(torch.cuda.max_memory_reserved(device))
        )
        self.max_rss_kib = max(
            self.max_rss_kib, int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        )


PRIMARY_SCHEDULE: tuple[tuple[str, int, str], ...] = tuple(
    (family, seed, stage)
    for stage in PRIMARY_STAGES
    for family in FITTED_FAMILY_IDS
    for seed in PRIMARY_SEEDS
)
if len(PRIMARY_SCHEDULE) != PRIMARY_SLOT_COUNT or len(set(PRIMARY_SCHEDULE)) != PRIMARY_SLOT_COUNT:
    raise RuntimeError("R4-P0 primary schedule must contain exactly 45 unique slots")


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tensor_digest(value: Any) -> str:
    if torch is None or not isinstance(value, torch.Tensor):
        raise TypeError("residual batch values must be torch tensors")
    contiguous = value.detach().cpu().contiguous()
    digest = hashlib.sha256()
    digest.update(_canonical({"shape": tuple(contiguous.shape), "dtype": str(contiguous.dtype)}))
    digest.update(contiguous.numpy().tobytes())
    return digest.hexdigest()


def _normalise_target_nodes(target_node: Any, batch_size: int, device: Any) -> Any:
    if torch is None:
        raise RuntimeError("R4-P0 CUDA runtime requires torch")
    if isinstance(target_node, int):
        target_nodes = torch.full((batch_size,), target_node, dtype=torch.long, device=device)
    elif isinstance(target_node, torch.Tensor):
        target_nodes = target_node.to(device=device, dtype=torch.long)
    else:
        target_nodes = torch.as_tensor(list(target_node), dtype=torch.long, device=device)
    if target_nodes.ndim != 1 or target_nodes.shape[0] != batch_size:
        raise ValueError("target-node indices must contain one instrument per training row")
    if bool(((target_nodes < 0) | (target_nodes >= len(ALL_INSTRUMENTS))).any()):
        raise ValueError("target node is outside the canonical universe")
    return target_nodes


def _global_instrument_weights(target_nodes: Any) -> tuple[Any, Any]:
    """Return global counts and inverse-count row weights for balanced slot MSE."""
    if torch is None:
        raise RuntimeError("R4-P0 CUDA runtime requires torch")
    counts = torch.bincount(target_nodes, minlength=len(ALL_INSTRUMENTS))
    if bool((counts <= 0).any()):
        raise ValueError("training batch must cover every canonical instrument")
    weights = 1.0 / (counts[target_nodes].to(dtype=torch.float32) * len(ALL_INSTRUMENTS))
    return counts, weights


@dataclass(frozen=True)
class FamilySpec:
    family_id: str
    fitted: bool
    graph_mode: Literal["none", "pooled", "fixed", "learned", "shuffled"]
    target_head: str = "TARGET_NODE_RESIDUAL"
    backbone: str = "SINGLE_LAYER_LSTM"

    def __post_init__(self) -> None:
        if self.fitted != (self.family_id in FITTED_FAMILY_IDS):
            raise ValueError("family fitted flag does not match the closed register")
        if self.family_id not in FAMILY_IDS:
            raise ValueError(f"unknown R4 family: {self.family_id}")
        if self.backbone != "SINGLE_LAYER_LSTM" and self.fitted:
            raise ValueError("fitted residual families share one LSTM backbone")


FAMILY_REGISTER: tuple[FamilySpec, ...] = (
    FamilySpec("ZERO_RETURN", False, "none", target_head="CONTROL"),
    FamilySpec("LOCAL_RIDGE", False, "none", target_head="CONTROL"),
    FamilySpec("FULLY_POOLED_LOCAL_RIDGE", False, "pooled", target_head="CONTROL"),
    FamilySpec("LOCAL_TEMPORAL_RESIDUAL", True, "none"),
    FamilySpec("POOLED_NON_GRAPH_RESIDUAL", True, "pooled"),
    FamilySpec("FIXED_ECONOMIC_GRAPH_RESIDUAL", True, "fixed"),
    FamilySpec("LEARNED_STATIC_GRAPH_RESIDUAL", True, "learned"),
    FamilySpec("SHUFFLED_FIXED_GRAPH_RESIDUAL", True, "shuffled"),
)
REGISTER_IDENTITY = _sha256([asdict(spec) for spec in FAMILY_REGISTER])
FAMILY_REGISTER_SHA256 = REGISTER_IDENTITY
_CANONICAL_TENSOR_IDENTITY = TensorContract().identity
_CANONICAL_FIXED_GRAPH_IDENTITY = build_fixed_economic_graph().identity
_CANONICAL_SHUFFLED_GRAPH_IDENTITY = shuffle_economic_graph(build_fixed_economic_graph()).identity


@dataclass(frozen=True)
class TrainingConfig:
    hidden_width: int = 32
    message_width: int = 32
    epochs: int = 1
    batch_size: int = 64
    learning_rate: float = 1e-3
    weight_decay: float = 1e-4
    loss: str = "MSE"
    optimiser: str = "ADAM"
    stopping: str = "FIXED_EPOCHS_NO_EARLY_STOPPING"

    def __post_init__(self) -> None:
        if not 1 <= self.hidden_width <= 32 or not 1 <= self.message_width <= 32:
            raise ValueError("R4-P0 widths must be between 1 and 32")
        if self.epochs < 1 or self.batch_size < 1:
            raise ValueError("training epochs and batch size must be positive")
        if (
            not np.isfinite(self.learning_rate)
            or not np.isfinite(self.weight_decay)
            or self.learning_rate <= 0
            or self.weight_decay < 0
        ):
            raise ValueError("optimiser parameters must be finite and positive/non-negative")
        if self.loss != "MSE" or self.optimiser != "ADAM":
            raise ValueError("R4-P0 uses the frozen MSE/Adam training policy")


@dataclass(frozen=True)
class FrozenRuntimeConfig:
    """Canonical, hashable binding for every R4-P0 runtime identity."""

    parent_identity: str = PARENT_IDENTITY
    manifest_identity: str = MANIFEST_IDENTITY
    child_closure_identity: str = CLOSURE_IDENTITY
    source_class: str = SOURCE_CLASS
    experiment_class: str = EXPERIMENT_CLASS
    universe: tuple[str, ...] = tuple(ALL_INSTRUMENTS)
    horizon_minutes: int = 15
    decision_cadence_seconds: int = 60
    chronology: tuple[str, ...] = PRIMARY_STAGES
    tensor_identity: str = _CANONICAL_TENSOR_IDENTITY
    support_identity: str = SUPPORT_IDENTITY
    fixed_graph_identity: str = _CANONICAL_FIXED_GRAPH_IDENTITY
    shuffled_graph_identity: str = _CANONICAL_SHUFFLED_GRAPH_IDENTITY
    register_identity: str = REGISTER_IDENTITY
    seeds: tuple[int, ...] = PRIMARY_SEEDS
    training: TrainingConfig = TrainingConfig()
    retry_budget: int = 0
    device: str = DEVICE_REQUIRED
    runtime_version: str = RUNTIME_VERSION
    deterministic_cuda_policy: tuple[tuple[str, str | bool], ...] = DETERMINISTIC_CUDA_POLICY
    output_policy: str = OUTPUT_POLICY
    source_root: str = "LAB-0"
    terminal_predicate: str = TERMINAL_PREDICATE

    def __post_init__(self) -> None:
        contract = TensorContract()
        fixed = build_fixed_economic_graph()
        shuffled = shuffle_economic_graph(fixed)
        if self.parent_identity != PARENT_IDENTITY or self.manifest_identity != MANIFEST_IDENTITY:
            raise ValueError("R4-P0 parent and authenticated manifest identities are frozen")
        if self.child_closure_identity != CLOSURE_IDENTITY:
            raise ValueError("R4-P0 child closure identity is not authenticated")
        if self.source_class != SOURCE_CLASS or self.experiment_class != EXPERIMENT_CLASS:
            raise ValueError("R4-P0 source and experiment classes are frozen")
        if self.horizon_minutes != 15 or self.decision_cadence_seconds != 60:
            raise ValueError("R4-P0 horizon and cadence are frozen")
        if self.support_identity != SUPPORT_IDENTITY:
            raise ValueError("R4-P0 support policy identity is frozen")
        if self.runtime_version != RUNTIME_VERSION or self.output_policy != OUTPUT_POLICY:
            raise ValueError("R4-P0 runtime and output policies are frozen")
        if self.deterministic_cuda_policy != DETERMINISTIC_CUDA_POLICY:
            raise ValueError("R4-P0 deterministic CUDA policy is frozen")
        if self.source_root != "LAB-0" or self.terminal_predicate != TERMINAL_PREDICATE:
            raise ValueError("R4-P0 source root and terminal predicate are frozen")
        if self.tensor_identity != contract.identity:
            raise ValueError("tensor identity does not match canonical contract")
        if self.fixed_graph_identity != fixed.identity:
            raise ValueError("fixed graph identity does not match frozen graph")
        if self.shuffled_graph_identity != shuffled.identity:
            raise ValueError("shuffled graph identity does not match frozen graph")
        if self.register_identity != REGISTER_IDENTITY:
            raise ValueError("family register identity does not match closed register")
        if not isinstance(self.universe, tuple) or self.universe != tuple(ALL_INSTRUMENTS):
            raise ValueError("R4-P0 requires the canonical immutable instrument universe")
        if not isinstance(self.chronology, tuple) or self.chronology != PRIMARY_STAGES:
            raise ValueError("R4-P0 chronology is DEV_2, DEV_3, TERMINAL_FORMER_HOLDOUT")
        if not isinstance(self.seeds, tuple) or self.seeds != PRIMARY_SEEDS:
            raise ValueError("R4-P0 seeds are frozen and cannot be selected or averaged")
        if self.training != TrainingConfig():
            raise ValueError("R4-P0 training configuration is frozen")
        if not 0 <= self.retry_budget <= MAX_RETRY_BUDGET:
            raise ValueError("retry budget must be in the frozen 0-5 range")
        if self.device != DEVICE_REQUIRED:
            raise ValueError("R4-P0 device must be exactly cuda")

    @property
    def resolved_tensor_identity(self) -> str:
        return self.tensor_identity or TensorContract().identity

    @property
    def resolved_fixed_graph_identity(self) -> str:
        return self.fixed_graph_identity or build_fixed_economic_graph().identity

    @property
    def resolved_shuffled_graph_identity(self) -> str:
        return (
            self.shuffled_graph_identity
            or shuffle_economic_graph(build_fixed_economic_graph()).identity
        )

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["tensor_identity"] = self.resolved_tensor_identity
        result["fixed_graph_identity"] = self.resolved_fixed_graph_identity
        result["shuffled_graph_identity"] = self.resolved_shuffled_graph_identity
        result["tensor_contract"] = asdict(TensorContract())
        result["graph_policy"] = {
            "fixed_version": "R4.B-FROZEN-ECONOMIC-V1",
            "fixed_identity": result["fixed_graph_identity"],
            "shuffled_identity": result["shuffled_graph_identity"],
            "shuffle": "FIXED_POINT_FREE_SHIFT_2",
        }
        result["training"] = asdict(self.training)
        result["family_register"] = [asdict(item) for item in FAMILY_REGISTER]
        result["primary_schedule"] = [list(item) for item in PRIMARY_SCHEDULE]
        return result

    @property
    def identity(self) -> str:
        return _sha256(self.to_dict())


R4P0Config = FrozenRuntimeConfig


def frozen_runtime_config(**kwargs: Any) -> FrozenRuntimeConfig:
    return FrozenRuntimeConfig(**kwargs)


def architecture_signature(
    spec: FamilySpec,
    *,
    contract: TensorContract | None = None,
    training: TrainingConfig | None = None,
) -> dict[str, Any]:
    active_contract = contract or TensorContract()
    policy = training or TrainingConfig()
    return {
        "family_id": spec.family_id,
        "backbone": spec.backbone,
        "lstm": {
            "input_size": len(active_contract.feature_names),
            "hidden_size": policy.hidden_width,
            "num_layers": 1,
        },
        "message_passing_layers": 0 if spec.graph_mode in {"none", "pooled"} else 1,
        "message_width": policy.message_width if spec.graph_mode != "none" else 0,
        "target_head": spec.target_head,
        "nodes": len(active_contract.node_order),
    }


if nn is not None:

    class ResidualGraphModel(nn.Module):
        """Shared one-layer LSTM residual model with an optional static graph operator."""

        def __init__(
            self,
            family: str | FamilySpec,
            *,
            contract: TensorContract | None = None,
            training: TrainingConfig | None = None,
            graph: FrozenEconomicGraph | ShuffledEconomicGraph | None = None,
        ) -> None:
            super().__init__()
            spec = family if isinstance(family, FamilySpec) else family_spec(family)
            if not spec.fitted:
                raise ValueError("controls are not fitted residual models")
            if spec != family_spec(spec.family_id):
                raise ValueError("family specification must match the frozen register")
            canonical_contract = TensorContract()
            if contract is not None and contract.identity != canonical_contract.identity:
                raise ValueError("model requires the authenticated canonical tensor contract")
            canonical_training = TrainingConfig()
            if training is not None and training != canonical_training:
                raise ValueError("model requires the frozen training configuration")
            self.spec = spec
            self.contract = canonical_contract
            self.training_config = canonical_training
            self.lstm = nn.LSTM(
                input_size=len(self.contract.feature_names),
                hidden_size=self.training_config.hidden_width,
                num_layers=1,
                batch_first=True,
            )
            message_width = self.training_config.message_width if spec.graph_mode != "none" else 0
            self.message = (
                nn.Linear(self.training_config.hidden_width, message_width)
                if message_width
                else nn.Identity()
            )
            self.head = nn.Linear(self.training_config.hidden_width + message_width, 1)
            if spec.graph_mode in {"fixed", "shuffled"}:
                if graph is None:
                    raise ValueError("fixed and shuffled families require their frozen graph")
                expected_graph = (
                    build_fixed_economic_graph()
                    if spec.graph_mode == "fixed"
                    else shuffle_economic_graph(build_fixed_economic_graph())
                )
                matrices_match = np.array_equal(
                    graph.adjacency, expected_graph.adjacency
                ) and np.array_equal(
                    graph.normalized_adjacency, expected_graph.normalized_adjacency
                )
                if spec.graph_mode == "fixed":
                    metadata_match = (
                        isinstance(graph, FrozenEconomicGraph)
                        and isinstance(expected_graph, FrozenEconomicGraph)
                        and graph.node_order == expected_graph.node_order
                        and graph.identity == expected_graph.identity
                        and graph.edges == expected_graph.edges
                        and graph.normalization == expected_graph.normalization
                        and graph.diagonal_policy == expected_graph.diagonal_policy
                        and graph.semantics_version == expected_graph.semantics_version
                    )
                else:
                    metadata_match = (
                        isinstance(graph, ShuffledEconomicGraph)
                        and isinstance(expected_graph, ShuffledEconomicGraph)
                        and graph.node_order == expected_graph.node_order
                        and graph.identity == expected_graph.identity
                        and graph.source_identity == expected_graph.source_identity
                        and np.array_equal(graph.permutation, expected_graph.permutation)
                        and graph.off_diagonal_difference_fraction
                        == expected_graph.off_diagonal_difference_fraction
                        and graph.neighbourhood_difference_fraction
                        == expected_graph.neighbourhood_difference_fraction
                    )
                if not (matrices_match and metadata_match):
                    raise ValueError("model requires the authenticated canonical graph")
                adjacency = np.asarray(graph.normalized_adjacency, dtype=np.float32)
                self.register_buffer("adjacency", torch.as_tensor(adjacency))
            elif spec.graph_mode == "learned":
                nodes = len(self.contract.node_order)
                initial = np.full((nodes, nodes), 1.0 / nodes, dtype=np.float32)
                self.learned_adjacency_logits = nn.Parameter(torch.as_tensor(np.log(initial)))
            else:
                self.register_buffer("adjacency", torch.empty((0, 0)))

        @property
        def graph_mode(self) -> str:
            return self.spec.graph_mode

        def adjacency_matrix(self) -> Tensor | None:
            """Return the static graph, including the learned directed matrix."""
            if self.spec.graph_mode == "learned":
                return torch.softmax(self.learned_adjacency_logits, dim=-1)
            if self.spec.graph_mode in {"fixed", "shuffled"}:
                return cast(Tensor, self.adjacency)
            return None

        def forward_grouped(
            self,
            values: Tensor,
            *,
            target_node: Tensor,
            target_batch: Tensor,
            value_mask: Tensor,
            availability_mask: Tensor,
            node_mask: Tensor,
        ) -> Tensor:
            """Encode each timestamp once and score its canonically ordered targets."""
            if values.ndim != 4 or target_node.ndim != 1 or target_batch.shape != target_node.shape:
                raise ValueError("grouped model inputs are not canonical")
            batch, _, nodes, features = values.shape
            if nodes != len(self.contract.node_order) or features != len(
                self.contract.feature_names
            ):
                raise ValueError("grouped model input does not match canonical tensor contract")
            if value_mask.shape != values.shape or availability_mask.shape != values.shape:
                raise ValueError("grouped model value masks do not match input")
            if node_mask.shape != values.shape[:3]:
                raise ValueError("grouped model node mask does not match input")
            if bool((target_batch < 0).any()) or bool((target_batch >= batch).any()):
                raise ValueError("grouped target batch index is outside timestamp batch")
            target_node = _normalise_target_nodes(target_node, len(target_node), values.device)
            observed = value_mask & availability_mask & node_mask.unsqueeze(-1)
            masked_values = torch.where(observed, values, torch.zeros_like(values))
            gated_values = masked_values * observed.to(dtype=values.dtype).mean(dim=-1).unsqueeze(
                -1
            )
            if self.spec.graph_mode == "none":
                sequence = gated_values[target_batch, :, target_node, :]
                step_mask = node_mask[target_batch, :, target_node] & observed[
                    target_batch, :, target_node, :
                ].any(dim=-1)
                hidden = self._masked_lstm(sequence, step_mask)
                return self.head(hidden).squeeze(-1)
            sequence = gated_values.permute(0, 2, 1, 3).reshape(
                batch * nodes, values.shape[1], features
            )
            step_mask = (
                node_mask.permute(0, 2, 1) & observed.any(dim=-1).permute(0, 2, 1)
            ).reshape(batch * nodes, values.shape[1])
            node_hidden = self._masked_lstm(sequence, step_mask).reshape(batch, nodes, -1)
            node_present = node_mask.any(dim=1)
            node_hidden = node_hidden * node_present.unsqueeze(-1).to(dtype=node_hidden.dtype)
            if self.spec.graph_mode == "pooled":
                weights = node_present.to(dtype=node_hidden.dtype)
                context = (node_hidden * weights.unsqueeze(-1)).sum(dim=1, keepdim=True)
                context = context / weights.sum(dim=1, keepdim=True).clamp_min(1.0).unsqueeze(-1)
                context = context.expand(-1, nodes, -1)
            elif self.spec.graph_mode in {"fixed", "shuffled"}:
                context = torch.einsum("ij,bjh->bih", self.adjacency, node_hidden)
            else:
                adjacency = torch.softmax(self.learned_adjacency_logits, dim=-1)
                context = torch.einsum("ij,bjh->bih", adjacency, node_hidden)
            message = torch.tanh(self.message(context))
            selected_hidden = node_hidden[target_batch, target_node]
            selected_message = message[target_batch, target_node]
            return self.head(torch.cat((selected_hidden, selected_message), dim=-1)).squeeze(-1)

        def architecture(self) -> dict[str, Any]:
            return architecture_signature(
                self.spec, contract=self.contract, training=self.training_config
            )

        def _masked_lstm(self, sequence: Tensor, step_mask: Tensor) -> Tensor:
            batch, steps, _ = sequence.shape
            if bool(step_mask.all()):
                _, (hidden, _) = self.lstm(sequence)
                return hidden[-1]
            hidden = torch.zeros(
                (1, batch, self.training_config.hidden_width),
                device=sequence.device,
                dtype=sequence.dtype,
            )
            cell = torch.zeros_like(hidden)
            for index in range(steps):
                _, (hidden, cell) = self.lstm(sequence[:, index : index + 1, :], (hidden, cell))
                active = step_mask[:, index].view(1, batch, 1).to(dtype=sequence.dtype)
                hidden = hidden * active
                cell = cell * active
            return hidden[-1]

        def forward(
            self,
            values: Tensor,
            *,
            target_node: Any = None,
            value_mask: Tensor | None = None,
            availability_mask: Tensor | None = None,
            node_mask: Tensor | None = None,
        ) -> Tensor:
            if values.ndim != 4:
                raise ValueError("model input must have shape (batch,time,node,feature)")
            batch, _, nodes, features = values.shape
            if nodes != len(self.contract.node_order) or features != len(
                self.contract.feature_names
            ):
                raise ValueError("model input does not match canonical tensor contract")
            if value_mask is None:
                value_mask = torch.ones_like(values, dtype=torch.bool)
            if availability_mask is None:
                availability_mask = torch.ones_like(values, dtype=torch.bool)
            if node_mask is None:
                node_mask = torch.ones(values.shape[:3], dtype=torch.bool, device=values.device)
            if (
                value_mask.shape != values.shape
                or availability_mask.shape != values.shape
                or node_mask.shape != values.shape[:3]
            ):
                raise ValueError("model masks do not match canonical tensor shape")
            observed = value_mask & availability_mask & node_mask.unsqueeze(-1)
            masked_values = torch.where(observed, values, torch.zeros_like(values))
            feature_gate = observed.to(dtype=values.dtype).mean(dim=-1)
            gated_values = masked_values * feature_gate.unsqueeze(-1)
            if target_node is None:
                raise ValueError("fitted residual forward requires target_node")
            target_nodes = _normalise_target_nodes(target_node, batch, values.device)
            if self.spec.graph_mode == "none" and target_nodes is not None:
                row_indices = torch.arange(batch, device=values.device)
                sequence = gated_values[row_indices, :, target_nodes, :]
                step_mask = node_mask[row_indices, :, target_nodes] & (
                    observed[row_indices, :, target_nodes, :].any(dim=-1)
                )
                node_hidden = self._masked_lstm(sequence, step_mask)
                return self.head(node_hidden).squeeze(-1)
            sequence = gated_values.permute(0, 2, 1, 3).reshape(
                batch * nodes, values.shape[1], features
            )
            step_mask = (
                node_mask.permute(0, 2, 1) & observed.any(dim=-1).permute(0, 2, 1)
            ).reshape(batch * nodes, values.shape[1])
            node_hidden = self._masked_lstm(sequence, step_mask).reshape(batch, nodes, -1)
            node_present = node_mask.any(dim=1)
            node_hidden = node_hidden * node_present.unsqueeze(-1).to(dtype=node_hidden.dtype)
            if self.spec.graph_mode == "none":
                context = torch.zeros_like(node_hidden)
            elif self.spec.graph_mode == "pooled":
                weights = node_present.to(dtype=node_hidden.dtype)
                context = (node_hidden * weights.unsqueeze(-1)).sum(dim=1, keepdim=True)
                context = context / weights.sum(dim=1, keepdim=True).clamp_min(1.0).unsqueeze(-1)
                context = context.expand(-1, nodes, -1)
            elif self.spec.graph_mode in {"fixed", "shuffled"}:
                context = torch.einsum("ij,bjh->bih", self.adjacency, node_hidden)
            else:
                adjacency = torch.softmax(self.learned_adjacency_logits, dim=-1)
                context = torch.einsum("ij,bjh->bih", adjacency, node_hidden)
            if self.spec.graph_mode == "none":
                message = torch.empty((batch, nodes, 0), device=values.device)
            else:
                message = torch.tanh(self.message(context))
            output = self.head(torch.cat((node_hidden, message), dim=-1)).squeeze(-1)
            return output.gather(1, target_nodes.view(-1, 1)).squeeze(1)

else:

    class ResidualGraphModel:  # type: ignore[no-redef]
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("R4-P0 CUDA runtime requires the torch dependency")


def family_spec(family_id: str) -> FamilySpec:
    for spec in FAMILY_REGISTER:
        if spec.family_id == family_id:
            return spec
    raise ValueError(f"unknown R4 family: {family_id}")


def build_family_model(
    family_id: str,
    *,
    contract: TensorContract | None = None,
    training: TrainingConfig | None = None,
    fixed_graph: FrozenEconomicGraph | None = None,
    shuffled_graph: ShuffledEconomicGraph | None = None,
) -> ResidualGraphModel:
    spec = family_spec(family_id)
    graph: FrozenEconomicGraph | ShuffledEconomicGraph | None = None
    if spec.graph_mode == "fixed":
        expected = build_fixed_economic_graph()
        graph = fixed_graph or expected
        if graph.identity != expected.identity or graph.node_order != expected.node_order:
            raise ValueError("fixed family requires the authenticated canonical graph")
    elif spec.graph_mode == "shuffled":
        expected_fixed = build_fixed_economic_graph()
        expected = shuffle_economic_graph(expected_fixed)
        graph = shuffled_graph or expected
        if (
            graph.identity != expected.identity
            or graph.source_identity != expected.source_identity
            or graph.node_order != expected.node_order
        ):
            raise ValueError("shuffled family requires the authenticated canonical permutation")
    return ResidualGraphModel(spec, contract=contract, training=training, graph=graph)


def model_parameter_count(model: ResidualGraphModel) -> int:
    if torch is None:
        raise RuntimeError("R4-P0 CUDA runtime requires torch")
    return sum(int(parameter.numel()) for parameter in model.parameters())


def require_cuda(device: str = DEVICE_REQUIRED) -> Any:
    if torch is None:
        raise RuntimeError("R4-P0 CUDA runtime requires torch; CPU fallback is prohibited")
    if device != DEVICE_REQUIRED:
        raise ValueError("R4-P0 device must be exactly cuda")
    if not torch.cuda.is_available():
        raise RuntimeError("R4-P0 CUDA is unavailable; refusing CPU fallback")
    resolved = torch.device("cuda")
    if torch.cuda.device_count() < 1:
        raise RuntimeError("R4-P0 CUDA device count is zero")
    return resolved


def environment_identity() -> dict[str, Any]:
    result: dict[str, Any] = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "torch": None if torch is None else str(torch.__version__),
        "cuda_available": False if torch is None else bool(torch.cuda.is_available()),
        "device": DEVICE_REQUIRED,
    }
    if torch is not None and torch.cuda.is_available():
        result["cuda_runtime"] = str(torch.version.cuda)
        result["gpu_name"] = torch.cuda.get_device_name(0)
        result["gpu_capability"] = tuple(torch.cuda.get_device_capability(0))
    return result


def tensor_to_cuda(tensor: MaskedTensor, preprocessor: FittedTrainingPreprocessor) -> Tensor:
    """Convert one causally preprocessed sequence to the required CUDA tensor."""
    if not isinstance(preprocessor, FittedTrainingPreprocessor):
        raise TypeError("primary tensor conversion requires an authenticated fitted preprocessor")
    device = require_cuda()
    transformed = preprocessor.transform(tensor)
    if np.isinf(transformed.values).any():
        raise ValueError("tensor values contain unexpected infinity")
    observed_nan = np.isnan(transformed.values) & transformed.value_mask
    if observed_nan.any():
        raise ValueError("observed tensor values cannot be NaN")
    values = np.nan_to_num(transformed.values, nan=0.0, posinf=None, neginf=None).astype(
        np.float32, copy=False
    )
    result = torch.as_tensor(values, dtype=torch.float32, device=device).unsqueeze(0)
    expected = (transformed.values.shape[0], len(ALL_INSTRUMENTS), transformed.values.shape[2])
    if tuple(result.shape[1:]) != expected:
        raise ValueError("unexpected CUDA tensor shape")
    return result


RESIDUAL_FOUNDATION_IDENTITY = _sha256(
    {
        "manifest_identity": MANIFEST_IDENTITY,
        "child_closure_identity": CLOSURE_IDENTITY,
        "local_configuration_id": (
            "64124c5fdb3d66b01338688b3f8283ac663461fa87e3cb815b5a002b34bf6180"
        ),
        "evidence": "CAUSAL_OOF_RESIDUALS",
    }
)
CHRONOLOGY_IDENTITY = _sha256(list(PRIMARY_STAGES))


_BATCH_SEAL = object()
_PREDICTION_SEAL = object()
_PRIMARY_BATCH_CAPABILITY = object()
_SMOKE_BATCH_CAPABILITY = object()
TIMESTAMP_MATERIALISATION_POLICY = "R4.D.TIMESTAMP_BATCH_V1"
TIMESTAMP_BATCH_SIZE = 64
# Retained for the existing synthetic qualification consumer outside this packet's mutation scope.
MATERIALISATION_POLICY = TIMESTAMP_MATERIALISATION_POLICY
_STREAMING_BATCH_CAPABILITY = object()
_STREAMING_BATCH_SEAL = object()


def _stack_preprocessed(
    tensors: Sequence[MaskedTensor], preprocessor: FittedTrainingPreprocessor
) -> tuple[Any, Any, Any, Any]:
    if not tensors:
        raise ValueError("authenticated tensor sequence cannot be empty")
    transformed = [preprocessor.transform(tensor) for tensor in tensors]
    values = np.stack([tensor.values for tensor in transformed]).astype(np.float32, copy=False)
    value_mask = np.stack([tensor.value_mask for tensor in transformed])
    availability_mask = np.stack([tensor.availability_mask for tensor in transformed])
    node_mask = np.stack([tensor.node_mask for tensor in transformed])
    if np.isinf(values).any() or (np.isnan(values) & value_mask).any():
        raise ValueError("preprocessed tensor values must be finite where observed")
    return (
        torch.as_tensor(
            np.nan_to_num(values, nan=0.0, posinf=None, neginf=None),
            dtype=torch.float32,
            device=require_cuda(),
        ),
        torch.as_tensor(value_mask, dtype=torch.bool, device=require_cuda()),
        torch.as_tensor(availability_mask, dtype=torch.bool, device=require_cuda()),
        torch.as_tensor(node_mask, dtype=torch.bool, device=require_cuda()),
    )


def _stream_provider_tensor(
    tensors: Mapping[str, MaskedTensor] | Sequence[tuple[str, MaskedTensor]],
    timestamp: str,
    cache: dict[str, MaskedTensor],
) -> MaskedTensor:
    cached = cache.get(timestamp)
    if cached is not None:
        return cached
    if isinstance(tensors, Mapping):
        tensor = cast(Mapping[str, MaskedTensor], tensors)[timestamp]
    else:
        tensor = dict(cast(Sequence[tuple[str, MaskedTensor]], tensors))[timestamp]
    if not isinstance(tensor, MaskedTensor):
        raise TypeError("authenticated tensor provider returned an invalid tensor")
    cache[timestamp] = tensor
    return tensor


def _stream_tensor_identity_digest(support: SupportRecord) -> str:
    return _sha256(
        {
            "policy": TIMESTAMP_MATERIALISATION_POLICY,
            "keys": support.keys,
            "tensor_identities": support.tensor_identities,
        }
    )


def _validate_stream_provider_lineage(
    tensors: Mapping[str, MaskedTensor] | Sequence[tuple[str, MaskedTensor]],
    support: SupportRecord,
    expected_identities: tuple[tuple[str, str], ...] | None = None,
) -> tuple[tuple[str, str], ...]:
    identities = tuple(support.tensor_identities)
    if tuple(key for key, _ in identities) != support.keys:
        raise ValueError("stream provider identities are not aligned with support chronology")
    from .tensor_store import RawTensorStore

    if isinstance(tensors, RawTensorStore):
        sealed_identities = tensors._support_tensor_identities(support.keys)
        if sealed_identities != identities:
            raise ValueError("sealed stream provider identities do not match support lineage")
    else:
        sealed_table = getattr(tensors, "tensor_identities", None)
        if sealed_table is not None:
            sealed_identities = tuple((key, sealed_table[key]) for key in support.keys)
            if sealed_identities != identities:
                raise ValueError("sealed stream provider identities do not match support lineage")
        else:
            identity_map = dict(identities)
            cache: dict[str, MaskedTensor] = {}
            for timestamp in support.keys:
                tensor = _stream_provider_tensor(tensors, timestamp, cache)
                if identity_map[timestamp] != _masked_tensor_identity(tensor):
                    raise ValueError(
                        "stream provider tensor content does not match support lineage"
                    )
    if expected_identities is not None and identities != expected_identities:
        raise ValueError("stream provider identities changed after authentication")
    return identities


@dataclass(frozen=True)
class _StreamingResidualTrainingBatch:
    tensors: Mapping[str, MaskedTensor] | Sequence[tuple[str, MaskedTensor]]
    preprocessor: FittedTrainingPreprocessor | None
    row_keys: Sequence[str]
    residuals: Sequence[float]
    target_nodes: Sequence[int]
    training_blocks: Sequence[str] | Sequence[int]
    input_identity: str
    support_identity: str
    chronology_identity: str
    preprocessor_identity: str
    content_identity: str
    _capability: object
    cached_arrays: Mapping[str, np.ndarray] | None = None
    batch_size: int = TIMESTAMP_BATCH_SIZE
    _sealed: object = _STREAMING_BATCH_SEAL
    provider_tensor_identities: tuple[tuple[str, str], ...] = ()
    training_block_order: tuple[str, ...] = ()
    _support: SupportRecord | None = None
    _foundation: AuthenticatedResidualFoundation | None = None

    def __post_init__(self) -> None:
        if self._capability is not _STREAMING_BATCH_CAPABILITY:
            raise TypeError("streaming residual batch capability is invalid")

    @property
    def mode(self) -> str:
        return "PRIMARY"

    def _validate(self) -> None:
        if self._capability is not _STREAMING_BATCH_CAPABILITY:
            raise TypeError("streaming residual batch capability is invalid")
        if self._sealed is not _STREAMING_BATCH_SEAL:
            raise TypeError("streaming residual batch is not sealed")
        if len(self.row_keys) == 0 or len(self.row_keys) != len(self.residuals):
            raise ValueError("streaming residual batch rows are invalid")
        if len(self.target_nodes) != len(self.row_keys):
            raise ValueError("streaming residual batch targets are invalid")
        if len(self.training_blocks) != len(self.row_keys):
            raise ValueError("streaming residual batch blocks are invalid")
        if self.batch_size != TIMESTAMP_BATCH_SIZE:
            raise ValueError("streaming residual batch size is frozen")
        if self.cached_arrays is None:
            expected_identity = _sha256(
                {
                    "policy": TIMESTAMP_MATERIALISATION_POLICY,
                    "batch_size": self.batch_size,
                    "row_keys": tuple(self.row_keys),
                    "residuals": tuple(self.residuals),
                    "target_nodes": tuple(self.target_nodes),
                    "training_blocks": tuple(self.training_blocks),
                    "input_identity": self.input_identity,
                    "support": self.support_identity,
                    "tensor_identities": self.provider_tensor_identities,
                    "chronology": self.chronology_identity,
                    "preprocessor": self.preprocessor_identity,
                }
            )
            if self.content_identity != expected_identity:
                raise ValueError("streaming residual batch content digest mismatch")


@dataclass(frozen=True)
class _StreamingPredictionBatch:
    tensors: Mapping[str, MaskedTensor] | Sequence[tuple[str, MaskedTensor]]
    preprocessor: FittedTrainingPreprocessor | None
    row_keys: Sequence[str]
    target_nodes: Sequence[int]
    target_mask: Sequence[bool]
    input_identity: str
    support_identity: str
    chronology_identity: str
    preprocessor_identity: str
    content_identity: str
    _capability: object
    cached_arrays: Mapping[str, np.ndarray] | None = None
    batch_size: int = TIMESTAMP_BATCH_SIZE
    _sealed: object = _STREAMING_BATCH_SEAL
    provider_tensor_identities: tuple[tuple[str, str], ...] = ()
    _support: SupportRecord | None = None

    def __post_init__(self) -> None:
        if self._capability is not _STREAMING_BATCH_CAPABILITY:
            raise TypeError("streaming prediction batch capability is invalid")

    @property
    def mode(self) -> str:
        return "PRIMARY"

    def _validate(self) -> None:
        if self._capability is not _STREAMING_BATCH_CAPABILITY:
            raise TypeError("streaming prediction batch capability is invalid")
        if self._sealed is not _STREAMING_BATCH_SEAL:
            raise TypeError("streaming prediction batch is not sealed")
        if len(self.row_keys) == 0 or len(self.row_keys) != len(self.target_nodes):
            raise ValueError("streaming prediction batch rows are invalid")
        if len(self.target_mask) != len(self.row_keys) or not all(self.target_mask):
            raise ValueError("streaming prediction target mask must be complete")
        if self.batch_size != TIMESTAMP_BATCH_SIZE:
            raise ValueError("streaming prediction batch size is frozen")
        if self.cached_arrays is None:
            expected_identity = _sha256(
                {
                    "policy": TIMESTAMP_MATERIALISATION_POLICY,
                    "batch_size": self.batch_size,
                    "row_keys": tuple(self.row_keys),
                    "target_nodes": tuple(self.target_nodes),
                    "input_identity": self.input_identity,
                    "support": self.support_identity,
                    "tensor_identities": self.provider_tensor_identities,
                    "chronology": self.chronology_identity,
                    "preprocessor": self.preprocessor_identity,
                }
            )
            if self.content_identity != expected_identity:
                raise ValueError("streaming prediction batch content digest mismatch")


def _cached_row_keys(
    arrays: Mapping[str, np.ndarray], metadata: Mapping[str, object]
) -> Sequence[str]:
    del metadata
    return cast(Sequence[str], arrays["row_key"])


def _load_grouped_stage_cache_batches(
    *,
    training_arrays: Mapping[str, np.ndarray],
    prediction_arrays: Mapping[str, np.ndarray],
    training_metadata: Mapping[str, object],
    prediction_metadata: Mapping[str, object],
) -> tuple[_StreamingResidualTrainingBatch, _StreamingPredictionBatch]:
    """Seal verified timestamp-unique mmap arrays for grouped runtime use."""
    common = {
        "values",
        "value_mask",
        "availability_mask",
        "node_mask",
        "row_key",
        "row_timestamp",
        "target_nodes",
        "target_mask",
    }
    if (
        set(training_arrays) != common | {"residuals", "row_weights", "training_block"}
        or set(prediction_arrays) != common
    ):
        raise ValueError("grouped stage cache arrays do not match the sealed contract")

    def fields(arrays: Mapping[str, np.ndarray], metadata: Mapping[str, object]) -> dict[str, Any]:
        return {
            "tensors": (),
            "preprocessor": None,
            "row_keys": _cached_row_keys(arrays, metadata),
            "input_identity": cast(str, metadata["input_identity"]),
            "support_identity": cast(str, metadata["support_identity"]),
            "chronology_identity": cast(str, metadata["chronology_identity"]),
            "preprocessor_identity": cast(str, metadata["preprocessor_identity"]),
            "content_identity": cast(str, metadata["content_identity"]),
            "_capability": _STREAMING_BATCH_CAPABILITY,
            "batch_size": cast(int, metadata["batch_size"]),
            "provider_tensor_identities": tuple(
                (str(x[0]), str(x[1]))
                for x in cast(Sequence[Sequence[object]], metadata["provider_tensor_identities"])
            ),
        }

    block_order = tuple(cast(Sequence[str], training_metadata["block_order"]))
    training = _StreamingResidualTrainingBatch(
        **fields(training_arrays, training_metadata),
        residuals=cast(Sequence[float], training_arrays["residuals"]),
        target_nodes=cast(Sequence[int], training_arrays["target_nodes"]),
        training_blocks=cast(Sequence[int], training_arrays["training_block"]),
        training_block_order=block_order,
        cached_arrays=training_arrays,
    )
    prediction = _StreamingPredictionBatch(
        **fields(prediction_arrays, prediction_metadata),
        target_nodes=cast(Sequence[int], prediction_arrays["target_nodes"]),
        target_mask=cast(Sequence[bool], prediction_arrays["target_mask"]),
        cached_arrays=prediction_arrays,
    )
    training._validate()
    prediction._validate()
    return training, prediction


class _PinnedCudaDoubleBuffer:
    """Two reusable pinned-host slots for bounded asynchronous cache transfer."""

    def __init__(self) -> None:
        self._slots: tuple[dict[str, Tensor], dict[str, Tensor]] = ({}, {})
        self._ready: list[Any | None] = [None, None]
        self._next_slot = 0
        self._device = require_cuda()
        self._transfer_stream = torch.cuda.Stream(device=self._device)

    def transfer(
        self,
        arrays: Mapping[str, np.ndarray],
        indices: Sequence[int],
        telemetry: RuntimeTelemetry | None,
        *,
        row_indices: Sequence[int] = (),
        target_batch_positions: Sequence[int] = (),
    ) -> tuple[tuple[Any, ...], Any]:
        selected = np.asarray(indices, dtype=np.int64)
        selected_rows = np.asarray(row_indices, dtype=np.int64)
        slot_index = self._next_slot
        slot = self._slots[slot_index]
        self._next_slot = 1 - slot_index
        prior_ready = self._ready[slot_index]
        if prior_ready is not None:
            prior_ready.synchronize()
        device = self._device
        outputs: list[Tensor] = []
        started = torch.cuda.Event(enable_timing=True) if telemetry is not None else None
        finished = torch.cuda.Event(enable_timing=telemetry is not None)
        sources: list[tuple[str, Tensor, Any]] = [
            (name, torch.from_numpy(np.asarray(arrays[name][selected])), dtype)
            for name, dtype in (
                ("values", torch.float32),
                ("value_mask", torch.bool),
                ("availability_mask", torch.bool),
                ("node_mask", torch.bool),
            )
        ]
        if len(row_indices) > 0:
            if "residuals" in arrays:
                sources.append(
                    (
                        "residuals",
                        torch.from_numpy(np.asarray(arrays["residuals"][selected_rows])),
                        torch.float32,
                    )
                )
            sources.extend(
                (
                    (
                        "target_nodes",
                        torch.from_numpy(np.asarray(arrays["target_nodes"][selected_rows])),
                        torch.long,
                    ),
                    (
                        "target_batch",
                        torch.as_tensor(target_batch_positions, dtype=torch.long),
                        torch.long,
                    ),
                )
            )
        with torch.cuda.stream(self._transfer_stream):
            if started is not None:
                started.record()
            for name, source, dtype in sources:
                host = slot.get(name)
                if host is None or host.shape != source.shape or host.dtype != dtype:
                    host = torch.empty(source.shape, dtype=dtype, pin_memory=True)
                    slot[name] = host
                host.copy_(source)
                outputs.append(host.to(device=device, non_blocking=True))
            finished.record()
        self._ready[slot_index] = finished
        if telemetry is not None:
            telemetry.record_h2d_transfer(outputs)
            assert started is not None
            telemetry.record_cuda_timing("h2d", started, finished)
        return tuple(outputs), finished

    @staticmethod
    def wait_ready(completion: Any) -> None:
        torch.cuda.current_stream(require_cuda()).wait_event(completion)


def _cached_transfer_spec(
    arrays: Mapping[str, np.ndarray], selected: Sequence[int]
) -> tuple[list[int], list[int]]:
    indices = [int(arrays["row_timestamp"][index]) for index in selected]
    unique_indices: list[int] = []
    positions: dict[int, int] = {}
    target_batch_positions: list[int] = []
    for timestamp_index in indices:
        if timestamp_index not in positions:
            positions[timestamp_index] = len(unique_indices)
            unique_indices.append(timestamp_index)
        target_batch_positions.append(positions[timestamp_index])
    return unique_indices, target_batch_positions


def _prefetched_cached_chunks(
    arrays: Mapping[str, np.ndarray],
    chunks: Iterator[tuple[int, ...]],
    buffer: _PinnedCudaDoubleBuffer,
    telemetry: RuntimeTelemetry | None,
) -> Iterator[tuple[tuple[int, ...], tuple[Any, ...]]]:
    def transfer(selected: tuple[int, ...]) -> tuple[tuple[int, ...], tuple[Any, ...], Any]:
        indices, positions = _cached_transfer_spec(arrays, selected)
        transferred, completion = buffer.transfer(
            arrays,
            indices,
            telemetry,
            row_indices=selected,
            target_batch_positions=positions,
        )
        return selected, transferred, completion

    try:
        current = transfer(next(chunks))
    except StopIteration:
        return
    if telemetry is not None:
        telemetry.prefetch_calls += 1
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="r4-cache-prefetch") as executor:
        for next_selected in chunks:
            following = executor.submit(transfer, next_selected)
            if telemetry is not None:
                telemetry.prefetch_calls += 1
            buffer.wait_ready(current[2])
            yield current[0], current[1]
            current = following.result()
            if telemetry is not None and not current[2].query():
                telemetry.overlapped_prefetch_calls += 1
        buffer.wait_ready(current[2])
        yield current[0], current[1]


@dataclass(frozen=True)
class ValidatedResidualFoundation:
    """One-time validation capability for an authenticated residual foundation."""

    foundation: AuthenticatedResidualFoundation
    _seal: object

    def __post_init__(self) -> None:
        if self._seal is not _VALIDATED_FOUNDATION_SEAL:
            raise TypeError("validated residual foundation capability is not sealed")


def validate_residual_foundation(
    foundation: AuthenticatedResidualFoundation,
) -> ValidatedResidualFoundation:
    if not isinstance(foundation, AuthenticatedResidualFoundation):
        raise TypeError("foundation validation requires an authenticated OOF foundation")
    foundation._validate()
    return ValidatedResidualFoundation(foundation, _VALIDATED_FOUNDATION_SEAL)


def _make_stream_residual_batch(
    tensors: Mapping[str, MaskedTensor] | Sequence[tuple[str, MaskedTensor]],
    foundation: AuthenticatedResidualFoundation,
    support: SupportRecord,
    preprocessor: FittedTrainingPreprocessor,
    foundation_capability: ValidatedResidualFoundation | None = None,
    *,
    projected_rows: Any | None = None,
    batch_size: int = TIMESTAMP_BATCH_SIZE,
) -> _StreamingResidualTrainingBatch:
    if batch_size != TIMESTAMP_BATCH_SIZE:
        raise ValueError("streaming training batch size is frozen")
    if foundation_capability is None or foundation_capability.foundation is not foundation:
        raise ValueError("foundation capability does not match streaming foundation")
    support = _validate_authenticated_support(support)
    if not isinstance(preprocessor, FittedTrainingPreprocessor):
        raise TypeError("PRIMARY streaming training requires an authenticated fitted preprocessor")
    preprocessor._validate()
    if (
        preprocessor.mode != "PRIMARY"
        or preprocessor.contract_identity != TensorContract().identity
    ):
        raise ValueError("PRIMARY streaming training preprocessor provenance is not canonical")
    preprocessor_identity = training_preprocessor_identity(preprocessor)
    frame = (foundation.rows if projected_rows is None else projected_rows).sort(
        ["decision_time", "instrument_id", "target_id"]
    )
    row_keys = _projected_residual_keys(frame)
    residuals = tuple(float(value) for value in frame["local_residual"].to_list())
    target_nodes = tuple(
        ALL_INSTRUMENTS.index(str(value)) for value in frame["instrument_id"].to_list()
    )
    training_blocks = tuple(str(value) for value in frame["block"].to_list())
    identities = _validate_stream_provider_lineage(tensors, support)
    if (
        tuple(sorted({row["decision_time"].isoformat() for row in frame.iter_rows(named=True)}))
        != support.keys
    ):
        raise ValueError("streaming residual keys are not canonical")
    return _StreamingResidualTrainingBatch(
        tensors=tensors,
        preprocessor=preprocessor,
        row_keys=row_keys,
        residuals=residuals,
        target_nodes=target_nodes,
        training_blocks=training_blocks,
        input_identity=foundation.content_identity,
        support_identity=support.identity,
        chronology_identity=foundation.chronology_identity,
        preprocessor_identity=preprocessor_identity,
        content_identity=_sha256(
            {
                "policy": TIMESTAMP_MATERIALISATION_POLICY,
                "batch_size": batch_size,
                "row_keys": row_keys,
                "residuals": residuals,
                "target_nodes": target_nodes,
                "training_blocks": training_blocks,
                "input_identity": foundation.content_identity,
                "support": support.identity,
                "tensor_identities": identities,
                "chronology": foundation.chronology_identity,
                "preprocessor": preprocessor_identity,
            }
        ),
        _capability=_STREAMING_BATCH_CAPABILITY,
        batch_size=batch_size,
        provider_tensor_identities=identities,
        _support=support,
        _foundation=foundation,
    )


def _make_stream_prediction_batch(
    tensors: Mapping[str, MaskedTensor] | Sequence[tuple[str, MaskedTensor]],
    support: SupportRecord,
    preprocessor: FittedTrainingPreprocessor,
    input_identity: str,
    *,
    batch_size: int = TIMESTAMP_BATCH_SIZE,
) -> _StreamingPredictionBatch:
    if batch_size != TIMESTAMP_BATCH_SIZE:
        raise ValueError("streaming prediction batch size is frozen")
    support = _validate_authenticated_support(support)
    if not isinstance(preprocessor, FittedTrainingPreprocessor):
        raise TypeError(
            "PRIMARY streaming prediction requires an authenticated fitted preprocessor"
        )
    preprocessor._validate()
    if (
        preprocessor.mode != "PRIMARY"
        or preprocessor.contract_identity != TensorContract().identity
    ):
        raise ValueError("PRIMARY streaming prediction preprocessor provenance is not canonical")
    preprocessor_identity = training_preprocessor_identity(preprocessor)
    identities = _validate_stream_provider_lineage(tensors, support)
    identity_map = dict(identities)
    expected_input = _sha256(
        {
            "support_identity": support.identity,
            "row_keys": support.target_keys,
            "tensor_identities": tuple(
                identity_map[key.rsplit("|", 1)[-1]] for key in support.target_keys
            ),
            "preprocessor_identity": preprocessor_identity,
            "policy": TIMESTAMP_MATERIALISATION_POLICY,
        }
    )
    if input_identity != expected_input:
        raise ValueError("prediction input identity does not match support lineage")
    return _StreamingPredictionBatch(
        tensors=tensors,
        preprocessor=preprocessor,
        row_keys=support.target_keys,
        target_nodes=support.target_nodes,
        target_mask=tuple(True for _ in support.target_keys),
        input_identity=input_identity,
        support_identity=support.identity,
        chronology_identity=CHRONOLOGY_IDENTITY,
        preprocessor_identity=preprocessor_identity,
        content_identity=_sha256(
            {
                "policy": TIMESTAMP_MATERIALISATION_POLICY,
                "batch_size": batch_size,
                "row_keys": support.target_keys,
                "target_nodes": support.target_nodes,
                "input_identity": input_identity,
                "support": support.identity,
                "tensor_identities": identities,
                "chronology": CHRONOLOGY_IDENTITY,
                "preprocessor": preprocessor_identity,
            }
        ),
        _capability=_STREAMING_BATCH_CAPABILITY,
        batch_size=batch_size,
        provider_tensor_identities=identities,
        _support=support,
    )


def _ordered_keyed_tensors(
    tensors: Mapping[str, MaskedTensor] | Sequence[tuple[str, MaskedTensor]],
    foundation: AuthenticatedResidualFoundation,
) -> tuple[MaskedTensor, ...]:
    if isinstance(tensors, Mapping):
        records = tuple((str(key), value) for key, value in tensors.items())
    else:
        records = tuple(tensors)
    expected_keys = foundation.ordered_row_keys
    if tuple(key for key, _ in records) != expected_keys:
        raise ValueError("authenticated tensors must be keyed by exact ordered residual keys")
    ordered_rows = foundation.rows.sort(["decision_time", "instrument_id", "target_id"])
    result: list[MaskedTensor] = []
    for (key, tensor), row in zip(records, ordered_rows.iter_rows(named=True), strict=True):
        if not isinstance(tensor, MaskedTensor):
            raise TypeError("authenticated tensor records must contain MaskedTensor values")
        expected_key = (
            f"{row['target_id']}|{row['instrument_id']}|{row['decision_time'].isoformat()}"
        )
        if key != expected_key or tensor.decision_time != row["decision_time"]:
            raise ValueError(
                "authenticated tensor key or decision time does not match residual row"
            )
        if tensor.contract_identity != TensorContract().identity:
            raise ValueError("authenticated tensor contract is not canonical")
        result.append(tensor)
    return tuple(result)


def _validate_authenticated_support(
    support: Any, foundation: AuthenticatedResidualFoundation | None = None
) -> SupportRecord:
    if not isinstance(support, SupportRecord):
        raise TypeError("primary execution requires an authenticated SupportRecord")
    support._validate()
    if support.mode != "PRIMARY":
        raise TypeError("PRIMARY execution rejects synthetic or SMOKE support provenance")
    if support.parent_identity != PARENT_IDENTITY:
        raise ValueError("authenticated support parent identity is not canonical")
    contract = TensorContract()
    if (
        support.contract_identity != contract.identity
        or support.lookback_minutes != contract.lookback_minutes
        or support.node_order != tuple(ALL_INSTRUMENTS)
        or support.feature_names != contract.feature_names
    ):
        raise ValueError("authenticated support contract does not match canonical tensor contract")
    if foundation is not None:
        expected_times = tuple(
            sorted(
                {row["decision_time"].isoformat() for row in foundation.rows.iter_rows(named=True)}
            )
        )
        if support.keys != expected_times or support.key_count != len(expected_times):
            raise ValueError(
                "authenticated support keys do not exactly cover residual decision times"
            )
    expected_hash = hashlib.sha256(_canonical([*support.keys])).hexdigest()
    if support.ordered_key_sha256 != expected_hash:
        raise ValueError("authenticated support ordered-key digest mismatch")
    if not support.target_keys or len(support.target_nodes) != len(support.target_keys):
        raise ValueError("authenticated support lacks candidate-independent target metadata")
    return support


def _projected_residual_keys(rows: Any) -> tuple[str, ...]:
    return tuple(
        f"{row['target_id']}|{row['instrument_id']}|{row['decision_time'].isoformat()}"
        for row in rows.sort(["decision_time", "instrument_id", "target_id"]).iter_rows(named=True)
    )


def project_authenticated_training_rows(
    foundation: AuthenticatedResidualFoundation,
    support: Any,
    *,
    foundation_capability: ValidatedResidualFoundation | None = None,
) -> Any:
    """Project authenticated OOF labels to exactly the stage support chronology."""
    import polars as pl

    capability = foundation_capability or validate_residual_foundation(foundation)
    if capability.foundation is not foundation:
        raise ValueError("foundation capability does not match projected foundation")
    authenticated_support = _validate_authenticated_support(support)
    decision_times = tuple(datetime.fromisoformat(value) for value in authenticated_support.keys)
    rows = foundation.rows.filter(pl.col("decision_time").is_in(decision_times)).sort(
        ["decision_time", "instrument_id", "target_id"]
    )
    projected_times = tuple(
        sorted({row["decision_time"].isoformat() for row in rows.iter_rows(named=True)})
    )
    if projected_times != authenticated_support.keys:
        raise ValueError("projected OOF rows do not exactly cover stage support chronology")
    if not rows.height:
        raise ValueError("projected OOF training rows cannot be empty")
    return rows


def _ordered_support_tensors(
    tensors: Mapping[str, MaskedTensor] | Sequence[tuple[str, MaskedTensor]],
    support: SupportRecord,
) -> tuple[MaskedTensor, ...]:
    if isinstance(tensors, Mapping):
        mapping = cast(Mapping[str, MaskedTensor], tensors)
        records = tuple((key, mapping[key]) for key in support.target_keys)
    else:
        records = tuple(tensors)
        if tuple(key for key, _ in records) != support.target_keys:
            raise ValueError("authenticated tensors must be keyed by exact support target keys")
    result: list[MaskedTensor] = []
    for index, (key, tensor) in enumerate(records):
        if not isinstance(tensor, MaskedTensor):
            raise TypeError("authenticated tensor records must contain MaskedTensor values")
        try:
            instrument, timestamp_text = key.split("|", 1)
            expected_time = datetime.fromisoformat(timestamp_text)
        except (TypeError, ValueError) as exc:
            raise ValueError("authenticated support target key is malformed") from exc
        if instrument != ALL_INSTRUMENTS[support.target_nodes[index]]:
            raise ValueError(
                "authenticated support target key instrument does not match node metadata"
            )
        if expected_time.tzinfo is None or expected_time.utcoffset() != timedelta(0):
            raise ValueError("authenticated support target key must use UTC")
        if tensor.decision_time != expected_time:
            raise ValueError("authenticated tensor decision time does not match support key")
        expected_tensor_identity = dict(support.tensor_identities).get(expected_time.isoformat())
        if expected_tensor_identity != _masked_tensor_identity(tensor):
            raise ValueError("authenticated tensor content does not match support lineage")
        if tensor.contract_identity != TensorContract().identity:
            raise ValueError("authenticated tensor contract is not canonical")
        result.append(tensor)
    return tuple(result)


def _expected_batch_identity(batch: Any) -> str:
    payload = {
        "mode": batch.mode,
        "values": _tensor_digest(batch.values),
        "value_mask": _tensor_digest(batch.value_mask),
        "availability_mask": _tensor_digest(batch.availability_mask),
        "node_mask": _tensor_digest(batch.node_mask),
        "target_nodes": _tensor_digest(batch.target_nodes),
        "target_mask": _tensor_digest(batch.target_mask),
        "instrument_counts": _tensor_digest(batch.instrument_counts),
        "row_weights": _tensor_digest(batch.row_weights),
        "row_keys": batch.row_keys,
        "input_identity": batch.input_identity,
        "support_identity": batch.support_identity,
        "chronology_identity": batch.chronology_identity,
        "preprocessor_identity": batch.preprocessor_identity,
        "training_blocks": getattr(batch, "training_blocks", None),
    }
    if isinstance(batch, ResidualTrainingBatch):
        payload["residuals"] = _tensor_digest(batch.residuals)
    return _sha256(payload)


@dataclass(frozen=True, init=False)
class ResidualTrainingBatch:
    """Sealed, digested residual rows for primary training or explicit smoke."""

    values: Any
    value_mask: Any
    availability_mask: Any
    node_mask: Any
    residuals: Any
    target_nodes: Any
    target_mask: Any
    input_identity: str
    support_identity: str
    chronology_identity: str
    preprocessor_identity: str
    row_keys: tuple[str, ...]
    mode: str
    instrument_counts: Any
    row_weights: Any
    content_identity: str
    training_blocks: tuple[str, ...] | None = None
    _provenance: object = None
    _sealed: object = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise TypeError("residual training batches must be created by an authenticated factory")

    @classmethod
    def _create(cls, *, token: object, **fields: Any) -> ResidualTrainingBatch:
        if token is not _BATCH_SEAL:
            raise TypeError("residual training batch seal is private")
        instance = object.__new__(cls)
        for name, value in fields.items():
            object.__setattr__(instance, name, value)
        instance._validate()
        return instance

    @classmethod
    def from_smoke(
        cls,
        *,
        values: Any,
        residuals: Any,
        target_nodes: Any,
        target_mask: Any,
        preprocessor_identity: str,
    ) -> ResidualTrainingBatch:
        if torch is None:
            raise RuntimeError("R4-P0 CUDA runtime requires torch")
        rows = int(values.shape[0])
        return cls._create(
            token=_BATCH_SEAL,
            values=values,
            value_mask=torch.ones_like(values, dtype=torch.bool),
            availability_mask=torch.ones_like(values, dtype=torch.bool),
            node_mask=torch.ones(values.shape[:3], dtype=torch.bool, device=values.device),
            residuals=residuals,
            target_nodes=target_nodes,
            target_mask=target_mask,
            input_identity=RESIDUAL_FOUNDATION_IDENTITY,
            support_identity=SUPPORT_IDENTITY,
            chronology_identity=CHRONOLOGY_IDENTITY,
            preprocessor_identity=preprocessor_identity,
            row_keys=tuple(f"SMOKE|{index}" for index in range(rows)),
            mode="SMOKE",
            instrument_counts=None,
            row_weights=None,
            training_blocks=None,
            content_identity="",
            _provenance=_SMOKE_BATCH_CAPABILITY,
            _sealed=_BATCH_SEAL,
        )

    @classmethod
    def from_authenticated_oof(
        cls,
        *,
        tensors: Mapping[str, MaskedTensor] | Sequence[tuple[str, MaskedTensor]],
        foundation: AuthenticatedResidualFoundation,
        support: Any,
        preprocessor: FittedTrainingPreprocessor,
        projected_rows: Any | None = None,
        foundation_capability: ValidatedResidualFoundation | None = None,
        stream: bool = False,
        batch_size: int = TIMESTAMP_BATCH_SIZE,
    ) -> Any:
        if torch is None:
            raise RuntimeError("R4-P0 CUDA runtime requires torch")
        capability = foundation_capability or validate_residual_foundation(foundation)
        if capability.foundation is not foundation:
            raise ValueError("foundation capability does not match training foundation")
        if not isinstance(preprocessor, FittedTrainingPreprocessor):
            raise TypeError("primary training requires an authenticated fitted preprocessor")
        preprocessor._validate()
        if preprocessor.mode != "PRIMARY":
            raise TypeError("PRIMARY training rejects synthetic or SMOKE preprocessor provenance")
        if preprocessor.contract_identity != TensorContract().identity:
            raise ValueError("primary training requires the canonical 60-minute tensor contract")
        authenticated_support = _validate_authenticated_support(support)
        rows = (
            project_authenticated_training_rows(
                foundation,
                authenticated_support,
                foundation_capability=capability,
            )
            if projected_rows is None
            else projected_rows
        )
        expected_times = tuple(
            sorted({row["decision_time"].isoformat() for row in rows.iter_rows(named=True)})
        )
        if expected_times != authenticated_support.keys:
            raise ValueError("authenticated projected rows do not match stage support chronology")
        if stream:
            if batch_size != TIMESTAMP_BATCH_SIZE:
                raise ValueError("streaming training batch size is frozen")
            return _make_stream_residual_batch(
                tensors,
                foundation,
                authenticated_support,
                preprocessor,
                capability,
                projected_rows=rows,
                batch_size=batch_size,
            )
        ordered_tensors = _ordered_keyed_tensors(tensors, foundation)
        expected_support_tensors = dict(authenticated_support.tensor_identities)
        for tensor in ordered_tensors:
            expected_tensor_identity = expected_support_tensors.get(
                tensor.decision_time.isoformat()
            )
            if expected_tensor_identity != _masked_tensor_identity(tensor):
                raise ValueError("primary training tensor content does not match support lineage")
        values, value_mask, availability_mask, node_mask = _stack_preprocessed(
            ordered_tensors, preprocessor
        )
        frame = rows.sort(["decision_time", "instrument_id", "target_id"])
        residuals = torch.as_tensor(
            frame["local_residual"].to_numpy(), dtype=torch.float32, device=values.device
        )
        target_nodes = torch.as_tensor(
            [ALL_INSTRUMENTS.index(str(value)) for value in frame["instrument_id"].to_list()],
            dtype=torch.long,
            device=values.device,
        )
        # Authenticated residual labels, not final-node availability, determine target eligibility.
        target_mask = torch.ones_like(target_nodes, dtype=torch.bool)
        expected_keys = _projected_residual_keys(frame)
        return cls._create(
            token=_BATCH_SEAL,
            values=values,
            value_mask=value_mask,
            availability_mask=availability_mask,
            node_mask=node_mask,
            residuals=residuals,
            target_nodes=target_nodes,
            target_mask=target_mask,
            input_identity=foundation.content_identity,
            support_identity=authenticated_support.identity,
            chronology_identity=foundation.chronology_identity,
            preprocessor_identity=training_preprocessor_identity(preprocessor),
            row_keys=expected_keys,
            mode="PRIMARY",
            instrument_counts=None,
            row_weights=None,
            training_blocks=tuple(str(value) for value in frame["block"].to_list()),
            content_identity="",
            _provenance=_PRIMARY_BATCH_CAPABILITY,
            _sealed=_BATCH_SEAL,
        )

    @classmethod
    def from_stage_cache(
        cls, *, arrays: Mapping[str, np.ndarray], metadata: Mapping[str, object]
    ) -> ResidualTrainingBatch:
        """Reconstruct a sealed PRIMARY batch from a verified stage cache."""
        required = (
            "values",
            "value_mask",
            "availability_mask",
            "node_mask",
            "residuals",
            "target_nodes",
            "target_mask",
        )
        if set(arrays) != set(required):
            raise ValueError("training cache arrays do not match the batch contract")
        return cls._create(
            token=_BATCH_SEAL,
            **{name: torch.from_numpy(arrays[name]) for name in required},
            input_identity=metadata["input_identity"],
            support_identity=metadata["support_identity"],
            chronology_identity=metadata["chronology_identity"],
            preprocessor_identity=metadata["preprocessor_identity"],
            row_keys=tuple(cast(Sequence[str], metadata["row_keys"])),
            mode=cast(str, metadata["mode"]),
            instrument_counts=None,
            row_weights=None,
            training_blocks=(
                tuple(cast(Sequence[str], metadata["training_blocks"]))
                if metadata["mode"] == "PRIMARY"
                else None
            ),
            content_identity=cast(str, metadata["content_identity"]),
            _provenance=(
                _PRIMARY_BATCH_CAPABILITY
                if metadata["mode"] == "PRIMARY"
                else _SMOKE_BATCH_CAPABILITY
            ),
            _sealed=_BATCH_SEAL,
        )

    def _validate(self) -> None:
        if self._sealed is not _BATCH_SEAL:
            raise TypeError("residual training batch is not sealed")
        expected_provenance = (
            _PRIMARY_BATCH_CAPABILITY if self.mode == "PRIMARY" else _SMOKE_BATCH_CAPABILITY
        )
        if self._provenance is not expected_provenance:
            raise TypeError("residual training batch provenance capability is invalid")
        if torch is None:
            raise RuntimeError("R4-P0 CUDA runtime requires torch")
        tensors = (
            self.values,
            self.value_mask,
            self.availability_mask,
            self.node_mask,
            self.residuals,
            self.target_nodes,
            self.target_mask,
        )
        if not all(isinstance(item, torch.Tensor) for item in tensors):
            raise TypeError("residual training batch fields must be torch tensors")
        values = self.values.detach().clone()
        value_mask = self.value_mask.detach().clone()
        availability_mask = self.availability_mask.detach().clone()
        node_mask = self.node_mask.detach().clone()
        residuals = self.residuals.detach().clone()
        target_nodes = self.target_nodes.detach().clone().to(dtype=torch.long)
        target_mask = self.target_mask.detach().clone()
        for name, value in (
            ("values", values),
            ("value_mask", value_mask),
            ("availability_mask", availability_mask),
            ("node_mask", node_mask),
            ("residuals", residuals),
            ("target_nodes", target_nodes),
            ("target_mask", target_mask),
        ):
            value.requires_grad_(False)
            object.__setattr__(self, name, value)
        contract = TensorContract()
        expected = (
            contract.lookback_minutes + 1,
            len(ALL_INSTRUMENTS),
            len(contract.feature_names),
        )
        if values.ndim != 4 or tuple(values.shape[1:]) != expected:
            raise ValueError("residual batch tensor does not match canonical sequence shape")
        if (
            value_mask.shape != values.shape
            or availability_mask.shape != values.shape
            or node_mask.shape != values.shape[:3]
            or value_mask.dtype != torch.bool
            or availability_mask.dtype != torch.bool
            or node_mask.dtype != torch.bool
        ):
            raise ValueError("residual batch masks do not match canonical tensor shape")
        if bool((value_mask & ~availability_mask).any()) or bool(
            (availability_mask & ~node_mask.unsqueeze(-1)).any()
        ):
            raise ValueError("residual batch masks violate availability semantics")
        if residuals.ndim not in {1, 2} or (residuals.ndim == 2 and residuals.shape[1] != 1):
            raise ValueError("residual batch targets must be one-dimensional per row")
        if (
            residuals.shape[0] != values.shape[0]
            or target_nodes.ndim != 1
            or target_nodes.shape[0] != values.shape[0]
        ):
            raise ValueError("residual batch fields must align with sequences")
        if (
            target_mask.shape != residuals.shape
            or target_mask.dtype != torch.bool
            or not bool(target_mask.all())
        ):
            raise ValueError("residual batch target mask must be complete and boolean")
        if not bool(torch.isfinite(values).all()) or not bool(torch.isfinite(residuals).all()):
            raise ValueError("residual batch values and targets must be finite")
        if bool(((target_nodes < 0) | (target_nodes >= len(ALL_INSTRUMENTS))).any()):
            raise ValueError("residual batch target node is outside canonical universe")
        if torch.unique(target_nodes).numel() != len(ALL_INSTRUMENTS):
            raise ValueError("residual batch must cover every canonical instrument")
        instrument_counts, row_weights = _global_instrument_weights(target_nodes)
        for name, expected_value in (
            ("instrument_counts", instrument_counts),
            ("row_weights", row_weights),
        ):
            supplied = getattr(self, name)
            if supplied is not None and (
                not isinstance(supplied, torch.Tensor)
                or not torch.equal(supplied.to(device=expected_value.device), expected_value)
            ):
                raise ValueError(f"residual batch {name} do not match target rows")
            expected_value = expected_value.detach().clone()
            expected_value.requires_grad_(False)
            object.__setattr__(self, name, expected_value)
        for identity in (
            self.input_identity,
            self.support_identity,
            self.chronology_identity,
            self.preprocessor_identity,
        ):
            if not isinstance(identity, str) or not identity:
                raise ValueError("residual batch identities are required")
        if self.mode not in {"PRIMARY", "SMOKE"}:
            raise ValueError("residual batch mode is invalid")
        if (
            not isinstance(self.row_keys, tuple)
            or len(self.row_keys) != values.shape[0]
            or len(set(self.row_keys)) != len(self.row_keys)
        ):
            raise ValueError("residual batch row keys are not unique and ordered")
        if self.training_blocks is not None and (
            len(self.training_blocks) != values.shape[0]
            or any(block not in DEVELOPMENT_BLOCKS for block in self.training_blocks)
        ):
            raise ValueError("residual batch training block lineage is invalid")
        expected_identity = _expected_batch_identity(self)
        if self.content_identity and self.content_identity != expected_identity:
            raise ValueError("residual batch content digest mismatch")
        object.__setattr__(self, "content_identity", expected_identity)


@dataclass(frozen=True, init=False)
class PredictionBatch:
    """Sealed outcome-blind preprocessed values for inference."""

    values: Any
    value_mask: Any
    availability_mask: Any
    node_mask: Any
    target_nodes: Any
    target_mask: Any
    input_identity: str
    support_identity: str
    chronology_identity: str
    preprocessor_identity: str
    row_keys: tuple[str, ...]
    mode: str
    instrument_counts: Any
    row_weights: Any
    content_identity: str
    _provenance: object = None
    _sealed: object = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise TypeError("prediction batches must be created by an authenticated factory")

    @classmethod
    def _create(cls, *, token: object, **fields: Any) -> PredictionBatch:
        if token is not _PREDICTION_SEAL:
            raise TypeError("prediction batch seal is private")
        instance = object.__new__(cls)
        for name, value in fields.items():
            object.__setattr__(instance, name, value)
        instance._validate()
        return instance

    @classmethod
    def from_smoke(
        cls,
        *,
        values: Any,
        target_nodes: Any,
        target_mask: Any,
        preprocessor_identity: str,
    ) -> PredictionBatch:
        return cls._create(
            token=_PREDICTION_SEAL,
            values=values,
            value_mask=torch.ones_like(values, dtype=torch.bool),
            availability_mask=torch.ones_like(values, dtype=torch.bool),
            node_mask=torch.ones(values.shape[:3], dtype=torch.bool, device=values.device),
            target_nodes=target_nodes,
            target_mask=target_mask,
            input_identity=RESIDUAL_FOUNDATION_IDENTITY,
            support_identity=SUPPORT_IDENTITY,
            chronology_identity=CHRONOLOGY_IDENTITY,
            preprocessor_identity=preprocessor_identity,
            row_keys=tuple(f"SMOKE|{index}" for index in range(int(values.shape[0]))),
            mode="SMOKE",
            instrument_counts=None,
            row_weights=None,
            content_identity="",
            _provenance=_SMOKE_BATCH_CAPABILITY,
            _sealed=_PREDICTION_SEAL,
        )

    @classmethod
    def from_authenticated_support(
        cls,
        *,
        tensors: Mapping[str, MaskedTensor] | Sequence[tuple[str, MaskedTensor]],
        support: Any,
        preprocessor: FittedTrainingPreprocessor,
        input_identity: str,
        chronology_identity: str = CHRONOLOGY_IDENTITY,
        stream: bool = False,
        batch_size: int = TIMESTAMP_BATCH_SIZE,
    ) -> Any:
        if torch is None:
            raise RuntimeError("R4-P0 CUDA runtime requires torch")
        authenticated_support = _validate_authenticated_support(support)
        if not isinstance(preprocessor, FittedTrainingPreprocessor):
            raise TypeError("primary prediction requires an authenticated fitted preprocessor")
        preprocessor._validate()
        if preprocessor.mode != "PRIMARY":
            raise TypeError("PRIMARY prediction rejects synthetic or SMOKE preprocessor provenance")
        if preprocessor.contract_identity != TensorContract().identity:
            raise ValueError("primary prediction requires the canonical 60-minute tensor contract")
        if chronology_identity != CHRONOLOGY_IDENTITY:
            raise ValueError("primary prediction chronology identity is not canonical")
        if stream:
            if batch_size != TIMESTAMP_BATCH_SIZE:
                raise ValueError("streaming prediction batch size is frozen")
            return _make_stream_prediction_batch(
                tensors, authenticated_support, preprocessor, input_identity, batch_size=batch_size
            )
        ordered_tensors = _ordered_support_tensors(tensors, authenticated_support)
        preprocessor_identity = training_preprocessor_identity(preprocessor)
        expected_input_identity = _sha256(
            {
                "support_identity": authenticated_support.identity,
                "row_keys": authenticated_support.target_keys,
                "tensor_identities": tuple(
                    _masked_tensor_identity(item) for item in ordered_tensors
                ),
                "preprocessor_identity": preprocessor_identity,
            }
        )
        if input_identity != expected_input_identity:
            raise ValueError("primary prediction input identity does not match support lineage")
        if chronology_identity != CHRONOLOGY_IDENTITY:
            raise ValueError("primary prediction chronology identity is not canonical")
        values, value_mask, availability_mask, node_mask = _stack_preprocessed(
            ordered_tensors, preprocessor
        )
        target_nodes = torch.as_tensor(
            authenticated_support.target_nodes, dtype=torch.long, device=values.device
        )
        # Authenticated support target keys determine eligibility; inputs may remain masked.
        target_mask = torch.ones_like(target_nodes, dtype=torch.bool)
        return cls._create(
            token=_PREDICTION_SEAL,
            values=values,
            value_mask=value_mask,
            availability_mask=availability_mask,
            node_mask=node_mask,
            target_nodes=target_nodes,
            target_mask=target_mask,
            input_identity=input_identity,
            support_identity=authenticated_support.identity,
            chronology_identity=chronology_identity,
            preprocessor_identity=training_preprocessor_identity(preprocessor),
            row_keys=authenticated_support.target_keys,
            mode="PRIMARY",
            instrument_counts=None,
            row_weights=None,
            content_identity="",
            _provenance=_PRIMARY_BATCH_CAPABILITY,
            _sealed=_PREDICTION_SEAL,
        )

    @classmethod
    def from_stage_cache(
        cls, *, arrays: Mapping[str, np.ndarray], metadata: Mapping[str, object]
    ) -> PredictionBatch:
        """Reconstruct a sealed PRIMARY batch from a verified stage cache."""
        required = (
            "values",
            "value_mask",
            "availability_mask",
            "node_mask",
            "target_nodes",
            "target_mask",
        )
        if set(arrays) != set(required):
            raise ValueError("prediction cache arrays do not match the batch contract")
        return cls._create(
            token=_PREDICTION_SEAL,
            **{name: torch.from_numpy(arrays[name]) for name in required},
            input_identity=metadata["input_identity"],
            support_identity=metadata["support_identity"],
            chronology_identity=metadata["chronology_identity"],
            preprocessor_identity=metadata["preprocessor_identity"],
            row_keys=tuple(cast(Sequence[str], metadata["row_keys"])),
            mode=cast(str, metadata["mode"]),
            instrument_counts=None,
            row_weights=None,
            content_identity=cast(str, metadata["content_identity"]),
            _provenance=(
                _PRIMARY_BATCH_CAPABILITY
                if metadata["mode"] == "PRIMARY"
                else _SMOKE_BATCH_CAPABILITY
            ),
            _sealed=_PREDICTION_SEAL,
        )

    def _validate(self) -> None:
        if self._sealed is not _PREDICTION_SEAL:
            raise TypeError("prediction batch is not sealed")
        expected_provenance = (
            _PRIMARY_BATCH_CAPABILITY if self.mode == "PRIMARY" else _SMOKE_BATCH_CAPABILITY
        )
        if self._provenance is not expected_provenance:
            raise TypeError("prediction batch provenance capability is invalid")
        if torch is None or not all(
            isinstance(item, torch.Tensor)
            for item in (
                self.values,
                self.value_mask,
                self.availability_mask,
                self.node_mask,
                self.target_nodes,
                self.target_mask,
            )
        ):
            raise TypeError("prediction batch fields must be torch tensors")
        values = self.values.detach().clone()
        value_mask = self.value_mask.detach().clone()
        availability_mask = self.availability_mask.detach().clone()
        node_mask = self.node_mask.detach().clone()
        target_nodes = self.target_nodes.detach().clone().to(dtype=torch.long)
        target_mask = self.target_mask.detach().clone()
        for name, value in (
            ("values", values),
            ("value_mask", value_mask),
            ("availability_mask", availability_mask),
            ("node_mask", node_mask),
            ("target_nodes", target_nodes),
            ("target_mask", target_mask),
        ):
            value.requires_grad_(False)
            object.__setattr__(self, name, value)
        contract = TensorContract()
        expected = (
            contract.lookback_minutes + 1,
            len(ALL_INSTRUMENTS),
            len(contract.feature_names),
        )
        if values.ndim != 4 or tuple(values.shape[1:]) != expected:
            raise ValueError("prediction batch tensor does not match canonical sequence shape")
        if (
            value_mask.shape != values.shape
            or availability_mask.shape != values.shape
            or node_mask.shape != values.shape[:3]
            or value_mask.dtype != torch.bool
            or availability_mask.dtype != torch.bool
            or node_mask.dtype != torch.bool
        ):
            raise ValueError("prediction batch masks do not match canonical tensor shape")
        if bool((value_mask & ~availability_mask).any()) or bool(
            (availability_mask & ~node_mask.unsqueeze(-1)).any()
        ):
            raise ValueError("prediction batch masks violate availability semantics")
        if (
            target_nodes.ndim != 1
            or target_nodes.shape[0] != values.shape[0]
            or target_mask.shape != target_nodes.shape
        ):
            raise ValueError("prediction batch fields must align with sequences")
        if target_mask.dtype != torch.bool or not bool(target_mask.all()):
            raise ValueError("prediction batch mask must be complete and boolean")
        if not bool(torch.isfinite(values).all()):
            raise ValueError("prediction batch values must be finite")
        if bool(((target_nodes < 0) | (target_nodes >= len(ALL_INSTRUMENTS))).any()):
            raise ValueError("prediction target node is outside canonical universe")
        if torch.unique(target_nodes).numel() != len(ALL_INSTRUMENTS):
            raise ValueError("prediction batch must cover every canonical instrument")
        instrument_counts, row_weights = _global_instrument_weights(target_nodes)
        object.__setattr__(self, "instrument_counts", instrument_counts)
        object.__setattr__(self, "row_weights", row_weights)
        for identity in (
            self.input_identity,
            self.support_identity,
            self.chronology_identity,
            self.preprocessor_identity,
        ):
            if not isinstance(identity, str) or not identity:
                raise ValueError("prediction batch identities are required")
        if self.mode not in {"PRIMARY", "SMOKE"}:
            raise ValueError("prediction batch mode is invalid")
        if (
            not isinstance(self.row_keys, tuple)
            or len(self.row_keys) != values.shape[0]
            or len(set(self.row_keys)) != len(self.row_keys)
        ):
            raise ValueError("prediction batch row keys are not unique and ordered")
        expected_identity = _expected_batch_identity(self)
        if self.content_identity and self.content_identity != expected_identity:
            raise ValueError("prediction batch content digest mismatch")
        object.__setattr__(self, "content_identity", expected_identity)


def training_preprocessor_identity(preprocessor: FittedTrainingPreprocessor) -> str:
    return _sha256(
        {
            "means": preprocessor.means.tolist(),
            "scales": preprocessor.scales.tolist(),
            "feature_names": preprocessor.feature_names,
            "training_cutoff": preprocessor.training_cutoff.isoformat(),
            "contract_identity": preprocessor.contract_identity,
            "fit_partition": preprocessor.fit_partition,
            "binary_features": preprocessor.binary_features,
            "training_partition_identity": preprocessor.training_partition_identity,
            "mode": preprocessor.mode,
            "reduction_algorithm": preprocessor.reduction_algorithm,
        }
    )


def _timestamp_row_chunks(
    row_keys: Sequence[str], indices: Sequence[int], timestamp_batch_size: int
) -> Iterator[tuple[int, ...]]:
    """Yield canonical rows for bounded batches of whole decision timestamps."""
    if timestamp_batch_size <= 0:
        raise ValueError("timestamp batch size must be positive")
    groups: list[list[int]] = []
    previous: str | None = None
    closed: set[str] = set()
    for index in indices:
        timestamp = row_keys[index].rsplit("|", 1)[-1]
        if timestamp != previous:
            if timestamp in closed:
                raise ValueError("canonical rows contain a non-contiguous duplicate timestamp")
            if previous is not None:
                closed.add(previous)
            groups.append([])
            previous = timestamp
        groups[-1].append(index)
    for start in range(0, len(groups), timestamp_batch_size):
        yield tuple(
            index for group in groups[start : start + timestamp_batch_size] for index in group
        )


def _cached_timestamp_row_chunks(
    row_timestamps: np.ndarray, indices: Sequence[int], timestamp_batch_size: int
) -> Iterator[tuple[int, ...]]:
    """Group cached rows using authenticated numeric timestamp indices."""
    if timestamp_batch_size <= 0:
        raise ValueError("timestamp batch size must be positive")
    groups: list[list[int]] = []
    previous: int | None = None
    closed: set[int] = set()
    for index in indices:
        timestamp = int(row_timestamps[index])
        if timestamp != previous:
            if timestamp in closed:
                raise ValueError("cached rows contain a non-contiguous duplicate timestamp")
            if previous is not None:
                closed.add(previous)
            groups.append([])
            previous = timestamp
        groups[-1].append(index)
    for start in range(0, len(groups), timestamp_batch_size):
        yield tuple(
            index for group in groups[start : start + timestamp_batch_size] for index in group
        )


def _iter_stream_training_chunks(
    batch: _StreamingResidualTrainingBatch,
    indices: Sequence[int],
    batch_width: int,
    telemetry: RuntimeTelemetry | None = None,
) -> Iterator[tuple[Any, Any, Any, Any, Tensor, Tensor, Tensor, Tensor]]:
    cuda_buffer = _PinnedCudaDoubleBuffer() if batch.cached_arrays is not None else None
    if batch.cached_arrays is not None:
        chunks = _cached_timestamp_row_chunks(
            batch.cached_arrays["row_timestamp"], indices, batch_width
        )
        assert cuda_buffer is not None
        work = _prefetched_cached_chunks(batch.cached_arrays, chunks, cuda_buffer, telemetry)
    else:
        chunks = _timestamp_row_chunks(batch.row_keys, indices, batch_width)
        work = ((selected, None) for selected in chunks)
    for selected, prefetched_arrays in work:
        materialisation_started = time.monotonic()
        cache: dict[str, MaskedTensor] = {}
        unique_timestamps: list[str] = []
        target_batch_positions: list[int] = []
        if batch.cached_arrays is None:
            timestamp_positions: dict[str, int] = {}
            for index in selected:
                timestamp = batch.row_keys[index].rsplit("|", 1)[-1]
                position = timestamp_positions.get(timestamp)
                if position is None:
                    position = len(unique_timestamps)
                    timestamp_positions[timestamp] = position
                    unique_timestamps.append(timestamp)
                target_batch_positions.append(position)
        if batch.cached_arrays is None:
            if batch.preprocessor is None:
                raise TypeError("streaming preprocessor provenance is missing")
            preprocessing_started = time.monotonic()
            tensors = [
                _stream_provider_tensor(batch.tensors, timestamp, cache)
                for timestamp in unique_timestamps
            ]
            values, value_mask, availability_mask, node_mask = _stack_preprocessed(
                tensors, batch.preprocessor
            )
            if telemetry is not None:
                telemetry.preprocessing_calls += len(unique_timestamps)
                telemetry.preprocessing_seconds += time.monotonic() - preprocessing_started
        else:
            assert prefetched_arrays is not None
            (
                values,
                value_mask,
                availability_mask,
                node_mask,
                residuals,
                target_nodes,
                target_batch,
            ) = prefetched_arrays
        device = values.device
        if batch.cached_arrays is None:
            residuals = torch.as_tensor(
                [batch.residuals[index] for index in selected], dtype=torch.float32, device=device
            )
            target_nodes = torch.as_tensor(
                [batch.target_nodes[index] for index in selected], dtype=torch.long, device=device
            )
            target_batch = torch.as_tensor(target_batch_positions, dtype=torch.long, device=device)
        target_mask = torch.ones_like(target_nodes, dtype=torch.bool)
        if telemetry is not None:
            telemetry.materialisation_calls += 1
            telemetry.materialisation_seconds += time.monotonic() - materialisation_started
        yield (
            values,
            value_mask,
            availability_mask,
            node_mask,
            residuals,
            target_nodes,
            target_mask,
            target_batch,
        )


def _validate_streaming_model_policy(model: ResidualGraphModel) -> TrainingConfig:
    if not isinstance(model, ResidualGraphModel):
        raise TypeError("streaming execution requires an authenticated residual graph model")
    canonical = TrainingConfig()
    config = model.training_config
    if not isinstance(config, TrainingConfig) or config != canonical:
        raise ValueError("streaming execution requires the canonical training configuration")
    if config.batch_size != TIMESTAMP_BATCH_SIZE:
        raise ValueError("streaming execution batch size is not canonical")
    return canonical


def _validate_streaming_primary_preprocessor(
    preprocessor: FittedTrainingPreprocessor,
    expected_identity: str,
) -> None:
    if not isinstance(preprocessor, FittedTrainingPreprocessor):
        raise TypeError("streaming execution requires an authenticated fitted preprocessor")
    preprocessor._validate()
    if preprocessor.mode != "PRIMARY":
        raise TypeError("streaming execution rejects non-PRIMARY preprocessor provenance")
    if preprocessor.contract_identity != TensorContract().identity:
        raise ValueError("streaming execution preprocessor contract is not canonical")
    if training_preprocessor_identity(preprocessor) != expected_identity:
        raise ValueError("streaming execution preprocessor identity changed after authentication")


def _reauthenticate_stream_residual_batch(
    batch: _StreamingResidualTrainingBatch,
) -> None:
    if batch.cached_arrays is not None:
        return
    foundation = batch._foundation
    support = batch._support
    if not isinstance(foundation, AuthenticatedResidualFoundation):
        raise TypeError("streaming training batch foundation provenance is missing")
    if not isinstance(support, SupportRecord):
        raise TypeError("streaming training batch support provenance is missing")
    foundation._validate()
    support = _validate_authenticated_support(support, foundation)
    if support.identity != batch.support_identity:
        raise ValueError("streaming training support identity changed after authentication")
    if foundation.content_identity != batch.input_identity:
        raise ValueError("streaming training foundation identity changed after authentication")
    if foundation.chronology_identity != batch.chronology_identity:
        raise ValueError("streaming training chronology changed after authentication")
    preprocessor = batch.preprocessor
    if preprocessor is None:
        raise TypeError("streaming training preprocessor provenance is missing")
    _validate_streaming_primary_preprocessor(preprocessor, batch.preprocessor_identity)
    frame = foundation.rows.sort(["decision_time", "instrument_id", "target_id"])
    expected_row_keys = foundation.ordered_row_keys
    expected_residuals = tuple(float(value) for value in frame["local_residual"].to_list())
    expected_target_nodes = tuple(
        ALL_INSTRUMENTS.index(str(value)) for value in frame["instrument_id"].to_list()
    )
    expected_training_blocks = tuple(str(value) for value in frame["block"].to_list())
    if (
        batch.row_keys != expected_row_keys
        or batch.residuals != expected_residuals
        or batch.target_nodes != expected_target_nodes
        or batch.training_blocks != expected_training_blocks
    ):
        raise ValueError("streaming training metadata changed after authentication")
    _validate_stream_provider_lineage(batch.tensors, support, batch.provider_tensor_identities)


def _reauthenticate_stream_prediction_batch(
    batch: _StreamingPredictionBatch,
) -> None:
    if batch.cached_arrays is not None:
        return
    support = batch._support
    if not isinstance(support, SupportRecord):
        raise TypeError("streaming prediction batch support provenance is missing")
    support = _validate_authenticated_support(support)
    if support.identity != batch.support_identity:
        raise ValueError("streaming prediction support identity changed after authentication")
    if batch.chronology_identity != CHRONOLOGY_IDENTITY:
        raise ValueError("streaming prediction chronology is not canonical")
    preprocessor = batch.preprocessor
    if preprocessor is None:
        raise TypeError("streaming prediction preprocessor provenance is missing")
    _validate_streaming_primary_preprocessor(preprocessor, batch.preprocessor_identity)
    if batch.row_keys != support.target_keys or batch.target_nodes != support.target_nodes:
        raise ValueError("streaming prediction metadata changed after authentication")
    identities = _validate_stream_provider_lineage(
        batch.tensors, support, batch.provider_tensor_identities
    )
    identity_map = dict(identities)
    expected_input_identity = _sha256(
        {
            "support_identity": support.identity,
            "row_keys": support.target_keys,
            "tensor_identities": tuple(
                identity_map[key.rsplit("|", 1)[-1]] for key in support.target_keys
            ),
            "preprocessor_identity": batch.preprocessor_identity,
            "policy": TIMESTAMP_MATERIALISATION_POLICY,
        }
    )
    if batch.input_identity != expected_input_identity:
        raise ValueError("streaming prediction input identity changed after authentication")


def _selected_instrument_counts(
    target_nodes: Sequence[int], indices: Sequence[int]
) -> tuple[int, ...]:
    counts = [0] * len(ALL_INSTRUMENTS)
    for index in indices:
        target = target_nodes[index]
        if target < 0 or target >= len(ALL_INSTRUMENTS):
            raise ValueError("training batch target node is outside canonical universe")
        counts[target] += 1
    if any(count == 0 for count in counts):
        raise ValueError("training batch must cover every canonical instrument")
    return tuple(counts)


def _fit_streaming_batch(
    model: ResidualGraphModel,
    training_batch: _StreamingResidualTrainingBatch,
    *,
    epochs: int | None,
    batch_size: int | None,
    training_blocks: Sequence[str] | None,
    telemetry: RuntimeTelemetry | None = None,
    gradient_probe: dict[str, dict[str, np.ndarray]] | None = None,
    calibration_batch_size: int | None = None,
) -> dict[str, Any]:
    device = require_cuda()
    _reauthenticate_stream_residual_batch(training_batch)
    config = _validate_streaming_model_policy(model)
    epoch_count = epochs if epochs is not None else config.epochs
    batch_width = batch_size if batch_size is not None else config.batch_size
    allowed_width = calibration_batch_size or TIMESTAMP_BATCH_SIZE
    if calibration_batch_size is not None and calibration_batch_size not in {4, 8, 16, 32, 64}:
        raise ValueError("calibration batch size is outside the bounded safe-width set")
    if (
        training_batch.batch_size != TIMESTAMP_BATCH_SIZE
        or epoch_count != config.epochs
        or batch_width != allowed_width
    ):
        raise ValueError("runtime training must use frozen epochs and authorised batch size")
    if training_blocks is None:
        indices = tuple(range(len(training_batch.row_keys)))
    else:
        requested = tuple(training_blocks)
        if not requested or any(block not in DEVELOPMENT_BLOCKS for block in requested):
            raise ValueError("training blocks must be canonical development stages")
        if training_batch.cached_arrays is not None:
            requested_codes = {
                training_batch.training_block_order.index(block) for block in requested
            }
            indices = tuple(
                index
                for index, code in enumerate(training_batch.training_blocks)
                if int(code) in requested_codes
            )
        else:
            indices = tuple(
                index
                for index, block in enumerate(training_batch.training_blocks)
                if block in requested
            )
        if not indices:
            raise ValueError("stage-specific training has no eligible residual rows")
    instrument_counts = _selected_instrument_counts(training_batch.target_nodes, indices)
    host_counts = torch.as_tensor(instrument_counts, dtype=torch.long).pin_memory()
    counts_started = torch.cuda.Event(enable_timing=True) if telemetry is not None else None
    counts_finished = torch.cuda.Event(enable_timing=True) if telemetry is not None else None
    if counts_started is not None:
        counts_started.record()
    all_counts = host_counts.to(device=device, non_blocking=True)
    if telemetry is not None:
        assert counts_started is not None and counts_finished is not None
        counts_finished.record()
        telemetry.record_h2d_transfer((all_counts,))
        telemetry.record_cuda_timing("h2d", counts_started, counts_finished)
    model = model.to(device)
    optimizer = torch.optim.Adam(
        model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay
    )
    model.train()
    torch.cuda.synchronize(device)
    started = time.monotonic()
    if telemetry is not None:
        telemetry.rows += len(indices)
        if training_batch.cached_arrays is not None:
            telemetry.timestamps += len(
                np.unique(training_batch.cached_arrays["row_timestamp"][np.asarray(indices)])
            )
        else:
            telemetry.timestamps += len(
                {training_batch.row_keys[index].rsplit("|", 1)[-1] for index in indices}
            )
        torch.cuda.reset_peak_memory_stats(device)
    for _ in range(epoch_count):
        optimizer.zero_grad(set_to_none=True)
        for (
            values,
            value_mask,
            availability_mask,
            node_mask,
            residuals,
            target_nodes,
            _,
            target_batch,
        ) in _iter_stream_training_chunks(training_batch, indices, batch_width, telemetry):
            if telemetry is not None:
                telemetry.forward_calls += 1
                telemetry.timestamp_batch_calls += 1
                telemetry.representation_timestamp_calls += int(values.shape[0])
                if model.spec.graph_mode == "none":
                    telemetry.local_vectorised_calls += 1
                else:
                    telemetry.shared_representation_calls += 1
            if telemetry is not None:
                forward_started = torch.cuda.Event(enable_timing=True)
                forward_finished = torch.cuda.Event(enable_timing=True)
                forward_started.record()
            prediction = model.forward_grouped(
                values,
                target_node=target_nodes,
                target_batch=target_batch,
                value_mask=value_mask,
                availability_mask=availability_mask,
                node_mask=node_mask,
            )
            if telemetry is not None:
                forward_finished.record()
                telemetry.record_cuda_timing("forward", forward_started, forward_finished)
            selected_weights = 1.0 / (
                all_counts[target_nodes].to(dtype=torch.float32) * len(ALL_INSTRUMENTS)
            )
            errors = (prediction - residuals).square()
            loss = (errors * selected_weights).sum()
            if not bool(torch.isfinite(loss)):
                raise ValueError("non-finite equal-instrument training loss")
            if telemetry is not None:
                backward_started = torch.cuda.Event(enable_timing=True)
                backward_finished = torch.cuda.Event(enable_timing=True)
                backward_started.record()
            loss.backward()
            if telemetry is not None:
                backward_finished.record()
                telemetry.backward_calls += 1
                telemetry.record_cuda_timing("backward", backward_started, backward_finished)
        if gradient_probe is not None:
            gradient_probe["pre_step_gradients"] = {
                name: parameter.grad.detach().cpu().numpy().copy()
                for name, parameter in model.named_parameters()
                if parameter.grad is not None
            }
        if telemetry is not None:
            optimiser_started = torch.cuda.Event(enable_timing=True)
            optimiser_finished = torch.cuda.Event(enable_timing=True)
            optimiser_started.record()
        optimizer.step()
        if telemetry is not None:
            optimiser_finished.record()
            telemetry.optimizer_steps += 1
            telemetry.record_cuda_timing("optimiser", optimiser_started, optimiser_finished)
        if gradient_probe is not None:
            gradient_probe["final_parameters"] = {
                name: parameter.detach().cpu().numpy().copy()
                for name, parameter in model.named_parameters()
            }
    torch.cuda.synchronize(device)
    if telemetry is not None:
        telemetry.finish(device, started)
    return {
        "elapsed_seconds": time.monotonic() - started,
        "parameter_count": model_parameter_count(model),
        "epochs": epoch_count,
        "target_instruments": sum(count > 0 for count in instrument_counts),
        "equal_instrument_loss": True,
        "training_batch_identity": training_batch.content_identity,
    }


def fit_one_model(
    model: ResidualGraphModel,
    training_batch: ResidualTrainingBatch | _StreamingResidualTrainingBatch,
    *,
    epochs: int | None = None,
    batch_size: int | None = None,
    training_blocks: Sequence[str] | None = None,
    _telemetry: RuntimeTelemetry | None = None,
    _gradient_probe: dict[str, dict[str, np.ndarray]] | None = None,
    _calibration_batch_size: int | None = None,
) -> dict[str, Any]:
    """Fit one model on a sealed, authenticated residual batch."""
    device = require_cuda()
    if isinstance(training_batch, _StreamingResidualTrainingBatch):
        training_batch._validate()
        return _fit_streaming_batch(
            model,
            training_batch,
            epochs=epochs,
            batch_size=batch_size,
            training_blocks=training_blocks,
            telemetry=_telemetry,
            gradient_probe=_gradient_probe,
            calibration_batch_size=_calibration_batch_size,
        )
    if not isinstance(training_batch, ResidualTrainingBatch):
        raise TypeError("fit requires an authenticated residual training batch")
    if training_batch._sealed is not _BATCH_SEAL:
        raise TypeError("fit requires a sealed residual training batch")
    values = training_batch.values
    residuals = training_batch.residuals
    target_nodes = training_batch.target_nodes
    target_mask = training_batch.target_mask
    value_mask = training_batch.value_mask
    availability_mask = training_batch.availability_mask
    node_mask = training_batch.node_mask
    if values.device.type != "cuda" or residuals.device.type != "cuda":
        raise ValueError("training tensors must already be on the required CUDA device")
    if target_nodes.device.type != "cuda" or target_mask.device.type != "cuda":
        raise ValueError("target indices and masks must already be on the required CUDA device")
    instrument_counts = training_batch.instrument_counts
    row_weights = training_batch.row_weights
    if (
        not isinstance(instrument_counts, torch.Tensor)
        or not isinstance(row_weights, torch.Tensor)
        or instrument_counts.device.type != "cuda"
        or row_weights.device.type != "cuda"
    ):
        raise ValueError("global instrument weights must already be on the required CUDA device")
    if (
        training_batch.mode == "SMOKE"
        and training_batch.input_identity != RESIDUAL_FOUNDATION_IDENTITY
    ):
        raise ValueError("smoke training input identity is not canonical")
    if training_batch.mode == "PRIMARY" and (
        not isinstance(training_batch.input_identity, str)
        or len(training_batch.input_identity) != 64
    ):
        raise ValueError("primary training input identity must be a content digest")
    if training_batch.content_identity != _expected_batch_identity(training_batch):
        raise ValueError("residual training batch content digest mismatch")
    if not bool(torch.isfinite(values).all()) or not bool(torch.isfinite(residuals).all()):
        raise ValueError("training values and residual targets must be finite")
    target_values = residuals.reshape(-1)
    target_indices = _normalise_target_nodes(target_nodes, values.shape[0], device)
    active_indices: torch.Tensor | None = None
    if training_blocks is not None:
        if training_batch.training_blocks is None:
            raise ValueError("stage-specific training requires block lineage")
        requested_blocks = tuple(training_blocks)
        if not requested_blocks or any(
            block not in DEVELOPMENT_BLOCKS for block in requested_blocks
        ):
            raise ValueError("training blocks must be canonical development stages")
        active = [
            index
            for index, block in enumerate(training_batch.training_blocks)
            if block in requested_blocks
        ]
        if not active:
            raise ValueError("stage-specific training has no eligible residual rows")
        active_indices = torch.as_tensor(active, dtype=torch.long, device=device)
        values = values.index_select(0, active_indices)
        residuals = residuals.index_select(0, active_indices)
        target_values = target_values.index_select(0, active_indices)
        target_indices = target_indices.index_select(0, active_indices)
        value_mask = training_batch.value_mask.index_select(0, active_indices)
        availability_mask = training_batch.availability_mask.index_select(0, active_indices)
        node_mask = training_batch.node_mask.index_select(0, active_indices)
        row_weights = row_weights.index_select(0, active_indices)
    if torch.unique(target_indices).numel() != len(ALL_INSTRUMENTS):
        raise ValueError("training batch must cover every canonical instrument")
    model = model.to(device)
    config = model.training_config
    optimizer = torch.optim.Adam(
        model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay
    )
    epoch_count = epochs if epochs is not None else config.epochs
    batch_width = batch_size if batch_size is not None else config.batch_size
    if epoch_count != config.epochs or batch_width != config.batch_size:
        raise ValueError("runtime training must use frozen epochs and batch size")
    model.train()
    torch.cuda.synchronize(device)
    started = time.monotonic()
    for _ in range(epoch_count):
        optimizer.zero_grad(set_to_none=True)
        for offset in range(0, values.shape[0], batch_width):
            end = offset + batch_width
            batch_indices = target_indices[offset:end]
            prediction = model(
                values[offset:end],
                target_node=batch_indices,
                value_mask=value_mask[offset:end],
                availability_mask=availability_mask[offset:end],
                node_mask=node_mask[offset:end],
            )
            errors = (prediction - target_values[offset:end]).square()
            loss = (errors * row_weights[offset:end]).sum()
            if not bool(torch.isfinite(loss)):
                raise ValueError("non-finite equal-instrument training loss")
            loss.backward()
        optimizer.step()
    torch.cuda.synchronize(device)
    elapsed = time.monotonic() - started
    return {
        "elapsed_seconds": elapsed,
        "parameter_count": model_parameter_count(model),
        "epochs": epoch_count,
        "target_instruments": len(torch.unique(target_indices)),
        "equal_instrument_loss": True,
        "training_batch_identity": training_batch.content_identity,
    }


def _predict_streaming_batch(
    model: ResidualGraphModel,
    prediction_batch: _StreamingPredictionBatch,
    target_node: Any | None,
    *,
    telemetry: RuntimeTelemetry | None = None,
    calibration_batch_size: int | None = None,
) -> Tensor:
    _validate_streaming_model_policy(model)
    _reauthenticate_stream_prediction_batch(prediction_batch)
    batch_width = calibration_batch_size or TIMESTAMP_BATCH_SIZE
    if calibration_batch_size is not None and calibration_batch_size not in {4, 8, 16, 32, 64}:
        raise ValueError("calibration batch size is outside the bounded safe-width set")
    if prediction_batch.batch_size != TIMESTAMP_BATCH_SIZE:
        raise ValueError("runtime prediction batch contract is not frozen")
    expected_target_nodes = tuple(int(value) for value in prediction_batch.target_nodes)
    if target_node is None:
        requested_target_nodes = expected_target_nodes
    elif isinstance(target_node, int):
        if target_node < 0 or target_node >= len(ALL_INSTRUMENTS):
            raise ValueError("target node is outside the canonical universe")
        requested_target_nodes = (target_node,) * len(prediction_batch.row_keys)
    elif isinstance(target_node, torch.Tensor):
        if target_node.ndim != 1 or target_node.shape[0] != len(prediction_batch.row_keys):
            raise ValueError("target-node indices must contain one instrument per prediction row")
        requested_target_nodes = tuple(int(value) for value in target_node.detach().cpu().tolist())
    else:
        requested_target_nodes = tuple(int(value) for value in target_node)
        if len(requested_target_nodes) != len(prediction_batch.row_keys):
            raise ValueError("target-node indices must contain one instrument per prediction row")
    if any(value < 0 or value >= len(ALL_INSTRUMENTS) for value in requested_target_nodes):
        raise ValueError("target node is outside the canonical universe")
    if requested_target_nodes != expected_target_nodes:
        raise ValueError("prediction target nodes must match the authenticated batch")
    outputs: list[Tensor] = []
    started = time.monotonic()
    if telemetry is not None:
        telemetry.rows += len(prediction_batch.row_keys)
        if prediction_batch.cached_arrays is not None:
            telemetry.timestamps += len(np.unique(prediction_batch.cached_arrays["row_timestamp"]))
        else:
            telemetry.timestamps += len(
                {key.rsplit("|", 1)[-1] for key in prediction_batch.row_keys}
            )
        device = require_cuda()
        torch.cuda.reset_peak_memory_stats(device)
    model.eval()
    cuda_buffer = _PinnedCudaDoubleBuffer() if prediction_batch.cached_arrays is not None else None
    all_indices = tuple(range(len(prediction_batch.row_keys)))
    if prediction_batch.cached_arrays is not None:
        chunks = _cached_timestamp_row_chunks(
            prediction_batch.cached_arrays["row_timestamp"],
            all_indices,
            batch_width,
        )
    else:
        chunks = _timestamp_row_chunks(prediction_batch.row_keys, all_indices, batch_width)
    if prediction_batch.cached_arrays is not None:
        assert cuda_buffer is not None
        work = _prefetched_cached_chunks(
            prediction_batch.cached_arrays, chunks, cuda_buffer, telemetry
        )
    else:
        work = ((selected, None) for selected in chunks)
    with torch.no_grad():
        for selected, prefetched_arrays in work:
            materialisation_started = time.monotonic()
            if prediction_batch.cached_arrays is not None:
                assert prefetched_arrays is not None
                (
                    values,
                    value_mask,
                    availability_mask,
                    node_mask,
                    target_nodes,
                    target_batch,
                ) = prefetched_arrays
                target_batch_positions = []
            else:
                if prediction_batch.preprocessor is None:
                    raise TypeError("streaming prediction preprocessor provenance is missing")
                selected_keys = tuple(prediction_batch.row_keys[index] for index in selected)
                cache: dict[str, MaskedTensor] = {}
                unique_timestamps: list[str] = []
                timestamp_positions: dict[str, int] = {}
                target_batch_positions = []
                for key in selected_keys:
                    timestamp = key.rsplit("|", 1)[-1]
                    position = timestamp_positions.get(timestamp)
                    if position is None:
                        position = len(unique_timestamps)
                        timestamp_positions[timestamp] = position
                        unique_timestamps.append(timestamp)
                    target_batch_positions.append(position)
                preprocessing_started = time.monotonic()
                tensors = [
                    _stream_provider_tensor(prediction_batch.tensors, timestamp, cache)
                    for timestamp in unique_timestamps
                ]
                values, value_mask, availability_mask, node_mask = _stack_preprocessed(
                    tensors, prediction_batch.preprocessor
                )
                if telemetry is not None:
                    telemetry.preprocessing_calls += len(unique_timestamps)
                    telemetry.preprocessing_seconds += time.monotonic() - preprocessing_started
            if prediction_batch.cached_arrays is None:
                target_nodes = torch.as_tensor(
                    [requested_target_nodes[index] for index in selected],
                    dtype=torch.long,
                    device=values.device,
                )
                target_batch = torch.as_tensor(
                    target_batch_positions, dtype=torch.long, device=values.device
                )
            if telemetry is not None:
                telemetry.materialisation_calls += 1
                telemetry.materialisation_seconds += time.monotonic() - materialisation_started
                telemetry.forward_calls += 1
                telemetry.timestamp_batch_calls += 1
                telemetry.representation_timestamp_calls += int(values.shape[0])
                if model.spec.graph_mode == "none":
                    telemetry.local_vectorised_calls += 1
                else:
                    telemetry.shared_representation_calls += 1
            if telemetry is not None:
                forward_started = torch.cuda.Event(enable_timing=True)
                forward_finished = torch.cuda.Event(enable_timing=True)
                forward_started.record()
            prediction = model.forward_grouped(
                values,
                target_node=target_nodes,
                target_batch=target_batch,
                value_mask=value_mask,
                availability_mask=availability_mask,
                node_mask=node_mask,
            )
            if telemetry is not None:
                forward_finished.record()
                telemetry.record_cuda_timing("forward", forward_started, forward_finished)
            if not bool(torch.isfinite(prediction).all()):
                raise ValueError("non-finite residual prediction")
            outputs.append(prediction)
    if telemetry is not None:
        telemetry.finish(require_cuda(), started)
    return torch.cat(outputs, dim=0)


def predict_residual(
    model: ResidualGraphModel,
    prediction_batch: PredictionBatch | _StreamingPredictionBatch,
    *,
    target_node: Any | None = None,
    _telemetry: RuntimeTelemetry | None = None,
    _calibration_batch_size: int | None = None,
) -> Tensor:
    """Predict residuals from a sealed, outcome-blind prediction batch."""
    require_cuda()
    if isinstance(prediction_batch, _StreamingPredictionBatch):
        prediction_batch._validate()
        return _predict_streaming_batch(
            model,
            prediction_batch,
            target_node,
            telemetry=_telemetry,
            calibration_batch_size=_calibration_batch_size,
        )
    if not isinstance(prediction_batch, PredictionBatch):
        raise TypeError("prediction requires an authenticated outcome-blind prediction batch")
    if prediction_batch._sealed is not _PREDICTION_SEAL:
        raise TypeError("prediction requires a sealed prediction batch")
    values = prediction_batch.values
    if values.device.type != "cuda":
        raise ValueError("prediction tensor must be on the required CUDA device")
    if target_node is None:
        target_nodes = prediction_batch.target_nodes
    else:
        target_nodes = _normalise_target_nodes(target_node, values.shape[0], values.device)
        if not torch.equal(target_nodes, prediction_batch.target_nodes):
            raise ValueError("prediction target nodes must match the authenticated batch")
    if (
        prediction_batch.instrument_counts.device.type != "cuda"
        or prediction_batch.row_weights.device.type != "cuda"
    ):
        raise ValueError("prediction weights must already be on the required CUDA device")
    if prediction_batch.content_identity != _expected_batch_identity(prediction_batch):
        raise ValueError("prediction batch content digest mismatch")
    if (
        prediction_batch.mode == "SMOKE"
        and prediction_batch.input_identity != RESIDUAL_FOUNDATION_IDENTITY
    ):
        raise ValueError("smoke prediction input identity is not canonical")
    if prediction_batch.mode == "PRIMARY" and (
        not isinstance(prediction_batch.input_identity, str)
        or len(prediction_batch.input_identity) != 64
    ):
        raise ValueError("primary prediction input identity must be a content digest")
    model.eval()
    with torch.no_grad():
        prediction = model(
            values,
            target_node=target_nodes,
            value_mask=prediction_batch.value_mask,
            availability_mask=prediction_batch.availability_mask,
            node_mask=prediction_batch.node_mask,
        )
    if not bool(torch.isfinite(prediction).all()):
        raise ValueError("non-finite residual prediction")
    return prediction


def apply_residual_correction(local_ridge_forecast: Any, residual_prediction: Any) -> Any:
    """Add a predicted residual to the exact causal LOCAL_RIDGE forecast."""
    if torch is not None and isinstance(local_ridge_forecast, torch.Tensor):
        if not isinstance(residual_prediction, torch.Tensor):
            raise TypeError("tensor forecast requires tensor residual prediction")
        if local_ridge_forecast.shape != residual_prediction.shape:
            raise ValueError("forecast and residual prediction shapes must match")
        corrected = local_ridge_forecast + residual_prediction
        if not bool(torch.isfinite(corrected).all()):
            raise ValueError("corrected forecast must be finite")
        return corrected
    forecast = np.asarray(local_ridge_forecast, dtype=float)
    residual = np.asarray(residual_prediction, dtype=float)
    if forecast.shape != residual.shape:
        raise ValueError("forecast and residual prediction shapes must match")
    corrected = forecast + residual
    if not np.isfinite(corrected).all():
        raise ValueError("corrected forecast must be finite")
    return corrected


def _safe_artifact_component(value: str, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or Path(value).is_absolute()
        or "/" in value
        or "\\" in value
    ):
        raise ValueError(f"{field} must be one safe path component")
    return value


class CreateOnlyArtifacts:
    """Create model, prediction and metric artefacts once; retries receive new paths."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()

    def create_json(
        self, kind: str, slot_id: str, payload: Mapping[str, Any], *, attempt: int = 0
    ) -> Path:
        if kind not in {"model", "prediction", "metric", "result", "config", "support"}:
            raise ValueError("unsupported create-only artefact kind")
        safe_kind = _safe_artifact_component(kind, field="artifact kind")
        safe_slot = _safe_artifact_component(slot_id, field="artifact slot")
        if attempt not in {0, 1}:
            raise ValueError("attempt must be 0 or 1")
        suffix = "" if attempt == 0 else f"_attempt{attempt}"
        path = (self.root / safe_kind / f"{safe_slot.replace(':', '_')}{suffix}.json").resolve()
        if self.root not in path.parents or path.parent != (self.root / safe_kind).resolve():
            raise ValueError("artifact path escapes the create-only root")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as handle:
            handle.write(_canonical(dict(payload)))
            handle.flush()
            os.fsync(handle.fileno())
        return path

    def create_bytes(
        self, kind: str, slot_id: str, payload: bytes, *, suffix: str = ".bin", attempt: int = 0
    ) -> Path:
        if kind not in {"model", "prediction", "metric", "result", "config", "support"}:
            raise ValueError("unsupported create-only artefact kind")
        safe_kind = _safe_artifact_component(kind, field="artifact kind")
        safe_slot = _safe_artifact_component(slot_id, field="artifact slot")
        safe_suffix = _safe_artifact_component(suffix, field="artifact suffix")
        if not safe_suffix.startswith("."):
            raise ValueError("artifact suffix must be an extension")
        if attempt not in {0, 1}:
            raise ValueError("attempt must be 0 or 1")
        name_suffix = "" if attempt == 0 else f"_attempt{attempt}"
        path = (
            self.root / safe_kind / f"{safe_slot.replace(':', '_')}{name_suffix}{safe_suffix}"
        ).resolve()
        if self.root not in path.parents or path.parent != (self.root / safe_kind).resolve():
            raise ValueError("artifact path escapes the create-only root")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        return path


@dataclass(frozen=True)
class ResourceProjection:
    observed_elapsed_seconds: float
    observed_peak_vram_bytes: int | None
    observed_peak_ram_bytes: int | None
    observed_rows: int
    observed_partitions: int
    observed_output_bytes: int
    accepted_support_rows: int = EXPECTED_COUNTS["lab_s_support"]["development"]
    accepted_support_partitions: int = 1
    observed_fit_slots: int = len(FITTED_FAMILY_IDS)
    calibration_rows: int | None = None
    calibration_elapsed_seconds: float | None = None
    calibration_fit_slots: int | None = None
    slot_count: int = PRIMARY_SLOT_COUNT
    safety_multiplier: float = 1.5

    def __post_init__(self) -> None:
        required_values = (
            self.observed_elapsed_seconds,
            self.observed_rows,
            self.observed_partitions,
            self.observed_output_bytes,
            self.accepted_support_rows,
            self.accepted_support_partitions,
            self.observed_fit_slots,
            self.slot_count,
            self.safety_multiplier,
        )
        optional_values = (self.observed_peak_vram_bytes, self.observed_peak_ram_bytes)
        if not all(
            bool(np.isfinite(value))
            for value in (*required_values, *optional_values)
            if value is not None
        ):
            raise ValueError("resource observations and support cardinalities must be finite")
        if (
            self.observed_elapsed_seconds <= 0
            or self.observed_rows <= 0
            or self.observed_partitions <= 0
            or self.observed_output_bytes < 0
            or self.accepted_support_rows <= 0
            or self.accepted_support_partitions <= 0
            or self.observed_fit_slots <= 0
            or any(value < 0 for value in optional_values if value is not None)
        ):
            raise ValueError("resource observations and support cardinalities must be positive")
        if self.slot_count != PRIMARY_SLOT_COUNT:
            raise ValueError("R4-P0 projection is fixed to 45 primary slots")
        if self.observed_fit_slots != len(FITTED_FAMILY_IDS):
            raise ValueError("resource observations must cover all five fitted families")
        if self.safety_multiplier < 1.0:
            raise ValueError("safety multiplier must be conservative")
        if self.calibration_rows is None and (
            self.calibration_elapsed_seconds is not None or self.calibration_fit_slots is not None
        ):
            raise ValueError("calibration rows are required with calibration timing")
        if self.calibration_rows is not None and (
            not bool(np.isfinite(self.calibration_rows))
            or self.calibration_rows <= 0
            or self.calibration_elapsed_seconds is None
            or not bool(np.isfinite(self.calibration_elapsed_seconds))
            or self.calibration_elapsed_seconds <= 0
            or self.calibration_fit_slots is None
            or not bool(np.isfinite(self.calibration_fit_slots))
            or self.calibration_fit_slots != len(FITTED_FAMILY_IDS)
        ):
            raise ValueError("calibration timing must include positive rows and fit slots")

    @property
    def elapsed_seconds(self) -> float:
        if self.calibration_rows is not None:
            calibration_elapsed = self.calibration_elapsed_seconds
            calibration_slots = self.calibration_fit_slots
            if calibration_elapsed is None or calibration_slots is None:
                raise ValueError("calibration timing is required for projected elapsed time")
            per_slot_row = calibration_elapsed / calibration_slots
            rows_per_slot = self.accepted_support_rows / self.calibration_rows
            return per_slot_row * rows_per_slot * self.slot_count * self.safety_multiplier
        return (
            self.observed_elapsed_seconds
            / self.observed_fit_slots
            / self.observed_rows
            * self.accepted_support_rows
            * self.slot_count
            * self.safety_multiplier
        )

    @property
    def peak_vram_bytes(self) -> int | None:
        return (
            None
            if self.observed_peak_vram_bytes is None
            else int(self.observed_peak_vram_bytes * self.safety_multiplier)
        )

    @property
    def peak_ram_bytes(self) -> int | None:
        return (
            None
            if self.observed_peak_ram_bytes is None
            else int(self.observed_peak_ram_bytes * self.safety_multiplier)
        )

    @property
    def rows(self) -> int:
        return self.accepted_support_rows * self.slot_count

    @property
    def partitions(self) -> int:
        return self.accepted_support_partitions * self.slot_count

    @property
    def output_bytes(self) -> int:
        per_slot_row = self.observed_output_bytes / self.observed_fit_slots / self.observed_rows
        return int(
            per_slot_row * self.accepted_support_rows * self.slot_count * self.safety_multiplier
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {
            "projected_elapsed_seconds": self.elapsed_seconds,
            "projected_peak_vram_bytes": self.peak_vram_bytes,
            "projected_peak_ram_bytes": self.peak_ram_bytes,
            "projected_rows": self.rows,
            "projected_partitions": self.partitions,
            "projected_output_bytes": self.output_bytes,
        }


def project_resources(**kwargs: Any) -> dict[str, Any]:
    return ResourceProjection(**kwargs).to_dict()


def smoke_environment() -> dict[str, Any]:
    """Return device evidence without running a scientific fit."""
    device = require_cuda()
    return environment_identity() | {"resolved_device": str(device)}


__all__ = [
    "CHRONOLOGY_IDENTITY",
    "CLOSURE_IDENTITY",
    "DEVICE_REQUIRED",
    "FAMILY_IDS",
    "FAMILY_REGISTER",
    "FAMILY_REGISTER_SHA256",
    "FITTED_FAMILY_IDS",
    "MANIFEST_IDENTITY",
    "MAX_RETRY_BUDGET",
    "PRIMARY_SCHEDULE",
    "PRIMARY_SEEDS",
    "PRIMARY_SLOT_COUNT",
    "PRIMARY_STAGES",
    "REGISTER_IDENTITY",
    "RESIDUAL_FOUNDATION_IDENTITY",
    "SUPPORT_IDENTITY",
    "CreateOnlyArtifacts",
    "FamilySpec",
    "FrozenRuntimeConfig",
    "R4P0Config",
    "ResidualGraphModel",
    "ResidualTrainingBatch",
    "ResourceProjection",
    "TrainingConfig",
    "apply_residual_correction",
    "architecture_signature",
    "build_family_model",
    "environment_identity",
    "family_spec",
    "fit_one_model",
    "frozen_runtime_config",
    "model_parameter_count",
    "predict_residual",
    "project_resources",
    "require_cuda",
    "smoke_environment",
    "tensor_to_cuda",
    "training_preprocessor_identity",
]
