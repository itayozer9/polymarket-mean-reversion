"""edge_lab — ONE shared, correct harness so every edge (existing + new) is judged
identically and comparably:

  filter -> first qualifying tick per window -> fill at best-ask at (entry_sec +
  latency) -> Chainlink resettle (the oracle Polymarket pays) -> window-clustered
  CIs per split -> latency-survival sweep ("assume we are NOT the fastest") ->
  CPCV positive-fraction -> daily Deflated Sharpe.

A new edge only supplies an ENTRY FILTER (which ticks qualify) and a BUY SIDE
(buy_yes bool). Everything downstream is this module, so results are comparable
across edges and hard to fake.

Fill model (joined-only, fast — for the broad hunt): buy the chosen side at its
best ask in the joined book at (entry_sec + latency); require the fill-tick book
to be healthy with best-ask depth >= stake (else the trade is DROPPED — the
adverse-selection penalty for being slow). Survivors get re-validated with the
full L2 ladder-walk (loss_patterns / e4_verify).

Economics match research.analysis.resettle_chainlink exactly (single taker fee
0.07*p*(1-p)*shares, hold to resolution) so numbers reconcile with the baseline.

Latency-survival is the user's hard gate: a home trader has NO speed edge, so an
edge that only pays at latency<=2s but dies by 5s is "real but not ours."
"""
from __future__ import annotations
import functools
import os

import numpy as np
import pandas as pd

from research.analysis.loss_patterns import _base, JOINED, STAKE
from research.analysis.resettle_chainlink import chainlink_outcome_by_slug
from research.lib.stats import window_clustered_bootstrap
from research.lib.rigor import (
    combinatorial_purged_cv, deflated_sharpe_ratio, daily_pnl_from_ledger)
from research.clean_window import available_clean_dates

FEE = 0.07
LATENCIES = (2, 3, 5, 10)
_DEC_COLS = ["slug", "symbol", "date", "split", "window_start_ts", "entry_sec", "buy_yes"]

# Slim prepped frame — only the columns edges actually need, so parallel workflow
# agents (and column-subset reads) stay memory-light. Built from _base once.
SLIM = JOINED.replace("joined_15m.parquet", "joined_15m_slim.parquet")
SLIM_COLS = [
    "slug", "symbol", "date", "split", "window_start_ts", "seconds_into_window",
    "time_left_sec", "yes_best_bid", "yes_best_ask", "yes_bid_depth", "yes_ask_depth",
    "no_best_bid", "no_best_ask", "no_ask_depth", "yes_mid", "spread_yes", "book_healthy",
    "dist_strike_bps", "abs_dist_bps", "cb_spot", "start_price", "chainlink_price",
    "cl_cb_basis_bps", "coinbase_price", "outcome_up_clean", "end_price", "realized_vol",
    "spot_vel_3s_bps", "spot_vel_10s_bps", "spot_vel_30s_bps", "spot_move_10s", "spot_move_30s",
    "microprice", "l2_imbalance", "l2_ask_depth", "l2_best_ask", "l2_depth_ask_2c",
    "tr_signed_usd", "tr_signed_5s", "tr_bear_10s", "tr_n", "tr_n_10s",
    "fav_side", "fav_ask", "fav_won", "consistent", "adverse_vel_10s", "adverse_vel_3s",
    "utc_hour", "dow", "depth_usd",
]


def build_slim() -> str:
    """Write the slim prepped frame (subset of _base columns)."""
    b = _base(pd.read_parquet(JOINED))
    cols = [c for c in SLIM_COLS if c in b.columns]
    b[cols].to_parquet(SLIM, index=False)
    return SLIM


@functools.lru_cache(maxsize=1)
def load_base() -> pd.DataFrame:
    """Canonical prepped per-tick frame (book_healthy, fav_side/ask, dist, vel,
    hour/dow, chainlink_price/basis). Prefers the slim frame if present (memory-
    light); falls back to building from the full chainlink-augmented joined_15m."""
    if os.path.exists(SLIM):
        return pd.read_parquet(SLIM)
    return _base(pd.read_parquet(JOINED))


@functools.lru_cache(maxsize=1)
def cl_outcomes() -> pd.DataFrame:
    """slug -> cl_up (Chainlink Up/Down — the Polymarket-true outcome)."""
    return chainlink_outcome_by_slug()


@functools.lru_cache(maxsize=1)
def _book_index() -> pd.DataFrame:
    b = load_base()
    cols = ["slug", "seconds_into_window", "yes_best_ask", "yes_best_bid",
            "yes_ask_depth", "no_ask_depth", "book_healthy"]
    return b[cols].drop_duplicates(["slug", "seconds_into_window"]).reset_index(drop=True)


def first_tick(cand: pd.DataFrame, buy_yes) -> pd.DataFrame:
    """Reduce gated ticks to ONE decision row per window (first qualifying tick).
    buy_yes: bool array/Series aligned to `cand`."""
    cand = cand.copy()
    cand["buy_yes"] = np.asarray(buy_yes)
    first = (cand.sort_values(["slug", "seconds_into_window"])
             .groupby("slug", as_index=False).first())
    first = first.rename(columns={"seconds_into_window": "entry_sec"})
    keep = [c for c in _DEC_COLS if c in first.columns]
    return first[keep]


def simulate(decision: pd.DataFrame, *, latency: int = 2, stake: float = STAKE) -> pd.DataFrame:
    """Chainlink-settled ledger from a decision frame (one row/window with
    slug, symbol, date, split, window_start_ts, entry_sec, buy_yes)."""
    if decision is None or decision.empty:
        return pd.DataFrame()
    d = decision.dropna(subset=["entry_sec"]).copy()
    d["entry_sec"] = d["entry_sec"].astype(int)
    d["fill_sec"] = d["entry_sec"] + int(latency)
    d = d.drop(columns=[c for c in ("seconds_into_window",) if c in d.columns])
    bk = _book_index()
    m = d.merge(bk, left_on=["slug", "fill_sec"],
                right_on=["slug", "seconds_into_window"], how="left")
    buy_yes = m["buy_yes"].astype(bool).to_numpy()
    ask = np.where(buy_yes, m["yes_best_ask"].to_numpy("f8"),
                   1.0 - m["yes_best_bid"].to_numpy("f8"))
    depth_sh = np.where(buy_yes, m["yes_ask_depth"].to_numpy("f8"),
                        m["no_ask_depth"].to_numpy("f8"))
    depth_usd = depth_sh * ask
    ok = ((m["book_healthy"] == True).to_numpy() & np.isfinite(ask)
          & (ask > 0.01) & (ask < 0.99) & (depth_usd >= stake))
    m = m[ok].copy()
    if m.empty:
        return m
    m["entry_ask"] = ask[ok]
    m = m.merge(cl_outcomes(), on="slug", how="inner")
    by = m["buy_yes"].astype(bool).to_numpy()
    m["won"] = np.where(by, m["cl_up"].to_numpy() == 1, m["cl_up"].to_numpy() == 0).astype(int)
    a = m["entry_ask"].to_numpy("f8")
    shares = stake / a
    fee = FEE * a * (1 - a) * shares
    m["pnl"] = np.where(m["won"] == 1, shares - stake - fee, -stake - fee)
    return m


# --------------------------------------------------------------------------
# evaluation
# --------------------------------------------------------------------------
def _split_ci(led: pd.DataFrame, n: int = 3000) -> dict:
    out = {}
    for sp in ("dev", "holdout", "future", "FULL"):
        s = led if sp == "FULL" else led[led["split"] == sp]
        if len(s) < 3:
            out[sp] = None
            continue
        lo, _, hi = window_clustered_bootstrap(s["pnl"].values, s["slug"].values, n=n)
        out[sp] = dict(n=int(len(s)), wr=round(float(s["won"].mean() * 100), 1),
                       ev=round(float(s["pnl"].mean()), 2), lo=round(float(lo), 2),
                       hi=round(float(hi), 2), total=round(float(s["pnl"].sum()), 1))
    return out


def _cpcv(led: pd.DataFrame) -> dict:
    days = available_clean_dates("btc")
    evs = [led[led["date"].isin(te)]["pnl"].mean()
           for _, te in combinatorial_purged_cv(days, n_groups=6, k_test=2, embargo_days=1)
           if len(led[led["date"].isin(te)])]
    if not evs:
        return dict(folds=0, pct_pos=float("nan"))
    evs = np.array(evs)
    return dict(folds=int(len(evs)), pct_pos=round(float(np.mean(evs > 0) * 100), 0),
                ev_mean=round(float(evs.mean()), 2), p5=round(float(np.percentile(evs, 5)), 1),
                p50=round(float(np.percentile(evs, 50)), 1), p95=round(float(np.percentile(evs, 95)), 1))


def latency_survival(decision: pd.DataFrame, latencies=LATENCIES, stake: float = STAKE) -> dict:
    """FULL + fresh-OOS Chainlink EV at each fill latency. The user's hard gate:
    EV must survive at >=3-5s (we are never the fastest)."""
    out = {}
    for lat in latencies:
        led = simulate(decision, latency=lat, stake=stake)
        if len(led) >= 3:
            lo, _, hi = window_clustered_bootstrap(led["pnl"].values, led["slug"].values, n=2000)
            fut = led[led["split"] == "future"]
            out[lat] = dict(n=int(len(led)), ev=round(float(led["pnl"].mean()), 2),
                            lo=round(float(lo), 2), hi=round(float(hi), 2),
                            fut_ev=round(float(fut["pnl"].mean()), 2) if len(fut) else None)
        else:
            out[lat] = dict(n=int(len(led)), ev=None)
    return out


def evaluate(led: pd.DataFrame, n_trials: int = 20) -> dict:
    """Full verdict for a Chainlink-settled ledger."""
    if led is None or len(led) == 0:
        return dict(n=0)
    dp = daily_pnl_from_ledger(led)
    dsr = deflated_sharpe_ratio(dp.values, n_trials=n_trials)
    return dict(n=int(len(led)), wr=round(float(led["won"].mean() * 100), 1),
                ev=round(float(led["pnl"].mean()), 2), total=round(float(led["pnl"].sum()), 1),
                per_split=_split_ci(led), cpcv=_cpcv(led),
                dsr={k: (round(v, 3) if isinstance(v, float) else v) for k, v in dsr.items()})


def verdict_line(name: str, led: pd.DataFrame, lat: dict | None = None) -> str:
    e = evaluate(led)
    if e["n"] == 0:
        return f"{name:28} n=0 (no fills)"
    fu = e["per_split"].get("future")
    fl = e["per_split"]["FULL"]
    s = (f"{name:28} n={e['n']:>4} FULL ${fl['ev']:+.2f}[{fl['lo']:+.2f},{fl['hi']:+.2f}] "
         f"WR{fl['wr']:.0f}% | future " +
         (f"${fu['ev']:+.2f}[{fu['lo']:+.2f},{fu['hi']:+.2f}]n{fu['n']}" if fu else "n/a") +
         f" | CPCV {e['cpcv'].get('pct_pos','?'):.0f}%+ | DSR {e['dsr']['dsr']}")
    if lat:
        s += "\n    latency EV: " + "  ".join(
            f"{k}s ${v['ev']:+.2f}" if v.get("ev") is not None else f"{k}s -" for k, v in lat.items())
    return s


# --------------------------------------------------------------------------
# self-validation: reproduce the resettle_chainlink baseline (E4 ~+$16.7 FULL,
# det ~+$0.9 FULL) with the joined-only fill, to prove the harness is sound.
# --------------------------------------------------------------------------
def _validate():
    b = load_base()
    # E4: last<=60s, |dist|>=5, book DISAGREES with spot -> buy the spot-implied side
    e4c = b[(b["time_left_sec"] >= 1) & (b["time_left_sec"] <= 60)
            & (b["abs_dist_bps"] >= 5) & (~b["consistent"])]
    e4d = first_tick(e4c, (e4c["dist_strike_bps"] > 0).to_numpy())
    e4l = simulate(e4d, latency=2)
    print(verdict_line("E4 disagree (reproduce)", e4l, latency_survival(e4d)))

    # det_lwd: last<=60s, |dist|>=5, AGREE, fav_ask in [0.5,0.9] -> buy favourite
    dc = b[(b["time_left_sec"] >= 1) & (b["time_left_sec"] <= 60) & (b["abs_dist_bps"] >= 5)
           & (b["consistent"]) & (b["fav_ask"].between(0.50, 0.90))]
    dd = first_tick(dc, (dc["yes_mid"] >= 0.5).to_numpy())
    dl = simulate(dd, latency=2)
    print(verdict_line("det_lwd (reproduce)", dl, latency_survival(dd)))
    print("\n(reference resettle baseline: E4 FULL +$16.74, det FULL +$0.88)")


if __name__ == "__main__":
    _validate()
