"""Deterministic R2.C training-only preprocessing and chronological alpha selection."""

from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite
from typing import Any

import numpy as np
from sklearn.linear_model import Ridge  # type: ignore[reportMissingTypeStubs]

from qtrad.domain.r2_models import (
    AlphaCandidateScore,
    AlphaSelection,
    FitDisposition,
    PreprocessingFit,
)


@dataclass(frozen=True, slots=True)
class TrainingRow:
    row_id: str
    features: tuple[float | None, ...]
    target: float

    def __post_init__(self) -> None:
        if not self.row_id or not isfinite(self.target):
            raise ValueError("training row requires an identity and finite target")
        if any(value is not None and not isfinite(value) for value in self.features):
            raise ValueError("features must be finite or null")


def select_chronological_alpha(
    rows: Sequence[TrainingRow],
    *,
    feature_names: Sequence[str],
    alpha_grid: Sequence[float],
    minimum_training_rows: int,
    minimum_inner_validation_rows: int,
) -> AlphaSelection:
    """Select a fixed-grid Ridge alpha without using outer validation rows."""
    ordered = tuple(sorted(rows, key=lambda row: row.row_id))
    names = tuple(feature_names)
    if (
        not names
        or len(set(names)) != len(names)
        or len(ordered) != len({row.row_id for row in ordered})
    ):
        raise ValueError("feature names and row identities must be unique and non-empty")
    if any(len(row.features) != len(names) for row in ordered):
        raise ValueError("row features must align with feature names")
    if tuple(sorted(set(alpha_grid))) != tuple(alpha_grid) or any(
        not isfinite(alpha) or alpha <= 0 for alpha in alpha_grid
    ):
        raise ValueError("alpha grid must be unique, ascending, finite and positive")
    ids = tuple(row.row_id for row in ordered)
    split = len(ordered) - minimum_inner_validation_rows
    if len(ordered) < minimum_training_rows or split < minimum_training_rows:
        return _failed(FitDisposition.INSUFFICIENT_TRAINING, ids)
    inner_fit, inner_validation = ordered[:split], ordered[split:]
    if len(inner_validation) < minimum_inner_validation_rows:
        return _failed(
            FitDisposition.INSUFFICIENT_INNER_VALIDATION, ids, inner_fit, inner_validation
        )
    if np.ptp([row.target for row in inner_fit]) == 0:
        return _failed(FitDisposition.DEGENERATE_TARGET, ids, inner_fit, inner_validation)
    inner_preprocessing = fit_preprocessing(inner_fit, names)
    if inner_preprocessing is None:
        return _failed(FitDisposition.DEGENERATE_FEATURE_MATRIX, ids, inner_fit, inner_validation)
    outer_preprocessing = fit_preprocessing(ordered, names)
    if outer_preprocessing is None:
        return _failed(FitDisposition.DEGENERATE_FEATURE_MATRIX, ids, inner_fit, inner_validation)
    x_fit = transform(inner_fit, inner_preprocessing)
    x_validation = transform(inner_validation, inner_preprocessing)
    if not np.isfinite(x_fit).all() or not np.isfinite(x_validation).all():
        return _failed(FitDisposition.NON_FINITE_MATRIX, ids, inner_fit, inner_validation)
    y_fit = np.array([row.target for row in inner_fit], dtype=float)
    y_validation = np.array([row.target for row in inner_validation], dtype=float)
    scores = tuple(
        AlphaCandidateScore(
            alpha=alpha,
            loss=float(
                np.mean((_ridge_predictions(alpha, x_fit, y_fit, x_validation) - y_validation) ** 2)
            ),
            inner_fit_row_ids=tuple(row.row_id for row in inner_fit),
            inner_validation_row_ids=tuple(row.row_id for row in inner_validation),
        )
        for alpha in alpha_grid
    )
    winner = min(scores, key=lambda score: (score.loss, -score.alpha))
    return AlphaSelection(
        FitDisposition.READY,
        ids,
        tuple(row.row_id for row in inner_fit),
        tuple(row.row_id for row in inner_validation),
        inner_preprocessing,
        scores,
        winner.alpha,
        outer_preprocessing,
    )


def fit_preprocessing(
    rows: Sequence[TrainingRow], feature_names: Sequence[str]
) -> PreprocessingFit | None:
    matrix = np.array(
        [[np.nan if value is None else value for value in row.features] for row in rows],
        dtype=float,
    )
    if matrix.ndim != 2 or not len(rows) or not np.isfinite(matrix[~np.isnan(matrix)]).all():
        return None
    medians = np.nanmedian(matrix, axis=0)
    if not np.isfinite(medians).all():
        return None
    filled = np.where(np.isnan(matrix), medians, matrix)
    scales = np.std(filled, axis=0)
    active = scales > 0
    if not active.any():
        return None
    means = np.mean(filled[:, active], axis=0)
    return PreprocessingFit(
        tuple(feature_names),
        tuple(float(value) for value in medians),
        tuple(float(value) for value in means),
        tuple(float(value) for value in scales[active]),
        tuple(name for name, keep in zip(feature_names, active, strict=True) if keep),
        tuple(row.row_id for row in rows),
    )


def transform(rows: Sequence[TrainingRow], fit: PreprocessingFit) -> np.ndarray:
    positions = {name: index for index, name in enumerate(fit.feature_names)}
    matrix = np.array(
        [[np.nan if value is None else value for value in row.features] for row in rows],
        dtype=float,
    )
    filled = np.where(np.isnan(matrix), np.array(fit.medians), matrix)
    active = [positions[name] for name in fit.active_feature_names]
    return (filled[:, active] - np.array(fit.means)) / np.array(fit.scales)


def _ridge_predictions(
    alpha: float, x_fit: np.ndarray, y_fit: np.ndarray, x_validation: np.ndarray
) -> np.ndarray:
    model: Any = Ridge(alpha=alpha, solver="lsqr")
    return np.asarray(model.fit(x_fit, y_fit).predict(x_validation), dtype=float)


def _failed(
    disposition: FitDisposition,
    outer: tuple[str, ...],
    inner_fit: Sequence[TrainingRow] = (),
    inner_validation: Sequence[TrainingRow] = (),
) -> AlphaSelection:
    return AlphaSelection(
        disposition,
        outer,
        tuple(row.row_id for row in inner_fit),
        tuple(row.row_id for row in inner_validation),
        None,
        (),
        None,
        None,
    )
