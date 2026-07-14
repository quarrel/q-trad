#!/usr/bin/env bash
set -euo pipefail

umask 077

readonly bucket="${QTRAD_BACKUP_BUCKET:?QTRAD_BACKUP_BUCKET is required}"
readonly status_dir="${QTRAD_STATUS_DIR:?QTRAD_STATUS_DIR is required}"
readonly oci_auth="${QTRAD_OCI_AUTH:-instance_principal}"
readonly status_file="$status_dir/restore-status.json"
readonly oci=(oci --auth "$oci_auth")
work_dir="$(mktemp -d)"
container="qtrad-restore-verify-$(date --utc +%s)"

mkdir -p "$status_dir"

record_result() {
  local exit_code=$?
  docker rm --force "$container" > /dev/null 2>&1 || true
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
jq -e \
  --arg archive "$archive_name" \
  --arg sha256 "$actual_sha" \
  '.schema == "qtrad-capture-backup-v1" and .archive == $archive and .sha256 == $sha256
    and (.universe_hash | test("^[0-9a-fA-F]{64}$"))
    and (.postgres_image | contains("@sha256:"))' \
  "$manifest_file" > /dev/null
postgres_image="$(jq -er '.postgres_image' "$manifest_file")"

docker run --detach --name "$container" --network none \
  --tmpfs /var/lib/postgresql:rw --env POSTGRES_HOST_AUTH_METHOD=trust \
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
[[ "$migration_version" == 0003 ]]
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
  --arg migration_version "$migration_version" \
  --argjson canonical_event_count "$event_count" \
  '{success:true, completed_at:$completed_at, object_name:$object_name, sha256:$sha256,
    migration_version:$migration_version, canonical_event_count:$canonical_event_count}' \
  > "$temporary_status"
mv -f "$temporary_status" "$status_file"

docker rm --force "$container" > /dev/null
rm -rf "$work_dir"
trap - EXIT
