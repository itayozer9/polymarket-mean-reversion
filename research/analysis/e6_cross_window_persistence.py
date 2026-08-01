"""HYPOTHESIS E6 — cross-window persistence / autocorrelation.

Does a symbol's PRIOR 15m window outcome / move predict the NEXT window, enough to
trade an early-window entry that clears the taker cost wall?

Two opposing directional rules, conditioned on prev-window strength:
  - MOMENTUM : bet the SAME direction the prev window resolved.
  - REVERSAL : bet the OPPOSITE direction.
Also tests the prev_fav_lost variant (prior art: weak).

Rules of the game (match live edges):
  - filter book_healthy, outcome_up_clean.notna(), cb_spot.notna(), start_price>0
  - ONE trade per window = first qualifying early tick (sort seconds_into_window,
    groupby slug, first); hold to resolution; settle on outcome_up_clean
  - fill at quoted best ask of the side bought (optimistic; noted)
  - taker fee = 0.07*p*(1-p)*shares one-way; $10 stake
  - pnl = shares*won - stake - fee ; shares = stake/ask ; won = side resolves correct
  - prev window MUST be the immediately-preceding 900s window of the SAME symbol.

Mandatory nulls: price-matched baseline, shuffled-outcome null, OOS gate on 'future'
with window-clustered CI (p5>0). Verdict 'promising' only if all pass.
"""
from __future__ import annotations
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, "/Users/itayozer/dev/polymarket-mean-reversion")
from research.lib.stats import window_clustered_bootstrap

PARQUET = "/Users/itayozer/dev/polymarket-mean-reversion/data/research/joined_15m.parquet"
STAKE = 10.0
FEE_RATE = 0.07
WINDOW_SEC = 900
EARLY_MAX_SEC = 60  # entry must be in the first 60s of the window
SEED = 0


# -----------------------------------------------------------------------------
# loaders
# -----------------------------------------------------------------------------
def load():
    cols = [
        "symbol", "slug", "window_start_ts", "seconds_into_window", "time_left_sec",
        "outcome_up_clean", "book_healthy", "cb_spot", "start_price",
        "yes_best_ask", "no_best_ask", "yes_best_bid", "no_best_bid",
        "dist_strike_bps", "date", "split",
    ]
    df = pd.read_parquet(PARQUET, columns=cols)
    df = df[
        df.outcome_up_clean.notna()
        & (df.start_price > 0)
        & df.cb_spot.notna()
        & df.book_healthy
    ].copy()
    df = df.sort_values(["symbol", "window_start_ts", "seconds_into_window"])
    return df


def fee(p, shares):
    return FEE_RATE * p * (1.0 - p) * shares


def trade_pnl(ask, won):
    """$10 stake at quoted ask, one-way fee, hold to resolution."""
    shares = STAKE / ask
    f = fee(ask, shares)
    return shares * won - STAKE - f


# -----------------------------------------------------------------------------
# build per-window table with prev-window features
# -----------------------------------------------------------------------------
def build_windows(df):
    """One row per (symbol, window_start_ts): outcome, signed/abs move, favorite,
    fav_won, plus the EARLY entry quotes (first tick within EARLY_MAX_SEC)."""
    # close spot = last tick's cb_spot (proxy for resolution spot)
    last = df.groupby(["symbol", "window_start_ts"]).tail(1)
    win = last[["symbol", "slug", "window_start_ts", "outcome_up_clean", "start_price",
                "cb_spot", "date", "split"]].copy()
    win["move_bps"] = (win.cb_spot - win.start_price) / win.start_price * 1e4
    win["abs_move_bps"] = win.move_bps.abs()

    # early entry quotes = first tick with seconds_into_window <= EARLY_MAX_SEC
    early = df[df.seconds_into_window <= EARLY_MAX_SEC].groupby(
        ["symbol", "window_start_ts"]).head(1)
    early = early[["symbol", "window_start_ts", "seconds_into_window",
                   "yes_best_ask", "no_best_ask", "yes_best_bid", "no_best_bid",
                   "dist_strike_bps"]].rename(
        columns={"seconds_into_window": "entry_sec",
                 "dist_strike_bps": "entry_dist_bps"})
    win = win.merge(early, on=["symbol", "window_start_ts"], how="inner")

    # favorite at entry = cheaper-to-be-true side. yes_ask < no_ask -> YES favorite.
    win["fav_is_up"] = (win.yes_best_ask < win.no_best_ask).astype(int)
    # ties -> use dist_strike: if spot above strike, UP favored
    tie = win.yes_best_ask == win.no_best_ask
    win.loc[tie, "fav_is_up"] = (win.loc[tie, "entry_dist_bps"] > 0).astype(int)
    win["fav_won"] = (win.fav_is_up == win.outcome_up_clean).astype(int)

    win = win.sort_values(["symbol", "window_start_ts"]).reset_index(drop=True)
    # lag-1 features WITHIN symbol
    g = win.groupby("symbol")
    win["prev_ts"] = g["window_start_ts"].shift(1)
    win["prev_outcome"] = g["outcome_up_clean"].shift(1)
    win["prev_move_bps"] = g["move_bps"].shift(1)
    win["prev_abs_move_bps"] = g["abs_move_bps"].shift(1)
    win["prev_fav_won"] = g["fav_won"].shift(1)
    # consecutive only: prev must be exactly 900s before
    win["consec"] = (win.window_start_ts - win.prev_ts) == WINDOW_SEC
    return win


# -----------------------------------------------------------------------------
# directional trade given a chosen side
# -----------------------------------------------------------------------------
def apply_side(win, bet_up):
    """bet_up: 1=buy UP(yes), 0=buy DOWN(no). Returns ask, won, pnl per row."""
    ask = np.where(bet_up == 1, win.yes_best_ask.values, win.no_best_ask.values)
    won = np.where(bet_up == 1,
                   win.outcome_up_clean.values,
                   1.0 - win.outcome_up_clean.values)
    shares = STAKE / ask
    f = FEE_RATE * ask * (1.0 - ask) * shares
    pnl = shares * won - STAKE - f
    return ask, won, pnl


def ev_block(values, groups, label):
    values = np.asarray(values, "f8")
    if len(values) < 5:
        return {"label": label, "n": int(len(values)), "ev": float("nan"),
                "p5": float("nan"), "p50": float("nan"), "p95": float("nan"),
                "wr": float("nan")}
    p5, p50, p95 = window_clustered_bootstrap(values, groups, seed=SEED)
    return {"label": label, "n": int(len(values)), "ev": float(values.mean()),
            "p5": p5, "p50": p50, "p95": p95}


# -----------------------------------------------------------------------------
# price-matched baseline:
#   for the SAME set of (symbol, window) entries, buy the same side WITHOUT signal,
#   i.e. for each traded window pick the side at the same price BUCKET but with the
#   sign chosen at random (coin flip) — then the baseline EV is the raw longshot EV
#   of trading those price buckets. We implement the proper version: bucket the
#   *bet side's ask*; for every window in a bucket, the baseline is the average pnl
#   of betting BOTH sides' prices in that bucket regardless of signal. Concretely we
#   compute the EV of betting the side whose ask falls in the bucket WITHOUT
#   conditioning on prev-window (use ALL healthy windows, same early entry).
# -----------------------------------------------------------------------------
def price_matched_baseline(win_all, traded_mask, bet_up_traded, n_buckets=20):
    """Baseline EV per trade = expected pnl of buying a side at the SAME ask price
    as the signal trades, but with direction chosen by the unconditional base rate
    (not the signal). We pool ALL healthy windows (win_all), form both-side asks,
    bin by ask, and for each signal trade look up the mean pnl of buying a side at
    that ask bucket across the whole pool. This strips the longshot effect."""
    # pool of (ask, won, pnl) for BOTH sides over all healthy windows
    up_ask = win_all.yes_best_ask.values
    up_won = win_all.outcome_up_clean.values
    dn_ask = win_all.no_best_ask.values
    dn_won = 1.0 - win_all.outcome_up_clean.values
    pool_ask = np.concatenate([up_ask, dn_ask])
    pool_won = np.concatenate([up_won, dn_won])
    pool_shares = STAKE / pool_ask
    pool_pnl = pool_shares * pool_won - STAKE - FEE_RATE * pool_ask * (1 - pool_ask) * pool_shares

    edges = np.linspace(0.0, 1.0, n_buckets + 1)
    bucket = np.clip(np.digitize(pool_ask, edges) - 1, 0, n_buckets - 1)
    base_by_bucket = pd.Series(pool_pnl).groupby(bucket).mean()

    # signal trades' asks
    sig = win_all[traded_mask]
    sig_ask = np.where(bet_up_traded == 1, sig.yes_best_ask.values, sig.no_best_ask.values)
    sig_bucket = np.clip(np.digitize(sig_ask, edges) - 1, 0, n_buckets - 1)
    base_pnl = base_by_bucket.reindex(sig_bucket).values
    return base_pnl  # per signal-trade baseline pnl (price-matched, unconditional)


def shuffled_null(win, traded_mask, bet_up, n=2000, seed=0):
    """Permute outcome_up_clean across the traded windows; recompute signal EV.
    Returns the distribution mean-EVs and the one-sided p (real <= shuffled)."""
    sig = win[traded_mask].copy()
    out = sig.outcome_up_clean.values.copy()
    yes_ask = sig.yes_best_ask.values
    no_ask = sig.no_best_ask.values
    bu = bet_up
    # real ev
    won_real = np.where(bu == 1, out, 1 - out)
    ask_real = np.where(bu == 1, yes_ask, no_ask)
    sh = STAKE / ask_real
    pnl_real = sh * won_real - STAKE - FEE_RATE * ask_real * (1 - ask_real) * sh
    real_ev = pnl_real.mean()
    rng = np.random.default_rng(seed)
    evs = np.empty(n)
    for i in range(n):
        perm = rng.permutation(out)
        won = np.where(bu == 1, perm, 1 - perm)
        ask = ask_real  # asks stay with the row; only labels permuted
        s = STAKE / ask
        pnl = s * won - STAKE - FEE_RATE * ask * (1 - ask) * s
        evs[i] = pnl.mean()
    p = float((evs >= real_ev).mean())
    return real_ev, float(evs.mean()), float(np.percentile(evs, 95)), p


# -----------------------------------------------------------------------------
# main
# -----------------------------------------------------------------------------
def main():
    df = load()
    win = build_windows(df)
    print(f"[data] healthy windows with early entry: {len(win)}")
    print(f"[data] consecutive-pair windows: {int(win.consec.sum())}")
    print(win.groupby("split").size().to_dict())

    # base for price-matched baseline = ALL healthy windows with early entry
    win_all = win.copy()

    # define candidate signals. Each returns (traded_mask, bet_up) on `win`.
    # Only act on consecutive pairs (prev features valid). Optionally gate on
    # prev_abs_move strength.
    def momentum(strength_bps):
        m = win.consec & (win.prev_abs_move_bps >= strength_bps) & win.prev_outcome.notna()
        bet = win.prev_outcome.fillna(0).astype(int).values  # same dir as prev
        return m.values, bet

    def reversal(strength_bps):
        m = win.consec & (win.prev_abs_move_bps >= strength_bps) & win.prev_outcome.notna()
        bet = (1 - win.prev_outcome.fillna(0).astype(int)).values  # opposite
        return m.values, bet

    def prev_fav_lost_continue():
        # prior art variant: after favorite LOST, bet the side that won last window
        m = win.consec & (win.prev_fav_won == 0) & win.prev_outcome.notna()
        bet = win.prev_outcome.fillna(0).astype(int).values
        return m.values, bet

    def prev_fav_lost_revert():
        m = win.consec & (win.prev_fav_won == 0) & win.prev_outcome.notna()
        bet = (1 - win.prev_outcome.fillna(0).astype(int)).values
        return m.values, bet

    strengths = [0, 10, 20, 30, 50]
    signals = {}
    for s in strengths:
        signals[f"momentum>={s}bps"] = momentum(s)
        signals[f"reversal>={s}bps"] = reversal(s)
    signals["prevFavLost_continue"] = prev_fav_lost_continue()
    signals["prevFavLost_revert"] = prev_fav_lost_revert()

    print("\n================ FULL-SAMPLE EV per signal (with baselines) ================")
    rows = []
    for name, (mask, bet) in signals.items():
        if mask.sum() < 20:
            continue
        sub = win[mask]
        bet_sub = bet[mask]
        ask, won, pnl = apply_side(sub, bet_sub)
        ev = ev_block(pnl, sub.slug.values, name)
        base = price_matched_baseline(win_all, mask, bet_sub)
        base_ev = np.nanmean(base)
        edge_vs_base = ev["ev"] - base_ev
        wr = won.mean()
        rows.append({
            "signal": name, "n": ev["n"], "wr": round(wr, 4),
            "ev": round(ev["ev"], 4), "p5": round(ev["p5"], 4), "p95": round(ev["p95"], 4),
            "base_ev": round(base_ev, 4), "edge_vs_base": round(edge_vs_base, 4),
        })
    full_tab = pd.DataFrame(rows).sort_values("ev", ascending=False)
    print(full_tab.to_string(index=False))

    # pick the best full-sample candidate by EV that also has positive edge_vs_base
    cand = full_tab[full_tab.edge_vs_base > 0].sort_values("ev", ascending=False)
    if cand.empty:
        print("\n[!] No signal beats its price-matched baseline on full sample.")
        best_name = full_tab.iloc[0]["signal"]
    else:
        best_name = cand.iloc[0]["signal"]
    print(f"\n[pick] best candidate for OOS gate: {best_name}")

    # ---------------- per-split EV for ALL signals (so we see the full picture) ----
    print("\n================ PER-SPLIT EV (ev / p5..p95) ================")
    split_rows = []
    for name, (mask, bet) in signals.items():
        for sp in ["dev", "holdout", "future"]:
            m2 = mask & (win.split == sp).values
            if m2.sum() < 15:
                split_rows.append({"signal": name, "split": sp, "n": int(m2.sum()),
                                   "ev": np.nan, "p5": np.nan, "p95": np.nan})
                continue
            sub = win[m2]; bet_sub = bet[m2]
            _, _, pnl = apply_side(sub, bet_sub)
            eb = ev_block(pnl, sub.slug.values, name)
            split_rows.append({"signal": name, "split": sp, "n": eb["n"],
                               "ev": round(eb["ev"], 4), "p5": round(eb["p5"], 4),
                               "p95": round(eb["p95"], 4)})
    split_tab = pd.DataFrame(split_rows)
    piv = split_tab.pivot(index="signal", columns="split", values="ev")
    piv = piv[["dev", "holdout", "future"]]
    print("EV by split:")
    print(piv.to_string())
    print("\nfuture-split detail (ev, p5, p95, n):")
    fut = split_tab[split_tab.split == "future"].sort_values("ev", ascending=False)
    print(fut[["signal", "n", "ev", "p5", "p95"]].to_string(index=False))

    # ---------------- OOS GATE on the picked candidate ----------------
    print(f"\n================ OOS GATE on '{best_name}' (future split) ================")
    mask, bet = signals[best_name]
    fmask = mask & (win.split == "future").values
    out = {}
    bet_sub = None
    if fmask.sum() >= 10:
        sub = win[fmask]; bet_sub = bet[fmask]
        _, won, pnl = apply_side(sub, bet_sub)
        p5, p50, p95 = window_clustered_bootstrap(pnl, sub.slug.values, seed=SEED)
        fut_ev = float(pnl.mean())
        out = {"fut_ev": fut_ev, "p5": p5, "p50": p50, "p95": p95,
               "n": int(fmask.sum()), "wr": float(won.mean())}
        print(out)
    else:
        print("insufficient future trades")

    # proper future price-matched baseline + shuffled null on the picked candidate
    fut_pool = win_all[win_all.split == "future"].reset_index(drop=True)
    # baseline pool pnl by bucket from future windows
    def fut_base_for(mask_local, bet_local):
        up_ask = fut_pool.yes_best_ask.values; up_won = fut_pool.outcome_up_clean.values
        dn_ask = fut_pool.no_best_ask.values; dn_won = 1 - fut_pool.outcome_up_clean.values
        pool_ask = np.concatenate([up_ask, dn_ask]); pool_won = np.concatenate([up_won, dn_won])
        ps = STAKE / pool_ask
        pool_pnl = ps * pool_won - STAKE - FEE_RATE * pool_ask * (1 - pool_ask) * ps
        edges = np.linspace(0, 1, 21); bk = np.clip(np.digitize(pool_ask, edges) - 1, 0, 19)
        bb = pd.Series(pool_pnl).groupby(bk).mean()
        sig = win[mask_local]
        sa = np.where(bet_local == 1, sig.yes_best_ask.values, sig.no_best_ask.values)
        sbk = np.clip(np.digitize(sa, edges) - 1, 0, 19)
        return bb.reindex(sbk).values

    if fmask.sum() >= 10:
        fb = fut_base_for(fmask, bet_sub)
        fut_base_ev = float(np.nanmean(fb))
        beats_base = bool(out["fut_ev"] > fut_base_ev)
        real_ev, null_mean, null_p95, p_shuf = shuffled_null(win, fmask, bet_sub, n=3000, seed=SEED)
        print(f"future price-matched baseline EV: {fut_base_ev:.4f}")
        print(f"future signal EV: {out['fut_ev']:.4f}  beats_base: {beats_base}")
        print(f"shuffled-null: real_ev={real_ev:.4f} null_mean={null_mean:.4f} "
              f"null_p95={null_p95:.4f} p(real<=null)={p_shuf:.4f}")
        out.update({"fut_base_ev": fut_base_ev, "beats_base": beats_base,
                    "shuf_p": p_shuf})

    # ---------------- also report FULL-sample baseline & null on best ----------------
    print(f"\n================ FULL-sample shuffled null on '{best_name}' ================")
    real_ev_f, null_mean_f, null_p95_f, p_shuf_f = shuffled_null(win, mask, bet[mask], n=3000, seed=SEED)
    print(f"full real_ev={real_ev_f:.4f} null_mean={null_mean_f:.4f} p(real<=null)={p_shuf_f:.4f}")

    # ---------------- robustness: combinatorial purged CV over days (best signal) --
    print(f"\n================ Combinatorial purged CV over days ('{best_name}') ============")
    from research.lib.rigor import combinatorial_purged_cv
    days = sorted(win.date.unique())
    sub_all = win[mask].copy()
    _, _, pnl_all = apply_side(sub_all, bet[mask])
    sub_all = sub_all.assign(_pnl=pnl_all)
    fold_evs = []
    for train_d, test_d in combinatorial_purged_cv(days, n_groups=6, k_test=2, embargo_days=1):
        te = sub_all[sub_all.date.isin(test_d)]
        if len(te) >= 10:
            fold_evs.append(te._pnl.mean())
    fold_evs = np.array(fold_evs)
    print(f"purged-CV test-fold EVs: n_folds={len(fold_evs)} "
          f"mean={fold_evs.mean():.4f} frac>0={np.mean(fold_evs>0):.2f} "
          f"min={fold_evs.min():.4f} max={fold_evs.max():.4f}")

    return full_tab, piv, out, (real_ev_f, p_shuf_f)


if __name__ == "__main__":
    main()
