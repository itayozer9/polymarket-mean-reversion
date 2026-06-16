"""hypothesis_sweep — generate ~2000 economically-motivated, LATENCY-PROOF
hypotheses and judge each through the shared edge_lab harness.

Every hypothesis is (a) a filter over the prepped per-tick frame and (b) a
buy-side rule. edge_lab does the rest IDENTICALLY for all of them:
  first qualifying tick -> fill at (entry+latency) -> Chainlink resettle ->
  window-clustered CIs per split -> CPCV -> daily Deflated Sharpe -> latency
  survival. So results are comparable and hard to fake.

Discipline baked in:
  - HOLD TO RESOLUTION only (no intra-window exit) => no speed needed.
  - Chainlink settlement (the oracle Polymarket pays), never Coinbase.
  - SELECTION must use dev + CPCV + latency only; the `future` split is reported
    but NOT used to shortlist (revealed once in Phase 4). This file just records
    every metric; selection.py enforces the discipline.
  - Cheap pre-filter (n>=MIN_N, dev_ev>0, full_ev>0) before the expensive
    bootstrap/CPCV/DSR, so 2000 hypotheses stay tractable.

Each family carries an economic RATIONALE (printed into the JSONL) — this is a
logic-driven search, not number-spraying; the grid explores each thesis's
parameter sensitivity, which is exactly what robustness requires.

Run:  uv run python -m research.analysis.hypothesis_sweep
Out:  data/research/hypotheses/results.jsonl  (+ specs.jsonl)
"""
from __future__ import annotations
import itertools
import json
import os
import time

import numpy as np
import pandas as pd

from research.analysis.edge_lab import (
    load_base, first_tick, simulate, evaluate, latency_survival)

OUT_DIR = os.path.join("data", "research", "hypotheses")
RESULTS = os.path.join(OUT_DIR, "results.jsonl")
SPECS = os.path.join(OUT_DIR, "specs.jsonl")

MIN_N = 20          # min trades to even consider
N_TRIALS = 2000     # multiple-testing correction (honest: kills DSR with 13 days;
                    # used as a ranking signal, not a binary gate — see report)
LATS = (2, 3, 5, 10)

# ---------------------------------------------------------------------------
# campaign split override (test_ledger.md "Live-cost campaign 2026-06-10").
# clean_window.split_of labels everything after 05-31 as `future`, but the
# 06-01..06-04 block was REVEALED by the 2026-06-05 campaign's Phase 4 — it is
# burned as an OOS reveal. For a re-run campaign, set FUTURE_START (e.g.
# "2026-06-05"): old-future days BEFORE it are relabeled `holdout` (post-dev
# OOS, already peeked for ~24 shortlisted specs) and `future` = days >= it.
# Default None => byte-identical to the original pipeline. Never rewrites
# clean_window.py — local relabeling only.
# ---------------------------------------------------------------------------
FUTURE_START: str | None = None


def set_future_override(date: str | None) -> None:
    """Set (or clear) the campaign future-split override. Clears the cached
    psettle frame so it is rebuilt with the new labels."""
    global FUTURE_START
    FUTURE_START = date
    _PSETTLE["df"] = None
    _TA["df"] = None


def apply_future_override(df):
    """Relabel old-future days (< FUTURE_START) as holdout. No-op when unset.
    Shallow-copies the frame and replaces only the split column (the lru-cached
    base frame is never mutated)."""
    if FUTURE_START is None or df is None or len(df) == 0:
        return df
    d = df["date"].astype(str)
    split = df["split"].astype(str).to_numpy().copy()
    burn = (split == "future") & (d < FUTURE_START).to_numpy()
    split[burn] = "holdout"
    out = df.copy(deep=False)
    out["split"] = split
    return out


# ---------------------------------------------------------------------------
# helpers shared by builders
# ---------------------------------------------------------------------------
def _fav_yes(c):                 # favourite is the YES (Up) side
    return (c["yes_mid"] >= 0.5).to_numpy()


def _aligned(vals_yes, vals_no, fav_yes):
    """pick the favourite-aligned value (yes-version if fav is yes else no)."""
    return np.where(fav_yes, vals_yes, vals_no)


# ---------------------------------------------------------------------------
# family builders: (cand_df, buy_yes_array) from base + params
# all sub-filtering keeps cand and buy_yes index-aligned.
# ---------------------------------------------------------------------------
def fam_det(b, p):
    """DETERMINISM: late/mid window, spot clearly off strike, book AGREES with
    spot, buy the (cheap) favourite, hold to resolution. Thesis: the book lags
    spot; by entry the outcome is mostly set, the book reprices into it."""
    m = ((b["time_left_sec"] >= p["t_lo"]) & (b["time_left_sec"] <= p["t_hi"])
         & (b["abs_dist_bps"] >= p["dist_min"]) & (b["consistent"])
         & (b["fav_ask"].between(p["ask_lo"], p["ask_hi"])))
    c = b[m]
    return c, _fav_yes(c)


def fam_fav(b, p):
    """FAVOURITE-VALUE: behavioural favourite-longshot bias — traders underprice
    near-locked favourites. Mid-window, optional low-vol / single-side restrict."""
    m = ((b["time_left_sec"] >= p["t_lo"]) & (b["time_left_sec"] <= p["t_hi"])
         & (b["abs_dist_bps"] >= p["dist_min"]) & (b["consistent"])
         & (b["fav_ask"].between(p["ask_lo"], p["ask_hi"])))
    if p.get("vol_max") is not None:
        m &= (b["realized_vol"] * 100.0 <= p["vol_max"])
    if p.get("side"):
        m &= (b["fav_side"] == p["side"])
    c = b[m]
    return c, _fav_yes(c)


def fam_longshot(b, p):
    """DEEP-FAVOURITE LONGSHOT: buy very-high-ask (0.85-0.98) near-certain
    favourites. Classic favourite-longshot: the longshot (the other side) is
    overpriced => the favourite is value even at a high ask."""
    m = ((b["time_left_sec"] >= p["t_lo"]) & (b["time_left_sec"] <= p["t_hi"])
         & (b["abs_dist_bps"] >= p["dist_min"]) & (b["consistent"])
         & (b["fav_ask"].between(p["ask_lo"], p["ask_hi"])))
    if p.get("side"):
        m &= (b["fav_side"] == p["side"])
    c = b[m]
    return c, _fav_yes(c)


def fam_e4(b, p):
    """E4 DISAGREEMENT: book DISAGREES with spot near close; buy the spot-implied
    side (book is the one that's wrong; it reprices toward spot by resolution)."""
    m = ((b["time_left_sec"] >= p["t_lo"]) & (b["time_left_sec"] <= p["t_hi"])
         & (b["abs_dist_bps"] >= p["dist_min"]) & (~b["consistent"]))
    c = b[m]
    by = (c["dist_strike_bps"] > 0).to_numpy()
    if p.get("ud_ask_max") is not None:
        ud_ask = np.where(by, c["yes_best_ask"].to_numpy("f8"),
                          1.0 - c["yes_best_bid"].to_numpy("f8"))
        keep = ud_ask <= p["ud_ask_max"]
        c, by = c[keep], by[keep]
    return c, by


def fam_oracle(b, p):
    """ORACLE DIVERGENCE: the book tracks Coinbase but the window SETTLES on
    Chainlink. When Chainlink leads Coinbase (|basis| large), buy the side
    Chainlink implies. Pure settlement-source edge, latency-proof."""
    m = ((b["cl_cb_basis_bps"].abs() >= p["basis_min"])
         & (b["time_left_sec"] >= p["t_lo"]) & (b["time_left_sec"] <= p["t_hi"])
         & (b["yes_mid"].between(0.05, 0.95)))
    c = b[m]
    by = (c["cl_cb_basis_bps"] > 0).to_numpy()
    if p.get("confirm_dist"):       # only when Coinbase dist doesn't contradict
        keep = np.sign(c["dist_strike_bps"].to_numpy("f8")) == np.sign(
            c["cl_cb_basis_bps"].to_numpy("f8"))
        c, by = c[keep], by[keep]
    return c, by


def fam_micro(b, p):
    """L2 IMBALANCE confirmation on determinism: only take the favourite when the
    book depth leans the favourite's way (imbalance), i.e. resting size agrees."""
    m = ((b["time_left_sec"] >= p["t_lo"]) & (b["time_left_sec"] <= p["t_hi"])
         & (b["abs_dist_bps"] >= p["dist_min"]) & (b["consistent"])
         & (b["fav_ask"].between(0.50, 0.95)))
    c = b[m]
    fy = _fav_yes(c)
    imb_fav = _aligned(c["l2_imbalance"].to_numpy("f8"),
                       1.0 - c["l2_imbalance"].to_numpy("f8"), fy)
    keep = np.isfinite(imb_fav) & (imb_fav >= p["imb_min"])
    return c[keep], fy[keep]


def fam_microprice(b, p):
    """MICROPRICE confirmation: take the favourite only when the size-weighted
    microprice agrees with (leans past) the mid in the favourite's direction."""
    m = ((b["time_left_sec"] >= p["t_lo"]) & (b["time_left_sec"] <= p["t_hi"])
         & (b["abs_dist_bps"] >= p["dist_min"]) & (b["consistent"])
         & (b["fav_ask"].between(0.50, 0.95)))
    c = b[m]
    fy = _fav_yes(c)
    mp_fav = _aligned(c["microprice"].to_numpy("f8"),
                      1.0 - c["microprice"].to_numpy("f8"), fy)
    keep = np.isfinite(mp_fav) & (mp_fav >= p["mp_min"])
    return c[keep], fy[keep]


def fam_flow(b, p):
    """TAKER-FLOW confirmation/fade: signed taker $ in last 5s. confirm=buy fav
    when recent flow agrees; fade=buy fav when flow leans AGAINST it (overdone)."""
    m = ((b["time_left_sec"] >= p["t_lo"]) & (b["time_left_sec"] <= p["t_hi"])
         & (b["abs_dist_bps"] >= p["dist_min"]) & (b["consistent"])
         & (b["fav_ask"].between(0.50, 0.95)))
    c = b[m]
    fy = _fav_yes(c)
    flow_fav = _aligned(c["tr_signed_5s"].to_numpy("f8"),
                        -c["tr_signed_5s"].to_numpy("f8"), fy)
    if p["dir"] == "confirm":
        keep = flow_fav >= p["thr"]
    else:                            # fade
        keep = flow_fav <= -p["thr"]
    return c[keep], fy[keep]


def fam_momentum(b, p):
    """LATE MOMENTUM CONTINUATION: spot still moving toward the favourite's side
    in the final minutes -> the lock is hardening, buy the favourite."""
    m = ((b["time_left_sec"] >= p["t_lo"]) & (b["time_left_sec"] <= p["t_hi"])
         & (b["abs_dist_bps"] >= p["dist_min"]) & (b["consistent"])
         & (b["fav_ask"].between(0.50, 0.95)))
    c = b[m]
    fy = _fav_yes(c)
    mv = _aligned(c["spot_vel_10s_bps"].to_numpy("f8"),
                  -c["spot_vel_10s_bps"].to_numpy("f8"), fy)
    keep = np.isfinite(mv) & (mv >= p["mom_min"])
    return c[keep], fy[keep]


def fam_stable(b, p):
    """STABLE-LOCK filter: determinism but drop fragile locks where spot is
    reverting toward strike (adverse_vel high). Thesis: a lock that is still
    moving toward strike can flip; require it to be calm/extending."""
    m = ((b["time_left_sec"] >= p["t_lo"]) & (b["time_left_sec"] <= p["t_hi"])
         & (b["abs_dist_bps"] >= p["dist_min"]) & (b["consistent"])
         & (b["fav_ask"].between(0.50, 0.95)))
    if p["dir"] == "calm":
        m &= (b["adverse_vel_10s"] <= p["av"])
    else:                            # fragile (null/contrarian check)
        m &= (b["adverse_vel_10s"] >= p["av"])
    c = b[m]
    return c, _fav_yes(c)


def fam_liq(b, p):
    """LIQUIDITY-CONDITIONED determinism: only when spread tight AND L2 depth
    deep (capacity-friendly; also a quality proxy for a real two-sided book)."""
    m = ((b["time_left_sec"] >= p["t_lo"]) & (b["time_left_sec"] <= p["t_hi"])
         & (b["abs_dist_bps"] >= p["dist_min"]) & (b["consistent"])
         & (b["fav_ask"].between(0.50, 0.95))
         & (b["spread_yes"] <= p["spread_max"])
         & (b["depth_usd"].fillna(0) >= p["depth_min"]))
    c = b[m]
    return c, _fav_yes(c)


def fam_tod(b, p):
    """TIME-OF-DAY/SESSION: run a base edge only in a UTC session window.
    Thesis: liquidity/flow regimes differ by Asia/EU/US session."""
    base = fam_det if p["base"] == "det" else fam_fav
    bp = dict(t_lo=p["t_lo"], t_hi=p["t_hi"], dist_min=p["dist_min"],
              ask_lo=p["ask_lo"], ask_hi=p["ask_hi"])
    c, by = base(b, bp)
    keep = (c["utc_hour"] >= p["h_lo"]) & (c["utc_hour"] < p["h_hi"])
    return c[keep], by[keep]


def fam_vol(b, p):
    """VOL-REGIME conditioning: run a base edge only inside a realized-vol band.
    Thesis: the book lags more (or less) depending on volatility."""
    base = {"det": fam_det, "fav": fam_fav, "e4": fam_e4}[p["base"]]
    bp = {k: p[k] for k in ("t_lo", "t_hi", "dist_min") if k in p}
    if p["base"] != "e4":
        bp.update(ask_lo=p["ask_lo"], ask_hi=p["ask_hi"])
    c, by = base(b, bp)
    v = (c["realized_vol"] * 100.0).to_numpy("f8")
    keep = (v >= p["v_lo"]) & (v < p["v_hi"])
    return c[keep], by[keep]


# cross-coin lead-lag needs a BTC reference keyed by (window_start_ts, sec).
_BTC_REF = {"df": None}

# psettle family: per-tick settlement-prob frame (oracle_print_model), lazy.
_PSETTLE = {"df": None}


def _psettle_frame():
    """tick_feature_frame + calibrated p_settle (P(cl_up)), book_healthy, tl 1-600.
    The model artifact (data/research/oracle_print_model.json) is fit on DEV ticks
    only (isotonic holdout, self-rejected) — the future block is clean OOS for it.
    Rows with NaN p (missing CL features) are dropped fail-closed. The campaign
    future-override is applied here so psettle candidates carry the same split
    labels as everything else."""
    if _PSETTLE["df"] is None:
        from research.analysis.oracle_print_model import (
            tick_feature_frame, p_settle, load_model)
        tf = tick_feature_frame().copy(deep=False)
        tf["p_up"] = p_settle(tf, model=load_model())
        tf = tf[np.isfinite(tf["p_up"])]
        _PSETTLE["df"] = apply_future_override(tf)
    return _PSETTLE["df"]


# TA family: base frame augmented with causal TA features, lazy.
_TA = {"df": None}


def _ta_frame():
    """load_base() left-joined with causal base-asset TA columns
    (research.dataset.ta_features). Built once; the campaign future-override is
    applied so TA candidates share the same split labels as every other family.
    The default (non-TA) pipeline never calls this, so it stays byte-identical."""
    if _TA["df"] is None:
        from research.dataset.ta_features import build_ta_features
        b = load_base()
        ta = build_ta_features(b)
        merged = b.merge(ta, on=["slug", "seconds_into_window"], how="left")
        _TA["df"] = apply_future_override(merged)
    return _TA["df"]


def fam_psettle(b, p):
    """MODEL-DIVERGENCE: calibrated settlement-print model P(side wins) vs the
    book's executable ask for that side; buy when the model says the side is
    underpriced by >= d_thr. Generalizes the OP4 near-strike fade (fav>=0.75 &
    p_fav<=0.60 -> buy cheap side) into a systematic family: side selection
    (favourite / cheap side / either), divergence threshold, tl band, ask band,
    optional |cl_dist| floor. Uses its own feature frame (NOT `b`)."""
    c = _psettle_frame()
    m = ((c["time_left_sec"] >= p["t_lo"]) & (c["time_left_sec"] <= p["t_hi"]))
    if p.get("cl_floor"):
        m &= (c["cl_dist_bps"].abs() >= p["cl_floor"])
    c = c[m]
    fav_yes = c["fav_side"].astype(str).str.lower().isin(("yes", "up")).to_numpy()
    p_up = c["p_up"].to_numpy("f8")
    yes_ask = c["yes_best_ask"].to_numpy("f8")
    no_ask = 1.0 - c["yes_best_bid"].to_numpy("f8")
    d_yes = p_up - yes_ask                    # model edge buying UP at its ask
    d_no = (1.0 - p_up) - no_ask              # model edge buying DOWN at its ask
    if p["side"] == "fav":
        by = fav_yes
    elif p["side"] == "cheap":
        by = ~fav_yes
    else:                                     # either: the side with more edge
        by = d_yes >= d_no
    ask = np.where(by, yes_ask, no_ask)
    d = np.where(by, d_yes, d_no)
    keep = (np.isfinite(d) & (d >= p["d_thr"]) & np.isfinite(ask)
            & (ask >= p["ask_lo"]) & (ask <= p["ask_hi"]) & (ask > 0.01))
    return c[keep], by[keep]


def fam_ta_directional(b, p):
    """TA DIRECTIONAL (honest control — book already prices spot; expected to
    fail): base-asset trend says up -> buy UP. Trend = EMA slope sign with a
    minimum magnitude (bps/sec). Tests the rejected family on clean data."""
    c = _ta_frame()
    m = ((c["time_left_sec"] >= p["t_lo"]) & (c["time_left_sec"] <= p["t_hi"])
         & (c["ta_ema_slope"].abs() >= p["slope_min"])
         & (c["ta_ema_slope"] != 0.0))      # flat sec-0 warm-up has no direction
    up = c["ta_ema_slope"] > 0
    by = up.to_numpy()
    ask = np.where(by, c["yes_best_ask"].to_numpy("f8"),
                   1.0 - c["yes_best_bid"].to_numpy("f8"))
    keep = (m.to_numpy() & np.isfinite(ask)
            & (ask >= p["ask_lo"]) & (ask <= p["ask_hi"]))
    return c[keep], by[keep]


def fam_ta_filter(b, p):
    """TA FILTER on the proven determinism edge: only take the favourite when the
    base-asset regime label matches (e.g. 'range' = no trend to fight). Tests
    whether a TA gate LIFTS an edge we already know is real."""
    c, by = fam_det(_ta_frame(), p)
    keep = (c["ta_regime"] == p["regime"]).to_numpy()
    return c[keep], by[keep]


def fam_ta_regime(b, p):
    """TA REGIME selection: run the determinism edge only inside an ATR band
    (bps). Thesis: the book lags spot more in higher-vol regimes -> bigger
    overshoots to harvest."""
    c, by = fam_det(_ta_frame(), p)
    atr = c["ta_atr"].to_numpy("f8")
    keep = np.isfinite(atr) & (atr >= p["atr_lo"]) & (atr < p["atr_hi"])
    return c[keep], by[keep]


def fam_ta_divergence(b, p):
    """TA DIVERGENCE (most aligned with 'rent on slow book repricing'): the base
    asset has moved (recent 30s return + EMA slope agree, magnitude >= ret_min
    bps) but the book hasn't repriced toward it. Buy the side the move implies."""
    c = _ta_frame()
    m = ((c["time_left_sec"] >= p["t_lo"]) & (c["time_left_sec"] <= p["t_hi"])
         & (c["ta_ema_slope"].abs() >= p["slope_min"])
         & (c["ta_ema_slope"] != 0.0)        # flat sec-0 warm-up has no direction
         & (c["ta_ret_30s"].abs() >= p["ret_min"])
         & (np.sign(c["ta_ema_slope"]) == np.sign(c["ta_ret_30s"])))
    up = c["ta_ret_30s"] > 0
    by = up.to_numpy()
    ask = np.where(by, c["yes_best_ask"].to_numpy("f8"),
                   1.0 - c["yes_best_bid"].to_numpy("f8"))
    keep = (m.to_numpy() & np.isfinite(ask)
            & (ask >= p["ask_lo"]) & (ask <= p["ask_hi"]))
    return c[keep], by[keep]


def _btc_ref(b):
    if _BTC_REF["df"] is None:
        btc = b[b["symbol"] == "btc"][
            ["window_start_ts", "seconds_into_window", "dist_strike_bps"]].copy()
        btc = btc.rename(columns={"dist_strike_bps": "btc_dist"})
        _BTC_REF["df"] = btc.drop_duplicates(["window_start_ts", "seconds_into_window"])
    return _BTC_REF["df"]


def _zscore(c):
    """vol-scaled distance to strike: |dist_bps| / (realized_vol_bps * sqrt(t_left)).
    The research-agent's #1 filter: distance only 'sticks' relative to how far spot
    can plausibly travel in the time remaining."""
    vol_bps = (c["realized_vol"] * 100.0).to_numpy("f8")
    tl = np.clip(c["time_left_sec"].to_numpy("f8"), 1, None)
    denom = vol_bps * np.sqrt(tl)
    return np.divide(c["abs_dist_bps"].to_numpy("f8"), denom,
                     out=np.full(len(c), np.nan), where=denom > 1e-9)


def fam_zscore(b, p):
    """Z-SCORE distance gate. consistent mode = buy favourite when the lock is
    deep relative to remaining-window vol; disagree mode = buy spot-implied side
    when book disagrees but the vol-scaled distance says spot has clearly moved."""
    m = ((b["time_left_sec"] >= p["t_lo"]) & (b["time_left_sec"] <= p["t_hi"])
         & (b["abs_dist_bps"] >= 2))
    if p["mode"] == "consistent":
        m &= b["consistent"] & b["fav_ask"].between(p["ask_lo"], p["ask_hi"])
    else:
        m &= ~b["consistent"]
    c = b[m]
    z = _zscore(c)
    keep = np.isfinite(z) & (z >= p["z_min"])
    c = c[keep]
    if p["mode"] == "consistent":
        return c, _fav_yes(c)
    by = (c["dist_strike_bps"] > 0).to_numpy()
    if p.get("ud_ask_max") is not None:
        ud = np.where(by, c["yes_best_ask"].to_numpy("f8"),
                      1.0 - c["yes_best_bid"].to_numpy("f8"))
        k = ud <= p["ud_ask_max"]
        c, by = c[k], by[k]
    return c, by


def fam_combo(b, p):
    """BEST-STACK: determinism + vol-scaled z-gate + low realized vol. The three
    filters the research synthesis says jointly underlie every survivor."""
    m = ((b["time_left_sec"] >= p["t_lo"]) & (b["time_left_sec"] <= p["t_hi"])
         & (b["consistent"]) & (b["fav_ask"].between(p["ask_lo"], p["ask_hi"]))
         & (b["realized_vol"] * 100.0 <= p["vol_max"]))
    c = b[m]
    z = _zscore(c)
    keep = np.isfinite(z) & (z >= p["z_min"])
    c = c[keep]
    return c, _fav_yes(c)


def fam_persymbol(b, p):
    """PER-SYMBOL determinism: which coins actually carry the edge (vs the macro-
    correlated illusion of 4 independent bets)."""
    m = ((b["symbol"] == p["sym"])
         & (b["time_left_sec"] >= p["t_lo"]) & (b["time_left_sec"] <= p["t_hi"])
         & (b["abs_dist_bps"] >= p["dist_min"]) & (b["consistent"])
         & (b["fav_ask"].between(p["ask_lo"], p["ask_hi"])))
    c = b[m]
    return c, _fav_yes(c)


def fam_xcoin(b, p):
    """CROSS-COIN LEAD-LAG: alts (eth/sol/xrp) tend to follow BTC. On an ALT
    window near close, require BTC's contemporaneous spot to AGREE with the alt's
    favourite (BTC dist same sign as alt dist) -> buy the alt favourite."""
    alts = b[(b["symbol"] != "btc")
             & (b["time_left_sec"] >= p["t_lo"]) & (b["time_left_sec"] <= p["t_hi"])
             & (b["abs_dist_bps"] >= p["dist_min"]) & (b["consistent"])
             & (b["fav_ask"].between(0.50, 0.95))]
    ref = _btc_ref(b)
    c = alts.merge(ref, on=["window_start_ts", "seconds_into_window"], how="left")
    keep = (c["btc_dist"].abs() >= p["btc_min"]) & (
        np.sign(c["btc_dist"].to_numpy("f8")) == np.sign(c["dist_strike_bps"].to_numpy("f8")))
    c = c[keep]
    return c, _fav_yes(c)


BUILDERS = {
    "det": fam_det, "fav": fam_fav, "longshot": fam_longshot, "e4": fam_e4,
    "oracle": fam_oracle, "micro": fam_micro, "microprice": fam_microprice,
    "flow": fam_flow, "momentum": fam_momentum, "stable": fam_stable,
    "liq": fam_liq, "tod": fam_tod, "vol": fam_vol, "xcoin": fam_xcoin,
    "zscore": fam_zscore, "combo": fam_combo, "persymbol": fam_persymbol,
    "psettle": fam_psettle,
    "ta_directional": fam_ta_directional, "ta_filter": fam_ta_filter,
    "ta_regime": fam_ta_regime, "ta_divergence": fam_ta_divergence,
}

RATIONALE = {
    "det": "book lags spot late-window; favourite reprices into the set outcome",
    "fav": "favourite-longshot bias: near-locked favourites underpriced mid-window",
    "longshot": "deep favourite is value because the longshot side is overpriced",
    "e4": "book disagrees with spot near close; book is wrong and reprices to spot",
    "oracle": "book tracks Coinbase but settles on Chainlink; trade the basis",
    "micro": "L2 depth imbalance confirms the favourite before resolution",
    "microprice": "size-weighted microprice confirms favourite beyond the mid",
    "flow": "signed taker flow confirms/fades the favourite",
    "momentum": "late spot momentum toward favourite hardens the lock",
    "stable": "calm (non-reverting) locks hold; fragile locks flip",
    "liq": "tight-spread deep-book determinism (capacity + book-quality filter)",
    "tod": "session/time-of-day regime conditioning of a base edge",
    "vol": "volatility-regime conditioning of a base edge (book lags more/less)",
    "xcoin": "alts follow BTC; require BTC spot to agree with the alt favourite",
    "zscore": "vol-scaled distance z = |dist|/(rvol*sqrt(t_left)); lock 'sticks'",
    "combo": "determinism + z-gate + low vol: the best-stack of survivor filters",
    "persymbol": "per-coin determinism: which symbols carry the edge",
    "psettle": "calibrated P(print) vs book ask: buy the side the settlement "
               "model says is underpriced by >= d (generalizes the OP4 fade)",
    "ta_directional": "base-asset TA trend says up -> buy UP (honest control; "
                      "book already prices spot, expected to fail)",
    "ta_filter": "TA regime gate on determinism: take the favourite only when "
                 "the base-asset regime label matches (does TA lift a real edge?)",
    "ta_regime": "ATR-band selection on determinism: harvest only where the "
                 "book lags spot more (higher base-asset vol = bigger overshoot)",
    "ta_divergence": "base asset moved (EMA slope + 30s return agree) but the "
                     "book hasn't repriced; buy the move-implied side",
}


# ---------------------------------------------------------------------------
# spec generation
# ---------------------------------------------------------------------------
def _grid(family, **axes):
    keys = list(axes)
    for combo in itertools.product(*[axes[k] for k in keys]):
        yield {"family": family, **dict(zip(keys, combo))}


def gen_specs():
    specs = []

    # DET — late/mid window grid
    specs += list(_grid(
        "det",
        t=[(1, 30), (1, 45), (1, 60), (1, 90), (1, 120), (1, 180),
           (60, 180), (120, 300), (180, 420)],
        dist_min=[3, 5, 8, 12, 20],
        ask=[(0.50, 0.90), (0.50, 0.95), (0.50, 0.85), (0.55, 0.85),
             (0.60, 0.90), (0.65, 0.95), (0.70, 0.95), (0.80, 0.95)]))

    # FAV — favourite-value mid-window, vol regime, side restrict
    specs += list(_grid(
        "fav",
        t=[(120, 300), (240, 420), (300, 540), (420, 660), (480, 720), (360, 600)],
        dist_min=[5, 8, 12],
        ask=[(0.55, 0.75), (0.55, 0.95), (0.60, 0.80), (0.70, 0.88),
             (0.80, 0.93), (0.85, 0.95)],
        vol_max=[None, 1.0, 1.5, 2.5],
        side=[None]))
    # FAV — single-side restricts on a focused sub-grid
    specs += list(_grid(
        "fav",
        t=[(240, 420), (300, 540), (420, 660)],
        dist_min=[5, 8],
        ask=[(0.60, 0.80), (0.70, 0.88), (0.80, 0.93)],
        vol_max=[None],
        side=["yes", "no"]))

    # LONGSHOT — deep favourite, high ask
    specs += list(_grid(
        "longshot",
        t=[(60, 300), (120, 420), (240, 600), (300, 720), (420, 780)],
        dist_min=[3, 5, 8],
        ask=[(0.85, 0.93), (0.88, 0.95), (0.90, 0.97), (0.92, 0.98)],
        side=[None, "yes", "no"]))

    # E4 — disagreement
    specs += list(_grid(
        "e4",
        t=[(1, 30), (1, 60), (1, 90), (1, 120), (60, 180), (120, 300)],
        dist_min=[5, 8, 12, 20],
        ud_ask_max=[None, 0.80, 0.90]))

    # ORACLE — chainlink-coinbase basis
    specs += list(_grid(
        "oracle",
        basis_min=[2, 3, 5, 8, 12],
        t=[(1, 60), (1, 120), (60, 300), (120, 540), (300, 720), (1, 840)],
        confirm_dist=[False, True]))

    # MICRO — L2 imbalance confirmation
    specs += list(_grid(
        "micro",
        t=[(1, 60), (1, 120), (60, 300), (120, 420)],
        dist_min=[3, 5, 8],
        imb_min=[0.50, 0.55, 0.60, 0.65, 0.70]))

    # MICROPRICE confirmation
    specs += list(_grid(
        "microprice",
        t=[(1, 60), (1, 120), (60, 300), (120, 420)],
        dist_min=[3, 5, 8],
        mp_min=[0.52, 0.55, 0.60, 0.65]))

    # FLOW — taker flow confirm/fade
    specs += list(_grid(
        "flow",
        t=[(1, 60), (1, 120), (60, 300), (120, 420)],
        dist_min=[3, 5, 8],
        thr=[0.0, 50.0, 200.0, 500.0],
        dir=["confirm", "fade"]))

    # MOMENTUM — late continuation
    specs += list(_grid(
        "momentum",
        t=[(1, 60), (1, 120), (60, 180)],
        dist_min=[8, 12],
        mom_min=[3.0, 5.0, 10.0, 15.0, 20.0]))

    # STABLE — calm vs fragile lock
    specs += list(_grid(
        "stable",
        t=[(1, 90), (1, 120), (60, 300), (120, 420)],
        dist_min=[5, 8],
        dir=["calm", "fragile"],
        av=[0.0, 2.0, 5.0]))

    # LIQ — liquidity-conditioned determinism
    specs += list(_grid(
        "liq",
        t=[(1, 90), (1, 120), (60, 300)],
        dist_min=[5, 8],
        spread_max=[0.01, 0.02, 0.03],
        depth_min=[10, 25, 50, 100]))

    # TOD — session slices on base edges
    specs += list(_grid(
        "tod",
        base=["det", "fav"],
        h=[(0, 8), (8, 16), (16, 24), (0, 6), (6, 12), (12, 18), (18, 24),
           (0, 12), (12, 24)],
        t=[(1, 120), (120, 420)],
        dist_min=[5, 8],
        ask=[(0.55, 0.90)]))

    # VOL — vol-regime slices on base edges
    specs += list(_grid(
        "vol",
        base=["det", "fav", "e4"],
        v=[(0.0, 0.7), (0.7, 1.5), (1.5, 99.0), (0.0, 1.5), (1.0, 99.0)],
        t=[(1, 120), (120, 420)],
        dist_min=[5, 8],
        ask=[(0.55, 0.90)]))

    # XCOIN — BTC lead-lag on alts
    specs += list(_grid(
        "xcoin",
        t=[(1, 60), (1, 120), (60, 300), (120, 420)],
        dist_min=[5, 8, 12],
        btc_min=[3, 5, 8, 12]))

    # ZSCORE — vol-scaled distance gate (the research-synthesis #1 filter)
    specs += list(_grid(
        "zscore",
        mode=["consistent"],
        t=[(1, 60), (1, 120), (1, 180), (60, 300), (120, 420), (240, 600), (300, 720)],
        z_min=[1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0],
        ask=[(0.50, 0.95), (0.55, 0.90), (0.60, 0.93)]))
    specs += list(_grid(
        "zscore",
        mode=["disagree"],
        t=[(1, 60), (1, 120), (1, 180), (60, 300), (120, 420), (240, 600), (300, 720)],
        z_min=[1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0],
        ud_ask_max=[None, 0.85]))

    # COMBO — determinism + z-gate + low vol (best-stack)
    specs += list(_grid(
        "combo",
        t=[(60, 300), (120, 420), (240, 600), (300, 720)],
        z_min=[1.5, 2.0, 2.5, 3.0],
        vol_max=[1.0, 1.5, 2.5],
        ask=[(0.55, 0.90), (0.70, 0.93)]))

    # PER-SYMBOL determinism
    specs += list(_grid(
        "persymbol",
        sym=["btc", "eth", "sol", "xrp"],
        t=[(1, 120), (120, 420)],
        dist_min=[5, 8],
        ask=[(0.55, 0.90), (0.80, 0.95)]))

    # DET2 — wider time bands / deeper distance
    specs += list(_grid(
        "det",
        t=[(1, 240), (60, 240), (240, 480)],
        dist_min=[5, 8, 15, 30],
        ask=[(0.50, 0.95), (0.70, 0.95)]))

    # ORACLE2 — finer basis / wider time
    specs += list(_grid(
        "oracle",
        basis_min=[1.5, 4, 10, 15],
        t=[(1, 300), (60, 540)],
        confirm_dist=[False, True]))

    # LONGSHOT2 — extra ask band
    specs += list(_grid(
        "longshot",
        t=[(120, 420), (240, 600), (300, 720)],
        dist_min=[3, 5],
        ask=[(0.86, 0.94), (0.89, 0.96)],
        side=[None]))

    # PSETTLE — model-divergence family (registered 2026-06-10, live-cost
    # campaign): calibrated settlement-prob model vs the book's ask. 324 specs.
    # Must rediscover the OP4 fade region (cheap side, low ask band, tl 60-360,
    # d>=0.10-0.15) — a failed rediscovery means the wiring is wrong.
    specs += list(_grid(
        "psettle",
        side=["fav", "cheap", "either"],
        d_thr=[0.10, 0.15, 0.20, 0.25],
        t=[(30, 180), (60, 360), (120, 420)],
        ask=[(0.05, 0.35), (0.50, 0.78), (0.05, 0.90)],
        cl_floor=[0, 5, 12]))

    # TA DIRECTIONAL — honest control across slope thresholds + ask bands
    specs += list(_grid(
        "ta_directional",
        t=[(1, 120), (120, 420), (420, 780)],
        slope_min=[0.0, 0.5, 1.0, 2.0],
        ask=[(0.05, 0.95), (0.30, 0.70), (0.40, 0.60)]))

    # TA FILTER — determinism gated by base-asset regime
    specs += list(_grid(
        "ta_filter",
        t=[(1, 120), (60, 300), (120, 420)],
        dist_min=[5, 8, 12],
        ask=[(0.55, 0.90), (0.70, 0.95)],
        regime=["range", "trend", "highvol"]))

    # TA REGIME — determinism inside an ATR band (bps)
    specs += list(_grid(
        "ta_regime",
        t=[(1, 120), (60, 300), (120, 420)],
        dist_min=[5, 8],
        ask=[(0.55, 0.90)],
        atr=[(0.0, 1.0), (1.0, 3.0), (3.0, 1e9), (1.0, 1e9)]))

    # TA DIVERGENCE — base moved, book lags; buy the move side
    specs += list(_grid(
        "ta_divergence",
        t=[(1, 120), (60, 300), (120, 420), (240, 600)],
        slope_min=[0.0, 0.5, 1.0],
        ret_min=[3.0, 5.0, 10.0, 20.0],
        ask=[(0.30, 0.55), (0.30, 0.70), (0.45, 0.65)]))

    # normalize tuple axes into named params + assign ids
    out = []
    for i, s in enumerate(specs):
        p = dict(s)
        fam = p.pop("family")
        if "t" in p:
            p["t_lo"], p["t_hi"] = p.pop("t")
        if "ask" in p:
            p["ask_lo"], p["ask_hi"] = p.pop("ask")
        if "h" in p:
            p["h_lo"], p["h_hi"] = p.pop("h")
        if "v" in p:
            p["v_lo"], p["v_hi"] = p.pop("v")
        if "atr" in p:
            p["atr_lo"], p["atr_hi"] = p.pop("atr")
        out.append({"id": f"{fam}_{i:04d}", "family": fam,
                    "rationale": RATIONALE[fam], "params": p})
    return out


# ---------------------------------------------------------------------------
# evaluation of one spec
# ---------------------------------------------------------------------------
def _depth_caps(led):
    """top-of-book capacity proxy: fraction of trades whose entry depth (USD)
    covers larger stakes (full L2 ladder-walk is Phase 4)."""
    by = led["buy_yes"].astype(bool).to_numpy()
    depth_sh = np.where(by, led["yes_ask_depth"].to_numpy("f8"),
                        led["no_ask_depth"].to_numpy("f8"))
    depth_usd = depth_sh * led["entry_ask"].to_numpy("f8")
    return {f"cap_{k}": round(float(np.mean(depth_usd >= k)), 3)
            for k in (10, 25, 50, 100)}


def run_spec(b, spec):
    fam, p = spec["family"], spec["params"]
    try:
        c, by = BUILDERS[fam](b, p)
    except Exception as e:                       # pragma: no cover
        return {**spec, "error": str(e), "n": 0}
    if c is None or len(c) == 0 or c["slug"].nunique() < MIN_N:
        return {**spec, "n": 0}
    dec = first_tick(c, by)
    led = simulate(dec, latency=2)
    n = int(len(led))
    if n < MIN_N:
        return {**spec, "n": n}
    full_ev = float(led["pnl"].mean())
    dev = led[led["split"] == "dev"]
    dev_ev = float(dev["pnl"].mean()) if len(dev) else float("nan")
    row = {**spec, "n": n, "wr": round(float(led["won"].mean() * 100), 1),
           "full_ev": round(full_ev, 3), "dev_ev": round(dev_ev, 3),
           "dev_n": int(len(dev))}
    # cheap pre-filter: must be positive on dev AND full before expensive rigor
    if not (n >= MIN_N and full_ev > 0 and dev_ev > 0):
        row["screened"] = False
        return row
    row["screened"] = True
    ev = evaluate(led, n_trials=N_TRIALS)
    lat = latency_survival(dec, latencies=LATS)
    ps = ev["per_split"]
    row.update(
        total=ev["total"],
        full_ci_lo=ps["FULL"]["lo"] if ps.get("FULL") else None,
        full_ci_hi=ps["FULL"]["hi"] if ps.get("FULL") else None,
        dev_split=ps.get("dev"), holdout_split=ps.get("holdout"),
        future_split=ps.get("future"),
        cpcv_pct_pos=ev["cpcv"].get("pct_pos"), cpcv=ev["cpcv"],
        dsr=ev["dsr"]["dsr"], dsr_full=ev["dsr"],
        lat={str(k): v for k, v in lat.items()},
        lat5_lo=(lat.get(5) or {}).get("lo"),
        lat5_fut=(lat.get(5) or {}).get("fut_ev"),
        caps=_depth_caps(led))
    return row


def main(shard: int = 0, nshards: int = 1, out_dir: str | None = None):
    out_dir = out_dir or OUT_DIR
    os.makedirs(out_dir, exist_ok=True)
    b = apply_future_override(load_base())
    specs = gen_specs()
    if shard == 0:
        with open(os.path.join(out_dir, "specs.jsonl"), "w") as f:
            for s in specs:
                f.write(json.dumps(s) + "\n")
        with open(os.path.join(out_dir, "campaign.json"), "w") as f:
            json.dump({"n_specs": len(specs), "future_start": FUTURE_START,
                       "stake_sweep": 10.0, "n_trials": N_TRIALS,
                       "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                      f, indent=2)
    mine = [s for i, s in enumerate(specs) if i % nshards == shard]
    out = (os.path.join(out_dir, "results.jsonl") if nshards == 1
           else os.path.join(out_dir, f"results_{shard}.jsonl"))
    print(f"shard {shard}/{nshards}: {len(mine)} of {len(specs)} hypotheses"
          + (f" (future override >= {FUTURE_START})" if FUTURE_START else ""))
    t0 = time.time()
    n_screened = 0
    with open(out, "w") as f:
        for i, s in enumerate(mine):
            r = run_spec(b, s)
            n_screened += int(r.get("screened", False))
            f.write(json.dumps(r) + "\n")
            f.flush()
            if (i + 1) % 25 == 0:
                dt = time.time() - t0
                print(f"  shard{shard} {i+1}/{len(mine)} screened={n_screened} "
                      f"{dt:.0f}s ({dt/(i+1):.2f}s/spec)", flush=True)
    print(f"shard {shard} done: {len(mine)} specs, {n_screened} screened, "
          f"{time.time()-t0:.0f}s -> {out}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("shard", nargs="?", type=int, default=0)
    ap.add_argument("nshards", nargs="?", type=int, default=1)
    ap.add_argument("--out-dir", default=None,
                    help="write artifacts here instead of the canonical dir")
    ap.add_argument("--future-start", default=None,
                    help="campaign override: future = days >= this; old-future "
                         "days before it are relabeled holdout (burned)")
    a = ap.parse_args()
    if a.future_start:
        set_future_override(a.future_start)
    main(a.shard, a.nshards, out_dir=a.out_dir)
