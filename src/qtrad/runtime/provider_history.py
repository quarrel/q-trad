"""Create-only provider-history publication and file-only verification."""

from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import polars as pl

from qtrad.application.provider_history import (
    build_provider_history_dataset,
    replay_provider_history_dataset,
)
from qtrad.domain.events import JsonValue, to_json_value
from qtrad.domain.ibkr_results import (
    HISTORICAL_RESULT_CONTRACT,
    IbkrHistoricalResultArtifact,
    canonical_json_bytes,
    sha256_bytes,
)
from qtrad.domain.provider_history import (
    PROVIDER_HISTORICAL_OBSERVATIONS_CONTRACT,
    PROVIDER_HISTORY_AVAILABILITY_SELECTOR_CONTRACT,
    PROVIDER_HISTORY_ENVIRONMENT,
    PROVIDER_HISTORY_PROVIDER,
    PROVIDER_HISTORY_SCHEMA_VERSION,
    PROVIDER_HISTORY_SOURCE_CLASS,
    ProviderHistoricalAvailabilityPolicy,
    ProviderHistoricalDataset,
    ProviderHistoricalObservation,
    sha256_json,
)
from qtrad.runtime.ibkr_results import verify_ibkr_historical_result

_MANIFEST_NAME = "manifest.json"
_OBSERVATIONS_PATH = "observations/observations.parquet"
_SOURCE_DIRECTORY = "source-result"
_SOURCE_MANIFEST_PATH = f"{_SOURCE_DIRECTORY}/manifest.json"
_MAX_ROWS = 1_000_000
_MAX_FILE_BYTES = 256 * 1024 * 1024
_MAX_CLOSURE_FILES = 20_100

_OBSERVATION_FIELDS = (
    "contract",
    "schema_version",
    "source_class",
    "provider",
    "environment",
    "instrument_id",
    "contract_selection_identity",
    "plan_sha256",
    "interval_start",
    "interval_end",
    "basis",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "wap",
    "count",
    "callback_sequence",
    "request_sha256",
    "result_sha256",
    "aggregate_sha256",
    "attempt_id",
    "attempt_started_at",
    "attempt_completed_at",
    "acquisition_started_at",
    "acquisition_completed_at",
    "available_at",
    "availability_selector",
    "availability_policy",
    "availability_delay",
    "correction_policy",
    "schedule_evidence",
    "gap_disposition",
    "observation_sha256",
)


def publish_provider_history(
    output_directory: Path,
    *,
    source_manifest: Path,
    source_artifact: IbkrHistoricalResultArtifact,
    dataset: ProviderHistoricalDataset,
) -> Path:
    """Stage, verify, and atomically create one provider-history closure."""
    destination = _absolute_output_path(output_directory)
    _require_new_output_directory(destination)
    source_root = _require_file(source_manifest, "IBKR historical result manifest").parent
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{destination.name}.staging-",
            dir=str(destination.parent),
        )
    )
    try:
        staged_manifest = write_provider_history(
            staging,
            source_root=source_root,
            source_artifact=source_artifact,
            dataset=dataset,
        )
        verified = verify_provider_history(staged_manifest)
        if verified.dataset_sha256 != dataset.dataset_sha256:
            raise RuntimeError("provider-history changed between staging and verification")
        if destination.exists():
            raise FileExistsError(f"provider-history output already exists: {destination}")
        os.rename(staging, destination)
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return destination / _MANIFEST_NAME


def write_provider_history(
    output_directory: Path,
    *,
    source_root: Path,
    source_artifact: IbkrHistoricalResultArtifact,
    dataset: ProviderHistoricalDataset,
) -> Path:
    """Write one bounded provider-history closure without replacing any bytes."""
    _prepare_output_directory(output_directory)
    source_files = _source_files(source_root)
    if len(source_files) + 2 > _MAX_CLOSURE_FILES:
        raise ValueError("provider-history closure exceeds its file bound")
    parquet_bytes = _parquet_bytes(dataset.rows)
    if not parquet_bytes or len(parquet_bytes) > _MAX_FILE_BYTES:
        raise ValueError("provider-history Parquet file exceeds its bound")
    source_manifest_bytes = source_files["manifest.json"]
    source_reference = {
        "path": _SOURCE_MANIFEST_PATH,
        "contract": HISTORICAL_RESULT_CONTRACT,
        "semantic_sha256": source_artifact.aggregate.aggregate_sha256,
        "bytes_sha256": sha256_bytes(source_manifest_bytes),
        "plan_sha256": source_artifact.plan.plan_sha256,
    }
    file_reference = {
        "path": _OBSERVATIONS_PATH,
        "bytes_sha256": sha256_bytes(parquet_bytes),
        "row_count": len(dataset.rows),
    }
    manifest_identity = {
        "contract": PROVIDER_HISTORICAL_OBSERVATIONS_CONTRACT,
        "schema_version": PROVIDER_HISTORY_SCHEMA_VERSION,
        "selector_contract": PROVIDER_HISTORY_AVAILABILITY_SELECTOR_CONTRACT,
        "dataset": dataset.as_json_value(),
        "availability_policy": dataset.availability_policy.as_json_value(),
        "source_result": source_reference,
        "files": [file_reference],
    }
    manifest_document = {
        **manifest_identity,
        "manifest_sha256": _sha256_json(manifest_identity),
    }
    root = _absolute_output_path(output_directory)
    _write_create_only(
        root / _MANIFEST_NAME,
        canonical_json_bytes(cast(Mapping[str, JsonValue], manifest_document)),
    )
    _write_create_only(
        root / _OBSERVATIONS_PATH,
        parquet_bytes,
    )
    for relative_path, payload in source_files.items():
        _write_create_only(
            root / _SOURCE_DIRECTORY / relative_path,
            payload,
        )
    return root / _MANIFEST_NAME


def verify_provider_history(path: Path) -> ProviderHistoricalDataset:
    """Verify the complete provider-history and embedded Stage 6 closure from files only."""
    manifest_path = _require_file(path, "provider-history manifest")
    root = manifest_path.parent
    manifest_bytes = _read_bounded(manifest_path, "provider-history manifest")
    document = _mapping(_parse_json(manifest_bytes, "provider-history manifest"), "manifest")
    _require_exact_keys(
        document,
        {
            "contract",
            "schema_version",
            "selector_contract",
            "dataset",
            "availability_policy",
            "source_result",
            "files",
            "manifest_sha256",
        },
        "provider-history manifest",
    )
    identity = dict(document)
    manifest_sha256 = _string(identity.pop("manifest_sha256"), "manifest_sha256")
    if document["contract"] != PROVIDER_HISTORICAL_OBSERVATIONS_CONTRACT:
        raise ValueError("provider-history manifest contract is unsupported")
    if document["schema_version"] != PROVIDER_HISTORY_SCHEMA_VERSION:
        raise ValueError("provider-history manifest schema is unsupported")
    if document["selector_contract"] != PROVIDER_HISTORY_AVAILABILITY_SELECTOR_CONTRACT:
        raise ValueError("provider-history availability selector contract is unsupported")
    if manifest_sha256 != _sha256_json(identity):
        raise ValueError("provider-history manifest identity does not match its content")
    if manifest_bytes != canonical_json_bytes(cast(Mapping[str, JsonValue], document)):
        raise ValueError("provider-history manifest bytes are not canonical")
    dataset_document = _mapping(document["dataset"], "provider-history dataset")

    policy = ProviderHistoricalAvailabilityPolicy.from_json_value(document["availability_policy"])
    dataset_values = _dataset_from_manifest(dataset_document, policy)
    source_reference = _source_reference(document["source_result"])
    if source_reference["path"] != _SOURCE_MANIFEST_PATH:
        raise ValueError("provider-history source result path is not canonical")
    source_path = _safe_child(root, str(source_reference["path"]), "source result")
    source_bytes = _read_bounded(source_path, "embedded IBKR result manifest")
    if sha256_bytes(source_bytes) != str(source_reference["bytes_sha256"]):
        raise ValueError("embedded IBKR result manifest bytes do not match its reference")
    source_artifact = verify_ibkr_historical_result(source_path)
    if source_artifact.aggregate.aggregate_sha256 != source_reference["semantic_sha256"]:
        raise ValueError("embedded IBKR result identity does not match provider history")
    if source_artifact.plan.plan_sha256 != source_reference["plan_sha256"]:
        raise ValueError("embedded IBKR plan identity does not match provider history")

    files = document["files"]
    if not isinstance(files, list) or len(files) != 1:
        raise ValueError("provider-history files must contain exactly one Parquet file")
    file_reference = _file_reference(files[0])
    if file_reference["path"] != _OBSERVATIONS_PATH:
        raise ValueError("provider-history observation path is not canonical")
    parquet_path = _safe_child(root, str(file_reference["path"]), "observation file")
    parquet_bytes = _read_bounded(parquet_path, "provider-history Parquet file")
    if sha256_bytes(parquet_bytes) != str(file_reference["bytes_sha256"]):
        raise ValueError("provider-history Parquet bytes do not match its reference")
    rows = _read_parquet_rows(parquet_bytes)
    file_row_count = _int(file_reference["row_count"], "provider-history file row count")
    if len(rows) != file_row_count:
        raise ValueError("provider-history row count does not match its file reference")
    observed = ProviderHistoricalDataset.create(
        rows=rows,
        contract_selection_sha256=str(dataset_values["contract_selection_sha256"]),
        plan_sha256=str(dataset_values["plan_sha256"]),
        runtime_sha256=str(dataset_values["runtime_sha256"]),
        aggregate_sha256=str(dataset_values["aggregate_sha256"]),
        availability_policy=policy,
    )
    manifest_row_count = _int(dataset_values["row_count"], "provider-history dataset row count")
    if len(rows) != manifest_row_count:
        raise ValueError("provider-history row count does not match its manifest")
    if observed.dataset_sha256 != str(dataset_values["dataset_sha256"]):
        raise ValueError("provider-history dataset identity does not match its manifest")
    replayed = replay_provider_history_dataset(observed)
    if replayed.dataset_sha256 != observed.dataset_sha256:
        raise ValueError("provider-history row replay changed its dataset identity")
    expected = build_provider_history_dataset(
        source_artifact,
        availability_delay=policy.delay,
    )
    if expected.dataset_sha256 != observed.dataset_sha256:
        raise ValueError("provider-history rows do not replay from request-result children")

    expected_paths = {
        _MANIFEST_NAME,
        _OBSERVATIONS_PATH,
        *{
            f"{_SOURCE_DIRECTORY}/{relative_path}"
            for relative_path in _source_files_from_disk(root / _SOURCE_DIRECTORY)
        },
    }
    _require_exact_tree(root, expected_paths)
    return observed


def _dataset_from_manifest(
    value: Mapping[str, object],
    policy: ProviderHistoricalAvailabilityPolicy,
) -> dict[str, object]:
    _require_exact_keys(
        value,
        {
            "contract",
            "schema_version",
            "source_class",
            "provider",
            "environment",
            "contract_selection_sha256",
            "plan_sha256",
            "runtime_sha256",
            "aggregate_sha256",
            "availability_policy",
            "observation_sha256",
            "row_count",
            "dataset_sha256",
        },
        "provider-history dataset",
    )
    if value["contract"] != PROVIDER_HISTORICAL_OBSERVATIONS_CONTRACT:
        raise ValueError("provider-history dataset contract is unsupported")
    if value["schema_version"] != PROVIDER_HISTORY_SCHEMA_VERSION:
        raise ValueError("provider-history dataset schema is unsupported")
    if value["source_class"] != PROVIDER_HISTORY_SOURCE_CLASS:
        raise ValueError("provider-history dataset source class is unsupported")
    if value["provider"] != PROVIDER_HISTORY_PROVIDER:
        raise ValueError("provider-history dataset provider is unsupported")
    if value["environment"] != PROVIDER_HISTORY_ENVIRONMENT:
        raise ValueError("provider-history dataset environment is unsupported")
    if value["availability_policy"] != policy.as_json_value():
        raise ValueError("provider-history dataset policy differs from its manifest")
    observation_hashes = value["observation_sha256"]
    if not isinstance(observation_hashes, list):
        raise ValueError("provider-history observation identity list is invalid")
    for observation_hash in observation_hashes:
        _require_digest(observation_hash, "provider-history observation identity")
    row_count = _int(value["row_count"], "provider-history dataset row count")
    if row_count > _MAX_ROWS:
        raise ValueError("provider-history dataset row count is invalid")
    if row_count != len(observation_hashes):
        raise ValueError("provider-history dataset row count is inconsistent")
    lineage = {
        "contract_selection_sha256": _string(
            value["contract_selection_sha256"], "contract_selection_sha256"
        ),
        "plan_sha256": _string(value["plan_sha256"], "plan_sha256"),
        "runtime_sha256": _string(value["runtime_sha256"], "runtime_sha256"),
        "aggregate_sha256": _string(value["aggregate_sha256"], "aggregate_sha256"),
    }
    for field, digest in lineage.items():
        _require_digest(digest, f"provider-history {field}")
    identity = {
        "contract": PROVIDER_HISTORICAL_OBSERVATIONS_CONTRACT,
        "schema_version": PROVIDER_HISTORY_SCHEMA_VERSION,
        "source_class": PROVIDER_HISTORY_SOURCE_CLASS,
        "provider": PROVIDER_HISTORY_PROVIDER,
        "environment": PROVIDER_HISTORY_ENVIRONMENT,
        **lineage,
        "availability_policy": policy.as_json_value(),
        "observation_sha256": [str(item) for item in observation_hashes],
    }
    dataset_sha256 = _string(value["dataset_sha256"], "dataset_sha256")
    _require_digest(dataset_sha256, "dataset_sha256")
    if dataset_sha256 != sha256_json(identity):
        raise ValueError("provider-history dataset identity does not match its manifest")
    return {
        **lineage,
        "row_count": row_count,
        "dataset_sha256": dataset_sha256,
    }


def _source_reference(value: object) -> dict[str, object]:
    mapping = _mapping(value, "provider-history source result")
    _require_exact_keys(
        mapping,
        {"path", "contract", "semantic_sha256", "bytes_sha256", "plan_sha256"},
        "provider-history source result",
    )
    if mapping["contract"] != HISTORICAL_RESULT_CONTRACT:
        raise ValueError("provider-history source result contract is unsupported")
    _require_digest(mapping["semantic_sha256"], "source semantic identity")
    _require_digest(mapping["bytes_sha256"], "source bytes identity")
    _require_digest(mapping["plan_sha256"], "source plan identity")
    return dict(mapping)


def _file_reference(value: object) -> dict[str, object]:
    mapping = _mapping(value, "provider-history file reference")
    _require_exact_keys(mapping, {"path", "bytes_sha256", "row_count"}, "file reference")
    _require_digest(mapping["bytes_sha256"], "provider-history file bytes identity")
    if not isinstance(mapping["row_count"], int) or mapping["row_count"] < 0:
        raise ValueError("provider-history file row count is invalid")
    return dict(mapping)


def _parquet_bytes(rows: tuple[ProviderHistoricalObservation, ...]) -> bytes:
    payloads = []
    for row in rows:
        payload = row.as_json_value()
        payload["schedule_evidence"] = json.dumps(
            payload["schedule_evidence"],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        payloads.append(payload)
    if payloads:
        frame = pl.DataFrame(payloads)
    else:
        frame = pl.DataFrame(
            {field: pl.Series(field, [], dtype=pl.String) for field in _OBSERVATION_FIELDS}
        )
    buffer = io.BytesIO()
    frame.write_parquet(buffer)
    return buffer.getvalue()


def _read_parquet_rows(payload: bytes) -> tuple[ProviderHistoricalObservation, ...]:
    frame = pl.read_parquet(io.BytesIO(payload))
    observed: list[ProviderHistoricalObservation] = []
    if set(frame.columns) != set(_OBSERVATION_FIELDS):
        raise ValueError("provider-history Parquet columns are not exact")
    for raw in frame.to_dicts():
        row = dict(raw)
        schedule = row["schedule_evidence"]
        if not isinstance(schedule, str):
            raise ValueError("provider-history schedule evidence column is not canonical JSON")
        parsed = json.loads(schedule)
        row["schedule_evidence"] = parsed
        observed.append(ProviderHistoricalObservation.from_json_value(row))
    return tuple(observed)


def _source_files(root: Path) -> dict[str, bytes]:
    root = _require_directory(root, "source result root")
    files: dict[str, bytes] = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("source result closure contains a symlink")
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            if not relative or relative.startswith("/") or ".." in Path(relative).parts:
                raise ValueError("source result closure contains an unsafe path")
            files[relative] = _read_bounded(path, f"source result child {relative}")
        elif not path.is_dir():
            raise ValueError("source result closure contains a non-regular path")
    if "manifest.json" not in files:
        raise FileNotFoundError("source result closure has no manifest")
    return files


def _source_files_from_disk(root: Path) -> set[str]:
    files = _source_files(root)
    return set(files)


def _require_exact_tree(root: Path, expected: set[str]) -> None:
    actual: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"provider-history tree contains a symlink: {path}")
        relative = path.relative_to(root).as_posix()
        if path.is_file():
            actual.add(relative)
        elif not path.is_dir():
            raise ValueError(f"provider-history tree contains a non-regular path: {path}")
    if actual != expected:
        raise ValueError(
            "provider-history tree differs from its manifest; "
            f"missing={sorted(expected - actual)}, unexpected={sorted(actual - expected)}"
        )


def _prepare_output_directory(path: Path) -> None:
    current = _absolute_output_path(path)
    if current.exists():
        if not current.is_dir():
            raise FileExistsError(f"provider-history output is not a directory: {path}")
        if any(current.iterdir()):
            raise FileExistsError(f"provider-history output directory is not empty: {path}")
        return
    if not current.parent.is_dir():
        raise FileNotFoundError(f"provider-history output parent does not exist: {current.parent}")
    current.mkdir()


def _require_new_output_directory(path: Path) -> None:
    if path.exists():
        if not path.is_dir():
            raise FileExistsError(f"provider-history output is not a directory: {path}")
        raise FileExistsError(f"provider-history output already exists: {path}")
    if not path.parent.is_dir():
        raise FileNotFoundError(f"provider-history output parent does not exist: {path.parent}")


def _write_create_only(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError(f"provider-history path cannot replace a symlink: {path}")
    try:
        with path.open("xb") as output:
            output.write(payload)
    except FileExistsError as error:
        raise FileExistsError(f"provider-history path already exists: {path}") from error


def _absolute_output_path(path: Path) -> Path:
    if ".." in path.parts:
        raise ValueError(f"provider-history output path escapes its root: {path}")
    current = path if path.is_absolute() else Path.cwd() / path
    for ancestor in (current, *current.parents):
        if ancestor.is_symlink():
            raise ValueError(f"provider-history output path contains a symlink: {path}")
    return current


def _safe_child(root: Path, relative: str, field: str) -> Path:
    if not relative or relative.startswith("/") or ".." in Path(relative).parts:
        raise ValueError(f"{field} path is unsafe: {relative}")
    child = root / relative
    for ancestor in (child, *child.parents):
        if ancestor == root.parent:
            break
        if ancestor.is_symlink():
            raise ValueError(f"{field} path contains a symlink: {relative}")
    if not child.is_file():
        raise FileNotFoundError(f"{field} is not a regular file: {relative}")
    return child


def _require_file(path: Path, field: str) -> Path:
    if ".." in path.parts:
        raise ValueError(f"{field} path escapes its root: {path}")
    current = path if path.is_absolute() else Path.cwd() / path
    for ancestor in (current, *current.parents):
        if ancestor.is_symlink():
            raise ValueError(f"{field} path contains a symlink: {path}")
    if not current.is_file():
        raise FileNotFoundError(f"{field} is not a regular file: {path}")
    return current


def _require_directory(path: Path, field: str) -> Path:
    current = _absolute_output_path(path)
    if not current.is_dir():
        raise FileNotFoundError(f"{field} is not a directory: {path}")
    return current


def _read_bounded(path: Path, field: str) -> bytes:
    size = path.stat().st_size
    if size <= 0 or size > _MAX_FILE_BYTES:
        raise ValueError(f"{field} exceeds its bound")
    payload = path.read_bytes()
    if len(payload) != size:
        raise ValueError(f"{field} changed while being read")
    return payload


def _parse_json(payload: bytes, field: str) -> object:
    try:
        return json.loads(payload)
    except json.JSONDecodeError as error:
        raise ValueError(f"{field} is not valid JSON") from error


def _mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be an object")
    return {key: item for key, item in cast(Mapping[str, object], value).items()}


def _require_exact_keys(value: Mapping[str, object], expected: set[str], field: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{field} fields are not exact")


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _require_digest(value: object, field: str) -> None:
    value = _string(value, field)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} must be a lower-case SHA-256 digest")


def _int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} must be an integer")
    return value


def _sha256_json(value: object) -> str:
    converted = to_json_value(value)
    if not isinstance(converted, dict):
        raise TypeError("provider-history identity must be an object")
    return sha256_json(converted)
