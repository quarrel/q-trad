"""Versioned local Parquet research data with deterministic manifests."""

import asyncio
import hashlib
import io
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Literal

import polars as pl
from pydantic import BaseModel, ConfigDict, Field

from qtrad.domain.events import JsonValue
from qtrad.domain.identifiers import InstrumentId, ProviderListingId
from qtrad.domain.market_data import (
    BarProvenance,
    DataQuality,
    MarketBar,
    PriceBasis,
)
from qtrad.ports.clock import Clock
from qtrad.ports.storage import ResearchManifest

_MAX_MANIFEST_BYTES = 4 * 1024 * 1024


class _ManifestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    manifest_id: str
    manifest_sha256: str | None = None
    created_at: datetime
    schema_version: Literal[1, 2]
    universe_name: str | None = None
    row_count: int = Field(ge=0)
    minimum_event_time: datetime | None
    maximum_event_time: datetime | None
    content_sha256: str
    configuration_hash: str
    files: list[str]
    file_sha256: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, JsonValue]


class ParquetResearchStore:
    def __init__(self, root: Path, clock: Clock) -> None:
        self._root = root
        self._clock = clock

    async def write_bars(
        self,
        bars: Sequence[MarketBar],
        *,
        universe_name: str,
        configuration_hash: str,
        metadata: Mapping[str, JsonValue],
    ) -> ResearchManifest:
        return await asyncio.to_thread(
            self._write_bars_sync,
            bars,
            universe_name,
            configuration_hash,
            metadata,
        )

    def _write_bars_sync(
        self,
        bars: Sequence[MarketBar],
        universe_name: str,
        configuration_hash: str,
        metadata: Mapping[str, JsonValue],
    ) -> ResearchManifest:
        ordered = sorted(
            bars,
            key=lambda bar: (
                str(bar.instrument_id),
                bar.interval_start,
                bar.basis.value,
                bar.revision,
            ),
        )
        groups: dict[tuple[str, str], list[MarketBar]] = defaultdict(list)
        for bar in ordered:
            groups[(str(bar.instrument_id), bar.interval_start.date().isoformat())].append(bar)

        serialised = [_bar_row(bar) for bar in ordered]
        content_hash = _sha256_json(serialised)
        files: list[str] = []
        file_sha256: dict[str, str] = {}

        for group in groups.values():
            partition_id = _sha256_json([_bar_row(bar) for bar in group])[:24]
            relative = Path(_partition_path(group[0], partition_id))
            destination = self._root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            encoded = _parquet_bytes(group)
            if destination.exists():
                _verify_partition(destination, group)
            else:
                try:
                    with destination.open("xb") as output:
                        output.write(encoded)
                except FileExistsError:
                    _verify_partition(destination, group)
            files.append(relative.as_posix())
            file_sha256[relative.as_posix()] = hashlib.sha256(destination.read_bytes()).hexdigest()

        created_at = self._clock.now()
        identity = _manifest_identity_payload(
            created_at=created_at,
            universe_name=universe_name,
            row_count=len(ordered),
            minimum_event_time=min((bar.interval_start for bar in ordered), default=None),
            maximum_event_time=max((bar.interval_end for bar in ordered), default=None),
            content_sha256=content_hash,
            configuration_hash=configuration_hash,
            files=tuple(sorted(files)),
            file_sha256=file_sha256,
            metadata=metadata,
        )
        manifest_sha256 = _sha256_json(identity)
        manifest = ResearchManifest(
            manifest_id=manifest_sha256[:24],
            manifest_sha256=manifest_sha256,
            created_at=created_at,
            schema_version=2,
            universe_name=universe_name,
            row_count=len(ordered),
            minimum_event_time=min((bar.interval_start for bar in ordered), default=None),
            maximum_event_time=max((bar.interval_end for bar in ordered), default=None),
            content_sha256=content_hash,
            configuration_hash=configuration_hash,
            files=tuple(sorted(files)),
            file_sha256=file_sha256,
            metadata=dict(metadata),
        )
        manifest_path = self._root / "manifests" / f"{manifest.manifest_id}.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        encoded_manifest = json.dumps(_manifest_row(manifest), indent=2, sort_keys=True) + "\n"
        if len(encoded_manifest.encode("utf-8")) > _MAX_MANIFEST_BYTES:
            raise ValueError("research manifest exceeds the 4 MiB limit")
        try:
            with manifest_path.open("x", encoding="utf-8") as output:
                output.write(encoded_manifest)
        except FileExistsError as error:
            existing = self._read_manifest_sync(manifest.manifest_id)
            if _manifest_identity_payload_from_manifest(existing) != identity:
                raise RuntimeError(
                    "existing research manifest conflicts with its identity"
                ) from error
            self._read_bars_sync(existing.manifest_id)
            return existing
        return manifest

    async def read_bars(self, manifest_id: str) -> Sequence[MarketBar]:
        return await asyncio.to_thread(self._read_bars_sync, manifest_id)

    async def read_manifest(self, manifest_id: str) -> ResearchManifest:
        return await asyncio.to_thread(self._read_manifest_sync, manifest_id)

    def _read_manifest_sync(self, manifest_id: str) -> ResearchManifest:
        _require_manifest_id(manifest_id)
        manifest_path = self._root / "manifests" / f"{manifest_id}.json"
        encoded = manifest_path.read_bytes()
        if len(encoded) > _MAX_MANIFEST_BYTES:
            raise ValueError("research manifest exceeds the 4 MiB limit")
        model = _ManifestModel.model_validate_json(encoded)
        manifest = ResearchManifest(
            manifest_id=model.manifest_id,
            manifest_sha256=model.manifest_sha256,
            created_at=model.created_at,
            schema_version=model.schema_version,
            universe_name=model.universe_name,
            row_count=model.row_count,
            minimum_event_time=model.minimum_event_time,
            maximum_event_time=model.maximum_event_time,
            content_sha256=model.content_sha256,
            configuration_hash=model.configuration_hash,
            files=tuple(model.files),
            file_sha256=model.file_sha256,
            metadata=model.metadata,
        )
        if manifest.manifest_id != manifest_id:
            raise ValueError("research manifest filename does not match its identity")
        for relative in manifest.files:
            path = _safe_manifest_file(relative)
            expected_root = "bars" if manifest.schema_version == 1 else "bars-v2"
            if path.parts[0] != expected_root:
                raise ValueError("research file path does not match its manifest schema")
        if manifest.schema_version == 2:
            calculated = _sha256_json(_manifest_identity_payload_from_manifest(manifest))
            if calculated != manifest.manifest_sha256:
                raise ValueError("research manifest hash does not match its canonical content")
        return manifest

    def _read_bars_sync(self, manifest_id: str) -> tuple[MarketBar, ...]:
        manifest = self._read_manifest_sync(manifest_id)
        bars: list[MarketBar] = []
        for relative in manifest.files:
            path = self._root / PurePosixPath(relative)
            if manifest.schema_version == 2:
                observed_hash = hashlib.sha256(path.read_bytes()).hexdigest()
                if observed_hash != manifest.file_sha256[relative]:
                    raise ValueError(f"research file hash mismatch: {relative}")
            rows = pl.read_parquet(path).to_dicts()
            file_bars = tuple(_bar_from_row(row) for row in rows)
            partition_root = "bars" if manifest.schema_version == 1 else "bars-v2"
            partition_id = (
                manifest.content_sha256[:24]
                if manifest.schema_version == 1
                else _sha256_json([_bar_row(bar) for bar in file_bars])[:24]
            )
            if any(
                _partition_path(bar, partition_id, root=partition_root) != relative
                for bar in file_bars
            ):
                raise ValueError(f"research file contains bars for another partition: {relative}")
            bars.extend(file_bars)
        ordered = tuple(
            sorted(
                bars,
                key=lambda bar: (
                    str(bar.instrument_id),
                    bar.interval_start,
                    bar.basis.value,
                    bar.revision,
                ),
            )
        )
        serialised = [_bar_row(bar) for bar in ordered]
        if _sha256_json(serialised) != manifest.content_sha256:
            raise ValueError("research content hash does not match its bars")
        if len(ordered) != manifest.row_count:
            raise ValueError("research manifest row count does not match its bars")
        minimum = min((bar.interval_start for bar in ordered), default=None)
        maximum = max((bar.interval_end for bar in ordered), default=None)
        if minimum != manifest.minimum_event_time or maximum != manifest.maximum_event_time:
            raise ValueError("research manifest time bounds do not match its bars")
        return ordered


def _parquet_bytes(bars: Sequence[MarketBar]) -> bytes:
    buffer = io.BytesIO()
    pl.DataFrame([_bar_row(bar) for bar in bars]).write_parquet(buffer)
    return buffer.getvalue()


def _verify_partition(path: Path, expected: Sequence[MarketBar]) -> None:
    rows = pl.read_parquet(path).to_dicts()
    observed = tuple(_bar_from_row(row) for row in rows)
    if [_bar_row(bar) for bar in observed] != [_bar_row(bar) for bar in expected]:
        raise RuntimeError(f"existing research partition conflicts with its content: {path}")


def _partition_path(bar: MarketBar, partition_id: str, *, root: str = "bars-v2") -> str:
    safe_instrument = str(bar.instrument_id).replace(":", "__")
    return (
        Path(root)
        / f"instrument={safe_instrument}"
        / f"date={bar.interval_start.date().isoformat()}"
        / f"part-{partition_id}.parquet"
    ).as_posix()


def _manifest_identity_payload(
    *,
    created_at: datetime,
    universe_name: str,
    row_count: int,
    minimum_event_time: datetime | None,
    maximum_event_time: datetime | None,
    content_sha256: str,
    configuration_hash: str,
    files: tuple[str, ...],
    file_sha256: Mapping[str, str],
    metadata: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    return {
        "schema_version": 2,
        "created_at": _time_text(created_at),
        "universe_name": universe_name,
        "row_count": row_count,
        "minimum_event_time": _time_text(minimum_event_time),
        "maximum_event_time": _time_text(maximum_event_time),
        "content_sha256": content_sha256,
        "configuration_hash": configuration_hash,
        "files": list(files),
        "file_sha256": dict(sorted(file_sha256.items())),
        "metadata": dict(metadata),
    }


def _manifest_identity_payload_from_manifest(
    manifest: ResearchManifest,
) -> dict[str, JsonValue]:
    if manifest.universe_name is None:
        raise ValueError("legacy research manifest has no version-two identity")
    return _manifest_identity_payload(
        created_at=manifest.created_at,
        universe_name=manifest.universe_name,
        row_count=manifest.row_count,
        minimum_event_time=manifest.minimum_event_time,
        maximum_event_time=manifest.maximum_event_time,
        content_sha256=manifest.content_sha256,
        configuration_hash=manifest.configuration_hash,
        files=manifest.files,
        file_sha256=manifest.file_sha256,
        metadata=manifest.metadata,
    )


def _sha256_json(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _time_text(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value is not None else None


def _require_manifest_id(value: str) -> None:
    if len(value) != 24 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError("research manifest ID must be 24 lower-case hexadecimal characters")


def _safe_manifest_file(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or path.parts[0] not in {"bars", "bars-v2"}
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.suffix != ".parquet"
    ):
        raise ValueError(f"unsafe research file path: {value}")
    return path


def _bar_row(bar: MarketBar) -> dict[str, object]:
    return {
        "instrument_id": str(bar.instrument_id),
        "basis": bar.basis.value,
        "interval_start": bar.interval_start.isoformat(),
        "interval_end": bar.interval_end.isoformat(),
        "open": str(bar.open),
        "high": str(bar.high),
        "low": str(bar.low),
        "close": str(bar.close),
        "sample_count": bar.sample_count,
        "revision": bar.revision,
        "provenance": bar.provenance.value,
        "quality": bar.quality.value,
        "provider": bar.source_listing_id.provider,
        "environment": bar.source_listing_id.environment,
        "external_id": bar.source_listing_id.external_id,
    }


def _bar_from_row(row: Mapping[str, object]) -> MarketBar:
    return MarketBar(
        instrument_id=InstrumentId(str(row["instrument_id"])),
        basis=PriceBasis(str(row["basis"])),
        interval_start=datetime.fromisoformat(str(row["interval_start"])).astimezone(UTC),
        interval_end=datetime.fromisoformat(str(row["interval_end"])).astimezone(UTC),
        open=Decimal(str(row["open"])),
        high=Decimal(str(row["high"])),
        low=Decimal(str(row["low"])),
        close=Decimal(str(row["close"])),
        sample_count=int(str(row["sample_count"])),
        revision=int(str(row["revision"])),
        provenance=BarProvenance(str(row["provenance"])),
        quality=DataQuality(str(row["quality"])),
        source_listing_id=ProviderListingId(
            provider=str(row["provider"]),
            environment=str(row["environment"]),
            external_id=str(row["external_id"]),
        ),
    )


def _manifest_row(manifest: ResearchManifest) -> dict[str, object]:
    return {
        "manifest_id": manifest.manifest_id,
        "manifest_sha256": manifest.manifest_sha256,
        "created_at": manifest.created_at.isoformat(),
        "schema_version": manifest.schema_version,
        "universe_name": manifest.universe_name,
        "row_count": manifest.row_count,
        "minimum_event_time": (
            manifest.minimum_event_time.isoformat() if manifest.minimum_event_time else None
        ),
        "maximum_event_time": (
            manifest.maximum_event_time.isoformat() if manifest.maximum_event_time else None
        ),
        "content_sha256": manifest.content_sha256,
        "configuration_hash": manifest.configuration_hash,
        "files": manifest.files,
        "file_sha256": dict(sorted(manifest.file_sha256.items())),
        "metadata": manifest.metadata,
    }
