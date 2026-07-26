"""Application composition for the R1 causal observation dataset."""

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta

from qtrad.domain.events import JsonValue
from qtrad.domain.research import (
    AvailabilityDelayReport,
    ObservationCandidate,
    ObservationDataset,
    RevisionDelayReport,
    build_availability_delay_report,
    build_observation_rows,
    build_revision_delay_report,
)


def build_observation_dataset(
    candidates: Sequence[ObservationCandidate],
    *,
    configuration: Mapping[str, JsonValue],
    source_dataset_ids: Sequence[str] = (),
    selection_policies: Mapping[str, JsonValue] | None = None,
) -> ObservationDataset:
    """Build a deterministic observation dataset from a lineage-joined input."""

    return ObservationDataset.create(
        build_observation_rows(candidates),
        configuration=configuration,
        source_dataset_ids=source_dataset_ids,
        selection_policies=selection_policies,
    )


def measure_availability_delay(
    dataset: ObservationDataset,
    *,
    calibration_start: datetime,
    calibration_end: datetime,
    configured_percentile: float,
    safety_margin: timedelta,
    grid_resolution: timedelta,
) -> AvailabilityDelayReport:
    """Create explicit feature-lag evidence without using any model outcome."""

    return build_availability_delay_report(
        dataset.rows,
        calibration_start=calibration_start,
        calibration_end=calibration_end,
        configured_percentile=configured_percentile,
        safety_margin=safety_margin,
        grid_resolution=grid_resolution,
    )


def measure_revision_delay(
    dataset: ObservationDataset,
    *,
    calibration_start: datetime,
    calibration_end: datetime,
) -> RevisionDelayReport:
    """Create correction-maturity evidence separately from first-revision lag."""

    return build_revision_delay_report(
        dataset.rows,
        calibration_start=calibration_start,
        calibration_end=calibration_end,
    )
