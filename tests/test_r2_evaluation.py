"""R2.F1 evaluation, support, persistence and selection evidence."""

import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from qtrad.application.r2_baselines import LocalRidgeOofResult
from qtrad.application.r2_evaluation import (
    EvaluationModel,
    TrainingPredictions,
    build_r2_evaluation,
    build_selection_manifest,
    calculate_predictive_metrics,
)
from qtrad.application.r2_pooled import build_pooled_ridge_oof
from qtrad.application.r2_readiness import R1FoundationBindings
from qtrad.domain.forecasts import ForecastDataset
from qtrad.domain.r2_evaluation import (
    ComparisonSupport,
    ConfigurationDisposition,
    ConfigurationRecord,
    EvaluationReport,
    LocalComparatorManifest,
    MetricAvailability,
    semantic_id,
)
from qtrad.domain.r2_features import R2FeatureDataset
from qtrad.domain.r2_readiness import ModelFamily, R2ExperimentConfig
from qtrad.runtime.r2_evaluation import (
    verify_persisted_r2_evaluation,
    verify_persisted_r2_selection,
    write_r2_evaluation_bundle,
    write_r2_selection_manifest,
)
from tests.test_r2_pooled import (
    _FINAL_APPLICATION_IMAGE,
    _FINAL_NUMPY_IDENTITY,
    _FINAL_SKLEARN_IDENTITY,
    _build_local_comparator,
    _pooled_fixture,
    _pooled_selection,
)


def _configuration(
    family: ModelFamily,
    feature_set_id: str | None,
    disposition: ConfigurationDisposition,
    forecast_dataset_id: str | None,
) -> ConfigurationRecord:
    identity = semantic_id(
        {
            "family": family.value,
            "feature_set_id": feature_set_id,
            "forecast_dataset_id": forecast_dataset_id,
        }
    )
    return ConfigurationRecord(
        identity,
        family,
        feature_set_id,
        disposition,
        f"fixture disposition for {family.value}",
        forecast_dataset_id,
    )


def _local_training_predictions(
    local: LocalRidgeOofResult,
    feature_set_id: str,
) -> tuple[TrainingPredictions, ...]:
    fit = next(
        result.fit for result in local.fold_results if result.fit.feature_set_id == feature_set_id
    )
    assert fit.preprocessing is not None
    return (
        TrainingPredictions(
            ModelFamily.LOCAL_RIDGE,
            fit.outer_fold_id,
            fit.horizon,
            tuple(
                (target_id, float(index))
                for index, target_id in enumerate(sorted(fit.preprocessing.training_target_ids))
            ),
        ),
    )


def _evaluation_fixture() -> tuple[
    R1FoundationBindings,
    R2ExperimentConfig,
    LocalRidgeOofResult,
    tuple[EvaluationModel, ...],
    tuple[ConfigurationRecord, ...],
    R2FeatureDataset,
    LocalComparatorManifest,
    EvaluationReport,
]:
    verified, datasets, config, fold = _pooled_fixture()
    local, comparator_features = _build_local_comparator(verified, datasets, config, fold)
    selections = (
        _pooled_selection(verified, datasets["P0"], config, fold, ModelFamily.POOLED_LOCAL_RIDGE),
        _pooled_selection(
            verified, datasets["P1"], config, fold, ModelFamily.POOLED_CROSS_ASSET_RIDGE
        ),
    )
    pooled = build_pooled_ridge_oof(
        verified,
        (datasets["P0"], datasets["P1"]),
        config,
        selections,
        local,
        comparator_features,
        application_image_identity=_FINAL_APPLICATION_IMAGE,
        numpy_library_identity=_FINAL_NUMPY_IDENTITY,
        sklearn_library_identity=_FINAL_SKLEARN_IDENTITY,
    )
    models: list[EvaluationModel] = []
    for family, feature_name in (
        (ModelFamily.POOLED_LOCAL_RIDGE, "P0"),
        (ModelFamily.POOLED_CROSS_ASSET_RIDGE, "P1"),
    ):
        children = tuple(
            result for result in pooled.fold_results if result.fit.model_family is family
        )
        forecasts = ForecastDataset.create(
            tuple(row for child in children for row in child.forecasts.rows),
            observation_dataset_id=pooled.forecasts.observation_dataset_id,
            panel_dataset_id=pooled.forecasts.panel_dataset_id,
            target_dataset_id=pooled.forecasts.target_dataset_id,
            fold_dataset_id=pooled.forecasts.fold_dataset_id,
        )
        fit = children[0].fit
        assert fit.preprocessing is not None
        training_values = tuple(
            (target_id, float(index))
            for index, target_id in enumerate(sorted(fit.preprocessing.training_target_ids))
        )
        models.append(
            EvaluationModel(
                family,
                datasets[feature_name].feature_set_id,
                forecasts,
                children,
                (
                    TrainingPredictions(
                        family,
                        fold.fold_id,
                        config.primary_horizon,
                        training_values,
                    ),
                ),
            )
        )
    local_forecast_id = ForecastDataset.create(
        tuple(
            row
            for child in local.fold_results
            if child.fit.feature_set_id == comparator_features.feature_set_id
            for row in child.forecasts.rows
        ),
        observation_dataset_id=local.forecasts.observation_dataset_id,
        panel_dataset_id=local.forecasts.panel_dataset_id,
        target_dataset_id=local.forecasts.target_dataset_id,
        fold_dataset_id=local.forecasts.fold_dataset_id,
    ).dataset_id
    configurations = (
        _configuration(
            ModelFamily.ZERO_RETURN,
            None,
            ConfigurationDisposition.RETAINED_CONTROL,
            None,
        ),
        _configuration(
            ModelFamily.LOCAL_RIDGE,
            comparator_features.feature_set_id,
            ConfigurationDisposition.RETAINED_CONTROL,
            local_forecast_id,
        ),
        _configuration(
            ModelFamily.POOLED_LOCAL_RIDGE,
            datasets["P0"].feature_set_id,
            ConfigurationDisposition.SELECTED_CANDIDATE,
            models[0].forecasts.dataset_id,
        ),
        _configuration(
            ModelFamily.POOLED_CROSS_ASSET_RIDGE,
            datasets["P1"].feature_set_id,
            ConfigurationDisposition.REJECTED,
            models[1].forecasts.dataset_id,
        ),
    )
    local_manifest, report = build_r2_evaluation(
        verified,
        config,
        local,
        models,
        configurations,
        local_feature_set_id=comparator_features.feature_set_id,
        local_training_predictions=_local_training_predictions(
            local, comparator_features.feature_set_id
        ),
    )
    return (
        verified,
        config,
        local,
        tuple(models),
        configurations,
        comparator_features,
        local_manifest,
        report,
    )


def test_metrics_report_undefined_values_instead_of_zero() -> None:
    verified, *_ = _pooled_fixture()
    targets = {row.target_id: row for row in verified.targets.rows}
    ids = tuple(targets)[:2]

    metrics = calculate_predictive_metrics(
        ids,
        {target_id: 0.0 for target_id in ids},
        targets,
        minimum_correlation_rows=3,
    )

    assert metrics.pearson.availability is MetricAvailability.NOT_DEFINED
    assert metrics.directional_accuracy.availability is MetricAvailability.NOT_DEFINED
    assert metrics.pearson.value is None


def test_evaluation_recomputes_common_support_and_persists_local_child(tmp_path: Path) -> None:
    (
        verified,
        config,
        local,
        models,
        configurations,
        comparator_features,
        local_manifest,
        report,
    ) = _evaluation_fixture()
    common_global = tuple(
        item
        for item in report.metric_slices
        if item.support is ComparisonSupport.COMMON and item.breakdown == "GLOBAL"
    )
    assert {item.model_family for item in common_global} == set(ModelFamily)
    assert report.common_target_ids
    assert len(report.configurations) == 4
    assert report.bucket_definitions
    assert report.stability

    bundle = write_r2_evaluation_bundle(tmp_path / "evaluation", local_manifest, report)
    verify_persisted_r2_evaluation(
        bundle,
        report,
        local_manifest,
        verified,
        config,
        local,
        models,
        configurations,
        local_feature_set_id=comparator_features.feature_set_id,
        local_training_predictions=_local_training_predictions(
            local, comparator_features.feature_set_id
        ),
    )
    assert (bundle.parent / "local-comparator.json").is_file()

    payload = json.loads((bundle.parent / "evaluation.json").read_text())
    payload["common_target_ids"] = []
    (bundle.parent / "evaluation.json").write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="failed authentication"):
        verify_persisted_r2_evaluation(
            bundle,
            report,
            local_manifest,
            verified,
            config,
            local,
            models,
            configurations,
            local_feature_set_id=comparator_features.feature_set_id,
            local_training_predictions=_local_training_predictions(
                local, comparator_features.feature_set_id
            ),
        )


def test_selection_freeze_retains_rejections_and_contains_no_holdout_data(
    tmp_path: Path,
) -> None:
    (
        _,
        config,
        _,
        _,
        configurations,
        _,
        local_manifest,
        report,
    ) = _evaluation_fixture()
    selected = next(
        item.configuration_id
        for item in configurations
        if item.disposition is ConfigurationDisposition.SELECTED_CANDIDATE
    )
    manifest = build_selection_manifest(
        report,
        local_manifest,
        config,
        selected_configuration_ids=(selected,),
        holdout_comparator_configuration_ids=tuple(
            item.configuration_id for item in configurations
        ),
        primary_metric="INSTRUMENT_BALANCED_COMMON_SUPPORT_MSE",
        secondary_metrics=("MAE", "SPEARMAN", "COVERAGE", "STABILITY"),
        final_fitting_procedure="REFIT_PRE_HOLDOUT_HISTORY_WITH_FROZEN_PREPROCESSING_V1",
        application_image_identity=_FINAL_APPLICATION_IMAGE,
        frozen_at=config.holdout_range[0] - timedelta(days=1),
        frozen_by="r2-f1-fixture",
    )
    path = tmp_path / "selection.json"
    write_r2_selection_manifest(path, manifest)
    verify_persisted_r2_selection(path, manifest, report, local_manifest, config)

    payload = manifest.as_json()
    assert set(manifest.evaluated_configuration_ids) == {
        item.configuration_id for item in configurations
    }
    assert "holdout_outcomes" not in payload
    assert "holdout_features" not in payload

    with pytest.raises(ValueError, match="does not authenticate"):
        replace(manifest, frozen_at=config.holdout_range[0])
