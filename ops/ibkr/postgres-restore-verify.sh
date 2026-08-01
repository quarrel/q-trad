#!/usr/bin/env bash
set -euo pipefail
umask 077

backup_dir="${QTRAD_IBKR_BACKUP_DIR:-/srv/qtrad/postgres/backups}"
container="${QTRAD_IBKR_POSTGRES_CONTAINER:-qtrad-postgres}"
database="${QTRAD_IBKR_POSTGRES_DATABASE:-qtrad}"
user="${QTRAD_IBKR_POSTGRES_USER:-qtrad}"
latest="$(find "$backup_dir" -maxdepth 1 -type f -name 'qtrad-ibkr-*.dump' -printf '%T@ %p\\n' | sort -nr | head -n 1 | cut -d ' ' -f 2-)"
[[ -n "$latest" && -f "$latest" ]] || {
    echo "no IBKR PostgreSQL backup is available" >&2
    exit 69
}
sha256sum --check "$latest.sha256"
docker exec "$container" \
    pg_restore --list < "$latest"
docker exec "$container" \
    psql --username="$user" --dbname="$database" --command 'SELECT 1' > /dev/null
