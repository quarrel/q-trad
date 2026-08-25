from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, cast

EVIDENCE_LABEL = "EXPLORATORY_POST_HOC_ONLY"
SOURCE_CLASS = "IBKR_HISTORICAL_RESEARCH"
DEVELOPMENT_BLOCKS = ["DEV_1", "DEV_2", "DEV_3"]
TERMINAL_BLOCK = "TERMINAL_FORMER_HOLDOUT"


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return cast(dict[str, Any], value)


def _read_register(
    paths: list[Path],
    *,
    workstream: str,
    manifest_sha256: str,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(path)
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise TypeError(f"{path}:{line_number} is not a JSON object")
            record = cast(dict[str, Any], value)
            if record["workstream"] != workstream:
                raise ValueError(f"{path}:{line_number} has the wrong workstream")
            if record["manifest_sha256"] != manifest_sha256:
                raise ValueError(f"{path}:{line_number} has the wrong LAB-0 manifest")
            if record["evidence_label"] != EVIDENCE_LABEL:
                raise ValueError(f"{path}:{line_number} has the wrong evidence label")
            configuration = cast(dict[str, Any], record["configuration"])
            result = cast(dict[str, Any], record["result"])
            declared_sources = [
                cast(str, source["source_class"])
                for source in (record, configuration, result)
                if "source_class" in source
            ]
            if not declared_sources:
                raise ValueError(f"{path}:{line_number} does not declare a source class")
            if any(source != SOURCE_CLASS for source in declared_sources):
                raise ValueError(f"{path}:{line_number} has the wrong source class")
            records.append(record)
    if not records:
        raise ValueError(f"{workstream} has no compact register records")
    return records


def _read_finalists(path: Path | None, *, workstream: str, manifest_sha256: str) -> list[str]:
    if path is None:
        return []
    value = _read_json(path)
    if value["workstream"] != workstream:
        raise ValueError(f"{path} has the wrong workstream")
    if value["manifest_sha256"] != manifest_sha256:
        raise ValueError(f"{path} has the wrong LAB-0 manifest")
    if value["evidence_label"] != EVIDENCE_LABEL:
        raise ValueError(f"{path} has the wrong evidence label")
    finalists = value["finalist_configuration_ids"]
    if not isinstance(finalists, list) or not all(isinstance(item, str) for item in finalists):
        raise TypeError(f"{path} has invalid finalist configuration IDs")
    return cast(list[str], finalists)


def _is_terminal(record: dict[str, Any]) -> bool:
    if "terminal_block_accessed" in record:
        return bool(record["terminal_block_accessed"])
    if record["workstream"] != "LAB-T":
        raise ValueError("register record does not declare its evaluation split")
    split = record["split"]
    if split not in {"PRE_HOLDOUT", "FORMER_HOLDOUT"}:
        raise ValueError(f"LAB-T record has invalid split: {split}")
    return split == "FORMER_HOLDOUT"


def _metric_view(record: dict[str, Any]) -> dict[str, Any]:
    result = cast(dict[str, Any], record["result"])
    if "positive_chronological_block_count" in result:
        positive_blocks = result["positive_chronological_block_count"]
        block_count = result["chronological_block_count"]
    elif record["workstream"] == "LAB-T":
        positive_blocks = result["positive_block_count"]
        block_count = result["block_count"]
    else:
        raise ValueError("result does not declare chronological-block metrics")
    return {
        "configuration_id": record["configuration_id"],
        "model_name": result["model_name"],
        "direct_delta_mse_versus_zero": result["direct_delta_mse_versus_zero"],
        "skill_versus_zero": result["skill_versus_zero"],
        "positive_chronological_block_count": positive_blocks,
        "chronological_block_count": block_count,
        "positive_instrument_count": result["positive_instrument_count"],
        "instrument_count": result["instrument_count"],
        "best_instrument_contribution": result["best_instrument_contribution"],
        "best_period_contribution": result["best_period_contribution"],
        "calibration_slope": result["calibration_slope"],
        "spearman_correlation": result["spearman_correlation"],
        "forecast_coverage": result["forecast_coverage"],
        "support": result["support"],
    }


def _is_success(record: dict[str, Any]) -> bool:
    if record["workstream"] == "LAB-T":
        return True
    return record["attempt_status"] == "SUCCEEDED"


def _best(records: list[dict[str, Any]], *, terminal: bool) -> dict[str, Any] | None:
    candidates = [
        record
        for record in records
        if (
            _is_success(record)
            and _is_terminal(record) is terminal
            and cast(dict[str, Any], record["result"])["model_name"] != "ZERO_RETURN"
        )
    ]
    if not candidates:
        return None
    return _metric_view(
        max(
            candidates,
            key=lambda record: float(cast(dict[str, Any], record["result"])["skill_versus_zero"]),
        )
    )


def _validate_supporting_file(path: Path, *, manifest_sha256: str) -> None:
    value = _read_json(path)
    found = value.get("manifest_sha256", value.get("lab_manifest_sha256"))
    if found != manifest_sha256:
        raise ValueError(f"{path} does not bind the expected LAB-0 manifest")
    if value.get("evidence_label") != EVIDENCE_LABEL:
        raise ValueError(f"{path} has the wrong evidence label")
    if value["source_class"] != SOURCE_CLASS:
        raise ValueError(f"{path} has the wrong source class")


def _promotion_failures(result: dict[str, Any], *, cadence_survived: bool) -> list[str]:
    failures: list[str] = []
    if float(result["skill_versus_zero"]) <= 0:
        failures.append("non_positive_direct_skill")
    if int(result["positive_chronological_block_count"]) < 2:
        failures.append("fewer_than_two_positive_chronological_blocks")
    if int(result["positive_instrument_count"]) < 2:
        failures.append("fewer_than_two_positive_instruments")
    if float(result["best_instrument_contribution"]) > 0.8:
        failures.append("instrument_concentration_above_0_8")
    if float(result["best_period_contribution"]) > 0.8:
        failures.append("period_concentration_above_0_8")
    slope = float(result["calibration_slope"])
    if not 0.0 < slope <= 2.0:
        failures.append("calibration_slope_not_sensible")
    if not cadence_survived:
        failures.append("cadence_non_overlap_test_not_survived")
    return failures


def run(config_path: Path) -> dict[str, Any]:
    design = _read_json(config_path)
    if design["programme_base_sha"] != "f31cf4731fc233726f45f67f54064c40965d01d7":
        raise ValueError("LAB-Z must retain the authorised programme base")
    if design["evidence_label"] != EVIDENCE_LABEL or design["source_class"] != SOURCE_CLASS:
        raise ValueError("LAB-Z authority labels changed")

    manifest_sha256 = str(design["manifest_sha256"])
    workstream_records: dict[str, list[dict[str, Any]]] = {}
    dimensions: list[dict[str, Any]] = []
    for item_value in cast(list[dict[str, Any]], design["workstreams"]):
        item = item_value
        workstream = str(item["workstream"])
        records = _read_register(
            [Path(path) for path in cast(list[str], item["register_paths"])],
            workstream=workstream,
            manifest_sha256=manifest_sha256,
        )
        finalists = _read_finalists(
            Path(str(item["finalist_path"])) if item["finalist_path"] is not None else None,
            workstream=workstream,
            manifest_sha256=manifest_sha256,
        )
        for path in cast(list[str], item["supporting_configuration_paths"]):
            _validate_supporting_file(Path(path), manifest_sha256=manifest_sha256)

        terminal_ids = {
            str(record["configuration_id"]) for record in records if _is_terminal(record)
        }
        if not terminal_ids.issubset(set(finalists)):
            raise ValueError(
                f"{workstream} terminal records are not covered by its finalist freeze"
            )
        unique_ids = {str(record["configuration_id"]) for record in records}
        workstream_records[workstream] = records
        dimensions.append(
            {
                "workstream": workstream,
                "registered_attempts": len(records),
                "unique_configurations": len(unique_ids),
                "development_attempts": sum(not _is_terminal(record) for record in records),
                "terminal_attempts": sum(_is_terminal(record) for record in records),
                "frozen_finalist_count": len(finalists),
                "best_pre_holdout": _best(records, terminal=False),
                "best_former_holdout_descriptive": _best(records, terminal=True),
                "complexity_and_dependency_cost": item["complexity_and_dependency_cost"],
                "material_failure_or_fragility": item["material_failure_or_fragility"],
            }
        )

    combination_design = cast(dict[str, Any], design["bounded_combination"])
    source_workstream = str(combination_design["source_workstream"])
    source_records = workstream_records[source_workstream]
    source_ids = cast(list[str], combination_design["source_configuration_ids"])
    if len(source_ids) > 12:
        raise ValueError("LAB-Z bounded combination exceeds twelve configurations")
    development_by_id = {
        str(record["configuration_id"]): record
        for record in source_records
        if not _is_terminal(record) and record["attempt_status"] == "SUCCEEDED"
    }
    terminal_by_id = {
        str(record["configuration_id"]): record
        for record in source_records
        if _is_terminal(record) and record["attempt_status"] == "SUCCEEDED"
    }

    combination_rows: list[dict[str, Any]] = []
    cadence_survived = bool(combination_design["cadence_non_overlap_survived"])
    for source_id in source_ids:
        record = development_by_id[source_id]
        configuration = cast(dict[str, Any], record["configuration"])
        combined_configuration = {
            "cadence_seconds": combination_design["cadence_seconds"],
            "configuration": configuration,
            "horizon_source": "LAB-H",
            "universe_and_pooling_source": "LAB-U",
            "statistical_source": "LAB-S",
            "source_configuration_id": source_id,
        }
        lab_z_id = hashlib.sha256(_canonical_json(combined_configuration)).hexdigest()
        metrics = _metric_view(record)
        failures = _promotion_failures(metrics, cadence_survived=cadence_survived)
        combination_rows.append(
            {
                "configuration_id": lab_z_id,
                "combined_configuration": combined_configuration,
                "development_result": metrics,
                "promotion_failures": failures,
                "promotion_standard_met": not failures,
            }
        )
    combination_rows.sort(
        key=lambda row: float(cast(dict[str, Any], row["development_result"])["skill_versus_zero"]),
        reverse=True,
    )
    for rank, row in enumerate(combination_rows, 1):
        row["pre_holdout_rank"] = rank

    frozen_source_ids = cast(list[str], combination_design["frozen_source_configuration_ids"])
    if len(frozen_source_ids) > 3:
        raise ValueError("LAB-Z terminal development freeze exceeds three configurations")
    combination_by_source = {
        str(cast(dict[str, Any], row["combined_configuration"])["source_configuration_id"]): row
        for row in combination_rows
    }
    terminal_rows: list[dict[str, Any]] = []
    for source_id in frozen_source_ids:
        combined = combination_by_source[source_id]
        terminal_rows.append(
            {
                "configuration_id": combined["configuration_id"],
                "source_configuration_id": source_id,
                "result": _metric_view(terminal_by_id[source_id]),
            }
        )

    output_root = Path(str(design["output_root"]))
    if output_root.exists():
        raise FileExistsError(output_root)
    output_root.mkdir(parents=True)
    freeze = {
        "contract": "qtrad-r2-historical-lab-z-finalist-freeze-v1",
        "evidence_label": EVIDENCE_LABEL,
        "manifest_sha256": manifest_sha256,
        "finalist_configuration_ids": [
            combination_by_source[source_id]["configuration_id"] for source_id in frozen_source_ids
        ],
        "terminal_results_reused_from_frozen_dependency_finalists": True,
    }
    slate = cast(dict[str, Any], design["future_experiment_slate"])
    outputs: dict[str, object] = {
        "dimension-summary.json": {
            "evidence_label": EVIDENCE_LABEL,
            "source_class": SOURCE_CLASS,
            "manifest_sha256": manifest_sha256,
            "workstreams": dimensions,
        },
        "finalist-freeze.json": freeze,
        "former-holdout-finalist-results.json": {
            "evidence_label": EVIDENCE_LABEL,
            "source_class": SOURCE_CLASS,
            "results": terminal_rows,
        },
        "future-experiment-slate.json": slate,
    }
    for name, value in outputs.items():
        (output_root / name).write_bytes(_canonical_json(value) + b"\n")
    with (output_root / "combination-register.jsonl").open("xb") as stream:
        for row in combination_rows:
            stream.write(_canonical_json(row) + b"\n")

    summary = {
        "status": "COMPLETE",
        "evidence_label": EVIDENCE_LABEL,
        "source_class": SOURCE_CLASS,
        "manifest_sha256": manifest_sha256,
        "workstream_count": len(dimensions),
        "combined_configuration_count": len(combination_rows),
        "frozen_finalist_count": len(frozen_source_ids),
        "promotion_standard_pass_count": sum(
            bool(row["promotion_standard_met"]) for row in combination_rows
        ),
        "nonlinear_challenger_count": 0,
        "sequence_challenger_count": 0,
        "terminal_results_reused_without_new_terminal_access": True,
    }
    (output_root / "run-summary.json").write_bytes(_canonical_json(summary) + b"\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(run(arguments.config), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
