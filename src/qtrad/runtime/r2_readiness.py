"""Strict JSON I/O for R2.A experiment configuration and readiness reports."""

import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

from qtrad.domain.foundation import InstrumentRole
from qtrad.domain.r2_readiness import (
    EvidenceClass,
    FeatureEligibility,
    FeatureFamily,
    FeatureSet,
    ModelFamily,
    R2ExperimentConfig,
    R2ReadinessReport,
)

_MAX_BYTES = 4 * 1024 * 1024
_KEYS = frozenset(
    {
        "contract",
        "schema_version",
        "name",
        "r1_bundle_id",
        "observation_dataset_id",
        "foundation_configuration_id",
        "panel_dataset_id",
        "target_dataset_id",
        "fold_dataset_id",
        "r1_application_version",
        "r1_image_identity",
        "ordered_instruments",
        "instrument_roles",
        "target_instruments",
        "market_groups",
        "horizons_seconds",
        "primary_horizon_seconds",
        "feature_sets",
        "feature_windows_seconds",
        "feature_coverage_thresholds",
        "feature_eligibility",
        "feature_eligibility_evidence_ids",
        "preprocessing_policy",
        "alpha_grid",
        "inner_validation_policy",
        "ridge_solver",
        "ridge_tolerance",
        "ridge_max_iterations",
        "pooled_weighting_policy",
        "minimum_training_rows",
        "minimum_inner_validation_rows",
        "minimum_outer_validation_rows",
        "metric_policy",
        "forecast_bucket_policy",
        "state_bucket_policy",
        "model_selection_policy",
        "acceptance_thresholds",
        "holdout_range",
        "numeric_replay_relative_tolerance",
        "numeric_replay_absolute_tolerance",
        "evidence_class",
        "model_families",
    }
)


def load_r2_experiment(path: Path) -> R2ExperimentConfig:
    if path.is_symlink() or not path.is_file():
        raise ValueError("R2 experiment must be a regular non-symlink file")
    encoded = path.read_bytes()
    if len(encoded) > _MAX_BYTES:
        raise ValueError("R2 experiment exceeds the 4 MiB limit")
    return decode_r2_experiment(_mapping(json.loads(encoded)))


def decode_r2_experiment(payload: Mapping[str, object]) -> R2ExperimentConfig:
    if set(payload) != _KEYS:
        raise ValueError("R2 experiment has unknown or missing fields")
    if payload["contract"] != R2ExperimentConfig.CONTRACT:
        raise ValueError("R2 experiment contract is unsupported")
    holdout = _sequence(payload["holdout_range"])
    if len(holdout) != 2:
        raise ValueError("R2 holdout range requires exactly two timestamps")
    families = tuple(FeatureFamily)
    coverage = _mapping(payload["feature_coverage_thresholds"])
    eligibility = _mapping(payload["feature_eligibility"])
    evidence = _mapping(payload["feature_eligibility_evidence_ids"])
    if (
        set(coverage) != {item.value for item in families}
        or set(eligibility) != set(coverage)
        or set(evidence) != set(coverage)
    ):
        raise ValueError("R2 feature-family mappings must use the complete declared family set")
    return R2ExperimentConfig(
        name=_text(payload["name"]),
        schema_version=_int(payload["schema_version"]),
        r1_bundle_id=_text(payload["r1_bundle_id"]),
        observation_dataset_id=_text(payload["observation_dataset_id"]),
        foundation_configuration_id=_text(payload["foundation_configuration_id"]),
        panel_dataset_id=_text(payload["panel_dataset_id"]),
        target_dataset_id=_text(payload["target_dataset_id"]),
        fold_dataset_id=_text(payload["fold_dataset_id"]),
        r1_application_version=_text(payload["r1_application_version"]),
        r1_image_identity=_text(payload["r1_image_identity"]),
        ordered_instruments=tuple(
            _text(item) for item in _sequence(payload["ordered_instruments"])
        ),
        instrument_roles={
            key: InstrumentRole(_text(value))
            for key, value in _mapping(payload["instrument_roles"]).items()
        },
        target_instruments=tuple(_text(item) for item in _sequence(payload["target_instruments"])),
        market_groups={
            key: _text(value) for key, value in _mapping(payload["market_groups"]).items()
        },
        horizons=tuple(_duration(item) for item in _sequence(payload["horizons_seconds"])),
        primary_horizon=_duration(payload["primary_horizon_seconds"]),
        feature_sets=tuple(
            _feature_set(_mapping(item)) for item in _sequence(payload["feature_sets"])
        ),
        feature_windows=tuple(
            _duration(item) for item in _sequence(payload["feature_windows_seconds"])
        ),
        feature_coverage_thresholds={family: _float(coverage[family.value]) for family in families},
        feature_eligibility={
            family: FeatureEligibility(_text(eligibility[family.value])) for family in families
        },
        feature_eligibility_evidence_ids={
            family: _text(evidence[family.value]) for family in families
        },
        preprocessing_policy=_text(payload["preprocessing_policy"]),
        alpha_grid=tuple(_float(item) for item in _sequence(payload["alpha_grid"])),
        inner_validation_policy=_text(payload["inner_validation_policy"]),
        ridge_solver=_text(payload["ridge_solver"]),
        ridge_tolerance=_float(payload["ridge_tolerance"]),
        ridge_max_iterations=_int(payload["ridge_max_iterations"]),
        pooled_weighting_policy=_text(payload["pooled_weighting_policy"]),
        minimum_training_rows=_int(payload["minimum_training_rows"]),
        minimum_inner_validation_rows=_int(payload["minimum_inner_validation_rows"]),
        minimum_outer_validation_rows=_int(payload["minimum_outer_validation_rows"]),
        metric_policy=_text(payload["metric_policy"]),
        forecast_bucket_policy=_text(payload["forecast_bucket_policy"]),
        state_bucket_policy=_text(payload["state_bucket_policy"]),
        model_selection_policy=_text(payload["model_selection_policy"]),
        acceptance_thresholds={
            key: _float(value) for key, value in _mapping(payload["acceptance_thresholds"]).items()
        },
        holdout_range=(_datetime(holdout[0]), _datetime(holdout[1])),
        numeric_replay_relative_tolerance=_float(payload["numeric_replay_relative_tolerance"]),
        numeric_replay_absolute_tolerance=_float(payload["numeric_replay_absolute_tolerance"]),
        evidence_class=EvidenceClass(_text(payload["evidence_class"])),
        model_families=tuple(
            ModelFamily(_text(item)) for item in _sequence(payload["model_families"])
        ),
    )


def write_r2_readiness(path: Path, report: R2ReadinessReport) -> None:
    if path.is_symlink() or path.exists():
        raise ValueError("R2 readiness output must be a new regular file")
    encoded = json.dumps(report.as_json(), indent=2, sort_keys=True) + "\n"
    if len(encoded.encode()) > _MAX_BYTES:
        raise ValueError("R2 readiness report exceeds the 4 MiB limit")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as output:
        output.write(encoded)


def _feature_set(value: Mapping[str, object]) -> FeatureSet:
    if set(value) != {"name", "families"}:
        raise ValueError("R2 feature set has unknown or missing fields")
    return FeatureSet(
        name=_text(value["name"]),
        families=tuple(FeatureFamily(_text(item)) for item in _sequence(value["families"])),
    )


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise TypeError("expected a JSON object")
    return cast(dict[str, object], value)


def _sequence(value: object) -> Sequence[object]:
    if not isinstance(value, list):
        raise TypeError("expected a JSON array")
    return value


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("expected a string")
    return value


def _int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError("expected an integer")
    return value


def _float(value: object) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise TypeError("expected a number")
    return float(value)


def _duration(value: object) -> timedelta:
    return timedelta(seconds=_float(value))


def _datetime(value: object) -> datetime:
    return datetime.fromisoformat(_text(value).replace("Z", "+00:00"))
