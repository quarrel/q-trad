from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from qtrad.runtime.strategy_experiment import (
    load_strategy_experiment,
    verify_provider_economics,
)


def test_research_proof_configuration_is_exact_and_paper_eligible() -> None:
    experiment = load_strategy_experiment(Path("config/research-proof-v1.toml"))

    assert str(experiment.instrument_id) == "index:australia-200"
    assert len(experiment.strategies) == 4
    assert sum(strategy.kind != "NO_SIGNAL" for strategy in experiment.strategies) == 3
    assert experiment.economics.value_per_price_unit == Decimal("25.00")
    assert experiment.economics.session_profile.allows(datetime(2026, 7, 16, 0, 10, tzinfo=UTC))
    assert not experiment.economics.session_profile.allows(datetime(2026, 7, 16, 6, 0, tzinfo=UTC))

    verify_provider_economics(
        experiment,
        {
            "metadata_version": "0454fc612d2ed4b3",
            "currency": "AUD",
            "minimum_deal_size": Decimal("1.0"),
            "economics": {
                "minimum_quantity": "1.0",
                "one_pip_means": "1 Index Point",
                "value_of_one_pip": "25.00",
            },
        },
    )


def test_provider_economics_mismatch_is_rejected() -> None:
    experiment = load_strategy_experiment(Path("config/research-proof-v1.toml"))

    with pytest.raises(ValueError, match="do not match"):
        verify_provider_economics(
            experiment,
            {
                "metadata_version": "0454fc612d2ed4b3",
                "currency": "AUD",
                "minimum_deal_size": Decimal("1.0"),
                "economics": {
                    "minimum_quantity": "1.0",
                    "one_pip_means": "1 Index Point",
                    "value_of_one_pip": "24.00",
                },
            },
        )
