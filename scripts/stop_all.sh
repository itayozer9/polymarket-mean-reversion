#!/usr/bin/env bash
# Graceful shutdown via SIGTERM. Falls back to SIGKILL after 30s.
# Dropped `set -u`: bash `for i in {1..30}` can interact oddly with strict mode
# on some shells / terminals.
set -eo pipefail

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
exited_cleanly=0
for i in {1..30}; do
    if ! ps -p "$PID" > /dev/null 2>&1; then
        echo "Wrapper exited cleanly after ${i}s."
        exited_cleanly=1
        break
    fi
    sleep 1
done

if [[ "$exited_cleanly" != "1" ]]; then
    echo "Still running after 30s — sending SIGKILL."
    kill -KILL "$PID" 2>/dev/null || true
    sleep 1
fi

rm -f "$PIDFILE"

# Belt-and-suspenders: ALWAYS sweep any orphan run_combined Python processes
# that escaped the wrapper's signal forwarding. This caught a real bug where
# two bots ran in parallel because a `uv run` middleman didn't propagate
# SIGTERM to its Python child during an earlier stop.
ORPHANS="$(pgrep -f 'mean_reversion_live.scripts.run_combined' 2>/dev/null || true)"
if [[ -n "$ORPHANS" ]]; then
    echo "Orphan run_combined processes found: $ORPHANS — SIGKILLing."
    kill -KILL $ORPHANS 2>/dev/null || true
    sleep 1
fi
echo "Done."
