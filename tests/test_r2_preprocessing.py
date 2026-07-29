from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import TypedDict, cast
from uuid import UUID

import pytest
from numpy import ndarray, zeros

from qtrad.application.r2_features import feature_schema_for_set
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
    feature_registry,
    feature_set_id,
)
from qtrad.domain.r2_models import (
    AlphaSelection,
    FitDisposition,
    PreprocessingFeatureKind,
    R2PreprocessingSchema,
    R2PreprocessingSelection,
    derive_r2_preprocessing_schema,
    validate_preprocessing_schema_correspondence,
)
from qtrad.domain.r2_readiness import (
    EvidenceClass,
    FeatureFamily,
    ModelFamily,
    R2ExperimentConfig,
)
from qtrad.runtime.r2_preprocessing_selection import (
    decode_r2_preprocessing_selection,
    serialize_r2_preprocessing_selection,
    verify_r2_preprocessing_selection,
)
from tests.test_r2_readiness import END, START
from tests.test_r2_readiness import experiment as base_experiment


class _SelectionKwargs(TypedDict):
    preprocessing_schema: R2PreprocessingSchema
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
    FeatureDefinition(
        "signal_available",
        FeatureFamily.LOCAL_RETURNS,
        availability_indicator=True,
    ),
    FeatureDefinition("all_null", FeatureFamily.LOCAL_RETURNS),
    FeatureDefinition("constant", FeatureFamily.LOCAL_RETURNS),
)
_PREPROCESSING_SCHEMA = derive_r2_preprocessing_schema(_SCHEMA)

_SELECTION_APPLICATION_IMAGE_IDENTITY = "qtrad@sha256:" + "7" * 64
_SELECTION_SKLEARN_LIBRARY_IDENTITY = "scikit-learn==locked-test"


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
        preprocessing_schema=_PREPROCESSING_SCHEMA,
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
    fit = fit_preprocessing(rows, _PREPROCESSING_SCHEMA)
    assert fit.dropped_all_null_feature_names == ("all_null",)
    assert fit.dropped_zero_variance_feature_names == ("constant",)
    assert fit.unscaled_feature_names == ("signal_available",)
    matrix = transform(rows, fit)
    assert tuple(matrix[:, 1]) == (0.0, 1.0, 0.0, 1.0, 0.0)

    with pytest.raises(ValueError, match="not binary"):
        fit_preprocessing(
            (replace(rows[0], features=(0.0, 2.0, None, 7.0)), *rows[1:]),
            _PREPROCESSING_SCHEMA,
        )


def test_preprocessing_schema_requires_exact_raw_feature_order() -> None:
    reordered = R2PreprocessingSchema.create(tuple(reversed(_PREPROCESSING_SCHEMA.features)))

    with pytest.raises(ValueError, match="authenticated raw-feature schema"):
        validate_preprocessing_schema_correspondence(_SCHEMA, reordered)


def test_real_feature_registry_kinds_drive_binary_preprocessing() -> None:
    raw_schema = feature_registry(base_experiment())
    schema = derive_r2_preprocessing_schema(raw_schema)
    binary_names = tuple(
        item.name
        for item in schema.features
        if item.kind is PreprocessingFeatureKind.BINARY_INDICATOR
    )
    assert {"source_active", "quality_healthy", "gap_known_by_cutoff"} <= set(binary_names)
    assert all(
        not item.availability_indicator
        for item in raw_schema
        if item.name in {"source_active", "quality_healthy", "gap_known_by_cutoff"}
    )
    rows = tuple(
        replace(
            row,
            target_id=f"registry-{index}",
            features=tuple(
                float((position + index) % 2)
                if definition.kind is PreprocessingFeatureKind.BINARY_INDICATOR
                else float(position + index)
                for position, definition in enumerate(schema.features)
            ),
        )
        for index, row in enumerate(_training_rows()[:2])
    )

    fit = fit_preprocessing(rows, schema)

    assert fit.indicator_feature_names == binary_names
    assert set(fit.unscaled_feature_names) == set(binary_names)


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


def test_exact_candidate_loss_tie_selects_larger_alpha(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from qtrad.application import r2_preprocessing

    def equal_predictions(
        _alpha: float,
        _x_fit: ndarray,
        _y_fit: ndarray,
        x_validation: ndarray,
        _sample_weights: object,
        **_kwargs: object,
    ) -> ndarray:
        return zeros(len(x_validation))

    monkeypatch.setattr(r2_preprocessing, "_ridge_predictions", equal_predictions)
    result = _select(_training_rows())

    assert len({score.loss for score in result.candidate_scores}) == 1
    assert result.selected_alpha == 10.0


def test_configured_solver_and_loss_fail_closed() -> None:
    kwargs: _SelectionKwargs = {
        "preprocessing_schema": _PREPROCESSING_SCHEMA,
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
    raw_schema = feature_schema_for_set(config, "L0")
    preprocessing_schema = derive_r2_preprocessing_schema(raw_schema)
    set_id = feature_set_id(config.configuration_id, "L0", raw_schema)
    feature_rows = tuple(
        RawFeatureRow(
            row.instrument_id,
            row.decision_time,
            row.decision_time,
            row.decision_time,
            set_id,
            tuple(
                RawFeatureValue(
                    definition.name,
                    float((position + index) % 2)
                    if preprocessing.kind is PreprocessingFeatureKind.BINARY_INDICATOR
                    else float(position + index),
                )
                for position, (definition, preprocessing) in enumerate(
                    zip(raw_schema, preprocessing_schema.features, strict=True)
                )
            ),
        )
        for index, row in enumerate(target_rows)
    )
    features = R2FeatureDataset.create(
        feature_rows,
        feature_schema=raw_schema,
        feature_set_name="L0",
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


def _build_bound_selection(
    verified: R1FoundationBindings,
    features: R2FeatureDataset,
    config: R2ExperimentConfig,
    fold: Fold,
    *,
    model_family: ModelFamily = ModelFamily.LOCAL_RIDGE,
    horizon: timedelta | None = None,
    target_instruments: tuple[str, ...] | None = None,
) -> R2PreprocessingSelection:
    return build_r2_preprocessing_selection(
        verified,
        features,
        config,
        model_family=model_family,
        horizon=config.primary_horizon if horizon is None else horizon,
        outer_fold_id=fold.fold_id,
        target_instruments=(config.confirmatory_target_instruments[0],)
        if target_instruments is None
        else target_instruments,
        application_image_identity=_SELECTION_APPLICATION_IMAGE_IDENTITY,
        sklearn_library_identity=_SELECTION_SKLEARN_LIBRARY_IDENTITY,
    )


def _verify_bound_selection(
    verified: R1FoundationBindings,
    features: R2FeatureDataset,
    config: R2ExperimentConfig,
    fold: Fold,
    *,
    persisted_payload: bytes | str | dict[str, JsonValue],
    target_instruments: tuple[str, ...] | None = None,
) -> R2PreprocessingSelection:
    return verify_r2_preprocessing_selection(
        verified,
        features,
        config,
        model_family=ModelFamily.LOCAL_RIDGE,
        horizon=config.primary_horizon,
        outer_fold_id=fold.fold_id,
        target_instruments=(config.confirmatory_target_instruments[0],)
        if target_instruments is None
        else target_instruments,
        application_image_identity=_SELECTION_APPLICATION_IMAGE_IDENTITY,
        sklearn_library_identity=_SELECTION_SKLEARN_LIBRARY_IDENTITY,
        persisted_payload=persisted_payload,
    )


def _rehashed_selection(
    artifact: R2PreprocessingSelection, selection: AlphaSelection
) -> R2PreprocessingSelection:
    return R2PreprocessingSelection.create(
        r2_feature_dataset_id=artifact.r2_feature_dataset_id,
        target_dataset_id=artifact.target_dataset_id,
        fold_dataset_id=artifact.fold_dataset_id,
        experiment_configuration_id=artifact.experiment_configuration_id,
        model_family=artifact.model_family,
        horizon=artifact.horizon,
        outer_fold_id=artifact.outer_fold_id,
        outer_fold_membership_hash=artifact.outer_fold_membership_hash,
        target_instruments=artifact.target_instruments,
        inner_validation_start=artifact.inner_validation_start,
        inner_validation_end=artifact.inner_validation_end,
        purge_boundary=artifact.purge_boundary,
        feature_schema_id=artifact.feature_schema_id,
        feature_set_id=artifact.feature_set_id,
        preprocessing_schema_id=artifact.preprocessing_schema_id,
        preprocessing_schema=artifact.preprocessing_schema,
        evidence_class=artifact.evidence_class,
        application_image_identity=artifact.application_image_identity,
        sklearn_library_identity=artifact.sklearn_library_identity,
        preprocessing_policy=artifact.preprocessing_policy,
        inner_validation_policy=artifact.inner_validation_policy,
        alpha_grid=artifact.alpha_grid,
        ridge_solver=artifact.ridge_solver,
        ridge_tolerance=artifact.ridge_tolerance,
        ridge_max_iterations=artifact.ridge_max_iterations,
        loss_policy=artifact.loss_policy,
        pooled_weighting_policy=artifact.pooled_weighting_policy,
        holdout_excluded=artifact.holdout_excluded,
        selection=selection,
    )


def _feature_dataset_for_set(
    source: R2FeatureDataset,
    config: R2ExperimentConfig,
    name: str,
    schema: tuple[FeatureDefinition, ...],
) -> R2FeatureDataset:
    preprocessing_schema = derive_r2_preprocessing_schema(schema)
    set_identity = feature_set_id(config.configuration_id, name, schema)
    rows = tuple(
        replace(
            row,
            feature_set_id=set_identity,
            values=tuple(
                RawFeatureValue(
                    definition.name,
                    float((position + row_index) % 2)
                    if preprocessing.kind is PreprocessingFeatureKind.BINARY_INDICATOR
                    else float(position + row_index),
                )
                for position, (definition, preprocessing) in enumerate(
                    zip(schema, preprocessing_schema.features, strict=True)
                )
            ),
        )
        for row_index, row in enumerate(source.rows)
    )
    return R2FeatureDataset.create(
        rows,
        feature_schema=schema,
        feature_set_name=name,
        observation_dataset_id=source.observation_dataset_id,
        panel_dataset_id=source.panel_dataset_id,
        target_dataset_id=source.target_dataset_id,
        fold_dataset_id=source.fold_dataset_id,
        experiment_configuration_id=source.experiment_configuration_id,
        evidence_class=source.evidence_class,
    )


def test_authenticated_build_binds_every_child_and_excludes_holdout() -> None:
    verified, features, config, fold = _bound_fixture()
    fit = _build_bound_selection(
        verified,
        features,
        config,
        fold,
        target_instruments=(config.confirmatory_target_instruments[0],),
    )
    assert fit.target_dataset_id == verified.targets.dataset_id
    assert fit.fold_dataset_id == verified.folds.dataset_id
    assert fit.r2_feature_dataset_id == features.dataset_id
    assert fit.experiment_configuration_id == config.configuration_id
    assert fit.model_family is ModelFamily.LOCAL_RIDGE
    assert fit.horizon == config.primary_horizon
    assert fit.application_image_identity == _SELECTION_APPLICATION_IMAGE_IDENTITY
    assert fit.sklearn_library_identity == _SELECTION_SKLEARN_LIBRARY_IDENTITY
    assert fit.evidence_class is features.evidence_class
    assert fit.preprocessing_schema == derive_r2_preprocessing_schema(features.feature_schema)
    assert fit.preprocessing_schema_id == fit.preprocessing_schema.preprocessing_schema_id
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
        _build_bound_selection(verified, mismatched, config, fold)


def test_build_rejects_undeclared_fabricated_feature_dataset() -> None:
    verified, features, config, fold = _bound_fixture()
    fabricated = _feature_dataset_for_set(features, config, "fabricated", features.feature_schema)

    with pytest.raises(ValueError, match="unknown R2 feature set"):
        _build_bound_selection(verified, fabricated, config, fold)


def test_build_rejects_declared_pooled_context_feature_dataset() -> None:
    verified, features, config, fold = _bound_fixture()
    pooled_schema = feature_schema_for_set(config, "P1")
    pooled = _feature_dataset_for_set(features, config, "P1", pooled_schema)

    with pytest.raises(ValueError, match="pooled-context feature set"):
        _build_bound_selection(verified, pooled, config, fold)


@pytest.mark.parametrize(
    "model_family",
    (
        ModelFamily.ZERO_RETURN,
        ModelFamily.POOLED_LOCAL_RIDGE,
        ModelFamily.POOLED_CROSS_ASSET_RIDGE,
    ),
)
def test_build_rejects_every_nonlocal_model_family(model_family: ModelFamily) -> None:
    verified, features, config, fold = _bound_fixture()

    with pytest.raises(ValueError, match="only LOCAL_RIDGE"):
        _build_bound_selection(
            verified,
            features,
            config,
            fold,
            model_family=model_family,
        )


def test_build_rejects_nonprimary_horizon_and_non_singleton_or_ineligible_scope() -> None:
    verified, features, config, fold = _bound_fixture()
    target = (config.confirmatory_target_instruments[0],)

    with pytest.raises(ValueError, match="primary horizon"):
        _build_bound_selection(
            verified,
            features,
            config,
            fold,
            horizon=config.horizons[0],
            target_instruments=target,
        )
    for scope in ((), config.target_instruments[:2], (config.ordered_instruments[-1],)):
        with pytest.raises(ValueError, match="exactly one eligible target"):
            _build_bound_selection(
                verified,
                features,
                config,
                fold,
                target_instruments=scope,
            )
    with pytest.raises(ValueError, match="exactly one eligible target"):
        build_r2_preprocessing_selection(
            verified,
            features,
            config,
            model_family=ModelFamily.LOCAL_RIDGE,
            horizon=config.primary_horizon,
            outer_fold_id=fold.fold_id,
            application_image_identity=_SELECTION_APPLICATION_IMAGE_IDENTITY,
            sklearn_library_identity=_SELECTION_SKLEARN_LIBRARY_IDENTITY,
        )


def test_build_rejects_unsupported_preprocessing_and_inner_validation_policies() -> None:
    verified, features, config, fold = _bound_fixture()

    with pytest.raises(ValueError, match="preprocessing policy"):
        _build_bound_selection(
            verified,
            features,
            replace(config, preprocessing_policy="UNSUPPORTED"),
            fold,
        )
    with pytest.raises(ValueError, match="inner-validation policy"):
        _build_bound_selection(
            verified,
            features,
            replace(config, inner_validation_policy="UNSUPPORTED"),
            fold,
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
        _build_bound_selection(verified, mismatched, config, fold)


def test_strict_json_decode_recomputes_identity_and_rejects_unknown_fields() -> None:
    verified, features, config, fold = _bound_fixture()
    fit = _build_bound_selection(verified, features, config, fold)
    encoded = serialize_r2_preprocessing_selection(fit)
    assert decode_r2_preprocessing_selection(encoded) == fit

    payload = fit.as_json()
    payload["unknown"] = True
    with pytest.raises(ValueError, match="unknown or missing"):
        decode_r2_preprocessing_selection(payload)
    nested_unknown = fit.as_json()
    nested_schema = cast(dict[str, JsonValue], nested_unknown["preprocessing_schema"])
    nested_schema["unknown"] = True
    with pytest.raises(ValueError, match="unknown or missing"):
        decode_r2_preprocessing_selection(nested_unknown)
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
    selection = _build_bound_selection(
        verified,
        features,
        config,
        fold,
        target_instruments=target_instruments,
    )
    persisted = serialize_r2_preprocessing_selection(selection)
    assert (
        _verify_bound_selection(
            verified,
            features,
            config,
            fold,
            target_instruments=target_instruments,
            persisted_payload=persisted,
        )
        == selection
    )

    inner = selection.selection.inner_preprocessing
    assert inner is not None
    position = next(index for index, value in enumerate(inner.medians) if value is not None)
    baseline_median = inner.medians[position]
    baseline_mean = inner.means[position]
    baseline_scale = inner.scales[position]
    assert baseline_median is not None and baseline_mean is not None and baseline_scale is not None
    within_delta = config.numeric_replay_absolute_tolerance / 2
    within_medians = list(inner.medians)
    within_means = list(inner.means)
    within_scales = list(inner.scales)
    within_weights = list(inner.sample_weights)
    within_medians[position] = baseline_median + within_delta
    within_means[position] = baseline_mean + within_delta
    within_scales[position] = baseline_scale + within_delta
    within_weights[0] += within_delta
    within_weights[1] -= within_delta
    within_inner = replace(
        inner,
        medians=tuple(within_medians),
        means=tuple(within_means),
        scales=tuple(within_scales),
        sample_weights=tuple(within_weights),
    )
    score_position = next(
        index
        for index, score in enumerate(selection.selection.candidate_scores)
        if score.loss is not None
    )
    within_scores = list(selection.selection.candidate_scores)
    score = within_scores[score_position]
    assert score.loss is not None
    within_scores[score_position] = replace(score, loss=score.loss + within_delta)
    within_selection = replace(
        selection.selection,
        inner_preprocessing=within_inner,
        candidate_scores=tuple(within_scores),
    )
    within = _rehashed_selection(selection, within_selection)
    assert within.artifact_id != selection.artifact_id
    assert (
        _verify_bound_selection(
            verified,
            features,
            config,
            fold,
            target_instruments=target_instruments,
            persisted_payload=serialize_r2_preprocessing_selection(within),
        )
        == within
    )

    outside_delta = 10 * (
        config.numeric_replay_absolute_tolerance
        + config.numeric_replay_relative_tolerance * max(1.0, abs(baseline_median))
    )
    outside_medians = list(inner.medians)
    outside_medians[position] = baseline_median + outside_delta
    outside_inner = replace(inner, medians=tuple(outside_medians))
    outside = _rehashed_selection(
        selection, replace(selection.selection, inner_preprocessing=outside_inner)
    )
    assert outside.artifact_id != selection.artifact_id
    with pytest.raises(ValueError, match="authenticated rebuild"):
        _verify_bound_selection(
            verified,
            features,
            config,
            fold,
            target_instruments=target_instruments,
            persisted_payload=serialize_r2_preprocessing_selection(outside),
        )

    alternate_alpha = next(
        score.alpha
        for score in selection.selection.candidate_scores
        if score.disposition is FitDisposition.READY
        and score.alpha != selection.selection.selected_alpha
    )
    tampered_selection = replace(selection.selection, selected_alpha=alternate_alpha)
    tampered = _rehashed_selection(selection, tampered_selection)
    assert tampered.artifact_id != selection.artifact_id

    with pytest.raises(ValueError, match="authenticated rebuild"):
        _verify_bound_selection(
            verified,
            features,
            config,
            fold,
            target_instruments=target_instruments,
            persisted_payload=serialize_r2_preprocessing_selection(tampered),
        )
