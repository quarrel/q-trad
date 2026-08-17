"""Fixed source-specific R2 representative profile for IBKR historical research."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
from typing import cast

from qtrad.domain.events import JsonValue
from qtrad.domain.foundation import InstrumentRole
from qtrad.domain.market_data import MarketDataSourceClass
from qtrad.domain.r2_readiness import (
    EvidenceClass,
    FeatureEligibility,
    FeatureFamily,
    FeatureSet,
    ModelFamily,
    R2ExperimentConfig,
)

IBKR_HISTORICAL_PROFILE = "IBKR_HISTORICAL_V1"
IBKR_HISTORICAL_PROFILE_ARGUMENT = "ibkr-historical-v1"
IBKR_HISTORICAL_SOURCE = MarketDataSourceClass.IBKR_HISTORICAL_RESEARCH
IBKR_HISTORICAL_EVIDENCE = EvidenceClass.IMPLEMENTATION
IBKR_HISTORICAL_ADAPTER_IDENTITY_CONTRACT = "qtrad-ibkr-historical-adapter-identity-v1"
IBKR_HISTORICAL_ADAPTER_IDENTITY_SCHEMA_VERSION = 1

IBKR_HISTORICAL_TARGETS: tuple[str, ...] = (
    "fx:aud-usd",
    "fx:eur-usd",
    "index:australia-200",
    "index:us-500",
    "commodity:spot-gold",
    "commodity:us-crude",
)

IBKR_HISTORICAL_GROUPS: dict[str, str] = {
    "fx:aud-usd": "FX",
    "fx:eur-usd": "FX",
    "index:australia-200": "INDEX",
    "index:us-500": "INDEX",
    "commodity:spot-gold": "COMMODITY",
    "commodity:us-crude": "COMMODITY",
}


@dataclass(frozen=True, slots=True)
class IBKRHistoricalAdapterIdentity:
    """Persisted source-specific identity used to adapt a verified Stage 8 bundle."""

    foundation_bundle_id: str
    application_identity: str
    image_identity: str
    adapter_identity_id: str

    CONTRACT = IBKR_HISTORICAL_ADAPTER_IDENTITY_CONTRACT
    SCHEMA_VERSION = IBKR_HISTORICAL_ADAPTER_IDENTITY_SCHEMA_VERSION

    @classmethod
    def create(
        cls,
        *,
        foundation_bundle_id: str,
        application_identity: str,
        image_identity: str,
    ) -> IBKRHistoricalAdapterIdentity:
        payload = {
            "contract": cls.CONTRACT,
            "schema_version": cls.SCHEMA_VERSION,
            "foundation_bundle_id": foundation_bundle_id,
            "application_identity": application_identity,
            "image_identity": image_identity,
        }
        return cls(
            foundation_bundle_id=_required_text(foundation_bundle_id, "foundation bundle ID"),
            application_identity=_required_text(application_identity, "application identity"),
            image_identity=_required_text(image_identity, "image identity"),
            adapter_identity_id=_semantic_id(payload),
        )

    @classmethod
    def from_json(cls, value: object) -> IBKRHistoricalAdapterIdentity:
        if not isinstance(value, Mapping):
            raise ValueError("IBKR adapter identity must be an object")
        raw = cast(Mapping[str, object], value)
        expected = {
            "contract",
            "schema_version",
            "foundation_bundle_id",
            "application_identity",
            "image_identity",
            "adapter_identity_id",
        }
        if set(raw) != expected:
            raise ValueError("IBKR adapter identity has unknown or missing fields")
        identity = cls.create(
            foundation_bundle_id=_required_text(
                raw["foundation_bundle_id"], "foundation bundle ID"
            ),
            application_identity=_required_text(
                raw["application_identity"], "application identity"
            ),
            image_identity=_required_text(raw["image_identity"], "image identity"),
        )
        if raw["contract"] != cls.CONTRACT or raw["schema_version"] != cls.SCHEMA_VERSION:
            raise ValueError("IBKR adapter identity contract is unsupported")
        if raw["adapter_identity_id"] != identity.adapter_identity_id:
            raise ValueError("IBKR adapter identity does not authenticate its content")
        return identity

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "contract": self.CONTRACT,
            "schema_version": self.SCHEMA_VERSION,
            "foundation_bundle_id": self.foundation_bundle_id,
            "application_identity": self.application_identity,
            "image_identity": self.image_identity,
            "adapter_identity_id": self.adapter_identity_id,
        }


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"IBKR adapter {field} must be non-empty")
    return value


def _semantic_id(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


IBKR_HISTORICAL_FEATURE_SETS: tuple[FeatureSet, ...] = (
    FeatureSet("L0", (FeatureFamily.LOCAL_RETURNS, FeatureFamily.TIME_AVAILABILITY)),
    FeatureSet(
        "L1",
        (
            FeatureFamily.LOCAL_RETURNS,
            FeatureFamily.TIME_AVAILABILITY,
            FeatureFamily.LOCAL_VOLATILITY_RANGE,
        ),
    ),
    FeatureSet(
        "P0",
        (
            FeatureFamily.LOCAL_RETURNS,
            FeatureFamily.TIME_AVAILABILITY,
            FeatureFamily.LOCAL_VOLATILITY_RANGE,
        ),
    ),
    FeatureSet(
        "P1",
        (
            FeatureFamily.LOCAL_RETURNS,
            FeatureFamily.TIME_AVAILABILITY,
            FeatureFamily.LOCAL_VOLATILITY_RANGE,
            FeatureFamily.POOLED_CROSS_ASSET,
        ),
    ),
)

IBKR_HISTORICAL_HORIZON = timedelta(minutes=15)
IBKR_HISTORICAL_ALPHA_GRID = (0.01, 0.1, 1.0, 10.0)
IBKR_HISTORICAL_FEATURE_WINDOWS = (timedelta(minutes=1), timedelta(minutes=5))
IBKR_HISTORICAL_MINIMUM_TRAINING_ROWS = 100
IBKR_HISTORICAL_MINIMUM_INNER_VALIDATION_ROWS = 20
IBKR_HISTORICAL_MINIMUM_OUTER_VALIDATION_ROWS = 20
IBKR_HISTORICAL_MINIMUM_COMMON_SUPPORT = 0.9


def validate_ibkr_historical_profile(
    experiment: R2ExperimentConfig,
    *,
    expected_evidence_class: EvidenceClass = IBKR_HISTORICAL_EVIDENCE,
) -> None:
    """Reject an experiment that is not the fixed IBKR historical profile."""

    if experiment.market_data_source_class is not IBKR_HISTORICAL_SOURCE:
        raise ValueError("IBKR historical profile requires IBKR_HISTORICAL_RESEARCH")
    if experiment.evidence_class is not expected_evidence_class:
        raise ValueError("IBKR historical profile has an unexpected evidence classification")
    if experiment.source_adapter_identity is None:
        raise ValueError("IBKR historical profile requires a persisted adapter identity")
    adapter_identity = IBKRHistoricalAdapterIdentity.from_json(experiment.source_adapter_identity)
    if adapter_identity.foundation_bundle_id != experiment.r1_bundle_id:
        raise ValueError("IBKR adapter identity differs from the R1 foundation")
    if adapter_identity.application_identity != experiment.r1_application_version:
        raise ValueError("IBKR adapter identity differs from the R1 application")
    if adapter_identity.image_identity != experiment.r1_image_identity:
        raise ValueError("IBKR adapter identity differs from the R1 image")

    ordered = experiment.ordered_instruments
    roles = {
        instrument: InstrumentRole(experiment.instrument_roles[instrument])
        for instrument in ordered
    }
    target_instruments = tuple(
        instrument for instrument in ordered if roles[instrument] is InstrumentRole.TARGET
    )
    if set(target_instruments) != set(IBKR_HISTORICAL_TARGETS):
        raise ValueError("IBKR historical profile target subset is not the fixed six")
    if any(
        role is not InstrumentRole.CONTEXT
        for instrument, role in roles.items()
        if instrument not in IBKR_HISTORICAL_TARGETS
    ):
        raise ValueError(
            "IBKR historical profile requires non-target instruments to remain CONTEXT"
        )
    if experiment.target_instruments != target_instruments:
        raise ValueError(
            "IBKR historical profile target order does not retain the foundation order"
        )
    if set(experiment.confirmatory_target_instruments) != set(IBKR_HISTORICAL_TARGETS):
        raise ValueError("IBKR historical profile confirmatory universe is not the fixed six")
    if dict(experiment.market_groups) != IBKR_HISTORICAL_GROUPS:
        raise ValueError("IBKR historical profile group assignments differ from the fixed pairs")
    if experiment.horizons != (IBKR_HISTORICAL_HORIZON,):
        raise ValueError("IBKR historical profile must use only the 15-minute horizon")
    if experiment.primary_horizon != IBKR_HISTORICAL_HORIZON:
        raise ValueError("IBKR historical profile primary horizon is not 15 minutes")
    if experiment.feature_sets != IBKR_HISTORICAL_FEATURE_SETS:
        raise ValueError("IBKR historical profile feature ladder differs from L0/L1/P0/P1")
    if experiment.feature_windows != (
        timedelta(minutes=1),
        timedelta(minutes=5),
    ):
        raise ValueError("IBKR historical profile feature windows differ from the fixed policy")
    eligible_families = {
        FeatureFamily.LOCAL_RETURNS,
        FeatureFamily.TIME_AVAILABILITY,
        FeatureFamily.LOCAL_VOLATILITY_RANGE,
        FeatureFamily.POOLED_CROSS_ASSET,
    }
    for family in eligible_families:
        if experiment.feature_eligibility[family].state is not FeatureEligibility.ELIGIBLE:
            raise ValueError(f"{family.value} must be eligible for IBKR historical research")
    for family in (FeatureFamily.SPREAD, FeatureFamily.QUOTE_IMBALANCE):
        if experiment.feature_eligibility[family].state is not FeatureEligibility.NOT_ELIGIBLE:
            raise ValueError(f"{family.value} must remain ineligible for IBKR historical research")
    if experiment.alpha_grid != IBKR_HISTORICAL_ALPHA_GRID:
        raise ValueError("IBKR historical profile alpha grid differs from the fixed policy")
    if experiment.ridge_solver != "lsqr" or experiment.ridge_tolerance != 1e-8:
        raise ValueError(
            "IBKR historical profile Ridge numerical policy differs from the fixed policy"
        )
    if experiment.ridge_max_iterations != 10_000:
        raise ValueError(
            "IBKR historical profile Ridge iteration policy differs from the fixed policy"
        )
    if experiment.pooled_weighting_policy != "EQUAL_INSTRUMENT_TOTAL_WEIGHT_MEAN_ONE":
        raise ValueError(
            "IBKR historical profile pooled weighting policy differs from the fixed policy"
        )
    if (
        experiment.acceptance_thresholds.get("minimum_common_support")
        != IBKR_HISTORICAL_MINIMUM_COMMON_SUPPORT
    ):
        raise ValueError(
            "IBKR historical profile common-support threshold differs from the fixed policy"
        )
    if (
        experiment.minimum_training_rows != IBKR_HISTORICAL_MINIMUM_TRAINING_ROWS
        or experiment.minimum_inner_validation_rows != IBKR_HISTORICAL_MINIMUM_INNER_VALIDATION_ROWS
        or experiment.minimum_outer_validation_rows != IBKR_HISTORICAL_MINIMUM_OUTER_VALIDATION_ROWS
    ):
        raise ValueError("IBKR historical profile minimum row policy differs from the fixed policy")
    if experiment.model_families != tuple(ModelFamily):
        raise ValueError("IBKR historical profile must retain the complete baseline model family")


__all__ = [
    "IBKR_HISTORICAL_ADAPTER_IDENTITY_CONTRACT",
    "IBKR_HISTORICAL_ADAPTER_IDENTITY_SCHEMA_VERSION",
    "IBKR_HISTORICAL_ALPHA_GRID",
    "IBKR_HISTORICAL_EVIDENCE",
    "IBKR_HISTORICAL_FEATURE_SETS",
    "IBKR_HISTORICAL_FEATURE_WINDOWS",
    "IBKR_HISTORICAL_GROUPS",
    "IBKR_HISTORICAL_HORIZON",
    "IBKR_HISTORICAL_MINIMUM_COMMON_SUPPORT",
    "IBKR_HISTORICAL_MINIMUM_INNER_VALIDATION_ROWS",
    "IBKR_HISTORICAL_MINIMUM_OUTER_VALIDATION_ROWS",
    "IBKR_HISTORICAL_MINIMUM_TRAINING_ROWS",
    "IBKR_HISTORICAL_PROFILE",
    "IBKR_HISTORICAL_PROFILE_ARGUMENT",
    "IBKR_HISTORICAL_SOURCE",
    "IBKR_HISTORICAL_TARGETS",
    "IBKRHistoricalAdapterIdentity",
    "validate_ibkr_historical_profile",
]
