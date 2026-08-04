"""Create-only provider-history publication and file-only verification."""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import tempfile
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import cast

import polars as pl

from qtrad.application.provider_history import (
    ProviderHistorySource,
    build_provider_history_dataset,
    iter_provider_history_partitions,
    provider_history_partition_row_bounds,
    replay_provider_history_dataset,
)
from qtrad.domain.events import JsonValue, to_json_value
from qtrad.domain.ibkr_results import (
    HISTORICAL_RESULT_CONTRACT,
    MAX_IBKR_RESULT_BYTES,
    MAX_IBKR_RESULT_CHILDREN,
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
    ProviderHistoricalPartition,
    ProviderHistoricalPartitionReference,
    sha256_json,
)
from qtrad.runtime.ibkr_results import (
    verify_ibkr_historical_result_stream,
)

_MANIFEST_NAME = "manifest.json"
_PLAN_NAME = "plan.json"
_OBSERVATIONS_DIRECTORY = "observations"
_SOURCE_DIRECTORY = "source-result"
_SOURCE_MANIFEST_PATH = f"{_SOURCE_DIRECTORY}/manifest.json"
_MAX_FILE_BYTES = 256 * 1024 * 1024
_MAX_CLOSURE_FILES = 20_100
_READ_BATCH_ROWS = 8_192

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
    source_artifact: ProviderHistorySource,
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
    source_artifact: ProviderHistorySource,
    dataset: ProviderHistoricalDataset,
) -> Path:
    """Write one bounded provider-history closure without replacing any bytes."""
    _prepare_output_directory(output_directory)
    source_files = _source_files(source_root)
    source_manifest_bytes = _read_bounded(
        source_root / _MANIFEST_NAME,
        "IBKR historical result manifest",
    )
    source_digests = _source_file_digests(source_artifact, source_manifest_bytes)
    if source_files != set(source_digests):
        raise ValueError("source result closure differs from its manifest")
    partition_bounds = provider_history_partition_row_bounds(source_artifact)
    source_plan_row_bound = sum(partition_bounds.values())
    if dataset.row_count > source_plan_row_bound:
        raise ValueError("provider-history rows exceed source-plan capacity")

    root = _absolute_output_path(output_directory)
    partition_references: list[dict[str, object]] = []
    partition_paths: set[str] = set()
    expected_partitions = iter(dataset.partitions)
    for partition in iter_provider_history_partitions(
        source_artifact,
        policy=dataset.availability_policy,
    ):
        row_upper_bound = partition_bounds.get(partition.key)
        if row_upper_bound is None:
            raise ValueError("provider-history partition is absent from the source plan")
        if partition.row_count > row_upper_bound:
            raise ValueError("provider-history partition rows exceed source-plan capacity")
        try:
            expected_partition = next(expected_partitions)
        except StopIteration as error:
            raise ValueError(
                "provider-history source replay produced an unexpected partition"
            ) from error
        actual_partition = ProviderHistoricalPartitionReference.from_partition(partition)
        if actual_partition != expected_partition:
            raise ValueError("provider-history dataset differs from source replay")
        parquet_bytes = _parquet_bytes(partition.rows)
        if not parquet_bytes or len(parquet_bytes) > _MAX_FILE_BYTES:
            raise ValueError("provider-history Parquet partition exceeds its bound")
        relative_path = _partition_path(partition.key)
        if relative_path in partition_paths:
            raise ValueError("provider-history partition path is duplicated")
        partition_paths.add(relative_path)
        _write_create_only(root / relative_path, parquet_bytes)
        partition_references.append(
            {
                "path": relative_path,
                "bytes_sha256": sha256_bytes(parquet_bytes),
                "partition_sha256": partition.partition_sha256,
                "row_count": partition.row_count,
                "row_upper_bound": row_upper_bound,
            }
        )
    try:
        next(expected_partitions)
    except StopIteration:
        pass
    else:
        raise ValueError("provider-history dataset contains a partition absent from source replay")

    if len(source_files) + len(partition_references) + 1 > _MAX_CLOSURE_FILES:
        raise ValueError("provider-history closure exceeds its file bound")
    source_reference = {
        "path": _SOURCE_MANIFEST_PATH,
        "contract": HISTORICAL_RESULT_CONTRACT,
        "semantic_sha256": source_artifact.aggregate.aggregate_sha256,
        "bytes_sha256": sha256_bytes(source_manifest_bytes),
        "plan_sha256": source_artifact.plan.plan_sha256,
    }
    manifest_identity = {
        "contract": PROVIDER_HISTORICAL_OBSERVATIONS_CONTRACT,
        "schema_version": PROVIDER_HISTORY_SCHEMA_VERSION,
        "selector_contract": PROVIDER_HISTORY_AVAILABILITY_SELECTOR_CONTRACT,
        "dataset": dataset.as_json_value(),
        "availability_policy": dataset.availability_policy.as_json_value(),
        "source_result": source_reference,
        "source_plan_row_bound": source_plan_row_bound,
        "files": partition_references,
    }
    manifest_document = {
        **manifest_identity,
        "manifest_sha256": _sha256_json(manifest_identity),
    }
    for relative_path in source_files:
        _copy_source_file(
            source_root,
            root / _SOURCE_DIRECTORY,
            relative_path,
            expected_digest=source_digests[relative_path],
        )
    _write_create_only(
        root / _MANIFEST_NAME,
        canonical_json_bytes(cast(Mapping[str, JsonValue], manifest_document)),
    )
    return root / _MANIFEST_NAME


def _partition_path(key: tuple[str, date]) -> str:
    instrument_digest = sha256_json({"instrument_id": key[0]})
    return (
        f"{_OBSERVATIONS_DIRECTORY}/instrument-{instrument_digest}/"
        f"date-{key[1].isoformat()}.parquet"
    )


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
            "source_plan_row_bound",
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
    dataset = _dataset_from_manifest(dataset_document, policy)

    source_reference = _source_reference(document["source_result"])
    if source_reference["path"] != _SOURCE_MANIFEST_PATH:
        raise ValueError("provider-history source result path is not canonical")
    source_path = _safe_child(root, str(source_reference["path"]), "source result")
    source_bytes = _read_bounded(source_path, "embedded IBKR result manifest")
    if sha256_bytes(source_bytes) != str(source_reference["bytes_sha256"]):
        raise ValueError("embedded IBKR result manifest bytes do not match its reference")
    source_stream = verify_ibkr_historical_result_stream(source_path)
    expected = build_provider_history_dataset(
        source_stream,
        availability_delay=policy.delay,
    )
    if expected.dataset_sha256 != dataset.dataset_sha256:
        raise ValueError("provider-history dataset does not replay from request-result children")
    if source_stream.aggregate.aggregate_sha256 != source_reference["semantic_sha256"]:
        raise ValueError("embedded IBKR result identity does not match provider history")
    if source_stream.plan.plan_sha256 != source_reference["plan_sha256"]:
        raise ValueError("embedded IBKR plan identity does not match provider history")
    source_file_paths = _source_files(source_stream.source_root)
    partition_bounds = provider_history_partition_row_bounds(source_stream)
    source_plan_row_bound = _int(
        document["source_plan_row_bound"],
        "provider-history source-plan row bound",
    )
    if source_plan_row_bound != sum(partition_bounds.values()):
        raise ValueError("provider-history source-plan row bound does not match its source")
    if dataset.row_count > source_plan_row_bound:
        raise ValueError("provider-history dataset exceeds its source-plan capacity")
    expected_bound_by_path = {
        _partition_path(key): row_bound for key, row_bound in partition_bounds.items()
    }
    expected_key_by_path = {_partition_path(key): key for key in partition_bounds}
    files = document["files"]
    if not isinstance(files, list) or len(files) > _MAX_CLOSURE_FILES:
        raise ValueError("provider-history files are invalid or exceed their bound")
    if 1 + len(source_file_paths) + len(files) > _MAX_CLOSURE_FILES:
        raise ValueError("provider-history closure exceeds its file bound")
    expected_paths = {_MANIFEST_NAME}
    paths: list[str] = []
    observed_refs: list[ProviderHistoricalPartitionReference] = []
    expected_refs = {partition.key: partition for partition in dataset.partitions}
    seen_keys: set[tuple[str, date]] = set()
    for item in files:
        reference = _file_reference(item)
        relative_path = _string(reference["path"], "provider-history partition path")
        if relative_path not in expected_bound_by_path:
            raise ValueError("provider-history partition path is absent from the source plan")
        if paths and relative_path <= paths[-1]:
            raise ValueError("provider-history partition references are not canonical")
        paths.append(relative_path)
        expected_row_bound = expected_bound_by_path[relative_path]
        declared_row_bound = _int(
            reference["row_upper_bound"],
            "provider-history partition row upper bound",
        )
        if declared_row_bound != expected_row_bound:
            raise ValueError(
                "provider-history partition row upper bound differs from its source plan"
            )
        row_count = _int(reference["row_count"], "provider-history partition row count")
        partition_path = _safe_child(root, relative_path, "provider-history partition")
        parquet_bytes = _read_bounded(partition_path, "provider-history Parquet partition")
        if sha256_bytes(parquet_bytes) != str(reference["bytes_sha256"]):
            raise ValueError("provider-history Parquet partition bytes do not match its reference")
        partition = _read_parquet_rows(
            partition_path,
            expected_row_count=row_count,
            row_upper_bound=expected_row_bound,
        )
        key = partition.key
        if key != expected_key_by_path[relative_path]:
            raise ValueError("provider-history partition content differs from its canonical path")
        if key in seen_keys:
            raise ValueError("provider-history partition references are duplicated")
        expected_partition = expected_refs.get(key)
        actual_partition = ProviderHistoricalPartitionReference.from_partition(partition)
        if expected_partition is None or actual_partition != expected_partition:
            raise ValueError(
                "provider-history partition identity differs from its dataset reference"
            )
        if actual_partition.partition_sha256 != str(reference["partition_sha256"]):
            raise ValueError("provider-history partition identity differs from its file reference")
        seen_keys.add(key)
        observed_refs.append(actual_partition)
        expected_paths.add(relative_path)

    if seen_keys != set(expected_refs):
        raise ValueError("provider-history partition closure differs from its dataset references")
    observed = ProviderHistoricalDataset.create(
        partitions=tuple(observed_refs),
        contract_selection_sha256=dataset.contract_selection_sha256,
        plan_sha256=dataset.plan_sha256,
        runtime_sha256=dataset.runtime_sha256,
        aggregate_sha256=dataset.aggregate_sha256,
        availability_policy=policy,
    )
    if observed.dataset_sha256 != dataset.dataset_sha256:
        raise ValueError("provider-history dataset identity does not match its manifest")
    replayed = replay_provider_history_dataset(observed)
    if replayed.dataset_sha256 != observed.dataset_sha256:
        raise ValueError("provider-history partition replay changed its dataset identity")
    expected_paths.update(
        f"{_SOURCE_DIRECTORY}/{relative_path}" for relative_path in source_file_paths
    )
    _require_exact_tree(root, expected_paths)
    return observed


def _dataset_from_manifest(
    value: Mapping[str, object],
    policy: ProviderHistoricalAvailabilityPolicy,
) -> ProviderHistoricalDataset:
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
            "partitions",
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
    raw_partitions = value["partitions"]
    if not isinstance(raw_partitions, list) or len(raw_partitions) > _MAX_CLOSURE_FILES:
        raise ValueError("provider-history partition references are invalid or exceed their bound")
    partitions: list[ProviderHistoricalPartitionReference] = []
    for item in raw_partitions:
        partition = _mapping(item, "provider-history partition reference")
        _require_exact_keys(
            partition,
            {"instrument_id", "partition_date", "row_count", "partition_sha256"},
            "provider-history partition reference",
        )
        instrument_id = _string(partition["instrument_id"], "provider-history partition instrument")
        partition_date_text = _string(
            partition["partition_date"],
            "provider-history partition date",
        )
        try:
            partition_date = date.fromisoformat(partition_date_text)
        except ValueError as error:
            raise ValueError("provider-history partition date is invalid") from error
        if partition_date.isoformat() != partition_date_text:
            raise ValueError("provider-history partition date is not canonical")
        partition_sha256 = _string(
            partition["partition_sha256"],
            "provider-history partition identity",
        )
        _require_digest(partition_sha256, "provider-history partition identity")
        partitions.append(
            ProviderHistoricalPartitionReference(
                instrument_id=instrument_id,
                partition_date=partition_date,
                row_count=_int(partition["row_count"], "provider-history partition row count"),
                partition_sha256=partition_sha256,
            )
        )
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
    dataset = ProviderHistoricalDataset.create(
        partitions=tuple(partitions),
        contract_selection_sha256=lineage["contract_selection_sha256"],
        plan_sha256=lineage["plan_sha256"],
        runtime_sha256=lineage["runtime_sha256"],
        aggregate_sha256=lineage["aggregate_sha256"],
        availability_policy=policy,
    )
    row_count = _int(value["row_count"], "provider-history dataset row count")
    if row_count != dataset.row_count:
        raise ValueError("provider-history dataset row count is inconsistent")
    dataset_sha256 = _string(value["dataset_sha256"], "dataset_sha256")
    _require_digest(dataset_sha256, "dataset_sha256")
    if dataset_sha256 != dataset.dataset_sha256:
        raise ValueError("provider-history dataset identity does not match its manifest")
    return dataset


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
    _require_exact_keys(
        mapping,
        {"path", "bytes_sha256", "partition_sha256", "row_count", "row_upper_bound"},
        "file reference",
    )
    _require_digest(mapping["bytes_sha256"], "provider-history file bytes identity")
    _require_digest(mapping["partition_sha256"], "provider-history partition identity")
    row_count = _int(mapping["row_count"], "provider-history file row count")
    row_upper_bound = _int(mapping["row_upper_bound"], "provider-history row upper bound")
    if row_count <= 0 or row_upper_bound <= 0 or row_count > row_upper_bound:
        raise ValueError("provider-history file row bounds are invalid")
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


def _read_parquet_rows(
    path: Path,
    *,
    expected_row_count: int,
    row_upper_bound: int,
) -> ProviderHistoricalPartition:
    if expected_row_count <= 0 or expected_row_count > row_upper_bound:
        raise ValueError("provider-history Parquet row bounds are invalid")
    schema = pl.read_parquet_schema(path)
    if set(schema) != set(_OBSERVATION_FIELDS):
        raise ValueError("provider-history Parquet columns are not exact")
    footer_row_count = _parquet_footer_row_count(path)
    if footer_row_count > row_upper_bound:
        raise ValueError("provider-history Parquet footer row count exceeds its bound")
    if footer_row_count != expected_row_count:
        raise ValueError("provider-history Parquet footer row count does not match its reference")

    observed: list[ProviderHistoricalObservation] = []
    decoded_count = 0
    for frame in pl.scan_parquet(path).collect_batches(chunk_size=_READ_BATCH_ROWS):
        if frame.height > _READ_BATCH_ROWS:
            raise ValueError("provider-history Parquet batch exceeds its bound")
        next_count = decoded_count + frame.height
        if next_count > row_upper_bound:
            raise ValueError("provider-history Parquet decoded rows exceed their bound")
        if next_count > expected_row_count:
            raise ValueError("provider-history Parquet decoded rows exceed their reference")
        decoded_count = next_count
        for raw in frame.to_dicts():
            row = dict(raw)
            schedule = row["schedule_evidence"]
            if not isinstance(schedule, str):
                raise ValueError("provider-history schedule evidence column is not canonical JSON")
            row["schedule_evidence"] = json.loads(schedule)
            observed.append(ProviderHistoricalObservation.from_json_value(row))
    if decoded_count != expected_row_count:
        raise ValueError("provider-history Parquet decoded row count does not match its reference")
    return ProviderHistoricalPartition.create(rows=tuple(observed))


def _parquet_footer_row_count(path: Path) -> int:
    """Read the Parquet footer row count without decoding observation columns."""
    try:
        footer = pl.scan_parquet(path).select(pl.len().alias("_row_count")).collect()
    except Exception as error:
        raise ValueError("provider-history Parquet footer row count is unreadable") from error
    if footer.height != 1:
        raise ValueError("provider-history Parquet footer row count is invalid")
    value = footer.item()
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("provider-history Parquet footer row count is invalid")
    return value


def _source_files(root: Path) -> set[str]:
    root = _require_directory(root, "source result root")
    files: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("source result closure contains a symlink")
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            if not relative or relative.startswith("/") or ".." in Path(relative).parts:
                raise ValueError("source result closure contains an unsafe path")
            files.add(relative)
        elif not path.is_dir():
            raise ValueError("source result closure contains a non-regular path")
    if _PLAN_NAME not in files or _MANIFEST_NAME not in files:
        raise FileNotFoundError("source result closure is missing its manifest or plan")
    return files


def _source_file_digests(
    source: ProviderHistorySource,
    manifest_bytes: bytes,
) -> dict[str, str]:
    digests = {
        _MANIFEST_NAME: sha256_bytes(manifest_bytes),
        _PLAN_NAME: source.aggregate.plan.bytes_sha256,
    }
    for reference in source.aggregate.request_results:
        digests[reference.path] = reference.bytes_sha256
    if len(digests) > MAX_IBKR_RESULT_CHILDREN:
        raise ValueError("source result closure exceeds its bound")
    return digests


def _copy_source_file(
    source_root: Path,
    destination_root: Path,
    relative_path: str,
    *,
    expected_digest: str,
) -> None:
    if not relative_path or relative_path.startswith("/") or ".." in Path(relative_path).parts:
        raise ValueError("source result closure contains an unsafe path")
    source_path = source_root / relative_path
    if source_path.is_symlink() or not source_path.is_file():
        raise ValueError(f"source result child is not a regular file: {relative_path}")
    size = source_path.stat().st_size
    if size <= 0 or size > MAX_IBKR_RESULT_BYTES:
        raise ValueError(f"source result child exceeds its bounded size: {relative_path}")
    target = destination_root / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    try:
        with source_path.open("rb") as source, target.open("xb") as destination:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
                destination.write(chunk)
    except FileExistsError as error:
        raise FileExistsError(f"provider-history path already exists: {target}") from error
    if digest.hexdigest() != expected_digest:
        raise ValueError(
            f"source result child bytes digest does not match its manifest: {relative_path}"
        )


def _source_files_from_disk(root: Path) -> set[str]:
    return _source_files(root)


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
