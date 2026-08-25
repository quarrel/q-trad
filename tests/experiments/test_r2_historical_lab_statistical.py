from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from experiments.r2_historical_lab.statistical import (
    _calibration_is_eligible,
    _fit_calibration,
    _hierarchical_ridge_prediction,
    _select_training,
    _select_validation,
    _target_scales,
)


def test_rolling_training_is_cut_at_fixed_history() -> None:
    fit_time = datetime(2026, 5, 1, tzinfo=UTC)
    frame = pl.DataFrame(
        {
            "decision_time": [
                fit_time - timedelta(days=85),
                fit_time - timedelta(days=84),
                fit_time - timedelta(days=1),
            ]
        }
    )

    selected = _select_training(
        frame,
        fit_time,
        {"recency": "ROLLING", "rolling_history_days": 84},
    )

    assert selected["decision_time"].to_list() == [
        fit_time - timedelta(days=84),
        fit_time - timedelta(days=1),
    ]


def test_causal_volatility_scale_uses_training_floor_and_fallback() -> None:
    training = pl.DataFrame({"realised_std_300s": [0.001, 0.002, None]})
    validation = pl.DataFrame({"realised_std_300s": [0.00001, None, 0.003]})

    train_scale, validation_scale = _target_scales(
        training,
        validation,
        "CAUSAL_VOL_STANDARDISED",
    )

    assert np.all(np.isfinite(train_scale))
    assert np.all(np.isfinite(validation_scale))
    expected_floor = np.sqrt(15.0) * np.quantile(np.asarray([0.001, 0.002]), 0.1)
    expected_median = np.sqrt(15.0) * 0.0015
    assert validation_scale[0] == pytest.approx(expected_floor)
    assert validation_scale[1] == pytest.approx(expected_median)


def test_non_negative_calibration_collapses_adverse_association() -> None:
    slope, intercept = _fit_calibration(
        np.asarray([1.0, 2.0, 3.0]),
        np.asarray([-1.0, -2.0, -3.0]),
        ["a", "a", "a"],
        "NON_NEGATIVE_SLOPE",
    )

    assert slope == 0.0
    assert intercept == 0.0


def test_affine_calibration_recovers_positive_slope_and_intercept() -> None:
    slope, intercept = _fit_calibration(
        np.asarray([1.0, 2.0, 3.0, 4.0]),
        np.asarray([3.0, 5.0, 7.0, 9.0]),
        ["a", "a", "b", "b"],
        "AFFINE",
    )

    assert slope == pytest.approx(2.0)
    assert intercept == pytest.approx(1.0)


def test_development_validation_excludes_targets_maturing_at_terminal_start() -> None:
    terminal_start = datetime(2026, 6, 26, 14, 6, tzinfo=UTC)
    rows = pl.DataFrame(
        {
            "block": ["DEV_3", "DEV_3", "TERMINAL_FORMER_HOLDOUT"],
            "target_available_at": [
                terminal_start - timedelta(microseconds=1),
                terminal_start,
                terminal_start + timedelta(minutes=20),
            ],
        }
    )
    specs = {
        "DEV_3": {"start": "2026-06-12T14:06:00+00:00"},
        "TERMINAL_FORMER_HOLDOUT": {"start": terminal_start.isoformat()},
    }

    development = _select_validation(rows, "DEV_3", specs)
    terminal = _select_validation(rows, "TERMINAL_FORMER_HOLDOUT", specs)

    assert development.height == 1
    assert development["target_available_at"].item() < terminal_start
    assert terminal.height == 1


def test_hierarchical_normal_equations_match_dense_design() -> None:
    random = np.random.default_rng(17)
    base_train = random.normal(size=(18, 4))
    base_validation = random.normal(size=(7, 4))
    targets = random.normal(size=18)
    weights = random.uniform(0.2, 1.5, size=18)
    train_groups = np.asarray([0] * 9 + [1] * 9)
    validation_groups = np.asarray([0, 1, 0, 1, 0, 1, 1])
    train_instruments = np.asarray([0] * 5 + [1] * 4 + [2] * 5 + [3] * 4)
    validation_instruments = np.asarray([0, 2, 1, 3, 0, 2, 3])
    alpha = 0.7
    ratio = 4.0

    prediction, column_count = _hierarchical_ridge_prediction(
        base_train,
        base_validation,
        targets,
        weights,
        train_groups,
        validation_groups,
        train_instruments,
        validation_instruments,
        group_count=2,
        instrument_count=4,
        alpha=alpha,
        instrument_penalty_ratio=ratio,
    )

    def interactions(base: np.ndarray, codes: np.ndarray, count: int) -> np.ndarray:
        identity = np.eye(count)[codes]
        return (identity[:, :, None] * base[:, None, :]).reshape(base.shape[0], -1)

    dense_train = np.column_stack(
        (
            base_train,
            interactions(base_train, train_groups, 2),
            interactions(base_train, train_instruments, 4) / np.sqrt(ratio),
        )
    )
    dense_validation = np.column_stack(
        (
            base_validation,
            interactions(base_validation, validation_groups, 2),
            interactions(base_validation, validation_instruments, 4) / np.sqrt(ratio),
        )
    )
    normal = dense_train.T @ (weights[:, None] * dense_train)
    normal.flat[:: normal.shape[0] + 1] += alpha
    coefficients = np.linalg.solve(normal, dense_train.T @ (weights * targets))

    assert column_count == dense_train.shape[1]
    assert prediction == pytest.approx(dense_validation @ coefficients, abs=1e-10)


def test_calibration_nomination_requires_three_stable_positive_slopes() -> None:
    design = {"calibration_stability_max_ratio": 3.0}
    row = {
        "calibration": "AFFINE",
        "fitted_calibration_slopes_json": "[0.5, 1.0, 1.4]",
    }
    unstable = {
        "calibration": "AFFINE",
        "fitted_calibration_slopes_json": "[0.2, 0.8, 1.0]",
    }
    collapsed = {
        "calibration": "NON_NEGATIVE_SLOPE",
        "fitted_calibration_slopes_json": "[0.5, 0.0, 1.0]",
    }

    assert _calibration_is_eligible(row, design)
    assert not _calibration_is_eligible(unstable, design)
    assert not _calibration_is_eligible(collapsed, design)
