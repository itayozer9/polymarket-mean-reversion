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

# --- signals.jsonl rotation (2026-07-09): the engine holds a persistent append handle,
# so rotation is only safe here — after the already-running guard, before launch.
# Rotate ONLY signals.jsonl (high-volume diagnostics); NEVER trades.jsonl /
# portfolio_snapshots.jsonl (durable ledgers — status span math reads them whole).
# >200MB threshold avoids churn on frequent restarts; gzip runs in background.
for f in "$REPO"/data/jsonl/*/signals.jsonl; do
    [[ -f "$f" ]] || continue
    sz=$(stat -f%z "$f" 2>/dev/null || echo 0)
    if (( sz > 200*1024*1024 )); then
        rot="${f%.jsonl}.$(date -u +%Y%m%d).jsonl"
        mv "$f" "$rot"
        nohup gzip "$rot" > /dev/null 2>&1 &
        echo "Rotated $(basename "$(dirname "$f")")/signals.jsonl ($((sz/1024/1024))MB) -> $(basename "$rot").gz"
    fi
done

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

# --- claim daemon: self-healing redeem + deposit-confirm (REAL MONEY, GASLESS, idempotent) ---
# Redeems resolved -updown-15m- WINS and wraps -> pUSD ("confirm deposit") at wall-clock minutes
# CLAIM_AT_MINUTES (default :05 and :35). GASLESS via Polymarket's relayer (relayer pays gas; EOA
# needs no MATIC) with the on-chain redeemability gate; shared-wallet filtered + idempotent.
# Skipped if data/KILL or data/live/CLAIM_KILL is set. Self-heals via respawn_generic; safe even
# if trading is stopped.
if [[ -f "$REPO/data/KILL" || -f "$REPO/data/live/CLAIM_KILL" ]]; then
    echo "Claim daemon: KILL/CLAIM_KILL present -> not starting."
elif pgrep -f "claim_loop" > /dev/null 2>&1; then
    echo "Claim daemon: already running."
else
    echo "Starting claim daemon (gasless redeem + deposit-confirm, at :${CLAIM_AT_MINUTES:-05 & :35})…"
    nohup "$REPO/scripts/respawn_generic.sh" claim_loop \
        "$REPO/data/live/CLAIM_KILL" bash "$REPO/scripts/claim_loop.sh" \
        >> "$REPO/logs/claim_loop_boot.log" 2>&1 &
    echo "  Claim daemon launched (logs/claim_loop.log)."
fi

# --- macro collector: IV/funding/OI research feed (no money; data only) ---
# Self-heals via respawn_generic. Sentinel: data/MACRO_KILL. Started here so a reboot can't
# silently gap the feed again (it went dark 06-19 -> 07-06 unnoticed).
if [[ -f "$REPO/data/KILL" || -f "$REPO/data/MACRO_KILL" ]]; then
    echo "Macro collector: KILL/MACRO_KILL present -> not starting."
elif pgrep -f "macro_collector" > /dev/null 2>&1; then
    echo "Macro collector: already running."
else
    echo "Starting macro collector (IV/funding/OI feed)…"
    nohup "$REPO/scripts/respawn_generic.sh" macro \
        "$REPO/data/MACRO_KILL" uv run python -m mean_reversion_live.collectors.macro_collector \
        >> "$REPO/logs/macro_boot.log" 2>&1 &
    echo "  Macro collector launched (logs/macro.log)."
fi

echo "Running. tail -f $ROT_LOG  to follow."
