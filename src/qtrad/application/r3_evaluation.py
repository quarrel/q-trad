"""Fixture-only R3.E vertical slice and create-only persistence."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from qtrad.domain.economics import (
    ContinuousCostComponent,
    ContinuousCostModel,
    CostBasis,
    CostComponentKind,
    ImpactDisposition,
    InputStatus,
)
from qtrad.domain.market_data import EvidencePurpose, MarketDataSourceClass
from qtrad.domain.portfolio import (
    AssetNetting,
    NettingResult,
    SleeveKey,
)
from qtrad.domain.portfolio import (
    SleeveAttribution as ParentSleeveAttribution,
)
from qtrad.domain.r3_evaluation import (
    DecisionClosure,
    EvaluationReport,
    QuoteEvidence,
    SleeveAttribution,
    VerificationReceipt,
    build_outcome_closures,
    evaluate_independently,
    identity,
)
from qtrad.domain.r3_rounding import (
    ROUNDING_CONTRACT,
    RepairedSleeveAttribution,
    RoundedTarget,
    RoundingDisposition,
    cost_states_identity,
)
from qtrad.domain.risk import RiskCaps, RiskState

_FIXTURE_TIME = datetime(2025, 1, 2, tzinfo=UTC)


def _component(kind: CostComponentKind, basis: CostBasis, slope: str) -> ContinuousCostComponent:
    status = InputStatus.AVAILABLE
    if kind is not CostComponentKind.IMPACT and slope == "0":
        status = InputStatus.DOCUMENTED_ZERO
    return ContinuousCostComponent(
        component=kind,
        basis=basis,
        native_currency="AUD",
        reporting_currency="AUD",
        conversion_rate=Decimal("1"),
        conversion_source="fixture-fx",
        conversion_version="fixture-fx-v1",
        slopes=(Decimal(slope),),
        breakpoints=(),
        form="LINEAR",
        version="fixture-cost-v1",
        provenance="R3.E fixture implementation evidence",
        status=status,
    )


def _model(asset_id: str) -> ContinuousCostModel:
    components = (
        _component(CostComponentKind.SPREAD, CostBasis.PHYSICAL_DELTA, "0"),
        _component(CostComponentKind.LATENCY_MOVEMENT, CostBasis.PHYSICAL_DELTA, "0"),
        _component(CostComponentKind.ADVERSE_SLIPPAGE, CostBasis.PHYSICAL_DELTA, "0"),
        _component(CostComponentKind.COMMISSION, CostBasis.PHYSICAL_DELTA, "0.01"),
        _component(CostComponentKind.FINANCING, CostBasis.PHYSICAL_HOLDING, "0.001"),
        _component(CostComponentKind.IMPACT, CostBasis.PHYSICAL_DELTA, "0"),
    )
    return ContinuousCostModel(
        asset_id=asset_id,
        horizon=timedelta(minutes=15),
        reporting_currency="AUD",
        components=components,
        economics_identity=identity({"contract": "fixture-economics-v1", "asset_id": asset_id}),
        commission_version="fixture-cost-v1",
        commission_provenance="R3.E fixture implementation evidence",
        financing_version="fixture-cost-v1",
        financing_provenance="R3.E fixture implementation evidence",
        impact_version="fixture-cost-v1",
        impact_disposition=ImpactDisposition.SUPPORTED_MODEL,
        impact_status=InputStatus.AVAILABLE,
        version="fixture-model-v1",
        provenance="R3.E fixture implementation evidence",
    )


def _decision_input_receipt(target: RoundedTarget) -> VerificationReceipt:
    semantic = target.decision_input_identity
    parent = identity(
        {"contract": "r3-d-input-root-v1", "asset_order": target.asset_order, "semantic": semantic}
    )
    return VerificationReceipt(
        artefact_contract="qtrad-r3d-decision-input-v1",
        semantic_identity=semantic,
        closure_identity=identity(
            {"contract": "qtrad-r3d-decision-input-closure-v1", "target": target.semantic_identity}
        ),
        parent_verification_identity=parent,
        verifier_contract="r3-d-input-fixture-verifier-v1",
        checks=("canonical-bytes", "input-identity", "create-only"),
    )


def _target_receipt(target: RoundedTarget) -> VerificationReceipt:
    return VerificationReceipt(
        artefact_contract=ROUNDING_CONTRACT,
        semantic_identity=target.semantic_identity,
        closure_identity=target.semantic_identity,
        parent_verification_identity=_decision_input_receipt(target).receipt_identity,
        verifier_contract="r3-d-fixture-verifier-v1",
        checks=("canonical-bytes", "target-reconciliation", "create-only"),
    )


def _risk_state(source: MarketDataSourceClass, purpose: EvidencePurpose) -> RiskState:
    caps = RiskCaps(
        asset_caps=(1.0,),
        gross_cap=1.0,
        net_cap=1.0,
        concentration_cap=1.0,
        portfolio_risk_cap=1.0,
        group_caps=(1.0,),
        currency_caps=(1.0,),
    )
    return RiskState(
        asset_order=("ASSET_A",),
        horizon=timedelta(minutes=15),
        as_of=_FIXTURE_TIME,
        observation_cutoff=_FIXTURE_TIME,
        lookback=timedelta(days=1),
        maximum_age=timedelta(days=1),
        availability_policy="AVAILABLE_BY_CUTOFF",
        return_unit="LOG_RETURN",
        estimator="LEDOIT_WOLF",
        estimator_version="qtrad-ledoit-wolf-pure-python-v1",
        shrinkage=0.25,
        covariance=((0.04,),),
        sample_count=2,
        raw_observation_count=2,
        missing_observation_count=0,
        excluded_observation_count=0,
        effective_observations=2,
        symmetry_tolerance=1e-12,
        psd_tolerance=1e-12,
        finite_tolerance=0.0,
        group_keys=("fixture-group",),
        group_exposure_matrix=((1.0,),),
        group_caps=(1.0,),
        currency_keys=("AUD",),
        currency_exposure_matrix=((1.0,),),
        currency_caps=(1.0,),
        caps=caps,
        source_class=source,
        evidence_purpose=purpose,
        provenance="R3.E fixture implementation evidence: ordered risk state",
    )


def build_fixture_inputs() -> tuple[DecisionClosure, tuple[QuoteEvidence, ...]]:
    """Build a one-asset opposing-sleeve fixture through R3.D target contracts."""
    asset = "ASSET_A"
    assets = (asset,)
    current = (Decimal("0"),)
    target_position = (Decimal("0.5"),)
    model = _model(asset)
    expected_state = model.evaluate(
        current_quantity=current[0],
        target_quantity=target_position[0],
        decision_time=_FIXTURE_TIME,
        internal_cross_quantity=Decimal("0.5"),
    )
    expected = {asset: expected_state}
    source = MarketDataSourceClass.IG_NATIVE_CAPTURE
    purpose = EvidencePurpose.FIXTURE_IMPLEMENTATION
    long_key = SleeveKey(source, purpose, "fixture-exp", "long", asset)
    short_key = SleeveKey(source, purpose, "fixture-exp", "short", asset)
    long_parent = ParentSleeveAttribution(long_key, Decimal("1"), Decimal("0.5"), Decimal("0.5"))
    short_parent = ParentSleeveAttribution(short_key, Decimal("-0.5"), Decimal("0.5"), Decimal("0"))
    netting = NettingResult(
        source,
        purpose,
        (
            AssetNetting(
                asset, Decimal("0.5"), Decimal("0.5"), Decimal("0.5"), (long_parent, short_parent)
            ),
        ),
        (long_parent, short_parent),
    )
    repaired = (
        RepairedSleeveAttribution(long_key, Decimal("1"), Decimal("0.5"), Decimal("0.5")),
        RepairedSleeveAttribution(short_key, Decimal("-0.5"), Decimal("0.5"), Decimal("0")),
    )
    financing = next(
        component.reporting_amount
        for component in expected_state.components
        if component.component is CostComponentKind.FINANCING
    )
    assert financing is not None
    total = expected_state.require_total_reporting()
    decision_input_identity = identity(
        {
            "contract": "qtrad-r3d-decision-input-v1",
            "source_class": source,
            "evidence_purpose": purpose,
            "asset_order": assets,
            "current_position": current,
            "target_position": target_position,
            "expected_cost_identity": cost_states_identity(expected),
        }
    )
    target = RoundedTarget(
        source_class=source,
        evidence_purpose=purpose,
        asset_order=assets,
        current_position=current,
        continuous_target=target_position,
        target_position=target_position,
        physical_delta=target_position,
        disposition=RoundingDisposition.ACCEPTED,
        reason_codes=(),
        expected_costs=expected,
        expected_cost_reporting=total - financing,
        expected_financing_reporting=financing,
        netting=netting,
        attributions=repaired,
        policy_identity=identity({"contract": ROUNDING_CONTRACT, "policy": "fixture"}),
        decision_input_identity=decision_input_identity,
        continuous_target_identity=identity(
            {
                "contract": "qtrad-continuous-target-v1",
                "asset_order": assets,
                "target": target_position,
            }
        ),
        cost_state_identity=cost_states_identity(expected),
    )
    target_receipt = _target_receipt(target)
    attributions = (
        SleeveAttribution("long", asset, Decimal("1"), Decimal("0.5"), Decimal("0.5")),
        SleeveAttribution("short", asset, Decimal("-0.5"), Decimal("0.5"), Decimal("0")),
    )
    risk = _risk_state(source, purpose)
    decision = DecisionClosure(
        source_class=source,
        evidence_purpose=purpose,
        asset_order=assets,
        current_position=current,
        target_position=target_position,
        physical_delta=target_position,
        decision_time=_FIXTURE_TIME,
        expiry_time=_FIXTURE_TIME + timedelta(minutes=15),
        holding_interval=timedelta(minutes=15),
        gross_forecast_return={asset: Decimal("0.02")},
        gross_contribution={asset: Decimal("1.00")},
        physical_notional={asset: Decimal("50")},
        expected_costs=expected,
        cost_models={asset: model},
        attributions=attributions,
        decision_input_identity=decision_input_identity,
        parent_verification_identity=target_receipt.receipt_identity,
        rounded_target=target,
        target_verification_identity=target_receipt.receipt_identity,
        risk_state=risk,
    )
    quotes = (
        QuoteEvidence(asset, _FIXTURE_TIME, Decimal("99"), Decimal("101"), sequence=0),
        QuoteEvidence(
            asset, _FIXTURE_TIME + timedelta(seconds=2), Decimal("99"), Decimal("101"), sequence=1
        ),
        QuoteEvidence(
            asset,
            _FIXTURE_TIME + timedelta(minutes=15, seconds=1),
            Decimal("102"),
            Decimal("104"),
            sequence=2,
        ),
    )
    return decision, quotes


def _write_create_only(path: Path, payload: bytes) -> str:
    if path.is_symlink() or path.exists():
        raise FileExistsError(f"create-only artifact already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    persisted = path.read_bytes()
    if persisted != payload:
        raise ValueError(f"persisted artifact bytes changed: {path}")
    return identity(json.loads(persisted))


def run_fixture(output_dir: Path) -> EvaluationReport:
    """Run and persist the production evaluator's bounded fixture path."""
    decision, quotes = build_fixture_inputs()
    latency = timedelta(seconds=1)
    outcomes = build_outcome_closures(decision, quotes, latency=latency)
    report = evaluate_independently(decision, quotes, latency=latency)
    target = decision.rounded_target
    if target is None:
        raise ValueError("fixture decision must bind a rounded target")
    target_receipt = _target_receipt(target)
    _write_create_only(output_dir / "decision.json", decision.canonical_bytes)
    _write_create_only(output_dir / "target.json", target.canonical_bytes)
    persisted_outcome_ids: list[str] = []
    for outcome in outcomes:
        persisted_id = _write_create_only(
            output_dir / f"outcome-{outcome.asset_id}.json", outcome.canonical_bytes
        )
        if persisted_id != outcome.semantic_identity:
            raise ValueError("persisted outcome identity does not match closure")
        persisted_outcome_ids.append(persisted_id)
    expected_outcome_ids = (
        report.outcome_identities
        + report.unavailable_outcome_identities
        + report.blocked_outcome_identities
    )
    if tuple(persisted_outcome_ids) != expected_outcome_ids:
        raise ValueError("persisted outcome identities do not match report dispositions")
    _write_create_only(output_dir / "report.json", report.canonical_bytes)
    _write_create_only(output_dir / "target-receipt.json", target_receipt.canonical_bytes)
    report_receipt = VerificationReceipt(
        artefact_contract=report.report_contract,
        semantic_identity=report.semantic_identity,
        closure_identity=report.closure_identity,
        parent_verification_identity=target_receipt.receipt_identity,
        verifier_contract="r3-e-fixture-verifier-v1",
        checks=(
            "canonical-bytes",
            "independent-reconciliation",
            "outcome-closure-identities",
            "create-only",
        ),
    )
    _write_create_only(output_dir / "report-receipt.json", report_receipt.canonical_bytes)
    return report


def fixture_cli(output: str) -> None:
    report = run_fixture(Path(output))
    print(
        json.dumps(
            {"report": str(Path(output) / "report.json"), "identity": report.semantic_identity},
            sort_keys=True,
        )
    )
