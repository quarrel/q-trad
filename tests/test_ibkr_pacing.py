import asyncio
import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from qtrad.adapters.ibkr.pacing import IbkrPostgresPacing
from qtrad.adapters.postgres.store import PostgresAuditStore
from qtrad.domain.ibkr_historical import IbkrHistoricalPacingPolicy

_PROFILE_SHA256 = "a" * 64
_POLICY = IbkrHistoricalPacingPolicy(15, 2, 5, 600, 55)


class Ledger:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.allowed = False

    async def reserve_ibkr_request(self, **kwargs: object) -> bool:
        self.calls.append(kwargs)
        return self.allowed


def _pacing(ledger: Ledger, *, clock: Callable[[], datetime]) -> IbkrPostgresPacing:
    return IbkrPostgresPacing(
        ledger,
        request_profile_sha256=_PROFILE_SHA256,
        pacing_policy=_POLICY,
        clock=clock,
        retry_seconds=2.0,
    )


@pytest.mark.asyncio
async def test_postgres_pacing_reserves_before_request_and_retries() -> None:
    ledger = Ledger()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    pacing = _pacing(ledger, clock=lambda: now)

    assert (
        await pacing.reserve(
            "historical",
            "42",
            "window",
            2,
            request_profile_sha256=_PROFILE_SHA256,
            pacing_policy=_POLICY,
        )
        == 2.0
    )
    ledger.allowed = True
    assert (
        await pacing.reserve(
            "historical",
            "42",
            "window",
            2,
            request_profile_sha256=_PROFILE_SHA256,
            pacing_policy=_POLICY,
        )
        == 0.0
    )
    assert ledger.calls[0]["requested_at"] == now
    assert ledger.calls[0]["request_profile_sha256"] == _PROFILE_SHA256
    assert ledger.calls[0]["pacing_policy"] == _POLICY


@pytest.mark.asyncio
async def test_postgres_pacing_rejects_mismatched_binding_and_naive_clock() -> None:
    ledger = Ledger()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    pacing = _pacing(ledger, clock=lambda: now)

    with pytest.raises(ValueError, match="profile hash"):
        await pacing.reserve(
            "historical",
            "42",
            "window",
            1,
            request_profile_sha256="b" * 64,
            pacing_policy=_POLICY,
        )

    naive_pacing = _pacing(
        ledger,
        clock=lambda: datetime.now(UTC).replace(tzinfo=None),
    )
    with pytest.raises(ValueError, match="aware"):
        await naive_pacing.reserve(
            "historical",
            "42",
            "window",
            1,
            request_profile_sha256=_PROFILE_SHA256,
            pacing_policy=_POLICY,
        )


async def _reserve(
    store: PostgresAuditStore,
    *,
    requested_at: datetime,
    request_profile_sha256: str,
    pacing_policy: IbkrHistoricalPacingPolicy,
    contract_key: str,
    request_fingerprint: str,
) -> bool:
    return await store.reserve_ibkr_request(
        requested_at=requested_at,
        request_kind="historical",
        contract_key=contract_key,
        request_fingerprint=request_fingerprint,
        weight=1,
        request_profile_sha256=request_profile_sha256,
        pacing_policy=pacing_policy,
    )


@pytest.mark.postgres
@pytest.mark.skipif(
    not os.getenv("QTRAD_TEST_DATABASE_URL"),
    reason="QTRAD_TEST_DATABASE_URL is required for PostgreSQL integration",
)
@pytest.mark.asyncio
async def test_postgres_pacing_enforces_exact_bound_policy_atomically() -> None:
    database_url = os.getenv("QTRAD_TEST_DATABASE_URL")
    assert database_url is not None
    engine = create_async_engine(database_url)
    try:
        store = PostgresAuditStore(engine)
        now = datetime.now(UTC)

        cooldown_contract = "cooldown-" + uuid4().hex
        cooldown_fingerprint = "fingerprint-" + uuid4().hex
        cooldown_profile = uuid4().hex + uuid4().hex
        assert await _reserve(
            store,
            requested_at=now,
            request_profile_sha256=cooldown_profile,
            pacing_policy=_POLICY,
            contract_key=cooldown_contract,
            request_fingerprint=cooldown_fingerprint,
        )
        assert not await _reserve(
            store,
            requested_at=now + timedelta(seconds=14),
            request_profile_sha256=cooldown_profile,
            pacing_policy=_POLICY,
            contract_key=cooldown_contract,
            request_fingerprint=cooldown_fingerprint,
        )
        assert await _reserve(
            store,
            requested_at=now + timedelta(seconds=15),
            request_profile_sha256=cooldown_profile,
            pacing_policy=_POLICY,
            contract_key=cooldown_contract,
            request_fingerprint=cooldown_fingerprint,
        )

        restart_contract = "restart-" + uuid4().hex
        restart_fingerprint = "fingerprint-" + uuid4().hex
        restart_profile = uuid4().hex + uuid4().hex
        pacing = IbkrPostgresPacing(
            store,
            request_profile_sha256=restart_profile,
            pacing_policy=_POLICY,
            clock=lambda: now,
            retry_seconds=2.0,
        )
        assert (
            await pacing.reserve(
                "historical",
                restart_contract,
                restart_fingerprint,
                1,
                request_profile_sha256=restart_profile,
                pacing_policy=_POLICY,
            )
            == 0.0
        )
        restarted_pacing = IbkrPostgresPacing(
            store,
            request_profile_sha256=restart_profile,
            pacing_policy=_POLICY,
            clock=lambda: now,
            retry_seconds=2.0,
        )
        assert (
            await restarted_pacing.reserve(
                "historical",
                restart_contract,
                restart_fingerprint,
                1,
                request_profile_sha256=restart_profile,
                pacing_policy=_POLICY,
            )
            == 2.0
        )

        contract_policy = IbkrHistoricalPacingPolicy(15, 2, 1, 600, 55)
        contract_profile = uuid4().hex + uuid4().hex
        contract_key = "contract-" + uuid4().hex
        contract_now = now + timedelta(seconds=700)
        assert await _reserve(
            store,
            requested_at=contract_now,
            request_profile_sha256=contract_profile,
            pacing_policy=contract_policy,
            contract_key=contract_key,
            request_fingerprint="fingerprint-" + uuid4().hex,
        )
        assert not await _reserve(
            store,
            requested_at=contract_now + timedelta(seconds=1),
            request_profile_sha256=contract_profile,
            pacing_policy=contract_policy,
            contract_key=contract_key,
            request_fingerprint="fingerprint-" + uuid4().hex,
        )

        rolling_policy = IbkrHistoricalPacingPolicy(15, 2, 5, 600, 1)
        rolling_profile = uuid4().hex + uuid4().hex
        rolling_now = now + timedelta(seconds=1400)
        assert await _reserve(
            store,
            requested_at=rolling_now,
            request_profile_sha256=rolling_profile,
            pacing_policy=rolling_policy,
            contract_key="rolling-a-" + uuid4().hex,
            request_fingerprint="fingerprint-" + uuid4().hex,
        )
        assert not await _reserve(
            store,
            requested_at=rolling_now + timedelta(seconds=1),
            request_profile_sha256=rolling_profile,
            pacing_policy=rolling_policy,
            contract_key="rolling-b-" + uuid4().hex,
            request_fingerprint="fingerprint-" + uuid4().hex,
        )

        long_policy = IbkrHistoricalPacingPolicy(3600, 2, 5, 600, 55)
        long_profile = uuid4().hex + uuid4().hex
        short_profile = uuid4().hex + uuid4().hex
        long_contract = "long-cooldown-" + uuid4().hex
        long_fingerprint = "fingerprint-" + uuid4().hex
        long_now = now + timedelta(seconds=2800)
        assert await _reserve(
            store,
            requested_at=long_now,
            request_profile_sha256=long_profile,
            pacing_policy=long_policy,
            contract_key=long_contract,
            request_fingerprint=long_fingerprint,
        )
        assert not await _reserve(
            store,
            requested_at=long_now + timedelta(seconds=601),
            request_profile_sha256=long_profile,
            pacing_policy=long_policy,
            contract_key=long_contract,
            request_fingerprint=long_fingerprint,
        )
        assert await _reserve(
            store,
            requested_at=long_now + timedelta(seconds=601),
            request_profile_sha256=short_profile,
            pacing_policy=_POLICY,
            contract_key="short-profile-" + uuid4().hex,
            request_fingerprint="fingerprint-" + uuid4().hex,
        )
        assert not await _reserve(
            store,
            requested_at=long_now + timedelta(seconds=602),
            request_profile_sha256=long_profile,
            pacing_policy=long_policy,
            contract_key=long_contract,
            request_fingerprint=long_fingerprint,
        )

        concurrent_policy = IbkrHistoricalPacingPolicy(15, 2, 1, 600, 55)
        concurrent_profile = uuid4().hex + uuid4().hex
        concurrent_contract = "concurrent-" + uuid4().hex
        concurrent_now = now + timedelta(seconds=2100)
        results = await asyncio.gather(
            _reserve(
                store,
                requested_at=concurrent_now,
                request_profile_sha256=concurrent_profile,
                pacing_policy=concurrent_policy,
                contract_key=concurrent_contract,
                request_fingerprint="fingerprint-" + uuid4().hex,
            ),
            _reserve(
                store,
                requested_at=concurrent_now,
                request_profile_sha256=concurrent_profile,
                pacing_policy=concurrent_policy,
                contract_key=concurrent_contract,
                request_fingerprint="fingerprint-" + uuid4().hex,
            ),
        )
        assert sorted(results) == [False, True]
    finally:
        await engine.dispose()
