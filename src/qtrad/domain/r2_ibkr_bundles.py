"""Source-specific R2.H software-verification contract for IBKR history."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, cast

from qtrad.domain.events import JsonValue
from qtrad.domain.market_data import MarketDataSourceClass
from qtrad.domain.r2_bundles import ArtifactReference
from qtrad.domain.r2_ibkr_historical import IBKR_HISTORICAL_PROFILE

IBKR_SOFTWARE_VERIFICATION_CONTRACT = "qtrad-r2-software-verification-v2"
IBKR_SOFTWARE_VERIFICATION_SCHEMA_VERSION = 2


@dataclass(frozen=True, slots=True)
class R2IbkrHistoricalSoftwareVerificationBundle:
    """Authenticated, implementation-only R2.H bundle for one IBKR profile."""

    market_data_source_class: MarketDataSourceClass
    representative_profile: str
    synthetic_oof_bundle: ArtifactReference
    representative_oof_bundle: ArtifactReference
    synthetic_selection: ArtifactReference
    representative_selection: ArtifactReference
    application_identity: str
    python_identity: str
    numpy_identity: str
    sklearn_identity: str
    representative_integration_ready: str
    evidence_disposition: str
    research_disposition: str
    bundle_id: str

    CONTRACT: ClassVar[str] = IBKR_SOFTWARE_VERIFICATION_CONTRACT
    SCHEMA_VERSION: ClassVar[int] = IBKR_SOFTWARE_VERIFICATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.market_data_source_class is not MarketDataSourceClass.IBKR_HISTORICAL_RESEARCH:
            raise ValueError("IBKR software verification requires IBKR_HISTORICAL_RESEARCH")
        if self.representative_profile != IBKR_HISTORICAL_PROFILE:
            raise ValueError("IBKR software verification profile is unsupported")
        references = (
            self.synthetic_oof_bundle,
            self.representative_oof_bundle,
            self.synthetic_selection,
            self.representative_selection,
        )
        if any(item.path == "manifest.json" for item in references):
            raise ValueError("IBKR software child path is reserved")
        if len({item.path for item in references}) != len(references):
            raise ValueError("IBKR software children must not duplicate paths")
        if len({(item.contract, item.semantic_id) for item in references}) != len(references):
            raise ValueError("IBKR software children must not duplicate identities")
        for value, field in (
            (self.application_identity, "application identity"),
            (self.python_identity, "Python identity"),
            (self.numpy_identity, "NumPy identity"),
            (self.sklearn_identity, "scikit-learn identity"),
            (self.bundle_id, "software bundle ID"),
        ):
            if not value:
                raise ValueError(f"{field} must be non-empty")
        if self.representative_integration_ready != "READY":
            raise ValueError("IBKR software verification requires READY integration")
        if self.evidence_disposition != "IMPLEMENTATION_EVIDENCE_ONLY":
            raise ValueError("IBKR software evidence disposition is not implementation-only")
        if self.research_disposition != "RESEARCH_EVIDENCE_PENDING":
            raise ValueError("IBKR software research disposition must remain pending")
        if self.bundle_id != _semantic_id(self.semantic_json()):
            raise ValueError("IBKR software bundle ID does not authenticate its content")

    @classmethod
    def create(cls, **values: Any) -> R2IbkrHistoricalSoftwareVerificationBundle:
        expected = {
            "market_data_source_class",
            "representative_profile",
            "synthetic_oof_bundle",
            "representative_oof_bundle",
            "synthetic_selection",
            "representative_selection",
            "application_identity",
            "python_identity",
            "numpy_identity",
            "sklearn_identity",
            "representative_integration_ready",
            "evidence_disposition",
            "research_disposition",
        }
        if set(values) != expected:
            raise ValueError("IBKR software bundle create arguments are incomplete or unexpected")
        semantic = {
            "contract": cls.CONTRACT,
            "schema_version": cls.SCHEMA_VERSION,
            **{
                key: value.as_json()
                if isinstance(value, ArtifactReference)
                else (value.value if isinstance(value, MarketDataSourceClass) else value)
                for key, value in values.items()
            },
        }
        return cls(**values, bundle_id=_semantic_id(semantic))

    @classmethod
    def from_json(cls, value: object) -> R2IbkrHistoricalSoftwareVerificationBundle:
        if not isinstance(value, dict):
            raise ValueError("IBKR software bundle has unknown or missing fields")
        raw = cast(dict[str, object], value)
        expected = {
            "contract",
            "schema_version",
            "market_data_source_class",
            "representative_profile",
            "synthetic_oof_bundle",
            "representative_oof_bundle",
            "synthetic_selection",
            "representative_selection",
            "application_identity",
            "python_identity",
            "numpy_identity",
            "sklearn_identity",
            "representative_integration_ready",
            "evidence_disposition",
            "research_disposition",
            "bundle_id",
        }
        if set(raw) != expected:
            raise ValueError("IBKR software bundle has unknown or missing fields")
        if raw["contract"] != cls.CONTRACT or raw["schema_version"] != cls.SCHEMA_VERSION:
            raise ValueError("IBKR software bundle contract is unsupported")
        source = raw["market_data_source_class"]
        if not isinstance(source, str):
            raise TypeError("IBKR software source class must be a string")
        return cls(
            market_data_source_class=MarketDataSourceClass(source),
            representative_profile=_string(raw["representative_profile"]),
            synthetic_oof_bundle=ArtifactReference.from_json(raw["synthetic_oof_bundle"]),
            representative_oof_bundle=ArtifactReference.from_json(raw["representative_oof_bundle"]),
            synthetic_selection=ArtifactReference.from_json(raw["synthetic_selection"]),
            representative_selection=ArtifactReference.from_json(raw["representative_selection"]),
            application_identity=_string(raw["application_identity"]),
            python_identity=_string(raw["python_identity"]),
            numpy_identity=_string(raw["numpy_identity"]),
            sklearn_identity=_string(raw["sklearn_identity"]),
            representative_integration_ready=_string(raw["representative_integration_ready"]),
            evidence_disposition=_string(raw["evidence_disposition"]),
            research_disposition=_string(raw["research_disposition"]),
            bundle_id=_string(raw["bundle_id"]),
        )

    def semantic_json(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": self.SCHEMA_VERSION,
            "market_data_source_class": self.market_data_source_class.value,
            "representative_profile": self.representative_profile,
            "synthetic_oof_bundle": self.synthetic_oof_bundle.as_json(),
            "representative_oof_bundle": self.representative_oof_bundle.as_json(),
            "synthetic_selection": self.synthetic_selection.as_json(),
            "representative_selection": self.representative_selection.as_json(),
            "application_identity": self.application_identity,
            "python_identity": self.python_identity,
            "numpy_identity": self.numpy_identity,
            "sklearn_identity": self.sklearn_identity,
            "representative_integration_ready": self.representative_integration_ready,
            "evidence_disposition": self.evidence_disposition,
            "research_disposition": self.research_disposition,
        }

    def as_json(self) -> dict[str, JsonValue]:
        return {**self.semantic_json(), "bundle_id": self.bundle_id}


def _semantic_id(value: object) -> str:
    import hashlib
    import json

    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("expected a string")
    return value


__all__ = [
    "IBKR_SOFTWARE_VERIFICATION_CONTRACT",
    "IBKR_SOFTWARE_VERIFICATION_SCHEMA_VERSION",
    "R2IbkrHistoricalSoftwareVerificationBundle",
]
