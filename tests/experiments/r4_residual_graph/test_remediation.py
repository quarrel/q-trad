from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import uuid
from collections.abc import Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock

import numpy as np
import pytest
import torch

import experiments.r4_residual_graph.execution as execution_module
import experiments.r4_residual_graph.foundation as foundation_module
import experiments.r4_residual_graph.stage_cache as stage_cache
import experiments.r4_residual_graph.supervisor as supervisor_module
import experiments.r4_residual_graph.tensor as tensor_module
import experiments.r4_residual_graph.tensor_store as tensor_store
from experiments.r4_residual_graph import evaluation as evaluation_module
from experiments.r4_residual_graph.attempt_artifacts import (
    AttemptIdentity,
    CreateOnlyAttemptJournal,
    canonical_json,
    create_json_once,
    publish_attempt_bundle,
    seal_attempt_staging,
    verify_attempt_bundle,
)
from experiments.r4_residual_graph.cuda_boundary import DETERMINISTIC_CUDA_POLICY
from experiments.r4_residual_graph.execution import (
    G0ExecutionIdentity,
    _run_receipted_development_slot,
    _select_development_attempt,
    close_development_register,
)
from experiments.r4_residual_graph.foundation import ALL_INSTRUMENTS
from experiments.r4_residual_graph.grouped import (
    canonical_timestamp_groups,
    grouped_weighted_loss,
)
from experiments.r4_residual_graph.prepared_stage import (
    load_prepared_stage,
    persist_prepared_stage,
)
from experiments.r4_residual_graph.runtime import (
    FITTED_FAMILY_IDS,
    PRIMARY_SCHEDULE,
    TIMESTAMP_MATERIALISATION_POLICY,
    FrozenRuntimeConfig,
    PredictionBatch,
    ResidualTrainingBatch,
    _masked_tensor_identity,
    _selected_instrument_counts,
    _sha256,
    _StreamingPredictionBatch,
    _StreamingResidualTrainingBatch,
    _timestamp_row_chunks,
    training_preprocessor_identity,
)
from experiments.r4_residual_graph.stage_cache import (
    CacheInput,
    build_stage_cache,
    load_stage_cache_batches,
    verify_stage_cache,
    write_verification_receipt,
)
from experiments.r4_residual_graph.supervisor import (
    ProcessOwnership,
    next_development_slot,
    process_matches,
    read_process_ownership,
    reconcile_attempts,
    validate_epoch_cache_metadata,
    verify_epoch_caches,
    wrapper_receipt_identity,
    write_process_ownership,
    write_supervisor_session,
)
from experiments.r4_residual_graph.tensor import MaskedTensor, TensorContract
from experiments.r4_residual_graph.tensor_store import (
    build_raw_tensor_store,
    load_raw_tensor_store,
)
from tests.experiments.r4_residual_graph.test_runtime_authenticated_masks import (
    _prediction_fixture,
    _preprocessor,
    _support,
    _tensor,
    _training_fixture,
)


def _attempt(root: Path) -> AttemptIdentity:
    return AttemptIdentity(
        "r" * 64,
        "g" * 64,
        str(root.resolve()),
        "GRAPH_LSTM:17:DEV_2",
        "GRAPH_LSTM",
        17,
        "DEV_2",
        0,
        "PRIMARY",
    )


def test_raw_tensor_store_materialises_once_bounded_and_authenticates(tmp_path: Path) -> None:
    foundation, support, _, tensors = _training_fixture(False)

    class CountingMapping(Mapping[str, MaskedTensor]):
        def __init__(self) -> None:
            self.reads: list[str] = []

        def __getitem__(self, key: str) -> MaskedTensor:
            self.reads.append(key)
            return tensors_by_time[key]

        def __iter__(self) -> Iterator[str]:
            raise AssertionError("raw tensor materialisation must not iterate/copy the corpus")

        def __len__(self) -> int:
            return len(tensors_by_time)

        def worker_rows(self) -> Mapping[str, Sequence[Mapping[str, Any]]]:
            return {}

    tensors_by_time = {tensor.decision_time.isoformat(): tensor for _, tensor in tensors}
    source = CountingMapping()
    keys = tuple(tensors_by_time)
    store = build_raw_tensor_store(
        tmp_path / "raw",
        source,
        keys=keys,
        source_identity=foundation.content_identity,
        support_input_identity=support.identity,
    )
    materialisation = store.manifest["materialisation"]
    assert source.reads == []  # dereferencing is worker-owned, never parent-owned
    assert materialisation["executor"] == "spawn-process-shared-mmap"
    assert 1 <= materialisation["observed_worker_processes"] <= 8
    assert materialisation["maximum_workers"] == len(keys)
    assert materialisation["aggregate_worker_peak_pss_kib"] > 0
    assert materialisation["aggregate_worker_peak_uss_kib"] > 0
    assert materialisation["parent_pss_kib"] > 0
    assert materialisation["parent_uss_kib"] > 0
    loaded = load_raw_tensor_store(store.root)
    assert tuple(loaded) == keys
    for key in keys:
        assert loaded[key].decision_time.isoformat() == key

    raw_values = store.root / "values.npy"
    corrupted = bytearray(raw_values.read_bytes())
    corrupted[-1] ^= 1
    raw_values.write_bytes(corrupted)
    with pytest.raises(ValueError, match="raw tensor store container drifted"):
        load_raw_tensor_store(store.root)


def _raw_publication_source(monkeypatch: pytest.MonkeyPatch, rows: int) -> dict[str, MaskedTensor]:
    source = {}
    for index in range(rows):
        timestamp = datetime(2026, 5, 16, 14, 6, tzinfo=UTC) + timedelta(minutes=index)
        tensor = _tensor(timestamp, inactive_target=index % 2 == 0)
        values = tensor.values.copy()
        value_mask = tensor.value_mask.copy()
        values[0, 1, 0] = index + 0.25
        value_mask[0, 1, 1] = False
        source[timestamp.isoformat()] = replace(tensor, values=values, value_mask=value_mask)

    def worker_source(source: Mapping[str, MaskedTensor], path: Path) -> dict[str, tuple[int, int]]:
        path.write_bytes(b"synthetic worker source")
        return {}

    def materialise(
        path: Path, index: dict[str, tuple[int, int]], keys: Sequence[str]
    ) -> Iterator[tuple[str, MaskedTensor, int, int, int]]:
        yield from ((key, source[key], 1, 2, 1) for key in keys)

    monkeypatch.setattr(tensor_store, "_write_worker_source", worker_source)
    monkeypatch.setattr(tensor_store, "_ordered_materialisation", materialise)
    return source


def _preparation_support_corpus(
    monkeypatch: pytest.MonkeyPatch,
    source: dict[str, MaskedTensor],
) -> tuple[tensor_module.AuthenticatedSupportCorpus, foundation_module.Lab0Capsule]:
    import polars as pl

    parent = foundation_module.Lab0Capsule._create(
        foundation_module._LAB0_SEAL,
        manifest_path=Path("synthetic-manifest"),
        manifest_sha256="b" * 64,
        manifest={},
        instruments=tuple(ALL_INSTRUMENTS),
        child_identities=(),
    )
    object.__setattr__(parent, "_authenticated_parent", foundation_module._LAB0_SEAL)
    rows = []
    for key in source:
        timestamp = datetime.fromisoformat(key)
        block = next(
            name
            for name, (start, end) in tensor_module._STAGE_WINDOWS.items()
            if start <= timestamp < end
        )
        for instrument in ALL_INSTRUMENTS:
            rows.append(
                {
                    **dict.fromkeys(tensor_module._SUPPORT_ALLOWED_COLUMNS),
                    "instrument_id": instrument,
                    "decision_time": timestamp,
                    "block": block,
                    "target_valid": True,
                    "target_available_at": timestamp + timedelta(minutes=15),
                }
            )
    frame = pl.DataFrame(rows)
    # Stub only parent I/O; the corpus authentication and source derivation are real.
    monkeypatch.setattr(foundation_module, "load_parent_rows", lambda *_args, **_kwargs: frame)
    corpus = tensor_module.authenticate_support_corpus(frame.reverse(), parent)
    return corpus, parent


def _preparation_support(
    corpus: tensor_module.AuthenticatedSupportCorpus,
    parent: foundation_module.Lab0Capsule,
    source: Mapping[str, MaskedTensor],
) -> tuple[tensor_module.SupportInputCapability, tensor_module.SupportRecord]:
    support_input = execution_module._build_execution_support_input(
        corpus, parent, tuple(datetime.fromisoformat(key) for key in source)
    )
    support = tensor_module.bind_support_tensor_identities(
        support_input, {key: _masked_tensor_identity(tensor) for key, tensor in source.items()}
    )
    return support_input, support


@pytest.mark.parametrize("sizes", [(3, 1, 2), (7, 2, 5)])
def test_preparation_partitions_defer_content_to_one_global_fit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sizes: tuple[int, int, int]
) -> None:
    source = _raw_publication_source(monkeypatch, sizes[0])
    keys = tuple(source)
    corpus, parent = _preparation_support_corpus(monkeypatch, source)
    support_input, _ = _preparation_support(corpus, parent, source)
    supports = {
        name: (_preparation_support(corpus, parent, dict(tuple(source.items())[:size]))[1], parent)
        for name, size in zip(("GLOBAL", "DEV_2", "DEV_3"), sizes, strict=True)
    }
    assert len({support.source_identity for support, _ in supports.values()}) == 3
    for support, _ in supports.values():
        assert (
            support.source_identity
            == hashlib.sha256(
                tensor_module._canonical_bytes(
                    {
                        "manifest_sha256": corpus.authentication[0],
                        "child_closure_sha256": corpus.authentication[1],
                        "source_content_identity": corpus.authentication[2],
                        "support_keys": support.keys,
                    }
                )
            ).hexdigest()
        )
        assert support.row_content_identity != corpus.authentication[2]
    store = build_raw_tensor_store(
        tmp_path,
        source,
        keys,
        support_input_identity=support_input.identity,
        source_identity=support_input.support.source_identity,
    )
    ordinary = {
        name: execution_module._build_training_partition(
            {"support": support, "parent": parent, "tensors": store}
        )[0]
        for name, (support, parent) in supports.items()
    }
    expected = tensor_module.fit_training_preprocessors(ordinary)
    reads: list[str] = []
    validations: list[str] = []
    finite_scans = 0
    getitem = tensor_store.RawTensorStore.__getitem__
    validate = MaskedTensor.__post_init__
    isfinite = np.isfinite

    def counted_finite(values: np.ndarray) -> np.ndarray:
        nonlocal finite_scans
        if values.ndim == 3:
            finite_scans += 1
        return isfinite(values)

    def counted_getitem(self: tensor_store.RawTensorStore, key: str) -> MaskedTensor:
        reads.append(key)
        return getitem(self, key)

    def counted_validation(self: MaskedTensor) -> None:
        validations.append(self.decision_time.isoformat())
        validate(self)

    monkeypatch.setattr(tensor_store.RawTensorStore, "__getitem__", counted_getitem)
    monkeypatch.setattr(MaskedTensor, "__post_init__", counted_validation)
    monkeypatch.setattr(np, "isfinite", counted_finite)
    with tensor_store._preparation_partition_construction(
        store, corpus, support_input
    ) as capability:
        partitions = {
            name: execution_module._build_training_partition(
                {
                    "support": support,
                    "parent": parent,
                    "tensors": store,
                    "_preparation_capability": capability,
                }
            )[0]
            for name, (support, parent) in supports.items()
        }
        assert reads == validations == []
        assert finite_scans == 0
        actual = tensor_module.fit_training_preprocessors(partitions)
        assert reads == validations == list(keys)
        assert finite_scans == len(keys)
    for name in partitions:
        assert partitions[name].row_keys == ordinary[name].row_keys
        assert partitions[name].tensor_identities == ordinary[name].tensor_identities
        assert partitions[name].seal == ordinary[name].seal
        np.testing.assert_array_equal(actual[name].means, expected[name].means)
        np.testing.assert_array_equal(actual[name].scales, expected[name].scales)
        assert training_preprocessor_identity(actual[name]) == training_preprocessor_identity(
            expected[name]
        )
    with pytest.raises(TypeError, match="inactive"):
        tensor_module.fit_training_preprocessors(partitions)
    with (
        pytest.raises(TypeError, match="unused authenticated"),
        tensor_store._preparation_partition_construction(store, corpus, support_input),
    ):
        raise AssertionError("publication capability must not be reusable")
    # Public dictionaries stay fresh; standalone validation still traverses payloads.
    public_identities = store.tensor_identities
    public_identities[keys[0]] = "0" * 64
    assert store.tensor_identities[keys[0]] != "0" * 64
    partitions["GLOBAL"]._validate()
    assert reads == validations == list(keys) * 2


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_capability",
        "wrong_capability",
        "direct_store",
        "loaded_store",
        "other_store",
        "provider",
        "identity",
        "source",
        "missing_key",
        "reordered",
        "duplicate",
        "naive",
        "metadata",
        "shape",
        "dtype",
        "index",
        "cutoff",
        "terminal",
        "parent",
        "support",
        "support_contract",
        "support_lookback",
        "after_construction_provider",
        "sequence_factory",
        "partition_capability",
        "sequence_capability",
        "file_content",
        "identity_table",
    ],
)
def test_preparation_partition_handoff_rejects_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    source = _raw_publication_source(monkeypatch, 3)
    corpus, parent = _preparation_support_corpus(monkeypatch, source)
    support_input, support = _preparation_support(corpus, parent, source)
    store = build_raw_tensor_store(
        tmp_path,
        source,
        tuple(source),
        support_input_identity=support_input.identity,
        source_identity=support.source_identity,
    )
    context: dict[str, Any] = {"support": support, "parent": parent, "tensors": store}
    if mutation in {"direct_store", "loaded_store"}:
        other = (
            tensor_store.RawTensorStore(store.root, store.manifest)
            if mutation == "direct_store"
            else load_raw_tensor_store(store.root)
        )
        with (
            pytest.raises(TypeError, match="unused authenticated"),
            tensor_store._preparation_partition_construction(other, corpus, support_input),
        ):
            raise AssertionError("independent construction must not mint preparation authority")
        return
    with tensor_store._preparation_partition_construction(
        store, corpus, support_input
    ) as capability:
        context["_preparation_capability"] = capability
        if mutation == "missing_capability":
            context["_preparation_capability"] = None
        elif mutation == "wrong_capability":
            context["_preparation_capability"] = object()
        elif mutation == "other_store":
            context["tensors"] = tensor_store.RawTensorStore(store.root, store.manifest)
        elif mutation == "provider":
            context["tensors"] = source
        elif mutation == "identity":
            object.__setattr__(
                support, "tensor_identities", tuple((key, "0" * 64) for key in source)
            )
        elif mutation == "source":
            object.__setattr__(support, "source_identity", "0" * 64)
        elif mutation in {"missing_key", "reordered", "duplicate", "naive"}:
            keys = support.keys
            changed = {
                "missing_key": (*keys[:-1], "2026-05-16T14:59:00+00:00"),
                "reordered": tuple(reversed(keys)),
                "duplicate": (keys[0], keys[0], keys[2]),
                "naive": tuple(key.removesuffix("+00:00") for key in keys),
            }[mutation]
            object.__setattr__(support, "keys", changed)
        elif mutation == "metadata":
            store.manifest["timestamps"] += 1
        elif mutation == "shape":
            store._values.shape = (3, 20, 61, 26)
        elif mutation == "dtype":
            store._value_mask.dtype = np.uint8
        elif mutation == "index":
            store._index[support.keys[0]] = 1
        elif mutation == "parent":
            object.__setattr__(parent, "manifest_sha256", "0" * 64)
        elif mutation == "support":
            object.__setattr__(support, "child_closure_sha256", "0" * 64)
        elif mutation == "support_contract":
            object.__setattr__(support, "contract_identity", "0" * 64)
        elif mutation == "support_lookback":
            object.__setattr__(support, "lookback_minutes", 0)
        elif mutation in {"file_content", "identity_table"}:
            path = store.root / (
                "values.npy" if mutation == "file_content" else "tensor-identities.npy"
            )
            payload = bytearray(path.read_bytes())
            payload[-1] ^= 1
            path.write_bytes(payload)
        with pytest.raises((TypeError, ValueError, KeyError)):
            partition, sequence = execution_module._build_training_partition(context)
            if mutation == "cutoff":
                tensor_module.TrainingTensorPartition(
                    sequence,
                    support.keys,
                    support.source_identity,
                    support.identity,
                    datetime.fromisoformat(support.keys[0]),
                    partition.tensor_identities,
                    _seal=tensor_module._TRAINING_PARTITION_SEAL,
                )
            elif mutation == "terminal":
                tensor_module.TrainingTensorPartition(
                    sequence,
                    support.keys,
                    support.source_identity,
                    support.identity,
                    foundation_module.TERMINAL_START,
                    partition.tensor_identities,
                    _seal=tensor_module._TRAINING_PARTITION_SEAL,
                )
            elif mutation == "after_construction_provider":
                object.__setattr__(partition, "tensors", tuple(source.values()))
                tensor_module.fit_training_preprocessors({"GLOBAL": partition})
            elif mutation == "sequence_factory":
                cast(
                    tensor_store._PreparationTensorSequence, sequence
                )._factory = source.__getitem__
                tensor_module.fit_training_preprocessors({"GLOBAL": partition})
            elif mutation == "partition_capability":
                object.__delattr__(partition, "_preparation_sequence")
                tensor_module.fit_training_preprocessors({"GLOBAL": partition})
            elif mutation == "sequence_capability":
                object.__setattr__(sequence, "_capability", None)
                tensor_module.fit_training_preprocessors({"GLOBAL": partition})


@pytest.mark.parametrize("boundary", ["construction", "pre_fit"])
@pytest.mark.parametrize("forgery", ["arbitrary", "global"])
def test_preparation_stage_source_is_independently_derived(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str, forgery: str
) -> None:
    source = _raw_publication_source(monkeypatch, 3)
    corpus, parent = _preparation_support_corpus(monkeypatch, source)
    global_input, global_support = _preparation_support(corpus, parent, source)
    _, support = _preparation_support(corpus, parent, dict(tuple(source.items())[:1]))
    store = build_raw_tensor_store(
        tmp_path,
        source,
        tuple(source),
        support_input_identity=global_input.identity,
        source_identity=global_support.source_identity,
    )
    forged = "0" * 64 if forgery == "arbitrary" else global_support.source_identity
    with tensor_store._preparation_partition_construction(
        store, corpus, global_input
    ) as capability:
        context = {
            "support": support,
            "parent": parent,
            "tensors": store,
            "_preparation_capability": capability,
        }
        if boundary == "construction":
            object.__setattr__(support, "source_identity", forged)
            support._validate()  # Provenance/format alone does not authenticate this digest.
            with pytest.raises(ValueError, match="preparation source identity"):
                execution_module._build_training_partition(context)
        else:
            partition, _ = execution_module._build_training_partition(context)
            object.__setattr__(partition, "source_identity", forged)
            object.__setattr__(
                partition,
                "seal",
                _sha256(
                    {
                        "row_keys": partition.row_keys,
                        "source_identity": forged,
                        "support_identity": partition.support_identity,
                        "training_cutoff": partition.training_cutoff.isoformat(),
                        "tensor_identities": partition.tensor_identities,
                    }
                ),
            )
            with pytest.raises(ValueError, match="preparation source identity"):
                tensor_module.fit_training_preprocessors({"DEV_2": partition})


@pytest.mark.parametrize(
    "mutation",
    [
        "corpus",
        "corpus_seal",
        "global_keys",
        "global_source",
        "publication_source",
        "publication_input",
        "global_input",
    ],
)
def test_preparation_source_handoff_binds_corpus_global_and_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    source = _raw_publication_source(monkeypatch, 3)
    corpus, parent = _preparation_support_corpus(monkeypatch, source)
    support_input, support = _preparation_support(corpus, parent, source)
    store = build_raw_tensor_store(
        tmp_path,
        source,
        tuple(source),
        support_input_identity="0" * 64
        if mutation == "publication_input"
        else support_input.identity,
        source_identity="0" * 64 if mutation == "publication_source" else support.source_identity,
    )
    if mutation == "corpus":
        # Another validly authenticated parent corpus with identical keys but changed content.
        import polars as pl

        rows = pl.DataFrame(corpus.supplied_rows).with_columns(pl.lit(False).alias("target_valid"))
        monkeypatch.setattr(foundation_module, "load_parent_rows", lambda *_args, **_kwargs: rows)
        corpus = tensor_module.authenticate_support_corpus(rows, parent)
    elif mutation == "corpus_seal":
        object.__setattr__(corpus, "_seal", object())
    elif mutation == "global_keys":
        support_input, _ = _preparation_support(corpus, parent, dict(tuple(source.items())[:1]))
    elif mutation == "global_source":
        object.__setattr__(support_input.support, "source_identity", "0" * 64)
    elif mutation == "global_input":
        object.__setattr__(support_input.support, "row_content_identity", "0" * 64)
    with (
        pytest.raises((TypeError, ValueError), match=r"sealed|GLOBAL support-input binding"),
        tensor_store._preparation_partition_construction(store, corpus, support_input),
    ):
        raise AssertionError("invalid source handoff must not grant preparation authority")
    assert store._preparation is None


@pytest.mark.parametrize("mutation", ["finite", "availability", "node", "decision_time"])
def test_preparation_fit_validates_actual_tensors_and_revokes_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    source = _raw_publication_source(monkeypatch, 1)
    tensor = next(iter(source.values()))
    # Simulate a broken materialiser: publication authenticates bytes, not semantic validity.
    if mutation == "finite":
        values = tensor.values.copy()
        values[0, 1, 0] = np.inf
        object.__setattr__(tensor, "values", values)
    elif mutation == "availability":
        availability = tensor.availability_mask.copy()
        availability[0, 1, 0] = False
        object.__setattr__(tensor, "availability_mask", availability)
    elif mutation == "node":
        node = tensor.node_mask.copy()
        node[0, 1] = False
        object.__setattr__(tensor, "node_mask", node)
    corpus, parent = _preparation_support_corpus(monkeypatch, source)
    support_input, support = _preparation_support(corpus, parent, source)
    store = build_raw_tensor_store(
        tmp_path,
        source,
        tuple(source),
        support_input_identity=support_input.identity,
        source_identity=support.source_identity,
    )
    if mutation == "decision_time":
        getitem = tensor_store.RawTensorStore.__getitem__

        def wrong_time(self: tensor_store.RawTensorStore, key: str) -> MaskedTensor:
            actual = getitem(self, key)
            object.__setattr__(actual, "decision_time", actual.decision_time + timedelta(minutes=1))
            return actual

        monkeypatch.setattr(tensor_store.RawTensorStore, "__getitem__", wrong_time)
    messages = {
        "finite": "non-finite",
        "availability": "unavailable feature",
        "node": "unavailable nodes",
        "decision_time": "decision time does not match",
    }
    capability = store._preparation
    assert capability is not None
    with (
        pytest.raises(ValueError, match=messages[mutation]),
        tensor_store._preparation_partition_construction(
            store, corpus, support_input
        ) as capability,
    ):
        partition, _ = execution_module._build_training_partition(
            {
                "support": support,
                "parent": parent,
                "tensors": store,
                "_preparation_capability": capability,
            }
        )
        tensor_module.fit_training_preprocessors({"GLOBAL": partition})
    assert not capability.active
    with pytest.raises(TypeError, match="inactive"):
        execution_module._build_training_partition(
            {
                "support": support,
                "parent": parent,
                "tensors": store,
                "_preparation_capability": capability,
            }
        )


@pytest.mark.parametrize("mutation", ["naive", "reordered", "duplicate"])
def test_preparation_publication_requires_canonical_timestamps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    source = _raw_publication_source(monkeypatch, 2)
    if mutation == "naive":
        source = {key.removesuffix("+00:00"): tensor for key, tensor in source.items()}
        # Keep real publication; only the synthetic worker's source key representation changes.
        monkeypatch.setattr(
            tensor_store,
            "_ordered_materialisation",
            lambda path, index, keys: iter((key, source[key], 1, 2, 1) for key in keys),
        )
    keys = tuple(source)
    if mutation == "reordered":
        keys = tuple(reversed(keys))
    elif mutation == "duplicate":
        keys = (keys[0], keys[0])
    with pytest.raises(ValueError, match="timestamps"):
        build_raw_tensor_store(
            tmp_path, source, keys, support_input_identity="support", source_identity="a" * 64
        )


@pytest.mark.parametrize("rows", [1, 4])
def test_raw_build_hashes_once_and_preserves_public_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rows: int
) -> None:
    source = _raw_publication_source(monkeypatch, rows)
    file_calls: list[str] = []
    semantic_calls: list[str] = []
    digest = tensor_store._file_digest
    semantic_digest = tensor_store._semantic_array_digest
    fsync = os.fsync
    synced: list[Path] = []

    def container(path: Path) -> str:
        file_calls.append(path.name)
        return digest(path)

    def semantic(array: np.ndarray) -> str:
        assert isinstance(array, np.memmap)
        semantic_calls.append(Path(str(array.filename)).name)
        return semantic_digest(array)

    def sync(fd: int) -> None:
        synced.append(Path(os.readlink(f"/proc/self/fd/{fd}")))
        fsync(fd)

    monkeypatch.setattr(tensor_store, "_file_digest", container)
    monkeypatch.setattr(tensor_store, "_semantic_array_digest", semantic)
    monkeypatch.setattr(os, "fsync", sync)
    store = build_raw_tensor_store(
        tmp_path, source, tuple(source), support_input_identity="support", source_identity="source"
    )
    names = sorted(item["path"] for item in store.manifest["files"])
    assert len(names) == 6
    assert file_calls == semantic_calls == names
    assert synced[-1] == tmp_path
    assert tuple(store) == tuple(source)
    assert store.tensor_identities == {
        key: _masked_tensor_identity(value) for key, value in source.items()
    }
    manifest = json.loads((store.root / "manifest.json").read_text())
    assert store.manifest == manifest
    identity = manifest.pop("store_identity")
    assert identity == tensor_store._identity(manifest) == store.root.name
    for key, expected in source.items():
        actual = store[key]
        assert actual.contract == expected.contract
        assert actual.decision_time == expected.decision_time
        for name in ("values", "value_mask", "availability_mask", "node_mask"):
            array = getattr(actual, name)
            assert isinstance(array, np.memmap)
            assert not array.flags.writeable
            np.testing.assert_array_equal(array, getattr(expected, name))
    loaded = load_raw_tensor_store(store.root, expected_identity=identity)
    assert file_calls == semantic_calls == names * 2
    assert loaded.manifest == store.manifest
    assert tuple(loaded) == tuple(store)
    assert loaded.tensor_identities == store.tensor_identities
    path = store.root / "values.npy"
    payload = bytearray(path.read_bytes())
    payload[-1] ^= 1
    path.write_bytes(payload)
    with pytest.raises(ValueError, match="container drifted"):
        load_raw_tensor_store(store.root, expected_identity=identity)


@pytest.mark.parametrize(
    "mutation",
    ["same_size", "replacement", "metadata", "manifest", "after_digest", "directory", "collision"],
)
def test_raw_build_rejects_changed_publication_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    source = _raw_publication_source(monkeypatch, 1)
    publish = tensor_store._publish_cache_once
    digest = tensor_store._file_digest

    def change(path: Path) -> None:
        state = path.stat()
        payload = bytearray(path.read_bytes())
        payload[-1] ^= 1
        path.write_bytes(payload)
        os.utime(path, ns=(state.st_atime_ns, state.st_mtime_ns))

    def changed_digest(path: Path) -> str:
        result = digest(path)
        if path.name == "values.npy":
            change(path)
        return result

    def changed_publish(staging: Path, destination: Path) -> None:
        if mutation == "collision":
            destination.mkdir()
            (destination / "owner").write_text("existing")
        publish(staging, destination)
        shard = destination / "values.npy"
        if mutation == "same_size":
            change(shard)
        elif mutation == "replacement":
            replacement = shard.with_suffix(".replacement")
            replacement.write_bytes(shard.read_bytes())
            replacement.replace(shard)
        elif mutation == "metadata":
            shard.chmod(0o400)
        elif mutation == "manifest":
            change(destination / "manifest.json")
        elif mutation == "directory":
            moved = destination.with_name("original")
            destination.rename(moved)
            destination.mkdir()
            for path in moved.iterdir():
                os.link(path, destination / path.name)

    monkeypatch.setattr(tensor_store, "_publish_cache_once", changed_publish)
    if mutation == "after_digest":
        monkeypatch.setattr(tensor_store, "_file_digest", changed_digest)
    error = FileExistsError if mutation == "collision" else ValueError
    message = "already exists" if mutation == "collision" else "publication handoff"
    with pytest.raises(error, match=message):
        build_raw_tensor_store(
            tmp_path,
            source,
            tuple(source),
            support_input_identity="support",
            source_identity="source",
        )
    if mutation in {"after_digest", "collision"}:
        failure = json.loads(next(tmp_path.glob(".raw-tensor-*/failure.json")).read_text())
        assert failure["status"] == "FAILED"
        assert failure["exception_type"] == error.__name__
    if mutation == "collision":
        assert next(tmp_path.glob("*/owner")).read_text() == "existing"


def test_sealed_bundle_publishes_atomically_and_detects_corruption(tmp_path: Path) -> None:
    attempt = _attempt(tmp_path)
    staging = tmp_path / "staging" / attempt.identity
    staging.mkdir(parents=True)
    (staging / "model.pt").write_bytes(b"model")
    (staging / "prediction.json").write_text('{"a":1}\n')
    seal_attempt_staging(staging, attempt, required_paths=("model.pt", "prediction.json"))
    destination = tmp_path / "attempts" / attempt.slot_id / "attempt-0"
    publish_attempt_bundle(staging, destination, attempt)
    verify_attempt_bundle(destination, attempt)
    with pytest.raises(FileExistsError):
        publish_attempt_bundle(destination, destination, attempt)
    (destination / "prediction.json").write_text('{"a":2}\n')
    with pytest.raises(ValueError, match="payload digest"):
        verify_attempt_bundle(destination, attempt)


def test_bundle_seal_rejects_unlisted_or_symlink_payload(tmp_path: Path) -> None:
    attempt = _attempt(tmp_path)
    staging = tmp_path / "unexpected"
    staging.mkdir()
    (staging / "model.pt").write_bytes(b"model")
    (staging / "extra").write_bytes(b"extra")
    with pytest.raises(ValueError, match="differ"):
        seal_attempt_staging(staging, attempt, required_paths=("model.pt",))

    link_staging = tmp_path / "link"
    link_staging.mkdir()
    (link_staging / "source").write_bytes(b"x")
    (link_staging / "model.pt").symlink_to(link_staging / "source")
    with pytest.raises(ValueError, match="symlink"):
        seal_attempt_staging(link_staging, attempt, required_paths=("source", "model.pt"))


def test_create_only_journal_is_idempotent_and_conflicts_fail(tmp_path: Path) -> None:
    attempt = _attempt(tmp_path)
    journal = CreateOnlyAttemptJournal(tmp_path)
    started = journal.append(attempt, "STARTED", {"worker": "one"})
    assert journal.append(attempt, "STARTED", {"worker": "one"}) == started
    with pytest.raises(FileExistsError, match="conflicting"):
        journal.append(attempt, "STARTED", {"worker": "two"})
    journal.append(attempt, "SUCCEEDED", {"bundle": "sealed"})
    with pytest.raises(ValueError, match="opposite"):
        journal.append(attempt, "FAILED", {"failure": "late"})


def test_journal_requires_started_and_attempt_identity_is_root_bound(tmp_path: Path) -> None:
    attempt = _attempt(tmp_path)
    journal = CreateOnlyAttemptJournal(tmp_path)
    with pytest.raises(ValueError, match="requires STARTED"):
        journal.append(attempt, "FAILED", {})
    other = replace(attempt, output_root=str((tmp_path / "other").resolve()))
    assert attempt.identity != other.identity


def _cache_input() -> CacheInput:
    return CacheInput(
        "DEV_2",
        ("DEV_1",),
        "f" * 64,
        "s" * 64,
        "t" * 64,
        "p" * 64,
        "c" * 64,
        "n" * 64,
        "head-one",
        ("A", "B"),
        ("A", "B"),
        ("x", "y"),
    )


def _cache_batches(
    rows: int,
) -> tuple[_StreamingResidualTrainingBatch, _StreamingPredictionBatch]:
    del rows
    foundation, training_support, training_preprocessor, training_tensors = _training_fixture(False)
    grouped_training_tensors = {
        tensor.decision_time.isoformat(): tensor for _, tensor in training_tensors
    }
    training = ResidualTrainingBatch.from_authenticated_oof(
        tensors=grouped_training_tensors,
        foundation=foundation,
        support=training_support,
        preprocessor=training_preprocessor,
        stream=True,
    )
    prediction_support, prediction_preprocessor, prediction_tensors, _ = _prediction_fixture(False)
    grouped_prediction_tensors = {
        tensor.decision_time.isoformat(): tensor for _, tensor in prediction_tensors
    }
    input_identity = _sha256(
        {
            "support_identity": prediction_support.identity,
            "row_keys": prediction_support.target_keys,
            "tensor_identities": tuple(
                _masked_tensor_identity(grouped_prediction_tensors[key.rsplit("|", 1)[-1]])
                for key in prediction_support.target_keys
            ),
            "preprocessor_identity": training_preprocessor_identity(prediction_preprocessor),
            "policy": TIMESTAMP_MATERIALISATION_POLICY,
        }
    )
    prediction = PredictionBatch.from_authenticated_support(
        tensors=grouped_prediction_tensors,
        support=prediction_support,
        preprocessor=prediction_preprocessor,
        input_identity=input_identity,
        stream=True,
    )
    assert isinstance(training, _StreamingResidualTrainingBatch)
    assert isinstance(prediction, _StreamingPredictionBatch)
    return training, prediction


def test_prepared_stage_load_is_bound_to_authenticated_paths_and_identities(
    tmp_path: Path,
) -> None:
    foundation, original_support, _, _ = _training_fixture(False)
    rows_by_time: dict[str, list[Mapping[str, Any]]] = {}
    for row in foundation.rows.iter_rows(named=True):
        timestamp = row["decision_time"].isoformat()
        rows_by_time.setdefault(timestamp, []).append(row)

    class WorkerRowSource(Mapping[str, MaskedTensor]):
        def __getitem__(self, key: str) -> MaskedTensor:
            raise AssertionError(f"parent unexpectedly dereferenced {key}")

        def __iter__(self) -> Iterator[str]:
            return iter(rows_by_time)

        def __len__(self) -> int:
            return len(rows_by_time)

        def worker_rows(self) -> Mapping[str, Sequence[Mapping[str, Any]]]:
            return rows_by_time

    raw_store = build_raw_tensor_store(
        tmp_path / "input" / "raw-tensors",
        WorkerRowSource(),
        keys=tuple(rows_by_time),
        source_identity=foundation.content_identity,
        support_input_identity=original_support.identity,
    )
    raw_tensors = {datetime.fromisoformat(key): raw_store[key] for key in raw_store}
    times = tuple(raw_tensors)
    support = _support(times, raw_tensors)
    preprocessor = _preprocessor(raw_tensors[times[0]])
    training = ResidualTrainingBatch.from_authenticated_oof(
        tensors=raw_store,
        foundation=foundation,
        support=support,
        preprocessor=preprocessor,
        stream=True,
    )
    prediction_input_identity = _sha256(
        {
            "support_identity": support.identity,
            "row_keys": support.target_keys,
            "tensor_identities": tuple(
                _masked_tensor_identity(raw_store[key.rsplit("|", 1)[-1]])
                for key in support.target_keys
            ),
            "preprocessor_identity": training_preprocessor_identity(preprocessor),
            "policy": TIMESTAMP_MATERIALISATION_POLICY,
        }
    )
    prediction = PredictionBatch.from_authenticated_support(
        tensors=raw_store,
        support=support,
        preprocessor=preprocessor,
        input_identity=prediction_input_identity,
        stream=True,
    )
    assert isinstance(training, _StreamingResidualTrainingBatch)
    assert isinstance(prediction, _StreamingPredictionBatch)
    assert training.preprocessor is not None
    prepared_root = persist_prepared_stage(
        tmp_path / "input" / "prepared-development",
        stage="DEV_2",
        raw_store=raw_store,
        training=training,
        prediction=prediction,
        preprocessor=training.preprocessor,
        evaluation_block="DEV_2",
    )
    stage_identity = prepared_root.name
    loaded_training, loaded_prediction, _, manifest = load_prepared_stage(
        prepared_root,
        expected_stage_identity=stage_identity,
        expected_raw_store_path=raw_store.root,
        expected_raw_store_identity=raw_store.manifest["store_identity"],
        trusted_root=tmp_path,
    )
    assert loaded_training.input_identity == training.input_identity
    assert loaded_prediction.input_identity == prediction.input_identity
    assert manifest["stage_identity"] == stage_identity

    with pytest.raises(ValueError, match="identity"):
        load_prepared_stage(
            prepared_root,
            expected_stage_identity="0" * 64,
            expected_raw_store_path=raw_store.root,
            expected_raw_store_identity=raw_store.manifest["store_identity"],
            trusted_root=tmp_path,
        )

    actual_raw_store = raw_store.root.with_name(f"{raw_store.root.name}.actual")
    raw_store.root.rename(actual_raw_store)
    raw_store.root.symlink_to(actual_raw_store, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        load_prepared_stage(
            prepared_root,
            expected_stage_identity=stage_identity,
            expected_raw_store_path=raw_store.root,
            expected_raw_store_identity=raw_store.manifest["store_identity"],
            trusted_root=tmp_path,
        )
    raw_store.root.unlink()
    actual_raw_store.rename(raw_store.root)

    actual_prepared = prepared_root.with_name(f"{stage_identity}.actual")
    prepared_root.rename(actual_prepared)
    prepared_root.symlink_to(actual_prepared, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        load_prepared_stage(
            prepared_root,
            expected_stage_identity=stage_identity,
            expected_raw_store_path=raw_store.root,
            expected_raw_store_identity=raw_store.manifest["store_identity"],
            trusted_root=tmp_path,
        )


def test_authenticated_register_evaluates_all_canonical_prediction_payloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = _execution_identity()
    config = FrozenRuntimeConfig()
    expected_slots = tuple(
        sorted(
            f"{family}:{seed}:{stage}"
            for family, seed, stage in execution_module._EXPECTED_DEVELOPMENT_SLOTS
        )
    )
    assert len(expected_slots) == 30
    capsule = {
        "foundation_content_identity": "f" * 64,
        "preparation_preprocessor_identity": "p" * 64,
        "stages": {
            stage: {
                "preprocessor_identity": f"preprocessor-{stage}",
                "control_period": {"period": stage},
            }
            for stage in execution_module._DEVELOPMENT_STAGES
        },
    }
    succeeded: dict[str, AttemptIdentity] = {}
    payloads: dict[Path, dict[str, Any]] = {}
    for slot_id in expected_slots:
        family, seed_text, stage = slot_id.split(":")
        succeeded[slot_id] = AttemptIdentity(
            identity.identity,
            identity.output_root_identity,
            str(tmp_path.resolve()),
            slot_id,
            family,
            int(seed_text),
            stage,
            0,
            "PRIMARY",
        )
        bundle = tmp_path / "attempts" / slot_id / "attempt-0"
        bundle.mkdir(parents=True)
        for name in ("result.json", "model.pt", "model.json", "prediction.json"):
            (bundle / name).touch()
        prediction_identity = hashlib.sha256(slot_id.encode()).hexdigest()
        payloads[bundle / "result.json"] = {
            "slot_id": slot_id,
            "attempt": 0,
            "g0_identity": identity.identity,
            "outcomes_loaded": False,
            "prediction_closed": True,
            "prediction_identity": prediction_identity,
            "prediction_file_sha256": "d" * 64,
            "model_metadata_file_sha256": "d" * 64,
            "fit": {},
        }
        payloads[bundle / "model.json"] = {
            "training_batch_identity": "t" * 64,
            "config_identity": config.identity,
            "foundation_content_identity": capsule["foundation_content_identity"],
            "model_file_sha256": "d" * 64,
            "state_sha256": "d" * 64,
            "preprocessor_identity": f"preprocessor-{stage}",
            "prediction_input_identity": f"prediction-{stage}",
            "architecture": {},
            "architecture_identity": execution_module._sha256({}),
            "graph_identity": None,
        }
        payloads[bundle / "prediction.json"] = {
            "slot_id": slot_id,
            "prediction_identity": prediction_identity,
            "model_file_sha256": "d" * 64,
            "model_metadata_file_sha256": "d" * 64,
            "preprocessor_identity": f"preprocessor-{stage}",
            "input_identity": f"prediction-{stage}",
        }

    class Model:
        placed = False

        def load_state_dict(self, _state: Mapping[str, Any]) -> None:
            return None

        def to(self, device: str) -> Model:
            assert device == config.device
            self.placed = True
            return self

        def architecture(self) -> dict[str, Any]:
            return {}

        def adjacency_matrix(self) -> None:
            assert self.placed
            return None

    evaluated: list[dict[str, Any]] = []
    written: dict[Path, dict[str, Any]] = {}

    monkeypatch.setattr(
        execution_module, "_load_development_execution_capsule", lambda *_args: capsule
    )
    monkeypatch.setattr(
        execution_module,
        "_validate_development_attempt_ledger",
        lambda *_args, **_kwargs: succeeded,
    )
    monkeypatch.setattr(execution_module, "verify_attempt_bundle", lambda *_args: None)
    monkeypatch.setattr(execution_module, "_read_json", lambda path: payloads[path])
    monkeypatch.setattr(execution_module, "_file_digest", lambda _path: "d" * 64)
    monkeypatch.setattr(
        execution_module, "_validate_prediction_payload", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        execution_module,
        "_validate_fit_evidence",
        lambda *_args, **_kwargs: {"parameter_count": 1},
    )
    monkeypatch.setattr(
        execution_module,
        "_validate_development_artifact_metadata",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(execution_module, "_validate_linear_control_payload", lambda *_args: None)
    monkeypatch.setattr(execution_module, "_build_real_family_model", lambda _family: Model())
    monkeypatch.setattr(torch, "load", lambda *_args, **_kwargs: {"weight": torch.tensor(1.0)})
    monkeypatch.setattr(execution_module, "model_parameter_count", lambda _model: 1)
    monkeypatch.setattr(
        execution_module, "_load_authenticated_development_outcomes", lambda *_args: {}
    )
    monkeypatch.setattr(execution_module, "_development_journal_identity", lambda _root: "j" * 64)
    monkeypatch.setattr(execution_module, "_summarize_fit_evidence", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        execution_module,
        "_write_json_once",
        lambda path, payload: written.__setitem__(path, payload),
    )
    monkeypatch.setattr(
        execution_module,
        "_validate_development_register_payload",
        lambda *_args, **_kwargs: None,
    )

    def evaluate(
        predictions: Sequence[dict[str, Any]],
        _outcomes: Mapping[str, Any],
        *,
        identities: Mapping[str, Any],
    ) -> dict[str, Any]:
        assert identities["g0_identity"] == identity.identity
        evaluated.extend(predictions)
        return {}

    monkeypatch.setattr(evaluation_module, "evaluate_development_register", evaluate)

    register_identity = execution_module._close_authenticated_register_impl(
        tmp_path, identity, config=config
    )

    assert [payload["slot_id"] for payload in evaluated] == list(expected_slots)
    assert len({payload["prediction_identity"] for payload in evaluated}) == 30
    register_path = tmp_path / "register" / "development-register.json"
    assert written[register_path]["register_identity"] == register_identity
    assert set(written[register_path]["artifact_hashes"]) == set(expected_slots)


def test_stage_cache_is_content_addressed_mmap_verified_and_cross_head_adoptable(
    tmp_path: Path,
) -> None:
    identity = _cache_input()
    training, prediction = _cache_batches(3)
    cache = build_stage_cache(
        tmp_path,
        identity,
        training_batch=training,
        prediction_batch=prediction,
        build_id="build-1",
    )
    loaded_training, loaded_prediction, _ = load_stage_cache_batches(cache.root, expected=identity)
    assert loaded_training.content_identity != training.content_identity
    assert set(int(code) for code in loaded_training.training_blocks) == {0}
    assert loaded_training.training_block_order == ("DEV_1",)
    assert tuple(loaded_training.row_keys) == tuple(
        key
        for key, block in zip(training.row_keys, training.training_blocks, strict=True)
        if block == "DEV_1"
    )
    assert loaded_prediction.content_identity == prediction.content_identity
    verified = verify_stage_cache(cache.root, expected=replace(identity, producer_head="head-two"))
    assert verified.cache_identity == cache.cache_identity
    mmap = np.load(cache.root / "train" / "values.part-00000.npy", mmap_mode="r")
    assert isinstance(mmap, np.memmap)
    receipt = write_verification_receipt(tmp_path, verified, "epoch-1")
    assert json.loads(receipt.read_text())["cache_identity"] == cache.cache_identity


def test_cache_build_hashes_once_and_preserves_payloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = _cache_input()
    training, prediction = _cache_batches(1)
    references = {
        "train": stage_cache._materialise_batch(
            tmp_path / "reference",
            "train",
            stage_cache._project_training_batch(training, identity),
            training=True,
        ),
        "predict": stage_cache._materialise_batch(
            tmp_path / "reference",
            "predict",
            prediction,
            training=False,
        ),
    }
    file_calls: list[str] = []
    semantic_calls = 0
    file_digest = stage_cache._file_digest
    semantic_digest = stage_cache._semantic_array_digest
    validate_arrays = stage_cache._validate_arrays
    validated_values: list[tuple[int, ...]] = []

    class UnsliceableValues(np.ndarray):
        def __getitem__(self, key: Any) -> Any:
            raise AssertionError("post-materialisation validation traversed values")

    def validate_without_values_scan(arrays: Mapping[str, np.ndarray]) -> None:
        validated_values.append(arrays["values"].shape)
        validate_arrays({**arrays, "values": arrays["values"].view(UnsliceableValues)})

    def container(path: Path) -> str:
        file_calls.append(path.relative_to(path.parents[1]).as_posix())
        return file_digest(path)

    def semantic(array: np.ndarray) -> str:
        nonlocal semantic_calls
        semantic_calls += 1
        return semantic_digest(array)

    monkeypatch.setattr(stage_cache, "_file_digest", container)
    monkeypatch.setattr(stage_cache, "_semantic_array_digest", semantic)
    monkeypatch.setattr(stage_cache, "_validate_arrays", validate_without_values_scan)
    cache = build_stage_cache(
        tmp_path, identity, training_batch=training, prediction_batch=prediction, build_id="once"
    )
    assert len(file_calls) == len(set(file_calls)) == len(cache.files)
    assert semantic_calls == len(cache.files) - 1
    assert validated_values == [references[group]["values"].shape for group in ("train", "predict")]
    for group, arrays in references.items():
        for name, expected in arrays.items():
            actual = np.load(cache.root / group / f"{name}.part-00000.npy")
            np.testing.assert_array_equal(actual, expected)
    keys = tuple(
        json.loads(line)["key"]
        for line in (cache.root / "keys" / "part-00000.jsonl").read_text().splitlines()
    )
    assert keys == prediction.row_keys
    assert verify_stage_cache(cache.root, expected=identity) == cache
    assert len(file_calls) == 2 * len(cache.files)
    assert semantic_calls == 2 * (len(cache.files) - 1)
    with pytest.raises(FileExistsError):
        build_stage_cache(
            tmp_path,
            identity,
            training_batch=training,
            prediction_batch=prediction,
            build_id="collision",
        )


def _validation_arrays() -> dict[str, np.ndarray]:
    return {
        "values": np.zeros((2, 1, 1, 1), dtype="<f4"),
        "value_mask": np.ones((2, 1, 1, 1), dtype=np.bool_),
        "availability_mask": np.ones((2, 1, 1, 1), dtype=np.bool_),
        "node_mask": np.ones((2, 1), dtype=np.bool_),
        "row_timestamp": np.array([0, 1], dtype="<i8"),
        "target_nodes": np.zeros(2, dtype="<i8"),
        "target_mask": np.ones(2, dtype=np.bool_),
        "residuals": np.zeros(2, dtype="<f4"),
        "row_weights": np.ones(2, dtype="<f4"),
    }


@pytest.mark.parametrize("name", ["residuals", "row_weights"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), 1e40])
def test_cache_validation_retains_finite_training_arrays(name: str, value: float) -> None:
    arrays = _validation_arrays()
    with np.errstate(over="ignore"):
        arrays[name] = np.asarray([value, 1.0], dtype="<f4")
    with pytest.raises(ValueError, match="must be finite"):
        stage_cache._validate_arrays(arrays)


@pytest.mark.parametrize(
    ("name", "array", "message"),
    [
        ("outcome", np.zeros(2), "outcome-bearing"),
        ("_values", np.zeros(2), "not canonical"),
        ("values", np.zeros(2, dtype=object), "non-object"),
        ("values", np.zeros(2, dtype=">f4"), "little endian"),
        ("values", np.array(1.0), "not be scalar"),
        ("value_mask", np.ones(1, dtype=np.bool_), "timestamp dimension"),
        ("target_nodes", np.zeros(1, dtype="<i8"), "mapping arrays"),
        ("row_timestamp", np.array([-1, 1]), "out of bounds"),
        ("row_timestamp", np.array([0, 2]), "out of bounds"),
        ("residuals", np.zeros(1), "training arrays"),
        ("row_weights", np.ones(1), "training arrays"),
    ],
)
def test_cache_validation_retains_structure(name: str, array: np.ndarray, message: str) -> None:
    arrays = _validation_arrays()
    stage_cache._validate_arrays(arrays)
    arrays[name] = array
    with pytest.raises(ValueError, match=message):
        stage_cache._validate_arrays(arrays)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), 1e40])
@pytest.mark.parametrize("observed", [False, True])
def test_cache_transform_enforces_float32_finiteness(
    monkeypatch: pytest.MonkeyPatch, value: float, observed: bool
) -> None:
    from types import SimpleNamespace

    transformed = SimpleNamespace(
        values=np.array([value, 2.0]),
        value_mask=np.array([observed, True]),
        availability_mask=np.array([True, True]),
        node_mask=np.array([True]),
    )
    monkeypatch.setattr(
        stage_cache, "_WORKER_PREPROCESSOR", SimpleNamespace(transform=lambda _: transformed)
    )
    with np.errstate(over="ignore"):
        if np.isnan(value) and not observed:
            values, mask, _, _ = stage_cache._transform_one(object())
            np.testing.assert_array_equal(values, [0.0, 2.0])
            np.testing.assert_array_equal(mask, [False, True])
            assert values.dtype == np.float32
        else:
            with pytest.raises(ValueError, match="finite where observed"):
                stage_cache._transform_one(object())


@pytest.mark.parametrize("mutation", ["same_size", "replacement", "metadata", "after_digest"])
def test_cache_build_rejects_changed_publication_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    training, prediction = _cache_batches(1)
    publish = stage_cache._publish_cache_once
    digest = stage_cache._file_digest

    def change(path: Path) -> None:
        payload = bytearray(path.read_bytes())
        payload[-1] ^= 1
        path.write_bytes(payload)

    def changed_digest(path: Path) -> str:
        result = digest(path)
        if path.name == "values.part-00000.npy":
            change(path)
        return result

    def changed_publish(staging: Path, destination: Path) -> None:
        publish(staging, destination)
        shard = destination / "train" / "values.part-00000.npy"
        if mutation == "same_size":
            change(shard)
        elif mutation == "replacement":
            replacement = shard.with_suffix(".replacement")
            replacement.write_bytes(shard.read_bytes())
            replacement.replace(shard)
        elif mutation == "metadata":
            (destination / "manifest.json").chmod(0o400)

    monkeypatch.setattr(stage_cache, "_publish_cache_once", changed_publish)
    if mutation == "after_digest":
        monkeypatch.setattr(stage_cache, "_file_digest", changed_digest)
    with pytest.raises(ValueError, match="publication handoff"):
        build_stage_cache(
            tmp_path,
            _cache_input(),
            training_batch=training,
            prediction_batch=prediction,
            build_id="changed",
        )


def test_stage_cache_light_load_skips_full_file_rehash(tmp_path: Path) -> None:
    identity = _cache_input()
    training, prediction = _cache_batches(1)
    cache = build_stage_cache(
        tmp_path,
        identity,
        training_batch=training,
        prediction_batch=prediction,
        build_id="light-load",
    )
    shard = cache.root / "train" / "values.part-00000.npy"
    corrupted = bytearray(shard.read_bytes())
    corrupted[-1] ^= 1
    shard.write_bytes(corrupted)
    load_stage_cache_batches(cache.root, expected=identity, full_verify=False)
    with pytest.raises(ValueError, match="container digest"):
        verify_stage_cache(cache.root, expected=identity)


def test_cache_parallel_scheduler_is_ordered_once_and_bounded_to_eight() -> None:
    class Future:
        def __init__(self, owner: Executor, value: int) -> None:
            self.owner = owner
            self.value = value

        def result(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
            self.owner.pending -= 1
            item = np.asarray([self.value], dtype=np.float32)
            return item, item, item, item

    class Executor:
        def __init__(self) -> None:
            self.pending = 0
            self.maximum = 0
            self.calls: list[int] = []

        def submit(self, function: Any, task: int) -> Future:
            del function
            self.calls.append(task)
            self.pending += 1
            self.maximum = max(self.maximum, self.pending)
            return Future(self, task)

    executor = Executor()
    tasks = iter(range(20))
    results = list(stage_cache._ordered_parallel_transforms(cast(Any, executor), tasks, 8))
    assert [int(item[0][0]) for item in results] == list(range(20))
    assert executor.calls == list(range(20))
    assert executor.maximum == 8


def test_cache_timestamp_source_is_lazy_and_does_not_copy_corpus() -> None:
    class LazyTensors(Mapping[str, object]):
        def __init__(self) -> None:
            self.reads: list[str] = []

        def __getitem__(self, key: str) -> object:
            self.reads.append(key)
            return key

        def __iter__(self) -> Iterator[str]:
            raise AssertionError("full corpus iteration is forbidden")

        def __len__(self) -> int:
            return 3

    tensors = LazyTensors()
    batch = type("Batch", (), {"tensors": tensors})()
    sources = stage_cache._timestamp_sources(batch, ("a", "b", "c"))
    assert next(sources) == "a"
    assert tensors.reads == ["a"]
    assert list(sources) == ["b", "c"]
    sequence_batch = type(
        "SequenceBatch",
        (),
        {"tensors": (("skip", 0), ("a", 1), ("skip-too", 2), ("c", 3), ("tail", 4))},
    )()
    assert list(stage_cache._timestamp_sources(sequence_batch, ("a", "c"))) == [1, 3]
    source = __import__("inspect").getsource(stage_cache._materialise_batch)
    assert "dict(batch.tensors)" not in source
    assert "initializer=_initialise_transform_worker" in source
    assert "raw_manifest" in source
    assert "else iter(timestamps)" in source


def test_register_close_uses_authenticated_minimum_outcomes_not_raw_context() -> None:
    import inspect

    import experiments.r4_residual_graph.execution as execution

    close_source = inspect.getsource(execution._close_authenticated_register_impl)
    outcome_source = inspect.getsource(execution._load_authenticated_development_outcomes)
    assert "_load_real_context" not in close_source
    assert "_load_authenticated_development_outcomes" in close_source
    assert "foundation_file_sha256" in outcome_source
    expected_columns = 'columns=["block", "instrument_id", "decision_time", "target_return"]'
    assert expected_columns in outcome_source


def test_cache_prefetch_primes_n_plus_one_before_current_consumption() -> None:
    import experiments.r4_residual_graph.runtime as runtime

    class Completion:
        def __init__(self, index: int) -> None:
            self.index = index

        def query(self) -> bool:
            return self.index == 0

    class Buffer:
        def __init__(self) -> None:
            self.calls: list[tuple[int, ...]] = []
            self.waits: list[int] = []
            self.transferred_second = Event()
            self.transferred_third = Event()

        def transfer(
            self,
            arrays: Any,
            indices: list[int],
            telemetry: Any,
            **kwargs: Any,
        ) -> tuple[tuple[Any, ...], Completion]:
            del arrays, telemetry, kwargs
            self.calls.append(tuple(indices))
            if indices == [1]:
                self.transferred_second.set()
            if indices == [2]:
                self.transferred_third.set()
            return (tuple(indices),) * 4, Completion(indices[0])

        def wait_ready(self, completion: Completion) -> None:
            self.waits.append(completion.index)

    arrays = {"row_timestamp": np.asarray([0, 1, 2], dtype=np.int64)}
    buffer = Buffer()
    telemetry = runtime.RuntimeTelemetry()
    prefetched = runtime._prefetched_cached_chunks(
        arrays, iter(((0,), (1,), (2,))), cast(Any, buffer), telemetry
    )
    first = next(prefetched)
    assert buffer.transferred_second.wait(timeout=1.0)
    assert first[0] == (0,)
    assert buffer.calls == [(0,), (1,)]
    assert buffer.waits == [0]
    assert telemetry.prefetch_calls == 2
    assert telemetry.overlapped_prefetch_calls == 0
    second = next(prefetched)
    assert buffer.transferred_third.wait(timeout=1.0)
    assert second[0] == (1,)
    assert buffer.calls == [(0,), (1,), (2,)]
    assert buffer.waits == [0, 1]
    assert telemetry.overlapped_prefetch_calls == 1
    transfer_source = __import__("inspect").getsource(runtime._PinnedCudaDoubleBuffer.transfer)
    assert "wait_event" not in transfer_source


@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA ordering probe requires CUDA")
def test_cache_prefetch_cuda_stream_ordering() -> None:
    import experiments.r4_residual_graph.runtime as runtime

    class CudaBuffer:
        def __init__(self) -> None:
            self.stream = torch.cuda.Stream()
            self.completions: dict[int, Any] = {}
            self.second_enqueued = Event()

        def transfer(
            self,
            arrays: Any,
            indices: list[int],
            telemetry: Any,
            **kwargs: Any,
        ) -> tuple[tuple[Any, ...], Any]:
            del arrays, telemetry, kwargs
            completion = torch.cuda.Event(enable_timing=True)
            with torch.cuda.stream(self.stream):
                if indices == [1]:
                    torch.cuda._sleep(50_000_000)
                completion.record()
            self.completions[indices[0]] = completion
            if indices == [1]:
                self.second_enqueued.set()
            return (tuple(indices),) * 4, completion

        def wait_ready(self, completion: Any) -> None:
            torch.cuda.current_stream().wait_event(completion)

    arrays = {"row_timestamp": np.asarray([0, 1], dtype=np.int64)}
    buffer = CudaBuffer()
    telemetry = runtime.RuntimeTelemetry()
    prefetched = runtime._prefetched_cached_chunks(
        arrays, iter(((0,), (1,))), cast(Any, buffer), telemetry
    )
    assert next(prefetched)[0] == (0,)
    assert buffer.second_enqueued.wait(timeout=1.0)
    current_compute_started = torch.cuda.Event(enable_timing=True)
    current_compute_started.record()
    assert not buffer.completions[1].query()
    assert next(prefetched)[0] == (1,)
    next_compute_started = torch.cuda.Event(enable_timing=True)
    next_compute_started.record()
    next_compute_started.synchronize()
    assert current_compute_started.elapsed_time(buffer.completions[1]) > 0.0
    assert buffer.completions[1].elapsed_time(next_compute_started) >= 0.0
    assert telemetry.overlapped_prefetch_calls == 1


def test_cache_publication_is_atomic_no_clobber(tmp_path: Path) -> None:
    destination = tmp_path / "published"
    sources = [tmp_path / "first", tmp_path / "second"]
    for index, source in enumerate(sources):
        source.mkdir()
        (source / "owner").write_text(str(index))

    def publish(source: Path) -> str:
        stage_cache._publish_cache_once(source, destination)
        return source.name

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(publish, source) for source in sources]
    successes = [future.result() for future in futures if future.exception() is None]
    failures = [future.exception() for future in futures if future.exception() is not None]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], FileExistsError)
    assert (destination / "owner").read_text() in {"0", "1"}


def test_stage_cache_rejects_corruption_semantic_change_and_outcomes(tmp_path: Path) -> None:
    identity = _cache_input()
    training, prediction = _cache_batches(2)
    cache = build_stage_cache(
        tmp_path,
        identity,
        training_batch=training,
        prediction_batch=prediction,
        build_id="build-2",
    )
    shard = cache.root / "predict" / "values.part-00000.npy"
    payload = bytearray(shard.read_bytes())
    payload[-1] ^= 1
    shard.write_bytes(payload)
    with pytest.raises(ValueError, match="container digest"):
        verify_stage_cache(cache.root, expected=identity)
    assert isinstance(training.tensors, dict)
    invalid_tensor = next(iter(training.tensors.values()))
    assert isinstance(invalid_tensor, MaskedTensor)
    invalid_values = invalid_tensor.values.copy()
    invalid_values[0, 0, 0] = float("nan")
    object.__setattr__(invalid_tensor, "values", invalid_values)
    with pytest.raises(ValueError, match="finite"):
        build_stage_cache(
            tmp_path,
            identity,
            training_batch=training,
            prediction_batch=prediction,
            build_id="build-forbidden",
        )


def test_grouping_and_equal_instrument_loss_match_scalar_reference() -> None:
    assert canonical_timestamp_groups((1, 1, 2, 4, 4)) == ((0, 1), (2,), (3, 4))
    with pytest.raises(ValueError, match="chronological"):
        canonical_timestamp_groups((2, 1))
    prediction = torch.tensor([1.0, 2.0, 3.0], requires_grad=True)
    target = torch.tensor([0.0, 1.0, 1.0])
    nodes = torch.tensor([0, 0, 1])
    counts = torch.tensor([2, 1])
    grouped = grouped_weighted_loss(prediction, target, nodes, counts, 2)
    scalar = torch.stack(
        tuple(
            (prediction[index] - target[index]).square() / (2 * counts[nodes[index]])
            for index in range(3)
        )
    ).sum()
    torch.testing.assert_close(grouped, scalar)
    grouped.backward()
    assert prediction.grad is not None
    grouped_gradient = prediction.grad.detach().clone()
    prediction.grad = None
    scalar.backward()
    assert prediction.grad is not None
    torch.testing.assert_close(grouped_gradient, prediction.grad)


def test_supervisor_advances_only_after_sealed_terminal_journal(tmp_path: Path) -> None:
    journal = CreateOnlyAttemptJournal(tmp_path)
    assert next_development_slot(journal) is not None
    first = next_development_slot(journal)
    assert first is not None
    family, seed, stage = first
    attempt = AttemptIdentity(
        "r" * 64,
        "g" * 64,
        str(tmp_path.resolve()),
        f"{family}:{seed}:{stage}",
        family,
        seed,
        stage,
        0,
        "PRIMARY",
    )
    journal.append(attempt, "STARTED", {})
    with pytest.raises(RuntimeError, match="reconciled"):
        next_development_slot(journal)
    journal.append(attempt, "SUCCEEDED", {"bundle": "sealed"})
    assert next_development_slot(journal) != first


@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA is required")
@pytest.mark.parametrize(
    "family", ("FULLY_POOLED_LSTM", "LOCAL_LSTM", "GRAPH_LSTM", "GRAPH_TRANSFORMER", "GRAPH_TCN")
)
def test_five_family_grouped_cuda_exact_repeat_and_scalar_equivalence(family: str) -> None:
    qualification = """
import sys

import torch

from experiments.r4_residual_graph.grouped import (
    configure_deterministic_cuda,
    grouped_weighted_loss,
)

family = sys.argv[1]


def run() -> tuple[torch.Tensor, torch.Tensor]:
    configure_deterministic_cuda(17)
    device = torch.device("cuda")
    base = torch.arange(24, device=device, dtype=torch.float32).reshape(6, 4) / 24
    parameter = torch.nn.Parameter(torch.tensor(0.25, device=device))
    if family == "FULLY_POOLED_LSTM":
        prediction = base.mean(dim=1) * parameter
    elif family == "LOCAL_LSTM":
        prediction = base[:, 0] * parameter
    elif family == "GRAPH_LSTM":
        prediction = (base[:, 0] + base[:, 1]) * parameter
    elif family == "GRAPH_TRANSFORMER":
        prediction = torch.tanh(base.sum(dim=1)) * parameter
    else:
        prediction = (base[:, 1:] - base[:, :-1]).square().mean(dim=1) * parameter
    target = torch.linspace(0, 1, 6, device=device)
    nodes = torch.tensor([0, 1, 2, 0, 1, 2], device=device)
    counts = torch.tensor([2, 2, 2], device=device)
    loss = grouped_weighted_loss(prediction, target, nodes, counts, 3)
    loss.backward()
    assert parameter.grad is not None
    return prediction.detach().cpu(), parameter.grad.detach().cpu()


first = run()
second = run()
assert torch.equal(first[0], second[0])
assert torch.equal(first[1], second[1])
"""
    subprocess.run(
        [sys.executable, "-c", qualification, family],
        check=True,
        env=os.environ | {"CUBLAS_WORKSPACE_CONFIG": ":4096:8"},
        timeout=60,
    )


@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA is required")
def test_persist_real_model_moves_residual_prediction_to_cpu_before_ridge_addition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from experiments.r4_residual_graph.execution import G0ExecutionIdentity, _persist_real_model
    from experiments.r4_residual_graph.runtime import FrozenRuntimeConfig

    class Model(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.weight = torch.nn.Parameter(torch.tensor(1.0))

        def adjacency_matrix(self) -> None:
            return None

        def architecture(self) -> dict[str, object]:
            return {"family": "test"}

    config = FrozenRuntimeConfig()
    identity = G0ExecutionIdentity(
        "a" * 40,
        "b" * 64,
        "c" * 64,
        "d" * 64,
        config.identity,
        "e" * 64,
        "f" * 64,
    )
    attempt = AttemptIdentity(
        identity.release_identity,
        identity.identity,
        str(tmp_path),
        "FIXED_ECONOMIC_GRAPH_RESIDUAL:17:DEV_2",
        "FIXED_ECONOMIC_GRAPH_RESIDUAL",
        17,
        "DEV_2",
        0,
        "PRIMARY",
    )
    CreateOnlyAttemptJournal(tmp_path).append(attempt, "STARTED", {})
    monkeypatch.setattr(execution_module, "_build_real_family_model", lambda _family: Model())
    monkeypatch.setattr(
        "experiments.r4_residual_graph.runtime.predict_residual",
        lambda *_args, **_kwargs: torch.tensor([0.25, -0.5], device="cuda"),
    )
    _persist_real_model(
        tmp_path,
        "FIXED_ECONOMIC_GRAPH_RESIDUAL:17:DEV_2",
        Model(),
        {"parameter_count": 1},
        torch.tensor([0.25, -0.5], device="cuda"),
        identity,
        attempt_identity=attempt,
        prediction_batch=object(),
        config=config,
        foundation_content_identity="1" * 64,
        training_batch_identity="2" * 64,
        preprocessor_identity="3" * 64,
        prediction_input_identity="4" * 64,
        target_keys=("a", "b"),
        local_ridge_forecasts=(1.0, 2.0),
        fully_pooled_local_ridge_forecasts=(1.0, 2.0),
        linear_control_identity="5" * 64,
        linear_controls={"ridge": [1.0, 2.0]},
        linear_control_identities={"ridge": "6" * 64},
        linear_control_support_identity="7" * 64,
    )
    bundle = tmp_path / "attempts" / "FIXED_ECONOMIC_GRAPH_RESIDUAL:17:DEV_2" / "attempt-0"
    assert not (tmp_path / "model").exists()
    verify_attempt_bundle(bundle)
    result = json.loads((bundle / "prediction.json").read_text())
    assert result["total_forecast"] == pytest.approx([1.25, 1.5])


@pytest.fixture(autouse=True)
def _process_globals_remain_unpatched(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    del monkeypatch  # Keep the assertion before pytest restores test-local patches.
    originals = (Path.read_text, Path.read_bytes, Path.stat, uuid.uuid4, subprocess.Popen, os.write)
    yield
    assert (
        Path.read_text,
        Path.read_bytes,
        Path.stat,
        uuid.uuid4,
        subprocess.Popen,
        os.write,
    ) == originals


@pytest.mark.parametrize(
    "mutation", ["duplicate", "nested", "hash", "schema", "filename", "status", "attempt", "root"]
)
def test_journal_authenticates_all_records_before_filter_or_reconciliation(
    tmp_path: Path,
    mutation: str,
) -> None:
    journal = CreateOnlyAttemptJournal(tmp_path)
    family, seed, stage = PRIMARY_SCHEDULE[0]
    attempt = _select_development_attempt(
        tmp_path, journal, _execution_identity(), family=family, seed=seed, stage=stage
    )
    journal.append(attempt, "STARTED", {"nested": {"value": 1}})
    journal.append(attempt, "SUCCEEDED", {})
    path = journal.path / f"{attempt.identity}.SUCCEEDED.json"
    payload = json.loads(path.read_bytes())
    if mutation in {"duplicate", "nested"}:
        encoded = path.read_bytes()
        if mutation == "duplicate":
            encoded = b'{"schema":"wrong",' + encoded[1:]
        else:
            encoded = encoded.replace(b'"payload":{}', b'"payload":{"value":1,"value":2}')
        path.write_bytes(encoded)
    elif mutation == "filename":
        path.rename(journal.path / f"{'0' * 64}.SUCCEEDED.json")
    else:
        payload.pop("record_identity")
        if mutation == "schema":
            payload["schema"] = "wrong"
        elif mutation == "status":
            payload["status"] = "INVALIDATED"
        elif mutation == "attempt":
            payload["attempt"]["seed"] += 1
        elif mutation == "root":
            payload["attempt"]["output_root"] = str(tmp_path.parent)
        payload["record_identity"] = hashlib.sha256(canonical_json(payload)).hexdigest()
        if mutation == "hash":
            payload["record_identity"] = "0" * 64
        path.write_bytes(canonical_json(payload))
    staging = tmp_path / "staging/attempts/orphan"
    staging.mkdir(parents=True)
    (staging / "partial").write_bytes(b"retained")
    before = _retained_bytes(tmp_path)
    with pytest.raises(ValueError):
        reconcile_attempts(tmp_path, journal)
    with pytest.raises(ValueError):
        next_development_slot(journal)
    assert _retained_bytes(tmp_path) == before


@pytest.mark.parametrize("nested", [False, True])
def test_ownership_evidence_rejects_duplicate_fields(tmp_path: Path, nested: bool) -> None:
    payload: dict[str, Any] = {"schema": "test", "nested": {"value": 1}}
    payload["identity"] = hashlib.sha256(canonical_json(payload)).hexdigest()
    encoded = canonical_json(payload)
    encoded = (
        encoded.replace(b'"value":1', b'"value":0,"value":1')
        if nested
        else b'{"schema":"discarded",' + encoded[1:]
    )
    path = tmp_path / "evidence.json"
    path.write_bytes(encoded)
    with pytest.raises(ValueError, match="duplicate"):
        supervisor_module._read_ownership_evidence(path, "identity")


def _execution_identity() -> G0ExecutionIdentity:
    return G0ExecutionIdentity("a" * 40, "b" * 64, "c" * 64, "d" * 64, "e" * 64, "f" * 64, "0" * 64)


def test_retry_identity_is_shared_and_a_second_retry_is_rejected(tmp_path: Path) -> None:
    journal = CreateOnlyAttemptJournal(tmp_path)
    identity = _execution_identity()
    family, seed, stage = PRIMARY_SCHEDULE[0]
    primary = _select_development_attempt(
        tmp_path, journal, identity, family=family, seed=seed, stage=stage
    )
    assert (primary.attempt, primary.mode) == (0, "PRIMARY")
    journal.append(primary, "STARTED", {})
    journal.append(primary, "FAILED", {"reason": "interrupted"})
    retry = _select_development_attempt(
        tmp_path, journal, identity, family=family, seed=seed, stage=stage
    )
    assert (retry.attempt, retry.mode) == (1, "PRIMARY")
    journal.append(retry, "STARTED", {})
    journal.append(retry, "FAILED", {"reason": "interrupted again"})
    with pytest.raises(ValueError, match="already used its retry"):
        _select_development_attempt(
            tmp_path, journal, identity, family=family, seed=seed, stage=stage
        )


@pytest.fixture(scope="module")
def ownership_cache_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("ownership-caches")
    training, prediction = _cache_batches(1)
    dev2 = _cache_input()
    for cache_input in (dev2, replace(dev2, stage="DEV_3", training_blocks=("DEV_1", "DEV_2"))):
        build_stage_cache(
            root,
            cache_input,
            training_batch=training,
            prediction_batch=prediction,
            build_id=cache_input.stage,
        )
    context: dict[str, Any] = {"schema": "R4-P0-DEVELOPMENT-EXECUTION-CONTEXT-V1"}
    context["capsule_identity"] = hashlib.sha256(canonical_json(context)).hexdigest()
    create_json_once(root / "input" / "development-execution-context.json", context)
    return root


def _owned_attempt(
    root: Path,
    cache_template: Path,
    *,
    epoch: str = "origin",
    attempt: AttemptIdentity | None = None,
) -> tuple[AttemptIdentity, CreateOnlyAttemptJournal, ProcessOwnership]:
    if attempt is None:
        family, seed, stage = PRIMARY_SCHEDULE[0]
        attempt = replace(
            _attempt(root),
            family_id=family,
            seed=seed,
            stage=stage,
            slot_id=f"{family}:{seed}:{stage}",
        )
    if not (root / "stage-cache").exists():
        shutil.copytree(cache_template / "stage-cache", root / "stage-cache")
        shutil.copytree(cache_template / "input", root / "input")
    cache_identity = verify_epoch_caches(root, f"{epoch}-pre")[0]
    ownership = read_process_ownership(
        pid=os.getpid(),
        exact_head="a" * 40,
        g0_identity=attempt.g0_identity,
        output_root=root,
        slot_id=attempt.slot_id,
        attempt=attempt.attempt,
        wrapper_receipt_identity=wrapper_receipt_identity(epoch, cache_identity, attempt.identity),
        supervisor_epoch_id=epoch,
        cache_receipt_identity=cache_identity,
    )
    write_supervisor_session(
        root,
        epoch,
        exact_head=ownership.exact_head,
        g0_identity=attempt.g0_identity,
        cache_receipts=[cache_identity],
    )
    write_process_ownership(root, attempt, ownership)
    journal = CreateOnlyAttemptJournal(root)
    journal.append(
        attempt,
        "STARTED",
        {
            "supervisor_epoch_id": epoch,
            "cache_receipt_identity": cache_identity,
        },
    )
    return attempt, journal, ownership


def _retained_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_reconcile_monitors_live_owner_with_unsealed_staging(
    tmp_path: Path, ownership_cache_template: Path
) -> None:
    attempt, journal, ownership = _owned_attempt(tmp_path, ownership_cache_template)
    staging = tmp_path / "staging" / "attempts" / attempt.identity
    staging.mkdir(parents=True)
    (staging / "partial").write_bytes(b"unfinished")
    before = _retained_bytes(tmp_path)
    for _ in range(2):
        reconciled = reconcile_attempts(tmp_path, journal)
        assert reconciled.live == ownership
        assert reconciled.live is not None
        assert reconciled.live.identity == ownership.identity
        assert reconciled.recovered == ()
        assert staging.is_dir()
        assert _retained_bytes(tmp_path) == before


@pytest.mark.parametrize(
    "field",
    [
        "pid",
        "boot_id",
        "process_start_ticks",
        "executable_sha256",
        "arguments_sha256",
    ],
)
def test_reconcile_refuses_each_live_process_mismatch_before_any_mutation(
    tmp_path: Path,
    ownership_cache_template: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    attempt, journal, ownership = _owned_attempt(tmp_path, ownership_cache_template)
    observation = supervisor_module._observe_process_or_none(ownership.pid)
    assert observation is not None
    values = asdict(observation)
    values[field] = values[field] + 1 if isinstance(values[field], int) else "changed"
    monkeypatch.setattr(
        supervisor_module,
        "_observe_process_or_none",
        lambda _pid: supervisor_module._ProcessObservation(**values),
    )
    staging = tmp_path / "staging" / "attempts" / attempt.identity
    staging.mkdir(parents=True)
    (staging / "partial").write_bytes(b"unfinished")
    orphan = staging.parent / "before-started"
    orphan.mkdir()
    (orphan / "partial").write_bytes(b"also unfinished")
    before = _retained_bytes(tmp_path)
    with pytest.raises(RuntimeError, match="live process identity"):
        reconcile_attempts(tmp_path, journal)
    assert _retained_bytes(tmp_path) == before
    assert staging.is_dir() and orphan.is_dir()
    with pytest.raises(RuntimeError, match="reconciled"):
        next_development_slot(journal)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "malformed",
        "incomplete",
        "empty",
        "wrong_type",
        "self_hash",
        "epoch",
        "cache",
        "wrapper",
        "session_head",
        "session_g0",
        "session_root",
        "session_epoch",
        "session_cache",
        "session_hash",
        "cache_epoch",
        "cache_root",
        "cache_hash",
        "cache_identity",
        "started_hash",
        "started_epoch",
        "started_cache",
    ],
)
def test_reconcile_authenticates_ownership_against_durable_origin(
    tmp_path: Path,
    ownership_cache_template: Path,
    mutation: str,
) -> None:
    attempt, journal, ownership = _owned_attempt(tmp_path, ownership_cache_template)
    path = tmp_path / "sessions" / "ownership" / f"{attempt.identity}.json"
    identity_key = "ownership_identity"
    if mutation.startswith("session_"):
        path = tmp_path / "sessions" / "origin.json"
        identity_key = "session_identity"
    elif mutation.startswith("cache_"):
        path = tmp_path / "cache-verification" / "origin-pre.json"
        identity_key = "receipt_identity"
    elif mutation.startswith("started_"):
        path = journal.path / f"{attempt.identity}.STARTED.json"
        identity_key = "record_identity"
    payload = json.loads(path.read_bytes())
    if mutation == "missing":
        path.unlink()
    elif mutation == "malformed":
        path.write_bytes(b"{")
    else:
        payload.pop(identity_key)
        if mutation == "incomplete":
            payload.pop("supervisor_epoch_id")
        elif mutation == "empty":
            payload["supervisor_epoch_id"] = ""
        elif mutation == "wrong_type":
            payload["pid"] = True
        elif mutation in {"epoch", "cache", "wrapper"}:
            key = {
                "epoch": "supervisor_epoch_id",
                "cache": "cache_receipt_identity",
                "wrapper": "wrapper_receipt_identity",
            }[mutation]
            payload[key] = "stale"
            if mutation != "wrapper":
                payload["wrapper_receipt_identity"] = wrapper_receipt_identity(
                    payload["supervisor_epoch_id"],
                    payload["cache_receipt_identity"],
                    attempt.identity,
                )
        elif mutation.startswith("session_") and mutation != "session_hash":
            key = {
                "session_head": "exact_head",
                "session_g0": "g0_identity",
                "session_root": "output_root",
                "session_epoch": "epoch_id",
                "session_cache": "cache_receipts",
            }[mutation]
            payload[key] = ["stale"] if mutation == "session_cache" else "stale"
        elif mutation in {"cache_epoch", "cache_root", "cache_identity"}:
            key = "epoch_id" if mutation == "cache_epoch" else "output_root"
            payload[key] = "stale"
        elif mutation in {"started_epoch", "started_cache"}:
            key = "supervisor_epoch_id" if mutation == "started_epoch" else "cache_receipt_identity"
            payload["payload"][key] = "stale"
        payload[identity_key] = hashlib.sha256(canonical_json(payload)).hexdigest()
        if mutation.endswith("hash"):
            payload[identity_key] = "0" * 64
        # Rebind session/ownership too: the cache's own origin still has to agree.
        if mutation in {"cache_epoch", "cache_root"}:
            cache_identity = payload[identity_key]
            session_path = tmp_path / "sessions" / "origin.json"
            session = json.loads(session_path.read_bytes())
            session.pop("session_identity")
            session["cache_receipts"] = [cache_identity]
            session["session_identity"] = hashlib.sha256(canonical_json(session)).hexdigest()
            session_path.write_bytes(canonical_json(session))
            changed = replace(
                ownership,
                cache_receipt_identity=cache_identity,
                wrapper_receipt_identity=wrapper_receipt_identity(
                    "origin", cache_identity, attempt.identity
                ),
            )
            owned = asdict(changed) | {"ownership_identity": changed.identity}
            (tmp_path / "sessions" / "ownership" / f"{attempt.identity}.json").write_bytes(
                canonical_json(owned)
            )
            started_path = journal.path / f"{attempt.identity}.STARTED.json"
            started = json.loads(started_path.read_bytes())
            started.pop("record_identity")
            started["payload"]["cache_receipt_identity"] = cache_identity
            started["record_identity"] = hashlib.sha256(canonical_json(started)).hexdigest()
            started_path.write_bytes(canonical_json(started))
        path.write_bytes(canonical_json(payload))
    staging = tmp_path / "staging" / "attempts" / attempt.identity
    staging.mkdir(parents=True)
    (staging / "partial").write_bytes(b"unfinished")
    before = _retained_bytes(tmp_path)
    with pytest.raises((ValueError, TypeError, KeyError, FileNotFoundError)):
        reconcile_attempts(tmp_path, journal)
    assert _retained_bytes(tmp_path) == before
    assert staging.is_dir()
    if mutation == "started_hash":
        with pytest.raises(ValueError, match="schema or hash"):
            next_development_slot(journal)
    else:
        with pytest.raises(RuntimeError, match="reconciled"):
            next_development_slot(journal)


def test_supervisor_restart_recognises_authentic_older_live_epoch(
    tmp_path: Path,
    ownership_cache_template: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempt, journal, ownership = _owned_attempt(tmp_path, ownership_cache_template)
    identity = replace(_execution_identity(), code_head=ownership.exact_head)
    monkeypatch.setattr(execution_module, "_is_real_execution", lambda _root: True)
    monkeypatch.setattr(execution_module, "_require_identity", lambda _root: identity)
    monkeypatch.setattr(
        execution_module, "uuid", SimpleNamespace(uuid4=lambda: SimpleNamespace(hex="restart"))
    )

    def forbidden_launch(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("authentic live wrapper must prevent relaunch")

    monkeypatch.setattr(execution_module, "subprocess", SimpleNamespace(Popen=forbidden_launch))
    before = journal.records(attempt.identity)
    assert execution_module.main(["--output-root", str(tmp_path), "development-supervise"]) == 0
    assert (tmp_path / "cache-verification" / "restart-pre.json").is_file()
    assert not (tmp_path / "sessions" / "restart.json").exists()
    assert journal.records(attempt.identity) == before
    assert reconcile_attempts(tmp_path, journal).live == ownership


@pytest.mark.parametrize("state", ["absent", "unsealed", "sealed", "published", "failure"])
def test_reconcile_confirmed_exit_preserves_recovery_and_idempotence(
    tmp_path: Path,
    ownership_cache_template: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
) -> None:
    attempt, journal, _ownership = _owned_attempt(tmp_path, ownership_cache_template)
    monkeypatch.setattr(supervisor_module, "_observe_process_or_none", lambda _pid: None)
    staging = tmp_path / "staging" / "attempts" / attempt.identity
    bundle = tmp_path / "attempts" / attempt.slot_id / "attempt-0"
    failure = tmp_path / "failures" / attempt.slot_id / "attempt-0"
    if state != "absent":
        staging.mkdir(parents=True)
        (staging / "partial").write_bytes(b"retained")
    if state in {"sealed", "published"}:
        seal_attempt_staging(staging, attempt, required_paths=("partial",))
    if state == "published":
        publish_attempt_bundle(staging, bundle, attempt)
    elif state == "failure":
        failure.parent.mkdir(parents=True)
        staging.rename(failure)
    orphan = staging.parent / "pre-started"
    orphan.mkdir(parents=True)
    (orphan / "partial").write_bytes(b"pre-started")
    result = reconcile_attempts(tmp_path, journal)
    assert result.live is None
    assert result.recovered == ("pre-STARTED:pre-started", f"{state}:{attempt.identity}")
    status = "SUCCEEDED" if state in {"sealed", "published"} else "FAILED"
    assert set(journal.records(attempt.identity)) == {"STARTED", status}
    if status == "SUCCEEDED":
        verify_attempt_bundle(bundle, attempt)
    elif state != "absent":
        assert (failure / "partial").read_bytes() == b"retained"
        assert (failure / "closure.json").is_file()
    before = _retained_bytes(tmp_path)
    assert reconcile_attempts(tmp_path, journal) == supervisor_module.ReconciliationResult((), None)
    assert _retained_bytes(tmp_path) == before


@pytest.mark.parametrize("state", ["sealed", "published", "failure", "multiple"])
def test_reconcile_refuses_conflicting_live_ownership_without_mutation(
    tmp_path: Path,
    ownership_cache_template: Path,
    state: str,
) -> None:
    attempt, journal, _ = _owned_attempt(tmp_path, ownership_cache_template)
    staging = tmp_path / "staging" / "attempts" / attempt.identity
    if state == "multiple":
        _owned_attempt(
            tmp_path, ownership_cache_template, epoch="second", attempt=replace(attempt, attempt=1)
        )
    else:
        staging.mkdir(parents=True)
        (staging / "partial").write_bytes(b"retained")
        if state in {"sealed", "published"}:
            seal_attempt_staging(staging, attempt, required_paths=("partial",))
        if state == "published":
            publish_attempt_bundle(
                staging, tmp_path / "attempts" / attempt.slot_id / "attempt-0", attempt
            )
        elif state == "failure":
            failure = tmp_path / "failures" / attempt.slot_id / "attempt-0"
            failure.parent.mkdir(parents=True)
            staging.rename(failure)
    before = _retained_bytes(tmp_path)
    with pytest.raises(RuntimeError, match=r"conflicting|multiple matching"):
        reconcile_attempts(tmp_path, journal)
    assert _retained_bytes(tmp_path) == before


def test_process_matches_confirms_real_child_exit(tmp_path: Path) -> None:
    with subprocess.Popen(
        [sys.executable, "-c", "import time; print('ready', flush=True); time.sleep(30)"],
        stdout=subprocess.PIPE,
        text=True,
    ) as child:
        assert child.stdout is not None
        assert child.stdout.readline() == "ready\n"
        try:
            receipt = read_process_ownership(
                pid=child.pid,
                exact_head="a" * 40,
                g0_identity="g" * 64,
                output_root=tmp_path,
                slot_id="GRAPH_LSTM:17:DEV_2",
                attempt=0,
                wrapper_receipt_identity="w" * 64,
                supervisor_epoch_id="origin",
                cache_receipt_identity="c" * 64,
            )
            assert process_matches(receipt)
        finally:
            child.terminate()
            child.wait(timeout=5)
        assert not process_matches(receipt)


@pytest.mark.parametrize("source", ["exe", "cmdline"])
def test_process_exit_during_observation_is_confirmed(
    monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    events: list[str] = []

    def stat_identity(pid: int) -> tuple[int, int]:
        events.append("stat")
        return pid, 123

    def read_bytes(path: Path) -> bytes:
        events.append(path.name)
        if path.name == source:
            raise FileNotFoundError("process exited")
        return b"executable"

    def absent_directory(path: Path) -> os.stat_result:
        assert path == Path("/proc/123")
        events.append("absent")
        raise FileNotFoundError("process exited")

    local_path = type("LocalPath", (type(Path()),), {"read_text": lambda _path: "boot"})
    monkeypatch.setattr(supervisor_module, "Path", local_path)
    monkeypatch.setattr(supervisor_module, "_read_process_stat", stat_identity)
    monkeypatch.setattr(local_path, "read_bytes", read_bytes)
    monkeypatch.setattr(local_path, "stat", absent_directory)
    assert supervisor_module._observe_process_or_none(123) is None
    assert events == (
        ["stat", "exe", "absent"] if source == "exe" else ["stat", "exe", "cmdline", "absent"]
    )


def test_process_identity_change_during_observation_refuses_adoption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identities = iter([(123, 456), (123, 789)])
    local_path = type(
        "LocalPath",
        (type(Path()),),
        {"read_text": lambda _path: "boot", "read_bytes": lambda _path: b"fingerprint"},
    )
    monkeypatch.setattr(supervisor_module, "Path", local_path)
    monkeypatch.setattr(supervisor_module, "_read_process_stat", lambda _pid: next(identities))
    with pytest.raises(RuntimeError, match="identity changed during observation"):
        supervisor_module._observe_process_or_none(123)


def test_process_stat_parses_parenthesised_command_name(monkeypatch: pytest.MonkeyPatch) -> None:
    suffix = ["S", *["0"] * 18, "98765", "0"]
    local_path = type(
        "LocalPath",
        (type(Path()),),
        {"read_text": lambda _path: "123 (worker ) name (extra)) " + " ".join(suffix)},
    )
    monkeypatch.setattr(supervisor_module, "Path", local_path)
    assert supervisor_module._read_process_stat(123) == (123, 98765)


@pytest.mark.parametrize("source", ["stat", "exe", "cmdline", "boot"])
@pytest.mark.parametrize("error", [FileNotFoundError, PermissionError])
def test_live_process_observation_errors_refuse_reconciliation_without_exposing_details(
    tmp_path: Path,
    ownership_cache_template: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
    error: type[OSError],
) -> None:
    attempt, journal, ownership = _owned_attempt(tmp_path, ownership_cache_template)
    target = (
        Path("/proc/sys/kernel/random/boot_id")
        if source == "boot"
        else Path(f"/proc/{ownership.pid}/{source}")
    )
    read_text, read_bytes = Path.read_text, Path.read_bytes

    def fail_text(path: Path, *args: Any, **kwargs: Any) -> str:
        if path == target:
            raise error("secret-bearing process detail")
        return read_text(path, *args, **kwargs)

    def fail_bytes(path: Path) -> bytes:
        if path == target:
            raise error("secret-bearing process detail")
        return read_bytes(path)

    local_path = type(
        "LocalPath", (type(Path()),), {"read_text": fail_text, "read_bytes": fail_bytes}
    )
    monkeypatch.setattr(supervisor_module, "Path", local_path)
    staging = tmp_path / "staging" / "attempts" / attempt.identity
    staging.mkdir(parents=True)
    (staging / "partial").write_bytes(b"unfinished")
    before = _retained_bytes(tmp_path)
    with pytest.raises(RuntimeError, match="unavailable") as raised:
        reconcile_attempts(tmp_path, journal)
    assert "secret" not in str(raised.value)
    assert raised.value.__suppress_context__
    assert _retained_bytes(tmp_path) == before
    assert staging.is_dir()


@pytest.mark.parametrize(
    "source,value",
    [
        ("stat", "123 (sensitive command) S 0"),
        ("stat", "unparenthesised sensitive command"),
        ("cmdline", b""),
        ("exe", b""),
        ("boot", ""),
    ],
)
def test_incomplete_live_process_observation_never_means_exit(
    tmp_path: Path,
    ownership_cache_template: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
    value: str | bytes,
) -> None:
    _attempt_id, _journal, ownership = _owned_attempt(tmp_path, ownership_cache_template)
    target = (
        Path("/proc/sys/kernel/random/boot_id")
        if source == "boot"
        else Path(f"/proc/{ownership.pid}/{source}")
    )
    read_text, read_bytes = Path.read_text, Path.read_bytes

    def observed_text(path: Path, *args: Any, **kwargs: Any) -> str:
        return cast(str, value) if path == target else read_text(path, *args, **kwargs)

    def observed_bytes(path: Path) -> bytes:
        return cast(bytes, value) if path == target else read_bytes(path)

    local_path = type(
        "LocalPath", (type(Path()),), {"read_text": observed_text, "read_bytes": observed_bytes}
    )
    monkeypatch.setattr(supervisor_module, "Path", local_path)
    with pytest.raises(RuntimeError) as raised:
        process_matches(ownership)
    assert "sensitive" not in str(raised.value)


def test_reconciliation_preflights_later_open_owner_before_recovering_earlier_exit(
    tmp_path: Path,
    ownership_cache_template: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, journal, _ = _owned_attempt(tmp_path, ownership_cache_template)
    second = replace(first, attempt=1)
    second, _, _ = _owned_attempt(
        tmp_path, ownership_cache_template, epoch="second", attempt=second
    )
    (tmp_path / "sessions" / "ownership" / f"{second.identity}.json").unlink()
    monkeypatch.setattr(supervisor_module, "_observe_process_or_none", lambda _pid: None)
    staging = tmp_path / "staging" / "attempts" / first.identity
    staging.mkdir(parents=True)
    (staging / "partial").write_bytes(b"unfinished")
    before = _retained_bytes(tmp_path)
    with pytest.raises(FileNotFoundError):
        reconcile_attempts(tmp_path, journal)
    assert _retained_bytes(tmp_path) == before
    assert staging.is_dir()


def test_supervisor_source_has_only_epoch_boundary_cache_rehashes() -> None:
    import inspect

    from experiments.r4_residual_graph.execution import _main

    source = inspect.getsource(_main)
    assert source.count("verify_epoch_caches(") == 2
    assert 'f"{epoch_id}-pre"' in source
    assert 'f"{epoch_id}-final"' in source


def test_runtime_identity_binds_canonical_deterministic_cuda_policy() -> None:
    config = FrozenRuntimeConfig()
    payload = config.to_dict()
    assert tuple(tuple(item) for item in payload["deterministic_cuda_policy"]) == (
        DETERMINISTIC_CUDA_POLICY
    )
    with pytest.raises(ValueError, match="deterministic CUDA policy is frozen"):
        replace(
            config,
            deterministic_cuda_policy=(("cublas_workspace_config", ":16:8"),),
        )


def test_runtime_import_configures_environment_before_torch_import() -> None:
    source = Path("experiments/r4_residual_graph/runtime.py").read_text()
    assert source.index("from .cuda_boundary import") < source.index("import torch")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os; import experiments.r4_residual_graph.runtime; "
            "assert os.environ['CUBLAS_WORKSPACE_CONFIG'] == ':4096:8'",
        ],
        check=False,
        capture_output=True,
        text=True,
        env={key: value for key, value in os.environ.items() if key != "CUBLAS_WORKSPACE_CONFIG"},
    )
    assert result.returncode == 0, result.stderr


def test_receipted_wrapper_publishes_ownership_before_scientific_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import experiments.r4_residual_graph.execution as execution

    family, seed, stage = PRIMARY_SCHEDULE[0]
    identity = _execution_identity()
    attempt = _select_development_attempt(
        tmp_path,
        CreateOnlyAttemptJournal(tmp_path),
        identity,
        family=family,
        seed=seed,
        stage=stage,
    )
    events: list[str] = []
    monkeypatch.setattr(execution, "_require_identity", lambda root: identity)
    monkeypatch.setattr(
        "experiments.r4_residual_graph.supervisor.read_process_ownership",
        lambda **kwargs: object(),
    )
    monkeypatch.setattr(
        "experiments.r4_residual_graph.supervisor.write_process_ownership",
        lambda root, actual_attempt, ownership: events.append(f"owned:{actual_attempt.identity}"),
    )
    monkeypatch.setattr(
        "experiments.r4_residual_graph.supervisor.validate_epoch_cache_metadata",
        lambda *args: events.append("cache-valid"),
    )

    def scientific(*args: Any, **kwargs: Any) -> list[str]:
        assert events[-1] == "handshake"
        assert execution._development_epoch_id.get() == "epoch"
        assert execution._development_cache_receipt.get() == "cache-receipt"
        events.append("scientific")
        return []

    real_write = os.write

    def handshake_write(fd: int, data: bytes) -> int:
        assert events[-1] == f"owned:{attempt.identity}"
        events.append("handshake")
        return real_write(fd, data)

    monkeypatch.setattr(execution, "run_development_slots", scientific)
    monkeypatch.setattr(
        execution, "os", SimpleNamespace(write=handshake_write, close=os.close, getpid=os.getpid)
    )
    read_fd, write_fd = os.pipe()
    result = _run_receipted_development_slot(
        tmp_path,
        slot=(family, seed, stage),
        epoch_id="epoch",
        cache_receipt_identity="cache-receipt",
        handshake_fd=write_fd,
    )
    handshake = os.read(read_fd, 256).decode("ascii")
    os.close(read_fd)

    assert result == []
    assert execution._development_epoch_id.get() is None
    assert execution._development_cache_receipt.get() is None
    assert events == [
        "cache-valid",
        f"owned:{attempt.identity}",
        "handshake",
        "scientific",
        "cache-valid",
    ]
    assert handshake == (
        f"{attempt.identity}:"
        f"{wrapper_receipt_identity('epoch', 'cache-receipt', attempt.identity)}\n"
    )


def test_cache_semantic_closure_binds_npy_container_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import experiments.r4_residual_graph.stage_cache as stage_cache

    baseline = stage_cache.cache_builder_semantic_closure()
    original_getsource = stage_cache.inspect.getsource

    def changed_getsource(item: Any) -> str:
        source = original_getsource(item)
        if item is stage_cache._write_npy_once:
            return source + "\n# changed byte writer"
        return source

    monkeypatch.setattr(stage_cache.inspect, "getsource", changed_getsource)
    assert stage_cache.cache_builder_semantic_closure() != baseline


def test_cache_semantic_closure_binds_grouped_cached_consumer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import experiments.r4_residual_graph.stage_cache as stage_cache

    baseline = stage_cache.cache_builder_semantic_closure()
    original_getsource = stage_cache.inspect.getsource

    def changed_getsource(item: Any) -> str:
        source = original_getsource(item)
        if item is stage_cache._PinnedCudaDoubleBuffer:
            return source + "\n# changed grouped cache consumer"
        return source

    monkeypatch.setattr(stage_cache.inspect, "getsource", changed_getsource)
    assert stage_cache.cache_builder_semantic_closure() != baseline


def test_production_stage_cache_builder_requires_streaming_batches() -> None:
    import inspect

    from experiments.r4_residual_graph.execution import _build_development_stage_cache

    source = inspect.getsource(_build_development_stage_cache)
    assert source.count("stream=True") == 2
    assert "stream=False" not in source


@pytest.mark.parametrize(
    "family",
    FITTED_FAMILY_IDS,
)
def test_loaded_grouped_cache_dispatches_all_families_without_dense_fallback(
    family: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert len(FITTED_FAMILY_IDS) == 5
    import experiments.r4_residual_graph.runtime as runtime

    identity = _cache_input()
    training, prediction = _cache_batches(1)
    cache = build_stage_cache(
        tmp_path,
        identity,
        training_batch=training,
        prediction_batch=prediction,
        build_id=f"dispatch-{family}",
    )
    loaded_training, loaded_prediction, _ = load_stage_cache_batches(cache.root, expected=identity)
    calls: list[tuple[str, str]] = []
    model = type("FamilyModel", (), {"family": family})()
    monkeypatch.setattr(runtime, "require_cuda", lambda: torch.device("cpu"))
    monkeypatch.setattr(
        runtime,
        "_fit_streaming_batch",
        lambda actual_model, batch, **kwargs: calls.append(("fit", actual_model.family)) or {},
    )
    monkeypatch.setattr(
        runtime,
        "_predict_streaming_batch",
        lambda actual_model, batch, target_node, **_kwargs: (
            calls.append(("predict", actual_model.family)) or torch.zeros(len(batch.row_keys))
        ),
    )

    runtime.fit_one_model(cast(Any, model), loaded_training)
    runtime.predict_residual(cast(Any, model), loaded_prediction)
    assert calls == [("fit", family), ("predict", family)]

    contract = TensorContract()
    dense_training = ResidualTrainingBatch.from_smoke(
        values=torch.zeros(
            20, contract.lookback_minutes + 1, len(ALL_INSTRUMENTS), len(contract.feature_names)
        ),
        residuals=torch.zeros(20),
        target_nodes=torch.arange(20),
        target_mask=torch.ones(20, dtype=torch.bool),
        preprocessor_identity="p" * 64,
    )
    with pytest.raises(TypeError, match="grouped streaming"):
        build_stage_cache(
            tmp_path / "dense",
            identity,
            training_batch=cast(Any, dense_training),
            prediction_batch=loaded_prediction,
            build_id="dense-rejected",
        )


def test_selected_instrument_counts_exclude_rows_outside_requested_blocks() -> None:
    instrument_count = len(ALL_INSTRUMENTS)
    all_targets = (0, 0, *range(instrument_count), 1)
    selected_indices = (*range(2, 2 + instrument_count), 2 + instrument_count)

    selected_counts = _selected_instrument_counts(all_targets, selected_indices)

    assert selected_counts == (1, 2, *([1] * (instrument_count - 2)))
    assert tuple(all_targets.count(index) for index in range(instrument_count)) != selected_counts
    selected_targets = torch.as_tensor(
        [all_targets[index] for index in selected_indices], dtype=torch.long
    )
    objective = grouped_weighted_loss(
        torch.zeros(len(selected_indices)),
        torch.ones(len(selected_indices)),
        selected_targets,
        torch.as_tensor(selected_counts),
        instrument_count,
    )
    assert float(objective) == pytest.approx(1.0)


def test_timestamp_batches_keep_complete_groups_and_reject_repeated_groups() -> None:
    timestamps = tuple(f"2026-01-0{index + 1}T00:00:00+00:00" for index in range(4))
    keys = tuple(f"I{row % 20}|{timestamp}" for timestamp in timestamps for row in range(20))
    chunks = tuple(_timestamp_row_chunks(keys, tuple(range(80)), 4))
    assert chunks == (tuple(range(80)),)
    assert tuple(keys[index] for index in chunks[0]) == keys
    with pytest.raises(ValueError, match="non-contiguous duplicate timestamp"):
        tuple(_timestamp_row_chunks(("A|t1", "B|t2", "C|t1"), (0, 1, 2), 4))


def test_receipted_wrapper_validates_after_scientific_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import experiments.r4_residual_graph.execution as execution

    family, seed, stage = PRIMARY_SCHEDULE[0]
    identity = _execution_identity()
    _select_development_attempt(
        tmp_path,
        CreateOnlyAttemptJournal(tmp_path),
        identity,
        family=family,
        seed=seed,
        stage=stage,
    )
    validations: list[str] = []
    monkeypatch.setattr(execution, "_require_identity", lambda root: identity)
    monkeypatch.setattr(
        "experiments.r4_residual_graph.supervisor.read_process_ownership",
        lambda **kwargs: object(),
    )
    monkeypatch.setattr(
        "experiments.r4_residual_graph.supervisor.write_process_ownership", lambda *args: None
    )
    monkeypatch.setattr(
        "experiments.r4_residual_graph.supervisor.validate_epoch_cache_metadata",
        lambda *args: validations.append("validated"),
    )

    def fail(*args: object, **kwargs: object) -> list[object]:
        raise RuntimeError("scientific failure")

    monkeypatch.setattr(execution, "run_development_slots", fail)
    read_fd, write_fd = os.pipe()
    with pytest.raises(RuntimeError, match="scientific failure"):
        _run_receipted_development_slot(
            tmp_path,
            slot=(family, seed, stage),
            epoch_id="epoch",
            cache_receipt_identity="cache-receipt",
            handshake_fd=write_fd,
        )
    os.close(read_fd)
    assert validations == ["validated", "validated"]


def test_close_register_full_rehash_precedes_closure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import experiments.r4_residual_graph.execution as execution

    events: list[str] = []
    monkeypatch.setattr(execution, "_require_identity", lambda *args: object())
    monkeypatch.setattr(
        "experiments.r4_residual_graph.supervisor.verify_epoch_caches",
        lambda root, epoch: events.append(f"rehash:{epoch}") or ("receipt",),
    )
    monkeypatch.setattr(
        execution,
        "close_authenticated_register",
        lambda *args, **kwargs: events.append("close") or "register",
    )
    assert close_development_register(tmp_path) == "register"
    assert events[0].startswith("rehash:register-close-")
    assert events[1] == "close"


def test_epoch_receipt_requires_and_binds_both_verified_caches(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        verify_epoch_caches(tmp_path, "absent")
    training, prediction = _cache_batches(1)
    dev2 = _cache_input()
    cache2 = build_stage_cache(
        tmp_path, dev2, training_batch=training, prediction_batch=prediction, build_id="dev2"
    )
    with pytest.raises(ValueError, match="exactly DEV_2 and DEV_3"):
        verify_epoch_caches(tmp_path, "missing")
    dev3 = replace(dev2, stage="DEV_3", training_blocks=("DEV_1", "DEV_2"))
    cache3 = build_stage_cache(
        tmp_path, dev3, training_batch=training, prediction_batch=prediction, build_id="dev3"
    )
    context = {"schema": "R4-P0-DEVELOPMENT-EXECUTION-CONTEXT-V1"}
    context["capsule_identity"] = hashlib.sha256(
        json.dumps(context, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    context_path = tmp_path / "input" / "development-execution-context.json"
    context_path.parent.mkdir()
    context_path.write_text(json.dumps(context, sort_keys=True, separators=(",", ":")))
    first = verify_epoch_caches(tmp_path, "epoch-one")[0]
    payload = json.loads((tmp_path / "cache-verification" / "epoch-one.json").read_text())
    caches = {cache["stage"]: cache for cache in payload["caches"]}
    assert tuple(sorted(caches)) == ("DEV_2", "DEV_3")
    assert all(caches[stage]["manifest"] for stage in ("DEV_2", "DEV_3"))
    assert all(caches[stage]["files"] for stage in ("DEV_2", "DEV_3"))
    assert payload["execution_context"]["capsule_identity"] == context["capsule_identity"]
    second = verify_epoch_caches(tmp_path, "epoch-two")[0]
    assert second != first
    validate_epoch_cache_metadata(tmp_path, "epoch-one", first)
    manifest = cache2.root / "manifest.json"
    os.utime(manifest, None)
    with pytest.raises(ValueError, match="metadata"):
        validate_epoch_cache_metadata(tmp_path, "epoch-one", first)
    shard = next(
        path for path in cache3.root.rglob("*") if path.is_file() and path.name != "manifest.json"
    )
    shard.write_bytes(shard.read_bytes() + b"corrupt")
    with pytest.raises(ValueError):
        verify_epoch_caches(tmp_path, "corrupt")


def test_authenticated_execution_refuses_missing_receipt_before_context_load(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import experiments.r4_residual_graph.execution as execution

    events: list[str] = []
    monkeypatch.setattr(execution, "_is_real_execution", lambda root: True)
    monkeypatch.setattr(
        execution,
        "_load_real_context",
        lambda *args, **kwargs: (
            events.append("context")
            or (_ for _ in ()).throw(AssertionError("context loaded before receipt guard"))
        ),
    )
    with pytest.raises(RuntimeError, match="verified supervisor cache receipt"):
        execution.run_authenticated_development_slots(
            tmp_path, (), FrozenRuntimeConfig(), _execution_identity()
        )
    assert events == []
    epoch_token = execution._development_epoch_id.set("epoch")
    receipt_token = execution._development_cache_receipt.set("receipt")
    try:
        with pytest.raises(FileNotFoundError, match="execution context capsule"):
            execution.run_authenticated_development_slots(
                tmp_path, (), FrozenRuntimeConfig(), _execution_identity()
            )
    finally:
        execution._development_cache_receipt.reset(receipt_token)
        execution._development_epoch_id.reset(epoch_token)
    assert events == []


@pytest.mark.parametrize("failure", ["pre_cache", "post_cache", "scientific_and_post_cache"])
def test_receipted_wrapper_preserves_cache_failure_precedence_and_context_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    identity = _execution_identity()
    events: list[str] = []
    cache_error = RuntimeError("cache drift")
    scientific_error = RuntimeError("scientific failure")
    monkeypatch.setattr(execution_module, "_require_identity", lambda _root: identity)
    monkeypatch.setattr(supervisor_module, "read_process_ownership", lambda **kwargs: object())
    monkeypatch.setattr(
        supervisor_module, "write_process_ownership", lambda *args: events.append("ownership")
    )

    def validate(*args: Any) -> None:
        events.append("cache")
        if failure == "pre_cache" or events.count("cache") == 2:
            raise cache_error

    def scientific(*args: Any, **kwargs: Any) -> list[str]:
        assert execution_module._development_epoch_id.get() == "epoch"
        assert execution_module._development_cache_receipt.get() == "cache"
        events.append("scientific")
        if failure == "scientific_and_post_cache":
            raise scientific_error
        return []

    monkeypatch.setattr(supervisor_module, "validate_epoch_cache_metadata", validate)
    monkeypatch.setattr(execution_module, "run_development_slots", scientific)
    read_fd, write_fd = os.pipe()
    expected = BaseExceptionGroup if failure == "scientific_and_post_cache" else RuntimeError
    try:
        with pytest.raises(expected) as raised:
            _run_receipted_development_slot(
                tmp_path,
                slot=PRIMARY_SCHEDULE[0],
                epoch_id="epoch",
                cache_receipt_identity="cache",
                handshake_fd=write_fd,
            )
    finally:
        os.close(read_fd)
        if failure == "pre_cache":
            os.close(write_fd)
    if failure == "scientific_and_post_cache":
        assert isinstance(raised.value, BaseExceptionGroup)
        assert raised.value.exceptions == (scientific_error, cache_error)
    else:
        assert raised.value is cache_error
    assert events == (
        ["cache"] if failure == "pre_cache" else ["cache", "ownership", "scientific", "cache"]
    )
    assert execution_module._development_epoch_id.get() is None
    assert execution_module._development_cache_receipt.get() is None


@pytest.mark.parametrize("caller", ["_persist_real_model", "_close_authenticated_register_impl"])
def test_real_model_reload_callers_bind_frozen_device(caller: str) -> None:
    import ast
    import inspect

    source = ast.parse(inspect.getsource(getattr(execution_module, caller)))
    calls = [
        node
        for node in ast.walk(source)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_reload_real_model"
    ]
    assert len(calls) == 1
    device = [keyword.value for keyword in calls[0].keywords if keyword.arg == "device"]
    assert len(device) == 1
    assert ast.unparse(device[0]) == "runtime_config.device"


def _writer_fixture(root: Path, family: str, mode: str) -> tuple[Any, dict[str, Any]]:
    identity = _execution_identity()
    attempt = AttemptIdentity(
        identity.release_identity,
        identity.identity,
        str(root.resolve()),
        f"{family}:17:DEV_2",
        family,
        17,
        "DEV_2",
        0,
        mode,
    )
    CreateOnlyAttemptJournal(root).append(attempt, "STARTED", {})
    model = execution_module._build_real_family_model(family)
    return model, {
        "root": root,
        "slot_id": attempt.slot_id,
        "model": model,
        "fit": {"parameter_count": execution_module.model_parameter_count(model)},
        "prediction": torch.tensor([0.25, -0.5]),
        "identity": identity,
        "attempt_identity": attempt,
        "prediction_batch": object(),
        "config": SimpleNamespace(device="cpu", identity=FrozenRuntimeConfig().identity),
        "foundation_content_identity": "1" * 64,
        "training_batch_identity": "2" * 64,
        "preprocessor_identity": "3" * 64,
        "prediction_input_identity": "4" * 64,
        "target_keys": ("a", "b"),
        "local_ridge_forecasts": (1.0, 2.0),
        "fully_pooled_local_ridge_forecasts": (1.0, 2.0),
        "linear_control_identity": "5" * 64,
        "linear_controls": {"ridge": [1.0, 2.0]},
        "linear_control_identities": {"ridge": "6" * 64},
        "linear_control_support_identity": "7" * 64,
    }


@pytest.mark.parametrize(
    ("family", "message_passing_layers", "message_width"),
    [
        ("LOCAL_TEMPORAL_RESIDUAL", 0, 0),
        ("POOLED_NON_GRAPH_RESIDUAL", 0, 32),
        ("FIXED_ECONOMIC_GRAPH_RESIDUAL", 1, 32),
        ("LEARNED_STATIC_GRAPH_RESIDUAL", 1, 32),
        ("SHUFFLED_FIXED_GRAPH_RESIDUAL", 1, 32),
    ],
)
@pytest.mark.parametrize("mode", ["PRIMARY", "SMOKE"])
def test_actual_writer_seals_classified_all_family_reload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    family: str,
    message_passing_layers: int,
    message_width: int,
    mode: str,
) -> None:
    model, kwargs = _writer_fixture(tmp_path, family, mode)
    reloaded_models: list[Any] = []
    events: list[str] = []
    build_model = execution_module._build_real_family_model

    def build_with_device_probe(family_id: str) -> Any:
        reloaded = build_model(family_id)
        transfer = reloaded.to
        adjacency = reloaded.adjacency_matrix

        def to(device: str) -> Any:
            assert device == kwargs["config"].device
            events.append("placement")
            return transfer(device)

        def adjacency_matrix() -> Any:
            assert events == ["placement"]
            events.append("adjacency")
            return adjacency()

        monkeypatch.setattr(reloaded, "to", to)
        monkeypatch.setattr(reloaded, "adjacency_matrix", adjacency_matrix)
        return reloaded

    monkeypatch.setattr(execution_module, "_build_real_family_model", build_with_device_probe)

    def predict(reloaded: Any, batch: Any, **_kwargs: Any) -> torch.Tensor:
        assert batch is kwargs["prediction_batch"]
        assert reloaded is not model
        assert events == ["placement", "adjacency"]
        assert next(reloaded.parameters()).device == next(model.parameters()).device
        for name, value in model.state_dict().items():
            torch.testing.assert_close(reloaded.state_dict()[name], value, rtol=0, atol=0)
        reloaded_models.append(reloaded)
        return kwargs["prediction"].clone()

    monkeypatch.setattr("experiments.r4_residual_graph.runtime.predict_residual", predict)
    execution_module._persist_real_model(**kwargs)
    attempt = kwargs["attempt_identity"]
    bundle = tmp_path / "attempts" / attempt.slot_id / "attempt-0"
    verify_attempt_bundle(bundle, attempt)
    assert json.loads((bundle / "manifest.json").read_text())["attempt"]["mode"] == mode
    assert len(reloaded_models) == 1
    assert {path.name for path in bundle.iterdir()} == {
        "model.pt",
        "model.json",
        "prediction.json",
        "result.json",
        "seal.json",
        "manifest.json",
    }
    metadata = json.loads((bundle / "model.json").read_text())
    architecture = metadata["architecture"]
    assert architecture["family_id"] == family
    assert architecture["message_passing_layers"] == message_passing_layers
    assert architecture["message_width"] == message_width
    assert (
        metadata["architecture_identity"]
        == hashlib.sha256(canonical_json(architecture)).hexdigest()
    )
    assert reloaded_models[0].architecture() == architecture
    result = json.loads((bundle / "result.json").read_text())
    prediction = json.loads((bundle / "prediction.json").read_text())
    assert metadata["artifact_type"] == f"R4.C_{mode}_MODEL_METADATA"
    assert result["artifact_type"] == f"R4.C_{mode}_RESULT"
    assert prediction["artifact_type"] == f"R4.C_{mode}_PREDICTION"
    assert prediction["total_forecast"] == [1.25, 1.5]
    assert prediction["outcomes_loaded"] is False
    assert result["outcomes_loaded"] is False
    assert set(result) == {
        "artifact_type",
        "slot_id",
        "attempt",
        "g0_identity",
        "fit",
        "model_metadata_file_sha256",
        "prediction_file_sha256",
        "prediction_identity",
        "outcomes_loaded",
        "prediction_closed",
    }
    assert (
        prediction.pop("prediction_identity")
        == hashlib.sha256(canonical_json(prediction)).hexdigest()
    )


@pytest.mark.parametrize(
    "failure", ["prediction", "architecture", "graph", "parameters", "attempt"]
)
def test_actual_writer_refuses_invalid_reload_or_attempt_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    model, kwargs = _writer_fixture(tmp_path, "LOCAL_TEMPORAL_RESIDUAL", "SMOKE")
    monkeypatch.setattr(
        "experiments.r4_residual_graph.runtime.predict_residual",
        lambda *_args, **_kwargs: kwargs["prediction"] + int(failure == "prediction"),
    )
    if failure == "architecture":
        monkeypatch.setattr(model, "architecture", lambda: {"drift": True})
    elif failure == "graph":
        monkeypatch.setattr(model, "adjacency_matrix", lambda: torch.ones(20, 20))
    elif failure == "parameters":
        kwargs["fit"]["parameter_count"] += 1
    elif failure == "attempt":
        kwargs["attempt_identity"] = replace(kwargs["attempt_identity"], mode="PRIMARY")
    with pytest.raises((AssertionError, ValueError)):
        execution_module._persist_real_model(**kwargs)
    assert not (tmp_path / "attempts").exists()


@pytest.mark.parametrize("status", ["STARTED", "SUCCEEDED", "FAILED"])
def test_primary_supervisor_rejects_smoke_before_any_reconciliation_mutation(
    tmp_path: Path, status: str
) -> None:
    identity = _execution_identity()
    journal = CreateOnlyAttemptJournal(tmp_path)
    primary = _select_development_attempt(
        tmp_path, journal, identity, family="LOCAL_TEMPORAL_RESIDUAL", seed=17, stage="DEV_2"
    )
    journal.append(primary, "STARTED", {})
    smoke = replace(
        primary,
        family_id="FIXED_ECONOMIC_GRAPH_RESIDUAL",
        slot_id="FIXED_ECONOMIC_GRAPH_RESIDUAL:17:DEV_2",
        mode="SMOKE",
    )
    journal.append(smoke, "STARTED", {})
    if status != "STARTED":
        journal.append(smoke, "SUCCEEDED" if status == "SUCCEEDED" else "FAILED", {})
    before = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    for operation in (
        lambda: supervisor_module.next_development_slot(journal),
        lambda: supervisor_module.reconcile_attempts(tmp_path, journal),
    ):
        with pytest.raises(ValueError, match="PRIMARY supervisor rejects SMOKE"):
            operation()
        assert before == {
            path.relative_to(tmp_path): path.read_bytes()
            for path in tmp_path.rglob("*")
            if path.is_file()
        }


@pytest.mark.parametrize("phase", ["entry", "exit"])
def test_smoke_reference_failure_precedes_distinct_root_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    @contextmanager
    def reference(_root: Path) -> Iterator[object]:
        if phase == "entry":
            raise ValueError("reference entry rejected")
        yield object()
        raise ValueError("reference exit rejected")

    monkeypatch.setattr(execution_module, "preparation_entry", reference)
    monkeypatch.setattr(
        "experiments.r4_residual_graph.preparation_reference.reference_binding", lambda _root: {}
    )
    monkeypatch.setattr(
        execution_module,
        "capture_g0_identity",
        Mock(side_effect=AssertionError("G0 before reference expiry")),
    )
    root = tmp_path / "smoke"
    with pytest.raises(ValueError, match=f"reference {phase} rejected"):
        execution_module.run_bounded_smoke(root, reference_root=tmp_path / "reference")
    assert not root.exists()


def test_smoke_orchestration_uses_real_writer_and_expired_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import experiments.r4_residual_graph.qualification as qualification
    import experiments.r4_residual_graph.runtime as runtime
    import experiments.r4_residual_graph.synthetic_qualification as synthetic

    root = tmp_path / "smoke"
    reference_root = tmp_path / "reference"
    events: list[str] = []
    identity = _execution_identity()

    @contextmanager
    def reference(actual: Path) -> Iterator[object]:
        assert actual == reference_root
        events.append("reference-enter")
        yield object()
        events.append("reference-expired")

    def capture(actual: Path, _config: Any) -> G0ExecutionIdentity:
        assert actual == root and events == ["reference-enter", "reference-expired"]
        events.append("g0")
        return identity

    monkeypatch.setattr(execution_module, "preparation_entry", reference)
    monkeypatch.setattr(
        "experiments.r4_residual_graph.preparation_reference.reference_binding",
        lambda _root: {"execution_g0": "reference-g0"},
    )
    monkeypatch.setattr(execution_module, "capture_g0_identity", capture)
    monkeypatch.setattr(execution_module, "require_cuda", lambda *_args: None)
    monkeypatch.setattr(execution_module, "configure_deterministic_cuda", torch.manual_seed)
    monkeypatch.setattr(
        execution_module,
        "close_authenticated_register",
        Mock(side_effect=AssertionError("smoke closed scientific register")),
    )
    monkeypatch.setattr(
        execution_module,
        "_run_receipted_development_slot",
        Mock(side_effect=AssertionError("smoke launched scientific wrapper")),
    )
    monkeypatch.setattr(
        execution_module,
        "_load_authenticated_development_outcomes",
        Mock(side_effect=AssertionError("smoke loaded outcomes")),
    )
    monkeypatch.setattr(
        evaluation_module,
        "evaluate_development_register",
        Mock(side_effect=AssertionError("smoke evaluated outcomes")),
    )
    keys = tuple(f"instrument-{index}" for index in range(5120))
    batch = SimpleNamespace(
        row_keys=keys,
        target_nodes=tuple(range(20)) * 256,
        cached_arrays={"row_timestamp": tuple(index // 20 for index in range(5120))},
        input_identity="a" * 64,
        content_identity="b" * 64,
        preprocessor_identity="c" * 64,
        support_identity="d" * 64,
    )

    def inputs(**kwargs: int) -> tuple[object, ...]:
        assert kwargs == {
            "dev1_timestamp_count": 256,
            "prediction_timestamp_count": 256,
            "vary_values": True,
        }
        events.append("inputs")
        return (batch,)

    def cache(actual: Path, *, inputs: Any) -> tuple[Path, object]:
        assert actual == root and inputs == (batch,)
        events.append("cache-build")
        return root / "cache", object()

    monkeypatch.setattr(synthetic, "synthetic_qualification_inputs", inputs)
    monkeypatch.setattr(synthetic, "build_synthetic_cache", cache)
    monkeypatch.setattr(
        stage_cache,
        "load_stage_cache_batches",
        lambda *_args, **_kwargs: (batch, batch, SimpleNamespace(cache_identity="cache")),
    )

    def telemetry(value: Any, training: bool) -> None:
        if value is not None:
            value.rows = 5120
            value.timestamps = 256
            value.timestamp_batch_calls = value.forward_calls = value.materialisation_calls = (
                value.prefetch_calls
            ) = 4
            value.h2d_calls = 4 + int(training)
            value.h2d_bytes = 100

    def fit(model: Any, actual: Any, **kwargs: Any) -> dict[str, int]:
        assert actual is batch and kwargs["training_blocks"] == ("DEV_1",)
        telemetry(kwargs["_telemetry"], True)
        events.append(f"fit:{model.spec.family_id}")
        return {"parameter_count": execution_module.model_parameter_count(model)}

    def predict(model: Any, actual: Any, *, _telemetry: Any = None) -> torch.Tensor:
        assert actual is batch
        telemetry(_telemetry, False)
        return torch.zeros(len(keys))

    monkeypatch.setattr(runtime, "fit_one_model", fit)
    monkeypatch.setattr(runtime, "predict_residual", predict)
    monkeypatch.setattr(
        qualification, "_oracle_scalar_forward", lambda *_args, **_kwargs: torch.tensor(0.0)
    )

    def place(model: Any, device: str) -> Any:
        assert device == FrozenRuntimeConfig().device
        return model

    monkeypatch.setattr(torch.nn.Module, "to", place)
    receipt = execution_module.run_bounded_smoke(root, reference_root=reference_root)
    assert events.count("cache-build") == 1
    assert receipt["g0_identity"] == identity.identity != "reference-g0"
    assert receipt["outcomes_loaded"] is False
    assert receipt["scientific_performance"] == "NOT_COMPUTED"
    assert len(receipt["families"]) == 5
    for family in FITTED_FAMILY_IDS:
        assert events.count(f"fit:{family}") == 2
        bundle = root / "attempts" / f"{family}:17:DEV_2" / "attempt-0"
        verify_attempt_bundle(bundle)
        assert json.loads((bundle / "manifest.json").read_text())["attempt"]["mode"] == "SMOKE"
        assert (
            json.loads((bundle / "result.json").read_text())["artifact_type"] == "R4.C_SMOKE_RESULT"
        )
    assert (root / "support" / "terminal-capsule.json").exists()
    with pytest.raises(ValueError, match="PRIMARY supervisor rejects SMOKE"):
        supervisor_module.next_development_slot(CreateOnlyAttemptJournal(root))


def test_synthetic_smoke_inputs_produce_four_real_chunks_per_phase(tmp_path: Path) -> None:
    from experiments.r4_residual_graph.runtime import _cached_timestamp_row_chunks
    from experiments.r4_residual_graph.synthetic_qualification import (
        build_synthetic_cache,
        synthetic_qualification_inputs,
    )

    inputs = synthetic_qualification_inputs(
        dev1_timestamp_count=256, prediction_timestamp_count=256, vary_values=True
    )
    cache_root, expected = build_synthetic_cache(tmp_path, inputs=inputs)
    training, prediction, _ = load_stage_cache_batches(cache_root, expected=expected)
    for batch in (training, prediction):
        assert batch.batch_size == 64
        assert batch.cached_arrays is not None
        assert batch.cached_arrays["values"].shape == (256, 61, 20, 26)
        boundaries = batch.cached_arrays["values"][[0, 64, 128, 192], 0, 0, 0]
        assert np.unique(boundaries).size == 4
        assert np.unique(batch.cached_arrays["values"][0, 0, :, 0]).size == 20
        chunks = tuple(
            _cached_timestamp_row_chunks(
                batch.cached_arrays["row_timestamp"], tuple(range(5120)), 64
            )
        )
        assert tuple(map(len, chunks)) == (1280, 1280, 1280, 1280)
