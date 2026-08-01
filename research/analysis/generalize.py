"""Phase 2 (core) — does each edge GENERALIZE? Two regimes on the FULL window.

R1 walk-forward (chronological, realism gate) + R2 Combinatorial Purged CV
(scattered held-out blocks, purge+embargo) — the user-requested CPCV, used as a
COMPLEMENT to walk-forward, never a replacement.

  determinism: a FIXED rule (no fitted params) -> WF/CPCV just slice the parity
    ledger by test days (the OOS EV distribution + %folds positive). PBO is run
    across the CONFIG GRID (is "best config" overfit?).
  stale_quote: has a FITTED curve -> leakage control REFITS the curve on each
    fold's TRAIN days and evaluates on its TEST days. This tests the EDGE, not a
    lucky curve vintage.

Run: uv run python -m research.analysis.generalize
"""
from __future__ import annotations
import os
import numpy as np
import pandas as pd

from research.lib.stats import window_clustered_bootstrap
from research.lib import rigor as R
from research.clean_window import available_clean_dates, CLEAN_START
from research.analysis.loss_patterns import (
    _base, _sq_prep, _fit_curve, JOINED, _ladders)
from research.analysis.build_full_ledgers import sq_ledger_parity
from research.analysis import gauntlet as G

LED = os.path.join(os.path.dirname(JOINED), "ledgers")


def _fold_ev(ledger, test_days):
    s = ledger[ledger["date"].isin(test_days)]
    return (float(s["pnl"].mean()), len(s)) if len(s) else (np.nan, 0)


def det_generalize(days):
    print("\n" + "=" * 70 + "\n DETERMINISM — generalization (fixed rule 60/5/0.90)\n" + "=" * 70)
    det = pd.read_parquet(os.path.join(LED, "det_full.parquet"))

    print("\n R1 walk-forward (expanding; test = next day):")
    wf = []
    for tr, te in R.walk_forward_splits(days, min_train=4, test=1):
        ev, n = _fold_ev(det, te)
        if n:
            wf.append(ev); print(f"   train≤{tr[-1]} -> test {te[0]}: n={n:>3} OOS ${ev:+.3f}/tr")
    print(f"   => {sum(np.array(wf)>0)}/{len(wf)} folds positive; mean OOS ${np.mean(wf):+.3f}/tr")

    print("\n R2 CPCV (n_groups=6,k_test=2,embargo=1; scattered test blocks):")
    cp = []
    for tr, te in R.combinatorial_purged_cv(days, n_groups=6, k_test=2, embargo_days=1):
        ev, n = _fold_ev(det, te)
        if n:
            cp.append(ev)
    cp = np.array(cp)
    print(f"   {len(cp)} folds; {np.mean(cp>0)*100:.0f}% positive; OOS EV mean ${cp.mean():+.3f} "
          f"p5/p50/p95 ${np.percentile(cp,5):+.2f}/{np.percentile(cp,50):+.2f}/{np.percentile(cp,95):+.2f}")


def det_pbo(days):
    """PBO across the det config grid (day x config daily-mean-PnL matrix)."""
    print("\n PBO across config grid (is the best config overfit?):")
    full = G._prep()
    ladders = {s: G.load_l2_ladders(s, CLEAN_START, days[-1]) for s in sorted(full["symbol"].unique())}
    grid = [(t, d, a) for t in (30, 60) for d in (5, 10, 20) for a in (0.88, 0.90, 0.95)]
    mat = []  # rows=days, cols=configs
    colnames = []
    per_day = {}
    for cfg in grid:
        t = G.precompute_trades(full, ladders, *cfg)
        if len(t) < 20:
            continue
        t = t.copy(); t["pnl"] = G._pnl(t)
        dmean = t.groupby("date")["pnl"].mean()
        per_day[cfg] = dmean
        colnames.append(cfg)
    M = pd.DataFrame(per_day).reindex(days).to_numpy("f8")
    M = np.where(np.isfinite(M), M, np.nanmean(M))  # fill empty (config,day) with col-agnostic mean
    res = R.pbo_cscv(M, n_splits=10)
    print(f"   {len(colnames)} configs x {len(days)} days; PBO={res['pbo']:.3f} "
          f"(median logit {res['median_logit']:+.2f}, {res['n_combos']} combos)  "
          f"{'LOW overfit' if res['pbo']<0.3 else 'HIGH overfit risk' if res['pbo']>0.5 else 'moderate'}")


def sq_generalize(days):
    print("\n" + "=" * 70 + "\n STALE-QUOTE — generalization (curve REFIT per fold; leakage-safe)\n" + "=" * 70)
    full = _base(pd.read_parquet(JOINED))
    sqf = _sq_prep(full)
    ladders = _ladders(sorted(full["symbol"].unique()))

    def oos_ledger(train_days, test_days):
        tr = sqf[sqf["date"].isin(train_days)]
        zc, p = _fit_curve(tr)
        te = sqf[sqf["date"].isin(test_days)]
        return sq_ledger_parity(te, ladders, zc, p)

    print("\n R1 walk-forward (expanding; curve refit on train; test=next day):")
    wf_ev, wf_all = [], []
    for tr, te in R.walk_forward_splits(days, min_train=4, test=1):
        led = oos_ledger(tr, te)
        if len(led):
            wf_ev.append(led["pnl"].mean()); wf_all.append(led)
            print(f"   train≤{tr[-1]} -> test {te[0]}: n={len(led):>4} OOS ${led['pnl'].mean():+.3f}/tr "
                  f"WR={led['won'].mean()*100:.0f}%")
    allwf = pd.concat(wf_all, ignore_index=True)
    lo, _, hi = window_clustered_bootstrap(allwf["pnl"].values, allwf["slug"].values, n=3000)
    print(f"   => {sum(np.array(wf_ev)>0)}/{len(wf_ev)} folds positive; pooled OOS "
          f"${allwf['pnl'].mean():+.3f}/tr CI[{lo:+.2f},{hi:+.2f}] n={len(allwf)}")

    print("\n R2 CPCV (n_groups=6,k_test=2,embargo=1; curve refit on each train):")
    cp_ev, cp_all = [], []
    for tr, te in R.combinatorial_purged_cv(days, n_groups=6, k_test=2, embargo_days=1):
        led = oos_ledger(tr, te)
        if len(led):
            cp_ev.append(led["pnl"].mean()); cp_all.append(led)
    allcp = pd.concat(cp_all, ignore_index=True)
    cp_ev = np.array(cp_ev)
    lo, _, hi = window_clustered_bootstrap(allcp["pnl"].values, allcp["slug"].values, n=3000)
    print(f"   {len(cp_ev)} folds; {np.mean(cp_ev>0)*100:.0f}% positive; OOS EV mean ${cp_ev.mean():+.3f} "
          f"p5/p50/p95 ${np.percentile(cp_ev,5):+.2f}/{np.percentile(cp_ev,50):+.2f}/{np.percentile(cp_ev,95):+.2f}")
    print(f"   pooled OOS ${allcp['pnl'].mean():+.3f}/tr CI[{lo:+.2f},{hi:+.2f}] n={len(allcp)}")


def run():
    days = available_clean_dates("btc")
    print(f"Generalization on FULL window: {days[0]}..{days[-1]} ({len(days)} days)")
    det_generalize(days)
    det_pbo(days)
    sq_generalize(days)


if __name__ == "__main__":
    run()
