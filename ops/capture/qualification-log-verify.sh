#!/usr/bin/env bash
set -euo pipefail

umask 077

if (($# != 2)); then
  printf 'usage: %s AUTOMATIC_EVIDENCE BUNDLE_DIRECTORY\n' "${0##*/}" >&2
  exit 64
fi

readonly automatic_evidence=$1
readonly bundle=$2
readonly manifest="$bundle/manifest.json"
readonly maximum_automatic_bytes=16777216
readonly maximum_manifest_bytes=1048576
readonly maximum_inspect_bytes=65536
readonly maximum_source_bytes=134217728

[[ -f "$automatic_evidence" && ! -L "$automatic_evidence" ]]
[[ -d "$bundle" && ! -L "$bundle" ]]
[[ -f "$manifest" && ! -L "$manifest" ]]
(( $(wc -c < "$automatic_evidence") <= maximum_automatic_bytes ))
(( $(wc -c < "$manifest") <= maximum_manifest_bytes ))
[[ "$(stat -c '%a' "$bundle")" == 700 ]]
[[ "$(stat -c '%u' "$bundle")" == "$EUID" ]]

sha256_canonical_without() {
  local path=$1
  local field=$2
  local canonical
  canonical="$(jq -cS "del(.$field)" "$path")"
  printf '%s' "$canonical" | sha256sum | cut -d ' ' -f 1
}

utc_epoch() {
  local value=$1
  local epoch
  local normalised
  epoch="$(date --date="$value" +%s)"
  normalised="$(date --utc --date="@$epoch" +%Y-%m-%dT%H:%M:%SZ)"
  [[ "$normalised" == "$value" ]]
  printf '%s\n' "$epoch"
}

automatic_sha256="$(jq -er '.evidence_sha256' "$automatic_evidence")"
calculated_automatic_sha256="$(
  sha256_canonical_without "$automatic_evidence" evidence_sha256
)"
manifest_sha256="$(jq -er '.manifest_sha256' "$manifest")"
calculated_manifest_sha256="$(sha256_canonical_without "$manifest" manifest_sha256)"
readonly automatic_sha256 calculated_automatic_sha256
readonly manifest_sha256 calculated_manifest_sha256
[[ "$automatic_sha256" =~ ^[0-9a-f]{64}$ ]]
[[ "$automatic_sha256" == "$calculated_automatic_sha256" ]]
[[ "$manifest_sha256" =~ ^[0-9a-f]{64}$ ]]
[[ "$manifest_sha256" == "$calculated_manifest_sha256" ]]

jq -e '
  .schema == "qtrad-capture-qualification-v1"
  and (.candidate_start | type == "string")
  and (.generated_at | type == "string")
  and (.release.actual_image | type == "string" and test("@sha256:[0-9a-f]{64}$"))
  and (.release.postgres_image | type == "string" and test("@sha256:[0-9a-f]{64}$"))
' "$automatic_evidence" > /dev/null

jq -e --arg evidence_sha256 "$automatic_sha256" '
  def sha256: type == "string" and test("^[0-9a-f]{64}$");
  def timestamp: type == "string" and test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T");
  type == "object"
  and (keys == [
    "containers", "created_at", "lifecycle_summary", "manifest_sha256", "qualification_evidence_sha256",
    "schema", "sources", "tool_sha256", "window_end", "window_start"
  ])
  and .schema == "qtrad-capture-qualification-log-bundle-v2"
  and .qualification_evidence_sha256 == $evidence_sha256
  and (.created_at | type == "string")
  and (.window_start | type == "string")
  and (.window_end | type == "string")
  and (.tool_sha256 | sha256)
  and (.manifest_sha256 | sha256)
  and (.containers | type == "array" and length == 3)
  and ([.containers[] | {service, file}] | sort_by(.service)) == [
    {service:"api",file:"container-api.json"},
    {service:"db",file:"container-db.json"},
    {service:"ingest",file:"container-ingest.json"}
  ]
  and all(.containers[];
    type == "object"
    and (keys == [
      "configured_image", "container_id", "created", "file", "image_id", "log_config",
      "restart_count", "service", "sha256", "started_at"
    ])
    and (.container_id | test("^[0-9a-f]{64}$"))
    and (.configured_image | test("@sha256:[0-9a-f]{64}$"))
    and (.image_id | test("^sha256:[0-9a-f]{64}$"))
    and (.sha256 | sha256)
    and (.created | timestamp) and (.started_at | timestamp)
    and (.restart_count | type == "number" and . >= 0 and floor == .)
    and (.log_config | type == "object"))
  and (.sources | type == "array" and length == 7)
  and (.lifecycle_summary.schema == "qtrad-capture-lifecycle-summary-v1")
  and (.lifecycle_summary.parsed_records | type == "number" and . > 0 and floor == .)
  and (.lifecycle_summary.adverse_event_count | type == "number" and . >= 0 and floor == .)
  and (.lifecycle_summary.tracked_events | type == "array" and length == 15)
  and (.lifecycle_summary.stream_statuses | type == "array")
  and ([.sources[] | {kind, name, file}] | sort_by(.kind, .name)) == [
    {kind:"container",name:"api",file:"container-api.log"},
    {kind:"container",name:"db",file:"container-db.log"},
    {kind:"container",name:"ingest",file:"container-ingest.log"},
    {kind:"systemd",name:"docker.service",file:"journal-docker.service.log"},
    {kind:"systemd",name:"qtrad-capture.service",file:"journal-qtrad-capture.service.log"},
    {kind:"systemd",name:"qtrad-ingest.service",file:"journal-qtrad-ingest.service.log"},
    {kind:"systemd",name:"tailscaled.service",file:"journal-tailscaled.service.log"}
  ]
  and all(.sources[];
    type == "object"
    and (keys == [
      "bytes", "file", "first_timestamp", "kind", "last_timestamp", "lines", "name", "sha256"
    ])
    and (.sha256 | sha256)
    and (.bytes | type == "number" and . > 0 and . <= 134217728 and floor == .)
    and (.lines | type == "number" and . > 0 and floor == .)
    and (.first_timestamp | timestamp) and (.last_timestamp | timestamp))
' "$manifest" > /dev/null

window_start="$(jq -er '.window_start' "$manifest")"
window_end="$(jq -er '.window_end' "$manifest")"
created_at="$(jq -er '.created_at' "$manifest")"
readonly window_start window_end created_at
[[ "$window_start" == "$(jq -er '.candidate_start' "$automatic_evidence")" ]]
[[ "$window_end" == "$(jq -er '.generated_at' "$automatic_evidence")" ]]
start_epoch="$(utc_epoch "$window_start")"
end_epoch="$(utc_epoch "$window_end")"
created_epoch="$(utc_epoch "$created_at")"
readonly start_epoch end_epoch created_epoch
((end_epoch - start_epoch >= 259200))
((created_epoch >= end_epoch))

qualification_image="$(jq -er '.release.actual_image' "$automatic_evidence")"
qualification_postgres_image="$(jq -er '.release.postgres_image' "$automatic_evidence")"
readonly qualification_image qualification_postgres_image

declare -A expected_files=(
  [manifest.json]=1
  [container-api.json]=1
  [container-db.json]=1
  [container-ingest.json]=1
  [container-api.log]=1
  [container-db.log]=1
  [container-ingest.log]=1
    [journal-docker.service.log]=1
    [journal-qtrad-capture.service.log]=1
    [journal-qtrad-ingest.service.log]=1
    [journal-tailscaled.service.log]=1
)
shopt -s dotglob nullglob
bundle_entries=("$bundle"/*)
readonly bundle_entries
((${#bundle_entries[@]} == ${#expected_files[@]}))
for path in "${bundle_entries[@]}"; do
  name=${path##*/}
  [[ -n "${expected_files[$name]:-}" ]]
  [[ -f "$path" && ! -L "$path" ]]
  [[ "$(stat -c '%a' "$path")" == 600 ]]
  [[ "$(stat -c '%u' "$path")" == "$EUID" ]]
done

for service in api db ingest; do
  row="$(jq -c --arg service "$service" '.containers[] | select(.service == $service)' "$manifest")"
  file="$(jq -r '.file' <<< "$row")"
  path="$bundle/$file"
  (( $(wc -c < "$path") <= maximum_inspect_bytes ))
  [[ "$(sha256sum "$path" | cut -d ' ' -f 1)" == "$(jq -r '.sha256' <<< "$row")" ]]
  jq -e --argjson row "$row" '
    type == "object"
    and (keys == [
      "configured_image", "created", "id", "image_id", "log_config", "name",
      "restart_count", "started_at", "status"
    ])
    and .status == "running"
    and .id == $row.container_id
    and .configured_image == $row.configured_image
    and .image_id == $row.image_id
    and .created == $row.created
    and .started_at == $row.started_at
    and .restart_count == $row.restart_count
    and .log_config == $row.log_config
  ' "$path" > /dev/null
  if [[ "$service" == api || "$service" == ingest ]]; then
    [[ "$(jq -er '.configured_image' <<< "$row")" == "$qualification_image" ]]
  elif [[ "$service" == db ]]; then
    [[ "$(jq -er '.configured_image' <<< "$row")" == "$qualification_postgres_image" ]]
  fi
done

while IFS= read -r row; do
  file="$(jq -r '.file' <<< "$row")"
  path="$bundle/$file"
  expected_bytes="$(jq -r '.bytes' <<< "$row")"
  expected_lines="$(jq -r '.lines' <<< "$row")"
  first_timestamp="$(jq -r '.first_timestamp' <<< "$row")"
  last_timestamp="$(jq -r '.last_timestamp' <<< "$row")"
  ((expected_bytes <= maximum_source_bytes))
  [[ "$(wc -c < "$path")" == "$expected_bytes" ]]
  [[ "$(wc -l < "$path")" == "$expected_lines" ]]
  [[ "$(sha256sum "$path" | cut -d ' ' -f 1)" == "$(jq -r '.sha256' <<< "$row")" ]]
  [[ "$(awk 'NR == 1 {print $1}' "$path")" == "$first_timestamp" ]]
  [[ "$(awk 'END {print $1}' "$path")" == "$last_timestamp" ]]
  first_epoch="$(date --date="$first_timestamp" +%s)"
  last_epoch="$(date --date="$last_timestamp" +%s)"
  ((first_epoch >= start_epoch))
  ((last_epoch <= end_epoch))
  ((first_epoch <= last_epoch))
done < <(jq -c '.sources[]' "$manifest")

summary_filter="$(dirname "${BASH_SOURCE[0]}")/qualification-log-summary.jq"
[[ -f "$summary_filter" && ! -L "$summary_filter" ]]
recomputed_summary="$(jq -ceS -R -s -f "$summary_filter" "$bundle/container-ingest.log")"
jq -e --argjson recomputed "$recomputed_summary" '.lifecycle_summary == $recomputed' \
  "$manifest" > /dev/null

printf '%s\n' "$manifest_sha256"
