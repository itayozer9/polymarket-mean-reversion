"""hypothesis_verify — Phase 4 deterministic verification of shortlisted survivors.

For each survivor it re-runs the rule through the FULL 10-level L2 ladder-walk
(realistic capacity-aware fills, not top-of-book), Chainlink-settled, and reports
the evidence a skeptic needs to judge whether the edge is real:

  - per-split EV incl. the held-out FUTURE block (revealed once, here)
  - CAPACITY CURVE: fill rate / slippage / unfilled at $10/25/50/100/250
  - COST-STRESS: fee 1.5x, +1c slippage, latency 5/10s, 30% rejects, ALL-combined
  - PER-REGIME: quiet vs volatile (realized-vol median split)
  - DIRECTIONAL BALANCE: buy-UP vs buy-DOWN EV (a real edge is not just a drift)
  - JACCARD overlap vs the known edges (det / e4 / fav-value) — is this a NEW edge
    or the same one in disguise?

Reuses fills_v2.walk_buy + feeds.load_l2_ladders (same fill model as gauntlet.py).

Fill models (--fill-model, default v2 — byte-identical to the original behavior):
  v2    fixed 2s latency, full L2 walk_buy at the fill tick (idealized; the old headline)
  live  live-calibrated executor physics (research/sim/fills_live.py, params
        data/research/fill_model_live.json): empirical latency SAMPLED per trade,
        zero-fill hazard by [depth x time-left], kappa depth haircut, guarded-band
        semantics (entry_ask = best in-band ask on the SIGNAL-second ladder; ladder
        restricted to [entry_ask-0.04, ceiling]; ceiling = the family's ask_hi if it
        has one else entry_ask+0.07 capped at 0.92). Pattern:
        rejudge_live_model.simulate_config.

Run: uv run python -m research.analysis.hypothesis_verify           # all shortlist
     uv run python -m research.analysis.hypothesis_verify det_0123  # one spec id
     uv run python -m research.analysis.hypothesis_verify --fill-model live \
         --stake 5 --future-start 2026-06-05 --dir data/research/hypotheses/X \
         --out .../verified_live.jsonl --extended-known   # live-cost campaign
"""
from __future__ import annotations
import argparse
import json
import os

import numpy as np
import pandas as pd

import research.analysis.hypothesis_sweep as hyp_sweep
from research.analysis.hypothesis_sweep import BUILDERS
from research.analysis.edge_lab import load_base, cl_outcomes
from research.sim.fills_v2 import walk_buy, settle_pnl, FeeSchedule, DEFAULT_FEES
from research.sim.fills_live import (
    load_params as load_live_params, sample_latency, simulate_taker_entry)
from research.dataset.feeds import load_l2_ladders
from research.lib.stats import window_clustered_bootstrap
from research.clean_window import CLEAN_START, available_clean_dates

OUT_DIR = os.path.join("data", "research", "hypotheses")
SHORTLIST = os.path.join(OUT_DIR, "shortlist.jsonl")
VERIFIED = os.path.join(OUT_DIR, "verified.jsonl")
STAKE = 10.0
LIVE_SEEDS = (0, 1, 2, 3, 4)   # MC robustness for the sampled-latency/hazard model
_LV = range(1, 11)
_LAD = {}


# ---------------------------------------------------------------------------
# live fill model — pure helpers (unit-tested in tests/research/)
# ---------------------------------------------------------------------------
def _side_ladder(lr, buy_yes: bool):
    """YES-equivalent ask ladder of the bought side from a raw L2 row.
    buy UP -> lift the YES asks; buy DOWN -> hit the YES bids at 1-px."""
    if buy_yes:
        px = [lr[f"ask_px_{i}"] for i in _LV]
        sz = [lr[f"ask_sz_{i}"] for i in _LV]
    else:
        px = [1.0 - lr[f"bid_px_{i}"] for i in _LV]
        sz = [lr[f"bid_sz_{i}"] for i in _LV]
    return np.asarray(px, dtype="f8"), np.asarray(sz, dtype="f8")


def spec_ask_hi(params: dict) -> float | None:
    """The family's registered ask ceiling, if it has one (ask_hi for the
    consistent-side families, ud_ask_max for the disagree families)."""
    for k in ("ask_hi", "ud_ask_max"):
        v = params.get(k)
        if v is not None:
            return float(v)
    return None


def live_entry_fill(lr_sig, lr_fill, buy_yes: bool, stake: float, *,
                    ask_hi: float | None, time_left: float,
                    rng: np.random.Generator, params, fees=DEFAULT_FEES):
    """One trade under the deployed-executor (guarded) semantics:
      entry_ask = best in-band ask at the SIGNAL second (band: <= ask_hi if the
                  family has one, else <= 0.92) -> None if no in-band quote;
      ceiling   = family ask_hi if present else entry_ask + 0.07 capped 0.92;
      fill      = simulate_taker_entry on the FILL-second ladder (sampled-latency
                  row), guarded floor entry_ask - 0.04, zero-fill hazard + kappa.
    Returns (Fill, entry_ask, ceiling) or (None, None, None) if the signal-second
    book has no in-band ask."""
    px0, _ = _side_ladder(lr_sig, buy_yes)
    band_hi = float(ask_hi) if ask_hi is not None else 0.92
    in_band = np.isfinite(px0) & (px0 > 0) & (px0 <= band_hi + 1e-9)
    if not in_band.any():
        return None, None, None
    entry_ask = float(px0[in_band].min())
    ceiling = float(ask_hi) if ask_hi is not None else min(entry_ask + 0.07, 0.92)
    px, sz = _side_ladder(lr_fill, buy_yes)
    f = simulate_taker_entry(
        px, sz, stake, entry_ask=entry_ask, max_ask=ceiling,
        time_left=float(time_left), rng=rng, params=params, fees=fees,
        mode="guarded")
    return f, entry_ask, ceiling


def _ladders():
    if not _LAD:
        end = (available_clean_dates("btc") or [CLEAN_START])[-1]
        for s in ("btc", "eth", "sol", "xrp"):
            _LAD[s] = load_l2_ladders(s, CLEAN_START, end)
    return _LAD


def _decision(spec, b):
    """first qualifying tick per window, keeping the fields the L2 walk needs."""
    fam, p = spec["family"], spec["params"]
    c, by = BUILDERS[fam](b, p)
    if c is None or len(c) == 0:
        return pd.DataFrame()
    c = c.copy()
    c["buy_yes"] = np.asarray(by, dtype=bool)
    first = (c.sort_values(["slug", "seconds_into_window"])
             .groupby("slug", as_index=False).first())
    keep = ["slug", "symbol", "date", "split", "seconds_into_window",
            "buy_yes", "realized_vol"]
    if "time_left_sec" in first.columns:   # used by the live fill model only
        keep.append("time_left_sec")
    return first[keep]


def _trades(dec, *, latency=2, fee_mult=1.0, slippage_c=0.0, reject_prob=0.0,
            stake=STAKE, seed=0, fill_model="v2", spec_params=None,
            live_params=None):
    """walk the real L2 ladder for each decision; Chainlink-settled per-trade PnL.

    fill_model="v2" (default): the original idealized path — UNCHANGED.
    fill_model="live": live-calibrated physics — per-trade sampled latency,
      signal-second entry_ask, guarded band, zero-fill hazard + kappa
      (live_entry_fill above). slippage_c / reject_prob are v2-only stress knobs.
    """
    if dec.empty:
        return pd.DataFrame()
    fees = FeeSchedule(rate=0.07 * fee_mult)
    rng = np.random.default_rng(seed)
    cl = cl_outcomes().set_index("slug")["cl_up"].to_dict()
    lad = _ladders()
    ask_hi = spec_ask_hi(spec_params or {}) if fill_model == "live" else None
    rows = []
    for _, r in dec.iterrows():
        if reject_prob > 0 and rng.random() < reject_prob:
            continue
        L = lad.get(r["symbol"])
        if L is None or r["slug"] not in cl:
            continue
        by = bool(r["buy_yes"])
        entry_sec = int(r["seconds_into_window"])
        if fill_model == "live":
            lat = sample_latency(rng, live_params)
            try:
                lr_sig = L.loc[(r["slug"], entry_sec)]
                lr_fill = L.loc[(r["slug"], entry_sec + int(round(lat)))]
            except KeyError:
                continue
            if isinstance(lr_sig, pd.DataFrame):
                lr_sig = lr_sig.iloc[0]
            if isinstance(lr_fill, pd.DataFrame):
                lr_fill = lr_fill.iloc[0]
            tl = float(r.get("time_left_sec", 900 - entry_sec))
            f, entry_ask, _ = live_entry_fill(
                lr_sig, lr_fill, by, stake, ask_hi=ask_hi, time_left=tl,
                rng=rng, params=live_params, fees=fees)
            if f is None:
                continue
            slip = (float(f.avg_price) - entry_ask) if f.filled else 0.0
        else:
            try:
                lr = L.loc[(r["slug"], entry_sec + latency)]
            except KeyError:
                continue
            if isinstance(lr, pd.DataFrame):
                lr = lr.iloc[0]
            if by:
                px = [lr[f"ask_px_{i}"] + slippage_c for i in _LV]
                sz = [lr[f"ask_sz_{i}"] for i in _LV]
            else:
                px = [1.0 - lr[f"bid_px_{i}"] + slippage_c for i in _LV]
                sz = [lr[f"bid_sz_{i}"] for i in _LV]
            f = walk_buy(px, sz, stake, fees=fees)
            slip = float(f.slippage_vs_best)
        if not f.filled or f.unfilled_usd > stake * 0.5:
            continue
        won = (cl[r["slug"]] == 1) if by else (cl[r["slug"]] == 0)
        rows.append(dict(slug=r["slug"], symbol=r["symbol"], date=str(r["date"]),
                         split=r["split"], buy_yes=by, won=int(won),
                         pnl=float(settle_pnl(f, won)), avg_price=float(f.avg_price),
                         slippage=slip,
                         unfilled=float(f.unfilled_usd), rvol=float(r["realized_vol"])))
    return pd.DataFrame(rows)


def _ci(t):
    if len(t) < 3:
        return None
    lo, _, hi = window_clustered_bootstrap(t["pnl"].values, t["slug"].values, n=2500)
    return dict(n=int(len(t)), wr=round(float(t["won"].mean() * 100), 1),
                ev=round(float(t["pnl"].mean()), 3), lo=round(float(lo), 3),
                hi=round(float(hi), 3), total=round(float(t["pnl"].sum()), 1))


def _known_edges(b):
    """trade-slug sets for the 3 deployed edges, for Jaccard overlap."""
    out = {}
    det = b[(b["time_left_sec"] >= 1) & (b["time_left_sec"] <= 60)
            & (b["abs_dist_bps"] >= 5) & (b["consistent"])
            & (b["fav_ask"].between(0.50, 0.90))]
    out["det"] = set(det["slug"].unique())
    e4 = b[(b["time_left_sec"] >= 1) & (b["time_left_sec"] <= 60)
           & (b["abs_dist_bps"] >= 5) & (~b["consistent"])]
    out["e4"] = set(e4["slug"].unique())
    fav = b[(b["time_left_sec"] >= 240) & (b["time_left_sec"] <= 420)
            & (b["abs_dist_bps"] >= 8) & (b["consistent"])
            & (b["fav_ask"].between(0.55, 0.80)) & (b["realized_vol"] * 100 <= 1.0)]
    out["fav"] = set(fav["slug"].unique())
    # deployed mid-window disagree edge (fav_disagree: disagree, t120-360, dist>=10).
    # was missing -> earlier overlap numbers understated duplication with the running
    # disagree strategy (skeptic found e4_1074 is a 100% subset of this).
    fdis = b[(b["time_left_sec"] >= 120) & (b["time_left_sec"] <= 360)
             & (b["abs_dist_bps"] >= 10) & (~b["consistent"])]
    out["fav_disagree"] = set(fdis["slug"].unique())
    return out


def _known_edges_extended(b):
    """DEPLOYED-strategy decision sets (exact filters via rejudge_live_model.
    decisions_for) + the OP4 fade region — the honest Jaccard reference for the
    live-cost campaign. Returned on top of the legacy _known_edges sets."""
    import research.analysis.rejudge_live_model as rj
    out = _known_edges(b)
    for name in ("det_lwd_live", "det_d12_wide_v1", "det_d12_dual_live",
                 "fav_disagree", "fav_lowvol", "fav_momentum", "fav_deepdown"):
        try:
            dec = rj.decisions_for(name, b)
            if name == "det_d12_dual_live" and not dec.empty:
                dec = rj._apply_dual_gate(dec, b)
            out[f"dep_{name}"] = set(dec["slug"].unique())
        except Exception as e:                          # pragma: no cover
            print(f"  (known-edge {name} unavailable: {e})")
    try:
        from research.analysis.oracle_print_model import (
            tick_feature_frame, load_model, fade_decisions)
        fd = fade_decisions(tick_feature_frame(), load_model())
        out["fade_region"] = set(fd["slug"].unique())
    except Exception as e:                              # pragma: no cover
        print(f"  (fade region unavailable: {e})")
    return out


def _jaccard(a, bset):
    a = set(a)
    if not a or not bset:
        return 0.0
    return round(len(a & bset) / len(a | bset), 3)


def verify(spec, b, known, *, fill_model="v2", stake=STAKE, live_params=None):
    dec = _decision(spec, b)
    kw = dict(fill_model=fill_model, spec_params=spec["params"],
              live_params=live_params)
    base = _trades(dec, latency=2, stake=stake, seed=0, **kw)
    if len(base) < 10:
        return {**spec, "verified_n": int(len(base)), "verdict": "too_few_fills"}

    per_split = {sp: _ci(base[base["split"] == sp]) for sp in ("dev", "holdout", "future")}
    per_split["FULL"] = _ci(base)

    # capacity curve (full L2 walk at increasing stakes)
    cap = []
    stakes = sorted({int(round(stake)), 10, 25, 50, 100, 250})
    for stk in stakes:
        tt = _trades(dec, latency=2, stake=stk, **kw)
        n0 = len(dec)
        cap.append(dict(stake=stk, fill_rate=round(len(tt) / max(n0, 1), 3),
                        ev=round(float(tt["pnl"].mean()), 3) if len(tt) else None,
                        avg_slip=round(float(tt["slippage"].mean()), 4) if len(tt) else None))

    # cost-stress (v2 only — the live model already embodies latency/rejects;
    # the live analogue is the seed sweep below)
    stress = {}
    if fill_model == "v2":
        for name, skw in (("fee1.5x", dict(fee_mult=1.5)),
                          ("slip1c", dict(slippage_c=0.01)),
                          ("lat5", dict(latency=5)), ("lat10", dict(latency=10)),
                          ("rej30", dict(reject_prob=0.30)),
                          ("ALL", dict(fee_mult=1.5, slippage_c=0.01, latency=5, reject_prob=0.30))):
            stress[name] = _ci(_trades(dec, stake=stake, **skw))

    # per-regime
    med = base["rvol"].median()
    regime = {"quiet": _ci(base[base["rvol"] <= med]),
              "volatile": _ci(base[base["rvol"] > med])}

    # directional balance
    direction = {"buy_up": _ci(base[base["buy_yes"]]),
                 "buy_down": _ci(base[~base["buy_yes"]])}

    # overlap vs known edges
    sl = set(base["slug"].unique())
    jac = {k: _jaccard(sl, v) for k, v in known.items()}

    fut = per_split.get("future")
    out = {**spec, "verified_n": int(len(base)),
           "per_split": per_split, "capacity": cap, "stress": stress,
           "regime": regime, "direction": direction, "jaccard": jac,
           "max_jaccard": max(jac.values()) if jac else 0.0}

    if fill_model == "live":
        # MC robustness: the live model samples latency + zero-fill hazard
        seeds = {}
        for s in LIVE_SEEDS:
            t = _trades(dec, latency=2, stake=stake, seed=s, **kw)
            fu = t[t["split"] == "future"] if len(t) else t
            seeds[str(s)] = dict(
                n=int(len(t)), ev=round(float(t["pnl"].mean()), 3) if len(t) else None,
                fut_n=int(len(fu)),
                fut_ev=round(float(fu["pnl"].mean()), 3) if len(fu) else None)
        fut_evs = [v["fut_ev"] for v in seeds.values() if v["fut_ev"] is not None]
        out.update(
            fill_model="live", stake=stake,
            fill_rate=round(len(base) / max(len(dec), 1), 3),
            seeds=seeds,
            fut_ev_seed_mean=round(float(np.mean(fut_evs)), 3) if fut_evs else None,
            fut_ev_seed_sd=round(float(np.std(fut_evs)), 3) if fut_evs else None)
        # pre-registered live verdict bands (test_ledger Live-cost campaign):
        live_pass = (fut is not None and fut["ev"] > 0 and fut["lo"] > 0
                     and fut["n"] >= 30)
        out["pre_verdict"] = ("deploy_paper_candidate"
                              if live_pass and out["max_jaccard"] < 0.5 else
                              "duplicate_of_known" if live_pass else "reject")
        return out

    allc = stress.get("ALL")
    # deterministic pre-verdict (the skeptic agent makes the final call)
    deploy = (fut and fut["ev"] > 0 and allc and allc["lo"] is not None and allc["lo"] > -0.05
              and per_split["FULL"]["lo"] > 0)
    cap50 = next((c for c in cap if c["stake"] == 50), None)
    capped = cap50["fill_rate"] < 0.8 if cap50 else True  # $50 fill
    out["pre_verdict"] = ("deploy_paper" if deploy and not capped else
                          "paper_only_no_live" if deploy else "reject")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("only", nargs="?", default=None, help="verify one spec id")
    ap.add_argument("--fill-model", choices=("v2", "live"), default="v2")
    ap.add_argument("--stake", type=float, default=STAKE)
    ap.add_argument("--dir", default=None,
                    help="campaign dir holding shortlist.jsonl (default canonical)")
    ap.add_argument("--out", default=None,
                    help="output jsonl (default <dir>/verified.jsonl)")
    ap.add_argument("--future-start", default=None,
                    help="campaign split override (see hypothesis_sweep)")
    ap.add_argument("--extended-known", action="store_true",
                    help="Jaccard vs deployed-strategy decision sets + fade region")
    a = ap.parse_args()

    if a.future_start:
        hyp_sweep.set_future_override(a.future_start)
    b = hyp_sweep.apply_future_override(load_base())
    known = _known_edges_extended(b) if a.extended_known else _known_edges(b)
    live_params = load_live_params() if a.fill_model == "live" else None
    if live_params is not None:
        print(f"live fill model: {live_params.version} "
              f"({live_params.n_attempts} attempts, kappa={live_params.kappa}, "
              f"window {live_params.calibration_window})")

    shortlist_path = (os.path.join(a.dir, "shortlist.jsonl") if a.dir else SHORTLIST)
    out_path = a.out or (os.path.join(a.dir, "verified.jsonl") if a.dir else VERIFIED)
    with open(shortlist_path) as f:
        shortlist = [json.loads(ln) for ln in f if ln.strip()]
    if a.only:
        shortlist = [s for s in shortlist if s["id"] == a.only]
    out = []
    for s in shortlist:
        spec = {"id": s["id"], "family": s["family"],
                "rationale": s.get("rationale"), "params": s["params"]}
        r = verify(spec, b, known, fill_model=a.fill_model, stake=a.stake,
                   live_params=live_params)
        out.append(r)
        ps = r.get("per_split", {})
        fu = ps.get("future") or {}
        al = (r.get("stress") or {}).get("ALL") or {}
        extra = (f"seedsFut ${r.get('fut_ev_seed_mean','-')}±{r.get('fut_ev_seed_sd','-')} "
                 f"fill {r.get('fill_rate','-')}" if a.fill_model == "live"
                 else f"ALLstress ${al.get('ev','-')}[{al.get('lo','-')}]")
        print(f"{s['id']:16} {s['family']:11} n={r.get('verified_n',0):>4} "
              f"future ${fu.get('ev','-')}[{fu.get('lo','-')},{fu.get('hi','-')}] "
              f"{extra} "
              f"maxJac={r.get('max_jaccard','-')} -> {r.get('pre_verdict')}")
    if not a.only:
        with open(out_path, "w") as f:
            for r in out:
                f.write(json.dumps(r) + "\n")
        print(f"\n-> {out_path}")


if __name__ == "__main__":
    main()
