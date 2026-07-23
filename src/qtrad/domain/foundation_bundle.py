"""Thin, hash-bound references for independently manifested R1 artefacts."""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import PurePosixPath
from typing import ClassVar, cast

from qtrad.domain.events import JsonValue, to_json_value
from qtrad.domain.foundation import HorizonCoverageSummary
from qtrad.domain.time import require_utc

FOUNDATION_BUNDLE_CONTRACT = "qtrad-research-foundation-bundle-v1"
AVAILABILITY_EVIDENCE_CONTRACT = "qtrad-research-availability-evidence-v1"


@dataclass(frozen=True, slots=True)
class ArtifactReference:
    """The semantic and physical identity needed to verify one child artefact."""

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
class FoundationBundle:
    """A bounded top-level manifest containing no child dataset rows."""

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
    bundle_id: str

    CONTRACT: ClassVar[str] = FOUNDATION_BUNDLE_CONTRACT
    SCHEMA_VERSION: ClassVar[int] = 1

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
        if self.bundle_id != _bundle_hash(self):
            raise ValueError("foundation bundle ID does not match its references")

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
        )
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
            bundle_id=_bundle_hash(unbound),
        )

    @property
    def children(self) -> tuple[ArtifactReference, ...]:
        return (
            self.configuration,
            self.observations,
            self.availability,
            self.panel,
            self.targets,
            self.folds,
            self.forecasts,
        )

    def as_json(self) -> dict[str, JsonValue]:
        payload = _bundle_payload(self)
        payload["bundle_id"] = self.bundle_id
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
    if {child.name for child in bundle.children} != set(expected):
        raise ValueError("foundation bundle child names are incomplete or duplicated")
    for child in bundle.children:
        if child.contract != expected[child.name]:
            raise ValueError(f"foundation {child.name} child contract is unsupported")


def _bundle_payload(bundle: FoundationBundle | _UnboundBundle) -> dict[str, object]:
    return {
        "contract": FOUNDATION_BUNDLE_CONTRACT,
        "schema_version": 1,
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
        "ordered_instruments": list(bundle.ordered_instruments),
        "range_start": bundle.range_start.isoformat(),
        "range_end": bundle.range_end.isoformat(),
        "coverage": [summary.as_json() for summary in bundle.coverage],
        "build_summary": dict(bundle.build_summary),
    }


def _bundle_hash(bundle: FoundationBundle | _UnboundBundle) -> str:
    canonical = to_json_value(_bundle_payload(bundle))
    return sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _require_sha256(value: str, field: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} must be a lower-case SHA-256")
