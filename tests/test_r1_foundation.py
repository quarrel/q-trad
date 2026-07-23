from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from math import log
from uuid import uuid4

import pytest

from qtrad.application.foundation import build_asof_panel, build_frozen_targets
from qtrad.domain.foundation import (
    AvailabilityBasis,
    ExcursionDisposition,
    FoundationConfig,
    InstrumentRole,
    PanelAuditDisposition,
    PanelStatus,
    ReturnDisposition,
)
from qtrad.domain.identifiers import InstrumentId
from qtrad.domain.market_data import BarProvenance, DataGap, DataQuality, PriceBasis
from qtrad.domain.research import ObservationDataset, ObservationRow


def _row(
    interval_end: datetime,
    *,
    close: str = "100",
    revision: int = 1,
    persisted_at: datetime | None = None,
    global_position: int | None = None,
    high: str | None = None,
    low: str | None = None,
) -> ObservationRow:
    persisted = persisted_at or interval_end + timedelta(seconds=1)
    return ObservationRow(
        event_id=uuid4(),
        stream_id="market-bar:fx:aud-usd:MID",
        stream_version=revision,
        event_type="MarketBarClosed" if revision == 1 else "MarketBarCorrected",
        event_time=interval_end,
        received_at=persisted,
        persisted_at=persisted,
        global_position=global_position or revision,
        instrument_id="fx:aud-usd",
        basis=PriceBasis.MID,
        interval_start=interval_end - timedelta(minutes=1),
        interval_end=interval_end,
        open=Decimal("100"),
        high=Decimal(high or close),
        low=Decimal(low or close),
        close=Decimal(close),
        sample_count=1,
        revision=revision,
        provenance=BarProvenance.QUOTE_DERIVED,
        quality=DataQuality.HEALTHY,
        source_provider="ig",
        source_environment="demo",
        source_external_id="AUDUSD",
    )


def _dataset(rows: tuple[ObservationRow, ...]) -> ObservationDataset:
    return ObservationDataset.create(rows, configuration={"fixture": "r1.b"})


def _config(dataset: ObservationDataset, *, start: datetime, end: datetime) -> FoundationConfig:
    return FoundationConfig(
        name="r1-b-fixture",
        schema_version=1,
        observation_dataset_id=dataset.dataset_id,
        ordered_instruments=("fx:aud-usd", "index:volatility"),
        instrument_roles={
            "fx:aud-usd": InstrumentRole.TARGET,
            "index:volatility": InstrumentRole.CONTEXT,
        },
        range_start=start,
        range_end=end,
        grid_resolution=timedelta(minutes=1),
        availability_basis=AvailabilityBasis.PERSISTED_AT,
        feature_lag_policy="PROVISIONAL_CONSERVATIVE",
        feature_lag_calibration_range=(start, end),
        feature_lag_percentile=0.95,
        feature_lag_safety_margin=timedelta(minutes=1),
        selected_feature_lag=timedelta(minutes=1),
        target_horizons=(timedelta(minutes=15),),
        primary_vertical_horizon=timedelta(minutes=15),
        target_revision_delay=timedelta(minutes=1),
        target_revision_policy="PROVISIONAL_CONSERVATIVE",
        required_feature_bases=(PriceBasis.MID,),
        target_basis=PriceBasis.MID,
        fold_policy="EXPANDING_WALK_FORWARD",
        holdout_range=(end - timedelta(minutes=1), end),
        embargo=timedelta(0),
        minimum_training_duration=timedelta(minutes=1),
        minimum_validation_duration=timedelta(minutes=1),
    )


def test_panel_selects_only_revisions_available_at_the_feature_cutoff() -> None:
    start = datetime(2026, 7, 1, 12, 3, tzinfo=UTC)
    first = _row(
        datetime(2026, 7, 1, 12, 2, tzinfo=UTC),
        close="101",
        persisted_at=datetime(2026, 7, 1, 12, 2, tzinfo=UTC),
        global_position=1,
    )
    late_correction = _row(
        first.interval_end,
        close="102",
        revision=2,
        persisted_at=datetime(2026, 7, 1, 12, 3, tzinfo=UTC),
        global_position=2,
    )
    dataset = _dataset((late_correction, first))
    config = _config(dataset, start=start, end=start + timedelta(minutes=1))
    panel = build_asof_panel(dataset, config)

    aud_rows = [row for row in panel.rows if row.instrument_id == "fx:aud-usd"]
    assert aud_rows[0].status is PanelStatus.OBSERVED
    assert aud_rows[0].selected_revision == 1
    assert aud_rows[0].close == Decimal("101")
    assert aud_rows[0].feature_data_asof != aud_rows[0].decision_time

    rebuilt = build_asof_panel(dataset, config)
    assert rebuilt.rows[0].selected_revision == aud_rows[0].selected_revision


def test_panel_missingness_is_explicit_and_audit_is_not_causal_state() -> None:
    start = datetime(2026, 7, 1, 12, 3, tzinfo=UTC)
    late = _row(
        datetime(2026, 7, 1, 12, 2, tzinfo=UTC),
        persisted_at=datetime(2026, 7, 1, 12, 4, tzinfo=UTC),
    )
    gap = DataGap(
        instrument_id=InstrumentId("fx:aud-usd"),
        interval_start=late.interval_start,
        interval_end=late.interval_end,
        reason="fixture gap",
        detected_at=datetime(2026, 7, 1, 12, 5, tzinfo=UTC),
    )
    dataset = _dataset((late,))
    panel = build_asof_panel(
        dataset,
        _config(dataset, start=start, end=start + timedelta(minutes=1)),
        gaps=(gap,),
    )
    row = next(row for row in panel.rows if row.instrument_id == "fx:aud-usd")
    assert row.status is PanelStatus.MISSING_AS_OF_CUTOFF
    assert row.close is None
    assert row.audit_disposition is PanelAuditDisposition.EVENTUALLY_OBSERVED_LATE

    no_evidence = build_asof_panel(
        _dataset(()),
        _config(_dataset(()), start=start, end=start + timedelta(minutes=1)),
    )
    no_evidence_row = next(
        row for row in no_evidence.rows if row.instrument_id == "fx:aud-usd"
    )
    assert no_evidence_row.audit_disposition is PanelAuditDisposition.NO_NATIVE_EVIDENCE


def test_gap_detected_after_cutoff_is_not_exposed_as_known_at_cutoff() -> None:
    start = datetime(2026, 7, 1, 12, 3, tzinfo=UTC)
    empty = _dataset(())
    config = _config(empty, start=start, end=start + timedelta(minutes=1))
    gap = DataGap(
        instrument_id=InstrumentId("fx:aud-usd"),
        interval_start=start - timedelta(minutes=2),
        interval_end=start - timedelta(minutes=1),
        reason="late diagnosis",
        detected_at=start + timedelta(minutes=1),
    )
    row = next(
        row
        for row in build_asof_panel(empty, config, gaps=(gap,)).rows
        if row.instrument_id == "fx:aud-usd"
    )
    assert row.audit_disposition is PanelAuditDisposition.RECORDED_GAP_DETECTED_LATER


def test_targets_freeze_revisions_and_keep_endpoint_return_separate_from_excursion() -> None:
    start = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    endpoint = start + timedelta(minutes=15)
    rows = [_row(start, close="100", global_position=1)]
    for minute in range(1, 16):
        end = start + timedelta(minutes=minute)
        if minute != 7:
            rows.append(
                _row(
                    end,
                    close=str(100 + minute),
                    high=str(101 + minute),
                    low=str(99 + minute),
                    global_position=minute + 1,
                )
            )
    rows.append(
        _row(
            endpoint,
            close="120",
            revision=2,
            persisted_at=start + timedelta(minutes=16, seconds=30),
            global_position=30,
        )
    )
    dataset = _dataset(tuple(rows))
    config = _config(dataset, start=start, end=start + timedelta(minutes=1))
    target = build_frozen_targets(dataset, config).rows[0]

    assert target.return_disposition is ReturnDisposition.VALID
    assert target.label_end_close == Decimal("115")
    assert target.log_return == pytest.approx(log(1.15))
    assert target.excursion_disposition is ExcursionDisposition.INCOMPLETE_PATH
    assert target.upper_log_excursion is None


def test_target_correction_before_freeze_is_selected_and_vix_gets_no_target() -> None:
    start = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    endpoint = start + timedelta(minutes=15)
    rows = [_row(start, close="100", global_position=1)]
    for minute in range(1, 16):
        rows.append(
            _row(
                start + timedelta(minutes=minute),
                close=str(100 + minute),
                global_position=minute + 1,
            )
        )
    rows.append(
        _row(
            endpoint,
            close="120",
            revision=2,
            persisted_at=start + timedelta(minutes=15, seconds=30),
            global_position=40,
        )
    )
    early_revision = replace(
        rows[-1],
        close=Decimal("125"),
        high=Decimal("125"),
        persisted_at=start + timedelta(minutes=15, seconds=45),
        global_position=41,
    )
    dataset = _dataset((*rows, early_revision))
    config = _config(dataset, start=start, end=start + timedelta(minutes=1))
    targets = build_frozen_targets(dataset, config).rows

    assert len(targets) == 1
    assert targets[0].label_end_close == Decimal("125")


@pytest.mark.parametrize(
    ("missing_start", "missing_end", "expected"),
    ((True, False, ReturnDisposition.MISSING_START),
     (False, True, ReturnDisposition.MISSING_END)),
)
def test_target_requires_exact_completed_endpoint_bars(
    missing_start: bool,
    missing_end: bool,
    expected: ReturnDisposition,
) -> None:
    start = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    endpoint = start + timedelta(minutes=15)
    rows = [] if missing_start else [_row(start, global_position=1)]
    if not missing_end:
        rows.append(_row(endpoint, close="115", global_position=2))
    dataset = _dataset(tuple(rows))
    target = build_frozen_targets(
        dataset, _config(dataset, start=start, end=start + timedelta(minutes=1))
    ).rows[0]
    assert target.return_disposition is expected


def test_ambiguous_target_source_fails_closed_without_selecting_a_price() -> None:
    start = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    endpoint = start + timedelta(minutes=15)
    first = _row(start, global_position=1)
    end = _row(endpoint, close="115", global_position=2)
    other_source = replace(end, source_external_id="OTHER")
    dataset = _dataset((first, end, other_source))
    target = build_frozen_targets(
        dataset, _config(dataset, start=start, end=start + timedelta(minutes=1))
    ).rows[0]
    assert target.return_disposition is ReturnDisposition.AMBIGUOUS_SOURCE
    assert target.label_end_close is None
