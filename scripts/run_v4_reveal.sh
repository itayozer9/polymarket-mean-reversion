#!/usr/bin/env bash
# Edge Hunt v4 reveal, run DETACHED (nohup) as three sequential python processes.
#
# Detached because a harness-managed background call was killed mid-run at ~10 min; the
# frame build survived the same wall-clock only because it was nohup'd. Sequential
# separate processes because load_base() materializes the full ~80-column frame before
# subsetting: doing the slim build in its own process means that memory peak is released
# before the atlas allocates its own obs frames and bootstrap arrays.
#
#   nohup ./scripts/run_v4_reveal.sh > logs/v4_reveal.log 2>&1 &
set -uo pipefail
export PATH="$HOME/.local/bin:$PATH"
cd "$(dirname "$0")/.." || exit 1
export PYTHONPATH=.
export PYTHONUNBUFFERED=1          # so the log is readable while it runs

echo "===== [1/3] slim frame $(date -u +%H:%M:%SZ) ====="
nice -n 19 uv run python -c "
from research.analysis.atlas_v4 import build_v4_slim
build_v4_slim()" || { echo "SLIM FAILED"; exit 1; }

echo "===== [2/3] V4a atlas reveal $(date -u +%H:%M:%SZ) ====="
nice -n 19 uv run python -m research.analysis.atlas_v4 || { echo "V4a FAILED"; exit 1; }

echo "===== [3/3] V4b frozen timing cells $(date -u +%H:%M:%SZ) ====="
nice -n 19 uv run python -m research.analysis.v4b_timing_cells || { echo "V4b FAILED"; exit 1; }

echo "===== DONE $(date -u +%H:%M:%SZ) ====="
