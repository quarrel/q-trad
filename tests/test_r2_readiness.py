import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from qtrad.application.r2_readiness import evaluate_r2_readiness
from qtrad.domain.foundation import InstrumentRole
from qtrad.domain.r2_readiness import (
    EvidenceClass,
    FeatureEligibility,
    FeatureFamily,
    FeatureSet,
    ModelFamily,
    R2ExperimentConfig,
)
from qtrad.runtime.r2_readiness import decode_r2_experiment, load_r2_experiment

START = datetime(2026, 1, 1, tzinfo=UTC)
END = START + timedelta(weeks=16)
SHA = "a" * 64


def experiment() -> R2ExperimentConfig:
    instruments = ("fx:aud-usd", "index:volatility")
    families = tuple(FeatureFamily)
    return R2ExperimentConfig(
        name="r2-a-fixture",
        schema_version=1,
        r1_bundle_id=SHA,
        observation_dataset_id="b" * 64,
        foundation_configuration_id="c" * 64,
        panel_dataset_id="d" * 64,
        target_dataset_id="e" * 64,
        fold_dataset_id="f" * 64,
        r1_application_version="0.1.0",
        r1_image_identity="qtrad@sha256:" + "1" * 64,
        ordered_instruments=instruments,
        instrument_roles={
            "fx:aud-usd": InstrumentRole.TARGET,
            "index:volatility": InstrumentRole.CONTEXT,
        },
        target_instruments=("fx:aud-usd",),
        market_groups={"fx:aud-usd": "FX"},
        horizons=(timedelta(minutes=5), timedelta(minutes=15)),
        primary_horizon=timedelta(minutes=15),
        feature_sets=(
            FeatureSet(
                name="L0",
                families=(FeatureFamily.LOCAL_RETURNS, FeatureFamily.TIME_AVAILABILITY),
            ),
        ),
        feature_windows=(timedelta(minutes=1), timedelta(minutes=5)),
        feature_coverage_thresholds={family: 0.9 for family in families},
        feature_eligibility={
            family: (
                FeatureEligibility.PENDING
                if family is FeatureFamily.QUOTE_IMBALANCE
                else FeatureEligibility.ELIGIBLE
            )
            for family in families
        },
        feature_eligibility_evidence_ids={
            family: f"pre-holdout:{family.value}" for family in families
        },
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
        acceptance_thresholds={"minimum_common_support": 0.9},
        holdout_range=(END - timedelta(weeks=4), END),
        numeric_replay_relative_tolerance=1e-10,
        numeric_replay_absolute_tolerance=1e-12,
        evidence_class=EvidenceClass.IMPLEMENTATION,
        model_families=tuple(ModelFamily),
    )


def test_experiment_round_trip_preserves_semantic_identity(tmp_path: Path) -> None:
    original = experiment()
    path = tmp_path / "experiment.json"
    path.write_text(json.dumps(original.as_json()), encoding="utf-8")

    loaded = load_r2_experiment(path)

    assert loaded == original
    assert loaded.configuration_id == original.configuration_id


def test_unknown_field_and_semantic_tampering_fail_closed() -> None:
    payload = experiment().as_json()
    payload["unexpected"] = True
    with pytest.raises(ValueError, match="unknown or missing"):
        decode_r2_experiment(payload)

    with pytest.raises(ValueError, match="R1 bundle ID"):
        replace(experiment(), r1_bundle_id="changed")


def test_vix_cannot_be_promoted_to_target() -> None:
    with pytest.raises(ValueError, match="VIX"):
        replace(
            experiment(),
            instrument_roles={
                "fx:aud-usd": InstrumentRole.TARGET,
                "index:volatility": InstrumentRole.TARGET,
            },
            target_instruments=("fx:aud-usd", "index:volatility"),
            market_groups={"fx:aud-usd": "FX", "index:volatility": "VOLATILITY"},
        )


def test_numerical_decision_rejects_auto_solver() -> None:
    with pytest.raises(ValueError, match="deterministic lsqr"):
        replace(experiment(), ridge_solver="auto")


def test_software_readiness_is_independent_of_short_representative_history() -> None:
    config = experiment()
    r1_config = SimpleNamespace(
        configuration_id=config.foundation_configuration_id,
        ordered_instruments=config.ordered_instruments,
        instrument_roles=config.instrument_roles,
        target_horizons=config.horizons,
        holdout_range=config.holdout_range,
        range_start=START,
        range_end=START + timedelta(weeks=2),
    )
    target = SimpleNamespace(
        horizon=timedelta(minutes=15),
        instrument_id="fx:aud-usd",
        return_disposition="VALID",
    )
    verified = SimpleNamespace(
        bundle=SimpleNamespace(
            bundle_id=config.r1_bundle_id,
            build_summary={
                "application_version": config.r1_application_version,
                "image_identity": config.r1_image_identity,
            },
        ),
        configuration=r1_config,
        observations=SimpleNamespace(dataset_id=config.observation_dataset_id),
        panel=SimpleNamespace(dataset_id=config.panel_dataset_id),
        targets=SimpleNamespace(dataset_id=config.target_dataset_id, rows=(target,)),
        folds=SimpleNamespace(dataset_id=config.fold_dataset_id, folds=(object(),)),
    )

    report = evaluate_r2_readiness(cast(Any, verified), config)

    assert report.software_contract_ready.value == "READY"
    assert report.representative_integration_ready.value == "READY"
    assert report.confirmatory_oof_ready.value == "NOT_READY"
    assert report.locked_holdout_ready.value == "NOT_READY"
    assert report.feature_family_states[FeatureFamily.QUOTE_IMBALANCE].value == "PARTIALLY_READY"
