#!/usr/bin/env bash
set -euo pipefail

umask 077

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly root
readonly postgres_image="postgres:18@sha256:22c89fe0d0f507606260237fd55e51f6137f58b2d5bcf6152242b96d9fe8f9a4"
readonly identity="${GITHUB_RUN_ID:-local}-$$"
readonly seed_container="qtrad-restore-seed-$identity"
work_dir="$(mktemp -d)"
readonly work_dir
readonly object_dir="$work_dir/objects"
readonly status_dir="$work_dir/status"
readonly fake_bin="$work_dir/bin"

cleanup() {
  docker rm --force "$seed_container" > /dev/null 2>&1 || true
  rm -rf "$work_dir"
}
trap cleanup EXIT

mkdir -p "$object_dir/daily" "$status_dir" "$fake_bin"

docker run --detach --name "$seed_container" --network none \
  --env POSTGRES_HOST_AUTH_METHOD=trust "$postgres_image" > /dev/null
ready=0
for _ in $(seq 1 30); do
  if docker exec -u postgres "$seed_container" pg_isready -U postgres > /dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
[[ "$ready" == 1 ]]

docker exec -u postgres "$seed_container" createdb qtrad_capture
docker exec -i -u postgres "$seed_container" psql --dbname=qtrad_capture > /dev/null <<'SQL'
CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY);
INSERT INTO alembic_version VALUES ('0010');
CREATE SCHEMA canonical;
CREATE TABLE canonical.events (event_id uuid PRIMARY KEY);
INSERT INTO canonical.events VALUES
  ('00000000-0000-0000-0000-000000000001'),
  ('00000000-0000-0000-0000-000000000002');
SQL

readonly archive="qtrad-capture-20260720T000000Z.dump"
docker exec -u postgres "$seed_container" pg_dump --format=custom qtrad_capture \
  > "$object_dir/daily/$archive"
archive_sha="$(sha256sum "$object_dir/daily/$archive" | cut -d ' ' -f 1)"
printf '%s  %s\n' "$archive_sha" "$archive" > "$object_dir/daily/$archive.sha256"

manifest_identity="$(
  jq -cS -n \
    --arg archive "$archive" \
    --arg sha256 "$archive_sha" \
    --arg postgres_image "$postgres_image" \
    '{schema:"qtrad-capture-backup-v2",created_at:"2026-07-20T00:00:00Z",
      archive:$archive,sha256:$sha256,database:"qtrad_capture",
      capture_source_id:"ci-restore",universe_name:"capture-v1",
      universe_hash:("a" * 64),capture_image:("example.invalid/qtrad@sha256:" + ("b" * 64)),
      postgres_image:$postgres_image,migration_version:"0010"}'
)"
manifest_sha="$(printf '%s' "$manifest_identity" | sha256sum | cut -d ' ' -f 1)"
jq --arg manifest_sha256 "$manifest_sha" '. + {manifest_sha256:$manifest_sha256}' \
  <<< "$manifest_identity" > "$object_dir/daily/$archive.manifest.json"

ln -s "$root/ops/dev/fake-oci-object-store.sh" "$fake_bin/oci"

containers_before="$(docker ps --all --quiet --filter label=qtrad.role=restore-verification | sort)"
volumes_before="$(docker volume ls --quiet --filter label=qtrad.role=restore-verification | sort)"
readonly containers_before volumes_before

PATH="$fake_bin:$PATH" \
QTRAD_FAKE_OBJECT_DIR="$object_dir" \
QTRAD_FAKE_OBJECT_NAME="daily/$archive" \
QTRAD_BACKUP_BUCKET=ci-restore \
QTRAD_STATUS_DIR="$status_dir" \
QTRAD_RESTORE_MIN_FREE_BYTES=1 \
  "$root/ops/capture/restore-verify.sh" || restore_result=$?
restore_result="${restore_result:-0}"
printf 'restore verification result: %s\n' "$restore_result"
cat "$status_dir/restore-status.json"
if ((restore_result != 0)); then
  exit "$restore_result"
fi

jq -e '
  .success == true
  and .manifest_schema == "qtrad-capture-backup-v2"
  and .migration_version == "0010"
  and .canonical_event_count == 2
' "$status_dir/restore-status.json" > /dev/null
containers_after="$(docker ps --all --quiet --filter label=qtrad.role=restore-verification | sort)"
volumes_after="$(docker volume ls --quiet --filter label=qtrad.role=restore-verification | sort)"
[[ "$containers_after" == "$containers_before" ]]
[[ "$volumes_after" == "$volumes_before" ]]
