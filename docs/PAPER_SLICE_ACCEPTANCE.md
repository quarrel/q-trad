# Paper vertical-slice acceptance scenarios

These scenarios are the executable contract for the next phase. Each identifier must map
to automated tests and, where stated, operational evidence before its work package is
`DONE`.

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

### PS-O03 — multi-day paper soak

Given the accepted frozen candidate, when live IG demo data drives allocated and shadow
paper paths for multiple active sessions and a deliberate restart, then all instruments
remain observable, no duplicate facts occur, gaps/drops/lag are reviewed, ledgers
reconcile and final replay is deterministic.
