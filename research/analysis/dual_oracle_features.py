"""Dual-oracle entry features — reconstruct the CHAINLINK view at entry time.

The determinism edge fires on a COINBASE signal (move_pct = (cb_spot-strike)/strike)
but Polymarket settles on CHAINLINK. Near the strike the two oracles disagree, and a
disagreement flips an expected +$1 favourite-win into a -$5 loss. To gate those out we
need, at the ENTRY tick, the Chainlink distance-from-strike and whether Chainlink agrees
with Coinbase on direction.

Neither `cl_start` (Chainlink price at window open) nor `cl_spot` (Chainlink at the entry
tick) is persisted in the tick data, so we reconstruct both by asof-merging the live
Chainlink feed (`data/live_chainlink/`) onto the decision frame — exactly the reconstruction
`oracle_settlement_gap.run()` already does for window open/close, extended to the entry tick
plus the oracle staleness.

`entry_cl_dist(decision, base)` takes an edge_lab decision frame (one row per window from
`first_tick`: slug, symbol, window_start_ts, entry_sec, buy_yes) and returns it augmented with:
  cl_start, cl_spot, cl_dist_bps, cl_oracle_age_s, cb_dist_bps (if base given), oracle_agree,
  cl_available.

Chainlink data exists only from ~2026-05-15 (live) so rows before that, or with no Chainlink
within tolerance, get cl_available=False (honest n-loss; do NOT silently impute).
"""
from __future__ import annotations
import glob
import os

import numpy as np
import pandas as pd

from research.analysis.oracle_settlement_gap import REPO, load_chainlink, asof_chainlink

TOL_MS = 120_000  # 2-min asof tolerance, matches oracle_settlement_gap / ChainlinkPriceCache


def load_chainlink_aged() -> pd.DataFrame:
    """Chainlink feed with oracle staleness: timestamp_ms, symbol, price, oracle_age_s.

    `oracle_age_s` = poll_time - feed.updatedAt — how stale the on-chain answer was when we
    read it. Flips may concentrate when the oracle is stale, so we carry it for bucketing."""
    parts = []
    for f in glob.glob(os.path.join(REPO, "data", "live_chainlink", "*.csv.gz")):
        try:
            d = pd.read_csv(f, usecols=["timestamp_ms", "symbol", "price", "oracle_age_s"])
            parts.append(d)
        except Exception:
            # older files may lack oracle_age_s — fall back to price-only with NaN age
            try:
                d = pd.read_csv(f, usecols=["timestamp_ms", "symbol", "price"])
                d["oracle_age_s"] = np.nan
                parts.append(d)
            except Exception:
                pass
    cl = pd.concat(parts, ignore_index=True)
    cl = cl[(cl["price"] > 0) & cl["timestamp_ms"].notna()].copy()
    cl["symbol"] = cl["symbol"].str.lower()
    return cl.sort_values("timestamp_ms").reset_index(drop=True)


def _asof_age(cl: pd.DataFrame, queries: pd.DataFrame, t_col: str,
              px_col: str, age_col: str, tol_ms: int = TOL_MS) -> pd.DataFrame:
    """asof the latest Chainlink (price + oracle_age_s) at-or-before each (symbol, t_ms)."""
    out = []
    q = queries.sort_values(t_col)
    for sym, g in q.groupby("symbol"):
        c = cl[cl["symbol"] == sym][["timestamp_ms", "price", "oracle_age_s"]]
        m = pd.merge_asof(g.sort_values(t_col), c, left_on=t_col, right_on="timestamp_ms",
                          direction="backward", tolerance=tol_ms)
        out.append(m.rename(columns={"price": px_col, "oracle_age_s": age_col})
                   .drop(columns=["timestamp_ms"], errors="ignore"))
    return pd.concat(out, ignore_index=True)


def entry_cl_dist(decision: pd.DataFrame, base: pd.DataFrame | None = None,
                  cl: pd.DataFrame | None = None, tol_ms: int = TOL_MS) -> pd.DataFrame:
    """Augment a decision frame with the Chainlink view at entry.

    decision: one row/window with slug, symbol, window_start_ts, entry_sec, buy_yes.
    base:     optional edge_lab.load_base() frame to pull the true Coinbase dist_strike_bps
              at the entry tick (for oracle_agree by magnitude sign). If absent, the Coinbase
              direction is taken from buy_yes (exact for det 'consistent' mode).
    Returns decision + cl_start, cl_spot, cl_dist_bps, cl_oracle_age_s, cb_dist_bps,
    oracle_agree (bool), cl_available (bool).
    """
    d = decision.copy()
    if d.empty:
        for c in ("cl_start", "cl_spot", "cl_dist_bps", "cl_oracle_age_s", "cb_dist_bps"):
            d[c] = np.nan
        d["oracle_agree"] = False
        d["cl_available"] = False
        return d
    d["symbol"] = d["symbol"].str.lower()
    d["entry_sec"] = d["entry_sec"].astype(int)
    d["t_start_ms"] = d["window_start_ts"].astype("int64") * 1000
    d["t_entry_ms"] = (d["window_start_ts"].astype("int64") + d["entry_sec"]) * 1000

    if cl is None:
        cl = load_chainlink_aged()

    # cl_start = Chainlink at window open (the strike basis); cl_spot/age = at the entry tick.
    d = asof_chainlink(cl[["timestamp_ms", "symbol", "price"]], d, "t_start_ms", "cl_start")
    d = _asof_age(cl, d, "t_entry_ms", "cl_spot", "cl_oracle_age_s", tol_ms=tol_ms)

    d["cl_dist_bps"] = (d["cl_spot"] / d["cl_start"] - 1.0) * 1e4
    d["cl_available"] = d["cl_start"].notna() & d["cl_spot"].notna()

    # Coinbase entry distance: prefer the true tick value from base; else use the buy side.
    if base is not None:
        bb = base[["slug", "seconds_into_window", "dist_strike_bps"]].drop_duplicates(
            ["slug", "seconds_into_window"])
        d = d.merge(bb, left_on=["slug", "entry_sec"],
                    right_on=["slug", "seconds_into_window"], how="left")
        d = d.rename(columns={"dist_strike_bps": "cb_dist_bps"}).drop(
            columns=["seconds_into_window"], errors="ignore")
        cb_up = d["cb_dist_bps"] > 0
    else:
        d["cb_dist_bps"] = np.where(d["buy_yes"].astype(bool), np.nan, np.nan)  # unknown magnitude
        cb_up = d["buy_yes"].astype(bool)  # consistent mode: bought side == coinbase up dir

    cl_up = d["cl_dist_bps"] > 0
    d["oracle_agree"] = (d["cl_available"] & (cl_up == cb_up)).fillna(False).astype(bool)
    return d


if __name__ == "__main__":
    # Smoke test against the det_d12 entry population (proves the merge + sign logic).
    from research.analysis import edge_lab
    b = edge_lab.load_base()
    c = b[(b["time_left_sec"] >= 1) & (b["time_left_sec"] <= 180) & (b["abs_dist_bps"] >= 12)
          & (b["consistent"]) & (b["fav_ask"].between(0.50, 0.85))]
    dd = edge_lab.first_tick(c, (c["yes_mid"] >= 0.5).to_numpy())
    aug = entry_cl_dist(dd, base=b)
    n = len(aug)
    av = int(aug["cl_available"].sum())
    print(f"det_d12 entries: {n}  chainlink-available: {av} ({av/max(n,1)*100:.0f}%)")
    a = aug[aug["cl_available"]]
    print(f"oracle_agree rate (where CL present): {a['oracle_agree'].mean()*100:.1f}%")
    print(f"cl_dist_bps  median |.|: {a['cl_dist_bps'].abs().median():.1f}  "
          f"cb_dist_bps median |.|: {a['cb_dist_bps'].abs().median():.1f}")
    print(f"cl_oracle_age_s  median: {a['cl_oracle_age_s'].median():.1f}s  "
          f"p90: {a['cl_oracle_age_s'].quantile(0.9):.1f}s")
