#!/usr/bin/env bash
set -euo pipefail

readonly endpoint="${QTRAD_READY_URL:-http://127.0.0.1:8000/health/ready}"
readonly metric_namespace="${QTRAD_OCI_METRIC_NAMESPACE:?QTRAD_OCI_METRIC_NAMESPACE is required}"
ready=0
if curl --fail --silent --show-error "$endpoint" > /dev/null; then
  ready=1
fi
oci monitoring metric-data post --metric-data "[{\"namespace\":\"$metric_namespace\",\"compartmentId\":\"${QTRAD_OCI_COMPARTMENT_ID:?QTRAD_OCI_COMPARTMENT_ID is required}\",\"name\":\"collector_ready\",\"datapoints\":[{\"timestamp\":\"$(date --utc +%Y-%m-%dT%H:%M:%SZ)\",\"value\":$ready}]}]"
test "$ready" -eq 1
