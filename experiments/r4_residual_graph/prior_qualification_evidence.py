"""Authenticated immutable inventory of retained qualification generations."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import Any

from .attempt_artifacts import canonical_json, create_json_once, sha256_bytes

_SCHEMA = "R4-D-PRIOR-QUALIFICATION-EVIDENCE-V1"
_SEAL_SCHEMA = "R4-D-PRIOR-QUALIFICATION-EVIDENCE-SEAL-V1"
_PRIOR_ROOTS: tuple[tuple[str, Path, bool], ...] = (
    ("generation-1-benchmark", Path("/workspace/tmp/qtrad-r4/r4-p0-remediation7-benchmark"), True),
    (
        "generation-1-cache",
        Path("/workspace/tmp/qtrad-r4/r4-p0-remediation7-benchmark-cache"),
        True,
    ),
    (
        "generation-2-benchmark",
        Path("/workspace/tmp/qtrad-r4/r4-p0-remediation7-benchmark-2"),
        True,
    ),
    (
        "generation-2-cache",
        Path("/workspace/tmp/qtrad-r4/r4-p0-remediation7-benchmark-2-cache"),
        True,
    ),
    (
        "generation-3-benchmark",
        Path("/workspace/tmp/qtrad-r4/r4-p0-remediation7-benchmark-3"),
        True,
    ),
    (
        "generation-3-cache",
        Path("/workspace/tmp/qtrad-r4/r4-p0-remediation7-benchmark-3-cache"),
        True,
    ),
    (
        "generation-4-benchmark",
        Path("/workspace/tmp/qtrad-r4/r4-p0-remediation7-benchmark-4"),
        True,
    ),
    (
        "generation-4-cache",
        Path("/workspace/tmp/qtrad-r4/r4-p0-remediation7-benchmark-4-cache"),
        False,
    ),
    ("generation-5-benchmark", Path("/data/q-trad/r4-p0/remediation-7/benchmark-5"), True),
    ("generation-5-cache", Path("/data/q-trad/r4-p0/remediation-7/benchmark-5-cache"), True),
    ("generation-6-benchmark", Path("/data/q-trad/r4-p0/remediation-7/benchmark-6"), True),
    ("generation-6-cache", Path("/data/q-trad/r4-p0/remediation-7/benchmark-6-cache"), True),
    ("generation-7-benchmark", Path("/data/q-trad/r4-p0/remediation-7/benchmark-7"), True),
    ("generation-7-cache", Path("/data/q-trad/r4-p0/remediation-7/benchmark-7-cache"), False),
    ("generation-8-benchmark", Path("/data/q-trad/r4-p0/remediation-7/benchmark-8"), True),
    ("generation-8-cache", Path("/data/q-trad/r4-p0/remediation-7/benchmark-8-cache"), False),
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _inventory_root(label: str, root: Path, expected_present: bool) -> dict[str, Any]:
    if root.is_symlink():
        raise ValueError(f"prior qualification root is a symlink: {root}")
    present = root.exists()
    if present != expected_present:
        raise ValueError(f"prior qualification root presence drift: {root}")
    files: list[dict[str, Any]] = []
    if present:
        if not root.is_dir():
            raise ValueError(f"prior qualification root is not a directory: {root}")
        for directory, directory_names, file_names in os.walk(root, followlinks=False):
            directory_path = Path(directory)
            for name in directory_names:
                child = directory_path / name
                if child.is_symlink() or not child.is_dir():
                    raise ValueError(f"prior qualification contains invalid directory: {child}")
            for name in file_names:
                path = directory_path / name
                before = path.lstat()
                if not stat.S_ISREG(before.st_mode):
                    raise ValueError(f"prior qualification contains non-regular file: {path}")
                digest = _sha256_file(path)
                after = path.lstat()
                if (
                    before.st_mode != after.st_mode
                    or before.st_size != after.st_size
                    or before.st_mtime_ns != after.st_mtime_ns
                    or before.st_ino != after.st_ino
                ):
                    raise ValueError(f"prior qualification file drifted during inventory: {path}")
                files.append(
                    {
                        "path": path.relative_to(root).as_posix(),
                        "mode": stat.S_IMODE(after.st_mode),
                        "size_bytes": after.st_size,
                        "sha256": digest,
                    }
                )
    files.sort(key=lambda item: item["path"])
    root_payload: dict[str, Any] = {
        "label": label,
        "root": str(root),
        "expected_present": expected_present,
        "present": present,
        "files": files,
        "total_bytes": sum(item["size_bytes"] for item in files),
    }
    root_payload["root_identity"] = sha256_bytes(canonical_json(root_payload))
    return root_payload


def _inventory() -> list[dict[str, Any]]:
    return [_inventory_root(label, path, present) for label, path, present in _PRIOR_ROOTS]


def create_prior_qualification_evidence(output_root: Path, *, candidate_identity: str) -> Path:
    roots = _inventory()
    payload: dict[str, Any] = {
        "schema": _SCHEMA,
        "evidence_kind": "AUTHENTICATED_IMMUTABLE_PRIOR_QUALIFICATION_INVENTORY",
        "candidate_identity": candidate_identity,
        "roots": roots,
        "prior_qualification_bytes": sum(root["total_bytes"] for root in roots),
    }
    payload["evidence_identity"] = sha256_bytes(canonical_json(payload))
    receipt = output_root / "prior-qualification-evidence.json"
    create_json_once(receipt, payload)
    create_json_once(
        output_root / "prior-qualification-evidence-seal.json",
        {
            "schema": _SEAL_SCHEMA,
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


def authenticate_prior_qualification_evidence(
    payload: dict[str, Any], *, candidate_identity: str
) -> int:
    if set(payload) != {
        "schema",
        "evidence_kind",
        "candidate_identity",
        "roots",
        "prior_qualification_bytes",
        "evidence_identity",
    }:
        raise ValueError("prior qualification evidence keys are invalid")
    if (
        payload["schema"] != _SCHEMA
        or payload["evidence_kind"] != "AUTHENTICATED_IMMUTABLE_PRIOR_QUALIFICATION_INVENTORY"
        or payload["candidate_identity"] != candidate_identity
    ):
        raise ValueError("prior qualification evidence contract is invalid")
    identity_payload = dict(payload)
    evidence_identity = identity_payload.pop("evidence_identity")
    if evidence_identity != sha256_bytes(canonical_json(identity_payload)):
        raise ValueError("prior qualification evidence identity is invalid")
    current_roots = _inventory()
    if payload["roots"] != current_roots:
        raise ValueError("prior qualification inventory drift")
    derived_bytes = sum(root["total_bytes"] for root in current_roots)
    if payload["prior_qualification_bytes"] != derived_bytes:
        raise ValueError("prior qualification byte total is invalid")
    return derived_bytes
