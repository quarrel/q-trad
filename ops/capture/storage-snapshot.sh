#!/usr/bin/env bash
set -euo pipefail

umask 077

if (($# != 1)); then
  printf 'usage: %s SNAPSHOT_LABEL\n' "${0##*/}" >&2
  exit 64
fi

readonly snapshot_label=$1
readonly root="${QTRAD_CAPTURE_ROOT:?QTRAD_CAPTURE_ROOT is required}"
readonly compose_file="$root/compose.capture.yaml"
readonly capture_env="${QTRAD_CAPTURE_ENV:-/etc/qtrad/capture.env}"
readonly evidence_dir="${QTRAD_STORAGE_EVIDENCE_DIR:?QTRAD_STORAGE_EVIDENCE_DIR is required}"
readonly inspector_image="${QTRAD_STORAGE_INSPECTOR_IMAGE:?QTRAD_STORAGE_INSPECTOR_IMAGE is required}"
readonly universe_path="${QTRAD_STORAGE_UNIVERSE_PATH:-/app/config/capture-v1.toml}"
readonly application_uid=10001
readonly output_name="storage-$snapshot_label.json"
readonly output="$evidence_dir/$output_name"
readonly compose=(
  docker compose --env-file "$capture_env" --project-directory "$root" -f "$compose_file"
)

[[ "$snapshot_label" =~ ^[a-z0-9][a-z0-9._-]{0,47}$ ]]
[[ "$inspector_image" =~ ^[^[:space:]]+@sha256:[0-9a-f]{64}$ ]]
[[ "$universe_path" =~ ^/app/[a-zA-Z0-9._/-]+$ ]]
[[ "$universe_path" != *'/../'* && "$universe_path" != *'/./'* ]]
[[ -f "$compose_file" ]]
[[ -f "$capture_env" ]]
[[ ! -L "$evidence_dir" ]]
[[ ! -e "$output" ]]

docker image inspect "$inspector_image" > /dev/null

install -d -o "$application_uid" -g "$application_uid" -m 0700 "$evidence_dir"
completed=0
secure_evidence() {
  local exit_code=$?
  if ((completed == 0)); then
    rm -f "$output"
  fi
  chown root:root "$evidence_dir"
  chmod 0700 "$evidence_dir"
  exit "$exit_code"
}
trap secure_evidence EXIT

QTRAD_IMAGE="$inspector_image" "${compose[@]}" run \
  --rm --no-deps --pull never \
  -v "$evidence_dir:/evidence:Z" \
  ingest python -m qtrad storage snapshot \
  --universe "$universe_path" \
  --output "/evidence/$output_name"

[[ -s "$output" ]]
chown root:root "$output"
chmod 0600 "$output"
chown root:root "$evidence_dir"
chmod 0700 "$evidence_dir"
completed=1
trap - EXIT

printf '%s\n' "$output"
