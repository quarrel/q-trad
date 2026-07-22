#!/usr/bin/env bash
set -euo pipefail

umask 077

if (($# != 5)); then
  printf 'usage: %s RELEASE_DIR DESCRIPTOR_RELATIVE RELEASE_COMMIT DESCRIPTOR_SHA256 CI_RUN_ID\n' \
    "${0##*/}" >&2
  exit 64
fi
if ((EUID != 0)); then
  printf 'activate-release.sh must run as root\n' >&2
  exit 77
fi

readonly release_dir=$1
readonly descriptor_relative=$2
readonly release_commit=$3
readonly expected_descriptor_sha=$4
readonly ci_run_id=$5
readonly descriptor="$release_dir/$descriptor_relative"
readonly active_release=/opt/qtrad-capture
readonly active_descriptor="$active_release/$descriptor_relative"
readonly capture_env=/etc/qtrad/capture.env
readonly universe_dir=/etc/qtrad/universe
readonly active_universe="$universe_dir/active.toml"
readonly evidence_dir="${QTRAD_DEPLOYMENT_EVIDENCE_DIR:-/var/lib/qtrad-capture/deployments}"
stage=bootstrap

[[ "$release_dir" == "/opt/qtrad-releases/$release_commit" ]]
[[ "$release_commit" =~ ^[0-9a-f]{40}$ ]]
[[ "$descriptor_relative" =~ ^config/[a-z0-9._-]+\.toml$ ]]
[[ "$expected_descriptor_sha" =~ ^[0-9a-f]{64}$ ]]
[[ "$ci_run_id" =~ ^[0-9]+$ ]]
[[ -d "$release_dir" && ! -L "$release_dir" ]]
[[ -f "$descriptor" && ! -L "$descriptor" ]]
[[ "$(sha256sum "$descriptor" | cut -d ' ' -f 1)" == "$expected_descriptor_sha" ]]
[[ -f "$capture_env" && ! -L "$capture_env" ]]
[[ -f "$active_universe" && ! -L "$active_universe" ]]

mapfile -t bootstrap_images < <(
  sed -n '0,/^\[rollback\]/{ s/^application_image = "\([^"]*\)"$/\1/p; }' "$descriptor"
)
((${#bootstrap_images[@]} == 1))
readonly candidate_image=${bootstrap_images[0]}
[[ "$candidate_image" =~ ^[^[:space:]@]+@sha256:[0-9a-f]{64}$ ]]
mapfile -t active_images < <(sed -n 's/^QTRAD_IMAGE=//p' "$capture_env")
((${#active_images[@]} == 1))
readonly previous_image=${active_images[0]}
[[ "$previous_image" =~ ^[^[:space:]@]+@sha256:[0-9a-f]{64}$ ]]
mapfile -t rollback_images < <(sed -n '/^\[rollback\]/,$ { s/^application_image = "\([^"]*\)"$/\1/p; }' "$descriptor")
((${#rollback_images[@]} == 1))
readonly bootstrap_rollback_image=${rollback_images[0]}
[[ "$bootstrap_rollback_image" =~ ^[^[:space:]@]+@sha256:[0-9a-f]{64}$ ]]
readonly image_repository="${candidate_image%@sha256:*}"
historical_rollback_image=''
if [[ -f "$active_descriptor" && ! -L "$active_descriptor" ]]; then
  mapfile -t historical_rollback_images < <(
    sed -n '/^\[rollback\]/,$ { s/^application_image = "\([^"]*\)"$/\1/p; }' "$active_descriptor"
  )
  ((${#historical_rollback_images[@]} == 1))
  historical_rollback_image=${historical_rollback_images[0]}
  [[ "$historical_rollback_image" =~ ^[^[:space:]@]+@sha256:[0-9a-f]{64}$ ]]
fi

preserved_image_ids=()
for image_reference in "$previous_image" "$bootstrap_rollback_image" "$historical_rollback_image" "$candidate_image"; do
  [[ -n "$image_reference" ]] || continue
  image_id="$(docker image inspect "$image_reference" --format '{{.Id}}' 2> /dev/null || true)"
  if [[ -n "$image_id" ]]; then
    preserved_image_ids+=("$image_id")
  fi
done
removed_image_ids=()
stage=image-retention
mapfile -t repository_image_ids < <(
  docker image ls --no-trunc --format '{{.Repository}}|{{.ID}}' \
    | awk -F'|' -v repository="$image_repository" '$1 == repository { print $2 }' \
    | sort -u
)
for image_id in "${repository_image_ids[@]}"; do
  if printf '%s\n' "${preserved_image_ids[@]}" | grep -Fxq "$image_id"; then
    continue
  fi
  docker image rm "$image_id" > /dev/null
  removed_image_ids+=("$image_id")
done
if ((${#removed_image_ids[@]} == 0)); then
  removed_image_ids_json='[]'
else
  removed_image_ids_json="$(printf '%s\n' "${removed_image_ids[@]}" | jq -Rsc 'split("\n") | map(select(length > 0))')"
fi
readonly removed_image_ids_json
env PATH=/usr/local/bin:/usr/bin:/bin docker pull "$candidate_image" > /dev/null
stage=descriptor_validation

descriptor_json="$(
  docker run --rm --network none --read-only --tmpfs /tmp \
    --env UV_CACHE_DIR=/tmp/uv-cache \
    --volume "$release_dir:/release:ro,Z" \
    "$candidate_image" python -m qtrad deployment inspect \
    --descriptor "/release/$descriptor_relative" --repository-root /release
)"
readonly descriptor_json
candidate_name="$(jq -er '.name' <<< "$descriptor_json")"
readonly candidate_name
application_commit="$(jq -er '.application_commit' <<< "$descriptor_json")"
readonly application_commit
application_image="$(jq -er '.application_image' <<< "$descriptor_json")"
readonly application_image
universe_file="$(jq -er '.universe_file' <<< "$descriptor_json")"
readonly universe_file
candidate_hash="$(jq -er '.universe_configuration_hash' <<< "$descriptor_json")"
readonly candidate_hash
candidate_count="$(jq -er '.universe_instrument_count' <<< "$descriptor_json")"
readonly candidate_count
schema_head="$(jq -er '.schema_head' <<< "$descriptor_json")"
readonly schema_head
rollback_release_commit="$(jq -er '.rollback_release_commit' <<< "$descriptor_json")"
readonly rollback_release_commit
rollback_image="$(jq -er '.rollback_application_image' <<< "$descriptor_json")"
readonly rollback_image
rollback_universe_name="$(jq -er '.rollback_universe_name' <<< "$descriptor_json")"
readonly rollback_universe_name
[[ "$application_image" == "$candidate_image" ]]

previous_release="$(readlink -f "$active_release")"
readonly previous_release
readonly expected_previous_release="/opt/qtrad-releases/$rollback_release_commit"
[[ "$previous_release" == "$expected_previous_release" ]]
[[ "$previous_image" == "$rollback_image" ]]

before_readiness="$(curl --fail --silent http://127.0.0.1:8000/health/ready)"
readonly before_readiness
jq -e '.ready == true' <<< "$before_readiness" > /dev/null
previous_hash="$(jq -er '.configuration_hash' <<< "$before_readiness")"
readonly previous_hash
previous_count="$(jq -er '.expected_instruments' <<< "$before_readiness")"
readonly previous_count

active_universe_json="$(
  docker run --rm --network none --read-only --tmpfs /tmp \
    --env UV_CACHE_DIR=/tmp/uv-cache \
    --volume "$universe_dir:/universe:ro,Z" \
    "$previous_image" python -c \
    'import json; from pathlib import Path; from qtrad.runtime.universe import load_capture_universe; u = load_capture_universe(Path("/universe/active.toml")); print(json.dumps({"name": u.name, "hash": u.configuration_hash}))'
)"
readonly active_universe_json
[[ "$(jq -er '.name' <<< "$active_universe_json")" == "$rollback_universe_name" ]]
[[ "$(jq -er '.hash' <<< "$active_universe_json")" == "$previous_hash" ]]

current_schema="$(
  docker compose --env-file "$capture_env" --project-directory "$active_release" \
    -f "$active_release/compose.capture.yaml" exec -T db \
    psql --username=qtrad_capture --dbname=qtrad_capture --tuples-only --no-align \
    --command 'SELECT version_num FROM alembic_version;'
)"
readonly current_schema
[[ "$current_schema" == "$schema_head" ]]

backup_started_at="$(date --utc +%Y-%m-%dT%H:%M:%SZ)"
readonly backup_started_at
stage=backup
systemctl start qtrad-backup.service
mapfile -t status_dirs < <(sed -n 's/^QTRAD_STATUS_DIR=//p' /etc/qtrad/capture-backup.env)
((${#status_dirs[@]} == 1))
[[ "${status_dirs[0]}" =~ ^/[a-zA-Z0-9._/-]+$ ]]
[[ "${status_dirs[0]}" != *'/../'* && "${status_dirs[0]}" != *'/./'* ]]
readonly backup_status="${status_dirs[0]}/backup-status.json"
[[ -f "$backup_status" && ! -L "$backup_status" ]]
jq -e --arg hash "$previous_hash" --arg started "$backup_started_at" \
  '.success == true and .universe_hash == $hash and .completed_at >= $started' \
  "$backup_status" > /dev/null
backup_completed_at="$(jq -er '.completed_at' "$backup_status")"
readonly backup_completed_at

install -d -o root -g root -m 0700 "$evidence_dir"
started_at="$(date --utc +%Y-%m-%dT%H:%M:%SZ)"
readonly started_at
readonly deployment_id="$candidate_name-${started_at//[:]/}"
readonly state_dir="$evidence_dir/$deployment_id.state"
readonly evidence="$evidence_dir/$deployment_id.json"
[[ ! -e "$state_dir" && ! -e "$evidence" ]]
install -d -o root -g root -m 0700 "$state_dir"
cp --preserve=mode,ownership "$capture_env" "$state_dir/capture.env"
cp --preserve=mode,ownership "$active_universe" "$state_dir/active.toml"
printf '%s\n' "$previous_release" > "$state_dir/release"

mutated=0
completed=0
write_evidence() {
  local result=$1
  local rollback_succeeded=$2
  local temporary
  temporary="$(mktemp "$evidence_dir/.deployment.XXXXXX")"
  jq -n \
    --arg schema qtrad-capture-deployment-v1 \
    --arg deployment "$candidate_name" \
    --arg release_commit "$release_commit" \
    --arg application_commit "$application_commit" \
    --arg descriptor_sha256 "$expected_descriptor_sha" \
    --argjson removed_image_ids "$removed_image_ids_json" \
    --arg ci_run_id "$ci_run_id" \
    --arg application_image "$application_image" \
    --arg configuration_hash "$candidate_hash" \
    --argjson instrument_count "$candidate_count" \
    --arg previous_configuration_hash "$previous_hash" \
    --arg backup_completed_at "$backup_completed_at" \
    --arg started_at "$started_at" \
    --arg completed_at "$(date --utc +%Y-%m-%dT%H:%M:%SZ)" \
    --arg result "$result" \
    --arg failure_stage "$stage" \
    --argjson exit_code "${failure_exit_code:-0}" \
    --argjson rollback_succeeded "$rollback_succeeded" \
    '{schema:$schema,deployment:$deployment,release_commit:$release_commit,
      application_commit:$application_commit,descriptor_sha256:$descriptor_sha256,
      ci_run_id:$ci_run_id,
      removed_image_ids:$removed_image_ids,
      application_image:$application_image,configuration_hash:$configuration_hash,
      instrument_count:$instrument_count,previous_configuration_hash:$previous_configuration_hash,
      backup_completed_at:$backup_completed_at,started_at:$started_at,
      completed_at:$completed_at,result:$result,
      failure_stage:$failure_stage,exit_code:$exit_code,
      rollback_succeeded:$rollback_succeeded}' > "$temporary"
  chmod 0600 "$temporary"
  mv "$temporary" "$evidence"
}
wait_ready() {
  local expected_hash=$1
  local expected_count=$2
  local payload
  for _attempt in $(seq 1 90); do
    payload="$(curl --silent http://127.0.0.1:8000/health/ready || true)"
    if jq -e --arg hash "$expected_hash" --argjson count "$expected_count" \
      '.ready == true and .configuration_hash == $hash
       and .expected_instruments == $count and .fresh_quote_count == $count' \
      <<< "$payload" > /dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  return 1
}
rollback_on_failure() {
  local exit_code=$?
  failure_exit_code=$exit_code
  if ((completed == 1)); then
    exit "$exit_code"
  fi
  local rollback_succeeded=false
  if ((mutated == 1)); then
    set +e
    cp "$state_dir/active.toml" "$universe_dir/active.toml.next"
    chown root:root "$universe_dir/active.toml.next"
    chmod 0644 "$universe_dir/active.toml.next"
    mv "$universe_dir/active.toml.next" "$active_universe"
    cp "$state_dir/capture.env" "$capture_env.next"
    chown root:root "$capture_env.next"
    chmod 0600 "$capture_env.next"
    mv "$capture_env.next" "$capture_env"
    ln -sfn "$previous_release" "$active_release.next"
    mv -Tf "$active_release.next" "$active_release"
    docker compose --env-file "$capture_env" --project-directory "$active_release" \
      -f "$active_release/compose.capture.yaml" up -d --no-deps api
    systemctl stop qtrad-ingest.service || true
    systemctl reset-failed qtrad-ingest.service || true
    systemctl start qtrad-ingest.service
    if wait_ready "$previous_hash" "$previous_count"; then
      rollback_succeeded=true
    fi
    set -e
  fi
  write_evidence failed "$rollback_succeeded"
  printf '%s\n' "$evidence" >&2
  exit "$exit_code"
}
trap rollback_on_failure EXIT

mutated=1
stage=application_swap
sed "s|^QTRAD_IMAGE=.*|QTRAD_IMAGE=$application_image|" "$capture_env" > "$capture_env.next"
[[ "$(grep -c '^QTRAD_IMAGE=' "$capture_env.next")" == 1 ]]
chown root:root "$capture_env.next"
chmod 0600 "$capture_env.next"
mv "$capture_env.next" "$capture_env"
ln -sfn "$release_dir" "$active_release.next"
mv -Tf "$active_release.next" "$active_release"
docker compose --env-file "$capture_env" --project-directory "$active_release" \
  -f "$active_release/compose.capture.yaml" up -d --no-deps api
systemctl restart qtrad-ingest.service
wait_ready "$previous_hash" "$previous_count"

cp "$release_dir/$universe_file" "$universe_dir/$candidate_name.next.toml"
stage=universe_activation
chown root:root "$universe_dir/$candidate_name.next.toml"
chmod 0644 "$universe_dir/$candidate_name.next.toml"
mv "$universe_dir/$candidate_name.next.toml" "$active_universe"
activation_at="$(date --utc +%Y-%m-%dT%H:%M:%SZ)"
readonly activation_at
docker kill --signal HUP qtrad-capture-ingest-1 > /dev/null
wait_ready "$candidate_hash" "$candidate_count"

readonly observation_seconds="${QTRAD_DEPLOYMENT_OBSERVE_SECONDS:-60}"
[[ "$observation_seconds" =~ ^[0-9]+$ ]]
((observation_seconds <= 600))
sleep "$observation_seconds"
stage=post_deployment_verification
wait_ready "$candidate_hash" "$candidate_count"

stage=system_check
system_json="$(curl --fail --silent http://127.0.0.1:8000/api/v1/system)"
readonly system_json
health_detail="$(jq -er '.adapter_health[] | select(.adapter_name == "ig-market-data") | .detail' <<< "$system_json")"
readonly health_detail
for required in \
  "subscriptions=$candidate_count/$candidate_count" \
  "updates=$candidate_count/$candidate_count" \
  "recent_quote_channels=$candidate_count/$candidate_count" \
  'stale_quote_channels=0' 'reconnects=0' 'dropped_records=0' \
  'lightstreamer_lost_updates=0' 'subscription_errors=0' 'server_errors=0'; do
  [[ "$health_detail" == *"$required"* ]]
done

stage=run_check
running_json="$(curl --fail --silent http://127.0.0.1:8000/api/v1/runs)"
readonly running_json
jq -e --arg hash "$candidate_hash" --arg previous_hash "$previous_hash" \
  'if $hash == $previous_hash then
     ([.[] | select(.kind == "INGESTION" and .status == "RUNNING")] | length) == 1
     and ([.[] | select(.kind == "INGESTION" and .status == "RUNNING")][0].configuration_hash == $hash)
   else
     ([.[] | select(.kind == "INGESTION" and .status == "RUNNING")] | length) == 1
     and ([.[] | select(.kind == "INGESTION" and .status == "RUNNING")][0].configuration_hash == $hash)
     and any(.[]; .kind == "INGESTION" and .configuration_hash == $previous_hash
                     and .status == "STOPPED")
   end' <<< "$running_json" > /dev/null
stage=reload_log_check
if [[ "$candidate_hash" == "$previous_hash" ]]; then
  docker logs --since "$activation_at" qtrad-capture-ingest-1 2>&1 \
    | jq -s -e --arg hash "$candidate_hash" \
      'any(.[]; .event == "capture_universe_reload_unchanged" and .configuration_hash == $hash)' \
      > /dev/null
else
  docker logs --since "$activation_at" qtrad-capture-ingest-1 2>&1 \
    | jq -s -e --arg hash "$candidate_hash" \
      'any(.[]; .event == "capture_universe_reloaded" and .configuration_hash == $hash)' \
      > /dev/null
fi
if docker logs --since "$activation_at" qtrad-capture-ingest-1 2>&1 \
  | jq -s -e 'any(.[]; .event == "capture_universe_reload_rejected")' > /dev/null; then
  exit 1
fi
stage=image_check
readonly image_id="sha256:${application_image##*@sha256:}"
[[ "$(docker inspect qtrad-capture-ingest-1 --format '{{.Image}}')" == "$image_id" ]]
[[ "$(docker inspect qtrad-capture-api-1 --format '{{.Image}}')" == "$image_id" ]]
stage=release_check
[[ "$(readlink -f "$active_release")" == "$release_dir" ]]

write_evidence succeeded null
completed=1
trap - EXIT
printf '%s\n' "$evidence"
