# ADR 0005: no-live-order paper vertical slice

- **Status:** Accepted
- **Date:** 2026-07-06

## Context

The data-foundation phase proves the canonical market-data path. The next phase must
exercise strategy, allocation, risk, paper execution and attribution contracts without
creating an external order route or making a profitability claim.

## Decision

After the data-foundation soak passes, extend the modular monolith with one deterministic
paper vertical slice:

> canonical market data → market-state observation → signal intent → sleeve target →
> allocation decision → risk decision → execution instruction → paper order and fill →
> virtual position and P&L → operator-console drill-down.

Use one fixed-budget allocated sleeve and one isolated shadow sleeve. Market state may
annotate decisions or scale risk but may not switch signal strategies. Persist accepted
facts as immutable canonical events and rebuild operational views from them. Historical
replay, live-data paper operation and restart recovery use the same domain contracts with
explicit clocks and data modes.

No IG order operation, broker-order port, live endpoint, automatic capital promotion,
IBKR integration or profitability claim belongs in this phase.

## Consequences

Accounting, allocation-budget, session and paper-fill semantics must be accepted before
their domain behaviour is implemented. Shadow activity has its own paper ledger and zero
deployable allocation. A later broker-order phase requires a separate ADR and readiness
gate; passing paper acceptance does not authorise external submission.
