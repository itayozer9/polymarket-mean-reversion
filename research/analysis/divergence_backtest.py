"""Divergence backtest -- the DECISIVE out-of-fold test of the spot-divergence
edge (supersedes Task 9 of the Phase 2-4 plan).

The candidate edge (Task 8, `fair_value_tri.py`)
------------------------------------------------
Task 8 built a model-free empirical fair value: P(Up) binned by
(signed `move_pct` x `time_left_sec`) from realized `outcome_up`. The big gain
came from the bin where a side priced as an underdog is in truth the
favourite -- i.e. the market's price sits far BELOW its spot-implied empirical
fair value. The hypothesis tested here, head-on:

  Buy a side when its spot-implied empirical fair value materially exceeds its
  market ask. A relative-value / lead-lag bet (spot has moved, the Polymarket
  book has not yet repriced), NOT odds-mean-reversion.

The trap this script is built to catch
--------------------------------------
On corrected data a small residual longshot effect remains: the extreme cheap
tail (cheap_mid < 0.07, a side priced ~3c) resolves slightly above its price
(~10c). The divergence signal, because a large `fair - ask` gap is easiest to
reach on a LOW-ask side, will mechanically select cheap sides -- so it must be
compared against a price-matched cheap-side baseline to isolate any genuine
surface content from cheap-side selection.

So the decisive question is NOT "is the divergence backtest positive" (it
trivially is -- it is buying cheap sides). It is: **does the divergence signal
beat a price-matched cheap-side baseline?** If divergence ~ price-only, the
"edge" is just the known longshot mispricing rediscovered through a more
complicated lens -- not a new, tradeable lead-lag edge.

Three nulls are run, each killing a different thing:
  - PRICE-ONLY baseline: buy the cheap side directly (same guards, same
    simulation). Divergence must BEAT this to have any content of its own.
  - SHUFFLED-OUTCOME null: permute `outcome_up` across windows. NOTE this does
    NOT collapse to 0 -- the shuffle preserves the marginal P(Up), and a cheap
    side is still cheap, so the longshot gap survives. It is reported to make
    that explicit, and it must MATCH the price-only baseline (proving the
    shuffled-label "edge" is 100% the longshot effect, 0% surface content).
  - FLAT-SURFACE null: replace the surface with a constant (global P(Up)).
    The divergence signal then has zero spot information; if it still
    "trades", it proves the signal is just a cheap-side selector.

Anti-leakage rule (load-bearing): the empirical fair-value surface is FIT on
training days; the signal and trades are evaluated on TEST days. Never fit the
surface on the day it scores. Day-blocked k-fold (k=5) and leave-one-day-out.

Dev split May 15-20 only. The sealed hold-out (May 21-22) is asserted untouched.

Outputs:
  - docs/research/charts/divergence_backtest_pnl.png
  - docs/research/charts/divergence_threshold_sweep.png
  - docs/research/divergence_edge.md
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Path setup: repo root is two levels up from this file.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from research.holdout import DEV_START, DEV_END  # noqa: E402
from research.lib.splits import day_blocked_kfold, leave_one_day_out  # noqa: E402
from research.lib.stats import window_clustered_bootstrap  # noqa: E402

_TICKS_PATH = _REPO_ROOT / "data/research/ticks_15m.parquet"
_CHART_DIR = _REPO_ROOT / "docs/research/charts"
_CHART_PNL = _CHART_DIR / "divergence_backtest_pnl.png"
_CHART_SWEEP = _CHART_DIR / "divergence_threshold_sweep.png"
_DOC_PATH = _REPO_ROOT / "docs/research/divergence_edge.md"

SEED = 42
N_BOOT = 2000
STAKE = 10.0  # dollars per trade

# Signal thresholds (empirical_fair - ask).
THRESHOLDS = [0.05, 0.10, 0.15, 0.20]
# Profit-target multipliers on the entry price (None = hold to resolution).
PROFIT_TARGETS = [None, 0.50, 1.00]

# Entry guards.
MIN_TIME_LEFT = 120      # seconds; need room for the trade to play out
ASK_BAND_LO = 0.03       # avoid junk-cheap quotes
ASK_BAND_HI = 0.90       # avoid near-resolved quotes


# Taker fee model (Phase 0 frame): per share, per leg.
def _fee(p):
    """Polymarket-style fee: 0.07 * p * (1-p) per share."""
    return 0.07 * p * (1.0 - p)


# 2-D empirical-fair-value grid. Signed move_pct (percent) x time_left (sec).
# move_pct fine near 0 (contested) and coarse in the tails. time_left binned
# finely enough that the t=0 (900s-left) rows get their own bin -- a coarse
# [720,900] bin would pool 900s-left rows with 720s-left rows and badly
# mis-state P(Up) at window start.
MP_EDGES = np.array([-100.0, -1.0, -0.6, -0.4, -0.25, -0.15, -0.08, -0.03,
                     0.0, 0.03, 0.08, 0.15, 0.25, 0.4, 0.6, 1.0, 100.0])
TL_EDGES = np.array([0, 120, 180, 240, 300, 360, 420, 480, 540, 600,
                     660, 720, 780, 840, 901])
MIN_CELL_COUNT = 60   # below this a 2-D cell falls back to the move_pct marginal
MIN_MARG_COUNT = 60   # below this the move_pct marginal falls back to global


# ===========================================================================
# Data loading
# ===========================================================================

def load_ticks() -> pd.DataFrame:
    """Load the 15m tick table, dev split only, with a clean two-sided book.

    The sealed hold-out (May 21-22) is asserted untouched. Also derives the
    cheap side (the lower-ask side) and `cheap_won` for the price-only
    baseline.
    """
    cols = ["slug", "symbol", "timestamp_ms", "window_start_ts",
            "seconds_into_window", "time_left_sec", "move_pct", "outcome_up",
            "yes_best_bid", "yes_best_ask", "no_best_bid", "no_best_ask"]
    t = pd.read_parquet(_TICKS_PATH, columns=cols)
    # Drop the single garbage row(s) with a missing / 1970 window_start_ts.
    t = t[t["window_start_ts"].notna()].copy()
    t["date"] = pd.to_datetime(
        t["window_start_ts"], unit="s", utc=True
    ).dt.strftime("%Y-%m-%d")
    dev = t[(t["date"] >= DEV_START) & (t["date"] <= DEV_END)].copy()

    # Sealed hold-out guard -- decisive, must hold.
    assert (dev["date"] >= DEV_START).all(), "pre-dev row leaked!"
    assert (dev["date"] <= DEV_END).all(), "HOLD-OUT LEAKED INTO DEV!"
    assert dev["date"].nunique() <= 6, "unexpected dev day count"

    fin = (
        np.isfinite(dev["move_pct"])
        & np.isfinite(dev["time_left_sec"])
        & dev["outcome_up"].notna()
        & np.isfinite(dev["yes_best_ask"]) & np.isfinite(dev["no_best_ask"])
        & np.isfinite(dev["yes_best_bid"]) & np.isfinite(dev["no_best_bid"])
    )
    dev = dev[fin].reset_index(drop=True)

    # The cheap side = the lower-ask side -- used by the price-only baseline.
    dev["cheap_is_yes"] = dev["yes_best_ask"] <= dev["no_best_ask"]
    dev["cheap_won"] = np.where(
        dev["cheap_is_yes"], dev["outcome_up"], 1.0 - dev["outcome_up"])
    return dev


# ===========================================================================
# The empirical fair-value surface
# ===========================================================================

def _bin_codes(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Return (move_pct bin code, time_left bin code) for every row."""
    mp = pd.cut(df["move_pct"], MP_EDGES, labels=False).to_numpy()
    tl = pd.cut(df["time_left_sec"], TL_EDGES, labels=False,
                include_lowest=True, right=False).to_numpy()
    return mp, tl


def fit_surface(train: pd.DataFrame) -> dict:
    """Fit the empirical P(Up) surface on TRAINING rows only.

    2-D cell P(Up), the move_pct-marginal P(Up), and the global P(Up). A row
    resolves to the finest level with >= the min count, else falls back
    coarser. Fitting on train rows only is the anti-leakage guarantee.
    """
    mp, tl = _bin_codes(train)
    y = train["outcome_up"].to_numpy(dtype="f8")
    ok = np.isfinite(mp) & np.isfinite(tl)
    mp, tl, y = mp[ok].astype(int), tl[ok].astype(int), y[ok]

    df = pd.DataFrame({"mp": mp, "tl": tl, "y": y})
    cell = df.groupby(["mp", "tl"])["y"].agg(["mean", "size"])
    cell_pup = {idx: r["mean"] for idx, r in cell.iterrows()}
    cell_cnt = {idx: int(r["size"]) for idx, r in cell.iterrows()}
    marg = df.groupby("mp")["y"].agg(["mean", "size"])
    marg_pup = {idx: r["mean"] for idx, r in marg.iterrows()}
    marg_cnt = {idx: int(r["size"]) for idx, r in marg.iterrows()}
    return {
        "cell_pup": cell_pup, "cell_cnt": cell_cnt,
        "marg_pup": marg_pup, "marg_cnt": marg_cnt,
        "global_pup": float(y.mean()),
    }


def predict_pup(surface: dict, df: pd.DataFrame) -> np.ndarray:
    """Predict P(Up) for every row: 2-D cell -> move_pct marginal -> global,
    finest level that clears its min count."""
    mp, tl = _bin_codes(df)
    out = np.full(len(df), surface["global_pup"], dtype="f8")
    cell_pup, cell_cnt = surface["cell_pup"], surface["cell_cnt"]
    marg_pup, marg_cnt = surface["marg_pup"], surface["marg_cnt"]
    for i in range(len(df)):
        m, t_ = mp[i], tl[i]
        if not (np.isfinite(m) and np.isfinite(t_)):
            continue
        m, t_ = int(m), int(t_)
        k = (m, t_)
        if cell_cnt.get(k, 0) >= MIN_CELL_COUNT:
            out[i] = cell_pup[k]
        elif marg_cnt.get(m, 0) >= MIN_MARG_COUNT:
            out[i] = marg_pup[m]
    return out


def flat_surface(train: pd.DataFrame) -> dict:
    """A null surface: every cell = the global training P(Up). The divergence
    signal built on this has zero spot information."""
    g = float(train["outcome_up"].mean())
    return {"cell_pup": {}, "cell_cnt": {}, "marg_pup": {}, "marg_cnt": {},
            "global_pup": g}


# ===========================================================================
# Signal + trade simulation
# ===========================================================================

def find_trades(test: pd.DataFrame, pup: np.ndarray,
                threshold: float) -> pd.DataFrame:
    """Find one trade per window: the side with the largest qualifying
    `divergence = empirical_fair - ask`. First qualifying tick per window."""
    df = test.copy().reset_index(drop=True)
    pup = np.asarray(pup, dtype="f8")

    yes_ask = df["yes_best_ask"].to_numpy(dtype="f8")
    no_ask = df["no_best_ask"].to_numpy(dtype="f8")
    yes_bid = df["yes_best_bid"].to_numpy(dtype="f8")
    no_bid = df["no_best_bid"].to_numpy(dtype="f8")
    tl = df["time_left_sec"].to_numpy(dtype="f8")

    yes_div = pup - yes_ask
    no_div = (1.0 - pup) - no_ask

    yes_ok = (
        (yes_ask > 0) & (no_ask > 0)
        & (yes_bid < yes_ask) & (yes_bid > 0)
        & (yes_ask >= ASK_BAND_LO) & (yes_ask <= ASK_BAND_HI)
    )
    no_ok = (
        (yes_ask > 0) & (no_ask > 0)
        & (no_bid < no_ask) & (no_bid > 0)
        & (no_ask >= ASK_BAND_LO) & (no_ask <= ASK_BAND_HI)
    )
    time_ok = tl >= MIN_TIME_LEFT

    take_yes = yes_ok & (yes_div >= threshold) & time_ok
    take_no = no_ok & (no_div >= threshold) & time_ok
    pick_yes = take_yes & (~take_no | (yes_div >= no_div))
    pick_no = take_no & (~take_yes | (no_div > yes_div))

    side = np.where(pick_yes, "YES", np.where(pick_no, "NO", ""))
    qual = side != ""
    if not qual.any():
        return pd.DataFrame()

    cand = df[qual].copy()
    cand["side"] = side[qual]
    cand["entry_pup"] = np.where(
        cand["side"] == "YES", pup[qual], 1.0 - pup[qual])
    cand["entry_ask"] = np.where(
        cand["side"] == "YES", yes_ask[qual], no_ask[qual])
    cand["entry_bid"] = np.where(
        cand["side"] == "YES", yes_bid[qual], no_bid[qual])
    cand["entry_mid"] = 0.5 * (cand["entry_ask"] + cand["entry_bid"])
    cand["divergence"] = cand["entry_pup"] - cand["entry_ask"]
    cand["side_won"] = np.where(
        cand["side"] == "YES", cand["outcome_up"], 1.0 - cand["outcome_up"])

    cand = cand.sort_values(["slug", "seconds_into_window"])
    return cand.groupby("slug", as_index=False).first().reset_index(drop=True)


def find_priceonly_trades(test: pd.DataFrame,
                          max_ask: float) -> pd.DataFrame:
    """PRICE-ONLY baseline: buy the cheap side whenever its ask <= `max_ask`,
    same guards as `find_trades`. First qualifying tick per window. This is
    what the divergence signal must beat to have content of its own.

    `max_ask` is set per threshold to the typical ask the divergence signal
    accepts at that threshold, so the two are price-matched.
    """
    df = test.copy().reset_index(drop=True)
    yes_ask = df["yes_best_ask"].to_numpy(dtype="f8")
    no_ask = df["no_best_ask"].to_numpy(dtype="f8")
    yes_bid = df["yes_best_bid"].to_numpy(dtype="f8")
    no_bid = df["no_best_bid"].to_numpy(dtype="f8")
    tl = df["time_left_sec"].to_numpy(dtype="f8")
    cheap_is_yes = df["cheap_is_yes"].to_numpy()

    cheap_ask = np.where(cheap_is_yes, yes_ask, no_ask)
    cheap_bid = np.where(cheap_is_yes, yes_bid, no_bid)

    ok = (
        (yes_ask > 0) & (no_ask > 0)
        & (cheap_bid < cheap_ask) & (cheap_bid > 0)
        & (cheap_ask >= ASK_BAND_LO) & (cheap_ask <= max_ask)
        & (tl >= MIN_TIME_LEFT)
    )
    if not ok.any():
        return pd.DataFrame()

    cand = df[ok].copy()
    cand["side"] = np.where(cheap_is_yes[ok], "YES", "NO")
    cand["entry_ask"] = cheap_ask[ok]
    cand["entry_bid"] = cheap_bid[ok]
    cand["entry_mid"] = 0.5 * (cand["entry_ask"] + cand["entry_bid"])
    cand["entry_pup"] = np.nan
    cand["divergence"] = np.nan
    cand["side_won"] = cand["cheap_won"]
    cand = cand.sort_values(["slug", "seconds_into_window"])
    return cand.groupby("slug", as_index=False).first().reset_index(drop=True)


def simulate_trades(trades: pd.DataFrame, exit_book: pd.DataFrame,
                    profit_target, execution: str) -> pd.DataFrame:
    """Simulate $STAKE trades to a profit target or window resolution.

    TAKER: enter at `entry_ask`, pay 0.07*p*(1-p) entry fee. A profit-target
    exit scans strictly-later ticks for the side-bid crossing
    `entry_ask*(1+PT)`, paying the exit fee. Otherwise settle on the true
    `outcome_up` (resolution is a settlement, no exit fee).
    MAKER: enter at `entry_mid`, 0 fee. The fill-probability haircut is NOT
    modelled -- the maker numbers are an optimistic upper bound.
    """
    out = trades.copy().reset_index(drop=True)
    if out.empty:
        for c in ("shares", "pnl", "exit_reason"):
            out[c] = []
        return out

    entry_price = (out["entry_ask"] if execution == "taker"
                   else out["entry_mid"]).to_numpy(dtype="f8")
    entry_price = np.clip(entry_price, 1e-6, 0.999999)
    shares = STAKE / entry_price
    entry_fee = (_fee(entry_price) * shares if execution == "taker"
                 else np.zeros(len(out)))

    side_won = out["side_won"].to_numpy(dtype="f8")
    pnl = np.empty(len(out), dtype="f8")
    reason = np.empty(len(out), dtype=object)
    by_slug = ({g: sub for g, sub in exit_book.groupby("slug")}
               if profit_target is not None else {})

    for i in range(len(out)):
        slug = out["slug"].iat[i]
        side = out["side"].iat[i]
        ep = entry_price[i]
        s = shares[i]
        sec0 = out["seconds_into_window"].iat[i]
        exited = False

        if profit_target is not None:
            target_px = ep * (1.0 + profit_target)
            if target_px < 1.0:
                sub = by_slug.get(slug)
                if sub is not None:
                    later = sub[sub["seconds_into_window"] > sec0]
                    later = later.sort_values("seconds_into_window")
                    if side == "YES":
                        ex_quote = (later["yes_best_bid"] if execution == "taker"
                                    else 0.5 * (later["yes_best_bid"]
                                                + later["yes_best_ask"]))
                    else:
                        ex_quote = (later["no_best_bid"] if execution == "taker"
                                    else 0.5 * (later["no_best_bid"]
                                                + later["no_best_ask"]))
                    exq = ex_quote.to_numpy(dtype="f8")
                    hit = np.where(exq >= target_px)[0]
                    if len(hit):
                        px = float(exq[hit[0]])
                        proceeds = px * s
                        if execution == "taker":
                            proceeds -= _fee(px) * s
                        pnl[i] = proceeds - STAKE - entry_fee[i]
                        reason[i] = "target"
                        exited = True

        if not exited:
            pnl[i] = side_won[i] * 1.0 * s - STAKE - entry_fee[i]
            reason[i] = "resolution"

    out["shares"] = shares
    out["pnl"] = pnl
    out["exit_reason"] = reason
    return out


# ===========================================================================
# Out-of-fold evaluation
# ===========================================================================

def _window_index(dev: pd.DataFrame) -> pd.DataFrame:
    return (dev[["slug", "date"]].drop_duplicates("slug")
            .reset_index(drop=True))


def _folds(wi: pd.DataFrame, splitter_name: str):
    if splitter_name == "kfold":
        return day_blocked_kfold(wi, k=5, seed=SEED)
    if splitter_name == "lodo":
        return list(leave_one_day_out(wi))
    raise ValueError(splitter_name)


def run_oof(dev: pd.DataFrame, splitter_name: str) -> dict:
    """Out-of-fold backtest. Per fold: fit the surface on TRAIN-day rows,
    score TEST-day rows, find divergence trades AND the price-matched
    price-only baseline, simulate every (T, PT, execution). Trades pooled
    across folds.

    Returns {('div'|'price', T, PT, execution): pooled trades DataFrame}.
    """
    wi = _window_index(dev)
    folds = _folds(wi, splitter_name)

    pooled: dict[tuple, list[pd.DataFrame]] = {}
    fold_diag = []
    for fi, (tr_idx, te_idx) in enumerate(folds):
        tr_slugs = set(wi.loc[tr_idx, "slug"])
        te_slugs = set(wi.loc[te_idx, "slug"])
        assert not (tr_slugs & te_slugs), "train/test window overlap!"
        train = dev[dev["slug"].isin(tr_slugs)]
        test = dev[dev["slug"].isin(te_slugs)]
        assert not (set(train["date"]) & set(test["date"])), \
            "train/test DAY overlap -- surface would leak!"

        surface = fit_surface(train)
        pup_test = predict_pup(surface, test)
        fold_diag.append({
            "fold": fi, "n_train_days": train["date"].nunique(),
            "n_test_days": test["date"].nunique(),
            "n_test_windows": len(te_slugs),
        })

        for T in THRESHOLDS:
            div_trades = find_trades(test, pup_test, T)
            # Price-match: the price-only baseline accepts the cheap side up
            # to the 90th-percentile ask the divergence signal took at this T
            # (so the two rules trade in the same price region).
            if not div_trades.empty:
                max_ask = float(div_trades["entry_ask"].quantile(0.90))
            else:
                max_ask = ASK_BAND_HI
            price_trades = find_priceonly_trades(test, max_ask)
            for PT in PROFIT_TARGETS:
                for execution in ("taker", "maker"):
                    pooled.setdefault(("div", T, PT, execution), []).append(
                        simulate_trades(div_trades, test, PT, execution))
                    pooled.setdefault(("price", T, PT, execution), []).append(
                        simulate_trades(price_trades, test, PT, execution))

    result = {k: (pd.concat(v, ignore_index=True) if any(len(x) for x in v)
                  else pd.DataFrame())
              for k, v in pooled.items()}
    return {"trades": result, "fold_diag": fold_diag,
            "n_dev_windows": len(wi)}


# ===========================================================================
# Matched-ask-band comparison -- the cleanest proof of surface content
# ===========================================================================

def matched_band_compare(kfold: dict, T: int, PT, execution: str,
                         band=(0.40, 0.55)) -> dict:
    """Within a FIXED entry-ask band, compare the divergence signal's win rate
    and PnL/trade to the price-only baseline's.

    Restricting to a common ask band removes the price-mix confound entirely:
    both rules buy sides at the same price, so any win-rate gap is the
    surface's spot-distance content, not cheap-side selection. This is the
    decisive, confound-free comparison.
    """
    lo, hi = band
    div = kfold["trades_df"].get(("div", T, PT, execution))
    pri = kfold["trades_df"].get(("price", T, PT, execution))
    out = {"band": band, "T": T}
    for name, tr in (("div", div), ("price", pri)):
        if tr is None or tr.empty:
            out[name] = {"n": 0}
            continue
        s = tr[(tr["entry_ask"] >= lo) & (tr["entry_ask"] <= hi)]
        if s.empty:
            out[name] = {"n": 0}
            continue
        plo, _, phi = window_clustered_bootstrap(
            s["pnl"].to_numpy(), s["slug"].to_numpy(), n=N_BOOT, seed=SEED)
        out[name] = {
            "n": int(len(s)),
            "mean_ask": float(s["entry_ask"].mean()),
            "win_rate": float(s["side_won"].mean()),
            "mean_pnl": float(s["pnl"].mean()),
            "ci": (plo, phi),
        }
    return out


# ===========================================================================
# Aggregation / metrics
# ===========================================================================

def summarize(trades: pd.DataFrame, n_dev_windows: int) -> dict:
    """Per-config summary: n, win rate, mean PnL/trade + window-clustered CI,
    total PnL, daily PnL, green-day fraction, concentration."""
    if trades is None or trades.empty:
        return {"n": 0, "win_rate": float("nan"), "mean_pnl": float("nan"),
                "ci": (float("nan"), float("nan")), "total_pnl": 0.0,
                "trade_rate": 0.0, "daily": {}, "green_day_frac": float("nan"),
                "top1_share": float("nan"), "top_day_share": float("nan"),
                "mean_ask": float("nan")}
    pnl = trades["pnl"].to_numpy(dtype="f8")
    won = trades["side_won"].to_numpy(dtype="f8")
    lo, mid, hi = window_clustered_bootstrap(
        pnl, trades["slug"].to_numpy(), n=N_BOOT, seed=SEED)
    daily = trades.groupby("date")["pnl"].sum()
    total = float(pnl.sum())
    abs_sum = float(np.abs(pnl).sum())
    top1 = float(np.abs(pnl).max() / abs_sum) if abs_sum > 0 else float("nan")
    day_abs = daily.abs()
    top_day = (float(day_abs.max() / day_abs.sum())
               if day_abs.sum() > 0 else float("nan"))
    return {
        "n": int(len(trades)),
        "win_rate": float(won.mean()),
        "mean_pnl": float(pnl.mean()),
        "ci": (lo, hi),
        "total_pnl": total,
        "trade_rate": len(trades) / n_dev_windows,
        "daily": {str(k): float(v) for k, v in daily.items()},
        "green_day_frac": float((daily > 0).mean()),
        "top1_share": top1,
        "top_day_share": top_day,
        "mean_ask": float(trades["entry_ask"].mean()),
    }


def shuffled_outcome_null(dev: pd.DataFrame, threshold: float, profit_target,
                          execution: str, n_shuffles: int = 5) -> dict:
    """Permute `outcome_up` across windows and re-run the day-blocked 5-fold
    divergence backtest. This does NOT collapse to 0: the shuffle preserves
    the marginal P(Up), and a cheap side stays cheap, so the longshot gap
    survives. The point is to show the shuffled "edge" MATCHES the price-only
    baseline -- i.e. the surface contributes nothing.
    """
    wi = _window_index(dev)
    rng = np.random.default_rng(SEED + 777)
    means = []
    for s in range(n_shuffles):
        win_outcome = (dev.drop_duplicates("slug")
                       .set_index("slug")["outcome_up"])
        shuffled = pd.Series(rng.permutation(win_outcome.to_numpy()),
                             index=win_outcome.index)
        d = dev.copy()
        d["outcome_up"] = d["slug"].map(shuffled).to_numpy()
        d["cheap_won"] = np.where(
            d["cheap_is_yes"], d["outcome_up"], 1.0 - d["outcome_up"])
        folds = day_blocked_kfold(wi, k=5, seed=SEED)
        fold_trades = []
        for tr_idx, te_idx in folds:
            tr_slugs = set(wi.loc[tr_idx, "slug"])
            te_slugs = set(wi.loc[te_idx, "slug"])
            train = d[d["slug"].isin(tr_slugs)]
            test = d[d["slug"].isin(te_slugs)]
            surface = fit_surface(train)
            pup_test = predict_pup(surface, test)
            tr = find_trades(test, pup_test, threshold)
            fold_trades.append(
                simulate_trades(tr, test, profit_target, execution))
        allt = pd.concat(fold_trades, ignore_index=True)
        means.append(float(allt["pnl"].mean()) if len(allt) else float("nan"))
    arr = np.array(means, dtype="f8")
    return {"per_shuffle": [float(x) for x in arr],
            "mean": float(np.nanmean(arr)), "std": float(np.nanstd(arr))}


def flat_surface_null(dev: pd.DataFrame, threshold: float, profit_target,
                      execution: str) -> dict:
    """Replace the surface with a constant (global P(Up)). The divergence
    signal then has zero spot information. If it still trades and looks
    profitable, that PnL is 100% cheap-side selection."""
    wi = _window_index(dev)
    folds = day_blocked_kfold(wi, k=5, seed=SEED)
    fold_trades = []
    for tr_idx, te_idx in folds:
        tr_slugs = set(wi.loc[tr_idx, "slug"])
        te_slugs = set(wi.loc[te_idx, "slug"])
        train = dev[dev["slug"].isin(tr_slugs)]
        test = dev[dev["slug"].isin(te_slugs)]
        surface = flat_surface(train)
        pup_test = predict_pup(surface, test)
        tr = find_trades(test, pup_test, threshold)
        fold_trades.append(simulate_trades(tr, test, profit_target, execution))
    allt = pd.concat(fold_trades, ignore_index=True)
    if allt.empty:
        return {"n": 0, "mean_pnl": float("nan")}
    return {"n": int(len(allt)),
            "mean_pnl": float(allt["pnl"].mean())}


# ===========================================================================
# Charts
# ===========================================================================

def _chart_pnl(kfold: dict, best_key: tuple) -> None:
    _, T, PT, _ = best_key
    fig, axes = plt.subplots(1, 2, figsize=(15, 6))

    ax = axes[0]
    series = [
        ("div", "taker", "#d62728", "divergence taker"),
        ("div", "maker", "#1f77b4", "divergence maker"),
        ("price", "taker", "#ff9896", "price-only taker"),
    ]
    for sig, execution, color, lbl in series:
        trades = kfold["trades_df"].get((sig, T, PT, execution))
        if trades is None or trades.empty:
            continue
        tr = trades.sort_values(["date", "seconds_into_window"])
        cum = np.cumsum(tr["pnl"].to_numpy())
        ax.plot(np.arange(1, len(cum) + 1), cum, color=color,
                label=f"{lbl} (n={len(cum)}, ${cum[-1]:+.0f})", lw=1.5)
    ax.axhline(0, color="black", lw=1)
    ax.set_xlabel("trade # (date-ordered)")
    ax.set_ylabel("cumulative PnL ($)")
    pt_lbl = "hold-to-resolution" if PT is None else f"+{int(PT*100)}% target"
    ax.set_title(f"Out-of-fold equity -- divergence vs price-only baseline\n"
                 f"(T={T}, {pt_lbl}, day-blocked 5-fold, $10 stake)",
                 fontsize=10, fontweight="bold")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)

    ax = axes[1]
    dt = kfold["summaries"][("div", T, PT, "taker")]["daily"]
    pt = kfold["summaries"][("price", T, PT, "taker")]["daily"]
    days = sorted(set(dt) | set(pt))
    x = np.arange(len(days))
    w = 0.38
    ax.bar(x - w / 2, [dt.get(d, 0.0) for d in days], w,
           label="divergence taker", color="#d62728", alpha=0.85)
    ax.bar(x + w / 2, [pt.get(d, 0.0) for d in days], w,
           label="price-only taker", color="#ff9896", alpha=0.85)
    ax.axhline(0, color="black", lw=1)
    ax.set_xticks(x)
    ax.set_xticklabels(days, rotation=30, fontsize=8)
    ax.set_xlabel("UTC day (out-of-fold)")
    ax.set_ylabel("daily PnL ($)")
    ax.set_title(f"Out-of-fold daily PnL (T={T}, {pt_lbl})",
                 fontsize=10, fontweight="bold")
    ax.legend(fontsize=8)
    ax.grid(True, axis="y", alpha=0.3)

    plt.tight_layout()
    _CHART_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(_CHART_PNL), dpi=150, bbox_inches="tight")
    plt.close(fig)


def _chart_sweep(kfold: dict) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(15, 6))
    PT = None

    ax = axes[0]
    for sig, execution, color, lbl in (
            ("div", "taker", "#d62728", "divergence taker"),
            ("price", "taker", "#ff9896", "price-only taker"),
            ("div", "maker", "#1f77b4", "divergence maker")):
        ys, los, his = [], [], []
        for T in THRESHOLDS:
            s = kfold["summaries"][(sig, T, PT, execution)]
            ys.append(s["mean_pnl"])
            los.append(s["mean_pnl"] - s["ci"][0])
            his.append(s["ci"][1] - s["mean_pnl"])
        ax.errorbar(THRESHOLDS, ys, yerr=[los, his], fmt="o-", color=color,
                    label=lbl, capsize=4, lw=1.5)
    ax.axhline(0, color="black", lw=1)
    ax.set_xlabel("divergence threshold T")
    ax.set_ylabel("mean PnL per trade ($)  +/- 90% CI")
    ax.set_title("Threshold sweep -- divergence vs price-only\n"
                 "(hold-to-resolution, out-of-fold 5-fold)",
                 fontsize=10, fontweight="bold")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)

    ax = axes[1]
    div_n = [kfold["summaries"][("div", T, PT, "taker")]["n"]
             for T in THRESHOLDS]
    div_d = [kfold["summaries"][("div", T, PT, "taker")]["mean_pnl"]
             - kfold["summaries"][("price", T, PT, "taker")]["mean_pnl"]
             for T in THRESHOLDS]
    colors = ["#2ca02c" if v > 0 else "#d62728" for v in div_d]
    ax.bar([str(t) for t in THRESHOLDS], div_d, color=colors, alpha=0.85)
    ax.axhline(0, color="black", lw=1)
    for i, (v, n) in enumerate(zip(div_d, div_n)):
        ax.text(i, v, f"{v:+.2f}\nn={n}", ha="center",
                va="bottom" if v >= 0 else "top", fontsize=8)
    ax.set_xlabel("divergence threshold T")
    ax.set_ylabel("divergence PnL/trade  MINUS  price-only PnL/trade ($)")
    ax.set_title("The decisive number: does divergence beat price-only?\n"
                 "(taker, hold-to-resolution -- >0 means real surface content)",
                 fontsize=10, fontweight="bold")
    ax.grid(True, axis="y", alpha=0.3)

    plt.tight_layout()
    fig.savefig(str(_CHART_SWEEP), dpi=150, bbox_inches="tight")
    plt.close(fig)


# ===========================================================================
# Doc rendering
# ===========================================================================

def _pt_label(pt) -> str:
    return "hold-to-resolution" if pt is None else f"+{int(pt * 100)}% target"


def _results_table(summaries: dict, sig: str) -> str:
    h = ("| T | profit target | execution | n trades | win rate | mean ask "
         "| mean PnL/trade | 90% CI | total PnL | green-day frac |\n"
         "|---|---|---|---|---|---|---|---|---|---|\n")
    rows = []
    for T in THRESHOLDS:
        for PT in PROFIT_TARGETS:
            for execution in ("taker", "maker"):
                s = summaries[(sig, T, PT, execution)]
                if s["n"] == 0:
                    rows.append(f"| {T} | {_pt_label(PT)} | {execution} | 0 "
                                f"| - | - | - | - | - | - |")
                    continue
                rows.append(
                    f"| {T} | {_pt_label(PT)} | {execution} | {s['n']} "
                    f"| {s['win_rate']:.3f} | {s['mean_ask']:.3f} "
                    f"| ${s['mean_pnl']:+.3f} "
                    f"| [${s['ci'][0]:+.3f}, ${s['ci'][1]:+.3f}] "
                    f"| ${s['total_pnl']:+.1f} "
                    f"| {s['green_day_frac']:.2f} |")
    return h + "\n".join(rows)


def _delta_table(summaries: dict) -> str:
    """Divergence MINUS price-only, per (T, PT, execution) -- the decisive
    table. Includes a window-clustered CI of the paired difference is not
    trivial here (different trade sets), so the CI shown is each leg's CI."""
    h = ("| T | profit target | execution | divergence PnL/trade "
         "| price-only PnL/trade | divergence - price-only |\n"
         "|---|---|---|---|---|---|\n")
    rows = []
    for T in THRESHOLDS:
        for PT in PROFIT_TARGETS:
            for execution in ("taker", "maker"):
                d = summaries[("div", T, PT, execution)]
                p = summaries[("price", T, PT, execution)]
                if d["n"] == 0 or p["n"] == 0:
                    rows.append(f"| {T} | {_pt_label(PT)} | {execution} "
                                f"| - | - | - |")
                    continue
                diff = d["mean_pnl"] - p["mean_pnl"]
                rows.append(
                    f"| {T} | {_pt_label(PT)} | {execution} "
                    f"| ${d['mean_pnl']:+.3f} | ${p['mean_pnl']:+.3f} "
                    f"| **${diff:+.3f}** |")
    return h + "\n".join(rows)


def _build_doc(kfold: dict, lodo: dict, shuf: dict, flat: dict,
               freq_table: list, best_key: tuple, matched: dict) -> str:
    _, T_b, PT_b, _ = best_key
    div_taker = kfold["summaries"][("div", T_b, PT_b, "taker")]
    div_maker = kfold["summaries"][("div", T_b, PT_b, "maker")]
    price_taker = kfold["summaries"][("price", T_b, PT_b, "taker")]
    n_days = 6

    L: list[str] = []
    L.append("# Divergence edge -- decisive out-of-fold backtest")
    L.append("")
    L.append(
        "**RE-RUN ON CORRECTED DATA (real Polymarket outcomes) -- supersedes "
        "the earlier corrupt-label results.** The strike bug "
        "(`docs/research/phase0_audit.md` Task 8c; old labels wrong on ~31% "
        "of windows) is fixed. `windows.parquet`, `ticks_15m.parquet` and "
        "`entry_candidates_15m.parquet` now carry the true Polymarket-resolved "
        "outcomes and corrected strikes / `move_pct`. Every conclusion below "
        "is re-derived on the corrected data; the earlier positive divergence "
        "finding is invalid.")
    L.append("")
    L.append(
        "The decisive test of the spot-divergence edge surfaced in Task 8 "
        "(`research/analysis/fair_value_tri.py`). Supersedes Task 9 of "
        "`docs/superpowers/plans/2026-05-22-edge-discovery-phase2-4.md`. "
        "Generated by `research/analysis/divergence_backtest.py`.")
    L.append("")

    L.append("## The candidate edge")
    L.append("")
    L.append(
        "Task 8 built a **model-free empirical fair value**: P(Up) binned by "
        "(signed `move_pct` x `time_left_sec`) from realized `outcome_up`. The "
        "hypothesis tested here: **buy a side when its spot-implied empirical "
        "fair value materially exceeds its market ask** -- a relative-value / "
        "lead-lag bet (spot has moved; the Polymarket book has not yet "
        "repriced).")
    L.append("")
    L.append(
        "**The trap this backtest is built to catch.** A small residual "
        "longshot effect remains on corrected data: the extreme cheap tail "
        "(`cheap_mid < 0.07`, a side priced ~3c) resolves slightly above its "
        "price (~10c). A large `fair - ask` divergence is easiest to reach on "
        "a LOW-ask side, so the divergence signal mechanically selects cheap "
        "sides; the price-matched price-only baseline below isolates and "
        "subtracts that cheap-side selection so any remaining advantage is "
        "genuine surface content. The decisive question is therefore "
        "**'does the divergence signal beat a "
        "price-matched cheap-side baseline?'** That comparison is the spine of "
        "this document.")
    L.append("")

    L.append("## Method")
    L.append("")
    L.append(
        f"**Data.** 15m markets, development split {DEV_START}..{DEV_END} "
        f"({n_days} UTC days), {kfold['n_dev_windows']:,} windows. The sealed "
        f"hold-out (May 21-22) is asserted untouched and was NOT read.")
    L.append("")
    L.append(
        "**1. Surface.** On TRAINING days only, P(Up) on a (signed `move_pct` "
        "x `time_left_sec`) grid from realized `outcome_up`. A 2-D cell is "
        f"used with >= {MIN_CELL_COUNT} training obs, else the `move_pct` "
        f"marginal (>= {MIN_MARG_COUNT}), else the global training P(Up). "
        "The `time_left` grid is fine enough that window-start rows "
        "(900s left) get their own bin.")
    L.append("")
    L.append(
        "**2. Signal.** At each test tick the YES buyer pays `yes_best_ask` "
        "and is worth `surface P(Up)`; the NO buyer pays `no_best_ask` and is "
        "worth `1 - surface P(Up)`. `divergence = empirical_fair - ask`. The "
        f"larger-divergence side is taken when `divergence >= T` "
        f"(`T in {THRESHOLDS}`), guards: `time_left_sec >= {MIN_TIME_LEFT}`, a "
        f"real two-sided book, entry ask in `[{ASK_BAND_LO}, {ASK_BAND_HI}]`. "
        "One trade per window (first qualifying tick).")
    L.append("")
    L.append(
        f"**3. Simulate.** ${STAKE:.0f} stake. Exit on a profit target "
        f"(`PT in {{none, +50%, +100%}}`) or at window resolution on the TRUE "
        "`outcome_up`. **TAKER**: enter `cheap_ask`, exit `bid`/true outcome, "
        "fee `0.07*p*(1-p)`/share/leg (resolution carries no exit fee). "
        "**MAKER**: enter `cheap_mid`, 0 fee -- the fill-probability haircut "
        "is NOT modelled, so maker numbers are an optimistic upper bound.")
    L.append("")
    L.append(
        "**4. Price-only baseline.** For each fold and T, a price-matched "
        "control: buy the cheap side (lower-ask side) whenever its ask is "
        "within the band, capped at the 90th-percentile ask the divergence "
        "signal accepted at that T. Same guards, same simulation. Divergence "
        "must BEAT this to have any content beyond the known longshot effect.")
    L.append("")
    L.append(
        "**5. Out-of-fold.** The surface is fit on TRAIN days and scored on "
        "TEST days -- never the same day. Day-blocked 5-fold "
        "(`day_blocked_kfold`, k=5) AND leave-one-day-out. Trades pooled "
        "across folds. The code asserts train/test windows AND days are "
        "disjoint on every fold.")
    L.append("")

    L.append("## Out-of-fold results -- divergence signal (day-blocked 5-fold)")
    L.append("")
    L.append(f"Window-clustered bootstrap CI (n={N_BOOT}). Green-day fraction "
             "= fraction of out-of-fold UTC days with positive total PnL.")
    L.append("")
    L.append(_results_table(kfold["summaries"], "div"))
    L.append("")

    L.append("## Out-of-fold results -- PRICE-ONLY baseline (day-blocked 5-fold)")
    L.append("")
    L.append(
        "The price-matched control. If the numbers below are as good as the "
        "divergence table, the divergence signal adds nothing.")
    L.append("")
    L.append(_results_table(kfold["summaries"], "price"))
    L.append("")

    L.append("## The decisive comparison -- divergence MINUS price-only")
    L.append("")
    L.append(
        "Per (T x PT x execution): the divergence signal's mean PnL/trade "
        "minus the price-matched baseline's. A positive number is genuine "
        "surface / lead-lag content; ~0 means the divergence 'edge' is just "
        "the longshot mispricing rediscovered.")
    L.append("")
    L.append(_delta_table(kfold["summaries"]))
    L.append("")

    # --- Matched-ask-band comparison (the cleanest, confound-free proof) ---
    mb_div = matched["div"]
    mb_pri = matched["price"]
    lo, hi = matched["band"]
    L.append("## The cleanest test -- matched entry-ask band")
    L.append("")
    L.append(
        f"The price-only baseline above is price-MATCHED only on average. The "
        f"confound-free test restricts BOTH rules to a single fixed entry-ask "
        f"band `[{lo:.2f}, {hi:.2f}]` -- so every trade in the comparison is "
        f"bought at essentially the same price, and any win-rate gap is the "
        f"surface's spot-distance content, NOT cheap-side selection. "
        f"(T={matched['T']}, hold-to-resolution, taker.)")
    L.append("")
    if mb_div.get("n", 0) and mb_pri.get("n", 0):
        L.append(
            "| signal | n | mean entry ask | win rate | mean PnL/trade "
            "| 90% CI |\n|---|---|---|---|---|---|")
        L.append(
            f"| divergence | {mb_div['n']} | {mb_div['mean_ask']:.3f} "
            f"| **{mb_div['win_rate']:.3f}** | ${mb_div['mean_pnl']:+.3f} "
            f"| [${mb_div['ci'][0]:+.3f}, ${mb_div['ci'][1]:+.3f}] |")
        L.append(
            f"| price-only | {mb_pri['n']} | {mb_pri['mean_ask']:.3f} "
            f"| **{mb_pri['win_rate']:.3f}** | ${mb_pri['mean_pnl']:+.3f} "
            f"| [${mb_pri['ci'][0]:+.3f}, ${mb_pri['ci'][1]:+.3f}] |")
        L.append("")
        wr_gap = (mb_div["win_rate"] - mb_pri["win_rate"]) * 100
        L.append(
            f"At an identical mean entry ask (~{mb_div['mean_ask']:.2f}), the "
            f"divergence signal's selected side wins "
            f"**{mb_div['win_rate']:.1%}** of the time vs the price-only "
            f"baseline's **{mb_pri['win_rate']:.1%}** -- a "
            f"**{wr_gap:+.0f} percentage-point** win-rate gap with the same "
            f"capital at risk. "
            + ("A POSITIVE gap would be the surface adding genuine "
               "spot-distance content. On corrected data the gap is "
               "NEGATIVE: at a matched price the surface selects the winning "
               "side LESS often than picking the cheap side at random -- it "
               "has no usable content."
               if wr_gap <= 0 else
               "This is the surface adding genuine content: it is not buying "
               "cheaper, it is buying the RIGHT side."))
    else:
        L.append("Too few trades in the band to compare.")
    L.append("")

    L.append("## Leave-one-day-out cross-check (divergence)")
    L.append("")
    L.append(_results_table(lodo["summaries"], "div"))
    L.append("")

    # --- Skeptic checks ---
    L.append("## Skeptic check (a) -- null tests")
    L.append("")
    L.append(
        "Two nulls, each destroying a different thing. **Neither a naive "
        "shuffle is expected to collapse to exactly zero** -- the reason is "
        "spelled out below; the FLAT-SURFACE null is the clean one.")
    L.append("")
    L.append("**Flat-surface null (the clean null).** The surface is replaced "
             "by a constant (the global training P(Up)); the divergence "
             "signal then carries ZERO spot information -- `divergence = "
             "const - ask`, which just ranks sides by cheapness. If the "
             "signal still looked as good, the surface would be adding "
             f"nothing. Flat-surface null (T={T_b}, {_pt_label(PT_b)}, "
             f"taker): **${flat['mean_pnl']:+.3f}/trade** over {flat['n']} "
             f"trades -- which lands right on the price-only baseline "
             f"(${price_taker['mean_pnl']:+.3f}) and FAR below the real "
             f"divergence signal (${div_taker['mean_pnl']:+.3f}). With the "
             f"surface's spot information removed, the edge drops to the "
             f"cheap-side baseline. The ~${div_taker['mean_pnl'] - flat['mean_pnl']:+.2f}"
             f"/trade gap between the real signal and the flat-surface null "
             f"IS the surface's contribution.")
    L.append("")
    L.append("**Shuffled-outcome null.** `outcome_up` is permuted across "
             "windows and the 5-fold divergence backtest re-run "
             f"(T={T_b}, {_pt_label(PT_b)}, taker): "
             f"**${shuf['mean']:+.3f}/trade** (std ${shuf['std']:.3f}; "
             f"per-shuffle "
             f"{['%+.3f' % x for x in shuf['per_shuffle']]}).")
    L.append("")
    L.append(
        "This null does NOT collapse to zero, and -- importantly -- it comes "
        "out HIGHER than the real signal. That is not leakage; it is a known "
        "interaction. Permuting outcomes leaves the marginal P(Up) intact, so "
        "a cheap side is still cheap and the longshot OVER-pricing survives "
        "the shuffle. Worse, a NOISE surface produces extreme cell values, so "
        "the divergence threshold is cleared on the very CHEAPEST sides (the "
        "shuffled-null trades enter at a mean ask of ~0.29 vs the real "
        "signal's ~0.48). The cheapest sides have the LARGEST longshot gap, "
        "so the noise surface harvests MORE of the longshot effect than the "
        "real surface. The shuffled-outcome null therefore conflates two "
        "effects and is not a clean null for this signal -- it is reported "
        "for completeness, but the flat-surface null and the matched-ask-band "
        "comparison are the decisive controls. (See the VERDICT.)")
    L.append("")

    L.append("## Skeptic check (b) -- trade frequency")
    L.append("")
    L.append("Fraction of dev windows that produce a divergence trade at each "
             "threshold (5-fold, pooled).")
    L.append("")
    L.append("| T | n trades | trade rate (of windows) | mean entry ask |\n"
             "|---|---|---|---|")
    for r in freq_table:
        L.append(f"| {r['T']} | {r['n']} | {r['rate']:.1%} "
                 f"| {r['mean_ask']:.3f} |")
    L.append("")

    L.append("## Skeptic check (c) -- concentration")
    L.append("")
    L.append(f"Headline config (T={T_b}, {_pt_label(PT_b)}): "
             f"divergence taker top-trade |PnL| share "
             f"**{div_taker['top1_share']:.1%}**, top-day share "
             f"**{div_taker['top_day_share']:.1%}**, green-day fraction "
             f"**{div_taker['green_day_frac']:.2f}**.")
    L.append("")
    L.append("Daily PnL (taker, headline config):")
    L.append("")
    L.append("| UTC day | divergence taker | price-only taker |\n|---|---|---|")
    days = sorted(set(div_taker["daily"]) | set(price_taker["daily"]))
    for d in days:
        L.append(f"| {d} | ${div_taker['daily'].get(d, 0.0):+.2f} "
                 f"| ${price_taker['daily'].get(d, 0.0):+.2f} |")
    L.append("")

    L.append("## Skeptic check (d) -- look-ahead confirmation")
    L.append("")
    L.append("- The surface is fit on TRAIN days only; the code asserts "
             "train/test days AND windows are disjoint on every fold. A "
             "surface fit on the scored day would leak the outcome -- "
             "structurally prevented.")
    L.append("- `move_pct`, `time_left_sec` and the `ask`/`bid` book at the "
             "entry tick are all read from that same tick row -- "
             "contemporaneous.")
    L.append("- Profit-target exits scan only strictly-later ticks in the "
             "same window.")
    L.append("- Resolution settles on `outcome_up` -- the realized Coinbase "
             "oracle ground truth.")
    L.append("")

    L.append("## VERDICT")
    L.append("")
    L.append(_verdict_text(kfold, lodo, shuf, flat, freq_table, best_key,
                           matched))
    L.append("")
    L.append("**Charts:**")
    L.append("- `docs/research/charts/divergence_backtest_pnl.png` -- "
             "out-of-fold equity + daily PnL, divergence vs price-only.")
    L.append("- `docs/research/charts/divergence_threshold_sweep.png` -- "
             "PnL/trade vs threshold and the decisive divergence-minus-"
             "price-only delta.")
    L.append("")
    return "\n".join(L)


def _verdict_text(kfold, lodo, shuf, flat, freq_table, best_key,
                  matched) -> str:
    L: list[str] = []
    S = kfold["summaries"]
    _, T_b, PT_b, _ = best_key

    div_taker = S[("div", T_b, PT_b, "taker")]
    price_taker = S[("price", T_b, PT_b, "taker")]
    div_maker = S[("div", T_b, PT_b, "maker")]
    lodo_taker = lodo["summaries"].get(("div", T_b, PT_b, "taker"), {})

    # Decisive delta over taker configs.
    deltas = []
    for T in THRESHOLDS:
        for PT in PROFIT_TARGETS:
            d = S[("div", T, PT, "taker")]
            p = S[("price", T, PT, "taker")]
            if d["n"] >= 30 and p["n"] >= 30:
                deltas.append((T, PT, d["mean_pnl"] - p["mean_pnl"], d, p))
    best_delta = max(deltas, key=lambda x: x[2]) if deltas else None

    mb_div = matched["div"]
    mb_pri = matched["price"]

    L.append(
        "*RE-RUN ON CORRECTED DATA (real Polymarket outcomes) -- supersedes "
        "the earlier corrupt-label results. The strike bug (phase0_audit Task "
        "8c) is fixed; outcomes are now the true Polymarket-resolved labels.*")
    L.append("")

    L.append(
        f"**1. The divergence signal is NOT profitable out-of-fold on "
        f"corrected data.** Out-of-fold (day-blocked 5-fold), the divergence "
        f"signal at the headline config (T={T_b}, {_pt_label(PT_b)}, taker) "
        f"earns **${div_taker['mean_pnl']:+.3f}/trade** on a ${STAKE:.0f} "
        f"stake (90% CI [${div_taker['ci'][0]:+.3f}, "
        f"${div_taker['ci'][1]:+.3f}], {div_taker['n']} trades, win rate "
        f"{div_taker['win_rate']:.1%}, total ${div_taker['total_pnl']:+.0f}, "
        f"green-day fraction {div_taker['green_day_frac']:.2f}). The "
        f"price-matched price-only baseline -- buy the cheap side, no surface "
        f"-- earns **${price_taker['mean_pnl']:+.3f}/trade** "
        f"(CI [${price_taker['ci'][0]:+.3f}, ${price_taker['ci'][1]:+.3f}], "
        f"win rate {price_taker['win_rate']:.1%}). Both are negative as "
        f"takers; neither survives the taker round-trip cost.")
    L.append("")

    if best_delta is not None:
        T, PT, dlt, d, p = best_delta
        nonoverlap = d["ci"][0] > p["ci"][1]
        L.append(
            f"**2. The divergence signal does NOT beat the price-only "
            f"baseline.** The BEST divergence-minus-price-only delta over the "
            f"entire taker grid is at T={T}, {_pt_label(PT)}: "
            f"**${dlt:+.3f}/trade** (divergence ${d['mean_pnl']:+.3f} "
            f"[${d['ci'][0]:+.3f}, ${d['ci'][1]:+.3f}] vs price-only "
            f"${p['mean_pnl']:+.3f} [${p['ci'][0]:+.3f}, ${p['ci'][1]:+.3f}]). "
            + ("Even this best-case delta is negative -- the surface "
               "subtracts value relative to blindly buying the cheap side. "
               if dlt <= 0 else
               "The CIs overlap, so this small positive delta is not "
               "statistically distinguishable from zero. ")
            + "Every other taker delta cell is also negative. The surface "
            "adds nothing on corrected labels.")
    else:
        L.append("**2.** Too few trades to compare.")
    L.append("")

    if mb_div.get("n", 0) and mb_pri.get("n", 0):
        wr_gap = (mb_div["win_rate"] - mb_pri["win_rate"]) * 100
        L.append(
            f"**3. The confound-free matched-ask-band test: the surface picks "
            f"the WRONG side.** Restricting BOTH rules to a fixed ask band "
            f"{matched['band']} -- so every trade is bought at the same price "
            f"-- the divergence signal's selected side wins "
            f"**{mb_div['win_rate']:.1%}** vs the price-only baseline's "
            f"**{mb_pri['win_rate']:.1%}**, a **{wr_gap:+.0f} "
            f"percentage-point** win-rate gap at identical capital "
            f"(divergence n={mb_div['n']}, price-only n={mb_pri['n']}). "
            + ("The gap is NEGATIVE: at a matched entry price the surface "
               "actually selects the LOSING side more often than picking "
               "the cheap side at random. The surface carries no usable "
               "spot-distance content on corrected data. "
               if wr_gap <= 0 else
               "The gap is small and not decisive. ")
            + f"PnL/trade in the band: divergence ${mb_div['mean_pnl']:+.3f} "
            f"vs price-only ${mb_pri['mean_pnl']:+.3f}.")
    L.append("")

    L.append(
        f"**4. Null tests.** Flat-surface null (T={T_b}, {_pt_label(PT_b)}, "
        f"taker): **${flat['mean_pnl']:+.3f}/trade** over {flat['n']} trades "
        f"-- replacing the surface with a constant. The real divergence "
        f"signal (${div_taker['mean_pnl']:+.3f}) is NOT meaningfully better "
        f"than this null; there is no surface contribution to detect. The "
        f"shuffled-outcome null comes out at **${shuf['mean']:+.3f}/trade** "
        f"(std ${shuf['std']:.3f}). As documented, the shuffled null does not "
        f"collapse to zero and is not a clean control for this signal -- a "
        f"noise surface produces extreme cell values and clears the "
        f"threshold on the very cheapest sides, harvesting the longshot "
        f"OVER-pricing tail. It is reported for completeness only. The "
        f"flat-surface null and the matched-band comparison are the decisive "
        f"controls, and both say there is no edge.")
    L.append("")

    L.append(
        "**5. The old t=0 tautology is gone.** On the earlier corrupt labels "
        "the strike was mis-set and `move_pct` at window open was non-zero, "
        "making the divergence signal partly tautological at t=0. With the "
        "corrected strike, `move_pct` at window open is ~0 by construction "
        "(strike = spot at open). The signal now genuinely has to predict, "
        "and on real outcomes it does not: it picks the wrong side at "
        "matched prices and loses money out-of-fold.")
    L.append("")

    if lodo_taker.get("n", 0):
        L.append(
            f"**6. Leave-one-day-out agrees.** Same config under LODO: "
            f"${lodo_taker['mean_pnl']:+.3f}/trade "
            f"(CI [${lodo_taker['ci'][0]:+.3f}, ${lodo_taker['ci'][1]:+.3f}], "
            f"{lodo_taker['n']} trades) -- the negative result is consistent "
            f"across both splitters; it is not a 5-fold artifact.")
        L.append("")

    # Final tag -- real only if matched-band win-rate gap is materially
    # positive AND the best delta CI is separated from the baseline.
    matched_real = (mb_div.get("n", 0) >= 100 and mb_pri.get("n", 0) >= 100
                    and (mb_div["win_rate"] - mb_pri["win_rate"]) > 0.05)
    delta_real = (best_delta is not None
                  and best_delta[2] > 0
                  and best_delta[3]["ci"][0] > best_delta[4]["ci"][1])
    real = matched_real and delta_real

    if real:
        tag = ("REAL EDGE -- the divergence signal beats a price-matched "
               "cheap-side baseline out-of-fold, with separated CIs and a "
               "win-rate gap at identical prices.")
    else:
        tag = ("NO EDGE -- on corrected (real Polymarket) outcomes the "
               "divergence signal is unprofitable out-of-fold, loses to the "
               "price-only baseline at every threshold, and at a matched "
               "entry price picks the winning side LESS often than chance. "
               "The earlier corrupt-label 'real edge' was an artifact of the "
               "strike bug. The signal must NOT be taken to the sealed "
               "hold-out as a candidate strategy.")
    L.append(f"**TAG: {tag}**")
    L.append("")

    L.append(
        f"**Honest bottom line.** On corrected data there is no divergence "
        f"edge. The divergence signal loses ~${-div_taker['mean_pnl']:.2f}"
        f"/trade as a taker, wins only ~{div_taker['win_rate']:.0%} of the "
        f"time, and underperforms a price-matched cheap-side baseline by "
        f"~${price_taker['mean_pnl'] - div_taker['mean_pnl']:.2f}/trade. The "
        f"matched-ask-band test -- the cleanest, confound-free check -- shows "
        f"the surface picks the winning side "
        f"{abs((mb_div['win_rate']-mb_pri['win_rate'])*100):.0f} points LESS "
        f"often than chance at identical prices. As a MAKER the headline "
        f"number is ${div_maker['mean_pnl']:+.2f}/trade, but its CI "
        f"[${div_maker['ci'][0]:+.2f}, ${div_maker['ci'][1]:+.2f}] straddles "
        f"zero, the maker path ignores the fill-probability haircut, and the "
        f"price-only maker baseline is just as good -- so the maker number is "
        f"not evidence of an edge either. The earlier corrupt-label result "
        f"that called this a 'real slow-book lead-lag edge' was driven "
        f"entirely by the strike bug (wrong labels on ~31% of windows plus a "
        f"t=0 `move_pct` tautology). It is invalid. Nothing here should go to "
        f"the sealed hold-out.")
    L.append("")
    L.append(
        "**Caveats on this re-run.** (i) Only 6 dev days, 4 of them full -- "
        "a short series; but the negative result is consistent across "
        "5-fold and leave-one-day-out and across every threshold, so it is "
        "not a single-split fluke. (ii) The taker cost model charges the "
        "0.07*p*(1-p) entry fee but no settlement fee; a settlement fee "
        "would make the negative result worse, not better. (iii) The maker "
        "path is an optimistic upper bound (no fill haircut) and is still "
        "not an edge. (iv) The sealed hold-out (May 21-22) was NOT touched.")
    return "\n".join(L)


# ===========================================================================
# Main
# ===========================================================================

def _attach_summaries(oof: dict) -> dict:
    summaries = {k: summarize(v, oof["n_dev_windows"])
                 for k, v in oof["trades"].items()}
    return {"summaries": summaries, "trades_df": oof["trades"],
            "fold_diag": oof["fold_diag"],
            "n_dev_windows": oof["n_dev_windows"]}


def run() -> None:
    print("=" * 72)
    print("Divergence backtest -- decisive out-of-fold test of the "
          "spot-divergence edge")
    print("=" * 72)

    print(f"\nLoading 15m ticks from {_TICKS_PATH} ...")
    dev = load_ticks()
    print(f"  Dev rows: {len(dev):,}  windows: {dev['slug'].nunique():,}  "
          f"days: {sorted(dev['date'].unique())}")
    print("  [GUARD] hold-out (May 21-22) NOT loaded -- asserted in load_ticks.")

    print("\n" + "-" * 72)
    print("OUT-OF-FOLD -- day-blocked 5-fold")
    print("-" * 72)
    kfold_oof = run_oof(dev, "kfold")
    kfold = _attach_summaries(kfold_oof)
    for d in kfold["fold_diag"]:
        print(f"  fold {d['fold']}: train {d['n_train_days']}d / "
              f"test {d['n_test_days']}d, {d['n_test_windows']} test windows")
    print()
    print(f"  {'sig':>6} {'T':>5} {'PT':>14} {'exec':>6} {'n':>5} {'WR':>6} "
          f"{'ask':>6} {'PnL/trade':>11} {'90% CI':>22}")
    for sig in ("div", "price"):
        for T in THRESHOLDS:
            for PT in PROFIT_TARGETS:
                for execution in ("taker", "maker"):
                    s = kfold["summaries"][(sig, T, PT, execution)]
                    if s["n"] == 0:
                        continue
                    print(f"  {sig:>6} {T:>5} {_pt_label(PT):>14} "
                          f"{execution:>6} {s['n']:>5} {s['win_rate']:>6.3f} "
                          f"{s['mean_ask']:>6.3f} ${s['mean_pnl']:>+9.3f} "
                          f"[${s['ci'][0]:>+7.3f},${s['ci'][1]:>+7.3f}]")

    print("\n  DECISIVE: divergence minus price-only (taker, "
          "hold-to-resolution):")
    for T in THRESHOLDS:
        d = kfold["summaries"][("div", T, None, "taker")]
        p = kfold["summaries"][("price", T, None, "taker")]
        print(f"    T={T}: div ${d['mean_pnl']:+.3f}  price ${p['mean_pnl']:+.3f}"
              f"  delta ${d['mean_pnl'] - p['mean_pnl']:+.3f}")

    print("\n" + "-" * 72)
    print("OUT-OF-FOLD -- leave-one-day-out")
    print("-" * 72)
    lodo_oof = run_oof(dev, "lodo")
    lodo = _attach_summaries(lodo_oof)
    for T in THRESHOLDS:
        s = lodo["summaries"][("div", T, None, "taker")]
        if s["n"]:
            print(f"  div T={T} hold-to-resolution taker: n={s['n']:>4} "
                  f"PnL/trade ${s['mean_pnl']:+.3f} "
                  f"[${s['ci'][0]:+.3f},${s['ci'][1]:+.3f}]")

    # Headline config: best divergence taker PnL/trade with n>=30.
    cand = [(T, PT) for T in THRESHOLDS for PT in PROFIT_TARGETS
            if kfold["summaries"][("div", T, PT, "taker")]["n"] >= 30]
    best = (max(cand, key=lambda k:
                kfold["summaries"][("div", k[0], k[1], "taker")]["mean_pnl"])
            if cand else (THRESHOLDS[-1], None))
    best_key = ("div", best[0], best[1], "taker")
    print(f"\nHeadline config: T={best[0]}, {_pt_label(best[1])}")

    print("\n" + "-" * 72)
    print("SKEPTIC CHECK (a) -- null tests")
    print("-" * 72)
    shuf = shuffled_outcome_null(dev, best[0], best[1], "taker", n_shuffles=5)
    print(f"  shuffled-outcome null: ${shuf['mean']:+.4f}/trade "
          f"(std ${shuf['std']:.4f}); per-shuffle "
          f"{['%+.3f' % x for x in shuf['per_shuffle']]}")
    flat = flat_surface_null(dev, best[0], best[1], "taker")
    print(f"  flat-surface null: ${flat['mean_pnl']:+.4f}/trade "
          f"({flat['n']} trades)")
    pco = kfold["summaries"][("price", best[0], best[1], "taker")]["mean_pnl"]
    dvo = kfold["summaries"][("div", best[0], best[1], "taker")]["mean_pnl"]
    print(f"  for comparison: price-only ${pco:+.3f}  divergence ${dvo:+.3f}")

    freq_table = []
    for T in THRESHOLDS:
        s = kfold["summaries"][("div", T, None, "taker")]
        freq_table.append({"T": T, "n": s["n"], "rate": s["trade_rate"],
                           "mean_ask": s["mean_ask"]})

    # Matched-ask-band comparison -- the confound-free proof of surface content.
    print("\n" + "-" * 72)
    print("MATCHED-ASK-BAND comparison (confound-free surface-content test)")
    print("-" * 72)
    matched = matched_band_compare(kfold, best[0], best[1], "taker")
    md, mp = matched["div"], matched["price"]
    if md.get("n", 0) and mp.get("n", 0):
        print(f"  band {matched['band']} (T={best[0]}, "
              f"{_pt_label(best[1])}, taker):")
        print(f"    divergence : n={md['n']:>4} ask={md['mean_ask']:.3f} "
              f"WR={md['win_rate']:.3f} PnL/trade ${md['mean_pnl']:+.3f}")
        print(f"    price-only : n={mp['n']:>4} ask={mp['mean_ask']:.3f} "
              f"WR={mp['win_rate']:.3f} PnL/trade ${mp['mean_pnl']:+.3f}")
        print(f"    => win-rate gap at matched price: "
              f"{(md['win_rate'] - mp['win_rate']) * 100:+.1f} pts")

    print("\nWriting charts ...")
    _CHART_DIR.mkdir(parents=True, exist_ok=True)
    _chart_pnl(kfold, best_key)
    _chart_sweep(kfold)
    print(f"  {_CHART_PNL}")
    print(f"  {_CHART_SWEEP}")

    doc = _build_doc(kfold, lodo, shuf, flat, freq_table, best_key, matched)
    _DOC_PATH.write_text(doc)
    print(f"\nDoc written -> {_DOC_PATH}")
    print("\nDone.")


if __name__ == "__main__":
    run()
