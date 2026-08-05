"""Immutable runtime persistence and replay for the IBKR historical foundation."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import cast

import polars as pl

from qtrad.application.ibkr_foundation import IBKRFoundationBuild, build_ibkr_foundation
from qtrad.application.provider_history import ProviderHistorySourceEvidence
from qtrad.domain.events import JsonValue, to_json_value
from qtrad.domain.foundation import FoundationConfig
from qtrad.domain.ibkr_foundation import (
    IBKR_FOUNDATION_CONTRACT,
    IBKR_FOUNDATION_SCHEMA_VERSION,
)
from qtrad.runtime.foundation_bundle import decode_foundation_config
from qtrad.runtime.provider_history import read_provider_history_source_evidence

_FOUNDATION_CHILD_CONTRACT = "qtrad-ibkr-historical-foundation-child-v1"
_FOUNDATION_CHILD_SCHEMA_VERSION = 1
_MAX_MANIFEST_BYTES = 4 * 1024 * 1024
_MAX_CHILD_MANIFEST_BYTES = 4 * 1024 * 1024
_MAX_CHILD_FILE_BYTES = 64 * 1024 * 1024
_MAX_CHILD_ROWS = 100_000
_MAX_CHILD_PARTS = 20_000
_CHILD_DIRECTORY_SUFFIX = ".children"
_CHILD_KINDS = ("observations", "panel", "targets", "folds")
_CHILD_FIELDS = {
    "contract",
    "schema_version",
    "kind",
    "dataset_id",
    "part_index",
    "row_count",
    "file",
    "file_sha256",
    "rows_sha256",
    "lineage",
    "manifest_sha256",
}
_REFERENCE_FIELDS = {
    "kind",
    "dataset_id",
    "manifest_id",
    "manifest_path",
    "manifest_sha256",
    "row_count",
    "file",
    "file_sha256",
}


def foundation_config_payload(configuration: FoundationConfig) -> dict[str, JsonValue]:
    """Encode the strict configuration child used by the source-specific bundle."""

    return {
        "contract": FoundationConfig.CONTRACT,
        "name": configuration.name,
        "schema_version": configuration.schema_version,
        "observation_dataset_id": configuration.observation_dataset_id,
        "ordered_instruments": list(configuration.ordered_instruments),
        "instrument_roles": {
            key: value.value for key, value in sorted(configuration.instrument_roles.items())
        },
        "range_start": configuration.range_start.isoformat(),
        "range_end": configuration.range_end.isoformat(),
        "grid_resolution_seconds": int(configuration.grid_resolution.total_seconds()),
        "availability_basis": configuration.availability_basis.value,
        "feature_lag_policy": configuration.feature_lag_policy,
        "feature_lag_calibration_range": [
            value.isoformat() for value in configuration.feature_lag_calibration_range
        ],
        "feature_lag_percentile": configuration.feature_lag_percentile,
        "feature_lag_safety_margin_seconds": int(
            configuration.feature_lag_safety_margin.total_seconds()
        ),
        "selected_feature_lag_seconds": int(configuration.selected_feature_lag.total_seconds()),
        "target_horizons_seconds": [
            int(value.total_seconds()) for value in configuration.target_horizons
        ],
        "primary_vertical_horizon_seconds": int(
            configuration.primary_vertical_horizon.total_seconds()
        ),
        "target_revision_delay_seconds": int(configuration.target_revision_delay.total_seconds()),
        "target_revision_policy": configuration.target_revision_policy,
        "target_revision_policy_reason": configuration.target_revision_policy_reason,
        "required_feature_bases": [value.value for value in configuration.required_feature_bases],
        "target_basis": configuration.target_basis.value,
        "fold_policy": configuration.fold_policy,
        "holdout_range": [value.isoformat() for value in configuration.holdout_range],
        "embargo_seconds": int(configuration.embargo.total_seconds()),
        "minimum_training_duration_seconds": int(
            configuration.minimum_training_duration.total_seconds()
        ),
        "minimum_validation_duration_seconds": int(
            configuration.minimum_validation_duration.total_seconds()
        ),
    }


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        to_json_value(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _sha(value: object) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _build_payload(
    build: IBKRFoundationBuild,
    source_evidence: ProviderHistorySourceEvidence,
    children: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    source = source_evidence.source_artifact
    provider_dataset = build.provider_history
    return {
        "configuration": foundation_config_payload(build.configuration),
        "provider_history": {
            "dataset_sha256": provider_dataset.dataset_sha256,
            "row_count": provider_dataset.row_count,
            "contract_selection_sha256": provider_dataset.contract_selection_sha256,
            "plan_sha256": provider_dataset.plan_sha256,
            "runtime_sha256": provider_dataset.runtime_sha256,
            "aggregate_sha256": provider_dataset.aggregate_sha256,
        },
        "source_evidence": {
            "eligible_contracts": [
                contract.as_json_value() for contract in source.plan.eligible_contracts
            ],
            "coverage_summary": source.aggregate.coverage_summary,
            "entitlement_summary": source.aggregate.entitlement_summary,
        },
        "children": dict(children),
        "active_intervals": {
            instrument: [[start.isoformat(), end.isoformat()] for start, end in intervals]
            for instrument, intervals in sorted(build.active_intervals.items())
        },
        "provider_gaps": [dict(gap) for gap in build.provider_gaps],
        "readiness": build.readiness.as_json(),
    }


def _manifest_payload(
    build: IBKRFoundationBuild,
    source_evidence: ProviderHistorySourceEvidence,
    children: Mapping[str, JsonValue],
    provider_manifest: Path,
    bundle_root: Path,
) -> dict[str, JsonValue]:
    payload = _build_payload(build, source_evidence, children)
    provider_path = _relative_path(bundle_root, provider_manifest, "provider-history manifest")
    return {
        "contract": IBKR_FOUNDATION_CONTRACT,
        "schema_version": IBKR_FOUNDATION_SCHEMA_VERSION,
        "source_class": "IBKR_HISTORICAL_RESEARCH",
        "provider_history_manifest": provider_path,
        "provider_history_sha256": hashlib.sha256(provider_manifest.read_bytes()).hexdigest(),
        "build_sha256": _sha(payload),
        "payload": payload,
    }


def write_ibkr_foundation(
    output: Path,
    *,
    provider_manifest: Path,
    configuration: FoundationConfig,
) -> IBKRFoundationBuild:
    """Build and create the source-specific bundle once."""

    output = _output_path(output)
    if output.exists():
        raise FileExistsError(f"IBKR foundation output already exists: {output}")
    provider_manifest = _regular_file(provider_manifest, "provider-history manifest")
    _relative_path(output.parent, provider_manifest, "provider-history manifest")
    child_root = output.parent / f"{output.name}{_CHILD_DIRECTORY_SUFFIX}"
    if child_root.exists():
        raise FileExistsError(f"IBKR foundation child directory already exists: {child_root}")

    source_evidence = read_provider_history_source_evidence(provider_manifest)
    build = build_ibkr_foundation(source_evidence, configuration)
    if build.provider_history.dataset_sha256 != source_evidence.dataset.dataset_sha256:
        raise ValueError("provider history changed during foundation construction")

    child_root_created = False
    output_created = False
    try:
        child_root.mkdir()
        child_root_created = True
        children = _write_children(
            child_root,
            output.parent,
            build,
            source_evidence,
            provider_manifest,
        )
        document = _manifest_payload(
            build,
            source_evidence,
            children,
            provider_manifest,
            output.parent,
        )
        encoded = _json_bytes(document) + b"\n"
        if len(encoded) > _MAX_MANIFEST_BYTES:
            raise ValueError("IBKR foundation manifest exceeds the 4 MiB limit")
        with output.open("xb") as handle:
            output_created = True
            handle.write(encoded)
    except BaseException:
        if child_root_created:
            shutil.rmtree(child_root)
        if output_created:
            output.unlink()
        raise
    return build


def verify_ibkr_foundation(path: Path) -> IBKRFoundationBuild:
    """Verify the thin manifest and independently replay every Parquet child."""

    manifest_path = _regular_file(path, "IBKR foundation manifest")
    manifest_bytes = _bounded_bytes(manifest_path, _MAX_MANIFEST_BYTES, "IBKR foundation manifest")
    document = _mapping(_parse_json(manifest_bytes, "IBKR foundation manifest"))
    if set(document) != {
        "contract",
        "schema_version",
        "source_class",
        "provider_history_manifest",
        "provider_history_sha256",
        "build_sha256",
        "payload",
    }:
        raise ValueError("IBKR foundation bundle has unknown or missing fields")
    if document["contract"] != IBKR_FOUNDATION_CONTRACT:
        raise ValueError("IBKR foundation bundle contract is unsupported")
    if document["schema_version"] != IBKR_FOUNDATION_SCHEMA_VERSION:
        raise ValueError("IBKR foundation bundle schema is unsupported")
    if document["source_class"] != "IBKR_HISTORICAL_RESEARCH":
        raise ValueError("IBKR foundation bundle source class is unsupported")
    if manifest_bytes != _json_bytes(document) + b"\n":
        raise ValueError("IBKR foundation manifest bytes are not canonical")

    root = manifest_path.parent
    provider_path = _safe_child(
        root,
        _text(document["provider_history_manifest"], "provider-history manifest path"),
        "provider-history manifest",
    )
    provider_bytes = _bounded_bytes(
        provider_path,
        _MAX_MANIFEST_BYTES,
        "provider-history manifest",
    )
    provider_manifest_sha256 = hashlib.sha256(provider_bytes).hexdigest()
    if provider_manifest_sha256 != _text(
        document["provider_history_sha256"],
        "provider-history manifest hash",
    ):
        raise ValueError("provider-history manifest bytes changed")

    payload = _mapping(document["payload"], "IBKR foundation payload")
    if _sha(payload) != _text(document["build_sha256"], "IBKR foundation build hash"):
        raise ValueError("IBKR foundation payload identity does not match")
    configuration_payload = _mapping(payload["configuration"], "IBKR foundation configuration")
    configuration = decode_foundation_config(configuration_payload)
    source_evidence = read_provider_history_source_evidence(provider_path)
    replay = build_ibkr_foundation(source_evidence, configuration)

    children = cast(dict[str, JsonValue], _mapping(payload["children"], "IBKR foundation children"))
    expected_payload = _build_payload(replay, source_evidence, children)
    if expected_payload != payload:
        raise ValueError("IBKR foundation metadata differs from independent replay")
    expected_rows = _child_rows(replay)
    expected_dataset_ids = _child_dataset_ids(replay)
    expected_lineage = _child_lineage(
        replay,
        source_evidence,
        provider_manifest_sha256,
    )
    _verify_children(
        root,
        children,
        expected_rows,
        expected_dataset_ids,
        expected_lineage,
    )

    if replay.provider_history.dataset_sha256 != source_evidence.dataset.dataset_sha256:
        raise ValueError("IBKR foundation source dataset differs from provider history")
    return replay


def load_ibkr_foundation(path: Path) -> IBKRFoundationBuild:
    """Load only after complete independent verification."""

    return verify_ibkr_foundation(path)


def _child_lineage(
    build: IBKRFoundationBuild,
    source_evidence: ProviderHistorySourceEvidence,
    provider_manifest_sha256: str,
) -> dict[str, JsonValue]:
    return {
        "provider_manifest_sha256": provider_manifest_sha256,
        "provider_dataset_sha256": build.provider_history.dataset_sha256,
        "plan_sha256": source_evidence.source_artifact.plan.plan_sha256,
        "aggregate_sha256": source_evidence.source_artifact.aggregate.aggregate_sha256,
    }


def _write_children(
    child_root: Path,
    bundle_root: Path,
    build: IBKRFoundationBuild,
    source_evidence: ProviderHistorySourceEvidence,
    provider_manifest: Path,
) -> dict[str, JsonValue]:
    provider_manifest_sha256 = hashlib.sha256(provider_manifest.read_bytes()).hexdigest()
    lineage = _child_lineage(build, source_evidence, provider_manifest_sha256)
    rows = _child_rows(build)
    dataset_ids = _child_dataset_ids(build)
    children: dict[str, JsonValue] = {}
    for kind in _CHILD_KINDS:
        children[kind] = _write_child_parts(
            child_root,
            bundle_root,
            kind,
            rows[kind],
            dataset_ids[kind],
            lineage,
        )
    return children


def _write_child_parts(
    child_root: Path,
    bundle_root: Path,
    kind: str,
    rows: tuple[dict[str, JsonValue], ...],
    dataset_id: str,
    lineage: Mapping[str, JsonValue],
) -> list[JsonValue]:
    if kind not in _CHILD_KINDS:
        raise ValueError(f"unsupported IBKR foundation child kind: {kind}")
    parts: list[JsonValue] = []
    chunks = (
        tuple(rows[index : index + _MAX_CHILD_ROWS])
        for index in range(0, len(rows), _MAX_CHILD_ROWS)
    )
    if not rows:
        chunks = iter(((),))
    for part_index, chunk in enumerate(chunks):
        payloads = tuple(_canonical_row(row) for row in chunk)
        parquet_bytes = _parquet_bytes(payloads)
        if not parquet_bytes or len(parquet_bytes) > _MAX_CHILD_FILE_BYTES:
            raise ValueError("IBKR foundation Parquet child exceeds its byte bound")
        relative_file = (
            f"{child_root.name}/parquet/{kind}/"
            f"part-{part_index:06d}-{hashlib.sha256(parquet_bytes).hexdigest()[:24]}.parquet"
        )
        file_path = bundle_root / PurePosixPath(relative_file)
        _write_create_only(file_path, parquet_bytes)
        identity: dict[str, JsonValue] = {
            "contract": _FOUNDATION_CHILD_CONTRACT,
            "schema_version": _FOUNDATION_CHILD_SCHEMA_VERSION,
            "kind": kind,
            "dataset_id": dataset_id,
            "part_index": part_index,
            "row_count": len(chunk),
            "file": relative_file,
            "file_sha256": hashlib.sha256(parquet_bytes).hexdigest(),
            "rows_sha256": _sha(list(payloads)),
            "lineage": dict(lineage),
        }
        manifest_sha256 = _sha(identity)
        manifest: dict[str, JsonValue] = {
            **identity,
            "manifest_sha256": manifest_sha256,
        }
        relative_manifest = (
            f"{child_root.name}/manifests/{kind}/part-{part_index:06d}-{manifest_sha256[:24]}.json"
        )
        manifest_path = bundle_root / PurePosixPath(relative_manifest)
        encoded_manifest = _json_bytes(manifest) + b"\n"
        if len(encoded_manifest) > _MAX_CHILD_MANIFEST_BYTES:
            raise ValueError("IBKR foundation child manifest exceeds the 4 MiB limit")
        _write_create_only(manifest_path, encoded_manifest)
        parts.append(
            {
                "kind": kind,
                "dataset_id": dataset_id,
                "manifest_id": manifest_sha256[:24],
                "manifest_path": relative_manifest,
                "manifest_sha256": manifest_sha256,
                "row_count": len(chunk),
                "file": relative_file,
                "file_sha256": hashlib.sha256(parquet_bytes).hexdigest(),
            }
        )
    if len(parts) > _MAX_CHILD_PARTS:
        raise ValueError("IBKR foundation child part count exceeds its bound")
    return parts


def _verify_children(
    bundle_root: Path,
    children: Mapping[str, object],
    expected_rows: Mapping[str, tuple[dict[str, JsonValue], ...]],
    expected_dataset_ids: Mapping[str, str],
    expected_lineage: Mapping[str, JsonValue],
) -> None:
    if set(children) != set(_CHILD_KINDS):
        raise ValueError("IBKR foundation child set is incomplete or duplicated")
    expected_files: set[str] = set()
    child_root_names: set[str] = set()
    for kind in _CHILD_KINDS:
        raw_parts = children[kind]
        if not isinstance(raw_parts, list) or not raw_parts:
            raise ValueError("IBKR foundation child parts are invalid")
        if len(raw_parts) > _MAX_CHILD_PARTS:
            raise ValueError("IBKR foundation child part count exceeds its bound")
        observed_rows: list[dict[str, JsonValue]] = []
        previous_path = ""
        for part_index, raw_part in enumerate(raw_parts):
            reference = _child_reference(raw_part, kind)
            manifest_reference = _text(
                reference["manifest_path"],
                "child manifest path",
            )
            if manifest_reference <= previous_path:
                raise ValueError("IBKR foundation child references are not canonical")
            previous_path = manifest_reference
            child_root_names.add(PurePosixPath(manifest_reference).parts[0])
            manifest_path = _safe_child(
                bundle_root,
                manifest_reference,
                "IBKR foundation child manifest",
            )
            manifest_bytes = _bounded_bytes(
                manifest_path,
                _MAX_CHILD_MANIFEST_BYTES,
                "IBKR foundation child manifest",
            )
            manifest = _mapping(_parse_json(manifest_bytes, "IBKR foundation child manifest"))
            if set(manifest) != _CHILD_FIELDS:
                raise ValueError("IBKR foundation child manifest has unknown or missing fields")
            if manifest_bytes != _json_bytes(manifest) + b"\n":
                raise ValueError("IBKR foundation child manifest bytes are not canonical")
            if manifest["contract"] != _FOUNDATION_CHILD_CONTRACT:
                raise ValueError("IBKR foundation child contract is unsupported")
            if manifest["schema_version"] != _FOUNDATION_CHILD_SCHEMA_VERSION:
                raise ValueError("IBKR foundation child schema is unsupported")
            identity = dict(manifest)
            manifest_hash = _text(identity.pop("manifest_sha256"), "child manifest hash")
            if manifest_hash != _sha(identity):
                raise ValueError("IBKR foundation child manifest identity does not match")
            if manifest_hash != _text(
                reference["manifest_sha256"],
                "child manifest hash",
            ):
                raise ValueError("IBKR foundation child manifest hash differs from its reference")
            manifest_kind = _text(manifest["kind"], "child kind")
            if manifest_kind != kind:
                raise ValueError("IBKR foundation child kind differs from its reference")
            manifest_dataset_id = _text(manifest["dataset_id"], "child dataset ID")
            if manifest_dataset_id != expected_dataset_ids[kind]:
                raise ValueError("IBKR foundation child dataset differs from replay")
            manifest_part_index = _int(manifest["part_index"], "child part index")
            if manifest_part_index != part_index:
                raise ValueError("IBKR foundation child part index is not contiguous")
            manifest_row_count = _int(manifest["row_count"], "child row count")
            manifest_file = _text(manifest["file"], "child Parquet path")
            manifest_file_sha256 = _text(manifest["file_sha256"], "child Parquet hash")
            manifest_lineage = _mapping(manifest["lineage"], "child lineage")
            if manifest_lineage != dict(expected_lineage):
                raise ValueError("IBKR foundation child lineage differs from replay")
            expected_reference: dict[str, object] = {
                "kind": manifest_kind,
                "dataset_id": manifest_dataset_id,
                "manifest_id": manifest_hash[:24],
                "manifest_path": manifest_reference,
                "manifest_sha256": manifest_hash,
                "row_count": manifest_row_count,
                "file": manifest_file,
                "file_sha256": manifest_file_sha256,
            }
            if reference != expected_reference:
                raise ValueError("IBKR foundation child reference differs from its manifest")
            file_path = _safe_child(
                bundle_root,
                manifest_file,
                "IBKR foundation child Parquet",
            )
            parquet_bytes = _bounded_bytes(
                file_path,
                _MAX_CHILD_FILE_BYTES,
                "IBKR foundation child Parquet",
            )
            file_hash = hashlib.sha256(parquet_bytes).hexdigest()
            if file_hash != manifest_file_sha256:
                raise ValueError("IBKR foundation child Parquet bytes changed")
            rows = _read_child_rows(
                file_path,
                expected_row_count=manifest_row_count,
            )
            if _sha([_canonical_row(row) for row in rows]) != _text(
                manifest["rows_sha256"],
                "child row hash",
            ):
                raise ValueError("IBKR foundation child row identity does not match")
            observed_rows.extend(rows)
            expected_files.update(
                {
                    manifest_reference,
                    manifest_file,
                }
            )
        if tuple(observed_rows) != expected_rows[kind]:
            raise ValueError(f"IBKR foundation {kind} differs from independent replay")

    if len(child_root_names) != 1:
        raise ValueError("IBKR foundation child references use multiple roots")
    child_root = bundle_root / next(iter(child_root_names))
    actual_files = {
        path.relative_to(bundle_root).as_posix() for path in child_root.rglob("*") if path.is_file()
    }
    if actual_files != expected_files:
        raise ValueError("IBKR foundation child closure contains unexpected files")


def _child_rows(build: IBKRFoundationBuild) -> dict[str, tuple[dict[str, JsonValue], ...]]:
    return {
        "observations": tuple(row.as_json() for row in build.observations.rows),
        "panel": tuple(row.as_json() for row in build.panel.rows),
        "targets": tuple(row.as_json() for row in build.targets.rows),
        "folds": tuple(row.as_json() for row in build.folds.folds),
    }


def _child_dataset_ids(build: IBKRFoundationBuild) -> dict[str, str]:
    return {
        "observations": build.observations.dataset_id,
        "panel": build.panel.dataset_id,
        "targets": build.targets.dataset_id,
        "folds": build.folds.dataset_id,
    }


def _canonical_row(row: Mapping[str, JsonValue]) -> str:
    return json.dumps(
        to_json_value(dict(row)),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _parquet_bytes(payloads: Sequence[str]) -> bytes:
    buffer = io.BytesIO()
    pl.DataFrame(
        {"payload": list(payloads)},
        schema={"payload": pl.String},
    ).write_parquet(buffer)
    return buffer.getvalue()


def _read_child_rows(path: Path, *, expected_row_count: int) -> tuple[dict[str, JsonValue], ...]:
    if expected_row_count < 0 or expected_row_count > _MAX_CHILD_ROWS:
        raise ValueError("IBKR foundation child row count exceeds its bound")
    frame = pl.read_parquet(path)
    if frame.schema != {"payload": pl.String}:
        raise ValueError("IBKR foundation child Parquet schema is unsupported")
    values = frame.get_column("payload").to_list()
    if len(values) != expected_row_count:
        raise ValueError("IBKR foundation child row count differs from its manifest")
    rows: list[dict[str, JsonValue]] = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError("IBKR foundation child payload is not text")
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError("IBKR foundation child row is not an object")
        if value != _canonical_row(parsed):
            raise ValueError("IBKR foundation child row is not canonical")
        rows.append(cast(dict[str, JsonValue], parsed))
    return tuple(rows)


def _child_reference(value: object, kind: str) -> dict[str, object]:
    reference = _mapping(value, "IBKR foundation child reference")
    if set(reference) != _REFERENCE_FIELDS:
        raise ValueError("IBKR foundation child reference has an unexpected schema")
    if reference["kind"] != kind:
        raise ValueError("IBKR foundation child reference kind is invalid")
    manifest_hash = _text(reference["manifest_sha256"], "child manifest hash")
    manifest_id = _text(reference["manifest_id"], "child manifest ID")
    if manifest_id != manifest_hash[:24]:
        raise ValueError("IBKR foundation child manifest ID is invalid")
    for field in ("dataset_id", "file_sha256"):
        _require_sha256(_text(reference[field], field), field)
    _require_sha256(manifest_hash, "child manifest hash")
    row_count = _int(reference["row_count"], "child row count")
    if row_count < 0 or row_count > _MAX_CHILD_ROWS:
        raise ValueError("IBKR foundation child row count exceeds its bound")
    _safe_relative(_text(reference["manifest_path"], "child manifest path"))
    _safe_relative(_text(reference["file"], "child Parquet path"))
    return reference


def _regular_file(path: Path, field: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{field} must be a regular non-symlink file")
    return path.resolve()


def _output_path(path: Path) -> Path:
    current = path if path.is_absolute() else Path.cwd() / path
    if ".." in current.parts:
        raise ValueError(f"IBKR foundation output path escapes its root: {path}")
    for ancestor in (current, *current.parents):
        if ancestor.is_symlink():
            raise ValueError(f"IBKR foundation output path contains a symlink: {path}")
    return current


def _relative_path(root: Path, path: Path, field: str) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as error:
        raise ValueError(f"{field} must be within the foundation root") from error
    _safe_relative(relative)
    return relative


def _safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"unsafe relative foundation path: {value}")
    return path


def _safe_child(root: Path, relative: str, field: str) -> Path:
    _safe_relative(relative)
    child = root / PurePosixPath(relative)
    for ancestor in (child, *child.parents):
        if ancestor == root.parent:
            break
        if ancestor.is_symlink():
            raise ValueError(f"{field} path contains a symlink: {relative}")
    if not child.is_file():
        raise FileNotFoundError(f"{field} is not a regular file: {relative}")
    return child


def _write_create_only(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(payload)


def _bounded_bytes(path: Path, limit: int, field: str) -> bytes:
    data = path.read_bytes()
    if not data or len(data) > limit:
        raise ValueError(f"{field} exceeds its byte bound")
    return data


def _parse_json(payload: bytes, field: str) -> object:
    try:
        return json.loads(payload)
    except json.JSONDecodeError as error:
        raise ValueError(f"{field} is not valid JSON") from error


def _mapping(value: object, field: str = "object") -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{field} must be an object")
    return cast(dict[str, object], value)


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def _require_sha256(value: str, field: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} must be a lower-case SHA-256")


__all__ = [
    "foundation_config_payload",
    "load_ibkr_foundation",
    "verify_ibkr_foundation",
    "write_ibkr_foundation",
]
