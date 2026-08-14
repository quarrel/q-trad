"""Create-only provider-history publication and file-only verification."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import cast

import polars as pl

from qtrad.application.provider_history import (
    ProviderHistoryObservationRows,
    ProviderHistoryObservationSummary,
    ProviderHistoryRequestEvidence,
    ProviderHistorySelection,
    ProviderHistorySource,
    ProviderHistorySourceEvidence,
    provider_history_partition_row_bounds,
)
from qtrad.domain.events import JsonValue, to_json_value
from qtrad.domain.ibkr_results import (
    HISTORICAL_RESULT_CONTRACT,
    MAX_IBKR_RESULT_CHILDREN,
    IbkrHistoricalEvidenceDisposition,
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
    ProviderHistoricalPartitionReference,
    sha256_json,
)
from qtrad.runtime.ibkr_results import (
    _LEGACY_HISTORICAL_RESULT_V2_CONTRACT,
    _read_legacy_ibkr_historical_result_v2_header,
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
_SOURCE_VERIFICATION_RECEIPT_CONTRACT = "qtrad-provider-history-source-verification-v1"
_PROVIDER_HISTORY_VERIFICATION_CONTRACT = "qtrad-provider-history-verification-v1"
_PROVIDER_HISTORY_VERIFICATION_SCHEMA_VERSION = 1
_PROVIDER_HISTORY_VERIFIER_CONTRACT = "qtrad-provider-history-semantic-verifier-v1"
_PROVIDER_HISTORY_VERIFIER_VERSION = 1
_PROVIDER_HISTORY_COMPLETED_CHECKS = (
    "manifest-and-closure-bytes",
    "stage6-plan-result-and-request-replay",
    "availability-policy-replay",
    "partition-semantic-replay",
    "request-evidence-summary",
    "observation-interval-summary",
)
_LEGACY_PROVIDER_HISTORY_VERIFIER_SHA256 = (
    "fb4b30c9486d45e8ca4bd5e427a79f12c48443a6ef8ac046e0cbc5927b6ee000"
)
_MAX_VERIFICATION_RECEIPT_BYTES = 64 * 1024 * 1024

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


def _partition_path(key: tuple[str, date]) -> str:
    instrument_digest = sha256_json({"instrument_id": key[0]})
    return (
        f"{_OBSERVATIONS_DIRECTORY}/instrument-{instrument_digest}/"
        f"date-{key[1].isoformat()}.parquet"
    )


class _ObservationSummaryBuilder:
    def __init__(self) -> None:
        self._accepted: dict[str, list[tuple[datetime, datetime]]] = {}
        self._source_start: datetime | None = None
        self._source_end: datetime | None = None

    def add(self, rows: tuple[ProviderHistoricalObservation, ...]) -> None:
        for row in rows:
            self._source_start = (
                row.interval_start
                if self._source_start is None
                else min(self._source_start, row.interval_start)
            )
            self._source_end = (
                row.interval_end
                if self._source_end is None
                else max(self._source_end, row.interval_end)
            )
            accepted_end = row.interval_start + timedelta(minutes=1)
            intervals = self._accepted.setdefault(row.request_sha256, [])
            if intervals and row.interval_start <= intervals[-1][1]:
                previous_start, previous_end = intervals[-1]
                intervals[-1] = (previous_start, max(previous_end, accepted_end))
            else:
                intervals.append((row.interval_start, accepted_end))

    def finish(self) -> ProviderHistoryObservationSummary | None:
        if self._source_start is None or self._source_end is None:
            return None
        return ProviderHistoryObservationSummary(
            accepted_intervals_by_request=tuple(
                (request_sha256, tuple(intervals))
                for request_sha256, intervals in sorted(self._accepted.items())
            ),
            source_start=self._source_start,
            source_end=self._source_end,
        )


def preflight_provider_history_verification_receipt(
    receipt_output: Path,
    *,
    immutable_roots: tuple[Path, ...],
) -> Path:
    """Reject receipt outputs that could mutate authenticated evidence."""

    receipt_path = _absolute_output_path(receipt_output)
    if receipt_path.exists():
        raise FileExistsError(f"provider-history path already exists: {receipt_path}")
    if any(receipt_path.is_relative_to(_absolute_output_path(root)) for root in immutable_roots):
        raise ValueError(
            "provider-history receipt cannot be written inside an authenticated closure"
        )
    return receipt_path


def _provider_history_contract(path: Path) -> str:
    manifest = _require_file(path, "provider-history manifest")
    document = _mapping(
        _parse_json(
            _read_bounded(manifest, "provider-history manifest"), "provider-history manifest"
        ),
        "provider-history manifest",
    )
    return _string(document["contract"], "provider-history contract")


def verify_provider_history(
    path: Path,
    *,
    receipt_output: Path | None = None,
) -> ProviderHistoricalDataset:
    """Deeply verify current provider history and optionally persist its receipt."""

    from qtrad.runtime.provider_history_v2 import verify_provider_history_v2

    return verify_provider_history_v2(path, receipt_output=receipt_output).dataset


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


def _source_reference(
    value: object,
    *,
    allow_legacy_stage6: bool = False,
) -> dict[str, object]:
    mapping = _mapping(value, "provider-history source result")
    _require_exact_keys(
        mapping,
        {"path", "contract", "semantic_sha256", "bytes_sha256", "plan_sha256"},
        "provider-history source result",
    )
    if mapping["contract"] != HISTORICAL_RESULT_CONTRACT and not (
        allow_legacy_stage6
        and mapping["contract"] == _LEGACY_HISTORICAL_RESULT_V2_CONTRACT
    ):
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
    if len(source.aggregate.request_results) > MAX_IBKR_RESULT_CHILDREN:
        raise ValueError("source result closure exceeds its bound")
    return digests


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


def verify_provider_history_file_only(path: Path) -> ProviderHistoricalDataset:
    """Authenticate the provider-history closure without decoding bar rows.

    The complete verifier remains the authority after the holdout is opened.
    This path is intentionally limited to canonical manifests, semantic
    identities, partition metadata/footer counts, and byte hashes so F2 cannot
    materialise provider features or outcomes.
    """

    from qtrad.runtime.provider_history_v2 import (
        PROVIDER_HISTORICAL_OBSERVATIONS_V2_CONTRACT,
        verify_provider_history_v2_file_only,
    )

    if _provider_history_contract(path) == PROVIDER_HISTORICAL_OBSERVATIONS_V2_CONTRACT:
        return verify_provider_history_v2_file_only(path).dataset

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
    source_reference = _source_reference(
        document["source_result"],
        allow_legacy_stage6=True,
    )
    if source_reference["path"] != _SOURCE_MANIFEST_PATH:
        raise ValueError("provider-history source result path is not canonical")
    source_path = _safe_child(root, str(source_reference["path"]), "source result")
    source_root = source_path.parent
    source_bytes = _read_bounded(source_path, "embedded IBKR result manifest")
    if sha256_bytes(source_bytes) != str(source_reference["bytes_sha256"]):
        raise ValueError("embedded IBKR result manifest bytes do not match its reference")
    source_document = _mapping(
        _parse_json(source_bytes, "embedded IBKR result manifest"),
        "embedded IBKR result manifest",
    )
    source_stream = (
        _read_legacy_ibkr_historical_result_v2_header(
            source_path,
            require_exact_tree=True,
        )
        if source_document["contract"] == _LEGACY_HISTORICAL_RESULT_V2_CONTRACT
        else verify_ibkr_historical_result_stream(source_path)
    )
    if source_stream.aggregate.aggregate_sha256 != source_reference["semantic_sha256"]:
        raise ValueError("embedded IBKR result identity does not match provider history")
    if source_stream.plan.plan_sha256 != source_reference["plan_sha256"]:
        raise ValueError("embedded IBKR plan identity does not match provider history")
    partition_bounds = provider_history_partition_row_bounds(source_stream)
    source_plan_row_bound = _int(
        document["source_plan_row_bound"],
        "provider-history source-plan row bound",
    )
    if source_plan_row_bound != sum(partition_bounds.values()):
        raise ValueError("provider-history source-plan row bound does not match its source")
    if dataset.row_count > source_plan_row_bound:
        raise ValueError("provider-history dataset exceeds its source-plan capacity")
    source_files = _source_files(source_root)
    expected_source_digests = {
        _MANIFEST_NAME: sha256_bytes(source_bytes),
        _PLAN_NAME: source_stream.aggregate.plan.bytes_sha256,
        **{
            reference.path: reference.bytes_sha256
            for reference in source_stream.aggregate.request_results
        },
    }
    if source_files != set(expected_source_digests):
        raise ValueError("provider-history source result closure differs from its manifest")
    for relative_path, expected_digest in expected_source_digests.items():
        child = _safe_child(source_root, relative_path, "provider-history source child")
        if sha256_bytes(_read_bounded(child, "provider-history source child")) != expected_digest:
            raise ValueError("provider-history source child bytes changed")

    raw_files = document["files"]
    if not isinstance(raw_files, list) or len(raw_files) > _MAX_CLOSURE_FILES:
        raise ValueError("provider-history files are invalid or exceed their bound")
    expected_partitions = {partition.key: partition for partition in dataset.partitions}
    expected_by_path = {
        _partition_path(partition.key): partition for partition in dataset.partitions
    }
    expected_bound_paths = {_partition_path(key) for key in partition_bounds}
    expected_paths = {
        _MANIFEST_NAME,
        *{f"{_SOURCE_DIRECTORY}/{relative}" for relative in source_files},
    }
    previous_path = ""
    seen_keys: set[tuple[str, date]] = set()
    for item in raw_files:
        reference = _file_reference(item)
        relative_path = _string(reference["path"], "provider-history partition path")
        if previous_path and relative_path <= previous_path:
            raise ValueError("provider-history partition references are not canonical")
        previous_path = relative_path
        if relative_path not in expected_bound_paths:
            raise ValueError("provider-history partition path is absent from the source plan")
        partition_path = _safe_child(root, relative_path, "provider-history partition")
        partition_bytes = _read_bounded(partition_path, "provider-history partition")
        if sha256_bytes(partition_bytes) != str(reference["bytes_sha256"]):
            raise ValueError("provider-history partition bytes do not match its reference")
        if set(pl.read_parquet_schema(partition_path)) != set(_OBSERVATION_FIELDS):
            raise ValueError("provider-history partition columns are not exact")
        if _parquet_footer_row_count(partition_path) != _int(
            reference["row_count"], "provider-history partition row count"
        ):
            raise ValueError(
                "provider-history partition footer row count differs from its reference"
            )
        expected = expected_by_path.get(relative_path)
        if expected is None or expected.partition_sha256 != str(reference["partition_sha256"]):
            raise ValueError("provider-history partition identity differs from its dataset")
        key = expected.key
        if key in seen_keys:
            raise ValueError("provider-history partition references are duplicated")
        seen_keys.add(key)
        expected_paths.add(relative_path)
    if seen_keys != set(expected_partitions):
        raise ValueError("provider-history partition closure differs from its dataset")
    _require_exact_tree(root, expected_paths)
    return dataset


def provider_history_verifier_sha256() -> str:
    """Return the claim-scoped semantic verifier identity."""

    return _sha256_json(
        {
            "contract": _PROVIDER_HISTORY_VERIFIER_CONTRACT,
            "version": _PROVIDER_HISTORY_VERIFIER_VERSION,
            "completed_checks": list(_PROVIDER_HISTORY_COMPLETED_CHECKS),
        }
    )


def is_provider_history_v1_verifier_sha256_accepted(value: str) -> bool:
    return value in {
        provider_history_verifier_sha256(),
        _LEGACY_PROVIDER_HISTORY_VERIFIER_SHA256,
    }


def is_provider_history_verifier_sha256_accepted(value: str) -> bool:
    from qtrad.runtime.provider_history_v2 import provider_history_v2_verifier_sha256

    return (
        is_provider_history_v1_verifier_sha256_accepted(value)
        or value == provider_history_v2_verifier_sha256()
    )


def provider_history_source_verification_receipt(
    evidence: ProviderHistorySourceEvidence,
) -> dict[str, JsonValue]:
    """Encode compact results from one complete semantic source verification."""

    summary = evidence.observation_summary
    return {
        "contract": _SOURCE_VERIFICATION_RECEIPT_CONTRACT,
        "provider_verifier_sha256": provider_history_verifier_sha256(),
        "dataset_sha256": evidence.dataset.dataset_sha256,
        "request_evidence": [
            {
                "request_sha256": item.request_sha256,
                "result_sha256": item.result_sha256,
                "evidence_disposition": item.evidence_disposition.value,
                "accepted_row_count": item.accepted_row_count,
                "sessions": [dict(session) for session in item.sessions],
            }
            for item in evidence.request_evidence
        ],
        "observation_summary": (
            None
            if summary is None
            else {
                "accepted_intervals_by_request": [
                    {
                        "request_sha256": request_sha256,
                        "intervals": [
                            [start.isoformat(), end.isoformat()] for start, end in intervals
                        ],
                    }
                    for request_sha256, intervals in summary.accepted_intervals_by_request
                ],
                "source_start": summary.source_start.isoformat(),
                "source_end": summary.source_end.isoformat(),
            }
        ),
    }


def _receipt_timestamp(value: object, field: str) -> datetime:
    raw = _string(value, field)
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as error:
        raise ValueError(f"{field} is not an ISO-8601 timestamp") from error
    if parsed.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must be UTC")
    return parsed


def read_provider_history_source_verification_receipt(
    path: Path,
    receipt: Mapping[str, object],
    *,
    _verified_dataset: ProviderHistoricalDataset | None = None,
    _verified_observations: ProviderHistoryObservationRows | None = None,
    _verified_source_artifact: ProviderHistorySource | None = None,
    _selection: ProviderHistorySelection | None = None,
) -> ProviderHistorySourceEvidence:
    """Reauthenticate unchanged bytes and restore prior compact semantic results."""

    if (_verified_dataset is None) != (_verified_observations is None):
        raise ValueError("preverified provider-history dataset and rows must be supplied together")
    if _verified_dataset is None:
        from qtrad.runtime.provider_history_v2 import verify_provider_history_v2_file_only

        dataset = verify_provider_history_v2_file_only(path).dataset
    else:
        dataset = _verified_dataset
    _require_exact_keys(
        receipt,
        {
            "contract",
            "provider_verifier_sha256",
            "dataset_sha256",
            "request_evidence",
            "observation_summary",
        },
        "provider-history source-verification receipt",
    )
    if receipt["contract"] != _SOURCE_VERIFICATION_RECEIPT_CONTRACT:
        raise ValueError("provider-history source-verification receipt contract is unsupported")
    verifier_sha256 = _string(
        receipt["provider_verifier_sha256"],
        "provider-history receipt verifier identity",
    )
    _require_digest(verifier_sha256, "provider-history receipt verifier identity")
    if not is_provider_history_verifier_sha256_accepted(verifier_sha256):
        raise ValueError("provider-history source-verification receipt implementation changed")
    dataset_sha256 = _string(
        receipt["dataset_sha256"],
        "provider-history receipt dataset identity",
    )
    _require_digest(dataset_sha256, "provider-history receipt dataset identity")
    if dataset_sha256 != dataset.dataset_sha256:
        raise ValueError("provider-history source-verification receipt dataset changed")

    manifest_path = _require_file(path, "provider-history manifest")
    root = manifest_path.parent
    document = _mapping(
        _parse_json(
            _read_bounded(manifest_path, "provider-history manifest"),
            "provider-history manifest",
        ),
        "provider-history manifest",
    )
    source_reference = _source_reference(
        document["source_result"],
        allow_legacy_stage6=True,
    )
    source_path = _safe_child(root, str(source_reference["path"]), "source result")
    if _verified_source_artifact is None:
        source_bytes = _read_bounded(source_path, "provider-history source manifest")
        source_document = _mapping(
            _parse_json(source_bytes, "provider-history source manifest"),
            "provider-history source manifest",
        )
        source_stream = (
            _read_legacy_ibkr_historical_result_v2_header(source_path)
            if source_document["contract"] == _LEGACY_HISTORICAL_RESULT_V2_CONTRACT
            else verify_ibkr_historical_result_stream(source_path)
        )
    else:
        source_stream = _verified_source_artifact
    result_sha256_by_request: dict[str, str] = {}
    for reference in source_stream.aggregate.request_results:
        if not reference.path.startswith("requests/") or not reference.path.endswith(".json"):
            raise ValueError("embedded IBKR request-result path is not canonical")
        request_sha256 = reference.path[len("requests/") : -len(".json")]
        _require_digest(request_sha256, "embedded IBKR request identity")
        if reference.path != f"requests/{request_sha256}.json":
            raise ValueError("embedded IBKR request-result path is not canonical")
        result_sha256_by_request[request_sha256] = reference.semantic_sha256

    raw_evidence = receipt["request_evidence"]
    if not isinstance(raw_evidence, list) or len(raw_evidence) > MAX_IBKR_RESULT_CHILDREN:
        raise ValueError(
            "provider-history receipt request evidence is invalid or exceeds its bound"
        )
    request_evidence: list[ProviderHistoryRequestEvidence] = []
    seen_requests: set[str] = set()
    accepted_row_count = 0
    for position, raw_item in enumerate(raw_evidence):
        item = _mapping(raw_item, f"provider-history receipt request evidence {position}")
        _require_exact_keys(
            item,
            {
                "request_sha256",
                "result_sha256",
                "evidence_disposition",
                "accepted_row_count",
                "sessions",
            },
            f"provider-history receipt request evidence {position}",
        )
        request_sha256 = _string(
            item["request_sha256"],
            "provider-history receipt request identity",
        )
        result_sha256 = _string(
            item["result_sha256"],
            "provider-history receipt result identity",
        )
        _require_digest(request_sha256, "provider-history receipt request identity")
        _require_digest(result_sha256, "provider-history receipt result identity")
        if request_sha256 in seen_requests:
            raise ValueError("provider-history receipt request identities are duplicated")
        if result_sha256_by_request.get(request_sha256) != result_sha256:
            raise ValueError("provider-history receipt result identity differs from its source")
        row_count = _int(
            item["accepted_row_count"],
            "provider-history receipt accepted row count",
        )
        if row_count < 0:
            raise ValueError("provider-history receipt accepted row count is negative")
        raw_sessions = item["sessions"]
        if not isinstance(raw_sessions, list):
            raise TypeError("provider-history receipt sessions must be a list")
        sessions: list[dict[str, JsonValue]] = []
        for raw_session in raw_sessions:
            converted = to_json_value(raw_session)
            if not isinstance(converted, dict):
                raise TypeError("provider-history receipt session must be an object")
            sessions.append(converted)
        try:
            disposition = IbkrHistoricalEvidenceDisposition(
                _string(
                    item["evidence_disposition"],
                    "provider-history receipt evidence disposition",
                )
            )
        except ValueError as error:
            raise ValueError(
                "provider-history receipt evidence disposition is unsupported"
            ) from error
        request_evidence.append(
            ProviderHistoryRequestEvidence(
                request_sha256=request_sha256,
                result_sha256=result_sha256,
                evidence_disposition=disposition,
                accepted_row_count=row_count,
                sessions=tuple(sessions),
            )
        )
        seen_requests.add(request_sha256)
        accepted_row_count += row_count

    expected_requests = {request.request_sha256 for request in source_stream.plan.requests}
    if seen_requests != expected_requests or seen_requests != set(result_sha256_by_request):
        raise ValueError("provider-history receipt request closure differs from its source")
    if accepted_row_count != dataset.row_count:
        raise ValueError("provider-history receipt accepted row count differs from its dataset")

    raw_summary = receipt["observation_summary"]
    observation_summary: ProviderHistoryObservationSummary | None
    if raw_summary is None:
        if dataset.row_count != 0:
            raise ValueError("provider-history receipt observation summary is missing")
        observation_summary = None
    else:
        summary = _mapping(raw_summary, "provider-history receipt observation summary")
        _require_exact_keys(
            summary,
            {"accepted_intervals_by_request", "source_start", "source_end"},
            "provider-history receipt observation summary",
        )
        raw_entries = summary["accepted_intervals_by_request"]
        if not isinstance(raw_entries, list) or len(raw_entries) > len(expected_requests):
            raise ValueError("provider-history receipt observation intervals are invalid")
        accepted_intervals: list[tuple[str, tuple[tuple[datetime, datetime], ...]]] = []
        for position, raw_entry in enumerate(raw_entries):
            entry = _mapping(
                raw_entry,
                f"provider-history receipt observation intervals {position}",
            )
            _require_exact_keys(
                entry,
                {"request_sha256", "intervals"},
                f"provider-history receipt observation intervals {position}",
            )
            request_sha256 = _string(
                entry["request_sha256"],
                "provider-history receipt observation request identity",
            )
            if request_sha256 not in seen_requests:
                raise ValueError("provider-history receipt observation request is absent")
            raw_intervals = entry["intervals"]
            if not isinstance(raw_intervals, list):
                raise TypeError("provider-history receipt observation intervals must be a list")
            intervals: list[tuple[datetime, datetime]] = []
            for raw_interval in raw_intervals:
                if not isinstance(raw_interval, list) or len(raw_interval) != 2:
                    raise TypeError("provider-history receipt observation interval is invalid")
                intervals.append(
                    (
                        _receipt_timestamp(
                            raw_interval[0],
                            "provider-history receipt interval start",
                        ),
                        _receipt_timestamp(
                            raw_interval[1],
                            "provider-history receipt interval end",
                        ),
                    )
                )
            accepted_intervals.append((request_sha256, tuple(intervals)))
        observation_summary = ProviderHistoryObservationSummary(
            accepted_intervals_by_request=tuple(accepted_intervals),
            source_start=_receipt_timestamp(
                summary["source_start"],
                "provider-history receipt source start",
            ),
            source_end=_receipt_timestamp(
                summary["source_end"],
                "provider-history receipt source end",
            ),
        )

    observations = _verified_observations
    if observations is None:
        from qtrad.runtime.provider_history_v2 import provider_history_v2_rows

        observations = provider_history_v2_rows(path, dataset)
    return ProviderHistorySourceEvidence(
        dataset=dataset,
        observations=observations,
        source_artifact=source_stream,
        request_evidence=tuple(request_evidence),
        observation_summary=observation_summary,
        selection=_selection,
    )


def authenticate_provider_history(
    path: Path,
    *,
    receipt: Path,
    instrument_ids: Sequence[str] | None = None,
    interval_start: datetime | None = None,
    interval_end: datetime | None = None,
) -> ProviderHistoricalDataset | ProviderHistorySourceEvidence:
    """Authenticate current history or the retained v1 receipt without replay."""

    from qtrad.runtime.provider_history_v2 import (
        PROVIDER_HISTORICAL_OBSERVATIONS_V2_CONTRACT,
        authenticate_provider_history_v2,
    )

    contract = _provider_history_contract(path)
    if contract == PROVIDER_HISTORICAL_OBSERVATIONS_V2_CONTRACT:
        return authenticate_provider_history_v2(
            path,
            receipt=receipt,
            instrument_ids=instrument_ids,
            interval_start=interval_start,
            interval_end=interval_end,
        )
    if contract != PROVIDER_HISTORICAL_OBSERVATIONS_CONTRACT:
        raise ValueError("provider-history contract is unsupported")
    if any(value is not None for value in (instrument_ids, interval_start, interval_end)):
        raise ValueError("retained provider-history v1 evidence cannot supply rows")
    dataset = verify_provider_history_file_only(path)
    receipt_path = _require_file(receipt, "provider-history verification receipt")
    receipt_bytes = _read_bounded(receipt_path, "provider-history verification receipt")
    if len(receipt_bytes) > _MAX_VERIFICATION_RECEIPT_BYTES:
        raise ValueError("provider-history verification receipt exceeds its byte bound")
    document = _mapping(
        _parse_json(receipt_bytes, "provider-history verification receipt"),
        "provider-history verification receipt",
    )
    fields = {
        "contract",
        "schema_version",
        "provider_history_contract",
        "provider_history_schema_version",
        "provider_history_manifest_sha256",
        "provider_history_dataset_sha256",
        "stage6_plan_sha256",
        "stage6_runtime_sha256",
        "stage6_aggregate_sha256",
        "stage6_result_manifest_sha256",
        "availability_policy",
        "request_evidence",
        "observation_summary",
        "verifier_contract",
        "verifier_version",
        "verifier_identity",
        "completed_checks",
        "receipt_sha256",
    }
    _require_exact_keys(document, fields, "provider-history verification receipt")
    if receipt_bytes != canonical_json_bytes(cast(Mapping[str, JsonValue], document)):
        raise ValueError("provider-history verification receipt bytes are not canonical")
    identity = dict(document)
    receipt_sha256 = _string(identity.pop("receipt_sha256"), "provider-history receipt identity")
    if receipt_sha256 != _sha256_json(identity):
        raise ValueError("provider-history verification receipt identity does not match")
    if (
        document["contract"] != _PROVIDER_HISTORY_VERIFICATION_CONTRACT
        or document["schema_version"] != _PROVIDER_HISTORY_VERIFICATION_SCHEMA_VERSION
        or document["provider_history_contract"] != PROVIDER_HISTORICAL_OBSERVATIONS_CONTRACT
        or document["provider_history_schema_version"] != PROVIDER_HISTORY_SCHEMA_VERSION
        or document["verifier_contract"] != _PROVIDER_HISTORY_VERIFIER_CONTRACT
        or document["verifier_version"] != _PROVIDER_HISTORY_VERIFIER_VERSION
        or document["verifier_identity"] != provider_history_verifier_sha256()
        or document["completed_checks"] != list(_PROVIDER_HISTORY_COMPLETED_CHECKS)
    ):
        raise ValueError("provider-history verification receipt verifier is unsupported")

    manifest_path = _require_file(path, "provider-history manifest")
    manifest_bytes = _read_bounded(manifest_path, "provider-history manifest")
    manifest = _mapping(
        _parse_json(manifest_bytes, "provider-history manifest"),
        "provider-history manifest",
    )
    source_reference = _source_reference(
        manifest["source_result"],
        allow_legacy_stage6=True,
    )
    expected = {
        "provider_history_manifest_sha256": sha256_bytes(manifest_bytes),
        "provider_history_dataset_sha256": _mapping(
            manifest["dataset"], "provider-history dataset"
        )["dataset_sha256"],
        "stage6_plan_sha256": _mapping(manifest["dataset"], "provider-history dataset")[
            "plan_sha256"
        ],
        "stage6_runtime_sha256": _mapping(manifest["dataset"], "provider-history dataset")[
            "runtime_sha256"
        ],
        "stage6_aggregate_sha256": _mapping(manifest["dataset"], "provider-history dataset")[
            "aggregate_sha256"
        ],
        "stage6_result_manifest_sha256": source_reference["bytes_sha256"],
        "availability_policy": manifest["availability_policy"],
    }
    if any(document[field] != value for field, value in expected.items()):
        raise ValueError("provider-history verification receipt does not match its closure")

    return dataset
