"""Oracle settlement-gap — the live probe's most important finding.

The engine/backtest settles every window on COINBASE spot at close
(discovery.py:118  end_price = coinbase.get_spot), but Polymarket RESOLVES ON
CHAINLINK. So `outcome_up_clean` (and every det/sq WR built on it) is a COINBASE
outcome — which can differ from the CHAINLINK outcome Polymarket actually pays,
especially on windows that close near the strike.

Surfaced live 2026-06-05: a det_lwd_live eth trade was a paper WIN (+$3.33,
Coinbase) but a real LOSS (−$10, Chainlink).

This quantifies the gap from LOCAL data (data/live_chainlink/ is collected but was
never merged into joined_15m, which is why chainlink_price was all-zeros there):
  1. per-window Coinbase outcome vs Chainlink outcome, disagreement rate by |dist|;
  2. RE-SETTLE the determinism edge on Chainlink -> the true (Polymarket) WR/EV vs
     the Coinbase-settled backtest (87.7% WR, +$1.23/tr).

Run: uv run python -m research.analysis.oracle_settlement_gap
"""
from __future__ import annotations
import glob
import os
import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
JOINED = os.path.join(REPO, "data", "research", "joined_15m.parquet")
LED = os.path.join(REPO, "data", "research", "ledgers", "det_full.parquet")


def load_chainlink() -> pd.DataFrame:
    parts = []
    for f in glob.glob(os.path.join(REPO, "data", "live_chainlink", "*.csv.gz")):
        try:
            d = pd.read_csv(f, usecols=["timestamp_ms", "symbol", "price"])
            parts.append(d)
        except Exception:
            pass
    cl = pd.concat(parts, ignore_index=True)
    cl = cl[(cl["price"] > 0) & cl["timestamp_ms"].notna()].copy()
    cl["symbol"] = cl["symbol"].str.lower()
    return cl.sort_values("timestamp_ms").reset_index(drop=True)


def asof_chainlink(cl: pd.DataFrame, queries: pd.DataFrame, t_col: str, out_col: str) -> pd.DataFrame:
    """For each (symbol, t_ms) in queries, the latest chainlink price <= t_ms."""
    out = []
    q = queries.sort_values(t_col)
    for sym, g in q.groupby("symbol"):
        c = cl[cl["symbol"] == sym][["timestamp_ms", "price"]]
        m = pd.merge_asof(g.sort_values(t_col), c, left_on=t_col, right_on="timestamp_ms",
                          direction="backward", tolerance=120_000)  # within 2 min
        out.append(m.rename(columns={"price": out_col}))
    return pd.concat(out, ignore_index=True)


def run():
    cl = load_chainlink()
    print(f"chainlink: {len(cl):,} rows, symbols={sorted(cl['symbol'].unique())}, "
          f"{cl['timestamp_ms'].min()}..{cl['timestamp_ms'].max()}")

    # Per-window summary from joined: strike (start_price), coinbase end (last cb_spot),
    # the engine's coinbase outcome, and window start/end epochs.
    j = pd.read_parquet(JOINED, columns=["slug", "symbol", "window_start_ts", "start_price",
                                         "cb_spot", "outcome_up_clean", "seconds_into_window", "date"])
    j = j[(j["start_price"] > 0) & j["cb_spot"].notna()]
    last = j.sort_values("seconds_into_window").groupby("slug", as_index=False).last()
    w = last[["slug", "symbol", "window_start_ts", "start_price", "cb_spot",
              "outcome_up_clean", "date"]].copy()
    w["symbol"] = w["symbol"].str.lower()
    w["t_start_ms"] = w["window_start_ts"] * 1000
    w["t_end_ms"] = (w["window_start_ts"] + 900) * 1000

    # asof chainlink at window start (strike basis) and end (resolution basis)
    w = asof_chainlink(cl, w, "t_start_ms", "cl_start")
    w = asof_chainlink(cl, w, "t_end_ms", "cl_end")
    w = w[w["cl_start"].notna() & w["cl_end"].notna()].copy()

    # Outcomes: Coinbase (engine) = cb_end >= strike ; Chainlink (Polymarket) = cl_end >= cl_start
    w["cb_up"] = (w["cb_spot"] >= w["start_price"]).astype(int)
    w["cl_up"] = (w["cl_end"] >= w["cl_start"]).astype(int)
    w["disagree"] = (w["cb_up"] != w["cl_up"]).astype(int)
    # closeness: |coinbase move| in bps (how near the strike it closed)
    w["dist_bps"] = (w["cb_spot"] / w["start_price"] - 1.0).abs() * 1e4

    print(f"\nwindows matched to chainlink: {len(w):,}  "
          f"OVERALL Coinbase-vs-Chainlink DISAGREEMENT: {w['disagree'].mean()*100:.2f}%")
    print("disagreement by closeness-to-strike at close (|coinbase move| bps):")
    bins = [0, 2, 5, 10, 20, 40, 1e9]
    w["db"] = pd.cut(w["dist_bps"], bins)
    g = w.groupby("db", observed=True).agg(n=("disagree", "size"), disagree=("disagree", "mean"))
    for db, r in g.iterrows():
        print(f"   {str(db):>14}: n={int(r['n']):>5}  disagree={r['disagree']*100:5.1f}%")

    # ---- RE-SETTLE the determinism edge on Chainlink ----
    det = pd.read_parquet(LED)
    det["symbol"] = det["symbol"].str.lower()
    cl_out = w[["slug", "cl_up", "cb_up", "disagree", "dist_bps"]].rename(
        columns={"dist_bps": "close_dist_bps"})  # CLOSE distance (det.dist_bps is ENTRY)
    d = det.merge(cl_out, on="slug", how="inner")
    # det 'won' is coinbase-settled (fav_won). Recompute won under chainlink:
    # det trades the favourite = the spot-implied side; side UP->needs Up, DOWN->needs Down.
    d["won_cl"] = np.where(d["fav_side"] == "yes", d["cl_up"] == 1, d["cl_up"] == 0).astype(int)
    # pnl under chainlink: won -> shares*1 - bet - fee ; lose -> -bet - fee. Reuse magnitudes.
    bet = 10.0
    shares = bet / d["fav_ask"]
    fee = 0.07 * d["fav_ask"] * (1 - d["fav_ask"]) * shares
    d["pnl_cl"] = np.where(d["won_cl"] == 1, shares - bet - fee, -bet - fee)

    print(f"\n=== DETERMINISM re-settled: Coinbase (backtest) vs Chainlink (Polymarket) ===")
    print(f"  matched det trades: {len(d)}  (of {len(det)})")
    print(f"  Coinbase-settled : WR {d['won'].mean()*100:5.1f}%  EV ${d['pnl'].mean():+.3f}/tr  total ${d['pnl'].sum():+.1f}")
    print(f"  Chainlink-settled: WR {d['won_cl'].mean()*100:5.1f}%  EV ${d['pnl_cl'].mean():+.3f}/tr  total ${d['pnl_cl'].sum():+.1f}")
    print(f"  flipped trades (Coinbase win -> Chainlink loss or v.v.): "
          f"{(d['won'] != d['won_cl']).sum()} ({(d['won']!=d['won_cl']).mean()*100:.1f}%)")
    # where do the flips concentrate?
    fl = d[d["won"] != d["won_cl"]]
    if len(fl):
        print(f"  flips by CLOSE distance: median {fl['close_dist_bps'].median():.1f}bps  "
              f"(all det median {d['close_dist_bps'].median():.1f}bps) — flips cluster where it CLOSES near strike")
        print("  re-settled det EV by ENTRY dist_bps (does a tighter entry filter avoid the gap?):")
        for lo, hi in [(5, 8), (8, 12), (12, 20), (20, 1e9)]:
            sub = d[(d["dist_bps"] >= lo) & (d["dist_bps"] < hi)]
            if len(sub):
                print(f"     entry dist [{lo},{hi}) bps: n={len(sub):>3} flip%={(sub['won']!=sub['won_cl']).mean()*100:4.1f}  "
                      f"Coinbase EV ${sub['pnl'].mean():+.2f} -> Chainlink EV ${sub['pnl_cl'].mean():+.2f}/tr")


if __name__ == "__main__":
    run()
