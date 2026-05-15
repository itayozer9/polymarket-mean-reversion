#!/usr/bin/env bash
# Start the combined collector + paper engine in the background.
# Usage: ./scripts/start_all.sh
set -euo pipefail

cd "$(dirname "$0")/.."
REPO="$(pwd)"
PIDFILE="$REPO/.combined.pid"
LOGFILE="$REPO/logs/combined.log"

mkdir -p "$REPO/logs"

if [[ -f "$PIDFILE" ]]; then
    PID="$(cat "$PIDFILE" 2>/dev/null || true)"
    if [[ -n "$PID" ]] && ps -p "$PID" > /dev/null 2>&1; then
        echo "Already running (pid=$PID). Run scripts/stop_all.sh first."
        exit 1
    else
        rm -f "$PIDFILE"
    fi
fi

# Clear any KILL sentinel from a previous shutdown
rm -f "$REPO/data/KILL"

echo "Starting combined collector + paper trader…"
nohup uv run python -m mean_reversion_live.scripts.run_combined >> "$LOGFILE" 2>&1 &
PID=$!
echo "$PID" > "$PIDFILE"
echo "Started with pid=$PID. Logs at: $LOGFILE"

sleep 3
if ! ps -p "$PID" > /dev/null 2>&1; then
    echo "Process died within 3s. Check logs:"
    tail -30 "$LOGFILE"
    rm -f "$PIDFILE"
    exit 1
fi

echo "Running. tail -f $LOGFILE  to follow."
