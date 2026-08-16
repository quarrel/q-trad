"""R2 OOF and software-verification orchestration.

The module deliberately keeps bundles thin: data and model artefacts remain children,
while manifests bind their immutable identities, source class and evidence class.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType, SimpleNamespace
from typing import Any, cast
from uuid import UUID

import numpy
import sklearn

from qtrad import __version__
from qtrad.adapters.parquet.r2 import ParquetR2FeatureStore, R2FeatureManifest
from qtrad.application.r2_baselines import build_local_ridge_oof
from qtrad.application.r2_evaluation import (
    EvaluationModel,
    build_r2_evaluation,
    build_selection_manifest,
)
from qtrad.application.r2_features import (
    R2FoundationInputs,
    R2OutcomeBlindFeatureInputs,
    build_outcome_blind_holdout_feature_rows,
    feature_schema_for_set,
    materialise_outcome_blind_training_features,
    verify_raw_feature_manifest_bindings,
    verify_raw_feature_rows,
)
from qtrad.application.r2_holdout import (
    _CONFIRMATORY_G2_PREPARATION_TOKEN,
    _VERIFIED_CONFIRMATORY_HOLDOUT_AUTHORITY_TOKEN,
    VerifiedConfirmatoryHoldoutAuthority,
    build_holdout_coverage,
    build_holdout_forecasts,
    fit_final_ridge,
    freeze_holdout_selection,
    materialise_confirmatory_holdout_features,
    seal_holdout_forecasts,
)
from qtrad.application.r2_ibkr_historical import (
    build_ibkr_r2_foundation_inputs,
)
from qtrad.application.r2_pooled import build_pooled_ridge_oof
from qtrad.application.r2_preprocessing import (
    build_pooled_preprocessing_selection,
    build_r2_preprocessing_selection,
)
from qtrad.application.r2_readiness import (
    R1FoundationBindings,
    _availability_dataset_id,
    evaluate_outcome_blind_confirmatory_readiness,
    verify_exact_r1_bindings,
)
from qtrad.domain.events import JsonValue
from qtrad.domain.folds import Fold, FoldDataset, membership_hash
from qtrad.domain.forecasts import ForecastDataset
from qtrad.domain.foundation import (
    AvailabilityBasis,
    ExcursionDisposition,
    FoundationConfig,
    InstrumentRole,
    PanelDataset,
    ReturnDisposition,
    TargetDataset,
    TargetRow,
)
from qtrad.domain.foundation_bundle import FoundationBundle
from qtrad.domain.market_data import MarketDataSourceClass, PriceBasis
from qtrad.domain.r2_bundles import (
    R2_HOLDOUT_SOURCE_BINDING_CONTRACT,
    ArtifactReference,
    R2ForecastManifest,
    R2OofBundle,
    R2OofVerificationReceipt,
)
from qtrad.domain.r2_confirmatory import ConfirmatoryF2Promotion
from qtrad.domain.r2_evaluation import (
    R2_EVALUATION_CONTRACT,
    ConfigurationDisposition,
    ConfigurationRecord,
    MetricAvailability,
    MetricValue,
    SelectionDecision,
    SelectionGateOutcome,
    SelectionManifest,
)
from qtrad.domain.r2_features import (
    FeatureDefinition,
    R2FeatureDataset,
    RawFeatureRow,
    RawFeatureValue,
    feature_set_id,
)
from qtrad.domain.r2_holdout import (
    HoldoutDirection,
    HoldoutScope,
    R2ConfirmatoryOpenedMarker,
    R2FinalFit,
    R2FinalFittingPolicy,
    R2HoldoutConsumedMarker,
    R2HoldoutCoverageDataset,
    R2HoldoutEvaluation,
    R2HoldoutFeatureDataset,
    R2HoldoutForecastDataset,
    R2HoldoutForecastSeal,
    R2HoldoutOpenedMarker,
    R2HoldoutOpportunityRegistry,
    R2HoldoutQuestion,
    R2HoldoutSelectionManifest,
    R2HoldoutTargetProjection,
    R2HoldoutTargetSource,
)
from qtrad.domain.r2_ibkr_historical import (
    IBKR_HISTORICAL_GROUPS,
    IBKR_HISTORICAL_PROFILE,
    IBKR_HISTORICAL_TARGETS,
    IBKRHistoricalAdapterIdentity,
    validate_ibkr_historical_profile,
)
from qtrad.domain.r2_models import (
    POOLED_INSTRUMENT_IDENTITY_POLICY,
    POOLED_INSTRUMENT_MEMBERSHIP_POLICY,
    POOLED_INTERCEPT_POLICY,
    R2_PREPROCESSING_SELECTION_CONTRACT,
    PreprocessingFeatureKind,
    R2PreprocessingSelection,
    derive_r2_preprocessing_schema,
)
from qtrad.domain.r2_readiness import (
    EligibilityDecision,
    EvidenceClass,
    FeatureEligibility,
    FeatureFamily,
    FeatureSet,
    ModelFamily,
    R2ExperimentConfig,
    R2ReadinessReport,
    ReadinessState,
)
from qtrad.domain.research import ObservationDataset
from qtrad.ports.clock import Clock
from qtrad.runtime.foundation_bundle import (
    G2FeatureSourceAuthority,
    OutcomeBlindVerifiedFoundationBundle,
    VerifiedG2FeatureSource,
    _verify_confirmatory_target_dataset,
    _verify_g2_feature_source,
    restore_authenticated_foundation_bundle,
    restore_authenticated_outcome_blind_foundation_bundle,
)
from qtrad.runtime.ibkr_foundation import (
    IBKRG2FeatureSourceAuthority,
    _load_ibkr_foundation_outcome_blind_with_g2_authority,
    _verify_ibkr_confirmatory_target_dataset,
    _verify_ibkr_g2_feature_source,
    load_ibkr_foundation_with_identity,
)
from qtrad.runtime.ibkr_foundation_promotion import authenticate_ibkr_foundation_promotion
from qtrad.runtime.r2_bundles import (
    R2_EVALUATION_REGISTER_CONTRACT,
    _canonical_payload_identity,
    _reject_orphan_files,
    _verify_r2_oof_bundle_with_source,
    atomic_create,
    canonical_bytes,
    reference_for_json,
    verify_r2_oof_bundle,
    write_r2_oof_bundle,
)
from qtrad.runtime.r2_holdout import (
    _reveal_confirmatory_holdout,
    _verify_confirmatory_holdout_evaluation,
    _verify_confirmatory_holdout_preparation,
    verify_holdout_markers,
    verify_holdout_preparation,
    write_holdout_preparation,
)
from qtrad.runtime.r2_holdout_source import (
    R2HoldoutTargetSourceAuthority,
    load_r2_holdout_target_source_authority,
)
from qtrad.runtime.r2_preprocessing_selection import decode_r2_preprocessing_selection
from qtrad.runtime.r2_readiness import load_r2_experiment

OOF_DESCRIPTOR_CONTRACT = "qtrad-r2-oof-run-descriptor-v1"
CONFIRMATORY_RUN_KIND = "CONFIRMATORY"
R2_OOF_VERIFIER_CONTRACT = "qtrad-r2-oof-semantic-verifier-v1"
R2_OOF_VERIFIER_VERSION = "1"
R2_OOF_COMPLETED_CHECKS = (
    "canonical_manifest",
    "exact_declared_tree",
    "oof_semantics",
    "immediate_parent_authority",
    "causal_holdout_exclusion",
)
R2_CONFIRMATORY_OOF_COMPLETED_CHECKS = (
    *R2_OOF_COMPLETED_CHECKS,
    "f2_readiness",
    "f2_inner_validation_register",
)
type ConfirmatoryG2FeatureSourceAuthority = G2FeatureSourceAuthority | IBKRG2FeatureSourceAuthority
_IMPLEMENTATION_RUN_KINDS = frozenset({"SYNTHETIC", "REPRESENTATIVE"})
_OOF_SELECTION_PRIMARY_METRIC = "INSTRUMENT_BALANCED_COMMON_SUPPORT_MSE"
_OOF_SELECTION_SECONDARY_METRICS = ("RMSE",)
_OOF_SELECTION_FINAL_FITTING_PROCEDURE = "PENDING_R2_H_INTEGRATION"
_IMAGE_IDENTITY_CONTRACT = "qtrad-runtime-image-identity-v1"
R2_CLAIM_VERIFIER_CONTRACTS = MappingProxyType(
    {
        "feature": "qtrad-r2-feature-verifier-v1",
        "preprocessing": "qtrad-r2-preprocessing-verifier-v1",
        "fit": "qtrad-r2-fit-verifier-v1",
        "oof": "qtrad-r2-oof-semantic-verifier-v1",
        "evaluation": "qtrad-r2-evaluation-verifier-v1",
    }
)
_OOF_DESCRIPTOR_PROVENANCE_FIELDS = frozenset(
    {
        "application_identity",
        "image_identity",
        "python_identity",
        "numpy_identity",
        "sklearn_identity",
    }
)
_REQUIRED_FEATURE_SETS = frozenset({"L0", "L1", "P0", "P1"})
_CAPTURE_V4_UNIVERSE = (
    "fx:aud-usd",
    "fx:eur-usd",
    "fx:usd-jpy",
    "fx:gbp-usd",
    "fx:usd-chf",
    "fx:usd-cad",
    "fx:nzd-usd",
    "fx:eur-jpy",
    "index:australia-200",
    "index:us-500",
    "index:ftse-100",
    "index:us-tech-100",
    "index:wall-street",
    "index:germany-40",
    "index:japan-225",
    "index:eu-stocks-50",
    "commodity:spot-gold",
    "commodity:spot-silver",
    "commodity:us-crude",
    "index:hong-kong-hs50",
    "index:china-a50",
    "index:taiwan",
    "index:volatility",
)
_CAPTURE_V4_TARGETS = (
    "fx:aud-usd",
    "fx:eur-usd",
    "index:australia-200",
    "index:us-500",
    "commodity:spot-gold",
    "commodity:us-crude",
)


_DEPLOYMENT_IMAGE_IDENTITY_PATH = Path("/run/qtrad/image-identity.json")


_VERIFIED_CONFIRMATORY_F2_TOKEN = object()
_VERIFIED_CONFIRMATORY_F2_PROMOTION_TOKEN = object()
_VERIFIED_CONFIRMATORY_G1_TOKEN = object()
_VERIFIED_CONFIRMATORY_G1_PROVENANCE = object()
_VERIFIED_CONFIRMATORY_G2_PREPARATION_TOKEN = object()
_VERIFIED_CONFIRMATORY_G2_PREPARATION_PROVENANCE = object()
_OPENED_CONFIRMATORY_HOLDOUT_TOKEN = object()
_OPENED_CONFIRMATORY_HOLDOUT_PROVENANCE = object()


def _deep_freeze(value: object) -> object:
    """Copy JSON-like authority payloads into recursively immutable values."""
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(item) for item in value)
    return value


def _immutable_readiness_report(report: R2ReadinessReport) -> R2ReadinessReport:
    return replace(
        report,
        feature_family_states=MappingProxyType(dict(report.feature_family_states)),
        coverage_matrix=MappingProxyType(dict(report.coverage_matrix)),
        active_source_duration_seconds=MappingProxyType(
            dict(report.active_source_duration_seconds)
        ),
    )


def _immutable_experiment(experiment: R2ExperimentConfig) -> R2ExperimentConfig:
    return replace(
        experiment,
        instrument_roles=MappingProxyType(dict(experiment.instrument_roles)),
        target_instrument_eligibility=MappingProxyType(
            dict(experiment.target_instrument_eligibility)
        ),
        market_groups=MappingProxyType(dict(experiment.market_groups)),
        feature_coverage_thresholds=MappingProxyType(dict(experiment.feature_coverage_thresholds)),
        feature_eligibility=MappingProxyType(dict(experiment.feature_eligibility)),
        acceptance_thresholds=MappingProxyType(dict(experiment.acceptance_thresholds)),
        source_adapter_identity=(
            None
            if experiment.source_adapter_identity is None
            else cast(Mapping[str, JsonValue], _deep_freeze(experiment.source_adapter_identity))
        ),
    )


@dataclass(frozen=True, slots=True)
class AuthenticatedR2Foundation:
    """Immediate-parent authority and semantic inputs consumed by R2."""

    foundation_id: str
    closure_id: str
    verification_id: str
    source_class: MarketDataSourceClass
    evidence_class: EvidenceClass
    semantic_inputs: R2FoundationInputs
    g2_feature_source: ConfirmatoryG2FeatureSourceAuthority | None = None
    promotion_id: str | None = None
    bundle_path: Path | None = None
    receipt_path: Path | None = None
    promotion_path: Path | None = None

    def __post_init__(self) -> None:
        for value, field in (
            (self.foundation_id, "foundation ID"),
            (self.closure_id, "foundation closure ID"),
            (self.verification_id, "foundation verification ID"),
        ):
            if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise ValueError(f"{field} must be a lowercase SHA-256 identity")
        if self.promotion_id is not None and (
            len(self.promotion_id) != 64
            or any(character not in "0123456789abcdef" for character in self.promotion_id)
        ):
            raise ValueError("foundation promotion ID must be a lowercase SHA-256 identity")
        if self.semantic_inputs.bundle.foundation_id != self.foundation_id:
            raise ValueError("R2 foundation authority differs from its semantic inputs")
        if self.semantic_inputs.bundle.market_data_source_class is not self.source_class:
            raise ValueError("R2 foundation source class differs from its semantic inputs")

    @property
    def bundle(self) -> FoundationBundle:
        return self.semantic_inputs.bundle

    @property
    def configuration(self) -> FoundationConfig:
        return self.semantic_inputs.configuration

    @property
    def observations(self) -> ObservationDataset:
        return self.semantic_inputs.observations

    @property
    def panel(self) -> PanelDataset:
        return self.semantic_inputs.panel

    @property
    def targets(self) -> TargetDataset:
        return self.semantic_inputs.targets

    @property
    def folds(self) -> FoldDataset:
        return self.semantic_inputs.folds

    @property
    def availability_evidence(self) -> Mapping[str, JsonValue]:
        return self.semantic_inputs.availability_evidence

    @property
    def source_active_intervals(self) -> Mapping[str, tuple[tuple[datetime, datetime], ...]]:
        return self.semantic_inputs.source_active_intervals

    def identity_json(self) -> dict[str, JsonValue]:
        """Return semantic, closure and authority identities for a descriptor."""
        return {
            "foundation_id": self.foundation_id,
            "closure_id": self.closure_id,
            "verification_id": self.verification_id,
            "promotion_id": self.promotion_id,
            "source_class": self.source_class.value,
            "evidence_class": self.evidence_class.value,
        }

    def runtime_json(
        self,
        *,
        feature_manifest_paths: Mapping[str, Path],
        experiment_path: Path,
        research_root: Path,
    ) -> dict[str, JsonValue]:
        """Return runtime locators; these are not scientific identity fields."""
        if self.bundle_path is None or self.receipt_path is None:
            raise ValueError("R2 foundation authority is missing its bundle or receipt path")
        if (
            self.source_class is MarketDataSourceClass.IBKR_HISTORICAL_RESEARCH
            and self.evidence_class is EvidenceClass.CONFIRMATORY
            and (self.promotion_id is None or self.promotion_path is None)
        ):
            raise ValueError(
                "confirmatory R2 IBKR foundation authority is missing promotion evidence"
            )
        absolute_research_root = research_root.absolute()

        def absolute_locator(path: Path, *, relative_root: Path | None = None) -> str:
            candidate = (
                path if path.is_absolute() or relative_root is None else relative_root / path
            )
            return str(candidate.absolute())

        return {
            "foundation": absolute_locator(self.bundle_path),
            "foundation_receipt": absolute_locator(self.receipt_path),
            "foundation_promotion": (
                absolute_locator(self.promotion_path) if self.promotion_path is not None else None
            ),
            "experiment": absolute_locator(experiment_path),
            "research_root": str(absolute_research_root),
            "feature_manifests": {
                name: absolute_locator(path, relative_root=absolute_research_root)
                for name, path in sorted(feature_manifest_paths.items())
            },
        }


class VerifiedConfirmatoryF2:
    """Runtime-only authority proving one independently replayed confirmatory F2 run."""

    __slots__ = (
        "_bundle",
        "_configuration_registry",
        "_confirmatory_holdout_authority",
        "_descriptor",
        "_evaluated_configurations",
        "_evaluation_policy",
        "_evaluation_report_id",
        "_experiment",
        "_g2_feature_source_authority",
        "_holdout_comparator_configuration_ids",
        "_holdout_target_source",
        "_local_comparator_manifest_id",
        "_outcome_blind_foundation",
        "_readiness_report",
        "_runtime_identities",
        "_selected_configuration_ids",
        "_selection_decisions",
        "_selection_policy",
    )

    _bundle: R2OofBundle
    _confirmatory_holdout_authority: VerifiedConfirmatoryHoldoutAuthority
    _configuration_registry: tuple[tuple[str, ModelFamily, str | None, str | None, str | None], ...]
    _descriptor: Mapping[str, JsonValue]
    _evaluated_configurations: tuple[ConfigurationRecord, ...]
    _evaluation_policy: Mapping[str, JsonValue]
    _evaluation_report_id: str
    _experiment: R2ExperimentConfig
    _holdout_comparator_configuration_ids: tuple[str, ...]
    _holdout_target_source: R2HoldoutTargetSource
    _g2_feature_source_authority: ConfirmatoryG2FeatureSourceAuthority
    _local_comparator_manifest_id: str
    _outcome_blind_foundation: R1FoundationBindings
    _readiness_report: R2ReadinessReport
    _runtime_identities: Mapping[str, str]
    _selection_policy: Mapping[str, JsonValue]
    _selected_configuration_ids: tuple[str, ...]
    _selection_decisions: tuple[SelectionDecision, ...]

    def __init__(self) -> None:
        raise TypeError("VerifiedConfirmatoryF2 is constructed only by audit_confirmatory_f2")

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("VerifiedConfirmatoryF2 is immutable")

    @classmethod
    def _create(
        cls,
        token: object,
        *,
        bundle: R2OofBundle,
        holdout_target_source: R2HoldoutTargetSource,
        g2_feature_source_authority: ConfirmatoryG2FeatureSourceAuthority,
        descriptor: Mapping[str, JsonValue],
        evaluation_report_id: str,
        experiment: R2ExperimentConfig,
        local_comparator_manifest_id: str,
        outcome_blind_foundation: R1FoundationBindings,
        evaluated_configurations: tuple[ConfigurationRecord, ...],
        selection_decisions: tuple[SelectionDecision, ...],
        selected_configuration_ids: tuple[str, ...],
        holdout_comparator_configuration_ids: tuple[str, ...],
        configuration_registry: tuple[
            tuple[str, ModelFamily, str | None, str | None, str | None], ...
        ],
        evaluation_policy: Mapping[str, JsonValue],
        confirmatory_holdout_authority: VerifiedConfirmatoryHoldoutAuthority,
        readiness_report: R2ReadinessReport,
        runtime_identities: Mapping[str, str],
        selection_policy: Mapping[str, JsonValue],
    ) -> VerifiedConfirmatoryF2:
        if token is not _VERIFIED_CONFIRMATORY_F2_TOKEN:
            raise TypeError("VerifiedConfirmatoryF2 is constructed only by its verifier")
        instance = object.__new__(cls)
        object.__setattr__(instance, "_bundle", bundle)
        object.__setattr__(instance, "_holdout_target_source", holdout_target_source)
        object.__setattr__(instance, "_g2_feature_source_authority", g2_feature_source_authority)
        object.__setattr__(
            instance,
            "_descriptor",
            cast(Mapping[str, JsonValue], _deep_freeze(descriptor)),
        )
        object.__setattr__(instance, "_evaluation_report_id", evaluation_report_id)
        object.__setattr__(instance, "_experiment", _immutable_experiment(experiment))
        object.__setattr__(instance, "_local_comparator_manifest_id", local_comparator_manifest_id)
        object.__setattr__(instance, "_outcome_blind_foundation", outcome_blind_foundation)
        object.__setattr__(instance, "_evaluated_configurations", evaluated_configurations)
        object.__setattr__(instance, "_selection_decisions", selection_decisions)
        object.__setattr__(instance, "_selected_configuration_ids", selected_configuration_ids)
        object.__setattr__(
            instance,
            "_holdout_comparator_configuration_ids",
            holdout_comparator_configuration_ids,
        )
        object.__setattr__(instance, "_configuration_registry", configuration_registry)
        object.__setattr__(
            instance, "_confirmatory_holdout_authority", confirmatory_holdout_authority
        )
        object.__setattr__(
            instance,
            "_evaluation_policy",
            cast(Mapping[str, JsonValue], _deep_freeze(evaluation_policy)),
        )
        object.__setattr__(
            instance, "_readiness_report", _immutable_readiness_report(readiness_report)
        )
        object.__setattr__(
            instance,
            "_runtime_identities",
            cast(Mapping[str, str], _deep_freeze(runtime_identities)),
        )
        object.__setattr__(
            instance,
            "_selection_policy",
            cast(Mapping[str, JsonValue], _deep_freeze(selection_policy)),
        )
        return instance

    @property
    def bundle(self) -> R2OofBundle:
        return self._bundle

    @property
    def foundation_bundle_id(self) -> str:
        return self._bundle.foundation_bundle_id

    @property
    def experiment_configuration_id(self) -> str:
        return self._bundle.experiment_configuration_id

    @property
    def evaluation_report_id(self) -> str:
        return self._evaluation_report_id

    @property
    def local_comparator_manifest_id(self) -> str:
        return self._local_comparator_manifest_id

    @property
    def source_class(self) -> MarketDataSourceClass:
        return self._bundle.source_class

    @property
    def evidence_class(self) -> EvidenceClass:
        return self._bundle.evidence_class

    @property
    def holdout_target_source(self) -> R2HoldoutTargetSource:
        return self._holdout_target_source

    @property
    def outcome_blind_foundation(self) -> R1FoundationBindings:
        """Return independently verified R1 projections that contain no holdout outcomes."""

        return self._outcome_blind_foundation

    @property
    def descriptor(self) -> Mapping[str, JsonValue]:
        return self._descriptor

    @property
    def evaluated_configurations(self) -> tuple[ConfigurationRecord, ...]:
        return self._evaluated_configurations

    @property
    def selection_decisions(self) -> tuple[SelectionDecision, ...]:
        return self._selection_decisions

    @property
    def selected_configuration_ids(self) -> tuple[str, ...]:
        return self._selected_configuration_ids

    @property
    def holdout_comparator_configuration_ids(self) -> tuple[str, ...]:
        return self._holdout_comparator_configuration_ids

    @property
    def configuration_registry(
        self,
    ) -> tuple[tuple[str, ModelFamily, str | None, str | None, str | None], ...]:
        return self._configuration_registry

    @property
    def confirmatory_holdout_authority(self) -> VerifiedConfirmatoryHoldoutAuthority:
        return self._confirmatory_holdout_authority

    @property
    def evaluation_policy(self) -> Mapping[str, JsonValue]:
        return self._evaluation_policy

    @property
    def runtime_identities(self) -> Mapping[str, str]:
        return self._runtime_identities

    @property
    def readiness_report(self) -> R2ReadinessReport:
        return self._readiness_report

    @property
    def experiment(self) -> R2ExperimentConfig:
        return self._experiment

    @property
    def selection_policy(self) -> Mapping[str, JsonValue]:
        return self._selection_policy


class VerifiedConfirmatoryF2Promotion(VerifiedConfirmatoryF2):
    """Typed capability restored from a durable confirmatory F2 promotion."""

    __slots__ = ("_promotion",)

    _promotion: ConfirmatoryF2Promotion

    @classmethod
    def _create_promotion(
        cls,
        token: object,
        *,
        promotion: ConfirmatoryF2Promotion,
        **values: object,
    ) -> VerifiedConfirmatoryF2Promotion:
        if token is not _VERIFIED_CONFIRMATORY_F2_PROMOTION_TOKEN:
            raise TypeError(
                "VerifiedConfirmatoryF2Promotion is constructed only by its authenticator"
            )
        instance = VerifiedConfirmatoryF2._create.__func__(
            cls,
            _VERIFIED_CONFIRMATORY_F2_TOKEN,
            **cast(Any, values),
        )
        object.__setattr__(instance, "_promotion", promotion)
        return cast(VerifiedConfirmatoryF2Promotion, instance)

    @property
    def promotion(self) -> ConfirmatoryF2Promotion:
        return self._promotion


def _has_confirmatory_f2_promotion_provenance(value: object) -> bool:
    return (
        type(value) is VerifiedConfirmatoryF2Promotion
        and getattr(value, "_promotion", None) is not None
    )


class _VerifiedConfirmatoryG2FeatureAccess:
    """Verifier-issued capability to decode the G2-safe feature source after G1."""

    __slots__ = (
        "_authority",
        "_selection_manifest_id",
        "_verified_f2",
        "_verifier_provenance",
    )

    _authority: ConfirmatoryG2FeatureSourceAuthority
    _selection_manifest_id: str
    _verified_f2: VerifiedConfirmatoryF2
    _verifier_provenance: object

    def __init__(self) -> None:
        raise TypeError("G2 feature access is issued only by verified G1")

    @classmethod
    def _create(
        cls,
        token: object,
        *,
        verified_f2: VerifiedConfirmatoryF2,
        selection: R2HoldoutSelectionManifest,
    ) -> _VerifiedConfirmatoryG2FeatureAccess:
        if token is not _VERIFIED_CONFIRMATORY_G1_TOKEN:
            raise TypeError("G2 feature access is issued only by verified G1")
        instance = object.__new__(cls)
        object.__setattr__(instance, "_authority", verified_f2._g2_feature_source_authority)
        object.__setattr__(instance, "_selection_manifest_id", selection.manifest_id)
        object.__setattr__(instance, "_verified_f2", verified_f2)
        object.__setattr__(instance, "_verifier_provenance", _VERIFIED_CONFIRMATORY_G1_PROVENANCE)
        return instance

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("G2 feature access is immutable")


class VerifiedConfirmatoryG1:
    """Runtime-only authority proving an exact persisted confirmatory G1 freeze."""

    __slots__ = (
        "_g2_feature_access",
        "_selection",
        "_verified_f2",
        "_verifier_provenance",
    )

    _g2_feature_access: _VerifiedConfirmatoryG2FeatureAccess
    _selection: R2HoldoutSelectionManifest
    _verified_f2: VerifiedConfirmatoryF2
    _verifier_provenance: object

    def __init__(self) -> None:
        raise TypeError("VerifiedConfirmatoryG1 is constructed only by verify_confirmatory_g1")

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("VerifiedConfirmatoryG1 is immutable")

    @classmethod
    def _create(
        cls,
        token: object,
        *,
        verified_f2: VerifiedConfirmatoryF2,
        selection: R2HoldoutSelectionManifest,
    ) -> VerifiedConfirmatoryG1:
        if token is not _VERIFIED_CONFIRMATORY_G1_TOKEN:
            raise TypeError("VerifiedConfirmatoryG1 is constructed only by its verifier")
        instance = object.__new__(cls)
        object.__setattr__(instance, "_verified_f2", verified_f2)
        object.__setattr__(instance, "_selection", selection)
        object.__setattr__(
            instance,
            "_g2_feature_access",
            _VerifiedConfirmatoryG2FeatureAccess._create(
                token,
                verified_f2=verified_f2,
                selection=selection,
            ),
        )
        object.__setattr__(instance, "_verifier_provenance", _VERIFIED_CONFIRMATORY_G1_PROVENANCE)
        return instance

    @property
    def verified_f2(self) -> VerifiedConfirmatoryF2:
        return self._verified_f2

    @property
    def selection(self) -> R2HoldoutSelectionManifest:
        return self._selection


class VerifiedConfirmatoryG2Preparation:
    """Runtime-only authority proving a sealed, unopened confirmatory preparation."""

    __slots__ = ("_path", "_seal", "_verified_g1", "_verifier_provenance")

    _path: Path
    _seal: R2HoldoutForecastSeal
    _verified_g1: VerifiedConfirmatoryG1
    _verifier_provenance: object

    def __init__(self) -> None:
        raise TypeError("VerifiedConfirmatoryG2Preparation is constructed only by its verifier")

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("VerifiedConfirmatoryG2Preparation is immutable")

    @classmethod
    def _create(
        cls,
        token: object,
        *,
        verified_g1: VerifiedConfirmatoryG1,
        seal: R2HoldoutForecastSeal,
        path: Path,
    ) -> VerifiedConfirmatoryG2Preparation:
        if token is not _VERIFIED_CONFIRMATORY_G2_PREPARATION_TOKEN:
            raise TypeError("VerifiedConfirmatoryG2Preparation is constructed only by its verifier")
        instance = object.__new__(cls)
        object.__setattr__(instance, "_verified_g1", verified_g1)
        object.__setattr__(instance, "_seal", seal)
        object.__setattr__(instance, "_path", path.resolve(strict=True))
        object.__setattr__(
            instance,
            "_verifier_provenance",
            _VERIFIED_CONFIRMATORY_G2_PREPARATION_PROVENANCE,
        )
        return instance

    @property
    def path(self) -> Path:
        return self._path

    @property
    def verified_g1(self) -> VerifiedConfirmatoryG1:
        return self._verified_g1

    @property
    def seal(self) -> R2HoldoutForecastSeal:
        return self._seal


class ConfirmatoryR2HStatus(StrEnum):
    VALID_CONSUMED_RESULT = "VALID_CONSUMED_RESULT"
    OPENED_INCOMPLETE = "OPENED_INCOMPLETE"
    INVALID = "INVALID"


@dataclass(frozen=True, slots=True)
class ConfirmatoryR2HReport:
    status: ConfirmatoryR2HStatus
    selection_manifest_id: str
    seal_id: str
    opened_marker_id: str | None
    consumed_marker_id: str | None
    evaluation_id: str | None
    reason: str


class OpenedConfirmatoryHoldout:
    """Runtime-only authority issued after both create-only OPENED markers exist."""

    __slots__ = ("_marker", "_preparation", "_verifier_provenance")

    _marker: R2ConfirmatoryOpenedMarker
    _preparation: VerifiedConfirmatoryG2Preparation
    _verifier_provenance: object

    def __init__(self) -> None:
        raise TypeError("OpenedConfirmatoryHoldout is constructed only after durable OPENED")

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("OpenedConfirmatoryHoldout is immutable")

    @classmethod
    def _create(
        cls,
        token: object,
        *,
        preparation: VerifiedConfirmatoryG2Preparation,
        marker: R2ConfirmatoryOpenedMarker,
    ) -> OpenedConfirmatoryHoldout:
        if token is not _OPENED_CONFIRMATORY_HOLDOUT_TOKEN:
            raise TypeError("OpenedConfirmatoryHoldout requires verifier-issued OPENED provenance")
        instance = object.__new__(cls)
        object.__setattr__(instance, "_preparation", preparation)
        object.__setattr__(instance, "_marker", marker)
        object.__setattr__(
            instance,
            "_verifier_provenance",
            _OPENED_CONFIRMATORY_HOLDOUT_PROVENANCE,
        )
        return instance

    @property
    def preparation(self) -> VerifiedConfirmatoryG2Preparation:
        return self._preparation

    @property
    def marker(self) -> R2ConfirmatoryOpenedMarker:
        return self._marker


def _image_identity_manifest(path: Path | None = None) -> Mapping[str, object]:
    manifest_path = _DEPLOYMENT_IMAGE_IDENTITY_PATH if path is None else path
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise RuntimeError("the deployment image identity manifest is unavailable")
    try:
        stat = manifest_path.stat()
    except OSError as exc:
        raise RuntimeError("the deployment image identity manifest is unavailable") from exc
    if path is None and (stat.st_uid != 0 or stat.st_mode & 0o022):
        raise RuntimeError("the deployment image identity manifest is not trusted")
    try:
        payload = json.loads(manifest_path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("the deployment image identity manifest is invalid") from exc
    if not isinstance(payload, dict) or set(payload) != {
        "contract",
        "schema_version",
        "application_commit",
        "image_digest",
        "manifest_sha256",
    }:
        raise RuntimeError("the deployment image identity manifest has unexpected fields")
    if payload["contract"] != _IMAGE_IDENTITY_CONTRACT or payload["schema_version"] != 1:
        raise RuntimeError("the deployment image identity manifest contract is unsupported")
    manifest_hash = payload["manifest_sha256"]
    unsigned = {key: value for key, value in payload.items() if key != "manifest_sha256"}
    if (
        not isinstance(manifest_hash, str)
        or sha256(canonical_bytes(unsigned)).hexdigest() != manifest_hash
    ):
        raise RuntimeError("the deployment image identity manifest digest is invalid")
    return cast(Mapping[str, object], payload)


def execution_provenance() -> dict[str, str]:
    """Return authenticated process provenance without scientific policy fields."""
    repository = Path(__file__).resolve().parents[2]
    try:
        result = subprocess.run(
            ("git", "-C", str(repository), "rev-parse", "HEAD"),
            check=True,
            capture_output=True,
            text=True,
        )
        status = subprocess.run(
            ("git", "-C", str(repository), "status", "--porcelain", "--untracked-files=all"),
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("cannot derive the application commit identity") from exc
    if status.stdout.strip():
        raise RuntimeError("application identity requires a clean source tree")
    commit = result.stdout.strip()
    if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
        raise RuntimeError("git did not return a verified commit identity")
    manifest = _image_identity_manifest()
    if manifest["application_commit"] != commit:
        raise RuntimeError("image identity manifest commit differs from the running source")
    image_digest = manifest["image_digest"]
    if (
        not isinstance(image_digest, str)
        or not image_digest.startswith("sha256:")
        or len(image_digest) != len("sha256:") + 64
        or any(character not in "0123456789abcdef" for character in image_digest[7:])
    ):
        raise RuntimeError("image identity manifest does not contain a verified sha256 digest")
    return {
        "git_commit": commit,
        "image_digest": image_digest,
        "application_identity": f"qtrad-{__version__}+git:{commit}+image:{image_digest}",
    }


def numerical_environment() -> dict[str, str]:
    """Return numerical-library provenance kept separate from scientific policy."""
    return {
        "python_version": sys.version.split()[0],
        "numpy_version": numpy.__version__,
        "sklearn_version": sklearn.__version__,
    }


def runtime_identities() -> dict[str, str]:
    """Return the adapter-facing view of split runtime provenance."""
    execution = execution_provenance()
    numerical = numerical_environment()
    return {
        "application_identity": execution["application_identity"],
        "image_identity": execution["image_digest"],
        "python_identity": numerical["python_version"],
        "numpy_identity": numerical["numpy_version"],
        "sklearn_identity": numerical["sklearn_version"],
    }


def parse_feature_manifest_arguments(arguments: list[str]) -> dict[str, Path]:
    """Parse and validate repeated NAME=PATH arguments without accepting duplicates."""
    parsed: dict[str, Path] = {}
    for argument in arguments:
        name, separator, raw_path = argument.partition("=")
        if not separator or not name or not raw_path:
            raise ValueError("feature manifest must use NAME=PATH")
        if name in parsed:
            raise ValueError(f"duplicate feature manifest: {name}")
        parsed[name] = Path(raw_path)
    if set(parsed) != _REQUIRED_FEATURE_SETS:
        missing = sorted(_REQUIRED_FEATURE_SETS - set(parsed))
        extra = sorted(set(parsed) - _REQUIRED_FEATURE_SETS)
        detail = []
        if missing:
            detail.append("missing " + ", ".join(missing))
        if extra:
            detail.append("undeclared " + ", ".join(extra))
        raise ValueError(
            "feature manifests must cover exactly L0/L1/P0/P1 (" + "; ".join(detail) + ")"
        )
    return parsed


def _foundation_inputs(verified: R1FoundationBindings) -> R2FoundationInputs:
    return R2FoundationInputs(
        bundle=verified.bundle,
        configuration=verified.configuration,
        observations=verified.observations,
        panel=verified.panel,
        targets=verified.targets,
        folds=verified.folds,
        availability_evidence=verified.availability_evidence,
    )


def _require_sha256_identity(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{field} is not a lowercase SHA-256 identity")
    return value


async def authenticate_r1_foundation_for_r2(
    *,
    root: Path,
    bundle_path: Path,
    receipt_path: Path,
    clock: Clock,
    evidence_class: EvidenceClass,
    outcome_blind: bool = False,
    holdout_target_source: R2HoldoutTargetSource | None = None,
) -> AuthenticatedR2Foundation:
    """Authenticate the current R1 receipt once and expose R2 semantic inputs."""
    if outcome_blind:
        if holdout_target_source is None:
            raise ValueError("outcome-blind R1 authentication requires a holdout target source")
        verified = await restore_authenticated_outcome_blind_foundation_bundle(
            root=root,
            bundle_path=bundle_path,
            clock=clock,
            holdout_target_source=holdout_target_source,
            receipt=receipt_path,
        )
    else:
        verified = await restore_authenticated_foundation_bundle(
            root=root,
            bundle_path=bundle_path,
            clock=clock,
            receipt=receipt_path,
        )
    receipt = verified.receipt
    if receipt is None:
        raise ValueError("authenticated R1 foundation has no verification receipt")
    return AuthenticatedR2Foundation(
        foundation_id=verified.bundle.foundation_id,
        closure_id=verified.bundle.closure_id,
        verification_id=receipt.verification_id,
        source_class=verified.bundle.market_data_source_class,
        evidence_class=evidence_class,
        semantic_inputs=_foundation_inputs(cast(R1FoundationBindings, verified)),
        g2_feature_source=getattr(verified, "g2_feature_source", None),
        bundle_path=bundle_path,
        receipt_path=receipt_path,
        promotion_path=None,
    )


def _stage8_authority_ids(bundle_path: Path, receipt_path: Path) -> tuple[str, str, str]:
    if bundle_path.is_symlink() or not bundle_path.is_file():
        raise ValueError("Stage 8 foundation must be a regular non-symlink file")
    if receipt_path.is_symlink() or not receipt_path.is_file():
        raise ValueError("Stage 8 receipt must be a regular non-symlink file")
    manifest_bytes = bundle_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    receipt = json.loads(receipt_path.read_bytes())
    if not isinstance(manifest, dict) or not isinstance(receipt, dict):
        raise ValueError("Stage 8 authority documents must be JSON objects")
    foundation_id = _require_sha256_identity(manifest.get("foundation_id"), "Stage 8 foundation ID")
    closure_id = _require_sha256_identity(manifest.get("closure_id"), "Stage 8 closure ID")
    verification_id = _require_sha256_identity(
        receipt.get("verification_id"), "Stage 8 verification ID"
    )
    receipt_foundation_id = _require_sha256_identity(
        receipt.get("foundation_id"), "Stage 8 receipt foundation ID"
    )
    receipt_closure_id = _require_sha256_identity(
        receipt.get("closure_id"), "Stage 8 receipt closure ID"
    )
    if receipt.get("foundation_manifest_sha256") != sha256(manifest_bytes).hexdigest():
        raise ValueError("Stage 8 receipt does not bind the foundation manifest bytes")
    if receipt_foundation_id != foundation_id:
        raise ValueError("Stage 8 receipt foundation identity differs from its manifest")
    if receipt_closure_id != closure_id:
        raise ValueError("Stage 8 receipt closure identity differs from its manifest")
    return foundation_id, closure_id, verification_id


def authenticate_ibkr_foundation_for_r2(
    *,
    foundation_path: Path,
    receipt_path: Path,
    adapter_identity: IBKRHistoricalAdapterIdentity,
    evidence_class: EvidenceClass,
    holdout_target_source: R2HoldoutTargetSource | None = None,
    promotion_path: Path | None = None,
) -> AuthenticatedR2Foundation:
    """Authenticate Stage 8 and adapt only its immediate semantic inputs for R2."""
    promotion_id: str | None = None
    g2_feature_source: IBKRG2FeatureSourceAuthority | None = None
    if evidence_class is EvidenceClass.CONFIRMATORY:
        if holdout_target_source is None or promotion_path is None:
            raise ValueError("confirmatory Stage 8 authentication requires holdout and promotion")
        try:
            promotion = authenticate_ibkr_foundation_promotion(
                foundation_path, receipt=receipt_path, promotion=promotion_path
            )
        except ValueError as exc:
            raise ValueError("confirmatory Stage 8 promotion attestation is invalid") from exc
        promotion_id = promotion.promotion_sha256
    elif promotion_path is not None:
        raise ValueError("Stage 8 promotion is valid only for confirmatory R2 work")
    if holdout_target_source is None:
        foundation, foundation_id = load_ibkr_foundation_with_identity(
            foundation_path, receipt=receipt_path
        )
    else:
        foundation, foundation_id, g2_feature_source = (
            _load_ibkr_foundation_outcome_blind_with_g2_authority(
                foundation_path,
                receipt=receipt_path,
                holdout_target_source=holdout_target_source,
            )
        )
    stage8_id, closure_id, verification_id = _stage8_authority_ids(foundation_path, receipt_path)
    if foundation_id != stage8_id or adapter_identity.foundation_bundle_id != stage8_id:
        raise ValueError("Stage 8 foundation identity differs from its adapter authority")
    semantic_inputs = build_ibkr_r2_foundation_inputs(
        foundation, foundation_bundle_id=stage8_id, adapter_identity=adapter_identity
    )
    return AuthenticatedR2Foundation(
        foundation_id=stage8_id,
        closure_id=closure_id,
        verification_id=verification_id,
        source_class=MarketDataSourceClass.IBKR_HISTORICAL_RESEARCH,
        evidence_class=evidence_class,
        semantic_inputs=semantic_inputs,
        g2_feature_source=g2_feature_source if holdout_target_source is not None else None,
        promotion_id=promotion_id,
        bundle_path=foundation_path,
        receipt_path=receipt_path,
        promotion_path=promotion_path,
    )


def _manifest_payload(manifest: R2FeatureManifest) -> dict[str, JsonValue]:
    return manifest.as_json()


def _load_and_verify_feature_manifests(
    *,
    verified: R1FoundationBindings,
    experiment: R2ExperimentConfig,
    feature_manifest_paths: dict[str, Path],
    root: Path,
    clock: Clock,
) -> dict[str, dict[str, JsonValue]]:
    foundation = _foundation_inputs(verified)
    store = ParquetR2FeatureStore(root, clock)
    payloads: dict[str, dict[str, JsonValue]] = {}
    for name in sorted(feature_manifest_paths):
        path = feature_manifest_paths[name]
        manifest = store.read_manifest(path)
        verify_raw_feature_manifest_bindings(
            manifest, foundation, experiment, feature_set_name=name
        )
        payloads[name] = _manifest_payload(manifest)
    return payloads


def _descriptor_payload(
    *,
    foundation_bundle_id: str,
    experiment: R2ExperimentConfig,
    feature_names: tuple[str, ...],
    run_kind: str,
    identities: dict[str, str],
    representative_profile: str | None = None,
    foundation_authority: AuthenticatedR2Foundation | None = None,
) -> dict[str, JsonValue]:
    semantic: dict[str, JsonValue] = {
        "contract": OOF_DESCRIPTOR_CONTRACT,
        "schema_version": 1,
        "foundation_bundle_id": foundation_bundle_id,
        "r1_bundle_id": experiment.r1_bundle_id,
        "experiment_configuration_id": experiment.configuration_id,
        "foundation_configuration_id": experiment.foundation_configuration_id,
        "observation_dataset_id": experiment.observation_dataset_id,
        "panel_dataset_id": experiment.panel_dataset_id,
        "target_dataset_id": experiment.target_dataset_id,
        "fold_dataset_id": experiment.fold_dataset_id,
        "source_class": experiment.market_data_source_class.value,
        "evidence_class": experiment.evidence_class.value,
        "feature_sets": list(feature_names),
        "run_kind": run_kind,
        "holdout_range": [item.isoformat() for item in experiment.holdout_range],
        "acceptance_thresholds": dict(experiment.acceptance_thresholds),
        "ordered_instruments": list(experiment.ordered_instruments),
        "instrument_roles": {
            instrument: role.value for instrument, role in experiment.instrument_roles.items()
        },
        "horizons_seconds": [int(horizon.total_seconds()) for horizon in experiment.horizons],
        "feature_windows_seconds": [
            int(window.total_seconds()) for window in experiment.feature_windows
        ],
        "alpha_grid": list(experiment.alpha_grid),
        "inner_validation_policy": experiment.inner_validation_policy,
        "preprocessing_policy": experiment.preprocessing_policy,
        "pooled_weighting_policy": experiment.pooled_weighting_policy,
        "ridge_solver": experiment.ridge_solver,
        "ridge_tolerance": experiment.ridge_tolerance,
        "ridge_max_iterations": experiment.ridge_max_iterations,
        "minimum_training_rows": experiment.minimum_training_rows,
        "minimum_inner_validation_rows": experiment.minimum_inner_validation_rows,
        "minimum_outer_validation_rows": experiment.minimum_outer_validation_rows,
        "model_selection_policy": experiment.model_selection_policy,
        "metric_policy": experiment.metric_policy,
        "forecast_bucket_policy": experiment.forecast_bucket_policy,
        "state_bucket_policy": experiment.state_bucket_policy,
        "target_instruments": list(experiment.target_instruments),
        "primary_horizon_seconds": experiment.primary_horizon.total_seconds(),
        "holdout_excluded": True,
    }
    if foundation_authority is not None:
        semantic["foundation_authority"] = foundation_authority.identity_json()
    if representative_profile is not None:
        semantic["representative_profile"] = representative_profile
    descriptor_id = sha256(canonical_bytes(semantic)).hexdigest()
    provenance = {field: identities[field] for field in _OOF_DESCRIPTOR_PROVENANCE_FIELDS}
    return {**semantic, **provenance, "descriptor_id": descriptor_id}


def _validate_representative_ibkr_historical_v1(
    verified: R1FoundationBindings,
    experiment: R2ExperimentConfig,
    *,
    expected_evidence_class: EvidenceClass = EvidenceClass.IMPLEMENTATION,
) -> None:
    """Admit only the fixed source-specific IBKR historical profile."""

    validate_ibkr_historical_profile(experiment, expected_evidence_class=expected_evidence_class)
    if experiment.market_data_source_class is not MarketDataSourceClass.IBKR_HISTORICAL_RESEARCH:
        raise ValueError("IBKR historical representative run has the wrong source class")
    if tuple(verified.bundle.ordered_instruments) != experiment.ordered_instruments:
        raise ValueError("IBKR historical foundation universe differs from the experiment")
    start = verified.bundle.range_start
    end = verified.bundle.range_end
    total_seconds = (end - start).total_seconds()
    if total_seconds <= 0 or experiment.holdout_range[1] != end:
        raise ValueError(
            "IBKR historical representative holdout is not the final foundation interval"
        )
    expected_holdout_start = start + timedelta(seconds=total_seconds * 0.8)
    if experiment.holdout_range[0] != expected_holdout_start:
        raise ValueError("IBKR historical representative holdout must be the final 20 percent")
    folds = verified.folds.folds
    if len(folds) != 3:
        raise ValueError("IBKR historical representative run must contain exactly three folds")
    targets_by_id = {row.target_id: row for row in verified.targets.rows}
    for fold in folds:
        for target_id in fold.validation_target_ids:
            target = targets_by_id[target_id]
            if (
                target.target_end_time > experiment.holdout_range[0]
                or target.target_freeze_at > experiment.holdout_range[0]
                or target.target_available_at > experiment.holdout_range[0]
            ):
                raise ValueError("IBKR historical validation dependency reaches the holdout")


def _validate_representative_capture_v4(
    verified: R1FoundationBindings, experiment: R2ExperimentConfig
) -> None:
    """Admit only the fixed, non-qualifying capture-v4 integration specification."""
    if experiment.market_data_source_class is not MarketDataSourceClass.IG_NATIVE_CAPTURE:
        raise ValueError("representative run must use IG_NATIVE_CAPTURE")
    if tuple(verified.bundle.ordered_instruments) != _CAPTURE_V4_UNIVERSE:
        raise ValueError("representative foundation is not the ordered capture-v4 universe")
    if experiment.ordered_instruments != _CAPTURE_V4_UNIVERSE:
        raise ValueError("representative experiment is not the ordered capture-v4 universe")
    expected_roles = {
        instrument: InstrumentRole.CONTEXT
        if instrument == "index:volatility"
        else InstrumentRole.TARGET
        for instrument in _CAPTURE_V4_UNIVERSE
    }
    if dict(experiment.instrument_roles) != expected_roles:
        raise ValueError("representative experiment has the wrong capture-v4 instrument roles")
    expected_eligibility = set(_CAPTURE_V4_UNIVERSE) - {"index:volatility"}
    if set(experiment.target_instrument_eligibility) != expected_eligibility:
        raise ValueError("representative experiment eligibility does not cover capture-v4 targets")
    for instrument, decision in experiment.target_instrument_eligibility.items():
        expected = (
            FeatureEligibility.ELIGIBLE
            if instrument in _CAPTURE_V4_TARGETS
            else FeatureEligibility.NOT_ELIGIBLE
        )
        if decision.state is not expected:
            raise ValueError(
                "representative target eligibility differs from the fixed specification"
            )
    if (
        experiment.target_instruments != _CAPTURE_V4_TARGETS
        or experiment.confirmatory_target_instruments != _CAPTURE_V4_TARGETS
    ):
        raise ValueError("representative experiment has the wrong six eligible targets")
    expected_groups = {
        frozenset({"fx:aud-usd", "fx:eur-usd"}),
        frozenset({"index:australia-200", "index:us-500"}),
        frozenset({"commodity:spot-gold", "commodity:us-crude"}),
    }
    actual_groups = {
        frozenset(
            instrument
            for instrument in _CAPTURE_V4_TARGETS
            if experiment.market_groups[instrument] == group
        )
        for group in set(experiment.market_groups.values())
    }
    if actual_groups != expected_groups:
        raise ValueError("representative market groups do not match the fixed pairs")
    if experiment.horizons != (timedelta(minutes=15),):
        raise ValueError("representative experiment must use only the 15-minute horizon")
    expected_features = {
        "L0": (FeatureFamily.LOCAL_RETURNS, FeatureFamily.TIME_AVAILABILITY),
        "L1": (
            FeatureFamily.LOCAL_RETURNS,
            FeatureFamily.TIME_AVAILABILITY,
            FeatureFamily.LOCAL_VOLATILITY_RANGE,
        ),
        "P0": (
            FeatureFamily.LOCAL_RETURNS,
            FeatureFamily.TIME_AVAILABILITY,
            FeatureFamily.LOCAL_VOLATILITY_RANGE,
        ),
        "P1": (
            FeatureFamily.LOCAL_RETURNS,
            FeatureFamily.TIME_AVAILABILITY,
            FeatureFamily.LOCAL_VOLATILITY_RANGE,
            FeatureFamily.POOLED_CROSS_ASSET,
        ),
    }
    if {item.name: item.families for item in experiment.feature_sets} != expected_features:
        raise ValueError("representative feature ladder differs from the fixed specification")
    for family in (FeatureFamily.SPREAD, FeatureFamily.QUOTE_IMBALANCE):
        if experiment.feature_eligibility[family].state is not FeatureEligibility.NOT_ELIGIBLE:
            raise ValueError("spread and quote imbalance must remain ineligible")
    if experiment.alpha_grid != (0.01, 0.1, 1.0, 10.0):
        raise ValueError("representative alpha grid differs from the fixed specification")
    if (
        experiment.ridge_solver != "lsqr"
        or experiment.ridge_tolerance != 1e-8
        or experiment.ridge_max_iterations != 10_000
        or experiment.pooled_weighting_policy != "EQUAL_INSTRUMENT_TOTAL_WEIGHT_MEAN_ONE"
        or experiment.minimum_training_rows != 100
        or experiment.minimum_inner_validation_rows != 20
        or experiment.minimum_outer_validation_rows != 20
    ):
        raise ValueError("representative numerical policy differs from the fixed specification")
    start = verified.bundle.range_start
    end = verified.bundle.range_end
    total_minutes = int((end - start).total_seconds() // 60)
    if start.second or start.microsecond or end.second or end.microsecond or total_minutes % 10:
        raise ValueError("representative capture interval must align to completed UTC minutes")

    def boundary(tenths: int) -> datetime:
        return start + timedelta(minutes=total_minutes * tenths // 10)

    if experiment.holdout_range != (boundary(8), end):
        raise ValueError("representative integration holdout does not use the final 20 percent")
    folds = verified.folds.folds
    _validate_representative_fold_layout(
        folds,
        start,
        end,
        experiment.holdout_range,
        embargo=verified.configuration.embargo,
    )
    targets_by_id = {row.target_id: row for row in verified.targets.rows}
    for fold in folds:
        for target_id in fold.validation_target_ids:
            target = targets_by_id[target_id]
            if (
                target.target_end_time > experiment.holdout_range[0]
                or target.target_freeze_at > experiment.holdout_range[0]
                or target.target_available_at > experiment.holdout_range[0]
            ):
                raise ValueError(
                    "representative validation dependency reaches the disposable holdout"
                )


def _validate_representative_fold_layout(
    folds: tuple[Fold, ...],
    start: datetime,
    end: datetime,
    holdout_range: tuple[datetime, datetime],
    *,
    embargo: timedelta,
) -> None:
    total_minutes = int((end - start).total_seconds() // 60)

    def boundary(tenths: int) -> datetime:
        return start + timedelta(minutes=total_minutes * tenths // 10)

    initial_training_cutoff = boundary(5)
    holdout_start = boundary(8)
    available_validation = holdout_start - initial_training_cutoff - 3 * embargo
    available_seconds = int(available_validation.total_seconds())
    if (
        len(folds) != 3
        or holdout_range != (holdout_start, end)
        or embargo < timedelta(0)
        or available_seconds <= 0
        or available_seconds % (3 * 60)
    ):
        raise ValueError("representative fold boundaries cannot satisfy the fixed 50/30/20 rule")
    validation_duration = timedelta(seconds=available_seconds // 3)
    expected_training_cutoff = initial_training_cutoff
    for fold in folds:
        expected_validation_start = expected_training_cutoff + embargo
        expected_validation_end = expected_validation_start + validation_duration
        if (
            fold.training_start != start
            or fold.training_cutoff != expected_training_cutoff
            or fold.validation_start != expected_validation_start
            or fold.validation_end != expected_validation_end
            or fold.embargo_end != expected_validation_start
            or not fold.holdout_excluded
            or fold.validation_end > holdout_start
        ):
            raise ValueError("representative fold boundaries differ from the fixed 50/30/20 rule")
        expected_training_cutoff = expected_validation_end
    if folds[-1].validation_end != holdout_start:
        raise ValueError("representative validation interval does not end at the holdout boundary")


def _descriptor_reference(
    *,
    output: Path,
    relative_path: str,
    payload: Mapping[str, object],
) -> ArtifactReference:
    return reference_for_json(
        path=relative_path,
        contract=str(payload["contract"]),
        semantic_id=str(payload["descriptor_id"]),
        content=payload,
    )


def _load_feature_datasets(
    *,
    verified: R1FoundationBindings,
    experiment: R2ExperimentConfig,
    feature_manifest_paths: dict[str, Path],
    root: Path,
    clock: Clock,
    recompute_rows: bool = True,
) -> tuple[dict[str, R2FeatureDataset], dict[str, R2FeatureManifest]]:
    foundation = _foundation_inputs(verified)
    store = ParquetR2FeatureStore(root, clock)
    datasets: dict[str, R2FeatureDataset] = {}
    manifests: dict[str, R2FeatureManifest] = {}
    for name in sorted(feature_manifest_paths):
        manifest = store.read_manifest(feature_manifest_paths[name])
        verify_raw_feature_manifest_bindings(
            manifest, foundation, experiment, feature_set_name=name
        )
        rows = tuple(store.iter_rows(feature_manifest_paths[name]))
        if recompute_rows:
            verify_raw_feature_rows(iter(rows), foundation, experiment, feature_set_name=name)
        dataset = R2FeatureDataset.create(
            rows,
            feature_schema=manifest.feature_schema,
            feature_set_name=name,
            feature_set_id=manifest.feature_set_id,
            observation_dataset_id=manifest.observation_dataset_id,
            panel_dataset_id=manifest.panel_dataset_id,
            target_dataset_id=manifest.target_dataset_id,
            fold_dataset_id=manifest.fold_dataset_id,
            experiment_configuration_id=manifest.experiment_configuration_id,
            evidence_class=manifest.evidence_class,
            market_data_source_class=experiment.market_data_source_class,
        )
        if dataset.dataset_id != manifest.semantic_dataset_id:
            raise ValueError(
                "feature manifest semantic dataset identity differs from replayed rows"
            )
        datasets[name] = dataset
        manifests[name] = manifest
    return datasets, manifests


def _dataset_payload(
    dataset: R2FeatureDataset, manifest: R2FeatureManifest | Mapping[str, object]
) -> dict[str, object]:
    manifest_payload = (
        manifest.as_json() if isinstance(manifest, R2FeatureManifest) else dict(manifest)
    )
    return {
        "contract": dataset.CONTRACT,
        "schema_version": dataset.SCHEMA_VERSION,
        "dataset_id": dataset.dataset_id,
        "manifest": manifest_payload,
        **{
            key: value
            for key, value in dataset.manifest_json().items()
            if key not in {"contract", "schema_version", "dataset_id"}
        },
        "rows": [row.as_json() for row in dataset.rows],
    }


def _forecast_payload(
    dataset: ForecastDataset,
    *,
    source_class: MarketDataSourceClass,
    evidence_class: EvidenceClass,
) -> dict[str, object]:
    return {
        "contract": dataset.CONTRACT,
        "schema_version": 1,
        "dataset_id": dataset.dataset_id,
        "observation_dataset_id": dataset.observation_dataset_id,
        "panel_dataset_id": dataset.panel_dataset_id,
        "target_dataset_id": dataset.target_dataset_id,
        "fold_dataset_id": dataset.fold_dataset_id,
        "source_class": source_class.value,
        "evidence_class": evidence_class.value,
        "rows": [row.as_json() for row in dataset.rows],
    }


def _payload_identity(payload: Mapping[str, object]) -> str:
    contract = payload.get("contract")
    if not isinstance(contract, str):
        raise ValueError("R2 child payload has no contract")
    return _canonical_payload_identity(contract, payload)


def _child_reference(path: str, payload: Mapping[str, object]) -> ArtifactReference:
    contract = payload.get("contract")
    if not isinstance(contract, str):
        raise ValueError("R2 child payload has no contract")
    return reference_for_json(
        path=path,
        contract=contract,
        semantic_id=_payload_identity(payload),
        content=payload,
    )


def _holdout_source_binding_payload(source_id: str, closure_id: str) -> dict[str, JsonValue]:
    semantic: dict[str, JsonValue] = {
        "contract": R2_HOLDOUT_SOURCE_BINDING_CONTRACT,
        "schema_version": 1,
        "source_id": source_id,
        "source_closure_id": closure_id,
    }
    semantic["binding_id"] = sha256(canonical_bytes(semantic)).hexdigest()
    return semantic


def _configuration_record(
    *,
    family: ModelFamily,
    feature_set_id: str | None,
    forecast_dataset_id: str | None,
    reason: str,
    market_data_source_class: MarketDataSourceClass,
) -> ConfigurationRecord:
    semantic = {
        "model_family": family.value,
        "feature_set_id": feature_set_id,
        "forecast_dataset_id": forecast_dataset_id,
        "reason": reason,
        "market_data_source_class": market_data_source_class.value,
    }
    return ConfigurationRecord(
        configuration_id=sha256(canonical_bytes(semantic)).hexdigest(),
        model_family=family,
        feature_set_id=feature_set_id,
        disposition=ConfigurationDisposition.EVALUATED,
        reason=reason,
        forecast_dataset_id=forecast_dataset_id,
        evaluated_model_manifest_id=None,
        market_data_source_class=market_data_source_class,
    )


def _model_forecasts(
    fold_results: tuple[Any, ...],
    *,
    observation_dataset_id: str,
    panel_dataset_id: str,
    target_dataset_id: str,
    fold_dataset_id: str,
) -> ForecastDataset:
    rows = tuple(row for result in fold_results for row in result.forecasts.rows)
    return ForecastDataset.create(
        rows,
        observation_dataset_id=observation_dataset_id,
        panel_dataset_id=panel_dataset_id,
        target_dataset_id=target_dataset_id,
        fold_dataset_id=fold_dataset_id,
    )


def _synthetic_pipeline_inputs(
    *,
    target_names: tuple[str, ...] = ("index:synthetic-a", "index:synthetic-b"),
    context_name: str = "index:volatility",
    fixture_name: str = "r2-synthetic",
    market_groups: Mapping[str, str] | None = None,
    application_identity: str = "synthetic",
    image_identity: str = "qtrad@sha256:" + "1" * 64,
    adapter_identity: IBKRHistoricalAdapterIdentity | None = None,
    evidence_class: EvidenceClass = EvidenceClass.IMPLEMENTATION,
    include_holdout_target: bool = False,
    qualifying_confirmatory: bool = False,
    market_data_source_class: MarketDataSourceClass = (
        MarketDataSourceClass.IBKR_HISTORICAL_RESEARCH
    ),
) -> tuple[
    R1FoundationBindings,
    R2ExperimentConfig,
    dict[str, R2FeatureDataset],
]:
    """Create deterministic typed inputs for the same R2 build path used in production."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    horizon = timedelta(minutes=15)
    holdout = (
        (
            start + timedelta(weeks=12, minutes=1),
            start + timedelta(weeks=16, minutes=1),
        )
        if qualifying_confirmatory
        else (start + timedelta(hours=6), start + timedelta(hours=24))
    )
    ordered = (*target_names, context_name)
    r1_id = sha256(f"{fixture_name}-r1".encode()).hexdigest()
    observation_id = sha256(f"{fixture_name}-observations".encode()).hexdigest()
    foundation_id = sha256(f"{fixture_name}-foundation-config".encode()).hexdigest()
    panel_id = sha256(f"{fixture_name}-panel".encode()).hexdigest()

    def eligibility(subject: str, state: FeatureEligibility) -> EligibilityDecision:
        return EligibilityDecision.create(
            subject=subject,
            state=state,
            evidence_start=start - timedelta(days=1),
            evidence_end=start + timedelta(hours=1),
            reason="deterministic software-verification fixture",
        )

    roles = {name: InstrumentRole.TARGET for name in target_names}
    roles[context_name] = InstrumentRole.CONTEXT
    resolved_market_groups = market_groups or {
        target_names[0]: "synthetic-0",
        target_names[1]: "synthetic-1",
    }
    feature_eligibility = {
        family: eligibility(
            family.value,
            FeatureEligibility.NOT_ELIGIBLE
            if family in {FeatureFamily.SPREAD, FeatureFamily.QUOTE_IMBALANCE}
            else FeatureEligibility.ELIGIBLE,
        )
        for family in FeatureFamily
    }
    local_families = (
        FeatureFamily.LOCAL_RETURNS,
        FeatureFamily.TIME_AVAILABILITY,
        FeatureFamily.LOCAL_VOLATILITY_RANGE,
    )
    experiment = R2ExperimentConfig(
        name=fixture_name,
        schema_version=2,
        r1_bundle_id=r1_id,
        observation_dataset_id=observation_id,
        foundation_configuration_id=foundation_id,
        panel_dataset_id=panel_id,
        target_dataset_id="a" * 64,
        fold_dataset_id="b" * 64,
        r1_application_version=application_identity,
        r1_image_identity=image_identity,
        ordered_instruments=ordered,
        instrument_roles=roles,
        target_instrument_eligibility={
            name: eligibility(name, FeatureEligibility.ELIGIBLE) for name in target_names
        },
        target_instruments=target_names,
        confirmatory_target_instruments=target_names,
        market_groups=resolved_market_groups,
        horizons=(horizon,),
        primary_horizon=horizon,
        feature_sets=(
            FeatureSet("L0", local_families[:2]),
            FeatureSet("L1", local_families),
            FeatureSet("P0", local_families),
            FeatureSet("P1", (*local_families, FeatureFamily.POOLED_CROSS_ASSET)),
        ),
        feature_windows=(timedelta(minutes=1), timedelta(minutes=5)),
        feature_coverage_thresholds={family: 0.0 for family in FeatureFamily},
        feature_eligibility=feature_eligibility,
        preprocessing_policy="TRAINING_MEDIAN_STANDARDISE_V1",
        alpha_grid=(0.01, 0.1, 1.0, 10.0),
        inner_validation_policy="CHRONOLOGICAL_TAIL_PURGED_V1",
        ridge_solver="lsqr",
        ridge_tolerance=1e-8,
        ridge_max_iterations=10_000,
        pooled_weighting_policy="EQUAL_INSTRUMENT_TOTAL_WEIGHT_MEAN_ONE",
        minimum_training_rows=100 if qualifying_confirmatory else 2,
        minimum_inner_validation_rows=20 if qualifying_confirmatory else 1,
        minimum_outer_validation_rows=20 if qualifying_confirmatory else 1,
        metric_policy="R2_METRICS_V1",
        forecast_bucket_policy="TRAINING_QUANTILES_V1",
        state_bucket_policy="TRAINING_THRESHOLDS_V1",
        model_selection_policy="OOF_PRIMARY_MSE_V1",
        acceptance_thresholds={
            "maximum_best_instrument_contribution": 1.0,
            "maximum_best_period_contribution": 1.0,
            "maximum_primary_mse_degradation": 0.0,
            "minimum_common_support": 0.9,
            "minimum_improving_fold_proportion": 0.0,
            "minimum_improving_instrument_proportion": 0.0,
        },
        holdout_range=holdout,
        numeric_replay_relative_tolerance=1e-10,
        numeric_replay_absolute_tolerance=1e-12,
        evidence_class=evidence_class,
        market_data_source_class=market_data_source_class,
        source_adapter_identity=adapter_identity.as_json() if adapter_identity else None,
        model_families=tuple(ModelFamily),
    )
    targets_rows: list[TargetRow] = []
    if qualifying_confirmatory:
        blocks = (
            (start, start + timedelta(weeks=6), 100),
            (
                start + timedelta(weeks=6, minutes=1),
                start + timedelta(weeks=8, minutes=1),
                20,
            ),
            (
                start + timedelta(weeks=8, minutes=1),
                start + timedelta(weeks=10, minutes=1),
                20,
            ),
            (
                start + timedelta(weeks=10, minutes=1),
                holdout[0],
                20,
            ),
            (holdout[0], holdout[1], 20),
        )
        target_decisions = [
            block_start
            + (block_end - block_start - horizon - timedelta(minutes=1)) * (index / (count - 1))
            for block_start, block_end, count in blocks
            for index in range(count)
        ]
    else:
        target_decisions = [start + timedelta(minutes=15 * index) for index in range(8)]
    if include_holdout_target:
        target_decisions.append(holdout[0])
    for index, decision in enumerate(target_decisions):
        for instrument_index, instrument in enumerate(target_names):
            targets_rows.append(
                TargetRow(
                    instrument_id=instrument,
                    decision_time=decision,
                    horizon=horizon,
                    target_basis=PriceBasis.MID,
                    target_revision_policy="LATEST_AVAILABLE_BEFORE_FREEZE",
                    target_start_time=decision,
                    target_end_time=decision + horizon,
                    target_freeze_at=decision + horizon,
                    target_available_at=decision + horizon,
                    label_start_close=Decimal("100"),
                    label_end_close=Decimal("101"),
                    log_return=(
                        0.01 * (index + 1) * (instrument_index + 1)
                        if qualifying_confirmatory
                        else 0.01 * (index + instrument_index)
                    ),
                    return_disposition=ReturnDisposition.VALID,
                    start_event_id=UUID(int=index * 2 + instrument_index + 1),
                    end_event_id=UUID(int=index * 2 + instrument_index + 2),
                    upper_log_excursion=0.02,
                    lower_log_excursion=-0.01,
                    excursion_disposition=ExcursionDisposition.VALID,
                )
            )
    targets = TargetDataset.create(
        targets_rows,
        observation_dataset_id=observation_id,
        foundation_configuration_id=foundation_id,
    )
    fold_values: list[Fold] = []
    fold_ranges = (
        tuple(
            (
                start + timedelta(weeks=6 + 2 * index, minutes=1),
                start + timedelta(weeks=8 + 2 * index, minutes=1),
            )
            for index in range(3)
        )
        if qualifying_confirmatory
        else ((start + timedelta(minutes=90), start + timedelta(minutes=120)),)
    )
    for index, (validation_start, validation_end) in enumerate(fold_ranges):
        training_cutoff = (
            validation_start - timedelta(minutes=1)
            if qualifying_confirmatory
            else start + timedelta(minutes=75)
        )
        training_ids = tuple(
            row.target_id for row in targets.rows if row.target_end_time <= training_cutoff
        )
        validation_ids = tuple(
            row.target_id
            for row in targets.rows
            if validation_start <= row.target_start_time < validation_end
        )
        fold_values.append(
            Fold(
                fold_id=f"synthetic-outer-{index}",
                training_start=start,
                training_cutoff=training_cutoff,
                validation_start=validation_start,
                validation_end=validation_end,
                embargo_end=validation_start,
                training_target_ids=training_ids,
                validation_target_ids=validation_ids,
                holdout_excluded=True,
                membership_hash=membership_hash(training_ids, validation_ids),
            )
        )
    folds = FoldDataset.create(
        fold_values,
        target_dataset_id=targets.dataset_id,
        foundation_configuration_id=foundation_id,
    )
    experiment = replace(
        experiment,
        target_dataset_id=targets.dataset_id,
        fold_dataset_id=folds.dataset_id,
    )
    datasets: dict[str, R2FeatureDataset] = {}
    for feature_set in experiment.feature_sets:
        schema = feature_schema_for_set(experiment, feature_set.name)
        preprocessing_schema = derive_r2_preprocessing_schema(schema)
        set_id = feature_set_id(
            experiment.configuration_id,
            feature_set.name,
            schema,
            experiment.market_data_source_class,
        )
        set_rows = []
        for target in targets.rows:
            values = []
            for definition, transformed in zip(schema, preprocessing_schema.features, strict=True):
                if transformed.kind is PreprocessingFeatureKind.BINARY_INDICATOR:
                    value = 1.0
                elif definition.family is FeatureFamily.LOCAL_RETURNS:
                    value = float(target.decision_time.minute) / 15.0
                elif definition.family is FeatureFamily.POOLED_CROSS_ASSET:
                    value = float(target.instrument_id == target_names[1])
                elif definition.family is FeatureFamily.TIME_AVAILABILITY:
                    value = 1.0
                else:
                    value = 0.0
                values.append(RawFeatureValue(definition.name, value))
            set_rows.append(
                RawFeatureRow(
                    target.instrument_id,
                    target.decision_time,
                    target.decision_time,
                    target.decision_time,
                    set_id,
                    tuple(values),
                )
            )
        dataset = R2FeatureDataset.create(
            set_rows,
            feature_schema=schema,
            feature_set_name=feature_set.name,
            feature_set_id=set_id,
            observation_dataset_id=observation_id,
            panel_dataset_id=panel_id,
            target_dataset_id=targets.dataset_id,
            fold_dataset_id=folds.dataset_id,
            experiment_configuration_id=experiment.configuration_id,
            evidence_class=experiment.evidence_class,
            market_data_source_class=experiment.market_data_source_class,
        )
        datasets[feature_set.name] = dataset
    evidence: dict[str, JsonValue] = {
        "availability_delay_report": {},
        "revision_delay_report": {},
        "data_gaps": [],
        "source_active_intervals": {
            name: [[start.isoformat(), holdout[1].isoformat()]] if qualifying_confirmatory else []
            for name in ordered
        },
        "lineage_summary": {},
        "observation_bounds": {
            "interval_start": start.isoformat(),
            "interval_end": holdout[1].isoformat(),
        },
    }
    availability_id = _availability_dataset_id(observation_id, evidence)
    verified = cast(
        R1FoundationBindings,
        SimpleNamespace(
            bundle=SimpleNamespace(
                foundation_id=r1_id,
                market_data_source_class=experiment.market_data_source_class,
                ordered_instruments=ordered,
                range_start=start,
                range_end=holdout[1],
                configuration=SimpleNamespace(dataset_id=foundation_id),
                observations=SimpleNamespace(dataset_id=observation_id),
                availability=SimpleNamespace(dataset_id=availability_id),
                panel=SimpleNamespace(dataset_id=panel_id),
                targets=SimpleNamespace(dataset_id=targets.dataset_id),
                folds=SimpleNamespace(dataset_id=folds.dataset_id),
                build_summary={
                    "application_version": application_identity,
                    "image_identity": image_identity,
                },
            ),
            configuration=SimpleNamespace(
                configuration_id=foundation_id,
                observation_dataset_id=observation_id,
                ordered_instruments=ordered,
                instrument_roles=roles,
                target_horizons=(horizon,),
                holdout_range=holdout,
                range_start=start,
                range_end=holdout[1],
                availability_basis=AvailabilityBasis.PERSISTED_AT,
            ),
            observations=SimpleNamespace(
                dataset_id=observation_id,
                selection_policies={"availability_basis": "persisted_at"},
            ),
            panel=SimpleNamespace(
                dataset_id=panel_id,
                observation_dataset_id=observation_id,
                foundation_configuration_id=foundation_id,
            ),
            targets=targets,
            folds=folds,
            forecasts=SimpleNamespace(),
            availability_evidence=evidence,
        ),
    )
    return verified, experiment, datasets


def _materialise_synthetic_feature_manifests(
    root: Path,
    experiment: R2ExperimentConfig,
    datasets: Mapping[str, R2FeatureDataset],
) -> dict[str, Path]:
    """Publish and independently verify canonical synthetic features through Parquet."""
    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    clock = cast(Clock, SimpleNamespace(now=lambda: created_at))
    store = ParquetR2FeatureStore(root, clock)
    paths: dict[str, Path] = {}
    for feature_set in experiment.feature_sets:
        dataset = datasets[feature_set.name]
        relative_path = Path("features") / f"{feature_set.name}.json"
        written = store.write(
            relative_path,
            dataset.rows,
            feature_set_name=dataset.feature_set_name,
            feature_set_id=dataset.feature_set_id,
            feature_schema=dataset.feature_schema,
            observation_dataset_id=dataset.observation_dataset_id,
            panel_dataset_id=dataset.panel_dataset_id,
            target_dataset_id=dataset.target_dataset_id,
            fold_dataset_id=dataset.fold_dataset_id,
            experiment_configuration_id=dataset.experiment_configuration_id,
            evidence_class=dataset.evidence_class,
            holdout_excluded=True,
            application_version="synthetic-software-verification-v1",
            image_identity="qtrad-synthetic@sha256:" + "1" * 64,
            market_data_source_class=dataset.market_data_source_class,
        )
        verified = store.verify(relative_path)
        loaded = store.load(relative_path)
        if verified != written or loaded != dataset:
            raise ValueError("synthetic Parquet feature publication did not independently replay")
        paths[feature_set.name] = relative_path
    return paths


def _build_ibkr_synthetic_oof_from_fixture(
    output: Path,
    *,
    runtime_provenance: Mapping[str, str] | None = None,
) -> Path:
    """Build an independent deterministic IBKR synthetic OOF child."""
    fixture_name = "r2-ibkr-historical-synthetic"
    identities = (
        dict(runtime_provenance) if runtime_provenance is not None else runtime_identities()
    )
    application_identity = identities["application_identity"]
    image_identity = identities["image_identity"]
    foundation_bundle_id = sha256(f"{fixture_name}-r1".encode()).hexdigest()
    adapter_identity = IBKRHistoricalAdapterIdentity.create(
        foundation_bundle_id=foundation_bundle_id,
        application_identity=application_identity,
        image_identity=image_identity,
    )
    verified, experiment, datasets = _synthetic_pipeline_inputs(
        target_names=tuple(sorted(IBKR_HISTORICAL_TARGETS)),
        context_name="index:synthetic-volatility",
        fixture_name=fixture_name,
        market_groups=IBKR_HISTORICAL_GROUPS,
        application_identity=application_identity,
        image_identity=image_identity,
        adapter_identity=adapter_identity,
    )
    validate_ibkr_historical_profile(experiment)
    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    clock = cast(Clock, SimpleNamespace(now=lambda: created_at))
    with TemporaryDirectory() as temporary:
        research_root = Path(temporary)
        feature_paths = _materialise_synthetic_feature_manifests(
            research_root, experiment, datasets
        )
        return build_oof_bundle(
            verified=verified,
            experiment=experiment,
            feature_manifest_paths=feature_paths,
            research_root=research_root,
            clock=clock,
            output=output,
            run_kind="SYNTHETIC",
            representative_profile=IBKR_HISTORICAL_PROFILE,
            runtime_provenance=identities,
        )


def _build_synthetic_oof(
    output: Path,
    *,
    runtime_provenance: Mapping[str, str] | None = None,
) -> Path:
    verified, experiment, datasets = _synthetic_pipeline_inputs()
    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    clock = cast(Clock, SimpleNamespace(now=lambda: created_at))
    with TemporaryDirectory() as temporary:
        research_root = Path(temporary)
        feature_paths = _materialise_synthetic_feature_manifests(
            research_root, experiment, datasets
        )
        return build_oof_bundle(
            verified=verified,
            experiment=experiment,
            feature_manifest_paths=feature_paths,
            research_root=research_root,
            clock=clock,
            output=output,
            run_kind="SYNTHETIC",
            runtime_provenance=runtime_provenance,
        )


def build_oof_bundle(
    *,
    verified: R1FoundationBindings,
    experiment: R2ExperimentConfig,
    feature_manifest_paths: dict[str, Path],
    research_root: Path,
    clock: Clock,
    output: Path,
    run_kind: str = "REPRESENTATIVE",
    foundation_authority: AuthenticatedR2Foundation | None = None,
    representative_profile: str | None = None,
    holdout_target_source: R2HoldoutTargetSource | None = None,
    holdout_target_source_authority: R2HoldoutTargetSourceAuthority | None = None,
    holdout_target_source_path: Path | None = None,
    experiment_path: Path | None = None,
    runtime_provenance: Mapping[str, str] | None = None,
) -> Path:
    """Build and persist the complete R2.C--F1 OOF run from authenticated children."""
    if run_kind not in {*_IMPLEMENTATION_RUN_KINDS, CONFIRMATORY_RUN_KIND}:
        raise ValueError("OOF descriptor has an unsupported run kind")
    if (
        experiment.evidence_class is EvidenceClass.CONFIRMATORY
        and run_kind != CONFIRMATORY_RUN_KIND
    ):
        raise ValueError("CONFIRMATORY evidence requires the CONFIRMATORY OOF run kind")
    if (
        experiment.evidence_class is not EvidenceClass.CONFIRMATORY
        and run_kind == CONFIRMATORY_RUN_KIND
    ):
        raise ValueError("CONFIRMATORY OOF runs require CONFIRMATORY evidence")
    if run_kind == CONFIRMATORY_RUN_KIND:
        if holdout_target_source is None:
            raise ValueError("confirmatory OOF build requires an authenticated target source")
        if foundation_authority is None:
            raise ValueError("confirmatory OOF build requires immediate foundation authority")
    if run_kind in {"REPRESENTATIVE", CONFIRMATORY_RUN_KIND}:
        if holdout_target_source is None:
            raise ValueError("OOF build requires an authenticated holdout target source")
        if holdout_target_source_authority is None:
            raise ValueError(
                "canonical OOF build requires an authenticated target source authority"
            )
        if holdout_target_source is not holdout_target_source_authority.source:
            raise ValueError("OOF target source must be the authority-owned source object")
        if foundation_authority is None:
            raise ValueError("canonical OOF build requires immediate foundation authority")
        if foundation_authority.foundation_id != verified.bundle.foundation_id:
            raise ValueError("R2 authority differs from the supplied semantic foundation")
        if foundation_authority.source_class is not experiment.market_data_source_class:
            raise ValueError("R2 authority source differs from the experiment")
        if foundation_authority.evidence_class is not experiment.evidence_class:
            raise ValueError("R2 authority evidence differs from the experiment")
        if holdout_target_source_path is not None and (
            holdout_target_source_path.absolute() != holdout_target_source_authority.manifest_path
        ):
            raise ValueError("OOF target source path differs from its authenticated authority")
        if experiment.market_data_source_class is MarketDataSourceClass.IBKR_HISTORICAL_RESEARCH:
            if representative_profile != IBKR_HISTORICAL_PROFILE:
                raise ValueError("IBKR historical OOF run requires IBKR_HISTORICAL_V1")
        elif representative_profile is not None:
            raise ValueError("representative profile is only valid for IBKR historical runs")
    foundation_source = getattr(verified.bundle, "market_data_source_class", None)
    if foundation_source is not experiment.market_data_source_class:
        raise ValueError("R2 experiment source class differs from the R1 foundation")
    datasets, manifests = _load_feature_datasets(
        verified=verified,
        experiment=experiment,
        feature_manifest_paths=feature_manifest_paths,
        root=research_root,
        clock=clock,
        recompute_rows=run_kind != "SYNTHETIC",
    )
    if holdout_target_source is not None:
        if verified.targets.dataset_id != holdout_target_source.source_target_dataset_id:
            raise ValueError("OOF target view differs from the authenticated holdout source")
        if tuple(verified.targets.rows) != holdout_target_source.pre_holdout_target_dataset.rows:
            raise ValueError(
                "OOF target rows are not the authenticated pre-holdout target projection"
            )
    if set(datasets) != _REQUIRED_FEATURE_SETS:
        raise ValueError("OOF build requires exactly L0/L1/P0/P1 feature datasets")
    if runtime_provenance is not None and (
        set(runtime_provenance) != _OOF_DESCRIPTOR_PROVENANCE_FIELDS
        or any(not value for value in runtime_provenance.values())
    ):
        raise ValueError("OOF replay runtime provenance is incomplete")
    identities = (
        dict(runtime_provenance) if runtime_provenance is not None else runtime_identities()
    )
    local_datasets = tuple(datasets[name] for name in ("L0", "L1"))
    pooled_datasets = (datasets["P0"], datasets["P1"])
    selections_local = tuple(
        build_r2_preprocessing_selection(
            verified,
            datasets[name],
            experiment,
            model_family=ModelFamily.LOCAL_RIDGE,
            horizon=experiment.primary_horizon,
            outer_fold_id=fold.fold_id,
            target_instruments=(instrument,),
            application_image_identity=identities["application_identity"],
            sklearn_library_identity=identities["sklearn_identity"],
        )
        for name in ("L0", "L1")
        for instrument in experiment.target_instruments
        for fold in verified.folds.folds
    )
    selections_pooled = tuple(
        build_pooled_preprocessing_selection(
            verified,
            datasets[name],
            experiment,
            model_family=(
                ModelFamily.POOLED_LOCAL_RIDGE
                if name == "P0"
                else ModelFamily.POOLED_CROSS_ASSET_RIDGE
            ),
            horizon=experiment.primary_horizon,
            outer_fold_id=fold.fold_id,
            target_instruments=experiment.target_instruments,
            application_image_identity=identities["application_identity"],
            sklearn_library_identity=identities["sklearn_identity"],
        )
        for name in ("P0", "P1")
        for fold in verified.folds.folds
    )
    local_result = build_local_ridge_oof(
        verified,
        local_datasets,
        experiment,
        selections_local,
        application_image_identity=identities["application_identity"],
        numpy_library_identity=identities["numpy_identity"],
        sklearn_library_identity=identities["sklearn_identity"],
    )
    pooled_result = build_pooled_ridge_oof(
        verified,
        pooled_datasets,
        experiment,
        selections_pooled,
        local_result,
        datasets["L1"],
        application_image_identity=identities["application_identity"],
        numpy_library_identity=identities["numpy_identity"],
        sklearn_library_identity=identities["sklearn_identity"],
    )
    models: list[EvaluationModel] = []
    for family, name in (
        (ModelFamily.POOLED_LOCAL_RIDGE, "P0"),
        (ModelFamily.POOLED_CROSS_ASSET_RIDGE, "P1"),
    ):
        fold_results = tuple(
            result for result in pooled_result.fold_results if result.fit.model_family is family
        )
        models.append(
            EvaluationModel(
                family,
                datasets[name].feature_set_id,
                datasets[name],
                _model_forecasts(
                    fold_results,
                    observation_dataset_id=verified.observations.dataset_id,
                    panel_dataset_id=verified.panel.dataset_id,
                    target_dataset_id=verified.targets.dataset_id,
                    fold_dataset_id=verified.folds.dataset_id,
                ),
                fold_results,
            )
        )
    configurations = (
        _configuration_record(
            family=ModelFamily.ZERO_RETURN,
            feature_set_id=None,
            forecast_dataset_id=None,
            reason="zero-return control",
            market_data_source_class=experiment.market_data_source_class,
        ),
        *(
            _configuration_record(
                family=ModelFamily.LOCAL_RIDGE,
                feature_set_id=dataset.feature_set_id,
                forecast_dataset_id=_model_forecasts(
                    tuple(
                        result
                        for result in local_result.fold_results
                        if result.fit.feature_set_id == dataset.feature_set_id
                    ),
                    observation_dataset_id=verified.observations.dataset_id,
                    panel_dataset_id=verified.panel.dataset_id,
                    target_dataset_id=verified.targets.dataset_id,
                    fold_dataset_id=verified.folds.dataset_id,
                ).dataset_id,
                reason=f"local {dataset.feature_set_name} Ridge",
                market_data_source_class=experiment.market_data_source_class,
            )
            for dataset in local_datasets
        ),
        *(
            _configuration_record(
                family=model.model_family,
                feature_set_id=model.feature_set_id,
                forecast_dataset_id=model.forecasts.dataset_id,
                reason=f"pooled {model.feature_set_id} Ridge",
                market_data_source_class=experiment.market_data_source_class,
            )
            for model in models
        ),
    )
    local_comparator, evaluation = build_r2_evaluation(
        verified,
        experiment,
        local_result,
        models,
        configurations,
        local_feature_set_id=datasets["L1"].feature_set_id,
        local_feature_datasets=local_datasets,
    )
    selection_preview = build_selection_manifest(
        evaluation,
        local_comparator,
        experiment,
        primary_metric=_OOF_SELECTION_PRIMARY_METRIC,
        secondary_metrics=_OOF_SELECTION_SECONDARY_METRICS,
        final_fitting_procedure=_OOF_SELECTION_FINAL_FITTING_PROCEDURE,
        application_image_identity=identities["application_identity"],
        frozen_at=clock.now(),
        frozen_by="oof-build-replay",
    )

    children: dict[str, dict[str, object]] = {}
    feature_refs: list[ArtifactReference] = []
    for name in sorted(datasets):
        payload = _dataset_payload(datasets[name], manifests[name])
        path = f"features/{name}.json"
        children[path] = payload
        feature_refs.append(_child_reference(path, payload))

    preprocessing_refs: list[ArtifactReference] = []
    for index, selection in enumerate((*selections_local, *selections_pooled)):
        payload = cast(dict[str, object], selection.as_json())
        path = f"preprocessing/{index:04d}.json"
        children[path] = payload
        preprocessing_refs.append(_child_reference(path, payload))

    fit_refs: list[ArtifactReference] = []
    coverage_refs: list[ArtifactReference] = []
    forecast_manifest_refs: list[ArtifactReference] = []
    forecast_manifest_identities: set[tuple[str, str]] = set()
    evaluation_refs: list[ArtifactReference] = []
    forecast_child_refs: list[ArtifactReference] = []
    forecast_child_by_identity: dict[tuple[str, str], ArtifactReference] = {}
    all_results = (*local_result.fold_results, *pooled_result.fold_results)
    for index, result in enumerate(all_results):
        fit_payload = cast(dict[str, object], result.fit.as_json())
        fit_path = f"fits/{index:04d}.json"
        children[fit_path] = fit_payload
        fit_refs.append(_child_reference(fit_path, fit_payload))
        coverage_payload = cast(
            dict[str, object],
            {
                **result.coverage.as_json(),
                "source_class": experiment.market_data_source_class.value,
                "evidence_class": experiment.evidence_class.value,
            },
        )
        coverage_path = f"coverage/{index:04d}.json"
        children[coverage_path] = coverage_payload
        coverage_refs.append(_child_reference(coverage_path, coverage_payload))
        forecast_payload = _forecast_payload(
            result.forecasts,
            source_class=experiment.market_data_source_class,
            evidence_class=experiment.evidence_class,
        )
        forecast_child_path = f"forecasts/{index:04d}.data.json"
        forecast_child_ref = _child_reference(forecast_child_path, forecast_payload)
        forecast_identity = (
            forecast_child_ref.contract,
            forecast_child_ref.semantic_id,
        )
        existing_forecast_ref = forecast_child_by_identity.get(forecast_identity)
        if existing_forecast_ref is None:
            children[forecast_child_path] = forecast_payload
            forecast_child_refs.append(forecast_child_ref)
            forecast_child_by_identity[forecast_identity] = forecast_child_ref
        else:
            forecast_child_ref = existing_forecast_ref
        forecast_manifest = R2ForecastManifest.create(
            forecast_dataset_id=result.forecasts.dataset_id,
            experiment_configuration_id=experiment.configuration_id,
            source_class=experiment.market_data_source_class,
            evidence_class=experiment.evidence_class,
            forecast_child=forecast_child_ref,
        )
        forecast_manifest_payload = cast(dict[str, object], forecast_manifest.as_json())
        forecast_manifest_path = f"forecasts/{index:04d}.manifest.json"
        forecast_manifest_ref = _child_reference(
            forecast_manifest_path,
            forecast_manifest_payload,
        )
        forecast_manifest_identity = (
            forecast_manifest_ref.contract,
            forecast_manifest_ref.semantic_id,
        )
        if forecast_manifest_identity not in forecast_manifest_identities:
            children[forecast_manifest_path] = forecast_manifest_payload
            forecast_manifest_refs.append(forecast_manifest_ref)
            forecast_manifest_identities.add(forecast_manifest_identity)

    for summary, name in (
        (local_result.coefficient_stability, "local"),
        (pooled_result.coefficient_stability, "pooled"),
    ):
        payload = cast(
            dict[str, object],
            {
                **summary.as_json(),
                "source_class": experiment.market_data_source_class.value,
                "evidence_class": experiment.evidence_class.value,
            },
        )
        path = f"evaluation/{name}-stability.json"
        reference = _child_reference(path, payload)
        if not any(
            item.contract == reference.contract and item.semantic_id == reference.semantic_id
            for item in evaluation_refs
        ):
            children[path] = payload
            evaluation_refs.append(reference)
    local_comparator_payload: dict[str, object] = {
        **local_comparator.as_json(),
        "source_class": experiment.market_data_source_class.value,
        "evidence_class": experiment.evidence_class.value,
    }
    local_comparator_path = "evaluation/local-comparator.json"
    children[local_comparator_path] = local_comparator_payload
    local_comparator_ref = _child_reference(local_comparator_path, local_comparator_payload)
    evaluation_refs.append(local_comparator_ref)

    evaluated_model_refs: list[ArtifactReference] = []
    for model in evaluation.evaluated_models:
        model_payload: dict[str, object] = {
            **model.as_json(),
            "source_class": experiment.market_data_source_class.value,
            "evidence_class": experiment.evidence_class.value,
        }
        model_path = f"evaluation/models/{model.manifest_id}.json"
        children[model_path] = model_payload
        model_ref = _child_reference(model_path, model_payload)
        evaluated_model_refs.append(model_ref)
        evaluation_refs.append(model_ref)

    evaluation_payload: dict[str, object] = {
        **evaluation.as_json(),
        "source_class": experiment.market_data_source_class.value,
        "evidence_class": experiment.evidence_class.value,
    }
    evaluation_path = "evaluation/report.json"
    children[evaluation_path] = evaluation_payload
    evaluation_ref = _child_reference(evaluation_path, evaluation_payload)
    evaluation_refs.append(evaluation_ref)

    ablation = pooled_result.ablation
    ablation_payload: dict[str, object] = {
        "contract": "qtrad-r2-pooled-ablation-v1",
        "schema_version": 1,
        "source_class": experiment.market_data_source_class.value,
        "evidence_class": experiment.evidence_class.value,
        "local_fold_fit_ids": list(ablation.local_fold_fit_ids),
        "pooled_local_fold_fit_ids": list(ablation.pooled_local_fold_fit_ids),
        "pooled_context_fold_fit_ids": list(ablation.pooled_context_fold_fit_ids),
        "local_target_ids": list(ablation.local_target_ids),
        "pooled_local_target_ids": list(ablation.pooled_local_target_ids),
        "pooled_context_target_ids": list(ablation.pooled_context_target_ids),
        "common_target_ids": list(ablation.common_target_ids),
        "holdout_excluded": True,
    }
    ablation_payload["ablation_id"] = sha256(
        canonical_bytes(
            {key: value for key, value in ablation_payload.items() if key != "ablation_id"}
        )
    ).hexdigest()
    ablation_path = "evaluation/pooled-ablation.json"
    children[ablation_path] = ablation_payload
    ablation_ref = _child_reference(ablation_path, ablation_payload)
    evaluation_refs.append(ablation_ref)
    register: dict[str, object] = {
        "contract": R2_EVALUATION_REGISTER_CONTRACT,
        "schema_version": 2,
        "source_class": experiment.market_data_source_class.value,
        "evidence_class": experiment.evidence_class.value,
        "local_comparator": local_comparator_ref.as_json(),
        "evaluated_models": [item.as_json() for item in evaluated_model_refs],
        "evaluation": evaluation_ref.as_json(),
        "forecast_manifests": [item.as_json() for item in forecast_manifest_refs],
        "coverage": [item.as_json() for item in coverage_refs],
        "pooled_ablation": ablation_ref.as_json(),
        "configurations": [item.as_json() for item in configurations],
        "selection_evaluation_report_id": evaluation.report_id,
        "selection_primary_metric": selection_preview.primary_metric,
        "selection_secondary_metrics": list(selection_preview.secondary_metrics),
        "selection_acceptance_thresholds": [
            [key, value] for key, value in selection_preview.acceptance_thresholds
        ],
        "selection_predeclared_comparators": [
            family.value for family in selection_preview.predeclared_comparators
        ],
        "selection_final_fitting_procedure": selection_preview.final_fitting_procedure,
        "selection_decisions": [item.as_json() for item in selection_preview.decisions],
        "selection_selected_configuration_ids": list(selection_preview.selected_configuration_ids),
        "selection_holdout_comparator_configuration_ids": list(
            selection_preview.holdout_comparator_configuration_ids
        ),
        "holdout_excluded": True,
    }
    register["report_id"] = sha256(canonical_bytes(register)).hexdigest()
    register_path = "evaluation/register.json"
    children[register_path] = register
    evaluation_refs.append(_child_reference(register_path, register))
    evaluation_refs.extend(forecast_child_refs)
    descriptor = _descriptor_payload(
        foundation_bundle_id=verified.bundle.foundation_id,
        experiment=experiment,
        feature_names=tuple(sorted(datasets)),
        run_kind=run_kind,
        identities=identities,
        representative_profile=representative_profile,
        foundation_authority=foundation_authority,
    )
    descriptor.update(
        {
            "fit_count": len(fit_refs),
            "forecast_manifest_count": len(forecast_manifest_refs),
            "coverage_count": len(coverage_refs),
            "evaluation_report_id": evaluation.report_id,
        }
    )
    source_binding_payload: dict[str, JsonValue] | None = None
    if foundation_authority is not None:
        if experiment_path is None:
            raise ValueError("canonical OOF build requires an experiment runtime locator")
        runtime_inputs = foundation_authority.runtime_json(
            feature_manifest_paths=feature_manifest_paths,
            experiment_path=experiment_path,
            research_root=research_root,
        )
        if holdout_target_source is not None:
            if holdout_target_source_authority is None:
                raise ValueError(
                    "canonical OOF build requires an authenticated target source authority"
                )
            closure_id = holdout_target_source_authority.closure_id
            source_binding_payload = _holdout_source_binding_payload(
                holdout_target_source_authority.source_id, closure_id
            )
            runtime_inputs["holdout_target_source"] = str(
                holdout_target_source_authority.manifest_path
            )
        descriptor["runtime_inputs"] = runtime_inputs
    descriptor["descriptor_id"] = sha256(
        canonical_bytes(
            {
                key: value
                for key, value in descriptor.items()
                if key not in {"descriptor_id", "runtime_inputs"}
                and key not in _OOF_DESCRIPTOR_PROVENANCE_FIELDS
            }
        )
    ).hexdigest()
    descriptor_path = "evaluation/run-descriptor.json"
    children[descriptor_path] = cast(dict[str, object], descriptor)
    evaluation_refs.append(
        _descriptor_reference(output=output, relative_path=descriptor_path, payload=descriptor)
    )
    holdout_source_ref: ArtifactReference | None = None
    if source_binding_payload is not None:
        source_path = "holdout/target-source-binding.json"
        children[source_path] = cast(dict[str, object], source_binding_payload)
        holdout_source_ref = _child_reference(source_path, source_binding_payload)
    bundle = R2OofBundle.create(
        foundation_bundle_id=verified.bundle.foundation_id,
        experiment_configuration_id=experiment.configuration_id,
        source_class=experiment.market_data_source_class,
        evidence_class=experiment.evidence_class,
        feature_children=tuple(feature_refs),
        preprocessing_children=tuple(preprocessing_refs),
        fit_children=tuple(fit_refs),
        forecast_manifests=tuple(forecast_manifest_refs),
        coverage_children=tuple(coverage_refs),
        evaluation_children=tuple(evaluation_refs),
        holdout_target_source=holdout_source_ref,
    )
    return write_r2_oof_bundle(output, bundle, children)


def _load_selection(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("selection manifest must be a regular non-symlink file")
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError("selection manifest must be a JSON object")
    return value


def _oof_child_payload(bundle_path: Path, bundle: R2OofBundle, contract: str) -> dict[str, object]:
    matches: list[dict[str, object]] = []
    references = [*bundle.evaluation_children, *bundle.forecast_manifests]
    if bundle.holdout_target_source is not None:
        references.append(bundle.holdout_target_source)
    for reference in references:
        if reference.contract != contract:
            continue
        payload = _load_selection(bundle_path.parent / reference.path)
        if contract == R2_EVALUATION_CONTRACT and "report_id" not in payload:
            continue
        authenticated = reference_for_json(
            path=reference.path,
            contract=contract,
            semantic_id=_payload_identity(payload),
            content=payload,
        )
        if (
            authenticated.semantic_id != reference.semantic_id
            or authenticated.sha256 != reference.sha256
        ):
            raise ValueError(
                "OOF child bytes or semantic identity differ from its bundle reference"
            )
        matches.append(payload)
    if len(matches) != 1:
        raise ValueError(f"OOF bundle must contain exactly one required {contract} child")
    return matches[0]


def _oof_holdout_source_authority(
    bundle_path: Path,
    bundle: R2OofBundle,
    descriptor: Mapping[str, object],
    *,
    authenticated_authority: R2HoldoutTargetSourceAuthority | None = None,
) -> R2HoldoutTargetSourceAuthority:
    reference = bundle.holdout_target_source
    if reference is None or reference.contract != R2_HOLDOUT_SOURCE_BINDING_CONTRACT:
        raise ValueError("OOF bundle does not bind a bounded holdout target source")
    binding = _oof_child_payload(bundle_path, bundle, R2_HOLDOUT_SOURCE_BINDING_CONTRACT)
    required = {
        "contract",
        "schema_version",
        "source_id",
        "source_closure_id",
        "binding_id",
    }
    if set(binding) != required or binding.get("schema_version") != 1:
        raise ValueError("OOF holdout source binding fields are incomplete")
    source_id = binding["source_id"]
    closure_id = binding["source_closure_id"]
    binding_id = binding["binding_id"]
    if (
        not isinstance(source_id, str)
        or not isinstance(closure_id, str)
        or not isinstance(binding_id, str)
    ):
        raise ValueError("OOF holdout source binding IDs are malformed")
    expected_binding = _holdout_source_binding_payload(source_id, closure_id)
    if expected_binding != binding or binding_id != reference.semantic_id:
        raise ValueError("OOF holdout source binding identity differs from its reference")
    raw_runtime = descriptor.get("runtime_inputs")
    if not isinstance(raw_runtime, dict):
        raise ValueError("OOF descriptor has no runtime source locator")
    raw_source_path = raw_runtime.get("holdout_target_source")
    if not isinstance(raw_source_path, str):
        raise ValueError("OOF descriptor has no persisted source locator")
    source_path = Path(raw_source_path)
    if not source_path.is_absolute() or source_path.is_symlink() or not source_path.is_file():
        raise ValueError("OOF persisted source locator is unavailable")
    if authenticated_authority is None:
        authority = load_r2_holdout_target_source_authority(source_path)
    else:
        authority = authenticated_authority
        if authority.manifest_path != source_path.absolute():
            raise ValueError("OOF source authority locator differs from its descriptor")
    if authority.source_id != source_id or authority.closure_id != closure_id:
        raise ValueError("OOF persisted source closure differs from its binding")
    return authority


def _oof_holdout_target_source(
    bundle_path: Path,
    bundle: R2OofBundle,
    descriptor: Mapping[str, object],
    *,
    authenticated_authority: R2HoldoutTargetSourceAuthority | None = None,
) -> R2HoldoutTargetSource:
    reference = bundle.holdout_target_source
    if reference is None:
        raise ValueError("OOF bundle has no authenticated holdout target source")
    if reference.contract == R2HoldoutTargetSource.CONTRACT:
        payload = _load_selection(bundle_path.parent / reference.path)
        source = R2HoldoutTargetSource.from_json(payload)
        if source.source_id != reference.semantic_id:
            raise ValueError("OOF holdout target source identity differs from its reference")
        return source
    return _oof_holdout_source_authority(
        bundle_path,
        bundle,
        descriptor,
        authenticated_authority=authenticated_authority,
    ).source


def _configuration_record_from_payload(value: object) -> ConfigurationRecord:
    if not isinstance(value, dict):
        raise ValueError("OOF configuration record must be an object")
    raw = cast(dict[str, object], value)
    return ConfigurationRecord(
        configuration_id=str(raw["configuration_id"]),
        model_family=ModelFamily(str(raw["model_family"])),
        feature_set_id=(None if raw["feature_set_id"] is None else str(raw["feature_set_id"])),
        disposition=ConfigurationDisposition(str(raw["disposition"])),
        reason=str(raw["reason"]),
        forecast_dataset_id=(
            None if raw["forecast_dataset_id"] is None else str(raw["forecast_dataset_id"])
        ),
        evaluated_model_manifest_id=(
            None
            if raw["evaluated_model_manifest_id"] is None
            else str(raw["evaluated_model_manifest_id"])
        ),
        market_data_source_class=MarketDataSourceClass(str(raw["market_data_source_class"])),
    )


def holdout_configuration_registry(
    oof_bundle_path: Path,
    bundle: R2OofBundle,
    *,
    expected_evaluation_report_id: str | None = None,
    expected_selected_configuration_ids: Sequence[str] | None = None,
    expected_holdout_configuration_ids: Sequence[str] | None = None,
    evaluation_payload: Mapping[str, object] | None = None,
    register_payload: Mapping[str, object] | None = None,
) -> tuple[tuple[str, ModelFamily, str | None, str | None, str | None], ...]:
    """Return the authenticated OOF configuration registry for holdout freezing."""
    if expected_evaluation_report_id is not None:
        evaluation_reference_ids = {
            reference.semantic_id
            for reference in bundle.evaluation_children
            if reference.contract == R2_EVALUATION_CONTRACT
        }
        if expected_evaluation_report_id not in evaluation_reference_ids:
            raise ValueError("prior selection report is not an authenticated OOF evaluation child")
    evaluation = (
        evaluation_payload
        if evaluation_payload is not None
        else _oof_child_payload(oof_bundle_path, bundle, R2_EVALUATION_CONTRACT)
    )
    if (
        expected_evaluation_report_id is not None
        and evaluation.get("report_id") != expected_evaluation_report_id
    ):
        raise ValueError("OOF evaluation child differs from prior selection report")
    if expected_selected_configuration_ids is not None or (
        expected_holdout_configuration_ids is not None
    ):
        register = (
            register_payload
            if register_payload is not None
            else _oof_child_payload(
                oof_bundle_path,
                bundle,
                R2_EVALUATION_REGISTER_CONTRACT,
            )
        )
        if (
            expected_evaluation_report_id is not None
            and register.get("selection_evaluation_report_id") != expected_evaluation_report_id
        ):
            raise ValueError("OOF register differs from prior selection report")
        decisions = _selection_decisions_from_payload(register.get("selection_decisions"))
        selected_ids = tuple(
            item.configuration_id
            for item in decisions
            if item.disposition is ConfigurationDisposition.SELECTED_CANDIDATE
        )
        holdout_ids = tuple(
            item.configuration_id
            for item in decisions
            if item.disposition
            in (
                ConfigurationDisposition.RETAINED_CONTROL,
                ConfigurationDisposition.SELECTED_CANDIDATE,
            )
        )
        stored_selected = register.get("selection_selected_configuration_ids")
        stored_holdout = register.get("selection_holdout_comparator_configuration_ids")
        if (
            not isinstance(stored_selected, list)
            or not isinstance(stored_holdout, list)
            or not all(isinstance(item, str) for item in (*stored_selected, *stored_holdout))
            or tuple(sorted(cast(list[str], stored_selected))) != selected_ids
            or tuple(sorted(cast(list[str], stored_holdout))) != holdout_ids
        ):
            raise ValueError("OOF register selection arrays do not replay its persisted decisions")
        if (
            expected_selected_configuration_ids is not None
            and tuple(sorted(expected_selected_configuration_ids)) != selected_ids
        ):
            raise ValueError("OOF selection decisions differ from prior selection")
        if (
            expected_holdout_configuration_ids is not None
            and tuple(sorted(expected_holdout_configuration_ids)) != holdout_ids
        ):
            raise ValueError("OOF holdout decisions differ from prior selection")
    evaluated_models = evaluation.get("evaluated_models")
    if not isinstance(evaluated_models, list) or not evaluated_models:
        raise ValueError("OOF evaluation has no authenticated evaluated-model registry")
    manifests: dict[str, tuple[ModelFamily, str | None, str | None]] = {}
    for raw_model in evaluated_models:
        if not isinstance(raw_model, dict):
            raise ValueError("OOF evaluated-model manifest must be an object")
        manifest_id = raw_model.get("manifest_id")
        if not isinstance(manifest_id, str):
            raise ValueError("OOF evaluated-model manifest has no authenticated ID")
        if manifest_id in manifests:
            raise ValueError("OOF evaluated-model manifest IDs are not unique")
        feature_set_id = raw_model.get("feature_set_id")
        feature_dataset_id = raw_model.get("feature_dataset_id")
        if feature_set_id is not None and not isinstance(feature_set_id, str):
            raise ValueError("OOF evaluated-model feature-set ID is invalid")
        if feature_dataset_id is not None and not isinstance(feature_dataset_id, str):
            raise ValueError("OOF evaluated-model feature-dataset ID is invalid")
        manifests[manifest_id] = (
            ModelFamily(str(raw_model.get("model_family"))),
            feature_set_id,
            feature_dataset_id,
        )
    configurations = evaluation.get("configurations")
    if not isinstance(configurations, list) or not configurations:
        raise ValueError("OOF evaluation has no authenticated configuration registry")
    records = tuple(_configuration_record_from_payload(item) for item in configurations)
    ordered = tuple(sorted(records, key=lambda item: item.configuration_id))
    if ordered != records or len({item.configuration_id for item in records}) != len(records):
        raise ValueError("OOF evaluation configuration registry is not ordered and unique")
    registry: list[tuple[str, ModelFamily, str | None, str | None, str | None]] = []
    for item in records:
        manifest_id = item.evaluated_model_manifest_id
        if manifest_id is None or manifest_id not in manifests:
            raise ValueError(
                "OOF configuration does not reference an authenticated evaluated model"
            )
        manifest_family, manifest_feature_set_id, feature_dataset_id = manifests[manifest_id]
        if (
            item.model_family is not manifest_family
            or item.feature_set_id != manifest_feature_set_id
        ):
            raise ValueError("OOF configuration differs from its authenticated evaluated model")
        registry.append(
            (
                item.configuration_id,
                item.model_family,
                item.feature_set_id,
                feature_dataset_id,
                manifest_id,
            )
        )
    return tuple(registry)


def _descriptor_experiment_path(descriptor: Mapping[str, object]) -> Path:
    runtime = descriptor.get("runtime_inputs")
    if not isinstance(runtime, dict):
        raise ValueError("OOF descriptor has no immediate-parent runtime locators")
    raw_path = runtime.get("experiment")
    if not isinstance(raw_path, str):
        raise ValueError("OOF descriptor has no experiment runtime locator")
    path = Path(raw_path)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError("OOF experiment runtime locator is unavailable")
    return path


def _authenticated_selection_policy(
    register: Mapping[str, object], experiment: R2ExperimentConfig
) -> dict[str, JsonValue]:
    primary_metric = register.get("selection_primary_metric")
    secondary_metrics = register.get("selection_secondary_metrics")
    acceptance_thresholds = register.get("selection_acceptance_thresholds")
    predeclared_comparators = register.get("selection_predeclared_comparators")
    final_fitting_procedure = register.get("selection_final_fitting_procedure")
    if (
        primary_metric != _OOF_SELECTION_PRIMARY_METRIC
        or secondary_metrics != list(_OOF_SELECTION_SECONDARY_METRICS)
        or final_fitting_procedure != _OOF_SELECTION_FINAL_FITTING_PROCEDURE
        or predeclared_comparators != [family.value for family in experiment.model_families]
    ):
        raise ValueError("confirmatory F2 register has an incompatible selection policy")
    if not isinstance(acceptance_thresholds, list):
        raise ValueError("confirmatory F2 register has no authenticated selection thresholds")
    parsed_thresholds: list[list[str | float]] = []
    for item in acceptance_thresholds:
        if (
            not isinstance(item, list)
            or len(item) != 2
            or not isinstance(item[0], str)
            or isinstance(item[1], bool)
            or not isinstance(item[1], (int, float))
        ):
            raise ValueError("confirmatory F2 selection thresholds are malformed")
        parsed_thresholds.append([item[0], float(item[1])])
    expected_thresholds = [
        [key, float(value)] for key, value in sorted(experiment.acceptance_thresholds.items())
    ]
    if parsed_thresholds != expected_thresholds:
        raise ValueError("confirmatory F2 selection thresholds differ from the experiment")
    return {
        "primary_metric": cast(str, primary_metric),
        "secondary_metrics": cast(list[JsonValue], secondary_metrics),
        "acceptance_thresholds": cast(list[JsonValue], acceptance_thresholds),
        "predeclared_comparators": cast(list[JsonValue], predeclared_comparators),
        "final_fitting_procedure": cast(str, final_fitting_procedure),
    }


def _qualified_inner_validation_selections(
    bundle_path: Path,
    bundle: R2OofBundle,
    experiment: R2ExperimentConfig,
    folds: FoldDataset,
    descriptor_values: Mapping[str, object],
) -> tuple[R2PreprocessingSelection, ...]:
    """Authenticate the complete R2.C inner-split register needed for F2 readiness."""

    expected_fold_ids = tuple(fold.fold_id for fold in folds.folds)
    expected_count = len(expected_fold_ids) * (2 * len(experiment.target_instruments) + 2)
    if len(bundle.preprocessing_children) != expected_count:
        raise ValueError("confirmatory F2 preprocessing register is incomplete")

    selections: list[R2PreprocessingSelection] = []
    for reference in bundle.preprocessing_children:
        child_path = bundle_path.parent / reference.path
        payload = _load_selection(child_path)
        semantic_id = _payload_identity(payload)
        authenticated = reference_for_json(
            path=reference.path,
            contract=R2_PREPROCESSING_SELECTION_CONTRACT,
            semantic_id=semantic_id,
            content=payload,
        )
        if reference != authenticated:
            raise ValueError("confirmatory F2 preprocessing child reference is unauthenticated")
        selection = decode_r2_preprocessing_selection(payload)
        if selection.artifact_id != reference.semantic_id:
            raise ValueError("confirmatory F2 preprocessing child identity differs")
        if (
            selection.experiment_configuration_id != experiment.configuration_id
            or selection.fold_dataset_id != experiment.fold_dataset_id
            or selection.target_dataset_id != experiment.target_dataset_id
            or selection.horizon != experiment.primary_horizon
            or selection.evidence_class is not EvidenceClass.CONFIRMATORY
            or selection.market_data_source_class is not experiment.market_data_source_class
            or selection.application_image_identity != descriptor_values["application_identity"]
            or selection.sklearn_library_identity != descriptor_values["sklearn_identity"]
            or selection.outer_fold_id not in expected_fold_ids
        ):
            raise ValueError("confirmatory F2 preprocessing child has incompatible lineage")
        if (
            len(selection.selection.inner_validation_target_ids)
            < experiment.minimum_inner_validation_rows
        ):
            raise ValueError(
                "confirmatory F2 preprocessing child has too few inner-validation rows"
            )
        selections.append(selection)

    if len({item.artifact_id for item in selections}) != len(selections):
        raise ValueError("confirmatory F2 preprocessing register contains duplicates")

    feature_sets = {item.name: item for item in experiment.feature_sets}
    if set(feature_sets) != set(_REQUIRED_FEATURE_SETS):
        raise ValueError("confirmatory F2 experiment feature register is incomplete")

    expected_feature_ids = {
        name: feature_set_id(
            experiment.configuration_id,
            name,
            feature_schema_for_set(experiment, name),
            experiment.market_data_source_class,
        )
        for name in _REQUIRED_FEATURE_SETS
    }
    local = tuple(item for item in selections if item.model_family is ModelFamily.LOCAL_RIDGE)
    local_feature_ids = tuple(sorted({item.feature_set_id for item in local}))
    expected_local_feature_ids = tuple(
        sorted((expected_feature_ids["L0"], expected_feature_ids["L1"]))
    )
    if local_feature_ids != expected_local_feature_ids:
        raise ValueError("confirmatory F2 local preprocessing register is incomplete")
    local_scopes = {
        (item.feature_set_id, item.target_instruments, item.outer_fold_id) for item in local
    }
    expected_local_scopes = {
        (feature_set_id, (instrument,), fold_id)
        for feature_set_id in local_feature_ids
        for instrument in experiment.target_instruments
        for fold_id in expected_fold_ids
    }
    if local_scopes != expected_local_scopes or len(local) != len(expected_local_scopes):
        raise ValueError("confirmatory F2 local inner-split coverage is incomplete")

    for family in (
        ModelFamily.POOLED_LOCAL_RIDGE,
        ModelFamily.POOLED_CROSS_ASSET_RIDGE,
    ):
        family_selections = tuple(item for item in selections if item.model_family is family)
        expected_feature_id = expected_feature_ids[
            "P0" if family is ModelFamily.POOLED_LOCAL_RIDGE else "P1"
        ]
        if {item.feature_set_id for item in family_selections} != {expected_feature_id}:
            raise ValueError(
                f"confirmatory F2 pooled feature scope is incomplete for {family.value}"
            )
        scopes = {(item.target_instruments, item.outer_fold_id) for item in family_selections}
        expected_scopes = {
            (tuple(experiment.target_instruments), fold_id) for fold_id in expected_fold_ids
        }
        if scopes != expected_scopes or len(family_selections) != len(expected_scopes):
            raise ValueError(
                f"confirmatory F2 pooled inner-split coverage is incomplete for {family.value}"
            )

    return tuple(selections)


def _complete_confirmatory_readiness(
    report: R2ReadinessReport,
) -> R2ReadinessReport:
    pending_inner_split = (
        "minimum_inner_validation_rows requires a verified R2.C chronological inner-split artefact"
    )
    return replace(
        report,
        inner_validation_rows_ready=ReadinessState.READY,
        confirmatory_oof_ready=ReadinessState.READY,
        unmet_conditions=tuple(
            condition for condition in report.unmet_conditions if condition != pending_inner_split
        ),
    )


def audit_confirmatory_f2(path: Path) -> VerifiedConfirmatoryF2:
    """Exceptional deep audit that replays a qualifying confirmatory F2 authority."""
    bundle, source_authority_value = _verify_r2_oof_bundle_with_source(path)
    source_authority = (
        source_authority_value
        if isinstance(source_authority_value, R2HoldoutTargetSourceAuthority)
        else None
    )
    if bundle.evidence_class is not EvidenceClass.CONFIRMATORY:
        raise ValueError("confirmatory F2 requires CONFIRMATORY evidence")
    descriptor = _oof_child_payload(path, bundle, OOF_DESCRIPTOR_CONTRACT)
    if descriptor.get("run_kind") != CONFIRMATORY_RUN_KIND:
        raise ValueError("confirmatory F2 requires the CONFIRMATORY OOF run kind")
    if descriptor.get("evidence_class") != EvidenceClass.CONFIRMATORY.value:
        raise ValueError("confirmatory F2 descriptor has the wrong evidence class")
    if descriptor.get("holdout_excluded") is not True:
        raise ValueError("confirmatory F2 must exclude the locked holdout")
    if bundle.holdout_target_source is None:
        raise ValueError("confirmatory F2 has no authenticated target source")
    if (
        bundle.holdout_target_source.contract == R2_HOLDOUT_SOURCE_BINDING_CONTRACT
        and source_authority is None
    ):
        source_authority = _oof_holdout_source_authority(path, bundle, descriptor)
    source = _oof_holdout_target_source(
        path,
        bundle,
        descriptor,
        authenticated_authority=source_authority,
    )
    expected_descriptor_values = {
        "foundation_bundle_id": bundle.foundation_bundle_id,
        "experiment_configuration_id": bundle.experiment_configuration_id,
        "source_class": bundle.source_class.value,
        "evidence_class": bundle.evidence_class.value,
        "target_dataset_id": source.source_target_dataset_id,
        "observation_dataset_id": source.observation_dataset_id,
        "foundation_configuration_id": source.foundation_configuration_id,
        "target_instruments": list(source.target_instruments),
        "primary_horizon_seconds": source.primary_horizon_seconds,
        "holdout_range": [item.isoformat() for item in source.holdout_range],
    }
    for key, expected in expected_descriptor_values.items():
        if descriptor.get(key) != expected:
            raise ValueError(f"confirmatory F2 descriptor has an unauthenticated {key}")

    experiment = load_r2_experiment(_descriptor_experiment_path(descriptor))
    if experiment.configuration_id != bundle.experiment_configuration_id:
        raise ValueError("confirmatory F2 experiment differs from the OOF envelope")
    if experiment.evidence_class is not EvidenceClass.CONFIRMATORY:
        raise ValueError("confirmatory F2 replay experiment is not confirmatory")
    if experiment.market_data_source_class is not bundle.source_class:
        raise ValueError("confirmatory F2 experiment source differs from the OOF envelope")
    if (
        experiment.target_dataset_id != source.source_target_dataset_id
        or experiment.observation_dataset_id != source.observation_dataset_id
        or experiment.foundation_configuration_id != source.foundation_configuration_id
        or tuple(experiment.target_instruments) != source.target_instruments
        or experiment.holdout_range != source.holdout_range
        or int(experiment.primary_horizon.total_seconds()) != source.primary_horizon_seconds
    ):
        raise ValueError("confirmatory F2 target source differs from the exact experiment")
    experiment_descriptor_values: dict[str, object] = {
        "r1_bundle_id": experiment.r1_bundle_id,
        "foundation_configuration_id": experiment.foundation_configuration_id,
        "panel_dataset_id": experiment.panel_dataset_id,
        "fold_dataset_id": experiment.fold_dataset_id,
        "ordered_instruments": list(experiment.ordered_instruments),
        "instrument_roles": {
            instrument: role.value for instrument, role in experiment.instrument_roles.items()
        },
        "horizons_seconds": [int(horizon.total_seconds()) for horizon in experiment.horizons],
        "feature_windows_seconds": [
            int(window.total_seconds()) for window in experiment.feature_windows
        ],
        "acceptance_thresholds": dict(experiment.acceptance_thresholds),
        "alpha_grid": list(experiment.alpha_grid),
        "inner_validation_policy": experiment.inner_validation_policy,
        "preprocessing_policy": experiment.preprocessing_policy,
        "pooled_weighting_policy": experiment.pooled_weighting_policy,
        "ridge_solver": experiment.ridge_solver,
        "ridge_tolerance": experiment.ridge_tolerance,
        "ridge_max_iterations": experiment.ridge_max_iterations,
        "minimum_training_rows": experiment.minimum_training_rows,
        "minimum_inner_validation_rows": experiment.minimum_inner_validation_rows,
        "minimum_outer_validation_rows": experiment.minimum_outer_validation_rows,
        "model_selection_policy": experiment.model_selection_policy,
        "metric_policy": experiment.metric_policy,
        "forecast_bucket_policy": experiment.forecast_bucket_policy,
        "state_bucket_policy": experiment.state_bucket_policy,
    }
    for key, expected in experiment_descriptor_values.items():
        if descriptor.get(key) != expected:
            raise ValueError(f"confirmatory F2 descriptor differs for {key}")

    (
        replayed_folds,
        replayed_source_active_intervals,
        outcome_blind_foundation,
        g2_feature_source_authority,
    ) = asyncio.run(
        _replay_authority_oof_async(
            path,
            expected_run_kind=CONFIRMATORY_RUN_KIND,
            authenticated_bundle=bundle,
            authenticated_source_authority=source_authority,
        )
    )
    if g2_feature_source_authority is None:
        raise ValueError("confirmatory F2 foundation has no authenticated G2 feature source")
    readiness_report = evaluate_outcome_blind_confirmatory_readiness(
        experiment=experiment,
        target_source=source,
        folds=replayed_folds,
        source_active=replayed_source_active_intervals,
        r1_bundle_id=experiment.r1_bundle_id,
    )
    if readiness_report.confirmatory_data_ready.value != "READY":
        raise ValueError(
            "confirmatory F2 requires independently replayed outcome-blind readiness: "
            + "; ".join(readiness_report.unmet_conditions)
        )
    _qualified_inner_validation_selections(
        path,
        bundle,
        experiment,
        replayed_folds,
        descriptor,
    )
    readiness_report = _complete_confirmatory_readiness(readiness_report)
    if (
        readiness_report.inner_validation_rows_ready is not ReadinessState.READY
        or readiness_report.confirmatory_oof_ready is not ReadinessState.READY
    ):
        raise ValueError("confirmatory F2 has incomplete R2.C/F2 readiness")

    register = _oof_child_payload(path, bundle, R2_EVALUATION_REGISTER_CONTRACT)
    raw_configurations = register.get("configurations")
    if not isinstance(raw_configurations, list) or not raw_configurations:
        raise ValueError("confirmatory F2 register has no complete configuration set")
    configurations = tuple(_configuration_record_from_payload(item) for item in raw_configurations)
    configuration_ids = tuple(item.configuration_id for item in configurations)
    if len(set(configuration_ids)) != len(configuration_ids):
        raise ValueError("confirmatory F2 configuration register contains duplicates")
    if {item.model_family for item in configurations} != set(ModelFamily):
        raise ValueError("confirmatory F2 configuration register is incomplete")

    decisions = _selection_decisions_from_payload(register.get("selection_decisions"))
    decision_ids = tuple(item.configuration_id for item in decisions)
    if len(set(decision_ids)) != len(decision_ids) or set(decision_ids) != set(configuration_ids):
        raise ValueError("confirmatory F2 selection decisions do not cover the register")
    selected_ids = tuple(
        item.configuration_id
        for item in decisions
        if item.disposition is ConfigurationDisposition.SELECTED_CANDIDATE
    )
    holdout_ids = tuple(
        item.configuration_id
        for item in decisions
        if item.disposition
        in (ConfigurationDisposition.SELECTED_CANDIDATE, ConfigurationDisposition.RETAINED_CONTROL)
    )
    stored_selected = register.get("selection_selected_configuration_ids")
    stored_holdout = register.get("selection_holdout_comparator_configuration_ids")
    if (
        not isinstance(stored_selected, list)
        or not isinstance(stored_holdout, list)
        or not all(isinstance(item, str) for item in (*stored_selected, *stored_holdout))
        or tuple(sorted(stored_selected)) != selected_ids
        or tuple(sorted(stored_holdout)) != holdout_ids
    ):
        raise ValueError("confirmatory F2 register selection is not independently replayable")
    selection_policy = _authenticated_selection_policy(register, experiment)
    report_id = register.get("selection_evaluation_report_id")
    if not isinstance(report_id, str) or register.get("report_id") is None:
        raise ValueError("confirmatory F2 register has no authenticated evaluation report")
    local_ref = register.get("local_comparator")
    if not isinstance(local_ref, dict) or not isinstance(local_ref.get("semantic_id"), str):
        raise ValueError("confirmatory F2 register has no authenticated local comparator")
    local_ref_payload = cast(dict[str, object], local_ref)
    local_comparator_manifest_id = local_ref_payload.get("semantic_id")
    if not isinstance(local_comparator_manifest_id, str):
        raise ValueError("confirmatory F2 register has no authenticated local comparator")
    evaluation_policy = holdout_evaluation_policy(
        path,
        bundle,
        expected_evaluation_report_id=report_id,
    )
    registry = holdout_configuration_registry(
        path,
        bundle,
        expected_evaluation_report_id=report_id,
        expected_selected_configuration_ids=selected_ids,
        expected_holdout_configuration_ids=holdout_ids,
    )
    confirmatory_holdout_authority = VerifiedConfirmatoryHoldoutAuthority._create(
        _VERIFIED_CONFIRMATORY_HOLDOUT_AUTHORITY_TOKEN,
        oof_id=bundle.oof_id,
        evaluation_report_id=report_id,
        configuration_registry=registry,
        evaluation_policy=evaluation_policy,
        experiment_configuration_id=bundle.experiment_configuration_id,
        evidence_class=bundle.evidence_class,
        local_comparator_manifest_id=local_comparator_manifest_id,
        evaluated_configuration_ids=tuple(item.configuration_id for item in configurations),
        selection_decisions=decisions,
        selected_configuration_ids=selected_ids,
        holdout_comparator_configuration_ids=holdout_ids,
        selection_policy=selection_policy,
        holdout_range=experiment.holdout_range,
        source_class=bundle.source_class,
        foundation_bundle_id=bundle.foundation_bundle_id,
    )
    return VerifiedConfirmatoryF2._create(
        _VERIFIED_CONFIRMATORY_F2_TOKEN,
        bundle=bundle,
        holdout_target_source=source,
        g2_feature_source_authority=g2_feature_source_authority,
        descriptor=cast(Mapping[str, JsonValue], descriptor),
        evaluation_report_id=report_id,
        experiment=experiment,
        local_comparator_manifest_id=local_comparator_manifest_id,
        outcome_blind_foundation=outcome_blind_foundation,
        evaluated_configurations=configurations,
        selection_decisions=decisions,
        selected_configuration_ids=selected_ids,
        holdout_comparator_configuration_ids=holdout_ids,
        configuration_registry=registry,
        evaluation_policy=cast(Mapping[str, JsonValue], evaluation_policy),
        confirmatory_holdout_authority=confirmatory_holdout_authority,
        readiness_report=readiness_report,
        runtime_identities={
            field: cast(str, descriptor[field]) for field in _OOF_DESCRIPTOR_PROVENANCE_FIELDS
        },
        selection_policy=selection_policy,
    )


def _canonical_path(
    path: Path,
    field: str,
    *,
    directory: bool = False,
    allow_missing: bool = False,
    require_absolute: bool = False,
) -> Path:
    """Return a rooted canonical path after validating every path component."""
    if require_absolute and not path.is_absolute():
        raise ValueError(f"{field} must be an absolute path")
    if any(part in {".", ".."} for part in path.parts):
        raise ValueError(f"{field} path contains traversal")
    candidate = path if path.is_absolute() else Path.cwd() / path
    if not candidate.is_absolute() or not candidate.parts:
        raise ValueError(f"{field} path is unsafe")
    parts = candidate.parts[1:]
    current = Path(candidate.anchor)
    for index, part in enumerate(parts):
        current /= part
        if current.is_symlink():
            raise ValueError(f"{field} path must not traverse a symlink")
        if index < len(parts) - 1 and (not current.exists() or not current.is_dir()):
            raise ValueError(f"{field} path has a non-directory ancestor")
    resolved = candidate.resolve(strict=False)
    root = Path(candidate.anchor).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"{field} path escapes its root")
    if not candidate.exists():
        if candidate.is_symlink():
            raise ValueError(f"{field} path must not be a symlink")
        if not allow_missing:
            raise FileNotFoundError(f"{field} path is unavailable: {candidate}")
        if not candidate.parent.is_dir() or candidate.parent.is_symlink():
            raise FileNotFoundError(f"{field} parent directory is unavailable: {candidate.parent}")
        return resolved
    if directory:
        if not candidate.is_dir():
            raise ValueError(f"{field} must be a regular directory")
    elif not candidate.is_file():
        raise ValueError(f"{field} must be a regular file")
    return resolved


def _preflight_promotion_output(output: Path, *, forbidden_roots: Sequence[Path] = ()) -> Path:
    candidate = _canonical_path(
        output,
        "F2 promotion output",
        allow_missing=True,
    )
    if candidate.exists() or candidate.is_symlink():
        raise FileExistsError(candidate)
    for root in forbidden_roots:
        canonical_root = _canonical_path(
            root,
            "F2 promotion forbidden boundary",
            directory=True,
            require_absolute=True,
        )
        if candidate == canonical_root or candidate.is_relative_to(canonical_root):
            raise ValueError(
                "F2 promotion output must be outside authenticated evidence boundaries"
            )
    return candidate


def _promotion_runtime_path(
    locators: Mapping[str, str], name: str, *, directory: bool = False
) -> Path:
    raw = locators.get(name)
    if not isinstance(raw, str):
        raise ValueError(f"F2 promotion runtime locator is missing: {name}")
    return _canonical_path(
        Path(raw),
        f"F2 promotion runtime {name}",
        directory=directory,
        require_absolute=True,
    )


def _promotion_parent_boundaries(descriptor: Mapping[str, object]) -> tuple[Path, ...]:
    runtime_raw = descriptor.get("runtime_inputs")
    if not isinstance(runtime_raw, dict):
        raise ValueError("F2 descriptor has no immediate-parent runtime locators")
    runtime = cast(dict[str, object], runtime_raw)
    expected_keys = {
        "foundation",
        "foundation_receipt",
        "foundation_promotion",
        "experiment",
        "research_root",
        "feature_manifests",
    }
    if "holdout_target_source" in runtime:
        expected_keys.add("holdout_target_source")
    if set(runtime) != expected_keys:
        raise ValueError("F2 runtime locators are incomplete or contain unknown fields")
    locators: dict[str, str] = {}
    for key in ("foundation", "foundation_receipt", "experiment", "research_root"):
        value = runtime[key]
        if not isinstance(value, str):
            raise ValueError(f"F2 runtime locator is malformed: {key}")
        locators[key] = value
    boundaries = [
        _promotion_runtime_path(locators, key).parent
        for key in ("foundation", "foundation_receipt")
    ]
    boundaries.append(_promotion_runtime_path(locators, "research_root", directory=True))
    raw_source = runtime.get("holdout_target_source")
    if raw_source is not None:
        if not isinstance(raw_source, str):
            raise ValueError("F2 holdout source locator is malformed")
        locators["holdout_target_source"] = raw_source
        source_path = _promotion_runtime_path(locators, "holdout_target_source")
        boundaries.append(source_path.parent / f"{source_path.name}.parts")
    raw_promotion = runtime["foundation_promotion"]
    if raw_promotion is not None:
        if not isinstance(raw_promotion, str):
            raise ValueError("F2 foundation promotion locator is malformed")
        locators["foundation_promotion"] = raw_promotion
        boundaries.append(_promotion_runtime_path(locators, "foundation_promotion").parent)
    return tuple(dict.fromkeys(boundaries))


def _confirmatory_descriptor_inputs(
    bundle_path: Path,
    bundle: R2OofBundle,
    descriptor: Mapping[str, object],
) -> tuple[R2HoldoutTargetSource, R2ExperimentConfig]:
    if (
        bundle.evidence_class is not EvidenceClass.CONFIRMATORY
        or descriptor.get("run_kind") != CONFIRMATORY_RUN_KIND
        or descriptor.get("evidence_class") != EvidenceClass.CONFIRMATORY.value
        or descriptor.get("holdout_excluded") is not True
    ):
        raise ValueError("F2 promotion requires a confirmatory outcome-blind OOF descriptor")
    if bundle.holdout_target_source is None:
        raise ValueError("F2 promotion requires an authenticated target source")
    source = _oof_holdout_target_source(bundle_path, bundle, descriptor)
    expected_values = {
        "foundation_bundle_id": bundle.foundation_bundle_id,
        "experiment_configuration_id": bundle.experiment_configuration_id,
        "source_class": bundle.source_class.value,
        "evidence_class": bundle.evidence_class.value,
        "target_dataset_id": source.source_target_dataset_id,
        "observation_dataset_id": source.observation_dataset_id,
        "foundation_configuration_id": source.foundation_configuration_id,
        "target_instruments": list(source.target_instruments),
        "primary_horizon_seconds": source.primary_horizon_seconds,
        "holdout_range": [item.isoformat() for item in source.holdout_range],
    }
    for key, expected in expected_values.items():
        if descriptor.get(key) != expected:
            raise ValueError(f"F2 descriptor has an unauthenticated {key}")
    experiment = load_r2_experiment(_descriptor_experiment_path(descriptor))
    if (
        experiment.configuration_id != bundle.experiment_configuration_id
        or experiment.evidence_class is not EvidenceClass.CONFIRMATORY
        or experiment.market_data_source_class is not bundle.source_class
        or experiment.target_dataset_id != source.source_target_dataset_id
        or experiment.observation_dataset_id != source.observation_dataset_id
        or experiment.foundation_configuration_id != source.foundation_configuration_id
        or tuple(experiment.target_instruments) != source.target_instruments
        or experiment.holdout_range != source.holdout_range
        or int(experiment.primary_horizon.total_seconds()) != source.primary_horizon_seconds
    ):
        raise ValueError("F2 target source differs from the exact experiment")
    descriptor_values: dict[str, object] = {
        "r1_bundle_id": experiment.r1_bundle_id,
        "foundation_configuration_id": experiment.foundation_configuration_id,
        "panel_dataset_id": experiment.panel_dataset_id,
        "fold_dataset_id": experiment.fold_dataset_id,
        "ordered_instruments": list(experiment.ordered_instruments),
        "instrument_roles": {
            instrument: role.value for instrument, role in experiment.instrument_roles.items()
        },
        "horizons_seconds": [int(horizon.total_seconds()) for horizon in experiment.horizons],
        "feature_windows_seconds": [
            int(window.total_seconds()) for window in experiment.feature_windows
        ],
        "acceptance_thresholds": dict(experiment.acceptance_thresholds),
        "alpha_grid": list(experiment.alpha_grid),
        "inner_validation_policy": experiment.inner_validation_policy,
        "preprocessing_policy": experiment.preprocessing_policy,
        "pooled_weighting_policy": experiment.pooled_weighting_policy,
        "ridge_solver": experiment.ridge_solver,
        "ridge_tolerance": experiment.ridge_tolerance,
        "ridge_max_iterations": experiment.ridge_max_iterations,
        "minimum_training_rows": experiment.minimum_training_rows,
        "minimum_inner_validation_rows": experiment.minimum_inner_validation_rows,
        "minimum_outer_validation_rows": experiment.minimum_outer_validation_rows,
        "model_selection_policy": experiment.model_selection_policy,
        "metric_policy": experiment.metric_policy,
        "forecast_bucket_policy": experiment.forecast_bucket_policy,
        "state_bucket_policy": experiment.state_bucket_policy,
    }
    for key, expected in descriptor_values.items():
        if descriptor.get(key) != expected:
            raise ValueError(f"F2 descriptor differs for {key}")
    return source, experiment


async def _authenticate_confirmatory_parent(
    *,
    descriptor: Mapping[str, object],
    source: R2HoldoutTargetSource,
    experiment: R2ExperimentConfig,
) -> AuthenticatedR2Foundation:
    raw_authority = descriptor.get("foundation_authority")
    if not isinstance(raw_authority, dict):
        raise ValueError("F2 descriptor has no authenticated foundation authority")
    runtime_raw = descriptor.get("runtime_inputs")
    if not isinstance(runtime_raw, dict):
        raise ValueError("F2 descriptor has no immediate-parent runtime locators")
    runtime = cast(dict[str, object], runtime_raw)
    expected_keys = {
        "foundation",
        "foundation_receipt",
        "foundation_promotion",
        "experiment",
        "research_root",
        "feature_manifests",
    }
    if "holdout_target_source" in runtime:
        expected_keys.add("holdout_target_source")
    if set(runtime) != expected_keys:
        raise ValueError("F2 runtime locators are incomplete or contain unknown fields")
    locators: dict[str, str] = {}
    for key in ("foundation", "foundation_receipt", "experiment"):
        value = runtime[key]
        if not isinstance(value, str):
            raise ValueError(f"F2 runtime locator is malformed: {key}")
        locators[key] = value
    research_root_raw = runtime["research_root"]
    if not isinstance(research_root_raw, str):
        raise ValueError("F2 research root locator is malformed")
    locators["research_root"] = research_root_raw
    foundation_path = _promotion_runtime_path(locators, "foundation")
    receipt_path = _promotion_runtime_path(locators, "foundation_receipt")
    experiment_path = _promotion_runtime_path(locators, "experiment")
    research_root = _promotion_runtime_path(locators, "research_root", directory=True)
    raw_promotion = runtime["foundation_promotion"]
    promotion_path: Path | None
    if raw_promotion is None:
        promotion_path = None
    elif isinstance(raw_promotion, str):
        locators["foundation_promotion"] = raw_promotion
        promotion_path = _promotion_runtime_path(locators, "foundation_promotion")
    else:
        raise ValueError("F2 foundation promotion locator is malformed")
    if experiment_path != _descriptor_experiment_path(descriptor):
        raise ValueError("F2 experiment locator differs from its descriptor")
    clock = cast(Clock, SimpleNamespace(now=lambda: datetime.now(UTC)))
    if experiment.market_data_source_class is MarketDataSourceClass.IBKR_HISTORICAL_RESEARCH:
        adapter_payload = experiment.source_adapter_identity
        if not isinstance(adapter_payload, Mapping):
            raise ValueError("IBKR experiment has no persisted adapter identity")
        authority = authenticate_ibkr_foundation_for_r2(
            foundation_path=foundation_path,
            receipt_path=receipt_path,
            adapter_identity=IBKRHistoricalAdapterIdentity.from_json(adapter_payload),
            evidence_class=EvidenceClass.CONFIRMATORY,
            holdout_target_source=source,
            promotion_path=promotion_path,
        )
    else:
        if promotion_path is not None:
            raise ValueError("native confirmatory authority cannot include a Stage 8 promotion")
        authority = await authenticate_r1_foundation_for_r2(
            root=research_root,
            bundle_path=foundation_path,
            receipt_path=receipt_path,
            clock=clock,
            evidence_class=EvidenceClass.CONFIRMATORY,
            outcome_blind=True,
            holdout_target_source=source,
        )
    if authority.identity_json() != cast(dict[str, JsonValue], raw_authority):
        raise ValueError("F2 foundation authority differs from its descriptor")
    verify_exact_r1_bindings(authority.semantic_inputs, experiment)
    if authority.g2_feature_source is None:
        raise ValueError("F2 foundation has no authenticated G2 feature source")
    return authority


def _confirmatory_ready_report(
    *,
    authority: AuthenticatedR2Foundation,
    source: R2HoldoutTargetSource,
    experiment: R2ExperimentConfig,
) -> R2ReadinessReport:
    report = evaluate_outcome_blind_confirmatory_readiness(
        experiment=experiment,
        target_source=source,
        folds=authority.semantic_inputs.folds,
        source_active=authority.semantic_inputs.source_active_intervals,
        r1_bundle_id=experiment.r1_bundle_id,
    )
    if report.confirmatory_data_ready is not ReadinessState.READY:
        raise ValueError("F2 promotion requires qualifying confirmatory readiness")
    report = _complete_confirmatory_readiness(report)
    if (
        report.inner_validation_rows_ready is not ReadinessState.READY
        or report.confirmatory_oof_ready is not ReadinessState.READY
    ):
        raise ValueError("F2 promotion requires complete inner-validation and OOF readiness")
    return report


def _promotion_readiness_report(
    *,
    promotion: ConfirmatoryF2Promotion,
    experiment: R2ExperimentConfig,
) -> R2ReadinessReport:
    """Expose receipt-bound F2 readiness without replaying any parent transformation."""
    feature_states = {
        family: (
            ReadinessState.PARTIALLY_READY
            if decision.state is FeatureEligibility.PENDING
            else ReadinessState.READY
        )
        for family, decision in experiment.feature_eligibility.items()
    }
    return R2ReadinessReport(
        experiment_configuration_id=experiment.configuration_id,
        r1_bundle_id=experiment.r1_bundle_id,
        software_contract_ready=ReadinessState.READY,
        representative_integration_ready=ReadinessState.READY,
        confirmatory_data_ready=ReadinessState(promotion.confirmatory_data_ready),
        inner_validation_rows_ready=ReadinessState(promotion.inner_validation_rows_ready),
        confirmatory_oof_ready=ReadinessState(promotion.confirmatory_oof_ready),
        locked_holdout_ready=ReadinessState.NOT_READY,
        feature_family_states=feature_states,
        coverage_matrix={},
        usable_common_week_count=0,
        active_source_duration_seconds={},
        unmet_conditions=(),
        evidence_class=EvidenceClass.CONFIRMATORY,
        market_data_source_class=experiment.market_data_source_class,
    )


def _persisted_confirmatory_f2_inputs(
    *,
    path: Path,
    bundle: R2OofBundle,
    experiment: R2ExperimentConfig,
) -> tuple[
    tuple[ConfigurationRecord, ...],
    tuple[SelectionDecision, ...],
    tuple[str, ...],
    tuple[str, ...],
    dict[str, JsonValue],
    str,
    str,
    dict[str, JsonValue],
    tuple[tuple[str, ModelFamily, str | None, str | None, str | None], ...],
    str,
    VerifiedConfirmatoryHoldoutAuthority,
]:
    register = _oof_child_payload(path, bundle, R2_EVALUATION_REGISTER_CONTRACT)
    evaluation = _oof_child_payload(path, bundle, R2_EVALUATION_CONTRACT)
    raw_configurations = register.get("configurations")
    if not isinstance(raw_configurations, list) or not raw_configurations:
        raise ValueError("F2 register has no complete configuration set")
    configurations = tuple(_configuration_record_from_payload(item) for item in raw_configurations)
    configuration_ids = tuple(item.configuration_id for item in configurations)
    if len(set(configuration_ids)) != len(configuration_ids) or {
        item.model_family for item in configurations
    } != set(ModelFamily):
        raise ValueError("F2 configuration register is incomplete")
    decisions = _selection_decisions_from_payload(register.get("selection_decisions"))
    decision_ids = tuple(item.configuration_id for item in decisions)
    if len(set(decision_ids)) != len(decision_ids) or set(decision_ids) != set(configuration_ids):
        raise ValueError("F2 selection decisions do not cover the register")
    selected_ids = tuple(
        item.configuration_id
        for item in decisions
        if item.disposition is ConfigurationDisposition.SELECTED_CANDIDATE
    )
    holdout_ids = tuple(
        item.configuration_id
        for item in decisions
        if item.disposition
        in (
            ConfigurationDisposition.SELECTED_CANDIDATE,
            ConfigurationDisposition.RETAINED_CONTROL,
        )
    )
    stored_selected = register.get("selection_selected_configuration_ids")
    stored_holdout = register.get("selection_holdout_comparator_configuration_ids")
    if (
        not isinstance(stored_selected, list)
        or not isinstance(stored_holdout, list)
        or not all(isinstance(item, str) for item in (*stored_selected, *stored_holdout))
        or tuple(sorted(stored_selected)) != selected_ids
        or tuple(sorted(stored_holdout)) != holdout_ids
    ):
        raise ValueError("F2 register selection is not authenticated")
    selection_policy = _authenticated_selection_policy(register, experiment)
    report_id = register.get("selection_evaluation_report_id")
    register_id = register.get("report_id")
    if not isinstance(report_id, str) or not isinstance(register_id, str):
        raise ValueError("F2 register has no authenticated evaluation report")
    local_ref = register.get("local_comparator")
    if not isinstance(local_ref, dict) or not isinstance(local_ref.get("semantic_id"), str):
        raise ValueError("F2 register has no authenticated local comparator")
    local_ref_payload = cast(dict[str, object], local_ref)
    local_id = str(local_ref_payload["semantic_id"])
    evaluation_policy = holdout_evaluation_policy(
        path,
        bundle,
        expected_evaluation_report_id=report_id,
        evaluation_payload=evaluation,
    )
    registry = holdout_configuration_registry(
        path,
        bundle,
        expected_evaluation_report_id=report_id,
        expected_selected_configuration_ids=selected_ids,
        expected_holdout_configuration_ids=holdout_ids,
        evaluation_payload=evaluation,
        register_payload=register,
    )
    authority = VerifiedConfirmatoryHoldoutAuthority._create(
        _VERIFIED_CONFIRMATORY_HOLDOUT_AUTHORITY_TOKEN,
        oof_id=bundle.oof_id,
        evaluation_report_id=report_id,
        configuration_registry=registry,
        evaluation_policy=evaluation_policy,
        experiment_configuration_id=bundle.experiment_configuration_id,
        evidence_class=bundle.evidence_class,
        local_comparator_manifest_id=local_id,
        evaluated_configuration_ids=tuple(item.configuration_id for item in configurations),
        selection_decisions=decisions,
        selected_configuration_ids=selected_ids,
        holdout_comparator_configuration_ids=holdout_ids,
        selection_policy=selection_policy,
        holdout_range=experiment.holdout_range,
        source_class=bundle.source_class,
        foundation_bundle_id=bundle.foundation_bundle_id,
    )
    return (
        configurations,
        decisions,
        selected_ids,
        holdout_ids,
        selection_policy,
        report_id,
        register_id,
        evaluation_policy,
        registry,
        local_id,
        authority,
    )


def _promotion_register_reference(bundle: R2OofBundle, report_id: str) -> ArtifactReference:
    matches = tuple(
        reference
        for reference in bundle.evaluation_children
        if reference.contract == R2_EVALUATION_REGISTER_CONTRACT
        and reference.semantic_id == report_id
    )
    if len(matches) != 1:
        raise ValueError("F2 bundle must contain exactly one authenticated evaluation register")
    return matches[0]


def _promotion_runtime_locators(oof_path: Path, receipt_path: Path) -> dict[str, str]:
    return {
        "oof_bundle": str(_canonical_path(oof_path, "OOF bundle")),
        "oof_receipt": str(_canonical_path(receipt_path, "OOF verification receipt")),
    }


def _load_canonical_promotion(path: Path) -> dict[str, object]:
    promotion_path = _canonical_path(path, "F2 promotion")
    encoded = promotion_path.read_bytes()
    if len(encoded) > 64 * 1024:
        raise ValueError("F2 promotion exceeds the size limit")
    try:
        value = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("F2 promotion is not valid JSON") from error
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError("F2 promotion must be a JSON object")
    payload = cast(dict[str, object], value)
    if encoded != canonical_bytes(payload):
        raise ValueError("F2 promotion is not canonical")
    return payload


def authenticate_confirmatory_f2_promotion(
    promotion: Path,
    *,
    oof_bundle: Path | None = None,
    oof_receipt: Path | None = None,
    _readiness_report: R2ReadinessReport | None = None,
) -> VerifiedConfirmatoryF2Promotion:
    """Restore a confirmatory F2 capability by authenticating its OOF receipt only."""
    promotion_document = ConfirmatoryF2Promotion.from_json(_load_canonical_promotion(promotion))
    locators = promotion_document.runtime_locators
    oof_path = (
        _canonical_path(oof_bundle, "OOF bundle")
        if oof_bundle is not None
        else _promotion_runtime_path(locators, "oof_bundle")
    )
    receipt_path = (
        _canonical_path(oof_receipt, "OOF verification receipt")
        if oof_receipt is not None
        else _promotion_runtime_path(locators, "oof_receipt")
    )
    bundle_value, descriptor, parsed_receipt = _authenticate_r2_oof_with_receipt(
        oof_path, receipt=receipt_path
    )
    if (
        parsed_receipt.verification_id != promotion_document.oof_verification_id
        or parsed_receipt.oof_id != promotion_document.oof_id
        or parsed_receipt.closure_id != promotion_document.oof_closure_id
        or parsed_receipt.manifest_sha256 != promotion_document.oof_manifest_sha256
        or parsed_receipt.foundation_semantic_id != promotion_document.foundation_semantic_id
        or parsed_receipt.foundation_verification_id
        != promotion_document.foundation_verification_id
        or parsed_receipt.foundation_promotion_id != promotion_document.foundation_promotion_id
        or parsed_receipt.source_class is not promotion_document.source_class
        or parsed_receipt.evidence_class is not EvidenceClass.CONFIRMATORY
        or parsed_receipt.verifier_contract != promotion_document.oof_verifier_contract
        or parsed_receipt.verifier_version != promotion_document.oof_verifier_version
        or parsed_receipt.numerical_identity != promotion_document.oof_numerical_identity
        or parsed_receipt.completed_checks != promotion_document.required_oof_checks
    ):
        raise ValueError("F2 promotion does not bind the exact OOF receipt")
    source, experiment = _confirmatory_descriptor_inputs(oof_path, bundle_value, descriptor)
    authority = asyncio.run(
        _authenticate_confirmatory_parent(
            descriptor=descriptor, source=source, experiment=experiment
        )
    )
    report = (
        _readiness_report
        if _readiness_report is not None
        else _promotion_readiness_report(promotion=promotion_document, experiment=experiment)
    )
    (
        configurations,
        decisions,
        selected_ids,
        holdout_ids,
        selection_policy,
        report_id,
        register_id,
        evaluation_policy,
        registry,
        local_comparator_manifest_id,
        holdout_authority,
    ) = _persisted_confirmatory_f2_inputs(path=oof_path, bundle=bundle_value, experiment=experiment)
    register_ref = _promotion_register_reference(bundle_value, register_id)
    if (
        promotion_document.evaluation_register_semantic_id != register_ref.semantic_id
        or promotion_document.evaluation_register_sha256 != register_ref.sha256
        or promotion_document.evaluation_report_id != report_id
        or promotion_document.confirmatory_data_ready != report.confirmatory_data_ready.value
        or promotion_document.inner_validation_rows_ready
        != report.inner_validation_rows_ready.value
        or promotion_document.confirmatory_oof_ready != report.confirmatory_oof_ready.value
        or promotion_document.experiment_semantic_id != experiment.configuration_id
        or promotion_document.source_class is not bundle_value.source_class
    ):
        raise ValueError("F2 promotion claims differ from authenticated persisted inputs")
    return VerifiedConfirmatoryF2Promotion._create_promotion(
        _VERIFIED_CONFIRMATORY_F2_PROMOTION_TOKEN,
        promotion=promotion_document,
        bundle=bundle_value,
        holdout_target_source=source,
        g2_feature_source_authority=authority.g2_feature_source,
        descriptor=cast(Mapping[str, JsonValue], descriptor),
        evaluation_report_id=report_id,
        experiment=experiment,
        local_comparator_manifest_id=local_comparator_manifest_id,
        outcome_blind_foundation=authority.semantic_inputs,
        evaluated_configurations=configurations,
        selection_decisions=decisions,
        selected_configuration_ids=selected_ids,
        holdout_comparator_configuration_ids=holdout_ids,
        configuration_registry=registry,
        evaluation_policy=evaluation_policy,
        confirmatory_holdout_authority=holdout_authority,
        readiness_report=report,
        runtime_identities={
            field: cast(str, descriptor[field]) for field in _OOF_DESCRIPTOR_PROVENANCE_FIELDS
        },
        selection_policy=selection_policy,
    )


def create_confirmatory_f2_promotion(
    oof_bundle: Path,
    *,
    oof_receipt: Path,
    output: Path,
    authorized_by: str,
    authorized_at: datetime,
) -> VerifiedConfirmatoryF2Promotion:
    """Create one durable F2 promotion after cheap receipt authentication."""
    oof_path = _canonical_path(oof_bundle, "OOF bundle")
    receipt_path = _canonical_path(oof_receipt, "OOF verification receipt")
    output_path = _preflight_promotion_output(
        output,
        forbidden_roots=(oof_path.parent,),
    )
    bundle_value, descriptor, parsed_receipt = _authenticate_r2_oof_with_receipt(
        oof_path, receipt=receipt_path
    )
    if (
        parsed_receipt.evidence_class is not EvidenceClass.CONFIRMATORY
        or parsed_receipt.completed_checks != R2_CONFIRMATORY_OOF_COMPLETED_CHECKS
    ):
        raise ValueError("F2 promotion requires the current qualifying OOF receipt checks")
    source, experiment = _confirmatory_descriptor_inputs(oof_path, bundle_value, descriptor)
    output_path = _preflight_promotion_output(
        output_path,
        forbidden_roots=(oof_path.parent, *_promotion_parent_boundaries(descriptor)),
    )
    authority = asyncio.run(
        _authenticate_confirmatory_parent(
            descriptor=descriptor, source=source, experiment=experiment
        )
    )
    report = _confirmatory_ready_report(authority=authority, source=source, experiment=experiment)
    (
        _configurations,
        _decisions,
        _selected_ids,
        _holdout_ids,
        _selection_policy,
        report_id,
        register_id,
        _evaluation_policy,
        _registry,
        _local_comparator_manifest_id,
        _holdout_authority,
    ) = _persisted_confirmatory_f2_inputs(path=oof_path, bundle=bundle_value, experiment=experiment)
    register_ref = _promotion_register_reference(bundle_value, register_id)
    output_path = _preflight_promotion_output(output_path)
    promotion_document = ConfirmatoryF2Promotion.create(
        oof_id=bundle_value.oof_id,
        oof_closure_id=bundle_value.closure_id,
        oof_manifest_sha256=parsed_receipt.manifest_sha256,
        oof_verification_id=parsed_receipt.verification_id,
        experiment_semantic_id=experiment.configuration_id,
        foundation_semantic_id=parsed_receipt.foundation_semantic_id,
        foundation_verification_id=parsed_receipt.foundation_verification_id,
        foundation_promotion_id=parsed_receipt.foundation_promotion_id,
        source_class=bundle_value.source_class,
        evidence_class=EvidenceClass.CONFIRMATORY,
        oof_verifier_contract=parsed_receipt.verifier_contract,
        oof_verifier_version=parsed_receipt.verifier_version,
        oof_numerical_identity=parsed_receipt.numerical_identity,
        required_oof_checks=R2_CONFIRMATORY_OOF_COMPLETED_CHECKS,
        evaluation_register_semantic_id=register_ref.semantic_id,
        evaluation_register_sha256=register_ref.sha256,
        evaluation_report_id=report_id,
        confirmatory_data_ready=report.confirmatory_data_ready.value,
        inner_validation_rows_ready=report.inner_validation_rows_ready.value,
        confirmatory_oof_ready=report.confirmatory_oof_ready.value,
        authorized_by=authorized_by,
        authorized_at=authorized_at,
        runtime_locators=_promotion_runtime_locators(oof_path, receipt_path),
    )
    atomic_create(output_path, canonical_bytes(promotion_document.as_json()))
    return authenticate_confirmatory_f2_promotion(
        output_path, oof_bundle=oof_path, oof_receipt=receipt_path, _readiness_report=report
    )


def _build_confirmatory_selection(
    *,
    verified_f2: VerifiedConfirmatoryF2,
    frozen_at: datetime,
    frozen_by: str,
) -> R2HoldoutSelectionManifest:
    """Derive the only confirmatory G1 permitted by one verified F2 authority."""
    if not isinstance(verified_f2, VerifiedConfirmatoryF2):
        raise TypeError("confirmatory selection requires an authenticated F2 capability")
    if not frozen_by.strip():
        raise ValueError("frozen-by must be non-empty")
    source = verified_f2.holdout_target_source
    projection = R2HoldoutTargetProjection.create_from_source(source)
    projection.verify_source(source)
    opportunity_registry = R2HoldoutOpportunityRegistry.create_from_source(source)
    opportunity_registry.verify_source(source)
    experiment = verified_f2.experiment
    holdout_range = source.holdout_range
    selected = verified_f2.selected_configuration_ids
    controls = tuple(sorted(set(verified_f2.holdout_comparator_configuration_ids) - set(selected)))
    registry_by_id = {item[0]: item for item in verified_f2.configuration_registry}
    if (set(selected) | set(controls)) - set(registry_by_id):
        raise ValueError("confirmatory selection references an unknown configuration")

    selection_policy = verified_f2.selection_policy
    metric = selection_policy.get("primary_metric")
    secondary_metrics_raw = selection_policy.get("secondary_metrics")
    threshold_pairs_raw = selection_policy.get("acceptance_thresholds")
    predeclared_raw = selection_policy.get("predeclared_comparators")
    final_fitting_procedure = selection_policy.get("final_fitting_procedure")
    if (
        not isinstance(metric, str)
        or not isinstance(secondary_metrics_raw, tuple)
        or not all(isinstance(item, str) for item in secondary_metrics_raw)
        or not isinstance(threshold_pairs_raw, tuple)
        or not isinstance(predeclared_raw, tuple)
        or not all(isinstance(item, str) for item in predeclared_raw)
        or not isinstance(final_fitting_procedure, str)
    ):
        raise ValueError("confirmatory F2 has no complete authenticated selection policy")
    thresholds: dict[str, float] = {}
    for pair in threshold_pairs_raw:
        if (
            not isinstance(pair, tuple)
            or len(pair) != 2
            or not isinstance(pair[0], str)
            or isinstance(pair[1], bool)
            or not isinstance(pair[1], (int, float))
        ):
            raise ValueError("confirmatory F2 selection thresholds are malformed")
        thresholds[pair[0]] = float(pair[1])
    if tuple(sorted(thresholds.items())) != tuple(sorted(experiment.acceptance_thresholds.items())):
        raise ValueError("confirmatory F2 selection thresholds differ from the experiment")

    evaluation_policy: dict[str, JsonValue] = dict(verified_f2.evaluation_policy)
    minimum_support = evaluation_policy.get("minimum_correlation_rows")
    if not isinstance(minimum_support, int) or minimum_support <= 0:
        raise ValueError("confirmatory F2 has no authenticated evaluation support policy")
    threshold = thresholds.get("maximum_primary_mse_degradation")
    minimum_coverage = thresholds.get("minimum_common_support")
    if threshold is None or minimum_coverage is None:
        raise ValueError("confirmatory F2 has no authenticated comparison policy")
    hierarchy_by_family: dict[ModelFamily, str] = {}
    for configuration_id in verified_f2.holdout_comparator_configuration_ids:
        family = registry_by_id[configuration_id][1]
        if family in hierarchy_by_family:
            raise ValueError("confirmatory F2 holdout hierarchy contains duplicate model families")
        hierarchy_by_family[family] = configuration_id
    required_controls = {ModelFamily.ZERO_RETURN, ModelFamily.LOCAL_RIDGE}
    if not required_controls.issubset(hierarchy_by_family):
        raise ValueError("confirmatory F2 holdout comparator hierarchy lacks its local baseline")
    hierarchy = tuple(
        (candidate, comparator)
        for candidate, comparator in (
            (ModelFamily.LOCAL_RIDGE, ModelFamily.ZERO_RETURN),
            (ModelFamily.POOLED_LOCAL_RIDGE, ModelFamily.LOCAL_RIDGE),
            (
                ModelFamily.POOLED_CROSS_ASSET_RIDGE,
                ModelFamily.POOLED_LOCAL_RIDGE,
            ),
        )
        if candidate in hierarchy_by_family and comparator in hierarchy_by_family
    )
    questions = tuple(
        R2HoldoutQuestion.create(
            question=(
                f"{hierarchy_by_family[candidate_family]} versus "
                f"{hierarchy_by_family[comparator_family]} on {metric}"
            ),
            candidate_configuration_id=hierarchy_by_family[candidate_family],
            comparator_configuration_id=hierarchy_by_family[comparator_family],
            metric=metric,
            support_policy="COMMON_ELIGIBLE",
            direction=HoldoutDirection.LOWER_IS_BETTER,
            threshold=threshold,
            minimum_support=minimum_support,
            minimum_coverage=minimum_coverage,
            conclusion_policy="THRESHOLD_OR_INCONCLUSIVE",
        )
        for candidate_family, comparator_family in hierarchy
    )

    runtime_values: dict[str, JsonValue] = dict(verified_f2.runtime_identities)
    runtime_values["instrument_identity_order"] = list(source.target_instruments)
    runtime_values["instrument_identity_policy"] = POOLED_INSTRUMENT_IDENTITY_POLICY
    final_fitting_policy = R2FinalFittingPolicy.create(
        pre_holdout_membership_policy="PRIMARY_HORIZON_MATURE_BEFORE_HOLDOUT_V1",
        maturity_purge_policy="TARGET_INTERVAL_PURGE_V1",
        inner_validation_policy=experiment.inner_validation_policy,
        alpha_grid=experiment.alpha_grid,
        alpha_tie_break_policy="LOSS_THEN_LARGER_ALPHA",
        preprocessing_policy=experiment.preprocessing_policy,
        pooled_membership_policy=POOLED_INSTRUMENT_MEMBERSHIP_POLICY,
        pooled_weighting_policy=experiment.pooled_weighting_policy,
        instrument_intercept_policy=POOLED_INTERCEPT_POLICY,
        solver_identity={
            "name": experiment.ridge_solver,
            "tolerance": experiment.ridge_tolerance,
            "max_iterations": experiment.ridge_max_iterations,
        },
        training_prediction_threshold=1e-10,
        failure_disposition_policy="RETAIN_EXPLICIT_FAILURE",
        runtime_identities=runtime_values,
    )
    evaluation_policy.update(
        {
            "primary_horizon_seconds": source.primary_horizon_seconds,
            "target_dataset_id": source.source_target_dataset_id,
            "target_instruments": list(source.target_instruments),
            "holdout_target_source_id": source.source_id,
            "holdout_target_source_artifact": source.as_json(),
            "pre_holdout_target_dataset_id": source.pre_holdout_target_dataset.dataset_id,
            "pre_holdout_target_dataset": source.pre_holdout_target_dataset.as_json(),
            "pre_holdout_projection_id": projection.projection_id,
            "pre_holdout_projection": projection.as_json(),
            "holdout_opportunity_registry_id": opportunity_registry.registry_id,
            "holdout_opportunity_registry_artifact": opportunity_registry.as_json(),
            "model_selection_policy": experiment.model_selection_policy,
            "loss_policy": experiment.model_selection_policy,
            "minimum_training_rows": experiment.minimum_training_rows,
            "minimum_inner_validation_rows": experiment.minimum_inner_validation_rows,
            "pre_holdout_membership_policy": final_fitting_policy.pre_holdout_membership_policy,
            "maturity_purge_policy": final_fitting_policy.maturity_purge_policy,
            "instrument_intercept_policy": final_fitting_policy.instrument_intercept_policy,
            "alpha_tie_break_policy": final_fitting_policy.alpha_tie_break_policy,
            "preprocessing_policy": final_fitting_policy.preprocessing_policy,
            "pooled_membership_policy": final_fitting_policy.pooled_membership_policy,
            "pooled_weighting_policy": final_fitting_policy.pooled_weighting_policy,
            "solver_identity": dict(final_fitting_policy.solver_identity),
        }
    )
    prior_selection = SelectionManifest.create(
        experiment_configuration_id=verified_f2.experiment_configuration_id,
        evidence_class=verified_f2.evidence_class,
        evaluation_report_id=verified_f2.evaluation_report_id,
        local_comparator_manifest_id=verified_f2.local_comparator_manifest_id,
        evaluated_configuration_ids=tuple(
            item.configuration_id for item in verified_f2.evaluated_configurations
        ),
        predeclared_comparators=tuple(ModelFamily(item) for item in predeclared_raw),
        primary_metric=metric,
        secondary_metrics=tuple(cast(tuple[str, ...], secondary_metrics_raw)),
        acceptance_thresholds=tuple(sorted(thresholds.items())),
        decisions=verified_f2.selection_decisions,
        selected_configuration_ids=selected,
        holdout_comparator_configuration_ids=verified_f2.holdout_comparator_configuration_ids,
        final_fitting_procedure=final_fitting_procedure,
        holdout_range=holdout_range,
        application_image_identity=experiment.r1_image_identity,
        frozen_at=frozen_at,
        frozen_by=frozen_by,
        market_data_source_class=verified_f2.source_class,
        foundation_bundle_id=verified_f2.foundation_bundle_id,
        oof_id=verified_f2.bundle.oof_id,
    )
    selection = freeze_holdout_selection(
        prior_selection=prior_selection,
        foundation_bundle_id=verified_f2.foundation_bundle_id,
        oof_id=verified_f2.bundle.oof_id,
        source_class=verified_f2.source_class,
        evidence_class=EvidenceClass.CONFIRMATORY,
        holdout_scope=HoldoutScope.CONFIRMATORY,
        final_fitting_policy=final_fitting_policy,
        questions=questions,
        metric_policy={
            "suite": experiment.metric_policy,
            "name": metric,
            "primary_metric": metric,
            "secondary_metrics": list(cast(tuple[str, ...], secondary_metrics_raw)),
        },
        threshold_policy={
            "acceptance_thresholds": [[key, value] for key, value in sorted(thresholds.items())]
        },
        runtime_identities=runtime_values,
        frozen_metadata={
            "source_class": verified_f2.source_class.value,
            "evidence_class": EvidenceClass.CONFIRMATORY.value,
            "oof_id": verified_f2.bundle.oof_id,
            "operator": frozen_by,
        },
        frozen_at=frozen_at,
        frozen_by=frozen_by,
        verified_oof_bundle=verified_f2.bundle,
        verified_experiment=experiment,
        configuration_registry=verified_f2.configuration_registry,
        evaluation_policy=evaluation_policy,
        confirmatory_authority=verified_f2.confirmatory_holdout_authority,
        holdout_target_source=source,
        holdout_opportunity_registry=opportunity_registry,
        pre_holdout_projection=projection,
    )
    return selection


def freeze_confirmatory_selection(
    *,
    verified_f2: VerifiedConfirmatoryF2,
    output: Path,
    frozen_by: str,
) -> Path:
    """Derive and persist confirmatory G1 from one verified F2 authority only."""

    selection = _build_confirmatory_selection(
        verified_f2=verified_f2,
        frozen_at=datetime.now(UTC),
        frozen_by=frozen_by,
    )
    atomic_create(output, canonical_bytes(cast(dict[str, object], selection.as_json())))
    return output


def verify_confirmatory_g1(
    *,
    verified_f2: VerifiedConfirmatoryF2,
    path: Path,
) -> VerifiedConfirmatoryG1:
    """Prove G1 policy and compare it with the persisted selection."""

    if not isinstance(verified_f2, VerifiedConfirmatoryF2):
        raise TypeError("confirmatory G1 verification requires an authenticated F2 capability")
    selection = R2HoldoutSelectionManifest.from_json(_load_selection(path))
    if (
        selection.holdout_scope is not HoldoutScope.CONFIRMATORY
        or selection.evidence_class is not EvidenceClass.CONFIRMATORY
        or selection.holdout_outcomes_accessed
    ):
        raise ValueError("confirmatory G1 is not outcome-blind confirmatory evidence")
    expected = _build_confirmatory_selection(
        verified_f2=verified_f2,
        frozen_at=selection.frozen_at,
        frozen_by=selection.frozen_by,
    )
    if selection.as_json() != expected.as_json():
        raise ValueError(
            "persisted confirmatory G1 differs from independently replayed F2 authority"
        )
    return VerifiedConfirmatoryG1._create(
        _VERIFIED_CONFIRMATORY_G1_TOKEN,
        verified_f2=verified_f2,
        selection=selection,
    )


def _confirmatory_feature_sets(
    experiment: R2ExperimentConfig,
) -> Mapping[str, tuple[str, tuple[FeatureDefinition, ...]]]:
    result: dict[str, tuple[str, tuple[FeatureDefinition, ...]]] = {}
    for declared in experiment.feature_sets:
        schema = feature_schema_for_set(experiment, declared.name)
        identity = feature_set_id(
            experiment.configuration_id,
            declared.name,
            schema,
            experiment.market_data_source_class,
        )
        if identity in result:
            raise ValueError("confirmatory experiment contains duplicate feature-set identities")
        result[identity] = (declared.name, tuple(schema))
    return MappingProxyType(result)


def verify_confirmatory_g2_feature_source(
    verified_g1: VerifiedConfirmatoryG1,
) -> VerifiedG2FeatureSource:
    """Decode the exact G2-safe feature source only with verified G1 provenance."""

    if type(verified_g1) is not VerifiedConfirmatoryG1:
        raise TypeError("G2 feature decoding requires VerifiedConfirmatoryG1")
    if (
        getattr(verified_g1, "_verifier_provenance", None)
        is not _VERIFIED_CONFIRMATORY_G1_PROVENANCE
    ):
        raise TypeError("G2 feature decoding requires verified G1 provenance")
    access = verified_g1._g2_feature_access
    if (
        type(access) is not _VerifiedConfirmatoryG2FeatureAccess
        or getattr(access, "_verifier_provenance", None) is not _VERIFIED_CONFIRMATORY_G1_PROVENANCE
    ):
        raise TypeError("G2 feature decoding requires verified G1 feature access")
    if (
        access._verified_f2 is not verified_g1.verified_f2
        or access._selection_manifest_id != verified_g1.selection.manifest_id
    ):
        raise ValueError("G2 feature access differs from verified G1 provenance")
    authority = access._authority
    verified_f2 = verified_g1.verified_f2
    source = (
        _verify_ibkr_g2_feature_source(
            authority,
            holdout_target_source=verified_f2.holdout_target_source,
        )
        if isinstance(authority, IBKRG2FeatureSourceAuthority)
        else asyncio.run(
            _verify_g2_feature_source(
                authority,
                clock=cast(Clock, SimpleNamespace(now=lambda: datetime.now(UTC))),
            )
        )
    )
    experiment = verified_f2.experiment
    if (
        source.source_id != authority.source_id
        or source.observations.dataset_id != experiment.observation_dataset_id
        or source.panel.dataset_id != experiment.panel_dataset_id
        or source.panel.observation_dataset_id != experiment.observation_dataset_id
        or source.panel.foundation_configuration_id != experiment.foundation_configuration_id
        or source.observations.holdout_range != experiment.holdout_range
        or source.panel.holdout_range != experiment.holdout_range
    ):
        raise ValueError("verified G2 feature source differs from the exact G1 experiment")
    return source


@dataclass(frozen=True, slots=True)
class _ConfirmatoryG2Build:
    training_features: Mapping[str, R2FeatureDataset]
    holdout_features: Mapping[str, R2HoldoutFeatureDataset]
    fits: tuple[R2FinalFit, ...]
    forecasts: tuple[R2HoldoutForecastDataset, ...]
    coverage: tuple[R2HoldoutCoverageDataset, ...]
    seal: R2HoldoutForecastSeal


def _build_confirmatory_g2(
    *,
    verified_g1: VerifiedConfirmatoryG1,
    prepared_by: str,
) -> _ConfirmatoryG2Build:
    """Replay every scientific preparation child from verified G1 authority."""

    if type(verified_g1) is not VerifiedConfirmatoryG1:
        raise TypeError("confirmatory G2 preparation requires VerifiedConfirmatoryG1")
    selection = verified_g1.selection
    verified_f2 = verified_g1.verified_f2
    experiment = verified_f2.experiment
    target_source = verified_f2.holdout_target_source
    foundation = cast(R2OutcomeBlindFeatureInputs, verified_f2.outcome_blind_foundation)
    g2_source = verify_confirmatory_g2_feature_source(verified_g1)
    holdout_foundation = cast(
        R2OutcomeBlindFeatureInputs,
        SimpleNamespace(
            bundle=foundation.bundle,
            configuration=foundation.configuration,
            observations=g2_source.observations,
            panel=g2_source.panel,
            folds=foundation.folds,
            source_active_intervals=foundation.source_active_intervals,
        ),
    )
    opportunities = tuple(target_source.opportunities)
    feature_sets = _confirmatory_feature_sets(experiment)
    required_feature_set_ids = {
        feature_set
        for (
            configuration,
            family,
            feature_set,
            _dataset,
            _manifest,
        ) in selection.configuration_registry
        if configuration in selection.holdout_configuration_ids
        and family is not ModelFamily.ZERO_RETURN
        and feature_set is not None
    }
    if required_feature_set_ids - set(feature_sets):
        raise ValueError("confirmatory G1 references an unknown feature-set identity")

    training_features: dict[str, R2FeatureDataset] = {}
    holdout_features: dict[str, R2HoldoutFeatureDataset] = {}
    for feature_set_identity in sorted(required_feature_set_ids):
        feature_set_name, raw_schema = feature_sets[feature_set_identity]
        schema = tuple(raw_schema)
        training = materialise_outcome_blind_training_features(
            foundation,
            experiment,
            target_source,
            feature_set_name=feature_set_name,
        )
        training_features[feature_set_identity] = training
        raw_rows = build_outcome_blind_holdout_feature_rows(
            holdout_foundation,
            experiment,
            target_source,
            feature_set_name=feature_set_name,
            opportunities=opportunities,
        )
        holdout_features[feature_set_identity] = materialise_confirmatory_holdout_features(
            selection=selection,
            authority=verified_f2.confirmatory_holdout_authority,
            target_source=target_source,
            feature_set_id=feature_set_identity,
            feature_schema=schema,
            raw_rows=raw_rows,
            opportunities=opportunities,
            observation_dataset_id=g2_source.observations.dataset_id,
            panel_dataset_id=g2_source.panel.dataset_id,
        )

    fits: list[R2FinalFit] = []
    for (
        configuration_id,
        family,
        feature_set_identity,
        expected_training_dataset_id,
        _manifest_id,
    ) in selection.configuration_registry:
        if (
            configuration_id not in selection.holdout_configuration_ids
            or family is ModelFamily.ZERO_RETURN
        ):
            continue
        if feature_set_identity is None or expected_training_dataset_id is None:
            raise ValueError("fitted confirmatory configuration lacks feature authority")
        training = training_features[feature_set_identity]
        if training.dataset_id != expected_training_dataset_id:
            raise ValueError("replayed training features differ from authenticated F2 evidence")
        prepared_features = holdout_features[feature_set_identity]
        target_instruments: tuple[str | None, ...] = (
            tuple(target_source.target_instruments)
            if family is ModelFamily.LOCAL_RIDGE
            else (None,)
        )
        for target_instrument in target_instruments:
            fits.append(
                fit_final_ridge(
                    selection=selection,
                    configuration_id=configuration_id,
                    model_family=family,
                    target_instrument_id=target_instrument,
                    feature_dataset_id=prepared_features.dataset_id,
                    feature_schema_id=training.raw_feature_schema_id,
                    training_feature_dataset=training,
                    training_target_dataset=target_source.pre_holdout_target_dataset,
                    training_target_source_dataset_id=target_source.source_target_dataset_id,
                    training_cutoff=selection.holdout_range[0],
                    policy=selection.final_fitting_policy,
                    _confirmatory_token=_CONFIRMATORY_G2_PREPARATION_TOKEN,
                )
            )
    forecasts = build_holdout_forecasts(
        selection=selection,
        feature_datasets=holdout_features,
        final_fits=tuple(fits),
        opportunities=opportunities,
    )
    coverage = tuple(
        build_holdout_coverage(
            selection=selection,
            feature_datasets=holdout_features,
            final_fit=None,
            final_fits=tuple(
                fit for fit in fits if fit.configuration_id == forecast.configuration_id
            ),
            forecast_dataset=forecast,
            opportunities=opportunities,
        )
        for forecast in forecasts
    )
    seal = seal_holdout_forecasts(
        selection=selection,
        feature_datasets=holdout_features,
        final_fits=tuple(fits),
        forecast_datasets=forecasts,
        coverage_datasets=coverage,
        prepared_at=selection.frozen_at,
        prepared_by=prepared_by,
    )
    return _ConfirmatoryG2Build(
        training_features=MappingProxyType(training_features),
        holdout_features=MappingProxyType(holdout_features),
        fits=tuple(fits),
        forecasts=forecasts,
        coverage=coverage,
        seal=seal,
    )


def prepare_confirmatory_g2(
    *,
    verified_g1: VerifiedConfirmatoryG1,
    output: Path,
    prepared_by: str,
) -> Path:
    """Create one sealed confirmatory G2 preparation without reading holdout outcomes."""

    manifest_path = output / "manifest.json"
    if manifest_path.exists() or manifest_path.is_symlink():
        raise FileExistsError(manifest_path)
    built = _build_confirmatory_g2(verified_g1=verified_g1, prepared_by=prepared_by)
    target_source = verified_g1.verified_f2.holdout_target_source
    pre_holdout_targets = target_source.pre_holdout_target_dataset
    write_holdout_preparation(
        output,
        selection=verified_g1.selection,
        feature_datasets={child.dataset_id: child for child in built.holdout_features.values()},
        final_fits={fit.fit_id: fit for fit in built.fits},
        forecasts={forecast.dataset_id: forecast for forecast in built.forecasts},
        coverage={child.coverage_id: child for child in built.coverage},
        seal=built.seal,
        training_feature_datasets={
            child.dataset_id: child for child in built.training_features.values()
        },
        training_target_datasets={pre_holdout_targets.dataset_id: pre_holdout_targets},
        _confirmatory_token=_CONFIRMATORY_G2_PREPARATION_TOKEN,
    )
    return manifest_path


def verify_confirmatory_g2_preparation(
    *,
    verified_g1: VerifiedConfirmatoryG1,
    path: Path,
) -> VerifiedConfirmatoryG2Preparation:
    """Independently replay one sealed preparation against exact verified G1 authority."""

    if type(verified_g1) is not VerifiedConfirmatoryG1:
        raise TypeError("confirmatory preparation verification requires VerifiedConfirmatoryG1")
    seal = verify_holdout_preparation(
        path,
        _confirmatory_token=_CONFIRMATORY_G2_PREPARATION_TOKEN,
    )
    if (
        seal.selection_manifest_id != verified_g1.selection.manifest_id
        or seal.holdout_scope is not HoldoutScope.CONFIRMATORY
        or seal.evidence_class is not EvidenceClass.CONFIRMATORY
        or seal.holdout_outcomes_accessed
    ):
        raise ValueError("confirmatory preparation differs from verified G1 authority")
    persisted_selection = R2HoldoutSelectionManifest.from_json(
        _load_selection(path / "selection.json")
    )
    if persisted_selection.as_json() != verified_g1.selection.as_json():
        raise ValueError("confirmatory preparation contains a substituted G1 selection")
    expected = _build_confirmatory_g2(
        verified_g1=verified_g1,
        prepared_by=seal.prepared_by,
    )
    if seal.as_json() != expected.seal.as_json():
        raise ValueError(
            "confirmatory preparation differs from independently replayed "
            "G1 feature and fit authority"
        )
    return VerifiedConfirmatoryG2Preparation._create(
        _VERIFIED_CONFIRMATORY_G2_PREPARATION_TOKEN,
        verified_g1=verified_g1,
        seal=seal,
        path=path,
    )


def _require_verified_confirmatory_preparation(
    preparation: VerifiedConfirmatoryG2Preparation,
) -> None:
    if (
        type(preparation) is not VerifiedConfirmatoryG2Preparation
        or preparation._verifier_provenance is not _VERIFIED_CONFIRMATORY_G2_PREPARATION_PROVENANCE
        or type(preparation.verified_g1) is not VerifiedConfirmatoryG1
        or preparation.verified_g1._verifier_provenance is not _VERIFIED_CONFIRMATORY_G1_PROVENANCE
    ):
        raise TypeError("confirmatory reveal requires verifier-authenticated G2 preparation")


def _verify_confirmatory_g2_lifecycle(
    verified_g1: VerifiedConfirmatoryG1,
    path: Path,
) -> VerifiedConfirmatoryG2Preparation:
    if (
        type(verified_g1) is not VerifiedConfirmatoryG1
        or verified_g1._verifier_provenance is not _VERIFIED_CONFIRMATORY_G1_PROVENANCE
    ):
        raise TypeError("confirmatory lifecycle verification requires verified G1 provenance")
    seal = _verify_confirmatory_holdout_preparation(path)
    if (
        seal.selection_manifest_id != verified_g1.selection.manifest_id
        or seal.holdout_scope is not HoldoutScope.CONFIRMATORY
        or seal.evidence_class is not EvidenceClass.CONFIRMATORY
        or seal.holdout_outcomes_accessed
    ):
        raise ValueError("confirmatory lifecycle differs from verified G1 authority")
    persisted = R2HoldoutSelectionManifest.from_json(_load_selection(path / "selection.json"))
    if persisted.as_json() != verified_g1.selection.as_json():
        raise ValueError("confirmatory lifecycle contains a substituted G1 selection")
    expected = _build_confirmatory_g2(
        verified_g1=verified_g1,
        prepared_by=seal.prepared_by,
    )
    if seal.as_json() != expected.seal.as_json():
        raise ValueError("confirmatory lifecycle seal differs from independent G1 replay")
    return VerifiedConfirmatoryG2Preparation._create(
        _VERIFIED_CONFIRMATORY_G2_PREPARATION_TOKEN,
        verified_g1=verified_g1,
        seal=seal,
        path=path,
    )


def _base_opened_marker(
    preparation: VerifiedConfirmatoryG2Preparation,
) -> R2HoldoutOpenedMarker:
    payload = _load_selection(preparation.path / "opened.json")
    try:
        opened_at = datetime.fromisoformat(str(payload["opened_at"]))
    except (KeyError, ValueError) as error:
        raise ValueError("confirmatory OPENED timestamp is invalid") from error
    marker = R2HoldoutOpenedMarker.create(
        selection_manifest_id=preparation.verified_g1.selection.manifest_id,
        seal_id=preparation.seal.seal_id,
        opened_at=opened_at,
        opened_by=str(payload.get("opened_by", "")),
        acknowledgement=str(payload.get("acknowledgement", "")),
        expected_selection_manifest_id=str(payload.get("expected_selection_manifest_id", "")),
        expected_seal_id=str(payload.get("expected_seal_id", "")),
    )
    if marker.as_json() != payload:
        raise ValueError("persisted OPENED marker differs from its exact authority")
    return marker


def _confirmatory_opened_marker(
    preparation: VerifiedConfirmatoryG2Preparation,
    opened: R2HoldoutOpenedMarker,
) -> R2ConfirmatoryOpenedMarker:
    verified_f2 = preparation.verified_g1.verified_f2
    authority = verified_f2._g2_feature_source_authority
    target_manifest_id = (
        verified_f2.outcome_blind_foundation.bundle.targets.manifest_id
        if isinstance(authority, G2FeatureSourceAuthority)
        else authority.target_child_references_sha256
    )
    selection = preparation.verified_g1.selection
    return R2ConfirmatoryOpenedMarker.create(
        selection_manifest_id=selection.manifest_id,
        seal_id=preparation.seal.seal_id,
        opened_marker_id=opened.marker_id,
        oof_id=selection.oof_id,
        foundation_bundle_id=selection.foundation_bundle_id,
        experiment_configuration_id=selection.experiment_configuration_id,
        evaluation_report_id=selection.evaluation_report_id,
        prior_selection_manifest_id=selection.prior_selection_manifest_id,
        holdout_target_source_id=verified_f2.holdout_target_source.source_id,
        target_dataset_id=verified_f2.holdout_target_source.source_target_dataset_id,
        target_manifest_id=target_manifest_id,
        holdout_range=selection.holdout_range,
        source_class=selection.source_class,
        runtime_identities=selection.runtime_identities,
        opened_at=opened.opened_at,
        opened_by=opened.opened_by,
        acknowledgement=opened.acknowledgement,
    )


def _issue_opened_confirmatory_holdout(
    preparation: VerifiedConfirmatoryG2Preparation,
) -> OpenedConfirmatoryHoldout:
    _require_verified_confirmatory_preparation(preparation)
    marker = _confirmatory_opened_marker(preparation, _base_opened_marker(preparation))
    atomic_create(
        preparation.path / "confirmatory-opened.json",
        canonical_bytes(marker.as_json()),
    )
    persisted = R2ConfirmatoryOpenedMarker.from_json(
        _load_selection(preparation.path / "confirmatory-opened.json")
    )
    if persisted.as_json() != marker.as_json():
        raise ValueError("confirmatory OPENED marker differs from verifier authority")
    return OpenedConfirmatoryHoldout._create(
        _OPENED_CONFIRMATORY_HOLDOUT_TOKEN,
        preparation=preparation,
        marker=persisted,
    )


def _decode_confirmatory_target(opened: OpenedConfirmatoryHoldout) -> TargetDataset:
    if (
        type(opened) is not OpenedConfirmatoryHoldout
        or opened._verifier_provenance is not _OPENED_CONFIRMATORY_HOLDOUT_PROVENANCE
    ):
        raise TypeError("confirmatory outcome decoding requires durable OPENED provenance")
    preparation = opened.preparation
    _require_verified_confirmatory_preparation(preparation)
    verified_f2 = preparation.verified_g1.verified_f2
    authority = verified_f2._g2_feature_source_authority
    if isinstance(authority, IBKRG2FeatureSourceAuthority):
        targets = _verify_ibkr_confirmatory_target_dataset(
            authority,
            holdout_target_source=verified_f2.holdout_target_source,
        )
    else:
        targets = asyncio.run(
            _verify_confirmatory_target_dataset(
                cast(
                    OutcomeBlindVerifiedFoundationBundle,
                    verified_f2.outcome_blind_foundation,
                ),
                authority,
                clock=cast(
                    Clock,
                    SimpleNamespace(now=lambda: datetime.now(UTC)),
                ),
            )
        )
        verified_f2.holdout_target_source.verify_target_dataset(targets)
    if targets.dataset_id != opened.marker.target_dataset_id:
        raise ValueError("decoded confirmatory target differs from OPENED authority")
    return targets


def reveal_confirmatory_g2(
    *,
    preparation: VerifiedConfirmatoryG2Preparation,
    expected_selection_manifest_id: str,
    expected_seal_id: str,
    acknowledgement: str,
    opened_by: str,
    consumed_by: str,
    opened_at: datetime,
    clock: Clock,
) -> tuple[R2HoldoutEvaluation, R2HoldoutConsumedMarker]:
    """Irreversibly reveal and evaluate only the science frozen by verified G1."""

    _require_verified_confirmatory_preparation(preparation)
    if (
        expected_selection_manifest_id != preparation.verified_g1.selection.manifest_id
        or expected_seal_id != preparation.seal.seal_id
    ):
        raise ValueError("confirmatory acknowledgement IDs differ from verified preparation")

    def load_outcomes() -> TargetDataset:
        return _decode_confirmatory_target(_issue_opened_confirmatory_holdout(preparation))

    result = _reveal_confirmatory_holdout(
        preparation.path,
        expected_selection_manifest_id=expected_selection_manifest_id,
        expected_seal_id=expected_seal_id,
        acknowledgement=acknowledgement,
        opened_by=opened_by,
        consumed_by=consumed_by,
        opened_at=opened_at,
        consumed_at=clock.now,
        outcome_loader=load_outcomes,
    )
    evaluation, consumed = result
    if evaluation is None:
        raise ValueError("confirmatory reveal did not produce a frozen evaluation")
    report = verify_confirmatory_r2h(
        verified_g1=preparation.verified_g1,
        path=preparation.path,
    )
    if report.status is not ConfirmatoryR2HStatus.VALID_CONSUMED_RESULT:
        raise ValueError("confirmatory reveal did not produce a valid consumed R2.H result")
    return evaluation, consumed


def _verify_confirmatory_opened(
    preparation: VerifiedConfirmatoryG2Preparation,
) -> tuple[R2HoldoutOpenedMarker, R2ConfirmatoryOpenedMarker]:
    opened = _base_opened_marker(preparation)
    marker = R2ConfirmatoryOpenedMarker.from_json(
        _load_selection(preparation.path / "confirmatory-opened.json")
    )
    if marker.as_json() != _confirmatory_opened_marker(preparation, opened).as_json():
        raise ValueError("confirmatory OPENED marker differs from verified G1/F2 authority")
    return opened, marker


def verify_confirmatory_r2h(
    *,
    verified_g1: VerifiedConfirmatoryG1,
    path: Path,
) -> ConfirmatoryR2HReport:
    """Independently classify and numerically replay terminal confirmatory evidence."""

    selection_id = verified_g1.selection.manifest_id
    seal_id = ""
    opened_id: str | None = None
    try:
        preparation = _verify_confirmatory_g2_lifecycle(verified_g1, path)
        seal_id = preparation.seal.seal_id
        opened = _base_opened_marker(preparation)
        opened_id = opened.marker_id
        confirmatory_opened_path = preparation.path / "confirmatory-opened.json"
        if not confirmatory_opened_path.exists() and not confirmatory_opened_path.is_symlink():
            return ConfirmatoryR2HReport(
                ConfirmatoryR2HStatus.OPENED_INCOMPLETE,
                selection_id,
                seal_id,
                opened_id,
                None,
                None,
                "base OPENED exists without confirmatory OPENED authority",
            )
        _verify_confirmatory_opened(preparation)
        consumed_path = preparation.path / "consumed.json"
        if not consumed_path.exists() and not consumed_path.is_symlink():
            return ConfirmatoryR2HReport(
                ConfirmatoryR2HStatus.OPENED_INCOMPLETE,
                selection_id,
                seal_id,
                opened_id,
                None,
                None,
                "OPENED exists without terminal CONSUMED evidence",
            )
        _persisted_opened, consumed = verify_holdout_markers(preparation.path)
        evaluation_path = preparation.path / "evaluation.json"
        if not evaluation_path.exists() and not evaluation_path.is_symlink():
            return ConfirmatoryR2HReport(
                ConfirmatoryR2HStatus.OPENED_INCOMPLETE,
                selection_id,
                seal_id,
                opened_id,
                consumed.marker_id,
                consumed.evaluation_id,
                "post-OPENED execution failed before a valid evaluation",
            )
        evaluation = _verify_confirmatory_holdout_evaluation(preparation.path)
        if evaluation.selection_manifest_id != selection_id:
            raise ValueError("confirmatory evaluation differs from verified G1")
        return ConfirmatoryR2HReport(
            ConfirmatoryR2HStatus.VALID_CONSUMED_RESULT,
            selection_id,
            seal_id,
            opened_id,
            consumed.marker_id,
            evaluation.evaluation_id,
            "complete confirmatory lifecycle independently replayed",
        )
    except (FileNotFoundError, TypeError, ValueError) as error:
        return ConfirmatoryR2HReport(
            ConfirmatoryR2HStatus.INVALID,
            selection_id,
            seal_id,
            opened_id,
            None,
            None,
            str(error),
        )


def holdout_evaluation_policy(
    oof_bundle_path: Path,
    bundle: R2OofBundle,
    *,
    expected_evaluation_report_id: str | None = None,
    evaluation_payload: Mapping[str, object] | None = None,
) -> dict[str, JsonValue]:
    """Return authenticated evaluation controls and immediate comparison pairs."""

    evaluation = (
        evaluation_payload
        if evaluation_payload is not None
        else _oof_child_payload(oof_bundle_path, bundle, R2_EVALUATION_CONTRACT)
    )
    if (
        expected_evaluation_report_id is not None
        and evaluation.get("report_id") != expected_evaluation_report_id
    ):
        raise ValueError("OOF evaluation child differs from prior selection report")
    metric_policy = evaluation.get("metric_policy")
    forecast_bucket_policy = evaluation.get("forecast_bucket_policy")
    minimum_rows = evaluation.get("minimum_correlation_rows")
    bucket_count = evaluation.get("forecast_bucket_count")
    if not isinstance(metric_policy, str) or not isinstance(forecast_bucket_policy, str):
        raise ValueError("OOF evaluation policies are not authenticated strings")
    if not isinstance(minimum_rows, int) or not isinstance(bucket_count, int):
        raise ValueError("OOF evaluation support controls are not authenticated integers")

    raw_comparisons = evaluation.get("comparisons")
    if not isinstance(raw_comparisons, list) or not raw_comparisons:
        raise ValueError("OOF evaluation report has no authenticated comparison registry")
    comparison_registry: list[JsonValue] = []
    seen_comparisons: set[tuple[ModelFamily, ModelFamily]] = set()
    for raw_comparison in raw_comparisons:
        if not isinstance(raw_comparison, dict):
            raise ValueError("OOF evaluation comparison registry entry is not an object")
        try:
            candidate = ModelFamily(raw_comparison.get("candidate"))
            comparator = ModelFamily(raw_comparison.get("comparator"))
        except (TypeError, ValueError) as exc:
            raise ValueError("OOF evaluation comparison registry has an invalid family") from exc
        pair = (candidate, comparator)
        if pair in seen_comparisons:
            raise ValueError("OOF evaluation comparison registry contains duplicate pairs")
        seen_comparisons.add(pair)
        comparison_registry.append([candidate.value, comparator.value])
    return {
        "metric_policy": metric_policy,
        "forecast_bucket_policy": forecast_bucket_policy,
        "minimum_correlation_rows": minimum_rows,
        "forecast_bucket_count": bucket_count,
        "comparison_registry": comparison_registry,
    }


def _selection_decisions_from_payload(value: object) -> tuple[SelectionDecision, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("OOF register has no persisted selection-gate decisions")

    def metric(raw: object) -> MetricValue:
        if not isinstance(raw, dict):
            raise ValueError("selection gate metric is not an object")
        raw = cast(dict[str, object], raw)
        availability = MetricAvailability(str(raw["availability"]))
        if availability is MetricAvailability.DEFINED:
            number = raw["value"]
            if isinstance(number, bool) or not isinstance(number, (int, float)):
                raise ValueError("defined selection metric has no numeric value")
            if raw.get("reason") is not None:
                raise ValueError("defined selection metric has an unexpected reason")
            return MetricValue.defined(float(number))
        reason = raw["reason"]
        if not isinstance(reason, str) or not reason:
            raise ValueError("undefined selection metric has no reason")
        if raw.get("value") is not None:
            raise ValueError("undefined selection metric has an unexpected value")
        return MetricValue.not_defined(reason)

    decisions: list[SelectionDecision] = []
    for raw_decision in value:
        if not isinstance(raw_decision, dict):
            raise ValueError("selection decision is not an object")
        raw_decision = cast(dict[str, object], raw_decision)
        configuration_id = str(raw_decision["configuration_id"])
        gates_raw = raw_decision["gates"]
        if not isinstance(gates_raw, list):
            raise ValueError("selection decision gates are not a list")
        gates: list[SelectionGateOutcome] = []
        for raw_gate in gates_raw:
            if not isinstance(raw_gate, dict):
                raise ValueError("selection gate is not an object")
            raw_gate = cast(dict[str, object], raw_gate)
            gates.append(
                SelectionGateOutcome(
                    configuration_id=configuration_id,
                    name=str(raw_gate["name"]),
                    passed=bool(raw_gate["passed"]),
                    observed=metric(raw_gate["observed"]),
                    threshold=metric(raw_gate["threshold"]),
                    reason=str(raw_gate["reason"]),
                )
            )
        decisions.append(
            SelectionDecision(
                configuration_id=configuration_id,
                disposition=ConfigurationDisposition(str(raw_decision["disposition"])),
                reason=str(raw_decision["reason"]),
                gates=tuple(gates),
            )
        )
    ordered = tuple(sorted(decisions, key=lambda item: item.configuration_id))
    if ordered != tuple(decisions) or len({item.configuration_id for item in decisions}) != len(
        decisions
    ):
        raise ValueError("selection decisions are not unique and ordered")
    return ordered


def selection_freeze(
    *,
    oof_bundle_path: Path,
    frozen_by: str,
    output: Path,
    receipt: Path | None = None,
) -> Path:
    """Create a typed, holdout-free SelectionManifest from authenticated OOF evidence."""
    if not frozen_by.strip():
        raise ValueError("frozen-by must be non-empty")
    if receipt is None:
        bundle, descriptor = _authenticate_oof_closure(oof_bundle_path)
        run_kind = descriptor["run_kind"]
        if run_kind != "SYNTHETIC":
            if run_kind in {"REPRESENTATIVE", CONFIRMATORY_RUN_KIND}:
                raise ValueError("OOF selection freeze requires an OOF verification receipt")
            raise ValueError("OOF descriptor has an unsupported run kind")
    else:
        bundle, descriptor = _authenticate_r2_oof_with_descriptor(oof_bundle_path, receipt=receipt)
    register = _oof_child_payload(oof_bundle_path, bundle, R2_EVALUATION_REGISTER_CONTRACT)
    raw_configurations = register.get("configurations")
    if not isinstance(raw_configurations, list) or not raw_configurations:
        raise ValueError("OOF evaluation register has no complete configuration set")
    configurations: list[dict[str, object]] = []
    for item in raw_configurations:
        if not isinstance(item, dict):
            raise ValueError("OOF evaluation register contains an invalid configuration")
        configurations.append(cast(dict[str, object], item))
    evaluated_ids = tuple(sorted(str(item["configuration_id"]) for item in configurations))
    raw_selection_decisions = register.get("selection_decisions")
    decisions = _selection_decisions_from_payload(raw_selection_decisions)
    if tuple(item.configuration_id for item in decisions) != evaluated_ids:
        raise ValueError("persisted selection decisions do not cover the evaluation register")
    holdout_range_value = descriptor.get("holdout_range")
    if (
        not isinstance(holdout_range_value, list)
        or len(holdout_range_value) != 2
        or not all(isinstance(value, str) for value in holdout_range_value)
    ):
        raise ValueError("OOF descriptor has no authenticated holdout range")
    thresholds = descriptor.get("acceptance_thresholds")
    if not isinstance(thresholds, dict):
        raise ValueError("OOF descriptor has no authenticated selection thresholds")
    evaluation_payload = _oof_child_payload(oof_bundle_path, bundle, R2_EVALUATION_CONTRACT)
    evaluation_report_id = evaluation_payload.get("report_id")
    report_ref = register.get("evaluation")
    local_ref = register.get("local_comparator")
    selected_values = register.get("selection_selected_configuration_ids")
    holdout_values = register.get("selection_holdout_comparator_configuration_ids")
    if (
        not isinstance(evaluation_report_id, str)
        or register.get("selection_evaluation_report_id") != evaluation_report_id
        or not isinstance(report_ref, dict)
        or report_ref.get("semantic_id") != evaluation_report_id
        or not isinstance(local_ref, dict)
        or not isinstance(selected_values, list)
        or not isinstance(holdout_values, list)
        or not all(isinstance(item, str) for item in (*selected_values, *holdout_values))
    ):
        raise ValueError("OOF register has incomplete evaluation lineage or selection replay")
    selected_ids = tuple(sorted(cast(list[str], selected_values)))
    holdout_ids = tuple(sorted(cast(list[str], holdout_values)))
    if selected_ids != tuple(
        item.configuration_id
        for item in decisions
        if item.disposition is ConfigurationDisposition.SELECTED_CANDIDATE
    ) or holdout_ids != tuple(
        item.configuration_id
        for item in decisions
        if item.disposition
        in (ConfigurationDisposition.RETAINED_CONTROL, ConfigurationDisposition.SELECTED_CANDIDATE)
    ):
        raise ValueError("persisted selection IDs differ from replayed decisions")
    local_ref_payload = cast(dict[str, object], local_ref)
    local_comparator_id = local_ref_payload.get("semantic_id")
    if not isinstance(local_comparator_id, str):
        raise ValueError("OOF register local comparator has no semantic ID")
    threshold_values: list[tuple[str, float]] = []
    for name, value in thresholds.items():
        if not isinstance(name, str) or not isinstance(value, (int, float)):
            raise ValueError("OOF descriptor has invalid selection thresholds")
        threshold_values.append((name, float(value)))
    manifest = SelectionManifest.create(
        experiment_configuration_id=bundle.experiment_configuration_id,
        evidence_class=bundle.evidence_class,
        evaluation_report_id=evaluation_report_id,
        local_comparator_manifest_id=local_comparator_id,
        evaluated_configuration_ids=evaluated_ids,
        predeclared_comparators=tuple(ModelFamily),
        primary_metric="INSTRUMENT_BALANCED_COMMON_SUPPORT_MSE",
        secondary_metrics=("RMSE",),
        acceptance_thresholds=tuple(threshold_values),
        decisions=tuple(decisions),
        selected_configuration_ids=selected_ids,
        holdout_comparator_configuration_ids=holdout_ids,
        final_fitting_procedure="PENDING_R2_H_INTEGRATION",
        holdout_range=(
            datetime.fromisoformat(str(holdout_range_value[0])),
            datetime.fromisoformat(str(holdout_range_value[1])),
        ),
        application_image_identity=str(descriptor["application_identity"]),
        frozen_at=datetime.now(UTC),
        frozen_by=frozen_by,
        market_data_source_class=bundle.source_class,
        foundation_bundle_id=bundle.foundation_bundle_id,
        oof_id=bundle.oof_id,
    )
    atomic_create(output, canonical_bytes(cast(dict[str, object], manifest.as_json())))
    return output


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        candidate.relative_to(root).as_posix(): candidate.read_bytes()
        for candidate in root.rglob("*")
        if candidate.is_file()
    }


async def _replay_authority_oof_async(
    path: Path,
    *,
    expected_run_kind: str = "REPRESENTATIVE",
    authenticated_bundle: R2OofBundle | None = None,
    authenticated_source_authority: R2HoldoutTargetSourceAuthority | None = None,
) -> tuple[
    FoldDataset,
    Mapping[str, tuple[tuple[datetime, datetime], ...]],
    R1FoundationBindings,
    ConfirmatoryG2FeatureSourceAuthority | None,
]:
    """Replay R2 from the authenticated immediate parent and consumed feature bytes."""
    if authenticated_bundle is None:
        bundle, source_authority = _verify_r2_oof_bundle_with_source(path)
    else:
        bundle = authenticated_bundle
        source_authority = authenticated_source_authority
    if bundle.holdout_target_source is None:
        raise ValueError("OOF bundle has no authenticated holdout target source")
    descriptor = _oof_child_payload(path, bundle, OOF_DESCRIPTOR_CONTRACT)
    if (
        bundle.holdout_target_source.contract == R2_HOLDOUT_SOURCE_BINDING_CONTRACT
        and not isinstance(source_authority, R2HoldoutTargetSourceAuthority)
    ):
        source_authority = _oof_holdout_source_authority(path, bundle, descriptor)
    holdout_target_source = _oof_holdout_target_source(
        path,
        bundle,
        descriptor,
        authenticated_authority=(
            source_authority
            if isinstance(source_authority, R2HoldoutTargetSourceAuthority)
            else None
        ),
    )
    if descriptor.get("run_kind") != expected_run_kind:
        raise ValueError(f"authority replay requires a {expected_run_kind} OOF run")
    raw_authority = descriptor.get("foundation_authority")
    if not isinstance(raw_authority, dict):
        raise ValueError("OOF descriptor has no authenticated foundation authority")
    raw_runtime = descriptor.get("runtime_inputs")
    if not isinstance(raw_runtime, dict):
        raise ValueError("OOF descriptor has no immediate-parent runtime locators")
    expected_runtime_keys = {
        "foundation",
        "foundation_receipt",
        "foundation_promotion",
        "experiment",
        "research_root",
        "feature_manifests",
    }
    if bundle.holdout_target_source.contract == R2_HOLDOUT_SOURCE_BINDING_CONTRACT:
        expected_runtime_keys.add("holdout_target_source")
    if set(raw_runtime) != expected_runtime_keys:
        raise ValueError("OOF runtime locators are incomplete or contain unknown fields")
    runtime = cast(dict[str, object], raw_runtime)

    def runtime_file(name: str) -> Path:
        value = runtime[name]
        if not isinstance(value, str):
            raise ValueError(f"OOF runtime locator is not a path: {name}")
        candidate = Path(value)
        if not candidate.is_absolute() or candidate.is_symlink() or not candidate.is_file():
            raise ValueError(f"OOF runtime file is unavailable: {name}")
        return candidate

    foundation_path = runtime_file("foundation")
    receipt_path = runtime_file("foundation_receipt")
    experiment_path = runtime_file("experiment")
    raw_research_root = runtime["research_root"]
    if not isinstance(raw_research_root, str):
        raise ValueError("OOF runtime research root is not a path")
    research_root = Path(raw_research_root)
    if not research_root.is_absolute() or research_root.is_symlink() or not research_root.is_dir():
        raise ValueError("OOF runtime research root is unavailable")
    raw_feature_paths = runtime["feature_manifests"]
    if not isinstance(raw_feature_paths, dict):
        raise ValueError("OOF runtime feature manifests are malformed")
    if set(raw_feature_paths) != _REQUIRED_FEATURE_SETS:
        raise ValueError("OOF runtime feature manifests must cover exactly L0/L1/P0/P1")
    feature_paths: dict[str, Path] = {
        name: runtime_file_value
        for name, raw_path in cast(dict[str, object], raw_feature_paths).items()
        if isinstance(raw_path, str)
        for runtime_file_value in (Path(raw_path),)
    }
    if set(feature_paths) != _REQUIRED_FEATURE_SETS:
        raise ValueError("OOF runtime feature manifest paths are malformed")
    for name, feature_path in feature_paths.items():
        if (
            not feature_path.is_absolute()
            or feature_path.is_symlink()
            or not feature_path.is_file()
        ):
            raise ValueError(f"OOF runtime feature manifest is unavailable: {name}")
    holdout_target_source_path = (
        runtime_file("holdout_target_source")
        if bundle.holdout_target_source.contract == R2_HOLDOUT_SOURCE_BINDING_CONTRACT
        else None
    )
    raw_promotion_path = runtime["foundation_promotion"]
    promotion_path: Path | None
    if raw_promotion_path is None:
        promotion_path = None
    elif isinstance(raw_promotion_path, str):
        promotion_path = Path(raw_promotion_path)
        if (
            not promotion_path.is_absolute()
            or promotion_path.is_symlink()
            or not promotion_path.is_file()
        ):
            raise ValueError("OOF runtime foundation promotion is unavailable")
    else:
        raise ValueError("OOF runtime foundation promotion locator is malformed")

    experiment = load_r2_experiment(experiment_path)
    expected_evidence_class = (
        EvidenceClass.CONFIRMATORY
        if expected_run_kind == CONFIRMATORY_RUN_KIND
        else EvidenceClass.IMPLEMENTATION
    )
    if experiment.evidence_class is not expected_evidence_class:
        raise ValueError("OOF experiment has the wrong evidence classification")
    representative_profile = descriptor.get("representative_profile")
    if representative_profile is not None and not isinstance(representative_profile, str):
        raise ValueError("OOF representative profile is malformed")

    clock = cast(Clock, SimpleNamespace(now=lambda: datetime.now(UTC)))
    if experiment.market_data_source_class is MarketDataSourceClass.IBKR_HISTORICAL_RESEARCH:
        adapter_payload = experiment.source_adapter_identity
        if not isinstance(adapter_payload, Mapping):
            raise ValueError("IBKR experiment has no persisted adapter identity")
        adapter_identity = IBKRHistoricalAdapterIdentity.from_json(adapter_payload)
        authority = authenticate_ibkr_foundation_for_r2(
            foundation_path=foundation_path,
            receipt_path=receipt_path,
            adapter_identity=adapter_identity,
            evidence_class=expected_evidence_class,
            holdout_target_source=holdout_target_source,
            promotion_path=promotion_path,
        )
        if authority.foundation_id != experiment.r1_bundle_id:
            raise ValueError("IBKR foundation differs from the experiment")
        if expected_run_kind != CONFIRMATORY_RUN_KIND:
            _validate_representative_ibkr_historical_v1(
                authority.semantic_inputs,
                experiment,
                expected_evidence_class=expected_evidence_class,
            )
    else:
        if promotion_path is not None:
            raise ValueError("R1 runtime authority cannot include a promotion")
        authority = await authenticate_r1_foundation_for_r2(
            root=research_root,
            bundle_path=foundation_path,
            receipt_path=receipt_path,
            clock=clock,
            evidence_class=expected_evidence_class,
            outcome_blind=True,
            holdout_target_source=holdout_target_source,
        )
        if representative_profile is not None:
            raise ValueError("representative profile is only valid for IBKR historical runs")
        if expected_run_kind != CONFIRMATORY_RUN_KIND:
            _validate_representative_capture_v4(authority.semantic_inputs, experiment)

    if authority.identity_json() != cast(dict[str, JsonValue], raw_authority):
        raise ValueError("OOF foundation authority differs from its descriptor")
    verify_exact_r1_bindings(authority.semantic_inputs, experiment)
    with TemporaryDirectory() as temporary:
        expected_root = Path(temporary) / "oof"
        build_oof_bundle(
            verified=authority.semantic_inputs,
            foundation_authority=authority,
            experiment=experiment,
            feature_manifest_paths=feature_paths,
            research_root=research_root,
            clock=clock,
            output=expected_root,
            representative_profile=representative_profile,
            run_kind=expected_run_kind,
            holdout_target_source=holdout_target_source,
            holdout_target_source_authority=(
                source_authority
                if isinstance(source_authority, R2HoldoutTargetSourceAuthority)
                else None
            ),
            holdout_target_source_path=holdout_target_source_path,
            experiment_path=experiment_path,
            runtime_provenance={
                field: cast(str, descriptor[field]) for field in _OOF_DESCRIPTOR_PROVENANCE_FIELDS
            },
        )
        if _tree_bytes(path.parent) != _tree_bytes(expected_root):
            raise ValueError("OOF bundle does not replay to the authenticated pipeline")
    return (
        authority.folds,
        authority.semantic_inputs.source_active_intervals,
        authority.semantic_inputs,
        authority.g2_feature_source,
    )


async def _replay_representative_oof_async(path: Path) -> None:
    await _replay_authority_oof_async(path)


async def _replay_confirmatory_oof_async(
    path: Path,
) -> tuple[
    FoldDataset,
    Mapping[str, tuple[tuple[datetime, datetime], ...]],
    R1FoundationBindings,
    ConfirmatoryG2FeatureSourceAuthority | None,
]:
    return await _replay_authority_oof_async(path, expected_run_kind=CONFIRMATORY_RUN_KIND)


def _replay_representative_oof(path: Path) -> None:
    asyncio.run(_replay_representative_oof_async(path))


def _replay_confirmatory_oof(
    path: Path,
) -> tuple[
    FoldDataset,
    Mapping[str, tuple[tuple[datetime, datetime], ...]],
    R1FoundationBindings,
    ConfirmatoryG2FeatureSourceAuthority | None,
]:
    return asyncio.run(_replay_confirmatory_oof_async(path))


def _replay_synthetic_oof(path: Path) -> None:
    bundle = verify_r2_oof_bundle(path)
    descriptor = _oof_child_payload(path, bundle, OOF_DESCRIPTOR_CONTRACT)
    if descriptor.get("run_kind") != "SYNTHETIC":
        return
    with TemporaryDirectory() as temporary:
        expected_root = Path(temporary) / "oof"
        persisted_provenance = {
            field: cast(str, descriptor[field]) for field in _OOF_DESCRIPTOR_PROVENANCE_FIELDS
        }
        if descriptor.get("representative_profile") == IBKR_HISTORICAL_PROFILE:
            _build_ibkr_synthetic_oof_from_fixture(
                expected_root, runtime_provenance=persisted_provenance
            )
        else:
            _build_synthetic_oof(expected_root, runtime_provenance=persisted_provenance)
        if _tree_bytes(path.parent) != _tree_bytes(expected_root):
            raise ValueError("synthetic OOF bundle does not replay to the authenticated pipeline")


def _oof_manifest_path(path: Path) -> Path:
    return _canonical_path(path, "R2 OOF manifest")


def _oof_receipt_output(manifest: Path, output: Path) -> Path:
    manifest_path = _oof_manifest_path(manifest)
    root = manifest_path.parent
    candidate = _canonical_path(
        output,
        "R2 OOF verification receipt",
        allow_missing=True,
    )
    if candidate == root or candidate.is_relative_to(root):
        raise ValueError("R2 OOF verification receipt must be outside the immutable closure")
    if candidate.exists() or candidate.is_symlink():
        raise FileExistsError(f"R2 OOF verification receipt already exists: {candidate}")
    return candidate


def _oof_numerical_identity(descriptor: Mapping[str, object]) -> str:
    numerical = {
        key: descriptor[key]
        for key in (
            "numpy_identity",
            "sklearn_identity",
            "ridge_solver",
            "ridge_tolerance",
            "ridge_max_iterations",
        )
    }
    return sha256(canonical_bytes(numerical)).hexdigest()


def _oof_foundation_authority(
    bundle: R2OofBundle, descriptor: Mapping[str, object]
) -> tuple[str, str, str | None]:
    raw = descriptor.get("foundation_authority")
    if raw is None:
        synthetic_verification = sha256(
            canonical_bytes({"synthetic_foundation_id": bundle.foundation_bundle_id})
        ).hexdigest()
        return bundle.foundation_bundle_id, synthetic_verification, None
    if not isinstance(raw, Mapping):
        raise ValueError("R2 OOF descriptor foundation authority is malformed")
    foundation_id = raw.get("foundation_id")
    verification_id = raw.get("verification_id")
    promotion_id = raw.get("promotion_id")
    if (
        not isinstance(foundation_id, str)
        or not isinstance(verification_id, str)
        or (promotion_id is not None and not isinstance(promotion_id, str))
    ):
        raise ValueError("R2 OOF descriptor foundation authority is incomplete")
    if foundation_id != bundle.foundation_bundle_id:
        raise ValueError("R2 OOF descriptor foundation authority differs from its bundle")
    return foundation_id, verification_id, promotion_id


def _build_oof_verification_receipt(
    manifest: Path, bundle: R2OofBundle, descriptor: Mapping[str, object]
) -> R2OofVerificationReceipt:
    manifest_path = _oof_manifest_path(manifest)
    manifest_bytes = manifest_path.read_bytes()
    payload = _load_selection(manifest_path)
    if manifest_bytes != canonical_bytes(payload):
        raise ValueError("R2 OOF manifest is not canonical")
    foundation_id, foundation_verification_id, foundation_promotion_id = _oof_foundation_authority(
        bundle, descriptor
    )
    completed_checks = (
        R2_CONFIRMATORY_OOF_COMPLETED_CHECKS
        if descriptor.get("run_kind") == CONFIRMATORY_RUN_KIND
        else R2_OOF_COMPLETED_CHECKS
    )
    return R2OofVerificationReceipt.create(
        oof_contract=bundle.CONTRACT,
        oof_schema_version=bundle.SCHEMA_VERSION,
        oof_id=bundle.oof_id,
        closure_id=bundle.closure_id,
        manifest_sha256=sha256(manifest_bytes).hexdigest(),
        experiment_semantic_id=bundle.experiment_configuration_id,
        foundation_semantic_id=foundation_id,
        foundation_verification_id=foundation_verification_id,
        foundation_promotion_id=foundation_promotion_id,
        source_class=bundle.source_class,
        evidence_class=bundle.evidence_class,
        verifier_contract=R2_OOF_VERIFIER_CONTRACT,
        verifier_version=R2_OOF_VERIFIER_VERSION,
        completed_checks=completed_checks,
        numerical_identity=_oof_numerical_identity(descriptor),
    )


def _oof_declared_references(bundle: R2OofBundle) -> tuple[ArtifactReference, ...]:
    return (
        *bundle.feature_children,
        *bundle.preprocessing_children,
        *bundle.fit_children,
        *bundle.forecast_manifests,
        *bundle.coverage_children,
        *bundle.evaluation_children,
        *((bundle.holdout_target_source,) if bundle.holdout_target_source is not None else ()),
    )


def _safe_oof_child(root: Path, reference: ArtifactReference) -> Path:
    root = root.resolve()
    candidate = root / reference.path
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise ValueError("R2 OOF child path escapes its closure") from error
    current = root
    for part in reference.path.split("/"):
        current /= part
        if current.is_symlink():
            raise ValueError("R2 OOF child path traverses a symlink")
    if not candidate.is_file():
        raise ValueError(f"R2 OOF child is missing or not a regular file: {reference.path}")
    return candidate


def _read_oof_consumed_json(root: Path, reference: ArtifactReference) -> dict[str, object]:
    candidate = _safe_oof_child(root, reference)
    encoded = candidate.read_bytes()
    if len(encoded) > 64 * 1024 * 1024:
        raise ValueError("R2 OOF child exceeds the size limit")
    if sha256(encoded).hexdigest() != reference.sha256:
        raise ValueError(f"R2 OOF child digest mismatch: {reference.path}")
    try:
        value = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"R2 OOF child is not valid JSON: {reference.path}") from error
    if not isinstance(value, dict):
        raise ValueError(f"R2 OOF child is not a JSON object: {reference.path}")
    payload = cast(dict[str, object], value)
    if encoded != canonical_bytes(payload):
        raise ValueError(f"R2 OOF child is not canonical: {reference.path}")
    if payload.get("contract") != reference.contract:
        raise ValueError(f"R2 OOF child contract mismatch: {reference.path}")
    if _canonical_payload_identity(reference.contract, payload) != reference.semantic_id:
        raise ValueError(f"R2 OOF child semantic identity mismatch: {reference.path}")
    return payload


def _authenticate_oof_closure(
    manifest: Path,
) -> tuple[R2OofBundle, dict[str, object]]:
    manifest_path = _oof_manifest_path(manifest)
    root = manifest_path.parent
    if root.is_symlink():
        raise ValueError("R2 OOF closure root must not be a symlink")
    manifest_payload = _load_selection(manifest_path)
    manifest_bytes = manifest_path.read_bytes()
    if manifest_bytes != canonical_bytes(manifest_payload):
        raise ValueError("R2 OOF manifest is not canonical")
    bundle = R2OofBundle.from_json(manifest_payload)
    allowed_paths = {"manifest.json"}
    descriptor: dict[str, object] | None = None
    descriptor_count = 0
    for reference in _oof_declared_references(bundle):
        allowed_paths.add(reference.path)
        if reference.contract == OOF_DESCRIPTOR_CONTRACT:
            descriptor_count += 1
            descriptor = _read_oof_consumed_json(root, reference)
        elif reference.contract == R2ForecastManifest.CONTRACT:
            forecast_payload = _read_oof_consumed_json(root, reference)
            forecast_manifest = R2ForecastManifest.from_json(forecast_payload)
            nested = forecast_manifest.forecast_child
            allowed_paths.add(nested.path)
            _safe_oof_child(root, nested)
        else:
            _safe_oof_child(root, reference)
    _reject_orphan_files(root, allowed_paths)
    if descriptor_count != 1 or descriptor is None:
        raise ValueError("R2 OOF closure must contain exactly one run descriptor")
    return bundle, descriptor


def _load_oof_verification_receipt(manifest: Path, receipt_path: Path) -> R2OofVerificationReceipt:
    manifest_path = _oof_manifest_path(manifest)
    root = manifest_path.parent
    receipt = _canonical_path(receipt_path, "R2 OOF verification receipt")
    candidate = receipt.resolve(strict=False)
    if candidate == root or candidate.is_relative_to(root):
        raise ValueError("R2 OOF verification receipt must be outside the immutable closure")
    encoded = receipt.read_bytes()
    if len(encoded) > 64 * 1024:
        raise ValueError("R2 OOF verification receipt exceeds the size limit")
    try:
        value = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("R2 OOF verification receipt is not valid JSON") from error
    if not isinstance(value, dict):
        raise ValueError("R2 OOF verification receipt must be a JSON object")
    if encoded != canonical_bytes(cast(dict[str, object], value)):
        raise ValueError("R2 OOF verification receipt is not canonical")
    return R2OofVerificationReceipt.from_json(value)


def _validate_oof_verification_receipt(
    manifest: Path,
    bundle: R2OofBundle,
    descriptor: Mapping[str, object],
    receipt: R2OofVerificationReceipt,
) -> None:
    manifest_path = _oof_manifest_path(manifest)
    manifest_bytes = manifest_path.read_bytes()
    foundation_id, foundation_verification_id, foundation_promotion_id = _oof_foundation_authority(
        bundle, descriptor
    )
    expected_checks = (
        R2_CONFIRMATORY_OOF_COMPLETED_CHECKS
        if descriptor.get("run_kind") == CONFIRMATORY_RUN_KIND
        else R2_OOF_COMPLETED_CHECKS
    )
    expected = {
        "oof_contract": bundle.CONTRACT,
        "oof_schema_version": bundle.SCHEMA_VERSION,
        "oof_id": bundle.oof_id,
        "closure_id": bundle.closure_id,
        "manifest_sha256": sha256(manifest_bytes).hexdigest(),
        "experiment_semantic_id": bundle.experiment_configuration_id,
        "foundation_semantic_id": foundation_id,
        "foundation_verification_id": foundation_verification_id,
        "foundation_promotion_id": foundation_promotion_id,
        "source_class": bundle.source_class,
        "evidence_class": bundle.evidence_class,
        "verifier_contract": R2_OOF_VERIFIER_CONTRACT,
        "verifier_version": R2_OOF_VERIFIER_VERSION,
        "completed_checks": expected_checks,
        "numerical_identity": _oof_numerical_identity(descriptor),
    }
    for field, value in expected.items():
        if getattr(receipt, field) != value:
            raise ValueError(f"R2 OOF verification receipt binding is not accepted: {field}")


def _replay_verified_oof(
    path: Path,
    bundle: R2OofBundle,
    descriptor: Mapping[str, object],
    source_authority: R2HoldoutTargetSourceAuthority | None,
    *,
    allow_confirmatory: bool,
) -> R2HoldoutTargetSourceAuthority | None:
    run_kind = descriptor.get("run_kind")
    if run_kind == "SYNTHETIC":
        _replay_synthetic_oof(path)
        return None
    if run_kind == CONFIRMATORY_RUN_KIND and not allow_confirmatory:
        raise ValueError("confirmatory OOF bundles require an authenticated F2 promotion")
    if (
        bundle.holdout_target_source is not None
        and bundle.holdout_target_source.contract == R2_HOLDOUT_SOURCE_BINDING_CONTRACT
        and source_authority is None
    ):
        source_authority = _oof_holdout_source_authority(path, bundle, descriptor)
    if run_kind == "REPRESENTATIVE":
        asyncio.run(
            _replay_authority_oof_async(
                path,
                authenticated_bundle=bundle,
                authenticated_source_authority=source_authority,
            )
        )
    elif run_kind == CONFIRMATORY_RUN_KIND:
        asyncio.run(
            _replay_authority_oof_async(
                path,
                expected_run_kind=CONFIRMATORY_RUN_KIND,
                authenticated_bundle=bundle,
                authenticated_source_authority=source_authority,
            )
        )
    else:
        raise ValueError("OOF descriptor has an unsupported run kind")
    return source_authority


def verify_r2_oof_semantics(path: Path, *, receipt_output: Path | None = None) -> R2OofBundle:
    """Replay one R2 OOF transformation and optionally issue its create-only receipt."""
    bundle, source_authority_value = _verify_r2_oof_bundle_with_source(path)
    source_authority = (
        source_authority_value
        if isinstance(source_authority_value, R2HoldoutTargetSourceAuthority)
        else None
    )
    descriptor = _oof_child_payload(path, bundle, OOF_DESCRIPTOR_CONTRACT)
    receipt_path = None if receipt_output is None else _oof_receipt_output(path, receipt_output)
    _replay_verified_oof(
        path,
        bundle,
        descriptor,
        source_authority,
        allow_confirmatory=True,
    )
    if receipt_path is not None:
        receipt = _build_oof_verification_receipt(path, bundle, descriptor)
        atomic_create(receipt_path, canonical_bytes(receipt.as_json()))
    return bundle


def _authenticate_r2_oof_with_receipt(
    path: Path, *, receipt: Path
) -> tuple[R2OofBundle, dict[str, object], R2OofVerificationReceipt]:
    bundle, descriptor = _authenticate_oof_closure(path)
    parsed = _load_oof_verification_receipt(path, receipt)
    _validate_oof_verification_receipt(path, bundle, descriptor, parsed)
    return bundle, descriptor, parsed


def _authenticate_r2_oof_with_descriptor(
    path: Path, *, receipt: Path
) -> tuple[R2OofBundle, dict[str, object]]:
    """Authenticate one OOF closure and return the consumed descriptor for its caller."""
    bundle, descriptor, _ = _authenticate_r2_oof_with_receipt(path, receipt=receipt)
    return bundle, descriptor


def authenticate_r2_oof(path: Path, *, receipt: Path) -> R2OofBundle:
    """Authenticate an R2 OOF closure and receipt without semantic replay."""
    bundle, _ = _authenticate_r2_oof_with_descriptor(path, receipt=receipt)
    return bundle


def verify_oof_bundle_with_source(
    path: Path,
) -> tuple[R2OofBundle, R2HoldoutTargetSourceAuthority | None]:
    """Verify and replay an OOF bundle, retaining its consumed source authority."""
    bundle, source_authority_value = _verify_r2_oof_bundle_with_source(path)
    source_authority = (
        source_authority_value
        if isinstance(source_authority_value, R2HoldoutTargetSourceAuthority)
        else None
    )
    descriptor = _oof_child_payload(path, bundle, OOF_DESCRIPTOR_CONTRACT)
    retained_authority = _replay_verified_oof(
        path,
        bundle,
        descriptor,
        source_authority,
        allow_confirmatory=False,
    )
    return bundle, retained_authority


def verify_oof_bundle(path: Path) -> R2OofBundle:
    """Verify an OOF envelope and replay the run's authenticated computation."""
    bundle, _source_authority = verify_oof_bundle_with_source(path)
    return bundle


def load_experiment_and_feature_paths(
    *,
    experiment_path: Path,
    feature_arguments: list[str],
) -> tuple[R2ExperimentConfig, dict[str, Path]]:
    experiment = load_r2_experiment(experiment_path)
    return experiment, parse_feature_manifest_arguments(feature_arguments)
