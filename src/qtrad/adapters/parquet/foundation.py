"""Independent manifested storage for R1 foundation child datasets."""

import asyncio
import hashlib
import io
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import polars as pl
from pydantic import BaseModel, ConfigDict, Field

from qtrad.domain.events import JsonValue, to_json_value
from qtrad.ports.clock import Clock

_MAX_MANIFEST_BYTES = 4 * 1024 * 1024
_ARTEFACT_ROOT = "foundation-v1"
_MANIFEST_ROOT = "foundation-manifests"
_KINDS = frozenset({"configuration", "availability", "panel", "targets", "folds", "forecasts"})


class _ManifestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    manifest_id: str
    manifest_sha256: str
    kind: str
    contract: str
    schema_version: int = Field(ge=1)
    dataset_id: str
    created_at: datetime
    row_count: int = Field(ge=0)
    file: str
    file_sha256: str
    lineage: dict[str, JsonValue]
    metadata: dict[str, JsonValue]
    application_version: str
    image_identity: str


@dataclass(frozen=True, slots=True)
class FoundationChildManifest:
    """Physical identity of one independently stored foundation child."""

    manifest_id: str
    manifest_sha256: str
    kind: str
    contract: str
    schema_version: int
    dataset_id: str
    created_at: datetime
    row_count: int
    file: str
    file_sha256: str
    lineage: Mapping[str, JsonValue]
    metadata: Mapping[str, JsonValue]
    application_version: str
    image_identity: str

    def __post_init__(self) -> None:
        _validate_manifest(self)

    @property
    def manifest_path(self) -> str:
        return f"{_MANIFEST_ROOT}/{self.manifest_id}.json"


class ParquetFoundationArtifactStore:
    """Store generic canonical JSON rows in independently authenticated Parquet files."""

    def __init__(self, root: Path, clock: Clock) -> None:
        self._root = root
        self._clock = clock

    async def write(
        self,
        *,
        kind: str,
        contract: str,
        schema_version: int,
        dataset_id: str,
        rows: Sequence[Mapping[str, JsonValue]],
        lineage: Mapping[str, JsonValue],
        metadata: Mapping[str, JsonValue] | None = None,
        application_version: str,
        image_identity: str,
    ) -> FoundationChildManifest:
        return await asyncio.to_thread(
            self._write_sync,
            kind,
            contract,
            schema_version,
            dataset_id,
            tuple(rows),
            dict(lineage),
            dict(metadata or {}),
            application_version,
            image_identity,
        )

    async def read_manifest(self, manifest_id: str) -> FoundationChildManifest:
        return await asyncio.to_thread(self._read_manifest_sync, manifest_id)

    async def read_rows(self, manifest_id: str) -> tuple[dict[str, JsonValue], ...]:
        return await asyncio.to_thread(self._read_rows_sync, manifest_id)

    async def verify(self, manifest_id: str) -> FoundationChildManifest:
        manifest = await self.read_manifest(manifest_id)
        await self.read_rows(manifest_id)
        return manifest

    def _write_sync(
        self,
        kind: str,
        contract: str,
        schema_version: int,
        dataset_id: str,
        rows: tuple[Mapping[str, JsonValue], ...],
        lineage: Mapping[str, JsonValue],
        metadata: Mapping[str, JsonValue],
        application_version: str,
        image_identity: str,
    ) -> FoundationChildManifest:
        _require_kind(kind)
        _require_text(contract, "foundation child contract")
        _require_sha256(dataset_id, "foundation child dataset ID")
        _require_text(application_version, "application version")
        _require_text(image_identity, "image identity")
        if schema_version <= 0:
            raise ValueError("foundation child schema version must be positive")
        payloads = tuple(_canonical_row(row) for row in rows)
        relative = f"{_ARTEFACT_ROOT}/{kind}/part-{dataset_id[:24]}.parquet"
        destination = self._root / PurePosixPath(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        encoded = _parquet_bytes(payloads)
        if destination.exists():
            if _read_payloads(destination) != payloads:
                raise RuntimeError(
                    f"existing foundation child conflicts with its dataset identity: {relative}"
                )
        else:
            try:
                with destination.open("xb") as output:
                    output.write(encoded)
            except FileExistsError as error:
                if _read_payloads(destination) != payloads:
                    raise RuntimeError(
                        f"existing foundation child conflicts with its dataset identity: {relative}"
                    ) from error
        file_sha256 = hashlib.sha256(destination.read_bytes()).hexdigest()
        created_at = self._clock.now()
        identity = _identity_payload(
            kind=kind,
            contract=contract,
            schema_version=schema_version,
            dataset_id=dataset_id,
            created_at=created_at,
            row_count=len(rows),
            file=relative,
            file_sha256=file_sha256,
            lineage=lineage,
            metadata=metadata,
            application_version=application_version,
            image_identity=image_identity,
        )
        manifest_sha256 = _sha256_json(identity)
        manifest = FoundationChildManifest(
            manifest_id=manifest_sha256[:24],
            manifest_sha256=manifest_sha256,
            kind=kind,
            contract=contract,
            schema_version=schema_version,
            dataset_id=dataset_id,
            created_at=created_at,
            row_count=len(rows),
            file=relative,
            file_sha256=file_sha256,
            lineage=lineage,
            metadata=metadata,
            application_version=application_version,
            image_identity=image_identity,
        )
        manifest_path = self._root / manifest.manifest_path
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        encoded_manifest = json.dumps(_manifest_json(manifest), indent=2, sort_keys=True) + "\n"
        if len(encoded_manifest.encode("utf-8")) > _MAX_MANIFEST_BYTES:
            raise ValueError("foundation child manifest exceeds the 4 MiB limit")
        try:
            with manifest_path.open("x", encoding="utf-8") as output:
                output.write(encoded_manifest)
        except FileExistsError as error:
            existing = self._read_manifest_sync(manifest.manifest_id)
            if _identity_payload_from_manifest(existing) != identity:
                raise RuntimeError(
                    "existing foundation child manifest conflicts with its identity"
                ) from error
            self._read_rows_sync(existing.manifest_id)
            return existing
        return manifest

    def _read_manifest_sync(self, manifest_id: str) -> FoundationChildManifest:
        _require_manifest_id(manifest_id)
        encoded = (self._root / _MANIFEST_ROOT / f"{manifest_id}.json").read_bytes()
        if len(encoded) > _MAX_MANIFEST_BYTES:
            raise ValueError("foundation child manifest exceeds the 4 MiB limit")
        model = _ManifestModel.model_validate_json(encoded)
        manifest = FoundationChildManifest(
            manifest_id=model.manifest_id,
            manifest_sha256=model.manifest_sha256,
            kind=model.kind,
            contract=model.contract,
            schema_version=model.schema_version,
            dataset_id=model.dataset_id,
            created_at=model.created_at,
            row_count=model.row_count,
            file=model.file,
            file_sha256=model.file_sha256,
            lineage=model.lineage,
            metadata=model.metadata,
            application_version=model.application_version,
            image_identity=model.image_identity,
        )
        if manifest.manifest_id != manifest_id:
            raise ValueError("foundation child manifest filename does not match its identity")
        if _sha256_json(_identity_payload_from_manifest(manifest)) != manifest.manifest_sha256:
            raise ValueError("foundation child manifest hash does not match its canonical content")
        return manifest

    def _read_rows_sync(self, manifest_id: str) -> tuple[dict[str, JsonValue], ...]:
        manifest = self._read_manifest_sync(manifest_id)
        path = self._root / _safe_file(manifest.file)
        if hashlib.sha256(path.read_bytes()).hexdigest() != manifest.file_sha256:
            raise ValueError(f"foundation child file hash mismatch: {manifest.file}")
        payloads = _read_payloads(path)
        if len(payloads) != manifest.row_count:
            raise ValueError("foundation child row count does not match its manifest")
        rows: list[dict[str, JsonValue]] = []
        for payload in payloads:
            value = json.loads(payload)
            if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
                raise ValueError("foundation child row must be a JSON object")
            rows.append(value)
        return tuple(rows)


def _canonical_row(row: Mapping[str, JsonValue]) -> str:
    return json.dumps(to_json_value(dict(row)), sort_keys=True, separators=(",", ":"))


def _parquet_bytes(payloads: Sequence[str]) -> bytes:
    buffer = io.BytesIO()
    pl.DataFrame({"payload": list(payloads)}, schema={"payload": pl.String}).write_parquet(buffer)
    return buffer.getvalue()


def _read_payloads(path: Path) -> tuple[str, ...]:
    frame = pl.read_parquet(path)
    if frame.schema != {"payload": pl.String}:
        raise ValueError("foundation child Parquet schema is unsupported")
    return tuple(str(value) for value in frame.get_column("payload").to_list())


def _identity_payload(
    *,
    kind: str,
    contract: str,
    schema_version: int,
    dataset_id: str,
    created_at: datetime,
    row_count: int,
    file: str,
    file_sha256: str,
    lineage: Mapping[str, JsonValue],
    metadata: Mapping[str, JsonValue],
    application_version: str,
    image_identity: str,
) -> dict[str, JsonValue]:
    return {
        "kind": kind,
        "contract": contract,
        "schema_version": schema_version,
        "dataset_id": dataset_id,
        "created_at": created_at.isoformat(),
        "row_count": row_count,
        "file": file,
        "file_sha256": file_sha256,
        "lineage": dict(lineage),
        "metadata": dict(metadata),
        "application_version": application_version,
        "image_identity": image_identity,
    }


def _identity_payload_from_manifest(manifest: FoundationChildManifest) -> dict[str, JsonValue]:
    return _identity_payload(
        kind=manifest.kind,
        contract=manifest.contract,
        schema_version=manifest.schema_version,
        dataset_id=manifest.dataset_id,
        created_at=manifest.created_at,
        row_count=manifest.row_count,
        file=manifest.file,
        file_sha256=manifest.file_sha256,
        lineage=manifest.lineage,
        metadata=manifest.metadata,
        application_version=manifest.application_version,
        image_identity=manifest.image_identity,
    )


def _manifest_json(manifest: FoundationChildManifest) -> dict[str, JsonValue]:
    payload = _identity_payload_from_manifest(manifest)
    payload["manifest_id"] = manifest.manifest_id
    payload["manifest_sha256"] = manifest.manifest_sha256
    return payload


def _validate_manifest(manifest: FoundationChildManifest) -> None:
    _require_manifest_id(manifest.manifest_id)
    _require_sha256(manifest.manifest_sha256, "foundation child manifest hash")
    _require_sha256(manifest.dataset_id, "foundation child dataset ID")
    _require_sha256(manifest.file_sha256, "foundation child file hash")
    _require_kind(manifest.kind)
    _require_text(manifest.contract, "foundation child contract")
    _require_text(manifest.application_version, "application version")
    _require_text(manifest.image_identity, "image identity")
    if manifest.schema_version <= 0 or manifest.row_count < 0:
        raise ValueError("foundation child schema version and row count are invalid")
    if manifest.manifest_id != manifest.manifest_sha256[:24]:
        raise ValueError("foundation child manifest ID must match its hash")
    if manifest.created_at.tzinfo is None or manifest.created_at.utcoffset() != UTC.utcoffset(
        manifest.created_at
    ):
        raise ValueError("foundation child manifest creation time must be UTC")
    _safe_file(manifest.file)


def _safe_file(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or len(path.parts) != 3
        or path.parts[0] != _ARTEFACT_ROOT
        or path.parts[1] not in _KINDS
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.suffix != ".parquet"
    ):
        raise ValueError(f"unsafe foundation child file path: {value}")
    return path


def _require_kind(value: str) -> None:
    if value not in _KINDS:
        raise ValueError(f"unsupported foundation child kind: {value}")


def _require_manifest_id(value: str) -> None:
    if len(value) != 24 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(
            "foundation child manifest ID must be 24 lower-case hexadecimal characters"
        )


def _require_sha256(value: str, field: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} must be a lower-case SHA-256")


def _require_text(value: str, field: str) -> None:
    if not value:
        raise ValueError(f"{field} must be non-empty")


def _sha256_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(to_json_value(value), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
