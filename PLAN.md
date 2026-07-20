# q-trad research-framework plan

**Status:** ACTIVE
**Objective:** reach a reproducible, cost-aware strategy ranking on a useful research universe by the
shortest path that preserves research validity.
**Safety boundary:** IG demo market data and internal paper outcomes only; no external orders.

## Current position

- The seven-market data path, canonical capture, bars, replay, Parquet export and read-only console
  are implemented.
- A bounded read-only observation found the OCI `capture-v1` collector ready on all seven channels
  with no internal/SDK loss and caught-up projection; no collector or cloud state was mutated.
- The 20-market candidate catalogue has been reviewed against IG demo. The hash-bound 19-market
  `capture-v2` release is deployed and initially ready; Bitcoin is explicitly quarantined as
  unavailable.
- A local deterministic research core now emits versioned forecasts for simple strategies, pairs
  exact-horizon outcomes, simulates causal bid/ask shadow fills, reconciles isolated ledgers and
  builds a hash-bound ranking report. The first retained snapshot-backed report reproduced its full
  trace and ranking; it is a framework proof with negative cost-aware results, not an effectiveness
  claim.
- The current callback-to-PostgreSQL path passed a 40-instrument, 200-callback/s bounded load run
  with zero loss and sub-second maximum lag, providing local headroom beyond the target catalogue.

Historical data-foundation detail is archived under `docs/archive/data-foundation/`; capture
qualification and incident detail is under `docs/archive/capture-v1/`.

## Milestones

| Milestone | Status | Exit evidence |
|---|---|---|
| M0 — focus and documentation reset | DONE | active docs describe the research loop and archived history is off the reading path |
| M1 — reviewed 20-market universe | DONE | 19 eligible IG demo listings reviewed, published by immutable digest and live with 19/19 channel readiness and zero observed loss |
| M2 — minimal multi-strategy paper path | DONE | several simple strategies produce causal shadow fills and independently checked P&L |
| M3 — deterministic evaluator and rank report | DONE | forecasts join to defined outcomes; replay reproduces scores and rankings |
| M4 — simple market-state comparison | NOT STARTED | contemporaneous state annotation and conditional versus unconditional report |
| M5 — viability review | NOT STARTED | evidence supports continue, revise or stop without requiring a profitability claim |

## M1 — reviewed 20-market universe

Candidate set: the eight FX pairs, eight equity indices, two spot metals, Bitcoin/USD and US crude in
`config/capture-v2-candidates.toml`. It includes every `capture-v1` instrument.

Work:

1. ~~Perform the existing bounded, non-authoritative IG demo candidate review.~~
2. ~~Select only unambiguous, liquid standard contracts with required metadata and economics.~~
3. ~~Record rejected or quarantined candidates without blocking the accepted subset.~~
4. ~~Prove the single connection, callback hand-off, queue and PostgreSQL path at the approved size.~~
5. ~~Define a proportionate active-market observation: per-channel delivery, visible gaps, zero
   internal drops and bounded lag. Do not require a perfect market or explanation of every quiet
   interval.~~
6. ~~Prepare an immutable application release for the reviewed `config/capture-v2.toml`; retain the
   pinned `capture-v1` image/configuration as the rollback point.~~
7. ~~Publish and deploy in separately authorised operations, then require 19/19 readiness and
   caught-up projection without rewriting retained history.~~

M1 does not require paper eligibility for every accepted capture market. Missing paper economics can
quarantine that instrument from strategy execution while capture continues.

## M2 — minimal multi-strategy paper path

- Use at least three simple, deterministic signal examples or parameter variants.
- Emit a common forecast containing strategy/configuration, applicability, observation time,
  horizon, direction/strength and target definition.
- Retain forecasts and hypothetical outcomes for every shadow strategy.
- Apply fixed sizing and hard limits through simple domain policies.
- Fill only from subsequent healthy bid/ask observations with versioned latency and adverse
  slippage.
- Maintain per-strategy virtual positions and cost-aware P&L.
- Prefer a report and inspectable event trace over new operator-console workflows.

## M3 — deterministic evaluator and rank report

- Define the first score contract before comparing results: outcome return, horizon, IC basis,
  window, overlapping observations, minimum sample and cost metrics.
- Pair each forecast with only its later eligible outcome.
- Report sample coverage, IC, paper P&L, drawdown, turnover and sensitivity to costs/latency.
- Include no-signal and simple persistence/reversal benchmarks.
- Produce deterministic unconditional rankings; do not automatically prune or allocate real money.

## M4 — simple market-state comparison

- Begin with transparent contemporaneous volatility/correlation/liquidity features.
- Record `UNKNOWN` when evidence is insufficient.
- Compare conditional and unconditional strategy scores in locked time order.
- Grant state control only if it improves held-out evidence after costs; otherwise keep it diagnostic.

## M5 — viability review

Decide one of:

- **continue:** the framework is trustworthy and at least one research direction merits deeper data
  or forward testing;
- **revise:** the framework works but data, costs or hypotheses need a bounded change;
- **stop:** no current path justifies further investment.

Negative findings are successful evidence. Do not extend infrastructure merely to avoid reaching
this decision.

## Completion and documentation rules

- A milestone is complete when its stated outcome is demonstrated by focused automated checks and a
  concise evidence reference.
- Run the complete clean database/static/test gate at milestone and release boundaries, not after
  every documentation or local domain change.
- Keep this file forward-looking. Move completed chronology to `docs/archive/`.
- Keep `docs/STATUS.md` to current facts, risks and next actions.
- Update architecture only when implemented structure or the intended near-term flow changes.
- Use an ADR only for a durable, costly-to-reverse decision.

## Deferred

- automatic pruning or strategy promotion;
- sophisticated factor timing, latent regimes, covariance optimisation and machine learning;
- broker-demo or live orders;
- full workstation UI and mutating console controls;
- new infrastructure products, multi-user operation and high availability;
- compatibility layers for disposable pre-experiment schemas;
- capture storage optimisation and further operational qualification unless measured research need
  or collector risk makes them necessary.
