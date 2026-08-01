"""hypothesis_select — Phase 3 mechanical selection.

Merge the sharded sweep results and shortlist robust candidates WITHOUT looking
at the held-out `future` block (06-01..06-04). Selection uses only:
  - dev split EV (+ n)
  - FULL window-clustered CI lower bound > 0
  - CPCV positive-fraction (purged/embargoed CV — a robustness estimator, not a
    single peek at one block)
  - latency survival at 5s and 10s on the FULL ledger (the user's hard "no speed"
    gate: a home trader is never the fastest)
  - top-of-book capacity at $10 (must fill at paper size)

The `future` block EV is recorded by the sweep but is DELIBERATELY ignored here;
it is revealed once per candidate in Phase 4 (adversarial verification). DSR at
n_trials=2000 is reported as a robustness rank, NOT a binary gate (13 daily obs
cannot clear DSR>=0.95 against 2000 trials — that is the honest statistical
reality; the real arbiter is the held-out future block + the 1-week forward run).

Run:  uv run python -m research.analysis.hypothesis_select
"""
from __future__ import annotations
import glob
import json
import os

import numpy as np
import pandas as pd

OUT_DIR = os.path.join("data", "research", "hypotheses")
SHORTLIST = os.path.join(OUT_DIR, "shortlist.jsonl")

# selection gates (no future block)
MIN_N = 40
MIN_DEV_N = 12
MIN_CPCV = 80.0
MAX_PER_FAMILY = 5
SHORTLIST_SIZE = 24


def load_results() -> pd.DataFrame:
    rows = []
    for p in sorted(glob.glob(os.path.join(OUT_DIR, "results_*.jsonl"))):
        with open(p) as f:
            rows += [json.loads(ln) for ln in f if ln.strip()]
    if not rows:
        # single-file fallback
        p = os.path.join(OUT_DIR, "results.jsonl")
        if os.path.exists(p):
            with open(p) as f:
                rows = [json.loads(ln) for ln in f if ln.strip()]
    return pd.DataFrame(rows)


def _g(d, *keys, default=None):
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k)
    return d if d is not None else default


def passes(r: dict) -> tuple[bool, dict]:
    """selection gates, future-blind. returns (ok, derived metrics)."""
    if r.get("screened") is not True:
        return False, {}
    n = r.get("n", 0)
    dev_n = _g(r, "dev_split", "n", default=0)
    dev_ev = _g(r, "dev_split", "ev", default=-9)
    full_lo = r.get("full_ci_lo")
    cpcv = r.get("cpcv_pct_pos")
    lat = r.get("lat")
    if not isinstance(lat, dict):
        lat = {}
    lat5 = (lat.get("5") or {}).get("ev")
    lat10 = (lat.get("10") or {}).get("ev")
    lat2 = (lat.get("2") or {}).get("ev")
    cap10 = _g(r, "caps", "cap_10", default=0)
    m = dict(n=n, dev_n=dev_n, dev_ev=dev_ev, full_lo=full_lo, cpcv=cpcv,
             lat2=lat2, lat5=lat5, lat10=lat10, cap10=cap10,
             cap25=_g(r, "caps", "cap_25", default=0),
             cap50=_g(r, "caps", "cap_50", default=0))
    ok = (n >= MIN_N and dev_n >= MIN_DEV_N and dev_ev > 0
          and full_lo is not None and full_lo > 0
          and cpcv is not None and cpcv >= MIN_CPCV
          and lat5 is not None and lat5 > 0
          and lat10 is not None and lat10 > 0
          and cap10 >= 0.90)
    return ok, m


def score(m: dict) -> float:
    """robustness composite (future-blind): reward positive dev EV, a FULL CI that
    clears zero, high CPCV, and latency PERSISTENCE (edge that holds as you slow
    down). Penalise edges whose EV collapses from 2s->10s."""
    persistence = (m["lat10"] / m["lat2"]) if m.get("lat2") else 0
    persistence = max(min(persistence, 1.5), 0)
    return (1.0 * np.tanh(m["dev_ev"])
            + 1.0 * np.tanh(m["full_lo"])
            + 0.01 * (m["cpcv"] - 80)
            + 0.8 * persistence
            + 0.5 * np.tanh(m.get("lat10", 0)))


def main():
    df = load_results()
    n_total = len(df)
    n_screened = int(df.get("screened", pd.Series(dtype=bool)).fillna(False).sum())
    cands = []
    for _, r in df.iterrows():
        ok, m = passes(r.to_dict())
        if ok:
            cands.append({**r.to_dict(), **{f"m_{k}": v for k, v in m.items()},
                          "sel_score": float(score(m))})
    cands.sort(key=lambda c: -c["sel_score"])

    # diversity: cap per family, keep top by score
    per_fam, shortlist = {}, []
    for c in cands:
        fam = c["family"]
        if per_fam.get(fam, 0) >= MAX_PER_FAMILY:
            continue
        per_fam[fam] = per_fam.get(fam, 0) + 1
        shortlist.append(c)
        if len(shortlist) >= SHORTLIST_SIZE:
            break

    with open(SHORTLIST, "w") as f:
        for c in shortlist:
            f.write(json.dumps(c) + "\n")

    print(f"results: {n_total} total, {n_screened} screened-in")
    print(f"passed selection gates: {len(cands)}")
    print(f"shortlist (diversified, <= {MAX_PER_FAMILY}/family): {len(shortlist)}\n")
    print(f"{'id':16} {'fam':11} {'n':>4} {'dev_ev':>7} {'full_lo':>7} "
          f"{'cpcv':>5} {'l2':>6} {'l5':>6} {'l10':>6} {'fut?':>6} {'dsr':>5} {'score':>6}")
    for c in shortlist:
        m = {k[2:]: v for k, v in c.items() if k.startswith("m_")}
        fut = _g(c, "future_split", "ev")  # shown for context ONLY, not used to rank
        print(f"{c['id']:16} {c['family']:11} {m['n']:>4} {m['dev_ev']:>7.2f} "
              f"{m['full_lo']:>7.2f} {m['cpcv']:>5.0f} {m['lat2']:>6.2f} "
              f"{m['lat5']:>6.2f} {m['lat10']:>6.2f} "
              f"{(fut if fut is not None else float('nan')):>6.2f} "
              f"{c.get('dsr', float('nan')):>5.2f} {c['sel_score']:>6.2f}")
    print(f"\n-> {SHORTLIST}")
    # family coverage of full screened set (context)
    print("\nscreened-in by family:")
    sc = df[df.get("screened", False) == True]
    for fam, n in sc["family"].value_counts().items():
        print(f"  {fam:12} {n}")


if __name__ == "__main__":
    main()
