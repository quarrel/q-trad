#!/usr/bin/env bash
set -euo pipefail
umask 077

backup_dir="${QTRAD_IBKR_BACKUP_DIR:-/srv/qtrad/postgres/backups}"
status_dir="${QTRAD_IBKR_STATUS_DIR:-/var/lib/qtrad/ibkr}"
container="${QTRAD_IBKR_POSTGRES_CONTAINER:-qtrad-ibkr-native-postgres}"
database="${QTRAD_IBKR_POSTGRES_DATABASE:-qtrad_ibkr}"
user="${QTRAD_IBKR_POSTGRES_USER:-qtrad_ibkr}"
retention_days="${QTRAD_IBKR_BACKUP_RETENTION_DAYS:-14}"

[[ "$backup_dir" == /srv/qtrad/postgres/* ]] || {
    echo "backup directory must remain on /srv/qtrad/postgres" >&2
    exit 64
}
[[ "$database" == qtrad_ibkr ]] || {
    echo "backup script is dedicated to qtrad_ibkr" >&2
    exit 64
}
[[ "$retention_days" =~ ^[1-9][0-9]*$ ]] || {
    echo "QTRAD_IBKR_BACKUP_RETENTION_DAYS must be positive" >&2
    exit 64
}
install -d -m 0750 "$backup_dir" "$status_dir"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
name="qtrad-ibkr-${timestamp}.dump"
partial="$backup_dir/.${name}.partial"
archive="$backup_dir/$name"

cleanup() { rm -f "$partial"; }
trap cleanup EXIT

docker exec "$container" \
    pg_dump --format=custom --no-owner --username="$user" --dbname="$database" > "$partial"
docker exec -i "$container" pg_restore --list < "$partial" > /dev/null
mv -f "$partial" "$archive"
sha256sum "$archive" > "$archive.sha256"

temporary_status="$(mktemp "$status_dir/.ibkr-backup.XXXXXX")"
jq -n --arg completed_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --arg archive "$archive" --arg sha256 "$(sha256sum "$archive" | cut -d ' ' -f 1)" \
    '{success:true, completed_at:$completed_at, archive:$archive, sha256:$sha256}' > "$temporary_status"
mv -f "$temporary_status" "$status_dir/ibkr-backup-status.json"

find "$backup_dir" -maxdepth 1 -type f -name 'qtrad-ibkr-*.dump*' \
    -mtime "+$retention_days" -delete
trap - EXIT
