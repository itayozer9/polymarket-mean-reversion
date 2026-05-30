"""Phase 2 — spot->book stale-quote pickoff (mid-window).

Phase 1 captured the LAST-60s determinism edge. Phase 2 asks the broader
question: away from the close, does the Polymarket book misprice relative to an
EMPIRICAL fair value implied by spot's standardized distance-to-strike — most
of all right after a fast spot jump the 1 Hz book hasn't caught up to?

Fair value is built EMPIRICALLY (not the Gaussian sigma-model, which prior work
found broken on jumpy crypto): P(Up | z) where z = signed distance-to-strike in
units of expected remaining move. The curve is FIT on dev-early and APPLIED to
dev-late + hold-out, so we never fit and bet on the same data.

Rule: when |model_P(Up) - market yes_mid| exceeds a margin (optionally gated on a
recent spot jump), bet the model's side as a taker and hold to resolution. One
trade per window, mid-window only (exclude the last 60s = Phase 1's turf).

Run: uv run python -m research.analysis.stale_quote_pickoff
"""
from __future__ import annotations
import os
import numpy as np
import pandas as pd

from research.sim.fills_v2 import walk_buy, settle_pnl
from research.dataset.feeds import load_l2_ladders
from research.lib.stats import window_clustered_bootstrap
from research.clean_window import CLEAN_START, available_clean_dates

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
JOINED = os.path.join(REPO, "data", "research", "joined_15m.parquet")
STAKE = 10.0
_LV = range(1, 11)


def _prep(df):
    df = df[df["book_healthy"] & df["outcome_up_clean"].notna()
            & df["cb_spot"].notna() & (df["start_price"] > 0)].copy()
    # z = signed distance-to-strike / expected remaining move (both in bps)
    vol_bps = df["realized_vol"].to_numpy("f8") * 100.0  # move_pct(%)->bps per sec
    tleft = np.clip(df["time_left_sec"].to_numpy("f8"), 1, None)
    exp_move = vol_bps * np.sqrt(tleft)
    z = np.where(exp_move > 1e-6, df["dist_strike_bps"].to_numpy("f8") / exp_move, np.nan)
    df["z"] = z
    return df


def fit_curve(train, n_bins=25):
    """Empirical P(Up | z): binned mean outcome on training windows. Returns
    (z_centers, p_up) for monotone interpolation."""
    t = train[np.isfinite(train["z"])].copy()
    # clip z to a sane range; bin by quantile for balanced support
    t["z"] = t["z"].clip(-6, 6)
    qs = np.quantile(t["z"], np.linspace(0, 1, n_bins + 1))
    qs = np.unique(qs)
    t["zb"] = pd.cut(t["z"], qs, include_lowest=True)
    g = t.groupby("zb", observed=True).agg(zc=("z", "mean"), p=("outcome_up_clean", "mean"))
    g = g.dropna().sort_values("zc")
    # enforce monotonic non-decreasing (P(Up) rises with z) via isotonic-ish cummax
    p = np.maximum.accumulate(g["p"].to_numpy())
    return g["zc"].to_numpy(), p


def model_p_up(z, zc, p):
    return np.interp(np.clip(z, -6, 6), zc, p, left=p[0], right=p[-1])


def _ladders(symbols):
    dates = available_clean_dates("btc")
    d1 = dates[-1] if dates else CLEAN_START
    return {s: load_l2_ladders(s, CLEAN_START, d1) for s in symbols}


def backtest(df, ladders, zc, p, margin, jump_bps=None,
             t_lo=60, t_hi=840, latency=2):
    """One trade/window. Enter at first tick (mid-window) where the model
    disagrees with the market by >= margin (and |spot_vel_10s| >= jump_bps if
    given). Buy the model's side (taker), hold to resolution."""
    d = df[(df["time_left_sec"] >= t_lo) & (df["time_left_sec"] <= t_hi)
           & np.isfinite(df["z"])].copy()
    d["model_p"] = model_p_up(d["z"].to_numpy(), zc, p)
    d["mis"] = d["model_p"] - d["yes_mid"]          # >0 => model says YES underpriced
    d = d[d["mis"].abs() >= margin]
    if jump_bps is not None:
        d = d[d["spot_vel_10s_bps"].abs() >= jump_bps]
    if d.empty:
        return np.array([]), np.array([]), np.array([])
    d = d.sort_values(["slug", "seconds_into_window"])
    first = d.groupby("slug", as_index=False).first()
    pnls, grp, wins = [], [], []
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
        if buy_yes:
            px = [lr[f"ask_px_{i}"] for i in _LV]; sz = [lr[f"ask_sz_{i}"] for i in _LV]
            won = r["outcome_up_clean"] == 1
        else:
            px = [1.0 - lr[f"bid_px_{i}"] for i in _LV]; sz = [lr[f"bid_sz_{i}"] for i in _LV]
            won = r["outcome_up_clean"] == 0
        fill = walk_buy(px, sz, STAKE)
        if not fill.filled or fill.unfilled_usd > STAKE * 0.5:
            continue
        pnls.append(settle_pnl(fill, bool(won))); grp.append(r["slug"]); wins.append(bool(won))
    return np.array(pnls), np.array(grp), np.array(wins)


def run():
    df = _prep(pd.read_parquet(JOINED))
    train = df[df["date"] <= "2026-05-24"]
    dev_late = df[(df["date"] >= "2026-05-25") & (df["split"] == "dev")]
    hold = df[df["split"] == "holdout"]
    zc, p = fit_curve(train)
    print(f"Phase 2 — stale-quote pickoff. fit on {len(train):,} early-dev ticks; "
          f"curve P(Up|z): z={np.round(zc[::5],2)} p={np.round(p[::5],2)}")
    ladders = _ladders(sorted(df["symbol"].unique()))

    for label, d in (("dev-late(25-27)", dev_late), ("HOLD-OUT(28-29)", hold)):
        print(f"\n=== {label} ===")
        print(f"{'margin':>7} {'jump':>6} {'trades':>7} {'WR':>6} {'$/trade':>9} {'90% CI':>20}")
        for margin in (0.05, 0.08, 0.12):
            for jump in (None, 8, 15):
                pnl, grp, wins = backtest(d, ladders, zc, p, margin, jump_bps=jump)
                if len(pnl) < 15:
                    continue
                lo, _, hi = window_clustered_bootstrap(pnl, grp, n=2000)
                flag = "  <==" if lo > 0 else ""
                js = "none" if jump is None else str(jump)
                print(f"{margin:>7.2f} {js:>6} {len(pnl):>7} {wins.mean():>6.3f} "
                      f"${pnl.mean():>+8.3f} [{lo:>+6.3f},{hi:>+6.3f}]{flag}")


if __name__ == "__main__":
    run()
