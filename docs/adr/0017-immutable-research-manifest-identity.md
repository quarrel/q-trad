# ADR 0017: immutable research manifest identity

- **Status:** Accepted
- **Date:** 2026-07-14

## Context

The original research manifest used the first 24 characters of the semantic bar-content hash as
both its identifier and filename. Re-exporting the same bars under another capture universe or
configuration therefore addressed the same manifest and could overwrite its evidence. Replay
checked repeatability but did not authenticate the manifest or individual Parquet files, and the
export labelled its gap count with a placeholder rather than observed coverage evidence.

Capture operations require an immutable, self-describing research input whose universe,
configuration, code/image identity, coverage, gaps and provenance can be verified independently.
The migration must remain compatible with rolling the application back to the prior image after
the forward schema has been applied.

## Decision

Research manifests written under schema version 2 are addressed by the first 24 characters of a
SHA-256 over their complete canonical identity. That identity includes creation time, exact
capture-universe name and configuration hash, requested UTC interval, row and time bounds,
semantic bar-content hash,
ordered file paths and byte hashes, and bounded metadata. The metadata records the application
version and image identity, exact instrument set, grouped bar coverage, provenance and basis
counts, observed live gaps and plan-scoped historical-coverage attempts. Missing, malformed or
out-of-universe evidence fails the export.

Export requires an explicit half-open UTC range. Parquet partitions are addressed by their own
semantic content, written exclusively under `bars-v2/` and never replaced. An existing
partition must decode to the expected canonical bars. Each manifest records its files' byte hashes
and the semantic hash of all decoded bars; replay verifies both layers, the manifest's canonical
hash, path ownership, row count and time bounds before accepting the data. Manifest files are also
created exclusively, bounded to 4 MiB and cannot silently replace another identity.

Schema-version-1 manifests remain readable and receive their original semantic verification. New
partitions use a separate namespace because an application rollback can still write the old
`bars/` layout; the old image therefore cannot overwrite data referenced by a schema-version-2
manifest. Migration `0006` adds nullable version-two columns and retains the old INSERT shape, so
the prior application can run after the forward migration. A database downgrade refuses to
discard version-two identity once such a manifest exists.

Research export is a writing workflow because it records its run and manifest. It runs against an
isolated restored database or another explicitly approved writable research copy, never through
the collector's read-only role and never against the frozen capture database. Replay uses the
verified manifest's configuration hash rather than the current runtime configuration.

## Consequences

Identical bars exported under different configurations or at different evidence times produce
distinct immutable manifests while safely sharing unchanged per-instrument/day content-addressed
partitions. Extending a range therefore writes only changed or new partitions rather than a new
copy of every prior date. Byte or metadata tampering fails replay. Forward schema application remains compatible with application
rollback, but a version-two manifest cannot be represented by the old database schema. Exporting a
large evidence set fails at the manifest bound rather than silently truncating it; an operator must
choose a narrower, explicit research slice.
