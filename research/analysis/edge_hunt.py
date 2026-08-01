"""edge_hunt — define each candidate edge's ENTRY LOGIC and run them ALL through
the shared edge_lab gauntlet (Chainlink resettle, depth-gated realistic fill,
latency-survival sweep, per-split CIs, CPCV, DSR). Ranks + saves ledgers.

Latency-RANKED per the user's hard constraint (no speed edge, ever):
  Tier A = decide with a TIME BUFFER (prediction); EV should be flat across the
           latency sweep (that IS the proof it's not a pickoff).
  Tier B = fires late; only counts if it survives the latency sweep.

Run: uv run python -m research.analysis.edge_hunt
"""
from __future__ import annotations
import os

import numpy as np
import pandas as pd

from research.analysis import edge_lab as L
from research.analysis.loss_patterns import _sq_prep, _fit_curve, REPO

LEDGER_DIR = os.path.join(REPO, "data", "research", "ledgers")


def _prev_up_map(b: pd.DataFrame) -> pd.Series:
    """slug -> the SAME symbol's previous 15m window's outcome_up_clean (0/1)."""
    w = (b.sort_values(["slug", "seconds_into_window"]).groupby("slug", as_index=False)
         .agg(symbol=("symbol", "first"), wst=("window_start_ts", "first"),
              up=("outcome_up_clean", "first")))
    w = w.sort_values(["symbol", "wst"])
    w["prev_up"] = w.groupby("symbol")["up"].shift(1)
    return w.set_index("slug")["prev_up"]


def edges() -> dict:
    b = L.load_base().copy()
    prev_up = _prev_up_map(b)
    b["cb_up_now"] = b["cb_spot"] >= b["start_price"]
    b["cl_ok"] = b["chainlink_price"].notna() & (b["chainlink_price"] > 0)
    b["cl_up_now"] = b["cl_ok"] & (b["chainlink_price"] >= b["start_price"])
    b["cl_dist_bps"] = (b["chainlink_price"] / b["start_price"] - 1.0) * 1e4

    out = {}

    # ---------------- Tier A: prediction / latency-free ----------------
    # E-oracle: book (~Coinbase) on the WRONG side of the strike vs the Chainlink
    # SETTLEMENT -> buy the Chainlink-implied side (the underdog the book underprices).
    # Decide with >=60s buffer (not a last-instant snipe). Uses the real chainlink feed.
    c = b[b["time_left_sec"].between(60, 180) & b["cl_ok"]
          & (b["cb_up_now"] != b["cl_up_now"]) & (b["cl_dist_bps"].abs() >= 5)
          & b["book_healthy"]]
    out["E-oracle(A)"] = L.first_tick(c, (c["cl_up_now"]).to_numpy())

    # E-persist: prior window's outcome -> this window (decide in the first ~60-80s).
    c = b[b["time_left_sec"].between(780, 860) & b["book_healthy"]].copy()
    c["pu"] = c["slug"].map(prev_up)
    c = c[c["pu"].notna()]
    out["E-persistMom(A)"] = L.first_tick(c, (c["pu"] == 1).to_numpy())   # momentum
    out["E-persistRev(A)"] = L.first_tick(c, (c["pu"] == 0).to_numpy())   # reversal

    # E-flb: buy the favourite mid-window (favourite-longshot / calibration), buffered.
    c = b[b["time_left_sec"].between(240, 420) & b["book_healthy"]
          & b["fav_ask"].between(0.55, 0.85)]
    out["E-flb(A)"] = L.first_tick(c, (c["fav_side"] == "yes").to_numpy())

    # E-model: calibrated P(Up|z) mispricing WITHOUT a spot-jump gate (prediction,
    # not a stale-quote pickoff) -> buy the model side, mid-window, buffered.
    sb = _sq_prep(b)
    zc, p = _fit_curve(sb[sb["date"] <= "2026-05-24"])
    sb = sb[np.isfinite(sb["z"])].copy()
    sb["model_p"] = np.interp(np.clip(sb["z"], -6, 6), zc, p, left=p[0], right=p[-1])
    sb["mis"] = sb["model_p"] - sb["yes_mid"]
    c = sb[sb["time_left_sec"].between(120, 600) & (sb["mis"].abs() >= 0.12) & sb["book_healthy"]]
    out["E-model(A)"] = L.first_tick(c, (c["mis"] > 0).to_numpy())

    # ---------------- Tier B: latency-exposed (must survive the sweep) ----------------
    # E-mom: last 60s, spot momentum AWAY from strike reinforcing the favourite.
    c = b[b["time_left_sec"].between(1, 60) & (b["abs_dist_bps"] >= 5) & b["consistent"]
          & (np.sign(b["spot_vel_10s_bps"]) == np.sign(b["dist_strike_bps"]))
          & b["fav_ask"].between(0.50, 0.90) & b["book_healthy"]]
    out["E-mom(B)"] = L.first_tick(c, (c["dist_strike_bps"] > 0).to_numpy())

    # E-imb: strong L2 book imbalance predicts the resolve side (mid-window).
    c = b[b["time_left_sec"].between(120, 600) & ((b["l2_imbalance"] - 0.5).abs() >= 0.25)
          & b["book_healthy"]]
    out["E-imb(B)"] = L.first_tick(c, (c["l2_imbalance"] > 0.5).to_numpy())

    return out


def run():
    os.makedirs(LEDGER_DIR, exist_ok=True)
    decs = edges()
    print(f"\n{'edge':16} {'n':>4} {'FULL EV':>8} {'FULL 90%CI':>16} "
          f"{'future EV':>9} {'fut 90%CI':>16} {'lat5s':>7} {'CPCV':>5} {'DSR':>5}")
    print("-" * 96)
    keep = []
    for name, dec in decs.items():
        led = L.simulate(dec, latency=2)
        n = 0 if led is None else len(led)
        if n < 3:
            print(f"{name:16} {n:>4}  (too few fills)")
            continue
        e = L.evaluate(led)
        lat = L.latency_survival(dec)
        fl = e["per_split"]["FULL"]
        fu = e["per_split"].get("future")
        l5 = lat.get(5, {}).get("ev")
        fu_ev = f"${fu['ev']:+.2f}" if fu else "n/a"
        fu_ci = f"[{fu['lo']:+.2f},{fu['hi']:+.2f}]" if fu else ""
        l5s = f"${l5:+.2f}" if l5 is not None else "-"
        print(f"{name:16} {e['n']:>4} ${fl['ev']:>+6.2f} [{fl['lo']:>+5.2f},{fl['hi']:>+5.2f}] "
              f"{fu_ev:>9} {fu_ci:>16} {l5s:>7} {e['cpcv'].get('pct_pos', 0):>4.0f}% "
              f"{e['dsr']['dsr']!s:>5}")
        led.to_parquet(os.path.join(LEDGER_DIR, f"edge_{name.split('(')[0]}.parquet"))
        keep.append((name, e, lat))
    print("\nRule: a real, OURS edge needs future CI lower-bound > 0 AND EV flat/positive "
          "across the latency sweep (Tier A) or surviving it (Tier B).")
    return keep


if __name__ == "__main__":
    run()
