#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
image="${QTRAD_IBKR_IMAGE:?set QTRAD_IBKR_IMAGE to an immutable image digest}"
evidence_root="${QTRAD_IBKR_EVIDENCE_ROOT:-/srv/qtrad/ibkr/evidence}"
checkpoint_root="${QTRAD_IBKR_CHECKPOINT_ROOT:?set QTRAD_IBKR_CHECKPOINT_ROOT to a persistent host path}"
api_fingerprint="${QTRAD_IBKR_API_PACKAGE_FINGERPRINT:?set QTRAD_IBKR_API_PACKAGE_FINGERPRINT}"
gateway_version="${QTRAD_IBKR_GATEWAY_VERSION:-10.49}"
gateway_archive_sha="${QTRAD_IBKR_GATEWAY_ARCHIVE_SHA256:?set QTRAD_IBKR_GATEWAY_ARCHIVE_SHA256}"
gateway_manifest="${QTRAD_IBKR_GATEWAY_MANIFEST:?set QTRAD_IBKR_GATEWAY_MANIFEST}"

[[ "$image" =~ @sha256:[0-9a-f]{64}$ ]] || {
    echo "QTRAD_IBKR_IMAGE must be an immutable digest" >&2
    exit 64
}
docker pull "$image"
[[ "$checkpoint_root" == /* ]] || {
    echo "QTRAD_IBKR_CHECKPOINT_ROOT must be an absolute host path" >&2
    exit 64
}
[[ "$api_fingerprint" =~ ^[0-9a-f]{64}$ ]] || {
    echo "QTRAD_IBKR_API_PACKAGE_FINGERPRINT must be a 64-character lowercase SHA-256 digest" >&2
    exit 64
}
install -d -o 10001 -g 10001 -m 0750 "$evidence_root" "$checkpoint_root"
QTRAD_IBKR_IMAGE="$image" \
QTRAD_IBKR_EVIDENCE_ROOT="$evidence_root" \
QTRAD_IBKR_CHECKPOINT_ROOT="$checkpoint_root" \
QTRAD_IBKR_API_PACKAGE_FINGERPRINT="$api_fingerprint" \
QTRAD_IBKR_API_VERSION="${QTRAD_IBKR_API_VERSION:-10.49}" \
QTRAD_IBKR_GATEWAY_VERSION="$gateway_version" \
QTRAD_IBKR_GATEWAY_ARCHIVE_SHA256="$gateway_archive_sha" \
QTRAD_IBKR_GATEWAY_MANIFEST="$gateway_manifest" \
    "$script_dir/verify-host.sh"

cat >&2 <<'EOF'
IBKR host invariants verified. Continuous IBKR ingest is not implemented yet;
no Gateway, ingest or operator-API service was started. Use the explicit bounded
`qtrad instruments review --provider ibkr --execute-account-probe` command for
capability evidence, with the persistent checkpoint path above.
EOF
