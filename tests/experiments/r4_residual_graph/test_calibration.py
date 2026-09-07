"""Focused tests for hard calibration resource controls."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from experiments.r4_residual_graph import calibration


def test_resource_monitor_actively_cancels_mid_measurement_floor_crossing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    samples = iter(((6 << 30, 10 << 30), (5 << 30, 10 << 30), (3 << 30, 10 << 30)))
    monkeypatch.setattr(calibration.torch.cuda, "mem_get_info", lambda: next(samples))
    cancellations: list[str] = []
    monitor = calibration._ResourceMonitor(cancel=cancellations.append)
    monitor._poll()
    monitor._poll()
    assert monitor.minimum_free_bytes == 3 << 30
    assert cancellations == ["calibration breached the total-device free-memory floor"]
    with pytest.raises(RuntimeError, match="free-memory floor"):
        monitor.require_limits()


def test_resource_monitor_actively_cancels_elapsed_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(calibration.torch.cuda, "mem_get_info", lambda: (6 << 30, 10 << 30))
    monkeypatch.setattr(calibration.time, "monotonic", lambda: 3600.01)
    cancellations: list[str] = []
    monitor = calibration._ResourceMonitor(started=0.0, cancel=cancellations.append)
    monitor._poll()
    assert cancellations == ["calibration exceeded the maximum elapsed-runtime cap"]
    with pytest.raises(RuntimeError, match="elapsed-runtime cap"):
        monitor.require_limits()


def test_failure_receipts_are_complete_and_self_hashing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(calibration.torch.cuda, "mem_get_info", lambda: (3 << 30, 10 << 30))
    monkeypatch.setattr(calibration.time, "monotonic", lambda: 4.0)
    monkeypatch.setattr(calibration.time, "time", lambda: 104.0)
    for name in ("process.json", "stdout.log", "stderr.log"):
        calibration._write_once(tmp_path / name, b"{}" if name == "process.json" else b"")
    calibration._write_once(tmp_path / "calibration.json", b"{}")
    calibration._write_once(tmp_path / "progress.jsonl", b"{}\n")
    monitor = calibration._ResourceMonitor(started=0.0)
    monitor._poll()
    calibration._write_failure_receipts(
        tmp_path,
        head="a" * 40,
        attempt="batch-one",
        reason=monitor.breach or "unreachable",
        minimum_free_bytes=monitor.minimum_free_bytes,
        monitor_elapsed_seconds=monitor.elapsed_seconds,
        started=100.0,
        cpu_resources={"cpu_affinity": list(range(8)), "process_pool_workers": 0},
        total_memory=10 << 30,
    )
    terminal = json.loads((tmp_path / "terminal.json").read_bytes())
    resource = json.loads((tmp_path / "resource.txt").read_bytes())
    inventory = json.loads((tmp_path / "inventory.json").read_bytes())
    assert terminal["status"] == resource["status"] == "FAIL"
    assert terminal["attempt"] == resource["attempt"] == "batch-one"
    assert resource["cuda"]["minimum_total_device_free_bytes"] == 3 << 30
    for name in (
        "process.json",
        "terminal.json",
        "stdout.log",
        "stderr.log",
        "resource.txt",
        "progress.jsonl",
        "calibration.json",
    ):
        assert inventory[name] == calibration._sha(tmp_path / name)


def test_calibration_rejects_reserved_memory_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monitor: Any = type("Monitor", (), {"require_limits": lambda self: None})()
    bundle = cast(calibration.ReusableOracleEvidence, object())
    monkeypatch.setattr(calibration, "FITTED_FAMILY_IDS", ("family",))
    monkeypatch.setattr(calibration.torch.cuda, "reset_peak_memory_stats", lambda: None)
    monkeypatch.setattr(calibration.torch.cuda, "synchronize", lambda: None)
    monkeypatch.setattr(calibration.torch.cuda, "max_memory_allocated", lambda: 5)
    monkeypatch.setattr(calibration.torch.cuda, "max_memory_reserved", lambda: 70)
    monkeypatch.setattr(
        calibration,
        "compare_production_to_oracle",
        lambda *args, phase_callback, release_callback, **kwargs: {},
    )
    with pytest.raises(RuntimeError, match="reserved-memory safety cap"):
        calibration._calibrate_width(4, 0, {"family": bundle}, object(), object(), monitor, 100)


def test_widths_reuse_once_per_repeat_and_bind_identical_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builds: list[int] = []
    productions: list[tuple[int, int]] = []

    def build(
        repeat: int, training: Any, prediction: Any, monitor: Any
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        builds.append(repeat)
        identity = f"oracle-{repeat}"
        return {"family": SimpleNamespace(identity=identity)}, {
            "repeat": repeat,
            "oracle_evidence_elapsed_seconds": 2.5,
            "families": [{"family": "family", "oracle_evidence_identity": identity}],
        }

    def production(
        width: int,
        repeat: int,
        bundles: Any,
        training: Any,
        prediction: Any,
        monitor: Any,
        total_memory_bytes: int,
    ) -> dict[str, Any]:
        productions.append((repeat, width))
        return {
            "family_semantic_identities": ["semantic"],
            "oracle_evidence_identities": [bundles["family"].identity],
            "production_elapsed_seconds": float(width),
            "production_setup_elapsed_seconds": 0.5,
        }

    monkeypatch.setattr(calibration, "FITTED_FAMILY_IDS", ("family",))
    monkeypatch.setattr(calibration, "_build_shared_oracle_evidence", build)
    monkeypatch.setattr(calibration, "_calibrate_width", production)

    widths, shared = calibration._calibrate_widths(
        object(), object(), cast(calibration._ResourceMonitor, object()), 16 << 30
    )

    assert builds == [0, 1]
    assert productions == [(repeat, width) for repeat in range(2) for width in (4, 8, 16, 32, 64)]
    assert [entry["width"] for entry in widths] == [4, 8, 16, 32, 64]
    assert all(entry["shared_evidence_binding"]["passed"] for entry in widths)
    assert all(
        repeat["production_elapsed_seconds"] == float(entry["width"])
        for entry in widths
        for repeat in entry["repeats"]
    )
    assert sum(entry["oracle_evidence_elapsed_seconds"] for entry in shared) == 5.0
    assert calibration._SCHEMA == "R4-P0-BATCH-CALIBRATION-V5"


def test_bind_cpu_resources_caps_affinity_and_torch_threads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    affinity: list[int] = []
    thread_settings: dict[str, int] = {}
    monkeypatch.setattr(calibration.os, "sched_getaffinity", lambda pid: set(range(12)))
    monkeypatch.setattr(
        calibration.os, "sched_setaffinity", lambda pid, cpus: affinity.extend(cpus)
    )
    monkeypatch.setattr(
        calibration.torch,
        "set_num_threads",
        lambda count: thread_settings.__setitem__("intra", count),
    )
    monkeypatch.setattr(
        calibration.torch,
        "set_num_interop_threads",
        lambda count: thread_settings.__setitem__("interop", count),
    )
    monkeypatch.setattr(calibration.torch, "get_num_threads", lambda: thread_settings["intra"])
    monkeypatch.setattr(
        calibration.torch, "get_num_interop_threads", lambda: thread_settings["interop"]
    )
    receipt: dict[str, Any] = calibration._bind_cpu_resources()
    assert affinity == list(range(8))
    assert thread_settings == {"intra": 8, "interop": 8}
    assert receipt["max_schedulable_cpu_concurrency"] == 8
    assert receipt["process_pool_workers"] == 0


def test_oracle_step_probe_has_distinct_cli_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[str, Path, str]] = []
    monkeypatch.setattr(
        calibration,
        "_supervise_oracle_step_probe",
        lambda path, *, attempt: calls.append(("supervisor", path, attempt)),
    )
    monkeypatch.setattr(
        calibration,
        "_run_oracle_step_probe",
        lambda path, *, attempt: calls.append(("worker", path, attempt)),
    )

    assert (
        calibration.main(["--oracle-step-probe", "--attempt", "reproduction11", str(tmp_path)]) == 0
    )
    assert (
        calibration.main(
            ["--worker", "--oracle-step-probe", "--attempt", "reproduction11", str(tmp_path)]
        )
        == 0
    )
    assert calls == [
        ("supervisor", tmp_path, "reproduction11"),
        ("worker", tmp_path, "reproduction11"),
    ]
    assert calibration._ORACLE_STEP_PROBE_SCHEMA == "R4-P0-ORACLE-STEP-PROBE-V1"
    assert calibration._ORACLE_STEP_PROBE_MINIMUM_FREE_BYTES == 4_831_838_208


@pytest.mark.parametrize("probe", (False, True))
def test_calibration_cli_passes_attempt_to_supervisor_and_worker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, probe: bool
) -> None:
    calls: list[tuple[str, Path, str, bool]] = []
    monkeypatch.setattr(
        calibration,
        "_supervise_run",
        lambda path, *, attempt, probe: calls.append(("supervisor", path, attempt, probe)),
    )
    monkeypatch.setattr(
        calibration,
        "run",
        lambda path, *, attempt, supervised, probe: calls.append(("worker", path, attempt, probe)),
    )
    probe_flag = ["--probe"] if probe else []

    assert calibration.main([*probe_flag, "--attempt", "batch-one", str(tmp_path)]) == 0
    assert calibration.main(["--worker", *probe_flag, "--attempt", "batch-one", str(tmp_path)]) == 0
    assert calls == [
        ("supervisor", tmp_path, "batch-one", probe),
        ("worker", tmp_path, "batch-one", probe),
    ]


def test_calibration_cli_requires_attempt(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    with pytest.raises(SystemExit):
        calibration.main([str(tmp_path)])
    assert "--attempt is required" in capsys.readouterr().err


@pytest.mark.parametrize("probe", (False, True))
def test_calibration_root_binds_candidate_and_attempt(tmp_path: Path, probe: bool) -> None:
    category = "r4-perf8-production-probe" if probe else "r4-perf8-batch-calibration"
    assert calibration._calibration_root(
        tmp_path / "worktrees" / "checkout", "a" * 40, "batch-one", probe=probe
    ) == (
        tmp_path
        / "MAP_orchestrator/r4-p0-20260828-ddd66f9/longrun"
        / category
        / f"{'a' * 40}-batch-one"
    )


@pytest.mark.parametrize("attempt", ("../escape", "Batch-one", "a" * 33, ""))
def test_calibration_root_rejects_invalid_attempt(tmp_path: Path, attempt: str) -> None:
    with pytest.raises(ValueError, match="calibration attempt"):
        calibration._calibration_root(
            tmp_path / "worktrees" / "checkout", "a" * 40, attempt, probe=False
        )


@pytest.mark.parametrize("probe", (False, True))
def test_supervisor_rejects_arbitrary_and_head_only_roots(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, probe: bool
) -> None:
    head = "a" * 40
    working_root = tmp_path / "worktrees" / "checkout"
    category = "r4-perf8-production-probe" if probe else "r4-perf8-batch-calibration"
    monkeypatch.setattr(
        calibration,
        "__file__",
        str(working_root / "experiments" / "r4_residual_graph" / "calibration.py"),
    )
    monkeypatch.setattr(calibration, "candidate_head", lambda root: head)

    for wrong_root in (
        tmp_path / "arbitrary",
        tmp_path / "MAP_orchestrator/r4-p0-20260828-ddd66f9/longrun" / category / head,
    ):
        with pytest.raises(ValueError, match="calibration output must be"):
            calibration._supervise_run(wrong_root, attempt="batch-one", probe=probe)


@pytest.mark.parametrize("probe", (False, True))
def test_supervisor_binds_attempt_through_worker_and_receipts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, probe: bool
) -> None:
    head = "a" * 40
    attempt = "batch-one"
    working_root = tmp_path / "worktrees" / "checkout"
    fake_file = working_root / "experiments" / "r4_residual_graph" / "calibration.py"
    output_root = calibration._calibration_root(working_root, head, attempt, probe=probe)
    output_root.mkdir(parents=True)
    process = {
        "schema": calibration._SCHEMA,
        "candidate": head,
        "attempt": attempt,
        "pid": 42,
    }
    calibration_payload = {
        "schema": calibration._SCHEMA,
        "candidate": head,
        "attempt": attempt,
        "production_probe_projection": {"passed": True},
    }
    for name, payload in (
        ("process.json", calibration._canonical(process)),
        ("calibration.json", calibration._canonical(calibration_payload)),
        (
            "terminal.json",
            calibration._canonical(
                {
                    "schema": calibration._SCHEMA,
                    "candidate": head,
                    "attempt": attempt,
                }
            ),
        ),
        (
            "resource.txt",
            calibration._canonical(
                {
                    "schema": calibration._SCHEMA,
                    "candidate": head,
                    "attempt": attempt,
                }
            ),
        ),
        ("stdout.log", b""),
        ("stderr.log", b""),
        ("progress.jsonl", b""),
    ):
        calibration._write_once(output_root / name, payload)

    commands: list[tuple[str, ...]] = []

    def supervise(command: tuple[str, ...], **kwargs: Any) -> SimpleNamespace:
        commands.append(command)
        kwargs["completed_receipt_validator"](
            {
                "candidate": head,
                "attempt": attempt,
                "pid": 42,
                "process_sha256": calibration._sha(output_root / "process.json"),
                "calibration_sha256": calibration._sha(output_root / "calibration.json"),
                "projection_passed": True,
            }
        )
        return SimpleNamespace(
            status="PASS",
            ready_payload={"attempt": attempt},
            cleanup_enforced=False,
            returncode=0,
            child_identity=SimpleNamespace(
                pid=42, boot_id="boot", start_ticks="ticks", cmdline=command
            ),
            telemetry_anomalies=(),
            telemetry_evidence=lambda: {},
        )

    monkeypatch.setattr(calibration, "__file__", str(fake_file))
    monkeypatch.setattr(calibration, "candidate_head", lambda root: head)
    monkeypatch.setattr(
        calibration.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="GPU-exact\n"),
    )
    monkeypatch.setattr(calibration, "nvidia_memory_bytes", lambda uuid: (16 << 30, 8 << 30))
    monkeypatch.setattr(calibration, "supervise", supervise)

    calibration._supervise_run(output_root, attempt=attempt, probe=probe)

    assert commands[0][3:6] == ("--worker", "--attempt", attempt)
    supervisor = json.loads((output_root / "supervisor.json").read_bytes())
    inventory = json.loads((output_root / "inventory.json").read_bytes())
    assert supervisor["attempt"] == attempt
    for name in (
        "process.json",
        "terminal.json",
        "stdout.log",
        "stderr.log",
        "resource.txt",
        "calibration.json",
        "progress.jsonl",
        "supervisor.json",
    ):
        assert inventory[name] == calibration._sha(output_root / name)


def test_oracle_step_probe_rejects_non_head_keyed_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="oracle-step probe output must be"):
        calibration._run_oracle_step_probe(tmp_path / "wrong", attempt="reproduction11")
    with pytest.raises(ValueError, match="oracle-step probe output must be"):
        calibration._supervise_oracle_step_probe(tmp_path / "wrong", attempt="reproduction11")


def test_oracle_step_probe_does_not_alias_production_probe(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    with pytest.raises(SystemExit):
        calibration.main(["--probe", "--oracle-step-probe", str(tmp_path)])
    assert "mutually exclusive" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("reason", "minimum_free_bytes", "anomalies"),
    [
        (
            "external GPU telemetry unavailable",
            0,
            (
                {
                    "attempt": 1,
                    "elapsed_seconds": 0.1,
                    "exception_class": "CalledProcessError",
                    "returncode": 3,
                    "stdout": "",
                    "stderr": "fault",
                },
            ),
        ),
        (
            "calibration breached the total-device free-memory floor",
            3 << 30,
            (),
        ),
    ],
)
def test_oracle_probe_failure_seals_truthful_complete_receipts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    reason: str,
    minimum_free_bytes: int,
    anomalies: tuple[dict[str, object], ...],
) -> None:
    from experiments.r4_residual_graph import resource_watchdog

    head = "a" * 40
    uuid = "GPU-exact"
    working_root = tmp_path / "worktrees" / "checkout"
    fake_file = working_root / "experiments" / "r4_residual_graph" / "calibration.py"
    output_root = (
        tmp_path
        / "MAP_orchestrator/r4-p0-20260828-ddd66f9/longrun/r4-perf8-oracle-step-probe"
        / f"{head}-reproduction11"
    )
    output_root.mkdir(parents=True)
    monkeypatch.setattr(calibration, "__file__", str(fake_file))
    for name, content in (
        ("process.json", b"{}"),
        ("progress.jsonl", b"{}\n"),
        ("stdout.log", b""),
        ("stderr.log", b""),
    ):
        calibration._write_once(output_root / name, content)
    identity = resource_watchdog.ProcessIdentity(
        pid=999_999,
        boot_id="boot",
        start_ticks="123",
        cmdline=("worker",),
    )
    result = resource_watchdog.WatchdogResult(
        status="FAIL",
        reason=reason,
        minimum_free_bytes=minimum_free_bytes,
        total_memory_bytes=16 << 30,
        elapsed_seconds=0.3,
        returncode=-9,
        child_identity=identity,
        ready_payload=None,
        phase_minimum_free_bytes={},
        cleanup_enforced=True,
        telemetry_anomalies=anomalies,
    )
    monkeypatch.setattr(calibration, "candidate_head", lambda root: head)
    monkeypatch.setattr(
        calibration.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout=f"{uuid}\n"),
    )
    monkeypatch.setattr(calibration, "nvidia_memory_bytes", lambda value: (16 << 30, 8 << 30))
    monkeypatch.setattr(calibration, "supervise", lambda *args, **kwargs: result)

    with pytest.raises(RuntimeError, match=reason):
        calibration._supervise_oracle_step_probe(output_root, attempt="reproduction11")

    terminal = json.loads((output_root / "terminal.json").read_bytes())
    resource = json.loads((output_root / "resource.txt").read_bytes())
    supervisor = json.loads((output_root / "supervisor.json").read_bytes())
    inventory = json.loads((output_root / "inventory.json").read_bytes())
    assert terminal["telemetry_anomalies"] == list(anomalies)
    assert resource == supervisor
    assert resource["gpu_uuid"] == uuid
    assert resource["minimum_free_bytes"] == minimum_free_bytes
    assert resource["child_returncode"] == -9
    assert resource["cleanup_enforced"] is True
    assert not Path("/proc/999999").exists()
    expected = {
        "process.json",
        "progress.jsonl",
        "stdout.log",
        "stderr.log",
        "resource.txt",
        "terminal.json",
        "supervisor.json",
    }
    assert set(inventory) == expected | {"inventory_identity"}
    for name in expected:
        assert inventory[name] == calibration._sha(output_root / name)
    unhashed = {name: inventory[name] for name in sorted(expected)}
    assert (
        inventory["inventory_identity"]
        == calibration.hashlib.sha256(calibration._canonical(unhashed)).hexdigest()
    )


@pytest.mark.parametrize("attempt", ("../escape", "Reproduction11", "a" * 33, ""))
def test_oracle_probe_rejects_invalid_attempt_identity(tmp_path: Path, attempt: str) -> None:
    with pytest.raises(ValueError, match="oracle-step probe attempt"):
        calibration._oracle_step_probe_root(tmp_path, "a" * 40, attempt)
