from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import NoReturn

import pytest

from qtrad.application.r2_holdout import (
    FinalTrainingRow,
    build_holdout_coverage,
    build_holdout_forecasts,
    evaluate_holdout,
    fit_final_ridge,
    materialise_r2_holdout_features,
    seal_holdout_forecasts,
)
from qtrad.domain.market_data import MarketDataSourceClass
from qtrad.domain.r2_evaluation import (
    ConfigurationDisposition,
    SelectionDecision,
    SelectionManifest,
)
from qtrad.domain.r2_holdout import (
    HOLDOUT_ACKNOWLEDGEMENT,
    FinalFitDisposition,
    HoldoutConclusion,
    HoldoutDirection,
    HoldoutScope,
    HoldoutTargetOpportunity,
    R2FinalFittingPolicy,
    R2HoldoutFeatureRow,
    R2HoldoutQuestion,
    R2HoldoutSelectionManifest,
)
from qtrad.domain.r2_readiness import EvidenceClass, ModelFamily
from qtrad.runtime.r2_holdout import (
    reveal_holdout,
    verify_holdout_bundle,
    verify_holdout_markers,
    verify_holdout_preparation,
    write_built_holdout_bundle,
    write_holdout_preparation,
)


def _id(value: str) -> str:
    return sha256(value.encode()).hexdigest()


NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _unexpected_evaluator(message: str) -> NoReturn:
    raise AssertionError(message)


def _policy() -> R2FinalFittingPolicy:
    return R2FinalFittingPolicy.create(
        pre_holdout_membership_policy="MATURE_BEFORE_HOLDOUT",
        maturity_purge_policy="TARGET_INTERVAL_PURGE",
        inner_validation_policy="CHRONOLOGICAL_TAIL",
        alpha_grid=(0.1, 1.0),
        alpha_tie_break_policy="LOSS_THEN_ALPHA",
        preprocessing_policy="TRAINING_MEDIAN_STANDARDISE",
        pooled_membership_policy="FIXED_UNIVERSE",
        pooled_weighting_policy="EQUAL_INSTRUMENT",
        instrument_intercept_policy="GLOBAL_INTERCEPT",
        solver_identity={"name": "numpy-ridge", "version": "1"},
        training_prediction_threshold=1e-10,
        failure_disposition_policy="RETAIN_EXPLICIT_FAILURE",
        runtime_identities={"application": "fixture"},
    )


def _selection() -> tuple[R2HoldoutSelectionManifest, R2HoldoutQuestion, tuple[str, str]]:
    zero = _id("zero")
    local = _id("local")
    decisions = (
        SelectionDecision(
            configuration_id=zero,
            disposition=ConfigurationDisposition.RETAINED_CONTROL,
            reason="fixed zero-return control",
            gates=(),
        ),
        SelectionDecision(
            configuration_id=local,
            disposition=ConfigurationDisposition.SELECTED_CANDIDATE,
            reason="fixture selected candidate",
            gates=(),
        ),
    )
    prior = SelectionManifest.create(
        experiment_configuration_id=_id("experiment"),
        evidence_class=EvidenceClass.IMPLEMENTATION,
        evaluation_report_id=_id("evaluation"),
        local_comparator_manifest_id=_id("local-comparator"),
        evaluated_configuration_ids=(zero, local),
        predeclared_comparators=(ModelFamily.ZERO_RETURN, ModelFamily.LOCAL_RIDGE),
        primary_metric="MSE",
        secondary_metrics=("RMSE",),
        acceptance_thresholds=(("minimum_support", 1.0),),
        decisions=decisions,
        selected_configuration_ids=(local,),
        holdout_comparator_configuration_ids=(zero, local),
        final_fitting_procedure="R2_G2_TYPED_FINAL_POLICY",
        holdout_range=(NOW + timedelta(days=1), NOW + timedelta(days=2)),
        application_image_identity="fixture-image",
        frozen_at=NOW,
        frozen_by="test",
    )
    question = R2HoldoutQuestion.create(
        question="Does the selected candidate improve the zero control?",
        candidate_configuration_id=local,
        comparator_configuration_id=zero,
        metric="MSE",
        support_policy="COMMON_ELIGIBLE",
        direction=HoldoutDirection.LOWER_IS_BETTER,
        threshold=0.0,
        minimum_support=1,
        minimum_coverage=1.0,
        conclusion_policy="THRESHOLD_OR_INCONCLUSIVE",
    )
    from qtrad.application.r2_holdout import freeze_holdout_selection

    selection = freeze_holdout_selection(
        prior_selection=prior,
        foundation_bundle_id=_id("foundation"),
        oof_bundle_id=_id("oof"),
        source_class=MarketDataSourceClass.IG_NATIVE_CAPTURE,
        evidence_class=EvidenceClass.IMPLEMENTATION,
        holdout_scope=HoldoutScope.DISPOSABLE_FIXTURE,
        final_fitting_policy=_policy(),
        questions=(question,),
        metric_policy={"metric": "MSE"},
        threshold_policy={"threshold": 0.0},
        runtime_identities={"application": "fixture"},
        frozen_metadata={"fixture": True},
        frozen_at=NOW,
        frozen_by="test",
    )
    return selection, question, (zero, local)


def _opportunities() -> tuple[HoldoutTargetOpportunity, ...]:
    result = []
    for index in range(2):
        decision = NOW + timedelta(days=1, hours=index)
        result.append(
            HoldoutTargetOpportunity.create(
                target_id=_id(f"holdout-target-{index}"),
                instrument_id=f"INSTRUMENT_{index}",
                decision_time=decision,
                target_horizon_seconds=900,
                feature_data_asof=decision - timedelta(minutes=1),
                latest_feature_bar_end=decision - timedelta(minutes=1),
                dependency_start=decision - timedelta(minutes=15),
                dependency_end=decision + timedelta(minutes=15),
            )
        )
    return tuple(result)


def _prepared(tmp_path: Path):
    selection, _question, configurations = _selection()
    opportunities = _opportunities()
    feature_schema_id = _id("feature-schema")
    features = materialise_r2_holdout_features(
        selection=selection,
        opportunities=opportunities,
        feature_schema_id=feature_schema_id,
        feature_set_id=_id("feature-set"),
        observation_dataset_id=_id("observations"),
        panel_dataset_id=_id("panel"),
        projection=lambda item: R2HoldoutFeatureRow.create(
            opportunity_id=item.opportunity_id,
            target_id=item.target_id,
            instrument_id=item.instrument_id,
            decision_time=item.decision_time,
            feature_cutoff=item.feature_data_asof,
            latest_feature_bar_end=item.latest_feature_bar_end,
            feature_schema_id=feature_schema_id,
            values=(1.0, 0.5),
        ),
    )
    training_rows = tuple(
        FinalTrainingRow(
            target_id=_id(f"training-{index}"),
            instrument_id="INSTRUMENT_0",
            decision_time=NOW - timedelta(days=2, hours=index),
            target_available_at=NOW - timedelta(days=1),
            features=(float(index), 1.0),
            target=float(index) / 10,
        )
        for index in range(4)
    )
    fits = tuple(
        fit_final_ridge(
            selection=selection,
            configuration_id=configuration_id,
            model_family=model_family,
            feature_dataset_id=features.dataset_id,
            feature_schema_id=feature_schema_id,
            training_rows=training_rows,
            policy=selection.final_fitting_policy,
        )
        for configuration_id, model_family in (
            (configurations[0], ModelFamily.ZERO_RETURN),
            (configurations[1], ModelFamily.LOCAL_RIDGE),
        )
    )
    forecasts = build_holdout_forecasts(
        selection=selection, feature_dataset=features, final_fits=fits
    )
    coverage = tuple(
        build_holdout_coverage(
            selection=selection,
            feature_dataset=features,
            final_fit=fit,
            forecast_dataset=forecast,
            opportunities=opportunities,
        )
        for fit in fits
        for forecast in forecasts
        if forecast.configuration_id == fit.configuration_id
    )
    seal = seal_holdout_forecasts(
        selection=selection,
        feature_dataset=features,
        final_fits=fits,
        forecast_datasets=forecasts,
        coverage_datasets=coverage,
        metric_policy={"metric": "MSE"},
        comparison_support={"rule": "COMMON_ELIGIBLE"},
        forecast_buckets={"source": "TRAINING_ONLY"},
        state_buckets={"source": "TRAINING_ONLY"},
        coverage_rules={"minimum": 1.0},
        prepared_at=NOW,
        prepared_by="test",
    )
    write_holdout_preparation(
        tmp_path,
        selection=selection,
        feature_dataset=features,
        final_fits={item.fit_id: item for item in fits},
        forecasts={item.dataset_id: item for item in forecasts},
        coverage={item.coverage_id: item for item in coverage},
        seal=seal,
    )
    return selection, opportunities, fits, forecasts, coverage, seal


def test_disposable_holdout_round_trip_and_reveal(tmp_path: Path) -> None:
    selection, opportunities, _, forecasts, coverage, seal = _prepared(tmp_path)
    assert verify_holdout_preparation(tmp_path).seal_id == seal.seal_id

    def evaluator(outcomes, opened):
        return evaluate_holdout(
            selection=selection,
            seal=seal,
            opened_marker=opened,
            forecast_datasets=forecasts,
            coverage_datasets=coverage,
            outcomes=outcomes,
        )

    evaluation, consumed = reveal_holdout(
        tmp_path,
        expected_selection_manifest_id=selection.manifest_id,
        expected_seal_id=seal.seal_id,
        acknowledgement=HOLDOUT_ACKNOWLEDGEMENT,
        opened_by="test",
        consumed_by="test",
        opened_at=NOW,
        consumed_at=NOW + timedelta(seconds=1),
        outcome_loader=lambda: {
            opportunities[0].target_id: 0.1,
            opportunities[1].target_id: 0.2,
        },
        evaluator=evaluator,
    )
    assert evaluation is not None
    assert consumed.outcome_accessed is True
    opened, replayed_consumed = verify_holdout_markers(tmp_path)
    assert opened.seal_id == seal.seal_id
    assert replayed_consumed.marker_id == consumed.marker_id
    assert evaluation.questions[0].conclusion in tuple(HoldoutConclusion)


def test_file_bundle_builder_replays_the_consumed_evidence(tmp_path: Path) -> None:
    selection, opportunities, _, forecasts, coverage, seal = _prepared(tmp_path)

    def evaluator(outcomes, opened):
        return evaluate_holdout(
            selection=selection,
            seal=seal,
            opened_marker=opened,
            forecast_datasets=forecasts,
            coverage_datasets=coverage,
            outcomes=outcomes,
        )

    evaluation, consumed = reveal_holdout(
        tmp_path,
        expected_selection_manifest_id=selection.manifest_id,
        expected_seal_id=seal.seal_id,
        acknowledgement=HOLDOUT_ACKNOWLEDGEMENT,
        opened_by="test",
        consumed_by="test",
        opened_at=NOW,
        consumed_at=NOW + timedelta(seconds=1),
        outcome_loader=lambda: {
            opportunities[0].target_id: 0.1,
            opportunities[1].target_id: 0.2,
        },
        evaluator=evaluator,
    )
    output = tmp_path / "bundle"
    bundle = write_built_holdout_bundle(tmp_path, output)
    assert bundle.bundle_id == verify_holdout_bundle(output).bundle_id
    assert bundle.evaluation.semantic_id == evaluation.evaluation_id
    assert consumed.evaluation_id == evaluation.evaluation_id


def test_failed_reveal_is_consumed_and_second_reveal_is_rejected(tmp_path: Path) -> None:
    selection, _, _, _, _, seal = _prepared(tmp_path)
    with pytest.raises(RuntimeError, match="fixture outcome failure"):
        reveal_holdout(
            tmp_path,
            expected_selection_manifest_id=selection.manifest_id,
            expected_seal_id=seal.seal_id,
            acknowledgement=HOLDOUT_ACKNOWLEDGEMENT,
            opened_by="test",
            consumed_by="test",
            opened_at=NOW,
            consumed_at=NOW + timedelta(seconds=1),
            outcome_loader=lambda: (_ for _ in ()).throw(RuntimeError("fixture outcome failure")),
            evaluator=lambda outcomes, opened: _unexpected_evaluator("evaluator must not run"),
        )
    assert (tmp_path / "opened.json").is_file()
    assert (tmp_path / "consumed.json").is_file()
    with pytest.raises(FileExistsError, match="consumed"):
        reveal_holdout(
            tmp_path,
            expected_selection_manifest_id=selection.manifest_id,
            expected_seal_id=seal.seal_id,
            acknowledgement=HOLDOUT_ACKNOWLEDGEMENT,
            opened_by="test",
            consumed_by="test",
            opened_at=NOW,
            consumed_at=NOW + timedelta(seconds=2),
            outcome_loader=lambda: {},
            evaluator=lambda outcomes, opened: _unexpected_evaluator("second reveal must not load"),
        )


def test_failed_configuration_and_unavailable_feature_remain_explicit() -> None:
    selection, _, configurations = _selection()
    opportunities = _opportunities()
    schema = _id("feature-schema")
    features = materialise_r2_holdout_features(
        selection=selection,
        opportunities=opportunities,
        feature_schema_id=schema,
        feature_set_id=_id("feature-set-2"),
        observation_dataset_id=_id("observations-2"),
        panel_dataset_id=_id("panel-2"),
        projection=lambda item: (
            None
            if item is opportunities[1]
            else R2HoldoutFeatureRow.create(
                opportunity_id=item.opportunity_id,
                target_id=item.target_id,
                instrument_id=item.instrument_id,
                decision_time=item.decision_time,
                feature_cutoff=item.feature_data_asof,
                latest_feature_bar_end=item.latest_feature_bar_end,
                feature_schema_id=schema,
                values=(1.0,),
            )
        ),
    )
    failed_fit = fit_final_ridge(
        selection=selection,
        configuration_id=configurations[0],
        model_family=ModelFamily.ZERO_RETURN,
        feature_dataset_id=features.dataset_id,
        feature_schema_id=schema,
        training_rows=(),
        policy=selection.final_fitting_policy,
        minimum_training_rows=2,
        forced_disposition=FinalFitDisposition.NUMERICAL_FAILURE,
        forced_failure_reason="fixture failed configuration",
    )
    forecasts = build_holdout_forecasts(
        selection=selection, feature_dataset=features, final_fits=(failed_fit,)
    )[0]
    coverage = build_holdout_coverage(
        selection=selection,
        feature_dataset=features,
        final_fit=failed_fit,
        forecast_dataset=forecasts,
        opportunities=opportunities,
    )
    dispositions = {item.opportunity_id: item.disposition for item in coverage.rows}
    assert dispositions[opportunities[0].opportunity_id].value == "FAILED_CONFIGURATION"
    assert dispositions[opportunities[1].opportunity_id].value == "UNAVAILABLE_FEATURE"
