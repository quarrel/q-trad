from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import experiments.r4_residual_graph.candidate_validation as validation


def _mock_success(monkeypatch: pytest.MonkeyPatch, head: str = "candidate") -> None:
    def git(root: Path, *args: str) -> str:
        if args == ("rev-parse", "--show-toplevel"):
            return str(root)
        return head if args == ("rev-parse", "HEAD") else ""

    monkeypatch.setattr(validation, "_git", git)
    monkeypatch.setattr(
        validation,
        "_run",
        lambda command, root: subprocess.CompletedProcess(command, 0, b"ok\n", b""),
    )


def test_validation_receipt_authenticates_and_rejects_tamper_and_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_success(monkeypatch)
    receipt = validation.run_candidate_validation(
        tmp_path / "validation", expected_head="candidate", working_root=tmp_path
    )
    assert (
        validation.authenticate_candidate_validation(
            receipt, expected_head="candidate", expected_working_root=tmp_path
        )["status"]
        == "PASS"
    )
    with pytest.raises(ValueError, match=r"HEAD mismatch|contract"):
        validation.authenticate_candidate_validation(
            receipt, expected_head="different", expected_working_root=tmp_path
        )
    payload = json.loads(receipt.read_text())
    payload["commands"][0]["exit_code"] = 9
    receipt.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        validation.authenticate_candidate_validation(
            receipt, expected_head="candidate", expected_working_root=tmp_path
        )


def test_validation_failed_command_is_sealed_and_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        validation,
        "_git",
        lambda root, *args: (
            str(root)
            if args == ("rev-parse", "--show-toplevel")
            else ("candidate" if args == ("rev-parse", "HEAD") else "")
        ),
    )
    calls = 0

    def run(command: tuple[str, ...], root: Path) -> subprocess.CompletedProcess[bytes]:
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(command, 2, b"", b"failure")

    monkeypatch.setattr(validation, "_run", run)
    root = tmp_path / "validation"
    with pytest.raises(validation.ValidationFailed, match="focused"):
        validation.run_candidate_validation(root, expected_head="candidate", working_root=tmp_path)
    assert calls == 1
    payload = json.loads((root / "validation.json").read_text())
    assert payload["status"] == "FAILED" and payload["commands"][0]["exit_code"] == 2
    assert (root / "seal.json").exists()


def test_validation_rejects_wrong_working_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "different").mkdir()
    monkeypatch.setattr(
        validation,
        "_git",
        lambda root, *args: (
            str(tmp_path / "different") if args == ("rev-parse", "--show-toplevel") else "candidate"
        ),
    )
    with pytest.raises(ValueError, match="working root"):
        validation.run_candidate_validation(
            tmp_path / "validation", expected_head="candidate", working_root=tmp_path
        )


@pytest.mark.parametrize("state", ["WORKTREE_DIRTY", "HEAD_CHANGED"])
def test_validation_seals_command_induced_repository_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    checks = 0

    def git(root: Path, *args: str) -> str:
        nonlocal checks
        if args == ("rev-parse", "--show-toplevel"):
            return str(root)
        if args == ("rev-parse", "HEAD"):
            checks += 1
            return "changed" if state == "HEAD_CHANGED" and checks > 1 else "candidate"
        if args == ("status", "--porcelain"):
            return "?? mutation" if state == "WORKTREE_DIRTY" and checks > 1 else ""
        raise AssertionError(args)

    monkeypatch.setattr(validation, "_git", git)
    monkeypatch.setattr(
        validation,
        "_run",
        lambda command, root: subprocess.CompletedProcess(command, 0, b"ok", b""),
    )
    output = tmp_path / "validation"
    with pytest.raises(validation.ValidationFailed, match="focused"):
        validation.run_candidate_validation(
            output, expected_head="candidate", working_root=tmp_path
        )
    payload = json.loads((output / "validation.json").read_text())
    assert payload["status"] == "FAILED"
    assert payload["commands"][0]["repository_state"] == state


def test_validation_final_command_drift_cannot_seal_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(validation, "COMMANDS", (("first", ("first",)), ("final", ("final",))))
    completed_commands = 0

    def git(root: Path, *args: str) -> str:
        if args == ("rev-parse", "--show-toplevel"):
            return str(root)
        if args == ("rev-parse", "HEAD"):
            return "candidate"
        if args == ("status", "--porcelain"):
            return "?? final-mutation" if completed_commands == 2 else ""
        raise AssertionError(args)

    def run(command: tuple[str, ...], root: Path) -> subprocess.CompletedProcess[bytes]:
        nonlocal completed_commands
        completed_commands += 1
        return subprocess.CompletedProcess(command, 0, b"ok", b"")

    monkeypatch.setattr(validation, "_git", git)
    monkeypatch.setattr(validation, "_run", run)
    output = tmp_path / "validation"
    with pytest.raises(validation.ValidationFailed, match="final"):
        validation.run_candidate_validation(
            output, expected_head="candidate", working_root=tmp_path
        )
    payload = json.loads((output / "validation.json").read_text())
    assert payload["status"] == "FAILED"
    assert [item["repository_state"] for item in payload["commands"]] == [
        "CLEAN",
        "WORKTREE_DIRTY",
    ]


def test_authentication_rechecks_live_repo_from_bound_root_not_process_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(("git", "init", "-q"), cwd=repo, check=True)
    subprocess.run(("git", "config", "user.name", "Test"), cwd=repo, check=True)
    subprocess.run(("git", "config", "user.email", "test@example.invalid"), cwd=repo, check=True)
    tracked = repo / "tracked.txt"
    tracked.write_text("initial")
    subprocess.run(("git", "add", "tracked.txt"), cwd=repo, check=True)
    subprocess.run(("git", "commit", "-qm", "initial"), cwd=repo, check=True)
    head = subprocess.run(
        ("git", "rev-parse", "HEAD"), cwd=repo, check=True, text=True, capture_output=True
    ).stdout.strip()
    monkeypatch.setattr(
        validation,
        "_run",
        lambda command, root: subprocess.CompletedProcess(command, 0, b"ok", b""),
    )
    monkeypatch.chdir(tmp_path)
    receipt = validation.run_candidate_validation(
        tmp_path / "validation", expected_head=head, working_root=repo
    )
    assert (
        validation.authenticate_candidate_validation(
            receipt, expected_head=head, expected_working_root=repo
        )["status"]
        == "PASS"
    )

    untracked = repo / "untracked.txt"
    untracked.write_text("drift")
    with pytest.raises(ValueError, match="WORKTREE_DIRTY"):
        validation.authenticate_candidate_validation(
            receipt, expected_head=head, expected_working_root=repo
        )
    untracked.unlink()
    tracked.write_text("new head")
    subprocess.run(("git", "add", "tracked.txt"), cwd=repo, check=True)
    subprocess.run(("git", "commit", "-qm", "drift"), cwd=repo, check=True)
    with pytest.raises(ValueError, match="HEAD mismatch"):
        validation.authenticate_candidate_validation(
            receipt, expected_head=head, expected_working_root=repo
        )
