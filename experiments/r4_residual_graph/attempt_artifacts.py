"""Create-only journal and sealed atomic attempt bundles for R4-P0."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypedDict, cast

JournalStatus = Literal["STARTED", "SUCCEEDED", "FAILED", "INVALIDATED"]


class AttemptPayload(TypedDict):
    release_identity: str
    g0_identity: str
    output_root: str
    slot_id: str
    family_id: str
    seed: int
    stage: str
    attempt: int
    mode: str


class JournalRecord(TypedDict):
    schema: str
    attempt_id: str
    attempt: AttemptPayload
    status: JournalStatus
    payload: dict[str, object]
    record_identity: str


_SCHEMA = "R4-P0-SEALED-ATTEMPT-V1"


def strict_json_object(value: bytes | str) -> dict[str, Any]:
    """Decode evidence without discarding duplicate fields at any depth."""

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError(f"duplicate evidence key: {key}")
            result[key] = item
        return result

    result = json.loads(value, object_pairs_hook=unique)
    if not isinstance(result, dict):
        raise ValueError("evidence must be a JSON object")
    return result


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _normalise_relative(path: str) -> str:
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts or path in {"", "."}:
        raise ValueError("artifact path must be a non-empty canonical relative path")
    normalised = candidate.as_posix()
    if normalised != path or any(part in {"", "."} for part in candidate.parts):
        raise ValueError("artifact path is not canonical")
    return normalised


def _create_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
    _fsync_directory(path.parent)


def create_json_once(path: Path, payload: Mapping[str, object]) -> None:
    encoded = canonical_json(payload) + b"\n"
    try:
        _create_bytes(path, encoded)
    except FileExistsError:
        if path.is_symlink() or path.read_bytes() != encoded:
            raise FileExistsError(f"conflicting create-only artifact: {path}") from None


def _regular_file(path: Path) -> os.stat_result:
    if path.is_symlink():
        raise ValueError(f"symlink is forbidden: {path}")
    stat = path.stat(follow_symlinks=False)
    if not path.is_file():
        raise ValueError(f"artifact is not a regular file: {path}")
    return stat


@dataclass(frozen=True)
class FileReceipt:
    path: str
    size: int
    sha256: str

    def to_dict(self) -> dict[str, object]:
        return {"path": self.path, "size": self.size, "sha256": self.sha256}


@dataclass(frozen=True)
class AttemptIdentity:
    release_identity: str
    g0_identity: str
    output_root: str
    slot_id: str
    family_id: str
    seed: int
    stage: str
    attempt: int
    mode: str

    def __post_init__(self) -> None:
        if self.slot_id != f"{self.family_id}:{self.seed}:{self.stage}":
            raise ValueError("attempt slot identity is not canonical")
        if self.attempt not in {0, 1}:
            raise ValueError("attempt number must be zero or one")
        if self.mode not in {"PRIMARY", "SMOKE"}:
            raise ValueError("attempt mode is invalid")
        root = Path(self.output_root)
        if not root.is_absolute() or root != root.resolve():
            raise ValueError("attempt output root must be canonical and absolute")

    @property
    def identity(self) -> str:
        return sha256_bytes(canonical_json(self.to_dict()))

    def to_dict(self) -> AttemptPayload:
        return {
            "release_identity": self.release_identity,
            "g0_identity": self.g0_identity,
            "output_root": self.output_root,
            "slot_id": self.slot_id,
            "family_id": self.family_id,
            "seed": self.seed,
            "stage": self.stage,
            "attempt": self.attempt,
            "mode": self.mode,
        }


class CreateOnlyAttemptJournal:
    """Directory journal whose record names and bytes are immutable."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.path = self.root / "register" / "attempts"

    def _record_path(self, attempt_id: str, status: JournalStatus) -> Path:
        if len(attempt_id) != 64 or any(char not in "0123456789abcdef" for char in attempt_id):
            raise ValueError("attempt identity must be a lowercase SHA-256 digest")
        return self.path / f"{attempt_id}.{status}.json"

    def records(self, attempt_id: str) -> dict[JournalStatus, dict[str, object]]:
        result: dict[JournalStatus, dict[str, object]] = {}
        for status in ("STARTED", "SUCCEEDED", "FAILED", "INVALIDATED"):
            path = self._record_path(attempt_id, status)
            if not path.exists() and not path.is_symlink():
                continue
            payload = self._read_record(path)
            result[status] = payload
        if "SUCCEEDED" in result and "FAILED" in result:
            raise ValueError("attempt has conflicting terminal journal records")
        if ("SUCCEEDED" in result or "FAILED" in result) and "STARTED" not in result:
            raise ValueError("terminal journal record has no STARTED record")
        return result

    def _read_record(self, path: Path) -> dict[str, Any]:
        _regular_file(path)
        payload = strict_json_object(path.read_bytes())
        if set(payload) != {
            "schema",
            "attempt_id",
            "attempt",
            "status",
            "payload",
            "record_identity",
        }:
            raise ValueError("journal record schema is not canonical")
        unsigned = dict(payload)
        identity = unsigned.pop("record_identity")
        if payload["schema"] != _SCHEMA or sha256_bytes(canonical_json(unsigned)) != identity:
            raise ValueError("journal record schema or hash mismatch")
        if payload["status"] not in {"STARTED", "SUCCEEDED", "FAILED"}:
            raise ValueError("journal record status is invalid")
        if not isinstance(payload["attempt"], dict) or not isinstance(payload["payload"], dict):
            raise ValueError("journal attempt and payload must be objects")
        attempt = AttemptIdentity(**payload["attempt"])
        if (
            attempt.to_dict() != payload["attempt"]
            or attempt.identity != payload["attempt_id"]
            or attempt.output_root != str(self.root)
            or path != self._record_path(attempt.identity, payload["status"])
        ):
            raise ValueError("journal filename, status, attempt or root binding mismatch")
        return payload

    def append(
        self,
        attempt: AttemptIdentity,
        status: JournalStatus,
        payload: Mapping[str, object],
    ) -> dict[str, object]:
        if status == "INVALIDATED":
            raise ValueError("attempt journal cannot append release invalidation")
        attempt_id = attempt.identity
        existing = self.records(attempt_id)
        if status in {"SUCCEEDED", "FAILED"}:
            if "STARTED" not in existing:
                raise ValueError("terminal journal record requires STARTED")
            opposite = "FAILED" if status == "SUCCEEDED" else "SUCCEEDED"
            if opposite in existing:
                raise ValueError("attempt already has the opposite terminal status")
        complete: dict[str, object] = {
            "schema": _SCHEMA,
            "attempt_id": attempt_id,
            "attempt": attempt.to_dict(),
            "status": status,
            "payload": dict(payload),
        }
        complete["record_identity"] = sha256_bytes(canonical_json(complete))
        create_json_once(self._record_path(attempt_id, status), complete)
        loaded = self.records(attempt_id)[status]
        if loaded != complete:
            raise ValueError("journal record read-back differs")
        return complete

    def ordered_records(
        self, schedule: Sequence[tuple[str, int, str]]
    ) -> tuple[JournalRecord, ...]:
        if not self.path.exists():
            return ()
        lifecycle = {"STARTED": 0, "SUCCEEDED": 1, "FAILED": 1}
        schedule_order = {
            f"{family}:{seed}:{stage}": index
            for index, (family, seed, stage) in enumerate(schedule)
        }
        records: list[JournalRecord] = []
        for path in self.path.iterdir():
            if path.is_symlink() or not path.is_file():
                raise ValueError("journal contains a non-regular entry")
            payload = self._read_record(path)
            records.append(cast(JournalRecord, payload))
        for attempt_id in {record["attempt_id"] for record in records}:
            self.records(attempt_id)
        return tuple(
            sorted(
                records,
                key=lambda item: (
                    schedule_order[item["attempt"]["slot_id"]],
                    item["attempt"]["attempt"],
                    lifecycle[str(item["status"])],
                ),
            )
        )


def _scan_payload(root: Path, *, exclude: frozenset[str] = frozenset()) -> tuple[FileReceipt, ...]:
    receipts: list[FileReceipt] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"symlink is forbidden in sealed payload: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative in exclude:
            continue
        stat = _regular_file(path)
        receipts.append(FileReceipt(relative, stat.st_size, sha256_bytes(path.read_bytes())))
    return tuple(receipts)


def _fsync_tree(root: Path) -> None:
    for path in sorted(root.rglob("*")):
        if path.is_file():
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    for path in sorted((item for item in root.rglob("*") if item.is_dir()), reverse=True):
        _fsync_directory(path)
    _fsync_directory(root)


def seal_attempt_staging(
    staging: Path,
    attempt: AttemptIdentity,
    *,
    required_paths: Sequence[str],
) -> dict[str, object]:
    staging = staging.resolve()
    if staging.is_symlink() or not staging.is_dir():
        raise ValueError("attempt staging must be a real directory")
    if (staging / "seal.json").exists():
        raise FileExistsError("attempt staging is already sealed")
    canonical_required = tuple(_normalise_relative(path) for path in required_paths)
    if len(set(canonical_required)) != len(canonical_required):
        raise ValueError("required attempt paths are duplicated")
    receipts = _scan_payload(staging)
    present = {receipt.path for receipt in receipts}
    if present != set(canonical_required):
        raise ValueError("attempt staging contents differ from required payload")
    manifest: dict[str, object] = {
        "schema": _SCHEMA,
        "attempt_id": attempt.identity,
        "attempt": attempt.to_dict(),
        "files": [receipt.to_dict() for receipt in receipts],
    }
    manifest["manifest_identity"] = sha256_bytes(canonical_json(manifest))
    create_json_once(staging / "manifest.json", manifest)
    manifest_receipt = FileReceipt(
        "manifest.json",
        (staging / "manifest.json").stat().st_size,
        sha256_bytes((staging / "manifest.json").read_bytes()),
    )
    seal: dict[str, object] = {
        "schema": _SCHEMA,
        "attempt_id": attempt.identity,
        "manifest_identity": manifest["manifest_identity"],
        "manifest": manifest_receipt.to_dict(),
    }
    seal["seal_identity"] = sha256_bytes(canonical_json(seal))
    create_json_once(staging / "seal.json", seal)
    _fsync_tree(staging)
    return seal


def verify_attempt_bundle(
    bundle: Path, expected: AttemptIdentity | None = None
) -> dict[str, object]:
    bundle = bundle.resolve()
    if bundle.is_symlink() or not bundle.is_dir():
        raise ValueError("attempt bundle must be a real directory")
    manifest_path = bundle / "manifest.json"
    seal_path = bundle / "seal.json"
    _regular_file(manifest_path)
    _regular_file(seal_path)
    manifest = strict_json_object(manifest_path.read_bytes())
    seal = strict_json_object(seal_path.read_bytes())
    if not isinstance(manifest, dict) or not isinstance(seal, dict):
        raise ValueError("attempt manifest and seal must be objects")
    expected_manifest = dict(manifest)
    identity = expected_manifest.pop("manifest_identity")
    if sha256_bytes(canonical_json(expected_manifest)) != identity:
        raise ValueError("attempt manifest identity mismatch")
    expected_seal = dict(seal)
    seal_identity = expected_seal.pop("seal_identity")
    if sha256_bytes(canonical_json(expected_seal)) != seal_identity:
        raise ValueError("attempt seal identity mismatch")
    if seal["manifest_identity"] != identity:
        raise ValueError("attempt seal does not bind manifest")
    manifest_receipt = dict(seal["manifest"])
    if (
        manifest_receipt["path"] != "manifest.json"
        or manifest_receipt["size"] != manifest_path.stat().st_size
        or manifest_receipt["sha256"] != sha256_bytes(manifest_path.read_bytes())
    ):
        raise ValueError("attempt seal manifest receipt mismatch")
    receipts = tuple(FileReceipt(**dict(item)) for item in list(manifest["files"]))
    actual = _scan_payload(bundle, exclude=frozenset({"manifest.json", "seal.json"}))
    if receipts != actual:
        raise ValueError("attempt bundle payload digest mismatch")
    if expected is not None and (
        manifest["attempt_id"] != expected.identity or manifest["attempt"] != expected.to_dict()
    ):
        raise ValueError("attempt bundle identity differs from expected attempt")
    return {"manifest": manifest, "seal": seal}


def publish_attempt_bundle(staging: Path, destination: Path, attempt: AttemptIdentity) -> None:
    verify_attempt_bundle(staging, attempt)
    staging_parent = staging.parent.resolve()
    destination_parent = destination.parent.resolve()
    destination_parent.mkdir(parents=True, exist_ok=True)
    if staging.stat().st_dev != destination_parent.stat().st_dev:
        raise OSError("attempt staging and destination are on different filesystems")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"attempt bundle destination exists: {destination}")
    os.rename(staging, destination)
    _fsync_directory(staging_parent)
    _fsync_directory(destination_parent)
    verify_attempt_bundle(destination, attempt)


def preserve_failed_staging(
    staging: Path, destination: Path, closure: Mapping[str, object]
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if staging.stat().st_dev != destination.parent.stat().st_dev:
        raise OSError("failure staging and destination are on different filesystems")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"failure destination exists: {destination}")
    os.rename(staging, destination)
    _fsync_directory(staging.parent)
    _fsync_directory(destination.parent)
    create_json_once(destination / "closure.json", closure)


def copy_payload_file(source: Path, destination: Path) -> None:
    _regular_file(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(destination, flags, 0o600)
    try:
        with source.open("rb") as reader, os.fdopen(descriptor, "wb", closefd=False) as writer:
            shutil.copyfileobj(reader, writer)
            writer.flush()
            os.fsync(writer.fileno())
    finally:
        os.close(descriptor)
