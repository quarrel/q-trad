"""Causal expanding folds and deterministic out-of-fold probe forecasts."""

import json
from datetime import datetime, timedelta
from hashlib import sha256

from qtrad.domain.events import to_json_value
from qtrad.domain.folds import FOLD_DATASET_CONTRACT, Fold, FoldDataset, membership_hash
from qtrad.domain.forecasts import ForecastDataset, ForecastRow, ReturnUnit
from qtrad.domain.foundation import (
    FoundationConfig,
    PanelDataset,
    PanelStatus,
    ReturnDisposition,
    TargetDataset,
)

ZERO_RETURN_MODEL_CONTRACT = "DETERMINISTIC_PROBE"
ZERO_RETURN_MODEL_ID = sha256(b"zero-return-v1").hexdigest()
DEFAULT_PROBE_EXPERIMENT_ID = "r1-c-zero-return-probe"


def build_expanding_folds(
    targets: TargetDataset,
    config: FoundationConfig,
    *,
    validation_duration: timedelta | None = None,
) -> FoldDataset:
    """Build expanding folds with maturity-based training eligibility and embargo."""

    _require_target_dataset(targets, config)
    duration = (
        config.minimum_validation_duration if validation_duration is None else validation_duration
    )
    if duration <= timedelta(0):
        raise ValueError("validation duration must be positive")
    holdout_start, holdout_end = config.holdout_range
    if holdout_start < config.range_start or holdout_end > config.range_end:
        raise ValueError("holdout range must be contained in the foundation range")

    primary_horizon = config.primary_vertical_horizon
    target_rows = tuple(
        row
        for row in targets.rows
        if row.horizon == primary_horizon and row.target_basis is config.target_basis
    )
    if len({row.target_id for row in target_rows}) != len(target_rows):
        raise ValueError("target dataset contains duplicate target identities")

    folds: list[Fold] = []
    validation_start = config.range_start + config.minimum_training_duration + config.embargo
    while validation_start < holdout_start:
        validation_end = validation_start + duration
        if validation_end > holdout_start:
            break
        training_cutoff = validation_start - config.embargo
        training_ids = tuple(
            sorted(
                row.target_id
                for row in target_rows
                if config.range_start <= row.decision_time < training_cutoff
                and row.target_available_at <= training_cutoff
                and row.target_end_time <= training_cutoff
                and row.return_disposition is ReturnDisposition.VALID
                and not _in_holdout(row.decision_time, config.holdout_range)
            )
        )
        validation_ids = tuple(
            sorted(
                row.target_id
                for row in target_rows
                if validation_start <= row.decision_time < validation_end
                and not _in_holdout(row.decision_time, config.holdout_range)
            )
        )
        if training_ids and validation_ids:
            membership = membership_hash(training_ids, validation_ids)
            fold_id = _hash_json(
                {
                    "contract": FOLD_DATASET_CONTRACT,
                    "schema_version": 1,
                    "training_start": config.range_start,
                    "training_cutoff": training_cutoff,
                    "validation_start": validation_start,
                    "validation_end": validation_end,
                    "embargo_end": validation_start,
                    "membership_hash": membership,
                }
            )
            folds.append(
                Fold(
                    fold_id=fold_id,
                    training_start=config.range_start,
                    training_cutoff=training_cutoff,
                    validation_start=validation_start,
                    validation_end=validation_end,
                    embargo_end=validation_start,
                    training_target_ids=training_ids,
                    validation_target_ids=validation_ids,
                    holdout_excluded=True,
                    membership_hash=membership,
                )
            )
        validation_start = validation_end + config.embargo

    if not folds:
        raise ValueError("no scientifically valid expanding folds are available")
    return FoldDataset.create(
        folds,
        target_dataset_id=targets.dataset_id,
        foundation_configuration_id=config.configuration_id,
    )


def build_zero_return_forecasts(
    panel: PanelDataset,
    targets: TargetDataset,
    folds: FoldDataset,
    config: FoundationConfig,
    *,
    experiment_id: str = DEFAULT_PROBE_EXPERIMENT_ID,
) -> ForecastDataset:
    """Emit deterministic zero-return forecasts for fold validation rows only."""

    _require_panel_and_targets(panel, targets, folds, config)
    target_by_id = {row.target_id: row for row in targets.rows}
    if len(target_by_id) != len(targets.rows):
        raise ValueError("target dataset contains duplicate target identities")
    panel_by_key = {(row.instrument_id, row.decision_time, row.basis): row for row in panel.rows}
    if len(panel_by_key) != len(panel.rows):
        raise ValueError("panel dataset contains duplicate panel identities")
    rows: list[ForecastRow] = []
    for fold in folds.folds:
        for target_id in fold.validation_target_ids:
            if target_id not in target_by_id:
                raise ValueError("fold validation references an unknown target")
            target = target_by_id[target_id]
            if not fold.validation_start <= target.decision_time < fold.validation_end:
                raise ValueError("fold validation membership is outside its interval")
            if _in_holdout(target.decision_time, config.holdout_range):
                raise ValueError("holdout target entered forecast validation membership")
            if (
                target.horizon != config.primary_vertical_horizon
                or target.target_basis is not config.target_basis
            ):
                raise ValueError("fold validation contains an unsupported target")
            panel_row = panel_by_key.get(
                (target.instrument_id, target.decision_time, config.target_basis)
            )
            if panel_row is None or panel_row.status is not PanelStatus.OBSERVED:
                continue
            if panel_row.feature_data_asof > target.decision_time:
                raise ValueError("forecast feature cutoff is after the decision time")
            rows.append(
                ForecastRow.create(
                    instrument_id=target.instrument_id,
                    decision_time=target.decision_time,
                    horizon=target.horizon,
                    expected_return=0.0,
                    return_unit=ReturnUnit.LOG_RETURN,
                    feature_data_asof=panel_row.feature_data_asof,
                    training_cutoff=fold.training_cutoff,
                    observation_dataset_id=config.observation_dataset_id,
                    panel_dataset_id=panel.dataset_id,
                    target_dataset_id=targets.dataset_id,
                    target_id=target.target_id,
                    fold_dataset_id=folds.dataset_id,
                    experiment_id=experiment_id,
                    fold_id=fold.fold_id,
                    model_id=ZERO_RETURN_MODEL_ID,
                    model_contract=ZERO_RETURN_MODEL_CONTRACT,
                )
            )
    return ForecastDataset.create(
        rows,
        observation_dataset_id=config.observation_dataset_id,
        panel_dataset_id=panel.dataset_id,
        target_dataset_id=targets.dataset_id,
        fold_dataset_id=folds.dataset_id,
    )


def _require_target_dataset(targets: TargetDataset, config: FoundationConfig) -> None:
    if targets.observation_dataset_id != config.observation_dataset_id:
        raise ValueError("target dataset observation lineage does not match configuration")
    if targets.foundation_configuration_id != config.configuration_id:
        raise ValueError("target dataset configuration lineage does not match configuration")


def _require_panel_and_targets(
    panel: PanelDataset,
    targets: TargetDataset,
    folds: FoldDataset,
    config: FoundationConfig,
) -> None:
    _require_target_dataset(targets, config)
    if panel.observation_dataset_id != config.observation_dataset_id:
        raise ValueError("panel dataset observation lineage does not match configuration")
    if panel.foundation_configuration_id != config.configuration_id:
        raise ValueError("panel dataset configuration lineage does not match configuration")
    if folds.target_dataset_id != targets.dataset_id:
        raise ValueError("fold dataset target lineage does not match targets")
    if folds.foundation_configuration_id != config.configuration_id:
        raise ValueError("fold dataset configuration lineage does not match configuration")


def _in_holdout(decision_time: datetime, holdout: tuple[datetime, datetime]) -> bool:
    return holdout[0] <= decision_time < holdout[1]


def _hash_json(value: object) -> str:
    canonical = to_json_value(value)
    return sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
