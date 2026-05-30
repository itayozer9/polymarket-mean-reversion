"""Phase 5 — robustness gauntlet for the Phase 1 determinism edge (primary).

Hardens the locked rule (last 60s, |spot-strike| >= dist_min bps, buy favourite
at ask <= max_ask, hold to resolution) against the ways a real edge dies:

  1. COST-STRESS: higher fee, +1c entry slippage, larger latency, random rejects,
     and the worst-case ALL-combined. The edge must survive the combined stress.
  2. MULTIPLE-TESTING (White-style reality check): we scanned ~36 dev configs;
     permute outcomes, re-run ALL configs, take the best mean-PnL each shuffle ->
     null distribution of "best-of-36 under no edge". Real best must beat it.
  3. PER-REGIME: split by realized vol; the edge must hold in quiet AND volatile.

Trades' ENTRY (fill price/shares/fee) is outcome-independent, so we precompute it
once per config and re-settle under permuted outcomes for a fast, exact null.

Run: uv run python -m research.analysis.gauntlet
"""
from __future__ import annotations
import os
import numpy as np
import pandas as pd

from research.sim.fills_v2 import walk_buy, FeeSchedule
from research.dataset.feeds import load_l2_ladders
from research.lib.stats import window_clustered_bootstrap
from research.clean_window import CLEAN_START, available_clean_dates

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
JOINED = os.path.join(REPO, "data", "research", "joined_15m.parquet")
STAKE = 10.0
_LV = range(1, 11)

# the config grid that was scanned on dev (for the multiple-testing null)
GRID = [(t, d, a) for t in (15, 30, 60) for d in (5, 10, 20, 40) for a in (0.90, 0.95, 0.97)]
PRIMARY = (60, 5, 0.90)


def _prep(split=None):
    df = pd.read_parquet(JOINED)
    df = df[df["book_healthy"] & df["outcome_up_clean"].notna()
            & df["cb_spot"].notna() & (df["start_price"] > 0)].copy()
    if split:
        df = df[df["split"].isin(split)]
    yf = df["yes_mid"] >= 0.5
    df["fav_side"] = np.where(yf, "yes", "no")
    df["fav_ask"] = np.where(yf, df["yes_best_ask"], 1.0 - df["yes_best_bid"])
    df["fav_won"] = np.where(yf, df["outcome_up_clean"] == 1, df["outcome_up_clean"] == 0).astype("f8")
    df["abs_dist_bps"] = df["dist_strike_bps"].abs()
    sfy = df["dist_strike_bps"] > 0
    df["consistent"] = (yf & sfy) | (~yf & ~sfy)
    return df


def precompute_trades(df, ladders, t_max, dist_min, max_ask, latency=2,
                      fee_mult=1.0, slippage_c=0.0, reject_prob=0.0, seed=0):
    """One trade/window; returns per-trade entry economics (outcome-independent)
    + the realized win flag + regime tag."""
    fees = FeeSchedule(rate=0.07 * fee_mult)
    rng = np.random.default_rng(seed)
    cand = df[(df["time_left_sec"] >= 1) & (df["time_left_sec"] <= t_max)
              & (df["abs_dist_bps"] >= dist_min) & df["consistent"]
              & df["fav_ask"].between(0.50, max_ask)]
    first = cand.sort_values(["slug", "seconds_into_window"]).groupby("slug", as_index=False).first()
    rows = []
    for _, r in first.iterrows():
        if reject_prob > 0 and rng.random() < reject_prob:
            continue
        lad = ladders.get(r["symbol"])
        if lad is None:
            continue
        try:
            lr = lad.loc[(r["slug"], int(r["seconds_into_window"]) + latency)]
        except KeyError:
            continue
        if isinstance(lr, pd.DataFrame):
            lr = lr.iloc[0]
        if r["fav_side"] == "yes":
            px = [lr[f"ask_px_{i}"] + slippage_c for i in _LV]; sz = [lr[f"ask_sz_{i}"] for i in _LV]
        else:
            px = [1.0 - lr[f"bid_px_{i}"] + slippage_c for i in _LV]; sz = [lr[f"bid_sz_{i}"] for i in _LV]
        f = walk_buy(px, sz, STAKE, fees=fees)
        if not f.filled or f.unfilled_usd > STAKE * 0.5:
            continue
        rows.append({"slug": r["slug"], "symbol": r["symbol"], "date": r["date"],
                     "shares": f.shares, "notional": f.notional_usd, "fee": f.fee_usd,
                     "won": bool(r["fav_won"]), "rvol": float(r["realized_vol"])})
    return pd.DataFrame(rows)


def _pnl(t, won=None):
    w = t["won"].to_numpy() if won is None else won
    return np.where(w, t["shares"].to_numpy(), 0.0) - t["notional"].to_numpy() - t["fee"].to_numpy()


def cost_stress(df, ladders):
    print("\n=== 1. COST-STRESS (primary rule, full clean window) ===")
    scenarios = [
        ("baseline (lat2)", dict()),
        ("fee 1.5x", dict(fee_mult=1.5)),
        ("+1c slippage", dict(slippage_c=0.01)),
        ("latency 5s", dict(latency=5)),
        ("latency 10s", dict(latency=10)),
        ("reject 30%", dict(reject_prob=0.30)),
        ("ALL combined", dict(fee_mult=1.5, slippage_c=0.01, latency=5, reject_prob=0.30)),
    ]
    t_max, dist_min, max_ask = PRIMARY
    print(f"{'scenario':>18} {'trades':>7} {'WR':>6} {'$/trade':>9} {'90% CI':>18}")
    for name, kw in scenarios:
        t = precompute_trades(df, ladders, t_max, dist_min, max_ask, **kw)
        if len(t) < 10:
            print(f"{name:>18} {len(t):>7}  too few"); continue
        pnl = _pnl(t)
        lo, _, hi = window_clustered_bootstrap(pnl, t["slug"].to_numpy(), n=2000)
        flag = "  <==" if lo > 0 else "  (CI crosses 0)"
        print(f"{name:>18} {len(t):>7} {t['won'].mean():>6.3f} ${pnl.mean():>+8.3f} "
              f"[{lo:>+6.3f},{hi:>+6.3f}]{flag}")


def multiple_testing(dev, ladders, n_shuffle=2000, seed=7):
    print(f"\n=== 2. MULTIPLE-TESTING reality check ({len(GRID)} configs scanned on dev) ===")
    print("   null = CALIBRATED: each trade's outcome ~ Bernoulli(entry_price). Tests")
    print("   whether the favourite beats its OWN PRICE more than chance, across the sweep.")
    per_cfg = {}
    for cfg in GRID:
        t = precompute_trades(dev, ladders, *cfg)
        if len(t) >= 20:
            per_cfg[cfg] = t
    real_means = {cfg: _pnl(t).mean() for cfg, t in per_cfg.items()}
    real_best_cfg = max(real_means, key=real_means.get)
    real_best = real_means[real_best_cfg]
    # per-cfg arrays incl. entry price p = notional/shares (the calibrated win prob)
    cfg_arrays = {}
    for cfg, t in per_cfg.items():
        sh = t["shares"].to_numpy(); no = t["notional"].to_numpy(); fe = t["fee"].to_numpy()
        cfg_arrays[cfg] = (sh, no, fe, no / sh)  # last = entry price per trade
    rng = np.random.default_rng(seed)
    null_best = np.empty(n_shuffle)
    for s in range(n_shuffle):
        best = -1e9
        for sh, no, fe, p in cfg_arrays.values():
            won = rng.random(len(p)) < p           # calibrated draw
            m = (np.where(won, sh, 0.0) - no - fe).mean()
            if m > best:
                best = m
        null_best[s] = best
    pval = float((null_best >= real_best).mean())
    print(f"   real best config = {real_best_cfg}  mean ${real_best:+.3f}/trade")
    print(f"   calibrated best-of-{len(per_cfg)} null: median ${np.median(null_best):+.3f}, "
          f"95th ${np.percentile(null_best,95):+.3f}")
    print(f"   p(null best >= real best) = {pval:.4f}  => "
          f"{'PASS — beats its own price beyond sweep luck' if pval < 0.05 else 'FAIL'}")


def per_regime(df, ladders):
    print("\n=== 3. PER-REGIME (realized-vol split at median) ===")
    t = precompute_trades(df, ladders, *PRIMARY)
    med = t["rvol"].median()
    for name, sub in (("quiet (rvol<=med)", t[t["rvol"] <= med]),
                      ("volatile (rvol>med)", t[t["rvol"] > med])):
        pnl = _pnl(sub)
        lo, _, hi = window_clustered_bootstrap(pnl, sub["slug"].to_numpy(), n=2000)
        print(f"   {name:>22}: n={len(sub):>3} WR={sub['won'].mean():.3f} "
              f"${pnl.mean():+.3f}/trade CI[{lo:+.3f},{hi:+.3f}]")


def run():
    full = _prep()
    dev = _prep(split=["dev"])
    ladders = {s: load_l2_ladders(s, CLEAN_START,
               (available_clean_dates("btc") or [CLEAN_START])[-1])
               for s in sorted(full["symbol"].unique())}
    print(f"Phase 5 gauntlet — primary rule t_max={PRIMARY[0]} dist_min={PRIMARY[1]} "
          f"max_ask={PRIMARY[2]}  (full clean window n_windows={full['slug'].nunique()})")
    cost_stress(full, ladders)
    multiple_testing(dev, ladders)
    per_regime(full, ladders)


if __name__ == "__main__":
    run()
