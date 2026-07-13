#!/usr/bin/env bash
set -euo pipefail

umask 077

readonly endpoint="${QTRAD_READY_URL:-http://127.0.0.1:8000/health/ready}"
readonly metric_namespace="${QTRAD_OCI_METRIC_NAMESPACE:?QTRAD_OCI_METRIC_NAMESPACE is required}"
readonly compartment_id="${QTRAD_OCI_COMPARTMENT_ID:?QTRAD_OCI_COMPARTMENT_ID is required}"
readonly status_dir="${QTRAD_STATUS_DIR:?QTRAD_STATUS_DIR is required}"
readonly data_mount="${QTRAD_DATA_MOUNT:-/srv/qtrad/postgres}"
readonly oci_auth="${QTRAD_OCI_AUTH:-instance_principal}"
readonly maximum_backup_age="${QTRAD_MAX_BACKUP_AGE_SECONDS:-129600}"
readonly maximum_restore_age="${QTRAD_MAX_RESTORE_AGE_SECONDS:-691200}"
readonly minimum_disk_free_percent="${QTRAD_MIN_DISK_FREE_PERCENT:-15}"
readonly backup_status="$status_dir/backup-status.json"
readonly restore_status="$status_dir/restore-status.json"

response="$(mktemp)"
metrics="$(mktemp)"
trap 'rm -f "$response" "$metrics"' EXIT

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
  --argjson backup_age "$backup_age" \
  --argjson restore_verified "$restore_verified" \
  --argjson restore_age "$restore_age" \
  --argjson disk_free_percent "$disk_free_percent" \
  '[
    {name:"collector_ready", value:$ready},
    {name:"fresh_quote_count", value:$fresh_quote_count},
    {name:"projection_lag_positions", value:$projection_lag},
    {name:"backup_age_seconds", value:$backup_age},
    {name:"restore_verified", value:$restore_verified},
    {name:"restore_age_seconds", value:$restore_age},
    {name:"database_disk_free_percent", value:$disk_free_percent}
  ] | map({namespace:$namespace, compartmentId:$compartment_id, name:.name,
    dimensions:{collector:"q-trad-capture"},
    datapoints:[{timestamp:$timestamp, value:.value}]})' > "$metrics"

oci --auth "$oci_auth" monitoring metric-data post --metric-data "file://$metrics" > /dev/null

healthy=1
((ready == 1)) || healthy=0
((projection_lag >= 0 && projection_lag <= 100)) || healthy=0
((backup_age >= 0 && backup_age <= maximum_backup_age)) || healthy=0
((restore_verified == 1)) || healthy=0
((disk_free_percent >= minimum_disk_free_percent)) || healthy=0
[[ "$healthy" == 1 ]]
