#!/usr/bin/env bash
set -euo pipefail

umask 077

if (($# < 1)); then
  printf 'usage: %s plan LABEL CUTOFF | execute LABEL CONFIRMED_PLAN_HASH\n' "${0##*/}" >&2
  exit 64
fi

readonly mode=$1
readonly root="${QTRAD_CAPTURE_ROOT:?QTRAD_CAPTURE_ROOT is required}"
readonly compose_file="$root/compose.capture.yaml"
readonly capture_env="${QTRAD_CAPTURE_ENV:-/etc/qtrad/capture.env}"
readonly evidence_dir="${QTRAD_RUN_RECONCILIATION_EVIDENCE_DIR:?QTRAD_RUN_RECONCILIATION_EVIDENCE_DIR is required}"
readonly reconciliation_image="${QTRAD_RUN_RECONCILIATION_IMAGE:?QTRAD_RUN_RECONCILIATION_IMAGE is required}"
readonly universe_path="${QTRAD_RUN_RECONCILIATION_UNIVERSE_PATH:-/app/config/capture-v1.toml}"
readonly application_uid=10001
readonly compose=(
  docker compose --env-file "$capture_env" --project-directory "$root" -f "$compose_file"
)

[[ "$reconciliation_image" =~ ^[^[:space:]]+@sha256:[0-9a-f]{64}$ ]]
[[ "$universe_path" =~ ^/app/[a-zA-Z0-9._/-]+$ ]]
[[ "$universe_path" != *'/../'* && "$universe_path" != *'/./'* ]]
[[ -f "$compose_file" ]]
[[ -f "$capture_env" ]]
[[ ! -L "$evidence_dir" ]]

case "$mode" in
  plan)
    if (($# != 3)); then
      printf 'usage: %s plan LABEL CUTOFF\n' "${0##*/}" >&2
      exit 64
    fi
    readonly label=$2
    readonly cutoff=$3
    readonly output_name="run-reconciliation-$label.json"
    readonly output="$evidence_dir/$output_name"
    [[ "$label" =~ ^[a-z0-9][a-z0-9._-]{0,47}$ ]]
    [[ "$cutoff" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?Z$ ]]
    [[ ! -e "$output" ]]
    docker image inspect "$reconciliation_image" > /dev/null

    install -d -o "$application_uid" -g "$application_uid" -m 0700 "$evidence_dir"
    completed=0
    secure_plan() {
      local exit_code=$?
      if ((completed == 0)); then
        rm -f "$output"
      fi
      chown root:root "$evidence_dir"
      chmod 0700 "$evidence_dir"
      exit "$exit_code"
    }
    trap secure_plan EXIT

    QTRAD_IMAGE="$reconciliation_image" "${compose[@]}" run \
      --rm --no-deps --pull never \
      -e QTRAD_IMAGE="$reconciliation_image" \
      -v "$evidence_dir:/evidence:Z" \
      ingest python -m qtrad runs reconcile-plan \
      --universe "$universe_path" \
      --cutoff "$cutoff" \
      --output "/evidence/$output_name"

    [[ -s "$output" ]]
    chown root:root "$output"
    chmod 0600 "$output"
    chown root:root "$evidence_dir"
    chmod 0700 "$evidence_dir"
    completed=1
    trap - EXIT
    printf '%s\n' "$output"
    ;;
  execute)
    if (($# != 3)); then
      printf 'usage: %s execute LABEL CONFIRMED_PLAN_HASH\n' "${0##*/}" >&2
      exit 64
    fi
    readonly label=$2
    readonly confirmed_plan_hash=$3
    readonly input_name="run-reconciliation-$label.json"
    readonly input="$evidence_dir/$input_name"
    [[ "$label" =~ ^[a-z0-9][a-z0-9._-]{0,47}$ ]]
    [[ "$confirmed_plan_hash" =~ ^[0-9a-f]{64}$ ]]
    [[ -f "$input" && ! -L "$input" && -s "$input" ]]
    docker image inspect "$reconciliation_image" > /dev/null

    chown "$application_uid:$application_uid" "$evidence_dir" "$input"
    restore_ownership() {
      local exit_code=$?
      chown root:root "$input" "$evidence_dir"
      chmod 0600 "$input"
      chmod 0700 "$evidence_dir"
      exit "$exit_code"
    }
    trap restore_ownership EXIT

    QTRAD_IMAGE="$reconciliation_image" "${compose[@]}" run \
      --rm --no-deps --pull never \
      -e QTRAD_IMAGE="$reconciliation_image" \
      -v "$evidence_dir:/evidence:ro,Z" \
      ingest python -m qtrad runs reconcile \
      --plan "/evidence/$input_name" \
      --confirm-plan-hash "$confirmed_plan_hash"

    chown root:root "$input" "$evidence_dir"
    chmod 0600 "$input"
    chmod 0700 "$evidence_dir"
    trap - EXIT
    ;;
  *)
    printf 'usage: %s plan LABEL CUTOFF | execute LABEL CONFIRMED_PLAN_HASH\n' "${0##*/}" >&2
    exit 64
    ;;
esac
