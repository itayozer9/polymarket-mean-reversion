#!/usr/bin/env bash
# Periodic win-claimer for the live probe. Runs live_claim.py --execute on a wall-clock
# schedule (default: minutes :05 and :35 of every hour) to redeem resolved crypto-15m WINS
# and wrap them to pUSD (tradeable). GASLESS via Polymarket's relayer (see invocation below).
# SHARED-WALLET SAFE: live_claim only ever touches `-updown-15m-` markets — the elon-tweets
# bot's positions are never claimed here.
#
# Schedule: CLAIM_AT_MINUTES (space/comma minutes-of-hour, default "5 35"). The 15m windows
# close at :00/:15/:30/:45; :05 and :35 redeem the :00 and :30 settlements ~5 min after close.
# Set e.g. CLAIM_AT_MINUTES="5 20 35 50" to also catch :15/:45 promptly (it's gasless, so the
# extra runs are free). Legacy CLAIM_INTERVAL (fixed-seconds sleep) is used only if
# CLAIM_AT_MINUTES is explicitly emptied.
#
# Independent of Claude AND of the live executor: it keeps claiming even if trading is
# stopped (you still want to collect already-won, already-open positions). Stops on
# data/KILL (global) or data/live/CLAIM_KILL (claimer-only). Per-run errors are logged
# and skipped — one bad run never kills the loop.
#
# Run (self-healing) via:
#   nohup scripts/respawn_generic.sh claim_loop data/live/CLAIM_KILL \
#     bash scripts/claim_loop.sh > logs/claim_loop_boot.log 2>&1 &
set -uo pipefail
cd "$(dirname "$0")/.."
REPO="$(pwd)"
CLAIM_AT_MINUTES="${CLAIM_AT_MINUTES-5 35}"   # wall-clock minutes-of-hour; empty => use CLAIM_INTERVAL
CLAIM_AT_MINUTES="${CLAIM_AT_MINUTES//,/ }"   # accept commas too
INTERVAL="${CLAIM_INTERVAL:-1800}"            # fallback fixed interval if CLAIM_AT_MINUTES is empty
KILL="$REPO/data/KILL"
SENT="$REPO/data/live/CLAIM_KILL"
LOG="$REPO/logs/claim_loop.log"
export POLYGON_RPC="${POLYGON_RPC:-https://polygon-bor-rpc.publicnode.com}"
mkdir -p "$REPO/logs" "$REPO/data/live"
log() { echo "$(date -u +%FT%TZ) claim_loop $1" | tee -a "$LOG" >&2; }
trap 'log "sigterm/sigint -> exit"; exit 0' SIGTERM SIGINT

# Seconds from now until the next scheduled minute-of-hour in CLAIM_AT_MINUTES (always > 0).
secs_until_next() {
    local now_m now_s t diff best=
    now_m=$((10#$(date +%M))); now_s=$((10#$(date +%S)))
    for t in $CLAIM_AT_MINUTES; do
        t=$((10#$t))
        diff=$(( t * 60 - (now_m * 60 + now_s) ))
        [ "$diff" -le 0 ] && diff=$(( diff + 3600 ))   # already passed this hour -> next hour
        { [ -z "$best" ] || [ "$diff" -lt "$best" ]; } && best=$diff
    done
    echo "$best"
}

log "start schedule_minutes='${CLAIM_AT_MINUTES:-<interval ${INTERVAL}s>}' rpc=${POLYGON_RPC}"
while true; do
    [ -f "$KILL" ] && { log "data/KILL seen -> exit"; break; }
    [ -f "$SENT" ] && { log "data/live/CLAIM_KILL seen -> exit"; break; }
    log "claim run begin"
    # Default path is the GASLESS relayer (CLAIM_VIA_RELAYER unset => on): py-builder-relayer-client
    # (+ poly-eip712-structs, py-builder-signing-sdk) submits via Polymarket's relayer which pays
    # gas; py-clob-client-v2 derives the relayer API creds from the key. web3/eth-abi/requests are
    # still used for the on-chain redeemability gate + discovery, and for the CLAIM_VIA_RELAYER=0
    # gas-paying fallback.
    uv run --python 3.11 --no-project \
        --with py-builder-relayer-client --with poly-eip712-structs --with py-builder-signing-sdk \
        --with py-clob-client-v2 \
        --with web3 --with eth-account --with eth-abi \
        --with requests --with structlog --with python-dotenv \
        scripts/live_claim.py --execute >> "$LOG" 2>&1 \
        || log "claim run errored (continuing)"
    if [ -n "$CLAIM_AT_MINUTES" ]; then
        secs=$(secs_until_next)
    else
        secs="$INTERVAL"
    fi
    log "claim run end -> next run in ${secs}s (minutes='${CLAIM_AT_MINUTES:-interval}')"
    # Sleep in 15s chunks so a stop sentinel is honored within ~15s, not a full interval.
    waited=0
    while [ "$waited" -lt "$secs" ]; do
        [ -f "$KILL" ] || [ -f "$SENT" ] && break
        sleep 15
        waited=$((waited + 15))
    done
done
log "stopped"
