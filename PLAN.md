# q-trad multi-asset paper-research plan

**Status:** ACTIVE
**Current milestone:** R3 — cost and portfolio baseline (NOT STARTED; R2 is complete for the source-specific terminal result below)
**Parallel track:** `R2-IBKR-HISTORICAL` is complete with a valid consumed confirmatory result; native IG and IBKR capture tracks remain provenance-distinct and do not inherit that conclusion
**Objective:** determine, with locked chronological evidence, whether multi-horizon local and
cross-asset forecasts can justify a cost- and risk-aware paper portfolio.
**Safety boundary:** IG demo and IBKR paper market data with internal paper outcomes only; no external
orders or production broker connectivity.

`docs/TRADING_RESEARCH.md` defines the stable research programme. The completed R2 decision-grade
execution record is archived at `docs/archive/r2/R2_SCIENTIFIC_EXECUTION_PLAN.md`. The active R3
execution authority is `docs/R3_EXECUTION_PLAN.md`; this file selects the current work and records
only the milestone sequence needed to reach the next trustworthy decision.

## Current position

- `capture-v4` is the live reviewed 23-market IG demo universe. Its 22 non-VIX markets remain
  potentially tradable, subject to experiment role and fail-closed paper eligibility; VIX is
  context-only.
- The capture, canonical event, one-minute bar, snapshot/import, Parquet, deterministic replay and
  read-only health paths are implemented.
- A retained single-instrument framework proof produced causal forecasts/outcomes, executable-side
  shadow fills, independently checked P&L and a reproducible ranking report. Its negative cost-aware
  result is evidence that the framework path works, not a strategy conclusion or the intended
  portfolio architecture.
- The next paper programme replaces runtime strategy ranking and regime selection with
  multi-horizon expected-return forecasts, explicit cost/risk states, horizon attribution and
  constrained paper portfolio construction.
- The bounded IG demo review accepted China A50 and Taiwan plus an AUD-denominated VIX for
  context-only capture. Korea 200 has no eligible demo listing, and the reviewed Bitcoin listings
  were unavailable; both are quarantined. The immutable 23-market `capture-v4` was activated on
  2026-07-22 after exact release, backup, rollback and readiness gates. R0's native audit,
  historical-source decision and restore verification are complete.
- The independent IBKR native collector reached its fixed-six B4 boundary on 2026-08-10: exact-two
  B3 was refreshed and independently reverified on the final `main` image, B4 was promoted and
  qualified, and continuous exact-six capture resumed healthy.
- R1.A–R1.E now provide the causal foundation path: isolated-snapshot observation build/verify,
  separate initial-availability and correction-maturity evidence, aligned panels with retained
  gap/source-activity audit evidence, frozen multi-horizon targets, chronological folds,
  model-independent OOF forecasts and a thin bundle over independently manifested children. A real
  23-market native bundle remains evidence work; the zero-return probe and short native history
  make no effectiveness claim. R1 implementation is complete; the native bundle remains a separate
  fail-closed prerequisite for native-source experiments once its configured evidence interval and
  product-role qualification support it. The historical R2 result does not alter that boundary.
- R2.A through R2.E are software-complete implementation evidence. The R2.F1 core and its source/evidence-bound replay machinery are implemented, including create-only forecast, OOF, selection and software-verification contracts plus CLI operations and mutation-tested independent authentication.
- A separate bounded 20-week sparse fixture exercises the exact production CLI and persistence path through Stage 8, feature receipts, OOF, F2 promotion, G1 selection and unopened G2 preparation. It remains implementation-only evidence and is not the source-specific result below.
- The terminal `R2-IBKR-HISTORICAL` run is `VALID_CONSUMED_RESULT`. Scope is `IBKR_HISTORICAL_RESEARCH`, historical MIDPOINT OHLC, declared provider-history delay, primary 15-minute horizon, six fixed targets and three fixed groups. Locked `LOCAL_RIDGE` versus `ZERO_RETURN` was **NEGATIVE**; `POOLED_LOCAL_RIDGE` versus `LOCAL_RIDGE` was **POSITIVE**; pooled beyond zero is **INCONCLUSIVE** because no pooled-versus-zero question was frozen; and pooled cross-asset was rejected at OOF. Both locked questions had coverage `0.9749187203016487` and support `202,709`.
- This result is source-specific. It does not establish native IG predictability, live IBKR predictability, executable bid/ask performance, post-cost profitability, portfolio performance or production readiness.
- The evidence-handoff simplification programme converged the Stage 6 through R2 boundaries before the terminal run on immediate-parent receipt authentication. Each boundary transforms and independently verifies once; ordinary descendants authenticate that proof, and promotion grants authority without semantic replay.
- PR-C1 is merged and complete for this convergence: obsolete readers, migration bridges, recursive replay-input discovery, redundant ancestor/whole-file verification and retired compatibility paths are deleted. The ordinary work-count matrix is covered by focused instrumentation; the clean full gate remains the release check.
- ADR 0028 and ADR 0029 approve an independent, market-data-only IBKR paper source and its
  historical evidence boundaries. The normative staged path is `docs/IBKR-HISTORICAL-ACQUISITION.md`.
  Stage 1 contract/runtime artefacts, Stage 2 deterministic request-profile/plan artefacts,
  Stage 3 durable execution state-machine artefacts and Stage 4 create-only result publication/file-only
  verification artefacts are implemented. Stage 5 adds the official direct TWS historical
  adapter, callback normalization, immutable canary evidence and file-only canary/profile operations.
  Account-gated capability review, host deployment and the bounded Stage 5 canary are now complete:
  all 12 representative 1D/1W/2W/4W MIDPOINT/SCHEDULE cases passed with the frozen 300-second profile.
- The full Stage 6 result closure and superseded Stage 7/8 evidence remain retained and immutable. The H4 migration produced the reviewed scientific invalidation record (`3610c94...`); the replacement Stage 8 promotion is authenticated as `1d881066c269a67dbe0663bd869f8b9d50ec3f539ef30bea982e4ee386d0fdd5` and bound to the terminal historical result.
- Current Stage 7 and Stage 8 construction, verification, authentication and promotion are v3-only. The completed historical result remains provenance-distinct and cannot substantiate native IG execution conclusions.

## Milestones

| Milestone                                   | Status      | Exit evidence                                                                                                                                                                                                                           |
| ------------------------------------------- | ----------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| R0 — alignment, coverage and data readiness | COMPLETE    | active docs agree; China/Korea review is resolved; native/aligned coverage, historical-source decisions and an independent restore verification are recorded                                                                            |
| R1 — causal multi-asset research foundation | COMPLETE    | deterministic aligned panels, multi-horizon targets, chronological folds, out-of-fold artefacts and independently verified bundle infrastructure pass causality/replay checks                                                           |
| R2 — local and pooled baselines             | COMPLETE    | `R2-IBKR-HISTORICAL` reached `VALID_CONSUMED_RESULT`; locked local-vs-zero was NEGATIVE, pooled-vs-local POSITIVE, pooled-vs-zero INCONCLUSIVE (not frozen), and pooled cross-asset was rejected at OOF; coverage `0.9749187203016487`, support `202,709` |
| R3 — cost and portfolio baseline            | NOT STARTED | costs, shrinkage risk, horizon positions, global netting and constrained targets reconcile deterministically                                                                                                                            |
| R4 — residual structural graph experiment   | NOT STARTED | local, pooled, fixed, learned and shuffled graph controls measure incremental graph value                                                                                                                                               |
| R5 — integrated offline MVP                 | NOT STARTED | chronological forecast, economic and portfolio gates report the full ablation set                                                                                                                                                       |
| R6 — continuous shadow paper                | NOT STARTED | the validated stack runs continuously with causal executable fills, horizon attribution and reconciling paper P&L                                                                                                                       |

## R0 — alignment, coverage and data readiness

1. **Done:** realign active planning, architecture, agent-routing and status documents around the
   multi-asset programme; archive the superseded strategy-ranking plan and planning inputs.
2. **Done:** review China A50, Korea 200, Taiwan, VIX and Bitcoin through the bounded demo gate;
   accept the smaller China/Taiwan contracts and context-only VIX, and quarantine unavailable
   Korea/Bitcoin without guessing.
3. **Done:** publish and activate immutable 23-market `capture-v4` under separate authority;
   verify 23/23 readiness, clean v3→v4 run transition and zero observed reconnect/loss counters.
4. **Done:** automate the repeatable capture release/activation path around an exact descriptor,
   CI run, safe application-image retention, backup, unchanged-universe checkpoint, one dynamic
   activation, bounded observation, automatic rollback and sanitised evidence.
5. **Done:** audit native quote and bar evidence needed by R1–R3: bid/ask-size coverage, session
   coverage, gaps, revisions, aligned multi-market intervals and available product economics.
6. **Done:** combine the available historical-source context with a bounded IG-candle/external-
   sample, licence and provenance decision.
7. **Done:** record which data can support prototyping, model selection and decision-grade IG paper
   conclusions. External history never substitutes for native executable-side evidence; see
   `docs/R0_DATA_READINESS.md`.
8. **Done:** repair and re-run weekly restore verification independently of research and universe
   work. The 2026-07-22 run restored `daily/qtrad-capture-20260722T161655Z.dump`, verified
   manifest schema `qtrad-capture-backup-v2`, migration `0010` and 10,319,635 canonical events,
   then removed the disposable target.

R0 does not implement model, optimiser or live-paper interfaces. It removes avoidable data and
chronology ambiguity before those contracts are fixed.

## R1 — causal multi-asset research foundation

- Archived R1 implementation plan: `docs/archive/r1/R1_IMPLEMENTATION_PLAN.md`.
- Use configurable 5, 15, 30 and 60-minute horizons; prove the first end-to-end path at 15 minutes.
- Define completed-bar log-return targets, target-availability and overlap intervals,
  direction-independent upper and lower excursions, and deterministic handling of gaps and closures.
- Build aligned panels without forward-filling executable prices or hiding unavailable assets.
- Generate reproducible walk-forward folds with dependency-derived purging/embargo and a locked
  final holdout.
- Store immutable out-of-fold forecasts with feature/training cut-offs, model/experiment/fold
  lineage and later outcomes, independently of model code.
- Compose the child artefacts into an independently verifiable foundation bundle and build it from
  a verified observation manifest through the offline CLI. The bundle contains references and
  cross-dataset metadata rather than duplicated child rows. Do not treat bundle infrastructure or
  the zero-return probe as an effectiveness result.

## R2 — local and pooled baselines

- Archived R2 implementation plan: `docs/archive/r2/R2_IMPLEMENTATION_PLAN.md`.
- R2.A through R2.E are software-complete; R2.F1 provides source-bound, independently replayable OOF, selection and software-bundle contracts and CLI operations. The terminal historical execution completed the confirmatory OOF, selection and locked-holdout lifecycle with independent R2.H verification.
- R2.C supplies authenticated final fold fits, chronological forecasts, explicit expected-opportunity coverage, independent coefficient replay and coefficient stability summaries. R2.E supplies pooled local-feature and fixed non-graph cross-asset controls with exact own/common-support ablations. R2.F1 retains evaluated-model children, pairwise metrics, stability/concentration evidence, deterministic gates and complete dispositions.
- Keep `R2-IG-NATIVE`, `R2-IBKR-HISTORICAL` and any later `R2-IBKR-NATIVE` experiment source-specific.
  Each consumes one independently verified foundation and makes conclusions only for its evidence
  class. No source-specific experiment silently replaces another.
- Retain per-asset Ridge as the required local baseline and add pooled local-feature and pooled
  non-graph cross-asset controls.
- Begin with ablatable returns, volatility, time/session, spread and validated quote-imbalance
  features. Do not call top-of-book size changes cumulative volume delta.
- Compare loss, correlation/rank correlation, direction, forecast buckets, coverage and stability by
  asset, horizon and period.
- `R2-IBKR-HISTORICAL` passed the unchanged confirmatory OOF, selection and locked-holdout gates. Any future `R2-IG-NATIVE` or `R2-IBKR-NATIVE` run remains source-specific and fail-closed until its own foundation, availability policy and fold durations pass the same gates.

## Parallel IBKR market-data track

- Normative historical-acquisition plan: `docs/IBKR-HISTORICAL-ACQUISITION.md`; durable boundaries:
  `docs/adr/0028-independent-ibkr-market-data-source.md` and
  `docs/adr/0029-ibkr-historical-acquisition-evidence-boundaries.md`.
- The software path through Stage 8 is implemented on `main`: immutable planning, execution and result closure; provider-history observations with declared availability; and the source-specific foundation/readiness verifier. The `R2-IBKR-HISTORICAL` profile-bound OOF and terminal R2.H paths now have independently verified lower-stage evidence and a completed consumed result.
- The account-gated exact-contract capability review, matched read-only host deployment and Stage 5
  canary are complete. The canary passed all 12 representative 1D/1W/2W/4W MIDPOINT/SCHEDULE cases,
  and the 300-second request profile is frozen.
- The full Stage 6 closure and superseded Stage 7/8 evidence remain retained and immutable. The H4 invalidation record `3610c94...` documents the scientific divergence and superseded promotion; replacement Stage 8 promotion `1d881066c269a67dbe0663bd869f8b9d50ec3f539ef30bea982e4ee386d0fdd5` is bound to the terminal result.
- Current Stage 7/8 runtime is v3-only: construction and deep verification use explicit immediate-parent receipts, ordinary authentication is cheap and selected-child bounded, and promotion is authority-only without semantic replay. The historical result is complete but cannot be generalised to native IG or native IBKR predictability.
- The independent IBKR native top-of-book collector reached its full reviewed B5 universe on 2026-08-10. Exact-main controlled B5 session `971facc4-cab4-413a-a29a-27c7f7ac89e1` received and persisted 24,056 callbacks with zero failed, dropped or reconciliation-loss callbacks, crossed generation 1 to 2, retained fresh post-reconnect LIVE bid/ask evidence for all twenty contracts, and passed snapshot plus independent three-restore replay to mint `B5_FULL_UNIVERSE`.
- The qualifying backup `qtrad-ibkr-20260810T153222Z.dump` has SHA-256 `f4ca959639ca4f10be4c19c07d795fc9987e887620247670cdabd3f7f0116e5d`. Continuous capture initially resumed 20/20 on application commit `af8037dff4e5557462eb359f962eb32f20cd0d7a`, but stopped receiving at the 2026-08-10 22:30 UTC Gateway auto-restart and later failed closed. Preserve that gap. PR #116 restored socket-death detection and canonical Gateway ownership; its 2026-08-12 rollout exposed a second explicit gap when concurrent restore work saturated persistence. PR #117 then completed the restore-isolated exact-20 deployment at commit `4e11e76e33cdeefd21ad0c266493c5d31c94536f` and configuration hash `4826925a13b92129303a40a3120ac4763551875b169dc8ccc7cb21bafa360a50`. Natural daily Gateway restarts on 2026-08-12 and 2026-08-13 each replaced the collector process and reconstructed 20/20 LIVE subscriptions with the old session fully persisted and zero drops. This is the applicable lifecycle gate for the current paper account, which has no distinct weekly 2FA expiry.
- Use the hardened interfaces in `ops/ibkr/README.md` for maintenance stops, bounded capture,
  qualification and restore verification; do not reconstruct those Docker/systemd paths ad hoc.
- The adapter remains market-data-only. Account access, acquisition, deployment and publication are
  separately authorised operations.

## R3 — cost and portfolio baseline

- Version observed spread, adverse slippage, commission, financing and supported impact assumptions.
- Estimate ordered horizon-specific shrinkage covariance and configured group/currency exposures.
- Maintain virtual asset/horizon positions, optimise each horizon, then net and repair the global
  physical paper target with stable reason codes.
- Select solver and numerical dependencies at milestone entry. Preserve `Decimal` at price,
  quantity, conversion and money boundaries.

## R4 — residual structural graph experiment

- Derive graph targets only from out-of-fold local residuals.
- Compare local-only, pooled non-graph, fixed economic graph, learned structural GNN-LSTM and
  shuffled-graph controls.
- Complete the experiment even if simpler forecasts are weak; retain the graph only on incremental
  held-out evidence. Dynamic adjacency and session experts remain deferred.

## R5 — integrated offline MVP

- Compare local threshold, local-plus-optimiser, graph-plus-optimiser and full multi-horizon sleeve
  configurations.
- Report forecast, economic and portfolio gates plus cost, feature, graph, horizon and nearby
  parameter sensitivities.
- Require attributable constraint/fallback decisions and independently reconciled accounting.
- Treat a trustworthy negative conclusion as successful completion.

## R6 — continuous shadow paper

- Adapt only stable offline contracts to continuous live data.
- Retain forecasts for every evaluated configuration and preserve horizon attribution through
  physical netting.
- Rebalance persistent virtual positions using later healthy bid/ask observations and record costs,
  operational vetoes and P&L.
- Decide checkpoint/process architecture from demonstrated runtime lifecycle needs. Add no broker
  order port or external submission.

## Completion and documentation rules

- A milestone completes only with focused automated evidence and a concise retained reference.
- Run the full clean PostgreSQL/static/test gate at code, schema, milestone or release boundaries,
  not for a documentation-only edit.
- Keep `PLAN.md` forward-looking, `docs/STATUS.md` factual and `docs/TRADING_RESEARCH.md` stable.
- Archive superseded chronology and source planning material outside the normal reading path.
- Add an ADR for a durable costly-to-reverse decision, not a reversible model experiment.
