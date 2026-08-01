"""SQ RESCUE — fix the stale-quote family's three blockers, judge under live physics.

Pre-registered: docs/research/test_ledger.md § "SQ RESCUE (2026-06-11)" (SQR1-SQR4),
written BEFORE any future-block number of this campaign existed.

Blockers addressed:
  SQR1  frozen-curve drift  -> rolling 3-day refit + walk-forward frozen-vs-rolling
                               + refit-cadence recommendation + drift alarm
  SQR2  regime bleed        -> chop/trend gate (BTC trendiness), fit on dev ONLY
  SQR3  macro-correlated    -> per-window-epoch cross-coin concurrency cap (sq-only)
        variance
  SQR4  execution fragility -> live_guarded re-judgment at $5 (fills_live live-1),
                               latency sensitivity {2,5,10}s, shadow-live verdict

Campaign splits (LC2 convention): dev 05-23..27 / holdout 05-28..06-04 / FUTURE
06-05..09, revealed once by `--stage reveal` (knobs are frozen by `--stage devhold`
into data/research/sq_rescue/knobs.json first).

Decision semantics = StaleQuoteState live parity (first qualifying tick per slug;
margin <= |model_p - yes_mid| <= max_mis; |vel10| >= jump; ask band + depth>=stake
pre-checks at the DECISION tick; v2 adds margin 0.12 + dist<=19bps). Exits are
hold-to-resolution (verified: 3,269/3,269 paper sq trades exit_reason="resolution")
— no early-exit modeling is needed; the $1/share settle convention is ~$0.04/win
optimistic at $5 (stated, not hidden).

Run:
  uv run python -m research.analysis.sq_rescue --stage devhold
  uv run python -m research.analysis.sq_rescue --stage reveal
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

from research.analysis.edge_lab import cl_outcomes, load_base
from research.analysis.loss_patterns import _fit_curve, _sq_prep
from research.clean_window import CLEAN_START, available_clean_dates
from research.dataset.feeds import load_l2_ladders
from research.lib.stats import window_clustered_bootstrap
from research.sim.fills_live import (DEFAULT_PARAMS_PATH, load_params,
                                     sample_latency, simulate_taker_entry)
from research.sim.fills_v2 import walk_buy, settle_pnl

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUTDIR = os.path.join(REPO, "data", "research", "sq_rescue")
KNOBS = os.path.join(OUTDIR, "knobs.json")
RESULTS = os.path.join(OUTDIR, "results.jsonl")
CURVE_ORIG = os.path.join(REPO, "data", "research", "stale_quote_curve.json.bak")
CURVE_DEPLOYED = os.path.join(REPO, "data", "research", "stale_quote_curve.json")
SQ_FULL = os.path.join(REPO, "data", "research", "ledgers", "sq_full.parquet")

T_LO, T_HI = 60, 840          # the sq decision band (time_left seconds)
WF_STAKE = 10.0               # walk-forward stake (continuity with sq_full)
LIVE_STAKE = 5.0              # SQR4 stake
MIN_FIT_TICKS = 200           # rolling refit minimum (else carry previous curve)
Z_GRID = np.linspace(-3.0, 3.0, 25)
DEPLOY_SWITCH_DATE = "2026-06-05"   # the live curve was hand-refit on this date

V1 = dict(margin=0.08, max_mis=0.30, jump_bps=8.0, min_ask=0.05, max_ask=0.95,
          max_dist_bps=None)
V2 = dict(margin=0.12, max_mis=0.30, jump_bps=8.0, min_ask=0.05, max_ask=0.95,
          max_dist_bps=19.0)
_LV = range(1, 11)


# ---------------------------------------------------------------------------
# pure helpers (unit-tested in tests/research/test_sq_rescue.py)
# ---------------------------------------------------------------------------
def campaign_split(date_str: str) -> str:
    """LC2 campaign splits: dev 05-23..27 / holdout 05-28..06-04 / future 06-05+."""
    d = str(date_str)
    if d <= "2026-05-27":
        return "dev"
    if d <= "2026-06-04":
        return "holdout"
    return "future"


def chase_ceiling(entry_ask: float) -> float:
    """Executor chase band for sq: min(entry_ask + 0.07, 0.95) (registered SQR4)."""
    return float(min(entry_ask + 0.07, 0.95))


def rolling_curve_map(band: pd.DataFrame, days: list[str], k: int = 3,
                      cadence: int = 1, min_ticks: int = MIN_FIT_TICKS
                      ) -> Dict[str, Optional[Tuple[np.ndarray, np.ndarray]]]:
    """date -> curve fit on the trailing k days, refit every `cadence` days.
    Days with no full k-day trail map to None (ineligible). If a trail has
    < min_ticks finite-z rows the previous curve is carried."""
    dates = band["date"].astype(str)
    out: Dict[str, Optional[Tuple[np.ndarray, np.ndarray]]] = {}
    curve = None
    last_refit = None
    for i, d in enumerate(days):
        if i < k:
            out[d] = None
            continue
        due = last_refit is None or (i - last_refit) >= cadence
        if due:
            train = band[dates.isin(days[i - k:i])]
            t = train[np.isfinite(train["z"])]
            if len(t) >= min_ticks:
                zc, p = _fit_curve(train)
                curve = (np.asarray(zc, "f8"), np.asarray(p, "f8"))
                last_refit = i
        out[d] = curve
    return out


def static_curve_map(days: list[str], zc, p
                     ) -> Dict[str, Tuple[np.ndarray, np.ndarray]]:
    c = (np.asarray(zc, "f8"), np.asarray(p, "f8"))
    return {d: c for d in days}


def deployed_curve_map(days: list[str], zc_old, p_old, zc_new, p_new,
                       switch_date: str = DEPLOY_SWITCH_DATE
                       ) -> Dict[str, Tuple[np.ndarray, np.ndarray]]:
    """What the paper bot actually traded: old curve through switch_date-1,
    the hand-refit curve from switch_date on."""
    old = (np.asarray(zc_old, "f8"), np.asarray(p_old, "f8"))
    new = (np.asarray(zc_new, "f8"), np.asarray(p_new, "f8"))
    return {d: (old if d < switch_date else new) for d in days}


def interp_curve(z: np.ndarray, zc: np.ndarray, p: np.ndarray) -> np.ndarray:
    return np.interp(np.clip(z, -6, 6), zc, p, left=p[0], right=p[-1])


def sq_decisions(band: pd.DataFrame,
                 curve_map: Dict[str, Optional[Tuple[np.ndarray, np.ndarray]]],
                 *, margin: float, max_mis: float, jump_bps: float,
                 min_ask: float, max_ask: float,
                 max_dist_bps: Optional[float] = None,
                 stake: float = WF_STAKE) -> pd.DataFrame:
    """First qualifying tick per slug under StaleQuoteState live semantics.
    band must carry: slug, symbol, date, window_start_ts, seconds_into_window,
    time_left_sec, yes_mid, yes_best_ask, no_best_ask, yes_ask_depth,
    no_ask_depth, spot_vel_10s_bps, abs_dist_bps, outcome_up_clean, z."""
    d = band.copy()
    d["date"] = d["date"].astype(str)
    # per-day model_p under the day's in-use curve (None days are ineligible)
    model_p = np.full(len(d), np.nan)
    dates = d["date"].to_numpy()
    z = d["z"].to_numpy("f8")
    for day in np.unique(dates):
        c = curve_map.get(day)
        if c is None:
            continue
        m = dates == day
        model_p[m] = interp_curve(z[m], c[0], c[1])
    d["model_p"] = model_p
    d["mis"] = d["model_p"] - d["yes_mid"]
    am = d["mis"].abs()
    mask = (am >= margin) & (am <= max_mis) & \
        (d["spot_vel_10s_bps"].abs() >= jump_bps) & np.isfinite(d["mis"])
    if max_dist_bps is not None:
        mask &= d["abs_dist_bps"] <= max_dist_bps
    d = d[mask]
    if d.empty:
        return pd.DataFrame()
    buy_yes = (d["mis"] > 0).to_numpy()
    side_ask = np.where(buy_yes, d["yes_best_ask"].to_numpy("f8"),
                        d["no_best_ask"].to_numpy("f8"))
    depth_sh = np.where(buy_yes, d["yes_ask_depth"].to_numpy("f8"),
                        d["no_ask_depth"].to_numpy("f8"))
    ok = ((side_ask >= min_ask) & (side_ask <= max_ask)
          & np.isfinite(side_ask) & (depth_sh * side_ask >= stake))
    d = d[ok].copy()
    if d.empty:
        return pd.DataFrame()
    d["buy_yes"] = buy_yes[ok]
    d["entry_ask"] = side_ask[ok]
    first = (d.sort_values(["slug", "seconds_into_window"])
             .groupby("slug", as_index=False).first())
    first = first.rename(columns={"seconds_into_window": "entry_sec",
                                  "time_left_sec": "time_left"})
    first["csplit"] = first["date"].astype(str).map(campaign_split)
    keep = ["slug", "symbol", "date", "csplit", "window_start_ts", "entry_sec",
            "time_left", "buy_yes", "entry_ask", "model_p", "mis", "z",
            "abs_dist_bps", "outcome_up_clean"]
    return first[keep]


def apply_concurrency_cap(dec: pd.DataFrame, c: Optional[int]) -> pd.DataFrame:
    """Keep <= c sq positions per window epoch (same window_start_ts);
    priority = earliest entry_sec, tie -> symbol asc (outcome-blind)."""
    if c is None or dec.empty:
        return dec
    d = dec.sort_values(["window_start_ts", "entry_sec", "symbol"],
                        kind="mergesort").copy()
    d["_rank"] = d.groupby("window_start_ts").cumcount()
    return d[d["_rank"] < c].drop(columns="_rank")


def efficiency_ratio(ts: np.ndarray, px: np.ndarray, query_ts: np.ndarray,
                     window_s: int, min_coverage: float = 0.6) -> np.ndarray:
    """ER(t) = |px(t) - px(t-W)| / sum |1s increments| over (t-W, t].
    ts must be sorted int seconds. < min_coverage*W points -> NaN (fail-open)."""
    out = np.full(len(query_ts), np.nan)
    for i, t in enumerate(query_ts):
        lo = np.searchsorted(ts, t - window_s, side="right")
        hi = np.searchsorted(ts, t, side="right")
        n = hi - lo
        if n < min_coverage * window_s:
            continue
        seg = px[lo:hi]
        path = float(np.sum(np.abs(np.diff(seg))))
        if path <= 0:
            continue
        out[i] = abs(float(seg[-1] - seg[0])) / path
    return out


def trailing_window_move(epoch_ts: np.ndarray, move_bps: np.ndarray,
                         query_epochs: np.ndarray, n_trail: int = 4) -> np.ndarray:
    """Mean |net move bps| of the n_trail CLOSED epochs strictly before each
    query epoch. < n_trail available -> NaN (fail-open). epoch_ts sorted."""
    out = np.full(len(query_epochs), np.nan)
    a = np.abs(move_bps)
    for i, e in enumerate(query_epochs):
        hi = np.searchsorted(epoch_ts, e, side="left")
        if hi < n_trail:
            continue
        out[i] = float(a[hi - n_trail:hi].mean())
    return out


def drift_metric(p_inuse: np.ndarray, p_fresh: np.ndarray,
                 weights: np.ndarray) -> float:
    """Decision-z-density-weighted mean |p_inuse - p_fresh| on a common grid."""
    w = np.asarray(weights, "f8")
    if w.sum() <= 0:
        return float("nan")
    return float(np.sum(w * np.abs(p_inuse - p_fresh)) / w.sum())


def weighted_cal_error(pred: np.ndarray, outcome: np.ndarray,
                       n_bins: int = 10) -> float:
    """Tick-weighted |mean_pred - realized| over equal-width pred bins (A3's
    wce without the bootstrap CI legs)."""
    pred = np.asarray(pred, "f8")
    outcome = np.asarray(outcome, "f8")
    m = np.isfinite(pred) & np.isfinite(outcome)
    pred, outcome = pred[m], outcome[m]
    if len(pred) == 0:
        return float("nan")
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    idx = np.clip(np.digitize(pred, edges) - 1, 0, n_bins - 1)
    num = 0.0
    den = 0.0
    for b in range(n_bins):
        mm = idx == b
        n = int(mm.sum())
        if n == 0:
            continue
        num += n * abs(float(pred[mm].mean()) - float(outcome[mm].mean()))
        den += n
    return num / den if den else float("nan")


# ---------------------------------------------------------------------------
# data assembly
# ---------------------------------------------------------------------------
_BAND_COLS = ["slug", "symbol", "date", "window_start_ts", "seconds_into_window",
              "time_left_sec", "yes_mid", "yes_best_ask", "yes_best_bid",
              "no_best_ask", "yes_ask_depth", "no_ask_depth",
              "spot_vel_10s_bps", "dist_strike_bps", "abs_dist_bps",
              "outcome_up_clean", "realized_vol", "cb_spot", "start_price"]


def load_band() -> pd.DataFrame:
    b = load_base()
    sq = _sq_prep(b)
    band = sq[(sq["time_left_sec"] >= T_LO) & (sq["time_left_sec"] <= T_HI)
              & np.isfinite(sq["z"])]
    band = band[[c for c in _BAND_COLS + ["z"] if c in band.columns]].copy()
    band["date"] = band["date"].astype(str)
    return band


def load_curve(path: str) -> Tuple[np.ndarray, np.ndarray]:
    with open(path) as f:
        j = json.load(f)
    return np.asarray(j["z"], "f8"), np.asarray(j["p_up"], "f8")


def btc_spot_arrays(b: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray]:
    btc = b[b["symbol"] == "btc"]
    ts = (btc["window_start_ts"].to_numpy("i8")
          + btc["seconds_into_window"].to_numpy("i8"))
    g = pd.DataFrame({"ts": ts, "px": btc["cb_spot"].to_numpy("f8")})
    g = g.dropna().groupby("ts", as_index=False)["px"].median().sort_values("ts")
    return g["ts"].to_numpy("i8"), g["px"].to_numpy("f8")


def btc_epoch_moves(b: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray]:
    """Per BTC 15m epoch: the window's net move (bps vs strike) at its LAST tick."""
    btc = b[b["symbol"] == "btc"]
    last = (btc.sort_values("seconds_into_window")
            .groupby("slug", as_index=False).last())
    last = last.sort_values("window_start_ts")
    return (last["window_start_ts"].to_numpy("i8"),
            last["dist_strike_bps"].to_numpy("f8"))


def attach_regime_features(dec: pd.DataFrame, spot_ts, spot_px,
                           ep_ts, ep_mv) -> pd.DataFrame:
    if dec.empty:
        return dec
    d = dec.copy()
    q = (d["window_start_ts"].to_numpy("i8") + d["entry_sec"].to_numpy("i8"))
    d["er_btc_1800"] = efficiency_ratio(spot_ts, spot_px, q, 1800)
    d["er_btc_3600"] = efficiency_ratio(spot_ts, spot_px, q, 3600)
    d["trail4_btc_move"] = trailing_window_move(
        ep_ts, ep_mv, d["window_start_ts"].to_numpy("i8"))
    return d


# ---------------------------------------------------------------------------
# fills + settlement
# ---------------------------------------------------------------------------
_LADDERS: dict = {}


def _ladder(sym: str):
    if sym not in _LADDERS:
        end = (available_clean_dates(sym) or [CLEAN_START])[-1]
        _LADDERS[sym] = load_l2_ladders(sym, CLEAN_START, end)
    return _LADDERS[sym]


def _side_ladder(lr, buy_yes: bool):
    if buy_yes:
        return ([lr[f"ask_px_{i}"] for i in _LV], [lr[f"ask_sz_{i}"] for i in _LV])
    return ([1.0 - lr[f"bid_px_{i}"] for i in _LV], [lr[f"bid_sz_{i}"] for i in _LV])


def wf_ledger(dec: pd.DataFrame, *, latency: int = 2,
              stake: float = WF_STAKE) -> pd.DataFrame:
    """Walk-forward ledger: real-L2 walk_buy at entry_sec+latency, dual settle
    (Coinbase parity + Chainlink primary)."""
    if dec.empty:
        return pd.DataFrame()
    cl = cl_outcomes().set_index("slug")["cl_up"].to_dict()
    rows = []
    for r in dec.itertuples(index=False):
        lad = _ladder(r.symbol)
        try:
            lr = lad.loc[(r.slug, int(r.entry_sec) + latency)]
        except KeyError:
            continue
        if isinstance(lr, pd.DataFrame):
            lr = lr.iloc[0]
        px, sz = _side_ladder(lr, bool(r.buy_yes))
        fill = walk_buy(px, sz, stake)
        if not fill.filled or fill.unfilled_usd > stake * 0.5:
            continue
        won_cb = (r.outcome_up_clean == 1) if r.buy_yes else (r.outcome_up_clean == 0)
        cl_up = cl.get(r.slug)
        won_cl = None if cl_up is None else ((cl_up == 1) if r.buy_yes else (cl_up == 0))
        rows.append(dict(
            slug=r.slug, symbol=r.symbol, date=str(r.date), csplit=r.csplit,
            window_start_ts=int(r.window_start_ts), entry_sec=int(r.entry_sec),
            entry_ask=float(fill.avg_price), buy_yes=bool(r.buy_yes),
            mis=float(r.mis), z=float(r.z),
            won_cb=int(won_cb), pnl_cb=float(settle_pnl(fill, bool(won_cb))),
            won_cl=(int(won_cl) if won_cl is not None else None),
            pnl_cl=(float(settle_pnl(fill, bool(won_cl))) if won_cl is not None else np.nan),
        ))
    return pd.DataFrame(rows)


def live_ledger(dec: pd.DataFrame, params, *, mode: str = "live_guarded",
                stake: float = LIVE_STAKE, seed: int = 0,
                fixed_latency: Optional[float] = None) -> pd.DataFrame:
    """SQR4 ledger per rejudge_live_model.simulate_config: sampled (or fixed)
    latency, zero-fill hazard + kappa + guarded floor, chase ceiling
    min(entry_ask+0.07, 0.95), Chainlink settle, hold to resolution."""
    if dec.empty:
        return pd.DataFrame()
    rng = np.random.default_rng(seed)
    cl = cl_outcomes().set_index("slug")["cl_up"].to_dict()
    rows = []
    for r in dec.itertuples(index=False):
        if r.slug not in cl:
            continue
        base = dict(slug=r.slug, csplit=r.csplit, date=str(r.date),
                    window_start_ts=int(r.window_start_ts), filled=0)
        if mode == "v2":
            lat = 2.0
        elif fixed_latency is not None:
            lat = float(fixed_latency)
        else:
            lat = sample_latency(rng, params)
        try:
            lr = _ladder(r.symbol).loc[(r.slug, int(r.entry_sec) + int(round(lat)))]
        except KeyError:
            rows.append(base)
            continue
        if isinstance(lr, pd.DataFrame):
            lr = lr.iloc[0]
        px, sz = _side_ladder(lr, bool(r.buy_yes))
        ceiling = chase_ceiling(float(r.entry_ask))
        if mode == "v2":
            pxa = np.asarray(px, "f8")
            sza = np.asarray(sz, "f8")
            band = pxa <= ceiling + 1e-9
            f = walk_buy(pxa[band], sza[band], stake)
        else:
            f = simulate_taker_entry(
                px, sz, stake, entry_ask=float(r.entry_ask), max_ask=ceiling,
                time_left=float(r.time_left), rng=rng, params=params,
                mode=("guarded" if mode == "live_guarded" else "legacy"))
        if not f.filled or f.unfilled_usd > stake * 0.5:
            rows.append(base)
            continue
        won = (cl[r.slug] == 1) if r.buy_yes else (cl[r.slug] == 0)
        rows.append({**base, "filled": 1, "won": int(won),
                     "pnl": float(settle_pnl(f, bool(won))),
                     "avg_price": float(f.avg_price)})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------
def _ci(values, clusters, n=2000):
    lo, _, hi = window_clustered_bootstrap(np.asarray(values, "f8"),
                                           np.asarray(clusters), n=n)
    return round(float(lo), 3), round(float(hi), 3)


def ledger_stats(led: pd.DataFrame, pnl_col: str = "pnl_cl",
                 won_col: str = "won_cl") -> dict:
    """EV/trade with slug- AND epoch-clustered CIs (sq is macro-correlated)."""
    if led is None or led.empty:
        return dict(n=0)
    s = led.dropna(subset=[pnl_col])
    if len(s) < 3:
        return dict(n=int(len(s)))
    slo, shi = _ci(s[pnl_col].values, s["slug"].values)
    elo, ehi = _ci(s[pnl_col].values, s["window_start_ts"].values)
    return dict(n=int(len(s)), wr=round(float(s[won_col].mean() * 100), 1),
                ev=round(float(s[pnl_col].mean()), 3),
                ci_slug=[slo, shi], ci_epoch=[elo, ehi],
                total=round(float(s[pnl_col].sum()), 1),
                days=int(s["date"].nunique()))


def split_stats(led: pd.DataFrame, **kw) -> dict:
    out = {}
    for sp in ("dev", "holdout", "future"):
        out[sp] = ledger_stats(led[led["csplit"] == sp], **kw) if len(led) else dict(n=0)
    return out


def daily_pnl(led: pd.DataFrame, pnl_col: str = "pnl_cl") -> pd.Series:
    s = led.dropna(subset=[pnl_col])
    return s.groupby("date")[pnl_col].sum()


def day_sharpe(led: pd.DataFrame, pnl_col: str = "pnl_cl") -> float:
    d = daily_pnl(led, pnl_col)
    if len(d) < 3 or d.std(ddof=1) <= 0:
        return float("nan")
    return float(d.mean() / d.std(ddof=1))


def worst_epoch(led: pd.DataFrame, pnl_col: str = "pnl_cl") -> float:
    s = led.dropna(subset=[pnl_col])
    if s.empty:
        return float("nan")
    return float(s.groupby("window_start_ts")[pnl_col].sum().min())


def paired_delta_ci(led_a: pd.DataFrame, led_b: pd.DataFrame,
                    pnl_col: str = "pnl_cl") -> dict:
    """Per-window ΔPnL (a - b) over the union of traded slugs (0 if absent)."""
    a = led_a.dropna(subset=[pnl_col]).set_index("slug")[pnl_col]
    bb = led_b.dropna(subset=[pnl_col]).set_index("slug")[pnl_col]
    slugs = sorted(set(a.index) | set(bb.index))
    if len(slugs) < 3:
        return dict(n=len(slugs))
    delta = np.array([a.get(s, 0.0) - bb.get(s, 0.0) for s in slugs])
    lo, hi = _ci(delta, np.asarray(slugs))
    return dict(n=len(slugs), mean=round(float(delta.mean()), 3), ci=[lo, hi])


# ---------------------------------------------------------------------------
# SQR1 calibration + drift alarm
# ---------------------------------------------------------------------------
def arm_predictions(band: pd.DataFrame, curve_map) -> np.ndarray:
    pred = np.full(len(band), np.nan)
    dates = band["date"].astype(str).to_numpy()
    z = band["z"].to_numpy("f8")
    for day in np.unique(dates):
        c = curve_map.get(day)
        if c is None:
            continue
        m = dates == day
        pred[m] = interp_curve(z[m], c[0], c[1])
    return pred


def arm_wce(band: pd.DataFrame, curve_map, dates: list[str]) -> float:
    sub = band[band["date"].isin(dates)]
    pred = arm_predictions(sub, curve_map)
    return weighted_cal_error(pred, sub["outcome_up_clean"].to_numpy("f8"))


def drift_alarm_series(band: pd.DataFrame, inuse_map, days: list[str],
                       fresh_k: int = 3) -> Dict[str, float]:
    """A(d) per day: decision-z-density-weighted |p_inuse - p_fresh3d| on Z_GRID,
    fresh3d fit on days [d-(k-1) .. d] (computable end-of-day d)."""
    out: Dict[str, float] = {}
    dcol = band["date"].astype(str)
    for i, d in enumerate(days):
        if i < fresh_k - 1 or inuse_map.get(d) is None:
            continue
        fresh_days = days[i - fresh_k + 1:i + 1]
        train = band[dcol.isin(fresh_days)]
        if len(train[np.isfinite(train["z"])]) < MIN_FIT_TICKS:
            continue
        zc_f, p_f = _fit_curve(train)
        p_fresh = interp_curve(Z_GRID, np.asarray(zc_f, "f8"), np.asarray(p_f, "f8"))
        zc_u, p_u = inuse_map[d]
        p_in = interp_curve(Z_GRID, zc_u, p_u)
        zd = band.loc[dcol == d, "z"].to_numpy("f8")
        zd = np.clip(zd[np.isfinite(zd)], Z_GRID[0], Z_GRID[-1])
        w, _ = np.histogram(zd, bins=np.linspace(Z_GRID[0], Z_GRID[-1], len(Z_GRID) + 1))
        out[d] = round(drift_metric(p_in, p_fresh, w.astype("f8")), 4)
    return out


# ---------------------------------------------------------------------------
# stages
# ---------------------------------------------------------------------------
def _dump(obj: dict, tag: str):
    os.makedirs(OUTDIR, exist_ok=True)
    with open(RESULTS, "a") as f:
        f.write(json.dumps({"tag": tag, **obj}, default=str) + "\n")


def build_all_curve_maps(band: pd.DataFrame, days: list[str]) -> dict:
    zc_o, p_o = load_curve(CURVE_ORIG)
    zc_n, p_n = load_curve(CURVE_DEPLOYED)
    maps = {
        "frozen_orig": static_curve_map(days, zc_o, p_o),
        "as_deployed": deployed_curve_map(days, zc_o, p_o, zc_n, p_n),
        "roll3_c1": rolling_curve_map(band, days, k=3, cadence=1),
    }
    for k in (2, 5, 7):
        maps[f"roll{k}_c1"] = rolling_curve_map(band, days, k=k, cadence=1)
    for c in (2, 3):
        maps[f"roll3_c{c}"] = rolling_curve_map(band, days, k=3, cadence=c)
    return maps


def decisions_for_arm(band, curve_map, cfg, dates=None) -> pd.DataFrame:
    dec = sq_decisions(band, curve_map, **cfg)
    if dec.empty:
        return dec
    if dates is not None:
        dec = dec[dec["date"].isin(dates)]
    return dec


def stage_devhold(quick: bool = False):
    """Choose every knob on dev(+holdout) only; freeze into knobs.json."""
    band = load_band()
    days = sorted(band["date"].unique())
    dh_days = [d for d in days if campaign_split(d) != "future"]
    # rolling arms are only defined from day index k; restrict ALL dev+holdout
    # comparisons to the common eligible days of roll3 (05-26+)
    elig = [d for d in dh_days if days.index(d) >= 3]
    print(f"band: {len(band):,} ticks, days {days[0]}..{days[-1]} "
          f"(dev+holdout eligible: {elig[0]}..{elig[-1]}, {len(elig)}d)")

    maps = build_all_curve_maps(band, days)

    # ---- reconciliation vs sq_full.parquet (dev+holdout, frozen-orig v1) ----
    rec = {}
    if os.path.exists(SQ_FULL):
        old = pd.read_parquet(SQ_FULL)
        old["csplit"] = old["date"].astype(str).map(campaign_split)
        old_dh = old[old["csplit"] != "future"]
        dec_v1 = decisions_for_arm(band, maps["frozen_orig"], V1, dh_days)
        led_v1 = wf_ledger(dec_v1)
        rec = dict(old_n=int(len(old_dh)), old_ev_cb=round(float(old_dh["pnl"].mean()), 3),
                   new_n=int(len(led_v1)), new_ev_cb=round(float(led_v1["pnl_cb"].mean()), 3),
                   slug_overlap=round(len(set(old_dh["slug"]) & set(led_v1["slug"]))
                                      / max(len(set(old_dh["slug"])), 1), 3))
        print(f"reconciliation vs sq_full (dev+holdout): {rec}")

    # ---- SQR1 knobs: K sweep + cadence (dev+holdout walk-forward, CL) ----
    sweep = {}
    for arm in ("frozen_orig", "roll2_c1", "roll3_c1", "roll5_c1", "roll7_c1",
                "roll3_c2", "roll3_c3"):
        led = wf_ledger(decisions_for_arm(band, maps[arm], V1, elig))
        st = ledger_stats(led)
        sweep[arm] = st
        print(f"  SQR1 d+h {arm:12s} n={st.get('n', 0):4d} "
              f"EV_cl=${st.get('ev', float('nan'))} ci_slug={st.get('ci_slug')}")
    ev3 = sweep["roll3_c1"].get("ev", np.nan)
    cadence = 1
    for c in (2, 3):  # cheapest = LEAST-frequent cadence within $0.10/tr of daily
        e = sweep[f"roll3_c{c}"].get("ev")
        if e is not None and ev3 is not None and (ev3 - e) <= 0.10:
            cadence = c
    print(f"  -> cadence recommendation: refit every {cadence} day(s)")

    # ---- SQR1 drift alarm: τ from dev+holdout under roll3 ----
    A_roll = drift_alarm_series(band[band["date"].isin(dh_days)],
                                maps["roll3_c1"], dh_days)
    tau = max(A_roll.values()) if A_roll else float("nan")
    A_frozen = drift_alarm_series(band[band["date"].isin(dh_days)],
                                  maps["frozen_orig"], dh_days)
    crossed = [d for d, a in A_frozen.items() if a >= tau]
    print(f"  drift alarm τ={tau:.4f} (roll3 churn envelope); "
          f"frozen crossings on d+h: {crossed}")

    # ---- SQR2: gate chooser on DEV only (frozen-orig v1 = the bleeding config) ----
    spot_ts, spot_px = btc_spot_arrays(load_base())
    ep_ts, ep_mv = btc_epoch_moves(load_base())
    dec_dev = decisions_for_arm(band, maps["frozen_orig"], V1,
                                [d for d in dh_days if campaign_split(d) == "dev"])
    dec_dev = attach_regime_features(dec_dev, spot_ts, spot_px, ep_ts, ep_mv)
    led_dev = wf_ledger(dec_dev)
    led_dev = led_dev.merge(
        dec_dev[["slug", "er_btc_1800", "er_btc_3600", "trail4_btc_move"]],
        on="slug", how="left")
    base_ev = float(led_dev["pnl_cl"].dropna().mean())
    best = None
    gate_grid = []
    for feat in ("er_btc_1800", "er_btc_3600", "trail4_btc_move"):
        fv = dec_dev[feat].dropna()
        nan_frac = float(dec_dev[feat].isna().mean())
        for q in (0.60, 0.70, 0.80, 0.90):
            theta = float(fv.quantile(q)) if len(fv) else float("nan")
            keepm = led_dev[feat].isna() | (led_dev[feat] <= theta)  # fail-OPEN
            kept = led_dev[keepm]
            kr = len(kept) / max(len(led_dev), 1)
            ev = float(kept["pnl_cl"].dropna().mean()) if len(kept) else float("nan")
            tot = float(kept["pnl_cl"].dropna().sum()) if len(kept) else float("nan")
            row = dict(feature=feat, q=q, theta=round(theta, 4),
                       keep_rate=round(kr, 3), dev_ev=round(ev, 3),
                       dev_total=round(tot, 1), nan_frac=round(nan_frac, 3))
            gate_grid.append(row)
            admissible = kr >= 0.50 and np.isfinite(ev) and ev > base_ev
            if admissible and (best is None or tot > best["dev_total"]):
                best = row
    print(f"  SQR2 dev base EV_cl=${base_ev:+.3f}; chooser -> "
          f"{best if best else 'NO admissible gate (honest negative)'}")

    # holdout one-shot validation of the chosen gate (if any)
    gate_holdout = None
    if best is not None:
        dec_h = decisions_for_arm(
            band, maps["frozen_orig"], V1,
            [d for d in dh_days if campaign_split(d) == "holdout"])
        dec_h = attach_regime_features(dec_h, spot_ts, spot_px, ep_ts, ep_mv)
        led_h = wf_ledger(dec_h).merge(
            dec_h[["slug", best["feature"]]], on="slug", how="left")
        keepm = led_h[best["feature"]].isna() | (led_h[best["feature"]] <= best["theta"])
        gate_holdout = dict(
            ungated_ev=round(float(led_h["pnl_cl"].dropna().mean()), 3),
            gated_ev=round(float(led_h[keepm]["pnl_cl"].dropna().mean()), 3),
            keep_rate=round(float(keepm.mean()), 3))
        print(f"  SQR2 holdout validation: {gate_holdout}")

    # ---- SQR3: concurrency C on dev+holdout (roll3 + gate-if-any) ----
    dec_dh = decisions_for_arm(band, maps["roll3_c1"], V1, elig)
    if best is not None:
        dec_dh = attach_regime_features(dec_dh, spot_ts, spot_px, ep_ts, ep_mv)
        keepm = dec_dh[best["feature"]].isna() | (dec_dh[best["feature"]] <= best["theta"])
        dec_dh = dec_dh[keepm]
    conc = {}
    for C in (None, 1, 2):
        led = wf_ledger(apply_concurrency_cap(dec_dh, C))
        conc[str(C)] = dict(n=int(led["pnl_cl"].notna().sum()),
                            ev=round(float(led["pnl_cl"].dropna().mean()), 3),
                            sharpe=round(day_sharpe(led), 3),
                            day_sd=round(float(daily_pnl(led).std(ddof=1)), 2),
                            worst_epoch=round(worst_epoch(led), 1))
        print(f"  SQR3 d+h C={C}: {conc[str(C)]}")
    cands = {k: v for k, v in conc.items() if k != "None" and np.isfinite(v["sharpe"])}
    best_c = max(cands, key=lambda k: cands[k]["sharpe"]) if cands else "None"
    chosen_c = None if (best_c == "None"
                        or cands[best_c]["sharpe"] <= conc["None"]["sharpe"]) \
        else int(best_c)
    print(f"  -> SQR3 chosen C = {chosen_c}")

    # ---- SQR4 chooser: rescued v1 vs rescued v2 on d+h live_guarded total ----
    params = load_params(DEFAULT_PARAMS_PATH)
    chooser = {}
    for nm, cfg in (("rescued_v1", V1), ("rescued_v2", V2)):
        dec = decisions_for_arm(band, maps["roll3_c1"], cfg, elig)
        if best is not None:
            dec = attach_regime_features(dec, spot_ts, spot_px, ep_ts, ep_mv)
            keepm = dec[best["feature"]].isna() | (dec[best["feature"]] <= best["theta"])
            dec = dec[keepm]
        dec = apply_concurrency_cap(dec, chosen_c)
        led = live_ledger(dec, params, mode="live_guarded", seed=0)
        f = led[led["filled"] == 1]
        chooser[nm] = dict(n_sig=int(len(led)), n_fill=int(len(f)),
                           total=round(float(f["pnl"].sum()), 1),
                           ev=round(float(f["pnl"].mean()), 3) if len(f) else None)
        print(f"  SQR4 d+h {nm}: {chooser[nm]}")
    final = max(chooser, key=lambda k: chooser[k]["total"])
    print(f"  -> SQR4 headline final config = {final}")

    knobs = dict(
        registered="test_ledger.md § SQ RESCUE (2026-06-11)",
        frozen_at=pd.Timestamp.utcnow().isoformat(),
        sqr1=dict(k=3, cadence=int(cadence), tau=round(float(tau), 4),
                  k_sweep={k: v.get("ev") for k, v in sweep.items()},
                  frozen_tau_crossings_dh=crossed),
        sqr2=(dict(adopted=True, **best, holdout=gate_holdout)
              if best is not None else dict(adopted=False, grid=gate_grid)),
        sqr3=dict(chosen_c=chosen_c, dh=conc),
        sqr4=dict(final=final, chooser=chooser),
        reconciliation=rec,
    )
    os.makedirs(OUTDIR, exist_ok=True)
    with open(KNOBS, "w") as f:
        json.dump(knobs, f, indent=2, default=float)
    _dump(knobs, "devhold_knobs")
    print(f"\nknobs frozen -> {KNOBS}")


def _rescued_decisions(band, maps, cfg, knobs, spot_ts, spot_px, ep_ts, ep_mv,
                       dates=None):
    dec = decisions_for_arm(band, maps["roll3_c1"], cfg, dates)
    g = knobs["sqr2"]
    if g.get("adopted"):
        dec = attach_regime_features(dec, spot_ts, spot_px, ep_ts, ep_mv)
        keepm = dec[g["feature"]].isna() | (dec[g["feature"]] <= g["theta"])
        dec = dec[keepm]
    return apply_concurrency_cap(dec, knobs["sqr3"]["chosen_c"])


def stage_reveal(quick: bool = False):
    """ONE future-block pass. Reads frozen knobs; computes every registered
    future number; appends to results.jsonl."""
    if not os.path.exists(KNOBS):
        raise SystemExit("knobs.json missing — run --stage devhold first")
    with open(KNOBS) as f:
        knobs = json.load(f)
    band = load_band()
    days = sorted(band["date"].unique())
    fut_days = [d for d in days if campaign_split(d) == "future"]
    maps = build_all_curve_maps(band, days)
    b = load_base()
    spot_ts, spot_px = btc_spot_arrays(b)
    ep_ts, ep_mv = btc_epoch_moves(b)
    out: dict = {"future_days": fut_days}

    # ---- SQR1 reveal: arms × configs on future ----
    sqr1 = {}
    arm_leds = {}
    for arm in ("frozen_orig", "as_deployed", "roll3_c1"):
        for nm, cfg in (("v1", V1), ("v2", V2)):
            led = wf_ledger(decisions_for_arm(band, maps[arm], cfg, fut_days))
            arm_leds[(arm, nm)] = led
            sqr1[f"{arm}.{nm}"] = dict(
                cl=ledger_stats(led),
                cb=ledger_stats(led, pnl_col="pnl_cb", won_col="won_cb"))
    for nm in ("v1", "v2"):
        sqr1[f"paired_roll3_minus_frozen.{nm}"] = paired_delta_ci(
            arm_leds[("roll3_c1", nm)], arm_leds[("frozen_orig", nm)])
    sqr1["wce_future"] = {
        arm: round(arm_wce(band, maps[arm], fut_days), 4)
        for arm in ("frozen_orig", "as_deployed", "roll3_c1")}
    A_fro = drift_alarm_series(band, maps["frozen_orig"], days)
    A_roll = drift_alarm_series(band, maps["roll3_c1"], days)
    sqr1["alarm"] = dict(tau=knobs["sqr1"]["tau"],
                         frozen_series=A_fro, roll3_series=A_roll)
    out["sqr1"] = sqr1

    # ---- SQR2 reveal (only if adopted on dev) ----
    if knobs["sqr2"].get("adopted"):
        g = knobs["sqr2"]
        dec = decisions_for_arm(band, maps["roll3_c1"], V1, fut_days)
        dec = attach_regime_features(dec, spot_ts, spot_px, ep_ts, ep_mv)
        led = wf_ledger(dec).merge(dec[["slug", g["feature"]]], on="slug", how="left")
        keepm = led[g["feature"]].isna() | (led[g["feature"]] <= g["theta"])
        out["sqr2"] = dict(ungated=ledger_stats(led),
                           gated=ledger_stats(led[keepm]),
                           keep_rate=round(float(keepm.mean()), 3))
    else:
        out["sqr2"] = dict(adopted=False, note="no admissible dev gate — future leg not run")

    # ---- SQR3 reveal: capped vs uncapped on future (roll3 + gate-if-any) ----
    dec_fut = _rescued_decisions(band, maps, V1, dict(knobs, sqr3=dict(chosen_c=None)),
                                 spot_ts, spot_px, ep_ts, ep_mv, fut_days)
    sqr3 = {}
    for C in (None, 1, 2):
        led = wf_ledger(apply_concurrency_cap(dec_fut, C))
        sqr3[str(C)] = dict(
            n=int(led["pnl_cl"].notna().sum()),
            ev=round(float(led["pnl_cl"].dropna().mean()), 3),
            day_sd=round(float(daily_pnl(led).std(ddof=1)), 2),
            worst_epoch=round(worst_epoch(led), 1),
            total=round(float(led["pnl_cl"].dropna().sum()), 1))
    out["sqr3"] = dict(chosen_c=knobs["sqr3"]["chosen_c"], future=sqr3)

    # ---- SQR4 reveal: 6 configs × 2 models, final config extras ----
    params = load_params(DEFAULT_PARAMS_PATH)
    sqr4: dict = {}
    cfg_sets = {
        "frozen_v1": (maps["frozen_orig"], V1, False),
        "frozen_v2": (maps["frozen_orig"], V2, False),
        "deployed_v1": (maps["as_deployed"], V1, False),
        "deployed_v2": (maps["as_deployed"], V2, False),
        "rescued_v1": (maps["roll3_c1"], V1, True),
        "rescued_v2": (maps["roll3_c1"], V2, True),
        # no-gate/no-cap twins (registered addendum pre-reveal): if SQR2's own
        # future leg fails, the shadow-live spec falls back to these.
        "rescued_v1_nogate": (maps["roll3_c1"], V1, "nogate"),
        "rescued_v2_nogate": (maps["roll3_c1"], V2, "nogate"),
    }
    final_name = knobs["sqr4"]["final"]
    # extras (latency sensitivity + seeds) run for the registered final AND its
    # no-gate twin, so a failed SQR2 future leg has a fully-scored fallback in
    # this same single pass (ledger addendum ii).
    extras_names = {final_name, f"{final_name}_nogate"}
    extras_decs: dict = {}
    nogate_knobs = dict(knobs, sqr2=dict(adopted=False),
                        sqr3=dict(chosen_c=knobs["sqr3"]["chosen_c"]))
    for nm, (cmap, cfg, rescued) in cfg_sets.items():
        if rescued == "nogate":
            dec = _rescued_decisions(band, maps, cfg, nogate_knobs,
                                     spot_ts, spot_px, ep_ts, ep_mv)
        elif rescued:
            dec = _rescued_decisions(band, maps, cfg, knobs,
                                     spot_ts, spot_px, ep_ts, ep_mv)
        else:
            dec = decisions_for_arm(band, cmap, cfg)
        if nm in extras_names:
            extras_decs[nm] = dec
        sqr4[nm] = {}
        for mode in ("v2", "live_guarded"):
            led = live_ledger(dec, params, mode=mode, seed=0)
            fut = led[led["csplit"] == "future"]
            f = fut[fut["filled"] == 1]
            st = (dict(n_sig=int(len(fut)), n_fill=int(len(f)),
                       fill_rate=round(len(f) / max(len(fut), 1), 3),
                       wr=round(float(f["won"].mean() * 100), 1) if len(f) else None,
                       ev=round(float(f["pnl"].mean()), 3) if len(f) else None,
                       total=round(float(f["pnl"].sum()), 1) if len(f) else None)
                  if len(fut) else dict(n_sig=0))
            if len(f) >= 3:
                st["ci_slug"] = list(_ci(f["pnl"].values, f["slug"].values))
                st["ci_epoch"] = list(_ci(f["pnl"].values,
                                          f["window_start_ts"].values))
                st["ev_per_signal"] = round(float(f["pnl"].sum()) / len(fut), 3)
            sqr4[nm][mode] = st

    # final config + no-gate twin: latency sensitivity + seeds
    for nm, dec in extras_decs.items():
        lat_sens = {}
        for lat in (2.0, 5.0, 10.0):
            led = live_ledger(dec, params, mode="live_guarded", seed=0,
                              fixed_latency=lat)
            f = led[(led["csplit"] == "future") & (led["filled"] == 1)]
            lat_sens[str(int(lat))] = dict(
                n_fill=int(len(f)),
                ev=round(float(f["pnl"].mean()), 3) if len(f) else None,
                ci_slug=(list(_ci(f["pnl"].values, f["slug"].values))
                         if len(f) >= 3 else None))
        seeds = []
        for sd in range(5):
            led = live_ledger(dec, params, mode="live_guarded", seed=sd)
            f = led[(led["csplit"] == "future") & (led["filled"] == 1)]
            if len(f):
                seeds.append(float(f["pnl"].mean()))
        sqr4[f"extras.{nm}"] = dict(
            config=nm, registered_final=(nm == final_name),
            latency_sensitivity=lat_sens,
            seeds_mean=round(float(np.mean(seeds)), 3) if seeds else None,
            seeds_sd=round(float(np.std(seeds, ddof=1)), 3) if len(seeds) > 1 else None,
            seeds=[round(s, 3) for s in seeds])
    out["sqr4"] = sqr4

    _dump(out, "reveal")
    print(json.dumps(out, indent=2, default=str))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stage", choices=("devhold", "reveal"), required=True)
    ap.add_argument("--quick", action="store_true")
    a = ap.parse_args()
    if a.stage == "devhold":
        stage_devhold(quick=a.quick)
    else:
        stage_reveal(quick=a.quick)


if __name__ == "__main__":
    main()
