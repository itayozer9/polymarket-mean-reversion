"""Joined, fully-instrumented per-tick dataset for the clean window.

Base = the existing per-tick feature frame (research/dataset/ticks.py), PLUS:
  - L2 depth summary (research/dataset/feeds.load_l2_features), merged on
    (slug, seconds_into_window).
  - Trade-tape per-second YES-space flow (feeds.load_trades_per_second), merged
    on (slug, seconds_into_window); no-trade seconds -> 0.
  - Coinbase-WS spot, asof-merged on timestamp_ms; plus distance-to-strike and
    short-horizon spot velocity (the fast-jump detector for Phase 2).
  - Corrected label `outcome_up` using `end >= start -> Up` (tie -> Up; the
    settlement rule per docs/research/phase0a_settlement_feed.md).

Output: data/research/joined_<tf>.parquet (one row per tick, clean window only).
Run: uv run python -m research.build_joined
"""
from __future__ import annotations
import os
import numpy as np
import pandas as pd

from research.data.loader import iter_windows, load_outcomes
from research.dataset.ticks import build_window_ticks
from research.dataset.feeds import (
    load_l2_features, load_trades_per_second, load_spot,
)
from research.clean_window import split_of

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUTPUT_DIR = os.path.join(REPO_ROOT, "data", "research")

_TRADE_COLS = ["tr_n", "tr_bull_usd", "tr_bear_usd", "tr_signed_usd", "tr_vwap_yes"]


def _enrich_symbol(symbol: str, timeframe: str, date_start: str, date_end: str,
                   outcomes: pd.DataFrame) -> pd.DataFrame:
    """Build the joined per-tick frame for one (symbol, timeframe) over a range."""
    # 1) base per-window features (reuses the existing, tested pipeline)
    base_frames = []
    for slug, ticks in iter_windows(symbol, timeframe, date_start, date_end):
        if slug in outcomes.index:
            oc = outcomes.loc[slug]
            outcome = str(oc["outcome"])
        else:
            outcome = None
        base_frames.append(build_window_ticks(slug, ticks, outcome))
    if not base_frames:
        return pd.DataFrame()
    df = pd.concat(base_frames, ignore_index=True)
    df["symbol"] = symbol

    # 2) merge L2 depth summary on (slug, second)
    l2 = load_l2_features(symbol, date_start, date_end)
    if not l2.empty:
        df = df.merge(l2, left_on=["slug", "seconds_into_window"],
                      right_on=["market_slug", "seconds_into_window"], how="left")
        df = df.drop(columns=[c for c in ("market_slug_y", "market_slug") if c in df.columns],
                     errors="ignore")

    # 3) merge trade-tape per-second flow on (slug, second); no-trade -> 0
    tr = load_trades_per_second(symbol, date_start, date_end)
    if not tr.empty:
        df = df.merge(tr, left_on=["slug", "seconds_into_window"],
                      right_on=["market_slug", "seconds_into_window"], how="left")
        df = df.drop(columns=[c for c in ("market_slug_y", "market_slug") if c in df.columns],
                     errors="ignore")
    for c in _TRADE_COLS:
        if c not in df.columns:
            df[c] = np.nan
    fill0 = ["tr_n", "tr_bull_usd", "tr_bear_usd", "tr_signed_usd"]
    df[fill0] = df[fill0].fillna(0.0)

    # 4) asof-merge Coinbase-WS spot on timestamp_ms (latest spot <= tick ts)
    spot = load_spot(symbol, date_start, date_end)
    if not spot.empty:
        df = df.sort_values("timestamp_ms", kind="mergesort")
        df = pd.merge_asof(df, spot[["timestamp_ms", "cb_spot", "cb_spot_bid", "cb_spot_ask"]],
                           on="timestamp_ms", direction="backward",
                           tolerance=5000)  # ms; don't match stale spot >5s
    else:
        df["cb_spot"] = np.nan

    # restore (slug, second) order for per-window rolling features
    df = df.sort_values(["slug", "seconds_into_window"], kind="mergesort").reset_index(drop=True)

    # 5) spot-derived: distance-to-strike (bps, off Coinbase-Coinbase) + velocity
    strike = df["start_price"].to_numpy("f8")
    _diff = df["cb_spot"].to_numpy("f8") - strike
    df["dist_strike_bps"] = np.divide(_diff, strike, out=np.full_like(_diff, np.nan),
                                      where=strike > 0) * 1e4
    g = df.groupby("slug", sort=False)["cb_spot"]
    for w in (3, 10):
        lag = g.shift(w)
        df[f"spot_vel_{w}s_bps"] = (df["cb_spot"] / lag - 1.0) * 1e4

    # 6) rolling trade-flow bursts within window
    gs = df.groupby("slug", sort=False)
    df["tr_signed_5s"] = gs["tr_signed_usd"].transform(
        lambda s: s.rolling(5, min_periods=1).sum())
    df["tr_bear_10s"] = gs["tr_bear_usd"].transform(
        lambda s: s.rolling(10, min_periods=1).sum())
    df["tr_n_10s"] = gs["tr_n"].transform(
        lambda s: s.rolling(10, min_periods=1).sum())

    # 7) corrected label: end >= start -> Up (tie -> Up)
    end_price = df["slug"].map(
        lambda s: float(outcomes.loc[s, "end_price"]) if s in outcomes.index
        and pd.notna(outcomes.loc[s, "end_price"]) else np.nan)
    df["end_price"] = end_price.to_numpy()
    df["outcome_up_clean"] = np.where(
        df["end_price"].notna(),
        (df["end_price"].to_numpy("f8") >= strike).astype("f8"),
        df["outcome_up"].to_numpy("f8"))

    # 8) book-health guard (the decided-market book artifact produced fantasy
    #    edges in prior research; ~92% of ticks are healthy two-sided books).
    yb = df["yes_best_bid"].to_numpy("f8"); ya = df["yes_best_ask"].to_numpy("f8")
    df["book_healthy"] = (
        (yb > 0.001) & (yb < 0.999) & (ya > 0.001) & (ya < 0.999)
        & (yb < ya) & (df["spread_yes"].to_numpy("f8") > 0)
    )

    # 9) bookkeeping
    df["date"] = pd.to_datetime(df["window_start_ts"], unit="s", utc=True).dt.strftime("%Y-%m-%d")
    df["split"] = df["date"].map(split_of)
    return df


def build(timeframes=("15m", "5m"), symbols=("btc", "eth", "sol", "xrp"),
          date_start=None, date_end=None) -> None:
    from research.clean_window import CLEAN_START, available_clean_dates
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    outcomes = load_outcomes()
    dates = available_clean_dates("btc")
    date_start = date_start or CLEAN_START
    date_end = date_end or (dates[-1] if dates else CLEAN_START)
    print(f"Joined dataset build: {date_start} .. {date_end}  (clean days: {dates})")

    for tf in timeframes:
        frames = []
        for sym in symbols:
            t0 = pd.Timestamp.utcnow()
            d = _enrich_symbol(sym, tf, date_start, date_end, outcomes)
            if not d.empty:
                frames.append(d)
                print(f"  {sym} {tf}: {len(d):>8,} ticks, "
                      f"{d['slug'].nunique():>4} windows  "
                      f"[{(pd.Timestamp.utcnow()-t0).total_seconds():.1f}s]")
        if not frames:
            print(f"  (no data for {tf})")
            continue
        out = pd.concat(frames, ignore_index=True)
        path = os.path.join(OUTPUT_DIR, f"joined_{tf}.parquet")
        out.to_parquet(path, index=False)
        print(f"  -> {path}  ({len(out):,} rows, {os.path.getsize(path)/1e6:.1f} MB)\n")


if __name__ == "__main__":
    build()
