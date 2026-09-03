#!/bin/sh
set -eu

control_directory="$HOME/.codex/app-server-control"
control_socket="$control_directory/app-server-control.sock"

if codex remote-control start; then
    exit 0
fi

if [ ! -S "$control_socket" ]; then
    if ! inotifywait \
        --quiet \
        --timeout 60 \
        --event create,moved_to \
        --include 'app-server-control\.sock$' \
        "$control_directory" \
        >/dev/null; then
        if [ ! -S "$control_socket" ]; then
            echo "remote control did not become ready within 60 seconds" >&2
            exit 1
        fi
    fi
fi

# The first invocation can time out while leaving a healthy daemon starting.
# Retry to make Codex verify that the daemon behind the new socket is ready.
codex remote-control start
