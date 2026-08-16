"""Bounded create-only persistence for the outcome-blind R2 target source.

The source semantic contract remains qtrad-r2-holdout-target-source-v1. A
large source is represented by a small manifest at the requested path and
deterministically bounded JSON part files beside it. The loader reconstructs
the domain contract and authenticates its semantic source ID before returning.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from qtrad.domain.events import JsonValue
from qtrad.domain.foundation import TARGET_DATASET_CONTRACT
from qtrad.domain.r2_holdout import R2HoldoutTargetSource
from qtrad.runtime.r2_bundles import atomic_create, canonical_bytes

_SOURCE_STORAGE = "qtrad-r2-holdout-target-source-bounded-parts-v1"
_PART_CONTRACT = "qtrad-r2-holdout-target-source-part-v1"
_PART_SCHEMA_VERSION = 1
_MAX_PART_BYTES = 64 * 1024 * 1024
_MAX_MANIFEST_BYTES = _MAX_PART_BYTES
_SOURCE_AUTHORITY_TOKEN = object()


@dataclass(frozen=True, slots=True)
class R2HoldoutTargetSourceAuthority:
    """One authenticated, bounded source load for an outcome-blind stage."""

    source: R2HoldoutTargetSource
    manifest_path: Path
    closure_id: str
    part_paths: frozenset[str]
    _token: object = field(repr=False, compare=False)

    @classmethod
    def _create(
        cls,
        token: object,
        *,
        source: R2HoldoutTargetSource,
        manifest_path: Path,
        closure_id: str,
        part_paths: frozenset[str],
    ) -> R2HoldoutTargetSourceAuthority:
        if token is not _SOURCE_AUTHORITY_TOKEN:
            raise TypeError("holdout target source authority construction is private")
        return cls(source, manifest_path.absolute(), closure_id, part_paths, token)

    @property
    def source_id(self) -> str:
        return self.source.source_id


def _json_value(value: object) -> JsonValue:
    return cast(JsonValue, value)


def _reject_symlink_ancestors(path: Path) -> None:
    """Reject a manifest or part path that traverses a symlinked ancestor."""
    current = path.parent
    while current != current.parent:
        if current.is_symlink():
            raise ValueError(f"holdout target source path traverses a symlink: {current}")
        current = current.parent


def _json_object_from_bytes(content: bytes, path: Path, *, limit: int) -> dict[str, object]:
    if not content:
        raise ValueError(f"holdout target source child is empty: {path}")
    if len(content) > limit:
        raise ValueError(f"holdout target source child exceeds its byte bound: {path}")
    value = json.loads(content)
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"holdout target source child must be a JSON object: {path}")
    return cast(dict[str, object], value)


def _json_object(path: Path, *, limit: int = _MAX_PART_BYTES) -> dict[str, object]:
    _reject_symlink_ancestors(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"holdout target source child must be a regular file: {path}")
    return _json_object_from_bytes(path.read_bytes(), path, limit=limit)


def _safe_part_path(root: Path, relative: str) -> Path:
    candidate = root / relative
    resolved_root = root.resolve()
    resolved_candidate = candidate.resolve()
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError as error:
        raise ValueError("holdout target source part path escapes its manifest") from error
    current = root
    for part in relative.split("/"):
        current /= part
        if current.is_symlink():
            raise ValueError("holdout target source part path traverses a symlink")
    return candidate


def _part_payload(
    *,
    source_id: str,
    kind: str,
    part_index: int,
    rows: Sequence[JsonValue],
) -> dict[str, JsonValue]:
    return {
        "contract": _PART_CONTRACT,
        "schema_version": _PART_SCHEMA_VERSION,
        "source_id": source_id,
        "kind": kind,
        "part_index": part_index,
        "rows": list(rows),
    }


def _encoded_part(
    *,
    source_id: str,
    kind: str,
    part_index: int,
    rows: Sequence[JsonValue],
) -> tuple[dict[str, JsonValue], bytes]:
    payload = _part_payload(
        source_id=source_id, kind=kind, part_index=part_index, rows=rows
    )
    return payload, canonical_bytes(payload)


def _part_envelope_size(
    *, source_id: str, kind: str, part_index: int
) -> tuple[int, bytes, bytes]:
    empty = canonical_bytes(
        _part_payload(source_id=source_id, kind=kind, part_index=part_index, rows=[])
    )
    marker = b'"rows":[]'
    marker_start = empty.index(marker)
    prefix = empty[:marker_start] + b'"rows":['
    suffix = empty[marker_start + len(marker) - 1 :]
    return len(prefix) + len(suffix), prefix, suffix


def _row_bytes(row: JsonValue) -> bytes:
    if not isinstance(row, Mapping):
        raise ValueError("holdout target source rows must be JSON objects")
    return canonical_bytes(cast(Mapping[str, object], row))[:-1]


def _part_batches(
    *,
    source_id: str,
    kind: str,
    rows: Iterable[JsonValue],
) -> Iterator[tuple[int, list[JsonValue], int]]:
    current_rows: list[JsonValue] = []
    current_size = 0
    part_index = 0
    for row in rows:
        row_bytes = _row_bytes(row)
        if not current_rows:
            envelope_size, _prefix, _suffix = _part_envelope_size(
                source_id=source_id, kind=kind, part_index=part_index
            )
            current_size = envelope_size
        probe_size = current_size + len(row_bytes) + (1 if current_rows else 0)
        if probe_size > _MAX_PART_BYTES:
            if not current_rows:
                raise ValueError(f"holdout target source {kind} row exceeds its 64 MiB part bound")
            yield part_index, current_rows, current_size
            part_index += 1
            current_rows = []
            envelope_size, _prefix, _suffix = _part_envelope_size(
                source_id=source_id, kind=kind, part_index=part_index
            )
            current_size = envelope_size
            probe_size = current_size + len(row_bytes)
            if probe_size > _MAX_PART_BYTES:
                raise ValueError(
                    f"holdout target source {kind} row exceeds its 64 MiB part bound"
                )
        current_rows.append(row)
        current_size = probe_size
    if current_rows:
        yield part_index, current_rows, current_size


def _part_sizes(
    *, source_id: str, kind: str, rows: Iterable[JsonValue]
) -> tuple[int, ...]:
    sizes: list[int] = []
    for _part_index, _part_rows, size in _part_batches(
        source_id=source_id, kind=kind, rows=rows
    ):
        sizes.append(size)
    return tuple(sizes)


def _split_part_rows(
    *,
    source_id: str,
    kind: str,
    rows: Sequence[JsonValue],
) -> tuple[tuple[dict[str, JsonValue], bytes], ...]:
    """Return encoded parts for focused deterministic partition tests."""
    parts: list[tuple[dict[str, JsonValue], bytes]] = []
    for part_index, part_rows, expected_size in _part_batches(
        source_id=source_id, kind=kind, rows=rows
    ):
        payload, encoded = _encoded_part(
            source_id=source_id, kind=kind, part_index=part_index, rows=part_rows
        )
        if len(encoded) != expected_size or len(encoded) > _MAX_PART_BYTES:
            raise ValueError(f"holdout target source {kind} part exceeds its byte bound")
        parts.append((payload, encoded))
    return tuple(parts)


def _part_relative_path(output: Path, kind: str, part_index: int) -> str:
    return f"{output.name}.parts/{kind}/part-{part_index:06d}.json"


def _preflight_paths(output: Path, part_counts: Mapping[str, int]) -> None:
    _reject_symlink_ancestors(output)
    if output.is_symlink() or output.exists():
        raise FileExistsError(f"create-only holdout target source already exists: {output}")
    parts_root = output.parent / f"{output.name}.parts"
    if parts_root.is_symlink() or parts_root.exists():
        raise FileExistsError(
            f"create-only holdout target source parts already exist: {parts_root}"
        )
    for kind, count in part_counts.items():
        for part_index in range(count):
            _safe_part_path(output.parent, _part_relative_path(output, kind, part_index))


def _write_parts(
    *,
    output: Path,
    source_id: str,
    kind: str,
    rows: Iterable[JsonValue],
    expected_sizes: Sequence[int],
    created_paths: list[Path],
) -> list[dict[str, JsonValue]]:
    references: list[dict[str, JsonValue]] = []
    for part_index, part_rows, expected_size in _part_batches(
        source_id=source_id, kind=kind, rows=rows
    ):
        _payload, encoded = _encoded_part(
            source_id=source_id, kind=kind, part_index=part_index, rows=part_rows
        )
        if part_index >= len(expected_sizes) or len(encoded) != expected_size:
            raise ValueError(f"holdout target source {kind} changed while partitioning")
        if len(encoded) > _MAX_PART_BYTES:
            raise ValueError(f"holdout target source {kind} part exceeds its byte bound")
        relative = _part_relative_path(output, kind, part_index)
        path = _safe_part_path(output.parent, relative)
        atomic_create(path, encoded)
        created_paths.append(path)
        references.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(encoded).hexdigest(),
                "row_count": len(part_rows),
            }
        )
    if len(references) != len(expected_sizes):
        raise ValueError(f"holdout target source {kind} changed while partitioning")
    return references


def _manifest_payload(
    source: R2HoldoutTargetSource,
    *,
    target_parts: Sequence[Mapping[str, JsonValue]],
    pre_holdout_target_parts: Sequence[Mapping[str, JsonValue]],
    opportunity_parts: Sequence[Mapping[str, JsonValue]],
) -> dict[str, JsonValue]:
    manifest: dict[str, JsonValue] = {
        "contract": source.CONTRACT,
        "schema_version": source.SCHEMA_VERSION,
        "storage": _SOURCE_STORAGE,
        "source_id": source.source_id,
        "source_target_dataset_id": source.source_target_dataset_id,
        "observation_dataset_id": source.observation_dataset_id,
        "foundation_configuration_id": source.foundation_configuration_id,
        "causal_panel_dataset_id": source.causal_panel_dataset_id,
        "availability_evidence_id": source.availability_evidence_id,
        "target_index_dataset_id": source.target_index_dataset_id,
        "causal_metadata_dataset_id": source.causal_metadata_dataset_id,
        "holdout_range": [item.isoformat() for item in source.holdout_range],
        "primary_horizon_seconds": source.primary_horizon_seconds,
        "target_instruments": list(source.target_instruments),
        "pre_holdout_target_dataset_id": source.pre_holdout_target_dataset.dataset_id,
        "pre_holdout_observation_dataset_id": (
            source.pre_holdout_target_dataset.observation_dataset_id
        ),
        "pre_holdout_foundation_configuration_id": (
            source.pre_holdout_target_dataset.foundation_configuration_id
        ),
        "target_parts": _json_value(list(target_parts)),
        "pre_holdout_target_parts": _json_value(list(pre_holdout_target_parts)),
        "opportunity_parts": _json_value(list(opportunity_parts)),
        "target_count": len(source.targets),
        "pre_holdout_target_count": len(source.pre_holdout_target_dataset.rows),
        "opportunity_count": len(source.opportunities),
    }
    manifest["closure_id"] = _json_value(_bounded_source_closure_id(manifest))
    return manifest


def _bounded_source_closure_id(manifest: Mapping[str, object]) -> str:
    """Hash semantic source identity and ordered declared part bytes/paths."""

    def parts(field: str) -> list[dict[str, object]]:
        raw_parts = manifest.get(field)
        if not isinstance(raw_parts, list):
            raise ValueError(f"holdout target source {field} must be an array")
        result: list[dict[str, object]] = []
        for raw_part in raw_parts:
            if not isinstance(raw_part, Mapping):
                raise ValueError(f"holdout target source {field} reference is invalid")
            if set(raw_part) != {"path", "sha256", "row_count"}:
                raise ValueError(f"holdout target source {field} reference has unknown fields")
            digest = raw_part.get("sha256")
            row_count = raw_part.get("row_count")
            relative = raw_part.get("path")
            if (
                not isinstance(relative, str)
                or not isinstance(digest, str)
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
                or not isinstance(row_count, int)
                or row_count < 0
            ):
                raise ValueError(f"holdout target source {field} reference is malformed")
            result.append({"path": relative, "sha256": digest, "row_count": row_count})
        return result

    source_id = manifest.get("source_id")
    if not isinstance(source_id, str):
        raise ValueError("holdout target source bounded manifest has no source ID")
    closure = {
        "contract": _SOURCE_STORAGE,
        "schema_version": _PART_SCHEMA_VERSION,
        "source_id": source_id,
        "target_parts": parts("target_parts"),
        "pre_holdout_target_parts": parts("pre_holdout_target_parts"),
        "opportunity_parts": parts("opportunity_parts"),
    }
    return hashlib.sha256(canonical_bytes(closure)).hexdigest()


def bounded_source_closure_id(manifest: Mapping[str, object]) -> str:
    """Return the authenticated physical closure identity for a bounded manifest."""
    return _bounded_source_closure_id(manifest)


def _cleanup_created_paths(paths: Sequence[Path]) -> None:
    for path in reversed(paths):
        path.unlink(missing_ok=True)
    directory_set: set[Path] = set()
    for path in paths:
        current = path.parent
        while current.name:
            directory_set.add(current)
            if current.name.endswith(".parts"):
                break
            current = current.parent
    directories = sorted(directory_set, key=lambda item: len(item.parts), reverse=True)
    for directory in directories:
        with suppress(OSError):
            directory.rmdir()


def write_r2_holdout_target_source(
    output: Path, source: R2HoldoutTargetSource
) -> dict[str, JsonValue]:
    """Persist one source manifest and bounded child parts, create-only."""
    _preflight_paths(output, {})
    target_sizes = _part_sizes(
        source_id=source.source_id,
        kind="targets",
        rows=(item.as_json() for item in source.targets),
    )
    pre_holdout_sizes = _part_sizes(
        source_id=source.source_id,
        kind="pre-holdout-target",
        rows=(item.as_json() for item in source.pre_holdout_target_dataset.rows),
    )
    opportunity_sizes = _part_sizes(
        source_id=source.source_id,
        kind="opportunities",
        rows=(item.as_json() for item in source.opportunities),
    )
    _preflight_paths(
        output,
        {
            "targets": len(target_sizes),
            "pre-holdout-target": len(pre_holdout_sizes),
            "opportunities": len(opportunity_sizes),
        },
    )
    created_paths: list[Path] = []
    try:
        target_parts = _write_parts(
            output=output,
            source_id=source.source_id,
            kind="targets",
            rows=(item.as_json() for item in source.targets),
            expected_sizes=target_sizes,
            created_paths=created_paths,
        )
        pre_holdout_target_parts = _write_parts(
            output=output,
            source_id=source.source_id,
            kind="pre-holdout-target",
            rows=(item.as_json() for item in source.pre_holdout_target_dataset.rows),
            expected_sizes=pre_holdout_sizes,
            created_paths=created_paths,
        )
        opportunity_parts = _write_parts(
            output=output,
            source_id=source.source_id,
            kind="opportunities",
            rows=(item.as_json() for item in source.opportunities),
            expected_sizes=opportunity_sizes,
            created_paths=created_paths,
        )
        manifest = _manifest_payload(
            source,
            target_parts=target_parts,
            pre_holdout_target_parts=pre_holdout_target_parts,
            opportunity_parts=opportunity_parts,
        )
        atomic_create(output, canonical_bytes(manifest))
    except Exception:
        _cleanup_created_paths(created_paths)
        raise
    return manifest


def _part_rows(
    manifest_path: Path,
    *,
    source_id: str,
    kind: str,
    references: object,
) -> list[dict[str, JsonValue]]:
    if not isinstance(references, list):
        raise ValueError(f"holdout target source {kind} parts must be an array")
    rows: list[dict[str, JsonValue]] = []
    for expected_index, raw_reference in enumerate(references):
        if not isinstance(raw_reference, Mapping):
            raise ValueError(f"holdout target source {kind} part reference is invalid")
        reference = cast(Mapping[str, object], raw_reference)
        if set(reference) != {"path", "sha256", "row_count"}:
            raise ValueError(f"holdout target source {kind} part reference has unknown fields")
        relative = reference["path"]
        digest = reference["sha256"]
        row_count = reference["row_count"]
        if not isinstance(relative, str) or not isinstance(digest, str) or not isinstance(
            row_count, int
        ):
            raise ValueError(f"holdout target source {kind} part reference is malformed")
        part_path = _safe_part_path(manifest_path.parent, relative)
        _reject_symlink_ancestors(part_path)
        if part_path.is_symlink() or not part_path.is_file():
            raise ValueError(f"holdout target source {kind} part is unavailable")
        encoded = part_path.read_bytes()
        if len(encoded) > _MAX_PART_BYTES or hashlib.sha256(encoded).hexdigest() != digest:
            raise ValueError(f"holdout target source {kind} part digest or size differs")
        payload = _json_object_from_bytes(encoded, part_path, limit=_MAX_PART_BYTES)
        expected = {
            "contract",
            "schema_version",
            "source_id",
            "kind",
            "part_index",
            "rows",
        }
        if set(payload) != expected or payload["contract"] != _PART_CONTRACT:
            raise ValueError(f"holdout target source {kind} part contract is unsupported")
        if (
            payload["schema_version"] != _PART_SCHEMA_VERSION
            or payload["source_id"] != source_id
            or payload["kind"] != kind
            or payload["part_index"] != expected_index
        ):
            raise ValueError(f"holdout target source {kind} part lineage differs")
        raw_rows = payload["rows"]
        if not isinstance(raw_rows, list) or len(raw_rows) != row_count:
            raise ValueError(f"holdout target source {kind} part row count differs")
        rows.extend(cast(list[dict[str, JsonValue]], raw_rows))
    return rows


def load_r2_holdout_target_source(
    path: Path,
    *,
    _manifest_payload: Mapping[str, object] | None = None,
    _part_paths: frozenset[str] | None = None,
) -> R2HoldoutTargetSource:
    """Load a full or bounded-parts source and authenticate its semantic ID."""
    payload = (
        dict(_manifest_payload)
        if _manifest_payload is not None
        else _json_object(path, limit=_MAX_MANIFEST_BYTES)
    )
    if payload.get("storage") != _SOURCE_STORAGE:
        source = R2HoldoutTargetSource.from_json(payload)
        return source
    required = {
        "contract",
        "schema_version",
        "storage",
        "source_id",
        "source_target_dataset_id",
        "observation_dataset_id",
        "foundation_configuration_id",
        "causal_panel_dataset_id",
        "availability_evidence_id",
        "target_index_dataset_id",
        "causal_metadata_dataset_id",
        "holdout_range",
        "primary_horizon_seconds",
        "target_instruments",
        "pre_holdout_target_dataset_id",
        "pre_holdout_observation_dataset_id",
        "pre_holdout_foundation_configuration_id",
        "target_parts",
        "pre_holdout_target_parts",
        "opportunity_parts",
        "target_count",
        "pre_holdout_target_count",
        "opportunity_count",
        "closure_id",
    }
    if set(payload) != required:
        raise ValueError("holdout target source bounded manifest has unknown or missing fields")
    if payload["contract"] != R2HoldoutTargetSource.CONTRACT or payload["schema_version"] != 1:
        raise ValueError("holdout target source contract is unsupported")
    source_id = payload["source_id"]
    closure_id = payload["closure_id"]
    if not isinstance(source_id, str) or not isinstance(closure_id, str):
        raise ValueError("holdout target source bounded manifest IDs are malformed")
    if _part_paths is None:
        _part_paths = bounded_manifest_part_paths(path, payload=payload)
    if closure_id != bounded_source_closure_id(payload):
        raise ValueError(
            "holdout target source bounded manifest closure ID differs from its content"
        )
    targets = _part_rows(
        path, source_id=source_id, kind="targets", references=payload["target_parts"]
    )
    pre_rows = _part_rows(
        path,
        source_id=source_id,
        kind="pre-holdout-target",
        references=payload["pre_holdout_target_parts"],
    )
    opportunities = _part_rows(
        path, source_id=source_id, kind="opportunities", references=payload["opportunity_parts"]
    )
    if (
        len(targets) != payload["target_count"]
        or len(pre_rows) != payload["pre_holdout_target_count"]
        or len(opportunities) != payload["opportunity_count"]
    ):
        raise ValueError("holdout target source bounded manifest row counts differ")
    pre_dataset: dict[str, JsonValue] = {
        "contract": TARGET_DATASET_CONTRACT,
        "schema_version": 1,
        "dataset_id": _json_value(payload["pre_holdout_target_dataset_id"]),
        "observation_dataset_id": _json_value(payload["pre_holdout_observation_dataset_id"]),
        "foundation_configuration_id": _json_value(
            payload["pre_holdout_foundation_configuration_id"]
        ),
        "rows": _json_value(pre_rows),
    }
    reconstructed: dict[str, JsonValue] = {
        "contract": _json_value(payload["contract"]),
        "schema_version": _json_value(payload["schema_version"]),
        "source_target_dataset_id": _json_value(payload["source_target_dataset_id"]),
        "observation_dataset_id": _json_value(payload["observation_dataset_id"]),
        "foundation_configuration_id": _json_value(payload["foundation_configuration_id"]),
        "causal_panel_dataset_id": _json_value(payload["causal_panel_dataset_id"]),
        "availability_evidence_id": _json_value(payload["availability_evidence_id"]),
        "target_index_dataset_id": _json_value(payload["target_index_dataset_id"]),
        "causal_metadata_dataset_id": _json_value(payload["causal_metadata_dataset_id"]),
        "holdout_range": _json_value(payload["holdout_range"]),
        "primary_horizon_seconds": _json_value(payload["primary_horizon_seconds"]),
        "opportunity_derivation_policy": R2HoldoutTargetSource.OPPORTUNITY_DERIVATION_POLICY,
        "target_instruments": _json_value(payload["target_instruments"]),
        "targets": _json_value(targets),
        "pre_holdout_target_dataset": _json_value(pre_dataset),
        "opportunities": _json_value(opportunities),
        "source_id": source_id,
    }
    source = R2HoldoutTargetSource.from_json(reconstructed)
    if source.source_id != source_id:
        raise ValueError("holdout target source bounded manifest ID differs from its content")
    return source


def bounded_manifest_payload(path: Path) -> dict[str, object] | None:
    """Return the compact manifest when path uses bounded source storage."""
    payload = _json_object(path, limit=_MAX_MANIFEST_BYTES)
    if payload.get("storage") != _SOURCE_STORAGE:
        return None
    return payload


def load_r2_holdout_target_source_authority(
    path: Path,
) -> R2HoldoutTargetSourceAuthority:
    """Authenticate and retain one bounded source load for a stage handoff."""
    payload = bounded_manifest_payload(path)
    if payload is None:
        raise ValueError("holdout target source authority requires bounded source storage")
    source_id = payload.get("source_id")
    closure_id = payload.get("closure_id")
    if not isinstance(source_id, str) or not isinstance(closure_id, str):
        raise ValueError("holdout target source authority IDs are malformed")
    part_paths = bounded_manifest_part_paths(path, payload=payload)
    if closure_id != bounded_source_closure_id(payload):
        raise ValueError("holdout target source authority closure is not authenticated")
    source = load_r2_holdout_target_source(
        path, _manifest_payload=payload, _part_paths=part_paths
    )
    if source.source_id != source_id:
        raise ValueError("holdout target source authority semantic ID differs from its manifest")
    return R2HoldoutTargetSourceAuthority._create(
        _SOURCE_AUTHORITY_TOKEN,
        source=source,
        manifest_path=path,
        closure_id=closure_id,
        part_paths=part_paths,
    )


_BOUND_PART_FIELDS = (
    ("targets", "target_parts"),
    ("pre-holdout-target", "pre_holdout_target_parts"),
    ("opportunities", "opportunity_parts"),
)

_BOUNDED_MANIFEST_FIELDS = frozenset(
    {
        "contract",
        "schema_version",
        "storage",
        "source_id",
        "source_target_dataset_id",
        "observation_dataset_id",
        "foundation_configuration_id",
        "causal_panel_dataset_id",
        "availability_evidence_id",
        "target_index_dataset_id",
        "causal_metadata_dataset_id",
        "holdout_range",
        "primary_horizon_seconds",
        "target_instruments",
        "pre_holdout_target_dataset_id",
        "pre_holdout_observation_dataset_id",
        "pre_holdout_foundation_configuration_id",
        "target_parts",
        "pre_holdout_target_parts",
        "opportunity_parts",
        "target_count",
        "pre_holdout_target_count",
        "opportunity_count",
        "closure_id",
    }
)


def bounded_manifest_part_paths(
    path: Path, *, payload: Mapping[str, object] | None = None
) -> frozenset[str]:
    """Validate the bounded source tree and return its declared child paths."""
    if payload is None:
        payload = bounded_manifest_payload(path)
    if payload is None:
        return frozenset()
    if (
        set(payload) != _BOUNDED_MANIFEST_FIELDS
        or payload.get("contract") != R2HoldoutTargetSource.CONTRACT
        or payload.get("schema_version") != 1
        or payload.get("storage") != _SOURCE_STORAGE
    ):
        raise ValueError("holdout target source bounded manifest fields are unsupported")
    declared: set[str] = set()
    for kind, field_name in _BOUND_PART_FIELDS:
        references = payload.get(field_name)
        if not isinstance(references, list):
            raise ValueError(f"holdout target source {field_name} must be an array")
        for expected_index, raw_reference in enumerate(references):
            if not isinstance(raw_reference, Mapping):
                raise ValueError(f"holdout target source {field_name} reference is invalid")
            if set(raw_reference) != {"path", "sha256", "row_count"}:
                raise ValueError(f"holdout target source {field_name} reference has unknown fields")
            relative = raw_reference.get("path")
            expected_relative = f"{path.name}.parts/{kind}/part-{expected_index:06d}.json"
            if relative != expected_relative:
                raise ValueError(f"holdout target source {field_name} path is not canonical")
            _safe_part_path(path.parent, expected_relative)
            if expected_relative in declared:
                raise ValueError("holdout target source manifest declares a duplicate part")
            declared.add(expected_relative)
    parts_root = path.parent / f"{path.name}.parts"
    _reject_symlink_ancestors(parts_root)
    if declared and (parts_root.is_symlink() or not parts_root.is_dir()):
        raise ValueError("holdout target source parts directory is missing")
    if not declared and (parts_root.is_symlink() or parts_root.exists()):
        raise ValueError("holdout target source has an unexpected parts directory")
    if parts_root.is_dir():
        for candidate in parts_root.rglob("*"):
            if candidate.is_symlink():
                raise ValueError("holdout target source parts tree contains a symlink")
            relative = candidate.relative_to(path.parent).as_posix()
            if candidate.is_file():
                if relative not in declared:
                    raise ValueError(f"holdout target source has an undeclared part: {relative}")
            elif candidate.is_dir():
                if not any(item.startswith(relative + "/") for item in declared):
                    raise ValueError(
                        f"holdout target source has an orphaned parts directory: {relative}"
                    )
            else:
                raise ValueError(
                    f"holdout target source parts tree contains a non-regular entry: {relative}"
                )
    return frozenset(declared)
