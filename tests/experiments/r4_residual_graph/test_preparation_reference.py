from __future__ import annotations

import json
import os
from contextvars import Context
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from weakref import WeakSet

import numpy as np
import polars as pl
import pytest
import torch

from experiments.r4_residual_graph import execution, foundation, runtime, stage_cache, supervisor
from experiments.r4_residual_graph import preparation_reference as reference
from experiments.r4_residual_graph.attempt_artifacts import canonical_json, sha256_bytes
from experiments.r4_residual_graph.runtime import environment_identity
from experiments.r4_residual_graph.stage_cache import cache_builder_semantic_closure
from experiments.r4_residual_graph.supervisor import (
    validate_epoch_cache_metadata,
    verify_epoch_caches,
    write_supervisor_session,
)


def _write(path: Path, payload: dict[str, Any], key: str | None = None) -> dict[str, Any]:
    if key is not None:
        payload[key] = sha256_bytes(canonical_json(payload))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(payload))
    return payload


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    return _roots(tmp_path, monkeypatch)


def _roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, integrated: bool = False
) -> tuple[Path, Path]:
    preparation, destination = tmp_path / "prepared", tmp_path / "execution"
    parent = _synthetic_parent(tmp_path, monkeypatch) if integrated else None
    identity = execution.capture_g0_identity(preparation)
    monkeypatch.setattr(reference, "PREPARATION_ROOT", preparation)
    monkeypatch.setattr(reference, "ACCEPTED_CANDIDATE", identity.code_head)
    monkeypatch.setattr(reference, "ACCEPTED_G0", identity.identity)
    _write(preparation / "config/g0-identity.json", identity.to_dict())
    binding = {
        "artifact_type": "R4.C_AUTHENTICATED_EXECUTION_INPUT",
        "g0_identity": identity.identity,
        "foundation_identity": "f" * 64,
        "preprocessor_identity": "c" * 64,
        "support_identity": "s" * 64,
        "raw_tensor_store_identity": "r" * 64,
        "raw_tensor_store_path": str(preparation / "input/raw-tensors" / ("r" * 64)),
        "prepared_development_stages": {
            stage: {
                "stage_identity": "d" * 64,
                "path": str(preparation / "input/prepared-development" / stage / ("d" * 64)),
                "raw_store_path": str(preparation / "input/raw-tensors" / ("r" * 64)),
                "raw_store_identity": "r" * 64,
            }
            for stage in ("DEV_2", "DEV_3")
        },
    }
    if parent is not None:
        binding.update(
            manifest_path=str(parent.manifest_path),
            manifest_sha256=parent.manifest_sha256,
            manifest_identity=parent.manifest_sha256,
        )
        rows = [
            {
                "block": stage,
                "instrument_id": instrument,
                "decision_time": datetime(2026, 5, day, 14, 6, tzinfo=UTC),
                "target_return": 0.01 * (index + 1),
            }
            for stage, day in (("DEV_2", 20), ("DEV_3", 27))
            for index, instrument in enumerate(foundation.ALL_INSTRUMENTS)
        ]
        foundation_path = preparation / "input/foundation.parquet"
        foundation_path.parent.mkdir(parents=True)
        pl.DataFrame(rows).write_parquet(foundation_path)
        binding.update(
            foundation_path=str(foundation_path),
            foundation_file_sha256=execution._file_digest(foundation_path),
        )
    _write(preparation / "config/execution-input.json", binding)
    stages: dict[str, Any] = {}
    accepted_stages: dict[str, Any] = {}
    for stage in ("DEV_2", "DEV_3"):
        if integrated:
            metadata, manifest, cache_identity = _synthetic_stage(preparation, stage, identity)
            stage_path = execution._development_stage_capsule_path(preparation, stage)
            stages[stage] = {
                "path": str(stage_path),
                "size_bytes": stage_path.stat().st_size,
                "sha256": execution._file_digest(stage_path),
                "stage_identity": metadata["stage_identity"],
            }
            accepted_stages[stage] = {
                "stage_identity": metadata["stage_identity"],
                "cache_identity": cache_identity,
                "manifest_identity": manifest["manifest_identity"],
                "prepared_identity": "d" * 64,
            }
            continue
        cache_identity = sha256_bytes(stage.encode())
        cache_root = preparation / "stage-cache" / stage / cache_identity
        manifest = _write(
            cache_root / "manifest.json",
            {
                "schema": "R4-P0-STAGE-CACHE-V2",
                "files": [],
                "builder_semantic_closure": cache_builder_semantic_closure(),
                "semantic_inputs": {
                    "config_identity": identity.config_identity,
                    "numerical_runtime_identity": sha256_bytes(
                        canonical_json(environment_identity())
                    ),
                },
            },
            "manifest_identity",
        )
        _write(
            cache_root / "seal.json",
            {
                "schema": manifest["schema"],
                "manifest_identity": manifest["manifest_identity"],
                "manifest_sha256": sha256_bytes((cache_root / "manifest.json").read_bytes()),
                "cache_identity": cache_identity,
            },
            "seal_identity",
        )
        stage_path = preparation / "input/development-execution-stages" / f"{stage}.json"
        metadata = _write(
            stage_path,
            {
                "schema": "R4-P0-DEVELOPMENT-EXECUTION-STAGE-V2",
                "stage": stage,
                "cache_root": str(cache_root),
                "cache_identity": cache_identity,
                "semantic_inputs": manifest["semantic_inputs"],
                "preprocessor_identity": "c" * 64,
            },
            "stage_identity",
        )
        stages[stage] = {
            "path": str(stage_path),
            "size_bytes": stage_path.stat().st_size,
            "sha256": sha256_bytes(stage_path.read_bytes()),
            "stage_identity": metadata["stage_identity"],
        }
        accepted_stages[stage] = {
            "stage_identity": metadata["stage_identity"],
            "cache_identity": cache_identity,
            "manifest_identity": manifest["manifest_identity"],
            "prepared_identity": "d" * 64,
        }
    capsule = _write(
        preparation / "input/development-execution-context.json",
        {
            "schema": "R4-P0-DEVELOPMENT-EXECUTION-CONTEXT-V2",
            "g0_identity": identity.identity,
            "config_identity": identity.config_identity,
            "foundation_content_identity": "f" * 64,
            "support_identity": "s" * 64,
            "preparation_preprocessor_identity": "c" * 64,
            "stages": stages,
        },
        "capsule_identity",
    )
    # Metadata inventory is exercised at the authorised cardinality without payloads.
    file_count = sum(path.is_file() for path in preparation.rglob("*"))
    for index in range(reference.RETAINED_FILES - file_count):
        (preparation / f"retained-{index}.npy").write_bytes(b"must never be opened")
    files = {
        path.relative_to(preparation).as_posix(): reference._regular_state(path)
        for path in sorted(preparation.rglob("*"))
        if path.is_file()
    }
    assert len(files) == reference.RETAINED_FILES
    receipt_path = tmp_path / "receipt.json"
    _write(
        receipt_path,
        {
            "status": "PASS",
            "candidate": [identity.code_head, reference.ACCEPTED_TREE],
            "g0_identity": identity.identity,
            "files": files,
            "capsule_identity": capsule["capsule_identity"],
            "raw_identity": "r" * 64,
            "stages": accepted_stages,
        },
    )
    monkeypatch.setattr(reference, "ACCEPTED_RECEIPT", receipt_path)
    monkeypatch.setattr(
        reference, "ACCEPTED_RECEIPT_SHA256", sha256_bytes(receipt_path.read_bytes())
    )
    original_capture = execution.capture_g0_identity
    monkeypatch.setattr(
        execution,
        "capture_g0_identity",
        lambda root, config=None: replace(original_capture(root, config), code_head="b" * 40),
    )
    return preparation, destination


def test_reference_create_reopen_routes_inputs_without_payload_reads(
    roots: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preparation, destination = roots
    before = {p: reference._regular_state(p) for p in preparation.rglob("*") if p.is_file()}
    original_read = Path.read_bytes

    def metadata_only(path: Path) -> bytes:
        assert path.suffix == ".json", f"payload opened: {path}"
        return original_read(path)

    local_path = type("MetadataPath", (type(Path()),), {"read_bytes": metadata_only})
    monkeypatch.setattr(reference, "PREPARATION_ROOT", local_path(preparation))
    payload = reference.create_preparation_reference(destination)
    assert payload["execution_g0"]["code_head"] == "b" * 40
    assert payload["bindings"]["preparation_g0"]["code_head"] == reference.ACCEPTED_CANDIDATE
    assert sorted(
        str(p.relative_to(destination)) for p in destination.rglob("*") if p.is_file()
    ) == ["config/g0-identity.json", "config/preparation-reference.json"]
    with reference.preparation_entry(destination) as capability:
        assert capability is not None
        assert reference.preparation_root(destination) == preparation
        capsule = execution._load_development_execution_capsule(
            destination,
            execution._require_identity(destination),
            execution.FrozenRuntimeConfig(),
            metadata_only=True,
        )
        assert capsule["g0_identity"] == reference.ACCEPTED_G0
        with reference.preparation_entry(destination) as nested:
            assert nested is capability
    assert not capability.active
    with pytest.raises(ValueError, match="expired"):
        capability.validate(destination)
    assert {p: reference._regular_state(p) for p in before} == before
    with pytest.raises(FileExistsError):
        reference.create_preparation_reference(destination)
    with pytest.raises(RuntimeError, match="process entry"):
        reference.preparation_root(destination)


@pytest.mark.parametrize(
    "mutation",
    [
        "receipt",
        "event",
        "root",
        "head",
        "schema",
        "configuration_identity",
        "cache_closure_identity",
        "raw_identity",
        "nested_duplicate",
        "file",
        "symlink",
        "extra",
    ],
)
def test_reference_rejects_authority_and_metadata_drift(
    roots: tuple[Path, Path],
    mutation: str,
) -> None:
    preparation, destination = roots
    reference.create_preparation_reference(destination)
    path = reference.reference_path(destination)
    payload = json.loads(path.read_bytes())
    if mutation in {"receipt", "event", "root", "head", "schema"}:
        field = {
            "receipt": "accepted_receipt_sha256",
            "event": "acceptance_event",
            "root": "preparation_root",
            "head": "preparation_candidate",
            "schema": "schema",
        }[mutation]
        payload.pop("reference_identity")
        payload[field] = "wrong"
        _write(path, payload, "reference_identity")
    elif mutation in {"configuration_identity", "cache_closure_identity", "raw_identity"}:
        payload.pop("reference_identity")
        payload["bindings"][mutation] = "wrong"
        _write(path, payload, "reference_identity")
    elif mutation == "nested_duplicate":
        path.write_bytes(
            path.read_bytes().replace(
                b'"execution_g0":{', b'"execution_g0":{"code_head":"wrong",', 1
            )
        )
    elif mutation == "file":
        (preparation / "retained-0.npy").write_bytes(b"drift")
    elif mutation == "symlink":
        retained = preparation / "retained-0.npy"
        retained.rename(preparation / "moved")
        retained.symlink_to(preparation / "moved")
    else:
        (preparation / "extra").write_bytes(b"extra")
    with pytest.raises(ValueError), reference.preparation_entry(destination):
        raise AssertionError("drift was accepted")
    assert reference._active.get() is None


def test_epoch_receipts_bind_reference_and_preserve_exception_precedence(
    roots: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from experiments.r4_residual_graph import supervisor

    preparation, destination = roots
    reference.create_preparation_reference(destination)

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("reference cache must not perform full verification")

    monkeypatch.setattr(supervisor, "verify_stage_cache", forbidden)
    capability: reference.PreparationCapability | None = None
    with (
        pytest.raises(ExceptionGroup) as raised,
        reference.preparation_entry(destination) as active_capability,
    ):
        capability = active_capability
        receipts = verify_epoch_caches(destination, "epoch-pre")
        identity = execution._require_identity(destination)
        write_supervisor_session(
            destination,
            "epoch",
            exact_head=identity.code_head,
            g0_identity=identity.identity,
            cache_receipts=list(receipts),
        )
        validate_epoch_cache_metadata(destination, "epoch-pre", receipts[0])
        cache = json.loads((destination / "cache-verification/epoch-pre.json").read_bytes())
        session = json.loads((destination / "sessions/epoch.json").read_bytes())
        assert cache["preparation_reference_identity"] == session["preparation_reference_identity"]
        assert cache["execution_g0"] == identity.to_dict()
        (preparation / "retained-0.npy").write_bytes(b"drift")
        raise RuntimeError("primary")
    assert str(raised.value.exceptions[0]) == "primary"
    assert isinstance(raised.value.exceptions[1], ValueError)
    assert capability is not None and not capability.active
    assert reference._active.get() is None


def test_reference_capability_refuses_cross_root_and_process_reuse(
    roots: tuple[Path, Path],
) -> None:
    _, destination = roots
    reference.create_preparation_reference(destination)
    with reference.preparation_entry(destination) as capability:
        assert capability is not None
        with pytest.raises(ValueError, match="another process/root"):
            capability.validate(destination.parent)
        original_pid = capability.pid
        capability.pid = -1
        with pytest.raises(ValueError, match="another process/root"):
            capability.validate(destination)
        capability.pid = original_pid


def test_capability_helpers_reuse_one_entry_proof(
    roots: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, root = roots
    reference.create_preparation_reference(root)
    capture = execution.capture_g0_identity
    inventory = reference._check_inventory
    counts = {"g0": 0, "inventory": 0}

    def counted_capture(*args: Any, **kwargs: Any) -> Any:
        counts["g0"] += 1
        return capture(*args, **kwargs)

    def counted_inventory(*args: Any, **kwargs: Any) -> None:
        counts["inventory"] += 1
        inventory(*args, **kwargs)

    monkeypatch.setattr(execution, "capture_g0_identity", counted_capture)
    monkeypatch.setattr(reference, "_check_inventory", counted_inventory)
    with reference.preparation_entry(root):
        assert counts == {"g0": 1, "inventory": 1}
        for _ in range(10):
            with reference.preparation_entry(root):
                assert reference.preparation_root(root) == reference.PREPARATION_ROOT
                assert reference.preparation_g0(root, "other") == reference.ACCEPTED_G0
                assert reference.reference_binding(root)["preparation_reference_identity"]
        assert counts == {"g0": 1, "inventory": 1}
    assert counts == {"g0": 2, "inventory": 2}


def _synthetic_parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from experiments.r2_historical_lab.lab_0 import harness

    child = tmp_path / "lab/feature.json"
    _write(child, {"synthetic": True})
    manifest_path = child.parent / "manifest.json"
    _write(
        manifest_path,
        {
            "contract": harness.MANIFEST_CONTRACT,
            "evidence_label": foundation.LABEL,
            "source_class": foundation.SOURCE_CLASS,
            "status": "COMPLETE",
            "authoritative": False,
            "instruments": list(foundation.ALL_INSTRUMENTS),
            "horizons_minutes": [5, 15, 30, 60],
            "fold_blocks": [
                {"name": name, "selection_prohibited": True}
                for name in ("DEV_1", "DEV_2", "DEV_3", "TERMINAL_FORMER_HOLDOUT")
            ],
            "parts": [
                {"kind": "feature", "path": str(child), "sha256": execution._file_digest(child)}
            ],
            "baseline_reconstruction": {
                "contract": "qtrad-r2-historical-lab-baseline-reconstruction-v2",
                "support_exact": True,
                "ordering_zero_pooled_local": True,
                "fit_count": 21,
                "maximum_metric_abs_delta": 0,
                "maximum_preprocessing_abs_delta": 0,
                "maximum_coefficient_abs_delta": 0,
                "maximum_intercept_abs_delta": 0,
                "tolerances": {
                    "metric_abs": 0,
                    "preprocessing_abs": 0,
                    "coefficient_abs": 0,
                    "intercept_abs": 0,
                },
                # Declaration checked by authenticate_manifest; this path is never read.
                "retained_oof_manifest": {
                    "path": (
                        "/workspace/tmp/r2-confirmatory-ibkr-historical-20260820T051751Z/"
                        "oof/manifest.json"
                    ),
                    "sha256": "ff0bd89fb97448beda6e70565191bb512458c4d3124ec0dc17476b2d43859819",
                    "contract": "qtrad-r2-oof-bundle-v2",
                    "schema_version": 2,
                    "source_class": foundation.SOURCE_CLASS,
                    "evidence_class": "CONFIRMATORY",
                    "oof_id": "c31dddc528936d1a415c4a5af009e59a43eefe27909b7a16267712f9671dfa65",
                    "closure_id": (
                        "d911eea62786f7e0d99719b78c93da80cd6574118e706812107dd949d2fcd6a6"
                    ),
                },
            },
        },
    )
    digest = execution._file_digest(manifest_path)
    parent = foundation.authenticate_parent(manifest_path, digest)
    monkeypatch.setattr(runtime, "MANIFEST_IDENTITY", digest)
    monkeypatch.setattr(runtime, "CLOSURE_IDENTITY", parent.child_closure_sha256)
    monkeypatch.setattr(execution, "MANIFEST_IDENTITY", digest)
    monkeypatch.setattr(execution, "MANIFEST_SHA256", digest)
    config = runtime.FrozenRuntimeConfig(
        manifest_identity=digest, child_closure_identity=parent.child_closure_sha256
    )
    monkeypatch.setattr(execution, "FrozenRuntimeConfig", lambda: config)
    monkeypatch.setitem(foundation.EXPECTED_LINEAR["development"], "support", 40)
    return parent


def _synthetic_stage(
    preparation: Path, stage: str, identity: execution.G0ExecutionIdentity
) -> tuple[dict[str, Any], dict[str, Any], str]:
    # The narrow batch adapter avoids constructing upstream scientific capabilities.
    # Its arrays, cache closure and execution capsule are genuinely digest-bound.
    day = 20 if stage == "DEV_2" else 27
    timestamp = datetime(2026, 5, day, 14, 6, tzinfo=UTC).isoformat()
    keys = [f"{instrument}|{timestamp}" for instrument in foundation.ALL_INSTRUMENTS]
    arrays = {
        "train": np.arange(20, dtype=np.float32),
        "predict": np.arange(20, dtype=np.float32) / 1000,
    }
    semantics = stage_cache.CacheInput(
        stage,
        ("DEV_1",) if stage == "DEV_2" else ("DEV_1", "DEV_2"),
        "f" * 64,
        "a" * 64,
        execution.FrozenRuntimeConfig().tensor_identity,
        "c" * 64,
        identity.config_identity,
        sha256_bytes(canonical_json(environment_identity())),
        identity.code_head,
        tuple(foundation.ALL_INSTRUMENTS),
        tuple(foundation.ALL_INSTRUMENTS),
        ("synthetic",),
    )
    staging = preparation / "staging" / stage
    files = []
    batches = {}
    for name, array in arrays.items():
        path = staging / f"{name}.npy"
        descriptor = execution._write_npy_once(path, array)
        content_identity = stage_cache._semantic_array_digest(array)
        batches[name] = content_identity
        files.append(
            {
                "path": path.name,
                "size": descriptor["size_bytes"],
                "container_sha256": descriptor["sha256"],
                "semantic_sha256": content_identity,
            }
        )
    manifest = _write(
        staging / "manifest.json",
        {
            "schema": "R4-P0-STAGE-CACHE-V2",
            "files": files,
            "builder_semantic_closure": cache_builder_semantic_closure(),
            "semantic_inputs": semantics.semantic_inputs(),
            "batch_identities": batches,
        },
        "manifest_identity",
    )
    cache_identity = execution._sha256(
        {
            "manifest_identity": manifest["manifest_identity"],
            "semantic_inputs": manifest["semantic_inputs"],
            "builder_semantic_closure": manifest["builder_semantic_closure"],
        }
    )
    _write(
        staging / "seal.json",
        {
            "schema": manifest["schema"],
            "manifest_identity": manifest["manifest_identity"],
            "manifest_sha256": execution._file_digest(staging / "manifest.json"),
            "cache_identity": cache_identity,
        },
        "seal_identity",
    )
    cache_root = preparation / "stage-cache" / stage / cache_identity
    cache_root.parent.mkdir(parents=True)
    staging.rename(cache_root)
    stage_cache.verify_stage_cache(cache_root, expected=semantics)
    controls = {
        "ZERO_RETURN": [0.0] * 20,
        "LOCAL_RIDGE": [0.01] * 20,
        "FULLY_POOLED_LOCAL_RIDGE": [0.02] * 20,
    }
    period = {
        "keys": keys,
        **controls,
        "controls": controls,
        "control_identities": {
            name: execution._sha256(values) for name, values in controls.items()
        },
        "support_identity": execution._sha256(
            [
                f"{instrument}|{datetime(2026, 5, day, 14, 6, tzinfo=UTC).isoformat()}"
                for day in (20, 27)
                for instrument in foundation.ALL_INSTRUMENTS
            ]
        ),
    }
    period["identity"] = execution._sha256(period)
    metadata = _write(
        execution._development_stage_capsule_path(preparation, stage),
        {
            "schema": "R4-P0-DEVELOPMENT-EXECUTION-STAGE-V2",
            "stage": stage,
            "cache_root": str(cache_root),
            "cache_identity": cache_identity,
            "semantic_inputs": manifest["semantic_inputs"],
            "preprocessor_identity": "c" * 64,
            "training_batch_identity": batches["train"],
            "prediction_batch_identity": batches["predict"],
            "prediction_input_identity": execution._sha256(keys),
            "target_keys": execution._write_npy_once(
                execution._development_stage_bulk_path(preparation, stage, "target-keys"),
                np.asarray(keys),
            ),
            "control_period": execution._write_npy_once(
                execution._development_stage_bulk_path(preparation, stage, "control-period"),
                np.frombuffer(canonical_json(period), dtype=np.uint8),
            ),
        },
        "stage_identity",
    )
    return metadata, manifest, cache_identity


def _install_synthetic_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    def load(cache_root: Path, *, expected: Any, full_verify: bool) -> Any:
        assert full_verify is False
        verified = stage_cache.verify_stage_cache(cache_root, expected=expected, full=True)
        manifest = execution._read_json(cache_root / "manifest.json")
        batches = []
        for name in ("train", "predict"):
            values = np.load(cache_root / f"{name}.npy", allow_pickle=False)
            content_identity = stage_cache._semantic_array_digest(values)
            assert content_identity == manifest["batch_identities"][name]
            batches.append(
                SimpleNamespace(
                    content_identity=content_identity,
                    values=values,
                    training_blocks=expected.training_blocks,
                )
            )
        return *batches, verified

    monkeypatch.setattr(stage_cache, "load_stage_cache_batches", load)


def _scientific_boundary(
    root: Path, monkeypatch: pytest.MonkeyPatch, *, failure: BaseException | None = None
) -> tuple[list[reference.PreparationCapability], list[str]]:
    capabilities: list[reference.PreparationCapability] = []
    placements: list[str] = []
    trained_models: WeakSet[Any] = WeakSet()
    build_model = execution._build_real_family_model
    write_ownership = supervisor.write_process_ownership

    def build_observed_model(family_id: str) -> Any:
        model = build_model(family_id)
        original_to = model.to
        original_adjacency = model.adjacency_matrix
        placed = False

        def place(device: str) -> Any:
            nonlocal placed
            assert device == runtime.DEVICE_REQUIRED == "cuda"
            assert not placed and model not in trained_models
            result = original_to("cpu")
            placed = True
            placements.append(family_id)
            return result

        def adjacency() -> Any:
            assert placed or model in trained_models
            return original_adjacency()

        monkeypatch.setattr(model, "to", place)
        monkeypatch.setattr(model, "adjacency_matrix", adjacency)
        return model

    monkeypatch.setattr(execution, "_build_real_family_model", build_observed_model)

    def observe_ownership(root: Path, attempt: Any, receipt: Any) -> Path:
        assert not execution.CreateOnlyAttemptJournal(root).records(attempt.identity)
        assert supervisor.process_matches(receipt)
        return write_ownership(root, attempt, receipt)

    monkeypatch.setattr(supervisor, "write_process_ownership", observe_ownership)

    def fit(model: Any, batch: Any, *, training_blocks: tuple[str, ...]) -> dict[str, Any]:
        trained_models.add(model)
        capability = reference.capability_or_none(root)
        assert capability is not None and capability.epoch_evidence is not None
        capabilities.append(capability)
        assert training_blocks == batch.training_blocks
        records = execution.CreateOnlyAttemptJournal(root).ordered_records(runtime.PRIMARY_SCHEDULE)
        started = [
            record
            for record in records
            if record["status"] == "STARTED"
            and record["attempt_id"]
            not in {item["attempt_id"] for item in records if item["status"] != "STARTED"}
        ]
        assert len(started) == 1
        record = started[0]
        attempt = supervisor._attempt_from_record(record)
        assert supervisor.process_matches(supervisor._load_ownership(root, attempt, record))
        ownership = execution._read_json(
            root / "sessions/ownership" / f"{record['attempt_id']}.json"
        )
        assert ownership["pid"] == os.getpid()
        assert ownership["supervisor_epoch_id"] == execution._development_epoch_id.get()
        assert ownership["cache_receipt_identity"] == execution._development_cache_receipt.get()
        assert record["payload"]["supervisor_epoch_id"] == ownership["supervisor_epoch_id"]
        assert record["payload"]["cache_receipt_identity"] == ownership["cache_receipt_identity"]
        if failure is not None:
            raise failure
        return {
            "epochs": 1,
            "elapsed_seconds": 0.0,
            "parameter_count": runtime.model_parameter_count(model),
            "target_instruments": 20,
            "equal_instrument_loss": True,
            "training_batch_identity": batch.content_identity,
        }

    def seed_cpu(seed: int) -> None:
        torch.set_rng_state(torch.Generator(device="cpu").manual_seed(seed).get_state())

    monkeypatch.setattr(execution, "configure_deterministic_cuda", seed_cpu)
    monkeypatch.setattr(runtime, "fit_one_model", fit)
    monkeypatch.setattr(
        runtime,
        "predict_residual",
        lambda model, batch, **kwargs: torch.tensor(batch.values.copy()),
    )
    return capabilities, placements


def test_reference_supervised_lifecycle_reaches_outcome_blind_metadata_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from experiments.r4_residual_graph import terminal_support

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("terminal data or support construction was reached")

    monkeypatch.setattr(foundation, "load_parent_rows", forbidden)
    monkeypatch.setattr(execution, "load_parent_rows", forbidden)
    monkeypatch.setattr(execution, "_terminal_rows_from_input", forbidden)
    monkeypatch.setattr(terminal_support, "build_terminal_support", forbidden)
    _install_synthetic_batches(monkeypatch)
    preparation, root = _roots(tmp_path, monkeypatch, integrated=True)
    payload = reference.create_preparation_reference(root)
    before = {p: reference._regular_state(p) for p in preparation.rglob("*") if p.is_file()}
    capabilities, placements = _scientific_boundary(root, monkeypatch)
    supervisors: list[reference.PreparationCapability] = []

    class SynchronousWrapper:
        # This is wrapper/control-flow evidence, not an operating-system launch test.
        def __init__(self, argv: list[str], *, env: dict[str, str], pass_fds: tuple[int, ...]):
            assert env["CUBLAS_WORKSPACE_CONFIG"] == execution.CUBLAS_WORKSPACE_CONFIG
            supervisor_capability = reference.capability_or_none(root)
            assert supervisor_capability is not None
            supervisors.append(supervisor_capability)
            context = Context()
            wrapper_args = argv[3:].copy()
            fd_index = wrapper_args.index("--handshake-fd") + 1
            assert int(wrapper_args[fd_index]) == pass_fds[0]
            wrapper_args[fd_index] = str(os.dup(pass_fds[0]))
            self.returncode = context.run(execution.main, wrapper_args)
            wrapper_capability = capabilities[-1]
            assert wrapper_capability is not supervisor_capability
            assert not wrapper_capability.active
            assert context.run(reference._active.get) is None
            assert context.run(execution._development_epoch_id.get) is None
            assert context.run(execution._development_cache_receipt.get) is None
            assert reference.capability_or_none(root) is supervisor_capability

        def wait(self) -> int:
            return self.returncode

    # Replacing only the consuming module avoids mutating subprocess globally.
    monkeypatch.setattr(
        execution,
        "subprocess",
        SimpleNamespace(Popen=SynchronousWrapper, run=execution.subprocess.run),
    )
    assert execution.main(["--output-root", str(root), "development-supervise"]) == 0
    assert len(placements) == 30
    assert set(placements) == set(runtime.FITTED_FAMILY_IDS)
    assert len(capabilities) == 30 and len({id(cap) for cap in capabilities}) == 30
    assert len({id(cap) for cap in supervisors}) == 1
    assert not supervisors[0].active and reference._active.get() is None
    journal = execution.CreateOnlyAttemptJournal(root)
    records = journal.ordered_records(runtime.PRIMARY_SCHEDULE)
    assert len(records) == 60
    epoch = records[0]["payload"]["supervisor_epoch_id"]
    assert len(list((root / "sessions").glob("*.json"))) == 2
    for suffix, session_suffix in (("pre", ""), ("final", "-final")):
        receipt = execution._read_json(root / "cache-verification" / f"{epoch}-{suffix}.json")
        session = execution._read_json(root / "sessions" / f"{epoch}{session_suffix}.json")
        assert session["cache_receipts"] == [receipt["receipt_identity"]]
        assert session["exact_head"] == payload["execution_g0"]["code_head"]
        assert session["g0_identity"] == payload["execution_g0"]["identity"]
        for evidence in (session, receipt):
            assert evidence["output_root"] == str(root)
            assert evidence["execution_g0"] == payload["execution_g0"]
            assert evidence["preparation_reference_identity"] == payload["reference_identity"]
        assert {cache["stage"] for cache in receipt["caches"]} == {"DEV_2", "DEV_3"}
    for slot in execution._EXPECTED_DEVELOPMENT_SLOTS:
        slot_id = ":".join(map(str, slot))
        selected = [record for record in records if record["attempt"]["slot_id"] == slot_id]
        assert [record["status"] for record in selected] == ["STARTED", "SUCCEEDED"]
        attempt = supervisor._attempt_from_record(selected[0])
        bundle = execution.verify_attempt_bundle(root / "attempts" / slot_id / "attempt-0", attempt)
        seal = cast(dict[str, object], bundle["seal"])
        assert selected[1]["payload"]["seal_identity"] == seal["seal_identity"]
    assert not (root / "register/development-register.json").exists()
    assert not (root / "register/development-metrics.json").exists()
    assert execution.main(["--output-root", str(root), "close-register"]) == 0
    assert len(placements) == 60
    assert set(placements[30:]) == set(runtime.FITTED_FAMILY_IDS)
    metrics = execution._read_json(root / "register/development-metrics.json")
    assert metrics["support_row_count"] == 40
    assert set(metrics["families"]) == set(runtime.FITTED_FAMILY_IDS)

    class MetadataGateReached(Exception):
        pass

    def metadata_gate(*, parent: Any, runtime_config: Any, graph: Any) -> Any:
        from experiments.r4_residual_graph.terminal_support import TerminalSupportConfig

        assert parent.manifest_path == tmp_path / "lab/manifest.json"
        assert len(parent.child_identities) == 1
        assert isinstance(runtime_config, runtime.FrozenRuntimeConfig)
        TerminalSupportConfig.from_authenticated_parent(parent, runtime_config, graph)
        assert (
            reference.reference_binding(root)["preparation_reference_identity"]
            == payload["reference_identity"]
        )
        raise MetadataGateReached

    monkeypatch.setattr(foundation, "authenticate_terminal_metadata", metadata_gate)
    with pytest.raises(MetadataGateReached):
        execution.main(["--output-root", str(root), "terminal-support"])
    assert {p: reference._regular_state(p) for p in before} == before
    # The public failure boundary retains an outcome-blind closure report in result/.
    for name in ("support", "prediction", "metrics", "model"):
        assert not (root / name).exists()
    assert not list(root.rglob("*TERMINAL_FORMER_HOLDOUT*"))


def test_reference_wrapper_exception_expires_capability_and_resets_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_synthetic_batches(monkeypatch)
    _, root = _roots(tmp_path, monkeypatch, integrated=True)
    reference.create_preparation_reference(root)
    failure = RuntimeError("synthetic fit failure")
    capabilities, placements = _scientific_boundary(root, monkeypatch, failure=failure)
    with reference.preparation_entry(root) as outer:
        identity = execution._require_identity(root)
        receipts = verify_epoch_caches(root, "exception-pre")
        write_supervisor_session(
            root,
            "exception",
            exact_head=identity.code_head,
            g0_identity=identity.identity,
            cache_receipts=list(receipts),
        )
        read_fd, write_fd = os.pipe()
        context = Context()
        try:
            try:
                with pytest.raises(RuntimeError) as raised:
                    context.run(
                        execution._run_receipted_development_slot,
                        root,
                        slot=execution._EXPECTED_DEVELOPMENT_SLOTS[0],
                        epoch_id="exception",
                        cache_receipt_identity=receipts[0],
                        handshake_fd=os.dup(write_fd),
                    )
            finally:
                os.close(write_fd)
            assert raised.value is failure
            assert execution._read_wrapper_handshake(read_fd)
        finally:
            os.close(read_fd)
        assert capabilities[0] is not outer and not capabilities[0].active
        assert reference.capability_or_none(root) is outer
        assert context.run(reference._active.get) is None
        assert context.run(execution._development_epoch_id.get) is None
        assert context.run(execution._development_cache_receipt.get) is None
    records = execution.CreateOnlyAttemptJournal(root).ordered_records(runtime.PRIMARY_SCHEDULE)
    assert [record["status"] for record in records] == ["STARTED", "FAILED"]
    assert placements == []
    assert reference._active.get() is None
