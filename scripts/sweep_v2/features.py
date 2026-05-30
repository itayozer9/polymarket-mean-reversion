"""Stage 2 — pre-compute conditioning features (macro stress, RV regime, depth
imbalance, BTC lead, spread z-score, expiry bucket) and apply them post-hoc
to filter trades the simulator produced.

Why post-hoc: polymarket-arb's signals.py is the single source of truth for
entry/exit logic (CLAUDE.md forbids editing it). Adding new filter dims at
the engine layer would either (a) violate that contract or (b) require a
parallel engine. Post-hoc filtering is conservative (rejects trades the
engine took) but correct for the strategies we want to discover: an entry
filter that says "don't enter when X" is equivalent to dropping trades
whose entry tick matches X.
"""
from __future__ import annotations

import argparse
import gzip
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
SWEEP_DIR = ROOT / "data" / "sweep_v2"
FEATURES_PATH = SWEEP_DIR / "features.parquet"

LIVE_DIR = ROOT / "data" / "live"
LIVE_MACRO_DIR = ROOT / "data" / "live_macro"

WINDOW_SEC = 900


# ---------------------------------------------------------------------------
# Build the feature parquet
# ---------------------------------------------------------------------------

def _load_macro(date: str) -> Optional[pd.DataFrame]:
    p = LIVE_MACRO_DIR / f"{date}.csv.gz"
    if not p.exists():
        return None
    try:
        return pd.read_csv(p)
    except Exception:
        # macro files are usually intact; if not, just skip macro fields for this day.
        return None


def _read_csv_gz_tolerant(path: Path, usecols: List[str]) -> Optional[pd.DataFrame]:
    """Same gzip-tolerant pattern as polymarket-arb loaders — falls back to
    subprocess gunzip if pandas raises on a truncated stream."""
    import subprocess
    from io import BytesIO
    try:
        return pd.read_csv(path, usecols=usecols)
    except Exception:
        pass
    try:
        proc = subprocess.Popen(
            ["gunzip", "-c", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        data, _ = proc.communicate()
        if not data:
            return None
        return pd.read_csv(BytesIO(data), usecols=usecols, on_bad_lines="skip")
    except Exception as e:
        print(f"  WARN: tolerant read failed for {path}: {e}")
        return None


def _load_live_day(symbol: str, date: str) -> Optional[pd.DataFrame]:
    p = LIVE_DIR / f"{symbol}_{date}.csv.gz"
    if not p.exists():
        return None
    cols_we_need = [
        "timestamp_ms", "market_slug", "symbol", "window_start_ts",
        "seconds_into_window",
        "yes_bid_depth", "yes_ask_depth", "no_bid_depth", "no_ask_depth",
        "spread_yes", "spread_no",
    ]
    df = _read_csv_gz_tolerant(p, cols_we_need)
    if df is None or df.empty:
        return None
    return df


def _compute_per_tick_features(
    df_tick: pd.DataFrame, df_macro: Optional[pd.DataFrame], symbol: str
) -> pd.DataFrame:
    """Compute per-tick conditioning features. Returns subset of columns we'll cache."""
    # Drop rows with NaN in critical columns (e.g. truncated last row).
    df_tick = df_tick.dropna(subset=[
        "timestamp_ms", "seconds_into_window", "market_slug",
    ]).copy()
    out = pd.DataFrame()
    out["ts_ms"] = df_tick["timestamp_ms"].astype("i8")
    out["symbol"] = symbol
    out["market_slug"] = df_tick["market_slug"].astype(str)
    out["seconds_into_window"] = df_tick["seconds_into_window"].astype("i4")

    # depth imbalance (computed for both sides; we keep yes-side as the
    # primary signal, no-side is symmetric since prices sum to 1)
    bid = df_tick["yes_bid_depth"].fillna(0.0)
    ask = df_tick["yes_ask_depth"].fillna(0.0)
    denom = bid + ask
    out["depth_imbalance"] = np.where(denom > 0, bid / denom, 0.5).astype("f4")

    # spread z-score: rolling 5-min mean+std over yes spread, then current z
    spr = df_tick["spread_yes"].fillna(0.0).astype("f8").to_numpy()
    if len(spr) >= 10:
        window = 300  # ~5 min at 1Hz
        s = pd.Series(spr)
        mu = s.rolling(window=window, min_periods=10).mean()
        sd = s.rolling(window=window, min_periods=10).std(ddof=0)
        z = (s - mu) / sd.where(sd > 1e-6, 1.0)
        out["spread_zscore"] = z.fillna(0.0).to_numpy(dtype="f4")
    else:
        out["spread_zscore"] = 0.0

    # expiry bucket: based on seconds_into_window
    siw = df_tick["seconds_into_window"].astype("i4").to_numpy()
    out["expiry_bucket"] = pd.cut(
        siw,
        bins=[-1, 300, 600, 1000],
        labels=["EARLY", "MID", "LATE"],
    ).astype(str)

    # macro features — join on nearest second
    if df_macro is not None and len(df_macro) > 0:
        # build a sorted index for asof merge
        macro = df_macro.copy().sort_values("ts_ms")
        macro = macro[["ts_ms", "n_symbols_dipping_5pct_60s"] + [
            c for c in df_macro.columns
            if c.startswith(("rv_60s_pct_", "rv_300s_pct_", "drop_60s_pct_"))
        ]]
        # rename per-symbol columns to our symbol's own values
        rv60 = f"rv_60s_pct_{symbol}"
        rv300 = f"rv_300s_pct_{symbol}"
        drop60 = f"drop_60s_pct_{symbol}"
        btc_drop60 = "drop_60s_pct_btc"

        merged = pd.merge_asof(
            out.sort_values("ts_ms"),
            macro,
            on="ts_ms",
            direction="nearest",
            tolerance=2000,  # 2s tolerance
        )
        out = merged.sort_values("ts_ms").reset_index(drop=True)

        def col_or_zero(name: str, dtype: str = "f4"):
            if name in out.columns:
                return out[name].fillna(0).astype(dtype)
            return pd.Series(np.zeros(len(out), dtype=dtype), index=out.index)

        out["macro_stress"] = col_or_zero("n_symbols_dipping_5pct_60s", "i4")
        out["rv_60s_pct"] = col_or_zero(rv60, "f4")
        out["rv_300s_pct"] = col_or_zero(rv300, "f4")
        out["drop_60s_pct"] = col_or_zero(drop60, "f4")
        out["btc_drop_60s_pct"] = col_or_zero(btc_drop60, "f4")
    else:
        out["macro_stress"] = 0
        out["rv_60s_pct"] = 0.0
        out["rv_300s_pct"] = 0.0
        out["drop_60s_pct"] = 0.0
        out["btc_drop_60s_pct"] = 0.0

    # rv regime: bucketed by per-day percentiles
    rv = out["rv_60s_pct"].to_numpy()
    if (rv > 0).any():
        p33, p67 = np.percentile(rv[rv > 0], [33, 67])
    else:
        p33 = p67 = 0.0
    out["rv_regime"] = np.where(
        rv < p33, "LOW", np.where(rv < p67, "MED", "HIGH"),
    )

    # idiosyncratic: own drop minus macro_stress contribution. Simple heuristic.
    out["idiosyncratic_flag"] = (
        (out["drop_60s_pct"].abs() > 1.0) & (out["macro_stress"] <= 1)
    ).astype("i1")

    keep = [
        "ts_ms", "symbol", "market_slug", "seconds_into_window",
        "depth_imbalance", "spread_zscore", "expiry_bucket",
        "macro_stress", "rv_60s_pct", "rv_regime",
        "drop_60s_pct", "btc_drop_60s_pct", "idiosyncratic_flag",
    ]
    return out[keep]


def build_features(
    symbols: List[str],
    dates: List[str],
    out_path: Path = FEATURES_PATH,
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    chunks = []
    for date in dates:
        macro = _load_macro(date)
        for sym in symbols:
            tick = _load_live_day(sym, date)
            if tick is None or len(tick) == 0:
                print(f"  skip {sym} {date}: no rows")
                continue
            f = _compute_per_tick_features(tick, macro, sym)
            chunks.append(f)
            print(f"  {sym} {date}: {len(f):,} ticks")
    if not chunks:
        raise RuntimeError("No data collected for feature build.")
    full = pd.concat(chunks, ignore_index=True)
    full.to_parquet(out_path, index=False)
    print(f"Wrote {out_path} ({len(full):,} rows, {out_path.stat().st_size / 1e6:.1f} MB)")
    return out_path


# ---------------------------------------------------------------------------
# Runtime feature lookup
# ---------------------------------------------------------------------------

@dataclass
class FeatureLookup:
    """Memory-resident feature lookup keyed by (symbol, ts_ms).
    Built once per worker. Used by `filter_trades()`."""
    by_symbol: Dict[str, pd.DataFrame]  # symbol → sorted-by-ts_ms slice
    by_symbol_arr: Dict[str, Dict[str, np.ndarray]]  # cached column arrays

    @classmethod
    def from_parquet(cls, path: Path) -> "FeatureLookup":
        df = pd.read_parquet(path)
        by_symbol: Dict[str, pd.DataFrame] = {}
        by_symbol_arr: Dict[str, Dict[str, np.ndarray]] = {}
        for sym, group in df.groupby("symbol", sort=False):
            g = group.sort_values("ts_ms").reset_index(drop=True)
            by_symbol[sym] = g
            by_symbol_arr[sym] = {
                "ts_ms": g["ts_ms"].to_numpy(dtype="i8"),
                "depth_imbalance": g["depth_imbalance"].to_numpy(dtype="f4"),
                "spread_zscore": g["spread_zscore"].to_numpy(dtype="f4"),
                "expiry_bucket": g["expiry_bucket"].to_numpy(dtype=object),
                "macro_stress": g["macro_stress"].to_numpy(dtype="i4"),
                "rv_regime": g["rv_regime"].to_numpy(dtype=object),
                "btc_drop_60s_pct": g["btc_drop_60s_pct"].to_numpy(dtype="f4"),
                "idiosyncratic_flag": g["idiosyncratic_flag"].to_numpy(dtype="i1"),
            }
        return cls(by_symbol=by_symbol, by_symbol_arr=by_symbol_arr)

    def at(self, symbol: str, ts_ms: int) -> Optional[Dict[str, Any]]:
        if symbol not in self.by_symbol_arr:
            return None
        arrs = self.by_symbol_arr[symbol]
        ts_arr = arrs["ts_ms"]
        idx = np.searchsorted(ts_arr, ts_ms)
        if idx >= len(ts_arr):
            idx = len(ts_arr) - 1
        # take nearest neighbour
        if idx > 0 and (idx == len(ts_arr) or abs(ts_arr[idx - 1] - ts_ms) < abs(ts_arr[idx] - ts_ms)):
            idx = idx - 1
        return {
            "depth_imbalance": float(arrs["depth_imbalance"][idx]),
            "spread_zscore": float(arrs["spread_zscore"][idx]),
            "expiry_bucket": str(arrs["expiry_bucket"][idx]),
            "macro_stress": int(arrs["macro_stress"][idx]),
            "rv_regime": str(arrs["rv_regime"][idx]),
            "btc_drop_60s_pct": float(arrs["btc_drop_60s_pct"][idx]),
            "idiosyncratic_flag": int(arrs["idiosyncratic_flag"][idx]),
        }


def filter_trades(
    trades: list, symbol: str, v2: Dict[str, Any], fl: FeatureLookup,
) -> list:
    """Apply filter_v2.* gates to a list of Trade objects. Trades whose entry
    tick's features violate any active gate are dropped."""
    out = []
    for t in trades:
        snap = fl.at(symbol, int(t.entry_ts_ms))
        if snap is None:
            # No feature data: pass through conservatively (or drop?). Drop.
            continue
        if v2.get("filter_v2.use_macro_stress"):
            if snap["macro_stress"] < v2.get("filter_v2.macro_stress_min_symbols", 1):
                continue
        if v2.get("filter_v2.use_rv_regime"):
            if snap["rv_regime"] != v2.get("filter_v2.rv_regime"):
                continue
        if v2.get("filter_v2.use_depth_imbalance"):
            if snap["depth_imbalance"] < v2.get("filter_v2.depth_imbalance_min", 0.5):
                continue
        if v2.get("filter_v2.use_btc_lead"):
            if abs(snap["btc_drop_60s_pct"]) < v2.get("filter_v2.btc_lead_pct_min", 0.1):
                continue
        if v2.get("filter_v2.use_spread_zscore"):
            if snap["spread_zscore"] > v2.get("filter_v2.spread_zscore_max", 1.0):
                continue
        if v2.get("filter_v2.use_expiry_bucket"):
            if snap["expiry_bucket"] != v2.get("filter_v2.expiry_bucket"):
                continue
        out.append(t)
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default="btc,eth,sol,xrp")
    parser.add_argument("--date-start", default="2026-05-15")
    parser.add_argument("--date-end", default="2026-05-23")
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    d0 = pd.Timestamp(args.date_start)
    d1 = pd.Timestamp(args.date_end)
    dates = [(d0 + pd.Timedelta(days=i)).strftime("%Y-%m-%d") for i in range((d1 - d0).days + 1)]
    build_features(symbols, dates)


if __name__ == "__main__":
    main()
