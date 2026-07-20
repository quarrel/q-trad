# Research-framework paper acceptance scenarios

These scenarios are the correctness contract for the framework proof. Implement the smallest set
needed by the active milestone; longer operational evidence is not a prerequisite for causal domain
and evaluator work.

## Strategy population and evaluation

### PS-E01 — common eligible observations

Given several applicable strategies in one run, when an eligible completed bar arrives, then every
strategy receives the same ordered observation and records a versioned forecast or explicit
no-forecast outcome.

### PS-E02 — causal outcome pairing

Given a forecast with a declared horizon, when its outcome becomes eligible, then evaluation uses
only later observations, records exclusions for unhealthy intervals and cannot change the earlier
forecast or selection.

### PS-E03 — deterministic ranking

Given a pinned dataset, score definition and strategy population, when evaluation is replayed, then
forecast coverage, scores and rank order are identical. Unselected strategies remain in the result.

### PS-E04 — market-state timing

Given a strategy ranking conditioned on market state, when the state is evaluated, then it uses only
features available at the selection time and compares against an unconditional baseline. `UNKNOWN`
state cannot be coerced into a favourable label.

## Causal allocated path

### PS-A01 — complete allocated trace

Given 60 complete eligible one-minute bars and healthy product/session metadata, when the
allocated moving-average signal changes target, then canonical events record market state,
signal intent, sleeve target, allocation, risk decision, execution instruction, paper
order, subsequent-quote fill, virtual position and P&L with one correlation chain.

### PS-A02 — warm-up isolation

Given historical warm-up bars, when indicator state becomes ready, then no execution
instruction, order or fill is emitted until an eligible non-warm-up bar arrives.

### PS-A03 — risk clipping

Given a requested target above its instrument or portfolio cap, when risk evaluates it,
then the approved quantity is clipped deterministically and the original quantity, limit,
unit and reason code remain attributable.

## Shadow isolation

### PS-S01 — hypothetical path only

Given a breakout intent from the shadow sleeve, when its paper path is evaluated, then its
isolated ledger may contain hypothetical orders, fills and P&L while deployable allocation
and allocated exposure remain exactly zero.

### PS-S02 — no indirect influence

Given any shadow result, health or opposing target, when allocated targets and risk are
recomputed, then allocated quantities are identical to a run in which the shadow sleeve is
absent.

## Fill evidence and safety

### PS-F01 — causal executable side

Given an approved buy or sell, when a healthy quote arrives after model latency, then a buy
uses ask plus adverse slippage and a sell uses bid minus adverse slippage; the triggering
quote and model version are recorded.

### PS-F02 — insufficient evidence

Given a stale, partial, crossed, closed-session or pre-instruction quote, when the paper
engine evaluates an order, then it emits no fill and records a bounded denial/wait reason.

### PS-F03 — unsupported economics

Given missing or ambiguous quantity step, value-per-price-unit, conversion path or price
increment, when allocation or risk evaluates the instrument, then deployable allocation is
zero and no order is created.

## Accounting and determinism

### PS-L01 — weighted-average lifecycle

Given additions, partial reductions, closure and reversal, when fills are applied, then
quantity, weighted-average cost, realised/unrealised gross P&L, costs and AUD net P&L equal
an independent `Decimal` calculation.

### PS-L02 — conversion provenance

Given non-AUD native P&L, when it is valued, then the result records the complete healthy
FX conversion path and valuation times; missing or stale conversion produces unavailable
P&L rather than zero.

### PS-D01 — duplicate and restart safety

Given duplicate market input or process restart after any canonical step, when processing
resumes, then no duplicate intent, order, fill, position change or P&L entry is created.

### PS-D02 — fixed replay

Given a pinned dataset, code/configuration version and clock, when replay runs twice, then
event sequence, decisions, fills, ledgers, P&L and semantic hash are identical.

### PS-D03 — projection rebuild

Given deleted paper projections, when all canonical events are replayed, then positions,
orders, fills, allocation, alerts and P&L exactly match their pre-delete semantic hashes.

## Operator and operational evidence

### PS-O01 — causal drill-down

Given allocated and shadow activity, when the operator opens the console, then portfolio,
sleeve, signal strategy, instrument, order, fill and P&L views expose correlation,
freshness, mode, quality and model versions without ambiguous “trade” terminology.

### PS-O02 — visible safety states

Given stale data, projection lag, risk denial or a halted run, when console data refreshes,
then the state is visibly distinct from healthy PAPER and SHADOW operation.

### PS-O03 — representative live-paper observation

Given a frozen framework candidate, when live IG demo data drives selected and shadow paper paths
through a representative active interval and deliberate restart, then no duplicate facts occur,
gaps/drops/lag are visible, ledgers reconcile and final replay is deterministic. Require a multi-day
soak only when unattended endurance is the experiment under review.
