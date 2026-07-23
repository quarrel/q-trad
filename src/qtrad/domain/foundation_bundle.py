"""Thin, hash-bound bundle for the causal R1 foundation artefacts."""

import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from typing import ClassVar, cast

from qtrad.domain.events import JsonValue, to_json_value
from qtrad.domain.folds import FoldDataset
from qtrad.domain.forecasts import ForecastDataset
from qtrad.domain.foundation import (
    ExcursionDisposition,
    FoundationConfig,
    HorizonCoverageSummary,
    PanelDataset,
    ReturnDisposition,
    TargetDataset,
)
from qtrad.domain.research import ObservationDataset

FOUNDATION_BUNDLE_CONTRACT = "qtrad-research-foundation-bundle-v1"


@dataclass(frozen=True, slots=True)
class FoundationBundle:
    """All R1 child artefacts with independently checkable lineage."""

    configuration: FoundationConfig
    observations: ObservationDataset
    panel: PanelDataset
    targets: TargetDataset
    folds: FoldDataset
    forecasts: ForecastDataset
    coverage: tuple[HorizonCoverageSummary, ...]
    bundle_id: str

    CONTRACT: ClassVar[str] = FOUNDATION_BUNDLE_CONTRACT

    def __post_init__(self) -> None:
        if not self.bundle_id:
            raise ValueError("foundation bundle ID must be non-empty")
        if tuple(sorted(self.coverage, key=lambda summary: summary.horizon)) != self.coverage:
            raise ValueError("foundation coverage summaries must use horizon ordering")
        expected = _bundle_hash(self)
        if self.bundle_id != expected:
            raise ValueError("foundation bundle ID does not match its child artefacts")
        self.verify()

    @classmethod
    def create(
        cls,
        *,
        configuration: FoundationConfig,
        observations: ObservationDataset,
        panel: PanelDataset,
        targets: TargetDataset,
        folds: FoldDataset,
        forecasts: ForecastDataset,
        coverage: Sequence[HorizonCoverageSummary],
    ) -> "FoundationBundle":
        ordered_coverage = tuple(sorted(coverage, key=lambda summary: summary.horizon))
        return cls(
            configuration=configuration,
            observations=observations,
            panel=panel,
            targets=targets,
            folds=folds,
            forecasts=forecasts,
            coverage=ordered_coverage,
            bundle_id=_bundle_hash(
                _UnboundBundle(
                    configuration=configuration,
                    observations=observations,
                    panel=panel,
                    targets=targets,
                    folds=folds,
                    forecasts=forecasts,
                    coverage=ordered_coverage,
                )
            ),
        )

    def verify(self) -> None:
        """Recompute every child identity and reject broken cross-references."""

        _verify_children(self)
        if (
            tuple(summary.horizon for summary in self.coverage)
            != self.configuration.target_horizons
        ):
            raise ValueError("foundation coverage does not match configured horizons")
        _verify_coverage(self.targets, self.coverage)
        _verify_forecast_lineage(self)

    def as_json(self) -> dict[str, JsonValue]:
        payload = _bundle_payload(self)
        payload["bundle_id"] = self.bundle_id
        return cast(dict[str, JsonValue], to_json_value(payload))


@dataclass(frozen=True, slots=True)
class _UnboundBundle:
    configuration: FoundationConfig
    observations: ObservationDataset
    panel: PanelDataset
    targets: TargetDataset
    folds: FoldDataset
    forecasts: ForecastDataset
    coverage: tuple[HorizonCoverageSummary, ...]


def _verify_children(bundle: FoundationBundle) -> None:
    configuration_id = bundle.configuration.configuration_id
    if bundle.observations.dataset_id != bundle.configuration.observation_dataset_id:
        raise ValueError("foundation observations do not match configuration")
    if bundle.panel.observation_dataset_id != bundle.observations.dataset_id:
        raise ValueError("foundation panel does not match observations")
    if bundle.targets.observation_dataset_id != bundle.observations.dataset_id:
        raise ValueError("foundation targets do not match observations")
    if bundle.panel.foundation_configuration_id != configuration_id:
        raise ValueError("foundation panel does not match configuration")
    if bundle.targets.foundation_configuration_id != configuration_id:
        raise ValueError("foundation targets do not match configuration")
    if bundle.folds.target_dataset_id != bundle.targets.dataset_id:
        raise ValueError("foundation folds do not match targets")
    if bundle.folds.foundation_configuration_id != configuration_id:
        raise ValueError("foundation folds do not match configuration")
    if bundle.forecasts.observation_dataset_id != bundle.observations.dataset_id:
        raise ValueError("foundation forecasts do not match observations")
    if bundle.forecasts.panel_dataset_id != bundle.panel.dataset_id:
        raise ValueError("foundation forecasts do not match panel")
    if bundle.forecasts.target_dataset_id != bundle.targets.dataset_id:
        raise ValueError("foundation forecasts do not match targets")
    if bundle.forecasts.fold_dataset_id != bundle.folds.dataset_id:
        raise ValueError("foundation forecasts do not match folds")

    rebuilt_observations = ObservationDataset.create(
        bundle.observations.rows,
        configuration=bundle.observations.configuration,
        source_dataset_ids=bundle.observations.source_dataset_ids,
        selection_policies=bundle.observations.selection_policies,
    )
    if rebuilt_observations.dataset_id != bundle.observations.dataset_id:
        raise ValueError("foundation observations fail independent verification")
    rebuilt_panel = PanelDataset.create(
        bundle.panel.rows,
        observation_dataset_id=bundle.panel.observation_dataset_id,
        foundation_configuration_id=bundle.panel.foundation_configuration_id,
    )
    if rebuilt_panel.dataset_id != bundle.panel.dataset_id:
        raise ValueError("foundation panel fails independent verification")
    rebuilt_targets = TargetDataset.create(
        bundle.targets.rows,
        observation_dataset_id=bundle.targets.observation_dataset_id,
        foundation_configuration_id=bundle.targets.foundation_configuration_id,
    )
    if rebuilt_targets.dataset_id != bundle.targets.dataset_id:
        raise ValueError("foundation targets fail independent verification")
    rebuilt_folds = FoldDataset.create(
        bundle.folds.folds,
        target_dataset_id=bundle.folds.target_dataset_id,
        foundation_configuration_id=bundle.folds.foundation_configuration_id,
    )
    if rebuilt_folds.dataset_id != bundle.folds.dataset_id:
        raise ValueError("foundation folds fail independent verification")
    rebuilt_forecasts = ForecastDataset.create(
        bundle.forecasts.rows,
        observation_dataset_id=bundle.forecasts.observation_dataset_id,
        panel_dataset_id=bundle.forecasts.panel_dataset_id,
        target_dataset_id=bundle.forecasts.target_dataset_id,
        fold_dataset_id=bundle.forecasts.fold_dataset_id,
    )
    if rebuilt_forecasts.dataset_id != bundle.forecasts.dataset_id:
        raise ValueError("foundation forecasts fail independent verification")


def _verify_coverage(
    targets: TargetDataset,
    summaries: Sequence[HorizonCoverageSummary],
) -> None:
    for summary in summaries:
        rows = tuple(row for row in targets.rows if row.horizon == summary.horizon)
        returns = Counter(row.return_disposition.value for row in rows)
        excursions = Counter(row.excursion_disposition.value for row in rows)
        if summary.total_target_count != len(rows):
            raise ValueError("foundation coverage target count is inconsistent")
        if summary.valid_return_count != returns[ReturnDisposition.VALID.value]:
            raise ValueError("foundation coverage return count is inconsistent")
        if summary.valid_excursion_count != excursions[ExcursionDisposition.VALID.value]:
            raise ValueError("foundation coverage excursion count is inconsistent")
        if (
            summary.unavailable_by_freeze_count
            != returns[ReturnDisposition.UNAVAILABLE_BY_FREEZE.value]
        ):
            raise ValueError("foundation coverage availability count is inconsistent")
        expected_return_coverage = (
            returns[ReturnDisposition.VALID.value] / len(rows) if rows else 0.0
        )
        expected_excursion_coverage = (
            excursions[ExcursionDisposition.VALID.value] / len(rows) if rows else 0.0
        )
        if summary.return_coverage != expected_return_coverage:
            raise ValueError("foundation return coverage ratio is inconsistent")
        if summary.excursion_coverage != expected_excursion_coverage:
            raise ValueError("foundation excursion coverage ratio is inconsistent")
        if summary.return_disposition_counts != tuple(sorted(returns.items())):
            raise ValueError("foundation coverage return dispositions are inconsistent")
        if summary.excursion_disposition_counts != tuple(sorted(excursions.items())):
            raise ValueError("foundation coverage excursion dispositions are inconsistent")


def _verify_forecast_lineage(bundle: FoundationBundle) -> None:
    targets = {row.target_id: row for row in bundle.targets.rows}
    folds = {fold.fold_id: fold for fold in bundle.folds.folds}
    for forecast in bundle.forecasts.rows:
        target = targets.get(forecast.target_id)
        fold = folds.get(forecast.fold_id)
        if target is None or fold is None:
            raise ValueError("foundation forecast references an unknown target or fold")
        if forecast.target_id not in fold.validation_target_ids:
            raise ValueError("foundation forecast is not in fold validation membership")
        if forecast.training_cutoff != fold.training_cutoff:
            raise ValueError("foundation forecast training cutoff does not match its fold")
        if (
            forecast.instrument_id != target.instrument_id
            or forecast.decision_time != target.decision_time
            or forecast.horizon != target.horizon
        ):
            raise ValueError("foundation forecast does not match its target")
        if not fold.validation_start <= forecast.decision_time < fold.validation_end:
            raise ValueError("foundation forecast is outside its fold validation interval")
        if (
            bundle.configuration.holdout_range[0]
            <= forecast.decision_time
            < bundle.configuration.holdout_range[1]
        ):
            raise ValueError("foundation forecast contains a holdout row")


def _bundle_payload(bundle: FoundationBundle | _UnboundBundle) -> dict[str, object]:
    return {
        "contract": FOUNDATION_BUNDLE_CONTRACT,
        "schema_version": 1,
        "configuration": bundle.configuration.as_json(),
        "observations": {
            "contract": "qtrad-research-observations-v1",
            "dataset_id": bundle.observations.dataset_id,
            "configuration": bundle.observations.configuration,
            "source_dataset_ids": list(bundle.observations.source_dataset_ids),
            "selection_policies": bundle.observations.selection_policies,
            "rows": [row.as_json() for row in bundle.observations.rows],
        },
        "panel": {
            "dataset_id": bundle.panel.dataset_id,
            "observation_dataset_id": bundle.panel.observation_dataset_id,
            "foundation_configuration_id": bundle.panel.foundation_configuration_id,
            "rows": [row.as_json() for row in bundle.panel.rows],
        },
        "targets": {
            "dataset_id": bundle.targets.dataset_id,
            "observation_dataset_id": bundle.targets.observation_dataset_id,
            "foundation_configuration_id": bundle.targets.foundation_configuration_id,
            "rows": [row.as_json() for row in bundle.targets.rows],
        },
        "folds": {
            "dataset_id": bundle.folds.dataset_id,
            "target_dataset_id": bundle.folds.target_dataset_id,
            "foundation_configuration_id": bundle.folds.foundation_configuration_id,
            "folds": [fold.as_json() for fold in bundle.folds.folds],
        },
        "forecasts": {
            "dataset_id": bundle.forecasts.dataset_id,
            "observation_dataset_id": bundle.forecasts.observation_dataset_id,
            "panel_dataset_id": bundle.forecasts.panel_dataset_id,
            "target_dataset_id": bundle.forecasts.target_dataset_id,
            "fold_dataset_id": bundle.forecasts.fold_dataset_id,
            "rows": [row.as_json() for row in bundle.forecasts.rows],
        },
        "coverage": [summary.as_json() for summary in bundle.coverage],
    }


def _bundle_hash(bundle: FoundationBundle | _UnboundBundle) -> str:
    canonical = to_json_value(_bundle_payload(bundle))
    return sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
