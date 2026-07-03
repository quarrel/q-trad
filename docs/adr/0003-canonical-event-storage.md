# ADR 0003: canonical event storage

- **Status:** Accepted
- **Date:** 2026-07-02

## Decision

Store redacted raw inputs and immutable canonical events in PostgreSQL. Build current-state tables as disposable projections. Store research datasets as versioned Parquet with manifests.

## Consequences

Corrections are new events. Projection rebuild and deterministic replay are acceptance requirements.
