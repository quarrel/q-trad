"""Versioned local Parquet research data with deterministic manifests."""

import asyncio
import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import polars as pl

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


class ParquetResearchStore:
    def __init__(self, root: Path, clock: Clock) -> None:
        self._root = root
        self._clock = clock

    async def write_bars(
        self,
        bars: Sequence[MarketBar],
        *,
        configuration_hash: str,
        metadata: Mapping[str, JsonValue],
    ) -> ResearchManifest:
        return await asyncio.to_thread(
            self._write_bars_sync, bars, configuration_hash, metadata
        )

    def _write_bars_sync(
        self,
        bars: Sequence[MarketBar],
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
            groups[(str(bar.instrument_id), bar.interval_start.date().isoformat())].append(
                bar
            )

        serialised = [_bar_row(bar) for bar in ordered]
        semantic_bytes = json.dumps(
            serialised, sort_keys=True, separators=(",", ":")
        ).encode()
        content_hash = hashlib.sha256(semantic_bytes).hexdigest()
        manifest_id = content_hash[:24]
        files: list[str] = []

        for (instrument_id, date), group in groups.items():
            safe_instrument = instrument_id.replace(":", "__")
            relative = (
                Path("bars")
                / f"instrument={safe_instrument}"
                / f"date={date}"
                / f"part-{manifest_id}.parquet"
            )
            destination = self._root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            pl.DataFrame([_bar_row(bar) for bar in group]).write_parquet(destination)
            files.append(relative.as_posix())

        created_at = self._clock.now()
        manifest = ResearchManifest(
            manifest_id=manifest_id,
            created_at=created_at,
            schema_version=1,
            row_count=len(ordered),
            minimum_event_time=min(
                (bar.interval_start for bar in ordered), default=None
            ),
            maximum_event_time=max((bar.interval_end for bar in ordered), default=None),
            content_sha256=content_hash,
            configuration_hash=configuration_hash,
            files=tuple(sorted(files)),
            metadata=dict(metadata),
        )
        manifest_path = self._root / "manifests" / f"{manifest_id}.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(_manifest_row(manifest), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return manifest

    async def read_bars(self, manifest_id: str) -> Sequence[MarketBar]:
        return await asyncio.to_thread(self._read_bars_sync, manifest_id)

    def _read_bars_sync(self, manifest_id: str) -> tuple[MarketBar, ...]:
        manifest_path = self._root / "manifests" / f"{manifest_id}.json"
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        bars: list[MarketBar] = []
        for relative in payload["files"]:
            rows = pl.read_parquet(self._root / relative).to_dicts()
            bars.extend(_bar_from_row(row) for row in rows)
        return tuple(
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
        "created_at": manifest.created_at.isoformat(),
        "schema_version": manifest.schema_version,
        "row_count": manifest.row_count,
        "minimum_event_time": (
            manifest.minimum_event_time.isoformat()
            if manifest.minimum_event_time
            else None
        ),
        "maximum_event_time": (
            manifest.maximum_event_time.isoformat()
            if manifest.maximum_event_time
            else None
        ),
        "content_sha256": manifest.content_sha256,
        "configuration_hash": manifest.configuration_hash,
        "files": manifest.files,
        "metadata": manifest.metadata,
    }
