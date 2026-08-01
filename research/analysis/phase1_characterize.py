"""Phase 1 — characterize the two live edges on the FULL window + quantify overfit.

Operates on the parity full-window ledgers (build_full_ledgers.py). Reports, per
edge:
  A. per-split EV/WR with window-clustered CIs (dev / holdout / future=fresh-OOS)
     -> does the edge hold out-of-sample on days that postdate all discovery?
  B. overfitting battery on DAILY pnl: Deflated Sharpe (n_trials sensitivity),
     PSR, min track-record length, + PBO across the det config grid.
  C. stale-quote tail decomposition: top-k winner share, EV minus top winners,
     EV/WR by entry-ask bucket (the cheap-zone H3 question).
  D. worst-path: block-bootstrap max drawdown / losing streak (daily).

Honest by construction: daily pnl is the observation unit for Sharpe/DSR (trades
within a day are correlated); CIs are window-clustered; n_trials is reported as a
sensitivity range because we have tried many configs over the program.

Run: uv run python -m research.analysis.phase1_characterize
"""
from __future__ import annotations
import os
import numpy as np
import pandas as pd

from research.lib.stats import window_clustered_bootstrap
from research.lib import rigor as R

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LED = os.path.join(REPO, "data", "research", "ledgers")
SPLITS = ("dev", "holdout", "future")


def _ci(pnl, groups):
    lo, mid, hi = window_clustered_bootstrap(np.asarray(pnl, "f8"),
                                             np.asarray(groups), n=4000)
    return lo, mid, hi


def per_split(led, name):
    print(f"\n=== {name}: per-split EV/WR (window-clustered 90% CI) ===")
    print(f"{'split':>8} {'days':>4} {'n':>5} {'WR':>6} {'$/trade':>9} {'90% CI':>20} {'total$':>9}")
    for sp in SPLITS:
        s = led[led["split"] == sp]
        if not len(s):
            continue
        lo, _, hi = _ci(s["pnl"], s["slug"])
        flag = " <==OOS+" if (sp == "future" and lo > 0) else ""
        print(f"{sp:>8} {s['date'].nunique():>4} {len(s):>5} {s['won'].mean()*100:>5.1f}% "
              f"${s['pnl'].mean():>+8.3f} [{lo:>+7.3f},{hi:>+7.3f}]  ${s['pnl'].sum():>+8.1f}{flag}")
    lo, _, hi = _ci(led["pnl"], led["slug"])
    print(f"{'FULL':>8} {led['date'].nunique():>4} {len(led):>5} {led['won'].mean()*100:>5.1f}% "
          f"${led['pnl'].mean():>+8.3f} [{lo:>+7.3f},{hi:>+7.3f}]  ${led['pnl'].sum():>+8.1f}")


def overfit_battery(led, name, n_trials_grid=(36, 100, 1000)):
    print(f"\n=== {name}: overfitting battery (daily pnl as observation unit) ===")
    dp = R.daily_pnl_from_ledger(led)
    print(f"  n_days={len(dp)}  mean/day=${dp.mean():+.2f}  std/day=${dp.std(ddof=1):+.2f}  "
          f"per-day Sharpe={R.sharpe(dp.values):+.3f}  Sortino={R.sortino(dp.values):+.3f}  "
          f"Calmar={R.calmar(dp.values):+.2f}")
    print(f"  skew={R._skew(dp.values):+.2f} kurt={R._kurt(dp.values):.2f}  "
          f"PSR(>0)={R.probabilistic_sharpe_ratio(dp.values):.3f}")
    for nt in n_trials_grid:
        d = R.deflated_sharpe_ratio(dp.values, n_trials=nt)
        mtrl = R.min_track_record_length(dp.values, sr_benchmark=d["sr0"])
        print(f"  n_trials={nt:>5}: sr0(luck)={d['sr0']:.3f}  DSR={d['dsr']:.3f}  "
              f"minTRL@DSR={'inf' if not np.isfinite(mtrl) else f'{mtrl:.0f}d'}")
    wp = R.block_bootstrap_worstpath(dp.values, n=5000, block=2)
    if wp:
        print(f"  worst-path (block-bootstrap): maxDD p5/p50=${wp['max_drawdown']['p5']:.0f}/"
              f"${wp['max_drawdown']['p50']:.0f}  longest losing streak p95="
              f"{wp['longest_losing_streak']['p95']:.0f}d  total p5/p50/p95="
              f"${wp['total']['p5']:.0f}/${wp['total']['p50']:.0f}/${wp['total']['p95']:.0f}")


def sq_tail(led):
    print("\n=== stale_quote: tail decomposition + cheap-zone (H3) ===")
    s = led.sort_values("pnl", ascending=False)
    tot = s["pnl"].sum()
    for k in (1, 5, 10, 20):
        share = s["pnl"].head(k).sum() / tot if tot else float("nan")
        rest = s["pnl"].iloc[k:].sum()
        print(f"  top {k:>2} winners: {share*100:5.1f}% of total; total WITHOUT top {k} = ${rest:+.1f}")
    print(f"  trades > +$30: {(led['pnl']>30).sum()}   > +$20: {(led['pnl']>20).sum()}   > +$10: {(led['pnl']>10).sum()}")
    print("\n  EV/WR by entry-ask bucket (is the cheap zone a net loser even WITH jackpots?):")
    bins = [0, 0.05, 0.10, 0.15, 0.20, 0.30, 0.50, 0.70, 1.0]
    led = led.copy(); led["ab"] = pd.cut(led["entry_ask"], bins)
    g = led.groupby("ab", observed=True).agg(n=("pnl", "size"), WR=("won", "mean"),
                                             ev=("pnl", "mean"), tot=("pnl", "sum"))
    for ab, r in g.iterrows():
        print(f"    ask {str(ab):>14}: n={int(r['n']):>4} WR={r['WR']*100:5.1f}% "
              f"${r['ev']:+.2f}/tr  total=${r['tot']:+8.1f}")
    # first-pass floor test (rigorous price-matched baseline is a separate step)
    for floor in (0.15, 0.18, 0.20, 0.25):
        f = led[led["entry_ask"] >= floor]
        lo, _, hi = _ci(f["pnl"], f["slug"]) if len(f) > 5 else (np.nan,)*3
        ff = led[(led["split"] == "future") & (led["entry_ask"] >= floor)]
        print(f"  floor ask>={floor}: keeps {len(f)}/{len(led)}  ${f['pnl'].mean():+.3f}/tr "
              f"CI[{lo:+.2f},{hi:+.2f}]  total=${f['pnl'].sum():+.1f}  "
              f"(future: n={len(ff)} ${ff['pnl'].mean() if len(ff) else float('nan'):+.3f}/tr)")


def run():
    det = pd.read_parquet(os.path.join(LED, "det_full.parquet"))
    sq = pd.read_parquet(os.path.join(LED, "sq_full.parquet"))
    print("#" * 72)
    print(f"PHASE 1 — full window | det n={len(det)} days={det['date'].nunique()} | "
          f"sq n={len(sq)} days={sq['date'].nunique()}")
    print("#" * 72)
    per_split(det, "determinism")
    overfit_battery(det, "determinism")
    per_split(sq, "stale_quote")
    overfit_battery(sq, "stale_quote")
    sq_tail(sq)


if __name__ == "__main__":
    run()
