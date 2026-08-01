from datetime import UTC, datetime
from decimal import Decimal

import pytest

from qtrad.adapters.ibkr.checkpoint import (
    IbkrCapabilityCheckpointIdentity,
    JsonIbkrCapabilityCheckpoint,
)
from qtrad.domain.identifiers import InstrumentId
from qtrad.ports.ibkr_capability import (
    IbkrCandidateCapability,
    IbkrContractEvidence,
    IbkrContractQuery,
    IbkrRequestEvidence,
)


def _identity() -> IbkrCapabilityCheckpointIdentity:
    return IbkrCapabilityCheckpointIdentity(
        catalogue_hash="a" * 64,
        probe_spec_hash="b" * 64,
        api_version="10.49",
        gateway_version="10.49",
        configuration_hash="c" * 64,
    )


def _result() -> IbkrCandidateCapability:
    query = IbkrContractQuery(
        instrument_id=InstrumentId("index:test"),
        symbol="TEST",
        security_type="IND",
        exchange="SMART",
        currency="USD",
    )
    contract = IbkrContractEvidence(
        con_id=42,
        symbol="TEST",
        local_symbol="TEST",
        security_type="IND",
        exchange="SMART",
        currency="USD",
        trading_class=None,
        multiplier=None,
        minimum_tick=Decimal("0.01"),
        market_rule_ids=(),
        valid_exchanges=("SMART",),
        long_name=None,
        underlier_con_id=None,
        timezone=None,
        trading_hours=None,
        liquid_hours=None,
    )
    return IbkrCandidateCapability(
        query=query,
        contracts=(contract,),
        requests=(
            IbkrRequestEvidence(
                kind="CONTRACT_DETAILS",
                status="SUCCESS",
                latency_milliseconds=2,
                contract_con_id=42,
                earliest_timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        ),
    )


@pytest.mark.asyncio
async def test_checkpoint_round_trips_and_resumes_by_query(tmp_path) -> None:
    checkpoint = JsonIbkrCapabilityCheckpoint(tmp_path / "probe.json", _identity())
    result = _result()

    await checkpoint.save(result)

    assert await checkpoint.load((result.query,)) == (result,)
    assert (
        await checkpoint.load(
            (
                IbkrContractQuery(
                    instrument_id=InstrumentId("index:other"),
                    symbol="OTHER",
                    security_type="IND",
                    exchange="SMART",
                    currency="USD",
                ),
            )
        )
        == ()
    )


@pytest.mark.asyncio
async def test_checkpoint_identity_mismatch_starts_a_new_run(tmp_path) -> None:
    path = tmp_path / "probe.json"
    result = _result()
    await JsonIbkrCapabilityCheckpoint(path, _identity()).save(result)

    changed = IbkrCapabilityCheckpointIdentity(
        catalogue_hash="d" * 64,
        probe_spec_hash="b" * 64,
        api_version="10.49",
        gateway_version="10.49",
        configuration_hash="c" * 64,
    )
    assert await JsonIbkrCapabilityCheckpoint(path, changed).load((result.query,)) == ()
