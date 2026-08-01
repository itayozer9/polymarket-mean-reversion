"""Independent verification of E4 (disagreement-determinism) with the trusted
parity harness: real L2 ladder-walk fills (walk_buy), one trade/window, hold to
resolution, per-split window-clustered CI + CPCV + a >=$10-depth subset.

E4 rule: last <=60s, |dist_strike_bps|>=5, book favourite (yes_mid>=0.5) DISAGREES
with spot-implied side (sign dist) -> buy the SPOT-implied side (the cheap one the
book has as underdog), hold to resolution. Disjoint from det_lwd (which requires
agreement), so it's additive.

Run: uv run python -m research.analysis.e4_verify
"""
from __future__ import annotations
import os
import numpy as np
import pandas as pd

from research.sim.fills_v2 import walk_buy, settle_pnl
from research.lib.stats import window_clustered_bootstrap
from research.lib.rigor import combinatorial_purged_cv
from research.clean_window import available_clean_dates
from research.analysis.loss_patterns import _base, _ladders, JOINED, STAKE

_LV = range(1, 11)


def e4_ledger(df, ladders, t_max=60, dist_min=5, latency=2):
    yf = df["yes_mid"] >= 0.5                       # book favours YES
    sfy = df["dist_strike_bps"] > 0                 # spot favours YES
    disagree = yf != sfy
    cand = df[(df["time_left_sec"] >= 1) & (df["time_left_sec"] <= t_max)
              & (df["abs_dist_bps"] >= dist_min) & disagree]
    first = cand.sort_values(["slug", "seconds_into_window"]).groupby("slug", as_index=False).first()
    rows = []
    for _, r in first.iterrows():
        lad = ladders.get(r["symbol"])
        if lad is None:
            continue
        try:
            lr = lad.loc[(r["slug"], int(r["seconds_into_window"]) + latency)]
        except KeyError:
            continue
        if isinstance(lr, pd.DataFrame):
            lr = lr.iloc[0]
        buy_yes = bool(r["dist_strike_bps"] > 0)    # buy the SPOT-implied side
        if buy_yes:
            px = [lr[f"ask_px_{i}"] for i in _LV]; sz = [lr[f"ask_sz_{i}"] for i in _LV]
            won = r["outcome_up_clean"] == 1
            top = float(lr["ask_px_1"]); d10 = float(r["yes_ask_depth"]) * top
        else:
            px = [1.0 - lr[f"bid_px_{i}"] for i in _LV]; sz = [lr[f"bid_sz_{i}"] for i in _LV]
            won = r["outcome_up_clean"] == 0
            top = 1.0 - float(lr["bid_px_1"]); d10 = float(r["no_ask_depth"]) * top
        fill = walk_buy(px, sz, STAKE)
        if not fill.filled or fill.unfilled_usd > STAKE * 0.5:
            continue
        rows.append(dict(slug=r["slug"], symbol=r["symbol"], date=str(r["date"]),
                         split=str(r["split"]), pnl=settle_pnl(fill, bool(won)), won=int(bool(won)),
                         entry_ask=float(fill.avg_price), top_ask=float(top),
                         dist_bps=float(r["abs_dist_bps"]), time_left=int(r["time_left_sec"]),
                         best_depth_usd=float(d10), unfilled=float(fill.unfilled_usd)))
    return pd.DataFrame(rows)


def _ci(s):
    lo, _, hi = window_clustered_bootstrap(s["pnl"].values, s["slug"].values, n=4000)
    return lo, hi


def run():
    full = _base(pd.read_parquet(JOINED))
    ladders = _ladders(sorted(full["symbol"].unique()))
    led = e4_ledger(full, ladders)
    print(f"E4 ledger (real L2 walk): n={len(led)} over {led['date'].nunique()} days\n")
    print(f"{'split':>8} {'n':>4} {'WR':>6} {'ask':>5} {'$/tr':>8} {'90% CI':>18} {'tot$':>8}")
    for sp in ("dev", "holdout", "future", "FULL"):
        s = led if sp == "FULL" else led[led["split"] == sp]
        if not len(s):
            continue
        lo, hi = _ci(s)
        print(f"{sp:>8} {len(s):>4} {s['won'].mean()*100:>5.1f}% {s['entry_ask'].mean():>5.2f} "
              f"${s['pnl'].mean():>+7.2f} [{lo:>+7.2f},{hi:>+7.2f}] ${s['pnl'].sum():>+7.0f}")

    # >=$10 best-ask depth subset (the realistically fillable trades)
    fl = led[led["best_depth_usd"] >= 10.0]
    print(f"\n  >=$10 best-ask depth subset: n={len(fl)} ({len(fl)/len(led)*100:.0f}% fillable)")
    for sp in ("future", "FULL"):
        s = fl if sp == "FULL" else fl[fl["split"] == sp]
        if len(s) > 5:
            lo, hi = _ci(s)
            print(f"    {sp:>7}: n={len(s)} WR={s['won'].mean()*100:.0f}% ${s['pnl'].mean():+.2f}/tr CI[{lo:+.2f},{hi:+.2f}]")

    # CPCV (fixed rule -> slice by test days)
    days = available_clean_dates("btc")
    evs = []
    for _, te in combinatorial_purged_cv(days, n_groups=6, k_test=2, embargo_days=1):
        s = led[led["date"].isin(te)]
        if len(s):
            evs.append(s["pnl"].mean())
    evs = np.array(evs)
    print(f"\n  CPCV: {len(evs)} folds, {np.mean(evs>0)*100:.0f}% positive, "
          f"EV mean ${evs.mean():+.2f} p5/p50/p95 ${np.percentile(evs,5):+.1f}/{np.percentile(evs,50):+.1f}/{np.percentile(evs,95):+.1f}")


if __name__ == "__main__":
    run()
