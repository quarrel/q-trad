"""Small, provider-independent contracts for causal research inputs."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from math import ceil
from typing import ClassVar
from uuid import UUID

from qtrad.domain.events import EventEnvelope, JsonValue, to_json_value
from qtrad.domain.identifiers import InstrumentId, ProviderListingId
from qtrad.domain.market_data import BarProvenance, DataQuality, MarketBar, PriceBasis
from qtrad.domain.time import require_utc

OBSERVATION_DATASET_CONTRACT = "qtrad-research-observations-v1"
OBSERVATION_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class ProjectedBar:
    """A market-bar projection row with its canonical event position."""

    bar: MarketBar
    global_position: int

    def __post_init__(self) -> None:
        if self.global_position <= 0:
            raise ValueError("projected bar global position must be positive")


@dataclass(frozen=True, slots=True)
class ObservationCandidate:
    """One projection row and the canonical event matched by global position."""

    projection: ProjectedBar
    event: EventEnvelope | None


@dataclass(frozen=True, slots=True)
class ObservationRow:
    """One immutable native bar revision with canonical availability lineage."""

    event_id: UUID
    stream_id: str
    stream_version: int
    event_type: str
    event_time: datetime
    received_at: datetime
    persisted_at: datetime
    global_position: int
    instrument_id: str
    basis: PriceBasis
    interval_start: datetime
    interval_end: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    sample_count: int
    revision: int
    provenance: BarProvenance
    quality: DataQuality
    source_provider: str
    source_environment: str
    source_external_id: str

    CONTRACT: ClassVar[str] = OBSERVATION_DATASET_CONTRACT
    SCHEMA_VERSION: ClassVar[int] = OBSERVATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        require_utc(self.event_time, "observation event_time")
        require_utc(self.received_at, "observation received_at")
        require_utc(self.persisted_at, "observation persisted_at")
        require_utc(self.interval_start, "observation interval_start")
        require_utc(self.interval_end, "observation interval_end")
        if self.global_position <= 0:
            raise ValueError("observation global position must be positive")
        if self.stream_version <= 0 or self.revision <= 0 or self.sample_count <= 0:
            raise ValueError("observation lineage and bar counts must be positive")
        if not self.instrument_id or not self.stream_id:
            raise ValueError("observation identity must be non-empty")
        if self.interval_end <= self.interval_start:
            raise ValueError("observation interval must be positive")

    @property
    def availability_delay(self) -> timedelta:
        return self.persisted_at - self.interval_end

    def semantic_key(self) -> tuple[object, ...]:
        return (
            self.instrument_id,
            self.basis.value,
            self.interval_start,
            self.source_provider,
            self.source_environment,
            self.source_external_id,
            self.stream_version,
            self.global_position,
        )

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "event_id": str(self.event_id),
            "stream_id": self.stream_id,
            "stream_version": self.stream_version,
            "event_type": self.event_type,
            "event_time": self.event_time.isoformat(),
            "received_at": self.received_at.isoformat(),
            "persisted_at": self.persisted_at.isoformat(),
            "global_position": self.global_position,
            "instrument_id": self.instrument_id,
            "basis": self.basis.value,
            "interval_start": self.interval_start.isoformat(),
            "interval_end": self.interval_end.isoformat(),
            "open": str(self.open),
            "high": str(self.high),
            "low": str(self.low),
            "close": str(self.close),
            "sample_count": self.sample_count,
            "revision": self.revision,
            "provenance": self.provenance.value,
            "quality": self.quality.value,
            "source_provider": self.source_provider,
            "source_environment": self.source_environment,
            "source_external_id": self.source_external_id,
        }


@dataclass(frozen=True, slots=True)
class ObservationDataset:
    """Rows plus the semantic inputs needed to reproduce their dataset ID."""

    rows: tuple[ObservationRow, ...]
    configuration: Mapping[str, JsonValue]
    source_dataset_ids: tuple[str, ...]
    selection_policies: Mapping[str, JsonValue]
    dataset_id: str

    def __post_init__(self) -> None:
        ordered = tuple(sorted(self.rows, key=ObservationRow.semantic_key))
        if ordered != self.rows:
            raise ValueError("observation rows must use canonical semantic ordering")
        if len({row.semantic_key() for row in self.rows}) != len(self.rows):
            raise ValueError("observation rows must have unique semantic keys")
        expected = observation_dataset_id(
            self.rows,
            configuration=self.configuration,
            source_dataset_ids=self.source_dataset_ids,
            selection_policies=self.selection_policies,
        )
        if self.dataset_id != expected:
            raise ValueError("observation dataset ID does not match its semantic content")

    @classmethod
    def create(
        cls,
        rows: Sequence[ObservationRow],
        *,
        configuration: Mapping[str, JsonValue],
        source_dataset_ids: Sequence[str] = (),
        selection_policies: Mapping[str, JsonValue] | None = None,
    ) -> "ObservationDataset":
        ordered = tuple(sorted(rows, key=ObservationRow.semantic_key))
        policies = selection_policies or {}
        return cls(
            rows=ordered,
            configuration=dict(configuration),
            source_dataset_ids=tuple(source_dataset_ids),
            selection_policies=dict(policies),
            dataset_id=observation_dataset_id(
                ordered,
                configuration=configuration,
                source_dataset_ids=source_dataset_ids,
                selection_policies=policies,
            ),
        )


@dataclass(frozen=True, slots=True)
class AvailabilityDelayReport:
    """Evidence for selecting a feature lag from persisted native observations."""

    calibration_start: datetime
    calibration_end: datetime
    eligible_row_count: int
    excluded_row_count: int
    delay_percentiles: Mapping[str, float]
    maximum_delay: timedelta | None
    configured_percentile: float
    safety_margin: timedelta
    selected_lag: timedelta

    def __post_init__(self) -> None:
        require_utc(self.calibration_start, "availability calibration_start")
        require_utc(self.calibration_end, "availability calibration_end")
        if self.calibration_end <= self.calibration_start:
            raise ValueError("availability calibration interval must be positive")
        if self.eligible_row_count < 0 or self.excluded_row_count < 0:
            raise ValueError("availability row counts must not be negative")
        if not 0 <= self.configured_percentile <= 1:
            raise ValueError("availability percentile must be between zero and one")
        if self.safety_margin < timedelta(0) or self.selected_lag < timedelta(0):
            raise ValueError("availability durations must not be negative")


def observation_dataset_id(
    rows: Sequence[ObservationRow],
    *,
    configuration: Mapping[str, JsonValue],
    source_dataset_ids: Sequence[str],
    selection_policies: Mapping[str, JsonValue],
) -> str:
    """Return a stable semantic identity independent of physical representation."""

    import hashlib
    import json

    payload = {
        "contract": OBSERVATION_DATASET_CONTRACT,
        "schema_version": OBSERVATION_SCHEMA_VERSION,
        "configuration": configuration,
        "source_dataset_ids": list(source_dataset_ids),
        "selection_policies": selection_policies,
        "rows": [row.as_json() for row in sorted(rows, key=ObservationRow.semantic_key)],
    }
    canonical = to_json_value(payload)
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def build_observation_rows(
    candidates: Sequence[ObservationCandidate],
) -> tuple[ObservationRow, ...]:
    """Join projections to canonical events and retain every valid native revision."""

    seen_positions: set[int] = set()
    rows: list[ObservationRow] = []
    for candidate in candidates:
        projection = candidate.projection
        if projection.global_position in seen_positions:
            raise ValueError(
                "multiple projection rows use canonical global position "
                f"{projection.global_position}"
            )
        seen_positions.add(projection.global_position)
        if projection.bar.provenance is not BarProvenance.QUOTE_DERIVED:
            raise ValueError("research observations accept only QUOTE_DERIVED bars")
        event = candidate.event
        if event is None:
            raise ValueError(
                f"projection has no canonical event at global position {projection.global_position}"
            )
        if event.global_position != projection.global_position:
            raise ValueError("projection and canonical event global positions do not match")
        if event.event_type not in {"MarketBarClosed", "MarketBarCorrected"}:
            raise ValueError("projection is linked to a non-bar canonical event")
        if event.persisted_time is None:
            raise ValueError("canonical bar event is not persisted")
        if event.event_time != projection.bar.interval_end:
            raise ValueError("canonical bar event time does not match the bar interval end")
        payload_bar = _market_bar_from_payload(event.payload)
        if _bar_identity(payload_bar) != _bar_identity(projection.bar):
            raise ValueError("projection bar does not match its canonical event payload")
        source = projection.bar.source_listing_id
        rows.append(
            ObservationRow(
                event_id=event.event_id,
                stream_id=event.stream_id,
                stream_version=event.stream_version,
                event_type=event.event_type,
                event_time=event.event_time,
                received_at=event.received_time,
                persisted_at=event.persisted_time,
                global_position=projection.global_position,
                instrument_id=str(projection.bar.instrument_id),
                basis=projection.bar.basis,
                interval_start=projection.bar.interval_start,
                interval_end=projection.bar.interval_end,
                open=projection.bar.open,
                high=projection.bar.high,
                low=projection.bar.low,
                close=projection.bar.close,
                sample_count=projection.bar.sample_count,
                revision=projection.bar.revision,
                provenance=projection.bar.provenance,
                quality=projection.bar.quality,
                source_provider=source.provider,
                source_environment=source.environment,
                source_external_id=source.external_id,
            )
        )
    return tuple(sorted(rows, key=ObservationRow.semantic_key))


def build_availability_delay_report(
    rows: Sequence[ObservationRow],
    *,
    calibration_start: datetime,
    calibration_end: datetime,
    configured_percentile: float,
    safety_margin: timedelta,
    grid_resolution: timedelta,
) -> AvailabilityDelayReport:
    """Summarise persisted availability delays using explicit policy inputs."""

    require_utc(calibration_start, "availability calibration_start")
    require_utc(calibration_end, "availability calibration_end")
    if calibration_end <= calibration_start:
        raise ValueError("availability calibration interval must be positive")
    if grid_resolution <= timedelta(0):
        raise ValueError("availability grid resolution must be positive")
    if not 0 <= configured_percentile <= 1:
        raise ValueError("availability percentile must be between zero and one")
    if safety_margin < timedelta(0):
        raise ValueError("availability safety margin must not be negative")

    in_calibration = [
        row
        for row in rows
        if calibration_start <= row.interval_end < calibration_end
    ]
    eligible = [
        row.availability_delay
        for row in in_calibration
        if row.provenance is BarProvenance.QUOTE_DERIVED
        and row.persisted_at >= row.interval_end
    ]
    excluded = len(in_calibration) - len(eligible)
    ordered = sorted(eligible)
    percentiles = {
        str(percentile): _percentile_seconds(ordered, percentile)
        for percentile in (0.5, 0.9, 0.95, configured_percentile, 1.0)
    }
    selected_base = timedelta(
        seconds=percentiles[str(configured_percentile)] + safety_margin.total_seconds()
    )
    selected_lag = _ceil_duration(selected_base, grid_resolution)
    return AvailabilityDelayReport(
        calibration_start=calibration_start,
        calibration_end=calibration_end,
        eligible_row_count=len(eligible),
        excluded_row_count=excluded,
        delay_percentiles=percentiles,
        maximum_delay=max(eligible, default=None),
        configured_percentile=configured_percentile,
        safety_margin=safety_margin,
        selected_lag=selected_lag,
    )


def _percentile_seconds(values: Sequence[timedelta], percentile: float) -> float:
    if not values:
        raise ValueError("cannot calculate availability percentiles without eligible rows")
    if not 0 <= percentile <= 1:
        raise ValueError("availability percentile must be between zero and one")
    position = (len(values) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    fraction = position - lower
    return values[lower].total_seconds() + (
        values[upper].total_seconds() - values[lower].total_seconds()
    ) * fraction


def _ceil_duration(value: timedelta, unit: timedelta) -> timedelta:
    units = ceil(value.total_seconds() / unit.total_seconds())
    return unit * units


def _bar_identity(bar: MarketBar) -> tuple[object, ...]:
    return (
        str(bar.instrument_id),
        bar.basis,
        bar.interval_start,
        bar.interval_end,
        bar.open,
        bar.high,
        bar.low,
        bar.close,
        bar.sample_count,
        bar.revision,
        bar.provenance,
        bar.quality,
        bar.source_listing_id,
    )


def _market_bar_from_payload(payload: Mapping[str, JsonValue]) -> MarketBar:
    source = payload["source_listing_id"]
    if not isinstance(source, dict):
        raise ValueError("canonical bar payload source listing is malformed")
    return MarketBar(
        instrument_id=InstrumentId(str(payload["instrument_id"])),
        basis=PriceBasis(str(payload["basis"])),
        interval_start=_datetime(str(payload["interval_start"])),
        interval_end=_datetime(str(payload["interval_end"])),
        open=Decimal(str(payload["open"])),
        high=Decimal(str(payload["high"])),
        low=Decimal(str(payload["low"])),
        close=Decimal(str(payload["close"])),
        sample_count=int(str(payload["sample_count"])),
        revision=int(str(payload["revision"])),
        provenance=BarProvenance(str(payload["provenance"])),
        quality=DataQuality(str(payload["quality"])),
        source_listing_id=ProviderListingId(
            provider=str(source["provider"]),
            environment=str(source["environment"]),
            external_id=str(source["external_id"]),
        ),
    )


def _datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require_utc(parsed, "canonical bar payload timestamp")
    return parsed
