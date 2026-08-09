#!/usr/bin/env bash
set -euo pipefail
umask 077

backup_dir="${QTRAD_IBKR_BACKUP_DIR:-/srv/qtrad/postgres/backups}"
container="${QTRAD_IBKR_POSTGRES_CONTAINER:-qtrad-ibkr-postgres}"
database="${QTRAD_IBKR_POSTGRES_DATABASE:-qtrad_ibkr}"
restore_database="${QTRAD_IBKR_RESTORE_DATABASE:-qtrad_ibkr_restore_$(date -u +%Y%m%d%H%M%S)_$$}"
user="${QTRAD_IBKR_POSTGRES_USER:-qtrad_ibkr}"

[[ "$database" == qtrad_ibkr && "$restore_database" != "$database" ]] || {
    echo "restore target must be separate from qtrad_ibkr" >&2
    exit 64
}
[[ "$restore_database" =~ ^qtrad_ibkr_restore_[a-z0-9_]+$ ]] || {
    echo "restore target name is not a dedicated disposable IBKR database" >&2
    exit 64
}

cleanup() {
    docker exec "$container" psql --username="$user" --dbname=postgres         --command "DROP DATABASE IF EXISTS \"$restore_database\"" > /dev/null 2>&1 || true
}
trap cleanup EXIT

latest="$(find "$backup_dir" -maxdepth 1 -type f -name 'qtrad-ibkr-*.dump' -printf '%T@ %p\n' | sort -nr | head -n 1 | cut -d ' ' -f 2-)"
[[ -n "$latest" && -f "$latest" ]] || {
    echo "no IBKR PostgreSQL backup is available" >&2
    exit 69
}
sha256sum --check "$latest.sha256"
docker exec "$container" psql --username="$user" --dbname=postgres     --command "CREATE DATABASE \"$restore_database\"" > /dev/null
docker exec "$container" pg_restore --exit-on-error --no-owner     --username="$user" --dbname="$restore_database" < "$latest"
docker exec "$container" psql --username="$user" --dbname="$restore_database"     --command 'SELECT 1' > /dev/null
