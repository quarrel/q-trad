#!/usr/bin/env bash
set -euo pipefail

source_root="${1:?usage: source-manifest-fingerprint.sh IBKR_PYTHONCLIENT_ROOT}"
package_root="${source_root}/ibapi"
[[ -d "$package_root" ]] || {
    echo "IBKR Python API package directory is missing: $package_root" >&2
    exit 1
}

manifest="$(mktemp)"
trap 'rm -f "$manifest"' EXIT

while IFS= read -r -d '' file; do
    relative="${file#"$source_root"/}"
    basename="${relative##*/}"
    case "$relative" in
        */__pycache__/*|*.pyc|*.pyo|*.dist-info/*|*.egg-info/*)
            continue
            ;;
    esac
    case "$basename" in
        RECORD|METADATA|WHEEL|INSTALLER|direct_url.json|top_level.txt|*.pyproj)
            continue
            ;;
    esac
    printf '%s' "$relative" >>"$manifest"
    cat "$file" >>"$manifest"
done < <(find "$package_root" -type f -print0 | sort -z)

sha256sum "$manifest" | awk '{print $1}'
