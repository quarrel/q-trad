"""Authenticated bounded CUDA batch-width calibration."""

# ruff: noqa: I001 -- CUDA boundary must configure the process before Torch imports.
from __future__ import annotations

from .cuda_boundary import (
    CUBLAS_WORKSPACE_CONFIG,
    DETERMINISTIC_CUDA_POLICY,
    MAX_SCHEDULABLE_CPU_CONCURRENCY,
    PRE_TORCH_ENVIRONMENT_CONFIGURED,
)

import argparse
import gc
import hashlib
import json
import re
import os
import platform
import resource
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import torch

from .candidate_validation import candidate_head
from .grouped import configure_deterministic_cuda
from .qualification import (
    ReusableOracleEvidence,
    _require_qualification_determinism,
    build_reusable_oracle_evidence,
    compare_production_to_oracle,
)
from .runtime import FITTED_FAMILY_IDS, environment_identity
from .stage_cache import load_stage_cache_batches
from .synthetic_qualification import build_synthetic_cache, synthetic_qualification_inputs
from .resource_watchdog import nvidia_memory_bytes, supervise
from .tensor import TensorContract

_SCHEMA = "R4-P0-BATCH-CALIBRATION-V5"
_WIDTHS = (4, 8, 16, 32, 64)
_SEED = 17
_TIMESTAMP_COUNT = 65
_REPEAT_COUNT = 2
_MAXIMUM_RESERVED_FRACTION = 0.7
_MINIMUM_DEVICE_FREE_BYTES = 4 * 1024**3
_DEVICE_MEMORY_SAMPLE_SECONDS = 0.05
_MAXIMUM_RUNTIME_SECONDS = 3600.0
_ACCEPTED_SHARED_UPPER_BOUND_SECONDS = 2048.064486
_MAXIMUM_PROJECTED_SECONDS = 3300.0
_COMPLETED_EXIT_GRACE_SECONDS = 10.0
_ORACLE_STEP_PROBE_SCHEMA = "R4-P0-ORACLE-STEP-PROBE-V1"
_ORACLE_STEP_PROBE_MINIMUM_FREE_BYTES = 4_831_838_208
_PROGRESS_PATH: Path | None = None


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _write_once(path: Path, value: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary.exists():
            temporary.unlink()


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _calibration_root(working_root: Path, head: str, attempt: str, *, probe: bool) -> Path:
    if re.fullmatch(r"[a-z][a-z0-9-]{0,31}", attempt) is None:
        raise ValueError(
            "calibration attempt must start with a lowercase letter and contain "
            "only lowercase letters, digits, or hyphens (maximum 32 characters)"
        )
    category = "r4-perf8-production-probe" if probe else "r4-perf8-batch-calibration"
    return (
        working_root.parent.parent
        / "MAP_orchestrator/r4-p0-20260828-ddd66f9/longrun"
        / category
        / f"{head}-{attempt}"
    )


def _device() -> dict[str, Any]:
    properties = torch.cuda.get_device_properties(0)
    driver = (
        subprocess.run(
            (
                "nvidia-smi",
                "--query-gpu=driver_version,uuid,name,memory.total",
                "--format=csv,noheader,nounits",
            ),
            check=True,
            capture_output=True,
            text=True,
        )
        .stdout.strip()
        .split(", ")
    )
    return {
        "driver": driver[0],
        "uuid": driver[1],
        "name": driver[2],
        "total_memory_mib": int(driver[3]),
        "cuda": torch.version.cuda,
        "torch": torch.__version__,
        "python": platform.python_version(),
        "compute_capability": [properties.major, properties.minor],
    }


def _semantic_identity(result: dict[str, Any]) -> str:
    semantic = {
        "model_hash": result["model_hash"],
        "prediction_hash": result["prediction_hash"],
        "oracle_prediction_identity": result["oracle_prediction_identity"],
        "production_gradient_identity": result["production_gradient_identity"],
        "production_final_parameter_identity": result["production_final_parameter_identity"],
        "float32": result["float32"],
        "float64": result["float64"],
    }
    return hashlib.sha256(_canonical(semantic)).hexdigest()


class _ResourceMonitor:
    """Actively enforce device-wide free memory and elapsed-runtime limits."""

    def __init__(
        self,
        *,
        started: float | None = None,
        cancel: Callable[[str], None] | None = None,
    ) -> None:
        self.minimum_free_bytes = torch.cuda.mem_get_info()[0]
        self._started = time.monotonic() if started is None else started
        self._cancel = cancel
        self._failure: BaseException | None = None
        self._breach: str | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample_until_stopped, daemon=True)

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self._started

    @property
    def breach(self) -> str | None:
        return self._breach

    def _record_breach(self, reason: str) -> None:
        if self._breach is not None:
            return
        self._breach = reason
        self._stop.set()
        if self._cancel is not None:
            self._cancel(reason)

    def _poll(self) -> None:
        free_bytes = torch.cuda.mem_get_info()[0]
        self.minimum_free_bytes = min(self.minimum_free_bytes, free_bytes)
        if free_bytes < _MINIMUM_DEVICE_FREE_BYTES:
            self._record_breach("calibration breached the total-device free-memory floor")
        elif self.elapsed_seconds > _MAXIMUM_RUNTIME_SECONDS:
            self._record_breach("calibration exceeded the maximum elapsed-runtime cap")

    def _sample_until_stopped(self) -> None:
        try:
            while not self._stop.wait(_DEVICE_MEMORY_SAMPLE_SECONDS):
                self._poll()
        except BaseException as exc:
            self._failure = exc
            self._record_breach("resource monitor failed")

    def __enter__(self) -> _ResourceMonitor:
        self._thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self._stop.set()
        self._thread.join()
        if exc_type is None:
            self.require_limits()

    def require_limits(self) -> None:
        if self._failure is not None:
            raise RuntimeError("resource monitor failed") from self._failure
        if self._breach is None:
            self._poll()
        if self._breach is not None:
            raise RuntimeError(self._breach)


def _release_allocator(monitor: _ResourceMonitor) -> float:
    """Release reusable CUDA allocator state outside all reported production timing."""
    started = time.perf_counter()
    torch.cuda.synchronize()
    monitor.require_limits()
    torch.cuda.empty_cache()
    torch.cuda.synchronize()
    monitor.require_limits()
    return time.perf_counter() - started


def _phase_reporter(width: int | str, repeat: int, family: str) -> Callable[[str], None]:
    def report_phase(name: str) -> None:
        phase = f"width={width}/repeat={repeat}/family={family}/{name}"
        _persist_phase(phase)
        print(
            json.dumps(
                {
                    "status": "PHASE",
                    "phase": phase,
                }
            ),
            flush=True,
        )

    return report_phase


def _persist_phase(phase: str) -> None:
    if _PROGRESS_PATH is None:
        return
    with _PROGRESS_PATH.open("ab") as stream:
        stream.write(_canonical({"phase": phase, "time_ns": time.time_ns()}) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def _release_completed_state(monitor: _ResourceMonitor) -> None:
    _persist_phase("cleanup_start")
    _release_allocator(monitor)
    gc.collect()
    torch.cuda.synchronize()
    monitor.require_limits()
    _persist_phase("cleanup_complete")


def _build_shared_oracle_evidence(
    repeat: int,
    training: Any,
    prediction: Any,
    monitor: _ResourceMonitor,
) -> tuple[dict[str, ReusableOracleEvidence], dict[str, Any]]:
    configure_deterministic_cuda(_SEED)
    bundles: dict[str, ReusableOracleEvidence] = {}
    family_evidence: list[dict[str, Any]] = []
    resource_control_seconds = 0.0
    started = time.perf_counter()
    for family in FITTED_FAMILY_IDS:
        torch.cuda.reset_peak_memory_stats()

        def release() -> None:
            nonlocal resource_control_seconds
            resource_control_seconds += _release_allocator(monitor)

        bundle = build_reusable_oracle_evidence(
            family,
            training,
            prediction,
            seed=_SEED,
            phase_callback=_phase_reporter("shared", repeat, family),
            release_callback=release,
        )
        bundles[family] = bundle
        family_evidence.append(
            {
                "family": family,
                "oracle_evidence_identity": bundle.identity,
                "evidence": dict(bundle.evidence),
                "peak_allocated_bytes": torch.cuda.max_memory_allocated(),
                "peak_reserved_bytes": torch.cuda.max_memory_reserved(),
            }
        )
        resource_control_seconds += _release_allocator(monitor)
    elapsed = time.perf_counter() - started
    return bundles, {
        "repeat": repeat,
        "elapsed_seconds": elapsed,
        "oracle_evidence_elapsed_seconds": elapsed - resource_control_seconds,
        "resource_control_elapsed_seconds": resource_control_seconds,
        "families": family_evidence,
    }


def _calibrate_width(
    width: int,
    repeat: int,
    bundles: Mapping[str, ReusableOracleEvidence],
    training: Any,
    prediction: Any,
    monitor: _ResourceMonitor,
    total_memory_bytes: int,
) -> dict[str, Any]:
    comparison: list[dict[str, Any]] = []
    family_resources: list[dict[str, Any]] = []
    reserved_limit_bytes = int(total_memory_bytes * _MAXIMUM_RESERVED_FRACTION)
    resource_control_seconds = 0.0
    started = time.perf_counter()
    for family in FITTED_FAMILY_IDS:
        torch.cuda.reset_peak_memory_stats()

        def release() -> None:
            nonlocal resource_control_seconds
            resource_control_seconds += _release_allocator(monitor)

        result = compare_production_to_oracle(
            bundles[family],
            training,
            prediction,
            calibration_batch_size=width,
            phase_callback=_phase_reporter(width, repeat, family),
            release_callback=release,
        )
        torch.cuda.synchronize()
        peak_allocated_bytes = torch.cuda.max_memory_allocated()
        peak_reserved_bytes = torch.cuda.max_memory_reserved()
        monitor.require_limits()
        if peak_reserved_bytes >= reserved_limit_bytes:
            raise RuntimeError("calibration exceeded the reserved-memory safety cap")
        comparison.append(result)
        family_resources.append(
            {
                "family": family,
                "peak_allocated_bytes": peak_allocated_bytes,
                "peak_reserved_bytes": peak_reserved_bytes,
            }
        )
        resource_control_seconds += _release_allocator(monitor)
    elapsed = time.perf_counter() - started
    production_elapsed = sum(result["telemetry"]["elapsed_seconds"] for result in comparison)
    return {
        "repeat": repeat,
        "production_elapsed_seconds": production_elapsed,
        "production_setup_elapsed_seconds": elapsed - production_elapsed - resource_control_seconds,
        "resource_control_elapsed_seconds": resource_control_seconds,
        "family_results": comparison,
        "family_resources": family_resources,
        "family_semantic_identities": [_semantic_identity(result) for result in comparison],
        "oracle_evidence_identities": [result["oracle_evidence_identity"] for result in comparison],
        "peak_allocated_bytes": max(item["peak_allocated_bytes"] for item in family_resources),
        "peak_reserved_bytes": max(item["peak_reserved_bytes"] for item in family_resources),
        "h2d_calls": sum(result["telemetry"]["h2d_calls"] for result in comparison),
        "h2d_bytes": sum(result["telemetry"]["h2d_bytes"] for result in comparison),
        "h2d_seconds": sum(result["telemetry"]["h2d_seconds"] for result in comparison),
        "overlapped_prefetch_calls": sum(
            result["telemetry"]["overlapped_prefetch_calls"] for result in comparison
        ),
        "rows_per_second": sum(result["telemetry"]["rows"] for result in comparison)
        / production_elapsed,
    }


def _calibrate_widths(
    training: Any,
    prediction: Any,
    monitor: _ResourceMonitor,
    total_memory_bytes: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    width_repeats: dict[int, list[dict[str, Any]]] = {width: [] for width in _WIDTHS}
    shared_repeats: list[dict[str, Any]] = []
    for repeat in range(_REPEAT_COUNT):
        bundles, shared = _build_shared_oracle_evidence(repeat, training, prediction, monitor)
        shared_repeats.append(shared)
        for width in _WIDTHS:
            width_repeats[width].append(
                _calibrate_width(
                    width, repeat, bundles, training, prediction, monitor, total_memory_bytes
                )
            )
    widths = []
    for width, repeats in width_repeats.items():
        identities = [repeat["family_semantic_identities"] for repeat in repeats]
        if len(identities) > 1 and identities[0] != identities[1]:
            raise RuntimeError(f"width {width} numerical repeat identity drift")
        oracle_identities = [repeat["oracle_evidence_identities"] for repeat in repeats]
        widths.append(
            {
                "width": width,
                "warmup_repeats": 1,
                "measured_repeats": 1,
                "repeats": repeats,
                "deterministic_repeat": {
                    "passed": len(identities) < 2 or identities[0] == identities[1],
                    "meaning": (
                        "exact repeated model/gradient/final-parameter/prediction identities"
                    ),
                    "family_semantic_identities": identities[0],
                },
                "shared_evidence_binding": {
                    "passed": all(
                        repeat_ids
                        == [
                            family["oracle_evidence_identity"]
                            for family in shared_repeats[index]["families"]
                        ]
                        for index, repeat_ids in enumerate(oracle_identities)
                    ),
                    "repeat_oracle_evidence_identities": oracle_identities,
                },
            }
        )
    return widths, shared_repeats


def _bind_cpu_resources() -> dict[str, Any]:
    available = sorted(os.sched_getaffinity(0))
    bound = available[:MAX_SCHEDULABLE_CPU_CONCURRENCY]
    os.sched_setaffinity(0, bound)
    torch.set_num_threads(len(bound))
    torch.set_num_interop_threads(len(bound))
    return {
        "policy": "hard process affinity and Torch intra/inter-op thread ceilings",
        "max_schedulable_cpu_concurrency": MAX_SCHEDULABLE_CPU_CONCURRENCY,
        "cpu_affinity": bound,
        "torch_intraop_threads": torch.get_num_threads(),
        "torch_interop_threads": torch.get_num_interop_threads(),
        "process_pool_workers": 0,
    }


def _write_failure_receipts(
    output_root: Path,
    *,
    head: str,
    attempt: str,
    reason: str,
    minimum_free_bytes: int,
    monitor_elapsed_seconds: float,
    started: float,
    cpu_resources: dict[str, Any],
    total_memory: int,
    telemetry_anomalies: tuple[dict[str, object], ...] = (),
    telemetry_evidence: dict[str, object] | None = None,
) -> None:
    resource_payload = {
        "schema": _SCHEMA,
        "candidate": head,
        "attempt": attempt,
        "status": "FAIL",
        "reason": reason,
        "wall_seconds": time.time() - started,
        "cpu": cpu_resources,
        "cuda": {
            "device_total_bytes": total_memory,
            "minimum_total_device_free_bytes": minimum_free_bytes,
            "minimum_total_device_free_limit_bytes": _MINIMUM_DEVICE_FREE_BYTES,
            "sample_interval_seconds": _DEVICE_MEMORY_SAMPLE_SECONDS,
        },
        "telemetry_anomalies": list(telemetry_anomalies),
        **(telemetry_evidence or {}),
        "maximum_runtime_seconds": _MAXIMUM_RUNTIME_SECONDS,
        "monitor_elapsed_seconds": monitor_elapsed_seconds,
    }
    _write_once(output_root / "resource.txt", _canonical(resource_payload))
    _write_once(
        output_root / "terminal.json",
        _canonical(
            {
                "schema": _SCHEMA,
                "candidate": head,
                "attempt": attempt,
                "status": "FAIL",
                "reason": reason,
            }
        ),
    )
    inventory = {
        name: _sha(output_root / name)
        for name in (
            "process.json",
            "terminal.json",
            "stdout.log",
            "stderr.log",
            "resource.txt",
            "progress.jsonl",
            *(("calibration.json",) if (output_root / "calibration.json").is_file() else ()),
        )
    }
    inventory["inventory_identity"] = hashlib.sha256(_canonical(inventory)).hexdigest()
    _write_once(output_root / "inventory.json", _canonical(inventory))


def run(output_root: Path, *, attempt: str, supervised: bool = False, probe: bool = False) -> None:
    global _PROGRESS_PATH
    if not PRE_TORCH_ENVIRONMENT_CONFIGURED:
        raise RuntimeError("pre-Torch CUDA boundary was not configured")
    if os.environ.get("CUBLAS_WORKSPACE_CONFIG") != CUBLAS_WORKSPACE_CONFIG:
        raise RuntimeError("cuBLAS deterministic policy was not bound before Torch import")
    cpu_resources = _bind_cpu_resources()
    working_root = Path(__file__).resolve().parents[2]
    head = candidate_head(working_root)
    expected = _calibration_root(working_root, head, attempt, probe=probe)
    if output_root.resolve() != expected.resolve():
        raise ValueError(f"calibration output must be {expected}")
    output_root.mkdir(parents=True, exist_ok=False)
    _PROGRESS_PATH = output_root / "progress.jsonl"
    _write_once(_PROGRESS_PATH, b"")
    _persist_phase("startup")
    started = time.time()
    monitor_started = time.monotonic()
    process = {
        "schema": _SCHEMA,
        "candidate": head,
        "attempt": attempt,
        "parent": subprocess.run(
            ("git", "rev-parse", "HEAD^"),
            cwd=working_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
        "working_root": str(working_root),
        "pid": os.getpid(),
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "start_ticks": Path(f"/proc/{os.getpid()}/stat").read_text().split()[21],
        "cmdline": sys.argv,
        "cpu": cpu_resources,
    }
    _write_once(output_root / "process.json", _canonical(process))
    _write_once(output_root / "stdout.log", b"")
    _write_once(output_root / "stderr.log", b"")
    stderr_fd = os.open(output_root / "stderr.log", os.O_WRONLY | os.O_APPEND)
    try:
        os.dup2(stderr_fd, sys.stderr.fileno())
    finally:
        os.close(stderr_fd)
    configure_deterministic_cuda(_SEED)
    observed_policy = _require_qualification_determinism(_SEED)
    total_memory = torch.cuda.get_device_properties(0).total_memory

    memory_monitor = _ResourceMonitor(started=monitor_started)

    with memory_monitor:
        memory_monitor.require_limits()
        with tempfile.TemporaryDirectory(prefix="r4-perf8-calibration-") as temporary:
            inputs = synthetic_qualification_inputs(dev1_timestamp_count=_TIMESTAMP_COUNT)
            cache_root, identity = build_synthetic_cache(Path(temporary) / "cache", inputs=inputs)
            training, prediction, _ = load_stage_cache_batches(cache_root, expected=identity)
            widths, reusable_oracle_evidence = _calibrate_widths(
                training, prediction, memory_monitor, total_memory
            )
        memory_monitor.require_limits()
    maximum_reserved = max(
        repeat["peak_reserved_bytes"] for width in widths for repeat in width["repeats"]
    )
    maximum_allocated = max(
        repeat["peak_allocated_bytes"] for width in widths for repeat in width["repeats"]
    )
    measured_all_width_production_setup_seconds = sum(
        repeat["production_elapsed_seconds"] + repeat["production_setup_elapsed_seconds"]
        for width in widths
        for repeat in width["repeats"]
    )
    conservative_projection_seconds = (
        _ACCEPTED_SHARED_UPPER_BOUND_SECONDS + measured_all_width_production_setup_seconds
    )
    projection = {
        "accepted_shared_upper_bound_seconds": _ACCEPTED_SHARED_UPPER_BOUND_SECONDS,
        "measured_all_width_production_setup_seconds": (
            measured_all_width_production_setup_seconds
        ),
        "conservative_total_seconds": conservative_projection_seconds,
        "maximum_seconds": _MAXIMUM_PROJECTED_SECONDS,
        "passed": conservative_projection_seconds <= _MAXIMUM_PROJECTED_SECONDS,
    }
    if probe and not projection["passed"]:
        raise RuntimeError("production probe conservative projection exceeded 3300 seconds")
    calibration = {
        "schema": _SCHEMA,
        "candidate": head,
        "attempt": attempt,
        "device": _device(),
        "runtime": environment_identity(),
        "deterministic_policy": {
            "declared": dict(DETERMINISTIC_CUDA_POLICY),
            "observed": observed_policy,
            "identity": hashlib.sha256(_canonical(observed_policy)).hexdigest(),
            "pre_torch_environment": True,
        },
        "production_geometry": {
            "lookback": TensorContract().lookback_minutes + 1,
            "nodes": 20,
            "features": len(TensorContract().feature_names),
            "training_timestamps": _TIMESTAMP_COUNT,
            "prediction_timestamps": 1,
            "source": "outcome-blind synthetic exact production tensor/cache contract",
            "pipeline": ["fit_one_model", "predict_residual"],
        },
        "reusable_oracle_evidence": reusable_oracle_evidence,
        "reusable_oracle_elapsed_seconds": sum(
            repeat["oracle_evidence_elapsed_seconds"] for repeat in reusable_oracle_evidence
        ),
        "production_probe_projection": projection,
        "widths": widths,
        "selected_width": min(
            widths,
            key=lambda result: result["repeats"][-1]["production_elapsed_seconds"],
        )["width"],
        "decision": (
            "select the width with the lowest measured production fit/predict time from "
            "representative geometry with at least 65 training timestamps; independently timed "
            "oracle validation, numerical identity and the reserved-memory cap remain mandatory"
        ),
        "elapsed_seconds": time.time() - started,
    }
    _write_once(output_root / "calibration.json", _canonical(calibration))
    _persist_phase("calibration_complete")
    del widths, reusable_oracle_evidence, training, prediction, calibration
    _release_completed_state(memory_monitor)
    usage = resource.getrusage(resource.RUSAGE_SELF)
    resource_payload = {
        "schema": _SCHEMA,
        "candidate": head,
        "attempt": attempt,
        "wall_seconds": time.time() - started,
        "cpu_user_seconds": usage.ru_utime,
        "cpu_system_seconds": usage.ru_stime,
        "maximum_rss_kib": usage.ru_maxrss,
        "cpu": cpu_resources,
        "cuda": {
            "policy": (
                "candidate PyTorch reserved/device total < 0.7 and total device free >= 4 GiB"
            ),
            "device_total_bytes": total_memory,
            "maximum_reserved_fraction_limit": _MAXIMUM_RESERVED_FRACTION,
            "maximum_reserved_bytes": maximum_reserved,
            "maximum_reserved_fraction": maximum_reserved / total_memory,
            "maximum_allocated_bytes": maximum_allocated,
            "minimum_total_device_free_bytes": memory_monitor.minimum_free_bytes,
            "minimum_total_device_free_limit_bytes": _MINIMUM_DEVICE_FREE_BYTES,
            "sample_interval_seconds": _DEVICE_MEMORY_SAMPLE_SECONDS,
            "maximum_runtime_seconds": _MAXIMUM_RUNTIME_SECONDS,
            "allocator_release": (
                "synchronise, sample, empty_cache, synchronise, sample between families"
            ),
        },
        "peak_allocated_bytes": maximum_allocated,
        "peak_reserved_bytes": maximum_reserved,
    }
    if supervised:
        _persist_phase("completed_receipt/ready")
        print(
            json.dumps(
                {
                    "status": "READY",
                    "candidate": head,
                    "attempt": attempt,
                    "pid": os.getpid(),
                    "process_sha256": _sha(output_root / "process.json"),
                    "calibration_sha256": _sha(output_root / "calibration.json"),
                    "projection_passed": projection["passed"],
                }
            ),
            flush=True,
        )
        supervisor_receipt = json.loads(sys.stdin.readline())
        resource_payload["cuda"]["minimum_total_device_free_bytes"] = min(
            resource_payload["cuda"]["minimum_total_device_free_bytes"],
            supervisor_receipt["minimum_free_bytes"],
        )
    _write_once(output_root / "resource.txt", _canonical(resource_payload))
    terminal = {
        "schema": _SCHEMA,
        "candidate": head,
        "attempt": attempt,
        "status": "PASS",
    }
    _write_once(output_root / "terminal.json", _canonical(terminal))
    sys.stderr.flush()


def _oracle_step_probe_root(working_root: Path, head: str, attempt: str) -> Path:
    if re.fullmatch(r"[a-z][a-z0-9-]{0,31}", attempt) is None:
        raise ValueError(
            "oracle-step probe attempt must start with a lowercase letter and contain "
            "only lowercase letters, digits, or hyphens (maximum 32 characters)"
        )
    return (
        working_root.parent.parent
        / "MAP_orchestrator/r4-p0-20260828-ddd66f9/longrun/r4-perf8-oracle-step-probe"
        / f"{head}-{attempt}"
    )


def _run_oracle_step_probe(output_root: Path, *, attempt: str) -> None:
    global _PROGRESS_PATH
    working_root = Path(__file__).resolve().parents[2]
    head = candidate_head(working_root)
    expected = _oracle_step_probe_root(working_root, head, attempt)
    if output_root.resolve() != expected.resolve():
        raise ValueError(f"oracle-step probe output must be {expected}")
    output_root.mkdir(parents=True, exist_ok=False)
    _PROGRESS_PATH = output_root / "progress.jsonl"
    _write_once(_PROGRESS_PATH, b"")
    started = time.time()
    process = {
        "schema": _ORACLE_STEP_PROBE_SCHEMA,
        "operation": "oracle_one_step_memory_probe",
        "candidate": head,
        "attempt": attempt,
        "parent": subprocess.run(
            ("git", "rev-parse", "HEAD^"),
            cwd=working_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
        "working_root": str(working_root),
        "pid": os.getpid(),
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "start_ticks": Path(f"/proc/{os.getpid()}/stat").read_text().split()[21],
        "cmdline": sys.argv,
        "cpu": _bind_cpu_resources(),
    }
    _write_once(output_root / "process.json", _canonical(process))
    _write_once(output_root / "stdout.log", b"")
    _write_once(output_root / "stderr.log", b"")
    stderr_fd = os.open(output_root / "stderr.log", os.O_WRONLY | os.O_APPEND)
    try:
        os.dup2(stderr_fd, sys.stderr.fileno())
    finally:
        os.close(stderr_fd)
    configure_deterministic_cuda(_SEED)
    policy = _require_qualification_determinism(_SEED)
    monitor = _ResourceMonitor(started=time.monotonic())
    with monitor, tempfile.TemporaryDirectory(prefix="r4-perf8-oracle-step-probe-") as temporary:
        inputs = synthetic_qualification_inputs(dev1_timestamp_count=_TIMESTAMP_COUNT)
        cache_root, identity = build_synthetic_cache(Path(temporary) / "cache", inputs=inputs)
        training, prediction, _ = load_stage_cache_batches(cache_root, expected=identity)
        family = "POOLED_NON_GRAPH_RESIDUAL"

        def release() -> None:
            _release_allocator(monitor)

        torch.cuda.reset_peak_memory_stats()
        evidence = build_reusable_oracle_evidence(
            family,
            training,
            prediction,
            seed=_SEED,
            phase_callback=_phase_reporter(64, 0, family),
            release_callback=release,
        )
        torch.cuda.synchronize()
        monitor.require_limits()
        probe = {
            "schema": _ORACLE_STEP_PROBE_SCHEMA,
            "operation": "oracle_one_step_memory_probe",
            "candidate": head,
            "attempt": attempt,
            "family": family,
            "width": 64,
            "repeat": 0,
            "training_timestamps": _TIMESTAMP_COUNT,
            "cache_identity": hashlib.sha256(_canonical(identity.semantic_inputs())).hexdigest(),
            "oracle_evidence_identity": evidence.identity,
            "oracle_evidence": dict(evidence.evidence),
            "deterministic_policy": policy,
            "device": _device(),
            "peak_allocated_bytes": torch.cuda.max_memory_allocated(),
            "peak_reserved_bytes": torch.cuda.max_memory_reserved(),
        }
        _write_once(output_root / "probe.json", _canonical(probe))
        del evidence, probe, training, prediction
        _release_completed_state(monitor)
    _persist_phase("completed_receipt/ready")
    print(
        json.dumps(
            {
                "status": "READY",
                "candidate": head,
                "attempt": attempt,
                "pid": os.getpid(),
                "process_sha256": _sha(output_root / "process.json"),
                "probe_sha256": _sha(output_root / "probe.json"),
            }
        ),
        flush=True,
    )
    supervisor_receipt = json.loads(sys.stdin.readline())
    usage = resource.getrusage(resource.RUSAGE_SELF)
    resource_payload = {
        "schema": _ORACLE_STEP_PROBE_SCHEMA,
        "candidate": head,
        "attempt": attempt,
        "wall_seconds": time.time() - started,
        "cpu_user_seconds": usage.ru_utime,
        "cpu_system_seconds": usage.ru_stime,
        "maximum_rss_kib": usage.ru_maxrss,
        "cuda": {
            "minimum_total_device_free_bytes": supervisor_receipt["minimum_free_bytes"],
            "minimum_total_device_free_limit_bytes": _ORACLE_STEP_PROBE_MINIMUM_FREE_BYTES,
            "maximum_reserved_fraction_limit": _MAXIMUM_RESERVED_FRACTION,
            "sample_interval_seconds": _DEVICE_MEMORY_SAMPLE_SECONDS,
        },
    }
    _write_once(output_root / "resource.txt", _canonical(resource_payload))
    _write_once(
        output_root / "terminal.json",
        _canonical(
            {
                "schema": _ORACLE_STEP_PROBE_SCHEMA,
                "candidate": head,
                "attempt": attempt,
                "status": "PASS",
            }
        ),
    )


def _supervise_oracle_step_probe(output_root: Path, *, attempt: str) -> None:
    working_root = Path(__file__).resolve().parents[2]
    head = candidate_head(working_root)
    expected = _oracle_step_probe_root(working_root, head, attempt)
    if output_root.resolve() != expected.resolve():
        raise ValueError(f"oracle-step probe output must be {expected}")
    uuid_rows = subprocess.run(
        ("nvidia-smi", "--query-gpu=uuid", "--format=csv,noheader,nounits"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    if len(uuid_rows) != 1:
        raise RuntimeError("oracle-step probe requires exactly one authenticated GPU")

    def validate(payload: dict[str, object]) -> None:
        process = json.loads((output_root / "process.json").read_bytes())
        probe = json.loads((output_root / "probe.json").read_bytes())
        if (
            payload["candidate"] != head
            or process["attempt"] != attempt
            or probe["attempt"] != attempt
            or payload["pid"] != process["pid"]
            or payload["process_sha256"] != _sha(output_root / "process.json")
            or payload["probe_sha256"] != _sha(output_root / "probe.json")
            or probe["schema"] != _ORACLE_STEP_PROBE_SCHEMA
            or probe["operation"] != "oracle_one_step_memory_probe"
            or probe["family"] != "POOLED_NON_GRAPH_RESIDUAL"
            or probe["width"] != 64
            or probe["repeat"] != 0
            or probe["training_timestamps"] != _TIMESTAMP_COUNT
        ):
            raise RuntimeError("oracle-step probe receipt identity mismatch")

    initial_total, initial_free = nvidia_memory_bytes(uuid_rows[0])
    if initial_free < _ORACLE_STEP_PROBE_MINIMUM_FREE_BYTES:
        raise RuntimeError(
            "oracle-step probe preflight breached the total-device free-memory floor"
        )
    result = supervise(
        (
            sys.executable,
            "-m",
            "experiments.r4_residual_graph.calibration",
            "--worker",
            "--oracle-step-probe",
            "--attempt",
            attempt,
            str(output_root),
        ),
        gpu_uuid=uuid_rows[0],
        minimum_free_bytes=_ORACLE_STEP_PROBE_MINIMUM_FREE_BYTES,
        maximum_runtime_seconds=_MAXIMUM_RUNTIME_SECONDS,
        sample_interval_seconds=_DEVICE_MEMORY_SAMPLE_SECONDS,
        cwd=working_root,
        completed_receipt_validator=validate,
        completed_exit_grace_seconds=_COMPLETED_EXIT_GRACE_SECONDS,
        expected_total_memory_bytes=initial_total,
    )
    if result.status != "PASS" or result.ready_payload is None:
        reason = result.reason or "oracle-step probe failed"
        child_identity = {
            "pid": result.child_identity.pid,
            "boot_id": result.child_identity.boot_id,
            "start_ticks": result.child_identity.start_ticks,
            "cmdline": list(result.child_identity.cmdline),
        }
        failure_details = {
            "schema": _ORACLE_STEP_PROBE_SCHEMA,
            "candidate": head,
            "attempt": attempt,
            "status": "FAIL",
            "reason": reason,
            "gpu_uuid": uuid_rows[0],
            "minimum_free_bytes": result.minimum_free_bytes,
            "total_memory_bytes": result.total_memory_bytes,
            "minimum_free_limit_bytes": _ORACLE_STEP_PROBE_MINIMUM_FREE_BYTES,
            "sample_interval_seconds": _DEVICE_MEMORY_SAMPLE_SECONDS,
            "elapsed_seconds": result.elapsed_seconds,
            "child_returncode": result.returncode,
            "cleanup_enforced": result.cleanup_enforced,
            "child_identity": child_identity,
            "phase_minimum_free_bytes": result.phase_minimum_free_bytes,
            "telemetry_anomalies": list(result.telemetry_anomalies),
            **result.telemetry_evidence(),
        }
        _write_once(output_root / "resource.txt", _canonical(failure_details))
        _write_once(
            output_root / "terminal.json",
            _canonical(
                {
                    "schema": _ORACLE_STEP_PROBE_SCHEMA,
                    "candidate": head,
                    "attempt": attempt,
                    "status": "FAIL",
                    "reason": reason,
                    "telemetry_anomalies": list(result.telemetry_anomalies),
                    **result.telemetry_evidence(),
                }
            ),
        )
        _write_once(output_root / "supervisor.json", _canonical(failure_details))
        inventory = {
            name: _sha(output_root / name)
            for name in (
                "process.json",
                "terminal.json",
                "stdout.log",
                "stderr.log",
                "resource.txt",
                "progress.jsonl",
                "probe.json",
                "calibration.json",
                "supervisor.json",
            )
            if (output_root / name).is_file()
        }
        inventory["inventory_identity"] = hashlib.sha256(_canonical(inventory)).hexdigest()
        _write_once(output_root / "inventory.json", _canonical(inventory))
        raise RuntimeError(reason)
    _write_once(
        output_root / "supervisor.json",
        _canonical(
            {
                "schema": _ORACLE_STEP_PROBE_SCHEMA,
                "candidate": head,
                "attempt": attempt,
                "status": "PASS",
                "minimum_free_bytes": result.minimum_free_bytes,
                "total_memory_bytes": result.total_memory_bytes,
                "elapsed_seconds": result.elapsed_seconds,
                "child_identity": {
                    "pid": result.child_identity.pid,
                    "boot_id": result.child_identity.boot_id,
                    "start_ticks": result.child_identity.start_ticks,
                    "cmdline": list(result.child_identity.cmdline),
                },
                "phase_minimum_free_bytes": result.phase_minimum_free_bytes,
                "telemetry_anomalies": list(result.telemetry_anomalies),
                **result.telemetry_evidence(),
            }
        ),
    )
    inventory = {
        name: _sha(output_root / name)
        for name in (
            "process.json",
            "terminal.json",
            "stdout.log",
            "stderr.log",
            "resource.txt",
            "progress.jsonl",
            "probe.json",
            "supervisor.json",
        )
    }
    inventory["inventory_identity"] = hashlib.sha256(_canonical(inventory)).hexdigest()
    _write_once(output_root / "inventory.json", _canonical(inventory))


def _supervise_run(output_root: Path, *, attempt: str, probe: bool = False) -> None:
    working_root = Path(__file__).resolve().parents[2]
    head = candidate_head(working_root)
    expected = _calibration_root(working_root, head, attempt, probe=probe)
    if output_root.resolve() != expected.resolve():
        raise ValueError(f"calibration output must be {expected}")
    uuid_rows = subprocess.run(
        ("nvidia-smi", "--query-gpu=uuid", "--format=csv,noheader,nounits"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    if len(uuid_rows) != 1:
        raise RuntimeError("calibration requires exactly one authenticated GPU")
    _total_memory, initial_free = nvidia_memory_bytes(uuid_rows[0])
    if initial_free < _MINIMUM_DEVICE_FREE_BYTES:
        raise RuntimeError("calibration preflight breached the total-device free-memory floor")
    started = time.time()

    def validate_completed_receipt(payload: dict[str, object]) -> None:
        process = json.loads((output_root / "process.json").read_bytes())
        if payload["candidate"] != head or payload["attempt"] != attempt:
            raise RuntimeError("completed calibration receipt candidate mismatch")
        if (
            process["schema"] != _SCHEMA
            or process["candidate"] != head
            or process["attempt"] != attempt
            or payload["pid"] != process["pid"]
            or payload["process_sha256"] != _sha(output_root / "process.json")
        ):
            raise RuntimeError("completed calibration receipt process identity mismatch")
        calibration_path = output_root / "calibration.json"
        if not calibration_path.is_file() or payload["calibration_sha256"] != _sha(
            calibration_path
        ):
            raise RuntimeError("completed calibration receipt hash mismatch")
        calibration = json.loads(calibration_path.read_bytes())
        if (
            calibration["candidate"] != head
            or calibration["attempt"] != attempt
            or calibration["schema"] != _SCHEMA
        ):
            raise RuntimeError("completed calibration receipt identity mismatch")
        if probe and (
            payload["projection_passed"] is not True
            or calibration["production_probe_projection"]["passed"] is not True
        ):
            raise RuntimeError("completed production probe projection did not pass")

    result = supervise(
        (
            sys.executable,
            "-m",
            "experiments.r4_residual_graph.calibration",
            "--worker",
            "--attempt",
            attempt,
            *(("--probe",) if probe else ()),
            str(output_root),
        ),
        gpu_uuid=uuid_rows[0],
        minimum_free_bytes=_MINIMUM_DEVICE_FREE_BYTES,
        maximum_runtime_seconds=_MAXIMUM_RUNTIME_SECONDS,
        sample_interval_seconds=_DEVICE_MEMORY_SAMPLE_SECONDS,
        cwd=working_root,
        completed_receipt_validator=validate_completed_receipt,
        completed_exit_grace_seconds=_COMPLETED_EXIT_GRACE_SECONDS,
        expected_total_memory_bytes=_total_memory,
    )
    if result.status == "FAIL":
        process = json.loads((output_root / "process.json").read_bytes())
        _write_failure_receipts(
            output_root,
            head=head,
            attempt=attempt,
            reason=result.reason or "unreachable",
            minimum_free_bytes=result.minimum_free_bytes,
            monitor_elapsed_seconds=result.elapsed_seconds,
            started=started,
            cpu_resources=process["cpu"],
            total_memory=result.total_memory_bytes,
            telemetry_anomalies=result.telemetry_anomalies,
            telemetry_evidence=result.telemetry_evidence(),
        )
        raise RuntimeError(result.reason)
    if result.ready_payload is None:
        raise RuntimeError("calibration supervisor did not authenticate a completed receipt")
    for receipt_name in (
        "process.json",
        "calibration.json",
        "resource.txt",
        "terminal.json",
    ):
        receipt = json.loads((output_root / receipt_name).read_bytes())
        if (
            receipt["schema"] != _SCHEMA
            or receipt["candidate"] != head
            or receipt["attempt"] != attempt
        ):
            raise RuntimeError(f"{receipt_name} calibration identity mismatch")
    _write_once(
        output_root / "supervisor.json",
        _canonical(
            {
                "schema": _SCHEMA,
                "candidate": head,
                "attempt": attempt,
                "status": "PASS",
                "cleanup_enforced": result.cleanup_enforced,
                "child_returncode": result.returncode,
                "child_identity": {
                    "pid": result.child_identity.pid,
                    "boot_id": result.child_identity.boot_id,
                    "start_ticks": result.child_identity.start_ticks,
                    "cmdline": list(result.child_identity.cmdline),
                },
                "telemetry_anomalies": list(result.telemetry_anomalies),
                **result.telemetry_evidence(),
            }
        ),
    )
    inventory = {
        name: _sha(output_root / name)
        for name in (
            "process.json",
            "terminal.json",
            "stdout.log",
            "stderr.log",
            "resource.txt",
            "calibration.json",
            "progress.jsonl",
            "supervisor.json",
        )
    }
    inventory["inventory_identity"] = hashlib.sha256(_canonical(inventory)).hexdigest()
    _write_once(output_root / "inventory.json", _canonical(inventory))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--oracle-step-probe", action="store_true")
    parser.add_argument("--attempt")
    parser.add_argument("output_root", type=Path)
    args = parser.parse_args(argv)
    if args.probe and args.oracle_step_probe:
        parser.error("--probe and --oracle-step-probe are mutually exclusive")
    if args.attempt is None:
        parser.error("--attempt is required")
    if args.oracle_step_probe:
        if args.worker:
            _run_oracle_step_probe(args.output_root, attempt=args.attempt)
        else:
            _supervise_oracle_step_probe(args.output_root, attempt=args.attempt)
    elif args.worker:
        run(args.output_root, attempt=args.attempt, supervised=True, probe=args.probe)
    else:
        _supervise_run(args.output_root, attempt=args.attempt, probe=args.probe)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
