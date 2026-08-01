"""HYPOTHESIS E5 — late-window momentum-continuation.

When spot is moving AWAY from strike with momentum in the final minute
(sign(spot_vel_10s) == sign(dist_strike_bps): the favourite's lead is GROWING),
is the outcome even more locked than the book prices? Does conditioning on
momentum-away lift NET EV/trade OVER the plain determinism baseline (the control)?

Determinism baseline (CONTROL): in the late window, among consistent favourites
(book mid agrees with spot side), buy the favourite side, hold to resolution.
Treatment: same, but ALSO require momentum reinforcing the lead.

The treatment must BEAT the control AND a price-matched baseline AND a shuffled
null, with an OOS (future split) window-clustered CI lower bound > 0.

Cost truth: one-way taker fee = 0.07*p*(1-p)*shares, hold-to-resolution.
$10 stake, fill at quoted best ask of the side bought (optimistic).
pnl = shares*won - stake - fee ; shares = stake/ask ; won = 1 if side correct.
"""
from __future__ import annotations
import sys, os
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from research.lib.stats import window_clustered_bootstrap

PARQUET = "data/research/joined_15m.parquet"
STAKE = 10.0
FEE = 0.07

COLS = [
    "symbol", "slug", "seconds_into_window", "time_left_sec", "split",
    "book_healthy", "outcome_up_clean", "cb_spot", "start_price",
    "dist_strike_bps", "spot_vel_10s_bps", "spot_vel_3s_bps",
    "yes_best_ask", "no_best_ask", "yes_mid", "date",
]


def load():
    df = pd.read_parquet(PARQUET, columns=COLS)
    df = df[(df.book_healthy == True) & (df.outcome_up_clean.notna())
            & (df.cb_spot.notna()) & (df.start_price > 0)].copy()
    return df


def pnl_vec(ask, won):
    """Vectorised per-trade pnl. ask = price paid, won = bool side correct."""
    ask = np.asarray(ask, dtype="f8")
    won = np.asarray(won, dtype="f8")
    shares = STAKE / ask
    fee = FEE * ask * (1.0 - ask) * shares
    return shares * won - STAKE - fee


def build_trades(df, tmin, tmax, require_mom=False):
    """One trade per window = first qualifying tick in [tmin, tmax] time_left.
    Qualify = consistent favourite (book mid agrees with spot side) and, if
    require_mom, momentum reinforcing the lead. Returns a per-window frame."""
    w = df[(df.time_left_sec <= tmax) & (df.time_left_sec >= tmin)].copy()
    fav_up = w.dist_strike_bps > 0
    book_up = w.yes_mid > 0.5
    agree = (fav_up == book_up)
    qual = agree.copy()
    if require_mom:
        mom_away = np.sign(w.spot_vel_10s_bps) == np.sign(w.dist_strike_bps)
        # require non-zero velocity in the reinforcing direction
        mom_away = mom_away & (w.spot_vel_10s_bps != 0)
        qual = qual & mom_away
    w = w[qual].copy()
    w["fav_up"] = w.dist_strike_bps > 0
    w["ask"] = np.where(w.fav_up, w.yes_best_ask, w.no_best_ask)
    # drop unusable asks (need a real price to buy)
    w = w[(w.ask > 0) & (w.ask < 1.0)].copy()
    w["won"] = np.where(w.fav_up, (w.outcome_up_clean == 1).astype(int),
                        (w.outcome_up_clean == 0).astype(int))
    # FIRST qualifying tick per window
    w = w.sort_values("seconds_into_window").groupby("slug", as_index=False).first()
    w["pnl"] = pnl_vec(w.ask, w.won)
    return w


def summ(w, label):
    n = len(w)
    if n == 0:
        return dict(label=label, n=0, wr=np.nan, ask=np.nan, ev=np.nan,
                    p5=np.nan, p95=np.nan)
    p5, p50, p95 = window_clustered_bootstrap(w.pnl.values, w.slug.values, n=4000)
    return dict(label=label, n=n, wr=float(w.won.mean()), ask=float(w.ask.mean()),
                ev=float(w.pnl.mean()), p5=p5, p95=p95)


def price_matched_ev(treat, control, bins):
    """EV of the CONTROL re-weighted to the treatment's ask-bucket distribution.
    This isolates whether momentum adds anything BEYOND the prices it selects:
    we compare treatment EV to control EV at the SAME price mix.
    Returns (matched_control_ev, treat_ev, per_bucket table)."""
    tb = pd.cut(treat.ask, bins)
    cb = pd.cut(control.ask, bins)
    treat = treat.assign(_b=tb)
    control = control.assign(_b=cb)
    tw = treat.groupby("_b", observed=True).size()
    tw = tw / tw.sum()
    cev = control.groupby("_b", observed=True).pnl.mean()
    cn = control.groupby("_b", observed=True).size()
    rows = []
    matched = 0.0
    for b, wgt in tw.items():
        c_ev = cev.get(b, np.nan)
        t_ev = treat[treat._b == b].pnl.mean()
        t_n = (treat._b == b).sum()
        rows.append((str(b), float(wgt), int(t_n), float(t_ev),
                     int(cn.get(b, 0)), float(c_ev) if pd.notna(c_ev) else np.nan))
        if pd.notna(c_ev):
            matched += wgt * c_ev
    tab = pd.DataFrame(rows, columns=["bucket", "treat_wgt", "treat_n",
                                       "treat_ev", "ctrl_n", "ctrl_ev"])
    return matched, float(treat.pnl.mean()), tab


def shuffled_null(df, tmin, tmax, require_mom, n_perm=300, seed=1):
    """Permute outcome_up_clean across windows, rebuild treatment trades, recompute
    EV. Returns the distribution of null EVs and the p-value vs the real EV.
    Permutation is done on the per-window outcome (one label per slug-window)."""
    rng = np.random.default_rng(seed)
    w = df[(df.time_left_sec <= tmax) & (df.time_left_sec >= tmin)].copy()
    fav_up = w.dist_strike_bps > 0
    book_up = w.yes_mid > 0.5
    qual = (fav_up == book_up)
    if require_mom:
        mom_away = (np.sign(w.spot_vel_10s_bps) == np.sign(w.dist_strike_bps)) & (w.spot_vel_10s_bps != 0)
        qual = qual & mom_away
    w = w[qual].copy()
    w["fav_up"] = w.dist_strike_bps > 0
    w["ask"] = np.where(w.fav_up, w.yes_best_ask, w.no_best_ask)
    w = w[(w.ask > 0) & (w.ask < 1.0)].copy()
    w = w.sort_values("seconds_into_window").groupby("slug", as_index=False).first()
    # real EV
    won_real = np.where(w.fav_up, (w.outcome_up_clean == 1).astype(int),
                        (w.outcome_up_clean == 0).astype(int))
    real_ev = pnl_vec(w.ask, won_real).mean()
    # permute the per-window outcome
    outc = w.outcome_up_clean.values.copy()
    null_evs = np.empty(n_perm)
    for i in range(n_perm):
        perm = rng.permutation(outc)
        won = np.where(w.fav_up.values, (perm == 1).astype(int),
                       (perm == 0).astype(int))
        null_evs[i] = pnl_vec(w.ask.values, won).mean()
    p = float((null_evs >= real_ev).mean())
    return real_ev, float(null_evs.mean()), float(null_evs.std()), p


def run_window(df, tmin, tmax, tag):
    print(f"\n{'='*78}\nLATE WINDOW time_left in [{tmin},{tmax}]s   ({tag})\n{'='*78}")
    rows = []
    for split in ["dev", "holdout", "future", "ALL"]:
        d = df if split == "ALL" else df[df.split == split]
        ctrl = build_trades(d, tmin, tmax, require_mom=False)
        treat = build_trades(d, tmin, tmax, require_mom=True)
        sc = summ(ctrl, f"{split}/CONTROL-determinism")
        st = summ(treat, f"{split}/TREAT-mom_away")
        rows.append(sc); rows.append(st)
        print(f"\n--- split={split} ---")
        for s in (sc, st):
            print(f"  {s['label']:34s} n={s['n']:6d} wr={s['wr']:.4f} "
                  f"ask={s['ask']:.4f} EV/trade={s['ev']:+.4f} "
                  f"CI[{s['p5']:+.4f},{s['p95']:+.4f}]")
        if split in ("future", "ALL"):
            lift = st["ev"] - sc["ev"]
            print(f"  >> TREAT - CONTROL EV lift = {lift:+.4f}")
            # price-matched: control reweighted to treat price mix
            bins = [0, 0.6, 0.7, 0.8, 0.9, 0.95, 0.98, 1.0]
            m_ev, t_ev, tab = price_matched_ev(treat, ctrl, bins)
            print(f"  >> price-matched CONTROL EV (at treat price-mix) = {m_ev:+.4f}; "
                  f"treat EV = {t_ev:+.4f}; treat - matched = {t_ev - m_ev:+.4f}")
            print(tab.to_string(index=False))
    return rows


def main():
    df = load()
    print("rows after filters:", len(df))
    print("split sizes:", df.split.value_counts().to_dict())

    results = {}
    for tmin, tmax, tag in [(60, 120, "last 60-120s"), (1, 60, "last 1-60s"),
                            (1, 120, "last 1-120s")]:
        results[(tmin, tmax)] = run_window(df, tmin, tmax, tag)

    # ---- decisive OOS gates on the future split for the primary window(s) ----
    print(f"\n{'#'*78}\nDECISIVE OOS GATES (future split)\n{'#'*78}")
    fut = df[df.split == "future"]
    for tmin, tmax in [(1, 60), (60, 120), (1, 120)]:
        treat = build_trades(fut, tmin, tmax, require_mom=True)
        ctrl = build_trades(fut, tmin, tmax, require_mom=False)
        if len(treat) == 0:
            continue
        p5, p50, p95 = window_clustered_bootstrap(treat.pnl.values, treat.slug.values, n=6000)
        # shuffled null on FULL sample (more perms, more power) for this window+treat
        r_ev, null_mean, null_std, pval = shuffled_null(df, tmin, tmax, True, n_perm=500)
        bins = [0, 0.6, 0.7, 0.8, 0.9, 0.95, 0.98, 1.0]
        m_ev, t_ev, _ = price_matched_ev(treat, ctrl, bins)
        c_ev = ctrl.pnl.mean()
        print(f"\n[{tmin}-{tmax}s] FUTURE: treat n={len(treat)} EV={treat.pnl.mean():+.4f} "
              f"CI5/50/95=[{p5:+.4f},{p50:+.4f},{p95:+.4f}]")
        print(f"           control(future) EV={c_ev:+.4f}  treat-control={treat.pnl.mean()-c_ev:+.4f}")
        print(f"           price-matched ctrl EV={m_ev:+.4f}  treat-matched={t_ev-m_ev:+.4f}")
        print(f"           shuffled null(full): real_ev={r_ev:+.4f} null_mean={null_mean:+.4f} "
              f"null_std={null_std:.4f} p={pval:.4f}")
        results[("gate", tmin, tmax)] = dict(
            n=len(treat), ev=float(treat.pnl.mean()), p5=p5, p50=p50, p95=p95,
            ctrl_ev=float(c_ev), matched_ev=float(m_ev), treat_matched=float(t_ev - m_ev),
            null_p=float(pval), null_mean=float(null_mean))
    return results


if __name__ == "__main__":
    main()


# =====================================================================
# FOLLOW-UP: discriminating nulls + cheap-bucket isolation
# =====================================================================
def followup():
    df = load()
    print("\n" + "#"*78)
    print("FOLLOWUP: label-permutation null + cheap-favourite isolation")
    print("#"*78)

    for tmin, tmax in [(1, 60), (60, 120), (1, 120)]:
        fut = df[df.split == "future"]
        ctrl = build_trades(fut, tmin, tmax, require_mom=False)
        treat = build_trades(fut, tmin, tmax, require_mom=True)

        # ---- LABEL-PERMUTATION NULL (the discriminating one) ----
        # Within the consistent-favourite pool (first tick per window), randomly
        # assign the SAME number of windows the "momentum" tag and recompute EV.
        # If momentum carries no info beyond the prices/windows it happens to pick,
        # the real treat-EV sits inside this null.
        w = fut[(fut.time_left_sec <= tmax) & (fut.time_left_sec >= tmin)].copy()
        fav_up = w.dist_strike_bps > 0
        book_up = w.yes_mid > 0.5
        w = w[(fav_up == book_up)].copy()
        w["fav_up"] = w.dist_strike_bps > 0
        w["ask"] = np.where(w.fav_up, w.yes_best_ask, w.no_best_ask)
        w = w[(w.ask > 0) & (w.ask < 1.0)].copy()
        w["won"] = np.where(w.fav_up, (w.outcome_up_clean == 1).astype(int),
                            (w.outcome_up_clean == 0).astype(int))
        w["mom"] = (np.sign(w.spot_vel_10s_bps) == np.sign(w.dist_strike_bps)) & (w.spot_vel_10s_bps != 0)
        # first tick per window, carrying its real mom flag
        wf = w.sort_values("seconds_into_window").groupby("slug", as_index=False).first()
        wf["pnl"] = pnl_vec(wf.ask, wf.won)
        real_treat_ev = wf[wf.mom].pnl.mean()
        k = int(wf.mom.sum())
        rng = np.random.default_rng(7)
        nperm = 2000
        null = np.empty(nperm)
        idx = np.arange(len(wf))
        pnl_arr = wf.pnl.values
        for i in range(nperm):
            pick = rng.choice(idx, size=k, replace=False)
            null[i] = pnl_arr[pick].mean()
        p_label = float((null >= real_treat_ev).mean())
        print(f"\n[{tmin}-{tmax}s] LABEL-PERM null (future): treat_ev={real_treat_ev:+.4f} "
              f"null_mean={null.mean():+.4f} null_p5={np.percentile(null,5):+.4f} "
              f"null_p95={np.percentile(null,95):+.4f} p(real<=null)={p_label:.4f}")

        # ---- treat-minus-control paired-ish via window bootstrap on FULL+future ----
        # cheap-favourite isolation: restrict to ask<0.80 where any edge lives
        for split in ["future", "ALL"]:
            d = df if split == "ALL" else df[df.split == split]
            t = build_trades(d, tmin, tmax, require_mom=True)
            c = build_trades(d, tmin, tmax, require_mom=False)
            tc = t[t.ask < 0.80]; cc = c[c.ask < 0.80]
            te = t[t.ask >= 0.80]; ce = c[c.ask >= 0.80]
            def ci(x):
                if len(x) < 3: return (np.nan, np.nan, np.nan)
                return window_clustered_bootstrap(x.pnl.values, x.slug.values, n=4000)
            tcl = ci(tc); ccl = ci(cc); tel = ci(te)
            print(f"  [{split}] ask<0.80: TREAT n={len(tc)} ev={tc.pnl.mean():+.4f} "
                  f"CI[{tcl[0]:+.4f},{tcl[2]:+.4f}] | CTRL n={len(cc)} ev={cc.pnl.mean():+.4f} "
                  f"CI[{ccl[0]:+.4f},{ccl[2]:+.4f}]")
            print(f"  [{split}] ask>=0.80: TREAT n={len(te)} ev={te.pnl.mean():+.4f} "
                  f"CI[{tel[0]:+.4f},{tel[2]:+.4f}]")


if __name__ == "__main__":
    followup()
