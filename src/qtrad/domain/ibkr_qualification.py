"""Runtime-only capability yielded by verified IBKR capture qualification."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from qtrad.domain.identifiers import InstrumentId, ProviderListingId

IBKR_QUALIFICATION_CONTRACT = "qtrad-ibkr-native-qualification-v1"


class IbkrQualificationStage(StrEnum):
    B3_EXACT_TWO = "B3_EXACT_TWO"
    B4_EXACT_SIX = "B4_EXACT_SIX"


@dataclass(frozen=True, slots=True)
class IbkrQualifiedContract:
    instrument_id: InstrumentId
    listing_id: ProviderListingId
    con_id: int

    def __post_init__(self) -> None:
        if self.con_id <= 0:
            raise ValueError("qualified IBKR conId must be positive")


@dataclass(frozen=True, slots=True)
class VerifiedIbkrCaptureQualification:
    """Opaque capability produced only by the pure qualification verifier."""

    stage: IbkrQualificationStage
    artifact_sha256: str
    release_contract: str
    release_sha256: str
    configuration_hash: str
    capture_source_id: str
    universe_id: str
    instruments: frozenset[InstrumentId]
    contracts: tuple[IbkrQualifiedContract, ...]
    qualified_at: datetime

    @classmethod
    def _from_verified_artifact(
        cls,
        *,
        stage: IbkrQualificationStage,
        artifact_sha256: str,
        release_contract: str,
        release_sha256: str,
        configuration_hash: str,
        capture_source_id: str,
        universe_id: str,
        instruments: frozenset[InstrumentId],
        contracts: tuple[IbkrQualifiedContract, ...],
        qualified_at: datetime,
    ) -> VerifiedIbkrCaptureQualification:
        return cls(
            stage=stage,
            artifact_sha256=artifact_sha256,
            release_contract=release_contract,
            release_sha256=release_sha256,
            configuration_hash=configuration_hash,
            capture_source_id=capture_source_id,
            universe_id=universe_id,
            instruments=instruments,
            contracts=contracts,
            qualified_at=qualified_at,
        )


VerifiedB3Qualification = VerifiedIbkrCaptureQualification
