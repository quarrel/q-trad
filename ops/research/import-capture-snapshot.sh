#!/usr/bin/env bash
set -euo pipefail

umask 077

readonly archive="${QTRAD_SNAPSHOT_ARCHIVE:?QTRAD_SNAPSHOT_ARCHIVE is required}"
readonly checksum="${QTRAD_SNAPSHOT_CHECKSUM:?QTRAD_SNAPSHOT_CHECKSUM is required}"
readonly manifest="${QTRAD_SNAPSHOT_MANIFEST:?QTRAD_SNAPSHOT_MANIFEST is required}"
readonly target_database="${QTRAD_RESEARCH_DATABASE:?QTRAD_RESEARCH_DATABASE is required}"
readonly evidence="${QTRAD_RESEARCH_IMPORT_EVIDENCE:?QTRAD_RESEARCH_IMPORT_EVIDENCE is required}"
readonly expected_source="${QTRAD_EXPECTED_CAPTURE_SOURCE_ID:?QTRAD_EXPECTED_CAPTURE_SOURCE_ID is required}"
readonly expected_universe_hash="${QTRAD_EXPECTED_UNIVERSE_HASH:?QTRAD_EXPECTED_UNIVERSE_HASH is required}"
readonly maintenance_database="${QTRAD_RESEARCH_MAINTENANCE_DATABASE:-postgres}"

[[ -f "$archive" && ! -L "$archive" ]]
[[ -f "$checksum" && ! -L "$checksum" ]]
[[ -f "$manifest" && ! -L "$manifest" ]]
[[ "$target_database" =~ ^qtrad_research_[a-z0-9_]{1,40}$ ]]
[[ "$expected_source" =~ ^[a-z0-9][a-z0-9._-]{0,63}$ ]]
[[ "$expected_universe_hash" =~ ^[0-9a-f]{64}$ ]]
[[ -d "$(dirname "$evidence")" ]]
[[ ! -e "$evidence" ]]

archive_name="$(basename "$archive")"
readonly archive_name
[[ "$archive_name" =~ ^qtrad-capture-[0-9]{8}T[0-9]{6}Z\.dump$ ]]
[[ "$(wc -l < "$checksum")" == 1 ]]
checksum_sha="$(cut -d ' ' -f 1 "$checksum")"
checksum_name="$(sed -n 's/^[0-9a-fA-F]\{64\}  //p' "$checksum")"
actual_archive_sha="$(sha256sum "$archive" | cut -d ' ' -f 1)"
[[ "$checksum_sha" == "$actual_archive_sha" ]]
[[ "$checksum_name" == "$archive_name" ]]

manifest_schema="$(jq -er '.schema' "$manifest")"
case "$manifest_schema" in
  qtrad-capture-backup-v1)
    jq -e \
      --arg archive "$archive_name" \
      --arg sha256 "$actual_archive_sha" \
      --arg universe_hash "$expected_universe_hash" \
      '.schema == "qtrad-capture-backup-v1" and .archive == $archive
        and .sha256 == $sha256 and .database == "qtrad_capture"
        and .universe_hash == $universe_hash
        and (.created_at | test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"))
        and (.capture_image | test("@sha256:[0-9a-f]{64}$"))
        and (.postgres_image | test("@sha256:[0-9a-f]{64}$"))' \
      "$manifest" > /dev/null
    capture_source_id="$expected_source"
    universe_name="unknown-v1"
    migration_version="${QTRAD_EXPECTED_V1_MIGRATION_VERSION:?QTRAD_EXPECTED_V1_MIGRATION_VERSION is required for a v1 backup}"
    [[ "$migration_version" =~ ^[0-9a-f]{4,32}$ ]]
    manifest_identity="$(jq -cS . "$manifest")"
    manifest_identity_sha="$(printf '%s' "$manifest_identity" | sha256sum | cut -d ' ' -f 1)"
    ;;
  qtrad-capture-backup-v2)
    jq -e \
      --arg archive "$archive_name" \
      --arg sha256 "$actual_archive_sha" \
      --arg source "$expected_source" \
      --arg universe_hash "$expected_universe_hash" \
      '(keys | sort) == ["archive", "capture_image", "capture_source_id", "created_at",
        "database", "manifest_sha256", "migration_version", "postgres_image", "schema",
        "sha256", "universe_hash", "universe_name"]
        and .archive == $archive and .sha256 == $sha256 and .database == "qtrad_capture"
        and .capture_source_id == $source and .universe_hash == $universe_hash
        and (.created_at | test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"))
        and (.capture_source_id | test("^[a-z0-9][a-z0-9._-]{0,63}$"))
        and (.universe_name | test("^[a-z0-9][a-z0-9._-]{0,63}$"))
        and (.universe_hash | test("^[0-9a-f]{64}$"))
        and (.capture_image | test("@sha256:[0-9a-f]{64}$"))
        and (.postgres_image | test("@sha256:[0-9a-f]{64}$"))
        and (.migration_version | test("^[0-9a-f]{4,32}$"))
        and (.manifest_sha256 | test("^[0-9a-f]{64}$"))' \
      "$manifest" > /dev/null
    manifest_identity="$(jq -cS 'del(.manifest_sha256)' "$manifest")"
    manifest_identity_sha="$(printf '%s' "$manifest_identity" | sha256sum | cut -d ' ' -f 1)"
    [[ "$manifest_identity_sha" == "$(jq -er '.manifest_sha256' "$manifest")" ]]
    capture_source_id="$(jq -er '.capture_source_id' "$manifest")"
    universe_name="$(jq -er '.universe_name' "$manifest")"
    migration_version="$(jq -er '.migration_version' "$manifest")"
    ;;
  *)
    printf 'unsupported capture backup manifest schema: %s\n' "$manifest_schema" >&2
    exit 65
    ;;
esac

pg_restore --list "$archive" > /dev/null

existing_database="$(
  psql --no-psqlrc --tuples-only --no-align --set ON_ERROR_STOP=1 \
    --set target_database="$target_database" --dbname "$maintenance_database" \
    --command "SELECT datname FROM pg_database WHERE datname = :'target_database';"
)"
[[ -z "$existing_database" ]]

created=0
cleanup_failed_import() {
  local exit_code=$?
  if ((exit_code != 0 && created == 1)); then
    dropdb --if-exists --maintenance-db "$maintenance_database" "$target_database" || true
  fi
  exit "$exit_code"
}
trap cleanup_failed_import EXIT

createdb --maintenance-db "$maintenance_database" --template template0 "$target_database"
created=1
pg_restore --exit-on-error --no-owner --no-privileges \
  --dbname "$target_database" "$archive"
psql --no-psqlrc --set ON_ERROR_STOP=1 --dbname "$target_database" \
  --command "REVOKE CONNECT ON DATABASE \"$target_database\" FROM PUBLIC;" > /dev/null

restored_evidence="$(
  psql --no-psqlrc --tuples-only --no-align --set ON_ERROR_STOP=1 \
    --field-separator '|' --dbname "$target_database" \
    --command 'SELECT (SELECT version_num FROM alembic_version),
      (SELECT count(*) FROM raw.market_messages),
      (SELECT count(*) FROM canonical.events);'
)"
IFS='|' read -r restored_migration raw_count canonical_count <<< "$restored_evidence"
[[ "$restored_migration" == "$migration_version" ]]
[[ "$raw_count" =~ ^[1-9][0-9]*$ ]]
[[ "$canonical_count" =~ ^[0-9]+$ ]]

manifest_file_sha="$(sha256sum "$manifest" | cut -d ' ' -f 1)"
capture_image="$(jq -er '.capture_image' "$manifest")"
postgres_image="$(jq -er '.postgres_image' "$manifest")"
source_created_at="$(jq -er '.created_at' "$manifest")"
import_identity="$(
  jq -cS -n \
    --arg schema "qtrad-research-snapshot-import-v1" \
    --arg imported_at "$(date --utc +%Y-%m-%dT%H:%M:%SZ)" \
    --arg target_database "$target_database" \
    --arg source_manifest_schema "$manifest_schema" \
    --arg source_manifest_file_sha256 "$manifest_file_sha" \
    --arg source_manifest_identity_sha256 "$manifest_identity_sha" \
    --arg source_archive_sha256 "$actual_archive_sha" \
    --arg source_created_at "$source_created_at" \
    --arg capture_source_id "$capture_source_id" \
    --arg universe_name "$universe_name" \
    --arg universe_hash "$expected_universe_hash" \
    --arg capture_image "$capture_image" \
    --arg postgres_image "$postgres_image" \
    --arg migration_version "$restored_migration" \
    --argjson raw_message_count "$raw_count" \
    --argjson canonical_event_count "$canonical_count" \
    '{schema:$schema, imported_at:$imported_at, target_database:$target_database,
      source_manifest_schema:$source_manifest_schema,
      source_manifest_file_sha256:$source_manifest_file_sha256,
      source_manifest_identity_sha256:$source_manifest_identity_sha256,
      source_archive_sha256:$source_archive_sha256, source_created_at:$source_created_at,
      capture_source_id:$capture_source_id, universe_name:$universe_name,
      universe_hash:$universe_hash, capture_image:$capture_image,
      postgres_image:$postgres_image, migration_version:$migration_version,
      raw_message_count:$raw_message_count, canonical_event_count:$canonical_event_count}'
)"
import_sha="$(printf '%s' "$import_identity" | sha256sum | cut -d ' ' -f 1)"
jq --arg import_sha256 "$import_sha" '. + {import_sha256:$import_sha256}' \
  <<< "$import_identity" > "$evidence"

trap - EXIT
printf '%s\n' "$evidence"
