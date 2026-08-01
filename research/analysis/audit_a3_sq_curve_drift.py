"""AUDIT A3 — stale-quote frozen-curve stability / drift.

The sq edge keys off a FROZEN empirical curve P(Up|z) fit on 05-23..29
(data/research/stale_quote_curve.json). Question: is that curve STABLE over
time, or does it drift (silently decaying the edge)?

What this does:
  1. Build the z-distribution exactly as the sq edge does
     (loss_patterns._sq_prep / _fit_curve machinery; SAME z = dist_strike_bps /
     (realized_vol*100 * sqrt(time_left)) ).
  2. Re-fit the curve PER UTC DAY and PER 3-DAY BLOCK over the clean window.
  3. Calibration drift: project each period's per-day curve onto a COMMON z-grid
     and measure how much p_up shifts per z-bin across periods (mean / max abs
     shift vs the frozen curve, plus a "z50" crossing-point drift = where the
     curve crosses p_up=0.5).
  4. Reliability of the FROZEN curve on the FUTURE split (06-01..04) and on
     holdout, via research.lib.stats.reliability_curve (window-clustered CI).
     This is the decision-relevant test: does the frozen curve stay calibrated
     out-of-sample, and is the residual mis-calibration small / non-systematic?
  5. A single DRIFT METRIC + verdict, keyed on the FUTURE split.

Honesty: this measures whether the *probability map* drifts. It does NOT by
itself prove the *traded edge* survives the cost wall — that is the sibling
audit. But if the curve is badly mis-calibrated on future days, the sq edge is
operating on a stale map and any apparent edge is suspect.

Run: uv run python -m research.analysis.audit_a3_sq_curve_drift
"""
from __future__ import annotations
import os, json
import numpy as np
import pandas as pd

from research.analysis.loss_patterns import _base, _sq_prep, _fit_curve, JOINED, REPO
from research.lib.stats import reliability_curve

CURVE_JSON = os.path.join(REPO, "data", "research", "stale_quote_curve.json")
FROZEN_FIT_DATES = ("2026-05-23", "2026-05-29")  # per the curve's own note

# Decision band the sq edge actually trades in (loss_patterns.sq_ledger defaults).
T_LO, T_HI = 60, 840


def _load_frozen():
    with open(CURVE_JSON) as f:
        c = json.load(f)
    return np.asarray(c["z"], "f8"), np.asarray(c["p_up"], "f8")


def _curve_on_grid(train, grid, n_bins=25):
    """Re-fit per-period and resample its monotone curve onto a common z-grid."""
    if len(train[np.isfinite(train["z"])]) < 200:
        return None
    zc, p = _fit_curve(train, n_bins=n_bins)
    if len(zc) < 3:
        return None
    return np.interp(grid, zc, p, left=p[0], right=p[-1])


def _z50(zc, p):
    """z at which the curve crosses p_up = 0.5 (linear interp)."""
    zc = np.asarray(zc); p = np.asarray(p)
    if p.min() > 0.5 or p.max() < 0.5:
        return np.nan
    i = np.searchsorted(p, 0.5)
    i = max(1, min(i, len(p) - 1))
    p0, p1, z0, z1 = p[i - 1], p[i], zc[i - 1], zc[i]
    if p1 == p0:
        return float(z0)
    return float(z0 + (0.5 - p0) * (z1 - z0) / (p1 - p0))


def main():
    full = _base(pd.read_parquet(
        JOINED,
        columns=["symbol", "slug", "window_start_ts", "seconds_into_window",
                 "time_left_sec", "date", "split", "dist_strike_bps",
                 "realized_vol", "outcome_up_clean", "book_healthy", "cb_spot",
                 "start_price", "yes_mid", "spot_vel_10s_bps",
                 "yes_best_ask", "yes_best_bid", "l2_ask_depth",
                 "yes_ask_depth", "spot_vel_3s_bps"]))
    sq = _sq_prep(full)
    sq = sq[np.isfinite(sq["z"])].copy()
    # Restrict to the band the sq edge decides in (so calibration reflects the
    # ticks the edge actually keys on, not z computed at e.g. t_left<60).
    band = sq[(sq["time_left_sec"] >= T_LO) & (sq["time_left_sec"] <= T_HI)].copy()

    zfroz, pfroz = _load_frozen()
    # common grid spanning the frozen curve's support, where most mass lives
    grid = np.linspace(-3.0, 3.0, 25)
    pfroz_g = np.interp(grid, zfroz, pfroz, left=pfroz[0], right=pfroz[-1])
    z50_froz = _z50(zfroz, pfroz)

    out = {"frozen_fit_dates": list(FROZEN_FIT_DATES),
           "z50_frozen": round(z50_froz, 4),
           "band": [T_LO, T_HI]}

    # ---- 1. refit the frozen window itself, sanity vs the JSON ----
    fw = band[(band["date"] >= FROZEN_FIT_DATES[0]) & (band["date"] <= FROZEN_FIT_DATES[1])]
    g_self = _curve_on_grid(fw, grid)
    out["refit_frozenwindow_vs_json_maxabs"] = (
        round(float(np.max(np.abs(g_self - pfroz_g))), 4) if g_self is not None else None)

    # ---- 2. per-UTC-day curves vs frozen, on the common grid ----
    days = sorted(band["date"].astype(str).unique())
    perday = []
    for d in days:
        gd = _curve_on_grid(band[band["date"].astype(str) == d], grid)
        sub = band[band["date"].astype(str) == d]
        if gd is None:
            perday.append({"date": d, "n_ticks": int(len(sub)),
                           "n_windows": int(sub["slug"].nunique()),
                           "maxabs_vs_frozen": None, "meanabs_vs_frozen": None,
                           "z50": None})
            continue
        zc_d, p_d = _fit_curve(band[band["date"].astype(str) == d])
        perday.append({
            "date": d,
            "n_ticks": int(len(sub)),
            "n_windows": int(sub["slug"].nunique()),
            "maxabs_vs_frozen": round(float(np.max(np.abs(gd - pfroz_g))), 4),
            "meanabs_vs_frozen": round(float(np.mean(np.abs(gd - pfroz_g))), 4),
            "z50": (round(_z50(zc_d, p_d), 4) if np.isfinite(_z50(zc_d, p_d)) else None),
        })
    out["per_day"] = perday

    # ---- 3. per-3-day-block curves vs frozen ----
    blocks = []
    for i in range(0, len(days), 3):
        blk = days[i:i + 3]
        sub = band[band["date"].astype(str).isin(blk)]
        gb = _curve_on_grid(sub, grid)
        if gb is None:
            continue
        zc_b, p_b = _fit_curve(sub)
        blocks.append({
            "block": f"{blk[0]}..{blk[-1]}",
            "n_ticks": int(len(sub)),
            "maxabs_vs_frozen": round(float(np.max(np.abs(gb - pfroz_g))), 4),
            "meanabs_vs_frozen": round(float(np.mean(np.abs(gb - pfroz_g))), 4),
            "z50": (round(_z50(zc_b, p_b), 4) if np.isfinite(_z50(zc_b, p_b)) else None),
        })
    out["per_block"] = blocks

    # ---- 4. reliability of the FROZEN curve on each split ----
    # pred = frozen model_p at the tick's z; outcome = outcome_up_clean; cluster
    # by window (slug). This is the literal calibration of the deployed map.
    rel = {}
    grade = {}
    for split in ("dev", "holdout", "future"):
        s = band[band["split"] == split]
        if not len(s):
            continue
        pred = np.interp(np.clip(s["z"], -6, 6), zfroz, pfroz,
                         left=pfroz[0], right=pfroz[-1])
        rc = reliability_curve(pred, s["outcome_up_clean"].to_numpy("f8"),
                               s["slug"].to_numpy(), n_bins=10)
        rel[split] = rc
        # calibration error weighted by tick count (only bins where the realized
        # CI excludes the predicted value count as "systematically off").
        if rc:
            w = np.array([b["n_ticks"] for b in rc], "f8")
            err = np.array([abs(b["mean_pred"] - b["realized"]) for b in rc])
            wce = float(np.sum(w * err) / w.sum())
            off = [b for b in rc
                   if not (b["ci_lo"] <= b["mean_pred"] <= b["ci_hi"])]
            n_off = len(off)
            # signed mean (is the frozen curve biased UP or DOWN out-of-sample?)
            signed = float(np.sum(w * (np.array([b["realized"] for b in rc]) -
                                       np.array([b["mean_pred"] for b in rc]))) / w.sum())
            grade[split] = {
                "weighted_cal_error": round(wce, 4),
                "signed_realized_minus_pred": round(signed, 4),
                "n_bins": len(rc), "n_bins_ci_excludes_pred": n_off,
                "n_ticks": int(len(s)), "n_windows": int(s["slug"].nunique()),
            }
    out["reliability"] = rel
    out["calibration_grade"] = grade

    # ---- 5. headline drift metric + verdict (keyed on FUTURE) ----
    # Drift metric = future weighted calibration error, contextualised by the
    # in-sample (dev) calibration error. A curve that is as calibrated on future
    # as on dev has NOT drifted, regardless of its absolute error.
    dev_wce = grade.get("dev", {}).get("weighted_cal_error")
    fut_g = grade.get("future", {})
    fut_wce = fut_g.get("weighted_cal_error")
    fut_signed = fut_g.get("signed_realized_minus_pred")
    fut_off = fut_g.get("n_bins_ci_excludes_pred")
    fut_bins = fut_g.get("n_bins")

    drift_ratio = (round(fut_wce / dev_wce, 3)
                   if (dev_wce and fut_wce is not None and dev_wce > 1e-9) else None)
    # per-day z50 spread within the clean window = curve-shape stability
    z50s = [d["z50"] for d in perday if d["z50"] is not None]
    z50_spread = round(float(np.nanmax(z50s) - np.nanmin(z50s)), 4) if z50s else None

    out["drift_metric"] = {
        "dev_weighted_cal_error": dev_wce,
        "future_weighted_cal_error": fut_wce,
        "future_signed_bias_realized_minus_pred": fut_signed,
        "drift_ratio_future_over_dev": drift_ratio,
        "future_bins_ci_excludes_pred": f"{fut_off}/{fut_bins}" if fut_bins else None,
        "perday_z50_spread": z50_spread,
        "perday_maxabs_vs_frozen_max": round(
            float(max(d["maxabs_vs_frozen"] for d in perday
                      if d["maxabs_vs_frozen"] is not None)), 4),
    }

    # Verdict thresholds (pre-registered, conservative for a first-pass scan):
    #   STABLE  : future weighted cal error <= 0.05 AND drift_ratio <= 1.75 AND
    #             no systematic one-sided bias (|signed| <= 0.04) AND
    #             <= 2/10 bins with CI excluding pred on future.
    #   DRIFTED : future weighted cal error > 0.08 OR drift_ratio > 2.5 OR
    #             |signed bias| > 0.06 OR >= 5/10 bins off.
    #   else MARGINAL.
    def _verdict():
        if fut_wce is None:
            return "INSUFFICIENT_DATA"
        bad = (fut_wce > 0.08 or (drift_ratio is not None and drift_ratio > 2.5)
               or (fut_signed is not None and abs(fut_signed) > 0.06)
               or (fut_off is not None and fut_bins and fut_off >= 5))
        good = (fut_wce <= 0.05 and (drift_ratio is None or drift_ratio <= 1.75)
                and (fut_signed is None or abs(fut_signed) <= 0.04)
                and (fut_off is not None and fut_off <= 2))
        if bad:
            return "DRIFTED"
        if good:
            return "STABLE"
        return "MARGINAL"

    out["verdict"] = _verdict()

    print(json.dumps(out, indent=2, default=float))
    return out


if __name__ == "__main__":
    main()
