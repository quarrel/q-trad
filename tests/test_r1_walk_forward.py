from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from uuid import uuid4

from qtrad.application.walk_forward import (
    ZERO_RETURN_MODEL_CONTRACT,
    ZERO_RETURN_MODEL_ID,
    build_expanding_folds,
    build_zero_return_forecasts,
)
from qtrad.domain.foundation import (
    AvailabilityBasis,
    ExcursionDisposition,
    FoundationConfig,
    InstrumentRole,
    PanelAuditDisposition,
    PanelDataset,
    PanelRow,
    PanelStatus,
    ReturnDisposition,
    TargetDataset,
    TargetRow,
)
from qtrad.domain.market_data import DataQuality, PriceBasis

OBSERVATION_DATASET_ID = sha256(b"observation-fixture").hexdigest()


def _config() -> FoundationConfig:
    start = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    end = datetime(2026, 7, 1, 13, 10, tzinfo=UTC)
    return FoundationConfig(
        name="r1-c-fixture",
        schema_version=1,
        observation_dataset_id=OBSERVATION_DATASET_ID,
        ordered_instruments=("fx:aud-usd",),
        instrument_roles={"fx:aud-usd": InstrumentRole.TARGET},
        range_start=start,
        range_end=end,
        grid_resolution=timedelta(minutes=1),
        availability_basis=AvailabilityBasis.RECEIVED_AT,
        feature_lag_policy="PROVISIONAL_CONSERVATIVE",
        feature_lag_calibration_range=(start, end),
        feature_lag_percentile=0.95,
        feature_lag_safety_margin=timedelta(minutes=1),
        selected_feature_lag=timedelta(minutes=1),
        target_horizons=(timedelta(minutes=15),),
        primary_vertical_horizon=timedelta(minutes=15),
        target_revision_delay=timedelta(minutes=1),
        target_revision_policy="PROVISIONAL_CONSERVATIVE",
        required_feature_bases=(PriceBasis.MID,),
        target_basis=PriceBasis.MID,
        fold_policy="EXPANDING_WALK_FORWARD",
        holdout_range=(end - timedelta(minutes=10), end),
        embargo=timedelta(minutes=5),
        minimum_training_duration=timedelta(minutes=15),
        minimum_validation_duration=timedelta(minutes=10),
    )


def _targets(config: FoundationConfig) -> TargetDataset:
    rows: list[TargetRow] = []
    for minute in range(0, 60, 5):
        decision = config.range_start + timedelta(minutes=minute)
        end = decision + config.primary_vertical_horizon
        rows.append(
            TargetRow(
                instrument_id="fx:aud-usd",
                decision_time=decision,
                horizon=config.primary_vertical_horizon,
                target_basis=PriceBasis.MID,
                target_revision_policy=config.target_revision_policy,
                target_start_time=decision,
                target_end_time=end,
                target_freeze_at=end,
                target_available_at=end,
                label_start_close=Decimal("100"),
                label_end_close=Decimal("101"),
                log_return=0.01,
                return_disposition=ReturnDisposition.VALID,
                start_event_id=uuid4(),
                end_event_id=uuid4(),
                upper_log_excursion=0.02,
                lower_log_excursion=-0.01,
                excursion_disposition=ExcursionDisposition.VALID,
            )
        )
    return TargetDataset.create(
        rows,
        observation_dataset_id=config.observation_dataset_id,
        foundation_configuration_id=config.configuration_id,
    )


def _panel(config: FoundationConfig, targets: TargetDataset) -> PanelDataset:
    rows = [
        PanelRow(
            decision_time=target.decision_time,
            instrument_id=target.instrument_id,
            basis=PriceBasis.MID,
            feature_data_asof=target.decision_time - timedelta(minutes=1),
            latest_feature_bar_end=target.decision_time - timedelta(minutes=1),
            status=PanelStatus.OBSERVED,
            audit_disposition=None,
            selected_event_id=uuid4(),
            selected_stream_version=1,
            selected_global_position=1,
            selected_availability_time=target.decision_time - timedelta(minutes=1),
            selected_revision=1,
            interval_start=target.decision_time - timedelta(minutes=2),
            interval_end=target.decision_time - timedelta(minutes=1),
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100"),
            sample_count=1,
            quality=DataQuality.HEALTHY,
        )
        for target in targets.rows
    ]
    return PanelDataset.create(
        rows,
        observation_dataset_id=config.observation_dataset_id,
        foundation_configuration_id=config.configuration_id,
    )


def test_expanding_folds_require_maturity_and_exclude_holdout() -> None:
    config = _config()
    targets = _targets(config)

    folds = build_expanding_folds(targets, config)

    assert len(folds.folds) >= 2
    for fold in folds.folds:
        assert fold.holdout_excluded
        assert fold.training_cutoff == fold.validation_start - config.embargo
        assert fold.embargo_end == fold.validation_start
        training = {row.target_id: row for row in targets.rows}
        for target_id in fold.training_target_ids:
            target = training[target_id]
            assert target.target_end_time <= fold.training_cutoff
            assert target.target_available_at <= fold.training_cutoff
        for target_id in fold.validation_target_ids:
            assert not (
                config.holdout_range[0]
                <= training[target_id].decision_time
                < config.holdout_range[1]
            )

    rebuilt = build_expanding_folds(
        TargetDataset.create(
            tuple(reversed(targets.rows)),
            observation_dataset_id=config.observation_dataset_id,
            foundation_configuration_id=config.configuration_id,
        ),
        config,
    )
    assert rebuilt.dataset_id == folds.dataset_id
    assert rebuilt.folds == folds.folds


def test_zero_return_forecasts_are_validation_only_and_model_independent() -> None:
    config = _config()
    targets = _targets(config)
    panel = _panel(config, targets)
    folds = build_expanding_folds(targets, config)

    forecasts = build_zero_return_forecasts(panel, targets, folds, config)
    rebuilt = build_zero_return_forecasts(panel, targets, folds, config)

    assert forecasts.dataset_id == rebuilt.dataset_id
    assert forecasts.rows
    assert all(row.expected_return == 0.0 for row in forecasts.rows)
    assert all(row.return_unit == "LOG_RETURN" for row in forecasts.rows)
    assert all(row.model_contract == ZERO_RETURN_MODEL_CONTRACT for row in forecasts.rows)
    assert all(row.model_id == ZERO_RETURN_MODEL_ID for row in forecasts.rows)
    assert all(
        not (config.holdout_range[0] <= row.decision_time < config.holdout_range[1])
        for row in forecasts.rows
    )
    assert {row.fold_id for row in forecasts.rows} == {fold.fold_id for fold in folds.folds}
    assert all(row.training_cutoff < row.decision_time for row in forecasts.rows)


def test_forecast_generation_skips_unobserved_features_without_fabricating_values() -> None:
    config = _config()
    targets = _targets(config)
    panel = _panel(config, targets)
    folds = build_expanding_folds(targets, config)
    missing_target = next(
        target for target in targets.rows if target.decision_time == folds.folds[0].validation_start
    )
    missing_panel = next(
        row for row in panel.rows if row.decision_time == missing_target.decision_time
    )
    missing_panel = replace(
        missing_panel,
        status=PanelStatus.MISSING_AS_OF_CUTOFF,
        audit_disposition=PanelAuditDisposition.NO_NATIVE_EVIDENCE,
        selected_event_id=None,
        selected_stream_version=None,
        selected_global_position=None,
        selected_availability_time=None,
        selected_revision=None,
        interval_start=None,
        interval_end=None,
        open=None,
        high=None,
        low=None,
        close=None,
        sample_count=None,
        quality=None,
    )
    panel = PanelDataset.create(
        tuple(
            missing_panel if row.decision_time == missing_target.decision_time else row
            for row in panel.rows
        ),
        observation_dataset_id=config.observation_dataset_id,
        foundation_configuration_id=config.configuration_id,
    )

    forecasts = build_zero_return_forecasts(panel, targets, folds, config)

    assert all(row.decision_time != missing_target.decision_time for row in forecasts.rows)
