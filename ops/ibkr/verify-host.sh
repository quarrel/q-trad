#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "$0")" && pwd)"

mountpoint -q /srv/qtrad/postgres || {
    echo "/srv/qtrad/postgres is not mounted" >&2
    exit 1
}
command -v docker >/dev/null
command -v jq >/dev/null
command -v curl >/dev/null
command -v firewall-cmd >/dev/null
[[ "$(firewall-cmd --state)" == "running" ]] || {
    echo "firewalld is not running" >&2
    exit 1
}

for unit in pmcd.service pmlogger.service pmie.service; do
    if systemctl is-active --quiet "$unit" || systemctl is-enabled --quiet "$unit"; then
        echo "PCP unit must be inactive and disabled: $unit" >&2
        exit 1
    fi
done
if ss -H -ltn '( sport = :4002 )' | awk '{print $4}' | grep -Ev '(^127[.]0[.]0[.]1:4002$|^[[]::1[]]:4002$)' >/dev/null; then
    echo "IB Gateway API is not restricted to localhost" >&2
    exit 1
fi
if firewall-cmd --query-port=4002/tcp; then
    echo "Gateway API is exposed by firewalld" >&2
    exit 1
fi

image="${QTRAD_IBKR_IMAGE:?set QTRAD_IBKR_IMAGE to an immutable digest}"
[[ "$image" =~ @sha256:[0-9a-f]{64}$ ]] || {
    echo "QTRAD_IBKR_IMAGE must be an immutable digest" >&2
    exit 1
}
gateway_version="${QTRAD_IBKR_GATEWAY_VERSION:-10.49}"
gateway_archive_sha="${QTRAD_IBKR_GATEWAY_ARCHIVE_SHA256:?set QTRAD_IBKR_GATEWAY_ARCHIVE_SHA256 to the private Gateway archive SHA-256}"
gateway_manifest="${QTRAD_IBKR_GATEWAY_MANIFEST:?set QTRAD_IBKR_GATEWAY_MANIFEST to the private Gateway identity manifest}"
[[ -f "$gateway_manifest" ]] || {
    echo "Gateway identity manifest is missing: $gateway_manifest" >&2
    exit 1
}
[[ "$gateway_archive_sha" =~ ^[0-9a-f]{64}$ ]] || {
    echo "QTRAD_IBKR_GATEWAY_ARCHIVE_SHA256 must be a lowercase SHA-256 digest" >&2
    exit 1
}
jq -e --arg version "$gateway_version" --arg archive_sha "$gateway_archive_sha" '
    .gateway_version == $version
    and .gateway_archive_sha256 == $archive_sha
    and (.launcher | strings | startswith("/"))
' "$gateway_manifest" >/dev/null || {
    echo "Gateway identity manifest does not match the configured installation" >&2
    exit 1
}

config_check="${QTRAD_IBKR_GATEWAY_CONFIG_CHECK:-$script_dir/gateway-config-check.sh}"
[[ "$config_check" == /* && -x "$config_check" ]] || {
    echo "Gateway configuration checker is missing or not executable: $config_check" >&2
    exit 1
}
"$config_check"

docker image inspect "$image" >/dev/null
labels="$(docker image inspect "$image" --format '{{json .Config.Labels}}')"
jq -e \
    --arg api "${QTRAD_IBKR_API_VERSION:-10.49}" \
    --arg gateway "$gateway_version" \
    '."org.qtrad.ibkr.api.version" == $api
     and ."org.qtrad.ibkr.gateway.expected.version" == $gateway
     and (.["org.qtrad.ibkr.api.archive.sha256"] | test("^[0-9a-f]{64}$"))
     and (.["org.qtrad.source.digest"] | test("^[0-9a-f]{64}$"))
     and (.["org.opencontainers.image.revision"] | test("^[0-9a-f]{40,64}$"))
     and (."org.opencontainers.image.created" | length > 0)' <<<"$labels" >/dev/null

evidence_root="${QTRAD_IBKR_EVIDENCE_ROOT:-/srv/qtrad/ibkr/evidence}"
checkpoint_root="${QTRAD_IBKR_CHECKPOINT_ROOT:?set QTRAD_IBKR_CHECKPOINT_ROOT to a persistent host path}"
api_fingerprint="${QTRAD_IBKR_API_PACKAGE_FINGERPRINT:?set QTRAD_IBKR_API_PACKAGE_FINGERPRINT}"
[[ "$checkpoint_root" == /* ]] || {
    echo "QTRAD_IBKR_CHECKPOINT_ROOT must be an absolute persistent path" >&2
    exit 1
}
[[ "$api_fingerprint" =~ ^[0-9a-f]{64}$ ]] || {
    echo "QTRAD_IBKR_API_PACKAGE_FINGERPRINT must be a 64-character lowercase SHA-256 digest" >&2
    exit 1
}
jq -e --arg fingerprint "$api_fingerprint" --arg gateway_sha "$gateway_archive_sha" '
    .["org.qtrad.ibkr.api.source-manifest.sha256"] == $fingerprint
    and .["org.qtrad.ibkr.gateway.expected.archive.sha256"] == $gateway_sha
' <<<"$labels" >/dev/null || {
    echo "image fingerprints do not match the configured API/expected Gateway identities" >&2
    exit 1
}
[[ -d "$evidence_root" && -w "$evidence_root" ]] || {
    echo "IBKR evidence path is not writable: $evidence_root" >&2
    exit 1
}
[[ -d "$checkpoint_root" && -w "$checkpoint_root" ]] || {
    echo "IBKR checkpoint path is not writable: $checkpoint_root" >&2
    exit 1
}
test -w /srv/qtrad/postgres
echo "IBKR host invariants verified for $image"
