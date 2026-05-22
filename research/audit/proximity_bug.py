"""Reproduce the proximity-filter unit-mismatch bug.

features.proximity_pct_from_move(move_pct) returns |move_pct|/100 (a fraction).
signals.entry_signal rejects a tick only when `proximity > proximity_max_pct`.
With proximity_max_pct intended as a percent (e.g. 0.5 == "0.5%"), the filter
can never reject a realistic tick.
"""
from research.data.loader import iter_windows
import sys, os
sys.path.insert(0, os.environ.get("POLYMARKET_ARB_PATH", os.path.expanduser("~/dev/polymarket-arb")))
from scripts.mean_reversion import features as feat


def run():
    # Largest |move_pct| seen across a sample of real BTC 15m windows.
    worst = 0.0
    n = 0
    for slug, g in iter_windows("btc", "15m", "2026-03-14", "2026-05-21"):
        worst = max(worst, g["move_pct"].abs().max())
        n += 1
        if n >= 2000:
            break
    prox_at_worst = abs(worst) / 100.0  # what features.py computes
    print(f"windows sampled: {n}")
    print(f"largest |move_pct| observed: {worst:.4f}%")
    print(f"feature 'proximity' at that extreme: {prox_at_worst:.6f}")
    for thr in (0.2, 0.5, 1.5, 3.0, 100.0):
        fires = prox_at_worst > thr
        print(f"  proximity_max_pct={thr}: filter ever rejects? {fires}")
    print("VERDICT: proximity filter is inert for all realistic configs"
          if prox_at_worst <= 0.2 else "VERDICT: re-examine")

if __name__ == "__main__":
    run()
