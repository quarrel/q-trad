"""Independent authenticated subprocess watchdog for CUDA calibration."""

from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import time
from collections import deque
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    boot_id: str
    start_ticks: str
    cmdline: tuple[str, ...]


def process_identity(pid: int) -> ProcessIdentity:
    boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    # comm is parenthesised and may itself contain spaces or closing parentheses.
    start_ticks = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    cmdline = tuple(
        part.decode() for part in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0") if part
    )
    return ProcessIdentity(pid=pid, boot_id=boot_id, start_ticks=start_ticks, cmdline=cmdline)


_IDENTITY_OBSERVATION_SECONDS = 0.05
_IDENTITY_RETRY_SECONDS = 0.005


def _acquire_identity(pid: int) -> ProcessIdentity:
    """Establish a complete, repeated initial observation before taking ownership."""
    deadline = time.monotonic() + _IDENTITY_OBSERVATION_SECONDS
    previous = process_identity(pid)
    while True:
        observed = process_identity(pid)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("watchdog child initial identity acquisition expired")
        if observed == previous and observed.boot_id and observed.start_ticks and observed.cmdline:
            return observed
        previous = observed
        time.sleep(min(_IDENTITY_RETRY_SECONDS, remaining))


def authenticate(identity: ProcessIdentity) -> None:
    """Re-observe only the original identity; a replacement never grants ownership."""
    deadline = time.monotonic() + _IDENTITY_OBSERVATION_SECONDS
    observations = 0
    while True:
        observed = process_identity(identity.pid)
        observations += 1
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(
                "watchdog child process identity changed or observation expired; "
                f"refusing to signal after {observations} observations"
            )
        if observed == identity:
            return
        time.sleep(min(_IDENTITY_RETRY_SECONDS, remaining))


_SAMPLER_OUTPUT_LIMIT = 2048
_SAMPLER_TIMEOUT_SECONDS = 0.25
_MAX_TELEMETRY_RECOVERY_SECONDS = 1.0
_SAMPLER_COMMAND = (
    "nvidia-smi",
    "--query-gpu=uuid,memory.total,memory.free",
    "--format=csv,noheader,nounits",
    "--loop-ms=50",
)


class TelemetrySampleError(RuntimeError):
    """Retryable external telemetry command failure with bounded diagnostics."""

    def __init__(self, diagnostic: dict[str, object]) -> None:
        super().__init__("external GPU telemetry sampler failed")
        self.diagnostic = diagnostic


def _bounded_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    text = value.decode(errors="replace") if isinstance(value, bytes) else value
    return text[:_SAMPLER_OUTPUT_LIMIT]


def nvidia_memory_bytes(expected_uuid: str) -> tuple[int, int]:
    try:
        completed = subprocess.run(
            (
                "nvidia-smi",
                "--query-gpu=uuid,memory.total,memory.free",
                "--format=csv,noheader,nounits",
            ),
            check=False,
            capture_output=True,
            text=True,
            timeout=_SAMPLER_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TelemetrySampleError(
            {
                "exception_class": type(exc).__name__,
                "returncode": None,
                "stdout": _bounded_output(getattr(exc, "stdout", None)),
                "stderr": _bounded_output(getattr(exc, "stderr", None)),
            }
        ) from exc
    if completed.returncode != 0:
        raise TelemetrySampleError(
            {
                "exception_class": "CalledProcessError",
                "returncode": completed.returncode,
                "stdout": _bounded_output(completed.stdout),
                "stderr": _bounded_output(completed.stderr),
            }
        )
    rows = [row.split(", ") for row in completed.stdout.splitlines()]
    if not rows or any(len(row) != 3 for row in rows):
        raise RuntimeError("GPU telemetry output was missing or malformed")
    matches = [row for row in rows if row[0] == expected_uuid]
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one authenticated GPU row for {expected_uuid}")
    _, total_mib, free_mib = matches[0]
    try:
        return int(total_mib) * 1024**2, int(free_mib) * 1024**2
    except ValueError as exc:
        raise RuntimeError("authenticated GPU telemetry row was malformed") from exc


@dataclass(frozen=True)
class TelemetrySample:
    sequence: int
    received_monotonic_ns: int
    total_bytes: int
    free_bytes: int


@contextmanager
def _cleanup() -> Iterator[list[Callable[[], object]]]:
    """Attempt every owned cleanup action, retaining independent failures."""
    actions: list[Callable[[], object]] = []
    primary: BaseException | None = None
    try:
        yield actions
    except BaseException as exc:
        primary = exc
        raise
    finally:
        errors: list[BaseException] = []
        for action in reversed(actions):
            try:
                action()
            except BaseException as exc:
                errors.append(exc)
        if errors:
            if primary is not None:
                errors.insert(0, primary)
            if len(errors) == 1:
                raise errors[0]
            raise BaseExceptionGroup("watchdog operation and cleanup failures", errors)


def _signal_owned(
    process: subprocess.Popen[bytes], identity: ProcessIdentity | None, action: int
) -> bool:
    if process.poll() is not None:
        process.wait(timeout=0.25)
        return False
    try:
        if identity is None:
            raise RuntimeError("watchdog child identity unavailable; refusing to signal")
        authenticate(identity)
        os.kill(identity.pid, action)
    except (RuntimeError, OSError):
        # Only the exact owned Popen can establish that this is an exit race.
        if process.poll() is None:
            raise
        process.wait(timeout=0.25)
        return False
    return True


def _terminate_owned(
    process: subprocess.Popen[bytes], identity: ProcessIdentity | None, *, paused: bool = False
) -> bool:
    if identity is None:
        # Failed initial authentication permits only a bounded natural exit.
        process.wait(timeout=0.25)
        return False
    signalled = _signal_owned(process, identity, signal.SIGKILL if paused else signal.SIGTERM)
    try:
        process.wait(timeout=0.25)
    except subprocess.TimeoutExpired:
        signalled = _signal_owned(process, identity, signal.SIGKILL) or signalled
        process.wait(timeout=0.25)
    return signalled


def _register_process_cleanup(
    actions: list[Callable[[], object]], process: subprocess.Popen[bytes]
) -> None:
    # Register separately so a failed close cannot skip another pipe or the reap.
    actions.append(lambda: process.wait(timeout=0.25))
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None:
            actions.append(stream.close)


class PersistentNvidiaSampler:
    """One authenticated, non-blocking nvidia-smi stream."""

    def __init__(self, expected_uuid: str) -> None:
        self.expected_uuid = expected_uuid
        self.process = subprocess.Popen(
            _SAMPLER_COMMAND,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        self.identity: ProcessIdentity
        self._selector: selectors.BaseSelector
        identity: ProcessIdentity | None = None
        with _cleanup() as actions:
            _register_process_cleanup(actions, self.process)
            actions.append(lambda: _terminate_owned(self.process, identity))
            identity = _acquire_identity(self.process.pid)
            self.identity = identity
            if self.identity.cmdline != _SAMPLER_COMMAND:
                raise RuntimeError("GPU telemetry sampler argv identity mismatch")
            assert self.process.stdout is not None
            assert self.process.stderr is not None
            os.set_blocking(self.process.stdout.fileno(), False)
            os.set_blocking(self.process.stderr.fileno(), False)
            self._selector = selectors.DefaultSelector()
            actions.append(self._selector.close)
            self._selector.register(self.process.stdout, selectors.EVENT_READ, "stdout")
            self._selector.register(self.process.stderr, selectors.EVENT_READ, "stderr")
            self._stdout = bytearray()
            self._stderr = bytearray()
            self._samples: deque[TelemetrySample] = deque()
            self._sequence = 0
            actions.clear()  # Ownership transfers to the fully initialised sampler.

    def _parse_line(self, raw: bytes) -> None:
        try:
            fields = raw.decode("ascii").split(", ")
        except UnicodeDecodeError as exc:
            raise RuntimeError("GPU telemetry output was malformed") from exc
        if len(fields) != 3 or fields[0] != self.expected_uuid:
            raise RuntimeError("GPU telemetry row was malformed or unauthenticated")
        try:
            total_bytes = int(fields[1]) * 1024**2
            free_bytes = int(fields[2]) * 1024**2
        except ValueError as exc:
            raise RuntimeError("authenticated GPU telemetry row was malformed") from exc
        self._sequence += 1
        self._samples.append(
            TelemetrySample(self._sequence, time.monotonic_ns(), total_bytes, free_bytes)
        )

    def sample(self, timeout: float = _SAMPLER_TIMEOUT_SECONDS) -> TelemetrySample:
        if self._samples:
            return self._samples.popleft()
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TelemetrySampleError(self.diagnostic("TelemetryStall"))
            events = self._selector.select(remaining)
            if not events:
                raise TelemetrySampleError(self.diagnostic("TelemetryStall"))
            for key, _mask in events:
                try:
                    chunk = os.read(key.fd, 65536)
                except BlockingIOError:
                    continue
                if key.data == "stderr":
                    self._stderr.extend(chunk)
                    if len(self._stderr) > _SAMPLER_OUTPUT_LIMIT:
                        del self._stderr[:-_SAMPLER_OUTPUT_LIMIT]
                    continue
                if not chunk:
                    continue
                self._stdout.extend(chunk)
                while b"\n" in self._stdout:
                    raw, _, rest = self._stdout.partition(b"\n")
                    self._stdout = bytearray(rest)
                    if not raw:
                        raise RuntimeError("GPU telemetry output was malformed")
                    self._parse_line(bytes(raw))
            if self._samples:
                return self._samples.popleft()
            returncode = self.process.poll()
            if returncode is not None:
                raise TelemetrySampleError(self.diagnostic("SamplerExit"))

    def diagnostic(self, exception_class: str) -> dict[str, object]:
        return {
            "exception_class": exception_class,
            "returncode": self.process.poll(),
            "stdout": _bounded_output(bytes(self._stdout)),
            "stderr": _bounded_output(bytes(self._stderr)),
            "sampler_identity": {
                "pid": self.identity.pid,
                "boot_id": self.identity.boot_id,
                "start_ticks": self.identity.start_ticks,
                "cmdline": list(self.identity.cmdline),
            },
        }

    def terminate(self) -> None:
        with _cleanup() as actions:
            _register_process_cleanup(actions, self.process)
            actions.append(lambda: _terminate_owned(self.process, self.identity))
            actions.append(self._selector.close)


@dataclass(frozen=True)
class WatchdogResult:
    status: str
    reason: str | None
    minimum_free_bytes: int
    total_memory_bytes: int
    elapsed_seconds: float
    returncode: int
    child_identity: ProcessIdentity
    ready_payload: dict[str, object] | None
    phase_minimum_free_bytes: dict[str, int]
    cleanup_enforced: bool
    telemetry_anomalies: tuple[dict[str, object], ...]
    sampler_identities: tuple[ProcessIdentity, ...] = ()
    telemetry_samples: tuple[TelemetrySample, ...] = ()

    def telemetry_evidence(self) -> dict[str, object]:
        return {
            "sampler_identities": [
                {
                    "pid": identity.pid,
                    "boot_id": identity.boot_id,
                    "start_ticks": identity.start_ticks,
                    "cmdline": list(identity.cmdline),
                }
                for identity in self.sampler_identities
            ],
            "telemetry_samples": [
                {
                    "sequence": sample.sequence,
                    "received_monotonic_ns": sample.received_monotonic_ns,
                    "total_bytes": sample.total_bytes,
                    "free_bytes": sample.free_bytes,
                }
                for sample in self.telemetry_samples
            ],
        }


def supervise(
    command: Sequence[str],
    *,
    gpu_uuid: str,
    minimum_free_bytes: int,
    maximum_runtime_seconds: float,
    sample_interval_seconds: float,
    memory_sample: Callable[[str], tuple[int, int]] | None = None,
    cwd: Path | None = None,
    completed_receipt_validator: Callable[[dict[str, object]], None] | None = None,
    completed_exit_grace_seconds: float = 10.0,
    expected_total_memory_bytes: int | None = None,
) -> WatchdogResult:
    if not 0 <= completed_exit_grace_seconds <= 60:
        raise ValueError("completed receipt exit grace must be between zero and 60 seconds")
    started = time.monotonic()
    with _cleanup() as actions:
        sampler = None if memory_sample is not None else PersistentNvidiaSampler(gpu_uuid)
        sampler_identities: list[ProcessIdentity] = []
        telemetry_samples: list[TelemetrySample] = []
        startup_sample: TelemetrySample | None = None
        if sampler is not None:
            sampler_identities.append(sampler.identity)
            actions.append(sampler.terminate)
            startup_sample = sampler.sample()
            telemetry_samples.append(startup_sample)
            if (
                expected_total_memory_bytes is not None
                and startup_sample.total_bytes != expected_total_memory_bytes
            ):
                raise RuntimeError("authenticated GPU total memory changed")
            if startup_sample.free_bytes < minimum_free_bytes:
                raise RuntimeError("calibration breached the total-device free-memory floor")
        child = subprocess.Popen(
            command,
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        identity: ProcessIdentity | None = None
        paused = False
        cleanup_enforced = False
        stdout = b""
        stderr = b""

        def communicate_child() -> None:
            nonlocal stdout, stderr
            stdout, stderr = child.communicate(timeout=0.25)

        def terminate_child() -> None:
            nonlocal cleanup_enforced
            cleanup_enforced = _terminate_owned(child, identity, paused=paused) or cleanup_enforced

        _register_process_cleanup(actions, child)
        actions.append(communicate_child)
        actions.append(terminate_child)
        identity = _acquire_identity(child.pid)
        assert child.stdout is not None
        assert child.stdin is not None
        child_stdin = child.stdin
        os.set_blocking(child.stdout.fileno(), False)
        selector = selectors.DefaultSelector()
        actions.append(selector.close)
        selector.register(child.stdout.fileno(), selectors.EVENT_READ)
        stdout_buffer = bytearray()
        observed_minimum: int | None = (
            startup_sample.free_bytes if startup_sample is not None else None
        )
        total_memory = (
            startup_sample.total_bytes
            if startup_sample is not None
            else expected_total_memory_bytes
        )
        ready_payload: dict[str, object] | None = None
        phase = "startup"
        phase_minimum_free_bytes: dict[str, int] = {}
        reason: str | None = None
        completed_receipt = False
        completed_deadline: float | None = None
        telemetry_anomalies: list[dict[str, object]] = []

        def handle_record(raw: bytes) -> None:
            nonlocal completed_deadline, completed_receipt, phase, ready_payload
            payload = json.loads(raw)
            status = payload["status"]
            if status == "PHASE":
                phase_value = payload["phase"]
                if not isinstance(phase_value, str) or not phase_value:
                    raise RuntimeError("calibration worker emitted an invalid phase")
                phase = phase_value
            elif status == "READY":
                if completed_receipt_validator is not None:
                    completed_receipt_validator(payload)
                ready_payload = payload
                completed_receipt = True
                completed_deadline = time.monotonic() + completed_exit_grace_seconds
                phase = "completed_receipt_cleanup"
                child_stdin.write(
                    (json.dumps({"minimum_free_bytes": observed_minimum}) + "\n").encode()
                )
                child_stdin.flush()
            else:
                raise RuntimeError("calibration worker emitted an unexpected supervisor message")

        def signal_child(action: int) -> None:
            _signal_owned(child, identity, action)

        def diagnostic(exc: BaseException, attempt: int) -> dict[str, object]:
            detail = (
                dict(exc.diagnostic)
                if isinstance(exc, TelemetrySampleError)
                else {
                    "exception_class": type(exc).__name__,
                    "returncode": getattr(exc, "returncode", None),
                    "stdout": _bounded_output(getattr(exc, "stdout", None)),
                    "stderr": _bounded_output(getattr(exc, "stderr", None)),
                }
            )
            detail.update({"timestamp_ns": time.time_ns(), "attempt": attempt})
            return detail

        restart_used = False

        def sample() -> tuple[int, int] | None:
            nonlocal paused, reason, total_memory, sampler, restart_used
            recovery_started = time.monotonic()
            attempts = 3 if memory_sample is not None else 2
            for attempt in range(1, attempts + 1):
                try:
                    if sampler is None:
                        assert memory_sample is not None
                        sampled_total, sampled_free = memory_sample(gpu_uuid)
                    else:
                        item = sampler.sample()
                        telemetry_samples.append(item)
                        sampled_total, sampled_free = item.total_bytes, item.free_bytes
                except (
                    TelemetrySampleError,
                    RuntimeError,
                    OSError,
                    subprocess.SubprocessError,
                ) as exc:
                    if sampler is None and type(exc) is RuntimeError:
                        telemetry_anomalies.append(diagnostic(exc, attempt))
                        reason = "GPU telemetry was malformed or unauthenticated"
                        return None
                    telemetry_anomalies.append(diagnostic(exc, attempt))
                    if not paused:
                        signal_child(signal.SIGSTOP)
                        paused = True
                    if sampler is None:
                        if attempt < attempts:
                            continue
                    elif not restart_used:
                        sampler.terminate()
                        restart_used = True
                        sampler = PersistentNvidiaSampler(gpu_uuid)
                        actions.append(sampler.terminate)
                        sampler_identities.append(sampler.identity)
                        continue
                    if time.monotonic() - recovery_started <= _MAX_TELEMETRY_RECOVERY_SECONDS:
                        reason = "external GPU telemetry unavailable"
                        return None
                    reason = "external GPU telemetry unavailable"
                    return None
                except Exception as exc:
                    telemetry_anomalies.append(diagnostic(exc, attempt))
                    reason = "GPU telemetry was malformed or unauthenticated"
                    return None
                if total_memory is not None and sampled_total != total_memory:
                    telemetry_anomalies.append(
                        {
                            "timestamp_ns": time.time_ns(),
                            "attempt": attempt,
                            "exception_class": "GPUIdentityError",
                            "returncode": None,
                            "stdout": "",
                            "stderr": "total GPU memory changed",
                        }
                    )
                    reason = "authenticated GPU total memory changed"
                    return None
                total_memory = sampled_total
                if sampled_free < minimum_free_bytes:
                    reason = "calibration breached the total-device free-memory floor"
                    return sampled_total, sampled_free
                if paused:
                    signal_child(signal.SIGCONT)
                    paused = False
                return sampled_total, sampled_free
            raise AssertionError("unreachable telemetry attempt count")

        while child.poll() is None:
            sampled = sample()
            if sampled is None:
                break
            _, free_memory = sampled
            observed_minimum = (
                free_memory if observed_minimum is None else min(observed_minimum, free_memory)
            )
            phase_minimum_free_bytes[phase] = min(
                phase_minimum_free_bytes.get(phase, free_memory), free_memory
            )
            if reason is not None:
                break
            for key, _ in selector.select(sample_interval_seconds):
                while True:
                    try:
                        chunk = os.read(key.fd, 65536)
                    except BlockingIOError:
                        break
                    if not chunk:
                        break
                    stdout_buffer.extend(chunk)
                while b"\n" in stdout_buffer:
                    raw, _, rest = stdout_buffer.partition(b"\n")
                    stdout_buffer = bytearray(rest)
                    handle_record(bytes(raw))
            elapsed = time.monotonic() - started
            completion_poll_deadline = maximum_runtime_seconds + sample_interval_seconds
            if not completed_receipt and elapsed > completion_poll_deadline:
                reason = "calibration exceeded the maximum elapsed-runtime cap"
                break
            if (
                completed_receipt
                and completed_deadline is not None
                and time.monotonic() > completed_deadline
            ):
                break
    elapsed = time.monotonic() - started
    stdout_buffer.extend(stdout)
    if stdout_buffer.strip():
        raise RuntimeError("calibration worker emitted unexpected trailing supervisor output")
    if stderr.strip():
        detail = stderr.decode(errors="replace").strip()
        raise RuntimeError(f"calibration worker stderr was not empty: {detail}")
    if observed_minimum is None:
        observed_minimum = 0
    if total_memory is None:
        total_memory = 0
    status = "FAIL" if reason is not None else "PASS"
    if reason is None and (
        ready_payload is None or (child.returncode != 0 and not cleanup_enforced)
    ):
        raise RuntimeError(f"calibration worker exited {child.returncode} without ready receipt")
    return WatchdogResult(
        status=status,
        reason=reason,
        minimum_free_bytes=observed_minimum,
        total_memory_bytes=total_memory,
        elapsed_seconds=elapsed,
        returncode=child.returncode,
        child_identity=identity,
        ready_payload=ready_payload,
        phase_minimum_free_bytes=phase_minimum_free_bytes,
        cleanup_enforced=cleanup_enforced,
        telemetry_anomalies=tuple(telemetry_anomalies),
        sampler_identities=tuple(sampler_identities),
        telemetry_samples=tuple(telemetry_samples),
    )
