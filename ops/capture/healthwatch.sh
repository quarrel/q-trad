#!/usr/bin/env bash
set -euo pipefail

umask 077

readonly endpoint="${QTRAD_READY_URL:-http://127.0.0.1:8000/health/ready}"
readonly system_endpoint="${QTRAD_SYSTEM_URL:-http://127.0.0.1:8000/api/v1/system}"
readonly metric_namespace="${QTRAD_OCI_METRIC_NAMESPACE:?QTRAD_OCI_METRIC_NAMESPACE is required}"
readonly compartment_id="${QTRAD_OCI_COMPARTMENT_ID:?QTRAD_OCI_COMPARTMENT_ID is required}"
readonly telemetry_endpoint="${QTRAD_OCI_TELEMETRY_ENDPOINT:?QTRAD_OCI_TELEMETRY_ENDPOINT is required}"
readonly status_dir="${QTRAD_STATUS_DIR:?QTRAD_STATUS_DIR is required}"
readonly data_mount="${QTRAD_DATA_MOUNT:-/srv/qtrad/postgres}"
readonly oci_auth="${QTRAD_OCI_AUTH:-instance_principal}"
readonly maximum_backup_age="${QTRAD_MAX_BACKUP_AGE_SECONDS:-129600}"
readonly maximum_restore_age="${QTRAD_MAX_RESTORE_AGE_SECONDS:-691200}"
readonly minimum_disk_free_percent="${QTRAD_MIN_DISK_FREE_PERCENT:-15}"
readonly maximum_clock_offset_seconds="${QTRAD_MAX_CLOCK_OFFSET_SECONDS:-0.1}"
readonly backup_status="$status_dir/backup-status.json"
readonly restore_status="$status_dir/restore-status.json"

[[ "$maximum_backup_age" =~ ^[0-9]+$ ]]
[[ "$maximum_restore_age" =~ ^[0-9]+$ ]]
[[ "$minimum_disk_free_percent" =~ ^[0-9]+$ ]]
((minimum_disk_free_percent >= 1 && minimum_disk_free_percent <= 100))
[[ "$maximum_clock_offset_seconds" =~ ^[0-9]+([.][0-9]+)?$ ]]
awk -v maximum="$maximum_clock_offset_seconds" 'BEGIN {exit !(maximum > 0)}'

response="$(mktemp)"
system_response="$(mktemp)"
metrics="$(mktemp)"
tracking="$(mktemp)"
sources="$(mktemp)"
trap 'rm -f "$response" "$system_response" "$metrics" "$tracking" "$sources"' EXIT

http_code="$(
  curl --silent --show-error --output "$response" --write-out '%{http_code}' "$endpoint" || true
)"
ready=0
fresh_quote_count=0
projection_lag=-1
if jq -e 'type == "object"' "$response" > /dev/null 2>&1; then
  fresh_quote_count="$(jq -er '.fresh_quote_count | numbers' "$response")"
  global_position="$(jq -er '.global_position | numbers' "$response")"
  checkpoint_position="$(jq -er '.checkpoint_position | numbers' "$response")"
  projection_lag=$((global_position - checkpoint_position))
  if [[ "$http_code" == 200 ]] && jq -e '.ready == true' "$response" > /dev/null; then
    ready=1
  fi
fi

system_http_code="$(
  curl --silent --show-error --output "$system_response" --write-out '%{http_code}' \
    "$system_endpoint" || true
)"
heartbeat_healthy=0
heartbeat_age=-1
heartbeat_events=-1
queue_depth=-1
queue_high_water=-1
dropped_records=-1
lightstreamer_lost_updates=-1
reconnects=-1
subscription_errors=-1
server_errors=-1
if [[ "$system_http_code" == 200 ]] && jq -e 'type == "object"' "$system_response" > /dev/null; then
  heartbeat_healthy="$(jq -r 'if .heartbeat.status == "HEALTHY" then 1 else 0 end' "$system_response")"
  heartbeat_age="$(jq -er '.heartbeat.observation_age_seconds | numbers' "$system_response")"
  heartbeat_events="$(jq -er '.heartbeat.events | numbers' "$system_response")"
  adapter_fields="$(jq -cer '
    [.adapter_health[] | select(.adapter_name == "ig-market-data" and .environment == "IG_DEMO")]
    | if length == 1 then .[0].detail else error("expected one IG demo adapter") end
    | split(";")
    | map(gsub("^ +| +$"; "") | capture("^(?<key>[^=]+)=(?<value>.*)$"))
    | from_entries
  ' "$system_response")"
  queue_depth="$(jq -er '.queue | capture("^(?<depth>[0-9]+)/[1-9][0-9]*$").depth | tonumber' \
    <<< "$adapter_fields")"
  queue_high_water="$(jq -er '.queue_high_water | tonumber' <<< "$adapter_fields")"
  dropped_records="$(jq -er '.dropped_records | tonumber' <<< "$adapter_fields")"
  lightstreamer_lost_updates="$(jq -er '.lightstreamer_lost_updates | tonumber' \
    <<< "$adapter_fields")"
  reconnects="$(jq -er '.reconnects | tonumber' <<< "$adapter_fields")"
  subscription_errors="$(jq -er '.subscription_errors | tonumber' <<< "$adapter_fields")"
  server_errors="$(jq -er '.server_errors | tonumber' <<< "$adapter_fields")"
fi

clock_source_online=0
clock_synchronised=0
clock_leap_normal=0
clock_offset_seconds=-1
if chronyc tracking > "$tracking" 2> /dev/null && chronyc -n sources > "$sources" 2> /dev/null; then
  if awk '$1 == "^*" && $2 == "169.254.169.254" {found=1} END {exit !found}' "$sources"; then
    clock_source_online=1
  fi
  leap_status="$(awk -F: '$1 ~ /^Leap status/ {sub(/^ +/, "", $2); print $2}' "$tracking")"
  if [[ "$leap_status" == Normal ]]; then
    clock_leap_normal=1
  fi
  clock_offset_seconds="$(awk -F: '$1 ~ /^System time/ {gsub(/^[[:space:]]+/, "", $2); print $2}' \
    "$tracking" | awk '{value=$1; if (value < 0) value=-value; print value}')"
  if [[ "$clock_source_online" == 1 && "$clock_leap_normal" == 1 ]] \
    && [[ "$(timedatectl show --property=NTPSynchronized --value 2> /dev/null)" == yes ]]; then
    clock_synchronised=1
  fi
fi

age_seconds() {
  local file=$1
  if [[ ! -s "$file" ]] || ! jq -e '.success == true and (.completed_at | type == "string")' \
    "$file" > /dev/null 2>&1; then
    printf '%s\n' -1
    return
  fi
  local completed_epoch
  completed_epoch="$(date --date="$(jq -er '.completed_at' "$file")" +%s)"
  printf '%s\n' "$(( $(date --utc +%s) - completed_epoch ))"
}

backup_age="$(age_seconds "$backup_status")"
restore_age="$(age_seconds "$restore_status")"
restore_verified=0
if ((restore_age >= 0 && restore_age <= maximum_restore_age)); then
  restore_verified=1
fi
disk_free_percent="$(df --output=pcent "$data_mount" | tail -1 | tr -dc '0-9')"
disk_free_percent=$((100 - disk_free_percent))
timestamp="$(date --utc +%Y-%m-%dT%H:%M:%SZ)"

jq -n \
  --arg namespace "$metric_namespace" \
  --arg compartment_id "$compartment_id" \
  --arg timestamp "$timestamp" \
  --argjson ready "$ready" \
  --argjson fresh_quote_count "$fresh_quote_count" \
  --argjson projection_lag "$projection_lag" \
  --argjson heartbeat_healthy "$heartbeat_healthy" \
  --argjson heartbeat_age "$heartbeat_age" \
  --argjson heartbeat_events "$heartbeat_events" \
  --argjson queue_depth "$queue_depth" \
  --argjson queue_high_water "$queue_high_water" \
  --argjson dropped_records "$dropped_records" \
  --argjson lightstreamer_lost_updates "$lightstreamer_lost_updates" \
  --argjson reconnects "$reconnects" \
  --argjson subscription_errors "$subscription_errors" \
  --argjson server_errors "$server_errors" \
  --argjson clock_source_online "$clock_source_online" \
  --argjson clock_synchronised "$clock_synchronised" \
  --argjson clock_leap_normal "$clock_leap_normal" \
  --argjson clock_offset_seconds "$clock_offset_seconds" \
  --argjson backup_age "$backup_age" \
  --argjson restore_verified "$restore_verified" \
  --argjson restore_age "$restore_age" \
  --argjson disk_free_percent "$disk_free_percent" \
  '[
    {name:"collector_ready", value:$ready},
    {name:"fresh_quote_count", value:$fresh_quote_count},
    {name:"projection_lag_positions", value:$projection_lag},
    {name:"heartbeat_healthy", value:$heartbeat_healthy},
    {name:"heartbeat_observation_age_seconds", value:$heartbeat_age},
    {name:"heartbeat_events", value:$heartbeat_events},
    {name:"ingest_queue_depth", value:$queue_depth},
    {name:"ingest_queue_high_water", value:$queue_high_water},
    {name:"dropped_records", value:$dropped_records},
    {name:"lightstreamer_lost_updates", value:$lightstreamer_lost_updates},
    {name:"stream_reconnects", value:$reconnects},
    {name:"subscription_errors", value:$subscription_errors},
    {name:"server_errors", value:$server_errors},
    {name:"clock_source_online", value:$clock_source_online},
    {name:"clock_synchronised", value:$clock_synchronised},
    {name:"clock_leap_normal", value:$clock_leap_normal},
    {name:"clock_offset_seconds", value:$clock_offset_seconds},
    {name:"backup_age_seconds", value:$backup_age},
    {name:"restore_verified", value:$restore_verified},
    {name:"restore_age_seconds", value:$restore_age},
    {name:"database_disk_free_percent", value:$disk_free_percent}
  ] | map({namespace:$namespace, compartmentId:$compartment_id, name:.name,
    dimensions:{collector:"q-trad-capture"},
    datapoints:[{timestamp:$timestamp, value:.value}]})' > "$metrics"

oci --auth "$oci_auth" --endpoint "$telemetry_endpoint" \
  monitoring metric-data post --metric-data "file://$metrics" > /dev/null

healthy=1
((ready == 1)) || healthy=0
((heartbeat_healthy == 1)) || healthy=0
((queue_depth >= 0 && dropped_records == 0 && lightstreamer_lost_updates == 0)) || healthy=0
((subscription_errors == 0 && server_errors == 0)) || healthy=0
((projection_lag >= 0 && projection_lag <= 100)) || healthy=0
((backup_age >= 0 && backup_age <= maximum_backup_age)) || healthy=0
((restore_verified == 1)) || healthy=0
((disk_free_percent >= minimum_disk_free_percent)) || healthy=0
((clock_source_online == 1 && clock_synchronised == 1 && clock_leap_normal == 1)) || healthy=0
awk -v offset="$clock_offset_seconds" -v maximum="$maximum_clock_offset_seconds" \
  'BEGIN {exit !(offset >= 0 && offset <= maximum)}' || healthy=0
[[ "$healthy" == 1 ]]
