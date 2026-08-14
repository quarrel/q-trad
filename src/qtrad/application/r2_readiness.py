"""Pure R2.A readiness evaluation against one verified R1 foundation."""

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from hashlib import sha256
from types import SimpleNamespace
from typing import Protocol, cast

from qtrad.domain.events import JsonValue, to_json_value
from qtrad.domain.folds import Fold, FoldDataset
from qtrad.domain.forecasts import ForecastDataset
from qtrad.domain.foundation import FoundationConfig, PanelDataset, ReturnDisposition, TargetDataset
from qtrad.domain.foundation_bundle import AVAILABILITY_EVIDENCE_CONTRACT, FoundationBundle
from qtrad.domain.r2_holdout import HoldoutOpportunityDisposition, R2HoldoutTargetSource
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
from qtrad.domain.time import require_utc

_REQUIRED_COMMON_WEEKS = 16
_WEEK = timedelta(weeks=1)
_INITIAL_TRAINING_DURATION = timedelta(weeks=6)
_VALIDATION_DURATION = timedelta(weeks=2)
_HOLDOUT_DURATION = timedelta(weeks=4)
R2_MINIMUM_COVERAGE = Fraction(9, 10)


class R1FoundationBindings(Protocol):
    """Verified R1 identities required by every R2 consumer."""

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
    def availability_evidence(self) -> Mapping[str, JsonValue]: ...


class VerifiedFoundation(R1FoundationBindings, Protocol):
    @property
    def forecasts(self) -> ForecastDataset: ...


def evaluate_r2_readiness(
    verified: VerifiedFoundation,
    experiment: R2ExperimentConfig,
    *,
    software_verified: bool = False,
) -> R2ReadinessReport:
    """Fail closed while keeping software and scientific readiness independent."""

    verify_exact_r1_bindings(verified, experiment)
    unmet: list[str] = []
    feature_states = {
        family: _feature_state(experiment.feature_eligibility[family]) for family in FeatureFamily
    }
    representative = ReadinessState.READY if software_verified else ReadinessState.NOT_READY
    if not software_verified:
        unmet.append(
            "representative integration requires retained R2 feature, fit, persistence, replay and "
            "evaluation evidence; an R1 bundle alone is insufficient"
        )

    group_counts = Counter(experiment.market_groups.values())
    folds = verified.folds.folds
    source_active = source_active_intervals_from_evidence(verified.availability_evidence)
    coverage_matrix = _coverage_matrix(
        experiment=experiment,
        targets=verified.targets,
        folds=folds,
        source_active=source_active,
    )
    coverage_passes = all(
        cell.expected_active_opportunities > 0
        and Fraction(cell.valid_targets, cell.expected_active_opportunities) >= R2_MINIMUM_COVERAGE
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
        r1_bundle_id=verified.bundle.foundation_id,
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
        market_data_source_class=experiment.market_data_source_class,
    )


def evaluate_outcome_blind_confirmatory_readiness(
    *,
    experiment: R2ExperimentConfig,
    target_source: R2HoldoutTargetSource,
    folds: FoldDataset,
    source_active: Mapping[str, tuple[tuple[datetime, datetime], ...]],
    r1_bundle_id: str,
) -> R2ReadinessReport:
    """Replay confirmatory data gates from R1 outcome-blind projections only.

    This is deliberately separate from :func:`evaluate_r2_readiness`: F2 may
    authenticate target identities and causal availability, but must not open
    the realised target child.  The lightweight row view below contains only
    identity, timing, and availability disposition metadata.
    """

    if (
        r1_bundle_id != experiment.r1_bundle_id
        or folds.dataset_id != experiment.fold_dataset_id
        or folds.target_dataset_id != target_source.source_target_dataset_id
        or folds.foundation_configuration_id != experiment.foundation_configuration_id
    ):
        raise ValueError("outcome-blind readiness folds differ from the verified source")
    if (
        target_source.source_target_dataset_id != experiment.target_dataset_id
        or target_source.observation_dataset_id != experiment.observation_dataset_id
        or target_source.foundation_configuration_id != experiment.foundation_configuration_id
        or target_source.holdout_range != experiment.holdout_range
        or target_source.primary_horizon_seconds != int(experiment.primary_horizon.total_seconds())
        or target_source.target_instruments != tuple(experiment.target_instruments)
    ):
        raise ValueError("outcome-blind readiness source differs from the verified experiment")
    if set(source_active) != set(experiment.ordered_instruments):
        raise ValueError("outcome-blind readiness source-active universe is incomplete")
    # Existing readiness helpers operate on a TargetDataset-shaped row view.
    # SimpleNamespace is intentional here: no TargetRow is constructed, and
    # the view has no field from which a realised label can be recovered.
    blind_rows = tuple(
        SimpleNamespace(
            target_id=item.target_id,
            instrument_id=item.instrument_id,
            decision_time=item.decision_time,
            horizon=timedelta(seconds=item.target_horizon_seconds),
            target_start_time=item.target_start_time,
            target_end_time=item.target_end_time,
            target_available_at=item.target_available_at,
            return_disposition=item.target_availability_disposition,
        )
        for item in target_source.targets
    )
    blind_targets = cast(TargetDataset, SimpleNamespace(rows=blind_rows))
    fold_values = folds.folds
    coverage = _coverage_matrix(
        experiment=experiment,
        targets=blind_targets,
        folds=fold_values,
        source_active=source_active,
    )

    # The holdout cell must come from the authenticated source-derived
    # opportunity registry, rather than from an identity-only approximation.
    for instrument in experiment.confirmatory_target_instruments:
        holdout = tuple(
            item
            for item in target_source.opportunities
            if item.instrument_id == instrument
            and item.target_horizon_seconds == int(experiment.primary_horizon.total_seconds())
            and experiment.holdout_range[0] <= item.decision_time < experiment.holdout_range[1]
        )
        cells = list(coverage[instrument])
        holdout_index = next(index for index, cell in enumerate(cells) if cell.block == "holdout")
        cells[holdout_index] = CoverageCell(
            instrument_id=instrument,
            block="holdout",
            block_start=experiment.holdout_range[0],
            block_end=experiment.holdout_range[1],
            expected_active_opportunities=sum(
                item.disposition
                in (
                    HoldoutOpportunityDisposition.ELIGIBLE,
                    HoldoutOpportunityDisposition.GAP,
                )
                for item in holdout
            ),
            valid_targets=sum(
                item.disposition is HoldoutOpportunityDisposition.ELIGIBLE for item in holdout
            ),
        )
        coverage[instrument] = tuple(cells)

    feature_states = {
        family: _feature_state(experiment.feature_eligibility[family]) for family in FeatureFamily
    }
    group_counts = Counter(experiment.market_groups.values())
    coverage_passes = all(
        cell.expected_active_opportunities > 0
        and Fraction(cell.valid_targets, cell.expected_active_opportunities) >= R2_MINIMUM_COVERAGE
        for cells in coverage.values()
        for cell in cells
    )
    training_counts, validation_counts = _membership_counts(
        experiment=experiment, targets=blind_targets, folds=fold_values
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
        next(cell.valid_targets for cell in coverage[instrument] if cell.block == "holdout")
        >= experiment.minimum_outer_validation_rows
        for instrument in experiment.confirmatory_target_instruments
    )
    candidate_start = fold_values[0].training_start if fold_values else experiment.holdout_range[0]
    candidate_end = experiment.holdout_range[1]
    usable_common_weeks = _usable_common_week_count(
        experiment=experiment,
        targets=blind_targets,
        source_active=source_active,
        candidate_start=candidate_start,
        candidate_end=candidate_end,
    )
    unmet: list[str] = []
    conditions = (
        (
            usable_common_weeks >= _REQUIRED_COMMON_WEEKS,
            "confirmatory evidence has fewer than 16 weekly buckets with source-active "
            "target opportunities for every confirmatory instrument",
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
            "one or more instrument/research-block cells has no active opportunities or "
            "target coverage below 90%",
        ),
        (
            training_rows_pass,
            "one or more confirmatory instruments has too few first-fold training members",
        ),
        (
            validation_rows_pass,
            "one or more confirmatory instrument/fold cells has too few valid members",
        ),
        (
            holdout_rows_pass,
            "one or more confirmatory instruments has too few valid holdout targets",
        ),
        (len(fold_values) == 3, "confirmatory bundle must have exactly 3 OOF folds"),
        (
            bool(fold_values)
            and fold_values[0].training_cutoff - fold_values[0].training_start
            >= _INITIAL_TRAINING_DURATION,
            "initial training interval is shorter than 6 calendar weeks",
        ),
        (
            len(fold_values) == 3
            and all(
                fold.validation_end - fold.validation_start >= _VALIDATION_DURATION
                for fold in fold_values
            ),
            "each of the 3 OOF validation intervals must span at least 2 calendar weeks",
        ),
        (
            experiment.holdout_range[1] - experiment.holdout_range[0] >= _HOLDOUT_DURATION,
            "locked holdout is shorter than 4 weeks",
        ),
        (
            all(state is not ReadinessState.PARTIALLY_READY for state in feature_states.values()),
            "feature-family eligibility has pending pre-holdout decisions",
        ),
    )
    confirmatory_data = _state_from_conditions(conditions, unmet)
    active_source_durations = {
        instrument: _union_duration(
            source_active[instrument], start=candidate_start, end=candidate_end
        ).total_seconds()
        for instrument in experiment.confirmatory_target_instruments
    }
    unmet.append(
        "minimum_inner_validation_rows requires a verified R2.C chronological inner-split artefact"
    )
    return R2ReadinessReport(
        experiment_configuration_id=experiment.configuration_id,
        r1_bundle_id=r1_bundle_id,
        software_contract_ready=ReadinessState.READY,
        representative_integration_ready=ReadinessState.READY,
        confirmatory_data_ready=confirmatory_data,
        inner_validation_rows_ready=ReadinessState.PARTIALLY_READY,
        confirmatory_oof_ready=ReadinessState.NOT_READY,
        locked_holdout_ready=ReadinessState.NOT_READY,
        feature_family_states=feature_states,
        coverage_matrix=coverage,
        usable_common_week_count=usable_common_weeks,
        active_source_duration_seconds=active_source_durations,
        unmet_conditions=tuple(unmet),
        evidence_class=experiment.evidence_class,
        market_data_source_class=experiment.market_data_source_class,
    )


def verify_exact_r1_bindings(
    verified: R1FoundationBindings,
    experiment: R2ExperimentConfig,
) -> None:
    """Authenticate the complete R1 identity before any R2 calculation."""
    bundle = verified.bundle
    config = verified.configuration
    observations = verified.observations
    panel = verified.panel
    targets = verified.targets
    folds = verified.folds
    build_summary = bundle.build_summary
    availability_basis = (
        observations.selection_policies["availability_basis"]
        if "availability_basis" in observations.selection_policies
        else observations.configuration["availability_basis"]
    )
    expected = {
        "r1_bundle_id": (experiment.r1_bundle_id, bundle.foundation_id),
        "observation_dataset_id": (experiment.observation_dataset_id, observations.dataset_id),
        "foundation_configuration_id": (
            experiment.foundation_configuration_id,
            config.configuration_id,
        ),
        "panel_dataset_id": (experiment.panel_dataset_id, panel.dataset_id),
        "target_dataset_id": (experiment.target_dataset_id, targets.dataset_id),
        "fold_dataset_id": (experiment.fold_dataset_id, folds.dataset_id),
        "r1_application_version": (
            experiment.r1_application_version,
            build_summary["application_version"],
        ),
        "r1_image_identity": (experiment.r1_image_identity, build_summary["image_identity"]),
        "ordered_instruments": (experiment.ordered_instruments, config.ordered_instruments),
        "instrument_roles": (dict(experiment.instrument_roles), dict(config.instrument_roles)),
        "bundle_ordered_instruments": (
            config.ordered_instruments,
            bundle.ordered_instruments,
        ),
        "availability_basis": (
            availability_basis,
            verified.configuration.availability_basis.value,
        ),
        "horizons": (experiment.horizons, config.target_horizons),
        "holdout_range": (experiment.holdout_range, config.holdout_range),
        "bundle_range_start": (config.range_start, bundle.range_start),
        "bundle_range_end": (config.range_end, bundle.range_end),
        "bundle_configuration_id": (config.configuration_id, bundle.configuration.dataset_id),
        "bundle_observation_id": (observations.dataset_id, bundle.observations.dataset_id),
        "bundle_availability_id": (
            _availability_dataset_id(observations.dataset_id, verified.availability_evidence),
            bundle.availability.dataset_id,
        ),
        "bundle_panel_id": (panel.dataset_id, bundle.panel.dataset_id),
        "bundle_target_id": (targets.dataset_id, bundle.targets.dataset_id),
        "bundle_fold_id": (folds.dataset_id, bundle.folds.dataset_id),
        "configuration_observation_id": (
            observations.dataset_id,
            config.observation_dataset_id,
        ),
        "panel_observation_id": (observations.dataset_id, panel.observation_dataset_id),
        "panel_configuration_id": (
            config.configuration_id,
            panel.foundation_configuration_id,
        ),
        "target_observation_id": (observations.dataset_id, targets.observation_dataset_id),
        "target_configuration_id": (
            config.configuration_id,
            targets.foundation_configuration_id,
        ),
        "fold_configuration_id": (
            config.configuration_id,
            folds.foundation_configuration_id,
        ),
        "fold_target_id": (targets.dataset_id, folds.target_dataset_id),
    }
    mismatches = [name for name, (actual, wanted) in expected.items() if actual != wanted]
    if mismatches:
        raise ValueError(f"R2 experiment or foundation binding mismatch: {', '.join(mismatches)}")
    _validate_authenticated_availability(
        verified.availability_evidence,
        universe=config.ordered_instruments,
        observation_dataset_id=observations.dataset_id,
    )


def source_active_intervals_from_evidence(
    evidence: Mapping[str, JsonValue],
) -> dict[str, tuple[tuple[datetime, datetime], ...]]:
    """Decode the one authenticated source-active evidence representation."""
    raw = evidence["source_active_intervals"]
    if not isinstance(raw, dict):
        raise TypeError("source-active evidence must be a JSON object")
    result: dict[str, tuple[tuple[datetime, datetime], ...]] = {}
    for instrument, raw_intervals in raw.items():
        if not instrument:
            raise TypeError("source-active evidence has an invalid instrument")
        if not isinstance(raw_intervals, list):
            raise TypeError("source-active evidence has an invalid interval list")
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


def _validate_authenticated_availability(
    evidence: Mapping[str, JsonValue],
    *,
    universe: tuple[str, ...],
    observation_dataset_id: str,
) -> None:
    expected_keys = {
        "availability_delay_report",
        "revision_delay_report",
        "data_gaps",
        "source_active_intervals",
        "lineage_summary",
        "observation_bounds",
    }
    if set(evidence) != expected_keys:
        raise ValueError("authenticated availability evidence has unknown or missing fields")
    intervals = source_active_intervals_from_evidence(evidence)
    if set(intervals) != set(universe):
        raise ValueError(
            "authenticated source-active evidence differs from the foundation universe"
        )
    if tuple(universe) != tuple(dict.fromkeys(universe)):
        raise ValueError("foundation universe is not canonical and unique")
    bounds = evidence["observation_bounds"]
    if not isinstance(bounds, dict) or set(bounds) != {"interval_start", "interval_end"}:
        raise ValueError("authenticated observation bounds are malformed")
    bound_start = _datetime(bounds["interval_start"])
    bound_end = _datetime(bounds["interval_end"])
    if bound_end <= bound_start:
        raise ValueError("authenticated observation bounds must be positive")
    for instrument, instrument_intervals in intervals.items():
        if instrument_intervals != tuple(sorted(instrument_intervals)):
            raise ValueError(f"authenticated source-active intervals are not ordered: {instrument}")
        if any(start < bound_start or end > bound_end for start, end in instrument_intervals):
            raise ValueError(
                f"authenticated source-active intervals exceed observation bounds: {instrument}"
            )
    policies = evidence.get("selection_policies")
    if policies is not None:
        raise ValueError("availability evidence contains an unexpected selection policy field")
    expected_id = _availability_dataset_id(observation_dataset_id, evidence)
    if not expected_id:
        raise ValueError("availability evidence identity is invalid")


def _availability_dataset_id(
    observation_dataset_id: str,
    evidence: Mapping[str, JsonValue],
) -> str:
    canonical = {
        "contract": AVAILABILITY_EVIDENCE_CONTRACT,
        "observation_dataset_id": observation_dataset_id,
        "evidence": to_json_value(dict(evidence)),
    }
    return sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


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
    blocks = r2_research_blocks(folds, experiment.holdout_range)
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


def r2_research_blocks(
    folds: Sequence[Fold], holdout_range: tuple[datetime, datetime]
) -> tuple[tuple[str, datetime, datetime], ...]:
    """Return the identity-bearing R2 training, validation, and holdout blocks."""

    blocks: list[tuple[str, datetime, datetime]] = []
    if folds:
        blocks.append(("initial_training", folds[0].training_start, folds[0].training_cutoff))
    blocks.extend(
        (f"validation_{index}", fold.validation_start, fold.validation_end)
        for index, fold in enumerate(folds, start=1)
    )
    blocks.append(("holdout", holdout_range[0], holdout_range[1]))
    return tuple(blocks)


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
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require_utc(parsed, "authenticated source-active timestamp")
    return parsed.astimezone(UTC)


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
