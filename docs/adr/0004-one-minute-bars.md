# ADR 0004: one-minute bar convention

- **Status:** Accepted
- **Date:** 2026-07-02

ADR 0019 bounds the live correction-state retention implied by this decision.

## Decision

Build UTC `[start, end)` one-minute bid, ask and midpoint OHLC bars. Midpoint samples require bid
and ask no more than five seconds apart. Close after a five-second lateness watermark and represent
later changes as revisions. Advance the watermark from transport receive-time progress, not the
processing wall clock: compute or database backlog must not manufacture late data from records that
were received in order.

## Consequences

Missing intervals remain gaps. Historical provider bars carry distinct provenance and never
masquerade as reconstructed quote updates. Processing delayed records against wall time can create a
self-amplifying correction storm and is therefore outside this convention.
