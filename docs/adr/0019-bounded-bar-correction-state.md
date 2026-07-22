# ADR 0019: bounded live bar-correction state

- **Status:** Accepted
- **Date:** 2026-07-22

## Context

The live one-minute builder retained every closed interval and sorted the complete history on every
quote. At 19 markets this grew ingestion to one full CPU, about 2 GiB of memory and a permanently
full input queue after roughly 17 hours, causing application-side data loss. This invalidates the
unbounded in-memory interpretation of ADR 0004.

## Decision

Keep open intervals plus one hour of closed intervals for live corrections. Watermark advancement
examines only open intervals and removes closed state after that correction window. A quote arriving
after its interval has expired remains in raw and canonical history, emits a
`BAR_CORRECTION_WINDOW_EXPIRED` data-gap fact, and does not revise the live bar. Stream versions are
cached only after a successful append; PostgreSQL remains the restart authority.

Replay uses the same deterministic transport-time watermark and correction window. A future
experiment that needs a longer correction horizon must rebuild from canonical events under a new,
versioned bar convention rather than retaining unbounded live process state.

## Consequences

Live memory and watermark work are bounded by recent activity rather than collector age. Extremely
late provider timestamps are explicit exclusions instead of silently creating revisions from
unbounded process memory. Raw and canonical evidence remains unchanged.
