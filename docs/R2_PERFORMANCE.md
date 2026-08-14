Assuming PR120 is merged first, I would make this a **six-stage programme comprising eight implementation PRs**, with three useful parallel workstreams. The governing principle throughout is:

> **Each boundary deeply proves only the claims it introduces. It cheaply authenticates already-proved parent claims. Semantic identity describes meaning; closure identity describes bytes; verification describes completed proof; promotion grants authority.**

PR120 already establishes the right lower-level primitives: provider-history v2 separates semantic dataset identity from physical layout, Stage 8 selects only relevant v2 parts, and `_read_ibkr_historical_result_header()` can authenticate Stage 6 metadata without walking/replaying every request child.

## Dependency graph

```text
PR120 merged
    │
    ▼
S0  Architecture contract / plan amendments
    │
    ├────────────────────┬─────────────────────┐
    ▼                    ▼                     ▼
S1-A R1 receipt       S1-B Stage 6 receipt  S1-C R2 identities
    │                    │                     │
    │                    ▼                     │
    │                 S2-B Stage6→7            │
    │                 receipt handoff          │
    │                                          │
    └────────────────────┬─────────────────────┘
                         ▼
                    S2-A R2 parent handoff
                    remove recursive closure
                         │
              ┌──────────┴──────────┐
              ▼                     ▼
         S3-A R2 OOF receipt   S3-B verifier/runtime
                               identity narrowing
              └──────────┬──────────┘
                         ▼
                    S4 F2 promotion
                    G1/G2 handoff
                         │
                         ▼
                    S5 convergence
                    + performance proof
```

**Parallelism:** S1-A, S1-B and S1-C can run simultaneously. Once S1-B lands, S2-B can proceed independently while the R1/R2 branch continues. S3-A and S3-B can also run in parallel after S2-A.

---

# Stage 0 — Freeze the architecture contract

**One short documentation-only PR. Serial prerequisite.**

This is not PLAN/STATUS housekeeping. R2's own governance requires a substantive amendment when implementation requirements change, and these changes alter identity and verification semantics.

### Agent task

Add one cross-cutting ADR, plus amendments to the R1/R2 implementation plans and the historical-acquisition plan where necessary.

The ADR should establish these invariants:

1. **One expensive semantic verification per boundary.**

   * Construction may compute the transformation once.
   * Independent verification may replay it once.
   * Ordinary downstream consumption must not replay it again.

2. **Immediate-parent rule.**

   * A child proves its own transformation.
   * It authenticates its parent's accepted receipt/promotion.
   * It does not recursively prove grandparents.

3. **Four identity classes.**

```text
semantic_id
    What the data/result means.

closure_id
    Exact physical representation and child-byte closure.

verification_id / receipt
    Evidence that a named verifier completed named semantic checks
    against an exact closure.

promotion_id
    Explicit authority to cross a consequential boundary.
```

4. Paths, Parquet byte layout, creation time, Git commit, complete application-image digest and logging/presentation code are **not scientific semantic identity** unless they actually alter the claimed computation.

5. Full application/image identity remains useful **provenance**.

6. Verifier invalidation is claim-scoped:

   * verifier contract;
   * verifier version;
   * completed-check set;
   * explicit verifier identity.

7. Cumulative deep replay remains permitted for:

   * explicit audit;
   * verifier revocation/replacement where required;
   * an explicitly defined promotion boundary.

   It is not ordinary consumption.

8. Existing Stage 7/8 receipts and PR120 v2 evidence remain authoritative and are not rebuilt.

### Suggested contracts to reserve

```text
qtrad-research-foundation-bundle-v3
qtrad-research-foundation-verification-v1

qtrad-ibkr-historical-result-verification-v1

qtrad-r2-experiment-v3
qtrad-r2-oof-bundle-v3
qtrad-r2-oof-verification-v1

qtrad-r2-confirmatory-f2-promotion-v1
```

Names can move slightly during implementation, but reserve the conceptual boundaries here.

### Exit gate

No code. `git diff --check`; documentation tests if applicable.

---

# Stage 1 — Three agents in parallel

## S1-A — R1 semantic/physical split and reusable verification receipt

**Agent A.**

### Problem being fixed

R1 currently verifies its children during bundle creation, persists them, then `verify_foundation_bundle()` reconstructs panel → targets → folds → forecasts and finally calls `verify_foundation_children()` again, which recreates semantic dataset identities and rechecks forecast lineage.

That is the direct R1 analogue of the old Stage 7 pattern.

### Primary files

```text
src/qtrad/domain/foundation_bundle.py
src/qtrad/application/foundation_bundle.py
src/qtrad/runtime/foundation_bundle.py
tests/...foundation...
```

Avoid R2 files in this PR.

### Required implementation

#### 1. Introduce R1 bundle v3

Do **not** reinterpret the existing v2 `bundle_id`: it currently hashes physical `ArtifactReference`s and build provenance, so it is a closure identity in practice.

v3 should expose explicitly:

```text
foundation_id       semantic
closure_id          physical/top-level closure
```

`foundation_id` should bind at least:

```text
source_class
configuration dataset_id
observation dataset_id
availability semantic dataset_id
panel dataset_id
target dataset_id
fold dataset_id
forecast dataset_id
ordered instruments
range
coverage semantics
```

It must **not** bind:

```text
manifest paths
manifest byte hashes
manifest IDs derived from physical bytes
application commit
image digest
creation time
Parquet layout
```

`closure_id` binds the complete exact references and provenance.

#### 2. Make construction single-pass

`persist_foundation_bundle()`:

```text
validate in-memory cross-child relationships
persist children
persist bundle
return PUBLISHED_UNVERIFIED-equivalent evidence
```

It must **not** call `verify_foundation_bundle()`.

`build_foundation_bundle()` may perform cheap relationship/invariant checks, but must not reconstruct every dataset merely to recalculate hashes.

#### 3. Retain one deep verifier

`verify_foundation_bundle()` remains the independent semantic verifier and should still prove:

```text
observations / availability
panel deterministic causal replay
targets
folds
zero forecasts
coverage
outcome-blind projections
G2-safe projections
cross-lineage
```

But each of those checks happens once.

Remove the second `verify_foundation_children()` semantic reconstruction after the deterministic replay has already proved the same datasets.

#### 4. Persist an R1 receipt

After successful deep verification:

```text
qtrad-research-foundation-verification-v1
```

Receipt should bind:

```text
foundation_id
closure_id
bundle file SHA-256
exact child closure/root
source class
verified semantic child IDs
verifier contract
verifier version
verifier identity
completed checks
receipt SHA-256
```

Receipt must be create-only and outside the authenticated R1 closure.

#### 5. Add cheap authentication

```python
authenticate_foundation_bundle(..., receipt=...)
```

It:

* authenticates receipt first;
* verifies exact bundle/child closure bytes;
* uses file-level verification rather than causal replay;
* loads required children;
* returns trusted typed bindings.

In particular it must not call:

```text
build_asof_panel
build_frozen_targets
build_expanding_folds
build_zero_return_forecasts
verify_observation_build_evidence semantic recomputation
```

where the receipt already claims those checks.

`verify_outcome_blind_foundation_bundle()` should receive the same treatment: authenticated outcome-blind projections should not trigger a second full R1 proof.

### Compatibility

Existing v2 bundles can remain readable for implementation fixtures, but **new R2 confirmatory authority should require v3/receipt-backed evidence**. Do not create an elaborate compatibility abstraction.

### Required regressions

Use spies/monkeypatches, not timing tests, to prove:

```text
build -> zero independent semantic replay
deep verify -> exactly one R1 semantic replay
authenticate -> zero R1 semantic replay
outcome-blind authenticate -> zero full target/panel replay
```

Also prove:

```text
same semantic children + different physical paths/layout/image
    => same foundation_id

changed panel/target/fold semantics
    => different foundation_id

mutated exact child bytes
    => authentication fails
```

### Exit

Focused R1 suite + `ops/dev/verify.sh`.

---

## S1-B — Stage 6 reusable verification receipt

**Agent B. Fully parallel with S1-A and S1-C.**

PR120 already introduces `_read_ibkr_historical_result_header()`, explicitly authenticating Stage 6 manifest/plan metadata **without walking the request-result children**. That is exactly the primitive this PR needs.

### Primary files

```text
src/qtrad/application/ibkr_results.py
src/qtrad/runtime/ibkr_results.py
tests/test_ibkr_results.py
```

Do not touch R1/R2.

### Required implementation

#### 1. Add Stage 6 verification receipt

```text
qtrad-ibkr-historical-result-verification-v1
```

The deep verifier remains able to replay every request from attempts/callbacks/markers and reconstruct the aggregate. `IbkrHistoricalAggregateReplay` already owns exactly this semantic proof.

Receipt binds:

```text
Stage 6 manifest SHA
plan semantic ID + plan bytes SHA
runtime ID
aggregate semantic ID
exact request-child reference root
verifier contract/version/identity
completed-check set
receipt SHA
```

#### 2. Decouple publication from deep replay

Currently Stage 6 publication stages the result and runs complete `verify_ibkr_historical_result()` before rename.

Change to:

```text
build
↓
bounded structural/canonical checks
↓
publish immutable closure
↓
deep verify once
↓
receipt
```

A structurally valid closure may therefore exist without authority if later semantic verification fails.

Do not delete it to conceal the failure.

#### 3. Add cheap authentication

```python
authenticate_ibkr_historical_result(path, receipt=...)
```

It should:

* authenticate header;
* authenticate receipt;
* validate exact closure/tree and child byte hashes;
* not replay attempts/callback semantics.

#### 4. Add receipt-backed result iteration

Provide a stream for downstream transformation that:

```text
reads request child
checks exact child byte SHA
canonical-decodes request result
checks request identity/result semantic ID
yields accepted result
```

but does **not** call:

```python
replay_ibkr_historical_request_result()
```

The receipt is what proves that accepted rows and operational dispositions were previously derived correctly.

### Important boundary

The existing full Stage 6 semantic replay remains available, and PR120's explicit confirmatory Stage 8 promotion replay remains valid. PR120 deliberately performs Stage 6 semantic replay at that exceptional cumulative boundary.

### Required tests

Prove:

```text
publish performs no deep semantic verifier
verify performs one complete replay
authenticate performs no request semantic replay
authenticated stream rejects changed child bytes
authenticated stream rejects request/result identity mismatch
receipt mutation/re-signing/incomplete check-set fails
```

No Gateway, PostgreSQL, provider call or real evidence mutation.

---

## S1-C — R1/R2 semantic identity cleanup

**Agent C. Parallel, but domain/contracts only.**

### Problem being fixed

`R2ExperimentConfig.configuration_id` currently binds the physical-ish R1 `bundle_id`, R1 application version and R1 image identity alongside the actual scientific configuration.

`verify_exact_r1_bindings()` then rechecks this whole identity mesh again.

R2 OOF identities similarly hash complete child references including paths and byte hashes.

### Scope

Primarily domain contracts and unit tests. Do **not** yet remove replay staging from `runtime/r2_verification.py`; that is S2-A.

### Required implementation

#### R2 experiment v3

Replace physical parent binding with:

```text
foundation_id
observation_dataset_id
configuration dataset_id
panel dataset_id
target dataset_id
fold dataset_id
source class
scientific experiment choices
```

Remove from the semantic `configuration_id`:

```text
R1 physical closure/bundle ID
R1 application version
R1 image digest
paths
byte hashes
```

Keep application/image information as execution provenance elsewhere.

#### R2 OOF v3

Expose:

```text
oof_id          semantic result identity
closure_id      exact physical bundle identity
```

`oof_id` hashes semantic child identities and experiment/foundation semantic IDs.

`closure_id` binds paths and SHA-256s.

#### Forecast/evaluation/selection audit

Inventory every R2 hash constructor and classify each field.

The agent should produce a test-backed table in the PR description:

```text
field                         semantic / closure / provenance / verifier
```

At minimum inspect:

```text
R2ForecastManifest
R2OofBundle
R2SoftwareVerificationBundle
SelectionManifest
R2HoldoutSelectionManifest
R2PreprocessingSelection
fold fits
evaluation report
holdout seal/evaluation
```

The rule is not “delete provenance.” It is:

> Provenance must not cause an unrelated physical rebuild or unrelated q-trad code change to become a new scientific experiment.

NumPy/scikit-learn identities can remain attached to numerical provenance/replay claims where justified; a full q-trad application image should not.

### Required regressions

Semantic IDs remain equal when only these vary:

```text
artifact path
physical child SHA for an equivalent physical encoding
R1 build image
unrelated q-trad application commit
creation timestamp
```

They change when these vary:

```text
dataset semantics
target/fold membership
model family
features
alpha/solver policy
selection thresholds
holdout definition
source class/evidence class
```

---

# Stage 2 — Two agents in parallel

## S2-A — Replace recursive R2 replay closures with immediate-parent authority

**Depends on S1-A + S1-C.**

This is the largest and highest-value R2 PR.

### Problem

Current R2 `_declared_replay_files()` recursively discovers a Stage 8 foundation's provider-history ancestry; `_stage_replay_inputs()` then copies and SHA-hashes the complete discovered closure into the OOF artifact.

The OOF verifier later re-hashes this copied closure again.

This must disappear from the new v3 path.

### Primary files

```text
src/qtrad/runtime/r2_verification.py
src/qtrad/runtime/r2_bundles.py
src/qtrad/application/r2_ibkr_historical.py
src/qtrad/runtime/foundation_bundle.py       # consume new authenticator only
src/qtrad/runtime/ibkr_foundation.py         # existing auth interface
tests/test_r2_...
```

### Required architecture

Introduce one **small R2-local parent-authority abstraction**, not a general artifact framework.

Conceptually:

```python
AuthenticatedR2Foundation(
    foundation_id,
    closure_id,
    verification_receipt_id,
    source_class,
    semantic child IDs,
    typed R1/R2 inputs,
    confirmatory_promotion_id=None,
)
```

Only authenticators can construct it.

Adapters:

```text
IG/native R1
    authenticate R1 v3 receipt
    → AuthenticatedR2Foundation

IBKR historical ordinary
    authenticate S8 receipt
    → AuthenticatedR2Foundation

IBKR historical confirmatory
    authenticate S8 receipt
    authenticate S8 promotion
    → confirmatory AuthenticatedR2Foundation
```

### Remove from the v3 OOF path

```text
_declared_replay_files
_stage_replay_inputs
recursive replay-input tree copying
complete parent closure duplication
ancestor-file SHA loops
```

Do not make the new OOF closure self-contained by copying Stage 6–8 into it.

### New OOF descriptor

Bind identities, not ancestor files:

```text
foundation_id
foundation_closure_id
foundation_verification_receipt_id
foundation_promotion_id | null

experiment_configuration_id

source/evidence class
R2-owned semantic children
execution provenance
```

Paths to external parent artifacts must not enter scientific identity.

### R2 boundary verification

The deep R2 verifier may replay **R2-owned claims**:

```text
features from authenticated R1/Stage8 inputs
preprocessing selections
fits
forecasts
coverage
evaluation
selection derivation
holdout exclusion
```

It must not replay:

```text
Stage 6 attempts/callbacks
Stage 7 provider-history semantic construction
Stage 8 panel/target/fold construction
native R1 panel/target/fold construction
```

### Feature handling

Do not retain the current double representation merely for replay.

The canonical feature children embedded/bound by the OOF run are sufficient R2 input evidence. During the one R2 deep verifier pass, recompute those feature semantics from the authenticated immediate parent and compare them.

No need to copy the original complete Parquet research tree into `replay-inputs/`.

### Confirmatory path

Preserve:

```text
outcome-blind target access
G2-safe feature-source authority
holdout outcome exclusion
Stage 8 promotion requirement for IBKR
```

This PR changes proof reuse, **not holdout safety**.

### Critical tests

Hard spies proving R2 build/auth/replay never invoke:

```text
verify_provider_history*
replay_provider_history*_stage6
verify_ibkr_historical_result*
verify_ibkr_foundation deep verifier
verify_foundation_bundle deep verifier
```

Parent **authentication** functions are expected.

Also prove no `replay-inputs/research/...` ancestry exists in a v3 OOF closure.

---

## S2-B — Make future Stage 7 consume Stage 6 receipt

**Depends only on S1-B. Can run while S2-A is underway.**

### Scope

The retained PR120 v1→v2 migration evidence is untouched.

This change is for **future Stage 6 acquisitions**.

### Primary files

```text
src/qtrad/runtime/provider_history.py
src/qtrad/application/provider_history.py
src/qtrad/runtime/ibkr_results.py
tests/test_provider_history.py
```

### Required change

Fresh provider-history construction should take:

```text
Stage 6 manifest
Stage 6 verification receipt
```

and consume the new authenticated Stage 6 result stream.

It must read each accepted request result once for transformation but **must not replay Stage 6 operational semantics while doing so**.

So the future path becomes:

```text
Stage 6 deep verification once
    ↓ receipt
Stage 7 authenticate receipt
    ↓
read accepted result rows once
    ↓
build provider history
```

rather than:

```text
Stage 6 verify
    ↓
Stage 7 replay all Stage 6 semantics again
    ↓
build provider history
```

Do not change PR120's v2 semantic dataset identity for equivalent observations.

### Deferred optimisation

I would **not** combine this PR with inventing a direct Stage6→v2 acquisition contract. PR120 v2 is deliberately a v1→v2 repack contract. A direct-v2 acquisition writer can be considered when we next actually need another historical acquisition.

The key architectural defect here is recursive proof, not one-off format migration.

---

# Stage 3 — Two R2 agents in parallel

## S3-A — Durable R2 OOF verification receipt

**Depends on S2-A.**

### Contract

```text
qtrad-r2-oof-verification-v1
```

### Required lifecycle

```text
R2 OOF build
    ↓
immutable OOF closure
    ↓
one independent R2 semantic replay
    ↓
R2 OOF verification receipt
    ↓
cheap authentication thereafter
```

Receipt binds:

```text
oof_id
OOF closure_id
OOF manifest SHA
experiment_configuration_id
foundation_id
parent verification receipt ID
parent promotion ID if applicable
source class
evidence class
R2 verifier contract/version/identity/check-set
relevant numerical-environment identity
receipt SHA
```

### API split

Make the distinction obvious in names:

```python
verify_r2_oof_semantics(...)
authenticate_r2_oof(...)
```

The old `verify_r2_oof_bundle()` currently does mostly closure authentication and structural checks; do not leave naming that makes agents think it is equivalent to a complete semantic replay.

### Authentication must not

```text
recompute features
redo preprocessing selection
refit Ridge
recompute forecasts
rerun evaluation
rerun parent verification
```

It verifies bytes, lineage and receipt claims.

### Mutation tests

Receipt authentication must fail on:

```text
changed child bytes
missing child
orphan child
changed OOF manifest
wrong parent receipt
wrong promotion
wrong semantic OOF ID
removed completed check
unsupported verifier version
```

---

## S3-B — Narrow R2 verifier/runtime identity

**Depends on S1-C and preferably S2-A; parallel with S3-A.**

### Current issue

`runtime_identities()` currently makes the complete Git commit and deployment image part of the R2 identity environment and confirmatory verifier comparisons.

### Required outcome

Retain:

```text
git commit
image digest
Python
NumPy
scikit-learn
```

as useful provenance.

But introduce claim-scoped identities such as:

```text
qtrad-r2-feature-verifier-v1
qtrad-r2-preprocessing-verifier-v1
qtrad-r2-fit-verifier-v1
qtrad-r2-oof-semantic-verifier-v1
```

with explicit version/check sets.

The scientific identity should bind the resulting semantic dataset/model/evaluation plus declared numerical policy.

It should not be invalidated merely because, for example, IBKR health code or documentation changed in the same image.

### Numerical boundary

Do not overcorrect.

For Ridge numerical reproduction, it remains legitimate to record:

```text
NumPy version
scikit-learn version
solver
tolerance
max iterations
```

But these belong to numerical provenance/verifier compatibility, not a blanket “entire q-trad image is the model”.

### Required regression

Construct equivalent R2 evidence with differing unrelated application/image provenance and prove:

```text
same semantic experiment
same semantic OOF
different provenance/closure where appropriate
```

Then mutate an actual solver/model policy and prove semantic identity changes.

---

# Stage 4 — Durable F2 promotion and G1/G2 hand-off

**Single agent after S3-A/S3-B converge.**

This is where we eliminate the last important runtime-only recursive verifier.

### Problem

`verify_confirmatory_f2()` currently authenticates the OOF bundle and then performs the complete staged confirmatory replay, readiness reconstruction, inner-validation authentication, runtime reconciliation and evaluation/selection reconciliation every time it needs to mint the in-memory `VerifiedConfirmatoryF2`.

The downstream G1 path is otherwise sensibly capability-gated.

### New contract

```text
qtrad-r2-confirmatory-f2-promotion-v1
```

### Creation

Promotion requires:

```text
authenticated confirmatory R2 OOF receipt
exact parent confirmatory promotion where source requires one
qualifying readiness claim from the verified OOF
exact experiment
operator authorization
clean/detached runtime provenance if still desired operationally
new output
```

**It must not perform another R2 deep replay.**

The one deep R2 replay already created the OOF receipt.

Promotion says:

> this already-verified confirmatory F2 evidence is authorised to freeze G1.

It is not another verifier.

### Authentication

```python
authenticate_confirmatory_f2_promotion(...)
```

cheaply constructs the runtime-only capability currently represented by `VerifiedConfirmatoryF2`.

That allows most existing type-gated downstream design to remain.

### G1

`freeze_confirmatory_selection()` consumes the authenticated F2 promotion capability.

`verify_confirmatory_g1()` independently replays **G1 only**:

```text
F2 promotion
    ↓
derive frozen selection
    ↓
compare persisted G1
```

It must not call `_replay_confirmatory_oof()`.

### G2

Preserve the current security/scientific boundary:

```text
verified G1
→ G2-safe feature source
→ sealed unopened preparation
→ OPENED marker
→ outcome decode
→ evaluation
→ CONSUMED
→ R2.H
```

No weakening of outcome-blindness or irreversible marker semantics.

### Explicit audit

If desired, retain:

```text
r2 confirmatory-f2 audit
```

that does the old complete R2 replay.

It is an operator-invoked audit, not the ordinary G1 prerequisite.

### Required spy test

The complete ordinary path:

```text
authenticate F2 promotion
→ freeze G1
→ verify G1
→ prepare G2
→ verify preparation
```

must execute **zero** calls to:

```text
_replay_confirmatory_oof
deep R1 verifier
deep Stage 8 verifier
deep Stage 7 verifier
Stage 6 semantic replay
```

That is probably the single most important regression in the entire tranche.

---

# Stage 5 — Convergence, deletion and work-count evidence

One final PR after all implementation PRs merge.

This is partly cleanup, but the key deliverable is proving that we actually removed the recursive architecture rather than merely adding receipts alongside it.

### Remove obsolete paths

For the new contracts, delete or make explicitly audit-only:

```text
recursive replay-input discovery
ancestor closure copying
automatic deep parent verification
whole-image scientific invalidation
duplicate R1 semantic-ID reconstruction
runtime-only F2 proof as the only authority
```

Avoid maintaining two normative architectures.

### Add architecture tests

I would add static/AST tests enforcing rules such as:

```text
r2_* may import parent authenticate functions
r2_* ordinary build/auth paths must not import parent deep-verification functions

provider_history ordinary construction may authenticate Stage 6
but does not call replay_ibkr_historical_request_result

foundation authenticate path does not import causal builders
```

This makes regression substantially harder.

### Add work-count tests

Prefer deterministic **work-count evidence** to flaky timing assertions:

| Operation                | Allowed deep parent replay |        Allowed own deep replay |
| ------------------------ | -------------------------: | -----------------------------: |
| S6 authenticate          |                          0 |                              0 |
| S7 build from S6 receipt |                          0 |            transformation once |
| R1 build                 |                          0 |            transformation once |
| R1 deep verify           |                          0 |                              1 |
| R1 authenticate          |                          0 |                              0 |
| R2 build                 |                          0 | transformation/model work once |
| R2 deep verify           |                          0 |                              1 |
| R2 authenticate          |                          0 |                              0 |
| F2 promotion             |                          0 |                              0 |
| G1 freeze/verify         |                          0 |                        G1 only |
| G2 prep/verify           |                          0 |                        G2 only |
| explicit audit           |                  permitted |                      permitted |

### Synthetic performance evidence

For representative fixtures report:

```text
files hashed
Parquet parts decoded
rows decoded
feature recomputations
Ridge fits
parent semantic verifier invocations
wall time as diagnostic only
```

The important acceptance result is **zero parent semantic verifier invocations during ordinary downstream use**.

### Final docs

Only here update the active architecture/status documents to describe what actually landed. PR120 will already have handled its own housekeeping, as assumed.

---

# Cross-agent no-touch rules

Every implementation agent should receive these constraints:

```text
No provider calls.
No Gateway access.
No real holdout access.
No rebuilding/mutating retained Stage 6/7/8 evidence.
No changes to R2 scientific thresholds.
No changes to fold durations or membership policy.
No feature-family changes.
No model-family changes.
No selection threshold changes.
No holdout-question changes.
No changes to OPENED/CONSUMED irreversibility.
No broker/order surface.
No broad generic artefact framework.
```

And one especially important instruction:

> **Do not solve compatibility by retaining both expensive and cheap normative paths. Legacy readers are acceptable where retained evidence requires them; new v3 evidence must have one clear `build → verify once → receipt → authenticate` lifecycle.**

# Recommended PR merge order

I would run the work operationally as:

```text
0. Architecture amendment

parallel:
1A. R1 receipt/single replay
1B. Stage 6 receipt
1C. R2 identity vNext

then parallel:
2A. R2 immediate-parent handoff / remove recursive replay inputs
2B. Stage 6 → Stage 7 receipt-backed handoff

then parallel:
3A. R2 OOF receipt
3B. R2 verifier/runtime identity narrowing

serial:
4.  F2 promotion → G1/G2 handoff

serial:
5.  Architecture enforcement, deletion, performance evidence
```

The critical path is therefore:

```text
S0 → S1-A/S1-C → S2-A → S3-A/S3-B → S4 → S5
```

while the Stage 6 work runs almost entirely beside it:

```text
S0 → S1-B → S2-B
```

That is how I would divide the agents. It gives us **three agents immediately**, then two-agents-at-a-time through most of the work, without putting several agents simultaneously into `r2_verification.py`.

The architectural end-state is much simpler:

```text
Stage 6 --receipt-->
Stage 7 --receipt-->
Stage 8 --receipt/promotion-->
R1 authority --receipt-->
R2 OOF --receipt/promotion-->
G1 --> G2 --> R2.H
```

with each arrow meaning **cheap authenticated hand-off**, rather than “copy everything below me and prove it again.”
