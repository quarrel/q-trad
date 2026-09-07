"""Sealed compact stage inputs produced by authenticated preparation."""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

from .runtime import (
    _STREAMING_BATCH_CAPABILITY,
    _StreamingPredictionBatch,
    _StreamingResidualTrainingBatch,
    training_preprocessor_identity,
)
from .stage_cache import _file_digest, _publish_cache_once, _semantic_array_digest
from .tensor import FittedTrainingPreprocessor, load_authenticated_training_preprocessor
from .tensor_store import RawTensorStore, load_raw_tensor_store

_SCHEMA = "R4-P0-PREPARED-DEVELOPMENT-STAGE-V1"


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _identity(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _bound_path(path: Path, *, trusted_root: Path, label: str) -> Path:
    """Resolve one existing path while rejecting escapes and symlink substitution."""
    candidate = path.absolute()
    try:
        relative = candidate.relative_to(trusted_root)
    except ValueError as exc:
        raise ValueError(f"{label} escaped its authenticated root") from exc
    current = trusted_root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError(f"{label} must not contain symlinks")
    resolved = candidate.resolve(strict=True)
    if resolved != candidate:
        raise ValueError(f"{label} escaped its authenticated root")
    return resolved


def _preprocessor_payload(preprocessor: FittedTrainingPreprocessor) -> dict[str, Any]:
    return {
        "means": preprocessor.means.tolist(),
        "scales": preprocessor.scales.tolist(),
        "feature_names": list(preprocessor.feature_names),
        "training_cutoff": preprocessor.training_cutoff.isoformat(),
        "contract_identity": preprocessor.contract_identity,
        "fit_partition": preprocessor.fit_partition,
        "binary_features": list(preprocessor.binary_features),
        "training_partition_identity": preprocessor.training_partition_identity,
        "mode": preprocessor.mode,
        "reduction_algorithm": preprocessor.reduction_algorithm,
        "preprocessor_identity": training_preprocessor_identity(preprocessor),
    }


def persist_prepared_stage(
    parent: Path,
    *,
    stage: str,
    raw_store: RawTensorStore,
    training: _StreamingResidualTrainingBatch,
    prediction: _StreamingPredictionBatch,
    preprocessor: FittedTrainingPreprocessor,
    evaluation_block: str,
) -> Path:
    """Persist bulk row data as sealed arrays and retain only small identities inline."""
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{stage.lower()}-", dir=parent))
    try:
        arrays = {
            "train-row-key": np.asarray(training.row_keys),
            "train-residual": np.asarray(training.residuals, dtype="<f8"),
            "train-target-node": np.asarray(training.target_nodes, dtype="<i2"),
            "train-block": np.asarray(training.training_blocks),
            "predict-row-key": np.asarray(prediction.row_keys),
            "predict-target-node": np.asarray(prediction.target_nodes, dtype="<i2"),
            "predict-target-mask": np.asarray(prediction.target_mask, dtype=np.bool_),
        }
        files = []
        for name, array in arrays.items():
            path = staging / f"{name}.npy"
            np.save(path, array, allow_pickle=False)
            files.append(
                {
                    "path": path.name,
                    "size": path.stat().st_size,
                    "sha256": _file_digest(path),
                    "semantic_sha256": _semantic_array_digest(array),
                }
            )
        manifest: dict[str, Any] = {
            "schema": _SCHEMA,
            "stage": stage,
            "evaluation_block": evaluation_block,
            "raw_store_identity": raw_store.manifest["store_identity"],
            "raw_store_path": str(raw_store.root),
            "preprocessor": _preprocessor_payload(preprocessor),
            "training": {
                "input_identity": training.input_identity,
                "support_identity": training.support_identity,
                "chronology_identity": training.chronology_identity,
                "content_identity": training.content_identity,
                "provider_tensor_identities": [
                    list(value) for value in training.provider_tensor_identities
                ],
            },
            "prediction": {
                "input_identity": prediction.input_identity,
                "support_identity": prediction.support_identity,
                "chronology_identity": prediction.chronology_identity,
                "content_identity": prediction.content_identity,
                "provider_tensor_identities": [
                    list(value) for value in prediction.provider_tensor_identities
                ],
            },
            "files": files,
        }
        manifest["stage_identity"] = _identity(manifest)
        (staging / "manifest.json").write_bytes(_canonical(manifest))
        destination = parent / stage / manifest["stage_identity"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        _publish_cache_once(staging, destination)
        return destination
    except BaseException as exc:
        if staging.exists():
            failure_payload = {
                "schema": "R4-P0-PREPARED-DEVELOPMENT-STAGE-FAILURE-V1",
                "status": "FAILED",
                "stage": stage,
                "exception_type": type(exc).__name__,
                "exception_message": str(exc),
                "files": [
                    {
                        "path": path.relative_to(staging).as_posix(),
                        "size": path.stat().st_size,
                        "sha256": _file_digest(path),
                    }
                    for path in sorted(staging.rglob("*"))
                    if path.is_file() and not path.is_symlink()
                ],
            }
            failure_payload["failure_identity"] = _identity(failure_payload)
            (staging / "failure.json").write_bytes(_canonical(failure_payload))
        raise


def load_prepared_stage(
    root: Path,
    *,
    expected_stage_identity: str,
    expected_raw_store_path: Path,
    expected_raw_store_identity: str,
    trusted_root: Path,
    authenticated_raw_store: RawTensorStore | None = None,
) -> tuple[Any, Any, FittedTrainingPreprocessor, dict[str, Any]]:
    """Authenticate a bound prepared stage and expose its mmap-backed inputs."""
    trusted_root = trusted_root.resolve(strict=True)
    root = _bound_path(root, trusted_root=trusted_root, label="prepared development stage")
    if root.name != expected_stage_identity:
        raise ValueError("prepared development stage path identity drifted")
    manifest_path = _bound_path(
        root / "manifest.json", trusted_root=root, label="prepared development stage manifest"
    )
    manifest = json.loads(manifest_path.read_text())
    stage_identity = manifest.pop("stage_identity")
    if stage_identity != expected_stage_identity or stage_identity != _identity(manifest):
        raise ValueError("prepared development stage identity drifted")
    manifest["stage_identity"] = stage_identity
    arrays: dict[str, np.ndarray] = {}
    for item in manifest["files"]:
        relative_path = Path(item["path"])
        if relative_path.is_absolute() or len(relative_path.parts) != 1:
            raise ValueError("prepared development stage file path escaped")
        path = _bound_path(
            root / relative_path, trusted_root=root, label="prepared development stage file"
        )
        if path.stat().st_size != item["size"] or _file_digest(path) != item["sha256"]:
            raise ValueError("prepared development stage container drifted")
        array = np.load(path, mmap_mode="r", allow_pickle=False)
        if _semantic_array_digest(array) != item["semantic_sha256"]:
            raise ValueError("prepared development stage semantic content drifted")
        arrays[path.stem] = array
    raw_store_path = Path(manifest["raw_store_path"])
    if (
        raw_store_path != expected_raw_store_path
        or manifest["raw_store_identity"] != expected_raw_store_identity
    ):
        raise ValueError("prepared development raw tensor store binding drifted")
    raw_store_path = _bound_path(
        raw_store_path, trusted_root=trusted_root, label="prepared development raw tensor store"
    )
    if authenticated_raw_store is None:
        raw_store = load_raw_tensor_store(
            raw_store_path, expected_identity=expected_raw_store_identity
        )
    else:
        raw_store = authenticated_raw_store
        if (
            raw_store.root != raw_store_path
            or raw_store.manifest["store_identity"] != expected_raw_store_identity
        ):
            raise ValueError("authenticated raw tensor store binding drifted")
    preprocessor = load_authenticated_training_preprocessor(manifest["preprocessor"])
    training_meta = manifest["training"]
    prediction_meta = manifest["prediction"]
    training = _StreamingResidualTrainingBatch(
        tensors=raw_store,
        preprocessor=preprocessor,
        row_keys=tuple(str(value) for value in arrays["train-row-key"]),
        residuals=tuple(float(value) for value in arrays["train-residual"]),
        target_nodes=tuple(int(value) for value in arrays["train-target-node"]),
        training_blocks=tuple(str(value) for value in arrays["train-block"]),
        input_identity=training_meta["input_identity"],
        support_identity=training_meta["support_identity"],
        chronology_identity=training_meta["chronology_identity"],
        preprocessor_identity=training_preprocessor_identity(preprocessor),
        content_identity=training_meta["content_identity"],
        provider_tensor_identities=tuple(
            tuple(value) for value in training_meta["provider_tensor_identities"]
        ),
        _capability=_STREAMING_BATCH_CAPABILITY,
    )
    prediction = _StreamingPredictionBatch(
        tensors=raw_store,
        preprocessor=preprocessor,
        row_keys=tuple(str(value) for value in arrays["predict-row-key"]),
        target_nodes=tuple(int(value) for value in arrays["predict-target-node"]),
        target_mask=tuple(bool(value) for value in arrays["predict-target-mask"]),
        input_identity=prediction_meta["input_identity"],
        support_identity=prediction_meta["support_identity"],
        chronology_identity=prediction_meta["chronology_identity"],
        preprocessor_identity=training_preprocessor_identity(preprocessor),
        content_identity=prediction_meta["content_identity"],
        provider_tensor_identities=tuple(
            tuple(value) for value in prediction_meta["provider_tensor_identities"]
        ),
        _capability=_STREAMING_BATCH_CAPABILITY,
    )
    training._validate()
    prediction._validate()
    return training, prediction, preprocessor, manifest
