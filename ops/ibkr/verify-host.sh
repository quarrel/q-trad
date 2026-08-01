#!/usr/bin/env bash
set -euo pipefail

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
    echo "firewalld exposes Gateway API port 4002" >&2
    exit 1
fi
image="${QTRAD_IBKR_IMAGE:?set QTRAD_IBKR_IMAGE to an immutable digest}"
[[ "$image" =~ @sha256:[0-9a-f]{64}$ ]] || {
    echo "QTRAD_IBKR_IMAGE must be an immutable digest" >&2
    exit 1
}
docker image inspect "$image" >/dev/null
labels="$(docker image inspect "$image" --format '{{json .Config.Labels}}')"
jq -e     --arg api "${QTRAD_IBKR_API_VERSION:-10.49}"     --arg gateway "${QTRAD_IBKR_GATEWAY_VERSION:-10.49}"     '."org.qtrad.ibkr.api.version" == $api
     and ."org.qtrad.ibkr.gateway.version" == $gateway
     and (.["org.qtrad.ibkr.api.archive.sha256"] | test("^[0-9a-f]{64}$"))
     and (.["org.qtrad.source.digest"] | test("^[0-9a-f]{64}$"))
     and (.["org.opencontainers.image.revision"] | test("^[0-9a-f]{40,64}$"))
     and (.["org.opencontainers.image.created"] | length > 0)' <<<"$labels" >/dev/null

evidence_root="${QTRAD_IBKR_EVIDENCE_ROOT:-/srv/qtrad/ibkr/evidence}"
[[ -d "$evidence_root" && -w "$evidence_root" ]] || {
    echo "IBKR evidence path is not writable: $evidence_root" >&2
    exit 1
}
test -w /srv/qtrad/postgres
echo "IBKR host invariants verified for $image"
