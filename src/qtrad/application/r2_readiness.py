"""Pure R2.A readiness evaluation against one verified R1 foundation."""

from collections import Counter
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Protocol

from qtrad.domain.events import JsonValue
from qtrad.domain.folds import Fold, FoldDataset
from qtrad.domain.forecasts import ForecastDataset
from qtrad.domain.foundation import FoundationConfig, PanelDataset, ReturnDisposition, TargetDataset
from qtrad.domain.foundation_bundle import FoundationBundle
from qtrad.domain.r2_readiness import (
    CoverageCell,
    EligibilityDecision,
    EvidenceClass,
    FeatureEligibility,
    FeatureFamily,
    R2ExperimentConfig,
    R2ReadinessReport,
    ReadinessState,
)
from qtrad.domain.research import ObservationDataset

_REQUIRED_COMMON_WEEKS = 16
_WEEK = timedelta(weeks=1)
_INITIAL_TRAINING_DURATION = timedelta(weeks=6)
_VALIDATION_DURATION = timedelta(weeks=2)
_HOLDOUT_DURATION = timedelta(weeks=4)
_MINIMUM_COVERAGE = 0.90


class VerifiedFoundation(Protocol):
    @property
    def bundle(self) -> FoundationBundle: ...

    @property
    def configuration(self) -> FoundationConfig: ...

    @property
    def observations(self) -> ObservationDataset: ...

    @property
    def panel(self) -> PanelDataset: ...

    @property
    def targets(self) -> TargetDataset: ...

    @property
    def folds(self) -> FoldDataset: ...

    @property
    def forecasts(self) -> ForecastDataset: ...

    @property
    def availability_evidence(self) -> Mapping[str, JsonValue]: ...


def evaluate_r2_readiness(
    verified: VerifiedFoundation, experiment: R2ExperimentConfig
) -> R2ReadinessReport:
    """Fail closed while keeping software and scientific readiness independent."""

    _verify_exact_r1_bindings(verified, experiment)
    unmet: list[str] = []
    feature_states = {
        family: _feature_state(experiment.feature_eligibility[family]) for family in FeatureFamily
    }
    representative = ReadinessState.NOT_READY
    unmet.append(
        "representative integration requires retained R2 feature, fit, persistence, replay and "
        "evaluation evidence; an R1 bundle alone is insufficient"
    )

    group_counts = Counter(experiment.market_groups.values())
    folds = verified.folds.folds
    source_active = _source_active_intervals(verified.availability_evidence)
    coverage_matrix = _coverage_matrix(
        experiment=experiment,
        targets=verified.targets,
        folds=folds,
        source_active=source_active,
    )
    coverage_passes = all(
        cell.expected_active_opportunities > 0
        and cell.coverage is not None
        and cell.coverage >= _MINIMUM_COVERAGE
        for cells in coverage_matrix.values()
        for cell in cells
    )
    training_counts, validation_counts = _membership_counts(
        experiment=experiment,
        targets=verified.targets,
        folds=folds,
    )
    training_rows_pass = all(
        training_counts.get(instrument, 0) >= experiment.minimum_training_rows
        for instrument in experiment.confirmatory_target_instruments
    )
    validation_rows_pass = len(validation_counts) == 3 and all(
        counts.get(instrument, 0) >= experiment.minimum_outer_validation_rows
        for counts in validation_counts
        for instrument in experiment.confirmatory_target_instruments
    )
    holdout_rows_pass = all(
        next(cell.valid_targets for cell in coverage_matrix[instrument] if cell.block == "holdout")
        >= experiment.minimum_outer_validation_rows
        for instrument in experiment.confirmatory_target_instruments
    )
    candidate_start = folds[0].training_start if folds else verified.configuration.range_start
    candidate_end = experiment.holdout_range[1]
    usable_common_weeks = _usable_common_week_count(
        experiment=experiment,
        targets=verified.targets,
        source_active=source_active,
        candidate_start=candidate_start,
        candidate_end=candidate_end,
    )
    active_source_durations = {
        instrument: _union_duration(
            source_active[instrument], start=candidate_start, end=candidate_end
        ).total_seconds()
        for instrument in experiment.confirmatory_target_instruments
    }
    confirmatory_conditions = (
        (
            usable_common_weeks >= _REQUIRED_COMMON_WEEKS,
            "confirmatory evidence has fewer than 16 weekly buckets with source-active target "
            "opportunities for every confirmatory instrument",
        ),
        (
            len(experiment.confirmatory_target_instruments) >= 6,
            "confirmatory subset has fewer than 6 targets",
        ),
        (len(group_counts) >= 3, "confirmatory subset has fewer than 3 market groups"),
        (
            sum(count >= 2 for count in group_counts.values()) >= 3,
            "confirmatory subset needs at least 2 targets in each of 3 groups",
        ),
        (
            coverage_passes,
            "one or more instrument/research-block cells has no active opportunities or valid "
            "15-minute target coverage below 90%",
        ),
        (
            training_rows_pass,
            "one or more confirmatory instruments has fewer first-fold training members than "
            "minimum_training_rows",
        ),
        (
            validation_rows_pass,
            "one or more confirmatory instrument/fold cells has fewer valid members than "
            "minimum_outer_validation_rows",
        ),
        (
            holdout_rows_pass,
            "one or more confirmatory instruments has fewer valid holdout targets than "
            "minimum_outer_validation_rows",
        ),
        (len(folds) == 3, "confirmatory bundle must have exactly 3 OOF folds"),
        (
            bool(folds)
            and folds[0].training_cutoff - folds[0].training_start >= _INITIAL_TRAINING_DURATION,
            "initial training interval is shorter than 6 calendar weeks",
        ),
        (
            len(folds) == 3
            and all(
                fold.validation_end - fold.validation_start >= _VALIDATION_DURATION
                for fold in folds
            ),
            "each of the 3 OOF validation intervals must span at least 2 calendar weeks",
        ),
        (
            experiment.holdout_range[1] - experiment.holdout_range[0] >= _HOLDOUT_DURATION,
            "locked holdout is shorter than 4 calendar weeks",
        ),
        (
            all(state is not ReadinessState.PARTIALLY_READY for state in feature_states.values()),
            "feature-family eligibility has pending pre-holdout decisions",
        ),
    )
    confirmatory_data = _state_from_conditions(confirmatory_conditions, unmet)
    inner_validation_rows = ReadinessState.PARTIALLY_READY
    unmet.append(
        "minimum_inner_validation_rows requires a verified R2.C chronological inner-split artefact"
    )
    confirmatory = ReadinessState.NOT_READY
    unmet.append("confirmatory OOF readiness requires representative integration readiness")
    if (
        experiment.evidence_class is EvidenceClass.CONFIRMATORY
        and confirmatory_data is not ReadinessState.READY
    ):
        unmet.append("CONFIRMATORY evidence class requires every confirmatory data gate")
    locked = ReadinessState.NOT_READY
    unmet.append("locked holdout requires a verified immutable confirmatory OOF selection manifest")
    return R2ReadinessReport(
        experiment_configuration_id=experiment.configuration_id,
        r1_bundle_id=verified.bundle.bundle_id,
        software_contract_ready=ReadinessState.READY,
        representative_integration_ready=representative,
        confirmatory_data_ready=confirmatory_data,
        inner_validation_rows_ready=inner_validation_rows,
        confirmatory_oof_ready=confirmatory,
        locked_holdout_ready=locked,
        feature_family_states=feature_states,
        coverage_matrix=coverage_matrix,
        usable_common_week_count=usable_common_weeks,
        active_source_duration_seconds=active_source_durations,
        unmet_conditions=tuple(unmet),
        evidence_class=experiment.evidence_class,
    )


def _verify_exact_r1_bindings(verified: VerifiedFoundation, experiment: R2ExperimentConfig) -> None:
    bundle = verified.bundle
    config = verified.configuration
    build_summary = bundle.build_summary
    expected = {
        "r1_bundle_id": (experiment.r1_bundle_id, bundle.bundle_id),
        "observation_dataset_id": (
            experiment.observation_dataset_id,
            verified.observations.dataset_id,
        ),
        "foundation_configuration_id": (
            experiment.foundation_configuration_id,
            config.configuration_id,
        ),
        "panel_dataset_id": (experiment.panel_dataset_id, verified.panel.dataset_id),
        "target_dataset_id": (experiment.target_dataset_id, verified.targets.dataset_id),
        "fold_dataset_id": (experiment.fold_dataset_id, verified.folds.dataset_id),
        "r1_application_version": (
            experiment.r1_application_version,
            build_summary["application_version"],
        ),
        "r1_image_identity": (experiment.r1_image_identity, build_summary["image_identity"]),
        "ordered_instruments": (experiment.ordered_instruments, config.ordered_instruments),
        "instrument_roles": (dict(experiment.instrument_roles), dict(config.instrument_roles)),
        "horizons": (experiment.horizons, config.target_horizons),
        "holdout_range": (experiment.holdout_range, config.holdout_range),
    }
    mismatches = [name for name, (actual, wanted) in expected.items() if actual != wanted]
    if mismatches:
        raise ValueError(
            f"R2 experiment differs from verified R1 foundation: {', '.join(mismatches)}"
        )


def _feature_state(decision: EligibilityDecision) -> ReadinessState:
    if decision.state is FeatureEligibility.PENDING:
        return ReadinessState.PARTIALLY_READY
    return ReadinessState.READY


def _coverage_matrix(
    *,
    experiment: R2ExperimentConfig,
    targets: TargetDataset,
    folds: tuple[Fold, ...],
    source_active: Mapping[str, tuple[tuple[datetime, datetime], ...]],
) -> dict[str, tuple[CoverageCell, ...]]:
    blocks: list[tuple[str, datetime, datetime]] = []
    if folds:
        blocks.append(("initial_training", folds[0].training_start, folds[0].training_cutoff))
    blocks.extend(
        (f"validation_{index}", fold.validation_start, fold.validation_end)
        for index, fold in enumerate(folds, start=1)
    )
    blocks.append(("holdout", experiment.holdout_range[0], experiment.holdout_range[1]))
    rows_by_instrument = {
        instrument: tuple(
            row
            for row in targets.rows
            if row.horizon == experiment.primary_horizon and row.instrument_id == instrument
        )
        for instrument in experiment.confirmatory_target_instruments
    }
    matrix: dict[str, tuple[CoverageCell, ...]] = {}
    for instrument in experiment.confirmatory_target_instruments:
        intervals = source_active[instrument]
        cells: list[CoverageCell] = []
        for block, start, end in blocks:
            active_rows = tuple(
                row
                for row in rows_by_instrument[instrument]
                if row.instrument_id == instrument
                and start <= row.decision_time < end
                and any(
                    active_start <= row.target_start_time and row.target_end_time <= active_end
                    for active_start, active_end in intervals
                )
            )
            cells.append(
                CoverageCell(
                    instrument_id=instrument,
                    block=block,
                    block_start=start,
                    block_end=end,
                    expected_active_opportunities=len(active_rows),
                    valid_targets=sum(
                        row.return_disposition is ReturnDisposition.VALID for row in active_rows
                    ),
                )
            )
        matrix[instrument] = tuple(cells)
    return matrix


def _source_active_intervals(
    evidence: Mapping[str, JsonValue],
) -> dict[str, tuple[tuple[datetime, datetime], ...]]:
    raw = evidence["source_active_intervals"]
    if not isinstance(raw, dict):
        raise TypeError("source-active evidence must be a JSON object")
    result: dict[str, tuple[tuple[datetime, datetime], ...]] = {}
    for instrument, raw_intervals in raw.items():
        if not isinstance(raw_intervals, list):
            raise TypeError("source-active evidence has an invalid instrument or interval list")
        intervals: list[tuple[datetime, datetime]] = []
        for raw_interval in raw_intervals:
            if not isinstance(raw_interval, list) or len(raw_interval) != 2:
                raise TypeError("source-active interval must contain two timestamps")
            start = _datetime(raw_interval[0])
            end = _datetime(raw_interval[1])
            if end <= start:
                raise ValueError("source-active interval must be positive")
            intervals.append((start, end))
        result[instrument] = tuple(intervals)
    return result


def _membership_counts(
    *,
    experiment: R2ExperimentConfig,
    targets: TargetDataset,
    folds: tuple[Fold, ...],
) -> tuple[dict[str, int], tuple[dict[str, int], ...]]:
    target_by_id = {row.target_id: row for row in targets.rows}
    if len(target_by_id) != len(targets.rows):
        raise ValueError("target dataset contains duplicate identities")
    if not folds:
        return {}, ()

    def counts(target_ids: tuple[str, ...]) -> dict[str, int]:
        result = {instrument: 0 for instrument in experiment.confirmatory_target_instruments}
        for target_id in target_ids:
            row = target_by_id[target_id]
            if (
                row.instrument_id in result
                and row.horizon == experiment.primary_horizon
                and row.return_disposition is ReturnDisposition.VALID
            ):
                result[row.instrument_id] += 1
        return result

    return counts(folds[0].training_target_ids), tuple(
        counts(fold.validation_target_ids) for fold in folds
    )


def _datetime(value: JsonValue) -> datetime:
    if not isinstance(value, str):
        raise TypeError("source-active timestamp must be a string")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _usable_common_week_count(
    *,
    experiment: R2ExperimentConfig,
    targets: TargetDataset,
    source_active: Mapping[str, tuple[tuple[datetime, datetime], ...]],
    candidate_start: datetime,
    candidate_end: datetime,
) -> int:
    full_weeks = int((candidate_end - candidate_start) // _WEEK)
    rows_by_instrument = {
        instrument: tuple(
            row
            for row in targets.rows
            if row.horizon == experiment.primary_horizon and row.instrument_id == instrument
        )
        for instrument in experiment.confirmatory_target_instruments
    }
    usable = 0
    for index in range(full_weeks):
        week_start = candidate_start + index * _WEEK
        week_end = week_start + _WEEK
        if all(
            any(
                row.instrument_id == instrument
                and week_start <= row.decision_time < week_end
                and any(
                    active_start <= row.target_start_time and row.target_end_time <= active_end
                    for active_start, active_end in source_active[instrument]
                )
                for row in rows_by_instrument[instrument]
            )
            for instrument in experiment.confirmatory_target_instruments
        ):
            usable += 1
    return usable


def _union_duration(
    intervals: tuple[tuple[datetime, datetime], ...],
    *,
    start: datetime,
    end: datetime,
) -> timedelta:
    clipped = sorted(
        (max(interval_start, start), min(interval_end, end))
        for interval_start, interval_end in intervals
        if interval_end > start and interval_start < end
    )
    if not clipped:
        return timedelta(0)
    merged: list[tuple[datetime, datetime]] = []
    for interval_start, interval_end in clipped:
        if merged and interval_start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], interval_end))
        else:
            merged.append((interval_start, interval_end))
    return sum(
        (interval_end - interval_start for interval_start, interval_end in merged), timedelta()
    )


def _state_from_conditions(
    conditions: tuple[tuple[bool, str], ...], unmet: list[str]
) -> ReadinessState:
    failures = tuple(message for passed, message in conditions if not passed)
    unmet.extend(failures)
    return ReadinessState.READY if not failures else ReadinessState.NOT_READY
