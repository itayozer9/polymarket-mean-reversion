"""Full-window per-trade ledgers for both live edges, at PARITY with the live
engine rules, over the ENTIRE clean window (all days; `split` preserved).

Why a new builder (vs loss_patterns.build_ledgers, which only emits dev+holdout):
  - the user wants every test on the FULL ~2-week window;
  - loss_patterns.sq_ledger is NOT exact parity — it omits the live
    `max_mispricing=0.30` cap and the `[min_ask,max_ask]` band. This builder
    matches StaleQuoteState exactly and uses the LIVE FROZEN curve
    (data/research/stale_quote_curve.json — the exact curve the bot trades).

Parity rules reproduced (from strategies.yaml):
  - determinism (det_lwd_v1): last 1..60s, |dist|>=5bps, consistent, fav ask in
    [0.50,0.90], hold to resolution. (reuses loss_patterns.det_ledger)
  - stale_quote (det_sqp_v1): t_left in [60,840], frozen curve, margin<=|mis|<=
    0.30, |vel_10s|>=8bps, fav ask in [0.05,0.95], hold to resolution.

Fills walk the real L2 ladder (walk_buy) — MORE realistic than the live paper
engine's best-ask fill. That fill-model gap (top_ask vs entry_ask here) is kept
per-trade for the Phase 4 paper->live execution audit.

Outputs: data/research/ledgers/{det,sq}_full.parquet
Run: uv run python -m research.analysis.build_full_ledgers
"""
from __future__ import annotations
import json
import os
import numpy as np
import pandas as pd

from research.sim.fills_v2 import walk_buy, settle_pnl
from research.clean_window import split_of
from research.analysis.loss_patterns import (
    _base, _sq_prep, _macro_stress, _prev_fav_lost, det_ledger, _ladders,
    JOINED, STAKE,
)

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CURVE = os.path.join(REPO, "data", "research", "stale_quote_curve.json")
LEDGERS = os.path.join(REPO, "data", "research", "ledgers")
_LV = range(1, 11)


def _load_curve():
    j = json.load(open(CURVE))
    return np.asarray(j["z"], "f8"), np.asarray(j["p_up"], "f8")


def sq_ledger_parity(df, ladders, zc, p, margin=0.08, max_mis=0.30, jump_bps=8.0,
                     t_lo=60, t_hi=840, min_ask=0.05, max_ask=0.95, latency=2):
    """Stale-quote ledger at EXACT live parity (incl. max_mispricing + ask band)."""
    d = df[(df["time_left_sec"] >= t_lo) & (df["time_left_sec"] <= t_hi)
           & np.isfinite(df["z"])].copy()
    d["model_p"] = np.interp(np.clip(d["z"], -6, 6), zc, p, left=p[0], right=p[-1])
    d["mis"] = d["model_p"] - d["yes_mid"]
    am = d["mis"].abs()
    d = d[(am >= margin) & (am <= max_mis)]                     # parity: max_mispricing cap
    d = d[d["spot_vel_10s_bps"].abs() >= jump_bps]
    if d.empty:
        return pd.DataFrame()
    first = d.sort_values(["slug", "seconds_into_window"]).groupby("slug", as_index=False).first()
    rows = []
    for _, r in first.iterrows():
        lad = ladders.get(r["symbol"])
        if lad is None:
            continue
        sec = int(r["seconds_into_window"]) + latency
        try:
            lr = lad.loc[(r["slug"], sec)]
        except KeyError:
            continue
        if isinstance(lr, pd.DataFrame):
            lr = lr.iloc[0]
        buy_yes = r["mis"] > 0
        top_ask = float(lr["ask_px_1"]) if buy_yes else 1.0 - float(lr["bid_px_1"])
        if not (min_ask <= top_ask <= max_ask):                # parity: ask band on quoted top-of-book
            continue
        if buy_yes:
            px = [lr[f"ask_px_{i}"] for i in _LV]; sz = [lr[f"ask_sz_{i}"] for i in _LV]
            won = r["outcome_up_clean"] == 1
        else:
            px = [1.0 - lr[f"bid_px_{i}"] for i in _LV]; sz = [lr[f"bid_sz_{i}"] for i in _LV]
            won = r["outcome_up_clean"] == 0
        fill = walk_buy(px, sz, STAKE)
        if not fill.filled or fill.unfilled_usd > STAKE * 0.5:
            continue
        won = bool(won)
        rows.append(dict(
            slug=r["slug"], symbol=r["symbol"], date=str(r["date"]), split=str(r["split"]),
            window_start_ts=int(r["window_start_ts"]), entry_sec=int(r["seconds_into_window"]),
            pnl=settle_pnl(fill, won), won=int(won), model_p=float(r["model_p"]),
            mis=float(r["mis"]), abs_mis=float(abs(r["mis"])), z=float(r["z"]),
            entry_ask=float(fill.avg_price), top_ask=float(top_ask),
            slippage=float(fill.avg_price - top_ask),
            dist_bps=float(r["abs_dist_bps"]),
            depth_usd=float(r["depth_usd"]) if pd.notna(r["depth_usd"]) else np.nan,
            spot_vel_10s=float(r["spot_vel_10s_bps"]), time_left=int(r["time_left_sec"]),
            utc_hour=int(r["utc_hour"]), dow=int(r["dow"]),
            symbol_buy=("yes" if buy_yes else "no"),
        ))
    return pd.DataFrame(rows)


def build():
    full = _base(pd.read_parquet(JOINED))
    macro, thr = _macro_stress(full)
    ladders = _ladders(sorted(full["symbol"].unique()))
    zc, p = _load_curve()
    sqf = _sq_prep(full)

    det = det_ledger(full, ladders)                            # all days, parity 60/5/0.90
    det["split"] = det["date"].map(split_of)
    det["n_coins_volatile"] = det["window_start_ts"].map(macro).fillna(0).astype(int)
    det = _prev_fav_lost(det)

    sq = sq_ledger_parity(sqf, ladders, zc, p)
    if len(sq):
        sq["n_coins_volatile"] = sq["window_start_ts"].map(macro).fillna(0).astype(int)

    os.makedirs(LEDGERS, exist_ok=True)
    det.to_parquet(os.path.join(LEDGERS, "det_full.parquet"))
    sq.to_parquet(os.path.join(LEDGERS, "sq_full.parquet"))

    print(f"macro hot-vel thr={thr:.2f} bps  |  days={sorted(det['date'].unique())}")
    for name, l in (("determinism", det), ("stale_quote", sq)):
        if not len(l):
            print(f"  {name}: 0 trades"); continue
        print(f"\n  {name}: n={len(l)}  WR={l['won'].mean()*100:.1f}%  "
              f"${l['pnl'].mean():+.3f}/tr  total=${l['pnl'].sum():+.1f}")
        for sp in ("dev", "holdout", "future"):
            ls = l[l["split"] == sp]
            if len(ls):
                print(f"     {sp:8}: n={len(ls):>4}  WR={ls['won'].mean()*100:5.1f}%  "
                      f"${ls['pnl'].mean():+.3f}/tr  total=${ls['pnl'].sum():+8.1f}  "
                      f"days={ls['date'].nunique()}")
    return det, sq


if __name__ == "__main__":
    build()
