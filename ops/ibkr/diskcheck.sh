#!/usr/bin/env bash
set -euo pipefail

root_path="${QTRAD_IBKR_ROOT_PATH:-/}"
postgres_path="${QTRAD_IBKR_POSTGRES_PATH:-/srv/qtrad/postgres}"
minimum_percent="${QTRAD_IBKR_MIN_FREE_PERCENT:-15}"

[[ "$minimum_percent" =~ ^[1-9][0-9]?$|^100$ ]] || {
    echo "QTRAD_IBKR_MIN_FREE_PERCENT must be between 1 and 100" >&2
    exit 64
}

for path in "$root_path" "$postgres_path"; do
    read -r available_percent _ < <(df -P --output=pcent "$path" | tail -n 1)
    available_percent="${available_percent%%%}"
    [[ "$available_percent" =~ ^[0-9]+$ ]] || {
        echo "could not determine disk usage for $path" >&2
        exit 69
    }
    free_percent=$((100 - available_percent))
    if ((free_percent < minimum_percent)); then
        logger -p alert -t qtrad-ibkr-disk "low free space on $path: ${free_percent}% free"
        exit 75
    fi
done
