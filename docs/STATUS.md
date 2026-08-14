# Current status

**Updated:** 2026-08-14
**Current milestone:** R2 — local and pooled baselines (R2.F1 core, source-specific IBKR representative integration and R2.H verification paths implemented; qualifying execution pending)
**Parallel track:** the retained Stage 6–8 IBKR historical evidence is verified, S7.3 v2 migration is complete, and the qualifying foundation has separate implementation-only receipt and confirmatory-promotion authorities
**Native capture:** the full reviewed B5 universe deployment, qualification and paper-account Gateway restart lifecycle are complete
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
  `qtrad-r2-preprocessing-selection-v1` artefact binds model, horizon, evidence, application,
  numerical-library, instrument-identity, intercept and membership-policy identities; verification
  independently rebuilds its structural state exactly and numerical state within configured
  tolerances. The v1 contract is an amended discriminated union: its R2.C local branch remains limited
  to one eligible target and `LOCAL_RIDGE`, while R2.E adds the explicitly identified pooled branch.
- R2.D software now consumes an independently authenticated R2.C selection and fits the final
  primary-horizon local Ridge only on complete outer-training membership. The immutable
  `qtrad-r2-fold-fit-v1` contract retains fold, target, feature, preprocessing, selected-alpha,
  library/image, coefficient, intercept, warning, failure and numerical-diagnostic evidence. Its
  strict serializer independently rebuilds the fit within declared numerical tolerances. Local
  outer-validation forecasts bind the exact R1 target and fold plus causal feature/training cut-offs;
  a separate `qtrad-r2-forecast-coverage-v1` child accounts for every expected opportunity and emits
  no zero fallback on failed fits or missing features. The local-feature ablation orchestrator
  requires exact target/fold/feature-set selection coverage, and
  `qtrad-r2-coefficient-stability-v1` records per-target, horizon and feature-set scale/sign stability.
  Synthetic signal recovery, validation-label isolation, explicit failed-fit and missing-feature
  coverage, coefficient replay, round-trip and rehashed-tamper tests pass. This remains
  `IMPLEMENTATION_EVIDENCE_ONLY`; representative native integration and any effectiveness conclusion
  remain pending.
- R2.E software now provides pooled local-feature and fixed non-graph cross-asset Ridge controls.
  Pooled selection requires the exact eligible-instrument order, a fixed full one-hot identity block
  with no intercept, and at least one observation for every scoped instrument in outer training,
  purged inner fit and inner validation. The configured row minima remain aggregate safeguards. Final
  fitting manifests equal total training weight per instrument normalised to mean one and independently
  supplied application, NumPy and scikit-learn provenance. Pooled fold fits retain OOF forecasts,
  exact opportunity coverage, coefficient stability and independent replay through the existing strict
  artefact contracts. The ablation orchestrator authenticates exact P0, P1 and local comparator
  target/fold keys, coverage and lineage, then records both own and exact common target support.
  Synthetic shared-signal and cross-asset-signal recovery, missing-member rejection, uneven weighting,
  instrument-order invariance, replay and incomplete-comparator tests pass. This remains
  `IMPLEMENTATION_EVIDENCE_ONLY`; representative native integration and any pooled-versus-local
  effectiveness conclusion remain pending.
- The R2.F1 core now provides `qtrad-r2-evaluation-v1`, `qtrad-r2-local-comparator-v1` and
  `qtrad-r2-selection-v1` contracts. Evaluation independently authenticates the complete declared local
  ladder and every evaluated local/pooled fold child against exact R1 membership. The complete attempted
  configuration register is retained separately from the four-model comparison hierarchy; configurations
  with no successful forecasts are marked `FAILED` and cannot become retained controls. Separately
  persisted, identity-bearing evaluated-model manifests bind feature, forecast, coverage, fit,
  training-prediction and coefficient-stability evidence.
- Comparator metrics use exact pairwise own/common support. Each comparison persists its expected
  pairwise denominator and realised common-support ratio, which drives the minimum-support gate;
  all-model common support remains diagnostic only. Training predictions are replayed from authenticated
  fits, and each fold's training-derived forecast buckets apply only to that fold's validation rows.
  Bucket ordering uses Spearman ranks. Reports retain ordering/monotonicity, fold/instrument stability and
  best-instrument/period concentration. Target-level data-quality slices are explicitly unavailable, with
  a reason, because the R1 bindings do not expose those classifications.
- Selection dispositions and retained IDs are derived from persisted acceptance thresholds and report
  evidence, including comparator, coverage, breadth, stability, concentration and replay gates. The
  verifier independently replays the comparator set, metrics, decisions, holdout comparator IDs, fitting
  policy and image identity. The manifest contains no holdout features, outcomes or caller-asserted empty
  external state; trusted absence verification is explicitly PENDING_R2_H_INTEGRATION. Rehashed semantic
  mutation and child mutation tests pass. The source/evidence-bound forecast-manifest, OOF-bundle and
  software-verification contracts now have create-only persistence, orphan/symlink/path-escape rejection,
  independent child authentication and CLI round-trip coverage. The generic synthetic software path is
  verified locally. The source-specific IBKR path now adds the fixed six-instrument profile, verified Stage 8
  file-only adaptation, profile-bound OOF replay and an implementation-only v2 R2.H envelope verifier; no
  fresh representative execution or qualifying bundle exists, so R2.F1/R2.H remain pending. No confirmatory
  model-selection or effectiveness claim is made.
- The disposable R2.G2 holdout machinery is implemented with versioned selection, outcome-blind feature
  preparation, pre-holdout final fits, sealed forecast/coverage children, marker-first reveal and
  irreversible consumption. Its focused evidence is implementation-only fixture evidence; no real
  holdout, confirmatory OOF selection, or effectiveness conclusion exists.
- The fixture-confirmatory C2a path independently replays qualifying F2 and persisted G1 authority. C2b
  derives the complete unopened feature/fit/forecast closure exclusively from verified G1, persists it as
  `OWNED_UNOPENED`, and then permits outcome decoding only after create-only base and confirmatory OPENED
  markers exist. Reveal uses the authenticated target child and exact frozen questions, support, metrics,
  thresholds and coverage policy; successful evaluation is irreversibly CONSUMED. Independent R2.H replay
  reports `VALID_CONSUMED_RESULT`, `OPENED_INCOMPLETE` or `INVALID`, and injected post-open failures cannot
  reset or reuse the preparation. This remains fixture-only software evidence: no real holdout was accessed
  and no research or effectiveness claim is made.
- ADR 0028 and ADR 0029 plus `docs/IBKR-HISTORICAL-ACQUISITION.md` define the independent,
  market-data-only IBKR paper source and its historical evidence boundaries. Stage 1 provides typed
  `qtrad-ibkr-contract-selection-v1` and `qtrad-ibkr-acquisition-runtime-v1` artefacts, strict
  independent loaders/verifiers, create-only persistence, archive rehashing and offline CLI paths.
  Stage 2 adds strict `qtrad-ibkr-historical-request-profile-v1` and
  `qtrad-ibkr-historical-plan-v1` contracts plus a PostgreSQL- and Gateway-independent deterministic
  planner and file-only plan verifier. Stage 3 adds a transport-independent PostgreSQL execution
  state machine with byte-stable registration, pre-I/O attempt persistence, append-only
  connection-session-namespaced provider callbacks, profile-bound pacing reservations, completion markers,
  restart recovery and publication state, with fake-port transition and crash-injection coverage.
  Stage 4 adds bounded request/aggregate result builders from durable state, create-only result
  manifests/children and a file-only verifier that reconstructs each accepted closure and rejects
  missing, altered, additional or orphaned files. Stage 5 adds the official direct TWS historical
  adapter, generation-fenced contract reauthentication, MIDPOINT/SCHEDULE callback normalization,
  sanitized error evidence, bounded cancellation/timeout handling, immutable canary evidence and
  file-only canary/profile operations. The account-gated capability review, matched host deployment
  and bounded canary are complete; all 12 representative 1D/1W/2W/4W cases passed.
  The full Stage 6 result closure and the provenance-distinct original Stage 7 v1 provider-history
  closure remain retained. S7.1 issued reusable receipt `42f6c251...338e`; S7.3 exact-head validation
  at `f0e882bbcd19aabbefb1add2d87a03daae7670e8` cheaply authenticated it, published and independently
  verified v2 once, and preserved all 3,376,258 rows, 2,948 logical partitions, dataset identity
  `2f7f6199...a0e7e3` and aggregate identity `2ca2fbd0...22a0c` while reducing 2,948 physical
  parts to 120. V2 receipt `daa116bc...86a44` authenticated in 3.99 seconds. A deliberately bounded
  Stage 8 build selected 14 of 120 parts, completed in 753.12 seconds and published build
  `31ae56bd...d5d7f`; its insufficient-history disposition is expected and makes no research claim.
  Durable rollout evidence is retained at
  `/workspace/tmp/ibkr-historical-r2-20260810T081317Z/stage7/s73-validation-f0e882b-attempt1`.
  The retained full Stage 8 build `9893e785...38d47` is `QUALIFYING_HISTORY_READY`; receipt
  `56228947...ffd01` is implementation-only and authenticated promotion `1a1d09d9...81e2` is the
  distinct confirmatory capability. No real holdout result, effectiveness claim or downstream R2 result
  has been created. IBKR history stays provenance-distinct and cannot substantiate native IG fills,
  spreads or slippage.
- The independent IBKR native top-of-book collector reached its full reviewed B5 universe on 2026-08-10. Final-image B3 and B4 parents were refreshed and independently replayed before the full-universe promotion.
- Controlled B5 session `971facc4-cab4-413a-a29a-27c7f7ac89e1` received and persisted 24,056 callbacks with zero failed, dropped or reconciliation-loss callbacks, crossed generation 1 to 2, and retained fresh post-reconnect LIVE bid/ask evidence for all twenty contracts. Backup `qtrad-ibkr-20260810T153222Z.dump` has SHA-256 `f4ca959639ca4f10be4c19c07d795fc9987e887620247670cdabd3f7f0116e5d`.
- Snapshot plus independent three-restore replay authenticated the qualification-bound B3 and B4 parents and current B5 store before minting `B5_FULL_UNIVERSE` with canonical artifact SHA-256 `efb6f465221659cb0b1c65d6e0df12ac01d20a9227d07e606e8febf78152ed24`. The qualification file SHA-256 is `87c4860dbc97b7e73e1849ed58ba528b1b630cdd13207393fec32ebfb1eb9218`; verifier output SHA-256 is `dbca7ba916fa2c1a97fecc2dd1ef71f73621ddf87cbe6313ca7f416b41949a67`. Continuous capture initially resumed 20/20 on commit `af8037dff4e5557462eb359f962eb32f20cd0d7a`, but its last callback was 2026-08-10 22:30:00 UTC when the active Gateway auto-restarted. It did not reconstruct subscriptions and later failed closed; the subsequent interval is an explicit collection gap. PR #117 subsequently completed the restore-isolated exact-20 deployment at commit `4e11e76e33cdeefd21ad0c266493c5d31c94536f` and configuration hash `4826925a13b92129303a40a3120ac4763551875b169dc8ccc7cb21bafa360a50`. Natural daily restarts on 2026-08-12 and 2026-08-13 each reconstructed 20/20 LIVE subscriptions with the old session fully persisted and zero drops.
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
  while its exact listing is available before it can be promoted.
- R0's 2026-07-22 bounded historical-data decision remains retained evidence. ADR 0028 subsequently
  approved an independently governed IBKR paper-market-data track. Exact contracts, account
  entitlements and the Stage 5 1D/1W/2W/4W capability boundary are evidenced. The retained Stage 6–8
  historical path is verified through the S7.3 v2 migration, an implementation-only Stage 8 receipt and a
  separate qualifying confirmatory-promotion attestation. No real holdout or downstream R2 result exists.
  IBKR history stays provenance-distinct and cannot substantiate native IG fills, spreads or slippage.
- `B5_FULL_UNIVERSE` qualifies only the retained twenty-contract native-capture session. PR #116 corrected clean socket-closure detection, bounded health output and canonical Gateway ownership. During its 2026-08-12 rollout, overlapping restore work contended with live persistence: session `536b9af2-af43-4b86-bdd0-313d8b6f3a9b` retained 66,380 of 116,381 callbacks and explicitly recorded 50,001 drops before failing closed. Preserve both this gap and the earlier post-2026-08-10 22:30 UTC gap. PR #117 deployed restore isolation and root-space retention through the controlled restore-backed apply. The current paper account has no distinct weekly 2FA expiry; two natural daily Gateway restarts have demonstrated exact 20/20 LIVE reconstruction and zero unexplained loss.

## Next actions

1. Remove the v1 provider-history writer and general v1 runtime in the next IBKR data-foundation
   maintenance PR; retain only cheap authentication of existing v1 evidence and use its archived exact
   commit for any historical deep audit.
2. The R2 integration/verification workstream owns the post-S7.3 recursive-verification architecture
   audit across R1 and R2; consolidate replay only where it preserves independent authority boundaries.
3. Use the source-specific file-only IBKR path for implementation-only R2.H work after Stage 8 receipt
   authentication. Real F2 additionally requires the retained confirmatory-promotion attestation; real
   holdout execution and effectiveness evidence remain separately authorised and pending.
4. Continue proportionate read-only observation of `capture-v4` delivery, gaps, loss and lag.
5. Run R2.B, R2.C and later R2 integration/verification against representative and qualifying bundles
   with explicit `IMPLEMENTATION_EVIDENCE_ONLY`, insufficient-history or source-limited dispositions.

## Evidence and current authorities

- Active milestone: `PLAN.md`
- Trading-research intent and gates: `docs/TRADING_RESEARCH.md`
- Implemented and intended system shape: `docs/ARCHITECTURE.md`
- Current capture procedure: `docs/CAPTURE_OPERATIONS_RUNBOOK.md`
- R0 native coverage and retained historical-source decision: `docs/R0_DATA_READINESS.md`
- IBKR normative historical implementation plan: `docs/IBKR-HISTORICAL-ACQUISITION.md`
- Completed IBKR native collector programme: `docs/archive/R2_LANEB_IMPLEMENTATION_PLAN.md`
- IBKR native operational interfaces: `ops/ibkr/README.md`
- Independent IBKR source decision: `docs/adr/0028-independent-ibkr-market-data-source.md`
- China A50/Korea 200/Taiwan/VIX/Bitcoin review: `docs/archive/capture-v4/APAC_REVIEW.md`
- Verified snapshot import: `docs/RESEARCH_SNAPSHOT_RUNBOOK.md`
- First framework-proof result: `docs/archive/research-proof/FIRST_RANKING_REPORT.md`
- Superseded plans, qualification and incident evidence: `docs/archive/`

Historical records are consulted only to reconstruct an incident or decision, verify retained
evidence or handle a compatibility boundary that still affects current work.
