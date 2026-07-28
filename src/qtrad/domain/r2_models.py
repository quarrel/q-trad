"""Immutable contracts for R2.C fold-local preprocessing and alpha selection."""

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite


class FitDisposition(StrEnum):
    READY = "READY"
    INSUFFICIENT_TRAINING = "INSUFFICIENT_TRAINING"
    INSUFFICIENT_INNER_VALIDATION = "INSUFFICIENT_INNER_VALIDATION"
    DEGENERATE_TARGET = "DEGENERATE_TARGET"
    DEGENERATE_FEATURE_MATRIX = "DEGENERATE_FEATURE_MATRIX"
    NON_FINITE_MATRIX = "NON_FINITE_MATRIX"


@dataclass(frozen=True, slots=True)
class PreprocessingFit:
    """Training-only parameters required to replay R2.C transformations."""

    feature_names: tuple[str, ...]
    medians: tuple[float, ...]
    means: tuple[float, ...]
    scales: tuple[float, ...]
    active_feature_names: tuple[str, ...]
    training_row_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.feature_names or len(set(self.feature_names)) != len(self.feature_names):
            raise ValueError("feature names must be non-empty and unique")
        if len(self.medians) != len(self.feature_names):
            raise ValueError("medians must align with feature names")
        if len(self.means) != len(self.active_feature_names) or len(self.scales) != len(
            self.active_feature_names
        ):
            raise ValueError("active scaling parameters must align")
        if not self.active_feature_names or not set(self.active_feature_names) <= set(
            self.feature_names
        ):
            raise ValueError("active features must be a non-empty feature subset")
        if not self.training_row_ids or len(set(self.training_row_ids)) != len(
            self.training_row_ids
        ):
            raise ValueError("training membership must be non-empty and unique")
        if any(not isfinite(value) for value in (*self.medians, *self.means, *self.scales)):
            raise ValueError("preprocessing values must be finite")
        if any(value <= 0 for value in self.scales):
            raise ValueError("active feature scales must be positive")


@dataclass(frozen=True, slots=True)
class AlphaCandidateScore:
    alpha: float
    loss: float
    inner_fit_row_ids: tuple[str, ...]
    inner_validation_row_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isfinite(self.alpha) or self.alpha <= 0:
            raise ValueError("alpha must be finite and positive")
        if not isfinite(self.loss) or self.loss < 0:
            raise ValueError("alpha loss must be finite and non-negative")
        if not self.inner_fit_row_ids or not self.inner_validation_row_ids:
            raise ValueError("inner memberships must be non-empty")


@dataclass(frozen=True, slots=True)
class AlphaSelection:
    disposition: FitDisposition
    outer_training_row_ids: tuple[str, ...]
    inner_fit_row_ids: tuple[str, ...]
    inner_validation_row_ids: tuple[str, ...]
    inner_preprocessing: PreprocessingFit | None
    candidate_scores: tuple[AlphaCandidateScore, ...]
    selected_alpha: float | None
    outer_preprocessing: PreprocessingFit | None

    def __post_init__(self) -> None:
        if not self.outer_training_row_ids or len(set(self.outer_training_row_ids)) != len(
            self.outer_training_row_ids
        ):
            raise ValueError("outer training membership must be non-empty and unique")
        if set(self.inner_fit_row_ids) & set(self.inner_validation_row_ids):
            raise ValueError("inner memberships must be disjoint")
        if not set((*self.inner_fit_row_ids, *self.inner_validation_row_ids)) <= set(
            self.outer_training_row_ids
        ):
            raise ValueError("inner membership must be contained in outer training")
        if self.disposition is FitDisposition.READY:
            if (
                self.selected_alpha is None
                or self.inner_preprocessing is None
                or self.outer_preprocessing is None
            ):
                raise ValueError("ready selection requires alpha and preprocessing fits")
            if not self.candidate_scores:
                raise ValueError("ready selection requires candidate scores")
        elif any(
            value is not None
            for value in (self.selected_alpha, self.inner_preprocessing, self.outer_preprocessing)
        ):
            raise ValueError("failed selection cannot contain fitted state")
