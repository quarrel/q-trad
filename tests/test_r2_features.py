from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from math import log
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from qtrad.application.r2_features import (
    FeatureLineageError,
    R2FoundationInputs,
    _calculate,
    _context,
    _missing_fraction,
    _return,
    _rolling,
    _spread,
    materialise_r2_features,
    select_current_cutoff,
    verify_raw_feature_dataset,
)
from qtrad.domain.foundation import PanelRow, PanelStatus
from qtrad.domain.identifiers import ProviderListingId
from qtrad.domain.market_data import PriceBasis
from qtrad.domain.r2_features import (
    FeatureDefinition,
    R2FeatureDataset,
    RawFeatureRow,
    RawFeatureValue,
    feature_registry,
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
from qtrad.runtime.r2_features import load_r2_feature_dataset, write_r2_feature_dataset
from tests.test_r1_observations import _bar, _candidate, _dataset
from tests.test_r2_readiness import experiment


def test_feature_registry_is_deterministic_and_rows_follow_schema() -> None:
    config = experiment()
    schema = feature_registry(config)
    assert schema == feature_registry(config)
    assert len({item.name for item in schema}) == len(schema)
    feature_set = config.feature_sets[0]
    names = tuple(item.name for item in schema if item.family in feature_set.families)
    row = RawFeatureRow(
        target_instrument_id=config.target_instruments[0],
        decision_time=datetime(2026, 1, 1, tzinfo=UTC),
        feature_data_asof=datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
        latest_feature_bar_end=datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
        feature_set_id="fixture-set",
        values=tuple(RawFeatureValue(name, None) for name in names),
    )
    assert row.semantic_key()[0] == config.target_instruments[0]


def test_feature_dataset_rejects_non_schema_order() -> None:
    config = experiment()
    schema = feature_registry(config)
    names = tuple(item.name for item in schema if item.family in config.feature_sets[0].families)
    row = RawFeatureRow(
        target_instrument_id=config.target_instruments[0],
        decision_time=datetime(2026, 1, 1, tzinfo=UTC),
        feature_data_asof=datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
        latest_feature_bar_end=datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
        feature_set_id="fixture-set",
        values=tuple(RawFeatureValue(name, None) for name in reversed(names)),
    )
    with pytest.raises(ValueError, match="feature row order"):
        R2FeatureDataset.create(
            (row,),
            feature_schema=tuple(
                item for item in schema if item.family in config.feature_sets[0].families
            ),
            observation_dataset_id=config.observation_dataset_id,
            panel_dataset_id=config.panel_dataset_id,
            target_dataset_id=config.target_dataset_id,
            fold_dataset_id=config.fold_dataset_id,
            experiment_configuration_id=config.configuration_id,
            evidence_class=EvidenceClass.IMPLEMENTATION,
        )


def test_current_cutoff_uses_latest_visible_revision() -> None:
    start = datetime(2026, 7, 1, 12, tzinfo=UTC)
    first = _candidate(
        _bar(start, close="1.1"),
        position=1,
        received=start + timedelta(minutes=1),
        persisted=start + timedelta(minutes=1),
    )
    correction = _candidate(
        _bar(start, close="1.2", revision=2),
        position=2,
        received=start + timedelta(minutes=2),
        persisted=start + timedelta(minutes=2),
        stream_version=2,
    )
    dataset = _dataset((first, correction))
    selected = select_current_cutoff(
        dataset.rows,
        instrument_id="fx:aud-usd",
        basis=PriceBasis.MID,
        interval_start=start,
        latest_feature_bar_end=start + timedelta(minutes=1),
        feature_data_asof=start + timedelta(minutes=3),
    )
    assert selected is not None
    assert selected.revision == 2
    early = select_current_cutoff(
        dataset.rows,
        instrument_id="fx:aud-usd",
        basis=PriceBasis.MID,
        interval_start=start,
        latest_feature_bar_end=start + timedelta(minutes=1),
        feature_data_asof=start + timedelta(minutes=1, seconds=30),
    )
    assert early is not None
    assert early.revision == 1


def test_current_cutoff_rejects_ambiguous_sources() -> None:
    start = datetime(2026, 7, 1, 12, tzinfo=UTC)
    left = _candidate(
        _bar(start),
        position=1,
        received=start + timedelta(minutes=1),
        persisted=start + timedelta(minutes=1),
    )
    other_bar = replace(
        _bar(start),
        source_listing_id=ProviderListingId("other", "demo", "AUDUSD"),
    )
    other = _candidate(
        other_bar,
        position=2,
        received=start + timedelta(minutes=1),
        persisted=start + timedelta(minutes=1),
        stream_version=2,
    )
    dataset = _dataset((left, other))
    with pytest.raises(FeatureLineageError, match="ambiguous source"):
        select_current_cutoff(
            dataset.rows,
            instrument_id="fx:aud-usd",
            basis=PriceBasis.MID,
            interval_start=start,
            latest_feature_bar_end=start + timedelta(minutes=1),
            feature_data_asof=start + timedelta(minutes=2),
        )


def test_exact_and_rolling_features_preserve_endpoints_and_reject_gaps() -> None:
    start = datetime(2026, 7, 1, 12, tzinfo=UTC)
    consecutive = _dataset(
        tuple(
            _candidate(
                _bar(start + timedelta(minutes=offset), close=str(1.0 + offset / 100)),
                position=offset + 1,
                received=start + timedelta(minutes=offset + 1),
                persisted=start + timedelta(minutes=offset + 1),
            )
            for offset in (0, 1)
        )
    ).rows
    value, lineage = _return(
        "return_60s",
        consecutive,
        start + timedelta(minutes=2),
        start + timedelta(minutes=3),
    )
    assert value == pytest.approx(log(1.01))
    assert len(lineage) == 2
    cross_source = (
        consecutive[0],
        replace(consecutive[1], source_external_id="OTHER"),
    )
    with pytest.raises(FeatureLineageError, match="cross source lineage"):
        _return(
            "return_60s",
            cross_source,
            start + timedelta(minutes=2),
            start + timedelta(minutes=3),
        )

    gapped = _dataset(
        tuple(
            _candidate(
                _bar(start + timedelta(minutes=offset), close=str(1.0 + offset / 100)),
                position=offset + 1,
                received=start + timedelta(minutes=offset + 1),
                persisted=start + timedelta(minutes=offset + 1),
            )
            for offset in (0, 2)
        )
    ).rows
    foundation = SimpleNamespace(
        configuration=SimpleNamespace(grid_resolution=timedelta(minutes=1)),
        source_active_intervals={"fx:aud-usd": ((start, start + timedelta(minutes=3)),)},
    )
    rolling, _ = _rolling(
        "realised_std_180s",
        "fx:aud-usd",
        gapped,
        start + timedelta(minutes=3),
        start + timedelta(minutes=4),
        cast(R2FoundationInputs, foundation),
        experiment(),
    )
    assert rolling is None


def test_rolling_values_are_causal_order_independent_and_counted() -> None:
    start = datetime(2026, 7, 1, 12, tzinfo=UTC)
    rows = _dataset(
        tuple(
            _candidate(
                _bar(start + timedelta(minutes=offset), close=str(2**offset)),
                position=offset + 1,
                received=start + timedelta(minutes=offset + 1),
                persisted=start + timedelta(minutes=offset + 1),
            )
            for offset in range(5)
        )
    ).rows
    foundation = cast(
        R2FoundationInputs,
        SimpleNamespace(
            configuration=SimpleNamespace(grid_resolution=timedelta(minutes=1)),
            source_active_intervals={"fx:aud-usd": ((start, start + timedelta(minutes=5)),)},
        ),
    )
    args = (
        "mean_absolute_return_300s",
        "fx:aud-usd",
        rows,
        start + timedelta(minutes=5),
        start + timedelta(minutes=6),
        foundation,
        experiment(),
    )
    value, lineage = _rolling(*args)
    reversed_value, reversed_lineage = _rolling(
        args[0],
        args[1],
        tuple(reversed(rows)),
        *args[3:],
    )
    assert value == pytest.approx(log(2))
    assert reversed_value == value
    assert reversed_lineage == lineage
    assert len(lineage) == 5
    cross_source = (*rows[:-1], replace(rows[-1], source_external_id="OTHER"))
    with pytest.raises(FeatureLineageError, match="crosses source lineage"):
        _rolling(
            "mean_absolute_return_300s",
            "fx:aud-usd",
            cross_source,
            start + timedelta(minutes=5),
            start + timedelta(minutes=6),
            foundation,
            experiment(),
        )

    coverage, empty_lineage = _rolling(
        "window_coverage_300s",
        "fx:aud-usd",
        (),
        start + timedelta(minutes=5),
        start + timedelta(minutes=6),
        foundation,
        experiment(),
    )
    assert coverage == 0.0
    assert empty_lineage == ()


def test_rolling_window_rejects_ambiguous_lineage() -> None:
    start = datetime(2026, 7, 1, 12, tzinfo=UTC)
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
    foundation = cast(
        R2FoundationInputs,
        SimpleNamespace(
            configuration=SimpleNamespace(grid_resolution=timedelta(minutes=1)),
            source_active_intervals={"fx:aud-usd": ((start, start + timedelta(minutes=1)),)},
        ),
    )
    with pytest.raises(FeatureLineageError, match="ambiguous source"):
        _rolling(
            "mean_log_range_60s",
            "fx:aud-usd",
            _dataset((left, other)).rows,
            start + timedelta(minutes=1),
            start + timedelta(minutes=2),
            foundation,
            experiment(),
        )


def test_missing_fraction_uses_cutoff_window_and_source_activity() -> None:
    start = datetime(2026, 7, 1, 12, tzinfo=UTC)
    rows = _dataset(
        tuple(
            _candidate(
                _bar(start + timedelta(minutes=offset)),
                position=offset + 1,
                received=(
                    start + timedelta(minutes=6)
                    if offset == 4
                    else start + timedelta(minutes=offset + 1)
                ),
                persisted=(
                    start + timedelta(minutes=6)
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
            feature_data_asof=start + timedelta(minutes=5, seconds=30),
        ),
    )
    foundation = cast(
        R2FoundationInputs,
        SimpleNamespace(
            configuration=SimpleNamespace(grid_resolution=timedelta(minutes=1)),
            source_active_intervals={"fx:aud-usd": ((start, start + timedelta(minutes=5)),)},
        ),
    )
    value, lineage = _missing_fraction(rows, panel, foundation, experiment())
    assert value == pytest.approx(0.2)
    assert len(lineage) == 4
    partial_foundation = cast(
        R2FoundationInputs,
        SimpleNamespace(
            configuration=SimpleNamespace(grid_resolution=timedelta(minutes=1)),
            source_active_intervals={
                "fx:aud-usd": ((start + timedelta(seconds=30), start + timedelta(minutes=5)),)
            },
        ),
    )
    partial_value, partial_lineage = _missing_fraction(
        rows, panel, partial_foundation, experiment()
    )
    assert partial_value == pytest.approx(0.25)
    assert len(partial_lineage) == 3

    closed_foundation = cast(
        R2FoundationInputs,
        SimpleNamespace(
            configuration=SimpleNamespace(grid_resolution=timedelta(minutes=1)),
            source_active_intervals={"fx:aud-usd": ((start, start + timedelta(minutes=3)),)},
        ),
    )
    closed_value, _ = _missing_fraction(rows[:3], panel, closed_foundation, experiment())
    assert closed_value == 0.0


def test_cross_market_missing_count_excludes_inactive_sources() -> None:
    start = datetime(2026, 7, 1, 12, tzinfo=UTC)
    decision_time = start + timedelta(minutes=6)
    available = replace(
        _dataset(
            (
                _candidate(
                    _bar(start + timedelta(minutes=4)),
                    position=1,
                    received=start + timedelta(minutes=5),
                    persisted=start + timedelta(minutes=5),
                ),
            )
        ).rows[0],
        instrument_id="index:target-2",
        stream_id="market-bar:index:target-2:MID",
    )
    panel = cast(
        PanelRow,
        SimpleNamespace(
            instrument_id="index:target-1",
            decision_time=decision_time,
            latest_feature_bar_end=start + timedelta(minutes=5),
            feature_data_asof=start + timedelta(minutes=5, seconds=30),
        ),
    )
    panels = cast(
        tuple[PanelRow, ...],
        tuple(
            SimpleNamespace(instrument_id=instrument, decision_time=decision_time)
            for instrument in (
                "index:target-1",
                "index:target-2",
                "index:target-3",
            )
        ),
    )
    foundation = cast(
        R2FoundationInputs,
        SimpleNamespace(
            configuration=SimpleNamespace(grid_resolution=timedelta(minutes=1)),
            source_active_intervals={
                "index:target-1": ((start, start + timedelta(minutes=5)),),
                "index:target-2": ((start, start + timedelta(minutes=5)),),
                "index:target-3": ((start, start + timedelta(minutes=4)),),
            },
        ),
    )
    indexed = {("index:target-2", PriceBasis.MID): (available,)}
    active_count, _ = _calculate(
        "cross_market_source_active_count",
        panel,
        (),
        indexed,
        panels,
        experiment(),
        foundation,
    )
    missing_count, lineage = _calculate(
        "cross_market_missing_count",
        panel,
        (),
        indexed,
        panels,
        experiment(),
        foundation,
    )
    assert active_count == 2.0
    assert missing_count == 1.0
    assert lineage == (str(available.event_id),)


def test_pooled_context_uses_market_groups_and_vix_context() -> None:
    start = datetime(2026, 7, 1, 12, tzinfo=UTC)
    base = _dataset(
        (
            _candidate(
                _bar(start, close="1.0"),
                position=1,
                received=start + timedelta(minutes=1),
                persisted=start + timedelta(minutes=1),
            ),
            _candidate(
                _bar(start + timedelta(minutes=1), close="1.1"),
                position=2,
                received=start + timedelta(minutes=2),
                persisted=start + timedelta(minutes=2),
            ),
        )
    ).rows
    peers = []
    for instrument, scale in (
        ("index:target-1", "100"),
        ("index:target-2", "1.2"),
        ("index:target-3", "1.3"),
        ("index:volatility", "1.4"),
    ):
        peers.extend(
            replace(
                row,
                instrument_id=instrument,
                stream_id=f"market-bar:{instrument}:MID",
                close=Decimal("1.0") if row.interval_start == start else Decimal(scale),
            )
            for row in base
        )
    indexed = {
        (instrument, PriceBasis.MID): tuple(row for row in peers if row.instrument_id == instrument)
        for instrument in (
            "index:target-1",
            "index:target-2",
            "index:target-3",
            "index:volatility",
        )
    }
    panel = SimpleNamespace(
        instrument_id="index:target-1",
        latest_feature_bar_end=start + timedelta(minutes=2),
        feature_data_asof=start + timedelta(minutes=3),
    )
    config = experiment()
    group_value, _ = _context(
        "loo_market_group_mean_return_60s",
        cast("PanelRow", panel),
        indexed,
        (),
        config,
    )
    global_value, _ = _context(
        "loo_mean_return_60s",
        cast("PanelRow", panel),
        indexed,
        (),
        config,
    )
    median_value, _ = _context(
        "loo_median_return_60s",
        cast("PanelRow", panel),
        indexed,
        (),
        config,
    )
    vix_value, _ = _context(
        "vix_context_return_60s",
        cast("PanelRow", panel),
        indexed,
        (),
        config,
    )
    assert group_value == pytest.approx(log(1.2))
    assert global_value == pytest.approx((log(1.2) + log(1.3)) / 2)
    assert median_value == pytest.approx((log(1.2) + log(1.3)) / 2)
    assert vix_value == pytest.approx(log(1.4))


def test_spread_features_use_aligned_sides_and_declared_window() -> None:
    start = datetime(2026, 7, 1, 12, tzinfo=UTC)
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
                (
                    PriceBasis.BID,
                    Decimal("98") if offset == 4 else Decimal("99"),
                ),
                (
                    PriceBasis.ASK,
                    Decimal("102") if offset == 4 else Decimal("101"),
                ),
                (PriceBasis.MID, Decimal("100")),
            )
        )
    )
    rows = _dataset(candidates).rows
    indexed = {
        ("fx:aud-usd", basis): tuple(row for row in rows if row.basis is basis)
        for basis in PriceBasis
    }
    panel = cast(
        PanelRow,
        SimpleNamespace(
            instrument_id="fx:aud-usd",
            latest_feature_bar_end=start + timedelta(minutes=5),
            feature_data_asof=start + timedelta(minutes=6),
        ),
    )
    foundation = cast(
        R2FoundationInputs,
        SimpleNamespace(
            configuration=SimpleNamespace(grid_resolution=timedelta(minutes=1)),
            source_active_intervals={"fx:aud-usd": ((start, start + timedelta(minutes=5)),)},
        ),
    )
    config = experiment()
    close_spread, _ = _spread("close_spread", panel, indexed, foundation, config)
    spread_fraction, _ = _spread("spread_fraction", panel, indexed, foundation, config)
    spread_bps, _ = _spread("spread_bps", panel, indexed, foundation, config)
    rolling_mean, _ = _spread("rolling_spread_mean", panel, indexed, foundation, config)
    rolling_change, _ = _spread("rolling_spread_change", panel, indexed, foundation, config)
    coverage, _ = _spread("spread_coverage", panel, indexed, foundation, config)
    assert close_spread == 4.0
    assert spread_fraction == pytest.approx(0.04)
    assert spread_bps == pytest.approx(400.0)
    assert rolling_mean == pytest.approx(0.024)
    assert rolling_change == pytest.approx(0.02)
    assert coverage == 1.0
    cross_source_rows = tuple(
        replace(row, source_external_id="OTHER")
        if row.interval_end == start + timedelta(minutes=5)
        else row
        for row in rows
    )
    cross_source_indexed = {
        ("fx:aud-usd", basis): tuple(row for row in cross_source_rows if row.basis is basis)
        for basis in PriceBasis
    }
    with pytest.raises(FeatureLineageError, match="crosses source lineage"):
        _spread("rolling_spread_mean", panel, cross_source_indexed, foundation, config)

    misaligned = dict(indexed)
    misaligned[("fx:aud-usd", PriceBasis.ASK)] = tuple(
        replace(row, source_external_id="OTHER") for row in indexed[("fx:aud-usd", PriceBasis.ASK)]
    )
    invalid, _ = _spread("close_spread", panel, misaligned, foundation, config)
    assert invalid is None


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
    local = (
        FeatureFamily.LOCAL_RETURNS,
        FeatureFamily.TIME_AVAILABILITY,
    )
    feature_sets = [FeatureSet("L0", local)]
    local += (FeatureFamily.LOCAL_VOLATILITY_RANGE,)
    feature_sets.append(FeatureSet("L1", local))
    if decisions[FeatureFamily.SPREAD].state is FeatureEligibility.ELIGIBLE:
        local += (FeatureFamily.SPREAD,)
        feature_sets.append(FeatureSet("L2", local))
    if decisions[FeatureFamily.QUOTE_IMBALANCE].state is FeatureEligibility.ELIGIBLE:
        local += (FeatureFamily.QUOTE_IMBALANCE,)
        feature_sets.append(FeatureSet("L3", local))
    feature_sets.extend(
        (
            FeatureSet("P0", local),
            FeatureSet("P1", (*local, FeatureFamily.POOLED_CROSS_ASSET)),
        )
    )
    return replace(
        config,
        feature_sets=tuple(feature_sets),
        feature_eligibility=decisions,
    )


def test_optional_family_eligibility_is_implemented_or_rejected() -> None:
    spread = _with_eligible_family(experiment(), FeatureFamily.SPREAD)
    dataset = materialise_r2_features(_foundation(spread), spread, feature_set_name="L2")
    assert {
        item.name for item in dataset.feature_schema if item.family is FeatureFamily.SPREAD
    } == {
        "close_spread",
        "spread_fraction",
        "spread_bps",
        "rolling_spread_mean",
        "rolling_spread_change",
        "spread_coverage",
    }

    imbalance = _with_eligible_family(
        experiment(),
        FeatureFamily.QUOTE_IMBALANCE,
    )
    with pytest.raises(ValueError, match="validated quote-size source"):
        materialise_r2_features(_foundation(imbalance), imbalance, feature_set_name="L3")


def _foundation(
    config: R2ExperimentConfig,
    overrides: Mapping[str, str] | None = None,
    *,
    observation_rows: tuple[ObservationRow, ...] = (),
    panel_rows: tuple[PanelRow, ...] = (),
    source_active_intervals: Mapping[str, tuple[tuple[datetime, datetime], ...]] | None = None,
) -> R2FoundationInputs:
    values = overrides or {}
    observation_id = values.get("observation_id", config.observation_dataset_id)
    foundation_id = values.get("foundation_id", config.foundation_configuration_id)
    target_id = values.get("target_id", config.target_dataset_id)
    active_intervals: dict[str, tuple[tuple[datetime, datetime], ...]] = {
        instrument: () for instrument in config.ordered_instruments
    }
    if source_active_intervals is not None:
        active_intervals.update(source_active_intervals)
    return cast(
        R2FoundationInputs,
        SimpleNamespace(
            bundle_id=values.get("bundle_id", config.r1_bundle_id),
            configuration=SimpleNamespace(
                configuration_id=foundation_id,
                observation_dataset_id=observation_id,
                ordered_instruments=config.ordered_instruments,
                instrument_roles=config.instrument_roles,
                grid_resolution=timedelta(minutes=1),
            ),
            observations=SimpleNamespace(dataset_id=observation_id, rows=observation_rows),
            panel=SimpleNamespace(
                dataset_id=values.get("panel_id", config.panel_dataset_id),
                observation_dataset_id=values.get("panel_observation_id", observation_id),
                foundation_configuration_id=values.get("panel_foundation_id", foundation_id),
                rows=panel_rows,
            ),
            targets=SimpleNamespace(
                dataset_id=target_id,
                observation_dataset_id=values.get("target_observation_id", observation_id),
                foundation_configuration_id=values.get("target_foundation_id", foundation_id),
            ),
            folds=SimpleNamespace(
                dataset_id=values.get("fold_id", config.fold_dataset_id),
                foundation_configuration_id=values.get("fold_foundation_id", foundation_id),
                target_dataset_id=values.get("fold_target_id", target_id),
            ),
            source_active_intervals=active_intervals,
        ),
    )


def _replay_foundation(
    config: R2ExperimentConfig,
    *,
    include_holdout: bool = False,
) -> R2FoundationInputs:
    decision_time = config.holdout_range[0] - timedelta(days=1)
    latest_feature_bar_end = decision_time - timedelta(minutes=1)
    window_start = latest_feature_bar_end - timedelta(minutes=5)
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
                    _bar(window_start + timedelta(minutes=offset), close=str(offset + 1)),
                    position=offset + 1,
                    received=window_start + timedelta(minutes=offset + 1),
                    persisted=window_start + timedelta(minutes=offset + 1),
                )
                for offset in range(5)
            )
        ).rows
    )
    current = rows[-1]
    panel = PanelRow(
        decision_time=decision_time,
        instrument_id=config.target_instruments[0],
        basis=PriceBasis.MID,
        feature_data_asof=decision_time,
        latest_feature_bar_end=latest_feature_bar_end,
        status=PanelStatus.OBSERVED,
        audit_disposition=None,
        selected_event_id=current.event_id,
        selected_stream_version=current.stream_version,
        selected_global_position=current.global_position,
        selected_availability_time=current.persisted_at,
        selected_revision=current.revision,
        interval_start=current.interval_start,
        interval_end=current.interval_end,
        open=current.open,
        high=current.high,
        low=current.low,
        close=current.close,
        sample_count=current.sample_count,
        quality=current.quality,
    )
    panels = (panel,)
    if include_holdout:
        panels = (*panels, replace(panel, decision_time=config.holdout_range[0]))
    return _foundation(
        config,
        observation_rows=rows,
        panel_rows=panels,
        source_active_intervals={
            config.target_instruments[0]: ((window_start, latest_feature_bar_end),)
        },
    )


def test_materialisation_is_order_independent_and_excludes_holdout() -> None:
    config = experiment()
    foundation = _replay_foundation(config, include_holdout=True)
    expected = materialise_r2_features(
        foundation,
        config,
        feature_set_name="L0",
    )
    reversed_foundation = _foundation(
        config,
        observation_rows=tuple(reversed(foundation.observations.rows)),
        panel_rows=foundation.panel.rows,
        source_active_intervals=foundation.source_active_intervals,
    )
    reversed_dataset = materialise_r2_features(
        reversed_foundation,
        config,
        feature_set_name="L0",
    )
    assert len(expected.rows) == 1
    values = {item.name: item.value for item in expected.rows[0].values}
    assert values["return_60s"] == pytest.approx(log(5 / 4))
    assert values["return_60s_available"] == 1.0
    assert values["return_300s"] is None
    assert values["return_300s_available"] == 0.0
    assert reversed_dataset == expected


def test_independent_verifier_rejects_values_lineage_schema_and_holdout() -> None:
    config = experiment()
    foundation = _replay_foundation(config)
    dataset = materialise_r2_features(foundation, config, feature_set_name="L0")
    verify_raw_feature_dataset(dataset, foundation, config)
    row = dataset.rows[0]

    value_index = next(index for index, value in enumerate(row.values) if value.value is not None)
    changed_values = list(row.values)
    original_value = changed_values[value_index].value
    assert original_value is not None
    changed_values[value_index] = replace(
        changed_values[value_index],
        value=original_value + 1.0,
    )
    changed_row = replace(row, values=tuple(changed_values))
    changed_dataset = R2FeatureDataset.create(
        (changed_row,),
        feature_schema=dataset.feature_schema,
        observation_dataset_id=dataset.observation_dataset_id,
        panel_dataset_id=dataset.panel_dataset_id,
        target_dataset_id=dataset.target_dataset_id,
        fold_dataset_id=dataset.fold_dataset_id,
        experiment_configuration_id=dataset.experiment_configuration_id,
        evidence_class=dataset.evidence_class,
    )
    with pytest.raises(ValueError, match="causal replay"):
        verify_raw_feature_dataset(changed_dataset, foundation, config)

    lineage_index = next(index for index, value in enumerate(row.values) if value.source_event_ids)
    changed_lineage = list(row.values)
    changed_lineage[lineage_index] = replace(
        changed_lineage[lineage_index],
        source_event_ids=(),
    )
    lineage_dataset = R2FeatureDataset.create(
        (replace(row, values=tuple(changed_lineage)),),
        feature_schema=dataset.feature_schema,
        observation_dataset_id=dataset.observation_dataset_id,
        panel_dataset_id=dataset.panel_dataset_id,
        target_dataset_id=dataset.target_dataset_id,
        fold_dataset_id=dataset.fold_dataset_id,
        experiment_configuration_id=dataset.experiment_configuration_id,
        evidence_class=dataset.evidence_class,
    )
    with pytest.raises(ValueError, match="causal replay"):
        verify_raw_feature_dataset(lineage_dataset, foundation, config)

    reduced_schema = dataset.feature_schema[:-1]
    reduced_row = replace(
        row,
        feature_set_id=feature_set_id(
            config.configuration_id,
            "L0",
            reduced_schema,
        ),
        values=row.values[:-1],
    )
    schema_dataset = R2FeatureDataset.create(
        (reduced_row,),
        feature_schema=reduced_schema,
        observation_dataset_id=dataset.observation_dataset_id,
        panel_dataset_id=dataset.panel_dataset_id,
        target_dataset_id=dataset.target_dataset_id,
        fold_dataset_id=dataset.fold_dataset_id,
        experiment_configuration_id=dataset.experiment_configuration_id,
        evidence_class=dataset.evidence_class,
    )
    with pytest.raises(ValueError, match="causal replay"):
        verify_raw_feature_dataset(schema_dataset, foundation, config)

    holdout_row = replace(row, decision_time=config.holdout_range[0])
    holdout_dataset = R2FeatureDataset.create(
        (row, holdout_row),
        feature_schema=dataset.feature_schema,
        observation_dataset_id=dataset.observation_dataset_id,
        panel_dataset_id=dataset.panel_dataset_id,
        target_dataset_id=dataset.target_dataset_id,
        fold_dataset_id=dataset.fold_dataset_id,
        experiment_configuration_id=dataset.experiment_configuration_id,
        evidence_class=dataset.evidence_class,
    )
    with pytest.raises(ValueError, match="causal replay"):
        verify_raw_feature_dataset(holdout_dataset, foundation, config)


@pytest.mark.parametrize(
    "override",
    (
        {"bundle_id": "0" * 64},
        {"observation_id": "0" * 64},
        {"foundation_id": "0" * 64},
        {"panel_id": "0" * 64},
        {"panel_observation_id": "0" * 64},
        {"panel_foundation_id": "0" * 64},
        {"target_id": "0" * 64},
        {"target_observation_id": "0" * 64},
        {"target_foundation_id": "0" * 64},
        {"fold_id": "0" * 64},
        {"fold_target_id": "0" * 64},
        {"fold_foundation_id": "0" * 64},
    ),
)
def test_materialisation_rejects_cross_foundation_bindings(
    override: Mapping[str, str],
) -> None:
    config = experiment()
    with pytest.raises(ValueError, match="binding"):
        materialise_r2_features(_foundation(config, override), config)


def test_feature_verifier_rejects_wrong_observation_child() -> None:
    config = experiment()
    dataset = R2FeatureDataset.create(
        (),
        feature_schema=(),
        observation_dataset_id="a" * 64,
        panel_dataset_id=config.panel_dataset_id,
        target_dataset_id=config.target_dataset_id,
        fold_dataset_id=config.fold_dataset_id,
        experiment_configuration_id=config.configuration_id,
        evidence_class=EvidenceClass.IMPLEMENTATION,
    )
    with pytest.raises(ValueError, match="observation"):
        verify_raw_feature_dataset(dataset, _foundation(config), config)


def test_persisted_feature_dataset_round_trips_and_rejects_tampering(tmp_path: Path) -> None:
    config = experiment()
    schema = (FeatureDefinition("fixture", FeatureFamily.LOCAL_RETURNS),)
    row = RawFeatureRow(
        target_instrument_id=config.target_instruments[0],
        decision_time=datetime(2026, 1, 1, tzinfo=UTC),
        feature_data_asof=datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
        latest_feature_bar_end=datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
        feature_set_id="fixture-set",
        values=(RawFeatureValue("fixture", 0.25),),
    )
    dataset = R2FeatureDataset.create(
        (row,),
        feature_schema=schema,
        observation_dataset_id=config.observation_dataset_id,
        panel_dataset_id=config.panel_dataset_id,
        target_dataset_id=config.target_dataset_id,
        fold_dataset_id=config.fold_dataset_id,
        experiment_configuration_id=config.configuration_id,
        evidence_class=EvidenceClass.IMPLEMENTATION,
    )
    path = tmp_path / "features.json"
    write_r2_feature_dataset(path, dataset)
    assert load_r2_feature_dataset(path) == dataset
    duplicate_path = tmp_path / "duplicate.json"
    write_r2_feature_dataset(duplicate_path, dataset)
    contract_line = '  "contract": "qtrad-r2-features-v1",'
    duplicate_path.write_text(
        duplicate_path.read_text(encoding="utf-8").replace(
            contract_line,
            f"{contract_line}\n{contract_line}",
            1,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate field"):
        load_r2_feature_dataset(duplicate_path)
    path.write_text(path.read_text(encoding="utf-8").replace("0.25", "0.5"), encoding="utf-8")
    with pytest.raises(ValueError, match="dataset ID"):
        load_r2_feature_dataset(path)
