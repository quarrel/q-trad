#!/usr/bin/env bash
set -euo pipefail

umask 077

if (($# != 3)); then
  printf 'usage: %s DESCRIPTOR SSH_HOST CONFIRM_DESCRIPTOR_SHA256\n' "${0##*/}" >&2
  exit 64
fi

readonly descriptor_input=$1
readonly ssh_host=$2
readonly confirmed_descriptor_sha=$3
root="$(git rev-parse --show-toplevel)"
readonly root
descriptor="$(realpath "$descriptor_input")"
readonly descriptor

[[ "$ssh_host" =~ ^[a-zA-Z0-9._-]+@[a-zA-Z0-9._-]+$ ]]
[[ "$confirmed_descriptor_sha" =~ ^[0-9a-f]{64}$ ]]
[[ "$descriptor" == "$root"/config/* && "$descriptor" != "$root"/config/*/* ]]
[[ -f "$descriptor" && ! -L "$descriptor" ]]
[[ -z "$(git -C "$root" status --porcelain)" ]]

descriptor_sha="$(sha256sum "$descriptor" | cut -d ' ' -f 1)"
readonly descriptor_sha
[[ "$descriptor_sha" == "$confirmed_descriptor_sha" ]]
readonly descriptor_relative="${descriptor#"$root"/}"
git -C "$root" ls-files --error-unmatch "$descriptor_relative" > /dev/null
git -C "$root" diff --quiet -- "$descriptor_relative"

release_commit="$(git -C "$root" rev-parse HEAD)"
readonly release_commit
[[ "$release_commit" =~ ^[0-9a-f]{40}$ ]]
git -C "$root" fetch --quiet origin main
[[ "$(git -C "$root" rev-parse origin/main)" == "$release_commit" ]]

if [[ -z "${GH_TOKEN:-}" ]]; then
  credential_output="$(printf 'protocol=https\nhost=github.com\n\n' | git credential fill)"
  github_token="$(printf '%s\n' "$credential_output" | sed -n 's/^password=//p')"
  [[ -n "$github_token" ]]
  export GH_TOKEN="$github_token"
fi

descriptor_json="$(
  cd "$root"
  uv run qtrad deployment inspect \
    --descriptor "$descriptor_relative" \
    --repository-root "$root"
)"
readonly descriptor_json
application_commit="$(jq -er '.application_commit' <<< "$descriptor_json")"
readonly application_commit
git -C "$root" merge-base --is-ancestor "$application_commit" "$release_commit"

ci_runs="$(
  gh run list --workflow CI --commit "$release_commit" --limit 20 \
    --json status,conclusion,headSha,databaseId,url
)"
readonly ci_runs
ci_run="$(
  jq -ce --arg commit "$release_commit" \
    '[.[] | select(.headSha == $commit)] | sort_by(.databaseId) | last
     | select(.status == "completed" and .conclusion == "success")' <<< "$ci_runs"
)"
readonly ci_run
ci_run_id="$(jq -er '.databaseId' <<< "$ci_run")"
readonly ci_run_id
[[ "$ci_run_id" =~ ^[0-9]+$ ]]

archive="$(mktemp)"
readonly archive
cleanup() {
  rm -f "$archive"
}
trap cleanup EXIT
git -C "$root" archive --format=tar "$release_commit" > "$archive"

readonly remote_release="/opt/qtrad-releases/$release_commit"
ssh -o BatchMode=yes "$ssh_host" \
  "test ! -e '$remote_release' || test -f '$remote_release/$descriptor_relative'"
if ! ssh -o BatchMode=yes "$ssh_host" "test -d '$remote_release'"; then
  ssh -o BatchMode=yes "$ssh_host" \
    "sudo install -d -o root -g root -m 0755 '$remote_release' && sudo tar -x -C '$remote_release'" \
    < "$archive"
fi

ssh -o BatchMode=yes "$ssh_host" \
  "sudo bash '$remote_release/ops/capture/activate-release.sh' \
    '$remote_release' '$descriptor_relative' '$release_commit' '$descriptor_sha' '$ci_run_id'"
