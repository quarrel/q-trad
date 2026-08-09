#!/usr/bin/env bash
set -euo pipefail

set -a
# shellcheck disable=SC1091
. /etc/qtrad/ibkr-postgres.env
set +a
container="${QTRAD_IBKR_POSTGRES_CONTAINER:-qtrad-ibkr-native-postgres}"
database="${QTRAD_IBKR_POSTGRES_DATABASE:-qtrad_ibkr}"
user="${QTRAD_IBKR_POSTGRES_USER:-qtrad_ibkr}"

for _ in $(seq 1 60); do
    if docker exec "$container" pg_isready --username="$user" --dbname="$database" >/dev/null 2>&1; then
        docker exec "$container" psql --tuples-only --no-align --username="$user" --dbname="$database" \
            --command 'SELECT current_database(), current_user' | grep -Fx 'qtrad_ibkr|qtrad_ibkr' >/dev/null
        exit 0
    fi
    sleep 1
done
echo "native PostgreSQL did not become ready" >&2
exit 69
