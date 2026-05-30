"""Stage 13 — March historical replay (separate-regime cross-check).

Replays survivors over the post-fix March 4-17 slice and requires net-positive
PnL. Adds historical CSVs to combined dir on demand (idempotent).
"""
from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

from scripts.sweep_v2 import _runner, evaluate, param_space

ROOT = Path(__file__).resolve().parent.parent.parent
SWEEP_DIR = ROOT / "data" / "sweep_v2"
COMBINED = SWEEP_DIR / "combined"


def ensure_march_linked():
    """Symlink data/historical/*.csv.gz into combined dir if missing."""
    hist = ROOT / "data" / "historical"
    n = 0
    for csv in hist.glob("*.csv.gz"):
        if csv.name.endswith("_raw.csv.gz"):
            continue
        dest = COMBINED / csv.name
        if not dest.exists():
            os.symlink(csv.resolve(), dest)
            n += 1
    return n


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=str(SWEEP_DIR / "stage12_replay_live.jsonl"))
    parser.add_argument("--symbols", default="btc,eth,sol,xrp")
    parser.add_argument("--march-start", default="2026-03-04")
    parser.add_argument("--march-end", default="2026-03-17")
    parser.add_argument("--out", default=str(SWEEP_DIR / "stage13_replay_march.jsonl"))
    args = parser.parse_args()

    rows = _runner.read_jsonl(Path(args.input)) if Path(args.input).exists() else []
    survivors = [r for r in rows if r.get("replay_check", {}).get("consistent")]
    print(f"  Stage 13: March cross-check on {len(survivors)} survivors.")
    if not survivors:
        _runner.write_jsonl([], Path(args.out))
        return

    n_linked = ensure_march_linked()
    if n_linked > 0:
        print(f"  Stage 13: linked {n_linked} new March CSVs into combined dir.")

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    # Note: no feature lookup for March — features.parquet only covers May.
    # filter_v2.* gates will reject all trades for March (no feature data).
    # We accept this conservatism — March is a sanity check, not a primary venue.
    ctx = evaluate.EvalContext.build(symbols, args.march_start, args.march_end, feature_lookup=None)

    out_rows = []
    for r in survivors:
        # No fold mask for March — run on all March markets.
        res = evaluate.eval_on_slugs(ctx, r["config"], slug_filter=None, seed=42)
        pnl = res["aggregate"]["net_pnl"]
        n = res["aggregate"]["n_trades"]
        passed = pnl > 0
        out_rows.append({**r, "march_replay": {"net_pnl": pnl, "n_trades": n, "pass": passed}})
        print(f"  Stage 13: {r['config_id']} March n={n} pnl={pnl:.2f} pass={passed}")

    n_pass = sum(1 for r in out_rows if r["march_replay"]["pass"])
    _runner.write_jsonl(out_rows, Path(args.out))
    print(f"  Stage 13: {n_pass}/{len(out_rows)} survivors net-positive on March.")


if __name__ == "__main__":
    main()
