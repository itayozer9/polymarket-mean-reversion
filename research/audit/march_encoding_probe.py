"""Phase 0 Task 3b — March data bid/ask encoding forensics.

Throwaway investigation script. Tests whether `yes_best_bid`/`yes_best_ask`
(and the NO side) are correctly labelled in each data regime, or swapped.

Reference regime: May 15-22 live data (collected by the current bot = known-good).

Run:  uv run python research/audit/march_encoding_probe.py
"""
from __future__ import annotations

import glob
import io
import os
import subprocess
import sys

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HIST = os.path.join(REPO, "data", "historical")
LIVE = os.path.join(REPO, "data", "live")

SYMBOLS = ["btc", "eth", "sol", "xrp"]

REGIMES = {
    "Mar 04-13": ("2026-03-04", "2026-03-13", HIST),
    "Mar 14-17": ("2026-03-14", "2026-03-17", HIST),
    "May 15-22": ("2026-05-15", "2026-05-22", LIVE),
}


def read_gz_tolerant(path: str) -> pd.DataFrame:
    try:
        return pd.read_csv(path)
    except Exception:
        proc = subprocess.Popen(["gunzip", "-c", path], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL)
        data, _ = proc.communicate()
        return pd.read_csv(io.BytesIO(data), on_bad_lines="skip")


def load_regime(symbol: str, d0: str, d1: str, folder: str) -> pd.DataFrame:
    frames = []
    for p in sorted(glob.glob(os.path.join(folder, f"{symbol}_*.csv.gz"))):
        if p.endswith("_raw.csv.gz"):
            continue
        base = os.path.basename(p)
        datestr = base.replace(f"{symbol}_", "").replace(".csv.gz", "")
        if not (d0 <= datestr <= d1):
            continue
        try:
            df = read_gz_tolerant(p)
            df["_src"] = base
            frames.append(df)
        except Exception as e:
            print(f"  !! failed {base}: {e}", file=sys.stderr)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def numcols(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    for c in cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


BOOK = ["yes_best_bid", "yes_best_ask", "no_best_bid", "no_best_ask",
        "yes_bid_depth", "yes_ask_depth", "no_bid_depth", "no_ask_depth",
        "yes_mid", "no_mid", "spread_yes", "spread_no", "total_mid"]


def pct(x):
    return f"{100*x:6.2f}%"


def desc(name, s):
    s = s.dropna()
    if len(s) == 0:
        print(f"  {name}: (empty)")
        return
    print(f"  {name}: n={len(s)} min={s.min():.4f} p25={s.quantile(.25):.4f} "
          f"median={s.median():.4f} mean={s.mean():.4f} p75={s.quantile(.75):.4f} "
          f"max={s.max():.4f}")


def analyse(regime: str, symbol: str, df: pd.DataFrame, tf_filter: str | None = None):
    if tf_filter:
        df = df[df["market_slug"].astype(str).str.contains(f"updown-{tf_filter}-")]
    df = df.copy()
    if df.empty:
        print(f"\n### {regime} / {symbol} / {tf_filter or 'all'}: NO DATA")
        return None
    df = numcols(df, [c for c in BOOK if c in df.columns])

    n = len(df)
    yes_diff = df["yes_best_ask"] - df["yes_best_bid"]
    no_diff = df["no_best_ask"] - df["no_best_bid"]

    # only consider "two-sided" rows: both sides strictly inside (0,1)
    two_sided = ((df["yes_best_bid"] > 0) & (df["yes_best_ask"] > 0) &
                 (df["yes_best_bid"] < 1) & (df["yes_best_ask"] < 1) &
                 (df["no_best_bid"] > 0) & (df["no_best_ask"] > 0))
    ts = df[two_sided]

    print(f"\n### {regime} / {symbol} / {tf_filter or 'all'}  (n={n}, two-sided={len(ts)})")

    # Q1: sign of ask-bid
    ya_ge = (yes_diff >= 0)
    na_ge = (no_diff >= 0)
    print(f"  YES ask>=bid : {pct(ya_ge.mean())}   ask<bid: {pct((yes_diff<0).mean())}  ask==bid: {pct((yes_diff==0).mean())}")
    print(f"  NO  ask>=bid : {pct(na_ge.mean())}   ask<bid: {pct((no_diff<0).mean())}  ask==bid: {pct((no_diff==0).mean())}")
    if len(ts):
        ysd = ts["yes_best_ask"] - ts["yes_best_bid"]
        nsd = ts["no_best_ask"] - ts["no_best_bid"]
        desc("YES (ask-bid) two-sided", ysd)
        desc("NO  (ask-bid) two-sided", nsd)

    # Q2: spread definition
    sp_y_eq = np.isclose(df["spread_yes"], yes_diff, atol=1e-6, equal_nan=True)
    sp_y_eq_abs = np.isclose(df["spread_yes"], yes_diff.abs(), atol=1e-6, equal_nan=True)
    print(f"  spread_yes == (ask-bid)        : {pct(sp_y_eq.mean())}")
    print(f"  spread_yes == |ask-bid|        : {pct(sp_y_eq_abs.mean())}")
    sp_n_eq = np.isclose(df["spread_no"], no_diff, atol=1e-6, equal_nan=True)
    print(f"  spread_no  == (no_ask-no_bid)  : {pct(sp_n_eq.mean())}")
    desc("spread_yes value", df["spread_yes"])

    # total_mid vs yes_mid+no_mid
    tm = np.isclose(df["total_mid"], df["yes_mid"] + df["no_mid"], atol=1e-6, equal_nan=True)
    print(f"  total_mid == yes_mid+no_mid    : {pct(tm.mean())}")

    # Q3: complement relation. correct book: no_bid = 1-yes_ask, no_ask = 1-yes_bid
    comp_correct = (np.isclose(df["no_best_bid"], 1 - df["yes_best_ask"], atol=1e-6) &
                    np.isclose(df["no_best_ask"], 1 - df["yes_best_bid"], atol=1e-6))
    # swapped book: no_bid = 1-yes_bid, no_ask = 1-yes_ask
    comp_swapped = (np.isclose(df["no_best_bid"], 1 - df["yes_best_bid"], atol=1e-6) &
                    np.isclose(df["no_best_ask"], 1 - df["yes_best_ask"], atol=1e-6))
    print(f"  no_bid=1-yes_ask & no_ask=1-yes_bid (correct complement) : {pct(comp_correct.mean())}")
    print(f"  no_bid=1-yes_bid & no_ask=1-yes_ask (alt complement)     : {pct(comp_swapped.mean())}")

    # Q3: does swapping yes_bid<->yes_ask fix it?
    sw_ask = df["yes_best_bid"]   # after swap, "ask" = old bid
    sw_bid = df["yes_best_ask"]   # after swap, "bid" = old ask
    sw_diff = sw_ask - sw_bid
    print(f"  AFTER swap: YES ask>=bid       : {pct((sw_diff >= 0).mean())}")
    if len(ts):
        ts_sw = (ts["yes_best_bid"] - ts["yes_best_ask"])  # swapped ask-bid on two-sided
        print(f"  AFTER swap two-sided ask>=bid  : {pct((ts_sw >= 0).mean())}")
        desc("AFTER swap (ask-bid) two-sided", ts_sw)

    # Depth tells
    print(f"  YES bid_depth == NO ask_depth  : {pct(np.isclose(df['yes_bid_depth'], df['no_ask_depth'], rtol=1e-4, equal_nan=True).mean())}")
    print(f"  YES ask_depth == NO bid_depth  : {pct(np.isclose(df['yes_ask_depth'], df['no_bid_depth'], rtol=1e-4, equal_nan=True).mean())}")
    desc("yes_ask_depth", df["yes_ask_depth"])
    desc("yes_bid_depth", df["yes_bid_depth"])

    return {
        "regime": regime, "symbol": symbol, "tf": tf_filter or "all", "n": n,
        "two_sided": len(ts),
        "yes_ask_ge_bid": ya_ge.mean(),
        "yes_ask_lt_bid": (yes_diff < 0).mean(),
        "no_ask_ge_bid": na_ge.mean(),
        "spread_yes_eq_diff": sp_y_eq.mean(),
        "spread_yes_eq_absdiff": sp_y_eq_abs.mean(),
        "comp_correct": comp_correct.mean(),
        "comp_swapped": comp_swapped.mean(),
        "swap_fixes": (sw_diff >= 0).mean(),
        "median_spread_abs": yes_diff.abs().median(),
        "median_yes_ask_depth": df["yes_ask_depth"].median(),
        "median_yes_bid_depth": df["yes_bid_depth"].median(),
    }


def main():
    rows = []
    cache = {}
    for regime, (d0, d1, folder) in REGIMES.items():
        for sym in SYMBOLS:
            df = load_regime(sym, d0, d1, folder)
            cache[(regime, sym)] = df
            for tf in ["15m", "5m"]:
                r = analyse(regime, sym, df, tf)
                if r:
                    rows.append(r)

    print("\n\n" + "=" * 110)
    print("SUMMARY TABLE")
    print("=" * 110)
    hdr = (f"{'regime':<11} {'sym':<4} {'tf':<4} {'n':>9} {'ask>=bid':>9} "
           f"{'ask<bid':>9} {'sp==diff':>9} {'sp==|d|':>9} {'compOK':>8} "
           f"{'swapFix':>8} {'medSprd':>8} {'medAskDep':>10}")
    print(hdr)
    for r in rows:
        print(f"{r['regime']:<11} {r['symbol']:<4} {r['tf']:<4} {r['n']:>9} "
              f"{pct(r['yes_ask_ge_bid']):>9} {pct(r['yes_ask_lt_bid']):>9} "
              f"{pct(r['spread_yes_eq_diff']):>9} {pct(r['spread_yes_eq_absdiff']):>9} "
              f"{pct(r['comp_correct']):>8} {pct(r['swap_fixes']):>8} "
              f"{r['median_spread_abs']:>8.4f} {r['median_yes_ask_depth']:>10.1f}")

    # ---- Q6: backtest impact, BTC 15m Mar 15-17 ----
    print("\n\n" + "=" * 110)
    print("Q6 — BACKTEST IMPACT: BTC 15m, Mar 14-17 windows")
    print("=" * 110)
    df = cache[("Mar 14-17", "btc")]
    df = df[df["market_slug"].astype(str).str.contains("updown-15m-")].copy()
    df = numcols(df, BOOK)
    # restrict to Mar 15-17 by window_start_ts
    df["_d"] = pd.to_datetime(df["window_start_ts"], unit="s", utc=True).dt.date.astype(str)
    df = df[df["_d"] >= "2026-03-15"]
    two_sided = ((df["yes_best_bid"] > 0.02) & (df["yes_best_ask"] > 0.02) &
                 (df["yes_best_bid"] < 0.98) & (df["yes_best_ask"] < 0.98))
    ts = df[two_sided]
    spread = (ts["yes_best_bid"] - ts["yes_best_ask"])  # bid-ask = inverted spread magnitude
    print(f"  two-sided ticks Mar15-17 BTC 15m: {len(ts)}")
    print(f"  |yes_best_bid - yes_best_ask| (the inverted spread):")
    desc("    inv-spread", spread)
    print(f"  If sim buys at yes_best_ask (low) and sells at yes_best_bid (high)")
    print(f"  on a swapped book, per-share free edge = median {spread.median():.4f},")
    print(f"  mean {spread.mean():.4f}.  On a $10 trade that is ~${10*spread.mean():.3f} free PnL.")
    # per distinct window
    w = ts.groupby("market_slug").apply(
        lambda g: (g["yes_best_bid"] - g["yes_best_ask"]).mean(),
        include_groups=False)
    print(f"  per-window mean inv-spread: n_windows={len(w)} "
          f"median={w.median():.4f} mean={w.mean():.4f}")


if __name__ == "__main__":
    main()
