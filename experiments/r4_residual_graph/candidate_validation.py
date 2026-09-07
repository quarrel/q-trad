"""Durable exact-candidate validation receipts created only after review authority."""

from __future__ import annotations

import os
import platform
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .attempt_artifacts import canonical_json, create_json_once, sha256_bytes

_SCHEMA = "R4-D-CANDIDATE-VALIDATION-V1"
_SEAL_SCHEMA = "R4-D-CANDIDATE-VALIDATION-SEAL-V1"
COMMANDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "focused",
        (
            "uv",
            "run",
            "pytest",
            "-q",
            "tests/experiments/r4_residual_graph/test_qualification.py",
            "tests/experiments/r4_residual_graph/test_prior_qualification_evidence.py",
            "tests/experiments/r4_residual_graph/test_candidate_validation.py",
        ),
    ),
    ("full-residual", ("uv", "run", "pytest", "-q", "tests/experiments/r4_residual_graph")),
    (
        "ruff-format",
        (
            "uv",
            "run",
            "ruff",
            "format",
            "--check",
            "experiments/r4_residual_graph",
            "tests/experiments/r4_residual_graph",
        ),
    ),
    (
        "ruff-check",
        (
            "uv",
            "run",
            "ruff",
            "check",
            "experiments/r4_residual_graph",
            "tests/experiments/r4_residual_graph",
        ),
    ),
    ("ty", ("uv", "run", "ty", "check")),
    ("repository-verify", ("ops/dev/verify.sh",)),
    ("diff-check", ("git", "diff", "--check")),
)


class ValidationFailed(RuntimeError):
    """One exact validation command failed after its evidence was sealed."""


def _git(working_root: Path, *args: str) -> str:
    return subprocess.run(
        ("git", *args), cwd=working_root, check=True, text=True, capture_output=True
    ).stdout.strip()


def _run(command: tuple[str, ...], working_root: Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(command, cwd=working_root, check=False, capture_output=True)


def _working_root_contract(working_root: Path) -> dict[str, Any]:
    resolved = working_root.resolve(strict=True)
    stat = resolved.stat()
    return {"canonical_path": str(resolved), "device": stat.st_dev, "inode": stat.st_ino}


def _repository_state_error(working_root: Path, expected_head: str) -> str | None:
    if _git(working_root, "rev-parse", "HEAD") != expected_head:
        return "HEAD_CHANGED"
    if _git(working_root, "status", "--porcelain"):
        return "WORKTREE_DIRTY"
    return None


def candidate_head(working_root: Path) -> str:
    """Return HEAD only for the exact canonical repository checkout."""
    resolved = working_root.resolve(strict=True)
    if Path(_git(resolved, "rev-parse", "--show-toplevel")).resolve(strict=True) != resolved:
        raise ValueError("candidate validation working root is not the repository checkout")
    return _git(resolved, "rev-parse", "HEAD")


def _write_once(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def run_candidate_validation(output_root: Path, *, expected_head: str, working_root: Path) -> Path:
    if output_root.exists() or output_root.is_symlink():
        raise FileExistsError("candidate validation root must be absent")
    root_contract = _working_root_contract(working_root)
    working_root = Path(root_contract["canonical_path"])
    if (
        Path(_git(working_root, "rev-parse", "--show-toplevel")).resolve(strict=True)
        != working_root
    ):
        raise ValueError("candidate validation working root is not the repository checkout")
    state_error = _repository_state_error(working_root, expected_head)
    if state_error is not None:
        raise ValueError(f"candidate validation repository state is invalid: {state_error}")
    head = expected_head
    output_root.mkdir(parents=True, exist_ok=False)
    logs = output_root / "logs"
    logs.mkdir()
    results: list[dict[str, Any]] = []
    for check_id, command in COMMANDS:
        started = datetime.now(UTC).isoformat()
        start = time.monotonic()
        completed = _run(command, working_root)
        state_error = _repository_state_error(working_root, expected_head)
        ended = datetime.now(UTC).isoformat()
        stdout_path = logs / f"{check_id}.stdout"
        stderr_path = logs / f"{check_id}.stderr"
        _write_once(stdout_path, completed.stdout)
        _write_once(stderr_path, completed.stderr)
        results.append(
            {
                "check_id": check_id,
                "command": list(command),
                "exit_code": completed.returncode,
                "started_at_utc": started,
                "ended_at_utc": ended,
                "elapsed_seconds": time.monotonic() - start,
                "stdout_path": stdout_path.relative_to(output_root).as_posix(),
                "stdout_sha256": sha256_bytes(completed.stdout),
                "stderr_path": stderr_path.relative_to(output_root).as_posix(),
                "stderr_sha256": sha256_bytes(completed.stderr),
                "repository_state": "CLEAN" if state_error is None else state_error,
            }
        )
        if completed.returncode != 0 or state_error is not None:
            break
    payload: dict[str, Any] = {
        "schema": _SCHEMA,
        "candidate_identity": head,
        "working_root": root_contract,
        "status": "PASS"
        if len(results) == len(COMMANDS)
        and all(item["exit_code"] == 0 and item["repository_state"] == "CLEAN" for item in results)
        else "FAILED",
        "commands": results,
        "runtime": {"python": platform.python_version(), "platform": platform.platform()},
    }
    payload["runtime_identity"] = sha256_bytes(canonical_json(payload["runtime"]))
    payload["receipt_identity"] = sha256_bytes(canonical_json(payload))
    receipt = output_root / "validation.json"
    create_json_once(receipt, payload)
    create_json_once(
        output_root / "seal.json",
        {
            "schema": _SEAL_SCHEMA,
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
    if payload["status"] != "PASS":
        raise ValidationFailed(f"candidate validation failed at {results[-1]['check_id']}")
    return receipt


def authenticate_candidate_validation(
    receipt: Path, *, expected_head: str, expected_working_root: Path
) -> dict[str, Any]:
    expected_working_root = expected_working_root.resolve(strict=True)
    if candidate_head(expected_working_root) != expected_head:
        raise ValueError("candidate validation live HEAD mismatch")
    state_error = _repository_state_error(expected_working_root, expected_head)
    if state_error is not None:
        raise ValueError(f"candidate validation live repository state is invalid: {state_error}")
    seal_path = receipt.parent / "seal.json"
    if (
        receipt.is_symlink()
        or not receipt.is_file()
        or seal_path.is_symlink()
        or not seal_path.is_file()
    ):
        raise ValueError("candidate validation receipt or seal is invalid")
    payload = __import__("json").loads(receipt.read_text())
    seal = __import__("json").loads(seal_path.read_text())
    identity_payload = dict(payload)
    identity = identity_payload.pop("receipt_identity", None)
    if (
        payload.get("schema") != _SCHEMA
        or payload.get("candidate_identity") != expected_head
        or payload.get("working_root") != _working_root_contract(expected_working_root)
        or payload.get("status") != "PASS"
        or identity != sha256_bytes(canonical_json(identity_payload))
    ):
        raise ValueError("candidate validation receipt contract is invalid")
    if seal != {
        "schema": _SEAL_SCHEMA,
        "receipt": receipt.name,
        "receipt_sha256": sha256_bytes(receipt.read_bytes()),
        "receipt_identity": identity,
    }:
        raise ValueError("candidate validation seal is invalid")
    expected_commands = [(check_id, list(command)) for check_id, command in COMMANDS]
    observed_commands = [
        (item.get("check_id"), item.get("command")) for item in payload["commands"]
    ]
    if observed_commands != expected_commands or any(
        item.get("exit_code") != 0 or item.get("repository_state") != "CLEAN"
        for item in payload["commands"]
    ):
        raise ValueError("candidate validation commands are invalid")
    for item in payload["commands"]:
        for stream in ("stdout", "stderr"):
            expected_relative = f"logs/{item['check_id']}.{stream}"
            if item.get(f"{stream}_path") != expected_relative:
                raise ValueError("candidate validation log path is invalid")
            path = receipt.parent / expected_relative
            if (
                path.is_symlink()
                or not path.is_file()
                or sha256_bytes(path.read_bytes()) != item[f"{stream}_sha256"]
            ):
                raise ValueError("candidate validation log is invalid")
    return payload
