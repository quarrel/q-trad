# pyright: strict
import json
from datetime import date, datetime
from pathlib import Path

import pytest

from experiments.universe_discovery import __main__ as batch
from experiments.universe_discovery.atlas import Probe, describe, select
from experiments.universe_discovery.fixtures import fixture_panel
from experiments.universe_discovery.panel import Market, index_panel, instant
from experiments.universe_discovery.payoff import EntryState, Outcome, positions


def test_frozen_matching_nuisance_bijection_and_anchor_views() -> None:
    markets, bars = fixture_panel(12)
    indexed = index_panel(bars)
    descriptors = tuple(
        describe(m, indexed[m.family], instant(date(2018, 12, 31), 23)) for m in markets
    )
    by_id = {d.family: d for d in descriptors}
    cohort = select(descriptors, markets, "continuation")
    assert len(cohort.selected) == 6
    assert cohort.matched is not None
    assert len(cohort.broad) == len(cohort.matched) == 100
    assert cohort.anchor_size_matched is not None
    assert len(cohort.anchor_size_matched) == 100
    for draw in cohort.matched:
        assert len(draw) == len(set(draw)) == len(cohort.selected)
        for selected, control in zip(cohort.selected, draw, strict=True):
            assert by_id[selected].nuisance == by_id[control].nuisance
    assert any(cohort.overlaps)  # Controls are allowed to include selected families.
    assert all(len(draw) == 6 for draw in cohort.anchor_size_matched)


def test_batch_spec_before_scoring_reuse_and_append_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def observed(
        market: Market, prepared: dict[date, EntryState], cutoff: datetime, end: date, probe: Probe
    ) -> Outcome:
        nonlocal calls
        assert (tmp_path / "first" / "experiment_spec.json").exists()
        assert (tmp_path / "first" / "selection_register.json").exists()
        assert not (tmp_path / "first" / "metrics.json").exists()
        calls += 1
        return positions(market, prepared, cutoff, end, probe)

    monkeypatch.setattr(batch, "positions", observed)
    performance = batch.run_fixture(tmp_path, "first", 6)
    assert calls == 6 * 8 * 2 == performance["outcome_calculations"]
    assert performance["family_preparations"] == 6
    assert performance["panel_decodes"] == 1
    assert performance["model_fits"] == 0
    register = tmp_path / "run_register.jsonl"
    completed = register.read_bytes()
    assert len(completed.splitlines()) == 2
    with pytest.raises(FileExistsError):
        batch.run_fixture(tmp_path, "first", 6)
    assert register.read_bytes() == completed
    with pytest.raises(ValueError, match="6-150"):
        batch.run_fixture(tmp_path, "failed", 5)
    assert register.read_bytes().startswith(completed)
    records: list[dict[str, object]] = [
        json.loads(line) for line in register.read_text().splitlines()
    ]
    assert [r["status"] for r in records] == ["STARTED", "COMPLETE", "STARTED", "FAILED"]


def test_invalid_fixture_economics_and_missing_coverage() -> None:
    from dataclasses import replace
    from decimal import Decimal

    markets, bars = fixture_panel(6)
    with pytest.raises(ValueError, match="friction"):
        replace(markets[0], cost=Decimal("-1"))
    with pytest.raises(ValueError, match="positive"):
        replace(markets[0], multiplier=Decimal(0))
    with pytest.raises(ValueError, match="liquidity"):
        replace(bars[0], volume=Decimal("-1"))
    unavailable = Outcome("SYNTHETIC", (), 10, (date(2020, 1, 2),), "MISSING_REQUIRED_OUTCOME")
    summary = unavailable.summary()
    assert summary["coverage"] == 0.9
    assert summary["observed_sessions"] == 9
    assert summary["evaluated_sessions"] == 0
    assert summary["turnover"] is None
    assert summary["adverse_gap_money"] is None
