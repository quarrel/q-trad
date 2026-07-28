from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import TypedDict, cast
from uuid import UUID

import pytest
from numpy import ndarray

from qtrad.application.r2_preprocessing import (
    TrainingRow,
    build_r2_preprocessing_selection,
    equal_instrument_total_weights,
    fit_preprocessing,
    select_chronological_alpha,
    transform,
)
from qtrad.application.r2_readiness import R1FoundationBindings, _availability_dataset_id
from qtrad.domain.events import JsonValue
from qtrad.domain.folds import Fold, FoldDataset, membership_hash
from qtrad.domain.foundation import (
    AvailabilityBasis,
    ExcursionDisposition,
    ReturnDisposition,
    TargetDataset,
    TargetRow,
)
from qtrad.domain.market_data import PriceBasis
from qtrad.domain.r2_features import (
    FeatureDefinition,
    R2FeatureDataset,
    RawFeatureRow,
    RawFeatureValue,
    feature_set_id,
)
from qtrad.domain.r2_models import (
    AlphaSelection,
    FitDisposition,
    R2PreprocessingSelection,
)
from qtrad.domain.r2_readiness import EvidenceClass, FeatureFamily, R2ExperimentConfig
from qtrad.runtime.r2_preprocessing_selection import (
    decode_r2_preprocessing_selection,
    serialize_r2_preprocessing_selection,
    verify_r2_preprocessing_selection,
)
from tests.test_r2_readiness import END, START
from tests.test_r2_readiness import experiment as base_experiment


class _SelectionKwargs(TypedDict):
    feature_schema: tuple[FeatureDefinition, ...]
    alpha_grid: tuple[float, ...]
    minimum_training_rows: int
    minimum_inner_validation_rows: int
    ridge_solver: str
    ridge_tolerance: float
    ridge_max_iterations: int
    loss_policy: str
    pooled_weighting_policy: str


_SCHEMA = (
    FeatureDefinition("signal", FeatureFamily.LOCAL_RETURNS),
    FeatureDefinition("signal_available", FeatureFamily.LOCAL_RETURNS, True),
    FeatureDefinition("all_null", FeatureFamily.LOCAL_RETURNS),
    FeatureDefinition("constant", FeatureFamily.LOCAL_RETURNS),
)


def _training_rows() -> tuple[TrainingRow, ...]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows: list[TrainingRow] = []
    for index in range(8):
        decision = start + timedelta(minutes=10 * index)
        target_available_at = decision + timedelta(minutes=15)
        rows.append(
            TrainingRow(
                f"t{index}",
                decision,
                target_available_at,
                target_available_at,
                "a" if index < 6 else "b",
                (float(index), float(index % 2), None, 7.0),
                float(index % 3),
            )
        )
    return tuple(rows)


def _select(rows: tuple[TrainingRow, ...]) -> AlphaSelection:
    return select_chronological_alpha(
        rows,
        feature_schema=_SCHEMA,
        alpha_grid=(0.1, 1.0, 10.0),
        minimum_training_rows=4,
        minimum_inner_validation_rows=2,
        ridge_solver="lsqr",
        ridge_tolerance=1e-8,
        ridge_max_iterations=1_000,
        loss_policy="OOF_PRIMARY_MSE_V1",
        pooled_weighting_policy="EQUAL_INSTRUMENT_TOTAL_WEIGHT_MEAN_ONE",
    )


def test_split_purges_target_endpoint_and_delayed_availability() -> None:
    rows = _training_rows()
    result = _select(tuple(reversed(rows)))
    assert result.disposition is FitDisposition.READY
    assert result.outer_training_target_ids == tuple(row.target_id for row in rows)
    assert result.inner_validation_target_ids == ("t6", "t7")
    assert result.purged_target_ids == ("t5",)
    assert result.inner_fit_target_ids == ("t0", "t1", "t2", "t3", "t4")

    boundary = rows[6].decision_time
    equality = replace(rows[5], target_end_time=boundary, target_available_at=boundary)
    accepted = _select((*rows[:5], equality, *rows[6:]))
    assert "t5" in accepted.inner_fit_target_ids
    assert "t5" not in accepted.purged_target_ids

    delayed = replace(equality, target_available_at=boundary + timedelta(microseconds=1))
    rejected = _select((*rows[:5], delayed, *rows[6:]))
    assert "t5" not in rejected.inner_fit_target_ids
    assert "t5" in rejected.purged_target_ids


def test_preprocessing_drops_all_null_and_zero_variance_but_leaves_binary_unscaled() -> None:
    rows = _training_rows()[:5]
    fit = fit_preprocessing(rows, _SCHEMA)
    assert fit.dropped_all_null_feature_names == ("all_null",)
    assert fit.dropped_zero_variance_feature_names == ("constant",)
    assert fit.unscaled_feature_names == ("signal_available",)
    matrix = transform(rows, fit)
    assert tuple(matrix[:, 1]) == (0.0, 1.0, 0.0, 1.0, 0.0)

    with pytest.raises(ValueError, match="not binary"):
        fit_preprocessing((replace(rows[0], features=(0.0, 2.0, None, 7.0)), *rows[1:]), _SCHEMA)


def test_pooled_weights_give_every_instrument_equal_total_and_mean_one() -> None:
    rows = _training_rows()
    weights = equal_instrument_total_weights(rows)
    totals = {
        instrument: sum(
            weight
            for row, weight in zip(rows, weights, strict=True)
            if row.target_instrument_id == instrument
        )
        for instrument in {row.target_instrument_id for row in rows}
    }
    assert totals["a"] == pytest.approx(totals["b"])
    assert sum(weights) / len(weights) == pytest.approx(1.0)


def test_complete_grid_retains_numerical_candidate_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from qtrad.application import r2_preprocessing

    real: Callable[..., ndarray] = r2_preprocessing._ridge_predictions

    def fail_one(alpha: float, *args: object, **kwargs: object) -> ndarray:
        if alpha == 1.0:
            raise ValueError("synthetic solver failure")
        return real(alpha, *args, **kwargs)

    monkeypatch.setattr(r2_preprocessing, "_ridge_predictions", fail_one)
    result = _select(_training_rows())
    assert tuple(score.alpha for score in result.candidate_scores) == (0.1, 1.0, 10.0)
    failed = result.candidate_scores[1]
    assert failed.disposition is FitDisposition.NUMERICAL_FAILURE
    assert failed.loss is None
    assert failed.failure == "ValueError"
    assert result.disposition is FitDisposition.READY


def test_configured_solver_and_loss_fail_closed() -> None:
    kwargs: _SelectionKwargs = {
        "feature_schema": _SCHEMA,
        "alpha_grid": (1.0,),
        "minimum_training_rows": 3,
        "minimum_inner_validation_rows": 2,
        "ridge_solver": "auto",
        "ridge_tolerance": 1e-8,
        "ridge_max_iterations": 100,
        "loss_policy": "OOF_PRIMARY_MSE_V1",
        "pooled_weighting_policy": "EQUAL_INSTRUMENT_TOTAL_WEIGHT_MEAN_ONE",
    }
    with pytest.raises(ValueError, match="Ridge"):
        select_chronological_alpha(_training_rows(), **kwargs)
    bad_loss_kwargs: _SelectionKwargs = {
        **kwargs,
        "ridge_solver": "lsqr",
        "loss_policy": "MAE",
    }
    with pytest.raises(ValueError, match="loss"):
        select_chronological_alpha(_training_rows(), **bad_loss_kwargs)


def _bound_fixture() -> tuple[R1FoundationBindings, R2FeatureDataset, R2ExperimentConfig, Fold]:
    base = base_experiment()
    instrument = base.confirmatory_target_instruments[0]
    target_rows: list[TargetRow] = []
    for index in range(8):
        decision = START + timedelta(minutes=10 * index)
        endpoint = decision + timedelta(minutes=15)
        target_rows.append(
            TargetRow(
                instrument_id=instrument,
                decision_time=decision,
                horizon=base.primary_horizon,
                target_basis=PriceBasis.MID,
                target_revision_policy="LATEST_AVAILABLE_BEFORE_FREEZE",
                target_start_time=decision,
                target_end_time=endpoint,
                target_freeze_at=endpoint,
                target_available_at=endpoint,
                label_start_close=Decimal("100"),
                label_end_close=Decimal("101"),
                log_return=float(index % 3) / 100,
                return_disposition=ReturnDisposition.VALID,
                start_event_id=UUID(int=2 * index + 1),
                end_event_id=UUID(int=2 * index + 2),
                upper_log_excursion=0.02,
                lower_log_excursion=-0.01,
                excursion_disposition=ExcursionDisposition.VALID,
            )
        )
    targets = TargetDataset.create(
        target_rows,
        observation_dataset_id=base.observation_dataset_id,
        foundation_configuration_id=base.foundation_configuration_id,
    )
    training_ids = tuple(row.target_id for row in target_rows)
    fold = Fold(
        fold_id="outer-1",
        training_start=START,
        training_cutoff=START + timedelta(minutes=90),
        validation_start=START + timedelta(minutes=91),
        validation_end=START + timedelta(minutes=120),
        embargo_end=START + timedelta(minutes=91),
        training_target_ids=training_ids,
        validation_target_ids=(),
        holdout_excluded=True,
        membership_hash=membership_hash(training_ids, ()),
    )
    folds = FoldDataset.create(
        (fold,),
        target_dataset_id=targets.dataset_id,
        foundation_configuration_id=base.foundation_configuration_id,
    )
    config = replace(
        base,
        target_dataset_id=targets.dataset_id,
        fold_dataset_id=folds.dataset_id,
        minimum_training_rows=4,
        minimum_inner_validation_rows=2,
    )
    set_id = feature_set_id(config.configuration_id, "fixture", _SCHEMA)
    feature_rows = tuple(
        RawFeatureRow(
            row.instrument_id,
            row.decision_time,
            row.decision_time,
            row.decision_time,
            set_id,
            (
                RawFeatureValue("signal", float(index)),
                RawFeatureValue("signal_available", float(index % 2)),
                RawFeatureValue("all_null", None),
                RawFeatureValue("constant", 7.0),
            ),
        )
        for index, row in enumerate(target_rows)
    )
    features = R2FeatureDataset.create(
        feature_rows,
        feature_schema=_SCHEMA,
        feature_set_name="fixture",
        observation_dataset_id=config.observation_dataset_id,
        panel_dataset_id=config.panel_dataset_id,
        target_dataset_id=targets.dataset_id,
        fold_dataset_id=folds.dataset_id,
        experiment_configuration_id=config.configuration_id,
        evidence_class=config.evidence_class,
    )
    active: dict[str, JsonValue] = {
        item: [[START.isoformat(), END.isoformat()]] for item in config.ordered_instruments
    }
    evidence: dict[str, JsonValue] = {
        "availability_delay_report": {},
        "revision_delay_report": {},
        "data_gaps": [],
        "source_active_intervals": active,
        "lineage_summary": {},
        "observation_bounds": {
            "interval_start": START.isoformat(),
            "interval_end": END.isoformat(),
        },
    }
    availability_id = _availability_dataset_id(config.observation_dataset_id, evidence)
    foundation_config = SimpleNamespace(
        configuration_id=config.foundation_configuration_id,
        observation_dataset_id=config.observation_dataset_id,
        ordered_instruments=config.ordered_instruments,
        instrument_roles=config.instrument_roles,
        target_horizons=config.horizons,
        holdout_range=config.holdout_range,
        range_start=START,
        range_end=END,
        availability_basis=AvailabilityBasis.PERSISTED_AT,
    )
    verified = cast(
        R1FoundationBindings,
        SimpleNamespace(
            bundle=SimpleNamespace(
                bundle_id=config.r1_bundle_id,
                ordered_instruments=config.ordered_instruments,
                range_start=START,
                range_end=END,
                configuration=SimpleNamespace(dataset_id=config.foundation_configuration_id),
                observations=SimpleNamespace(dataset_id=config.observation_dataset_id),
                availability=SimpleNamespace(dataset_id=availability_id),
                panel=SimpleNamespace(dataset_id=config.panel_dataset_id),
                targets=SimpleNamespace(dataset_id=targets.dataset_id),
                folds=SimpleNamespace(dataset_id=folds.dataset_id),
                build_summary={
                    "application_version": config.r1_application_version,
                    "image_identity": config.r1_image_identity,
                },
            ),
            configuration=foundation_config,
            observations=SimpleNamespace(
                dataset_id=config.observation_dataset_id,
                selection_policies={"availability_basis": AvailabilityBasis.PERSISTED_AT.value},
            ),
            panel=SimpleNamespace(
                dataset_id=config.panel_dataset_id,
                observation_dataset_id=config.observation_dataset_id,
                foundation_configuration_id=config.foundation_configuration_id,
            ),
            targets=targets,
            folds=folds,
            availability_evidence=evidence,
        ),
    )
    return verified, features, config, fold


def test_authenticated_build_binds_every_child_and_excludes_holdout() -> None:
    verified, features, config, fold = _bound_fixture()
    fit = build_r2_preprocessing_selection(
        verified,
        features,
        config,
        outer_fold_id=fold.fold_id,
        target_instruments=(config.confirmatory_target_instruments[0],),
    )
    assert fit.target_dataset_id == verified.targets.dataset_id
    assert fit.fold_dataset_id == verified.folds.dataset_id
    assert fit.r2_feature_dataset_id == features.dataset_id
    assert fit.experiment_configuration_id == config.configuration_id
    assert fit.evidence_class is features.evidence_class
    assert fit.preprocessing_policy == config.preprocessing_policy
    assert fit.inner_validation_policy == config.inner_validation_policy
    assert fit.holdout_excluded is True
    assert fit.selection.purged_target_ids == (verified.targets.rows[5].target_id,)

    mismatched = R2FeatureDataset.create(
        features.rows,
        feature_schema=features.feature_schema,
        feature_set_name=features.feature_set_name,
        observation_dataset_id=features.observation_dataset_id,
        panel_dataset_id="9" * 64,
        target_dataset_id=features.target_dataset_id,
        fold_dataset_id=features.fold_dataset_id,
        experiment_configuration_id=features.experiment_configuration_id,
        evidence_class=features.evidence_class,
    )
    with pytest.raises(ValueError, match="panel_dataset_id"):
        build_r2_preprocessing_selection(verified, mismatched, config, outer_fold_id=fold.fold_id)


def test_build_rejects_unsupported_preprocessing_and_inner_validation_policies() -> None:
    verified, features, config, fold = _bound_fixture()

    with pytest.raises(ValueError, match="preprocessing policy"):
        build_r2_preprocessing_selection(
            verified,
            features,
            replace(config, preprocessing_policy="UNSUPPORTED"),
            outer_fold_id=fold.fold_id,
        )
    with pytest.raises(ValueError, match="inner-validation policy"):
        build_r2_preprocessing_selection(
            verified,
            features,
            replace(config, inner_validation_policy="UNSUPPORTED"),
            outer_fold_id=fold.fold_id,
        )


def test_build_rejects_feature_evidence_class_mismatch() -> None:
    verified, features, config, fold = _bound_fixture()
    mismatched_evidence = (
        EvidenceClass.CONFIRMATORY
        if config.evidence_class is EvidenceClass.IMPLEMENTATION
        else EvidenceClass.IMPLEMENTATION
    )
    mismatched = R2FeatureDataset.create(
        features.rows,
        feature_schema=features.feature_schema,
        feature_set_name=features.feature_set_name,
        observation_dataset_id=features.observation_dataset_id,
        panel_dataset_id=features.panel_dataset_id,
        target_dataset_id=features.target_dataset_id,
        fold_dataset_id=features.fold_dataset_id,
        experiment_configuration_id=features.experiment_configuration_id,
        evidence_class=mismatched_evidence,
    )

    with pytest.raises(ValueError, match="evidence_class"):
        build_r2_preprocessing_selection(verified, mismatched, config, outer_fold_id=fold.fold_id)


def test_strict_json_decode_recomputes_identity_and_rejects_unknown_fields() -> None:
    verified, features, config, fold = _bound_fixture()
    fit = build_r2_preprocessing_selection(verified, features, config, outer_fold_id=fold.fold_id)
    encoded = serialize_r2_preprocessing_selection(fit)
    assert decode_r2_preprocessing_selection(encoded) == fit

    payload = fit.as_json()
    payload["unknown"] = True
    with pytest.raises(ValueError, match="unknown or missing"):
        decode_r2_preprocessing_selection(payload)
    tampered = fit.as_json()
    tampered["ridge_tolerance"] = 0.5
    with pytest.raises(ValueError, match="artifact ID"):
        decode_r2_preprocessing_selection(tampered)
    wrong_type = fit.as_json()
    wrong_type["ridge_max_iterations"] = True
    with pytest.raises(TypeError, match="integer"):
        decode_r2_preprocessing_selection(wrong_type)


def test_independent_verifier_rebuilds_and_rejects_rehashed_semantic_tampering() -> None:
    verified, features, config, fold = _bound_fixture()
    target_instruments = (config.confirmatory_target_instruments[0],)
    selection = build_r2_preprocessing_selection(
        verified,
        features,
        config,
        outer_fold_id=fold.fold_id,
        target_instruments=target_instruments,
    )
    persisted = serialize_r2_preprocessing_selection(selection)
    assert (
        verify_r2_preprocessing_selection(
            verified,
            features,
            config,
            outer_fold_id=fold.fold_id,
            target_instruments=target_instruments,
            persisted_payload=persisted,
        )
        == selection
    )

    alternate_alpha = next(
        score.alpha
        for score in selection.selection.candidate_scores
        if score.disposition is FitDisposition.READY
        and score.alpha != selection.selection.selected_alpha
    )
    tampered_selection = replace(selection.selection, selected_alpha=alternate_alpha)
    tampered = R2PreprocessingSelection.create(
        r2_feature_dataset_id=selection.r2_feature_dataset_id,
        target_dataset_id=selection.target_dataset_id,
        fold_dataset_id=selection.fold_dataset_id,
        experiment_configuration_id=selection.experiment_configuration_id,
        outer_fold_id=selection.outer_fold_id,
        outer_fold_membership_hash=selection.outer_fold_membership_hash,
        target_instruments=selection.target_instruments,
        inner_validation_start=selection.inner_validation_start,
        inner_validation_end=selection.inner_validation_end,
        purge_boundary=selection.purge_boundary,
        feature_schema_id=selection.feature_schema_id,
        feature_set_id=selection.feature_set_id,
        evidence_class=selection.evidence_class,
        preprocessing_policy=selection.preprocessing_policy,
        inner_validation_policy=selection.inner_validation_policy,
        alpha_grid=selection.alpha_grid,
        ridge_solver=selection.ridge_solver,
        ridge_tolerance=selection.ridge_tolerance,
        ridge_max_iterations=selection.ridge_max_iterations,
        loss_policy=selection.loss_policy,
        pooled_weighting_policy=selection.pooled_weighting_policy,
        holdout_excluded=selection.holdout_excluded,
        selection=tampered_selection,
    )
    assert tampered.artifact_id != selection.artifact_id

    with pytest.raises(ValueError, match="authenticated rebuild"):
        verify_r2_preprocessing_selection(
            verified,
            features,
            config,
            outer_fold_id=fold.fold_id,
            target_instruments=target_instruments,
            persisted_payload=serialize_r2_preprocessing_selection(tampered),
        )
