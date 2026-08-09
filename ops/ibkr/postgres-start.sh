#!/usr/bin/env bash
set -euo pipefail

env_file="/etc/qtrad/ibkr-postgres.env"
[[ -r "$env_file" ]] || { echo "missing $env_file" >&2; exit 69; }
set -a
# shellcheck disable=SC1090
. "$env_file"
set +a

image="${QTRAD_IBKR_POSTGRES_IMAGE:?set QTRAD_IBKR_POSTGRES_IMAGE}"
container="${QTRAD_IBKR_POSTGRES_CONTAINER:-qtrad-ibkr-native-postgres}"
data_root="${QTRAD_IBKR_POSTGRES_DATA_ROOT:-/srv/qtrad/postgres/ibkr-native-data}"
database="${QTRAD_IBKR_POSTGRES_DATABASE:-qtrad_ibkr}"
user="${QTRAD_IBKR_POSTGRES_USER:-qtrad_ibkr}"
port="${QTRAD_IBKR_POSTGRES_PORT:-5432}"

[[ "$image" =~ ^postgres@sha256:[0-9a-f]{64}$ ]] || { echo "PostgreSQL image must be immutable" >&2; exit 64; }
[[ "$container" == qtrad-ibkr-native-postgres ]] || { echo "unexpected PostgreSQL container" >&2; exit 64; }
[[ "$data_root" == /srv/qtrad/postgres/ibkr-native-data ]] || { echo "unexpected PostgreSQL data root" >&2; exit 64; }
[[ "$database" == qtrad_ibkr && "$user" == qtrad_ibkr && "$port" == 5432 ]] || { echo "unexpected PostgreSQL identity" >&2; exit 64; }
mountpoint -q /srv/qtrad/postgres || { echo "/srv/qtrad/postgres is not mounted" >&2; exit 69; }

install -d -o 999 -g 999 -m 0700 "$data_root"
if docker container inspect "$container" >/dev/null 2>&1; then
    actual_image="$(docker container inspect "$container" --format '{{.Config.Image}}')"
    actual_data="$(docker container inspect "$container" --format '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Source}}{{end}}{{end}}')"
    actual_binding="$(docker container inspect "$container" --format '{{(index (index .HostConfig.PortBindings "5432/tcp") 0).HostIp}}:{{(index (index .HostConfig.PortBindings "5432/tcp") 0).HostPort}}')"
    [[ "$actual_image" == "$image" && "$actual_data" == "$data_root" && "$actual_binding" == 127.0.0.1:5432 ]] || {
        echo "existing native PostgreSQL container identity mismatch" >&2
        exit 69
    }
    [[ "$(docker container inspect "$container" --format '{{.State.Running}}')" == true ]] || docker start "$container" >/dev/null
else
    docker run --detach --name "$container" --restart=no \
        --publish 127.0.0.1:5432:5432 \
        --mount "type=bind,source=$data_root,target=/var/lib/postgresql/data" \
        --env "POSTGRES_DB=$database" --env "POSTGRES_USER=$user" \
        --env POSTGRES_HOST_AUTH_METHOD=trust \
        "$image" >/dev/null
fi

container_exit_code="$(docker wait "$container")"
[[ "$container_exit_code" =~ ^[0-9]+$ ]] || exit 1
exit "$container_exit_code"
