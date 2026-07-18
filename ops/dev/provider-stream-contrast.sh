#!/usr/bin/env bash
set -euo pipefail

if (($# < 2)); then
  printf 'usage: %s MANIFEST.json EVENTS.jsonl.gz [provider_stream_contrast.py options...]\n' "${0##*/}" >&2
  exit 64
fi

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly root
cd "$root"

exec uv run ops/dev/provider_stream_contrast.py "$1" --events "$2" "${@:3}"
