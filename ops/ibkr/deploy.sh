#!/usr/bin/env bash
set -euo pipefail

usage() {
    echo "usage: deploy.sh --check|--apply" >&2
    exit 64
}

mode="${1:-}"
[[ "$mode" == "--check" || "$mode" == "--apply" ]] || usage
[[ "${2:-}" == "" ]] || usage

script_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
canonical_env_file="/etc/qtrad/ibkr-ingest.env"
if [[ -n "${QTRAD_IBKR_ENV_FILE:-}" && "$QTRAD_IBKR_ENV_FILE" != "$canonical_env_file" ]]; then
    echo "IBKR B3 preflight: QTRAD_IBKR_ENV_FILE must use canonical $canonical_env_file" >&2
    exit 64
fi
env_file="$canonical_env_file"
backup_env_file="/etc/qtrad/ibkr-backup.env"
if [[ -f "$env_file" ]]; then
    set -a
    # shellcheck disable=SC1090
    . "$env_file"
    set +a
fi

image="${QTRAD_IBKR_IMAGE:?set QTRAD_IBKR_IMAGE}"
descriptor="${QTRAD_IBKR_RELEASE_DESCRIPTOR:?set QTRAD_IBKR_RELEASE_DESCRIPTOR}"
repository_root="${QTRAD_IBKR_REPOSITORY_ROOT:-$script_dir/../..}"
preflight_bin="${QTRAD_B3_PREFLIGHT_BIN:-}"
release_policy="${QTRAD_IBKR_RELEASE_POLICY:-b3-exact-two}"
checkpoint_root="${QTRAD_IBKR_CHECKPOINT_ROOT:?set QTRAD_IBKR_CHECKPOINT_ROOT}"
api_fingerprint="${QTRAD_IBKR_API_PACKAGE_FINGERPRINT:?set QTRAD_IBKR_API_PACKAGE_FINGERPRINT}"
gateway_archive_sha="${QTRAD_IBKR_GATEWAY_ARCHIVE_SHA256:?set QTRAD_IBKR_GATEWAY_ARCHIVE_SHA256}"
gateway_manifest="${QTRAD_IBKR_GATEWAY_MANIFEST:?set QTRAD_IBKR_GATEWAY_MANIFEST}"
ibc_version="${QTRAD_IBKR_IBC_VERSION:-3.24.1}"
api_version="${QTRAD_IBKR_API_VERSION:-10.49}"
gateway_version="${QTRAD_IBKR_GATEWAY_VERSION:-10.49}"
gateway_host="${QTRAD_IBKR_GATEWAY_HOST:-127.0.0.1}"
gateway_port="${QTRAD_IBKR_GATEWAY_PORT:-4002}"
api_host="${QTRAD_IBKR_API_HOST:-127.0.0.1}"
api_port="${QTRAD_IBKR_API_PORT:-8000}"
client_id="${QTRAD_IBKR_CLIENT_ID:-71}"
historical_client_id="${QTRAD_IBKR_HISTORICAL_CLIENT_ID:-}"
database_name="${QTRAD_IBKR_DATABASE_NAME:-qtrad_ibkr}"
configuration_hash="${QTRAD_IBKR_CAPTURE_CONFIGURATION_HASH:?set QTRAD_IBKR_CAPTURE_CONFIGURATION_HASH}"
capture_configuration_path="${QTRAD_IBKR_CAPTURE_CONFIGURATION_PATH:?set QTRAD_IBKR_CAPTURE_CONFIGURATION_PATH}"
database_url="${QTRAD_DATABASE_URL:?set QTRAD_DATABASE_URL}"
base_image="${QTRAD_IMAGE:-$image}"

fail() { echo "IBKR B3 preflight: $*" >&2; exit 64; }

declare -a image_retention_remove_references=()

plan_image_retention() {
    local image_repository deployed_image_id container_output container_id referenced_image_id
    local image_rows repository digest image_id rollback_image_id references
    local deployed_seen=0
    local -a repository_image_ids=()
    local -A protected_image_ids=()
    local -A repository_image_seen=()
    local -A repository_references=()

    image_retention_remove_references=()
    image_repository="${image%@sha256:*}"
    deployed_image_id="$(docker image inspect "$image" --format '{{.Id}}')" \
        || fail "deployed image identity cannot be resolved"
    [[ "$deployed_image_id" =~ ^sha256:[0-9a-f]{64}$ ]] \
        || fail "deployed image ID is invalid"
    protected_image_ids["$deployed_image_id"]=1

    container_output="$(docker container ls -aq)" \
        || fail "container image references cannot be listed"
    while IFS= read -r container_id; do
        [[ -n "$container_id" ]] || continue
        referenced_image_id="$(
            docker container inspect --format '{{.Image}}' "$container_id"
        )" || fail "container image reference cannot be resolved: $container_id"
        [[ "$referenced_image_id" =~ ^sha256:[0-9a-f]{64}$ ]] \
            || fail "container image ID is invalid: $container_id"
        protected_image_ids["$referenced_image_id"]=1
    done <<<"$container_output"

    image_rows="$(
        docker image ls --digests --no-trunc \
            --format '{{.Repository}}|{{.Digest}}|{{.ID}}' "$image_repository"
    )" || fail "IBKR repository images cannot be listed"
    while IFS='|' read -r repository digest image_id; do
        [[ -n "$repository" ]] || continue
        [[ "$repository" == "$image_repository" ]] || continue
        [[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]] \
            || fail "IBKR repository returned an invalid image ID"
        if [[ -z "${repository_image_seen[$image_id]:-}" ]]; then
            repository_image_ids+=("$image_id")
            repository_image_seen["$image_id"]=1
        fi
        if [[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]]; then
            repository_references["$image_id"]+="$repository@$digest"$'\n'
        fi
        if [[ "$image_id" == "$deployed_image_id" ]]; then
            deployed_seen=1
        fi
    done <<<"$image_rows"
    ((deployed_seen == 1)) || fail "deployed image is absent from its IBKR repository listing"

    rollback_image_id=""
    for image_id in "${repository_image_ids[@]}"; do
        if [[ -n "${protected_image_ids[$image_id]:-}" ]]; then
            continue
        fi
        rollback_image_id="$image_id"
        break
    done

    printf 'IBKR image retention plan: repository=%s\n' "$image_repository"
    for image_id in "${repository_image_ids[@]}"; do
        if [[ "$image_id" == "$deployed_image_id" ]]; then
            printf 'keep %s reason=deployed\n' "$image_id"
            continue
        fi
        if [[ -n "${protected_image_ids[$image_id]:-}" ]]; then
            printf 'keep %s reason=container-referenced\n' "$image_id"
            continue
        fi
        if [[ "$image_id" == "$rollback_image_id" ]]; then
            printf 'keep %s reason=most-recent-unreferenced-rollback\n' "$image_id"
            continue
        fi
        references="${repository_references[$image_id]:-}"
        if [[ -z "$references" ]]; then
            printf 'keep %s reason=no-immutable-repository-reference\n' "$image_id"
            continue
        fi
        while IFS= read -r reference; do
            [[ -n "$reference" ]] || continue
            image_retention_remove_references+=("$reference")
            printf 'remove %s reference=%s\n' "$image_id" "$reference"
        done <<<"$references"
    done
}

apply_image_retention() {
    if (("${#image_retention_remove_references[@]}" == 0)); then
        return
    fi
    docker image rm "${image_retention_remove_references[@]}" >/dev/null
}

verify_backup_identity() {
    [[ -f "$backup_env_file" ]] || fail "canonical backup environment is missing: $backup_env_file"
    [[ "$(stat -c '%U:%G' "$backup_env_file")" == root:root ]] \
        || fail "canonical backup environment must be root-owned"
    backup_mode="$(stat -c '%a' "$backup_env_file")"
    [[ "$backup_mode" == 600 || "$backup_mode" == 640 ]] \
        || fail "canonical backup environment must use mode 0600 or 0640"
    (
        set -a
        # shellcheck disable=SC1090
        . "$backup_env_file"
        set +a
        [[ "${QTRAD_IBKR_BACKUP_DIR:-}" == /srv/qtrad/postgres/backups \
            && "${QTRAD_IBKR_STATUS_DIR:-}" == /var/lib/qtrad/ibkr \
            && "${QTRAD_IBKR_POSTGRES_CONTAINER:-}" == qtrad-ibkr-native-postgres \
            && "${QTRAD_IBKR_POSTGRES_DATABASE:-}" == qtrad_ibkr \
            && "${QTRAD_IBKR_POSTGRES_USER:-}" == qtrad_ibkr \
            && "${QTRAD_IBKR_BACKUP_RETENTION_DAYS:-}" =~ ^[1-9][0-9]*$ \
            && "${QTRAD_IBKR_RUNTIME_GID:-}" == 10001 ]] \
            || fail "canonical backup environment has unexpected identities"
        [[ -z "${QTRAD_IBKR_RESTORE_DATABASE:-}" ]] \
            || fail "restore database identity must be generated per verification run"
    )
}

verify_checkout_identity() {
    [[ "$application_commit" =~ ^[0-9a-f]{40}$ ]] || fail "application commit is invalid"
    checkout_commit="$(git -C "$repository_root" rev-parse --verify HEAD 2>/dev/null)" || fail "checkout commit cannot be resolved"
    [[ "$checkout_commit" == "$application_commit" ]] || fail "checkout commit does not match the reviewed descriptor"
    [[ -z "$(git -C "$repository_root" status --porcelain --untracked-files=all)" ]] || fail "reviewed checkout is dirty"
}

verify_host_identity() {
    QTRAD_IBKR_IMAGE="$image" \
        QTRAD_IBKR_APPLICATION_COMMIT="$application_commit" \
        QTRAD_IBKR_API_VERSION="$api_version" \
        QTRAD_IBKR_GATEWAY_VERSION="$gateway_version" \
        QTRAD_IBKR_GATEWAY_ARCHIVE_SHA256="$gateway_archive_sha" \
        QTRAD_IBKR_API_PACKAGE_FINGERPRINT="$api_fingerprint" \
        QTRAD_IBKR_CHECKPOINT_ROOT="$checkpoint_root" \
        bash "$script_dir/verify-host.sh"
}

verify_database_head() {
    docker run --rm --network host --user 10001:10001 \
        --read-only --cap-drop=ALL --security-opt=no-new-privileges \
        --env-file "$env_file" --entrypoint /app/.venv/bin/python "$image" \
        -m qtrad db verify-head
}

[[ "$image" =~ @sha256:[0-9a-f]{64}$ ]] || fail "image must be immutable"
[[ "$base_image" == "$image" ]] || fail "application and IBKR image identities differ"
[[ "$api_fingerprint" =~ ^[0-9a-f]{64}$ ]] || fail "API fingerprint is invalid"
[[ "$gateway_archive_sha" =~ ^[0-9a-f]{64}$ ]] || fail "Gateway archive hash is invalid"
[[ "$configuration_hash" =~ ^[0-9a-f]{64}$ ]] || fail "configuration hash is invalid"
[[ "$ibc_version" == 3.24.1 ]] || fail "IBC version is not the reviewed version"
[[ "$capture_configuration_path" == /* ]] || fail "capture configuration path must be absolute"
[[ "$database_url" == postgresql+asyncpg://qtrad_ibkr@127.0.0.1:5432/qtrad_ibkr ]] || fail "database URL must be dedicated and credential-free"
[[ "$checkpoint_root" == /* ]] || fail "checkpoint root must be absolute"
[[ "$gateway_host" == 127.0.0.1 || "$gateway_host" == ::1 ]] || fail "Gateway must be loopback"
[[ "$api_host" == 127.0.0.1 ]] || fail "API must use the reviewed runtime host"
[[ "$gateway_port" == 4002 && "$api_port" == 8000 ]] || fail "unexpected private ports"
[[ "$client_id" =~ ^[1-9][0-9]*$ ]] || fail "client ID must be positive"
[[ "$release_policy" == b3-exact-two || "$release_policy" == b4-exact-six ]] || fail "release policy is invalid"
[[ "$database_name" == qtrad_ibkr ]] || fail "database must be dedicated qtrad_ibkr"
[[ "$api_version" == "$gateway_version" && ( "$api_version" == 10.49 || "$api_version" == 10.45 ) ]] || fail "API/Gateway versions mismatch"
[[ -r "$descriptor" ]] || fail "release descriptor is not readable"
[[ -r "$gateway_manifest" ]] || fail "Gateway identity manifest is not readable"
[[ -d "$repository_root" ]] || fail "repository root is not readable"
if [[ -n "$preflight_bin" ]]; then
    command -v "$preflight_bin" >/dev/null || fail "qtrad offline preflight command is unavailable"
else
    [[ -r "$script_dir/qtrad-container-cli.sh" ]] || fail "reviewed container CLI wrapper is unavailable"
fi
[[ -r "$script_dir/qtrad-ibkr-qualification-wrapper.example" ]] \
    || fail "reviewed qualification wrapper is unavailable"
[[ -r "$script_dir/qtrad-ibkr-dual-restore-qualification.example" ]] \
    || fail "reviewed dual-restore qualification wrapper is unavailable"
command -v jq >/dev/null || fail "jq is required to compare release identities"
command -v git >/dev/null || fail "git is required to authenticate the reviewed checkout"

preflight_args=(
    deployment ibkr-preflight
    --policy "$release_policy" --descriptor "$descriptor"
    --repository-root "$repository_root"
    --observed-at "${QTRAD_IBKR_PREFLIGHT_OBSERVED_AT:?set reviewed UTC preflight timestamp}"
)
if [[ -n "$preflight_bin" ]]; then
    preflight_json="$("$preflight_bin" "${preflight_args[@]}")"
else
    preflight_json="$(
        QTRAD_IBKR_IMAGE="$image" \
            QTRAD_IBKR_REPOSITORY_ROOT="$repository_root" \
            bash "$script_dir/qtrad-container-cli.sh" "${preflight_args[@]}"
    )"
fi
application_commit="$(jq -er '.application_commit' <<<"$preflight_json")"
printf '%s\n' "$preflight_json" | jq -e \
    --arg image "$image" \
    --arg configuration_hash "$configuration_hash" \
    --arg configuration_path "$capture_configuration_path" \
    --arg api_fingerprint "$api_fingerprint" \
    --arg gateway_archive_sha "$gateway_archive_sha" \
    --arg api_version "$api_version" \
    --arg ibc_version "$ibc_version" \
    --arg database_name "$database_name" \
    --arg database_url_environment "QTRAD_DATABASE_URL" \
    --arg gateway_host "$gateway_host" \
    --arg api_host "$api_host" \
    --arg gateway_port "$gateway_port" \
    --arg api_port "$api_port" \
    --arg client_id "$client_id" \
    '.valid == true
     and .operational_ready == true
     and .requires_evidence_refresh == false
     and .image == $image
     and .configuration_hash == $configuration_hash
     and .configuration_path == $configuration_path
     and .api_package_fingerprint == $api_fingerprint
     and .gateway_archive_sha256 == $gateway_archive_sha
     and .api_version == $api_version
     and .gateway_version == $api_version
     and .ibc_version == $ibc_version
     and .database_name == $database_name
     and .database_url_environment == $database_url_environment
     and .gateway_host == $gateway_host
     and .api_host == $api_host
     and .gateway_port == ($gateway_port|tonumber)
     and .api_port == ($api_port|tonumber)
     and .client_id == ($client_id|tonumber)
     and .source == "ibkr-paper-v1"
     and .universe == "capture-ibkr-v1"' >/dev/null || fail "release identity does not match the reviewed descriptor"

[[ "$historical_client_id" =~ ^[1-9][0-9]*$ ]] || fail "historical client ID must be positive"
[[ "$historical_client_id" != "$client_id" ]] || fail "historical client ID must differ from capture client ID"

[[ -r "$env_file" ]] || fail "canonical IBKR ingest environment file is not readable"

verify_backup_identity
verify_checkout_identity
verify_host_identity
bash "$script_dir/postgres-provision.sh" --check
verify_database_head

if [[ "$mode" == "--check" ]]; then
    plan_image_retention
    echo "IBKR B3 preflight passed; no host mutation performed"
    exit 0
fi

# Database schema changes require the explicit, migration-only qtrad db migrate command.
install -d -o 10001 -g 10001 -m 0750 "$checkpoint_root"

install -D -m 0750 "$script_dir/qtrad-ibkr-ingest-wrapper.example" /usr/local/sbin/qtrad-ibkr-ingest
install -D -m 0750 "$script_dir/qtrad-ibkr-api-wrapper.example" /usr/local/sbin/qtrad-ibkr-api
install -D -m 0750 "$script_dir/healthcheck.sh" /usr/local/sbin/qtrad-ibkr-healthcheck
install -D -m 0750 "$script_dir/postgres-backup.sh" /usr/local/sbin/qtrad-ibkr-postgres-backup
install -D -m 0750 "$script_dir/postgres-restore-verify.sh" /usr/local/sbin/qtrad-ibkr-postgres-restore-verify
install -D -m 0750 "$script_dir/qtrad-ibkr-qualification-wrapper.example" /usr/local/sbin/qtrad-ibkr-qualification
install -D -m 0750 "$script_dir/qtrad-ibkr-dual-restore-qualification.example" /usr/local/sbin/qtrad-ibkr-dual-restore-qualification
install -D -m 0750 "$script_dir/postgres-start.sh" /usr/local/sbin/qtrad-ibkr-postgres-start
install -D -m 0750 "$script_dir/postgres-ready.sh" /usr/local/sbin/qtrad-ibkr-postgres-ready
install -D -m 0750 "$script_dir/postgres-stop.sh" /usr/local/sbin/qtrad-ibkr-postgres-stop
install -D -m 0644 "$script_dir/qtrad-ibkr-ingest.service.example" /etc/systemd/system/qtrad-ibkr-ingest.service
install -D -m 0644 "$script_dir/qtrad-ibkr-api.service.example" /etc/systemd/system/qtrad-ibkr-api.service
install -D -m 0644 "$script_dir/qtrad-ibkr-health.service.example" /etc/systemd/system/qtrad-ibkr-health.service
install -D -m 0644 "$script_dir/qtrad-ibkr-health.timer.example" /etc/systemd/system/qtrad-ibkr-health.timer
install -D -m 0644 "$script_dir/qtrad-ibkr-backup.service.example" /etc/systemd/system/qtrad-ibkr-backup.service
install -D -m 0644 "$script_dir/qtrad-ibkr-backup.timer.example" /etc/systemd/system/qtrad-ibkr-backup.timer
install -D -m 0644 "$script_dir/qtrad-ibkr-postgres.service.example" /etc/systemd/system/qtrad-ibkr-postgres.service
systemctl daemon-reload
systemctl enable \
    qtrad-ibkr-postgres.service qtrad-ibkr-api.service qtrad-ibkr-ingest.service \
    qtrad-ibkr-health.timer qtrad-ibkr-backup.timer
systemctl restart qtrad-ibkr-api.service qtrad-ibkr-ingest.service
systemctl restart qtrad-ibkr-health.timer qtrad-ibkr-backup.timer
plan_image_retention
apply_image_retention
