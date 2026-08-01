"""Asof-merge the Chainlink price feed (data/live_chainlink/) onto a per-tick joined
frame so `chainlink_price` is REAL.

Polymarket settles crypto Up/Down on **Chainlink**, but the base collector ships
`chainlink_price` as all-zeros, so every Coinbase-settled backtest is on the wrong
oracle (see memory [[backtest-settles-coinbase-not-chainlink]]). This populates the
column (latest chainlink price <= tick ts, per symbol) and derives the
chainlink-vs-coinbase basis the E-oracle edge trades.

Used by:
  - research/dataset/joined.py  (canonical build — every future rebuild includes it)
  - research/dataset/augment_chainlink.py  (one-time: fill the existing parquet)

The chainlink feed polls ~every 15s; a 60s backward tolerance is 4x headroom and
never matches a stale price across a window boundary.
"""
from __future__ import annotations
import functools
import glob
import os

import numpy as np
import pandas as pd

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CL_DIR = os.path.join(REPO_ROOT, "data", "live_chainlink")
CL_TOL_MS = 60_000  # chainlink cadence ~15s; 60s backward tolerance


@functools.lru_cache(maxsize=1)
def load_chainlink() -> pd.DataFrame:
    """All chainlink ticks: timestamp_ms, symbol(lower), price>0, sorted by ts."""
    parts = []
    for f in glob.glob(os.path.join(CL_DIR, "*.csv.gz")):
        try:
            parts.append(pd.read_csv(f, usecols=["timestamp_ms", "symbol", "price"]))
        except Exception:
            pass
    if not parts:
        return pd.DataFrame(columns=["timestamp_ms", "symbol", "price"])
    cl = pd.concat(parts, ignore_index=True)
    cl = cl[(cl["price"] > 0) & cl["timestamp_ms"].notna()].copy()
    cl["symbol"] = cl["symbol"].str.lower()
    return cl.sort_values("timestamp_ms").reset_index(drop=True)


def asof_merge_chainlink(df: pd.DataFrame, tol_ms: int = CL_TOL_MS) -> pd.DataFrame:
    """Populate `chainlink_price` (latest chainlink <= tick ts, per symbol) and add
    `cl_cb_basis_bps` (chainlink vs cb_spot, in bps — the oracle divergence).

    Requires columns: timestamp_ms, symbol, cb_spot. Row count and every other column
    are preserved and original row order is restored. Safe to call on a single-symbol
    frame (the build) or a multi-symbol frame (the augmentation).
    """
    cl = load_chainlink()
    df = df.copy()
    if cl.empty or "timestamp_ms" not in df.columns:
        df["chainlink_price"] = np.nan
        df["cl_cb_basis_bps"] = np.nan
        return df

    df["_ord"] = np.arange(len(df))
    df = df.drop(columns=[c for c in ("chainlink_price",) if c in df.columns])
    sym_lower = df["symbol"].str.lower()
    out = []
    for sym in sym_lower.unique():
        g = df[sym_lower == sym].sort_values("timestamp_ms", kind="mergesort")
        c = cl[cl["symbol"] == sym][["timestamp_ms", "price"]]
        if c.empty:
            g = g.copy()
            g["chainlink_price"] = np.nan
            out.append(g)
            continue
        m = pd.merge_asof(g, c, on="timestamp_ms", direction="backward", tolerance=tol_ms)
        out.append(m.rename(columns={"price": "chainlink_price"}))
    r = pd.concat(out, ignore_index=True).sort_values("_ord").drop(columns=["_ord"])
    r = r.reset_index(drop=True)

    cb = r["cb_spot"].to_numpy("f8") if "cb_spot" in r.columns else np.full(len(r), np.nan)
    clp = r["chainlink_price"].to_numpy("f8")
    r["cl_cb_basis_bps"] = np.divide(clp - cb, cb, out=np.full_like(cb, np.nan), where=cb > 0) * 1e4
    return r
