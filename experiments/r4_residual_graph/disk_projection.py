"""Outcome-blind authenticated additional-disk projection for qualification."""

from __future__ import annotations

import hashlib
import inspect
import io
import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from .attempt_artifacts import canonical_json, create_json_once, sha256_bytes
from .prior_qualification_evidence import authenticate_prior_qualification_evidence
from .projection_evidence import authenticate_capacity_evidence
from .runtime import (
    FITTED_FAMILY_IDS,
    PRIMARY_SEEDS,
    CreateOnlyArtifacts,
    build_family_model,
    environment_identity,
    model_parameter_count,
)
from .stage_cache import CacheInput, cache_builder_semantic_closure

_PROJECTION_VERSION = "R4-D-ADDITIONAL-DISK-PROJECTION-V4"
_REQUIRED_RESERVE_BYTES = 100_000_000_000
_QUALIFICATION_BENCHMARK_GENERATIONS = 4
_QUALIFICATION_BENCHMARK_PER_GENERATION_BYTES = 20_000_000
_QUALIFICATION_AGGREGATE_CAP_BYTES = 5_000_000_000
_TERMINAL_START_UTC = "2026-06-26T14:06:00Z"
_TERMINAL_END_UTC = "2026-08-01T23:36:00Z"
_TERMINAL_CADENCE_SECONDS = 60
_TERMINAL_UNIVERSE_SIZE = 20
_TERMINAL_RAW_ROW_UPPER_BOUND = 664_380
_TERMINAL_TIMESTAMP_UPPER_BOUND = 52_411
_DEV_BUNDLE_COUNT = len(FITTED_FAMILY_IDS) * len(PRIMARY_SEEDS) * 2
_LATER_BUNDLE_COUNT = len(FITTED_FAMILY_IDS) * len(PRIMARY_SEEDS)


class DiskProjectionRejected(RuntimeError):
    """The durable disk projection receipt records a rejected prebuild gate."""

    def __init__(self, receipt: Path) -> None:
        self.receipt = receipt
        super().__init__(f"additional-disk projection rejected; sealed receipt: {receipt}")


def _load_sealed_evidence(path: Path, *, schema: str, seal_schema: str) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    seal = json.loads(path.with_name(f"{path.stem}-seal.json").read_text())
    identity_payload = dict(payload)
    identity = identity_payload.pop("evidence_identity")
    if (
        payload["schema"] != schema
        or seal["schema"] != seal_schema
        or seal["evidence"] != path.name
        or seal["evidence_sha256"] != sha256_bytes(path.read_bytes())
        or seal["evidence_identity"] != identity
        or sha256_bytes(canonical_json(identity_payload)) != identity
    ):
        raise ValueError(f"authenticated evidence drift: {path.name}")
    return payload


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _authenticate_production_shapes(shape: dict[str, Any]) -> None:
    source_root = Path(shape["source_root"])
    for relative, evidence in shape["source_files"].items():
        path = source_root / relative
        if (
            not path.is_file()
            or path.is_symlink()
            or path.stat().st_size != evidence["size_bytes"]
            or _sha256_file(path) != evidence["sha256"]
        ):
            raise ValueError("production-shape source provenance drift")
    observed = (
        pl.scan_parquet(source_root / "input/residual-foundation.parquet")
        .select("block", "decision_time")
        .group_by("block")
        .agg(
            pl.len().alias("rows"),
            pl.col("decision_time").n_unique().alias("unique_decision_timestamps"),
        )
        .collect()
    )
    counts = {
        str(row["block"]): {
            "rows": int(row["rows"]),
            "unique_decision_timestamps": int(row["unique_decision_timestamps"]),
        }
        for row in observed.iter_rows(named=True)
    }
    if counts != shape["stages"]:
        raise ValueError("production-shape evidence disagrees with authenticated source")


def _npy_container_bytes(shape: tuple[int, ...], dtype: str) -> int:
    value_dtype = np.dtype(dtype)
    header = io.BytesIO()
    np.lib.format.write_array_header_1_0(
        header,
        {"descr": value_dtype.str, "fortran_order": False, "shape": shape},
    )
    return len(header.getvalue()) + int(np.prod(shape, dtype=np.int64)) * value_dtype.itemsize


def cache_byte_projection(
    *, tensor_schema: dict[str, int], train: dict[str, int], prediction: dict[str, int]
) -> dict[str, int]:
    """Project exact NPY containers plus explicit conservative JSON metadata allowances."""
    lookback = tensor_schema["lookback_steps"]
    nodes = tensor_schema["nodes"]
    features = tensor_schema["features"]

    def tensor_arrays(timestamps: int) -> int:
        feature_shape = (timestamps, lookback, nodes, features)
        node_shape = (timestamps, lookback, nodes)
        return (
            _npy_container_bytes(feature_shape, "<f4")
            + _npy_container_bytes(feature_shape, "|b1") * 2
            + _npy_container_bytes(node_shape, "|b1")
        )

    train_rows = train["rows"]
    prediction_rows = prediction["rows"]
    train_row_key_chars = train.get("row_key_chars", 256)
    prediction_row_key_chars = prediction.get("row_key_chars", 256)
    train_mappings = (
        _npy_container_bytes((train_rows,), "<i8") * 2
        + _npy_container_bytes((train_rows,), "|b1")
        + _npy_container_bytes((train_rows,), "<i1")
        + _npy_container_bytes((train_rows,), "<f4") * 2
        + _npy_container_bytes((train_rows,), f"<U{train_row_key_chars}")
    )
    prediction_mappings = (
        _npy_container_bytes((prediction_rows,), "<i8") * 2
        + _npy_container_bytes((prediction_rows,), "|b1")
        + _npy_container_bytes((prediction_rows,), f"<U{prediction_row_key_chars}")
    )
    train_tensor_containers = tensor_arrays(train["timestamps"])
    prediction_tensor_containers = tensor_arrays(prediction["timestamps"])
    array_containers = (
        train_tensor_containers
        + prediction_tensor_containers
        + train_mappings
        + prediction_mappings
    )
    # The real schema writes eleven train and eight prediction NPY containers.
    npy_header_bytes = 19 * 128
    # Batch metadata retains only small identities, timestamp identities and block order.
    manifest_metadata_allowance = (train["timestamps"] + prediction["timestamps"]) * 256 + 1_000_000
    prediction_keys_allowance = prediction_rows * 256
    seal_and_directory_metadata_allowance = 16_384
    return {
        "train_tensor_containers": train_tensor_containers,
        "prediction_tensor_containers": prediction_tensor_containers,
        "train_mapping_containers": train_mappings,
        "prediction_mapping_containers": prediction_mappings,
        "npy_header_bytes": npy_header_bytes,
        "array_payload_bytes": array_containers - npy_header_bytes,
        "array_containers": array_containers,
        "manifest_metadata_allowance": manifest_metadata_allowance,
        "prediction_keys_allowance": prediction_keys_allowance,
        "seal_and_directory_metadata_allowance": seal_and_directory_metadata_allowance,
        "total": array_containers
        + manifest_metadata_allowance
        + prediction_keys_allowance
        + seal_and_directory_metadata_allowance,
    }


def raw_store_byte_projection(*, tensor_schema: dict[str, int], timestamps: int) -> dict[str, int]:
    """Project every persistent raw-store NPY container and conservative metadata."""
    lookback = tensor_schema["lookback_steps"]
    nodes = tensor_schema["nodes"]
    features = tensor_schema["features"]
    feature_shape = (timestamps, lookback, nodes, features)
    node_shape = (timestamps, lookback, nodes)
    tensor_containers = (
        _npy_container_bytes(feature_shape, "<f4")
        + 2 * _npy_container_bytes(feature_shape, "|b1")
        + _npy_container_bytes(node_shape, "|b1")
    )
    timestamp_chars = 32
    timestamps_container = _npy_container_bytes((timestamps,), f"<U{timestamp_chars}")
    identities_container = _npy_container_bytes((timestamps,), "<U64")
    metadata = timestamps * 256 + 1_000_000
    total = tensor_containers + timestamps_container + identities_container + metadata
    return {
        "tensor_containers": tensor_containers,
        "timestamps_container": timestamps_container,
        "tensor_identities_container": identities_container,
        "metadata_allowance": metadata,
        "total": total,
    }


def prepared_stage_byte_projection(*, training_rows: int, prediction_rows: int) -> int:
    """Project one persistent prepared-stage package including both row sets."""
    training = (
        2 * _npy_container_bytes((training_rows,), "<i8")
        + _npy_container_bytes((training_rows,), "|b1")
        + _npy_container_bytes((training_rows,), "<i1")
        + 2 * _npy_container_bytes((training_rows,), "<f4")
        + _npy_container_bytes((training_rows,), "<U256")
    )
    prediction = (
        2 * _npy_container_bytes((prediction_rows,), "<i8")
        + _npy_container_bytes((prediction_rows,), "|b1")
        + _npy_container_bytes((prediction_rows,), "<U256")
    )
    return training + prediction + (training_rows + prediction_rows) * 256 + 1_000_000


def create_additional_disk_projection(
    output_root: Path,
    *,
    shape_evidence: Path,
    capacity_evidence: Path,
    prior_qualification_evidence: Path,
    identity: CacheInput,
    candidate_identity: str,
) -> Path:
    """Authenticate evidence and seal the mandatory executable prebuild gate."""
    if identity.producer_head != candidate_identity:
        raise ValueError("authenticated cache input does not match the projected candidate")
    shape = _load_sealed_evidence(
        shape_evidence,
        schema="R4-D-PRODUCTION-SHAPE-EVIDENCE-V1",
        seal_schema="R4-D-PRODUCTION-SHAPE-EVIDENCE-SEAL-V1",
    )
    capacity = _load_sealed_evidence(
        capacity_evidence,
        schema="R4-D-OPERATOR-CAPACITY-EVIDENCE-V2",
        seal_schema="R4-D-OPERATOR-CAPACITY-EVIDENCE-SEAL-V2",
    )
    prior = _load_sealed_evidence(
        prior_qualification_evidence,
        schema="R4-D-PRIOR-QUALIFICATION-EVIDENCE-V1",
        seal_schema="R4-D-PRIOR-QUALIFICATION-EVIDENCE-SEAL-V1",
    )
    prior_qualification_bytes = authenticate_prior_qualification_evidence(
        prior, candidate_identity=candidate_identity
    )
    if shape["candidate_identity"] != candidate_identity or shape["outcome_columns_accessed"]:
        raise ValueError("production-shape evidence is not candidate-bound and outcome-blind")
    if capacity["candidate_identity"] != candidate_identity:
        raise ValueError("operator-capacity evidence is not candidate-bound")
    _authenticate_production_shapes(shape)
    current_capacity = authenticate_capacity_evidence(capacity, output_root=output_root)
    stages = shape["stages"]
    tensor_schema = shape["tensor_schema"]

    def combined(*names: str) -> dict[str, int]:
        return {
            "rows": sum(stages[name]["rows"] for name in names),
            "timestamps": sum(stages[name]["unique_decision_timestamps"] for name in names),
        }

    dev_2_breakdown = cache_byte_projection(
        tensor_schema=tensor_schema,
        train=combined("DEV_1"),
        prediction=combined("DEV_2"),
    )
    dev_3_breakdown = cache_byte_projection(
        tensor_schema=tensor_schema,
        train=combined("DEV_1", "DEV_2"),
        prediction=combined("DEV_3"),
    )
    terminal_support_policy: dict[str, Any] = {
        "policy": "FROZEN_PUBLIC_TERMINAL_WINDOW_RAW_POPULATION_UPPER_BOUND",
        "source": "docs/R4_P0_EXECUTION_PLAN.md sections 5.4 and 6.3",
        "terminal_start_utc": _TERMINAL_START_UTC,
        "terminal_end_utc": _TERMINAL_END_UTC,
        "cadence_seconds": _TERMINAL_CADENCE_SECONDS,
        "universe_size": _TERMINAL_UNIVERSE_SIZE,
        "maximum_rows": _TERMINAL_RAW_ROW_UPPER_BOUND,
        "maximum_unique_decision_timestamps": _TERMINAL_TIMESTAMP_UPPER_BOUND,
        "timestamp_formula": "((terminal_end_utc - terminal_start_utc) / cadence_seconds) + 1",
        "row_bound_provenance": (
            "authorised raw 15-minute target row population before eligibility filtering"
        ),
        "outcomes_accessed": False,
    }
    terminal_support_policy["policy_identity"] = sha256_bytes(
        canonical_json(terminal_support_policy)
    )
    later_breakdown = cache_byte_projection(
        tensor_schema=tensor_schema,
        train=combined("DEV_1", "DEV_2", "DEV_3"),
        prediction={
            "rows": _TERMINAL_RAW_ROW_UPPER_BOUND,
            "timestamps": _TERMINAL_TIMESTAMP_UPPER_BOUND,
        },
    )
    development = combined("DEV_1", "DEV_2", "DEV_3")
    raw_store_breakdown = raw_store_byte_projection(
        tensor_schema=tensor_schema, timestamps=development["timestamps"]
    )
    prepared_dev_2 = prepared_stage_byte_projection(
        training_rows=stages["DEV_1"]["rows"], prediction_rows=stages["DEV_2"]["rows"]
    )
    prepared_dev_3 = prepared_stage_byte_projection(
        training_rows=stages["DEV_1"]["rows"] + stages["DEV_2"]["rows"],
        prediction_rows=stages["DEV_3"]["rows"],
    )
    source_file_bytes = sum(evidence["size_bytes"] for evidence in shape["source_files"].values())
    source_rows = sum(stage["rows"] for stage in stages.values())
    # Protocol-5 pickle output can exceed columnar source bytes. Bind a conservative
    # expansion and per-row/string allowance rather than treating source size as a bound.
    source_staging_high_water = source_file_bytes * 2 + source_rows * 512 + 1_000_000
    cache_staging_high_water = max(
        dev_2_breakdown["total"], dev_3_breakdown["total"], later_breakdown["total"]
    )
    prepared_staging_high_water = max(prepared_dev_2, prepared_dev_3)
    peak_duplicate_bytes = max(
        raw_store_breakdown["total"], cache_staging_high_water, prepared_staging_high_water
    )
    dev_2_cache = dev_2_breakdown["total"]
    dev_3_cache = dev_3_breakdown["total"]
    later_cache = later_breakdown["total"]
    model_bytes = sum(
        model_parameter_count(build_family_model(family)) * 4 for family in FITTED_FAMILY_IDS
    )
    per_bundle = model_bytes + tensor_schema["nodes"] * 8 + 1_000_000
    qualification_benchmark_policy: dict[str, Any] = {
        "policy": "RETAINED_QUALIFICATION_GENERATIONS_TIMES_CONSERVATIVE_ALLOWANCE",
        "retained_generations": _QUALIFICATION_BENCHMARK_GENERATIONS,
        "per_generation_allowance_bytes": _QUALIFICATION_BENCHMARK_PER_GENERATION_BYTES,
        "formula": "retained_generations * per_generation_allowance_bytes",
        "projected_bytes": (
            _QUALIFICATION_BENCHMARK_GENERATIONS * _QUALIFICATION_BENCHMARK_PER_GENERATION_BYTES
        ),
    }
    qualification_benchmark_policy["policy_identity"] = sha256_bytes(
        canonical_json(qualification_benchmark_policy)
    )
    components = {
        "raw_tensor_store": raw_store_breakdown["total"],
        "prepared_dev_2": prepared_dev_2,
        "prepared_dev_3": prepared_dev_3,
        "dev_2_cache": dev_2_cache,
        "dev_3_cache": dev_3_cache,
        "later_separately_gated_cache_projection": later_cache,
        "worker_source_staging_high_water": source_staging_high_water,
        "peak_create_only_staging_duplicate_bytes": peak_duplicate_bytes,
        "development_bundles_30": per_bundle * _DEV_BUNDLE_COUNT,
        "later_gated_bundles_15": per_bundle * _LATER_BUNDLE_COUNT,
        "prediction_shards": tensor_schema["nodes"] * 8 * (_DEV_BUNDLE_COUNT + _LATER_BUNDLE_COUNT)
        + 1_000_000,
        "journal_session_cache_verification_closure_receipts": 50_000_000,
        "qualification_benchmarks": qualification_benchmark_policy["projected_bytes"],
        "retained_failed_payloads": 5 * per_bundle,
    }
    projected = sum(components.values())
    required_available = projected + _REQUIRED_RESERVE_BYTES
    rejection_reasons: list[dict[str, Any]] = []
    if current_capacity.available_bytes < required_available:
        rejection_reasons.append(
            {
                "code": "CURRENT_AVAILABLE_BYTES_BELOW_PROJECTION_AND_RESERVE",
                "observed_bytes": current_capacity.available_bytes,
                "required_relation": "GREATER_THAN_OR_EQUAL",
                "threshold_bytes": required_available,
            }
        )
    gate = "REJECTED" if rejection_reasons else "ACCEPTED"
    payload: dict[str, Any] = {
        "schema": _PROJECTION_VERSION,
        "classification": "BENCHMARK_NOT_SCIENTIFIC",
        "scientific_performance": "NOT_COMPUTED",
        "candidate_identity": candidate_identity,
        "method": "AUTHENTICATED_PRODUCTION_SHAPES_EXPLICIT_COMPONENT_SUM",
        "method_identity": sha256_bytes(
            canonical_json(
                {
                    "version": _PROJECTION_VERSION,
                    "components": components,
                    "reserve": _REQUIRED_RESERVE_BYTES,
                    "qualification_benchmark_policy_identity": qualification_benchmark_policy[
                        "policy_identity"
                    ],
                    "prior_qualification_evidence_identity": prior["evidence_identity"],
                    "terminal_support_policy_identity": terminal_support_policy["policy_identity"],
                }
            )
        ),
        "inputs": {
            "production_shape_evidence_identity": shape["evidence_identity"],
            "operator_capacity_evidence_identity": capacity["evidence_identity"],
            "prior_qualification_evidence_identity": prior["evidence_identity"],
            "physical_host_available_bytes": current_capacity.available_bytes,
            "sealed_observed_available_bytes": capacity["observed_available_bytes"],
            "canonical_bulk_base": capacity["canonical_bulk_base"],
            "mount_point": current_capacity.mount_point,
            "mount_source": current_capacity.mount_source,
            "filesystem": current_capacity.filesystem,
            "total_bytes": current_capacity.total_bytes,
            "source_root": shape["source_root"],
            "stage_shapes": stages,
            "instruments": tensor_schema["nodes"],
            "development_schedule_cardinality": _DEV_BUNDLE_COUNT,
            "later_schedule_cardinality": _LATER_BUNDLE_COUNT,
            "outcomes_accessed": False,
        },
        "terminal_support_upper_bound_policy": terminal_support_policy,
        "qualification_benchmark_policy": qualification_benchmark_policy,
        "components": components,
        "cache_breakdowns": {
            "dev_2": dev_2_breakdown,
            "dev_3": dev_3_breakdown,
            "later_separately_gated": later_breakdown,
            "raw_tensor_store": raw_store_breakdown,
            "prepared_stages": {"DEV_2": prepared_dev_2, "DEV_3": prepared_dev_3},
        },
        "staging_rationale": (
            "Create-only staging is conservatively projected as an additional retained peak: "
            "raw-store construction plus worker source staging, either prepared package, or one "
            "cache build, whichever is largest. Failed staging remains preserved evidence."
        ),
        "projected_additional_bytes": projected,
        "required_reserve_bytes": _REQUIRED_RESERVE_BYTES,
        "required_available_bytes": required_available,
        "qualification_aggregate_cap_bytes": _QUALIFICATION_AGGREGATE_CAP_BYTES,
        "prior_qualification_evidence_identity": prior["evidence_identity"],
        "prior_qualification_roots": [
            {
                "root": root["root"],
                "present": root["present"],
                "root_identity": root["root_identity"],
                "total_bytes": root["total_bytes"],
            }
            for root in prior["roots"]
        ],
        "prior_qualification_bytes": prior_qualification_bytes,
        "threshold_comparisons": {
            "current_capacity_covers_projection_and_reserve": current_capacity.available_bytes
            >= required_available,
            "qualification_projection_with_prior_within_cap": (
                qualification_benchmark_policy["projected_bytes"] + prior_qualification_bytes
                <= _QUALIFICATION_AGGREGATE_CAP_BYTES
            ),
        },
        "rejection_reasons": rejection_reasons,
        "schema_bindings": {
            "cache_builder_semantic_closure": cache_builder_semantic_closure(),
            "cache_input_identity": sha256_bytes(canonical_json(identity.semantic_inputs())),
            "cache_schema": "R4-P0-STAGE-CACHE-V2",
            "cache_array_formula": (
                "values:f4[ts,lookback,nodes,features];value_mask:b1[same];"
                "availability_mask:b1[same];node_mask:b1[ts,lookback,nodes];"
                "train_rows:i8+i8+b1+f4+f4;predict_rows:i8+i8+b1"
            ),
            "artifact_schema": "R4-P0-CREATE-ONLY-ARTIFACTS",
            "artifact_schema_closure": sha256_bytes(
                inspect.getsource(CreateOnlyArtifacts).encode()
            ),
            "runtime_environment_identity": sha256_bytes(canonical_json(environment_identity())),
        },
        "gate": gate,
    }
    payload["receipt_identity"] = sha256_bytes(canonical_json(payload))
    receipt = output_root / "disk-projection.json"
    create_json_once(receipt, payload)
    create_json_once(
        output_root / "disk-projection-seal.json",
        {
            "schema": "R4-D-ADDITIONAL-DISK-PROJECTION-SEAL-V2",
            "receipt": receipt.name,
            "receipt_sha256": sha256_bytes(receipt.read_bytes()),
            "receipt_identity": payload["receipt_identity"],
        },
    )
    descriptor = os.open(output_root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return receipt
