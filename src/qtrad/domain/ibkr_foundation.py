"""Contracts shared by the IBKR historical foundation boundary."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import ClassVar

from qtrad.domain.events import JsonValue
from qtrad.domain.identifiers import InstrumentId


class IBKRFoundationReadinessState(StrEnum):
    """Source-specific readiness dispositions before any R2 experiment."""

    QUALIFYING_HISTORY_READY = "QUALIFYING_HISTORY_READY"
    INSUFFICIENT_HISTORY_FOR_MODEL_CONCLUSION = "INSUFFICIENT_HISTORY_FOR_MODEL_CONCLUSION"


class IBKRFoundationReadinessCause(StrEnum):
    """Exact causes which prevent a historical foundation from qualifying."""

    ENTITLEMENT_UNAVAILABLE = "ENTITLEMENT_UNAVAILABLE"
    CONTRACT_IDENTITY_CHANGED = "CONTRACT_IDENTITY_CHANGED"
    SESSION_EVIDENCE_UNAVAILABLE = "SESSION_EVIDENCE_UNAVAILABLE"
    INSUFFICIENT_COMMON_SUPPORT = "INSUFFICIENT_COMMON_SUPPORT"
    INSUFFICIENT_BLOCK_COVERAGE = "INSUFFICIENT_BLOCK_COVERAGE"
    INSUFFICIENT_DURATION = "INSUFFICIENT_DURATION"
    INSUFFICIENT_ROWS = "INSUFFICIENT_ROWS"
    MISSING_CONFIRMATORY_TARGET = "MISSING_CONFIRMATORY_TARGET"


IBKR_CONFIRMATORY_CANDIDATES: tuple[tuple[InstrumentId, str], ...] = (
    (InstrumentId("fx:aud-usd"), "FX"),
    (InstrumentId("fx:eur-usd"), "FX"),
    (InstrumentId("index:australia-200"), "indices"),
    (InstrumentId("index:us-500"), "indices"),
    (InstrumentId("commodity:spot-gold"), "commodities"),
    (InstrumentId("commodity:us-crude"), "commodities"),
)
IBKR_CONFIRMATORY_INSTRUMENTS = tuple(
    instrument_id for instrument_id, _ in IBKR_CONFIRMATORY_CANDIDATES
)
IBKR_CONFIRMATORY_GROUPS: tuple[str, ...] = ("FX", "indices", "commodities")
IBKR_FOUNDATION_CONTRACT = "qtrad-ibkr-historical-foundation-v1"
IBKR_FOUNDATION_SCHEMA_VERSION = 1
IBKR_MINIMUM_COMMON_SUPPORT_ROWS = 1_000
IBKR_MINIMUM_ROWS_PER_CANDIDATE = 1_000
IBKR_MINIMUM_DURATION_SECONDS = 30 * 24 * 60 * 60


def _zero_candidate_rows() -> dict[str, int]:
    return {str(candidate): 0 for candidate in IBKR_CONFIRMATORY_INSTRUMENTS}


def _empty_evidence() -> dict[str, JsonValue]:
    return {}


@dataclass(frozen=True, slots=True)
class IBKRFoundationReadiness:
    """Fixed, model-independent readiness report for the IBKR foundation."""

    state: IBKRFoundationReadinessState
    causes: tuple[IBKRFoundationReadinessCause, ...]
    candidate_instruments: tuple[InstrumentId, ...] = IBKR_CONFIRMATORY_INSTRUMENTS
    groups: tuple[str, ...] = IBKR_CONFIRMATORY_GROUPS
    common_support_start: datetime | None = None
    common_support_end: datetime | None = None
    common_support_rows: int = 0
    rows_by_candidate: dict[str, int] = field(default_factory=_zero_candidate_rows)
    evidence: dict[str, JsonValue] = field(default_factory=_empty_evidence)

    CONTRACT: ClassVar[str] = IBKR_FOUNDATION_CONTRACT
    SCHEMA_VERSION: ClassVar[int] = IBKR_FOUNDATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.candidate_instruments != IBKR_CONFIRMATORY_INSTRUMENTS:
            raise ValueError("IBKR confirmatory candidates are fixed by the foundation contract")
        if self.groups != IBKR_CONFIRMATORY_GROUPS:
            raise ValueError("IBKR foundation groups are fixed by the foundation contract")
        if self.common_support_rows < 0:
            raise ValueError("common support row count must not be negative")
        if set(self.rows_by_candidate) != {str(item) for item in self.candidate_instruments}:
            raise ValueError("readiness must report every fixed confirmatory candidate")
        if self.state is IBKRFoundationReadinessState.QUALIFYING_HISTORY_READY and self.causes:
            raise ValueError("qualifying readiness cannot contain unmet causes")
        if (
            self.state is IBKRFoundationReadinessState.INSUFFICIENT_HISTORY_FOR_MODEL_CONCLUSION
            and not self.causes
        ):
            raise ValueError("insufficient readiness must contain an exact cause")

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": self.SCHEMA_VERSION,
            "state": self.state.value,
            "causes": [cause.value for cause in self.causes],
            "candidate_instruments": [str(item) for item in self.candidate_instruments],
            "groups": list(self.groups),
            "common_support_start": (
                self.common_support_start.isoformat()
                if self.common_support_start is not None
                else None
            ),
            "common_support_end": (
                self.common_support_end.isoformat() if self.common_support_end is not None else None
            ),
            "common_support_rows": self.common_support_rows,
            "rows_by_candidate": dict(sorted(self.rows_by_candidate.items())),
            "evidence": dict(self.evidence),
        }
