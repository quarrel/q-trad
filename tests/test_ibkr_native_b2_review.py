"""Regression coverage for the second B2 review pass."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

import pytest

from qtrad.adapters.ibkr.market_hours import ibkr_contract_is_expected_active
from qtrad.adapters.postgres.queries import OperatorQueries
from qtrad.adapters.postgres.store import PostgresAuditStore
from qtrad.ports.ibkr_capability import IbkrContractEvidence

_NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)


def _evidence(liquid_hours: str) -> IbkrContractEvidence:
    return IbkrContractEvidence(
        con_id=123,
        symbol="EUR",
        local_symbol="EUR.USD",
        security_type="CASH",
        exchange="IDEALPRO",
        currency="USD",
        trading_class="",
        multiplier="",
        minimum_tick=Decimal("0.00005"),
        market_rule_ids=("26",),
        valid_exchanges=("IDEALPRO",),
        long_name="EUR.USD",
        underlier_con_id=None,
        timezone="UTC",
        trading_hours=liquid_hours,
        liquid_hours=liquid_hours,
    )


def test_authenticated_ibkr_liquid_hours_distinguish_closed_and_open_sessions() -> None:
    assert not ibkr_contract_is_expected_active(_evidence("20260808:CLOSED"), _NOW)
    assert ibkr_contract_is_expected_active(_evidence("20260808:0000-2400"), _NOW)
    assert ibkr_contract_is_expected_active(
        _evidence("20260807:1700-1600;20260808:1700-1600"), _NOW
    )
    assert ibkr_contract_is_expected_active(_evidence("20260807:CLOSED"), _NOW)


class _ReconciliationStore:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object] | None]] = []
        self.session_metrics = {
            "11111111-1111-1111-1111-111111111111": {"received": 100, "persisted": 90},
            "22222222-2222-2222-2222-222222222222": {"received": 20, "persisted": 19},
        }

    async def query(
        self, statement: str, parameters: dict[str, object] | None = None
    ) -> list[dict[str, Any]]:
        self.calls.append((statement, parameters))
        if "SELECT capture_session_id" in statement and "ops.capture_session_metrics" in statement:
            assert set(self.session_metrics) == {
                "11111111-1111-1111-1111-111111111111",
                "22222222-2222-2222-2222-222222222222",
            }
            return [{"capture_session_id": "22222222-2222-2222-2222-222222222222"}]
        if "WITH selected AS" in statement:
            assert parameters is not None
            assert parameters["capture_session_id"] == "22222222-2222-2222-2222-222222222222"
            return [
                {
                    "raw_persisted": 19,
                    "canonical_persisted": 18,
                    "quarantined": 1,
                    "unclassified": 0,
                }
            ]
        if "SELECT records_received, persisted, failed, dropped" in statement:
            assert parameters is not None
            assert parameters["capture_session_id"] == "22222222-2222-2222-2222-222222222222"
            return [
                {
                    "records_received": 20,
                    "persisted": 19,
                    "failed": 0,
                    "dropped": 1,
                    "observed_at": _NOW,
                }
            ]
        raise AssertionError(f"unexpected query: {statement}")


@pytest.mark.asyncio
async def test_default_reconciliation_resolves_one_latest_session_for_both_sides() -> None:
    store = _ReconciliationStore()
    result = await OperatorQueries(cast(PostgresAuditStore, store)).capture_reconciliation(
        provider="ibkr",
        environment="IBKR_PAPER",
        source_class="IBKR_NATIVE_CAPTURE",
        configuration_hash="a" * 64,
    )

    assert result["capture_session_id"] == "22222222-2222-2222-2222-222222222222"
    assert result["adapter_accepted"] == 20
    assert result["raw_persisted"] == 19
    assert result["records_dropped"] == 1
    assert result["loss"] == 1


class _ReadinessStore:
    async def query(
        self, statement: str, parameters: dict[str, object] | None = None
    ) -> list[dict[str, Any]]:
        assert parameters is not None
        assert json.loads(str(parameters["instrument_ids"])) == ["fx:eur-usd"]
        return [
            {
                "ingestion_running": True,
                "adapter_healthy": True,
                "fresh_quote_count": 1,
                "global_position": 10,
                "checkpoint_position": 10,
                "checkpoint_updated_at": _NOW,
            }
        ]


@pytest.mark.asyncio
async def test_readiness_counts_only_authenticated_expected_active_instruments() -> None:
    result = await OperatorQueries(cast(PostgresAuditStore, _ReadinessStore())).readiness(
        ("fx:eur-usd", "index:australia-200"),
        "a" * 64,
        provider="ibkr",
        environment="IBKR_PAPER",
        adapter_name="ibkr-native-capture",
        source_class="IBKR_NATIVE_CAPTURE",
        expected_active_instrument_ids=("fx:eur-usd",),
    )

    assert result["ready"] is True
    assert result["expected_instruments"] == 2
    assert result["expected_active_instruments"] == 1
    assert result["fresh_quote_count"] == 1
