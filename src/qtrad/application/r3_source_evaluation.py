"""R3.G source-aligned fixture application path.

This path is deterministic and fixture-only.  It does not acquire provider data,
open a holdout, or expose an order operation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Final

from qtrad.domain.market_data import EvidencePurpose, MarketDataSourceClass
from qtrad.domain.r3_evaluation import identity
from qtrad.domain.r3_source_evaluation import (
    R3IReadinessInput,
    SourceAlignedEvaluation,
    SourceAlignedOutcome,
    SourceAuthority,
    SourceOutcomeDisposition,
    SourceQuote,
    SourceVerificationReceipt,
)

FIXTURE_CONTRACT: Final = "qtrad-r3-g-fixture-v1"
FIXTURE_PARENT_IDENTITY: Final = "0" * 64
FIXTURE_PRODUCT_ID: Final = "IG-DEMO-ASSET-PAIR"
FIXTURE_SESSION_VERSION: Final = "ig-demo-session-v1"
FIXTURE_ECONOMICS_IDENTITY: Final = identity(
    {
        "contract": "qtrad-r3-product-economics-fixture-v1",
        "asset_order": ("ASSET_A", "ASSET_B"),
        "source_class": MarketDataSourceClass.IG_NATIVE_CAPTURE,
        "source_product_id": FIXTURE_PRODUCT_ID,
        "price_currency": "AUD",
        "settlement_currency": "AUD",
        "reporting_currency": "AUD",
        "session_version": FIXTURE_SESSION_VERSION,
        "spread_policy": "paired-bid-ask",
        "latency_policy": "bounded-stress-v1",
        "slippage_policy": "adverse-v1",
    }
)
_FIXTURE_DECISION = datetime(2026, 1, 2, 0, 0, tzinfo=UTC)
_FIXTURE_TARGET = _FIXTURE_DECISION + timedelta(minutes=15)


@dataclass(frozen=True, slots=True)
class SourceFixtureInputs:
    authority: SourceAuthority
    outcomes: tuple[SourceAlignedOutcome, ...]
    readiness: R3IReadinessInput


def build_fixture_inputs() -> SourceFixtureInputs:
    """Build paired native fixture evidence plus one explicitly unavailable outcome."""

    authority = SourceAuthority(
        source_class=MarketDataSourceClass.IG_NATIVE_CAPTURE,
        classification=EvidencePurpose.FIXTURE_IMPLEMENTATION,
        source_product_id=FIXTURE_PRODUCT_ID,
        product_economics_identity=FIXTURE_ECONOMICS_IDENTITY,
        session_version=FIXTURE_SESSION_VERSION,
        decision_time=_FIXTURE_DECISION,
        receive_time=_FIXTURE_DECISION - timedelta(seconds=1),
        executable_quote_authority=True,
        parent_verification_identity=FIXTURE_PARENT_IDENTITY,
        source_evidence_identity=identity(
            {"contract": FIXTURE_CONTRACT, "decision": _FIXTURE_DECISION}
        ),
    )
    entry = SourceQuote(
        asset_id="ASSET_A",
        source_class=authority.source_class,
        source_product_id=authority.source_product_id,
        product_economics_identity=authority.product_economics_identity,
        session_version=authority.session_version,
        received_time=_FIXTURE_DECISION + timedelta(seconds=2),
        bid=Decimal("100.00"),
        ask=Decimal("100.10"),
        sequence=1,
    )
    exit_quote = SourceQuote(
        asset_id="ASSET_A",
        source_class=authority.source_class,
        source_product_id=authority.source_product_id,
        product_economics_identity=authority.product_economics_identity,
        session_version=authority.session_version,
        received_time=_FIXTURE_TARGET + timedelta(seconds=1),
        bid=Decimal("101.00"),
        ask=Decimal("101.10"),
        sequence=2,
    )
    accepted = SourceAlignedOutcome(
        asset_id="ASSET_A",
        authority=authority,
        target_time=_FIXTURE_TARGET,
        physical_delta=Decimal("1"),
        entry=entry,
        exit=exit_quote,
        disposition=SourceOutcomeDisposition.ACCEPTED,
        gross_return=Decimal("0.0100"),
        spread_cost=Decimal("0.0010"),
        latency_stress=Decimal("0.0002"),
        slippage_stress=Decimal("0.0003"),
        latency=timedelta(seconds=2),
    )
    unavailable = SourceAlignedOutcome(
        asset_id="ASSET_B",
        authority=authority,
        target_time=_FIXTURE_TARGET,
        physical_delta=Decimal("1"),
        entry=None,
        exit=None,
        disposition=SourceOutcomeDisposition.UNAVAILABLE,
        reason_codes=("MISSING_EXECUTABLE_OUTCOME",),
    )
    outcomes = (accepted, unavailable)
    readiness = R3IReadinessInput(
        source_class=authority.source_class,
        source_product_id=authority.source_product_id,
        product_economics_identity=authority.product_economics_identity,
        session_version=authority.session_version,
        receive_time=entry.received_time,
        executable_quote_identities=(entry.quote_identity, exit_quote.quote_identity),
        source_evidence_identity=authority.source_evidence_identity,
    )
    return SourceFixtureInputs(authority, outcomes, readiness)


def evaluate_source_aligned(
    authority: SourceAuthority,
    outcomes: tuple[SourceAlignedOutcome, ...],
) -> SourceAlignedEvaluation:
    """Evaluate only outcomes bound to the supplied source authority."""

    return SourceAlignedEvaluation(
        authority=authority, outcomes=tuple(sorted(outcomes, key=lambda x: x.asset_id))
    )


def evaluate_fixture() -> SourceAlignedEvaluation:
    inputs = build_fixture_inputs()
    return evaluate_source_aligned(inputs.authority, inputs.outcomes)


def build_r3i_readiness_input() -> R3IReadinessInput:
    return build_fixture_inputs().readiness


def _write_create_only(path: Path, payload: bytes) -> None:
    if path.is_symlink() or path.exists():
        raise FileExistsError(f"create-only artifact already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    if path.read_bytes() != payload:
        raise ValueError(f"persisted artifact bytes changed: {path}")


def run_fixture(output_dir: Path) -> SourceAlignedEvaluation:
    """Persist one deterministic report and receipt; never overwrite either."""

    report = evaluate_fixture()
    _write_create_only(output_dir / "report.json", report.canonical_bytes)
    receipt = SourceVerificationReceipt(
        semantic_identity=report.semantic_identity,
        closure_identity=report.closure_identity,
        parent_verification_identity=report.authority.parent_verification_identity,
        classification=report.authority.classification,
        checks=(
            "canonical-bytes",
            "source-product-economics-session-binding",
            "paired-bid-ask",
            "latency-slippage-stress",
            "unavailable-outcome",
            "create-only",
        ),
    )
    _write_create_only(output_dir / "report-receipt.json", receipt.canonical_bytes)
    return report


def fixture_cli(output: str) -> None:
    report = run_fixture(Path(output))
    print(
        json.dumps(
            {
                "classification": report.authority.classification,
                "report": str(Path(output) / "report.json"),
                "receipt": str(Path(output) / "report-receipt.json"),
                "result_kind": report.result_kind,
                "semantic_identity": report.semantic_identity,
            },
            sort_keys=True,
        )
    )


# Explicit names for callers that prefer the R3.G label.
build_source_aligned_fixture = evaluate_fixture
