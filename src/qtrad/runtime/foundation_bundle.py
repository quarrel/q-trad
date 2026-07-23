"""JSON persistence and independent loading for R1 foundation bundles."""

import json
from collections.abc import Mapping
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast
from uuid import UUID

from qtrad.domain.events import JsonValue
from qtrad.domain.folds import Fold, FoldDataset
from qtrad.domain.forecasts import ForecastDataset, ForecastRow, ReturnUnit
from qtrad.domain.foundation import (
    AvailabilityBasis,
    ExcursionDisposition,
    FoundationConfig,
    HorizonCoverageSummary,
    InstrumentRole,
    PanelAuditDisposition,
    PanelDataset,
    PanelRow,
    PanelStatus,
    ReturnDisposition,
    TargetDataset,
    TargetRow,
)
from qtrad.domain.foundation_bundle import FoundationBundle
from qtrad.domain.market_data import BarProvenance, DataQuality, PriceBasis
from qtrad.domain.research import ObservationDataset, ObservationRow
from qtrad.domain.time import require_utc


def write_foundation_bundle(path: Path, bundle: FoundationBundle) -> None:
    """Write one immutable bundle and refuse to overwrite existing evidence."""

    if path.is_symlink() or path.exists():
        raise ValueError("foundation bundle output must be a new regular file")
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(bundle.as_json(), sort_keys=True, indent=2) + "\n"
    with path.open("x", encoding="utf-8") as output:
        output.write(encoded)


def load_foundation_bundle(path: Path) -> FoundationBundle:
    """Load a JSON bundle and verify every child and cross-reference."""

    if path.is_symlink() or not path.is_file():
        raise ValueError("foundation bundle must be a regular non-symlink file")
    payload = _mapping(json.loads(path.read_text(encoding="utf-8")))
    expected_keys = {
        "contract",
        "schema_version",
        "configuration",
        "observations",
        "panel",
        "targets",
        "folds",
        "forecasts",
        "coverage",
        "bundle_id",
    }
    if set(payload) != expected_keys:
        raise ValueError("foundation bundle has an unexpected schema")
    if (
        payload["contract"] != "qtrad-research-foundation-bundle-v1"
        or payload["schema_version"] != 1
    ):
        raise ValueError("foundation bundle contract is unsupported")
    configuration = _configuration(_mapping(payload["configuration"]))
    observations = _observations(_mapping(payload["observations"]))
    panel = _panel(_mapping(payload["panel"]))
    targets = _targets(_mapping(payload["targets"]))
    folds = _folds(_mapping(payload["folds"]))
    forecasts = _forecasts(_mapping(payload["forecasts"]))
    coverage = tuple(_coverage(_mapping(item)) for item in _sequence(payload["coverage"]))
    return FoundationBundle(
        configuration=configuration,
        observations=observations,
        panel=panel,
        targets=targets,
        folds=folds,
        forecasts=forecasts,
        coverage=coverage,
        bundle_id=_text(payload["bundle_id"]),
    )


def load_foundation_config(path: Path) -> FoundationConfig:
    """Load one standalone foundation configuration JSON document."""

    if path.is_symlink() or not path.is_file():
        raise ValueError("foundation configuration must be a regular non-symlink file")
    payload = _mapping(json.loads(path.read_text(encoding="utf-8")))
    if payload["contract"] != FoundationConfig.CONTRACT:
        raise ValueError("foundation configuration contract is unsupported")
    return _configuration(payload)


def _configuration(payload: Mapping[str, object]) -> FoundationConfig:
    calibration = _sequence(payload["feature_lag_calibration_range"])
    holdout = _sequence(payload["holdout_range"])
    return FoundationConfig(
        name=_text(payload["name"]),
        schema_version=_int(payload["schema_version"]),
        observation_dataset_id=_text(payload["observation_dataset_id"]),
        ordered_instruments=tuple(
            _text(item) for item in _sequence(payload["ordered_instruments"])
        ),
        instrument_roles={
            _text(key): InstrumentRole(_text(value))
            for key, value in _mapping(payload["instrument_roles"]).items()
        },
        range_start=_datetime(payload["range_start"]),
        range_end=_datetime(payload["range_end"]),
        grid_resolution=_duration(payload["grid_resolution_seconds"]),
        availability_basis=AvailabilityBasis(_text(payload["availability_basis"])),
        feature_lag_policy=_text(payload["feature_lag_policy"]),
        feature_lag_calibration_range=(_datetime(calibration[0]), _datetime(calibration[1])),
        feature_lag_percentile=_float(payload["feature_lag_percentile"]),
        feature_lag_safety_margin=_duration(payload["feature_lag_safety_margin_seconds"]),
        selected_feature_lag=_duration(payload["selected_feature_lag_seconds"]),
        target_horizons=tuple(
            _duration(item) for item in _sequence(payload["target_horizons_seconds"])
        ),
        primary_vertical_horizon=_duration(payload["primary_vertical_horizon_seconds"]),
        target_revision_delay=_duration(payload["target_revision_delay_seconds"]),
        target_revision_policy=_text(payload["target_revision_policy"]),
        required_feature_bases=tuple(
            PriceBasis(_text(item)) for item in _sequence(payload["required_feature_bases"])
        ),
        target_basis=PriceBasis(_text(payload["target_basis"])),
        fold_policy=_text(payload["fold_policy"]),
        holdout_range=(_datetime(holdout[0]), _datetime(holdout[1])),
        embargo=_duration(payload["embargo_seconds"]),
        minimum_training_duration=_duration(payload["minimum_training_duration_seconds"]),
        minimum_validation_duration=_duration(payload["minimum_validation_duration_seconds"]),
    )


def _observations(payload: Mapping[str, object]) -> ObservationDataset:
    rows = tuple(_observation(_mapping(item)) for item in _sequence(payload["rows"]))
    return ObservationDataset(
        rows=rows,
        configuration=cast(Mapping[str, JsonValue], _mapping(payload["configuration"])),
        source_dataset_ids=tuple(_text(item) for item in _sequence(payload["source_dataset_ids"])),
        selection_policies=cast(Mapping[str, JsonValue], _mapping(payload["selection_policies"])),
        dataset_id=_text(payload["dataset_id"]),
    )


def _observation(payload: Mapping[str, object]) -> ObservationRow:
    return ObservationRow(
        event_id=UUID(_text(payload["event_id"])),
        stream_id=_text(payload["stream_id"]),
        stream_version=_int(payload["stream_version"]),
        event_type=_text(payload["event_type"]),
        event_time=_datetime(payload["event_time"]),
        received_at=_datetime(payload["received_at"]),
        persisted_at=_datetime(payload["persisted_at"]),
        global_position=_int(payload["global_position"]),
        instrument_id=_text(payload["instrument_id"]),
        basis=PriceBasis(_text(payload["basis"])),
        interval_start=_datetime(payload["interval_start"]),
        interval_end=_datetime(payload["interval_end"]),
        open=_decimal(payload["open"]),
        high=_decimal(payload["high"]),
        low=_decimal(payload["low"]),
        close=_decimal(payload["close"]),
        sample_count=_int(payload["sample_count"]),
        revision=_int(payload["revision"]),
        provenance=BarProvenance(_text(payload["provenance"])),
        quality=DataQuality(_text(payload["quality"])),
        source_provider=_text(payload["source_provider"]),
        source_environment=_text(payload["source_environment"]),
        source_external_id=_text(payload["source_external_id"]),
    )


def _panel(payload: Mapping[str, object]) -> PanelDataset:
    return PanelDataset(
        rows=tuple(_panel_row(_mapping(item)) for item in _sequence(payload["rows"])),
        observation_dataset_id=_text(payload["observation_dataset_id"]),
        foundation_configuration_id=_text(payload["foundation_configuration_id"]),
        dataset_id=_text(payload["dataset_id"]),
    )


def _panel_row(payload: Mapping[str, object]) -> PanelRow:
    return PanelRow(
        decision_time=_datetime(payload["decision_time"]),
        instrument_id=_text(payload["instrument_id"]),
        basis=PriceBasis(_text(payload["basis"])),
        feature_data_asof=_datetime(payload["feature_data_asof"]),
        latest_feature_bar_end=_datetime(payload["latest_feature_bar_end"]),
        status=PanelStatus(_text(payload["status"])),
        audit_disposition=(
            None
            if payload["audit_disposition"] is None
            else PanelAuditDisposition(_text(payload["audit_disposition"]))
        ),
        selected_event_id=_uuid(payload["selected_event_id"]),
        selected_stream_version=_optional_int(payload["selected_stream_version"]),
        selected_global_position=_optional_int(payload["selected_global_position"]),
        selected_availability_time=_optional_datetime(payload["selected_availability_time"]),
        selected_revision=_optional_int(payload["selected_revision"]),
        interval_start=_optional_datetime(payload["interval_start"]),
        interval_end=_optional_datetime(payload["interval_end"]),
        open=_optional_decimal(payload["open"]),
        high=_optional_decimal(payload["high"]),
        low=_optional_decimal(payload["low"]),
        close=_optional_decimal(payload["close"]),
        sample_count=_optional_int(payload["sample_count"]),
        quality=(None if payload["quality"] is None else DataQuality(_text(payload["quality"]))),
    )


def _targets(payload: Mapping[str, object]) -> TargetDataset:
    return TargetDataset(
        rows=tuple(_target(_mapping(item)) for item in _sequence(payload["rows"])),
        observation_dataset_id=_text(payload["observation_dataset_id"]),
        foundation_configuration_id=_text(payload["foundation_configuration_id"]),
        dataset_id=_text(payload["dataset_id"]),
    )


def _target(payload: Mapping[str, object]) -> TargetRow:
    return TargetRow(
        instrument_id=_text(payload["instrument_id"]),
        decision_time=_datetime(payload["decision_time"]),
        horizon=_duration(payload["horizon_seconds"]),
        target_basis=PriceBasis(_text(payload["target_basis"])),
        target_revision_policy=_text(payload["target_revision_policy"]),
        target_start_time=_datetime(payload["target_start_time"]),
        target_end_time=_datetime(payload["target_end_time"]),
        target_freeze_at=_datetime(payload["target_freeze_at"]),
        target_available_at=_datetime(payload["target_available_at"]),
        label_start_close=_optional_decimal(payload["label_start_close"]),
        label_end_close=_optional_decimal(payload["label_end_close"]),
        log_return=_optional_float(payload["log_return"]),
        return_disposition=ReturnDisposition(_text(payload["return_disposition"])),
        start_event_id=_uuid(payload["start_event_id"]),
        end_event_id=_uuid(payload["end_event_id"]),
        upper_log_excursion=_optional_float(payload["upper_log_excursion"]),
        lower_log_excursion=_optional_float(payload["lower_log_excursion"]),
        excursion_disposition=ExcursionDisposition(_text(payload["excursion_disposition"])),
    )


def _folds(payload: Mapping[str, object]) -> FoldDataset:
    return FoldDataset(
        folds=tuple(_fold(_mapping(item)) for item in _sequence(payload["folds"])),
        target_dataset_id=_text(payload["target_dataset_id"]),
        foundation_configuration_id=_text(payload["foundation_configuration_id"]),
        dataset_id=_text(payload["dataset_id"]),
    )


def _fold(payload: Mapping[str, object]) -> Fold:
    return Fold(
        fold_id=_text(payload["fold_id"]),
        training_start=_datetime(payload["training_start"]),
        training_cutoff=_datetime(payload["training_cutoff"]),
        validation_start=_datetime(payload["validation_start"]),
        validation_end=_datetime(payload["validation_end"]),
        embargo_end=_datetime(payload["embargo_end"]),
        training_target_ids=tuple(
            _text(item) for item in _sequence(payload["training_target_ids"])
        ),
        validation_target_ids=tuple(
            _text(item) for item in _sequence(payload["validation_target_ids"])
        ),
        holdout_excluded=bool(payload["holdout_excluded"]),
        membership_hash=_text(payload["membership_hash"]),
    )


def _forecasts(payload: Mapping[str, object]) -> ForecastDataset:
    return ForecastDataset(
        rows=tuple(_forecast(_mapping(item)) for item in _sequence(payload["rows"])),
        observation_dataset_id=_text(payload["observation_dataset_id"]),
        panel_dataset_id=_text(payload["panel_dataset_id"]),
        target_dataset_id=_text(payload["target_dataset_id"]),
        fold_dataset_id=_text(payload["fold_dataset_id"]),
        dataset_id=_text(payload["dataset_id"]),
    )


def _forecast(payload: Mapping[str, object]) -> ForecastRow:
    return ForecastRow(
        forecast_id=_text(payload["forecast_id"]),
        instrument_id=_text(payload["instrument_id"]),
        decision_time=_datetime(payload["decision_time"]),
        horizon=_duration(payload["horizon_seconds"]),
        expected_return=_float(payload["expected_return"]),
        return_unit=ReturnUnit(_text(payload["return_unit"])),
        feature_data_asof=_datetime(payload["feature_data_asof"]),
        training_cutoff=_datetime(payload["training_cutoff"]),
        observation_dataset_id=_text(payload["observation_dataset_id"]),
        panel_dataset_id=_text(payload["panel_dataset_id"]),
        target_dataset_id=_text(payload["target_dataset_id"]),
        target_id=_text(payload["target_id"]),
        fold_dataset_id=_text(payload["fold_dataset_id"]),
        experiment_id=_text(payload["experiment_id"]),
        fold_id=_text(payload["fold_id"]),
        model_id=_text(payload["model_id"]),
        model_contract=_text(payload["model_contract"]),
    )


def _coverage(payload: Mapping[str, object]) -> HorizonCoverageSummary:
    return HorizonCoverageSummary(
        horizon=_duration(payload["horizon_seconds"]),
        total_target_count=_int(payload["total_target_count"]),
        valid_return_count=_int(payload["valid_return_count"]),
        valid_excursion_count=_int(payload["valid_excursion_count"]),
        unavailable_by_freeze_count=_int(payload["unavailable_by_freeze_count"]),
        return_coverage=_float(payload["return_coverage"]),
        excursion_coverage=_float(payload["excursion_coverage"]),
        return_disposition_counts=_counts(payload["return_disposition_counts"]),
        excursion_disposition_counts=_counts(payload["excursion_disposition_counts"]),
    )


def _counts(value: object) -> tuple[tuple[str, int], ...]:
    counts: list[tuple[str, int]] = []
    for item in _sequence(value):
        pair = _sequence(item)
        if len(pair) != 2:
            raise ValueError("foundation bundle disposition count is invalid")
        counts.append((_text(pair[0]), _int(pair[1])))
    return tuple(counts)


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("foundation bundle object must be a JSON object")
    return {str(key): item for key, item in value.items()}


def _sequence(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError("foundation bundle value must be a JSON array")
    return value


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("foundation bundle text value is invalid")
    return value


def _datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(_text(value).replace("Z", "+00:00"))
    require_utc(parsed, "foundation bundle timestamp")
    return parsed


def _optional_datetime(value: object) -> datetime | None:
    return None if value is None else _datetime(value)


def _duration(value: object) -> timedelta:
    return timedelta(seconds=_float(value))


def _decimal(value: object) -> Decimal:
    return Decimal(_text(value))


def _optional_decimal(value: object) -> Decimal | None:
    return None if value is None else _decimal(value)


def _optional_float(value: object) -> float | None:
    return None if value is None else _float(value)


def _optional_int(value: object) -> int | None:
    return None if value is None else _int(value)


def _uuid(value: object) -> UUID | None:
    return None if value is None else UUID(_text(value))


def _int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError("foundation bundle integer value is invalid")
    return int(value)


def _float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError("foundation bundle numeric value is invalid")
    return float(value)
