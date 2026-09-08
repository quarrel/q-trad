"""Closed alpha-one Ridge with a training-only population standard deviation."""

from __future__ import annotations

import numpy as np

from .data import Array


def ridge_prediction(training: Array, target: Array, evaluation: Array) -> Array:
    mean = training.mean(axis=0)
    scale = training.std(axis=0)
    scale[scale < 1e-12] = 1
    x = (training - mean) / scale
    target_mean = float(target.mean())
    coefficients = np.linalg.solve(x.T @ x + np.eye(x.shape[1]), x.T @ (target - target_mean))
    return ((evaluation - mean) / scale) @ coefficients + target_mean
