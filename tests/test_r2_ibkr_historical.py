"""Contracts and adapter tests for the IBKR historical R2 profile."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast

import pytest

from qtrad.application.ibkr_foundation import IBKRFoundationBuild
from qtrad.application.r2_ibkr_historical import (
    build_ibkr_historical_experiment,
    build_ibkr_r2_foundation_inputs,
)
from qtrad.domain.foundation import AvailabilityBasis, InstrumentRole
from qtrad.domain.market_data import MarketDataSourceClass
from qtrad.domain.r2_bundles import ArtifactReference
from qtrad.domain.r2_ibkr_bundles import R2IbkrHistoricalSoftwareVerificationBundle
from qtrad.domain.r2_ibkr_historical import (
    IBKR_HISTORICAL_FEATURE_SETS,
    IBKR_HISTORICAL_FEATURE_WINDOWS,
    IBKR_HISTORICAL_GROUPS,
    IBKR_HISTORICAL_HORIZON,
    IBKR_HISTORICAL_PROFILE,
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
from qtrad.runtime.r2_bundles import canonical_bytes

START = datetime(2026, 1, 1, tzinfo=UTC)
END = datetime(2026, 1, 31, tzinfo=UTC)
HOLDOUT = (datetime(2026, 1, 25, tzinfo=UTC), END)
FOUNDATION_ID = "a" * 64
CONTEXT_INSTRUMENT = "index:volatility"
ORDERED_UNIVERSE = (*IBKR_HISTORICAL_TARGETS, CONTEXT_INSTRUMENT)
ADAPTER = IBKRHistoricalAdapterIdentity.create(
    foundation_bundle_id=FOUNDATION_ID,
    application_identity="qtrad-test+git:" + "1" * 40,
    image_identity="sha256:" + "2" * 64,
)


def _decision(subject: str, state: FeatureEligibility) -> EligibilityDecision:
    return EligibilityDecision.create(
        subject=subject,
        state=state,
        evidence_start=START,
        evidence_end=HOLDOUT[0] - timedelta(minutes=1),
        reason="fixed IBKR historical profile fixture",
    )


def _experiment() -> R2ExperimentConfig:
    feature_eligibility = {
        family: _decision(
            family.value,
            (
                FeatureEligibility.NOT_ELIGIBLE
                if family in {FeatureFamily.SPREAD, FeatureFamily.QUOTE_IMBALANCE}
                else FeatureEligibility.ELIGIBLE
            ),
        )
        for family in FeatureFamily
    }
    return R2ExperimentConfig(
        name="r2-ibkr-historical-fixture",
        schema_version=2,
        r1_bundle_id=FOUNDATION_ID,
        observation_dataset_id="b" * 64,
        foundation_configuration_id="c" * 64,
        panel_dataset_id="d" * 64,
        target_dataset_id="e" * 64,
        fold_dataset_id="f" * 64,
        r1_application_version=ADAPTER.application_identity,
        r1_image_identity=ADAPTER.image_identity,
        ordered_instruments=ORDERED_UNIVERSE,
        instrument_roles={
            **{instrument: InstrumentRole.TARGET for instrument in IBKR_HISTORICAL_TARGETS},
            CONTEXT_INSTRUMENT: InstrumentRole.CONTEXT,
        },
        target_instrument_eligibility={
            instrument: _decision(instrument, FeatureEligibility.ELIGIBLE)
            for instrument in IBKR_HISTORICAL_TARGETS
        },
        target_instruments=IBKR_HISTORICAL_TARGETS,
        confirmatory_target_instruments=IBKR_HISTORICAL_TARGETS,
        market_groups=IBKR_HISTORICAL_GROUPS,
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
        minimum_training_rows=100,
        minimum_inner_validation_rows=20,
        minimum_outer_validation_rows=20,
        metric_policy="R2_METRICS_V1",
        forecast_bucket_policy="TRAINING_QUANTILES_V1",
        state_bucket_policy="TRAINING_THRESHOLDS_V1",
        model_selection_policy="OOF_PRIMARY_MSE_V1",
        acceptance_thresholds={"minimum_common_support": 0.0},
        holdout_range=HOLDOUT,
        numeric_replay_relative_tolerance=1e-10,
        numeric_replay_absolute_tolerance=1e-12,
        evidence_class=EvidenceClass.IMPLEMENTATION,
        model_families=tuple(ModelFamily),
        market_data_source_class=IBKR_HISTORICAL_SOURCE,
        source_adapter_identity=ADAPTER.as_json(),
    )


def _foundation() -> SimpleNamespace:
    configuration = SimpleNamespace(
        ordered_instruments=ORDERED_UNIVERSE,
        instrument_roles={
            **{instrument: InstrumentRole.TARGET for instrument in IBKR_HISTORICAL_TARGETS},
            CONTEXT_INSTRUMENT: InstrumentRole.CONTEXT,
        },
        target_horizons=(IBKR_HISTORICAL_HORIZON,),
        holdout_range=HOLDOUT,
        range_start=START,
        range_end=END,
        configuration_id="c" * 64,
        availability_basis=AvailabilityBasis.RECEIVED_AT,
    )
    observations = SimpleNamespace(
        dataset_id="b" * 64,
        configuration={"availability_basis": AvailabilityBasis.RECEIVED_AT.value},
        selection_policies={},
    )
    folds = SimpleNamespace(dataset_id="f" * 64, folds=(object(), object(), object()))
    return SimpleNamespace(
        configuration=configuration,
        observations=observations,
        panel=SimpleNamespace(dataset_id="d" * 64),
        targets=SimpleNamespace(dataset_id="e" * 64),
        folds=folds,
        provider_history=SimpleNamespace(dataset_sha256="9" * 64),
        active_intervals={instrument: ((START, END),) for instrument in ORDERED_UNIVERSE},
        provider_gaps=(),
    )


def _reference(name: str) -> ArtifactReference:
    return ArtifactReference(
        contract=f"qtrad-test-{name}-v1",
        semantic_id=hashlib.sha256(f"{name}-semantic".encode()).hexdigest(),
        path=f"{name}/manifest.json",
        sha256=hashlib.sha256(f"{name}-bytes".encode()).hexdigest(),
    )


def _software_bundle() -> R2IbkrHistoricalSoftwareVerificationBundle:
    return R2IbkrHistoricalSoftwareVerificationBundle.create(
        market_data_source_class=IBKR_HISTORICAL_SOURCE,
        representative_profile=IBKR_HISTORICAL_PROFILE,
        synthetic_oof_bundle=_reference("synthetic-oof"),
        representative_oof_bundle=_reference("representative-oof"),
        synthetic_selection=_reference("synthetic-selection"),
        representative_selection=_reference("representative-selection"),
        application_identity="qtrad-test-application",
        python_identity="3.13",
        numpy_identity="2",
        sklearn_identity="1",
        representative_integration_ready="READY",
        evidence_disposition="IMPLEMENTATION_EVIDENCE_ONLY",
        research_disposition="RESEARCH_EVIDENCE_PENDING",
    )


def test_ibkr_profile_preserves_stage8_universe_and_fixed_target_subset() -> None:
    experiment = _experiment()
    validate_ibkr_historical_profile(experiment)

    assert experiment.ordered_instruments == ORDERED_UNIVERSE
    assert experiment.instrument_roles[CONTEXT_INSTRUMENT] is InstrumentRole.CONTEXT
    assert experiment.target_instruments == IBKR_HISTORICAL_TARGETS
    assert experiment.confirmatory_target_instruments == IBKR_HISTORICAL_TARGETS


def test_ibkr_profile_authenticates_the_persisted_adapter_identity() -> None:
    experiment = _experiment()
    validate_ibkr_historical_profile(experiment)

    identity = dict(cast(dict[str, object], experiment.source_adapter_identity))
    identity["application_identity"] = "tampered"
    with pytest.raises(ValueError, match=r"authenticate|differs"):
        validate_ibkr_historical_profile(replace(experiment, source_adapter_identity=identity))


def test_ibkr_builder_binds_stage8_children_and_availability() -> None:
    foundation = cast(IBKRFoundationBuild, _foundation())
    experiment = build_ibkr_historical_experiment(
        foundation,
        foundation_bundle_id=FOUNDATION_ID,
        adapter_identity=ADAPTER,
    )
    assert experiment.r1_bundle_id == FOUNDATION_ID
    assert experiment.ordered_instruments == ORDERED_UNIVERSE
    assert experiment.instrument_roles[CONTEXT_INSTRUMENT] is InstrumentRole.CONTEXT
    assert experiment.target_instruments == IBKR_HISTORICAL_TARGETS
    assert experiment.source_adapter_identity == ADAPTER.as_json()

    inputs = build_ibkr_r2_foundation_inputs(
        foundation,
        foundation_bundle_id=FOUNDATION_ID,
        adapter_identity=ADAPTER,
    )
    assert inputs.bundle.bundle_id == FOUNDATION_ID
    assert inputs.bundle.market_data_source_class is IBKR_HISTORICAL_SOURCE
    assert inputs.bundle.ordered_instruments == ORDERED_UNIVERSE
    assert inputs.bundle.build_summary["source_adapter_identity"] == ADAPTER.as_json()
    assert set(
        cast(dict[str, object], inputs.availability_evidence["source_active_intervals"])
    ) == set(ORDERED_UNIVERSE)


def test_ibkr_builder_rejects_non_fixed_foundation_targets() -> None:
    foundation = cast(IBKRFoundationBuild, _foundation())
    cast(SimpleNamespace, foundation.configuration).instrument_roles[CONTEXT_INSTRUMENT] = (
        InstrumentRole.TARGET
    )
    with pytest.raises(ValueError, match=r"fixed six|CONTEXT"):
        build_ibkr_historical_experiment(
            foundation,
            foundation_bundle_id=FOUNDATION_ID,
            adapter_identity=ADAPTER,
        )


def test_ibkr_profile_is_strictly_source_and_policy_bound() -> None:
    experiment = _experiment()

    with pytest.raises(ValueError, match="IBKR_HISTORICAL_RESEARCH"):
        validate_ibkr_historical_profile(
            replace(experiment, market_data_source_class=MarketDataSourceClass.IG_NATIVE_CAPTURE)
        )
    with pytest.raises(ValueError, match="feature windows"):
        validate_ibkr_historical_profile(
            replace(experiment, feature_windows=(timedelta(minutes=5),))
        )
    with pytest.raises(ValueError, match="group assignments"):
        validate_ibkr_historical_profile(
            replace(experiment, market_groups={**IBKR_HISTORICAL_GROUPS, "fx:aud-usd": "INDEX"})
        )


def test_ibkr_software_bundle_round_trip_authenticates_all_dispositions() -> None:
    bundle = _software_bundle()
    restored = R2IbkrHistoricalSoftwareVerificationBundle.from_json(bundle.as_json())
    assert restored == bundle
    assert json.loads(canonical_bytes(bundle.as_json())) == bundle.as_json()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("representative_profile", "IG_CAPTURE_V4", "profile"),
        ("market_data_source_class", "IG_NATIVE_CAPTURE", "IBKR_HISTORICAL_RESEARCH"),
        ("evidence_disposition", "RESEARCH_EVIDENCE", "implementation-only"),
        ("research_disposition", "READY", "research disposition"),
    ),
)
def test_ibkr_software_bundle_rejects_source_profile_and_disposition_mutations(
    field: str, value: str, message: str
) -> None:
    payload = _software_bundle().as_json()
    payload[field] = value
    with pytest.raises((ValueError, TypeError), match=message):
        R2IbkrHistoricalSoftwareVerificationBundle.from_json(payload)


def test_ibkr_software_bundle_rejects_identity_path_and_orphan_mutations() -> None:
    bundle = _software_bundle()
    payload = bundle.as_json()
    payload["bundle_id"] = "f" * 64
    with pytest.raises(ValueError, match="bundle ID"):
        R2IbkrHistoricalSoftwareVerificationBundle.from_json(payload)

    with pytest.raises(ValueError, match="unsafe path"):
        ArtifactReference(
            contract="child-v1",
            semantic_id="a" * 64,
            path="../outside.json",
            sha256="b" * 64,
        )

    extra = bundle.as_json()
    extra["unexpected"] = True
    with pytest.raises(ValueError, match="unknown or missing fields"):
        R2IbkrHistoricalSoftwareVerificationBundle.from_json(extra)
