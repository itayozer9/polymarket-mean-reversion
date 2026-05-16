#!/usr/bin/env bash
# Respawn wrapper for the combined collector + paper engine.
#
# Goals:
#   - 24/7 unattended operation: if the inner Python process crashes, bring it back.
#   - Detect runaway crash loops (5 crashes in 60s) and bail rather than hammer.
#   - Stop gracefully on SIGTERM (forwarded to inner process) — stop_all.sh works.
#   - Stop if data/KILL appears.
#   - Cap total respawns at 100 to bound damage from a chronic bug.
#
# This script holds the .combined.pid file; the inner Python pid is tracked
# separately. On stop, sending SIGTERM to THIS process is what stop_all.sh does;
# we forward it to the child, wait for clean exit, then exit ourselves.
set -uo pipefail

cd "$(dirname "$0")/.."
REPO="$(pwd)"
LOGFILE="$REPO/logs/combined.console.log"
RESPAWN_LOG="$REPO/logs/respawn.log"
KILL_FILE="$REPO/data/KILL"

mkdir -p "$REPO/logs" "$REPO/data"

MAX_RESPAWNS=100
WINDOW_SEC=60
WINDOW_FAILS_CAP=5

respawn_count=0
recent_fails=()  # epoch seconds of recent crashes

CHILD_PID=""
SHUTDOWN_REQUESTED=0

log_event() {
    local msg="$1"
    local ts
    ts="$(date -u +%FT%TZ)"
    echo "$ts $msg" | tee -a "$RESPAWN_LOG" >&2
}

forward_signal() {
    SHUTDOWN_REQUESTED=1
    if [[ -n "$CHILD_PID" ]] && ps -p "$CHILD_PID" > /dev/null 2>&1; then
        log_event "respawn_loop_received_signal pid=$CHILD_PID forwarding=SIGTERM"
        kill -TERM "$CHILD_PID" 2>/dev/null || true
    fi
}

trap forward_signal SIGTERM SIGINT

log_event "respawn_loop_started repo=$REPO"

while (( respawn_count < MAX_RESPAWNS )); do
    if [[ -f "$KILL_FILE" ]]; then
        log_event "respawn_loop_kill_sentinel_seen path=$KILL_FILE bailing"
        break
    fi
    if (( SHUTDOWN_REQUESTED )); then
        log_event "respawn_loop_shutdown_requested bailing"
        break
    fi

    log_event "respawn_loop_spawn n=$respawn_count"

    # Invoke the venv Python directly so CHILD_PID is the actual Python process.
    # Previously we used `uv run python ...`, but uv-run can sit between us and
    # Python and not always forward SIGTERM cleanly — that left grandchild
    # Python processes orphaned after stop_all.sh, producing duplicate bots.
    .venv/bin/python -m mean_reversion_live.scripts.run_combined >> "$LOGFILE" 2>&1 &
    CHILD_PID=$!
    log_event "respawn_loop_child_started pid=$CHILD_PID"

    wait "$CHILD_PID"
    rc=$?
    CHILD_PID=""

    if (( SHUTDOWN_REQUESTED )); then
        log_event "respawn_loop_child_exited rc=$rc reason=shutdown"
        break
    fi

    log_event "respawn_loop_child_exited rc=$rc"

    # Sentinel takes precedence — don't respawn if user touched data/KILL.
    if [[ -f "$KILL_FILE" ]]; then
        log_event "respawn_loop_kill_sentinel_after_exit path=$KILL_FILE bailing"
        break
    fi

    # Track recent failures.
    now_epoch=$(date +%s)
    recent_fails+=("$now_epoch")
    # Drop entries older than WINDOW_SEC.
    pruned=()
    for ts in "${recent_fails[@]}"; do
        if (( now_epoch - ts <= WINDOW_SEC )); then
            pruned+=("$ts")
        fi
    done
    recent_fails=("${pruned[@]}")

    if (( ${#recent_fails[@]} >= WINDOW_FAILS_CAP )); then
        log_event "respawn_loop_crash_loop_detected fails_in_${WINDOW_SEC}s=${#recent_fails[@]} bailing"
        break
    fi

    respawn_count=$((respawn_count + 1))
    log_event "respawn_loop_sleep 5s before respawn (total=$respawn_count)"
    sleep 5
done

if (( respawn_count >= MAX_RESPAWNS )); then
    log_event "respawn_loop_max_respawns_reached cap=$MAX_RESPAWNS"
fi

log_event "respawn_loop_exiting"
