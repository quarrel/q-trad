import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from math import log
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import polars as pl
import pytest

from qtrad import __main__ as cli
from qtrad.adapters.parquet.r2 import ParquetR2FeatureStore
from qtrad.application.r2_features import (
    FeatureLineageError,
    R2FoundationInputs,
    _calculate,
    _context,
    _index,
    _missing_fraction,
    _return,
    _rolling,
    _RowCache,
    _spread,
    feature_schema_for_set,
    materialise_r2_features,
    select_current_cutoff,
    verify_raw_feature_dataset,
    verify_raw_feature_manifest_bindings,
    verify_raw_feature_rows,
)
from qtrad.application.r2_readiness import _availability_dataset_id
from qtrad.domain.foundation import AvailabilityBasis, PanelRow, PanelStatus
from qtrad.domain.identifiers import ProviderListingId
from qtrad.domain.market_data import PriceBasis
from qtrad.domain.r2_features import (
    FeatureDefinition,
    R2FeatureDataset,
    RawFeatureRow,
    RawFeatureValue,
    feature_registry,
    feature_schema_id,
    feature_set_id,
)
from qtrad.domain.r2_readiness import (
    EligibilityDecision,
    EvidenceClass,
    FeatureEligibility,
    FeatureFamily,
    FeatureSet,
    R2ExperimentConfig,
)
from qtrad.domain.research import ObservationRow
from qtrad.runtime.settings import Settings
from tests.test_r1_observations import _bar, _candidate, _dataset
from tests.test_r2_readiness import END, START, TARGETS, experiment


class FixedClock:
    def __init__(self, now: datetime = datetime(2026, 7, 28, tzinfo=UTC)) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


def _minimal_foundation(
    start: datetime,
    end: datetime,
    instruments: tuple[str, ...] = ("fx:aud-usd",),
    *,
    availability_basis: AvailabilityBasis = AvailabilityBasis.PERSISTED_AT,
    active: dict[str, tuple[tuple[datetime, datetime], ...]] | None = None,
) -> R2FoundationInputs:
    intervals = active or {instrument: ((start, end),) for instrument in instruments}
    return cast(
        R2FoundationInputs,
        SimpleNamespace(
            configuration=SimpleNamespace(
                grid_resolution=timedelta(minutes=1),
                availability_basis=availability_basis,
            ),
            source_active_intervals=intervals,
        ),
    )


def _availability_evidence(
    config: R2ExperimentConfig,
    intervals: dict[str, tuple[tuple[datetime, datetime], ...]],
) -> dict[str, Any]:
    complete = {
        instrument: intervals.get(instrument, ()) for instrument in config.ordered_instruments
    }
    return {
        "availability_delay_report": {},
        "revision_delay_report": {},
        "data_gaps": [],
        "source_active_intervals": {
            instrument: [[left.isoformat(), right.isoformat()] for left, right in values]
            for instrument, values in complete.items()
        },
        "lineage_summary": {},
        "observation_bounds": {
            "interval_start": START.isoformat(),
            "interval_end": END.isoformat(),
        },
    }


def _foundation(
    config: R2ExperimentConfig,
    *,
    observation_rows: tuple[ObservationRow, ...] = (),
    panel_rows: tuple[PanelRow, ...] = (),
    active: dict[str, tuple[tuple[datetime, datetime], ...]] | None = None,
    overrides: dict[str, str] | None = None,
) -> R2FoundationInputs:
    values = overrides or {}
    observation_id = values.get("observation_id", config.observation_dataset_id)
    configuration_id = values.get("configuration_id", config.foundation_configuration_id)
    panel_id = values.get("panel_id", config.panel_dataset_id)
    target_id = values.get("target_id", config.target_dataset_id)
    fold_id = values.get("fold_id", config.fold_dataset_id)
    foundation_id = values.get("foundation_id", config.r1_bundle_id)
    intervals: dict[str, tuple[tuple[datetime, datetime], ...]] = (
        active
        if active is not None
        else {instrument: () for instrument in config.ordered_instruments}
    )
    evidence = _availability_evidence(config, intervals)
    availability_id = _availability_dataset_id(observation_id, evidence)
    return R2FoundationInputs(
        bundle=cast(
            Any,
            SimpleNamespace(
                foundation_id=foundation_id,
                ordered_instruments=config.ordered_instruments,
                range_start=START,
                range_end=END,
                configuration=SimpleNamespace(dataset_id=configuration_id),
                observations=SimpleNamespace(dataset_id=observation_id),
                availability=SimpleNamespace(dataset_id=availability_id),
                panel=SimpleNamespace(dataset_id=panel_id),
                targets=SimpleNamespace(dataset_id=target_id),
                folds=SimpleNamespace(dataset_id=fold_id),
                build_summary={
                    "application_version": config.r1_application_version,
                    "image_identity": config.r1_image_identity,
                },
            ),
        ),
        configuration=cast(
            Any,
            SimpleNamespace(
                configuration_id=configuration_id,
                observation_dataset_id=observation_id,
                ordered_instruments=config.ordered_instruments,
                instrument_roles=config.instrument_roles,
                grid_resolution=timedelta(minutes=1),
                target_horizons=config.horizons,
                holdout_range=config.holdout_range,
                range_start=START,
                range_end=END,
                availability_basis=AvailabilityBasis.PERSISTED_AT,
            ),
        ),
        observations=cast(
            Any,
            SimpleNamespace(
                dataset_id=observation_id,
                rows=observation_rows,
                selection_policies={"availability_basis": AvailabilityBasis.PERSISTED_AT.value},
            ),
        ),
        panel=cast(
            Any,
            SimpleNamespace(
                dataset_id=panel_id,
                observation_dataset_id=values.get("panel_observation_id", observation_id),
                foundation_configuration_id=values.get("panel_configuration_id", configuration_id),
                rows=panel_rows,
            ),
        ),
        targets=cast(
            Any,
            SimpleNamespace(
                dataset_id=target_id,
                observation_dataset_id=values.get("target_observation_id", observation_id),
                foundation_configuration_id=values.get("target_configuration_id", configuration_id),
            ),
        ),
        folds=cast(
            Any,
            SimpleNamespace(
                dataset_id=fold_id,
                foundation_configuration_id=values.get("fold_configuration_id", configuration_id),
                target_dataset_id=values.get("fold_target_id", target_id),
            ),
        ),
        availability_evidence=evidence,
    )


def _panel(config: R2ExperimentConfig, row: ObservationRow) -> PanelRow:
    decision_time = config.holdout_range[0] - timedelta(days=1)
    return PanelRow(
        decision_time=decision_time,
        instrument_id=config.target_instruments[0],
        basis=PriceBasis.MID,
        feature_data_asof=decision_time,
        latest_feature_bar_end=row.interval_end,
        status=PanelStatus.OBSERVED,
        audit_disposition=None,
        selected_event_id=row.event_id,
        selected_stream_version=row.stream_version,
        selected_global_position=row.global_position,
        selected_availability_time=row.persisted_at,
        selected_revision=row.revision,
        interval_start=row.interval_start,
        interval_end=row.interval_end,
        open=row.open,
        high=row.high,
        low=row.low,
        close=row.close,
        sample_count=row.sample_count,
        quality=row.quality,
    )


def _with_eligible_family(
    config: R2ExperimentConfig,
    family: FeatureFamily,
) -> R2ExperimentConfig:
    decisions = dict(config.feature_eligibility)
    current = decisions[family]
    decisions[family] = EligibilityDecision.create(
        subject=current.subject,
        state=FeatureEligibility.ELIGIBLE,
        evidence_start=current.evidence_start,
        evidence_end=current.evidence_end,
        reason=current.reason,
    )
    local = (FeatureFamily.LOCAL_RETURNS, FeatureFamily.TIME_AVAILABILITY)
    feature_sets = [FeatureSet("L0", local)]
    local = (*local, FeatureFamily.LOCAL_VOLATILITY_RANGE)
    feature_sets.append(FeatureSet("L1", local))
    if decisions[FeatureFamily.SPREAD].state is FeatureEligibility.ELIGIBLE:
        local = (*local, FeatureFamily.SPREAD)
        feature_sets.append(FeatureSet("L2", local))
    if decisions[FeatureFamily.QUOTE_IMBALANCE].state is FeatureEligibility.ELIGIBLE:
        local = (*local, FeatureFamily.QUOTE_IMBALANCE)
        feature_sets.append(FeatureSet("L3", local))
    feature_sets.extend(
        (
            FeatureSet("P0", local),
            FeatureSet("P1", (*local, FeatureFamily.POOLED_CROSS_ASSET)),
        )
    )
    return replace(config, feature_sets=tuple(feature_sets), feature_eligibility=decisions)


def _wider_target_experiment() -> R2ExperimentConfig:
    config = experiment()
    eligibility = dict(config.target_instrument_eligibility)
    current = eligibility[TARGETS[-1]]
    eligibility[TARGETS[-1]] = EligibilityDecision.create(
        subject=current.subject,
        state=FeatureEligibility.ELIGIBLE,
        evidence_start=current.evidence_start,
        evidence_end=current.evidence_end,
        reason="eligible wider-model target fixture",
    )
    return replace(
        config,
        target_instrument_eligibility=eligibility,
        target_instruments=TARGETS,
    )


def test_feature_registry_and_explicit_empty_dataset_identity_are_deterministic() -> None:
    config = experiment()
    schema = feature_schema_for_set(config, "L0")
    assert schema == feature_schema_for_set(config, "L0")
    assert len({item.name for item in feature_registry(config)}) == len(feature_registry(config))
    dataset = R2FeatureDataset.create(
        (),
        feature_schema=schema,
        feature_set_name="L0",
        observation_dataset_id=config.observation_dataset_id,
        panel_dataset_id=config.panel_dataset_id,
        target_dataset_id=config.target_dataset_id,
        fold_dataset_id=config.fold_dataset_id,
        experiment_configuration_id=config.configuration_id,
        evidence_class=config.evidence_class,
    )
    assert dataset.rows == ()
    assert dataset.feature_set_id == feature_set_id(config.configuration_id, "L0", schema)
    assert len(dataset.dataset_id) == 64


def test_r2b_feature_json_and_source_bound_ids_are_deterministic() -> None:
    schema = (
        FeatureDefinition("return_60s", FeatureFamily.LOCAL_RETURNS),
        FeatureDefinition("window_coverage_300s", FeatureFamily.LOCAL_VOLATILITY_RANGE),
    )
    assert schema[0].as_json() == {
        "name": "return_60s",
        "family": "LOCAL_RETURNS",
        "availability_indicator": False,
    }
    assert "kind" not in schema[0].as_json()
    assert (
        feature_schema_id(schema)
        == "b77d8c36f5f98f79cd605787fede860335f75e7942992d75f32d7d684465e012"
    )
    set_identity = feature_set_id("c" * 64, "fixture", schema)
    assert set_identity == "e77eb0a85bb37ce0a3ed44afc642e8c9b48ae4f6919ce7be7d056bab214cecfb"
    dataset = R2FeatureDataset.create(
        (),
        feature_schema=schema,
        feature_set_name="fixture",
        observation_dataset_id="a" * 64,
        panel_dataset_id="b" * 64,
        target_dataset_id="d" * 64,
        fold_dataset_id="e" * 64,
        experiment_configuration_id="c" * 64,
        evidence_class=EvidenceClass.IMPLEMENTATION,
    )
    assert dataset.dataset_id == "57feeaa2f139fefd227c3ccf4a52c2ae04e415e268956bc108e7f7adfc8b4a5f"


def test_current_cutoff_obeys_configured_availability_and_exact_both_endpoints() -> None:
    start = datetime(2026, 2, 1, 12, tzinfo=UTC)
    first = _candidate(
        _bar(start, close="1.1"),
        position=1,
        received=start + timedelta(minutes=1),
        persisted=start + timedelta(minutes=1, seconds=30),
    )
    correction = _candidate(
        _bar(start, close="1.2", revision=2),
        position=2,
        received=start + timedelta(minutes=1, seconds=15),
        persisted=start + timedelta(minutes=3),
        stream_version=2,
    )
    rows = _dataset((first, correction)).rows
    cutoff = start + timedelta(minutes=2)
    received = select_current_cutoff(
        rows,
        instrument_id="fx:aud-usd",
        basis=PriceBasis.MID,
        interval_start=start,
        latest_feature_bar_end=start + timedelta(minutes=1),
        feature_data_asof=cutoff,
        availability_basis=AvailabilityBasis.RECEIVED_AT,
    )
    persisted = select_current_cutoff(
        tuple(reversed(rows)),
        instrument_id="fx:aud-usd",
        basis=PriceBasis.MID,
        interval_start=start,
        latest_feature_bar_end=start + timedelta(minutes=1),
        feature_data_asof=cutoff,
        availability_basis=AvailabilityBasis.PERSISTED_AT,
    )
    assert received is not None and received.revision == 2
    assert persisted is not None and persisted.revision == 1

    wrong_start = replace(rows[0], interval_start=start - timedelta(minutes=1))
    assert (
        select_current_cutoff(
            (wrong_start,),
            instrument_id="fx:aud-usd",
            basis=PriceBasis.MID,
            interval_start=start,
            latest_feature_bar_end=start + timedelta(minutes=1),
            feature_data_asof=cutoff,
            availability_basis=AvailabilityBasis.RECEIVED_AT,
        )
        is None
    )


def test_current_cutoff_rejects_ambiguous_sources_and_highest_revision() -> None:
    start = datetime(2026, 2, 1, 12, tzinfo=UTC)
    left = _candidate(
        _bar(start),
        position=1,
        received=start + timedelta(minutes=1),
        persisted=start + timedelta(minutes=1),
    )
    other = _candidate(
        replace(
            _bar(start),
            source_listing_id=ProviderListingId("other", "demo", "AUDUSD"),
        ),
        position=2,
        received=start + timedelta(minutes=1),
        persisted=start + timedelta(minutes=1),
        stream_version=2,
    )
    with pytest.raises(FeatureLineageError, match="ambiguous source"):
        select_current_cutoff(
            _dataset((left, other)).rows,
            instrument_id="fx:aud-usd",
            basis=PriceBasis.MID,
            interval_start=start,
            latest_feature_bar_end=start + timedelta(minutes=1),
            feature_data_asof=start + timedelta(minutes=2),
            availability_basis=AvailabilityBasis.PERSISTED_AT,
        )
    left_row = _dataset((left,)).rows[0]
    duplicate = replace(left_row, global_position=2, stream_version=2)
    with pytest.raises(FeatureLineageError, match="highest revision"):
        select_current_cutoff(
            (left_row, duplicate),
            instrument_id="fx:aud-usd",
            basis=PriceBasis.MID,
            interval_start=start,
            latest_feature_bar_end=start + timedelta(minutes=1),
            feature_data_asof=start + timedelta(minutes=2),
            availability_basis=AvailabilityBasis.PERSISTED_AT,
        )


def test_exact_return_and_five_minute_window_use_six_endpoints_and_five_pairs() -> None:
    start = datetime(2026, 2, 1, 12, tzinfo=UTC)
    rows = _dataset(
        tuple(
            _candidate(
                _bar(start + timedelta(minutes=offset), close=str(2**offset)),
                position=offset + 1,
                received=start + timedelta(minutes=offset + 1),
                persisted=start + timedelta(minutes=offset + 1),
            )
            for offset in range(6)
        )
    ).rows
    foundation = _minimal_foundation(start, start + timedelta(minutes=6))
    index = _index(tuple(reversed(rows)))
    end = start + timedelta(minutes=6)
    cutoff = end + timedelta(minutes=1)
    value, lineage = _return(
        "return_300s", index, "fx:aud-usd", end, cutoff, foundation, _RowCache()
    )
    assert value == pytest.approx(5 * log(2))
    assert len(lineage) == 2
    mean, return_lineage = _rolling(
        "mean_absolute_return_300s",
        "fx:aud-usd",
        index,
        end,
        cutoff,
        foundation,
        experiment(),
        _RowCache(),
    )
    count, range_lineage = _rolling(
        "available_interval_count_300s",
        "fx:aud-usd",
        index,
        end,
        cutoff,
        foundation,
        experiment(),
        _RowCache(),
    )
    coverage, _ = _rolling(
        "window_coverage_300s",
        "fx:aud-usd",
        index,
        end,
        cutoff,
        foundation,
        experiment(),
        _RowCache(),
    )
    assert mean == pytest.approx(log(2))
    assert len(return_lineage) == 6
    assert count == 5.0
    assert len(range_lineage) == 5
    assert coverage == 1.0


def test_rolling_gap_inactive_boundary_closure_and_lineage_fail_closed() -> None:
    start = datetime(2026, 2, 1, 12, tzinfo=UTC)
    rows = _dataset(
        tuple(
            _candidate(
                _bar(start + timedelta(minutes=offset), close=str(offset + 1)),
                position=offset + 1,
                received=start + timedelta(minutes=offset + 1),
                persisted=start + timedelta(minutes=offset + 1),
            )
            for offset in range(6)
        )
    ).rows
    end = start + timedelta(minutes=6)
    cutoff = end + timedelta(minutes=1)
    full = _minimal_foundation(start, end)
    gapped = tuple(row for row in rows if row.interval_start != start + timedelta(minutes=3))
    value, _ = _rolling(
        "realised_std_300s",
        "fx:aud-usd",
        _index(gapped),
        end,
        cutoff,
        full,
        experiment(),
        _RowCache(),
    )
    coverage, _ = _rolling(
        "window_coverage_300s",
        "fx:aud-usd",
        _index(gapped),
        end,
        cutoff,
        full,
        experiment(),
        _RowCache(),
    )
    assert value is None
    assert coverage == pytest.approx(0.8)

    boundary = _minimal_foundation(start + timedelta(minutes=1), end)
    boundary_count, _ = _rolling(
        "available_interval_count_300s",
        "fx:aud-usd",
        _index(rows),
        end,
        cutoff,
        boundary,
        experiment(),
        _RowCache(),
    )
    assert boundary_count == 5.0
    closed = _minimal_foundation(start, end, active={"fx:aud-usd": ()})
    closed_coverage, _ = _rolling(
        "window_coverage_300s",
        "fx:aud-usd",
        _index(rows),
        end,
        cutoff,
        closed,
        experiment(),
        _RowCache(),
    )
    assert closed_coverage is None

    cross_source = (*rows[:-1], replace(rows[-1], source_external_id="OTHER"))
    with pytest.raises(FeatureLineageError, match="crosses source lineage"):
        _rolling(
            "mean_absolute_return_300s",
            "fx:aud-usd",
            _index(cross_source),
            end,
            cutoff,
            full,
            experiment(),
            _RowCache(),
        )


def test_missing_fraction_uses_activity_adjusted_exact_slots() -> None:
    start = datetime(2026, 2, 1, 12, tzinfo=UTC)
    rows = _dataset(
        tuple(
            _candidate(
                _bar(start + timedelta(minutes=offset)),
                position=offset + 1,
                received=start + timedelta(minutes=offset + 1),
                persisted=(
                    start + timedelta(minutes=7)
                    if offset == 4
                    else start + timedelta(minutes=offset + 1)
                ),
            )
            for offset in range(5)
        )
    ).rows
    panel = cast(
        PanelRow,
        SimpleNamespace(
            instrument_id="fx:aud-usd",
            latest_feature_bar_end=start + timedelta(minutes=5),
            feature_data_asof=start + timedelta(minutes=6),
        ),
    )
    foundation = _minimal_foundation(start, start + timedelta(minutes=5))
    value, lineage = _missing_fraction(_index(rows), panel, foundation, experiment(), _RowCache())
    assert value == pytest.approx(0.2)
    assert len(lineage) == 4
    partial = _minimal_foundation(
        start,
        start + timedelta(minutes=5),
        active={"fx:aud-usd": ((start + timedelta(minutes=1), start + timedelta(minutes=5)),)},
    )
    partial_value, _ = _missing_fraction(_index(rows), panel, partial, experiment(), _RowCache())
    assert partial_value == pytest.approx(0.25)


def _peer_rows(
    start: datetime,
    scales: dict[str, Decimal],
) -> tuple[ObservationRow, ...]:
    rows: list[ObservationRow] = []
    position = 1
    for instrument, scale in scales.items():
        for offset, close in enumerate((Decimal("1"), scale)):
            candidate = _candidate(
                replace(
                    _bar(start + timedelta(minutes=offset)),
                    close=close,
                    open=close,
                    high=close,
                    low=close,
                ),
                position=position,
                received=start + timedelta(minutes=offset + 1),
                persisted=start + timedelta(minutes=offset + 1),
            )
            rows.append(
                replace(
                    _dataset((candidate,)).rows[0],
                    instrument_id=instrument,
                    stream_id=f"market-bar:{instrument}:MID",
                )
            )
            position += 1
    return tuple(rows)


def test_pooled_universes_use_fixed_leave_one_out_and_group_denominators() -> None:
    config = _wider_target_experiment()
    start = datetime(2026, 2, 1, 12, tzinfo=UTC)
    scales = {instrument: Decimal("1.1") for instrument in config.target_instruments}
    scales[config.target_instruments[0]] = Decimal("100")
    scales["index:volatility"] = Decimal("1.4")
    rows = _peer_rows(start, scales)
    missing_peer = config.target_instruments[-1]
    sparse = tuple(row for row in rows if row.instrument_id != missing_peer)
    active: dict[str, tuple[tuple[datetime, datetime], ...]] = {
        instrument: ((start, start + timedelta(minutes=2)),)
        for instrument in config.ordered_instruments
    }
    foundation = _minimal_foundation(
        start,
        start + timedelta(minutes=2),
        config.ordered_instruments,
        active=active,
    )
    panel = cast(
        PanelRow,
        SimpleNamespace(
            instrument_id=config.target_instruments[0],
            latest_feature_bar_end=start + timedelta(minutes=2),
            feature_data_asof=start + timedelta(minutes=3),
        ),
    )
    sparse_index = _index(tuple(reversed(sparse)))
    global_mean, _ = _context(
        "loo_mean_return_60s",
        panel,
        sparse_index,
        config,
        foundation,
        _RowCache(),
    )
    available_count, _ = _context(
        "loo_available_count_60s",
        panel,
        sparse_index,
        config,
        foundation,
        _RowCache(),
    )
    group_mean, _ = _context(
        "loo_market_group_mean_return_60s",
        panel,
        sparse_index,
        config,
        foundation,
        _RowCache(),
    )
    vix, _ = _context(
        "vix_context_return_60s",
        panel,
        _index(rows),
        config,
        foundation,
        _RowCache(),
    )
    assert global_mean is None
    assert available_count == 5.0
    assert group_mean == pytest.approx(log(1.1))
    assert vix == pytest.approx(log(1.4))

    no_group_panel = cast(
        PanelRow,
        SimpleNamespace(
            instrument_id=TARGETS[-1],
            latest_feature_bar_end=start + timedelta(minutes=2),
            feature_data_asof=start + timedelta(minutes=3),
        ),
    )
    no_group, lineage = _context(
        "loo_market_group_mean_return_60s",
        no_group_panel,
        _index(rows),
        config,
        foundation,
        _RowCache(),
    )
    assert no_group is None and lineage == ()


def test_cross_market_counts_use_leave_one_out_model_universe() -> None:
    config = _wider_target_experiment()
    start = datetime(2026, 2, 1, 12, tzinfo=UTC)
    peers = config.target_instruments[1:]
    available_peer = peers[0]
    rows = _peer_rows(start, {available_peer: Decimal("1.1")})
    active: dict[str, tuple[tuple[datetime, datetime], ...]] = {
        instrument: () for instrument in config.ordered_instruments
    }
    active[available_peer] = ((start, start + timedelta(minutes=2)),)
    active[peers[1]] = ((start, start + timedelta(minutes=2)),)
    foundation = _minimal_foundation(
        start,
        start + timedelta(minutes=2),
        config.ordered_instruments,
        active=active,
    )
    panel = cast(
        PanelRow,
        SimpleNamespace(
            instrument_id=config.target_instruments[0],
            decision_time=start + timedelta(minutes=3),
            latest_feature_bar_end=start + timedelta(minutes=2),
            feature_data_asof=start + timedelta(minutes=3),
            audit_disposition=None,
        ),
    )
    index = _index(rows)
    active_count, _ = _calculate(
        "cross_market_source_active_count",
        panel,
        index,
        config,
        foundation,
        _RowCache(),
    )
    missing_count, lineage = _calculate(
        "cross_market_missing_count",
        panel,
        index,
        config,
        foundation,
        _RowCache(),
    )
    available_count, _ = _calculate(
        "cross_market_available_count",
        panel,
        index,
        config,
        foundation,
        _RowCache(),
    )
    assert active_count == 2.0
    assert missing_count == 1.0
    assert available_count == 1.0
    assert len(lineage) == 1


def test_spread_alignment_source_identity_invalid_sides_and_coverage() -> None:
    config = _with_eligible_family(experiment(), FeatureFamily.SPREAD)
    start = datetime(2026, 2, 1, 12, tzinfo=UTC)
    candidates = tuple(
        _candidate(
            replace(
                _bar(start + timedelta(minutes=offset)),
                basis=basis,
                open=price,
                high=price,
                low=price,
                close=price,
            ),
            position=offset * 3 + index + 1,
            received=start + timedelta(minutes=offset + 1),
            persisted=start + timedelta(minutes=offset + 1),
            stream_id=f"market-bar:fx:aud-usd:{basis.value}",
        )
        for offset in range(5)
        for index, (basis, price) in enumerate(
            (
                (PriceBasis.BID, Decimal("98") if offset == 4 else Decimal("99")),
                (PriceBasis.ASK, Decimal("102") if offset == 4 else Decimal("101")),
                (PriceBasis.MID, Decimal("100")),
            )
        )
    )
    rows = _dataset(candidates).rows
    foundation = _minimal_foundation(start, start + timedelta(minutes=5))
    panel = cast(
        PanelRow,
        SimpleNamespace(
            instrument_id="fx:aud-usd",
            latest_feature_bar_end=start + timedelta(minutes=5),
            feature_data_asof=start + timedelta(minutes=6),
        ),
    )
    index = _index(rows)
    close, _ = _spread("close_spread", panel, index, foundation, config, _RowCache())
    mean, _ = _spread("rolling_spread_mean", panel, index, foundation, config, _RowCache())
    change, _ = _spread("rolling_spread_change", panel, index, foundation, config, _RowCache())
    coverage, _ = _spread("spread_coverage", panel, index, foundation, config, _RowCache())
    assert close == 4.0
    assert mean == pytest.approx(2.4)
    assert change == 2.0
    assert coverage == 1.0

    misaligned = tuple(
        replace(row, source_external_id="OTHER")
        if row.basis is PriceBasis.ASK and row.interval_end == start + timedelta(minutes=5)
        else row
        for row in rows
    )
    invalid, _ = _spread("close_spread", panel, _index(misaligned), foundation, config, _RowCache())
    assert invalid is None
    crossed = tuple(
        replace(row, close=Decimal("97"))
        if row.basis is PriceBasis.ASK and row.interval_end == start + timedelta(minutes=5)
        else row
        for row in rows
    )
    crossed_value, _ = _spread(
        "close_spread", panel, _index(crossed), foundation, config, _RowCache()
    )
    assert crossed_value is None
    missing = tuple(
        row
        for row in rows
        if not (row.basis is PriceBasis.ASK and row.interval_end == start + timedelta(minutes=3))
    )
    sparse_mean, _ = _spread(
        "rolling_spread_mean", panel, _index(missing), foundation, config, _RowCache()
    )
    sparse_coverage, _ = _spread(
        "spread_coverage", panel, _index(missing), foundation, config, _RowCache()
    )
    assert sparse_mean is None
    assert sparse_coverage == pytest.approx(0.8)


def test_eligible_l3_fails_closed_without_validated_quote_size() -> None:
    config = _with_eligible_family(experiment(), FeatureFamily.QUOTE_IMBALANCE)
    with pytest.raises(ValueError, match="validated quote-size source"):
        feature_schema_for_set(config, "L3")


def _replay_foundation(
    config: R2ExperimentConfig, *, include_holdout: bool = False
) -> R2FoundationInputs:
    decision_time = config.holdout_range[0] - timedelta(days=1)
    latest_end = decision_time - timedelta(minutes=1)
    start = latest_end - timedelta(minutes=5)
    rows = tuple(
        replace(
            row,
            instrument_id=config.target_instruments[0],
            stream_id=f"market-bar:{config.target_instruments[0]}:MID",
            source_external_id="TARGET-1",
        )
        for row in _dataset(
            tuple(
                _candidate(
                    _bar(start + timedelta(minutes=offset), close=str(offset + 1)),
                    position=offset + 1,
                    received=start + timedelta(minutes=offset + 1),
                    persisted=start + timedelta(minutes=offset + 1),
                )
                for offset in range(5)
            )
        ).rows
    )
    panel = _panel(config, rows[-1])
    panels = (panel,)
    if include_holdout:
        panels = (*panels, replace(panel, decision_time=config.holdout_range[0]))
    return _foundation(
        config,
        observation_rows=rows,
        panel_rows=panels,
        active={config.target_instruments[0]: ((start, latest_end),)},
    )


def test_materialisation_is_explicit_order_independent_target_only_and_excludes_holdout() -> None:
    config = experiment()
    foundation = _replay_foundation(config, include_holdout=True)
    dataset = materialise_r2_features(foundation, config, feature_set_name="L0")
    reversed_foundation = _foundation(
        config,
        observation_rows=tuple(reversed(foundation.observations.rows)),
        panel_rows=foundation.panel.rows,
        active={
            config.target_instruments[0]: tuple(
                foundation.source_active_intervals[config.target_instruments[0]]
            )
        },
    )
    reversed_dataset = materialise_r2_features(
        reversed_foundation,
        config,
        feature_set_name="L0",
    )
    assert len(dataset.rows) == 1
    assert dataset.rows[0].target_instrument_id in config.target_instruments
    assert not (config.holdout_range[0] <= dataset.rows[0].decision_time < config.holdout_range[1])
    values = {item.name: item.value for item in dataset.rows[0].values}
    assert values["return_60s"] == pytest.approx(log(5 / 4))
    assert reversed_dataset == dataset
    verify_raw_feature_dataset(dataset, foundation, config, feature_set_name="L0")


@pytest.mark.parametrize(
    "override",
    (
        {"foundation_id": "0" * 64},
        {"observation_id": "0" * 64},
        {"configuration_id": "0" * 64},
        {"panel_id": "0" * 64},
        {"target_id": "0" * 64},
        {"fold_id": "0" * 64},
        {"panel_observation_id": "0" * 64},
        {"target_observation_id": "0" * 64},
        {"fold_target_id": "0" * 64},
    ),
)
def test_materialisation_rejects_cross_foundation_bindings(override: dict[str, str]) -> None:
    config = experiment()
    with pytest.raises(ValueError, match="binding"):
        materialise_r2_features(
            _foundation(config, overrides=override),
            config,
            feature_set_name="L0",
        )


def test_authenticated_source_active_values_cannot_be_mutated_independently() -> None:
    config = experiment()
    foundation = _foundation(config)
    mutable_intervals = cast(Any, foundation.source_active_intervals)
    with pytest.raises(TypeError):
        mutable_intervals[config.ordered_instruments[0]] = ()
    changed = dict(foundation.availability_evidence)
    changed["source_active_intervals"] = {}
    changed_foundation = R2FoundationInputs(
        bundle=foundation.bundle,
        configuration=foundation.configuration,
        observations=foundation.observations,
        panel=foundation.panel,
        targets=foundation.targets,
        folds=foundation.folds,
        availability_evidence=changed,
    )
    with pytest.raises(ValueError, match="availability"):
        materialise_r2_features(
            changed_foundation,
            config,
            feature_set_name="L0",
        )


def _feature_rows(
    count: int,
    set_identity: str,
    schema: tuple[FeatureDefinition, ...],
) -> tuple[RawFeatureRow, ...]:
    start = datetime(2026, 3, 1, tzinfo=UTC)
    return tuple(
        RawFeatureRow(
            target_instrument_id=f"index:target-{index % 3}",
            decision_time=start + timedelta(minutes=index),
            feature_data_asof=start + timedelta(minutes=index, seconds=30),
            latest_feature_bar_end=start + timedelta(minutes=index),
            feature_set_id=set_identity,
            values=tuple(
                RawFeatureValue(
                    definition.name,
                    None if (index + feature_index) % 4 == 0 else float(index + feature_index),
                    () if feature_index % 2 == 0 else (f"event-{index}",),
                )
                for feature_index, definition in enumerate(schema)
            ),
        )
        for index in range(count)
    )


def _write_store(
    root: Path,
    *,
    manifest_name: str = "features.json",
    count: int = 5,
    chunk_rows: int = 2,
    now: datetime = datetime(2026, 7, 28, tzinfo=UTC),
    image: str = "image-a",
) -> tuple[ParquetR2FeatureStore, Any, tuple[RawFeatureRow, ...]]:
    schema = (
        FeatureDefinition("return_60s", FeatureFamily.LOCAL_RETURNS),
        FeatureDefinition("window_coverage_300s", FeatureFamily.LOCAL_VOLATILITY_RANGE),
    )
    experiment_id = "c" * 64
    set_identity = feature_set_id(experiment_id, "fixture", schema)
    rows = _feature_rows(count, set_identity, schema)
    store = ParquetR2FeatureStore(root, FixedClock(now), chunk_rows=chunk_rows)
    manifest = store.write(
        Path(manifest_name),
        iter(rows),
        feature_set_name="fixture",
        feature_set_id=set_identity,
        feature_schema=schema,
        observation_dataset_id="a" * 64,
        panel_dataset_id="b" * 64,
        target_dataset_id="d" * 64,
        fold_dataset_id="e" * 64,
        experiment_configuration_id=experiment_id,
        evidence_class=EvidenceClass.IMPLEMENTATION,
        holdout_excluded=True,
        application_version="test",
        image_identity=image,
    )
    return store, manifest, rows


def test_chunked_parquet_round_trip_zero_rows_and_physical_semantic_identity(
    tmp_path: Path,
) -> None:
    store, manifest, rows = _write_store(tmp_path)
    assert manifest.row_count == 5
    assert len(manifest.chunks) == 3
    assert tuple(store.iter_rows(Path("features.json"))) == rows
    assert store.load(Path("features.json")).dataset_id == manifest.semantic_dataset_id

    zero_store, zero, _ = _write_store(
        tmp_path,
        manifest_name="zero.json",
        count=0,
        chunk_rows=3,
    )
    assert zero.row_count == 0 and zero.chunks == ()
    assert tuple(zero_store.iter_rows(Path("zero.json"))) == ()

    other_store, physical, _ = _write_store(
        tmp_path,
        manifest_name="features-other.json",
        count=5,
        chunk_rows=3,
        now=datetime(2026, 7, 29, tzinfo=UTC),
        image="image-b",
    )
    assert other_store.verify(Path("features-other.json")) == physical
    assert physical.semantic_dataset_id == manifest.semantic_dataset_id
    assert physical.manifest_sha256 != manifest.manifest_sha256


def test_parquet_manifest_chunk_value_lineage_and_schema_tampering_fail_closed(
    tmp_path: Path,
) -> None:
    store, manifest, _ = _write_store(tmp_path)
    first = manifest.chunks[0]
    data_path = tmp_path / first.data_file
    frame = pl.read_parquet(data_path)
    frame.with_columns((pl.col("f0000") + 1).alias("f0000")).write_parquet(data_path)
    with pytest.raises(ValueError, match="data chunk hash"):
        store.verify(Path("features.json"))

    lineage_root = tmp_path / "lineage-case"
    lineage_store, lineage_manifest, _ = _write_store(lineage_root)
    lineage_path = lineage_root / lineage_manifest.chunks[0].lineage_file
    lineage = pl.read_parquet(lineage_path)
    lineage.with_columns(pl.lit('["tampered"]').alias("source_event_ids")).write_parquet(
        lineage_path
    )
    with pytest.raises(ValueError, match="lineage chunk hash"):
        lineage_store.verify(Path("features.json"))

    schema_root = tmp_path / "schema-case"
    schema_store, schema_manifest, _ = _write_store(schema_root)
    schema_path = schema_root / schema_manifest.chunks[0].data_file
    schema_frame = pl.read_parquet(schema_path).rename({"f0000": "wrong"})
    schema_frame.write_parquet(schema_path)
    manifest_payload = json.loads((schema_root / "features.json").read_text(encoding="utf-8"))
    manifest_payload["chunks"][0]["data_sha256"] = hashlib.sha256(
        schema_path.read_bytes()
    ).hexdigest()
    identity = {
        key: value
        for key, value in manifest_payload.items()
        if key not in {"manifest_id", "manifest_sha256"}
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    manifest_payload["manifest_id"] = digest[:24]
    manifest_payload["manifest_sha256"] = digest
    (schema_root / "features.json").write_text(json.dumps(manifest_payload), encoding="utf-8")
    with pytest.raises(ValueError, match="schema"):
        schema_store.verify(Path("features.json"))


def test_parquet_path_safety_no_clobber_and_failed_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _, _ = _write_store(tmp_path)
    payload = json.loads((tmp_path / "features.json").read_text(encoding="utf-8"))
    payload["chunks"][0]["data_file"] = "../escape.parquet"
    identity = {
        key: value
        for key, value in payload.items()
        if key not in {"manifest_id", "manifest_sha256"}
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    payload["manifest_id"] = digest[:24]
    payload["manifest_sha256"] = digest
    (tmp_path / "features.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="unsafe"):
        store.read_manifest(Path("features.json"))

    immutable_root = tmp_path / "immutable"
    _, immutable, _ = _write_store(immutable_root)
    with pytest.raises(RuntimeError, match="cannot be republished"):
        ParquetR2FeatureStore(
            immutable_root,
            FixedClock(datetime(2026, 7, 29, tzinfo=UTC)),
            chunk_rows=2,
        ).write(
            Path("features.json"),
            iter(_feature_rows(6, immutable.feature_set_id, immutable.feature_schema)),
            feature_set_name=immutable.feature_set_name,
            feature_set_id=immutable.feature_set_id,
            feature_schema=immutable.feature_schema,
            observation_dataset_id=immutable.observation_dataset_id,
            panel_dataset_id=immutable.panel_dataset_id,
            target_dataset_id=immutable.target_dataset_id,
            fold_dataset_id=immutable.fold_dataset_id,
            experiment_configuration_id=immutable.experiment_configuration_id,
            evidence_class=immutable.evidence_class,
            holdout_excluded=True,
            application_version="test",
            image_identity="changed-image",
        )
    expected_data_files = {Path(chunk.data_file).name for chunk in immutable.chunks}
    expected_lineage_files = {Path(chunk.lineage_file).name for chunk in immutable.chunks}
    data_directory = (immutable_root / immutable.chunks[0].data_file).parent
    lineage_directory = (immutable_root / immutable.chunks[0].lineage_file).parent
    assert {path.name for path in data_directory.glob("*.parquet")} == expected_data_files
    assert {path.name for path in lineage_directory.glob("*.parquet")} == expected_lineage_files
    assert (
        ParquetR2FeatureStore(
            immutable_root,
            FixedClock(datetime(2026, 7, 29, tzinfo=UTC)),
            chunk_rows=2,
        ).verify(Path("features.json"))
        == immutable
    )

    failed_root = tmp_path / "failed"
    failed_store = ParquetR2FeatureStore(failed_root, FixedClock(), chunk_rows=2)
    schema = immutable.feature_schema
    set_identity = immutable.feature_set_id
    calls = 0
    original = ParquetR2FeatureStore._publish_chunk

    def fail_second(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected chunk failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(ParquetR2FeatureStore, "_publish_chunk", fail_second)
    with pytest.raises(OSError, match="injected"):
        failed_store.write(
            Path("features.json"),
            iter(_feature_rows(5, set_identity, schema)),
            feature_set_name="fixture",
            feature_set_id=set_identity,
            feature_schema=schema,
            observation_dataset_id="a" * 64,
            panel_dataset_id="b" * 64,
            target_dataset_id="d" * 64,
            fold_dataset_id="e" * 64,
            experiment_configuration_id="c" * 64,
            evidence_class=EvidenceClass.IMPLEMENTATION,
            holdout_excluded=True,
            application_version="test",
            image_identity="image",
        )
    assert not (failed_root / "features.json").exists()


def test_streamed_persisted_verifier_rejects_wrong_set_and_value(tmp_path: Path) -> None:
    config = experiment()
    foundation = _replay_foundation(config)
    dataset = materialise_r2_features(foundation, config, feature_set_name="L0")
    schema = dataset.feature_schema
    store = ParquetR2FeatureStore(tmp_path, FixedClock(), chunk_rows=1)
    manifest = store.write(
        Path("features.json"),
        iter(dataset.rows),
        feature_set_name="L0",
        feature_set_id=dataset.feature_set_id,
        feature_schema=schema,
        observation_dataset_id=dataset.observation_dataset_id,
        panel_dataset_id=dataset.panel_dataset_id,
        target_dataset_id=dataset.target_dataset_id,
        fold_dataset_id=dataset.fold_dataset_id,
        experiment_configuration_id=dataset.experiment_configuration_id,
        evidence_class=dataset.evidence_class,
        holdout_excluded=True,
        application_version="test",
        image_identity="image",
    )
    verify_raw_feature_manifest_bindings(
        manifest,
        foundation,
        config,
        feature_set_name="L0",
    )
    assert (
        verify_raw_feature_rows(
            store.iter_rows(Path("features.json")),
            foundation,
            config,
            feature_set_name="L0",
        )
        == 1
    )
    with pytest.raises(ValueError, match="feature-set"):
        verify_raw_feature_manifest_bindings(
            manifest,
            foundation,
            config,
            feature_set_name="L1",
        )
    row = dataset.rows[0]
    changed_values = list(row.values)
    index = next(index for index, value in enumerate(changed_values) if value.value is not None)
    assert changed_values[index].value is not None
    changed_values[index] = replace(
        changed_values[index], value=cast(float, changed_values[index].value) + 1
    )
    with pytest.raises(ValueError, match="causal replay"):
        verify_raw_feature_rows(
            iter((replace(row, values=tuple(changed_values)),)),
            foundation,
            config,
            feature_set_name="L0",
        )


@pytest.mark.asyncio
async def test_cli_feature_build_and_verify_use_verified_foundation_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = experiment()
    foundation = _replay_foundation(config)
    restore_bundle = AsyncMock(return_value=foundation)
    monkeypatch.setattr(cli, "restore_authenticated_foundation_bundle", restore_bundle)
    verify_bundle = AsyncMock(return_value=foundation)
    monkeypatch.setattr(cli, "_verify_foundation_bundle_runtime", verify_bundle, raising=False)
    monkeypatch.setattr(cli, "load_r2_experiment", lambda _: config)
    settings = Settings(
        research_root=tmp_path,
        image="test-image",
    )
    await cli._materialise_r2_features(
        settings,
        FixedClock(),
        foundation_bundle_path=Path("foundation.json"),
        foundation_receipt_path=Path("foundation-receipt.json"),
        experiment_path=Path("experiment.json"),
        feature_set_name="L0",
        output_path=Path("features.json"),
    )
    built = json.loads(capsys.readouterr().out)
    assert built["contract"] == "qtrad-r2-feature-parquet-v2"
    assert built["rows"] == 1
    await cli._verify_persisted_r2_features(
        settings,
        FixedClock(),
        foundation_bundle_path=Path("foundation.json"),
        foundation_receipt_path=Path("foundation-receipt.json"),
        experiment_path=Path("experiment.json"),
        feature_set_name="L0",
        manifest_path=Path("features.json"),
    )
    verified = json.loads(capsys.readouterr().out)
    assert verified == built
    assert restore_bundle.await_count == 2
    assert verify_bundle.await_count == 0


def test_parquet_feature_datasets_share_content_store_without_invalidating_evidence(
    tmp_path: Path,
) -> None:
    first_store, first, _ = _write_store(tmp_path, count=4, chunk_rows=2)
    second_store, second, _ = _write_store(
        tmp_path,
        manifest_name="features-second.json",
        count=6,
        chunk_rows=2,
    )

    assert first_store.verify(Path("features.json")) == first
    assert second_store.verify(Path("features-second.json")) == second
    assert first.chunks[0].data_file == second.chunks[0].data_file


def test_gap_known_by_cutoff_excludes_source_inactive_closures() -> None:
    from qtrad.domain.foundation import PanelAuditDisposition

    decision = datetime(2026, 2, 1, 12, tzinfo=UTC)
    panel = cast(
        PanelRow,
        SimpleNamespace(
            instrument_id=experiment().target_instruments[0],
            latest_feature_bar_end=decision,
            feature_data_asof=decision,
            audit_disposition=PanelAuditDisposition.SOURCE_NOT_ACTIVE,
        ),
    )
    foundation = _minimal_foundation(decision, decision + timedelta(minutes=1))
    inactive, _ = _calculate(
        "gap_known_by_cutoff",
        panel,
        {},
        experiment(),
        foundation,
        _RowCache(),
    )
    known_gap, _ = _calculate(
        "gap_known_by_cutoff",
        cast(
            PanelRow,
            SimpleNamespace(
                instrument_id=panel.instrument_id,
                latest_feature_bar_end=panel.latest_feature_bar_end,
                feature_data_asof=panel.feature_data_asof,
                audit_disposition=PanelAuditDisposition.RECORDED_GAP_KNOWN_BY_CUTOFF,
            ),
        ),
        {},
        experiment(),
        foundation,
        _RowCache(),
    )

    assert inactive == 0.0
    assert known_gap == 1.0
