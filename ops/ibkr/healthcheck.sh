#!/usr/bin/env bash
set -euo pipefail

system_url="${QTRAD_IBKR_SYSTEM_URL:-http://127.0.0.1:8000/api/v1/system}"
state_dir="${QTRAD_IBKR_HEALTH_STATE_DIR:-/var/lib/qtrad/ibkr}"
restart_history_path="${QTRAD_IBKR_RESTART_HISTORY_PATH:-${state_dir}/gateway-restarts}"
max_gateway_restarts="${QTRAD_IBKR_MAX_GATEWAY_RESTARTS_PER_HOUR:-3}"
min_root_free_percent="${QTRAD_IBKR_MIN_ROOT_FREE_PERCENT:-15}"
[[ "$max_gateway_restarts" =~ ^[1-9][0-9]*$ ]] || {
    logger -p alert -t qtrad-ibkr-health "invalid Gateway restart budget"
    exit 2
}
[[ "$min_root_free_percent" =~ ^[1-9][0-9]?$ ]] || {
    logger -p alert -t qtrad-ibkr-health "invalid root filesystem free-space threshold"
    exit 2
}
lock_path="${state_dir}/ibkr-healthcheck.lock"
state_path="${state_dir}/ibkr-healthcheck.state"
mkdir -p "$state_dir"
exec 9>"$lock_path"
flock -n 9 || exit 0
root_used_percent="$(df --output=pcent / | tail -n 1 | tr -dc '0-9')"
[[ "$root_used_percent" =~ ^[0-9]+$ && "$root_used_percent" -le 100 ]] || {
    logger -p alert -t qtrad-ibkr-health "root filesystem usage is unavailable"
    exit 2
}
if ((100 - root_used_percent < min_root_free_percent)); then
    logger -p alert -t qtrad-ibkr-health "root filesystem has less than ${min_root_free_percent}% free"
    exit 2
fi
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
    if [[ "$previous" == "OPERATOR" && $((now - previous_at)) -lt 900 ]]; then
        exit 2
    fi
fi

if [[ "$action" == "RESTART_GATEWAY" ]]; then
    history_dir="$(dirname -- "$restart_history_path")"
    install -d -m 0750 "$history_dir"
    history_tmp="${restart_history_path}.tmp.$$"
    if [[ -f "$restart_history_path" ]]; then
        awk -v cutoff="$((now - 3600))" '$1 >= cutoff { print $1 }' \
            "$restart_history_path" >"$history_tmp"
    else
        : >"$history_tmp"
    fi
    mv -f "$history_tmp" "$restart_history_path"
    restart_count="$(wc -l <"$restart_history_path")"
    if ((restart_count >= max_gateway_restarts)); then
        logger -p alert -t qtrad-ibkr-health "IBKR Gateway restart budget exhausted; operator intervention required"
        printf 'OPERATOR %s\n' "$now" >"$state_path"
        exit 2
    fi
fi

printf '%s %s\n' "$action" "$now" >"$state_path"

case "$action" in
    RESTART_ADAPTER)
        systemctl restart qtrad-ibkr-ingest.service
        ;;
    RESTART_GATEWAY)
        printf '%s\n' "$now" >>"$restart_history_path"
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
