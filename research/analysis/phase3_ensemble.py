"""Phase 3 (P1) — ensemble / portfolio construction.

The lwd<->sq timing anti-correlation memo suggests combining the edges lowers
variance. Measure it honestly: daily-PnL correlation between determinism and
stale-quote, and the risk-adjusted return (Deflated Sharpe / Sortino) of each
alone vs combined (equal-dollar and risk-parity weights).

NOTE sq deploys ~3-4x det's daily dollars at $10/trade, so equal-stake != equal-
risk; risk-parity scales each edge to equal daily-PnL volatility.

Run: uv run python -m research.analysis.phase3_ensemble
"""
from __future__ import annotations
import os
import numpy as np
import pandas as pd

from research.lib import rigor as R

LED = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "..", "data", "research", "ledgers"))


def daily(name):
    led = pd.read_parquet(os.path.join(LED, name))
    return R.daily_pnl_from_ledger(led)


def stats(s, label):
    sr = R.sharpe(s.values)
    so = R.sortino(s.values)
    ca = R.calmar(s.values)
    print(f"  {label:>26}: mean/day ${s.mean():+7.2f}  std ${s.std(ddof=1):6.2f}  "
          f"Sharpe {sr:+.3f}  Sortino {so:+.3f}  Calmar {ca:+.1f}  total ${s.sum():+.0f}")
    return sr


def run():
    det = daily("det_full.parquet")
    sq = daily("sq_full.parquet")
    idx = sorted(set(det.index) | set(sq.index))
    det = det.reindex(idx, fill_value=0.0)
    sq = sq.reindex(idx, fill_value=0.0)

    print(f"=== Ensemble: determinism x stale-quote ({len(idx)} days) ===")
    corr = np.corrcoef(det.values, sq.values)[0, 1]
    print(f"  daily-PnL correlation = {corr:+.3f}  "
          f"({'DIVERSIFYING' if corr < 0.3 else 'correlated'})")

    print("\n  Standalone:")
    stats(det, "determinism ($10/tr)")
    stats(sq, "stale_quote ($10/tr)")

    print("\n  Combined books:")
    stats(det + sq, "equal-dollar (det+sq)")
    # risk-parity: scale each to unit daily-vol, then equal-weight
    wd = 1.0 / det.std(ddof=1)
    ws = 1.0 / sq.std(ddof=1)
    rp = wd * det + ws * sq
    srp = stats(rp, "risk-parity (vol-scaled)")
    # det-heavy (sq down-weighted to det's vol) — what a cautious live book looks like
    k = det.std(ddof=1) / sq.std(ddof=1)
    stats(det + k * sq, f"det + {k:.2f}*sq (sq->det vol)")

    print(f"\n  Read: if corr is low/negative, the combined Sharpe exceeds either alone.")
    print(f"  det Sharpe {R.sharpe(det.values):+.2f} | sq {R.sharpe(sq.values):+.2f} | "
          f"risk-parity {srp:+.2f}")


if __name__ == "__main__":
    run()
