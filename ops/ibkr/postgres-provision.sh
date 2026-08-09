#!/usr/bin/env bash
set -euo pipefail

mode="${1:-}"
[[ "$mode" == --check || "$mode" == --apply ]] || { echo "usage: postgres-provision.sh --check|--apply" >&2; exit 64; }
script_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
env_file="/etc/qtrad/ibkr-postgres.env"
[[ -r "$env_file" ]] || { echo "missing $env_file" >&2; exit 69; }
[[ "$(stat -c '%u:%a' "$env_file")" =~ ^0:0?6(00|40)$ ]] || {
    echo "$env_file must be root-owned and mode 0600 or 0640" >&2
    exit 69
}
set -a
# shellcheck disable=SC1090
. "$env_file"
set +a

[[ "${QTRAD_IBKR_POSTGRES_IMAGE:-}" =~ ^postgres@sha256:[0-9a-f]{64}$ ]] || { echo "PostgreSQL image must be immutable" >&2; exit 64; }
[[ "${QTRAD_IBKR_POSTGRES_CONTAINER:-}" == qtrad-ibkr-native-postgres ]] || { echo "unexpected PostgreSQL container" >&2; exit 64; }
[[ "${QTRAD_IBKR_POSTGRES_DATA_ROOT:-}" == /srv/qtrad/postgres/ibkr-native-data ]] || { echo "unexpected PostgreSQL data root" >&2; exit 64; }
[[ "${QTRAD_IBKR_POSTGRES_DATABASE:-}" == qtrad_ibkr && "${QTRAD_IBKR_POSTGRES_USER:-}" == qtrad_ibkr && "${QTRAD_IBKR_POSTGRES_PORT:-}" == 5432 ]] || { echo "unexpected PostgreSQL identity" >&2; exit 64; }
mountpoint -q /srv/qtrad/postgres || { echo "/srv/qtrad/postgres is not mounted" >&2; exit 69; }

if [[ "$mode" == --apply ]]; then
    install -D -m 0750 "$script_dir/postgres-start.sh" /usr/local/sbin/qtrad-ibkr-postgres-start
    install -D -m 0750 "$script_dir/postgres-ready.sh" /usr/local/sbin/qtrad-ibkr-postgres-ready
    install -D -m 0750 "$script_dir/postgres-stop.sh" /usr/local/sbin/qtrad-ibkr-postgres-stop
    install -D -m 0644 "$script_dir/qtrad-ibkr-postgres.service.example" /etc/systemd/system/qtrad-ibkr-postgres.service
    systemctl daemon-reload
    systemctl enable --now qtrad-ibkr-postgres.service
fi

for installed_source in \
    /usr/local/sbin/qtrad-ibkr-postgres-start:postgres-start.sh \
    /usr/local/sbin/qtrad-ibkr-postgres-ready:postgres-ready.sh \
    /usr/local/sbin/qtrad-ibkr-postgres-stop:postgres-stop.sh \
    /etc/systemd/system/qtrad-ibkr-postgres.service:qtrad-ibkr-postgres.service.example; do
    installed="${installed_source%%:*}"
    source_name="${installed_source#*:}"
    cmp --silent "$script_dir/$source_name" "$installed" || {
        echo "installed native PostgreSQL asset differs from reviewed checkout: $installed" >&2
        exit 69
    }
done
systemctl is-active --quiet qtrad-ibkr-postgres.service || { echo "native PostgreSQL service is not active" >&2; exit 69; }
"/usr/local/sbin/qtrad-ibkr-postgres-ready"
docker container inspect qtrad-ibkr-native-postgres >/dev/null
echo "IBKR native PostgreSQL identity verified"
