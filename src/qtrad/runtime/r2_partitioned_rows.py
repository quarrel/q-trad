"""Bounded create-only JSON row persistence for large R2-owned datasets."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from contextlib import suppress
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import cast

from qtrad.runtime.r2_bundles import atomic_create, canonical_bytes

PARTITIONED_ROWS_STORAGE = "qtrad-r2-partitioned-json-rows-v1"
_PART_CONTRACT = "qtrad-r2-partitioned-json-row-part-v1"
_MAX_PART_BYTES = 64 * 1024 * 1024
_BUFFER_ROWS = 8192
_PART_FIELDS = {
    "contract",
    "schema_version",
    "parent_contract",
    "parent_semantic_id",
    "part_index",
    "rows",
}
_REF_FIELDS = {"path", "sha256", "row_count", "part_index"}


def write_partitioned_rows(
    output_root: Path,
    manifest_relative_path: str,
    *,
    header: Mapping[str, object],
    identity_field: str,
    rows: Iterable[Mapping[str, object]],
    expected_row_count: int,
) -> dict[str, object]:
    """Write bounded row parts and return their compact parent manifest payload."""

    contract = header.get("contract")
    semantic_id = header.get(identity_field)
    if not isinstance(contract, str) or not contract:
        raise ValueError("partitioned R2 rows require a parent contract")
    if not isinstance(semantic_id, str) or len(semantic_id) != 64:
        raise ValueError("partitioned R2 rows require a 64-hex semantic identity")
    if any(character not in "0123456789abcdef" for character in semantic_id):
        raise ValueError("partitioned R2 row semantic identity must be lowercase hexadecimal")
    if expected_row_count < 0:
        raise ValueError("partitioned R2 expected row count must be non-negative")
    if any(
        field in header for field in ("rows", "storage", "parts", "row_count", "identity_field")
    ):
        raise ValueError("partitioned R2 row header contains reserved physical fields")

    manifest_relative = _relative_manifest(manifest_relative_path)
    manifest_path = output_root / manifest_relative
    parts_root = output_root / f"{manifest_relative_path}.parts"
    _reject_symlink_ancestors(manifest_path)
    if manifest_path.exists() or manifest_path.is_symlink():
        raise FileExistsError(manifest_path)
    if parts_root.exists() or parts_root.is_symlink():
        raise FileExistsError(parts_root)

    created: list[Path] = []
    references: list[dict[str, object]] = []
    buffer: list[Mapping[str, object]] = []
    row_count = 0
    try:
        for row in rows:
            if not isinstance(row, Mapping):
                raise TypeError("partitioned R2 row must be an object")
            buffer.append(dict(row))
            row_count += 1
            if len(buffer) == _BUFFER_ROWS:
                _write_bounded_buffer(
                    output_root,
                    manifest_relative_path,
                    contract=contract,
                    semantic_id=semantic_id,
                    rows=tuple(buffer),
                    references=references,
                    created=created,
                )
                buffer.clear()
        if buffer:
            _write_bounded_buffer(
                output_root,
                manifest_relative_path,
                contract=contract,
                semantic_id=semantic_id,
                rows=tuple(buffer),
                references=references,
                created=created,
            )
        if row_count != expected_row_count:
            raise ValueError(
                "partitioned R2 row count differs from its declared dataset: "
                f"{row_count} != {expected_row_count}"
            )
        return {
            **dict(header),
            "storage": PARTITIONED_ROWS_STORAGE,
            "identity_field": identity_field,
            "row_count": row_count,
            "parts": references,
        }
    except BaseException:
        _cleanup(created)
        raise


def partitioned_manifest_part_paths(
    root: Path,
    manifest_relative_path: str,
    payload: Mapping[str, object],
) -> tuple[str, ...]:
    """Validate a compact parent manifest and its exact canonical declared part paths."""

    contract, semantic_id, references = _manifest_values(payload)
    expected_root = f"{manifest_relative_path}.parts"
    result: list[str] = []
    total_rows = 0
    for expected_index, raw_reference in enumerate(references):
        reference = _object(raw_reference, "partitioned R2 part reference")
        if set(reference) != _REF_FIELDS:
            raise ValueError("partitioned R2 part reference has unknown or missing fields")
        relative = reference["path"]
        digest = reference["sha256"]
        row_count = reference["row_count"]
        part_index = reference["part_index"]
        expected_path = f"{expected_root}/part-{expected_index:06d}.json"
        if relative != expected_path or part_index != expected_index:
            raise ValueError("partitioned R2 part path or index is not canonical")
        _sha256_text(digest, "partitioned R2 part digest")
        if type(row_count) is not int or cast(int, row_count) <= 0:
            raise ValueError("partitioned R2 part row count must be a positive integer")
        part_path = _safe_child(root, cast(str, relative))
        if part_path.stat().st_size > _MAX_PART_BYTES:
            raise ValueError(f"partitioned R2 part exceeds the 64 MiB limit: {relative}")
        result.append(cast(str, relative))
        total_rows += cast(int, row_count)
    declared_count = payload.get("row_count")
    if type(declared_count) is not int or cast(int, declared_count) < 0:
        raise ValueError("partitioned R2 manifest row count must be a non-negative integer")
    if total_rows != declared_count:
        raise ValueError("partitioned R2 manifest row count differs from its parts")
    if declared_count and not references:
        raise ValueError("partitioned R2 manifest has rows but no parts")
    if not declared_count and references:
        raise ValueError("partitioned R2 empty manifest must not declare parts")
    _reject_part_orphans(root, expected_root, set(result))
    del contract, semantic_id
    return tuple(result)


def load_partitioned_rows(
    root: Path,
    manifest_relative_path: str,
    payload: Mapping[str, object],
) -> tuple[dict[str, object], ...]:
    """Consume each declared part exactly once and return its canonical ordered rows."""

    contract, semantic_id, references = _manifest_values(payload)
    paths = partitioned_manifest_part_paths(root, manifest_relative_path, payload)
    rows: list[dict[str, object]] = []
    for expected_index, (relative, raw_reference) in enumerate(zip(paths, references, strict=True)):
        reference = _object(raw_reference, "partitioned R2 part reference")
        path = _safe_child(root, relative)
        encoded = path.read_bytes()
        if sha256(encoded).hexdigest() != reference["sha256"]:
            raise ValueError(f"partitioned R2 part digest mismatch: {relative}")
        value = _json_object(encoded, relative)
        if set(value) != _PART_FIELDS:
            raise ValueError("partitioned R2 part has unknown or missing fields")
        if (
            value["contract"] != _PART_CONTRACT
            or value["schema_version"] != 1
            or value["parent_contract"] != contract
            or value["parent_semantic_id"] != semantic_id
            or value["part_index"] != expected_index
        ):
            raise ValueError("partitioned R2 part lineage differs from its manifest")
        raw_rows = value["rows"]
        if not isinstance(raw_rows, list) or len(raw_rows) != reference["row_count"]:
            raise ValueError("partitioned R2 part rows differ from its reference")
        for raw_row in raw_rows:
            rows.append(_object(raw_row, "partitioned R2 row"))
    if len(rows) != payload["row_count"]:
        raise ValueError("partitioned R2 consumed row count differs from its manifest")
    return tuple(rows)


def _write_bounded_buffer(
    output_root: Path,
    manifest_relative_path: str,
    *,
    contract: str,
    semantic_id: str,
    rows: tuple[Mapping[str, object], ...],
    references: list[dict[str, object]],
    created: list[Path],
) -> None:
    encoded = _part_bytes(
        contract=contract,
        semantic_id=semantic_id,
        part_index=len(references),
        rows=rows,
    )
    if len(encoded) > _MAX_PART_BYTES:
        if len(rows) == 1:
            raise ValueError(
                "partitioned R2 single-row part exceeds the 64 MiB limit: "
                f"{len(encoded)} > {_MAX_PART_BYTES}"
            )
        midpoint = len(rows) // 2
        _write_bounded_buffer(
            output_root,
            manifest_relative_path,
            contract=contract,
            semantic_id=semantic_id,
            rows=rows[:midpoint],
            references=references,
            created=created,
        )
        _write_bounded_buffer(
            output_root,
            manifest_relative_path,
            contract=contract,
            semantic_id=semantic_id,
            rows=rows[midpoint:],
            references=references,
            created=created,
        )
        return
    index = len(references)
    relative = f"{manifest_relative_path}.parts/part-{index:06d}.json"
    path = _safe_child(output_root, relative, require_file=False)
    atomic_create(path, encoded)
    created.append(path)
    references.append(
        {
            "path": relative,
            "sha256": sha256(encoded).hexdigest(),
            "row_count": len(rows),
            "part_index": index,
        }
    )


def _part_bytes(
    *,
    contract: str,
    semantic_id: str,
    part_index: int,
    rows: Sequence[Mapping[str, object]],
) -> bytes:
    return canonical_bytes(
        {
            "contract": _PART_CONTRACT,
            "schema_version": 1,
            "parent_contract": contract,
            "parent_semantic_id": semantic_id,
            "part_index": part_index,
            "rows": [dict(row) for row in rows],
        }
    )


def _manifest_values(
    payload: Mapping[str, object],
) -> tuple[str, str, list[object]]:
    if payload.get("storage") != PARTITIONED_ROWS_STORAGE:
        raise ValueError("R2 row manifest has an unsupported storage contract")
    contract = payload.get("contract")
    if not isinstance(contract, str) or not contract:
        raise ValueError("partitioned R2 manifest has no parent contract")
    identity_field = payload.get("identity_field")
    if not isinstance(identity_field, str) or not identity_field.endswith("_id"):
        raise ValueError("partitioned R2 manifest identity field is invalid")
    semantic_id = payload.get(identity_field)
    _sha256_text(semantic_id, "partitioned R2 manifest semantic identity")
    parts = payload.get("parts")
    if not isinstance(parts, list):
        raise ValueError("partitioned R2 manifest parts must be an array")
    return contract, cast(str, semantic_id), cast(list[object], parts)


def _relative_manifest(value: str) -> PurePosixPath:
    relative = _relative_json_path(value)
    return relative


def _relative_json_path(value: str) -> PurePosixPath:
    relative = PurePosixPath(value)
    if (
        not value
        or relative.is_absolute()
        or relative.suffix != ".json"
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError("partitioned R2 JSON path is unsafe")
    return relative


def _safe_child(root: Path, relative: str, *, require_file: bool = True) -> Path:
    _relative_json_path(relative)
    candidate = root / relative
    current = root
    for part in PurePosixPath(relative).parts:
        current /= part
        if current.is_symlink():
            raise ValueError(f"partitioned R2 path traverses a symlink: {relative}")
    if require_file and not candidate.is_file():
        raise ValueError(f"partitioned R2 part is missing or not regular: {relative}")
    return candidate


def _reject_part_orphans(root: Path, relative_root: str, allowed: set[str]) -> None:
    part_root = root / relative_root
    if not part_root.is_dir() or part_root.is_symlink():
        if allowed:
            raise ValueError("partitioned R2 parts root is missing or unsafe")
        return
    for candidate in part_root.rglob("*"):
        relative = candidate.relative_to(root).as_posix()
        if candidate.is_symlink():
            raise ValueError(f"partitioned R2 parts contain a symlink: {relative}")
        if candidate.is_dir():
            continue
        if not candidate.is_file():
            raise ValueError(f"partitioned R2 parts contain a special entry: {relative}")
        if relative not in allowed:
            raise ValueError(f"partitioned R2 parts contain an orphan: {relative}")


def _reject_symlink_ancestors(path: Path) -> None:
    current = path.parent
    while True:
        if current.is_symlink():
            raise ValueError(f"partitioned R2 output traverses a symlink: {current}")
        if current == current.parent:
            return
        current = current.parent


def _json_object(encoded: bytes, field: str) -> dict[str, object]:
    try:
        value = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{field} is not valid JSON") from error
    if not isinstance(value, dict) or canonical_bytes(cast(dict[str, object], value)) != encoded:
        raise ValueError(f"{field} is not a canonical JSON object")
    return cast(dict[str, object], value)


def _object(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{field} must be an object")
    return cast(dict[str, object], value)


def _sha256_text(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{field} must be lowercase SHA-256")
    return value


def _cleanup(paths: Sequence[Path]) -> None:
    for path in reversed(paths):
        path.unlink(missing_ok=True)
    parents = sorted(
        {path.parent for path in paths},
        key=lambda item: len(item.parts),
        reverse=True,
    )
    for parent in parents:
        with suppress(OSError):
            parent.rmdir()
