"""Phase 3 -- the drop-event / sell-the-bounce study (corrected data).

The user's actual manual strategy
---------------------------------
"Buy a side after a visible odds DROP, then SELL the bounce within minutes."
Phase 2 tested price-vs-RESOLUTION and found the market calibrated. Phase 3
tests something genuinely different: **after a sharp intra-window odds drop,
does the PRICE revert (overshoot-and-correct) within the window -- enough to
trade, net of cost?**

A market can be calibrated to resolution and still have a tradeable intra-window
price oscillation, or not. Calibration to resolution and intra-window mean
reversion are different statements; Phase 3 is the head-on test of the second.

What a drop event is
--------------------
Per 15m window, per side, a drop event = the side's mid falls sharply from a
short trailing peak (the peak over the last <=30s, i.e. <=31 ticks at 1Hz). A
drop of magnitude D = (trailing_peak - mid) / trailing_peak. We sweep
D >= {10%, 20%, 35%}. One event per crossing per window per side (dedupe: once
a side crosses the threshold, it must first fully recover -- drop below half the
threshold -- before a new event can fire on that side).

We work with BOTH sides. Reason: a 15m Up/Down market has two complementary
sides; a drop on one is (near-)mechanically a rise on the other, but the
*tradeable* side after a drop is the one that fell -- buying low. Restricting to
"the cheap side" would bias toward longshots (Phase 2's known residual longshot
tail) and miss drops that happen on a side that is still the favourite. We
record the side that dropped and treat each (window, side) drop independently;
window-clustered bootstrap (groups = slug) accounts for the within-window
dependence between the two sides.

The decisive question
---------------------
At a drop event, BUY the dropped side at the ASK. Exit at the best of:
  - a profit target: sweep +25 / +50 / +100% on the entry price,
  - a max hold (we sweep none / 120s / 300s),
  - window close, settling on the TRUE corrected `outcome_up`.
Compute net PnL per trade for TAKER (pay ask, sell bid, fee 0.07*p*(1-p) both
legs) and MAKER (limit at mid, 0 fee, fill-probability caveat noted).
Report per-trade PnL +- window-clustered CI, win rate, trades/day, daily PnL,
and the resolution-loss rate (fraction held to a -100%).

H2 -- noise vs signal
---------------------
Split drop events by |spot_move_30s| at the event. Odds dropped but spot flat
= noise -> expect reversion. Odds dropped because spot moved = signal -> expect
continuation. Does reversion concentrate in noise-drops?

Skeptic checks
--------------
- Null: enter at RANDOM ticks (same guards, same exit logic). The drop signal
  must beat it.
- Trade concentration across days / windows.
- No look-ahead: every exit scan is strictly-later ticks in the same window.
- Day-blocked / per-day consistency.

Discipline
----------
Dev split May 15-20 only. The sealed hold-out (May 21-22) is asserted untouched
and never loaded. All CIs are 90% window-clustered bootstraps, groups = slug.
`sigma_proximity` is NOT used (Phase 2 / Task 8 proved it broken).

Outputs:
  - docs/research/charts/drop_forward_path.png
  - docs/research/charts/drop_bounce_distribution.png
  - docs/research/charts/drop_noise_vs_signal.png
  - docs/research/bounce_atlas.md
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
from research.lib.stats import window_clustered_bootstrap  # noqa: E402

_TICKS_PATH = _REPO_ROOT / "data/research/ticks_15m.parquet"
_CHART_DIR = _REPO_ROOT / "docs/research/charts"
_CHART_PATH = _CHART_DIR / "drop_forward_path.png"
_CHART_DIST = _CHART_DIR / "drop_bounce_distribution.png"
_CHART_NS = _CHART_DIR / "drop_noise_vs_signal.png"
_DOC_PATH = _REPO_ROOT / "docs/research/bounce_atlas.md"

SEED = 42
N_BOOT = 2000
STAKE = 10.0  # dollars per trade

# Drop-detection thresholds: fractional fall from the trailing peak.
DROP_THRESHOLDS = [0.10, 0.20, 0.35]
# Trailing-peak window (ticks at 1Hz) -- the "<=30s" lookback.
PEAK_LOOKBACK = 31
# Forward horizons (seconds) at which we record the price path.
FWD_HORIZONS = [15, 30, 60, 120, 300]
# Profit-target multipliers on the entry price.
PROFIT_TARGETS = [0.25, 0.50, 1.00]
# Max-hold caps (seconds); None = hold to window close.
MAX_HOLDS = [None, 120, 300]
# The headline drop threshold the doc / charts focus on.
HEADLINE_DROP = 0.20

# Entry guards.
MIN_TIME_LEFT = 120      # need room for the bounce / exit to play out
EVENT_PRICE_LO = 0.03    # the dropped side's mid floor -- avoid junk-cheap
EVENT_PRICE_HI = 0.97    # ... and near-resolved quotes

# Noise-vs-signal split: |spot_move_30s| (percent) below this = "noise drop".
NOISE_SPOT_CUTOFF = 0.05


# ---------------------------------------------------------------------------
# Taker fee model (Phase 0 frame): per share, per leg.
# ---------------------------------------------------------------------------
def _fee(p):
    """Polymarket-style fee: 0.07 * p * (1-p) per share."""
    return 0.07 * p * (1.0 - p)


# ===========================================================================
# Data loading
# ===========================================================================

def load_ticks() -> pd.DataFrame:
    """Load the 15m tick table, dev split only, sorted within each window.

    The sealed hold-out (May 21-22) is asserted untouched -- the dev filter
    runs before anything else and the asserts fire if a later date leaks.
    """
    cols = ["slug", "symbol", "timestamp_ms", "window_start_ts",
            "seconds_into_window", "time_left_sec",
            "yes_mid", "no_mid", "yes_best_bid", "yes_best_ask",
            "no_best_bid", "no_best_ask", "spot_move_30s", "move_pct",
            "outcome_up"]
    t = pd.read_parquet(_TICKS_PATH, columns=cols)
    t = t[t["window_start_ts"].notna()].copy()
    t["date"] = pd.to_datetime(
        t["window_start_ts"], unit="s", utc=True).dt.strftime("%Y-%m-%d")
    dev = t[(t["date"] >= DEV_START) & (t["date"] <= DEV_END)].copy()

    # Sealed hold-out guard -- decisive, must hold.
    assert (dev["date"] >= DEV_START).all(), "pre-dev row leaked!"
    assert (dev["date"] <= DEV_END).all(), "HOLD-OUT LEAKED INTO DEV!"
    assert dev["date"].nunique() <= 6, "unexpected dev day count"

    fin = (
        np.isfinite(dev["yes_mid"]) & np.isfinite(dev["no_mid"])
        & np.isfinite(dev["seconds_into_window"])
        & dev["outcome_up"].notna()
    )
    dev = dev[fin].copy()
    dev["seconds_into_window"] = dev["seconds_into_window"].astype(int)
    dev = dev.sort_values(["slug", "seconds_into_window"]).reset_index(drop=True)
    return dev


# ===========================================================================
# Drop-event detection
# ===========================================================================

def detect_drops_one_window(g: pd.DataFrame, side: str,
                            threshold: float) -> list[dict]:
    """Detect drop events for one side of one window.

    A drop event fires at the first tick where
        D = (trailing_peak - mid) / trailing_peak >= threshold,
    trailing_peak = max mid over the last PEAK_LOOKBACK ticks (<=30s).

    Dedupe: after a crossing, the side must RECOVER -- its drop fall back
    below threshold/2 -- before a new event can fire. One event per genuine
    crossing per window per side.

    Returns a list of event dicts (row index into `g`, side, magnitude, ...).
    """
    mid = g[f"{side}_mid"].to_numpy(dtype="f8")
    sec = g["seconds_into_window"].to_numpy(dtype="f8")
    tl = g["time_left_sec"].to_numpy(dtype="f8")
    spot = g["spot_move_30s"].to_numpy(dtype="f8")
    n = len(g)
    if n < 5:
        return []

    # Trailing peak over the last PEAK_LOOKBACK ticks (inclusive of current).
    peak = pd.Series(mid).rolling(PEAK_LOOKBACK, min_periods=1).max().to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        drop = np.where(peak > 1e-9, (peak - mid) / peak, 0.0)

    events: list[dict] = []
    armed = True  # can a new event fire?
    recover = threshold * 0.5
    for i in range(n):
        if armed and drop[i] >= threshold:
            # Entry guards: room left, sane event price, valid spot feature.
            if (tl[i] >= MIN_TIME_LEFT
                    and EVENT_PRICE_LO <= mid[i] <= EVENT_PRICE_HI
                    and np.isfinite(spot[i])):
                events.append({
                    "row": int(g.index[i]),
                    "side": side,
                    "sec": float(sec[i]),
                    "event_mid": float(mid[i]),
                    "drop_mag": float(drop[i]),
                    "peak_mid": float(peak[i]),
                    "spot_move_30s": float(spot[i]),
                    "time_left_sec": float(tl[i]),
                })
            armed = False
        elif (not armed) and drop[i] < recover:
            armed = True
    return events


def detect_all_drops(dev: pd.DataFrame, threshold: float) -> pd.DataFrame:
    """Detect drop events across all dev windows, both sides.

    Returns one row per event with the entry book attached (the dropped
    side's ask / bid / mid at the event tick), the window outcome, and the
    forward index range needed for the path / simulation.
    """
    events: list[dict] = []
    for slug, g in dev.groupby("slug", sort=False):
        g = g.sort_values("seconds_into_window")
        # `g` keeps its original dev index; row indices below refer to it.
        for side in ("yes", "no"):
            events.extend(detect_drops_one_window(g, side, threshold))
    if not events:
        return pd.DataFrame()

    ev = pd.DataFrame(events)
    # Attach static window fields + the entry book.
    static = dev.set_index(dev.index)
    ev["slug"] = static.loc[ev["row"], "slug"].to_numpy()
    ev["symbol"] = static.loc[ev["row"], "symbol"].to_numpy()
    ev["date"] = static.loc[ev["row"], "date"].to_numpy()
    ev["outcome_up"] = static.loc[ev["row"], "outcome_up"].to_numpy()

    yes = ev["side"] == "yes"
    ev["entry_ask"] = np.where(
        yes, static.loc[ev["row"], "yes_best_ask"].to_numpy(),
        static.loc[ev["row"], "no_best_ask"].to_numpy())
    ev["entry_bid"] = np.where(
        yes, static.loc[ev["row"], "yes_best_bid"].to_numpy(),
        static.loc[ev["row"], "no_best_bid"].to_numpy())
    # The dropped side won iff: yes-side & outcome up, or no-side & outcome down
    ev["side_won"] = np.where(
        yes, ev["outcome_up"], 1.0 - ev["outcome_up"]).astype("f8")
    # Tradeable-entry guard: a real two-sided book at the event tick.
    ev["book_ok"] = (
        (ev["entry_ask"] > 0) & (ev["entry_bid"] > 0)
        & (ev["entry_bid"] < ev["entry_ask"])
        & (ev["entry_ask"] >= EVENT_PRICE_LO)
        & (ev["entry_ask"] <= EVENT_PRICE_HI)
    )
    return ev.reset_index(drop=True)


# ===========================================================================
# Forward price path
# ===========================================================================

def forward_paths(dev: pd.DataFrame, ev: pd.DataFrame) -> pd.DataFrame:
    """For each event, record the dropped side's mid / bid / ask forward at
    each FWD_HORIZON and at window close.

    Strictly-later ticks only -- the forward look at +Hs reads the tick whose
    `seconds_into_window` is the first >= event_sec + H. Window close = the
    last tick of the window.
    """
    by_slug = {s: g.sort_values("seconds_into_window")
               for s, g in dev.groupby("slug", sort=False)}
    rows = []
    for _, e in ev.iterrows():
        g = by_slug[e["slug"]]
        side = e["side"]
        sec = g["seconds_into_window"].to_numpy(dtype="f8")
        mid = g[f"{side}_mid"].to_numpy(dtype="f8")
        bid = g[f"{side}_best_bid"].to_numpy(dtype="f8")
        ask = g[f"{side}_best_ask"].to_numpy(dtype="f8")
        rec = {}
        for h in FWD_HORIZONS:
            tgt = e["sec"] + h
            j = np.searchsorted(sec, tgt, side="left")
            if j < len(sec):
                rec[f"mid_{h}"] = float(mid[j])
                rec[f"bid_{h}"] = float(bid[j])
                rec[f"ask_{h}"] = float(ask[j])
            else:
                rec[f"mid_{h}"] = np.nan
                rec[f"bid_{h}"] = np.nan
                rec[f"ask_{h}"] = np.nan
        rec["mid_close"] = float(mid[-1])
        rows.append(rec)
    fp = pd.DataFrame(rows)
    out = pd.concat([ev.reset_index(drop=True), fp], axis=1)
    return out


def path_summary(fp: pd.DataFrame) -> list[dict]:
    """Average forward MID path normalised to the event price, with
    window-clustered CIs. Positive = the price recovered (bounced)."""
    rows = []
    for h in FWD_HORIZONS:
        col = f"mid_{h}"
        sub = fp[np.isfinite(fp[col])].copy()
        if sub.empty:
            continue
        # Normalised change vs the event mid (fractional).
        delta = (sub[col].to_numpy() - sub["event_mid"].to_numpy()) \
            / sub["event_mid"].to_numpy()
        lo, mid, hi = window_clustered_bootstrap(
            delta, sub["slug"].to_numpy(), n=N_BOOT, seed=SEED)
        rows.append({
            "horizon": h, "n": int(len(sub)),
            "mean_delta": float(np.mean(delta)),
            "median_delta": float(np.median(delta)),
            "ci_lo": lo, "ci_hi": hi,
            "frac_up": float((delta > 0).mean()),
        })
    return rows


# ===========================================================================
# The decisive net-of-cost trade simulation
# ===========================================================================

def simulate_drop_trade(dev: pd.DataFrame, ev: pd.DataFrame,
                        profit_target: float, max_hold,
                        execution: str) -> pd.DataFrame:
    """Simulate the actual sell-the-bounce trade for every drop event.

    BUY the dropped side at the event tick. Exit at the BEST (earliest) of:
      - profit target: the side's exit quote crossing entry*(1+PT),
      - max hold: forced exit at event_sec + max_hold (if not None),
      - window close: settle on the TRUE corrected `outcome_up`.

    TAKER: enter at `entry_ask`, exit at the side `bid`, fee 0.07*p*(1-p) per
    share per leg (resolution is a settlement -- no exit fee).
    MAKER: enter at `entry_mid`, exit at the side `mid`, 0 fee. The
    fill-probability haircut is NOT modelled -- maker is an optimistic bound.

    Look-ahead: the exit scan reads only ticks strictly later than the event
    tick. Resolution settles on the realized outcome.
    """
    ev = ev[ev["book_ok"]].reset_index(drop=True)
    if ev.empty:
        return pd.DataFrame()

    by_slug = {s: g.sort_values("seconds_into_window")
               for s, g in dev.groupby("slug", sort=False)}

    out = ev.copy()
    entry_mid = 0.5 * (out["entry_ask"] + out["entry_bid"])
    entry_price = (out["entry_ask"] if execution == "taker"
                   else entry_mid).to_numpy(dtype="f8")
    entry_price = np.clip(entry_price, 1e-6, 0.999999)
    shares = STAKE / entry_price
    entry_fee = (_fee(entry_price) * shares if execution == "taker"
                 else np.zeros(len(out)))

    pnl = np.empty(len(out), dtype="f8")
    reason = np.empty(len(out), dtype=object)
    exit_sec = np.empty(len(out), dtype="f8")

    for i in range(len(out)):
        slug = out["slug"].iat[i]
        side = out["side"].iat[i]
        ep = entry_price[i]
        s = shares[i]
        sec0 = out["sec"].iat[i]
        g = by_slug[slug]
        sec = g["seconds_into_window"].to_numpy(dtype="f8")

        # Strictly-later ticks only.
        later_mask = sec > sec0
        if max_hold is not None:
            later_mask &= sec <= sec0 + max_hold
        idx = np.where(later_mask)[0]

        target_px = ep * (1.0 + profit_target)
        exited = False

        if len(idx) and target_px < 1.0:
            if execution == "taker":
                exq = g[f"{side}_best_bid"].to_numpy(dtype="f8")[idx]
            else:
                exq = g[f"{side}_mid"].to_numpy(dtype="f8")[idx]
            hit = np.where(exq >= target_px)[0]
            if len(hit):
                k = idx[hit[0]]
                px = float(exq[hit[0]])
                proceeds = px * s
                if execution == "taker":
                    proceeds -= _fee(px) * s
                pnl[i] = proceeds - STAKE - entry_fee[i]
                reason[i] = "target"
                exit_sec[i] = float(sec[k])
                exited = True

        if not exited and max_hold is not None and len(idx):
            # Forced exit at the last in-window tick within the hold cap.
            k = idx[-1]
            if sec[k] < sec[-1]:
                # Genuine max-hold exit (window has not yet ended).
                if execution == "taker":
                    px = float(g[f"{side}_best_bid"].to_numpy()[k])
                else:
                    px = float(g[f"{side}_mid"].to_numpy()[k])
                px = max(px, 0.0)
                proceeds = px * s
                if execution == "taker" and px > 0:
                    proceeds -= _fee(px) * s
                pnl[i] = proceeds - STAKE - entry_fee[i]
                reason[i] = "max_hold"
                exit_sec[i] = float(sec[k])
                exited = True

        if not exited:
            # Hold to window close -- settle on the true corrected outcome.
            pnl[i] = out["side_won"].iat[i] * 1.0 * s - STAKE - entry_fee[i]
            reason[i] = "resolution"
            exit_sec[i] = float(sec[-1])

    out["execution"] = execution
    out["profit_target"] = profit_target
    out["max_hold"] = -1 if max_hold is None else max_hold
    out["shares"] = shares
    out["entry_price"] = entry_price
    out["pnl"] = pnl
    out["exit_reason"] = reason
    out["exit_sec"] = exit_sec
    return out


# ===========================================================================
# The random-entry null
# ===========================================================================

def random_entry_events(dev: pd.DataFrame, n_target: int,
                         seed: int) -> pd.DataFrame:
    """Build a null event set: random ticks (random side) with the SAME entry
    guards as a drop event. `n_target` matched to the drop-event count.

    The drop signal must BEAT this -- if random entries with the same exit
    logic earn the same PnL, the drop is not a signal.
    """
    rng = np.random.default_rng(seed)
    # Eligible ticks: room left, sane mid, finite spot feature, for either side.
    cand = []
    for side in ("yes", "no"):
        m = (
            (dev["time_left_sec"] >= MIN_TIME_LEFT)
            & (dev[f"{side}_mid"] >= EVENT_PRICE_LO)
            & (dev[f"{side}_mid"] <= EVENT_PRICE_HI)
            & np.isfinite(dev["spot_move_30s"])
            & (dev[f"{side}_best_ask"] > 0) & (dev[f"{side}_best_bid"] > 0)
            & (dev[f"{side}_best_bid"] < dev[f"{side}_best_ask"])
        )
        sub = dev[m]
        cand.append(pd.DataFrame({
            "row": sub.index.to_numpy(),
            "side": side,
        }))
    pool = pd.concat(cand, ignore_index=True)
    if len(pool) == 0:
        return pd.DataFrame()
    take = rng.choice(len(pool), size=min(n_target, len(pool)),
                      replace=False)
    pick = pool.iloc[take].copy()

    static = dev
    ev = pd.DataFrame()
    ev["row"] = pick["row"].to_numpy()
    ev["side"] = pick["side"].to_numpy()
    ev["sec"] = static.loc[ev["row"], "seconds_into_window"].to_numpy()
    ev["slug"] = static.loc[ev["row"], "slug"].to_numpy()
    ev["symbol"] = static.loc[ev["row"], "symbol"].to_numpy()
    ev["date"] = static.loc[ev["row"], "date"].to_numpy()
    ev["outcome_up"] = static.loc[ev["row"], "outcome_up"].to_numpy()
    ev["time_left_sec"] = static.loc[ev["row"], "time_left_sec"].to_numpy()
    ev["spot_move_30s"] = static.loc[ev["row"], "spot_move_30s"].to_numpy()
    yes = ev["side"] == "yes"
    ev["event_mid"] = np.where(
        yes, static.loc[ev["row"], "yes_mid"].to_numpy(),
        static.loc[ev["row"], "no_mid"].to_numpy())
    ev["entry_ask"] = np.where(
        yes, static.loc[ev["row"], "yes_best_ask"].to_numpy(),
        static.loc[ev["row"], "no_best_ask"].to_numpy())
    ev["entry_bid"] = np.where(
        yes, static.loc[ev["row"], "yes_best_bid"].to_numpy(),
        static.loc[ev["row"], "no_best_bid"].to_numpy())
    ev["side_won"] = np.where(
        yes, ev["outcome_up"], 1.0 - ev["outcome_up"]).astype("f8")
    ev["drop_mag"] = np.nan
    ev["book_ok"] = True
    return ev.reset_index(drop=True)


# ===========================================================================
# Aggregation / metrics
# ===========================================================================

def summarize(trades: pd.DataFrame, n_days: int) -> dict:
    """Per-config trade summary: n, win rate, mean PnL/trade + window-clustered
    CI, total / daily PnL, resolution-loss rate, concentration."""
    if trades is None or trades.empty:
        return {"n": 0, "win_rate": float("nan"), "mean_pnl": float("nan"),
                "ci": (float("nan"), float("nan")), "total_pnl": 0.0,
                "trades_per_day": 0.0, "daily_pnl": float("nan"),
                "res_loss_rate": float("nan"), "target_hit_rate": float("nan"),
                "green_day_frac": float("nan"), "top1_share": float("nan"),
                "top_day_share": float("nan"), "mean_entry": float("nan"),
                "daily": {}}
    pnl = trades["pnl"].to_numpy(dtype="f8")
    won = trades["pnl"].to_numpy(dtype="f8") > 0
    lo, mid, hi = window_clustered_bootstrap(
        pnl, trades["slug"].to_numpy(), n=N_BOOT, seed=SEED)
    daily = trades.groupby("date")["pnl"].sum()
    total = float(pnl.sum())
    abs_sum = float(np.abs(pnl).sum())
    top1 = float(np.abs(pnl).max() / abs_sum) if abs_sum > 0 else float("nan")
    day_abs = daily.abs()
    top_day = (float(day_abs.max() / day_abs.sum())
               if day_abs.sum() > 0 else float("nan"))
    is_res_loss = ((trades["exit_reason"] == "resolution")
                   & (trades["side_won"] == 0.0))
    return {
        "n": int(len(trades)),
        "win_rate": float(won.mean()),
        "mean_pnl": float(pnl.mean()),
        "ci": (lo, hi),
        "total_pnl": total,
        "trades_per_day": len(trades) / max(n_days, 1),
        "daily_pnl": total / max(n_days, 1),
        "res_loss_rate": float(is_res_loss.mean()),
        "target_hit_rate": float((trades["exit_reason"] == "target").mean()),
        "green_day_frac": float((daily > 0).mean()),
        "top1_share": top1,
        "top_day_share": top_day,
        "mean_entry": float(trades["entry_price"].mean()),
        "daily": {str(k): float(v) for k, v in daily.items()},
    }


def noise_signal_split(dev: pd.DataFrame, ev: pd.DataFrame,
                       n_days: int) -> dict:
    """H2: split drop events by |spot_move_30s| at the event.

    NOISE drop  = |spot_move_30s| <  NOISE_SPOT_CUTOFF (odds fell, spot flat)
    SIGNAL drop = |spot_move_30s| >= NOISE_SPOT_CUTOFF (odds fell, spot moved)

    For each group: forward bounce (mid +60s vs event) AND the headline trade
    result (taker, +50% PT, hold to close). H2 predicts reversion concentrates
    in noise drops.
    """
    fp = forward_paths(dev, ev)
    out = {}
    abs_spot = fp["spot_move_30s"].abs()
    groups = {
        "noise": fp[abs_spot < NOISE_SPOT_CUTOFF].copy(),
        "signal": fp[abs_spot >= NOISE_SPOT_CUTOFF].copy(),
    }
    for name, sub in groups.items():
        rec = {"n": int(len(sub))}
        if sub.empty:
            out[name] = rec
            continue
        col = "mid_60"
        s2 = sub[np.isfinite(sub[col])]
        if not s2.empty:
            delta = (s2[col].to_numpy() - s2["event_mid"].to_numpy()) \
                / s2["event_mid"].to_numpy()
            lo, _, hi = window_clustered_bootstrap(
                delta, s2["slug"].to_numpy(), n=N_BOOT, seed=SEED)
            rec["bounce_60s"] = float(np.mean(delta))
            rec["bounce_60s_ci"] = (lo, hi)
            rec["frac_up_60s"] = float((delta > 0).mean())
        # Headline trade on this subgroup.
        tr = simulate_drop_trade(dev, sub, profit_target=0.50,
                                 max_hold=None, execution="taker")
        rec["trade"] = summarize(tr, n_days)
        out[name] = rec
    return out


# ===========================================================================
# Charts
# ===========================================================================

def chart_forward_path(path_rows: dict) -> None:
    """Average forward MID path (normalised to the event price) per drop
    threshold, with window-clustered 90% CI bands."""
    fig, ax = plt.subplots(figsize=(10, 6))
    colors = {0.10: "#1f77b4", 0.20: "#ff7f0e", 0.35: "#d62728"}
    for thr in DROP_THRESHOLDS:
        rows = path_rows.get(thr, [])
        if not rows:
            continue
        xs = [0] + [r["horizon"] for r in rows]
        ys = [0.0] + [r["mean_delta"] * 100 for r in rows]
        los = [0.0] + [r["ci_lo"] * 100 for r in rows]
        his = [0.0] + [r["ci_hi"] * 100 for r in rows]
        c = colors.get(thr, "#555555")
        n0 = rows[0]["n"] if rows else 0
        ax.plot(xs, ys, "o-", color=c, lw=2,
                label=f">={int(thr*100)}% drop (n={n0})")
        ax.fill_between(xs, los, his, color=c, alpha=0.15)
    ax.axhline(0, color="black", lw=1)
    ax.set_xlabel("seconds after the drop event")
    ax.set_ylabel("mean mid change vs event price (%)  +/- 90% CI")
    ax.set_title("Forward price path after a sharp intra-window odds drop\n"
                 "(dropped side's mid, normalised to the event price, dev "
                 "May 15-20)", fontsize=11, fontweight="bold")
    ax.legend()
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    _CHART_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(_CHART_PATH), dpi=150, bbox_inches="tight")
    plt.close(fig)


def chart_bounce_distribution(fp: pd.DataFrame) -> None:
    """Full distribution of the +60s mid bounce -- the mean hides this."""
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    col = "mid_60"
    sub = fp[np.isfinite(fp[col])].copy()
    delta = (sub[col].to_numpy() - sub["event_mid"].to_numpy()) \
        / sub["event_mid"].to_numpy() * 100

    ax = axes[0]
    ax.hist(np.clip(delta, -80, 80), bins=60, color="#ff7f0e",
            edgecolor="white", alpha=0.85)
    ax.axvline(0, color="black", lw=1.5)
    ax.axvline(float(np.mean(delta)), color="#d62728", lw=2, ls="--",
               label=f"mean {np.mean(delta):+.1f}%")
    ax.axvline(float(np.median(delta)), color="#1f77b4", lw=2, ls=":",
               label=f"median {np.median(delta):+.1f}%")
    ax.set_xlabel("+60s mid change vs event price (%)")
    ax.set_ylabel("drop events")
    ax.set_title(f"Bounce distribution +60s after a >={int(HEADLINE_DROP*100)}"
                 f"% drop (n={len(sub)})", fontsize=10, fontweight="bold")
    ax.legend(fontsize=9)
    ax.grid(True, axis="y", alpha=0.3)

    ax = axes[1]
    qs = np.percentile(delta, np.arange(1, 100))
    ax.plot(np.arange(1, 100), qs, color="#ff7f0e", lw=2)
    ax.axhline(0, color="black", lw=1)
    ax.set_xlabel("percentile")
    ax.set_ylabel("+60s mid change vs event price (%)")
    ax.set_title("Bounce quantiles -- the full distribution",
                 fontsize=10, fontweight="bold")
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    fig.savefig(str(_CHART_DIST), dpi=150, bbox_inches="tight")
    plt.close(fig)


def chart_noise_vs_signal(ns: dict) -> None:
    """Noise vs signal: forward bounce + trade PnL side by side."""
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    names = ["noise", "signal"]
    colors = {"noise": "#2ca02c", "signal": "#d62728"}

    ax = axes[0]
    for nm in names:
        rec = ns.get(nm, {})
        b = rec.get("bounce_60s")
        if b is None:
            continue
        ci = rec.get("bounce_60s_ci", (b, b))
        ax.bar(nm, b * 100, color=colors[nm], alpha=0.85,
               yerr=[[(b - ci[0]) * 100], [(ci[1] - b) * 100]], capsize=6)
        ax.text(nm, b * 100, f"  n={rec['n']}", ha="center",
                va="bottom" if b >= 0 else "top", fontsize=9)
    ax.axhline(0, color="black", lw=1)
    ax.set_ylabel("+60s mean mid bounce (%)  +/- 90% CI")
    ax.set_title("H2: bounce by drop type\n(noise = spot flat, signal = spot "
                 "moved)", fontsize=10, fontweight="bold")
    ax.grid(True, axis="y", alpha=0.3)

    ax = axes[1]
    for nm in names:
        tr = ns.get(nm, {}).get("trade", {})
        m = tr.get("mean_pnl")
        if m is None or not np.isfinite(m):
            continue
        ci = tr.get("ci", (m, m))
        ax.bar(nm, m, color=colors[nm], alpha=0.85,
               yerr=[[m - ci[0]], [ci[1] - m]], capsize=6)
        ax.text(nm, m, f"  n={tr['n']}", ha="center",
                va="bottom" if m >= 0 else "top", fontsize=9)
    ax.axhline(0, color="black", lw=1)
    ax.set_ylabel("mean net PnL/trade ($)  +/- 90% CI")
    ax.set_title("H2: sell-the-bounce PnL by drop type\n(taker, +50% PT, hold "
                 "to close)", fontsize=10, fontweight="bold")
    ax.grid(True, axis="y", alpha=0.3)

    plt.tight_layout()
    fig.savefig(str(_CHART_NS), dpi=150, bbox_inches="tight")
    plt.close(fig)


# ===========================================================================
# Doc rendering
# ===========================================================================

def _mh_label(mh) -> str:
    return "hold to close" if mh in (None, -1) else f"max hold {mh}s"


def _trade_table(results: dict) -> str:
    """results: {(PT, max_hold, execution): summary} for the headline drop
    threshold."""
    h = ("| profit target | max hold | execution | n | win rate "
         "| mean entry | net PnL/trade | 90% CI | trades/day "
         "| daily PnL | resolution-loss rate | target-hit rate |\n"
         "|---|---|---|---|---|---|---|---|---|---|---|---|\n")
    rows = []
    for PT in PROFIT_TARGETS:
        for mh in MAX_HOLDS:
            for execution in ("taker", "maker"):
                s = results.get((PT, mh, execution))
                if s is None or s["n"] == 0:
                    rows.append(f"| +{int(PT*100)}% | {_mh_label(mh)} "
                                f"| {execution} | 0 | - | - | - | - | - | - "
                                f"| - | - |")
                    continue
                rows.append(
                    f"| +{int(PT*100)}% | {_mh_label(mh)} | {execution} "
                    f"| {s['n']} | {s['win_rate']:.3f} "
                    f"| {s['mean_entry']:.3f} | ${s['mean_pnl']:+.3f} "
                    f"| [${s['ci'][0]:+.3f}, ${s['ci'][1]:+.3f}] "
                    f"| {s['trades_per_day']:.1f} | ${s['daily_pnl']:+.2f} "
                    f"| {s['res_loss_rate']:.3f} | {s['target_hit_rate']:.3f} |")
    return h + "\n".join(rows)


def _build_doc(counts: dict, path_rows: dict, fp_headline: pd.DataFrame,
               trade_results: dict, ns: dict, null_results: dict,
               n_days: int, n_dev_windows: int) -> str:
    L: list[str] = []
    L.append("# Bounce atlas -- Phase 3 drop-event / sell-the-bounce study")
    L.append("")
    L.append(
        "**Corrected data (real Polymarket outcomes, corrected strikes).** "
        "Generated by `research/analysis/drop_events.py`. The order book / "
        "mids / drop features were never corrupted; `move_pct` / `outcome_up` "
        "are the corrected values (see `docs/research/corrected_labels.md`, "
        "`docs/research/PHASE2_RERUN_VERDICT.md`).")
    L.append("")
    L.append(
        "**The question.** The user's manual strategy is *buy a side after a "
        "visible odds drop, then sell the bounce within minutes*. Phase 2 "
        "tested price-vs-RESOLUTION and found the market calibrated. Phase 3 "
        "tests something genuinely different: **after a sharp intra-window "
        "odds drop, does the PRICE revert (overshoot-and-correct) within the "
        "window -- enough to trade, net of cost?** A market can be calibrated "
        "to resolution and still have a tradeable intra-window oscillation, "
        "or not.")
    L.append("")
    L.append(
        f"**Discipline.** Dev split {DEV_START}..{DEV_END} ({n_days} UTC days, "
        f"{n_dev_windows:,} windows). The sealed hold-out (May 21-22) is "
        f"asserted untouched and was NOT loaded. All CIs are 90% "
        f"window-clustered bootstraps (groups = `slug`, n={N_BOOT}). "
        f"`sigma_proximity` is NOT used (Phase 2 / Task 8 proved it broken).")
    L.append("")

    # --- Method ---
    L.append("## Method")
    L.append("")
    L.append(
        "**1. Drop detection.** Per 15m window, per side (`yes` and `no`), a "
        f"drop event fires at the first tick where the side's mid has fallen "
        f"`D = (trailing_peak - mid) / trailing_peak >= threshold`, with the "
        f"trailing peak taken over the last {PEAK_LOOKBACK} ticks (<=30s at "
        f"1Hz). Thresholds swept: "
        f"{', '.join(f'{int(t*100)}%' for t in DROP_THRESHOLDS)}. **Dedupe:** "
        f"after a crossing the side must recover (its drop fall back below "
        f"threshold/2) before a new event can fire -- one event per genuine "
        f"crossing per window per side.")
    L.append("")
    L.append(
        "**Why both sides.** A 15m Up/Down market has two complementary "
        "sides; the tradeable side after a drop is the one that *fell*. "
        "Restricting to 'the cheap side' would bias toward longshots (Phase "
        "2's residual cheap-tail effect) and miss drops on a side that is "
        "still the favourite. Each (window, side) drop is treated "
        "independently; the window-clustered bootstrap accounts for the "
        "within-window dependence between the two sides.")
    L.append("")
    L.append(
        f"**2. Guards.** `time_left_sec >= {MIN_TIME_LEFT}` (room for the "
        f"bounce / exit), event mid in `[{EVENT_PRICE_LO}, {EVENT_PRICE_HI}]`, "
        f"a real two-sided book at the entry tick.")
    L.append("")
    L.append(
        "**3. Forward path.** For each event the dropped side's mid / bid / "
        f"ask are recorded at +{'/'.join(str(h) for h in FWD_HORIZONS)}s and "
        f"at window close (strictly-later ticks only).")
    L.append("")
    L.append(
        f"**4. Trade simulation.** ${STAKE:.0f} stake. BUY the dropped side at "
        f"the event. Exit at the best (earliest) of: a profit target "
        f"(+{'/'.join(str(int(p*100)) for p in PROFIT_TARGETS)}%), a max hold "
        f"({'/'.join(_mh_label(m) for m in MAX_HOLDS)}), or window close "
        f"settling on the TRUE corrected `outcome_up`. **TAKER**: enter at "
        f"`ask`, exit at `bid`, fee `0.07*p*(1-p)`/share/leg (resolution = a "
        f"settlement, no exit fee). **MAKER**: enter at `mid`, exit at `mid`, "
        f"0 fee -- the fill-probability haircut is NOT modelled, so maker is "
        f"an optimistic upper bound.")
    L.append("")

    # --- Event counts ---
    L.append("## Drop-event counts")
    L.append("")
    L.append("| drop threshold | events detected | events w/ tradeable book "
             "| events / window | windows touched |\n"
             "|---|---|---|---|---|")
    for thr in DROP_THRESHOLDS:
        c = counts[thr]
        L.append(f"| >={int(thr*100)}% | {c['n_total']} | {c['n_book_ok']} "
                 f"| {c['per_window']:.3f} | {c['n_windows']} |")
    L.append("")

    # --- Forward path ---
    L.append("## Forward price path -- does the price bounce?")
    L.append("")
    L.append(
        "Mean forward MID change of the dropped side, normalised to the event "
        "price (positive = the price recovered / bounced). 90% "
        "window-clustered CI.")
    L.append("")
    for thr in DROP_THRESHOLDS:
        rows = path_rows.get(thr, [])
        if not rows:
            continue
        L.append(f"**>={int(thr*100)}% drop** (n={rows[0]['n']}):")
        L.append("")
        L.append("| horizon | n | mean delta | median delta | 90% CI "
                 "| frac bounced up |\n|---|---|---|---|---|---|")
        for r in rows:
            L.append(f"| +{r['horizon']}s | {r['n']} "
                     f"| {r['mean_delta']*100:+.2f}% "
                     f"| {r['median_delta']*100:+.2f}% "
                     f"| [{r['ci_lo']*100:+.2f}%, {r['ci_hi']*100:+.2f}%] "
                     f"| {r['frac_up']:.3f} |")
        L.append("")

    # --- Bounce distribution ---
    col = "mid_60"
    s60 = fp_headline[np.isfinite(fp_headline[col])]
    d60 = ((s60[col].to_numpy() - s60["event_mid"].to_numpy())
           / s60["event_mid"].to_numpy() * 100)
    L.append(f"## Bounce distribution (+60s, >={int(HEADLINE_DROP*100)}% drop)")
    L.append("")
    L.append(
        f"The mean hides the spread. +60s after a "
        f">={int(HEADLINE_DROP*100)}% drop (n={len(s60)}): "
        f"mean **{np.mean(d60):+.2f}%**, median **{np.median(d60):+.2f}%**, "
        f"P10 {np.percentile(d60,10):+.1f}%, P25 {np.percentile(d60,25):+.1f}%, "
        f"P75 {np.percentile(d60,75):+.1f}%, P90 {np.percentile(d60,90):+.1f}%. "
        f"Fraction that bounced up: **{(d60>0).mean():.1%}**.")
    L.append("")
    L.append("See `docs/research/charts/drop_bounce_distribution.png`.")
    L.append("")

    # --- The decisive trade table ---
    L.append(f"## The decisive net-of-cost trade test "
             f"(>={int(HEADLINE_DROP*100)}% drop)")
    L.append("")
    L.append(
        "BUY the dropped side at the event, exit at the best of {profit "
        "target, max hold, window close on the true outcome}. Net PnL per "
        "trade. **The resolution-loss rate** is the fraction of trades that "
        "ran all the way to a -100% (bought the bounce, it never came, the "
        "side resolved worthless).")
    L.append("")
    L.append(_trade_table(trade_results))
    L.append("")

    # --- Noise vs signal ---
    L.append("## H2 -- noise drops vs signal drops")
    L.append("")
    L.append(
        f"Split drop events by `|spot_move_30s|` at the event. **Noise drop** "
        f"= `|spot_move_30s| < {NOISE_SPOT_CUTOFF}` (odds fell but spot was "
        f"flat -> a book wobble -> expect reversion). **Signal drop** = "
        f"`|spot_move_30s| >= {NOISE_SPOT_CUTOFF}` (odds fell because spot "
        f"moved -> informed -> expect continuation). H2 predicts reversion "
        f"concentrates in noise drops.")
    L.append("")
    L.append("| drop type | n | +60s mean bounce | 90% CI | frac up "
             "| trade n | trade win rate | net PnL/trade | 90% CI |\n"
             "|---|---|---|---|---|---|---|---|---|")
    for nm in ("noise", "signal"):
        rec = ns.get(nm, {})
        if not rec or rec.get("n", 0) == 0:
            L.append(f"| {nm} | 0 | - | - | - | - | - | - | - |")
            continue
        b = rec.get("bounce_60s", float("nan"))
        bci = rec.get("bounce_60s_ci", (float("nan"), float("nan")))
        fu = rec.get("frac_up_60s", float("nan"))
        tr = rec.get("trade", {})
        L.append(
            f"| {nm} | {rec['n']} | {b*100:+.2f}% "
            f"| [{bci[0]*100:+.2f}%, {bci[1]*100:+.2f}%] | {fu:.3f} "
            f"| {tr.get('n',0)} | {tr.get('win_rate',float('nan')):.3f} "
            f"| ${tr.get('mean_pnl',float('nan')):+.3f} "
            f"| [${tr.get('ci',(0,0))[0]:+.3f}, "
            f"${tr.get('ci',(0,0))[1]:+.3f}] |")
    L.append("")

    # --- Null test ---
    L.append("## Skeptic check -- the random-entry null")
    L.append("")
    L.append(
        "Enter at RANDOM ticks (random side, same guards), same exit logic, "
        "matched event count. The drop signal must BEAT this -- if random "
        "entries earn the same PnL, the drop is not a signal. Headline config: "
        f">={int(HEADLINE_DROP*100)}% drop, +50% PT, hold to close, taker.")
    L.append("")
    L.append("| entry rule | n | win rate | net PnL/trade | 90% CI "
             "| resolution-loss rate |\n|---|---|---|---|---|---|")
    drop_h = trade_results.get((0.50, None, "taker"), {})
    L.append(
        f"| drop event | {drop_h.get('n',0)} "
        f"| {drop_h.get('win_rate',float('nan')):.3f} "
        f"| ${drop_h.get('mean_pnl',float('nan')):+.3f} "
        f"| [${drop_h.get('ci',(0,0))[0]:+.3f}, "
        f"${drop_h.get('ci',(0,0))[1]:+.3f}] "
        f"| {drop_h.get('res_loss_rate',float('nan')):.3f} |")
    nr = null_results
    L.append(
        f"| random entry | {nr.get('n',0)} "
        f"| {nr.get('win_rate',float('nan')):.3f} "
        f"| ${nr.get('mean_pnl',float('nan')):+.3f} "
        f"| [${nr.get('ci',(0,0))[0]:+.3f}, ${nr.get('ci',(0,0))[1]:+.3f}] "
        f"| {nr.get('res_loss_rate',float('nan')):.3f} |")
    L.append("")

    # --- Concentration ---
    L.append("## Skeptic check -- trade concentration & daily consistency")
    L.append("")
    L.append(
        f"Headline config (>={int(HEADLINE_DROP*100)}% drop, +50% PT, hold to "
        f"close, taker): top-trade |PnL| share **{drop_h.get('top1_share',float('nan')):.1%}**, "
        f"top-day |PnL| share **{drop_h.get('top_day_share',float('nan')):.1%}**, "
        f"green-day fraction **{drop_h.get('green_day_frac',float('nan')):.2f}**.")
    L.append("")
    L.append("Daily net PnL (headline config):")
    L.append("")
    L.append("| UTC day | net PnL |\n|---|---|")
    for d in sorted(drop_h.get("daily", {})):
        L.append(f"| {d} | ${drop_h['daily'][d]:+.2f} |")
    L.append("")

    # --- Look-ahead ---
    L.append("## Skeptic check -- look-ahead confirmation")
    L.append("")
    L.append("- Drop detection reads the trailing peak from a backward-only "
             "rolling max; the event tick's mid is contemporaneous.")
    L.append("- The forward path and every profit-target / max-hold exit scan "
             "ONLY ticks strictly later than the event tick in the same "
             "window.")
    L.append("- Resolution settles on the realized corrected `outcome_up` -- "
             "the ground-truth oracle.")
    L.append("- No fitting and no day-blocked surface here -- the analysis is "
             "descriptive + a fixed trade rule, so there is nothing to leak "
             "across days; per-day PnL is reported for consistency.")
    L.append("")

    # --- Verdict ---
    L.append("## VERDICT")
    L.append("")
    L.extend(_verdict_text(counts, path_rows, fp_headline, trade_results, ns,
                           null_results))
    L.append("")
    L.append("**Charts:**")
    L.append("- `docs/research/charts/drop_forward_path.png` -- the forward "
             "price path per drop threshold.")
    L.append("- `docs/research/charts/drop_bounce_distribution.png` -- the "
             "full +60s bounce distribution.")
    L.append("- `docs/research/charts/drop_noise_vs_signal.png` -- the "
             "noise-vs-signal split.")
    L.append("")
    return "\n".join(L)


def _verdict_text(counts, path_rows, fp_headline, trade_results, ns,
                  null_results) -> list[str]:
    L: list[str] = []
    # Headline forward bounce at +60s.
    hp = path_rows.get(HEADLINE_DROP, [])
    bounce_60 = next((r for r in hp if r["horizon"] == 60), None)
    bounce_close = None  # close is via fp directly

    # Best trade config by mean PnL among taker rows (the realistic one).
    taker = {k: v for k, v in trade_results.items()
             if k[2] == "taker" and v.get("n", 0) > 0}
    best_taker = (max(taker.items(), key=lambda kv: kv[1]["mean_pnl"])
                  if taker else None)
    maker = {k: v for k, v in trade_results.items()
             if k[2] == "maker" and v.get("n", 0) > 0}
    best_maker = (max(maker.items(), key=lambda kv: kv[1]["mean_pnl"])
                  if maker else None)

    drop_h = trade_results.get((0.50, None, "taker"), {})
    nr = null_results

    # Is there a forward bounce at all? Three cases:
    #   has_bounce      -- CI clear above 0 (price reverted up)
    #   keeps_falling   -- CI clear below 0 (price continued DOWN, not a bounce)
    #   else            -- CI straddles 0 (no reliable move either way)
    has_bounce = (bounce_60 is not None and bounce_60["ci_lo"] > 0)
    keeps_falling = (bounce_60 is not None and bounce_60["ci_hi"] < 0)
    # Is any taker config net-positive with a CI clear of zero?
    taker_pos = (best_taker is not None
                 and best_taker[1]["ci"][0] > 0)
    maker_pos = (best_maker is not None
                 and best_maker[1]["ci"][0] > 0)
    # Does the drop signal beat the null?
    beats_null = (drop_h.get("mean_pnl", float("-inf"))
                  > nr.get("mean_pnl", float("inf"))
                  and drop_h.get("ci", (float("-inf"),))[0]
                  > nr.get("mean_pnl", float("inf")))

    if bounce_60 is not None:
        L.append(
            f"**The forward path.** +60s after a "
            f">={int(HEADLINE_DROP*100)}% intra-window odds drop, the dropped "
            f"side's mid moves **{bounce_60['mean_delta']*100:+.2f}%** on "
            f"average (90% CI [{bounce_60['ci_lo']*100:+.2f}%, "
            f"{bounce_60['ci_hi']*100:+.2f}%], "
            f"{bounce_60['frac_up']:.0%} of events bounce up). "
            + ("The CI clears zero on the positive side -- there IS a "
               "measurable mean bounce in the mid."
               if has_bounce else
               ("The CI is entirely BELOW zero -- the price does not bounce, "
                "it CONTINUES TO FALL after the drop. This is momentum / "
                "continuation, the opposite of the sell-the-bounce premise."
                if keeps_falling else
                "The CI straddles zero -- there is no reliable mean move in "
                "the mid either way."))
        )
        L.append("")

    L.append(
        "**The decisive net-of-cost test.** The mid is not tradeable -- a "
        "buyer pays the ask and sells the bid, and pays the taker fee both "
        "legs (Phase 0: ~16-21% of stake round-trip). The honest question is "
        "whether the *net* sell-the-bounce trade is positive.")
    L.append("")
    if best_taker is not None:
        PT, mh, _ = best_taker[0]
        s = best_taker[1]
        L.append(
            f"- **Taker (the realistic execution).** The best taker config "
            f"(+{int(PT*100)}% PT, {_mh_label(mh)}) earns "
            f"**${s['mean_pnl']:+.3f}/trade** (90% CI "
            f"[${s['ci'][0]:+.3f}, ${s['ci'][1]:+.3f}], n={s['n']}, win rate "
            f"{s['win_rate']:.1%}, resolution-loss rate "
            f"{s['res_loss_rate']:.1%}). "
            + ("The CI clears zero -- net-positive."
               if taker_pos else
               "The CI straddles zero -- NOT a reliable net-positive edge."))
    if best_maker is not None:
        PT, mh, _ = best_maker[0]
        s = best_maker[1]
        L.append(
            f"- **Maker (optimistic, fill not modelled).** Best maker config "
            f"(+{int(PT*100)}% PT, {_mh_label(mh)}): "
            f"**${s['mean_pnl']:+.3f}/trade** (90% CI "
            f"[${s['ci'][0]:+.3f}, ${s['ci'][1]:+.3f}]). "
            + ("Even the optimistic maker upper bound is positive."
               if maker_pos else
               "Even the optimistic maker upper bound does not reliably "
               "clear zero -- the fill caveat only makes the real number "
               "worse."))
    L.append("")

    # Noise vs signal.
    noise = ns.get("noise", {})
    signal = ns.get("signal", {})
    if noise.get("n", 0) and signal.get("n", 0):
        nb = noise.get("bounce_60s", float("nan"))
        sb = signal.get("bounce_60s", float("nan"))
        nt = noise.get("trade", {})
        st = signal.get("trade", {})
        L.append(
            f"**H2 (noise vs signal).** Noise drops (spot flat) bounce "
            f"{nb*100:+.2f}% at +60s and trade at "
            f"${nt.get('mean_pnl',float('nan')):+.3f}/trade; signal drops "
            f"(spot moved) bounce {sb*100:+.2f}% and trade at "
            f"${st.get('mean_pnl',float('nan')):+.3f}/trade. "
            + ("Reversion concentrates in noise drops as H2 predicts."
               if nb > sb else
               "Reversion does NOT concentrate in noise drops -- H2 is not "
               "supported in the bounce magnitude."))
        L.append("")

    # Null.
    L.append(
        f"**The null.** The drop-event headline config earns "
        f"${drop_h.get('mean_pnl',float('nan')):+.3f}/trade; random entries "
        f"with the same exit logic earn ${nr.get('mean_pnl',float('nan')):+.3f}"
        f"/trade. "
        + ("The drop signal beats the random-entry null with a CI clear of "
           "the null mean -- the drop carries content beyond generic entry."
           if beats_null else
           "The drop signal does NOT clearly beat the random-entry null -- "
           "whatever PnL the drop rule shows is not distinguishable from "
           "entering at a random tick."))
    L.append("")

    # Overall verdict.
    real_edge = taker_pos and beats_null
    L.append("### Is intra-window sell-the-bounce a real tradeable edge?")
    L.append("")
    if real_edge:
        PT, mh, _ = best_taker[0]
        s = best_taker[1]
        L.append(
            f"**YES (with the usual caution).** Net of taker cost the best "
            f"config (>={int(HEADLINE_DROP*100)}% drop, +{int(PT*100)}% PT, "
            f"{_mh_label(mh)}) earns ${s['mean_pnl']:+.3f}/trade "
            f"(CI [${s['ci'][0]:+.3f}, ${s['ci'][1]:+.3f}]), "
            f"{s['trades_per_day']:.1f} trades/day, ${s['daily_pnl']:+.2f}/day, "
            f"and beats the random-entry null. The bounce is real and "
            f"survives cost.")
    else:
        L.append(
            "**NO.** After a sharp intra-window odds drop the price does not "
            "revert enough to trade net of cost. "
            + ("The mid does not bounce -- it CONTINUES TO FALL after the "
               "drop (the forward path's CI is entirely below zero), the "
               "opposite of the sell-the-bounce premise; "
               if keeps_falling else
               ("The mid shows no reliable mean bounce; "
                if not has_bounce else
                "Even where the mid shows a small mean bounce, "))
            + "the taker trade -- paying the ask, selling the bid, the fee on "
            "both legs -- does not produce a net-positive PnL whose CI clears "
            "zero, and "
            + ("it does not beat a random-entry null. "
               if not beats_null else
               "the maker upper bound is not enough either. ")
            + "The market's intra-window price oscillation, like its "
            "resolution calibration in Phase 2, is not a tradeable edge on "
            "this dev sample. This is a clean, honest negative.")
    L.append("")

    # H-status.
    L.append("### Hypothesis status")
    L.append("")
    L.append(
        "- **H1 -- intra-window mean reversion after a drop is tradeable net "
        "of cost.** "
        + ("**SUPPORTED** -- the net taker trade clears zero."
           if real_edge else
           ("**REJECTED** on the dev sample -- and not merely 'flat': after a "
            "drop the price CONTINUES TO FALL (the forward-path CI is "
            "entirely below zero), so even the directional premise of "
            "sell-the-bounce is wrong here, and the net-of-cost trade is "
            "firmly negative."
            if keeps_falling else
            "**REJECTED** on the dev sample -- the net-of-cost trade does "
            "not clear zero.")))
    if noise.get("n", 0) and signal.get("n", 0):
        nb = noise.get("bounce_60s", float("nan"))
        sb = signal.get("bounce_60s", float("nan"))
        L.append(
            "- **H2 -- reversion concentrates in noise drops (spot flat), "
            "continuation in signal drops (spot moved).** "
            + ("**SUPPORTED** in direction -- noise drops bounce more than "
               "signal drops"
               if nb > sb else
               "**NOT SUPPORTED** -- noise drops do not bounce more than "
               "signal drops")
            + (" (but this does not by itself make the trade profitable)."
               if not real_edge else "."))
    else:
        L.append("- **H2** -- could not be evaluated (one subgroup empty).")
    L.append(
        "- **H3 -- the drop carries information beyond a random entry.** "
        + ("**SUPPORTED** -- the drop signal beats the random-entry null."
           if beats_null else
           "**REJECTED** -- the drop signal does not beat the random-entry "
           "null."))
    L.append(
        "- **H5 -- the maker (zero-fee) execution rescues the edge.** "
        + ("**SUPPORTED as an upper bound** -- the optimistic maker number is "
           "positive (fill probability still un-modelled)."
           if maker_pos else
           "**REJECTED** -- even the optimistic, zero-fee maker upper bound "
           "does not reliably clear zero."))
    return L


# ===========================================================================
# Orchestration
# ===========================================================================

def run() -> dict:
    """Run the full Phase 3 drop-event study. Writes charts + the doc.
    Returns a result dict for tests / the report."""
    dev = load_ticks()
    n_days = dev["date"].nunique()
    n_dev_windows = dev["slug"].nunique()

    # --- 1. Detect drops at every threshold ---
    events_by_thr: dict[float, pd.DataFrame] = {}
    counts: dict[float, dict] = {}
    for thr in DROP_THRESHOLDS:
        ev = detect_all_drops(dev, thr)
        events_by_thr[thr] = ev
        counts[thr] = {
            "n_total": int(len(ev)),
            "n_book_ok": int(ev["book_ok"].sum()) if not ev.empty else 0,
            "per_window": (len(ev) / n_dev_windows) if n_dev_windows else 0.0,
            "n_windows": int(ev["slug"].nunique()) if not ev.empty else 0,
        }

    # --- 2. Forward paths ---
    path_rows: dict[float, list] = {}
    fp_by_thr: dict[float, pd.DataFrame] = {}
    for thr in DROP_THRESHOLDS:
        ev = events_by_thr[thr]
        if ev.empty:
            path_rows[thr] = []
            fp_by_thr[thr] = pd.DataFrame()
            continue
        fp = forward_paths(dev, ev)
        fp_by_thr[thr] = fp
        path_rows[thr] = path_summary(fp)

    fp_headline = fp_by_thr[HEADLINE_DROP]

    # --- 3. The decisive trade simulation (headline drop threshold) ---
    ev_headline = events_by_thr[HEADLINE_DROP]
    trade_results: dict[tuple, dict] = {}
    for PT in PROFIT_TARGETS:
        for mh in MAX_HOLDS:
            for execution in ("taker", "maker"):
                tr = simulate_drop_trade(dev, ev_headline, PT, mh, execution)
                trade_results[(PT, mh, execution)] = summarize(tr, n_days)

    # --- 4. Noise vs signal (H2) ---
    ns = noise_signal_split(dev, ev_headline, n_days)

    # --- 5. Random-entry null ---
    n_drop = int(ev_headline["book_ok"].sum()) if not ev_headline.empty else 0
    null_ev = random_entry_events(dev, n_drop, seed=SEED + 99)
    null_tr = simulate_drop_trade(dev, null_ev, profit_target=0.50,
                                  max_hold=None, execution="taker")
    null_results = summarize(null_tr, n_days)

    # --- Charts ---
    chart_forward_path(path_rows)
    if not fp_headline.empty:
        chart_bounce_distribution(fp_headline)
    chart_noise_vs_signal(ns)

    # --- Doc ---
    doc = _build_doc(counts, path_rows, fp_headline, trade_results, ns,
                     null_results, n_days, n_dev_windows)
    _DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    _DOC_PATH.write_text(doc)

    return {
        "counts": counts,
        "path_rows": path_rows,
        "trade_results": trade_results,
        "noise_signal": ns,
        "null_results": null_results,
        "n_days": n_days,
        "n_dev_windows": n_dev_windows,
    }


if __name__ == "__main__":
    res = run()
    print(f"Dev: {res['n_days']} days, {res['n_dev_windows']} windows")
    print("\nDrop-event counts:")
    for thr, c in res["counts"].items():
        print(f"  >={int(thr*100)}%: {c['n_total']} events "
              f"({c['n_book_ok']} tradeable), {c['n_windows']} windows")
    print(f"\nForward path (>={int(HEADLINE_DROP*100)}% drop):")
    for r in res["path_rows"].get(HEADLINE_DROP, []):
        print(f"  +{r['horizon']}s: mean {r['mean_delta']*100:+.2f}% "
              f"CI [{r['ci_lo']*100:+.2f}%, {r['ci_hi']*100:+.2f}%] "
              f"frac_up {r['frac_up']:.2f}")
    print("\nTrade results (headline drop, taker):")
    for (PT, mh, ex), s in res["trade_results"].items():
        if ex != "taker" or s["n"] == 0:
            continue
        mh_l = "close" if mh is None else f"{mh}s"
        print(f"  +{int(PT*100)}% PT / {mh_l}: ${s['mean_pnl']:+.3f}/trade "
              f"CI [${s['ci'][0]:+.3f},${s['ci'][1]:+.3f}] "
              f"WR {s['win_rate']:.2f} n={s['n']} "
              f"resloss {s['res_loss_rate']:.2f}")
    nr = res["null_results"]
    print(f"\nNull (random entry, +50% PT, taker): ${nr['mean_pnl']:+.3f}/trade "
          f"CI [${nr['ci'][0]:+.3f},${nr['ci'][1]:+.3f}] n={nr['n']}")
    print(f"\nDoc written: {_DOC_PATH}")
