"""Fixture-only regression tests for the confirmatory F2-to-G1 boundary."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest

import qtrad.runtime.r2_verification as verification
from qtrad.application.r2_features import build_raw_feature_rows, feature_schema_for_set
from qtrad.domain.events import JsonValue
from qtrad.domain.folds import FoldDataset
from qtrad.domain.foundation import PanelRow, PanelStatus
from qtrad.domain.market_data import BarProvenance, DataQuality, PriceBasis
from qtrad.domain.r2_features import R2FeatureDataset, feature_set_id
from qtrad.domain.r2_holdout import (
    HoldoutScope,
    R2HoldoutQuestion,
    R2HoldoutSelectionManifest,
    R2HoldoutTargetSource,
    R2OutcomeBlindTargetView,
)
from qtrad.domain.r2_readiness import (
    EvidenceClass,
    FeatureFamily,
    MarketDataSourceClass,
    ModelFamily,
    R2ReadinessReport,
    ReadinessState,
)
from qtrad.domain.research import ObservationRow
from qtrad.ports.clock import Clock
from qtrad.runtime.r2_bundles import canonical_bytes
from qtrad.runtime.r2_verification import (
    CONFIRMATORY_RUN_KIND,
    VerifiedConfirmatoryF2,
    _build_synthetic_oof,
    _materialise_synthetic_feature_manifests,
    _synthetic_pipeline_inputs,
    build_oof_bundle,
    freeze_confirmatory_selection,
    verify_confirmatory_f2,
)


def _build_confirmatory_fixture(
    root: Path,
    *,
    qualifying: bool = False,
) -> tuple[
    Path,
    Any,
    FoldDataset,
    dict[str, tuple[tuple[datetime, datetime], ...]],
]:
    target_names = (
        tuple(f"index:synthetic-{index}" for index in range(6))
        if qualifying
        else ("index:synthetic-a", "index:synthetic-b")
    )
    verified, experiment, datasets = _synthetic_pipeline_inputs(
        target_names=target_names,
        market_groups=(
            {name: f"group-{index // 2}" for index, name in enumerate(target_names)}
            if qualifying
            else None
        ),
        evidence_class=EvidenceClass.CONFIRMATORY,
        include_holdout_target=not qualifying,
        qualifying_confirmatory=qualifying,
        market_data_source_class=(
            MarketDataSourceClass.IG_NATIVE_CAPTURE
            if qualifying
            else MarketDataSourceClass.IBKR_HISTORICAL_RESEARCH
        ),
    )
    fixture_verified = cast(Any, verified)
    start: datetime = (
        cast(datetime, fixture_verified.bundle.range_start)
        if qualifying
        else experiment.holdout_range[0] - timedelta(hours=6)
    )
    active_start = start - experiment.feature_windows[-1] - timedelta(minutes=1)
    active_end = experiment.holdout_range[0]
    observations: list[ObservationRow] = []
    position = 1
    interval_ends = (
        sorted(
            {
                target.decision_time - timedelta(minutes=offset)
                for target in fixture_verified.targets.rows
                for offset in range(6)
            }
        )
        if qualifying
        else [
            active_start + timedelta(minutes=offset + 1)
            for offset in range(int((active_end - active_start).total_seconds() // 60))
        ]
    )
    for interval_end in interval_ends:
        current = interval_end - timedelta(minutes=1)
        for instrument in experiment.ordered_instruments:
            close = Decimal("100") + Decimal(position) / Decimal("100")
            observations.append(
                ObservationRow(
                    event_id=UUID(int=position),
                    stream_id=f"market-bar:{instrument}:{PriceBasis.MID}",
                    stream_version=position,
                    event_type="MarketBarClosed",
                    event_time=interval_end,
                    received_at=interval_end,
                    persisted_at=interval_end,
                    global_position=position,
                    instrument_id=instrument,
                    basis=PriceBasis.MID,
                    interval_start=current,
                    interval_end=interval_end,
                    open=close,
                    high=close + Decimal("0.1"),
                    low=close - Decimal("0.1"),
                    close=close,
                    sample_count=1,
                    revision=1,
                    provenance=BarProvenance.IBKR_HISTORICAL,
                    quality=DataQuality.HEALTHY,
                    source_provider="fixture",
                    source_environment="test",
                    source_external_id=instrument,
                )
            )
            position += 1
    by_key = {(row.instrument_id, row.interval_end): row for row in observations}
    panels = tuple(
        PanelRow(
            decision_time=target.decision_time,
            instrument_id=target.instrument_id,
            basis=PriceBasis.MID,
            feature_data_asof=target.decision_time,
            latest_feature_bar_end=target.decision_time,
            status=PanelStatus.OBSERVED,
            audit_disposition=None,
            selected_event_id=by_key[(target.instrument_id, target.decision_time)].event_id,
            selected_stream_version=by_key[
                (target.instrument_id, target.decision_time)
            ].stream_version,
            selected_global_position=by_key[
                (target.instrument_id, target.decision_time)
            ].global_position,
            selected_availability_time=target.decision_time,
            selected_revision=1,
            interval_start=by_key[(target.instrument_id, target.decision_time)].interval_start,
            interval_end=target.decision_time,
            open=by_key[(target.instrument_id, target.decision_time)].open,
            high=by_key[(target.instrument_id, target.decision_time)].high,
            low=by_key[(target.instrument_id, target.decision_time)].low,
            close=by_key[(target.instrument_id, target.decision_time)].close,
            sample_count=1,
            quality=DataQuality.HEALTHY,
        )
        for target in fixture_verified.targets.rows
    )
    fixture_verified.configuration.grid_resolution = experiment.feature_windows[0]
    fixture_verified.observations.rows = tuple(observations)
    fixture_verified.panel.rows = panels
    active_intervals: dict[str, tuple[tuple[datetime, datetime], ...]] = {
        instrument: (
            (
                active_start,
                experiment.holdout_range[1],
            ),
        )
        for instrument in experiment.ordered_instruments
    }
    fixture_verified.availability_evidence["source_active_intervals"] = {
        instrument: [[start.isoformat(), end.isoformat()] for start, end in intervals]
        for instrument, intervals in active_intervals.items()
    }
    fixture_verified.availability_evidence["observation_bounds"]["interval_start"] = (
        active_start.isoformat()
    )
    fixture_verified.source_active_intervals = active_intervals
    fixture_verified.bundle.availability.dataset_id = verification._availability_dataset_id(
        fixture_verified.observations.dataset_id,
        fixture_verified.availability_evidence,
    )
    foundation = verification._foundation_inputs(fixture_verified)
    datasets = {}
    for feature_set in experiment.feature_sets:
        schema = feature_schema_for_set(experiment, feature_set.name)
        rows = build_raw_feature_rows(
            foundation,
            experiment,
            feature_set_name=feature_set.name,
        )
        datasets[feature_set.name] = R2FeatureDataset.create(
            rows,
            feature_schema=schema,
            feature_set_name=feature_set.name,
            feature_set_id=feature_set_id(
                experiment.configuration_id,
                feature_set.name,
                schema,
                experiment.market_data_source_class,
            ),
            observation_dataset_id=foundation.observations.dataset_id,
            panel_dataset_id=foundation.panel.dataset_id,
            target_dataset_id=foundation.targets.dataset_id,
            fold_dataset_id=foundation.folds.dataset_id,
            experiment_configuration_id=experiment.configuration_id,
            evidence_class=experiment.evidence_class,
            market_data_source_class=experiment.market_data_source_class,
        )
    research_root = root / "research"
    research_root.mkdir(parents=True)
    feature_paths = _materialise_synthetic_feature_manifests(research_root, experiment, datasets)
    foundation_path = research_root / "foundation.json"
    foundation_path.write_bytes(b"{}")
    experiment_path = root / "experiment.json"
    experiment_path.write_bytes(canonical_bytes(cast(dict[str, object], experiment.as_json())))
    target_source = R2HoldoutTargetSource.create_from_target_dataset(
        fixture_verified.targets,
        holdout_range=experiment.holdout_range,
        primary_horizon_seconds=int(experiment.primary_horizon.total_seconds()),
        target_instruments=experiment.target_instruments,
        panel=fixture_verified.panel,
        source_active_intervals=active_intervals,
        availability_evidence_id=fixture_verified.bundle.availability.dataset_id,
    )
    fixture_verified.targets = R2OutcomeBlindTargetView.from_source(target_source)
    bundle_path = build_oof_bundle(
        verified=fixture_verified,
        experiment=experiment,
        feature_manifest_paths=feature_paths,
        research_root=research_root,
        clock=cast(Clock, SimpleNamespace(now=lambda: experiment.holdout_range[0])),
        output=root / "oof",
        run_kind=CONFIRMATORY_RUN_KIND,
        representative_profile=(None if qualifying else verification.IBKR_HISTORICAL_PROFILE),
        replay_inputs={
            "foundation": foundation_path,
            "experiment": experiment_path,
            **{name: research_root / path for name, path in feature_paths.items()},
        },
        holdout_target_source=target_source,
    )
    return (
        bundle_path,
        fixture_verified,
        cast(FoldDataset, fixture_verified.folds),
        active_intervals,
    )


def test_confirmatory_run_and_evidence_classes_are_strict() -> None:
    verified, implementation, _ = _synthetic_pipeline_inputs()
    with pytest.raises(ValueError, match="CONFIRMATORY OOF runs require CONFIRMATORY evidence"):
        build_oof_bundle(
            verified=verified,
            experiment=implementation,
            feature_manifest_paths={},
            research_root=Path("."),
            clock=cast(Clock, SimpleNamespace(now=lambda: implementation.holdout_range[0])),
            output=Path("unused"),
            run_kind=CONFIRMATORY_RUN_KIND,
        )
    confirmatory_verified, confirmatory, _ = _synthetic_pipeline_inputs(
        evidence_class=EvidenceClass.CONFIRMATORY
    )
    with pytest.raises(ValueError, match="requires the CONFIRMATORY OOF run kind"):
        build_oof_bundle(
            verified=confirmatory_verified,
            experiment=confirmatory,
            feature_manifest_paths={},
            research_root=Path("."),
            clock=cast(Clock, SimpleNamespace(now=lambda: confirmatory.holdout_range[0])),
            output=Path("unused"),
            run_kind="REPRESENTATIVE",
        )


def test_qualifying_confirmatory_f2_runs_real_oof_replay_and_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identities = {
        "application_identity": "fixture-application",
        "image_identity": "sha256:" + "1" * 64,
        "python_identity": "fixture-python",
        "numpy_identity": "fixture-numpy",
        "sklearn_identity": "fixture-sklearn",
    }
    monkeypatch.setattr(verification, "runtime_identities", lambda: identities)
    bundle_path, fixture_verified, _, _ = _build_confirmatory_fixture(
        tmp_path,
        qualifying=True,
    )

    async def load_fixture_foundation(**_: object) -> Any:
        return fixture_verified

    monkeypatch.setattr(
        verification,
        "verify_outcome_blind_foundation_bundle",
        load_fixture_foundation,
    )

    authority = verify_confirmatory_f2(bundle_path)

    assert authority.readiness_report.confirmatory_data_ready is ReadinessState.READY
    assert authority.readiness_report.inner_validation_rows_ready is ReadinessState.READY
    assert authority.readiness_report.confirmatory_oof_ready is ReadinessState.READY
    assert authority.readiness_report.usable_common_week_count == 16


def test_confirmatory_f2_is_constructed_by_verifier_and_freezes_without_full_target_decode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        verification,
        "runtime_identities",
        lambda: {
            "application_identity": "fixture-application",
            "image_identity": "sha256:" + "1" * 64,
            "python_identity": "fixture-python",
            "numpy_identity": "fixture-numpy",
            "sklearn_identity": "fixture-sklearn",
        },
    )
    bundle_path, _, fixture_folds, active_intervals = _build_confirmatory_fixture(tmp_path)
    monkeypatch.setattr(
        verification,
        "_replay_confirmatory_oof",
        lambda _: (fixture_folds, active_intervals),
    )

    def ready_report(**kwargs: object) -> R2ReadinessReport:
        experiment = cast(Any, kwargs["experiment"])
        return R2ReadinessReport(
            experiment_configuration_id=experiment.configuration_id,
            r1_bundle_id=experiment.r1_bundle_id,
            software_contract_ready=ReadinessState.READY,
            representative_integration_ready=ReadinessState.READY,
            confirmatory_data_ready=ReadinessState.READY,
            inner_validation_rows_ready=ReadinessState.PARTIALLY_READY,
            confirmatory_oof_ready=ReadinessState.NOT_READY,
            locked_holdout_ready=ReadinessState.NOT_READY,
            feature_family_states={family: ReadinessState.READY for family in FeatureFamily},
            coverage_matrix={},
            usable_common_week_count=16,
            active_source_duration_seconds={},
            unmet_conditions=(),
            evidence_class=experiment.evidence_class,
            market_data_source_class=experiment.market_data_source_class,
        )

    monkeypatch.setattr(
        verification,
        "evaluate_outcome_blind_confirmatory_readiness",
        lambda **kwargs: replace(
            ready_report(**kwargs),
            confirmatory_data_ready=ReadinessState.NOT_READY,
            unmet_conditions=("fixture readiness gate",),
        ),
    )
    with pytest.raises(ValueError, match="independently replayed outcome-blind readiness"):
        verify_confirmatory_f2(bundle_path)
    monkeypatch.setattr(verification, "evaluate_outcome_blind_confirmatory_readiness", ready_report)

    authority = verify_confirmatory_f2(bundle_path)

    assert type(authority) is VerifiedConfirmatoryF2
    with pytest.raises(TypeError, match="constructed only by verify_confirmatory_f2"):
        VerifiedConfirmatoryF2()
    assert authority.evidence_class is EvidenceClass.CONFIRMATORY
    assert authority.descriptor["run_kind"] == CONFIRMATORY_RUN_KIND
    with pytest.raises(TypeError):
        cast(Any, authority.descriptor)["run_kind"] = "REPRESENTATIVE"
    with pytest.raises(TypeError):
        cast(Any, authority.selection_policy)["primary_metric"] = "RMSE"
    with pytest.raises(TypeError):
        cast(Any, authority.selection_policy["acceptance_thresholds"])[0][0] = "tampered"
    with pytest.raises(TypeError):
        cast(Any, authority.experiment.acceptance_thresholds)["tampered"] = 0.0
    with pytest.raises(TypeError):
        cast(Any, authority.readiness_report.feature_family_states)[FeatureFamily.LOCAL_RETURNS] = (
            ReadinessState.NOT_READY
        )

    with pytest.raises(ValueError, match="require verify_confirmatory_f2"):
        verification.verify_oof_bundle(bundle_path)

    def reject_full_target_decoder(_: Path) -> dict[str, object]:
        raise AssertionError("confirmatory G1 decoded a holdout-bearing child")

    monkeypatch.setattr(verification, "_load_selection", reject_full_target_decoder)
    selection_path = tmp_path / "selection.json"
    captured_freeze: dict[str, Any] = {}
    shared_freeze = verification.freeze_holdout_selection

    def capture_freeze(**kwargs: Any) -> R2HoldoutSelectionManifest:
        captured_freeze.update(kwargs)
        return shared_freeze(**kwargs)

    monkeypatch.setattr(verification, "freeze_holdout_selection", capture_freeze)
    freeze_confirmatory_selection(
        verified_f2=authority,
        output=selection_path,
        frozen_by="fixture-operator",
    )
    selection = json.loads(selection_path.read_bytes())
    assert selection["holdout_scope"] == HoldoutScope.CONFIRMATORY.value
    assert selection["evidence_class"] == EvidenceClass.CONFIRMATORY.value
    assert selection["holdout_outcomes_accessed"] is False
    assert selection["questions"][0]["support_policy"] == "COMMON_ELIGIBLE"
    assert selection["questions"][0]["metric"] == "INSTRUMENT_BALANCED_COMMON_SUPPORT_MSE"
    family_by_configuration = {item[0]: item[1] for item in selection["configuration_registry"]}
    assert [
        (
            family_by_configuration[item["candidate_configuration_id"]],
            family_by_configuration[item["comparator_configuration_id"]],
        )
        for item in selection["questions"]
    ] == [
        ("LOCAL_RIDGE", "ZERO_RETURN"),
        ("POOLED_LOCAL_RIDGE", "LOCAL_RIDGE"),
        ("POOLED_CROSS_ASSET_RIDGE", "POOLED_LOCAL_RIDGE"),
    ]
    pooled_local_id = next(
        configuration_id
        for configuration_id, family in family_by_configuration.items()
        if family == "POOLED_LOCAL_RIDGE"
    )
    assert pooled_local_id not in selection["selected_configuration_ids"]
    assert selection["questions"][2]["comparator_configuration_id"] == pooled_local_id

    configuration_ids_by_family = {
        family: configuration_id for configuration_id, family in family_by_configuration.items()
    }
    frozen_questions = cast(tuple[R2HoldoutQuestion, ...], captured_freeze["questions"])
    source_question = frozen_questions[-1]
    invented_question = R2HoldoutQuestion.create(
        question="invented P1 versus zero comparison",
        candidate_configuration_id=configuration_ids_by_family["POOLED_CROSS_ASSET_RIDGE"],
        comparator_configuration_id=configuration_ids_by_family["ZERO_RETURN"],
        metric=source_question.metric,
        direction=source_question.direction,
        threshold=source_question.threshold,
        minimum_support=source_question.minimum_support,
        minimum_coverage=source_question.minimum_coverage,
        support_policy=source_question.support_policy,
        conclusion_policy=source_question.conclusion_policy,
    )
    with pytest.raises(
        ValueError,
        match="confirmatory question is not an authenticated immediate comparison",
    ):
        cast(Any, shared_freeze)(**{**captured_freeze, "questions": (invented_question,)})
    tampered_evaluation_policy = dict(
        cast(Mapping[str, JsonValue], captured_freeze["evaluation_policy"])
    )
    tampered_evaluation_policy["comparison_registry"] = [
        *cast(list[JsonValue], tampered_evaluation_policy["comparison_registry"]),
        ["POOLED_CROSS_ASSET_RIDGE", "ZERO_RETURN"],
    ]
    with pytest.raises(
        ValueError,
        match="evaluation policy differs from verifier-only confirmatory authority",
    ):
        cast(Any, shared_freeze)(
            **{
                **captured_freeze,
                "questions": (invented_question,),
                "evaluation_policy": tampered_evaluation_policy,
            }
        )
    tampered_configuration_registry = tuple(
        (
            configuration_id,
            (ModelFamily.ZERO_RETURN if family is ModelFamily.POOLED_CROSS_ASSET_RIDGE else family),
            feature_set_id,
            feature_dataset_id,
            manifest_id,
        )
        for (
            configuration_id,
            family,
            feature_set_id,
            feature_dataset_id,
            manifest_id,
        ) in cast(
            tuple[
                tuple[str, ModelFamily, str | None, str | None, str | None],
                ...,
            ],
            captured_freeze["configuration_registry"],
        )
    )
    with pytest.raises(
        ValueError,
        match="configuration registry differs from verifier-only confirmatory authority",
    ):
        cast(Any, shared_freeze)(
            **{
                **captured_freeze,
                "configuration_registry": tampered_configuration_registry,
            }
        )
    assert (
        selection["final_fitting_policy"]["pooled_membership_policy"]
        == "FIXED_UNIVERSE_OUTER_INNER_FIT_VALIDATION_V1"
    )
    assert selection["final_fitting_policy"]["instrument_intercept_policy"] == (
        "NO_GLOBAL_INTERCEPT_V1"
    )
    assert selection["final_fitting_policy"]["runtime_identities"]["instrument_identity_order"] == [
        "index:synthetic-a",
        "index:synthetic-b",
    ]

    with pytest.raises(FileExistsError):
        freeze_confirmatory_selection(
            verified_f2=authority,
            output=selection_path,
            frozen_by="fixture-operator",
        )


def test_confirmatory_verifier_rejects_implementation_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        verification,
        "runtime_identities",
        lambda: {
            "application_identity": "fixture-application",
            "image_identity": "sha256:" + "1" * 64,
            "python_identity": "fixture-python",
            "numpy_identity": "fixture-numpy",
            "sklearn_identity": "fixture-sklearn",
        },
    )
    implementation = _build_synthetic_oof(tmp_path / "implementation" / "oof")
    with pytest.raises(ValueError, match="CONFIRMATORY evidence"):
        verify_confirmatory_f2(implementation)
