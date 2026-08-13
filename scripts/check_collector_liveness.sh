#!/usr/bin/env bash
# Collector-dark alarm. Exits non-zero (and prints why) when the tick collector has
# stopped writing rows, or when its status line is absent entirely.
#
# Why: on 2026-08-09 the collector wrote ZERO rows for 43 min while the heartbeat
# stayed at 1s and the process stayed alive (a DNS outage mass-settled every live
# window; see markets/discovery.py step 3). A human found it by reading the log.
# Silent data stops are this project's recurring failure mode - external CPU
# contention 06-06, the heartbeat full-file parse 06-12, DNS 08-09 - and the fix
# is the same one the Chainlink feed got after 32h of unnoticed failure: an alarm.
#
# `aggregator_status` is logged every ~10s; `rows_written` is that cycle's row count.
# 15m windows are contiguous, so a healthy collector always has a current window and
# rows_written > 0 every cycle. Requires 3 consecutive zero cycles (~30s) so a single
# unlucky boundary sample cannot fire it, and reads only the TAIL so it clears itself
# once the collector recovers instead of latching.
#
#   usage: check_collector_liveness.sh [path/to/combined.log]
set -uo pipefail
LOG="${1:-$(cd "$(dirname "$0")/.." && pwd)/logs/combined.log}"

if [ ! -f "$LOG" ]; then
  echo "[warn] collector log not found at ${LOG}: cannot verify the tick feed is writing"
  exit 1
fi

# No mapfile / no arrays: macOS /bin/bash is 3.2 and cron may invoke either bash.
ROWS="$(grep 'aggregator_status' "$LOG" 2>/dev/null \
        | grep -oE 'rows_written=[0-9]+' | tail -3 | tr '\n' ' ')"
set -- $ROWS   # positional params: $# = cycles found, $1..$3 = the values

if [ "$#" -eq 0 ]; then
  echo "[warn] no aggregator_status line in ${LOG}: the collector may not be running;" \
       "no tick data is being written and no paper signals can fire"
  exit 1
fi

# Fewer than 3 cycles = fresh boot; not enough evidence to call it dark.
[ "$#" -lt 3 ] && exit 0

for r in "$@"; do
  [ "$r" = "rows_written=0" ] || exit 0
done

echo "[warn] collector DARK: last 3 cycles all rows_written=0. The feed is alive but no" \
     "ticks are being written, so paper strategies cannot fire and live intents stop." \
     "Heartbeat/process checks do NOT catch this. Check: emit_summary skipped_window (all" \
     "markets outside their window => discovery lost the live set) and recent" \
     "spot_fetch_failed / chainlink_fetch_failed / gamma errors in ${LOG}."
exit 1
