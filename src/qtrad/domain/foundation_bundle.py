"""Semantic and physical identities for the R1 foundation handoff."""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import PurePosixPath
from typing import ClassVar, cast

from qtrad.domain.events import JsonValue, to_json_value
from qtrad.domain.foundation import HorizonCoverageSummary
from qtrad.domain.market_data import MarketDataSourceClass
from qtrad.domain.time import require_utc

FOUNDATION_BUNDLE_CONTRACT = "qtrad-research-foundation-bundle-v3"
FOUNDATION_BUNDLE_SCHEMA_VERSION = 3
FOUNDATION_VERIFICATION_RECEIPT_CONTRACT = "qtrad-research-foundation-verification-v1"
FOUNDATION_VERIFICATION_RECEIPT_SCHEMA_VERSION = 1
AVAILABILITY_EVIDENCE_CONTRACT = "qtrad-research-availability-evidence-v1"

# The source observation manifest is an immediate parent owned by the observation
# boundary.  It contributes semantic identity but not this boundary's closure.
_PARENT_CHILDREN = frozenset({"observations"})


@dataclass(frozen=True, slots=True)
class ArtifactReference:
    """The semantic and physical identity needed to authenticate one child."""

    name: str
    contract: str
    schema_version: int
    dataset_id: str
    manifest_id: str
    manifest_sha256: str
    manifest_path: str
    row_count: int

    def __post_init__(self) -> None:
        if not self.name or not self.contract or self.schema_version <= 0:
            raise ValueError("foundation child reference contract is invalid")
        _require_sha256(self.dataset_id, "foundation child dataset ID")
        _require_sha256(self.manifest_sha256, "foundation child manifest hash")
        if (
            len(self.manifest_id) != 24
            or self.manifest_id != self.manifest_sha256[:24]
            or any(character not in "0123456789abcdef" for character in self.manifest_id)
        ):
            raise ValueError("foundation child manifest ID is invalid")
        if self.row_count < 0:
            raise ValueError("foundation child row count must not be negative")
        path = PurePosixPath(self.manifest_path)
        if (
            path.is_absolute()
            or len(path.parts) != 2
            or path.parts[0] not in {"manifests", "foundation-manifests"}
            or path.name != f"{self.manifest_id}.json"
        ):
            raise ValueError("foundation child manifest path is unsafe or inconsistent")

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "name": self.name,
            "contract": self.contract,
            "schema_version": self.schema_version,
            "dataset_id": self.dataset_id,
            "manifest_id": self.manifest_id,
            "manifest_sha256": self.manifest_sha256,
            "manifest_path": self.manifest_path,
            "row_count": self.row_count,
        }


@dataclass(frozen=True, slots=True)
class FoundationVerificationReceipt:
    """Create-only proof of one explicit, independent R1 semantic verification."""

    foundation_id: str
    closure_id: str
    bundle_manifest_sha256: str
    child_semantic_ids: Mapping[str, str]
    verifier_contract: str
    verifier_version: int
    completed_checks: tuple[str, ...]
    verifier_identity: str
    verification_id: str
    CONTRACT: ClassVar[str] = FOUNDATION_VERIFICATION_RECEIPT_CONTRACT
    SCHEMA_VERSION: ClassVar[int] = FOUNDATION_VERIFICATION_RECEIPT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_sha256(self.foundation_id, "foundation verification foundation ID")
        _require_sha256(self.closure_id, "foundation verification closure ID")
        _require_sha256(self.bundle_manifest_sha256, "foundation bundle manifest hash")
        _require_sha256(self.verifier_identity, "foundation verifier identity")
        if not self.verifier_contract or self.verifier_version <= 0:
            raise ValueError("foundation verifier contract is invalid")
        if not self.completed_checks or len(set(self.completed_checks)) != len(
            self.completed_checks
        ):
            raise ValueError("foundation verification check set is invalid")
        if not self.child_semantic_ids or set(self.child_semantic_ids) != {
            "configuration",
            "observations",
            "availability",
            "panel",
            "targets",
            "folds",
            "forecasts",
            "projections",
        }:
            raise ValueError("foundation verification child semantic IDs are incomplete")
        for name, value in self.child_semantic_ids.items():
            _require_sha256(value, f"foundation verification {name} semantic ID")
        if self.verification_id != _verification_hash(self):
            raise ValueError("foundation verification ID does not match its receipt")

    @classmethod
    def create(
        cls,
        *,
        foundation_id: str,
        closure_id: str,
        bundle_manifest_sha256: str,
        child_semantic_ids: Mapping[str, str],
        verifier_contract: str,
        verifier_version: int,
        completed_checks: Sequence[str],
        verifier_identity: str,
    ) -> "FoundationVerificationReceipt":
        unbound: dict[str, JsonValue] = {
            "contract": cls.CONTRACT,
            "schema_version": cls.SCHEMA_VERSION,
            "foundation_id": foundation_id,
            "closure_id": closure_id,
            "bundle_manifest_sha256": bundle_manifest_sha256,
            "child_semantic_ids": dict(child_semantic_ids),
            "verifier_contract": verifier_contract,
            "verifier_version": verifier_version,
            "completed_checks": list(completed_checks),
            "verifier_identity": verifier_identity,
        }
        return cls(
            foundation_id=foundation_id,
            closure_id=closure_id,
            bundle_manifest_sha256=bundle_manifest_sha256,
            child_semantic_ids=dict(child_semantic_ids),
            verifier_contract=verifier_contract,
            verifier_version=verifier_version,
            completed_checks=tuple(completed_checks),
            verifier_identity=verifier_identity,
            verification_id=_hash_json(unbound),
        )

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": self.SCHEMA_VERSION,
            "foundation_id": self.foundation_id,
            "closure_id": self.closure_id,
            "bundle_manifest_sha256": self.bundle_manifest_sha256,
            "child_semantic_ids": dict(self.child_semantic_ids),
            "verifier_contract": self.verifier_contract,
            "verifier_version": self.verifier_version,
            "completed_checks": list(self.completed_checks),
            "verifier_identity": self.verifier_identity,
            "verification_id": self.verification_id,
        }


@dataclass(frozen=True, slots=True)
class FoundationBundle:
    """R1 manifest with distinct semantic and boundary-owned closure identities."""

    configuration: ArtifactReference
    observations: ArtifactReference
    availability: ArtifactReference
    panel: ArtifactReference
    targets: ArtifactReference
    folds: ArtifactReference
    forecasts: ArtifactReference
    ordered_instruments: tuple[str, ...]
    range_start: datetime
    range_end: datetime
    coverage: tuple[HorizonCoverageSummary, ...]
    build_summary: Mapping[str, JsonValue]
    foundation_id: str
    closure_id: str
    market_data_source_class: MarketDataSourceClass = MarketDataSourceClass.IG_NATIVE_CAPTURE
    projections: tuple[ArtifactReference, ...] = ()

    CONTRACT: ClassVar[str] = FOUNDATION_BUNDLE_CONTRACT
    SCHEMA_VERSION: ClassVar[int] = FOUNDATION_BUNDLE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        require_utc(self.range_start, "foundation bundle range_start")
        require_utc(self.range_end, "foundation bundle range_end")
        if self.range_end <= self.range_start:
            raise ValueError("foundation bundle range must be positive")
        if not self.ordered_instruments or len(set(self.ordered_instruments)) != len(
            self.ordered_instruments
        ):
            raise ValueError("foundation bundle instruments must be non-empty and unique")
        if tuple(sorted(self.coverage, key=lambda item: item.horizon)) != self.coverage:
            raise ValueError("foundation coverage summaries must use horizon ordering")
        _verify_reference_contracts(self)
        if self.foundation_id != _foundation_hash(self):
            raise ValueError("foundation ID does not match semantic content")
        if self.closure_id != _closure_hash(self):
            raise ValueError("foundation closure ID does not match owned children")

    @classmethod
    def create(
        cls,
        *,
        configuration: ArtifactReference,
        observations: ArtifactReference,
        availability: ArtifactReference,
        panel: ArtifactReference,
        targets: ArtifactReference,
        folds: ArtifactReference,
        forecasts: ArtifactReference,
        ordered_instruments: Sequence[str],
        range_start: datetime,
        range_end: datetime,
        coverage: Sequence[HorizonCoverageSummary],
        build_summary: Mapping[str, JsonValue],
        market_data_source_class: MarketDataSourceClass = MarketDataSourceClass.IG_NATIVE_CAPTURE,
        projections: Sequence[ArtifactReference] = (),
    ) -> "FoundationBundle":
        unbound = _UnboundBundle(
            configuration=configuration,
            observations=observations,
            availability=availability,
            panel=panel,
            targets=targets,
            folds=folds,
            forecasts=forecasts,
            ordered_instruments=tuple(ordered_instruments),
            range_start=range_start,
            range_end=range_end,
            coverage=tuple(sorted(coverage, key=lambda item: item.horizon)),
            build_summary=dict(build_summary),
            market_data_source_class=market_data_source_class,
            projections=tuple(sorted(projections, key=lambda item: item.name)),
        )
        foundation_id = _foundation_hash(unbound)
        closure_id = _closure_hash(unbound)
        return cls(
            configuration=unbound.configuration,
            observations=unbound.observations,
            availability=unbound.availability,
            panel=unbound.panel,
            targets=unbound.targets,
            folds=unbound.folds,
            forecasts=unbound.forecasts,
            ordered_instruments=unbound.ordered_instruments,
            range_start=unbound.range_start,
            range_end=unbound.range_end,
            coverage=unbound.coverage,
            build_summary=unbound.build_summary,
            foundation_id=foundation_id,
            closure_id=closure_id,
            market_data_source_class=unbound.market_data_source_class,
            projections=unbound.projections,
        )

    @property
    def core_children(self) -> tuple[ArtifactReference, ...]:
        return (
            self.configuration,
            self.observations,
            self.availability,
            self.panel,
            self.targets,
            self.folds,
            self.forecasts,
        )

    @property
    def children(self) -> tuple[ArtifactReference, ...]:
        """All references owned by the R1 manifest, including projections."""
        return self.core_children + self.projections

    @property
    def semantic_child_ids(self) -> dict[str, str]:
        return {
            "configuration": self.configuration.dataset_id,
            "observations": self.observations.dataset_id,
            "availability": self.availability.dataset_id,
            "panel": self.panel.dataset_id,
            "targets": self.targets.dataset_id,
            "folds": self.folds.dataset_id,
            "forecasts": self.forecasts.dataset_id,
            "projections": _hash_json(
                [(reference.name, reference.dataset_id) for reference in self.projections]
            ),
        }

    def as_json(self) -> dict[str, JsonValue]:
        payload = _bundle_payload(self)
        payload["foundation_id"] = self.foundation_id
        payload["closure_id"] = self.closure_id
        return cast(dict[str, JsonValue], to_json_value(payload))


@dataclass(frozen=True, slots=True)
class _UnboundBundle:
    configuration: ArtifactReference
    observations: ArtifactReference
    availability: ArtifactReference
    panel: ArtifactReference
    targets: ArtifactReference
    folds: ArtifactReference
    forecasts: ArtifactReference
    ordered_instruments: tuple[str, ...]
    range_start: datetime
    range_end: datetime
    coverage: tuple[HorizonCoverageSummary, ...]
    build_summary: Mapping[str, JsonValue]
    market_data_source_class: MarketDataSourceClass
    projections: tuple[ArtifactReference, ...]


def _verify_reference_contracts(bundle: FoundationBundle) -> None:
    expected = {
        "configuration": "qtrad-research-foundation-config-v1",
        "observations": "qtrad-research-observations-v1",
        "availability": AVAILABILITY_EVIDENCE_CONTRACT,
        "panel": "qtrad-research-panel-v1",
        "targets": "qtrad-research-targets-v1",
        "folds": "qtrad-research-folds-v1",
        "forecasts": "qtrad-research-forecasts-v1",
    }
    if {child.name for child in bundle.core_children} != set(expected):
        raise ValueError("foundation bundle child names are incomplete or duplicated")
    for child in bundle.core_children:
        if child.contract != expected[child.name]:
            raise ValueError(f"foundation {child.name} child contract is unsupported")
    if len({child.name for child in bundle.projections}) != len(bundle.projections):
        raise ValueError("foundation projection names are duplicated")
    if any(not child.name for child in bundle.projections):
        raise ValueError("foundation projection name is empty")


def _bundle_payload(bundle: FoundationBundle | _UnboundBundle) -> dict[str, object]:
    return {
        "contract": FOUNDATION_BUNDLE_CONTRACT,
        "schema_version": FOUNDATION_BUNDLE_SCHEMA_VERSION,
        "children": {
            child.name: child.as_json()
            for child in (
                bundle.configuration,
                bundle.observations,
                bundle.availability,
                bundle.panel,
                bundle.targets,
                bundle.folds,
                bundle.forecasts,
            )
        },
        "projections": [child.as_json() for child in bundle.projections],
        "ordered_instruments": list(bundle.ordered_instruments),
        "range_start": bundle.range_start.isoformat(),
        "range_end": bundle.range_end.isoformat(),
        "coverage": [summary.as_json() for summary in bundle.coverage],
        "build_summary": dict(bundle.build_summary),
        "source_class": bundle.market_data_source_class.value,
    }


def _semantic_payload(bundle: FoundationBundle | _UnboundBundle) -> dict[str, object]:
    return {
        "contract": FOUNDATION_BUNDLE_CONTRACT,
        "schema_version": FOUNDATION_BUNDLE_SCHEMA_VERSION,
        "source_class": bundle.market_data_source_class.value,
        "configuration_id": bundle.configuration.dataset_id,
        "observation_dataset_id": bundle.observations.dataset_id,
        "availability_id": bundle.availability.dataset_id,
        "panel_dataset_id": bundle.panel.dataset_id,
        "target_dataset_id": bundle.targets.dataset_id,
        "fold_dataset_id": bundle.folds.dataset_id,
        "forecast_dataset_id": bundle.forecasts.dataset_id,
        "projection_dataset_ids": [
            [reference.name, reference.dataset_id] for reference in bundle.projections
        ],
        "ordered_instruments": list(bundle.ordered_instruments),
        "range_start": bundle.range_start.isoformat(),
        "range_end": bundle.range_end.isoformat(),
        "coverage": [summary.as_json() for summary in bundle.coverage],
    }


def _closure_payload(bundle: FoundationBundle | _UnboundBundle) -> list[dict[str, JsonValue]]:
    core_children = (
        bundle.configuration,
        bundle.observations,
        bundle.availability,
        bundle.panel,
        bundle.targets,
        bundle.folds,
        bundle.forecasts,
    )
    return [
        {
            "name": reference.name,
            "manifest_id": reference.manifest_id,
            "manifest_sha256": reference.manifest_sha256,
            "manifest_path": reference.manifest_path,
            "row_count": reference.row_count,
        }
        for reference in (*core_children, *bundle.projections)
        if reference.name not in _PARENT_CHILDREN
    ]


def _foundation_hash(bundle: FoundationBundle | _UnboundBundle) -> str:
    return _hash_json(_semantic_payload(bundle))


def _closure_hash(bundle: FoundationBundle | _UnboundBundle) -> str:
    return _hash_json(_closure_payload(bundle))


def _verification_hash(receipt: FoundationVerificationReceipt) -> str:
    payload = receipt.as_json()
    payload.pop("verification_id", None)
    return _hash_json(payload)


def _hash_json(value: object) -> str:
    canonical = to_json_value(value)
    return sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _require_sha256(value: str, field: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} must be a lower-case SHA-256")
