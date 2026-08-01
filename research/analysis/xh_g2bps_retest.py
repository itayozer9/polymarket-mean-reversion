"""V3a — g2bps-5y RETEST (Edge Hunt v3, pre-registered 2026-07-24, test_ledger.md).

Cross-horizon door revival per the T1 rule: >=3wk new forward data + fresh registration.
Scores the FROZEN 3-spec list (the virgin CI-lo>0 specs from the v2 reveal) on the new
window 2026-07-03..2026-07-23 — data no sweep has ever touched. Identical mechanics to
xh_sweep.reveal (official 5m labels, $5 taker, 5-seed guarded fills, slug-clustered CI);
BH-FDR 10% within k=3. PASS gates: n>=30, CI-lo>0, BH, seed_ok, Jaccard<0.5 vs xb twin.

Run:  uv run python -m research.analysis.xh_g2bps_retest
Out:  data/research/hypotheses/xh_g2bps_v3/verdicts.{parquet,md}
"""
from __future__ import annotations
import json
import os

import numpy as np
import pandas as pd

import research.analysis.xh_sweep as xh
from research.dataset.official_outcomes import official_only_by_slug
from research.sim.fills_live import load_params
from research.lib.stats import window_clustered_bootstrap

OUT_DIR = os.path.join(xh.REPO, "data", "research", "hypotheses", "xh_g2bps_v3")
D_START, D_END = "2026-07-03", "2026-07-23"

# Frozen BEFORE scoring (registered): the 3 v2-virgin CI-lo>0 specs, params from the name.
SPECS = [
    dict(spec="xh_5y_m02_g02_b600-900_r1_c90", leg="5y", m=0.02, g=2.0,
         band=(600, 900), ref=1.0, ceil=0.90),
    dict(spec="xh_5y_m02_g02_b600-900_r10_c97", leg="5y", m=0.02, g=2.0,
         band=(600, 900), ref=10.0, ceil=0.97),
    dict(spec="xh_5y_m01_g02_b600-900_r1_c97", leg="5y", m=0.01, g=2.0,
         band=(600, 900), ref=1.0, ceil=0.97),
]


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    x = pd.read_parquet(xh.XBOOK)
    x = x[(x["date"] >= D_START) & (x["date"] <= D_END)]
    off = {k: int(v) for k, v in
           dict(zip(*official_only_by_slug().to_numpy().T)).items()}
    params = load_params()
    xb_slugs = set()
    if os.path.exists(xh.XB_TWIN_PARQ):
        tw = pd.read_parquet(xh.XB_TWIN_PARQ)
        d = pd.to_datetime(tw["entry_ts_ms"], unit="ms").dt.strftime("%Y-%m-%d")
        xb_slugs = set(tw[(d >= D_START) & (d <= D_END)]["slug"])
    print(f"[g2bps-v3] {len(x):,} joined seconds {D_START}..{D_END}, "
          f"fill model {params.version}, k={len(SPECS)}")
    out = []
    for sp in SPECS:
        e = xh._entries(x, sp)
        per_seed, t0 = [], None
        for s in xh.SEEDS:
            t = xh._simulate(e, sp, off, params, seed=s)
            if s == xh.SEEDS[0]:
                t0 = t
            per_seed.append(t["pnl"].mean() if len(t) else np.nan)
        if t0 is None or len(t0) == 0:
            out.append(dict(spec=sp["spec"], n=0, verdict="NO-TRADES"))
            continue
        lo, mid, hi = window_clustered_bootstrap(t0["pnl"].values, t0["slug"].values, n=2000)
        p = xh._boot_p(t0["pnl"].values, t0["slug"].values)
        seeds = np.array(per_seed, dtype=float)
        seed_ok = bool(np.nanmean(seeds) - 2 * np.nanstd(seeds) > 0)
        jac = (len(set(t0["slug"]) & xb_slugs) / len(set(t0["slug"]) | xb_slugs)
               if (len(t0) and xb_slugs) else 0.0)
        out.append(dict(spec=sp["spec"], n=int(len(t0)), ev=float(t0["pnl"].mean()),
                        ci_lo=lo, ci_hi=hi, p=p, seed_ok=seed_ok, jaccard=round(jac, 3),
                        wr=float(t0["won"].mean())))
    d = pd.DataFrame(out)
    scored = d[d["p"].notna()].sort_values("p").reset_index(drop=True)
    m = len(scored)
    bh_ok = set(scored.loc[[i for i, r in scored.iterrows()
                            if r["p"] <= (i + 1) / m * 0.10], "spec"]) if m else set()
    d["survives"] = (d["spec"].isin(bh_ok) & (d.get("n", 0) >= 30) & (d["ci_lo"] > 0)
                     & d["seed_ok"] & (d["jaccard"] < 0.5)) if m else False
    d.to_parquet(os.path.join(OUT_DIR, "verdicts.parquet"), index=False)
    with open(os.path.join(OUT_DIR, "verdicts.md"), "w") as f:
        f.write(f"# g2bps-5y retest — ONE look at {D_START}..{D_END}\n\n```\n"
                + d.to_string(index=False) + "\n```\n")
    print(d.to_string(index=False))
    print(f"[g2bps-v3] survivors: {int(d['survives'].sum())}/{len(d)}")


if __name__ == "__main__":
    main()
