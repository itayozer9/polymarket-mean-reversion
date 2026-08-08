"""Re-judge the deployed / candidate strategies under the LIVE-CALIBRATED fill model.

Every backtest before 2026-06-10 was judged under an idealized fill (best-ask walk at
fixed 2s latency, 100% match). The live record (322 intents, 4.5 days) shows the real
funnel: ~44% zero-fill hazard, knife-catch fills, 0.5-20s latency. This script replays
each strategy's EXACT entry filter (mirrored from strategies.yaml) over the joined
frame and scores it three ways:

  v2        — the legacy idealized model (walk_buy at fixed 2s): the old headline
  live_legacy  — live model, PRE-guard executor semantics (knife fills possible)
  live_guarded — live model, deployed-guard semantics (floor abort; what live should
                 look like after EXEC_GUARDS=on)

Known limitation (stated, not hidden): the zero-fill hazard is applied RANDOMLY, but
live misses are adversely selected (missed trades were winners: the attribution shows
+$100 of paper EV sat on zero-fill windows). ev_per_signal under this model is
therefore an OPTIMISTIC bound for pre-guard live; the guards' touch-start is designed
to break exactly that selection.

Run: uv run python -m research.analysis.rejudge_live_model [--stake 5] [--seed 0]
Out: data/research/live_gap/rejudge_live_model.jsonl + stdout table
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from research.analysis.edge_lab import cl_outcomes, load_base
from research.dataset.feeds import load_l2_ladders
from research.lib.stats import window_clustered_bootstrap
from research.clean_window import CLEAN_START, available_clean_dates
from research.sim.fills_live import (
    DEFAULT_PARAMS_PATH, load_params, sample_latency, simulate_taker_entry)
from research.sim.fills_v2 import walk_buy, settle_pnl

OUT = os.path.join("data", "research", "live_gap", "rejudge_live_model.jsonl")
_LV = range(1, 11)

# Entry filters mirrored from strategies.yaml (the deployed source of truth).
# NOTE det_lwd_live's min_strike_crossings>=1 gate is OMITTED (the joined frame has
# no crossings column) — slightly pessimistic for lwd (the gate lifted WR in dev).
CONFIGS = {
    "det_lwd_live": dict(mode="consistent", t_lo=1, t_hi=60, dist_min=8.0,
                         ask_lo=0.50, ask_hi=0.88, adverse_vel_max=2.0),
    "det_d12_wide_v1": dict(mode="consistent", t_lo=1, t_hi=180, dist_min=12.0,
                            ask_lo=0.50, ask_hi=0.85),
    "det_d12_dual_live": dict(mode="consistent", t_lo=1, t_hi=180, dist_min=12.0,
                              ask_lo=0.50, ask_hi=0.78, adverse_vel_max=2.0,
                              oracle_gate="agree", ask_hi_deep=0.85,
                              cl_dist_hi_bps=20.0),
    "fav_disagree": dict(mode="disagree", t_lo=120, t_hi=360, dist_min=10.0,
                         ask_lo=0.05, ask_hi=0.90),
    # early_disagree_live (added 2026-06-18 reckoning): same disagree mode, earlier
    # window + tighter cheap-side band (strategies.yaml). The one live edge needing a
    # firm clean-data verdict (4 straight losing real-money days).
    "early_disagree_live": dict(mode="disagree", t_lo=450, t_hi=900, dist_min=10.0,
                                ask_lo=0.30, ask_hi=0.45),
    "fav_momentum": dict(mode="consistent", t_lo=60, t_hi=120, dist_min=12.0,
                         ask_lo=0.50, ask_hi=0.90, adverse_vel_max=0.0),
    "fav_lowvol": dict(mode="consistent", t_lo=240, t_hi=420, dist_min=8.0,
                       ask_lo=0.55, ask_hi=0.80, vol_max=1.0),
    "fav_deepdown": dict(mode="consistent", t_lo=420, t_hi=480, dist_min=0.0,
                         ask_lo=0.88, ask_hi=0.93, restrict_fav_side="no"),
    # tadiv (added 2026-06-18): offline proxy of the deployed tadiv_approx twins.
    # APPROXIMATION — uses joined spot_vel_30s_bps (Δspot/spot) vs the engine's
    # RollingMove.vel_bps(30) (Δdist/strike); <1% in-window. buy the 30s-move side
    # when |v30|>=ret_min and sign(v10)==sign(v30). dist_min=0 (no distance gate).
    "tadiv_approx_v1": dict(mode="tadiv", t_lo=60, t_hi=300, dist_min=0.0,
                            ask_lo=0.30, ask_hi=0.55, ret_min=5.0),
    "tadiv_approx_ret3_v1": dict(mode="tadiv", t_lo=60, t_hi=300, dist_min=0.0,
                                 ask_lo=0.30, ask_hi=0.55, ret_min=3.0),
}


def decisions_for(name: str, b: pd.DataFrame) -> pd.DataFrame:
    """First qualifying tick per window under the named config's exact filter.
    Returns slug, symbol, date, split, entry_sec, time_left, buy_yes, entry_ask."""
    cfg = CONFIGS[name]
    m = ((b["time_left_sec"] >= cfg["t_lo"]) & (b["time_left_sec"] <= cfg["t_hi"])
         & (b["abs_dist_bps"] >= cfg["dist_min"])
         & (b["book_healthy"].fillna(False).astype(bool)))
    if cfg["mode"] == "consistent":
        m &= b["consistent"].fillna(False).astype(bool)
        m &= b["fav_ask"].between(cfg["ask_lo"], cfg["ask_hi"])
        if cfg.get("adverse_vel_max") is not None:
            m &= (b["adverse_vel_10s"] <= cfg["adverse_vel_max"])
        if cfg.get("vol_max") is not None:
            m &= (b["realized_vol"] * 100.0 <= cfg["vol_max"])
        if cfg.get("restrict_fav_side"):
            m &= (b["fav_side"].astype(str).str.lower()
                  .isin((cfg["restrict_fav_side"], "down" if
                         cfg["restrict_fav_side"] == "no" else "up")))
        c = b[m].copy()
        c["buy_yes"] = (c["fav_side"].astype(str).str.lower()
                        .isin(("yes", "up"))).to_numpy()
        c["entry_ask"] = c["fav_ask"].to_numpy("f8")
    elif cfg["mode"] == "disagree":
        m &= ~b["consistent"].fillna(True).astype(bool)
        c = b[m].copy()
        by = (c["dist_strike_bps"] > 0).to_numpy()
        ud_ask = np.where(by, c["yes_best_ask"].to_numpy("f8"),
                          1.0 - c["yes_best_bid"].to_numpy("f8"))
        keep = ((ud_ask >= cfg["ask_lo"]) & (ud_ask <= cfg["ask_hi"])
                & np.isfinite(ud_ask))
        c, by, ud_ask = c[keep], by[keep], ud_ask[keep]
        c["buy_yes"] = by
        c["entry_ask"] = ud_ask
    elif cfg["mode"] == "tadiv":
        # buy the 30s-spot-move side when |v30|>=ret_min and v10 agrees in sign.
        v30, v10 = b["spot_vel_30s_bps"], b["spot_vel_10s_bps"]
        m &= ((v30.abs() >= cfg["ret_min"]) & (np.sign(v30) == np.sign(v10))
              & v30.notna() & v10.notna())
        c = b[m].copy()
        by = (c["spot_vel_30s_bps"] > 0).to_numpy()
        ud_ask = np.where(by, c["yes_best_ask"].to_numpy("f8"),
                          1.0 - c["yes_best_bid"].to_numpy("f8"))
        keep = ((ud_ask >= cfg["ask_lo"]) & (ud_ask <= cfg["ask_hi"])
                & np.isfinite(ud_ask))
        c, by, ud_ask = c[keep], by[keep], ud_ask[keep]
        c["buy_yes"] = by
        c["entry_ask"] = ud_ask
    else:
        raise ValueError(f"unknown mode {cfg['mode']}")
    if c.empty:
        return pd.DataFrame(columns=["slug", "symbol", "date", "split", "entry_sec",
                                     "time_left", "buy_yes", "entry_ask"])
    first = (c.sort_values(["slug", "seconds_into_window"])
             .groupby("slug", as_index=False).first())
    first = first.rename(columns={"seconds_into_window": "entry_sec",
                                  "time_left_sec": "time_left"})
    keep = ["slug", "symbol", "date", "split", "entry_sec", "time_left",
            "buy_yes", "entry_ask"]
    for extra in ("window_start_ts", "cl_dist_bps"):
        if extra in first.columns:
            keep.append(extra)
    return first[keep]


def effective_ceiling(cfg: dict, cl_dist_bps: Optional[float]) -> float:
    """The adaptive max_ask the executor actually enforces (dual: 0.78 base,
    0.85 when the Chainlink lock is deep)."""
    hi = cfg.get("ask_hi_deep")
    if hi is not None and cl_dist_bps is not None and np.isfinite(cl_dist_bps) \
            and abs(cl_dist_bps) >= cfg.get("cl_dist_hi_bps", 1e9):
        return float(hi)
    return float(cfg["ask_hi"])


def _apply_dual_gate(dec: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    """AGREE gate + cl_dist column via the dual-oracle helpers (same code path
    the deployed config was validated with)."""
    from research.analysis.dual_oracle_features import entry_cl_dist, load_chainlink_aged
    cl = load_chainlink_aged()
    d = dec.rename(columns={"entry_sec": "entry_sec"})
    aug = entry_cl_dist(d.rename(columns={"time_left": "time_left_sec"}), base=b, cl=cl)
    aug = aug[aug["oracle_agree"].fillna(False).astype(bool)].copy()
    aug = aug.rename(columns={"time_left_sec": "time_left"})
    keep = [c for c in ("slug", "symbol", "date", "split", "entry_sec", "time_left",
                        "buy_yes", "entry_ask", "cl_dist_bps") if c in aug.columns]
    return aug[keep]


_LADDERS = {}


def _ladder(sym):
    if sym not in _LADDERS:
        end = (available_clean_dates(sym) or [CLEAN_START])[-1]
        _LADDERS[sym] = load_l2_ladders(sym, CLEAN_START, end)
    return _LADDERS[sym]


def simulate_config(dec: pd.DataFrame, cfg: dict, mode: str, params, stake: float,
                    seed: int = 0) -> pd.DataFrame:
    """Per-decision fill + Chainlink settle. mode: v2 | live_legacy | live_guarded."""
    if dec.empty:
        return pd.DataFrame()
    rng = np.random.default_rng(seed)
    cl = cl_outcomes().set_index("slug")["cl_up"].to_dict()
    rows = []
    for _, r in dec.iterrows():
        if r["slug"] not in cl:
            continue
        L = _ladder(r["symbol"])
        lat = 2.0 if mode == "v2" else sample_latency(rng, params)
        try:
            lr = L.loc[(r["slug"], int(r["entry_sec"]) + int(round(lat)))]
        except KeyError:
            rows.append(dict(slug=r["slug"], split=r["split"], filled=0))
            continue
        if isinstance(lr, pd.DataFrame):
            lr = lr.iloc[0]
        by = bool(r["buy_yes"])
        if by:
            px = [lr[f"ask_px_{i}"] for i in _LV]
            sz = [lr[f"ask_sz_{i}"] for i in _LV]
        else:
            px = [1.0 - lr[f"bid_px_{i}"] for i in _LV]
            sz = [lr[f"bid_sz_{i}"] for i in _LV]
        ceiling = effective_ceiling(cfg, r.get("cl_dist_bps"))
        # Outcome is known here (rows without a label were skipped above). Hand it to the
        # fill model so the zero-fill hazard is drawn ADVERSELY: live misses are not
        # random, they skew to the winners (lambda 1.42, measured 2026-08-07). Passing it
        # is a no-op when the params carry adverse_tilt == 1.0, so runs against the
        # pre-2026-08-07 params file reproduce the old numbers exactly.
        won = (cl[r["slug"]] == 1) if by else (cl[r["slug"]] == 0)
        if mode == "v2":
            pxa = np.asarray(px, dtype="f8")
            sza = np.asarray(sz, dtype="f8")
            band = pxa <= ceiling + 1e-9
            f = walk_buy(pxa[band], sza[band], stake)
        else:
            f = simulate_taker_entry(
                px, sz, stake, entry_ask=float(r["entry_ask"]), max_ask=ceiling,
                time_left=float(r["time_left"]), rng=rng, params=params,
                mode=("guarded" if mode == "live_guarded" else "legacy"),
                wins=bool(won))
        if not f.filled or f.unfilled_usd > stake * 0.5:
            rows.append(dict(slug=r["slug"], split=r["split"], filled=0))
            continue
        rows.append(dict(slug=r["slug"], split=r["split"], filled=1, won=int(won),
                         pnl=float(settle_pnl(f, won)),
                         avg_price=float(f.avg_price)))
    return pd.DataFrame(rows)


def _ci(t: pd.DataFrame):
    f = t[t["filled"] == 1]
    if len(f) < 3:
        return dict(n=int(len(f)), ev=None, lo=None, hi=None, wr=None, total=None)
    lo, _, hi = window_clustered_bootstrap(f["pnl"].values, f["slug"].values, n=2000)
    return dict(n=int(len(f)), wr=round(float(f["won"].mean() * 100), 1),
                ev=round(float(f["pnl"].mean()), 3), lo=round(float(lo), 3),
                hi=round(float(hi), 3), total=round(float(f["pnl"].sum()), 1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stake", type=float, default=5.0)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--only", default=None)
    ap.add_argument("--params", default=str(DEFAULT_PARAMS_PATH),
                    help="fill-model params JSON. Point at a pre-2026-08-07 file "
                         "(adverse_tilt absent => 1.0) to reproduce the old numbers.")
    args = ap.parse_args()

    params = load_params(Path(args.params))
    b = load_base()
    out = []
    names = [args.only] if args.only else list(CONFIGS)
    print(f"re-judging {len(names)} configs at ${args.stake:.0f}/trade "
          f"(fill model {params.version}, {params.n_attempts} attempts, "
          f"kappa={params.kappa}, adverse_tilt={params.adverse_tilt})\n")
    hdr = (f"{'config':18s} {'model':12s} {'sig':>5s} {'fill%':>6s} "
           f"{'EV/fill':>8s} {'CI':>16s} {'WR%':>5s} {'EV/sig':>7s} {'future EV':>20s}")
    print(hdr)
    print("-" * len(hdr))
    for name in names:
        cfg = CONFIGS[name]
        dec = decisions_for(name, b)
        if cfg.get("oracle_gate") == "agree" and not dec.empty:
            dec = _apply_dual_gate(dec, b)
        for mode in ("v2", "live_legacy", "live_guarded"):
            t = simulate_config(dec, cfg, mode, params, args.stake, seed=args.seed)
            n_sig = int(len(t))
            full = _ci(t)
            fut = _ci(t[t["split"] == "future"]) if n_sig else {}
            fill_rate = (full["n"] / n_sig) if n_sig else 0.0
            ev_sig = (full["total"] / n_sig) if (n_sig and full["total"] is not None) else None
            rec = dict(config=name, model=mode, n_signals=n_sig,
                       fill_rate=round(fill_rate, 3), full=full, future=fut,
                       ev_per_signal=(round(ev_sig, 3) if ev_sig is not None else None),
                       stake=args.stake, seed=args.seed,
                       fill_model_version=params.version)
            out.append(rec)
            ci_s = (f"[{full['lo']},{full['hi']}]" if full["ev"] is not None else "-")
            fut_s = (f"${fut.get('ev')}[{fut.get('lo')},{fut.get('hi')}] n={fut.get('n')}"
                     if fut.get("ev") is not None else "-")
            print(f"{name:18s} {mode:12s} {n_sig:5d} {fill_rate*100:5.1f}% "
                  f"{('$'+str(full['ev'])) if full['ev'] is not None else '-':>8s} "
                  f"{ci_s:>16s} {str(full.get('wr','-')):>5s} "
                  f"{('$'+str(rec['ev_per_signal'])) if ev_sig is not None else '-':>7s} "
                  f"{fut_s:>20s}")
        print()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        for r in out:
            f.write(json.dumps(r) + "\n")
    print(f"-> {OUT}")
    # sanity: the dual config under the OLD model must reproduce the documented
    # ballpark (+$1.3..2.7/tr full-window at $10 was the validated figure; scaled
    # by stake). A wild miss means the filter drifted from strategies.yaml.
    dual_v2 = next((r for r in out if r["config"] == "det_d12_dual_live"
                    and r["model"] == "v2"), None)
    if dual_v2 and dual_v2["full"]["ev"] is not None:
        scaled = dual_v2["full"]["ev"] * (10.0 / args.stake)
        if not (0.3 <= scaled <= 5.0):
            print(f"!! SANITY WARNING: dual v2 EV ${dual_v2['full']['ev']} "
                  f"(${scaled:.2f} at $10) is far from the documented +$1.3-2.7 — "
                  f"check filter drift vs strategies.yaml")


if __name__ == "__main__":
    main()
