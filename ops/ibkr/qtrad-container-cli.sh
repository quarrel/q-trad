#!/usr/bin/env bash
set -euo pipefail

image="${QTRAD_IBKR_IMAGE:?set QTRAD_IBKR_IMAGE}"
repository_root="${QTRAD_IBKR_REPOSITORY_ROOT:?set QTRAD_IBKR_REPOSITORY_ROOT}"

[[ "$image" =~ @sha256:[0-9a-f]{64}$ ]] || {
    echo "QTRAD_IBKR_IMAGE must be an immutable digest" >&2
    exit 64
}
[[ "$repository_root" == /srv/qtrad/ibkr/* && -d "$repository_root" ]] || {
    echo "reviewed repository root must be beneath /srv/qtrad/ibkr" >&2
    exit 64
}
[[ -d /etc/qtrad && -d /srv/qtrad/ibkr ]] || {
    echo "IBKR release authority roots are unavailable" >&2
    exit 69
}

exec docker run --rm --network none --user 10001:10001 \
    --read-only --cap-drop=ALL --security-opt=no-new-privileges \
    --volume /etc/qtrad:/etc/qtrad:ro \
    --volume /srv/qtrad/ibkr:/srv/qtrad/ibkr:ro \
    --entrypoint uv "$image" \
    run --frozen --no-dev --no-sync python -m qtrad "$@"
