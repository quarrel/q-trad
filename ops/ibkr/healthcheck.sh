#!/usr/bin/env bash
set -euo pipefail

system_url="${QTRAD_IBKR_SYSTEM_URL:-http://127.0.0.1:8000/api/v1/system}"
state_dir="${QTRAD_IBKR_HEALTH_STATE_DIR:-/run/qtrad}"
lock_path="${state_dir}/ibkr-healthcheck.lock"
state_path="${state_dir}/ibkr-healthcheck.state"
mkdir -p "$state_dir"
exec 9>"$lock_path"
flock -n 9 || exit 0

payload="$(curl --fail --silent --show-error --max-time 10 "$system_url")" || {
    logger -t qtrad-ibkr-health "system endpoint unavailable; no component restart requested"
    exit 1
}
action="$(jq -r '
    [.adapter_health[]? | select((.adapter_name // "") | startswith("ibkr"))
      | (.recovery_action // "NONE")]
    | if any(.[]; . == "OPERATOR") then "OPERATOR"
      elif any(.[]; . == "RESTART_GATEWAY") then "RESTART_GATEWAY"
      elif any(.[]; . == "RESTART_ADAPTER") then "RESTART_ADAPTER"
      else "NONE" end
' <<<"$payload")"

if [[ "$action" == "NONE" ]]; then
    rm -f "$state_path"
    exit 0
fi

now="$(date +%s)"
if [[ -f "$state_path" ]]; then
    read -r previous previous_at <"$state_path"
    if [[ "$previous" == "$action" && $((now - previous_at)) -lt 900 ]]; then
        logger -t qtrad-ibkr-health "recovery action $action remains pending; cooldown suppresses a loop"
        exit 1
    fi
fi
printf '%s %s\n' "$action" "$now" >"$state_path"

case "$action" in
    RESTART_ADAPTER)
        systemctl restart qtrad-ibkr-ingest.service
        ;;
    RESTART_GATEWAY)
        systemctl restart qtrad-ibgateway.service
        systemctl restart qtrad-ibkr-ingest.service
        ;;
    OPERATOR)
        logger -p alert -t qtrad-ibkr-health "IBKR requires operator intervention"
        exit 2
        ;;
    *)
        logger -p warning -t qtrad-ibkr-health "unknown recovery action: $action"
        exit 2
        ;;
esac
