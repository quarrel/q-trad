import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from qtrad.application.r2_readiness import evaluate_r2_readiness
from qtrad.domain.foundation import InstrumentRole, ReturnDisposition
from qtrad.domain.r2_readiness import (
    EligibilityDecision,
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
TARGETS = tuple(f"index:target-{index}" for index in range(1, 8))
QUALIFYING = TARGETS[:6]
CONTEXT = "index:volatility"
SHA = "a" * 64


def _decision(subject: str, state: FeatureEligibility) -> EligibilityDecision:
    return EligibilityDecision.create(
        subject=subject,
        state=state,
        evidence_start=START,
        evidence_end=START + timedelta(weeks=5),
        reason=f"pre-holdout fixture evidence for {subject}",
    )


def experiment() -> R2ExperimentConfig:
    instruments = (*TARGETS, CONTEXT)
    roles = {instrument: InstrumentRole.TARGET for instrument in TARGETS}
    roles[CONTEXT] = InstrumentRole.CONTEXT
    feature_decisions = {
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
    local = (
        FeatureFamily.LOCAL_RETURNS,
        FeatureFamily.TIME_AVAILABILITY,
        FeatureFamily.LOCAL_VOLATILITY_RANGE,
    )
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
        instrument_roles=roles,
        target_instrument_eligibility={
            instrument: _decision(
                instrument,
                (
                    FeatureEligibility.ELIGIBLE
                    if instrument in QUALIFYING
                    else FeatureEligibility.NOT_ELIGIBLE
                ),
            )
            for instrument in TARGETS
        },
        target_instruments=QUALIFYING,
        confirmatory_target_instruments=QUALIFYING,
        market_groups={
            instrument: f"GROUP-{index // 2}" for index, instrument in enumerate(QUALIFYING)
        },
        horizons=(timedelta(minutes=5), timedelta(minutes=15)),
        primary_horizon=timedelta(minutes=15),
        feature_sets=(
            FeatureSet("L0", local[:2]),
            FeatureSet("L1", local),
            FeatureSet("P0", local),
            FeatureSet("P1", (*local, FeatureFamily.POOLED_CROSS_ASSET)),
        ),
        feature_windows=(timedelta(minutes=1), timedelta(minutes=5)),
        feature_coverage_thresholds={family: 0.9 for family in FeatureFamily},
        feature_eligibility=feature_decisions,
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


def _folds(*, short_validation: bool = False, short_training: bool = False) -> tuple[object, ...]:
    training_start = START + (timedelta(days=1) if short_training else timedelta(0))
    folds: list[object] = []
    for index in range(3):
        validation_start = START + timedelta(weeks=6 + 2 * index)
        validation_end = validation_start + timedelta(
            days=13 if short_validation and index == 0 else 14
        )
        folds.append(
            SimpleNamespace(
                training_start=training_start,
                training_cutoff=START + timedelta(weeks=6 + 2 * index),
                validation_start=validation_start,
                validation_end=validation_end,
            )
        )
    return tuple(folds)


def _target_rows(
    folds: tuple[object, ...],
    *,
    failing_instrument: str | None = None,
    failing_block: str | None = None,
    sparse: bool = False,
) -> tuple[Any, ...]:
    first = cast(Any, folds[0])
    blocks = [("initial_training", first.training_start, first.training_cutoff)]
    blocks.extend(
        (f"validation_{index}", cast(Any, fold).validation_start, cast(Any, fold).validation_end)
        for index, fold in enumerate(folds, start=1)
    )
    blocks.append(("holdout", END - timedelta(weeks=4), END))
    rows: list[Any] = []
    for instrument in QUALIFYING:
        for block, start, _ in blocks:
            required_count = 100 if block == "initial_training" else 20
            count = required_count - 1 if sparse else required_count
            for offset in range(count):
                decision_time = start + timedelta(minutes=offset)
                invalid = instrument == failing_instrument and block == failing_block and offset < 3
                rows.append(
                    SimpleNamespace(
                        target_id=f"{instrument}:{block}:{offset}",
                        block=block,
                        instrument_id=instrument,
                        horizon=timedelta(minutes=15),
                        decision_time=decision_time,
                        target_start_time=decision_time,
                        target_end_time=decision_time + timedelta(minutes=15),
                        return_disposition=(
                            ReturnDisposition.MISSING_END if invalid else ReturnDisposition.VALID
                        ),
                    )
                )
    return tuple(rows)


def _verified(
    config: R2ExperimentConfig,
    *,
    folds: tuple[object, ...] | None = None,
    failing_instrument: str | None = None,
    failing_block: str | None = None,
    late_instrument: str | None = None,
    sparse: bool = False,
) -> Any:
    raw_folds = folds or _folds()
    rows = _target_rows(
        raw_folds,
        failing_instrument=failing_instrument,
        failing_block=failing_block,
        sparse=sparse,
    )
    selected_folds = tuple(
        SimpleNamespace(
            training_start=cast(Any, fold).training_start,
            training_cutoff=cast(Any, fold).training_cutoff,
            validation_start=cast(Any, fold).validation_start,
            validation_end=cast(Any, fold).validation_end,
            training_target_ids=tuple(
                row.target_id for row in rows if row.block == "initial_training"
            ),
            validation_target_ids=tuple(
                row.target_id for row in rows if row.block == f"validation_{index}"
            ),
        )
        for index, fold in enumerate(raw_folds, start=1)
    )
    active = {
        instrument: [
            [
                (
                    START + timedelta(weeks=1) if instrument == late_instrument else START
                ).isoformat(),
                END.isoformat(),
            ]
        ]
        for instrument in config.ordered_instruments
    }
    r1_config = SimpleNamespace(
        configuration_id=config.foundation_configuration_id,
        ordered_instruments=config.ordered_instruments,
        instrument_roles=config.instrument_roles,
        target_horizons=config.horizons,
        holdout_range=config.holdout_range,
        range_start=START,
        range_end=END,
    )
    return SimpleNamespace(
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
        targets=SimpleNamespace(
            dataset_id=config.target_dataset_id,
            rows=rows,
        ),
        folds=SimpleNamespace(dataset_id=config.fold_dataset_id, folds=selected_folds),
        forecasts=SimpleNamespace(),
        availability_evidence={"source_active_intervals": active},
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

    decision = _decision(FeatureFamily.LOCAL_RETURNS.value, FeatureEligibility.ELIGIBLE)
    with pytest.raises(ValueError, match="does not authenticate"):
        replace(decision, reason="tampered")


def test_vix_cannot_be_promoted_to_target() -> None:
    config = experiment()
    roles = dict(config.instrument_roles)
    roles[CONTEXT] = InstrumentRole.TARGET
    eligibility = dict(config.target_instrument_eligibility)
    eligibility[CONTEXT] = _decision(CONTEXT, FeatureEligibility.ELIGIBLE)
    with pytest.raises(ValueError, match="VIX"):
        replace(
            config,
            instrument_roles=roles,
            target_instrument_eligibility=eligibility,
            target_instruments=(*config.target_instruments, CONTEXT),
            confirmatory_target_instruments=(*config.confirmatory_target_instruments, CONTEXT),
        )


def test_numerical_decision_rejects_auto_solver() -> None:
    with pytest.raises(ValueError, match="deterministic lsqr"):
        replace(experiment(), ridge_solver="auto")


def test_six_target_three_group_fixture_passes_confirmatory_data_gate() -> None:
    config = experiment()
    report = evaluate_r2_readiness(_verified(config), config)

    assert report.software_contract_ready.value == "READY"
    assert report.representative_integration_ready.value == "NOT_READY"
    assert report.confirmatory_data_ready.value == "READY"
    assert report.inner_validation_rows_ready.value == "PARTIALLY_READY"
    assert report.confirmatory_oof_ready.value == "NOT_READY"
    assert len(report.coverage_matrix) == 6
    assert all(len(cells) == 5 for cells in report.coverage_matrix.values())


def test_per_cell_coverage_cannot_be_masked_by_aggregate_coverage() -> None:
    config = experiment()
    report = evaluate_r2_readiness(
        _verified(
            config,
            failing_instrument=QUALIFYING[0],
            failing_block="validation_2",
        ),
        config,
    )

    assert report.confirmatory_data_ready.value == "NOT_READY"
    cell = report.coverage_matrix[QUALIFYING[0]][2]
    assert cell.coverage == 0.85


def test_common_usable_history_not_nominal_range_controls_readiness() -> None:
    config = experiment()
    report = evaluate_r2_readiness(_verified(config, late_instrument=QUALIFYING[0]), config)

    assert report.confirmatory_data_ready.value == "NOT_READY"
    assert any("common evidence" in condition for condition in report.unmet_conditions)


def test_sparse_disjoint_activity_cannot_pass_from_first_and_last_timestamps() -> None:
    config = experiment()
    verified = _verified(config, sparse=True)
    block_starts = (
        START,
        START + timedelta(weeks=6),
        START + timedelta(weeks=8),
        START + timedelta(weeks=10),
        START + timedelta(weeks=12),
    )
    disjoint = [
        [start.isoformat(), (start + timedelta(hours=2)).isoformat()] for start in block_starts
    ]
    disjoint.append([(END - timedelta(minutes=1)).isoformat(), END.isoformat()])
    for instrument in QUALIFYING:
        verified.availability_evidence["source_active_intervals"][instrument] = disjoint

    report = evaluate_r2_readiness(verified, config)

    assert report.confirmatory_data_ready.value == "NOT_READY"
    assert any("minimum_training_rows" in condition for condition in report.unmet_conditions)
    assert any(
        "minimum_outer_validation_rows" in condition for condition in report.unmet_conditions
    )


@pytest.mark.parametrize(
    ("folds", "message"),
    [
        (_folds(short_validation=True), "validation intervals"),
        (_folds(short_training=True), "initial training"),
    ],
)
def test_declared_fold_durations_are_enforced(folds: tuple[object, ...], message: str) -> None:
    config = experiment()
    report = evaluate_r2_readiness(_verified(config, folds=folds), config)

    assert report.confirmatory_data_ready.value == "NOT_READY"
    assert any(message in condition for condition in report.unmet_conditions)


def test_inactive_intervals_are_excluded_but_active_missing_targets_reduce_coverage() -> None:
    config = experiment()
    verified = _verified(config)
    extra_inactive = SimpleNamespace(
        target_id="inactive-gap-target",
        block="initial_training",
        instrument_id=QUALIFYING[0],
        horizon=timedelta(minutes=15),
        decision_time=START + timedelta(minutes=45),
        target_start_time=START + timedelta(minutes=45),
        target_end_time=START + timedelta(minutes=60),
        return_disposition=ReturnDisposition.MISSING_END,
    )
    verified.targets.rows += (extra_inactive,)
    verified.availability_evidence["source_active_intervals"][QUALIFYING[0]] = [
        [START.isoformat(), (START + timedelta(minutes=30)).isoformat()],
        [(START + timedelta(minutes=60)).isoformat(), END.isoformat()],
    ]
    ready = evaluate_r2_readiness(verified, config)
    missing = evaluate_r2_readiness(
        _verified(
            config,
            failing_instrument=QUALIFYING[0],
            failing_block="holdout",
        ),
        config,
    )

    assert ready.confirmatory_data_ready.value == "READY"
    assert missing.confirmatory_data_ready.value == "NOT_READY"


def test_required_feature_cannot_be_not_eligible_and_evidence_cannot_enter_holdout() -> None:
    config = experiment()
    decisions = dict(config.feature_eligibility)
    decisions[FeatureFamily.LOCAL_RETURNS] = _decision(
        FeatureFamily.LOCAL_RETURNS.value, FeatureEligibility.NOT_ELIGIBLE
    )
    with pytest.raises(ValueError, match="core R2 feature"):
        replace(config, feature_eligibility=decisions)

    contaminated = EligibilityDecision.create(
        subject=FeatureFamily.LOCAL_RETURNS.value,
        state=FeatureEligibility.ELIGIBLE,
        evidence_start=START,
        evidence_end=config.holdout_range[0] + timedelta(minutes=1),
        reason="fixture evidence enters holdout",
    )
    decisions[FeatureFamily.LOCAL_RETURNS] = contaminated
    with pytest.raises(ValueError, match="before the holdout"):
        replace(config, feature_eligibility=decisions)


def test_qualifying_subset_preserves_wider_r1_target_universe() -> None:
    config = experiment()

    assert len(config.target_instrument_eligibility) == 7
    assert config.target_instruments == QUALIFYING
    assert config.confirmatory_target_instruments == QUALIFYING
    assert (
        config.target_instrument_eligibility[TARGETS[-1]].state is FeatureEligibility.NOT_ELIGIBLE
    )
