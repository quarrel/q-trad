# q-trad evidence and retained-execution governance

## Purpose and routing

This document governs q-trad identity-bearing contracts, immutable research/operational evidence,
compatibility and migration, retained-scale execution, verification, promotion and consequential
research authority. It applies whether work is performed directly or through MAP. MAP supplies
generic orchestration procedure; this document supplies q-trad authority and scientific constraints.

For direct work, read only the relevant active-plan sections and accepted ADR or runbook governing the
boundary. For delegated MAP work, follow the exact supplied plan/ADR/runbook references; do not load a
whole authority merely because the packet cites one section. Historical documents under `docs/archive/`
are evidence, not current authority unless an active source names the surviving boundary.

## Evidence handoff

The governing rule is:

> Prove a transformation once; authenticate that proof thereafter.

For an ordinary boundary:

1. authenticate the immediate parent's accepted receipt or promotion;
2. perform the current transformation once;
3. publish the current immutable artefact after bounded structural checks;
4. independently replay the current transformation once;
5. issue a create-only verification receipt; and
6. let ordinary descendants authenticate that receipt instead of replaying the ancestry.

A child proves the claims introduced by its own boundary. Deep audit is exceptional: use it after
verifier revocation, a defect that may affect an established claim, or explicit operator authority.

Do not solve an efficiency problem by caching unnecessary work. Remove the unnecessary work first.

## Identity classes

Keep these classes separate:

- semantic identity: what data, configuration or result means under its contract;
- closure identity: the exact physical representation and declared child bytes;
- verification identity: create-only proof that a named verifier completed a named check set against
  exact artefact and immediate-parent authority;
- promotion identity: authority to use already-verified evidence at a consequential boundary; and
- execution provenance: code, image, Python and library identity associated with execution.

Include provenance in semantic identity only when it changes the scientific meaning or numerical
policy. Paths, timestamps, formatting, layout, whole-application identity and child byte hashes are
not semantic merely because they are easy to hash.

For a non-trivial identity-bearing contract change, classify every field as semantic,
closure/physical, provenance, verifier or promotion/authority, and record that classification in the
PR description.

## Authentication, receipts and promotion

A manifest declares expected closure and child byte identities. Ordinary authentication establishes
the manifest, exact expected tree and applicable receipt/promotion. Hash and validate a child when
the current operation consumes it; a consumer selecting 14 of 120 parts does not decode the other
106 merely to prove unused work.

Do not copy parent closures into child artefacts for hypothetical portability. Bind the parent's
semantic, closure and verification/promotion identities.

A reusable receipt is small, create-only and boundary-specific. It normally binds:

- artefact contract/version, semantic identity, closure identity and manifest identity;
- immediate-parent semantic and verification/promotion identities;
- verifier contract/version and explicit completed-check set;
- claim-scoped verifier identity where needed; and
- receipt identity.

Promotion authenticates the exact receipt, confirms the required evidence/readiness class and
verifier acceptance, records explicit operator authority and writes a create-only promotion. It is
not another semantic replay. Holdout opening and consumption remain distinct irreversible
authorities.

Do not create signing infrastructure, PKI, transparency logs or a generic evidence service. The
threat model is implementation error, accidental mutation/corruption, stale or mismatched inputs,
unsupported claims, look-ahead, leakage and false provenance—not the sole operator deliberately
forging every record.

Prefer explicit stage-specific functions and small frozen values. Allow local duplication until
concrete repeated uses demonstrate a stable abstraction that reduces total code and cognitive burden.

## Compatibility, state and migration

Before a decision-grade result, internal research schemas, events, CLIs and APIs may change
incompatibly. Compatibility requires one named current reason:

- retained expensive evidence awaiting migration;
- an active operation that cannot be cut over in the same change; or
- a decision-grade result that explicitly cites the older contract.

An old fixture/test/schema or hypothetical consumer is not enough. A temporary bridge must name its
dependent evidence/operation, remain outside normal construction where practical, avoid dual writers,
define its deletion trigger and be removed once migration succeeds.

Classify state before mutation:

- development databases, projections and failed local experiments are disposable unless retained by
  an experiment record;
- cited market data, manifests, forecasts and configurations are research evidence;
- material collector failures may be incident evidence; and
- collector raw/canonical history remains operational evidence until reviewed authority says
  otherwise.

Never overwrite immutable evidence in place. Migration creates a new artefact, independently verifies
it and records the relationship. Never rewrite or selectively delete running collectors' raw or
canonical history.

## Preflight before retained or expensive execution

Before the first retained-scale or computationally significant production path:

1. run a correctly shaped micro-sample through the exact production CLI, persistence path, schemas,
   authority handoffs, planned outputs, state transitions and verifier;
2. treat that run as implementation evidence only;
3. inventory every variable-length or nested output and immediate encoder/decoder limit, including
   cardinality, bytes, depth, partitions, closure shape, transactions and resources;
4. exercise a retained-equivalent bound or make an authorised read-only/synthetic projection with an
   explicit safety margin for every scale-sensitive dimension;
5. trace outputs through the next durable authority, including sibling outputs, authenticators,
   verifiers and publication consumers; and
6. record every untested assumption, exact source/destination and stop condition.

A small row slice proves only the dimensions it exercises. It does not discharge cardinality,
representation or resource risk. Do not launch a multi-hour or 10+ minute retained run until this
preflight establishes a credible end-to-end path.

Before a command that may run for 10+ minutes, record exact command/code/input/output identity, start
time, process/session identity, durable terminal evidence, resource/stop limits and authority for
follow-up actions. Observe via a supported event-aware wait or a low-cost passive monitor. Activity,
elapsed time, checkpoints and partial files do not prove completion.

## Failure and rerun mode

A failed retained-scale or 10+ minute run changes operating mode. Preserve truthful state and do not
immediately replay the next patch.

Before another attempt:

1. perform a read-only look-ahead audit from the failing boundary through the next durable authority;
2. inventory sibling outputs, nested collections, aggregate/resource/transaction limits,
   create-only destinations, orphan/symlink rules and downstream consumers;
3. use authorised metadata/counts or bounded constructions to project each scale-sensitive boundary;
4. fix the owning mechanism and add regression coverage at the real trust/persistence boundary;
5. run the exact production path through every authorised reversible or fail-closed transition; and
6. record the audit, projections, assumptions, exact destinations and downstream stop conditions in
   the named durable execution record.

Search the finite path for the same failure class. Fixing the first failing child is incomplete when a
sibling can fail next. A validation or rerun grants no provider, promotion, holdout, irreversible or
other special-state authority.

After two attempts fail before the same durable authority, a third requires fresh independent review
of the finite inventory and downstream gate across all applicable scale-sensitive dimensions. Do not
raise limits, shrink required evidence or weaken identity to force success. Reuse a valid immutable
upstream artefact when a downstream gate failed; rebuild it only after demonstrated invalidation and
authority for a create-only replacement.

## Validation and change records

When performance matters, prefer deterministic work counts such as verifier/replay invocations,
files/bytes hashed, parts/rows decoded, feature recomputations and model fits. An ordinary descendant
normally performs zero parent semantic replays.

For an evidence-boundary PR, state:

- old redundant or incorrect work and the new immediate-parent handoff;
- semantic and closure/physical identity impact;
- retained-evidence or migration impact;
- temporary compatibility and deletion trigger, if any;
- work-count regression evidence; and
- exact validation.

Before claiming an exception has no persistence/invalidation impact, trace semantic, closure,
provenance, verifier, promotion, checkpoint and cache identities plus downstream authority boundaries.
Update active documents only when current claims change; use an ADR for a durable architectural
decision, not an ordinary implementation detail or reversible experiment.
