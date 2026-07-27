"""Strict JSON persistence for R2 raw-feature artefacts."""

import json
from datetime import datetime
from pathlib import Path
from typing import cast

from qtrad.domain.r2_features import (
    FeatureDefinition,
    R2FeatureDataset,
    RawFeatureRow,
    RawFeatureValue,
)
from qtrad.domain.r2_readiness import EvidenceClass, FeatureFamily
from qtrad.domain.time import require_utc

_MAX_BYTES = 64 * 1024 * 1024
_ROOT_KEYS = {"contract", "manifest", "rows"}
_MANIFEST_KEYS = {
    "contract",
    "schema_version",
    "dataset_id",
    "raw_feature_schema_id",
    "feature_schema",
    "observation_dataset_id",
    "panel_dataset_id",
    "target_dataset_id",
    "fold_dataset_id",
    "experiment_configuration_id",
    "evidence_class",
    "holdout_excluded",
    "row_count",
}


def write_r2_feature_dataset(path: Path, dataset: R2FeatureDataset) -> None:
    """Write one immutable feature artefact without replacing existing evidence."""
    if path.is_symlink() or path.exists():
        raise ValueError("R2 feature output must be a new regular file")
    payload = {
        "contract": dataset.CONTRACT,
        "manifest": dataset.manifest_json(),
        "rows": [row.as_json() for row in dataset.rows],
    }
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if len(encoded.encode()) > _MAX_BYTES:
        raise ValueError("R2 feature artefact exceeds the size limit")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(encoded, encoding="utf-8", newline="\n")


def load_r2_feature_dataset(path: Path) -> R2FeatureDataset:
    """Load and semantically verify a persisted feature artefact."""
    if path.is_symlink() or not path.is_file():
        raise ValueError("R2 feature artefact must be a regular non-symlink file")
    raw = path.read_bytes()
    if len(raw) > _MAX_BYTES:
        raise ValueError("R2 feature artefact exceeds the size limit")
    payload = _mapping(json.loads(raw, object_pairs_hook=_strict_object))
    if set(payload) != _ROOT_KEYS:
        raise ValueError("R2 feature artefact has unknown or missing fields")
    if payload["contract"] != R2FeatureDataset.CONTRACT:
        raise ValueError("R2 feature artefact contract is unsupported")
    manifest = _mapping(payload["manifest"])
    if set(manifest) != _MANIFEST_KEYS:
        raise ValueError("R2 feature manifest has unknown or missing fields")
    schema = tuple(_feature_definition(item) for item in _sequence(manifest["feature_schema"]))
    rows = tuple(_feature_row(item) for item in _sequence(payload["rows"]))
    if manifest["row_count"] != len(rows):
        raise ValueError("R2 feature manifest row count differs from artefact rows")
    dataset = R2FeatureDataset(
        rows=rows,
        feature_schema=schema,
        raw_feature_schema_id=_text(manifest["raw_feature_schema_id"]),
        observation_dataset_id=_text(manifest["observation_dataset_id"]),
        panel_dataset_id=_text(manifest["panel_dataset_id"]),
        target_dataset_id=_text(manifest["target_dataset_id"]),
        fold_dataset_id=_text(manifest["fold_dataset_id"]),
        experiment_configuration_id=_text(manifest["experiment_configuration_id"]),
        evidence_class=EvidenceClass(_text(manifest["evidence_class"])),
        holdout_excluded=_bool(manifest["holdout_excluded"]),
        dataset_id=_text(manifest["dataset_id"]),
    )
    if dataset.manifest_json() != manifest:
        raise ValueError("R2 feature manifest does not match its decoded dataset")
    return dataset


def _feature_definition(value: object) -> FeatureDefinition:
    payload = _mapping(value)
    if set(payload) != {"name", "family", "availability_indicator"}:
        raise ValueError("feature definition has unknown or missing fields")
    return FeatureDefinition(
        name=_text(payload["name"]),
        family=FeatureFamily(_text(payload["family"])),
        availability_indicator=_bool(payload["availability_indicator"]),
    )


def _feature_row(value: object) -> RawFeatureRow:
    payload = _mapping(value)
    expected = {
        "target_instrument_id",
        "decision_time",
        "feature_data_asof",
        "latest_feature_bar_end",
        "feature_set_id",
        "values",
    }
    if set(payload) != expected:
        raise ValueError("feature row has unknown or missing fields")
    return RawFeatureRow(
        target_instrument_id=_text(payload["target_instrument_id"]),
        decision_time=_datetime(payload["decision_time"]),
        feature_data_asof=_datetime(payload["feature_data_asof"]),
        latest_feature_bar_end=_datetime(payload["latest_feature_bar_end"]),
        feature_set_id=_text(payload["feature_set_id"]),
        values=tuple(_feature_value(item) for item in _sequence(payload["values"])),
    )


def _feature_value(value: object) -> RawFeatureValue:
    payload = _mapping(value)
    if set(payload) != {"name", "value", "source_event_ids"}:
        raise ValueError("feature value has unknown or missing fields")
    raw_value = payload["value"]
    if raw_value is not None and (
        isinstance(raw_value, bool) or not isinstance(raw_value, (int, float))
    ):
        raise TypeError("feature value must be a number or null")
    return RawFeatureValue(
        name=_text(payload["name"]),
        value=None if raw_value is None else float(raw_value),
        source_event_ids=tuple(_text(item) for item in _sequence(payload["source_event_ids"])),
    )


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"R2 feature artefact contains duplicate field: {key}")
        value[key] = item
    return value


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise TypeError("expected an object with string keys")
    return cast(dict[str, object], value)


def _sequence(value: object) -> list[object]:
    if not isinstance(value, list):
        raise TypeError("expected an array")
    return cast(list[object], value)


def _text(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise TypeError("expected non-empty text")
    return value


def _bool(value: object) -> bool:
    if not isinstance(value, bool):
        raise TypeError("expected a boolean")
    return value


def _datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(_text(value).replace("Z", "+00:00"))
    require_utc(parsed, "R2 feature timestamp")
    return parsed
