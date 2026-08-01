"""HYPOTHESIS E3 -- Chainlink-vs-Coinbase settlement-oracle divergence.

Claim under test: Polymarket settles on CHAINLINK (col `chainlink_price`); we
observe COINBASE WS (`cb_spot`). In the last 60s, when the chainlink-implied
side of the strike != the coinbase-implied side, the SETTLEMENT follows
chainlink, so buy the chainlink-implied favourite and hold to resolution. The
residual EV must be measured BEYOND the plain determinism (Coinbase) rule.

VERDICT (first pass): DEAD -- the signal cannot be constructed. `chainlink_price`
is identically 0.0 for ALL 4,404,453 rows in joined_15m.parquet (every split,
every time bucket). The settlement-oracle feed the hypothesis keys on was never
populated in the tick data. This is already documented in
docs/research/corrected_labels.md:53 ("`chainlink_price` is always 0 in our tick
data, so Coinbase is our [proxy]") and FINAL_REPORT.md:206 ("the collector is
being upgraded to capture Chainlink oracle prices").

`dist_strike_bps` correlates 1.0 with the cb_spot-implied strike distance -- it
is derived purely from Coinbase spot, NOT from any chainlink reading. So there is
no second oracle in the data to diverge from.

What we CAN still quantify, and do below, as the closest observable proxy:
  How often does the TRUE settled outcome (`outcome_up_clean`, the real
  Polymarket/Chainlink resolution) disagree with the LATE-WINDOW Coinbase-implied
  side? That disagreement IS the realized Coinbase-vs-Chainlink basis (+timing
  slack). If it is tiny, even a hypothetical chainlink feed would add ~nothing
  over the existing Coinbase determinism edge -> redundant, as prior art warned.

We run the mandatory baselines/nulls on the ONLY constructible variant: the plain
Coinbase late-window determinism trade, so the StructuredOutput numbers are real
and honest about what is (and is not) testable.

Run: uv run python -m research.analysis.e3_oracle_divergence
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _REPO)
from research.lib.stats import window_clustered_bootstrap  # noqa: E402

JOINED = os.path.join(_REPO, "data", "research", "joined_15m.parquet")
STAKE = 10.0


def _fee(p, shares):
    """Polymarket taker fee, ONE-WAY (hold-to-resolution, no exit fee)."""
    return 0.07 * p * (1.0 - p) * shares


def _load():
    cols = [
        "symbol", "slug", "seconds_into_window", "time_left_sec", "date", "split",
        "cb_spot", "chainlink_price", "start_price", "dist_strike_bps",
        "yes_best_bid", "yes_best_ask", "no_best_bid", "no_best_ask", "yes_mid",
        "outcome_up_clean", "book_healthy",
    ]
    df = pd.read_parquet(JOINED, columns=cols)
    m = (df["book_healthy"] & df["outcome_up_clean"].notna()
         & df["cb_spot"].notna() & (df["start_price"] > 0))
    return df[m].copy()


# ---------------------------------------------------------------------------
# Step 1 -- the data autopsy: chainlink_price is dead.
# ---------------------------------------------------------------------------
def autopsy(df_all_path: str) -> dict:
    raw = pd.read_parquet(df_all_path, columns=["chainlink_price", "split"])
    info = {
        "n_rows": int(len(raw)),
        "chainlink_nonnull": int(raw["chainlink_price"].notna().sum()),
        "chainlink_gt0": int((raw["chainlink_price"] > 0).sum()),
        "chainlink_eq0_frac": float((raw["chainlink_price"] == 0.0).mean()),
        "chainlink_unique": [float(x) for x in np.unique(raw["chainlink_price"])][:5],
    }
    print("=== STEP 1: chainlink_price autopsy (FULL dataset, no filters) ===")
    print(f"  rows                 : {info['n_rows']:,}")
    print(f"  chainlink non-null   : {info['chainlink_nonnull']:,}")
    print(f"  chainlink > 0        : {info['chainlink_gt0']:,}")
    print(f"  fraction == 0.0      : {info['chainlink_eq0_frac']:.6f}")
    print(f"  unique values (<=5)  : {info['chainlink_unique']}")
    print("  -> chainlink_price carries ZERO information. The E3 signal "
          "(chainlink-implied side) cannot be constructed.")
    return info


# ---------------------------------------------------------------------------
# Step 2 -- closest observable proxy for the Coinbase-vs-Chainlink basis:
#           how often does the SETTLED outcome flip vs the late-window
#           Coinbase-implied side? (this is the realized basis + timing slack)
# ---------------------------------------------------------------------------
def basis_proxy(df: pd.DataFrame) -> dict:
    """Last-60s: per window FIRST tick. cb_up = cb_spot>strike. Does the true
    settled outcome agree? The disagreement rate IS the realized basis that a
    chainlink feed would have to beat-to-matter."""
    last = df[df["time_left_sec"] <= 60].copy()
    last = last.sort_values(["slug", "seconds_into_window"])
    first = last.groupby("slug", as_index=False).first()
    first["cb_up"] = first["cb_spot"] > first["start_price"]
    first["settled_up"] = first["outcome_up_clean"] == 1
    first["abs_bps"] = first["dist_strike_bps"].abs()
    out = {}
    print("\n=== STEP 2: settled-outcome vs late-window Coinbase side "
          "(proxy for the realized Coinbase-Chainlink basis) ===")
    print(f"{'abs_dist_bps band':>20} {'n_win':>7} {'disagree_rate':>14} "
          f"{'cb_side_WR':>11}")
    for lo, hi, name in [(0, 2, "0-2 (on the line)"), (2, 5, "2-5"),
                         (5, 15, "5-15"), (15, 40, "15-40"),
                         (40, 1e9, "40+"), (0, 1e9, "ALL")]:
        s = first[(first["abs_bps"] > lo) & (first["abs_bps"] <= hi)]
        if len(s) == 0:
            continue
        disagree = (s["cb_up"] != s["settled_up"]).mean()
        cb_wr = (s["cb_up"] == s["settled_up"]).mean()
        out[name] = {"n": int(len(s)), "disagree": float(disagree),
                     "cb_side_wr": float(cb_wr)}
        print(f"{name:>20} {len(s):>7} {disagree:>14.4f} {cb_wr:>11.4f}")
    return out


# ---------------------------------------------------------------------------
# Step 3 -- the ONLY constructible trade: plain Coinbase late-window
#           determinism. Buy the side cb_spot favours when decisively past the
#           strike; hold to resolution. Run mandatory baselines/nulls so the
#           StructuredOutput numbers are real.  (E3's "residual beyond
#           determinism" is necessarily 0 here -- there is no second oracle.)
# ---------------------------------------------------------------------------
def _build_trades(df, t_max, dist_min, max_ask, min_ask=0.50):
    """ONE trade per window: first tick with time_left in [1,t_max],
    abs_dist>=dist_min, consistent (favourite agrees with spot side),
    fill at quoted best ask of the favourite, ask in [min_ask,max_ask]."""
    d = df.copy()
    yes_fav = d["yes_mid"] >= 0.5
    d["fav_side"] = np.where(yes_fav, "yes", "no")
    d["fav_ask"] = np.where(yes_fav, d["yes_best_ask"], 1.0 - d["yes_best_bid"])
    d["fav_won"] = np.where(yes_fav, d["outcome_up_clean"] == 1,
                            d["outcome_up_clean"] == 0).astype("f8")
    d["abs_bps"] = d["dist_strike_bps"].abs()
    spot_yes = d["dist_strike_bps"] > 0
    d["consistent"] = (yes_fav & spot_yes) | (~yes_fav & ~spot_yes)
    cand = d[(d["time_left_sec"] >= 1) & (d["time_left_sec"] <= t_max)
             & (d["abs_bps"] >= dist_min) & d["consistent"]
             & (d["fav_ask"] >= min_ask) & (d["fav_ask"] <= max_ask)].copy()
    cand = cand.sort_values(["slug", "seconds_into_window"])
    first = cand.groupby("slug", as_index=False).first()
    if first.empty:
        return first
    ask = first["fav_ask"].clip(1e-6, 0.999999).to_numpy()
    shares = STAKE / ask
    won = first["fav_won"].to_numpy()
    first["pnl"] = shares * won - STAKE - _fee(ask, shares)
    return first


def _price_matched_baseline(df, t_max, ref_trades, min_ask=0.50):
    """Buy the SAME price-bucket favourite WITHOUT the determinism (dist) gate:
    any window whose favourite ask lands in the ref trades' ask band, first
    qualifying tick. Isolates the longshot/cheap-favourite effect."""
    if ref_trades.empty:
        return ref_trades
    a_lo = float(ref_trades["fav_ask"].quantile(0.05))
    a_hi = float(ref_trades["fav_ask"].quantile(0.95))
    a_lo = max(a_lo, min_ask)
    d = df.copy()
    yes_fav = d["yes_mid"] >= 0.5
    d["fav_ask"] = np.where(yes_fav, d["yes_best_ask"], 1.0 - d["yes_best_bid"])
    d["fav_won"] = np.where(yes_fav, d["outcome_up_clean"] == 1,
                            d["outcome_up_clean"] == 0).astype("f8")
    cand = d[(d["time_left_sec"] >= 1) & (d["time_left_sec"] <= t_max)
             & (d["fav_ask"] >= a_lo) & (d["fav_ask"] <= a_hi)].copy()
    cand = cand.sort_values(["slug", "seconds_into_window"])
    first = cand.groupby("slug", as_index=False).first()
    if first.empty:
        return first
    ask = first["fav_ask"].clip(1e-6, 0.999999).to_numpy()
    shares = STAKE / ask
    first["pnl"] = shares * first["fav_won"].to_numpy() - STAKE - _fee(ask, shares)
    return first


def _shuffled_null(trades_template_df, df, t_max, dist_min, max_ask,
                   n_shuffles=200, seed=0):
    """Permute outcome_up_clean across windows; rebuild the determinism trade;
    record mean pnl. p = fraction of shuffles whose mean >= real mean."""
    rng = np.random.default_rng(seed)
    win = df.drop_duplicates("slug").set_index("slug")["outcome_up_clean"]
    slugs = win.index.to_numpy()
    vals = win.to_numpy()
    means = np.empty(n_shuffles)
    base = df.copy()
    for i in range(n_shuffles):
        perm = pd.Series(rng.permutation(vals), index=slugs)
        base["outcome_up_clean"] = base["slug"].map(perm).to_numpy()
        tr = _build_trades(base, t_max, dist_min, max_ask)
        means[i] = tr["pnl"].mean() if len(tr) else np.nan
    return means


def run():
    info = autopsy(JOINED)
    df = _load()
    proxy = basis_proxy(df)

    # The plain Coinbase determinism trade -- the only thing E3 can reduce to.
    # Pick a sensible config on dev, then report dev/holdout/future honestly.
    T_MAX, DIST_MIN, MAX_ASK = 30, 10, 0.97
    print(f"\n=== STEP 3: plain Coinbase late-window determinism "
          f"(t_max={T_MAX}, dist_min={DIST_MIN}bps, ask<= {MAX_ASK}) ===")
    print("  (E3 'residual beyond determinism' is necessarily 0: no 2nd oracle "
          "in the data. We report determinism itself for an honest EV.)")

    res = {}
    for split in ["dev", "holdout", "future"]:
        sd = df[df["split"] == split]
        tr = _build_trades(sd, T_MAX, DIST_MIN, MAX_ASK)
        if tr.empty:
            print(f"  {split:>8}: no trades")
            continue
        pnl = tr["pnl"].to_numpy()
        grp = tr["slug"].to_numpy()
        lo, med, hi = window_clustered_bootstrap(pnl, grp, n=5000)
        base = _price_matched_baseline(sd, T_MAX, tr)
        bpnl = base["pnl"].to_numpy() if len(base) else np.array([np.nan])
        blo, bmed, bhi = (window_clustered_bootstrap(bpnl, base["slug"].to_numpy(),
                                                     n=5000)
                          if len(base) else (np.nan,) * 3)
        res[split] = {
            "n": int(len(tr)), "wr": float(tr["fav_won"].mean()),
            "ev": float(pnl.mean()), "ci": (lo, hi),
            "base_n": int(len(base)), "base_ev": float(np.nanmean(bpnl)),
            "base_ci": (blo, bhi),
            "pnl": pnl, "grp": grp,
        }
        print(f"  {split:>8}: n={len(tr):>4} WR={tr['fav_won'].mean():.3f} "
              f"EV=${pnl.mean():+.3f}/trade CI=[{lo:+.3f},{hi:+.3f}]  "
              f"| price-matched baseline n={len(base)} "
              f"EV=${np.nanmean(bpnl):+.3f} CI=[{blo:+.3f},{bhi:+.3f}]")

    # Shuffled null on the FUTURE split (the decision split).
    print("\n=== STEP 4: shuffled-outcome null on FUTURE split ===")
    fut = df[df["split"] == "future"]
    if "future" in res:
        real = res["future"]["ev"]
        sh = _shuffled_null(None, fut, T_MAX, DIST_MIN, MAX_ASK, n_shuffles=200)
        sh = sh[np.isfinite(sh)]
        p = float((sh >= real).mean())
        print(f"  real future EV=${real:+.3f}; shuffled null mean=${sh.mean():+.3f} "
              f"std={sh.std():.3f}; p(shuffle>=real)={p:.3f}")
        res["future"]["shuffled_p"] = p
        res["future"]["shuffled_mean"] = float(sh.mean())

    return info, proxy, res


if __name__ == "__main__":
    run()
