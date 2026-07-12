# ADR 0013: historical coverage is not live-gap repair

- **Status:** Accepted
- **Date:** 2026-07-11

## Decision

Historical backfill is a reviewed, range-scoped operation with source listing, universe,
quota and plan identity. It appends `IG_HISTORICAL` bars and records coverage separately
from quote-derived observations.

Historical data can establish coverage for a historical dataset but must never overwrite
raw input, canonical quotes, quote-derived bars or live-stream gap observations.

## Consequences

New instruments can be listing-validated and backfilled before being admitted to a live
capture universe. Backfill cannot claim to recreate missing ticks.
