"""Pure R2.A readiness evaluation against one verified R1 foundation."""

from collections import Counter
from collections.abc import Mapping
from datetime import timedelta
from typing import Protocol

from qtrad.domain.events import JsonValue
from qtrad.domain.folds import FoldDataset
from qtrad.domain.forecasts import ForecastDataset
from qtrad.domain.foundation import FoundationConfig, PanelDataset, ReturnDisposition, TargetDataset
from qtrad.domain.foundation_bundle import FoundationBundle
from qtrad.domain.r2_readiness import (
    EvidenceClass,
    FeatureEligibility,
    FeatureFamily,
    R2ExperimentConfig,
    R2ReadinessReport,
    ReadinessState,
)
from qtrad.domain.research import ObservationDataset

_CONFIRMATORY_DURATION = timedelta(weeks=16)
_HOLDOUT_DURATION = timedelta(weeks=4)


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
    representative_conditions = (
        (
            len(experiment.ordered_instruments) > 1,
            "representative bundle needs multiple instruments",
        ),
        (bool(verified.targets.rows), "representative bundle has no target rows"),
        (bool(verified.folds.folds), "representative bundle has no chronological folds"),
    )
    representative = _state_from_conditions(representative_conditions, unmet)

    group_counts = Counter(experiment.market_groups.values())
    primary_rows = tuple(
        row
        for row in verified.targets.rows
        if row.horizon == experiment.primary_horizon
        and row.instrument_id in experiment.target_instruments
    )
    valid_primary = sum(row.return_disposition is ReturnDisposition.VALID for row in primary_rows)
    coverage = valid_primary / len(primary_rows) if primary_rows else 0.0
    confirmatory_conditions = (
        (
            verified.configuration.range_end - verified.configuration.range_start
            >= _CONFIRMATORY_DURATION,
            "confirmatory common evidence is shorter than 16 calendar weeks",
        ),
        (len(experiment.target_instruments) >= 6, "confirmatory subset has fewer than 6 targets"),
        (len(group_counts) >= 3, "confirmatory subset has fewer than 3 market groups"),
        (
            sum(count >= 2 for count in group_counts.values()) >= 3,
            "confirmatory subset needs at least 2 targets in each of 3 groups",
        ),
        (coverage >= 0.90, "primary-horizon valid target coverage is below 90%"),
        (len(verified.folds.folds) >= 3, "confirmatory bundle has fewer than 3 OOF folds"),
        (
            experiment.holdout_range[1] - experiment.holdout_range[0] >= _HOLDOUT_DURATION,
            "locked holdout is shorter than 4 calendar weeks",
        ),
        (
            all(state is not ReadinessState.PARTIALLY_READY for state in feature_states.values()),
            "feature-family eligibility has pending pre-holdout decisions",
        ),
    )
    confirmatory = _state_from_conditions(confirmatory_conditions, unmet)
    if (
        experiment.evidence_class is EvidenceClass.CONFIRMATORY
        and confirmatory is not ReadinessState.READY
    ):
        unmet.append("CONFIRMATORY evidence class requires every confirmatory readiness gate")
    locked = ReadinessState.NOT_READY
    unmet.append("locked holdout requires a verified immutable confirmatory OOF selection manifest")
    return R2ReadinessReport(
        experiment_configuration_id=experiment.configuration_id,
        r1_bundle_id=verified.bundle.bundle_id,
        software_contract_ready=ReadinessState.READY,
        representative_integration_ready=representative,
        confirmatory_oof_ready=confirmatory,
        locked_holdout_ready=locked,
        feature_family_states=feature_states,
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


def _feature_state(eligibility: FeatureEligibility) -> ReadinessState:
    if eligibility is FeatureEligibility.PENDING:
        return ReadinessState.PARTIALLY_READY
    return ReadinessState.READY


def _state_from_conditions(
    conditions: tuple[tuple[bool, str], ...], unmet: list[str]
) -> ReadinessState:
    failures = tuple(message for passed, message in conditions if not passed)
    unmet.extend(failures)
    return ReadinessState.READY if not failures else ReadinessState.NOT_READY
