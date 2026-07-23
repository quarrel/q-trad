from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from qtrad.application.foundation import build_frozen_targets, summarise_horizon_coverage
from qtrad.domain.foundation import (
    AvailabilityBasis,
    FoundationConfig,
    InstrumentRole,
    ReturnDisposition,
)
from qtrad.domain.market_data import BarProvenance, DataQuality, PriceBasis
from qtrad.domain.research import ObservationDataset, ObservationRow

START = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
END = START + timedelta(minutes=1)
HORIZONS = tuple(timedelta(minutes=minutes) for minutes in (5, 15, 30, 60))


def _observations() -> ObservationDataset:
    rows = []
    for minute in range(61):
        if minute == 7:
            continue
        interval_end = START + timedelta(minutes=minute)
        available_at = interval_end + timedelta(seconds=1)
        rows.append(
            ObservationRow(
                event_id=uuid4(),
                stream_id="market-bar:fx:aud-usd:MID",
                stream_version=minute + 1,
                event_type="MarketBarClosed",
                event_time=interval_end,
                received_at=available_at,
                persisted_at=available_at,
                global_position=minute + 1,
                instrument_id="fx:aud-usd",
                basis=PriceBasis.MID,
                interval_start=interval_end - timedelta(minutes=1),
                interval_end=interval_end,
                open=Decimal("100"),
                high=Decimal("102"),
                low=Decimal("98"),
                close=Decimal(100 + minute),
                sample_count=1,
                revision=1,
                provenance=BarProvenance.QUOTE_DERIVED,
                quality=DataQuality.HEALTHY,
                source_provider="ig",
                source_environment="demo",
                source_external_id="AUDUSD",
            )
        )
    return ObservationDataset.create(
        tuple(rows),
        configuration={
            "fixture": "r1.d",
            "interval_start": (START - timedelta(minutes=2)).isoformat(),
            "interval_end": (END + timedelta(minutes=61)).isoformat(),
        },
    )


def _config(dataset: ObservationDataset) -> FoundationConfig:
    return FoundationConfig(
        name="r1-d-fixture",
        schema_version=1,
        observation_dataset_id=dataset.dataset_id,
        ordered_instruments=("fx:aud-usd",),
        instrument_roles={"fx:aud-usd": InstrumentRole.TARGET},
        range_start=START,
        range_end=END,
        grid_resolution=timedelta(minutes=1),
        availability_basis=AvailabilityBasis.RECEIVED_AT,
        feature_lag_policy="PROVISIONAL_CONSERVATIVE",
        feature_lag_calibration_range=(START, END),
        feature_lag_percentile=0.95,
        feature_lag_safety_margin=timedelta(minutes=1),
        selected_feature_lag=timedelta(minutes=1),
        target_horizons=HORIZONS,
        primary_vertical_horizon=timedelta(minutes=15),
        target_revision_delay=timedelta(minutes=1),
        target_revision_policy="PROVISIONAL_CONSERVATIVE",
        required_feature_bases=(PriceBasis.MID,),
        target_basis=PriceBasis.MID,
        fold_policy="EXPANDING_WALK_FORWARD",
        holdout_range=(START + timedelta(seconds=30), END),
        embargo=timedelta(minutes=5),
        minimum_training_duration=timedelta(minutes=15),
        minimum_validation_duration=timedelta(minutes=1),
    )


def test_all_configured_horizons_share_selection_rules_and_keep_excursions_independent() -> None:
    observations = _observations()
    config = _config(observations)
    targets = build_frozen_targets(observations, config, horizons=HORIZONS)

    assert {row.horizon for row in targets.rows} == set(HORIZONS)
    assert all(row.return_disposition is ReturnDisposition.VALID for row in targets.rows)
    five_minute = next(row for row in targets.rows if row.horizon == timedelta(minutes=5))
    longer_horizon = next(row for row in targets.rows if row.horizon == timedelta(minutes=15))
    assert five_minute.excursion_disposition.value == "VALID"
    assert longer_horizon.excursion_disposition.value == "INCOMPLETE_PATH"
    assert longer_horizon.upper_log_excursion is None
    assert longer_horizon.lower_log_excursion is None

    summaries = summarise_horizon_coverage(targets, config)

    assert tuple(summary.horizon for summary in summaries) == HORIZONS
    assert all(summary.total_target_count == 1 for summary in summaries)
    assert all(summary.valid_return_count == 1 for summary in summaries)
    assert summaries[0].valid_excursion_count == 1
    assert summaries[1].valid_excursion_count == 0
    assert summaries[1].return_coverage == 1.0
    assert summaries[1].excursion_coverage == 0.0


def test_horizon_changes_are_bound_into_configuration_and_target_identity() -> None:
    observations = _observations()
    config = _config(observations)
    single_horizon_config = replace(config, target_horizons=(timedelta(minutes=15),))
    all_targets = build_frozen_targets(observations, config, horizons=HORIZONS)
    single_targets = build_frozen_targets(
        observations,
        single_horizon_config,
        horizons=(timedelta(minutes=15),),
    )

    assert config.configuration_id != single_horizon_config.configuration_id
    assert all_targets.dataset_id != single_targets.dataset_id
    assert all_targets.foundation_configuration_id == config.configuration_id
    assert single_targets.foundation_configuration_id == single_horizon_config.configuration_id


def test_coverage_summary_can_report_a_horizon_absent_from_a_subset_dataset() -> None:
    observations = _observations()
    config = _config(observations)
    targets = build_frozen_targets(observations, config, horizons=(timedelta(minutes=15),))

    summaries = summarise_horizon_coverage(targets, config)

    assert summaries[0].total_target_count == 0
    assert summaries[1].total_target_count == 1
    assert summaries[1].valid_return_count == 1


def test_foundation_config_requires_canonical_horizon_order() -> None:
    config = _config(_observations())
    with pytest.raises(ValueError, match="canonical ascending"):
        replace(
            config,
            target_horizons=tuple(timedelta(minutes=minutes) for minutes in (60, 5, 15, 30)),
        )
