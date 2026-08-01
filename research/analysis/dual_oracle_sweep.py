"""A3 — backtest the dual-oracle gate + refine det_d12 params (Chainlink-settled).

Everything runs through the shared edge_lab harness (Chainlink settlement, depth-capped fills,
window-clustered CIs, CPCV, DSR, latency survival), so results reconcile with every other edge.

Two things measured:
  1. GATE: det_d12 baseline vs + dual-oracle gate (AGREE / |cl_dist|>=k / both). Does requiring
     Chainlink to agree with Coinbase at entry lift the Chainlink win-rate back toward the ~83%
     the favourite-longshot payoff needs, while keeping usable volume?
  2. PARAMS: a focused grid around det_d12 (dist_min, max_ask, adverse-vel filter, fav-side,
     t_max) — the cheap YAML knobs, each varied with the rest fixed, future-blind.

Selection discipline: pick on dev + CPCV pct-pos + latency survival; the `future` block is
reported but NOT used to choose. Prefer the mechanistic AGREE gate (0 free params) to a fitted
|cl_dist| threshold on the short Chainlink sample.

Run: uv run python -m research.analysis.dual_oracle_sweep
"""
from __future__ import annotations
import numpy as np
import pandas as pd

from research.analysis import edge_lab
from research.analysis.dual_oracle_features import entry_cl_dist, load_chainlink_aged

# det_d12 defaults (mode=consistent, buy favourite)
DEF = dict(t_min=1, t_max=180, dist_min=12.0, ask_lo=0.50, ask_hi=0.85,
           adverse_vel_max=None, restrict_side=None)


def det_decision(b: pd.DataFrame, **p) -> pd.DataFrame:
    """det_d12 decision frame (first qualifying tick/window), mode=consistent."""
    q = {**DEF, **p}
    m = ((b["time_left_sec"] >= q["t_min"]) & (b["time_left_sec"] <= q["t_max"])
         & (b["abs_dist_bps"] >= q["dist_min"]) & (b["consistent"])
         & (b["fav_ask"].between(q["ask_lo"], q["ask_hi"])))
    if q["adverse_vel_max"] is not None and "adverse_vel_10s" in b.columns:
        m &= (b["adverse_vel_10s"] <= q["adverse_vel_max"])
    if q["restrict_side"] is not None and "fav_side" in b.columns:
        m &= (b["fav_side"] == q["restrict_side"])
    c = b[m]
    return edge_lab.first_tick(c, (c["yes_mid"] >= 0.5).to_numpy())


def apply_gate(decision: pd.DataFrame, b, cl, *, mode=None, cl_dist_min=None) -> pd.DataFrame:
    """Augment with Chainlink view; fail-closed (drop cl-unavailable) when a gate is set."""
    aug = entry_cl_dist(decision, base=b, cl=cl)
    if mode is None:
        return aug
    m = aug["cl_available"].to_numpy(dtype=bool, copy=True)
    if mode in ("agree", "agree_and_dist"):
        m &= aug["oracle_agree"].to_numpy(dtype=bool)
    if mode in ("dist", "agree_and_dist"):
        m &= (aug["cl_dist_bps"].abs() >= cl_dist_min).to_numpy(dtype=bool)
    return aug[m]


def _dec_cols(aug: pd.DataFrame) -> pd.DataFrame:
    """Strip back to the columns edge_lab.simulate needs (it re-merges book/outcome)."""
    keep = ["slug", "symbol", "date", "split", "window_start_ts", "entry_sec", "buy_yes"]
    return aug[[c for c in keep if c in aug.columns]].copy()


def _quick(name: str, aug: pd.DataFrame, lat: int = 2) -> dict:
    """Light report: n, FULL EV/CI/WR, future EV/CI — no CPCV/DSR (for grid cells)."""
    led = edge_lab.simulate(_dec_cols(aug), latency=lat)
    if led is None or len(led) < 3:
        print(f"  {name:34} n={0 if led is None else len(led):>4} (too few fills)")
        return {}
    ci = edge_lab._split_ci(led)
    fl, fu = ci["FULL"], ci.get("future")
    print(f"  {name:34} n={fl['n']:>4} FULL ${fl['ev']:+.2f}[{fl['lo']:+.2f},{fl['hi']:+.2f}] "
          f"WR{fl['wr']:.0f}% | future " +
          (f"${fu['ev']:+.2f}[{fu['lo']:+.2f},{fu['hi']:+.2f}]n{fu['n']}" if fu else "n/a"))
    return ci


def run():
    b = edge_lab.load_base()
    cl = load_chainlink_aged()
    base_dec = det_decision(b)

    print("=== det_d12 baseline (Chainlink-settled, edge_lab) ===")
    base_aug = apply_gate(base_dec, b, cl, mode=None)
    avail = base_aug[base_aug["cl_available"]]
    _quick("baseline (all windows)", base_aug)
    _quick("baseline (Chainlink-available)", avail)

    print("\n=== + dual-oracle GATE ===")
    _quick("AGREE", apply_gate(base_dec, b, cl, mode="agree"))
    for k in (4, 8, 12, 16):
        _quick(f"|cl_dist|>={k}", apply_gate(base_dec, b, cl, mode="dist", cl_dist_min=k))
    for k in (8, 12):
        _quick(f"AGREE & |cl_dist|>={k}", apply_gate(base_dec, b, cl, mode="agree_and_dist", cl_dist_min=k))

    print("\n=== PARAM grid (each varied, rest at det_d12 default; no gate) ===")
    print("  -- dist_min_bps --")
    for d in (12, 14, 16, 18, 20):
        _quick(f"dist_min={d}", apply_gate(det_decision(b, dist_min=d), b, cl))
    print("  -- max_ask (payoff-asymmetry lever) --")
    for a in (0.85, 0.80, 0.78, 0.75, 0.72):
        _quick(f"max_ask={a}", apply_gate(det_decision(b, ask_hi=a), b, cl))
    print("  -- adverse_vel_max (skip reverting spot) --")
    for v in (3.0, 2.0, 1.0):
        _quick(f"adverse_vel<={v}", apply_gate(det_decision(b, adverse_vel_max=v), b, cl))
    print("  -- restrict_fav_side --")
    for s in ("yes", "no"):
        _quick(f"side={s}", apply_gate(det_decision(b, restrict_side=s), b, cl))
    print("  -- t_max_sec --")
    for t in (120, 180, 240):
        _quick(f"t_max={t}", apply_gate(det_decision(b, t_max=t), b, cl))

    # ---- Full verdict (CPCV/DSR/latency) on the leading candidates ----
    print("\n=== FULL verdict on leading candidates (CPCV + DSR + latency) ===")
    cands = {
        "baseline (cl-avail)": apply_gate(base_dec, b, cl, mode=None),
        "AGREE gate": apply_gate(base_dec, b, cl, mode="agree"),
        "AGREE + max_ask0.80": apply_gate(det_decision(b, ask_hi=0.80), b, cl, mode="agree"),
        "AGREE + dist16": apply_gate(det_decision(b, dist_min=16), b, cl, mode="agree"),
    }
    for nm, aug in cands.items():
        a = aug[aug["cl_available"]] if "cl_available" in aug.columns else aug
        dec = _dec_cols(a)
        led = edge_lab.simulate(dec, latency=2)
        print(edge_lab.verdict_line(nm, led, edge_lab.latency_survival(dec)))


if __name__ == "__main__":
    run()
