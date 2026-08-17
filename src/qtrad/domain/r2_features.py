"""Immutable contracts for causal R2 raw features."""

import json
from collections.abc import Sequence
from dataclasses import InitVar, dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from itertools import pairwise
from math import isfinite
from typing import Any, ClassVar, cast

from qtrad.domain.events import JsonValue, to_json_value
from qtrad.domain.market_data import MarketDataSourceClass
from qtrad.domain.r2_readiness import EvidenceClass, FeatureFamily, R2ExperimentConfig
from qtrad.domain.time import require_utc

R2_FEATURE_DATASET_CONTRACT = "qtrad-r2-features-v2"
R2_FEATURE_VERIFICATION_RECEIPT_CONTRACT = "qtrad-r2-feature-verification-receipt-v1"
_VERIFIED_FEATURE_DATASET_TOKEN = object()


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
        if any(not item for item in self.source_event_ids):
            raise ValueError("raw feature lineage identifiers must be non-empty")

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
            self.decision_time,
            self.target_instrument_id,
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
    feature_set_name: str
    feature_set_id: str
    raw_feature_schema_id: str
    observation_dataset_id: str
    panel_dataset_id: str
    target_dataset_id: str
    fold_dataset_id: str
    experiment_configuration_id: str
    evidence_class: EvidenceClass
    holdout_excluded: bool
    dataset_id: str
    market_data_source_class: MarketDataSourceClass = MarketDataSourceClass.IG_NATIVE_CAPTURE
    _identity_capability: InitVar[object | None] = None

    CONTRACT: ClassVar[str] = R2_FEATURE_DATASET_CONTRACT
    SCHEMA_VERSION: ClassVar[int] = 2

    def __post_init__(self, _identity_capability: object) -> None:
        if not self.feature_set_name or not self.feature_set_id:
            raise ValueError("feature dataset requires explicit feature-set identity")
        if not self.holdout_excluded:
            raise ValueError("an OOF feature dataset must exclude the locked holdout")
        ordered = tuple(sorted(self.rows, key=RawFeatureRow.semantic_key))
        if ordered != self.rows or len({row.semantic_key() for row in self.rows}) != len(self.rows):
            raise ValueError("feature rows must have unique canonical ordering")
        if len({item.name for item in self.feature_schema}) != len(self.feature_schema):
            raise ValueError("feature schema names must be unique")
        expected_names = tuple(item.name for item in self.feature_schema)
        expected_set_id = feature_set_id(
            self.experiment_configuration_id,
            self.feature_set_name,
            self.feature_schema,
            self.market_data_source_class,
        )
        if self.feature_set_id != expected_set_id:
            raise ValueError("feature set ID does not match its declared name and schema")
        for row in self.rows:
            if row.feature_set_id != self.feature_set_id:
                raise ValueError("feature row feature-set ID differs from its dataset")
            if tuple(item.name for item in row.values) != expected_names:
                raise ValueError("feature row order differs from its raw-feature schema")
        if self.raw_feature_schema_id != feature_schema_id(self.feature_schema):
            raise ValueError("raw-feature schema ID does not match its definitions")
        if _identity_capability is _VERIFIED_FEATURE_DATASET_TOKEN:
            return
        expected = feature_dataset_id(
            rows=self.rows,
            feature_schema=self.feature_schema,
            feature_set_name=self.feature_set_name,
            feature_set_identity=self.feature_set_id,
            observation_dataset_id=self.observation_dataset_id,
            panel_dataset_id=self.panel_dataset_id,
            target_dataset_id=self.target_dataset_id,
            fold_dataset_id=self.fold_dataset_id,
            experiment_configuration_id=self.experiment_configuration_id,
            evidence_class=self.evidence_class,
            market_data_source_class=self.market_data_source_class,
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
        feature_set_name: str,
        feature_set_id: str | None = None,
        observation_dataset_id: str,
        panel_dataset_id: str,
        target_dataset_id: str,
        fold_dataset_id: str,
        experiment_configuration_id: str,
        evidence_class: EvidenceClass,
        market_data_source_class: MarketDataSourceClass = MarketDataSourceClass.IG_NATIVE_CAPTURE,
    ) -> "R2FeatureDataset":
        ordered_rows = tuple(sorted(rows, key=RawFeatureRow.semantic_key))
        schema = tuple(feature_schema)
        expected_set_id = _canonical_feature_set_id(
            experiment_configuration_id,
            feature_set_name,
            schema,
            market_data_source_class,
        )
        if feature_set_id is not None and feature_set_id != expected_set_id:
            raise ValueError("feature set ID does not match its declared name and schema")
        resolved_set_id = expected_set_id
        dataset_id = _canonical_feature_dataset_id(
            rows=ordered_rows,
            feature_schema=schema,
            feature_set_name=feature_set_name,
            feature_set_identity=resolved_set_id,
            observation_dataset_id=observation_dataset_id,
            panel_dataset_id=panel_dataset_id,
            target_dataset_id=target_dataset_id,
            fold_dataset_id=fold_dataset_id,
            experiment_configuration_id=experiment_configuration_id,
            evidence_class=evidence_class,
            market_data_source_class=market_data_source_class,
            holdout_excluded=True,
        )
        return cls(
            rows=ordered_rows,
            feature_schema=schema,
            feature_set_name=feature_set_name,
            feature_set_id=resolved_set_id,
            raw_feature_schema_id=feature_schema_id(schema),
            observation_dataset_id=observation_dataset_id,
            panel_dataset_id=panel_dataset_id,
            target_dataset_id=target_dataset_id,
            fold_dataset_id=fold_dataset_id,
            experiment_configuration_id=experiment_configuration_id,
            evidence_class=evidence_class,
            market_data_source_class=market_data_source_class,
            holdout_excluded=True,
            dataset_id=dataset_id,
        )

    @classmethod
    def _from_verified_rows(
        cls,
        rows: Sequence[RawFeatureRow],
        *,
        feature_schema: Sequence[FeatureDefinition],
        feature_set_name: str,
        feature_set_id: str,
        observation_dataset_id: str,
        panel_dataset_id: str,
        target_dataset_id: str,
        fold_dataset_id: str,
        experiment_configuration_id: str,
        evidence_class: EvidenceClass,
        dataset_id: str,
        market_data_source_class: MarketDataSourceClass = MarketDataSourceClass.IG_NATIVE_CAPTURE,
        _capability: object | None = None,
    ) -> "R2FeatureDataset":
        if _capability is not _VERIFIED_FEATURE_DATASET_TOKEN:
            raise ValueError(
                "verified feature dataset construction requires an internal capability"
            )
        return cls(
            rows=tuple(rows),
            feature_schema=tuple(feature_schema),
            feature_set_name=feature_set_name,
            feature_set_id=feature_set_id,
            raw_feature_schema_id=feature_schema_id(feature_schema),
            observation_dataset_id=observation_dataset_id,
            panel_dataset_id=panel_dataset_id,
            target_dataset_id=target_dataset_id,
            fold_dataset_id=fold_dataset_id,
            experiment_configuration_id=experiment_configuration_id,
            evidence_class=evidence_class,
            market_data_source_class=market_data_source_class,
            holdout_excluded=True,
            dataset_id=dataset_id,
            _identity_capability=_VERIFIED_FEATURE_DATASET_TOKEN,
        )

    def manifest_json(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": self.SCHEMA_VERSION,
            "dataset_id": self.dataset_id,
            "feature_set_name": self.feature_set_name,
            "feature_set_id": self.feature_set_id,
            "raw_feature_schema_id": self.raw_feature_schema_id,
            "feature_schema": [item.as_json() for item in self.feature_schema],
            "observation_dataset_id": self.observation_dataset_id,
            "panel_dataset_id": self.panel_dataset_id,
            "target_dataset_id": self.target_dataset_id,
            "fold_dataset_id": self.fold_dataset_id,
            "experiment_configuration_id": self.experiment_configuration_id,
            "evidence_class": self.evidence_class.value,
            "market_data_source_class": self.market_data_source_class.value,
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
            "gap_known_by_cutoff",
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
                    "cross_market_source_active_count",
                    FeatureFamily.POOLED_CROSS_ASSET,
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


def feature_set_id(
    experiment_id: str,
    name: str,
    schema: Sequence[FeatureDefinition],
    market_data_source_class: MarketDataSourceClass = MarketDataSourceClass.IG_NATIVE_CAPTURE,
) -> str:
    return _hash_json(
        {
            "contract": "qtrad-r2-feature-set-v2",
            "experiment_configuration_id": experiment_id,
            "name": name,
            "raw_feature_schema_id": feature_schema_id(schema),
            "market_data_source_class": market_data_source_class.value,
        }
    )


_canonical_feature_set_id = feature_set_id


@dataclass(frozen=True, slots=True)
class R2FeatureVerificationReceipt:
    """Create-only proof that one persisted R2 feature child was independently verified."""

    manifest_contract: str
    manifest_schema_version: int
    manifest_id: str
    manifest_sha256: str
    semantic_dataset_id: str
    feature_set_name: str
    feature_set_id: str
    raw_feature_schema_id: str
    feature_schema: tuple[FeatureDefinition, ...]
    observation_dataset_id: str
    panel_dataset_id: str
    target_dataset_id: str
    fold_dataset_id: str
    experiment_configuration_id: str
    source_class: MarketDataSourceClass
    evidence_class: EvidenceClass
    holdout_excluded: bool
    foundation_semantic_id: str
    foundation_closure_id: str
    foundation_verification_id: str
    foundation_promotion_id: str | None
    verifier_contract: str
    verifier_version: str
    completed_checks: tuple[str, ...]
    verification_id: str

    CONTRACT: ClassVar[str] = R2_FEATURE_VERIFICATION_RECEIPT_CONTRACT
    SCHEMA_VERSION: ClassVar[int] = 1

    def __post_init__(self) -> None:
        identity_fields: tuple[tuple[object, str, int], ...] = (
            (self.manifest_id, "manifest ID", 24),
            (self.manifest_sha256, "manifest hash", 64),
            (self.semantic_dataset_id, "semantic dataset ID", 64),
            (self.feature_set_id, "feature set ID", 64),
            (self.raw_feature_schema_id, "raw feature schema ID", 64),
            (self.observation_dataset_id, "observation dataset ID", 64),
            (self.panel_dataset_id, "panel dataset ID", 64),
            (self.target_dataset_id, "target dataset ID", 64),
            (self.fold_dataset_id, "fold dataset ID", 64),
            (self.experiment_configuration_id, "experiment configuration ID", 64),
            (self.foundation_semantic_id, "foundation semantic ID", 64),
            (self.foundation_closure_id, "foundation closure ID", 64),
            (self.foundation_verification_id, "foundation verification ID", 64),
            (self.verification_id, "feature verification ID", 64),
        )
        for value, field, length in identity_fields:
            if (
                type(value) is not str
                or len(value) != length
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise ValueError(f"{field} must be a lowercase hexadecimal SHA-256 identity")
        if self.foundation_promotion_id is not None and (
            len(self.foundation_promotion_id) != 64
            or any(
                character not in "0123456789abcdef" for character in self.foundation_promotion_id
            )
        ):
            raise ValueError(
                "foundation promotion ID must be a lowercase hexadecimal SHA-256 identity"
            )
        if not self.manifest_contract or self.manifest_schema_version <= 0:
            raise ValueError("feature receipt must bind a supported manifest contract")
        if not self.feature_set_name or not self.verifier_contract or not self.verifier_version:
            raise ValueError("feature receipt has incomplete textual bindings")
        if not self.feature_schema:
            raise ValueError("feature receipt must bind a non-empty feature schema")
        if self.raw_feature_schema_id != feature_schema_id(self.feature_schema):
            raise ValueError("feature receipt raw schema identity is invalid")
        if not self.holdout_excluded:
            raise ValueError("feature receipt must bind holdout exclusion")
        if not self.completed_checks or any(not item for item in self.completed_checks):
            raise ValueError("feature receipt completed checks must be non-empty strings")
        if tuple(self.completed_checks) != tuple(dict.fromkeys(self.completed_checks)):
            raise ValueError("feature receipt completed checks must be unique")
        if self.verification_id != _hash_json(self.semantic_json()):
            raise ValueError("feature verification ID does not authenticate its content")

    @classmethod
    def create(cls, **values: Any) -> "R2FeatureVerificationReceipt":
        expected = {
            "manifest_contract",
            "manifest_schema_version",
            "manifest_id",
            "manifest_sha256",
            "semantic_dataset_id",
            "feature_set_name",
            "feature_set_id",
            "raw_feature_schema_id",
            "feature_schema",
            "observation_dataset_id",
            "panel_dataset_id",
            "target_dataset_id",
            "fold_dataset_id",
            "experiment_configuration_id",
            "source_class",
            "evidence_class",
            "holdout_excluded",
            "foundation_semantic_id",
            "foundation_closure_id",
            "foundation_verification_id",
            "foundation_promotion_id",
            "verifier_contract",
            "verifier_version",
            "completed_checks",
        }
        if set(values) != expected:
            raise ValueError("feature receipt create arguments are incomplete or unexpected")
        semantic = {
            "contract": cls.CONTRACT,
            "schema_version": cls.SCHEMA_VERSION,
            **{
                key: (
                    value.value
                    if isinstance(value, (MarketDataSourceClass, EvidenceClass))
                    else [item.as_json() for item in value]
                    if key == "feature_schema"
                    else list(value)
                    if key == "completed_checks"
                    else value
                )
                for key, value in values.items()
            },
        }
        return cls(**values, verification_id=_hash_json(semantic))

    @classmethod
    def from_json(cls, value: object) -> "R2FeatureVerificationReceipt":
        if not isinstance(value, dict):
            raise ValueError("R2 feature verification receipt must be an object")
        raw = cast(dict[str, object], value)
        expected = {
            "contract",
            "schema_version",
            "manifest_contract",
            "manifest_schema_version",
            "manifest_id",
            "manifest_sha256",
            "semantic_dataset_id",
            "feature_set_name",
            "feature_set_id",
            "raw_feature_schema_id",
            "feature_schema",
            "observation_dataset_id",
            "panel_dataset_id",
            "target_dataset_id",
            "fold_dataset_id",
            "experiment_configuration_id",
            "source_class",
            "evidence_class",
            "holdout_excluded",
            "foundation_semantic_id",
            "foundation_closure_id",
            "foundation_verification_id",
            "foundation_promotion_id",
            "verifier_contract",
            "verifier_version",
            "completed_checks",
            "verification_id",
        }
        if set(raw) != expected:
            raise ValueError("R2 feature verification receipt has unknown or missing fields")
        if raw["contract"] != cls.CONTRACT or raw["schema_version"] != cls.SCHEMA_VERSION:
            raise ValueError("R2 feature verification receipt contract is unsupported")
        schema_raw = raw["feature_schema"]
        if not isinstance(schema_raw, list):
            raise ValueError("R2 feature verification receipt schema is invalid")
        schema_items: list[FeatureDefinition] = []
        for raw_item in cast(list[object], schema_raw):
            if not isinstance(raw_item, dict):
                raise ValueError("R2 feature verification receipt schema is invalid")
            item = cast(dict[str, object], raw_item)
            if set(item) != {
                "name",
                "family",
                "availability_indicator",
            }:
                raise ValueError("R2 feature verification receipt schema is invalid")
            schema_items.append(
                FeatureDefinition(
                    name=_receipt_string(item["name"]),
                    family=FeatureFamily(_receipt_string(item["family"])),
                    availability_indicator=_receipt_bool(item["availability_indicator"]),
                )
            )
        checks_value = raw["completed_checks"]
        if not isinstance(checks_value, list):
            raise ValueError("R2 feature verification receipt checks are invalid")
        checks_raw = cast(list[object], checks_value)
        if not all(isinstance(item, str) for item in checks_raw):
            raise ValueError("R2 feature verification receipt checks are invalid")
        check_strings = cast(list[str], checks_raw)
        receipt = cls(
            manifest_contract=_receipt_string(raw["manifest_contract"]),
            manifest_schema_version=_receipt_int(raw["manifest_schema_version"]),
            manifest_id=_receipt_string(raw["manifest_id"]),
            manifest_sha256=_receipt_string(raw["manifest_sha256"]),
            semantic_dataset_id=_receipt_string(raw["semantic_dataset_id"]),
            feature_set_name=_receipt_string(raw["feature_set_name"]),
            feature_set_id=_receipt_string(raw["feature_set_id"]),
            raw_feature_schema_id=_receipt_string(raw["raw_feature_schema_id"]),
            feature_schema=tuple(schema_items),
            observation_dataset_id=_receipt_string(raw["observation_dataset_id"]),
            panel_dataset_id=_receipt_string(raw["panel_dataset_id"]),
            target_dataset_id=_receipt_string(raw["target_dataset_id"]),
            fold_dataset_id=_receipt_string(raw["fold_dataset_id"]),
            experiment_configuration_id=_receipt_string(raw["experiment_configuration_id"]),
            source_class=MarketDataSourceClass(_receipt_string(raw["source_class"])),
            evidence_class=EvidenceClass(_receipt_string(raw["evidence_class"])),
            holdout_excluded=_receipt_bool(raw["holdout_excluded"]),
            foundation_semantic_id=_receipt_string(raw["foundation_semantic_id"]),
            foundation_closure_id=_receipt_string(raw["foundation_closure_id"]),
            foundation_verification_id=_receipt_string(raw["foundation_verification_id"]),
            foundation_promotion_id=_receipt_nullable_string(raw["foundation_promotion_id"]),
            verifier_contract=_receipt_string(raw["verifier_contract"]),
            verifier_version=_receipt_string(raw["verifier_version"]),
            completed_checks=tuple(check_strings),
            verification_id=_receipt_string(raw["verification_id"]),
        )
        if receipt.as_json() != raw:
            raise ValueError("R2 feature verification receipt is not canonical")
        return receipt

    def semantic_json(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": self.SCHEMA_VERSION,
            "manifest_contract": self.manifest_contract,
            "manifest_schema_version": self.manifest_schema_version,
            "manifest_id": self.manifest_id,
            "manifest_sha256": self.manifest_sha256,
            "semantic_dataset_id": self.semantic_dataset_id,
            "feature_set_name": self.feature_set_name,
            "feature_set_id": self.feature_set_id,
            "raw_feature_schema_id": self.raw_feature_schema_id,
            "feature_schema": [item.as_json() for item in self.feature_schema],
            "observation_dataset_id": self.observation_dataset_id,
            "panel_dataset_id": self.panel_dataset_id,
            "target_dataset_id": self.target_dataset_id,
            "fold_dataset_id": self.fold_dataset_id,
            "experiment_configuration_id": self.experiment_configuration_id,
            "source_class": self.source_class.value,
            "evidence_class": self.evidence_class.value,
            "holdout_excluded": self.holdout_excluded,
            "foundation_semantic_id": self.foundation_semantic_id,
            "foundation_closure_id": self.foundation_closure_id,
            "foundation_verification_id": self.foundation_verification_id,
            "foundation_promotion_id": self.foundation_promotion_id,
            "verifier_contract": self.verifier_contract,
            "verifier_version": self.verifier_version,
            "completed_checks": list(self.completed_checks),
        }

    def as_json(self) -> dict[str, JsonValue]:
        return {**self.semantic_json(), "verification_id": self.verification_id}


def _receipt_string(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("feature receipt value must be a string")
    return value


def _receipt_nullable_string(value: object) -> str | None:
    if value is None:
        return None
    return _receipt_string(value)


def _receipt_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("feature receipt integer is invalid")
    return value


def _receipt_bool(value: object) -> bool:
    if not isinstance(value, bool):
        raise TypeError("feature receipt boolean is invalid")
    return value


class FeatureDatasetSemanticHasher:
    """Incrementally hash the canonical semantic dataset representation."""

    def __init__(
        self,
        *,
        feature_schema: Sequence[FeatureDefinition],
        feature_set_name: str,
        feature_set_identity: str,
        observation_dataset_id: str,
        panel_dataset_id: str,
        target_dataset_id: str,
        fold_dataset_id: str,
        experiment_configuration_id: str,
        evidence_class: EvidenceClass,
        holdout_excluded: bool,
        market_data_source_class: MarketDataSourceClass = MarketDataSourceClass.IG_NATIVE_CAPTURE,
    ) -> None:
        self._hash = sha256()
        identity = {
            "contract": R2_FEATURE_DATASET_CONTRACT,
            "schema_version": 2,
            "feature_schema": [item.as_json() for item in feature_schema],
            "feature_set_name": feature_set_name,
            "feature_set_id": feature_set_identity,
            "observation_dataset_id": observation_dataset_id,
            "panel_dataset_id": panel_dataset_id,
            "target_dataset_id": target_dataset_id,
            "fold_dataset_id": fold_dataset_id,
            "experiment_configuration_id": experiment_configuration_id,
            "evidence_class": evidence_class.value,
            "holdout_excluded": holdout_excluded,
        }
        identity["market_data_source_class"] = market_data_source_class.value
        encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self._hash.update(encoded[:-1])
        self._hash.update(b',"rows":[')
        self._first = True

    def update(self, row: RawFeatureRow) -> None:
        if not self._first:
            self._hash.update(b",")
        self._hash.update(
            json.dumps(row.as_json(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        self._first = False

    def hexdigest(self) -> str:
        state = self._hash.copy()
        state.update(b"]}")
        return state.hexdigest()


def feature_dataset_id(
    *,
    rows: Sequence[RawFeatureRow],
    feature_schema: Sequence[FeatureDefinition],
    feature_set_name: str,
    feature_set_identity: str,
    observation_dataset_id: str,
    panel_dataset_id: str,
    target_dataset_id: str,
    fold_dataset_id: str,
    experiment_configuration_id: str,
    evidence_class: EvidenceClass,
    holdout_excluded: bool,
    market_data_source_class: MarketDataSourceClass = MarketDataSourceClass.IG_NATIVE_CAPTURE,
) -> str:
    hasher = FeatureDatasetSemanticHasher(
        feature_schema=feature_schema,
        feature_set_name=feature_set_name,
        feature_set_identity=feature_set_identity,
        observation_dataset_id=observation_dataset_id,
        panel_dataset_id=panel_dataset_id,
        target_dataset_id=target_dataset_id,
        fold_dataset_id=fold_dataset_id,
        experiment_configuration_id=experiment_configuration_id,
        evidence_class=evidence_class,
        market_data_source_class=market_data_source_class,
        holdout_excluded=holdout_excluded,
    )
    for row in rows:
        hasher.update(row)
    return hasher.hexdigest()


_canonical_feature_dataset_id = feature_dataset_id


def _window_suffix(window: timedelta) -> str:
    seconds = int(window.total_seconds())
    if window <= timedelta(0) or window != timedelta(seconds=seconds):
        raise ValueError("feature windows must contain positive whole seconds")
    return f"{seconds}s"


def _hash_json(value: object) -> str:
    canonical = to_json_value(value)
    return sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
