"""R2.F1 evaluation, support, persistence and selection evidence."""

import json
from dataclasses import replace
from datetime import UTC, timedelta
from pathlib import Path

import pytest

from qtrad.application.r2_baselines import LocalRidgeOofResult
from qtrad.application.r2_evaluation import (
    EvaluationModel,
    build_r2_evaluation,
    build_selection_manifest,
    calculate_bucket_metrics,
    calculate_pairwise_comparison,
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
    TrainingBucketDefinition,
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
    if disposition is not ConfigurationDisposition.EVALUATED:
        raise ValueError("evaluation fixture configurations must start unevaluated")
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
        ConfigurationDisposition.EVALUATED,
        f"fixture evaluated configuration for {family.value}",
        forecast_dataset_id,
        None,
    )


def _evaluation_fixture() -> tuple[
    R1FoundationBindings,
    R2ExperimentConfig,
    LocalRidgeOofResult,
    tuple[EvaluationModel, ...],
    tuple[ConfigurationRecord, ...],
    R2FeatureDataset,
    tuple[R2FeatureDataset, ...],
    LocalComparatorManifest,
    EvaluationReport,
]:
    verified, datasets, config, fold = _pooled_fixture()
    local, comparator_features = _build_local_comparator(verified, datasets, config, fold)
    local_feature_datasets = tuple(
        datasets[item.name] for item in config.feature_sets if item.name not in {"P0", "P1"}
    )
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
        models.append(
            EvaluationModel(
                family,
                datasets[feature_name].feature_set_id,
                datasets[feature_name],
                forecasts,
                children,
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
            ConfigurationDisposition.EVALUATED,
            None,
        ),
        _configuration(
            ModelFamily.LOCAL_RIDGE,
            comparator_features.feature_set_id,
            ConfigurationDisposition.EVALUATED,
            local_forecast_id,
        ),
        _configuration(
            ModelFamily.POOLED_LOCAL_RIDGE,
            datasets["P0"].feature_set_id,
            ConfigurationDisposition.EVALUATED,
            models[0].forecasts.dataset_id,
        ),
        _configuration(
            ModelFamily.POOLED_CROSS_ASSET_RIDGE,
            datasets["P1"].feature_set_id,
            ConfigurationDisposition.EVALUATED,
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
        local_feature_datasets=local_feature_datasets,
    )
    return (
        verified,
        config,
        local,
        tuple(models),
        configurations,
        comparator_features,
        local_feature_datasets,
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


def test_evaluation_authenticates_every_declared_local_ladder_child() -> None:
    (
        verified,
        config,
        local,
        models,
        configurations,
        comparator_features,
        local_feature_datasets,
        _,
        _,
    ) = _evaluation_fixture()
    non_comparator = next(
        dataset
        for dataset in local_feature_datasets
        if dataset.feature_set_id != comparator_features.feature_set_id
    )
    incomplete = R2FeatureDataset.create(
        non_comparator.rows[:-1],
        feature_schema=non_comparator.feature_schema,
        feature_set_name=non_comparator.feature_set_name,
        feature_set_id=non_comparator.feature_set_id,
        observation_dataset_id=non_comparator.observation_dataset_id,
        panel_dataset_id=non_comparator.panel_dataset_id,
        target_dataset_id=non_comparator.target_dataset_id,
        fold_dataset_id=non_comparator.fold_dataset_id,
        experiment_configuration_id=non_comparator.experiment_configuration_id,
        evidence_class=non_comparator.evidence_class,
    )
    incomplete_ladder = tuple(
        incomplete if dataset.feature_set_id == incomplete.feature_set_id else dataset
        for dataset in local_feature_datasets
    )

    with pytest.raises(ValueError):
        build_r2_evaluation(
            verified,
            config,
            local,
            models,
            configurations,
            local_feature_set_id=comparator_features.feature_set_id,
            local_feature_datasets=incomplete_ladder,
        )


def test_evaluation_recomputes_common_support_and_persists_local_child(tmp_path: Path) -> None:
    (
        verified,
        config,
        local,
        models,
        configurations,
        comparator_features,
        local_feature_datasets,
        local_manifest,
        report,
    ) = _evaluation_fixture()
    common_global = tuple(
        item
        for item in report.metric_slices
        if item.support is ComparisonSupport.COMMON and item.breakdown == "GLOBAL"
    )
    assert common_global
    assert all(item.comparator_model_family is not None for item in common_global)
    assert len(report.comparisons) == 3
    assert report.all_model_common_target_ids
    assert len(report.configurations) == 4
    assert report.bucket_definitions
    assert report.bucket_ordering
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
        local_feature_datasets=local_feature_datasets,
    )
    assert (bundle.parent / "local-comparator.json").is_file()
    assert len(tuple(bundle.parent.glob("evaluated-model-*.json"))) == 4

    payload = json.loads((bundle.parent / "evaluation.json").read_text())
    payload["all_model_common_target_ids"] = []
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
            local_feature_datasets=local_feature_datasets,
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
        _,
        local_manifest,
        report,
    ) = _evaluation_fixture()
    manifest = build_selection_manifest(
        report,
        local_manifest,
        config,
        primary_metric="INSTRUMENT_BALANCED_COMMON_SUPPORT_MSE",
        secondary_metrics=("MAE", "SPEARMAN", "COVERAGE", "STABILITY"),
        final_fitting_procedure="REFIT_PRE_HOLDOUT_HISTORY_WITH_FROZEN_PREPROCESSING_V1",
        application_image_identity=_FINAL_APPLICATION_IMAGE,
        frozen_at=config.holdout_range[1] + timedelta(days=1),
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
        replace(
            manifest,
            frozen_at=manifest.frozen_at.replace(tzinfo=UTC) + timedelta(seconds=1),
        )


def test_pairwise_common_support_is_independent_of_third_model() -> None:
    verified, *_ = _pooled_fixture()
    targets = {
        row.target_id: row
        for row in verified.targets.rows
        if row.return_disposition.value == "VALID"
    }
    target_ids = tuple(targets)[:3]
    supports = {
        ModelFamily.LOCAL_RIDGE: target_ids,
        ModelFamily.POOLED_LOCAL_RIDGE: target_ids[:2],
        ModelFamily.POOLED_CROSS_ASSET_RIDGE: target_ids[:1],
    }
    predictions = {
        ModelFamily.LOCAL_RIDGE: {target_id: 0.0 for target_id in target_ids},
        ModelFamily.POOLED_LOCAL_RIDGE: {target_id: 0.0 for target_id in target_ids[:2]},
        ModelFamily.POOLED_CROSS_ASSET_RIDGE: {target_id: 0.0 for target_id in target_ids[:1]},
    }

    pooled_vs_local = calculate_pairwise_comparison(
        ModelFamily.POOLED_LOCAL_RIDGE,
        ModelFamily.LOCAL_RIDGE,
        supports,
        predictions,
        targets,
    )
    context_vs_pooled = calculate_pairwise_comparison(
        ModelFamily.POOLED_CROSS_ASSET_RIDGE,
        ModelFamily.POOLED_LOCAL_RIDGE,
        supports,
        predictions,
        targets,
    )

    assert pooled_vs_local.common_target_ids == tuple(sorted(target_ids[:2]))
    assert context_vs_pooled.common_target_ids == tuple(sorted(target_ids[:1]))


def test_fold_bucket_thresholds_apply_only_to_their_validation_fold() -> None:
    verified, *_ = _pooled_fixture()
    targets = {
        row.target_id: row
        for row in verified.targets.rows
        if row.return_disposition.value == "VALID"
    }
    target_ids = tuple(targets)[:4]
    family = ModelFamily.LOCAL_RIDGE
    predictions = {family: {target_id: float(index) for index, target_id in enumerate(target_ids)}}
    fold_by_target = {
        target_ids[0]: "fold-a",
        target_ids[1]: "fold-a",
        target_ids[2]: "fold-b",
        target_ids[3]: "fold-b",
    }
    definitions = (
        TrainingBucketDefinition.create(
            model_family=family,
            outer_fold_id="fold-a",
            horizon=targets[target_ids[0]].horizon,
            training_target_ids=(target_ids[0],),
            thresholds=(0.5,),
            training_prediction_evidence_id=semantic_id({"fold": "a"}),
        ),
        TrainingBucketDefinition.create(
            model_family=family,
            outer_fold_id="fold-b",
            horizon=targets[target_ids[0]].horizon,
            training_target_ids=(target_ids[1],),
            thresholds=(100.0,),
            training_prediction_evidence_id=semantic_id({"fold": "b"}),
        ),
    )

    rows, _ = calculate_bucket_metrics(
        definitions,
        predictions,
        targets,
        fold_by_target,
        {family: target_ids},
        (),
    )

    counts = {
        fold_id: sum(row.row_count for row in rows if row.outer_fold_id == fold_id)
        for fold_id in ("fold-a", "fold-b")
    }
    assert counts == {"fold-a": 2, "fold-b": 2}


def test_selection_rejects_materialised_holdout_state() -> None:
    _, config, _, _, _, _, _, local_manifest, report = _evaluation_fixture()

    with pytest.raises(ValueError, match="holdout evidence"):
        build_selection_manifest(
            report,
            local_manifest,
            config,
            primary_metric="INSTRUMENT_BALANCED_COMMON_SUPPORT_MSE",
            secondary_metrics=("MAE", "COVERAGE", "STABILITY"),
            final_fitting_procedure="REFIT_PRE_HOLDOUT_HISTORY_WITH_FROZEN_PREPROCESSING_V1",
            application_image_identity=_FINAL_APPLICATION_IMAGE,
            frozen_at=config.holdout_range[1] + timedelta(days=1),
            frozen_by="r2-f1-fixture",
            holdout_feature_dataset_ids=(semantic_id({"holdout": "feature"}),),
        )
