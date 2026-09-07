"""Durable content-addressed R4-P0 stage-cache storage and verification."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import inspect
import json
import multiprocessing
import os
from collections import deque
from collections.abc import Iterator, Mapping
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import numpy as np

from . import tensor
from .attempt_artifacts import canonical_json, create_json_once, sha256_bytes
from .foundation import ALL_INSTRUMENTS
from .runtime import (
    _iter_stream_training_chunks,
    _load_grouped_stage_cache_batches,
    _PinnedCudaDoubleBuffer,
    _predict_streaming_batch,
    _StreamingPredictionBatch,
    _StreamingResidualTrainingBatch,
)

_SCHEMA = "R4-P0-STAGE-CACHE-V2"
_FORBIDDEN_NAMES = frozenset({"outcome", "target_return", "realised_return", "label"})
_MAX_MATERIALISATION_WORKERS = 8
_HASH_CHUNK_BYTES = 8 * 1024 * 1024
_STAGE_TRAINING_BLOCKS = {
    "DEV_2": ("DEV_1",),
    "DEV_3": ("DEV_1", "DEV_2"),
}


@dataclass(frozen=True)
class CacheInput:
    stage: str
    training_blocks: tuple[str, ...]
    foundation_identity: str
    support_identity: str
    tensor_identity: str
    preprocessor_identity: str
    config_identity: str
    numerical_runtime_identity: str
    producer_head: str
    universe: tuple[str, ...]
    node_order: tuple[str, ...]
    feature_order: tuple[str, ...]

    def semantic_inputs(self) -> dict[str, object]:
        return {
            "stage": self.stage,
            "training_blocks": list(self.training_blocks),
            "foundation_identity": self.foundation_identity,
            "support_identity": self.support_identity,
            "tensor_identity": self.tensor_identity,
            "preprocessor_identity": self.preprocessor_identity,
            "config_identity": self.config_identity,
            "numerical_runtime_identity": self.numerical_runtime_identity,
            "universe": list(self.universe),
            "node_order": list(self.node_order),
            "feature_order": list(self.feature_order),
        }


@dataclass(frozen=True)
class VerifiedCache:
    root: Path
    cache_identity: str
    manifest_identity: str
    files: tuple[dict[str, object], ...]


def cache_builder_semantic_closure() -> str:

    sources = (
        inspect.getsource(build_stage_cache),
        inspect.getsource(verify_stage_cache),
        inspect.getsource(load_stage_cache_batches),
        inspect.getsource(CacheInput.semantic_inputs),
        inspect.getsource(_project_training_batch),
        inspect.getsource(_materialise_batch),
        inspect.getsource(_take),
        inspect.getsource(_ordered_parallel_transforms),
        inspect.getsource(_timestamp_sources),
        inspect.getsource(_initialise_transform_worker),
        inspect.getsource(_transform_one),
        inspect.getsource(_batch_row_arrays),
        inspect.getsource(_batch_metadata),
        inspect.getsource(_semantic_array_digest),
        inspect.getsource(_file_digest),
        inspect.getsource(_write_npy_once),
        inspect.getsource(_validate_arrays),
        inspect.getsource(_publish_cache_once),
        inspect.getsource(_publication_file_state),
        inspect.getsource(canonical_json),
        inspect.getsource(sha256_bytes),
        inspect.getsource(create_json_once),
        inspect.getsource(tensor.FittedTrainingPreprocessor),
        inspect.getsource(_load_grouped_stage_cache_batches),
        inspect.getsource(_PinnedCudaDoubleBuffer),
        inspect.getsource(_iter_stream_training_chunks),
        inspect.getsource(_predict_streaming_batch),
        _SCHEMA,
    )
    return sha256_bytes("\n".join(sources).encode())


def _publish_cache_once(staging: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if staging.stat().st_dev != destination.parent.stat().st_dev:
        raise OSError("cache staging and destination are on different filesystems")
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = libc.renameat2
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    if renameat2(-100, os.fsencode(staging), -100, os.fsencode(destination), 1) != 0:
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise FileExistsError(error, "cache destination already exists", destination)
        raise OSError(error, os.strerror(error), destination)


def _publication_file_state(path: Path) -> tuple[int, ...]:
    """Bind a digest to an unchanged local file across the atomic rename."""
    state = path.lstat()
    return (
        state.st_dev,
        state.st_ino,
        state.st_mode,
        state.st_size,
        state.st_mtime_ns,
        state.st_ctime_ns,
    )


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _semantic_array_digest(array: np.ndarray) -> str:
    digest = hashlib.sha256(
        canonical_json({"dtype": array.dtype.str, "shape": array.shape, "order": "C", "version": 1})
    )
    byte_view = memoryview(np.asarray(array).view(np.uint8).reshape(-1))
    for offset in range(0, len(byte_view), _HASH_CHUNK_BYTES):
        digest.update(byte_view[offset : offset + _HASH_CHUNK_BYTES])
    return digest.hexdigest()


def _write_npy_once(path: Path, array: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        np.save(handle, np.ascontiguousarray(array), allow_pickle=False)
        handle.flush()
        os.fsync(handle.fileno())


def _validate_arrays(arrays: Mapping[str, np.ndarray]) -> None:
    if not arrays:
        raise ValueError("stage cache requires arrays")
    if _FORBIDDEN_NAMES.intersection(arrays):
        raise ValueError("stage cache contains an outcome-bearing field")
    for name, array in arrays.items():
        if not name.isidentifier() or name.startswith("_"):
            raise ValueError("cache array name is not canonical")
        if array.dtype.hasobject or array.dtype.byteorder not in {"<", "=", "|"}:
            raise ValueError("cache arrays must be non-object native/little endian")
        if array.ndim == 0:
            raise ValueError("cache arrays must not be scalar")
        # Values were checked after float32 conversion by _transform_one.
        if name != "values" and np.issubdtype(array.dtype, np.floating):
            for start in range(0, array.shape[0], 64):
                if not np.isfinite(np.asarray(array[start : start + 64])).all():
                    raise ValueError("cache arrays must be finite")
    timestamp_count = arrays["values"].shape[0]
    row_count = arrays["row_timestamp"].shape[0]
    if any(
        arrays[name].shape[0] != timestamp_count
        for name in ("value_mask", "availability_mask", "node_mask")
    ):
        raise ValueError("grouped cache tensor arrays must share the timestamp dimension")
    if any(arrays[name].shape[0] != row_count for name in ("target_nodes", "target_mask")):
        raise ValueError("grouped cache mapping arrays must share the row dimension")
    if row_count and (
        arrays["row_timestamp"].min() < 0 or arrays["row_timestamp"].max() >= timestamp_count
    ):
        raise ValueError("grouped cache row timestamp mapping is out of bounds")
    for optional in ("residuals", "row_weights"):
        if optional in arrays and arrays[optional].shape[0] != row_count:
            raise ValueError("grouped cache training arrays must share the row dimension")


def _stream_batch_identity(batch: _StreamingResidualTrainingBatch) -> str:
    payload = {
        "policy": "R4.D.TIMESTAMP_BATCH_V1",
        "batch_size": batch.batch_size,
        "row_keys": batch.row_keys,
        "residuals": batch.residuals,
        "target_nodes": batch.target_nodes,
        "training_blocks": batch.training_blocks,
        "input_identity": batch.input_identity,
        "support": batch.support_identity,
        "tensor_identities": batch.provider_tensor_identities,
        "chronology": batch.chronology_identity,
        "preprocessor": batch.preprocessor_identity,
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _project_training_batch(
    batch: _StreamingResidualTrainingBatch, identity: CacheInput
) -> _StreamingResidualTrainingBatch:
    expected_blocks = _STAGE_TRAINING_BLOCKS.get(identity.stage)
    if expected_blocks is None or identity.training_blocks != expected_blocks:
        raise ValueError("stage cache training blocks do not match authorised chronology")
    selected = tuple(
        index for index, block in enumerate(batch.training_blocks) if block in expected_blocks
    )
    if not selected:
        raise ValueError("stage cache has no authorised training rows")
    row_keys = tuple(batch.row_keys[index] for index in selected)
    timestamps = tuple(dict.fromkeys(key.rsplit("|", 1)[-1] for key in row_keys))
    identities = dict(batch.provider_tensor_identities)
    projected = replace(
        batch,
        row_keys=row_keys,
        residuals=tuple(batch.residuals[index] for index in selected),
        target_nodes=tuple(batch.target_nodes[index] for index in selected),
        training_blocks=tuple(batch.training_blocks[index] for index in selected),
        provider_tensor_identities=tuple(
            (timestamp, identities[timestamp]) for timestamp in timestamps
        ),
        support_identity=identity.support_identity,
        preprocessor_identity=identity.preprocessor_identity,
        content_identity="",
        _support=None,
        _foundation=None,
    )
    projected = replace(projected, content_identity=_stream_batch_identity(projected))
    projected._validate()
    return projected


_WORKER_PREPROCESSOR: Any | None = None
_WORKER_RAW_ARRAYS: dict[str, np.ndarray] | None = None
_WORKER_RAW_INDEX: dict[str, int] | None = None


def _initialise_transform_worker(
    preprocessor: Any,
    raw_root: str | None = None,
    authenticated_raw_manifest: Mapping[str, Any] | None = None,
) -> None:
    global _WORKER_PREPROCESSOR, _WORKER_RAW_ARRAYS, _WORKER_RAW_INDEX
    _WORKER_PREPROCESSOR = preprocessor
    _WORKER_RAW_ARRAYS = None
    _WORKER_RAW_INDEX = None
    if raw_root is not None:
        if authenticated_raw_manifest is None:
            raise ValueError(
                "cache transform worker requires an authenticated raw-store descriptor"
            )
        from .tensor_store import RawTensorStore

        # The supervisor has already authenticated every sealed container. Workers only
        # open the trusted descriptor's mmap views; replaying full hashes here is both
        # redundant and proportional to corpus size for every spawned worker.
        store = RawTensorStore(Path(raw_root), dict(authenticated_raw_manifest))
        _WORKER_RAW_INDEX = dict(store._index)
        _WORKER_RAW_ARRAYS = {
            "values": store._values,
            "value-mask": store._value_mask,
            "availability-mask": store._availability_mask,
            "node-mask": store._node_mask,
        }


def _transform_one(source: Any) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    if _WORKER_PREPROCESSOR is None:
        raise RuntimeError("cache transform worker was not initialised")
    if isinstance(source, str):
        if _WORKER_RAW_ARRAYS is None or _WORKER_RAW_INDEX is None:
            raise RuntimeError("cache transform worker raw mmap binding is unavailable")
        position = _WORKER_RAW_INDEX[source]
        contract = tensor.TensorContract()
        source = tensor.MaskedTensor(
            values=_WORKER_RAW_ARRAYS["values"][position],
            value_mask=_WORKER_RAW_ARRAYS["value-mask"][position],
            availability_mask=_WORKER_RAW_ARRAYS["availability-mask"][position],
            node_mask=_WORKER_RAW_ARRAYS["node-mask"][position],
            decision_time=datetime.fromisoformat(source),
            contract_identity=contract.identity,
            contract=contract,
        )
    transformed = _WORKER_PREPROCESSOR.transform(source)
    values = transformed.values.astype(np.float32, copy=False)
    value_mask = transformed.value_mask.astype(np.bool_, copy=False)
    if np.isinf(values).any() or (np.isnan(values) & value_mask).any():
        raise ValueError("preprocessed cache values must be finite where observed")
    return (
        np.nan_to_num(values, nan=0.0, posinf=None, neginf=None),
        value_mask,
        transformed.availability_mask.astype(np.bool_, copy=False),
        transformed.node_mask.astype(np.bool_, copy=False),
    )


def _take(tasks: Iterator[Any], count: int) -> Iterator[Any]:
    for _ in range(count):
        try:
            yield next(tasks)
        except StopIteration:
            return


def _ordered_parallel_transforms(
    executor: ProcessPoolExecutor,
    tasks: Iterator[Any],
    window: int,
) -> Iterator[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]:
    """Yield canonical results with a bounded resident timestamp window."""
    pending = deque(executor.submit(_transform_one, task) for task in _take(tasks, window))
    while pending:
        yield pending.popleft().result()
        try:
            task = next(tasks)
        except StopIteration:
            continue
        pending.append(executor.submit(_transform_one, task))


def _timestamp_sources(batch: Any, timestamps: tuple[str, ...]) -> Iterator[Any]:
    if isinstance(batch.tensors, Mapping):
        tensors = cast(Mapping[str, Any], batch.tensors)
        for timestamp in timestamps:
            yield tensors[timestamp]
        return
    source = iter(cast(Iterator[tuple[str, Any]], iter(batch.tensors)))
    for timestamp in timestamps:
        for source_timestamp, tensor_value in source:
            if source_timestamp == timestamp:
                yield tensor_value
                break
        else:
            raise ValueError(f"cache tensor source is missing timestamp {timestamp}")


def _batch_row_arrays(
    batch: _StreamingResidualTrainingBatch | _StreamingPredictionBatch, *, training: bool
) -> dict[str, np.ndarray]:
    timestamps = tuple(key for key, _ in batch.provider_tensor_identities)
    positions = {timestamp: index for index, timestamp in enumerate(timestamps)}
    target_nodes = np.asarray(batch.target_nodes, dtype="<i8")
    max_key_length = max(len(key) for key in batch.row_keys)
    arrays = {
        "row_key": np.asarray(batch.row_keys, dtype=f"<U{max_key_length}"),
        "row_timestamp": np.asarray(
            [positions[key.rsplit("|", 1)[-1]] for key in batch.row_keys], dtype="<i8"
        ),
        "target_nodes": target_nodes,
        "target_mask": np.ones(len(batch.row_keys), dtype=np.bool_),
    }
    if training:
        training_batch = cast(_StreamingResidualTrainingBatch, batch)
        arrays["residuals"] = np.asarray(training_batch.residuals, dtype="<f4")
        block_order = tuple(dict.fromkeys(training_batch.training_blocks))
        block_positions = {block: index for index, block in enumerate(block_order)}
        arrays["training_block"] = np.asarray(
            [block_positions[block] for block in training_batch.training_blocks], dtype="<i1"
        )
        counts = np.bincount(target_nodes, minlength=len(ALL_INSTRUMENTS))
        if np.any(target_nodes < 0) or np.any(target_nodes >= len(ALL_INSTRUMENTS)):
            raise ValueError("training cache target node is outside the canonical universe")
        if np.any(counts == 0):
            raise ValueError("training cache must cover every canonical instrument")
        arrays["row_weights"] = (1.0 / (len(ALL_INSTRUMENTS) * counts[target_nodes])).astype("<f4")
    return arrays


def _materialise_batch(
    staging: Path,
    group: str,
    batch: _StreamingResidualTrainingBatch | _StreamingPredictionBatch,
    *,
    training: bool,
) -> dict[str, np.ndarray]:
    if not isinstance(batch, (_StreamingResidualTrainingBatch, _StreamingPredictionBatch)):
        raise TypeError("authenticated stage cache requires a grouped streaming batch")
    batch._validate()
    if batch.preprocessor is None:
        raise TypeError("cache builder requires authenticated preprocessor provenance")
    timestamps = tuple(key for key, _ in batch.provider_tensor_identities)
    if tuple(timestamps) != tuple(dict.fromkeys(timestamps)):
        raise ValueError("cache timestamps are not unique")
    target = staging / group
    target.mkdir(parents=True, exist_ok=False)
    arrays = _batch_row_arrays(batch, training=training)
    for name, array in arrays.items():
        _write_npy_once(target / f"{name}.part-00000.npy", array)
    raw_root = getattr(batch.tensors, "root", None)
    raw_manifest = (
        None
        if raw_root is None
        else dict(cast(Mapping[str, Any], cast(Any, batch.tensors).manifest))
    )
    tasks = _timestamp_sources(batch, timestamps) if raw_root is None else iter(timestamps)
    context = multiprocessing.get_context("spawn")
    workers = min(_MAX_MATERIALISATION_WORKERS, len(timestamps))
    with ProcessPoolExecutor(
        max_workers=workers,
        mp_context=context,
        initializer=_initialise_transform_worker,
        initargs=(batch.preprocessor, None if raw_root is None else str(raw_root), raw_manifest),
    ) as executor:
        results = iter(_ordered_parallel_transforms(executor, iter(tasks), workers))
        first = next(results)
        mmaps: dict[str, np.memmap] = {}
        names = ("values", "value_mask", "availability_mask", "node_mask")
        for name, item in zip(names, first, strict=True):
            mmaps[name] = np.lib.format.open_memmap(
                target / f"{name}.part-00000.npy",
                mode="w+",
                dtype=item.dtype,
                shape=(len(timestamps), *item.shape),
            )
            mmaps[name][0] = item
        for position, transformed in enumerate(results, start=1):
            for name, item in zip(names, transformed, strict=True):
                mmaps[name][position] = item
    for mmap in mmaps.values():
        mmap.flush()
    return {**arrays, **mmaps}


def _batch_metadata(
    batch: _StreamingResidualTrainingBatch | _StreamingPredictionBatch,
    *,
    training: bool,
) -> dict[str, object]:
    if not isinstance(batch, (_StreamingResidualTrainingBatch, _StreamingPredictionBatch)):
        raise TypeError("stage cache metadata requires grouped streaming provenance")
    metadata: dict[str, object] = {
        "input_identity": batch.input_identity,
        "support_identity": batch.support_identity,
        "chronology_identity": batch.chronology_identity,
        "preprocessor_identity": batch.preprocessor_identity,
        "content_identity": batch.content_identity,
        "mode": batch.mode,
        "provider_tensor_identities": [list(item) for item in batch.provider_tensor_identities],
        "batch_size": batch.batch_size,
    }
    if training:
        training_batch = cast(_StreamingResidualTrainingBatch, batch)
        metadata["block_order"] = list(dict.fromkeys(training_batch.training_blocks))
    return metadata


def build_stage_cache(
    release_root: Path,
    identity: CacheInput,
    *,
    training_batch: _StreamingResidualTrainingBatch,
    prediction_batch: _StreamingPredictionBatch,
    build_id: str,
) -> VerifiedCache:
    if identity.stage == "TERMINAL_FORMER_HOLDOUT":
        raise PermissionError("terminal stage cache requires separate terminal authority")
    if not isinstance(training_batch, _StreamingResidualTrainingBatch):
        raise TypeError("stage cache requires a grouped streaming training batch")
    training_batch = _project_training_batch(training_batch, identity)
    prediction_keys = tuple(prediction_batch.row_keys)
    if prediction_keys and prediction_keys[0].startswith("SMOKE|"):
        canonical_keys = tuple(sorted(prediction_keys, key=lambda key: int(key.split("|", 1)[1])))
    else:
        canonical_keys = tuple(sorted(prediction_keys, key=lambda key: key.split("|", 1)[::-1]))
    if prediction_keys != canonical_keys:
        raise ValueError("prediction keys are not canonical")
    release_root = release_root.resolve()
    staging = release_root / "staging" / "cache" / build_id
    staging.mkdir(parents=True, exist_ok=False)
    train_arrays = _materialise_batch(staging, "train", training_batch, training=True)
    predict_arrays = _materialise_batch(staging, "predict", prediction_batch, training=False)
    _validate_arrays(train_arrays)
    _validate_arrays(predict_arrays)
    files: list[dict[str, object]] = []
    publication_states: dict[str, tuple[int, ...]] = {}
    for group, arrays in (("train", train_arrays), ("predict", predict_arrays)):
        for name, array in sorted(arrays.items()):
            path = staging / group / f"{name}.part-00000.npy"
            publication_states[path.relative_to(staging).as_posix()] = _publication_file_state(path)
            files.append(
                {
                    "path": path.relative_to(staging).as_posix(),
                    "size": path.stat().st_size,
                    "container_sha256": _file_digest(path),
                    "semantic_sha256": _semantic_array_digest(array),
                    "dtype": array.dtype.str,
                    "shape": list(array.shape),
                }
            )
    keys_path = staging / "keys" / "part-00000.jsonl"
    keys_path.parent.mkdir(parents=True)
    with keys_path.open("xb") as handle:
        for key in prediction_keys:
            handle.write(canonical_json({"key": key}) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    publication_states["keys/part-00000.jsonl"] = _publication_file_state(keys_path)
    files.append(
        {
            "path": "keys/part-00000.jsonl",
            "size": keys_path.stat().st_size,
            "container_sha256": _file_digest(keys_path),
            "semantic_sha256": sha256_bytes(canonical_json(tuple(prediction_keys))),
            "dtype": "canonical-jsonl",
            "shape": [len(prediction_keys)],
        }
    )
    manifest: dict[str, object] = {
        "schema": _SCHEMA,
        "producer_head": identity.producer_head,
        "builder_semantic_closure": cache_builder_semantic_closure(),
        "semantic_inputs": identity.semantic_inputs(),
        "batch_metadata": {
            "train": _batch_metadata(training_batch, training=True),
            "predict": _batch_metadata(prediction_batch, training=False),
        },
        "row_counts": {
            "train": train_arrays["target_nodes"].shape[0],
            "predict": predict_arrays["target_nodes"].shape[0],
        },
        "timestamp_counts": {
            "train": train_arrays["values"].shape[0],
            "predict": predict_arrays["values"].shape[0],
        },
        "files": files,
    }
    manifest["manifest_identity"] = sha256_bytes(canonical_json(manifest))
    create_json_once(staging / "manifest.json", manifest)
    cache_identity = sha256_bytes(
        canonical_json(
            {
                "manifest_identity": manifest["manifest_identity"],
                "semantic_inputs": identity.semantic_inputs(),
                "builder_semantic_closure": manifest["builder_semantic_closure"],
            }
        )
    )
    seal: dict[str, object] = {
        "schema": _SCHEMA,
        "cache_identity": cache_identity,
        "manifest_identity": manifest["manifest_identity"],
        "manifest_sha256": sha256_bytes((staging / "manifest.json").read_bytes()),
    }
    seal["seal_identity"] = sha256_bytes(canonical_json(seal))
    create_json_once(staging / "seal.json", seal)
    for name in ("manifest.json", "seal.json"):
        publication_states[name] = _publication_file_state(staging / name)
    directory_state = staging.stat()
    destination = release_root / "stage-cache" / identity.stage / cache_identity
    _publish_cache_once(staging, destination)
    descriptor = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    verified = verify_stage_cache(destination, expected=identity, full=False)
    published_directory = destination.lstat()
    if (
        (published_directory.st_dev, published_directory.st_ino)
        != (directory_state.st_dev, directory_state.st_ino)
        or verified.cache_identity != cache_identity
        or verified.manifest_identity != manifest["manifest_identity"]
        or any(
            _publication_file_state(destination / name) != state
            for name, state in publication_states.items()
        )
    ):
        raise ValueError("cache publication handoff changed authenticated files or metadata")
    return verified


def verify_stage_cache(
    cache_root: Path, *, expected: CacheInput | None = None, full: bool = True
) -> VerifiedCache:
    cache_root = cache_root.resolve()
    if cache_root.is_symlink() or not cache_root.is_dir():
        raise ValueError("stage cache must be a real directory")
    manifest_path = cache_root / "manifest.json"
    seal_path = cache_root / "seal.json"
    if manifest_path.is_symlink() or seal_path.is_symlink():
        raise ValueError("stage cache metadata cannot be symlinks")
    manifest = json.loads(manifest_path.read_bytes())
    seal = json.loads(seal_path.read_bytes())
    stored_manifest_identity = manifest.pop("manifest_identity")
    if sha256_bytes(canonical_json(manifest)) != stored_manifest_identity:
        raise ValueError("cache manifest identity mismatch")
    manifest["manifest_identity"] = stored_manifest_identity
    stored_seal_identity = seal.pop("seal_identity")
    if sha256_bytes(canonical_json(seal)) != stored_seal_identity:
        raise ValueError("cache seal identity mismatch")
    seal["seal_identity"] = stored_seal_identity
    if seal["manifest_identity"] != stored_manifest_identity or seal[
        "manifest_sha256"
    ] != sha256_bytes(manifest_path.read_bytes()):
        raise ValueError("cache seal does not authenticate manifest")
    if expected is not None:
        if manifest["semantic_inputs"] != expected.semantic_inputs():
            raise ValueError("cache semantic inputs differ")
        if manifest["builder_semantic_closure"] != cache_builder_semantic_closure():
            raise ValueError("cache builder semantic closure differs")
    for receipt in manifest["files"]:
        item = dict(receipt)
        relative = Path(str(item["path"]))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("cache file path is not canonical")
        path = cache_root / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError("cache shard is not a regular file")
        if path.stat().st_size != item["size"]:
            raise ValueError("cache shard size mismatch")
        if full and _file_digest(path) != item["container_sha256"]:
            raise ValueError("cache shard container digest mismatch")
        if full and path.suffix == ".npy":
            array = np.load(path, mmap_mode="r", allow_pickle=False)
            if _semantic_array_digest(array) != item["semantic_sha256"]:
                raise ValueError("cache shard semantic digest mismatch")
    cache_identity = sha256_bytes(
        canonical_json(
            {
                "manifest_identity": stored_manifest_identity,
                "semantic_inputs": manifest["semantic_inputs"],
                "builder_semantic_closure": manifest["builder_semantic_closure"],
            }
        )
    )
    if seal["cache_identity"] != cache_identity or cache_root.name != cache_identity:
        raise ValueError("cache content address mismatch")
    return VerifiedCache(
        cache_root,
        cache_identity,
        str(stored_manifest_identity),
        tuple(manifest["files"]),
    )


def load_stage_cache_batches(
    cache_root: Path, *, expected: CacheInput, full_verify: bool = True
) -> tuple[_StreamingResidualTrainingBatch, _StreamingPredictionBatch, VerifiedCache]:
    """Authenticate metadata, optionally rehash, and reconstruct grouped mmap batches."""
    cache = verify_stage_cache(cache_root, expected=expected, full=full_verify)
    manifest = json.loads((cache.root / "manifest.json").read_text())
    metadata = cast(Mapping[str, Mapping[str, object]], manifest["batch_metadata"])
    arrays: dict[str, dict[str, np.ndarray]] = {"train": {}, "predict": {}}
    for receipt in cache.files:
        relative = Path(str(receipt["path"]))
        if relative.suffix != ".npy":
            continue
        group = relative.parts[0]
        name = relative.name.split(".part-", 1)[0]
        if name != "decision_time":
            arrays[group][name] = np.load(cache.root / relative, mmap_mode="r", allow_pickle=False)
    training, prediction = _load_grouped_stage_cache_batches(
        training_arrays=arrays["train"],
        prediction_arrays=arrays["predict"],
        training_metadata=metadata["train"],
        prediction_metadata=metadata["predict"],
    )
    return training, prediction, cache


def write_verification_receipt(
    release_root: Path, cache: VerifiedCache, supervisor_epoch_id: str
) -> Path:
    entries: list[dict[str, object]] = []
    for receipt in cache.files:
        path = cache.root / str(receipt["path"])
        stat = path.stat(follow_symlinks=False)
        entries.append(
            {
                "path": str(path.resolve()),
                "device": stat.st_dev,
                "inode": stat.st_ino,
                "size": stat.st_size,
                "ctime_ns": stat.st_ctime_ns,
                "sha256": receipt["container_sha256"],
            }
        )
    payload: dict[str, object] = {
        "schema": _SCHEMA,
        "supervisor_epoch_id": supervisor_epoch_id,
        "cache_identity": cache.cache_identity,
        "files": entries,
    }
    payload["receipt_identity"] = sha256_bytes(canonical_json(payload))
    path = release_root.resolve() / "cache-verification" / f"{supervisor_epoch_id}.json"
    create_json_once(path, payload)
    return path
