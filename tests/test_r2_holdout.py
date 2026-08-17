from __future__ import annotations

import builtins
import json
import shutil
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import NoReturn
from uuid import UUID

import pytest

import qtrad.domain.r2_holdout as holdout_domain
import qtrad.runtime.r2_holdout as holdout_runtime
import qtrad.runtime.r2_partitioned_rows as partitioned_rows_runtime
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
    PanelDataset,
    PanelRow,
    PanelStatus,
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
    R2HoldoutConsumedMarker,
    R2HoldoutFeatureRow,
    R2HoldoutForecastSeal,
    R2HoldoutOpenedMarker,
    R2HoldoutOpportunityRegistry,
    R2HoldoutQuestion,
    R2HoldoutSelectionManifest,
    R2HoldoutTargetProjection,
    R2HoldoutTargetSource,
    R2PreHoldoutTargetProjection,
    holdout_selection_compact_bindings,
)
from qtrad.domain.r2_readiness import EvidenceClass, FeatureFamily, ModelFamily
from qtrad.runtime.r2_bundles import canonical_bytes
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

    holdout_target_source = _target_source(include_noneligible=include_noneligible)
    selection = freeze_holdout_selection(
        prior_selection=prior,
        foundation_bundle_id=_id("foundation"),
        oof_id=_id("oof"),
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
        holdout_target_source=holdout_target_source,
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
                holdout_target_source.source_target_dataset_id if bind_target_dataset else None
            ),
            "primary_horizon_seconds": 900,
            "pre_holdout_target_dataset_id": (
                holdout_target_source.pre_holdout_target_dataset.dataset_id
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
                    instrument_id="INSTRUMENT_0",
                    decision_time=decision,
                    horizon=timedelta(seconds=900),
                    target_basis=PriceBasis.MID,
                    target_revision_policy="FIXTURE_V1",
                ),
                instrument_id="INSTRUMENT_0",
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


def _target_source(*, include_noneligible: bool = False) -> R2HoldoutTargetSource:
    return R2HoldoutTargetSource.create_from_target_dataset(
        _target_dataset(include_noneligible=include_noneligible),
        holdout_range=(NOW + timedelta(days=1), NOW + timedelta(days=2)),
        primary_horizon_seconds=900,
        target_instruments=("INSTRUMENT_0", "INSTRUMENT_1"),
        opportunities=_opportunities(include_noneligible=include_noneligible),
    )


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


def _causal_source() -> R2HoldoutTargetSource:
    target_dataset = _target_dataset(include_noneligible=True)
    panel_rows = tuple(
        PanelRow(
            decision_time=opportunity.decision_time,
            instrument_id=opportunity.instrument_id,
            basis=PriceBasis.MID,
            feature_data_asof=opportunity.feature_data_asof,
            latest_feature_bar_end=opportunity.latest_feature_bar_end,
            status=PanelStatus.OBSERVED,
            audit_disposition=None,
            selected_event_id=UUID(int=index + 1),
            selected_stream_version=1,
            selected_global_position=index + 1,
            selected_availability_time=opportunity.feature_data_asof,
            selected_revision=1,
            interval_start=opportunity.feature_data_asof - timedelta(minutes=1),
            interval_end=opportunity.latest_feature_bar_end,
            open=Decimal("1"),
            high=Decimal("1"),
            low=Decimal("1"),
            close=Decimal("1"),
            sample_count=1,
            quality=None,
        )
        for index, opportunity in enumerate(_opportunities(include_noneligible=True))
    )
    panel = PanelDataset.create(
        panel_rows,
        observation_dataset_id=_id("observations"),
        foundation_configuration_id=_id("foundation"),
    )
    return R2HoldoutTargetSource.create_from_target_dataset(
        target_dataset,
        holdout_range=(NOW + timedelta(days=1), NOW + timedelta(days=2)),
        primary_horizon_seconds=900,
        target_instruments=("INSTRUMENT_0", "INSTRUMENT_1"),
        panel=panel,
        source_active_intervals={
            "INSTRUMENT_0": ((NOW, NOW + timedelta(days=3)),),
            "INSTRUMENT_1": ((NOW, NOW + timedelta(days=3)),),
        },
        availability_evidence_id=_id("availability"),
    )


def test_holdout_source_replays_causal_disposition_from_r1_evidence() -> None:
    source = _causal_source()
    panel_id = source.causal_panel_dataset_id
    assert panel_id is not None
    panel = PanelDataset(
        rows=tuple(
            PanelRow(
                decision_time=opportunity.decision_time,
                instrument_id=opportunity.instrument_id,
                basis=PriceBasis.MID,
                feature_data_asof=opportunity.feature_data_asof,
                latest_feature_bar_end=opportunity.latest_feature_bar_end,
                status=PanelStatus.OBSERVED,
                audit_disposition=None,
                selected_event_id=UUID(int=index + 1),
                selected_stream_version=1,
                selected_global_position=index + 1,
                selected_availability_time=opportunity.feature_data_asof,
                selected_revision=1,
                interval_start=opportunity.feature_data_asof - timedelta(minutes=1),
                interval_end=opportunity.latest_feature_bar_end,
                open=Decimal("1"),
                high=Decimal("1"),
                low=Decimal("1"),
                close=Decimal("1"),
                sample_count=1,
                quality=None,
            )
            for index, opportunity in enumerate(_opportunities(include_noneligible=True))
        ),
        observation_dataset_id=_id("observations"),
        foundation_configuration_id=_id("foundation"),
        dataset_id=panel_id,
    )
    source.verify_r1_causal_evidence(
        panel=panel,
        source_active_intervals={
            "INSTRUMENT_0": ((NOW, NOW + timedelta(days=3)),),
            "INSTRUMENT_1": ((NOW, NOW + timedelta(days=3)),),
        },
        data_gaps=(),
        availability_evidence_id=_id("availability"),
    )
    mutated = HoldoutTargetOpportunity.create(
        target_id=source.opportunities[0].target_id,
        instrument_id=source.opportunities[0].instrument_id,
        decision_time=source.opportunities[0].decision_time,
        target_horizon_seconds=900,
        feature_data_asof=source.opportunities[0].feature_data_asof + timedelta(minutes=1),
        latest_feature_bar_end=source.opportunities[0].latest_feature_bar_end,
        dependency_start=source.opportunities[0].dependency_start,
        dependency_end=source.opportunities[0].dependency_end,
        disposition=source.opportunities[0].disposition,
    )
    tampered = R2HoldoutTargetSource.create(
        source_target_dataset_id=source.source_target_dataset_id,
        observation_dataset_id=source.observation_dataset_id,
        foundation_configuration_id=source.foundation_configuration_id,
        holdout_range=source.holdout_range,
        primary_horizon_seconds=source.primary_horizon_seconds,
        target_instruments=source.target_instruments,
        targets=source.targets,
        pre_holdout_target_dataset=source.pre_holdout_target_dataset,
        opportunities=(mutated, *source.opportunities[1:]),
        causal_panel_dataset_id=source.causal_panel_dataset_id,
        availability_evidence_id=source.availability_evidence_id,
    )
    with pytest.raises(ValueError, match="causal registry"):
        tampered.verify_r1_causal_evidence(
            panel=panel,
            source_active_intervals={
                "INSTRUMENT_0": ((NOW, NOW + timedelta(days=3)),),
                "INSTRUMENT_1": ((NOW, NOW + timedelta(days=3)),),
            },
            data_gaps=(),
            availability_evidence_id=_id("availability"),
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


def _training_feature_authority(
    *, include_noneligible: bool = False
) -> dict[str, R2FeatureDataset]:
    dataset = _training_feature_dataset(include_noneligible=include_noneligible)
    return {dataset.dataset_id: dataset}


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
    target_source = _target_source(include_noneligible=include_noneligible)
    opportunities = target_source.opportunities
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
    training_targets = target_source.pre_holdout_target_dataset
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
        holdout_target_source=target_source,
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
    assert (
        verify_holdout_preparation(
            tmp_path,
            training_feature_datasets=_training_feature_authority(),
            holdout_target_source=_target_source(),
        ).seal_id
        == seal.seal_id
    )

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
        holdout_target_source=_target_source(),
        training_feature_datasets=_training_feature_authority(),
        outcome_loader=lambda: target_dataset,
        evaluator=evaluator,
    )
    assert evaluation is not None
    assert consumed.outcome_accessed is True
    opened, replayed_consumed = verify_holdout_markers(tmp_path)
    assert opened.seal_id == seal.seal_id
    assert replayed_consumed.marker_id == consumed.marker_id
    assert evaluation.questions[0].conclusion in tuple(HoldoutConclusion)


def test_preparation_replay_reads_each_compact_child_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _selection, _opportunities, _fits, _forecasts, _coverage, _seal = _prepared(tmp_path)
    calls: list[str] = []
    original_loader = holdout_runtime.load_partitioned_rows

    def count_loader(
        root: Path,
        relative: str,
        payload: dict[str, object],
        *,
        identity_field: str,
    ) -> tuple[dict[str, object], ...]:
        calls.append(relative)
        return original_loader(root, relative, payload, identity_field=identity_field)

    monkeypatch.setattr(holdout_runtime, "load_partitioned_rows", count_loader)
    verify_holdout_preparation(
        tmp_path,
        training_feature_datasets=_training_feature_authority(),
        holdout_target_source=_target_source(),
    )
    assert calls
    assert len(calls) == len(set(calls))
    assert all(path.endswith(".json") for path in calls)


def test_cached_child_snapshot_rejects_added_part(tmp_path: Path) -> None:
    _selection, _opportunities, _fits, _forecasts, _coverage, _seal = _prepared(tmp_path)
    cache: holdout_runtime._PayloadCache = {}
    holdout_runtime._verify_child(
        tmp_path,
        "features.json",
        contract=holdout_runtime.R2_HOLDOUT_FEATURES_CONTRACT,
        identity_key="dataset_id",
        expected_fields=holdout_runtime._FEATURE_FIELDS,
        _payload_cache=cache,
    )
    part = next((tmp_path / "features.json.parts").glob("*.json"))
    (tmp_path / "features.json.parts" / "part-999999.json").write_bytes(part.read_bytes())
    with pytest.raises(ValueError, match="part closure"):
        holdout_runtime._verify_child(
            tmp_path,
            "features.json",
            contract=holdout_runtime.R2_HOLDOUT_FEATURES_CONTRACT,
            identity_key="dataset_id",
            expected_fields=holdout_runtime._FEATURE_FIELDS,
            _payload_cache=cache,
        )


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
        holdout_target_source=_target_source(include_noneligible=True),
        training_feature_datasets=_training_feature_authority(include_noneligible=True),
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


def test_target_source_identity_streams_large_row_collections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _target_dataset()
    original_semantic_id = holdout_domain._semantic_id

    def bounded_semantic_id(value: object) -> str:
        if isinstance(value, dict):
            for field in ("rows", "targets"):
                collection = value.get(field)
                if isinstance(collection, list):
                    assert len(collection) <= 1, f"identity materialised full {field} collection"
        return original_semantic_id(value)

    monkeypatch.setattr(holdout_domain, "_semantic_id", bounded_semantic_id)
    target_source = R2HoldoutTargetSource.create_from_target_dataset(
        source,
        holdout_range=(NOW + timedelta(days=1), NOW + timedelta(days=2)),
        primary_horizon_seconds=900,
        target_instruments=("INSTRUMENT_0", "INSTRUMENT_1"),
        opportunities=_opportunities(),
    )

    assert target_source.targets
    assert target_source.pre_holdout_target_dataset.rows


def test_target_identity_streaming_preserves_canonical_digests() -> None:
    source = _target_dataset()
    target_index = holdout_domain.R2HoldoutTargetIndex.create(source)
    expected_index_id = holdout_domain._semantic_id(
        {
            "contract": target_index.CONTRACT,
            "schema_version": target_index.SCHEMA_VERSION,
            "source_target_dataset_id": source.dataset_id,
            "observation_dataset_id": source.observation_dataset_id,
            "foundation_configuration_id": source.foundation_configuration_id,
            "targets": [item.as_json() for item in target_index.targets],
        }
    )
    assert target_index.dataset_id == expected_index_id

    target_source = _target_source()
    assert target_source.source_id == holdout_domain._semantic_id(target_source.semantic_json())


def test_causal_metadata_identity_streams_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    panel_rows = tuple(
        PanelRow(
            decision_time=NOW + timedelta(minutes=index),
            instrument_id="INSTRUMENT_0",
            basis=PriceBasis.MID,
            feature_data_asof=NOW + timedelta(minutes=index + 1),
            latest_feature_bar_end=NOW + timedelta(minutes=index),
            status=PanelStatus.OBSERVED,
            audit_disposition=None,
            selected_event_id=UUID(int=index + 1),
            selected_stream_version=1,
            selected_global_position=index + 1,
            selected_availability_time=NOW + timedelta(minutes=index) - timedelta(minutes=1),
            selected_revision=1,
            interval_start=NOW + timedelta(minutes=index) - timedelta(minutes=1),
            interval_end=NOW + timedelta(minutes=index),
            open=Decimal("1"),
            high=Decimal("1"),
            low=Decimal("1"),
            close=Decimal("1"),
            sample_count=1,
            quality=None,
        )
        for index in range(2)
    )
    panel = PanelDataset.create(
        panel_rows,
        observation_dataset_id=_id("observations"),
        foundation_configuration_id=_id("foundation"),
    )
    original_semantic_id = holdout_domain._semantic_id

    def bounded_semantic_id(value: object) -> str:
        if isinstance(value, dict):
            collection = value.get("rows")
            if isinstance(collection, list):
                assert len(collection) <= 1, "causal metadata identity copied all rows"
        return original_semantic_id(value)

    monkeypatch.setattr(holdout_domain, "_semantic_id", bounded_semantic_id)
    metadata = holdout_domain.R2HoldoutCausalMetadata.create(panel)
    restored = holdout_domain.R2HoldoutCausalMetadata.from_rows(
        source_panel_dataset_id=panel.dataset_id,
        rows=[row.as_json() for row in metadata.rows],
    )
    assert restored == metadata
    assert restored.dataset_id == metadata.dataset_id
    expected_id = original_semantic_id(
        {
            "contract": metadata.CONTRACT,
            "schema_version": metadata.SCHEMA_VERSION,
            "source_panel_dataset_id": panel.dataset_id,
            "rows": [row.as_json() for row in metadata.rows],
        }
    )
    assert metadata.dataset_id == expected_id


def test_holdout_child_identities_stream_serialised_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_dataset = _target_dataset()
    pre_projection = R2PreHoldoutTargetProjection.create_from_target_dataset(
        target_dataset,
        holdout_start=NOW + timedelta(days=1),
        primary_horizon_seconds=900,
        target_instruments=("INSTRUMENT_0", "INSTRUMENT_1"),
    )
    target_source = _target_source()
    target_projection = R2HoldoutTargetProjection.create_from_source(target_source)
    opportunity_registry = R2HoldoutOpportunityRegistry.create_from_source(target_source)

    assert pre_projection.projection_id == holdout_domain._semantic_id(
        pre_projection.semantic_json()
    )
    assert target_projection.projection_id == holdout_domain._semantic_id(
        target_projection.semantic_json()
    )
    assert opportunity_registry.registry_id == holdout_domain._semantic_id(
        opportunity_registry.semantic_json()
    )

    payloads = (
        pre_projection.as_json(),
        target_projection.as_json(),
        opportunity_registry.as_json(),
    )

    def forbidden(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("identity verification must not materialise semantic JSON")

    monkeypatch.setattr(TargetDataset, "as_json", forbidden)
    monkeypatch.setattr(R2PreHoldoutTargetProjection, "semantic_json", forbidden)
    monkeypatch.setattr(R2HoldoutTargetProjection, "semantic_json", forbidden)
    monkeypatch.setattr(R2HoldoutOpportunityRegistry, "semantic_json", forbidden)

    assert R2PreHoldoutTargetProjection.from_json(payloads[0]) == pre_projection
    assert R2HoldoutTargetProjection.from_json(payloads[1]) == target_projection
    assert R2HoldoutOpportunityRegistry.from_json(payloads[2]) == opportunity_registry


def test_target_projection_rejects_a_different_source_dataset() -> None:
    source = _target_dataset()
    target_source = R2HoldoutTargetSource.create_from_target_dataset(
        source,
        holdout_range=(NOW + timedelta(days=1), NOW + timedelta(days=2)),
        primary_horizon_seconds=900,
        target_instruments=("INSTRUMENT_0", "INSTRUMENT_1"),
        opportunities=_opportunities(),
    )
    projection = R2HoldoutTargetProjection.create_from_source(
        target_source,
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

    with pytest.raises(ValueError, match="retained target"):
        target_source.verify_target_dataset(altered_source)
    projection.verify_source(target_source)


def test_opportunity_registry_requires_complete_source_coverage_and_round_trips() -> None:
    source = _target_dataset()
    opportunities = _opportunities()
    with pytest.raises(ValueError, match="complete"):
        R2HoldoutTargetSource.create_from_target_dataset(
            source,
            holdout_range=(NOW + timedelta(days=1), NOW + timedelta(days=2)),
            primary_horizon_seconds=900,
            target_instruments=("INSTRUMENT_0", "INSTRUMENT_1"),
            opportunities=opportunities[:1],
        )
    target_source = R2HoldoutTargetSource.create_from_target_dataset(
        source,
        holdout_range=(NOW + timedelta(days=1), NOW + timedelta(days=2)),
        primary_horizon_seconds=900,
        target_instruments=("INSTRUMENT_0", "INSTRUMENT_1"),
        opportunities=opportunities,
    )
    registry = R2HoldoutOpportunityRegistry.create_from_source(
        target_source,
    )
    restored = R2HoldoutOpportunityRegistry.from_json(registry.as_json())
    assert restored == registry
    assert restored.opportunities == tuple(
        sorted(opportunities, key=lambda item: item.opportunity_id)
    )
    mutated_opportunity = HoldoutTargetOpportunity.create(
        target_id=opportunities[0].target_id,
        instrument_id=opportunities[0].instrument_id,
        decision_time=opportunities[0].decision_time,
        target_horizon_seconds=opportunities[0].target_horizon_seconds,
        feature_data_asof=opportunities[0].feature_data_asof,
        latest_feature_bar_end=opportunities[0].latest_feature_bar_end,
        dependency_start=opportunities[0].dependency_start,
        dependency_end=opportunities[0].dependency_end,
        disposition=HoldoutOpportunityDisposition.GAP,
    )
    with pytest.raises(ValueError, match="authenticated source derivation"):
        R2HoldoutTargetSource.create_from_target_dataset(
            source,
            holdout_range=(NOW + timedelta(days=1), NOW + timedelta(days=2)),
            primary_horizon_seconds=900,
            target_instruments=("INSTRUMENT_0", "INSTRUMENT_1"),
            opportunities=(mutated_opportunity, opportunities[1]),
        )


def test_target_source_preserves_verified_instrument_order() -> None:
    source = R2HoldoutTargetSource.create_from_target_dataset(
        _target_dataset(),
        holdout_range=(NOW + timedelta(days=1), NOW + timedelta(days=2)),
        primary_horizon_seconds=900,
        target_instruments=("INSTRUMENT_1", "INSTRUMENT_0"),
        opportunities=_opportunities(),
    )

    assert source.target_instruments == ("INSTRUMENT_1", "INSTRUMENT_0")
    assert R2HoldoutTargetSource.from_json(source.as_json()).target_instruments == (
        "INSTRUMENT_1",
        "INSTRUMENT_0",
    )


def test_target_source_rejects_configuration_specific_dispositions() -> None:
    opportunity = _opportunities()[0]
    unavailable = HoldoutTargetOpportunity.create(
        target_id=opportunity.target_id,
        instrument_id=opportunity.instrument_id,
        decision_time=opportunity.decision_time,
        target_horizon_seconds=opportunity.target_horizon_seconds,
        feature_data_asof=opportunity.feature_data_asof,
        latest_feature_bar_end=opportunity.latest_feature_bar_end,
        dependency_start=opportunity.dependency_start,
        dependency_end=opportunity.dependency_end,
        disposition=HoldoutOpportunityDisposition.UNAVAILABLE_FEATURE,
    )

    with pytest.raises(ValueError, match="configuration-specific"):
        R2HoldoutTargetSource.create_from_target_dataset(
            _target_dataset(),
            holdout_range=(NOW + timedelta(days=1), NOW + timedelta(days=2)),
            primary_horizon_seconds=900,
            target_instruments=("INSTRUMENT_0", "INSTRUMENT_1"),
            opportunities=(unavailable, _opportunities()[1]),
        )


def test_holdout_feature_timing_must_match_the_frozen_opportunity() -> None:
    selection, _question, _configurations = _selection()
    opportunities = _opportunities()
    bad = opportunities[0]

    def projection(item: HoldoutTargetOpportunity) -> R2HoldoutFeatureRow:
        return R2HoldoutFeatureRow.create(
            opportunity_id=item.opportunity_id,
            target_id=item.target_id,
            instrument_id=item.instrument_id,
            decision_time=item.decision_time,
            feature_cutoff=item.feature_data_asof + timedelta(seconds=1),
            latest_feature_bar_end=item.latest_feature_bar_end,
            feature_schema_id=_id("fixture-schema"),
            values=(1.0,),
        )

    with pytest.raises(ValueError, match="timing"):
        materialise_r2_holdout_features(
            selection=selection,
            opportunities=(bad, *opportunities[1:]),
            feature_schema_id=_id("fixture-schema"),
            feature_set_id=_training_feature_set_id(),
            observation_dataset_id=_id("observations"),
            panel_dataset_id=_id("panel"),
            target_dataset_id=_target_dataset().dataset_id,
            projection=projection,
            allow_disposable_projection=True,
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
        holdout_target_source=_target_source(),
        training_feature_datasets=_training_feature_authority(),
        outcome_loader=lambda: target_dataset,
        evaluator=evaluator,
    )
    assert evaluation is not None
    output = tmp_path / "bundle"
    bundle = write_built_holdout_bundle(
        tmp_path,
        output,
        holdout_target_source=_target_source(),
        training_feature_datasets=_training_feature_authority(),
    )
    assert (
        bundle.bundle_id
        == verify_holdout_bundle(
            output,
            holdout_target_source=_target_source(),
            training_feature_datasets=_training_feature_authority(),
        ).bundle_id
    )
    assert bundle.evaluation.semantic_id == evaluation.evaluation_id
    assert consumed.evaluation_id == evaluation.evaluation_id
    impossible_consumed = R2HoldoutConsumedMarker.create(
        selection_manifest_id=consumed.selection_manifest_id,
        seal_id=consumed.seal_id,
        opened_marker_id=consumed.opened_marker_id,
        consumed_at=NOW - timedelta(seconds=1),
        consumed_by=consumed.consumed_by,
        evaluation_id=consumed.evaluation_id,
    )
    (tmp_path / "consumed.json").write_bytes(canonical_bytes(impossible_consumed.as_json()))
    with pytest.raises(ValueError, match="consumed time must not precede OPENED"):
        verify_holdout_markers(tmp_path)


def test_holdout_preparation_cannot_be_cloned_after_claim(tmp_path: Path) -> None:
    _prepared(tmp_path / "source")
    first = tmp_path / "first"
    second = tmp_path / "second"
    prepare_holdout_from_files(
        tmp_path / "source",
        first,
        holdout_target_source=_target_source(),
        training_feature_datasets=_training_feature_authority(),
    )
    with pytest.raises(FileExistsError, match="transferred"):
        prepare_holdout_from_files(
            tmp_path / "source",
            second,
            holdout_target_source=_target_source(),
            training_feature_datasets=_training_feature_authority(),
        )
    with pytest.raises(FileExistsError, match="transferred"):
        prepare_holdout_from_files(
            first,
            second,
            holdout_target_source=_target_source(),
            training_feature_datasets=_training_feature_authority(),
        )


def test_holdout_transfer_auth_failure_leaves_source_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    _prepared(source)
    claim_path = source / ".preparation-claim.json"
    claim_before = claim_path.read_bytes()
    original_verify = holdout_runtime.verify_holdout_preparation
    calls = 0

    def fail_destination(
        path: Path,
        *,
        _confirmatory_token: object | None = None,
        holdout_target_source: R2HoldoutTargetSource,
        training_feature_datasets: Mapping[str, R2FeatureDataset] | None = None,
        immediate_parent_authority: Mapping[str, object] | None = None,
        _payload_cache: holdout_runtime._PayloadCache | None = None,
        _allow_incomplete_transfer: bool = False,
        _expected_destination_root_id: str | None = None,
    ) -> R2HoldoutForecastSeal:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("destination authentication failed")
        return original_verify(
            path,
            _confirmatory_token=_confirmatory_token,
            holdout_target_source=holdout_target_source,
            training_feature_datasets=training_feature_datasets,
            immediate_parent_authority=immediate_parent_authority,
            _payload_cache=_payload_cache,
            _allow_incomplete_transfer=_allow_incomplete_transfer,
            _expected_destination_root_id=_expected_destination_root_id,
        )

    monkeypatch.setattr(holdout_runtime, "verify_holdout_preparation", fail_destination)
    with pytest.raises(RuntimeError, match="destination authentication"):
        prepare_holdout_from_files(
            source,
            destination,
            holdout_target_source=_target_source(),
            training_feature_datasets=_training_feature_authority(),
        )
    assert calls == 2
    assert claim_path.read_bytes() == claim_before
    assert not (source / ".preparation-source-claim.json").exists()
    assert not destination.exists()


def test_impossible_consumption_chronology_does_not_open_or_claim(tmp_path: Path) -> None:
    selection, _, _, _, _, seal = _prepared(tmp_path)
    claim_before = (tmp_path / ".preparation-claim.json").read_bytes()

    with pytest.raises(ValueError, match="consumed time must not precede OPENED"):
        reveal_holdout(
            tmp_path,
            expected_selection_manifest_id=selection.manifest_id,
            expected_seal_id=seal.seal_id,
            acknowledgement=HOLDOUT_ACKNOWLEDGEMENT,
            opened_by="test",
            consumed_by="test",
            opened_at=NOW,
            consumed_at=NOW - timedelta(seconds=1),
            holdout_target_source=_target_source(),
            training_feature_datasets=_training_feature_authority(),
            outcome_loader=lambda: _target_dataset(),
            evaluator=lambda outcomes, opened: _unexpected_evaluator("evaluator must not run"),
        )

    assert (tmp_path / ".preparation-claim.json").read_bytes() == claim_before
    assert not (tmp_path / "opened.json").exists()
    assert not (tmp_path / "consumed.json").exists()


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
            holdout_target_source=_target_source(),
            training_feature_datasets=_training_feature_authority(),
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
            holdout_target_source=_target_source(),
            training_feature_datasets=_training_feature_authority(),
            outcome_loader=lambda: {},
            evaluator=lambda outcomes, opened: _unexpected_evaluator("second reveal must not load"),
        )


def test_preparation_replays_a_forced_failed_fit(tmp_path: Path) -> None:
    _selection_value, _opportunities_value, fits, _forecasts, _coverage, _seal = _prepared(
        tmp_path,
        forced_failure_configuration=_id("local"),
    )
    assert any(item.disposition is FinalFitDisposition.NUMERICAL_FAILURE for item in fits)
    for fit_path in (tmp_path / "fits").glob("*.json"):
        fit_payload = json.loads(fit_path.read_text())
        assert "training_rows" not in fit_payload
        assert "training_rows" not in fit_payload["preprocessing"]
    assert (
        verify_holdout_preparation(
            tmp_path,
            training_feature_datasets=_training_feature_authority(),
            holdout_target_source=_target_source(),
        ).state.value
        == "PREPARED_UNOPENED"
    )


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


def test_mutated_holdout_part_is_rejected(tmp_path: Path) -> None:
    _prepared(tmp_path)
    part_path = next((tmp_path / "features.json.parts").iterdir())
    payload = json.loads(part_path.read_text())
    payload["rows"][0]["value"] = {"tampered": True}
    part_path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="part digest mismatch"):
        verify_holdout_preparation(
            tmp_path,
            training_feature_datasets=_training_feature_authority(),
            holdout_target_source=_target_source(),
        )


def test_claimed_consumed_preparation_cannot_be_reopened_from_core_files(
    tmp_path: Path,
) -> None:
    selection, _opportunities, _fits, _forecasts, _coverage, seal = _prepared(tmp_path / "source")
    first = tmp_path / "first"
    prepare_holdout_from_files(
        tmp_path / "source",
        first,
        holdout_target_source=_target_source(),
        training_feature_datasets=_training_feature_authority(),
    )
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
            holdout_target_source=_target_source(),
            training_feature_datasets=_training_feature_authority(),
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
        prepare_holdout_from_files(
            stripped,
            tmp_path / "reopened",
            holdout_target_source=_target_source(),
            training_feature_datasets=_training_feature_authority(),
        )


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
            holdout_target_source=_target_source(),
            training_feature_datasets=_training_feature_authority(),
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
    prepare_holdout_from_files(
        tmp_path / "source",
        destination,
        holdout_target_source=_target_source(),
        training_feature_datasets=_training_feature_authority(),
    )

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
        holdout_target_source=_target_source(),
        training_feature_datasets=_training_feature_authority(),
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
            holdout_target_source=_target_source(),
            training_feature_datasets=_training_feature_authority(),
            outcome_loader=lambda: target_dataset,
            evaluator=evaluator,
        )
    assert not (tmp_path / "source" / "opened.json").exists()


def test_preparation_persists_no_training_rows(tmp_path: Path) -> None:
    _prepared(tmp_path)
    assert not (tmp_path / "training").exists()
    fit_path = next((tmp_path / "fits").glob("*.json"))
    fit_payload = json.loads(fit_path.read_text())
    assert "training_rows" not in fit_payload
    assert "training_rows" not in fit_payload["preprocessing"]
    authority = json.loads((tmp_path / "authority.json").read_text())
    assert (
        authority["target_source"]["pre_holdout_target_dataset_id"]
        == _target_source().pre_holdout_target_dataset.dataset_id
    )
    assert "pre_holdout_target_dataset" not in authority
    assert (tmp_path / "features.json.parts").is_dir()
    assert not (tmp_path / "outcome-target.json").exists()


def test_selection_source_artifact_is_outcome_blind() -> None:
    selection, _question, _configurations = _selection()
    policy = selection.evaluation_policy
    forbidden = (
        "holdout_target_source_artifact",
        "pre_holdout_target_dataset",
        "pre_holdout_projection",
        "holdout_opportunity_registry_artifact",
        "holdout_opportunity_registry",
    )
    assert all(field not in policy for field in forbidden)
    source = _target_source()
    projection_id, registry_id, opportunity_count, opportunity_digest = (
        holdout_selection_compact_bindings(source)
    )
    assert policy["holdout_target_source_id"] == source.source_id
    assert policy["pre_holdout_target_dataset_id"] == source.pre_holdout_target_dataset.dataset_id
    assert policy["pre_holdout_projection_id"] == projection_id
    assert policy["holdout_opportunity_registry_id"] == registry_id
    assert policy["holdout_opportunity_count"] == opportunity_count
    assert policy["holdout_opportunity_digest"] == opportunity_digest


def test_compact_opportunity_digest_streams_ordered_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _target_source()

    def forbidden(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("compact digest must not sort or materialise semantic JSON")

    monkeypatch.setattr(builtins, "sorted", forbidden)
    monkeypatch.setattr(holdout_domain, "_semantic_id", forbidden)
    count, digest = holdout_domain.holdout_opportunity_summary(source.opportunities)

    assert count == len(source.opportunities)
    assert digest == holdout_domain.holdout_opportunity_digest(source.opportunities)


def test_compact_selection_path_does_not_materialise_full_children(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("compact selection path must not construct full child artifacts")

    monkeypatch.setattr(R2HoldoutTargetProjection, "create_from_source", forbidden)
    monkeypatch.setattr(R2HoldoutOpportunityRegistry, "create_from_source", forbidden)
    selection, *_ = _prepared(tmp_path)

    assert selection.evaluation_policy["pre_holdout_projection_id"]
    assert selection.evaluation_policy["holdout_opportunity_registry_id"]


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
        holdout_target_source=_target_source(),
        training_feature_datasets=_training_feature_authority(),
        consumed_at=NOW + timedelta(seconds=1),
    )
    assert evaluation is not None
    assert evaluation.evaluation_id
    assert consumed.outcome_accessed is True
    target_part = next((tmp_path / "outcome-target.json.parts").glob("part-*.json"))
    target_payload = json.loads(target_part.read_text())
    target_payload["rows"][0]["log_return"] = 999.0
    target_part.write_text(json.dumps(target_payload))
    with pytest.raises(ValueError, match="part digest mismatch"):
        verify_holdout_evaluation(
            tmp_path,
            training_feature_datasets=_training_feature_authority(),
            holdout_target_source=_target_source(),
        )


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
    assert verify_holdout_preparation(
        tmp_path,
        holdout_target_source=_target_source(),
        training_feature_datasets=_training_feature_authority(),
    ).seal_id


def test_holdout_semantic_identity_excludes_runtime_provenance() -> None:
    from dataclasses import fields

    selection, question, _ = _selection()

    def rebuild(**changes: object) -> R2HoldoutSelectionManifest:
        values = {
            item.name: getattr(selection, item.name)
            for item in fields(selection)
            if item.name != "manifest_id"
        }
        return R2HoldoutSelectionManifest.create(**{**values, **changes})

    provenance_only = rebuild(
        foundation_bundle_id=_id("different-foundation"),
        runtime_identities={"application": "different-build"},
        frozen_metadata={"fixture": "different"},
        frozen_at=NOW + timedelta(minutes=1),
        frozen_by="different-operator",
    )
    assert provenance_only.manifest_id == selection.manifest_id
    assert provenance_only.semantic_json() == selection.semantic_json()

    changed_threshold = rebuild(threshold_policy={"threshold": 0.1})
    assert changed_threshold.manifest_id != selection.manifest_id

    changed_question = R2HoldoutQuestion.create(
        question=question.question + " (changed)",
        candidate_configuration_id=question.candidate_configuration_id,
        comparator_configuration_id=question.comparator_configuration_id,
        metric=question.metric,
        support_policy=question.support_policy,
        direction=question.direction,
        threshold=question.threshold,
        minimum_support=question.minimum_support,
        minimum_coverage=question.minimum_coverage,
        conclusion_policy=question.conclusion_policy,
    )
    changed_questions = rebuild(questions=(changed_question,))
    assert changed_questions.manifest_id != selection.manifest_id


def test_final_fit_and_seal_identity_exclude_build_provenance(tmp_path: Path) -> None:
    from dataclasses import fields

    _selection_value, _opportunities, fits, _forecasts, _coverage, seal = _prepared(tmp_path)
    fit = fits[0]
    fit_values = {
        item.name: getattr(fit, item.name) for item in fields(fit) if item.name != "fit_id"
    }
    alternate_fit = R2FinalFit.create(
        **{**fit_values, "runtime_identities": {"application": "different-build"}}
    )
    assert alternate_fit.fit_id == fit.fit_id
    assert alternate_fit.semantic_json() == fit.semantic_json()

    physical_preprocessing = dict(fit.preprocessing)
    physical_preprocessing["foundation_bundle_id"] = _id("different-foundation")
    alternate_foundation = R2FinalFit.create(
        **{**fit_values, "preprocessing": physical_preprocessing}
    )
    assert alternate_foundation.fit_id == fit.fit_id
    assert alternate_foundation.semantic_json() == fit.semantic_json()
    assert alternate_foundation.as_json()["preprocessing"] != fit.as_json()["preprocessing"]

    scientific_preprocessing = dict(fit.preprocessing)
    scientific_preprocessing["training_feature_dataset_id"] = _id("different-training-features")
    changed_preprocessing = R2FinalFit.create(
        **{**fit_values, "preprocessing": scientific_preprocessing}
    )
    assert changed_preprocessing.fit_id != fit.fit_id

    changed_selection = R2FinalFit.create(
        **{**fit_values, "selection_manifest_id": _id("different-selection")}
    )
    assert changed_selection.fit_id != fit.fit_id

    seal_values = {
        item.name: getattr(seal, item.name) for item in fields(seal) if item.name != "seal_id"
    }
    alternate_seal = type(seal).create(
        **{
            **seal_values,
            "runtime_identities": {"application": "different-build"},
            "prepared_at": NOW + timedelta(minutes=1),
            "prepared_by": "different-operator",
        }
    )
    assert alternate_seal.seal_id == seal.seal_id
    assert alternate_seal.semantic_json() == seal.semantic_json()


def test_transfer_publish_crash_leaves_resumable_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    _prepared(source)
    original_replace = holdout_runtime.os.replace

    def fail_final_publish(source_path: Path, destination_path: Path) -> None:
        if Path(destination_path) == destination and Path(source_path).is_dir():
            raise RuntimeError("simulated final publish crash")
        original_replace(source_path, destination_path)

    monkeypatch.setattr(holdout_runtime.os, "replace", fail_final_publish)
    with pytest.raises(RuntimeError, match="final publish crash"):
        prepare_holdout_from_files(
            source,
            destination,
            holdout_target_source=_target_source(),
            training_feature_datasets=_training_feature_authority(),
        )
    claim = json.loads((source / ".preparation-claim.json").read_text())
    assert claim["state"] == "TRANSFERRED"
    staging = tuple(tmp_path.glob(".destination.transfer-*"))
    assert len(staging) == 1
    assert not destination.exists()

    monkeypatch.setattr(holdout_runtime.os, "replace", original_replace)
    seal = prepare_holdout_from_files(
        source,
        destination,
        holdout_target_source=_target_source(),
        training_feature_datasets=_training_feature_authority(),
    )
    assert seal.seal_id
    assert destination.is_dir()
    assert not staging[0].exists()
    assert (
        verify_holdout_preparation(
            destination,
            holdout_target_source=_target_source(),
            training_feature_datasets=_training_feature_authority(),
        ).seal_id
        == seal.seal_id
    )


def test_transfer_claim_crash_leaves_resumable_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    _prepared(source)
    original_write = holdout_runtime._write_json
    source_usage = source / ".preparation-source-claim.json"

    def fail_source_usage(path: Path, payload: dict[str, object]) -> None:
        if path == source_usage:
            raise RuntimeError("simulated source usage crash")
        original_write(path, payload)

    monkeypatch.setattr(holdout_runtime, "_write_json", fail_source_usage)
    with pytest.raises(RuntimeError, match="source usage crash"):
        prepare_holdout_from_files(
            source,
            destination,
            holdout_target_source=_target_source(),
            training_feature_datasets=_training_feature_authority(),
        )
    assert json.loads((source / ".preparation-claim.json").read_text())["state"] == "TRANSFERRED"
    assert not source_usage.exists()
    assert tuple(tmp_path.glob(".destination.transfer-*"))
    alternate = tmp_path / "alternate"
    with pytest.raises(FileExistsError, match="another destination"):
        prepare_holdout_from_files(
            source,
            alternate,
            holdout_target_source=_target_source(),
            training_feature_datasets=_training_feature_authority(),
        )
    assert not alternate.exists()

    monkeypatch.setattr(holdout_runtime, "_write_json", original_write)
    seal = prepare_holdout_from_files(
        source,
        destination,
        holdout_target_source=_target_source(),
        training_feature_datasets=_training_feature_authority(),
    )
    assert destination.is_dir()
    assert seal.seal_id


def test_transfer_claim_publication_crash_resumes_exact_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    _prepared(source)
    original_replace = holdout_runtime.os.replace
    claim_path = source / ".preparation-claim.json"
    claim_next = source / "..preparation-claim.json.next"

    def fail_claim_publication(source_path: Path, destination_path: Path) -> None:
        if (
            Path(destination_path) == claim_path
            and Path(source_path).name == "..preparation-claim.json.next"
        ):
            raise RuntimeError("simulated claim publication crash")
        original_replace(source_path, destination_path)

    monkeypatch.setattr(holdout_runtime.os, "replace", fail_claim_publication)
    with pytest.raises(RuntimeError, match="claim publication crash"):
        prepare_holdout_from_files(
            source,
            destination,
            holdout_target_source=_target_source(),
            training_feature_datasets=_training_feature_authority(),
        )
    assert json.loads(claim_path.read_text())["state"] == "OWNED_UNOPENED"
    assert claim_next.is_file()
    assert not destination.exists()

    monkeypatch.setattr(holdout_runtime.os, "replace", original_replace)
    seal = prepare_holdout_from_files(
        source,
        destination,
        holdout_target_source=_target_source(),
        training_feature_datasets=_training_feature_authority(),
    )
    assert destination.is_dir()
    assert seal.seal_id
    assert not claim_next.exists()


def test_transferred_preparation_rejects_moved_destination_root(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    moved = tmp_path / "moved"
    _prepared(source)
    prepare_holdout_from_files(
        source,
        destination,
        holdout_target_source=_target_source(),
        training_feature_datasets=_training_feature_authority(),
    )
    destination.rename(moved)
    with pytest.raises(ValueError, match="destination identity"):
        verify_holdout_preparation(
            moved,
            holdout_target_source=_target_source(),
            training_feature_datasets=_training_feature_authority(),
        )


def test_completed_output_requires_source_usage_authority(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    _prepared(source)
    prepare_holdout_from_files(
        source,
        destination,
        holdout_target_source=_target_source(),
        training_feature_datasets=_training_feature_authority(),
    )
    (source / ".preparation-source-claim.json").unlink()
    (source / ".preparation-transfer-intent.json").unlink()
    with pytest.raises(ValueError, match="authenticated transfer intent"):
        prepare_holdout_from_files(
            source,
            destination,
            holdout_target_source=_target_source(),
            training_feature_datasets=_training_feature_authority(),
        )
    assert not (source / ".preparation-source-claim.json").exists()


def test_completed_output_repairs_missing_source_usage_from_intent(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    _prepared(source)
    prepare_holdout_from_files(
        source,
        destination,
        holdout_target_source=_target_source(),
        training_feature_datasets=_training_feature_authority(),
    )
    source_usage = source / ".preparation-source-claim.json"
    source_usage.unlink()
    seal = prepare_holdout_from_files(
        source,
        destination,
        holdout_target_source=_target_source(),
        training_feature_datasets=_training_feature_authority(),
    )
    assert seal.seal_id
    assert source_usage.is_file()


def test_reveal_claim_publication_crash_resumes_exact_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    selection, _opportunities, _fits, _forecasts, _coverage, _source_seal = _prepared(source)
    seal = prepare_holdout_from_files(
        source,
        destination,
        holdout_target_source=_target_source(),
        training_feature_datasets=_training_feature_authority(),
    )
    claim_path = destination / ".preparation-claim.json"
    claim_next = destination / "..preparation-claim.json.next"
    original_replace = holdout_runtime.os.replace

    def fail_claim_publication(source_path: Path, destination_path: Path) -> None:
        if (
            Path(destination_path) == claim_path
            and Path(source_path).name == "..preparation-claim.json.next"
        ):
            raise RuntimeError("simulated reveal claim publication crash")
        original_replace(source_path, destination_path)

    monkeypatch.setattr(holdout_runtime.os, "replace", fail_claim_publication)
    with pytest.raises(RuntimeError, match="reveal claim publication crash"):
        holdout_runtime._claim_preparation(destination, selection.manifest_id, seal.seal_id)
    assert json.loads(claim_path.read_text())["state"] == "OWNED_UNOPENED"
    assert claim_next.is_file()
    assert (
        verify_holdout_preparation(
            destination,
            holdout_target_source=_target_source(),
            training_feature_datasets=_training_feature_authority(),
        ).seal_id
        == seal.seal_id
    )

    monkeypatch.setattr(holdout_runtime.os, "replace", original_replace)
    holdout_runtime._claim_preparation(destination, selection.manifest_id, seal.seal_id)
    assert json.loads(claim_path.read_text())["state"] == "OWNED_OPENED"
    assert not claim_next.exists()


def test_terminal_outcomes_round_trip_with_forced_part_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selection, _opportunities, _fits, forecasts, coverage, seal = _prepared(tmp_path)
    target_dataset = _target_dataset()
    original_bound = partitioned_rows_runtime._MAX_PART_BYTES
    original_verify = holdout_runtime.verify_holdout_preparation

    def verify_then_lower(
        path: Path,
        *,
        _confirmatory_token: object | None = None,
        holdout_target_source: R2HoldoutTargetSource,
        training_feature_datasets: Mapping[str, R2FeatureDataset] | None = None,
        immediate_parent_authority: Mapping[str, object] | None = None,
        _payload_cache: holdout_runtime._PayloadCache | None = None,
        _allow_incomplete_transfer: bool = False,
        _expected_destination_root_id: str | None = None,
    ) -> R2HoldoutForecastSeal:
        result = original_verify(
            path,
            _confirmatory_token=_confirmatory_token,
            holdout_target_source=holdout_target_source,
            training_feature_datasets=training_feature_datasets,
            immediate_parent_authority=immediate_parent_authority,
            _payload_cache=_payload_cache,
            _allow_incomplete_transfer=_allow_incomplete_transfer,
            _expected_destination_root_id=_expected_destination_root_id,
        )
        monkeypatch.setattr(partitioned_rows_runtime, "_MAX_PART_BYTES", 900)
        return result

    monkeypatch.setattr(holdout_runtime, "verify_holdout_preparation", verify_then_lower)
    monkeypatch.setattr(partitioned_rows_runtime, "_MAX_PART_BYTES", original_bound)

    def evaluator(outcomes: Mapping[str, float], opened: R2HoldoutOpenedMarker):
        return evaluate_holdout(
            selection=selection,
            seal=seal,
            opened_marker=opened,
            forecast_datasets=forecasts,
            coverage_datasets=coverage,
            outcomes=outcomes,
        )

    reveal_holdout(
        tmp_path,
        expected_selection_manifest_id=selection.manifest_id,
        expected_seal_id=seal.seal_id,
        acknowledgement=HOLDOUT_ACKNOWLEDGEMENT,
        opened_by="test",
        consumed_by="test",
        opened_at=NOW,
        consumed_at=NOW + timedelta(seconds=1),
        holdout_target_source=_target_source(),
        training_feature_datasets=_training_feature_authority(),
        outcome_loader=lambda: target_dataset,
        evaluator=evaluator,
    )
    target_parts = tuple((tmp_path / "outcome-target.json.parts").glob("*.json"))
    evidence_parts = tuple((tmp_path / "outcome-evidence.json.parts").glob("*.json"))
    assert len(target_parts) > 1
    assert len(evidence_parts) > 1
    monkeypatch.setattr(partitioned_rows_runtime, "_MAX_PART_BYTES", original_bound)
    assert verify_holdout_evaluation(
        tmp_path,
        holdout_target_source=_target_source(),
        training_feature_datasets=_training_feature_authority(),
    ).evaluation_id
