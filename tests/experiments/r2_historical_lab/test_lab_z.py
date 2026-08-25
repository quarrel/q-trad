from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from experiments.r2_historical_lab.lab_z.synthesis import run

MANIFEST = "462e40fa84038156b16c68bde4b68d574ab7862c680657ebd1a0035b39bf0072"
SOURCE_ID = "source-finalist"


def _record(*, workstream: str, terminal: bool, skill: float = 0.001) -> dict[str, Any]:
    block = "TERMINAL_FORMER_HOLDOUT" if terminal else "DEV_1"
    return {
        "attempt_status": "SUCCEEDED",
        "configuration": {
            "evidence_label": "EXPLORATORY_POST_HOC_ONLY",
            "source_class": "IBKR_HISTORICAL_RESEARCH",
            "universe": "ALL_20",
            "horizon_minutes": 15,
        },
        "configuration_id": SOURCE_ID,
        "evaluated_blocks": [block],
        "evidence_label": "EXPLORATORY_POST_HOC_ONLY",
        "manifest_sha256": MANIFEST,
        "result": {
            "best_instrument_contribution": 0.5,
            "best_period_contribution": 0.5,
            "calibration_slope": 0.5,
            "chronological_block_count": 3 if not terminal else 1,
            "direct_delta_mse_versus_zero": -1e-9 if skill > 0 else 1e-9,
            "forecast_coverage": 1.0,
            "instrument_count": 20,
            "model_name": "FULLY_POOLED_LOCAL_RIDGE",
            "positive_chronological_block_count": 2 if not terminal else 1,
            "positive_instrument_count": 3,
            "skill_versus_zero": skill,
            "source_class": "IBKR_HISTORICAL_RESEARCH",
            "spearman_correlation": 0.01,
            "support": 100,
        },
        "terminal_block_accessed": terminal,
        "workstream": workstream,
    }


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _design(tmp_path: Path) -> Path:
    workstreams: list[dict[str, Any]] = []
    for name in ("LAB-H", "LAB-U", "LAB-S", "LAB-T", "LAB-L"):
        register = tmp_path / f"{name}.jsonl"
        records = [_record(workstream=name, terminal=False)]
        finalist_path: Path | None = None
        if name == "LAB-S":
            records.append(_record(workstream=name, terminal=True, skill=-0.001))
        register.write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )
        if name != "LAB-L":
            finalist_path = tmp_path / f"{name}-finalists.json"
            _write_json(
                finalist_path,
                {
                    "workstream": name,
                    "manifest_sha256": MANIFEST,
                    "evidence_label": "EXPLORATORY_POST_HOC_ONLY",
                    "finalist_configuration_ids": [SOURCE_ID] if name == "LAB-S" else [],
                },
            )
        workstreams.append(
            {
                "workstream": name,
                "register_paths": [str(register)],
                "finalist_path": str(finalist_path) if finalist_path else None,
                "supporting_configuration_paths": [],
                "complexity_and_dependency_cost": "test",
                "material_failure_or_fragility": "test",
            }
        )
    config = tmp_path / "config.json"
    _write_json(
        config,
        {
            "programme_base_sha": "f31cf4731fc233726f45f67f54064c40965d01d7",
            "evidence_label": "EXPLORATORY_POST_HOC_ONLY",
            "source_class": "IBKR_HISTORICAL_RESEARCH",
            "manifest_sha256": MANIFEST,
            "output_root": str(tmp_path / "output"),
            "workstreams": workstreams,
            "bounded_combination": {
                "source_workstream": "LAB-S",
                "source_configuration_ids": [SOURCE_ID],
                "frozen_source_configuration_ids": [SOURCE_ID],
                "cadence_seconds": 60,
                "cadence_non_overlap_survived": False,
            },
            "future_experiment_slate": {
                "evidence_label": "EXPLORATORY_POST_HOC_ONLY",
                "source_class": "IBKR_HISTORICAL_RESEARCH",
            },
        },
    )
    return config


def test_run_synthesises_compact_registers_without_new_terminal_access(tmp_path: Path) -> None:
    summary = run(_design(tmp_path))

    assert summary["status"] == "COMPLETE"
    assert summary["combined_configuration_count"] == 1
    assert summary["frozen_finalist_count"] == 1
    assert summary["promotion_standard_pass_count"] == 0
    assert summary["terminal_results_reused_without_new_terminal_access"] is True
    output = json.loads((tmp_path / "output/run-summary.json").read_bytes())
    assert output == summary


def test_run_rejects_wrong_source_boundary(tmp_path: Path) -> None:
    config = _design(tmp_path)
    design = json.loads(config.read_bytes())
    register_path = Path(design["workstreams"][0]["register_paths"][0])
    record = json.loads(register_path.read_text(encoding="utf-8"))
    record["configuration"]["source_class"] = "WRONG"
    register_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="wrong source class"):
        run(config)
