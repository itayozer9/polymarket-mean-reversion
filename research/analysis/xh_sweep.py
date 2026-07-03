"""fam_xh — bounded, pre-registered cross-book 5m↔15m no-arb sweep (T1b, Edge Hunt v2).

Grid (registered in test_ledger.md "HONEST EDGE HUNT v2"): leg ∈ {15y,5n,15n,5y} ×
premium m ∈ {0,.01,.02,.04} × gap g ∈ {2,5,10,20}bps × time band × ref-notional floor ×
ask ceiling = 1,280 specs. One trade per 15m parent window (first qualifying second),
taker, hold to resolution, $5 stake, live-2 guarded fills, OFFICIAL labels on the traded
instrument's own slug (5m legs settle on the 5m official label).

Discipline: `discover` runs on entries <= DISC_END (2026-06-18) ONLY — virgin rows are
masked before any stat. It writes a shortlist by the pre-declared rule (disc n>=20 AND
disc CI-lo>0). `reveal` scores ONLY the frozen shortlist on the virgin block (>= 2026-06-19),
ONCE, applying the registered gates: virgin n>=30, CI-lo>0, BH-FDR 10% across the revealed
set, 5-seed fill robustness, Jaccard<0.5 vs the running xb twin.

Run:  uv run python -m research.analysis.xh_sweep discover
      uv run python -m research.analysis.xh_sweep reveal
Out:  data/research/hypotheses/xh_sweep/{discovery.parquet,shortlist.json,verdicts.md}
"""
from __future__ import annotations
import argparse
import itertools
import json
import os

import numpy as np
import pandas as pd

from research.dataset.official_outcomes import official_only_by_slug
from research.sim.fills_live import load_params, simulate_taker_entry
from research.lib.stats import window_clustered_bootstrap

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
XBOOK = os.path.join(REPO, "data", "research", "xbook_15m.parquet")
OUT_DIR = os.path.join(REPO, "data", "research", "hypotheses", "xh_sweep")
XB_TWIN_PARQ = os.path.join(REPO, "data", "research", "paper_official",
                            "xb_5m15m_causal_v1.parquet")

DISC_END = "2026-06-18"          # inclusive; virgin = date >= 2026-06-19 (registered)
STAKE = 5.0
SEEDS = (0, 1, 2, 3, 4)

# leg -> (margin col, ref col, gap sign, instrument, ask cols, side bought)
LEGS = {
    "15y": ("m_15y", "ref_15y_usd", +1, "15m", "yes_best_ask", "yes_ask_depth", "UP"),
    "5n": ("m_5n", "ref_5n_usd", +1, "5m", "no5_ask", "no5_ask_sh", "DOWN"),
    "15n": ("m_15n", "ref_15n_usd", -1, "15m", "no_best_ask", "no_ask_depth", "DOWN"),
    "5y": ("m_5y", "ref_5y_usd", -1, "5m", "yes5_ask", "yes5_ask_sh", "UP"),
}
GRID = dict(
    m=(0.0, 0.01, 0.02, 0.04),
    g=(2.0, 5.0, 10.0, 20.0),
    band=((600, 690), (690, 780), (780, 870), (870, 900), (600, 900)),
    ref=(1.0, 10.0),
    ceil=(0.90, 0.97),
)


def specs():
    for leg, m, g, band, ref, ceil in itertools.product(
            LEGS, GRID["m"], GRID["g"], GRID["band"], GRID["ref"], GRID["ceil"]):
        yield dict(spec=f"xh_{leg}_m{int(m*100):02d}_g{int(g):02d}_"
                        f"b{band[0]}-{band[1]}_r{int(ref)}_c{int(ceil*100)}",
                   leg=leg, m=m, g=g, band=band, ref=ref, ceil=ceil)


def _entries(x: pd.DataFrame, sp: dict) -> pd.DataFrame:
    mcol, refcol, sign, instr, askcol, depcol, side = LEGS[sp["leg"]]
    lo, hi = sp["band"]
    ok = (x["k5_causal"]
          & (x["seconds_into_window"] >= lo) & (x["seconds_into_window"] < hi)
          & (sign * x["gap_bps"] >= sp["g"])
          & (x[mcol] >= sp["m"])
          & (x[refcol] >= sp["ref"])
          & (x[askcol] > 0.03) & (x[askcol] <= sp["ceil"]))
    e = x[ok]
    if e.empty:
        return e
    e = e.sort_values("seconds_into_window").groupby("slug", as_index=False).first()
    e = e.assign(instr_ask=e[askcol], instr_depth=e[depcol], side=side,
                 settle_slug=np.where(instr == "5m", e["slug5"], e["slug"]),
                 time_left=np.where(instr == "5m", 300 - (e["seconds_into_window"] - 600),
                                    900 - e["seconds_into_window"]))
    return e


def _simulate(e: pd.DataFrame, sp: dict, off: dict, params, seed: int) -> pd.DataFrame:
    import zlib
    rng = np.random.default_rng(seed * 100003 + zlib.crc32(sp["spec"].encode()))
    rows = []
    for r in e.itertuples():
        lab = off.get(r.settle_slug)
        if lab is None:
            continue
        f = simulate_taker_entry([r.instr_ask], [r.instr_depth], STAKE,
                                 entry_ask=r.instr_ask, max_ask=sp["ceil"],
                                 time_left=float(r.time_left), rng=rng,
                                 params=params, mode="guarded")
        if not f.filled or f.shares <= 0:
            continue
        won = (lab == 1) == (r.side == "UP")
        pnl = (f.shares * (1 - f.avg_price) - f.fee_usd if won
               else -f.notional_usd - f.fee_usd)
        rows.append((r.slug, r.settle_slug, str(r.date), pnl, won))
    return pd.DataFrame(rows, columns=["slug", "settle_slug", "date", "pnl", "won"])


def _boot_p(pnl: np.ndarray, groups: np.ndarray, n: int = 2000, seed: int = 0) -> float:
    """One-sided window-clustered bootstrap p-value for mean <= 0."""
    uniq = np.unique(groups)
    idx = {g: np.where(groups == g)[0] for g in uniq}
    rng = np.random.default_rng(seed)
    hits = 0
    for _ in range(n):
        pick = rng.choice(uniq, size=len(uniq), replace=True)
        if pnl[np.concatenate([idx[g] for g in pick])].mean() <= 0:
            hits += 1
    return (hits + 1) / (n + 1)


def discover() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    x = pd.read_parquet(XBOOK)
    x = x[x["date"] <= DISC_END]                       # virgin rows never touched here
    # DEGRADED stale-book epoch (test_ledger): a starved WS wrote fresh-timestamped rows
    # with FROZEN books — the age<=3s guard cannot catch it, and a stale 5m book fakes
    # exactly the violations this family trades. Registered: excluded from every verdict.
    x = x[(x["date"] < "2026-06-05") | (x["date"] >= "2026-06-13")]
    off = dict(zip(*official_only_by_slug().to_numpy().T))
    off = {k: int(v) for k, v in off.items()}
    params = load_params()
    print(f"[xh:discover] {len(x):,} joined seconds <= {DISC_END}, "
          f"fill model {params.version}")
    res = []
    for sp in specs():
        e = _entries(x, sp)
        if len(e) < 20:
            res.append(dict(sp, n=len(e), keep=False)); continue
        t = _simulate(e, sp, off, params, seed=SEEDS[0])
        if len(t) < 20:
            res.append(dict(sp, n=len(t), keep=False)); continue
        lo, mid, hi = window_clustered_bootstrap(t["pnl"].values, t["slug"].values, n=2000)
        res.append(dict(sp, n=len(t), ev=float(t["pnl"].mean()), ci_lo=lo, ci_hi=hi,
                        wr=float(t["won"].mean()), keep=bool(lo > 0)))
    df = pd.DataFrame(res)
    df["band"] = df["band"].astype(str)
    df.to_parquet(os.path.join(OUT_DIR, "discovery.parquet"), index=False)
    short = df[df["keep"] == True]          # noqa: E712
    short.to_json(os.path.join(OUT_DIR, "shortlist.json"), orient="records", indent=1)
    print(f"[xh:discover] {len(df)} specs, {len(short)} shortlisted "
          f"(disc n>=20 & CI-lo>0) -> shortlist.json")
    if len(short):
        print(short.sort_values("ci_lo", ascending=False)
              .head(15)[["spec", "n", "ev", "ci_lo", "ci_hi", "wr"]].to_string(index=False))


def reveal() -> None:
    short_path = os.path.join(OUT_DIR, "shortlist.json")
    if not os.path.exists(short_path):
        raise SystemExit("no shortlist — run discover first")
    short = json.load(open(short_path))
    if not short:
        print("[xh:reveal] shortlist empty — nothing to reveal; door closes on T1a verdict")
        return
    x = pd.read_parquet(XBOOK)
    x = x[x["date"] >= "2026-06-19"]
    off = {k: int(v) for k, v in
           dict(zip(*official_only_by_slug().to_numpy().T)).items()}
    params = load_params()
    xb_slugs = set()
    if os.path.exists(XB_TWIN_PARQ):
        tw = pd.read_parquet(XB_TWIN_PARQ)
        xb_slugs = set(tw[tw["era"] == "virgin"]["slug"])
    print(f"[xh:reveal] ONE virgin reveal for {len(short)} specs, "
          f"fill model {params.version}")
    out = []
    for sp in short:
        sp["band"] = tuple(int(v) for v in
                           sp["band"].strip("()").replace(" ", "").split(","))
        e = _entries(x, sp)
        per_seed = []
        t0 = None
        for s in SEEDS:
            t = _simulate(e, sp, off, params, seed=s)
            if s == SEEDS[0]:
                t0 = t
            per_seed.append(t["pnl"].mean() if len(t) else np.nan)
        if t0 is None or len(t0) == 0:
            out.append(dict(spec=sp["spec"], n=0, verdict="NO-TRADES")); continue
        lo, mid, hi = window_clustered_bootstrap(t0["pnl"].values, t0["slug"].values, n=2000)
        p = _boot_p(t0["pnl"].values, t0["slug"].values)
        seeds = np.array(per_seed, dtype=float)
        seed_ok = bool(np.nanmean(seeds) - 2 * np.nanstd(seeds) > 0)
        jac = (len(set(t0["slug"]) & xb_slugs) / len(set(t0["slug"]) | xb_slugs)
               if (len(t0) and xb_slugs) else 0.0)
        out.append(dict(spec=sp["spec"], n=int(len(t0)), ev=float(t0["pnl"].mean()),
                        ci_lo=lo, ci_hi=hi, p=p, seed_ok=seed_ok, jaccard=round(jac, 3),
                        wr=float(t0["won"].mean())))
    d = pd.DataFrame(out)
    # BH-FDR 10% across the revealed set (registered)
    scored = d[d["p"].notna()].sort_values("p").reset_index(drop=True)
    m = len(scored)
    bh_ok = set()
    for i, r in scored.iterrows():
        if r["p"] <= (i + 1) / m * 0.10:
            bh_ok.add(r["spec"])
    thr = max(((i + 1) / m * 0.10) for i, r in scored.iterrows()
              if r["spec"] in bh_ok) if bh_ok else 0.0
    bh_ok = set(scored[scored["p"] <= thr]["spec"]) if bh_ok else set()
    d["survives"] = (d["spec"].isin(bh_ok) & (d["n"] >= 30) & (d["ci_lo"] > 0)
                     & d["seed_ok"] & (d["jaccard"] < 0.5))
    d.to_parquet(os.path.join(OUT_DIR, "virgin_verdicts.parquet"), index=False)
    with open(os.path.join(OUT_DIR, "verdicts.md"), "w") as f:
        f.write("# fam_xh virgin reveal (ONE look)\n\n```\n"
                + d.sort_values(["survives", "ci_lo"], ascending=False).to_string(index=False)
                + "\n```\n")
    print(d.sort_values(["survives", "ci_lo"], ascending=False).head(20).to_string(index=False))
    print(f"[xh:reveal] survivors: {int(d['survives'].sum())}/{len(d)} -> verdicts.md")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["discover", "reveal"])
    a = ap.parse_args()
    discover() if a.cmd == "discover" else reveal()
