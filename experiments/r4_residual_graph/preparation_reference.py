"""Metadata handoff from the accepted preparation to a descendant execution.

The accepted receipt is authority; file states are physical closure, input/cache
identities are semantics, and the two G0 identities are execution provenance.
This boundary neither grants execution authority nor replays preparation.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Any, Concatenate

from .attempt_artifacts import canonical_json, create_json_once, sha256_bytes, strict_json_object

SCHEMA = "R4-P0-PREPARATION-REFERENCE-V1"
ACCEPTANCE_EVENT = "/workspace/tmp/MAP_orchestrator/r4-p0-20260828-ddd66f9/events.jsonl:319"
PREPARATION_ROOT = Path("/data/q-trad/r4-p0/r4-p0-pg-8-prepare-679ae0a")
ACCEPTED_CANDIDATE = "679ae0a676121d0cfc4420c12e86b6fbb53c2d92"
ACCEPTED_TREE = "13b49de33ea25b74b5bf4c638b78367cecee4696"
ACCEPTED_G0 = "174707b5b1b24b489a70ade0ac6233607b8523e260b98c30b8f7d743fead3d32"
ACCEPTED_RECEIPT = Path(
    "/workspace/tmp/MAP_orchestrator/r4-p0-20260828-ddd66f9/longrun/"
    "r4-preparation10-control/679ae0a676121d0cfc4420c12e86b6fbb53c2d92-pg8-10/"
    "output-authentication.json"
)
ACCEPTED_RECEIPT_SHA256 = "ee1ccea910aae6683c5e6fc7efd1e6e0dc46273c57b9020102e0754a4122e125"
RETAINED_FILES = 85


def reference_path(root: Path) -> Path:
    return root / "config" / "preparation-reference.json"


def _regular_state(path: Path) -> list[int]:
    value = path.lstat()
    if not stat.S_ISREG(value.st_mode) or path.resolve(strict=True) != path.absolute():
        raise ValueError(f"reference input must be a canonical regular file: {path}")
    return [
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    ]


def _metadata(path: Path) -> dict[str, Any]:
    _regular_state(path)
    return strict_json_object(path.read_bytes())


def _self_hashed(payload: Mapping[str, Any], key: str) -> None:
    unsigned = dict(payload)
    identity = unsigned.pop(key)
    if sha256_bytes(canonical_json(unsigned)) != identity:
        raise ValueError(f"preparation metadata {key} mismatch")


def _accepted_receipt() -> dict[str, Any]:
    _regular_state(ACCEPTED_RECEIPT)
    encoded = ACCEPTED_RECEIPT.read_bytes()
    if sha256_bytes(encoded) != ACCEPTED_RECEIPT_SHA256:
        raise ValueError("accepted preparation receipt digest mismatch")
    receipt = strict_json_object(encoded)
    if (
        receipt["status"] != "PASS"
        or receipt["candidate"][:2] != [ACCEPTED_CANDIDATE, ACCEPTED_TREE]
        or receipt["g0_identity"] != ACCEPTED_G0
        or len(receipt["files"]) != RETAINED_FILES
        or set(receipt["stages"]) != {"DEV_2", "DEV_3"}
    ):
        raise ValueError("accepted preparation receipt authority mismatch")
    return receipt


def _check_inventory(files: Mapping[str, Any], *, enumerate_tree: bool) -> None:
    root = PREPARATION_ROOT
    if root.resolve(strict=True) != root or not root.is_dir():
        raise ValueError("preparation root must be canonical and contain no symlinks")
    for relative, expected in files.items():
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts or path.as_posix() != relative:
            raise ValueError("preparation inventory path is not canonical")
        if (
            not isinstance(expected, list)
            or len(expected) != 6
            or any(type(value) is not int for value in expected)
            or _regular_state(root / relative) != expected
        ):
            raise ValueError(f"accepted preparation metadata drift: {relative}")
    if enumerate_tree:
        observed: set[str] = set()
        for path in root.rglob("*"):
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode):
                continue
            if not stat.S_ISREG(mode):
                raise ValueError("preparation inventory contains a non-regular entry")
            observed.add(path.relative_to(root).as_posix())
        if observed != set(files):
            raise ValueError("accepted preparation inventory differs")


def _bindings(receipt: dict[str, Any], execution: dict[str, Any]) -> dict[str, Any]:
    from .runtime import environment_identity
    from .stage_cache import cache_builder_semantic_closure

    root = PREPARATION_ROOT
    preparation = _metadata(root / "config" / "g0-identity.json")
    _self_hashed(preparation, "identity")
    if preparation["identity"] != ACCEPTED_G0 or preparation["code_head"] != ACCEPTED_CANDIDATE:
        raise ValueError("accepted preparation G0 mismatch")
    for key in (
        "lock_identity",
        "runtime_identity",
        "config_identity",
        "register_identity",
        "release_identity",
        "parent_identity",
        "harness_identity",
    ):
        if preparation[key] != execution[key]:
            raise ValueError(f"preparation/execution {key} drift")
    binding = _metadata(root / "config" / "execution-input.json")
    capsule = _metadata(root / "input" / "development-execution-context.json")
    _self_hashed(capsule, "capsule_identity")
    if (
        binding["artifact_type"] != "R4.C_AUTHENTICATED_EXECUTION_INPUT"
        or binding["g0_identity"] != ACCEPTED_G0
        or capsule["schema"] != "R4-P0-DEVELOPMENT-EXECUTION-CONTEXT-V2"
        or capsule["g0_identity"] != ACCEPTED_G0
        or capsule["capsule_identity"] != receipt["capsule_identity"]
        or capsule["config_identity"] != execution["config_identity"]
        or capsule["foundation_content_identity"] != binding["foundation_identity"]
        or capsule["preparation_preprocessor_identity"] != binding["preprocessor_identity"]
        or capsule["support_identity"] != binding["support_identity"]
        or binding["raw_tensor_store_identity"] != receipt["raw_identity"]
        or binding["raw_tensor_store_path"]
        != str(root / "input" / "raw-tensors" / receipt["raw_identity"])
        or set(binding["prepared_development_stages"]) != {"DEV_2", "DEV_3"}
        or set(capsule["stages"]) != {"DEV_2", "DEV_3"}
    ):
        raise ValueError("accepted preparation input/capsule binding mismatch")
    closure = cache_builder_semantic_closure()
    numerical = sha256_bytes(canonical_json(environment_identity()))
    stages: dict[str, Any] = {}
    for stage in ("DEV_2", "DEV_3"):
        accepted = receipt["stages"][stage]
        prepared = binding["prepared_development_stages"][stage]
        if (
            prepared["stage_identity"] != accepted["prepared_identity"]
            or prepared["path"]
            != str(root / "input" / "prepared-development" / stage / accepted["prepared_identity"])
            or prepared["raw_store_path"] != binding["raw_tensor_store_path"]
            or prepared["raw_store_identity"] != receipt["raw_identity"]
        ):
            raise ValueError(f"accepted {stage} prepared/raw identity drift")
        path = root / "input" / "development-execution-stages" / f"{stage}.json"
        metadata = _metadata(path)
        _self_hashed(metadata, "stage_identity")
        cache_root = root / "stage-cache" / stage / accepted["cache_identity"]
        manifest = _metadata(cache_root / "manifest.json")
        seal = _metadata(cache_root / "seal.json")
        _self_hashed(manifest, "manifest_identity")
        _self_hashed(seal, "seal_identity")
        descriptor = capsule["stages"][stage]
        if (
            metadata["schema"] != "R4-P0-DEVELOPMENT-EXECUTION-STAGE-V2"
            or metadata["stage"] != stage
            or metadata["stage_identity"] != accepted["stage_identity"]
            or descriptor["stage_identity"] != accepted["stage_identity"]
            or descriptor["path"] != str(path)
            or descriptor["sha256"] != sha256_bytes(path.read_bytes())
            or metadata["cache_root"] != str(cache_root)
            or metadata["cache_identity"] != accepted["cache_identity"]
            or manifest["schema"] != "R4-P0-STAGE-CACHE-V2"
            or seal["schema"] != manifest["schema"]
            or manifest["manifest_identity"] != accepted["manifest_identity"]
            or seal["manifest_identity"] != manifest["manifest_identity"]
            or seal["cache_identity"] != accepted["cache_identity"]
            or seal["manifest_sha256"] != sha256_bytes((cache_root / "manifest.json").read_bytes())
            or manifest["builder_semantic_closure"] != closure
            or manifest["semantic_inputs"] != metadata["semantic_inputs"]
            or metadata["semantic_inputs"]["config_identity"] != execution["config_identity"]
            or metadata["semantic_inputs"]["numerical_runtime_identity"] != numerical
        ):
            raise ValueError(f"accepted {stage} cache/stage/closure/numerical identity drift")
        stages[stage] = {
            "accepted": accepted,
            "stage": metadata,
            "manifest": manifest,
            "seal": seal,
        }
    return {
        "preparation_g0": preparation,
        "execution_input_identity": sha256_bytes(canonical_json(binding)),
        "execution_input": binding,
        "capsule": capsule,
        "raw_identity": receipt["raw_identity"],
        "stages": stages,
        "configuration_identity": execution["config_identity"],
        "numerical_runtime_identity": numerical,
        "cache_closure_identity": closure,
    }


def _payload(root: Path, execution: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "acceptance_event": ACCEPTANCE_EVENT,
        "preparation_root": str(PREPARATION_ROOT),
        "preparation_candidate": ACCEPTED_CANDIDATE,
        "preparation_tree": ACCEPTED_TREE,
        "accepted_receipt_path": str(ACCEPTED_RECEIPT),
        "accepted_receipt_sha256": ACCEPTED_RECEIPT_SHA256,
        "execution_root": str(root),
        "execution_g0": execution,
        "inventory_identity": sha256_bytes(canonical_json(receipt["files"])),
        "bindings": _bindings(receipt, execution),
    }


def create_preparation_reference(root: Path) -> dict[str, Any]:
    """Create only the two metadata files; callers require separate creation authority."""
    from .execution import capture_g0_identity

    if root.absolute() != root.resolve() or root.exists() or root.is_symlink():
        raise FileExistsError("preparation reference requires an absent canonical execution root")
    identity = capture_g0_identity(root)
    receipt = _accepted_receipt()
    _check_inventory(receipt["files"], enumerate_tree=True)
    payload = _payload(root, identity.to_dict(), receipt)
    payload["reference_identity"] = sha256_bytes(canonical_json(payload))
    root.mkdir(parents=True, exist_ok=False)
    create_json_once(root / "config" / "g0-identity.json", identity.to_dict())
    create_json_once(reference_path(root), payload)
    return payload


@dataclass
class PreparationCapability:
    root: Path
    payload: dict[str, Any]
    files: dict[str, Any]
    pid: int
    active: bool = True
    epoch_evidence: tuple[Path, dict[str, Any], Path, dict[str, Any]] | None = None

    def validate(self, root: Path) -> None:
        from .execution import capture_g0_identity

        self.check_live(root)
        if (
            capture_g0_identity(root).to_dict() != self.payload["execution_g0"]
            or _metadata(root / "config" / "g0-identity.json") != self.payload["execution_g0"]
        ):
            raise ValueError("preparation capability execution identity drift")
        if _metadata(reference_path(root)) != self.payload:
            raise ValueError("preparation reference drift")
        if self.epoch_evidence is not None:
            session_path, session, receipt_path, receipt = self.epoch_evidence
            if _metadata(session_path) != session or _metadata(receipt_path) != receipt:
                raise ValueError("preparation capability session/cache evidence drift")
        _check_inventory(self.files, enumerate_tree=False)

    def check_live(self, root: Path) -> None:
        """Consumers reuse the entry proof; the owning entry validates drift on exit."""
        if not self.active or self.pid != os.getpid() or root != self.root:
            raise ValueError(
                "preparation capability expired, reused or belongs to another process/root"
            )

    def bind_epoch(self, epoch_id: str, receipt_identity: str) -> None:
        if not epoch_id.endswith("-pre"):
            raise ValueError("wrapper requires a pre-execution cache epoch")
        session_path = self.root / "sessions" / f"{epoch_id[:-4]}.json"
        receipt_path = self.root / "cache-verification" / f"{epoch_id}.json"
        session, receipt = _metadata(session_path), _metadata(receipt_path)
        _self_hashed(session, "session_identity")
        _self_hashed(receipt, "receipt_identity")
        if (
            session["schema"] != "R4-P0-SUPERVISOR-EPOCH-V1"
            or session["epoch_id"] != epoch_id[:-4]
            or session["cache_receipts"] != [receipt_identity]
            or session["exact_head"] != self.payload["execution_g0"]["code_head"]
            or session["g0_identity"] != self.payload["execution_g0"]["identity"]
            or any(
                record["output_root"] != str(self.root)
                or record["execution_g0"] != self.payload["execution_g0"]
                or record["preparation_reference_identity"] != self.payload["reference_identity"]
                for record in (session, receipt)
            )
            or receipt["receipt_identity"] != receipt_identity
        ):
            raise ValueError("preparation capability session/cache binding mismatch")
        evidence = (session_path, session, receipt_path, receipt)
        if self.epoch_evidence is not None and self.epoch_evidence != evidence:
            raise ValueError("preparation capability cannot be reused for another epoch")
        self.epoch_evidence = evidence


_active: ContextVar[PreparationCapability | None] = ContextVar(
    "preparation_reference", default=None
)


def capability_or_none(root: Path) -> PreparationCapability | None:
    capability = _active.get()
    if capability is not None:
        capability.check_live(root)
        return capability
    path = reference_path(root)
    if path.exists() or path.is_symlink():
        raise RuntimeError("preparation reference requires an authenticated process entry")
    return None


def preparation_root(root: Path) -> Path:
    capability = capability_or_none(root)
    return PREPARATION_ROOT if capability is not None else root


def preparation_g0(root: Path, ordinary: str) -> str:
    return ACCEPTED_G0 if capability_or_none(root) is not None else ordinary


def reference_binding(root: Path) -> dict[str, Any]:
    capability = capability_or_none(root)
    if capability is None:
        return {}
    return {
        "preparation_reference_identity": capability.payload["reference_identity"],
        "execution_g0": capability.payload["execution_g0"],
    }


@contextmanager
def preparation_entry(root: Path) -> Iterator[PreparationCapability | None]:
    """Authenticate once per entry, share only within it, and expire on every exit."""
    from .execution import _require_identity

    existing = _active.get()
    if existing is not None:
        existing.check_live(root)
        yield existing
        return
    path = reference_path(root)
    if not path.exists() and not path.is_symlink():
        yield None
        return
    execution = _require_identity(root).to_dict()
    receipt = _accepted_receipt()
    _check_inventory(receipt["files"], enumerate_tree=True)
    payload = _metadata(path)
    _self_hashed(payload, "reference_identity")
    expected = _payload(root, execution, receipt)
    expected["reference_identity"] = sha256_bytes(canonical_json(expected))
    if payload != expected:
        raise ValueError("preparation reference binding drift")
    capability = PreparationCapability(root, payload, receipt["files"], os.getpid())
    token = _active.set(capability)
    try:
        try:
            yield capability
        except BaseException as primary:
            try:
                capability.validate(root)
            except BaseException as drift:
                raise BaseExceptionGroup(
                    "execution and preparation validation failed", [primary, drift]
                ) from None
            raise
        capability.validate(root)
    finally:
        capability.active = False
        _active.reset(token)


def reference_process[**P, R](
    operation: Callable[Concatenate[Path, P], R],
) -> Callable[Concatenate[Path, P], R]:
    """Keep direct Python entries under the same lifetime contract as the CLI."""

    @wraps(operation)
    def entered(root: Path, /, *args: P.args, **kwargs: P.kwargs) -> R:
        with preparation_entry(Path(root).resolve()):
            return operation(root, *args, **kwargs)

    return entered
