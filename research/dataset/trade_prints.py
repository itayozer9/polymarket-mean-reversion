"""Per-(slug, second) features from RAW CLOB trade prints — T4 (Edge Hunt v2).

data/live_trades/*.csv.gz holds every public print (~350k/day/sym): exchange event_ts_ms,
outcome (yes/no), price, size, side (BUY/SELL), fee_rate_bps. The joined frame already has
per-second tr_* sums; what only the raw prints carry is the print-SIZE distribution, burst
micro-shape, and fade timers — the "identifiable uninformed aggression" signature.

CAUSALITY: a feature at decision second s uses ONLY prints with event_ts <= s−2 seconds
(EMBARGO_S=2; prints can arrive delayed vs book ticks). Dedupe on the full print identity.
No outcome/label columns are ever read.

Features (per slug, per second s, window anchored at s−EMBARGO_S):
  pr_usd_2s        gross $ printed in the 2s ending at s−2
  pr_usd_30s       gross $ in the 30s ending at s−2
  pr_n_30s         print count, 30s
  pr_signed_2s/30s YES-equivalent signed flow (BUY yes / SELL no = +; SELL yes / BUY no = −)
  pr_max_30s       largest single print ($), 30s
  pr_p90_bkt       p90 single-print $ of the last COMPLETED 30s bucket (fixed buckets, causal)
  pr_burst_ratio   pr_usd_2s / (pr_usd_30s / 15)  — burst intensity vs own baseline
  pr_since_burst_s seconds since the last 2s-bucket with >= BURST_USD gross

Run:  uv run python -m research.dataset.trade_prints [--timeframe 15m]
Out:  data/research/prints_{tf}.parquet
"""
from __future__ import annotations
import argparse
import glob
import os
import re

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TRADES_DIR = os.path.join(REPO, "data", "live_trades")
OUT_TPL = os.path.join(REPO, "data", "research", "prints_{tf}.parquet")

EMBARGO_S = 2
BURST_USD = 50.0
WLEN = {"15m": 900, "5m": 300}
_DEDUP = ["asset_id", "event_ts_ms", "price", "size", "side"]


def _load_day(path: str, tf: str) -> pd.DataFrame:
    df = pd.read_csv(path, usecols=["event_ts_ms", "market_slug", "outcome",
                                    "price", "size", "side"])
    df = df[df["market_slug"].str.contains(f"-updown-{tf}-", na=False)]
    return df


def _per_second(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Collapse prints to per-(slug, print-second) partial aggregates."""
    wlen = WLEN[tf]
    w0 = df["market_slug"].str.rsplit("-", n=1).str[1].astype(np.int64)
    sec = (df["event_ts_ms"] // 1000).astype(np.int64) - w0
    df = df.assign(psec=sec)
    df = df[(df["psec"] >= 0) & (df["psec"] < wlen)]
    usd = df["price"].to_numpy("f8") * df["size"].to_numpy("f8")
    yes_buy = ((df["outcome"].str.lower() == "yes") == (df["side"].str.upper() == "BUY"))
    df = df.assign(usd=usd, signed=np.where(yes_buy, usd, -usd))
    g = df.groupby(["market_slug", "psec"], sort=False)
    out = g.agg(usd=("usd", "sum"), signed=("signed", "sum"),
                n=("usd", "size"), mx=("usd", "max")).reset_index()
    return out


def _features_slug(s: pd.DataFrame, wlen: int) -> pd.DataFrame:
    """Dense per-second causal features for one slug from its per-second partials."""
    grid = np.zeros((wlen, 4))                     # usd, signed, n, mx per print-second
    idx = s["psec"].to_numpy(np.int64)
    grid[idx, 0], grid[idx, 1] = s["usd"], s["signed"]
    grid[idx, 2], grid[idx, 3] = s["n"], s["mx"]
    # shift by EMBARGO_S: the window visible at decision second t ends at t-EMBARGO_S
    usd = pd.Series(grid[:, 0]).shift(EMBARGO_S, fill_value=0.0)
    signed = pd.Series(grid[:, 1]).shift(EMBARGO_S, fill_value=0.0)
    n = pd.Series(grid[:, 2]).shift(EMBARGO_S, fill_value=0.0)
    mx = pd.Series(grid[:, 3]).shift(EMBARGO_S, fill_value=0.0)
    f = pd.DataFrame({
        "seconds_into_window": np.arange(wlen),
        "pr_usd_2s": usd.rolling(2, min_periods=1).sum(),
        "pr_usd_30s": usd.rolling(30, min_periods=1).sum(),
        "pr_n_30s": n.rolling(30, min_periods=1).sum(),
        "pr_signed_2s": signed.rolling(2, min_periods=1).sum(),
        "pr_signed_30s": signed.rolling(30, min_periods=1).sum(),
        "pr_max_30s": mx.rolling(30, min_periods=1).max().fillna(0.0),
    })
    f["pr_burst_ratio"] = f["pr_usd_2s"] / (f["pr_usd_30s"] / 15.0 + 1e-9)
    # seconds since the last 2s burst >= BURST_USD (as of the embargoed view)
    burst = (f["pr_usd_2s"] >= BURST_USD).to_numpy()
    last = np.where(burst, np.arange(wlen), -1)
    last = np.maximum.accumulate(last)
    f["pr_since_burst_s"] = np.where(last >= 0, np.arange(wlen) - last, np.inf)
    return f


def _p90_buckets(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    """p90 single-print $ per completed 30s bucket; visible from bucket end + EMBARGO_S."""
    wlen = WLEN[tf]
    w0 = df["market_slug"].str.rsplit("-", n=1).str[1].astype(np.int64)
    sec = (df["event_ts_ms"] // 1000).astype(np.int64) - w0
    usd = df["price"].to_numpy("f8") * df["size"].to_numpy("f8")
    d = pd.DataFrame({"market_slug": df["market_slug"], "bkt": sec // 30, "usd": usd})
    d = d[(d["bkt"] >= 0) & (d["bkt"] < wlen // 30)]
    p = d.groupby(["market_slug", "bkt"])["usd"].quantile(0.9).rename("pr_p90_bkt")
    return p.reset_index()


def build_prints(tf: str = "15m", out: str | None = None,
                 since: str | None = None) -> pd.DataFrame:
    out = OUT_TPL.format(tf=tf) if out is None else out
    frames = []
    for path in sorted(glob.glob(os.path.join(TRADES_DIR, "*.csv.gz"))):
        m = re.search(r"_(\d{4}-\d{2}-\d{2})\.csv\.gz$", path)
        if not m or (since and m.group(1) < since):
            continue
        try:
            day = _load_day(path, tf)
        except Exception:
            continue                                   # torn EOD gzip
        if day.empty:
            continue
        day = day.drop_duplicates(subset=[c for c in _DEDUP if c in day.columns])
        per_sec = _per_second(day, tf)
        for slug, s in per_sec.groupby("market_slug", sort=False):
            f = _features_slug(s, WLEN[tf])
            f = f[(f["pr_usd_30s"] > 0)]               # keep only seconds with visible flow
            f.insert(0, "slug", slug)
            frames.append(f)
    feat = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if feat.empty:
        print("[trade_prints] nothing built")
        return feat
    # join p90 of the last COMPLETED bucket visible at s: bucket floor((s-EMBARGO_S)/30)-1
    allp90 = []
    for path in sorted(glob.glob(os.path.join(TRADES_DIR, "*.csv.gz"))):
        m = re.search(r"_(\d{4}-\d{2}-\d{2})\.csv\.gz$", path)
        if not m or (since and m.group(1) < since):
            continue
        try:
            d = _load_day(path, tf)
        except Exception:
            continue
        if len(d):
            allp90.append(_p90_buckets(d.drop_duplicates(
                subset=[c for c in _DEDUP if c in d.columns]), tf))
    p90 = (pd.concat(allp90, ignore_index=True).rename(columns={"market_slug": "slug"})
           if allp90 else pd.DataFrame(columns=["slug", "bkt", "pr_p90_bkt"]))
    feat["bkt"] = (feat["seconds_into_window"] - EMBARGO_S) // 30 - 1
    feat = feat.merge(p90, on=["slug", "bkt"], how="left").drop(columns=["bkt"])
    os.makedirs(os.path.dirname(out), exist_ok=True)
    feat.to_parquet(out, index=False)
    print(f"[trade_prints] {len(feat):,} slug-seconds, {feat['slug'].nunique():,} windows "
          f"-> {out}")
    return feat


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeframe", default="15m", choices=["15m", "5m"])
    ap.add_argument("--since", default="2026-05-23")
    args = ap.parse_args()
    build_prints(args.timeframe, since=args.since)
