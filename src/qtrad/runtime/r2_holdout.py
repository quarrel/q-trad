"""Create-only persistence and marker-first reveal for R2.G2 holdouts."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast
from uuid import UUID

from qtrad.application.r2_holdout import _CONFIRMATORY_G2_PREPARATION_TOKEN
from qtrad.domain.foundation import (
    TARGET_DATASET_CONTRACT,
    ExcursionDisposition,
    ReturnDisposition,
    TargetDataset,
    TargetRow,
)
from qtrad.domain.market_data import MarketDataSourceClass, PriceBasis
from qtrad.domain.r2_bundles import ArtifactReference
from qtrad.domain.r2_features import (
    FeatureDefinition,
    R2FeatureDataset,
    RawFeatureRow,
    RawFeatureValue,
)
from qtrad.domain.r2_holdout import (
    R2_HOLDOUT_BUNDLE_CONTRACT,
    R2_HOLDOUT_CONSUMED_CONTRACT,
    R2_HOLDOUT_COVERAGE_CONTRACT,
    R2_HOLDOUT_EVALUATION_CONTRACT,
    R2_HOLDOUT_FEATURES_CONTRACT,
    R2_HOLDOUT_FORECAST_CONTRACT,
    R2_HOLDOUT_FORECAST_SEAL_CONTRACT,
    R2_HOLDOUT_OPENED_CONTRACT,
    R2_HOLDOUT_OUTCOME_EVIDENCE_CONTRACT,
    R2_HOLDOUT_SELECTION_CONTRACT,
    EvidenceClass,
    HoldoutConclusion,
    HoldoutMarkerState,
    HoldoutScope,
    HoldoutTargetOpportunity,
    R2HoldoutBundle,
    R2HoldoutConsumedMarker,
    R2HoldoutEvaluation,
    R2HoldoutFeatureDataset,
    R2HoldoutFeatureRow,
    R2HoldoutForecastSeal,
    R2HoldoutOpenedMarker,
    R2HoldoutOutcomeEvidence,
    R2HoldoutQuestionResult,
    R2HoldoutSelectionManifest,
    R2HoldoutTargetSource,
    holdout_selection_compact_bindings,
)
from qtrad.domain.r2_readiness import FeatureFamily
from qtrad.domain.time import require_utc
from qtrad.runtime.r2_bundles import atomic_create, canonical_bytes
from qtrad.runtime.r2_partitioned_rows import (
    PARTITIONED_ROWS_STORAGE,
    load_partitioned_rows,
    partitioned_manifest_part_paths,
    write_partitioned_rows,
)

_MAX_BYTES = 64 * 1024 * 1024
_FAILURE_EVALUATION_ID = sha256(b"qtrad-r2-holdout-reveal-failed").hexdigest()
_PARTITIONED_PART_CONTRACT = "qtrad-r2-partitioned-json-row-part-v1"
_CONFIRMATORY_G2_LIFECYCLE_TOKEN = object()


@dataclass(frozen=True)
class _PayloadCacheEntry:
    root: str
    relative: str
    contract: str
    identity_key: str
    expected_id: str | None
    expected_fields: frozenset[str]
    payload: dict[str, object]
    payload_sha256: str
    top_level_sha256: str
    header_sha256: str | None
    part_snapshot: tuple[tuple[str, str], ...]


_PayloadCacheKey = tuple[str, str, str, str, str | None, frozenset[str]]
_PayloadCache = dict[_PayloadCacheKey, _PayloadCacheEntry]


def _load_object_bytes(encoded: bytes, path: Path) -> dict[str, object]:
    if len(encoded) > _MAX_BYTES:
        raise ValueError(f"holdout child exceeds the {_MAX_BYTES} byte limit: {path}")
    value = json.loads(encoded)
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"holdout child must be a JSON object: {path}")
    return cast(dict[str, object], value)


def _load_object(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"holdout child must be a regular non-symlink file: {path}")
    return _load_object_bytes(path.read_bytes(), path)


def _partitioned_payload(
    root: Path,
    relative: str,
    payload: Mapping[str, object],
    *,
    identity_field: str,
    row_field: str,
    array_fields: Sequence[str] = (),
    mapping_fields: Sequence[str] = (),
) -> dict[str, object]:
    """Reconstruct one compact physical child into its logical semantic payload."""
    if payload.get("storage") != PARTITIONED_ROWS_STORAGE:
        return dict(payload)
    _verify_compact_header(payload)
    if payload.get("partition_row_field") != row_field:
        raise ValueError("partitioned holdout child row field differs from its contract")
    raw_fields = payload.get("partition_fields", [row_field])
    if not isinstance(raw_fields, list) or any(not isinstance(item, str) for item in raw_fields):
        raise ValueError("partitioned holdout child field register is invalid")
    fields = tuple(str(item) for item in raw_fields)
    expected_fields = tuple(array_fields or (row_field,))
    if fields != expected_fields:
        raise ValueError("partitioned holdout child field register differs from its contract")
    raw_mapping_fields = payload.get("partition_mapping_fields", [])
    if not isinstance(raw_mapping_fields, list) or any(
        not isinstance(item, str) for item in raw_mapping_fields
    ):
        raise ValueError("partitioned holdout mapping field register is invalid")
    if tuple(raw_mapping_fields) != tuple(mapping_fields):
        raise ValueError("partitioned holdout mapping field register differs from its contract")
    rows = load_partitioned_rows(root, relative, payload, identity_field=identity_field)
    logical = {
        key: value
        for key, value in payload.items()
        if key
        not in {
            "storage",
            "identity_field",
            "row_count",
            "parts",
            "partition_row_field",
            "partition_fields",
            "partition_mapping_fields",
            "header_sha256",
        }
    }
    if array_fields:
        grouped: dict[str, list[object]] = {
            field: [] for field in fields if field not in mapping_fields
        }
        grouped_mappings: dict[str, dict[str, object] | None] = {
            field: {} for field in mapping_fields
        }
        nullable_fields: set[str] = set()
        mapping_row_seen: set[str] = set()
        field_positions = {field: index for index, field in enumerate(fields)}
        last_field_index = -1
        last_mapping_keys: dict[str, str] = {}
        for row in rows:
            field = row.get("field")
            if not isinstance(field, str) or field not in fields:
                raise ValueError("partitioned holdout field row names an unknown field")
            field_index = field_positions[field]
            if field_index < last_field_index:
                raise ValueError("partitioned holdout rows are not in canonical field order")
            last_field_index = field_index
            if field in grouped_mappings:
                if set(row) == {"field", "value"} and row["value"] is None:
                    if field in mapping_row_seen or grouped_mappings[field] != {}:
                        raise ValueError("partitioned holdout mapping row is invalid")
                    grouped_mappings[field] = None
                    mapping_row_seen.add(field)
                elif set(row) == {"field", "key", "value"} and isinstance(row["key"], str):
                    mapping = grouped_mappings[field]
                    if mapping is None:
                        raise ValueError("partitioned R2 mapping row is invalid")
                    key = row["key"]
                    previous_key = last_mapping_keys.get(field)
                    if key in mapping:
                        raise ValueError("partitioned R2 mapping row contains a duplicate key")
                    if previous_key is not None and key <= previous_key:
                        raise ValueError("partitioned R2 mapping rows are not in canonical order")
                    mapping[key] = row["value"]
                    last_mapping_keys[field] = key
                    mapping_row_seen.add(field)
                else:
                    raise ValueError("partitioned holdout mapping row is invalid")
            else:
                if set(row) == {"field", "value", "is_null"} and row["is_null"] is True:
                    if row["value"] is not None or field in nullable_fields or grouped[field]:
                        raise ValueError("partitioned holdout null field row is invalid")
                    nullable_fields.add(field)
                elif set(row) == {"field", "value"}:
                    if field in nullable_fields:
                        raise ValueError("partitioned holdout field row is invalid")
                    grouped[field].append(row["value"])
                else:
                    raise ValueError("partitioned holdout field row is invalid")
        for field, values in grouped.items():
            _set_nested_field(logical, field, values)
        for field, mapping_values in grouped_mappings.items():
            _set_nested_field(logical, field, mapping_values)
        for field in nullable_fields:
            _set_nested_field(logical, field, None)
    else:
        values: list[object] = []
        for row in rows:
            if set(row) != {"value"}:
                raise ValueError("partitioned holdout row is invalid")
            values.append(row["value"])
        logical[row_field] = values
    return logical


def _nested_field_value(payload: Mapping[str, object], field: str) -> object:
    value: object = payload
    for component in field.split("."):
        if not isinstance(value, Mapping) or component not in value:
            raise ValueError(f"partitioned holdout field is missing: {field}")
        value = cast(Mapping[str, object], value)[component]
    return value


def _set_nested_field(payload: dict[str, object], field: str, value: object) -> None:
    components = field.split(".")
    if len(components) == 1:
        payload[field] = value
        return
    current: dict[str, object] = payload
    for component in components[:-1]:
        nested = current.get(component)
        if not isinstance(nested, Mapping):
            raise ValueError(f"partitioned holdout nested field is not an object: {field}")
        copied = dict(nested)
        current[component] = copied
        current = copied
    current[components[-1]] = value


def _partitioned_paths(
    root: Path,
    relative: str,
    payload: Mapping[str, object],
    *,
    identity_field: str,
) -> tuple[str, ...]:
    if payload.get("storage") != PARTITIONED_ROWS_STORAGE:
        return ()
    _verify_compact_header(payload)
    return partitioned_manifest_part_paths(root, relative, payload, identity_field=identity_field)


def _compact_header_digest(payload: Mapping[str, object]) -> str:
    physical_fields = {
        "storage",
        "identity_field",
        "row_count",
        "parts",
        "header_sha256",
    }
    return sha256(
        canonical_bytes(
            {key: value for key, value in payload.items() if key not in physical_fields}
        )
    ).hexdigest()


def _verify_compact_header(payload: Mapping[str, object], *, encoded: bytes | None = None) -> None:
    digest = payload.get("header_sha256")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise ValueError("partitioned holdout child header digest is malformed")
    if digest != _compact_header_digest(payload):
        raise ValueError(
            "partitioned holdout child header digest differs from its canonical header"
        )
    if encoded is not None and encoded != canonical_bytes(payload):
        raise ValueError("partitioned holdout child header is not canonical")


def _partitioned_header(
    payload: Mapping[str, object],
    *,
    row_field: str,
    array_fields: Sequence[str] = (),
    mapping_fields: Sequence[str] = (),
) -> dict[str, object]:
    header = dict(payload)
    _remove_nested_field(header, row_field)
    for field in array_fields:
        _remove_nested_field(header, field)
    header["partition_row_field"] = row_field
    header["partition_fields"] = list(array_fields or (row_field,))
    header["partition_mapping_fields"] = list(mapping_fields)
    return header


def _remove_nested_field(payload: dict[str, object], field: str) -> None:
    components = field.split(".")
    if len(components) == 1:
        payload.pop(field, None)
        return
    nested = payload.get(components[0])
    if not isinstance(nested, Mapping):
        raise ValueError(f"partitioned holdout nested field is not an object: {field}")
    copied = dict(nested)
    payload[components[0]] = copied
    _remove_nested_field(copied, ".".join(components[1:]))


def _write_partitioned_child(
    output: Path,
    relative: str,
    payload: Mapping[str, object],
    *,
    identity_field: str,
    row_field: str,
    array_fields: Sequence[str] = (),
    mapping_fields: Sequence[str] = (),
) -> tuple[dict[str, object], tuple[str, ...]]:
    """Write bounded physical parts while retaining the original logical identity."""
    fields = tuple(array_fields or (row_field,))
    mapping_names = tuple(mapping_fields)
    if any(field not in fields for field in mapping_names):
        raise ValueError("partitioned mapping fields must be registered array fields")
    if array_fields:

        def encoded_rows() -> Iterator[Mapping[str, object]]:
            for field in fields:
                if field in mapping_names:
                    mapping = _nested_field_value(payload, field)
                    if mapping is None:
                        yield {"field": field, "value": None}
                    elif isinstance(mapping, Mapping):
                        if any(not isinstance(key, str) for key in mapping):
                            raise TypeError(
                                f"partitioned mapping field keys must be strings: {field}"
                            )
                        yield from (
                            {"field": field, "key": key, "value": value}
                            for key, value in ((key, mapping[key]) for key in sorted(mapping))
                        )
                    else:
                        raise TypeError(f"partitioned mapping field is not an object: {field}")
                else:
                    values = _nested_field_value(payload, field)
                    if values is None:
                        yield {"field": field, "value": None, "is_null": True}
                    elif isinstance(values, Sequence) and not isinstance(
                        values, (str, bytes, bytearray)
                    ):
                        yield from ({"field": field, "value": value} for value in values)
                    else:
                        raise TypeError(f"partitioned array field is not a sequence: {field}")

        rows = encoded_rows()

        def mapping_count(value: object, field: str) -> int:
            if isinstance(value, Mapping):
                return len(value)
            if value is None:
                return 1
            raise TypeError(f"partitioned mapping field is not an object: {field}")

        expected_count = sum(
            mapping_count(_nested_field_value(payload, field), field)
            if field in mapping_names
            else 1
            if _nested_field_value(payload, field) is None
            else len(cast(Sequence[object], _nested_field_value(payload, field)))
            for field in fields
        )
    else:
        rows = ({"value": value} for value in cast(Sequence[object], payload[row_field]))
        expected_count = len(cast(Sequence[object], payload[row_field]))
    header = _partitioned_header(
        payload,
        row_field=row_field,
        array_fields=fields,
        mapping_fields=mapping_names,
    )
    header["header_sha256"] = _compact_header_digest(header)
    compact = write_partitioned_rows(
        output,
        relative,
        header=header,
        identity_field=identity_field,
        rows=rows,
        expected_row_count=expected_count,
    )
    return compact, _partitioned_paths(output, relative, compact, identity_field=identity_field)


def _semantic_id(payload: Mapping[str, object], identity_key: str) -> str:
    semantic = {key: value for key, value in payload.items() if key != identity_key}
    contract = payload.get("contract")
    if contract == "qtrad-r2-final-fit-v1":
        semantic.pop("runtime_identities", None)
        preprocessing = semantic.get("preprocessing")
        if not isinstance(preprocessing, Mapping):
            raise ValueError("final fit preprocessing evidence must be an object")
        semantic["preprocessing"] = {
            key: value for key, value in preprocessing.items() if key != "foundation_bundle_id"
        }
    elif contract == "qtrad-r2-holdout-forecast-seal-v1":
        semantic.pop("runtime_identities", None)
        semantic.pop("prepared_at", None)
        semantic.pop("prepared_by", None)
    encoded = json.dumps(semantic, sort_keys=True, separators=(",", ":")).encode()
    return sha256(encoded).hexdigest()


def _safe_child(root: Path, relative: str) -> Path:
    candidate = Path(relative)
    if (
        candidate.is_absolute()
        or "\\" in relative
        or any(part in ("", ".", "..") for part in candidate.parts)
    ):
        raise ValueError(f"holdout child path is unsafe: {relative}")
    candidate_path = root / candidate
    if candidate_path.is_symlink():
        raise ValueError(f"holdout child path must not be a symlink: {relative}")
    resolved_root = root.resolve()
    resolved = candidate_path.resolve()
    if not resolved.is_relative_to(resolved_root):
        raise ValueError(f"holdout child path escapes its root: {relative}")
    return resolved


def _payload_cache_key(
    root: Path,
    relative: str,
    *,
    contract: str,
    identity_key: str,
    expected_fields: set[str],
    expected_id: str | None,
) -> _PayloadCacheKey:
    return (
        str(root.resolve()),
        relative,
        contract,
        identity_key,
        expected_id,
        frozenset(expected_fields),
    )


def _authenticate_cached_snapshot(root: Path, entry: _PayloadCacheEntry) -> None:
    if str(root.resolve()) != entry.root:
        raise ValueError("holdout payload cache root differs from its authenticated snapshot")
    path = _safe_child(root, entry.relative)
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"cached holdout child is not a regular file: {entry.relative}")
    encoded = path.read_bytes()
    if len(encoded) > _MAX_BYTES or sha256(encoded).hexdigest() != entry.top_level_sha256:
        raise ValueError(
            f"cached holdout child bytes differ from its authenticated snapshot: {entry.relative}"
        )
    expected_parts = {relative for relative, _digest in entry.part_snapshot}
    parts_root = _safe_child(root, f"{entry.relative}.parts")
    if expected_parts:
        if parts_root.is_symlink() or not parts_root.is_dir():
            raise ValueError(
                f"cached holdout part root is not a regular directory: {entry.relative}"
            )
        actual_parts: set[str] = set()
        for candidate in parts_root.rglob("*"):
            relative = candidate.relative_to(root.resolve()).as_posix()
            if candidate.is_symlink():
                raise ValueError(f"cached holdout part is a symlink: {relative}")
            if candidate.is_dir():
                raise ValueError(f"cached holdout part tree contains a directory: {relative}")
            if not candidate.is_file():
                raise ValueError(f"cached holdout part is not a regular file: {relative}")
            actual_parts.add(relative)
        if actual_parts != expected_parts:
            raise ValueError(f"cached holdout part closure differs: {entry.relative}")
    elif parts_root.exists() or parts_root.is_symlink():
        raise ValueError(f"cached holdout part closure differs: {entry.relative}")
    for relative, expected_digest in entry.part_snapshot:
        part = _safe_child(root, relative)
        encoded_part = part.read_bytes()
        if len(encoded_part) > _MAX_BYTES or sha256(encoded_part).hexdigest() != expected_digest:
            raise ValueError(
                f"cached holdout part bytes differ from its authenticated snapshot: {relative}"
            )
    if sha256(canonical_bytes(entry.payload)).hexdigest() != entry.payload_sha256:
        raise ValueError(f"cached holdout payload was mutated in memory: {entry.relative}")


def _verify_child(
    root: Path,
    relative: str,
    *,
    contract: str,
    identity_key: str,
    expected_fields: set[str],
    expected_id: str | None = None,
    _payload_cache: _PayloadCache | None = None,
) -> dict[str, object]:
    key = _payload_cache_key(
        root,
        relative,
        contract=contract,
        identity_key=identity_key,
        expected_fields=expected_fields,
        expected_id=expected_id,
    )
    if _payload_cache is not None and key in _payload_cache:
        entry = _payload_cache[key]
        _authenticate_cached_snapshot(root, entry)
        return entry.payload
    path = _safe_child(root, relative)
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"holdout child must be a regular non-symlink file: {path}")
    encoded = path.read_bytes()
    physical = _load_object_bytes(encoded, path)
    payload = physical
    part_snapshot: tuple[tuple[str, str], ...] = ()
    header_sha256: str | None = None
    if physical.get("storage") == PARTITIONED_ROWS_STORAGE:
        _verify_compact_header(physical, encoded=encoded)
        header_value = physical.get("header_sha256")
        header_sha256 = header_value if isinstance(header_value, str) else None
        if contract == "qtrad-r2-final-fit-v1":
            row_field = "fit_arrays"
            raw_fields = physical.get("partition_fields")
            if not isinstance(raw_fields, list) or any(
                not isinstance(field, str) for field in raw_fields
            ):
                raise ValueError("final-fit partition field register is invalid")
            array_fields = tuple(str(field) for field in raw_fields)
            if not _final_fit_partition_fields_are_valid(array_fields):
                raise ValueError("final-fit partition field register is unsupported")
            mapping_fields = ("diagnostics",)
        elif contract in {
            R2_HOLDOUT_FEATURES_CONTRACT,
            R2_HOLDOUT_FORECAST_CONTRACT,
            R2_HOLDOUT_COVERAGE_CONTRACT,
            TARGET_DATASET_CONTRACT,
        }:
            row_field = "rows"
            array_fields = ()
            mapping_fields = ()
        elif contract == R2_HOLDOUT_OUTCOME_EVIDENCE_CONTRACT:
            row_field = "outcomes"
            array_fields = ("expected_target_ids", "source_row_ids", "outcomes")
            mapping_fields = ()
        else:
            raise ValueError(f"{contract} does not support partitioned persistence")
        paths = _partitioned_paths(root, relative, physical, identity_field=identity_key)
        references = physical.get("parts")
        if not isinstance(references, list):
            raise ValueError("partitioned holdout child part register is invalid")
        part_snapshot = tuple(
            (
                part_path,
                cast(
                    str,
                    _object_dict(reference, "partitioned holdout part reference")["sha256"],
                ),
            )
            for part_path, reference in zip(paths, references, strict=True)
        )
        payload = _partitioned_payload(
            root,
            relative,
            physical,
            identity_field=identity_key,
            row_field=row_field,
            array_fields=array_fields,
            mapping_fields=mapping_fields,
        )
    if set(payload) != expected_fields:
        raise ValueError(f"{contract} child has unknown or missing fields")
    if payload.get("contract") != contract or payload.get("schema_version") != 1:
        raise ValueError(f"{contract} child contract is unsupported")
    identity = payload.get(identity_key)
    if not isinstance(identity, str) or identity != _semantic_id(payload, identity_key):
        raise ValueError(f"{contract} child identity does not authenticate its content")
    if expected_id is not None and identity != expected_id:
        raise ValueError(f"{contract} child identity differs from its declared reference")
    if _payload_cache is not None:
        _payload_cache[key] = _PayloadCacheEntry(
            root=str(root.resolve()),
            relative=relative,
            contract=contract,
            identity_key=identity_key,
            expected_id=expected_id,
            expected_fields=frozenset(expected_fields),
            payload=payload,
            payload_sha256=sha256(canonical_bytes(payload)).hexdigest(),
            top_level_sha256=sha256(encoded).hexdigest(),
            header_sha256=header_sha256,
            part_snapshot=part_snapshot,
        )
    return payload


def _as_json(value: object) -> Mapping[str, object]:
    serializer = getattr(value, "as_json", None)
    if not callable(serializer):
        raise TypeError("holdout child must expose as_json")
    return cast(Mapping[str, object], cast(Callable[[], object], serializer)())


def _object_dict(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{field} must be a JSON object")
    return cast(dict[str, object], value)


def _object_list(value: object, field: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a JSON array")
    return cast(list[object], value)


def _text_value(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _utc_value(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO-8601 string")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from error


def _training_feature_dataset_from_payload(
    payload: Mapping[str, object],
) -> R2FeatureDataset:
    if set(payload) != _TRAINING_FEATURE_FIELDS:
        raise ValueError("training feature child has unknown or missing fields")
    if payload.get("contract") != "qtrad-r2-features-v2" or payload.get("schema_version") != 2:
        raise ValueError("training feature child contract is unsupported")
    schema: list[FeatureDefinition] = []
    for raw_definition in _object_list(payload["feature_schema"], "feature_schema"):
        definition = _object_dict(raw_definition, "feature_schema item")
        if set(definition) != {"name", "family", "availability_indicator"}:
            raise ValueError("training feature schema item has unknown or missing fields")
        availability = definition["availability_indicator"]
        if not isinstance(availability, bool):
            raise ValueError("training feature availability indicator must be boolean")
        schema.append(
            FeatureDefinition(
                name=_text_value(definition["name"], "feature name"),
                family=FeatureFamily(_text_value(definition["family"], "feature family")),
                availability_indicator=availability,
            )
        )
    rows: list[RawFeatureRow] = []
    for raw_row in _object_list(payload["rows"], "training feature rows"):
        row = _object_dict(raw_row, "training feature row")
        expected_fields = {
            "target_instrument_id",
            "decision_time",
            "feature_data_asof",
            "latest_feature_bar_end",
            "feature_set_id",
            "values",
        }
        if set(row) != expected_fields:
            raise ValueError("training feature row has unknown or missing fields")
        values: list[RawFeatureValue] = []
        for raw_value in _object_list(row["values"], "training feature values"):
            item = _object_dict(raw_value, "training feature value")
            if set(item) != {"name", "value", "source_event_ids"}:
                raise ValueError("training feature value has unknown or missing fields")
            source_events = _object_list(item["source_event_ids"], "feature source events")
            values.append(
                RawFeatureValue(
                    name=_text_value(item["name"], "feature value name"),
                    value=_optional_float(item["value"], "feature value"),
                    source_event_ids=tuple(
                        _text_value(event, "feature source event") for event in source_events
                    ),
                )
            )
        rows.append(
            RawFeatureRow(
                target_instrument_id=_text_value(row["target_instrument_id"], "feature instrument"),
                decision_time=_utc_value(row["decision_time"], "feature decision time"),
                feature_data_asof=_utc_value(row["feature_data_asof"], "feature data cutoff"),
                latest_feature_bar_end=_utc_value(
                    row["latest_feature_bar_end"], "latest feature bar"
                ),
                feature_set_id=_text_value(row["feature_set_id"], "feature set ID"),
                values=tuple(values),
            )
        )
    dataset = R2FeatureDataset(
        rows=tuple(rows),
        feature_schema=tuple(schema),
        feature_set_name=_text_value(payload["feature_set_name"], "feature set name"),
        feature_set_id=_text_value(payload["feature_set_id"], "feature set ID"),
        raw_feature_schema_id=_text_value(
            payload["raw_feature_schema_id"], "raw feature schema ID"
        ),
        observation_dataset_id=_text_value(
            payload["observation_dataset_id"], "feature observations"
        ),
        panel_dataset_id=_text_value(payload["panel_dataset_id"], "feature panel"),
        target_dataset_id=_text_value(payload["target_dataset_id"], "feature targets"),
        fold_dataset_id=_text_value(payload["fold_dataset_id"], "feature folds"),
        experiment_configuration_id=_text_value(
            payload["experiment_configuration_id"], "feature experiment"
        ),
        evidence_class=EvidenceClass(
            _text_value(payload["evidence_class"], "feature evidence class")
        ),
        holdout_excluded=payload["holdout_excluded"] is True,
        market_data_source_class=MarketDataSourceClass(
            _text_value(payload["market_data_source_class"], "feature source class")
        ),
        dataset_id=_text_value(payload["dataset_id"], "feature dataset ID"),
    )
    if payload["row_count"] != len(dataset.rows):
        raise ValueError("training feature row count does not authenticate")
    return dataset


def _target_dataset_payload(dataset: TargetDataset) -> dict[str, object]:
    return {
        "contract": TARGET_DATASET_CONTRACT,
        "schema_version": 1,
        "dataset_id": dataset.dataset_id,
        "observation_dataset_id": dataset.observation_dataset_id,
        "foundation_configuration_id": dataset.foundation_configuration_id,
        "rows": [row.as_json() for row in dataset.rows],
    }


def _target_dataset_from_payload(
    payload: Mapping[str, object],
    *,
    field: str,
) -> TargetDataset:
    if set(payload) != _TARGET_DATASET_FIELDS:
        raise ValueError(f"{field} has unknown or missing fields")
    if payload.get("contract") != TARGET_DATASET_CONTRACT or payload.get("schema_version") != 1:
        raise ValueError(f"{field} contract is unsupported")
    rows: list[TargetRow] = []
    expected_fields = {
        "instrument_id",
        "decision_time",
        "horizon_seconds",
        "target_basis",
        "target_revision_policy",
        "target_start_time",
        "target_end_time",
        "target_freeze_at",
        "target_available_at",
        "label_start_close",
        "label_end_close",
        "log_return",
        "return_disposition",
        "start_event_id",
        "end_event_id",
        "upper_log_excursion",
        "lower_log_excursion",
        "excursion_disposition",
    }
    for raw_row in _object_list(payload["rows"], f"{field} rows"):
        row = _object_dict(raw_row, f"{field} row")
        if set(row) != expected_fields:
            raise ValueError(f"{field} row has unknown or missing fields")
        start_event = row["start_event_id"]
        end_event = row["end_event_id"]
        rows.append(
            TargetRow(
                instrument_id=_text_value(row["instrument_id"], "target instrument"),
                decision_time=_utc_value(row["decision_time"], "target decision time"),
                horizon=timedelta(seconds=_float_value(row["horizon_seconds"], "target horizon")),
                target_basis=PriceBasis(_text_value(row["target_basis"], "target basis")),
                target_revision_policy=_text_value(
                    row["target_revision_policy"], "target revision policy"
                ),
                target_start_time=_utc_value(row["target_start_time"], "target start"),
                target_end_time=_utc_value(row["target_end_time"], "target end"),
                target_freeze_at=_utc_value(row["target_freeze_at"], "target freeze"),
                target_available_at=_utc_value(row["target_available_at"], "target availability"),
                label_start_close=(
                    Decimal(str(row["label_start_close"]))
                    if row["label_start_close"] is not None
                    else None
                ),
                label_end_close=(
                    Decimal(str(row["label_end_close"]))
                    if row["label_end_close"] is not None
                    else None
                ),
                log_return=_optional_float(row["log_return"], "target return"),
                return_disposition=ReturnDisposition(
                    _text_value(row["return_disposition"], "target return disposition")
                ),
                start_event_id=UUID(_text_value(start_event, "target start event"))
                if start_event is not None
                else None,
                end_event_id=UUID(_text_value(end_event, "target end event"))
                if end_event is not None
                else None,
                upper_log_excursion=_optional_float(
                    row["upper_log_excursion"], "target upper excursion"
                ),
                lower_log_excursion=_optional_float(
                    row["lower_log_excursion"], "target lower excursion"
                ),
                excursion_disposition=ExcursionDisposition(
                    _text_value(row["excursion_disposition"], "target excursion disposition")
                ),
            )
        )
    return TargetDataset._from_verified_rows(
        rows,
        observation_dataset_id=_text_value(
            payload["observation_dataset_id"], "target observations"
        ),
        foundation_configuration_id=_text_value(
            payload["foundation_configuration_id"], "target foundation"
        ),
        dataset_id=_text_value(payload["dataset_id"], "target dataset ID"),
    )


def _opportunities_from_selection(
    selection: R2HoldoutSelectionManifest,
    source: R2HoldoutTargetSource,
) -> tuple[HoldoutTargetOpportunity, ...]:
    if not isinstance(source, R2HoldoutTargetSource):
        raise TypeError("holdout selection requires authenticated source authority")
    (
        projection_id,
        registry_id,
        opportunity_count,
        opportunity_digest,
    ) = holdout_selection_compact_bindings(source)
    policy = selection.evaluation_policy
    expected_source = policy.get("target_dataset_id")
    expected_source_id = policy.get("holdout_target_source_id")
    expected_pre_holdout = policy.get("pre_holdout_target_dataset_id")
    expected_projection = policy.get("pre_holdout_projection_id")
    expected_registry = policy.get("holdout_opportunity_registry_id")
    expected_count = policy.get("holdout_opportunity_count")
    expected_digest = policy.get("holdout_opportunity_digest")
    primary_horizon = policy.get("primary_horizon_seconds")
    if (
        source.source_target_dataset_id != expected_source
        or source.source_id != expected_source_id
        or source.pre_holdout_target_dataset.dataset_id != expected_pre_holdout
        or source.holdout_range != selection.holdout_range
        or source.primary_horizon_seconds != primary_horizon
    ):
        raise ValueError("authenticated source differs from the frozen holdout policy")
    if projection_id != expected_projection:
        raise ValueError("derived compact pre-holdout projection ID differs")
    if registry_id != expected_registry:
        raise ValueError("derived compact opportunity registry ID differs")
    if (
        isinstance(expected_count, bool)
        or not isinstance(expected_count, int)
        or expected_count <= 0
        or opportunity_count != expected_count
        or not isinstance(expected_digest, str)
        or opportunity_digest != expected_digest
    ):
        raise ValueError("derived compact opportunity summary differs")
    return source.opportunities


def _training_target_dataset_from_payload(payload: Mapping[str, object]) -> TargetDataset:
    if set(payload) != _TRAINING_TARGET_FIELDS:
        raise ValueError("training target child has unknown or missing fields")
    if (
        payload.get("contract") != _TRAINING_TARGET_PROJECTION_CONTRACT
        or payload.get("schema_version") != 1
    ):
        raise ValueError("training target child contract is unsupported")
    nested = _object_dict(payload["target_dataset"], "training target dataset")
    return _target_dataset_from_payload(nested, field="training target dataset")


def _training_target_source_id(payload: Mapping[str, object]) -> str:
    return _text_value(payload["source_target_dataset_id"], "training target source dataset ID")


def _training_feature_payload(dataset: R2FeatureDataset) -> dict[str, object]:
    return {
        **dataset.manifest_json(),
        "rows": [row.as_json() for row in dataset.rows],
    }


def _training_target_payload(
    dataset: TargetDataset,
    *,
    source_target_dataset_id: str | None = None,
) -> dict[str, object]:
    return {
        "contract": _TRAINING_TARGET_PROJECTION_CONTRACT,
        "schema_version": 1,
        "source_target_dataset_id": source_target_dataset_id or dataset.dataset_id,
        "target_dataset": _target_dataset_payload(dataset),
    }


def _training_child_ids_from_fit(payload: Mapping[str, object]) -> tuple[str, str]:
    preprocessing = _object_dict(payload["preprocessing"], "final fit preprocessing")
    return (
        _text_value(
            preprocessing.get("training_feature_dataset_id"),
            "training feature dataset ID",
        ),
        _text_value(
            preprocessing.get("training_target_dataset_id"),
            "training target dataset ID",
        ),
    )


def _training_dataset_paths(
    fit_payloads: Sequence[Mapping[str, object]],
) -> tuple[str, ...]:
    feature_ids: set[str] = set()
    target_ids: set[str] = set()
    for payload in fit_payloads:
        feature_id, target_id = _training_child_ids_from_fit(payload)
        feature_ids.add(feature_id)
        target_ids.add(target_id)
    return tuple(
        sorted(
            [f"training/features/{dataset_id}.json" for dataset_id in feature_ids]
            + [f"training/targets/{dataset_id}.json" for dataset_id in target_ids]
        )
    )


def _training_paths_for_seal(
    root: Path,
    seal: R2HoldoutForecastSeal,
) -> tuple[str, ...]:
    fit_payloads = tuple(
        _verify_child(
            root,
            f"fits/{fit_id}.json",
            contract="qtrad-r2-final-fit-v1",
            identity_key="fit_id",
            expected_fields=_FINAL_FIT_FIELDS,
            expected_id=fit_id,
        )
        for fit_id in seal.final_fit_ids
    )
    return _training_dataset_paths(fit_payloads)


def _load_training_children(
    root: Path,
    fit_payloads: Sequence[Mapping[str, object]],
    selection: R2HoldoutSelectionManifest,
    source: R2HoldoutTargetSource,
) -> tuple[
    dict[str, R2FeatureDataset],
    dict[str, TargetDataset],
    dict[str, str],
]:
    feature_ids: set[str] = set()
    target_ids: set[str] = set()
    for payload in fit_payloads:
        feature_id, target_id = _training_child_ids_from_fit(payload)
        feature_ids.add(feature_id)
        target_ids.add(target_id)
    features: dict[str, R2FeatureDataset] = {}
    for dataset_id in sorted(feature_ids):
        payload = _load_object(_safe_child(root, f"training/features/{dataset_id}.json"))
        dataset = _training_feature_dataset_from_payload(payload)
        if dataset.dataset_id != dataset_id:
            raise ValueError("training feature child differs from fit lineage")
        features[dataset_id] = dataset
    expected_source = selection.evaluation_policy.get("target_dataset_id")
    if not isinstance(expected_source, str):
        raise ValueError("selection is missing the authenticated target source")
    expected_pre_holdout = selection.evaluation_policy.get("pre_holdout_target_dataset_id")
    if not isinstance(expected_pre_holdout, str):
        raise ValueError("selection is missing the authenticated pre-holdout target")
    _opportunities_from_selection(selection, source)
    if source.source_target_dataset_id != expected_source:
        raise ValueError("selection target source differs from the frozen target")
    frozen_pre = source.pre_holdout_target_dataset
    if frozen_pre.dataset_id != expected_pre_holdout:
        raise ValueError("selection pre-holdout target evidence has the wrong identity")
    targets: dict[str, TargetDataset] = {}
    target_sources: dict[str, str] = {}
    for dataset_id in sorted(target_ids):
        payload = _load_object(_safe_child(root, f"training/targets/{dataset_id}.json"))
        dataset = _training_target_dataset_from_payload(payload)
        if dataset.dataset_id != dataset_id:
            raise ValueError("training target child differs from fit lineage")
        if dataset.dataset_id != expected_pre_holdout:
            raise ValueError("training target child differs from the frozen pre-holdout target")
        if dataset != frozen_pre:
            raise ValueError("training target child differs from frozen target evidence")
        source_id = _training_target_source_id(payload)
        if source_id != expected_source:
            raise ValueError("training target source differs from the frozen target dataset")
        targets[dataset_id] = dataset
        target_sources[dataset_id] = source_id
    return features, targets, target_sources


def _float_value(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    return float(value)


def _optional_float(value: object, field: str) -> float | None:
    if value is None:
        return None
    return _float_value(value, field)


def _int_value(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    atomic_create(path, canonical_bytes(payload))


def _copy_create(source: Path, target: Path) -> None:
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"holdout transfer source must be a regular file: {source}")
    atomic_create(target, source.read_bytes())


def _feature_set_id(value: object) -> str:
    payload = value if isinstance(value, Mapping) else _as_json(value)
    feature_set_id = payload.get("feature_set_id")
    if not isinstance(feature_set_id, str):
        raise ValueError("holdout feature child has no feature-set identity")
    return feature_set_id


def _preparation_authority_payload(
    selection: R2HoldoutSelectionManifest,
    seal: R2HoldoutForecastSeal,
    source: R2HoldoutTargetSource,
    feature_children: Mapping[str, object],
    *,
    parent_authority: Mapping[str, object] | None = None,
) -> dict[str, object]:
    policy = selection.evaluation_policy
    parent = dict(parent_authority or {})
    if seal.holdout_scope is HoldoutScope.CONFIRMATORY:
        f2_parent = parent.get("f2")
        oof_parent = parent.get("oof")
        foundation_parent = parent.get("foundation")
        target_parent = parent.get("target_source")
        feature_parent = parent.get("feature_source")
        selection_parent = parent.get("selection")
        if not all(
            isinstance(value, Mapping)
            for value in (
                f2_parent,
                oof_parent,
                foundation_parent,
                target_parent,
                feature_parent,
                selection_parent,
            )
        ):
            raise ValueError("confirmatory preparation authority is incomplete")
        f2_parent = cast(Mapping[str, object], f2_parent)
        oof_parent = cast(Mapping[str, object], oof_parent)
        foundation_parent = cast(Mapping[str, object], foundation_parent)
        target_parent = cast(Mapping[str, object], target_parent)
        feature_parent = cast(Mapping[str, object], feature_parent)
        selection_parent = cast(Mapping[str, object], selection_parent)
        feature_required_ids = [
            (feature_parent, "authority_id"),
            *(
                (feature_parent, key)
                for key in (
                    "foundation_bundle_id",
                    "foundation_configuration_id",
                    "observation_dataset_id",
                    "panel_dataset_id",
                    "child_closure_id",
                    "target_child_closure_id",
                )
                if key in feature_parent
            ),
        ]
        required_ids = (
            (f2_parent, "promotion_id"),
            (oof_parent, "semantic_id"),
            (oof_parent, "closure_id"),
            (oof_parent, "verification_id"),
            (foundation_parent, "semantic_id"),
            (foundation_parent, "verification_id"),
            (foundation_parent, "promotion_id"),
            (target_parent, "semantic_id"),
            (target_parent, "closure_id"),
            (target_parent, "verification_id"),
            *feature_required_ids,
            (selection_parent, "manifest_id"),
            (selection_parent, "authority_id"),
        )
        if any(
            not isinstance(mapping.get(key), str) or not mapping[key]
            for mapping, key in required_ids
        ):
            raise ValueError("confirmatory preparation authority contains an empty identity")
        if any(
            not isinstance(feature_parent.get(key), Mapping) or not feature_parent[key]
            for key in ("observation_reference", "panel_reference")
            if key in feature_parent
        ):
            raise ValueError("confirmatory feature authority contains an empty child reference")
        authority_ids = {
            "f2_promotion_id": f2_parent["promotion_id"],
            "oof_semantic_id": oof_parent["semantic_id"],
            "oof_closure_id": oof_parent["closure_id"],
            "oof_verification_id": oof_parent["verification_id"],
            "foundation_semantic_id": foundation_parent["semantic_id"],
            "foundation_verification_id": foundation_parent["verification_id"],
            "foundation_promotion_id": foundation_parent["promotion_id"],
            "target_source_semantic_id": target_parent["semantic_id"],
            "target_source_closure_id": target_parent["closure_id"],
            "target_source_verification_id": target_parent["verification_id"],
            "feature_source_authority_id": feature_parent["authority_id"],
            "selection_manifest_id": selection_parent["manifest_id"],
            "selection_authority_id": selection_parent["authority_id"],
        }
    else:
        feature_parent = parent.get("feature_source")
        authority_ids = {
            key: policy[key]
            for key in (
                "holdout_target_source_closure_id",
                "holdout_target_source_verification_id",
                "f2_promotion_id",
            )
            if isinstance(policy.get(key), str) and policy[key]
        }
    target_source = {
        "source_id": source.source_id,
        "source_target_dataset_id": source.source_target_dataset_id,
        "pre_holdout_target_dataset_id": source.pre_holdout_target_dataset.dataset_id,
        "pre_holdout_projection_id": policy.get("pre_holdout_projection_id"),
        "opportunity_registry_id": policy.get("holdout_opportunity_registry_id"),
        "opportunity_count": policy.get("holdout_opportunity_count"),
        "opportunity_digest": policy.get("holdout_opportunity_digest"),
        "authority_ids": authority_ids,
    }
    feature_bindings = [
        {
            "configuration_id": configuration_id,
            "dataset_id": dataset_id,
            "feature_set_id": next(
                (
                    feature_set_id
                    for (
                        registry_configuration,
                        _family,
                        feature_set_id,
                        registry_dataset_id,
                        _manifest_id,
                    ) in selection.configuration_registry
                    if registry_configuration == configuration_id
                    and registry_dataset_id == dataset_id
                    and feature_set_id is not None
                ),
                _feature_set_id(feature_children[dataset_id]),
            ),
            "manifest_id": next(
                (
                    manifest_id
                    for (
                        registry_configuration,
                        _family,
                        _feature_set_id_value,
                        registry_dataset_id,
                        manifest_id,
                    ) in selection.configuration_registry
                    if registry_configuration == configuration_id
                    and registry_dataset_id == dataset_id
                ),
                None,
            ),
            "parent_authority": (
                dict(feature_parent) if isinstance(feature_parent, Mapping) else None
            ),
        }
        for configuration_id, dataset_id in seal.configuration_feature_dataset_ids
        if dataset_id is not None and dataset_id in feature_children
    ]
    semantic = {
        "contract": _PREPARATION_AUTHORITY_CONTRACT,
        "schema_version": 1,
        "selection_manifest_id": selection.manifest_id,
        "seal_id": seal.seal_id,
        "target_source": target_source,
        "feature_bindings": feature_bindings,
        "parent_authority": dict(parent),
    }
    return {**semantic, "authority_id": _semantic_id(semantic, "authority_id")}


def _verify_preparation_authority(
    path: Path,
    *,
    selection: R2HoldoutSelectionManifest,
    seal: R2HoldoutForecastSeal,
    source: R2HoldoutTargetSource,
    feature_children: Mapping[str, object],
    parent_authority: Mapping[str, object] | None = None,
) -> None:
    payload = _load_object(path / _PREPARATION_AUTHORITY_FILE)
    if set(payload) != _PREPARATION_AUTHORITY_FIELDS:
        raise ValueError("preparation authority has unknown or missing fields")
    if (
        payload.get("contract") != _PREPARATION_AUTHORITY_CONTRACT
        or payload.get("schema_version") != 1
        or payload.get("selection_manifest_id") != selection.manifest_id
        or payload.get("seal_id") != seal.seal_id
        or payload.get("authority_id")
        != _semantic_id(
            {key: payload[key] for key in payload if key != "authority_id"}, "authority_id"
        )
    ):
        raise ValueError("preparation authority does not bind the exact selection and seal")
    expected = _preparation_authority_payload(
        selection,
        seal,
        source,
        feature_children,
        parent_authority=parent_authority,
    )
    if payload != expected:
        raise ValueError("preparation authority differs from authenticated immediate parents")


def _replace_json(path: Path, payload: Mapping[str, object]) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"expected a regular claim file: {path}")
    temporary = path.with_name(f".{path.name}.next")
    encoded = canonical_bytes(payload)
    if temporary.exists() or temporary.is_symlink():
        if temporary.is_symlink() or not temporary.is_file() or temporary.read_bytes() != encoded:
            raise ValueError(
                f"claim transition temporary differs from its intended bytes: {temporary}"
            )
    else:
        atomic_create(temporary, encoded)
    os.replace(temporary, path)


_TRAINING_FEATURE_FIELDS = {
    "contract",
    "schema_version",
    "dataset_id",
    "feature_set_name",
    "feature_set_id",
    "raw_feature_schema_id",
    "feature_schema",
    "observation_dataset_id",
    "panel_dataset_id",
    "target_dataset_id",
    "fold_dataset_id",
    "experiment_configuration_id",
    "evidence_class",
    "market_data_source_class",
    "holdout_excluded",
    "row_count",
    "rows",
}
_TARGET_DATASET_FIELDS = {
    "contract",
    "schema_version",
    "dataset_id",
    "observation_dataset_id",
    "foundation_configuration_id",
    "rows",
}
_TRAINING_TARGET_PROJECTION_CONTRACT = "qtrad-r2-target-projection-v1"
_TRAINING_TARGET_FIELDS = {
    "contract",
    "schema_version",
    "source_target_dataset_id",
    "target_dataset",
}

_SELECTION_FIELDS = {
    "contract",
    "schema_version",
    "experiment_configuration_id",
    "foundation_bundle_id",
    "oof_id",
    "evaluation_report_id",
    "prior_selection_manifest_id",
    "source_class",
    "evidence_class",
    "holdout_scope",
    "evaluated_configuration_ids",
    "selected_configuration_ids",
    "control_configuration_ids",
    "holdout_configuration_ids",
    "comparator_families",
    "configuration_registry",
    "metric_policy",
    "threshold_policy",
    "evaluation_policy",
    "final_fitting_policy",
    "questions",
    "holdout_range",
    "experiment_count",
    "runtime_identities",
    "frozen_metadata",
    "frozen_at",
    "frozen_by",
    "state",
    "holdout_outcomes_accessed",
    "manifest_id",
}
_FEATURE_FIELDS = {
    "contract",
    "schema_version",
    "selection_manifest_id",
    "experiment_configuration_id",
    "foundation_bundle_id",
    "observation_dataset_id",
    "panel_dataset_id",
    "feature_schema_id",
    "feature_set_id",
    "source_class",
    "evidence_class",
    "holdout_scope",
    "holdout_range",
    "expected_opportunity_ids",
    "unavailable_opportunity_ids",
    "rows",
    "opportunity_target_ids",
    "target_dataset_id",
    "outcome_blind_projection",
    "holdout_outcomes_accessed",
    "dataset_id",
}
_FINAL_FIT_FIELDS = {
    "contract",
    "schema_version",
    "selection_manifest_id",
    "configuration_id",
    "model_family",
    "target_instrument_id",
    "feature_dataset_id",
    "feature_schema_id",
    "training_cutoff",
    "training_target_ids",
    "purged_target_ids",
    "inner_fit_target_ids",
    "inner_validation_target_ids",
    "preprocessing",
    "alpha_candidate_scores",
    "selected_alpha",
    "sample_weights",
    "coefficients",
    "intercept",
    "disposition",
    "failure_reason",
    "diagnostics",
    "runtime_identities",
    "evidence_class",
    "holdout_scope",
    "fit_id",
}
_FINAL_FIT_PARTITION_ARRAY_FIELDS = (
    "training_target_ids",
    "purged_target_ids",
    "inner_fit_target_ids",
    "inner_validation_target_ids",
    "alpha_candidate_scores",
    "sample_weights",
    "coefficients",
    "diagnostics",
    "preprocessing.inner.training_target_ids",
    "preprocessing.inner.sample_weights",
    "preprocessing.outer.training_target_ids",
    "preprocessing.outer.sample_weights",
)
_FINAL_FIT_REQUIRED_PARTITION_ARRAY_FIELDS = _FINAL_FIT_PARTITION_ARRAY_FIELDS[:8]


def _final_fit_partition_fields(payload: Mapping[str, object]) -> tuple[str, ...]:
    fields: list[str] = list(_FINAL_FIT_REQUIRED_PARTITION_ARRAY_FIELDS)
    preprocessing = payload.get("preprocessing")
    if not isinstance(preprocessing, Mapping):
        return tuple(fields)
    for fit_name in ("inner", "outer"):
        fit = preprocessing.get(fit_name)
        if fit is None:
            continue
        if not isinstance(fit, Mapping):
            raise ValueError(f"final-fit preprocessing {fit_name} state is not an object")
        for field in ("training_target_ids", "sample_weights"):
            if field in fit:
                fields.append(f"preprocessing.{fit_name}.{field}")
    return tuple(fields)


def _final_fit_partition_fields_are_valid(fields: Sequence[str]) -> bool:
    values = tuple(fields)
    canonical = tuple(field for field in _FINAL_FIT_PARTITION_ARRAY_FIELDS if field in values)
    return values == canonical and set(_FINAL_FIT_REQUIRED_PARTITION_ARRAY_FIELDS) <= set(values)


_FORECAST_FIELDS = {
    "contract",
    "schema_version",
    "selection_manifest_id",
    "feature_dataset_id",
    "configuration_id",
    "final_fit_id",
    "final_fit_ids",
    "rows",
    "expected_opportunity_ids",
    "opportunity_target_ids",
    "source_class",
    "evidence_class",
    "holdout_scope",
    "holdout_outcomes_accessed",
    "dataset_id",
}
_COVERAGE_FIELDS = {
    "contract",
    "schema_version",
    "selection_manifest_id",
    "feature_dataset_id",
    "configuration_id",
    "expected_opportunity_ids",
    "rows",
    "source_class",
    "evidence_class",
    "holdout_scope",
    "holdout_outcomes_accessed",
    "coverage_id",
}
_SEAL_FIELDS = {
    "contract",
    "schema_version",
    "selection_manifest_id",
    "configuration_feature_dataset_ids",
    "final_fit_ids",
    "forecast_dataset_ids",
    "coverage_ids",
    "metric_policy",
    "comparison_support",
    "forecast_buckets",
    "state_buckets",
    "configuration_pairs",
    "coverage_rules",
    "questions",
    "source_class",
    "evidence_class",
    "holdout_scope",
    "runtime_identities",
    "prepared_at",
    "prepared_by",
    "state",
    "holdout_outcomes_accessed",
    "seal_id",
}
_OPENED_FIELDS = {
    "contract",
    "schema_version",
    "selection_manifest_id",
    "seal_id",
    "opened_at",
    "opened_by",
    "acknowledgement",
    "expected_selection_manifest_id",
    "expected_seal_id",
    "state",
    "marker_id",
}
_CONSUMED_FIELDS = {
    "contract",
    "schema_version",
    "selection_manifest_id",
    "seal_id",
    "opened_marker_id",
    "consumed_at",
    "consumed_by",
    "evaluation_id",
    "outcome_accessed",
    "state",
    "marker_id",
}
_OUTCOME_EVIDENCE_FIELDS = {
    "contract",
    "schema_version",
    "selection_manifest_id",
    "seal_id",
    "opened_marker_id",
    "experiment_configuration_id",
    "foundation_bundle_id",
    "feature_dataset_id",
    "target_dataset_id",
    "holdout_range",
    "expected_target_ids",
    "source_row_ids",
    "outcomes",
    "source_class",
    "evidence_class",
    "holdout_scope",
    "outcome_evidence_id",
}
_EVALUATION_FIELDS = {
    "contract",
    "schema_version",
    "selection_manifest_id",
    "seal_id",
    "opened_marker_id",
    "questions",
    "source_class",
    "evidence_class",
    "holdout_scope",
    "holdout_outcomes_accessed",
    "evaluation_id",
}

_PREPARATION_CLAIM_FILE = ".preparation-claim.json"
_PREPARATION_CLAIM_NEXT_FILE = "..preparation-claim.json.next"
_PREPARATION_USAGE_FILE = ".preparation-source-claim.json"
_PREPARATION_TRANSFER_INTENT_FILE = ".preparation-transfer-intent.json"
_PREPARATION_CLAIM_CONTRACT = "qtrad-r2-holdout-preparation-claim-v1"
_PREPARATION_USAGE_CONTRACT = "qtrad-r2-holdout-preparation-usage-v1"
_PREPARATION_TRANSFER_INTENT_CONTRACT = "qtrad-r2-holdout-transfer-intent-v1"

_PREPARATION_CLAIM_FIELDS = {
    "contract",
    "schema_version",
    "selection_manifest_id",
    "seal_id",
    "state",
    "transfer_id",
    "source_claim_id",
    "claim_id",
}
_PREPARATION_USAGE_FIELDS = {
    "contract",
    "schema_version",
    "selection_manifest_id",
    "seal_id",
    "source_claim_id",
    "destination_claim_id",
    "destination_root_id",
    "transfer_id",
    "usage_id",
}
_PREPARATION_TRANSFER_INTENT_FIELDS = {
    "contract",
    "schema_version",
    "selection_manifest_id",
    "seal_id",
    "source_claim_id",
    "transfer_id",
    "destination_root_id",
    "intent_id",
}
_PREPARATION_AUTHORITY_FILE = "authority.json"
_PREPARATION_AUTHORITY_CONTRACT = "qtrad-r2-holdout-preparation-authority-v1"
_PREPARATION_AUTHORITY_FIELDS = {
    "contract",
    "schema_version",
    "selection_manifest_id",
    "seal_id",
    "target_source",
    "feature_bindings",
    "parent_authority",
    "authority_id",
}


def write_holdout_selection(output: Path, manifest: R2HoldoutSelectionManifest) -> Path:
    """Persist PR A's selection freeze as one create-only file."""
    if manifest.holdout_scope is HoldoutScope.CONFIRMATORY:
        raise ValueError("G2 selection freeze is restricted to disposable fixtures")
    _write_json(output, manifest.as_json())
    return output


def verify_holdout_selection(path: Path) -> R2HoldoutSelectionManifest:
    payload = _load_object(path)
    if set(payload) != _SELECTION_FIELDS:
        raise ValueError(f"{R2_HOLDOUT_SELECTION_CONTRACT} child has unknown or missing fields")
    if (
        payload.get("contract") != R2_HOLDOUT_SELECTION_CONTRACT
        or payload.get("schema_version") != 1
    ):
        raise ValueError(f"{R2_HOLDOUT_SELECTION_CONTRACT} child contract is unsupported")
    manifest = R2HoldoutSelectionManifest.from_json(payload)
    if payload["manifest_id"] != manifest.manifest_id:
        raise ValueError(
            f"{R2_HOLDOUT_SELECTION_CONTRACT} child identity does not authenticate its content"
        )
    return manifest


def _preparation_claim(
    selection_manifest_id: str,
    seal_id: str,
    *,
    state: str = "AVAILABLE",
    transfer_id: str | None = None,
    source_claim_id: str | None = None,
) -> dict[str, object]:
    semantic = {
        "contract": _PREPARATION_CLAIM_CONTRACT,
        "schema_version": 1,
        "selection_manifest_id": selection_manifest_id,
        "seal_id": seal_id,
        "state": state,
        "transfer_id": transfer_id,
        "source_claim_id": source_claim_id,
    }
    return {
        **semantic,
        "claim_id": _semantic_id(semantic, "claim_id"),
    }


def _preparation_transfer_id(
    selection_manifest_id: str,
    seal_id: str,
    source_claim_id: str,
) -> str:
    semantic = {
        "contract": "qtrad-r2-holdout-preparation-transfer-v1",
        "schema_version": 1,
        "selection_manifest_id": selection_manifest_id,
        "seal_id": seal_id,
        "source_claim_id": source_claim_id,
    }
    return _semantic_id(semantic, "transfer_id")


def _preparation_transfer_intent(
    selection_manifest_id: str,
    seal_id: str,
    source_claim_id: str,
    transfer_id: str,
    destination_root_id: str,
) -> dict[str, object]:
    semantic = {
        "contract": "qtrad-r2-holdout-transfer-intent-v1",
        "schema_version": 1,
        "selection_manifest_id": selection_manifest_id,
        "seal_id": seal_id,
        "source_claim_id": source_claim_id,
        "transfer_id": transfer_id,
        "destination_root_id": destination_root_id,
    }
    return {
        **semantic,
        "intent_id": _semantic_id(semantic, "intent_id"),
    }


def _preparation_usage(
    selection_manifest_id: str,
    seal_id: str,
    source_claim_id: str,
    destination_claim_id: str,
    transfer_id: str,
    destination_root_id: str,
) -> dict[str, object]:
    semantic = {
        "contract": _PREPARATION_USAGE_CONTRACT,
        "schema_version": 1,
        "selection_manifest_id": selection_manifest_id,
        "seal_id": seal_id,
        "source_claim_id": source_claim_id,
        "destination_claim_id": destination_claim_id,
        "transfer_id": transfer_id,
        "destination_root_id": destination_root_id,
    }
    return {
        **semantic,
        "usage_id": _semantic_id(semantic, "usage_id"),
    }


def _preparation_claim_transition(
    root: Path,
    selection_manifest_id: str,
    seal_id: str,
) -> tuple[dict[str, object], dict[str, object]]:
    current = _load_object(root / _PREPARATION_CLAIM_FILE)
    initial_unopened = _preparation_claim(
        selection_manifest_id,
        seal_id,
        state="OWNED_UNOPENED",
    )
    initial_opened = _preparation_claim(
        selection_manifest_id,
        seal_id,
        state="OWNED_OPENED",
    )
    if current == initial_unopened:
        return current, initial_opened
    if current == initial_opened:
        return current, current
    if current.get("state") == "TRANSFERRED":
        raise ValueError("transferred holdout preparation is not revealable from the source root")
    transfer_id = current.get("transfer_id")
    source_claim_id = current.get("source_claim_id")
    if not isinstance(transfer_id, str) or not isinstance(source_claim_id, str):
        raise ValueError("holdout preparation is not an owned transferred preparation")
    owned_unopened = _preparation_claim(
        selection_manifest_id,
        seal_id,
        state="OWNED_UNOPENED",
        transfer_id=transfer_id,
        source_claim_id=source_claim_id,
    )
    owned_opened = _preparation_claim(
        selection_manifest_id,
        seal_id,
        state="OWNED_OPENED",
        transfer_id=transfer_id,
        source_claim_id=source_claim_id,
    )
    if current == owned_unopened:
        return current, owned_opened
    if current == owned_opened:
        return current, current
    raise ValueError("holdout preparation is not owned by this root")


def _claim_preparation(
    root: Path,
    selection_manifest_id: str,
    seal_id: str,
) -> None:
    claim_path = root / _PREPARATION_CLAIM_FILE
    current, opened = _preparation_claim_transition(root, selection_manifest_id, seal_id)
    if current != opened:
        _replace_json(claim_path, opened)


def _replay_final_fit(
    selection: R2HoldoutSelectionManifest,
    payload: Mapping[str, object],
    *,
    training_feature_dataset: R2FeatureDataset,
    training_target_dataset: TargetDataset,
    training_target_source_dataset_id: str,
) -> None:
    from qtrad.application.r2_holdout import fit_final_ridge
    from qtrad.domain.r2_holdout import FinalFitDisposition
    from qtrad.domain.r2_readiness import ModelFamily

    disposition = str(payload.get("disposition"))
    try:
        FinalFitDisposition(disposition)
    except ValueError as exc:
        raise ValueError("final fit has an unsupported disposition") from exc
    preprocessing = payload.get("preprocessing")
    if not isinstance(preprocessing, Mapping):
        raise ValueError("final fit preprocessing evidence must be an object")
    expected_source = selection.evaluation_policy.get("target_dataset_id")
    if not isinstance(expected_source, str) or training_target_source_dataset_id != expected_source:
        raise ValueError("final fit target source differs from the frozen target dataset")
    expected_lineage = {
        "selection_manifest_id": selection.manifest_id,
        "experiment_configuration_id": selection.experiment_configuration_id,
        "foundation_bundle_id": selection.foundation_bundle_id,
        "configuration_id": str(payload["configuration_id"]),
        "model_family": str(payload["model_family"]),
        "feature_dataset_id": str(payload["feature_dataset_id"]),
        "feature_schema_id": str(payload["feature_schema_id"]),
        "training_feature_dataset_id": training_feature_dataset.dataset_id,
        "training_target_dataset_id": training_target_dataset.dataset_id,
        "training_target_source_dataset_id": expected_source,
    }
    for key, expected in expected_lineage.items():
        if preprocessing.get(key) != expected:
            raise ValueError(f"final fit training evidence is not bound to {key}")
    failure_mode = preprocessing.get("failure_mode")
    forced_fit_disposition: FinalFitDisposition | None = None
    if failure_mode is not None:
        if failure_mode != "FORCED_FIXTURE":
            raise ValueError("final fit has an unsupported failure mode")
        forced_fit_disposition = FinalFitDisposition(disposition)
    replayed = fit_final_ridge(
        selection=selection,
        configuration_id=str(payload["configuration_id"]),
        model_family=ModelFamily(str(payload["model_family"])),
        target_instrument_id=(
            None
            if payload.get("target_instrument_id") is None
            else str(payload["target_instrument_id"])
        ),
        feature_dataset_id=str(payload["feature_dataset_id"]),
        feature_schema_id=str(payload["feature_schema_id"]),
        training_feature_dataset=training_feature_dataset,
        training_target_dataset=training_target_dataset,
        training_target_source_dataset_id=training_target_source_dataset_id,
        policy=selection.final_fitting_policy,
        training_cutoff=_utc_value(payload["training_cutoff"], "final fit cutoff"),
        purged_target_ids=tuple(
            str(item) for item in cast(list[object], payload["purged_target_ids"])
        ),
        forced_disposition=forced_fit_disposition,
        forced_failure_reason=(
            str(payload["failure_reason"]) if forced_fit_disposition is not None else None
        ),
        _confirmatory_token=(
            _CONFIRMATORY_G2_PREPARATION_TOKEN
            if selection.holdout_scope is HoldoutScope.CONFIRMATORY
            else None
        ),
    )
    if replayed.as_json() != dict(payload):
        raise ValueError("final fit does not replay from its authenticated training children")


def _feature_dataset_from_payload(
    payload: Mapping[str, object],
) -> R2HoldoutFeatureDataset:
    raw_range = cast(list[object], payload["holdout_range"])
    if len(raw_range) != 2:
        raise ValueError("holdout feature range must contain exactly two timestamps")
    raw_rows = _object_list(payload["rows"], "holdout feature rows")
    rows = tuple(
        R2HoldoutFeatureRow(
            opportunity_id=str(raw["opportunity_id"]),
            target_id=str(raw["target_id"]),
            instrument_id=str(raw["instrument_id"]),
            decision_time=datetime.fromisoformat(str(raw["decision_time"])),
            feature_cutoff=datetime.fromisoformat(str(raw["feature_cutoff"])),
            latest_feature_bar_end=datetime.fromisoformat(str(raw["latest_feature_bar_end"])),
            feature_schema_id=str(raw["feature_schema_id"]),
            values=tuple(
                float(cast(float | int | str, item)) for item in cast(list[object], raw["values"])
            ),
            row_id=str(raw["row_id"]),
        )
        for raw in (cast(Mapping[str, object], item) for item in raw_rows)
    )
    return R2HoldoutFeatureDataset(
        selection_manifest_id=str(payload["selection_manifest_id"]),
        experiment_configuration_id=str(payload["experiment_configuration_id"]),
        foundation_bundle_id=str(payload["foundation_bundle_id"]),
        observation_dataset_id=str(payload["observation_dataset_id"]),
        panel_dataset_id=str(payload["panel_dataset_id"]),
        feature_schema_id=str(payload["feature_schema_id"]),
        feature_set_id=str(payload["feature_set_id"]),
        source_class=MarketDataSourceClass(str(payload["source_class"])),
        evidence_class=EvidenceClass(str(payload["evidence_class"])),
        holdout_scope=HoldoutScope(str(payload["holdout_scope"])),
        holdout_range=(
            datetime.fromisoformat(str(raw_range[0])),
            datetime.fromisoformat(str(raw_range[1])),
        ),
        expected_opportunity_ids=tuple(
            str(item) for item in cast(list[object], payload["expected_opportunity_ids"])
        ),
        unavailable_opportunity_ids=tuple(
            str(item) for item in cast(list[object], payload["unavailable_opportunity_ids"])
        ),
        rows=rows,
        opportunity_target_ids=tuple(
            (str(cast(list[object], item)[0]), str(cast(list[object], item)[1]))
            for item in cast(list[object], payload.get("opportunity_target_ids", []))
        ),
        target_dataset_id=(
            None if payload.get("target_dataset_id") is None else str(payload["target_dataset_id"])
        ),
        outcome_blind_projection=str(payload["outcome_blind_projection"]),
        holdout_outcomes_accessed=payload["holdout_outcomes_accessed"] is True,
        dataset_id=str(payload["dataset_id"]),
    )


def _feature_dataset_paths(
    seal: R2HoldoutForecastSeal,
) -> tuple[tuple[str, str], ...]:
    dataset_ids = tuple(
        sorted(
            {
                dataset_id
                for _configuration_id, dataset_id in seal.configuration_feature_dataset_ids
                if dataset_id is not None
            }
        )
    )
    if len(dataset_ids) <= 1:
        return tuple((dataset_id, "features.json") for dataset_id in dataset_ids)
    return tuple((dataset_id, f"features/{dataset_id}.json") for dataset_id in dataset_ids)


def _physical_child_paths(root: Path, relative: str, *, identity_field: str) -> tuple[str, ...]:
    """Return one exact top-level child and its declared bounded part files."""
    path = _safe_child(root, relative)
    encoded = path.read_bytes()
    physical = _load_object_bytes(encoded, path)
    if physical.get("storage") == PARTITIONED_ROWS_STORAGE:
        _verify_compact_header(physical, encoded=encoded)
    return (relative, *_partitioned_paths(root, relative, physical, identity_field=identity_field))


def _load_feature_payloads(
    root: Path,
    seal: R2HoldoutForecastSeal,
    *,
    _payload_cache: _PayloadCache | None = None,
) -> dict[str, Mapping[str, object]]:
    payloads: dict[str, Mapping[str, object]] = {}
    for dataset_id, relative in _feature_dataset_paths(seal):
        payloads[dataset_id] = _verify_child(
            root,
            relative,
            contract=R2_HOLDOUT_FEATURES_CONTRACT,
            identity_key="dataset_id",
            expected_fields=_FEATURE_FIELDS,
            expected_id=dataset_id,
            _payload_cache=_payload_cache,
        )
    return payloads


def _primary_feature_payload(
    payloads: Mapping[str, Mapping[str, object]],
) -> Mapping[str, object]:
    if not payloads:
        raise ValueError("holdout requires a feature child for outcome binding")
    return payloads[sorted(payloads)[0]]


def _replay_holdout_outputs(
    selection: R2HoldoutSelectionManifest,
    seal: R2HoldoutForecastSeal,
    feature_payloads: Mapping[str, Mapping[str, object]],
    fit_payloads: Sequence[Mapping[str, object]],
    forecast_payloads: Sequence[Mapping[str, object]],
    coverage_payloads: Sequence[Mapping[str, object]],
    opportunities: Sequence[HoldoutTargetOpportunity],
) -> None:
    from types import SimpleNamespace

    from qtrad.application.r2_holdout import (
        build_holdout_coverage,
        build_holdout_forecasts,
    )
    from qtrad.domain.r2_holdout import (
        FinalFitDisposition,
    )
    from qtrad.domain.r2_readiness import ModelFamily

    feature_datasets_by_configuration: dict[str, R2HoldoutFeatureDataset | None] = {}
    for configuration_id, dataset_id in seal.configuration_feature_dataset_ids:
        if dataset_id is not None:
            payload = feature_payloads.get(dataset_id)
            if payload is None:
                raise ValueError("seal feature lineage is missing a referenced child")
            feature_datasets_by_configuration[configuration_id] = _feature_dataset_from_payload(
                payload
            )
    fits = tuple(
        SimpleNamespace(
            selection_manifest_id=str(item["selection_manifest_id"]),
            configuration_id=str(item["configuration_id"]),
            model_family=ModelFamily(str(item["model_family"])),
            target_instrument_id=(
                None
                if item.get("target_instrument_id") is None
                else str(item["target_instrument_id"])
            ),
            feature_dataset_id=str(item["feature_dataset_id"]),
            feature_schema_id=str(item["feature_schema_id"]),
            fit_id=str(item["fit_id"]),
            disposition=FinalFitDisposition(str(item["disposition"])),
            preprocessing=cast(Mapping[str, object], item["preprocessing"]),
            coefficients=(
                None
                if item.get("coefficients") is None
                else tuple(
                    float(cast(float | int | str, value))
                    for value in cast(list[object], item["coefficients"])
                )
            ),
            intercept=(
                None
                if item.get("intercept") is None
                else float(cast(float | int | str, item["intercept"]))
            ),
        )
        for item in fit_payloads
    )
    expected_opportunities = tuple(sorted(item.opportunity_id for item in opportunities))
    expected_pairs = tuple(sorted((item.opportunity_id, item.target_id) for item in opportunities))
    for dataset in feature_datasets_by_configuration.values():
        if dataset is None:
            continue
        if (
            dataset.expected_opportunity_ids != expected_opportunities
            or dataset.opportunity_target_ids != expected_pairs
        ):
            raise ValueError("holdout feature replay differs from the shared opportunity registry")
    for payload in forecast_payloads:
        payload_expected = tuple(
            sorted(str(item) for item in cast(list[object], payload["expected_opportunity_ids"]))
        )
        payload_pairs = tuple(
            sorted(
                (str(cast(list[object], item)[0]), str(cast(list[object], item)[1]))
                for item in _object_list(
                    payload["opportunity_target_ids"], "forecast opportunity bindings"
                )
            )
        )
        if payload_expected != expected_opportunities or payload_pairs != expected_pairs:
            raise ValueError("holdout forecast replay differs from the shared opportunity registry")
    for payload in coverage_payloads:
        payload_expected = tuple(
            sorted(str(item) for item in cast(list[object], payload["expected_opportunity_ids"]))
        )
        if payload_expected != expected_opportunities:
            raise ValueError("holdout coverage replay differs from the shared opportunity registry")
    actual_forecasts = build_holdout_forecasts(
        selection=selection,
        feature_datasets=cast(Any, feature_datasets_by_configuration),
        final_fits=cast(Any, fits),
        opportunities=cast(Any, opportunities),
    )
    forecasts_by_id = {item.dataset_id: item for item in actual_forecasts}
    for payload in forecast_payloads:
        dataset_id = str(payload["dataset_id"])
        actual = forecasts_by_id.get(dataset_id)
        if actual is None or actual.as_json() != dict(payload):
            raise ValueError(f"holdout forecast does not replay: {dataset_id}")
    if set(forecasts_by_id) != {str(item["dataset_id"]) for item in forecast_payloads}:
        raise ValueError("holdout forecast replay set differs from persisted children")
    fits_by_configuration: dict[str, tuple[object, ...]] = {}
    for fit in fits:
        fits_by_configuration[fit.configuration_id] = (
            *fits_by_configuration.get(fit.configuration_id, ()),
            fit,
        )
    actual_coverage = {
        item.coverage_id: item
        for item in (
            build_holdout_coverage(
                selection=selection,
                feature_datasets=cast(Any, feature_datasets_by_configuration),
                final_fit=None,
                final_fits=cast(Any, fits_by_configuration.get(forecast.configuration_id, ())),
                forecast_dataset=forecast,
                opportunities=cast(Any, opportunities),
            )
            for forecast in actual_forecasts
        )
    }
    for payload in coverage_payloads:
        coverage_id = str(payload["coverage_id"])
        actual = actual_coverage.get(coverage_id)
        if actual is None or actual.as_json() != dict(payload):
            raise ValueError(f"holdout coverage does not replay: {coverage_id}")
    if set(actual_coverage) != {str(item["coverage_id"]) for item in coverage_payloads}:
        raise ValueError("holdout coverage replay set differs from persisted children")


def _verify_seal_registry(
    selection: R2HoldoutSelectionManifest,
    seal: R2HoldoutForecastSeal,
    feature_payloads: Mapping[str, Mapping[str, object]],
    fit_payloads: Sequence[Mapping[str, object]],
    forecast_payloads: Sequence[Mapping[str, object]],
    coverage_payloads: Sequence[Mapping[str, object]],
) -> None:
    expected_configurations = set(selection.holdout_configuration_ids)
    feature_by_configuration = dict(seal.configuration_feature_dataset_ids)
    if set(feature_by_configuration) != expected_configurations:
        raise ValueError("seal feature registry does not exactly cover frozen configurations")
    registry_by_configuration = {
        configuration_id: model_family.value
        for (
            configuration_id,
            model_family,
            _feature_set_id,
            _feature_dataset_id,
            _manifest_id,
        ) in selection.configuration_registry
    }
    expected_fit_configurations = {
        configuration_id
        for configuration_id in expected_configurations
        if registry_by_configuration.get(configuration_id) != "ZERO_RETURN"
    }
    expected_pairs = tuple(
        sorted(
            (question.candidate_configuration_id, question.comparator_configuration_id)
            for question in selection.questions
        )
    )
    if tuple(sorted(seal.configuration_pairs)) != expected_pairs or len(
        seal.configuration_pairs
    ) != len(set(seal.configuration_pairs)):
        raise ValueError("holdout configuration-pair registry differs from selection questions")
    fits_by_configuration: dict[str, tuple[Mapping[str, object], ...]] = {}
    for payload in fit_payloads:
        configuration_id = str(payload["configuration_id"])
        model_family = str(payload["model_family"])
        if (
            configuration_id not in expected_fit_configurations
            or model_family != registry_by_configuration.get(configuration_id)
            or payload["feature_dataset_id"] != feature_by_configuration.get(configuration_id)
            or payload["selection_manifest_id"] != seal.selection_manifest_id
        ):
            raise ValueError("final-fit registry does not reconcile to the frozen selection")
        if str(payload["feature_dataset_id"]) not in feature_payloads:
            raise ValueError("final-fit registry references an unverified feature child")
        fits_by_configuration[configuration_id] = (
            *fits_by_configuration.get(configuration_id, ()),
            payload,
        )
    for configuration_id in expected_fit_configurations:
        configuration_fits = fits_by_configuration.get(configuration_id, ())
        if not configuration_fits:
            raise ValueError("final-fit registry does not exactly cover frozen configurations")
        if registry_by_configuration[configuration_id] == "LOCAL_RIDGE":
            expected_instruments = selection.evaluation_policy.get("target_instruments")
            if not isinstance(expected_instruments, list) or {
                fit.get("target_instrument_id") for fit in configuration_fits
            } != {str(item) for item in expected_instruments}:
                raise ValueError("final-fit registry does not cover frozen local instruments")
        elif len(configuration_fits) != 1:
            raise ValueError("final-fit registry has duplicate non-local fits")
    forecasts_by_configuration: dict[str, Mapping[str, object]] = {}
    for payload in forecast_payloads:
        configuration_id = str(payload["configuration_id"])
        expected_family = registry_by_configuration.get(configuration_id)
        configuration_fits = fits_by_configuration.get(configuration_id, ())
        if (
            configuration_id in forecasts_by_configuration
            or configuration_id not in expected_configurations
            or payload["selection_manifest_id"] != seal.selection_manifest_id
            or payload["feature_dataset_id"] != feature_by_configuration.get(configuration_id)
        ):
            raise ValueError("forecast registry does not reconcile to the frozen selection")
        if expected_family == "ZERO_RETURN":
            if (
                configuration_fits
                or payload["final_fit_id"] is not None
                or payload["final_fit_ids"]
            ):
                raise ValueError("ZERO_RETURN forecast must be fitless")
        elif set(cast(list[object], payload["final_fit_ids"])) != {
            fit["fit_id"] for fit in configuration_fits
        }:
            raise ValueError("forecast registry does not reconcile to the final-fit registry")
        for raw_row in _object_list(payload["rows"], "holdout forecast rows"):
            row = _object_dict(raw_row, "holdout forecast row")
            if row.get("model_family") != expected_family:
                raise ValueError("forecast row model family differs from its registry")
        forecasts_by_configuration[configuration_id] = payload
    coverage_by_configuration: dict[str, Mapping[str, object]] = {}
    for payload in coverage_payloads:
        configuration_id = str(payload["configuration_id"])
        expected_family = registry_by_configuration.get(configuration_id)
        if (
            configuration_id in coverage_by_configuration
            or configuration_id not in expected_configurations
            or payload["selection_manifest_id"] != seal.selection_manifest_id
            or payload["feature_dataset_id"] != feature_by_configuration.get(configuration_id)
            or (
                expected_family != "ZERO_RETURN" and not fits_by_configuration.get(configuration_id)
            )
        ):
            raise ValueError("coverage registry does not reconcile to the frozen selection")
        coverage_by_configuration[configuration_id] = payload
    for registry, expected, name in (
        (fits_by_configuration, expected_fit_configurations, "final-fit"),
        (forecasts_by_configuration, expected_configurations, "forecast"),
        (coverage_by_configuration, expected_configurations, "coverage"),
    ):
        if set(registry) != expected:
            raise ValueError(f"{name} registry does not exactly cover frozen configurations")


def _verify_prepare_children(
    root: Path,
    seal: R2HoldoutForecastSeal,
    selection: R2HoldoutSelectionManifest,
    source: R2HoldoutTargetSource,
    *,
    training_feature_datasets: Mapping[str, R2FeatureDataset] | None = None,
    parent_authority: Mapping[str, object] | None = None,
    _payload_cache: _PayloadCache | None = None,
) -> None:
    opportunities = _opportunities_from_selection(selection, source)
    feature_payloads = _load_feature_payloads(root, seal, _payload_cache=_payload_cache)
    _verify_preparation_authority(
        root,
        selection=selection,
        seal=seal,
        source=source,
        feature_children=feature_payloads,
        parent_authority=parent_authority,
    )
    for payload in feature_payloads.values():
        if payload["selection_manifest_id"] != seal.selection_manifest_id:
            raise ValueError("holdout features are bound to a different selection")
    feature_by_configuration = dict(seal.configuration_feature_dataset_ids)
    expected_fits: set[str] = set()
    expected_forecasts: set[str] = set()
    expected_coverage: set[str] = set()
    fit_payloads: list[Mapping[str, object]] = []
    forecast_payloads: list[Mapping[str, object]] = []
    coverage_payloads: list[Mapping[str, object]] = []
    for fit_id in seal.final_fit_ids:
        payload = _verify_child(
            root,
            f"fits/{fit_id}.json",
            contract="qtrad-r2-final-fit-v1",
            identity_key="fit_id",
            expected_fields=_FINAL_FIT_FIELDS,
            expected_id=fit_id,
            _payload_cache=_payload_cache,
        )
        expected_fits.add(str(payload["fit_id"]))
        fit_payloads.append(payload)
        configuration_id = str(payload["configuration_id"])
        if payload["selection_manifest_id"] != seal.selection_manifest_id or payload[
            "feature_dataset_id"
        ] != feature_by_configuration.get(configuration_id):
            raise ValueError("final fit lineage differs from its seal")
    if fit_payloads:
        if training_feature_datasets is None:
            raise ValueError(
                "final-fit verification requires authenticated training feature authority"
            )
        if any(
            not isinstance(dataset, R2FeatureDataset)
            for dataset in training_feature_datasets.values()
        ):
            raise TypeError("training feature authority must contain R2FeatureDataset values")
        training_features = {
            dataset.dataset_id: dataset for dataset in training_feature_datasets.values()
        }
        expected_training_ids = {
            _training_child_ids_from_fit(payload)[0] for payload in fit_payloads
        }
        if set(training_features) != expected_training_ids:
            raise ValueError("training feature authority does not match final-fit lineage")
        frozen_target = source.pre_holdout_target_dataset
        for payload in fit_payloads:
            feature_id, target_id = _training_child_ids_from_fit(payload)
            if target_id != frozen_target.dataset_id:
                raise ValueError("final fit target differs from the frozen pre-holdout target")
            _replay_final_fit(
                selection,
                payload,
                training_feature_dataset=training_features[feature_id],
                training_target_dataset=frozen_target,
                training_target_source_dataset_id=source.source_target_dataset_id,
            )
    for dataset_id in seal.forecast_dataset_ids:
        payload = _verify_child(
            root,
            f"forecasts/{dataset_id}.json",
            contract=R2_HOLDOUT_FORECAST_CONTRACT,
            identity_key="dataset_id",
            expected_fields=_FORECAST_FIELDS,
            expected_id=dataset_id,
            _payload_cache=_payload_cache,
        )
        expected_forecasts.add(str(payload["dataset_id"]))
        forecast_payloads.append(payload)
        configuration_id = str(payload["configuration_id"])
        if payload["selection_manifest_id"] != seal.selection_manifest_id or payload[
            "feature_dataset_id"
        ] != feature_by_configuration.get(configuration_id):
            raise ValueError("holdout forecast lineage differs from its seal")
    for coverage_id in seal.coverage_ids:
        payload = _verify_child(
            root,
            f"coverage/{coverage_id}.json",
            contract=R2_HOLDOUT_COVERAGE_CONTRACT,
            identity_key="coverage_id",
            expected_fields=_COVERAGE_FIELDS,
            expected_id=coverage_id,
            _payload_cache=_payload_cache,
        )
        expected_coverage.add(str(payload["coverage_id"]))
        coverage_payloads.append(payload)
        configuration_id = str(payload["configuration_id"])
        if payload["selection_manifest_id"] != seal.selection_manifest_id or payload[
            "feature_dataset_id"
        ] != feature_by_configuration.get(configuration_id):
            raise ValueError("holdout coverage lineage differs from its seal")
    _verify_seal_registry(
        selection,
        seal,
        feature_payloads,
        fit_payloads,
        forecast_payloads,
        coverage_payloads,
    )
    _replay_holdout_outputs(
        selection,
        seal,
        feature_payloads,
        fit_payloads,
        forecast_payloads,
        coverage_payloads,
        opportunities,
    )
    if expected_fits != set(seal.final_fit_ids):
        raise ValueError("holdout final-fit children do not reconcile to the seal")
    if expected_forecasts != set(seal.forecast_dataset_ids):
        raise ValueError("holdout forecast children do not reconcile to the seal")
    if expected_coverage != set(seal.coverage_ids):
        raise ValueError("holdout coverage children do not reconcile to the seal")


def write_holdout_preparation(
    output: Path,
    *,
    selection: R2HoldoutSelectionManifest,
    holdout_target_source: R2HoldoutTargetSource,
    feature_dataset: object | None = None,
    feature_datasets: Mapping[str, object] | None = None,
    final_fits: Mapping[str, object],
    forecasts: Mapping[str, object],
    coverage: Mapping[str, object],
    seal: R2HoldoutForecastSeal,
    training_feature_datasets: Mapping[str, object] | None = None,
    training_target_datasets: Mapping[str, object] | None = None,
    immediate_parent_authority: Mapping[str, object] | None = None,
    _confirmatory_token: object | None = None,
    _staging: bool = False,
) -> Path:
    """Persist all PR B children transactionally without overwriting any path."""
    if not _staging:
        existing_empty_dir = (
            output.is_dir() and not output.is_symlink() and not any(output.iterdir())
        )
        if (output.exists() or output.is_symlink()) and not existing_empty_dir:
            raise FileExistsError("holdout preparation output already exists")
        output.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=output.parent, prefix=f".{output.name}.") as staged_name:
            write_holdout_preparation(
                Path(staged_name),
                selection=selection,
                holdout_target_source=holdout_target_source,
                feature_dataset=feature_dataset,
                feature_datasets=feature_datasets,
                final_fits=final_fits,
                forecasts=forecasts,
                coverage=coverage,
                seal=seal,
                training_feature_datasets=training_feature_datasets,
                training_target_datasets=training_target_datasets,
                immediate_parent_authority=immediate_parent_authority,
                _confirmatory_token=_confirmatory_token,
                _staging=True,
            )
            if (output.exists() and not existing_empty_dir) or output.is_symlink():
                raise FileExistsError("holdout preparation output appeared during staging")
            try:
                if existing_empty_dir:
                    output.rmdir()
                os.replace(staged_name, output)
            except BaseException:
                if existing_empty_dir and not output.exists():
                    output.mkdir()
                raise
        return output / "manifest.json"
    """Persist all PR B children and the seal without overwriting any path."""
    if (
        seal.holdout_scope is HoldoutScope.CONFIRMATORY
        and _confirmatory_token is not _CONFIRMATORY_G2_PREPARATION_TOKEN
    ):
        raise ValueError("G2 preparation is restricted to disposable fixtures")
    if seal.holdout_scope is HoldoutScope.CONFIRMATORY and not immediate_parent_authority:
        raise ValueError("confirmatory G2 preparation requires immediate-parent authority")
    if selection.manifest_id != seal.selection_manifest_id:
        raise ValueError("selection and seal lineage differs")
    feature_children: dict[str, R2HoldoutFeatureDataset] = {}
    if isinstance(feature_dataset, R2HoldoutFeatureDataset):
        feature_children[feature_dataset.dataset_id] = feature_dataset
    elif feature_dataset is not None:
        raise TypeError("feature_dataset must be an R2HoldoutFeatureDataset")
    for child in (feature_datasets or {}).values():
        if not isinstance(child, R2HoldoutFeatureDataset):
            raise TypeError("feature_datasets must contain R2HoldoutFeatureDataset values")
        feature_children[child.dataset_id] = child
    expected_feature_ids = {
        dataset_id
        for _configuration_id, dataset_id in seal.configuration_feature_dataset_ids
        if dataset_id is not None
    }
    if set(feature_children) != expected_feature_ids:
        raise ValueError("feature arguments do not exactly match the seal lineage")
    if set(final_fits) != set(seal.final_fit_ids):
        raise ValueError("final-fit arguments do not exactly match the seal")
    if set(forecasts) != set(seal.forecast_dataset_ids):
        raise ValueError("forecast arguments do not exactly match the seal")
    if set(coverage) != set(seal.coverage_ids):
        raise ValueError("coverage arguments do not exactly match the seal")
    fit_payloads = tuple(_as_json(final_fits[fit_id]) for fit_id in seal.final_fit_ids)
    expected_source = selection.evaluation_policy.get("target_dataset_id")
    if not isinstance(expected_source, str):
        raise ValueError("selection is missing the authenticated target source")
    expected_pre_holdout = selection.evaluation_policy.get("pre_holdout_target_dataset_id")
    if not isinstance(expected_pre_holdout, str):
        raise ValueError("selection is missing the authenticated pre-holdout target")
    if not isinstance(holdout_target_source, R2HoldoutTargetSource):
        raise TypeError("holdout preparation requires authenticated source authority")
    source = holdout_target_source
    if source.pre_holdout_target_dataset.dataset_id != expected_pre_holdout:
        raise ValueError("source evidence differs from the frozen pre-holdout target")
    _opportunities_from_selection(selection, source)
    training_feature_ids = {_training_child_ids_from_fit(payload)[0] for payload in fit_payloads}
    training_target_ids = {_training_child_ids_from_fit(payload)[1] for payload in fit_payloads}
    supplied_training_features = {
        child.dataset_id: child
        for child in (training_feature_datasets or {}).values()
        if isinstance(child, R2FeatureDataset)
    }
    supplied_training_targets = {
        child.dataset_id: child
        for child in (training_target_datasets or {}).values()
        if isinstance(child, TargetDataset)
    }
    if any(
        not isinstance(child, R2FeatureDataset)
        for child in (training_feature_datasets or {}).values()
    ):
        raise TypeError("training_feature_datasets must contain R2FeatureDataset values")
    if any(
        not isinstance(child, TargetDataset) for child in (training_target_datasets or {}).values()
    ):
        raise TypeError("training_target_datasets must contain TargetDataset values")
    if set(supplied_training_features) != training_feature_ids:
        raise ValueError("training feature authority does not match final-fit lineage")
    if set(supplied_training_targets) != training_target_ids:
        raise ValueError("training target authority does not match final-fit lineage")
    if any(
        dataset.dataset_id != expected_pre_holdout for dataset in supplied_training_targets.values()
    ):
        raise ValueError("training target authority does not match the frozen pre-holdout target")
    _write_json(
        output / _PREPARATION_CLAIM_FILE,
        _preparation_claim(
            selection.manifest_id,
            seal.seal_id,
            state="OWNED_UNOPENED",
        ),
    )
    selection_path = output / "selection.json"
    if selection_path.exists() or selection_path.is_symlink():
        existing = verify_holdout_selection(selection_path)
        if existing.manifest_id != selection.manifest_id:
            raise ValueError("existing selection child differs from preparation selection")
    else:
        _write_json(selection_path, selection.as_json())
    _write_json(
        output / _PREPARATION_AUTHORITY_FILE,
        _preparation_authority_payload(
            selection,
            seal,
            source,
            feature_children,
            parent_authority=immediate_parent_authority,
        ),
    )
    for dataset_id, relative in _feature_dataset_paths(seal):
        payload = _as_json(feature_children[dataset_id])
        compact, _part_paths = _write_partitioned_child(
            output,
            relative,
            payload,
            identity_field="dataset_id",
            row_field="rows",
        )
        _write_json(output / relative, compact)
    for fit_id in seal.final_fit_ids:
        payload = _as_json(final_fits[fit_id])
        partition_fields = _final_fit_partition_fields(payload)
        compact, _part_paths = _write_partitioned_child(
            output,
            f"fits/{fit_id}.json",
            payload,
            identity_field="fit_id",
            row_field="fit_arrays",
            array_fields=partition_fields,
            mapping_fields=("diagnostics",),
        )
        _write_json(output / f"fits/{fit_id}.json", compact)
    for dataset_id in seal.forecast_dataset_ids:
        payload = _as_json(forecasts[dataset_id])
        compact, _part_paths = _write_partitioned_child(
            output,
            f"forecasts/{dataset_id}.json",
            payload,
            identity_field="dataset_id",
            row_field="rows",
        )
        _write_json(output / f"forecasts/{dataset_id}.json", compact)
    for coverage_id in seal.coverage_ids:
        payload = _as_json(coverage[coverage_id])
        compact, _part_paths = _write_partitioned_child(
            output,
            f"coverage/{coverage_id}.json",
            payload,
            identity_field="coverage_id",
            row_field="rows",
        )
        _write_json(output / f"coverage/{coverage_id}.json", compact)
    _write_json(output / "manifest.json", seal.as_json())
    return output / "manifest.json"


def verify_holdout_preparation(
    path: Path,
    *,
    _confirmatory_token: object | None = None,
    holdout_target_source: R2HoldoutTargetSource,
    training_feature_datasets: Mapping[str, R2FeatureDataset] | None = None,
    immediate_parent_authority: Mapping[str, object] | None = None,
    _payload_cache: _PayloadCache | None = None,
    _allow_incomplete_transfer: bool = False,
    _expected_destination_root_id: str | None = None,
) -> R2HoldoutForecastSeal:
    seal_payload = _verify_child(
        path,
        "manifest.json",
        contract=R2_HOLDOUT_FORECAST_SEAL_CONTRACT,
        identity_key="seal_id",
        expected_fields=_SEAL_FIELDS,
        _payload_cache=_payload_cache,
    )
    seal = R2HoldoutForecastSeal.from_json(seal_payload)
    selection = verify_holdout_selection(path / "selection.json")
    if selection.holdout_scope is HoldoutScope.CONFIRMATORY and _confirmatory_token not in {
        _CONFIRMATORY_G2_PREPARATION_TOKEN,
        _CONFIRMATORY_G2_LIFECYCLE_TOKEN,
    }:
        raise ValueError(
            "confirmatory holdout preparation requires the unsupported source-child workflow"
        )
    if selection.holdout_scope is HoldoutScope.CONFIRMATORY and not immediate_parent_authority:
        raise ValueError("confirmatory preparation requires immediate-parent authority")
    claim_payload = _verify_child(
        path,
        _PREPARATION_CLAIM_FILE,
        contract=_PREPARATION_CLAIM_CONTRACT,
        identity_key="claim_id",
        expected_fields=_PREPARATION_CLAIM_FIELDS,
    )
    if (
        claim_payload.get("selection_manifest_id") != selection.manifest_id
        or claim_payload.get("seal_id") != seal.seal_id
    ):
        raise ValueError("preparation claim does not bind the exact seal")
    claim_state = claim_payload.get("state")
    if claim_state not in {"AVAILABLE", "TRANSFERRED", "OWNED_UNOPENED", "OWNED_OPENED"}:
        raise ValueError("preparation claim has an unsupported state")
    if selection.holdout_scope is HoldoutScope.CONFIRMATORY:
        if _confirmatory_token is _CONFIRMATORY_G2_PREPARATION_TOKEN:
            if claim_state != "OWNED_UNOPENED":
                raise ValueError("confirmatory G2 preparation must remain owned and unopened")
            if any(
                (path / name).exists() or (path / name).is_symlink()
                for name in (
                    "opened.json",
                    "confirmatory-opened.json",
                    "consumed.json",
                    "outcome-evidence.json",
                    "outcome-target.json",
                    "evaluation.json",
                )
            ):
                raise ValueError(
                    "confirmatory G2 preparation contains post-open lifecycle evidence"
                )
        elif claim_state not in {"OWNED_UNOPENED", "OWNED_OPENED"}:
            raise ValueError("confirmatory lifecycle requires an owned preparation")
    if claim_state == "AVAILABLE":
        expected_claim = _preparation_claim(selection.manifest_id, seal.seal_id)
        if claim_payload != expected_claim:
            raise ValueError("available preparation claim is not authenticated")
    else:
        transfer_id = claim_payload.get("transfer_id")
        source_claim_id = claim_payload.get("source_claim_id")
        if transfer_id is None and source_claim_id is None:
            expected_claim = _preparation_claim(
                selection.manifest_id,
                seal.seal_id,
                state=str(claim_state),
            )
            if claim_payload != expected_claim:
                raise ValueError("owned preparation claim is not authenticated")
        elif not isinstance(transfer_id, str) or not isinstance(source_claim_id, str):
            raise ValueError("transferred preparation claim lacks ownership lineage")
        else:
            expected_claim = _preparation_claim(
                selection.manifest_id,
                seal.seal_id,
                state=str(claim_state),
                transfer_id=transfer_id,
                source_claim_id=source_claim_id,
            )
            if claim_payload != expected_claim:
                raise ValueError("preparation claim ownership lineage is not authenticated")
    allowed = {
        "manifest.json",
        "selection.json",
        _PREPARATION_CLAIM_FILE,
        _PREPARATION_AUTHORITY_FILE,
    }
    intent_path = path / _PREPARATION_TRANSFER_INTENT_FILE
    if intent_path.is_file():
        if intent_path.is_symlink():
            raise ValueError("transfer intent must be a regular file")
        intent_payload = _verify_child(
            path,
            _PREPARATION_TRANSFER_INTENT_FILE,
            contract=_PREPARATION_TRANSFER_INTENT_CONTRACT,
            identity_key="intent_id",
            expected_fields=_PREPARATION_TRANSFER_INTENT_FIELDS,
        )
        if claim_state not in {"AVAILABLE", "OWNED_UNOPENED", "TRANSFERRED"}:
            raise ValueError("transfer intent cannot accompany an owned preparation")
        if claim_state in {"AVAILABLE", "OWNED_UNOPENED"}:
            source_claim_value = claim_payload.get("claim_id")
            if not isinstance(source_claim_value, str) or not source_claim_value:
                raise ValueError("transfer intent source claim is malformed")
            intent_source_claim_id = source_claim_value
            intent_transfer_id = _preparation_transfer_id(
                selection.manifest_id,
                seal.seal_id,
                intent_source_claim_id,
            )
        else:
            source_claim_value = claim_payload.get("source_claim_id")
            transfer_value = claim_payload.get("transfer_id")
            if (
                not isinstance(source_claim_value, str)
                or not source_claim_value
                or not isinstance(transfer_value, str)
                or not transfer_value
            ):
                raise ValueError("transfer intent source claim is malformed")
            intent_source_claim_id = source_claim_value
            intent_transfer_id = transfer_value
        intent_destination_root_id = intent_payload.get("destination_root_id")
        if (
            not isinstance(intent_destination_root_id, str)
            or len(intent_destination_root_id) != 64
            or any(character not in "0123456789abcdef" for character in intent_destination_root_id)
        ):
            raise ValueError("transfer intent destination identity is malformed")
        expected_intent = _preparation_transfer_intent(
            selection.manifest_id,
            seal.seal_id,
            intent_source_claim_id,
            intent_transfer_id,
            intent_destination_root_id,
        )
        if intent_payload != expected_intent:
            raise ValueError("transfer intent does not authenticate its source claim")
        allowed.add(_PREPARATION_TRANSFER_INTENT_FILE)
    elif intent_path.exists() or intent_path.is_symlink():
        raise ValueError("transfer intent must be a regular file")
    usage_path = path / _PREPARATION_USAGE_FILE
    for _dataset_id, relative in _feature_dataset_paths(seal):
        allowed.update(_physical_child_paths(path, relative, identity_field="dataset_id"))
    has_transfer_lineage = isinstance(claim_payload.get("transfer_id"), str) and isinstance(
        claim_payload.get("source_claim_id"), str
    )
    if usage_path.is_file():
        usage_payload = _verify_child(
            path,
            _PREPARATION_USAGE_FILE,
            contract=_PREPARATION_USAGE_CONTRACT,
            identity_key="usage_id",
            expected_fields=_PREPARATION_USAGE_FIELDS,
        )
        if not has_transfer_lineage:
            raise ValueError("untransferred preparation cannot carry a usage claim")
        destination_root_id = usage_payload.get("destination_root_id")
        if (
            not isinstance(destination_root_id, str)
            or len(destination_root_id) != 64
            or any(character not in "0123456789abcdef" for character in destination_root_id)
        ):
            raise ValueError("preparation usage destination identity is malformed")
        expected_usage = _preparation_usage(
            selection.manifest_id,
            seal.seal_id,
            str(claim_payload["source_claim_id"]),
            str(usage_payload["destination_claim_id"]),
            str(claim_payload["transfer_id"]),
            destination_root_id,
        )
        expected_destination = _preparation_claim(
            selection.manifest_id,
            seal.seal_id,
            state="OWNED_UNOPENED",
            transfer_id=str(claim_payload["transfer_id"]),
            source_claim_id=str(claim_payload["source_claim_id"]),
        )
        if (
            usage_payload != expected_usage
            or usage_payload["destination_claim_id"] != expected_destination["claim_id"]
        ):
            raise ValueError("preparation usage does not bind the transferred owner")
        if claim_state != "TRANSFERRED":
            expected_root_id = _expected_destination_root_id
            if expected_root_id is None:
                expected_root_id = sha256(str(path.resolve()).encode()).hexdigest()
            if destination_root_id != expected_root_id:
                raise ValueError(
                    "preparation usage destination identity differs from its actual root"
                )
        allowed.add(_PREPARATION_USAGE_FILE)
    elif usage_path.exists() or usage_path.is_symlink():
        raise ValueError("preparation usage must be a regular file")
    elif has_transfer_lineage and not (_allow_incomplete_transfer and claim_state == "TRANSFERRED"):
        raise ValueError("transferred preparation requires a usage claim")
    for fit_id in seal.final_fit_ids:
        allowed.update(
            _physical_child_paths(
                path,
                f"fits/{fit_id}.json",
                identity_field="fit_id",
            )
        )
    for dataset_id in seal.forecast_dataset_ids:
        allowed.update(
            _physical_child_paths(
                path,
                f"forecasts/{dataset_id}.json",
                identity_field="dataset_id",
            )
        )
    for coverage_id in seal.coverage_ids:
        allowed.update(
            _physical_child_paths(
                path,
                f"coverage/{coverage_id}.json",
                identity_field="coverage_id",
            )
        )
    lifecycle_identity_fields = {
        "outcome-evidence.json": "outcome_evidence_id",
        "outcome-target.json": "dataset_id",
    }
    for lifecycle_name in (
        "opened.json",
        "confirmatory-opened.json",
        "consumed.json",
        "outcome-evidence.json",
        "outcome-target.json",
        "evaluation.json",
    ):
        if (path / lifecycle_name).is_file():
            identity_field = lifecycle_identity_fields.get(lifecycle_name)
            if identity_field is None:
                allowed.add(lifecycle_name)
            else:
                allowed.update(
                    _physical_child_paths(path, lifecycle_name, identity_field=identity_field)
                )
    claim_next_path = path / _PREPARATION_CLAIM_NEXT_FILE
    if claim_next_path.is_symlink():
        raise ValueError("claim transition temporary must be a regular file")
    if claim_next_path.is_file():
        if claim_state not in {"AVAILABLE", "OWNED_UNOPENED"}:
            raise ValueError(
                "claim transition temporary is only valid for an untransferred source "
                "or an unopened owned preparation"
            )
        next_payload = _verify_child(
            path,
            _PREPARATION_CLAIM_NEXT_FILE,
            contract=_PREPARATION_CLAIM_CONTRACT,
            identity_key="claim_id",
            expected_fields=_PREPARATION_CLAIM_FIELDS,
        )
        claim_id_value = claim_payload.get("claim_id")
        if not isinstance(claim_id_value, str) or not claim_id_value:
            raise ValueError("claim transition source claim is malformed")
        if claim_state == "OWNED_UNOPENED" and has_transfer_lineage:
            transfer_value = claim_payload.get("transfer_id")
            source_claim_value = claim_payload.get("source_claim_id")
            if (
                not isinstance(transfer_value, str)
                or not transfer_value
                or not isinstance(source_claim_value, str)
                or not source_claim_value
            ):
                raise ValueError("claim transition owned lineage is malformed")
            expected_next = _preparation_claim(
                selection.manifest_id,
                seal.seal_id,
                state="OWNED_OPENED",
                transfer_id=transfer_value,
                source_claim_id=source_claim_value,
            )
        else:
            expected_next = _preparation_claim(
                selection.manifest_id,
                seal.seal_id,
                state="TRANSFERRED",
                transfer_id=_preparation_transfer_id(
                    selection.manifest_id,
                    seal.seal_id,
                    claim_id_value,
                ),
                source_claim_id=claim_id_value,
            )
        if next_payload != expected_next:
            raise ValueError("claim transition temporary is not authenticated")
        allowed.add(_PREPARATION_CLAIM_NEXT_FILE)
    elif claim_next_path.exists():
        raise ValueError("claim transition temporary must be a regular file")
    _reject_orphans(path, allowed)
    _verify_prepare_children(
        path,
        seal,
        selection,
        holdout_target_source,
        training_feature_datasets=training_feature_datasets,
        parent_authority=immediate_parent_authority,
        _payload_cache=_payload_cache,
    )
    return seal


def prepare_holdout_from_files(
    source: Path,
    output: Path,
    *,
    holdout_target_source: R2HoldoutTargetSource,
    training_feature_datasets: Mapping[str, R2FeatureDataset] | None = None,
    immediate_parent_authority: Mapping[str, object] | None = None,
    expected_selection_manifest_id: str | None = None,
) -> R2HoldoutForecastSeal:
    """Transfer one disposable preparation with a discoverable crash-safe handoff."""
    source_seal = verify_holdout_preparation(
        source,
        holdout_target_source=holdout_target_source,
        training_feature_datasets=training_feature_datasets,
        immediate_parent_authority=immediate_parent_authority,
        _allow_incomplete_transfer=True,
    )
    source_selection = verify_holdout_selection(source / "selection.json")
    if (
        expected_selection_manifest_id is not None
        and source_selection.manifest_id != expected_selection_manifest_id
    ):
        raise ValueError("source preparation selection differs from the expected selection")
    if source_selection.manifest_id != source_seal.selection_manifest_id:
        raise ValueError("source preparation selection differs from its seal")
    for lifecycle_name in (
        "opened.json",
        "consumed.json",
        "outcome-evidence.json",
        "outcome-target.json",
        "evaluation.json",
    ):
        lifecycle_path = source / lifecycle_name
        if lifecycle_path.exists() or lifecycle_path.is_symlink():
            raise ValueError("cannot prepare from a holdout with lifecycle evidence")
    output.parent.mkdir(parents=True, exist_ok=True)
    claim_path = source / _PREPARATION_CLAIM_FILE
    existing_claim = _load_object(claim_path)
    available = _preparation_claim(
        source_selection.manifest_id,
        source_seal.seal_id,
        state="AVAILABLE",
    )
    initial_unopened = _preparation_claim(
        source_selection.manifest_id,
        source_seal.seal_id,
        state="OWNED_UNOPENED",
    )
    source_claim_id: str
    if existing_claim in (available, initial_unopened):
        source_claim_id = str(existing_claim["claim_id"])
    else:
        transferred_claim_id = existing_claim.get("claim_id")
        if existing_claim.get("state") != "TRANSFERRED":
            raise FileExistsError("source preparation has already been transferred")
        source_claim_id_value = existing_claim.get("source_claim_id")
        if not isinstance(source_claim_id_value, str) or not source_claim_id_value:
            raise ValueError("transferred source preparation lacks its original claim")
        source_claim_id = source_claim_id_value
        expected_transfer_id = _preparation_transfer_id(
            source_selection.manifest_id,
            source_seal.seal_id,
            source_claim_id,
        )
        expected_transferred = _preparation_claim(
            source_selection.manifest_id,
            source_seal.seal_id,
            state="TRANSFERRED",
            transfer_id=expected_transfer_id,
            source_claim_id=source_claim_id,
        )
        if (
            existing_claim != expected_transferred
            or transferred_claim_id != expected_transferred["claim_id"]
        ):
            raise ValueError("transferred source preparation claim is not authenticated")
    transfer_id = _preparation_transfer_id(
        source_selection.manifest_id,
        source_seal.seal_id,
        source_claim_id,
    )
    transferred = _preparation_claim(
        source_selection.manifest_id,
        source_seal.seal_id,
        state="TRANSFERRED",
        transfer_id=transfer_id,
        source_claim_id=source_claim_id,
    )
    destination_claim = _preparation_claim(
        source_selection.manifest_id,
        source_seal.seal_id,
        state="OWNED_UNOPENED",
        transfer_id=transfer_id,
        source_claim_id=source_claim_id,
    )
    destination_root_id = sha256(str(output.resolve()).encode()).hexdigest()
    usage = _preparation_usage(
        source_selection.manifest_id,
        source_seal.seal_id,
        source_claim_id,
        str(destination_claim["claim_id"]),
        transfer_id,
        destination_root_id,
    )
    source_usage_path = source / _PREPARATION_USAGE_FILE
    transfer_intent = _preparation_transfer_intent(
        source_selection.manifest_id,
        source_seal.seal_id,
        source_claim_id,
        transfer_id,
        destination_root_id,
    )
    intent_path = source / _PREPARATION_TRANSFER_INTENT_FILE
    has_valid_transfer_intent = False
    if intent_path.exists() or intent_path.is_symlink():
        if intent_path.is_symlink() or not intent_path.is_file():
            raise FileExistsError(
                "source preparation has already been transferred to another destination"
            )
        if _load_object(intent_path) != transfer_intent:
            raise FileExistsError(
                "source preparation has already been transferred to another destination"
            )
        has_valid_transfer_intent = True
    elif existing_claim == transferred:
        raise ValueError("transferred source preparation lacks an authenticated transfer intent")
    if (
        existing_claim == transferred
        and (source_usage_path.exists() or source_usage_path.is_symlink())
        and (
            source_usage_path.is_symlink()
            or not source_usage_path.is_file()
            or _load_object(source_usage_path).get("destination_root_id") != destination_root_id
        )
    ):
        raise FileExistsError(
            "source preparation has already been transferred to another destination"
        )
    if output.exists() or output.is_symlink():
        if existing_claim != transferred:
            raise FileExistsError("holdout preparation output already exists")
        if output.is_symlink() or not output.is_dir():
            raise ValueError("transferred holdout output is not a regular directory")
        verified = verify_holdout_preparation(
            output,
            holdout_target_source=holdout_target_source,
            training_feature_datasets=training_feature_datasets,
            immediate_parent_authority=immediate_parent_authority,
        )
        if not source_usage_path.exists() and not source_usage_path.is_symlink():
            if not has_valid_transfer_intent:
                raise ValueError(
                    "completed transfer output lacks an authenticated source usage claim"
                )
            _write_json(source_usage_path, usage)
        return verified
    staging = output.with_name(f".{output.name}.transfer-{transfer_id}")
    if staging.is_symlink() or (staging.exists() and not staging.is_dir()):
        raise ValueError("holdout transfer staging path is not a regular directory")
    paths = [
        "selection.json",
        "authority.json",
        "manifest.json",
    ]
    for _dataset_id, relative in _feature_dataset_paths(source_seal):
        paths.extend(_physical_child_paths(source, relative, identity_field="dataset_id"))
    for fit_id in source_seal.final_fit_ids:
        paths.extend(_physical_child_paths(source, f"fits/{fit_id}.json", identity_field="fit_id"))
    for dataset_id in source_seal.forecast_dataset_ids:
        paths.extend(
            _physical_child_paths(
                source,
                f"forecasts/{dataset_id}.json",
                identity_field="dataset_id",
            )
        )
    for coverage_id in source_seal.coverage_ids:
        paths.extend(
            _physical_child_paths(
                source,
                f"coverage/{coverage_id}.json",
                identity_field="coverage_id",
            )
        )
    source_usage_path = source / _PREPARATION_USAGE_FILE

    def ensure_json(path: Path, payload: Mapping[str, object]) -> None:
        if path.exists() or path.is_symlink():
            if path.is_symlink() or not path.is_file() or _load_object(path) != dict(payload):
                raise ValueError(
                    f"transfer destination differs from its authenticated source: {path}"
                )
        else:
            _write_json(path, payload)

    def ensure_copy(relative: str) -> None:
        source_path = source / relative
        target_path = staging / relative
        if source_path.is_symlink() or not source_path.is_file():
            raise ValueError(f"holdout transfer source must be a regular file: {source_path}")
        encoded = source_path.read_bytes()
        if target_path.exists() or target_path.is_symlink():
            if (
                target_path.is_symlink()
                or not target_path.is_file()
                or target_path.read_bytes() != encoded
            ):
                raise ValueError(
                    f"transfer destination differs from its authenticated source: {target_path}"
                )
        else:
            atomic_create(target_path, encoded)

    # The staging root is intentionally persistent and non-authoritative.  A crash
    # before or after the source claim transition leaves this exact path resumable.
    staging.mkdir(parents=True, exist_ok=True)
    ensure_json(staging / _PREPARATION_CLAIM_FILE, destination_claim)
    for relative in paths:
        ensure_copy(relative)
    ensure_json(staging / _PREPARATION_USAGE_FILE, usage)
    verify_holdout_preparation(
        staging,
        holdout_target_source=holdout_target_source,
        training_feature_datasets=training_feature_datasets,
        immediate_parent_authority=immediate_parent_authority,
        _expected_destination_root_id=destination_root_id,
    )
    if intent_path.exists() or intent_path.is_symlink():
        if intent_path.is_symlink() or not intent_path.is_file():
            raise ValueError("transfer intent must be a regular file")
        if _load_object(intent_path) != transfer_intent:
            raise ValueError("transfer intent differs from its authenticated transfer")
    else:
        _write_json(intent_path, transfer_intent)

    # Source authority is consumed only after the complete staged destination is
    # authenticated.  The persistent staging root makes every later crash
    # recoverable without rolling back an irreversible claim.
    if existing_claim != transferred:
        _replace_json(claim_path, transferred)
    if source_usage_path.exists() or source_usage_path.is_symlink():
        if (
            source_usage_path.is_symlink()
            or not source_usage_path.is_file()
            or _load_object(source_usage_path) != usage
        ):
            raise ValueError(
                "source preparation usage claim differs from the authenticated transfer"
            )
    else:
        _write_json(source_usage_path, usage)
    if _load_object(claim_path) != transferred:
        raise ValueError("source preparation transfer claim was not durably published")
    if output.exists() or output.is_symlink():
        raise FileExistsError("holdout preparation output appeared during transfer")
    # os.replace is the final authority publication.  If the process dies or the
    # call fails, the source is TRANSFERRED and the staging root remains discoverable.
    os.replace(staging, output)
    return verify_holdout_preparation(
        output,
        holdout_target_source=holdout_target_source,
        training_feature_datasets=training_feature_datasets,
        immediate_parent_authority=immediate_parent_authority,
    )


def _reject_orphans(root: Path, allowed: set[str]) -> None:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("holdout bundle root must be a regular directory")
    actual: set[str] = set()
    for candidate in root.rglob("*"):
        relative = candidate.relative_to(root).as_posix()
        if candidate.is_symlink():
            raise ValueError(f"holdout bundle contains a symlink: {candidate}")
        if candidate.is_dir():
            if not any(path.startswith(f"{relative}/") for path in allowed):
                raise ValueError(f"holdout bundle contains an unexpected directory: {relative}")
        elif candidate.is_file():
            actual.add(relative)
        else:
            raise ValueError(f"holdout bundle contains a special entry: {relative}")
    if actual != allowed:
        extra = sorted(actual - allowed)
        missing = sorted(allowed - actual)
        raise ValueError(f"holdout bundle file closure differs; extra={extra}, missing={missing}")


def _outcome_binding_from_feature_payload(
    feature_payload: Mapping[str, object],
    *,
    selection: R2HoldoutSelectionManifest,
    opportunities: Sequence[HoldoutTargetOpportunity],
) -> dict[str, object]:
    raw_range = _object_list(feature_payload["holdout_range"], "feature holdout range")
    if len(raw_range) != 2:
        raise ValueError("feature holdout range must contain exactly two timestamps")
    raw_rows = _object_list(feature_payload["rows"], "feature rows")
    source_row_ids: list[tuple[str, str]] = []
    for raw in raw_rows:
        if not isinstance(raw, Mapping):
            raise ValueError("feature rows must be objects")
        row = cast(Mapping[str, object], raw)
        source_row_ids.append((str(row["target_id"]), str(row["row_id"])))
    source_row_ids.sort()
    target_ids = tuple(target_id for target_id, _ in source_row_ids)
    if len(set(target_ids)) != len(target_ids):
        raise ValueError("feature preparation must contain one row per target")
    if len({row_id for _, row_id in source_row_ids}) != len(source_row_ids):
        raise ValueError("feature preparation source rows must be unique")
    raw_bindings = _object_list(
        feature_payload.get("opportunity_target_ids", []),
        "feature opportunity target bindings",
    )
    bindings: list[tuple[str, str]] = []
    for item in raw_bindings:
        if not isinstance(item, list) or len(item) != 2:
            raise ValueError(
                "feature opportunity target bindings must be [opportunity_id, target_id]"
            )
        bindings.append((str(item[0]), str(item[1])))
    registry = tuple(
        sorted((item.opportunity_id, item.target_id, item.disposition) for item in opportunities)
    )
    expected_bindings = tuple(
        (opportunity_id, target_id) for opportunity_id, target_id, _ in registry
    )
    if tuple(sorted(bindings)) != expected_bindings:
        raise ValueError("feature opportunity target bindings differ from the frozen registry")
    expected_target_ids = tuple(
        sorted(
            target_id
            for _opportunity_id, target_id, disposition in registry
            if disposition.value == "ELIGIBLE"
        )
    )
    row_by_target = {
        str(cast(Mapping[str, object], raw)["target_id"]): str(
            cast(Mapping[str, object], raw)["row_id"]
        )
        for raw in raw_rows
    }
    source_row_ids = [
        (target_id, row_by_target.get(target_id, target_id)) for target_id in expected_target_ids
    ]
    if feature_payload.get("target_dataset_id") is not None:
        source_row_ids = [(target_id, target_id) for target_id in expected_target_ids]
    return {
        "experiment_configuration_id": str(feature_payload["experiment_configuration_id"]),
        "foundation_bundle_id": str(feature_payload["foundation_bundle_id"]),
        "feature_dataset_id": str(feature_payload["dataset_id"]),
        "target_dataset_id": (
            None
            if feature_payload.get("target_dataset_id") is None
            else str(feature_payload["target_dataset_id"])
        ),
        "holdout_range": (
            datetime.fromisoformat(str(raw_range[0])),
            datetime.fromisoformat(str(raw_range[1])),
        ),
        "expected_target_ids": expected_target_ids,
        "source_row_ids": tuple(source_row_ids),
    }


def _outcome_evidence_from_payload(payload: Mapping[str, object]) -> R2HoldoutOutcomeEvidence:
    raw_range = _object_list(payload["holdout_range"], "outcome holdout range")
    if len(raw_range) != 2:
        raise ValueError("outcome holdout range must contain exactly two timestamps")
    raw_expected = _object_list(payload["expected_target_ids"], "outcome expected targets")
    raw_sources = _object_list(payload["source_row_ids"], "outcome source rows")
    source_row_ids: list[tuple[str, str]] = []
    for raw in raw_sources:
        if not isinstance(raw, list) or len(raw) != 2:
            raise ValueError("outcome source rows must be [target_id, row_id]")
        source_row_ids.append((str(raw[0]), str(raw[1])))
    raw_outcomes = _object_list(payload["outcomes"], "holdout outcome evidence outcomes")
    outcomes: list[tuple[str, float]] = []
    for raw in raw_outcomes:
        if not isinstance(raw, list) or len(raw) != 2:
            raise ValueError("holdout outcome evidence entries must be [target_id, outcome]")
        outcomes.append((str(raw[0]), _float_value(raw[1], "holdout outcome")))
    return R2HoldoutOutcomeEvidence(
        selection_manifest_id=str(payload["selection_manifest_id"]),
        seal_id=str(payload["seal_id"]),
        opened_marker_id=str(payload["opened_marker_id"]),
        experiment_configuration_id=str(payload["experiment_configuration_id"]),
        foundation_bundle_id=str(payload["foundation_bundle_id"]),
        feature_dataset_id=str(payload["feature_dataset_id"]),
        target_dataset_id=(
            None if payload.get("target_dataset_id") is None else str(payload["target_dataset_id"])
        ),
        holdout_range=(
            datetime.fromisoformat(str(raw_range[0])),
            datetime.fromisoformat(str(raw_range[1])),
        ),
        expected_target_ids=tuple(str(item) for item in raw_expected),
        source_row_ids=tuple(source_row_ids),
        outcomes=tuple(outcomes),
        source_class=MarketDataSourceClass(str(payload["source_class"])),
        evidence_class=EvidenceClass(str(payload["evidence_class"])),
        holdout_scope=HoldoutScope(str(payload["holdout_scope"])),
        outcome_evidence_id=str(payload["outcome_evidence_id"]),
    )


def _outcome_items(value: object) -> tuple[tuple[str, float], ...]:
    if isinstance(value, Mapping):
        raw_items = tuple(value.items())
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        raw_items = tuple(value)
    else:
        raise TypeError("holdout outcomes must be a mapping or pair sequence")
    items: list[tuple[str, float]] = []
    seen: set[str] = set()
    for raw in raw_items:
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            raise ValueError("holdout outcomes must contain [target_id, outcome] pairs")
        target_id = str(raw[0])
        if target_id in seen:
            raise ValueError("duplicate outcome target IDs are not permitted")
        seen.add(target_id)
        items.append((target_id, _float_value(raw[1], "holdout outcome")))
    return tuple(items)


def _outcome_items_from_source(
    value: object,
    *,
    expected_target_dataset_id: str | None,
    expected_target_ids: Sequence[str],
) -> tuple[tuple[str, float], ...]:
    if isinstance(value, TargetDataset):
        if expected_target_dataset_id is None or value.dataset_id != expected_target_dataset_id:
            raise ValueError("outcomes are not bound to the prepared target dataset")
        rows_by_target = {row.target_id: row for row in value.rows}
        selected: list[tuple[str, float]] = []
        for target_id in expected_target_ids:
            row = rows_by_target.get(target_id)
            if row is None or row.log_return is None:
                raise ValueError("prepared target dataset lacks a realised target")
            selected.append((target_id, row.log_return))
        return _outcome_items(selected)
    if expected_target_dataset_id is not None:
        raise ValueError(
            "prepared target outcomes must be loaded from the authenticated target dataset"
        )
    return _outcome_items(value)


def _validate_target_rows_against_selection(
    selection: R2HoldoutSelectionManifest,
    target_dataset: TargetDataset,
    expected_target_ids: Sequence[str],
    opportunities: Sequence[HoldoutTargetOpportunity],
) -> None:
    registry = {
        item.target_id: (
            item.instrument_id,
            item.decision_time,
            item.target_horizon_seconds,
            item.disposition,
        )
        for item in opportunities
    }
    primary_horizon = selection.evaluation_policy.get("primary_horizon_seconds")
    if not isinstance(primary_horizon, int) or primary_horizon <= 0:
        raise ValueError("selection is missing its primary target horizon")
    rows_by_target: dict[str, TargetRow] = {}
    for row in target_dataset.rows:
        if int(row.horizon.total_seconds()) != primary_horizon:
            continue
        if row.target_id in rows_by_target:
            raise ValueError("target dataset repeats a primary target identity")
        rows_by_target[row.target_id] = row
    eligible_target_ids = {
        target_id
        for target_id, (
            _instrument_id,
            _decision_time,
            _horizon_seconds,
            disposition,
        ) in registry.items()
        if disposition.value == "ELIGIBLE"
    }
    if set(expected_target_ids) != eligible_target_ids:
        raise ValueError("outcome targets differ from the frozen eligible opportunity set")
    for target_id, expected in registry.items():
        row = rows_by_target.get(target_id)
        if row is None or expected is None:
            raise ValueError("outcome target is absent from the frozen opportunity registry")
        if (
            row.target_id != target_id
            or row.instrument_id != expected[0]
            or row.decision_time != expected[1]
            or int(row.horizon.total_seconds()) != expected[2]
        ):
            raise ValueError("outcome target identity differs from the frozen opportunity")


def _opened_from_payload(payload: Mapping[str, object]) -> R2HoldoutOpenedMarker:
    if set(payload) != _OPENED_FIELDS:
        raise ValueError("opened marker has unknown or missing fields")
    if payload.get("contract") != R2_HOLDOUT_OPENED_CONTRACT or payload.get("schema_version") != 1:
        raise ValueError("opened marker contract is unsupported")
    return R2HoldoutOpenedMarker(
        selection_manifest_id=str(payload["selection_manifest_id"]),
        seal_id=str(payload["seal_id"]),
        opened_at=datetime.fromisoformat(str(payload["opened_at"])),
        opened_by=str(payload["opened_by"]),
        acknowledgement=str(payload["acknowledgement"]),
        expected_selection_manifest_id=str(payload["expected_selection_manifest_id"]),
        expected_seal_id=str(payload["expected_seal_id"]),
        state=HoldoutMarkerState(str(payload["state"])),
        marker_id=str(payload["marker_id"]),
    )


def _consumed_from_payload(payload: Mapping[str, object]) -> R2HoldoutConsumedMarker:
    if set(payload) != _CONSUMED_FIELDS:
        raise ValueError("consumed marker has unknown or missing fields")
    if (
        payload.get("contract") != R2_HOLDOUT_CONSUMED_CONTRACT
        or payload.get("schema_version") != 1
    ):
        raise ValueError("consumed marker contract is unsupported")
    return R2HoldoutConsumedMarker(
        selection_manifest_id=str(payload["selection_manifest_id"]),
        seal_id=str(payload["seal_id"]),
        opened_marker_id=str(payload["opened_marker_id"]),
        consumed_at=datetime.fromisoformat(str(payload["consumed_at"])),
        consumed_by=str(payload["consumed_by"]),
        evaluation_id=str(payload["evaluation_id"]),
        outcome_accessed=bool(payload["outcome_accessed"]),
        state=HoldoutMarkerState(str(payload["state"])),
        marker_id=str(payload["marker_id"]),
    )


def _write_opened_marker(
    root: Path,
    *,
    selection_manifest_id: str,
    seal_id: str,
    expected_selection_manifest_id: str,
    expected_seal_id: str,
    opened_by: str,
    opened_at: object,
    acknowledgement: str,
) -> R2HoldoutOpenedMarker:
    if root.joinpath("opened.json").exists() or root.joinpath("opened.json").is_symlink():
        raise FileExistsError("holdout has already been opened")
    if not isinstance(opened_at, datetime):
        raise TypeError("opened_at must be a datetime")
    marker = R2HoldoutOpenedMarker.create(
        selection_manifest_id=selection_manifest_id,
        seal_id=seal_id,
        opened_at=opened_at,
        opened_by=opened_by,
        acknowledgement=acknowledgement,
        expected_selection_manifest_id=expected_selection_manifest_id,
        expected_seal_id=expected_seal_id,
    )
    _write_json(root / "opened.json", marker.as_json())
    return marker


def _validate_consumed_time_source(*, opened_at: object, consumed_at: object) -> None:
    if not isinstance(opened_at, datetime):
        raise TypeError("opened_at must be a datetime")
    require_utc(opened_at, "holdout opened time")
    if callable(consumed_at):
        return
    if not isinstance(consumed_at, datetime):
        raise TypeError("consumed_at must be a datetime or zero-argument callable")
    require_utc(consumed_at, "holdout consumed time")
    if consumed_at < opened_at:
        raise ValueError("holdout consumed time must not precede OPENED")


def _resolve_consumed_at(*, opened_at: datetime, consumed_at: object) -> datetime:
    value = cast(Callable[[], object], consumed_at)() if callable(consumed_at) else consumed_at
    if not isinstance(value, datetime):
        raise TypeError("consumed_at source must return a datetime")
    require_utc(value, "holdout consumed time")
    if value < opened_at:
        raise ValueError("holdout consumed time must not precede OPENED")
    return value


def reveal_holdout(
    root: Path,
    *,
    expected_selection_manifest_id: str,
    expected_seal_id: str,
    acknowledgement: str,
    opened_by: str,
    consumed_by: str,
    opened_at: object,
    consumed_at: object,
    outcome_loader: Callable[
        [],
        TargetDataset | Mapping[str, float] | Sequence[tuple[str, float]],
    ],
    evaluator: Callable[[Mapping[str, float], R2HoldoutOpenedMarker], R2HoldoutEvaluation],
    _confirmatory_token: object | None = None,
    holdout_target_source: R2HoldoutTargetSource,
    training_feature_datasets: Mapping[str, R2FeatureDataset] | None = None,
    immediate_parent_authority: Mapping[str, object] | None = None,
    _payload_cache: _PayloadCache | None = None,
) -> tuple[R2HoldoutEvaluation | None, R2HoldoutConsumedMarker]:
    """Atomically record OPENED, then load/evaluate, and always record CONSUMED.

    The callback is intentionally the first code allowed to receive realised
    outcomes.  Any callback exception is re-raised after the consumed marker is
    durably created.
    """
    payload_cache = _payload_cache if _payload_cache is not None else {}
    seal = verify_holdout_preparation(
        root,
        _confirmatory_token=_confirmatory_token,
        holdout_target_source=holdout_target_source,
        training_feature_datasets=training_feature_datasets,
        immediate_parent_authority=immediate_parent_authority,
        _payload_cache=payload_cache,
    )
    if (
        seal.selection_manifest_id != expected_selection_manifest_id
        or seal.seal_id != expected_seal_id
    ):
        raise ValueError("reveal IDs do not match the exact prepared seal")
    selection_path = root / "selection.json"
    if selection_path.exists():
        selection = verify_holdout_selection(selection_path)
        if selection.manifest_id != expected_selection_manifest_id:
            raise ValueError("selection child differs from expected reveal selection")
    else:
        raise FileNotFoundError("prepared holdout root must contain selection.json")
    target_source = holdout_target_source
    opportunities = _opportunities_from_selection(selection, target_source)
    if (
        selection.holdout_scope is HoldoutScope.CONFIRMATORY
        and _confirmatory_token is not _CONFIRMATORY_G2_LIFECYCLE_TOKEN
    ):
        raise ValueError(
            "confirmatory reveal requires an independently verified target dataset child"
        )
    if root.joinpath("consumed.json").exists() or root.joinpath("consumed.json").is_symlink():
        raise FileExistsError("holdout has already been consumed")
    _validate_consumed_time_source(opened_at=opened_at, consumed_at=consumed_at)
    _preparation_claim_transition(root, selection.manifest_id, seal.seal_id)
    marker = _write_opened_marker(
        root,
        selection_manifest_id=selection.manifest_id,
        seal_id=seal.seal_id,
        expected_selection_manifest_id=expected_selection_manifest_id,
        expected_seal_id=expected_seal_id,
        opened_by=opened_by,
        opened_at=opened_at,
        acknowledgement=acknowledgement,
    )
    evaluation: R2HoldoutEvaluation | None = None
    evaluation_id = _FAILURE_EVALUATION_ID
    error: BaseException | None = None
    try:
        _claim_preparation(
            root,
            selection.manifest_id,
            seal.seal_id,
        )
        feature_payload = _primary_feature_payload(
            _load_feature_payloads(root, seal, _payload_cache=payload_cache)
        )
        binding = _outcome_binding_from_feature_payload(
            feature_payload,
            selection=selection,
            opportunities=opportunities,
        )
        expected_target_ids = cast(tuple[str, ...], binding["expected_target_ids"])
        target_dataset_id = cast(str | None, binding["target_dataset_id"])
        raw_outcomes = outcome_loader()
        if not isinstance(raw_outcomes, TargetDataset):
            raise ValueError(
                "holdout outcomes must be loaded from the authenticated target dataset"
            )
        expected_source = selection.evaluation_policy.get("target_dataset_id")
        if raw_outcomes.dataset_id != expected_source:
            raise ValueError("revealed target dataset differs from the frozen target source")
        target_source.verify_target_dataset(raw_outcomes)
        _validate_target_rows_against_selection(
            selection,
            raw_outcomes,
            expected_target_ids,
            opportunities,
        )
        target_payload = _target_dataset_payload(raw_outcomes)
        compact_target, _target_parts = _write_partitioned_child(
            root,
            "outcome-target.json",
            target_payload,
            identity_field="dataset_id",
            row_field="rows",
        )
        _write_json(root / "outcome-target.json", compact_target)
        outcome_items = _outcome_items_from_source(
            raw_outcomes,
            expected_target_dataset_id=target_dataset_id,
            expected_target_ids=expected_target_ids,
        )
        outcome_mapping = dict(outcome_items)
        if tuple(sorted(target_id for target_id, _ in outcome_items)) != expected_target_ids:
            raise ValueError("holdout outcomes must exactly cover prepared targets")
        outcome_evidence = R2HoldoutOutcomeEvidence.create(
            selection_manifest_id=selection.manifest_id,
            seal_id=seal.seal_id,
            opened_marker_id=marker.marker_id,
            **binding,
            outcomes=outcome_items,
            source_class=selection.source_class,
            evidence_class=selection.evidence_class,
            holdout_scope=selection.holdout_scope,
        )
        evidence_payload = outcome_evidence.as_json()
        compact_evidence, _evidence_parts = _write_partitioned_child(
            root,
            "outcome-evidence.json",
            evidence_payload,
            identity_field="outcome_evidence_id",
            row_field="outcomes",
            array_fields=("expected_target_ids", "source_row_ids", "outcomes"),
        )
        _write_json(root / "outcome-evidence.json", compact_evidence)
        evaluation = evaluator(outcome_mapping, marker)
        if (
            evaluation.selection_manifest_id != selection.manifest_id
            or evaluation.seal_id != seal.seal_id
        ):
            raise ValueError("holdout evaluation lineage differs from the opened seal")
        _write_json(root / "evaluation.json", evaluation.as_json())
        evaluation_id = evaluation.evaluation_id
    except BaseException as exc:
        error = exc
        evaluation_id = _FAILURE_EVALUATION_ID
    finally:
        resolved_consumed_at = _resolve_consumed_at(
            opened_at=marker.opened_at,
            consumed_at=consumed_at,
        )
        consumed = R2HoldoutConsumedMarker.create(
            selection_manifest_id=selection.manifest_id,
            seal_id=seal.seal_id,
            opened_marker_id=marker.marker_id,
            consumed_at=resolved_consumed_at,
            consumed_by=consumed_by,
            evaluation_id=evaluation_id,
        )
        _write_json(root / "consumed.json", consumed.as_json())
    if error is not None:
        raise error
    if evaluation is None:
        raise RuntimeError("holdout evaluation unexpectedly missing")
    return evaluation, consumed


def recover_holdout_consumption(
    root: Path,
    *,
    expected_selection_manifest_id: str,
    expected_seal_id: str,
    consumed_by: str,
    consumed_at: object,
    holdout_target_source: R2HoldoutTargetSource,
    evaluation_id: str = _FAILURE_EVALUATION_ID,
) -> R2HoldoutConsumedMarker:
    """Recover only the missing consumed marker; never reloads or refits."""
    seal = verify_holdout_preparation(
        root,
        holdout_target_source=holdout_target_source,
    )
    if (
        seal.selection_manifest_id != expected_selection_manifest_id
        or seal.seal_id != expected_seal_id
    ):
        raise ValueError("recovery requires the exact original seal IDs")
    _claim_preparation(
        root,
        expected_selection_manifest_id,
        expected_seal_id,
    )
    opened_payload = _verify_child(
        root,
        "opened.json",
        contract=R2_HOLDOUT_OPENED_CONTRACT,
        identity_key="marker_id",
        expected_fields=_OPENED_FIELDS,
    )
    opened = _opened_from_payload(opened_payload)
    if (
        opened.selection_manifest_id != expected_selection_manifest_id
        or opened.seal_id != expected_seal_id
    ):
        raise ValueError("recovery opened marker differs from the exact original seal")
    if root.joinpath("consumed.json").exists() or root.joinpath("consumed.json").is_symlink():
        raise FileExistsError("holdout has already been consumed")
    _validate_consumed_time_source(opened_at=opened.opened_at, consumed_at=consumed_at)
    resolved_consumed_at = _resolve_consumed_at(
        opened_at=opened.opened_at,
        consumed_at=consumed_at,
    )
    consumed = R2HoldoutConsumedMarker.create(
        selection_manifest_id=expected_selection_manifest_id,
        seal_id=expected_seal_id,
        opened_marker_id=opened.marker_id,
        consumed_at=resolved_consumed_at,
        consumed_by=consumed_by,
        evaluation_id=evaluation_id,
    )
    _write_json(root / "consumed.json", consumed.as_json())
    return consumed


def verify_holdout_markers(root: Path) -> tuple[R2HoldoutOpenedMarker, R2HoldoutConsumedMarker]:
    opened_payload = _verify_child(
        root,
        "opened.json",
        contract=R2_HOLDOUT_OPENED_CONTRACT,
        identity_key="marker_id",
        expected_fields=_OPENED_FIELDS,
    )
    consumed_payload = _verify_child(
        root,
        "consumed.json",
        contract=R2_HOLDOUT_CONSUMED_CONTRACT,
        identity_key="marker_id",
        expected_fields=_CONSUMED_FIELDS,
    )
    opened = _opened_from_payload(opened_payload)
    consumed = _consumed_from_payload(consumed_payload)
    if consumed.opened_marker_id != opened.marker_id:
        raise ValueError("consumed marker does not bind the opened marker")
    if consumed.consumed_at < opened.opened_at:
        raise ValueError("holdout consumed time must not precede OPENED")
    if (consumed.selection_manifest_id, consumed.seal_id) != (
        opened.selection_manifest_id,
        opened.seal_id,
    ):
        raise ValueError("holdout marker lineage is inconsistent")
    evaluation_path = root / "evaluation.json"
    if evaluation_path.exists() or evaluation_path.is_symlink():
        evaluation_payload = _verify_child(
            root,
            "evaluation.json",
            contract=R2_HOLDOUT_EVALUATION_CONTRACT,
            identity_key="evaluation_id",
            expected_fields=_EVALUATION_FIELDS,
        )
        if consumed.evaluation_id != str(evaluation_payload["evaluation_id"]):
            raise ValueError("consumed marker does not bind the persisted evaluation")
    elif consumed.evaluation_id != _FAILURE_EVALUATION_ID:
        raise ValueError("consumed marker has no persisted evaluation evidence")
    return opened, consumed


def verify_holdout_evaluation(
    root: Path,
    *,
    _confirmatory_token: object | None = None,
    holdout_target_source: R2HoldoutTargetSource,
    training_feature_datasets: Mapping[str, R2FeatureDataset] | None = None,
    immediate_parent_authority: Mapping[str, object] | None = None,
    _payload_cache: _PayloadCache | None = None,
) -> R2HoldoutEvaluation:
    payload_cache = _payload_cache if _payload_cache is not None else {}
    payload = _verify_child(
        root,
        "evaluation.json",
        contract=R2_HOLDOUT_EVALUATION_CONTRACT,
        identity_key="evaluation_id",
        expected_fields=_EVALUATION_FIELDS,
        _payload_cache=payload_cache,
    )
    selection = verify_holdout_selection(root / "selection.json")
    seal = verify_holdout_preparation(
        root,
        _confirmatory_token=_confirmatory_token,
        holdout_target_source=holdout_target_source,
        training_feature_datasets=training_feature_datasets,
        immediate_parent_authority=immediate_parent_authority,
        _payload_cache=payload_cache,
    )
    opened, consumed = verify_holdout_markers(root)
    outcome_payload = _verify_child(
        root,
        "outcome-evidence.json",
        contract=R2_HOLDOUT_OUTCOME_EVIDENCE_CONTRACT,
        identity_key="outcome_evidence_id",
        expected_fields=_OUTCOME_EVIDENCE_FIELDS,
        _payload_cache=payload_cache,
    )
    outcome_evidence = _outcome_evidence_from_payload(outcome_payload)
    target_payload = _verify_child(
        root,
        "outcome-target.json",
        contract=TARGET_DATASET_CONTRACT,
        identity_key="dataset_id",
        expected_fields=_TARGET_DATASET_FIELDS,
        _payload_cache=payload_cache,
    )
    target_dataset = _target_dataset_from_payload(target_payload, field="outcome target dataset")
    expected_source = selection.evaluation_policy.get("target_dataset_id")
    primary_horizon = selection.evaluation_policy.get("primary_horizon_seconds")
    if target_dataset.dataset_id != expected_source or not isinstance(primary_horizon, int):
        raise ValueError("outcome target dataset is not bound to the frozen target policy")
    target_source = holdout_target_source
    opportunities = _opportunities_from_selection(selection, target_source)
    target_source.verify_target_dataset(target_dataset)
    _validate_target_rows_against_selection(
        selection,
        target_dataset,
        outcome_evidence.expected_target_ids,
        opportunities,
    )
    target_rows_by_id: dict[str, TargetRow] = {}
    for row in target_dataset.rows:
        if int(row.horizon.total_seconds()) != primary_horizon:
            continue
        if row.target_id in target_rows_by_id:
            raise ValueError("outcome target dataset has duplicate primary target IDs")
        target_rows_by_id[row.target_id] = row
    if any(
        target_rows_by_id.get(target_id) is None
        or target_rows_by_id[target_id].log_return != outcome
        for target_id, outcome in outcome_evidence.outcomes
    ):
        raise ValueError("outcome evidence values differ from the retained target dataset")
    if (
        outcome_evidence.selection_manifest_id != selection.manifest_id
        or outcome_evidence.seal_id != seal.seal_id
        or outcome_evidence.opened_marker_id != opened.marker_id
        or outcome_evidence.experiment_configuration_id != selection.experiment_configuration_id
    ):
        raise ValueError("holdout outcome evidence does not bind the exact opened seal")
    feature_payload = _primary_feature_payload(
        _load_feature_payloads(root, seal, _payload_cache=payload_cache)
    )
    binding = _outcome_binding_from_feature_payload(
        feature_payload,
        selection=selection,
        opportunities=opportunities,
    )
    expected_target_dataset_id = selection.evaluation_policy.get("target_dataset_id")
    if (
        expected_target_dataset_id is not None
        and binding["target_dataset_id"] != expected_target_dataset_id
    ):
        raise ValueError("holdout features are not bound to the frozen target dataset")
    if (
        outcome_evidence.experiment_configuration_id != str(binding["experiment_configuration_id"])
        or outcome_evidence.foundation_bundle_id != str(binding["foundation_bundle_id"])
        or outcome_evidence.feature_dataset_id != str(binding["feature_dataset_id"])
        or outcome_evidence.target_dataset_id != binding["target_dataset_id"]
        or outcome_evidence.holdout_range != binding["holdout_range"]
        or outcome_evidence.expected_target_ids
        != cast(tuple[str, ...], binding["expected_target_ids"])
        or outcome_evidence.source_row_ids
        != cast(tuple[tuple[str, str], ...], binding["source_row_ids"])
    ):
        raise ValueError("holdout outcome evidence does not bind the prepared target lineage")
    forecast_datasets = tuple(
        _forecast_dataset_from_payload(
            _verify_child(
                root,
                f"forecasts/{dataset_id}.json",
                contract=R2_HOLDOUT_FORECAST_CONTRACT,
                identity_key="dataset_id",
                expected_fields=_FORECAST_FIELDS,
                expected_id=dataset_id,
                _payload_cache=payload_cache,
            )
        )
        for dataset_id in seal.forecast_dataset_ids
    )
    coverage_datasets = tuple(
        _coverage_dataset_from_payload(
            _verify_child(
                root,
                f"coverage/{coverage_id}.json",
                contract=R2_HOLDOUT_COVERAGE_CONTRACT,
                identity_key="coverage_id",
                expected_fields=_COVERAGE_FIELDS,
                expected_id=coverage_id,
                _payload_cache=payload_cache,
            )
        )
        for coverage_id in seal.coverage_ids
    )
    question_values = _object_list(payload["questions"], "holdout evaluation questions")
    results = tuple(
        R2HoldoutQuestionResult(
            question_id=str(item["question_id"]),
            metric=str(item["metric"]),
            candidate_value=_optional_float(item["candidate_value"], "candidate metric value"),
            comparator_value=_optional_float(item["comparator_value"], "comparator metric value"),
            delta=_optional_float(item["delta"], "metric delta"),
            support_count=_int_value(item["support_count"], "question support"),
            coverage=_float_value(item["coverage"], "question coverage"),
            conclusion=HoldoutConclusion(str(item["conclusion"])),
            reason=str(item["reason"]),
        )
        for item in (
            _object_dict(raw, "holdout evaluation question result") for raw in question_values
        )
    )
    evaluation = R2HoldoutEvaluation(
        selection_manifest_id=str(payload["selection_manifest_id"]),
        seal_id=str(payload["seal_id"]),
        opened_marker_id=str(payload["opened_marker_id"]),
        questions=results,
        source_class=MarketDataSourceClass(str(payload["source_class"])),
        evidence_class=EvidenceClass(str(payload["evidence_class"])),
        holdout_scope=HoldoutScope(str(payload["holdout_scope"])),
        holdout_outcomes_accessed=bool(payload["holdout_outcomes_accessed"]),
        evaluation_id=str(payload["evaluation_id"]),
    )
    if (
        evaluation.selection_manifest_id != selection.manifest_id
        or evaluation.seal_id != seal.seal_id
        or evaluation.opened_marker_id != opened.marker_id
        or tuple(item.question_id for item in results)
        != tuple(item.question_id for item in seal.questions)
        or consumed.evaluation_id != evaluation.evaluation_id
    ):
        raise ValueError("holdout evaluation does not cover the exact frozen seal")
    from qtrad.application.r2_holdout import evaluate_holdout

    replayed = evaluate_holdout(
        selection=selection,
        seal=seal,
        opened_marker=opened,
        forecast_datasets=forecast_datasets,
        coverage_datasets=coverage_datasets,
        outcomes=dict(outcome_evidence.outcomes),
    )
    if replayed.as_json() != evaluation.as_json():
        raise ValueError("holdout evaluation metrics do not replay from authenticated evidence")
    return evaluation


def _artifact_reference(
    root: Path,
    source_path: str,
    bundle_path: str,
    *,
    contract: str,
    identity_key: str,
) -> tuple[ArtifactReference, dict[str, object]]:
    payload = _load_object(root / source_path)
    semantic_id = payload.get(identity_key)
    if contract == _TRAINING_TARGET_PROJECTION_CONTRACT:
        nested = _object_dict(payload.get("target_dataset"), "training target dataset")
        semantic_id = nested.get(identity_key)
    if not isinstance(semantic_id, str):
        raise ValueError(f"{contract} child lacks its semantic identity")
    reference = ArtifactReference(
        contract=contract,
        semantic_id=semantic_id,
        path=bundle_path,
        sha256=sha256(canonical_bytes(payload)).hexdigest(),
    )
    return reference, payload


def _verify_artifact_reference_payload(
    reference: ArtifactReference, payload: Mapping[str, object]
) -> None:
    identity_key_by_contract = {
        R2_HOLDOUT_SELECTION_CONTRACT: "manifest_id",
        R2_HOLDOUT_FORECAST_SEAL_CONTRACT: "seal_id",
        R2_HOLDOUT_OPENED_CONTRACT: "marker_id",
        R2_HOLDOUT_CONSUMED_CONTRACT: "marker_id",
        R2_HOLDOUT_EVALUATION_CONTRACT: "evaluation_id",
        R2_HOLDOUT_FEATURES_CONTRACT: "dataset_id",
        "qtrad-r2-features-v2": "dataset_id",
        TARGET_DATASET_CONTRACT: "dataset_id",
        _TRAINING_TARGET_PROJECTION_CONTRACT: "dataset_id",
        "qtrad-r2-final-fit-v1": "fit_id",
        R2_HOLDOUT_FORECAST_CONTRACT: "dataset_id",
        R2_HOLDOUT_COVERAGE_CONTRACT: "coverage_id",
        R2_HOLDOUT_OUTCOME_EVIDENCE_CONTRACT: "outcome_evidence_id",
        _PREPARATION_CLAIM_CONTRACT: "claim_id",
        _PREPARATION_AUTHORITY_CONTRACT: "authority_id",
        _PARTITIONED_PART_CONTRACT: "parent_semantic_id",
    }
    identity_key = identity_key_by_contract.get(reference.contract)
    if identity_key is None:
        raise ValueError(f"holdout bundle has an unsupported child contract: {reference.contract}")
    if payload.get("contract") != reference.contract:
        raise ValueError(f"holdout bundle child contract differs: {reference.path}")
    identity = payload.get(identity_key)
    if reference.contract == _TRAINING_TARGET_PROJECTION_CONTRACT:
        nested = _object_dict(payload.get("target_dataset"), "training target dataset")
        identity = nested.get(identity_key)
    if str(identity) != reference.semantic_id:
        raise ValueError(f"holdout bundle child identity differs: {reference.path}")


def build_holdout_bundle(
    root: Path,
    output: Path,
    *,
    holdout_target_source: R2HoldoutTargetSource,
    training_feature_datasets: Mapping[str, R2FeatureDataset] | None = None,
    immediate_parent_authority: Mapping[str, object] | None = None,
) -> R2HoldoutBundle:
    """Build a thin, hash-referenced bundle only after full replay verification."""
    payload_cache: _PayloadCache = {}
    seal = verify_holdout_preparation(
        root,
        holdout_target_source=holdout_target_source,
        training_feature_datasets=training_feature_datasets,
        immediate_parent_authority=immediate_parent_authority,
        _payload_cache=payload_cache,
    )
    selection = verify_holdout_selection(root / "selection.json")
    opened, consumed = verify_holdout_markers(root)
    evaluation = verify_holdout_evaluation(
        root,
        holdout_target_source=holdout_target_source,
        training_feature_datasets=training_feature_datasets,
        immediate_parent_authority=immediate_parent_authority,
        _payload_cache=payload_cache,
    )
    children: dict[str, Mapping[str, object]] = {}
    selection_ref, selection_payload = _artifact_reference(
        root,
        "selection.json",
        "selection.json",
        contract=R2_HOLDOUT_SELECTION_CONTRACT,
        identity_key="manifest_id",
    )
    seal_ref, seal_payload = _artifact_reference(
        root,
        "manifest.json",
        "forecast-seal.json",
        contract=R2_HOLDOUT_FORECAST_SEAL_CONTRACT,
        identity_key="seal_id",
    )
    opened_ref, opened_payload = _artifact_reference(
        root,
        "opened.json",
        "opened.json",
        contract=R2_HOLDOUT_OPENED_CONTRACT,
        identity_key="marker_id",
    )
    consumed_ref, consumed_payload = _artifact_reference(
        root,
        "consumed.json",
        "consumed.json",
        contract=R2_HOLDOUT_CONSUMED_CONTRACT,
        identity_key="marker_id",
    )
    evaluation_ref, evaluation_payload = _artifact_reference(
        root,
        "evaluation.json",
        "evaluation.json",
        contract=R2_HOLDOUT_EVALUATION_CONTRACT,
        identity_key="evaluation_id",
    )
    _, outcome_target_payload = _artifact_reference(
        root,
        "outcome-target.json",
        "outcome-target.json",
        contract=TARGET_DATASET_CONTRACT,
        identity_key="dataset_id",
    )
    children.update(
        {
            "selection.json": selection_payload,
            "forecast-seal.json": seal_payload,
            "opened.json": opened_payload,
            "consumed.json": consumed_payload,
            "evaluation.json": evaluation_payload,
            "outcome-target.json": outcome_target_payload,
        }
    )

    def child_replay_specs(
        relative: str,
        *,
        contract: str,
        identity_key: str,
    ) -> tuple[tuple[str, str, str, str], ...]:
        return tuple(
            (
                physical,
                physical,
                contract if physical == relative else _PARTITIONED_PART_CONTRACT,
                identity_key if physical == relative else "parent_semantic_id",
            )
            for physical in _physical_child_paths(root, relative, identity_field=identity_key)
        )

    replay_specs: list[tuple[str, str, str, str]] = [
        (
            _PREPARATION_CLAIM_FILE,
            _PREPARATION_CLAIM_FILE,
            _PREPARATION_CLAIM_CONTRACT,
            "claim_id",
        ),
        (
            _PREPARATION_AUTHORITY_FILE,
            _PREPARATION_AUTHORITY_FILE,
            _PREPARATION_AUTHORITY_CONTRACT,
            "authority_id",
        ),
        *[
            spec
            for _dataset_id, relative in _feature_dataset_paths(seal)
            for spec in child_replay_specs(
                relative, contract=R2_HOLDOUT_FEATURES_CONTRACT, identity_key="dataset_id"
            )
        ],
        *[
            spec
            for fit_id in seal.final_fit_ids
            for spec in child_replay_specs(
                f"fits/{fit_id}.json", contract="qtrad-r2-final-fit-v1", identity_key="fit_id"
            )
        ],
        *[
            spec
            for dataset_id in seal.forecast_dataset_ids
            for spec in child_replay_specs(
                f"forecasts/{dataset_id}.json",
                contract=R2_HOLDOUT_FORECAST_CONTRACT,
                identity_key="dataset_id",
            )
        ],
        *[
            spec
            for coverage_id in seal.coverage_ids
            for spec in child_replay_specs(
                f"coverage/{coverage_id}.json",
                contract=R2_HOLDOUT_COVERAGE_CONTRACT,
                identity_key="coverage_id",
            )
        ],
    ]
    if (root / "outcome-evidence.json").is_file():
        replay_specs.extend(
            child_replay_specs(
                "outcome-evidence.json",
                contract=R2_HOLDOUT_OUTCOME_EVIDENCE_CONTRACT,
                identity_key="outcome_evidence_id",
            )
        )
    replay_specs.extend(
        child_replay_specs(
            "outcome-target.json",
            contract=TARGET_DATASET_CONTRACT,
            identity_key="dataset_id",
        )
    )
    replay_refs: list[ArtifactReference] = []
    for source_path, bundle_path, contract, identity_key in replay_specs:
        reference, payload = _artifact_reference(
            root,
            source_path,
            bundle_path,
            contract=contract,
            identity_key=identity_key,
        )
        replay_refs.append(reference)
        children[bundle_path] = payload
    bundle = R2HoldoutBundle.create(
        selection=selection_ref,
        forecast_seal=seal_ref,
        opened_marker=opened_ref,
        consumed_marker=consumed_ref,
        evaluation=evaluation_ref,
        replay_evidence=tuple(replay_refs),
        source_class=selection.source_class,
        evidence_class=selection.evidence_class,
        holdout_scope=selection.holdout_scope,
    )
    if (
        opened.marker_id != consumed.opened_marker_id
        or evaluation.evaluation_id != consumed.evaluation_id
    ):
        raise ValueError("holdout bundle lifecycle references are inconsistent")
    return bundle


def write_holdout_bundle(
    output: Path,
    bundle: R2HoldoutBundle,
    children: Mapping[str, Mapping[str, object]],
) -> Path:
    refs = (
        bundle.selection,
        bundle.forecast_seal,
        bundle.opened_marker,
        bundle.consumed_marker,
        bundle.evaluation,
        *bundle.replay_evidence,
    )
    if set(children) != {ref.path for ref in refs}:
        raise ValueError("holdout bundle children must exactly match declared references")
    for ref in refs:
        if ref.path == "manifest.json":
            raise ValueError("holdout bundle manifest path is reserved")
        payload = children[ref.path]
        _verify_artifact_reference_payload(ref, payload)
        if sha256(canonical_bytes(payload)).hexdigest() != ref.sha256:
            raise ValueError(f"holdout child digest differs from its reference: {ref.path}")
        _write_json(output / ref.path, payload)
    _write_json(output / "manifest.json", bundle.as_json())
    return output / "manifest.json"


def _verify_bundle_replay(
    path: Path,
    refs: Sequence[ArtifactReference],
    *,
    holdout_target_source: R2HoldoutTargetSource,
    training_feature_datasets: Mapping[str, R2FeatureDataset] | None = None,
    immediate_parent_authority: Mapping[str, object] | None = None,
) -> None:
    with TemporaryDirectory(prefix="qtrad-r2-holdout-bundle-") as temporary:
        replay_root = Path(temporary)
        for reference in refs:
            source = _safe_child(path, reference.path)
            relative = "manifest.json" if reference.path == "forecast-seal.json" else reference.path
            target = replay_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
        replayed = verify_holdout_evaluation(
            replay_root,
            holdout_target_source=holdout_target_source,
            training_feature_datasets=training_feature_datasets,
            immediate_parent_authority=immediate_parent_authority,
        )
        persisted = _load_object(replay_root / "evaluation.json")
        if replayed.as_json() != persisted:
            raise ValueError("holdout bundle evidence does not replay independently")


def verify_holdout_bundle(
    path: Path,
    *,
    holdout_target_source: R2HoldoutTargetSource,
    training_feature_datasets: Mapping[str, R2FeatureDataset] | None = None,
    immediate_parent_authority: Mapping[str, object] | None = None,
) -> R2HoldoutBundle:
    payload = _load_object(path / "manifest.json")
    expected = {
        "contract",
        "schema_version",
        "selection",
        "forecast_seal",
        "opened_marker",
        "consumed_marker",
        "evaluation",
        "replay_evidence",
        "source_class",
        "evidence_class",
        "holdout_scope",
        "bundle_id",
    }
    if set(payload) != expected or payload.get("contract") != R2_HOLDOUT_BUNDLE_CONTRACT:
        raise ValueError("holdout bundle manifest has unknown or unsupported fields")
    refs: list[ArtifactReference] = []
    for key in ("selection", "forecast_seal", "opened_marker", "consumed_marker", "evaluation"):
        refs.append(ArtifactReference.from_json(payload[key]))
    replay = payload["replay_evidence"]
    if not isinstance(replay, list):
        raise ValueError("holdout replay evidence must be an array")
    refs.extend(ArtifactReference.from_json(item) for item in replay)
    for ref in refs:
        child = _safe_child(path, ref.path)
        if not child.is_file() or child.is_symlink():
            raise ValueError(f"holdout bundle child is unavailable: {ref.path}")
        if sha256(child.read_bytes()).hexdigest() != ref.sha256:
            raise ValueError(f"holdout bundle child changed: {ref.path}")
        child_payload = _load_object(child)
        _verify_artifact_reference_payload(ref, child_payload)
    _reject_orphans(path, {"manifest.json", *(ref.path for ref in refs)})
    semantic = {key: value for key, value in payload.items() if key != "bundle_id"}
    expected_id = sha256(
        json.dumps(semantic, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if payload["bundle_id"] != expected_id:
        raise ValueError("holdout bundle ID does not authenticate its content")
    _verify_bundle_replay(
        path,
        tuple(refs),
        holdout_target_source=holdout_target_source,
        training_feature_datasets=training_feature_datasets,
        immediate_parent_authority=immediate_parent_authority,
    )
    return R2HoldoutBundle(
        selection=refs[0],
        forecast_seal=refs[1],
        opened_marker=refs[2],
        consumed_marker=refs[3],
        evaluation=refs[4],
        replay_evidence=tuple(refs[5:]),
        source_class=MarketDataSourceClass(str(payload["source_class"])),
        evidence_class=EvidenceClass(str(payload["evidence_class"])),
        holdout_scope=HoldoutScope(str(payload["holdout_scope"])),
        bundle_id=str(payload["bundle_id"]),
    )


def write_built_holdout_bundle(
    root: Path,
    output: Path,
    *,
    holdout_target_source: R2HoldoutTargetSource,
    training_feature_datasets: Mapping[str, R2FeatureDataset] | None = None,
    immediate_parent_authority: Mapping[str, object] | None = None,
) -> R2HoldoutBundle:
    bundle = build_holdout_bundle(
        root,
        output,
        holdout_target_source=holdout_target_source,
        training_feature_datasets=training_feature_datasets,
        immediate_parent_authority=immediate_parent_authority,
    )
    children: dict[str, Mapping[str, object]] = {}
    refs = (
        bundle.selection,
        bundle.forecast_seal,
        bundle.opened_marker,
        bundle.consumed_marker,
        bundle.evaluation,
        *bundle.replay_evidence,
    )
    for reference in refs:
        source_name = "manifest.json" if reference.path == "forecast-seal.json" else reference.path
        children[reference.path] = _load_object(root / source_name)
    write_holdout_bundle(output, bundle, children)
    return verify_holdout_bundle(
        output,
        holdout_target_source=holdout_target_source,
        training_feature_datasets=training_feature_datasets,
        immediate_parent_authority=immediate_parent_authority,
    )


def load_prior_selection_manifest(path: Path) -> object:
    """Load and independently authenticate the existing v2 selection contract."""
    from qtrad.domain.r2_evaluation import SelectionManifest
    from qtrad.domain.r2_readiness import ModelFamily
    from qtrad.runtime.r2_verification import _selection_decisions_from_payload

    payload = _load_object(path)
    expected = {
        "contract",
        "schema_version",
        "experiment_configuration_id",
        "evidence_class",
        "evaluation_report_id",
        "local_comparator_manifest_id",
        "evaluated_configuration_ids",
        "predeclared_comparators",
        "primary_metric",
        "secondary_metrics",
        "acceptance_thresholds",
        "decisions",
        "selected_configuration_ids",
        "holdout_comparator_configuration_ids",
        "final_fitting_procedure",
        "holdout_range",
        "holdout_state_verification",
        "application_image_identity",
        "frozen_at",
        "frozen_by",
        "manifest_id",
    }
    optional = {"source_class", "foundation_bundle_id", "oof_id"}
    if set(payload) - expected - optional or not expected <= set(payload):
        raise ValueError("prior R2 selection has unknown or missing fields")
    if payload["contract"] != "qtrad-r2-selection-v2" or payload["schema_version"] != 1:
        raise ValueError("prior R2 selection is not qtrad-r2-selection-v2")
    raw_thresholds = _object_dict(payload["acceptance_thresholds"], "prior R2 selection thresholds")
    raw_decisions = _object_list(payload["decisions"], "prior R2 selection decisions")
    source = payload.get("source_class")
    foundation = payload.get("foundation_bundle_id")
    oof = payload.get("oof_id")
    return SelectionManifest(
        experiment_configuration_id=str(payload["experiment_configuration_id"]),
        evidence_class=EvidenceClass(str(payload["evidence_class"])),
        evaluation_report_id=str(payload["evaluation_report_id"]),
        local_comparator_manifest_id=str(payload["local_comparator_manifest_id"]),
        evaluated_configuration_ids=tuple(
            str(item) for item in cast(list[object], payload["evaluated_configuration_ids"])
        ),
        predeclared_comparators=tuple(
            ModelFamily(str(item))
            for item in cast(list[object], payload["predeclared_comparators"])
        ),
        primary_metric=str(payload["primary_metric"]),
        secondary_metrics=tuple(
            str(item) for item in cast(list[object], payload["secondary_metrics"])
        ),
        acceptance_thresholds=tuple(
            sorted(
                (str(key), _float_value(value, "selection threshold"))
                for key, value in raw_thresholds.items()
            )
        ),
        decisions=_selection_decisions_from_payload(raw_decisions),
        selected_configuration_ids=tuple(
            str(item) for item in cast(list[object], payload["selected_configuration_ids"])
        ),
        holdout_comparator_configuration_ids=tuple(
            str(item)
            for item in cast(list[object], payload["holdout_comparator_configuration_ids"])
        ),
        final_fitting_procedure=str(payload["final_fitting_procedure"]),
        holdout_range=(
            datetime.fromisoformat(str(cast(list[object], payload["holdout_range"])[0])),
            datetime.fromisoformat(str(cast(list[object], payload["holdout_range"])[1])),
        ),
        holdout_state_verification=str(payload["holdout_state_verification"]),
        application_image_identity=str(payload["application_image_identity"]),
        frozen_at=datetime.fromisoformat(str(payload["frozen_at"])),
        frozen_by=str(payload["frozen_by"]),
        manifest_id=str(payload["manifest_id"]),
        market_data_source_class=(None if source is None else MarketDataSourceClass(str(source))),
        foundation_bundle_id=None if foundation is None else str(foundation),
        oof_id=None if oof is None else str(oof),
    )


def load_holdout_policy(path: Path):
    from qtrad.domain.r2_holdout import R2FinalFittingPolicy

    return R2FinalFittingPolicy.from_json(_load_object(path))


def load_holdout_questions(path: Path):
    from qtrad.domain.r2_holdout import R2HoldoutQuestion

    payload = _load_object(path)
    raw = payload.get("questions")
    if set(payload) != {"questions"}:
        raise ValueError("holdout question register must contain only a questions array")
    return tuple(
        R2HoldoutQuestion.from_json(_object_dict(item, "holdout question"))
        for item in _object_list(raw, "holdout questions")
    )


def _forecast_dataset_from_payload(payload: Mapping[str, object]):
    from qtrad.domain.r2_holdout import R2HoldoutForecastDataset, R2HoldoutForecastRow
    from qtrad.domain.r2_readiness import ModelFamily

    rows = _object_list(payload["rows"], "holdout forecast rows")
    expected = _object_list(payload["expected_opportunity_ids"], "forecast opportunities")
    bindings = _object_list(payload["opportunity_target_ids"], "forecast opportunity bindings")
    parsed_rows = []
    for raw_item in rows:
        item = _object_dict(raw_item, "holdout forecast row")
        parsed_rows.append(
            R2HoldoutForecastRow(
                configuration_id=str(item["configuration_id"]),
                target_id=str(item["target_id"]),
                target_instrument_id=str(item["target_instrument_id"]),
                feature_row_id=(
                    None if item["feature_row_id"] is None else str(item["feature_row_id"])
                ),
                forecast=_float_value(item["forecast"], "forecast"),
                model_family=ModelFamily(str(item["model_family"])),
                row_id=str(item["row_id"]),
            )
        )
    return R2HoldoutForecastDataset(
        selection_manifest_id=str(payload["selection_manifest_id"]),
        feature_dataset_id=(
            None if payload["feature_dataset_id"] is None else str(payload["feature_dataset_id"])
        ),
        configuration_id=str(payload["configuration_id"]),
        final_fit_id=(None if payload["final_fit_id"] is None else str(payload["final_fit_id"])),
        final_fit_ids=(
            tuple(str(item) for item in cast(list[object], payload["final_fit_ids"]))
            if "final_fit_ids" in payload
            else ((str(payload["final_fit_id"]),) if payload["final_fit_id"] is not None else ())
        ),
        rows=tuple(parsed_rows),
        expected_opportunity_ids=tuple(str(item) for item in expected),
        opportunity_target_ids=tuple(
            (
                str(cast(list[object], item)[0]),
                str(cast(list[object], item)[1]),
            )
            for item in bindings
        ),
        source_class=MarketDataSourceClass(str(payload["source_class"])),
        evidence_class=EvidenceClass(str(payload["evidence_class"])),
        holdout_scope=HoldoutScope(str(payload["holdout_scope"])),
        holdout_outcomes_accessed=bool(payload["holdout_outcomes_accessed"]),
        dataset_id=str(payload["dataset_id"]),
    )


def _coverage_dataset_from_payload(payload: Mapping[str, object]):
    from qtrad.domain.r2_holdout import (
        HoldoutOpportunityDisposition,
        R2HoldoutCoverageDataset,
        R2HoldoutCoverageRow,
    )

    rows = _object_list(payload["rows"], "holdout coverage rows")
    expected = _object_list(payload["expected_opportunity_ids"], "coverage opportunities")
    parsed_rows = []
    for raw_item in rows:
        item = _object_dict(raw_item, "holdout coverage row")
        forecast_row_id = item["forecast_row_id"]
        parsed_rows.append(
            R2HoldoutCoverageRow(
                configuration_id=str(item["configuration_id"]),
                opportunity_id=str(item["opportunity_id"]),
                disposition=HoldoutOpportunityDisposition(str(item["disposition"])),
                forecast_row_id=None if forecast_row_id is None else str(forecast_row_id),
                reason=str(item["reason"]),
            )
        )
    return R2HoldoutCoverageDataset(
        selection_manifest_id=str(payload["selection_manifest_id"]),
        feature_dataset_id=(
            None if payload["feature_dataset_id"] is None else str(payload["feature_dataset_id"])
        ),
        configuration_id=str(payload["configuration_id"]),
        expected_opportunity_ids=tuple(str(item) for item in expected),
        rows=tuple(parsed_rows),
        source_class=MarketDataSourceClass(str(payload["source_class"])),
        evidence_class=EvidenceClass(str(payload["evidence_class"])),
        holdout_scope=HoldoutScope(str(payload["holdout_scope"])),
        holdout_outcomes_accessed=bool(payload["holdout_outcomes_accessed"]),
        coverage_id=str(payload["coverage_id"]),
    )


def reveal_holdout_from_files(
    root: Path,
    *,
    outcomes_path: Path,
    expected_selection_manifest_id: str,
    expected_seal_id: str,
    acknowledgement: str,
    opened_by: str,
    consumed_by: str,
    opened_at: datetime,
    consumed_at: datetime,
    holdout_target_source: R2HoldoutTargetSource,
    training_feature_datasets: Mapping[str, R2FeatureDataset] | None = None,
    immediate_parent_authority: Mapping[str, object] | None = None,
):
    """Reveal persisted disposable children; outcome bytes are read after OPENED."""
    from qtrad.application.r2_holdout import evaluate_holdout

    payload_cache: _PayloadCache = {}
    selection = verify_holdout_selection(root / "selection.json")
    seal = verify_holdout_preparation(
        root,
        holdout_target_source=holdout_target_source,
        training_feature_datasets=training_feature_datasets,
        immediate_parent_authority=immediate_parent_authority,
        _payload_cache=payload_cache,
    )
    forecast_datasets = tuple(
        _forecast_dataset_from_payload(
            _verify_child(
                root,
                f"forecasts/{dataset_id}.json",
                contract=R2_HOLDOUT_FORECAST_CONTRACT,
                identity_key="dataset_id",
                expected_fields=_FORECAST_FIELDS,
                expected_id=dataset_id,
                _payload_cache=payload_cache,
            )
        )
        for dataset_id in seal.forecast_dataset_ids
    )
    coverage_datasets = tuple(
        _coverage_dataset_from_payload(
            _verify_child(
                root,
                f"coverage/{coverage_id}.json",
                contract=R2_HOLDOUT_COVERAGE_CONTRACT,
                identity_key="coverage_id",
                expected_fields=_COVERAGE_FIELDS,
                expected_id=coverage_id,
                _payload_cache=payload_cache,
            )
        )
        for coverage_id in seal.coverage_ids
    )
    target_dataset: TargetDataset | None = None

    def load_outcomes() -> TargetDataset:
        nonlocal target_dataset
        target_dataset = _target_dataset_from_payload(
            _load_object(outcomes_path),
            field="canonical target dataset",
        )
        return target_dataset

    def evaluate(
        outcomes: Mapping[str, float], opened: R2HoldoutOpenedMarker
    ) -> R2HoldoutEvaluation:
        if target_dataset is None:
            raise RuntimeError("canonical target dataset was not loaded")
        target_instruments = {row.target_id: row.instrument_id for row in target_dataset.rows}
        return evaluate_holdout(
            selection=selection,
            seal=seal,
            opened_marker=opened,
            forecast_datasets=forecast_datasets,
            coverage_datasets=coverage_datasets,
            outcomes=outcomes,
            target_instruments=target_instruments,
        )

    return reveal_holdout(
        root,
        expected_selection_manifest_id=expected_selection_manifest_id,
        expected_seal_id=expected_seal_id,
        acknowledgement=acknowledgement,
        opened_by=opened_by,
        consumed_by=consumed_by,
        opened_at=opened_at,
        consumed_at=consumed_at,
        holdout_target_source=holdout_target_source,
        outcome_loader=load_outcomes,
        evaluator=evaluate,
        training_feature_datasets=training_feature_datasets,
        immediate_parent_authority=immediate_parent_authority,
        _payload_cache=payload_cache,
    )


def _reveal_confirmatory_holdout(
    root: Path,
    *,
    expected_selection_manifest_id: str,
    expected_seal_id: str,
    acknowledgement: str,
    opened_by: str,
    consumed_by: str,
    opened_at: datetime,
    consumed_at: Callable[[], datetime],
    holdout_target_source: R2HoldoutTargetSource,
    outcome_loader: Callable[[], TargetDataset],
    training_feature_datasets: Mapping[str, R2FeatureDataset] | None = None,
    immediate_parent_authority: Mapping[str, object] | None = None,
) -> tuple[R2HoldoutEvaluation | None, R2HoldoutConsumedMarker]:
    """Run frozen confirmatory evaluation after marker-first target decoding."""

    from qtrad.application.r2_holdout import evaluate_holdout

    payload_cache: _PayloadCache = {}
    selection = verify_holdout_selection(root / "selection.json")
    seal = verify_holdout_preparation(
        root,
        _confirmatory_token=_CONFIRMATORY_G2_PREPARATION_TOKEN,
        holdout_target_source=holdout_target_source,
        training_feature_datasets=training_feature_datasets,
        immediate_parent_authority=immediate_parent_authority,
        _payload_cache=payload_cache,
    )
    if selection.holdout_scope is not HoldoutScope.CONFIRMATORY:
        raise ValueError("confirmatory reveal requires a confirmatory selection")
    forecast_datasets = tuple(
        _forecast_dataset_from_payload(
            _verify_child(
                root,
                f"forecasts/{dataset_id}.json",
                contract=R2_HOLDOUT_FORECAST_CONTRACT,
                identity_key="dataset_id",
                expected_fields=_FORECAST_FIELDS,
                expected_id=dataset_id,
                _payload_cache=payload_cache,
            )
        )
        for dataset_id in seal.forecast_dataset_ids
    )
    coverage_datasets = tuple(
        _coverage_dataset_from_payload(
            _verify_child(
                root,
                f"coverage/{coverage_id}.json",
                contract=R2_HOLDOUT_COVERAGE_CONTRACT,
                identity_key="coverage_id",
                expected_fields=_COVERAGE_FIELDS,
                expected_id=coverage_id,
                _payload_cache=payload_cache,
            )
        )
        for coverage_id in seal.coverage_ids
    )
    target_dataset: TargetDataset | None = None

    def load_outcomes() -> TargetDataset:
        nonlocal target_dataset
        target_dataset = outcome_loader()
        return target_dataset

    def evaluate(
        outcomes: Mapping[str, float], opened: R2HoldoutOpenedMarker
    ) -> R2HoldoutEvaluation:
        if target_dataset is None:
            raise RuntimeError("canonical target dataset was not loaded")
        return evaluate_holdout(
            selection=selection,
            seal=seal,
            opened_marker=opened,
            forecast_datasets=forecast_datasets,
            coverage_datasets=coverage_datasets,
            outcomes=outcomes,
            target_instruments={row.target_id: row.instrument_id for row in target_dataset.rows},
        )

    return reveal_holdout(
        root,
        expected_selection_manifest_id=expected_selection_manifest_id,
        expected_seal_id=expected_seal_id,
        acknowledgement=acknowledgement,
        opened_by=opened_by,
        consumed_by=consumed_by,
        opened_at=opened_at,
        consumed_at=consumed_at,
        outcome_loader=load_outcomes,
        evaluator=evaluate,
        _confirmatory_token=_CONFIRMATORY_G2_LIFECYCLE_TOKEN,
        holdout_target_source=holdout_target_source,
        training_feature_datasets=training_feature_datasets,
        immediate_parent_authority=immediate_parent_authority,
        _payload_cache=payload_cache,
    )


def _verify_confirmatory_holdout_preparation(
    root: Path,
    *,
    holdout_target_source: R2HoldoutTargetSource,
    training_feature_datasets: Mapping[str, R2FeatureDataset] | None = None,
    immediate_parent_authority: Mapping[str, object] | None = None,
) -> R2HoldoutForecastSeal:
    return verify_holdout_preparation(
        root,
        _confirmatory_token=_CONFIRMATORY_G2_LIFECYCLE_TOKEN,
        holdout_target_source=holdout_target_source,
        training_feature_datasets=training_feature_datasets,
        immediate_parent_authority=immediate_parent_authority,
    )


def _verify_confirmatory_holdout_evaluation(
    root: Path,
    *,
    holdout_target_source: R2HoldoutTargetSource,
    training_feature_datasets: Mapping[str, R2FeatureDataset] | None = None,
    immediate_parent_authority: Mapping[str, object] | None = None,
) -> R2HoldoutEvaluation:
    return verify_holdout_evaluation(
        root,
        _confirmatory_token=_CONFIRMATORY_G2_LIFECYCLE_TOKEN,
        holdout_target_source=holdout_target_source,
        training_feature_datasets=training_feature_datasets,
        immediate_parent_authority=immediate_parent_authority,
    )
