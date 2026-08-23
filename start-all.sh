#!/bin/bash
set -Eeuo pipefail

cd "$(dirname "$0")"

# Hold the lock across exec so a restart race or a second launcher cannot run
# duplicate web servers. Use the per-user runtime directory when available.
lock_file="${XDG_RUNTIME_DIR:-/tmp}/claude-server.lock"
exec 9>"$lock_file"
if ! flock -n 9; then
    echo "claude-server is already running; refusing a duplicate instance" >&2
    exit 0
fi

exec ./venv/bin/python server.py
