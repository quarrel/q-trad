"""Thin, identity-bearing R2 replay bundle contracts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, ClassVar, cast

from qtrad.domain.events import JsonValue, to_json_value
from qtrad.domain.market_data import MarketDataSourceClass
from qtrad.domain.r2_readiness import EvidenceClass

R2_FORECAST_MANIFEST_CONTRACT = "qtrad-r2-forecast-manifest-v1"
R2_OOF_BUNDLE_CONTRACT = "qtrad-r2-oof-bundle-v2"


def _semantic_id(value: object) -> str:
    return sha256(
        json.dumps(to_json_value(value), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _sha256(value: str, field: str) -> None:
    if len(value) not in (24, 64) or any(c not in "0123456789abcdef" for c in value):
        raise ValueError(
            f"{field} must be a lowercase SHA-256 identifier (24 or 64 hex characters)"
        )


@dataclass(frozen=True, slots=True)
class ArtifactReference:
    """A safe relative reference to one independently authenticated child."""

    contract: str
    semantic_id: str
    path: str
    sha256: str

    def __post_init__(self) -> None:
        if not self.contract or not self.semantic_id or not self.path:
            raise ValueError("artifact references require contract, identity and path")
        _sha256(self.semantic_id, "artifact semantic ID")
        _sha256(self.sha256, "artifact digest")
        if self.path.startswith(("/", "\\")) or "\\" in self.path:
            raise ValueError("artifact reference must use a relative POSIX path")
        parts = self.path.split("/")
        if any(part in ("", ".", "..") for part in parts):
            raise ValueError("artifact reference contains an unsafe path component")

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "contract": self.contract,
            "semantic_id": self.semantic_id,
            "path": self.path,
            "sha256": self.sha256,
        }

    def semantic_json(self) -> dict[str, JsonValue]:
        return {
            "contract": self.contract,
            "semantic_id": self.semantic_id,
        }

    def closure_json(self) -> dict[str, JsonValue]:
        return self.as_json()

    @classmethod
    def from_json(cls, value: object) -> ArtifactReference:
        if not isinstance(value, dict):
            raise ValueError("artifact reference has unknown or missing fields")
        raw = cast(dict[str, object], value)
        if set(raw) != {
            "contract",
            "semantic_id",
            "path",
            "sha256",
        }:
            raise ValueError("artifact reference has unknown or missing fields")
        return cls(
            contract=_string(raw["contract"]),
            semantic_id=_string(raw["semantic_id"]),
            path=_string(raw["path"]),
            sha256=_string(raw["sha256"]),
        )


def _ordered_refs(refs: tuple[ArtifactReference, ...]) -> tuple[ArtifactReference, ...]:
    if len({(item.contract, item.semantic_id) for item in refs}) != len(refs):
        raise ValueError("bundle children must not contain duplicate identities")
    if len({item.path for item in refs}) != len(refs):
        raise ValueError("bundle children must not contain duplicate paths")
    if any(item.path == "manifest.json" for item in refs):
        raise ValueError("bundle child path is reserved for the bundle manifest")
    return tuple(sorted(refs, key=lambda item: (item.contract, item.semantic_id, item.path)))


@dataclass(frozen=True, slots=True)
class R2ForecastManifest:
    forecast_dataset_id: str
    experiment_configuration_id: str
    source_class: MarketDataSourceClass
    evidence_class: EvidenceClass
    forecast_child: ArtifactReference
    manifest_id: str

    CONTRACT: ClassVar[str] = R2_FORECAST_MANIFEST_CONTRACT
    SCHEMA_VERSION: ClassVar[int] = 1

    def __post_init__(self) -> None:
        for value, field in (
            (self.forecast_dataset_id, "forecast dataset ID"),
            (self.experiment_configuration_id, "experiment configuration ID"),
            (self.manifest_id, "forecast manifest ID"),
        ):
            _sha256(value, field)
        if self.forecast_child.contract != "qtrad-research-forecasts-v1":
            raise ValueError("forecast manifest child must be the canonical forecast dataset")
        if self.manifest_id != _semantic_id(self.semantic_json()):
            raise ValueError("forecast manifest ID does not authenticate its content")

    @classmethod
    def create(
        cls,
        *,
        forecast_dataset_id: str,
        experiment_configuration_id: str,
        source_class: MarketDataSourceClass,
        evidence_class: EvidenceClass,
        forecast_child: ArtifactReference,
    ) -> R2ForecastManifest:
        semantic = {
            "contract": cls.CONTRACT,
            "schema_version": cls.SCHEMA_VERSION,
            "forecast_dataset_id": forecast_dataset_id,
            "experiment_configuration_id": experiment_configuration_id,
            "source_class": source_class.value,
            "evidence_class": evidence_class.value,
            "forecast_child": forecast_child.semantic_json(),
        }
        return cls(
            forecast_dataset_id=forecast_dataset_id,
            experiment_configuration_id=experiment_configuration_id,
            source_class=source_class,
            evidence_class=evidence_class,
            forecast_child=forecast_child,
            manifest_id=_semantic_id(semantic),
        )

    def semantic_json(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": self.SCHEMA_VERSION,
            "forecast_dataset_id": self.forecast_dataset_id,
            "experiment_configuration_id": self.experiment_configuration_id,
            "source_class": self.source_class.value,
            "evidence_class": self.evidence_class.value,
            "forecast_child": self.forecast_child.semantic_json(),
        }

    def as_json(self) -> dict[str, JsonValue]:
        return {
            **self.semantic_json(),
            "forecast_child": self.forecast_child.as_json(),
            "manifest_id": self.manifest_id,
        }

    @classmethod
    def from_json(cls, value: object) -> R2ForecastManifest:
        if not isinstance(value, dict):
            raise ValueError("R2 forecast manifest has unknown or missing fields")
        raw = cast(dict[str, object], value)
        if set(raw) != {
            "contract",
            "schema_version",
            "forecast_dataset_id",
            "experiment_configuration_id",
            "source_class",
            "evidence_class",
            "forecast_child",
            "manifest_id",
        }:
            raise ValueError("R2 forecast manifest has unknown or missing fields")
        if raw["contract"] != cls.CONTRACT or raw["schema_version"] != cls.SCHEMA_VERSION:
            raise ValueError("R2 forecast manifest contract is unsupported")
        return cls(
            forecast_dataset_id=_string(raw["forecast_dataset_id"]),
            experiment_configuration_id=_string(raw["experiment_configuration_id"]),
            source_class=MarketDataSourceClass(_string(raw["source_class"])),
            evidence_class=EvidenceClass(_string(raw["evidence_class"])),
            forecast_child=ArtifactReference.from_json(raw["forecast_child"]),
            manifest_id=_string(raw["manifest_id"]),
        )


@dataclass(frozen=True, slots=True)
class R2OofBundle:
    foundation_bundle_id: str
    experiment_configuration_id: str
    source_class: MarketDataSourceClass
    evidence_class: EvidenceClass
    feature_children: tuple[ArtifactReference, ...]
    preprocessing_children: tuple[ArtifactReference, ...]
    fit_children: tuple[ArtifactReference, ...]
    forecast_manifests: tuple[ArtifactReference, ...]
    coverage_children: tuple[ArtifactReference, ...]
    evaluation_children: tuple[ArtifactReference, ...]
    oof_id: str
    closure_id: str
    holdout_target_source: ArtifactReference | None = None

    CONTRACT: ClassVar[str] = R2_OOF_BUNDLE_CONTRACT
    SCHEMA_VERSION: ClassVar[int] = 2

    def __post_init__(self) -> None:
        for value, field in (
            (self.foundation_bundle_id, "foundation bundle ID"),
            (self.experiment_configuration_id, "experiment configuration ID"),
            (self.oof_id, "OOF semantic ID"),
            (self.closure_id, "OOF closure ID"),
        ):
            _sha256(value, field)
        references = (
            *self.feature_children,
            *self.preprocessing_children,
            *self.fit_children,
            *self.forecast_manifests,
            *self.coverage_children,
            *self.evaluation_children,
            *((self.holdout_target_source,) if self.holdout_target_source is not None else ()),
        )
        for refs in (
            self.feature_children,
            self.preprocessing_children,
            self.fit_children,
            self.forecast_manifests,
            self.coverage_children,
            self.evaluation_children,
        ):
            if refs != _ordered_refs(refs):
                raise ValueError("OOF bundle children must use deterministic ordering")
        if any(item.path in {"manifest.json", "selection.json"} for item in references):
            raise ValueError("OOF bundle child path is reserved")
        if len({(item.contract, item.semantic_id) for item in references}) != len(references):
            raise ValueError("OOF bundle children must not contain duplicate identities")
        if len({item.path for item in references}) != len(references):
            raise ValueError("OOF bundle children must not contain duplicate paths")
        if self.holdout_target_source is not None and self.holdout_target_source.contract != (
            "qtrad-r2-holdout-target-source-v1"
        ):
            raise ValueError("OOF holdout target source has an unexpected contract")
        if self.oof_id != _semantic_id(self.semantic_json()):
            raise ValueError("OOF semantic ID does not authenticate its content")
        if self.closure_id != _semantic_id(self.closure_json()):
            raise ValueError("OOF closure ID does not authenticate its content")

    @classmethod
    def create(cls, **values: Any) -> R2OofBundle:
        expected = {
            "foundation_bundle_id",
            "experiment_configuration_id",
            "source_class",
            "evidence_class",
            "feature_children",
            "preprocessing_children",
            "fit_children",
            "forecast_manifests",
            "coverage_children",
            "evaluation_children",
        }
        if not set(values) <= expected | {"holdout_target_source"} or not expected <= set(values):
            raise ValueError("R2 OOF bundle create arguments are incomplete or unexpected")
        refs: dict[str, tuple[ArtifactReference, ...]] = {
            key: _ordered_refs(tuple(value))
            for key, value in values.items()
            if key.endswith("_children") or key == "forecast_manifests"
        }
        semantic = {
            "contract": cls.CONTRACT,
            "schema_version": cls.SCHEMA_VERSION,
            "experiment_configuration_id": values["experiment_configuration_id"],
            "source_class": values["source_class"].value,
            "evidence_class": values["evidence_class"].value,
            "holdout_target_source": (
                None
                if values.get("holdout_target_source") is None
                else values["holdout_target_source"].semantic_json()
            ),
            **{key: [item.semantic_json() for item in value] for key, value in refs.items()},
        }
        oof_id = _semantic_id(semantic)
        closure = {
            **semantic,
            "foundation_bundle_id": values["foundation_bundle_id"],
            "oof_id": oof_id,
            "holdout_target_source": (
                None
                if values.get("holdout_target_source") is None
                else values["holdout_target_source"].as_json()
            ),
            **{key: [item.as_json() for item in value] for key, value in refs.items()},
        }
        return cls(
            foundation_bundle_id=values["foundation_bundle_id"],
            experiment_configuration_id=values["experiment_configuration_id"],
            source_class=values["source_class"],
            evidence_class=values["evidence_class"],
            feature_children=refs["feature_children"],
            preprocessing_children=refs["preprocessing_children"],
            fit_children=refs["fit_children"],
            forecast_manifests=refs["forecast_manifests"],
            coverage_children=refs["coverage_children"],
            evaluation_children=refs["evaluation_children"],
            holdout_target_source=values.get("holdout_target_source"),
            oof_id=oof_id,
            closure_id=_semantic_id(closure),
        )

    @classmethod
    def from_json(cls, value: object) -> R2OofBundle:
        if not isinstance(value, dict):
            raise ValueError("R2 OOF bundle has unknown or missing fields")
        raw = cast(dict[str, object], value)
        expected = {
            "contract",
            "schema_version",
            "foundation_bundle_id",
            "experiment_configuration_id",
            "source_class",
            "evidence_class",
            "feature_children",
            "preprocessing_children",
            "fit_children",
            "forecast_manifests",
            "coverage_children",
            "evaluation_children",
            "holdout_target_source",
            "oof_id",
            "closure_id",
        }
        if set(raw) != expected:
            raise ValueError("R2 OOF bundle has unknown or missing fields")
        if raw["contract"] != cls.CONTRACT or raw["schema_version"] != cls.SCHEMA_VERSION:
            raise ValueError("R2 OOF bundle contract is unsupported")

        def references(key: str) -> tuple[ArtifactReference, ...]:
            items = raw[key]
            if not isinstance(items, list):
                raise TypeError("R2 OOF child references must be arrays")
            return _ordered_refs(
                tuple(ArtifactReference.from_json(item) for item in cast(list[object], items))
            )

        return cls(
            foundation_bundle_id=_string(raw["foundation_bundle_id"]),
            experiment_configuration_id=_string(raw["experiment_configuration_id"]),
            source_class=MarketDataSourceClass(_string(raw["source_class"])),
            evidence_class=EvidenceClass(_string(raw["evidence_class"])),
            feature_children=references("feature_children"),
            preprocessing_children=references("preprocessing_children"),
            fit_children=references("fit_children"),
            forecast_manifests=references("forecast_manifests"),
            coverage_children=references("coverage_children"),
            evaluation_children=references("evaluation_children"),
            holdout_target_source=(
                None
                if raw["holdout_target_source"] is None
                else ArtifactReference.from_json(raw["holdout_target_source"])
            ),
            oof_id=_string(raw["oof_id"]),
            closure_id=_string(raw["closure_id"]),
        )

    def semantic_json(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": self.SCHEMA_VERSION,
            "experiment_configuration_id": self.experiment_configuration_id,
            "source_class": self.source_class.value,
            "evidence_class": self.evidence_class.value,
            "feature_children": [item.semantic_json() for item in self.feature_children],
            "preprocessing_children": [
                item.semantic_json() for item in self.preprocessing_children
            ],
            "fit_children": [item.semantic_json() for item in self.fit_children],
            "forecast_manifests": [item.semantic_json() for item in self.forecast_manifests],
            "coverage_children": [item.semantic_json() for item in self.coverage_children],
            "evaluation_children": [item.semantic_json() for item in self.evaluation_children],
            "holdout_target_source": (
                None
                if self.holdout_target_source is None
                else self.holdout_target_source.semantic_json()
            ),
        }

    def closure_json(self) -> dict[str, JsonValue]:
        return {
            **self.semantic_json(),
            "foundation_bundle_id": self.foundation_bundle_id,
            "oof_id": self.oof_id,
            "feature_children": [item.as_json() for item in self.feature_children],
            "preprocessing_children": [item.as_json() for item in self.preprocessing_children],
            "fit_children": [item.as_json() for item in self.fit_children],
            "forecast_manifests": [item.as_json() for item in self.forecast_manifests],
            "coverage_children": [item.as_json() for item in self.coverage_children],
            "evaluation_children": [item.as_json() for item in self.evaluation_children],
            "holdout_target_source": (
                None if self.holdout_target_source is None else self.holdout_target_source.as_json()
            ),
        }

    def as_json(self) -> dict[str, JsonValue]:
        return {
            **self.semantic_json(),
            "foundation_bundle_id": self.foundation_bundle_id,
            "feature_children": [item.as_json() for item in self.feature_children],
            "preprocessing_children": [item.as_json() for item in self.preprocessing_children],
            "fit_children": [item.as_json() for item in self.fit_children],
            "forecast_manifests": [item.as_json() for item in self.forecast_manifests],
            "coverage_children": [item.as_json() for item in self.coverage_children],
            "evaluation_children": [item.as_json() for item in self.evaluation_children],
            "holdout_target_source": (
                None if self.holdout_target_source is None else self.holdout_target_source.as_json()
            ),
            "oof_id": self.oof_id,
            "closure_id": self.closure_id,
        }


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("expected a string")
    return value
