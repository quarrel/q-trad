"""Chronological fold contracts for the R1.C offline evaluator."""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import ClassVar

from qtrad.domain.events import JsonValue, to_json_value
from qtrad.domain.time import require_utc

FOLD_DATASET_CONTRACT = "qtrad-research-folds-v1"


@dataclass(frozen=True, slots=True)
class Fold:
    """One expanding fold with explicit target membership and embargo boundary."""

    fold_id: str
    training_start: datetime
    training_cutoff: datetime
    validation_start: datetime
    validation_end: datetime
    embargo_end: datetime
    training_target_ids: tuple[str, ...]
    validation_target_ids: tuple[str, ...]
    holdout_excluded: bool
    membership_hash: str

    def __post_init__(self) -> None:
        for value, field in (
            (self.training_start, "fold training_start"),
            (self.training_cutoff, "fold training_cutoff"),
            (self.validation_start, "fold validation_start"),
            (self.validation_end, "fold validation_end"),
            (self.embargo_end, "fold embargo_end"),
        ):
            require_utc(value, field)
        if not self.fold_id:
            raise ValueError("fold ID must be non-empty")
        if not (
            self.training_start
            <= self.training_cutoff
            <= self.validation_start
            < self.validation_end
        ):
            raise ValueError("fold chronology is invalid")
        if self.embargo_end < self.validation_start or self.embargo_end > self.validation_end:
            raise ValueError("fold embargo end must be within the validation interval")
        if not self.holdout_excluded:
            raise ValueError("every R1 fold must declare holdout exclusion")
        if len(set(self.training_target_ids)) != len(self.training_target_ids):
            raise ValueError("fold training membership contains duplicate targets")
        if len(set(self.validation_target_ids)) != len(self.validation_target_ids):
            raise ValueError("fold validation membership contains duplicate targets")
        if set(self.training_target_ids) & set(self.validation_target_ids):
            raise ValueError("fold training and validation memberships must be disjoint")
        expected = membership_hash(self.training_target_ids, self.validation_target_ids)
        if self.membership_hash != expected:
            raise ValueError("fold membership hash does not match its rows")

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "fold_id": self.fold_id,
            "training_start": self.training_start.isoformat(),
            "training_cutoff": self.training_cutoff.isoformat(),
            "validation_start": self.validation_start.isoformat(),
            "validation_end": self.validation_end.isoformat(),
            "embargo_end": self.embargo_end.isoformat(),
            "training_target_ids": to_json_value(sorted(self.training_target_ids)),
            "validation_target_ids": to_json_value(sorted(self.validation_target_ids)),
            "holdout_excluded": self.holdout_excluded,
            "membership_hash": self.membership_hash,
        }


@dataclass(frozen=True, slots=True)
class FoldDataset:
    """Immutable, hash-bound fold definitions for one target/configuration pair."""

    folds: tuple[Fold, ...]
    target_dataset_id: str
    foundation_configuration_id: str
    dataset_id: str

    CONTRACT: ClassVar[str] = FOLD_DATASET_CONTRACT

    def __post_init__(self) -> None:
        if tuple(sorted(self.folds, key=lambda fold: fold.validation_start)) != self.folds:
            raise ValueError("folds must use chronological ordering")
        expected = _dataset_hash(
            self.folds,
            target_dataset_id=self.target_dataset_id,
            foundation_configuration_id=self.foundation_configuration_id,
        )
        if self.dataset_id != expected:
            raise ValueError("fold dataset ID does not match its semantic content")

    @classmethod
    def create(
        cls,
        folds: Sequence[Fold],
        *,
        target_dataset_id: str,
        foundation_configuration_id: str,
    ) -> "FoldDataset":
        ordered = tuple(sorted(folds, key=lambda fold: fold.validation_start))
        return cls(
            folds=ordered,
            target_dataset_id=target_dataset_id,
            foundation_configuration_id=foundation_configuration_id,
            dataset_id=_dataset_hash(
                ordered,
                target_dataset_id=target_dataset_id,
                foundation_configuration_id=foundation_configuration_id,
            ),
        )


def membership_hash(
    training_target_ids: Sequence[str], validation_target_ids: Sequence[str]
) -> str:
    """Hash canonical, order-independent membership while retaining explicit row lists."""

    return _hash_json(
        {
            "training_target_ids": sorted(training_target_ids),
            "validation_target_ids": sorted(validation_target_ids),
        }
    )


def _dataset_hash(
    folds: Sequence[Fold],
    *,
    target_dataset_id: str,
    foundation_configuration_id: str,
) -> str:
    return _hash_json(
        {
            "contract": FOLD_DATASET_CONTRACT,
            "schema_version": 1,
            "target_dataset_id": target_dataset_id,
            "foundation_configuration_id": foundation_configuration_id,
            "folds": [fold.as_json() for fold in folds],
        }
    )


def _hash_json(value: object) -> str:
    canonical = to_json_value(value)
    return sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
