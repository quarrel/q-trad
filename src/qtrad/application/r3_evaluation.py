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
from qtrad.domain.r3_evaluation import (
    DecisionClosure,
    EvaluationReport,
    OutcomeClosure,
    QuoteEvidence,
    SleeveAttribution,
    VerificationReceipt,
    build_outcome_closures,
    evaluate_independently,
    identity,
)

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
        economics_identity="a" * 64,
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


def build_fixture_inputs() -> tuple[DecisionClosure, tuple[QuoteEvidence, ...]]:
    """Build a tiny two-asset opposing-sleeve fixture through real contracts."""
    assets = ("ASSET_A", "ASSET_B")
    current = (Decimal("0"), Decimal("0"))
    target = (Decimal("1"), Decimal("-1"))
    models = {asset: _model(asset) for asset in assets}
    expected = {
        asset: models[asset].evaluate(
            current_quantity=current[index],
            target_quantity=target[index],
            decision_time=_FIXTURE_TIME,
        )
        for index, asset in enumerate(assets)
    }
    attributions = tuple(
        SleeveAttribution(
            sleeve_id=f"sleeve-{asset.lower()}",
            asset_id=asset,
            requested_delta=target[index],
            internal_cross_quantity=Decimal("0"),
            external_delta_share=target[index],
        )
        for index, asset in enumerate(assets)
    )
    decision = DecisionClosure(
        source_class=MarketDataSourceClass.IG_NATIVE_CAPTURE,
        evidence_purpose=EvidencePurpose.FIXTURE_IMPLEMENTATION,
        asset_order=assets,
        current_position=current,
        target_position=target,
        physical_delta=target,
        decision_time=_FIXTURE_TIME,
        expiry_time=_FIXTURE_TIME + timedelta(minutes=15),
        holding_interval=timedelta(minutes=15),
        gross_forecast_return={asset: Decimal("0.02") for asset in assets},
        gross_contribution={"ASSET_A": Decimal("0.20"), "ASSET_B": Decimal("0.10")},
        expected_costs=expected,
        cost_models=models,
        attributions=attributions,
        decision_input_identity="b" * 64,
        parent_verification_identity="c" * 64,
    )
    quotes = (
        QuoteEvidence(
            "ASSET_A", _FIXTURE_TIME + timedelta(seconds=1), Decimal("99"), Decimal("101")
        ),
        QuoteEvidence(
            "ASSET_B", _FIXTURE_TIME + timedelta(seconds=1), Decimal("49"), Decimal("51")
        ),
        QuoteEvidence(
            "ASSET_A",
            _FIXTURE_TIME + timedelta(minutes=15, seconds=1),
            Decimal("102"),
            Decimal("104"),
        ),
        QuoteEvidence(
            "ASSET_B",
            _FIXTURE_TIME + timedelta(minutes=15, seconds=1),
            Decimal("47"),
            Decimal("49"),
        ),
    )
    return decision, quotes


def _write_create_only(path: Path, payload: bytes) -> str:
    if path.is_symlink() or path.exists():
        raise FileExistsError(f"create-only artifact already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return identity(json.loads(payload))


def run_fixture(output_dir: Path) -> EvaluationReport:
    """Run and persist the production evaluator's bounded fixture path."""
    decision, quotes = build_fixture_inputs()
    latency = timedelta(seconds=1)
    outcomes = build_outcome_closures(decision, quotes, latency=latency)
    report = evaluate_independently(decision, quotes, latency=latency)
    decision_path = output_dir / "decision.json"
    _write_create_only(decision_path, decision.canonical_bytes)
    for outcome in outcomes:
        _write_create_only(output_dir / f"outcome-{outcome.asset_id}.json", outcome.canonical_bytes)
    report_path = output_dir / "report.json"
    _write_create_only(report_path, report.canonical_bytes)
    receipt_artifacts: list[
        tuple[str, str, DecisionClosure | OutcomeClosure | EvaluationReport]
    ] = [(decision.contract, "decision", decision)]
    receipt_artifacts.extend(
        ("outcome-closure", f"outcome-{outcome.asset_id}", outcome) for outcome in outcomes
    )
    receipt_artifacts.append((report.report_contract, "report", report))
    for contract, stem, artefact in receipt_artifacts:
        receipt = VerificationReceipt(
            artefact_contract=contract,
            semantic_identity=artefact.semantic_identity,
            closure_identity=identity(json.loads(artefact.canonical_bytes)),
            parent_verification_identity=decision.parent_verification_identity,
            verifier_contract="r3-e-fixture-verifier-v1",
            checks=("canonical-bytes", "independent-reconciliation", "create-only"),
        )
        _write_create_only(output_dir / f"{stem}-receipt.json", receipt.canonical_bytes)
    return report


def fixture_cli(output: str) -> None:
    report = run_fixture(Path(output))
    print(
        json.dumps(
            {"report": str(Path(output) / "report.json"), "identity": report.semantic_identity},
            sort_keys=True,
        )
    )
