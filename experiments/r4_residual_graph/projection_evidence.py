"""Authenticated outcome-blind inputs for the R4 additional-disk gate."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

import polars as pl

from .attempt_artifacts import canonical_json, create_json_once, sha256_bytes

_BULK_BASE = Path("/data/q-trad/r4-p0")
_MOUNT_POINT = Path("/data")
_MOUNT_SOURCE = "/dev/sde[/q-trad-bulkdata]"
_FILESYSTEM = "ext4"
_OBSERVED_TOTAL_BYTES = 1_078_442_151_936
_OBSERVED_AVAILABLE_BYTES = 1_023_584_808_960
_OBSERVED_AT_UTC = "2026-09-02T14:47:54.373Z"
_CAPACITY_EVIDENCE_MAX_AGE_SECONDS = 300
_RESERVE_BYTES = 100_000_000_000
_DEVELOPMENT_STAGES = ("DEV_1", "DEV_2", "DEV_3")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _seal(output_root: Path, name: str, payload: dict[str, Any], seal_schema: str) -> Path:
    payload["evidence_identity"] = sha256_bytes(canonical_json(payload))
    receipt = output_root / name
    create_json_once(receipt, payload)
    create_json_once(
        output_root / f"{receipt.stem}-seal.json",
        {
            "schema": seal_schema,
            "evidence": receipt.name,
            "evidence_sha256": sha256_bytes(receipt.read_bytes()),
            "evidence_identity": payload["evidence_identity"],
        },
    )
    descriptor = os.open(output_root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return receipt


class CapacityObservation(NamedTuple):
    total_bytes: int
    available_bytes: int
    mount_point: str
    mount_source: str
    filesystem: str
    device: int


def _mount_contract(path: Path) -> tuple[str, str, str]:
    matches: list[tuple[int, str, str, str]] = []
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        fields = line.split()
        separator = fields.index("-")
        mount_point = fields[4].replace(r"\040", " ")
        root = fields[3].replace(r"\040", " ")
        candidate = Path(mount_point)
        if path == candidate or candidate in path.parents:
            source = fields[separator + 2].replace(r"\040", " ")
            represented_source = f"{source}[{root}]" if root != "/" else source
            matches.append(
                (len(candidate.parts), mount_point, represented_source, fields[separator + 1])
            )
    if not matches:
        raise ValueError("bulk base has no authoritative mountinfo entry")
    _, mount_point, source, filesystem = max(matches)
    return mount_point, source, filesystem


def observe_bulk_capacity(bulk_base: Path | None = None) -> CapacityObservation:
    bulk_base = _BULK_BASE if bulk_base is None else bulk_base
    if not bulk_base.exists() or bulk_base.is_symlink() or not bulk_base.is_dir():
        raise ValueError("canonical bulk base is absent, a symlink, or not a directory")
    resolved = bulk_base.resolve(strict=True)
    if resolved != bulk_base:
        raise ValueError("canonical bulk base path is not canonical")
    mount_point, source, filesystem = _mount_contract(resolved)
    stats = os.statvfs(resolved)
    observation = CapacityObservation(
        stats.f_blocks * stats.f_frsize,
        stats.f_bavail * stats.f_frsize,
        mount_point,
        source,
        filesystem,
        resolved.stat().st_dev,
    )
    if (
        observation.mount_point != str(_MOUNT_POINT)
        or observation.mount_source != _MOUNT_SOURCE
        or observation.filesystem != _FILESYSTEM
        or observation.total_bytes != _OBSERVED_TOTAL_BYTES
    ):
        raise ValueError("canonical bulk mount/device/statvfs contract mismatch")
    try:
        descriptor, probe = tempfile.mkstemp(prefix=".r4-capacity-", dir=resolved)
        os.close(descriptor)
        Path(probe).unlink()
    except OSError as error:
        raise ValueError("canonical bulk base is not writable") from error
    return observation


def create_operator_capacity_evidence(output_root: Path, *, candidate_identity: str) -> Path:
    """Seal the operator-authorised bulk mount and a fresh executable observation."""
    observation = observe_bulk_capacity()
    if observation.available_bytes > _OBSERVED_AVAILABLE_BYTES:
        raise ValueError("current available bytes exceed operator-authorised observation")
    output_device = output_root.resolve(strict=True).stat().st_dev
    cache_parent = output_root.with_name(f"{output_root.name}-cache").parent.resolve(strict=True)
    if output_device != observation.device or cache_parent.stat().st_dev != observation.device:
        raise ValueError(
            "qualification staging and final paths are not on the canonical filesystem"
        )
    measured_at = datetime.now(UTC)
    payload: dict[str, Any] = {
        "schema": "R4-D-OPERATOR-CAPACITY-EVIDENCE-V2",
        "evidence_kind": "OPERATOR_AUTHORISED_EXECUTABLE_BULK_CAPACITY",
        "canonical_bulk_base": str(_BULK_BASE),
        "mount_point": str(_MOUNT_POINT),
        "mount_source": _MOUNT_SOURCE,
        "filesystem": _FILESYSTEM,
        "device": observation.device,
        "total_bytes": observation.total_bytes,
        "observed_available_bytes": _OBSERVED_AVAILABLE_BYTES,
        "observed_at_utc": _OBSERVED_AT_UTC,
        "current_available_bytes": observation.available_bytes,
        "measured_at_utc": measured_at.isoformat(),
        "freshness_max_age_seconds": _CAPACITY_EVIDENCE_MAX_AGE_SECONDS,
        "available_drift_policy": "CURRENT_MAY_DECLINE_BUT_MUST_NOT_EXCEED_SEALED_OBSERVATION",
        "reserve_bytes": _RESERVE_BYTES,
        "staging_root": str(output_root.resolve(strict=True)),
        "final_root": str(output_root.resolve(strict=True)),
        "cache_final_root": str(output_root.with_name(f"{output_root.name}-cache")),
        "same_filesystem": True,
        "container_or_workspace_df_authoritative": False,
        "authority_ref": "R4.D_REMEDIATION_7_QUALIFICATION operator capacity authority",
        "candidate_identity": candidate_identity,
    }
    return _seal(
        output_root,
        "operator-capacity-evidence.json",
        payload,
        "R4-D-OPERATOR-CAPACITY-EVIDENCE-SEAL-V2",
    )


def authenticate_capacity_evidence(
    payload: dict[str, Any], *, output_root: Path
) -> CapacityObservation:
    expected = {
        "schema": "R4-D-OPERATOR-CAPACITY-EVIDENCE-V2",
        "evidence_kind": "OPERATOR_AUTHORISED_EXECUTABLE_BULK_CAPACITY",
        "canonical_bulk_base": str(_BULK_BASE),
        "mount_point": str(_MOUNT_POINT),
        "mount_source": _MOUNT_SOURCE,
        "filesystem": _FILESYSTEM,
        "total_bytes": _OBSERVED_TOTAL_BYTES,
        "observed_available_bytes": _OBSERVED_AVAILABLE_BYTES,
        "observed_at_utc": _OBSERVED_AT_UTC,
        "freshness_max_age_seconds": _CAPACITY_EVIDENCE_MAX_AGE_SECONDS,
        "available_drift_policy": "CURRENT_MAY_DECLINE_BUT_MUST_NOT_EXCEED_SEALED_OBSERVATION",
        "reserve_bytes": _RESERVE_BYTES,
        "staging_root": str(output_root.resolve(strict=True)),
        "final_root": str(output_root.resolve(strict=True)),
        "cache_final_root": str(output_root.with_name(f"{output_root.name}-cache")),
        "same_filesystem": True,
        "container_or_workspace_df_authoritative": False,
        "authority_ref": "R4.D_REMEDIATION_7_QUALIFICATION operator capacity authority",
    }
    if any(payload[key] != value for key, value in expected.items()):
        raise ValueError("operator-capacity evidence contract mismatch")
    measured_at = datetime.fromisoformat(payload["measured_at_utc"])
    if measured_at.tzinfo is None:
        raise ValueError("operator-capacity evidence timestamp is not timezone-aware")
    age = (datetime.now(UTC) - measured_at).total_seconds()
    if age < 0 or age > _CAPACITY_EVIDENCE_MAX_AGE_SECONDS:
        raise ValueError("operator-capacity evidence is stale")
    observation = observe_bulk_capacity()
    if (
        observation.device != payload["device"]
        or payload["current_available_bytes"] > payload["observed_available_bytes"]
        or observation.available_bytes > payload["current_available_bytes"]
        or output_root.resolve(strict=True).stat().st_dev != observation.device
        or output_root.parent.resolve(strict=True).stat().st_dev != observation.device
    ):
        raise ValueError("operator-capacity evidence drift or filesystem mismatch")
    return observation


def create_production_shape_evidence(
    output_root: Path, *, source_root: Path, candidate_identity: str
) -> Path:
    """Read only authenticated development block and decision-time columns."""
    source_root = source_root.resolve()
    execution_input_path = source_root / "config/execution-input.json"
    foundation_path = source_root / "input/residual-foundation.parquet"
    foundation_sidecar_path = source_root / "input/residual-foundation.parquet.json"
    development_support_path = source_root / "input/development-support.parquet"
    training_partition_path = source_root / "input/training-partition.json"
    paths = (
        execution_input_path,
        foundation_path,
        foundation_sidecar_path,
        development_support_path,
        training_partition_path,
    )
    if not all(path.is_file() and not path.is_symlink() for path in paths):
        raise ValueError("production-shape source is incomplete or contains a symlink")
    execution_input = json.loads(execution_input_path.read_text())
    if execution_input["artifact_type"] != "R4.C_AUTHENTICATED_EXECUTION_INPUT":
        raise ValueError("production-shape execution input has the wrong contract")
    if (
        Path(execution_input["foundation_path"]).resolve() != foundation_path
        or Path(execution_input["development_support_path"]).resolve() != development_support_path
    ):
        raise ValueError("production-shape execution input paths do not match the source root")
    manifest_path = Path(execution_input["manifest_path"])
    if (
        not manifest_path.is_file()
        or manifest_path.is_symlink()
        or _sha256_file(manifest_path) != execution_input["manifest_sha256"]
    ):
        raise ValueError("production-shape authoritative manifest hash mismatch")
    authenticated_hashes = {
        foundation_path: execution_input["foundation_file_sha256"],
        development_support_path: execution_input["development_support_file_sha256"],
        training_partition_path: execution_input["partition_file_sha256"],
    }
    for path, expected in authenticated_hashes.items():
        if _sha256_file(path) != expected:
            raise ValueError(f"production-shape authenticated hash mismatch: {path.name}")
    sidecar = json.loads(foundation_sidecar_path.read_text())
    counts = (
        pl.scan_parquet(foundation_path)
        .select("block", "decision_time")
        .group_by("block")
        .agg(
            pl.len().alias("rows"),
            pl.col("decision_time").n_unique().alias("unique_decision_timestamps"),
        )
        .collect()
    )
    stage_shapes = {
        str(row["block"]): {
            "rows": int(row["rows"]),
            "unique_decision_timestamps": int(row["unique_decision_timestamps"]),
        }
        for row in counts.iter_rows(named=True)
    }
    if tuple(sorted(stage_shapes)) != _DEVELOPMENT_STAGES:
        raise ValueError("production-shape source does not contain exactly the development stages")
    if {stage: stage_shapes[stage]["rows"] for stage in _DEVELOPMENT_STAGES} != sidecar[
        "stage_counts"
    ]:
        raise ValueError("production-shape counts disagree with the authenticated sidecar")
    development_counts = (
        pl.scan_parquet(development_support_path)
        .select("block", "decision_time")
        .group_by("block")
        .agg(pl.len().alias("rows"), pl.col("decision_time").n_unique().alias("timestamps"))
        .collect()
    )
    observed_development = {
        str(row["block"]): (int(row["rows"]), int(row["timestamps"]))
        for row in development_counts.iter_rows(named=True)
    }
    for stage in ("DEV_2", "DEV_3"):
        expected = (
            stage_shapes[stage]["rows"],
            stage_shapes[stage]["unique_decision_timestamps"],
        )
        if observed_development[stage] != expected:
            raise ValueError("development support disagrees with production-shape evidence")
    payload: dict[str, Any] = {
        "schema": "R4-D-PRODUCTION-SHAPE-EVIDENCE-V1",
        "evidence_kind": "AUTHENTICATED_OUTCOME_BLIND_PRODUCTION_DEVELOPMENT_SHAPES",
        "candidate_identity": candidate_identity,
        "source_root": str(source_root),
        "source_producer_head": json.loads((source_root / "config/g0-identity.json").read_text())[
            "code_head"
        ],
        "source_manifest_identity": execution_input["manifest_identity"],
        "authoritative_manifest": {
            "path": str(manifest_path),
            "sha256": execution_input["manifest_sha256"],
            "size_bytes": manifest_path.stat().st_size,
        },
        "source_config_identity": execution_input["g0_identity"],
        "source_files": {
            str(path.relative_to(source_root)): {
                "sha256": _sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
            for path in paths
        },
        "columns_accessed": ["block", "decision_time"],
        "outcome_columns_accessed": False,
        "stages": stage_shapes,
        "tensor_schema": {
            "values_dtype": "float32",
            "mask_dtype": "bool",
            "nodes": 20,
            "lookback_steps": 361,
            "features": 36,
        },
    }
    return _seal(
        output_root,
        "production-shape-evidence.json",
        payload,
        "R4-D-PRODUCTION-SHAPE-EVIDENCE-SEAL-V1",
    )
