# ADR 0021: observed quote silence in capture qualification

- **Status:** Accepted
- **Date:** 2026-07-15

## Context

The first OCI qualification produced bounded per-instrument gap records while one Lightstreamer
generation remained connected and all subscriptions later resumed without recovery. A gap records
the absence of a recently received healthy quote; it does not establish that the transport,
subscription or collector failed. Quiet or out-of-hours pricing can legitimately produce no item
callback even though the shared connection remains usable.

The original operator-review taxonomy could label only a market closure, provider maintenance,
lifecycle event or unexplained gap. Forcing connected provider silence into one of the first three
would invent a cause, while treating every quiet interval as an unexplained collector failure would
contradict the per-channel bounded-grace readiness contract.

## Decision

- Preserve every observed gap and its raw/canonical history. Classification never repairs, deletes
  or reinterprets the gap.
- Add the pass-eligible `EXPECTED_MARKET_INACTIVITY` operator classification. It means the collector
  received no healthy quote callback during the interval while retained evidence supports normal
  market/provider inactivity rather than capture-path failure.
- Require one or more bounded evidence references for every reviewed gap. A market-inactivity
  classification requires evidence covering the same connection generation and subscription set
  before and after the interval, no disconnect/reconnect/unsubscription/drop/terminal failure,
  spontaneous same-generation quote resumption before the configured stale-reconnect threshold,
  and relevant dealing-state, cross-channel or market-session context. The finaliser binds those
  references and the operator rationale but cannot authenticate the external artifacts or replace
  operator judgement.
- Use `UNEXPLAINED` when that evidence is absent, ambiguous or contradicts continuity. A gap that
  reaches recovery, loses a subscription, coincides with dropped records or cannot be bounded by
  full-window logs is not expected market inactivity. `UNEXPLAINED` cannot pass qualification.
- Version the operator-review and final-decision contracts as
  `qtrad-capture-qualification-review-v2` and `qtrad-capture-qualification-final-v2`. The automatic
  read-only evidence remains v1 because its observed facts are unchanged.

## Consequences

Qualification can distinguish an observed data silence from a collector or transport failure
without erasing either. A passing label is more demanding than the previous free-text rationale
because each gap now carries explicit bounded evidence references. The current OCI gaps remain
pending operator review until the full candidate window, logs and monitoring history are available;
this ADR does not pre-classify them or authorise qualification.
