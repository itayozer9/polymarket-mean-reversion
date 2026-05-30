#!/usr/bin/env bash
# End-to-end strategy discovery pipeline.
set -e
cd "$(dirname "$0")/../.."

mkdir -p runs

echo "Step 1/5: Broad sweep (1500 configs)..."
[[ -f runs/broad_sweep_v1.jsonl && $(wc -l < runs/broad_sweep_v1.jsonl) -ge 1400 ]] && echo "  (already done)" || \
  uv run python scripts/analysis/run_broad_sweep.py --n 1500 --workers 5 --out runs/broad_sweep_v1.jsonl

echo "Step 2/5: Focused sweep (1000 configs)..."
[[ -f runs/focused_sweep_v1.jsonl && $(wc -l < runs/focused_sweep_v1.jsonl) -ge 900 ]] && echo "  (already done)" || \
  uv run python scripts/analysis/run_focused_sweep.py --n 1000 --workers 5 --out runs/focused_sweep_v1.jsonl

echo "Step 3/5: ASIA-specialist sweep (400 configs)..."
[[ -f runs/asia_sweep_v1.jsonl && $(wc -l < runs/asia_sweep_v1.jsonl) -ge 380 ]] && echo "  (already done)" || \
  uv run python scripts/analysis/run_asia_specialist_sweep.py --n 400 --workers 5 --out runs/asia_sweep_v1.jsonl

echo "Step 4/5: Post-hoc filter slicing..."
uv run python scripts/analysis/post_hoc_filters.py \
  --sweeps runs/broad_sweep_v1.jsonl runs/focused_sweep_v1.jsonl runs/asia_sweep_v1.jsonl \
  --out runs/post_hoc_v1.jsonl > runs/post_hoc_summary.txt

echo "Step 5/5: Robust portfolio selection..."
uv run python scripts/analysis/build_strategy_portfolio.py \
  --sweeps runs/broad_sweep_v1.jsonl runs/focused_sweep_v1.jsonl runs/asia_sweep_v1.jsonl \
  --out-md PROPOSED_STRATEGIES.md \
  --out-yaml runs/proposed_strategies.yaml \
  --use-relaxed --top 10

echo ""
echo "Validation: running picks on live-only data..."
uv run python scripts/analysis/validate_picks_live.py \
  --picks runs/proposed_strategies.yaml \
  --date-start 2026-05-15 --date-end 2026-05-17 \
  > runs/live_validation.txt

echo ""
echo "Pipeline complete. See:"
echo "  - PROPOSED_STRATEGIES.md"
echo "  - runs/proposed_strategies.yaml"
echo "  - runs/live_validation.txt"
