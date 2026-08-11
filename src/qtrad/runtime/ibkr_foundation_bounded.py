"""Bounded provider-history foundation construction.

The general R1 builders intentionally retain immutable row tuples.  A full
provider-history acquisition is much larger, so Stage 8 uses this adapter to
replay the same row semantics partition-by-instrument while publishing the
same canonical child identities.  It never changes the small-fixture
application builders.
"""

from __future__ import annotations

import gc
import hashlib
import heapq
import inspect
import json
import resource
import shutil
import time
from collections import defaultdict, deque
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from multiprocessing import get_context
from pathlib import Path, PurePosixPath
from tempfile import mkdtemp
from types import SimpleNamespace
from typing import Any, cast

from qtrad.adapters.parquet.observations import _observation_from_row
from qtrad.application.foundation import (
    _log_ratio,
    _missing_panel_row,
    _panel_audit_disposition,
    _return_disposition,
    _revision_key,
    _source_key,
    observation_availability_time,
)
from qtrad.application.ibkr_foundation import (
    IBKRFoundationBuild,
    _adapt_observation,
    _provider_evidence,
    evaluate_ibkr_foundation_readiness,
)
from qtrad.application.provider_history import (
    ProviderHistoryObservationRows,
    ProviderHistorySourceEvidence,
)
from qtrad.application.walk_forward import _hash_json
from qtrad.domain.folds import FOLD_DATASET_CONTRACT, Fold, FoldDataset, membership_hash
from qtrad.domain.foundation import (
    PANEL_DATASET_CONTRACT,
    TARGET_DATASET_CONTRACT,
    ExcursionDisposition,
    FoundationConfig,
    InstrumentRole,
    PanelAuditDisposition,
    PanelDataset,
    PanelRow,
    PanelStatus,
    ReturnDisposition,
    TargetDataset,
    TargetRow,
)
from qtrad.domain.ibkr_foundation import IBKR_CONFIRMATORY_INSTRUMENTS
from qtrad.domain.market_data import PriceBasis
from qtrad.domain.provider_history import ProviderHistoricalObservation
from qtrad.domain.research import ObservationDataset, ObservationRow
from qtrad.runtime.provider_history import (
    provider_history_source_verification_receipt,
    read_provider_history_source_verification_receipt,
)

_CHECKPOINT_CONTRACT = "qtrad-stage8-foundation-checkpoint-v1"
_REPLAY_CHECKPOINT_CONTRACT = "qtrad-stage8-foundation-replay-checkpoint-v1"
_SOURCE_VERIFICATION_NAME = "source-verification.json"
_LEGACY_CHECKPOINT_IMPLEMENTATION_SHA256 = (
    "cc10c4ebdac5edef1880bbe9348d0220d5a715a3e21c5a26e6582154b42b35e8"
)
_LEGACY_CHECKPOINT_COMPATIBILITY_SHA256 = (
    "c7cc4139e11609280ce1458960d4aea0c66a2a455306210379864d26ef4b8951"
)
_MAX_SOURCE_VERIFICATION_BYTES = 64 * 1024 * 1024
_DERIVATION_CHUNK = timedelta(days=7)
_DEFAULT_WORKERS = 4
_MAX_WORKERS = 8
_ProgressCallback = Callable[[Mapping[str, object]], None]
_ReplayPartCallback = Callable[[str, int, Mapping[str, object], Path, Path], None]


@dataclass(frozen=True, slots=True)
class _Part:
    index: int
    relative_path: str
    file_sha256: str
    row_count: int
    rows_sha256: str


@dataclass(frozen=True, slots=True)
class _CheckpointFile:
    path: Path
    row_count: int
    bytes_sha256: str


@dataclass(frozen=True, slots=True)
class _ObservationCheckpoint:
    rows: _CheckpointFile
    source_start: datetime | None
    source_end: datetime | None


@dataclass(frozen=True, slots=True)
class _DerivedCheckpoint:
    panel: _CheckpointFile
    targets: _CheckpointFile
    summaries: _CheckpointFile


@dataclass(frozen=True, slots=True)
class _ReplayPart:
    path: Path
    file_sha256: str
    row_count: int
    rows_sha256: str


@dataclass(frozen=True, slots=True)
class _DerivationJob:
    instrument: str
    role: InstrumentRole
    observations: _CheckpointFile
    configuration: FoundationConfig
    active_intervals: Mapping[str, Sequence[tuple[datetime, datetime]]]
    panel_path: Path
    target_path: Path
    summary_path: Path


@dataclass(frozen=True, slots=True)
class _DerivationResult:
    instrument: str
    panel_row_count: int
    target_row_count: int


class _Progress:
    def __init__(self, callback: _ProgressCallback | None) -> None:
        self._callback = callback
        self._started = time.monotonic()

    def emit(self, phase: str, event: str, **fields: object) -> None:
        if self._callback is None:
            return
        usage = resource.getrusage(resource.RUSAGE_SELF)
        self._callback(
            {
                "contract": "qtrad-stage8-progress-v1",
                "phase": phase,
                "event": event,
                "elapsed_seconds": round(time.monotonic() - self._started, 3),
                "maximum_rss_kib": int(usage.ru_maxrss),
                **fields,
            }
        )


class _Stage8Checkpoint:
    """Disposable, identity-bound canonical per-instrument staging."""

    def __init__(
        self,
        root: Path | None,
        *,
        provider_manifest_sha256: str,
        provider_dataset_sha256: str,
        configuration_id: str,
    ) -> None:
        self.root = root
        if root is None:
            return
        identity = {
            "contract": _CHECKPOINT_CONTRACT,
            "provider_manifest_sha256": provider_manifest_sha256,
            "provider_dataset_sha256": provider_dataset_sha256,
            "configuration_id": configuration_id,
            "implementation_sha256": _implementation_sha256(),
        }
        encoded = _runtime()._json_bytes(identity) + b"\n"
        if root.exists():
            if root.is_symlink() or not root.is_dir():
                raise ValueError("Stage 8 checkpoint root must be a real directory")
            identity_path = root / "identity.json"
            if identity_path.exists():
                if not identity_path.is_file() or identity_path.is_symlink():
                    raise ValueError("Stage 8 checkpoint identity must be a regular file")
                actual_encoded = identity_path.read_bytes()
                try:
                    actual = json.loads(actual_encoded)
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise ValueError("Stage 8 checkpoint identity is invalid JSON") from error
                if (
                    not isinstance(actual, dict)
                    or actual_encoded != _runtime()._json_bytes(actual) + b"\n"
                    or not _checkpoint_identity_matches(actual, identity)
                ):
                    raise ValueError("Stage 8 checkpoint identity does not match this build")
            else:
                if any(root.iterdir()):
                    raise ValueError("Stage 8 checkpoint identity is missing")
                _runtime()._write_create_only(identity_path, encoded)
        else:
            root.mkdir(parents=True, exist_ok=False)
            _runtime()._write_create_only(root / "identity.json", encoded)

    def source_verification(self) -> dict[str, object] | None:
        if self.root is None:
            return None
        path = self.root / _SOURCE_VERIFICATION_NAME
        if not path.exists():
            return None
        if not path.is_file() or path.is_symlink():
            raise ValueError("Stage 8 source-verification checkpoint must be a regular file")
        if path.stat().st_size > _MAX_SOURCE_VERIFICATION_BYTES:
            raise ValueError("Stage 8 source-verification checkpoint exceeds its byte bound")
        encoded = path.read_bytes()
        try:
            value = json.loads(encoded)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("Stage 8 source-verification checkpoint is invalid JSON") from error
        if not isinstance(value, dict):
            raise ValueError("Stage 8 source-verification checkpoint must be an object")
        if encoded != _runtime()._json_bytes(value) + b"\n":
            raise ValueError("Stage 8 source-verification checkpoint is not canonical")
        return value

    def store_source_verification(self, receipt: Mapping[str, object]) -> None:
        if self.root is None:
            return
        encoded = _runtime()._json_bytes(receipt) + b"\n"
        if len(encoded) > _MAX_SOURCE_VERIFICATION_BYTES:
            raise ValueError("Stage 8 source-verification checkpoint exceeds its byte bound")
        path = self.root / _SOURCE_VERIFICATION_NAME
        if path.exists():
            if self.source_verification() != dict(receipt):
                raise ValueError("Stage 8 source-verification checkpoint content changed")
            return
        _runtime()._write_create_only(path, encoded)

    def observation(self, instrument: str) -> _ObservationCheckpoint | None:
        directory = self._instrument_directory("observations", instrument)
        if directory is None or not directory.exists():
            return None
        metadata = self._metadata(directory, instrument, "observations")
        rows = self._file(directory, metadata, "rows")
        start_value = metadata["source_start"]
        end_value = metadata["source_end"]
        source_start = datetime.fromisoformat(str(start_value)) if start_value else None
        source_end = datetime.fromisoformat(str(end_value)) if end_value else None
        if rows.row_count > 0 and (source_start is None or source_end is None):
            raise ValueError("Stage 8 observation checkpoint bounds are missing")
        if rows.row_count == 0 and (source_start is not None or source_end is not None):
            raise ValueError("Stage 8 empty observation checkpoint has bounds")
        if source_start is not None and source_end is not None and source_end <= source_start:
            raise ValueError("Stage 8 observation checkpoint bounds are invalid")
        return _ObservationCheckpoint(rows=rows, source_start=source_start, source_end=source_end)

    def store_observation(
        self,
        instrument: str,
        source: Path,
        *,
        source_start: datetime | None,
        source_end: datetime | None,
    ) -> _ObservationCheckpoint:
        if self.root is None:
            details = _checkpoint_file(source)
            return _ObservationCheckpoint(details, source_start, source_end)
        final = self._instrument_directory("observations", instrument)
        assert final is not None
        if final.exists():
            loaded = self.observation(instrument)
            if loaded is None:
                raise ValueError("Stage 8 observation checkpoint is incomplete")
            return loaded
        temporary = Path(mkdtemp(prefix=".observation-", dir=str(final.parent)))
        try:
            rows_path = temporary / "rows.jsonl"
            shutil.copyfile(source, rows_path)
            details = _checkpoint_file(rows_path)
            metadata = {
                "contract": _CHECKPOINT_CONTRACT,
                "kind": "observations",
                "instrument_id": instrument,
                "source_start": source_start.isoformat() if source_start else None,
                "source_end": source_end.isoformat() if source_end else None,
                "rows": _checkpoint_file_payload(details),
            }
            _runtime()._write_create_only(
                temporary / "complete.json", _runtime()._json_bytes(metadata) + b"\n"
            )
            temporary.rename(final)
            return _ObservationCheckpoint(
                rows=replace(details, path=final / "rows.jsonl"),
                source_start=source_start,
                source_end=source_end,
            )
        except BaseException:
            shutil.rmtree(temporary, ignore_errors=True)
            raise

    def derived(
        self,
        instrument: str,
        *,
        foundation_configuration_id: str,
    ) -> _DerivedCheckpoint | None:
        directory = self._instrument_directory(f"derived-{foundation_configuration_id}", instrument)
        if directory is None or not directory.exists():
            return None
        metadata = self._metadata(directory, instrument, "derived")
        if metadata["foundation_configuration_id"] != foundation_configuration_id:
            raise ValueError("Stage 8 derived checkpoint configuration changed")
        return _DerivedCheckpoint(
            panel=self._file(directory, metadata, "panel"),
            targets=self._file(directory, metadata, "targets"),
            summaries=self._file(directory, metadata, "summaries"),
        )

    def store_derived(
        self,
        instrument: str,
        *,
        foundation_configuration_id: str,
        panel: Path,
        targets: Path,
        summaries: Path,
    ) -> _DerivedCheckpoint:
        if self.root is None:
            return _DerivedCheckpoint(
                panel=_checkpoint_file(panel),
                targets=_checkpoint_file(targets),
                summaries=_checkpoint_file(summaries),
            )
        final = self._instrument_directory(f"derived-{foundation_configuration_id}", instrument)
        assert final is not None
        if final.exists():
            loaded = self.derived(
                instrument, foundation_configuration_id=foundation_configuration_id
            )
            if loaded is None:
                raise ValueError("Stage 8 derived checkpoint is incomplete")
            return loaded
        temporary = Path(mkdtemp(prefix=".derived-", dir=str(final.parent)))
        try:
            copied: dict[str, _CheckpointFile] = {}
            for name, source in (
                ("panel", panel),
                ("targets", targets),
                ("summaries", summaries),
            ):
                target = temporary / f"{name}.jsonl"
                shutil.copyfile(source, target)
                copied[name] = _checkpoint_file(target)
            metadata = {
                "contract": _CHECKPOINT_CONTRACT,
                "kind": "derived",
                "instrument_id": instrument,
                "foundation_configuration_id": foundation_configuration_id,
                **{name: _checkpoint_file_payload(details) for name, details in copied.items()},
            }
            _runtime()._write_create_only(
                temporary / "complete.json", _runtime()._json_bytes(metadata) + b"\n"
            )
            temporary.rename(final)
            return _DerivedCheckpoint(
                panel=replace(copied["panel"], path=final / "panel.jsonl"),
                targets=replace(copied["targets"], path=final / "targets.jsonl"),
                summaries=replace(copied["summaries"], path=final / "summaries.jsonl"),
            )
        except BaseException:
            shutil.rmtree(temporary, ignore_errors=True)
            raise

    def _instrument_directory(self, kind: str, instrument: str) -> Path | None:
        if self.root is None:
            return None
        parent = self.root / kind
        parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(instrument.encode("utf-8")).hexdigest()[:24]
        return parent / f"instrument-{digest}"

    @staticmethod
    def _metadata(directory: Path, instrument: str, kind: str) -> dict[str, object]:
        path = directory / "complete.json"
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Stage 8 {kind} checkpoint is incomplete")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"Stage 8 {kind} checkpoint metadata is invalid")
        if (
            value.get("contract") != _CHECKPOINT_CONTRACT
            or value.get("kind") != kind
            or value.get("instrument_id") != instrument
        ):
            raise ValueError(f"Stage 8 {kind} checkpoint identity is invalid")
        return value

    @staticmethod
    def _file(
        directory: Path,
        metadata: Mapping[str, object],
        name: str,
    ) -> _CheckpointFile:
        value = metadata.get(name)
        if not isinstance(value, Mapping):
            raise ValueError(f"Stage 8 checkpoint {name} metadata is invalid")
        path = directory / f"{name}.jsonl"
        details = _checkpoint_file(path)
        if _checkpoint_file_payload(details) != dict(value):
            raise ValueError(f"Stage 8 checkpoint {name} content changed")
        return details


def prepare_stage8_checkpoint(
    root: Path | None,
    *,
    provider_manifest_sha256: str,
    provider_dataset_sha256: str,
    configuration_id: str,
) -> None:
    """Initialize or authenticate build checkpoints before expensive source replay."""

    _Stage8Checkpoint(
        root,
        provider_manifest_sha256=provider_manifest_sha256,
        provider_dataset_sha256=provider_dataset_sha256,
        configuration_id=configuration_id,
    )


@dataclass(slots=True)
class _ObservationCaptureState:
    path: Path
    source_start: datetime | None = None
    source_end: datetime | None = None


class _Stage8ObservationCapture:
    """Stage authenticated rows for checkpoint publication after full verification."""

    def __init__(
        self,
        checkpoint: _Stage8Checkpoint,
        *,
        provider_dataset_sha256: str,
    ) -> None:
        if checkpoint.root is None:
            raise AssertionError("Stage 8 observation capture requires a checkpoint root")
        self._checkpoint = checkpoint
        self._provider_dataset_sha256 = provider_dataset_sha256
        self._temporary = Path(mkdtemp(prefix=".verified-observations-", dir=str(checkpoint.root)))
        self._states: dict[str, _ObservationCaptureState | None] = {}
        self._finished = False

    def add_partition(
        self,
        rows: tuple[ProviderHistoricalObservation, ...],
        row_offset: int,
    ) -> None:
        if self._finished:
            raise ValueError("Stage 8 observation capture is already finished")
        if not rows:
            return
        instrument = rows[0].instrument_id
        if any(row.instrument_id != instrument for row in rows):
            raise ValueError("verified provider partition contains multiple instruments")
        if instrument not in self._states:
            cached = self._checkpoint.observation(instrument)
            if cached is not None:
                self._states[instrument] = None
            else:
                digest = hashlib.sha256(instrument.encode("utf-8")).hexdigest()
                self._states[instrument] = _ObservationCaptureState(
                    path=self._temporary / f"{digest}.jsonl"
                )
        state = self._states[instrument]
        if state is None:
            return
        with state.path.open("a", encoding="utf-8") as stream:
            for position, provider_row in enumerate(rows, row_offset + 1):
                row = _adapt_observation(
                    provider_row,
                    self._provider_dataset_sha256,
                    position,
                )
                state.source_start = (
                    row.interval_start
                    if state.source_start is None
                    else min(state.source_start, row.interval_start)
                )
                state.source_end = (
                    row.interval_end
                    if state.source_end is None
                    else max(state.source_end, row.interval_end)
                )
                stream.write(_canonical_row(row.as_json()))
                stream.write("\n")

    def complete(self) -> None:
        if self._finished:
            raise ValueError("Stage 8 observation capture is already finished")
        try:
            for instrument, state in sorted(self._states.items()):
                if state is None:
                    continue
                self._checkpoint.store_observation(
                    instrument,
                    state.path,
                    source_start=state.source_start,
                    source_end=state.source_end,
                )
        finally:
            self._finished = True
            shutil.rmtree(self._temporary, ignore_errors=True)

    def abort(self) -> None:
        if self._finished:
            return
        self._finished = True
        shutil.rmtree(self._temporary, ignore_errors=True)


def prepare_stage8_observation_capture(
    root: Path,
    *,
    provider_manifest_sha256: str,
    provider_dataset_sha256: str,
    configuration_id: str,
) -> _Stage8ObservationCapture:
    """Authenticate a checkpoint and prepare fused verified-row capture."""

    checkpoint = _Stage8Checkpoint(
        root,
        provider_manifest_sha256=provider_manifest_sha256,
        provider_dataset_sha256=provider_dataset_sha256,
        configuration_id=configuration_id,
    )
    return _Stage8ObservationCapture(
        checkpoint,
        provider_dataset_sha256=provider_dataset_sha256,
    )


def read_stage8_source_verification(
    root: Path,
    *,
    provider_manifest: Path,
    provider_manifest_sha256: str,
    provider_dataset_sha256: str,
    configuration_id: str,
) -> ProviderHistorySourceEvidence | None:
    """Restore a completed semantic pass only after byte-level reauthentication."""

    checkpoint = _Stage8Checkpoint(
        root,
        provider_manifest_sha256=provider_manifest_sha256,
        provider_dataset_sha256=provider_dataset_sha256,
        configuration_id=configuration_id,
    )
    receipt = checkpoint.source_verification()
    if receipt is None:
        return None
    evidence = read_provider_history_source_verification_receipt(provider_manifest, receipt)
    observation_row_count = 0
    instruments = tuple(
        dict.fromkeys(partition.key[0] for partition in evidence.dataset.partitions)
    )
    for instrument in instruments:
        observation = checkpoint.observation(instrument)
        if observation is None:
            raise ValueError("Stage 8 source-verification checkpoint observations are incomplete")
        observation_row_count += observation.rows.row_count
    if observation_row_count != evidence.dataset.row_count:
        raise ValueError(
            "Stage 8 source-verification checkpoint observation count differs from its dataset"
        )
    return evidence


def store_stage8_source_verification(
    root: Path,
    *,
    source_evidence: ProviderHistorySourceEvidence,
    provider_manifest_sha256: str,
    provider_dataset_sha256: str,
    configuration_id: str,
) -> None:
    """Publish a receipt only after semantic verification and observation capture complete."""

    checkpoint = _Stage8Checkpoint(
        root,
        provider_manifest_sha256=provider_manifest_sha256,
        provider_dataset_sha256=provider_dataset_sha256,
        configuration_id=configuration_id,
    )
    checkpoint.store_source_verification(
        provider_history_source_verification_receipt(source_evidence)
    )


class _ReplayCheckpoint:
    """Identity-bound cache of child parts already matched to one published bundle."""

    def __init__(
        self,
        root: Path | None,
        *,
        provider_manifest_sha256: str,
        provider_dataset_sha256: str,
        configuration_id: str,
        published_bundle_sha256: str,
    ) -> None:
        self.root = root
        if root is None:
            return
        identity = {
            "contract": _REPLAY_CHECKPOINT_CONTRACT,
            "provider_manifest_sha256": provider_manifest_sha256,
            "provider_dataset_sha256": provider_dataset_sha256,
            "configuration_id": configuration_id,
            "implementation_sha256": _implementation_sha256(),
            "published_bundle_sha256": published_bundle_sha256,
        }
        encoded = _runtime()._json_bytes(identity) + b"\n"
        if root.exists():
            if root.is_symlink() or not root.is_dir():
                raise ValueError("Stage 8 replay checkpoint root must be a real directory")
            identity_path = root / "identity.json"
            if not identity_path.is_file() or identity_path.is_symlink():
                raise ValueError("Stage 8 replay checkpoint identity is missing")
            if identity_path.read_bytes() != encoded:
                raise ValueError(
                    "Stage 8 replay checkpoint identity does not match this verification"
                )
        else:
            root.mkdir(parents=True, exist_ok=False)
            _runtime()._write_create_only(root / "identity.json", encoded)

    def part(
        self,
        kind: str,
        index: int,
        *,
        row_count: int,
        rows_sha256: str,
    ) -> _ReplayPart | None:
        directory = self._part_directory(kind, index)
        if directory is None or not directory.exists():
            return None
        metadata_path = directory / "complete.json"
        part_path = directory / "part.parquet"
        if (
            not metadata_path.is_file()
            or metadata_path.is_symlink()
            or not part_path.is_file()
            or part_path.is_symlink()
        ):
            raise ValueError(f"Stage 8 replay checkpoint {kind} part {index} is incomplete")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict):
            raise ValueError(f"Stage 8 replay checkpoint {kind} part {index} metadata is invalid")
        file_sha256 = hashlib.sha256(part_path.read_bytes()).hexdigest()
        expected = {
            "contract": _REPLAY_CHECKPOINT_CONTRACT,
            "kind": kind,
            "part_index": index,
            "row_count": row_count,
            "rows_sha256": rows_sha256,
            "file_sha256": file_sha256,
        }
        if metadata != expected:
            raise ValueError(f"Stage 8 replay checkpoint {kind} part {index} content changed")
        return _ReplayPart(part_path, file_sha256, row_count, rows_sha256)

    def store_verified(
        self,
        kind: str,
        index: int,
        *,
        row_count: int,
        rows_sha256: str,
        generated_path: Path,
    ) -> None:
        if self.root is None:
            return
        directory = self._part_directory(kind, index)
        assert directory is not None
        file_sha256 = hashlib.sha256(generated_path.read_bytes()).hexdigest()
        if directory.exists():
            cached = self.part(
                kind,
                index,
                row_count=row_count,
                rows_sha256=rows_sha256,
            )
            if cached is None or cached.file_sha256 != file_sha256:
                raise ValueError(f"Stage 8 replay checkpoint {kind} part {index} diverged")
            return
        temporary = Path(mkdtemp(prefix=".part-", dir=str(directory.parent)))
        try:
            target = temporary / "part.parquet"
            shutil.copyfile(generated_path, target)
            metadata = {
                "contract": _REPLAY_CHECKPOINT_CONTRACT,
                "kind": kind,
                "part_index": index,
                "row_count": row_count,
                "rows_sha256": rows_sha256,
                "file_sha256": file_sha256,
            }
            _runtime()._write_create_only(
                temporary / "complete.json", _runtime()._json_bytes(metadata) + b"\n"
            )
            temporary.rename(directory)
        except BaseException:
            shutil.rmtree(temporary, ignore_errors=True)
            raise

    def _part_directory(self, kind: str, index: int) -> Path | None:
        if self.root is None:
            return None
        parent = self.root / kind
        parent.mkdir(parents=True, exist_ok=True)
        return parent / f"part-{index:06d}"


def _implementation_sha256() -> str:
    hasher = hashlib.sha256()
    hasher.update(Path(__file__).read_bytes())
    for function in (
        _adapt_observation,
        _observation_from_row,
        _panel_audit_disposition,
        _provider_evidence,
        _revision_key,
        _source_key,
        _hash_json,
        membership_hash,
    ):
        hasher.update(inspect.getsource(function).encode("utf-8"))
    return hasher.hexdigest()


def _legacy_checkpoint_compatibility_sha256() -> str:
    source = Path(__file__).read_text(encoding="utf-8")
    lines = source.splitlines()
    start = next(
        index
        for index, line in enumerate(lines)
        if line.startswith("_LEGACY_CHECKPOINT_COMPATIBILITY_SHA256 =")
    )
    end = start + 1
    while not lines[end].endswith(")"):
        end += 1
    lines[start : end + 1] = ["_LEGACY_CHECKPOINT_COMPATIBILITY_SHA256 = <bound>"]
    hasher = hashlib.sha256(("\n".join(lines) + "\n").encode("utf-8"))
    for function in (
        _adapt_observation,
        _observation_from_row,
        _panel_audit_disposition,
        _provider_evidence,
        _revision_key,
        _source_key,
        _hash_json,
        membership_hash,
    ):
        hasher.update(inspect.getsource(function).encode("utf-8"))
    return hasher.hexdigest()


def _checkpoint_identity_matches(
    actual: Mapping[str, object], expected: Mapping[str, object]
) -> bool:
    if actual == expected:
        return True
    legacy = dict(expected)
    legacy["implementation_sha256"] = _LEGACY_CHECKPOINT_IMPLEMENTATION_SHA256
    return (
        actual == legacy
        and _legacy_checkpoint_compatibility_sha256() == _LEGACY_CHECKPOINT_COMPATIBILITY_SHA256
    )


def _checkpoint_file(path: Path) -> _CheckpointFile:
    if not path.is_file() or path.is_symlink():
        raise ValueError("Stage 8 checkpoint file is missing")
    hasher = hashlib.sha256()
    row_count = 0
    with path.open("rb") as stream:
        for line in stream:
            if not line.endswith(b"\n") or line == b"\n":
                raise ValueError("Stage 8 checkpoint JSONL is not canonical")
            hasher.update(line)
            row_count += 1
    return _CheckpointFile(path=path, row_count=row_count, bytes_sha256=hasher.hexdigest())


def _checkpoint_file_payload(details: _CheckpointFile) -> dict[str, object]:
    return {
        "row_count": details.row_count,
        "bytes_sha256": details.bytes_sha256,
    }


def _checkpoint_payloads(details: _CheckpointFile) -> Iterator[str]:
    with details.path.open("r", encoding="utf-8") as stream:
        for line in stream:
            payload = line.rstrip("\n")
            if payload:
                yield payload


def _dataset_id_from_checkpoint(
    metadata: Mapping[str, object],
    details: _CheckpointFile,
) -> str:
    hasher = _StreamingRowsHash(metadata)
    for payload in _checkpoint_payloads(details):
        hasher.add(payload)
    return hasher.finish()


class _StreamingRowsHash:
    """Hash a canonical object whose rows array is supplied incrementally."""

    def __init__(self, metadata: Mapping[str, object]) -> None:
        runtime = _runtime()
        template = runtime._json_bytes({**metadata, "rows": []})
        marker = b"[]"
        marker_index = template.find(marker)
        if marker_index < 0:
            raise AssertionError("canonical rows placeholder was not found")
        self._hasher = __import__("hashlib").sha256()
        self._hasher.update(template[:marker_index] + b"[")
        self._suffix = template[marker_index + len(marker) :]
        self._count = 0

    def add(self, payload: str) -> None:
        if self._count:
            self._hasher.update(b",")
        self._hasher.update(payload.encode("utf-8"))
        self._count += 1

    def finish(self) -> str:
        self._hasher.update(b"]")
        self._hasher.update(self._suffix)
        return self._hasher.hexdigest()


class _DeferredChildWriter:
    """Write bounded Parquet parts before the final dataset identity is known."""

    def __init__(
        self,
        *,
        child_root: Path,
        bundle_root: Path,
        child_name: str,
        kind: str,
        lineage: Mapping[str, object],
        replay_checkpoint: _ReplayCheckpoint | None = None,
        part_callback: _ReplayPartCallback | None = None,
    ) -> None:
        self.child_root = child_root
        self.bundle_root = bundle_root
        self.child_name = child_name
        self.kind = kind
        self.lineage = dict(lineage)
        self.replay_checkpoint = replay_checkpoint
        self.part_callback = part_callback
        self.parts: list[_Part] = []
        self._payloads: list[str] = []
        self._payload_bytes = 0
        self._finalized = False

    def add(self, payload: str) -> None:
        if self._finalized:
            raise RuntimeError("child writer is already finalized")
        runtime = _runtime()
        payload_bytes = runtime._payload_byte_count(payload)
        if self._payloads and (
            len(self._payloads) >= runtime._MAX_CHILD_ROWS
            or self._payload_bytes + payload_bytes > runtime._MAX_CHILD_PAYLOAD_BYTES
        ):
            self._flush()
        self._payloads.append(payload)
        self._payload_bytes += payload_bytes
        if len(self._payloads) >= runtime._MAX_CHILD_ROWS:
            self._flush()

    def close(self) -> None:
        if self._payloads or not self.parts:
            self._flush()

    def _flush(self) -> None:
        runtime = _runtime()
        index = len(self.parts)
        payloads = tuple(self._payloads)
        rows_sha256 = runtime._sha(list(payloads))
        cached = (
            self.replay_checkpoint.part(
                self.kind,
                index,
                row_count=len(payloads),
                rows_sha256=rows_sha256,
            )
            if self.replay_checkpoint is not None
            else None
        )
        if cached is None:
            data = runtime._parquet_bytes(payloads)
            if not data or len(data) > runtime._MAX_CHILD_FILE_BYTES:
                raise ValueError("IBKR foundation Parquet child exceeds its byte bound")
            file_sha256 = hashlib.sha256(data).hexdigest()
        else:
            data = None
            file_sha256 = cached.file_sha256
        relative = (
            f"{self.child_name}/parquet/{self.kind}/part-{index:06d}-{file_sha256[:24]}.parquet"
        )
        path = self.bundle_root / PurePosixPath(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        if cached is None:
            assert data is not None
            runtime._write_create_only(path, data)
        else:
            shutil.copyfile(cached.path, path)
        self.parts.append(
            _Part(
                index=index,
                relative_path=relative,
                file_sha256=file_sha256,
                row_count=len(payloads),
                rows_sha256=rows_sha256,
            )
        )
        self._payloads.clear()
        self._payload_bytes = 0

    def finalize(self, dataset_id: str) -> tuple[dict[str, object], ...]:
        if self._finalized:
            raise RuntimeError("child writer is already finalized")
        self.close()
        runtime = _runtime()
        references: list[dict[str, object]] = []
        for part in self.parts:
            identity = {
                "contract": runtime._FOUNDATION_CHILD_CONTRACT,
                "schema_version": runtime._FOUNDATION_CHILD_SCHEMA_VERSION,
                "kind": self.kind,
                "dataset_id": dataset_id,
                "part_index": part.index,
                "row_count": part.row_count,
                "file": part.relative_path,
                "file_sha256": part.file_sha256,
                "rows_sha256": part.rows_sha256,
                "lineage": self.lineage,
            }
            manifest_sha256 = runtime._sha(identity)
            manifest = {**identity, "manifest_sha256": manifest_sha256}
            relative_manifest = (
                f"{self.child_name}/manifests/{self.kind}/"
                f"part-{part.index:06d}-{manifest_sha256[:24]}.json"
            )
            encoded = runtime._json_bytes(manifest) + b"\n"
            if len(encoded) > runtime._MAX_CHILD_MANIFEST_BYTES:
                raise ValueError("IBKR foundation child manifest exceeds the 4 MiB limit")
            manifest_path = self.bundle_root / PurePosixPath(relative_manifest)
            runtime._write_create_only(manifest_path, encoded)
            reference = {
                "kind": self.kind,
                "dataset_id": dataset_id,
                "manifest_id": manifest_sha256[:24],
                "manifest_path": relative_manifest,
                "manifest_sha256": manifest_sha256,
                "row_count": part.row_count,
                "file": part.relative_path,
                "file_sha256": part.file_sha256,
            }
            references.append(reference)
            if self.part_callback is not None:
                self.part_callback(
                    self.kind,
                    part.index,
                    reference,
                    manifest_path,
                    self.bundle_root / PurePosixPath(part.relative_path),
                )
        self._finalized = True
        return tuple(references)


def _runtime() -> Any:
    # Keep runtime imports lazy: runtime.ibkr_foundation imports this module.
    from qtrad.runtime import ibkr_foundation

    return ibkr_foundation


def _canonical_row(value: Mapping[str, object]) -> str:
    return _runtime()._canonical_row(value)


def _observation_metadata(
    configuration: Mapping[str, object],
    *,
    source_dataset_id: str,
    selection_policies: Mapping[str, object],
) -> dict[str, object]:
    return {
        "contract": ObservationRow.CONTRACT,
        "schema_version": ObservationRow.SCHEMA_VERSION,
        "configuration": configuration,
        "source_dataset_ids": [source_dataset_id],
        "selection_policies": selection_policies,
    }


def _foundation_child_lineage(
    source_evidence: ProviderHistorySourceEvidence,
    provider_manifest_sha256: str,
) -> dict[str, object]:
    return {
        "provider_manifest_sha256": provider_manifest_sha256,
        "provider_dataset_sha256": source_evidence.dataset.dataset_sha256,
        "plan_sha256": source_evidence.source_artifact.plan.plan_sha256,
        "aggregate_sha256": source_evidence.source_artifact.aggregate.aggregate_sha256,
    }


def _sorted_groups(
    rows: Iterable[ProviderHistoricalObservation],
) -> dict[str, tuple[tuple[ProviderHistoricalObservation, int], ...]]:
    grouped: dict[str, list[tuple[ProviderHistoricalObservation, int]]] = defaultdict(list)
    previous_key: tuple[str, datetime, datetime] | None = None
    for position, row in enumerate(rows, 1):
        key = (row.instrument_id, row.interval_start, row.interval_end)
        if previous_key is not None and key < previous_key:
            ordered = sorted(
                rows,
                key=lambda item: (
                    item.instrument_id,
                    item.interval_start,
                    item.interval_end,
                ),
            )
            grouped = defaultdict(list)
            for position, item in enumerate(ordered, 1):
                grouped[item.instrument_id].append((item, position))
            break
        grouped[row.instrument_id].append((row, position))
        previous_key = key
    return {instrument: tuple(values) for instrument, values in grouped.items()}


def _adapted_rows(
    grouped: Mapping[str, Sequence[tuple[ProviderHistoricalObservation, int]]],
    instrument: str,
    source_dataset_id: str,
) -> tuple[ObservationRow, ...]:
    return tuple(
        _adapt_observation(row, source_dataset_id, position)
        for row, position in grouped.get(instrument, ())
    )


def _provider_instruments(
    rows: ProviderHistoryObservationRows,
) -> tuple[str, ...]:
    instruments = getattr(rows, "instruments", None)
    if isinstance(instruments, tuple) and all(
        isinstance(instrument, str) for instrument in instruments
    ):
        return instruments
    return tuple(sorted({row.instrument_id for row in rows}))


def _adapted_instrument_rows(
    rows: ProviderHistoryObservationRows,
    grouped: Mapping[str, Sequence[tuple[ProviderHistoricalObservation, int]]] | None,
    instrument: str,
    source_dataset_id: str,
) -> Iterator[ObservationRow]:
    positioned = cast(
        Callable[[str], Iterator[tuple[ProviderHistoricalObservation, int]]] | None,
        getattr(rows, "iter_instrument_with_positions", None),
    )
    if positioned is not None:
        return (
            _adapt_observation(row, source_dataset_id, position)
            for row, position in positioned(instrument)
        )
    if grouped is None:
        raise AssertionError("provider-history rows require a grouped fallback")
    return iter(_adapted_rows(grouped, instrument, source_dataset_id))


def _local_observation_configuration(
    global_configuration: Mapping[str, object],
    instrument: str,
) -> dict[str, object]:
    result = dict(global_configuration)
    result["ordered_instruments"] = [instrument]
    return result


def _local_foundation_configuration(
    configuration: FoundationConfig,
    *,
    instrument: str,
    role: InstrumentRole,
    observation_dataset_id: str,
) -> FoundationConfig:
    return replace(
        configuration,
        ordered_instruments=(instrument,),
        instrument_roles={instrument: role},
        observation_dataset_id=observation_dataset_id,
    )


def _dataset_id(
    metadata: Mapping[str, object],
    rows: Iterable[Mapping[str, object]],
) -> str:
    hasher = _StreamingRowsHash(metadata)
    for row in rows:
        hasher.add(_canonical_row(row))
    return hasher.finish()


def _observation_checkpoint_rows(details: _CheckpointFile) -> Iterator[ObservationRow]:
    with details.path.open("r", encoding="utf-8") as stream:
        for line in stream:
            value = json.loads(line)
            if not isinstance(value, Mapping):
                raise ValueError("Stage 8 observation checkpoint row is invalid")
            yield _observation_from_row(cast(Mapping[str, object], value))


def _derivation_chunks(
    details: _CheckpointFile,
    config: FoundationConfig,
) -> Iterator[tuple[datetime, datetime, tuple[ObservationRow, ...]]]:
    if tuple(config.required_feature_bases) != (config.target_basis,):
        raise ValueError("bounded Stage 8 requires one shared panel and target price basis")
    rows = iter(_observation_checkpoint_rows(details))
    buffered: deque[ObservationRow] = deque()
    pending = next(rows, None)
    previous_end: datetime | None = None
    maximum_horizon = max(config.target_horizons, default=timedelta(0))
    chunk_start = config.range_start
    while chunk_start < config.range_end:
        chunk_end = min(chunk_start + _DERIVATION_CHUNK, config.range_end)
        window_start = chunk_start - config.selected_feature_lag
        window_end = chunk_end + maximum_horizon
        while buffered and buffered[0].interval_end < window_start:
            buffered.popleft()
        while pending is not None and pending.interval_end <= window_end:
            if previous_end is not None and pending.interval_end < previous_end:
                raise ValueError("Stage 8 observation checkpoint is not chronological")
            previous_end = pending.interval_end
            if pending.interval_end >= window_start:
                buffered.append(pending)
            pending = next(rows, None)
        yield chunk_start, chunk_end, tuple(buffered)
        chunk_start = chunk_end


def _stream_panel_rows(
    rows: Sequence[ObservationRow],
    config: FoundationConfig,
    *,
    instrument: str,
    range_start: datetime,
    range_end: datetime,
    active_intervals: Mapping[str, Sequence[tuple[datetime, datetime]]],
) -> Iterator[PanelRow]:
    index: dict[tuple[PriceBasis, datetime], list[ObservationRow]] = defaultdict(list)
    for row in rows:
        if row.instrument_id == instrument:
            index[(row.basis, row.interval_end)].append(row)
    decision_time = range_start
    while decision_time < range_end:
        feature_data_asof = decision_time
        latest_feature_bar_end = decision_time - config.selected_feature_lag
        interval_start = latest_feature_bar_end - config.grid_resolution
        for basis in config.required_feature_bases:
            candidates = tuple(index.get((basis, latest_feature_bar_end), ()))
            eligible = tuple(
                row
                for row in candidates
                if observation_availability_time(row, config.availability_basis)
                <= feature_data_asof
            )
            source_keys = {_source_key(row) for row in eligible}
            if len(source_keys) > 1:
                yield _missing_panel_row(
                    decision_time=decision_time,
                    instrument_id=instrument,
                    basis=basis,
                    feature_data_asof=feature_data_asof,
                    latest_feature_bar_end=latest_feature_bar_end,
                    audit_disposition=PanelAuditDisposition.AMBIGUOUS_OR_INVALID_SOURCE,
                )
                continue
            selected = max(eligible, key=_revision_key, default=None)
            if selected is None:
                yield _missing_panel_row(
                    decision_time=decision_time,
                    instrument_id=instrument,
                    basis=basis,
                    feature_data_asof=feature_data_asof,
                    latest_feature_bar_end=latest_feature_bar_end,
                    audit_disposition=_panel_audit_disposition(
                        candidates=candidates,
                        instrument_id=instrument,
                        interval_start=interval_start,
                        interval_end=latest_feature_bar_end,
                        feature_data_asof=feature_data_asof,
                        gaps=(),
                        source_active_intervals=active_intervals,
                    ),
                )
                continue
            yield PanelRow(
                decision_time=decision_time,
                instrument_id=instrument,
                basis=basis,
                feature_data_asof=feature_data_asof,
                latest_feature_bar_end=latest_feature_bar_end,
                status=PanelStatus.OBSERVED,
                audit_disposition=None,
                selected_event_id=selected.event_id,
                selected_stream_version=selected.stream_version,
                selected_global_position=selected.global_position,
                selected_availability_time=observation_availability_time(
                    selected, config.availability_basis
                ),
                selected_revision=selected.revision,
                interval_start=selected.interval_start,
                interval_end=selected.interval_end,
                open=selected.open,
                high=selected.high,
                low=selected.low,
                close=selected.close,
                sample_count=selected.sample_count,
                quality=selected.quality,
            )
        decision_time += config.grid_resolution


def _write_instrument_derivatives(
    details: _CheckpointFile,
    config: FoundationConfig,
    *,
    instrument: str,
    role: InstrumentRole,
    active_intervals: Mapping[str, Sequence[tuple[datetime, datetime]]],
    panel_path: Path,
    target_path: Path,
    summary_path: Path,
) -> tuple[int, int]:
    panel_count = 0
    target_count = 0
    with (
        panel_path.open("w", encoding="utf-8") as panel_stream,
        target_path.open("w", encoding="utf-8") as target_stream,
        summary_path.open("w", encoding="utf-8") as summary_stream,
    ):
        for chunk_start, chunk_end, rows in _derivation_chunks(details, config):
            for row in _stream_panel_rows(
                rows,
                config,
                instrument=instrument,
                range_start=chunk_start,
                range_end=chunk_end,
                active_intervals=active_intervals,
            ):
                panel_stream.write(_canonical_row(row.as_json()))
                panel_stream.write("\n")
                panel_count += 1
            if role is not InstrumentRole.TARGET:
                continue
            target_rows = _fast_target_rows(
                rows,
                config,
                horizons=config.target_horizons,
                range_start=chunk_start,
                range_end=chunk_end,
            )
            if target_rows is None:
                raise ValueError("bounded Stage 8 target observations are not singleton bars")
            for row in target_rows:
                payload = _canonical_row(row.as_json())
                target_stream.write(payload)
                target_stream.write("\n")
                target_count += 1
                if row.horizon == config.primary_vertical_horizon:
                    summary_stream.write(_canonical_row(_TargetSummary(row).as_json_value()))
                    summary_stream.write("\n")
    return panel_count, target_count


def _derive_instrument(job: _DerivationJob) -> _DerivationResult:
    panel_count, target_count = _write_instrument_derivatives(
        job.observations,
        job.configuration,
        instrument=job.instrument,
        role=job.role,
        active_intervals=job.active_intervals,
        panel_path=job.panel_path,
        target_path=job.target_path,
        summary_path=job.summary_path,
    )
    return _DerivationResult(job.instrument, panel_count, target_count)


def _validated_workers(workers: int) -> int:
    if isinstance(workers, bool) or not 1 <= workers <= _MAX_WORKERS:
        raise ValueError(f"Stage 8 workers must be between 1 and {_MAX_WORKERS}")
    return workers


class _SummaryRows:
    def __init__(self, path: Path, total_count: int) -> None:
        self._path = path
        self._total_count = total_count

    def __iter__(self) -> Iterator[object]:
        with self._path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    yield _TargetSummary.from_json_value(json.loads(line))

    def __len__(self) -> int:
        return self._total_count


class _TargetSummary:
    __slots__ = (
        "decision_time",
        "horizon",
        "instrument_id",
        "return_disposition",
        "target_available_at",
        "target_basis",
        "target_end_time",
        "target_freeze_at",
        "target_id",
        "target_start_time",
    )

    def __init__(self, row: TargetRow) -> None:
        self.instrument_id = row.instrument_id
        self.decision_time = row.decision_time
        self.horizon = row.horizon
        self.target_basis = row.target_basis
        self.target_id = row.target_id
        self.target_start_time = row.target_start_time
        self.target_end_time = row.target_end_time
        self.target_freeze_at = row.target_freeze_at
        self.target_available_at = row.target_available_at
        self.return_disposition = row.return_disposition

    def as_json_value(self) -> dict[str, object]:
        return {
            "decision_time": self.decision_time.isoformat(),
            "horizon_seconds": self.horizon.total_seconds(),
            "instrument_id": self.instrument_id,
            "return_disposition": self.return_disposition.value,
            "target_available_at": self.target_available_at.isoformat(),
            "target_basis": self.target_basis.value,
            "target_end_time": self.target_end_time.isoformat(),
            "target_freeze_at": self.target_freeze_at.isoformat(),
            "target_id": self.target_id,
            "target_start_time": self.target_start_time.isoformat(),
        }

    @classmethod
    def from_json_value(cls, value: Mapping[str, object]) -> _TargetSummary:
        summary = cls.__new__(cls)
        summary.instrument_id = str(value["instrument_id"])
        summary.decision_time = datetime.fromisoformat(str(value["decision_time"]))
        summary.horizon = timedelta(
            seconds=float(cast(str | int | float, value["horizon_seconds"]))
        )
        summary.target_basis = PriceBasis(str(value["target_basis"]))
        summary.target_id = str(value["target_id"])
        summary.target_start_time = datetime.fromisoformat(str(value["target_start_time"]))
        summary.target_end_time = datetime.fromisoformat(str(value["target_end_time"]))
        summary.target_freeze_at = datetime.fromisoformat(str(value["target_freeze_at"]))
        summary.target_available_at = datetime.fromisoformat(str(value["target_available_at"]))
        summary.return_disposition = ReturnDisposition(str(value["return_disposition"]))
        return summary


def _fast_target_rows(
    rows: Sequence[ObservationRow],
    config: FoundationConfig,
    *,
    horizons: Sequence[timedelta],
    range_start: datetime | None = None,
    range_end: datetime | None = None,
) -> Iterator[TargetRow] | None:
    """Build singleton historical targets with rolling path extrema.

    A source with revisions must use the general builder.  The Stage 7
    provider source has one immutable MID observation per minute, allowing
    exact endpoint lookups and linear-time rolling path calculations.
    """
    target_instruments = tuple(
        instrument_id
        for instrument_id in config.ordered_instruments
        if InstrumentRole(config.instrument_roles[instrument_id]) is InstrumentRole.TARGET
    )
    if len(target_instruments) != 1:
        return None
    if not horizons:
        return iter(())

    target_instrument = target_instruments[0]
    rows_by_time: dict[datetime, ObservationRow] = {}
    for row in rows:
        if row.instrument_id != target_instrument or row.basis is not config.target_basis:
            continue
        if row.interval_end in rows_by_time:
            return None
        rows_by_time[row.interval_end] = row

    decision_times: list[datetime] = []
    effective_range_start = config.range_start if range_start is None else range_start
    effective_range_end = config.range_end if range_end is None else range_end
    current = effective_range_start
    while current < effective_range_end:
        decision_times.append(current)
        current += config.grid_resolution
    if not decision_times:
        return iter(())
    resolution = config.grid_resolution.total_seconds()
    if resolution <= 0 or not resolution.is_integer():
        return None
    resolution_steps = int(resolution)

    horizon_steps: list[int] = []
    for horizon in horizons:
        seconds = horizon.total_seconds()
        if seconds <= 0 or not seconds.is_integer() or int(seconds) % resolution_steps:
            return None
        horizon_steps.append(int(seconds) // resolution_steps)

    maximum_end = decision_times[-1] + max(horizons)
    total_steps = int((maximum_end - effective_range_start).total_seconds()) // resolution_steps
    values = [
        rows_by_time.get(effective_range_start + index * config.grid_resolution)
        for index in range(total_steps + 1)
    ]
    missing_prefix = [0]
    bad_high_prefix = [0]
    bad_low_prefix = [0]
    for row in values:
        missing_prefix.append(missing_prefix[-1] + (row is None))
        bad_high_prefix.append(
            bad_high_prefix[-1] + (row is not None and (row.high <= 0 or not row.high.is_finite()))
        )
        bad_low_prefix.append(
            bad_low_prefix[-1] + (row is not None and (row.low <= 0 or not row.low.is_finite()))
        )

    metric_sets: list[
        tuple[
            list[Decimal | None],
            list[Decimal | None],
            list[datetime | None],
            list[bool],
            list[bool],
        ]
    ] = []
    for steps in horizon_steps:
        max_high: deque[tuple[int, Decimal]] = deque()
        min_low: deque[tuple[int, Decimal]] = deque()
        max_available: deque[tuple[int, datetime]] = deque()
        highs: list[Decimal | None] = [None] * len(decision_times)
        lows: list[Decimal | None] = [None] * len(decision_times)
        availabilities: list[datetime | None] = [None] * len(decision_times)
        missing: list[bool] = [False] * len(decision_times)
        bad: list[bool] = [False] * len(decision_times)
        for right in range(1, len(values)):
            row = values[right]
            if row is not None:
                while max_high and max_high[-1][1] <= row.high:
                    max_high.pop()
                max_high.append((right, row.high))
                while min_low and min_low[-1][1] >= row.low:
                    min_low.pop()
                min_low.append((right, row.low))
                available_at = observation_availability_time(row, config.availability_basis)
                while max_available and max_available[-1][1] <= available_at:
                    max_available.pop()
                max_available.append((right, available_at))
            left = right - steps + 1
            while max_high and max_high[0][0] < left:
                max_high.popleft()
            while min_low and min_low[0][0] < left:
                min_low.popleft()
            while max_available and max_available[0][0] < left:
                max_available.popleft()
            decision_index = right - steps
            if 0 <= decision_index < len(decision_times):
                highs[decision_index] = max_high[-1][1] if max_high else None
                lows[decision_index] = min_low[-1][1] if min_low else None
                availabilities[decision_index] = max_available[-1][1] if max_available else None
                missing[decision_index] = (
                    missing_prefix[right + 1] - missing_prefix[decision_index + 1] != 0
                )
                bad[decision_index] = bool(
                    bad_high_prefix[right + 1] - bad_high_prefix[decision_index + 1]
                    or bad_low_prefix[right + 1] - bad_low_prefix[decision_index + 1]
                )
        metric_sets.append((highs, lows, availabilities, missing, bad))

    def _iter_rows() -> Iterator[TargetRow]:
        for decision_index, decision_time in enumerate(decision_times):
            start_row = values[decision_index]
            for horizon_index, (horizon, steps) in enumerate(
                zip(horizons, horizon_steps, strict=True)
            ):
                target_end = decision_time + horizon
                freeze_at = target_end + config.target_revision_delay

                start_state = (
                    "MISSING"
                    if start_row is None
                    else (
                        "OBSERVED"
                        if observation_availability_time(start_row, config.availability_basis)
                        <= freeze_at
                        else "UNAVAILABLE"
                    )
                )
                end_row = values[decision_index + steps]
                end_state = (
                    "MISSING"
                    if end_row is None
                    else (
                        "OBSERVED"
                        if observation_availability_time(end_row, config.availability_basis)
                        <= freeze_at
                        else "UNAVAILABLE"
                    )
                )
                return_disposition = _return_disposition(
                    start_row,
                    start_state,
                    end_row,
                    end_state,
                )
                label_start = start_row.close if start_row else None
                label_end = end_row.close if end_row else None
                if return_disposition is ReturnDisposition.VALID:
                    assert label_start is not None and label_end is not None
                    log_return = _log_ratio(label_start, label_end)
                else:
                    log_return = None
                highs, lows, max_available, path_missing, path_bad = metric_sets[horizon_index]
                path_high = highs[decision_index]
                path_low = lows[decision_index]
                latest_available = max_available[decision_index]
                if return_disposition in {
                    ReturnDisposition.MISSING_START,
                    ReturnDisposition.UNAVAILABLE_BY_FREEZE,
                }:
                    upper = lower = None
                    excursion_disposition = ExcursionDisposition.MISSING_START
                elif return_disposition is ReturnDisposition.MISSING_END:
                    upper = lower = None
                    excursion_disposition = ExcursionDisposition.MISSING_END
                elif return_disposition is ReturnDisposition.AMBIGUOUS_SOURCE:
                    upper = lower = None
                    excursion_disposition = ExcursionDisposition.AMBIGUOUS_SOURCE
                elif (
                    start_row is None
                    or end_row is None
                    or start_row.close <= 0
                    or not start_row.close.is_finite()
                    or path_missing[decision_index]
                    or path_bad[decision_index]
                    or (latest_available is not None and latest_available > freeze_at)
                    or path_high is None
                    or path_low is None
                ):
                    upper = lower = None
                    excursion_disposition = ExcursionDisposition.INCOMPLETE_PATH
                else:
                    try:
                        upper = _log_ratio(path_high, start_row.close)
                        lower = _log_ratio(path_low, start_row.close)
                        excursion_disposition = ExcursionDisposition.VALID
                    except (ValueError, OverflowError):
                        upper = lower = None
                        excursion_disposition = ExcursionDisposition.INCOMPLETE_PATH

                yield TargetRow(
                    instrument_id=target_instrument,
                    decision_time=decision_time,
                    horizon=horizon,
                    target_basis=config.target_basis,
                    target_revision_policy=config.target_revision_policy,
                    target_start_time=decision_time,
                    target_end_time=target_end,
                    target_freeze_at=freeze_at,
                    target_available_at=freeze_at,
                    label_start_close=label_start,
                    label_end_close=label_end,
                    log_return=log_return,
                    return_disposition=return_disposition,
                    start_event_id=start_row.event_id if start_row else None,
                    end_event_id=end_row.event_id if end_row else None,
                    upper_log_excursion=upper,
                    lower_log_excursion=lower,
                    excursion_disposition=excursion_disposition,
                )

    return _iter_rows()


def _panel_temp_key(payload: str) -> tuple[str, str, str]:
    row = json.loads(payload)
    return (str(row["decision_time"]), str(row["instrument_id"]), str(row["basis"]))


def _merge_panel_files(
    files: Sequence[Path],
    *,
    target_writer: _DeferredChildWriter,
    target_hasher: _StreamingRowsHash,
) -> None:
    handles = [path.open("r", encoding="utf-8") for path in files]
    heap: list[tuple[tuple[str, str, str], int, str]] = []
    try:
        for index, handle in enumerate(handles):
            line = handle.readline()
            if line:
                payload = line.rstrip("\n")
                heapq.heappush(heap, (_panel_temp_key(payload), index, payload))
        while heap:
            _, index, payload = heapq.heappop(heap)
            target_hasher.add(payload)
            target_writer.add(payload)
            line = handles[index].readline()
            if line:
                next_payload = line.rstrip("\n")
                heapq.heappush(heap, (_panel_temp_key(next_payload), index, next_payload))
    finally:
        for handle in handles:
            handle.close()


def _make_summary_target_dataset(
    path: Path,
    *,
    target_dataset_id: str,
    observation_dataset_id: str,
    foundation_configuration_id: str,
    total_count: int,
) -> Any:
    return SimpleNamespace(
        rows=_SummaryRows(path, total_count),
        dataset_id=target_dataset_id,
        observation_dataset_id=observation_dataset_id,
        foundation_configuration_id=foundation_configuration_id,
    )


def _empty_fold_dataset(
    *,
    target_dataset_id: str,
    foundation_configuration_id: str,
) -> FoldDataset:
    return FoldDataset.create(
        (),
        target_dataset_id=target_dataset_id,
        foundation_configuration_id=foundation_configuration_id,
    )


@dataclass(frozen=True, slots=True)
class _FoldWindow:
    training_cutoff: datetime
    validation_start: datetime
    validation_end: datetime


def _fold_windows(config: FoundationConfig) -> tuple[_FoldWindow, ...]:
    duration = config.minimum_validation_duration
    if duration <= timedelta(0):
        raise ValueError("validation duration must be positive")
    holdout_start, holdout_end = config.holdout_range
    if holdout_start < config.range_start or holdout_end != config.range_end:
        raise ValueError("locked holdout must be the final foundation interval")
    selected_horizon = config.primary_vertical_horizon
    if selected_horizon not in config.target_horizons:
        raise ValueError("fold builder received a horizon absent from the foundation configuration")

    windows: list[_FoldWindow] = []
    validation_start = (
        config.range_start + config.minimum_training_duration + selected_horizon + config.embargo
    )
    while validation_start < holdout_start:
        validation_end = validation_start + duration
        if validation_end > holdout_start:
            break
        windows.append(
            _FoldWindow(
                training_cutoff=validation_start - config.embargo,
                validation_start=validation_start,
                validation_end=validation_end,
            )
        )
        validation_start = validation_end + config.embargo
    return tuple(windows)


def _next_summary(stream: Any) -> _TargetSummary | None:
    for line in stream:
        if line.strip():
            value = json.loads(line)
            if not isinstance(value, Mapping):
                raise ValueError("Stage 8 target summary row is invalid")
            return _TargetSummary.from_json_value(value)
    return None


def _merged_target_summaries(paths: Sequence[Path]) -> Iterator[_TargetSummary]:
    streams = [path.open("r", encoding="utf-8") for path in paths]
    heap: list[tuple[datetime, str, str, int, _TargetSummary]] = []
    previous_by_stream: dict[int, tuple[datetime, str]] = {}
    try:
        for index, stream in enumerate(streams):
            row = _next_summary(stream)
            if row is not None:
                heapq.heappush(
                    heap,
                    (row.decision_time, row.instrument_id, row.target_id, index, row),
                )
        while heap:
            _, _, _, index, row = heapq.heappop(heap)
            key = (row.decision_time, row.target_id)
            previous = previous_by_stream.get(index)
            if previous is not None and key < previous:
                raise ValueError("Stage 8 target summary checkpoint is not chronological")
            previous_by_stream[index] = key
            yield row
            next_row = _next_summary(streams[index])
            if next_row is not None:
                heapq.heappush(
                    heap,
                    (
                        next_row.decision_time,
                        next_row.instrument_id,
                        next_row.target_id,
                        index,
                        next_row,
                    ),
                )
    finally:
        for stream in streams:
            stream.close()


def _read_membership(path: Path) -> list[str]:
    with path.open("r", encoding="ascii") as stream:
        return [line.rstrip("\n") for line in stream if line.rstrip("\n")]


def _build_bounded_folds(
    summary_paths: Sequence[Path],
    config: FoundationConfig,
    *,
    target_dataset_id: str,
    temporary_root: Path,
) -> FoldDataset:
    windows = _fold_windows(config)
    membership_root = temporary_root / ".fold-membership"
    membership_root.mkdir()
    training_paths = [
        membership_root / f"training-{index:06d}.txt" for index in range(len(windows))
    ]
    validation_paths = [
        membership_root / f"validation-{index:06d}.txt" for index in range(len(windows))
    ]
    training_streams = [path.open("w", encoding="ascii") for path in training_paths]
    validation_streams = [path.open("w", encoding="ascii") for path in validation_paths]
    seen_target_ids: set[str] = set()
    holdout_start, holdout_end = config.holdout_range
    try:
        for row in _merged_target_summaries(summary_paths):
            if (
                row.horizon != config.primary_vertical_horizon
                or row.target_basis is not config.target_basis
            ):
                raise ValueError("Stage 8 target summary identity is inconsistent")
            if row.target_id in seen_target_ids:
                raise ValueError("target dataset contains duplicate target identities")
            seen_target_ids.add(row.target_id)
            outside_holdout = not (holdout_start <= row.decision_time < holdout_end)
            if not outside_holdout:
                continue
            for index, window in enumerate(windows):
                if (
                    window.validation_start <= row.decision_time < window.validation_end
                    and row.target_end_time <= holdout_start
                    and row.target_freeze_at <= holdout_start
                    and row.target_available_at <= holdout_start
                ):
                    validation_streams[index].write(f"{row.target_id}\n")
                    break
            if (
                row.return_disposition is ReturnDisposition.VALID
                and config.range_start <= row.decision_time
            ):
                for index, window in enumerate(windows):
                    if (
                        row.decision_time < window.training_cutoff
                        and row.target_available_at <= window.training_cutoff
                        and row.target_end_time <= window.training_cutoff
                    ):
                        training_streams[index].write(f"{row.target_id}\n")
                        break
    finally:
        for stream in (*training_streams, *validation_streams):
            stream.close()

    folds: list[Fold] = []
    training_ids: list[str] = []
    for index, window in enumerate(windows):
        training_ids.extend(_read_membership(training_paths[index]))
        training_ids.sort()
        validation_ids = sorted(_read_membership(validation_paths[index]))
        if not training_ids or not validation_ids:
            continue
        training_membership = tuple(training_ids)
        validation_membership = tuple(validation_ids)
        membership = membership_hash(training_membership, validation_membership)
        fold_id = _hash_json(
            {
                "contract": FOLD_DATASET_CONTRACT,
                "schema_version": 1,
                "training_start": config.range_start,
                "training_cutoff": window.training_cutoff,
                "validation_start": window.validation_start,
                "validation_end": window.validation_end,
                "embargo_end": window.validation_start,
                "membership_hash": membership,
            }
        )
        folds.append(
            Fold(
                fold_id=fold_id,
                training_start=config.range_start,
                training_cutoff=window.training_cutoff,
                validation_start=window.validation_start,
                validation_end=window.validation_end,
                embargo_end=window.validation_start,
                training_target_ids=training_membership,
                validation_target_ids=validation_membership,
                holdout_excluded=True,
                membership_hash=membership,
            )
        )
    if not folds:
        raise ValueError("no scientifically valid expanding folds are available")
    return FoldDataset.create(
        folds,
        target_dataset_id=target_dataset_id,
        foundation_configuration_id=config.configuration_id,
    )


def build_bounded_provider_foundation(
    *,
    source_evidence: ProviderHistorySourceEvidence,
    configuration: FoundationConfig,
    child_root: Path,
    bundle_root: Path,
    child_name: str,
    provider_manifest_sha256: str,
    checkpoint_root: Path | None = None,
    replay_checkpoint: _ReplayCheckpoint | None = None,
    part_callback: _ReplayPartCallback | None = None,
    workers: int = _DEFAULT_WORKERS,
    progress_callback: _ProgressCallback | None = None,
) -> tuple[IBKRFoundationBuild, dict[str, tuple[dict[str, object], ...]]]:
    """Build and publish a large provider-history foundation with bounded memory."""

    worker_count = _validated_workers(workers)
    progress = _Progress(progress_callback)
    provider_rows = source_evidence.observations
    if not provider_rows:
        raise ValueError("provider-history source has no observations")

    observed_instruments = set(_provider_instruments(provider_rows))
    positioned = getattr(provider_rows, "iter_instrument_with_positions", None)
    grouped = None if callable(positioned) else _sorted_groups(provider_rows)
    candidate_names = {str(item) for item in IBKR_CONFIRMATORY_INSTRUMENTS}
    ordered_instruments = tuple(
        sorted(observed_instruments | set(configuration.ordered_instruments) | candidate_names)
    )
    roles = {
        instrument: (
            InstrumentRole.TARGET if instrument in candidate_names else InstrumentRole.CONTEXT
        )
        for instrument in ordered_instruments
    }
    progress.emit(
        "source",
        "started",
        instrument_count=len(ordered_instruments),
        provider_row_count=source_evidence.dataset.row_count,
    )

    checkpoint = _Stage8Checkpoint(
        checkpoint_root,
        provider_manifest_sha256=provider_manifest_sha256,
        provider_dataset_sha256=source_evidence.dataset.dataset_sha256,
        configuration_id=configuration.configuration_id,
    )
    active_intervals, provider_gaps, source_start, source_end = _provider_evidence(
        source_evidence,
        include_bounds=True,
    )
    observation_configuration = {
        "contract": "qtrad-ibkr-historical-observation-adapter-v1",
        "source_class": "IBKR_HISTORICAL_RESEARCH",
        "provider": "ibkr",
        "environment": "paper",
        "ordered_instruments": list(ordered_instruments),
        "interval_start": configuration.required_observation_start.isoformat(),
        "interval_end": configuration.required_observation_end.isoformat(),
        "observed_interval_start": source_start.isoformat(),
        "observed_interval_end": source_end.isoformat(),
        "grid_resolution_seconds": int(configuration.grid_resolution.total_seconds()),
        "availability_basis": configuration.availability_basis.value,
        "source_dataset_id": source_evidence.dataset.dataset_sha256,
    }
    selection_policies = {
        "source_class": "IBKR_HISTORICAL_RESEARCH",
        "availability_policy": source_evidence.dataset.availability_policy.as_json_value(),
        "correction_policy": "FROZEN_FIRST_SUCCESSFUL_RESPONSE_NO_REFETCH_MERGE",
    }
    global_observation_metadata = _observation_metadata(
        observation_configuration,
        source_dataset_id=source_evidence.dataset.dataset_sha256,
        selection_policies=selection_policies,
    )
    adapted_configuration = replace(
        configuration,
        ordered_instruments=ordered_instruments,
        instrument_roles=roles,
        observation_dataset_id="0" * 64,
    )
    progress.emit(
        "source",
        "completed",
        active_interval_count=len(active_intervals),
        gap_count=len(provider_gaps),
    )
    lineage = _foundation_child_lineage(source_evidence, provider_manifest_sha256)
    if not child_root.exists():
        child_root.mkdir(parents=True, exist_ok=False)
    panel_tmp = child_root / ".panel-temp"
    panel_tmp.mkdir()

    observation_writer = _DeferredChildWriter(
        child_root=child_root,
        bundle_root=bundle_root,
        child_name=child_name,
        kind="observations",
        lineage=lineage,
        replay_checkpoint=replay_checkpoint,
        part_callback=part_callback,
    )
    observation_hasher = _StreamingRowsHash(global_observation_metadata)
    observation_files: dict[str, _CheckpointFile] = {}
    for instrument_index, instrument in enumerate(ordered_instruments, 1):
        cached_observation = checkpoint.observation(instrument)
        if cached_observation is not None:
            details = cached_observation.rows
            payloads: Iterable[str] = _checkpoint_payloads(details)
            event = "reused"
        else:
            observation_path = panel_tmp / f"{instrument_index:05d}.observations.jsonl"
            instrument_start: datetime | None = None
            instrument_end: datetime | None = None
            with observation_path.open("w", encoding="utf-8") as stream:
                for row in _adapted_instrument_rows(
                    provider_rows,
                    grouped,
                    instrument,
                    source_evidence.dataset.dataset_sha256,
                ):
                    instrument_start = (
                        row.interval_start
                        if instrument_start is None
                        else min(instrument_start, row.interval_start)
                    )
                    instrument_end = (
                        row.interval_end
                        if instrument_end is None
                        else max(instrument_end, row.interval_end)
                    )
                    payload = _canonical_row(row.as_json())
                    stream.write(payload)
                    stream.write("\n")
                    observation_hasher.add(payload)
                    observation_writer.add(payload)
            details = (
                checkpoint.store_observation(
                    instrument,
                    observation_path,
                    source_start=instrument_start,
                    source_end=instrument_end,
                ).rows
                if checkpoint_root is not None
                else _checkpoint_file(observation_path)
            )
            payloads = ()
            event = "completed"
        observation_files[instrument] = details
        for payload in payloads:
            observation_hasher.add(payload)
            observation_writer.add(payload)
        progress.emit(
            "observations",
            event,
            instrument_id=instrument,
            instrument_index=instrument_index,
            instrument_count=len(ordered_instruments),
            row_count=details.row_count,
        )
    observation_dataset_id = observation_hasher.finish()
    observation_refs = observation_writer.finalize(observation_dataset_id)
    adapted_configuration = replace(
        adapted_configuration,
        observation_dataset_id=observation_dataset_id,
    )

    panel_metadata = {
        "contract": PANEL_DATASET_CONTRACT,
        "schema_version": 1,
        "observation_dataset_id": observation_dataset_id,
        "foundation_configuration_id": adapted_configuration.configuration_id,
    }
    target_metadata = {
        "contract": TARGET_DATASET_CONTRACT,
        "schema_version": 1,
        "observation_dataset_id": observation_dataset_id,
        "foundation_configuration_id": adapted_configuration.configuration_id,
    }
    panel_hasher = _StreamingRowsHash(panel_metadata)
    target_hasher = _StreamingRowsHash(target_metadata)
    panel_writer = _DeferredChildWriter(
        child_root=child_root,
        bundle_root=bundle_root,
        child_name=child_name,
        kind="panel",
        lineage=lineage,
        replay_checkpoint=replay_checkpoint,
        part_callback=part_callback,
    )
    target_writer = _DeferredChildWriter(
        child_root=child_root,
        bundle_root=bundle_root,
        child_name=child_name,
        kind="targets",
        lineage=lineage,
        replay_checkpoint=replay_checkpoint,
        part_callback=part_callback,
    )

    panel_files: list[Path] = []
    summary_files: list[Path] = []
    summary_path = panel_tmp / ".primary-target-summaries.jsonl"
    summary_stream = summary_path.open("w", encoding="utf-8")
    target_row_count = 0
    try:
        cached_derivatives: dict[str, _DerivedCheckpoint] = {}
        jobs: list[_DerivationJob] = []
        panel_paths: dict[str, Path] = {}
        for instrument_index, instrument in enumerate(ordered_instruments):
            role = roles[instrument]
            panel_file = panel_tmp / f"{instrument_index:05d}.jsonl"
            target_file = panel_tmp / f"{instrument_index:05d}.targets.jsonl"
            instrument_summary_file = panel_tmp / f"{instrument_index:05d}.summaries.jsonl"
            panel_paths[instrument] = panel_file
            cached_derived = checkpoint.derived(
                instrument,
                foundation_configuration_id=adapted_configuration.configuration_id,
            )
            if cached_derived is not None:
                shutil.copyfile(cached_derived.panel.path, panel_file)
                cached_derivatives[instrument] = cached_derived
                continue
            local_observation_configuration = _local_observation_configuration(
                observation_configuration, instrument
            )
            local_observation_metadata = _observation_metadata(
                local_observation_configuration,
                source_dataset_id=source_evidence.dataset.dataset_sha256,
                selection_policies=selection_policies,
            )
            local_observation_id = _dataset_id_from_checkpoint(
                local_observation_metadata, observation_files[instrument]
            )
            local_configuration = _local_foundation_configuration(
                adapted_configuration,
                instrument=instrument,
                role=role,
                observation_dataset_id=local_observation_id,
            )
            jobs.append(
                _DerivationJob(
                    instrument=instrument,
                    role=role,
                    observations=observation_files[instrument],
                    configuration=local_configuration,
                    active_intervals=dict(active_intervals),
                    panel_path=panel_file,
                    target_path=target_file,
                    summary_path=instrument_summary_file,
                )
            )

        if worker_count == 1 or len(jobs) <= 1:
            derivation_results = {
                result.instrument: result for result in map(_derive_instrument, jobs)
            }
        else:
            with ProcessPoolExecutor(
                max_workers=min(worker_count, len(jobs)),
                mp_context=get_context("spawn"),
            ) as executor:
                derivation_results = {
                    result.instrument: result for result in executor.map(_derive_instrument, jobs)
                }

        jobs_by_instrument = {job.instrument: job for job in jobs}
        for instrument_index, instrument in enumerate(ordered_instruments, 1):
            cached_derived = cached_derivatives.get(instrument)
            if cached_derived is not None:
                derived = cached_derived
                event = "reused"
            else:
                job = jobs_by_instrument[instrument]
                result = derivation_results[instrument]
                derived = (
                    checkpoint.store_derived(
                        instrument,
                        foundation_configuration_id=adapted_configuration.configuration_id,
                        panel=job.panel_path,
                        targets=job.target_path,
                        summaries=job.summary_path,
                    )
                    if checkpoint_root is not None
                    else _DerivedCheckpoint(
                        panel=_checkpoint_file(job.panel_path),
                        targets=_checkpoint_file(job.target_path),
                        summaries=_checkpoint_file(job.summary_path),
                    )
                )
                event = "completed"
                if derived.panel.row_count != result.panel_row_count:
                    raise AssertionError("Stage 8 panel checkpoint row count changed")
                if derived.targets.row_count != result.target_row_count:
                    raise AssertionError("Stage 8 target checkpoint row count changed")
            summary_files.append(derived.summaries.path)
            panel_files.append(panel_paths[instrument])
            for payload in _checkpoint_payloads(derived.targets):
                target_hasher.add(payload)
                target_writer.add(payload)
            target_row_count += derived.targets.row_count
            for payload in _checkpoint_payloads(derived.summaries):
                summary_stream.write(payload)
                summary_stream.write("\n")
            gc.collect()
            progress.emit(
                "derived",
                event,
                instrument_id=instrument,
                instrument_index=instrument_index,
                instrument_count=len(ordered_instruments),
                panel_row_count=derived.panel.row_count,
                target_row_count=derived.targets.row_count,
            )

        progress.emit("panel_merge", "started", instrument_count=len(panel_files))
        _merge_panel_files(
            panel_files,
            target_writer=panel_writer,
            target_hasher=panel_hasher,
        )
        panel_dataset_id = panel_hasher.finish()
        target_dataset_id = target_hasher.finish()
        panel_refs = panel_writer.finalize(panel_dataset_id)
        target_refs = target_writer.finalize(target_dataset_id)
        progress.emit("panel_merge", "completed", panel_dataset_id=panel_dataset_id)

        summary_stream.close()
        summary_dataset = _make_summary_target_dataset(
            summary_path,
            target_dataset_id=target_dataset_id,
            observation_dataset_id=observation_dataset_id,
            foundation_configuration_id=adapted_configuration.configuration_id,
            total_count=target_row_count,
        )
        progress.emit("folds", "started")
        try:
            folds = _build_bounded_folds(
                summary_files,
                adapted_configuration,
                target_dataset_id=target_dataset_id,
                temporary_root=panel_tmp,
            )
        except ValueError as error:
            if str(error) != "no scientifically valid expanding folds are available":
                raise
            folds = _empty_fold_dataset(
                target_dataset_id=target_dataset_id,
                foundation_configuration_id=adapted_configuration.configuration_id,
            )
        fold_writer = _DeferredChildWriter(
            child_root=child_root,
            bundle_root=bundle_root,
            child_name=child_name,
            kind="folds",
            lineage=lineage,
            replay_checkpoint=replay_checkpoint,
            part_callback=part_callback,
        )
        for fold in folds.folds:
            payload = _canonical_row(fold.as_json())
            fold_writer.add(payload)
        fold_refs = fold_writer.finalize(folds.dataset_id)
        progress.emit("folds", "completed", fold_count=len(folds.folds))
        readiness = evaluate_ibkr_foundation_readiness(
            source_evidence,
            summary_dataset,
            source_start=source_start,
            source_end=source_end,
            active_intervals=active_intervals,
            provider_gaps=provider_gaps,
            primary_horizon=adapted_configuration.primary_vertical_horizon,
            folds=folds.folds,
            holdout_range=adapted_configuration.holdout_range,
        )
    finally:
        if not summary_stream.closed:
            summary_stream.close()
        shutil.rmtree(panel_tmp, ignore_errors=True)

    observations_ref = cast(
        ObservationDataset,
        SimpleNamespace(
            rows=(),
            dataset_id=observation_dataset_id,
            configuration=observation_configuration,
            source_dataset_ids=(source_evidence.dataset.dataset_sha256,),
            selection_policies=selection_policies,
        ),
    )
    panel_ref = cast(
        PanelDataset,
        SimpleNamespace(
            rows=(),
            dataset_id=panel_dataset_id,
            observation_dataset_id=observation_dataset_id,
            foundation_configuration_id=adapted_configuration.configuration_id,
        ),
    )
    target_ref = cast(
        TargetDataset,
        SimpleNamespace(
            rows=(),
            dataset_id=target_dataset_id,
            observation_dataset_id=observation_dataset_id,
            foundation_configuration_id=adapted_configuration.configuration_id,
        ),
    )
    build = IBKRFoundationBuild(
        configuration=adapted_configuration,
        observations=observations_ref,
        panel=panel_ref,
        targets=target_ref,
        folds=folds,
        target_index=None,
        causal_metadata=None,
        provider_history=source_evidence.dataset,
        active_intervals=active_intervals,
        provider_gaps=provider_gaps,
        readiness=readiness,
    )
    progress.emit(
        "foundation",
        "completed",
        observation_dataset_id=observation_dataset_id,
        panel_dataset_id=panel_dataset_id,
        target_dataset_id=target_dataset_id,
        fold_dataset_id=folds.dataset_id,
    )
    return build, {
        "observations": tuple(observation_refs),
        "panel": tuple(panel_refs),
        "targets": tuple(target_refs),
        "folds": tuple(fold_refs),
    }


class _ReplayChildComparator:
    def __init__(
        self,
        *,
        actual_child_root: Path,
        actual_payload: Mapping[str, object],
        replay_checkpoint: _ReplayCheckpoint,
    ) -> None:
        self.actual_child_root = actual_child_root
        self.actual_payload = actual_payload
        self.replay_checkpoint = replay_checkpoint
        self.expected_paths: set[str] = set()
        self.seen_parts: dict[str, int] = defaultdict(int)

    def compare(
        self,
        kind: str,
        index: int,
        generated_reference: Mapping[str, object],
        generated_manifest_path: Path,
        generated_file_path: Path,
    ) -> None:
        actual_parts = self.actual_payload.get(kind)
        if (
            not isinstance(actual_parts, Sequence)
            or isinstance(actual_parts, (str, bytes))
            or index >= len(actual_parts)
        ):
            raise ValueError(f"child {kind} part {index} reference is missing")
        actual_reference = actual_parts[index]
        if not isinstance(actual_reference, Mapping):
            raise ValueError(f"child {kind} part {index} reference is invalid")
        if _runtime()._json_bytes(actual_reference) != _runtime()._json_bytes(generated_reference):
            raise ValueError(f"child {kind} part {index} reference diverges from bounded replay")
        paths = (
            ("manifest_path", generated_manifest_path),
            ("file", generated_file_path),
        )
        for key, generated_path in paths:
            relative = generated_reference.get(key)
            if not isinstance(relative, str):
                raise ValueError(f"child {kind} part {index} {key} is invalid")
            self.expected_paths.add(relative)
            actual_path = self.actual_child_root.parent / relative
            if (
                not actual_path.is_file()
                or actual_path.is_symlink()
                or not generated_path.is_file()
                or generated_path.is_symlink()
            ):
                raise ValueError(f"child {kind} part {index} {key} is missing")
            if actual_path.read_bytes() != generated_path.read_bytes():
                raise ValueError(f"child {kind} part {index} {key} bytes diverge from replay")
        row_count = generated_reference.get("row_count")
        rows_sha256 = json.loads(generated_manifest_path.read_text(encoding="utf-8")).get(
            "rows_sha256"
        )
        if not isinstance(row_count, int) or not isinstance(rows_sha256, str):
            raise ValueError(f"child {kind} part {index} replay metadata is invalid")
        self.replay_checkpoint.store_verified(
            kind,
            index,
            row_count=row_count,
            rows_sha256=rows_sha256,
            generated_path=generated_file_path,
        )
        self.seen_parts[kind] += 1

    def finish(self, generated: Mapping[str, Sequence[Mapping[str, object]]]) -> None:
        for kind, generated_parts in generated.items():
            actual_parts = self.actual_payload.get(kind)
            if not isinstance(actual_parts, Sequence) or isinstance(actual_parts, (str, bytes)):
                raise ValueError(f"child references for {kind} are invalid")
            if len(actual_parts) != len(generated_parts) or self.seen_parts[kind] != len(
                generated_parts
            ):
                raise ValueError(f"child {kind} part count diverges from bounded replay")
        actual_paths = {
            path.relative_to(self.actual_child_root.parent).as_posix()
            for path in self.actual_child_root.rglob("*")
            if path.is_file()
        }
        if actual_paths != self.expected_paths:
            raise ValueError("foundation child root contains an unexpected or missing file")


def verify_bounded_provider_foundation(
    *,
    source_evidence: ProviderHistorySourceEvidence,
    configuration: FoundationConfig,
    bundle_path: Path,
    document: Mapping[str, object],
    payload: Mapping[str, object],
    replay_checkpoint_root: Path | None = None,
    workers: int = _DEFAULT_WORKERS,
) -> IBKRFoundationBuild:
    runtime = _runtime()
    child_payload = payload.get("children")
    if not isinstance(child_payload, Mapping):
        raise ValueError("foundation children payload is invalid")
    typed_child_payload = cast(Mapping[str, object], child_payload)
    provider_value = document.get("provider_history_manifest")
    if not isinstance(provider_value, str):
        raise ValueError("foundation provider manifest path is invalid")
    provider_path = bundle_path.parent / provider_value
    provider_manifest_sha256 = hashlib.sha256(provider_path.read_bytes()).hexdigest()
    replay_checkpoint = _ReplayCheckpoint(
        replay_checkpoint_root,
        provider_manifest_sha256=provider_manifest_sha256,
        provider_dataset_sha256=source_evidence.dataset.dataset_sha256,
        configuration_id=configuration.configuration_id,
        published_bundle_sha256=hashlib.sha256(bundle_path.read_bytes()).hexdigest(),
    )
    root_name: str | None = None
    for raw_parts in typed_child_payload.values():
        if isinstance(raw_parts, Sequence) and raw_parts:
            first = raw_parts[0]
            if isinstance(first, Mapping):
                manifest_path = first.get("manifest_path")
                if isinstance(manifest_path, str):
                    root_name = PurePosixPath(manifest_path).parts[0]
                    break
    if root_name is None:
        raise ValueError("foundation child root is missing")
    actual_child_root = bundle_path.parent / root_name
    temp_parent = Path(
        mkdtemp(
            prefix=f".{actual_child_root.name}.verify-",
            dir=str(bundle_path.parent),
        )
    )
    temp_child = temp_parent / actual_child_root.name
    comparator = _ReplayChildComparator(
        actual_child_root=actual_child_root,
        actual_payload=typed_child_payload,
        replay_checkpoint=replay_checkpoint,
    )
    try:
        replay, generated = build_bounded_provider_foundation(
            source_evidence=source_evidence,
            configuration=configuration,
            child_root=temp_child,
            bundle_root=temp_parent,
            child_name=actual_child_root.name,
            provider_manifest_sha256=provider_manifest_sha256,
            replay_checkpoint=replay_checkpoint,
            part_callback=comparator.compare,
            workers=workers,
        )
        comparator.finish(generated)
        expected_document = runtime._manifest_payload(
            replay,
            source_evidence,
            generated,
            provider_path,
            bundle_path.parent,
        )
        expected_payload = expected_document["payload"]
        if runtime._json_bytes(expected_payload) != runtime._json_bytes(payload):
            raise ValueError("bounded foundation payload identity does not match replay")
        return replay
    finally:
        shutil.rmtree(temp_parent, ignore_errors=True)
