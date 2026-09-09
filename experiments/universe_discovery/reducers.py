# pyright: strict
"""Equal-market/equal-vintage reductions of already computed scenario scores."""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from math import isclose, isfinite
from random import Random
from statistics import mean, median
from typing import TypedDict


@dataclass(frozen=True)
class VintageScores:
    vintage: str
    selected: tuple[str, ...]
    anchors: tuple[str, ...]
    simple: tuple[str, ...]
    broad: tuple[tuple[str, ...], ...]
    matched: tuple[tuple[str, ...], ...] | None
    scores: dict[str, float | None]
    groups: dict[str, str]


class VintageResult(TypedDict):
    vintage: str
    selected_members: list[str]
    selected: float | None
    controls: dict[str, float | None]
    draws: dict[str, list[float | None]]
    deltas: dict[str, float | None]
    unavailable: dict[str, str]
    missing_selected_fraction: float
    primary_scorable: bool


class Concentration(TypedDict):
    status: str
    market: dict[str, float]
    quarter: dict[str, float]
    selection_frequency: dict[str, int]
    best_market_share: float | None
    best_quarter_share: float | None
    positive_market_contribution: bool
    positive_quarter_contribution: bool


class Diagnostics(TypedDict):
    group_contributions: dict[str, float]
    group_balanced_delta: float | None
    leave_one_market_delta: dict[str, float]
    leave_one_quarter_delta: dict[str, float]
    quarter_bootstrap_interval: list[float]
    matched_median_delta: float | None
    matched_positive_fraction: float | None
    matched_complete_delta: float | None


class Reduction(TypedDict):
    status: str
    vintages: list[VintageResult]
    overall_deltas: dict[str, float | None]
    concentration: Concentration
    diagnostics: Diagnostics


def _cohort(members: tuple[str, ...], scores: dict[str, float | None]) -> float | None:
    if not members:
        return None
    values = [scores[market] for market in members]
    if any(value is None for value in values):
        return None
    return mean(value for value in values if value is not None)


def _validate(vintage: VintageScores) -> None:
    cohorts = (vintage.selected, vintage.anchors, vintage.simple, *vintage.broad)
    if vintage.matched is not None:
        cohorts += vintage.matched
        if any(len(draw) != len(vintage.selected) for draw in vintage.matched):
            raise ValueError(f"{vintage.vintage}: matched slots do not match selected size")
    for cohort in cohorts:
        if len(set(cohort)) != len(cohort):
            raise ValueError(f"{vintage.vintage}: repeated market within cohort")
        for market in cohort:
            score = vintage.scores[market]
            if score is not None and not isfinite(score):
                raise ValueError(f"{vintage.vintage}: non-finite score for {market}")
            if not vintage.groups[market]:
                raise ValueError(f"{vintage.vintage}: missing economic group for {market}")


def _vintage(vintage: VintageScores) -> VintageResult:
    selected = _cohort(vintage.selected, vintage.scores)
    draws = {
        "broad": [_cohort(draw, vintage.scores) for draw in vintage.broad],
        "matched": [
            _cohort(draw, vintage.scores)
            for draw in (vintage.matched if vintage.matched is not None else ())
        ],
    }
    controls = {
        "anchors": _cohort(vintage.anchors, vintage.scores),
        "simple": _cohort(vintage.simple, vintage.scores),
    }
    for name, values in draws.items():
        controls[name] = (
            mean(value for value in values if value is not None)
            if values and all(value is not None for value in values)
            else None
        )
    unavailable: dict[str, str] = {}
    deltas: dict[str, float | None] = {}
    for name, score in controls.items():
        deltas[name] = selected - score if selected is not None and score is not None else None
        if selected is None:
            unavailable[name] = "SELECTED_OUTCOME_UNAVAILABLE"
        elif name == "matched" and not vintage.matched:
            unavailable[name] = "MATCHING_UNAVAILABLE"
        elif score is None:
            unavailable[name] = "CONTROL_OUTCOME_UNAVAILABLE"
    return VintageResult(
        vintage=vintage.vintage,
        selected_members=list(vintage.selected),
        selected=selected,
        controls=controls,
        draws=draws,
        deltas=deltas,
        unavailable=unavailable,
        primary_scorable=all(value is not None for value in deltas.values()),
        missing_selected_fraction=(
            sum(vintage.scores[m] is None for m in vintage.selected) / len(vintage.selected)
            if vintage.selected
            else 1.0
        ),
    )


def _share(values: Sequence[float]) -> tuple[float, bool]:
    positive = [max(0.0, value) for value in values]
    total = sum(positive)
    return (max(positive) / total, True) if total > 0 else (1.0, False)


def reduce(vintages: Sequence[VintageScores]) -> Reduction:
    """Require complete recorded cohorts; never drop missing members or random draws.

    Primary support requires every predeclared comparator. Matched-complete
    sensitivity is separate; no triage or alpha verdict is inferred.
    """
    if len({v.vintage for v in vintages}) != len(vintages):
        raise ValueError("Duplicate vintage identity")
    for vintage in vintages:
        _validate(vintage)
    rows = [_vintage(vintage) for vintage in vintages]
    primary_rows = [row for row in rows if row["primary_scorable"]]
    overall: dict[str, float | None] = {}
    for name in ("anchors", "simple", "broad", "matched"):
        values = [row["deltas"][name] for row in primary_rows]
        present = [value for value in values if value is not None]
        overall[name] = mean(present) if present else None
    matched_complete = [value for row in rows if (value := row["deltas"]["matched"]) is not None]
    paired = [
        vintage for vintage, row in zip(vintages, rows, strict=True) if row["primary_scorable"]
    ]
    market: dict[str, float] = defaultdict(float)
    quarter: dict[str, float] = {}
    frequency: dict[str, int] = defaultdict(int)
    group_contributions: dict[str, float] = defaultdict(float)
    slot_deltas: dict[str, dict[str, float]] = {}
    balanced: list[float] = []
    for vintage in paired:
        assert vintage.matched is not None
        slots: dict[str, float] = {}
        group_slots: dict[str, list[float]] = defaultdict(list)
        for slot, selected in enumerate(vintage.selected):
            selected_score = vintage.scores[selected]
            control_scores = [vintage.scores[draw[slot]] for draw in vintage.matched]
            assert selected_score is not None and all(s is not None for s in control_scores)
            delta = selected_score - mean(s for s in control_scores if s is not None)
            slots[selected] = delta
            contribution = delta / (len(paired) * len(vintage.selected))
            market[selected] += contribution
            frequency[selected] += 1
            group_contributions[vintage.groups[selected]] += contribution
            group_slots[vintage.groups[selected]].append(delta)
        slot_deltas[vintage.vintage] = slots
        quarter[vintage.vintage] = mean(slots.values()) / len(paired)
        balanced.append(mean(mean(values) for values in group_slots.values()))
    effect = overall["matched"]
    if effect is not None and not all(
        isclose(sum(values), effect, rel_tol=1e-12, abs_tol=1e-12)
        for values in (market.values(), quarter.values())
    ):
        raise ArithmeticError("Matched contribution totals do not reconcile")
    market_share, market_positive = _share(list(market.values()))
    quarter_share, quarter_positive = _share(list(quarter.values()))
    quarter_deltas = {name: mean(slots.values()) for name, slots in slot_deltas.items()}
    leave_market: dict[str, float] = {}
    for excluded in market:
        remaining = [
            mean(value for name, value in slots.items() if name != excluded)
            for slots in slot_deltas.values()
            if any(name != excluded for name in slots)
        ]
        if remaining:
            leave_market[excluded] = mean(remaining)
    leave_quarter = {
        excluded: mean(value for name, value in quarter_deltas.items() if name != excluded)
        for excluded in quarter_deltas
        if len(quarter_deltas) > 1
    }
    bootstrap: list[float] = []
    if quarter_deltas:
        # Whole quarters retain their cross-market dependence. Descriptive only.
        rng = Random(0)
        values = list(quarter_deltas.values())
        replicates = sorted(mean(rng.choices(values, k=len(values))) for _ in range(1000))
        bootstrap = [replicates[24], replicates[974]]
    return Reduction(
        status="SCORABLE" if paired else "INSUFFICIENT_EVIDENCE",
        vintages=rows,
        overall_deltas=overall,
        concentration=Concentration(
            status="SCORABLE" if paired else "INSUFFICIENT_EVIDENCE",
            market=dict(market),
            quarter=quarter,
            selection_frequency=dict(frequency),
            best_market_share=market_share if paired else None,
            best_quarter_share=quarter_share if paired else None,
            positive_market_contribution=market_positive,
            positive_quarter_contribution=quarter_positive,
        ),
        diagnostics=Diagnostics(
            matched_complete_delta=mean(matched_complete) if matched_complete else None,
            group_contributions=dict(group_contributions),
            group_balanced_delta=mean(balanced) if balanced else None,
            leave_one_market_delta=leave_market,
            leave_one_quarter_delta=leave_quarter,
            quarter_bootstrap_interval=bootstrap,
            matched_median_delta=median(quarter_deltas.values()) if paired else None,
            matched_positive_fraction=(
                mean(value > 0 for value in quarter_deltas.values()) if paired else None
            ),
        ),
    )
