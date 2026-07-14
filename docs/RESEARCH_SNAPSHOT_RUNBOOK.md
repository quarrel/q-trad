# Research snapshot runbook

Use this path to turn an immutable collector backup into a separate writable research database.
It does not connect to IG and must not target the collector database.

## 1. Acquire one complete object set

Select one `daily/qtrad-capture-<UTC>.dump` object in the private backup bucket and download that
dump plus its exact `.sha256` and `.manifest.json` companions into a new local, private directory.
Use the OCI Console or an operator OCI CLI profile outside the repository. Do not copy OCI API keys
or Object Storage credentials into the Dev Container, repository or `capture.env`.

Do not rename any member of the set. A v1 manifest is accepted only because existing qualification
backups predate ADR 0019; v2 is the normal contract after a later collector release.

## 2. Import without overwriting

Choose a new database name. The importer accepts only the `qtrad_research_*` namespace and refuses
an existing database or evidence file. PostgreSQL connection details use normal libpq variables;
keep `PGPASSWORD` out of shell history and tracked files.

```bash
export PGHOST=db
export PGPORT=5432
export PGUSER=qtrad
# Supply PGPASSWORD through a private environment mechanism if required.

export QTRAD_SNAPSHOT_ARCHIVE=/private/snapshot/qtrad-capture-20260714T000000Z.dump
export QTRAD_SNAPSHOT_CHECKSUM="$QTRAD_SNAPSHOT_ARCHIVE.sha256"
export QTRAD_SNAPSHOT_MANIFEST="$QTRAD_SNAPSHOT_ARCHIVE.manifest.json"
export QTRAD_RESEARCH_DATABASE=qtrad_research_capture_20260714
export QTRAD_RESEARCH_IMPORT_EVIDENCE=/private/snapshot/import-20260714.json
export QTRAD_EXPECTED_CAPTURE_SOURCE_ID=oci-sydney-capture-1
export QTRAD_EXPECTED_UNIVERSE_HASH='<capture-v1 configuration hash>'

# Required only for a legacy v1 backup; use the migration recorded by its restore evidence.
export QTRAD_EXPECTED_V1_MIGRATION_VERSION=0003

ops/research/import-capture-snapshot.sh
```

The script verifies the three files before creating anything, restores without source ownership or
privileges, checks migration and row counts, revokes public database access and writes immutable
import evidence. On failure it removes only the database it created during that invocation.

## 3. Prepare and export from the copy

Point both application database URLs at the new database. Set the capture-source ID to the import
record's value and use a new empty research output directory. Apply expand-only migrations to this
copy when the source snapshot predates the exporter; never apply them to the collector as part of
this workflow.

```bash
export QTRAD_DATABASE_URL='postgresql+asyncpg://qtrad:<private-password>@db:5432/qtrad_research_capture_20260714'
export QTRAD_MIGRATION_DATABASE_URL='postgresql+psycopg://qtrad:<private-password>@db:5432/qtrad_research_capture_20260714'
export QTRAD_CAPTURE_SOURCE_ID=oci-sydney-capture-1
export QTRAD_RESEARCH_ROOT=/private/research/capture-20260714

uv run qtrad db upgrade
uv run qtrad research export \
  --universe config/capture-v1.toml \
  --snapshot-import-evidence /private/snapshot/import-20260714.json \
  --start 2026-07-01T00:00:00Z \
  --end 2026-07-14T00:00:00Z
```

Export validates that the configured database name, capture source and selected universe match the
import evidence. Its schema-version-2 manifest then binds the snapshot import/archive identity as
well as the existing application, range, file, bar, gap, provenance and coverage evidence.

## 4. Retention and disposal

Keep the source bundle and import evidence until every derived manifest has reached its required
retention. The research database is a mutable working copy and is not the durable dataset; verified
Parquet manifests are. Drop a research database only by an explicit operator action after confirming
that no active export, replay or investigation uses it.

