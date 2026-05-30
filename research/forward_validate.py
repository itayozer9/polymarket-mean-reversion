"""Phase 5 — forward validation of the determinism edge (the SAFE way).

The live paper engine cannot yet run this strategy faithfully: it would key on
the tick's stale coinbase_price/move_pct (median 1.75bps off the fresh WS spot,
sign-disagrees 12.8% — fatal at a 5bps threshold). So instead of deploying to the
live engine, we forward-validate by re-running the LOCKED rule on each clean day
as the bot collects it, using the FRESH cb_spot (live_spot) and settling on the
TRUE outcome — the same harness the edge was validated on.

Workflow (run after each new clean UTC day lands):
  uv run python -m research.build_joined          # rebuild joined_15m with the new day
  uv run python -m research.forward_validate       # per-day OOS track + append to log

Dev days (selection) are marked [dev]; everything after is genuine OOS.
Run: uv run python -m research.forward_validate
"""
from __future__ import annotations
import os
import pandas as pd

from research.analysis.gauntlet import _prep, precompute_trades, _pnl, PRIMARY
from research.dataset.feeds import load_l2_ladders
from research.lib.stats import window_clustered_bootstrap
from research.clean_window import CLEAN_START, CLEAN_DEV_END, available_clean_dates

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(REPO, "docs", "research", "forward_validation_log.md")


def run():
    full = _prep()
    ladders = {s: load_l2_ladders(s, CLEAN_START,
               (available_clean_dates("btc") or [CLEAN_START])[-1])
               for s in sorted(full["symbol"].unique())}
    t = precompute_trades(full, ladders, *PRIMARY)   # fresh cb_spot, true-outcome settle
    t["pnl"] = _pnl(t)
    print(f"Forward validation — rule t_max={PRIMARY[0]} dist_min={PRIMARY[1]} "
          f"max_ask={PRIMARY[2]} (fresh cb_spot, true-outcome settle)\n")
    print(f"{'date':>12} {'split':>5} {'trades':>7} {'WR':>6} {'$/trade':>9} {'$ total':>9} {'cum $':>9}")
    cum = 0.0
    rows = []
    for dt, g in t.groupby("date"):
        split = "dev" if dt <= CLEAN_DEV_END else "OOS"
        cum += g["pnl"].sum()
        rows.append((dt, split, len(g), g["pnl"].mean(), g["pnl"].sum(), cum))
        print(f"{dt:>12} {split:>5} {len(g):>7} {g['won'].mean():>6.3f} "
              f"${g['pnl'].mean():>+8.3f} ${g['pnl'].sum():>+8.1f} ${cum:>+8.1f}")
    # OOS-only summary (the honest forward number)
    oos = t[t["date"] > CLEAN_DEV_END]
    if len(oos) >= 10:
        lo, _, hi = window_clustered_bootstrap(oos["pnl"].to_numpy(),
                                               oos["slug"].to_numpy(), n=2000)
        print(f"\nOOS (post-{CLEAN_DEV_END}): n={len(oos)} WR={oos['won'].mean():.3f} "
              f"${oos['pnl'].mean():+.3f}/trade CI[{lo:+.3f},{hi:+.3f}] total=${oos['pnl'].sum():+.1f}")
    return rows


if __name__ == "__main__":
    run()
