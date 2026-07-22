# Current status

**Updated:** 2026-07-22
**Current milestone:** R0 — alignment, coverage and data readiness
**State:** `capture-v3` is live with 20/20 channels ready; paper research remains offline/replay

## Working now

- The modular application captures IG demo quotes, preserves raw/canonical facts, builds one-minute
  bars, exports versioned Parquet, replays deterministically and exposes read-only health views.
- The live OCI collector runs the reviewed 20-market `capture-v3` universe. Its initial observation
  reached 20/20 subscribed, updated and recent channels with caught-up projection, queue depth zero
  and no observed reconnect, internal-drop, Lightstreamer-loss, subscription or server error.
- The collector can validate and atomically replace its mounted universe through the reviewed
  `SIGHUP` path without a second IG session. The previous immutable release remains the rollback
  point.
- The current callback-to-PostgreSQL path passed a 40-instrument, 200-callback/s bounded local run
  with zero loss and sub-second maximum lag, so China/Korea review does not require another general
  capacity programme.
- A retained framework proof uses a verified `capture-v1` snapshot for Australia 200. It produced
  deterministic single-horizon forecasts/outcomes, causal bid/ask paper fills, isolated reconciling
  ledgers and a hash-bound ranking report. Every active strategy lost after costs under the tested
  sensitivities. This proves the old research path, not forecast effectiveness.
- The active programme now targets multi-horizon local and cross-asset return forecasts followed by
  explicit cost/risk states and constrained paper portfolio construction. The first full path will
  be offline and chronological; continuous shadow paper follows only after the offline MVP.
- All current 20 markets remain potentially tradable subject to experiment role, reviewed product
  economics, sessions, conversion and data quality. China A50 and Korea 200 are the only immediate
  APAC capture candidates and have no approved mapping or deployment yet.

## Current risks and unknowns

- The replaced `capture-v2` run accumulated application-side callback drops while saturated. That
  interval is incomplete market evidence and must remain excluded or explicitly gap-qualified.
- The initial `capture-v3` observation is a deployment smoke, not representative-session evidence.
- Weekly restore verification remains failed even though the latest backup succeeded.
- Native forward history is still short for model selection or an effectiveness claim.
- Bid/ask size is captured, but its availability and meaning across markets/sessions have not yet
  qualified a quote-imbalance feature. It is not evidence of executed trade volume or CVD.
- Exact China A50 and Korea 200 IG demo listings, sessions and product economics are unresolved.
- The historical-data source and licence decision is open. External history will be
  provenance-distinct and cannot substantiate native IG fills, spreads or slippage.

## Next actions

1. Finish the R0 documentation/archive reset and retain the old ranking proof as historical evidence.
2. Review China A50 and Korea 200 through the existing non-authoritative catalogue and fail-closed
   selection flow; prepare `capture-v4` only for accepted listings.
3. Continue proportionate read-only observation of `capture-v3` delivery, gaps, loss and lag while
   the review proceeds.
4. Audit quote-size, session, gap, revision and aligned-bar coverage for the R1 dataset contract.
5. Combine the user's historical-source work with bounded IG-candle/external-sample, licence and
   provenance review.
6. Repair and re-run weekly restore verification independently of universe deployment.

## Evidence and current authorities

- Active milestone: `PLAN.md`
- Trading-research intent and gates: `docs/TRADING_RESEARCH.md`
- Implemented and intended system shape: `docs/ARCHITECTURE.md`
- Current capture procedure: `docs/CAPTURE_OPERATIONS_RUNBOOK.md`
- Verified snapshot import: `docs/RESEARCH_SNAPSHOT_RUNBOOK.md`
- First framework-proof result: `docs/archive/research-proof/FIRST_RANKING_REPORT.md`
- Superseded plans, qualification and incident evidence: `docs/archive/`

Historical records are consulted only to reconstruct an incident or decision, verify retained
evidence or handle a compatibility boundary that still affects current work.
