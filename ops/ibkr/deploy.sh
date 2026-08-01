#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
image="${QTRAD_IBKR_IMAGE:?set QTRAD_IBKR_IMAGE to an immutable image digest}"
evidence_root="${QTRAD_IBKR_EVIDENCE_ROOT:-/srv/qtrad/ibkr/evidence}"

[[ "$image" =~ @sha256:[0-9a-f]{64}$ ]] || {
    echo "QTRAD_IBKR_IMAGE must be an immutable digest" >&2
    exit 64
}
install -d -o qtrad -g qtrad -m 0750 "$evidence_root"
QTRAD_IBKR_IMAGE="$image" QTRAD_IBKR_EVIDENCE_ROOT="$evidence_root" \
    "$script_dir/verify-host.sh"

systemctl daemon-reload
systemctl enable --now docker.service qtrad-ibgateway.service
systemctl enable --now qtrad-ibkr-ingest.service
systemctl enable --now qtrad-ibkr-health.timer qtrad-ibkr-diskcheck.timer \
    qtrad-ibkr-backup.timer qtrad-ibkr-restore-verify.timer

systemctl is-active --quiet qtrad-ibgateway.service
systemctl is-active --quiet qtrad-ibkr-ingest.service
curl --fail --silent --show-error --max-time 10 \
    "${QTRAD_IBKR_SYSTEM_URL:-http://127.0.0.1:8000/api/v1/system}" > /dev/null
echo "q-trad IBKR services deployed and system endpoint is responding"
