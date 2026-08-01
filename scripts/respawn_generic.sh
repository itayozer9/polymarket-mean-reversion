#!/usr/bin/env bash
# Generic respawn wrapper — keeps a long-running command alive 24/7, independent
# of any Claude session.  Usage:
#   respawn_generic.sh <name> <stop_sentinel_path> <cmd...>
#
# Restarts <cmd> if it exits. Stops (no respawn) on SIGTERM, on data/KILL, or when
# <stop_sentinel_path> appears. Crash-loop guard: >=5 exits in 60s -> bail (don't
# hammer). Caps total respawns at 100. Logs to logs/respawn_<name>.log and pipes the
# child's stdout/stderr to logs/<name>.log.
set -uo pipefail

cd "$(dirname "$0")/.."
REPO="$(pwd)"
NAME="${1:?name required}"
STOP_SENTINEL="${2:?stop sentinel path required}"
shift 2
KILL_FILE="$REPO/data/KILL"
RLOG="$REPO/logs/respawn_${NAME}.log"
CLOG="$REPO/logs/${NAME}.log"
mkdir -p "$REPO/logs"

MAX_RESPAWNS=100
WINDOW_SEC=60
WINDOW_FAILS_CAP=5

CHILD_PID=""
SHUTDOWN=0
fails_in_window=0
window_start=$(date +%s)
respawn_count=0

log() { echo "$(date -u +%FT%TZ) ${NAME} $1" | tee -a "$RLOG" >&2; }
on_sig() {
    SHUTDOWN=1
    [ -n "$CHILD_PID" ] && kill -TERM "$CHILD_PID" 2>/dev/null || true
}
trap on_sig SIGTERM SIGINT

log "respawn_start cmd=$*"
while [ "$respawn_count" -lt "$MAX_RESPAWNS" ]; do
    if [ -f "$KILL_FILE" ] || [ -f "$STOP_SENTINEL" ]; then log "stop_sentinel_seen bail"; break; fi
    [ "$SHUTDOWN" -eq 1 ] && { log "shutdown_requested bail"; break; }

    "$@" >> "$CLOG" 2>&1 &
    CHILD_PID=$!
    log "spawned pid=$CHILD_PID n=$respawn_count"
    wait "$CHILD_PID"
    rc=$?
    CHILD_PID=""

    [ "$SHUTDOWN" -eq 1 ] && { log "child_exit rc=$rc reason=shutdown"; break; }
    if [ -f "$KILL_FILE" ] || [ -f "$STOP_SENTINEL" ]; then log "child_exit rc=$rc reason=stop_sentinel"; break; fi
    log "child_exit rc=$rc"

    now=$(date +%s)
    if [ $((now - window_start)) -gt "$WINDOW_SEC" ]; then fails_in_window=0; window_start=$now; fi
    fails_in_window=$((fails_in_window + 1))
    if [ "$fails_in_window" -ge "$WINDOW_FAILS_CAP" ]; then
        log "crash_loop_detected ${fails_in_window}_in_${WINDOW_SEC}s bail"; break
    fi
    respawn_count=$((respawn_count + 1))
    log "sleep 5 before respawn (total=$respawn_count)"
    sleep 5
done
log "respawn_exit"
