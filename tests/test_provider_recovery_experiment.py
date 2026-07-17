from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ops.dev.provider_recovery_experiment import (
    _adapter_snapshot,
    _bounded_call,
    _fresh_counts,
    _listings,
    _phase_ready,
    _write,
)
from qtrad.adapters.ig.market_data import IgDemoConfig, IgDemoMarketDataAdapter
from qtrad.domain.operations import HealthStatus
from qtrad.runtime.universe import load_capture_universe


class _Clock:
    def now(self) -> datetime:
        return datetime(2026, 7, 17, tzinfo=UTC)


def _adapter() -> IgDemoMarketDataAdapter:
    universe = load_capture_universe(Path("config/capture-v1.toml"))
    return IgDemoMarketDataAdapter(
        IgDemoConfig(username="demo", password="secret", api_key="secret"),
        _Clock(),
        instruments_by_id=universe.instruments_by_id,
        preferred_epics=universe.preferred_epics,
    )


def test_recovery_listings_bind_exact_reviewed_universe() -> None:
    universe = load_capture_universe(Path("config/capture-v1.toml"))

    listings = _listings(universe, datetime(2026, 7, 17, tzinfo=UTC))

    assert len(listings) == 7
    assert {listing.instrument_id for listing in listings} == {
        instrument.instrument_id for instrument in universe.instruments
    }
    assert {listing.listing_id.external_id for listing in listings} == set(
        universe.preferred_epics.values()
    )


def test_recovery_phase_requires_complete_fresh_lifecycle_evidence() -> None:
    adapter = _adapter()
    epics = {"epic-a", "epic-b"}
    adapter._status = HealthStatus.HEALTHY
    adapter._reconnect_count = 1
    adapter._rest_reauthentications = 0
    adapter._published_trading_requests_per_minute = 9
    adapter._published_non_trading_requests_per_minute = 25
    adapter._effective_trading_requests_per_minute = 7
    adapter._effective_non_trading_requests_per_minute = 23
    adapter._expected_epics = set(epics)
    adapter._subscribed_epics = set(epics)
    adapter._updated_epics = set(epics)
    adapter._heartbeat_subscribed = True
    adapter._heartbeat_events = 1
    adapter._heartbeat_real_max_frequency = "unlimited"
    adapter._real_max_frequency_by_epic = {epic: "unlimited" for epic in epics}

    assert _phase_ready(adapter, reconnects=1, reauthentications=0)

    adapter._updated_epics.remove("epic-b")
    assert not _phase_ready(adapter, reconnects=1, reauthentications=0)


def test_recovery_snapshot_is_bounded_and_contains_no_credentials() -> None:
    adapter = _adapter()
    adapter._expected_epics = {"epic-a"}
    adapter._quote_received_times = {"epic-a": datetime(2026, 7, 17, tzinfo=UTC)}
    adapter._real_max_frequency_by_epic = {"epic-a": "1.0"}

    snapshot = _adapter_snapshot(adapter)
    encoded = json.dumps(snapshot)

    assert snapshot["expected_subscriptions"] == 1
    assert snapshot["abandoned_provider_operation"] is False
    assert "username" not in encoded
    assert "password" not in encoded
    assert "api_key" not in encoded
    assert "account" not in encoded


def test_recovery_fresh_counts_require_every_instrument_to_advance() -> None:
    expected = {"fx:aud-usd", "fx:eur-usd"}
    baseline = {"fx:aud-usd": 2, "fx:eur-usd": 3}

    assert _fresh_counts({"fx:aud-usd": 3, "fx:eur-usd": 4}, baseline, expected)
    assert not _fresh_counts({"fx:aud-usd": 3, "fx:eur-usd": 3}, baseline, expected)
    assert not _fresh_counts({"fx:aud-usd": 3}, baseline, expected)


def test_recovery_provider_call_is_bounded() -> None:
    assert _bounded_call("success", 1, lambda: 42) == 42
    with pytest.raises(ValueError, match="classified"):
        _bounded_call("failure", 1, lambda: (_ for _ in ()).throw(ValueError("classified")))

    def slow() -> None:
        time.sleep(0.05)

    with pytest.raises(TimeoutError, match="provider operation timed out"):
        _bounded_call("timeout", 0.001, slow)


def test_recovery_evidence_is_non_overwriting_and_self_hashed(tmp_path: Path) -> None:
    output = tmp_path / "recovery.json"
    evidence: dict[str, object] = {"result": "PASS"}

    _write(output, evidence)

    stored = json.loads(output.read_text())
    evidence_hash = stored.pop("evidence_sha256")
    assert (
        evidence_hash
        == hashlib.sha256(
            json.dumps(stored, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    assert output.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        _write(output, {"result": "PASS"})
