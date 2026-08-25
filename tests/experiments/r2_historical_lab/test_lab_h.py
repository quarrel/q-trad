from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from experiments.r2_historical_lab.lab_h import horizons
from experiments.r2_historical_lab.lab_h.horizons import (
    HorizonConfig,
    _development_targets,
    _effective_opportunities,
    _load_joined,
    _phase,
    _rank_horizons,
)
from experiments.r2_historical_lab.lab_h.one_minute import _one_minute_targets


def test_phase_filter_reports_each_non_overlapping_offset() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    frame = pl.DataFrame(
        {
            "decision_time": [start + timedelta(minutes=value) for value in range(12)],
            "value": list(range(12)),
        }
    )
    selected = [_phase(frame, 5, offset)["value"].to_list() for offset in range(5)]
    assert selected == [[0, 5, 10], [1, 6, 11], [2, 7], [3, 8], [4, 9]]


def test_effective_opportunities_are_greedily_non_overlapping() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    targets = pl.DataFrame(
        {
            "instrument_id": ["commodity:spot-gold"] * 6,
            "decision_time": [start + timedelta(minutes=value) for value in range(6)],
            "target_end": [start + timedelta(minutes=value + 3) for value in range(6)],
            "target_valid": [True] * 6,
        }
    )
    effective, overlap = _effective_opportunities(targets)
    assert effective == 2
    assert overlap == pytest.approx(2 / 3)


def test_horizon_ranking_balances_skill_and_breadth() -> None:
    screen = pl.DataFrame(
        {
            "horizon_minutes": [5, 15, 30, 60],
            "skill_versus_zero": [0.02, 0.01, 0.00, -0.01],
            "positive_block_count": [1, 2, 3, 0],
            "positive_instrument_count": [1, 2, 3, 0],
        }
    )
    assert _rank_horizons(screen, 2) == (30, 15)


def test_horizon_ranking_excludes_horizons_without_a_positive_block() -> None:
    screen = pl.DataFrame(
        {
            "horizon_minutes": [5, 15, 30, 60],
            "skill_versus_zero": [-0.001, -0.002, 0.01, -0.003],
            "positive_block_count": [1, 1, 0, 1],
            "positive_instrument_count": [0, 0, 4, 0],
        }
    )
    assert _rank_horizons(screen, 2) == (5, 15)


def test_development_targets_exclude_targets_maturing_at_terminal_start() -> None:
    terminal_start = datetime(2026, 1, 2, tzinfo=UTC)
    rows = pl.DataFrame(
        {
            "block": ["DEV_1", "DEV_1"],
            "instrument_id": ["fx:eur-usd", "fx:eur-usd"],
            "decision_time": [
                terminal_start - timedelta(minutes=20),
                terminal_start - timedelta(minutes=19),
            ],
            "target_valid": [True, True],
            "target_return": [0.1, 0.2],
            "target_available_at": [terminal_start - timedelta(minutes=1), terminal_start],
            "feature_available_at": [
                terminal_start - timedelta(minutes=20),
                terminal_start - timedelta(minutes=19),
            ],
        }
    )
    keys = rows.select("instrument_id", "decision_time")
    empty_keys = keys.head(0)
    selected = _development_targets(
        rows,
        {
            "DEV_1": (empty_keys, keys),
            "DEV_2": (empty_keys, empty_keys),
            "DEV_3": (empty_keys, empty_keys),
        },
        terminal_start,
    )
    assert selected["target_return"].to_list() == [0.1]


def test_terminal_authority_is_forwarded_to_feature_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def reject_after_capture(**kwargs: object) -> pl.LazyFrame:
        calls.append(kwargs)
        raise RuntimeError("captured")

    monkeypatch.setattr(horizons, "load_parts", reject_after_capture)
    config = HorizonConfig(
        base_sha="base",
        manifest_path=Path("manifest.json"),
        manifest_sha256="manifest-sha",
        output_root=Path("output"),
        horizons_minutes=(15,),
        finalist_count=1,
    )
    with pytest.raises(RuntimeError, match="captured"):
        _load_joined(
            config,
            blocks=("TERMINAL_FORMER_HOLDOUT",),
            horizons=(15,),
            finalist_freeze=Path("finalists.json"),
            expected_finalist_freeze_sha256="freeze-sha",
            configuration_id="configuration-id",
        )
    assert calls == [
        {
            "manifest_path": Path("manifest.json"),
            "expected_manifest_sha256": "manifest-sha",
            "instruments": horizons.ORIGINAL_TARGETS,
            "blocks": ("TERMINAL_FORMER_HOLDOUT",),
            "finalist_freeze": Path("finalists.json"),
            "expected_finalist_freeze_sha256": "freeze-sha",
            "configuration_id": "configuration-id",
            "kind": "feature",
        }
    ]


def test_one_minute_target_uses_exact_endpoint_and_five_minute_delay() -> None:
    decision_time = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    scaffold = pl.DataFrame(
        {
            "instrument_id": ["fx:eur-usd"],
            "decision_time": [decision_time],
            "block": ["DEV_1"],
        }
    )
    source = pl.DataFrame(
        {
            "interval_end": [decision_time, decision_time + timedelta(minutes=1)],
            "close": [100.0, 101.0],
            "available_at": [
                decision_time + timedelta(minutes=5),
                decision_time + timedelta(minutes=6),
            ],
        }
    )

    target = _one_minute_targets(scaffold, source, "fx:eur-usd").row(0, named=True)

    assert target["target_end"] == decision_time + timedelta(minutes=1)
    assert target["target_available_at"] == decision_time + timedelta(minutes=6)
    assert target["target_return"] == pytest.approx(0.009950330853168092)
    assert target["target_valid"] is True
    assert target["horizon_minutes"] == 1
