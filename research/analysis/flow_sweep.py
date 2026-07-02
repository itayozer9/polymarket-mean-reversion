"""fam_flow2 — bounded, pre-registered uninformed-flow fade sweep (T4, Edge Hunt v2).

Theory: retail burst aggression moves a ~$10-deep book away from fair; fading an
identifiable one-sided burst harvests the reversion. Prior order-flow deaths were all
BOOK-derived signals; this is the raw-print modality's one shot (registered in
test_ledger.md "HONEST EDGE HUNT v2" T4).

Grid: action {fade,follow(control)} × burst $ {50,150,400} × recency {<=2,<=10,<=30}s ×
time-left band {(30,60],(60,300],(300,600],(600,900]} × entry-ask band
{0.05-0.35,0.35-0.65,0.65-0.90} × big-print gate {any, pr_max_30s>=100} = 432 specs.
One trade per window (first qualifying second), $5 taker, live-2 guarded fills,
hold-to-resolution, OFFICIAL labels. Burst direction = sign(pr_signed_2s), requiring
|signed|>=0.6*gross (one-sided). All print features are 2s-embargoed (trade_prints.py).

Discipline identical to xh_sweep: `discover` masks entries > 2026-06-18; `reveal` scores
the frozen shortlist ONCE on the virgin block with BH-FDR 10% + 5-seed + n>=30 + CI-lo>0.

Run:  uv run python -m research.analysis.flow_sweep discover|reveal
Out:  data/research/hypotheses/flow_sweep/
"""
from __future__ import annotations
import argparse
import itertools
import json
import os
import zlib

import numpy as np
import pandas as pd

from research.dataset.official_outcomes import official_only_by_slug
from research.sim.fills_live import load_params, simulate_taker_entry
from research.lib.stats import window_clustered_bootstrap
from research.analysis.xh_sweep import _boot_p

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SLIM = os.path.join(REPO, "data", "research", "joined_15m_slim.parquet")
PRINTS = os.path.join(REPO, "data", "research", "prints_15m.parquet")
OUT_DIR = os.path.join(REPO, "data", "research", "hypotheses", "flow_sweep")

DISC_END = "2026-06-18"
STAKE = 5.0
SEEDS = (0, 1, 2, 3, 4)

GRID = dict(
    action=("fade", "follow"),
    burst=(50.0, 150.0, 400.0),
    recency=(2.0, 10.0, 30.0),
    tl=((30, 60), (60, 300), (300, 600), (600, 900)),
    ask=((0.05, 0.35), (0.35, 0.65), (0.65, 0.90)),
    big=(0, 1),
)
_COLS = ["slug", "symbol", "date", "seconds_into_window", "time_left_sec",
         "yes_best_ask", "no_best_ask", "yes_ask_depth", "no_ask_depth", "book_healthy"]


def specs():
    for a, b, r, tl, ask, big in itertools.product(*GRID.values()):
        yield dict(spec=f"fl_{a}_b{int(b)}_r{int(r)}_tl{tl[0]}-{tl[1]}_"
                        f"a{int(ask[0]*100)}-{int(ask[1]*100)}_big{big}",
                   action=a, burst=b, recency=r, tl=tl, ask=ask, big=big)


def load_joined_prints(virgin: bool) -> pd.DataFrame:
    b = pd.read_parquet(SLIM, columns=_COLS)
    b = b[b["date"] >= "2026-06-19"] if virgin else b[b["date"] <= DISC_END]
    b = b[b["book_healthy"].fillna(False).astype(bool)]
    p = pd.read_parquet(PRINTS)
    j = b.merge(p, on=["slug", "seconds_into_window"], how="inner")
    one_sided = j["pr_signed_2s"].abs() >= 0.6 * j["pr_usd_2s"].clip(lower=1e-9)
    j = j[(j["pr_usd_2s"] > 0) & one_sided]
    return j


def _entries(j: pd.DataFrame, sp: dict) -> pd.DataFrame:
    burst_up = j["pr_signed_2s"] > 0
    buy_up = ~burst_up if sp["action"] == "fade" else burst_up
    ask = np.where(buy_up, j["yes_best_ask"], j["no_best_ask"])
    dep = np.where(buy_up, j["yes_ask_depth"], j["no_ask_depth"])
    ok = ((j["pr_usd_2s"] >= sp["burst"])
          & (j["pr_since_burst_s"] <= sp["recency"])
          & (j["time_left_sec"] > sp["tl"][0]) & (j["time_left_sec"] <= sp["tl"][1])
          & (ask >= sp["ask"][0]) & (ask <= sp["ask"][1]) & (dep > 0))
    if sp["big"]:
        ok &= j["pr_max_30s"] >= 100.0
    e = j[ok].copy()
    if e.empty:
        return e
    e["instr_ask"], e["instr_depth"] = ask[ok], dep[ok]
    e["side"] = np.where(buy_up[ok], "UP", "DOWN")
    e = e.sort_values("seconds_into_window").groupby("slug", as_index=False).first()
    return e


def _simulate(e: pd.DataFrame, sp: dict, off: dict, params, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed * 100003 + zlib.crc32(sp["spec"].encode()))
    rows = []
    for r in e.itertuples():
        lab = off.get(r.slug)
        if lab is None:
            continue
        f = simulate_taker_entry([r.instr_ask], [r.instr_depth], STAKE,
                                 entry_ask=r.instr_ask, max_ask=sp["ask"][1],
                                 time_left=float(r.time_left_sec), rng=rng,
                                 params=params, mode="guarded")
        if not f.filled or f.shares <= 0:
            continue
        won = (lab == 1) == (r.side == "UP")
        pnl = (f.shares * (1 - f.avg_price) - f.fee_usd if won
               else -f.notional_usd - f.fee_usd)
        rows.append((r.slug, str(r.date), pnl, won))
    return pd.DataFrame(rows, columns=["slug", "date", "pnl", "won"])


def discover() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    j = load_joined_prints(virgin=False)
    off = {k: int(v) for k, v in
           dict(zip(*official_only_by_slug().to_numpy().T)).items()}
    params = load_params()
    print(f"[flow:discover] {len(j):,} burst-seconds <= {DISC_END}, "
          f"fill model {params.version}")
    res = []
    for sp in specs():
        e = _entries(j, sp)
        if len(e) < 20:
            res.append(dict(sp, n=len(e), keep=False)); continue
        t = _simulate(e, sp, off, params, seed=SEEDS[0])
        if len(t) < 20:
            res.append(dict(sp, n=len(t), keep=False)); continue
        lo, mid, hi = window_clustered_bootstrap(t["pnl"].values, t["slug"].values, n=2000)
        res.append(dict(sp, n=len(t), ev=float(t["pnl"].mean()), ci_lo=lo, ci_hi=hi,
                        wr=float(t["won"].mean()), keep=bool(lo > 0)))
    df = pd.DataFrame(res)
    for c in ("tl", "ask"):
        df[c] = df[c].astype(str)
    df.to_parquet(os.path.join(OUT_DIR, "discovery.parquet"), index=False)
    short = df[df["keep"] == True]          # noqa: E712
    short.to_json(os.path.join(OUT_DIR, "shortlist.json"), orient="records", indent=1)
    print(f"[flow:discover] {len(df)} specs, {len(short)} shortlisted -> shortlist.json")
    if len(short):
        print(short.sort_values("ci_lo", ascending=False)
              .head(15)[["spec", "n", "ev", "ci_lo", "ci_hi", "wr"]].to_string(index=False))


def reveal() -> None:
    short_path = os.path.join(OUT_DIR, "shortlist.json")
    if not os.path.exists(short_path):
        raise SystemExit("no shortlist — run discover first")
    short = json.load(open(short_path))
    if not short:
        print("[flow:reveal] shortlist empty — flow door CLOSES (registered)")
        return
    j = load_joined_prints(virgin=True)
    off = {k: int(v) for k, v in
           dict(zip(*official_only_by_slug().to_numpy().T)).items()}
    params = load_params()
    # registered overlap gate: a flow spec must trade DIFFERENT windows than the
    # deployed det/disagree families, else it just re-labels them (the known trap)
    det_slugs = set()
    for sid in ("det_lwd_v1", "fav_disagree", "det_d12_wide_v1"):
        p = os.path.join(REPO, "data", "research", "paper_official", f"{sid}.parquet")
        if os.path.exists(p):
            tw = pd.read_parquet(p, columns=["slug", "era"])
            det_slugs |= set(tw[tw["era"] == "virgin"]["slug"])
    print(f"[flow:reveal] ONE virgin reveal for {len(short)} specs "
          f"(overlap ref: {len(det_slugs)} det-family virgin slugs)")
    out = []
    for sp in short:
        sp["tl"] = tuple(int(v) for v in sp["tl"].strip("()").replace(" ", "").split(","))
        sp["ask"] = tuple(float(v) for v in sp["ask"].strip("()").replace(" ", "").split(","))
        e = _entries(j, sp)
        per_seed, t0 = [], None
        for s in SEEDS:
            t = _simulate(e, sp, off, params, seed=s)
            if s == SEEDS[0]:
                t0 = t
            per_seed.append(t["pnl"].mean() if len(t) else np.nan)
        if t0 is None or len(t0) == 0:
            out.append(dict(spec=sp["spec"], n=0)); continue
        lo, mid, hi = window_clustered_bootstrap(t0["pnl"].values, t0["slug"].values, n=2000)
        p = _boot_p(t0["pnl"].values, t0["slug"].values)
        seeds = np.array(per_seed, dtype=float)
        mine = set(t0["slug"])
        jac = (len(mine & det_slugs) / len(mine | det_slugs)) if det_slugs else 0.0
        out.append(dict(spec=sp["spec"], n=int(len(t0)), ev=float(t0["pnl"].mean()),
                        ci_lo=lo, ci_hi=hi, p=p, jaccard=round(jac, 3),
                        seed_ok=bool(np.nanmean(seeds) - 2 * np.nanstd(seeds) > 0),
                        wr=float(t0["won"].mean())))
    d = pd.DataFrame(out)
    scored = d[d["p"].notna()].sort_values("p").reset_index(drop=True)
    m = max(len(scored), 1)
    passing = scored[scored["p"] <= (scored.index + 1) / m * 0.10]
    thr = passing["p"].max() if len(passing) else 0.0
    d["survives"] = ((d["p"] <= thr) & (d["n"] >= 30) & (d["ci_lo"] > 0) & d["seed_ok"]
                     & (d["jaccard"] < 0.5))
    d.to_parquet(os.path.join(OUT_DIR, "virgin_verdicts.parquet"), index=False)
    with open(os.path.join(OUT_DIR, "verdicts.md"), "w") as f:
        f.write("# fam_flow2 virgin reveal (ONE look)\n\n"
                + d.sort_values(["survives", "ci_lo"], ascending=False).to_markdown(index=False))
    print(d.sort_values(["survives", "ci_lo"], ascending=False).head(20).to_string(index=False))
    print(f"[flow:reveal] survivors: {int(d['survives'].sum())}/{len(d)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["discover", "reveal"])
    a = ap.parse_args()
    discover() if a.cmd == "discover" else reveal()
