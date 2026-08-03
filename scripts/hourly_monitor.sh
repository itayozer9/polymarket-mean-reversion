#!/usr/bin/env bash
# Persistent hourly monitor for the mean-rev paper bot + live $100 probe.
# Runs from OS cron (independent of any Claude session) so live wins are ALWAYS
# redeemed and the probe stays up. The richer Claude `mean-rev-status` skill runs
# separately (session cron) for analysis; this is the money-safe backbone.
#
#   crontab:  37 * * * *  /Users/itayozer/dev/polymarket-mean-reversion/scripts/hourly_monitor.sh
set -uo pipefail
export PATH="$HOME/.local/bin:$PATH"  # cron's PATH lacks uv
cd "$(dirname "$0")/.." || exit 1
REPO="$(pwd)"
LOG="$REPO/logs/hourly_monitor.log"
mkdir -p "$REPO/logs"
exec >>"$LOG" 2>&1
echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) hourly monitor ====="

# 1) Status snapshot (per-strategy PnL/WR + heartbeat) — how we track the edges.
uv run python -m mean_reversion_live.scripts.status || echo "[warn] status failed"

# 2) Auto-claim WON live positions — idempotent, eth_call-sim-gated, gas-capped,
#    and filtered to THIS probe's crypto-15m wins only (shared-wallet safe).
uv run --python 3.11 --no-project \
  --with py-builder-relayer-client --with poly-eip712-structs --with py-builder-signing-sdk \
  --with py-clob-client-v2 \
  --with web3 --with eth-account --with eth-abi \
  --with requests --with structlog --with python-dotenv \
  scripts/live_claim.py --execute || echo "[warn] claim failed"

# 3) Keep the live executor up (probe continuity) unless a KILL switch is set.
if ! pgrep -f "live_executor.py --live" >/dev/null 2>&1; then
  if [ ! -f "$REPO/data/live/EXEC_KILL" ] && [ ! -f "$REPO/data/KILL" ]; then
    echo "[info] live executor down -> restarting"
    nohup uv run --python 3.11 --no-project --with py-clob-client-v2 \
      --with python-dotenv --with structlog --with requests \
      scripts/live_executor.py --live >>"$REPO/logs/live_exec.log" 2>&1 &
  else
    echo "[info] live executor down but KILL switch set -> staying down"
  fi
fi
# 4) Daily (06 UTC pass): print-model drift alarm — exit 1 means the live op-model
#    has drifted; run the refit dry-run + review (see oracle_model_refit.py header).
if [ "$(date -u +%H)" = "06" ]; then
  if ! uv run python -m research.analysis.oracle_model_refit --check >>"$REPO/logs/model_check.log" 2>&1; then
    echo "[warn] print-model drift alarm FIRED — run: uv run python -m research.analysis.oracle_model_refit --refit (review gate, then --execute + scheduled restart)"
  fi
fi
# 5) Chainlink feed liveness. The collector logs `chainlink_status rows_ok=N rows_err=M`
#    every 20 cycles with the counters reset each time, so rows_ok=0 means a WHOLE block
#    of polls failed = feed dead. On 2026-07-24/25 this ran at 100% failure for 32h
#    unnoticed (dead built-in RPC default); a dead feed is silent, so it needs an alarm.
CL_STATUS="$(grep -o 'chainlink_status.*' "$REPO/logs/combined.log" 2>/dev/null | tail -1)"
case "$CL_STATUS" in
  *rows_ok=0*) echo "[warn] chainlink feed DEAD — last: ${CL_STATUS}. Check POLYGON_RPC_URL in .env and DEFAULT_POLYGON_RPC; oracle-gated strategies fail CLOSED (silently stop firing) and cl_* research features go blank." ;;
  "")          echo "[warn] no chainlink_status line in combined.log — collector may not be running" ;;
esac

# 6) Honest-label pipeline liveness. EVERY gate read (score_gates, the §5 paper table) scores
#    on the official on-chain labels this nightly produces. If it dies, the numbers silently
#    fall back to a stale window while still LOOKING fresh — and the engine tape they'd be
#    compared against runs ~3x hot. Nightly is launchd ~03:15Z, so >26h means it missed a run.
if [ -n "$(find "$REPO/data/research/paper_official/daily_scores.parquet" -mtime +1 2>/dev/null)" ]; then
  echo "[warn] paper_official labels STALE (>26h) — nightly_honest may be dead. Gates and the §5 paper table are scoring on old labels. Run: ./scripts/nightly_honest.sh"
fi

echo "----- done -----"
