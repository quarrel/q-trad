from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from qtrad.adapters.parquet.observations import ParquetObservationStore
from qtrad.application.research_observations import (
    build_observation_dataset,
    measure_availability_delay,
)
from qtrad.domain.events import EventEnvelope
from qtrad.domain.identifiers import InstrumentId, ProviderListingId
from qtrad.domain.market_data import BarProvenance, DataQuality, MarketBar, PriceBasis
from qtrad.domain.research import ObservationCandidate, ProjectedBar


class FixedClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


def _bar(interval_start: datetime, *, close: str = "1.1", revision: int = 1) -> MarketBar:
    return MarketBar(
        instrument_id=InstrumentId("fx:aud-usd"),
        basis=PriceBasis.MID,
        interval_start=interval_start,
        interval_end=interval_start + timedelta(minutes=1),
        open=Decimal("1.0"),
        high=Decimal(close),
        low=Decimal("1.0"),
        close=Decimal(close),
        sample_count=2,
        revision=revision,
        provenance=BarProvenance.QUOTE_DERIVED,
        source_listing_id=ProviderListingId("ig", "demo", "AUDUSD"),
        quality=DataQuality.HEALTHY,
    )


def _candidate(bar: MarketBar, *, position: int, received: datetime, persisted: datetime):
    event = EventEnvelope.create(
        stream_id=f"market-bar:{bar.instrument_id}:{bar.basis}",
        stream_version=bar.revision,
        event_type="MarketBarClosed" if bar.revision == 1 else "MarketBarCorrected",
        event_time=bar.interval_end,
        received_time=received,
        producer="fixture",
        producer_version="1",
        payload=bar,
    )
    return ObservationCandidate(
        projection=ProjectedBar(bar=bar, global_position=position),
        event=replace(event, persisted_time=persisted, global_position=position),
    )


def _dataset(candidates: tuple[ObservationCandidate, ...]):
    return build_observation_dataset(
        candidates,
        configuration={"name": "fixture"},
        source_dataset_ids=("a" * 64,),
        selection_policies={"provenance": "QUOTE_DERIVED"},
    )


def test_observation_lineage_is_exact_and_keeps_receive_and_persist_times() -> None:
    interval = datetime(2026, 7, 1, 12, tzinfo=UTC)
    received = interval + timedelta(minutes=2)
    persisted = received + timedelta(seconds=3)
    dataset = _dataset(
        (_candidate(_bar(interval), position=7, received=received, persisted=persisted),)
    )

    row = dataset.rows[0]
    assert row.received_at == received
    assert row.persisted_at == persisted
    assert row.global_position == 7
    assert row.availability_delay == timedelta(minutes=1, seconds=3)

    with pytest.raises(ValueError, match="no canonical event"):
        build_observation_dataset(
            (ObservationCandidate(ProjectedBar(_bar(interval), 7), None),),
            configuration={"name": "fixture"},
        )


def test_reversed_inputs_have_the_same_semantic_identity() -> None:
    start = datetime(2026, 7, 1, 12, tzinfo=UTC)
    candidates = (
        _candidate(
            _bar(start),
            position=7,
            received=start + timedelta(minutes=2),
            persisted=start + timedelta(minutes=2, seconds=1),
        ),
        _candidate(
            _bar(start + timedelta(minutes=1), close="1.2"),
            position=8,
            received=start + timedelta(minutes=3),
            persisted=start + timedelta(minutes=3, seconds=1),
        ),
    )
    assert _dataset(candidates).dataset_id == _dataset(tuple(reversed(candidates))).dataset_id


def test_availability_delay_report_is_explicit_and_grid_rounded() -> None:
    start = datetime(2026, 7, 1, 12, tzinfo=UTC)
    dataset = _dataset(
        (
            _candidate(
                _bar(start),
                position=7,
                received=start + timedelta(minutes=2),
                persisted=start + timedelta(minutes=2, seconds=1),
            ),
            _candidate(
                _bar(start + timedelta(minutes=1), close="1.2"),
                position=8,
                received=start + timedelta(minutes=3),
                persisted=start + timedelta(minutes=3, seconds=31),
            ),
        )
    )
    report = measure_availability_delay(
        dataset,
        calibration_start=start,
        calibration_end=start + timedelta(minutes=3),
        configured_percentile=0.5,
        safety_margin=timedelta(seconds=10),
        grid_resolution=timedelta(minutes=1),
    )

    assert report.eligible_row_count == 2
    assert report.excluded_row_count == 0
    assert report.maximum_delay == timedelta(minutes=1, seconds=31)
    assert report.selected_lag == timedelta(minutes=2)


@pytest.mark.asyncio
async def test_observation_manifest_has_separate_semantic_and_physical_identity(
    tmp_path: Path,
) -> None:
    start = datetime(2026, 7, 1, 12, tzinfo=UTC)
    dataset = _dataset(
        (
            _candidate(
                _bar(start),
                position=7,
                received=start + timedelta(minutes=2),
                persisted=start + timedelta(minutes=2, seconds=1),
            ),
        )
    )
    first = await ParquetObservationStore(
        tmp_path, FixedClock(datetime(2026, 7, 2, tzinfo=UTC))
    ).write_observations(dataset, metadata={"rows": 1})
    second = await ParquetObservationStore(
        tmp_path, FixedClock(datetime(2026, 7, 3, tzinfo=UTC))
    ).write_observations(dataset, metadata={"rows": 1})

    assert first.dataset_id == second.dataset_id == dataset.dataset_id
    assert first.manifest_id != second.manifest_id
    restored = await ParquetObservationStore(
        tmp_path, FixedClock(datetime(2026, 7, 4, tzinfo=UTC))
    ).read_observations(first.manifest_id)
    assert restored == dataset

    manifest_path = tmp_path / "manifests" / f"{first.manifest_id}.json"
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8").replace('"rows": 1', '"rows": 2'),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="manifest hash"):
        await ParquetObservationStore(
            tmp_path, FixedClock(datetime(2026, 7, 4, tzinfo=UTC))
        ).read_manifest(first.manifest_id)


def test_historical_bars_are_rejected_from_native_observations() -> None:
    start = datetime(2026, 7, 1, 12, tzinfo=UTC)
    historical = replace(_bar(start), provenance=BarProvenance.IG_HISTORICAL)
    candidate = _candidate(historical, position=7, received=start, persisted=start)
    with pytest.raises(ValueError, match="QUOTE_DERIVED"):
        build_observation_dataset((candidate,), configuration={"name": "fixture"})
