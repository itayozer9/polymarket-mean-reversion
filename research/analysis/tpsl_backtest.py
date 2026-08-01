"""#4/#6 — backtest the favourite-value edges with TAKE-PROFIT / STOP-LOSS exits
(not hold-to-resolution). Walks the per-tick favourite price path after entry; exits
at the favourite's BID when TP/SL triggers, else holds to Chainlink resolution.

Directly tests the user's concern: deep favourites risk ~$10 to win ~$1, and a
reverting favourite can settle as a near-strike loss — does a stop cut that tail?
Non-latency: TP/SL act at 1s granularity, never a sub-second race.

Run: uv run python -m research.analysis.tpsl_backtest
"""
from __future__ import annotations
import functools

import numpy as np
import pandas as pd

from research.analysis import edge_lab as L
from research.analysis.verify_survivors import wildcard, pricestruct, momentum
from research.lib.rigor import daily_pnl_from_ledger, max_drawdown, longest_losing_streak

FEE = L.FEE


@functools.lru_cache(maxsize=1)
def _paths():
    """slug -> ndarray rows (sec, yes_mid, yes_bid, yes_ask) sorted by sec."""
    b = L.load_base()[["slug", "seconds_into_window", "yes_mid", "yes_best_bid", "yes_best_ask"]]
    out = {}
    for slug, g in b.sort_values("seconds_into_window").groupby("slug", sort=False):
        out[slug] = g[["seconds_into_window", "yes_mid", "yes_best_bid", "yes_best_ask"]].to_numpy("f8")
    return out


def _tpsl_pnl(row, tp, sl, stake=10.0):
    """One trade's PnL under (tp, sl) exits. tp/sl are price moves from entry of the
    FAVOURITE value (e.g. tp=0.08 -> exit if fav mid rises 8c; sl=0.12 -> exit if it
    falls 12c). None disables that leg. Falls back to Chainlink resolution."""
    paths = _paths().get(row["slug"])
    entry = float(row["entry_ask"])
    buy_yes = bool(row["buy_yes"])
    shares = stake / entry
    fee_in = FEE * entry * (1 - entry) * shares
    if paths is not None:
        after = paths[paths[:, 0] > row["entry_sec"]]
        for sec, ymid, ybid, yask in after:
            fav_mid = ymid if buy_yes else 1.0 - ymid
            fav_bid = ybid if buy_yes else (1.0 - yask)   # sell the favourite -> hit its bid
            if not np.isfinite(fav_mid) or not (0 < fav_bid < 1):
                continue
            hit_tp = tp is not None and fav_mid >= min(entry + tp, 0.995)
            hit_sl = sl is not None and fav_mid <= entry - sl
            if hit_tp or hit_sl:
                fee_out = FEE * fav_bid * (1 - fav_bid) * shares
                return fav_bid * shares - stake - fee_in - fee_out
    # no trigger -> Chainlink resolution (won -> $1, lost -> $0, no redemption fee)
    return (shares - stake - fee_in) if row["won"] else (-stake - fee_in)


def _ledger(dec, cl):
    d = dec.merge(cl, on="slug", how="inner").copy()
    by = d["buy_yes"].astype(bool).to_numpy()
    d["won"] = np.where(by, d["cl_up"].to_numpy() == 1, d["cl_up"].to_numpy() == 0).astype(int)
    # entry_ask from the book at entry tick (reuse edge_lab fill: depth-gated)
    led = L.simulate(dec, latency=2)
    d = d.merge(led[["slug", "entry_ask"]], on="slug", how="inner")
    return d


def _stats(pnls, dates, label):
    s = pd.DataFrame({"pnl": pnls, "date": dates})
    dp = s.groupby("date")["pnl"].sum().sort_index()
    wr = (np.asarray(pnls) > 0).mean() * 100
    return (f"  {label:22} n={len(pnls):>3} EV ${np.mean(pnls):>+5.2f}/tr WR {wr:>4.0f}%  "
            f"total ${np.sum(pnls):>+6.0f}  worst-day ${dp.min():>+6.1f}  maxDD ${max_drawdown(np.cumsum(dp.values)):>+6.1f}")


def run():
    b = L.load_base()
    cl = L.cl_outcomes()
    # combined fav-value book (the 3 buy-favourite slices), deduped
    decs = [wildcard(b), pricestruct(b), momentum(b)]
    uni = pd.concat(decs, ignore_index=True).drop_duplicates("slug", keep="first")
    led = _ledger(uni, cl)
    led = led[led["entry_ask"].notna()].reset_index(drop=True)
    dates = led["date"].values
    print(f"=== TP/SL on the combined favourite-value book (n={len(led)}, Chainlink) ===")
    print(_stats([_tpsl_pnl(r, None, None) for _, r in led.iterrows()], dates, "hold-to-resolution"))
    for sl in (0.10, 0.15, 0.20):
        print(_stats([_tpsl_pnl(r, None, sl) for _, r in led.iterrows()], dates, f"SL -{sl:.2f} only"))
    for tp, sl in [(0.06, 0.12), (0.08, 0.15), (0.05, 0.10)]:
        print(_stats([_tpsl_pnl(r, tp, sl) for _, r in led.iterrows()], dates, f"TP +{tp:.2f} / SL -{sl:.2f}"))


if __name__ == "__main__":
    run()
