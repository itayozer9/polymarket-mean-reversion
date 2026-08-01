"""Oracle-print study — predict the CHAINLINK SETTLEMENT PRINT, not the market.

Polymarket settles each 15m Up/Down window on the Chainlink oracle print (CL_close vs
the CL value at window open); our signal feed is Coinbase. The deployed live primary
(det_d12_dual_live) uses a BINARY "AGREE" gate (Chainlink must agree in sign with
Coinbase at entry). This module builds the CONTINUOUS version: a calibrated
settlement-probability model P(cl_up | decision-tick features) and its two
applications, pre-registered in docs/research/test_ledger.md (OP1-OP4, 2026-06-10):

  OP1 --characterize  physics of the print process from the on-chain rounds data
                      (data/live_chainlink/): inter-update times, empirical deviation
                      threshold + heartbeat, and the dispersion of (CL_close - CL_now)
                      conditioned on time-to-close T, oracle age and the concurrent
                      Coinbase-vs-Chainlink gap.
  OP2 --fit           logistic regression (dev only) + isotonic (holdout) ->
                      data/research/oracle_print_model.json. `p_settle(df)` is the
                      importable deliverable.
       --eval-future  G1 reveal: future Brier/logloss/reliability vs (a) the book's
                      own price, (b) the AGREE 2-leaf predictor.
  OP3 --gate [--reveal]   App 1: det_d12 dual config WITHOUT the agree gate, trade iff
                      P_settle(buy side wins) >= theta*. theta* swept on DEV only;
                      --reveal scores the future block under v2 + live_guarded fills
                      vs the AGREE baseline (G2).
  OP4 --fade [--reveal]   App 2: near-strike fade — book prices favourite >= 0.75 but
                      model says P(fav wins) <= 0.60 -> buy the cheap side (G3).

Discipline: model fit on dev; isotonic on holdout; theta* on dev; the future block is
revealed once, at the end. Ledger CIs use research/lib/stats.py::
window_clustered_bootstrap; per-tick Brier-difference CIs use an algebraically
equivalent per-slug-sum bootstrap (`clustered_bootstrap_fast`, unit-tested to
reproduce the canonical function's draws).

Run from the repo root:
    uv run python -m research.analysis.oracle_print_model --characterize
    uv run python -m research.analysis.oracle_print_model --fit
    uv run python -m research.analysis.oracle_print_model --gate           # dev sweep
    uv run python -m research.analysis.oracle_print_model --eval-future    # G1 reveal
    uv run python -m research.analysis.oracle_print_model --gate --reveal  # G2 reveal
    uv run python -m research.analysis.oracle_print_model --fade --reveal  # G3 reveal
"""
from __future__ import annotations

import argparse
import functools
import glob
import json
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CL_DIR = os.path.join(REPO, "data", "live_chainlink")
MODEL_PATH = os.path.join(REPO, "data", "research", "oracle_print_model.json")

# --- pre-registered constants (test_ledger.md, Oracle-print study 2026-06-10) ---
SIGMA_FLOOR_BPS = 0.05      # bps per sqrt-second floor on realized vol
Z_CLIP = 8.0                # diffusion z-score clip
DIST_CLIP = 10.0            # raw distance feature clip, in 10-bps units
BASIS_CLIP = 5.0            # basis feature clip, in 10-bps units
AGE_CAP_S = 120.0           # oracle-age cap for the staleness interaction
TL_LO, TL_HI = 1.0, 600.0   # model population: time_left in [1, 600] s
HORIZONS = (30, 60, 120, 180, 300)          # characterization time-to-close grid
THETA_GRID = [round(x, 3) for x in np.arange(0.50, 0.9501, 0.025)]
GATE_SEEDS = (0, 1, 2, 3, 4)
STAKE = 5.0
FADE_FAV_ASK_MIN = 0.75
FADE_P_FAV_MAX = 0.60
FADE_TL = (60.0, 360.0)
FADE_CHEAP_ASK_MAX = 0.35
FADE_FILL_CEILING = 0.40
ISO_ADOPT_MIN_GAIN = 5e-4

FEATURES = ["z_cl", "z_cb", "d_cl", "d_cb", "basis", "age_gap",
            "coin_eth", "coin_sol", "coin_xrp"]

# det_d12_dual_live (strategies.yaml mirror in rejudge_live_model.CONFIGS) WITHOUT
# the agree gate — the App-1 decision universe.
DET_NOGATE = dict(mode="consistent", t_lo=1, t_hi=180, dist_min=12.0,
                  ask_lo=0.50, ask_hi=0.78, adverse_vel_max=2.0,
                  ask_hi_deep=0.85, cl_dist_hi_bps=20.0)
FADE_CFG = dict(ask_lo=0.01, ask_hi=FADE_FILL_CEILING)


# ==========================================================================
# pure helpers (unit-tested in tests/research/test_oracle_print_model.py)
# ==========================================================================
def split_round_id(round_id: int) -> tuple[int, int]:
    """Chainlink aggregator roundId = (phaseId << 64) | aggregatorRoundId."""
    rid = int(round_id)
    return rid >> 64, rid & ((1 << 64) - 1)


def dedupe_rounds(polls: pd.DataFrame) -> pd.DataFrame:
    """One row per OBSERVED on-chain round from the 15s-poll frame.

    polls: rows with symbol, timestamp_ms (poll time), round_id (string — the ids
    overflow uint64), updated_at (s), price. Returns per (symbol, round_id) the first
    observation, plus phase / agg_round (for missed-round accounting), sorted by
    (symbol, updated_at).
    """
    p = polls.dropna(subset=["round_id", "updated_at", "price"]).copy()
    if p.empty:
        return pd.DataFrame(columns=["symbol", "round_id", "phase", "agg_round",
                                     "updated_at", "price", "first_seen_ms"])
    p["round_id"] = p["round_id"].astype(str)
    r = (p.sort_values("timestamp_ms")
         .groupby(["symbol", "round_id"], as_index=False)
         .first()[["symbol", "round_id", "updated_at", "price", "timestamp_ms"]]
         .rename(columns={"timestamp_ms": "first_seen_ms"}))
    ints = r["round_id"].map(int)
    r["phase"] = ints.map(lambda x: x >> 64)
    r["agg_round"] = ints.map(lambda x: x & ((1 << 64) - 1))
    return r.sort_values(["symbol", "updated_at"]).reset_index(drop=True)


def sigma_bps_per_sec(realized_vol) -> np.ndarray:
    """Per-second vol in bps (realized_vol is in PERCENT units), floored."""
    rv = np.asarray(realized_vol, dtype="f8")
    return np.maximum(rv * 100.0, SIGMA_FLOOR_BPS)


def diffusion_z(dist_bps, sigma_bps, time_left_s) -> np.ndarray:
    """Diffusion-scaled distance dist / (sigma * sqrt(T)), clipped to +-Z_CLIP."""
    d = np.asarray(dist_bps, dtype="f8")
    s = np.asarray(sigma_bps, dtype="f8")
    t = np.maximum(np.asarray(time_left_s, dtype="f8"), 1.0)
    return np.clip(d / (s * np.sqrt(t)), -Z_CLIP, Z_CLIP)


def design_matrix(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Pre-registered feature matrix from a tick/decision frame.

    Required columns: cl_dist_bps, cb_dist_bps, cl_cb_basis_bps, cl_oracle_age_s,
    time_left_sec, realized_vol, symbol. Returns (X[n, len(FEATURES)], valid mask) —
    rows with any non-finite feature are invalid (fail-closed).
    """
    sig = sigma_bps_per_sec(df["realized_vol"].to_numpy("f8"))
    tl = df["time_left_sec"].to_numpy("f8")
    cl = df["cl_dist_bps"].to_numpy("f8")
    cb = df["cb_dist_bps"].to_numpy("f8")
    z_cl = diffusion_z(cl, sig, tl)
    z_cb = diffusion_z(cb, sig, tl)
    d_cl = np.clip(cl / 10.0, -DIST_CLIP, DIST_CLIP)
    d_cb = np.clip(cb / 10.0, -DIST_CLIP, DIST_CLIP)
    basis = np.clip(df["cl_cb_basis_bps"].to_numpy("f8") / 10.0, -BASIS_CLIP, BASIS_CLIP)
    age = np.clip(df["cl_oracle_age_s"].to_numpy("f8"), 0.0, AGE_CAP_S)
    age_gap = (age / 60.0) * (z_cb - z_cl)
    sym = df["symbol"].astype(str).str.lower().to_numpy()
    X = np.column_stack([
        z_cl, z_cb, d_cl, d_cb, basis, age_gap,
        (sym == "eth").astype("f8"), (sym == "sol").astype("f8"),
        (sym == "xrp").astype("f8")])
    valid = np.isfinite(X).all(axis=1)
    return X, valid


def brier(p, y) -> float:
    p = np.asarray(p, dtype="f8")
    y = np.asarray(y, dtype="f8")
    return float(np.mean((p - y) ** 2))


def logloss(p, y, eps: float = 1e-3) -> float:
    p = np.clip(np.asarray(p, dtype="f8"), eps, 1.0 - eps)
    y = np.asarray(y, dtype="f8")
    return float(-np.mean(y * np.log(p) + (1.0 - y) * np.log(1.0 - p)))


def clustered_bootstrap_fast(values, groups, n: int = 2000, seed: int = 0):
    """Same resampling law as research.lib.stats.window_clustered_bootstrap (identical
    RNG draws) but O(groups) per iteration via per-group sums — usable on millions of
    ticks. Returns (p5, p50, p95) of the bootstrap distribution of the mean."""
    values = np.asarray(values, dtype="f8")
    groups = np.asarray(groups)
    uniq, inv = np.unique(groups, return_inverse=True)
    sums = np.bincount(inv, weights=values, minlength=len(uniq))
    cnts = np.bincount(inv, minlength=len(uniq)).astype("f8")
    rng = np.random.default_rng(seed)
    means = np.empty(n, dtype="f8")
    for b in range(n):
        pick = rng.choice(uniq, size=len(uniq), replace=True)  # same draw as canonical
        ki = np.searchsorted(uniq, pick)
        means[b] = sums[ki].sum() / cnts[ki].sum()
    return tuple(float(x) for x in np.percentile(means, [5, 50, 95]))


def agree_leaf_fit(cb_dist_bps, cl_dist_bps, cl_up) -> dict:
    """The AGREE gate rendered as an honest 2-leaf probability model (leaf
    probabilities = dev frequencies): P(coinbase side wins | oracles agree/disagree)."""
    cb = np.asarray(cb_dist_bps, dtype="f8")
    cl = np.asarray(cl_dist_bps, dtype="f8")
    y = np.asarray(cl_up, dtype="f8")
    agree = (cl >= 0) == (cb >= 0)
    correct = (y == 1) == (cb >= 0)
    p_agree = float(correct[agree].mean()) if agree.any() else 0.5
    p_dis = float(correct[~agree].mean()) if (~agree).any() else 0.5
    return {"p_agree": p_agree, "p_disagree": p_dis}


def agree_leaf_predict(cb_dist_bps, cl_dist_bps, leaves: dict) -> np.ndarray:
    """P(cl_up) under the 2-leaf AGREE predictor."""
    cb = np.asarray(cb_dist_bps, dtype="f8")
    cl = np.asarray(cl_dist_bps, dtype="f8")
    agree = (cl >= 0) == (cb >= 0)
    p_corr = np.where(agree, leaves["p_agree"], leaves["p_disagree"])
    return np.where(cb >= 0, p_corr, 1.0 - p_corr)


def jaccard(a, b) -> float:
    a, b = set(a), set(b)
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def iso_apply(p, xs, ys) -> np.ndarray:
    """Apply a stored isotonic map (np.interp == sklearn's interpolation + clip)."""
    return np.interp(np.asarray(p, dtype="f8"), np.asarray(xs, "f8"), np.asarray(ys, "f8"))


def asof_price_age(upd_s: np.ndarray, px: np.ndarray, t_s: np.ndarray):
    """For each query time t (s): price & age of the latest update <= t (NaN if none).
    upd_s must be sorted ascending."""
    t = np.asarray(t_s, dtype="f8")
    idx = np.searchsorted(upd_s, t, side="right") - 1
    safe = np.clip(idx, 0, None)
    ok = idx >= 0
    price = np.where(ok, np.asarray(px, "f8")[safe], np.nan)
    age = np.where(ok, t - np.asarray(upd_s, "f8")[safe], np.nan)
    return price, age


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -35.0, 35.0)))


# ==========================================================================
# data assembly
# ==========================================================================
@functools.lru_cache(maxsize=1)
def load_polls() -> pd.DataFrame:
    """All chainlink poll rows with round metadata where present. round_id is kept as
    STRING — aggregator ids (phase<<64|round) overflow int64/uint64 and would collapse
    under float64 (eps ~16k rounds at 2^66)."""
    parts = []
    for f in sorted(glob.glob(os.path.join(CL_DIR, "*.csv.gz"))):
        try:
            d = pd.read_csv(f, dtype={"round_id": str})
        except Exception:
            continue
        keep = [c for c in ("timestamp_ms", "symbol", "price", "round_id",
                            "updated_at", "started_at") if c in d.columns]
        d = d[keep]
        for c in ("round_id", "updated_at", "started_at"):
            if c not in d.columns:
                d[c] = np.nan
        parts.append(d)
    p = pd.concat(parts, ignore_index=True)
    p = p[(p["price"] > 0) & p["timestamp_ms"].notna()].copy()
    p["symbol"] = p["symbol"].str.lower()
    return p.sort_values("timestamp_ms").reset_index(drop=True)


@functools.lru_cache(maxsize=1)
def load_rounds() -> pd.DataFrame:
    return dedupe_rounds(load_polls())


@functools.lru_cache(maxsize=1)
def tick_feature_frame() -> pd.DataFrame:
    """The model population: one row per (slug, second) with book_healthy,
    1 <= time_left_sec <= 600 and the Chainlink view reconstructed at the tick:

      cl_start         poll-asof Chainlink at window open (the settlement strike
                       basis, same convention as cl_outcomes / resettle_chainlink)
      cl_dist_bps      chainlink_price (per-tick poll-asof, 60s tol) vs cl_start
      cl_oracle_age_s  tick time - updated_at of the round visible at the tick
      cb_dist_bps      Coinbase distance vs the Coinbase strike (dist_strike_bps)
      cl_up            the Polymarket-true settlement label (cl_outcomes)
    """
    from research.analysis.edge_lab import load_base, cl_outcomes
    from research.analysis.oracle_settlement_gap import asof_chainlink

    b = load_base()
    cols = ["slug", "symbol", "date", "split", "window_start_ts",
            "seconds_into_window", "time_left_sec", "yes_mid", "yes_best_ask",
            "yes_best_bid", "fav_side", "fav_ask", "book_healthy", "consistent",
            "dist_strike_bps", "cb_spot", "chainlink_price", "cl_cb_basis_bps",
            "realized_vol"]
    t = b[cols]
    t = t[(t["book_healthy"] == True)  # noqa: E712
          & t["time_left_sec"].between(TL_LO, TL_HI)
          & (t["chainlink_price"] > 0) & (t["cb_spot"] > 0)].copy()
    t = t.drop_duplicates(["slug", "seconds_into_window"])
    t["symbol"] = t["symbol"].astype(str).str.lower()

    polls = load_polls()
    # cl_start per slug — poll-asof at window open (matches the label's strike basis)
    w = t[["slug", "symbol", "window_start_ts"]].drop_duplicates("slug").copy()
    w["t_start_ms"] = w["window_start_ts"].astype("int64") * 1000
    w = asof_chainlink(polls[["timestamp_ms", "symbol", "price"]], w,
                       "t_start_ms", "cl_start")
    t = t.merge(w[["slug", "cl_start"]], on="slug", how="left")
    t["cl_dist_bps"] = (t["chainlink_price"] / t["cl_start"] - 1.0) * 1e4
    t["cb_dist_bps"] = t["dist_strike_bps"]

    # per-tick oracle age via the poll the engine would have seen at the tick
    t["t_ms"] = ((t["window_start_ts"].astype("int64")
                  + t["seconds_into_window"].astype("int64")) * 1000)
    parts = []
    for sym, g in t.groupby("symbol"):
        pr = (polls[(polls["symbol"] == sym) & polls["updated_at"].notna()]
              [["timestamp_ms", "updated_at"]].sort_values("timestamp_ms"))
        pr["timestamp_ms"] = pr["timestamp_ms"].astype("int64")
        g = g.sort_values("t_ms")
        m = pd.merge_asof(g, pr, left_on="t_ms", right_on="timestamp_ms",
                          direction="backward", tolerance=120_000)
        parts.append(m.drop(columns=["timestamp_ms"], errors="ignore"))
    t = pd.concat(parts, ignore_index=True)
    t["cl_oracle_age_s"] = np.clip(t["t_ms"] / 1000.0 - t["updated_at"], 0.0, None)

    t = t.merge(cl_outcomes(), on="slug", how="left")
    return t


# ==========================================================================
# model — fit / persist / p_settle
# ==========================================================================
def fit_model(save: bool = True, verbose: bool = True) -> dict:
    """Logistic on DEV ticks; isotonic fit on HOLDOUT, adopted iff it improves DEV
    Brier (the isotonic's own out-of-sample) by > ISO_ADOPT_MIN_GAIN. No future-block
    number is computed here."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.isotonic import IsotonicRegression

    tf = tick_feature_frame()
    X, valid = design_matrix(tf)
    y = tf["cl_up"].to_numpy("f8")
    ok = valid & np.isfinite(y)
    split = tf["split"].to_numpy()
    dev = ok & (split == "dev")
    hold = ok & (split == "holdout")

    mu = X[dev].mean(axis=0)
    sd = np.maximum(X[dev].std(axis=0), 1e-9)
    clf = LogisticRegression(C=1.0, max_iter=2000)
    clf.fit((X[dev] - mu) / sd, y[dev].astype(int))

    def raw_p(mask):
        return _sigmoid(((X[mask] - mu) / sd) @ clf.coef_[0] + clf.intercept_[0])

    p_dev, p_hold = raw_p(dev), raw_p(hold)
    iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
    iso.fit(p_hold, y[hold])
    xs = np.asarray(iso.X_thresholds_, "f8")
    ys = np.asarray(iso.y_thresholds_, "f8")
    b_dev_raw = brier(p_dev, y[dev])
    b_dev_iso = brier(iso_apply(p_dev, xs, ys), y[dev])
    adopt = (b_dev_raw - b_dev_iso) > ISO_ADOPT_MIN_GAIN

    art = {
        "version": "op-1",
        "fitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "features": FEATURES,
        "mu": [float(v) for v in mu],
        "sd": [float(v) for v in sd],
        "coef": [float(v) for v in clf.coef_[0]],
        "intercept": float(clf.intercept_[0]),
        "isotonic": {"x": [float(v) for v in xs], "y": [float(v) for v in ys]},
        "iso_adopted": bool(adopt),
        "n_dev": int(dev.sum()), "n_holdout": int(hold.sum()),
        "n_dropped_features_nan": int((~valid).sum()),
        "metrics": {
            "dev": {"brier_raw": round(b_dev_raw, 5), "brier_iso": round(b_dev_iso, 5),
                    "logloss_raw": round(logloss(p_dev, y[dev]), 5),
                    "base_rate": round(float(y[dev].mean()), 4)},
            "holdout": {"brier_raw": round(brier(p_hold, y[hold]), 5),
                        "logloss_raw": round(logloss(p_hold, y[hold]), 5),
                        "base_rate": round(float(y[hold].mean()), 4)},
        },
    }
    if save:
        os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
        with open(MODEL_PATH, "w") as f:
            json.dump(art, f, indent=2)
    if verbose:
        print(f"fit on dev n={art['n_dev']:,} (holdout n={art['n_holdout']:,}; "
              f"{art['n_dropped_features_nan']:,} ticks dropped for NaN features)")
        for nm, c in zip(FEATURES, art["coef"]):
            print(f"  coef {nm:10s} {c:+.4f}")
        print(f"  intercept {art['intercept']:+.4f}")
        print(f"  dev:     brier raw {art['metrics']['dev']['brier_raw']:.5f}  "
              f"iso {art['metrics']['dev']['brier_iso']:.5f}  -> iso_adopted={adopt}")
        print(f"  holdout: brier raw {art['metrics']['holdout']['brier_raw']:.5f}  "
              f"logloss {art['metrics']['holdout']['logloss_raw']:.5f}")
        if save:
            print(f"  -> {MODEL_PATH}")
    return art


def load_model(path: str = MODEL_PATH) -> dict:
    with open(path) as f:
        return json.load(f)


def p_settle(df: pd.DataFrame, model: dict | None = None) -> np.ndarray:
    """P(window settles UP on Chainlink) for each row of `df`.

    THE importable deliverable. Required columns: cl_dist_bps, cb_dist_bps,
    cl_cb_basis_bps, cl_oracle_age_s, time_left_sec, realized_vol, symbol.
    Returns NaN where features are unavailable (callers must fail closed).
    """
    model = model or load_model()
    X, valid = design_matrix(df)
    mu = np.asarray(model["mu"], "f8")
    sd = np.asarray(model["sd"], "f8")
    lin = ((X - mu) / sd) @ np.asarray(model["coef"], "f8") + model["intercept"]
    p = _sigmoid(np.where(valid, lin, 0.0))
    if model.get("iso_adopted") and model.get("isotonic"):
        p = iso_apply(p, model["isotonic"]["x"], model["isotonic"]["y"])
    return np.where(valid, p, np.nan)


def p_settle_side(df: pd.DataFrame, buy_yes, model: dict | None = None) -> np.ndarray:
    """P(the bought side wins) = p_settle if buying UP else 1 - p_settle."""
    p = p_settle(df, model=model)
    by = np.asarray(buy_yes, dtype=bool)
    return np.where(by, p, 1.0 - p)


# ==========================================================================
# OP1 — characterize the print process
# ==========================================================================
def characterize() -> None:
    rounds = load_rounds()
    print("=== OP1a: round physics per coin (observed via ~15s polls; gaps in "
          "agg_round reveal MISSED rounds — inter-update stats are right-biased) ===")
    for sym, g in rounds.groupby("symbol"):
        g = g.sort_values("updated_at")
        dt = g["updated_at"].diff().to_numpy("f8")
        dpx = (g["price"] / g["price"].shift(1) - 1.0).abs().to_numpy("f8") * 1e4
        gap = g["agg_round"].diff().to_numpy("f8")
        same_phase = (g["phase"].diff() == 0).to_numpy()
        seq = same_phase & (gap == 1)            # consecutive observed rounds
        n_rounds = len(g)
        span = float(np.nansum(np.where(same_phase, gap, np.nan)))
        missed = 1.0 - (n_rounds - 1) / span if span > 0 else np.nan
        d_seq, p_seq = dt[seq], dpx[seq]
        moved = p_seq[p_seq > 0]
        # deviation threshold: the smallest price moves that trigger an early update
        early = p_seq[(p_seq > 0) & (d_seq < np.nanpercentile(d_seq, 50))]
        # heartbeat: forced updates with ~no price move
        hb = d_seq[p_seq <= (np.nanpercentile(moved, 5) / 2 if len(moved) else 0.01)]
        print(f"\n  {sym}: rounds observed {n_rounds:,}  missed {missed*100:.1f}% "
              f"(poll cadence ~15s)")
        print(f"    inter-update dt (consecutive rounds): p50 {np.nanpercentile(d_seq,50):.0f}s "
              f"p90 {np.nanpercentile(d_seq,90):.0f}s p99 {np.nanpercentile(d_seq,99):.0f}s "
              f"max {np.nanmax(d_seq):.0f}s")
        print(f"    |dPrice| at update (bps): p5 {np.nanpercentile(moved,5):.2f} "
              f"p50 {np.nanpercentile(moved,50):.2f} p90 {np.nanpercentile(moved,90):.2f}"
              f"  (early-update p5 {np.nanpercentile(early,5):.2f})" if len(moved) else "")
        if len(hb):
            print(f"    heartbeat (dt at ~zero price move): p50 {np.nanpercentile(hb,50):.0f}s "
                  f"p90 {np.nanpercentile(hb,90):.0f}s n={len(hb)}")

    # ---- OP1b: predictability of the final print T seconds out ----
    from research.analysis.edge_lab import load_base, cl_outcomes
    b = load_base()
    w = (b[["slug", "symbol", "window_start_ts"]].drop_duplicates("slug")
         .reset_index(drop=True))
    w["symbol"] = w["symbol"].astype(str).str.lower()
    w["close_s"] = w["window_start_ts"].astype("int64") + 900

    arr = {sym: (g["updated_at"].to_numpy("f8"), g["price"].to_numpy("f8"))
           for sym, g in rounds.sort_values("updated_at").groupby("symbol")}

    def cl_at(sym_series, t_series):
        price = np.full(len(t_series), np.nan)
        age = np.full(len(t_series), np.nan)
        for sym in arr:
            m = (sym_series == sym).to_numpy()
            if m.any():
                pr, ag = asof_price_age(arr[sym][0], arr[sym][1],
                                        t_series.to_numpy("f8")[m])
                price[m], age[m] = pr, ag
        return price, age

    w["cl_strike"], _ = cl_at(w["symbol"], w["window_start_ts"].astype("f8"))
    w["cl_close"], _ = cl_at(w["symbol"], w["close_s"].astype("f8"))

    # convention check: rounds-asof settlement sign vs the ledger label (poll-asof)
    lab = w.merge(cl_outcomes(), on="slug", how="inner")
    lab = lab[lab["cl_close"].notna() & lab["cl_strike"].notna()]
    up_rounds = (lab["cl_close"] >= lab["cl_strike"]).astype(int)
    agree_lab = float((up_rounds == lab["cl_up"]).mean())
    print(f"\n=== OP1b: settlement-print predictability ({len(w):,} windows) ===")
    print(f"  convention check: rounds-asof outcome vs cl_outcomes label agree "
          f"{agree_lab*100:.2f}% (n={len(lab):,})")

    print(f"\n  {'T':>4} {'n':>6} {'sd(d)':>6} {'p90|d|':>7} {'P>2bp':>6} {'P>5bp':>6} "
          f"{'P>10bp':>7} {'corr(gap)':>9} {'beta':>6} {'R2':>5} {'resid_sd':>8} "
          f"{'flip%':>6} {'flip%|d_cl|<5':>13}")
    for T in HORIZONS:
        q = w.copy()
        q["t_dec"] = q["close_s"].astype("f8") - T
        q["cl_now"], q["age_now"] = cl_at(q["symbol"], q["t_dec"])
        sec = 900 - T
        cb = (b[b["seconds_into_window"].between(sec - 9, sec)]
              .sort_values("seconds_into_window").groupby("slug")["cb_spot"].last())
        q = q.merge(cb.rename("cb_now"), on="slug", how="left")
        q = q[q["cl_now"].notna() & q["cl_close"].notna() & q["cb_now"].notna()
              & q["cl_strike"].notna()].copy()
        q["delta"] = (q["cl_close"] / q["cl_now"] - 1.0) * 1e4
        raw_gap = (q["cb_now"] / q["cl_now"] - 1.0) * 1e4
        q["gap"] = raw_gap - raw_gap.groupby(q["symbol"]).transform("median")
        d, g = q["delta"].to_numpy(), q["gap"].to_numpy()
        beta, r2 = np.nan, np.nan
        if len(q) > 10 and np.nanstd(g) > 0:
            beta = float(np.cov(d, g)[0, 1] / np.var(g))
            r2 = float(np.corrcoef(d, g)[0, 1] ** 2)
        resid = d - (beta * g if np.isfinite(beta) else 0.0)
        now_side = q["cl_now"] >= q["cl_strike"]
        close_side = q["cl_close"] >= q["cl_strike"]
        flip = (now_side != close_side)
        cl_dist_now = (q["cl_now"] / q["cl_strike"] - 1.0).abs() * 1e4
        near = cl_dist_now < 5
        print(f"  {T:>4} {len(q):>6} {np.std(d):>6.2f} {np.percentile(np.abs(d),90):>7.2f} "
              f"{np.mean(np.abs(d)>2)*100:>5.1f}% {np.mean(np.abs(d)>5)*100:>5.1f}% "
              f"{np.mean(np.abs(d)>10)*100:>6.1f}% {np.corrcoef(d,g)[0,1]:>9.3f} "
              f"{beta:>6.2f} {r2:>5.2f} {np.std(resid):>8.2f} "
              f"{flip.mean()*100:>5.1f}% {flip[near].mean()*100:>12.1f}%")

    # dispersion by oracle age at the decision time (T=60 slice)
    q = w.copy()
    q["t_dec"] = q["close_s"].astype("f8") - 60
    q["cl_now"], q["age_now"] = cl_at(q["symbol"], q["t_dec"])
    q = q[q["cl_now"].notna() & q["cl_close"].notna()].copy()
    q["delta"] = (q["cl_close"] / q["cl_now"] - 1.0) * 1e4
    print("\n  T=60 dispersion by oracle age at decision:")
    for lo, hi in [(0, 5), (5, 15), (15, 30), (30, 1e9)]:
        s = q[(q["age_now"] >= lo) & (q["age_now"] < hi)]
        if len(s) > 5:
            print(f"    age [{lo:>3.0f},{hi if hi < 1e9 else np.inf:>4.0f})s: n={len(s):>5} "
                  f"sd {s['delta'].std():.2f}bps  P(|d|>5bps) {np.mean(s['delta'].abs()>5)*100:.1f}%")


# ==========================================================================
# OP2 — G1: future calibration reveal
# ==========================================================================
def eval_future() -> None:
    model = load_model()
    tf = tick_feature_frame()
    X, valid = design_matrix(tf)
    y = tf["cl_up"].to_numpy("f8")
    mid = tf["yes_mid"].to_numpy("f8")
    pop = valid & np.isfinite(y) & np.isfinite(mid)
    split = tf["split"].to_numpy()

    leaves = agree_leaf_fit(tf.loc[pop & (split == "dev"), "cb_dist_bps"],
                            tf.loc[pop & (split == "dev"), "cl_dist_bps"],
                            y[pop & (split == "dev")])
    fut = pop & (split == "future")
    f = tf[fut]
    p_m = p_settle(f, model=model)
    p_b = np.clip(f["yes_mid"].to_numpy("f8"), 1e-3, 1 - 1e-3)
    p_a = agree_leaf_predict(f["cb_dist_bps"], f["cl_dist_bps"], leaves)
    yy = y[fut]
    slugs = f["slug"].to_numpy()

    print(f"=== G1 reveal — FUTURE calibration (n={fut.sum():,} ticks, "
          f"{f['slug'].nunique():,} windows; AGREE leaves fit on dev: "
          f"p_agree={leaves['p_agree']:.3f} p_disagree={leaves['p_disagree']:.3f}) ===")
    rows = [("model", p_m), ("book yes_mid", p_b), ("AGREE 2-leaf", p_a)]
    for nm, p in rows:
        print(f"  {nm:14s} brier {brier(p, yy):.5f}  logloss {logloss(p, yy):.5f}")
    d_book = (p_b - yy) ** 2 - (p_m - yy) ** 2
    d_agree = (p_a - yy) ** 2 - (p_m - yy) ** 2
    lo_b, mid_b, hi_b = clustered_bootstrap_fast(d_book, slugs, n=2000, seed=0)
    lo_a, mid_a, hi_a = clustered_bootstrap_fast(d_agree, slugs, n=2000, seed=0)
    print(f"\n  paired Brier diff (book - model):  {np.mean(d_book):+.5f}  "
          f"CI[{lo_b:+.5f},{hi_b:+.5f}]  (G1 needs >= +0.01 with lower>0)")
    print(f"  paired Brier diff (AGREE - model): {np.mean(d_agree):+.5f}  "
          f"CI[{lo_a:+.5f},{hi_a:+.5f}]  (secondary)")
    g1 = (np.mean(d_book) >= 0.01) and (lo_b > 0)
    print(f"  G1 verdict: {'PASS' if g1 else 'FAIL'}")

    print("\n  reliability (future), model vs book — bin: mean_pred -> realized [CI]:")
    for nm, p in (("model", p_m), ("book", p_b)):
        print(f"   {nm}:")
        edges = np.linspace(0, 1, 11)
        for i in range(10):
            m = (p >= edges[i]) & (p < edges[i + 1] if i < 9 else p <= edges[i + 1])
            if m.sum() < 50:
                continue
            lo, _, hi = clustered_bootstrap_fast(yy[m], slugs[m], n=500, seed=0)
            print(f"     [{edges[i]:.1f},{edges[i+1]:.1f}) n={m.sum():>7,} "
                  f"pred {p[m].mean():.3f} -> real {yy[m].mean():.3f} [{lo:.3f},{hi:.3f}]")


# ==========================================================================
# App frames + battery (rejudge_live_model pattern)
# ==========================================================================
def _battery():
    from research.analysis import rejudge_live_model as rj
    from research.sim.fills_live import load_params, DEFAULT_PARAMS_PATH
    return rj, load_params(DEFAULT_PARAMS_PATH)


def _feat_at_entry(dec: pd.DataFrame, tf: pd.DataFrame) -> pd.DataFrame:
    """Merge the per-tick model features onto a decision frame at (slug, entry_sec)."""
    cols = ["slug", "seconds_into_window", "cl_dist_bps", "cb_dist_bps",
            "cl_cb_basis_bps", "cl_oracle_age_s", "realized_vol"]
    f = tf[cols].drop_duplicates(["slug", "seconds_into_window"])
    out = dec.merge(f, left_on=["slug", "entry_sec"],
                    right_on=["slug", "seconds_into_window"], how="left")
    out = out.drop(columns=["seconds_into_window"], errors="ignore")
    out["time_left_sec"] = out["time_left"]
    return out


def det_decisions_nogate(b) -> pd.DataFrame:
    rj, _ = _battery()
    rj.CONFIGS.setdefault("det_d12_nogate", DET_NOGATE)
    return rj.decisions_for("det_d12_nogate", b)


def agree_baseline_decisions(b) -> pd.DataFrame:
    """The deployed det_d12_dual_live decisions (binary AGREE gate), exact rejudge path."""
    rj, _ = _battery()
    dec = rj.decisions_for("det_d12_dual_live", b)
    return rj._apply_dual_gate(dec, b)


def _ci_line(t: pd.DataFrame, label: str) -> dict:
    from research.lib.stats import window_clustered_bootstrap
    f = t[t["filled"] == 1]
    n_sig = int(len(t))
    if len(f) < 3:
        print(f"    {label:34s} n_sig={n_sig:>4} fills={len(f):>4} (too few fills)")
        return dict(n_signals=n_sig, n_fills=int(len(f)), ev=None, lo=None, hi=None)
    lo, _, hi = window_clustered_bootstrap(f["pnl"].values, f["slug"].values, n=2000)
    r = dict(n_signals=n_sig, n_fills=int(len(f)),
             ev=float(f["pnl"].mean()), lo=float(lo), hi=float(hi),
             wr=float(f["won"].mean() * 100), total=float(f["pnl"].sum()))
    print(f"    {label:34s} n_sig={n_sig:>4} fills={r['n_fills']:>4} "
          f"EV ${r['ev']:+.3f} [{r['lo']:+.3f},{r['hi']:+.3f}] WR {r['wr']:.1f}% "
          f"total ${r['total']:+.1f}")
    return r


def gate(reveal: bool, stake: float = STAKE) -> None:
    from research.analysis.edge_lab import load_base
    rj, params = _battery()
    model = load_model()
    b = load_base()
    tf = tick_feature_frame()

    dec = det_decisions_nogate(b)
    dec = _feat_at_entry(dec, tf)
    p_up = p_settle(dec, model=model)
    dec["p_win"] = np.where(dec["buy_yes"].astype(bool), p_up, 1.0 - p_up)
    n_all = len(dec)
    dec = dec[np.isfinite(dec["p_win"])].copy()
    print(f"App 1 — det_d12 dual config, NO agree gate: {n_all} signals, "
          f"{n_all - len(dec)} dropped (no CL features, fail-closed) -> {len(dec)}")

    base = agree_baseline_decisions(b)
    print(f"AGREE baseline (deployed det_d12_dual_live): {len(base)} signals")

    # sanity: reproduce the documented baseline under both fill models (FULL sample)
    print("\n  sanity — AGREE baseline, full sample (expect v2 ~$1.13/fill, "
          "live_guarded ~$0.87/fill at $5, jsonl 2026-06-10):")
    cfg_dual = rj.CONFIGS["det_d12_dual_live"]
    for mode in ("v2", "live_guarded"):
        t = rj.simulate_config(base, cfg_dual, mode, params, stake, seed=0)
        _ci_line(t, f"AGREE full {mode}")

    # ---- dev sweep (selection happens HERE, future-blind) ----
    dev = dec[dec["split"] == "dev"]
    base_dev = base[base["split"] == "dev"]
    agree_dev_ev, agree_dev_tot = [], []
    for s in GATE_SEEDS:
        t = rj.simulate_config(base_dev, cfg_dual, "live_guarded", params, stake, seed=s)
        f = t[t["filled"] == 1]
        agree_dev_ev.append(f["pnl"].mean() if len(f) else np.nan)
        agree_dev_tot.append(f["pnl"].sum() if len(f) else 0.0)
    agree_ev = float(np.nanmean(agree_dev_ev))
    print(f"\n  dev sweep (live_guarded, seeds {GATE_SEEDS}, stake ${stake:.0f}); "
          f"AGREE dev EV/fill ${agree_ev:+.3f}, total ${np.mean(agree_dev_tot):+.1f}, "
          f"n_sig {len(base_dev)}")
    print(f"  {'theta':>6} {'n_sig':>6} {'fills':>6} {'EV/fill':>8} {'total':>8} {'v2 EV':>7}")
    rows = []
    for th in THETA_GRID:
        sub = dev[dev["p_win"] >= th]
        evs, tots, fls = [], [], []
        for s in GATE_SEEDS:
            t = rj.simulate_config(sub, DET_NOGATE, "live_guarded", params, stake, seed=s)
            f = t[t["filled"] == 1]
            evs.append(f["pnl"].mean() if len(f) else np.nan)
            tots.append(f["pnl"].sum() if len(f) else 0.0)
            fls.append(len(f))
        tv = rj.simulate_config(sub, DET_NOGATE, "v2", params, stake, seed=0)
        fv = tv[tv["filled"] == 1]
        v2ev = fv["pnl"].mean() if len(fv) else np.nan
        r = dict(theta=th, n_sig=len(sub), fills=float(np.mean(fls)),
                 ev=float(np.nanmean(evs)), total=float(np.mean(tots)),
                 v2_ev=float(v2ev) if np.isfinite(v2ev) else None)
        rows.append(r)
        print(f"  {th:>6.3f} {r['n_sig']:>6} {r['fills']:>6.1f} {r['ev']:>8.3f} "
              f"{r['total']:>8.1f} {r['v2_ev'] if r['v2_ev'] is not None else float('nan'):>7.3f}")
    ok = [r for r in rows if np.isfinite(r["ev"]) and r["ev"] >= agree_ev]
    pick = (max(ok, key=lambda r: r["total"]) if ok
            else max([r for r in rows if np.isfinite(r["ev"])], key=lambda r: r["ev"]))
    theta = pick["theta"]
    print(f"\n  theta* = {theta} (rule: max dev total s.t. dev EV/fill >= AGREE's "
          f"{agree_ev:+.3f}; {'constraint satisfiable' if ok else 'FALLBACK max EV/fill'})")
    model.setdefault("gate", {})
    model["gate"] = {"theta": theta, "selected_on": "dev", "rule": "max_total_st_ev>=agree",
                     "agree_dev_ev": round(agree_ev, 4),
                     "dev_row": {k: (round(v, 4) if isinstance(v, float) else v)
                                 for k, v in pick.items()}}
    with open(MODEL_PATH, "w") as fh:
        json.dump(model, fh, indent=2)
    print(f"  theta* persisted to {MODEL_PATH}")

    if not reveal:
        print("\n  (future block NOT touched — run with --reveal for G2)")
        return

    # ---- G2 reveal ----
    print(f"\n=== G2 reveal — FUTURE block, theta*={theta}, stake ${stake:.0f} ===")
    fut = dec[(dec["split"] == "future") & (dec["p_win"] >= theta)]
    fut_all = dec[dec["split"] == "future"]
    base_fut = base[base["split"] == "future"]
    print(f"  future n_sig: model-gated {len(fut)} | ungated {len(fut_all)} | "
          f"AGREE {len(base_fut)}")
    res = {}
    for nm, frame, cfg in (("model-gate", fut, DET_NOGATE),
                           ("ungated", fut_all, DET_NOGATE),
                           ("AGREE", base_fut, cfg_dual)):
        for mode in ("v2", "live_guarded"):
            t = rj.simulate_config(frame, cfg, mode, params, stake, seed=0)
            res[(nm, mode)] = _ci_line(t, f"{nm} {mode}")
        evs = []
        for s in GATE_SEEDS:
            t = rj.simulate_config(frame, cfg, "live_guarded", params, stake, seed=s)
            f = t[t["filled"] == 1]
            evs.append(f["pnl"].mean() if len(f) else np.nan)
        print(f"    {nm + ' live_guarded seeds0-4':34s} EV ${np.nanmean(evs):+.3f} "
              f"(sd {np.nanstd(evs):.3f})")
    m = res[("model-gate", "live_guarded")]
    a = res[("AGREE", "live_guarded")]
    g2 = (m["ev"] is not None and a["ev"] is not None and m["ev"] >= a["ev"]
          and m["lo"] > 0 and m["n_signals"] >= a["n_signals"])
    print(f"\n  G2 verdict: {'PASS' if g2 else 'FAIL'} "
          f"(need EV>=AGREE ${a['ev'] if a['ev'] is not None else float('nan'):+.3f}, "
          f"CI lower>0, n_sig>={a['n_signals']})")


def fade_decisions(tf: pd.DataFrame, model: dict) -> pd.DataFrame:
    """App-2 decision frame: first tick per window where the book prices the
    favourite >= 0.75 but the model gives it <= 0.60, 60-360s left, healthy book,
    cheap side ask <= 0.35. Buy the CHEAP side at its YES-equivalent ask."""
    c = tf[tf["time_left_sec"].between(FADE_TL[0], FADE_TL[1])
           & (tf["fav_ask"] >= FADE_FAV_ASK_MIN)].copy()
    p_up = p_settle(c, model=model)
    fav_yes = c["fav_side"].astype(str).str.lower().isin(("yes", "up")).to_numpy()
    p_fav = np.where(fav_yes, p_up, 1.0 - p_up)
    cheap_ask = np.where(fav_yes, 1.0 - c["yes_best_bid"].to_numpy("f8"),
                         c["yes_best_ask"].to_numpy("f8"))
    qual = (np.isfinite(p_fav) & (p_fav <= FADE_P_FAV_MAX)
            & np.isfinite(cheap_ask) & (cheap_ask <= FADE_CHEAP_ASK_MAX)
            & (cheap_ask > 0.01))
    q = c[qual].copy()
    q["buy_yes"] = ~fav_yes[qual]
    q["entry_ask"] = cheap_ask[qual]
    q["p_fav"] = p_fav[qual]
    first = (q.sort_values(["slug", "seconds_into_window"])
             .groupby("slug", as_index=False).first())
    first = first.rename(columns={"seconds_into_window": "entry_sec",
                                  "time_left_sec": "time_left"})
    keep = ["slug", "symbol", "date", "split", "entry_sec", "time_left", "buy_yes",
            "entry_ask", "p_fav", "fav_ask", "cl_dist_bps"]
    return first[[c2 for c2 in keep if c2 in first.columns]]


def fade(reveal: bool, stake: float = STAKE) -> None:
    from research.analysis.edge_lab import load_base
    rj, params = _battery()
    model = load_model()
    b = load_base()
    tf = tick_feature_frame()

    dec = fade_decisions(tf, model)
    print(f"App 2 — near-strike fade: {len(dec)} decisions "
          f"(fav_ask>={FADE_FAV_ASK_MIN}, P(fav)<= {FADE_P_FAV_MAX}, "
          f"tl {FADE_TL[0]:.0f}-{FADE_TL[1]:.0f}s, cheap ask<={FADE_CHEAP_ASK_MAX})")
    print(f"  per split: {dec['split'].value_counts().to_dict()}")
    print(f"  per coin:  {dec['symbol'].value_counts().to_dict()}")
    ea = dec["entry_ask"]
    print(f"  entry-ask distribution: p10 {ea.quantile(0.1):.2f} p50 {ea.quantile(0.5):.2f} "
          f"p90 {ea.quantile(0.9):.2f} max {ea.max():.2f}")

    # overlap with fav_disagree (is this NEW volume or the same edge?)
    fd = rj.decisions_for("fav_disagree", b)
    jac = jaccard(dec["slug"], fd["slug"])
    print(f"  Jaccard slug-overlap vs fav_disagree ({len(fd)} decisions): {jac:.3f} "
          f"(G3 needs < 0.5)")

    # dev/holdout preview is allowed; future only with --reveal
    for sp in ("dev", "holdout"):
        sub = dec[dec["split"] == sp]
        print(f"\n  {sp} preview:")
        for mode in ("v2", "live_guarded"):
            t = rj.simulate_config(sub, FADE_CFG, mode, params, stake, seed=0)
            _ci_line(t, f"{sp} {mode}")

    if not reveal:
        print("\n  (future block NOT touched — run with --reveal for G3)")
        return

    print(f"\n=== G3 reveal — FUTURE block, stake ${stake:.0f} ===")
    fut = dec[dec["split"] == "future"]
    res = {}
    for mode in ("v2", "live_guarded"):
        t = rj.simulate_config(fut, FADE_CFG, mode, params, stake, seed=0)
        res[mode] = _ci_line(t, f"future {mode}")
    evs = []
    for s in GATE_SEEDS:
        t = rj.simulate_config(fut, FADE_CFG, "live_guarded", params, stake, seed=s)
        f = t[t["filled"] == 1]
        evs.append(f["pnl"].mean() if len(f) else np.nan)
    print(f"    {'future live_guarded seeds0-4':34s} EV ${np.nanmean(evs):+.3f} "
          f"(sd {np.nanstd(evs):.3f})")
    lg = res["live_guarded"]
    g3 = (lg["ev"] is not None and lg["lo"] > 0 and lg["n_fills"] >= 30 and jac < 0.5)
    print(f"\n  G3 verdict: {'PASS' if g3 else 'FAIL'} "
          f"(need CI lower>0, n_fills>=30, Jaccard<0.5)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--characterize", action="store_true")
    ap.add_argument("--fit", action="store_true")
    ap.add_argument("--eval-future", action="store_true")
    ap.add_argument("--gate", action="store_true")
    ap.add_argument("--fade", action="store_true")
    ap.add_argument("--reveal", action="store_true",
                    help="touch the future block (G2/G3 headline)")
    ap.add_argument("--stake", type=float, default=STAKE)
    args = ap.parse_args()
    if args.characterize:
        characterize()
    if args.fit:
        fit_model()
    if args.eval_future:
        eval_future()
    if args.gate:
        gate(reveal=args.reveal, stake=args.stake)
    if args.fade:
        fade(reveal=args.reveal, stake=args.stake)
    if not any((args.characterize, args.fit, args.eval_future, args.gate, args.fade)):
        ap.print_help()


if __name__ == "__main__":
    main()
