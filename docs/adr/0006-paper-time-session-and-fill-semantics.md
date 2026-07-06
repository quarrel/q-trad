# ADR 0006: paper time, session and fill semantics

- **Status:** Accepted
- **Date:** 2026-07-06

## Context

IG top-of-book observations have provider event time and q-trad receive time but no
exchange queue or consolidated trade tape. Provider clock skew can also make event time
later than receive time. A paper model must therefore be causal and conservative without
claiming exchange-level fidelity.

## Decision

Strategies consume only completed one-minute q-trad-derived bars. Midpoint bars drive the
toy signals; bid and ask observations remain the executable evidence. Warm-up updates
indicator state but cannot create an execution instruction.

Versioned session profiles classify observations as underlying cash session, extended
session, closed or unknown. The initial index profiles use their underlying ASX, London
and New York cash sessions in venue time; FX uses a versioned weekday/rollover profile.
Holiday exceptions are explicit data. Unknown, closed, stale or incomplete state denies
new exposure.

Paper orders are evaluated in canonical global-position order using receive time for
causality. A fill requires a subsequent healthy quote after the configured latency:

- a buy uses the ask and a sell uses the bid;
- adverse slippage is a configured number of price increments;
- spread is embodied in the executable side;
- v1 orders fill completely or not at all, because the feed does not justify partial-fill
  or queue-position rules;
- unsupported order features and missing price-increment or session metadata are denied;
- provider event time is retained for provenance but is not interpreted as measured
  network latency.

The first execution algorithm issues at most one immediate paper order per instrument and
cooldown window. Paper execution supports no external submission.

## Consequences

Historical replay must preserve receive/global-position ordering as well as event time.
Fill events record the quote/event evidence, session-profile version, latency, slippage and
model version. Sensitivity tests vary delay and slippage; a midpoint-only profit is never
reported as executable.
