"""Focused R3.B ordered-risk contract checks."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from typing import cast

import numpy as np
import pytest
from sklearn.covariance import LedoitWolf  # type: ignore[reportMissingTypeStubs]

from qtrad.domain.risk import (
    ExposureMapping,
    FloatMatrix,
    RiskCaps,
    RiskEstimatorConfig,
    RiskObservation,
    RiskState,
    canonical_asset_order,
    estimate_ordered_risk_state,
)

ASSETS = ("asset:a", "asset:b", "asset:c")
CUTOFF = datetime(2026, 8, 1, 12, tzinfo=UTC)
CONFIG = RiskEstimatorConfig(
    horizon=timedelta(minutes=15),
    lookback=timedelta(hours=2),
    maximum_age=timedelta(hours=1),
    minimum_observations=3,
)
MAPPING = ExposureMapping(
    group_keys=("macro", "style"),
    group_exposure_matrix=((1.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    group_caps=(1.0, 1.0),
    currency_keys=("aud", "usd"),
    currency_exposure_matrix=((1.0, 0.0, 0.0), (0.0, 1.0, 1.0)),
    currency_caps=(1.0, 1.0),
)
CAPS = RiskCaps(
    asset_caps=(1.0, 1.0, 1.0),
    gross_cap=2.0,
    net_cap=1.0,
    concentration_cap=0.7,
    portfolio_risk_cap=1.0,
    group_caps=MAPPING.group_caps,
    currency_caps=MAPPING.currency_caps,
)


def _observations() -> tuple[RiskObservation, ...]:
    return tuple(
        RiskObservation(
            observed_at=CUTOFF - timedelta(minutes=20 * index),
            values=values,
        )
        for index, values in enumerate(
            (
                (0.001, -0.002, 0.003),
                (0.002, -0.001, 0.001),
                (-0.001, 0.003, -0.002),
                (0.003, 0.002, -0.001),
                (-0.002, -0.003, 0.002),
            )
        )
    )


def _estimate(observations: tuple[RiskObservation, ...]) -> RiskState:
    return estimate_ordered_risk_state(
        asset_order=ASSETS,
        observations=observations,
        as_of=CUTOFF,
        observation_cutoff=CUTOFF,
        config=CONFIG,
        exposure_mapping=MAPPING,
        caps=CAPS,
        provenance="test-r3-risk",
    )


def test_asset_order_and_observation_permutation_are_deterministic() -> None:
    observations = _observations()
    first = _estimate(observations)
    permuted = _estimate(tuple(reversed(observations)))
    assert first.asset_order == ASSETS
    assert first.covariance == permuted.covariance
    assert first.semantic_id == permuted.semantic_id
    assert first.closure_id == permuted.closure_id
    assert canonical_asset_order(("asset:c", "asset:a", "asset:b")) == ASSETS
    with pytest.raises(ValueError, match="asset_order"):
        replace(first, asset_order=("asset:b", "asset:a", "asset:c"))


def test_future_and_unavailable_observations_cannot_change_state() -> None:
    baseline = _estimate(_observations())
    future = RiskObservation(
        observed_at=CUTOFF + timedelta(minutes=1),
        available_at=CUTOFF + timedelta(minutes=1),
        values=(100.0, -100.0, 50.0),
    )
    delayed = RiskObservation(
        observed_at=CUTOFF - timedelta(minutes=1),
        available_at=CUTOFF + timedelta(minutes=1),
        values=(-100.0, 100.0, -50.0),
    )
    changed = _estimate((*_observations(), future, delayed))
    assert changed.covariance == baseline.covariance
    assert changed.sample_count == baseline.sample_count
    assert changed.excluded_observation_count == baseline.excluded_observation_count + 2


def test_missing_rows_are_explicitly_counted_and_nonfinite_values_fail_closed() -> None:
    missing = RiskObservation(
        observed_at=CUTOFF - timedelta(minutes=5),
        values=(0.0, None, 0.0),
    )
    state = _estimate((*_observations(), missing))
    assert state.missing_observation_count == 1
    assert state.sample_count == len(_observations())
    with pytest.raises(ValueError, match="finite"):
        RiskObservation(
            observed_at=CUTOFF,
            values=(float("nan"), 0.0, 0.0),
        )


def test_covariance_matches_independent_pinned_ledoit_wolf_calculation() -> None:
    state = _estimate(_observations())
    matrix = np.asarray([observation.values for observation in _observations()], dtype=np.float64)
    expected = LedoitWolf().fit(matrix).covariance_
    np.testing.assert_allclose(state.covariance, expected, rtol=0.0, atol=1e-15)
    np.testing.assert_allclose(
        state.portfolio_variance((0.2, -0.1, 0.05)),
        np.asarray((0.2, -0.1, 0.05)) @ expected @ np.asarray((0.2, -0.1, 0.05)),
        rtol=0.0,
        atol=1e-18,
    )
    assert state.shrinkage >= 0.0
    assert state.estimator_version == "qtrad-ledoit-wolf-pure-python-v1"


def test_mapping_exposures_and_caps_are_ordered_and_fail_closed() -> None:
    state = _estimate(_observations())
    position = (0.2, -0.1, 0.05)
    assert state.group_exposure(position) == pytest.approx((0.1, 0.05))
    assert state.currency_exposure(position) == pytest.approx((0.2, -0.05))
    assert state.gross_exposure(position) == pytest.approx(0.35)
    assert state.net_exposure(position) == pytest.approx(0.15)
    assert state.concentration(position) == pytest.approx(0.2 / 0.35)
    assert state.position_is_valid(position)
    assert not state.position_is_valid((0.9, 0.0, 0.0))
    with pytest.raises(ValueError, match="symmetric"):
        replace(state, covariance=((1.0, 0.5, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)))
    with pytest.raises(ValueError, match="positive semidefinite"):
        replace(state, covariance=((1.0, 2.0, 0.0), (2.0, 1.0, 0.0), (0.0, 0.0, 1.0)))


def test_risk_state_is_frozen_and_identity_fields_are_content_bound() -> None:
    state = _estimate(_observations())
    with pytest.raises(FrozenInstanceError):
        state.covariance = state.covariance  # type: ignore[misc]
    with pytest.raises(ValueError, match="semantic identity"):
        replace(state, semantic_identity="0" * 64)
    assert len(state.semantic_id) == 64
    assert len(state.closure_id) == 64
    assert len(state.provenance_id) == 64


def test_nested_contracts_normalise_mutable_inputs_before_identity() -> None:
    mutable_caps = RiskCaps(
        asset_caps=cast(tuple[float, ...], [1.0, 1.0, 1.0]),
        gross_cap=2.0,
        net_cap=1.0,
        concentration_cap=0.7,
        portfolio_risk_cap=1.0,
        group_caps=cast(tuple[float, ...], [1.0, 1.0]),
        currency_caps=cast(tuple[float, ...], [1.0, 1.0]),
    )
    assert isinstance(mutable_caps.asset_caps, tuple)
    mutable_mapping = ExposureMapping(
        group_keys=cast(tuple[str, ...], ["macro", "style"]),
        group_exposure_matrix=cast(FloatMatrix, [[1.0, 1.0, 0.0], [0.0, 0.0, 1.0]]),
        group_caps=cast(tuple[float, ...], [1.0, 1.0]),
        currency_keys=cast(tuple[str, ...], ["aud", "usd"]),
        currency_exposure_matrix=cast(FloatMatrix, [[1.0, 0.0, 0.0], [0.0, 1.0, 1.0]]),
        currency_caps=cast(tuple[float, ...], [1.0, 1.0]),
    )
    assert mutable_mapping.group_exposure_matrix == MAPPING.group_exposure_matrix
    state = _estimate(_observations())
    replay = replace(
        state,
        covariance=[list(row) for row in state.covariance],
        group_exposure_matrix=[list(row) for row in state.group_exposure_matrix],
        currency_exposure_matrix=[list(row) for row in state.currency_exposure_matrix],
        semantic_identity=None,
        closure_identity=None,
        provenance_identity=None,
    )
    assert replay.covariance == state.covariance
    assert replay.semantic_id == state.semantic_id


def test_direct_state_rejects_unsupported_estimator() -> None:
    state = _estimate(_observations())
    with pytest.raises(ValueError, match="unsupported risk estimator"):
        replace(
            state,
            estimator="UNSUPPORTED",
            semantic_identity=None,
            closure_identity=None,
            provenance_identity=None,
        )


def test_maximum_age_enforces_causal_freshness_in_estimator_and_state() -> None:
    boundary = _estimate(_observations())
    assert boundary.maximum_age == timedelta(hours=1)
    at_boundary = estimate_ordered_risk_state(
        asset_order=ASSETS,
        observations=_observations(),
        as_of=CUTOFF + CONFIG.maximum_age,
        observation_cutoff=CUTOFF,
        config=CONFIG,
        exposure_mapping=MAPPING,
        caps=CAPS,
        provenance="test-r3-risk-boundary",
    )
    assert at_boundary.as_of - at_boundary.observation_cutoff == CONFIG.maximum_age
    with pytest.raises(ValueError, match="maximum age"):
        estimate_ordered_risk_state(
            asset_order=ASSETS,
            observations=_observations(),
            as_of=CUTOFF + CONFIG.maximum_age + timedelta(microseconds=1),
            observation_cutoff=CUTOFF,
            config=CONFIG,
            exposure_mapping=MAPPING,
            caps=CAPS,
            provenance="test-r3-risk-stale",
        )
    with pytest.raises(ValueError, match="maximum age"):
        replace(
            boundary,
            as_of=CUTOFF + CONFIG.maximum_age + timedelta(microseconds=1),
            semantic_identity=None,
            closure_identity=None,
            provenance_identity=None,
        )


def test_config_and_state_reject_unsupported_policy_labels() -> None:
    with pytest.raises(ValueError, match="availability policy"):
        replace(CONFIG, availability_policy="UNSUPPORTED")
    with pytest.raises(ValueError, match="estimator version"):
        replace(CONFIG, estimator_version="unsupported-version")
    state = _estimate(_observations())
    with pytest.raises(ValueError, match="availability policy"):
        replace(
            state,
            availability_policy="UNSUPPORTED",
            semantic_identity=None,
            closure_identity=None,
            provenance_identity=None,
        )
    with pytest.raises(ValueError, match="estimator version"):
        replace(
            state,
            estimator_version="unsupported-version",
            semantic_identity=None,
            closure_identity=None,
            provenance_identity=None,
        )


def test_huge_finite_positions_fail_closed_on_derived_overflow() -> None:
    state = _estimate(_observations())
    huge_caps = RiskCaps(
        asset_caps=(1e308, 1e308, 1e308),
        gross_cap=1e308,
        net_cap=1e308,
        concentration_cap=1.0,
        portfolio_risk_cap=1e308,
        group_caps=(1e308, 1e308),
        currency_caps=(1e308, 1e308),
    )
    state = replace(
        state,
        caps=huge_caps,
        group_caps=huge_caps.group_caps,
        currency_caps=huge_caps.currency_caps,
        semantic_identity=None,
        closure_identity=None,
        provenance_identity=None,
    )
    huge_position = (1e308, 1e308, 1e308)
    with pytest.raises(ValueError, match="non-finite"):
        state.portfolio_variance(huge_position)
    with pytest.raises(ValueError, match="non-finite"):
        state.group_exposure(huge_position)
    with pytest.raises(ValueError, match="non-finite"):
        state.validate_position(huge_position)
    assert not state.position_is_valid(huge_position)
