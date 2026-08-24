"""Focused R3.D Decimal rounding, repair and reconciliation tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from decimal import Decimal

import pytest

from qtrad.application.r3_portfolio import solve_continuous_target
from qtrad.application.r3_rounding import _repaired_attributions, round_and_repair_target
from qtrad.domain.economics import ImpactDisposition, SessionState
from qtrad.domain.market_data import EvidencePurpose, MarketDataSourceClass
from qtrad.domain.portfolio import AssetNetting, NettingResult, SleeveAttribution
from qtrad.domain.r3_rounding import RoundingDisposition, RoundingPolicy, RoundingReasonCode
from qtrad.domain.risk import RiskCaps
from tests.test_r3_portfolio import _key, _netting_for, _target_inputs


def _target(inputs, values: tuple[float, ...]):
    return solve_continuous_target(inputs, runner=lambda inputs: ("optimal", values))


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
    target = solve_continuous_target(inputs, runner=lambda inputs: ("infeasible", (99.0, 99.0)))
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
    assert result.netting.internal_cross_quantity == Decimal("2")
    assert sum(
        (item.external_delta_share for item in result.attributions), Decimal("0")
    ) == Decimal("0")
    assert result.attributions[0].requested_delta == Decimal("2")
    assert result.attributions[1].requested_delta == Decimal("-2")
    assert all(item.repair_delta == Decimal("0") for item in result.attributions)


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
        type(result).__setattr__(result, "target_position", (Decimal("9"), Decimal("0")))


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


def _risk_replaced(inputs, *, caps: RiskCaps, **fields):
    risk = replace(
        inputs.risk,
        caps=caps,
        semantic_identity=None,
        closure_identity=None,
        provenance_identity=None,
        **fields,
    )
    object.__setattr__(inputs, "risk", risk)
    return inputs


@pytest.mark.parametrize(
    ("name", "requested", "caps", "fields"),
    [
        (
            "asset",
            (Decimal("5"), Decimal("0")),
            RiskCaps((2.0, 10.0), 20.0, 20.0, 1.0, 20.0),
            {},
        ),
        (
            "gross",
            (Decimal("3"), Decimal("3")),
            RiskCaps((10.0, 10.0), 2.0, 20.0, 1.0, 20.0),
            {},
        ),
        (
            "net",
            (Decimal("3"), Decimal("0")),
            RiskCaps((10.0, 10.0), 20.0, 2.0, 1.0, 20.0),
            {},
        ),
        (
            "concentration",
            (Decimal("3"), Decimal("1")),
            RiskCaps((10.0, 10.0), 20.0, 20.0, 0.5, 20.0),
            {},
        ),
        (
            "group",
            (Decimal("3"), Decimal("0")),
            RiskCaps((10.0, 10.0), 20.0, 20.0, 1.0, 20.0, group_caps=(2.0,)),
            {
                "group_keys": ("all",),
                "group_exposure_matrix": ((1.0, 1.0),),
                "group_caps": (2.0,),
            },
        ),
        (
            "currency",
            (Decimal("3"), Decimal("0")),
            RiskCaps((10.0, 10.0), 20.0, 20.0, 1.0, 20.0, currency_caps=(2.0,)),
            {
                "currency_keys": ("AUD",),
                "currency_exposure_matrix": ((1.0, 1.0),),
                "currency_caps": (2.0,),
            },
        ),
        (
            "risk",
            (Decimal("3"), Decimal("3")),
            RiskCaps((10.0, 10.0), 20.0, 20.0, 1.0, 0.0001),
            {},
        ),
    ],
)
def test_every_coupled_cap_repairs_without_over_cap(
    name: str,
    requested: tuple[Decimal, Decimal],
    caps: RiskCaps,
    fields: dict[str, object],
) -> None:
    del name
    inputs = _target_inputs(requested_target=requested)
    capped = _risk_replaced(inputs, caps=caps, **fields)
    result = round_and_repair_target(
        _target(capped, tuple(float(value) for value in requested)), capped
    )
    if result.disposition is not RoundingDisposition.BLOCKED:
        capped.risk.validate_position(tuple(float(value) for value in result.target_position))
    assert not result.target_position or result.disposition is not RoundingDisposition.BLOCKED


def test_repair_rejects_below_minimum_quantity() -> None:
    inputs = _target_inputs(requested_target=(Decimal("7"), Decimal("0")))
    target = _target(inputs, (7.0, 0.0))
    economics = dict(inputs.economics)
    economics["asset:a"] = replace(
        economics["asset:a"], minimum_quantity=Decimal("5"), quantity_increment=Decimal("1")
    )
    object.__setattr__(inputs, "economics", economics)
    capped = _risk_replaced(
        inputs,
        caps=RiskCaps((2.0, 10.0), 20.0, 20.0, 1.0, 20.0),
    )
    target = replace(target, decision_input_identity=capped.decision_input_identity)
    result = round_and_repair_target(target, capped)
    assert result.disposition is RoundingDisposition.BLOCKED
    assert not result.target_position
    assert RoundingReasonCode.MINIMUM_QUANTITY_NOT_MET.value in result.reason_codes


def test_parent_solver_reasons_are_normalised_to_r3d() -> None:
    inputs = _target_inputs(requested_target=(Decimal("1"), Decimal("0")))
    target = _target(inputs, (1.0, 0.0))
    object.__setattr__(
        target,
        "reason_codes",
        ("SOLVER_INACCURATE", "ASSET_CAP", "unrecognised-parent-reason"),
    )
    result = round_and_repair_target(target, inputs)
    assert result.disposition is RoundingDisposition.BLOCKED
    assert RoundingReasonCode.SOLVER_NON_OPTIMAL.value in result.reason_codes
    assert RoundingReasonCode.ASSET_CAP_REPAIR.value in result.reason_codes
    assert RoundingReasonCode.DECISION_BLOCKED.value in result.reason_codes


def test_strict_cap_repair_handles_sub_tolerance_excess() -> None:
    inputs = _target_inputs(requested_target=(Decimal("1.000000001"), Decimal("0")))
    capped = _risk_replaced(
        inputs,
        caps=RiskCaps((1.0, 10.0), 20.0, 20.0, 1.0, 20.0),
    )
    result = round_and_repair_target(_target(capped, (1.000000001, 0.0)), capped)
    assert result.target_position == (Decimal("1"), Decimal("0"))
    capped.risk.validate_position((1.0, 0.0))


def test_impact_quantity_cap_fails_closed_without_partial_target() -> None:
    inputs = _target_inputs(requested_target=(Decimal("3"), Decimal("0")))
    economics = dict(inputs.economics)
    economics["asset:a"] = replace(
        economics["asset:a"],
        impact_disposition=ImpactDisposition.CAPPED_NO_IMPACT_RANGE,
        impact_max_quantity=Decimal("1"),
    )
    object.__setattr__(inputs, "economics", economics)
    result = round_and_repair_target(_target(inputs, (3.0, 0.0)), inputs)
    assert result.disposition is RoundingDisposition.BLOCKED
    assert not result.target_position
    assert RoundingReasonCode.ASSET_PAPER_INELIGIBLE.value in result.reason_codes


def test_stale_input_identity_fails_closed() -> None:
    inputs = _target_inputs(requested_target=(Decimal("1"), Decimal("0")))
    target = _target(inputs, (1.0, 0.0))
    object.__setattr__(inputs, "alpha_return", (Decimal("9"), Decimal("0")))
    result = round_and_repair_target(target, inputs)
    assert result.disposition is RoundingDisposition.BLOCKED
    assert not result.target_position
    assert RoundingReasonCode.DECISION_BLOCKED.value in result.reason_codes


def test_forged_cost_state_with_same_total_is_rejected() -> None:
    inputs = _target_inputs(requested_target=(Decimal("1"), Decimal("0")))
    result = round_and_repair_target(_target(inputs, (1.0, 0.0)), inputs)
    assert result.disposition is RoundingDisposition.ACCEPTED
    forged = replace(result.expected_costs["asset:a"], provenance="forged-cost")
    with pytest.raises(ValueError, match="cost state identity"):
        replace(result, expected_costs={**result.expected_costs, "asset:a": forged})


def test_nonzero_attribution_residual_is_rejected() -> None:
    inputs = _target_inputs(requested_target=(Decimal("1"), Decimal("0")))
    result = round_and_repair_target(_target(inputs, (1.0, 0.0)), inputs)
    assert result.disposition is RoundingDisposition.ACCEPTED
    with pytest.raises(ValueError, match="attribution residual"):
        replace(result, attribution_residual=Decimal("0.01"))


def test_cost_mapping_order_does_not_change_identity() -> None:
    inputs = _target_inputs(requested_target=(Decimal("1"), Decimal("0")))
    result = round_and_repair_target(_target(inputs, (1.0, 0.0)), inputs)
    assert result.disposition is RoundingDisposition.ACCEPTED
    reversed_costs = dict(reversed(tuple(result.expected_costs.items())))
    replay = replace(result, expected_costs=reversed_costs)
    assert replay.canonical_bytes == result.canonical_bytes
    assert replay.semantic_identity == result.semantic_identity


def _multi_sleeve_netting(weights: tuple[Decimal, ...]) -> NettingResult:
    asset_attributions = tuple(
        SleeveAttribution(
            _key("asset:a", experiment=f"sleeve-{index}"),
            weight,
            Decimal("0"),
            weight,
        )
        for index, weight in enumerate(weights)
    )
    first = AssetNetting(
        "asset:a",
        sum(weights, Decimal("0")),
        sum(weights, Decimal("0")),
        Decimal("0"),
        asset_attributions,
    )
    second = AssetNetting("asset:b", Decimal("0"), Decimal("0"), Decimal("0"), ())
    return NettingResult(
        source_class=MarketDataSourceClass.IBKR_HISTORICAL_RESEARCH,
        evidence_purpose=EvidencePurpose.FIXTURE_IMPLEMENTATION,
        assets=(first, second),
        sleeves=asset_attributions,
    )


def test_repaired_attribution_uses_largest_remainder_and_canonical_tie() -> None:
    inputs = _target_inputs()
    netting = _multi_sleeve_netting((Decimal("1"), Decimal("1"), Decimal("1")))
    reasons: set[str] = set()
    attributions, residual = _repaired_attributions(
        netting, (Decimal("1"), Decimal("0")), inputs.economics, reasons
    )
    asset_a = [item for item in attributions if item.key.asset_id == "asset:a"]
    assert [item.external_delta_share for item in asset_a] == [
        Decimal("1"),
        Decimal("0"),
        Decimal("0"),
    ]
    assert residual == Decimal("0")
    assert sum((item.external_delta_share for item in asset_a), Decimal("0")) == Decimal("1")
    object.__setattr__(
        netting.assets[0], "attributions", tuple(reversed(netting.assets[0].attributions))
    )
    object.__setattr__(netting, "sleeves", tuple(reversed(netting.sleeves)))
    replay, replay_residual = _repaired_attributions(
        netting, (Decimal("1"), Decimal("0")), inputs.economics, set()
    )
    assert {
        item.key: item.external_delta_share for item in replay if item.key.asset_id == "asset:a"
    } == {
        item.key: item.external_delta_share
        for item in attributions
        if item.key.asset_id == "asset:a"
    }
    assert replay_residual == Decimal("0")


def test_repaired_attribution_allocates_unequal_and_signed_movement() -> None:
    inputs = _target_inputs()
    positive = _multi_sleeve_netting((Decimal("2"), Decimal("1"), Decimal("1")))
    negative = _multi_sleeve_netting((Decimal("-2"), Decimal("-1"), Decimal("-1")))
    positive_result, positive_residual = _repaired_attributions(
        positive, (Decimal("2"), Decimal("0")), inputs.economics, set()
    )
    negative_result, negative_residual = _repaired_attributions(
        negative, (Decimal("-2"), Decimal("0")), inputs.economics, set()
    )
    assert [
        item.external_delta_share for item in positive_result if item.key.asset_id == "asset:a"
    ] == [
        Decimal("1"),
        Decimal("1"),
        Decimal("0"),
    ]
    assert [
        item.external_delta_share for item in negative_result if item.key.asset_id == "asset:a"
    ] == [
        Decimal("-1"),
        Decimal("-1"),
        Decimal("0"),
    ]
    assert positive_residual == Decimal("0")
    assert negative_residual == Decimal("0")
