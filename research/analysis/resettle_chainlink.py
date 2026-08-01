"""Re-settle the edges on CHAINLINK (what Polymarket actually pays) vs the COINBASE
backtest. Produces the TRUE per-split WR/EV for det, sq, and E4. See
docs/research and memory [[backtest-settles-coinbase-not-chainlink]].

Run: uv run python -m research.analysis.resettle_chainlink
"""
from __future__ import annotations
import os
import numpy as np
import pandas as pd

from research.lib.stats import window_clustered_bootstrap
from research.analysis.oracle_settlement_gap import load_chainlink, asof_chainlink, JOINED, REPO

LED = os.path.join(REPO, "data", "research", "ledgers")
BET, FEE = 10.0, 0.07


def chainlink_outcome_by_slug() -> pd.DataFrame:
    """slug -> chainlink Up/Down outcome (cl_end >= cl_start), + the coinbase one."""
    cl = load_chainlink()
    j = pd.read_parquet(JOINED, columns=["slug", "symbol", "window_start_ts", "start_price",
                                         "cb_spot", "seconds_into_window"])
    j = j[j["start_price"] > 0]
    w = j.sort_values("seconds_into_window").groupby("slug", as_index=False).last()
    w["symbol"] = w["symbol"].str.lower()
    w["t_start_ms"] = w["window_start_ts"] * 1000
    w["t_end_ms"] = (w["window_start_ts"] + 900) * 1000
    w = asof_chainlink(cl, w, "t_start_ms", "cl_start")
    w = asof_chainlink(cl, w, "t_end_ms", "cl_end")
    w = w[w["cl_start"].notna() & w["cl_end"].notna()].copy()
    w["cl_up"] = (w["cl_end"] >= w["cl_start"]).astype(int)
    return w[["slug", "cl_up"]]


def _resettle(led: pd.DataFrame, cl: pd.DataFrame, buy_side_col: str) -> pd.DataFrame:
    d = led.merge(cl, on="slug", how="inner").copy()
    yes = d[buy_side_col].isin(["yes", "UP", "up"])
    d["won_cl"] = np.where(yes, d["cl_up"] == 1, d["cl_up"] == 0).astype(int)
    ask = d["entry_ask"] if "entry_ask" in d.columns else d["fav_ask"]
    shares = BET / ask
    fee = FEE * ask * (1 - ask) * shares
    d["pnl_cl"] = np.where(d["won_cl"] == 1, shares - BET - fee, -BET - fee)
    return d


def _report(name: str, d: pd.DataFrame):
    print(f"\n=== {name}: Coinbase backtest vs Chainlink (Polymarket-true) ===")
    print(f"{'split':>8} {'n':>5} | {'CB WR':>6} {'CB EV':>8} | {'CL WR':>6} {'CL EV':>8} {'CL 90%CI':>16} | {'flip%':>5}")
    for sp in ("dev", "holdout", "future", "FULL"):
        s = d if sp == "FULL" else d[d["split"] == sp]
        if not len(s):
            continue
        lo, _, hi = window_clustered_bootstrap(s["pnl_cl"].values, s["slug"].values, n=3000)
        flip = (s["won"] != s["won_cl"]).mean() * 100 if "won" in s else float("nan")
        cbwr = s["won"].mean()*100 if "won" in s else float("nan")
        print(f"{sp:>8} {len(s):>5} | {cbwr:>5.1f}% ${s['pnl'].mean() if 'pnl' in s else 0:>+6.2f} | "
              f"{s['won_cl'].mean()*100:>5.1f}% ${s['pnl_cl'].mean():>+6.2f} [{lo:>+5.2f},{hi:>+5.2f}] | {flip:>4.0f}")


def run():
    cl = chainlink_outcome_by_slug()
    print(f"chainlink outcomes resolved for {len(cl):,} windows")

    det = pd.read_parquet(os.path.join(LED, "det_full.parquet"))
    _report("DETERMINISM (det_lwd)", _resettle(det, cl, "fav_side"))

    sq = pd.read_parquet(os.path.join(LED, "sq_full.parquet"))
    _report("STALE-QUOTE (det_sqp)", _resettle(sq, cl, "symbol_buy"))

    # E4: rebuild with the buy-side persisted, then re-settle.
    from research.analysis.e4_verify import e4_ledger
    from research.analysis.loss_patterns import _base, _ladders
    full = _base(pd.read_parquet(JOINED))
    e4 = e4_ledger(full, _ladders(sorted(full["symbol"].unique())))
    # e4 buys the SPOT-implied side: yes if the window's dist>0 at entry. Recover sign by
    # re-deriving from joined's dist_strike_bps at the entry tick is overkill; instead the
    # E4 trade WON iff bought-side matched coinbase outcome -> bought = coinbase_up if won else !.
    # Cleaner: bought_yes = (won == coinbase_up). We have won; get coinbase_up per slug.
    jc = pd.read_parquet(JOINED, columns=["slug", "outcome_up_clean"]).groupby("slug").first().reset_index()
    e4 = e4.merge(jc, on="slug", how="left")
    e4["bought_yes"] = (e4["won"] == e4["outcome_up_clean"]).astype(int)
    e4["fav_side"] = np.where(e4["bought_yes"] == 1, "yes", "no")
    _report("E4 (disagreement-determinism)", _resettle(e4, cl, "fav_side"))


if __name__ == "__main__":
    run()
