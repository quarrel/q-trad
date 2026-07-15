# ADR 0023: frozen-release readiness identity

- **Status:** Accepted
- **Date:** 2026-07-15

## Context

The first `capture-v1` OCI candidate runs image digest
`3ca07eaee8cf1500546c1779bb0732d9260b085e8a179e3514a507da4ee77d80`. Its readiness
response proves seven-instrument freshness and projection position but predates the later endpoint
field that reports the capture-universe configuration hash. The exact hash is nevertheless persisted
on every ingestion run. Five pre-candidate rows left `RUNNING` by the superseded stop contract mean
that an unqualified “some ingestion run exists” check is insufficient until reviewed reconciliation.

The hour-24 audit also found that OCI's installed Docker Compose emits one JSON object per line for
`ps --format json`, while another supported Compose version emits one JSON array. Qualification
helpers must not make their evidence depend on that presentation difference.

## Decision

- Normalise either Compose representation into one bounded array before applying the same exact
  service, image, state and health checks.
- Later images must expose the expected configuration hash in readiness. A missing or mismatched
  endpoint identity fails closed.
- For this exact frozen application digest only, the automatic qualification snapshot may record
  `LEGACY_SINGLE_MATCHING_RUN_SHARED_RELEASE` instead. That basis is valid only after reconciliation
  leaves exactly one `RUNNING` ingestion row in total, that row has the expected configuration hash,
  no pre-candidate non-terminal row remains, and the normal exact descriptor, source, migration,
  application/PostgreSQL-image, Compose, seven-channel readiness, adapter and projection gates all
  pass.
- Record the identity basis in the self-hashed automatic evidence. The fallback is selected by the
  hard-coded immutable digest, not an operator-supplied waiver or a mutable tag.

## Consequences

The current candidate can prove configuration-bound readiness without changing its image or
invalidating the frozen interval. This is a composite proof, not permission to accept `ready=true`
alone. A second running ingestion record, a non-matching hash, an unreconciled legacy row, image or
descriptor drift, or a missing endpoint hash on any later image prevents automatic qualification.
No production endpoint, order capability, database reinterpretation or collector mutation is added.
