# q-trad multi-asset paper-research plan

**Status:** ACTIVE
**Current milestone:** R0 — alignment, coverage and data readiness
**Objective:** determine, with locked chronological evidence, whether multi-horizon local and
cross-asset forecasts can justify a cost- and risk-aware paper portfolio.
**Safety boundary:** IG demo market data and internal paper outcomes only; no external orders.

`docs/TRADING_RESEARCH.md` defines the stable research programme. This file selects the current
work and records only the milestone sequence needed to reach the next trustworthy decision.

## Current position

- `capture-v3` is the live reviewed 20-market IG demo universe. All 20 markets remain potentially
  tradable, subject to experiment role and fail-closed paper eligibility.
- The capture, canonical event, one-minute bar, snapshot/import, Parquet, deterministic replay and
  read-only health paths are implemented.
- A retained single-instrument framework proof produced causal forecasts/outcomes, executable-side
  shadow fills, independently checked P&L and a reproducible ranking report. Its negative cost-aware
  result is evidence that the framework path works, not a strategy conclusion or the intended
  portfolio architecture.
- The next paper programme replaces runtime strategy ranking and regime selection with
  multi-horizon expected-return forecasts, explicit cost/risk states, horizon attribution and
  constrained paper portfolio construction.
- The bounded IG demo review accepted China A50 and Taiwan plus an AUD-denominated VIX for
  context-only capture. Korea 200 has no eligible demo listing, and the reviewed Bitcoin listings
  were unavailable; both are quarantined. An immutable 23-market `capture-v4` is prepared but
  undeployed; publication and activation remain separate authorised operations.

## Milestones

| Milestone | Status | Exit evidence |
|---|---|---|
| R0 — alignment, coverage and data readiness | ACTIVE | active docs agree; China/Korea review is resolved; native/aligned coverage and historical-source decisions are recorded |
| R1 — causal multi-asset research foundation | NOT STARTED | deterministic aligned panels, multi-horizon targets, chronological folds and out-of-fold artefacts pass causality/replay checks |
| R2 — local and pooled baselines | NOT STARTED | per-asset Ridge and pooled non-graph forecasts are compared on locked out-of-sample evidence |
| R3 — cost and portfolio baseline | NOT STARTED | costs, shrinkage risk, horizon positions, global netting and constrained targets reconcile deterministically |
| R4 — residual structural graph experiment | NOT STARTED | local, pooled, fixed, learned and shuffled graph controls measure incremental graph value |
| R5 — integrated offline MVP | NOT STARTED | chronological forecast, economic and portfolio gates report the full ablation set |
| R6 — continuous shadow paper | NOT STARTED | the validated stack runs continuously with causal executable fills, horizon attribution and reconciling paper P&L |

## R0 — alignment, coverage and data readiness

1. **Done:** realign active planning, architecture, agent-routing and status documents around the
   multi-asset programme; archive the superseded strategy-ranking plan and planning inputs.
2. **Done:** review China A50, Korea 200, Taiwan, VIX and Bitcoin through the bounded demo gate;
   accept the smaller China/Taiwan contracts and context-only VIX, and quarantine unavailable
   Korea/Bitcoin without guessing.
3. **Done:** prepare immutable 23-market `capture-v4`. Publication, activation and cloud changes
   remain separate explicitly authorised operations; `capture-v3` keeps running until then.
4. Audit native quote and bar evidence needed by R1–R3: bid/ask-size coverage, session coverage,
   gaps, revisions, aligned multi-market intervals and available product economics.
5. Combine the separately gathered historical-source research with a bounded review of IG candles,
   external samples, timestamp/correction semantics, licensing and exact instrument coverage.
6. Record which data can support prototyping, model selection and decision-grade IG paper
   conclusions. External history never substitutes for native executable-side evidence.
7. Repair and re-run weekly restore verification independently of research and universe work.

R0 does not implement model, optimiser or live-paper interfaces. It removes avoidable data and
chronology ambiguity before those contracts are fixed.

## R1 — causal multi-asset research foundation

- Use configurable 5, 15, 30 and 60-minute horizons; prove the first end-to-end path at 15 minutes.
- Define completed-bar log-return targets, target-availability and overlap intervals, favourable and
  adverse excursions, and deterministic handling of gaps and closures.
- Build aligned panels without forward-filling executable prices or hiding unavailable assets.
- Generate reproducible walk-forward folds with dependency-derived purging/embargo and a locked
  final holdout.
- Store immutable out-of-fold forecasts with feature/training cut-offs, model/experiment/fold
  lineage and later outcomes, independently of model code.

## R2 — local and pooled baselines

- Retain per-asset Ridge as the required local baseline and add a pooled non-graph cross-asset
  control.
- Begin with ablatable returns, volatility, time/session, spread and validated quote-imbalance
  features. Do not call top-of-book size changes cumulative volume delta.
- Compare loss, correlation/rank correlation, direction, forecast buckets, coverage and stability
  by asset, horizon and period.

## R3 — cost and portfolio baseline

- Version observed spread, adverse slippage, commission, financing and supported impact assumptions.
- Estimate ordered horizon-specific shrinkage covariance and configured group/currency exposures.
- Maintain virtual asset/horizon positions, optimise each horizon, then net and repair the global
  physical paper target with stable reason codes.
- Select solver and numerical dependencies at milestone entry. Preserve `Decimal` at price,
  quantity, conversion and money boundaries.

## R4 — residual structural graph experiment

- Derive graph targets only from out-of-fold local residuals.
- Compare local-only, pooled non-graph, fixed economic graph, learned structural GNN-LSTM and
  shuffled-graph controls.
- Complete the experiment even if simpler forecasts are weak; retain the graph only on incremental
  held-out evidence. Dynamic adjacency and session experts remain deferred.

## R5 — integrated offline MVP

- Compare local threshold, local-plus-optimiser, graph-plus-optimiser and full multi-horizon sleeve
  configurations.
- Report forecast, economic and portfolio gates plus cost, feature, graph, horizon and nearby
  parameter sensitivities.
- Require attributable constraint/fallback decisions and independently reconciled accounting.
- Treat a trustworthy negative conclusion as successful completion.

## R6 — continuous shadow paper

- Adapt only stable offline contracts to continuous live data.
- Retain forecasts for every evaluated configuration and preserve horizon attribution through
  physical netting.
- Rebalance persistent virtual positions using later healthy bid/ask observations and record costs,
  operational vetoes and P&L.
- Decide checkpoint/process architecture from demonstrated runtime lifecycle needs. Add no broker
  order port or external submission.

## Completion and documentation rules

- A milestone completes only with focused automated evidence and a concise retained reference.
- Run the full clean PostgreSQL/static/test gate at code, schema, milestone or release boundaries,
  not for a documentation-only edit.
- Keep `PLAN.md` forward-looking, `docs/STATUS.md` factual and `docs/TRADING_RESEARCH.md` stable.
- Archive superseded chronology and source planning material outside the normal reading path.
- Add an ADR for a durable costly-to-reverse decision, not a reversible model experiment.
