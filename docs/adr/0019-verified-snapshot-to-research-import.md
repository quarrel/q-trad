# ADR 0019: verified snapshot-to-research import

**Status:** Accepted

## Context

The collector database is capture-only. Research export writes run and manifest records, so it
must not target the collector or the exceptional read-only tunnel role. Object Storage backups
already provide a natural transfer unit, but the first backup manifest did not bind capture-source
or migration identity, and the restore verifier assumed migration `0003`.

A downloaded dump, a similarly named manifest and a manually created database are not sufficient
provenance for an immutable research dataset. Import must prove which source, universe, schema and
images produced the snapshot and must not replace an existing research database.

## Decision

- Future backups use `qtrad-capture-backup-v2`. Its canonical, self-hashed identity binds archive
  name/hash, creation time, capture source, universe name/hash, source database, application and
  PostgreSQL image digests, and Alembic migration version.
- Restore verification remains compatible with v1 backups. A v1 restore requires the explicitly
  configured expected migration version; v2 reads it from verified manifest identity.
- A local import accepts an already downloaded dump, checksum and manifest. Transport from Object
  Storage is an operator concern and carries no database authority.
- Import requires the expected capture source and universe hash, verifies the archive and manifest,
  validates `pg_restore --list`, and creates only a new database named `qtrad_research_*`. Existing
  target databases and evidence files fail closed.
- Restore uses `--no-owner --no-privileges`. Failure removes only the database created by that
  invocation. Success revokes public connect and emits a non-overwriting, hash-verified import
  record containing source identity, migration and restored raw/canonical counts.
- A research export may bind `--snapshot-import-evidence`. The configured database, capture source
  and selected universe must match that evidence; the verified import and archive identity then
  enter the content-authenticated research manifest metadata.
- Schema upgrades, historical backfill and research exports occur only after import, against the
  separate writable copy. They never feed back into capture.

## Consequences

The normal collector-to-research path is auditable without granting research processes access to
the live capture database. Old v1 backups remain usable only with explicit operator assertions for
the missing source and migration fields; their weaker provenance remains visible as
`unknown-v1`. A v2 bundle removes those assertions.

Import evidence describes the initial restored copy, not every later mutation. The research
manifest separately binds the export application, requested range, bars and coverage evidence.
Database disposal and retention remain deliberate operator actions; the importer never replaces
or drops a pre-existing database.

