# research/dataset/ta_features.py
"""Causal base-asset TA features from the per-second Coinbase spot tape.

Input: a frame with ["slug", "seconds_into_window", "cb_spot"] (the joined/slim
base frame already carries cb_spot). Output: one TA row per (slug,
seconds_into_window). EVERY indicator is causal — grouped by slug, ordered by
seconds_into_window, computed with ewm/rolling that never read a future row.
This project has been burned by look-ahead twice; causality is enforced here once.

Run:  uv run python -m research.dataset.ta_features   # rebuilds the parquet
Out:  data/research/ta_features/ta_features.parquet
"""
from __future__ import annotations
import os
import numpy as np
import pandas as pd

OUT = os.path.join("data", "research", "ta_features", "ta_features.parquet")
TA_COLS = [
    "ta_ema_slope", "ta_ma_cross", "ta_rsi", "ta_macd_hist", "ta_ret_30s",
    "ta_atr", "ta_boll_width", "ta_z_vwap", "ta_regime",
]


def _rsi(ret: pd.Series, period: int = 14) -> pd.Series:
    up = ret.clip(lower=0.0)
    dn = (-ret).clip(lower=0.0)
    roll_up = up.ewm(alpha=1.0 / period, adjust=False, min_periods=1).mean()
    roll_dn = dn.ewm(alpha=1.0 / period, adjust=False, min_periods=1).mean()
    rs = roll_up / roll_dn.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    # No downside moves yet: pure-up tape is RSI 100, pure-flat tape is neutral 50.
    out = out.mask((roll_dn == 0.0) & (roll_up > 0.0), 100.0)
    return out.fillna(50.0)


def _features_one(g: pd.DataFrame) -> pd.DataFrame:
    # WARM-UP CONTRACT: the first ~14-60 seconds of each window are rolling/ewm
    # warm-up. The std-derived features (ta_boll_width, ta_z_vwap) and
    # ta_atr / ta_ema_slope structurally collapse to 0.0 there BY DESIGN —
    # rolling std is NaN with <2 samples and is fillna(0.0)'d into the
    # conservative "range" / no-signal default. This is NOT a real low-vol
    # regime: a sec-0 zero is synthetic, not measured calm. Downstream gates
    # MUST rely on time_left_sec / entry-second bands and never treat early-window
    # zeros as a tradable signal. The 0.0 (not NaN) semantics are deliberate —
    # tests and downstream isfinite-gating depend on them; do NOT switch to NaN.
    s = g["cb_spot"].astype("f8")
    bps = 1e4 / s.clip(lower=1e-9)                 # 1 price unit -> bps of price
    ema30 = s.ewm(span=30, adjust=False, min_periods=1).mean()
    ema10 = s.ewm(span=10, adjust=False, min_periods=1).mean()
    ema60 = s.ewm(span=60, adjust=False, min_periods=1).mean()
    ema12 = s.ewm(span=12, adjust=False, min_periods=1).mean()
    ema26 = s.ewm(span=26, adjust=False, min_periods=1).mean()
    macd = ema12 - ema26
    macd_sig = macd.ewm(span=9, adjust=False, min_periods=1).mean()
    ret = s.diff().fillna(0.0)
    mean60 = s.rolling(60, min_periods=1).mean()
    std60 = s.rolling(60, min_periods=1).std().fillna(0.0)
    mean20 = s.rolling(20, min_periods=1).mean()
    std20 = s.rolling(20, min_periods=1).std().fillna(0.0)

    ema_slope = (ema30.diff().fillna(0.0)) * bps
    ma_cross = np.sign((ema10 - ema60).to_numpy()).astype("i8")
    z_vwap = ((s - mean60) / std60.replace(0.0, np.nan)).fillna(0.0)
    boll_width = ((std20 / mean20.clip(lower=1e-9)) * 1e4)            # bps
    atr = ret.abs().rolling(14, min_periods=1).mean() * bps
    ret_30s = (s - s.shift(30)).fillna(0.0) * bps

    out = pd.DataFrame({
        "slug": g["slug"].to_numpy(),
        "seconds_into_window": g["seconds_into_window"].to_numpy(),
        "ta_ema_slope": ema_slope.to_numpy(),
        "ta_ma_cross": ma_cross,
        "ta_rsi": _rsi(ret).to_numpy(),
        "ta_macd_hist": (macd - macd_sig).to_numpy(),
        "ta_ret_30s": ret_30s.to_numpy(),
        "ta_atr": atr.to_numpy(),
        "ta_boll_width": boll_width.to_numpy(),
        "ta_z_vwap": z_vwap.to_numpy(),
    })
    trend = (out["ta_z_vwap"].abs() >= 1.5) & (
        np.sign(out["ta_ema_slope"]) == np.sign(out["ta_z_vwap"]))
    highvol = out["ta_boll_width"] >= 8.0
    out["ta_regime"] = np.where(trend, "trend",
                                np.where(highvol, "highvol", "range"))
    return out


def build_ta_features(df: pd.DataFrame) -> pd.DataFrame:
    """Causal TA columns, one row per (slug, seconds_into_window)."""
    need = ["slug", "seconds_into_window", "cb_spot"]
    g = (df[need].dropna(subset=["cb_spot"])
         .drop_duplicates(["slug", "seconds_into_window"])
         .sort_values(["slug", "seconds_into_window"]))
    parts = [_features_one(grp) for _, grp in g.groupby("slug", sort=False)]
    if not parts:
        return pd.DataFrame(columns=need[:2] + TA_COLS)
    return pd.concat(parts, ignore_index=True)


def main() -> str:
    from research.analysis.edge_lab import load_base
    out = build_ta_features(load_base())
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    out.to_parquet(OUT, index=False)
    print(f"wrote {len(out):,} rows -> {OUT}")
    return OUT


if __name__ == "__main__":
    main()
