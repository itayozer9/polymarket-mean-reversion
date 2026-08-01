"""Backtest the EXISTING strategies in their post-change ('new form') on CHAINLINK
settlement — the oracle Polymarket actually pays — to answer: do they still perform?

The engine change settles on Chainlink (open vs close), not Coinbase. The signal/entry
path is unchanged (replay-parity green), so the existing ledgers' ENTRIES are still
valid; we just re-settle them on Chainlink. For determinism we ALSO show the LIVE
config's dist>=8bps filter (the locked-favourite filter that keeps it positive on the
correct oracle). Window-clustered bootstrap CIs per split.

Run: uv run python -m research.analysis.backtest_chainlink_newform
"""
from __future__ import annotations
import os
import pandas as pd

from research.analysis.resettle_chainlink import (
    chainlink_outcome_by_slug, _resettle, _report, LED)


def run():
    cl = chainlink_outcome_by_slug()
    print(f"chainlink outcomes resolved for {len(cl):,} windows\n")

    det = pd.read_parquet(os.path.join(LED, "det_full.parquet"))
    _report("DET_LWD  (all, Chainlink)", _resettle(det, cl, "fav_side"))

    det8 = det[det["dist_bps"] >= 8.0].copy()
    _report(f"DET_LWD  dist>=8bps  [LIVE CONFIG]  (n={len(det8)}/{len(det)}, Chainlink)",
            _resettle(det8, cl, "fav_side"))

    sq = pd.read_parquet(os.path.join(LED, "sq_full.parquet"))
    _report("DET_SQP  (Chainlink)", _resettle(sq, cl, "symbol_buy"))

    print("\nNOTE: 'CL EV' is per-$10-trade on the CHAINLINK outcome Polymarket pays. "
          "future = fresh-OOS (most recent days). Decide on the future-split CI.")


if __name__ == "__main__":
    run()
