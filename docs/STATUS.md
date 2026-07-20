# Current status

**Updated:** 2026-07-20
**Current milestone:** M1 — reviewed 20-market universe
**State:** planning and review; no collector mutation authorised

## Working now

- The modular application captures IG demo quotes, preserves raw/canonical facts, builds one-minute
  bars, exports versioned Parquet, replays deterministically and exposes read-only health views.
- The original seven instruments have validated mappings and have passed bounded live capture,
  reconnect and endurance work.
- The operator reports that the OCI `capture-v1` collector is currently ingesting those seven
  instruments effectively. This documentation turn did not independently inspect it.
- Queue-loss and processing-watermark defects found during qualification were corrected. Historical
  failed evidence remains archived rather than presented as a pass.
- The proposed 20-market catalogue already exists at `config/capture-v2-candidates.toml`; it has no
  provider epics and cannot authorise ingestion.
- Paper timing, fills, fixed allocation, accounting and checkpoint decisions exist. The actual
  multi-strategy evaluator and ranking loop does not.

## Current risks

- Expanding the universe could overload the callback/persistence path or expose ambiguous listings.
- A short forward capture cannot support a strategy-effectiveness conclusion.
- Rank IC and outcome contracts are not yet defined precisely enough to compare strategies.
- Existing paper documents contain more hierarchy than the first framework proof may need.
- The running collector is evidence-bearing state; an unplanned restart or deployment could
  invalidate an active observation interval.

## Next actions

1. Read-only confirm the collector's current identity, measurement state, readiness, loss and queue
   evidence before planning any stop or deployment.
2. Run the bounded IG demo review for the 20-market candidate catalogue without changing capture.
3. Produce an operator-reviewed accepted/quarantined mapping set with product economics.
4. Demonstrate single-connection and persistence headroom for the accepted size.
5. Prepare the explicit release/deployment proposal for approval.
6. In parallel, specify the minimal common forecast and realised-outcome contracts for M2/M3.

## Historical records

- Data-foundation plan, status, architecture and original product preplan:
  `docs/archive/data-foundation/`
- Initial soak, capture qualification, streaming investigation and storage audit:
  `docs/archive/capture-v1/`
- Accepted durable decisions: `docs/adr/`
- Current operational procedure: `docs/CAPTURE_OPERATIONS_RUNBOOK.md`

Historical records are consulted only for incident reconstruction, evidence verification or a
decision they still govern. They are not routine agent context.
