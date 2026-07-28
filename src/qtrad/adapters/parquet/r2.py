"""Bounded, immutable Parquet persistence for R2 raw-feature rows."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import cast

import polars as pl

from qtrad.domain.events import JsonValue, to_json_value
from qtrad.domain.r2_features import (
    FeatureDatasetSemanticHasher,
    FeatureDefinition,
    FeatureKind,
    R2FeatureDataset,
    RawFeatureRow,
    RawFeatureValue,
    feature_schema_id,
)
from qtrad.domain.r2_features import (
    feature_set_id as canonical_feature_set_id,
)
from qtrad.domain.r2_readiness import EvidenceClass, FeatureFamily
from qtrad.domain.time import require_utc
from qtrad.ports.clock import Clock

R2_PARQUET_MANIFEST_CONTRACT = "qtrad-r2-feature-parquet-v1"
R2_PARQUET_MANIFEST_SCHEMA_VERSION = 1
_MAX_MANIFEST_BYTES = 4 * 1024 * 1024
_DEFAULT_CHUNK_ROWS = 8192
_DATA_ROOT = "chunks"
_LINEAGE_ROOT = "lineage"

_IDENTITY_COLUMNS = (
    "target_instrument_id",
    "decision_time",
    "feature_data_asof",
    "latest_feature_bar_end",
    "feature_set_id",
)


@dataclass(frozen=True, slots=True)
class R2FeatureChunkReference:
    """Physical identity and bounds for one bounded data/lineage pair."""

    index: int
    data_file: str
    data_sha256: str
    lineage_file: str
    lineage_sha256: str
    row_count: int
    first_row_key: str
    last_row_key: str
    semantic_hash: str

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "index": self.index,
            "data_file": self.data_file,
            "data_sha256": self.data_sha256,
            "lineage_file": self.lineage_file,
            "lineage_sha256": self.lineage_sha256,
            "row_count": self.row_count,
            "first_row_key": self.first_row_key,
            "last_row_key": self.last_row_key,
            "semantic_hash": self.semantic_hash,
        }


@dataclass(frozen=True, slots=True)
class R2FeatureManifest:
    """Strict physical manifest over a semantic R2 feature dataset."""

    manifest_id: str
    manifest_sha256: str
    manifest_filename: str
    created_at: datetime
    semantic_dataset_id: str
    feature_set_name: str
    feature_set_id: str
    raw_feature_schema_id: str
    feature_schema: tuple[FeatureDefinition, ...]
    observation_dataset_id: str
    panel_dataset_id: str
    target_dataset_id: str
    fold_dataset_id: str
    experiment_configuration_id: str
    evidence_class: EvidenceClass
    holdout_excluded: bool
    row_count: int
    chunk_row_limit: int
    chunks: tuple[R2FeatureChunkReference, ...]
    application_version: str
    image_identity: str

    CONTRACT = R2_PARQUET_MANIFEST_CONTRACT
    SCHEMA_VERSION = R2_PARQUET_MANIFEST_SCHEMA_VERSION

    def as_json(self) -> dict[str, JsonValue]:
        payload = _manifest_identity_payload(self)
        payload["manifest_id"] = self.manifest_id
        payload["manifest_sha256"] = self.manifest_sha256
        payload["manifest_filename"] = self.manifest_filename
        return cast(dict[str, JsonValue], to_json_value(payload))

    @property
    def manifest_path(self) -> str:
        return self.manifest_filename


class ParquetR2FeatureStore:
    """Write and verify R2 rows in bounded chunks with no-clobber publication."""

    def __init__(self, root: Path, clock: Clock, *, chunk_rows: int = _DEFAULT_CHUNK_ROWS) -> None:
        if chunk_rows <= 0:
            raise ValueError("R2 Parquet chunk row limit must be positive")
        self._root = root
        self._clock = clock
        self._chunk_rows = chunk_rows

    def write(
        self,
        manifest_path: Path,
        rows: Iterable[RawFeatureRow],
        *,
        feature_set_name: str,
        feature_set_id: str,
        feature_schema: Sequence[FeatureDefinition],
        observation_dataset_id: str,
        panel_dataset_id: str,
        target_dataset_id: str,
        fold_dataset_id: str,
        experiment_configuration_id: str,
        evidence_class: EvidenceClass,
        holdout_excluded: bool,
        application_version: str,
        image_identity: str,
    ) -> R2FeatureManifest:
        """Publish a new immutable manifest and its bounded chunks."""
        path = self._resolve_manifest_path(manifest_path)
        if path.is_symlink():
            raise ValueError("R2 feature manifest must not be a symlink")
        if path.exists():
            if not path.is_file():
                raise ValueError("R2 feature manifest must be a regular file")
            raise RuntimeError("existing R2 feature manifest cannot be republished")
        _require_text(feature_set_name, "feature set name")
        _require_sha256(feature_set_id, "feature set ID")
        _require_text(application_version, "application version")
        _require_text(image_identity, "image identity")
        if not holdout_excluded:
            raise ValueError("R2 feature Parquet output must exclude the locked holdout")
        schema = tuple(feature_schema)
        if not schema:
            raise ValueError("R2 feature Parquet schema must be non-empty")
        expected_feature_set_id = canonical_feature_set_id(
            experiment_configuration_id,
            feature_set_name,
            schema,
        )
        if feature_set_id != expected_feature_set_id:
            raise ValueError("R2 feature-set ID does not match its declared name and schema")
        raw_schema_id = feature_schema_id(schema)
        created_at = self._clock.now()
        require_utc(created_at, "R2 feature manifest creation time")
        semantic_hasher = FeatureDatasetSemanticHasher(
            feature_schema=schema,
            feature_set_name=feature_set_name,
            feature_set_identity=feature_set_id,
            observation_dataset_id=observation_dataset_id,
            panel_dataset_id=panel_dataset_id,
            target_dataset_id=target_dataset_id,
            fold_dataset_id=fold_dataset_id,
            experiment_configuration_id=experiment_configuration_id,
            evidence_class=evidence_class,
            holdout_excluded=holdout_excluded,
        )
        chunk_refs: list[R2FeatureChunkReference] = []
        buffer: list[RawFeatureRow] = []
        previous_key: tuple[datetime, str, datetime, datetime, str] | None = None
        row_count = 0
        try:
            for row in rows:
                if row.feature_set_id != feature_set_id:
                    raise ValueError("R2 row feature-set ID differs from the manifest")
                if tuple(value.name for value in row.values) != tuple(item.name for item in schema):
                    raise ValueError("R2 row schema differs from the manifest")
                key = _row_order_key(row)
                if previous_key is not None and key <= previous_key:
                    raise ValueError("R2 rows must use strict canonical global ordering")
                previous_key = key
                semantic_hasher.update(row)
                buffer.append(row)
                row_count += 1
                if len(buffer) == self._chunk_rows:
                    chunk_refs.append(
                        self._publish_chunk(path, len(chunk_refs), tuple(buffer), schema)
                    )
                    buffer.clear()
            if buffer:
                chunk_refs.append(self._publish_chunk(path, len(chunk_refs), tuple(buffer), schema))
            semantic_dataset_id = semantic_hasher.hexdigest()
            manifest = _build_manifest(
                path=path,
                created_at=created_at,
                semantic_dataset_id=semantic_dataset_id,
                feature_set_name=feature_set_name,
                feature_set_id=feature_set_id,
                raw_feature_schema_id=raw_schema_id,
                feature_schema=schema,
                observation_dataset_id=observation_dataset_id,
                panel_dataset_id=panel_dataset_id,
                target_dataset_id=target_dataset_id,
                fold_dataset_id=fold_dataset_id,
                experiment_configuration_id=experiment_configuration_id,
                evidence_class=evidence_class,
                holdout_excluded=holdout_excluded,
                row_count=row_count,
                chunk_row_limit=self._chunk_rows,
                chunks=tuple(chunk_refs),
                application_version=application_version,
                image_identity=image_identity,
            )
            self._publish_manifest(path, manifest)
            return manifest
        except BaseException:
            # Chunk files are content-addressed by semantic set and chunk limit. They are
            # harmless orphan evidence; the absence of a manifest makes the publication unusable.
            if not path.exists():
                _remove_empty_manifest_parent(path)
            raise

    def read_manifest(self, manifest_path: Path) -> R2FeatureManifest:
        path = self._resolve_manifest_path(manifest_path)
        return _read_manifest(path)

    def iter_rows(self, manifest_path: Path) -> Iterator[RawFeatureRow]:
        path = self._resolve_manifest_path(manifest_path)
        manifest = _read_manifest(path)
        expected_key: tuple[datetime, str, datetime, datetime, str] | None = None
        observed_count = 0
        semantic_hasher = _semantic_hasher(manifest)
        for chunk in manifest.chunks:
            data_path = _safe_child(path.parent, chunk.data_file, _DATA_ROOT)
            lineage_path = _safe_child(path.parent, chunk.lineage_file, _LINEAGE_ROOT)
            if _sha256_file(data_path) != chunk.data_sha256:
                raise ValueError(f"R2 feature data chunk hash mismatch: {chunk.data_file}")
            if _sha256_file(lineage_path) != chunk.lineage_sha256:
                raise ValueError(f"R2 feature lineage chunk hash mismatch: {chunk.lineage_file}")
            rows = _read_chunk(data_path, lineage_path, manifest.feature_schema)
            if any(row.feature_set_id != manifest.feature_set_id for row in rows):
                raise ValueError("R2 feature chunk contains an unexpected feature-set ID")
            if len(rows) != chunk.row_count:
                raise ValueError("R2 feature chunk row count differs from its manifest")
            if not rows:
                raise ValueError("R2 feature manifest contains an empty chunk")
            if (
                _row_key_text(rows[0]) != chunk.first_row_key
                or _row_key_text(rows[-1]) != chunk.last_row_key
            ):
                raise ValueError("R2 feature chunk row bounds differ from its manifest")
            chunk_hash = _chunk_semantic_hash(rows)
            if chunk_hash != chunk.semantic_hash:
                raise ValueError("R2 feature chunk semantic hash mismatch")
            for row in rows:
                key = _row_order_key(row)
                if expected_key is not None and key <= expected_key:
                    raise ValueError("R2 feature rows are not globally canonical or unique")
                expected_key = key
                semantic_hasher.update(row)
                observed_count += 1
                yield row
        if observed_count != manifest.row_count:
            raise ValueError("R2 feature row count differs from its manifest")
        if semantic_hasher.hexdigest() != manifest.semantic_dataset_id:
            raise ValueError("R2 feature semantic dataset identity mismatch")
        _validate_chunk_directories(manifest)

    def verify(self, manifest_path: Path) -> R2FeatureManifest:
        """Verify manifest, every bounded chunk, lineage and semantic identity."""
        manifest = self.read_manifest(manifest_path)
        for _ in self.iter_rows(manifest_path):
            pass
        return manifest

    def load(self, manifest_path: Path) -> R2FeatureDataset:
        """Load a bounded artefact for small fixtures; verification remains chunked."""
        manifest = self.verify(manifest_path)
        rows = tuple(self.iter_rows(manifest_path))
        dataset = R2FeatureDataset.create(
            rows,
            feature_schema=manifest.feature_schema,
            feature_set_name=manifest.feature_set_name,
            feature_set_id=manifest.feature_set_id,
            observation_dataset_id=manifest.observation_dataset_id,
            panel_dataset_id=manifest.panel_dataset_id,
            target_dataset_id=manifest.target_dataset_id,
            fold_dataset_id=manifest.fold_dataset_id,
            experiment_configuration_id=manifest.experiment_configuration_id,
            evidence_class=manifest.evidence_class,
        )
        if dataset.dataset_id != manifest.semantic_dataset_id:
            raise ValueError("loaded R2 feature dataset identity differs from its manifest")
        return dataset

    def _resolve_manifest_path(self, path: Path) -> Path:
        resolved = path if path.is_absolute() else self._root / path
        if resolved.suffix != ".json":
            raise ValueError("R2 feature manifest path must be a JSON file")
        _reject_unsafe_path(resolved, self._root)
        return resolved

    def _publish_chunk(
        self,
        manifest_path: Path,
        index: int,
        rows: tuple[RawFeatureRow, ...],
        schema: tuple[FeatureDefinition, ...],
    ) -> R2FeatureChunkReference:
        semantic_id = _chunk_semantic_hash(rows)
        relative_base = f"{_DATA_ROOT}/{_chunk_directory(rows[0].feature_set_id, self._chunk_rows)}"
        data_file = f"{relative_base}/chunk-{semantic_id}.parquet"
        lineage_file = (
            f"{_LINEAGE_ROOT}/{_chunk_directory(rows[0].feature_set_id, self._chunk_rows)}"
            f"/chunk-{semantic_id}.parquet"
        )
        data_path = _safe_child(manifest_path.parent, data_file, _DATA_ROOT)
        lineage_path = _safe_child(manifest_path.parent, lineage_file, _LINEAGE_ROOT)
        data_frame, lineage_frame = _chunk_frames(rows, schema)
        _publish_parquet(data_path, data_frame)
        _publish_parquet(lineage_path, lineage_frame)
        return R2FeatureChunkReference(
            index=index,
            data_file=data_file,
            data_sha256=_sha256_file(data_path),
            lineage_file=lineage_file,
            lineage_sha256=_sha256_file(lineage_path),
            row_count=len(rows),
            first_row_key=_row_key_text(rows[0]),
            last_row_key=_row_key_text(rows[-1]),
            semantic_hash=semantic_id,
        )

    def _publish_manifest(self, path: Path, manifest: R2FeatureManifest) -> None:
        payload = manifest.as_json()
        encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        encoded_bytes = encoded.encode("utf-8")
        if len(encoded_bytes) > _MAX_MANIFEST_BYTES:
            raise ValueError("R2 feature manifest exceeds the 4 MiB limit")
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            raise ValueError("R2 feature manifest must not be a symlink")
        if path.exists():
            if not path.is_file():
                raise ValueError("R2 feature manifest must be a regular file")
            existing = _read_manifest(path)
            if existing.as_json() != payload:
                raise RuntimeError("existing R2 feature manifest conflicts with its identity")
            return

        def verify_existing() -> None:
            existing = _read_manifest(path)
            if existing.as_json() != payload:
                raise RuntimeError("existing R2 feature manifest conflicts with its identity")

        _atomic_write(path, encoded_bytes, verify_existing=verify_existing)


def _build_manifest(
    *,
    path: Path,
    created_at: datetime,
    semantic_dataset_id: str,
    feature_set_name: str,
    feature_set_id: str,
    raw_feature_schema_id: str,
    feature_schema: tuple[FeatureDefinition, ...],
    observation_dataset_id: str,
    panel_dataset_id: str,
    target_dataset_id: str,
    fold_dataset_id: str,
    experiment_configuration_id: str,
    evidence_class: EvidenceClass,
    holdout_excluded: bool,
    row_count: int,
    chunk_row_limit: int,
    chunks: tuple[R2FeatureChunkReference, ...],
    application_version: str,
    image_identity: str,
) -> R2FeatureManifest:
    unbound = R2FeatureManifest(
        manifest_id="0" * 24,
        manifest_sha256="0" * 64,
        manifest_filename=path.name,
        created_at=created_at,
        semantic_dataset_id=semantic_dataset_id,
        feature_set_name=feature_set_name,
        feature_set_id=feature_set_id,
        raw_feature_schema_id=raw_feature_schema_id,
        feature_schema=feature_schema,
        observation_dataset_id=observation_dataset_id,
        panel_dataset_id=panel_dataset_id,
        target_dataset_id=target_dataset_id,
        fold_dataset_id=fold_dataset_id,
        experiment_configuration_id=experiment_configuration_id,
        evidence_class=evidence_class,
        holdout_excluded=holdout_excluded,
        row_count=row_count,
        chunk_row_limit=chunk_row_limit,
        chunks=chunks,
        application_version=application_version,
        image_identity=image_identity,
    )
    identity = _manifest_identity_payload(unbound)
    digest = _sha256_json(identity)
    return R2FeatureManifest(
        manifest_id=digest[:24],
        manifest_sha256=digest,
        manifest_filename=path.name,
        created_at=created_at,
        semantic_dataset_id=semantic_dataset_id,
        feature_set_name=feature_set_name,
        feature_set_id=feature_set_id,
        raw_feature_schema_id=raw_feature_schema_id,
        feature_schema=feature_schema,
        observation_dataset_id=observation_dataset_id,
        panel_dataset_id=panel_dataset_id,
        target_dataset_id=target_dataset_id,
        fold_dataset_id=fold_dataset_id,
        experiment_configuration_id=experiment_configuration_id,
        evidence_class=evidence_class,
        holdout_excluded=holdout_excluded,
        row_count=row_count,
        chunk_row_limit=chunk_row_limit,
        chunks=chunks,
        application_version=application_version,
        image_identity=image_identity,
    )


def _manifest_identity_payload(manifest: R2FeatureManifest) -> dict[str, JsonValue]:
    return {
        "contract": R2_PARQUET_MANIFEST_CONTRACT,
        "schema_version": R2_PARQUET_MANIFEST_SCHEMA_VERSION,
        "manifest_filename": manifest.manifest_filename,
        "created_at": manifest.created_at.isoformat(),
        "semantic_dataset_id": manifest.semantic_dataset_id,
        "feature_set_name": manifest.feature_set_name,
        "feature_set_id": manifest.feature_set_id,
        "raw_feature_schema_id": manifest.raw_feature_schema_id,
        "feature_schema": [item.as_json() for item in manifest.feature_schema],
        "observation_dataset_id": manifest.observation_dataset_id,
        "panel_dataset_id": manifest.panel_dataset_id,
        "target_dataset_id": manifest.target_dataset_id,
        "fold_dataset_id": manifest.fold_dataset_id,
        "experiment_configuration_id": manifest.experiment_configuration_id,
        "evidence_class": manifest.evidence_class.value,
        "holdout_excluded": manifest.holdout_excluded,
        "row_count": manifest.row_count,
        "chunk_row_limit": manifest.chunk_row_limit,
        "chunks": [item.as_json() for item in manifest.chunks],
        "application_version": manifest.application_version,
        "image_identity": manifest.image_identity,
    }


def _read_manifest(path: Path) -> R2FeatureManifest:
    if path.is_symlink() or not path.is_file():
        raise ValueError("R2 feature manifest must be a regular non-symlink file")
    encoded = path.read_bytes()
    if len(encoded) > _MAX_MANIFEST_BYTES:
        raise ValueError("R2 feature manifest exceeds the 4 MiB limit")
    raw = _mapping(json.loads(encoded, object_pairs_hook=_strict_object))
    expected = {
        "contract",
        "schema_version",
        "manifest_id",
        "manifest_sha256",
        "manifest_filename",
        "created_at",
        "semantic_dataset_id",
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
        "holdout_excluded",
        "row_count",
        "chunk_row_limit",
        "chunks",
        "application_version",
        "image_identity",
    }
    if set(raw) != expected:
        raise ValueError("R2 feature manifest has unknown or missing fields")
    manifest = R2FeatureManifest(
        manifest_id=_hex(raw["manifest_id"], 24, "manifest ID"),
        manifest_sha256=_hex(raw["manifest_sha256"], 64, "manifest hash"),
        manifest_filename=_text(raw["manifest_filename"], "manifest filename"),
        created_at=_datetime(raw["created_at"]),
        semantic_dataset_id=_hex(raw["semantic_dataset_id"], 64, "semantic dataset ID"),
        feature_set_name=_text(raw["feature_set_name"], "feature set name"),
        feature_set_id=_hex(raw["feature_set_id"], 64, "feature set ID"),
        raw_feature_schema_id=_hex(raw["raw_feature_schema_id"], 64, "raw schema ID"),
        feature_schema=tuple(
            _feature_definition(item) for item in _sequence(raw["feature_schema"])
        ),
        observation_dataset_id=_hex(raw["observation_dataset_id"], 64, "observation dataset ID"),
        panel_dataset_id=_hex(raw["panel_dataset_id"], 64, "panel dataset ID"),
        target_dataset_id=_hex(raw["target_dataset_id"], 64, "target dataset ID"),
        fold_dataset_id=_hex(raw["fold_dataset_id"], 64, "fold dataset ID"),
        experiment_configuration_id=_hex(
            raw["experiment_configuration_id"], 64, "experiment configuration ID"
        ),
        evidence_class=EvidenceClass(_text(raw["evidence_class"], "evidence class")),
        holdout_excluded=_bool(raw["holdout_excluded"]),
        row_count=_nonnegative_int(raw["row_count"], "row count"),
        chunk_row_limit=_positive_int(raw["chunk_row_limit"], "chunk row limit"),
        chunks=tuple(_chunk_reference(item) for item in _sequence(raw["chunks"])),
        application_version=_text(raw["application_version"], "application version"),
        image_identity=_text(raw["image_identity"], "image identity"),
    )
    if manifest.manifest_filename != path.name:
        raise ValueError("R2 feature manifest filename does not match its path")
    if manifest.manifest_id != manifest.manifest_sha256[:24]:
        raise ValueError("R2 feature manifest ID does not match its hash")
    if _sha256_json(_manifest_identity_payload(manifest)) != manifest.manifest_sha256:
        raise ValueError("R2 feature manifest hash does not match its canonical content")
    if manifest.raw_feature_schema_id != feature_schema_id(manifest.feature_schema):
        raise ValueError("R2 feature manifest schema identity is invalid")
    if manifest.row_count != sum(item.row_count for item in manifest.chunks):
        raise ValueError("R2 feature manifest row count does not match its chunks")
    if tuple(item.index for item in manifest.chunks) != tuple(range(len(manifest.chunks))):
        raise ValueError("R2 feature manifest chunk indexes are not canonical")
    if len({item.data_file for item in manifest.chunks}) != len(manifest.chunks):
        raise ValueError("R2 feature manifest contains duplicate data chunks")
    if len({item.lineage_file for item in manifest.chunks}) != len(manifest.chunks):
        raise ValueError("R2 feature manifest contains duplicate lineage chunks")
    if not manifest.feature_schema:
        raise ValueError("R2 feature manifest schema must be non-empty")
    if manifest.feature_set_id != canonical_feature_set_id(
        manifest.experiment_configuration_id,
        manifest.feature_set_name,
        manifest.feature_schema,
    ):
        raise ValueError("R2 feature manifest feature-set identity is invalid")
    for chunk in manifest.chunks:
        _validate_manifest_chunk_path(chunk.data_file, _DATA_ROOT)
        _validate_manifest_chunk_path(chunk.lineage_file, _LINEAGE_ROOT)
    return manifest


def _chunk_reference(value: object) -> R2FeatureChunkReference:
    raw = _mapping(value)
    expected = {
        "index",
        "data_file",
        "data_sha256",
        "lineage_file",
        "lineage_sha256",
        "row_count",
        "first_row_key",
        "last_row_key",
        "semantic_hash",
    }
    if set(raw) != expected:
        raise ValueError("R2 feature chunk reference has unknown or missing fields")
    return R2FeatureChunkReference(
        index=_nonnegative_int(raw["index"], "chunk index"),
        data_file=_text(raw["data_file"], "chunk data path"),
        data_sha256=_hex(raw["data_sha256"], 64, "chunk data hash"),
        lineage_file=_text(raw["lineage_file"], "chunk lineage path"),
        lineage_sha256=_hex(raw["lineage_sha256"], 64, "chunk lineage hash"),
        row_count=_positive_int(raw["row_count"], "chunk row count"),
        first_row_key=_text(raw["first_row_key"], "chunk first row key"),
        last_row_key=_text(raw["last_row_key"], "chunk last row key"),
        semantic_hash=_hex(raw["semantic_hash"], 64, "chunk semantic hash"),
    )


def _feature_definition(value: object) -> FeatureDefinition:
    raw = _mapping(value)
    if set(raw) != {"name", "family", "kind", "availability_indicator"}:
        raise ValueError("R2 feature definition has unknown or missing fields")
    return FeatureDefinition(
        name=_text(raw["name"], "feature name"),
        family=FeatureFamily(_text(raw["family"], "feature family")),
        kind=FeatureKind(_text(raw["kind"], "feature kind")),
        availability_indicator=_bool(raw["availability_indicator"]),
    )


def _chunk_frames(
    rows: Sequence[RawFeatureRow],
    schema: Sequence[FeatureDefinition],
) -> tuple[pl.DataFrame, pl.DataFrame]:
    lineage_ids: dict[tuple[str, ...], int] = {(): 0}
    lineage_values: list[tuple[int, tuple[str, ...]]] = [(0, ())]
    data: dict[str, list[object]] = {column: [] for column in _IDENTITY_COLUMNS}
    data.update({f"f{index:04d}": [] for index in range(len(schema))})
    data.update({f"l{index:04d}": [] for index in range(len(schema))})
    data["row_hash"] = []
    for row in rows:
        data["target_instrument_id"].append(row.target_instrument_id)
        data["decision_time"].append(row.decision_time)
        data["feature_data_asof"].append(row.feature_data_asof)
        data["latest_feature_bar_end"].append(row.latest_feature_bar_end)
        data["feature_set_id"].append(row.feature_set_id)
        for index, value in enumerate(row.values):
            data[f"f{index:04d}"].append(value.value)
            lineage_id = lineage_ids.get(value.source_event_ids)
            if lineage_id is None:
                lineage_id = len(lineage_values)
                lineage_ids[value.source_event_ids] = lineage_id
                lineage_values.append((lineage_id, value.source_event_ids))
            data[f"l{index:04d}"].append(lineage_id)
        data["row_hash"].append(_row_hash(row))
    data_schema: dict[str, pl.DataType] = {
        "target_instrument_id": pl.String(),
        "decision_time": pl.Datetime("us", "UTC"),
        "feature_data_asof": pl.Datetime("us", "UTC"),
        "latest_feature_bar_end": pl.Datetime("us", "UTC"),
        "feature_set_id": pl.String(),
    }
    data_schema.update({f"f{index:04d}": pl.Float64() for index in range(len(schema))})
    data_schema.update({f"l{index:04d}": pl.UInt32() for index in range(len(schema))})
    data_schema["row_hash"] = pl.String()
    data_frame = pl.DataFrame(data, schema=data_schema, strict=True)
    lineage_frame = pl.DataFrame(
        {
            "lineage_set_id": [item[0] for item in lineage_values],
            "source_event_ids": [
                json.dumps(list(item[1]), separators=(",", ":")) for item in lineage_values
            ],
        },
        schema={"lineage_set_id": pl.UInt32(), "source_event_ids": pl.String()},
        strict=True,
    )
    return data_frame, lineage_frame


def _read_chunk(
    data_path: Path,
    lineage_path: Path,
    schema: Sequence[FeatureDefinition],
) -> tuple[RawFeatureRow, ...]:
    expected_data_schema: dict[str, pl.DataType] = {
        "target_instrument_id": pl.String(),
        "decision_time": pl.Datetime("us", "UTC"),
        "feature_data_asof": pl.Datetime("us", "UTC"),
        "latest_feature_bar_end": pl.Datetime("us", "UTC"),
        "feature_set_id": pl.String(),
    }
    expected_data_schema.update({f"f{index:04d}": pl.Float64() for index in range(len(schema))})
    expected_data_schema.update({f"l{index:04d}": pl.UInt32() for index in range(len(schema))})
    expected_data_schema["row_hash"] = pl.String()
    data = pl.read_parquet(data_path)
    lineage = pl.read_parquet(lineage_path)
    if data.schema != expected_data_schema:
        raise ValueError("R2 feature Parquet data schema is unsupported")
    if lineage.schema != {"lineage_set_id": pl.UInt32(), "source_event_ids": pl.String()}:
        raise ValueError("R2 feature Parquet lineage schema is unsupported")
    lineage_map: dict[int, tuple[str, ...]] = {}
    for raw_id, raw_ids in lineage.iter_rows():
        if raw_id in lineage_map:
            raise ValueError("R2 feature lineage contains duplicate set IDs")
        parsed = json.loads(raw_ids, object_pairs_hook=_strict_object)
        if not isinstance(parsed, list) or any(
            not isinstance(item, str) or not item for item in parsed
        ):
            raise ValueError("R2 feature lineage set is malformed")
        ids = tuple(parsed)
        if len(set(ids)) != len(ids):
            raise ValueError("R2 feature lineage set contains duplicate event IDs")
        lineage_map[int(raw_id)] = ids
    if lineage_map.get(0) != ():
        raise ValueError("R2 feature lineage set zero must be empty")
    rows: list[RawFeatureRow] = []
    for raw in data.iter_rows(named=True):
        raw_instrument = raw["target_instrument_id"]
        raw_feature_set_id = raw["feature_set_id"]
        raw_row_hash = raw["row_hash"]
        if (
            not isinstance(raw_instrument, str)
            or not raw_instrument
            or not isinstance(raw_feature_set_id, str)
            or not raw_feature_set_id
            or not isinstance(raw_row_hash, str)
        ):
            raise ValueError("R2 feature row identity columns are malformed")
        values: list[RawFeatureValue] = []
        for index, definition in enumerate(schema):
            lineage_id = int(raw[f"l{index:04d}"])
            if lineage_id not in lineage_map:
                raise ValueError("R2 feature row references an unknown lineage set")
            value = raw[f"f{index:04d}"]
            values.append(
                RawFeatureValue(
                    name=definition.name,
                    value=None if value is None else float(value),
                    source_event_ids=lineage_map[lineage_id],
                )
            )
        row = RawFeatureRow(
            target_instrument_id=raw_instrument,
            decision_time=cast(datetime, raw["decision_time"]),
            feature_data_asof=cast(datetime, raw["feature_data_asof"]),
            latest_feature_bar_end=cast(datetime, raw["latest_feature_bar_end"]),
            feature_set_id=raw_feature_set_id,
            values=tuple(values),
        )
        if raw_row_hash != _row_hash(row):
            raise ValueError("R2 feature row hash mismatch")
        rows.append(row)
    return tuple(rows)


def _semantic_hasher(manifest: R2FeatureManifest) -> FeatureDatasetSemanticHasher:
    return FeatureDatasetSemanticHasher(
        feature_schema=manifest.feature_schema,
        feature_set_name=manifest.feature_set_name,
        feature_set_identity=manifest.feature_set_id,
        observation_dataset_id=manifest.observation_dataset_id,
        panel_dataset_id=manifest.panel_dataset_id,
        target_dataset_id=manifest.target_dataset_id,
        fold_dataset_id=manifest.fold_dataset_id,
        experiment_configuration_id=manifest.experiment_configuration_id,
        evidence_class=manifest.evidence_class,
        holdout_excluded=manifest.holdout_excluded,
    )


def _chunk_semantic_hash(rows: Sequence[RawFeatureRow]) -> str:
    digest = sha256()
    for row in rows:
        encoded = json.dumps(row.as_json(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _row_order_key(row: RawFeatureRow) -> tuple[datetime, str, datetime, datetime, str]:
    return (
        row.decision_time,
        row.target_instrument_id,
        row.feature_data_asof,
        row.latest_feature_bar_end,
        row.feature_set_id,
    )


def _row_key_text(row: RawFeatureRow) -> str:
    return json.dumps(
        [
            row.target_instrument_id,
            row.decision_time.isoformat(),
            row.feature_data_asof.isoformat(),
            row.latest_feature_bar_end.isoformat(),
            row.feature_set_id,
        ],
        separators=(",", ":"),
    )


def _row_hash(row: RawFeatureRow) -> str:
    return _sha256_json(row.as_json())


def _publish_parquet(path: Path, frame: pl.DataFrame) -> None:
    encoded_schema = frame.schema
    if path.is_symlink():
        raise ValueError("R2 feature Parquet chunk must not be a symlink")
    if path.exists():
        if _sha256_file(path) != _sha256_bytes_from_frame(frame):
            existing = pl.read_parquet(path)
            if existing.schema != encoded_schema:
                raise RuntimeError("existing R2 feature chunk schema conflicts with its identity")
            raise RuntimeError("existing R2 feature chunk conflicts with its identity")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
    ) as output:
        temporary = Path(output.name)
    try:
        frame.write_parquet(temporary)
        _link_no_clobber(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _sha256_bytes_from_frame(frame: pl.DataFrame) -> str:
    with tempfile.NamedTemporaryFile(suffix=".parquet", delete=True) as output:
        temporary = Path(output.name)
        frame.write_parquet(temporary)
        return _sha256_file(temporary)


def _link_no_clobber(source: Path, destination: Path) -> None:
    try:
        os.link(source, destination)
    except FileExistsError:
        raise RuntimeError(
            f"existing R2 feature chunk conflicts with its identity: {destination}"
        ) from None


def _atomic_write(path: Path, content: bytes, *, verify_existing: Callable[[], None]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
    ) as output:
        temporary = Path(output.name)
        output.write(content)
        output.flush()
        os.fsync(output.fileno())
    try:
        try:
            os.link(temporary, path)
        except FileExistsError:
            verify_existing()
    finally:
        temporary.unlink(missing_ok=True)


def _validate_chunk_directories(manifest: R2FeatureManifest) -> None:
    expected_data_parent = PurePosixPath(_DATA_ROOT) / _chunk_directory(
        manifest.feature_set_id,
        manifest.chunk_row_limit,
    )
    expected_lineage_parent = PurePosixPath(_LINEAGE_ROOT) / _chunk_directory(
        manifest.feature_set_id,
        manifest.chunk_row_limit,
    )
    data_parent = (
        PurePosixPath(manifest.chunks[0].data_file).parent
        if manifest.chunks
        else expected_data_parent
    )
    lineage_parent = (
        PurePosixPath(manifest.chunks[0].lineage_file).parent
        if manifest.chunks
        else expected_lineage_parent
    )
    if data_parent != expected_data_parent or lineage_parent != expected_lineage_parent:
        raise ValueError("R2 feature chunks use an unexpected directory")
    if any(PurePosixPath(item.data_file).parent != data_parent for item in manifest.chunks):
        raise ValueError("R2 feature chunks use multiple data directories")
    if any(PurePosixPath(item.lineage_file).parent != lineage_parent for item in manifest.chunks):
        raise ValueError("R2 feature chunks use multiple lineage directories")
    if any(
        PurePosixPath(item.data_file).name != f"chunk-{item.semantic_hash}.parquet"
        for item in manifest.chunks
    ):
        raise ValueError("R2 feature data chunks are not content-addressed")
    if any(
        PurePosixPath(item.lineage_file).name != f"chunk-{item.semantic_hash}.parquet"
        for item in manifest.chunks
    ):
        raise ValueError("R2 feature lineage chunks are not content-addressed")


def _validate_manifest_chunk_path(relative: str, expected_root: str) -> None:
    path = PurePosixPath(relative)
    if (
        path.is_absolute()
        or not path.parts
        or path.parts[0] != expected_root
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.suffix != ".parquet"
    ):
        raise ValueError(f"unsafe R2 feature chunk path: {relative}")


def _safe_child(parent: Path, relative: str, expected_root: str) -> Path:
    _validate_manifest_chunk_path(relative, expected_root)
    destination = parent / PurePosixPath(relative)
    _reject_unsafe_path(destination, parent)
    return destination


def _reject_unsafe_path(path: Path, root: Path) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise ValueError("R2 feature path escapes its store root") from error
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("R2 feature path is unsafe")
    current = root
    for part in relative.parts[:-1]:
        current /= part
        if current.is_symlink():
            raise ValueError("R2 feature path traverses a symlink")


def _chunk_directory(feature_set_id: str, chunk_rows: int) -> str:
    return f"{feature_set_id[:24]}-rows-{chunk_rows}"


def _require_text(value: str, field: str) -> None:
    if not value:
        raise ValueError(f"{field} must be non-empty")


def _require_sha256(value: str, field: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} must be a lower-case SHA-256")


def _remove_empty_manifest_parent(path: Path) -> None:
    with suppress(OSError):
        if path.parent.exists() and not any(path.parent.iterdir()):
            path.parent.rmdir()


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_json(value: object) -> str:
    return sha256(
        json.dumps(to_json_value(value), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"R2 feature manifest contains duplicate field: {key}")
        result[key] = value
    return result


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise TypeError("expected an object with string keys")
    return cast(dict[str, object], value)


def _sequence(value: object) -> list[object]:
    if not isinstance(value, list):
        raise TypeError("expected an array")
    return cast(list[object], value)


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise TypeError(f"{field} must be non-empty text")
    return value


def _bool(value: object) -> bool:
    if not isinstance(value, bool):
        raise TypeError("expected a boolean")
    return value


def _hex(value: object, length: int, field: str) -> str:
    text = _text(value, field)
    if len(text) != length or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{field} must be lower-case hexadecimal")
    return text


def _nonnegative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TypeError(f"{field} must be a non-negative integer")
    return value


def _positive_int(value: object, field: str) -> int:
    result = _nonnegative_int(value, field)
    if result == 0:
        raise ValueError(f"{field} must be positive")
    return result


def _datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(_text(value, "timestamp").replace("Z", "+00:00"))
    require_utc(parsed, "R2 feature manifest timestamp")
    return parsed.astimezone(UTC)
