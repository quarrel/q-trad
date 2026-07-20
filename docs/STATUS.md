# Current status

**Updated:** 2026-07-20
**Current milestone:** M4 — simple market-state comparison
**State:** `capture-v2` deployed; 19/19 channels initially ready with Bitcoin quarantined

## Working now

- The modular application captures IG demo quotes, preserves raw/canonical facts, builds one-minute
  bars, exports versioned Parquet, replays deterministically and exposes read-only health views.
- The original seven instruments have validated mappings and have passed bounded live capture,
  reconnect and endurance work.
- The OCI collector now runs merged commit `ab86b9de330b78b21a5809f0613b34c28202be73`
  from immutable OCI index digest `sha256:5849acc0...` with `config/capture-v2.toml`. Its initial
  active-market observation reached HTTP 200 readiness, 19/19 subscribed, updated and current quote
  channels, a caught-up core projection, queue depth 0 with high-water 26, and zero reconnects,
  internal drops, Lightstreamer loss, subscription errors or server errors. The previous `6cd9615`
  release, `sha256:72ad6290...` image and `capture-v1` environment remain the rollback point.
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
- A pre-deployment backup completed and uploaded successfully at 2026-07-20T12:03:47Z. The latest
  weekly restore verification remains failed and is a separate operational repair.
- A current 40-instrument, 200-callback/s disposable-PostgreSQL run persisted all 12,000 callbacks
  with zero loss, queue high-water 51/10,000, 5.3 ms p95 and 257.6 ms maximum lag, complete-state
  renewal and clean termination. This provides local headroom beyond the target size; the initial
  real-provider 19/19 delivery smoke passed, while longer observation remains required.
- The release boundary gate passes formatting, Ruff, strict typing and 374 tests; 13 credential-
  gated PostgreSQL integration tests were skipped by the local environment.

## Current risks

- The initial 19/19 observation is a deployment smoke, not endurance or representative-session
  evidence; continue observing gaps, loss and lag without protecting an arbitrary interval.
- Restore verification is currently failed even though the latest backup succeeded.
- A short forward capture cannot support a strategy-effectiveness conclusion.
- Existing paper documents contain more hierarchy than the first framework proof may need.

## Next actions

1. Continue proportionate read-only observation of 19-market delivery, gaps, loss and lag.
2. Repair and re-run weekly restore verification independently of universe deployment.
3. Add the first transparent contemporaneous market-state annotation and compare conditional with
   unconditional strategy scores in locked time order.
4. Use longer locked time-ordered data before making any strategy-effectiveness claim.

## Historical records

- Data-foundation plan, status, architecture and original product preplan:
  `docs/archive/data-foundation/`
- Initial soak, capture qualification, streaming investigation and storage audit:
  `docs/archive/capture-v1/`
- Accepted durable decisions: `docs/adr/`
- Current operational procedure: `docs/CAPTURE_OPERATIONS_RUNBOOK.md`

Historical records are consulted only for incident reconstruction, evidence verification or a
decision they still govern. They are not routine agent context.
