"""Loaders + per-window aligners for the new instrumented feeds.

Feeds (all start 2026-05-22, 1 file per symbol per UTC date):
  - live_l2/<sym>_<date>.csv.gz   : YES-token L2, top-10 levels/side, 1 Hz,
        keyed by (market_slug, seconds_into_window) -> aligns directly to ticks.
  - live_spot/<sym>_<date>.csv.gz : Coinbase WS spot, ~1 Hz event-time,
        keyed by timestamp_ms -> asof-merged to each tick's timestamp.
  - live_trades/<sym>_<date>.csv.gz : Polymarket executed-trade tape (BUY/SELL,
        yes/no, price, size), event-time -> aggregated to (slug, second).
  - live_chainlink/<sym>_<date>.csv.gz : on-chain Aggregator (SLOW feed; NOT the
        settlement Data Stream — see docs/research/phase0a_settlement_feed.md).
        Kept as a low-value context column only.

Design: everything is reduced to per-(slug, second) so it merges cleanly onto the
1 Hz tick frame without ever holding the raw (huge) trade tape in memory. The raw
10-level L2 ladder stays on disk for the fill simulator to read on demand.
"""
from __future__ import annotations
import glob
import io
import os
import subprocess
from datetime import datetime, date

import numpy as np
import pandas as pd

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(REPO_ROOT, "data")

L2_LEVELS = 10


def _read_csv_gz_tolerant(path: str, usecols=None) -> pd.DataFrame:
    try:
        return pd.read_csv(path, usecols=usecols)
    except Exception:
        proc = subprocess.Popen(["gunzip", "-c", path], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL)
        data, _ = proc.communicate()
        return pd.read_csv(io.BytesIO(data), usecols=usecols, on_bad_lines="skip")


def _feed_files(feed: str, symbol: str, date_start: str, date_end: str) -> list[str]:
    d0 = datetime.strptime(date_start, "%Y-%m-%d").date()
    d1 = datetime.strptime(date_end, "%Y-%m-%d").date()
    out = []
    for p in sorted(glob.glob(os.path.join(DATA, feed, f"{symbol}_*.csv.gz"))):
        m = p[-13:-7]  # YYYY-MM-DD is the 10 chars before .csv.gz
        import re
        mm = re.search(r"_(\d{4}-\d{2}-\d{2})\.csv\.gz$", p)
        if not mm:
            continue
        fd = datetime.strptime(mm.group(1), "%Y-%m-%d").date()
        if d0 <= fd <= d1:
            out.append(p)
    return out


# --------------------------------------------------------------------------
# L2 — per (slug, second) top-of-book + depth summary
# --------------------------------------------------------------------------

def load_l2_features(symbol: str, date_start: str, date_end: str) -> pd.DataFrame:
    """Return per-(market_slug, seconds_into_window) L2-derived features for YES.

    Columns: market_slug, seconds_into_window, l2_best_bid, l2_best_ask,
    l2_bid_depth, l2_ask_depth (sum of 10 levels, USD = sum px*sz proxy via sz),
    l2_depth_bid_2c, l2_depth_ask_2c, l2_imbalance, microprice.
    """
    bid_px = [f"bid_px_{i}" for i in range(1, L2_LEVELS + 1)]
    bid_sz = [f"bid_sz_{i}" for i in range(1, L2_LEVELS + 1)]
    ask_px = [f"ask_px_{i}" for i in range(1, L2_LEVELS + 1)]
    ask_sz = [f"ask_sz_{i}" for i in range(1, L2_LEVELS + 1)]
    keep = ["market_slug", "seconds_into_window"] + bid_px + bid_sz + ask_px + ask_sz

    frames = []
    for f in _feed_files("live_l2", symbol, date_start, date_end):
        df = _read_csv_gz_tolerant(f)
        miss = [c for c in keep if c not in df.columns]
        if miss:
            continue
        frames.append(df[keep])
    if not frames:
        return pd.DataFrame(columns=["market_slug", "seconds_into_window"])
    df = pd.concat(frames, ignore_index=True)

    bpx = df[bid_px].to_numpy("f8"); bsz = df[bid_sz].to_numpy("f8")
    apx = df[ask_px].to_numpy("f8"); asz = df[ask_sz].to_numpy("f8")
    # best level = level 1
    best_bid = bpx[:, 0]; best_ask = apx[:, 0]
    best_bsz = bsz[:, 0]; best_asz = asz[:, 0]
    # full-ladder share depth
    bid_depth = np.nansum(bsz, axis=1)
    ask_depth = np.nansum(asz, axis=1)
    # USD notional within 2c of best (capacity for a marketable order)
    bid_mask_2c = bpx >= (best_bid[:, None] - 0.02 - 1e-9)
    ask_mask_2c = apx <= (best_ask[:, None] + 0.02 + 1e-9)
    depth_bid_2c = np.nansum(np.where(bid_mask_2c, bsz * bpx, 0.0), axis=1)
    depth_ask_2c = np.nansum(np.where(ask_mask_2c, asz * apx, 0.0), axis=1)
    tot = bid_depth + ask_depth
    imbalance = np.where(tot > 0, bid_depth / tot, 0.5)
    den = best_bsz + best_asz
    microprice = np.where(den > 0,
                          (best_bid * best_asz + best_ask * best_bsz) / den,
                          (best_bid + best_ask) / 2.0)

    out = pd.DataFrame({
        "market_slug": df["market_slug"].astype(str).to_numpy(),
        "seconds_into_window": df["seconds_into_window"].astype("i4").to_numpy(),
        "l2_best_bid": best_bid, "l2_best_ask": best_ask,
        "l2_bid_depth": bid_depth, "l2_ask_depth": ask_depth,
        "l2_depth_bid_2c": depth_bid_2c, "l2_depth_ask_2c": depth_ask_2c,
        "l2_imbalance": imbalance, "microprice": microprice,
    })
    # one row per (slug, second): keep last observation in the second
    out = out.drop_duplicates(["market_slug", "seconds_into_window"], keep="last")
    return out


# --------------------------------------------------------------------------
# Trades — per (slug, second) YES-space taker-flow aggregates
# --------------------------------------------------------------------------

def load_trades_per_second(symbol: str, date_start: str, date_end: str) -> pd.DataFrame:
    """Aggregate the trade tape to per-(market_slug, second).

    YES-space mapping (UP-bullish vs UP-bearish taker pressure):
      bullish (pushes P(Up) up)  = BUY yes  + SELL no
      bearish (pushes P(Up) down) = SELL yes + BUY no
    USD notional = price * size.

    Returns: market_slug, sec, tr_n, tr_bull_usd, tr_bear_usd, tr_signed_usd,
    tr_vwap_yes (volume-weighted YES-equivalent price of the second's trades).
    """
    keep = ["event_ts_ms", "market_slug", "outcome", "price", "size", "side"]
    frames = []
    for f in _feed_files("live_trades", symbol, date_start, date_end):
        df = _read_csv_gz_tolerant(f, usecols=lambda c: c in keep)
        if df.empty or "market_slug" not in df.columns:
            continue
        df = df[df["market_slug"].astype(str).str.len() > 0].copy()
        if df.empty:
            continue
        df["price"] = pd.to_numeric(df["price"], errors="coerce")
        df["size"] = pd.to_numeric(df["size"], errors="coerce")
        df["event_ts_ms"] = pd.to_numeric(df["event_ts_ms"], errors="coerce")
        df = df.dropna(subset=["price", "size", "event_ts_ms"])
        df["usd"] = df["price"] * df["size"]
        side = df["side"].astype(str).str.upper()
        oc = df["outcome"].astype(str).str.lower()
        is_bull = ((side == "BUY") & (oc == "yes")) | ((side == "SELL") & (oc == "no"))
        df["bull_usd"] = np.where(is_bull, df["usd"], 0.0)
        df["bear_usd"] = np.where(~is_bull, df["usd"], 0.0)
        # YES-equivalent price: a 'no' trade at p is a 'yes' trade at 1-p
        df["yes_px"] = np.where(oc == "yes", df["price"], 1.0 - df["price"])
        # window start ts is the trailing integer of the slug -> second
        wstart = df["market_slug"].astype(str).str.rsplit("-", n=1).str[-1]
        df["window_start_ts"] = pd.to_numeric(wstart, errors="coerce")
        df = df.dropna(subset=["window_start_ts"])
        df["sec"] = (df["event_ts_ms"] // 1000 - df["window_start_ts"]).astype("i8")
        df = df[(df["sec"] >= 0) & (df["sec"] <= 901)]
        frames.append(df[["market_slug", "sec", "usd", "bull_usd", "bear_usd",
                           "yes_px", "size"]])
    if not frames:
        return pd.DataFrame(columns=["market_slug", "seconds_into_window"])
    allt = pd.concat(frames, ignore_index=True)
    g = allt.groupby(["market_slug", "sec"], sort=False)
    agg = g.agg(tr_n=("usd", "size"),
                tr_bull_usd=("bull_usd", "sum"),
                tr_bear_usd=("bear_usd", "sum"),
                _wpx=("yes_px", lambda s: np.average(s, weights=allt.loc[s.index, "size"])
                      if allt.loc[s.index, "size"].sum() > 0 else np.nan)).reset_index()
    agg["tr_signed_usd"] = agg["tr_bull_usd"] - agg["tr_bear_usd"]
    agg = agg.rename(columns={"sec": "seconds_into_window", "_wpx": "tr_vwap_yes"})
    return agg


# --------------------------------------------------------------------------
# Spot (Coinbase WS) — sorted by ts for asof-merge
# --------------------------------------------------------------------------

def load_l2_ladders(symbol: str, date_start: str, date_end: str) -> pd.DataFrame:
    """Raw 10-level YES ladders per (market_slug, seconds_into_window), for the
    fill simulator to walk the book. Indexed by (market_slug, seconds_into_window).
    """
    bid_px = [f"bid_px_{i}" for i in range(1, L2_LEVELS + 1)]
    bid_sz = [f"bid_sz_{i}" for i in range(1, L2_LEVELS + 1)]
    ask_px = [f"ask_px_{i}" for i in range(1, L2_LEVELS + 1)]
    ask_sz = [f"ask_sz_{i}" for i in range(1, L2_LEVELS + 1)]
    keep = ["market_slug", "seconds_into_window"] + bid_px + bid_sz + ask_px + ask_sz
    frames = []
    for f in _feed_files("live_l2", symbol, date_start, date_end):
        df = _read_csv_gz_tolerant(f)
        if all(c in df.columns for c in keep):
            frames.append(df[keep])
    if not frames:
        return pd.DataFrame(columns=keep).set_index(["market_slug", "seconds_into_window"])
    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(["market_slug", "seconds_into_window"], keep="last")
    return df.set_index(["market_slug", "seconds_into_window"]).sort_index()


def load_spot(symbol: str, date_start: str, date_end: str) -> pd.DataFrame:
    keep = ["timestamp_ms", "price", "best_bid", "best_ask"]
    frames = []
    for f in _feed_files("live_spot", symbol, date_start, date_end):
        df = _read_csv_gz_tolerant(f, usecols=lambda c: c in keep)
        if not df.empty and "timestamp_ms" in df.columns:
            frames.append(df)
    if not frames:
        return pd.DataFrame(columns=keep)
    df = pd.concat(frames, ignore_index=True)
    for c in keep:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["timestamp_ms", "price"]).sort_values("timestamp_ms")
    return df.rename(columns={"price": "cb_spot", "best_bid": "cb_spot_bid",
                              "best_ask": "cb_spot_ask"}).reset_index(drop=True)
