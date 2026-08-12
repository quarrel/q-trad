# q-trad IBKR historical path remediation

## PR plan: Stage 8 first, then Stage 7

**Planning date:** 2026-08-11  
**Repository:** `quarrel/q-trad`  
**Starting authority:** current `main`, including the merged ADR 0029 amendment on reusable
verification evidence, claim-scoped invalidation, separate
validity/readiness/provenance/confirmatory authority, and confirmatory promotion.
**Status:** Active remediation plan until the retirement criteria below are met.
**Documentation handoff:** Each PR must update `docs/ARCHITECTURE.md` in the same PR when it makes a
durable implemented flow true. S8.3 records Stage 8 receipts and cheap authentication; S8.4 records
confirmatory promotion; S7.1/S7.2 record the Stage 7 receipt and single-verification publication flow;
and S7.3 records semantic-versus-physical identity and prunable consumption.
`docs/IBKR-HISTORICAL-ACQUISITION.md` remains the operator workflow authority.
**Retirement criteria:** Move this plan to `docs/archive/` only after S8.1–S8.4 and S7.1–S7.3 have
landed, or any deliberately deferred remainder has a named active owner; exact-head operational
handoffs are complete; durable contracts are folded into `docs/ARCHITECTURE.md`,
`docs/IBKR-HISTORICAL-ACQUISITION.md` and the relevant R2 documents; `PLAN.md` and `docs/STATUS.md`
record the verified state; and no active authority depends on this file.

---

## 0. Non-negotiable programme constraints

All agents working these PRs must preserve the following boundaries.

1. **No provider calls and no reacquisition.** The current Stage 6 and Stage 7 evidence is immutable. A refetch would be a separately authorised new plan and is not part of these PRs.
2. **No R2 registration, OOF construction, model fitting, G1/G2 action, or holdout access.** These PRs repair the data and assurance path only.
3. **Do not delete or rewrite the current Stage 8 rehearsal records or checkpoint root.** The current run directory is:

   ```text
   /workspace/tmp/ibkr-historical-r2-20260810T081317Z/stage8/rehearsal-2
   ```

   Read `records/preflight.json` to discover the exact checkpoint root; do not infer it from naming.
4. **Protect checkpoint compatibility until the current Stage 8 foundation is published.** PRs S8.1 and S8.2 must not alter `src/qtrad/runtime/ibkr_foundation_bounded.py`, its current `_implementation_sha256()` inputs, or checkpoint serialization unless the agent first proves that the existing checkpoint remains accepted. If compatibility cannot be preserved, stop and revise the PR rather than launching another full build.
5. **A valid nonqualifying foundation is publishable.** Readiness gates downstream authority; it does not determine whether the foundation may exist or be retained.
6. **Gaps remain explicit.** No forward fill, silent removal, result merging, schedule coercion, or recategorisation of unknown evidence.
7. **The ordinary local threat model is non-adversarial with respect to the operator.** Continue to defend against bugs, accidental mutation, corruption, stale or mismatched inputs, leakage, look-ahead, false provenance, and unsupported claims.
8. **Deep cumulative replay occurs once at confirmatory promotion**, or after an explicit verifier revocation/defect. Ordinary downstream consumers authenticate immutable inputs and accepted receipts.
9. **One trust boundary per PR.** Do not use these PRs to refactor unrelated R1/R2 code, reorganise the package tree, or revisit Stage 6.
10. **Do not update `PLAN.md` or `docs/STATUS.md` with operational success claims until the exact-head real operation has completed.** Code/document-contract changes may be documented; evidence claims require evidence.

---

## 1. Starting-state capture before coding

The first agent must create a local, uncommitted inventory record before touching code.

### 1.1 Repository

```bash
git fetch origin
git switch main
git pull --ff-only
git status --short
git rev-parse HEAD
```

Confirm that the merged ADR 0029 amendment is present. Record the exact starting SHA in the PR description.

### 1.2 Existing Stage 7 evidence

Record without copying data:

- provider-history manifest path;
- dataset SHA-256: `2f7f6199fed48f26ea34a9dd10b217b2bf0cab5b47845f101372d66e66a0e7e3`;
- manifest file SHA-256: `bc917842fc37978fa79c440117fab283833b136969ee309ed2b5dd58fe14f0d7`;
- row count: `3,376,258`;
- instruments: `20`;
- partitions: `2,948`.

### 1.3 Existing Stage 8 work

From the rehearsal records, record:

- exact provider-history manifest;
- foundation configuration path and configuration ID;
- checkpoint root;
- original pinned code SHA `d8837a83773bb7127c0b626cb9db546ab553b47d`;
- worker count `4`;
- readiness result and evidence:
  - 9 folds;
  - 149,446 common-support rows;
  - 10 confirmatory provider gaps;
  - current cause `INSUFFICIENT_COMMON_SUPPORT`.

Hash and preserve:

```text
records/preflight.json
records/monitor.log
completion.txt
records/rehearsal.stdout
```

Do not add machine-local paths or bulk data to Git.

---

# Stage 8 PR sequence

## PR S8.1 — Correct Stage 8 coverage readiness and expose exact gap evidence

### Suggested metadata

```text
branch: fix/stage8-coverage-readiness
title:  Correct Stage 8 provider-gap coverage readiness
```

### Goal

Replace the current rule “any provider gap on a confirmatory instrument is fatal” with the frozen denominator-based coverage rule, while preserving every gap and keeping true common-support insufficiency separate.

### Scope

Primary files expected:

```text
src/qtrad/application/ibkr_foundation.py
src/qtrad/application/r2_readiness.py          # only if sharing existing policy requires it
src/qtrad/domain/ibkr_foundation.py
src/qtrad/domain/r2_readiness.py               # only for an existing shared policy/type
src/qtrad/application/r2_ibkr_historical.py    # only if it owns the block registry
src/qtrad/__main__.py                          # summary output only; no new destructive workflow
tests/test_ibkr_foundation.py
tests/test_r2_readiness.py
tests/test_r2_ibkr_historical.py
docs/IBKR-HISTORICAL-ACQUISITION.md            # only wording whose contract changed
docs/R2_IBKR_HISTORICAL_PLAN.md                # exact readiness policy, if this is its owner
```

Do **not** edit `src/qtrad/runtime/ibkr_foundation_bounded.py` in this PR.

### Required design

#### A. Reuse the exact R2 block policy

Do not invent a second Stage 8 interpretation of “research block.” Locate the exact identity-bearing block registry already used by R2 readiness and reuse or extract a pure shared helper.

The helper must derive the applicable blocks from authenticated configuration/fold/holdout evidence. Stage 8 must not use hand-written approximate date windows.

#### B. Opportunity accounting

For each fixed confirmatory instrument and each applicable research block, derive mutually exclusive counts:

```text
ELIGIBLE
GAP
INACTIVE
OTHER_INELIGIBLE (where the existing contract distinguishes it)
```

The qualifying coverage is:

```text
ELIGIBLE / (ELIGIBLE + GAP)
```

Rules:

- threshold: the existing frozen `0.90` policy;
- `GAP` remains in the denominator;
- `INACTIVE` is outside the denominator;
- a decision opportunity affected by overlapping raw gaps is counted once;
- coverage is calculated from the persisted causal opportunity/target/panel state, not from `raw_gap_minutes / total_rows`;
- context-only instruments remain reported but cannot block the fixed six-candidate gate;
- an empty denominator must retain the existing explicit unavailable/insufficient disposition rather than divide by zero or qualify.

#### C. Readiness causes

Add or use a cause whose meaning is specifically block coverage, for example:

```text
INSUFFICIENT_BLOCK_COVERAGE
```

Reserve `INSUFFICIENT_COMMON_SUPPORT` for actual common-support duration/row/week failure. Do not continue using it as an alias for “at least one provider gap exists.”

#### D. Persist diagnostic evidence

The Stage 8 readiness evidence must include a deterministic, bounded structure containing:

- every raw provider gap already retained by the foundation;
- candidate/block opportunity counts;
- coverage ratio in canonical exact form;
- threshold;
- pass/fail by candidate/block;
- exact list of blocking candidate/block keys;
- aggregate diagnostics, clearly non-authoritative where appropriate.

Do not create a second data-scale artefact in this PR. This report is compact derived metadata inside the existing foundation/readiness payload.

### Tests required

Add focused tests proving:

1. one isolated active-session gap with every candidate/block above 90% still qualifies;
2. exactly 90% qualifies;
3. immediately below 90% fails with `INSUFFICIENT_BLOCK_COVERAGE`;
4. overlapping gaps do not double-count one opportunity;
5. inactive periods are excluded from the denominator;
6. context-only gaps remain visible but do not block;
7. no-gap behaviour is unchanged;
8. actual insufficient common-support rows/weeks still produce `INSUFFICIENT_COMMON_SUPPORT`;
9. all six candidates and three groups remain fixed;
10. coverage evidence is deterministic under input ordering changes;
11. a semantic mutation to counts/threshold/block identity fails verification.

### Validation

During iteration:

```bash
uv run pytest -q \
  tests/test_ibkr_foundation.py \
  tests/test_r2_readiness.py \
  tests/test_r2_ibkr_historical.py
uv run ruff format --check src tests
uv run ruff check src tests
```

Before review:

```bash
ops/dev/verify.sh
git diff --check
```

### Definition of done

- The old zero-gap test has been replaced, not merely supplemented.
- The current 149,446-row run would be decided by per-block percentages, not by `gap_count > 0`.
- No checkpoint identity or Stage 8 physical derivation code changed.
- No claim is made about whether the real ten gaps pass until the exact-head foundation is built.

### Review emphasis

Review the denominator, block identity, deduplication, boundary timestamps, and cause semantics before style or performance.

---

## PR S8.2 — Replace destructive rehearsal with cheap preflight and retained publication

### Suggested metadata

```text
branch: fix/stage8-retained-builds
title:  Retain Stage 8 foundations and replace full rehearsal with preflight
```

### Goal

Make the ordinary workflow:

```text
preflight -> build/publish -> verify/receipt later
```

rather than:

```text
build all useful outputs -> label rehearsal -> delete outputs
```

### Scope

Primary files expected:

```text
src/qtrad/runtime/ibkr_foundation.py
src/qtrad/__main__.py
tests/test_ibkr_foundation.py
tests/test_research_cli.py
docs/IBKR-HISTORICAL-ACQUISITION.md
```

Avoid `ibkr_foundation_bounded.py` to preserve the current checkpoint.

### Required design

#### A. Add `foundation preflight`

Add a cheap command that validates only:

- provider manifest is a regular file and has a bounded canonical top-level document;
- configuration is valid and bound to the provider dataset expected by the command;
- output and child paths are available and safe;
- checkpoint identity can be opened/authenticated;
- worker/resource arguments are valid;
- required paths are on supported local filesystems where the existing policy requires it.

It must not:

- call `read_provider_history_source_evidence()`;
- decode provider-history Parquet rows;
- build observations/panels/targets/folds;
- create the final output or child root;
- perform a full disposable foundation build.

Add tests that monkeypatch expensive entry points to fail if preflight touches them.

#### B. Ordinary build retains output for both readiness states

`research foundation build` must:

- publish a structurally valid foundation whether readiness is qualifying or nonqualifying;
- return exit 0 for either truthful readiness state;
- print the exact output path, build identity, readiness state, causes, and compact coverage summary;
- leave downstream authorisation to later verification/promotion/F2 gates.

#### C. Remove rehearsal from the normative path

Preferred implementation:

- remove the public `foundation rehearse` command; or
- retain it only as an explicitly named performance/fault-injection command requiring an explicit discard flag and remove it from normal documentation.

A private test helper may still exercise disposable builds. Deleting useful output must not be represented as an assurance requirement.

#### D. Claim-scoped late failure

A failure after the foundation manifest and children have been successfully published—such as terminal rendering, an optional report, or wrapper bookkeeping—must not delete or invalidate that foundation. Publication and presentation are separate claims.

The writer may still remove its own incomplete staging/partial child output before atomic publication.

### Tests required

1. preflight rejects bad configuration/checkpoint/output before any row decode;
2. preflight performs no publication;
3. a qualifying build publishes;
4. a nonqualifying build also publishes and exits 0;
5. downstream readiness checking still rejects nonqualifying authority;
6. a simulated post-publication rendering/bookkeeping failure leaves the foundation intact;
7. an internal pre-publication child failure removes only incomplete output;
8. existing checkpoint reuse remains accepted;
9. no public normal workflow performs a full build and then deletes it.

### Validation

```bash
uv run pytest -q tests/test_ibkr_foundation.py tests/test_research_cli.py
ops/dev/verify.sh
git diff --check
```

### Operational handoff after merge

This is the first point at which the real checkpoint may be used.

1. Create a detached worktree at the exact merged S8.2 SHA.
2. Run `foundation preflight` using the existing provider manifest, configuration, checkpoint root, and a new create-only output path.
3. Confirm preflight reports checkpoint reuse and performs no expensive work.
4. If the checkpoint identity is rejected, **stop**. Do not delete it and do not start a fresh full build. Fix compatibility first.
5. Run `foundation build`, not rehearsal, using the existing checkpoint.
6. Preserve the resulting foundation regardless of readiness.
7. Record the ten-gap report and per-block coverage from the published payload.
8. Do not run the old multi-hour independent verifier yet; wait for S8.3.

Expected operational outcome:

```text
PUBLISHED_UNVERIFIED_FOUNDATION
```

with a truthful readiness state. No downstream R2 authority exists yet.

---

## PR S8.3 — Add reusable Stage 8 verification receipts and cheap authentication

### Suggested metadata

```text
branch: feat/stage8-verification-receipt
title:  Persist and reuse Stage 8 semantic verification evidence
```

### Goal

Pay the complete Stage 8 semantic replay cost once for an exact foundation, persist the result, and make all ordinary later consumers authenticate exact bytes plus that receipt instead of replaying the foundation again.

### Scope

Expected files:

```text
src/qtrad/domain/ibkr_foundation.py
src/qtrad/runtime/ibkr_foundation.py
src/qtrad/runtime/ibkr_foundation_bounded.py
src/qtrad/application/r2_ibkr_historical.py
src/qtrad/runtime/r2_ibkr_verification.py
src/qtrad/__main__.py
tests/test_ibkr_foundation.py
tests/test_r2_ibkr_historical.py
tests/test_research_cli.py
```

This PR may change checkpoint/verifier identities because the current foundation must already have been published under S8.2.

### Required design

#### A. New create-only receipt

Add a bounded canonical contract, for example:

```text
qtrad-ibkr-foundation-verification-v1
```

It must bind:

- foundation contract/schema;
- foundation manifest byte SHA and semantic build ID;
- exact provider-history manifest/dataset identities;
- complete Stage 8 child-reference root;
- foundation configuration ID;
- Stage 8 verifier contract/version;
- claim-scoped semantic verifier identity;
- completed check set;
- verified readiness payload identity;
- evidence profile `IMPLEMENTATION_EVIDENCE_ONLY` (or the repository’s existing equivalent);
- verification completion time as non-identity metadata, if timestamps are retained.

The receipt must not contain rows or holdout outcomes.

#### B. Separate operations

Provide three explicit operations:

```text
foundation build
foundation verify --receipt-output <new-file>
foundation authenticate --receipt <file>
```

- `build`: creates the foundation; makes no independent-verification claim.
- `verify`: independently replays the Stage 8 transformation from authenticated immediate Stage 7 evidence and writes one receipt.
- `authenticate`: re-hashes/rechecks the exact foundation closure and receipt, restores the compact verified result, and performs no data-scale semantic replay.

Do not leave ordinary callers using `load_ibkr_foundation()` if that name silently triggers a full replay. Rename/internalise the deep path or make call sites explicit.

#### C. Boundary-local source handling

Until Stage 7 PR S7.1 publishes a first-class Stage 7 receipt, Stage 8 verification may use the existing authenticated source-verification checkpoint/receipt. The consumer interface must accept an external Stage 7 receipt once S7.1 supplies it.

Do not make Stage 8 receipt validity depend on Stage 8 checkpoint existence. Checkpoints are disposable acceleration state; the published foundation plus receipt is the evidence.

#### D. Claim-scoped semantic identity

Replace whole-large-module hashing for verification authority with a claim-scoped identity. Acceptable approaches:

- explicit versioned semantic constants reviewed with each semantic change; or
- a digest over a small dedicated semantic module and exact relevant policy constants/functions.

Repository SHA/image digest remains provenance but is not the ordinary cache invalidation key.

#### E. Downstream integration

R2 IBKR historical adaptation/F2 setup must consume `foundation authenticate`, not deep verification. A mutated child, manifest, receipt, provider root, configuration, or verifier identity must reject before R2 work.

### Tests required

1. deep verify writes one create-only receipt;
2. receipt round-trip restores exactly the verified readiness/build identity;
3. authenticate does not invoke Stage 7 semantic replay or Stage 8 derivation;
4. manifest, child, provider root, receipt, check-set, verifier identity, and readiness mutations reject;
5. missing or orphaned files reject;
6. a verifier-version change requires reverification, not data rebuild;
7. checkpoint deletion does not affect published receipt authentication;
8. downstream R2 source loading uses authenticate only;
9. the receipt cannot be upgraded to confirmatory authority by changing a field and rehashing;
10. existing outcome-blind/holdout protections remain unchanged.

### Validation

```bash
uv run pytest -q \
  tests/test_ibkr_foundation.py \
  tests/test_r2_ibkr_historical.py \
  tests/test_r2_verification_boundaries.py \
  tests/test_research_cli.py
ops/dev/verify.sh
git diff --check
```

### Operational handoff after merge

From a detached exact-head worktree:

1. run the new deep `foundation verify` **once** against the S8.2-published foundation, using a
   new or matching verifier replay-checkpoint directory rather than the retained construction
   checkpoint;
2. write the receipt create-only;
3. run `foundation authenticate` and prove it is cheap;
4. record exact manifest, build, receipt, verifier, and provider identities;
5. do not rerun the deep verifier unless S8.3 itself reports a defect or the verifier is revoked.

Expected state:

```text
VERIFIED_FOUNDATION
```

Readiness may be qualifying or nonqualifying. No confirmatory authority yet.

---

## PR S8.4 — Add explicit confirmatory promotion and require it at real F2

### Suggested metadata

```text
branch: feat/stage8-confirmatory-promotion
title:  Add confirmatory promotion for IBKR historical foundations
```

### Goal

Implement the ADR 0029 amendment’s stronger boundary: complete cumulative replay once from an immutable runtime before an exact foundation may authorise real F2/holdout-facing confirmatory work.

### Scope

Expected files:

```text
src/qtrad/domain/ibkr_foundation.py
src/qtrad/runtime/ibkr_foundation.py
src/qtrad/runtime/r2_ibkr_verification.py
src/qtrad/application/r2_ibkr_historical.py
src/qtrad/__main__.py
tests/test_ibkr_foundation.py
tests/test_r2_ibkr_historical.py
tests/test_r2_verification_boundaries.py
docs/R2_IBKR_HISTORICAL_PLAN.md
```

### Required design

#### A. Promotion attestation

Add a create-only bounded contract, for example:

```text
qtrad-ibkr-foundation-confirmatory-promotion-v1
```

It must bind:

- exact Stage 6 aggregate/result roots;
- exact Stage 7 provider-history roots and accepted verifier identity;
- exact Stage 8 foundation root/build ID and Stage 8 verifier identity;
- exact readiness payload and qualifying state;
- complete cumulative check set;
- detached clean q-trad commit;
- immutable image digest where the existing runtime policy requires it;
- promotion profile `CONFIRMATORY`;
- operator authorisation metadata under existing repository conventions.

Promotion does not rebuild Stage 6, Stage 7, or Stage 8 artefacts.

#### B. Runtime requirements

The command must fail before data-scale replay when:

- checkout is dirty;
- current commit differs from the expected commit;
- image/runtime identity differs where required;
- foundation receipt is absent/invalid;
- readiness is nonqualifying;
- output exists;
- another promotion output claims the same path.

A developer’s ordinary checkout changing elsewhere after the detached process starts is irrelevant. The operation is bound to its immutable worktree/image.

#### C. F2 gate

Real IBKR F2 must require the promotion attestation. Implementation-only fixture paths may retain their existing explicit evidence class, but must not silently accept an ordinary Stage 8 receipt as confirmatory.

### Tests required

1. qualifying exact foundation promotes;
2. nonqualifying foundation cannot promote but remains valid evidence;
3. dirty/mismatched runtime fails before replay;
4. changed Stage 6/7/8 root rejects;
5. ordinary verification receipt cannot masquerade as promotion;
6. promotion is create-only and deterministic apart from declared non-identity metadata;
7. real F2 requires promotion;
8. no holdout feature or outcome is decoded during promotion;
9. a later unrelated repository commit does not revoke an existing exact promotion attestation;
10. a revoked verifier identity blocks promotion/requires re-verification, not rebuild.

### Validation

```bash
uv run pytest -q \
  tests/test_ibkr_foundation.py \
  tests/test_r2_ibkr_historical.py \
  tests/test_r2_verification_boundaries.py \
  tests/test_r2_confirmatory.py
ops/dev/verify.sh
git diff --check
```

### Operational decision

Run real promotion only if S8.1’s corrected coverage policy says the current foundation is qualifying. If not, retain the verified nonqualifying foundation and stop. Do not reacquire automatically.

---

# Stage 7 PR sequence

Stage 7 work starts only after the current Stage 8 foundation has been published and its S8.3 receipt safely retained. This prevents Stage 7 contract/layout work from stranding the current expensive checkpoint.

## PR S7.1 — Persist the existing Stage 7 semantic verification result as a first-class receipt

### Suggested metadata

```text
branch: feat/stage7-verification-receipt
title:  Persist reusable provider-history verification evidence
```

### Goal

Turn the already implemented provider-history source-verification receipt into a normal Stage 7 output, so Stage 8 and future consumers can authenticate it without recreating the complete Stage 6→7 semantic proof.

### Scope

Expected files:

```text
src/qtrad/domain/provider_history.py
src/qtrad/runtime/provider_history.py
src/qtrad/__main__.py
src/qtrad/runtime/ibkr_foundation.py
src/qtrad/runtime/ibkr_foundation_bounded.py
tests/test_provider_history.py
tests/test_ibkr_foundation.py
tests/test_research_cli.py
docs/IBKR-HISTORICAL-ACQUISITION.md
```

### Required design

#### A. Public receipt persistence

Use or evolve the existing compact source-verification payload into a create-only bounded Stage 7 receipt. It must bind:

- provider-history manifest byte SHA;
- provider-history dataset semantic identity;
- Stage 6 plan and aggregate/result identities;
- declared availability policy;
- request evidence summary;
- observation interval summary;
- provider-history verifier contract/version;
- claim-scoped semantic verifier identity;
- completed check set.

It must be configuration-independent. No Stage 8 foundation configuration ID belongs in this receipt.

#### B. Normal Stage 7 command path

The normal build path should perform the existing deep independent verification once and persist its receipt. Do not immediately invoke the same deep verifier again in a second process merely to recreate the same result.

A supported shape is:

```text
build-provider-history --output <directory> --verification-receipt <new-file>
```

The existing standalone verifier remains for explicit re-verification, verifier upgrades, and audits. Add a cheap receipt authentication command or mode.

#### C. Stage 8 consumption

Stage 8 source verification accepts the external Stage 7 receipt and:

- authenticates exact Stage 7 bytes/tree relevant to the consumed source;
- authenticates the receipt;
- restores compact semantic source evidence;
- does not replay Stage 6.

The old Stage 8-internal source receipt remains readable only as needed for current evidence/migration tests; do not maintain two indefinite authorities.

#### D. Semantic verifier identity

Replace `hash(the entire provider_history.py file)` with a claim-scoped version/digest. Logging, CLI output, comments, or unrelated helpers must not invalidate an otherwise applicable Stage 7 receipt.

### Tests required

1. normal Stage 7 build performs exactly one deep semantic verification;
2. it writes a create-only receipt bound to the final renamed closure;
3. cheap authentication does not decode Parquet rows or Stage 6 request-result children;
4. explicit deep reverify still works;
5. verifier version change invalidates receipt applicability but leaves data intact;
6. availability policy, dataset, manifest, source aggregate, request evidence, or summary mutation rejects;
7. Stage 8 consumes the external receipt without Stage 6 replay;
8. Stage 8 configuration changes do not invalidate the Stage 7 receipt;
9. a receipt-write failure leaves the immutable Stage 7 artefact present but without a verification claim;
10. a moved ordinary checkout does not alter artefact validity; exact execution provenance is recorded separately.

### Validation

```bash
uv run pytest -q tests/test_provider_history.py tests/test_ibkr_foundation.py tests/test_research_cli.py
ops/dev/verify.sh
git diff --check
```

### Operational handoff

Use an immutable worktree to deeply reverify the existing Stage 7 artefact once and issue the new receipt. Do **not** rebuild Stage 7. After authentication succeeds, Stage 8 variants should consume this receipt.

---

## PR S7.2 — Remove redundant Stage 7 verification invocations and make failure claim-scoped

### Suggested metadata

```text
branch: fix/stage7-single-deep-verification
title:  Eliminate redundant provider-history semantic replay
```

### Goal

Make the Stage 7 workflow perform data-scale transformation once and independent semantic verification once, with all later checks receipt-based.

### Scope

Expected files:

```text
src/qtrad/runtime/provider_history.py
src/qtrad/__main__.py
tests/test_provider_history.py
tests/test_research_cli.py
docs/IBKR-HISTORICAL-ACQUISITION.md
```

### Required design

#### A. Publication phases

Separate and name:

```text
BUILDING
PUBLISHED_UNVERIFIED
VERIFIED
```

The builder must retain an atomically published closure even if later semantic verification or receipt writing fails. The absence of a valid receipt prevents downstream authority but does not make unchanged published bytes disappear.

#### B. Staging checks

Before atomic rename, run only checks needed to ensure a safe complete publication:

- canonical manifest encoding;
- exact staged tree;
- bounded regular files and safe paths;
- hashes and row/footer counts already produced during writing;
- source/partition references consistent with construction state.

Do not run a second complete semantic reconstruction merely to decide whether staging may be renamed.

After publication, run the independent semantic verifier once and issue the S7.1 receipt.

#### C. Explicit audit modes

Provide clear modes:

```text
verify-provider-history --receipt <file>       # cheap normal authentication
verify-provider-history --deep --receipt-output <new-file>
```

Names may follow existing CLI conventions, but ordinary use must not accidentally choose the deep path.

### Tests required

1. build does not call the deep verifier during staging;
2. normal build+verify calls deep replay exactly once total;
3. a semantic verification failure retains `PUBLISHED_UNVERIFIED` output;
4. a staging closure/hash/footer failure publishes nothing;
5. receipt write failure retains verified bytes but no usable receipt;
6. cheap normal authentication performs no row decode/source replay;
7. explicit deep audit performs full replay;
8. overwrite, symlink, orphan, missing child, and mutation protections remain;
9. CLI status/output distinguishes publication from verification;
10. no wrapper performs a redundant standalone deep verifier after a successful receipt-producing build.

### Validation

```bash
uv run pytest -q tests/test_provider_history.py tests/test_research_cli.py
ops/dev/verify.sh
git diff --check
```

### Performance acceptance

On a controlled representative fixture, log separately:

- Stage 6 source authentication time;
- Stage 7 transform/write time;
- Stage 7 deep verification time;
- receipt authentication time.

The ordinary second-use path must be proportional to bounded metadata/selected byte hashing, not Stage 6/7 semantic replay.

---

## PR S7.3 — Introduce provider-history v2 with coarse physical parts and prunable Stage 8 reads

### Suggested metadata

```text
branch: perf/provider-history-v2-layout
title:  Coarsen provider-history storage and decouple semantic identity from layout
```

### Goal

Replace approximately one file per instrument/day with a bulk-history layout suitable for repeated instrument/time-range experiments, without any provider reacquisition.

### Precondition

Do not begin implementation until S7.1 and S7.2 receipts are merged and the existing v1 Stage 7 artefact has an accepted receipt.

### Contract direction

Introduce a new schema/contract version rather than silently changing v1.

Preferred physical grouping:

```text
instrument × calendar month
```

Use a bounded representative benchmark to confirm that resulting files remain comfortably below the existing maximum file size. If an instrument-month would exceed the bound, deterministically split it into numbered target-sized parts. Do not return to daily files.

### Required design

#### A. Separate semantic dataset identity from physical manifest identity

For v2:

- semantic dataset identity binds ordered observation semantics, lineage, source contract, availability policy, and logical coverage;
- physical manifest identity binds paths, byte hashes, Parquet encoding, row counts, and part boundaries;
- repacking unchanged observations changes the physical manifest identity but not the semantic dataset identity.

This is a contract change and requires explicit mutation tests.

#### B. Indexed part metadata

Each physical part reference must include enough authenticated metadata for deterministic pruning:

- instrument ID;
- minimum interval start;
- maximum interval end;
- row count;
- ordered-row semantic digest;
- byte digest;
- bounded canonical relative path;
- part ordinal where one month splits.

Daily/session/gap coverage remains semantic evidence and need not equal file boundaries.

#### C. Streaming construction

Construction must hold at most one bounded physical part plus bounded summaries at a time. Do not materialise the full dataset or all rows for an instrument.

Hash semantic rows and physical bytes while writing so later structural checks do not reopen every just-written file unnecessarily.

#### D. Migration/repack command

Add an offline command similar to:

```text
research observations repack-provider-history \
  --manifest <v1-manifest> \
  --verification-receipt <v1-receipt> \
  --output <new-v2-directory> \
  --verification-receipt-output <new-v2-receipt>
```

It must:

- authenticate v1 through its exact receipt;
- decode each v1 observation once;
- write v2 create-only;
- independently verify v2 once;
- never replay Stage 6 and never call IBKR;
- preserve v1 evidence unchanged.

#### E. Stage 8 pruning

Adapt Stage 8 to select only physical parts intersecting its authenticated instrument set and configured time range before decoding rows.

Rules:

- selected part byte hashes are rechecked;
- selection/view identity binds parent semantic dataset ID, parent manifest ID, instruments, range, and exact selected part references;
- no out-of-range or unselected instrument rows reach derivation;
- unselected parts are not decoded;
- the accepted parent receipt establishes the original whole-dataset semantic proof.

A separate persisted view artefact is optional; prefer a deterministic compact selection identity unless a second concrete caller requires a durable view contract.

#### F. Compatibility

Before the first decision-grade result, a clean v2 migration is preferred over indefinite dual-reader complexity. Retain only the minimum v1 reader needed to authenticate/migrate existing evidence. Document the removal condition.

### Tests required

1. a 26-week fixture no longer creates one part per day;
2. deterministic month grouping and split ordinals;
3. semantic dataset ID is invariant under equivalent physical repacking;
4. physical manifest ID changes when layout/encoding changes;
5. row mutation changes semantic ID;
6. path/hash/footer/row-count/orphan mutations reject;
7. streaming memory topology remains bounded;
8. Stage 8 decodes only selected instrument/range parts;
9. selected-part mutation rejects;
10. no Stage 6 replay occurs during v1→v2 repack;
11. v1 and v2 produce identical ordered observation semantics and Stage 8 panel/target/fold identities for the same configuration;
12. gaps, sessions, availability, source class, and correction policy remain unchanged;
13. create-only and race protections remain;
14. output file sizes stay within bounds.

### Validation

Focused:

```bash
uv run pytest -q tests/test_provider_history.py tests/test_ibkr_foundation.py
```

Complete:

```bash
ops/dev/verify.sh
git diff --check
```

Performance evidence in PR description:

- fixture file count before/after;
- transform time before/after;
- deep verify time before/after;
- Stage 8 selected-range decode count;
- peak RSS;
- no semantic identity drift between v1/v2 observations.

### Real-data rollout after merge

1. detached exact-head worktree;
2. authenticate v1 Stage 7 plus receipt;
3. repack to a new v2 directory—never overwrite v1;
4. deep verify v2 once and retain receipt;
5. compare semantic dataset IDs/row summaries as defined by the v2 migration proof;
6. run one Stage 8 build over a deliberately bounded instrument/time subset to prove pruning;
7. only then use v2 for broader experiments.

Do not rebuild the already retained current Stage 8 v1 foundation merely to migrate storage. It remains valid evidence under its original identities.

---

# 2. Cross-PR review and CI protocol

For every PR:

1. Determine exact base SHA and record it.
2. Keep the branch rebased/merged from current `main` before final validation.
3. Run focused tests during iteration; run `ops/dev/verify.sh` once at the final candidate head.
4. Inspect CI for the exact head SHA.
5. Recheck PR head immediately before posting the review-ready handoff.
6. PR description must state:
   - exact contract changed;
   - exact claims preserved;
   - invalidation scope;
   - whether current Stage 8 checkpoint compatibility is preserved;
   - focused/full validation results;
   - no provider calls / no holdout / no effectiveness claim.
7. Update `docs/ARCHITECTURE.md` in the same PR when the change makes one of this plan's durable
   implemented flows true; do not leave architecture consolidation to the final archival PR.
8. Do not merge a PR with an unexplained data-scale regression, receipt bypass, broader invalidation
   key, or missing required architecture update.

---

# 3. Stop conditions

An agent must stop and report rather than improvising when any of these occurs:

- S8.1 or S8.2 rejects the existing Stage 8 checkpoint because of code identity drift;
- block coverage cannot be derived from the same authenticated policy used by R2 readiness;
- the ten gaps expose a timestamp/session-boundary defect that changes panel/target semantics;
- a proposed receipt omits an immediate input capable of changing the protected claim;
- an ordinary loader still performs a deep replay after a receipt has been authenticated;
- a verifier change would require rebuilding unchanged data rather than re-verifying it;
- v2 migration cannot prove semantic equality with v1;
- real-data work would require a provider call;
- any code path reaches R2 registration or holdout access.

---

# 4. Expected programme outcomes

After S8.1–S8.3:

- the current Stage 8 foundation is retained;
- its ten gaps are quantified by candidate/block coverage;
- the readiness cause is truthful;
- a complete Stage 8 verification is paid once and represented by a reusable receipt;
- ordinary downstream authentication is cheap.

After S8.4, if readiness qualifies:

- one immutable-runtime promotion attestation authorises real IBKR F2;
- no descendant repeats the same cumulative replay.

After S7.1–S7.2:

- Stage 7 no longer pays a redundant standalone deep verification;
- Stage 8 consumes durable Stage 7 verification evidence;
- verifier or reporting changes do not force data rebuilds.

After S7.3:

- canonical historical data is stored in roughly hundreds rather than thousands of physical files;
- instrument/time-range Stage 8 work decodes only relevant parts;
- repacking is separated from semantic dataset meaning;
- future historical experiments reuse one canonical verified source without provider reacquisition.

---

# 5. Deliberately deferred work

Do not include in these PRs:

- redesign of Stage 6 acquisition or its immutable correction policy;
- general R1/R2 recursive-verification audit;
- model architecture changes;
- portfolio/cost work;
- new datastores, queues, services, or deployable images;
- provider-history augmentation or merging across plans;
- direct WSL-versus-devcontainer migration.

After S7.3, run a separate architecture review to locate the same recursive-verification and over-broad identity patterns elsewhere in R1/R2.
