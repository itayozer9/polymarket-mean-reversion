"""De-staled EV re-score (Clean-Data Reckoning, plan in-this-repo-we-silly-thimble).

Re-scores every deployed CONFIG on CLEAN data only, bracketing the fill model
(v2 idealized = optimistic bound, live_guarded with the stale live-1 params =
pessimistic bound — the honest binned model can't be recalibrated yet: only 59
clean attempts). The clean fill model is DEFERRED; this brackets the truth.

Periods (frozen in test_ledger "CLEAN-DATA RECKONING"):
  devhold      = split in {dev, holdout} (2026-05-23..05-31, pre-degradation reference)
  clean_future = entry UTC ts (window_start_ts + entry_sec) >= 2026-06-12 11:00 UTC
  (the degraded 2026-06-05..06-12 10:55 middle is neither — excluded from both)

Verdict rule (pre-registered): KEEP-live if clean_future EV/fill CI-lower > 0 under
live_guarded AND >= 2 fills/active-day; DEMOTE if CI-lower <= 0 OR clean_future fill
frequency < 0.5x the devhold rate; INSUFFICIENT-DATA (hold, keep running) if
clean_future fills < 20.

Run: uv run python -m research.analysis.rejudge_clean [--stake 5] [--seed 0]
"""
from __future__ import annotations
import argparse
import datetime as dt
import json

import numpy as np
import pandas as pd

from research.analysis.rejudge_live_model import (
    CONFIGS, decisions_for, simulate_config, _apply_dual_gate, _ci)
from research.analysis.edge_lab import load_base
from research.sim.fills_live import DEFAULT_PARAMS_PATH, load_params

CLEAN_CUT = int(dt.datetime(2026, 6, 12, 11, 0, tzinfo=dt.timezone.utc).timestamp())
ACTIVE_FRAC = 7.0 / 24.0  # det opps concentrate in ~7 active UTC hours/day (clean-feed re-score)


def _entry_ts(dec: pd.DataFrame) -> pd.Series:
    ws = dec["slug"].str.rsplit("-", n=1).str[1].astype("int64")
    return ws + dec["entry_sec"].astype("int64")


def _span_days(dec: pd.DataFrame) -> float:
    if dec.empty:
        return 0.0
    ts = _entry_ts(dec)
    return max((ts.max() - ts.min()) / 86400.0, 1e-9)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stake", type=float, default=5.0)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--only", default=None)
    args = ap.parse_args()

    params = load_params(DEFAULT_PARAMS_PATH)
    b = load_base()
    names = [args.only] if args.only else list(CONFIGS)
    print(f"DE-STALED RE-SCORE @ ${args.stake:.0f}/trade  (fill bracket: v2 optimistic | "
          f"live_guarded pessimistic [{params.version}, {params.n_attempts} STALE attempts])")
    print(f"clean_future = entry UTC >= 2026-06-12 11:00; devhold = dev+holdout (05-23..05-31)\n")
    hdr = (f"{'config':18s} {'period':12s} {'model':12s} {'dec':>4s} {'fill%':>6s} "
           f"{'fills':>5s} {'EV/fill':>8s} {'CI95':>17s} {'WR%':>5s} {'tot$':>7s} {'fills/actday':>12s}")
    out = []
    for name in names:
        cfg = CONFIGS[name]
        dec = decisions_for(name, b)
        if dec.empty:
            print(f"{name}: no decisions"); continue
        if cfg.get("oracle_gate") == "agree":
            dec = _apply_dual_gate(dec, b)
        ts = _entry_ts(dec)
        periods = {
            "devhold": dec[dec["split"].isin(["dev", "holdout"])],
            "clean_future": dec[ts >= CLEAN_CUT],
        }
        print(hdr); print("-" * len(hdr))
        verdict = {}
        for period, d in periods.items():
            span = _span_days(d)
            for mode in ("v2", "live_guarded"):
                t = simulate_config(d, cfg, mode, params, args.stake, seed=args.seed)
                ci = (_ci(t) if (not t.empty and "filled" in t.columns)
                      else dict(n=0, ev=None, lo=None, hi=None, wr=None, total=None))
                ndec = int(len(d)); nfill = ci["n"]
                fillpct = (100.0 * nfill / ndec) if ndec else 0.0
                fpd = (nfill / (span * ACTIVE_FRAC)) if span > 0 else 0.0
                ci_s = (f"[{ci['lo']},{ci['hi']}]" if ci["ev"] is not None else "-")
                print(f"{name:18s} {period:12s} {mode:12s} {ndec:4d} {fillpct:5.1f}% "
                      f"{nfill:5d} {('$'+str(ci['ev'])) if ci['ev'] is not None else '-':>8s} "
                      f"{ci_s:>17s} {str(ci.get('wr','-')):>5s} "
                      f"{str(ci.get('total','-')):>7s} {fpd:11.1f}")
                rec = dict(config=name, period=period, model=mode, n_dec=ndec,
                           n_fill=nfill, fill_pct=round(fillpct, 1), ev=ci["ev"],
                           ci_lo=ci["lo"], ci_hi=ci["hi"], wr=ci.get("wr"),
                           total=ci.get("total"), fills_per_actday=round(fpd, 2),
                           span_days=round(span, 2), stake=args.stake)
                out.append(rec)
                if period == "clean_future" and mode == "live_guarded":
                    verdict[period] = (nfill, ci["lo"], fpd)
        # pre-registered verdict
        dh_fill = next((r["n_fill"] for r in out if r["config"] == name
                        and r["period"] == "devhold" and r["model"] == "live_guarded"), 0)
        dh_span = next((r["span_days"] for r in out if r["config"] == name
                        and r["period"] == "devhold" and r["model"] == "live_guarded"), 1)
        nfill, lo, fpd = verdict.get("clean_future", (0, None, 0))
        dh_rate = (dh_fill / (dh_span * ACTIVE_FRAC)) if dh_span else 0
        if nfill < 20:
            v = "INSUFFICIENT-DATA (hold, keep running)"
        elif lo is not None and lo > 0 and fpd >= 2:
            v = "KEEP-live (clean CI-lower>0, freq ok)"
        elif (lo is not None and lo <= 0) or (dh_rate and fpd < 0.5 * dh_rate):
            v = "DEMOTE-candidate (CI-lower<=0 or freq collapsed)"
        else:
            v = "MARGINAL (review)"
        print(f"  -> VERDICT {name}: {v}  [clean_future fills={nfill}, CI-lo={lo}, "
              f"fills/actday={fpd:.1f} vs devhold {dh_rate:.1f}]\n")
    OUTP = "data/research/live_gap/rejudge_clean.jsonl"
    with open(OUTP, "w") as f:
        for r in out:
            f.write(json.dumps(r) + "\n")
    print(f"wrote {OUTP}")


if __name__ == "__main__":
    main()
