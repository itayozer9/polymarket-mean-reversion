"""Creative loss-eliminating features (owner request): per-window, point-in-time
intra-window microstructure of SPOT vs the strike ("15m checkpoint"):

  strike_crossings : # times cb_spot crossed the strike from window-open to entry
                     (a whippy/indecisive window -> the determinism "lock" is fragile)
  spot_from_hi_bps : entry spot's distance below the running window HIGH (bps of strike)
  spot_from_lo_bps : entry spot's distance above the running window LOW
  at_extreme       : entry spot sits at/near the window extreme on the favourite's
                     side -> reversal (mean-revert) risk against a hold-to-resolution bet
  rsi_win          : intra-window RSI of per-second spot (momentum/overbought-oversold)
  vel_decel        : |spot_vel_3s| - |spot_vel_10s| (<0 = the move is decelerating/reversing)
  realized_vol     : (already in joined) vol regime

All are computed cumulatively/rolling WITHIN the window up to the entry second, so
they are observable at decision time (no lookahead). Merged onto the det/sq
ledgers by (slug, entry_sec) and mined dev->holdout like loss_patterns.

Run: uv run python -m research.analysis.loss_features_creative
"""
from __future__ import annotations
import os
import numpy as np
import pandas as pd

from research.lib.stats import window_clustered_bootstrap

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
JOINED = os.path.join(REPO, "data", "research", "joined_15m.parquet")
LED = os.path.join(REPO, "data", "research", "ledgers")


def _rsi(x: np.ndarray, period: int = 60) -> np.ndarray:
    """Rolling RSI of a 1-D series (per-second spot). Wilder-ish via simple
    rolling means; returns array same length (NaN until min_periods)."""
    s = pd.Series(x)
    d = s.diff()
    up = d.clip(lower=0.0)
    dn = (-d).clip(lower=0.0)
    au = up.rolling(period, min_periods=10).mean()
    ad = dn.rolling(period, min_periods=10).mean()
    rs = au / ad.replace(0.0, np.nan)
    return (100.0 - 100.0 / (1.0 + rs)).to_numpy()


def build_features() -> pd.DataFrame:
    cols = ["slug", "symbol", "seconds_into_window", "cb_spot", "start_price",
            "dist_strike_bps", "yes_mid", "spot_vel_3s_bps", "spot_vel_10s_bps",
            "realized_vol", "book_healthy", "outcome_up_clean"]
    df = pd.read_parquet(JOINED, columns=[c for c in cols])
    df = df[df["cb_spot"].notna() & (df["start_price"] > 0)].copy()
    df = df.sort_values(["slug", "seconds_into_window"])
    strike = df["start_price"].to_numpy()
    spot = df["cb_spot"].to_numpy()
    sign = np.sign(spot - strike)

    feats = []
    for slug, g in df.groupby("slug", sort=False):
        idx = g.index
        sp = g["cb_spot"].to_numpy()
        st = g["start_price"].to_numpy()
        sg = np.sign(sp - st)
        # strike crossings: cumulative # of sign changes (ignore zeros)
        nz = sg.copy()
        for i in range(1, len(nz)):
            if nz[i] == 0:
                nz[i] = nz[i - 1]
        crossings = np.concatenate([[0], np.cumsum(np.abs(np.diff(nz)) > 0)])
        run_hi = np.maximum.accumulate(sp)
        run_lo = np.minimum.accumulate(sp)
        from_hi_bps = (run_hi - sp) / st * 1e4
        from_lo_bps = (sp - run_lo) / st * 1e4
        rsi = _rsi(sp, period=60)
        feats.append(pd.DataFrame({
            "strike_crossings": crossings,
            "spot_from_hi_bps": from_hi_bps,
            "spot_from_lo_bps": from_lo_bps,
            "rsi_win": rsi,
        }, index=idx))
    F = pd.concat(feats).sort_index()
    df = df.join(F)
    df["vel_decel"] = df["spot_vel_3s_bps"].abs() - df["spot_vel_10s_bps"].abs()
    return df


def attach(ledger: pd.DataFrame, feat: pd.DataFrame) -> pd.DataFrame:
    key = feat.set_index(["slug", "seconds_into_window"])
    cols = ["strike_crossings", "spot_from_hi_bps", "spot_from_lo_bps", "rsi_win",
            "vel_decel", "realized_vol"]
    rows = []
    for _, r in ledger.iterrows():
        try:
            f = key.loc[(r["slug"], int(r["entry_sec"]))]
            if isinstance(f, pd.DataFrame):
                f = f.iloc[0]
            rows.append({c: float(f[c]) for c in cols})
        except KeyError:
            rows.append({c: np.nan for c in cols})
    add = pd.DataFrame(rows, index=ledger.index)
    # at_extreme: favourite side spot stretched to window extreme.
    # det fav side = the side spot favours; "extreme" = spot near the far end it
    # has reached, i.e. small distance from the running high (if spot>strike) or
    # low (if spot<strike) -> potential reversal back toward strike.
    out = pd.concat([ledger, add], axis=1)
    return out


def report(df, col, bins=None, qbins=5, label=None):
    d = df.dropna(subset=[col]).copy()
    if not len(d):
        print(f"  {label or col}: no data"); return
    if bins is not None:
        d["b"] = pd.cut(d[col], bins, include_lowest=True)
    else:
        try:
            d["b"] = pd.qcut(d[col], qbins, duplicates="drop")
        except Exception:
            return
    g = d.groupby("b", observed=True).agg(n=("won", "size"), wr=("won", "mean"),
                                          ev=("pnl", "mean"), tot=("pnl", "sum"))
    print(f"\n--- {label or col} ---")
    print(g.to_string(float_format=lambda x: f"{x:+.3f}"))


def mine(edge):
    feat = build_features()
    dev = attach(pd.read_parquet(os.path.join(LED, f"{edge}_dev.parquet")), feat)
    print("\n" + "=" * 74)
    print(f"{edge.upper()} creative features (dev n={len(dev)}, "
          f"WR={dev['won'].mean()*100:.1f}%, EV=${dev['pnl'].mean():+.3f})")
    print("=" * 74)
    report(dev, "strike_crossings", bins=[-0.1, 0.1, 1.1, 2.1, 4.1, 100],
           label="strike_crossings (checkpoint touches before entry)")
    report(dev, "rsi_win", bins=[0, 30, 45, 55, 70, 100], label="intra-window RSI")
    report(dev, "spot_from_hi_bps", qbins=5, label="dist below window high (bps)")
    report(dev, "spot_from_lo_bps", qbins=5, label="dist above window low (bps)")
    report(dev, "vel_decel", bins=[-100, -2, -0.5, 0.5, 100],
           label="vel_decel (<0 = move decelerating/reversing)")
    report(dev, "realized_vol", qbins=5, label="realized_vol regime")
    return feat


def validate_filter(edge, predicate, feat, name):
    dev = attach(pd.read_parquet(os.path.join(LED, f"{edge}_dev.parquet")), feat)
    hold = attach(pd.read_parquet(os.path.join(LED, f"{edge}_holdout.parquet")), feat)
    print(f"\n[{edge} filter: {name}]")
    for split, d in (("dev", dev), ("holdout", hold)):
        k = d[predicate(d)]
        if not len(k):
            print(f"  {split}: empty"); continue
        lo, _, hi = window_clustered_bootstrap(k["pnl"].to_numpy(), k["slug"].to_numpy(), n=3000)
        b = d["pnl"].mean()
        print(f"  {split:8} base EV ${b:+.3f} (n{len(d)}) -> filt EV ${k['pnl'].mean():+.3f} "
              f"(n{len(k)}, WR {k['won'].mean()*100:.0f}%) CI[{lo:+.3f},{hi:+.3f}]")


if __name__ == "__main__":
    feat = mine("det")
    mine("sq")
