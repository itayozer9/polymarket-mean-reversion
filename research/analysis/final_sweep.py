"""Lead C/F — final directional sweep, Polymarket 15m crypto Up/Down.

Three completeness-check hypotheses. Prior leads A, B, D all came back clean
negatives; this script tests the last open directional angles honestly.

  Hypothesis C — intra-window MOMENTUM.
    Phase 3's drop event study found that after an odds drop the price keeps
    going DOWN (momentum, not reversion). We tested buying the dropped (falling)
    side and lost. We never tested the momentum direction: when a side makes a
    sharp intra-window move, BUY the side moving in the trend's favour (the side
    that is RISING) and ride it. Exit on a profit target / max hold / window
    close (settle on corrected `outcome_up`). Net PnL/trade ± window-clustered
    CI, taker & maker, out-of-fold via day_blocked_kfold, vs a random-entry
    null. Is riding intra-window momentum net-positive after cost?

  Hypothesis F — time-of-day / liquidity regime.
    The old "ASIA hours" effect was an overfit artifact on corrupt data.
    Re-test honestly on corrected data: bucket windows by UTC hour and by a
    liquidity proxy (spread / depth / stale-rate). Is the cheap-side GROSS edge
    materially different in any hour/liquidity bucket — window-clustered CI
    excluding zero AND stable across an earlier/later dev split? Beware multiple
    testing across 24 hours: a couple of "significant" hours by chance is
    expected, so we require dev-internal early/late CV stability.

  Hypothesis G — 5m markets (optional, only if cheap).
    The 5m markets were never outcome-corrected (only 15m was). The gamma
    /events API exposes resolved 5m outcomes too (same approach as
    research/analysis/corrected_labels.py). We fetch them, run a quick
    calibration on the corrected 5m labels, and report whether 5m looks any
    different from 15m.

Conventions (shared with leads A/B/D):
  - Dev split May 15-20 only; May 21-22 hold-out SEALED — asserted untouched.
  - Healthy-book guard: genuine two-sided books only (not decided / crossed /
    one-sided). This guard has caught artifacts repeatedly.
  - Cost: taker round-trip ~16-21% of stake (Polymarket crypto fee
    0.07*p*(1-p) per share, both legs, plus the spread crossed); maker ~0 fee.
  - Window-clustered bootstrap CIs; the window is the resampling unit because
    ~87% of ticks are stale.

Run:  uv run --extra dev python -m research.analysis.final_sweep
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from research.holdout import (  # noqa: E402
    DEV_START, DEV_END, HOLDOUT_START, HOLDOUT_END,
)
from research.lib.splits import add_date_col, day_blocked_kfold  # noqa: E402
from research.lib.stats import window_clustered_bootstrap  # noqa: E402

DATA = REPO / "data" / "research"
TICKS_15M = DATA / "ticks_15m.parquet"
TICKS_5M = DATA / "ticks_5m.parquet"
CHARTS = REPO / "docs" / "research" / "charts"

STAKE = 10.0          # $ per trade
N_BOOT = 5000
SEED = 0
TAKER_FEE_RATE = 0.07  # Polymarket crypto fee = 0.07*p*(1-p) per share


# ===========================================================================
# Shared: load the genuine two-sided 15m book with the healthy-book guard.
# ===========================================================================

def load_15m_book(dev_only: bool = True) -> pd.DataFrame:
    """Load the 15m tick book, applying the hard healthy-book guard, deriving
    cheap side / both-side velocity / hour. dev_only restricts to May 15-20.

    The healthy guard (identical to lead D / lead A): both YES and NO quoted
    strictly inside (0.001, 0.999), not crossed, with complement-consistency
    (yes_ask + no_bid ~ 1). Decided-market / crossed / one-sided books fail it.
    """
    cols = [
        "slug", "market_slug", "symbol", "window_start_ts", "seconds_into_window",
        "time_left_sec", "yes_best_bid", "yes_best_ask", "no_best_bid",
        "no_best_ask", "yes_bid_depth", "yes_ask_depth", "no_bid_depth",
        "no_ask_depth", "yes_mid", "no_mid", "yes_velocity_30s", "no_velocity_30s",
        "yes_velocity_10s", "no_velocity_10s", "spot_move_30s", "realized_vol",
        "outcome_up",
    ]
    t = pd.read_parquet(TICKS_15M, columns=cols)
    t = t[t["window_start_ts"].notna()].copy()
    t["date"] = pd.to_datetime(
        t["window_start_ts"], unit="s", utc=True).dt.strftime("%Y-%m-%d")
    t = t[t["date"] >= DEV_START].copy()  # drop the lone 1970 garbage row

    ya, yb = t["yes_best_ask"], t["yes_best_bid"]
    na, nb = t["no_best_ask"], t["no_best_bid"]
    healthy = (
        (ya > 0.001) & (ya < 0.999) & (na > 0.001) & (na < 0.999)
        & (yb > 0) & (nb > 0) & (yb < ya) & (nb < na)
        & ((ya + nb - 1.0).abs() < 0.06) & ((na + yb - 1.0).abs() < 0.06)
    )
    h = t[healthy].copy()
    h["hour"] = pd.to_datetime(
        h["window_start_ts"], unit="s", utc=True).dt.hour
    # cheap side from the genuine book
    h["cheap_is_yes"] = h["yes_best_ask"] <= h["no_best_ask"]
    h["cheap_ask"] = np.where(h["cheap_is_yes"], h["yes_best_ask"], h["no_best_ask"])
    h["cheap_bid"] = np.where(h["cheap_is_yes"], h["yes_best_bid"], h["no_best_bid"])
    h["cheap_mid"] = 0.5 * (h["cheap_ask"] + h["cheap_bid"])
    h["cheap_spread"] = h["cheap_ask"] - h["cheap_bid"]
    h["cheap_depth"] = np.where(
        h["cheap_is_yes"], h["yes_ask_depth"], h["no_ask_depth"])
    h["cheap_won"] = np.where(
        h["cheap_is_yes"], h["outcome_up"], 1.0 - h["outcome_up"])

    if dev_only:
        h = h[(h["date"] >= DEV_START) & (h["date"] <= DEV_END)].copy()
        assert h["date"].max() <= DEV_END, "HOLD-OUT LEAKED INTO DEV"
        assert h["date"].min() >= DEV_START
    return h.reset_index(drop=True)


def _trades_per_day(cand: pd.DataFrame) -> float:
    span = cand["window_start_ts"].max() - cand["window_start_ts"].min()
    days = max(span / 86400.0, 1.0)
    return len(cand) / days


# ===========================================================================
# Hypothesis C — intra-window momentum.
# ===========================================================================

def _settle_pnl_momentum(cand: pd.DataFrame) -> pd.DataFrame:
    """Compute held-to-resolution taker & maker PnL for one trade per window.

    The trade buys the RISING side at the decision tick. `entry_ask` / `entry_bid`
    / `won` are already attached. Taker buys at the ask + entry fee; maker buys
    at the bid (a resting limit assumed filled) + 0 fee. Settlement pays $1 per
    share if `won` else $0, no exit fee on a held-to-resolution binary.
    """
    c = cand.copy()
    # taker: pay the ask, pay 0.07*p*(1-p) entry fee
    p_t = c["entry_ask"].to_numpy("f8")
    sh_t = STAKE / p_t
    fee_t = TAKER_FEE_RATE * p_t * (1 - p_t) * sh_t
    c["pnl_taker"] = sh_t * c["won"].to_numpy("f8") - STAKE - fee_t
    # maker: rest a limit BUY at the bid, 0 fee (assume fill — optimistic)
    p_m = c["entry_bid"].to_numpy("f8")
    sh_m = STAKE / p_m
    c["pnl_maker"] = sh_m * c["won"].to_numpy("f8") - STAKE
    return c


def run_hypothesis_C(book: pd.DataFrame, vel_pctile: float = 90.0) -> dict:
    """Detect sharp intra-window UP moves; enter the rising side; hold to
    resolution; settle on outcome_up. Taker & maker, out-of-fold, vs a
    random-entry null.

    A "sharp move" = at a mid-window tick, one side's `yes_velocity_30s` (signed
    30s mid change) is positive and above the `vel_pctile` percentile of all
    mid-window |velocity|. We BUY that rising side. One trade per window: the
    first qualifying tick (a patient bot acts once).
    """
    print(f"\n=== HYPOTHESIS C: intra-window momentum "
          f"(velocity >= p{vel_pctile:.0f}) ===")

    b = book.copy()
    # mid-window only: avoid the open noise and the decided last minute.
    mid = b[(b["seconds_into_window"] >= 60) & (b["time_left_sec"] >= 60)].copy()

    # The signed velocity of each side; a "rising side" has velocity > 0.
    # Threshold = p-th percentile of |velocity| pooled over both sides.
    allvel = np.abs(np.concatenate([
        mid["yes_velocity_30s"].to_numpy("f8"),
        mid["no_velocity_30s"].to_numpy("f8")]))
    thr = float(np.nanpercentile(allvel, vel_pctile))
    print(f"  sharp-move threshold |velocity_30s| >= {thr:.4f} "
          f"(p{vel_pctile:.0f} of {len(allvel):,} side-observations)")

    # For each tick, is YES rising sharply? is NO rising sharply?
    yes_rise = mid["yes_velocity_30s"] >= thr
    no_rise = mid["no_velocity_30s"] >= thr
    # A momentum entry: exactly one side is rising sharply (clean trend signal).
    # If both rise sharply the book is incoherent — skip.
    sig = mid[(yes_rise ^ no_rise)].copy()
    sig["buy_yes"] = sig["yes_velocity_30s"] >= thr

    # buy at the ask of the rising side (taker) / bid (maker)
    sig["entry_ask"] = np.where(
        sig["buy_yes"], sig["yes_best_ask"], sig["no_best_ask"])
    sig["entry_bid"] = np.where(
        sig["buy_yes"], sig["yes_best_bid"], sig["no_best_bid"])
    # tradeable price band: avoid degenerate extremes
    sig = sig[sig["entry_ask"].between(0.05, 0.95)
              & sig["entry_bid"].gt(0.0)].copy()
    # settlement of the bought side
    sig["won"] = np.where(
        sig["buy_yes"], sig["outcome_up"] == 1, sig["outcome_up"] == 0
    ).astype(float)

    # one trade per window: first qualifying tick
    sig = sig.sort_values("seconds_into_window")
    cand = sig.drop_duplicates("slug", keep="first").copy()
    cand = add_date_col(cand, ts_col="window_start_ts")
    cand = _settle_pnl_momentum(cand)

    res = _backtest_block(cand, label="momentum (ride the rising side)")

    # Profit-target variant: also report exit at a +0.10 mid gain or window
    # close. We approximate with the held-to-resolution result plus a note:
    # because the dataset is per-tick we CAN model intra-window exits.
    pt = _momentum_profit_target(book, cand, thr)
    res["profit_target"] = pt
    return res


def _momentum_profit_target(book: pd.DataFrame, cand: pd.DataFrame,
                             thr: float, target: float = 0.10) -> dict:
    """Variant of Hypothesis C with an intra-window exit: after entering the
    rising side, exit the first later tick whose bought-side BID has risen by
    `target` from entry; else settle on outcome at window close. Taker only
    (the realistic case for a momentum-chaser who must lift the offer).
    """
    # index the book by slug for fast per-window lookup
    by_slug = {s: g.sort_values("seconds_into_window")
               for s, g in book.groupby("slug")}
    pnls = []
    groups = []
    for _, row in cand.iterrows():
        g = by_slug.get(row["slug"])
        if g is None:
            continue
        later = g[g["seconds_into_window"] > row["seconds_into_window"]]
        buy_yes = bool(row["buy_yes"])
        side_bid = (later["yes_best_bid"] if buy_yes else later["no_best_bid"])
        entry_ask = float(row["entry_ask"])
        sh = STAKE / entry_ask
        fee_in = TAKER_FEE_RATE * entry_ask * (1 - entry_ask) * sh
        hit = side_bid[side_bid >= entry_ask + target]
        if len(hit):
            exit_p = float(hit.iloc[0])
            fee_out = TAKER_FEE_RATE * exit_p * (1 - exit_p) * sh
            pnl = sh * exit_p - STAKE - fee_in - fee_out
        else:
            # settle: pay $1/share if won else $0, no exit fee
            pnl = sh * float(row["won"]) - STAKE - fee_in
        pnls.append(pnl)
        groups.append(row["slug"])
    pnls = np.asarray(pnls, dtype="f8")
    if len(pnls) == 0:
        return {"n": 0}
    ci = window_clustered_bootstrap(pnls, np.asarray(groups), n=N_BOOT, seed=SEED)
    tpd = _trades_per_day(cand)
    print(f"  profit-target variant (+{target:.2f} exit, taker): "
          f"n={len(pnls)}  PnL/trade ${pnls.mean():+.3f}  "
          f"CI[{ci[0]:+.3f},{ci[2]:+.3f}]  ${pnls.mean()*tpd:+.2f}/day")
    return {"n": int(len(pnls)), "pnl_mean": float(pnls.mean()),
            "ci": ci, "per_day": float(pnls.mean() * tpd)}


def _backtest_block(cand: pd.DataFrame, label: str) -> dict:
    """Out-of-fold PnL + window-clustered CI + random-entry null for a one-
    trade-per-window candidate set with pnl_taker / pnl_maker / won attached."""
    n = len(cand)
    if n < 30:
        print(f"  [{label}] n={n} — too few trades, skipping stats.")
        return {"label": label, "n": n, "note": "too few"}
    n_days = cand["date"].nunique()
    wr = float(cand["won"].mean())

    folds = day_blocked_kfold(cand, k=min(5, n_days), seed=SEED)
    oof_t = [cand.loc[te, "pnl_taker"].mean() for _, te in folds]
    oof_m = [cand.loc[te, "pnl_maker"].mean() for _, te in folds]
    oof_taker = float(np.mean(oof_t))
    oof_maker = float(np.mean(oof_m))

    groups = cand["slug"].to_numpy()
    ci_t = window_clustered_bootstrap(
        cand["pnl_taker"].to_numpy("f8"), groups, n=N_BOOT, seed=SEED)
    ci_m = window_clustered_bootstrap(
        cand["pnl_maker"].to_numpy("f8"), groups, n=N_BOOT, seed=SEED)

    # Random-entry null: keep the SAME entry prices, shuffle which window's
    # outcome each trade gets. This is the honest null for "is the signal doing
    # anything beyond the price band it selects".
    rng = np.random.default_rng(SEED)
    ask = cand["entry_ask"].to_numpy("f8")
    sh = STAKE / ask
    fee = TAKER_FEE_RATE * ask * (1 - ask) * sh
    won = cand["won"].to_numpy("f8")
    null = np.empty(2000)
    for i in range(2000):
        w = rng.permutation(won)
        null[i] = (sh * w - STAKE - fee).mean()
    null_p = float((null >= oof_taker).mean())

    tpd = _trades_per_day(cand)
    print(f"  [{label}] n={n}  windows={n}  days={n_days}  WR={wr*100:.1f}%")
    print(f"    TAKER: oof PnL/trade ${oof_taker:+.3f}  "
          f"CI[{ci_t[0]:+.3f},{ci_t[2]:+.3f}]  ${oof_taker*tpd:+.2f}/day")
    print(f"    MAKER: oof PnL/trade ${oof_maker:+.3f}  "
          f"CI[{ci_m[0]:+.3f},{ci_m[2]:+.3f}]  ${oof_maker*tpd:+.2f}/day")
    print(f"    NULL (shuffled outcomes): mean ${null.mean():+.3f}  "
          f"p(null>=observed taker)={null_p:.3f}")
    return {
        "label": label, "n": n, "n_days": n_days, "win_rate": wr,
        "oof_taker": oof_taker, "oof_maker": oof_maker,
        "ci_taker": ci_t, "ci_maker": ci_m,
        "null_mean": float(null.mean()), "null_p": null_p,
        "trades_per_day": tpd,
        "taker_per_day": oof_taker * tpd, "maker_per_day": oof_maker * tpd,
    }


# ===========================================================================
# Hypothesis F — time-of-day / liquidity regime.
# ===========================================================================

def _debias(df: pd.DataFrame) -> pd.DataFrame:
    """One observation per (window, 60s slice): ~87% of ticks are stale, so
    tick-pooling over-weights long-lingering quotes. The de-biased cross-
    section is the honest unit for the gross-edge search."""
    d = df.copy()
    d["ts"] = (d["seconds_into_window"] // 60).astype(int)
    return d.groupby(["slug", "ts"], as_index=False).first()


def _gross_ci(sub: pd.DataFrame, seed: int = SEED):
    """Window-clustered 90% bootstrap of the mean cheap-side gross edge
    `cheap_won - cheap_mid` for one bucket. Returns (lo, mid, hi) or None."""
    if sub["slug"].nunique() < 8 or len(sub) < 30:
        return None
    e = (sub["cheap_won"] - sub["cheap_mid"]).to_numpy("f8")
    return window_clustered_bootstrap(e, sub["slug"].to_numpy(), n=N_BOOT, seed=seed)


def _gross_by(deb: pd.DataFrame, group_col: str) -> list[dict]:
    """Gross cheap-side edge per bucket of `group_col`, window-clustered CI."""
    out = []
    for b, sub in deb.groupby(group_col, observed=True):
        r = _gross_ci(sub)
        if r is None:
            continue
        out.append({
            "bucket": b, "n": int(len(sub)), "nwin": int(sub["slug"].nunique()),
            "mean_mid": float(sub["cheap_mid"].mean()),
            "realized": float(sub["cheap_won"].mean()),
            "gross": r[1], "ci_lo": r[0], "ci_hi": r[2],
        })
    return out


def run_hypothesis_F(book: pd.DataFrame) -> dict:
    """Bucket the de-biased cheap-side cross-section by UTC hour and by a
    liquidity proxy. A bucket is a real edge ONLY if its pooled gross-edge CI
    excludes zero AND both dev halves (May 15-17 / 18-20) independently agree.
    """
    print("\n=== HYPOTHESIS F: time-of-day / liquidity regime ===")
    deb = _debias(book)
    print(f"  de-biased cross-section: {len(deb):,} obs, "
          f"{deb['slug'].nunique()} windows")

    # ---- by UTC hour ----
    by_hour = _gross_by(deb, "hour")
    print("\n  Cheap-side GROSS edge by UTC hour (gross = cheap_won - cheap_mid):")
    print(f"  {'hr':>3} {'nwin':>5} {'mid':>7} {'real':>7} {'gross':>8} "
          f"{'ci_lo':>8} {'ci_hi':>8}  sig")
    hour_sig = []
    for r in sorted(by_hour, key=lambda x: x["bucket"]):
        sig = "***" if (r["ci_lo"] > 0 or r["ci_hi"] < 0) else ""
        if sig:
            hour_sig.append(r["bucket"])
        print(f"  {r['bucket']:>3d} {r['nwin']:>5d} {r['mean_mid']:>7.3f} "
              f"{r['realized']:>7.3f} {r['gross']:>+8.4f} "
              f"{r['ci_lo']:>+8.4f} {r['ci_hi']:>+8.4f}  {sig}")
    print(f"  hours with CI excluding zero: {hour_sig or 'none'}  "
          f"(of 24 — expect ~2-3 false positives by chance at 90% CI)")

    # ---- dev-internal CV: early vs late dev half, per hour ----
    early = deb[deb["date"] <= "2026-05-17"]
    late = deb[deb["date"] >= "2026-05-18"]
    cv_rows = {}
    for half, name in ((early, "early"), (late, "late")):
        for r in _gross_by(half, "hour"):
            cv_rows.setdefault(r["bucket"], {})[name] = r
    cv_stable = []
    for hr, hd in cv_rows.items():
        e, lt = hd.get("early"), hd.get("late")
        if not e or not lt:
            continue
        # stable = both halves same sign AND both CIs exclude zero
        e_sig = e["ci_lo"] > 0 or e["ci_hi"] < 0
        l_sig = lt["ci_lo"] > 0 or lt["ci_hi"] < 0
        same_sign = np.sign(e["gross"]) == np.sign(lt["gross"])
        if e_sig and l_sig and same_sign:
            cv_stable.append(hr)
    print(f"\n  CV-STABLE hours (both dev halves' GROSS CI excl. zero, "
          f"same sign): {cv_stable or 'NONE'}")
    print("  NOTE: a CV-stable GROSS hour effect is only a candidate. The de-")
    print("  biased cross-section has ~14 obs/window — its bootstrap n is ~14x")
    print("  a real one-trade-per-window backtest. A gross-edge flag must be")
    print("  re-tested as an HONEST one-trade-per-window NET-of-cost backtest.")

    # ---- honest tradeable test of the CV-stable hours ----
    # A negative cheap-side gross edge means the EXPENSIVE side over-performs;
    # we trade whichever side the hour favours. One trade per window (first
    # healthy mid-window tick), net of taker cost, window-clustered CI, plus
    # an early/late CV of that NET number — the real bar.
    tradeable = _tradeable_hours_backtest(book, cv_stable)

    # ---- by liquidity proxy ----
    # Three proxies, each cut into terciles on the dev set.
    liq = {}
    for col, name in (("cheap_spread", "spread"),
                      ("cheap_depth", "depth")):
        d = deb.copy()
        try:
            d["_lb"] = pd.qcut(d[col], 3, labels=["low", "mid", "high"],
                               duplicates="drop")
        except ValueError:
            continue
        rows = _gross_by(d, "_lb")
        liq[name] = rows
        print(f"\n  Cheap-side GROSS edge by {name} tercile:")
        for r in rows:
            sig = "***" if (r["ci_lo"] > 0 or r["ci_hi"] < 0) else ""
            print(f"    {str(r['bucket']):>5}: nwin={r['nwin']:>4d} "
                  f"gross={r['gross']:>+8.4f} "
                  f"CI[{r['ci_lo']:>+.4f},{r['ci_hi']:>+.4f}]  {sig}")

    # stale-rate proxy: per window, fraction of ticks where cheap_mid is
    # unchanged from the prior tick — a window-level liquidity/activity proxy.
    stale = _stale_rate_buckets(book)

    return {
        "by_hour": by_hour, "hour_sig": hour_sig, "cv_stable": cv_stable,
        "tradeable": tradeable, "by_liquidity": liq, "stale_rate": stale,
    }


def _tradeable_hours_backtest(book: pd.DataFrame,
                              cv_stable: list[int]) -> dict:
    """For each CV-stable hour, run an honest ONE-trade-per-window net-of-cost
    backtest: at the first healthy mid-window tick, BUY whichever side the
    hour's gross effect favours (cheap side if its gross > 0, else the
    expensive side), pay the taker entry fee, settle on outcome_up. Report the
    net PnL/trade with a window-clustered CI AND an early/late CV of that NET
    number. The combined-hours basket is the headline (single trade rule).

    A real edge requires: the combined-hours net CI excludes zero AND both
    dev halves of that net number agree in sign with CIs excluding zero.
    """
    if not cv_stable:
        print("\n  No CV-stable hours -> no tradeable hour backtest.")
        return {"hours": [], "verdict": "no_cv_stable_hours"}

    print(f"\n  Honest one-trade-per-window NET backtest of CV-stable hours "
          f"{cv_stable}:")
    mw = book[(book["seconds_into_window"] >= 60)
              & (book["time_left_sec"] >= 60)].copy()
    mw["exp_ask"] = np.where(mw["cheap_is_yes"],
                             mw["no_best_ask"], mw["yes_best_ask"])
    mw["exp_bid"] = np.where(mw["cheap_is_yes"],
                             mw["no_best_bid"], mw["yes_best_bid"])
    mw["exp_won"] = 1.0 - mw["cheap_won"]
    one = mw.sort_values("seconds_into_window").drop_duplicates(
        "slug", keep="first").copy()
    one = add_date_col(one, ts_col="window_start_ts")

    # decide the favoured side per hour from the de-biased gross sign
    deb = _debias(book)
    fav = {}
    for hr in cv_stable:
        sub = deb[deb["hour"] == hr]
        g = (sub["cheap_won"] - sub["cheap_mid"]).mean()
        fav[hr] = "cheap" if g > 0 else "expensive"

    def _net(df: pd.DataFrame) -> np.ndarray:
        """Net taker PnL/trade buying the per-hour favoured side."""
        p = np.where(df["_fav"] == "cheap",
                     df["cheap_ask"], df["exp_ask"]).astype("f8")
        won = np.where(df["_fav"] == "cheap",
                       df["cheap_won"], df["exp_won"]).astype("f8")
        sh = STAKE / p
        fee = TAKER_FEE_RATE * p * (1 - p) * sh
        return sh * won - STAKE - fee

    rows = []
    # per-hour
    for hr in cv_stable:
        sub = one[one["hour"] == hr].copy()
        sub["_fav"] = fav[hr]
        if len(sub) < 15:
            continue
        net = _net(sub)
        ci = window_clustered_bootstrap(
            net, sub["slug"].to_numpy(), n=N_BOOT, seed=SEED)
        rows.append({"hour": hr, "side": fav[hr], "n": int(len(sub)),
                     "net": float(net.mean()), "ci_lo": ci[0], "ci_hi": ci[2]})
        print(f"    hour {hr:>2d} (buy {fav[hr]:>9} side): n={len(sub):>3d}  "
              f"net taker ${net.mean():+.3f}/trade  "
              f"CI[{ci[0]:+.3f},{ci[2]:+.3f}]")

    # combined basket — the headline (one consistent rule, all CV-stable hours)
    basket = one[one["hour"].isin(cv_stable)].copy()
    basket["_fav"] = basket["hour"].map(fav)
    net = _net(basket)
    ci = window_clustered_bootstrap(
        net, basket["slug"].to_numpy(), n=N_BOOT, seed=SEED)
    tpd = _trades_per_day(basket)
    print(f"    COMBINED basket ({len(basket)} trades): "
          f"net taker ${net.mean():+.3f}/trade  "
          f"CI[{ci[0]:+.3f},{ci[2]:+.3f}]  ${net.mean()*tpd:+.2f}/day")

    # early/late CV of the NET basket
    cv = {}
    for half, name in ((basket[basket["date"] <= "2026-05-17"], "early"),
                       (basket[basket["date"] >= "2026-05-18"], "late")):
        if len(half) < 15:
            cv[name] = None
            continue
        n2 = _net(half)
        c2 = window_clustered_bootstrap(
            n2, half["slug"].to_numpy(), n=N_BOOT, seed=SEED)
        cv[name] = {"n": int(len(half)), "net": float(n2.mean()),
                    "ci_lo": c2[0], "ci_hi": c2[2]}
        print(f"    CV {name}: n={len(half):>3d}  net ${n2.mean():+.3f}  "
              f"CI[{c2[0]:+.3f},{c2[2]:+.3f}]")

    # verdict: basket net CI excludes zero AND both halves agree
    basket_sig = ci[0] > 0
    e, lt = cv.get("early"), cv.get("late")
    cv_ok = bool(e and lt
                 and e["ci_lo"] > 0 and lt["ci_lo"] > 0
                 and np.sign(e["net"]) == np.sign(lt["net"]))
    verdict = "edge" if (basket_sig and cv_ok) else "no_edge"
    print(f"    -> tradeable-hour verdict: {verdict.upper()}  "
          f"(basket net CI excl. 0: {basket_sig}; net CV-stable: {cv_ok})")

    return {
        "hours": rows, "favoured_side": fav,
        "basket_n": int(len(basket)), "basket_net": float(net.mean()),
        "basket_ci": ci, "basket_per_day": float(net.mean() * tpd),
        "cv": cv, "verdict": verdict,
    }


def _stale_rate_buckets(book: pd.DataFrame) -> list[dict]:
    """Per-window stale-rate (fraction of ticks with unchanged cheap_mid),
    bucketed into terciles; gross cheap-side edge per bucket."""
    g = book.sort_values(["slug", "seconds_into_window"])
    g = g.copy()
    g["mid_changed"] = g.groupby("slug")["cheap_mid"].diff().fillna(0).ne(0)
    per_win = g.groupby("slug").agg(
        stale_rate=("mid_changed", lambda x: 1.0 - x.mean()),
    ).reset_index()
    deb = _debias(book).merge(per_win, on="slug", how="left")
    try:
        deb["_sb"] = pd.qcut(deb["stale_rate"], 3,
                             labels=["active", "mid", "stale"],
                             duplicates="drop")
    except ValueError:
        return []
    rows = _gross_by(deb, "_sb")
    print("\n  Cheap-side GROSS edge by per-window stale-rate tercile:")
    for r in rows:
        sig = "***" if (r["ci_lo"] > 0 or r["ci_hi"] < 0) else ""
        print(f"    {str(r['bucket']):>7}: nwin={r['nwin']:>4d} "
              f"gross={r['gross']:>+8.4f} "
              f"CI[{r['ci_lo']:>+.4f},{r['ci_hi']:>+.4f}]  {sig}")
    return rows


# ===========================================================================
# Hypothesis G — 5m markets: fetch corrected outcomes + calibrate.
# ===========================================================================

_GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"


def _parse_5m_outcome(event_doc: dict) -> float | None:
    """Resolved outcome_up (1.0/0.0) from a gamma /events doc, or None."""
    if not event_doc:
        return None
    markets = event_doc.get("markets") or []
    if not markets:
        return None
    m = markets[0]
    outcomes_raw, prices_raw = m.get("outcomes"), m.get("outcomePrices")
    try:
        outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
        prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
    except (json.JSONDecodeError, TypeError):
        return None
    if not outcomes or not prices or len(outcomes) != len(prices):
        return None
    up_idx = next((i for i, o in enumerate(outcomes)
                   if str(o).strip().lower() in ("up", "yes")), None)
    if up_idx is None:
        return None
    try:
        up_price = float(prices[up_idx])
    except (ValueError, TypeError):
        return None
    if up_price >= 0.99:
        return 1.0
    if up_price <= 0.01:
        return 0.0
    return None


async def _fetch_5m(slugs: list[str], concurrency: int = 8) -> dict:
    """Fetch resolved 5m outcomes; return {slug: outcome_up}."""
    import aiohttp
    sem = asyncio.Semaphore(concurrency)
    out = {}

    async def one(session, slug):
        async with sem:
            for attempt in range(3):
                try:
                    async with session.get(
                            _GAMMA_EVENTS_URL, params={"slug": slug}) as r:
                        if r.status == 404:
                            return
                        r.raise_for_status()
                        data = await r.json()
                    ev = data[0] if isinstance(data, list) and data else None
                    res = _parse_5m_outcome(ev)
                    if res is not None:
                        out[slug] = res
                    await asyncio.sleep(0.1)
                    return
                except Exception:  # noqa: BLE001
                    await asyncio.sleep(0.4 * (attempt + 1))

    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        tasks = [one(session, s) for s in slugs]
        done = 0
        for c in asyncio.as_completed(tasks):
            await c
            done += 1
            if done % 1000 == 0:
                print(f"    5m API fetch: {done}/{len(slugs)}")
    return out


def run_hypothesis_G(do_fetch: bool = True) -> dict:
    """Fetch corrected 5m outcomes from the gamma API; run a quick calibration
    on the genuine two-sided 5m book; compare to 15m.

    Caches the fetched outcomes to data/research/corrected_labels_5m.parquet so
    re-runs are instant.
    """
    print("\n=== HYPOTHESIS G: 5m markets — corrected-outcome calibration ===")
    cache = DATA / "corrected_labels_5m.parquet"

    cols = ["slug", "symbol", "window_start_ts", "seconds_into_window",
            "time_left_sec", "yes_best_bid", "yes_best_ask", "no_best_bid",
            "no_best_ask"]
    t5 = pd.read_parquet(TICKS_5M, columns=cols)
    t5 = t5[t5["window_start_ts"].notna()].copy()
    t5["date"] = pd.to_datetime(
        t5["window_start_ts"], unit="s", utc=True).dt.strftime("%Y-%m-%d")
    t5 = t5[(t5["date"] >= DEV_START) & (t5["date"] <= DEV_END)].copy()
    assert t5["date"].max() <= DEV_END, "HOLD-OUT LEAKED INTO 5m G"
    slugs = sorted(t5["slug"].unique())
    print(f"  5m dev windows (May 15-20): {len(slugs)}")

    if cache.exists():
        lab = pd.read_parquet(cache)
        print(f"  loaded cached corrected 5m labels: {len(lab)} slugs")
    elif do_fetch:
        print(f"  fetching resolved 5m outcomes from gamma API "
              f"({len(slugs)} slugs)...")
        t0 = time.time()
        got = asyncio.run(_fetch_5m(slugs))
        lab = pd.DataFrame(
            {"slug": list(got.keys()), "outcome_up_5m": list(got.values())})
        lab.to_parquet(cache, index=False)
        print(f"  fetched {len(lab)}/{len(slugs)} resolved in "
              f"{time.time()-t0:.0f}s -> cached {cache.name}")
    else:
        print("  skipped (do_fetch=False) — 5m remains uncorrected.")
        return {"skipped": True}

    if lab.empty:
        print("  API returned no resolved 5m outcomes — skipping.")
        return {"skipped": True, "note": "no api outcomes"}

    # Healthy-book guard on the 5m book.
    ya, yb = t5["yes_best_ask"], t5["yes_best_bid"]
    na, nb = t5["no_best_ask"], t5["no_best_bid"]
    healthy = (
        (ya > 0.001) & (ya < 0.999) & (na > 0.001) & (na < 0.999)
        & (yb > 0) & (nb > 0) & (yb < ya) & (nb < na)
        & ((ya + nb - 1.0).abs() < 0.06) & ((na + yb - 1.0).abs() < 0.06)
    )
    h = t5[healthy].merge(lab, on="slug", how="inner")
    h = h.dropna(subset=["outcome_up_5m"])
    print(f"  healthy 5m ticks with corrected outcome: {len(h):,} "
          f"({h['slug'].nunique()} windows)")
    if h["slug"].nunique() < 30:
        return {"skipped": True, "note": "too few healthy 5m windows"}

    h["cheap_is_yes"] = h["yes_best_ask"] <= h["no_best_ask"]
    h["cheap_ask"] = np.where(h["cheap_is_yes"], h["yes_best_ask"], h["no_best_ask"])
    h["cheap_bid"] = np.where(h["cheap_is_yes"], h["yes_best_bid"], h["no_best_bid"])
    h["cheap_mid"] = 0.5 * (h["cheap_ask"] + h["cheap_bid"])
    h["cheap_won"] = np.where(
        h["cheap_is_yes"], h["outcome_up_5m"], 1.0 - h["outcome_up_5m"])

    # de-bias (one obs per window, 60s slice) and calibrate
    h["ts"] = (h["seconds_into_window"] // 60).astype(int)
    deb = h.groupby(["slug", "ts"], as_index=False).first()

    # Overall gross cheap-side edge.
    e = (deb["cheap_won"] - deb["cheap_mid"]).to_numpy("f8")
    ci = window_clustered_bootstrap(e, deb["slug"].to_numpy(), n=N_BOOT, seed=SEED)
    print(f"  5m cheap-side GROSS edge (de-biased): {e.mean():+.4f}  "
          f"CI[{ci[0]:+.4f},{ci[2]:+.4f}]")

    # Calibration by cheap_mid price bucket.
    print("  5m calibration (cheap_mid bucket -> realized cheap-win rate):")
    cal = []
    deb["_b"] = pd.cut(deb["cheap_mid"], [0, .2, .35, .5, .65, .8, 1.0])
    for b, sub in deb.groupby("_b", observed=True):
        if sub["slug"].nunique() < 8:
            continue
        ec = (sub["cheap_won"] - sub["cheap_mid"]).to_numpy("f8")
        cci = window_clustered_bootstrap(
            ec, sub["slug"].to_numpy(), n=N_BOOT, seed=SEED)
        sig = "***" if (cci[0] > 0 or cci[2] < 0) else ""
        print(f"    mid {str(b):>13}: nwin={sub['slug'].nunique():>4d}  "
              f"mid={sub['cheap_mid'].mean():.3f}  "
              f"realized={sub['cheap_won'].mean():.3f}  "
              f"gross={ec.mean():+.4f}  CI[{cci[0]:+.4f},{cci[2]:+.4f}]  {sig}")
        cal.append({"bucket": str(b), "nwin": int(sub["slug"].nunique()),
                    "mean_mid": float(sub["cheap_mid"].mean()),
                    "realized": float(sub["cheap_won"].mean()),
                    "gross": float(ec.mean()), "ci_lo": cci[0], "ci_hi": cci[2]})

    return {
        "skipped": False, "n_windows": int(deb["slug"].nunique()),
        "gross_overall": float(e.mean()), "gross_ci": ci, "calibration": cal,
    }


# ===========================================================================
def run(do_5m: bool = True) -> dict:
    print("=" * 72)
    print("Lead C/F — final directional sweep")
    print(f"Dev split {DEV_START}..{DEV_END}; hold-out {HOLDOUT_START}.."
          f"{HOLDOUT_END} SEALED (asserted untouched).")
    print("=" * 72)

    book = load_15m_book(dev_only=True)
    print(f"  15m healthy two-sided book (dev): {len(book):,} ticks, "
          f"{book['slug'].nunique()} windows, "
          f"{book['date'].min()}..{book['date'].max()}")

    res_c = run_hypothesis_C(book, vel_pctile=90.0)
    res_f = run_hypothesis_F(book)
    res_g = run_hypothesis_G(do_fetch=do_5m)

    print("\n" + "=" * 72)
    print("SUMMARY")
    print("=" * 72)
    # C
    if res_c.get("n", 0) >= 30:
        ci = res_c["ci_taker"]
        edge_c = "EDGE" if ci[0] > 0 else "no edge"
        print(f"  C (momentum):  taker ${res_c['oof_taker']:+.3f}/trade "
              f"CI[{ci[0]:+.3f},{ci[2]:+.3f}]  null_p={res_c['null_p']:.3f}"
              f"  -> {edge_c}")
    # F — the verdict is the HONEST tradeable test, not the gross-edge flag
    tr = res_f.get("tradeable", {})
    f_edge = tr.get("verdict") == "edge"
    print(f"  F (time/liq):  gross-CV-stable hours = "
          f"{res_f['cv_stable'] or 'NONE'}; "
          f"honest one-trade/window net-of-cost basket "
          f"-> {'EDGE' if f_edge else 'no edge'}")
    if tr.get("hours") is not None and tr.get("basket_n"):
        bci = tr["basket_ci"]
        print(f"                 basket net ${tr['basket_net']:+.3f}/trade "
              f"CI[{bci[0]:+.3f},{bci[2]:+.3f}], "
              f"net-CV-stable={tr['verdict']=='edge'}")
    # G
    if res_g.get("skipped"):
        print(f"  G (5m):        skipped ({res_g.get('note','not cheap')})")
    else:
        gci = res_g["gross_ci"]
        print(f"  G (5m):        gross ${res_g['gross_overall']:+.4f} "
              f"CI[{gci[0]:+.4f},{gci[2]:+.4f}] over {res_g['n_windows']} windows")

    return {"hypothesis_C": res_c, "hypothesis_F": res_f, "hypothesis_G": res_g}


if __name__ == "__main__":
    run()
