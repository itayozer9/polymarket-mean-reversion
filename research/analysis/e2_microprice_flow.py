"""
HYPOTHESIS E2 — order-flow / microprice divergence (microstructure edge).

Claim: depth-weighted microprice and signed taker flow LEAD the mid. When the
microprice diverges from yes_mid (book imbalance) or a one-sided taker burst hits,
the mid is stale and drifts toward the pressure => buy the indicated side, hold to
resolution. Test BOTH follow and fade.

Key feature definitions (mid-window only, time_left 60..840):
  nd  = (microprice - yes_mid) / halfspread   in [-1,+1]  (microprice is bounded
        inside [bid,ask] BY CONSTRUCTION, so "divergence" == where the depth-
        weighted price sits within the spread == signed book imbalance).
  flw = tr_signed_5s   (signed taker $ over the last 5s).

Trade mechanics: ONE trade/window = FIRST qualifying tick (sort seconds_into_window,
groupby slug, first). Hold to resolution. $10 stake, one-way taker fee
0.07*p*(1-p)*shares, fill at quoted best ask of the side bought (OPTIMISTIC; a real
L2-ladder walk is worse). pnl = shares*won - stake - fee.

Gates (decision keys on 'future' = freshest OOS):
  promising ONLY if future EV>0 AND window-clustered CI p5>0 AND beats the
  PRICE-MATCHED baseline (same side & ask-bucket, no signal) AND clears a
  within-price-bucket shuffled-outcome null (p<0.10).

=============================================================================
FINDINGS (see __main__ output):
  - Lead-lag precondition HOLDS but is weak. corr(nd, future-mid-move) ~0.05-0.06;
    corr(flow, future-mid-move) ~0.05-0.09. At 10s the SIGN of nd predicts the
    OPPOSITE short move (sign-agree 0.25) -> book imbalance mean-reverts at the
    tick scale; only by 30-60s does follow-through appear.
  - Conditional on price, FOLLOW nd lifts P(up) by ~+2-3 pts vs the opposite
    pressure, stable across dev/holdout/future. FOLLOW flow lifts ~+5-8 pts in
    mid buckets. FADE loses everywhere. So the directional content is real and in
    the FOLLOW direction.
  - The best single rule is the CONFLUENCE: nd>0.7 AND flw>50 (both agree).
    vs the PRICE-MATCHED baseline it wins by ~+1.3 pts on holdout & future
    (CI off zero); within-bucket shuffle null p~0.002 on the baseline-eligible
    subset. The MECHANISM is verified.
  - BUT it FAILS the net-EV bar. FULL-SAMPLE future EV=+0.33/trade with CI
    p5=-0.15 (below zero). Drop the single best future day -> EV +0.095. dev
    split is flat (-0.07). The ~+2pt directional edge does not clear the
    ~16-21%-of-stake cost wall at the avg ask ~0.57 it trades; realistic L2
    fills would erode the optimistic best-ask fill further. Outlier-day
    dependent, small N (~190 trades/day over 13 days).
  => INCONCLUSIVE: a real but sub-cost-wall microstructure signal. Not dead (it
     beats the price-matched baseline OOS), not promising (fails p5>0 on the full
     future split and is fragile). FOLLOW, never FADE.
=============================================================================
"""
from __future__ import annotations
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, "/Users/itayozer/dev/polymarket-mean-reversion")
from research.lib.stats import window_clustered_bootstrap
from research.lib.rigor import combinatorial_purged_cv, walk_forward_splits

PARQUET = "data/research/joined_15m.parquet"
STAKE = 10.0
FEE = 0.07
EDGES = np.linspace(0, 1, 26)  # 0.04-wide ask-price buckets for baselines


def load():
    cols = [
        "split", "slug", "symbol", "date", "seconds_into_window", "time_left_sec",
        "book_healthy", "outcome_up_clean", "cb_spot", "start_price",
        "microprice", "yes_mid", "yes_best_bid", "yes_best_ask",
        "no_best_ask", "tr_signed_5s",
    ]
    df = pd.read_parquet(PARQUET, columns=cols)
    m = (
        (df["book_healthy"] == True)
        & df["outcome_up_clean"].notna()
        & df["cb_spot"].notna()
        & (df["start_price"] > 0)
    )
    d = df[m].copy()
    d = d[(d["time_left_sec"] >= 60) & (d["time_left_sec"] <= 840)]
    hs = (d["yes_best_ask"] - d["yes_best_bid"]) / 2.0
    d["nd"] = ((d["microprice"] - d["yes_mid"]) / hs).where(hs > 0, 0.0)
    d["up"] = d["outcome_up_clean"].astype(int)
    return d.sort_values(["slug", "seconds_into_window"])


def signal_trades(d, kind, thr, flowT=None):
    """First qualifying tick per window for one (kind, thr[, flowT]).
    kind in {follow_nd, fade_nd, follow_flow, fade_flow, confluence}."""
    if kind == "confluence":
        pos = (d["nd"] > thr) & (d["tr_signed_5s"] > flowT)
        neg = (d["nd"] < -thr) & (d["tr_signed_5s"] < -flowT)
        follow = True
    elif kind.endswith("nd"):
        pos, neg, follow = d["nd"] > thr, d["nd"] < -thr, kind.startswith("follow")
    else:
        pos, neg, follow = d["tr_signed_5s"] > thr, d["tr_signed_5s"] < -thr, kind.startswith("follow")
    qual = pos | neg
    dd = d.copy()
    dd["_sy"] = np.where(pos, follow, (not follow))
    f = dd[qual].groupby("slug", as_index=False, sort=False).first()
    f["ask"] = np.where(f["_sy"], f["yes_best_ask"], f["no_best_ask"])
    f["won"] = np.where(f["_sy"], f["up"] == 1, f["up"] == 0).astype(float)
    sh = STAKE / f["ask"].values
    fee = FEE * f["ask"].values * (1 - f["ask"].values) * sh
    f["pnl"] = sh * f["won"].values - STAKE - fee
    return f


def matched_baseline_wr(d_split, side_yes, ask):
    """Win rate of buying the SAME side at the SAME ask-bucket across ALL windows
    (signal-agnostic). Population = first tick of every window in the split."""
    f = d_split.groupby("slug", as_index=False, sort=False).first()
    out = np.full(len(ask), np.nan)
    for s in (True, False):
        pa = f["yes_best_ask"].values if s else f["no_best_ask"].values
        pw = (f["up"].values == (1 if s else 0)).astype(float)
        pb = np.digitize(pa, EDGES)
        means = {bk: pw[pb == bk].mean() for bk in np.unique(pb) if (pb == bk).sum() >= 20}
        idx = np.where(side_yes == s)[0]
        sb = np.digitize(ask[idx], EDGES)
        for j, bk in zip(idx, sb):
            out[j] = means.get(bk, np.nan)
    return out


def within_bucket_shuffle_null(d_split, f, n=2000, seed=7):
    """Null preserving price+side composition: replace each trade's outcome with a
    random draw from the SAME (side, ask-bucket) population => isolates the
    signal's info beyond price. Returns (real_ev, null_mean, p(null>=real))."""
    rng = np.random.default_rng(seed)
    pop = d_split.groupby("slug", as_index=False, sort=False).first()
    ask = f["ask"].values
    sy = f["_sy"].values
    sh = STAKE / ask
    fee = FEE * ask * (1 - ask) * sh
    real = (sh * f["won"].values - STAKE - fee).mean()
    sb = np.digitize(ask, EDGES)
    pools = {}
    for s in (True, False):
        pa = pop["yes_best_ask"].values if s else pop["no_best_ask"].values
        pw = (pop["up"].values == (1 if s else 0)).astype(float)
        pbk = np.digitize(pa, EDGES)
        for bk in np.unique(pbk):
            pools[(s, bk)] = pw[pbk == bk]
    plist = [pools.get((sy[i], sb[i]), np.array([0.5])) for i in range(len(f))]
    nulls = np.empty(n)
    for b in range(n):
        won = np.array([p[rng.integers(len(p))] for p in plist])
        nulls[b] = (sh * won - STAKE - fee).mean()
    return real, float(nulls.mean()), float((nulls >= real).mean())


def ev_ci(f):
    p5, p50, p95 = window_clustered_bootstrap(f["pnl"].values, f["slug"].values, n=3000, seed=1)
    return f["pnl"].mean(), p5, p95


def main():
    d = load()
    print(f"loaded {len(d):,} mid-window ticks, {d['slug'].nunique()} windows\n")

    # ---- 1. scan all directions/thresholds, per split EV ----
    print("=== SCAN: per-split net EV/trade (full sample) ===")
    variants = ([("follow_nd", t) for t in (0.5, 0.7, 0.85)]
                + [("fade_nd", t) for t in (0.5, 0.7)]
                + [("follow_flow", t) for t in (50, 200)]
                + [("fade_flow", t) for t in (50, 200)])
    for kind, thr in variants:
        line = f"{kind:12s} thr={thr:<5}"
        for sp in ("dev", "holdout", "future"):
            f = signal_trades(d[d["split"] == sp], kind, thr)
            line += f"  {sp[:3]}EV={f['pnl'].mean():+.4f}(n={len(f)})"
        print(line)

    # ---- 2. the CONFLUENCE rule: full pipeline on the gate (future) ----
    print("\n=== CONFLUENCE nd>0.7 & flw>50 : FULL-SAMPLE per split ===")
    for sp in ("dev", "holdout", "future"):
        f = signal_trades(d[d["split"] == sp], "confluence", 0.7, flowT=50)
        ev, p5, p95 = ev_ci(f)
        print(f"  {sp:8s} n={len(f):4d} EV={ev:+.4f} CI[{p5:+.4f},{p95:+.4f}] WR={f['won'].mean():.3f}")

    print("\n=== CONFLUENCE on FUTURE: baseline + shuffle null (full sample) ===")
    ds = d[d["split"] == "future"]
    f = signal_trades(ds, "confluence", 0.7, flowT=50)
    ev, p5, p95 = ev_ci(f)
    bwr = matched_baseline_wr(ds, f["_sy"].values, f["ask"].values)
    ok = np.isfinite(bwr)
    sh = STAKE / f["ask"].values
    fee = FEE * f["ask"].values * (1 - f["ask"].values) * sh
    pbase = sh * bwr - STAKE - fee
    diff = f["pnl"].values[ok] - pbase[ok]
    d5, _, d95 = window_clustered_bootstrap(diff, f["slug"].values[ok], n=3000, seed=1)
    real, nullm, pval = within_bucket_shuffle_null(ds, f, n=2000, seed=7)
    print(f"  future full EV={ev:+.4f} CI[{p5:+.4f},{p95:+.4f}] n={len(f)}")
    print(f"  price-matched baseline EV={np.nanmean(pbase):+.4f} ; DIFF(sig-base)={diff.mean():+.4f} CI[{d5:+.4f},{d95:+.4f}] (n_ok={ok.sum()})")
    print(f"  within-bucket shuffle null: real={real:+.4f} nullmean={nullm:+.4f} p(null>=real)={pval:.3f}")
    promising = (ev > 0 and p5 > 0 and diff.mean() > 0 and d5 > 0 and pval < 0.10)
    print(f"  => {'PROMISING' if promising else 'NOT (full-sample) promising'} "
          f"[EV>0:{ev>0}, p5>0:{p5>0}, beats_base(diff_p5>0):{d5>0}, null_p<.10:{pval<0.10}]")

    # ---- 3. robustness: per-day, CPCV, walk-forward, outlier sensitivity ----
    print("\n=== CONFLUENCE robustness (fixed rule, all 13 days) ===")
    fall = signal_trades(d, "confluence", 0.7, flowT=50)
    days = sorted(d["date"].unique())
    by_day = fall.groupby("date")["pnl"].agg(["mean", "size"])
    print("  per-day EV:", {k: round(v, 3) for k, v in by_day["mean"].items()})
    cpcv = [fall[fall["date"].isin(te)]["pnl"].mean()
            for _, te in combinatorial_purged_cv(days, 6, 2, 1)
            if len(fall[fall["date"].isin(te)]) > 30]
    cpcv = np.array(cpcv)
    print(f"  CPCV: {len(cpcv)} folds mean={cpcv.mean():+.4f} frac>0={(cpcv>0).mean():.2f}")
    wf = [fall[fall["date"].isin(te)]["pnl"].mean()
          for _, te in walk_forward_splits(days, 4, 1)
          if len(fall[fall["date"].isin(te)]) > 20]
    print(f"  walk-forward: {len(wf)} steps mean={np.mean(wf):+.4f} frac>0={np.mean([x>0 for x in wf]):.2f}")
    fut = fall[fall["split"] == "future"]
    worst_drop = fut[fut["date"] != fut.groupby("date")["pnl"].mean().idxmax()]
    print(f"  future EV={fut['pnl'].mean():+.4f} ; excl single best future day -> {worst_drop['pnl'].mean():+.4f}")
    ae, ap5, ap95 = ev_ci(fall)
    print(f"  ALL-13-day EV={ae:+.4f} CI[{ap5:+.4f},{ap95:+.4f}] n={len(fall)} (~{len(fall)//13}/day)")


if __name__ == "__main__":
    main()
