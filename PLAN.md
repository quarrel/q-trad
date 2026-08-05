# q-trad multi-asset paper-research plan

**Status:** ACTIVE
**Current milestone:** R2 — local and pooled baselines (R2.F1 core and software-verification machinery implemented; fresh representative integration pending)
**Parallel track:** independent IBKR paper-market-data qualification and historical acquisition
**Objective:** determine, with locked chronological evidence, whether multi-horizon local and
cross-asset forecasts can justify a cost- and risk-aware paper portfolio.
**Safety boundary:** IG demo and IBKR paper market data with internal paper outcomes only; no external
orders or production broker connectivity.

`docs/TRADING_RESEARCH.md` defines the stable research programme. This file selects the current
work and records only the milestone sequence needed to reach the next trustworthy decision.

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
- R1.A–R1.E now provide the causal foundation path: isolated-snapshot observation build/verify,
  separate initial-availability and correction-maturity evidence, aligned panels with retained
  gap/source-activity audit evidence, frozen multi-horizon targets, chronological folds,
  model-independent OOF forecasts and a thin bundle over independently manifested children. A real
  23-market native bundle remains evidence work; the zero-return probe and short native history
  make no effectiveness claim. R1 implementation is complete; producing the first real 23-market
  bundle remains a fail-closed R2 entry prerequisite once the configured evidence interval and
  product-role qualification support it.
- R2.A through R2.E are software-complete implementation evidence. The R2.F1 core and its source/evidence-bound
  replay machinery are implemented, including create-only forecast, OOF, selection and software-verification
  contracts plus CLI operations and mutation-tested independent authentication. A fresh representative
  source-specific integration and its R2.H software bundle remain before the R2.F1 exit is complete; R2.F2
  and the locked holdout remain gated on a frozen qualifying foundation.
- ADR 0028 and ADR 0029 approve an independent, market-data-only IBKR paper source and its
  historical evidence boundaries. The normative staged path is `docs/IBKR-HISTORICAL-ACQUISITION.md`.
  Stage 1 contract/runtime artefacts, Stage 2 deterministic request-profile/plan artefacts,
  Stage 3 durable execution state-machine artefacts and Stage 4 create-only result publication/file-only
  verification artefacts are implemented. Stage 5 adapter, callback normalization, immutable canary
  evidence and file-only canary/profile operations are implemented locally; account-gated review, host
  deployment and request-profile canary evidence remain pending.
  Stages 6–8 are now implemented on `main`: Stage 6 immutable acquisition registration/execution,
  Stage 7 verified provider-history observation construction, and Stage 8 source-specific
  foundation/readiness with independent replay and bounded child persistence. This is implementation
  evidence only: account-gated deployment, a full acquisition and a qualifying readiness disposition
  remain pending, and IBKR evidence cannot substantiate an IG-native conclusion.

## Milestones

| Milestone                                   | Status      | Exit evidence                                                                                                                                                                                                                           |
| ------------------------------------------- | ----------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| R0 — alignment, coverage and data readiness | COMPLETE    | active docs agree; China/Korea review is resolved; native/aligned coverage, historical-source decisions and an independent restore verification are recorded                                                                            |
| R1 — causal multi-asset research foundation | COMPLETE    | deterministic aligned panels, multi-horizon targets, chronological folds, out-of-fold artefacts and independently verified bundle infrastructure pass causality/replay checks                                                           |
| R2 — local and pooled baselines             | IN PROGRESS | R2.A–R2.E software is complete and the R2.F1 core is implemented; representative integration and the R2.H software-verification bundle remain before a qualifying frozen foundation drives confirmatory OOF and locked-holdout evidence |
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

- R1 implementation plan `docs\R1_IMPLEMENTATION_PLAN.md`
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

- R2 implementation plan `docs/R2_IMPLEMENTATION_PLAN.md`.
- R2.A through R2.E are software-complete; R2.F1 now has source-bound, independently replayable
  OOF/selection/software bundle contracts and CLI operations, but still requires a fresh representative
  capture-v4 integration to exercise them. R2.C supplies authenticated,
  final fold fits, chronological forecasts, explicit expected-opportunity coverage, independent
  coefficient replay and coefficient stability summaries. R2.E adds pooled local-feature and fixed
  cross-asset controls with explicit instrument identity, manifested equal-instrument weights and exact
  own/common-support ablations. R2.F1 adds independently authenticated evaluated-model children,
  pairwise own/common-support metrics, fold-local training-derived forecast buckets and ordering,
  stability/concentration evidence, deterministic selection gates, complete configuration dispositions
  and immutable evaluation/selection evidence with a separately authenticated local-comparator child.
- Keep `R2-IG-NATIVE`, `R2-IBKR-HISTORICAL` and any later `R2-IBKR-NATIVE` experiment source-specific.
  Each consumes one independently verified foundation and makes conclusions only for its evidence
  class. No source-specific experiment silently replaces another.
- Retain per-asset Ridge as the required local baseline and add pooled local-feature and pooled
  non-graph cross-asset controls.
- Begin with ablatable returns, volatility, time/session, spread and validated quote-imbalance
  features. Do not call top-of-book size changes cumulative volume delta.
- Compare loss, correlation/rank correlation, direction, forecast buckets, coverage and stability by
  asset, horizon and period.
- Keep confirmatory OOF, selection freeze and holdout execution fail-closed until the exact source's
  history, contract/product roles, availability policy and fold durations pass the unchanged gates.

## Parallel IBKR market-data track

- Normative historical-acquisition plan: `docs/IBKR-HISTORICAL-ACQUISITION.md`; durable boundaries:
  `docs/adr/0028-independent-ibkr-market-data-source.md` and
  `docs/adr/0029-ibkr-historical-acquisition-evidence-boundaries.md`.
- The software path through Stage 8 is implemented on `main`: immutable planning, execution and
  result closure; provider-history observations with declared availability; and the source-specific
  foundation/readiness verifier. It remains dependent on independently verified lower-stage evidence.
- Operational work remains: qualify account-visible exact contracts, entitlements, timestamps, sessions
  and historical capabilities; deploy the matched read-only runtime; execute the Stage 5 canary; then
  register and run the full Stage 6 acquisition before building Stage 7/8 evidence.
- No qualifying IBKR readiness disposition or downstream R2 artifact has been created; keep
  `R2-IBKR-HISTORICAL` source-specific and do not combine its evidence with IG-native data.
- Add live IBKR top-of-book capture only as an independent runtime and canonical store with
  operator-authenticated Gateway lifecycle, truthful health, recovery, backups and restore evidence.
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
