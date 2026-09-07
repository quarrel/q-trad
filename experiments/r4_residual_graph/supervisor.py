"""Durable one-slot R4-P0 development supervisor and crash reconciler."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, cast

from .attempt_artifacts import (
    AttemptIdentity,
    CreateOnlyAttemptJournal,
    canonical_json,
    create_json_once,
    preserve_failed_staging,
    publish_attempt_bundle,
    strict_json_object,
    verify_attempt_bundle,
)
from .runtime import PRIMARY_SCHEDULE
from .stage_cache import verify_stage_cache


@dataclass(frozen=True)
class ProcessOwnership:
    boot_id: str
    pid: int
    process_start_ticks: int
    executable_sha256: str
    arguments_sha256: str
    exact_head: str
    g0_identity: str
    output_root: str
    slot_id: str
    attempt: int
    wrapper_receipt_identity: str
    supervisor_epoch_id: str
    cache_receipt_identity: str

    @property
    def identity(self) -> str:
        return hashlib.sha256(canonical_json(asdict(self))).hexdigest()


@dataclass(frozen=True)
class ReconciliationResult:
    recovered: tuple[str, ...]
    live: ProcessOwnership | None


def wrapper_receipt_identity(epoch_id: str, cache_receipt_identity: str, attempt_id: str) -> str:
    return hashlib.sha256(f"{epoch_id}:{cache_receipt_identity}:{attempt_id}".encode()).hexdigest()


@dataclass(frozen=True)
class _ProcessObservation:
    boot_id: str
    pid: int
    process_start_ticks: int
    executable_sha256: str
    arguments_sha256: str


def _read_process_stat(pid: int) -> tuple[int, int]:
    # comm is parenthesised and may itself contain spaces and closing parentheses.
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except UnicodeError:
        raise RuntimeError("process stat identity is unavailable or malformed") from None
    prefix, separator, suffix = raw.rpartition(")")
    try:
        observed_pid, opening, _comm = prefix.partition("(")
        fields = suffix.split()
        if not separator or not opening or len(fields) < 20:
            raise ValueError
        observed = (int(observed_pid), int(fields[19]))
        if observed[0] != pid or observed[1] < 0:
            raise ValueError
        return observed
    except (ValueError, IndexError):
        raise RuntimeError("process stat identity is unavailable or malformed") from None


def _observe_process_or_none(pid: int) -> _ProcessObservation | None:
    """Return absence only when the PID directory is confirmed to have disappeared."""
    try:
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except (OSError, UnicodeError):
        raise RuntimeError("process boot identity is unavailable") from None
    try:
        before = _read_process_stat(pid)
        executable = Path(f"/proc/{pid}/exe").read_bytes()
        arguments = Path(f"/proc/{pid}/cmdline").read_bytes()
        after = _read_process_stat(pid)
    except OSError:
        try:
            Path(f"/proc/{pid}").stat()
        except FileNotFoundError:
            return None
        except OSError:
            raise RuntimeError("process presence is unavailable") from None
        raise RuntimeError("live process fingerprint is unavailable") from None
    if not boot_id or not executable or not arguments:
        raise RuntimeError("process fingerprint is incomplete")
    if before != after:
        raise RuntimeError("process identity changed during observation")
    return _ProcessObservation(
        boot_id,
        before[0],
        before[1],
        hashlib.sha256(executable).hexdigest(),
        hashlib.sha256(arguments).hexdigest(),
    )


def read_process_ownership(
    *,
    pid: int,
    exact_head: str,
    g0_identity: str,
    output_root: Path,
    slot_id: str,
    attempt: int,
    wrapper_receipt_identity: str,
    supervisor_epoch_id: str,
    cache_receipt_identity: str,
) -> ProcessOwnership:
    observed = _observe_process_or_none(pid)
    if observed is None:
        raise ProcessLookupError("process exited before ownership could be recorded")
    return ProcessOwnership(
        observed.boot_id,
        observed.pid,
        observed.process_start_ticks,
        observed.executable_sha256,
        observed.arguments_sha256,
        exact_head,
        g0_identity,
        str(output_root.resolve()),
        slot_id,
        attempt,
        wrapper_receipt_identity,
        supervisor_epoch_id,
        cache_receipt_identity,
    )


def process_matches(receipt: ProcessOwnership) -> bool:
    """Compare independent process facts with the original authenticated receipt."""
    current = _observe_process_or_none(receipt.pid)
    if current is None:
        return False
    expected = _ProcessObservation(
        receipt.boot_id,
        receipt.pid,
        receipt.process_start_ticks,
        receipt.executable_sha256,
        receipt.arguments_sha256,
    )
    if current != expected:
        raise RuntimeError("live process identity differs from authenticated ownership")
    return True


def _attempt_from_record(record: Mapping[str, object]) -> AttemptIdentity:
    raw = record["attempt"]
    if not isinstance(raw, Mapping):
        raise ValueError("journal attempt identity must be an object")
    attempt = cast(Mapping[str, object], raw)
    seed = attempt["seed"]
    attempt_number = attempt["attempt"]
    if (
        not isinstance(seed, int)
        or isinstance(seed, bool)
        or not isinstance(attempt_number, int)
        or isinstance(attempt_number, bool)
    ):
        raise ValueError("journal seed and attempt number must be integers")
    return AttemptIdentity(
        release_identity=str(attempt["release_identity"]),
        g0_identity=str(attempt["g0_identity"]),
        output_root=str(attempt["output_root"]),
        slot_id=str(attempt["slot_id"]),
        family_id=str(attempt["family_id"]),
        seed=seed,
        stage=str(attempt["stage"]),
        attempt=attempt_number,
        mode=str(attempt["mode"]),
    )


def _read_ownership_evidence(path: Path, identity_key: str) -> dict[str, Any]:
    if path.is_symlink() or path.resolve(strict=True) != path.absolute() or not path.is_file():
        raise ValueError("process ownership evidence must be a canonical regular file")
    payload = strict_json_object(path.read_bytes())
    if not isinstance(payload, dict):
        raise ValueError("process ownership evidence must be an object")
    identity = payload.pop(identity_key)
    if hashlib.sha256(canonical_json(payload)).hexdigest() != identity:
        raise ValueError("process ownership evidence identity mismatch")
    return payload


def _load_ownership(
    root: Path, attempt: AttemptIdentity, started: Mapping[str, object]
) -> ProcessOwnership:
    unsigned = dict(started)
    record_identity = unsigned.pop("record_identity")
    if (
        hashlib.sha256(canonical_json(unsigned)).hexdigest() != record_identity
        or started["attempt_id"] != attempt.identity
        or started["attempt"] != attempt.to_dict()
    ):
        raise ValueError("process ownership STARTED identity mismatch")
    started_payload = started["payload"]
    if not isinstance(started_payload, Mapping):
        raise ValueError("process ownership STARTED payload must be an object")
    started_payload = cast(Mapping[str, object], started_payload)
    path = root / "sessions" / "ownership" / f"{attempt.identity}.json"
    payload = _read_ownership_evidence(path, "ownership_identity")
    if set(payload) != {field.name for field in fields(ProcessOwnership)}:
        raise ValueError("process ownership receipt fields are incomplete or unexpected")
    receipt = ProcessOwnership(**payload)
    for name, value in asdict(receipt).items():
        if name in {"pid", "process_start_ticks", "attempt"}:
            if type(value) is not int or value < (1 if name == "pid" else 0):
                raise ValueError("process ownership receipt has invalid integer fields")
        elif not isinstance(value, str) or not value:
            raise ValueError("process ownership receipt has incomplete identity fields")
    if (
        receipt.output_root != str(root)
        or attempt.output_root != str(root)
        or receipt.slot_id != attempt.slot_id
        or receipt.attempt != attempt.attempt
        or receipt.g0_identity != attempt.g0_identity
    ):
        raise ValueError("process ownership receipt differs from attempt")
    epoch = receipt.supervisor_epoch_id
    if Path(epoch).name != epoch or epoch in {".", ".."}:
        raise ValueError("process ownership epoch must be a path component")
    if (
        started_payload["supervisor_epoch_id"] != epoch
        or started_payload["cache_receipt_identity"] != receipt.cache_receipt_identity
    ):
        raise ValueError("process ownership receipt differs from STARTED provenance")
    session = _read_ownership_evidence(root / "sessions" / f"{epoch}.json", "session_identity")
    from .preparation_reference import reference_binding

    reference = reference_binding(root)
    if session != {
        "schema": "R4-P0-SUPERVISOR-EPOCH-V1",
        "epoch_id": epoch,
        "exact_head": receipt.exact_head,
        "g0_identity": attempt.g0_identity,
        "output_root": str(root),
        "cache_receipts": [receipt.cache_receipt_identity],
        **reference,
    }:
        raise ValueError("process ownership receipt differs from originating supervisor session")
    cache = _read_ownership_evidence(
        root / "cache-verification" / f"{epoch}-pre.json", "receipt_identity"
    )
    if (
        hashlib.sha256(canonical_json(cache)).hexdigest() != receipt.cache_receipt_identity
        or cache["schema"] != "R4-P0-CACHE-EPOCH-V1"
        or cache["epoch_id"] != f"{epoch}-pre"
        or cache["output_root"] != str(root)
        or any(cache[key] != value for key, value in reference.items())
    ):
        raise ValueError("process ownership receipt differs from originating cache receipt")
    if receipt.wrapper_receipt_identity != wrapper_receipt_identity(
        epoch, receipt.cache_receipt_identity, attempt.identity
    ):
        raise ValueError("process ownership wrapper receipt identity mismatch")
    return receipt


def write_process_ownership(
    root: Path, attempt: AttemptIdentity, receipt: ProcessOwnership
) -> Path:
    payload = asdict(receipt) | {"ownership_identity": receipt.identity}
    path = root.resolve() / "sessions" / "ownership" / f"{attempt.identity}.json"
    create_json_once(path, payload)
    return path


def _seal_identity(bundle: Path, attempt: AttemptIdentity) -> str:
    verified = verify_attempt_bundle(bundle, attempt)
    return str(cast(Mapping[str, object], verified["seal"])["seal_identity"])


def _complete_failure(
    root: Path,
    journal: CreateOnlyAttemptJournal,
    attempt: AttemptIdentity,
    failure: Path,
    *,
    reason: str,
) -> None:
    closure_path = failure / "closure.json"
    if closure_path.exists():
        raw = strict_json_object(closure_path.read_bytes())
        if not isinstance(raw, dict) or raw.get("attempt_id") != attempt.identity:
            raise ValueError("failure closure differs from attempt")
        closure = cast(dict[str, object], raw)
    else:
        closure = {
            "schema": "R4-P0-ATTEMPT-FAILURE-V1",
            "attempt_id": attempt.identity,
            "reason": reason,
        }
        create_json_once(closure_path, closure)
    journal.append(
        attempt,
        "FAILED",
        {"failure": failure.relative_to(root).as_posix(), "closure": closure},
    )


def reconcile_attempts(root: Path, journal: CreateOnlyAttemptJournal) -> ReconciliationResult:
    """Reconcile every crash state before allowing another slot to start."""
    root = root.resolve()
    recovered: list[str] = []
    live: ProcessOwnership | None = None
    records = journal.ordered_records(PRIMARY_SCHEDULE)
    for record in records:
        if _attempt_from_record(record).mode != "PRIMARY":
            raise ValueError("PRIMARY supervisor rejects SMOKE attempts")
    started_records = [record for record in records if record["status"] == "STARTED"]
    started_ids = {str(record["attempt_id"]) for record in started_records}

    # Refuse unresolved ownership anywhere before mutating any crash state.
    absent_attempts: list[AttemptIdentity] = []
    for record in started_records:
        attempt = _attempt_from_record(record)
        records = journal.records(attempt.identity)
        if "SUCCEEDED" in records or "FAILED" in records:
            continue
        ownership = _load_ownership(root, attempt, records["STARTED"])
        staging = root / "staging" / "attempts" / attempt.identity
        bundle = root / "attempts" / attempt.slot_id / f"attempt-{attempt.attempt}"
        failure = root / "failures" / attempt.slot_id / f"attempt-{attempt.attempt}"
        present = sum(path.exists() or path.is_symlink() for path in (staging, bundle, failure))
        if present > 1:
            raise RuntimeError(f"ambiguous attempt crash state: {attempt.identity}")
        if process_matches(ownership):
            if bundle.exists() or failure.exists():
                raise RuntimeError("live owned process has conflicting published crash state")
            if staging.exists() and (staging / "seal.json").exists():
                raise RuntimeError("live owned process has conflicting sealed staging")
            if live is not None:
                raise RuntimeError("multiple matching live attempt processes")
            live = ownership
        else:
            absent_attempts.append(attempt)

    staging_parent = root / "staging" / "attempts"
    if staging_parent.exists():
        for staging in sorted(staging_parent.iterdir()):
            if staging.is_symlink() or not staging.is_dir():
                raise ValueError("attempt staging contains a non-directory entry")
            if staging.name in started_ids:
                continue
            failure = root / "failures" / "pre-started" / staging.name
            preserve_failed_staging(
                staging,
                failure,
                {"schema": "R4-P0-PRE-STARTED-FAILURE-V1", "build_id": staging.name},
            )
            recovered.append(f"pre-STARTED:{staging.name}")

    for attempt in absent_attempts:
        staging = root / "staging" / "attempts" / attempt.identity
        bundle = root / "attempts" / attempt.slot_id / f"attempt-{attempt.attempt}"
        failure = root / "failures" / attempt.slot_id / f"attempt-{attempt.attempt}"
        if bundle.exists():
            seal_identity = _seal_identity(bundle, attempt)
            journal.append(
                attempt,
                "SUCCEEDED",
                {
                    "bundle": bundle.relative_to(root).as_posix(),
                    "seal_identity": seal_identity,
                    "reconciled": True,
                },
            )
            recovered.append(f"published:{attempt.identity}")
        elif staging.exists():
            if (staging / "seal.json").is_file():
                seal_identity = _seal_identity(staging, attempt)
                publish_attempt_bundle(staging, bundle, attempt)
                journal.append(
                    attempt,
                    "SUCCEEDED",
                    {
                        "bundle": bundle.relative_to(root).as_posix(),
                        "seal_identity": seal_identity,
                        "reconciled": True,
                    },
                )
                recovered.append(f"sealed:{attempt.identity}")
            else:
                preserve_failed_staging(
                    staging,
                    failure,
                    {
                        "schema": "R4-P0-ATTEMPT-FAILURE-V1",
                        "attempt_id": attempt.identity,
                        "reason": "post-STARTED pre-seal process interruption",
                    },
                )
                _complete_failure(
                    root,
                    journal,
                    attempt,
                    failure,
                    reason="post-STARTED pre-seal process interruption",
                )
                recovered.append(f"unsealed:{attempt.identity}")
        elif failure.exists():
            _complete_failure(
                root,
                journal,
                attempt,
                failure,
                reason="reconciled moved failure without closure",
            )
            recovered.append(f"failure:{attempt.identity}")
        else:
            journal.append(
                attempt,
                "FAILED",
                {
                    "reason": "STARTED attempt has no live owner or retained payload",
                    "reconciled": True,
                },
            )
            recovered.append(f"absent:{attempt.identity}")
    return ReconciliationResult(tuple(recovered), live)


def verify_epoch_caches(root: Path, epoch_id: str) -> tuple[str, ...]:
    """Authenticate accepted cache proof, or fully verify ordinary-root caches."""
    from .preparation_reference import capability_or_none, preparation_root, reference_binding
    from .stage_cache import VerifiedCache

    capability = capability_or_none(root.resolve())
    input_root = preparation_root(root.resolve())
    cache_root = input_root / "stage-cache"
    if cache_root.is_symlink() or not cache_root.is_dir():
        raise FileNotFoundError("sealed DEV_2 and DEV_3 stage caches are required")
    expected_stages = ("DEV_2", "DEV_3")
    if tuple(sorted(path.name for path in cache_root.iterdir())) != expected_stages:
        raise ValueError("stage-cache must contain exactly DEV_2 and DEV_3")
    caches: list[dict[str, object]] = []
    for stage_name in expected_stages:
        stage = cache_root / stage_name
        if stage.is_symlink() or not stage.is_dir():
            raise ValueError("stage-cache contains a non-directory entry")
        candidates = tuple(stage.iterdir())
        if len(candidates) != 1 or candidates[0].is_symlink():
            raise ValueError(f"{stage_name} must contain exactly one sealed cache")
        manifest_path = candidates[0] / "manifest.json"
        if capability is None:
            manifest_payload = strict_json_object(manifest_path.read_bytes())
            strict_json_object((candidates[0] / "seal.json").read_bytes())
            cache = verify_stage_cache(candidates[0])
        else:
            accepted = capability.payload["bindings"]["stages"][stage_name]
            manifest = accepted["manifest"]
            manifest_payload = manifest
            cache = VerifiedCache(
                candidates[0],
                accepted["accepted"]["cache_identity"],
                manifest["manifest_identity"],
                tuple(manifest["files"]),
            )
        files: list[dict[str, object]] = []
        for metadata_path in (manifest_path,):
            if (
                metadata_path.is_symlink()
                or metadata_path.resolve(strict=True) != metadata_path.absolute()
            ):
                raise ValueError("cache metadata paths must be canonical and contain no symlinks")
            metadata_stat = metadata_path.stat(follow_symlinks=False)
            files.append(
                {
                    "path": str(metadata_path),
                    "device": metadata_stat.st_dev,
                    "inode": metadata_stat.st_ino,
                    "size": metadata_stat.st_size,
                    "ctime_ns": metadata_stat.st_ctime_ns,
                    "sha256": hashlib.sha256(metadata_path.read_bytes()).hexdigest(),
                }
            )
        for manifest_entry in cache.files:
            path = cache.root / str(manifest_entry["path"])
            if path.is_symlink() or path.resolve(strict=True) != path.absolute():
                raise ValueError("cache receipt paths must be canonical and contain no symlinks")
            stat = path.stat(follow_symlinks=False)
            files.append(
                {
                    "path": str(path),
                    "device": stat.st_dev,
                    "inode": stat.st_ino,
                    "size": stat.st_size,
                    "ctime_ns": stat.st_ctime_ns,
                    "sha256": manifest_entry["container_sha256"],
                }
            )
        caches.append(
            {
                "stage": stage_name,
                "root": str(cache.root.resolve(strict=True)),
                "cache_identity": cache.cache_identity,
                "manifest_identity": cache.manifest_identity,
                "manifest": manifest_payload,
                "files": files,
            }
        )
    context_path = input_root / "input" / "development-execution-context.json"
    if context_path.is_symlink() or not context_path.is_file():
        raise FileNotFoundError("sealed development execution context capsule is required")
    context_stat = context_path.stat(follow_symlinks=False)
    context_payload = strict_json_object(context_path.read_bytes())
    context_identity = context_payload.pop("capsule_identity")
    if hashlib.sha256(canonical_json(context_payload)).hexdigest() != context_identity:
        raise ValueError("development execution context capsule identity drifted")
    context_receipt = {
        "path": str(context_path),
        "device": context_stat.st_dev,
        "inode": context_stat.st_ino,
        "size": context_stat.st_size,
        "ctime_ns": context_stat.st_ctime_ns,
        "sha256": hashlib.sha256(context_path.read_bytes()).hexdigest(),
        "capsule_identity": context_identity,
    }
    payload: dict[str, object] = {
        "schema": "R4-P0-CACHE-EPOCH-V1",
        "epoch_id": epoch_id,
        "output_root": str(root.resolve()),
        "caches": caches,
        "execution_context": context_receipt,
        **reference_binding(root.resolve()),
    }
    payload["receipt_identity"] = hashlib.sha256(canonical_json(payload)).hexdigest()
    path = root.resolve() / "cache-verification" / f"{epoch_id}.json"
    create_json_once(path, payload)
    return (str(payload["receipt_identity"]),)


def validate_epoch_cache_metadata(root: Path, epoch_id: str, receipt_identity: str) -> None:
    """Validate canonical cache paths and immutable metadata without rehashing."""
    receipt_path = root.resolve() / "cache-verification" / f"{epoch_id}.json"
    if (
        receipt_path.is_symlink()
        or receipt_path.parent.is_symlink()
        or receipt_path.resolve(strict=True) != receipt_path.absolute()
    ):
        raise ValueError("epoch cache receipt path must be canonical and contain no symlinks")
    payload = strict_json_object(receipt_path.read_bytes())
    if payload["receipt_identity"] != receipt_identity:
        raise ValueError("epoch cache receipt identity mismatch")
    unsigned = dict(payload)
    unsigned.pop("receipt_identity")
    if hashlib.sha256(canonical_json(unsigned)).hexdigest() != receipt_identity:
        raise ValueError("epoch cache receipt content mismatch")
    from .preparation_reference import reference_binding

    if (
        payload["schema"] != "R4-P0-CACHE-EPOCH-V1"
        or payload["epoch_id"] != epoch_id
        or payload["output_root"] != str(root.resolve())
    ):
        raise ValueError("epoch cache receipt binding mismatch")
    binding = reference_binding(root.resolve())
    for key, value in binding.items():
        if payload[key] != value:
            raise ValueError("epoch cache preparation reference drift")
    if binding:
        from .preparation_reference import capability_or_none

        capability = capability_or_none(root.resolve())
        assert capability is not None
        capability.bind_epoch(epoch_id, receipt_identity)
    context = payload["execution_context"]
    context_path = Path(context["path"])
    if context_path.is_symlink() or context_path.resolve(strict=True) != context_path:
        raise ValueError("execution context capsule path drift")
    context_stat = context_path.stat(follow_symlinks=False)
    if (
        context_stat.st_dev,
        context_stat.st_ino,
        context_stat.st_size,
        context_stat.st_ctime_ns,
    ) != (context["device"], context["inode"], context["size"], context["ctime_ns"]):
        raise ValueError("execution context capsule metadata drift")
    for cache in payload["caches"]:
        cache_root = Path(cache["root"])
        if cache_root.is_symlink() or cache_root.resolve(strict=True) != cache_root:
            raise ValueError("epoch cache root path drift")
        for entry in cache["files"]:
            path = Path(entry["path"])
            if path.is_symlink() or path.resolve(strict=True) != path:
                raise ValueError("epoch cache path drift")
            stat = path.stat(follow_symlinks=False)
            observed = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_ctime_ns)
            expected = (entry["device"], entry["inode"], entry["size"], entry["ctime_ns"])
            if observed != expected:
                raise ValueError("epoch cache metadata drift")


def next_development_slot(journal: CreateOnlyAttemptJournal) -> tuple[str, int, str] | None:
    records = journal.ordered_records(PRIMARY_SCHEDULE)
    for record in records:
        if _attempt_from_record(record).mode != "PRIMARY":
            raise ValueError("PRIMARY supervisor rejects SMOKE attempts")
    terminals: set[str] = set()
    open_attempts: set[str] = set()
    for record in records:
        raw_attempt = record["attempt"]
        if not isinstance(raw_attempt, Mapping):
            raise ValueError("journal attempt identity must be an object")
        attempt = cast(Mapping[str, object], raw_attempt)
        slot_id = str(attempt["slot_id"])
        status = str(record["status"])
        if status == "STARTED":
            open_attempts.add(slot_id)
        elif status in {"SUCCEEDED", "FAILED"}:
            open_attempts.discard(slot_id)
            if status == "SUCCEEDED":
                terminals.add(slot_id)
    if open_attempts:
        raise RuntimeError("an existing STARTED attempt must be reconciled before another slot")
    for family, seed, stage in PRIMARY_SCHEDULE:
        if stage == "TERMINAL_FORMER_HOLDOUT":
            continue
        if f"{family}:{seed}:{stage}" not in terminals:
            return family, seed, stage
    return None


def write_supervisor_session(
    root: Path, epoch_id: str, *, exact_head: str, g0_identity: str, cache_receipts: list[str]
) -> Path:
    from .preparation_reference import reference_binding

    payload: dict[str, object] = {
        "schema": "R4-P0-SUPERVISOR-EPOCH-V1",
        "epoch_id": epoch_id,
        "exact_head": exact_head,
        "g0_identity": g0_identity,
        "output_root": str(root.resolve()),
        "cache_receipts": cache_receipts,
        **reference_binding(root.resolve()),
    }
    payload["session_identity"] = hashlib.sha256(canonical_json(payload)).hexdigest()
    path = root.resolve() / "sessions" / f"{epoch_id}.json"
    create_json_once(path, payload)
    return path
