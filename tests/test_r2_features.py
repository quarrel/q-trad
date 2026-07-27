from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from qtrad.application.r2_features import (
    FeatureLineageError,
    select_current_cutoff,
    verify_raw_feature_dataset,
)
from qtrad.domain.identifiers import ProviderListingId
from qtrad.domain.market_data import PriceBasis
from qtrad.domain.r2_features import (
    R2FeatureDataset,
    RawFeatureRow,
    RawFeatureValue,
    feature_registry,
)
from qtrad.domain.r2_readiness import EvidenceClass
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
    assert (
        select_current_cutoff(
            dataset.rows,
            instrument_id="fx:aud-usd",
            basis=PriceBasis.MID,
            interval_start=start,
            latest_feature_bar_end=start + timedelta(minutes=1),
            feature_data_asof=start + timedelta(minutes=1, seconds=30),
        )
        is not None
    )


def test_current_cutoff_rejects_ambiguous_sources() -> None:
    start = datetime(2026, 7, 1, 12, tzinfo=UTC)
    left = _candidate(
        _bar(start),
        position=1,
        received=start + timedelta(minutes=1),
        persisted=start + timedelta(minutes=1),
    )
    other_bar = replace(_bar(start), source_listing_id=ProviderListingId("other", "demo", "AUDUSD"))
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


def test_feature_verifier_rejects_wrong_observation_child() -> None:
    config = experiment()
    start = datetime(2026, 7, 1, 12, tzinfo=UTC)
    observations = _dataset(
        (
            _candidate(
                _bar(start),
                position=1,
                received=start + timedelta(minutes=1),
                persisted=start + timedelta(minutes=1),
            ),
        )
    )
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
    with pytest.raises(ValueError, match="observation identity"):
        verify_raw_feature_dataset(dataset, observations, config)
