#!/usr/bin/env bash
# Start the combined collector + paper engine in the background.
# Usage: ./scripts/start_all.sh
set -euo pipefail

cd "$(dirname "$0")/.."
REPO="$(pwd)"
PIDFILE="$REPO/.combined.pid"
WRAPPER="$REPO/scripts/respawn_loop.sh"
# Rotated structlog output (RotatingFileHandler in logging_config.py).
ROT_LOG="$REPO/logs/combined.log"
# Raw stdout/stderr from the wrapper (unbuffered; useful for crash trails).
CONSOLE_LOG="$REPO/logs/combined.console.log"

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

echo "Starting combined collector + paper trader via respawn wrapper…"
nohup "$WRAPPER" >> "$CONSOLE_LOG" 2>&1 &
PID=$!
echo "$PID" > "$PIDFILE"
echo "Started wrapper pid=$PID."
echo "  Rotated structlog: $ROT_LOG"
echo "  Raw console:       $CONSOLE_LOG"
echo "  Respawn events:    $REPO/logs/respawn.log"

sleep 3
if ! ps -p "$PID" > /dev/null 2>&1; then
    echo "Wrapper died within 3s. Check logs:"
    tail -30 "$CONSOLE_LOG"
    rm -f "$PIDFILE"
    exit 1
fi

echo "Running. tail -f $ROT_LOG  to follow."
