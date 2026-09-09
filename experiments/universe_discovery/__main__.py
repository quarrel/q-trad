# pyright: strict
"""Run a CPU-only synthetic implementation batch: uv run -m experiments.universe_discovery."""

from __future__ import annotations

import argparse
import hashlib
import json
import resource
import statistics
import subprocess
import time
from dataclasses import asdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from experiments.universe_discovery.atlas import Cohorts, Probe, describe, select
from experiments.universe_discovery.fixtures import fixture_panel
from experiments.universe_discovery.panel import (
    SOURCE,
    SyntheticPartition,
    index_panel,
    instant,
    load_panel,
    write_panel,
)
from experiments.universe_discovery.payoff import positions, prepare
from experiments.universe_discovery.reducers import VintageScores, reduce


def _encode(value: object) -> object:
    if isinstance(value, (date, datetime, Decimal)):
        return str(value)
    raise TypeError(f"Unsupported output value: {type(value).__name__}")


def save(path: Path, value: object) -> None:
    with path.open("x") as output:
        json.dump(value, output, default=_encode, indent=2, allow_nan=False, sort_keys=True)
        output.write("\n")


def register(root: Path, value: object) -> None:
    with (root / "run_register.jsonl").open("a") as output:
        output.write(json.dumps(value, default=_encode, allow_nan=False, sort_keys=True) + "\n")


def run_fixture(root: Path, run_id: str, families: int = 24) -> dict[str, object]:
    if not run_id or Path(run_id).name != run_id or run_id in (".", ".."):
        raise ValueError("run_id must be a single directory name")
    started = time.perf_counter()
    root.mkdir(parents=True, exist_ok=True)
    destination = root / run_id
    destination.mkdir()  # A repair gets a new ID; no output replacement.
    register(
        root,
        {
            "run_id": run_id,
            "status": "STARTED",
            "substantive_configs": 2,
            "source": SOURCE,
            "families": families,
        },
    )
    try:
        return _batch(root, destination, run_id, families, started)
    except BaseException as error:
        # Process boundary: record failure class and propagate the original failure.
        register(root, {"run_id": run_id, "status": "FAILED", "error_type": type(error).__name__})
        raise


def _batch(
    root: Path, destination: Path, run_id: str, families: int, started: float
) -> dict[str, object]:
    stage = time.perf_counter()
    markets, created = fixture_panel(families)
    panel_path = destination / "daily.parquet"
    checksum = write_panel(panel_path, created)
    generation_seconds = time.perf_counter() - stage
    partition = SyntheticPartition(date(2018, 1, 1), date(2020, 12, 31), date(2021, 1, 1))
    quarters = tuple(
        (
            date(year, month, 1) - timedelta(days=1),
            date(year + (month == 10), (month + 2) % 12 + 1, 1) - timedelta(days=1),
        )
        for year in (2019, 2020)
        for month in (1, 4, 7, 10)
    )
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    source_hashes = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(Path(__file__).parent.glob("*.py"))
    }
    spec: dict[str, object] = {
        "experiment_class": "IMPLEMENTATION_ONLY",
        "source": SOURCE,
        "source_identity": checksum,
        "code_commit": commit,
        "code_files_sha256": source_hashes,
        "permitted_empirical_inputs": "NONE_APPROVED",
        "exposure": "All values and dates synthetic; no empirical payload was used",
        "synthetic_source_endpoint": "2022-12-31",
        "synthetic_reserve": ["2021-Q1", "2022-Q4"],
        "partition": asdict(partition),
        "quarters": quarters,
        "access_isolation": (
            "NOT_ESTABLISHED; loader denial is not actual tool/filesystem isolation"
        ),
        "families": families,
        "hypotheses": {
            "continuation": (
                "Sign preceding 20 available same-contract session changes; hold <=5 sessions"
            ),
            "reversal": "Opposite sign preceding 5 available same-contract changes; hold 1 session",
        },
        "rationale": "Invented AR(+.8), AR(-.8), IID mechanisms exercise selector/probe behaviour",
        "selection": (
            "Continuation trend efficiency; reversal minus lag1; group cap2; "
            "|corr| .95 components; canonical ties"
        ),
        "eligibility": (
            "12 calendar months, >=.9 synthetic weekday coverage, >=60 changes, "
            "positive scale, known listing/contract, volume and resolved cost"
        ),
        "descriptors": {
            "volatility": "population sd of same-contract daily price changes",
            "range": "mean(high-low)",
            "vol_of_vol": "population sd of trailing20 population sd",
            "efficiency": "abs(sum changes)/sum(abs changes); zero denominator unavailable",
            "lag": "Pearson lag1; constant/short arrays unavailable",
            "gaps_drawdown": "max abs(open-prior same-contract close); cumulative-change drawdown",
            "liquidity": "mean volume and open_interest separately, published by cutoff only",
            "range_to_cost": "mean_range*multiplier/(2*(variable_cost+commission))",
            "invalid_prices": (
                "Decimal contract changes remain valid; percentage/log domains unavailable"
            ),
            "atlas_windows": "12 calendar months and rolling16 weeks at each quarterly cutoff",
        },
        "cohort_size": 6,
        "anchors": "First up to20 SYNTHETIC families; not mappings of real anchors",
        "draws": 100,
        "seeds": "731+draw; draw0..99, reset each vintage/probe",
        "broad_matching": "economic-group-stratified by selected group counts",
        "nuisance_matching": (
            "exact group, volume>=1000, range/cost>=10, session and "
            "history>=.95 bins; distinct per slot, overlap with selected "
            "allowed, no score matching"
        ),
        "entry": "next artificial UTC14 open after strictly available signal; no same-close fill",
        "exit": (
            "UTC21 close at horizon/weekend/known contract last/quarter/listing "
            "end, whichever first"
        ),
        "position": (
            "one underlying unit; long/flat/short; no pyramiding; entry scale fixed for MTM"
        ),
        "risk": (
            "Decimal population sd of preceding60 published valid same-contract "
            "monetary changes; minimum .01 money"
        ),
        "costs": (
            "Per side .10 variable + .02 commission; .01 per carried session; "
            "base/double variable friction"
        ),
        "support": (
            "COMPLETE_ALL_COMPARATORS; separate matched-complete sensitivity; "
            "never reweight a missing member"
        ),
        "reduction": (
            "equal session within market, equal market within cohort, all100 "
            "draws, equal paired vintage; section8.2 slot contribution"
        ),
        "uncertainty": "1000 whole-quarter bootstrap draws seed0; descriptive only",
        "triage": (
            "No empirical advancement from fixtures; >=8 quarters, positive "
            "mean/median/majority matched deltas, positive base/double "
            "selected, >=3 groups, max positive market/quarter share<=.5"
        ),
        "budget": "CPU; 2 substantive configs; <20GB target; minutes, no GPU or fits",
        "limitations": [
            (
                "No historical market membership, real anchor mapping or empirical "
                "session/roll adapter qualified"
            ),
            "R2 acquisition/OOF/terminal comparisons unavailable: empirical inputs NONE_APPROVED",
            (
                "No H-SESSION/H-EVENT efficacy, actual stress episode, currency "
                "conversion, capital return, collateral or margin"
            ),
            "No delayed-close fill, intraday stop, depth or limit-move exit evidence",
        ],
    }
    save(destination / "experiment_spec.json", spec)  # Before descriptors, selection or scoring.
    save(destination / "catalogue.json", [asdict(m) for m in markets])
    stage = time.perf_counter()
    loaded = load_panel(panel_path, partition, partition.start, partition.end, checksum)
    indexed = index_panel(loaded)
    preparations = {m.family: prepare(m, indexed[m.family]) for m in markets}
    preparation_seconds = time.perf_counter() - stage
    atlas_records: list[dict[str, object]] = []
    selected_records: list[dict[str, object]] = []
    cohorts: dict[tuple[date, Probe], Cohorts] = {}
    probes: tuple[Probe, ...] = ("continuation", "reversal")
    stage = time.perf_counter()
    for cutoff_day, _ in quarters:
        cutoff = instant(cutoff_day, 23)
        ds = tuple(describe(m, indexed[m.family], cutoff) for m in markets)
        for weeks, table in (
            (52, ds),
            (16, tuple(describe(m, indexed[m.family], cutoff, 16) for m in markets)),
        ):
            for descriptor in table:
                record: dict[str, object] = asdict(descriptor)
                record.pop("daily_changes")
                atlas_records.append(
                    {
                        "cutoff": cutoff,
                        "window": "12_months" if weeks == 52 else "16_weeks",
                        **record,
                    }
                )
        for probe in probes:
            selected = select(ds, markets, probe)
            cohorts[(cutoff_day, probe)] = selected
            selected_records.append({"cutoff": cutoff, "probe": probe, **asdict(selected)})
    save(destination / "atlas.json", atlas_records)
    save(
        destination / "selection_register.json", selected_records
    )  # All memberships before scoring.
    selection_seconds = time.perf_counter() - stage
    stage = time.perf_counter()
    outcomes = {
        (cutoff, probe, m.family): positions(
            m, preparations[m.family], instant(cutoff, 23), end, probe
        )
        for cutoff, end in quarters
        for probe in probes
        for m in markets
    }
    save(
        destination / "position_ledger.json",
        [
            {
                "cutoff": cutoff,
                "probe": probe,
                "family": family,
                "sessions": [asdict(row) for row in outcome.sessions],
            }
            for (cutoff, probe, family), outcome in outcomes.items()
        ],
    )
    scoring_seconds = time.perf_counter() - stage
    stage = time.perf_counter()
    results: dict[str, object] = {}
    details: list[dict[str, object]] = []
    groups = {m.family: m.group for m in markets}
    for probe in probes:
        for scenario, friction in (("base", Decimal(1)), ("double", Decimal(2))):
            vintages: list[VintageScores] = []
            anchor_views: dict[str, list[float | None] | None] = {}
            for cutoff, _ in quarters:
                selected = cohorts[(cutoff, probe)]
                scores = {
                    m.family: outcomes[(cutoff, probe, m.family)].score(friction) for m in markets
                }
                anchor_views[str(cutoff)] = (
                    [
                        statistics.fmean(v for m in draw if (v := scores[m]) is not None)
                        if all(scores[m] is not None for m in draw)
                        else None
                        for draw in selected.anchor_size_matched
                    ]
                    if selected.anchor_size_matched is not None
                    else None
                )
                vintages.append(
                    VintageScores(
                        str(cutoff),
                        selected.selected,
                        selected.anchors,
                        selected.simple,
                        selected.broad,
                        selected.matched,
                        scores,
                        groups,
                    )
                )
                for m in markets:
                    details.append(
                        {
                            "cutoff": cutoff,
                            "probe": probe,
                            "scenario": scenario,
                            "family": m.family,
                            **outcomes[(cutoff, probe, m.family)].summary(friction),
                        }
                    )
            reduced = reduce(vintages)
            selected_scores = [r["selected"] for r in reduced["vintages"] if r["primary_scorable"]]
            selected_mean = (
                statistics.fmean(v for v in selected_scores if v is not None)
                if selected_scores
                else None
            )
            results[f"{probe}:{scenario}"] = {
                "reduction": reduced,
                "size_matched_anchor_draws": anchor_views,
                "partial_selection_vintages": [
                    str(cutoff)
                    for cutoff, _ in quarters
                    if cohorts[(cutoff, probe)].selection_reason != "COMPLETE"
                ],
                "selected_mean": selected_mean,
                "selector_value": reduced["overall_deltas"]["matched"],
                "hypothesis_positive": selected_mean > 0 if selected_mean is not None else None,
                "decision": "IMPLEMENTATION_ONLY; empirical conclusion DATA_LIMITED",
            }
    save(destination / "metrics.json", results)
    save(destination / "market_diagnostics.json", details)
    aggregation_seconds = time.perf_counter() - stage
    performance: dict[str, object] = {
        "elapsed_seconds": time.perf_counter() - started,
        "generation_write_seconds": generation_seconds,
        "load_prepare_seconds": preparation_seconds,
        "atlas_selection_seconds": selection_seconds,
        "scoring_seconds": scoring_seconds,
        "aggregation_write_seconds": aggregation_seconds,
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "panel_rows": len(loaded),
        "families": families,
        "quarters": len(quarters),
        "panel_decodes": 1,
        "input_authentications": 1,
        "family_preparations": len(preparations),
        "outcome_calculations": len(outcomes),
        "control_draws": 100,
        "substantive_configs": 2,
        "model_fits": 0,
        "device": "CPU",
        "bytes_before_performance_record": sum(p.stat().st_size for p in destination.iterdir()),
        "scale_limit": (
            "Synthetic3-year sample only; not measured150-family20-year "
            "empirical contract expansion"
        ),
    }
    save(destination / "performance.json", performance)
    register(
        root,
        {
            "run_id": run_id,
            "status": "COMPLETE",
            "performance": performance,
            "output": str(destination),
            "source_sha256": checksum,
        },
    )
    return performance


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--families", type=int, default=24)
    args = parser.parse_args()
    output_root: Path = args.output_root
    run_id: str = args.run_id
    families: int = args.families
    print(json.dumps(run_fixture(output_root, run_id, families), indent=2))


if __name__ == "__main__":
    main()
