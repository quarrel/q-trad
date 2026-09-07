"""Synthetic authenticated inputs used only by the bounded qualification benchmark."""

from __future__ import annotations

import hashlib
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from . import foundation as foundation_module
from . import runtime as runtime_module
from . import tensor as tensor_module
from .runtime import PredictionBatch, ResidualTrainingBatch
from .stage_cache import CacheInput, VerifiedCache, build_stage_cache
from .tensor import MaskedTensor, TensorContract


def _tensor(decision_time: datetime, *, vary_values: bool = False) -> MaskedTensor:
    contract = TensorContract()
    shape = (
        contract.lookback_minutes + 1,
        len(runtime_module.ALL_INSTRUMENTS),
        len(contract.feature_names),
    )
    values = np.zeros(shape, dtype=float)
    if vary_values:
        minute = (decision_time.hour * 60 + decision_time.minute) / 1440
        values[:] = 0.01 * (
            minute
            + np.arange(shape[0])[:, None, None] / shape[0]
            + np.arange(shape[1])[None, :, None] / shape[1]
            + np.arange(shape[2])[None, None, :] / shape[2]
        )
    mask = np.ones(shape, dtype=bool)
    return MaskedTensor(
        values=values,
        value_mask=mask.copy(),
        availability_mask=mask,
        node_mask=np.ones(shape[:2], dtype=bool),
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
        target_nodes=tuple(range(len(runtime_module.ALL_INSTRUMENTS))) * len(times),
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


def _training_fixture(
    *, dev1_timestamp_count: int = 1, vary_values: bool = False
) -> tuple[Any, Any, Any, Any]:
    if dev1_timestamp_count < 1:
        raise ValueError("DEV_1 timestamp count must be positive")
    first_dev1 = datetime(2026, 5, 16, 14, 6, tzinfo=UTC)
    stage_times = (
        *(
            ("DEV_1", first_dev1 + timedelta(minutes=index))
            for index in range(dev1_timestamp_count)
        ),
        ("DEV_2", datetime(2026, 5, 30, 14, 6, tzinfo=UTC)),
        ("DEV_3", datetime(2026, 6, 13, 14, 6, tzinfo=UTC)),
    )
    memberships = {"DEV_1": "WARMUP", "DEV_2": "DEV_1", "DEV_3": "DEV_1+DEV_2"}
    rows: list[dict[str, Any]] = []
    for block, decision_time in stage_times:
        for instrument in foundation_module.ALL_INSTRUMENTS:
            rows.append(
                {
                    "target_id": (
                        f"{block}-{instrument}"
                        if dev1_timestamp_count == 1
                        else f"{block}-{decision_time.isoformat()}-{instrument}"
                    ),
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
    tensors_by_time = {time: _tensor(time, vary_values=vary_values) for _, time in stage_times}
    tensors = tuple(
        (key, tensors_by_time[row["decision_time"]])
        for key, row in zip(foundation.ordered_row_keys, frame.iter_rows(named=True), strict=True)
    )
    times = tuple(time for _, time in stage_times)
    return (
        foundation,
        _support(times, tensors_by_time),
        _preprocessor(tensors_by_time[times[0]]),
        tensors,
    )


def synthetic_qualification_inputs(
    *, dev1_timestamp_count: int = 1, prediction_timestamp_count: int = 1, vary_values: bool = False
) -> tuple[Any, Any, CacheInput]:
    """Construct authenticated inputs without writing or initialising CUDA."""
    contract = TensorContract()
    tensor_bytes = (
        (contract.lookback_minutes + 1)
        * len(runtime_module.ALL_INSTRUMENTS)
        * len(contract.feature_names)
        * 8
    )
    if prediction_timestamp_count < 1:
        raise ValueError("prediction timestamp count must be positive")
    projected_bytes = tensor_bytes * (dev1_timestamp_count + prediction_timestamp_count + 2) * 2
    if projected_bytes >= 5_000_000_000:
        raise ValueError("synthetic qualification projected footprint must remain below 5 GB")
    foundation, support, preprocessor, tensors = _training_fixture(
        dev1_timestamp_count=dev1_timestamp_count, vary_values=vary_values
    )
    grouped = {tensor.decision_time.isoformat(): tensor for _, tensor in tensors}
    training = ResidualTrainingBatch.from_authenticated_oof(
        tensors=grouped,
        foundation=foundation,
        support=support,
        preprocessor=preprocessor,
        stream=True,
    )
    prediction_time = datetime(2026, 5, 16, 14, 6, tzinfo=UTC)
    prediction_times = tuple(
        prediction_time + timedelta(minutes=index) for index in range(prediction_timestamp_count)
    )
    prediction_by_time = {time: _tensor(time, vary_values=vary_values) for time in prediction_times}
    prediction_support = _support(prediction_times, prediction_by_time)
    prediction_preprocessor = _preprocessor(prediction_by_time[prediction_time])
    prediction_tensors = {time.isoformat(): tensor for time, tensor in prediction_by_time.items()}
    input_identity = runtime_module._sha256(
        {
            "support_identity": prediction_support.identity,
            "row_keys": prediction_support.target_keys,
            "tensor_identities": tuple(
                runtime_module._masked_tensor_identity(prediction_tensors[key.split("|", 1)[1]])
                for key in prediction_support.target_keys
            ),
            "preprocessor_identity": runtime_module.training_preprocessor_identity(
                prediction_preprocessor
            ),
            "policy": runtime_module.MATERIALISATION_POLICY,
        }
    )
    prediction = PredictionBatch.from_authenticated_support(
        tensors=prediction_tensors,
        support=prediction_support,
        preprocessor=prediction_preprocessor,
        input_identity=input_identity,
        stream=True,
    )
    identity = CacheInput(
        stage="DEV_2",
        training_blocks=("DEV_1",),
        foundation_identity=training.input_identity,
        support_identity=training.support_identity,
        tensor_identity=hashlib.sha256(
            repr(training.provider_tensor_identities).encode()
        ).hexdigest(),
        preprocessor_identity=training.preprocessor_identity,
        config_identity=runtime_module.FrozenRuntimeConfig().identity,
        numerical_runtime_identity=runtime_module._sha256(runtime_module.environment_identity()),
        producer_head=subprocess.run(
            ("git", "rev-parse", "HEAD"),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
        universe=tuple(runtime_module.ALL_INSTRUMENTS),
        node_order=tuple(runtime_module.ALL_INSTRUMENTS),
        feature_order=TensorContract().feature_names,
    )
    return training, prediction, identity


def build_synthetic_cache(
    release_root: Path, *, inputs: tuple[Any, Any, CacheInput] | None = None
) -> tuple[Path, CacheInput]:
    training, prediction, identity = inputs or synthetic_qualification_inputs()
    verified: VerifiedCache = build_stage_cache(
        release_root,
        identity,
        training_batch=training,
        prediction_batch=prediction,
        build_id="qualification-synthetic",
    )
    return verified.root, identity
