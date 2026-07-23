"""Application composition for the immutable R1 foundation bundle."""

from qtrad.application.foundation import summarise_horizon_coverage
from qtrad.domain.folds import FoldDataset
from qtrad.domain.forecasts import ForecastDataset
from qtrad.domain.foundation import FoundationConfig, PanelDataset, TargetDataset
from qtrad.domain.foundation_bundle import FoundationBundle
from qtrad.domain.research import ObservationDataset


def build_foundation_bundle(
    *,
    configuration: FoundationConfig,
    observations: ObservationDataset,
    panel: PanelDataset,
    targets: TargetDataset,
    folds: FoldDataset,
    forecasts: ForecastDataset,
) -> FoundationBundle:
    """Compose and verify one complete causal foundation bundle."""

    return FoundationBundle.create(
        configuration=configuration,
        observations=observations,
        panel=panel,
        targets=targets,
        folds=folds,
        forecasts=forecasts,
        coverage=summarise_horizon_coverage(targets, configuration),
    )
