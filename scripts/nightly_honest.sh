#!/bin/zsh
# Nightly honest scoreboard (Edge Hunt v2, 2026-07-02).
# Fetches yesterday's official on-chain outcomes (15m + 5m), re-settles every paper
# ledger on them, and regenerates data/research/paper_official/scoreboard.md.
# Official labels == real-money resolution (parity-pinned), so the scoreboard is a
# true out-of-sample stream. Exits nonzero on fetch/resettle failure so a wrapper
# (cron mail / status skill) can surface it.
set -e
cd "$(dirname "$0")/.."

SINCE=$(date -u -v-3d +%Y-%m-%d 2>/dev/null || date -u -d '3 days ago' +%Y-%m-%d)

uv run python -m research.dataset.official_outcomes --timeframes 15m,5m --since "$SINCE"
uv run python -m research.analysis.resettle_official
uv run pytest tests/research/test_resettle_official.py -q --no-header 2>&1 | tail -1

echo "[nightly_honest] OK $(date -u +%Y-%m-%dT%H:%MZ)"
