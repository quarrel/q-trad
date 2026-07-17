# ADR 0013: historical coverage is not live-gap repair

- **Status:** Accepted
- **Date:** 2026-07-11

## Decision

Historical backfill is a reviewed, range-scoped operation with source listing, universe,
quota and plan identity. It appends `IG_HISTORICAL` bars and records coverage separately
from quote-derived observations.

The workflow has three explicit stages:

1. `plan` resolves exactly one already validated IG demo listing for each requested
   instrument, and requires that listing to match the selected universe's explicit epic. It
   records the exact UTC `[start, end)` range, one-minute resolution, listing effective
   version, universe hash, operator-observed allowance, 20% reserve and request chunk size
   in canonical JSON. The SHA-256 of that content is the plan identity. Planning makes no
   provider request and cannot overwrite an existing plan file.
2. `register` strictly decodes and rehashes the reviewed file. It requires the operator to
   repeat the exact hash, then atomically persists the immutable plan and one open coverage
   attempt for each instrument and BID/ASK/MID basis. Registration does not contact IG.
3. `execute` accepts only the hash of a registered `PLANNED` or deliberately retried
   `FAILED` plan. The database atomically claims it before credentials are read or IG is
   contacted. Execution uses the persisted listing effective version and exact range; it
   cannot rediscover, substitute or widen either. Completion requires provider observations
   for every planned basis and closes only that plan's coverage attempts.

Coverage identity includes instrument, provider listing and effective version, provenance,
basis, resolution, interval and detecting plan. This final plan discriminator preserves
separate evidence when a later reviewed plan intentionally revisits an already covered
range. Identical returned bars remain idempotent. Changed provider bars append a canonical
`MarketBarCorrected` revision rather than changing an earlier event or projection row.

Historical data can establish coverage for a historical dataset but must never overwrite
raw input, canonical quotes, quote-derived bars or live-stream gap observations.

Listing validation for a non-streaming universe is explicit: `instruments sync --universe PATH`
loads that reviewed, epic-pinned universe for only the validation command. It does not change the
runtime capture-universe setting or start ingestion. Run it against the same isolated writable
database that will receive the planned historical coverage; never use candidate validation to
replace effective listings underneath the persistent collector.

## Consequences

New instruments can be listing-validated and backfilled before being admitted to a live
capture universe. Backfill cannot claim to recreate missing ticks.

`read_model.historical_coverage_gaps` and the bounded read-only
`/api/v1/historical-coverage` resource are therefore deliberately separate from
`read_model.data_gaps` and `/api/v1/gaps`. A covered historical range means the reviewed
provider request returned at least one point for each basis; it is not evidence that every
market minute traded or that an observed stream interruption was repaired.
Completed provider requests and their quota usage are recorded separately. A request that returns
zero points remains useful diagnostic evidence, but leaves the historical coverage gap open.
