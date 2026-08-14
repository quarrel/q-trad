# ADR 0029: IBKR historical acquisition evidence boundaries

- **Status:** Accepted
- **Date:** 2026-08-02
- **Amended:** 2026-08-14 by Amendment 2
- **Amendment 1 status:** Accepted
- **Amendment 2 status:** Accepted
- **Amendment authority:** Amendment 2 controls where amendments conflict; otherwise Amendment 1 controls over the original decision.

## Context

IBKR historical acquisition crosses operator-authored decisions, licensed runtime archives, a mutable
PostgreSQL execution state machine, asynchronous provider callbacks and immutable research artefacts.
A restartable collector must recover incomplete work, but recovery must not amend a registered plan or
replace published evidence. Provider responses also describe what IBKR returned; they do not by
themselves prove that a market was open, that no price existed or when the data first became available.

ADR 0028 authorises the independent IBKR market-data source. This decision fixes the trust and replay
boundary for the staged historical-acquisition artefacts in
`docs/IBKR-HISTORICAL-ACQUISITION.md` before production code is added.

## Decision

### Trust model

The trust root is the accepted contract and policy definitions, operator-authorised verifier bytes,
and the operator decisions that the relevant contract explicitly makes authoritative. Exact clean
application-commit and image digests identify the verifier bytes for comparison; SHA-256 does not
establish their origin or authority. Operator acceptance establishes the chosen mapping or policy; it
does not prove an IBKR fact.

Everything else is untrusted input until the verifier for its boundary authenticates and interprets
it. This includes capability-review JSON, selection files, archives, configuration, caller-supplied
runtime labels, filesystem paths, database rows, clocks, callback payloads and ordering, provider
errors and schedules, availability claims, child manifests and summary fields. Earlier verified
artefacts become authenticated inputs to the next boundary, but only for the claims their contracts
make.

> **Amendment 1:** Earlier verified artefacts are consumed through authenticated
> verification evidence. Ordinary downstream boundaries do not recursively
> repeat unchanged ancestor replay.

The named boundaries are:

| Artefact or layer | Untrusted input | Acceptance boundary |
|---|---|---|
| Capability review | Probe specification and provider callbacks | Existing capability-review verifier authenticates the complete recorded review; it remains capability evidence, not a contract selection |
| `qtrad-ibkr-contract-selection-v1` | Capability review and operator decisions | Selection verifier reconstructs exactly one decision per canonical instrument and authenticates each accepted product-aware fingerprint |
| `qtrad-ibkr-acquisition-runtime-v1` | Archives, configuration and runtime labels | Runtime verifier re-hashes exact archives and sanitized configuration and authenticates the clean application commit/image and matched Gateway/API identity |
| `qtrad-ibkr-historical-request-profile-v1` | Canary results and operator freeze | Profile verifier authenticates the frozen durations, timeouts, concurrency, retry and pacing policy and binds the recorded canary evidence used as its rationale; it does not claim that the profile is objectively reliable or optimal |
| `qtrad-ibkr-historical-plan-v1` | Selection, runtime lock, profile and requested range | Plan verifier reloads those artefacts and reconstructs the exact request set and half-open coverage without PostgreSQL or IB Gateway |
| Request-result publication | Durable plan, attempt, callback and terminal records read from PostgreSQL | Publisher reads one repeatable-read snapshot, preflights bounded row and payload sizes, and derives a bounded create-only file closure; publication makes no verification claim |
| `qtrad-ibkr-historical-request-result-v2` | Published plan, result, attempt, callback and terminal files | Request-result verifier reads files only, independently reconstructs callback ownership, completion eligibility, marker counts, normalized rows or sessions and both operational and evidence dispositions for one planned request |
| Operational database reconciliation | Verified request-result evidence and current PostgreSQL records | A separate audit compares operational records with published evidence; it can identify drift but cannot establish or revoke evidence validity |
| `qtrad-ibkr-historical-result-v2` | Plan and request-result children | Aggregate verifier derives the exact expected child set, rejects omissions, additions and orphans, and recomputes coverage, chunk totals and dispositions |
| `qtrad-provider-historical-observations-v1` and availability selector | Verified aggregate result and declared-delay policy | Observation verifier replays rows and declared availability, and proves the absence of native receive/persistence timestamps |
| Source-specific foundation and readiness | Verified observations, selector and foundation configuration | Foundation verifier replays panels, targets, folds, support and source/availability lineage; readiness recomputes the fixed six-target, three-group gate |

### Digest claims

> **Amendment 1:** Repository commits and image digests remain execution
> provenance. Claim-scoped semantic verifier identities govern ordinary receipt
> and checkpoint reuse.

A SHA-256 proves equality of exact bytes to the bytes that were hashed. It does not prove correctness,
origin, completeness, semantic validity or absence of another file.

A semantic identity proves the canonical meaning accepted under its named contract and version. It
does not authenticate physical bytes unless paired with their digest. A child reference therefore
binds a safe relative path, contract, semantic identity and byte digest. A collection or closure
digest authenticates only the declared members; completeness exists only when an independent verifier
derives the expected set, authenticates every child, rejects duplicates and unexpected or orphaned
files, and enforces bounded regular-file and no-symlink rules.

Archive, source-manifest, configuration, commit and image digests prove only their named runtime
inputs after the verifier recomputes them. Caller assertions and mutable image tags are not proof.
Database digests and identifiers are operational lookup aids and never substitute for published
create-only files and independent verification. SHA-256 identifies bytes only after an authorised
source supplies the expected digest; it does not authenticate where those bytes came from.

### Mutable operation and immutable evidence

The database owns mutable scheduling, leases, pacing reservations, recovery eligibility, health and
publication status. Registered plan bytes are byte-stable. Attempts, callbacks, completion markers
and errors are append-only operational records. A crash may release a lease, invalidate an unfinished
attempt and schedule a permitted retry; it may not mutate the plan, callbacks or a completed success.

Contract selections, runtime locks, request profiles, plans, request results, aggregate results,
provider-history observations, availability selectors and foundations become immutable evidence only
when written create-only and independently verified. Published files are never repaired or replaced.
A changed mapping, runtime, profile, range, correction policy or acquisition outcome requires a new
semantic identity and preserves the earlier artefact, including explicit failures.

The request-result publisher is the only boundary that reads execution records from PostgreSQL. It reads
the exact registered plan, attempts, callbacks and terminal records in one read-only repeatable-read
transaction, preflights bounded row counts and raw payload bytes, and copies them into create-only
children before constructing the result manifest. The independent verifier reads only that published file
closure and derives its expected members from the published plan. Publication completion is recorded for
all request children and the plan in one database transaction after the final verified directory exists.
Optional reconciliation from a verified result back to PostgreSQL is an operational audit and is neither
publication nor evidence verification.

### Recovery, retry and request success

Only a failure classified by the frozen policy as transient is retryable: a transport timeout,
disconnect, transient farm unavailability, pacing rejection eligible for deferred retry, or a database
failure proven to have occurred before provider I/O. Each provider retry is a new append-only attempt
bound to the durable connection session identity and its generation, within the fixed attempt limit and backoff policy. A
database failure after send recovers the persisted attempt; it cannot authorize another provider
request until the fixed state machine has classified that attempt as incomplete and retryable.

Contract or runtime mismatch, invalid parameters, entitlement denial, conflicting or invalid
callbacks, evidence/closure failure, unknown global state requiring an operator, and exhausted retry
limits are not silently retried. `NO_DATA_RETURNED` and `SESSION_EVIDENCE_UNAVAILABLE` are terminal
evidence, not reasons to mutate or split the request. Unrelated planned requests may continue.

A published result keeps operational request/attempt state separate from evidence disposition. A bar request
with an operational success and a complete eligible closure is evidence-successful only when it has at
least one valid accepted row; a complete eligible closure with no accepted rows is `NO_DATA_RETURNED`.
Invalid OHLC, conflicting duplicates, eligible error callbacks or invalid schedule evidence receive an
explicit evidence disposition and cannot become accepted observations. A schedule result may declare an
active or inactive provider interval only from structurally valid in-range schedule evidence; invalid or
non-overlapping schedule evidence is `SESSION_EVIDENCE_UNAVAILABLE` with session state `UNKNOWN`.

An eligible completion marker must exactly copy the matching completion callback's transport identity,
payload, eligibility and receive timestamp, and that timestamp must be no later than the attempt's
terminal timestamp. Every completion callback has exactly one matching marker, including ineligible
closure evidence, and every SUCCEEDED attempt has exactly one eligible marker. Published attempt,
callback and completion-marker record identities, plus provider request transport identities
(connection_session_id, connection_generation, provider_request_id), are unique across the aggregate.
A closure that independently proves completion must agree with the stored attempt status and disposition;
an invalidated attempt may instead have a terminal timestamp with no disposition when it precedes a permitted
retry. Once the first independently valid terminal outcome is selected, it is never rerun or replaced.

### Independent replay and absence claims

> **Amendment 2:** The cumulative replay described below is exceptional deep audit only. Ordinary
> verification authenticates accepted immediate-parent verification or promotion evidence and proves
> the current boundary's claims. Confirmatory promotion authenticates existing proof; it does not replay ancestry.

Independent replay means loading the original files in a new verifier path, authenticating their
complete closure, deriving expected identities and outputs from lower-layer children and fixed policy,
and comparing recomputed results. Re-reading builder summaries, trusting a pass flag, querying a
mutable projection or merely checking digests is not replay.

Replay proceeds cumulatively: selection from capability review and decisions; runtime lock from local
archives/configuration/application identity; plan from selection, runtime and profile; request result
from the published plan, attempt, callback and terminal closure; aggregate from plan and request
results; provider observations from the aggregate and availability policy; foundation/readiness from
observations, selector and foundation configuration. A later layer cannot strengthen an unsupported
claim made by an earlier one.

Absence has three disjoint meanings:

- **Measured acquisition absence:** a planned, completed and verified bar request returned no accepted
  rows for its exact interval. This supports only `NO_DATA_RETURNED`; it does not prove that no market
  activity or price existed.
- **Provider-declared session state:** a verified `SCHEDULE` response declares an interval active or
  inactive. It is provider evidence, not measured quote availability.
- **Unknown:** incomplete, disconnected, invalid, entitlement-limited, conflicting or missing schedule
  evidence. Unknown is never coerced to inactive, closed, no data or a measured gap.

`available_at = interval_end + PT5M` is a declared provider-history policy, not a measured timestamp.
Native measured availability remains separate and provider-history artefacts must not contain
fabricated `received_at` or `persisted_at` values.

> **Amendment 1:** Artefact validity, execution provenance, scientific readiness
> and confirmatory authority are separate outcomes. The matrix is read subject
> to that separation.

## Invariant matrix

| Invariant | Required authenticated proof | Failure behaviour |
|---|---|---|
| Contract identity | Exactly one canonical decision; accepted mapping binds capability-review digest and complete product-aware fingerprint | Duplicate, substitution, missing decision or identity-field drift blocks that contract and is retained explicitly |
| Runtime identity | Recomputed archive, configuration, clean commit and image digests; matched Gateway/API pair; paper endpoint policy | Assertion, dirty source, mutable tag, digest mismatch or version mismatch fails before planning or provider I/O |
| Plan identity | Verified selection, runtime lock and profile plus exact range and deterministic complete request set | Mutation, overlap, planner-created gap, unsafe parameter or unexpected request requires a new plan |
| Attempt identity | Planned-request identity, unique ordinal, connection generation and append-only start/terminal record | Reuse, rewrite or ambiguous generation makes the attempt ineligible for success |
| Callback ownership | Published planned-request/API-request correlation, current generation, monotonic arrival sequence and half-open interval ownership | Stale, uncorrelated or boundary-external callbacks remain raw evidence and cannot become accepted rows |
| Terminal result selection | First independently valid success reconstructed from the published closure, otherwise deterministic terminal disposition after the fixed retry policy | Partial completion, later replacement, unclassified error or successful-request rerun fails verification |
| File closure | Safe relative regular-file references binding contract, semantic ID and SHA-256; independently derived exact bounded child set | Traversal, ancestor or child symlink, missing/duplicate/substituted/oversized child or orphan fails closed |
| Availability semantics | Source class and selector bind `BAR_END_PLUS_DECLARED_PROVIDER_DELAY`, exact delay and recomputed `available_at` | Unknown selector, native/provider mixing, fabricated native timestamps or changed delay changes identity or fails |
| Session semantics | Verified schedule callback closure and provider-declared intervals kept separate from observed bars | Failed or absent schedule is `UNKNOWN`, never inactive; schedule does not imply quote continuity |
| Foundation lineage | Verified aggregate result, contract selection, provider observations, selector, source class, configuration, panels, targets and folds | Altered or unauthenticated source evidence or children fail foundation verification. Authenticated gaps and insufficiencies remain valid evidence and affect readiness only through the frozen readiness policy |

## Consequences

Operational recovery remains possible without weakening immutable evidence: unfinished attempts may
be retried, while registered plans and published outcomes remain fixed. Every later artefact has one
named verifier and a bounded claim. This adds explicit contracts and mutation tests in later stages,
but prevents database state, provider declarations or self-authenticating summaries from masquerading
as research evidence.

This ADR adds no production code, provider request or dataset. Stages 1–8 remain separately reviewed
and authorised.

## Amendment 1 — Reusable verification evidence, claim-scoped invalidation,
and confirmatory promotion

- **Status:** Accepted
- **Date:** 2026-08-11
- **Amends:** Trust model, digest claims, mutable operation and immutable
  evidence, independent replay, invariant matrix and consequences
- **Approval:** PR #108

### Context

The first full-scale Stage 7 and Stage 8 executions demonstrated that the
original decision's cumulative-replay wording could be interpreted as requiring
every downstream process to repeat complete semantic verification of unchanged
ancestor artefacts.

That interpretation caused multi-hour repeated processing of immutable data,
made operationally irrelevant implementation changes invalidate expensive work,
and allowed a failure of execution provenance or readiness policy to be treated
as invalidation of otherwise unchanged evidence.

The ordinary local research threat model is non-adversarial with respect to the
operator. It must protect against implementation defects, accidental mutation,
corruption, stale or mismatched inputs, unsupported claims, look-ahead,
holdout leakage and false provenance. It is not required to resist an operator
deliberately forging both evidence and its verification records in order to
mislead themselves.

Confirmatory evidence retains a separate authority boundary. Before an artefact may authorise
confirmatory research or holdout use, promotion authenticates its exact accepted verification receipt,
the required evidence and readiness class, verifier acceptance and explicit operator authorisation.
Promotion does not repeat semantic replay; the exceptional deep-audit triggers remain separate.

### Decision

#### 1. Claims remain separate

The following are distinct claims:

1. **Artefact validity:** the exact immutable artefact satisfies its contract.
2. **Execution provenance:** the artefact or verification was produced by the
   claimed source revision, image or runtime.
3. **Scientific readiness:** the authenticated data satisfy a separately frozen
   experiment or promotion policy.
4. **Confirmatory authority:** the exact artefact has completed the required
   immutable-runtime promotion procedure.

Failure or uncertainty in one claim invalidates only that claim unless it can
causally affect another.

A checkout movement, provenance mismatch, reporting error or readiness failure
does not by itself alter immutable artefact bytes or revoke an already
established artefact-validity claim.

#### 2. Verification evidence is reusable

A complete boundary verifier may issue a create-only semantic-verification
receipt.

The receipt must bind at least:

- the artefact contract and semantic identity;
- the exact manifest identity and authenticated closure identity;
- immediate input artefact identities;
- the verifier contract and version;
- a claim-scoped verifier identity;
- the verification procedure or check set completed; and
- the verification result.

A later consumer may restore the established semantic result when it:

1. independently authenticates the canonical manifest, declared exact tree and closure identity;
2. rejects missing, additional or orphaned entries, symlinks and path escapes;
3. independently authenticates the receipt;
4. confirms that the receipt names the exact artefact and immediate inputs;
5. confirms that the verifier contract and claim-scoped verifier identity remain accepted and have not
   been revoked; and
6. hashes and validates the exact bytes of each child when the current operation consumes that child.

This is not equivalent to trusting a caller-supplied pass flag. The receipt is immutable evidence whose
applicability is re-established against the declared unchanged closure. Structural authentication does
not read or hash unconsumed child bodies.

#### 3. Independent replay is boundary-local by default

At an ordinary build or verification boundary, independent replay means:

- authenticate the immediate input artefacts or their accepted verification
  receipts;
- independently derive and verify the transformation and claims introduced by
  the current boundary; and
- reject altered, missing, additional, malformed or mismatched current-boundary
  evidence.

It does not require recursively repeating complete semantic replay of every
unchanged ancestor on every descendant invocation.

Complete cumulative replay remains available only when:

- an accepted verifier identity has been revoked;
- a defect may have affected a previously established semantic result; or
- an operator explicitly requests a fresh deep audit.

Neither ordinary verification nor confirmatory promotion triggers deep audit. Descendants consume the
exact accepted verification receipt or promotion rather than repeating ancestor replay.

#### 4. Confirmatory promotion is separate from ordinary iteration

Ordinary exploratory and implementation-evidence work may consume authenticated
immutable artefacts through accepted semantic-verification receipts.

Before an artefact may support `CONFIRMATORY` evidence, irreversible holdout
access or a confirmatory conclusion:

- authenticate the canonical manifest, declared exact tree, closure identity and accepted verification
  receipt, rejecting structural/path violations and hashing child bodies only if promotion consumes them;
- confirm the required completed-check set, verifier acceptance, evidence class and readiness class;
- record explicit operator authorisation; and
- create a promotion binding the exact semantic, closure and verification identities.

Promotion reuses the existing verified artefact and does not repeat semantic replay. A separate deep
audit is required only under the exceptional triggers above.

#### 5. Invalidation is claim-scoped

Invalidation follows these rules:

| Event | Required action | Does not require |
|---|---|---|
| Artefact bytes or closure tree changed | Reject the receipt and establish a new artefact identity | Treating the changed and original artefacts as the same |
| Producer semantics changed or were defective | Rebuild affected artefacts and semantic descendants | Reacquiring unrelated source evidence |
| Verifier semantics changed or were defective | Reverify the same immutable artefact under the new verifier | Rebuilding unchanged data |
| Downstream experiment configuration changed | Rebuild configuration-dependent descendants | Reverify source history |
| Logging, progress, presentation, CLI text or unrelated code changed | Rerun only the affected operational or presentation step | Rebuilding or semantically reverifying data |
| Documentation changed without a semantic contract amendment | No runtime invalidation | Any data-scale work |
| Mutable checkout moved after publication | Mark exact execution provenance unbound until re-attested | Declaring unchanged immutable bytes invalid |
| Backup or reporting failed | Retry that operation | Rebuilding or reverifying the foundation |

Cache and receipt identities must therefore be based on the code and policy
that can affect their protected claim. A whole source-file hash or repository
commit may be retained as provenance, but must not be the sole semantic
invalidation key when unrelated changes can alter it.

#### 6. Validity, publication and readiness are separate

A successfully constructed and verified foundation is publishable evidence
whether its readiness state is qualifying or nonqualifying.

Readiness gates downstream authority. It does not determine whether the
foundation exists or whether its authenticated historical facts may be
retained.

Provider gaps, entitlement failures, unavailable schedules and other
data-quality facts remain explicit evidence. Their scientific effect is
evaluated under a separately frozen readiness policy. The mere presence of one
or more authenticated provider gaps does not make the foundation artefact
invalid and is not, by itself, a universal readiness failure.

Exact numerical readiness thresholds and denominator rules belong to the
versioned experiment/readiness policy, not this durable acquisition-boundary
ADR.

#### 7. Rehearsal is not a required data-scale stage

Cheap preflight may authenticate configuration, inputs, output reservations,
checkpoint compatibility and resource bounds.

A disposable full-scale build may be used deliberately for performance or
fault-injection testing, but it is not a normative prerequisite for publication
and must not be presented as adding assurance merely because its useful output
is deleted.

Where a full deterministic build has completed successfully, the normal
workflow should retain or publish its evidence rather than repeat the same work
solely because the invocation was named a rehearsal.

### Original and revised requirements

| Amendment ID | Original requirement or interpretation | Revised requirement |
|---|---|---|
| A1.1 | Later verification was implemented as cumulative semantic replay of unchanged ancestors | Later boundaries authenticate accepted immediate-parent proof and replay only their own transformation; cumulative replay is exceptional deep audit, while confirmatory promotion authenticates existing proof |
| A1.2 | Verification authority existed only transiently in a process | Complete semantic verification may issue reusable create-only evidence |
| A1.3 | Exact source revision or whole-module identity could invalidate all cached work | Exact revision remains provenance; semantic invalidation is scoped to code and policy capable of changing the protected claim |
| A1.4 | A provenance failure could block use of an otherwise unchanged artefact without distinguishing the failed claim | Artefact validity, provenance, readiness and confirmatory authority are reported separately |
| A1.5 | A nonqualifying readiness result could prevent retention of a valid foundation | Every valid foundation is publishable; readiness gates only downstream authority |
| A1.6 | Any confirmatory-instrument provider gap could be interpreted as automatic invalidity or insufficiency | Gaps remain visible and affect readiness according to the separately frozen coverage policy |
| A1.7 | Full disposable rehearsal could become an expected operational stage | Preflight is cheap; full disposable execution is optional test activity, not a production research prerequisite |

### Invariant-matrix additions

| Invariant | Required authenticated proof | Failure behaviour |
|---|---|---|
| Verification receipt | Exact artefact and closure identities, immediate inputs, accepted verifier contract/version and claim-scoped verifier identity | Reject or reverify the receipt's exact artefact; do not rebuild unchanged data solely because verifier authority changed |
| Claim-scoped invalidation | Causal relationship between the changed input, code or policy and the protected claim | Invalidate only affected claims and descendants; unrelated operational or provenance changes do not revoke artefact validity |

### Evidence and migration impact

- Existing Stage 6 and Stage 7 immutable artefacts are not invalidated by this
  amendment.
- Existing artefacts may be reverified or promoted without rebuilding when
  their exact bytes and producer semantics remain unchanged.
- Existing operational checkpoints are disposable caches rather than research
  evidence. They may be adopted when their relevant semantic identities can be
  authenticated; otherwise their invalidation does not invalidate the source
  artefacts.
- Existing nonqualifying or failed runs remain retained evidence.
- This amendment does not declare any current Stage 8 foundation qualifying and
  does not authorise holdout access.
- The current Stage 8 readiness disposition must be recomputed under the
  separately frozen readiness policy before downstream authority is granted.

### Requirements preserved unchanged

This amendment does not weaken:

- create-only publication;
- canonical serialization and semantic identities;
- exact bounded file-tree closure;
- byte hashing and safe path requirements;
- mutation testing;
- source-class separation;
- declared provider-history availability;
- provider-session versus observed-bar distinctions;
- prohibition on fabricated or forward-filled observations;
- chronological folds, purging and embargo;
- holdout isolation;
- explicit failure and gap evidence; and
- immutable Stage 6 acquisition outcomes and correction policy.

### Consequences

Ordinary local research can reuse authenticated immutable history and
deterministic derived work without recursively repeating unchanged semantic
verification.

Confirmatory promotion is replay-free authority over an exact accepted verification receipt. The
semantic verification cost is paid once per exact artefact and accepted verifier contract, not again
at promotion or every downstream boundary.

The system must maintain explicit verifier-version and revocation policy.
Ordinary local iteration does not claim resistance to deliberate self-forgery.

## Amendment 2 — Immediate-parent verification and replay-free promotion

- **Status:** Accepted
- **Date:** 2026-08-14
- **Amends:** Independent replay, verification evidence, confirmatory promotion, identity and compatibility

### Context

Amendment 1 made ordinary verification boundary-local but still required cumulative semantic replay at
confirmatory promotion. That repeated already-established work and mixed verification with authority.
The same protection is obtained more directly by authenticating the exact accepted proof and granting
explicit promotion authority over it.

### Decision

1. **Immediate-parent proof.** A boundary authenticates its accepted immediate-parent verification or
   promotion evidence and independently proves only the transformation and claims it introduces. A child
   binds the parent's semantic, closure and verification or promotion identities; it does not copy or
   recursively prove the parent's complete ancestry.
2. **Prove once, authenticate many.** A successful deep verifier issues a durable create-only receipt for
   its exact artefact, immediate-parent authority, verifier contract and completed-check set. Ordinary
   descendants authenticate that receipt instead of rerunning the verifier.
3. **Promotion grants authority.** Confirmatory promotion authenticates the exact accepted receipt,
   required evidence and readiness class, verifier acceptance and explicit operator authorisation, then
   writes a create-only promotion. Promotion does not replay already-proved semantics.
4. **Deep audit is exceptional.** Semantic replay is repeated only after verifier revocation, when a
   discovered defect may affect an established claim, or when the operator explicitly requests an audit.
5. **Exact bytes are authenticated lazily.** A manifest and receipt establish the declared closure and
   expected child byte identities. A consumer hashes and validates each child it consumes, but does not
   hash or decode unused children merely because they are present.
6. **Identity classes remain distinct.** `semantic_id` identifies scientific meaning, `closure_id` the
   exact physical representation, `verification_id` the completed proof, and `promotion_id` the granted
   authority. Execution provenance is separate. Paths, timestamps, file layout, JSON formatting,
   whole-application revision and physical child hashes are not semantic identity unless they change the
   claimed computation.
7. **The trust mechanism stays proportional.** This single-user system does not add signatures, PKI,
   transparency logs, a generic trust service or a hostile-operator threat model.
8. **Compatibility requires a named current need.** A temporary reader or migration bridge must name the
   retained evidence it serves and the PR that deletes it after migration. There are no dual writers or
   permanent legacy paths for old fixtures or hypothetical consumers.

### Consequences

This amendment changes authority for the planned Stage 6 through R2 handoffs; it does not claim those
implementation migrations are complete. Scientific, source-class, causal, holdout, create-only and
exact-consumed-byte controls remain unchanged. Existing immutable evidence is migrated through new
artefacts and receipts where required, without provider reacquisition or in-place mutation.
