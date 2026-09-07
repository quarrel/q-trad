from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import numpy as np
import polars as pl
import pytest
import torch

import experiments.r4_residual_graph.foundation as foundation_module
import experiments.r4_residual_graph.runtime as runtime_module
import experiments.r4_residual_graph.tensor as tensor_module
from experiments.r4_residual_graph.runtime import (
    PredictionBatch,
    ResidualTrainingBatch,
    build_family_model,
    predict_residual,
)
from experiments.r4_residual_graph.tensor import MaskedTensor, TensorContract


def _tensor(decision_time: datetime, inactive_target: bool) -> MaskedTensor:
    contract = TensorContract()
    shape = (contract.lookback_minutes + 1, 20, len(contract.feature_names))
    values = np.zeros(shape, dtype=float)
    node_mask = np.ones(shape[:2], dtype=bool)
    if inactive_target:
        node_mask[-1, 0] = False
    availability_mask = np.broadcast_to(node_mask[..., None], shape).copy()
    return MaskedTensor(
        values=values,
        value_mask=availability_mask.copy(),
        availability_mask=availability_mask,
        node_mask=node_mask,
        decision_time=decision_time,
        contract_identity=contract.identity,
        contract=contract,
    )


def _preprocessor(tensor: MaskedTensor) -> Any:
    contract = TensorContract()
    cutoff = datetime(2026, 6, 20, tzinfo=UTC)
    partition = tensor_module.TrainingTensorPartition(
        tensors=(tensor,),
        row_keys=(f"TRAINING|{tensor.decision_time.isoformat()}",),
        source_identity="a" * 64,
        support_identity="b" * 64,
        training_cutoff=cutoff,
        _seal=tensor_module._TRAINING_PARTITION_SEAL,
    )
    return tensor_module.FittedTrainingPreprocessor._create(
        token=tensor_module._PREPROCESSOR_SEAL,
        means=np.zeros(len(contract.feature_names), dtype=float),
        scales=np.ones(len(contract.feature_names), dtype=float),
        feature_names=contract.feature_names,
        training_cutoff=cutoff,
        contract_identity=contract.identity,
        training_partition_identity=partition.seal,
        mode="PRIMARY",
        _provenance=tensor_module._PRIMARY_PREPROCESSOR_CAPABILITY,
        _partition_capability=partition,
    )


def _support(times: tuple[datetime, ...], tensors: dict[datetime, MaskedTensor]) -> Any:
    contract = TensorContract()
    keys = tuple(time.isoformat() for time in times)
    target_keys = tuple(
        f"{instrument}|{time.isoformat()}"
        for time in times
        for instrument in runtime_module.ALL_INSTRUMENTS
    )
    return tensor_module.SupportRecord._create(
        token=tensor_module._SUPPORT_SEAL,
        keys=keys,
        ordered_key_sha256=hashlib.sha256(runtime_module._canonical([*keys])).hexdigest(),
        key_count=len(keys),
        contract_identity=contract.identity,
        lookback_minutes=contract.lookback_minutes,
        node_order=tuple(runtime_module.ALL_INSTRUMENTS),
        feature_names=contract.feature_names,
        target_keys=target_keys,
        target_nodes=tuple(range(20)) * len(times),
        source_identity="a" * 64,
        manifest_sha256="b" * 64,
        child_closure_sha256="c" * 64,
        row_content_identity="d" * 64,
        tensor_identities=tuple(
            (time.isoformat(), runtime_module._masked_tensor_identity(tensors[time]))
            for time in times
        ),
        mode="PRIMARY",
        parent_identity=runtime_module.PARENT_IDENTITY,
        _provenance=tensor_module._PRIMARY_SUPPORT_CAPABILITY,
    )


def _training_fixture(inactive_target: bool) -> tuple[Any, Any, Any, Any]:
    stage_times = (
        ("DEV_1", datetime(2026, 5, 16, 14, 6, tzinfo=UTC)),
        ("DEV_2", datetime(2026, 5, 30, 14, 6, tzinfo=UTC)),
        ("DEV_3", datetime(2026, 6, 13, 14, 6, tzinfo=UTC)),
    )
    memberships = {"DEV_1": "WARMUP", "DEV_2": "DEV_1", "DEV_3": "DEV_1+DEV_2"}
    rows: list[dict[str, Any]] = []
    for block, decision_time in stage_times:
        for instrument in foundation_module.ALL_INSTRUMENTS:
            rows.append(
                {
                    "target_id": f"{block}-{instrument}",
                    "instrument_id": instrument,
                    "decision_time": decision_time,
                    "horizon_minutes": 15,
                    "target_available_at": decision_time + timedelta(minutes=15),
                    "block": block,
                    "target_return": 0.1,
                    "local_forecast": 0.05,
                    "local_residual": 0.05,
                    "local_configuration_id": foundation_module.LOCAL_CONFIG_ID,
                    "forecast_fold": block,
                    "manifest_sha256": foundation_module.MANIFEST_SHA256,
                    "child_closure_sha256": "c" * 64,
                    "feature_semantic_sha256": foundation_module.FEATURE_SEMANTIC_SHA256,
                    "evidence_label": foundation_module.LABEL,
                    "source_class": foundation_module.SOURCE_CLASS,
                    "forecast_identity": foundation_module._forecast_identity(
                        foundation_module.LOCAL_CONFIG_ID, block, instrument, decision_time
                    ),
                    "residual_training_membership": memberships[block],
                }
            )
    frame = pl.DataFrame(rows).sort(["decision_time", "instrument_id", "target_id"])
    chronology = foundation_module.chronological_memberships(frame)
    chronology_identity = hashlib.sha256(
        foundation_module._canonical_json(
            [
                {"block": row["block"], "membership": row["residual_training_membership"]}
                for row in chronology.iter_rows(named=True)
            ]
        )
    ).hexdigest()
    foundation = foundation_module.AuthenticatedResidualFoundation._create(
        token=foundation_module._RESIDUAL_CAPSULE_TOKEN,
        rows=frame,
        manifest_sha256=foundation_module.MANIFEST_SHA256,
        child_closure_sha256="c" * 64,
        local_configuration_id=foundation_module.LOCAL_CONFIG_ID,
        feature_semantic_sha256=foundation_module.FEATURE_SEMANTIC_SHA256,
        ordered_row_keys=foundation_module._ordered_residual_keys(frame),
        stage_counts=tuple(
            (str(row["block"]), int(row["len"]))
            for row in frame.group_by("block").len().sort("block").iter_rows(named=True)
        ),
        instrument_counts=tuple(
            (str(row["instrument_id"]), int(row["len"]))
            for row in frame.group_by("instrument_id")
            .len()
            .sort("instrument_id")
            .iter_rows(named=True)
        ),
        forecast_identities=tuple(frame["forecast_identity"].to_list()),
        target_identities=tuple(frame["target_id"].to_list()),
        chronology_identity=chronology_identity,
        content_identity=foundation_module._residual_content_digest(frame),
    )
    tensors_by_time = {
        decision_time: _tensor(decision_time, inactive_target) for _, decision_time in stage_times
    }
    tensors = tuple(
        (key, tensors_by_time[row["decision_time"]])
        for key, row in zip(foundation.ordered_row_keys, frame.iter_rows(named=True), strict=True)
    )
    times = tuple(decision_time for _, decision_time in stage_times)
    return (
        foundation,
        _support(times, tensors_by_time),
        _preprocessor(tensors_by_time[times[0]]),
        tensors,
    )


def _prediction_fixture(inactive_target: bool) -> tuple[Any, Any, Any, str]:
    decision_time = datetime(2026, 5, 16, 14, 6, tzinfo=UTC)
    tensor = _tensor(decision_time, inactive_target)
    support = _support((decision_time,), {decision_time: tensor})
    preprocessor = _preprocessor(tensor)
    tensors = tuple((key, tensor) for key in support.target_keys)
    input_identity = runtime_module._sha256(
        {
            "support_identity": support.identity,
            "row_keys": support.target_keys,
            "tensor_identities": tuple(
                runtime_module._masked_tensor_identity(item) for _, item in tensors
            ),
            "preprocessor_identity": runtime_module.training_preprocessor_identity(preprocessor),
        }
    )
    return support, preprocessor, tensors, input_identity


@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA required")
def test_authenticated_training_accepts_mature_label_with_masked_target_node() -> None:
    foundation, support, preprocessor, tensors = _training_fixture(True)
    batch = ResidualTrainingBatch.from_authenticated_oof(
        tensors=tensors, foundation=foundation, support=support, preprocessor=preprocessor
    )
    assert bool(batch.target_mask.all())
    assert not bool(batch.node_mask[:, -1, 0].any())
    result = runtime_module.fit_one_model(
        cast(Any, build_family_model("LOCAL_TEMPORAL_RESIDUAL")).to("cuda"), batch
    )
    assert result["target_instruments"] == len(runtime_module.ALL_INSTRUMENTS)


@pytest.mark.parametrize("inactive_target", (False, True))
@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA required")
def test_authenticated_prediction_uses_support_targets_not_node_mask(
    inactive_target: bool,
) -> None:
    support, preprocessor, tensors, input_identity = _prediction_fixture(inactive_target)
    batch = PredictionBatch.from_authenticated_support(
        tensors=tensors,
        support=support,
        preprocessor=preprocessor,
        input_identity=input_identity,
    )
    assert bool(batch.target_mask.all())
    assert bool(batch.node_mask[:, -1, 0].all()) is not inactive_target
    prediction = predict_residual(
        cast(Any, build_family_model("LOCAL_TEMPORAL_RESIDUAL")).to("cuda"), batch
    )
    assert prediction.shape == (len(runtime_module.ALL_INSTRUMENTS),)
    assert bool(torch.isfinite(prediction).all())


@pytest.mark.parametrize("column", ("local_residual", "local_forecast"))
def test_authenticated_training_rejects_nonfinite_label_or_forecast(column: str) -> None:
    foundation, support, preprocessor, tensors = _training_fixture(True)
    object.__setattr__(
        foundation,
        "rows",
        foundation.rows.with_columns(pl.lit(float("nan")).alias(column)),
    )
    invalid = foundation
    with pytest.raises(ValueError, match=r"foundation|finite"):
        ResidualTrainingBatch.from_authenticated_oof(
            tensors=tensors, foundation=invalid, support=support, preprocessor=preprocessor
        )


def test_authenticated_prediction_rejects_missing_support_target_key() -> None:
    support, preprocessor, tensors, input_identity = _prediction_fixture(True)
    with pytest.raises(ValueError, match="exact support target keys"):
        PredictionBatch.from_authenticated_support(
            tensors=tensors[:-1],
            support=support,
            preprocessor=preprocessor,
            input_identity=input_identity,
        )
