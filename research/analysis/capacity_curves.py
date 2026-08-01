"""capacity_curves — how big can each validated/candidate config trade before
fills/EV degrade? (BC3, pre-registered in docs/research/test_ledger.md
"Burst-cap + capacity study (2026-06-11)".)

For each config — det_lwd_live, det_d12_dual_live (AGREE gate), the deployed
fav_disagree_live 0.45 band, early_disagree (deployed early_disagree_live params),
psettle_2246 (livecost survivor), OP4 near-strike fade — compute the stake curve
at $5/$10/$25/$50 under BOTH fill models:

  v2           idealized: fixed 2s latency, full 10-level L2 walk_buy in-band
  live_guarded fills_live physics: sampled empirical latency, zero-fill hazard by
               [depth-ratio x time-left] (depth ratio SCALES with stake), kappa
               haircut, guard floor entry_ask-0.04, ceiling = the config's max_ask
               (adaptive for the dual config). Pattern: rejudge_live_model.
               simulate_config, extended to record slippage.

Per (config, stake, model): fill rate, EV/fill (slug-clustered CI, full + future),
EV/signal, avg slippage vs the signal-tick ask. MAX VIABLE STAKE (registered) =
largest stake whose FUTURE live_guarded seed-0 EV/fill CI-lower > 0 AND seeds 0-4
mean > 0. Portfolio $/day = sum of future fills/day x seeds-mean EV/fill at the
max-viable stakes (macro-correlation caveat applies — these are NOT independent
books).

Run: uv run python -m research.analysis.capacity_curves [--only NAME]
Out: data/research/capacity_curves/capacity.jsonl + stdout tables
"""
from __future__ import annotations

import argparse
import json
import os

import numpy as np
import pandas as pd

import research.analysis.hypothesis_sweep as hyp_sweep
import research.analysis.rejudge_live_model as rj
from research.analysis.edge_lab import cl_outcomes, load_base
from research.lib.stats import window_clustered_bootstrap
from research.sim.fills_live import (
    DEFAULT_PARAMS_PATH, load_params, sample_latency, simulate_taker_entry)
from research.sim.fills_v2 import settle_pnl, walk_buy

OUT_DIR = os.path.join("data", "research", "capacity_curves")
FUTURE_START = "2026-06-05"
STAKES = (5.0, 10.0, 25.0, 50.0)
SEEDS = (0, 1, 2, 3, 4)
_LV = range(1, 11)
WINDOWS_PER_DAY = 96 * 4   # 15m windows x 4 coins

# ---------------------------------------------------------------------------
# configs under test. kind:
#   rejudge        — rejudge_live_model.decisions_for (CONFIGS name or custom cfg)
#   psettle        — hypothesis_sweep.fam_psettle with the livecost spec params
#   fade           — oracle_print_model.fade_decisions (OP4)
# ceiling_cfg feeds rj.effective_ceiling (ask_hi / adaptive ask_hi_deep).
# ---------------------------------------------------------------------------
CONFIGS = {
    "det_lwd_live": dict(kind="rejudge", ceiling_cfg=rj.CONFIGS["det_lwd_live"]),
    "det_d12_dual_live": dict(kind="rejudge", dual_gate=True,
                              ceiling_cfg=rj.CONFIGS["det_d12_dual_live"]),
    "fav_disagree_live045": dict(
        kind="rejudge",
        custom=dict(mode="disagree", t_lo=120, t_hi=360, dist_min=10.0,
                    ask_lo=0.05, ask_hi=0.45),
        ceiling_cfg=dict(ask_hi=0.45)),
    "early_disagree": dict(
        kind="rejudge",
        custom=dict(mode="disagree", t_lo=450, t_hi=900, dist_min=10.0,
                    ask_lo=0.30, ask_hi=0.45),
        ceiling_cfg=dict(ask_hi=0.45)),
    # livecost specs.jsonl psettle_2246 params (the campaign's one new edge)
    "psettle_2246": dict(
        kind="psettle",
        params=dict(side="cheap", d_thr=0.15, cl_floor=0, t_lo=60, t_hi=360,
                    ask_lo=0.50, ask_hi=0.78),
        ceiling_cfg=dict(ask_hi=0.78)),
    # OP4 near-strike fade (fill ceiling 0.40 per the registered rule)
    "fade_op4": dict(kind="fade", ceiling_cfg=dict(ask_hi=0.40)),
}


# ---------------------------------------------------------------------------
# decision-frame builders -> [slug,symbol,date,split,entry_sec,time_left,
#                             buy_yes,entry_ask,(cl_dist_bps)]
# ---------------------------------------------------------------------------
def first_tick_decisions(c: pd.DataFrame, buy_yes, entry_ask) -> pd.DataFrame:
    """Reduce gated ticks to the first qualifying tick per window, carrying the
    side + executable ask chosen AT that tick (pure helper, unit-tested)."""
    c = c.copy()
    c["buy_yes"] = np.asarray(buy_yes, dtype=bool)
    c["entry_ask"] = np.asarray(entry_ask, dtype="f8")
    first = (c.sort_values(["slug", "seconds_into_window"])
             .groupby("slug", as_index=False).first())
    first = first.rename(columns={"seconds_into_window": "entry_sec",
                                  "time_left_sec": "time_left"})
    keep = [k for k in ("slug", "symbol", "date", "split", "entry_sec",
                        "time_left", "buy_yes", "entry_ask", "cl_dist_bps")
            if k in first.columns]
    return first[keep]


def build_decisions(name: str, b: pd.DataFrame) -> pd.DataFrame:
    spec = CONFIGS[name]
    if spec["kind"] == "rejudge":
        if "custom" in spec:
            rj.CONFIGS.setdefault(name, spec["custom"])
        dec = rj.decisions_for(name, b)
        if spec.get("dual_gate"):
            dec = rj._apply_dual_gate(dec, b)
        return dec
    if spec["kind"] == "psettle":
        c, by = hyp_sweep.fam_psettle(b, spec["params"])
        yes_ask = c["yes_best_ask"].to_numpy("f8")
        no_ask = 1.0 - c["yes_best_bid"].to_numpy("f8")
        ask = np.where(np.asarray(by, dtype=bool), yes_ask, no_ask)
        return first_tick_decisions(c, by, ask)
    if spec["kind"] == "fade":
        from research.analysis.oracle_print_model import (
            fade_decisions, load_model, tick_feature_frame)
        dec = fade_decisions(tick_feature_frame(), load_model())
        return hyp_sweep.apply_future_override(dec)
    raise ValueError(f"unknown kind {spec['kind']}")


# ---------------------------------------------------------------------------
# fill engine — rejudge_live_model.simulate_config + slippage/unfilled fields
# ---------------------------------------------------------------------------
def simulate_decisions(dec: pd.DataFrame, ceiling_cfg: dict, mode: str, params,
                       stake: float, seed: int = 0) -> pd.DataFrame:
    """Per-decision fill + Chainlink settle. mode: v2 | live_guarded.
    Returns one row per decision with a settlement label: filled, won, pnl,
    avg_price, slip (avg_price - signal-tick entry_ask), unfilled_frac."""
    if dec.empty:
        return pd.DataFrame()
    rng = np.random.default_rng(seed)
    cl = cl_outcomes().set_index("slug")["cl_up"].to_dict()
    rows = []
    for _, r in dec.iterrows():
        if r["slug"] not in cl:
            continue
        L = rj._ladder(r["symbol"])
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
        ceiling = rj.effective_ceiling(ceiling_cfg, r.get("cl_dist_bps"))
        if mode == "v2":
            pxa = np.asarray(px, dtype="f8")
            sza = np.asarray(sz, dtype="f8")
            band = pxa <= ceiling + 1e-9
            f = walk_buy(pxa[band], sza[band], stake)
        else:
            f = simulate_taker_entry(
                px, sz, stake, entry_ask=float(r["entry_ask"]), max_ask=ceiling,
                time_left=float(r["time_left"]), rng=rng, params=params,
                mode="guarded")
        if not f.filled or f.unfilled_usd > stake * 0.5:
            rows.append(dict(slug=r["slug"], split=r["split"], filled=0))
            continue
        won = (cl[r["slug"]] == 1) if by else (cl[r["slug"]] == 0)
        rows.append(dict(
            slug=r["slug"], split=r["split"], filled=1, won=int(won),
            pnl=float(settle_pnl(f, won)), avg_price=float(f.avg_price),
            slip=float(f.avg_price - float(r["entry_ask"])),
            unfilled_frac=float(f.unfilled_usd / stake)))
    return pd.DataFrame(rows)


def _ci(f: pd.DataFrame) -> dict:
    if len(f) < 3:
        return dict(n=int(len(f)), ev=None, lo=None, hi=None)
    lo, _, hi = window_clustered_bootstrap(f["pnl"].values, f["slug"].values, n=2000)
    return dict(n=int(len(f)), ev=round(float(f["pnl"].mean()), 3),
                lo=round(float(lo), 3), hi=round(float(hi), 3),
                wr=round(float(f["won"].mean() * 100), 1),
                total=round(float(f["pnl"].sum()), 1))


def stake_point(dec: pd.DataFrame, ceiling_cfg: dict, mode: str, params,
                stake: float) -> dict:
    """One (stake, model) point of the capacity curve."""
    t0 = simulate_decisions(dec, ceiling_cfg, mode, params, stake, seed=0)
    n_sig = int(len(t0))
    f0 = t0[t0["filled"] == 1] if n_sig else t0
    fut0 = f0[f0["split"] == "future"] if len(f0) else f0
    n_fut_sig = int((t0["split"] == "future").sum()) if n_sig else 0
    rec = dict(
        stake=stake, model=mode, n_signals=n_sig, n_fut_signals=n_fut_sig,
        fill_rate=round(len(f0) / max(n_sig, 1), 3),
        fut_fill_rate=round(len(fut0) / max(n_fut_sig, 1), 3),
        avg_slip=round(float(f0["slip"].mean()), 4) if len(f0) else None,
        avg_unfilled=round(float(f0["unfilled_frac"].mean()), 3) if len(f0) else None,
        full=_ci(f0), future=_ci(fut0),
        ev_per_signal=round(float(f0["pnl"].sum()) / n_sig, 3) if n_sig and len(f0) else None,
        fut_ev_per_signal=(round(float(fut0["pnl"].sum()) / n_fut_sig, 3)
                           if n_fut_sig and len(fut0) else None),
    )
    if mode == "live_guarded":
        evs, fills = [], []
        for s in SEEDS:
            t = (t0 if s == 0 else
                 simulate_decisions(dec, ceiling_cfg, mode, params, stake, seed=s))
            f = t[t["filled"] == 1]
            fu = f[f["split"] == "future"]
            evs.append(float(fu["pnl"].mean()) if len(fu) else np.nan)
            fills.append(int(len(fu)))
        rec["fut_seeds"] = dict(
            ev_mean=round(float(np.nanmean(evs)), 3),
            ev_sd=round(float(np.nanstd(evs)), 3),
            fills_mean=round(float(np.mean(fills)), 1))
    return rec


def max_viable_stake(points: list[dict]) -> float | None:
    """Pre-registered: largest stake with future live_guarded seed-0 CI-lower > 0
    AND seeds 0-4 mean EV/fill > 0."""
    best = None
    for p in points:
        if p["model"] != "live_guarded":
            continue
        fut, seeds = p["future"], p.get("fut_seeds") or {}
        if (fut.get("lo") is not None and fut["lo"] > 0
                and (seeds.get("ev_mean") or 0) > 0):
            best = max(best or 0, p["stake"])
    return best


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", default=None)
    args = ap.parse_args()

    hyp_sweep.set_future_override(FUTURE_START)
    b = hyp_sweep.apply_future_override(load_base())
    params = load_params(DEFAULT_PARAMS_PATH)
    fut_days = b.loc[b["split"] == "future", "slug"].nunique() / WINDOWS_PER_DAY
    print(f"capacity_curves: future={FUTURE_START}.. ({fut_days:.2f} day-equiv), "
          f"fill model {params.version} (kappa={params.kappa})\n")

    names = [args.only] if args.only else list(CONFIGS)
    os.makedirs(OUT_DIR, exist_ok=True)
    out, summary = [], []
    decsets = {}
    for name in names:
        dec = build_decisions(name, b)
        decsets[name] = set(dec["slug"])
        print(f"=== {name}: {len(dec)} decisions "
              f"(future {int((dec['split'] == 'future').sum())}) ===")
        points = []
        for stake in STAKES:
            for mode in ("v2", "live_guarded"):
                p = stake_point(dec, CONFIGS[name]["ceiling_cfg"], mode, params, stake)
                points.append(p)
                fut, fl = p["future"], p["full"]
                seeds = p.get("fut_seeds") or {}
                seed_s = (f" seeds {seeds.get('ev_mean')}±{seeds.get('ev_sd')}"
                          if seeds else "")
                print(f"  ${stake:>4.0f} {mode:12s} fill {p['fill_rate']*100:5.1f}% "
                      f"(fut {p['fut_fill_rate']*100:5.1f}%) "
                      f"slip {p['avg_slip'] if p['avg_slip'] is not None else '-':>7} "
                      f"| FULL ev {fl.get('ev')} | fut ev {fut.get('ev')} "
                      f"[{fut.get('lo')},{fut.get('hi')}] n={fut.get('n')} "
                      f"ev/sig {p['fut_ev_per_signal']}{seed_s}")
        mvs = max_viable_stake(points)
        # fills/day + $/day at the max viable stake (live seeds mean)
        rate = None
        if mvs is not None:
            pt = next(p for p in points
                      if p["model"] == "live_guarded" and p["stake"] == mvs)
            seeds = pt.get("fut_seeds") or {}
            rate = dict(stake=mvs,
                        fills_per_day=round(seeds.get("fills_mean", 0) / fut_days, 1),
                        ev_fill=seeds.get("ev_mean"),
                        usd_per_day=round(seeds.get("fills_mean", 0) / fut_days
                                          * (seeds.get("ev_mean") or 0), 1))
        print(f"  -> MAX VIABLE STAKE: {('$%.0f' % mvs) if mvs else 'none at $5 (not stake-ready)'}"
              f"{'  ~' + str(rate['fills_per_day']) + ' fills/day x $' + str(rate['ev_fill']) + ' = $' + str(rate['usd_per_day']) + '/day' if rate else ''}\n")
        out.append(dict(config=name, n_decisions=int(len(dec)),
                        n_fut_decisions=int((dec["split"] == "future").sum()),
                        points=points, max_viable_stake=mvs, at_max=rate))
        summary.append((name, mvs, rate))

    # pairwise decision-slug overlap (portfolio honesty)
    print("=== pairwise decision-slug Jaccard (portfolio overlap) ===")
    ks = list(decsets)
    for i, a in enumerate(ks):
        for bn in ks[i + 1:]:
            ja = (len(decsets[a] & decsets[bn]) / len(decsets[a] | decsets[bn])
                  if decsets[a] | decsets[bn] else 0.0)
            if ja >= 0.01:
                print(f"  {a:22s} x {bn:22s} {ja:.3f}")

    total = sum(r["usd_per_day"] for _, m, r in summary if r)
    print(f"\n=== portfolio at max-viable stakes: ${total:.0f}/day "
          f"(future-block estimate; macro-correlated, NOT independent books) ===")
    for name, mvs, rate in summary:
        if rate:
            print(f"  {name:22s} ${rate['stake']:>3.0f} x {rate['fills_per_day']:>5.1f} "
                  f"fills/day x ${rate['ev_fill']:+.2f} = ${rate['usd_per_day']:+.1f}/day")
        else:
            print(f"  {name:22s} not stake-ready (no stake passes the registered rule)")

    with open(os.path.join(OUT_DIR, "capacity.jsonl"), "w") as fh:
        for r in out:
            fh.write(json.dumps(r) + "\n")
    print(f"\n-> {OUT_DIR}/capacity.jsonl")


if __name__ == "__main__":
    main()
