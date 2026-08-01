"""Diagnostic for the calibration-NO edge: is it CALIBRATION (a persistent
mispricing of NO) or just DIRECTIONAL down-drift in the scored window?

If the future days simply drifted down (mean cl_up << 0.5), then "buy NO"
wins for a reason that will NOT generalize — it is a directional tilt, not a
calibration edge. A genuine calibration edge needs the NO side to be under-
priced AFTER accounting for the realized drift: i.e. the per-day gap between
the realized NO-win rate and the NO ask must be positive even on UP days.

Run: uv run python -m research.analysis.hunt.calibration_diag
"""
from __future__ import annotations
import numpy as np, pandas as pd, warnings
from research.analysis import edge_lab as L
warnings.filterwarnings("ignore")

B = L.load_base().merge(L.cl_outcomes(), on="slug", how="inner")

# Decision band: 60-120s in, healthy book, tradeable mid.
g = B[(B["seconds_into_window"] >= 60) & (B["seconds_into_window"] < 120)
      & (B["time_left_sec"] >= 30) & B["book_healthy"]
      & (B["yes_mid"] >= 0.05) & (B["yes_mid"] <= 0.95)]
f = g.sort_values("seconds_into_window").groupby("slug", as_index=False).first()

f = f.copy()
f["no_ask"] = 1.0 - f["yes_best_bid"]
f["no_win"] = 1 - f["cl_up"]
f = f[np.isfinite(f["no_ask"]) & (f["no_ask"] > 0.05) & (f["no_ask"] < 0.95)]

print("=" * 90)
print("CALIBRATION-vs-DIRECTIONAL diagnostic for blind-NO @ 60-120s decision band")
print("=" * 90)
print(f"total windows {len(f)}  overall NO-win {f['no_win'].mean():.3f}  "
      f"mean no_ask {f['no_ask'].mean():.3f}  gap {f['no_win'].mean()-f['no_ask'].mean():+.3f}")

print("\nPer-day: realized NO-win vs mean no_ask (gap>0 = NO under-priced THAT day):")
print(f"{'date':12}{'split':9}{'n':>6}{'NOwin':>8}{'no_ask':>8}{'gap':>8}{'updrift':>9}")
for d, s in f.groupby("date"):
    sp = s["split"].iloc[0]
    updrift = s["cl_up"].mean()
    print(f"{d:12}{sp:9}{len(s):>6}{s['no_win'].mean():>8.3f}{s['no_ask'].mean():>8.3f}"
          f"{s['no_win'].mean()-s['no_ask'].mean():>+8.3f}{updrift:>9.3f}")

# Per-symbol per-split, to see if it's one coin / one direction.
print("\nPer-symbol x split: NO-win, no_ask, gap, cl_up(updrift):")
for (sym, sp), s in f.groupby(["symbol", "split"]):
    print(f"  {sym:4} {sp:8} n={len(s):>4}  NOwin {s['no_win'].mean():.3f}  "
          f"ask {s['no_ask'].mean():.3f}  gap {s['no_win'].mean()-s['no_ask'].mean():+.3f}  "
          f"updrift {s['cl_up'].mean():.3f}")

# The key control: regress out the day's drift. A calibration edge means NO is
# under-priced even where the market is a coin-flip (yes_mid~0.5) AND on up-days.
print("\nDrift control — restrict to near-balanced mids (yes_mid in 0.45-0.55):")
bal = f[(f["yes_mid"] >= 0.45) & (f["yes_mid"] <= 0.55)]
for sp, s in bal.groupby("split"):
    print(f"  {sp:8} n={len(s):>4}  NOwin {s['no_win'].mean():.3f}  ask {s['no_ask'].mean():.3f}"
          f"  gap {s['no_win'].mean()-s['no_ask'].mean():+.3f}  updrift {s['cl_up'].mean():.3f}")

# Split future days into up vs down days; does NO still beat its ask on UP days?
print("\nFuture split — NO gap conditioned on the day's drift direction:")
fut = f[f["split"] == "future"]
day_drift = fut.groupby("date")["cl_up"].mean()
up_days = day_drift[day_drift >= 0.5].index
dn_days = day_drift[day_drift < 0.5].index
for lbl, days in [("UP-drift days", up_days), ("DOWN-drift days", dn_days)]:
    s = fut[fut["date"].isin(days)]
    if len(s):
        print(f"  {lbl:16} days={list(days)} n={len(s)}  NOwin {s['no_win'].mean():.3f}  "
              f"ask {s['no_ask'].mean():.3f}  gap {s['no_win'].mean()-s['no_ask'].mean():+.3f}")

# Coinbase-vs-Chainlink basis: is YES overpriced because the market prices off
# Coinbase but Chainlink resolves lower? Compare Coinbase-implied outcome vs CL.
if "outcome_up_clean" in f.columns:
    print("\nCoinbase(outcome_up_clean) vs Chainlink(cl_up) outcome disagreement:")
    for sp, s in f.groupby("split"):
        cb = s["outcome_up_clean"]
        disagree = (cb != s["cl_up"]).mean()
        print(f"  {sp:8} n={len(s):>4}  cb_up {cb.mean():.3f}  cl_up {s['cl_up'].mean():.3f}  "
              f"disagree {disagree:.3f}  (cb-cl) {cb.mean()-s['cl_up'].mean():+.3f}")
