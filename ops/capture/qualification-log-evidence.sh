#!/usr/bin/env bash
set -euo pipefail

umask 077

if (($# != 2)); then
  printf 'usage: %s AUTOMATIC_EVIDENCE OUTPUT_DIRECTORY\n' "${0##*/}" >&2
  exit 64
fi

readonly automatic_evidence=$1
readonly output=$2
readonly root="${QTRAD_CAPTURE_ROOT:?QTRAD_CAPTURE_ROOT is required}"
readonly capture_env="${QTRAD_CAPTURE_ENV:-/etc/qtrad/capture.env}"
readonly compose_file="$root/compose.capture.yaml"
readonly now="${QTRAD_QUALIFICATION_NOW:-$(date --utc +%Y-%m-%dT%H:%M:%SZ)}"
readonly maximum_source_bytes="${QTRAD_QUALIFICATION_LOG_MAX_BYTES:-33554432}"
readonly maximum_automatic_bytes=16777216
readonly compose=(
  docker compose --env-file "$capture_env" --project-directory "$root" -f "$compose_file"
)

[[ "$maximum_source_bytes" =~ ^[0-9]+$ ]]
((maximum_source_bytes >= 1048576 && maximum_source_bytes <= 134217728))
[[ -f "$automatic_evidence" && ! -L "$automatic_evidence" ]]
(( $(wc -c < "$automatic_evidence") <= maximum_automatic_bytes ))
[[ -f "$compose_file" ]]
[[ -f "$capture_env" ]]
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

automatic_identity="$(jq -cS 'del(.evidence_sha256)' "$automatic_evidence")"
recorded_evidence_sha256="$(jq -er '.evidence_sha256' "$automatic_evidence")"
calculated_evidence_sha256="$(printf '%s' "$automatic_identity" | sha256sum | cut -d ' ' -f 1)"
readonly automatic_identity
readonly recorded_evidence_sha256 calculated_evidence_sha256
[[ "$recorded_evidence_sha256" =~ ^[0-9a-f]{64}$ ]]
[[ "$recorded_evidence_sha256" == "$calculated_evidence_sha256" ]]
jq -e '
  .schema == "qtrad-capture-qualification-v1"
  and (.candidate_start | type == "string")
  and (.generated_at | type == "string")
  and (.release.actual_image | type == "string" and test("@sha256:[0-9a-f]{64}$"))
  and (.release.postgres_image | type == "string" and test("@sha256:[0-9a-f]{64}$"))
' "$automatic_evidence" > /dev/null
window_start="$(jq -er '.candidate_start' "$automatic_evidence")"
window_end="$(jq -er '.generated_at' "$automatic_evidence")"
qualification_image="$(jq -er '.release.actual_image' "$automatic_evidence")"
qualification_postgres_image="$(jq -er '.release.postgres_image' "$automatic_evidence")"
readonly window_start window_end qualification_image qualification_postgres_image
start_epoch="$(utc_epoch "$window_start")"
end_epoch="$(utc_epoch "$window_end")"
now_epoch="$(utc_epoch "$now")"
readonly start_epoch end_epoch now_epoch
((end_epoch - start_epoch >= 259200))
((now_epoch >= end_epoch))

work_dir="$(mktemp -d "$(dirname "$output")/.qualification-logs.XXXXXX")"
cleanup() {
  rm -rf "$work_dir"
}
trap cleanup EXIT
chmod 0700 "$work_dir"

tool_sha256="$(sha256sum "${BASH_SOURCE[0]}" | cut -d ' ' -f 1)"
readonly tool_sha256
timeout 30 "${compose[@]}" ps --format json > "$work_dir/compose.raw.json"
(( $(wc -c < "$work_dir/compose.raw.json") <= 1048576 ))
jq -s 'if length == 1 and (.[0] | type == "array") then .[0] else . end' \
  "$work_dir/compose.raw.json" > "$work_dir/compose.json"
rm "$work_dir/compose.raw.json"
jq -e '
  type == "array" and length == 3
  and ([.[].Service] | sort == ["api", "db", "ingest"])
  and all(.[]; .State == "running" and (.Name | type == "string" and length > 0))
' "$work_dir/compose.json" > /dev/null

source_rows='[]'
container_rows='[]'

capture_bounded() {
  local path=$1
  shift
  local -a statuses=()
  set +e
  timeout 60 "$@" 2>&1 \
    | head --bytes="$((maximum_source_bytes + 1))" > "$path"
  statuses=("${PIPESTATUS[@]}")
  set -e
  ((statuses[1] == 0))
  (( $(wc -c < "$path") <= maximum_source_bytes ))
  ((statuses[0] == 0))
}

record_source() {
  local kind=$1
  local name=$2
  local file=$3
  local path="$work_dir/$file"
  local bytes
  local lines
  local first_timestamp
  local last_timestamp
  local digest
  bytes="$(wc -c < "$path")"
  ((bytes <= maximum_source_bytes))
  lines="$(wc -l < "$path")"
  ((lines > 0))
  first_timestamp="$(awk 'NR == 1 {print $1}' "$path")"
  last_timestamp="$(awk 'END {print $1}' "$path")"
  [[ "$first_timestamp" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T ]]
  [[ "$last_timestamp" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T ]]
  digest="$(sha256sum "$path" | cut -d ' ' -f 1)"
  source_rows="$(
    jq -cS \
      --arg kind "$kind" \
      --arg name "$name" \
      --arg file "$file" \
      --arg sha256 "$digest" \
      --arg first_timestamp "$first_timestamp" \
      --arg last_timestamp "$last_timestamp" \
      --argjson bytes "$bytes" \
      --argjson lines "$lines" \
      '. + [{kind:$kind,name:$name,file:$file,sha256:$sha256,bytes:$bytes,lines:$lines,
        first_timestamp:$first_timestamp,last_timestamp:$last_timestamp}]' \
      <<< "$source_rows"
  )"
}

for service in api db ingest; do
  container="$(jq -er --arg service "$service" '.[] | select(.Service == $service) | .Name' \
    "$work_dir/compose.json")"
  inspect_file="container-$service.json"
  timeout 30 docker inspect "$container" \
    | jq -e 'select(length == 1) | .[0]
      | {id:.Id,name:.Name,created:.Created,restart_count:.RestartCount,
         configured_image:.Config.Image,image_id:.Image,
         started_at:.State.StartedAt,status:.State.Status,
         log_config:.HostConfig.LogConfig}' > "$work_dir/$inspect_file"
  jq -e '
    (.id | type == "string" and test("^[0-9a-f]{64}$"))
    and (.configured_image | type == "string" and test("@sha256:[0-9a-f]{64}$"))
    and (.image_id | type == "string" and test("^sha256:[0-9a-f]{64}$"))
    and .status == "running"
    and (.restart_count | type == "number" and . >= 0 and floor == .)
    and (.log_config.Type | type == "string" and length > 0)
  ' "$work_dir/$inspect_file" > /dev/null
  if [[ "$service" == api || "$service" == ingest ]]; then
    [[ "$(jq -er '.configured_image' "$work_dir/$inspect_file")" == "$qualification_image" ]]
  elif [[ "$service" == db ]]; then
    [[ "$(jq -er '.configured_image' "$work_dir/$inspect_file")" == "$qualification_postgres_image" ]]
  fi
  (( $(wc -c < "$work_dir/$inspect_file") <= 65536 ))
  inspect_sha="$(sha256sum "$work_dir/$inspect_file" | cut -d ' ' -f 1)"
  container_rows="$(
    jq -cS \
      --arg service "$service" \
      --arg file "$inspect_file" \
      --arg sha256 "$inspect_sha" \
      --slurpfile inspect "$work_dir/$inspect_file" \
      '. + [{service:$service,file:$file,sha256:$sha256,
        container_id:$inspect[0].id,configured_image:$inspect[0].configured_image,
        image_id:$inspect[0].image_id,created:$inspect[0].created,
        started_at:$inspect[0].started_at,restart_count:$inspect[0].restart_count,
        log_config:$inspect[0].log_config}]' <<< "$container_rows"
  )"

  log_file="container-$service.log"
  capture_bounded "$work_dir/$log_file" \
    docker logs --timestamps --since "$window_start" --until "$window_end" "$container"
  record_source container "$service" "$log_file"
done

for unit in docker.service qtrad-capture.service qtrad-ingest.service tailscaled.service; do
  file="journal-$unit.log"
  capture_bounded "$work_dir/$file" \
    journalctl --utc --no-pager --output=short-iso-precise \
      --since="$window_start" --until="$window_end" --unit="$unit"
  record_source systemd "$unit" "$file"
done

rm "$work_dir/compose.json"
created_at="$now"
identity="$(
  jq -cS -n \
    --arg schema qtrad-capture-qualification-log-bundle-v1 \
    --arg created_at "$created_at" \
    --arg window_start "$window_start" \
    --arg window_end "$window_end" \
    --arg qualification_evidence_sha256 "$recorded_evidence_sha256" \
    --arg tool_sha256 "$tool_sha256" \
    --argjson containers "$container_rows" \
    --argjson sources "$source_rows" \
    '{schema:$schema,created_at:$created_at,
      qualification_evidence_sha256:$qualification_evidence_sha256,
      window_start:$window_start,window_end:$window_end,tool_sha256:$tool_sha256,
      containers:$containers,sources:$sources}'
)"
readonly identity
manifest_sha256="$(printf '%s' "$identity" | sha256sum | cut -d ' ' -f 1)"
jq --arg manifest_sha256 "$manifest_sha256" \
  '. + {manifest_sha256:$manifest_sha256}' <<< "$identity" > "$work_dir/manifest.json"
chmod 0600 "$work_dir"/*
mv "$work_dir" "$output"
trap - EXIT
printf '%s\n' "$output"
