# Current status

**Updated:** 2026-07-22
**Current milestone:** M4 — simple market-state comparison
**State:** `capture-v3` deployed; 20/20 channels ready with Bitcoin and VIX quarantined

## Working now

- The modular application captures IG demo quotes, preserves raw/canonical facts, builds one-minute
  bars, exports versioned Parquet, replays deterministically and exposes read-only health views.
- The original seven instruments have validated mappings and have passed bounded live capture,
  reconnect and endurance work.
- The OCI collector runs merged commit `f317c1ac0b47586782ac9047bab3bb18f31287ff`
  from immutable OCI index digest `sha256:9d46c139fc58b580bff8ffeb53b6ed00e4ee3dd8a91c8d40694d96390ce1edba`
  with `config/capture-v3.toml`. Its deployment observation reached HTTP 200 readiness, 20/20
  subscribed, updated and recent channels, caught-up projection, queue depth 0 with high-water 20,
  and zero reconnects, internal drops, Lightstreamer loss, subscription errors or server errors.
  The prior `ab86b9de` release and `sha256:5849acc0...` image remain the immediate rollback point.
- Queue-loss and processing-watermark defects found during qualification were corrected. Historical
  failed evidence remains archived rather than presented as a pass.
- `capture-v3` adds the reviewed rolling, tradeable HKD mini Hang Seng listing
  `IX.D.HANGSENG.IFM.IP`. Bitcoin remains isolated and no eligible VIX Web API listing was found, so
  neither is guessed into capture. WTI uses the approved `CC.D.LCO.USS.IP` mapping. Configuration
  hash: `50202ef7218f1d9816ebc88673259ecb5470f9360abe6b40f1f730c06d712836`.
- The collector reads an atomically replaceable mounted universe file. `SIGHUP` validates and
  synchronises approved listings before replacing subscriptions and starts a new evidence run; an
  unchanged-file signal was proved live without reconnecting or losing readiness.
- The local research core now has versioned selected/shadow forecasts, three simple strategy
  variants plus a no-signal benchmark, exact-horizon causal outcomes, time-series Spearman Rank IC,
  later healthy bid/ask fills, explicit latency/slippage, isolated reconciling ledgers and a
  deterministic hash-bound ranking report.
- The first retained report uses a verified `capture-v1` snapshot, 714 quote-derived bars and 5,070
  canonical quotes for Australia 200. Replay was byte-identical. Reversal ranked first by Rank IC,
  but all active strategies lost after costs under every sensitivity; this is a framework proof,
  not an effectiveness claim. Evidence is in
  `docs/archive/research-proof/FIRST_RANKING_REPORT.md`.
- A pre-deployment backup completed successfully before the 2026-07-22 cutover. The latest weekly
  restore verification remains failed and is a separate operational repair.
- A current 40-instrument, 200-callback/s disposable-PostgreSQL run persisted all 12,000 callbacks
  with zero loss, queue high-water 51/10,000, 5.3 ms p95 and 257.6 ms maximum lag, complete-state
  renewal and clean termination. This provides local headroom beyond the target size; the initial
  real-provider 19/19 delivery smoke passed, while longer observation remains required.
- The `capture-v3` release boundary passed the complete clean PostgreSQL, formatting, Ruff, strict
  typing and test gate in GitHub Actions. Post-deployment ingest used about 11% CPU and 113 MiB after
  the queue-drain correction, versus about 95% CPU and 1.95 GiB immediately before replacement.

## Current risks

- The replaced `capture-v2` run accumulated 451,041 application-side callback drops while its
  queue was saturated. That interval is not complete market evidence and must be excluded or
  explicitly gap-qualified in research datasets.
- The initial 20/20 observation is a deployment smoke, not representative-session evidence;
  continue observing gaps, loss and lag without protecting an arbitrary interval.
- The single-instrument operator endpoint currently returns HTTP 500 because PostgreSQL cannot
  infer the nullable bar-filter parameter type. Capture is unaffected; a narrow typed-SQL fix is in
  progress.
- Restore verification is currently failed even though the latest backup succeeded.
- A short forward capture cannot support a strategy-effectiveness conclusion.
- Existing paper documents contain more hierarchy than the first framework proof may need.

## Next actions

1. Continue proportionate read-only observation of 20-market delivery, gaps, loss and lag.
2. Repair and re-run weekly restore verification independently of universe deployment.
3. Merge and deploy the typed nullable bar-filter repair for the operator endpoint.
4. Add the first transparent contemporaneous market-state annotation and compare conditional with
   unconditional strategy scores in locked time order.
5. Use longer locked time-ordered data before making any strategy-effectiveness claim.

## Historical records

- Data-foundation plan, status, architecture and original product preplan:
  `docs/archive/data-foundation/`
- Initial soak, capture qualification, streaming investigation and storage audit:
  `docs/archive/capture-v1/`
- Accepted durable decisions: `docs/adr/`
- Current operational procedure: `docs/CAPTURE_OPERATIONS_RUNBOOK.md`

Historical records are consulted only for incident reconstruction, evidence verification or a
decision they still govern. They are not routine agent context.
