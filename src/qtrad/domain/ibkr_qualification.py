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


_VERIFIED_IBKR_CAPTURE_QUALIFICATION_TOKEN = object()


class VerifiedIbkrCaptureQualification:
    """Opaque immutable capability produced only by an evidence-replaying verifier."""

    __slots__ = (
        "_artifact_sha256",
        "_capture_source_id",
        "_configuration_hash",
        "_contracts",
        "_instruments",
        "_qualified_at",
        "_release_contract",
        "_release_sha256",
        "_stage",
        "_universe_id",
        "_verifier_token",
    )

    _artifact_sha256: str
    _capture_source_id: str
    _configuration_hash: str
    _contracts: tuple[IbkrQualifiedContract, ...]
    _instruments: frozenset[InstrumentId]
    _qualified_at: datetime
    _release_contract: str
    _release_sha256: str
    _stage: IbkrQualificationStage
    _universe_id: str
    _verifier_token: object

    def __init__(self) -> None:
        raise TypeError(
            "VerifiedIbkrCaptureQualification is constructed only by an evidence-replaying verifier"
        )

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("VerifiedIbkrCaptureQualification is immutable")

    @property
    def stage(self) -> IbkrQualificationStage:
        return self._stage

    @property
    def artifact_sha256(self) -> str:
        return self._artifact_sha256

    @property
    def release_contract(self) -> str:
        return self._release_contract

    @property
    def release_sha256(self) -> str:
        return self._release_sha256

    @property
    def configuration_hash(self) -> str:
        return self._configuration_hash

    @property
    def capture_source_id(self) -> str:
        return self._capture_source_id

    @property
    def universe_id(self) -> str:
        return self._universe_id

    @property
    def instruments(self) -> frozenset[InstrumentId]:
        return self._instruments

    @property
    def contracts(self) -> tuple[IbkrQualifiedContract, ...]:
        return self._contracts

    @property
    def qualified_at(self) -> datetime:
        return self._qualified_at


def has_verified_ibkr_capture_qualification_provenance(value: object) -> bool:
    """Authenticate exact capability type and the private verifier sentinel.

    No production code can currently mint the sentinel. The independently
    replayable live-evidence verifier will own that transition in a later tranche.
    """

    return (
        type(value) is VerifiedIbkrCaptureQualification
        and getattr(value, "_verifier_token", None) is _VERIFIED_IBKR_CAPTURE_QUALIFICATION_TOKEN
    )


VerifiedB3Qualification = VerifiedIbkrCaptureQualification
