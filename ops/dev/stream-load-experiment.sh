#!/usr/bin/env bash
set -euo pipefail

readonly test_host="${QTRAD_TEST_POSTGRES_HOST:?set QTRAD_TEST_POSTGRES_HOST}"
readonly test_port="${QTRAD_TEST_POSTGRES_PORT:-5432}"
readonly test_user="${QTRAD_TEST_POSTGRES_USER:?set QTRAD_TEST_POSTGRES_USER}"
readonly test_password="${QTRAD_TEST_POSTGRES_PASSWORD:?set QTRAD_TEST_POSTGRES_PASSWORD}"
readonly output="${1:?usage: stream-load-experiment.sh OUTPUT [experiment arguments]}"
shift
test_database="qtrad_test_stream_$(date -u +%Y%m%d%H%M%S)_$$"
readonly test_database

if [[ ! "$test_database" =~ ^qtrad_test_stream_[0-9]{14}_[0-9]+$ ]]; then
  echo "refusing unsafe experiment database name" >&2
  exit 1
fi
if [[ "$test_host" != "test-db" && "$test_host" != "127.0.0.1" && "$test_host" != "localhost" ]]; then
  echo "refusing non-local PostgreSQL test host: $test_host" >&2
  exit 1
fi
if [[ -e "$output" ]]; then
  echo "refusing to overwrite experiment evidence: $output" >&2
  exit 1
fi

export PGPASSWORD="$test_password"

admin_psql() {
  psql \
    --host "$test_host" \
    --port "$test_port" \
    --username "$test_user" \
    --dbname postgres \
    --no-psqlrc \
    --set ON_ERROR_STOP=1 \
    "$@"
}

cleanup() {
  local original_status=$?
  trap - EXIT
  if ! admin_psql --command "DROP DATABASE IF EXISTS \"$test_database\" WITH (FORCE)"; then
    echo "failed to remove disposable experiment database $test_database" >&2
    if ((original_status == 0)); then
      exit 1
    fi
  fi
  exit "$original_status"
}
trap cleanup EXIT

admin_psql --command "CREATE DATABASE \"$test_database\""

export QTRAD_TEST_DATABASE_URL="postgresql+asyncpg://${test_user}:${test_password}@${test_host}:${test_port}/${test_database}"
export QTRAD_DATABASE_URL="$QTRAD_TEST_DATABASE_URL"
export QTRAD_MIGRATION_DATABASE_URL="postgresql+psycopg://${test_user}:${test_password}@${test_host}:${test_port}/${test_database}"

uv run alembic upgrade head
uv run ops/dev/stream_load_experiment.py --output "$output" "$@"
