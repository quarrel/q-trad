#!/usr/bin/env bash
set -euo pipefail

archive="${1:?usage: build-image.sh OFFICIAL_API_ZIP SHA256}"
expected_sha="${2:?usage: build-image.sh OFFICIAL_API_ZIP SHA256}"
base_image="${QTRAD_BASE_IMAGE:?set QTRAD_BASE_IMAGE to an immutable digest}"
gateway_version="${QTRAD_IBKR_GATEWAY_VERSION:-10.49}"
api_version="${QTRAD_IBKR_API_VERSION:-10.49}"
source_digest="${QTRAD_SOURCE_DIGEST:?set the source-tree digest}"
app_commit="${QTRAD_APP_COMMIT:?set the application commit}"
build_time="${QTRAD_BUILD_TIME:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"
image="${QTRAD_IBKR_IMAGE:?set an output image reference}"

[[ -f "$archive" ]]
[[ "$expected_sha" =~ ^[0-9a-f]{64}$ ]]
[[ "$base_image" =~ @sha256:[0-9a-f]{64}$ ]]
[[ "$image" =~ @sha256:[0-9a-f]{64}$ ]]
[[ "$source_digest" =~ ^[0-9a-f]{64}$ ]]
[[ "$app_commit" =~ ^[0-9a-f]{40,64}$ ]]
[[ "$api_version" =~ ^10[.]((49)|(45))$ ]]
[[ "$gateway_version" == "$api_version" ]]
actual_sha="$(sha256sum "$archive" | awk '{print $1}')"
[[ "$actual_sha" == "$expected_sha" ]] || {
    echo "official IBKR API archive SHA-256 mismatch" >&2
    exit 1
}

context="$(mktemp -d)"
trap 'rm -rf "$context"' EXIT
cp "$archive" "$context/ibkr-api.zip"
docker buildx build \
    --file Dockerfile.ibkr \
    --build-context "ibkr-api=$context" \
    --build-arg "QTRAD_BASE_IMAGE=$base_image" \
    --build-arg "IBKR_API_SHA256=$expected_sha" \
    --build-arg "IBKR_API_VERSION=$api_version" \
    --build-arg "IBKR_GATEWAY_VERSION=$gateway_version" \
    --build-arg "QTRAD_SOURCE_DIGEST=$source_digest" \
    --build-arg "QTRAD_APP_COMMIT=$app_commit" \
    --build-arg "QTRAD_BUILD_TIME=$build_time" \
    --tag "$image" \
    --load \
    .
