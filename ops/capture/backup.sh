#!/usr/bin/env bash
set -euo pipefail

umask 077

readonly root="${QTRAD_CAPTURE_ROOT:?QTRAD_CAPTURE_ROOT is required}"
readonly compose_file="$root/compose.capture.yaml"
readonly capture_env="${QTRAD_CAPTURE_ENV:-/etc/qtrad/capture.env}"
readonly backup_dir="${QTRAD_BACKUP_DIR:?QTRAD_BACKUP_DIR is required}"
readonly status_dir="${QTRAD_STATUS_DIR:?QTRAD_STATUS_DIR is required}"
readonly bucket="${QTRAD_BACKUP_BUCKET:?QTRAD_BACKUP_BUCKET is required}"
readonly oci_auth="${QTRAD_OCI_AUTH:-instance_principal}"
readonly weekly_day="${QTRAD_WEEKLY_BACKUP_DAY:-7}"
readonly local_retention_days="${QTRAD_LOCAL_BACKUP_RETENTION_DAYS:-2}"
timestamp="$(date --utc +%Y%m%dT%H%M%SZ)"
readonly timestamp
readonly basename="qtrad-capture-$timestamp.dump"
readonly archive="$backup_dir/$basename"
readonly checksum="$archive.sha256"
readonly manifest="$archive.manifest.json"
readonly status_file="$status_dir/backup-status.json"
readonly compose=(
  docker compose --env-file "$capture_env" --project-directory "$root" -f "$compose_file"
)
readonly oci=(oci --auth "$oci_auth")
capture_image="$(sed -n 's/^QTRAD_IMAGE=//p' "$capture_env")"
readonly capture_image
[[ "$capture_image" == *@sha256:* ]]

mkdir -p "$backup_dir" "$status_dir"
partial="$archive.partial"

record_failure() {
  local exit_code=$?
  rm -f "$partial"
  if ((exit_code != 0)); then
    local temporary_status
    temporary_status="$(mktemp "$status_dir/.backup-status.XXXXXX")"
    jq -n \
      --arg completed_at "$(date --utc +%Y-%m-%dT%H:%M:%SZ)" \
      --argjson exit_code "$exit_code" \
      '{success:false, completed_at:$completed_at, exit_code:$exit_code}' \
      > "$temporary_status"
    mv -f "$temporary_status" "$status_file"
  fi
  exit "$exit_code"
}
trap record_failure EXIT

"${compose[@]}" exec -T db \
  pg_dump --username=qtrad_capture --dbname=qtrad_capture --format=custom > "$partial"
mv "$partial" "$archive"

archive_sha="$(sha256sum "$archive" | cut -d ' ' -f 1)"
printf '%s  %s\n' "$archive_sha" "$basename" > "$checksum"
"${compose[@]}" exec -T db pg_restore --list < "$archive" > /dev/null

universe_evidence="$(
  docker run --rm --network none --read-only --tmpfs /tmp \
    --env UV_CACHE_DIR=/tmp/uv-cache \
    --env-file "$capture_env" "$capture_image" python -c \
    'import json; from qtrad.runtime.settings import Settings; from qtrad.runtime.universe import load_capture_universe; universe = load_capture_universe(Settings().capture_universe_path); print(json.dumps({"name": universe.name, "hash": universe.configuration_hash}, sort_keys=True))'
)"
universe_name="$(jq -er '.name' <<< "$universe_evidence")"
universe_hash="$(jq -er '.hash' <<< "$universe_evidence")"
postgres_image="$(sed -n 's/^QTRAD_POSTGRES_IMAGE=//p' "$capture_env")"
capture_source_id="$(sed -n 's/^QTRAD_CAPTURE_SOURCE_ID=//p' "$capture_env")"
migration_version="$(
  "${compose[@]}" exec -T db psql --username=qtrad_capture --dbname=qtrad_capture \
    --tuples-only --no-align --command 'SELECT version_num FROM alembic_version;'
)"
[[ "$universe_name" =~ ^[a-z0-9][a-z0-9._-]{0,63}$ ]]
[[ "$universe_hash" =~ ^[[:xdigit:]]{64}$ ]]
[[ "$postgres_image" == *@sha256:* ]]
[[ "$capture_source_id" =~ ^[a-z0-9][a-z0-9._-]{0,63}$ ]]
[[ "$migration_version" =~ ^[0-9a-f]{4,32}$ ]]

manifest_identity="$(
  jq -cS -n \
  --arg schema "qtrad-capture-backup-v2" \
  --arg created_at "$(date --utc +%Y-%m-%dT%H:%M:%SZ)" \
  --arg archive "$basename" \
  --arg sha256 "$archive_sha" \
  --arg database "qtrad_capture" \
  --arg capture_source_id "$capture_source_id" \
  --arg universe_name "$universe_name" \
  --arg universe_hash "$universe_hash" \
  --arg capture_image "$capture_image" \
  --arg postgres_image "$postgres_image" \
  --arg migration_version "$migration_version" \
  '{schema:$schema, created_at:$created_at, archive:$archive, sha256:$sha256,
    database:$database, capture_source_id:$capture_source_id, universe_name:$universe_name,
    universe_hash:$universe_hash, capture_image:$capture_image,
    postgres_image:$postgres_image, migration_version:$migration_version}'
)"
manifest_sha="$(printf '%s' "$manifest_identity" | sha256sum | cut -d ' ' -f 1)"
jq --arg manifest_sha256 "$manifest_sha" \
  '. + {manifest_sha256:$manifest_sha256}' <<< "$manifest_identity" > "$manifest"

upload_prefix() {
  local prefix=$1
  "${oci[@]}" os object put --bucket-name "$bucket" --file "$archive" \
    --name "$prefix/$basename" --force
  "${oci[@]}" os object put --bucket-name "$bucket" --file "$checksum" \
    --name "$prefix/$(basename "$checksum")" --force
  "${oci[@]}" os object put --bucket-name "$bucket" --file "$manifest" \
    --name "$prefix/$(basename "$manifest")" --force
}

upload_prefix daily
if [[ "$(date --utc +%u)" == "$weekly_day" ]]; then
  upload_prefix weekly
fi

temporary_status="$(mktemp "$status_dir/.backup-status.XXXXXX")"
jq -n \
  --arg completed_at "$(date --utc +%Y-%m-%dT%H:%M:%SZ)" \
  --arg object_name "daily/$basename" \
  --arg sha256 "$archive_sha" \
  --arg universe_hash "$universe_hash" \
  --arg manifest_sha256 "$manifest_sha" \
  '{success:true, completed_at:$completed_at, object_name:$object_name,
    sha256:$sha256, universe_hash:$universe_hash,
    manifest_sha256:$manifest_sha256}' > "$temporary_status"
mv -f "$temporary_status" "$status_file"

find "$backup_dir" -maxdepth 1 -type f -name 'qtrad-capture-*' \
  -mtime "+$local_retention_days" -delete

trap - EXIT
