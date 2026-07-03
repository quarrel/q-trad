# ADR 0004: one-minute bar convention

- **Status:** Accepted
- **Date:** 2026-07-02

## Decision

Build UTC `[start, end)` one-minute bid, ask and midpoint OHLC bars. Midpoint samples require bid and ask no more than five seconds apart. Close after a five-second lateness watermark and represent later changes as revisions.

## Consequences

Missing intervals remain gaps. Historical provider bars carry distinct provenance and never masquerade as reconstructed quote updates.
