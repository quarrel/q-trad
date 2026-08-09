#!/usr/bin/env bash
set -euo pipefail

set -a
# shellcheck disable=SC1091
. /etc/qtrad/ibkr-postgres.env
set +a
container="${QTRAD_IBKR_POSTGRES_CONTAINER:-qtrad-ibkr-native-postgres}"
if docker container inspect "$container" >/dev/null 2>&1 \
    && [[ "$(docker container inspect "$container" --format '{{.State.Running}}')" == true ]]; then
    docker stop --time 90 "$container" >/dev/null
fi
