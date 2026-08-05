#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "$0")" && pwd)"

archive="${1:?usage: build-image.sh OFFICIAL_API_ZIP SHA256}"
expected_sha="${2:?usage: build-image.sh OFFICIAL_API_ZIP SHA256}"
base_image="${QTRAD_BASE_IMAGE:?set QTRAD_BASE_IMAGE to an immutable digest}"
gateway_version="${QTRAD_IBKR_GATEWAY_VERSION:-10.49}"
gateway_archive_sha="${QTRAD_IBKR_GATEWAY_ARCHIVE_SHA256:?set QTRAD_IBKR_GATEWAY_ARCHIVE_SHA256 to the private Gateway archive SHA-256}"
api_version="${QTRAD_IBKR_API_VERSION:-10.49}"
source_digest="${QTRAD_SOURCE_DIGEST:?set the source-tree digest}"
app_commit="${QTRAD_APP_COMMIT:?set the application commit}"
build_time="${QTRAD_BUILD_TIME:?set a deterministic OCI build timestamp}"
output_ref="${QTRAD_IBKR_IMAGE:?set QTRAD_IBKR_IMAGE to the output repository or repository@digest}"
build_tag="${QTRAD_IBKR_BUILD_TAG:-ibkr-${app_commit:0:12}-${expected_sha:0:12}}"

[[ -f "$archive" ]]
[[ "$expected_sha" =~ ^[0-9a-f]{64}$ ]]
[[ "$base_image" =~ @sha256:[0-9a-f]{64}$ ]]
[[ "$source_digest" =~ ^[0-9a-f]{64}$ ]]
[[ "$app_commit" =~ ^[0-9a-f]{40,64}$ ]]
[[ "$build_time" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]]
source_epoch="$(date -u -d "$build_time" +%s)"
[[ "$gateway_archive_sha" =~ ^[0-9a-f]{64}$ ]]
[[ "$api_version" =~ ^10[.]((49)|(45))$ ]]
[[ "$gateway_version" == "$api_version" ]]
[[ "$output_ref" != *[[:space:]]* ]]
[[ "$build_tag" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$ ]]
[[ "${QTRAD_IBKR_PUSH:-0}" == 1 ]] || {
    echo "set QTRAD_IBKR_PUSH=1; a registry manifest digest is required for deployment" >&2
    exit 64
}

if [[ "$output_ref" == *@* ]]; then
    [[ "$output_ref" =~ @sha256:[0-9a-f]{64}$ ]] || {
        echo "QTRAD_IBKR_IMAGE digest suffix is invalid" >&2
        exit 64
    }
    repository="${output_ref%@sha256:*}"
else
    repository="$output_ref"
fi
[[ -n "$repository" && "$repository" != *[[:space:]]* && "$repository" != *"@"* ]] || {
    echo "QTRAD_IBKR_IMAGE must be a repository or repository@sha256:digest" >&2
    exit 64
}
actual_sha="$(sha256sum "$archive" | awk '{print $1}')"
[[ "$actual_sha" == "$expected_sha" ]] || {
    echo "official IBKR API archive SHA-256 mismatch" >&2
    exit 1
}

context="$(mktemp -d)"
trap 'rm -rf "$context"' EXIT
cp "$archive" "$context/ibkr-api.zip"
command -v unzip >/dev/null || {
    echo "unzip is required to compute the IBKR API source fingerprint" >&2
    exit 69
}
mkdir -p "$context/api"
unzip -q "$archive" 'IBJts/source/pythonclient/*' -d "$context/api"
api_source_fingerprint="$(bash "$script_dir/source-manifest-fingerprint.sh" "$context/api/IBJts/source/pythonclient")"
[[ "$api_source_fingerprint" =~ ^[0-9a-f]{64}$ ]] || {
    echo "unable to compute the IBKR API source-manifest fingerprint" >&2
    exit 1
}
docker buildx build \
    --file Dockerfile.ibkr \
    --build-context "ibkr-api=$context" \
    --build-arg "QTRAD_BASE_IMAGE=$base_image" \
    --build-arg "IBKR_API_SHA256=$expected_sha" \
    --build-arg "IBKR_API_VERSION=$api_version" \
    --build-arg "IBKR_GATEWAY_EXPECTED_VERSION=$gateway_version" \
    --build-arg "QTRAD_SOURCE_DIGEST=$source_digest" \
    --build-arg "QTRAD_APP_COMMIT=$app_commit" \
    --build-arg "QTRAD_BUILD_TIME=$build_time" \
    --build-arg "SOURCE_DATE_EPOCH=$source_epoch" \
    --build-arg "IBKR_GATEWAY_EXPECTED_ARCHIVE_SHA256=$gateway_archive_sha" \
    --build-arg "IBKR_API_SOURCE_MANIFEST_SHA256=$api_source_fingerprint" \
    --tag "$repository:$build_tag" \
    --push \
    .

manifest_digest="$(docker buildx imagetools inspect "$repository:$build_tag" | awk '$1 == "Digest:" { print $2; exit }')"
[[ "$manifest_digest" =~ ^sha256:[0-9a-f]{64}$ ]] || {
    echo "unable to resolve the pushed image manifest digest" >&2
    exit 1
}
printf 'QTRAD_IBKR_API_PACKAGE_FINGERPRINT=%s\n' "$api_source_fingerprint"
printf '%s@%s\n' "$repository" "$manifest_digest"
