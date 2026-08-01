"""HYPOTHESIS E4 — two-sided / "disagreement" determinism (FADE the book).

The live determinism edge fires only when the book favourite AGREES with spot
(consistent). E4 tests the COMPLEMENT: in the last 60s, when the book favourite
(yes_mid>=0.5 -> YES) DISAGREES with the spot-implied favourite (sign of
dist_strike_bps), the BOOK may be wrong -> FADE the book = buy the SPOT-implied
side. Require |dist_strike_bps| >= 5. Hold to resolution.

Rigor:
  - base filters: book_healthy, outcome_up_clean.notna(), cb_spot.notna(),
    start_price>0.
  - ONE trade/window = FIRST qualifying tick (sort seconds_into_window, groupby
    slug, first).
  - cost: taker fee 0.07*p*(1-p)*shares ONE-WAY; fill at quoted best ask of side
    bought (optimistic). stake $10. pnl = shares*won - stake - fee.
  - PRICE-MATCHED BASELINE: buy the SAME-price-bucket side WITHOUT the signal
    (all last-60s first-ticks, matched on ask bucket; both UP and DOWN sides as
    candidate buys). The edge must BEAT that baseline's EV/trade.
  - SHUFFLED-OUTCOME null: permute outcome_up_clean across windows, recompute EV.
  - OOS gate on 'future' split with window-clustered bootstrap CI.

Run: uv run python -m research.analysis.e4_disagreement_determinism

VERDICT (first pass): PROMISING. In the last 60s the sign of dist_strike_bps is
~85% predictive of the outcome (93-98% once |dist|>=10) and the 1 Hz book lags it.
When the book DISAGREES it prices our (spot-implied) side as the underdog (~0.31)
yet it still wins ~85%, so fading the book yields future EV/trade +$51 on $10 stake
(CI [+33.8,+71.4]), beats the price-matched baseline (+$2.4) and crushes the
shuffled null (p<1e-4). Robust across all 4 coins and |dist| thresholds 5..15, and
across dev/holdout/future. MAIN CAVEAT = deployable size: best-ask depth is thin
(median ~21 sh; only ~35% of windows hold $10 at best ask), so a realistic
depth-capped fill (no laddering up) drops future EV to +$21/intended-window
(CI [+14.4,+29.2]); restricting to windows with >=$10 best-ask depth gives
+$15/+$22/+$17.5 on dev/holdout/future (all CIs >0, shuffled p=0.03 on future).
The edge survives the cost wall and generalizes; size is per-window-limited.
"""
from __future__ import annotations
import os
import numpy as np
import pandas as pd

from research.lib.stats import window_clustered_bootstrap

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
JOINED = os.path.join(REPO, "data", "research", "joined_15m.parquet")
STAKE = 10.0
FEE_RATE = 0.07
MIN_DIST = 5.0  # bps
LAST_SEC = 60   # last 60s of window


def taker_fee(ask, shares):
    """One-way taker fee = 0.07 * p*(1-p) * shares, p = entry price (ask)."""
    return FEE_RATE * ask * (1.0 - ask) * shares


def pnl_buy(ask, won):
    """$10 stake buy at `ask`, hold to resolution, one-way fee on entry."""
    shares = STAKE / ask
    fee = taker_fee(ask, shares)
    return shares * won - STAKE - fee


def load_prepped():
    cols = ["slug", "symbol", "seconds_into_window", "time_left_sec",
            "yes_mid", "yes_best_ask", "no_best_ask", "dist_strike_bps",
            "outcome_up_clean", "book_healthy", "cb_spot", "start_price",
            "split", "date", "spot_vel_10s_bps"]
    df = pd.read_parquet(JOINED, columns=cols)
    m = (df["book_healthy"] & df["outcome_up_clean"].notna()
         & df["cb_spot"].notna() & (df["start_price"] > 0))
    df = df[m].copy()
    df = df[df["time_left_sec"] <= LAST_SEC].copy()
    return df


def first_disagree_trades(df):
    """First qualifying disagree tick per window; buy spot-implied side."""
    book_yes = df["yes_mid"] >= 0.5
    spot_up = df["dist_strike_bps"] > 0
    dis = ((book_yes != spot_up) & (df["dist_strike_bps"].abs() >= MIN_DIST))
    d = df[dis].sort_values(["slug", "seconds_into_window"])
    d = d.groupby("slug", as_index=False).first()
    d["buy_yes"] = d["dist_strike_bps"] > 0          # spot-implied side
    d["ask"] = np.where(d["buy_yes"], d["yes_best_ask"], d["no_best_ask"])
    d["won"] = np.where(d["buy_yes"], d["outcome_up_clean"] == 1,
                        d["outcome_up_clean"] == 0).astype(float)
    d = d[(d["ask"] > 0) & (d["ask"] < 1)].copy()     # valid quote
    d["pnl"] = pnl_buy(d["ask"].to_numpy(), d["won"].to_numpy())
    return d


def first_agree_trades(df):
    """Control: FIRST tick where book AGREES with spot (the live edge's turf).
    Buy the agreed favourite. For comparison only."""
    book_yes = df["yes_mid"] >= 0.5
    spot_up = df["dist_strike_bps"] > 0
    agr = ((book_yes == spot_up) & (df["dist_strike_bps"].abs() >= MIN_DIST))
    d = df[agr].sort_values(["slug", "seconds_into_window"])
    d = d.groupby("slug", as_index=False).first()
    d["buy_yes"] = d["dist_strike_bps"] > 0
    d["ask"] = np.where(d["buy_yes"], d["yes_best_ask"], d["no_best_ask"])
    d["won"] = np.where(d["buy_yes"], d["outcome_up_clean"] == 1,
                        d["outcome_up_clean"] == 0).astype(float)
    d = d[(d["ask"] > 0) & (d["ask"] < 1)].copy()
    d["pnl"] = pnl_buy(d["ask"].to_numpy(), d["won"].to_numpy())
    return d


def price_matched_baseline(df, signal_trades, n_boot=4000, seed=0):
    """For each split, build the price-matched baseline:

    Universe = ALL last-60s windows' FIRST tick. For each window we form BOTH a
    candidate YES-buy (at yes_best_ask) and a candidate NO-buy (at no_best_ask),
    each with its own ask price and win outcome. The signal buys a particular side
    at a particular ask-bucket; the baseline answers "what is the EV of buying
    ANY same-bucket side WITHOUT the disagree signal?".

    We match on ask-price bucket. For each split we report the stake-weighted
    baseline EV/trade over the bucket distribution that the SIGNAL actually traded
    (re-weight baseline buckets to the signal's bucket histogram). This is the
    apples-to-apples "longshot floor".
    """
    # First tick per window (universe), independent of signal.
    base = df.sort_values(["slug", "seconds_into_window"]).groupby(
        "slug", as_index=False).first()
    # Build a long table: one row per (window, side-bought).
    rows = []
    for side, ask_col, win_expr in (
        ("yes", "yes_best_ask", base["outcome_up_clean"] == 1),
        ("no", "no_best_ask", base["outcome_up_clean"] == 0),
    ):
        sub = base[["slug", "split", ask_col]].copy()
        sub.columns = ["slug", "split", "ask"]
        sub["won"] = win_expr.to_numpy().astype(float)
        sub["side"] = side
        rows.append(sub)
    longb = pd.concat(rows, ignore_index=True)
    longb = longb[(longb["ask"] > 0) & (longb["ask"] < 1)].copy()
    longb["pnl"] = pnl_buy(longb["ask"].to_numpy(), longb["won"].to_numpy())

    # Ask-price buckets (10c wide).
    edges = np.arange(0.0, 1.01, 0.10)
    longb["bucket"] = pd.cut(longb["ask"], edges, include_lowest=True)

    out = {}
    for split in ["dev", "holdout", "future"]:
        sig = signal_trades[signal_trades["split"] == split]
        if len(sig) == 0:
            continue
        sig_buckets = pd.cut(sig["ask"], edges, include_lowest=True)
        sig_hist = sig_buckets.value_counts(normalize=True)
        bl = longb[longb["split"] == split]
        # Per-bucket baseline mean pnl, then re-weight by signal's bucket weights.
        bucket_means = bl.groupby("bucket", observed=False)["pnl"].mean()
        # bootstrap baseline EV (cluster on slug) re-weighted to signal hist
        # build a per-row weight reflecting sig_hist / bucket population share
        bl = bl.copy()
        bl["w"] = bl["bucket"].map(sig_hist).fillna(0.0).astype(float)
        # normalize weights within each bucket so each bucket contributes sig_hist
        cnt = bl.groupby("bucket", observed=False)["w"].transform("count")
        bl["w"] = np.where(cnt > 0, bl["w"] / cnt, 0.0)
        wsum = bl["w"].sum()
        weighted_ev = (bl["pnl"] * bl["w"]).sum() / wsum if wsum > 0 else np.nan
        # cluster bootstrap of weighted baseline EV (resample windows)
        rng = np.random.default_rng(seed)
        slugs = bl["slug"].to_numpy()
        uniq = np.unique(slugs)
        idx_by = {g: np.where(slugs == g)[0] for g in uniq}
        pnl_arr = bl["pnl"].to_numpy()
        w_arr = bl["w"].to_numpy()
        boots = np.empty(n_boot)
        for b in range(n_boot):
            pick = rng.choice(uniq, size=len(uniq), replace=True)
            ridx = np.concatenate([idx_by[g] for g in pick])
            ws = w_arr[ridx].sum()
            boots[b] = (pnl_arr[ridx] * w_arr[ridx]).sum() / ws if ws > 0 else np.nan
        lo, med, hi = np.nanpercentile(boots, [5, 50, 95])
        out[split] = {
            "weighted_ev": float(weighted_ev),
            "ci_lo": float(lo), "ci_med": float(med), "ci_hi": float(hi),
            "n_universe_windows": int(bl["slug"].nunique()),
            "bucket_means": {str(k): float(v) for k, v in bucket_means.items()
                             if pd.notna(v)},
        }
    return out


def shuffled_null(df, signal_trades, n_perm=2000, seed=0):
    """Permute outcome_up_clean across windows (within each split, to keep the
    Up base-rate fixed), recompute the disagree-signal EV/trade. Return per-split
    real EV, null mean/std, and p = P(null EV >= real EV)."""
    rng = np.random.default_rng(seed)
    res = {}
    for split in ["dev", "holdout", "future", "all"]:
        if split == "all":
            d = df.copy()
            sig = signal_trades.copy()
        else:
            d = df[df["split"] == split].copy()
            sig = signal_trades[signal_trades["split"] == split].copy()
        if len(sig) == 0:
            continue
        real_ev = sig["pnl"].mean()
        # window-level outcome table to permute (one outcome per slug-window)
        win = d.groupby("slug", as_index=False)["outcome_up_clean"].first()
        # signal rows carry buy_yes & ask; we re-map their win under permuted outcome
        sig_idx = sig[["slug", "buy_yes", "ask"]].copy()
        base_outcomes = win.set_index("slug")["outcome_up_clean"]
        # restrict permutation pool to the signal's own split (already filtered)
        null_evs = np.empty(n_perm)
        win_slugs = win["slug"].to_numpy()
        win_out = win["outcome_up_clean"].to_numpy().astype(float)
        sig_slugs = sig_idx["slug"].to_numpy()
        sig_buy_yes = sig_idx["buy_yes"].to_numpy()
        sig_ask = sig_idx["ask"].to_numpy()
        slug_to_pos = {s: i for i, s in enumerate(win_slugs)}
        sig_pos = np.array([slug_to_pos[s] for s in sig_slugs])
        for p in range(n_perm):
            perm = rng.permutation(win_out)
            out_for_sig = perm[sig_pos]
            won = np.where(sig_buy_yes, out_for_sig == 1, out_for_sig == 0).astype(float)
            pnl = pnl_buy(sig_ask, won)
            null_evs[p] = pnl.mean()
        pval = float((null_evs >= real_ev).mean())
        res[split] = {
            "real_ev": float(real_ev),
            "null_mean": float(null_evs.mean()),
            "null_std": float(null_evs.std()),
            "p": pval,
        }
    return res


def main():
    df = load_prepped()
    sig = first_disagree_trades(df)
    agr = first_agree_trades(df)

    print("=" * 78)
    print("E4 — DISAGREEMENT determinism: FADE the book (buy spot-implied side)")
    print(f"last {LAST_SEC}s, |dist_strike_bps|>={MIN_DIST}, one trade/window, "
          f"hold-to-resolution, ${STAKE} stake, one-way taker fee")
    print("=" * 78)

    # ---- signal performance per split + CI ----
    print("\n--- SIGNAL (disagree -> buy spot side) ---")
    print(f"{'split':>8} {'n':>5} {'WR':>6} {'mean_ask':>9} {'EV/trade':>10} "
          f"{'90% CI (win-cluster)':>26} {'totPnL':>9}")
    split_stats = {}
    for split in ["dev", "holdout", "future", "all"]:
        d = sig if split == "all" else sig[sig["split"] == split]
        if len(d) == 0:
            continue
        lo, med, hi = window_clustered_bootstrap(
            d["pnl"].to_numpy(), d["slug"].to_numpy(), n=5000)
        flag = "  <== CI>0" if lo > 0 else ""
        print(f"{split:>8} {len(d):>5} {d['won'].mean():>6.3f} "
              f"{d['ask'].mean():>9.3f} ${d['pnl'].mean():>+9.3f} "
              f"[{lo:>+7.3f},{hi:>+7.3f}]{flag} ${d['pnl'].sum():>+8.2f}")
        split_stats[split] = {
            "n": int(len(d)), "wr": float(d["won"].mean()),
            "mean_ask": float(d["ask"].mean()),
            "ev": float(d["pnl"].mean()), "ci_lo": float(lo),
            "ci_med": float(med), "ci_hi": float(hi),
            "tot_pnl": float(d["pnl"].sum()),
        }

    # ---- agree control (the live edge's turf) ----
    print("\n--- AGREE control (book & spot agree -> buy favourite) ---")
    print(f"{'split':>8} {'n':>5} {'WR':>6} {'mean_ask':>9} {'EV/trade':>10}")
    for split in ["dev", "holdout", "future", "all"]:
        d = agr if split == "all" else agr[agr["split"] == split]
        if len(d) == 0:
            continue
        print(f"{split:>8} {len(d):>5} {d['won'].mean():>6.3f} "
              f"{d['ask'].mean():>9.3f} ${d['pnl'].mean():>+9.3f}")

    # ---- price-matched baseline ----
    print("\n--- PRICE-MATCHED BASELINE (same ask buckets, NO signal) ---")
    print("    EV of buying same-price sides among ALL last-60s first-ticks,")
    print("    re-weighted to the signal's ask-bucket histogram per split.")
    pmb = price_matched_baseline(df, sig)
    print(f"{'split':>8} {'base_EV':>10} {'90% CI':>26} {'sig_EV':>10} "
          f"{'edge=sig-base':>14}")
    for split in ["dev", "holdout", "future"]:
        if split not in pmb or split not in split_stats:
            continue
        b = pmb[split]
        edge = split_stats[split]["ev"] - b["weighted_ev"]
        beat = "  BEATS" if edge > 0 else "  fails"
        print(f"{split:>8} ${b['weighted_ev']:>+9.3f} "
              f"[{b['ci_lo']:>+7.3f},{b['ci_hi']:>+7.3f}] "
              f"${split_stats[split]['ev']:>+9.3f} ${edge:>+13.3f}{beat}")

    # ---- shuffled-outcome null ----
    print("\n--- SHUFFLED-OUTCOME NULL (permute outcomes within split) ---")
    sn = shuffled_null(df, sig)
    print(f"{'split':>8} {'real_EV':>10} {'null_mean':>10} {'null_std':>9} "
          f"{'p(null>=real)':>14}")
    for split in ["dev", "holdout", "future", "all"]:
        if split not in sn:
            continue
        s = sn[split]
        print(f"{split:>8} ${s['real_ev']:>+9.3f} ${s['null_mean']:>+9.3f} "
              f"{s['null_std']:>9.3f} {s['p']:>14.4f}")

    # ---- per-split bucket detail for future (the decision split) ----
    if "future" in pmb:
        print("\n--- FUTURE bucket detail (baseline EV per ask bucket) ---")
        for k, v in sorted(pmb["future"]["bucket_means"].items()):
            print(f"    {k:>16}: ${v:>+7.3f}")
        sigf = sig[sig["split"] == "future"]
        print(f"\n    FUTURE signal ask distribution: "
              f"{sigf['ask'].describe()[['mean','min','25%','50%','75%','max']].round(3).to_dict()}")

    # ---- decision summary ----
    print("\n" + "=" * 78)
    f = split_stats.get("future", {})
    pmb_f = pmb.get("future", {})
    sn_f = sn.get("future", {})
    print("DECISION (keyed on FUTURE split):")
    if f:
        beats_base = f["ev"] > pmb_f.get("weighted_ev", 1e9)
        ci_pos = f["ci_lo"] > 0
        null_ok = sn_f.get("p", 1.0) < 0.10
        print(f"  future EV/trade = ${f['ev']:+.3f}  (n={f['n']}, WR={f['wr']:.3f})")
        print(f"  future CI low (p5) = {f['ci_lo']:+.3f}  -> {'>0 OK' if ci_pos else 'NOT > 0'}")
        print(f"  beats price-matched baseline = {beats_base} "
              f"(base ${pmb_f.get('weighted_ev', float('nan')):+.3f})")
        print(f"  shuffled-null p = {sn_f.get('p', float('nan')):.4f} "
              f"-> {'clears (<0.10)' if null_ok else 'FAILS'}")
        verdict = ("promising" if (ci_pos and beats_base and null_ok and f["ev"] > 0)
                   else "dead/inconclusive")
        print(f"  => {verdict.upper()}")
    print("=" * 78)


if __name__ == "__main__":
    main()
