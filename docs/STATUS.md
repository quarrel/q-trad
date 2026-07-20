# Current status

**Updated:** 2026-07-20
**Current milestone:** M1 — reviewed 20-market universe
**State:** 19-market release prepared locally; publication and collector mutation not authorised

## Working now

- The modular application captures IG demo quotes, preserves raw/canonical facts, builds one-minute
  bars, exports versioned Parquet, replays deterministically and exposes read-only health views.
- The original seven instruments have validated mappings and have passed bounded live capture,
  reconnect and endurance work.
- The operator reports that the OCI `capture-v1` collector is currently ingesting those seven
  instruments effectively. A bounded read-only observation on 2026-07-20 independently found the
  pinned `6cd9615` release and `sha256:72ad6290...` image ready on all seven channels, one connected
  stream generation, zero internal/SDK loss, queue depth 0 with high-water 15, and caught-up core
  projection. The 100 GB data volume was 13% used.
- Queue-loss and processing-watermark defects found during qualification were corrected. Historical
  failed evidence remains archived rather than presented as a pass.
- The bounded IG demo review completed without disturbing the live collector. The hash-bound
  `config/capture-v2.toml` release selects 19 reviewed rolling, tradeable listings and explicitly
  quarantines Bitcoin because both search and direct lookup found it unavailable. Japan 225 uses
  the reviewed AUD contract `IX.D.NIKKEI.IFA.IP`; the provider review returned current USD rolling
  spot Gold/Silver listings and `CC.D.CL.UNC.IP` as Oil - US Crude. Configuration hash:
  `20c3e99257a7c8e554d971b76948291f382c8f0c84a116d409996e8fcc07ea84`.
- The local research core now has versioned selected/shadow forecasts, three simple strategy
  variants plus a no-signal benchmark, exact-horizon causal outcomes, time-series Spearman Rank IC,
  later healthy bid/ask fills, explicit latency/slippage, isolated reconciling ledgers and a
  deterministic hash-bound ranking report.
- The first retained report uses a verified `capture-v1` snapshot, 714 quote-derived bars and 5,070
  canonical quotes for Australia 200. Replay was byte-identical. Reversal ranked first by Rank IC,
  but all active strategies lost after costs under every sensitivity; this is a framework proof,
  not an effectiveness claim. Evidence is in
  `docs/archive/research-proof/FIRST_RANKING_REPORT.md`.
- The latest daily collector backup succeeded, but the latest weekly restore verification reported
  failure. No cloud or collector mutation was attempted.
- A current 40-instrument, 200-callback/s disposable-PostgreSQL run persisted all 12,000 callbacks
  with zero loss, queue high-water 51/10,000, 5.3 ms p95 and 257.6 ms maximum lag, complete-state
  renewal and clean termination. This provides local headroom beyond the target size; real candidate
  subscription and post-deployment delivery evidence remain required.
- The release boundary gate passes formatting, Ruff, strict typing and 374 tests; 13 credential-
  gated PostgreSQL integration tests were skipped by the local environment.

## Current risks

- Post-deployment delivery still needs to prove 19/19 per-channel readiness, visible gaps, zero loss
  and bounded lag on the real provider stream.
- IG began rejecting additional short-lived login sessions after the completed review and release
  validation. The live collector remained healthy on its original generation, but do not retry
  provider experiments until login access has recovered.
- Restore verification is currently failed even though the latest backup succeeded.
- A short forward capture cannot support a strategy-effectiveness conclusion.
- Existing paper documents contain more hierarchy than the first framework proof may need.
- Publication, backup and a controlled collector restart still require explicit approval.

## Next actions

1. Review and approve the exact 19-market mapping/configuration and Bitcoin quarantine.
2. Publish an immutable application image containing the reviewed changes and pin its digest.
3. Take the required pre-deployment backup, deploy `config/capture-v2.toml` in one controlled
   restart, and retain the existing image/configuration as the rollback target.
4. Observe 19/19 per-channel delivery, loss and lag immediately after deployment; roll back on
   failed readiness or evidence loss.
5. Repair and re-run weekly restore verification independently of universe deployment.
6. Use longer locked time-ordered data before making any strategy-effectiveness claim; do not let
   that follow-on evaluation delay M1 universe expansion.

## Historical records

- Data-foundation plan, status, architecture and original product preplan:
  `docs/archive/data-foundation/`
- Initial soak, capture qualification, streaming investigation and storage audit:
  `docs/archive/capture-v1/`
- Accepted durable decisions: `docs/adr/`
- Current operational procedure: `docs/CAPTURE_OPERATIONS_RUNBOOK.md`

Historical records are consulted only for incident reconstruction, evidence verification or a
decision they still govern. They are not routine agent context.
