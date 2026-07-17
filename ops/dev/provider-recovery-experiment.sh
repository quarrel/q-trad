#!/usr/bin/env bash
set -euo pipefail

if (($# < 1)); then
  printf 'usage: %s EVIDENCE.json [provider_recovery_experiment.py options...]\n' "${0##*/}" >&2
  exit 64
fi

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly root
cd "$root"

exec uv run ops/dev/provider_recovery_experiment.py "$@"
