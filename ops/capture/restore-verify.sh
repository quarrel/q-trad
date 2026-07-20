#!/usr/bin/env bash
set -euo pipefail

umask 077

readonly bucket="${QTRAD_BACKUP_BUCKET:?QTRAD_BACKUP_BUCKET is required}"
readonly status_dir="${QTRAD_STATUS_DIR:?QTRAD_STATUS_DIR is required}"
readonly oci_auth="${QTRAD_OCI_AUTH:-instance_principal}"
readonly status_file="$status_dir/restore-status.json"
readonly oci=(oci --auth "$oci_auth")
readonly minimum_free_bytes="${QTRAD_RESTORE_MIN_FREE_BYTES:-34359738368}"

if [[ ! "$minimum_free_bytes" =~ ^[1-9][0-9]*$ ]]; then
  printf 'QTRAD_RESTORE_MIN_FREE_BYTES must be a positive integer\n' >&2
  exit 64
fi

work_dir="$(mktemp -d)"
restore_identity="$(date --utc +%s)-$$"
container="qtrad-restore-verify-$restore_identity"
volume="qtrad-restore-verify-$restore_identity"
volume_created=0

mkdir -p "$status_dir"

record_result() {
  local exit_code=$?
  docker rm --force "$container" > /dev/null 2>&1 || true
  if ((volume_created == 1)); then
    docker volume rm --force "$volume" > /dev/null 2>&1 || true
  fi
  rm -rf "$work_dir"
  if ((exit_code != 0)); then
    local temporary_status
    temporary_status="$(mktemp "$status_dir/.restore-status.XXXXXX")"
    jq -n \
      --arg completed_at "$(date --utc +%Y-%m-%dT%H:%M:%SZ)" \
      --argjson exit_code "$exit_code" \
      '{success:false, completed_at:$completed_at, exit_code:$exit_code}' \
      > "$temporary_status"
    mv -f "$temporary_status" "$status_file"
  fi
  exit "$exit_code"
}
trap record_result EXIT

docker_root="$(docker info --format '{{.DockerRootDir}}')"
if [[ "$docker_root" != /* || ! -d "$docker_root" ]]; then
  printf 'Docker reported an unusable storage root\n' >&2
  exit 69
fi
available_bytes="$(df --output=avail -B1 "$docker_root" | tail -n 1 | tr -d '[:space:]')"
if [[ ! "$available_bytes" =~ ^[0-9]+$ ]]; then
  printf 'could not determine free bytes for Docker storage root\n' >&2
  exit 69
fi
if ((available_bytes < minimum_free_bytes)); then
  printf 'restore verification requires at least %s free bytes; Docker storage has %s\n' \
    "$minimum_free_bytes" "$available_bytes" >&2
  exit 75
fi

latest_object="$(
  "${oci[@]}" os object list --bucket-name "$bucket" --prefix daily/qtrad-capture- \
    --all --fields name,timeCreated,size,etag \
    | jq -er '[.data[] | select(.name | endswith(".dump"))]
      | sort_by(."time-created") | last | .name'
)"
archive_name="$(basename "$latest_object")"
checksum_object="$latest_object.sha256"
manifest_object="$latest_object.manifest.json"

"${oci[@]}" os object get --bucket-name "$bucket" --name "$latest_object" \
  --file "$work_dir/$archive_name"
"${oci[@]}" os object get --bucket-name "$bucket" --name "$checksum_object" \
  --file "$work_dir/$archive_name.sha256"
"${oci[@]}" os object get --bucket-name "$bucket" --name "$manifest_object" \
  --file "$work_dir/$archive_name.manifest.json"

(
  cd "$work_dir"
  sha256sum --check "$archive_name.sha256"
)
actual_sha="$(sha256sum "$work_dir/$archive_name" | cut -d ' ' -f 1)"
manifest_file="$work_dir/$archive_name.manifest.json"
manifest_schema="$(jq -er '.schema' "$manifest_file")"
case "$manifest_schema" in
  qtrad-capture-backup-v1)
    jq -e \
      --arg archive "$archive_name" \
      --arg sha256 "$actual_sha" \
      '.schema == "qtrad-capture-backup-v1" and .archive == $archive and .sha256 == $sha256
        and (.universe_hash | test("^[0-9a-fA-F]{64}$"))
        and (.postgres_image | contains("@sha256:"))' \
      "$manifest_file" > /dev/null
    expected_migration_version="${QTRAD_EXPECTED_V1_MIGRATION_VERSION:-0003}"
    ;;
  qtrad-capture-backup-v2)
    jq -e \
      --arg archive "$archive_name" \
      --arg sha256 "$actual_sha" \
      '(keys | sort) == ["archive", "capture_image", "capture_source_id", "created_at",
        "database", "manifest_sha256", "migration_version", "postgres_image", "schema",
        "sha256", "universe_hash", "universe_name"]
        and .archive == $archive and .sha256 == $sha256 and .database == "qtrad_capture"
        and (.created_at | test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"))
        and (.capture_source_id | test("^[a-z0-9][a-z0-9._-]{0,63}$"))
        and (.universe_name | test("^[a-z0-9][a-z0-9._-]{0,63}$"))
        and (.universe_hash | test("^[0-9a-f]{64}$"))
        and (.capture_image | test("@sha256:[0-9a-f]{64}$"))
        and (.postgres_image | test("@sha256:[0-9a-f]{64}$"))
        and (.migration_version | test("^[0-9a-f]{4,32}$"))
        and (.manifest_sha256 | test("^[0-9a-f]{64}$"))' \
      "$manifest_file" > /dev/null
    manifest_identity="$(jq -cS 'del(.manifest_sha256)' "$manifest_file")"
    actual_manifest_sha="$(printf '%s' "$manifest_identity" | sha256sum | cut -d ' ' -f 1)"
    expected_manifest_sha="$(jq -er '.manifest_sha256' "$manifest_file")"
    [[ "$actual_manifest_sha" == "$expected_manifest_sha" ]]
    expected_migration_version="$(jq -er '.migration_version' "$manifest_file")"
    ;;
  *)
    printf 'unsupported capture backup manifest schema: %s\n' "$manifest_schema" >&2
    exit 65
    ;;
esac
postgres_image="$(jq -er '.postgres_image' "$manifest_file")"

docker volume create \
  --label qtrad.role=restore-verification \
  "$volume" > /dev/null
volume_created=1
docker run --detach --name "$container" --network none \
  --mount "type=volume,source=$volume,target=/var/lib/postgresql" \
  --env POSTGRES_HOST_AUTH_METHOD=trust \
  "$postgres_image" > /dev/null
ready=0
for _ in $(seq 1 30); do
  if docker exec -u postgres "$container" pg_isready -U postgres > /dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 2
done
[[ "$ready" == 1 ]]

docker exec -u postgres "$container" createdb qtrad_restore
docker exec -i -u postgres "$container" \
  pg_restore --exit-on-error --no-owner --no-privileges --dbname=qtrad_restore \
  < "$work_dir/$archive_name"
migration_version="$(
  docker exec -u postgres "$container" \
    psql --dbname=qtrad_restore --tuples-only --no-align \
    --command 'SELECT version_num FROM alembic_version;'
)"
[[ "$migration_version" == "$expected_migration_version" ]]
event_count="$(
  docker exec -u postgres "$container" \
    psql --dbname=qtrad_restore --tuples-only --no-align \
    --command 'SELECT count(*) FROM canonical.events;'
)"
[[ "$event_count" =~ ^[0-9]+$ ]]

temporary_status="$(mktemp "$status_dir/.restore-status.XXXXXX")"
jq -n \
  --arg completed_at "$(date --utc +%Y-%m-%dT%H:%M:%SZ)" \
  --arg object_name "$latest_object" \
  --arg sha256 "$actual_sha" \
  --arg manifest_schema "$manifest_schema" \
  --arg migration_version "$migration_version" \
  --argjson canonical_event_count "$event_count" \
  '{success:true, completed_at:$completed_at, object_name:$object_name, sha256:$sha256,
    manifest_schema:$manifest_schema, migration_version:$migration_version,
    canonical_event_count:$canonical_event_count}' \
  > "$temporary_status"
mv -f "$temporary_status" "$status_file"

docker rm --force "$container" > /dev/null
docker volume rm "$volume" > /dev/null
volume_created=0
rm -rf "$work_dir"
trap - EXIT
