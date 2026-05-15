#!/usr/bin/env bash
# Graceful shutdown via SIGTERM. Falls back to SIGKILL after 30s.
set -euo pipefail

cd "$(dirname "$0")/.."
REPO="$(pwd)"
PIDFILE="$REPO/.combined.pid"

if [[ ! -f "$PIDFILE" ]]; then
    echo "No pid file at $PIDFILE — nothing to stop."
    exit 0
fi

PID="$(cat "$PIDFILE")"
if ! ps -p "$PID" > /dev/null 2>&1; then
    echo "Process pid=$PID is not running."
    rm -f "$PIDFILE"
    exit 0
fi

echo "Sending SIGTERM to pid=$PID…"
kill -TERM "$PID" 2>/dev/null || true

# Wait up to 30s for graceful shutdown.
for i in {1..30}; do
    if ! ps -p "$PID" > /dev/null 2>&1; then
        echo "Process exited cleanly after ${i}s."
        rm -f "$PIDFILE"
        exit 0
    fi
    sleep 1
done

echo "Still running after 30s — sending SIGKILL."
kill -KILL "$PID" 2>/dev/null || true
sleep 1
rm -f "$PIDFILE"
echo "Killed."
