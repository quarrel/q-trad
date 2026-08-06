"""Fixed source-specific R2 representative profile for IBKR historical research."""

from __future__ import annotations

from datetime import timedelta

from qtrad.domain.foundation import InstrumentRole
from qtrad.domain.market_data import MarketDataSourceClass
from qtrad.domain.r2_readiness import (
    EvidenceClass,
    FeatureEligibility,
    FeatureFamily,
    FeatureSet,
    ModelFamily,
    R2ExperimentConfig,
)

IBKR_HISTORICAL_PROFILE = "IBKR_HISTORICAL_V1"
IBKR_HISTORICAL_PROFILE_ARGUMENT = "ibkr-historical-v1"
IBKR_HISTORICAL_SOURCE = MarketDataSourceClass.IBKR_HISTORICAL_RESEARCH
IBKR_HISTORICAL_EVIDENCE = EvidenceClass.IMPLEMENTATION

IBKR_HISTORICAL_UNIVERSE: tuple[str, ...] = (
    "fx:aud-usd",
    "fx:eur-usd",
    "index:australia-200",
    "index:us-500",
    "commodity:spot-gold",
    "commodity:us-crude",
)

IBKR_HISTORICAL_GROUPS: dict[str, str] = {
    "fx:aud-usd": "FX",
    "fx:eur-usd": "FX",
    "index:australia-200": "INDEX",
    "index:us-500": "INDEX",
    "commodity:spot-gold": "COMMODITY",
    "commodity:us-crude": "COMMODITY",
}

IBKR_HISTORICAL_FEATURE_SETS: tuple[FeatureSet, ...] = (
    FeatureSet("L0", (FeatureFamily.LOCAL_RETURNS, FeatureFamily.TIME_AVAILABILITY)),
    FeatureSet(
        "L1",
        (
            FeatureFamily.LOCAL_RETURNS,
            FeatureFamily.TIME_AVAILABILITY,
            FeatureFamily.LOCAL_VOLATILITY_RANGE,
        ),
    ),
    FeatureSet(
        "P0",
        (
            FeatureFamily.LOCAL_RETURNS,
            FeatureFamily.TIME_AVAILABILITY,
            FeatureFamily.LOCAL_VOLATILITY_RANGE,
        ),
    ),
    FeatureSet(
        "P1",
        (
            FeatureFamily.LOCAL_RETURNS,
            FeatureFamily.TIME_AVAILABILITY,
            FeatureFamily.LOCAL_VOLATILITY_RANGE,
            FeatureFamily.POOLED_CROSS_ASSET,
        ),
    ),
)

IBKR_HISTORICAL_HORIZON = timedelta(minutes=15)
IBKR_HISTORICAL_ALPHA_GRID = (0.01, 0.1, 1.0, 10.0)
IBKR_HISTORICAL_FEATURE_WINDOWS = (timedelta(minutes=1), timedelta(minutes=5))
IBKR_HISTORICAL_MINIMUM_TRAINING_ROWS = 100
IBKR_HISTORICAL_MINIMUM_INNER_VALIDATION_ROWS = 20
IBKR_HISTORICAL_MINIMUM_OUTER_VALIDATION_ROWS = 20


def validate_ibkr_historical_profile(experiment: R2ExperimentConfig) -> None:
    """Reject an experiment that is not the fixed implementation-only profile."""

    if experiment.market_data_source_class is not IBKR_HISTORICAL_SOURCE:
        raise ValueError("IBKR historical profile requires IBKR_HISTORICAL_RESEARCH")
    if experiment.evidence_class is not IBKR_HISTORICAL_EVIDENCE:
        raise ValueError("IBKR historical profile requires implementation-only evidence")
    if experiment.ordered_instruments != IBKR_HISTORICAL_UNIVERSE:
        raise ValueError("IBKR historical profile has the wrong ordered six-instrument universe")
    expected_roles = {instrument: InstrumentRole.TARGET for instrument in IBKR_HISTORICAL_UNIVERSE}
    if dict(experiment.instrument_roles) != expected_roles:
        raise ValueError("IBKR historical profile requires every fixed instrument to be a TARGET")
    if experiment.target_instruments != IBKR_HISTORICAL_UNIVERSE:
        raise ValueError("IBKR historical profile target universe is not the fixed six")
    if experiment.confirmatory_target_instruments != IBKR_HISTORICAL_UNIVERSE:
        raise ValueError("IBKR historical profile confirmatory universe is not the fixed six")
    if dict(experiment.market_groups) != IBKR_HISTORICAL_GROUPS:
        raise ValueError("IBKR historical profile group assignments differ from the fixed pairs")
    if experiment.horizons != (IBKR_HISTORICAL_HORIZON,):
        raise ValueError("IBKR historical profile must use only the 15-minute horizon")
    if experiment.primary_horizon != IBKR_HISTORICAL_HORIZON:
        raise ValueError("IBKR historical profile primary horizon is not 15 minutes")
    if experiment.feature_sets != IBKR_HISTORICAL_FEATURE_SETS:
        raise ValueError("IBKR historical profile feature ladder differs from L0/L1/P0/P1")
    if experiment.feature_windows != (
        timedelta(minutes=1),
        timedelta(minutes=5),
    ):
        raise ValueError("IBKR historical profile feature windows differ from the fixed policy")
    eligible_families = {
        FeatureFamily.LOCAL_RETURNS,
        FeatureFamily.TIME_AVAILABILITY,
        FeatureFamily.LOCAL_VOLATILITY_RANGE,
        FeatureFamily.POOLED_CROSS_ASSET,
    }
    for family in eligible_families:
        if experiment.feature_eligibility[family].state is not FeatureEligibility.ELIGIBLE:
            raise ValueError(f"{family.value} must be eligible for IBKR historical research")
    for family in (FeatureFamily.SPREAD, FeatureFamily.QUOTE_IMBALANCE):
        if experiment.feature_eligibility[family].state is not FeatureEligibility.NOT_ELIGIBLE:
            raise ValueError(f"{family.value} must remain ineligible for IBKR historical research")
    if experiment.alpha_grid != IBKR_HISTORICAL_ALPHA_GRID:
        raise ValueError("IBKR historical profile alpha grid differs from the fixed policy")
    if experiment.ridge_solver != "lsqr" or experiment.ridge_tolerance != 1e-8:
        raise ValueError(
            "IBKR historical profile Ridge numerical policy differs from the fixed policy"
        )
    if experiment.ridge_max_iterations != 10_000:
        raise ValueError(
            "IBKR historical profile Ridge iteration policy differs from the fixed policy"
        )
    if experiment.pooled_weighting_policy != "EQUAL_INSTRUMENT_TOTAL_WEIGHT_MEAN_ONE":
        raise ValueError(
            "IBKR historical profile pooled weighting policy differs from the fixed policy"
        )
    if (
        experiment.minimum_training_rows != IBKR_HISTORICAL_MINIMUM_TRAINING_ROWS
        or experiment.minimum_inner_validation_rows != IBKR_HISTORICAL_MINIMUM_INNER_VALIDATION_ROWS
        or experiment.minimum_outer_validation_rows != IBKR_HISTORICAL_MINIMUM_OUTER_VALIDATION_ROWS
    ):
        raise ValueError("IBKR historical profile minimum row policy differs from the fixed policy")
    if experiment.model_families != tuple(ModelFamily):
        raise ValueError("IBKR historical profile must retain the complete baseline model family")


__all__ = [
    "IBKR_HISTORICAL_ALPHA_GRID",
    "IBKR_HISTORICAL_EVIDENCE",
    "IBKR_HISTORICAL_FEATURE_SETS",
    "IBKR_HISTORICAL_FEATURE_WINDOWS",
    "IBKR_HISTORICAL_GROUPS",
    "IBKR_HISTORICAL_HORIZON",
    "IBKR_HISTORICAL_MINIMUM_INNER_VALIDATION_ROWS",
    "IBKR_HISTORICAL_MINIMUM_OUTER_VALIDATION_ROWS",
    "IBKR_HISTORICAL_MINIMUM_TRAINING_ROWS",
    "IBKR_HISTORICAL_PROFILE",
    "IBKR_HISTORICAL_PROFILE_ARGUMENT",
    "IBKR_HISTORICAL_SOURCE",
    "IBKR_HISTORICAL_UNIVERSE",
    "validate_ibkr_historical_profile",
]
