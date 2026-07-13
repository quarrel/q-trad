#!/usr/bin/env bash
set -euo pipefail

readonly root="${QTRAD_CAPTURE_ROOT:?QTRAD_CAPTURE_ROOT is required}"
readonly compose_file="$root/compose.capture.yaml"
readonly capture_env="${QTRAD_CAPTURE_ENV:-/etc/qtrad/capture.env}"
readonly backup_dir="${QTRAD_BACKUP_DIR:?QTRAD_BACKUP_DIR is required}"
timestamp="$(date --utc +%Y%m%dT%H%M%SZ)"
readonly timestamp
readonly archive="$backup_dir/qtrad-capture-$timestamp.dump"

mkdir -p "$backup_dir"
docker compose --env-file "$capture_env" --project-directory "$root" -f "$compose_file" exec -T db \
  pg_dump --username=qtrad_capture --dbname=qtrad_capture --format=custom > "$archive"
sha256sum "$archive" > "$archive.sha256"
docker compose --env-file "$capture_env" --project-directory "$root" -f "$compose_file" exec -T db \
  pg_restore --list < "$archive" > /dev/null
oci os object put --bucket-name "${QTRAD_BACKUP_BUCKET:?QTRAD_BACKUP_BUCKET is required}" \
  --file "$archive" --name "daily/$(basename "$archive")" --force
oci os object put --bucket-name "$QTRAD_BACKUP_BUCKET" --file "$archive.sha256" \
  --name "daily/$(basename "$archive.sha256")" --force
