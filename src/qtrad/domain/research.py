"""Small, provider-independent contracts for causal research inputs."""

import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import InitVar, dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from math import ceil
from typing import ClassVar, cast
from uuid import UUID

from qtrad.domain.events import EventEnvelope, JsonValue, to_json_value
from qtrad.domain.identifiers import InstrumentId, ProviderListingId
from qtrad.domain.market_data import BarProvenance, DataQuality, MarketBar, PriceBasis
from qtrad.domain.time import require_utc

OBSERVATION_DATASET_CONTRACT = "qtrad-research-observations-v1"
OBSERVATION_SCHEMA_VERSION = 1
_OBSERVATION_DATASET_VERIFIED = object()


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
        expected_event_type = "MarketBarClosed" if self.revision == 1 else "MarketBarCorrected"
        if self.event_type != expected_event_type:
            raise ValueError("observation event type does not match its bar revision")
        if self.stream_id != f"market-bar:{self.instrument_id}:{self.basis}":
            raise ValueError("observation stream identity does not match its bar")

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

    def revision_key(self) -> tuple[object, ...]:
        return (
            self.instrument_id,
            self.basis,
            self.interval_start,
            self.source_provider,
            self.source_environment,
            self.source_external_id,
            self.revision,
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
    _verified: InitVar[object | None] = None

    def __post_init__(self, _verified: object | None) -> None:
        if _verified is _OBSERVATION_DATASET_VERIFIED:
            return
        _validate_observation_rows(self.rows)
        expected = _observation_dataset_identity(
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
        policies = {} if selection_policies is None else dict(selection_policies)
        _validate_observation_rows(ordered)
        dataset_id = _observation_dataset_identity(
            ordered,
            configuration=configuration,
            source_dataset_ids=source_dataset_ids,
            selection_policies=policies,
        )
        return cls(
            rows=ordered,
            configuration=dict(configuration),
            source_dataset_ids=tuple(source_dataset_ids),
            selection_policies=policies,
            dataset_id=dataset_id,
            _verified=_OBSERVATION_DATASET_VERIFIED,
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

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "calibration_start": self.calibration_start.isoformat(),
            "calibration_end": self.calibration_end.isoformat(),
            "eligible_row_count": self.eligible_row_count,
            "excluded_row_count": self.excluded_row_count,
            "delay_percentiles_seconds": dict(self.delay_percentiles),
            "maximum_delay_seconds": (
                self.maximum_delay.total_seconds() if self.maximum_delay is not None else None
            ),
            "configured_percentile": self.configured_percentile,
            "safety_margin_seconds": self.safety_margin.total_seconds(),
            "selected_lag_seconds": self.selected_lag.total_seconds(),
        }


@dataclass(frozen=True, slots=True)
class RevisionDelayReport:
    """Separate maturity evidence for corrections after the first usable revision."""

    calibration_start: datetime
    calibration_end: datetime
    eligible_correction_count: int
    excluded_correction_count: int
    delay_percentiles: Mapping[str, float]
    maximum_delay: timedelta | None

    def __post_init__(self) -> None:
        require_utc(self.calibration_start, "revision calibration_start")
        require_utc(self.calibration_end, "revision calibration_end")
        if self.calibration_end <= self.calibration_start:
            raise ValueError("revision calibration interval must be positive")
        if self.eligible_correction_count < 0 or self.excluded_correction_count < 0:
            raise ValueError("revision correction counts must not be negative")

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "calibration_start": self.calibration_start.isoformat(),
            "calibration_end": self.calibration_end.isoformat(),
            "eligible_correction_count": self.eligible_correction_count,
            "excluded_correction_count": self.excluded_correction_count,
            "delay_percentiles_seconds": dict(self.delay_percentiles),
            "maximum_delay_seconds": (
                self.maximum_delay.total_seconds() if self.maximum_delay is not None else None
            ),
        }


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(to_json_value(value), sort_keys=True, separators=(",", ":")).encode("utf-8")


def _observation_dataset_chunks(
    rows: Sequence[ObservationRow],
    *,
    configuration: Mapping[str, JsonValue],
    source_dataset_ids: Sequence[str],
    selection_policies: Mapping[str, JsonValue],
) -> Iterator[bytes]:
    """Yield the legacy canonical payload without materialising its row array."""
    yield b'{"configuration":'
    yield _canonical_json_bytes(configuration)
    yield b',"contract":'
    yield _canonical_json_bytes(OBSERVATION_DATASET_CONTRACT)
    yield b',"rows":['
    for index, row in enumerate(rows):
        if index:
            yield b","
        yield _canonical_json_bytes(row.as_json())
    yield b'],"schema_version":'
    yield _canonical_json_bytes(OBSERVATION_SCHEMA_VERSION)
    yield b',"selection_policies":'
    yield _canonical_json_bytes(selection_policies)
    yield b',"source_dataset_ids":'
    yield _canonical_json_bytes(list(source_dataset_ids))
    yield b"}"


def _observation_dataset_identity(
    rows: Sequence[ObservationRow],
    *,
    configuration: Mapping[str, JsonValue],
    source_dataset_ids: Sequence[str],
    selection_policies: Mapping[str, JsonValue],
) -> str:
    digest = sha256()
    for chunk in _observation_dataset_chunks(
        rows,
        configuration=configuration,
        source_dataset_ids=source_dataset_ids,
        selection_policies=selection_policies,
    ):
        digest.update(chunk)
    return digest.hexdigest()


def observation_dataset_id(
    rows: Sequence[ObservationRow],
    *,
    configuration: Mapping[str, JsonValue],
    source_dataset_ids: Sequence[str],
    selection_policies: Mapping[str, JsonValue],
) -> str:
    """Return a stable semantic identity independent of physical representation."""
    ordered = tuple(sorted(rows, key=ObservationRow.semantic_key))
    return _observation_dataset_identity(
        ordered,
        configuration=configuration,
        source_dataset_ids=source_dataset_ids,
        selection_policies=selection_policies,
    )


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
        expected_event_type = (
            "MarketBarClosed" if projection.bar.revision == 1 else "MarketBarCorrected"
        )
        if event.event_type != expected_event_type:
            raise ValueError("canonical event type does not match the bar revision")
        expected_stream_id = f"market-bar:{projection.bar.instrument_id}:{projection.bar.basis}"
        if event.stream_id != expected_stream_id:
            raise ValueError("canonical bar event has an unexpected stream identity")
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
        row for row in rows if calibration_start <= row.interval_end < calibration_end
    ]
    interval_keys = {
        (row.instrument_id, row.basis, row.interval_start, row.interval_end)
        for row in in_calibration
    }
    initial_by_interval: dict[tuple[object, ...], timedelta] = {}
    for row in in_calibration:
        if (
            row.provenance is not BarProvenance.QUOTE_DERIVED
            or row.event_type != "MarketBarClosed"
            or row.revision != 1
            or row.persisted_at < row.interval_end
        ):
            continue
        key = (row.instrument_id, row.basis, row.interval_start, row.interval_end)
        delay = row.availability_delay
        previous = initial_by_interval.get(key)
        if previous is None or delay < previous:
            initial_by_interval[key] = delay
    eligible = list(initial_by_interval.values())
    excluded = len(interval_keys) - len(eligible)
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


def build_revision_delay_report(
    rows: Sequence[ObservationRow],
    *,
    calibration_start: datetime,
    calibration_end: datetime,
) -> RevisionDelayReport:
    """Summarise correction maturity without contaminating initial feature lag."""

    require_utc(calibration_start, "revision calibration_start")
    require_utc(calibration_end, "revision calibration_end")
    if calibration_end <= calibration_start:
        raise ValueError("revision calibration interval must be positive")
    corrections = [
        row
        for row in rows
        if calibration_start <= row.interval_end < calibration_end
        and (row.revision > 1 or row.event_type == "MarketBarCorrected")
    ]
    eligible = sorted(
        row.availability_delay
        for row in corrections
        if row.provenance is BarProvenance.QUOTE_DERIVED
        and row.event_type == "MarketBarCorrected"
        and row.revision > 1
        and row.persisted_at >= row.interval_end
    )
    percentiles = (
        {
            str(percentile): _percentile_seconds(eligible, percentile)
            for percentile in (0.5, 0.9, 0.95, 1.0)
        }
        if eligible
        else {}
    )
    return RevisionDelayReport(
        calibration_start=calibration_start,
        calibration_end=calibration_end,
        eligible_correction_count=len(eligible),
        excluded_correction_count=len(corrections) - len(eligible),
        delay_percentiles=percentiles,
        maximum_delay=max(eligible, default=None),
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
    return (
        values[lower].total_seconds()
        + (values[upper].total_seconds() - values[lower].total_seconds()) * fraction
    )


def _ceil_duration(value: timedelta, unit: timedelta) -> timedelta:
    units = ceil(value.total_seconds() / unit.total_seconds())
    return unit * units


def _validate_observation_rows(rows: Sequence[ObservationRow]) -> None:
    if not isinstance(rows, tuple):
        raise ValueError("observation rows must use canonical semantic ordering")
    previous_key: tuple[str, str, datetime, str, str, str, int, int] | None = None
    semantic_keys: set[tuple[object, ...]] = set()
    revision_keys: set[tuple[object, ...]] = set()
    stream_keys: set[tuple[str, int]] = set()
    for row in rows:
        semantic_key = cast(tuple[str, str, datetime, str, str, str, int, int], row.semantic_key())
        if previous_key is not None and semantic_key < previous_key:
            raise ValueError("observation rows must use canonical semantic ordering")
        if semantic_key in semantic_keys:
            raise ValueError("observation rows must have unique semantic keys")
        revision_key = row.revision_key()
        if revision_key in revision_keys:
            raise ValueError("observation rows must have unique source revision lineage")
        stream_key = (row.stream_id, row.stream_version)
        if stream_key in stream_keys:
            raise ValueError("observation rows must have unique canonical stream versions")
        semantic_keys.add(semantic_key)
        revision_keys.add(revision_key)
        stream_keys.add(stream_key)
        previous_key = semantic_key
    _require_contiguous_revisions(rows)


def _require_contiguous_revisions(rows: Sequence[ObservationRow]) -> None:
    revisions_by_interval: dict[tuple[object, ...], set[int]] = {}
    for row in rows:
        key = (
            row.instrument_id,
            row.basis,
            row.interval_start,
            row.interval_end,
            row.source_provider,
            row.source_environment,
            row.source_external_id,
        )
        revisions_by_interval.setdefault(key, set()).add(row.revision)
    for revisions in revisions_by_interval.values():
        if revisions != set(range(1, max(revisions) + 1)):
            raise ValueError("observation interval has missing intermediate revisions")


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
