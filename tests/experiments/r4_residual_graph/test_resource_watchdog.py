"""Subprocess tests for the calibration resource watchdog."""

from __future__ import annotations

import io
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from experiments.r4_residual_graph import resource_watchdog

_TOTAL = 16 << 30
_HIGH = 8 << 30
_LOW = 3 << 30
_REAL_MONOTONIC = time.monotonic
_REAL_SLEEP = time.sleep


def _ready_command() -> tuple[str, ...]:
    code = (
        "import json,sys; print(json.dumps({'status':'READY'}),flush=True); "
        "json.loads(sys.stdin.readline())"
    )
    return (sys.executable, "-c", code)


def test_watchdog_normal_completion_passes_supervisor_minimum() -> None:
    result = resource_watchdog.supervise(
        _ready_command(),
        gpu_uuid="gpu",
        minimum_free_bytes=4 << 30,
        maximum_runtime_seconds=2.0,
        sample_interval_seconds=0.01,
        memory_sample=lambda uuid: (_TOTAL, _HIGH),
    )
    assert result.status == "PASS"
    assert result.minimum_free_bytes == _HIGH
    assert result.returncode == 0
    assert result.cleanup_enforced is False


def test_watchdog_drains_coalesced_phase_and_ready_records() -> None:
    code = (
        "import json,sys; "
        "messages=({'status':'PHASE','phase':'completed_receipt/ready'},"
        "{'status':'READY','receipt':'ok'}); "
        "sys.stdout.write(''.join(json.dumps(message)+'\\n' for message in messages)); "
        "sys.stdout.flush(); "
        "json.loads(sys.stdin.readline())"
    )
    result = resource_watchdog.supervise(
        (sys.executable, "-c", code),
        gpu_uuid="gpu",
        minimum_free_bytes=4 << 30,
        maximum_runtime_seconds=0.05,
        sample_interval_seconds=0.05,
        memory_sample=lambda uuid: (_TOTAL, _HIGH),
        completed_receipt_validator=lambda payload: (
            None
            if payload["receipt"] == "ok"
            else (_ for _ in ()).throw(RuntimeError("invalid receipt"))
        ),
    )

    assert result.status == "PASS"
    assert result.ready_payload == {"status": "READY", "receipt": "ok"}


def test_watchdog_preserves_partial_frames_without_stalling_samples() -> None:
    code = (
        "import json,sys,time; "
        "wire=json.dumps({'status':'PHASE','phase':'partial'})+'\\n'+"
        "json.dumps({'status':'READY'})+'\\n'; "
        "sys.stdout.write(wire[:12]); sys.stdout.flush(); time.sleep(.08); "
        "sys.stdout.write(wire[12:]); sys.stdout.flush(); "
        "json.loads(sys.stdin.readline())"
    )
    sample_count = 0

    def sample(uuid: str) -> tuple[int, int]:
        nonlocal sample_count
        sample_count += 1
        return _TOTAL, _HIGH

    result = resource_watchdog.supervise(
        (sys.executable, "-c", code),
        gpu_uuid="gpu",
        minimum_free_bytes=4 << 30,
        maximum_runtime_seconds=1.0,
        sample_interval_seconds=0.01,
        memory_sample=sample,
    )

    assert result.status == "PASS"
    assert sample_count >= 4
    assert result.ready_payload == {"status": "READY"}


def test_watchdog_terminates_gil_starved_child_on_floor_breach() -> None:
    samples = iter(((_TOTAL, _HIGH), (_TOTAL, _LOW)))
    code = "import hashlib; hashlib.pbkdf2_hmac('sha256',b'x',b'y',100000000)"
    result = resource_watchdog.supervise(
        (sys.executable, "-c", code),
        gpu_uuid="gpu",
        minimum_free_bytes=4 << 30,
        maximum_runtime_seconds=2.0,
        sample_interval_seconds=0.01,
        memory_sample=lambda uuid: next(samples),
    )
    assert result.status == "FAIL"
    assert result.reason == "calibration breached the total-device free-memory floor"
    assert result.returncode < 0


def test_watchdog_terminates_child_on_elapsed_cap() -> None:
    result = resource_watchdog.supervise(
        (sys.executable, "-c", "import time; time.sleep(10)"),
        gpu_uuid="gpu",
        minimum_free_bytes=4 << 30,
        maximum_runtime_seconds=0.05,
        sample_interval_seconds=0.01,
        memory_sample=lambda uuid: (_TOTAL, _HIGH),
    )
    assert result.status == "FAIL"
    assert result.reason == "calibration exceeded the maximum elapsed-runtime cap"
    assert result.elapsed_seconds >= 0.05


def test_identity_mismatch_refuses_to_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    identity = resource_watchdog.ProcessIdentity(123, "boot", "1", ("command",))
    monkeypatch.setattr(
        resource_watchdog,
        "process_identity",
        lambda pid: resource_watchdog.ProcessIdentity(pid, "boot", "2", ("replacement",)),
    )
    with pytest.raises(RuntimeError, match="refusing to signal"):
        resource_watchdog.authenticate(identity)


def test_nvidia_memory_requires_exact_gpu_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    completed = type(
        "Completed",
        (),
        {"returncode": 0, "stdout": "other, 16303, 8000\n", "stderr": ""},
    )()
    monkeypatch.setattr(resource_watchdog.subprocess, "run", lambda *args, **kwargs: completed)
    with pytest.raises(RuntimeError, match="exactly one authenticated GPU"):
        resource_watchdog.nvidia_memory_bytes("expected")


def test_watchdog_records_phase_local_minima() -> None:
    code = (
        "import json,sys,time; "
        "print(json.dumps({'status':'PHASE','phase':'oracle'}),flush=True); "
        "time.sleep(0.05); "
        "print(json.dumps({'status':'PHASE','phase':'production'}),flush=True); "
        "time.sleep(0.05); "
        "print(json.dumps({'status':'READY'}),flush=True); "
        "json.loads(sys.stdin.readline())"
    )
    free_values = iter((9, 8, 7, 6, 5, 5, 5, 5, 5, 5))

    def sample(uuid: str) -> tuple[int, int]:
        assert uuid == "gpu"
        return 10, next(free_values, 5)

    result = resource_watchdog.supervise(
        (sys.executable, "-c", code),
        gpu_uuid="gpu",
        minimum_free_bytes=1,
        maximum_runtime_seconds=2.0,
        sample_interval_seconds=0.01,
        memory_sample=sample,
    )

    assert result.phase_minimum_free_bytes["oracle"] <= 8
    assert result.phase_minimum_free_bytes["production"] <= 6


def test_authenticated_completed_receipt_hung_child_is_cleaned_up() -> None:
    code = (
        "import json,sys,time; "
        "print(json.dumps({'status':'READY','receipt':'ok'}),flush=True); "
        "json.loads(sys.stdin.readline()); time.sleep(60)"
    )
    result = resource_watchdog.supervise(
        (sys.executable, "-c", code),
        gpu_uuid="gpu",
        minimum_free_bytes=4 << 30,
        maximum_runtime_seconds=2.0,
        sample_interval_seconds=0.01,
        memory_sample=lambda uuid: (_TOTAL, _HIGH),
        completed_receipt_validator=lambda payload: (
            None
            if payload["receipt"] == "ok"
            else (_ for _ in ()).throw(RuntimeError("invalid receipt"))
        ),
        completed_exit_grace_seconds=0.02,
    )
    assert result.status == "PASS"
    assert result.cleanup_enforced is True
    assert result.returncode < 0


def test_watchdog_rejects_missing_or_invalid_completed_receipt() -> None:
    with pytest.raises(RuntimeError, match="without ready receipt"):
        resource_watchdog.supervise(
            (sys.executable, "-c", "pass"),
            gpu_uuid="gpu",
            minimum_free_bytes=4 << 30,
            maximum_runtime_seconds=2.0,
            sample_interval_seconds=0.01,
            memory_sample=lambda uuid: (_TOTAL, _HIGH),
        )

    code = "import json; print(json.dumps({'status':'READY'}),flush=True)"
    with pytest.raises(RuntimeError, match="stale completion handshake"):
        resource_watchdog.supervise(
            (sys.executable, "-c", code),
            gpu_uuid="gpu",
            minimum_free_bytes=4 << 30,
            maximum_runtime_seconds=2.0,
            sample_interval_seconds=0.01,
            memory_sample=lambda uuid: (_TOTAL, _HIGH),
            completed_receipt_validator=lambda payload: (_ for _ in ()).throw(
                RuntimeError("stale completion handshake")
            ),
        )


def test_completed_child_identity_mismatch_is_not_signalled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    code = (
        "import json,sys,time; "
        "print(json.dumps({'status':'READY'}),flush=True); "
        "json.loads(sys.stdin.readline()); time.sleep(.2)"
    )
    calls: list[resource_watchdog.ProcessIdentity] = []

    def reject(identity: resource_watchdog.ProcessIdentity) -> None:
        calls.append(identity)
        raise RuntimeError("identity changed; refusing to signal")

    monkeypatch.setattr(resource_watchdog, "authenticate", reject)
    with pytest.raises(RuntimeError, match="refusing to signal"):
        resource_watchdog.supervise(
            (sys.executable, "-c", code),
            gpu_uuid="gpu",
            minimum_free_bytes=4 << 30,
            maximum_runtime_seconds=2.0,
            sample_interval_seconds=0.01,
            memory_sample=lambda uuid: (_TOTAL, _HIGH),
            completed_receipt_validator=lambda payload: None,
            completed_exit_grace_seconds=0.0,
        )
    assert len(calls) == 1


def test_watchdog_accepts_ready_arriving_at_elapsed_boundary(
    identity_clock: _Clock,
) -> None:
    maximum_runtime_seconds = 0.2
    sample_interval_seconds = 0.1
    adjudication_time = maximum_runtime_seconds + sample_interval_seconds + 0.01

    def validate_completed_receipt(payload: dict[str, object]) -> None:
        assert payload == {"status": "READY", "receipt": "ok"}
        identity_clock.now = adjudication_time

    code = (
        "import json,sys; "
        "print(json.dumps({'status':'READY','receipt':'ok'}),flush=True); "
        "json.loads(sys.stdin.readline())"
    )
    result = resource_watchdog.supervise(
        (sys.executable, "-c", code),
        gpu_uuid="gpu",
        minimum_free_bytes=4 << 30,
        maximum_runtime_seconds=maximum_runtime_seconds,
        sample_interval_seconds=sample_interval_seconds,
        memory_sample=lambda uuid: (_TOTAL, _HIGH),
        completed_receipt_validator=validate_completed_receipt,
    )
    assert result.status == "PASS"
    assert result.ready_payload == {"status": "READY", "receipt": "ok"}
    assert result.elapsed_seconds == adjudication_time


def _retryable_failure(returncode: int = 3) -> resource_watchdog.TelemetrySampleError:
    return resource_watchdog.TelemetrySampleError(
        {
            "exception_class": "CalledProcessError",
            "returncode": returncode,
            "stdout": "bounded stdout",
            "stderr": "bounded stderr",
        }
    )


def test_one_shot_telemetry_failure_pauses_and_resumes_exact_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    samples: list[resource_watchdog.TelemetrySampleError | tuple[int, int]] = [
        _retryable_failure(),
        (_TOTAL, _HIGH),
    ]
    signals: list[int] = []
    real_kill = resource_watchdog.os.kill

    def sample(uuid: str) -> tuple[int, int]:
        value = samples.pop(0) if samples else (_TOTAL, _HIGH)
        if isinstance(value, BaseException):
            raise value
        return value

    def record_kill(pid: int, action: int) -> None:
        signals.append(action)
        real_kill(pid, action)

    monkeypatch.setattr(resource_watchdog.os, "kill", record_kill)
    result = resource_watchdog.supervise(
        _ready_command(),
        gpu_uuid="gpu",
        minimum_free_bytes=4 << 30,
        maximum_runtime_seconds=2.0,
        sample_interval_seconds=0.01,
        memory_sample=sample,
        expected_total_memory_bytes=_TOTAL,
    )

    assert result.status == "PASS"
    assert signals[:2] == [resource_watchdog.signal.SIGSTOP, resource_watchdog.signal.SIGCONT]
    assert len(result.telemetry_anomalies) == 1
    assert result.telemetry_anomalies[0]["returncode"] == 3
    assert result.minimum_free_bytes == _HIGH


def test_recovered_below_floor_is_resource_failure_without_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    samples: list[resource_watchdog.TelemetrySampleError | tuple[int, int]] = [
        _retryable_failure(),
        (_TOTAL, _LOW),
    ]
    signals: list[int] = []
    real_kill = resource_watchdog.os.kill

    def sample(uuid: str) -> tuple[int, int]:
        value = samples.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    def record_kill(pid: int, action: int) -> None:
        signals.append(action)
        real_kill(pid, action)

    monkeypatch.setattr(resource_watchdog.os, "kill", record_kill)
    result = resource_watchdog.supervise(
        (sys.executable, "-c", "import time; time.sleep(60)"),
        gpu_uuid="gpu",
        minimum_free_bytes=4 << 30,
        maximum_runtime_seconds=2.0,
        sample_interval_seconds=0.01,
        memory_sample=sample,
        expected_total_memory_bytes=_TOTAL,
    )

    assert result.status == "FAIL"
    assert result.reason == "calibration breached the total-device free-memory floor"
    assert result.minimum_free_bytes == _LOW
    assert signals == [resource_watchdog.signal.SIGSTOP, resource_watchdog.signal.SIGKILL]


@pytest.mark.parametrize(
    "failure",
    [
        _retryable_failure(),
        OSError("nvidia-smi unavailable"),
        resource_watchdog.subprocess.TimeoutExpired("nvidia-smi", 0.25),
    ],
)
def test_persistent_telemetry_outage_has_three_diagnostics_and_cleans_up(
    failure: BaseException,
) -> None:
    attempts = 0

    def sample(uuid: str) -> tuple[int, int]:
        nonlocal attempts
        attempts += 1
        raise failure

    result = resource_watchdog.supervise(
        (sys.executable, "-c", "import time; time.sleep(60)"),
        gpu_uuid="gpu",
        minimum_free_bytes=4 << 30,
        maximum_runtime_seconds=2.0,
        sample_interval_seconds=0.01,
        memory_sample=sample,
        expected_total_memory_bytes=_TOTAL,
    )

    assert result.status == "FAIL"
    assert result.reason == "external GPU telemetry unavailable"
    assert attempts == 3
    assert len(result.telemetry_anomalies) == 3
    assert result.minimum_free_bytes == 0
    assert result.cleanup_enforced is True
    assert result.returncode < 0


@pytest.mark.parametrize(
    ("sampled", "reason"),
    [
        (RuntimeError("malformed"), "GPU telemetry was malformed or unauthenticated"),
        ((_TOTAL + 1, _HIGH), "authenticated GPU total memory changed"),
    ],
)
def test_fatal_telemetry_fault_cleans_up(
    sampled: BaseException | tuple[int, int],
    reason: str,
) -> None:
    def sample(uuid: str) -> tuple[int, int]:
        if isinstance(sampled, BaseException):
            raise sampled
        return sampled

    result = resource_watchdog.supervise(
        (sys.executable, "-c", "import time; time.sleep(60)"),
        gpu_uuid="gpu",
        minimum_free_bytes=4 << 30,
        maximum_runtime_seconds=2.0,
        sample_interval_seconds=0.01,
        memory_sample=sample,
        expected_total_memory_bytes=_TOTAL,
    )

    assert result.status == "FAIL"
    assert result.reason == reason
    assert result.cleanup_enforced is True
    assert result.returncode < 0
    assert len(result.telemetry_anomalies) == 1


def test_nvidia_sampler_captures_bounded_returncode_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = type(
        "Completed",
        (),
        {"returncode": 3, "stdout": "x" * 3000, "stderr": "failure"},
    )()
    monkeypatch.setattr(resource_watchdog.subprocess, "run", lambda *args, **kwargs: completed)

    with pytest.raises(resource_watchdog.TelemetrySampleError) as caught:
        resource_watchdog.nvidia_memory_bytes("gpu")

    assert caught.value.diagnostic["returncode"] == 3
    assert caught.value.diagnostic["stderr"] == "failure"
    stdout = caught.value.diagnostic["stdout"]
    assert isinstance(stdout, str)
    assert len(stdout) == 2048


class _Pipe(io.BytesIO):
    def fileno(self) -> int:
        return 42


class _OwnedProcess:
    """Deterministic child: only the test's exit event makes it reapable."""

    def __init__(self) -> None:
        self.pid = 123
        self.stdin = _Pipe()
        self.stdout = _Pipe()
        self.stderr = _Pipe()
        self.returncode: int | None = None
        self.waits: list[float] = []
        self.communications: list[float] = []
        self.signals: list[int] = []
        self.ignore_term = False
        self.reaped = False

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float) -> int:
        self.waits.append(timeout)
        assert 0 < timeout <= 0.25
        if self.returncode is None:
            raise subprocess.TimeoutExpired("owned", timeout)
        self.reaped = True
        return self.returncode

    def communicate(self, timeout: float) -> tuple[bytes, bytes]:
        self.communications.append(timeout)
        self.wait(timeout)
        return b"", b""

    def kill(self, pid: int, action: int) -> None:
        assert pid == self.pid
        self.signals.append(action)
        if action == signal.SIGKILL or (action == signal.SIGTERM and not self.ignore_term):
            self.returncode = -action

    def as_popen(self) -> subprocess.Popen[bytes]:
        return cast("subprocess.Popen[bytes]", self)

    def assert_closed(self) -> None:
        assert self.stdin.closed and self.stdout.closed and self.stderr.closed
        assert self.reaped


class _Selector:
    def __init__(self) -> None:
        self.closed = False

    def register(self, *args: object) -> None:
        pass

    def select(self, timeout: float) -> list[tuple[SimpleNamespace, int]]:
        return [(SimpleNamespace(fd=42), 1)]

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def lifecycle(monkeypatch: pytest.MonkeyPatch) -> tuple[_OwnedProcess, _Selector]:
    child = _OwnedProcess()
    selector = _Selector()
    identity = resource_watchdog.ProcessIdentity(child.pid, "boot", "1", ("worker",))
    monkeypatch.setattr(resource_watchdog.subprocess, "Popen", lambda *args, **kwargs: child)
    monkeypatch.setattr(resource_watchdog, "process_identity", lambda pid: identity)
    monkeypatch.setattr(resource_watchdog.os, "kill", child.kill)
    monkeypatch.setattr(resource_watchdog.os, "set_blocking", lambda *args: None)
    monkeypatch.setattr(resource_watchdog.selectors, "DefaultSelector", lambda: selector)
    frames = iter((b'{"status":"READY"}\n', b""))
    monkeypatch.setattr(resource_watchdog.os, "read", lambda *args: next(frames))
    return child, selector


def _supervise_lifecycle(validator: Callable[[dict[str, object]], None]) -> None:
    resource_watchdog.supervise(
        ("worker",),
        gpu_uuid="gpu",
        minimum_free_bytes=4 << 30,
        maximum_runtime_seconds=2,
        sample_interval_seconds=0.01,
        memory_sample=lambda uuid: (_TOTAL, _HIGH),
        completed_receipt_validator=validator,
        completed_exit_grace_seconds=0,
    )


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        assert 0 < seconds <= resource_watchdog._IDENTITY_RETRY_SECONDS
        self.now += seconds


@pytest.fixture
def identity_clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    clock = _Clock()
    monkeypatch.setattr(resource_watchdog, "time", clock)
    return clock


def test_identity_clock_isolated_from_unrelated_sleeper(identity_clock: _Clock) -> None:
    assert time.monotonic is _REAL_MONOTONIC
    assert time.sleep is _REAL_SLEEP
    assert resource_watchdog.time is identity_clock
    resource_watchdog.time.sleep(resource_watchdog._IDENTITY_RETRY_SECONDS)
    before = identity_clock.now

    # Exercise an unrelated imported-module sleeper while the watchdog double is active.
    time.sleep(0.001)
    assert identity_clock.now == before
    assert resource_watchdog.time.monotonic() == before


def test_process_identity_parses_start_ticks_after_parenthesised_comm(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    real_read_text = Path.read_text
    real_read_bytes = Path.read_bytes
    unrelated = tmp_path / "unrelated"
    unrelated.write_text("real file")

    class IdentityPath:
        def __init__(self, path: str) -> None:
            self.path = path

        def read_text(self) -> str:
            if self.path == "/proc/sys/kernel/random/boot_id":
                return "boot\n"
            assert self.path == "/proc/123/stat"
            return "123 (worker ) with spaces) S " + " ".join(["0"] * 18) + " 987 0"

        def read_bytes(self) -> bytes:
            assert self.path == "/proc/123/cmdline"
            return b"worker\0argument\0"

    monkeypatch.setattr(resource_watchdog, "Path", IdentityPath)
    assert resource_watchdog.process_identity(123) == resource_watchdog.ProcessIdentity(
        123, "boot", "987", ("worker", "argument")
    )
    assert Path.read_text is real_read_text
    assert Path.read_bytes is real_read_bytes
    assert unrelated.read_text() == "real file"
    assert unrelated.read_bytes() == b"real file"


def test_identity_acquisition_waits_for_complete_repeated_observation(
    monkeypatch: pytest.MonkeyPatch, identity_clock: _Clock
) -> None:
    original = resource_watchdog.ProcessIdentity(123, "boot", "1", ("worker",))
    incomplete = replace(original, cmdline=())
    observations = iter((incomplete, incomplete, original, original))
    monkeypatch.setattr(resource_watchdog, "process_identity", lambda pid: next(observations))
    assert resource_watchdog._acquire_identity(123) == original
    assert 0 < identity_clock.now < resource_watchdog._IDENTITY_OBSERVATION_SECONDS


@pytest.mark.parametrize("incomplete", [True, False])
def test_identity_acquisition_expiry_never_establishes_ownership(
    lifecycle: tuple[_OwnedProcess, _Selector],
    monkeypatch: pytest.MonkeyPatch,
    identity_clock: _Clock,
    incomplete: bool,
) -> None:
    child, _ = lifecycle
    count = 0

    def observe(pid: int) -> resource_watchdog.ProcessIdentity:
        nonlocal count
        count += 1
        return resource_watchdog.ProcessIdentity(
            pid, "boot", "1", () if incomplete else (f"changing-{count}",)
        )

    monkeypatch.setattr(resource_watchdog, "process_identity", observe)
    with pytest.raises(ExceptionGroup) as caught:
        _supervise_lifecycle(lambda payload: None)
    assert "initial identity acquisition expired" in str(caught.value.exceptions[0])
    assert len(caught.value.exceptions) == 4
    assert child.signals == []
    assert child.stdin.closed and child.stdout.closed and child.stderr.closed
    assert identity_clock.now == resource_watchdog._IDENTITY_OBSERVATION_SECONDS
    assert count <= 13


@pytest.mark.parametrize("action", [signal.SIGSTOP, signal.SIGCONT, signal.SIGTERM, signal.SIGKILL])
def test_identity_resampling_signals_only_after_original_match(
    lifecycle: tuple[_OwnedProcess, _Selector],
    monkeypatch: pytest.MonkeyPatch,
    identity_clock: _Clock,
    action: int,
) -> None:
    child, _ = lifecycle
    original = resource_watchdog.ProcessIdentity(child.pid, "boot", "1", ("worker",))
    observations = iter((replace(original, cmdline=()), original))
    count = 0

    def observe(pid: int) -> resource_watchdog.ProcessIdentity:
        nonlocal count
        assert child.signals == []
        count += 1
        return next(observations)

    monkeypatch.setattr(resource_watchdog, "process_identity", observe)
    assert resource_watchdog._signal_owned(child.as_popen(), original, action)
    assert count == 2
    assert child.signals == [action]
    assert original.cmdline == ("worker",)


@pytest.mark.parametrize("field", ["pid", "boot_id", "start_ticks", "cmdline"])
def test_identity_resampling_persistent_mismatch_refuses_live_child(
    lifecycle: tuple[_OwnedProcess, _Selector],
    monkeypatch: pytest.MonkeyPatch,
    identity_clock: _Clock,
    field: str,
) -> None:
    child, _ = lifecycle
    original = resource_watchdog.ProcessIdentity(child.pid, "boot", "1", ("worker",))
    changed = resource_watchdog.ProcessIdentity(
        456 if field == "pid" else child.pid,
        "other-boot" if field == "boot_id" else "boot",
        "2" if field == "start_ticks" else "1",
        ("secret-replacement",) if field == "cmdline" else ("worker",),
    )
    count = 0

    def observe(pid: int) -> resource_watchdog.ProcessIdentity:
        nonlocal count
        count += 1
        return changed

    monkeypatch.setattr(resource_watchdog, "process_identity", observe)
    with pytest.raises(RuntimeError, match="refusing to signal") as caught:
        resource_watchdog._signal_owned(child.as_popen(), original, signal.SIGTERM)
    assert "secret-replacement" not in str(caught.value)
    assert child.signals == []
    assert child.returncode is None
    assert identity_clock.now == resource_watchdog._IDENTITY_OBSERVATION_SECONDS
    assert 2 <= count <= 12


@pytest.mark.parametrize("read_error", [False, True])
def test_identity_exit_during_resampling_preserves_primary_and_reaps(
    lifecycle: tuple[_OwnedProcess, _Selector],
    monkeypatch: pytest.MonkeyPatch,
    identity_clock: _Clock,
    read_error: bool,
) -> None:
    child, selector = lifecycle
    primary = ValueError("invalid receipt")
    observations = 0

    def observe(pid: int) -> resource_watchdog.ProcessIdentity:
        nonlocal observations
        observations += 1
        if observations == 2:
            child.returncode = 0
            if read_error:
                raise FileNotFoundError("owned child exited")
        return resource_watchdog.ProcessIdentity(pid, "boot", "1", ())

    def reject(payload: dict[str, object]) -> None:
        monkeypatch.setattr(resource_watchdog, "process_identity", observe)
        raise primary

    with pytest.raises(ValueError) as caught:
        _supervise_lifecycle(reject)
    assert caught.value is primary
    assert observations >= 2
    assert child.signals == []
    child.assert_closed()
    assert selector.closed


def test_identity_resampling_rejects_match_observed_after_deadline(
    lifecycle: tuple[_OwnedProcess, _Selector],
    monkeypatch: pytest.MonkeyPatch,
    identity_clock: _Clock,
) -> None:
    child, _ = lifecycle
    original = resource_watchdog.ProcessIdentity(child.pid, "boot", "1", ("worker",))

    def observe(pid: int) -> resource_watchdog.ProcessIdentity:
        identity_clock.now += resource_watchdog._IDENTITY_OBSERVATION_SECONDS
        return original

    monkeypatch.setattr(resource_watchdog, "process_identity", observe)
    with pytest.raises(RuntimeError, match="observation expired"):
        resource_watchdog._signal_owned(child.as_popen(), original, signal.SIGTERM)
    assert child.signals == []


@pytest.mark.parametrize("seam", ["authentication", "signal"])
def test_lifecycle_exit_race_preserves_validator_error(
    lifecycle: tuple[_OwnedProcess, _Selector], monkeypatch: pytest.MonkeyPatch, seam: str
) -> None:
    child, selector = lifecycle
    primary = RuntimeError("stale completion handshake")

    def exit_at_authentication(identity: resource_watchdog.ProcessIdentity) -> None:
        child.returncode = 0
        raise RuntimeError("identity changed; refusing to signal")

    def exit_at_signal(pid: int, action: int) -> None:
        child.returncode = 0
        raise ProcessLookupError("already exited")

    def reject(payload: dict[str, object]) -> None:
        if seam == "authentication":
            monkeypatch.setattr(resource_watchdog, "authenticate", exit_at_authentication)
        else:
            monkeypatch.setattr(resource_watchdog.os, "kill", exit_at_signal)
        raise primary

    with pytest.raises(RuntimeError) as caught:
        _supervise_lifecycle(reject)
    assert caught.value is primary
    assert child.signals == []
    child.assert_closed()
    assert selector.closed


@pytest.mark.parametrize("seam", ["authentication", "signal"])
def test_lifecycle_live_identity_or_signal_failure_is_retained(
    lifecycle: tuple[_OwnedProcess, _Selector], monkeypatch: pytest.MonkeyPatch, seam: str
) -> None:
    child, selector = lifecycle
    primary = ValueError("invalid receipt")
    cleanup_error = (
        RuntimeError("identity changed") if seam == "authentication" else ProcessLookupError()
    )

    def fail(*args: object) -> None:
        raise cleanup_error

    def reject(payload: dict[str, object]) -> None:
        if seam == "authentication":
            monkeypatch.setattr(resource_watchdog, "authenticate", fail)
        else:
            monkeypatch.setattr(resource_watchdog.os, "kill", fail)
        raise primary

    with pytest.raises(ExceptionGroup) as caught:
        _supervise_lifecycle(reject)
    assert caught.value.exceptions[:2] == (primary, cleanup_error)
    assert all(isinstance(exc, subprocess.TimeoutExpired) for exc in caught.value.exceptions[2:])
    assert len(caught.value.exceptions) == 4
    assert child.signals == []
    assert child.stdin.closed and child.stdout.closed and child.stderr.closed and selector.closed
    assert not child.reaped


def test_lifecycle_ack_write_failure_escalates_with_fresh_authentication(
    lifecycle: tuple[_OwnedProcess, _Selector], monkeypatch: pytest.MonkeyPatch
) -> None:
    child, selector = lifecycle
    primary = BrokenPipeError("ack write failed")
    authentications: list[resource_watchdog.ProcessIdentity] = []
    child.ignore_term = True

    def fail_write(value: bytes) -> int:
        raise primary

    monkeypatch.setattr(child.stdin, "write", fail_write)
    monkeypatch.setattr(resource_watchdog, "authenticate", authentications.append)
    with pytest.raises(BrokenPipeError) as caught:
        _supervise_lifecycle(lambda payload: None)
    assert caught.value is primary
    assert child.signals == [signal.SIGTERM, signal.SIGKILL]
    assert len(authentications) == 2
    assert child.communications == [0.25]
    child.assert_closed()
    assert selector.closed


@pytest.mark.parametrize("boundary", [signal.SIGTERM, signal.SIGKILL])
@pytest.mark.parametrize("seam", ["authentication", "signal"])
def test_lifecycle_sampler_exit_at_signal_boundary_closes_and_reaps(
    lifecycle: tuple[_OwnedProcess, _Selector],
    monkeypatch: pytest.MonkeyPatch,
    boundary: int,
    seam: str,
) -> None:
    child, selector = lifecycle
    identity = resource_watchdog.ProcessIdentity(
        child.pid, "boot", "1", resource_watchdog._SAMPLER_COMMAND
    )
    monkeypatch.setattr(resource_watchdog, "process_identity", lambda pid: identity)
    sampler = resource_watchdog.PersistentNvidiaSampler("gpu")
    child.ignore_term = boundary == signal.SIGKILL
    authentications = 0

    def authenticate(identity: resource_watchdog.ProcessIdentity) -> None:
        nonlocal authentications
        authentications += 1
        if seam == "authentication" and authentications == (1 if boundary == signal.SIGTERM else 2):
            child.returncode = 0
            raise RuntimeError("identity changed")

    def kill(pid: int, action: int) -> None:
        if seam == "signal" and action == boundary:
            child.returncode = 0
            raise ProcessLookupError()
        child.kill(pid, action)

    monkeypatch.setattr(resource_watchdog, "authenticate", authenticate)
    monkeypatch.setattr(resource_watchdog.os, "kill", kill)
    sampler.terminate()
    child.assert_closed()
    assert selector.closed
    assert child.signals == ([] if boundary == signal.SIGTERM else [signal.SIGTERM])


def test_lifecycle_sampler_argv_mismatch_before_selector_setup(
    lifecycle: tuple[_OwnedProcess, _Selector],
) -> None:
    child, selector = lifecycle
    with pytest.raises(RuntimeError, match="sampler argv identity mismatch"):
        resource_watchdog.PersistentNvidiaSampler("gpu")
    child.assert_closed()
    assert not selector.closed  # Never acquired.
    assert child.signals == [signal.SIGTERM]


@pytest.mark.parametrize("seam", ["launch", "identity", "selector", "register"])
def test_lifecycle_worker_partial_setup_closes_acquired_resources(
    lifecycle: tuple[_OwnedProcess, _Selector], monkeypatch: pytest.MonkeyPatch, seam: str
) -> None:
    child, selector = lifecycle
    primary = OSError(f"{seam} failed")
    sampler_closed: list[bool] = []
    sampler = SimpleNamespace(
        identity=resource_watchdog.ProcessIdentity(456, "boot", "2", ("sampler",)),
        sample=lambda: resource_watchdog.TelemetrySample(1, 1, _TOTAL, _HIGH),
        terminate=lambda: sampler_closed.append(True),
    )
    monkeypatch.setattr(resource_watchdog, "PersistentNvidiaSampler", lambda uuid: sampler)

    def fail(*args: object, **kwargs: object) -> None:
        if seam == "identity":
            child.returncode = 0
        raise primary

    if seam == "launch":
        monkeypatch.setattr(resource_watchdog.subprocess, "Popen", fail)
    elif seam == "identity":
        monkeypatch.setattr(resource_watchdog, "process_identity", fail)
    elif seam == "selector":
        monkeypatch.setattr(resource_watchdog.selectors, "DefaultSelector", fail)
    else:
        monkeypatch.setattr(selector, "register", fail)
    with pytest.raises(OSError) as caught:
        resource_watchdog.supervise(
            ("worker",),
            gpu_uuid="gpu",
            minimum_free_bytes=4 << 30,
            maximum_runtime_seconds=2,
            sample_interval_seconds=0.01,
        )
    assert caught.value is primary
    assert sampler_closed == [True]
    assert selector.closed == (seam == "register")
    if seam != "launch":
        child.assert_closed()
    assert child.signals == ([signal.SIGTERM] if seam in {"selector", "register"} else [])


def test_lifecycle_independent_cleanup_failures_do_not_skip_resources(
    lifecycle: tuple[_OwnedProcess, _Selector], monkeypatch: pytest.MonkeyPatch
) -> None:
    child, selector = lifecycle
    primary = ValueError("validator failed")
    selector_error = OSError("selector close failed")
    pipe_error = OSError("pipe close failed")
    sampler_error = OSError("sampler cleanup failed")
    sampler_closed: list[bool] = []

    def close_sampler() -> None:
        sampler_closed.append(True)
        raise sampler_error

    sampler = SimpleNamespace(
        identity=resource_watchdog.ProcessIdentity(456, "boot", "2", ("sampler",)),
        sample=lambda: resource_watchdog.TelemetrySample(1, 1, _TOTAL, _HIGH),
        terminate=close_sampler,
    )
    monkeypatch.setattr(resource_watchdog, "PersistentNvidiaSampler", lambda uuid: sampler)

    def close_selector() -> None:
        selector.closed = True
        raise selector_error

    def close_pipe() -> None:
        io.BytesIO.close(child.stderr)
        raise pipe_error

    def reject(payload: dict[str, object]) -> None:
        raise primary

    monkeypatch.setattr(selector, "close", close_selector)
    monkeypatch.setattr(child.stderr, "close", close_pipe)
    with pytest.raises(ExceptionGroup) as caught:
        resource_watchdog.supervise(
            ("worker",),
            gpu_uuid="gpu",
            minimum_free_bytes=4 << 30,
            maximum_runtime_seconds=2,
            sample_interval_seconds=0.01,
            completed_receipt_validator=reject,
        )
    assert caught.value.exceptions == (primary, selector_error, pipe_error, sampler_error)
    child.assert_closed()
    assert selector.closed
    assert sampler_closed == [True]


@pytest.mark.parametrize("seam", ["identity", "blocking", "selector", "register"])
def test_lifecycle_sampler_partial_setup_cleans_up(
    lifecycle: tuple[_OwnedProcess, _Selector], monkeypatch: pytest.MonkeyPatch, seam: str
) -> None:
    child, selector = lifecycle
    primary = OSError(f"{seam} failed")
    identity = resource_watchdog.ProcessIdentity(
        child.pid, "boot", "1", resource_watchdog._SAMPLER_COMMAND
    )
    monkeypatch.setattr(resource_watchdog, "process_identity", lambda pid: identity)

    def fail(*args: object) -> None:
        if seam == "identity":
            child.returncode = 0
        raise primary

    if seam == "identity":
        monkeypatch.setattr(resource_watchdog, "process_identity", fail)
    elif seam == "blocking":
        monkeypatch.setattr(resource_watchdog.os, "set_blocking", fail)
    elif seam == "selector":
        monkeypatch.setattr(resource_watchdog.selectors, "DefaultSelector", fail)
    else:
        monkeypatch.setattr(selector, "register", fail)
    with pytest.raises(OSError) as caught:
        resource_watchdog.PersistentNvidiaSampler("gpu")
    assert caught.value is primary
    child.assert_closed()
    assert selector.closed == (seam == "register")
    assert child.signals == ([] if seam == "identity" else [signal.SIGTERM])


def test_lifecycle_failed_initial_identity_never_signals_live_child(
    lifecycle: tuple[_OwnedProcess, _Selector], monkeypatch: pytest.MonkeyPatch
) -> None:
    child, _ = lifecycle
    primary = OSError("identity read failed")

    def fail(pid: int) -> resource_watchdog.ProcessIdentity:
        raise primary

    monkeypatch.setattr(resource_watchdog, "process_identity", fail)
    with pytest.raises(ExceptionGroup) as caught:
        _supervise_lifecycle(lambda payload: None)
    assert caught.value.exceptions[0] is primary
    assert len(caught.value.exceptions) == 4
    assert all(isinstance(exc, subprocess.TimeoutExpired) for exc in caught.value.exceptions[1:])
    assert child.signals == []
    assert child.stdin.closed and child.stdout.closed and child.stderr.closed


def test_lifecycle_kill_timeout_is_bounded_and_retains_primary(
    lifecycle: tuple[_OwnedProcess, _Selector], monkeypatch: pytest.MonkeyPatch
) -> None:
    child, selector = lifecycle
    primary = ValueError("validator failed")
    authentications: list[resource_watchdog.ProcessIdentity] = []

    def ignore_signal(pid: int, action: int) -> None:
        child.signals.append(action)

    def reject(payload: dict[str, object]) -> None:
        raise primary

    monkeypatch.setattr(resource_watchdog.os, "kill", ignore_signal)
    monkeypatch.setattr(resource_watchdog, "authenticate", authentications.append)
    with pytest.raises(ExceptionGroup) as caught:
        _supervise_lifecycle(reject)
    assert caught.value.exceptions[0] is primary
    assert len(caught.value.exceptions) == 4
    assert child.signals == [signal.SIGTERM, signal.SIGKILL]
    assert len(authentications) == 2
    assert child.communications == [0.25]
    assert child.stdin.closed and child.stdout.closed and child.stderr.closed and selector.closed


def test_lifecycle_changed_identity_at_kill_escalation_is_not_signalled(
    lifecycle: tuple[_OwnedProcess, _Selector], monkeypatch: pytest.MonkeyPatch
) -> None:
    child, selector = lifecycle
    child.ignore_term = True
    primary = ValueError("validator failed")
    cleanup_error = RuntimeError("identity changed; refusing to signal")
    authentications = 0

    def authenticate(identity: resource_watchdog.ProcessIdentity) -> None:
        nonlocal authentications
        authentications += 1
        if authentications == 2:
            raise cleanup_error

    def reject(payload: dict[str, object]) -> None:
        raise primary

    monkeypatch.setattr(resource_watchdog, "authenticate", authenticate)
    with pytest.raises(ExceptionGroup) as caught:
        _supervise_lifecycle(reject)
    assert caught.value.exceptions[:2] == (primary, cleanup_error)
    assert child.signals == [signal.SIGTERM]
    assert authentications == 2
    assert child.stdin.closed and child.stdout.closed and child.stderr.closed and selector.closed
