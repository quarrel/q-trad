#!/usr/bin/env bash
set -euo pipefail
umask 077

backup_dir="${QTRAD_IBKR_BACKUP_DIR:-/srv/qtrad/postgres/backups}"
container="${QTRAD_IBKR_POSTGRES_CONTAINER:-qtrad-ibkr-native-postgres}"
database="${QTRAD_IBKR_POSTGRES_DATABASE:-qtrad_ibkr}"
restore_database="${QTRAD_IBKR_RESTORE_DATABASE:-qtrad_ibkr_restore_verify_$(date -u +%Y%m%d%H%M%S)_$$}"
evidence_path="${QTRAD_IBKR_RESTORE_EVIDENCE_PATH:?set a create-only restore evidence path}"
requested_archive="${QTRAD_IBKR_RESTORE_ARCHIVE:-}"
user="${QTRAD_IBKR_POSTGRES_USER:-qtrad_ibkr}"

[[ "$database" == qtrad_ibkr && "$restore_database" != "$database" ]] || {
    echo "restore target must be separate from qtrad_ibkr" >&2
    exit 64
}
[[ "$restore_database" =~ ^qtrad_ibkr_restore_verify_[a-z0-9_]+$ ]] || {
    echo "restore target name is not a dedicated disposable IBKR database" >&2
    exit 64
}
[[ "$evidence_path" == /var/lib/qtrad/ibkr/restore-evidence/*.json && ! -e "$evidence_path" ]] || {
    echo "restore evidence path must be a new file in /var/lib/qtrad/ibkr/restore-evidence" >&2
    exit 64
}
[[ "$#" -gt 0 ]] || {
    echo "restore verification requires a bounded qualification command" >&2
    exit 64
}

temporary_evidence=""
cleanup() {
    [[ -z "$temporary_evidence" ]] || rm -f "$temporary_evidence"
    docker exec "$container" psql --username="$user" --dbname=postgres \
        --command "DROP DATABASE IF EXISTS \"$restore_database\"" > /dev/null 2>&1 || true
}
trap cleanup EXIT

if [[ -n "$requested_archive" ]]; then
    latest="$(realpath -e "$requested_archive")" || {
        echo "requested IBKR PostgreSQL backup is unavailable" >&2
        exit 69
    }
    resolved_backup_dir="$(realpath -e "$backup_dir")"
    [[ "$(dirname -- "$latest")" == "$resolved_backup_dir" \
        && "$(basename -- "$latest")" =~ ^qtrad-ibkr-[0-9]{8}T[0-9]{6}Z[.]dump$ ]] || {
        echo "requested backup is not an exact IBKR archive" >&2
        exit 64
    }
else
    latest="$(find "$backup_dir" -maxdepth 1 -type f -name 'qtrad-ibkr-*.dump' -printf '%T@ %p\n' | sort -nr | head -n 1 | cut -d ' ' -f 2-)"
fi
[[ -n "$latest" && -f "$latest" && -f "$latest.sha256" ]] || {
    echo "no complete IBKR PostgreSQL backup is available" >&2
    exit 69
}
started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
sha256sum --check "$latest.sha256"
archive_sha256="$(sha256sum "$latest" | cut -d ' ' -f 1)"

docker exec "$container" psql --username="$user" --dbname=postgres \
    --command "CREATE DATABASE \"$restore_database\"" > /dev/null
docker exec "$container" pg_restore --exit-on-error --no-owner \
    --username="$user" --dbname="$restore_database" < "$latest"
schema_head="$(docker exec "$container" psql --tuples-only --no-align \
    --username="$user" --dbname="$restore_database" \
    --command 'SELECT version_num FROM alembic_version')"
[[ "$schema_head" =~ ^[0-9]{4}$ ]] || {
    echo "restored database schema head is unavailable" >&2
    exit 69
}
restore_marker="qtrad-ibkr-postgres-restore-v1:$restore_database:$archive_sha256"
docker exec "$container" psql --username="$user" --dbname=postgres \
    --command "COMMENT ON DATABASE \"$restore_database\" IS '$restore_marker'" > /dev/null
docker exec "$container" psql --username="$user" --dbname="$restore_database" \
    --command 'SELECT 1' > /dev/null
completed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

install -d -m 0750 "$(dirname -- "$evidence_path")"
unsigned="$(jq -cS -n \
    --arg contract "qtrad-ibkr-postgres-restore-v1" \
    --arg archive_path "$latest" \
    --arg archive_sha256 "$archive_sha256" \
    --arg source_database_name "$database" \
    --arg restored_database_name "$restore_database" \
    --arg schema_head "$schema_head" \
    --arg started_at "$started_at" \
    --arg completed_at "$completed_at" \
    --arg restore_marker "$restore_marker" \
    '{contract:$contract,archive_path:$archive_path,archive_sha256:$archive_sha256,source_database_name:$source_database_name,restored_database_name:$restored_database_name,schema_head:$schema_head,started_at:$started_at,completed_at:$completed_at,restore_marker:$restore_marker}')"
artifact_sha256="$(printf '%s' "$unsigned" | sha256sum | cut -d ' ' -f 1)"
temporary_evidence="$(mktemp "$(dirname -- "$evidence_path")/.restore-evidence.XXXXXX")"
printf '%s' "$unsigned" | jq -cS --arg artifact_sha256 "$artifact_sha256" \
    '. + {artifact_sha256:$artifact_sha256}' > "$temporary_evidence"
ln "$temporary_evidence" "$evidence_path"
rm -f "$temporary_evidence"
temporary_evidence=""

export QTRAD_IBKR_QUALIFICATION_RESTORE_DATABASE_URL="postgresql+asyncpg://$user@127.0.0.1:5432/$restore_database"
export QTRAD_IBKR_QUALIFICATION_RESTORE_EVIDENCE_PATH="$evidence_path"
"$@"
