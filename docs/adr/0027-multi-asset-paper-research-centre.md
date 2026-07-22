# ADR 0027: multi-horizon forecast and paper portfolio research centre

- **Status:** Accepted
- **Date:** 2026-07-22
- **Supersedes:** ADR 0005; the allocation/netting parts of ADR 0007; the immediate sequencing in
  ADR 0008; and the strategy-ranking/regime roadmap in ADR 0026

## Context

The first research-framework proof demonstrated causal forecasts/outcomes, executable-side paper
fills, independent P&L arithmetic and reproducible ranking. Its single-instrument strategies,
bounded strength, isolated round trips and simple rank order were deliberately narrow proof
contracts.

The next research question is materially different: whether local and cross-asset expected-return
forecasts at several holding horizons contain stable out-of-sample information, survive costs and
justify coherent joint paper positions. Runtime strategy ranking and a learned regime selector do
not answer that question.

## Decision

Organise the active programme around:

> multi-horizon expected-return forecasts → explicit costs and ordered portfolio risk → persistent
> horizon intents → constrained net paper positions → causal paper outcomes.

Complete the first MVP offline with chronological folds, purging/embargo, immutable out-of-fold
forecasts and component-aware ablations. Use per-asset Ridge and pooled non-graph controls. Commit to
testing a residual learned-structural GNN-LSTM against fixed and shuffled graph controls, but retain
it only if held-out incremental evidence supports it.

Represent asset/horizon intentions separately and reconcile horizon-specific targets into one
physical paper position per instrument. Portfolio risk and cost estimation remain separate from
expected return and forecast uncertainty. Every constraint, netting, rounding, fallback or
operational adjustment receives stable attribution.

All 20 `capture-v3` markets remain potentially tradable. China A50 and Korea 200 enter the existing
fail-closed provider review as capture candidates; there is no numerical universe target and no
candidate has selection or deployment authority.

Continuous shadow-paper integration follows the offline MVP. Its process/checkpoint architecture is
decided then from demonstrated lifecycle needs. No external order route is introduced.

## Retained decisions

- ADR 0006 remains authoritative for completed-bar decisions, causal receive-time ordering,
  executable bid/ask fills, sessions, latency and adverse slippage.
- ADR 0007 remains authoritative for `Decimal` financial arithmetic, AUD reporting, conversion
  provenance, immutable fills and independently reconciling accounting. Its fixed allocated/shadow
  budgets and prohibition on netting isolated strategy ledgers do not govern the new horizon
  portfolio model.
- ADR 0009 remains authoritative for fail-closed product economics and provider-listing review.
- ADR 0026 remains authoritative for shortest-trustworthy-research priority, evidence preservation,
  experimental compatibility and the no-live-order boundary. Its simple strategy ranking and
  diagnostic market-state milestone are superseded.

## Consequences

The retained ranking proof remains reproducible historical evidence but does not impose an internal
compatibility layer on new forecast, risk, sleeve or portfolio contracts. Before a decision-grade
result, one-time migrations, re-exports or clean rebuilds are preferred to dual interfaces.

The source planning document's suggested package tree and CVXPY choice are not adopted as durable
architecture. Solver and model libraries are selected at the milestone that exercises them using
version-specific evidence. Probabilistic calibration, session experts, dynamic graphs and other
refinements remain promotion-gated experiments.
