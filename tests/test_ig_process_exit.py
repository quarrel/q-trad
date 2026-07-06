import subprocess
import sys
from pathlib import Path


def test_timed_out_provider_operation_does_not_keep_process_resident() -> None:
    source = """
import asyncio
import threading

from qtrad.adapters.clock import SystemClock
from qtrad.adapters.ig.market_data import (
    IgDemoConfig,
    IgDemoMarketDataAdapter,
    _ProviderOperationTimeout,
)


async def main() -> None:
    never = threading.Event()
    adapter = IgDemoMarketDataAdapter(
        IgDemoConfig(
            username="demo",
            password="not-a-real-password",
            api_key="not-a-real-key",
            provider_operation_timeout_seconds=0.01,
        ),
        SystemClock(),
    )
    try:
        await adapter._run_provider_operation("blocked_test", never.wait)
    except _ProviderOperationTimeout:
        return
    raise AssertionError("blocked provider operation did not time out")


asyncio.run(main())
"""
    completed = subprocess.run(
        [sys.executable, "-c", source],
        cwd=Path(__file__).parents[1],
        check=False,
        capture_output=True,
        text=True,
        timeout=3,
    )

    assert completed.returncode == 0, completed.stderr
