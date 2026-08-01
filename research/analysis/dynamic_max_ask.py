"""Adaptive max_ask — should the price ceiling depend on market state?

Flat max_ask 0.78 is the best risk-adjusted point (high EV/tr, high future EV) but drops volume;
0.80+ adds volume at lower per-trade EV. Hypothesis: the EXPENSIVE favourites (0.78-0.85) are
low-EV ON AVERAGE, but those where Chainlink ALSO shows a DEEP lock (large |cl_dist|) or the tape
is CALM are genuinely safe — so allow a higher ceiling only in those states, and tighten otherwise.
That could recover 0.80's volume WITHOUT its low-EV expensive favourites = more robust AND profitable.

Per-tick `cl_dist` is computed directly from the populated `chainlink_price` column (cl_start =
Chainlink at window open), so the adaptive ceiling + AGREE gate are applied at TICK level (what the
live engine would do), then first-qualifying-tick → Chainlink-settled sim → CI/CPCV/DSR.

Run: uv run python -m research.analysis.dynamic_max_ask
"""
from __future__ import annotations
import numpy as np
import pandas as pd

from research.analysis import edge_lab

T_MIN, T_MAX, DIST_MIN, ASK_LO, ADVEL = 1, 180, 12.0, 0.50, 2.0


def _prep(b: pd.DataFrame) -> pd.DataFrame:
    """Add per-tick cl_dist_bps (from chainlink_price + per-slug cl_start) + oracle_agree."""
    b = b.copy()
    cl = b[b["chainlink_price"] > 0]
    # cl_start = chainlink_price at the earliest tick of each window
    start = (cl.sort_values("seconds_into_window").groupby("slug")["chainlink_price"]
             .first().rename("cl_start"))
    b = b.merge(start, on="slug", how="left")
    b["cl_dist_bps"] = np.where((b["chainlink_price"] > 0) & (b["cl_start"] > 0),
                                (b["chainlink_price"] / b["cl_start"] - 1.0) * 1e4, np.nan)
    b["cl_ok"] = b["cl_dist_bps"].notna()
    b["oracle_agree"] = b["cl_ok"] & ((b["cl_dist_bps"] > 0) == (b["dist_strike_bps"] > 0))
    return b


def _ledger(b: pd.DataFrame, ceiling: np.ndarray, period_cut=None) -> pd.DataFrame:
    """Build the det_d12_dual (gate=agree + advel) ledger with a PER-TICK ask ceiling."""
    base = ((b["time_left_sec"] >= T_MIN) & (b["time_left_sec"] <= T_MAX)
            & (b["abs_dist_bps"] >= DIST_MIN) & (b["consistent"])
            & (b["fav_ask"] >= ASK_LO) & (b["adverse_vel_10s"] <= ADVEL)
            & (b["cl_ok"]) & (b["oracle_agree"]))
    m = base & (b["fav_ask"] <= ceiling + 1e-9)
    if period_cut is not None:
        m &= (b["window_start_ts"] >= period_cut)
    c = b[m]
    if c.empty:
        return pd.DataFrame()
    dd = edge_lab.first_tick(c, (c["yes_mid"] >= 0.5).to_numpy())
    return edge_lab.simulate(dd, latency=2)


def _row(name: str, led: pd.DataFrame) -> dict:
    if led is None or len(led) == 0:
        print(f"  {name:34} n=0"); return {}
    fut = led[led["split"] == "future"]
    n, wr, ev, tot = len(led), 100*led["won"].mean(), led["pnl"].mean(), led["pnl"].sum()
    fev = fut["pnl"].mean() if len(fut) else float("nan")
    print(f"  {name:34} n={n:>3}  WR={wr:>5.1f}%  EV=${ev:+.2f}  total=${tot:>+5.0f}  future=${fev:+.2f}(n{len(fut)})")
    return dict(n=n, wr=wr, ev=ev, tot=tot, fev=fev)


def run():
    b = _prep(edge_lab.load_base())
    fa = b["fav_ask"].to_numpy("f8")
    cld = b["cl_dist_bps"].abs().to_numpy("f8")
    rv = b["realized_vol"].to_numpy("f8")
    vmed = np.nanmedian(rv[(b["fav_ask"] >= ASK_LO).to_numpy()])
    flat = lambda v: np.full(len(b), v)
    # dynamic: higher ceiling only when Chainlink lock is DEEP (|cl_dist|>=K) else lo
    dyn_cl = lambda lo, hi, K: np.where(cld >= K, hi, lo)
    # dynamic: higher ceiling only when tape is CALM (rvol<=median) else lo
    dyn_vol = lambda lo, hi: np.where(rv <= vmed, hi, lo)

    for period, label in ((None, "ALL-TIME (05-23..06-09)"),):
        print(f"\n=== {label}  [det_d12_dual: gate=agree + advel2; $10 stake] ===")
        print("  -- flat baselines --")
        for v in (0.78, 0.80, 0.85):
            _row(f"flat max_ask={v}", _ledger(b, flat(v), period))
        print("  -- dynamic: max_ask = hi if |cl_dist|>=K else lo --")
        for lo, hi, K in [(0.75,0.85,12),(0.75,0.85,16),(0.75,0.85,20),(0.72,0.85,16),
                          (0.75,0.82,16),(0.78,0.85,16),(0.78,0.85,20)]:
            _row(f"cl-dyn {lo}->{hi} @|cld|>={K}", _ledger(b, dyn_cl(lo,hi,K), period))
        print("  -- dynamic: max_ask = hi if rvol<=median else lo --")
        for lo, hi in [(0.75,0.85),(0.75,0.82),(0.78,0.85)]:
            _row(f"vol-dyn {lo}->{hi}", _ledger(b, dyn_vol(lo,hi), period))

    # Full verdict (CPCV/DSR/latency) on the best dynamic vs flat 0.78
    print("\n=== FULL verdict (CPCV/DSR/latency) ===")
    for name, ceil in [("flat 0.78", flat(0.78)), ("flat 0.80", flat(0.80)),
                       ("cl-dyn 0.75->0.85@16", dyn_cl(0.75,0.85,16)),
                       ("cl-dyn 0.78->0.85@16", dyn_cl(0.78,0.85,16))]:
        led = _ledger(b, ceil, None)
        if led is None or len(led) == 0:
            print(f"{name:24} n=0"); continue
        # rebuild decision for latency_survival
        e = edge_lab.evaluate(led)
        ps = e["per_split"]; fl = ps["FULL"]; fu = ps.get("future")
        print(f"{name:24} n={e['n']:>3} FULL ${fl['ev']:+.2f}[{fl['lo']:+.2f},{fl['hi']:+.2f}] "
              f"WR{fl['wr']:.0f}% | future " +
              (f"${fu['ev']:+.2f}[{fu['lo']:+.2f},{fu['hi']:+.2f}]n{fu['n']}" if fu else "n/a") +
              f" | CPCV {e['cpcv'].get('pct_pos','?'):.0f}% | DSR {e['dsr']['dsr']}")


if __name__ == "__main__":
    run()
