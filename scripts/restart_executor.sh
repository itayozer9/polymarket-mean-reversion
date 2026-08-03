#!/usr/bin/env bash
# Restart the LIVE money executor safely.
#
# WHY THIS EXISTS: the executor is deliberately NOT in start_all.sh / stop_all.sh. Those
# manage the engine (`run_combined`); the executor's supervisor is `hourly_monitor.sh` at
# cron :37, which relaunches it whenever it is down AND no KILL switch is set. So:
#   - killing it without EXEC_KILL races the monitor (it may relaunch mid-edit),
#   - adding it to stop_all.sh would leave real money DOWN after an engine-only stop.
# The correct sequence is: set EXEC_KILL -> wait for a clean exit -> edit -> clear -> relaunch.
#
# TIMING: intents fire in the last ~6 minutes of each 15m window (fav_disagree tl 120-360s,
# det_lwd last 60s), so restart in the DEAD ZONE = minutes 0-8 past :00/:15/:30/:45.
# Since the C1 fix (2026-08-03) a restart REPLAYS the intent backlog instead of discarding
# it, so a mistimed restart is no longer silently lossy — but the dead zone is still free.
#
#   ./scripts/restart_executor.sh            # refuses outside the dead zone
#   ./scripts/restart_executor.sh --force    # restart now anyway
set -uo pipefail
export PATH="$HOME/.local/bin:$PATH"
cd "$(dirname "$0")/.." || exit 1
REPO="$(pwd)"

MIN=$(( 10#$(date +%M) % 15 ))
if [ "${1:-}" != "--force" ] && [ "$MIN" -ge 9 ]; then
  echo "minute-of-quarter $MIN is inside the intent-firing window (9-14)."
  echo "Wait $((15 - MIN))m for the next boundary, or pass --force."
  exit 1
fi

if [ -f "$REPO/data/KILL" ]; then
  echo "data/KILL present — the whole system is halted on purpose. Not restarting."; exit 1
fi

echo "== pre-restart state =="
cp "$REPO/data/live/executor_state.json" "$REPO/data/live/.executor_state.prerestart" 2>/dev/null \
  && echo "  book snapshot -> data/live/.executor_state.prerestart"
BEFORE_OK=$(grep -c '"ok": true' "$REPO/data/live/fills.jsonl" 2>/dev/null || echo 0)
echo "  ok-fills before: $BEFORE_OK"

echo "== halting (EXEC_KILL) =="
touch "$REPO/data/live/EXEC_KILL"
for _ in $(seq 1 15); do
  pgrep -f "live_executor.py --live" >/dev/null || break
  sleep 1
done
if pgrep -f "live_executor.py --live" >/dev/null; then
  echo "  did NOT exit within 15s — investigate before forcing; leaving EXEC_KILL set."; exit 1
fi
echo "  exited clean"

echo "== relaunching =="
rm -f "$REPO/data/live/EXEC_KILL"
nohup uv run --python 3.11 --no-project --with py-clob-client-v2 \
  --with python-dotenv --with structlog --with requests \
  scripts/live_executor.py --live >>"$REPO/logs/live_exec.log" 2>&1 &
sleep 10

echo "== post-restart checks =="
pgrep -f "live_executor.py --live" >/dev/null \
  && echo "  ✓ process up" || { echo "  ✗ FAILED TO START — check logs/live_exec.log"; exit 1; }
grep "executor_started" "$REPO/logs/live_exec.log" | tail -1 | cut -c1-200
AFTER_OK=$(grep -c '"ok": true' "$REPO/data/live/fills.jsonl" 2>/dev/null || echo 0)
[ "$AFTER_OK" = "$BEFORE_OK" ] \
  && echo "  ✓ ok-fills unchanged ($AFTER_OK) — the backlog replay placed no orders" \
  || echo "  ⚠️ ok-fills moved $BEFORE_OK -> $AFTER_OK — a replayed intent TRADED; verify it was live, not stale"
echo "  backlog replay skips: $(tail -400 "$REPO/logs/live_exec.log" | grep -c intent_skipped)"
echo "done. Books: diff data/live/.executor_state.prerestart data/live/executor_state.json"
