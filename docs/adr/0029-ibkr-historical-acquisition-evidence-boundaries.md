# ADR 0029: IBKR historical acquisition evidence boundaries

- **Status:** Accepted
- **Date:** 2026-08-02

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

The trust root is the accepted contract and policy definitions, an independently invoked verifier
identified by an exact clean application commit and image, SHA-256, and the operator decisions that the
relevant contract explicitly makes authoritative. Operator acceptance establishes the chosen mapping
or policy; it does not prove an IBKR fact.

Everything else is untrusted input until the verifier for its boundary authenticates and interprets
it. This includes capability-review JSON, selection files, archives, configuration, caller-supplied
runtime labels, filesystem paths, database rows, clocks, callback payloads and ordering, provider
errors and schedules, availability claims, child manifests and summary fields. Earlier verified
artefacts become authenticated inputs to the next boundary, but only for the claims their contracts
make.

The named boundaries are:

| Artefact or layer | Untrusted input | Acceptance boundary |
|---|---|---|
| Capability review | Probe specification and provider callbacks | Existing capability-review verifier authenticates the complete recorded review; it remains capability evidence, not a contract selection |
| `qtrad-ibkr-contract-selection-v1` | Capability review and operator decisions | Selection verifier reconstructs exactly one decision per canonical instrument and authenticates each accepted product-aware fingerprint |
| `qtrad-ibkr-acquisition-runtime-v1` | Archives, configuration and runtime labels | Runtime verifier re-hashes exact archives and sanitized configuration and authenticates the clean application commit/image and matched Gateway/API identity |
| `qtrad-ibkr-historical-request-profile-v1` | Canary results and operator freeze | Profile verifier authenticates the fixed durations, timeouts, concurrency, retry and pacing policy against its recorded canary evidence |
| `qtrad-ibkr-historical-plan-v1` | Selection, runtime lock, profile and requested range | Plan verifier reloads those artefacts and reconstructs the exact request set and half-open coverage without PostgreSQL or IB Gateway |
| `qtrad-ibkr-historical-request-result-v1` | Attempts, callbacks and terminal state read from PostgreSQL | Request-result verifier reconstructs callback ownership, terminal selection, normalized rows or sessions and disposition for one planned request |
| `qtrad-ibkr-historical-result-v1` | Plan and request-result children | Aggregate verifier derives the exact expected child set, rejects omissions, additions and orphans, and recomputes coverage and dispositions |
| `qtrad-provider-historical-observations-v1` and availability selector | Verified aggregate result and declared-delay policy | Observation verifier replays rows and declared availability, and proves the absence of native receive/persistence timestamps |
| Source-specific foundation and readiness | Verified observations, selector and foundation configuration | Foundation verifier replays panels, targets, folds, support and source/availability lineage; readiness recomputes the fixed six-target, three-group gate |

### Digest claims

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
create-only files and independent verification.

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

### Recovery, retry and request success

Only a failure classified by the frozen policy as transient is retryable: a transport timeout,
disconnect, transient farm unavailability, pacing rejection eligible for deferred retry, or a database
failure proven to have occurred before provider I/O. Each provider retry is a new append-only attempt
bound to the connection generation it uses, within the fixed attempt limit and backoff policy. A
database failure after send recovers the persisted attempt; it cannot authorize another provider
request until the fixed state machine has classified that attempt as incomplete and retryable.

Contract or runtime mismatch, invalid parameters, entitlement denial, conflicting or invalid
callbacks, evidence/closure failure, unknown global state requiring an operator, and exhausted retry
limits are not silently retried. `NO_DATA_RETURNED` and `SESSION_EVIDENCE_UNAVAILABLE` are terminal
evidence, not reasons to mutate or split the request. Unrelated planned requests may continue.

A bar request succeeds only when the first eligible attempt has a current-generation completion
callback, at least one valid accepted row, a complete persisted callback closure, no conflicting
duplicate or invalid OHLC, deterministic half-open interval ownership, and passes independent
request-result verification. A schedule request succeeds under the same closure rules with a
structurally valid completed provider schedule; it may validly declare no active interval. Partial or
superseded-generation callbacks remain evidence but cannot contribute accepted rows. Once selected,
the first verified success is never rerun or replaced.

### Independent replay and absence claims

Independent replay means loading the original files in a new verifier path, authenticating their
complete closure, deriving expected identities and outputs from lower-layer children and fixed policy,
and comparing recomputed results. Re-reading builder summaries, trusting a pass flag, querying a
mutable projection or merely checking digests is not replay.

Replay proceeds cumulatively: selection from capability review and decisions; runtime lock from local
archives/configuration/application identity; plan from selection, runtime and profile; request result
from attempt/callback closure; aggregate from plan and request results; provider observations from the
aggregate and availability policy; foundation/readiness from observations, selector and foundation
configuration. A later layer cannot strengthen an unsupported claim made by an earlier one.

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

## Invariant matrix

| Invariant | Required authenticated proof | Failure behaviour |
|---|---|---|
| Contract identity | Exactly one canonical decision; accepted mapping binds capability-review digest and complete product-aware fingerprint | Duplicate, substitution, missing decision or identity-field drift blocks that contract and is retained explicitly |
| Runtime identity | Recomputed archive, configuration, clean commit and image digests; matched Gateway/API pair; paper endpoint policy | Assertion, dirty source, mutable tag, digest mismatch or version mismatch fails before planning or provider I/O |
| Plan identity | Verified selection, runtime lock and profile plus exact range and deterministic complete request set | Mutation, overlap, planner-created gap, unsafe parameter or unexpected request requires a new plan |
| Attempt identity | Planned-request identity, unique ordinal, connection generation and append-only start/terminal record | Reuse, rewrite or ambiguous generation makes the attempt ineligible for success |
| Callback ownership | Planned request/API request correlation, current generation, monotonic arrival sequence and half-open interval ownership | Stale, uncorrelated or boundary-external callbacks remain raw evidence and cannot become accepted rows |
| Terminal result selection | First independently valid success, otherwise deterministic terminal disposition after the fixed retry policy | Partial completion, later replacement, unclassified error or successful-request rerun fails verification |
| File closure | Safe relative regular-file references binding contract, semantic ID and SHA-256; independently derived exact bounded child set | Traversal, ancestor or child symlink, missing/duplicate/substituted/oversized child or orphan fails closed |
| Availability semantics | Source class and selector bind `BAR_END_PLUS_DECLARED_PROVIDER_DELAY`, exact delay and recomputed `available_at` | Unknown selector, native/provider mixing, fabricated native timestamps or changed delay changes identity or fails |
| Session semantics | Verified schedule callback closure and provider-declared intervals kept separate from observed bars | Failed or absent schedule is `UNKNOWN`, never inactive; schedule does not imply quote continuity |
| Foundation lineage | Verified aggregate result, contract selection, provider observations, selector, source class, configuration, panels, targets and folds | Mixed source, altered child, unsupported target/group or incomplete replay blocks readiness and downstream R2 registration |

## Consequences

Operational recovery remains possible without weakening immutable evidence: unfinished attempts may
be retried, while registered plans and published outcomes remain fixed. Every later artefact has one
named verifier and a bounded claim. This adds explicit contracts and mutation tests in later stages,
but prevents database state, provider declarations or self-authenticating summaries from masquerading
as research evidence.

This ADR adds no production code, provider request or dataset. Stages 1–8 remain separately reviewed
and authorised.
