# Current status

**Updated:** 2026-07-29
**Current milestone:** R2 — local and pooled baselines (R2.C software complete; R2.D next)
**Parallel track:** IBKR Stage 1 local preflight complete; account-gated capability probe pending
**State:** R0 and R1 are complete; `capture-v4` is live with 23/23 channels ready, and paper research
remains offline/replay.

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
  minima. Inner-validation row readiness remains explicitly partial until a verified R2.C selection
  from a qualifying source-specific foundation is supplied to readiness reporting. Representative
  native integration also remains pending until later R2 feature, fit, persistence, replay and
  evaluation evidence actually exercises it; current native history supports no model-selection or
  effectiveness claim.
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
- R2.C software now provides authenticated fold-local preprocessing and primary-horizon local-Ridge
  alpha-selection evidence. It derives exact inner-fit, inner-validation and purged membership from the
  verified R1 fold and mature target availability; authenticates the declared local R2.B feature set;
  and binds a separate `qtrad-r2-preprocessing-schema-v1` without changing any R2.B v1 identity.
  Continuous features use training-only median imputation and standardisation while binary state
  indicators remain unscaled. Candidate evaluation executes the declared Ridge policy, retains
  numerical failures and chooses the larger alpha on equal loss. The strict
  `qtrad-r2-preprocessing-selection-v1` artefact binds model, horizon, evidence, application and
  numerical-library identities; verification independently rebuilds its structural state exactly and
  numerical state within configured tolerances. This remains `IMPLEMENTATION_EVIDENCE_ONLY`: R2.C v1
  is deliberately limited to one eligible target, `LOCAL_RIDGE` and the primary horizon; final local
  coefficients and forecasts begin in R2.D, and pooled selection belongs to R2.E.
- ADR 0028 and `docs/IBKR_CAPTURE_IMPLEMENTATION_PLAN.md` define an independent, market-data-only
  IBKR paper source and its complete staged path. The local Stage 1 boundary now includes the canonical
  20-concept candidate catalogue, validated non-secret Gateway endpoint/client configuration and a
  deterministic `instruments review --provider ibkr --environment paper --preflight` artefact. It
  performs no external I/O and reports `OPERATOR_AUTHENTICATION_REQUIRED`. No IBKR adapter, exact
  contract or entitlement result, historical dataset, live collector, host or research result has
  been implemented or qualified.
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
- R0's 2026-07-22 bounded historical-data decision remains retained evidence. ADR 0028 subsequently
  approved an independently governed IBKR paper-market-data track, but its exact contracts, account
  entitlements, timestamp/session semantics and historical availability remain unqualified. Any future
  IBKR history stays provenance-distinct and cannot substantiate native IG fills, spreads or slippage.

## Next actions

1. Complete the bounded IBKR exact-contract, entitlement and capability probe after selecting the
   official direct-API installation and operator-authenticated Gateway approach; do not ingest or infer
   mappings.
2. Continue R2.D local Ridge fitting and chronological forecast evidence while the independent IBKR
   data track proceeds and native IG history accumulates.
3. Continue proportionate read-only observation of `capture-v4` delivery, gaps, loss and lag.
4. Build and independently verify each source-specific foundation only when its configured evidence,
   availability and product-role gates pass; do not weaken durations or combine sources.
5. Run R2.B, R2.C and later R2 integration/verification against representative and qualifying bundles
   with explicit `IMPLEMENTATION_EVIDENCE_ONLY`, insufficient-history or source-limited dispositions.

## Evidence and current authorities

- Active milestone: `PLAN.md`
- Trading-research intent and gates: `docs/TRADING_RESEARCH.md`
- Implemented and intended system shape: `docs/ARCHITECTURE.md`
- Current capture procedure: `docs/CAPTURE_OPERATIONS_RUNBOOK.md`
- R0 native coverage and retained historical-source decision: `docs/R0_DATA_READINESS.md`
- IBKR normative implementation plan: `docs/IBKR_CAPTURE_IMPLEMENTATION_PLAN.md`
- Independent IBKR source decision: `docs/adr/0028-independent-ibkr-market-data-source.md`
- China A50/Korea 200/Taiwan/VIX/Bitcoin review: `docs/archive/capture-v4/APAC_REVIEW.md`
- Verified snapshot import: `docs/RESEARCH_SNAPSHOT_RUNBOOK.md`
- First framework-proof result: `docs/archive/research-proof/FIRST_RANKING_REPORT.md`
- Superseded plans, qualification and incident evidence: `docs/archive/`

Historical records are consulted only to reconstruct an incident or decision, verify retained
evidence or handle a compatibility boundary that still affects current work.
