from datetime import UTC, datetime

import pytest

from qtrad.adapters.ibkr.pacing import IbkrPostgresPacing


class Ledger:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.allowed = False

    async def reserve_ibkr_request(self, **kwargs: object) -> bool:
        self.calls.append(kwargs)
        return self.allowed


@pytest.mark.asyncio
async def test_postgres_pacing_reserves_before_request_and_retries() -> None:
    ledger = Ledger()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    pacing = IbkrPostgresPacing(ledger, clock=lambda: now, retry_seconds=2.0)

    assert await pacing.reserve("historical", "42", "window", 2) == 2.0
    ledger.allowed = True
    assert await pacing.reserve("historical", "42", "window", 2) == 0.0
    assert ledger.calls[0]["requested_at"] == now


@pytest.mark.asyncio
async def test_postgres_pacing_rejects_naive_clock() -> None:
    ledger = Ledger()
    pacing = IbkrPostgresPacing(
        ledger,
        clock=lambda: datetime.now(UTC).replace(tzinfo=None),
    )

    with pytest.raises(ValueError, match="aware"):
        await pacing.reserve("historical", "42", "window", 1)
