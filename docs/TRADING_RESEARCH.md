# Multi-asset paper-trading research programme

## Purpose and authority

This document defines q-trad's longer-term paper-trading research programme. `PLAN.md` selects the
active milestone; this document supplies the stable research intent and gates for work involving
targets, forecasts, risk, portfolio construction or paper outcomes.

The programme asks whether short-horizon expected-return forecasts can survive realistic costs and
be converted into coherent paper positions under joint risk constraints. A negative result is a
valid outcome. No milestone authorises a broker order, production IG endpoint or real-capital use.

The centre of the decision stack is:

> multi-horizon expected-return forecasts → explicit costs and portfolio risk → horizon-aware
> targets → constrained paper portfolio → causal outcomes.

The retained single-instrument strategy-ranking proof demonstrates causal data, fills, accounting
and reproducibility. It is historical framework evidence, not the intended forecasting or
allocation model and not an effectiveness claim.

## Concepts that remain separate

- A **point forecast** estimates an asset return for a declared horizon and return definition. It
  records feature and training cut-offs, model/configuration identity and experiment/fold lineage.
- **Forecast dispersion or model uncertainty** describes predictive uncertainty. It is neither
  expected return nor portfolio covariance and is not required for the first offline MVP.
- A **cost estimate** describes expected spread, adverse slippage, commission, financing and any
  supported impact for a proposed physical position change. Gross and net alpha remain explicit so
  costs cannot be subtracted twice.
- A **portfolio risk state** contains an ordered, versioned covariance estimate and configured
  group/currency exposures. It is independent of any per-asset forecast scale.
- A **horizon sleeve** retains one asset/horizon intent, current virtual position, review/expiry
  time and model attribution. Conflicting horizons remain visible.
- A **target portfolio** nets sleeve intents to physical paper positions, applies global
  constraints and records every adjustment using stable reason codes.
- A **paper outcome** uses subsequent healthy executable bid/ask evidence and separately reports
  gross midpoint performance, costs and net P&L.

Models and experiment configurations are compared out of sample. There is no runtime strategy
selector, learned regime veto or automatic promotion lifecycle in the offline MVP.

## Data and capture policy

### Universe

All 20 markets in `capture-v3` remain potentially tradable. An experiment may mark an instrument as
a forecast target, context-only input or paper-ineligible without changing its capture status. No
instrument count is a success criterion.

China A50 and Korea 200 are the immediate APAC capture candidates. IG's public
[index product details](https://www.ig.com/en/help-and-support/articles/597590-what-are-ig-s-indices-cfd-product-details)
identify both market concepts, but public pages do not establish an acceptable demo listing. Each
must pass the existing fail-closed IG demo catalogue, listing, mapping, session and
product-economics review. The current collector remains on `capture-v3` until an immutable
`capture-v4` release is separately approved and activated. A rejected or ambiguous candidate is
recorded rather than guessed. Singapore, India and USD/CNH are not planned additions.

Capacity already proved above the intended universe size. A release that changes only the reviewed
universe needs focused channel readiness, delivery, loss and lag evidence rather than another broad
load programme.

### Native and historical evidence

Continue native IG quote capture because lost forward bid/ask history cannot be reconstructed.
Audit bid/ask-size coverage and meaning before using quote imbalance. Top-of-book size changes are
not cumulative volume delta; any trade-volume feature requires a separately validated source.

Use two historical-data tracks:

1. native IG capture and provenance-distinct IG candles for bounded prototyping; and
2. reviewed external samples/licensing where they materially accelerate chronological model work.

External data must retain venue, product, timestamp and correction provenance. It may support model
training or hypothesis rejection but may not masquerade as IG CFD quote history or substantiate IG
paper fills, spreads or slippage. A purchase or new adapter requires its own explicit decision.

### Chronology and alignment

- Internal timestamps are timezone-aware UTC; source, receive, feature cut-off, decision, training
  cut-off and target-availability times remain distinct.
- The initial horizon grid is 5, 15, 30 and 60 minutes, with 15 minutes used for the first complete
  path. The schema supports every configured horizon from the start.
- The forecasting label is cumulative log midpoint return from a completed one-minute bar close to
  the exactly matching later completed close. Missing target intervals are excluded, not filled.
- Economic evaluation is separate: entry and exit/rebalance use subsequent healthy executable-side
  observations with versioned latency and adverse slippage.
- Multi-market alignment preserves closures, gaps, stale inputs and unavailable assets. Executable
  prices are never forward-filled.
- Overlapping target and feature windows determine purging and embargo. Final holdout observations
  cannot influence feature choice, model selection, calibration or risk configuration.

## Active research stages

### R0 — alignment, coverage and data readiness

Realign active documentation; review and, with separate authority, release China A50 and Korea 200;
audit quote-size, session, gap and aligned-bar coverage; and make a bounded historical-data decision.

### R1 — causal multi-asset research foundation

Implement model-independent identifiers and units, aligned panels, multi-horizon targets,
target-availability and overlap intervals, favourable/adverse excursions, chronological folds,
purging/embargo and an immutable out-of-fold forecast artefact. Forecast consumers must not need to
load the model that created the data.

### R2 — local and pooled baselines

Produce chronological per-asset Ridge forecasts and a pooled non-graph cross-asset control. Begin
with ablatable price-return, volatility, time/session, spread and validated quote-imbalance feature
families. Fit all transformations inside each training fold.

Pass evidence includes predictive loss, linear/rank relationship, direction where meaningful,
forecast-bucket monotonicity, coverage and stability by asset, horizon and period. Ridge remains a
retained baseline even when a stronger model is tested.

### R3 — cost and portfolio baseline

Add versioned cost assumptions, shrinkage covariance, explicit group/currency mappings,
horizon-specific virtual positions, horizon-specific optimisation and global netting/constraint
repair. Select solver and numerical dependencies at milestone entry using version-specific
documentation; CVXPY and the source plan's package tree are not architectural requirements.

Model matrices may use an appropriate floating-point numerical representation. Prices, quantities,
currency conversion and money continue to use `Decimal`, with explicit conversions at the numerical
boundary.

Optimiser failure cannot expose partially solved targets. Retain a valid current position where
permitted; otherwise project it into the valid set, block new alpha-driven exposure and record a
stable failure reason.

### R4 — residual structural graph experiment

Train the cross-asset model only on residuals calculated from out-of-fold local forecasts. Compare:

- local models only;
- pooled non-graph cross-asset model;
- fixed economically specified graph;
- learned structural graph with an LSTM temporal baseline; and
- shuffled/permuted graph control.

This experiment is completed even if simpler forecasts are weak. The graph is retained only if it
adds stable held-out information beyond the simpler controls. State-dependent adjacency and session
experts are not part of this milestone.

### R5 — integrated offline MVP

Compare local threshold decisions, local forecasts plus portfolio optimisation, local plus residual
graph forecasts plus optimisation, and the complete multi-horizon sleeve system. Produce
chronological forecast, economic and portfolio reports with cost, feature, graph, horizon and nearby
parameter sensitivities. Every forecast-to-position adjustment remains attributable.

MVP completion means the framework can measure incremental forecast and portfolio value honestly.
It does not require positive Sharpe, profitability or production readiness.

### R6 — continuous shadow paper

Adapt the stable offline contracts to continuous live data. Retain forecasts for every evaluated
configuration, maintain sleeve attribution through physical netting, rebalance persistent virtual
positions using later executable quotes and record operational vetoes and reconciling P&L. Runtime
and checkpoint architecture is decided at this milestone from the demonstrated lifecycle needs.

## Evaluation gates

- **Forecast gate:** compare with local and pooled controls; require chronological information that
  is stable across more than one period or asset subset.
- **Economic gate:** compare forecast scale with observed/modelled costs, report gross and net
  ordering, and stress latency, spreads and slippage.
- **Portfolio gate:** require all constraints after rounding/repair, bounded concentration,
  attributable turnover, reconciled marginal risk and stability near the chosen risk parameters.
- **Research-process gate:** bind data, code, configuration, folds and outputs by immutable identity;
  retain failed/rejected experiments; record the number of configurations tested.

No individual metric substitutes for the others. Rank correlation is forecast evidence, not
profitability; negative paper P&L does not excuse look-ahead or unrealistic costs.

## Deferred promotion register

After the offline MVP, separately ablate probabilistic/distributional heads, conformal calibration,
opportunity or expected-net-payoff models, APAC/Atlantic experts, dynamic graphs, alternative
temporal engines, context-only nodes, deep ensembles, competing-risk targets and multi-period
optimisation. Interfaces may leave room for these outputs, but active code must not scaffold them
before an experiment exercises them.

Each promoted component must improve the immediately simpler comparable configuration on locked,
time-ordered evidence after accounting for costs and experiment count.

## Compatibility and safety

Before the first decision-grade result, internal research schemas may change incompatibly. Prefer a
one-time migration, re-export or clean rebuild over dual readers and indefinite compatibility. The
archived research-proof configuration and report remain reproducible evidence but do not constrain
new interfaces.

The running collector's raw and canonical history is never rewritten or selectively deleted. No
research or paper component connects to an IG order operation, production IG endpoint or external
capital path.
