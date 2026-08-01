"""HUNT: calibration (Tier A) — fair-value calibration, NOT a stale-quote pickoff.

Mechanism under test (discovered in exploration): on the Chainlink oracle the
market's yes_mid is systematically ABOVE the realized P(Up) — i.e. YES is over-
priced / NO is under-priced — by a roughly uniform ~3c across the price curve at
a mid-window decision point. A blind "buy NO mid-window" already shows +EV on
the joined fill. This is a fair-value calibration bet (the market's implied prob
is mis-calibrated vs the oracle Polymarket actually pays), decided mid-window
with a time buffer and NO spot-jump gate — so it must be latency-flat (Tier A).

This script builds a better P(Up) than the market from decision-time features
and bets when model_p − yes_mid persists, then judges EVERY variant on the
shared edge_lab harness (Chainlink settle, depth-gated realistic fill at
entry_sec+latency, window-clustered CIs per split, latency sweep, CPCV, DSR).

Variant axes:
  - model form: NONE (trivial always-NO / always-cheap), Z-CURVE (binned
    dist_strike z-score → P(Up), fit on dev+holdout only), LOGISTIC (on
    decision-time features, fit on dev+holdout only).
  - margin threshold (how far model_p must beat the market mid).
  - time-left decision window (Tier-A: decide with a buffer, mid-window).
  - side policy: NO-only, or two-sided (buy whichever side the model says is
    under-priced).
  - regime gates (abs_dist band, vol band).

Anti-overfit: any model is FIT ONLY on dev+holdout rows and SCORED on all rows;
the FUTURE split is never used to fit. The verdict is the future-split CI lower
bound + the latency sweep, never FULL.

Run:  uv run python -m research.analysis.hunt.calibration
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from research.analysis import edge_lab as L

warnings.filterwarnings("ignore")

# ----------------------------------------------------------------------------
# Base frame + Chainlink outcome (the truth we calibrate to / settle on).
# ----------------------------------------------------------------------------
B = L.load_base()
CL = L.cl_outcomes()                       # slug -> cl_up
B = B.merge(CL, on="slug", how="inner")
FIT_MASK = B["split"].isin(["dev", "holdout"]).to_numpy()   # never fit on future

# Decision-time feature columns we are allowed to use (per the brief).
FEATS = ["dist_strike_bps", "time_left_sec", "realized_vol", "microprice",
         "abs_dist_bps"]


# ----------------------------------------------------------------------------
# Models: each returns model_p = P(Up) for every row of a frame `f`.
# Trained ONLY on dev+holdout rows; scored on whatever is passed in.
# ----------------------------------------------------------------------------
def _fit_zcurve(train: pd.DataFrame, n_bins: int = 25):
    """Binned z-curve: z = dist_strike_bps / (realized_vol_bps * sqrt(time_left)).
    Map z to realized P(Up) using monotone-ish empirical bins on the train set.
    realized_vol is a per-tick fractional vol; scale to bps-per-sqrt-sec roughly.
    """
    z = _zscore(train)
    y = train["cl_up"].to_numpy("f8")
    ok = np.isfinite(z) & np.isfinite(y)
    z, y = z[ok], y[ok]
    qs = np.quantile(z, np.linspace(0, 1, n_bins + 1))
    qs = np.unique(qs)
    idx = np.clip(np.searchsorted(qs, z, side="right") - 1, 0, len(qs) - 2)
    tab = pd.DataFrame({"b": idx, "y": y}).groupby("b")["y"].mean()
    # isotonic-ish: enforce monotone increasing in z by cumulative max blend
    centers = tab.reindex(range(len(qs) - 1)).interpolate().fillna(method="bfill").fillna(method="ffill")
    centers = centers.to_numpy()
    return {"qs": qs, "centers": centers}


def _zscore(f: pd.DataFrame) -> np.ndarray:
    # dist_strike_bps is signed (spot - strike). vol in fractional units/tick.
    tl = np.clip(f["time_left_sec"].to_numpy("f8"), 1, None)
    vol = np.clip(f["realized_vol"].to_numpy("f8"), 1e-6, None)  # frac per ~sec
    # convert frac vol to bps over the remaining horizon
    horizon_bps = vol * 1e4 * np.sqrt(tl)
    return f["dist_strike_bps"].to_numpy("f8") / np.clip(horizon_bps, 1e-6, None)


def _apply_zcurve(model, f: pd.DataFrame) -> np.ndarray:
    z = _zscore(f)
    qs, centers = model["qs"], model["centers"]
    idx = np.clip(np.searchsorted(qs, z, side="right") - 1, 0, len(qs) - 2)
    p = centers[idx]
    p = np.where(np.isfinite(z), p, 0.5)
    return np.clip(p, 0.01, 0.99)


def _fit_logit(train: pd.DataFrame):
    """Plain logistic regression on standardized decision-time features."""
    from sklearn.linear_model import LogisticRegression
    X, mu, sd = _design(train, None)
    y = train["cl_up"].to_numpy("f8")
    ok = np.isfinite(X).all(1) & np.isfinite(y)
    clf = LogisticRegression(C=1.0, max_iter=400)
    clf.fit(X[ok], y[ok].astype(int))
    return {"clf": clf, "mu": mu, "sd": sd}


def _design(f: pd.DataFrame, scaler):
    raw = np.column_stack([
        f["dist_strike_bps"].to_numpy("f8"),
        np.log(np.clip(f["time_left_sec"].to_numpy("f8"), 1, None)),
        f["realized_vol"].to_numpy("f8") * 1e4,
        (f["microprice"].to_numpy("f8") - 0.5),
        f["abs_dist_bps"].to_numpy("f8"),
        _zscore(f),
    ])
    if scaler is None:
        mu = np.nanmean(raw, axis=0)
        sd = np.nanstd(raw, axis=0)
        sd = np.where(sd > 0, sd, 1.0)
    else:
        mu, sd = scaler
    X = (raw - mu) / sd
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    if scaler is None:
        return X, mu, sd
    return X


def _apply_logit(model, f: pd.DataFrame) -> np.ndarray:
    X = _design(f, (model["mu"], model["sd"]))
    p = model["clf"].predict_proba(X)[:, 1]
    return np.clip(p, 0.01, 0.99)


# Fit the two models once on dev+holdout.
_TRAIN = B[FIT_MASK]
ZMODEL = _fit_zcurve(_TRAIN)
try:
    LMODEL = _fit_logit(_TRAIN)
    HAVE_LOGIT = True
except Exception as e:  # sklearn missing
    print("logit unavailable:", e)
    HAVE_LOGIT = False


def model_p(form: str, f: pd.DataFrame) -> np.ndarray:
    if form == "zcurve":
        return _apply_zcurve(ZMODEL, f)
    if form == "logit":
        return _apply_logit(LMODEL, f)
    raise ValueError(form)


# ----------------------------------------------------------------------------
# One variant evaluation through the shared harness.
# ----------------------------------------------------------------------------
def build_decision(name, *, tl_lo, tl_hi, form, margin, side="auto",
                   abs_lo=None, abs_hi=None, vol_lo=None, vol_hi=None,
                   mid_lo=0.05, mid_hi=0.95):
    """Gate ticks by a mid-window decision band + regime; compute model_p; the
    chosen side is the one whose model edge (model_p_side − ask_side, proxied at
    decision time by mid) clears `margin`. Returns a decision frame (one row/win).

    side: 'no' = always buy NO (the structurally under-priced side);
          'auto' = buy whichever side the model says is under-priced by >= margin.
    """
    g = B[(B["seconds_into_window"] >= tl_lo) & (B["seconds_into_window"] < tl_hi)
          & (B["time_left_sec"] >= 30) & B["book_healthy"]
          & (B["yes_mid"] >= mid_lo) & (B["yes_mid"] <= mid_hi)]
    if abs_lo is not None:
        g = g[g["abs_dist_bps"] >= abs_lo]
    if abs_hi is not None:
        g = g[g["abs_dist_bps"] < abs_hi]
    if vol_lo is not None:
        g = g[(g["realized_vol"] * 1e4) >= vol_lo]
    if vol_hi is not None:
        g = g[(g["realized_vol"] * 1e4) < vol_hi]
    if g.empty:
        return None

    if form == "none":
        # trivial: model says NO is under-priced by a flat amount; buy NO.
        mp = 1.0 - g["yes_mid"].to_numpy("f8")  # P(no) proxy not used for gating
        ymid = g["yes_mid"].to_numpy("f8")
        # under-pricing of NO = realized-belief that yes_mid is too high; gate on
        # the cross-sectional mid being within band (no model). buy_yes = False.
        buy_yes = np.zeros(len(g), dtype=bool)
        qual = np.ones(len(g), dtype=bool)
    else:
        mp = model_p(form, g)                  # model P(Up)
        ymid = g["yes_mid"].to_numpy("f8")
        yes_edge = mp - ymid                    # model thinks YES under-priced if >0
        no_edge = (1.0 - mp) - (1.0 - ymid)     # == ymid - mp ; NO under-priced if >0
        if side == "no":
            buy_yes = np.zeros(len(g), dtype=bool)
            qual = no_edge >= margin
        elif side == "yes":
            buy_yes = np.ones(len(g), dtype=bool)
            qual = yes_edge >= margin
        else:  # auto
            buy_yes = yes_edge >= no_edge
            qual = np.maximum(yes_edge, no_edge) >= margin

    g = g[qual]
    buy_yes = buy_yes[qual]
    if len(g) == 0:
        return None
    return L.first_tick(g, buy_yes)


def quick_score(dec):
    """Fast sweep scorer: ONE latency-2 simulate + light window-bootstrap of
    FULL and future only (the judge is future-lo)."""
    led = L.simulate(dec, latency=2)
    if led is None or len(led) < 3:
        return None
    from research.lib.stats import window_clustered_bootstrap as wb
    flo, _, fhi = wb(led["pnl"].values, led["slug"].values, n=800)
    fu = led[led["split"] == "future"]
    if len(fu) >= 3:
        ulo, _, uhi = wb(fu["pnl"].values, fu["slug"].values, n=800)
    else:
        ulo = uhi = float("nan")
    return dict(n=len(led), full_ev=float(led["pnl"].mean()), full_lo=flo, full_hi=fhi,
                fut_n=len(fu), fut_ev=float(fu["pnl"].mean()) if len(fu) else float("nan"),
                fut_lo=ulo, fut_hi=uhi, fut_wr=float(fu["won"].mean()*100) if len(fu) else float("nan"),
                led=led)


# ----------------------------------------------------------------------------
# The sweep.
# ----------------------------------------------------------------------------
def main():
    print("=" * 100)
    print("CALIBRATION HUNT (Tier A) — Chainlink-settled, future-split judged, latency-gated")
    print(f"train(fit) rows = {FIT_MASK.sum():,}  (dev+holdout only; future never fit)")
    print("mechanism: market yes_mid sits ABOVE Chainlink-realized P(Up) ~3c → NO under-priced")
    print("=" * 100)

    results = []  # (name, dec, score)

    def add(name, **kw):
        dec = build_decision(name, **kw)
        if dec is None or dec.empty:
            print(f"{name:30} n=0 (empty)")
            return
        sc = quick_score(dec)
        if sc is None:
            print(f"{name:30} n<3 (no fills)")
            return
        results.append((name, dec, sc))
        print(f"{name:30} n={sc['n']:>4} FULL ${sc['full_ev']:+.2f}"
              f"[{sc['full_lo']:+.2f},{sc['full_hi']:+.2f}] | "
              f"future ${sc['fut_ev']:+.2f}[{sc['fut_lo']:+.2f},{sc['fut_hi']:+.2f}]"
              f"n{sc['fut_n']} WR{sc['fut_wr']:.0f}%")

    print("\n--- A. Trivial baseline: blind NO at a mid-window decision band (no model) ---")
    add("A1 NO 60-90s", tl_lo=60, tl_hi=90, form="none", margin=0)
    add("A2 NO 120-180s", tl_lo=120, tl_hi=180, form="none", margin=0)
    add("A3 NO 300-360s", tl_lo=300, tl_hi=360, form="none", margin=0)
    add("A4 NO 30-60s(open)", tl_lo=30, tl_hi=60, form="none", margin=0)
    add("A5 NO 60-90 mid.35-.65", tl_lo=60, tl_hi=90, form="none", margin=0,
        mid_lo=0.35, mid_hi=0.65)

    print("\n--- B. Z-curve model, NO-only, vary margin (60-120s decision band) ---")
    for m in (0.00, 0.02, 0.04, 0.06, 0.10):
        add(f"B z-no m={m:.2f}", tl_lo=60, tl_hi=120, form="zcurve", margin=m, side="no")

    print("\n--- C. Z-curve model, AUTO side, vary margin ---")
    for m in (0.02, 0.04, 0.06, 0.10, 0.15):
        add(f"C z-auto m={m:.2f}", tl_lo=60, tl_hi=120, form="zcurve", margin=m, side="auto")

    if HAVE_LOGIT:
        print("\n--- D. Logistic model, NO-only, vary margin ---")
        for m in (0.00, 0.03, 0.05, 0.08, 0.12):
            add(f"D log-no m={m:.2f}", tl_lo=60, tl_hi=120, form="logit", margin=m, side="no")

        print("\n--- E. Logistic model, AUTO side, vary margin ---")
        for m in (0.03, 0.05, 0.08, 0.12, 0.18):
            add(f"E log-auto m={m:.2f}", tl_lo=60, tl_hi=120, form="logit", margin=m, side="auto")

    print("\n--- F. Decide-near-open (Tier-A buffer): NO-only across decision bands ---")
    add("F1 NO open 30-90", tl_lo=30, tl_hi=90, form="none", margin=0)
    add("F2 z-no open m.04", tl_lo=30, tl_hi=90, form="zcurve", margin=0.04, side="no")
    add("F3 NO mid 240-420", tl_lo=240, tl_hi=420, form="none", margin=0)
    add("F4 NO late 540-720", tl_lo=540, tl_hi=720, form="none", margin=0)

    print("\n--- G. Regime gates on the trivial-NO core (abs_dist / vol bands, 60-120s) ---")
    add("G1 NO abs<10", tl_lo=60, tl_hi=120, form="none", margin=0, abs_hi=10)
    add("G2 NO abs10-40", tl_lo=60, tl_hi=120, form="none", margin=0, abs_lo=10, abs_hi=40)
    add("G3 NO abs>=40", tl_lo=60, tl_hi=120, form="none", margin=0, abs_lo=40)
    add("G4 NO vol-low<8", tl_lo=60, tl_hi=120, form="none", margin=0, vol_hi=8)
    add("G5 NO vol-hi>=8", tl_lo=60, tl_hi=120, form="none", margin=0, vol_lo=8)
    add("G6 NO mid.40-.60", tl_lo=60, tl_hi=120, form="none", margin=0, mid_lo=0.40, mid_hi=0.60)

    # ---- pick BEST by future-split CI lower bound (quick scorer) ----
    print("\n" + "=" * 100)
    print("RANK by FUTURE-split CI lower bound (the judge; NOT FULL):")
    print("=" * 100)
    rank = sorted(results, key=lambda r: (-(r[2]["fut_lo"] if np.isfinite(r[2]["fut_lo"]) else -9)))
    for name, dec, sc in rank[:12]:
        print(f"{name:30} future ${sc['fut_ev']:+.2f}[{sc['fut_lo']:+.2f},{sc['fut_hi']:+.2f}]"
              f"n{sc['fut_n']} WR{sc['fut_wr']:.0f}% | FULL ${sc['full_ev']:+.2f}"
              f"[{sc['full_lo']:+.2f},{sc['full_hi']:+.2f}] n{sc['n']}")

    # ---- FULL evaluation (latency sweep + CPCV + DSR) on the top finalists ----
    import pprint
    print("\n" + "=" * 100)
    print("FULL EVAL + LATENCY SWEEP on top finalists (the real verdict):")
    print("=" * 100)
    for name, dec, sc in rank[:4]:
        print("\n" + "-" * 90)
        led = sc["led"]
        e = L.evaluate(led)
        lat = L.latency_survival(dec)
        print(L.verdict_line(name, led, lat))
        fu = e["per_split"].get("future")
        ho = e["per_split"].get("holdout")
        dv = e["per_split"].get("dev")
        print(f"  per-split: dev {dv and dv['ev']:+.2f} | holdout {ho and ho['ev']:+.2f} "
              f"| future {fu and fu['ev']:+.2f}")
        print(f"  CPCV pct_pos {e['cpcv'].get('pct_pos','?')}%  DSR {e['dsr']}")
        fut = led[led["split"] == "future"]
        if len(fut):
            print(f"  future concentration: n={len(fut)} total=${fut['pnl'].sum():+.1f} "
                  f"max|pnl|=${fut['pnl'].abs().max():.1f} "
                  f"top3share={fut['pnl'].abs().nlargest(3).sum()/fut['pnl'].abs().sum():.2f}")
        print(f"  >>> EVAL_DICT {name}: ", end="")
        pprint.pprint({"n": e["n"], "full": e["per_split"]["FULL"], "future": fu,
                       "latency": {k: v.get("ev") for k, v in lat.items()},
                       "latency_fut": {k: v.get("fut_ev") for k, v in lat.items()},
                       "cpcv": e["cpcv"].get("pct_pos"), "dsr": e["dsr"].get("dsr")})


if __name__ == "__main__":
    main()
