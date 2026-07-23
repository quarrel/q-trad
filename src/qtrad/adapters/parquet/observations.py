"""Immutable Parquet storage for the R1 causal observation dataset."""

import asyncio
import hashlib
import io
import json
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal

import polars as pl
from pydantic import BaseModel, ConfigDict, Field

from qtrad.domain.events import JsonValue
from qtrad.domain.market_data import BarProvenance, DataQuality, PriceBasis
from qtrad.domain.research import (
    OBSERVATION_DATASET_CONTRACT,
    ObservationDataset,
    ObservationRow,
)
from qtrad.ports.clock import Clock

_MAX_MANIFEST_BYTES = 4 * 1024 * 1024
_OBSERVATION_ROOT = "observations-v1"


class _ObservationManifestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    manifest_id: str
    manifest_sha256: str
    dataset_id: str
    contract: Literal["qtrad-research-observations-v1"]
    schema_version: Literal[1]
    created_at: datetime
    row_count: int = Field(ge=0)
    files: list[str]
    file_sha256: dict[str, str]
    configuration: dict[str, JsonValue]
    source_dataset_ids: list[str]
    selection_policies: dict[str, JsonValue]
    metadata: dict[str, JsonValue]
    application_version: str
    image_identity: str
    source_snapshot: dict[str, JsonValue]
    build_evidence: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class ObservationManifest:
    """Physical identity for one immutable representation of an observation dataset."""

    manifest_id: str
    manifest_sha256: str
    dataset_id: str
    created_at: datetime
    row_count: int
    files: tuple[str, ...]
    file_sha256: Mapping[str, str]
    configuration: Mapping[str, JsonValue]
    source_dataset_ids: tuple[str, ...]
    selection_policies: Mapping[str, JsonValue]
    metadata: Mapping[str, JsonValue]
    application_version: str
    image_identity: str
    source_snapshot: Mapping[str, JsonValue]
    build_evidence: Mapping[str, JsonValue]
    contract: str = OBSERVATION_DATASET_CONTRACT
    schema_version: int = 1

    def __post_init__(self) -> None:
        _validate_manifest(self)


class ParquetObservationStore:
    """Read and write only the R1 observation namespace."""

    def __init__(self, root: Path, clock: Clock) -> None:
        self._root = root
        self._clock = clock

    async def write_observations(
        self,
        dataset: ObservationDataset,
        *,
        metadata: Mapping[str, JsonValue] | None = None,
        application_version: str = "development",
        image_identity: str = "development",
        source_snapshot: Mapping[str, JsonValue] | None = None,
        build_evidence: Mapping[str, JsonValue] | None = None,
    ) -> ObservationManifest:
        return await asyncio.to_thread(
            self._write_sync,
            dataset,
            dict(metadata or {}),
            application_version,
            image_identity,
            dict(source_snapshot or {}),
            dict(build_evidence or {}),
        )

    async def read_manifest(self, manifest_id: str) -> ObservationManifest:
        return await asyncio.to_thread(self._read_manifest_sync, manifest_id)

    async def read_observations(self, manifest_id: str) -> ObservationDataset:
        return await asyncio.to_thread(self._read_dataset_sync, manifest_id)

    async def verify(self, manifest_id: str) -> ObservationManifest:
        manifest = await self.read_manifest(manifest_id)
        await self.read_observations(manifest_id)
        return manifest

    def _write_sync(
        self,
        dataset: ObservationDataset,
        metadata: Mapping[str, JsonValue],
        application_version: str,
        image_identity: str,
        source_snapshot: Mapping[str, JsonValue],
        build_evidence: Mapping[str, JsonValue],
    ) -> ObservationManifest:
        created_at = self._clock.now()
        _require_text(application_version, "application version")
        _require_text(image_identity, "image identity")
        groups: dict[tuple[str, str], list[ObservationRow]] = defaultdict(list)
        for row in dataset.rows:
            groups[(row.instrument_id, row.interval_start.date().isoformat())].append(row)

        files: list[str] = []
        file_sha256: dict[str, str] = {}
        for group in groups.values():
            ordered = tuple(sorted(group, key=ObservationRow.semantic_key))
            partition_id = _sha256_json([row.as_json() for row in ordered])[:24]
            relative = _observation_partition_path(ordered[0], partition_id)
            destination = self._root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            encoded = _parquet_bytes(ordered)
            if destination.exists():
                _verify_partition(destination, ordered)
            else:
                try:
                    with destination.open("xb") as output:
                        output.write(encoded)
                except FileExistsError:
                    _verify_partition(destination, ordered)
            files.append(relative)
            file_sha256[relative] = hashlib.sha256(destination.read_bytes()).hexdigest()

        ordered_files = tuple(sorted(files))
        physical_payload = _physical_identity_payload(
            created_at=created_at,
            dataset=dataset,
            files=ordered_files,
            file_sha256=file_sha256,
            metadata=metadata,
            application_version=application_version,
            image_identity=image_identity,
            source_snapshot=source_snapshot,
            build_evidence=build_evidence,
        )
        manifest_sha256 = _sha256_json(physical_payload)
        manifest = ObservationManifest(
            manifest_id=manifest_sha256[:24],
            manifest_sha256=manifest_sha256,
            dataset_id=dataset.dataset_id,
            created_at=created_at,
            row_count=len(dataset.rows),
            files=ordered_files,
            file_sha256=file_sha256,
            configuration=dataset.configuration,
            source_dataset_ids=dataset.source_dataset_ids,
            selection_policies=dataset.selection_policies,
            metadata=metadata,
            application_version=application_version,
            image_identity=image_identity,
            source_snapshot=source_snapshot,
            build_evidence=build_evidence,
        )
        manifest_path = self._root / "manifests" / f"{manifest.manifest_id}.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        encoded_manifest = json.dumps(_manifest_row(manifest), indent=2, sort_keys=True) + "\n"
        if len(encoded_manifest.encode("utf-8")) > _MAX_MANIFEST_BYTES:
            raise ValueError("observation manifest exceeds the 4 MiB limit")
        try:
            with manifest_path.open("x", encoding="utf-8") as output:
                output.write(encoded_manifest)
        except FileExistsError as error:
            existing = self._read_manifest_sync(manifest.manifest_id)
            if _physical_identity_payload_from_manifest(existing) != physical_payload:
                raise RuntimeError(
                    "existing observation manifest conflicts with its identity"
                ) from error
            self._read_dataset_sync(existing.manifest_id)
            return existing
        return manifest

    def _read_manifest_sync(self, manifest_id: str) -> ObservationManifest:
        _require_manifest_id(manifest_id)
        encoded = (self._root / "manifests" / f"{manifest_id}.json").read_bytes()
        if len(encoded) > _MAX_MANIFEST_BYTES:
            raise ValueError("observation manifest exceeds the 4 MiB limit")
        model = _ObservationManifestModel.model_validate_json(encoded)
        if model.manifest_id != manifest_id:
            raise ValueError("observation manifest filename does not match its identity")
        manifest = ObservationManifest(
            manifest_id=model.manifest_id,
            manifest_sha256=model.manifest_sha256,
            dataset_id=model.dataset_id,
            created_at=model.created_at,
            row_count=model.row_count,
            files=tuple(model.files),
            file_sha256=model.file_sha256,
            configuration=model.configuration,
            source_dataset_ids=tuple(model.source_dataset_ids),
            selection_policies=model.selection_policies,
            metadata=model.metadata,
            application_version=model.application_version,
            image_identity=model.image_identity,
            source_snapshot=model.source_snapshot,
            build_evidence=model.build_evidence,
        )
        if (
            _sha256_json(_physical_identity_payload_from_manifest(manifest))
            != manifest.manifest_sha256
        ):
            raise ValueError("observation manifest hash does not match its canonical content")
        return manifest

    def _read_dataset_sync(self, manifest_id: str) -> ObservationDataset:
        manifest = self._read_manifest_sync(manifest_id)
        rows: list[ObservationRow] = []
        for relative in manifest.files:
            path = self._root / PurePosixPath(relative)
            observed_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            if observed_hash != manifest.file_sha256[relative]:
                raise ValueError(f"observation file hash mismatch: {relative}")
            file_rows = tuple(
                _observation_from_row(row) for row in pl.read_parquet(path).to_dicts()
            )
            partition_id = _sha256_json([row.as_json() for row in file_rows])[:24]
            if any(_observation_partition_path(row, partition_id) != relative for row in file_rows):
                raise ValueError(
                    f"observation file contains rows for another partition: {relative}"
                )
            rows.extend(file_rows)
        dataset = ObservationDataset.create(
            rows,
            configuration=manifest.configuration,
            source_dataset_ids=manifest.source_dataset_ids,
            selection_policies=manifest.selection_policies,
        )
        if dataset.dataset_id != manifest.dataset_id:
            raise ValueError("observation semantic dataset hash does not match its manifest")
        if len(dataset.rows) != manifest.row_count:
            raise ValueError("observation manifest row count does not match its rows")
        return dataset


def _parquet_bytes(rows: tuple[ObservationRow, ...]) -> bytes:
    buffer = io.BytesIO()
    pl.DataFrame([row.as_json() for row in rows]).write_parquet(buffer)
    return buffer.getvalue()


def _verify_partition(path: Path, expected: tuple[ObservationRow, ...]) -> None:
    observed = tuple(_observation_from_row(row) for row in pl.read_parquet(path).to_dicts())
    if tuple(sorted(observed, key=ObservationRow.semantic_key)) != expected:
        raise RuntimeError(f"existing observation partition conflicts with its content: {path}")


def _observation_partition_path(row: ObservationRow, partition_id: str) -> str:
    safe_instrument = row.instrument_id.replace(":", "__")
    return (
        Path(_OBSERVATION_ROOT)
        / f"instrument={safe_instrument}"
        / f"date={row.interval_start.date().isoformat()}"
        / f"part-{partition_id}.parquet"
    ).as_posix()


def _observation_from_row(row: Mapping[str, object]) -> ObservationRow:
    return ObservationRow(
        event_id=_uuid(str(row["event_id"])),
        stream_id=str(row["stream_id"]),
        stream_version=int(str(row["stream_version"])),
        event_type=str(row["event_type"]),
        event_time=_utc_datetime(str(row["event_time"])),
        received_at=_utc_datetime(str(row["received_at"])),
        persisted_at=_utc_datetime(str(row["persisted_at"])),
        global_position=int(str(row["global_position"])),
        instrument_id=str(row["instrument_id"]),
        basis=PriceBasis(str(row["basis"])),
        interval_start=_utc_datetime(str(row["interval_start"])),
        interval_end=_utc_datetime(str(row["interval_end"])),
        open=_decimal(row["open"]),
        high=_decimal(row["high"]),
        low=_decimal(row["low"]),
        close=_decimal(row["close"]),
        sample_count=int(str(row["sample_count"])),
        revision=int(str(row["revision"])),
        provenance=BarProvenance(str(row["provenance"])),
        quality=DataQuality(str(row["quality"])),
        source_provider=str(row["source_provider"]),
        source_environment=str(row["source_environment"]),
        source_external_id=str(row["source_external_id"]),
    )


def _physical_identity_payload(
    *,
    created_at: datetime,
    dataset: ObservationDataset,
    files: tuple[str, ...],
    file_sha256: Mapping[str, str],
    metadata: Mapping[str, JsonValue],
    application_version: str,
    image_identity: str,
    source_snapshot: Mapping[str, JsonValue],
    build_evidence: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    return _physical_identity_payload_values(
        created_at=created_at,
        dataset_id=dataset.dataset_id,
        configuration=dataset.configuration,
        source_dataset_ids=dataset.source_dataset_ids,
        selection_policies=dataset.selection_policies,
        files=files,
        file_sha256=file_sha256,
        metadata=metadata,
        application_version=application_version,
        image_identity=image_identity,
        source_snapshot=source_snapshot,
        build_evidence=build_evidence,
    )


def _physical_identity_payload_values(
    *,
    created_at: datetime,
    dataset_id: str,
    configuration: Mapping[str, JsonValue],
    source_dataset_ids: tuple[str, ...],
    selection_policies: Mapping[str, JsonValue],
    files: tuple[str, ...],
    file_sha256: Mapping[str, str],
    metadata: Mapping[str, JsonValue],
    application_version: str,
    image_identity: str,
    source_snapshot: Mapping[str, JsonValue],
    build_evidence: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    return {
        "contract": OBSERVATION_DATASET_CONTRACT,
        "schema_version": 1,
        "dataset_id": dataset_id,
        "created_at": _time_text(created_at),
        "files": list(files),
        "file_sha256": dict(sorted(file_sha256.items())),
        "configuration": dict(configuration),
        "source_dataset_ids": list(source_dataset_ids),
        "selection_policies": dict(selection_policies),
        "metadata": dict(metadata),
        "application_version": application_version,
        "image_identity": image_identity,
        "source_snapshot": dict(source_snapshot),
        "build_evidence": dict(build_evidence),
    }


def _physical_identity_payload_from_manifest(
    manifest: ObservationManifest,
) -> dict[str, JsonValue]:
    return _physical_identity_payload_values(
        created_at=manifest.created_at,
        dataset_id=manifest.dataset_id,
        configuration=manifest.configuration,
        source_dataset_ids=manifest.source_dataset_ids,
        selection_policies=manifest.selection_policies,
        files=manifest.files,
        file_sha256=manifest.file_sha256,
        metadata=manifest.metadata,
        application_version=manifest.application_version,
        image_identity=manifest.image_identity,
        source_snapshot=manifest.source_snapshot,
        build_evidence=manifest.build_evidence,
    )


def _manifest_row(manifest: ObservationManifest) -> dict[str, object]:
    return {
        "manifest_id": manifest.manifest_id,
        "manifest_sha256": manifest.manifest_sha256,
        "dataset_id": manifest.dataset_id,
        "contract": manifest.contract,
        "schema_version": manifest.schema_version,
        "created_at": manifest.created_at.isoformat(),
        "row_count": manifest.row_count,
        "files": list(manifest.files),
        "file_sha256": dict(sorted(manifest.file_sha256.items())),
        "configuration": manifest.configuration,
        "source_dataset_ids": list(manifest.source_dataset_ids),
        "selection_policies": manifest.selection_policies,
        "metadata": manifest.metadata,
        "application_version": manifest.application_version,
        "image_identity": manifest.image_identity,
        "source_snapshot": manifest.source_snapshot,
        "build_evidence": manifest.build_evidence,
    }


def _validate_manifest(manifest: ObservationManifest) -> None:
    _require_manifest_id(manifest.manifest_id)
    _require_sha256(manifest.manifest_sha256, "observation manifest hash")
    _require_sha256(manifest.dataset_id, "observation dataset ID")
    if manifest.row_count < 0 or len(manifest.files) != len(manifest.file_sha256):
        raise ValueError("observation manifest counts and files are inconsistent")
    if set(manifest.files) != set(manifest.file_sha256):
        raise ValueError("observation manifest requires one file hash per file")
    if not manifest.files and manifest.row_count:
        raise ValueError("non-empty observation manifest requires files")
    for relative in manifest.files:
        _safe_file(relative)
    if manifest.manifest_id != manifest.manifest_sha256[:24]:
        raise ValueError("observation manifest ID must match its hash")
    if (
        manifest.created_at.tzinfo is None
        or manifest.created_at.utcoffset() != UTC.utcoffset(manifest.created_at)
    ):
        raise ValueError("observation manifest creation time must be UTC")


def _safe_file(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or path.parts[0] != _OBSERVATION_ROOT
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.suffix != ".parquet"
    ):
        raise ValueError(f"unsafe observation file path: {value}")
    return path


def _sha256_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _time_text(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _utc_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError("observation timestamp must be UTC")
    return parsed.astimezone(UTC)


def _decimal(value: object):
    from decimal import Decimal

    return Decimal(str(value))


def _uuid(value: str):
    from uuid import UUID

    return UUID(value)


def _require_manifest_id(value: str) -> None:
    if len(value) != 24 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError("observation manifest ID must be 24 lower-case hexadecimal characters")


def _require_sha256(value: str, field: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} must be a lower-case SHA-256")


def _require_text(value: str, field: str) -> None:
    if not value:
        raise ValueError(f"{field} must be non-empty")
