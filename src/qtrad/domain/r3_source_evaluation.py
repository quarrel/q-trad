"""Source-aligned R3.G evaluation contracts.

The source evaluator is deliberately smaller than the R3.E lifecycle evaluator.  It
binds every quote and outcome to one source/product/session authority, keeps
historical midpoint evidence non-executable, and exposes only read-only readiness
inputs for the later R3.I protocol.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from qtrad.domain.market_data import EvidencePurpose, MarketDataSourceClass
from qtrad.domain.r3_evaluation import canonical_bytes, identity
from qtrad.domain.time import require_utc

SOURCE_EVALUATION_CONTRACT = "qtrad-r3-source-aligned-evaluation-v1"
SOURCE_REPORT_CONTRACT = "qtrad-r3-source-aligned-report-v1"
SOURCE_RECEIPT_CONTRACT = "qtrad-r3-source-verification-receipt-v1"
R3I_READINESS_CONTRACT = "qtrad-r3-native-readiness-input-v1"
_DIGEST_ZERO = "0" * 64


def _digest(value: str, name: str) -> None:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be a SHA-256 digest")


def _decimal(value: Decimal, name: str) -> None:
    if type(value) is not Decimal or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")


class SourceOutcomeDisposition(StrEnum):
    ACCEPTED = "ACCEPTED"
    UNAVAILABLE = "UNAVAILABLE"
    BLOCKED = "BLOCKED"


class SourceResultKind(StrEnum):
    MIDPOINT_ONLY = "MIDPOINT_ONLY"
    EXECUTABLE = "EXECUTABLE"
    INCOMPLETE = "INCOMPLETE"


@dataclass(frozen=True, slots=True)
class SourceAuthority:
    """The exact source/product/economics/session authority for one evaluation."""

    source_class: MarketDataSourceClass
    classification: EvidencePurpose
    source_product_id: str
    product_economics_identity: str
    session_version: str
    decision_time: datetime
    receive_time: datetime
    executable_quote_authority: bool = False
    parent_verification_identity: str = _DIGEST_ZERO
    source_evidence_identity: str = _DIGEST_ZERO

    def __post_init__(self) -> None:
        if type(self.source_class) is not MarketDataSourceClass:
            raise ValueError("source authority requires a declared source class")
        if type(self.classification) is not EvidencePurpose:
            raise ValueError("source authority requires a declared classification")
        if (
            not self.source_product_id
            or not self.product_economics_identity
            or not self.session_version
        ):
            raise ValueError("source product, economics and session identities are required")
        for value, name in (
            (self.product_economics_identity, "product economics identity"),
            (self.parent_verification_identity, "parent verification identity"),
            (self.source_evidence_identity, "source evidence identity"),
        ):
            _digest(value, name)
        require_utc(self.decision_time, "source decision time")
        require_utc(self.receive_time, "source receive time")
        if self.receive_time > self.decision_time:
            raise ValueError("source receive time cannot follow decision time")
        if self.classification is EvidencePurpose.HISTORICAL_EXPLORATORY:
            if self.source_class is not MarketDataSourceClass.IBKR_HISTORICAL_RESEARCH:
                raise ValueError("historical exploratory authority requires IBKR historical source")
            if self.executable_quote_authority:
                raise ValueError("historical midpoint authority cannot be executable")
        if self.classification is EvidencePurpose.FUTURE_NATIVE_DECISION_GRADE:
            if self.source_class not in {
                MarketDataSourceClass.IG_NATIVE_CAPTURE,
                MarketDataSourceClass.IBKR_NATIVE_CAPTURE,
            }:
                raise ValueError("future native authority requires a native source")
            if not self.executable_quote_authority:
                raise ValueError("future native authority requires executable quote authority")

    @property
    def semantic_identity(self) -> str:
        return identity(self.canonical_payload)

    @property
    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": SOURCE_EVALUATION_CONTRACT,
            "source_class": self.source_class,
            "classification": self.classification,
            "source_product_id": self.source_product_id,
            "product_economics_identity": self.product_economics_identity,
            "session_version": self.session_version,
            "decision_time": self.decision_time,
            "receive_time": self.receive_time,
            "executable_quote_authority": self.executable_quote_authority,
            "parent_verification_identity": self.parent_verification_identity,
            "source_evidence_identity": self.source_evidence_identity,
        }


@dataclass(frozen=True, slots=True)
class SourceQuote:
    """A received midpoint observation or a paired executable bid/ask quote."""

    asset_id: str
    source_class: MarketDataSourceClass
    source_product_id: str
    product_economics_identity: str
    session_version: str
    source_evidence_identity: str
    received_time: datetime
    midpoint: Decimal | None = None
    bid: Decimal | None = None
    ask: Decimal | None = None
    healthy: bool = True
    session_open: bool = True
    sequence: int = 0

    def __post_init__(self) -> None:
        if not self.asset_id or not self.source_product_id or not self.session_version:
            raise ValueError("quote asset, product and session identities are required")
        if type(self.source_class) is not MarketDataSourceClass:
            raise ValueError("quote source class must be a declared enum")
        _digest(self.product_economics_identity, "quote product economics identity")
        _digest(self.source_evidence_identity, "quote source evidence identity")
        require_utc(self.received_time, "quote received time")
        provided_sides = self.bid is not None or self.ask is not None
        if provided_sides:
            if self.bid is None or self.ask is None:
                raise ValueError("executable quote requires both bid and ask")
            _decimal(self.bid, "quote bid")
            _decimal(self.ask, "quote ask")
            if self.bid <= 0 or self.ask <= 0 or self.bid > self.ask:
                raise ValueError("quote sides must be positive and ordered")
            if self.midpoint is not None:
                raise ValueError("paired quote cannot carry a separate midpoint")
        else:
            if self.midpoint is None:
                raise ValueError("quote requires midpoint or paired bid and ask")
            _decimal(self.midpoint, "quote midpoint")
            if self.midpoint <= 0:
                raise ValueError("quote midpoint must be positive")
        if self.source_class is MarketDataSourceClass.IBKR_HISTORICAL_RESEARCH and provided_sides:
            raise ValueError("historical source may provide midpoint evidence only")

    @property
    def executable(self) -> bool:
        return self.bid is not None and self.ask is not None

    @property
    def value(self) -> Decimal:
        if self.midpoint is not None:
            return self.midpoint
        assert self.bid is not None and self.ask is not None
        return (self.bid + self.ask) / Decimal("2")

    @property
    def receive_time(self) -> datetime:
        return self.received_time

    @property
    def quote_identity(self) -> str:
        return identity(self.canonical_payload)

    @property
    def canonical_payload(self) -> dict[str, object]:
        return {
            "asset_id": self.asset_id,
            "source_class": self.source_class,
            "source_product_id": self.source_product_id,
            "product_economics_identity": self.product_economics_identity,
            "session_version": self.session_version,
            "source_evidence_identity": self.source_evidence_identity,
            "received_time": self.received_time,
            "midpoint": self.midpoint,
            "bid": self.bid,
            "ask": self.ask,
            "healthy": self.healthy,
            "session_open": self.session_open,
            "sequence": self.sequence,
        }

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.canonical_payload)


@dataclass(frozen=True, slots=True)
class SourceAlignedOutcome:
    """One source-bound outcome, including explicit stress costs."""

    asset_id: str
    authority: SourceAuthority
    target_time: datetime
    physical_delta: Decimal
    entry: SourceQuote | None
    exit: SourceQuote | None
    disposition: SourceOutcomeDisposition
    gross_return: Decimal = Decimal("0")
    spread_cost: Decimal = Decimal("0")
    latency_stress: Decimal = Decimal("0")
    slippage_stress: Decimal = Decimal("0")
    reason_codes: tuple[str, ...] = ()
    latency: timedelta = timedelta(0)

    def __post_init__(self) -> None:
        if not self.asset_id:
            raise ValueError("outcome asset is required")
        if type(self.disposition) is not SourceOutcomeDisposition:
            raise ValueError("outcome disposition must be a declared enum")
        require_utc(self.target_time, "outcome target time")
        if self.target_time <= self.authority.decision_time:
            raise ValueError("outcome target time must follow decision")
        _decimal(self.physical_delta, "outcome physical delta")
        for value, name in (
            (self.gross_return, "gross return"),
            (self.spread_cost, "spread cost"),
            (self.latency_stress, "latency stress"),
            (self.slippage_stress, "slippage stress"),
        ):
            _decimal(value, name)
            if value < 0 and name != "gross return":
                raise ValueError(f"{name} must be non-negative")
        if self.latency < timedelta(0):
            raise ValueError("outcome latency cannot be negative")
        if not self.reason_codes and self.disposition is not SourceOutcomeDisposition.ACCEPTED:
            raise ValueError("unavailable or blocked outcome requires a reason")
        if self.disposition is not SourceOutcomeDisposition.ACCEPTED and any(
            value != Decimal("0")
            for value in (
                self.gross_return,
                self.spread_cost,
                self.latency_stress,
                self.slippage_stress,
            )
        ):
            raise ValueError("non-accepted outcome cannot carry economics")
        for quote in (self.entry, self.exit):
            if quote is None:
                continue
            if (
                quote.asset_id != self.asset_id
                or quote.source_class is not self.authority.source_class
                or quote.source_product_id != self.authority.source_product_id
                or quote.product_economics_identity != self.authority.product_economics_identity
                or quote.session_version != self.authority.session_version
                or quote.source_evidence_identity != self.authority.source_evidence_identity
            ):
                raise ValueError("quote does not bind source/product/economics/session authority")
            if self.disposition is SourceOutcomeDisposition.ACCEPTED and (
                not quote.healthy or not quote.session_open
            ):
                raise ValueError("unhealthy or closed quote cannot be executable evidence")
        if self.disposition is SourceOutcomeDisposition.ACCEPTED:
            if self.entry is not None and self.entry.received_time >= self.target_time:
                raise ValueError("entry quote must precede target")
            if self.entry is not None and self.entry.received_time < (
                self.authority.decision_time + self.latency
            ):
                raise ValueError("entry quote precedes latency boundary")
            if self.exit is not None and self.exit.received_time <= self.target_time:
                raise ValueError("exit quote must be received after target")
            if (
                self.entry is not None
                and self.exit is not None
                and self.entry.received_time >= self.exit.received_time
            ):
                raise ValueError("entry quote must precede exit quote")
            if self.entry is None or self.exit is None:
                raise ValueError("accepted outcome requires entry and exit evidence")
            if self.authority.executable_quote_authority and (
                not self.entry.executable or not self.exit.executable
            ):
                raise ValueError("executable authority requires paired bid/ask evidence")
            if not self.authority.executable_quote_authority and (
                self.entry.executable or self.exit.executable
            ):
                raise ValueError("executable quote cannot bypass executable authority")
        if self.authority.classification is EvidencePurpose.HISTORICAL_EXPLORATORY and any(
            quote is not None and quote.executable for quote in (self.entry, self.exit)
        ):
            raise ValueError("historical midpoint outcome cannot be executable")
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes))

    @property
    def executable(self) -> bool:
        return (
            self.disposition is SourceOutcomeDisposition.ACCEPTED
            and self.entry is not None
            and self.exit is not None
            and self.entry.executable
            and self.exit.executable
        )

    @property
    def total_cost(self) -> Decimal:
        return self.spread_cost + self.latency_stress + self.slippage_stress

    @property
    def net_return(self) -> Decimal:
        return self.gross_return - self.total_cost

    @property
    def outcome_identity(self) -> str:
        return identity(self.canonical_payload)

    @property
    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": SOURCE_EVALUATION_CONTRACT,
            "asset_id": self.asset_id,
            "authority": self.authority.canonical_payload,
            "target_time": self.target_time,
            "physical_delta": self.physical_delta,
            "entry": self.entry.canonical_payload if self.entry is not None else None,
            "exit": self.exit.canonical_payload if self.exit is not None else None,
            "disposition": self.disposition,
            "gross_return": self.gross_return,
            "spread_cost": self.spread_cost,
            "latency_stress": self.latency_stress,
            "slippage_stress": self.slippage_stress,
            "reason_codes": self.reason_codes,
            "latency": self.latency,
        }


@dataclass(frozen=True, slots=True)
class SourceAlignedEvaluation:
    """Canonical source-aligned report with no cross-source borrowing."""

    authority: SourceAuthority
    outcomes: tuple[SourceAlignedOutcome, ...]
    report_contract: str = SOURCE_REPORT_CONTRACT

    def __post_init__(self) -> None:
        if not self.outcomes:
            raise ValueError("source evaluation requires outcomes")
        if any(
            item.authority.semantic_identity != self.authority.semantic_identity
            for item in self.outcomes
        ):
            raise ValueError("outcome authority differs from report authority")
        assets = tuple(item.asset_id for item in self.outcomes)
        if len(set(assets)) != len(assets):
            raise ValueError("source evaluation assets must be unique")
        if tuple(sorted(assets)) != assets:
            raise ValueError("source evaluation assets must be canonical")
        if self.result_kind is SourceResultKind.EXECUTABLE and (
            self.authority.classification is EvidencePurpose.HISTORICAL_EXPLORATORY
        ):
            raise ValueError("historical report cannot emit executable result")

    @property
    def result_kind(self) -> SourceResultKind:
        if any(
            item.disposition is not SourceOutcomeDisposition.ACCEPTED
            and item.physical_delta != Decimal("0")
            for item in self.outcomes
        ):
            return SourceResultKind.INCOMPLETE
        if any(item.executable for item in self.outcomes):
            return SourceResultKind.EXECUTABLE
        return SourceResultKind.MIDPOINT_ONLY

    @property
    def gross_total(self) -> Decimal:
        return sum((item.gross_return for item in self.outcomes), Decimal("0"))

    @property
    def cost_total(self) -> Decimal:
        return sum((item.total_cost for item in self.outcomes), Decimal("0"))

    @property
    def net_total(self) -> Decimal:
        return self.gross_total - self.cost_total

    @property
    def semantic_identity(self) -> str:
        return identity(self.canonical_payload)

    @property
    def closure_identity(self) -> str:
        return hashlib.sha256(self.canonical_bytes).hexdigest()

    @property
    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": self.report_contract,
            "authority": self.authority.canonical_payload,
            "classification": self.authority.classification,
            "result_kind": self.result_kind,
            "outcomes": tuple(item.canonical_payload for item in self.outcomes),
            "gross_total": self.gross_total,
            "cost_total": self.cost_total,
            "net_total": self.net_total,
        }

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.canonical_payload)


@dataclass(frozen=True, slots=True)
class SourceVerificationReceipt:
    """Small create-only receipt for the exact source-aligned report."""

    semantic_identity: str
    closure_identity: str
    parent_verification_identity: str
    classification: EvidencePurpose
    checks: tuple[str, ...]
    receipt_identity: str = ""

    def __post_init__(self) -> None:
        _digest(self.semantic_identity, "receipt semantic identity")
        _digest(self.closure_identity, "receipt closure identity")
        _digest(self.parent_verification_identity, "receipt parent identity")
        if type(self.classification) is not EvidencePurpose or not self.checks:
            raise ValueError("receipt classification and checks are required")
        object.__setattr__(self, "checks", tuple(self.checks))
        expected = identity(self.canonical_payload)
        if not self.receipt_identity:
            object.__setattr__(self, "receipt_identity", expected)
        elif self.receipt_identity != expected:
            raise ValueError("receipt identity does not bind source report")

    @property
    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": SOURCE_RECEIPT_CONTRACT,
            "semantic_identity": self.semantic_identity,
            "closure_identity": self.closure_identity,
            "parent_verification_identity": self.parent_verification_identity,
            "classification": self.classification,
            "checks": self.checks,
        }

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_bytes(
            {**self.canonical_payload, "receipt_identity": self.receipt_identity}
        )


# Alias used by readiness consumers.
SourceEvaluationReceipt = SourceVerificationReceipt


@dataclass(frozen=True, slots=True)
class R3IReadinessInput:
    """Read-only, outcome-free handoff into the separately frozen R3.I protocol."""

    source_class: MarketDataSourceClass
    source_product_id: str
    product_economics_identity: str
    session_version: str
    receive_time: datetime
    executable_quote_identities: tuple[str, ...]
    source_evidence_identity: str
    classification: EvidencePurpose = EvidencePurpose.FUTURE_NATIVE_DECISION_GRADE
    native_execution_authority: bool = False

    def __post_init__(self) -> None:
        if self.source_class not in {
            MarketDataSourceClass.IG_NATIVE_CAPTURE,
            MarketDataSourceClass.IBKR_NATIVE_CAPTURE,
        }:
            raise ValueError("R3.I readiness requires a native source")
        if self.classification is not EvidencePurpose.FUTURE_NATIVE_DECISION_GRADE:
            raise ValueError("R3.I readiness classification must be future native decision grade")
        if self.native_execution_authority:
            raise ValueError("R3.I readiness cannot grant native execution authority")
        if not self.source_product_id or not self.session_version:
            raise ValueError("R3.I source product and session identity are required")
        _digest(self.product_economics_identity, "R3.I economics identity")
        _digest(self.source_evidence_identity, "R3.I source evidence identity")
        if not self.executable_quote_identities:
            raise ValueError("R3.I readiness requires executable quote identities")
        for value in self.executable_quote_identities:
            _digest(value, "R3.I executable quote identity")
        require_utc(self.receive_time, "R3.I receive time")

    @property
    def semantic_identity(self) -> str:
        return identity(self.canonical_payload)

    @property
    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": R3I_READINESS_CONTRACT,
            "source_class": self.source_class,
            "classification": self.classification,
            "source_product_id": self.source_product_id,
            "product_economics_identity": self.product_economics_identity,
            "session_version": self.session_version,
            "receive_time": self.receive_time,
            "executable_quote_identities": self.executable_quote_identities,
            "source_evidence_identity": self.source_evidence_identity,
            "native_execution_authority": self.native_execution_authority,
        }
