# q-trad evidence-handoff simplification implementation plan

**Programme objective:** before the first real confirmatory R2 run, replace recursive/cumulative
verification and mixed semantic/physical identities from Stage 6 through R2 with the smallest
trustworthy handoff model: each boundary transforms once, independently verifies its own claims once,
issues a reusable receipt, and ordinary descendants authenticate that receipt without repeating parent
semantic proof.

This is an internal single-user research system. Backward compatibility is not a product requirement.
Temporary migration support exists only where named retained evidence must be moved to the new contract,
and is deleted immediately after that migration succeeds.

## 1. Preconditions

No PR in this programme may perform provider reacquisition, call IB Gateway for historical data, access
real holdout outcomes or introduce an order/trading surface unless the plan is explicitly amended first.

## 2. Target architecture

The target ordinary path is:

```text
Stage 6 result
  build once
  publish structurally
  deep-verify Stage 6 once
  -> Stage 6 verification receipt

Stage 7 provider history
  authenticate Stage 6 receipt
  consume Stage 6 result children once
  transform once
  publish structurally
  deep-verify Stage 7 once
  -> Stage 7 verification receipt

Stage 8 historical foundation
  authenticate Stage 7 receipt
  consume selected Stage 7 parts only
  transform once
  publish structurally
  deep-verify Stage 8 once
  -> Stage 8 verification receipt
  -> optional confirmatory promotion (no semantic replay)

R1/native foundation
  transform once
  publish structurally
  deep-verify R1 once
  -> R1 verification receipt

R2 OOF
  authenticate immediate foundation authority
  calculate R2 once
  publish structurally
  deep-verify R2 once
  -> R2 OOF verification receipt
  -> optional F2 confirmatory promotion (no semantic replay)

G1 -> G2 -> R2.H
```

Every child binds parent semantic/closure/verification or promotion identities. No ordinary child
copies a complete parent closure or recursively proves the parent's ancestors.

## 3. Global hard constraints

These apply to every PR below.

### 3.1 Scientific/safety invariants that must not change

- source-class separation;
- causal availability and target maturity;
- target/fold chronology and purging/embargo;
- holdout exclusion from feature/model/calibration/selection decisions;
- exact evidence/source-class readiness gates unless a separate scientific plan changes them;
- outcome-blind G1/G2 interfaces;
- create-only immutable selection evidence;
- marker-first `OPENED` before holdout outcome access;
- irreversible `CONSUMED` semantics;
- exact-byte verification of evidence actually consumed; and
- no broker-order or real-capital path.

### 3.2 Simplicity constraints

- one real user; do not implement hypothetical consumers;
- no dual writers;
- no permanent legacy readers merely because old fixtures exist;
- migration bridge must name its retained evidence and deletion PR;
- do not copy parent closures for portability;
- do not hash or decode unused parent bytes merely because they exist;
- do not solve unnecessary work with another cache before removing that work;
- no generic artefact/receipt framework or verifier registry;
- prefer explicit functions and frozen values local to each boundary;
- a contract version bump does not imply an old reader remains supported; and
- Git history, not the active runtime, archives retired implementation.

### 3.3 Required identity vocabulary

For each persisted boundary, distinguish:

- `semantic_id`: scientific/domain meaning;
- `closure_id`: exact physical representation owned by that boundary;
- `verification_id`: receipt proving the named semantic checks were completed; and
- `promotion_id`: authority to cross a consequential boundary.

Execution provenance is separate. Whole q-trad commit/image identity must not become scientific
identity unless it actually changes the claimed computation.

### 3.4 Verification work-count target

Ordinary downstream operations should invoke zero parent semantic verifiers.

Use spies/counters to prove this. Wall-clock measurements are diagnostic only.

## 4. Programme graph and parallelism

```text
PR-00 Governance / ADR
      |
      +-----------------------+-----------------------+
      |                       |                       |
      v                       v                       v
PR-H1 Stage 6            PR-F1 R1                PR-I1 R2 identities
      |                       |                       |
      v                       |                       |
PR-H2 Stage 7                 +-----------+-----------+
      |                                   |
      v                                   |
PR-H3 Stage 8                           PR-R1 R2 handoff
      |                                   |
      v                                   +----------+----------+
PR-H4 migrate/delete                              |          |
                                                 v          v
                                             PR-R2       PR-R3
                                             OOF receipt provenance/
                                                         software bundle
                                                 +----------+----------+
                                                            |
                                                            v
                                                        PR-R4 F2/G1
                                                            |
                                      PR-H4 ----------------+
                                                            |
                                                            v
                                                       PR-C1 converge
```

Immediately after PR-00, three agents can work independently on PR-H1, PR-F1 and PR-I1.

PR-H2 and PR-H3 are serial within the historical chain. PR-R1 may be developed in parallel once the
new R1 and R2 identity interfaces are fixed, but must not merge until the new Stage 8 interface needed
by the IBKR historical path is available.

PR-R2 and PR-R3 can run in parallel after PR-R1 if file ownership is kept separate.

---

# PR-00 — Adopt efficient evidence handoff as current architecture

## Purpose

Make the simplification rules authoritative before implementation begins so later agents do not
re-introduce cumulative replay, compatibility or generic abstractions while attempting to be
"safe".

## Dependencies

- PR #123 merged.

## Expected files

Hard minimum:

- `docs/adr/0029-ibkr-historical-acquisition-evidence-boundaries.md`;
- `docs/ARCHITECTURE.md`;
- `PLAN.md`; and
- this implementation plan as an active document, for example
  `docs/R2_EVIDENCE_HANDOFF_SIMPLIFICATION_PLAN.md`.

Do not edit `docs/STATUS.md` merely to narrate planned work unless its current-state claims actually
change.

## Required ADR 0029 Amendment 2

Amend, do not create a competing ADR. Amendment 2 must supersede the parts of Amendment 1 that still
require cumulative replay at confirmatory promotion.

Record these decisions:

1. **Immediate-parent proof rule.** A boundary authenticates accepted immediate-parent verification
   evidence and independently proves only claims introduced by the current boundary.
2. **Prove once, authenticate many.** A successful deep verifier issues durable create-only evidence;
   ordinary descendants consume that evidence rather than re-running the same verifier.
3. **Promotion is authority, not verification.** Confirmatory promotion authenticates an accepted
   verification receipt, required readiness/evidence class and operator authorisation. It does not
   replay already-proved semantic ancestry.
4. **Deep audit is exceptional.** Re-run semantic verification when a verifier is revoked, a discovered
   defect may affect an established claim, or the operator explicitly requests an audit.
5. **Lazy exact-byte authentication.** A manifest/receipt establishes the declared closure and expected
   byte identities. Ordinary consumers hash and validate exact child bytes when they consume those
   children. They need not hash every unused child on every call.
6. **Four identity classes.** Semantic, closure, verification and promotion identities are distinct;
   provenance is separate.
7. **Single-user proportionality.** No signatures/PKI/transparency log or hostile-operator threat model.
8. **Compatibility proportionality.** Before the first decision-grade result, old internal contracts are
   retained only for a specifically named migration/evidence need and are removed after migration.

Update any existing ADR wording that says complete cumulative replay remains required for confirmatory
promotion so there is no contradictory active text.

## Architecture/PLAN updates

Describe the target handoff, but do not claim implementation is complete.

Add a concise active-work statement that the Stage 6→R2 evidence architecture is being simplified
before real confirmatory R2 execution.

## Validation

- documentation/link checks used by repository;
- `git diff --check`;
- run the relevant static/doc assertions if present.

Full `ops/dev/verify.sh` is optional for a documentation-only PR unless existing repository policy
requires it.

## Exit gate

The merged docs must make it impossible to read current authority as requiring ordinary cumulative
replay or permanent internal backward compatibility.

---

# PR-H1 — Replace Stage 6 aggregate identity and add reusable Stage 6 verification

## Purpose

Fix Stage 6 before rebuilding the retained historical chain. Separate semantic result identity from
physical closure identity, decouple publication from semantic verification, and make Stage 6 semantic
proof reusable by Stage 7.

## Parallel lane

May run immediately after PR-00 in parallel with PR-F1 and PR-I1.

## Primary ownership

Expected files, subject to exact-head inspection:

- `src/qtrad/domain/ibkr_results.py`;
- `src/qtrad/application/ibkr_results.py`;
- `src/qtrad/runtime/ibkr_results.py`;
- Stage 6 CLI wiring in `src/qtrad/__main__.py`;
- Stage 6 tests; and
- one narrowly named temporary legacy-migration helper if required.

Do not touch Stage 7/8 or R1/R2 except compile/test fixture fallout that cannot be avoided.

## Current defect to remove

The current aggregate semantic identity includes physical child references containing paths and byte
hashes, and publication runs the complete semantic verifier before rename. This couples meaning to
representation and repeats the semantic proof before downstream Stage 7 work.

## Contract design

Create the next Stage 6 aggregate/result contract. Versioning is a clean replacement, not a promise to
support both indefinitely.

Recommended shape:

### Request-result children

Keep the existing request-result semantic contract if it is already correct. A request-result semantic
identity may continue to bind its planned request, attempts, callbacks, completion markers, accepted
rows/sessions, terminal/evidence disposition and acquisition timestamps because those are the semantics
being claimed.

Do not change that contract merely for symmetry.

### Stage 6 semantic aggregate

Create a semantic `result_id` that binds meaning, for example:

- plan semantic identity;
- canonical ordered request-result semantic IDs;
- coverage summary;
- entitlement summary; and
- any acquisition policy identity that actually changes the meaning of the accepted result.

Do not put these into `result_id` solely as provenance:

- child paths;
- child byte SHA-256s;
- JSON representation;
- publication directory;
- q-trad commit/image unless its semantics genuinely change the result contract.

### Stage 6 closure

The manifest must separately bind a `closure_id` over exact owned bytes/references:

- plan path + bytes SHA;
- request child path + contract + expected semantic ID + bytes SHA;
- manifest representation/version; and
- exact declared tree.

Keep the implementation direct; do not invent a repository-wide `ArtifactReference` framework.

## Publication lifecycle

Replace:

```text
stage -> full semantic verify -> rename
```

with:

```text
construct in-memory result
-> write staging closure
-> bounded structural/canonical/reference checks only
-> atomic create-only publication
-> PUBLISHED_UNVERIFIED
```

Structural publication checks may validate canonical JSON, safe paths, declared child counts, exact
reference sets and bounded file sizes. They must not call the aggregate semantic replay.

A semantic-verification failure after publication leaves the immutable closure present without a valid
receipt.

If operational PostgreSQL publication status currently assumes semantic verification occurred before
publication, change that status meaning explicitly rather than hiding verification inside publication.
The database remains operational state; the create-only receipt owns semantic authority.

## Deep verifier

Retain the independent semantic machinery that reconstructs request-result/aggregate semantics from the
published files.

It must run exactly once per explicit Stage 6 verification invocation and issue:

`qtrad-ibkr-historical-result-verification-v1` (or equally clear name).

Receipt fields should be minimal and explicit:

- Stage 6 contract/version;
- `result_id`;
- `closure_id`;
- manifest SHA;
- plan semantic identity;
- verifier contract/version;
- completed check set;
- claim-scoped verifier identity if required; and
- `verification_id`/receipt SHA.

Receipt is create-only and outside the Stage 6 closure.

## Cheap authentication

Add a single current API such as:

```python
authenticate_ibkr_historical_result(manifest, receipt=...)
```

Authentication must:

- parse/canonical-check the manifest;
- derive/check declared `result_id` and `closure_id` metadata;
- enumerate/reject unexpected or missing tree entries without reading every child body;
- authenticate the receipt and accepted verifier version/check set; and
- return a typed authenticated Stage 6 header/source.

It must not call `IbkrHistoricalAggregateReplay` or request semantic replay.

## Receipt-backed streaming consumption

Provide one stream used by Stage 7 that:

- starts from the authenticated Stage 6 header;
- for each requested/consumed child, reads it once;
- checks the exact expected child byte SHA;
- canonical-decodes it;
- checks its planned request/result semantic identity; and
- yields the already-verified request-result semantics.

It must not reconstruct attempts/callback semantics again while iterating.

## Temporary legacy migration support

The retained real Stage 6 evidence uses the old aggregate contract. It must be migrated later in
PR-H4 without provider calls.

If a legacy reader is required, isolate the smallest possible read/deep-verify/migrate helper and mark
it explicitly temporary. It must not appear in the normal CLI or normal current Stage 6 code path.

The PR description must name PR-H4 as its deletion trigger.

Do not add a second normal writer.

## Required tests

### Work-count tests

Use spies/monkeypatches to prove:

- publication semantic aggregate replay count = 0;
- explicit deep verify aggregate replay count = 1;
- authentication aggregate replay count = 0;
- authentication request-child bodies read = 0, except manifest/plan if required;
- receipt-backed stream reads/hashes each consumed child exactly once; and
- receipt-backed stream performs zero request semantic replay.

### Identity tests

Prove:

- same semantic request results under different physical paths/encoding -> same `result_id`;
- changed physical layout -> different `closure_id`;
- changed accepted request semantics/coverage -> different `result_id`;
- changed child bytes with unchanged manifest -> rejected when that child is consumed; and
- missing/orphan tree entries -> authentication fails before semantic use.

### Receipt mutation tests

Reject:

- wrong manifest;
- wrong result/closure ID;
- wrong verifier version;
- incomplete completed-check set;
- mutated receipt body with recomputed self-hash where the verifier/binding is not accepted; and
- receipt placed inside the immutable Stage 6 closure.

## Not in scope

- Stage 7 format;
- Stage 8;
- provider reacquisition;
- changing request planning/pacing/canary semantics;
- generic verification framework.

## Validation

Focused Stage 6 suite, static/type checks, then clean `ops/dev/verify.sh`.

## PR description must include

- before/after semantic replay counts;
- identity field classification;
- temporary legacy support and PR-H4 deletion trigger; and
- confirmation that no provider call or real evidence mutation occurred.

---

# PR-F1 — Replace the R1 foundation bundle lifecycle with semantic/closure identity and one reusable proof

## Purpose

Remove the R1 analogue of the old Stage 7 pattern: mixed semantic/physical bundle identity,
construction-time verification followed by another independent replay, and repeated child semantic
reconstruction.

## Parallel lane

May run immediately after PR-00 in parallel with PR-H1 and PR-I1.

## Primary ownership

Expected files:

- `src/qtrad/domain/foundation_bundle.py`;
- `src/qtrad/application/foundation_bundle.py`;
- `src/qtrad/runtime/foundation_bundle.py`;
- R1 foundation tests and fixtures; and
- minimal CLI changes required for receipt paths.

Avoid R2 orchestration files; PR-I1 owns R2 domain identities and PR-R1 owns R2 runtime handoff.

## Compatibility rule

There is no normal old R1 bundle compatibility. Replace the current bundle contract with the clean
current contract and update fixtures/tests.

A new contract/version may be used as an on-disk discriminator, but the old reader/writer is deleted.

## Identity design

Replace mixed `bundle_id` semantics with:

### `foundation_id`

Bind only scientific/domain meaning, including the applicable:

- source class;
- foundation configuration semantic identity;
- observation semantic dataset ID;
- availability semantic identity;
- panel dataset ID;
- target dataset ID;
- fold dataset ID;
- zero/initial forecast dataset ID if the R1 contract includes it;
- outcome-blind/G2-safe projection semantic IDs;
- ordered instrument/range semantics required by the contract.

Do not bind:

- paths;
- manifest byte hashes;
- physical Parquet representation;
- build timestamp;
- whole q-trad commit/image;
- child manifest IDs that themselves are physical closure IDs.

### `closure_id`

Bind the exact R1-owned manifested children and byte identities.

Do not include parent source closures that R1 does not own.

## Construction lifecycle

`persist_foundation_bundle()` should:

1. receive/build the in-memory semantic children;
2. perform bounded internal relationship checks needed to safely persist them;
3. write create-only child artefacts/manifests;
4. write the bundle manifest with `foundation_id` and `closure_id`; and
5. return publication evidence.

It must not call the full independent `verify_foundation_bundle()`.

`build_foundation_bundle()` may verify cheap cross-object invariants already in memory, but must not
reconstruct full datasets solely to recompute identities that construction just produced.

## Deep verifier

Retain one independent R1 semantic verifier that proves:

- observation build/availability semantics;
- causal panel derivation;
- target derivation/maturity;
- chronological folds;
- zero/initial forecast semantics where applicable;
- coverage;
- cross-lineage;
- outcome-blind projections; and
- G2-safe projections.

Remove duplicate work where `verify_foundation_children()` or equivalent reconstructs semantic datasets
a second time after the independent builders have already established them.

One explicit R1 verification invocation -> one semantic replay -> one receipt.

## R1 verification receipt

Add a create-only current receipt that binds:

- R1 contract/version;
- `foundation_id`;
- `closure_id`;
- bundle manifest SHA;
- relevant child semantic IDs;
- verifier contract/version/check set;
- claim-scoped verifier identity where needed; and
- `verification_id`.

Receipt must be outside the bundle closure.

## Cheap R1 authentication

Add/standardise:

```python
authenticate_foundation_bundle(..., receipt=...)
```

It should authenticate manifest/receipt/expected current tree and validate exact child bytes when those
children are consumed. It must not invoke the causal panel/target/fold/forecast builders.

Outcome-blind and G2-safe loaders must restore their authorised views from the authenticated R1
closure/receipt without triggering a full R1 replay.

## Required tests

### Work-count

Prove:

- R1 build/persist deep verifier count = 0;
- R1 explicit deep verification count = 1;
- R1 ordinary authentication deep verifier count = 0;
- outcome-blind/G2-safe authentication full panel/target/fold replay count = 0.

### Identity

Prove:

- same semantic children with changed paths/physical encoding/provenance -> same `foundation_id`;
- changed physical child bytes/layout -> different `closure_id`;
- changed panel/target/fold semantics -> different `foundation_id`;
- changed application image alone -> same `foundation_id`;
- mutated consumed child bytes -> authentication fails.

### Holdout safety regression

Prove that outcome-blind/G2-safe views expose exactly the same allowed information as before. Do not
weaken target/outcome gating to simplify verification.

## Delete

Delete the old bundle parser/writer/compatibility tests and redundant semantic reconstruction paths.
Do not retain dual R1 contracts.

## Validation

Focused R1 suite, R2 fixture fallout as necessary, then clean `ops/dev/verify.sh`.

---

# PR-I1 — Clean-break R2 semantic identity audit

## Purpose

Separate scientific R2 identities from physical closure/provenance before changing runtime handoff.
This PR should make later R2 simplification mechanical rather than entangled with identity changes.

## Parallel lane

May run immediately after PR-00 in parallel with PR-H1 and PR-F1.

## Primary ownership

Primarily domain/configuration/manifest classes and their unit tests. Avoid major orchestration changes in
`runtime/r2_verification.py`; PR-R1 owns those.

Expected areas:

- `src/qtrad/domain/r2_readiness.py`;
- `src/qtrad/domain/r2_bundles.py` or equivalent domain bundle contracts;
- feature/preprocessing/fit/forecast/evaluation/selection manifest domain types; and
- focused R2 identity tests.

## Compatibility rule

No old R2 research contract compatibility. No real confirmatory F2 exists. Replace current internal
contracts and update fixtures.

## R2 experiment configuration

Replace semantic configuration fields that currently bind physical/provenance R1 data with a clean
scientific configuration identity.

The semantic experiment ID should bind only things that alter the experiment meaning, such as:

- immediate foundation semantic ID;
- source class;
- evidence class;
- feature definition/set;
- horizon;
- fold scientific policy/identity;
- preprocessing scientific policy;
- model family;
- hyperparameters/solver policy;
- selection/evaluation policy as applicable.

Remove from semantic identity:

- R1 physical bundle/closure ID;
- R1 path;
- R1 application version;
- R1 image digest;
- unrelated whole q-trad commit/image provenance.

Keep provenance separately where useful.

## R2 OOF identity

Replace the current mixed `bundle_id` concept with:

- `oof_id` — semantic OOF result identity; and
- `closure_id` — exact R2-owned physical bytes/references.

`oof_id` binds semantic feature/fit/forecast/evaluation children and experiment/foundation semantics.
It must not bind paths or byte hashes merely because the runtime needs them.

## Full field audit

Inspect every identity-bearing R2 contract, including at least:

- `R2ExperimentConfig`;
- raw feature manifests;
- preprocessing selection/fits;
- fold/model fits;
- forecast manifests;
- OOF bundle;
- evaluation report;
- local comparator evidence;
- selection manifests;
- software-verification/integration bundle;
- holdout selection/preparation manifests; and
- any source-specific IBKR R2 envelope.

For each persisted/hash-bearing field, classify it as:

- semantic;
- closure;
- provenance;
- verifier; or
- promotion.

Put the resulting table in the PR description. Do not add a generic class hierarchy for the
classification.

## Required tests

Semantic IDs must remain equal when only these vary:

- artifact path;
- equivalent physical representation;
- R1 build image;
- unrelated q-trad application commit;
- creation timestamp.

Semantic IDs must change when these vary:

- feature semantics;
- target/fold membership;
- model family;
- hyperparameters/solver policy;
- selection/evaluation policy;
- holdout definition;
- source class/evidence class.

Closure IDs must change on exact R2-owned byte/layout changes.

## Not in scope

- recursive replay-input removal;
- new OOF verification receipt;
- Stage 6–8;
- runtime provenance enforcement.

## Validation

Focused R2 domain/manifest tests, then clean `ops/dev/verify.sh` because this changes persisted
research contracts broadly.

---

# PR-H2 — Replace provider-history v2 with direct receipt-backed Stage 7

## Purpose

Create the steady-state Stage 7 format that consumes the new Stage 6 receipt directly and no longer
embeds/copies the Stage 6 closure or migration evidence.

## Dependency

- PR-H1 merged.

May run while PR-F1/PR-I1 or later R2 work continues.

## Primary ownership

Expected files:

- provider-history domain/application/runtime modules;
- provider-history CLI;
- provider-history tests/fixtures; and
- a minimal temporary migration reader for current retained v2 only if needed by PR-H4.

Do not modify Stage 8 except compile-level interface preparation agreed with PR-H3.

## Clean-break contract

Introduce the next provider-history contract as the sole normal writer/reader after PR-H4.

The current v2 was a migration closure that embeds Stage 6 and a migration receipt. The new contract
must remove that nesting.

## New Stage 7 closure

The Stage 7-owned closure should contain only Stage 7 evidence, primarily:

```text
manifest
monthly/split provider-history Parquet parts
```

It must not contain:

- copied Stage 6 manifest;
- copied Stage 6 plan;
- copied Stage 6 request-result children;
- copied Stage 6 verification receipt;
- v1 migration receipt/tree; or
- legacy migration bridge data.

## Parent authority binding

The Stage 7 manifest/receipt must bind the immediate Stage 6 authority by identities, not by embedding
it:

- Stage 6 `result_id`;
- Stage 6 `closure_id` where needed for exact source provenance;
- Stage 6 `verification_id`;
- plan/contract-selection semantic identity only if it is not already transitively and unambiguously
  part of Stage 6 `result_id` and Stage 7 scientific lineage.

Do not put the Stage 6 filesystem path into Stage 7 semantic identity.

## Provider-history semantic identity

Re-audit `ProviderHistoricalDataset` and row lineage under the new Stage 6 identities.

It is acceptable and likely that the new provider-history semantic ID differs from the retained v2 ID
because the contract is being corrected. Do **not** contort the new design merely to preserve the old
hash.

The migration in PR-H4 must prove semantic equivalence of the scientific rows/policy, not equality of
legacy and new IDs when the identity contract itself intentionally changed.

Provider-history semantic identity should bind:

- provider/source class;
- contract selection/product lineage needed by the scientific claim;
- Stage 6 semantic result identity;
- availability policy;
- canonical observation semantics/partition identities; and
- row count/range semantics.

Do not bind Stage 6 physical closure, application image or copied source paths as semantic meaning.

## Source summary needed by Stage 8

Stage 8 currently uses selected source/coverage/entitlement facts. Carry the minimal Stage 6-derived
summary needed for Stage 8 readiness as Stage 7 semantic evidence or receipt-bound metadata so Stage 8
never needs to reopen Stage 6.

Do not carry the entire Stage 6 operational closure just because a few summary fields are needed.

## Stage 7 build

The build command must require:

- current Stage 6 manifest; and
- current Stage 6 verification receipt.

Flow:

```text
authenticate Stage 6 header + receipt
-> iterate authenticated Stage 6 request-result children
-> hash/decode each consumed child once
-> trust already-verified Stage 6 dispositions
-> derive provider-history observations/availability
-> write bounded monthly parts
-> structural publication
```

It must not call request/aggregate semantic replay.

## Stage 7 deep verifier

The verifier requires the explicit current Stage 6 manifest/receipt as immediate-parent input for this
one proof.

It must:

1. authenticate Stage 6 receipt;
2. consume Stage 6 request-result children once;
3. independently reconstruct provider-history observations/availability and required summaries;
4. compare the published Stage 7 semantic dataset/parts; and
5. issue the Stage 7 verification receipt.

It must not re-derive Stage 6 attempt/callback dispositions.

## Stage 7 receipt

Bind:

- Stage 7 semantic dataset ID;
- Stage 7 closure ID/manifest SHA;
- Stage 6 `result_id` and `verification_id`;
- availability policy;
- required Stage 8 source summary identity;
- verifier contract/version/check set; and
- Stage 7 `verification_id`.

## Stage 7 authentication

Ordinary Stage 7 authentication after receipt creation must not require Stage 6 bytes at all.

It authenticates Stage 7 manifest/receipt and returns metadata plus a prunable part reader. Selected
parts are hashed/decoded when consumed; unselected parts are not read.

## Temporary current-v2 migration support

PR-H4 must migrate the retained current v2 evidence. If a current-v2 reader is needed for migration,
keep it explicitly temporary and outside normal construction.

Do not preserve the current v2 writer/repacker or general runtime.

## Required tests

### No recursive Stage 6 proof

Spies must prove Stage 7 build and Stage 7 verify never call:

- `IbkrHistoricalAggregateReplay`;
- request-result semantic replay; or
- Stage 6 deep verifier.

### Consumption counts

- build consumes each required Stage 6 child once;
- deep verify consumes each required Stage 6 child once;
- Stage 7 ordinary auth consumes zero Stage 6 children;
- selected Stage 7 authentication/reads decode only selected monthly parts.

### Closure tests

Reject:

- missing/orphan Stage 7 part;
- changed selected part bytes;
- changed semantic row with physical manifest rewritten consistently;
- changed availability policy;
- wrong Stage 6 verification binding at deep verification time.

## Delete

Once this PR lands, there is one normal current Stage 7 writer. Do not retain a second writer for
current v2.

Legacy migration reader deletion happens in PR-H4 after real migration.

## Validation

Focused Stage 6/7 tests, memory/work-count evidence on a representative synthetic multi-month fixture,
then `ops/dev/verify.sh`.

---

# PR-H3 — Replace Stage 8 parent handoff and make promotion replay-free

## Purpose

Make Stage 8 consume only the new Stage 7 receipt/selected parts, issue a reusable Stage 8 proof, and
turn confirmatory promotion into pure authority rather than cumulative semantic replay.

## Dependency

- PR-H2 merged.

## Primary ownership

Expected files:

- `src/qtrad/runtime/ibkr_foundation.py`;
- `src/qtrad/runtime/ibkr_foundation_bounded.py`;
- `src/qtrad/runtime/ibkr_foundation_promotion.py`;
- Stage 8 domain/application types as required;
- Stage 8 CLI wiring; and
- Stage 8/promotion tests.

PR #123 should already have removed the obsolete Stage 7 source-verification checkpoint subsystem. Do
not recreate it.

## Stage 8 contract

Use a clean current Stage 8 contract/version if parent/identity representation changes materially.
No old Stage 8 writer/reader survives after PR-H4 migration.

## Stage 8 identity

Expose explicitly:

- `foundation_id` — Stage 8 scientific foundation meaning; and
- `closure_id` — exact Stage 8-owned bundle/child representation.

`foundation_id` binds:

- source class;
- Stage 7 semantic dataset ID;
- exact Stage 7 selected-input semantic identity;
- foundation configuration semantic ID;
- Stage 8 observation/panel/target/fold semantic IDs;
- readiness semantics where part of the foundation contract; and
- outcome-blind/G2-safe semantic projections.

It does not bind:

- Stage 7 path;
- Stage 7 physical part paths as scientific meaning;
- whole q-trad image/commit;
- copied Stage 6/7 bytes.

`closure_id` binds only Stage 8-owned manifest/child bytes.

## Build handoff

Stage 8 build takes current Stage 7 manifest + receipt as explicit inputs.

Flow:

```text
authenticate Stage 7 receipt
-> derive required instrument/range part selection from authenticated Stage 7 metadata
-> hash/decode selected Stage 7 parts only
-> build Stage 8 observations/panel/targets/folds/readiness once
-> structural create-only publication
```

No Stage 6 path, manifest or receipt is accepted or read.

No Stage 7 deep verifier runs.

## Deep Stage 8 verifier

The one Stage 8 semantic verification invocation:

1. authenticates Stage 7 receipt;
2. re-reads only the Stage 7 selected parts needed by the Stage 8 configuration;
3. independently reconstructs Stage 8 observations/panel/targets/folds/readiness and protected
   projections;
4. compares Stage 8 semantic children; and
5. issues a Stage 8 verification receipt.

No Stage 7 deep verifier and no Stage 6 semantic replay.

## Stage 8 receipt

Bind:

- `foundation_id`;
- Stage 8 `closure_id`/manifest SHA;
- Stage 7 semantic dataset ID;
- Stage 7 `verification_id`;
- selected-input identity;
- configuration ID;
- readiness identity/state;
- evidence class;
- verifier contract/version/check set; and
- Stage 8 `verification_id`.

Ordinary Stage 8 authentication should not require a provider-history path/receipt once this Stage 8
receipt exists.

## Confirmatory promotion redesign

Replace cumulative S6→S7→S8 replay with:

```text
authenticate exact Stage 8 manifest + verification receipt
-> require accepted verifier/check set
-> require qualifying readiness and correct source/evidence class
-> verify create-only destination and operator authorisation fields
-> write promotion
```

Promotion must execute zero semantic replays.

Remove CLI/API arguments for Stage 7 receipt/path or replay checkpoint if they exist only to rerun
ancestry during promotion.

A separate explicit deep-audit command may remain if genuinely useful, but it must not be called by
ordinary promotion/authentication and should not be added merely to preserve old code.

## Stage 8 authentication

`authenticate_ibkr_foundation(...)` after this PR should authenticate Stage 8's own manifest/receipt and
current children as consumed. It should not reopen Stage 7 solely to establish already-receipted
semantics.

## Required tests

### Work-count

Prove:

- Stage 8 build parent deep-verifier calls = 0;
- Stage 8 deep verify Stage 7 deep-verifier calls = 0;
- Stage 8 auth Stage 7 reads = 0 except any explicitly consumed locator metadata still required by the
  final contract (prefer none);
- Stage 8 promotion semantic replay calls = 0;
- promotion Stage 6/Stage 7 bytes read = 0.

### Selection efficiency

On a multi-part fixture, prove only selected Stage 7 parts are hashed/decoded by build and deep verify.

### Authority

Reject promotion on:

- implementation-only/non-qualifying readiness;
- wrong Stage 8 receipt;
- revoked/unsupported Stage 8 verifier;
- changed Stage 8 closure;
- reused promotion output;
- malformed authorisation.

Authentication of a valid promotion must perform no semantic replay.

## Delete

Delete:

- cumulative Stage 6 replay from promotion;
- Stage 7 deep verification from promotion;
- replay checkpoint support used only by cumulative promotion;
- current old Stage 8 compatibility branches once PR-H4 migration is complete; and
- any dead source-verification caching left from older Stage 8 design.

Some final old-contract deletion may wait for PR-H4 because the real retained authority still uses it.
Mark every such temporary branch explicitly.

## Validation

Focused Stage 7/8/promotion suites, work-count regression, then `ops/dev/verify.sh`.

---

# PR-H4 — Migrate retained real Stage 6→8 evidence and delete historical compatibility

## Purpose

Use the already-retained historical data to establish the new Stage 6/7/8 authority chain without
provider reacquisition, record equivalence, then delete every temporary legacy reader/migration bridge.

This is the point where the historical lane becomes clean-break steady state.

## Dependencies

- PR-H1, PR-H2 and PR-H3 merged to `main`;
- exact `main` clean and fully verified;
- retained old Stage 6, current provider-history v2, current Stage 8 receipt and current S8.4 promotion
  still available as migration evidence.

## Operational rule

No IBKR provider call. No database reacquisition. Use retained files only.

Do not overwrite any old evidence. New artefacts go to new create-only locations.

## Migration sequence

### H4.1 Migrate Stage 6

Using the explicitly temporary legacy Stage 6 migration reader:

1. independently authenticate/deep-verify the retained old Stage 6 source under its frozen old
   contract/verifier as needed for migration;
2. create the new Stage 6 contract from the retained semantic request-result evidence;
3. publish new Stage 6 structurally;
4. run the new Stage 6 deep verifier exactly once;
5. issue the new Stage 6 receipt.

Record old and new semantic/physical identities.

Do not require identity equality where the new contract intentionally separates semantics from
physical representation.

### H4.2 Build new Stage 7

From new Stage 6 + receipt:

1. build current direct Stage 7;
2. deep-verify once;
3. issue current Stage 7 receipt.

Compare to retained provider-history v2 at the scientific level:

- exact canonical observation rows/meaning;
- availability policy and `available_at` semantics;
- instrument/range coverage;
- source/evidence dispositions used downstream;
- row counts and interval summaries.

If semantic IDs differ because the contract lineage was intentionally corrected, record why. Do not
force legacy hash equality.

### H4.3 Build new Stage 8

From new Stage 7 + receipt:

1. build current Stage 8;
2. deep-verify once;
3. issue current Stage 8 receipt.

Compare old vs new scientifically relevant Stage 8 outputs:

- observation semantics;
- panel rows/semantics;
- target rows/semantics;
- fold membership;
- source coverage/gap/readiness inputs;
- readiness state and causes;
- outcome-blind/G2-safe projections.

Again, IDs may intentionally change if identity contracts changed. The migration report must explain
identity changes rather than treating them as failure when row/policy semantics are equivalent.

### H4.4 Create new confirmatory promotion

Create the current Stage 8 promotion from the new Stage 8 receipt using the new replay-free promotion
path.

Record the new promotion ID and prove ordinary promotion/authentication performed no ancestor semantic
replay.

## Migration record

Persist a concise reviewed migration/equivalence record in the repository docs or designated evidence
location with:

- exact implementation commit;
- old/new Stage 6 semantic/closure IDs;
- old/new Stage 7 semantic/closure/verification IDs;
- old/new Stage 8 foundation/closure/verification IDs;
- old/new promotion IDs;
- explicit semantic equivalence checks performed;
- work-count evidence; and
- confirmation of zero provider calls/holdout access.

Do not copy multi-gigabyte evidence into Git.

## Code cleanup in the same PR after successful migration

Delete all temporary compatibility/migration code that no active authority needs, including as
applicable:

- old Stage 6 aggregate parser/deep verifier exposed for migration;
- old Stage 6 migration command;
- current provider-history v2 migration reader if no active result requires it;
- retained provider-history v1 cheap-auth paths if the new chain fully replaces their active authority;
- old Stage 8 parser/compatibility branch;
- old S8.4 cumulative-promotion compatibility code;
- legacy checkpoint/source-verification compatibility constants whose only purpose was old evidence;
- old fixtures/tests that exist only to keep retired runtime contracts executable.

Old files may remain archived externally. Active q-trad no longer needs to parse them.

## Hard exit condition

After PR-H4 there is exactly one normal Stage 6 contract, one normal Stage 7 contract and one normal
Stage 8 contract in the active runtime.

No production/current code path can mint authority using the superseded historical contracts.

## Validation

- migration/equivalence checks against real retained evidence;
- focused Stage 6/7/8 tests;
- exact work-count evidence;
- clean `ops/dev/verify.sh` after compatibility code deletion.

Because this PR changes the retained active authority, do not merge without the real migration record.

---

# PR-R1 — Remove recursive R2 replay-input ancestry and consume immediate parent authority

## Purpose

Eliminate the largest R2 architectural inefficiency: recursively discovering/copying parent ancestry
into OOF `replay-inputs`, hashing that copied closure, then verifying it again.

## Dependencies

- PR-F1 merged;
- PR-I1 merged;
- PR-H3 merged before this PR merges so the IBKR historical adapter can target the current Stage 8
  authority.

Development can begin earlier on a stacked branch once interfaces are stable.

## Primary ownership

Expected files:

- `src/qtrad/runtime/r2_verification.py`;
- `src/qtrad/runtime/r2_bundles.py`;
- source-specific R2 foundation adapters, including IBKR historical;
- minimal R1/Stage 8 authenticator calls; and
- R2 integration tests.

Avoid runtime/provenance refactoring owned by PR-R3 except where necessary to compile.

## Delete the recursive architecture

For the sole current R2 path, remove:

- `_declared_replay_files()` or equivalent recursive ancestry discovery;
- `_stage_replay_inputs()` or equivalent complete parent-tree copy;
- nested `replay-inputs/research/...` ancestry;
- complete parent closure duplication;
- ancestor byte-hash loops used only to re-establish already-receipted parent claims.

Do not retain an old-path fallback.

## Small R2-local parent authority

Introduce the smallest typed value needed by R2, for example:

```python
@dataclass(frozen=True, slots=True)
class AuthenticatedR2Foundation:
    foundation_id: str
    closure_id: str
    verification_id: str
    source_class: MarketDataSourceClass
    evidence_class: EvidenceClass
    semantic_inputs: ...
    promotion_id: str | None
```

Do not turn this into a generic repository-wide authority hierarchy.

Provide explicit adapters:

### Native/R1

```text
R1 bundle + R1 receipt
-> authenticate R1
-> AuthenticatedR2Foundation
```

### IBKR historical implementation evidence

```text
Stage 8 bundle + Stage 8 receipt
-> authenticate Stage 8
-> AuthenticatedR2Foundation
```

### IBKR historical confirmatory

```text
Stage 8 bundle + Stage 8 receipt + Stage 8 promotion
-> authenticate both
-> confirmatory AuthenticatedR2Foundation
```

No adapter may deep-verify its parent.

## R2 build

R2 should calculate only R2-owned evidence:

- features;
- preprocessing selections/fits;
- model fits;
- forecasts;
- coverage;
- evaluation/comparator evidence; and
- selection inputs.

Do not copy the parent research tree merely so the OOF bundle can later replay it.

The OOF descriptor binds parent identities:

- `foundation_id`;
- parent `closure_id` if exact parent representation matters to provenance;
- parent `verification_id`;
- parent `promotion_id` when confirmatory; and
- source/evidence class.

Paths are runtime locators/provenance, not scientific identity.

## R2 deep semantic verification

R2's independent verifier may recompute the transformation introduced by R2:

```text
authenticated foundation semantic inputs
-> features
-> preprocessing
-> fits
-> forecasts
-> coverage/evaluation
```

It must not semantically replay:

- Stage 6 attempts/callbacks;
- Stage 7 provider-history construction;
- Stage 8 panel/target/fold construction; or
- native R1 panel/target/fold construction.

Parent authenticators are allowed and expected.

## Feature evidence

Do not maintain a second complete copy of parent input files solely for replay.

Persist/bind the R2-owned feature evidence needed to reproduce R2. During the one R2 semantic verifier,
recompute features from the authenticated parent inputs and compare them.

## Required tests

### Hard call guards

Ordinary R2 build and R2 deep verification must raise a test failure if they call:

- Stage 6 semantic verifier/replay;
- Stage 7 deep verifier;
- Stage 8 deep verifier;
- R1 deep verifier.

### Closure shape

Assert a current OOF bundle contains no recursive `replay-inputs/research/...` ancestor tree.

### Work counts

Record:

- parent semantic verifier calls = 0;
- parent files copied = 0;
- R2 feature recomputation count at build = one calculation;
- R2 feature recomputation count at deep verify = one independent replay;
- fit counts are exactly those required by build/replay, not multiplied by parent verification.

### Parent binding mutation

Reject wrong foundation/receipt/promotion identities without falling back to deep verification.

## Validation

Focused R2 integration suites plus clean `ops/dev/verify.sh`.

---

# PR-R2 — Add durable R2 OOF verification receipt and cheap authentication

## Purpose

Persist the result of the one R2 deep semantic replay so G1 and other descendants do not repeat it.

## Dependency

- PR-R1 merged.

May run in parallel with PR-R3 if file ownership is separated.

## Primary ownership

Expected files:

- R2 OOF runtime bundle/verifier module;
- receipt domain/serialization local to R2;
- CLI wiring for explicit verify/authenticate if needed;
- R2 OOF tests.

Avoid broad runtime provenance changes owned by PR-R3.

## Lifecycle

Current R2 should follow:

```text
build OOF
-> structural publication
-> explicit semantic verify once
-> create R2 OOF verification receipt
-> ordinary authenticate thereafter
```

Do not automatically re-run semantic verification inside every load/auth operation.

## APIs

Make naming unambiguous, for example:

```python
verify_r2_oof_semantics(...)
authenticate_r2_oof(..., receipt=...)
```

Do not call a closure-only checker `verify` if that obscures whether semantic replay occurred.

## Receipt contract

Add a small create-only receipt binding:

- `oof_id`;
- R2 `closure_id`/manifest SHA;
- experiment semantic ID;
- foundation semantic ID;
- foundation `verification_id`;
- foundation `promotion_id` if confirmatory;
- source/evidence class;
- R2 verifier contract/version/check set;
- relevant numerical verifier/environment identity; and
- OOF `verification_id`.

No ancestor file list.

## Cheap authentication

Authentication must not:

- recompute features;
- rerun preprocessing selection;
- refit models;
- recompute forecasts;
- reevaluate metrics;
- replay parent semantics.

It authenticates the OOF manifest/receipt and exact R2-owned child bytes when those children are
actually consumed.

## Required tests

### Work-count

- build semantic verifier calls = 0;
- explicit semantic verify calls = 1;
- authenticate semantic verifier calls = 0;
- authenticate fit/feature/evaluation calls = 0.

### Receipt/closure mutation

Reject:

- changed OOF manifest;
- changed consumed child bytes;
- missing/orphan child;
- wrong foundation receipt binding;
- wrong confirmatory promotion binding;
- wrong `oof_id`;
- incomplete verifier check set;
- unsupported verifier version;
- reused receipt output path.

## Validation

Focused OOF/confirmatory fixture suite plus `ops/dev/verify.sh`.

---

# PR-R3 — Separate R2 execution provenance from scientific identity and delete/shrink the software-verification envelope

## Purpose

Remove whole-application identity from ordinary scientific invalidation and eliminate the remaining
recursive `R2SoftwareVerificationBundle` pattern unless it owns a genuinely unique integration claim.

## Dependency

- PR-R1 merged.

Can run in parallel with PR-R2 with pre-agreed file ownership.

## Primary ownership

Expected files:

- `runtime_identities()` and callers in R2;
- R2 preprocessing/fit/forecast verifier identity definitions;
- `src/qtrad/runtime/r2_ibkr_verification.py`;
- software-verification bundle domain/runtime types;
- relevant tests.

Avoid OOF receipt code owned by PR-R2 except shared field wiring.

## Provenance split

Replace a monolithic runtime identity with explicit concepts such as:

```python
execution_provenance()
numerical_environment()
```

Execution provenance may retain:

- git commit;
- image digest;
- Python version;
- NumPy version;
- scikit-learn version.

Scientific semantic IDs should not change merely because unrelated q-trad code or docs changed.

## Claim-scoped verifier identity

Use explicit boundary contracts/versions/check sets such as:

- feature verifier;
- preprocessing verifier;
- fit verifier;
- OOF semantic verifier;
- evaluation verifier.

Do not hash whole source files to manufacture semantic implementation identity unless a very specific
reason remains after this review.

Numerical reproduction may legitimately bind:

- NumPy/scikit-learn version;
- solver;
- tolerance;
- max iterations;
- other declared numerical policy.

Classify these deliberately as semantic numerical policy, verifier compatibility or provenance rather
than blindly inheriting the whole application image.

## `R2SoftwareVerificationBundle` review

Default action: delete it.

Before deletion, enumerate every claim it currently introduces. For each claim ask:

1. Is it already established by the R2 OOF semantic verifier/receipt?
2. Is it already established by unit/integration tests rather than retained scientific evidence?
3. Does a downstream scientific decision actually consume this claim?

If all claims are redundant, delete the bundle, builder, verifier, copied OOF trees and CLI.

If one source-specific representative integration claim remains genuinely useful, replace the whole
envelope with a tiny integration record containing only:

- integration check contract/version;
- exact OOF `verification_id`(s) consumed;
- specific integration checks performed;
- result; and
- provenance needed to interpret that integration test.

It must not copy OOF directories or re-run the OOF semantic verifier.

## Required tests

Prove:

- two otherwise identical R2 runs under different unrelated git/image provenance have the same
  scientific experiment/OOF semantic IDs;
- provenance metadata differs and remains inspectable;
- changing a real solver/model/feature scientific policy changes semantic identity;
- ordinary OOF authentication does not require current git HEAD/image equality;
- any retained integration record authenticates OOF receipts without verifying OOF semantics again.

## Validation

Focused runtime/provenance/software-envelope suite plus `ops/dev/verify.sh`.

---

# PR-R4 — Replace runtime-only confirmatory F2 proof with durable promotion and narrow G1 handoff

## Purpose

Eliminate the final consequential recursive verifier: downstream G1 currently needs a runtime
`VerifiedConfirmatoryF2` established by replaying/reconstructing the complete F2 proof. Replace it with
a persisted promotion over an already-verified OOF receipt.

## Dependencies

- PR-R2 merged;
- PR-R3 merged;
- PR-H3 merged for the current historical Stage 8 promotion semantics.

## Primary ownership

Expected files:

- confirmatory functions in `src/qtrad/runtime/r2_verification.py`;
- F2 promotion contract/runtime;
- G1 verification/freeze handoff;
- confirmatory tests;
- minimal CLI changes.

Do not change scientific selection policy or G2 holdout mechanics.

## New F2 promotion

Create a small create-only contract such as:

`qtrad-r2-confirmatory-f2-promotion-v1`.

Promotion creation must:

1. authenticate the exact R2 OOF verification receipt;
2. require confirmatory evidence/source class;
3. require the exact parent foundation confirmatory promotion where applicable;
4. require that the verified OOF receipt/check set includes qualifying F2 readiness, complete
   inner-validation register authentication and the necessary confirmatory OOF claims;
5. verify accepted verifier versions/check sets;
6. record operator authorisation fields; and
7. create the promotion.

It must not replay OOF features/fits/forecasts/evaluation or any parent stage.

## Promotion authentication

Add:

```python
authenticate_confirmatory_f2_promotion(...)
```

This cheaply restores the typed/capability information needed by G1.

The ordinary downstream path should stop calling the old full `verify_confirmatory_f2()` replay.

If an explicit deep audit remains useful, rename it clearly as an audit and keep it out of normal G1
or promotion paths. Prefer deletion if tests/receipts already provide all required claims.

## G1

`freeze_confirmatory_selection()` consumes the authenticated F2 promotion/capability.

`verify_confirmatory_g1()` independently proves **G1 only**:

```text
authenticated promoted F2 metrics/register
-> apply frozen selection policy
-> derive expected G1 selection
-> compare persisted G1
```

It must not refit models, replay forecasts or reconstruct the OOF semantic chain.

## G2

Do not simplify the scientific security boundary. Preserve:

- verified G1 as sole scientific-choice authority;
- G2-safe outcome-blind feature sources;
- selected-plus-required dependency set;
- sealed unopened forecasts/coverage;
- marker-first `OPENED`;
- authenticated target decode only after opened capability;
- irreversible `CONSUMED`;
- `OPENED_INCOMPLETE` on post-open failure; and
- independent R2.H terminal classification.

## Required tests

### Critical ordinary-path call guard

The path:

```text
authenticate F2 promotion
-> freeze G1
-> verify G1
-> prepare G2
-> verify G2 preparation
```

must perform zero calls to:

- OOF semantic replay;
- R1 deep verifier;
- Stage 8 deep verifier;
- Stage 7 deep verifier;
- Stage 6 semantic replay.

### Promotion mutation

Reject:

- wrong OOF receipt;
- wrong parent promotion;
- non-confirmatory evidence class;
- readiness not READY;
- incomplete required verified check set;
- unsupported/revoked verifier;
- reused promotion path;
- malformed authorisation.

### G1 scientific regression

Existing selection hierarchy, comparator retention, coverage denominator/numerator rules and holdout
question semantics must be unchanged.

## Validation

Focused confirmatory F2/G1/G2 suite plus clean `ops/dev/verify.sh`.

---

# PR-C1 — Converge, delete obsolete paths, enforce work-count architecture, and update current docs

## Purpose

Ensure the programme leaves one understandable architecture rather than a set of new receipts layered
on top of old cumulative code.

## Dependencies

- PR-H4 merged with real historical migration complete;
- PR-R4 merged;
- all new active contracts authoritative.

## Delete aggressively

Search for and remove every obsolete current-path remnant, including as applicable:

- legacy Stage 6/7/8 contract parsers;
- one-time migration commands/readers;
- old cumulative promotion functions;
- recursive R2 replay-input discovery/copy code;
- ancestor tree hashing used only for redundant proof;
- whole-file/source-code verifier hashes no longer part of accepted contracts;
- old R1 bundle compatibility;
- old R2 experiment/OOF contract compatibility;
- dead source-verification/checkpoint cache code;
- redundant software-verification envelope code;
- tests whose only purpose is preserving retired runtime compatibility;
- CLI flags for no-longer-needed parent replay inputs.

Do not delete historical documentation/evidence that is useful for audit; move chronology to archive if
necessary rather than keeping executable code.

## Architecture regression tests

Add focused tests that enforce the ordinary work-count matrix. Prefer monkeypatch/call counters over
fragile static import bans when practical.

Required target matrix:

| Operation | Parent semantic replay | Own semantic replay |
|---|---:|---:|
| Stage 6 publish | 0 | 0 |
| Stage 6 verify | n/a | 1 |
| Stage 6 authenticate | 0 | 0 |
| Stage 7 build | 0 | transformation once |
| Stage 7 verify | 0 | 1 |
| Stage 7 authenticate | 0 | 0 |
| Stage 8 build | 0 | transformation once |
| Stage 8 verify | 0 | 1 |
| Stage 8 authenticate | 0 | 0 |
| Stage 8 promotion | 0 | 0 |
| R1 build | 0 | transformation once |
| R1 verify | 0 | 1 |
| R1 authenticate | 0 | 0 |
| R2 build | 0 | calculation once |
| R2 verify | 0 | 1 |
| R2 authenticate | 0 | 0 |
| F2 promotion | 0 | 0 |
| G1 verify | 0 | G1 only |
| G2 verify | 0 | G2 only |

Also collect deterministic work counts for representative fixtures:

- files hashed;
- bytes hashed where easy to measure without intrusive infrastructure;
- Parquet parts decoded;
- rows decoded;
- feature recomputations;
- model fits;
- parent verifier invocations.

Do not add production telemetry solely for these tests; lightweight test instrumentation is sufficient.

## Current-doc updates

Update:

- `PLAN.md` current position;
- `docs/STATUS.md`;
- `docs/ARCHITECTURE.md`;
- ADR 0029 Amendment 2 implementation status if needed; and
- this plan, marking it complete/archive-ready.

The final active architecture should be explainable as:

```text
parent verification/promotion
        |
        v
child transform once
        |
        v
child deep verify once -> receipt
        |
        v
cheap descendant authentication
```

and:

```text
verified receipt + explicit authority -> promotion
```

not:

```text
promotion -> replay everything since acquisition
```

## Final exit criteria

Before moving to the first real confirmatory R2 execution, all must hold:

1. Exactly one normal active Stage 6/7/8 contract each.
2. Exactly one normal active R1 bundle contract.
3. Exactly one normal active R2 experiment/OOF contract.
4. No ordinary parent semantic replay downstream of an accepted receipt.
5. No recursive R2 parent closure copy.
6. No whole-application commit/image in scientific identity absent a documented numerical reason.
7. Stage 8 and F2 promotions perform zero semantic replay.
8. G1/G2 holdout safety semantics unchanged.
9. Real historical chain has been migrated and independently verified under the new contracts.
10. Clean `ops/dev/verify.sh` passes.

Once these are true, the next major action should be the real qualifying R2/F2 scientific workflow,
not another infrastructure hardening tranche unless a concrete failure blocks it.

---

# 5. Agent assignment summary

## Immediately after PR-00

### Agent H
Own PR-H1 and then serially PR-H2, PR-H3, PR-H4.

Reason: Stage 6/7/8 contracts directly hand off to each other, and one owner minimises accidental
reintroduction of transitive proof.

### Agent F
Own PR-F1 only.

Do not touch historical Stage 6–8 or R2 orchestration.

### Agent I
Own PR-I1 only.

Stay primarily in R2 domain/manifest identities; do not perform the recursive runtime refactor.

## Second wave

### Agent R-A
Own PR-R1 then PR-R2.

Primary ownership: R2 handoff and OOF receipt.

### Agent R-B
Own PR-R3.

Primary ownership: runtime/provenance and software-verification envelope simplification.

PR-R2 and PR-R3 may run concurrently after PR-R1 if they agree file boundaries before starting.

## Final research wave

One agent owns PR-R4 after PR-R2/PR-R3 merge, then a fresh review/convergence agent owns PR-C1.

The convergence agent should not be the principal author of every preceding PR; a fresh pass is useful
for finding compatibility and dead-code residue.

# 6. Review checklist for every PR in this programme

Reviewers should ask, in order:

1. What exact repeated/incorrect work is removed?
2. What immediate parent proof replaces it?
3. Does this boundary now prove only its own claims?
4. Are semantic and physical identities separated?
5. Did a path/image/hash enter semantic identity merely because it was convenient?
6. Are unused parent bytes still being hashed or copied?
7. Is any compatibility branch present without a named retained-evidence need and deletion trigger?
8. Did the PR introduce a generic framework instead of deleting complexity?
9. Are the concrete causal/holdout safety controls unchanged?
10. Do call-count tests prove ordinary parent replay is zero?
11. Is obsolete code actually deleted?

A finding that proposes more abstraction, compatibility or hardening must name the concrete current
failure it prevents. Otherwise it should not block the programme.
