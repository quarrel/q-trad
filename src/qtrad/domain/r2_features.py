"""Immutable contracts for causal R2 raw features."""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from itertools import pairwise
from math import isfinite
from typing import ClassVar

from qtrad.domain.events import JsonValue, to_json_value
from qtrad.domain.r2_readiness import EvidenceClass, FeatureFamily, R2ExperimentConfig
from qtrad.domain.time import require_utc

R2_FEATURE_DATASET_CONTRACT = "qtrad-r2-features-v1"


@dataclass(frozen=True, slots=True)
class FeatureDefinition:
    """One position in the immutable raw-feature vector."""

    name: str
    family: FeatureFamily
    availability_indicator: bool = False

    def __post_init__(self) -> None:
        if not self.name or not self.name.isascii():
            raise ValueError("feature name must be non-empty ASCII")

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "name": self.name,
            "family": self.family.value,
            "availability_indicator": self.availability_indicator,
        }


@dataclass(frozen=True, slots=True)
class RawFeatureValue:
    """One untransformed value and the observations used to calculate it."""

    name: str
    value: float | None
    source_event_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("raw feature name must be non-empty")
        if self.value is not None and not isfinite(self.value):
            raise ValueError("raw feature values must be finite or null")
        if len(set(self.source_event_ids)) != len(self.source_event_ids):
            raise ValueError("raw feature lineage must not contain duplicate events")

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "name": self.name,
            "value": self.value,
            "source_event_ids": list(self.source_event_ids),
        }


@dataclass(frozen=True, slots=True)
class RawFeatureRow:
    """One target-relative feature vector at a single causal cutoff."""

    target_instrument_id: str
    decision_time: datetime
    feature_data_asof: datetime
    latest_feature_bar_end: datetime
    feature_set_id: str
    values: tuple[RawFeatureValue, ...]

    def __post_init__(self) -> None:
        require_utc(self.decision_time, "feature decision_time")
        require_utc(self.feature_data_asof, "feature data cutoff")
        require_utc(self.latest_feature_bar_end, "latest feature bar end")
        if not self.target_instrument_id or not self.feature_set_id:
            raise ValueError("feature row identity must be non-empty")
        if self.latest_feature_bar_end > self.feature_data_asof:
            raise ValueError("latest feature bar cannot follow the feature cutoff")
        if len({item.name for item in self.values}) != len(self.values):
            raise ValueError("feature row names must be unique")

    def semantic_key(self) -> tuple[object, ...]:
        return (
            self.target_instrument_id,
            self.decision_time,
            self.feature_data_asof,
            self.latest_feature_bar_end,
            self.feature_set_id,
        )

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "target_instrument_id": self.target_instrument_id,
            "decision_time": self.decision_time.isoformat(),
            "feature_data_asof": self.feature_data_asof.isoformat(),
            "latest_feature_bar_end": self.latest_feature_bar_end.isoformat(),
            "feature_set_id": self.feature_set_id,
            "values": [item.as_json() for item in self.values],
        }


@dataclass(frozen=True, slots=True)
class R2FeatureDataset:
    """Canonical OOF raw-feature rows over independently verified R1 children."""

    rows: tuple[RawFeatureRow, ...]
    feature_schema: tuple[FeatureDefinition, ...]
    raw_feature_schema_id: str
    observation_dataset_id: str
    panel_dataset_id: str
    target_dataset_id: str
    fold_dataset_id: str
    experiment_configuration_id: str
    evidence_class: EvidenceClass
    holdout_excluded: bool
    dataset_id: str

    CONTRACT: ClassVar[str] = R2_FEATURE_DATASET_CONTRACT
    SCHEMA_VERSION: ClassVar[int] = 1

    def __post_init__(self) -> None:
        if not self.holdout_excluded:
            raise ValueError("an OOF feature dataset must exclude the locked holdout")
        ordered = tuple(sorted(self.rows, key=RawFeatureRow.semantic_key))
        if ordered != self.rows or len({row.semantic_key() for row in self.rows}) != len(self.rows):
            raise ValueError("feature rows must have unique canonical ordering")
        if len({item.name for item in self.feature_schema}) != len(self.feature_schema):
            raise ValueError("feature schema names must be unique")
        expected_names = tuple(item.name for item in self.feature_schema)
        for row in self.rows:
            if tuple(item.name for item in row.values) != expected_names:
                raise ValueError("feature row order differs from its raw-feature schema")
        if self.raw_feature_schema_id != feature_schema_id(self.feature_schema):
            raise ValueError("raw-feature schema ID does not match its definitions")
        expected = feature_dataset_id(
            rows=self.rows,
            feature_schema=self.feature_schema,
            observation_dataset_id=self.observation_dataset_id,
            panel_dataset_id=self.panel_dataset_id,
            target_dataset_id=self.target_dataset_id,
            fold_dataset_id=self.fold_dataset_id,
            experiment_configuration_id=self.experiment_configuration_id,
            evidence_class=self.evidence_class,
            holdout_excluded=self.holdout_excluded,
        )
        if self.dataset_id != expected:
            raise ValueError("feature dataset ID does not match its semantic content")

    @classmethod
    def create(
        cls,
        rows: Sequence[RawFeatureRow],
        *,
        feature_schema: Sequence[FeatureDefinition],
        observation_dataset_id: str,
        panel_dataset_id: str,
        target_dataset_id: str,
        fold_dataset_id: str,
        experiment_configuration_id: str,
        evidence_class: EvidenceClass,
    ) -> "R2FeatureDataset":
        ordered_rows = tuple(sorted(rows, key=RawFeatureRow.semantic_key))
        schema = tuple(feature_schema)
        return cls(
            rows=ordered_rows,
            feature_schema=schema,
            raw_feature_schema_id=feature_schema_id(schema),
            observation_dataset_id=observation_dataset_id,
            panel_dataset_id=panel_dataset_id,
            target_dataset_id=target_dataset_id,
            fold_dataset_id=fold_dataset_id,
            experiment_configuration_id=experiment_configuration_id,
            evidence_class=evidence_class,
            holdout_excluded=True,
            dataset_id=feature_dataset_id(
                rows=ordered_rows,
                feature_schema=schema,
                observation_dataset_id=observation_dataset_id,
                panel_dataset_id=panel_dataset_id,
                target_dataset_id=target_dataset_id,
                fold_dataset_id=fold_dataset_id,
                experiment_configuration_id=experiment_configuration_id,
                evidence_class=evidence_class,
                holdout_excluded=True,
            ),
        )

    def manifest_json(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": self.SCHEMA_VERSION,
            "dataset_id": self.dataset_id,
            "raw_feature_schema_id": self.raw_feature_schema_id,
            "feature_schema": [item.as_json() for item in self.feature_schema],
            "observation_dataset_id": self.observation_dataset_id,
            "panel_dataset_id": self.panel_dataset_id,
            "target_dataset_id": self.target_dataset_id,
            "fold_dataset_id": self.fold_dataset_id,
            "experiment_configuration_id": self.experiment_configuration_id,
            "evidence_class": self.evidence_class.value,
            "holdout_excluded": self.holdout_excluded,
            "row_count": len(self.rows),
        }


def feature_registry(experiment: R2ExperimentConfig) -> tuple[FeatureDefinition, ...]:
    """Return the one deterministic union schema required by declared feature sets."""

    families = {
        family for feature_set in experiment.feature_sets for family in feature_set.families
    }
    windows = tuple(sorted(experiment.feature_windows))
    definitions: list[FeatureDefinition] = []
    if FeatureFamily.LOCAL_RETURNS in families:
        for window in windows:
            suffix = _window_suffix(window)
            definitions.extend(
                (
                    FeatureDefinition(f"return_{suffix}", FeatureFamily.LOCAL_RETURNS),
                    FeatureDefinition(
                        f"return_{suffix}_available",
                        FeatureFamily.LOCAL_RETURNS,
                        availability_indicator=True,
                    ),
                )
            )
        for short, long in pairwise(windows):
            definitions.append(
                FeatureDefinition(
                    f"return_contrast_{_window_suffix(short)}_{_window_suffix(long)}",
                    FeatureFamily.LOCAL_RETURNS,
                )
            )
    if FeatureFamily.LOCAL_VOLATILITY_RANGE in families:
        for window in windows:
            suffix = _window_suffix(window)
            for stem in (
                "realised_std",
                "mean_absolute_return",
                "mean_log_range",
                "return_sign_balance",
            ):
                definitions.append(
                    FeatureDefinition(f"{stem}_{suffix}", FeatureFamily.LOCAL_VOLATILITY_RANGE)
                )
            definitions.extend(
                (
                    FeatureDefinition(
                        f"available_interval_count_{suffix}",
                        FeatureFamily.LOCAL_VOLATILITY_RANGE,
                    ),
                    FeatureDefinition(
                        f"window_coverage_{suffix}", FeatureFamily.LOCAL_VOLATILITY_RANGE
                    ),
                )
            )
    if FeatureFamily.TIME_AVAILABILITY in families:
        for name in (
            "utc_minute_sin",
            "utc_minute_cos",
            "utc_day_sin",
            "utc_day_cos",
            "source_active",
            "target_feature_missing_fraction",
            "cross_market_available_count",
            "quality_healthy",
            "gap_disposition_present",
        ):
            definitions.append(FeatureDefinition(name, FeatureFamily.TIME_AVAILABILITY))
    if FeatureFamily.SPREAD in families:
        for name in (
            "close_spread",
            "spread_fraction",
            "spread_bps",
            "rolling_spread_mean",
            "rolling_spread_change",
            "spread_coverage",
        ):
            definitions.append(FeatureDefinition(name, FeatureFamily.SPREAD))
    if FeatureFamily.QUOTE_IMBALANCE in families:
        definitions.extend(
            (
                FeatureDefinition("quote_imbalance", FeatureFamily.QUOTE_IMBALANCE),
                FeatureDefinition(
                    "quote_imbalance_available",
                    FeatureFamily.QUOTE_IMBALANCE,
                    availability_indicator=True,
                ),
            )
        )
    if FeatureFamily.POOLED_CROSS_ASSET in families:
        for window in windows:
            suffix = _window_suffix(window)
            for stem in (
                "loo_mean_return",
                "loo_median_return",
                "loo_return_dispersion",
                "loo_positive_proportion",
                "loo_available_count",
                "loo_market_group_mean_return",
                "loo_market_group_dispersion",
                "loo_market_group_available_count",
                "vix_context_return",
            ):
                definitions.append(
                    FeatureDefinition(f"{stem}_{suffix}", FeatureFamily.POOLED_CROSS_ASSET)
                )
        definitions.extend(
            (
                FeatureDefinition("cross_market_missing_count", FeatureFamily.POOLED_CROSS_ASSET),
                FeatureDefinition(
                    "cross_market_source_active_count", FeatureFamily.POOLED_CROSS_ASSET
                ),
            )
        )
    return tuple(definitions)


def feature_schema_id(schema: Sequence[FeatureDefinition]) -> str:
    return _hash_json(
        {
            "contract": "qtrad-r2-raw-feature-schema-v1",
            "features": [item.as_json() for item in schema],
        }
    )


def feature_set_id(experiment_id: str, name: str, schema: Sequence[FeatureDefinition]) -> str:
    return _hash_json(
        {
            "contract": "qtrad-r2-feature-set-v1",
            "experiment_configuration_id": experiment_id,
            "name": name,
            "raw_feature_schema_id": feature_schema_id(schema),
        }
    )


def feature_dataset_id(
    *,
    rows: Sequence[RawFeatureRow],
    feature_schema: Sequence[FeatureDefinition],
    observation_dataset_id: str,
    panel_dataset_id: str,
    target_dataset_id: str,
    fold_dataset_id: str,
    experiment_configuration_id: str,
    evidence_class: EvidenceClass,
    holdout_excluded: bool,
) -> str:
    return _hash_json(
        {
            "contract": R2_FEATURE_DATASET_CONTRACT,
            "schema_version": 1,
            "rows": [row.as_json() for row in rows],
            "feature_schema": [item.as_json() for item in feature_schema],
            "observation_dataset_id": observation_dataset_id,
            "panel_dataset_id": panel_dataset_id,
            "target_dataset_id": target_dataset_id,
            "fold_dataset_id": fold_dataset_id,
            "experiment_configuration_id": experiment_configuration_id,
            "evidence_class": evidence_class.value,
            "holdout_excluded": holdout_excluded,
        }
    )


def _window_suffix(window: timedelta) -> str:
    seconds = int(window.total_seconds())
    if window <= timedelta(0) or window != timedelta(seconds=seconds):
        raise ValueError("feature windows must contain positive whole seconds")
    return f"{seconds}s"


def _hash_json(value: object) -> str:
    canonical = to_json_value(value)
    return sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
