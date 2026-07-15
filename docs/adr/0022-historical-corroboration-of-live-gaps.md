# ADR 0022: historical corroboration of observed live gaps

- **Status:** Accepted
- **Date:** 2026-07-15

## Context

ADR 0021 requires bounded evidence before an operator can classify a live qualification gap. IG's
demo historical endpoint may later contain one-minute bars for an interval in which the streaming
collector received no healthy quote callback. That distinction is operationally useful, but the
historical and streaming endpoints are separate provider paths: presence or absence in one cannot
prove what the other emitted.

Running an ad hoc query would also lose listing, range, quota and returned-data identity. Writing the
result into the collector would conflate historical coverage with immutable observed live capture.

## Decision

- Investigate qualification gaps only after the frozen candidate window and automatic evidence
  snapshot. Use the existing reviewed IG demo backfill plan/register/execute path in a new writable
  database imported from a verified collector snapshot that postdates the automatic evidence.
- One plan must cover every investigated gap's instrument and minute-aligned UTC interval, bind the
  exact effective listing versions and retain quota evidence. It never targets or repairs the
  collector database or `read_model.data_gaps`. `qualification gap-plan` derives that range and
  instrument set from the self-hashed automatic evidence, requires a verified post-evidence snapshot
  import into the configured `qtrad_research_*` database and proves the database is at the repository's
  single current Alembic head before reading listings.
- Export the exact plan interval through the version-two research manifest. An offline
  `qualification gap-history` command verifies the automatic evidence self-hash, configuration and
  capture source; the plan and completed BID/ASK/MID coverage; the snapshot import; the research
  manifest and Parquet hashes; and an exact copy of every live gap.
- Emit one bounded, non-overwriting, self-hashed artifact containing per-gap and per-basis returned
  point counts, completeness and semantic bar hashes. The only result labels are
  `HISTORICAL_DATA_PRESENT` and `NO_HISTORICAL_DATA_RETURNED`.
- Treat the artifact as corroborating evidence only. Historical presence warrants deeper
  stream-path investigation; absence is consistent with upstream inactivity. Neither result
  classifies, repairs, deletes or reinterprets a live gap and neither can replace ADR 0021's
  continuity, full-window log and monitoring evidence.

## Consequences

The operator can answer whether IG later supplied historical bars for each silence without trusting
stdout, mutating capture history or overclaiming causality. The investigation consumes historical
quota and therefore remains an explicit reviewed post-window operation. A missing basis, stale or
unverified snapshot, listing drift, incomplete plan evidence, changed gap, tampered manifest or
existing output fails closed.
