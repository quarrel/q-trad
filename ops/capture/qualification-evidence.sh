#!/usr/bin/env bash
set -euo pipefail

umask 077

if (($# != 1)); then
  printf 'usage: %s OUTPUT_JSON\n' "${0##*/}" >&2
  exit 64
fi

readonly output=$1
readonly root="${QTRAD_CAPTURE_ROOT:?QTRAD_CAPTURE_ROOT is required}"
readonly compose_file="$root/compose.capture.yaml"
readonly capture_env="${QTRAD_CAPTURE_ENV:-/etc/qtrad/capture.env}"
readonly status_dir="${QTRAD_STATUS_DIR:?QTRAD_STATUS_DIR is required}"
readonly data_mount="${QTRAD_DATA_MOUNT:-/srv/qtrad/postgres}"
readonly endpoint="${QTRAD_READY_BASE_URL:-http://127.0.0.1:8000}"
readonly candidate_start="${QTRAD_QUALIFICATION_START:?QTRAD_QUALIFICATION_START is required}"
readonly not_before_end="${QTRAD_QUALIFICATION_NOT_BEFORE_END:?QTRAD_QUALIFICATION_NOT_BEFORE_END is required}"
readonly expected_image="${QTRAD_QUALIFICATION_IMAGE:?QTRAD_QUALIFICATION_IMAGE is required}"
readonly expected_descriptor_commit="${QTRAD_QUALIFICATION_DESCRIPTOR_COMMIT:?QTRAD_QUALIFICATION_DESCRIPTOR_COMMIT is required}"
readonly expected_descriptor_sha="${QTRAD_QUALIFICATION_DESCRIPTOR_SHA256:?QTRAD_QUALIFICATION_DESCRIPTOR_SHA256 is required}"
readonly expected_source_id="${QTRAD_QUALIFICATION_SOURCE_ID:?QTRAD_QUALIFICATION_SOURCE_ID is required}"
readonly expected_configuration_hash="${QTRAD_QUALIFICATION_CONFIGURATION_HASH:?QTRAD_QUALIFICATION_CONFIGURATION_HASH is required}"
readonly expected_migration="${QTRAD_QUALIFICATION_MIGRATION:?QTRAD_QUALIFICATION_MIGRATION is required}"
readonly maximum_backup_age="${QTRAD_MAX_BACKUP_AGE_SECONDS:-129600}"
readonly maximum_restore_age="${QTRAD_MAX_RESTORE_AGE_SECONDS:-691200}"
readonly minimum_disk_free_percent="${QTRAD_MIN_DISK_FREE_PERCENT:-15}"
readonly now="${QTRAD_QUALIFICATION_NOW:-$(date --utc +%Y-%m-%dT%H:%M:%SZ)}"
readonly compose=(
  docker compose --env-file "$capture_env" --project-directory "$root" -f "$compose_file"
)

[[ "$endpoint" =~ ^http://127\.0\.0\.1:([1-9][0-9]{0,4})$ ]]
((10#${BASH_REMATCH[1]} <= 65535))
[[ "$expected_image" =~ ^[^[:space:]]+@sha256:[0-9a-f]{64}$ ]]
[[ "$expected_descriptor_commit" =~ ^[0-9a-f]{40}$ ]]
[[ "$expected_descriptor_sha" =~ ^[0-9a-f]{64}$ ]]
[[ "$expected_source_id" =~ ^[a-z0-9][a-z0-9._-]{0,63}$ ]]
[[ "$expected_configuration_hash" =~ ^[0-9a-f]{64}$ ]]
[[ "$expected_migration" =~ ^[0-9a-f]{4,32}$ ]]
[[ "$maximum_backup_age" =~ ^[0-9]+$ ]]
[[ "$maximum_restore_age" =~ ^[0-9]+$ ]]
[[ "$minimum_disk_free_percent" =~ ^[0-9]+$ ]]
((minimum_disk_free_percent >= 1 && minimum_disk_free_percent <= 100))
[[ -f "$compose_file" ]]
[[ -f "$capture_env" ]]
[[ -d "$status_dir" ]]
[[ -d "$data_mount" ]]
[[ -d "$(dirname "$output")" ]]
[[ ! -L "$(dirname "$output")" ]]
[[ ! -L "$output" && ! -e "$output" ]]

utc_epoch() {
  local value=$1
  local epoch
  local normalised
  epoch="$(date --date="$value" +%s)"
  normalised="$(date --utc --date="@$epoch" +%Y-%m-%dT%H:%M:%SZ)"
  [[ "$normalised" == "$value" ]]
  printf '%s\n' "$epoch"
}

capture_value() {
  local key=$1
  local -a values=()
  mapfile -t values < <(sed -n "s/^${key}=//p" "$capture_env")
  ((${#values[@]} == 1))
  [[ -n "${values[0]}" ]]
  printf '%s\n' "${values[0]}"
}

unit_state() {
  systemctl is-active "$1" 2> /dev/null || true
}

unit_result() {
  systemctl show --property=Result --value "$1" 2> /dev/null || true
}

age_seconds() {
  local file=$1
  local completed_at
  local completed_epoch
  [[ -s "$file" ]]
  jq -e '.success == true and (.completed_at | type == "string")' "$file" > /dev/null
  completed_at="$(jq -er '.completed_at' "$file")"
  completed_epoch="$(utc_epoch "$completed_at")"
  printf '%s\n' "$((now_epoch - completed_epoch))"
}

start_epoch="$(utc_epoch "$candidate_start")"
end_epoch="$(utc_epoch "$not_before_end")"
now_epoch="$(utc_epoch "$now")"
readonly start_epoch end_epoch now_epoch
((end_epoch - start_epoch >= 259200))

work_dir="$(mktemp -d)"
temporary_output=''
cleanup() {
  rm -rf "$work_dir"
  if [[ -n "$temporary_output" ]]; then
    rm -f "$temporary_output"
  fi
}
trap cleanup EXIT

fetch_json() {
  local name=$1
  local path=$2
  local allowed_codes=$3
  local destination="$work_dir/$name.json"
  local http_code
  http_code="$(
    curl --silent --show-error --max-time 15 --proto '=http' \
      --output "$destination" --write-out '%{http_code}' "$endpoint$path"
  )"
  [[ " $allowed_codes " == *" $http_code "* ]]
  printf '%s\n' "$http_code" > "$work_dir/$name.http"
  (( $(wc -c < "$destination") <= 16777216 ))
  jq -e 'type == "object" or type == "array"' "$destination" > /dev/null
}

fetch_json readiness /health/ready "200 503"
fetch_json system /api/v1/system 200
fetch_json runs /api/v1/runs 200
fetch_json gaps /api/v1/gaps 200

actual_image="$(capture_value QTRAD_IMAGE)"
actual_source_id="$(capture_value QTRAD_CAPTURE_SOURCE_ID)"
actual_descriptor_sha="$(sha256sum "$compose_file" | cut -d ' ' -f 1)"
evidence_tool_sha="$(sha256sum "${BASH_SOURCE[0]}" | cut -d ' ' -f 1)"
migration_version="$(
  "${compose[@]}" exec -T db psql --username=qtrad_capture --dbname=qtrad_capture \
    --tuples-only --no-align --command 'SELECT version_num FROM alembic_version;'
)"
readonly actual_image actual_source_id actual_descriptor_sha evidence_tool_sha migration_version
[[ "$migration_version" =~ ^[0-9a-f]{4,32}$ ]]

"${compose[@]}" ps --format json > "$work_dir/compose.json"
jq -e 'type == "array"' "$work_dir/compose.json" > /dev/null

readonly backup_status="$status_dir/backup-status.json"
readonly restore_status="$status_dir/restore-status.json"
backup_age="$(age_seconds "$backup_status")"
restore_age="$(age_seconds "$restore_status")"
readonly backup_age restore_age
cp "$backup_status" "$work_dir/backup.json"
cp "$restore_status" "$work_dir/restore.json"

used_percent="$(df --output=pcent "$data_mount" | tail -1 | tr -dc '0-9')"
readonly used_percent
readonly disk_free_percent="$((100 - used_percent))"
findmnt --json --target "$data_mount" --output TARGET,SOURCE,FSTYPE,OPTIONS \
  > "$work_dir/data-mount.json"
jq -e 'type == "object" and (.filesystems | type == "array")' \
  "$work_dir/data-mount.json" > /dev/null

jq -n \
  --arg docker "$(unit_state docker.service)" \
  --arg tailscale "$(unit_state tailscaled.service)" \
  --arg capture "$(unit_state qtrad-capture.service)" \
  --arg capture_result "$(unit_result qtrad-capture.service)" \
  --arg healthwatch_timer "$(unit_state qtrad-healthwatch.timer)" \
  --arg backup_timer "$(unit_state qtrad-backup.timer)" \
  --arg restore_timer "$(unit_state qtrad-restore-verify.timer)" \
  --arg healthwatch_result "$(unit_result qtrad-healthwatch.service)" \
  --arg backup_result "$(unit_result qtrad-backup.service)" \
  --arg restore_result "$(unit_result qtrad-restore-verify.service)" \
  '{docker_service:$docker, tailscale_service:$tailscale, capture_service:$capture,
    capture_last_result:$capture_result,
    healthwatch_timer:$healthwatch_timer,
    backup_timer:$backup_timer, restore_timer:$restore_timer,
    healthwatch_last_result:$healthwatch_result, backup_last_result:$backup_result,
    restore_last_result:$restore_result}' > "$work_dir/units.json"

run_checks="$(
  jq -c \
    --arg start "$candidate_start" \
    --arg configuration_hash "$expected_configuration_hash" '
      def epoch: sub("\\+00:00$"; "Z") | sub("\\.[0-9]+Z$"; "Z") | fromdateiso8601;
      def candidate: select((.started_at | epoch) >= ($start | fromdateiso8601));
      {
        response_is_bounded: (type == "array" and length < 100),
        current_matching_runs: ([.[] | select(.kind == "INGESTION" and .status == "RUNNING"
          and .configuration_hash == $configuration_hash)] | length),
        candidate_failed_runs: ([.[] | candidate | select(.kind == "INGESTION" and .status == "FAILED")] | length),
        candidate_unexpected_status_runs: ([.[] | candidate
          | select(.kind == "INGESTION"
            and (.status | IN("RUNNING", "STOPPED", "FAILED") | not))] | length),
        candidate_stopped_runs: ([.[] | candidate | select(.kind == "INGESTION" and .status == "STOPPED")] | length),
        candidate_stops_without_zero_drops: ([.[] | candidate
          | select(.kind == "INGESTION" and .status == "STOPPED")
          | select((.detail.adapter_health // "") | contains("dropped_records=0") | not)] | length),
        pre_candidate_nonterminal_runs: [.[]
          | select(.kind == "INGESTION" and .status == "RUNNING"
            and (.started_at | epoch) < ($start | fromdateiso8601))
          | {run_id, started_at, configuration_hash}]
      }' "$work_dir/runs.json"
)"
readonly run_checks

gap_review="$(
  jq -c --arg start "$candidate_start" '
    def epoch: sub("\\+00:00$"; "Z") | sub("\\.[0-9]+Z$"; "Z") | fromdateiso8601;
    [.[] | select((.detected_at | epoch) >= ($start | fromdateiso8601))
      | {gap_id, instrument_id, interval_start, interval_end, reason, detected_at, repaired_at}]' \
    "$work_dir/gaps.json"
)"
readonly gap_review

automatic_checks="$(
  jq -cS -n \
    --argjson now_at_or_after_end "$([[ "$now_epoch" -ge "$end_epoch" ]] && printf true || printf false)" \
    --argjson elapsed_at_least_72_hours "$([[ $((now_epoch - start_epoch)) -ge 259200 ]] && printf true || printf false)" \
    --argjson image_matches "$([[ "$actual_image" == "$expected_image" ]] && printf true || printf false)" \
    --argjson descriptor_matches "$([[ "$actual_descriptor_sha" == "$expected_descriptor_sha" ]] && printf true || printf false)" \
    --argjson source_matches "$([[ "$actual_source_id" == "$expected_source_id" ]] && printf true || printf false)" \
    --argjson migration_matches "$([[ "$migration_version" == "$expected_migration" ]] && printf true || printf false)" \
    --arg readiness_http_code "$(< "$work_dir/readiness.http")" \
    --argjson readiness_ok "$(jq -e --arg hash "$expected_configuration_hash" --arg now "$now" \
      'def epoch: sub("\\+00:00$"; "Z") | sub("\\.[0-9]+Z$"; "Z") | fromdateiso8601;
       .ready == true and .fresh_quote_count == 7 and .expected_instruments == 7
       and .configuration_hash == $hash and .reasons == []
       and (.global_position - .checkpoint_position >= 0)
       and (.global_position - .checkpoint_position <= 100)
       and ((.checkpoint_updated_at | epoch)
         <= ($now | fromdateiso8601))
         and (($now | fromdateiso8601)
           - (.checkpoint_updated_at | epoch) <= 300)' \
      "$work_dir/readiness.json" > /dev/null \
      && printf true || printf false)" \
    --argjson adapter_ok "$(jq -e '
      [.adapter_health[] | select(.adapter_name == "ig-market-data" and .environment == "IG_DEMO"
        and .status == "HEALTHY" and (.detail | contains("subscriptions=7/7"))
        and (.detail | contains("updates=7/7")) and (.detail | contains("dropped_records=0")))]
      | length == 1' "$work_dir/system.json" > /dev/null && printf true || printf false)" \
    --argjson units_ok "$(jq -e '
      .docker_service == "active" and .tailscale_service == "active"
      and .capture_service == "active" and .capture_last_result == "success"
      and .healthwatch_timer == "active"
      and .backup_timer == "active" and .restore_timer == "active"
      and .healthwatch_last_result == "success" and .backup_last_result == "success"
      and .restore_last_result == "success"' "$work_dir/units.json" > /dev/null \
      && printf true || printf false)" \
    --argjson data_mount_ok "$(jq -e --arg target "$data_mount" '
      .filesystems | length == 1 and .[0].target == $target and .[0].fstype == "xfs"
      and (.[0].source | type == "string" and length > 0)
      and (.[0].options | split(",") | index("rw") != null)' \
      "$work_dir/data-mount.json" > /dev/null && printf true || printf false)" \
    --argjson compose_ok "$(jq -e '
      length == 3 and ([.[].Service] | sort == ["api", "db", "ingest"])
      and all(.[]; .State == "running")
      and ([.[] | select(.Service == "api" or .Service == "db") | .Health]
        | all(. == "healthy"))' "$work_dir/compose.json" > /dev/null && printf true || printf false)" \
    --argjson backup_ok "$([[ "$backup_age" -ge 0 && "$backup_age" -le "$maximum_backup_age" ]] && printf true || printf false)" \
    --argjson restore_ok "$([[ "$restore_age" -ge 0 && "$restore_age" -le "$maximum_restore_age" ]] && printf true || printf false)" \
    --argjson disk_ok "$([[ "$disk_free_percent" -ge "$minimum_disk_free_percent" ]] && printf true || printf false)" \
    --argjson run_checks "$run_checks" \
    '{now_at_or_after_end:$now_at_or_after_end, elapsed_at_least_72_hours:$elapsed_at_least_72_hours,
      image_matches:$image_matches, descriptor_matches:$descriptor_matches,
      source_matches:$source_matches, migration_matches:$migration_matches,
      readiness_http_200:($readiness_http_code == "200"),
      readiness_ok:$readiness_ok, adapter_ok:$adapter_ok, units_ok:$units_ok,
      compose_ok:$compose_ok, data_mount_ok:$data_mount_ok,
      backup_ok:$backup_ok, restore_ok:$restore_ok, disk_ok:$disk_ok,
      run_history_bounded:$run_checks.response_is_bounded,
      exactly_one_current_matching_run:($run_checks.current_matching_runs == 1),
      no_candidate_failed_runs:($run_checks.candidate_failed_runs == 0),
      no_candidate_unexpected_statuses:($run_checks.candidate_unexpected_status_runs == 0),
      lifecycle_restarts_observed:($run_checks.candidate_stopped_runs >= 2),
      stopped_runs_report_zero_drops:($run_checks.candidate_stops_without_zero_drops == 0),
      pre_candidate_runs_reconciled:($run_checks.pre_candidate_nonterminal_runs | length == 0)}'
)"
readonly automatic_checks
automatic_passed="$(jq -r '[.[]] | all' <<< "$automatic_checks")"
readonly automatic_passed

evidence_identity="$(
  jq -cS -n \
    --arg schema qtrad-capture-qualification-v1 \
    --arg generated_at "$now" \
    --arg candidate_start "$candidate_start" \
    --arg not_before_end "$not_before_end" \
    --arg expected_image "$expected_image" \
    --arg actual_image "$actual_image" \
    --arg descriptor_commit "$expected_descriptor_commit" \
    --arg descriptor_sha256 "$actual_descriptor_sha" \
    --arg evidence_tool_sha256 "$evidence_tool_sha" \
    --arg capture_source_id "$actual_source_id" \
    --arg configuration_hash "$expected_configuration_hash" \
    --arg migration_version "$migration_version" \
    --argjson elapsed_seconds "$((now_epoch - start_epoch))" \
    --argjson backup_age_seconds "$backup_age" \
    --argjson restore_age_seconds "$restore_age" \
    --argjson database_disk_free_percent "$disk_free_percent" \
    --argjson automatic_checks "$automatic_checks" \
    --argjson automatic_checks_passed "$automatic_passed" \
    --argjson run_checks "$run_checks" \
    --argjson candidate_gaps "$gap_review" \
    --slurpfile readiness "$work_dir/readiness.json" \
    --slurpfile system "$work_dir/system.json" \
    --slurpfile units "$work_dir/units.json" \
    --slurpfile compose_services "$work_dir/compose.json" \
    --slurpfile data_mount "$work_dir/data-mount.json" \
    --slurpfile backup "$work_dir/backup.json" \
    --slurpfile restore "$work_dir/restore.json" \
    '{schema:$schema, generated_at:$generated_at, candidate_start:$candidate_start,
      not_before_end:$not_before_end, elapsed_seconds:$elapsed_seconds,
      release:{expected_image:$expected_image, actual_image:$actual_image,
        descriptor_commit:$descriptor_commit, descriptor_sha256:$descriptor_sha256,
        evidence_tool_sha256:$evidence_tool_sha256,
        capture_source_id:$capture_source_id, configuration_hash:$configuration_hash,
        migration_version:$migration_version},
      readiness:$readiness[0], system:$system[0], units:$units[0],
      compose_services:$compose_services[0], data_mount:$data_mount[0],
      backup:$backup[0], restore:$restore[0],
      backup_age_seconds:$backup_age_seconds, restore_age_seconds:$restore_age_seconds,
      database_disk_free_percent:$database_disk_free_percent,
      run_checks:$run_checks, candidate_gaps:$candidate_gaps,
      automatic_checks:$automatic_checks, automatic_checks_passed:$automatic_checks_passed,
      operator_reviews:{candidate_gap_classification:(if ($candidate_gaps | length) == 0
          then "NOT_REQUIRED" else "REQUIRED" end),
        container_log_history:"REQUIRED", monitoring_history:"REQUIRED",
        active_market_representativeness:"REQUIRED"},
      qualification_decision:"PENDING_OPERATOR_REVIEW"}'
)"
readonly evidence_identity
evidence_sha="$(printf '%s' "$evidence_identity" | sha256sum | cut -d ' ' -f 1)"
readonly evidence_sha
temporary_output="$(mktemp "$(dirname "$output")/.qualification.XXXXXX")"
jq --arg evidence_sha256 "$evidence_sha" '. + {evidence_sha256:$evidence_sha256}' \
  <<< "$evidence_identity" > "$temporary_output"
ln "$temporary_output" "$output"
rm "$temporary_output"
temporary_output=''
printf '%s\n' "$output"

[[ "$automatic_passed" == true ]]
