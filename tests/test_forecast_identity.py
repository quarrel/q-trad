"""Focused regressions for retained-scale forecast row identity construction."""

from datetime import UTC, datetime, timedelta
from typing import TypedDict

import pytest

import qtrad.domain.forecasts as forecasts_domain
import qtrad.domain.r2_baselines as baselines_domain
from qtrad.domain.forecasts import ForecastRow, ReturnUnit
from qtrad.domain.market_data import MarketDataSourceClass
from qtrad.domain.r2_baselines import (
    ForecastCoverageDisposition,
    ForecastCoverageRow,
)


class _ForecastArguments(TypedDict):
    instrument_id: str
    decision_time: datetime
    horizon: timedelta
    expected_return: float
    return_unit: ReturnUnit
    feature_data_asof: datetime
    training_cutoff: datetime
    observation_dataset_id: str
    panel_dataset_id: str
    target_dataset_id: str
    target_id: str
    fold_dataset_id: str
    experiment_id: str
    fold_id: str
    model_id: str
    model_contract: str


class _CoverageArguments(TypedDict):
    target_id: str
    target_instrument_id: str
    decision_time: datetime
    horizon: timedelta
    outer_fold_id: str
    fold_fit_id: str
    feature_data_asof: datetime | None
    disposition: ForecastCoverageDisposition
    forecast_id: str | None
    reason: str | None
    market_data_source_class: MarketDataSourceClass


def _forecast_arguments() -> _ForecastArguments:
    return {
        "instrument_id": 'EUR/USD-quoted-"é',
        "decision_time": datetime(2024, 1, 1, tzinfo=UTC),
        "horizon": timedelta(minutes=5),
        "expected_return": 1.25e-6,
        "return_unit": ReturnUnit.LOG_RETURN,
        "feature_data_asof": datetime(2023, 12, 31, 23, 59, tzinfo=UTC),
        "training_cutoff": datetime(2023, 12, 31, tzinfo=UTC),
        "observation_dataset_id": 'observation-"é',
        "panel_dataset_id": "panel",
        "target_dataset_id": "target",
        "target_id": "target",
        "fold_dataset_id": "fold",
        "experiment_id": "experiment",
        "fold_id": "outer-fold",
        "model_id": "model",
        "model_contract": "model-contract",
    }


def _coverage_arguments(
    forecast_id: str | None,
    *,
    disposition: ForecastCoverageDisposition = ForecastCoverageDisposition.FORECASTED,
    feature_data_asof: datetime | None = datetime(2023, 12, 31, 23, 59, tzinfo=UTC),
    reason: str | None = None,
) -> _CoverageArguments:
    return {
        "target_id": "target",
        "target_instrument_id": 'EUR/USD-quoted-"é',
        "decision_time": datetime(2024, 1, 1, tzinfo=UTC),
        "horizon": timedelta(minutes=5),
        "outer_fold_id": "outer-fold",
        "fold_fit_id": "a" * 64,
        "feature_data_asof": feature_data_asof,
        "disposition": disposition,
        "forecast_id": forecast_id,
        "reason": reason,
        "market_data_source_class": MarketDataSourceClass.IBKR_HISTORICAL_RESEARCH,
    }


def _fail_identity_work(*_args: object, **_kwargs: object) -> str:
    raise AssertionError("retained-scale row creation must not use legacy JSON identity work")


def test_forecast_row_create_matches_legacy_digest_without_per_row_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arguments = _forecast_arguments()
    baseline = ForecastRow.create(**arguments)
    expected_id = forecasts_domain._hash_json(forecasts_domain._forecast_semantic(baseline))

    monkeypatch.setattr(forecasts_domain, "_hash_json", _fail_identity_work)
    monkeypatch.setattr(forecasts_domain.json, "dumps", _fail_identity_work)

    row = ForecastRow.create(**arguments)

    assert row.forecast_id == expected_id


def test_direct_row_constructors_reject_forged_identity() -> None:
    with pytest.raises(ValueError, match="forecast ID"):
        ForecastRow(forecast_id="0" * 64, **_forecast_arguments())

    with pytest.raises(ValueError, match="forecast-coverage row ID"):
        ForecastCoverageRow(
            coverage_id="0" * 64,
            **_coverage_arguments("b" * 64),
        )


@pytest.mark.parametrize(
    ("disposition", "feature_data_asof", "forecast_id", "reason"),
    (
        (
            ForecastCoverageDisposition.FORECASTED,
            datetime(2023, 12, 31, 23, 59, tzinfo=UTC),
            "b" * 64,
            None,
        ),
        (
            ForecastCoverageDisposition.FEATURES_UNAVAILABLE,
            None,
            None,
            "no exact raw-feature row",
        ),
    ),
)
def test_coverage_row_create_matches_legacy_digest_without_per_row_json(
    monkeypatch: pytest.MonkeyPatch,
    disposition: ForecastCoverageDisposition,
    feature_data_asof: datetime | None,
    forecast_id: str | None,
    reason: str | None,
) -> None:
    arguments = _coverage_arguments(
        forecast_id,
        disposition=disposition,
        feature_data_asof=feature_data_asof,
        reason=reason,
    )
    expected_id = baselines_domain._semantic_id(baselines_domain._coverage_row_json(arguments))

    monkeypatch.setattr(baselines_domain, "_semantic_id", _fail_identity_work)
    monkeypatch.setattr(baselines_domain.json, "dumps", _fail_identity_work)

    row = ForecastCoverageRow.create(**arguments)

    assert row.coverage_id == expected_id
