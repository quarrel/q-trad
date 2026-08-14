"""Application construction for the fixed IBKR historical R2 profile."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import timedelta
from hashlib import sha256
from types import SimpleNamespace
from typing import cast

from qtrad.application.ibkr_foundation import IBKRFoundationBuild
from qtrad.application.r2_features import R2FoundationInputs
from qtrad.domain.events import JsonValue, to_json_value
from qtrad.domain.foundation import InstrumentRole, PanelDataset, TargetDataset
from qtrad.domain.foundation_bundle import AVAILABILITY_EVIDENCE_CONTRACT, FoundationBundle
from qtrad.domain.ibkr_foundation import (
    VerifiedIbkrFoundationPromotion,
    has_verified_ibkr_foundation_promotion_provenance,
)
from qtrad.domain.market_data import MarketDataSourceClass
from qtrad.domain.r2_ibkr_historical import (
    IBKR_HISTORICAL_EVIDENCE,
    IBKR_HISTORICAL_FEATURE_SETS,
    IBKR_HISTORICAL_FEATURE_WINDOWS,
    IBKR_HISTORICAL_GROUPS,
    IBKR_HISTORICAL_HORIZON,
    IBKR_HISTORICAL_MINIMUM_INNER_VALIDATION_ROWS,
    IBKR_HISTORICAL_MINIMUM_OUTER_VALIDATION_ROWS,
    IBKR_HISTORICAL_MINIMUM_TRAINING_ROWS,
    IBKR_HISTORICAL_SOURCE,
    IBKR_HISTORICAL_TARGETS,
    IBKRHistoricalAdapterIdentity,
    validate_ibkr_historical_profile,
)
from qtrad.domain.r2_readiness import (
    EligibilityDecision,
    EvidenceClass,
    FeatureEligibility,
    FeatureFamily,
    ModelFamily,
    R2ExperimentConfig,
)
from qtrad.domain.research import ObservationDataset
from qtrad.domain.time import require_utc

_REPRESENTATIVE_REASON = "fixed IBKR historical implementation profile"


def build_ibkr_historical_experiment(
    foundation: IBKRFoundationBuild,
    *,
    foundation_bundle_id: str,
    adapter_identity: IBKRHistoricalAdapterIdentity,
    evidence_class: EvidenceClass = IBKR_HISTORICAL_EVIDENCE,
    promotion_authority: VerifiedIbkrFoundationPromotion | None = None,
) -> R2ExperimentConfig:
    """Build the profile from a verified Stage 8 foundation and persisted adapter identity."""

    if evidence_class is EvidenceClass.CONFIRMATORY:
        if (
            promotion_authority is None
            or not has_verified_ibkr_foundation_promotion_provenance(promotion_authority)
            or promotion_authority.foundation_bundle_id != foundation_bundle_id
        ):
            raise ValueError(
                "confirmatory IBKR historical work requires the exact Stage 8 promotion attestation"
            )
    elif promotion_authority is not None:
        raise ValueError("Stage 8 promotion authority is valid only for confirmatory IBKR work")
    configuration = foundation.configuration
    if adapter_identity.foundation_bundle_id != foundation_bundle_id:
        raise ValueError("IBKR adapter identity does not bind the verified foundation")
    target_instruments = tuple(
        instrument
        for instrument in configuration.ordered_instruments
        if InstrumentRole(configuration.instrument_roles[instrument]) is InstrumentRole.TARGET
    )
    if set(target_instruments) != set(IBKR_HISTORICAL_TARGETS):
        raise ValueError("verified IBKR foundation target subset is not the fixed six")
    if any(
        InstrumentRole(configuration.instrument_roles[instrument]) is not InstrumentRole.CONTEXT
        for instrument in configuration.ordered_instruments
        if instrument not in IBKR_HISTORICAL_TARGETS
    ):
        raise ValueError("verified IBKR foundation non-target instruments must remain CONTEXT")
    if IBKR_HISTORICAL_HORIZON not in configuration.target_horizons:
        raise ValueError("verified IBKR foundation does not configure the 15-minute horizon")
    total_range = configuration.range_end - configuration.range_start
    expected_holdout_start = configuration.range_start + total_range * 0.8
    if (
        configuration.holdout_range[1] != configuration.range_end
        or configuration.holdout_range[0] != expected_holdout_start
    ):
        raise ValueError("verified IBKR foundation holdout is not the final 20 percent")
    if not foundation.folds.folds or len(foundation.folds.folds) != 3:
        raise ValueError("verified IBKR foundation must contain exactly three chronological folds")
    require_utc(configuration.holdout_range[0], "IBKR historical holdout start")
    require_utc(configuration.holdout_range[1], "IBKR historical holdout end")
    if not foundation_bundle_id:
        raise ValueError("verified foundation identity is required")

    evidence_start = configuration.range_start - timedelta(days=1)
    evidence_end = configuration.holdout_range[0] - timedelta(microseconds=1)

    def eligibility(subject: str, state: FeatureEligibility) -> EligibilityDecision:
        return EligibilityDecision.create(
            subject=subject,
            state=state,
            evidence_start=evidence_start,
            evidence_end=evidence_end,
            reason=_REPRESENTATIVE_REASON,
        )

    feature_eligibility = {
        family: eligibility(
            family.value,
            (
                FeatureEligibility.NOT_ELIGIBLE
                if family in {FeatureFamily.SPREAD, FeatureFamily.QUOTE_IMBALANCE}
                else FeatureEligibility.ELIGIBLE
            ),
        )
        for family in FeatureFamily
    }
    target_eligibility = {
        instrument: eligibility(instrument, FeatureEligibility.ELIGIBLE)
        for instrument in target_instruments
    }
    experiment = R2ExperimentConfig(
        name="r2-ibkr-historical-v1",
        schema_version=2,
        r1_bundle_id=foundation_bundle_id,
        observation_dataset_id=foundation.observations.dataset_id,
        foundation_configuration_id=configuration.configuration_id,
        panel_dataset_id=foundation.panel.dataset_id,
        target_dataset_id=foundation.targets.dataset_id,
        fold_dataset_id=foundation.folds.dataset_id,
        r1_application_version=adapter_identity.application_identity,
        r1_image_identity=adapter_identity.image_identity,
        ordered_instruments=configuration.ordered_instruments,
        instrument_roles=dict(configuration.instrument_roles),
        target_instrument_eligibility=target_eligibility,
        target_instruments=target_instruments,
        confirmatory_target_instruments=target_instruments,
        market_groups=IBKR_HISTORICAL_GROUPS,
        source_adapter_identity=adapter_identity.as_json(),
        horizons=(IBKR_HISTORICAL_HORIZON,),
        primary_horizon=IBKR_HISTORICAL_HORIZON,
        feature_sets=IBKR_HISTORICAL_FEATURE_SETS,
        feature_windows=IBKR_HISTORICAL_FEATURE_WINDOWS,
        feature_coverage_thresholds={family: 0.0 for family in FeatureFamily},
        feature_eligibility=feature_eligibility,
        preprocessing_policy="TRAINING_MEDIAN_STANDARDISE_V1",
        alpha_grid=(0.01, 0.1, 1.0, 10.0),
        inner_validation_policy="CHRONOLOGICAL_TAIL_PURGED_V1",
        ridge_solver="lsqr",
        ridge_tolerance=1e-8,
        ridge_max_iterations=10_000,
        pooled_weighting_policy="EQUAL_INSTRUMENT_TOTAL_WEIGHT_MEAN_ONE",
        minimum_training_rows=IBKR_HISTORICAL_MINIMUM_TRAINING_ROWS,
        minimum_inner_validation_rows=IBKR_HISTORICAL_MINIMUM_INNER_VALIDATION_ROWS,
        minimum_outer_validation_rows=IBKR_HISTORICAL_MINIMUM_OUTER_VALIDATION_ROWS,
        metric_policy="R2_METRICS_V1",
        forecast_bucket_policy="TRAINING_QUANTILES_V1",
        state_bucket_policy="TRAINING_THRESHOLDS_V1",
        model_selection_policy="OOF_PRIMARY_MSE_V1",
        acceptance_thresholds={
            "maximum_best_instrument_contribution": 1.0,
            "maximum_best_period_contribution": 1.0,
            "maximum_primary_mse_degradation": 0.0,
            "minimum_common_support": 0.0,
            "minimum_improving_fold_proportion": 0.0,
            "minimum_improving_instrument_proportion": 0.0,
        },
        holdout_range=configuration.holdout_range,
        numeric_replay_relative_tolerance=1e-10,
        numeric_replay_absolute_tolerance=1e-12,
        evidence_class=evidence_class,
        model_families=tuple(ModelFamily),
        market_data_source_class=IBKR_HISTORICAL_SOURCE,
    )
    validate_ibkr_historical_profile(experiment, expected_evidence_class=evidence_class)
    return experiment


def ibkr_availability_evidence(foundation: IBKRFoundationBuild) -> dict[str, JsonValue]:
    """Convert verified Stage 8 session/gap evidence to the R2 availability contract."""

    configuration = foundation.configuration
    intervals = {
        instrument: [
            [start.isoformat(), end.isoformat()]
            for start, end in foundation.active_intervals.get(instrument, ())
        ]
        for instrument in configuration.ordered_instruments
    }
    return cast(
        dict[str, JsonValue],
        {
            "availability_delay_report": {
                "source_class": IBKR_HISTORICAL_SOURCE.value,
                "policy": "PROVIDER_HISTORY_AVAILABLE_AT",
            },
            "revision_delay_report": {
                "policy": "PROVIDER_HISTORY_FROZEN_REVISION",
            },
            "data_gaps": [dict(gap) for gap in foundation.provider_gaps],
            "source_active_intervals": intervals,
            "lineage_summary": {
                "source_class": IBKR_HISTORICAL_SOURCE.value,
                "provider_history_dataset_id": foundation.provider_history.dataset_sha256,
            },
            "observation_bounds": {
                "interval_start": configuration.range_start.isoformat(),
                "interval_end": configuration.range_end.isoformat(),
            },
        },
    )


def _availability_dataset_id(observation_dataset_id: str, evidence: Mapping[str, object]) -> str:
    payload = {
        "contract": AVAILABILITY_EVIDENCE_CONTRACT,
        "observation_dataset_id": observation_dataset_id,
        "evidence": to_json_value(dict(evidence)),
    }
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build_ibkr_r2_foundation_inputs(
    foundation: IBKRFoundationBuild,
    *,
    foundation_bundle_id: str,
    adapter_identity: IBKRHistoricalAdapterIdentity,
) -> R2FoundationInputs:
    """Adapt verified Stage 8 children to the existing R2 feature/OOF pipeline."""

    build_ibkr_historical_experiment(
        foundation,
        foundation_bundle_id=foundation_bundle_id,
        adapter_identity=adapter_identity,
    )
    evidence = ibkr_availability_evidence(foundation)
    availability_id = _availability_dataset_id(foundation.observations.dataset_id, evidence)
    configuration = foundation.configuration

    def child(dataset_id: str) -> SimpleNamespace:
        return SimpleNamespace(dataset_id=dataset_id)

    bundle = SimpleNamespace(
        bundle_id=foundation_bundle_id,
        foundation_id=foundation_bundle_id,
        market_data_source_class=MarketDataSourceClass.IBKR_HISTORICAL_RESEARCH,
        ordered_instruments=configuration.ordered_instruments,
        range_start=configuration.range_start,
        range_end=configuration.range_end,
        build_summary={
            "application_version": adapter_identity.application_identity,
            "image_identity": adapter_identity.image_identity,
            "source_adapter_identity": adapter_identity.as_json(),
        },
        configuration=child(configuration.configuration_id),
        observations=child(foundation.observations.dataset_id),
        availability=child(availability_id),
        panel=child(foundation.panel.dataset_id),
        targets=child(foundation.targets.dataset_id),
        folds=child(foundation.folds.dataset_id),
    )
    observations = cast(ObservationDataset, foundation.observations)
    if (
        observations.configuration.get("availability_basis")
        != configuration.availability_basis.value
    ):
        raise ValueError("verified IBKR observation availability basis is inconsistent")
    return R2FoundationInputs(
        bundle=cast(FoundationBundle, bundle),
        configuration=configuration,
        observations=observations,
        panel=cast(PanelDataset, foundation.panel),
        targets=cast(TargetDataset, foundation.targets),
        folds=foundation.folds,
        availability_evidence=evidence,
    )


__all__ = [
    "build_ibkr_historical_experiment",
    "build_ibkr_r2_foundation_inputs",
    "ibkr_availability_evidence",
]
