from __future__ import annotations

import hashlib
import json
from copy import copy
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest
import torch

from experiments.r4_residual_graph.graph import (
    build_fixed_economic_graph,
    shuffle_economic_graph,
)
from experiments.r4_residual_graph.runtime import (
    CHRONOLOGY_IDENTITY,
    CLOSURE_IDENTITY,
    DEVICE_REQUIRED,
    FAMILY_REGISTER,
    FITTED_FAMILY_IDS,
    MANIFEST_IDENTITY,
    PRIMARY_SCHEDULE,
    PRIMARY_SEEDS,
    PRIMARY_SLOT_COUNT,
    RESIDUAL_FOUNDATION_IDENTITY,
    SUPPORT_IDENTITY,
    CreateOnlyArtifacts,
    FrozenRuntimeConfig,
    PredictionBatch,
    ResidualGraphModel,
    ResidualTrainingBatch,
    ResourceProjection,
    apply_residual_correction,
    build_family_model,
    fit_one_model,
    predict_residual,
)
from experiments.r4_residual_graph.tensor import MaskedTensor, TensorContract


def test_closed_register_schedule_and_config_are_frozen() -> None:
    config = FrozenRuntimeConfig()
    assert len(FAMILY_REGISTER) == 8
    assert len(FITTED_FAMILY_IDS) == 5
    assert len(PRIMARY_SCHEDULE) == PRIMARY_SLOT_COUNT == 45
    assert len(set(PRIMARY_SCHEDULE)) == PRIMARY_SLOT_COUNT
    assert tuple(config.chronology) == ("DEV_2", "DEV_3", "TERMINAL_FORMER_HOLDOUT")
    payload = config.to_dict()
    assert payload["support_identity"] == SUPPORT_IDENTITY
    assert payload["tensor_contract"]["lookback_minutes"] == 60
    assert payload["manifest_identity"] == MANIFEST_IDENTITY
    assert payload["child_closure_identity"] == CLOSURE_IDENTITY
    assert payload["device"] == DEVICE_REQUIRED
    assert tuple(config.seeds) == PRIMARY_SEEDS


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tensor_identity", "tampered"),
        ("fixed_graph_identity", "tampered"),
        ("shuffled_graph_identity", "tampered"),
        ("support_identity", "tampered"),
        ("child_closure_identity", "tampered"),
        ("device", "cpu"),
    ],
)
def test_frozen_config_rejects_unsupported_authority_values(field: str, value: str) -> None:
    with pytest.raises(ValueError):
        cast(Any, FrozenRuntimeConfig)(**{field: value})


def test_create_only_artifacts_reopen_and_hash_without_rewrite(tmp_path: Path) -> None:
    artifacts = CreateOnlyArtifacts(tmp_path)
    result = artifacts.create_json("result", "slot", {"mode": "SMOKE"})
    support = artifacts.create_json("support", "slot", {"identity": SUPPORT_IDENTITY})
    assert json.loads(result.read_text(encoding="utf-8"))["mode"] == "SMOKE"
    assert json.loads(support.read_text(encoding="utf-8"))["identity"] == SUPPORT_IDENTITY
    assert hashlib.sha256(result.read_bytes()).hexdigest()
    with pytest.raises(FileExistsError):
        artifacts.create_json("result", "slot", {"mode": "SMOKE"})
    retry = artifacts.create_json("result", "slot", {"mode": "SMOKE"}, attempt=1)
    assert retry != result and retry.exists()
    with pytest.raises(ValueError, match="attempt must be 0 or 1"):
        artifacts.create_json("result", "slot", {"mode": "SMOKE"}, attempt=2)
    with pytest.raises(ValueError, match="attempt must be 0 or 1"):
        artifacts.create_bytes("model", "slot", b"data", attempt=2)


def test_graph_models_require_authenticated_fixed_and_shuffled_graphs() -> None:
    fixed = build_fixed_economic_graph()
    shuffled = shuffle_economic_graph(fixed)
    with pytest.raises(ValueError, match="authenticated canonical graph"):
        build_family_model(
            "FIXED_ECONOMIC_GRAPH_RESIDUAL", fixed_graph=replace(fixed, identity="bad")
        )
    with pytest.raises(ValueError, match="authenticated canonical permutation"):
        build_family_model(
            "SHUFFLED_FIXED_GRAPH_RESIDUAL", shuffled_graph=replace(shuffled, identity="bad")
        )


@pytest.mark.parametrize(
    ("family_id", "layers", "width", "parameters"),
    [
        ("LOCAL_TEMPORAL_RESIDUAL", 0, 0, 7713),
        ("POOLED_NON_GRAPH_RESIDUAL", 0, 32, 8801),
        ("FIXED_ECONOMIC_GRAPH_RESIDUAL", 1, 32, 8801),
        ("LEARNED_STATIC_GRAPH_RESIDUAL", 1, 32, 9201),
        ("SHUFFLED_FIXED_GRAPH_RESIDUAL", 1, 32, 8801),
    ],
)
def test_five_family_architecture_contract(
    family_id: str, layers: int, width: int, parameters: int
) -> None:
    model = cast(Any, build_family_model(family_id))
    architecture = model.architecture()
    assert architecture["message_passing_layers"] == layers
    assert architecture["message_width"] == width
    assert architecture["lstm"]["hidden_size"] == 32
    assert sum(parameter.numel() for parameter in model.parameters()) == parameters
    if family_id == "POOLED_NON_GRAPH_RESIDUAL":
        assert isinstance(model.message, torch.nn.Linear)
        assert model.message.weight.shape == (32, 32)
        assert model.adjacency_matrix() is None


def test_target_head_and_local_input_are_explicit() -> None:
    model = build_family_model("LOCAL_TEMPORAL_RESIDUAL")
    architecture = model.architecture()  # type: ignore[reportAttributeAccessIssue]
    assert architecture["lstm"]["num_layers"] == 1
    assert architecture["target_head"] == "TARGET_NODE_RESIDUAL"
    values = torch.zeros((1, 61, 20, 26), dtype=torch.float32)
    with pytest.raises(ValueError, match="requires target_node"):
        model(values)  # type: ignore[reportCallIssue]
    baseline = model(values, target_node=0)  # type: ignore[reportCallIssue]
    values[:, :, 1, :] = 1.0
    assert torch.equal(  # type: ignore[reportCallIssue]
        baseline,
        model(values, target_node=0),  # type: ignore[reportCallIssue]
    )
    assert model(values, target_node=0).shape == (1,)  # type: ignore[reportCallIssue]


@pytest.mark.parametrize(
    "family_id",
    (
        "POOLED_NON_GRAPH_RESIDUAL",
        "FIXED_ECONOMIC_GRAPH_RESIDUAL",
        "LEARNED_STATIC_GRAPH_RESIDUAL",
        "SHUFFLED_FIXED_GRAPH_RESIDUAL",
    ),
)
def test_graph_family_forward_returns_target_node_scalars(family_id: str) -> None:
    model = build_family_model(family_id)
    values = torch.zeros((2, 61, 20, 26), dtype=torch.float32)
    output = model(  # type: ignore[reportCallIssue]
        values, target_node=torch.tensor([0, 1])
    )
    assert output.shape == (2,)
    assert bool(torch.isfinite(output).all())


def test_nonfinite_tensor_is_rejected_before_cuda_conversion() -> None:
    if not torch.cuda.is_available():
        cast(Any, pytest.skip)("CUDA required by R4-P0")
    contract = TensorContract()
    values = np.zeros((61, 20, 26), dtype=float)
    values[0, 0, 0] = np.inf
    mask = np.zeros_like(values, dtype=bool)
    mask[0, 0, 0] = True
    nodes = np.zeros((61, 20), dtype=bool)
    nodes[0, 0] = True
    with pytest.raises(ValueError, match="non-finite"):
        MaskedTensor(
            values,
            mask,
            mask.copy(),
            nodes,
            datetime(2026, 7, 1, tzinfo=UTC),
            contract.identity,
            contract,
        )


def test_residual_correction_is_shape_and_finiteness_checked() -> None:
    corrected = apply_residual_correction(np.array([1.0]), np.array([0.25]))
    assert corrected.tolist() == [1.25]
    with pytest.raises(ValueError, match="shapes"):
        apply_residual_correction(np.array([1.0]), np.array([0.1, 0.2]))
    with pytest.raises(ValueError, match="finite"):
        apply_residual_correction(np.array([1.0]), np.array([np.inf]))


def test_authenticated_residual_and_chronology_identities_are_stable() -> None:
    assert len(RESIDUAL_FOUNDATION_IDENTITY) == 64
    assert len(CHRONOLOGY_IDENTITY) == 64
    assert SUPPORT_IDENTITY == "R4-C-SUPPORT-POLICY-V1"


def test_mixed_target_vector_and_training_batch_digest_are_bound() -> None:
    values = torch.zeros((20, 61, 20, 26), dtype=torch.float32)
    target_nodes = torch.arange(20, dtype=torch.long)
    model = build_family_model("LOCAL_TEMPORAL_RESIDUAL")
    output = model(values, target_node=target_nodes)  # type: ignore[reportCallIssue]
    assert output.shape == (20,)
    batch = ResidualTrainingBatch.from_smoke(
        values=values,
        residuals=torch.zeros(20),
        target_nodes=target_nodes,
        target_mask=torch.ones(20, dtype=torch.bool),
        preprocessor_identity="a" * 64,
    )
    assert len(batch.content_identity) == 64
    object.__setattr__(batch, "content_identity", "b" * 64)
    with pytest.raises(ValueError, match="digest"):
        batch._validate()
    with pytest.raises(TypeError, match="authenticated factory"):
        ResidualTrainingBatch(
            values=values,
            residuals=torch.zeros(20),
            target_nodes=target_nodes,
            target_mask=torch.ones(20, dtype=torch.bool),
            input_identity="tampered",
            support_identity=SUPPORT_IDENTITY,
            chronology_identity=CHRONOLOGY_IDENTITY,
            preprocessor_identity="a" * 64,
        )


def test_resource_projection_uses_accepted_support_cardinality() -> None:
    projection = ResourceProjection(
        observed_elapsed_seconds=1.0,
        observed_peak_vram_bytes=10,
        observed_peak_ram_bytes=20,
        observed_rows=20,
        observed_partitions=1,
        observed_output_bytes=200,
    )
    assert projection.rows == 771_140 * PRIMARY_SLOT_COUNT
    assert projection.partitions == PRIMARY_SLOT_COUNT
    assert projection.to_dict()["accepted_support_rows"] == 771_140


def test_resource_projection_calibration_basis_is_explicit() -> None:
    projection = ResourceProjection(
        observed_elapsed_seconds=1.0,
        observed_peak_vram_bytes=10,
        observed_peak_ram_bytes=20,
        observed_rows=20,
        observed_partitions=1,
        observed_output_bytes=200,
        calibration_rows=100,
        calibration_elapsed_seconds=10.0,
        calibration_fit_slots=5,
    )
    expected = (10.0 / 5.0) * (771_140 / 100.0) * 45 * 1.5
    assert projection.elapsed_seconds == pytest.approx(expected)


@pytest.mark.parametrize(
    ("observed_elapsed_seconds", "observed_peak_vram_bytes", "safety_multiplier"),
    [
        (float("nan"), 10, 1.5),
        (float("inf"), 10, 1.5),
        (1.0, -1, 1.5),
        (1.0, 10, float("nan")),
    ],
)
def test_resource_projection_rejects_nonfinite_or_negative_observations(
    observed_elapsed_seconds: float, observed_peak_vram_bytes: int, safety_multiplier: float
) -> None:
    with pytest.raises(ValueError):
        ResourceProjection(
            observed_elapsed_seconds=observed_elapsed_seconds,
            observed_peak_vram_bytes=observed_peak_vram_bytes,
            observed_peak_ram_bytes=20,
            observed_rows=20,
            observed_partitions=1,
            observed_output_bytes=200,
            safety_multiplier=safety_multiplier,
        )
    with pytest.raises(ValueError):
        ResourceProjection(
            observed_elapsed_seconds=1.0,
            observed_peak_vram_bytes=10,
            observed_peak_ram_bytes=20,
            observed_rows=20,
            observed_partitions=1,
            observed_output_bytes=200,
            calibration_rows=100,
            calibration_elapsed_seconds=float("inf"),
            calibration_fit_slots=5,
        )


def test_resource_projection_requires_all_fitted_slots() -> None:
    with pytest.raises(ValueError, match="all five fitted families"):
        ResourceProjection(
            observed_elapsed_seconds=1.0,
            observed_peak_vram_bytes=10,
            observed_peak_ram_bytes=20,
            observed_rows=20,
            observed_partitions=1,
            observed_output_bytes=200,
            observed_fit_slots=4,
        )


def test_global_instrument_weights_are_order_and_batch_invariant() -> None:
    target_nodes = torch.tensor([0, 0, *range(1, 20)], dtype=torch.long)
    rows = target_nodes.shape[0]
    batch = ResidualTrainingBatch.from_smoke(
        values=torch.zeros((rows, 61, 20, 26), dtype=torch.float32),
        residuals=torch.arange(1, rows + 1, dtype=torch.float32),
        target_nodes=target_nodes,
        target_mask=torch.ones(rows, dtype=torch.bool),
        preprocessor_identity="a" * 64,
    )
    errors = batch.residuals.square()
    full_objective = (errors * batch.row_weights).sum()
    split_objective = sum(
        (errors[start : start + 4] * batch.row_weights[start : start + 4]).sum()
        for start in range(0, rows, 4)
    )
    permutation = torch.tensor([*range(2, rows), 0, 1], dtype=torch.long)
    reordered = ResidualTrainingBatch.from_smoke(
        values=batch.values[permutation],
        residuals=batch.residuals[permutation],
        target_nodes=batch.target_nodes[permutation],
        target_mask=batch.target_mask[permutation],
        preprocessor_identity="a" * 64,
    )
    reordered_objective = (reordered.residuals.square() * reordered.row_weights).sum()
    assert batch.instrument_counts[0].item() == 2
    assert batch.row_weights[0].item() == pytest.approx(1 / 40)
    assert split_objective == pytest.approx(full_objective.item())
    assert reordered_objective == pytest.approx(full_objective.item())


def test_streaming_dispatch_rejects_mutable_model_training_policy() -> None:
    import experiments.r4_residual_graph.runtime as runtime_module

    model = build_family_model("LOCAL_TEMPORAL_RESIDUAL")
    model.__dict__["training_config"] = replace(model.__dict__["training_config"], epochs=2)
    with pytest.raises(ValueError, match="canonical training configuration"):
        runtime_module._validate_streaming_model_policy(model)


def test_model_constructor_cannot_bypass_frozen_contract() -> None:
    contract = TensorContract()
    tampered_contract = replace(contract, lookback_minutes=59)
    with pytest.raises(ValueError, match="canonical tensor contract"):
        ResidualGraphModel("LOCAL_TEMPORAL_RESIDUAL", contract=tampered_contract)


def test_prediction_rejects_bare_unbound_tensor() -> None:
    if not torch.cuda.is_available():
        cast(Any, pytest.skip)("CUDA required by R4-P0")
    model = build_family_model("LOCAL_TEMPORAL_RESIDUAL")
    values = torch.zeros((20, 61, 20, 26), device="cuda")
    with pytest.raises(TypeError, match="outcome-blind prediction batch"):
        predict_residual(model, cast(Any, values), target_node=0)


def test_graph_models_reject_matrix_tampering_with_accepted_identity() -> None:
    fixed = build_fixed_economic_graph()
    fixed_matrix = fixed.normalized_adjacency.copy()
    fixed_matrix[0, 0] += 0.01
    with pytest.raises(ValueError, match="authenticated canonical graph"):
        build_family_model(
            "FIXED_ECONOMIC_GRAPH_RESIDUAL",
            fixed_graph=replace(fixed, normalized_adjacency=fixed_matrix),
        )
    shuffled = shuffle_economic_graph(fixed)
    shuffled_matrix = shuffled.adjacency.copy()
    shuffled_matrix[0, 1] += 0.01
    with pytest.raises(ValueError, match="authenticated canonical graph"):
        build_family_model(
            "SHUFFLED_FIXED_GRAPH_RESIDUAL",
            shuffled_graph=replace(shuffled, adjacency=shuffled_matrix),
        )


def test_frozen_config_rejects_mutable_authority_sequences() -> None:
    with pytest.raises(ValueError, match="immutable instrument universe"):
        FrozenRuntimeConfig(universe=cast(Any, list(FrozenRuntimeConfig().universe)))
    with pytest.raises(ValueError, match="chronology"):
        FrozenRuntimeConfig(chronology=cast(Any, list(FrozenRuntimeConfig().chronology)))


def test_create_only_artifacts_reject_path_escape_without_writes(tmp_path: Path) -> None:
    artifacts = CreateOnlyArtifacts(tmp_path / "root")
    for bad_slot in ("../../escaped", "/tmp/absolute", ".", "..", "..\\\\escaped"):
        with pytest.raises(ValueError):
            artifacts.create_json("result", bad_slot, {"mode": "SMOKE"})
        with pytest.raises(ValueError):
            artifacts.create_bytes("model", bad_slot, b"data")
    assert not (tmp_path / "escaped").exists()
    assert not (tmp_path / "root").exists()


def test_streaming_training_chunks_preserve_order_and_bound_live_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import experiments.r4_residual_graph.runtime as runtime_module

    provider_calls: list[str] = []
    stack_sizes: list[int] = []

    def provider(_tensors: object, timestamp: str, _cache: dict[str, object]) -> object:
        provider_calls.append(timestamp)
        return object()

    def stack(tensors: list[object], _preprocessor: object) -> tuple[torch.Tensor, ...]:
        stack_sizes.append(len(tensors))
        values = torch.zeros((len(tensors), 1, 1, 1))
        masks = torch.ones_like(values, dtype=torch.bool)
        nodes = torch.ones((len(tensors), 1, 1), dtype=torch.bool)
        return values, masks, masks, nodes

    monkeypatch.setattr(runtime_module, "_stream_provider_tensor", provider)
    monkeypatch.setattr(runtime_module, "_stack_preprocessed", stack)
    row_keys = tuple("asset|2026-01-01T00:0" + str(index) + ":00+00:00" for index in range(7))
    batch = runtime_module._StreamingResidualTrainingBatch(
        tensors={},
        preprocessor=cast(Any, object()),
        row_keys=row_keys,
        residuals=tuple(float(index) for index in range(7)),
        target_nodes=tuple(index % 20 for index in range(7)),
        training_blocks=tuple("DEV_1" for _ in row_keys),
        input_identity="i",
        support_identity="s",
        chronology_identity="c",
        preprocessor_identity="p",
        content_identity="b",
        _capability=runtime_module._STREAMING_BATCH_CAPABILITY,
    )
    chunks = list(runtime_module._iter_stream_training_chunks(batch, tuple(range(7)), 4))
    assert [int(chunk[0].shape[0]) for chunk in chunks] == [4, 3]
    assert stack_sizes == [4, 3]
    assert len(provider_calls) == 7


def test_streaming_batch_identity_binds_materialisation_policy() -> None:
    import experiments.r4_residual_graph.runtime as runtime_module

    batch = runtime_module._StreamingPredictionBatch(
        tensors={},
        preprocessor=cast(Any, object()),
        row_keys=("asset|2026-01-01T00:00:00+00:00",),
        target_nodes=(0,),
        target_mask=(True,),
        input_identity="i",
        support_identity="s",
        chronology_identity="c",
        preprocessor_identity="p",
        content_identity="",
        _capability=runtime_module._STREAMING_BATCH_CAPABILITY,
    )
    identity = runtime_module._sha256(
        {
            "policy": runtime_module.TIMESTAMP_MATERIALISATION_POLICY,
            "batch_size": batch.batch_size,
            "row_keys": batch.row_keys,
            "target_nodes": batch.target_nodes,
            "input_identity": batch.input_identity,
            "support": batch.support_identity,
            "tensor_identities": batch.provider_tensor_identities,
            "chronology": batch.chronology_identity,
            "preprocessor": batch.preprocessor_identity,
        }
    )
    valid = replace(batch, content_identity=identity)
    valid._validate()
    with pytest.raises(ValueError, match="content digest"):
        replace(valid, content_identity="tampered")._validate()


def test_streaming_batch_rejects_capability_bypass_and_width_tamper() -> None:
    import experiments.r4_residual_graph.runtime as runtime_module

    batch = runtime_module._StreamingPredictionBatch(
        tensors={},
        preprocessor=cast(Any, object()),
        row_keys=("asset|2026-01-01T00:00:00+00:00",),
        target_nodes=(0,),
        target_mask=(True,),
        input_identity="i",
        support_identity="s",
        chronology_identity="c",
        preprocessor_identity="p",
        content_identity="",
        _capability=runtime_module._STREAMING_BATCH_CAPABILITY,
    )
    identity = runtime_module._sha256(
        {
            "policy": runtime_module.TIMESTAMP_MATERIALISATION_POLICY,
            "batch_size": batch.batch_size,
            "row_keys": batch.row_keys,
            "target_nodes": batch.target_nodes,
            "input_identity": batch.input_identity,
            "support": batch.support_identity,
            "tensor_identities": batch.provider_tensor_identities,
            "chronology": batch.chronology_identity,
            "preprocessor": batch.preprocessor_identity,
        }
    )
    valid = replace(batch, content_identity=identity)
    valid._validate()
    with pytest.raises(TypeError, match="capability"):
        replace(valid, _capability=object())
    with pytest.raises(ValueError, match="batch size is frozen"):
        replace(valid, batch_size=8)._validate()


def test_streaming_factories_reject_non_frozen_batch_width() -> None:
    import experiments.r4_residual_graph.runtime as runtime_module

    with pytest.raises(ValueError, match="batch size is frozen"):
        runtime_module._make_stream_prediction_batch(
            {}, cast(Any, object()), cast(Any, object()), "", batch_size=8
        )
    with pytest.raises(ValueError, match="batch size is frozen"):
        runtime_module._make_stream_residual_batch(
            {}, cast(Any, object()), cast(Any, object()), cast(Any, object()), batch_size=8
        )


@pytest.mark.parametrize("support_size", [1, 2, 3])
def test_real_stream_factories_use_ordered_support_lookup(
    monkeypatch: pytest.MonkeyPatch, support_size: int
) -> None:
    import numpy as np
    import polars as pl

    import experiments.r4_residual_graph.runtime as runtime_module
    from experiments.r4_residual_graph.tensor_store import RawTensorStore
    from tests.experiments.r4_residual_graph.test_runtime_authenticated_masks import (
        _support,
        _training_fixture,
    )

    foundation, _, preprocessor, keyed = _training_fixture(False)
    by_time = {tensor.decision_time: tensor for _, tensor in keyed}
    times = tuple(sorted(by_time))[:support_size]
    support = _support(times, by_time)
    tensors = {time.isoformat(): tensor for time, tensor in by_time.items()}
    store = object.__new__(RawTensorStore)
    store._keys = tuple(reversed(tensors))
    store._index = {key: index for index, key in enumerate(store._keys)}
    store._identities = np.array(
        [runtime_module._masked_tensor_identity(tensors[key]) for key in store._keys]
    )
    builds = 0
    original = RawTensorStore.tensor_identities.fget
    assert original is not None

    def complete_map(value: RawTensorStore) -> dict[str, str]:
        nonlocal builds
        builds += 1
        return original(value)

    monkeypatch.setattr(RawTensorStore, "tensor_identities", property(complete_map))
    projected = foundation.rows.filter(pl.col("decision_time").is_in(times))
    capability = runtime_module.validate_residual_foundation(foundation)
    identities = dict(support.tensor_identities)
    input_identity = runtime_module._sha256(
        {
            "support_identity": support.identity,
            "row_keys": support.target_keys,
            "tensor_identities": tuple(
                identities[key.rsplit("|", 1)[-1]] for key in support.target_keys
            ),
            "preprocessor_identity": runtime_module.training_preprocessor_identity(preprocessor),
            "policy": runtime_module.TIMESTAMP_MATERIALISATION_POLICY,
        }
    )

    def training(provider: Any) -> Any:
        return runtime_module._make_stream_residual_batch(
            provider,
            foundation,
            support,
            preprocessor,
            projected_rows=projected,
            foundation_capability=capability,
        )

    def prediction(provider: Any) -> Any:
        return runtime_module._make_stream_prediction_batch(
            provider, support, preprocessor, input_identity
        )

    # Exercise the two real factories twice, as preparation does for its two stages.
    for _ in range(2):
        for factory in (training, prediction):
            actual, reference = factory(store), factory(tensors)
            assert actual.content_identity == reference.content_identity
            assert actual.row_keys == reference.row_keys
            assert actual.provider_tensor_identities == support.tensor_identities
    assert builds == 0
    assert store._support_tensor_identities(tuple(reversed(support.keys))) == tuple(
        reversed(support.tensor_identities)
    )
    first = store.tensor_identities
    first[support.keys[0]] = "changed"
    assert store.tensor_identities[support.keys[0]] == identities[support.keys[0]]
    assert builds == 2
    key = support.keys[0]
    index = store._index.pop(key)
    for factory in (training, prediction):
        with pytest.raises(KeyError):
            factory(store)
    store._index[key] = (index + 1) % len(store._keys)
    for factory in (training, prediction):
        with pytest.raises(ValueError, match="sealed stream provider"):
            factory(store)
    store._index[key] = index
    store._identities[index] = "f" * 64
    for factory in (training, prediction):
        with pytest.raises(ValueError, match="sealed stream provider"):
            factory(store)


def _authenticated_stream_training_fixture() -> tuple[Any, Any]:
    from tests.experiments.r4_residual_graph.test_runtime_authenticated_masks import (
        _training_fixture,
    )

    foundation, support, preprocessor, keyed_tensors = _training_fixture(False)
    tensors = {
        tensor.decision_time.isoformat(): tensor
        for _, tensor in cast(tuple[tuple[str, MaskedTensor], ...], keyed_tensors)
    }
    batch = ResidualTrainingBatch.from_authenticated_oof(
        tensors=tensors,
        foundation=foundation,
        support=support,
        preprocessor=preprocessor,
        stream=True,
    )
    return batch, tensors


def _authenticated_stream_prediction_fixture() -> tuple[Any, dict[str, MaskedTensor]]:
    import experiments.r4_residual_graph.runtime as runtime_module
    from tests.experiments.r4_residual_graph.test_runtime_authenticated_masks import (
        _prediction_fixture,
    )

    support, preprocessor, keyed_tensors, _ = _prediction_fixture(False)
    tensors = {
        tensor.decision_time.isoformat(): tensor
        for _, tensor in cast(tuple[tuple[str, MaskedTensor], ...], keyed_tensors)
    }
    identity_map = dict(support.tensor_identities)
    input_identity = runtime_module._sha256(
        {
            "support_identity": support.identity,
            "row_keys": support.target_keys,
            "tensor_identities": tuple(
                identity_map[key.rsplit("|", 1)[-1]] for key in support.target_keys
            ),
            "preprocessor_identity": runtime_module.training_preprocessor_identity(preprocessor),
            "policy": runtime_module.TIMESTAMP_MATERIALISATION_POLICY,
        }
    )
    batch = PredictionBatch.from_authenticated_support(
        tensors=tensors,
        support=support,
        preprocessor=preprocessor,
        input_identity=input_identity,
        stream=True,
    )
    return batch, tensors


@pytest.mark.parametrize("family_id", ["LOCAL_TEMPORAL_RESIDUAL", "FIXED_ECONOMIC_GRAPH_RESIDUAL"])
def test_authenticated_stream_dispatches_real_fit_and_prediction_in_bounded_chunks(
    monkeypatch: pytest.MonkeyPatch,
    family_id: str,
) -> None:
    if not torch.cuda.is_available():
        cast(Any, pytest.skip)("CUDA required by R4-P0")
    import experiments.r4_residual_graph.runtime as runtime_module

    training_batch, _ = _authenticated_stream_training_fixture()
    prediction_batch, _ = _authenticated_stream_prediction_fixture()
    stack_widths: list[int] = []
    original_stack = runtime_module._stack_preprocessed

    def bounded_stack(tensors: list[MaskedTensor], preprocessor: Any) -> Any:
        stack_widths.append(len(tensors))
        assert len(tensors) <= runtime_module.TIMESTAMP_BATCH_SIZE
        return original_stack(tensors, preprocessor)

    monkeypatch.setattr(runtime_module, "_stack_preprocessed", bounded_stack)
    fit_telemetry = runtime_module.RuntimeTelemetry()
    fit_result = runtime_module.fit_one_model(
        cast(Any, build_family_model(family_id)).to("cuda"),
        training_batch,
        _telemetry=fit_telemetry,
    )
    assert fit_result["epochs"] == 1
    assert stack_widths == [3]
    assert fit_telemetry.materialisation_calls == 1
    assert fit_telemetry.forward_calls == 1
    assert fit_telemetry.h2d_calls < 20 * runtime_module.TIMESTAMP_BATCH_SIZE

    stack_widths.clear()
    prediction_telemetry = runtime_module.RuntimeTelemetry()
    prediction = runtime_module.predict_residual(
        cast(Any, build_family_model(family_id)).to("cuda"),
        prediction_batch,
        _telemetry=prediction_telemetry,
    )
    assert prediction.shape == (len(runtime_module.ALL_INSTRUMENTS),)
    assert stack_widths == [1]
    assert prediction_telemetry.materialisation_calls == 1
    assert prediction_telemetry.forward_calls == 1
    assert prediction_telemetry.h2d_calls < 20 * runtime_module.TIMESTAMP_BATCH_SIZE


@pytest.mark.parametrize(
    ("tamper", "message"),
    (
        ("provider", "provider tensor content"),
        ("preprocessor", "preprocessor identity"),
        ("support", "support identity"),
        ("foundation", "foundation|identity|digest"),
        ("input", "content digest"),
        ("width", "batch size is frozen"),
        ("model_epochs", "canonical training configuration"),
        ("model_optimizer", "canonical training configuration"),
        ("model_learning_rate", "canonical training configuration"),
    ),
)
def test_authenticated_stream_fit_dispatch_reauthenticates_lineage(
    tamper: str, message: str
) -> None:
    if not torch.cuda.is_available():
        cast(Any, pytest.skip)("CUDA required by R4-P0")

    batch, tensors = _authenticated_stream_training_fixture()
    model = cast(Any, build_family_model("LOCAL_TEMPORAL_RESIDUAL")).to("cuda")
    if tamper == "provider":
        key, tensor = next(iter(tensors.items()))
        values = tensor.values.copy()
        values.flat[0] += 1.0
        batch = replace(batch, tensors={**tensors, key: replace(tensor, values=values)})
    elif tamper == "preprocessor":
        clone = copy(batch.preprocessor)
        means = clone.means.copy()
        means[0] += 1.0
        object.__setattr__(clone, "means", means)
        batch = replace(batch, preprocessor=clone)
    elif tamper == "support":
        clone = copy(batch._support)
        object.__setattr__(clone, "source_identity", "f" * 64)
        batch = replace(batch, _support=clone)
    elif tamper == "foundation":
        clone = copy(batch._foundation)
        object.__setattr__(clone, "content_identity", "f" * 64)
        batch = replace(batch, _foundation=clone)
    elif tamper == "input":
        batch = replace(batch, input_identity="a" * 64)
    elif tamper == "width":
        batch = replace(batch, batch_size=8)
    elif tamper == "model_epochs":
        model.__dict__["training_config"] = replace(model.__dict__["training_config"], epochs=2)
    elif tamper == "model_optimizer":
        config = copy(model.__dict__["training_config"])
        object.__setattr__(config, "optimiser", "SGD")
        model.__dict__["training_config"] = config
    elif tamper == "model_learning_rate":
        model.__dict__["training_config"] = replace(
            model.__dict__["training_config"], learning_rate=2e-3
        )
    with pytest.raises((TypeError, ValueError), match=message):
        fit_one_model(model, batch)


@pytest.mark.parametrize(
    ("tamper", "message"),
    (
        ("provider", "provider tensor content"),
        ("preprocessor", "preprocessor identity"),
        ("support", "support identity"),
        ("input", "content digest"),
        ("width", "batch size is frozen"),
        ("model_epochs", "canonical training configuration"),
        ("model_optimizer", "canonical training configuration"),
        ("model_learning_rate", "canonical training configuration"),
    ),
)
def test_authenticated_stream_prediction_dispatch_reauthenticates_lineage(
    tamper: str, message: str
) -> None:
    if not torch.cuda.is_available():
        cast(Any, pytest.skip)("CUDA required by R4-P0")
    batch, tensors = _authenticated_stream_prediction_fixture()
    model = cast(Any, build_family_model("LOCAL_TEMPORAL_RESIDUAL")).to("cuda")
    if tamper == "provider":
        key, tensor = next(iter(tensors.items()))
        values = tensor.values.copy()
        values.flat[0] += 1.0
        batch = replace(batch, tensors={**tensors, key: replace(tensor, values=values)})
    elif tamper == "preprocessor":
        clone = copy(batch.preprocessor)
        means = clone.means.copy()
        means[0] += 1.0
        object.__setattr__(clone, "means", means)
        batch = replace(batch, preprocessor=clone)
    elif tamper == "support":
        clone = copy(batch._support)
        object.__setattr__(clone, "source_identity", "f" * 64)
        batch = replace(batch, _support=clone)
    elif tamper == "input":
        batch = replace(batch, input_identity="a" * 64)
    elif tamper == "width":
        batch = replace(batch, batch_size=8)
    elif tamper == "model_epochs":
        model.__dict__["training_config"] = replace(model.__dict__["training_config"], epochs=2)
    elif tamper == "model_optimizer":
        config = copy(model.__dict__["training_config"])
        object.__setattr__(config, "optimiser", "SGD")
        model.__dict__["training_config"] = config
    elif tamper == "model_learning_rate":
        model.__dict__["training_config"] = replace(
            model.__dict__["training_config"], learning_rate=2e-3
        )
    with pytest.raises((TypeError, ValueError), match=message):
        predict_residual(model, batch)
