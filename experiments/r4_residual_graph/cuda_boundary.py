"""Pre-Torch deterministic CUDA process-boundary configuration."""

from __future__ import annotations

import os

MAX_SCHEDULABLE_CPU_CONCURRENCY = 8
_CPU_THREAD_ENVIRONMENT = ("OMP_NUM_THREADS", "MKL_NUM_THREADS")

CUBLAS_WORKSPACE_CONFIG = ":4096:8"
DETERMINISTIC_CUDA_POLICY: tuple[tuple[str, str | bool], ...] = (
    ("cublas_workspace_config", CUBLAS_WORKSPACE_CONFIG),
    ("deterministic_algorithms", True),
    ("cudnn_deterministic", True),
    ("cudnn_benchmark", False),
    ("cuda_matmul_allow_tf32", False),
    ("cudnn_allow_tf32", False),
    ("float32_matmul_precision", "highest"),
)


def configure_pre_torch_environment() -> None:
    """Bind deterministic CUDA and CPU thread settings before Torch import."""
    current = os.environ.get("CUBLAS_WORKSPACE_CONFIG")
    if current not in {None, CUBLAS_WORKSPACE_CONFIG}:
        raise RuntimeError("CUBLAS_WORKSPACE_CONFIG conflicts with the R4 deterministic runtime")
    os.environ["CUBLAS_WORKSPACE_CONFIG"] = CUBLAS_WORKSPACE_CONFIG
    for name in _CPU_THREAD_ENVIRONMENT:
        configured = os.environ.get(name)
        if configured is not None and int(configured) > MAX_SCHEDULABLE_CPU_CONCURRENCY:
            raise RuntimeError(f"{name} exceeds the R4 CPU concurrency cap")
        os.environ[name] = str(MAX_SCHEDULABLE_CPU_CONCURRENCY)


configure_pre_torch_environment()
PRE_TORCH_ENVIRONMENT_CONFIGURED = True
