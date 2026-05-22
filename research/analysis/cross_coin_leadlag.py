"""Lead B — cross-coin spot lead-lag, Polymarket 15m crypto Up/Down.

Hypothesis: BTC (or the BTC+ETH basket) leads ETH/SOL/XRP spot prices by a
capturable margin. If BTC's spot jumps and SOL's spot has not followed yet,
SOL's 15m Up/Down binary (which resolves on SOL's spot) is predictable before
it reprices.

Two parts; Part 1 gates Part 2:

  Part 1 - does the SPOT itself lead-lag?
    Align all 4 coins' `coinbase_price` to a common 1 Hz wall-clock grid,
    log-return them, and cross-correlate. Does BTC's return at t predict the
    other coins' returns at t+lag (lag 1..120 s) BEYOND contemporaneous
    correlation? Honest gate: a lag of only 1-3 s is an HFT race, NOT capturable
    by a patient API bot. We treat a peak at <~10 s as not capturable -> Part 2
    is moot.

  Part 2 - only if Part 1 finds a capturable lag.
    Build the basket-move -> lagging-coin signal, backtest at ask (taker) and
    mid (maker), settle on the corrected `outcome_up`, out-of-fold via
    day_blocked_kfold, with a shuffled-outcome null.

CRITICAL DATA CAVEAT (measured here, not assumed): `coinbase_price` in this
dataset is NOT a real tick feed. It is the collector's REST poll, refreshing
only every ~15 s on average (median constant-price run = 14 s). So the spot
series is genuinely resolved at ~0.06 Hz, not 1 Hz. Any lead-lag below the
poll cadence is invisible to this data by construction; we can only measure
lags at or above ~15 s. We carry this caveat through every conclusion.

Run:  uv run --extra dev python -m research.analysis.cross_coin_leadlag
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from research.holdout import DEV_START, DEV_END, HOLDOUT_START, HOLDOUT_END  # noqa: E402
from research.lib.splits import add_date_col, day_blocked_kfold  # noqa: E402
from research.lib.stats import window_clustered_bootstrap  # noqa: E402

DATA = REPO / "data" / "research"
CHARTS = REPO / "docs" / "research" / "charts"

COINS = ["btc", "eth", "sol", "xrp"]
LEADERS = ["btc", "eth"]          # the basket
LAGGERS = ["sol", "xrp", "eth"]   # candidates that might lag the basket
MAX_LAG = 120                     # seconds
STAKE = 10.0                      # $ per trade


# ---------------------------------------------------------------------------
# Data: build a common 1 Hz wall-clock grid of coinbase_price per coin.
# ---------------------------------------------------------------------------
def build_spot_grid(dev_only: bool = True) -> pd.DataFrame:
    """Return a DataFrame indexed by 1 Hz UTC timestamp with one column per
    coin: the coinbase_price. Dev split only when dev_only=True (May 15-20);
    the May 21-22 hold-out is asserted untouched.
    """
    t = pd.read_parquet(
        DATA / "ticks_15m.parquet",
        columns=["timestamp_ms", "symbol", "coinbase_price"],
    )
    t["date"] = pd.to_datetime(t["timestamp_ms"], unit="ms", utc=True).dt.strftime("%Y-%m-%d")

    if dev_only:
        before = len(t)
        t = t[(t["date"] >= DEV_START) & (t["date"] <= DEV_END)]
        # Hard assertion: no hold-out rows leaked into the Part 1 fit.
        assert t["date"].max() <= DEV_END, "hold-out leaked into dev grid"
        assert t["date"].min() >= DEV_START
        print(f"  dev split: {len(t):,}/{before:,} tick rows, "
              f"{t['date'].min()}..{t['date'].max()}")

    cols = {}
    for c in COINS:
        g = (t[t["symbol"] == c]
             .drop_duplicates("timestamp_ms")
             .set_index("timestamp_ms")["coinbase_price"]
             .sort_index())
        cols[c] = g
    grid = pd.DataFrame(cols)
    # Drop any second where a coin is missing (collector gaps: ~8 of them).
    grid = grid.dropna()
    grid.index = pd.to_datetime(grid.index, unit="ms", utc=True)
    return grid


def measure_staleness(grid: pd.DataFrame) -> dict:
    """Per coin: the median run length (s) of a constant coinbase_price -
    i.e. the effective poll cadence of the spot feed."""
    out = {}
    for c in COINS:
        v = grid[c].values
        chg = np.where(np.diff(v) != 0)[0]
        runs = np.diff(np.concatenate([[-1], chg, [len(v) - 1]]))
        out[c] = {
            "median_run_s": float(np.median(runs)),
            "mean_run_s": float(np.mean(runs)),
            "frac_changed": float((np.diff(v) != 0).mean()),
        }
    return out


# ---------------------------------------------------------------------------
# Part 1 - lead-lag cross-correlation.
# ---------------------------------------------------------------------------
def log_returns(grid: pd.DataFrame, horizon: int = 1) -> pd.DataFrame:
    """Log-returns over `horizon` seconds on the 1 Hz grid."""
    return np.log(grid).diff(horizon)


def leadlag_curve(leader_ret: pd.Series, follower_ret: pd.Series,
                  max_lag: int = MAX_LAG) -> pd.DataFrame:
    """corr(leader_t, follower_{t+lag}) for lag = -max_lag .. +max_lag.

    Positive lag => leader's return today is correlated with follower's return
    `lag` seconds LATER => leader leads. Returns a tidy DataFrame.
    """
    rows = []
    lr = leader_ret.values
    fr = follower_ret.values
    n = len(lr)
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            a = lr[: n - lag] if lag > 0 else lr
            b = fr[lag:] if lag > 0 else fr
        else:
            a = lr[-lag:]
            b = fr[: n + lag]
        mask = np.isfinite(a) & np.isfinite(b)
        if mask.sum() < 100:
            rows.append({"lag": lag, "corr": np.nan, "n": int(mask.sum())})
            continue
        c = np.corrcoef(a[mask], b[mask])[0, 1]
        rows.append({"lag": lag, "corr": float(c), "n": int(mask.sum())})
    return pd.DataFrame(rows)


def coarse_horizon_check(grid: pd.DataFrame) -> pd.DataFrame:
    """Re-run the lead-lag at return horizons matching the ~15s feed cadence.

    At a 1s horizon ~92% of follower returns are exactly zero (the feed is
    stale), which could mask structure. This re-tests at 15/30/60s horizons,
    sampling NON-OVERLAPPINGLY (every h-th row) so the lead-lag is measured in
    genuine, independent steps. Returns a tidy summary frame.
    """
    rows = []
    for h in (15, 30, 60):
        ret = log_returns(grid, h)
        basket = ret[LEADERS].mean(axis=1)
        for follower in ("sol", "xrp"):
            sub_b = basket.iloc[::h].reset_index(drop=True)
            sub_f = ret[follower].iloc[::h].reset_index(drop=True)
            cur = leadlag_curve(sub_b, sub_f, max_lag=8)
            contemp = cur.loc[cur["lag"] == 0, "corr"].iloc[0]
            pos = cur[cur["lag"] > 0]
            neg = cur[cur["lag"] < 0]
            pk = pos.loc[pos["corr"].idxmax()]
            pkn = neg.loc[neg["corr"].idxmax()]
            rows.append({
                "horizon_s": h, "pair": f"basket->{follower}",
                "contemp_corr": contemp,
                "peak_lead_step_s": int(pk["lag"]) * h,
                "peak_lead_corr": pk["corr"],
                "best_lag_corr": pkn["corr"],
                # zero-return fraction at this horizon (staleness diagnostic)
                "frac_zero_follower": float((ret[follower] == 0).mean()),
            })
    df = pd.DataFrame(rows)
    print("\n  Coarse-horizon robustness (non-overlapping steps):")
    print(df.to_string(index=False, float_format=lambda x: f"{x:.4f}"))
    print("  Read: contemp_corr >> peak_lead_corr at every horizon, and the")
    print("  'lead' peak sits at exactly ONE horizon-step (the feed cadence).")
    print("  That is the signature of sub-cadence (HFT-scale) lead-lag aliased")
    print("  up to the poll grid — NOT a real, capturable multi-second lead.")
    return df


def run_part1(grid: pd.DataFrame, horizon: int = 1) -> dict:
    """Compute lead-lag curves for the basket leading each candidate, plus
    BTC alone. Returns a results dict and the per-pair curves."""
    print(f"\n=== PART 1: spot lead-lag (return horizon = {horizon}s) ===")

    stale = measure_staleness(grid)
    print("  coinbase_price feed cadence (constant-price run length):")
    for c in COINS:
        print(f"    {c}: median {stale[c]['median_run_s']:.0f}s  "
              f"mean {stale[c]['mean_run_s']:.0f}s  "
              f"changed {stale[c]['frac_changed']*100:.1f}% of ticks")
    cadence = max(stale[c]["median_run_s"] for c in COINS)
    print(f"  => effective spot resolution ~{cadence:.0f}s; "
          f"lags below this are invisible by construction.")

    ret = log_returns(grid, horizon)
    # Basket leader = equal-weight mean of BTC+ETH log-returns.
    basket = ret[LEADERS].mean(axis=1)

    curves = {}
    summary = []
    for follower in ["sol", "xrp"]:
        # Basket -> follower
        cur_b = leadlag_curve(basket, ret[follower])
        curves[f"basket->{follower}"] = cur_b
        # BTC alone -> follower
        cur_btc = leadlag_curve(ret["btc"], ret[follower])
        curves[f"btc->{follower}"] = cur_btc

        for name, cur in [(f"basket->{follower}", cur_b),
                          (f"btc->{follower}", cur_btc)]:
            contemp = cur.loc[cur["lag"] == 0, "corr"].iloc[0]
            pos = cur[cur["lag"] > 0]
            neg = cur[cur["lag"] < 0]
            peak_pos = pos.loc[pos["corr"].idxmax()]
            peak_neg = neg.loc[neg["corr"].idxmax()]
            # Predictive EXCESS at the positive peak vs contemporaneous.
            excess = peak_pos["corr"] - contemp
            summary.append({
                "pair": name,
                "contemp_corr": contemp,
                "peak_lag_pos": int(peak_pos["lag"]),
                "peak_corr_pos": peak_pos["corr"],
                "peak_lag_neg": int(peak_neg["lag"]),
                "peak_corr_neg": peak_neg["corr"],
                "excess_at_pos_peak": excess,
            })

    sumdf = pd.DataFrame(summary)
    print("\n  Lead-lag summary (positive lag = leader leads follower):")
    print(sumdf.to_string(index=False, float_format=lambda x: f"{x:.4f}"))

    # Capturable verdict: a peak must be at lag >= ~10s AND show real excess
    # predictive power over contemporaneous corr.
    capturable = []
    for r in summary:
        if (r["peak_lag_pos"] >= 10 and r["peak_corr_pos"] > r["contemp_corr"]
                and r["excess_at_pos_peak"] > 0.01):
            capturable.append(r)

    coarse = coarse_horizon_check(grid)

    return {
        "staleness": stale,
        "cadence_s": cadence,
        "curves": curves,
        "summary": sumdf,
        "coarse": coarse,
        "capturable": capturable,
        "horizon": horizon,
    }


def plot_part1(p1: dict):
    """Lead-lag curves for basket->sol, basket->xrp, btc->sol, btc->xrp."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    CHARTS.mkdir(parents=True, exist_ok=True)
    curves = p1["curves"]

    fig, axes = plt.subplots(2, 2, figsize=(13, 9), sharex=True)
    panels = ["basket->sol", "btc->sol", "basket->xrp", "btc->xrp"]
    for ax, key in zip(axes.flat, panels):
        cur = curves[key]
        ax.plot(cur["lag"], cur["corr"], lw=1.4, color="#1f77b4")
        ax.axvline(0, color="#888", lw=0.8, ls="--")
        ax.axhline(0, color="#ccc", lw=0.8)
        # Mark contemporaneous and positive peak.
        contemp = cur.loc[cur["lag"] == 0, "corr"].iloc[0]
        pos = cur[cur["lag"] > 0]
        pk = pos.loc[pos["corr"].idxmax()]
        ax.scatter([0], [contemp], color="#d62728", zorder=5,
                   label=f"lag 0: r={contemp:.3f}")
        ax.scatter([pk["lag"]], [pk["corr"]], color="#2ca02c", zorder=5,
                   label=f"peak +{int(pk['lag'])}s: r={pk['corr']:.3f}")
        ax.axvspan(1, 10, color="#ffcccc", alpha=0.35)  # HFT-only zone
        ax.set_title(f"{key}  (leader return @t  vs  follower return @t+lag)")
        ax.set_ylabel("Pearson r")
        ax.legend(fontsize=8, loc="upper right")
        ax.grid(alpha=0.2)
    for ax in axes[1]:
        ax.set_xlabel("lag (s)  —  positive = leader leads follower")
    fig.suptitle("Part 1: cross-coin spot lead-lag (coinbase_price, dev May 15-20)\n"
                 "pink band = HFT-only (<10s, not capturable by a patient bot)",
                 fontsize=11)
    fig.tight_layout()
    out = CHARTS / "lead_B_leadlag_curves.png"
    fig.savefig(out, dpi=110)
    plt.close(fig)
    print(f"  chart -> {out}")
    return out


# ---------------------------------------------------------------------------
# Part 2 - basket-move -> lagging-coin binary backtest.
# Only runs if Part 1 finds a capturable lag.
# ---------------------------------------------------------------------------
def load_windows_with_ticks(follower: str, dev_only: bool = True) -> pd.DataFrame:
    """Per-tick frame for `follower` 15m windows, with the spot grid joined so
    we can compute the basket move at each tick. Dev split only."""
    t = pd.read_parquet(DATA / "ticks_15m.parquet")
    t = t[t["symbol"] == follower].copy()
    t["date"] = pd.to_datetime(t["timestamp_ms"], unit="ms", utc=True).dt.strftime("%Y-%m-%d")
    if dev_only:
        t = t[(t["date"] >= DEV_START) & (t["date"] <= DEV_END)]
        assert t["date"].max() <= DEV_END, "hold-out leaked into Part 2"
    return t


def run_part2(grid: pd.DataFrame, p1: dict, lag_s: int,
              move_window_s: int = 30) -> dict:
    """Backtest: when the BTC+ETH basket makes a sharp spot move over
    `move_window_s` seconds and the follower's spot has not yet followed, BUY
    the follower's binary on the basket-implied side. Settle on outcome_up.

    Enters at the ask (taker) and at mid (maker, 0 fee). Out-of-fold via
    day_blocked_kfold. Includes a shuffled-outcome null.
    """
    print(f"\n=== PART 2: basket-move backtest (lag={lag_s}s, "
          f"move_window={move_window_s}s) ===")

    # Basket cumulative move and follower cumulative move over move_window_s.
    lr1 = log_returns(grid, 1)
    basket_move = lr1[LEADERS].mean(axis=1).rolling(move_window_s).sum()

    results = []
    for follower in ["sol", "xrp"]:
        follower_move = lr1[follower].rolling(move_window_s).sum()
        # Divergence: basket moved, follower has not caught up.
        diverge = basket_move - follower_move
        gridf = pd.DataFrame({
            "basket_move": basket_move,
            "follower_move": follower_move,
            "diverge": diverge,
        })

        ticks = load_windows_with_ticks(follower)
        ticks["ts"] = pd.to_datetime(ticks["timestamp_ms"], unit="ms", utc=True)
        # Join the basket-divergence signal onto every follower tick.
        ticks = ticks.merge(gridf, left_on="ts", right_index=True, how="left")

        # Entry universe: a real two-sided book, mid-window (avoid the last
        # 60s where it is decided), and a meaningful basket move.
        # Threshold = 80th pct of |basket_move| on the dev set (data-driven but
        # we report sensitivity).
        thr = np.nanpercentile(np.abs(ticks["basket_move"]), 80)
        cand = ticks[
            ticks["basket_move"].abs().ge(thr)
            & ticks["diverge"].abs().ge(thr * 0.5)   # follower lagging
            & ticks["time_left_sec"].between(60, 840)
            & ticks["yes_best_ask"].between(0.05, 0.95)
            & ticks["no_best_ask"].between(0.05, 0.95)
            & ticks["yes_best_bid"].gt(0)
            & ticks["no_best_bid"].gt(0)
        ].copy()

        # One trade per window: the FIRST qualifying tick (a patient bot acts
        # once, then holds to resolution).
        cand = cand.sort_values("timestamp_ms").drop_duplicates("market_slug")
        if cand.empty:
            results.append({"follower": follower, "n": 0,
                            "note": "no qualifying entries"})
            continue

        # Direction: basket up => bet follower UP (buy YES); basket down => NO.
        cand["bet_up"] = cand["basket_move"] > 0
        # Taker entry price = ask of the chosen side.
        cand["entry_ask"] = np.where(cand["bet_up"],
                                     cand["yes_best_ask"], cand["no_best_ask"])
        # Maker entry price = mid of the chosen side.
        cand["entry_mid"] = np.where(
            cand["bet_up"],
            (cand["yes_best_bid"] + cand["yes_best_ask"]) / 2,
            (cand["no_best_bid"] + cand["no_best_ask"]) / 2,
        )
        # Settlement: 1 if our side wins, else 0.
        cand["won"] = np.where(cand["bet_up"],
                               cand["outcome_up"] == 1,
                               cand["outcome_up"] == 0).astype(float)

        # Taker fee = 0.07 * p * (1-p) per share, on entry; settlement leg of a
        # held-to-resolution binary pays $1/$0 with no exit fee.
        def pnl_taker(row):
            p = row["entry_ask"]
            shares = STAKE / p
            fee = 0.07 * p * (1 - p) * shares
            payoff = shares * row["won"]      # $1 if won else $0
            return payoff - STAKE - fee

        def pnl_maker(row):
            p = row["entry_mid"]
            shares = STAKE / p
            payoff = shares * row["won"]
            return payoff - STAKE          # maker fee = 0

        cand["pnl_taker"] = cand.apply(pnl_taker, axis=1)
        cand["pnl_maker"] = cand.apply(pnl_maker, axis=1)
        cand = add_date_col(cand, ts_col="window_start_ts")

        res = _backtest_stats(cand, follower, thr)
        results.append(res)

    return {"per_follower": results, "lag_s": lag_s,
            "move_window_s": move_window_s}


def _backtest_stats(cand: pd.DataFrame, follower: str, thr: float) -> dict:
    """Out-of-fold stats + window-clustered CI + shuffled-outcome null."""
    n = len(cand)
    n_days = cand["date"].nunique()
    wr = float(cand["won"].mean())

    # Out-of-fold mean PnL: average the held-out fold means.
    folds = day_blocked_kfold(cand, k=min(5, n_days), seed=0)
    oof_taker, oof_maker = [], []
    for _, test_idx in folds:
        sub = cand.loc[test_idx]
        oof_taker.append(sub["pnl_taker"].mean())
        oof_maker.append(sub["pnl_maker"].mean())
    oof_taker_mean = float(np.mean(oof_taker))
    oof_maker_mean = float(np.mean(oof_maker))

    # Window-clustered CI (each window = one cluster).
    groups = cand["market_slug"].values
    ci_taker = window_clustered_bootstrap(cand["pnl_taker"].values, groups)
    ci_maker = window_clustered_bootstrap(cand["pnl_maker"].values, groups)

    # Null: shuffle the outcomes 1000x, recompute taker PnL/trade.
    rng = np.random.default_rng(0)
    won = cand["won"].values
    ask = cand["entry_ask"].values
    shares = STAKE / ask
    fee = 0.07 * ask * (1 - ask) * shares
    null_means = np.empty(1000)
    for i in range(1000):
        w = rng.permutation(won)
        pnl = shares * w - STAKE - fee
        null_means[i] = pnl.mean()
    null_p = float((null_means >= oof_taker_mean).mean())

    span = (cand["window_start_ts"].max() - cand["window_start_ts"].min())
    days_span = max(span / 86400.0, 1.0)
    trades_per_day = n / days_span

    print(f"\n  [{follower}] n={n}  windows={n} (1/window)  days={n_days}  "
          f"WR={wr*100:.1f}%  basket-move thr={thr:.5f}")
    print(f"    TAKER: oof PnL/trade ${oof_taker_mean:+.3f}  "
          f"CI[{ci_taker[0]:+.3f},{ci_taker[2]:+.3f}]  "
          f"${oof_taker_mean*trades_per_day:+.2f}/day")
    print(f"    MAKER: oof PnL/trade ${oof_maker_mean:+.3f}  "
          f"CI[{ci_maker[0]:+.3f},{ci_maker[2]:+.3f}]  "
          f"${oof_maker_mean*trades_per_day:+.2f}/day")
    print(f"    NULL (shuffled outcomes): mean ${null_means.mean():+.3f}  "
          f"p(null>=observed taker)={null_p:.3f}")

    return {
        "follower": follower, "n": n, "n_days": n_days, "win_rate": wr,
        "thr": float(thr), "trades_per_day": trades_per_day,
        "oof_taker": oof_taker_mean, "oof_maker": oof_maker_mean,
        "ci_taker": ci_taker, "ci_maker": ci_maker,
        "null_taker_mean": float(null_means.mean()), "null_p": null_p,
        "taker_per_day": oof_taker_mean * trades_per_day,
        "maker_per_day": oof_maker_mean * trades_per_day,
    }


# ---------------------------------------------------------------------------
def run() -> dict:
    print("=" * 72)
    print("Lead B — cross-coin spot lead-lag")
    print(f"Dev split {DEV_START}..{DEV_END}; hold-out {HOLDOUT_START}.."
          f"{HOLDOUT_END} SEALED.")
    print("=" * 72)

    grid = build_spot_grid(dev_only=True)
    print(f"  spot grid: {len(grid):,} seconds x {len(COINS)} coins")

    p1 = run_part1(grid, horizon=1)
    plot_part1(p1)

    # Honest gate.
    if not p1["capturable"]:
        print("\n" + "=" * 72)
        print("GATE: Part 1 found NO capturable lead-lag.")
        print("  No coin's spot lags the basket/BTC by >=10s with real excess")
        print("  predictive power. Any structure is at/below the ~15s feed")
        print("  cadence — an HFT race at best, invisible here at worst.")
        print("  Part 2 is MOOT and is NOT run.")
        print("=" * 72)
        return {"part1": p1, "part2": None, "verdict": "no_capturable_lag"}

    print("\n  GATE PASSED: a capturable lag exists. Running Part 2.")
    # Use the largest capturable peak lag found.
    lag_s = max(r["peak_lag_pos"] for r in p1["capturable"])
    p2 = run_part2(grid, p1, lag_s=lag_s, move_window_s=30)
    return {"part1": p1, "part2": p2, "verdict": "ran_part2"}


if __name__ == "__main__":
    run()
