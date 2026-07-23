"""Cross-artefact verification and composition for the thin R1 bundle."""

from collections.abc import Mapping, Sequence

from qtrad.application.foundation import summarise_horizon_coverage
from qtrad.domain.events import JsonValue
from qtrad.domain.folds import FoldDataset
from qtrad.domain.forecasts import ForecastDataset
from qtrad.domain.foundation import (
    FoundationConfig,
    HorizonCoverageSummary,
    PanelDataset,
    PanelStatus,
    TargetDataset,
)
from qtrad.domain.foundation_bundle import ArtifactReference, FoundationBundle
from qtrad.domain.research import ObservationDataset


def build_foundation_bundle(
    *,
    configuration: FoundationConfig,
    observations: ObservationDataset,
    panel: PanelDataset,
    targets: TargetDataset,
    folds: FoldDataset,
    forecasts: ForecastDataset,
    configuration_reference: ArtifactReference,
    observation_reference: ArtifactReference,
    availability_reference: ArtifactReference,
    panel_reference: ArtifactReference,
    target_reference: ArtifactReference,
    fold_reference: ArtifactReference,
    forecast_reference: ArtifactReference,
    build_summary: Mapping[str, JsonValue],
) -> FoundationBundle:
    """Verify loaded children, then emit references without duplicating their rows."""

    coverage = summarise_horizon_coverage(targets, configuration)
    verify_foundation_children(
        configuration=configuration,
        observations=observations,
        panel=panel,
        targets=targets,
        folds=folds,
        forecasts=forecasts,
        coverage=coverage,
    )
    expected_ids = (
        (configuration_reference.dataset_id, configuration.configuration_id),
        (observation_reference.dataset_id, observations.dataset_id),
        (panel_reference.dataset_id, panel.dataset_id),
        (target_reference.dataset_id, targets.dataset_id),
        (fold_reference.dataset_id, folds.dataset_id),
        (forecast_reference.dataset_id, forecasts.dataset_id),
    )
    if any(reference_id != dataset_id for reference_id, dataset_id in expected_ids):
        raise ValueError("foundation child reference does not match its semantic dataset")
    return FoundationBundle.create(
        configuration=configuration_reference,
        observations=observation_reference,
        availability=availability_reference,
        panel=panel_reference,
        targets=target_reference,
        folds=fold_reference,
        forecasts=forecast_reference,
        ordered_instruments=configuration.ordered_instruments,
        range_start=configuration.range_start,
        range_end=configuration.range_end,
        coverage=coverage,
        build_summary=build_summary,
    )


def verify_foundation_children(
    *,
    configuration: FoundationConfig,
    observations: ObservationDataset,
    panel: PanelDataset,
    targets: TargetDataset,
    folds: FoldDataset,
    forecasts: ForecastDataset,
    coverage: Sequence[HorizonCoverageSummary],
) -> None:
    """Rebuild semantic identities and reject broken cross-dataset references."""

    configuration_id = configuration.configuration_id
    if observations.dataset_id != configuration.observation_dataset_id:
        raise ValueError("foundation observations do not match configuration")
    if panel.observation_dataset_id != observations.dataset_id:
        raise ValueError("foundation panel does not match observations")
    if targets.observation_dataset_id != observations.dataset_id:
        raise ValueError("foundation targets do not match observations")
    if panel.foundation_configuration_id != configuration_id:
        raise ValueError("foundation panel does not match configuration")
    if targets.foundation_configuration_id != configuration_id:
        raise ValueError("foundation targets do not match configuration")
    if folds.target_dataset_id != targets.dataset_id:
        raise ValueError("foundation folds do not match targets")
    if folds.foundation_configuration_id != configuration_id:
        raise ValueError("foundation folds do not match configuration")
    if forecasts.observation_dataset_id != observations.dataset_id:
        raise ValueError("foundation forecasts do not match observations")
    if forecasts.panel_dataset_id != panel.dataset_id:
        raise ValueError("foundation forecasts do not match panel")
    if forecasts.target_dataset_id != targets.dataset_id:
        raise ValueError("foundation forecasts do not match targets")
    if forecasts.fold_dataset_id != folds.dataset_id:
        raise ValueError("foundation forecasts do not match folds")

    rebuilt_observations = ObservationDataset.create(
        observations.rows,
        configuration=observations.configuration,
        source_dataset_ids=observations.source_dataset_ids,
        selection_policies=observations.selection_policies,
    )
    rebuilt_panel = PanelDataset.create(
        panel.rows,
        observation_dataset_id=panel.observation_dataset_id,
        foundation_configuration_id=panel.foundation_configuration_id,
    )
    rebuilt_targets = TargetDataset.create(
        targets.rows,
        observation_dataset_id=targets.observation_dataset_id,
        foundation_configuration_id=targets.foundation_configuration_id,
    )
    rebuilt_folds = FoldDataset.create(
        folds.folds,
        target_dataset_id=folds.target_dataset_id,
        foundation_configuration_id=folds.foundation_configuration_id,
    )
    rebuilt_forecasts = ForecastDataset.create(
        forecasts.rows,
        observation_dataset_id=forecasts.observation_dataset_id,
        panel_dataset_id=forecasts.panel_dataset_id,
        target_dataset_id=forecasts.target_dataset_id,
        fold_dataset_id=forecasts.fold_dataset_id,
    )
    rebuilt = (
        rebuilt_observations.dataset_id,
        rebuilt_panel.dataset_id,
        rebuilt_targets.dataset_id,
        rebuilt_folds.dataset_id,
        rebuilt_forecasts.dataset_id,
    )
    expected = (
        observations.dataset_id,
        panel.dataset_id,
        targets.dataset_id,
        folds.dataset_id,
        forecasts.dataset_id,
    )
    if rebuilt != expected:
        raise ValueError("foundation child fails independent semantic verification")
    _verify_coverage(targets, coverage, configuration)
    _verify_forecast_lineage(configuration, panel, targets, folds, forecasts)


def _verify_coverage(
    targets: TargetDataset,
    summaries: Sequence[HorizonCoverageSummary],
    configuration: FoundationConfig,
) -> None:
    if tuple(summary.horizon for summary in summaries) != configuration.target_horizons:
        raise ValueError("foundation coverage does not match configured horizons")
    if tuple(summaries) != summarise_horizon_coverage(targets, configuration):
        raise ValueError("foundation coverage is inconsistent with target dispositions")


def _verify_forecast_lineage(
    configuration: FoundationConfig,
    panel: PanelDataset,
    targets: TargetDataset,
    folds: FoldDataset,
    forecasts: ForecastDataset,
) -> None:
    target_by_id = {row.target_id: row for row in targets.rows}
    fold_by_id = {fold.fold_id: fold for fold in folds.folds}
    panel_by_key = {(row.instrument_id, row.decision_time, row.basis): row for row in panel.rows}
    for forecast in forecasts.rows:
        target = target_by_id.get(forecast.target_id)
        fold = fold_by_id.get(forecast.fold_id)
        if target is None or fold is None:
            raise ValueError("foundation forecast references an unknown target or fold")
        if forecast.target_id not in fold.validation_target_ids:
            raise ValueError("foundation forecast is not in fold validation membership")
        if forecast.training_cutoff != fold.training_cutoff:
            raise ValueError("foundation forecast training cutoff does not match its fold")
        panel_row = panel_by_key.get(
            (forecast.instrument_id, forecast.decision_time, configuration.target_basis)
        )
        if (
            panel_row is None
            or panel_row.status is not PanelStatus.OBSERVED
            or forecast.feature_data_asof != panel_row.feature_data_asof
        ):
            raise ValueError("foundation forecast feature cutoff does not match its panel row")
        if (
            forecast.instrument_id != target.instrument_id
            or forecast.decision_time != target.decision_time
            or forecast.horizon != target.horizon
        ):
            raise ValueError("foundation forecast does not match its target")
        if not fold.validation_start <= forecast.decision_time < fold.validation_end:
            raise ValueError("foundation forecast is outside its fold validation interval")
        if (
            target.target_end_time > configuration.holdout_range[0]
            or target.target_freeze_at > configuration.holdout_range[0]
            or target.target_available_at > configuration.holdout_range[0]
        ):
            raise ValueError("foundation forecast target dependency enters the holdout")
        if (
            configuration.holdout_range[0]
            <= forecast.decision_time
            < configuration.holdout_range[1]
        ):
            raise ValueError("foundation forecast contains a holdout row")
