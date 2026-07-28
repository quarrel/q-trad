# Current status

**Updated:** 2026-07-27
**Current milestone:** R2 — local and pooled baselines (R2.B software complete; representative integration pending)
**State:** R0 and R1 are complete; `capture-v4` is live with 23/23 channels ready, and paper research
remains offline/replay

## Working now

- The modular application captures IG demo quotes, preserves raw/canonical facts, builds one-minute
  bars, exports versioned Parquet, replays deterministically and exposes read-only health views.
- The live OCI collector runs the reviewed 23-market `capture-v4` universe at configuration hash
  `eca6649c...606078`. Activation reached 23/23 subscribed, updated and recent channels with a
  caught-up projection, queue depth zero and no observed reconnect, internal-drop,
  Lightstreamer-loss, subscription or server error.
- The collector can validate and atomically replace its mounted universe through the reviewed
  `SIGHUP` path without a second IG session. The previous immutable release remains the rollback
  point.
- The deployed application release is `08879c7`; it includes the console query fix, the
  descriptor-driven deployment orchestrator. The orchestrator verifies exact main-branch CI,
  backup/schema/rollback identity, proves the new image on the unchanged universe, activates once,
  observes readiness/loss/run evidence and automatically restores the prior release on a failed
  post-mutation gate. It also retains only the active, declared rollback and candidate application
  images, preserving the host's disk headroom.
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
- R1 now has isolated-snapshot observation build/verify commands, separate initial-availability and
  correction-maturity evidence, and a thin immutable bundle over independently manifested
  observation, configuration, availability, panel, target, fold and forecast children. Verification
  recomputes delay evidence, binds the complete observation universe and deterministically replays
  every causal child transformation without loading model code. The current implementation passed
  the full clean PostgreSQL, formatting, linting, typing and 503-test gate; R1 remains complete. No
  real native 23-market bundle or effectiveness result has been claimed from the zero-return probe or
  history.
- The R2.A contract and readiness preflight are implemented. The identity-bearing experiment keeps
  the full R1 target universe, authenticated pre-holdout target/feature eligibility decisions and a
  frozen confirmatory subset separate. Its declared cumulative feature ladder and baseline-model
  families use a pinned scikit-learn numerical decision. The offline readiness command verifies exact
  R1 identities and reports source-active coverage for each qualifying instrument and research block,
  active-source duration, and usable common weeks rather than a first/last timestamp span. It enforces
  the documented 6+2+2+2+4-week gates and configured training, outer-validation and holdout row
  minima. Inner-validation row readiness remains explicitly partial until R2.C produces its verified
  chronological split. Representative native integration also remains pending until later R2
  feature, fit, persistence, replay and evaluation evidence actually exercises it; current native
  history supports no model-selection or effectiveness claim.
- R2.B software now provides the identity-bound `qtrad-r2-features-v1` raw-feature contract.
  Materialisation authenticates the complete R1 bundle, its availability evidence and every child
  binding before using exact interval start/end and configured current-cutoff selection. It provides
  exact local return and causal N-return rolling features, activity-adjusted missingness, fixed
  leave-one-out model and market-group universes, VIX context, aligned eligibility-gated spreads and
  locked-holdout exclusion. `research baselines features` writes a bounded immutable chunked-Parquet
  child with separate semantic dataset and physical manifest identities; `features-verify` validates
  its strict wide schema, chunk and lineage hashes, then independently replays every row, value and
  lineage from the verified R1 bundle. Eligible quote imbalance fails closed until quote-size
  semantics are separately validated. This is `IMPLEMENTATION_EVIDENCE_ONLY`; representative
  integration and any model conclusion remain pending.
- The 22 non-VIX markets remain potentially tradable subject to experiment role, reviewed product
  economics, sessions, conversion and data quality. China A50 and Taiwan are now captured; the
  AUD-denominated VIX is captured context-only. Korea 200 has no eligible demo listing, and all
  reviewed Bitcoin listings were unavailable; both remain quarantined.

## Current risks and unknowns

- The replaced `capture-v2` run accumulated application-side callback drops while saturated. That
  interval is incomplete market evidence and must remain excluded or explicitly gap-qualified.
- The initial `capture-v4` observation is a deployment smoke, not representative-session evidence.
- The independent restore verification passed on 2026-07-22 at 16:57:59 UTC against
  `daily/qtrad-capture-20260722T161655Z.dump`; the manifest, checksum, migration `0010` and
  10,319,635 canonical events were verified, and the disposable target was removed from `/srv`.
- That restore was materially I/O-intensive on the shared host: the preceding ingestion run
  recorded 12,738 callback drops from 16:38:29 to 16:50:18 UTC. The run is retained as incomplete
  evidence, and the ingest service was restarted at 17:02:04 UTC; the new run is healthy at 23/23
  with zero drops. Future restore checks need an explicitly accepted low-load window.
- Native forward history is still short for model selection or an effectiveness claim.
- Bid/ask size is captured, but its availability and meaning across markets/sessions have not yet
  qualified a quote-imbalance feature. It is not evidence of executed trade volume or CVD.
- China A50 and Taiwan session/data-quality qualification remains part of the native coverage audit.
  Korea 200 remains unavailable without a future eligible listing. VIX is capture-only and must not
  become paper-tradable without a separate economics/role decision. Bitcoin needs a future review
  while its exact listing is available before it can be promoted as potentially tradable.
- R0's bounded historical-data decision is recorded in `docs/R0_DATA_READINESS.md`: no external
  source, purchase or adapter is approved yet. External history remains provenance-distinct and
  cannot substantiate native IG fills, spreads or slippage.

## Next actions

1. Continue proportionate read-only observation of `capture-v4` delivery, gaps, loss and lag.
2. As the confirmatory R2 entry gate, build and independently verify the real 23-market native
   foundation bundle when the configured evidence interval and product-role qualification support it.
3. Run R2.B materialisation and independent persisted verification against that bundle, then retain
   coverage and failure dispositions without making a model-effectiveness claim.
4. Keep native history and product-economics qualification fail-closed while R2.C is prepared.

## Evidence and current authorities

- Active milestone: `PLAN.md`
- Trading-research intent and gates: `docs/TRADING_RESEARCH.md`
- Implemented and intended system shape: `docs/ARCHITECTURE.md`
- Current capture procedure: `docs/CAPTURE_OPERATIONS_RUNBOOK.md`
- R0 native coverage and historical-source decision: `docs/R0_DATA_READINESS.md`
- China A50/Korea 200/Taiwan/VIX/Bitcoin review: `docs/archive/capture-v4/APAC_REVIEW.md`
- Verified snapshot import: `docs/RESEARCH_SNAPSHOT_RUNBOOK.md`
- First framework-proof result: `docs/archive/research-proof/FIRST_RANKING_REPORT.md`
- Superseded plans, qualification and incident evidence: `docs/archive/`

Historical records are consulted only to reconstruct an incident or decision, verify retained
evidence or handle a compatibility boundary that still affects current work.
