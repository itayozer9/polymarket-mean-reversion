#!/usr/bin/env bash
# Rebuild the canonical research frame (joined_15m + slim) through the last COMPLETE UTC day.
#
# WHY: the frame is the silent input to every atlas/edge_lab analysis. On 2026-08-15 it was
# 3 weeks stale (built 07-24 10:06) and the Edge Hunt v4 reveal read 100 windows with an
# EMPTY sealed split before anyone noticed. A gate that reads a truncated window produces a
# confident wrong verdict, and a terminal one-look gate cannot be re-taken.
#
# COIN SET: btc,eth,sol,xrp — the joined.build() default, kept deliberately (user decision
# 2026-08-15). Widening it would change frame contents for every consumer that assumed 4
# coins (v3 explicitly excluded the new coins). New-coin work uses its own window frame,
# e.g. data/research/v4_frame/ from research/dataset/build_v4_window.py.
#
# The build writes only at the END (concat -> to_parquet), so an abort leaves the old file
# intact. A .bak copy is still taken because this is the most depended-on artifact in the repo.
#
#   nohup ./scripts/rebuild_canonical_frame.sh > logs/frame_rebuild.log 2>&1 &
set -uo pipefail
export PATH="$HOME/.local/bin:$PATH"
cd "$(dirname "$0")/.." || exit 1
export PYTHONPATH=.
export PYTHONUNBUFFERED=1

END="${1:-$(date -u -v-1d +%Y-%m-%d)}"     # default: yesterday UTC = last complete day
echo "===== canonical frame rebuild -> $END  ($(date -u +%H:%M:%SZ)) ====="

nice -n 19 uv run python -c "
import research.dataset.joined as J
J.build(timeframes=('15m',), symbols=('btc','eth','sol','xrp'), date_end='$END')
" || { echo 'JOINED BUILD FAILED — canonical left untouched'; exit 1; }

echo "===== slim frame ($(date -u +%H:%M:%SZ)) ====="
# load_base() PREFERS the slim frame, so a fresh joined + stale slim would change nothing.
nice -n 19 uv run python -c "
from research.analysis.edge_lab import build_slim
print('slim ->', build_slim())
" || { echo 'SLIM BUILD FAILED — slim is now stale vs joined, FIX BEFORE ANY GATE'; exit 1; }

echo "===== verify ($(date -u +%H:%M:%SZ)) ====="
nice -n 19 uv run python -c "
import pandas as pd
from research.analysis.edge_lab import load_base
b = load_base()
d = b['date'].astype(str)
print(f'  rows {len(b):,} | slugs {b[\"slug\"].nunique():,} | coins {sorted(b[\"symbol\"].unique())}')
print(f'  date range {d.min()} .. {d.max()}')
"
echo "===== DONE $(date -u +%H:%M:%SZ) ====="
