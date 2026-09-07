from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import polars as pl
import pytest
import torch

import experiments.r4_residual_graph.prior_qualification_evidence as prior_evidence
import experiments.r4_residual_graph.projection_evidence as projection_evidence
import experiments.r4_residual_graph.qualification as qualification
from experiments.r4_residual_graph.attempt_artifacts import canonical_json, sha256_bytes
from experiments.r4_residual_graph.disk_projection import (
    DiskProjectionRejected,
    cache_byte_projection,
    create_additional_disk_projection,
)
from experiments.r4_residual_graph.prior_qualification_evidence import (
    create_prior_qualification_evidence,
)
from experiments.r4_residual_graph.projection_evidence import (
    CapacityObservation,
    create_operator_capacity_evidence,
    create_production_shape_evidence,
)
from experiments.r4_residual_graph.qualification import RuntimeTelemetry
from experiments.r4_residual_graph.runtime import FITTED_FAMILY_IDS
from experiments.r4_residual_graph.stage_cache import load_stage_cache_batches
from experiments.r4_residual_graph.synthetic_qualification import (
    build_synthetic_cache,
    synthetic_qualification_inputs,
)


def test_runtime_telemetry_is_explicit_and_counts_transfers() -> None:
    telemetry = RuntimeTelemetry()
    tensor = torch.zeros((2, 3), dtype=torch.float32)
    telemetry.record_h2d_transfer((tensor,))
    assert telemetry.h2d_calls == 1
    assert telemetry.h2d_bytes == tensor.numel() * tensor.element_size()
    assert RuntimeTelemetry() != telemetry


def test_synthetic_cache_is_authenticated_and_bounded(tmp_path: Path) -> None:
    cache_root, identity = build_synthetic_cache(tmp_path / "cache")
    training, prediction, verified = load_stage_cache_batches(cache_root, expected=identity)
    assert len(training.row_keys) == 20
    assert len(prediction.row_keys) == 20
    assert sum(cast(int, item["size"]) for item in verified.files) < 5_000_000_000
    projection = cache_byte_projection(
        tensor_schema={"lookback_steps": 61, "nodes": 20, "features": 26},
        train={
            "rows": 20,
            "timestamps": 1,
            "row_key_chars": max(map(len, training.row_keys)),
        },
        prediction={
            "rows": 20,
            "timestamps": 1,
            "row_key_chars": max(map(len, prediction.row_keys)),
        },
    )
    npy_inventory = sum(
        cast(int, item["size"]) for item in verified.files if str(item["path"]).endswith(".npy")
    )
    complete_inventory = sum(
        path.stat().st_size for path in verified.root.rglob("*") if path.is_file()
    )
    assert projection["array_containers"] == npy_inventory
    assert projection["npy_header_bytes"] == 19 * 128
    assert projection["array_payload_bytes"] + projection["npy_header_bytes"] == npy_inventory
    assert (
        projection["train_tensor_containers"]
        + projection["prediction_tensor_containers"]
        + projection["train_mapping_containers"]
        + projection["prediction_mapping_containers"]
        == npy_inventory
    )
    assert projection["total"] >= complete_inventory


def _production_shape_source(root: Path, *, production_scale: bool = False) -> Path:
    source = root / "production"
    (source / "config").mkdir(parents=True)
    (source / "input").mkdir()
    timestamp_counts = (
        {"DEV_1": 14_460, "DEV_2": 14_460, "DEV_3": 14_440}
        if production_scale
        else {"DEV_1": 2, "DEV_2": 3, "DEV_3": 4}
    )
    row_counts = (
        {"DEV_1": 254_550, "DEV_2": 260_490, "DEV_3": 256_100}
        if production_scale
        else timestamp_counts
    )
    rows = pl.concat(
        pl.DataFrame(
            {
                "block": [stage] * row_counts[stage],
                "decision_time": [index % timestamp_count for index in range(row_counts[stage])],
            }
        )
        for stage, timestamp_count in timestamp_counts.items()
    )
    foundation = source / "input/residual-foundation.parquet"
    development = source / "input/development-support.parquet"
    rows.write_parquet(foundation)
    rows.filter(pl.col("block") != "DEV_1").write_parquet(development)
    partition = source / "input/training-partition.json"
    partition.write_text("{}")
    manifest = source / "manifest.json"
    manifest.write_text("{}")
    (source / "input/residual-foundation.parquet.json").write_text(
        json.dumps({"stage_counts": row_counts})
    )

    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    (source / "config/execution-input.json").write_text(
        json.dumps(
            {
                "artifact_type": "R4.C_AUTHENTICATED_EXECUTION_INPUT",
                "foundation_file_sha256": digest(foundation),
                "development_support_file_sha256": digest(development),
                "partition_file_sha256": digest(partition),
                "foundation_path": str(foundation),
                "development_support_path": str(development),
                "manifest_path": str(manifest),
                "manifest_sha256": digest(manifest),
                "manifest_identity": "manifest",
                "g0_identity": "config",
            }
        )
    )
    (source / "config/g0-identity.json").write_text(json.dumps({"code_head": "source"}))
    return source


def _rewrite_evidence(path: Path, **changes: Any) -> None:
    payload = json.loads(path.read_text())
    payload.update(changes)
    payload.pop("evidence_identity")
    payload["evidence_identity"] = sha256_bytes(canonical_json(payload))
    path.write_text(json.dumps(payload, sort_keys=True))
    seal_path = path.with_name(f"{path.stem}-seal.json")
    seal = json.loads(seal_path.read_text())
    seal["evidence_identity"] = payload["evidence_identity"]
    seal["evidence_sha256"] = sha256_bytes(path.read_bytes())
    seal_path.write_text(json.dumps(seal, sort_keys=True))


def _mock_bulk_capacity(
    monkeypatch: pytest.MonkeyPatch, bulk_base: Path, *, available_bytes: int
) -> None:
    device = bulk_base.stat().st_dev
    monkeypatch.setattr(projection_evidence, "_BULK_BASE", bulk_base)
    monkeypatch.setattr(projection_evidence, "_MOUNT_POINT", bulk_base)
    monkeypatch.setattr(
        projection_evidence,
        "observe_bulk_capacity",
        lambda: CapacityObservation(
            1_078_442_151_936,
            available_bytes,
            str(bulk_base),
            "/dev/sde[/q-trad-bulkdata]",
            "ext4",
            device,
        ),
    )


def _mock_prior_inventory(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    specifications: list[tuple[str, Path, bool]] = []
    for generation in range(1, 9):
        benchmark = root / f"prior-{generation}-benchmark"
        benchmark.mkdir(parents=True)
        size = (
            7
            if generation < 5
            else (
                3_242_698
                if generation == 5
                else (848_051 if generation == 6 else (6_013 if generation == 7 else 16_322))
            )
        )
        (benchmark / "receipt.json").write_bytes(b"x" * size)
        specifications.append((f"generation-{generation}-benchmark", benchmark, True))
        cache = root / f"prior-{generation}-cache"
        if generation not in {4, 7, 8}:
            cache.mkdir()
            specifications.append((f"generation-{generation}-cache", cache, True))
        else:
            specifications.append((f"generation-{generation}-cache", cache, False))
    monkeypatch.setattr(prior_evidence, "_PRIOR_ROOTS", tuple(specifications))


def test_capacity_observation_rejects_symlink_and_mount_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    symlink = tmp_path / "bulk-link"
    symlink.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        projection_evidence.observe_bulk_capacity(symlink)

    monkeypatch.setattr(projection_evidence, "_MOUNT_POINT", tmp_path)
    monkeypatch.setattr(
        projection_evidence,
        "_mount_contract",
        lambda _: (str(tmp_path), "/wrong-device", "ext4"),
    )
    monkeypatch.setattr(
        projection_evidence.os,
        "statvfs",
        lambda _: SimpleNamespace(f_blocks=1, f_frsize=1, f_bavail=1),
    )
    with pytest.raises(ValueError, match="mount/device/statvfs"):
        projection_evidence.observe_bulk_capacity(tmp_path)


def test_capacity_receipt_binds_actual_bulk_contract_and_rejects_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "benchmark-9"
    output.mkdir()
    _mock_bulk_capacity(monkeypatch, tmp_path, available_bytes=1_023_584_808_961)
    with pytest.raises(ValueError, match="exceed operator-authorised"):
        create_operator_capacity_evidence(output, candidate_identity="candidate")
    _mock_bulk_capacity(monkeypatch, tmp_path, available_bytes=1_000_000_000_000)
    receipt = create_operator_capacity_evidence(output, candidate_identity="candidate")
    payload = json.loads(receipt.read_text())
    assert payload["observed_available_bytes"] == 1_023_584_808_960
    assert payload["observed_at_utc"] == "2026-09-02T14:47:54.373Z"
    assert payload["mount_source"] == "/dev/sde[/q-trad-bulkdata]"
    assert payload["filesystem"] == "ext4"
    assert payload["total_bytes"] == 1_078_442_151_936
    assert payload["reserve_bytes"] == 100_000_000_000
    assert payload["staging_root"] == payload["final_root"] == str(output)
    assert payload["cache_final_root"] == str(tmp_path / "benchmark-9-cache")
    assert payload["container_or_workspace_df_authoritative"] is False

    device = tmp_path.stat().st_dev
    monkeypatch.setattr(
        projection_evidence,
        "observe_bulk_capacity",
        lambda: CapacityObservation(
            1_078_442_151_936,
            1_000_000_000_001,
            str(tmp_path),
            "/dev/sde[/q-trad-bulkdata]",
            "ext4",
            device,
        ),
    )
    with pytest.raises(ValueError, match="drift"):
        projection_evidence.authenticate_capacity_evidence(payload, output_root=output)

    assert (
        Path("/data/q-trad/r4-p0/remediation-7/benchmark-17")
        == qualification._QUALIFICATION_OUTPUT_ROOT
    )


def test_post_cache_capacity_decline_stops_before_cuda_and_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_root, identity = build_synthetic_cache(tmp_path / "cache")
    output = tmp_path / "benchmark-9"
    output.mkdir()
    _mock_bulk_capacity(monkeypatch, tmp_path, available_bytes=1_000_000_000_000)
    shape = create_production_shape_evidence(
        output,
        source_root=_production_shape_source(tmp_path / "shape"),
        candidate_identity=identity.producer_head,
    )
    capacity = create_operator_capacity_evidence(output, candidate_identity=identity.producer_head)
    _mock_prior_inventory(monkeypatch, tmp_path / "prior")
    prior = create_prior_qualification_evidence(output, candidate_identity=identity.producer_head)
    projection = create_additional_disk_projection(
        output,
        shape_evidence=shape,
        capacity_evidence=capacity,
        prior_qualification_evidence=prior,
        identity=identity,
        candidate_identity=identity.producer_head,
    )
    required = json.loads(projection.read_text())["required_available_bytes"]
    _mock_bulk_capacity(monkeypatch, tmp_path, available_bytes=required - 1)

    def fail_if_cuda_started(_: int) -> None:
        raise AssertionError("CUDA qualification began before the post-cache capacity gate")

    monkeypatch.setattr(
        qualification,
        "_require_qualification_determinism",
        fail_if_cuda_started,
    )
    with pytest.raises(ValueError, match="post-cache current available"):
        qualification.qualify_cache(
            cache_root,
            identity,
            output,
            prebuild_projection=projection,
        )
    assert not (output / "qualification.json").exists()


def test_prebuild_projection_is_outcome_blind_sealed_and_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, identity = synthetic_qualification_inputs()
    output = tmp_path / "projection"
    output.mkdir()
    _mock_bulk_capacity(monkeypatch, tmp_path, available_bytes=1_000_000_000_000)
    shape = create_production_shape_evidence(
        output,
        source_root=_production_shape_source(tmp_path),
        candidate_identity=identity.producer_head,
    )
    capacity = create_operator_capacity_evidence(output, candidate_identity=identity.producer_head)
    _mock_prior_inventory(monkeypatch, tmp_path / "prior")
    prior = create_prior_qualification_evidence(output, candidate_identity=identity.producer_head)
    receipt = create_additional_disk_projection(
        output,
        shape_evidence=shape,
        capacity_evidence=capacity,
        prior_qualification_evidence=prior,
        identity=identity,
        candidate_identity=identity.producer_head,
    )
    payload = json.loads(receipt.read_text())
    seal = json.loads((output / "disk-projection-seal.json").read_text())
    assert payload["gate"] == "ACCEPTED"
    assert payload["inputs"]["outcomes_accessed"] is False
    assert payload["inputs"]["physical_host_available_bytes"] == 1_000_000_000_000
    assert payload["projected_additional_bytes"] < 204_869_271_716
    assert payload["required_available_bytes"] == (
        payload["projected_additional_bytes"] + 100_000_000_000
    )
    assert set(payload["components"]) == {
        "raw_tensor_store",
        "prepared_dev_2",
        "prepared_dev_3",
        "worker_source_staging_high_water",
        "peak_create_only_staging_duplicate_bytes",
        "dev_2_cache",
        "dev_3_cache",
        "later_separately_gated_cache_projection",
        "development_bundles_30",
        "later_gated_bundles_15",
        "prediction_shards",
        "journal_session_cache_verification_closure_receipts",
        "qualification_benchmarks",
        "retained_failed_payloads",
    }
    assert payload["components"]["worker_source_staging_high_water"] > 0
    assert payload["components"]["peak_create_only_staging_duplicate_bytes"] > 0
    assert seal["receipt_identity"] == payload["receipt_identity"]
    assert payload["inputs"]["stage_shapes"] == {
        "DEV_1": {"rows": 2, "unique_decision_timestamps": 2},
        "DEV_2": {"rows": 3, "unique_decision_timestamps": 3},
        "DEV_3": {"rows": 4, "unique_decision_timestamps": 4},
    }
    _rewrite_evidence(capacity, container_or_workspace_df_authoritative=True)
    with pytest.raises(ValueError, match="capacity evidence contract"):
        create_additional_disk_projection(
            output,
            shape_evidence=shape,
            capacity_evidence=capacity,
            prior_qualification_evidence=prior,
            identity=identity,
            candidate_identity=identity.producer_head,
        )
    _rewrite_evidence(capacity, container_or_workspace_df_authoritative=False)
    shape_payload = json.loads(shape.read_text())
    shape_payload["stages"] = {
        stage: {"rows": 20, "unique_decision_timestamps": 1}
        for stage in ("DEV_1", "DEV_2", "DEV_3")
    }
    _rewrite_evidence(shape, stages=shape_payload["stages"])
    with pytest.raises(ValueError, match="authenticated source"):
        create_additional_disk_projection(
            output,
            shape_evidence=shape,
            capacity_evidence=capacity,
            prior_qualification_evidence=prior,
            identity=identity,
            candidate_identity=identity.producer_head,
        )


def test_rejected_projection_is_sealed_before_cli_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "rejected"
    cache = tmp_path / "rejected-cache"
    source = _production_shape_source(tmp_path / "large", production_scale=True)
    _mock_bulk_capacity(monkeypatch, tmp_path, available_bytes=300_000_000_000)
    _mock_prior_inventory(monkeypatch, tmp_path / "prior")
    monkeypatch.setattr(qualification, "_QUALIFICATION_OUTPUT_ROOT", output.resolve())
    monkeypatch.setattr(
        qualification, "authenticate_candidate_validation", lambda *args, **kwargs: {}
    )
    monkeypatch.setattr(qualification, "configure_qualification_determinism", lambda _: None)
    with pytest.raises(DiskProjectionRejected) as caught:
        qualification.main([str(output), "--production-shape-root", str(source)])
    receipt = caught.value.receipt
    payload = json.loads(receipt.read_text())
    seal = json.loads((output / "disk-projection-seal.json").read_text())
    assert payload["gate"] == "REJECTED"
    policy = payload["qualification_benchmark_policy"]
    assert policy["retained_generations"] == 4
    assert policy["per_generation_allowance_bytes"] == 20_000_000
    independently_recomputed_benchmark_bytes = (
        policy["retained_generations"] * policy["per_generation_allowance_bytes"]
    )
    assert independently_recomputed_benchmark_bytes == 80_000_000
    assert (
        payload["components"]["qualification_benchmarks"]
        == independently_recomputed_benchmark_bytes
    )
    policy_without_identity = {
        key: value for key, value in policy.items() if key != "policy_identity"
    }
    assert policy["policy_identity"] == sha256_bytes(canonical_json(policy_without_identity))
    terminal_policy = payload["terminal_support_upper_bound_policy"]
    assert terminal_policy["maximum_rows"] == 664_380
    assert terminal_policy["maximum_unique_decision_timestamps"] == 36 * 24 * 60 + 9 * 60 + 30 + 1
    assert terminal_policy["outcomes_accessed"] is False
    terminal_without_identity = {
        key: value for key, value in terminal_policy.items() if key != "policy_identity"
    }
    assert terminal_policy["policy_identity"] == sha256_bytes(
        canonical_json(terminal_without_identity)
    )
    assert payload["cache_breakdowns"]["later_separately_gated"]["total"] == 151_745_190_592
    assert sum(payload["components"].values()) == payload["projected_additional_bytes"]
    assert payload["required_available_bytes"] == (
        payload["projected_additional_bytes"] + 100_000_000_000
    )
    assert payload["prior_qualification_bytes"] == 4_113_112
    independently_recomputed_with_prior = (
        independently_recomputed_benchmark_bytes + payload["prior_qualification_bytes"]
    )
    assert independently_recomputed_with_prior == 84_113_112
    assert payload["qualification_aggregate_cap_bytes"] == 5_000_000_000
    assert independently_recomputed_with_prior <= payload["qualification_aggregate_cap_bytes"]
    assert payload["required_available_bytes"] > payload["inputs"]["physical_host_available_bytes"]
    assert payload["threshold_comparisons"] == {
        "current_capacity_covers_projection_and_reserve": False,
        "qualification_projection_with_prior_within_cap": True,
    }
    assert payload["rejection_reasons"] == [
        {
            "code": "CURRENT_AVAILABLE_BYTES_BELOW_PROJECTION_AND_RESERVE",
            "observed_bytes": 300_000_000_000,
            "required_relation": "GREATER_THAN_OR_EQUAL",
            "threshold_bytes": payload["required_available_bytes"],
        }
    ]
    assert seal["receipt_sha256"] == sha256_bytes(receipt.read_bytes())

    assert seal["receipt_identity"] == payload["receipt_identity"]
    assert not cache.exists()
    assert not (output / "qualification.json").exists()
    assert not (output / "seal.json").exists()
    assert {path.name for path in output.iterdir()} == {
        "operator-capacity-evidence.json",
        "operator-capacity-evidence-seal.json",
        "production-shape-evidence.json",
        "production-shape-evidence-seal.json",
        "prior-qualification-evidence.json",
        "prior-qualification-evidence-seal.json",
        "disk-projection.json",
        "disk-projection-seal.json",
    }


def test_cli_requires_authenticated_validation_before_projection_cache_or_cuda(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "benchmark-9"
    source = tmp_path / "shape"
    source.mkdir()
    monkeypatch.setattr(qualification, "_QUALIFICATION_OUTPUT_ROOT", output.resolve())

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("projection, cache or CUDA was reached")

    monkeypatch.setattr(qualification, "configure_qualification_determinism", forbidden)
    monkeypatch.setattr(
        qualification,
        "authenticate_candidate_validation",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("validation invalid")),
    )
    with pytest.raises(ValueError, match="validation invalid"):
        qualification.main([str(output), "--production-shape-root", str(source)])
    assert not output.exists()


@pytest.mark.parametrize("comparison_failure", [False, True])
def test_driver_query_is_post_fit_and_does_not_discard_comparisons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, comparison_failure: bool
) -> None:
    cache_root, identity = build_synthetic_cache(tmp_path / "cache")
    calls: list[str] = []
    driver_queries: list[object] = []
    publications: list[str] = []
    original_run = subprocess.run
    original_create = qualification.create_json_once

    def query(command: Any, **kwargs: Any) -> Any:
        if command != qualification._DRIVER_QUERY:
            return original_run(command, **kwargs)
        assert calls == list(FITTED_FAMILY_IDS) * 2
        driver_queries.append(command)
        return subprocess.CompletedProcess(command, 3, "", "NVML unavailable")

    def publish(path: Path, payload: Any) -> None:
        publications.append(path.name)
        original_create(path, payload)

    monkeypatch.setattr(subprocess, "run", query)
    monkeypatch.setattr(qualification, "create_json_once", publish)
    monkeypatch.setattr(qualification.torch.cuda, "get_device_name", lambda *_: "test CUDA device")

    def fake_compare(family: str, *_: Any, seed: int) -> dict[str, Any]:
        calls.append(family)
        if comparison_failure:
            raise RuntimeError("CUDA/oracle comparison failed")
        numerical = {
            "grouped_loss_identity": family,
            "oracle_loss_identity": family,
            "grouped_gradient_identity": family,
            "oracle_gradient_identity": family,
        }
        return {
            "family": family,
            "seed": seed,
            "model_hash": family,
            "prediction_hash": family,
            "oracle_prediction_identity": family,
            "float64": numerical,
            "float32": numerical,
            "parameter_max_abs_delta": 0.0,
            "prediction_max_abs_delta": 0.0,
            "parameter_tolerance": 1e-3,
            "prediction_tolerance": 2e-6,
            "telemetry": {"optimizer_steps": 1},
        }

    monkeypatch.setattr(qualification, "compare_runtime_to_oracle", fake_compare)
    policy = {
        **dict(qualification.DETERMINISTIC_CUDA_POLICY),
        "cpu_seed": 17,
        "cuda_seed": 17,
        "bound_seed": 17,
        "config_identity": "config",
    }
    monkeypatch.setattr(qualification, "_require_qualification_determinism", lambda seed: policy)
    output = tmp_path / "qualification"
    if comparison_failure:
        with pytest.raises(RuntimeError, match="CUDA/oracle comparison failed"):
            qualification.qualify_cache(cache_root, identity, output)
        assert calls == [FITTED_FAMILY_IDS[0]]
        assert driver_queries == []
        assert publications == []
        assert not (output / "qualification.json").exists()
        assert not (output / "seal.json").exists()
        return
    receipt = qualification.qualify_cache(cache_root, identity, output)
    payload = json.loads(receipt.read_text())
    seal = json.loads((output / "seal.json").read_text())
    assert calls == list(FITTED_FAMILY_IDS) * 2
    assert driver_queries == [qualification._DRIVER_QUERY]
    assert publications == ["qualification.json", "seal.json"]
    driver = payload["device"]["driver"]
    assert driver["status"] == "UNAVAILABLE"
    assert driver["failure_class"] == "WSL_NVML_TELEMETRY_UNAVAILABLE"
    assert driver["version"] is None
    qualification.validate_driver_observation(driver)
    assert len(driver_queries) == 1
    assert seal["receipt_sha256"] == sha256_bytes(receipt.read_bytes())
    assert payload["receipt_identity"] == sha256_bytes(
        canonical_json({key: value for key, value in payload.items() if key != "receipt_identity"})
    )
    forged = {**payload, "device": {**payload["device"], "driver": {**driver, "version": "999.1"}}}
    assert payload["receipt_identity"] != sha256_bytes(
        canonical_json({key: value for key, value in forged.items() if key != "receipt_identity"})
    )
    with pytest.raises(ValueError, match="inconsistent"):
        qualification.validate_driver_observation(forged["device"]["driver"])
    assert payload["classification"] == "BENCHMARK_NOT_SCIENTIFIC"
    assert payload["scientific_performance"] == "NOT_COMPUTED"
    assert payload["deterministic_hash_match"] is True
    assert payload["schema"] == "R4-D-QUALIFICATION-V2"
    assert payload["candidate_identity"] == identity.producer_head
    assert payload["application_identity"]
    assert payload["fixture_identity"]
    assert set(payload["device"]) == {"name", "identity", "driver", "cuda", "torch", "python"}
    assert payload["deterministic_policy_identity"]
    assert payload["deterministic_policy"] == policy
    assert payload["cache"]["builder_semantic_closure"]
    assert payload["cache"]["files"]
    assert set(payload["resources"]) == {
        "wall_seconds",
        "cpu_user_seconds",
        "cpu_system_seconds",
        "max_rss_kib",
        "cgroup_memory_current",
        "cgroup_memory_peak",
        "cuda_allocated_peak",
        "cuda_reserved_peak",
    }
    assert seal["receipt_identity"] == payload["receipt_identity"]
    with pytest.raises(FileExistsError, match="already exists"):
        qualification.qualify_cache(cache_root, identity, output)
    with pytest.raises((ValueError, AssertionError)):
        qualification.qualify_cache(
            cache_root,
            replace(identity, producer_head="0" * 40),
            tmp_path / "mismatched",
        )


@pytest.mark.parametrize(
    ("returncode", "stdout", "status", "failure", "version"),
    [
        (0, " 580.82.07\n", "AVAILABLE", None, "580.82.07"),
        (3, "580.82.07\n", "UNAVAILABLE", "WSL_NVML_TELEMETRY_UNAVAILABLE", None),
        (1, "", "UNAVAILABLE", "NONZERO_EXIT", None),
        (-9, "", "UNAVAILABLE", "NONZERO_EXIT", None),
        (0, " \n", "INVALID", "EMPTY_OUTPUT", None),
        (0, "unknown", "INVALID", "MALFORMED_OUTPUT", None),
        (0, "580.1\n580.1\n", "INVALID", "MALFORMED_OUTPUT", None),
        (0, "580", "INVALID", "MALFORMED_OUTPUT", None),
        (0, "\uff15\uff18\uff10.1", "INVALID", "MALFORMED_OUTPUT", None),
        (0, "580.1\n" + " " * 4096, "INVALID", "TRUNCATED_OUTPUT", None),
    ],
)
def test_driver_observation_exit_results(
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    stdout: str,
    status: str,
    failure: str | None,
    version: str | None,
) -> None:
    calls: list[object] = []

    def query(command: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert command == ("nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader")
        assert kwargs == {
            "check": False,
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "timeout": 2,
        }
        calls.append(command)
        return subprocess.CompletedProcess(command, returncode, stdout, "e" * 5000)

    monkeypatch.setattr(subprocess, "run", query)
    observation = qualification._observe_driver()
    assert observation["status"] == status
    assert observation["failure_class"] == failure
    assert observation["version"] == version
    assert observation["returncode"] == returncode
    assert observation["exception_class"] is None
    assert observation["stdout"] == stdout[:4096]
    assert observation["stdout_truncated"] is (len(stdout) > 4096)
    assert observation["stderr"] == "e" * 4096
    assert observation["stderr_truncated"] is True
    qualification.validate_driver_observation(json.loads(json.dumps(observation)))
    assert len(calls) == 1


@pytest.mark.parametrize(
    "error",
    [
        subprocess.TimeoutExpired(qualification._DRIVER_QUERY, 2, b"x" * 5000, b"\xff" * 5000),
        subprocess.TimeoutExpired(qualification._DRIVER_QUERY, 2),
        OSError(5, "I/O failure"),
        FileNotFoundError(2, "nvidia-smi absent"),
        PermissionError(13, "e" * 5000),
    ],
)
def test_driver_observation_query_exceptions(
    monkeypatch: pytest.MonkeyPatch, error: OSError | subprocess.TimeoutExpired
) -> None:
    calls = 0

    def query(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        raise error

    monkeypatch.setattr(subprocess, "run", query)
    observation = qualification._observe_driver()
    assert observation["status"] == "UNAVAILABLE"
    assert observation["failure_class"] == type(error).__name__
    assert observation["exception_class"] == type(error).__name__
    assert observation["returncode"] is None
    assert observation["version"] is None
    for stream in ("stdout", "stderr"):
        assert len(observation[stream]) <= 4096
    if isinstance(error, subprocess.TimeoutExpired):
        if error.output is not None:
            assert observation["stdout"] == "x" * 4096
            assert observation["stderr"] == "\ufffd" * 4096
            assert observation["stdout_truncated"] is True
            assert observation["stderr_truncated"] is True
        else:
            assert observation["stdout"] == observation["stderr"] == ""
    else:
        assert observation["stderr"] == str(error)[:4096]
    qualification.validate_driver_observation(json.loads(json.dumps(observation)))
    assert calls == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "AVAILABLE"},
        {"version": "580.1"},
        {"failure_class": "NONZERO_EXIT"},
        {"returncode": False},
        {"exception_class": "TimeoutExpired"},
        {"query": ["nvidia-smi"]},
        {"timeout_seconds": 3},
        {"stderr": "x" * 4097},
        {"stdout_truncated": True},
        {"stderr_truncated": 1},
        {"extra": None},
    ],
)
def test_driver_observation_contract_rejects_inconsistent_evidence_without_query(
    monkeypatch: pytest.MonkeyPatch, changes: dict[str, Any]
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("post-authentication must not query the driver")

    monkeypatch.setattr(subprocess, "run", forbidden)
    observation = {
        "query": list(qualification._DRIVER_QUERY),
        "timeout_seconds": 2,
        "returncode": 3,
        "exception_class": None,
        "status": "UNAVAILABLE",
        "failure_class": "WSL_NVML_TELEMETRY_UNAVAILABLE",
        "version": None,
        "stdout": "",
        "stderr": "NVML unavailable",
        "stdout_truncated": False,
        "stderr_truncated": False,
    }
    qualification.validate_driver_observation(observation)
    with pytest.raises(ValueError, match="driver observation"):
        qualification.validate_driver_observation({**observation, **changes})


def test_deterministic_configuration_is_pre_cuda_and_drift_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []
    require_policy = qualification._require_qualification_determinism
    policy = {
        **dict(qualification.DETERMINISTIC_CUDA_POLICY),
        "cpu_seed": 17,
        "cuda_seed": 17,
        "bound_seed": 17,
        "config_identity": "config",
    }
    monkeypatch.setattr(torch.cuda, "is_initialized", lambda: False)
    monkeypatch.setattr(qualification, "configure_deterministic_cuda", calls.append)
    monkeypatch.setattr(qualification, "_require_qualification_determinism", lambda seed: policy)
    assert qualification.configure_qualification_determinism(17) == policy
    assert calls == [17]

    monkeypatch.setattr(torch.cuda, "is_initialized", lambda: True)
    with pytest.raises(RuntimeError, match="before CUDA initialisation"):
        qualification.configure_qualification_determinism(17)

    monkeypatch.setattr(
        qualification,
        "_observed_deterministic_policy",
        lambda seed: {**policy, "cudnn_deterministic": False},
    )
    monkeypatch.setattr(qualification, "_require_qualification_determinism", require_policy)
    with pytest.raises(RuntimeError, match="deterministic policy drift"):
        qualification._require_qualification_determinism(17)


@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA qualification contract")
@pytest.mark.parametrize(
    ("family", "local_calls", "shared_calls"),
    (
        (FITTED_FAMILY_IDS[0], 2, 0),
        (FITTED_FAMILY_IDS[1], 0, 2),
        (FITTED_FAMILY_IDS[2], 0, 2),
        (FITTED_FAMILY_IDS[3], 0, 2),
        (FITTED_FAMILY_IDS[4], 0, 2),
    ),
)
def test_rowwise_oracle_matches_production_and_reports_counters(
    tmp_path: Path, family: str, local_calls: int, shared_calls: int
) -> None:
    cache_root, identity = build_synthetic_cache(tmp_path / "cache")
    training, prediction, _ = load_stage_cache_batches(cache_root, expected=identity)
    events: list[str] = []
    comparison = qualification.compare_runtime_to_oracle(
        family,
        training,
        prediction,
        seed=17,
        phase_callback=lambda phase: events.append(f"phase:{phase}"),
        release_callback=lambda: events.append("release"),
    )
    assert comparison["float64"]["loss_abs_delta"] <= 1e-12
    assert comparison["float64"]["gradient_max_abs_delta"] <= 1e-12
    assert comparison["float32"]["grouped_loss_identity"]
    assert comparison["float32"]["oracle_gradient_identity"]
    assert comparison["prediction_max_abs_delta"] <= 1e-6
    telemetry = comparison["telemetry"]
    assert telemetry["optimizer_steps"] == 1
    assert telemetry["backward_calls"] == 1
    assert telemetry["rows"] == 40
    assert telemetry["timestamps"] == 2
    assert telemetry["materialisation_seconds"] > 0
    assert telemetry["materialisation_calls"] > 0
    assert telemetry["preprocessing_calls"] == 0
    assert telemetry["preprocessing_seconds"] == 0
    assert telemetry["h2d_calls"] > 0
    assert telemetry["h2d_seconds"] > 0
    assert telemetry["forward_calls"] > 0
    assert telemetry["forward_seconds"] > 0
    assert telemetry["backward_seconds"] > 0
    assert telemetry["optimiser_seconds"] > 0
    assert telemetry["timestamp_batch_calls"] == 2
    assert telemetry["representation_timestamp_calls"] == telemetry["timestamps"]
    assert telemetry["local_vectorised_calls"] == local_calls
    assert telemetry["shared_representation_calls"] == shared_calls
    assert telemetry["rows_per_second"] > 0
    assert telemetry["peak_cuda_allocated_bytes"] > 0
    assert telemetry["peak_cuda_reserved_bytes"] > 0
    assert events == [
        "release",
        "phase:float64_grouped_forward",
        "phase:float64_grouped_backward",
        "release",
        "phase:float64_oracle_forward",
        "phase:float64_oracle_backward",
        "release",
        "phase:float32_grouped_forward",
        "phase:float32_grouped_backward",
        "release",
        "phase:float32_oracle_forward",
        "phase:float32_oracle_backward",
        "release",
        "release",
        "phase:oracle_one_step_forward",
        "phase:oracle_one_step_backward",
        "phase:oracle_one_step_gradient_copy",
        "phase:oracle_one_step_optimizer_step",
        "phase:oracle_one_step_parameter_copy",
        "release",
        "phase:oracle_one_step_prediction",
        "release",
        "phase:production_fit",
        "release",
        "phase:production_prediction",
        "release",
        "release",
    ]
    assert (
        comparison["fresh_initial_model_identity"]
        == comparison["oracle_evidence"]["initial_model_identity"]
    )
    assert comparison["oracle_evidence_identity"]


@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA qualification contract")
@pytest.mark.parametrize("family", FITTED_FAMILY_IDS)
def test_multichunk_cached_oracle_preserves_one_step_and_exact_gradients(
    tmp_path: Path, family: str
) -> None:
    inputs = synthetic_qualification_inputs(dev1_timestamp_count=5)
    cache_root, identity = build_synthetic_cache(tmp_path / "cache", inputs=inputs)
    training, prediction, _ = load_stage_cache_batches(cache_root, expected=identity)

    comparison = qualification.compare_runtime_to_oracle(family, training, prediction, seed=17)

    assert comparison["float64"]["loss_abs_delta"] <= 1e-12
    assert comparison["float64"]["gradient_max_abs_delta"] <= 1e-12
    assert comparison["float32"]["grouped_loss_identity"]
    assert comparison["float32"]["oracle_gradient_identity"]
    assert comparison["prediction_max_abs_delta"] <= 1e-6
    assert comparison["chunked_pre_step_gradient_max_abs_delta"] <= 1e-4
    assert (
        comparison["chunked_final_parameter_max_abs_delta"]
        <= comparison["chunked_final_parameter_tolerance"]
    )
    telemetry = comparison["telemetry"]
    assert telemetry["timestamps"] == 6
    assert telemetry["timestamp_batch_calls"] == 2
    assert telemetry["h2d_calls"] == 3
    assert telemetry["h2d_bytes"] > 0
    assert telemetry["backward_calls"] == 1
    assert telemetry["optimizer_steps"] == 1


@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA qualification contract")
def test_independent_oracle_detects_grouped_gather_and_objective_defects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_root, identity = build_synthetic_cache(tmp_path / "cache")
    training, prediction, _ = load_stage_cache_batches(cache_root, expected=identity)
    model_type = type(qualification.build_family_model(FITTED_FAMILY_IDS[0]))
    original = cast(Callable[..., torch.Tensor], cast(Any, model_type).forward_grouped)

    def shifted(self: Any, *args: Any, **kwargs: Any) -> torch.Tensor:
        return original(self, *args, **kwargs) + 0.01

    monkeypatch.setattr(model_type, "forward_grouped", shifted)
    with pytest.raises(AssertionError, match="grouped/oracle"):
        qualification.compare_runtime_to_oracle(FITTED_FAMILY_IDS[0], training, prediction, seed=17)
    monkeypatch.setattr(model_type, "forward_grouped", original)

    original_counts = qualification._grouped_counts
    monkeypatch.setattr(
        qualification,
        "_grouped_counts",
        lambda targets: tuple(count * 2 for count in original_counts(targets)),
    )
    with pytest.raises(AssertionError, match="grouped/oracle"):
        qualification.compare_runtime_to_oracle(FITTED_FAMILY_IDS[0], training, prediction, seed=17)


@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA qualification contract")
@pytest.mark.parametrize("family", FITTED_FAMILY_IDS)
@pytest.mark.parametrize("dtype", (torch.float64, torch.float32))
def test_sequential_objective_evidence_matches_simultaneous_reference(
    tmp_path: Path, family: str, dtype: torch.dtype
) -> None:
    import io

    cache_root, identity = build_synthetic_cache(tmp_path / "cache")
    training, _, _ = load_stage_cache_batches(cache_root, expected=identity)
    torch.manual_seed(17)
    torch.cuda.manual_seed_all(17)
    initial = qualification.build_family_model(family)
    assert isinstance(initial, torch.nn.Module)
    initial = initial.to("cuda")
    initial_state = qualification._state_bytes(initial)
    del initial

    grouped = qualification.build_family_model(family)
    oracle = qualification.build_family_model(family)
    assert isinstance(grouped, torch.nn.Module)
    assert isinstance(oracle, torch.nn.Module)
    grouped = grouped.to(device="cuda", dtype=dtype)
    oracle = oracle.to(device="cuda", dtype=dtype)
    state = torch.load(io.BytesIO(initial_state), weights_only=True)
    grouped.load_state_dict(state)
    oracle.load_state_dict(state)
    grouped.zero_grad(set_to_none=True)
    oracle.zero_grad(set_to_none=True)
    grouped_loss, _ = qualification._grouped_objective(grouped, training, dtype=dtype)
    oracle_loss, _ = qualification._oracle_objective(oracle, training, dtype=dtype)
    grouped_loss.backward()
    oracle_loss.backward()
    simultaneous = {
        "grouped_loss": float(grouped_loss.detach().cpu()),
        "oracle_loss": float(oracle_loss.detach().cpu()),
        "grouped_gradient_identity": qualification._gradient_identity(
            qualification._gradient_arrays(grouped)
        ),
        "oracle_gradient_identity": qualification._gradient_identity(
            qualification._gradient_arrays(oracle)
        ),
    }
    del grouped_loss, oracle_loss, grouped, oracle
    torch.cuda.empty_cache()

    phases: list[str] = []
    sequential = qualification._compare_objective_and_gradients(
        family,
        initial_state,
        training,
        dtype=dtype,
        release_callback=torch.cuda.empty_cache,
        phase_callback=phases.append,
    )
    assert phases == ["grouped_forward", "grouped_backward", "oracle_forward", "oracle_backward"]

    assert {key: sequential[key] for key in simultaneous} == simultaneous


@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA qualification contract")
def test_checkpointed_oracle_preserves_rng_and_uses_non_reentrant_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_root, identity = build_synthetic_cache(tmp_path / "cache")
    training, _, _ = load_stage_cache_batches(cache_root, expected=identity)
    torch.manual_seed(17)
    torch.cuda.manual_seed_all(17)
    initial = qualification.build_family_model("POOLED_NON_GRAPH_RESIDUAL")
    assert isinstance(initial, torch.nn.Module)
    initial = initial.to("cuda")
    initial_state = qualification._state_bytes(initial)
    del initial
    cpu_rng_state = torch.get_rng_state().clone()
    cuda_rng_state = torch.cuda.get_rng_state().clone()
    observed_options: list[tuple[bool, bool]] = []
    real_checkpoint = qualification.checkpoint

    def observed_checkpoint(
        function: Callable[..., torch.Tensor], *args: Any, **kwargs: Any
    ) -> torch.Tensor:
        observed_options.append((kwargs["use_reentrant"], kwargs["preserve_rng_state"]))
        return real_checkpoint(function, *args, **kwargs)

    monkeypatch.setattr(qualification, "checkpoint", observed_checkpoint)
    qualification._compare_objective_and_gradients(
        "POOLED_NON_GRAPH_RESIDUAL",
        initial_state,
        training,
        dtype=torch.float64,
        release_callback=torch.cuda.empty_cache,
        phase_callback=lambda unused: None,
    )

    assert observed_options
    assert all(options == (False, True) for options in observed_options)
    assert torch.equal(torch.get_rng_state(), cpu_rng_state)
    assert torch.equal(torch.cuda.get_rng_state(), cuda_rng_state)


@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA qualification contract")
@pytest.mark.parametrize("family", FITTED_FAMILY_IDS)
def test_checkpointed_oracle_one_step_matches_uncheckpointed_reference(
    tmp_path: Path, family: str
) -> None:

    inputs = synthetic_qualification_inputs(dev1_timestamp_count=5)
    cache_root, identity = build_synthetic_cache(tmp_path / "cache", inputs=inputs)
    training, prediction, _ = load_stage_cache_batches(cache_root, expected=identity)
    torch.manual_seed(17)
    torch.cuda.manual_seed_all(17)
    initial = qualification.build_family_model(family)
    assert isinstance(initial, torch.nn.Module)
    initial = initial.to("cuda")
    initial_state = qualification._state_bytes(initial)
    del initial
    torch.cuda.empty_cache()
    cpu_rng_state = torch.get_rng_state().clone()
    cuda_rng_state = torch.cuda.get_rng_state().clone()

    reference = qualification._oracle_one_step_state(
        family,
        initial_state,
        training,
        prediction,
        checkpoint_activations=False,
    )
    reference_cpu_rng_state = torch.get_rng_state().clone()
    reference_cuda_rng_state = torch.cuda.get_rng_state().clone()
    torch.set_rng_state(cpu_rng_state)
    torch.cuda.set_rng_state(cuda_rng_state)
    phases: list[str] = []
    checkpointed = qualification._oracle_one_step_state(
        family,
        initial_state,
        training,
        prediction,
        phase_callback=phases.append,
        release_callback=torch.cuda.empty_cache,
    )

    pairs = zip(checkpointed[:2], reference[:2], strict=True)
    for checkpointed_mapping, reference_mapping in pairs:
        assert checkpointed_mapping.keys() == reference_mapping.keys()
        for name in checkpointed_mapping:
            assert torch.equal(
                torch.from_numpy(checkpointed_mapping[name]),
                torch.from_numpy(reference_mapping[name]),
            )
    assert torch.equal(torch.from_numpy(checkpointed[2]), torch.from_numpy(reference[2]))
    assert qualification._gradient_identity(checkpointed[0]) == qualification._gradient_identity(
        reference[0]
    )
    assert qualification._gradient_identity(checkpointed[1]) == qualification._gradient_identity(
        reference[1]
    )
    assert qualification._array_identity(checkpointed[2]) == qualification._array_identity(
        reference[2]
    )
    assert torch.equal(torch.get_rng_state(), reference_cpu_rng_state)
    assert torch.equal(torch.cuda.get_rng_state(), reference_cuda_rng_state)
    assert phases == [
        "forward",
        "backward",
        "gradient_copy",
        "optimizer_step",
        "parameter_copy",
        "prediction",
    ]


@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA qualification contract")
def test_reusable_oracle_reports_fine_one_step_phases(tmp_path: Path) -> None:
    cache_root, identity = build_synthetic_cache(tmp_path / "cache")
    training, prediction, _ = load_stage_cache_batches(cache_root, expected=identity)
    phases: list[str] = []

    qualification.build_reusable_oracle_evidence(
        FITTED_FAMILY_IDS[0],
        training,
        prediction,
        seed=17,
        phase_callback=phases.append,
        release_callback=torch.cuda.empty_cache,
    )

    assert phases[-6:] == [
        "oracle_one_step_forward",
        "oracle_one_step_backward",
        "oracle_one_step_gradient_copy",
        "oracle_one_step_optimizer_step",
        "oracle_one_step_parameter_copy",
        "oracle_one_step_prediction",
    ]
