"""Focused R3.D Decimal rounding, repair and reconciliation tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from decimal import Decimal

import pytest
from test_r3_portfolio import _key, _netting_for, _target_inputs

from qtrad.application.r3_portfolio import solve_continuous_target
from qtrad.application.r3_rounding import round_and_repair_target
from qtrad.domain.economics import SessionState
from qtrad.domain.market_data import EvidencePurpose, MarketDataSourceClass
from qtrad.domain.portfolio import AssetNetting, NettingResult, SleeveAttribution
from qtrad.domain.r3_rounding import RoundingDisposition, RoundingPolicy, RoundingReasonCode
from qtrad.domain.risk import RiskCaps


def _target(inputs, values: tuple[float, float]):
    return solve_continuous_target(inputs, runner=lambda _: ("optimal", values))


def test_positive_negative_and_decimal_rounding_are_sign_symmetric() -> None:
    positive_inputs = _target_inputs(requested_target=(Decimal("1.9"), Decimal("0")))
    negative_inputs = _target_inputs(requested_target=(Decimal("-1.9"), Decimal("0")))
    positive = round_and_repair_target(_target(positive_inputs, (1.9, 0.0)), positive_inputs)
    negative = round_and_repair_target(_target(negative_inputs, (-1.9, 0.0)), negative_inputs)
    assert positive.target_position == (Decimal("1"), Decimal("0"))
    assert negative.target_position == (Decimal("-1"), Decimal("0"))
    assert RoundingReasonCode.QUANTITY_ROUNDED.value in positive.reason_codes
    assert RoundingReasonCode.QUANTITY_ROUNDED.value in negative.reason_codes
    assert positive.physical_delta == (Decimal("1"), Decimal("0"))
    assert negative.physical_delta == (Decimal("-1"), Decimal("0"))


def test_zero_forecast_cannot_open_or_reverse_and_flat_is_stable() -> None:
    inputs = _target_inputs(
        alpha=(Decimal("0"), Decimal("0")), requested_target=(Decimal("2"), Decimal("0"))
    )
    result = round_and_repair_target(_target(inputs, (2.0, 0.0)), inputs)
    assert result.target_position == (Decimal("0"), Decimal("0"))
    assert result.disposition is RoundingDisposition.PROJECTED
    assert RoundingReasonCode.ZERO_FORECAST_NEW_EXPOSURE_BLOCKED.value in result.reason_codes


def test_asset_cap_repair_reaches_valid_increment() -> None:
    inputs = _target_inputs(requested_target=(Decimal("5"), Decimal("0")))
    target = _target(inputs, (5.0, 0.0))
    capped = replace(
        inputs,
        risk=replace(
            inputs.risk,
            caps=RiskCaps(
                asset_caps=(2.0, 10.0),
                gross_cap=20.0,
                net_cap=20.0,
                concentration_cap=1.0,
                portfolio_risk_cap=20.0,
            ),
            semantic_identity=None,
            closure_identity=None,
            provenance_identity=None,
        ),
    )
    target = replace(target, decision_input_identity=capped.decision_input_identity)
    result = round_and_repair_target(target, capped)
    assert result.target_position == (Decimal("2"), Decimal("0"))
    assert result.disposition is RoundingDisposition.PROJECTED
    assert RoundingReasonCode.ASSET_CAP_REPAIR.value in result.reason_codes
    capped.risk.validate_position(tuple(float(value) for value in result.target_position))


def test_solver_failure_projects_current_without_partial_target() -> None:
    inputs = _target_inputs()
    target = solve_continuous_target(inputs, runner=lambda _: ("infeasible", (99.0, 99.0)))
    result = round_and_repair_target(target, inputs)
    assert result.disposition is RoundingDisposition.PROJECTED
    assert result.target_position == inputs.current_position
    assert result.physical_delta == (Decimal("0"), Decimal("0"))
    assert result.expected_costs


def test_internal_cross_and_attribution_totals_are_exact() -> None:
    key_a = _key("asset:a", experiment="a")
    key_b = _key("asset:a", experiment="b")
    first = SleeveAttribution(key_a, Decimal("2"), Decimal("2"), Decimal("0"))
    second = SleeveAttribution(key_b, Decimal("-2"), Decimal("2"), Decimal("0"))
    netting = NettingResult(
        source_class=MarketDataSourceClass.IBKR_HISTORICAL_RESEARCH,
        evidence_purpose=EvidencePurpose.FIXTURE_IMPLEMENTATION,
        assets=(
            AssetNetting("asset:a", Decimal("0"), Decimal("0"), Decimal("2"), (first, second)),
            AssetNetting("asset:b", Decimal("0"), Decimal("0"), Decimal("0"), ()),
        ),
        sleeves=(first, second),
    )
    inputs = _target_inputs(
        requested_target=(Decimal("0"), Decimal("0")),
        netting=netting,
    )
    result = round_and_repair_target(_target(inputs, (0.0, 0.0)), inputs)
    assert result.netting.internal_cross_quantity == Decimal("0")
    assert sum(
        (item.external_delta_share for item in result.attributions), Decimal("0")
    ) == Decimal("0")
    assert result.attributions[0].requested_delta == Decimal("0")


def test_permuted_reason_replay_and_identity_are_frozen() -> None:
    inputs = _target_inputs(requested_target=(Decimal("1.9"), Decimal("0")))
    target = _target(inputs, (1.9, 0.0))
    first = round_and_repair_target(target, inputs)
    second = round_and_repair_target(target, inputs)
    assert first.semantic_identity == second.semantic_identity
    assert first.canonical_bytes == second.canonical_bytes
    assert first.reason_codes == tuple(
        sorted(
            first.reason_codes,
            key=lambda code: (
                tuple(RoundingReasonCode).index(RoundingReasonCode(code))
                if code in {item.value for item in RoundingReasonCode}
                else len(RoundingReasonCode),
                code,
            ),
        )
    )


def test_current_short_zero_forecast_never_increases_or_reverses() -> None:
    inputs = _target_inputs(alpha=(Decimal("0"), Decimal("0")))
    inputs = replace(
        inputs,
        current_position=(Decimal("-2"), Decimal("0")),
        netting=_netting_for((Decimal("3"), Decimal("0"))),
    )
    result = round_and_repair_target(_target(inputs, (1.0, 0.0)), inputs)
    assert result.target_position == (Decimal("-2"), Decimal("0"))
    assert result.disposition is RoundingDisposition.PROJECTED
    assert RoundingReasonCode.ZERO_FORECAST_NEW_EXPOSURE_BLOCKED.value in result.reason_codes


def test_repair_limit_blocks_without_partial_target() -> None:
    inputs = _target_inputs(requested_target=(Decimal("5"), Decimal("0")))
    target = _target(inputs, (5.0, 0.0))
    capped = replace(
        inputs,
        risk=replace(
            inputs.risk,
            caps=RiskCaps(
                asset_caps=(2.0, 10.0),
                gross_cap=20.0,
                net_cap=20.0,
                concentration_cap=1.0,
                portfolio_risk_cap=20.0,
            ),
            semantic_identity=None,
            closure_identity=None,
            provenance_identity=None,
        ),
    )
    target = replace(target, decision_input_identity=capped.decision_input_identity)
    result = round_and_repair_target(target, capped, policy=RoundingPolicy(max_repair_steps=1))
    assert result.disposition is RoundingDisposition.BLOCKED
    assert not result.target_position
    assert RoundingReasonCode.DECISION_BLOCKED.value in result.reason_codes


def test_ineligible_product_fails_closed_without_partial_target() -> None:
    inputs = _target_inputs()
    economics = dict(inputs.economics)
    economics["asset:a"] = replace(economics["asset:a"], session_state=SessionState.CLOSED)
    object.__setattr__(inputs, "economics", economics)
    result = round_and_repair_target(_target(inputs, (1.0, 0.0)), inputs)
    assert result.disposition is RoundingDisposition.BLOCKED
    assert not result.target_position
    assert RoundingReasonCode.ASSET_PAPER_INELIGIBLE.value in result.reason_codes


def test_rounded_target_is_frozen_and_decimal_reconciled() -> None:
    inputs = _target_inputs(requested_target=(Decimal("1.9"), Decimal("0")))
    result = round_and_repair_target(_target(inputs, (1.9, 0.0)), inputs)
    assert all(type(value) is Decimal for value in result.target_position)
    with pytest.raises(FrozenInstanceError):
        result.target_position = (Decimal("9"), Decimal("0"))


def test_missing_economics_fails_closed_with_explicit_reason() -> None:
    inputs = _target_inputs()
    target = _target(inputs, (1.0, 0.0))
    object.__setattr__(inputs, "economics", {})
    result = round_and_repair_target(target, inputs)
    assert result.disposition is RoundingDisposition.BLOCKED
    assert RoundingReasonCode.INPUT_ECONOMICS_MISSING.value in result.reason_codes


def test_invalid_risk_fails_closed_without_partial_target() -> None:
    inputs = _target_inputs()
    target = _target(inputs, (1.0, 0.0))
    object.__setattr__(inputs, "risk", None)
    result = round_and_repair_target(target, inputs)
    assert result.disposition is RoundingDisposition.BLOCKED
    assert RoundingReasonCode.INPUT_RISK_INVALID.value in result.reason_codes
