from __future__ import annotations

import json
import shutil
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import NoReturn

import pytest

from qtrad.application.r2_holdout import (
    build_holdout_coverage,
    build_holdout_forecasts,
    evaluate_holdout,
    fit_final_ridge,
    materialise_r2_holdout_features,
    seal_holdout_forecasts,
)
from qtrad.domain.foundation import (
    ExcursionDisposition,
    ReturnDisposition,
    TargetDataset,
    TargetRow,
    target_identity,
)
from qtrad.domain.market_data import MarketDataSourceClass, PriceBasis
from qtrad.domain.r2_evaluation import (
    ConfigurationDisposition,
    SelectionDecision,
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
    HOLDOUT_ACKNOWLEDGEMENT,
    FinalFitDisposition,
    HoldoutConclusion,
    HoldoutDirection,
    HoldoutOpportunityDisposition,
    HoldoutScope,
    HoldoutTargetOpportunity,
    R2FinalFit,
    R2FinalFittingPolicy,
    R2HoldoutFeatureRow,
    R2HoldoutOpenedMarker,
    R2HoldoutOpportunityRegistry,
    R2HoldoutQuestion,
    R2HoldoutSelectionManifest,
    R2HoldoutTargetProjection,
)
from qtrad.domain.r2_readiness import EvidenceClass, FeatureFamily, ModelFamily
from qtrad.runtime.r2_holdout import (
    _target_dataset_payload,
    prepare_holdout_from_files,
    reveal_holdout,
    reveal_holdout_from_files,
    verify_holdout_bundle,
    verify_holdout_evaluation,
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


def _training_feature_spec() -> tuple[tuple[FeatureDefinition, ...], str, str]:
    schema = (
        FeatureDefinition("training_return", FeatureFamily.LOCAL_RETURNS),
        FeatureDefinition("training_volatility", FeatureFamily.LOCAL_VOLATILITY_RANGE),
    )
    feature_name = "fixture-training-features"
    return (
        schema,
        feature_name,
        feature_set_id(
            _id("experiment"),
            feature_name,
            schema,
            MarketDataSourceClass.IG_NATIVE_CAPTURE,
        ),
    )


def _training_feature_set_id() -> str:
    return _training_feature_spec()[2]


def _selection(
    *, bind_target_dataset: bool = True, include_noneligible: bool = False
) -> tuple[R2HoldoutSelectionManifest, R2HoldoutQuestion, tuple[str, str]]:
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
        primary_metric="INSTRUMENT_BALANCED_COMMON_SUPPORT_MSE",
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
        metric="INSTRUMENT_BALANCED_COMMON_SUPPORT_MSE",
        support_policy="COMMON_ELIGIBLE",
        direction=HoldoutDirection.LOWER_IS_BETTER,
        threshold=0.0,
        minimum_support=1,
        minimum_coverage=1.0,
        conclusion_policy="THRESHOLD_OR_INCONCLUSIVE",
    )
    from qtrad.application.r2_holdout import freeze_holdout_selection

    source_target_dataset = _target_dataset(include_noneligible=include_noneligible)
    pre_holdout_projection = R2HoldoutTargetProjection.create_from_source(
        source_target_dataset,
        holdout_start=NOW + timedelta(days=1),
        primary_horizon_seconds=900,
    )
    opportunity_registry = R2HoldoutOpportunityRegistry.create_from_source(
        source_target_dataset,
        holdout_range=(NOW + timedelta(days=1), NOW + timedelta(days=2)),
        primary_horizon_seconds=900,
        opportunities=_opportunities(include_noneligible=include_noneligible),
    )
    selection = freeze_holdout_selection(
        prior_selection=prior,
        foundation_bundle_id=_id("foundation"),
        oof_bundle_id=_id("oof"),
        source_class=MarketDataSourceClass.IG_NATIVE_CAPTURE,
        evidence_class=EvidenceClass.IMPLEMENTATION,
        holdout_scope=HoldoutScope.DISPOSABLE_FIXTURE,
        final_fitting_policy=_policy(),
        questions=(question,),
        metric_policy={"metric": "INSTRUMENT_BALANCED_COMMON_SUPPORT_MSE"},
        threshold_policy={"threshold": 0.0},
        runtime_identities={"application": "fixture"},
        frozen_metadata={"fixture": True},
        frozen_at=NOW,
        frozen_by="test",
        source_target_dataset=source_target_dataset,
        holdout_opportunity_registry=opportunity_registry,
        pre_holdout_projection=pre_holdout_projection,
        configuration_registry=tuple(
            sorted(
                (
                    (zero, ModelFamily.ZERO_RETURN, None, None, None),
                    (
                        local,
                        ModelFamily.LOCAL_RIDGE,
                        _training_feature_set_id(),
                        _training_feature_dataset(
                            include_noneligible=include_noneligible
                        ).dataset_id,
                        None,
                    ),
                ),
                key=lambda item: item[0],
            )
        ),
        evaluation_policy={
            "target_dataset_id": (
                source_target_dataset.dataset_id if bind_target_dataset else None
            ),
            "primary_horizon_seconds": 900,
            "pre_holdout_target_dataset_id": (
                pre_holdout_projection.projected_target_dataset.dataset_id
            ),
            "observation_dataset_id": _id("observations"),
            "panel_dataset_id": _id("panel"),
            "minimum_training_rows": 2,
            "minimum_inner_validation_rows": 1,
            "target_instruments": ["INSTRUMENT_0", "INSTRUMENT_1"],
            "seal_policy": {
                "metric_policy": {"metric": "INSTRUMENT_BALANCED_COMMON_SUPPORT_MSE"},
                "comparison_support": {"rule": "COMMON_ELIGIBLE"},
                "forecast_buckets": {"source": "TRAINING_ONLY"},
                "state_buckets": {"source": "TRAINING_ONLY"},
                "coverage_rules": {"minimum": 1.0},
            },
        },
    )
    return selection, question, (zero, local)


def _opportunities(*, include_noneligible: bool = False) -> tuple[HoldoutTargetOpportunity, ...]:
    result = []
    for index in range(2):
        decision = NOW + timedelta(days=1, hours=index)
        result.append(
            HoldoutTargetOpportunity.create(
                target_id=target_identity(
                    instrument_id=f"INSTRUMENT_{index}",
                    decision_time=decision,
                    horizon=timedelta(seconds=900),
                    target_basis=PriceBasis.MID,
                    target_revision_policy="FIXTURE_V1",
                ),
                instrument_id=f"INSTRUMENT_{index}",
                decision_time=decision,
                target_horizon_seconds=900,
                feature_data_asof=decision - timedelta(minutes=1),
                latest_feature_bar_end=decision - timedelta(minutes=1),
                dependency_start=decision - timedelta(minutes=15),
                dependency_end=decision + timedelta(minutes=15),
            )
        )
    if include_noneligible:
        decision = NOW + timedelta(days=1, hours=2)
        result.append(
            HoldoutTargetOpportunity.create(
                target_id=target_identity(
                    instrument_id="INSTRUMENT_2",
                    decision_time=decision,
                    horizon=timedelta(seconds=900),
                    target_basis=PriceBasis.MID,
                    target_revision_policy="FIXTURE_V1",
                ),
                instrument_id="INSTRUMENT_2",
                decision_time=decision,
                target_horizon_seconds=900,
                feature_data_asof=decision - timedelta(minutes=1),
                latest_feature_bar_end=decision - timedelta(minutes=1),
                dependency_start=decision - timedelta(minutes=15),
                dependency_end=decision + timedelta(minutes=15),
                disposition=HoldoutOpportunityDisposition.GAP,
            )
        )
    return tuple(result)


def _target_dataset(*, include_noneligible: bool = False) -> TargetDataset:
    opportunities = _opportunities(include_noneligible=include_noneligible)
    rows = tuple(
        TargetRow(
            instrument_id=opportunity.instrument_id,
            decision_time=opportunity.decision_time,
            horizon=timedelta(seconds=opportunity.target_horizon_seconds),
            target_basis=PriceBasis.MID,
            target_revision_policy="FIXTURE_V1",
            target_start_time=opportunity.decision_time,
            target_end_time=opportunity.decision_time
            + timedelta(seconds=opportunity.target_horizon_seconds),
            target_freeze_at=opportunity.decision_time
            + timedelta(seconds=opportunity.target_horizon_seconds),
            target_available_at=opportunity.decision_time
            + timedelta(seconds=opportunity.target_horizon_seconds),
            label_start_close=None,
            label_end_close=None,
            log_return=(
                None
                if opportunity.disposition is HoldoutOpportunityDisposition.GAP
                else 0.1 + index * 0.1
            ),
            return_disposition=(
                ReturnDisposition.UNAVAILABLE_BY_FREEZE
                if opportunity.disposition is HoldoutOpportunityDisposition.GAP
                else ReturnDisposition.VALID
            ),
            start_event_id=None,
            end_event_id=None,
            upper_log_excursion=None,
            lower_log_excursion=None,
            excursion_disposition=ExcursionDisposition.INCOMPLETE_PATH,
        )
        for index, opportunity in enumerate(opportunities)
    )
    training_rows = tuple(
        TargetRow(
            instrument_id=f"INSTRUMENT_{instrument_index}",
            decision_time=NOW - timedelta(days=2, hours=index),
            horizon=timedelta(seconds=900),
            target_basis=PriceBasis.MID,
            target_revision_policy="FIXTURE_V1",
            target_start_time=NOW - timedelta(days=2, hours=index),
            target_end_time=NOW - timedelta(days=2, hours=index) + timedelta(seconds=900),
            target_freeze_at=NOW - timedelta(days=2, hours=index) + timedelta(seconds=900),
            target_available_at=NOW - timedelta(days=2, hours=index) + timedelta(seconds=900),
            label_start_close=None,
            label_end_close=None,
            log_return=(index + instrument_index) / 10,
            return_disposition=ReturnDisposition.VALID,
            start_event_id=None,
            end_event_id=None,
            upper_log_excursion=None,
            lower_log_excursion=None,
            excursion_disposition=ExcursionDisposition.INCOMPLETE_PATH,
        )
        for instrument_index in range(2)
        for index in range(4)
    )
    return TargetDataset.create(
        (*rows, *training_rows),
        observation_dataset_id=_id("observations"),
        foundation_configuration_id=_id("foundation"),
    )


def _training_feature_dataset(*, include_noneligible: bool = False) -> R2FeatureDataset:
    schema, feature_name, training_feature_set_id = _training_feature_spec()
    experiment_id = _id("experiment")
    rows = tuple(
        RawFeatureRow(
            target_instrument_id=f"INSTRUMENT_{instrument_index}",
            decision_time=NOW - timedelta(days=2, hours=index),
            feature_data_asof=NOW - timedelta(days=2, hours=index) - timedelta(minutes=1),
            latest_feature_bar_end=NOW - timedelta(days=2, hours=index) - timedelta(minutes=1),
            feature_set_id=training_feature_set_id,
            values=(
                RawFeatureValue("training_return", float(index + instrument_index)),
                RawFeatureValue("training_volatility", 1.0),
            ),
        )
        for instrument_index in range(2)
        for index in range(4)
    )
    return R2FeatureDataset.create(
        rows,
        feature_schema=schema,
        feature_set_name=feature_name,
        feature_set_id=training_feature_set_id,
        observation_dataset_id=_id("observations"),
        panel_dataset_id=_id("panel"),
        target_dataset_id=_target_dataset(include_noneligible=include_noneligible).dataset_id,
        fold_dataset_id=_id("fold"),
        experiment_configuration_id=experiment_id,
        evidence_class=EvidenceClass.IMPLEMENTATION,
        market_data_source_class=MarketDataSourceClass.IG_NATIVE_CAPTURE,
    )


def _prepared(
    tmp_path: Path,
    *,
    forced_failure_configuration: str | None = None,
    bind_target_dataset: bool = True,
    unavailable_opportunity_id: str | None = None,
    include_noneligible: bool = False,
):
    selection, _question, configurations = _selection(
        bind_target_dataset=bind_target_dataset,
        include_noneligible=include_noneligible,
    )
    opportunities = _opportunities(include_noneligible=include_noneligible)
    feature_schema_id = _training_feature_dataset(
        include_noneligible=include_noneligible
    ).raw_feature_schema_id
    target_dataset = _target_dataset(include_noneligible=include_noneligible)
    features = materialise_r2_holdout_features(
        selection=selection,
        opportunities=opportunities,
        feature_schema_id=feature_schema_id,
        feature_set_id=_training_feature_set_id(),
        observation_dataset_id=_id("observations"),
        panel_dataset_id=_id("panel"),
        target_dataset_id=(target_dataset.dataset_id if bind_target_dataset else None),
        projection=lambda item: (
            None
            if item.opportunity_id == unavailable_opportunity_id
            else R2HoldoutFeatureRow.create(
                opportunity_id=item.opportunity_id,
                target_id=item.target_id,
                instrument_id=item.instrument_id,
                decision_time=item.decision_time,
                feature_cutoff=item.feature_data_asof,
                latest_feature_bar_end=item.latest_feature_bar_end,
                feature_schema_id=feature_schema_id,
                values=(1.0, 0.5),
            )
        ),
    )
    training_features = _training_feature_dataset(include_noneligible=include_noneligible)
    training_targets = target_dataset
    fits = tuple(
        fit_final_ridge(
            selection=selection,
            configuration_id=configurations[1],
            model_family=ModelFamily.LOCAL_RIDGE,
            target_instrument_id=target_instrument_id,
            feature_dataset_id=features.dataset_id,
            feature_schema_id=feature_schema_id,
            training_feature_dataset=training_features,
            training_target_dataset=training_targets,
            training_target_source_dataset_id=target_dataset.dataset_id,
            policy=selection.final_fitting_policy,
            forced_disposition=(
                FinalFitDisposition.NUMERICAL_FAILURE
                if forced_failure_configuration == configurations[1]
                else None
            ),
            forced_failure_reason=(
                "fixture forced replay failure"
                if forced_failure_configuration == configurations[1]
                else None
            ),
        )
        for target_instrument_id in ("INSTRUMENT_0", "INSTRUMENT_1")
    )
    forecasts = build_holdout_forecasts(
        selection=selection,
        feature_dataset=features,
        final_fits=fits,
        opportunities=opportunities,
    )
    fits_by_configuration: dict[str, tuple[R2FinalFit, ...]] = {}
    for fit in fits:
        fits_by_configuration[fit.configuration_id] = (
            *fits_by_configuration.get(fit.configuration_id, ()),
            fit,
        )
    coverage = tuple(
        build_holdout_coverage(
            selection=selection,
            feature_dataset=features,
            final_fit=None,
            final_fits=fits_by_configuration.get(forecast.configuration_id, ()),
            forecast_dataset=forecast,
            opportunities=opportunities,
        )
        for forecast in forecasts
    )
    seal = seal_holdout_forecasts(
        selection=selection,
        feature_dataset=features,
        final_fits=fits,
        forecast_datasets=forecasts,
        coverage_datasets=coverage,
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
        training_feature_datasets={training_features.dataset_id: training_features},
        training_target_datasets={training_targets.dataset_id: training_targets},
    )
    return selection, opportunities, fits, forecasts, coverage, seal


def test_disposable_holdout_round_trip_and_reveal(tmp_path: Path) -> None:
    selection, _opportunities, _, forecasts, coverage, seal = _prepared(tmp_path)
    target_dataset = _target_dataset()
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
        outcome_loader=lambda: target_dataset,
        evaluator=evaluator,
    )
    assert evaluation is not None
    assert consumed.outcome_accessed is True
    opened, replayed_consumed = verify_holdout_markers(tmp_path)
    assert opened.seal_id == seal.seal_id
    assert replayed_consumed.marker_id == consumed.marker_id
    assert evaluation.questions[0].conclusion in tuple(HoldoutConclusion)


def test_noneligible_gap_with_null_return_does_not_block_first_reveal(
    tmp_path: Path,
) -> None:
    selection, opportunities, _fits, forecasts, coverage, seal = _prepared(
        tmp_path,
        include_noneligible=True,
    )
    target_dataset = _target_dataset(include_noneligible=True)

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
        outcome_loader=lambda: target_dataset,
        evaluator=evaluator,
    )

    assert evaluation is not None
    assert consumed.outcome_accessed is True
    gap = next(
        item for item in opportunities if item.disposition is HoldoutOpportunityDisposition.GAP
    )
    assert any(row.opportunity_id == gap.opportunity_id for row in coverage[0].rows)
    assert all(row.target_id != gap.target_id for forecast in forecasts for row in forecast.rows)


def test_target_projection_rejects_a_different_source_dataset() -> None:
    source = _target_dataset()
    projection = R2HoldoutTargetProjection.create_from_source(
        source,
        holdout_start=NOW + timedelta(days=1),
        primary_horizon_seconds=900,
    )
    altered_rows = tuple(
        replace(row, log_return=0.95) if row.target_id == _opportunities()[0].target_id else row
        for row in source.rows
    )
    altered_source = TargetDataset.create(
        altered_rows,
        observation_dataset_id=source.observation_dataset_id,
        foundation_configuration_id=source.foundation_configuration_id,
    )

    with pytest.raises(ValueError, match="source"):
        projection.verify_source(altered_source)


def test_opportunity_registry_requires_complete_source_coverage_and_round_trips() -> None:
    source = _target_dataset()
    opportunities = _opportunities()
    with pytest.raises(ValueError, match="exactly cover"):
        R2HoldoutOpportunityRegistry.create_from_source(
            source,
            holdout_range=(NOW + timedelta(days=1), NOW + timedelta(days=2)),
            primary_horizon_seconds=900,
            opportunities=opportunities[:1],
        )

    registry = R2HoldoutOpportunityRegistry.create_from_source(
        source,
        holdout_range=(NOW + timedelta(days=1), NOW + timedelta(days=2)),
        primary_horizon_seconds=900,
        opportunities=opportunities,
    )
    restored = R2HoldoutOpportunityRegistry.from_json(registry.as_json())
    assert restored == registry
    assert restored.opportunities == tuple(
        sorted(opportunities, key=lambda item: item.opportunity_id)
    )


def test_zero_threshold_exact_tie_is_inconclusive(tmp_path: Path) -> None:
    selection, _opportunities, _fits, forecasts, coverage, seal = _prepared(tmp_path)
    local = next(
        item
        for item in forecasts
        if item.configuration_id == selection.selected_configuration_ids[0]
    )
    outcomes = {row.target_id: row.forecast / 2.0 for row in local.rows}

    opened = R2HoldoutOpenedMarker.create(
        selection_manifest_id=selection.manifest_id,
        seal_id=seal.seal_id,
        opened_at=NOW,
        opened_by="test",
        acknowledgement=HOLDOUT_ACKNOWLEDGEMENT,
        expected_selection_manifest_id=selection.manifest_id,
        expected_seal_id=seal.seal_id,
    )
    evaluation = evaluate_holdout(
        selection=selection,
        seal=seal,
        opened_marker=opened,
        forecast_datasets=forecasts,
        coverage_datasets=coverage,
        outcomes=outcomes,
    )
    result = evaluation.questions[0]
    assert result.candidate_value == pytest.approx(result.comparator_value)
    assert result.conclusion is HoldoutConclusion.INCONCLUSIVE


def test_file_bundle_builder_replays_the_consumed_evidence(tmp_path: Path) -> None:
    selection, _opportunities, _, forecasts, coverage, seal = _prepared(tmp_path)
    target_dataset = _target_dataset()

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
        outcome_loader=lambda: target_dataset,
        evaluator=evaluator,
    )
    assert evaluation is not None
    output = tmp_path / "bundle"
    bundle = write_built_holdout_bundle(tmp_path, output)
    assert bundle.bundle_id == verify_holdout_bundle(output).bundle_id
    assert bundle.evaluation.semantic_id == evaluation.evaluation_id
    assert consumed.evaluation_id == evaluation.evaluation_id


def test_holdout_preparation_cannot_be_cloned_after_claim(tmp_path: Path) -> None:
    _prepared(tmp_path / "source")
    first = tmp_path / "first"
    second = tmp_path / "second"
    prepare_holdout_from_files(tmp_path / "source", first)
    with pytest.raises(FileExistsError, match="transferred"):
        prepare_holdout_from_files(tmp_path / "source", second)
    with pytest.raises(FileExistsError, match="transferred"):
        prepare_holdout_from_files(first, second)


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


def test_preparation_replays_a_forced_failed_fit(tmp_path: Path) -> None:
    _selection_value, _opportunities_value, fits, _forecasts, _coverage, _seal = _prepared(
        tmp_path,
        forced_failure_configuration=_id("local"),
    )
    assert any(item.disposition is FinalFitDisposition.NUMERICAL_FAILURE for item in fits)
    assert verify_holdout_preparation(tmp_path).state.value == "PREPARED_UNOPENED"


def test_seal_rejects_an_incomplete_frozen_configuration_registry(tmp_path: Path) -> None:
    selection, _opportunities_value, fits, forecasts, coverage, _seal = _prepared(tmp_path)
    with pytest.raises(ValueError, match="local final fits"):
        seal_holdout_forecasts(
            selection=selection,
            feature_dataset=materialise_r2_holdout_features(
                selection=selection,
                opportunities=_opportunities(),
                feature_schema_id=_training_feature_dataset().raw_feature_schema_id,
                feature_set_id=_training_feature_set_id(),
                observation_dataset_id=_id("observations"),
                panel_dataset_id=_id("panel"),
                target_dataset_id=_target_dataset().dataset_id,
                projection=lambda item: R2HoldoutFeatureRow.create(
                    opportunity_id=item.opportunity_id,
                    target_id=item.target_id,
                    instrument_id=item.instrument_id,
                    decision_time=item.decision_time,
                    feature_cutoff=item.feature_data_asof,
                    latest_feature_bar_end=item.latest_feature_bar_end,
                    feature_schema_id=_training_feature_dataset().raw_feature_schema_id,
                    values=(1.0, 0.5),
                ),
            ),
            final_fits=fits[:1],
            forecast_datasets=forecasts[:1],
            coverage_datasets=coverage[:1],
            prepared_at=NOW,
            prepared_by="test",
        )


def test_mutated_training_evidence_is_rejected(tmp_path: Path) -> None:
    (
        _selection_value,
        _opportunities_value,
        _fits,
        _forecasts,
        _coverage,
        _seal,
    ) = _prepared(tmp_path)
    feature_path = next((tmp_path / "training/features").iterdir())
    payload = json.loads(feature_path.read_text())
    payload["rows"][0]["values"][0]["value"] = 99.0
    feature_path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="dataset ID"):
        verify_holdout_preparation(tmp_path)


def test_claimed_consumed_preparation_cannot_be_reopened_from_core_files(
    tmp_path: Path,
) -> None:
    selection, _opportunities, _fits, _forecasts, _coverage, seal = _prepared(tmp_path / "source")
    first = tmp_path / "first"
    prepare_holdout_from_files(tmp_path / "source", first)
    with pytest.raises(RuntimeError, match="stop"):
        reveal_holdout(
            first,
            expected_selection_manifest_id=selection.manifest_id,
            expected_seal_id=seal.seal_id,
            acknowledgement=HOLDOUT_ACKNOWLEDGEMENT,
            opened_by="test",
            consumed_by="test",
            opened_at=NOW,
            consumed_at=NOW + timedelta(seconds=1),
            outcome_loader=lambda: (_ for _ in ()).throw(RuntimeError("stop")),
            evaluator=lambda outcomes, opened: _unexpected_evaluator("must not evaluate"),
        )
    stripped = tmp_path / "stripped"
    stripped.mkdir()
    lifecycle_files = {
        ".preparation-source-claim.json",
        "opened.json",
        "consumed.json",
        "outcome-evidence.json",
        "evaluation.json",
    }
    for child in first.iterdir():
        if child.name in lifecycle_files:
            continue
        target = stripped / child.name
        if child.is_dir():
            shutil.copytree(child, target)
        else:
            shutil.copy2(child, target)
    with pytest.raises(ValueError, match="usage"):
        prepare_holdout_from_files(stripped, tmp_path / "reopened")


def test_duplicate_outcome_ids_are_rejected_and_consumed(tmp_path: Path) -> None:
    selection, opportunities, _fits, _forecasts, _coverage, seal = _prepared(tmp_path)
    with pytest.raises(ValueError, match="authenticated target dataset"):
        reveal_holdout(
            tmp_path,
            expected_selection_manifest_id=selection.manifest_id,
            expected_seal_id=seal.seal_id,
            acknowledgement=HOLDOUT_ACKNOWLEDGEMENT,
            opened_by="test",
            consumed_by="test",
            opened_at=NOW,
            consumed_at=NOW + timedelta(seconds=1),
            outcome_loader=lambda: [
                (opportunities[0].target_id, 0.1),
                (opportunities[0].target_id, 0.2),
                (opportunities[1].target_id, 0.3),
            ],
            evaluator=lambda outcomes, opened: _unexpected_evaluator("must not evaluate"),
        )


def test_preparation_transfer_has_one_reveal_owner(tmp_path: Path) -> None:
    selection, _opportunities, _fits, forecasts, coverage, seal = _prepared(tmp_path / "source")
    target_dataset = _target_dataset()
    destination = tmp_path / "destination"
    prepare_holdout_from_files(tmp_path / "source", destination)

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
        destination,
        expected_selection_manifest_id=selection.manifest_id,
        expected_seal_id=seal.seal_id,
        acknowledgement=HOLDOUT_ACKNOWLEDGEMENT,
        opened_by="test",
        consumed_by="test",
        opened_at=NOW,
        consumed_at=NOW + timedelta(seconds=1),
        outcome_loader=lambda: target_dataset,
        evaluator=evaluator,
    )
    assert evaluation is not None
    assert evaluation.evaluation_id
    assert consumed.outcome_accessed is True

    with pytest.raises(ValueError, match="transferred"):
        reveal_holdout(
            tmp_path / "source",
            expected_selection_manifest_id=selection.manifest_id,
            expected_seal_id=seal.seal_id,
            acknowledgement=HOLDOUT_ACKNOWLEDGEMENT,
            opened_by="test",
            consumed_by="test",
            opened_at=NOW,
            consumed_at=NOW + timedelta(seconds=1),
            outcome_loader=lambda: target_dataset,
            evaluator=evaluator,
        )
    assert not (tmp_path / "source" / "opened.json").exists()


def test_preparation_persists_only_pre_holdout_target_rows(tmp_path: Path) -> None:
    _prepared(tmp_path)
    target_path = next((tmp_path / "training/targets").iterdir())
    payload = json.loads(target_path.read_text())
    assert payload["contract"] == "qtrad-r2-target-projection-v1"
    assert payload["schema_version"] == 1
    assert payload["source_target_dataset_id"] == _target_dataset().dataset_id
    child = payload["target_dataset"]
    assert isinstance(child, dict)
    assert len(child["rows"]) == 8
    assert child["dataset_id"] != payload["source_target_dataset_id"]
    assert all(datetime.fromisoformat(row["decision_time"]) < NOW for row in child["rows"])


def test_file_reveal_loads_an_authenticated_target_child_after_open(tmp_path: Path) -> None:
    selection, _opportunities_value, _fits, _forecasts, _coverage, seal = _prepared(tmp_path)
    outcomes_path = tmp_path.parent / "canonical-target.json"
    outcomes_path.write_text(json.dumps(_target_dataset_payload(_target_dataset())))
    evaluation, consumed = reveal_holdout_from_files(
        tmp_path,
        outcomes_path=outcomes_path,
        expected_selection_manifest_id=selection.manifest_id,
        expected_seal_id=seal.seal_id,
        acknowledgement=HOLDOUT_ACKNOWLEDGEMENT,
        opened_by="test",
        consumed_by="test",
        opened_at=NOW,
        consumed_at=NOW + timedelta(seconds=1),
    )
    assert evaluation is not None
    assert evaluation.evaluation_id
    assert consumed.outcome_accessed is True
    target_payload = json.loads((tmp_path / "outcome-target.json").read_text())
    target_payload["rows"][0]["log_return"] = 999.0
    (tmp_path / "outcome-target.json").write_text(json.dumps(target_payload))
    with pytest.raises(ValueError, match="identity does not authenticate"):
        verify_holdout_evaluation(tmp_path)


def test_zero_return_retains_shared_eligibility_when_model_features_are_missing(
    tmp_path: Path,
) -> None:
    unavailable = _opportunities()[0].opportunity_id
    selection, opportunities, _fits, forecasts, coverage, _seal = _prepared(
        tmp_path,
        unavailable_opportunity_id=unavailable,
    )
    zero_configuration = next(
        configuration_id
        for configuration_id, model_family, *_ in selection.configuration_registry
        if model_family is ModelFamily.ZERO_RETURN
    )
    local_configuration = next(
        configuration_id
        for configuration_id, model_family, *_ in selection.configuration_registry
        if model_family is ModelFamily.LOCAL_RIDGE
    )
    zero_forecast = next(item for item in forecasts if item.configuration_id == zero_configuration)
    local_forecast = next(
        item for item in forecasts if item.configuration_id == local_configuration
    )
    assert len(zero_forecast.rows) == len(opportunities)
    assert len(local_forecast.rows) == len(opportunities) - 1
    zero_coverage = next(item for item in coverage if item.configuration_id == zero_configuration)
    local_coverage = next(item for item in coverage if item.configuration_id == local_configuration)
    assert any(row.opportunity_id == unavailable for row in zero_coverage.rows)
    assert any(row.opportunity_id == unavailable for row in local_coverage.rows)
    assert verify_holdout_preparation(tmp_path).seal_id
