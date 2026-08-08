#!/usr/bin/env bash
set -euo pipefail

usage() {
    echo "usage: deploy.sh --check|--apply" >&2
    exit 64
}

mode="${1:-}"
[[ "$mode" == "--check" || "$mode" == "--apply" ]] || usage
[[ "${2:-}" == "" ]] || usage

script_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
env_file="${QTRAD_IBKR_ENV_FILE:-/etc/qtrad/ibkr-ingest.env}"
if [[ -f "$env_file" ]]; then
    set -a
    # shellcheck disable=SC1090
    . "$env_file"
    set +a
fi

image="${QTRAD_IBKR_IMAGE:?set QTRAD_IBKR_IMAGE}"
descriptor="${QTRAD_IBKR_RELEASE_DESCRIPTOR:?set QTRAD_IBKR_RELEASE_DESCRIPTOR}"
repository_root="${QTRAD_IBKR_REPOSITORY_ROOT:-$script_dir/../..}"
preflight_bin="${QTRAD_B3_PREFLIGHT_BIN:-qtrad}"
checkpoint_root="${QTRAD_IBKR_CHECKPOINT_ROOT:?set QTRAD_IBKR_CHECKPOINT_ROOT}"
api_fingerprint="${QTRAD_IBKR_API_PACKAGE_FINGERPRINT:?set QTRAD_IBKR_API_PACKAGE_FINGERPRINT}"
gateway_archive_sha="${QTRAD_IBKR_GATEWAY_ARCHIVE_SHA256:?set QTRAD_IBKR_GATEWAY_ARCHIVE_SHA256}"
gateway_manifest="${QTRAD_IBKR_GATEWAY_MANIFEST:?set QTRAD_IBKR_GATEWAY_MANIFEST}"
ibc_version="${QTRAD_IBKR_IBC_VERSION:-3.24.1}"
api_version="${QTRAD_IBKR_API_VERSION:-10.49}"
gateway_version="${QTRAD_IBKR_GATEWAY_VERSION:-10.49}"
gateway_host="${QTRAD_IBKR_GATEWAY_HOST:-127.0.0.1}"
gateway_port="${QTRAD_IBKR_GATEWAY_PORT:-4002}"
api_host="${QTRAD_IBKR_API_HOST:-127.0.0.1}"
api_port="${QTRAD_IBKR_API_PORT:-8000}"
client_id="${QTRAD_IBKR_CLIENT_ID:-71}"
database_name="${QTRAD_IBKR_DATABASE_NAME:-qtrad_ibkr}"
configuration_hash="${QTRAD_IBKR_CAPTURE_CONFIGURATION_HASH:?set QTRAD_IBKR_CAPTURE_CONFIGURATION_HASH}"
capture_configuration_path="${QTRAD_IBKR_CAPTURE_CONFIGURATION_PATH:?set QTRAD_IBKR_CAPTURE_CONFIGURATION_PATH}"
database_url="${QTRAD_DATABASE_URL:?set QTRAD_DATABASE_URL}"
base_image="${QTRAD_IMAGE:-$image}"

fail() { echo "IBKR B3 preflight: $*" >&2; exit 64; }

[[ "$image" =~ @sha256:[0-9a-f]{64}$ ]] || fail "image must be immutable"
[[ "$base_image" == "$image" ]] || fail "application and IBKR image identities differ"
[[ "$api_fingerprint" =~ ^[0-9a-f]{64}$ ]] || fail "API fingerprint is invalid"
[[ "$gateway_archive_sha" =~ ^[0-9a-f]{64}$ ]] || fail "Gateway archive hash is invalid"
[[ "$configuration_hash" =~ ^[0-9a-f]{64}$ ]] || fail "configuration hash is invalid"
[[ "$ibc_version" == 3.24.1 ]] || fail "IBC version is not the reviewed version"
[[ "$capture_configuration_path" == /* ]] || fail "capture configuration path must be absolute"
[[ "$database_url" == postgresql+asyncpg://qtrad_ibkr@127.0.0.1:5432/qtrad_ibkr ]] || fail "database URL must be dedicated and credential-free"
[[ "$checkpoint_root" == /* ]] || fail "checkpoint root must be absolute"
[[ "$gateway_host" == 127.0.0.1 || "$gateway_host" == ::1 ]] || fail "Gateway must be loopback"
[[ "$api_host" == 127.0.0.1 || "$api_host" == ::1 ]] || fail "API must be loopback"
[[ "$gateway_port" == 4002 && "$api_port" == 8000 ]] || fail "unexpected private ports"
[[ "$client_id" =~ ^[1-9][0-9]*$ ]] || fail "client ID must be positive"
[[ "$database_name" == qtrad_ibkr ]] || fail "database must be dedicated qtrad_ibkr"
[[ "$api_version" == "$gateway_version" && ( "$api_version" == 10.49 || "$api_version" == 10.45 ) ]] || fail "API/Gateway versions mismatch"
[[ -r "$descriptor" ]] || fail "release descriptor is not readable"
[[ -r "$gateway_manifest" ]] || fail "Gateway identity manifest is not readable"
[[ -d "$repository_root" ]] || fail "repository root is not readable"
command -v "$preflight_bin" >/dev/null || fail "qtrad offline preflight command is unavailable"
command -v jq >/dev/null || fail "jq is required to compare release identities"

preflight_json="$("$preflight_bin" deployment ibkr-preflight \
    --descriptor "$descriptor" --repository-root "$repository_root" \
    --observed-at "${QTRAD_IBKR_PREFLIGHT_OBSERVED_AT:?set reviewed UTC preflight timestamp}")"
printf '%s\n' "$preflight_json" | jq -e \
    --arg image "$image" \
    --arg configuration_hash "$configuration_hash" \
    --arg configuration_path "$capture_configuration_path" \
    --arg api_fingerprint "$api_fingerprint" \
    --arg gateway_archive_sha "$gateway_archive_sha" \
    --arg api_version "$api_version" \
    --arg ibc_version "$ibc_version" \
    --arg database_name "$database_name" \
    --arg database_url_environment "QTRAD_DATABASE_URL" \
    --arg gateway_host "$gateway_host" \
    --arg api_host "$api_host" \
    --arg gateway_port "$gateway_port" \
    --arg api_port "$api_port" \
    --arg client_id "$client_id" \
    '.valid == true
     and .image == $image
     and .configuration_hash == $configuration_hash
     and .configuration_path == $configuration_path
     and .api_package_fingerprint == $api_fingerprint
     and .gateway_archive_sha256 == $gateway_archive_sha
     and .api_version == $api_version
     and .gateway_version == $api_version
     and .ibc_version == $ibc_version
     and .database_name == $database_name
     and .database_url_environment == $database_url_environment
     and .gateway_host == $gateway_host
     and .api_host == $api_host
     and .gateway_port == ($gateway_port|tonumber)
     and .api_port == ($api_port|tonumber)
     and .client_id == ($client_id|tonumber)
     and .source == "ibkr-paper-v1"
     and .universe == "capture-ibkr-v1"' >/dev/null || fail "release identity does not match the reviewed descriptor"

if [[ "$mode" == "--check" ]]; then
    echo "IBKR B3 preflight passed; no host mutation performed"
    exit 0
fi

docker pull "$image"
"$script_dir/verify-host.sh"
docker run --rm --network host --user 10001:10001 \
    --read-only --cap-drop=ALL --security-opt=no-new-privileges \
    --env-file "$env_file" --entrypoint uv "$image" \
    run --frozen --no-dev --no-sync python -m qtrad db upgrade
install -d -o 10001 -g 10001 -m 0750 "$checkpoint_root"

install -D -m 0750 "$script_dir/qtrad-ibkr-ingest-wrapper.example" /usr/local/sbin/qtrad-ibkr-ingest
install -D -m 0750 "$script_dir/qtrad-ibkr-api-wrapper.example" /usr/local/sbin/qtrad-ibkr-api
install -D -m 0750 "$script_dir/healthcheck.sh" /usr/local/sbin/qtrad-ibkr-healthcheck
install -D -m 0750 "$script_dir/postgres-backup.sh" /usr/local/sbin/qtrad-ibkr-postgres-backup
install -D -m 0750 "$script_dir/postgres-restore-verify.sh" /usr/local/sbin/qtrad-ibkr-postgres-restore-verify
install -D -m 0644 "$script_dir/qtrad-ibkr-ingest.service.example" /etc/systemd/system/qtrad-ibkr-ingest.service
install -D -m 0644 "$script_dir/qtrad-ibkr-api.service.example" /etc/systemd/system/qtrad-ibkr-api.service
install -D -m 0644 "$script_dir/qtrad-ibkr-health.service.example" /etc/systemd/system/qtrad-ibkr-health.service
install -D -m 0644 "$script_dir/qtrad-ibkr-health.timer.example" /etc/systemd/system/qtrad-ibkr-health.timer
install -D -m 0644 "$script_dir/qtrad-ibkr-backup.service.example" /etc/systemd/system/qtrad-ibkr-backup.service
install -D -m 0644 "$script_dir/qtrad-ibkr-backup.timer.example" /etc/systemd/system/qtrad-ibkr-backup.timer
systemctl daemon-reload
systemctl enable --now qtrad-ibkr-api.service qtrad-ibkr-ingest.service
systemctl enable --now qtrad-ibkr-health.timer qtrad-ibkr-backup.timer
