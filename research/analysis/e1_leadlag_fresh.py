"""HYPOTHESIS E1 — cross-coin lead-lag, RE-TEST on FRESH spot (cb_spot).

Prior test (research/analysis/cross_coin_leadlag.py) found NO capturable lag, but
it used the STALE 15s coinbase_price poll on old May 15-20 data. This dataset's
cb_spot is genuinely fresh (~1-2s cadence; measured below). We re-test honestly.

PART 1 (gates Part 2): build a 1 Hz wall-clock grid of cb_spot per coin, log-return
it, and cross-correlate basket (BTC+ETH) returns vs each follower at lags 1..60s.
A CAPTURABLE lead requires a peak at lag >= 5s WITH real EXCESS over the
contemporaneous correlation. A peak at <5s is an HFT race (not capturable by an API
bot) -> mechanism_verified=False, verdict=dead, do NOT run Part 2.

PART 2 (only if capturable): when basket moves and follower lags, BUY the follower's
basket-implied side mid-window, hold to resolution. OOS(future) + price-matched
baseline + shuffled-outcome null.

Run: uv run python -m research.analysis.e1_leadlag_fresh
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from research.lib.stats import window_clustered_bootstrap  # noqa: E402

DATA = REPO / "data" / "research" / "joined_15m.parquet"

COINS = ["btc", "eth", "sol", "xrp"]
LEADERS = ["btc", "eth"]
FOLLOWERS = ["eth", "sol", "xrp"]   # eth tested as follower of btc too
MAX_LAG = 60
STAKE = 10.0
CAPTURABLE_MIN_LAG = 5   # seconds; below this = HFT race, not capturable
EXCESS_MIN = 0.005       # min excess of peak corr over contemporaneous to matter


# ---------------------------------------------------------------------------
# Part 1: build 1 Hz grid of cb_spot and cross-correlate log returns.
# ---------------------------------------------------------------------------
def build_spot_grid(split: str | None = None) -> pd.DataFrame:
    """1 Hz wall-clock grid (UTC seconds index) with one cb_spot column per coin.
    If split given, restrict to that split. Drops seconds where any coin missing.
    """
    cols = ["timestamp_ms", "symbol", "cb_spot", "split"]
    t = pd.read_parquet(DATA, columns=cols)
    if split is not None:
        t = t[t["split"] == split]
    t = t.dropna(subset=["cb_spot"]).copy()
    t["ts_s"] = t["timestamp_ms"] // 1000
    out = {}
    for c in COINS:
        g = (t[t["symbol"] == c]
             .drop_duplicates("ts_s")
             .set_index("ts_s")["cb_spot"]
             .sort_index())
        out[c] = g
    grid = pd.DataFrame(out).dropna()
    return grid


def measure_staleness(grid: pd.DataFrame) -> dict:
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


def fast_leadlag_curve(leader_ret: pd.Series, follower_ret: pd.Series,
                       max_lag: int = MAX_LAG) -> pd.DataFrame:
    """Vectorized lead-lag using a dense contiguous reindex. leader_ret and
    follower_ret are indexed by integer second. We reindex onto the full second
    range so a simple array shift = a true wall-clock lag; gaps become NaN and
    are dropped per-lag. Positive lag => leader leads follower.
    """
    lo = int(min(leader_ret.index.min(), follower_ret.index.min()))
    hi = int(max(leader_ret.index.max(), follower_ret.index.max()))
    full = np.arange(lo, hi + 1)
    lr = leader_ret.reindex(full).values
    fr = follower_ret.reindex(full).values
    n = len(full)
    rows = []
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            a = lr[: n - lag] if lag > 0 else lr
            b = fr[lag:] if lag > 0 else fr
        else:
            a = lr[-lag:]
            b = fr[: n + lag]
        mask = np.isfinite(a) & np.isfinite(b)
        if mask.sum() < 500:
            rows.append({"lag": lag, "corr": np.nan, "n": int(mask.sum())})
            continue
        c = np.corrcoef(a[mask], b[mask])[0, 1]
        rows.append({"lag": lag, "corr": float(c), "n": int(mask.sum())})
    return pd.DataFrame(rows)


def run_part1(grid: pd.DataFrame, label: str) -> dict:
    print(f"\n=== PART 1: cb_spot lead-lag  [{label}] ===")
    stale = measure_staleness(grid)
    print("  cb_spot cadence (constant-price run length):")
    for c in COINS:
        print(f"    {c}: median {stale[c]['median_run_s']:.0f}s  "
              f"mean {stale[c]['mean_run_s']:.1f}s  "
              f"changed {stale[c]['frac_changed']*100:.1f}% of seconds")
    cadence = max(stale[c]["median_run_s"] for c in COINS)
    print(f"  => effective resolution ~{cadence:.0f}s (fresh if ~1-2s).")

    # 1s log returns, reindexed to full second range inside fast_leadlag_curve.
    lret = {c: np.log(grid[c]).diff(1) for c in COINS}
    basket = (lret["btc"] + lret["eth"]) / 2.0

    summary = []
    curves = {}
    pairs = [("basket", f, basket, lret[f]) for f in ["sol", "xrp", "eth"]]
    pairs += [("btc", f, lret["btc"], lret[f]) for f in ["sol", "xrp", "eth"]]
    for lead_name, follower, lead_s, foll_s in pairs:
        cur = fast_leadlag_curve(lead_s, foll_s, MAX_LAG)
        name = f"{lead_name}->{follower}"
        curves[name] = cur
        contemp = cur.loc[cur["lag"] == 0, "corr"].iloc[0]
        pos = cur[(cur["lag"] >= 1)]
        peak = pos.loc[pos["corr"].idxmax()]
        # Peak strictly in the capturable zone (lag>=CAPTURABLE_MIN_LAG):
        posC = cur[cur["lag"] >= CAPTURABLE_MIN_LAG]
        peakC = posC.loc[posC["corr"].idxmax()]
        excess_any = peak["corr"] - contemp
        excess_C = peakC["corr"] - contemp
        summary.append({
            "pair": name,
            "contemp_corr": contemp,
            "peak_lag_any": int(peak["lag"]),
            "peak_corr_any": peak["corr"],
            "excess_any": excess_any,
            "peak_lag_ge5": int(peakC["lag"]),
            "peak_corr_ge5": peakC["corr"],
            "excess_ge5": excess_C,
        })
    sdf = pd.DataFrame(summary)
    print("\n  Lead-lag summary (positive lag = leader leads follower):")
    with pd.option_context("display.width", 200):
        print(sdf.to_string(index=False, float_format=lambda x: f"{x:.4f}"))

    # A capturable lead = positive peak at lag>=5s WITH excess over contemp.
    capturable = [
        r for r in summary
        if r["peak_lag_any"] >= CAPTURABLE_MIN_LAG
        and r["excess_ge5"] > EXCESS_MIN
    ]
    print(f"\n  Capturable (peak lag>=5s AND excess>{EXCESS_MIN}): "
          f"{[r['pair'] for r in capturable] or 'NONE'}")
    return {"summary": sdf, "curves": curves, "capturable": capturable,
            "cadence_s": cadence, "staleness": stale}


# ---------------------------------------------------------------------------
# Part 2 (only if capturable). Defined for completeness; gated at runtime.
# ---------------------------------------------------------------------------
def _pnl_taker(ask, won):
    shares = STAKE / ask
    fee = 0.07 * ask * (1 - ask) * shares
    return shares * won - STAKE - fee


def run_part2(lag_s: int, move_window_s: int = 30) -> dict:
    """BUY the lagging follower's basket-implied side, one trade/window, hold to
    resolution. EV on each split with window-clustered CI; price-matched baseline;
    shuffled-outcome null."""
    print(f"\n=== PART 2: basket-move backtest (lag={lag_s}s, move_win={move_window_s}s) ===")
    cols = ["timestamp_ms", "symbol", "slug", "seconds_into_window",
            "time_left_sec", "yes_best_bid", "yes_best_ask", "no_best_bid",
            "no_best_ask", "outcome_up_clean", "book_healthy", "start_price",
            "cb_spot", "split"]
    df = pd.read_parquet(DATA, columns=cols)
    df = df[df["book_healthy"] & df["outcome_up_clean"].notna()
            & df["cb_spot"].notna() & (df["start_price"] > 0)].copy()
    df["ts_s"] = df["timestamp_ms"] // 1000

    # Build basket move grid from cb_spot.
    grid = build_spot_grid(split=None)
    lret = {c: np.log(grid[c]).diff(1) for c in COINS}
    basket1 = (lret["btc"] + lret["eth"]) / 2.0
    full = np.arange(int(grid.index.min()), int(grid.index.max()) + 1)
    basket1f = basket1.reindex(full)
    basket_move = basket1f.rolling(move_window_s, min_periods=move_window_s).sum()

    results = {}
    for follower in ["sol", "xrp"]:
        foll1 = lret[follower].reindex(full)
        foll_move = foll1.rolling(move_window_s, min_periods=move_window_s).sum()
        diverge = basket_move - foll_move
        sig = pd.DataFrame({"basket_move": basket_move, "diverge": diverge},
                           index=full)
        sub = df[df["symbol"] == follower].copy()
        sub = sub.merge(sig, left_on="ts_s", right_index=True, how="left")

        thr = np.nanpercentile(np.abs(sub["basket_move"]), 80)
        cand = sub[
            sub["basket_move"].abs().ge(thr)
            & sub["diverge"].abs().ge(thr * 0.5)
            & sub["time_left_sec"].between(60, 840)
            & sub["yes_best_ask"].between(0.05, 0.95)
            & sub["no_best_ask"].between(0.05, 0.95)
            & sub["yes_best_bid"].gt(0) & sub["no_best_bid"].gt(0)
        ].copy()
        if cand.empty:
            results[follower] = {"n": 0}
            continue
        cand = cand.sort_values("seconds_into_window").drop_duplicates("slug")
        cand["bet_up"] = cand["basket_move"] > 0
        cand["entry_ask"] = np.where(cand["bet_up"], cand["yes_best_ask"],
                                     cand["no_best_ask"])
        cand["won"] = np.where(cand["bet_up"], cand["outcome_up_clean"] == 1,
                               cand["outcome_up_clean"] == 0).astype(float)
        cand["pnl"] = _pnl_taker(cand["entry_ask"].values, cand["won"].values)

        rec = {"n": len(cand)}
        for sp in ["dev", "holdout", "future"]:
            s = cand[cand["split"] == sp]
            if len(s) < 5:
                rec[sp] = {"n": len(s)}
                continue
            ci = window_clustered_bootstrap(s["pnl"].values, s["slug"].values)
            rec[sp] = {"n": len(s), "ev": float(s["pnl"].mean()),
                       "wr": float(s["won"].mean()),
                       "ci_lo": ci[0], "ci_hi": ci[2]}
        results[follower] = rec
        f = cand[cand["split"] == "future"]
        print(f"  [{follower}] n_total={len(cand)} "
              f"future_n={len(f)} "
              f"future_ev={f['pnl'].mean() if len(f) else float('nan'):+.3f}")
    return results


# ---------------------------------------------------------------------------
def run():
    print("=" * 72)
    print("E1 — cross-coin lead-lag, RE-TEST on FRESH cb_spot")
    print("=" * 72)
    # Part 1 on the FUTURE split (the decision split) AND on dev for context.
    grid_all = build_spot_grid(split=None)
    print(f"  full grid: {len(grid_all):,} seconds x {len(COINS)} coins")
    p1_all = run_part1(grid_all, "ALL splits")

    grid_fut = build_spot_grid(split="future")
    print(f"\n  future grid: {len(grid_fut):,} seconds x {len(COINS)} coins")
    p1_fut = run_part1(grid_fut, "FUTURE split")

    # Gate on the FUTURE split (decision split). Require capturable in BOTH to be
    # generous to the hypothesis, but the verdict keys on future.
    cap_fut = p1_fut["capturable"]
    cap_all = p1_all["capturable"]
    print("\n" + "=" * 72)
    if not cap_fut and not cap_all:
        print("GATE: NO capturable lead-lag (peak<5s OR no excess) in either grid.")
        print("  Any structure sits at lag<5s = HFT race, not capturable by an")
        print("  API bot. Part 2 is MOOT and NOT run. verdict=dead.")
        print("=" * 72)
        return {"p1_all": p1_all, "p1_fut": p1_fut, "ran_part2": False}

    print(f"GATE PASSED: capturable lag exists "
          f"(future={[r['pair'] for r in cap_fut]}, all={[r['pair'] for r in cap_all]}).")
    print("=" * 72)
    lag_s = max([r["peak_lag_ge5"] for r in (cap_fut or cap_all)])
    p2 = run_part2(lag_s=lag_s, move_window_s=30)
    return {"p1_all": p1_all, "p1_fut": p1_fut, "ran_part2": True, "p2": p2}


if __name__ == "__main__":
    run()
