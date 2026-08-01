"""oracle_model_refit — rolling refit + drift alarm for the settlement-print model.

The engine paper twins psettle_ud_v1 / oracle_fade_v1 (strategies.yaml, mode
"psettle") consume data/research/oracle_print_model.json through the frozen engine
copy src/mean_reversion_live/engine/print_model.py (loaded fail-fast at boot; a
FEATURE-LIST change raises). That artifact is a ONE-SHOT dev fit (2026-05-23..27).
The sq investigation (docs/research/SQ_RESCUE_2026-06-11.md) proved frozen
probability maps in this market rot within days and can flip sign — its shipped
rolling-refit + drift-alarm pattern (research/analysis/sq_rescue.py, SQR1) is the
template for this module.

Two modes:

--check  (drift alarm, READ-ONLY; cron-friendly)
    Score the INCUMBENT artifact against a FRESH trailing-window fit on the
    trailing N days (default 3) of the joined frame (edge_lab.load_base() +
    cl_outcomes(), via oracle_print_model.tick_feature_frame). Alarm statistic
    S = decision-density-weighted mean |p_incumbent − p_fresh| over the ticks of
    the deployed twins' decision band (time_left 60..360 s) in the check window —
    the multi-feature generalization of sq_rescue.drift_metric (sq weighted a 1-D
    z-grid by the day's decision-z histogram; averaging per BAND TICK weights
    feature-space cells by exactly where decisions can fire). Threshold τ is
    derived the way sq_rescue derived its τ: the max of the same statistic under
    a MAINTAINED reference arm (the refit pipeline below walked forward at the
    registered cadence, taking effect the next day) over the frame days strictly
    BEFORE the check window — zero false alarms on the historical window by
    construction; the incumbent's would-have-fired days on that window are
    reported (backdating). Exit codes: 0 OK, 1 ALARM, 2 not enough data.

--refit [--execute]
    Refit the SAME feature set / transforms / hyperparameters (FEATURES,
    design_matrix, standardized logistic C=1.0 — NO spec drift; the engine
    fail-fasts on a feature-list change, which is the safety net) on the trailing
    refit window (default 14 frame days), HOLDING OUT the most recent 2 days.
    Future-blind gate: the candidate replaces the incumbent ONLY if its
    decision-band Brier on those held-out days is non-inferior to the
    incumbent's (brier_cand <= brier_inc + NONINF_TOL_BRIER) with at least
    MIN_GATE_TICKS labelled band ticks. Rolling refits are RAW logistic only
    (iso_adopted=false): the original study's isotonic self-rejected, and
    fitting one on the gate days would contaminate the gate. Default is DRY-RUN
    (prints the comparison, writes nothing); --execute backs the incumbent up
    alongside, writes the new JSON (version bump + fit-window metadata,
    atomically) and round-trips it through the ENGINE loader when importable.

DEPLOYMENT REALITY: the engine reads the JSON once at boot. A refit takes effect
at the NEXT RESTART only — ship refits with scheduled restarts (e.g. the Friday
unfreeze / weekly review), never mid-run.

Run from the repo root:
    uv run python -m research.analysis.oracle_model_refit --check
    uv run python -m research.analysis.oracle_model_refit --refit            # dry-run
    uv run python -m research.analysis.oracle_model_refit --refit --execute  # writes
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from research.analysis.oracle_print_model import (
    FEATURES, MODEL_PATH, brier, clustered_bootstrap_fast, iso_apply, load_model,
    _sigmoid)

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --- registered constants (test_ledger.md § "Print-model refit + drift alarm") ---
DECISION_TL = (60.0, 360.0)   # both deployed twins decide only in tl 60..360 s
CHECK_TRAIL_DAYS = 3          # --check trailing window (and the fresh-fit window)
REFIT_WINDOW_DAYS = 14        # --refit trailing window, INCLUDING the gate holdout
GATE_HOLDOUT_DAYS = 2         # most recent days held out of the refit, gate-only
NONINF_TOL_BRIER = 0.002      # non-inferiority tolerance on decision-band Brier
REFIT_CADENCE_DAYS = 7        # registered cadence (weekly review / Friday unfreeze)
MIN_INUSE_DAYS = 5            # maintained τ-arm needs >= this much history
MIN_TRAIN_DAYS = 3            # refit needs >= this many train days after the carve
MIN_FIT_TICKS = 20_000        # minimum valid labelled ticks for any fit
MIN_GATE_TICKS = 20_000       # minimum labelled band ticks in the gate holdout

DEPLOY_NOTE = ("engine loads this JSON at boot only — ship with a scheduled "
               "restart (weekly review / Friday unfreeze), never mid-run")


# ==========================================================================
# pure helpers (unit-tested in tests/research/test_oracle_model_refit.py)
# ==========================================================================
def bump_version(v: str) -> str:
    """'op-1' -> 'op-2', 'v2.3' -> 'v2.4'; no trailing integer -> '<v>-r2'."""
    m = re.match(r"^(.*?)(\d+)$", str(v or ""))
    if m:
        return f"{m.group(1)}{int(m.group(2)) + 1}"
    return f"{v}-r2" if v else "op-2"


def trailing_days(days: Sequence[str], n: int) -> List[str]:
    if n <= 0:
        raise ValueError("n must be positive")
    return list(days)[-n:]


def refit_split(days: Sequence[str], window: int = REFIT_WINDOW_DAYS,
                holdout: int = GATE_HOLDOUT_DAYS,
                min_train: int = MIN_TRAIN_DAYS) -> Tuple[List[str], List[str]]:
    """(train_days, holdout_days): the trailing `window` frame days, with the most
    recent `holdout` of them held out for the gate. The SHIPPED candidate is the
    one fit on train_days — refitting on the full window after the gate would
    un-blind it."""
    days = list(days)
    w = days[-min(window, len(days)):]
    if len(w) < holdout + min_train:
        raise ValueError(f"refit window has {len(w)} days; "
                         f"need >= {holdout + min_train}")
    return w[:-holdout], w[-holdout:]


def maintained_schedule(days: Sequence[str], window: int = REFIT_WINDOW_DAYS,
                        holdout: int = GATE_HOLDOUT_DAYS,
                        cadence: int = REFIT_CADENCE_DAYS,
                        min_days: int = MIN_INUSE_DAYS,
                        min_train: int = MIN_TRAIN_DAYS
                        ) -> Dict[str, Optional[Tuple[str, ...]]]:
    """day -> train-day tuple of the model IN USE that day under healthy operation.

    Mirrors the --refit pipeline exactly: end-of-day refits on refit_split(days
    through today) every `cadence` days, eligible once min_days of history exist,
    taking effect the NEXT day (the scheduled restart). Days before the first
    refit lands map to None (no maintained model yet)."""
    days = list(days)
    out: Dict[str, Optional[Tuple[str, ...]]] = {}
    cur: Optional[Tuple[str, ...]] = None
    last: Optional[int] = None
    for i, d in enumerate(days):
        out[d] = cur                      # in use today = the latest PAST refit
        due = last is None or (i - last) >= cadence
        if due and (i + 1) >= max(min_days, holdout + min_train):
            train, _ = refit_split(days[:i + 1], window=window, holdout=holdout,
                                   min_train=min_train)
            cur = tuple(train)
            last = i
    return out


def weighted_abs_shift(p_inuse, p_fresh) -> Tuple[float, int]:
    """Decision-density-weighted calibration shift: mean |p_inuse − p_fresh| over
    rows where BOTH are finite (each decision-band tick weighs 1 — the empirical
    tick density over feature space IS the decision density; sq_rescue's
    drift_metric is this construction collapsed onto its 1-D z-grid)."""
    a = np.asarray(p_inuse, "f8")
    b = np.asarray(p_fresh, "f8")
    m = np.isfinite(a) & np.isfinite(b)
    if not m.any():
        return float("nan"), 0
    return float(np.mean(np.abs(a[m] - b[m]))), int(m.sum())


def derive_tau(series: Dict[str, float]) -> float:
    """τ = the normal-churn envelope: max of the maintained arm's shift series
    (zero false alarms on the historical window by construction)."""
    vals = [v for v in series.values() if np.isfinite(v)]
    return float(max(vals)) if vals else float("nan")


def crossings(series: Dict[str, float], tau: float) -> List[str]:
    if not np.isfinite(tau):
        return []
    return sorted(d for d, v in series.items() if np.isfinite(v) and v >= tau)


def gate_decision(brier_candidate: float, brier_incumbent: float,
                  tol: float = NONINF_TOL_BRIER, n: Optional[int] = None,
                  min_n: int = 0) -> Tuple[bool, str]:
    """Registered replacement gate: candidate ships iff its holdout decision-band
    Brier is non-inferior (<= incumbent + tol) with enough labelled ticks.
    Fail closed on non-finite inputs."""
    if n is not None and n < min_n:
        return False, f"holdout too thin: {n} labelled band ticks < {min_n}"
    if not (np.isfinite(brier_candidate) and np.isfinite(brier_incumbent)):
        return False, "non-finite Brier (no labelled holdout ticks) — fail closed"
    if brier_candidate <= brier_incumbent + tol:
        return True, (f"candidate {brier_candidate:.5f} <= incumbent "
                      f"{brier_incumbent:.5f} + tol {tol:g}")
    return False, (f"candidate {brier_candidate:.5f} > incumbent "
                   f"{brier_incumbent:.5f} + tol {tol:g}")


def fit_logistic(X: np.ndarray, y: np.ndarray) -> dict:
    """Same fit as oracle_print_model.fit_model: standardize on the train rows,
    sklearn LogisticRegression(C=1.0, max_iter=2000). Raw logistic only — no
    isotonic on rolling refits (registered)."""
    from sklearn.linear_model import LogisticRegression
    mu = X.mean(axis=0)
    sd = np.maximum(X.std(axis=0), 1e-9)
    clf = LogisticRegression(C=1.0, max_iter=2000)
    clf.fit((X - mu) / sd, np.asarray(y, "f8").astype(int))
    return dict(mu=np.asarray(mu, "f8"), sd=np.asarray(sd, "f8"),
                coef=np.asarray(clf.coef_[0], "f8"),
                intercept=float(clf.intercept_[0]))


def predict_params(params: dict, X: np.ndarray) -> np.ndarray:
    """p_settle evaluation on pre-validated design-matrix rows — identical math to
    oracle_print_model.p_settle (standardized logistic + optional isotonic)."""
    mu = np.asarray(params["mu"], "f8")
    sd = np.asarray(params["sd"], "f8")
    lin = ((X - mu) / sd) @ np.asarray(params["coef"], "f8") + float(params["intercept"])
    p = _sigmoid(lin)
    iso = params.get("isotonic") or {}
    if params.get("iso_adopted") and iso.get("x"):
        p = iso_apply(p, iso["x"], iso["y"])
    return p


def build_artifact(fit: dict, incumbent: dict, train_days: Sequence[str],
                   holdout_days: Sequence[str], gate_info: dict, n_train: int,
                   now_iso: Optional[str] = None) -> dict:
    """New artifact dict: SAME feature list (the engine fail-fasts otherwise),
    version bump, refit metadata. Keys of the incumbent that we neither recompute
    nor obsolete (e.g. the OP3 'gate' block) are carried verbatim and listed."""
    recomputed = {"version", "fitted_at", "features", "mu", "sd", "coef",
                  "intercept", "isotonic", "iso_adopted",
                  # old-fit metadata that would be misleading on a refit:
                  "metrics", "n_dev", "n_holdout", "n_dropped_features_nan",
                  "n_train", "refit"}
    carried = {k: v for k, v in incumbent.items() if k not in recomputed}
    art = dict(carried)
    art.update({
        "version": bump_version(str(incumbent.get("version") or "")),
        "fitted_at": now_iso or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "features": list(FEATURES),
        "mu": [float(v) for v in fit["mu"]],
        "sd": [float(v) for v in fit["sd"]],
        "coef": [float(v) for v in fit["coef"]],
        "intercept": float(fit["intercept"]),
        "isotonic": None,
        "iso_adopted": False,
        "n_train": int(n_train),
        "refit": {
            "tool": "research/analysis/oracle_model_refit.py",
            "refit_of": {"version": incumbent.get("version"),
                         "fitted_at": incumbent.get("fitted_at")},
            "train_days": [train_days[0], train_days[-1]],
            "n_train_days": len(list(train_days)),
            "holdout_days": list(holdout_days),
            "gate": gate_info,
            "carried_keys": sorted(carried),
            "deployment_note": DEPLOY_NOTE,
        },
    })
    return art


def validate_artifact(art: dict) -> None:
    """Mirror of the engine loader's fail-fast checks (engine/print_model.py::
    load_print_model) so a bad artifact is rejected BEFORE it is written."""
    feats = list(art.get("features") or [])
    if feats != list(FEATURES):
        raise ValueError(f"feature list drifted: {feats} != {list(FEATURES)}")
    mu = np.asarray(art["mu"], "f8")
    sd = np.asarray(art["sd"], "f8")
    coef = np.asarray(art["coef"], "f8")
    if not (len(mu) == len(sd) == len(coef) == len(FEATURES)):
        raise ValueError("artifact arrays have wrong length")
    if not (np.isfinite(mu).all() and np.isfinite(sd).all()
            and np.isfinite(coef).all() and np.isfinite(float(art["intercept"]))):
        raise ValueError("artifact has non-finite parameters")
    if (sd <= 0).any():
        raise ValueError("artifact sd must be strictly positive")
    iso = art.get("isotonic") or {}
    if art.get("iso_adopted") and not (iso.get("x") and iso.get("y")):
        raise ValueError("iso_adopted but isotonic map missing")


def backup_path(path: str, version, fitted_at) -> str:
    """Unique sibling path for the incumbent backup (never overwritten)."""
    try:
        ts = datetime.fromisoformat(str(fitted_at)).strftime("%Y%m%dT%H%M%SZ")
    except (ValueError, TypeError):
        ts = re.sub(r"[^0-9A-Za-z]", "", str(fitted_at or ""))[:15] or "unknown"
    base = f"{path}.bak-{version or 'unversioned'}-{ts}"
    cand, n = base, 1
    while os.path.exists(cand):
        n += 1
        cand = f"{base}.{n}"
    return cand


def write_with_backup(path: str, art: dict) -> str:
    """Validate -> back the incumbent up alongside -> atomic replace (tempfile +
    os.replace, repo convention). Returns the backup path."""
    validate_artifact(art)
    with open(path) as f:
        old_text = f.read()
    old = json.loads(old_text)
    bp = backup_path(path, old.get("version"), old.get("fitted_at"))
    with open(bp, "w") as f:
        f.write(old_text)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(art, f, indent=2)
    _engine_roundtrip(tmp)
    os.replace(tmp, path)
    return bp


def _engine_roundtrip(path: str) -> str:
    """Load the candidate file through the REAL engine loader when importable
    (read-only import of src/ — the fail-fast the bot will run at boot)."""
    src = os.path.join(REPO, "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    try:
        from mean_reversion_live.engine.print_model import load_print_model
    except Exception as e:                                    # pragma: no cover
        return f"engine loader unavailable ({e}) — dict-level validation only"
    load_print_model(path)        # raises at the same place the bot would
    return "engine load_print_model OK"


# ==========================================================================
# data assembly (heavy — lazy imports, lru-cached upstream)
# ==========================================================================
def prep() -> dict:
    """One pass over the joined frame: design matrix + masks, shared by both
    modes. Population = oracle_print_model.tick_feature_frame (book_healthy,
    1 <= tl <= 600, CL features present; label = cl_outcomes' cl_up)."""
    from research.analysis.oracle_print_model import design_matrix, tick_feature_frame
    tf = tick_feature_frame()
    X, valid = design_matrix(tf)
    y = tf["cl_up"].to_numpy("f8")
    tl = tf["time_left_sec"].to_numpy("f8")
    codes, uniq = pd.factorize(tf["date"].astype(str), sort=True)
    return dict(
        X=X, y=y, codes=codes, days=[str(d) for d in uniq],
        slug=tf["slug"].to_numpy(),
        band=valid & (tl >= DECISION_TL[0]) & (tl <= DECISION_TL[1]),
        fit_mask=valid & np.isfinite(y))


def _mask_days(P: dict, day_list: Sequence[str]) -> np.ndarray:
    idx = [P["days"].index(d) for d in day_list]
    return np.isin(P["codes"], idx)


def _fit_on_days(P: dict, day_tuple: Tuple[str, ...], cache: dict):
    """Fit (or fetch) the same-spec logistic on the full model population of the
    given days; None when below MIN_FIT_TICKS (callers skip that day)."""
    if day_tuple not in cache:
        m = P["fit_mask"] & _mask_days(P, day_tuple)
        cache[day_tuple] = (fit_logistic(P["X"][m], P["y"][m])
                            if int(m.sum()) >= MIN_FIT_TICKS else None)
    return cache[day_tuple]


def check_stat(P: dict, inuse_params: dict, window_days: Sequence[str],
               cache: dict) -> Tuple[float, int]:
    """S(window) = weighted_abs_shift(incumbent/in-use vs fresh fit) over the
    decision-band ticks of `window_days`; fresh = fit on those days."""
    fresh = _fit_on_days(P, tuple(window_days), cache)
    if fresh is None:
        return float("nan"), 0
    m = P["band"] & _mask_days(P, window_days)
    if not m.any():
        return float("nan"), 0
    Xm = P["X"][m]
    return weighted_abs_shift(predict_params(inuse_params, Xm),
                              predict_params(fresh, Xm))


def shift_series(P: dict, days: Sequence[str], inuse_map: Dict[str, Optional[dict]],
                 k: int = CHECK_TRAIL_DAYS, cache: Optional[dict] = None
                 ) -> Dict[str, float]:
    """S(d) per day d = check_stat over the trailing-k window ending at d, under
    the day's in-use model (None days skipped) — computable end-of-day d."""
    cache = {} if cache is None else cache
    days = list(days)
    out: Dict[str, float] = {}
    for i, d in enumerate(days):
        params = inuse_map.get(d)
        if i < k - 1 or params is None:
            continue
        s, n = check_stat(P, params, days[i - k + 1:i + 1], cache)
        if n:
            out[d] = s
    return out


# ==========================================================================
# --check
# ==========================================================================
def run_check(n_days: int = CHECK_TRAIL_DAYS, model_path: str = MODEL_PATH) -> int:
    incumbent = load_model(model_path)
    if list(incumbent.get("features") or []) != list(FEATURES):
        print(f"FATAL: incumbent feature list drifted ({incumbent.get('features')}) "
              f"— the engine would refuse this artifact at boot")
        return 2
    P = prep()
    days = P["days"]
    need = n_days + max(MIN_INUSE_DAYS, GATE_HOLDOUT_DAYS + MIN_TRAIN_DAYS) + 1
    if len(days) < need:
        print(f"not enough frame days for a drift check: have {len(days)}, "
              f"need >= {need}")
        return 2
    check_days = trailing_days(days, n_days)
    tau_days = days[:-n_days]
    n_band = {d: int((P["band"] & _mask_days(P, [d])).sum()) for d in check_days}
    print(f"frame days {days[0]}..{days[-1]} ({len(days)}); check window "
          f"{check_days[0]}..{check_days[-1]} (band ticks/day {n_band}); "
          f"τ window {tau_days[0]}..{tau_days[-1]}")
    print(f"incumbent: version={incumbent.get('version')} "
          f"fitted_at={incumbent.get('fitted_at')}")

    cache: dict = {}
    # τ from the maintained arm on days strictly before the check window
    sched = maintained_schedule(tau_days)
    inuse_maint = {d: (_fit_on_days(P, tr, cache) if tr is not None else None)
                   for d, tr in sched.items()}
    s_maint = shift_series(P, tau_days, inuse_maint, k=n_days, cache=cache)
    tau = derive_tau(s_maint)
    print(f"\nmaintained arm (refit window {REFIT_WINDOW_DAYS}d − {GATE_HOLDOUT_DAYS}d "
          f"holdout, cadence {REFIT_CADENCE_DAYS}d, effect next day):")
    for d, v in sorted(s_maint.items()):
        print(f"  S_maintained({d}) = {v:.4f}")
    print(f"  -> τ = {tau:.4f} (max — zero false alarms on the historical window "
          f"by construction)")

    # incumbent series on ALL days (backdating on tau_days + the live headline)
    inuse_inc = {d: incumbent for d in days}
    s_inc = shift_series(P, days, inuse_inc, k=n_days, cache=cache)
    print("\nincumbent shift series:")
    for d, v in sorted(s_inc.items()):
        mark = " *" if d in check_days else ""
        print(f"  S_incumbent({d}) = {v:.4f}{mark}")
    backdated = crossings({d: v for d, v in s_inc.items() if d in tau_days}, tau)
    print(f"  would-have-fired (backdating, before the check window): "
          f"{backdated if backdated else 'never'}")

    headline = s_inc.get(check_days[-1], float("nan"))

    # descriptive calibration context (fresh is in-sample on its own window —
    # context only; the alarm keys on the registered shift statistic)
    ctx = ""
    fresh = _fit_on_days(P, tuple(check_days), cache)
    mlab = P["band"] & _mask_days(P, check_days) & np.isfinite(P["y"])
    if fresh is not None and mlab.any():
        Xl, yl = P["X"][mlab], P["y"][mlab]
        ctx = (f"band Brier on check window: incumbent "
               f"{brier(predict_params(incumbent, Xl), yl):.5f} vs fresh (in-sample) "
               f"{brier(predict_params(fresh, Xl), yl):.5f}; base rate "
               f"{float(yl.mean()):.4f} (n={int(mlab.sum()):,} labelled band ticks)")
        print(f"\ncontext: {ctx}")

    if not np.isfinite(headline) or not np.isfinite(tau):
        print("\nDRIFT CHECK INCONCLUSIVE — could not compute the shift or τ "
              "(thin data); treat as a data problem, not an all-clear")
        return 2
    if headline >= tau:
        print(f"\nDRIFT ALARM: incumbent calibration shift {headline:.4f} >= "
              f"τ {tau:.4f} on {check_days[0]}..{check_days[-1]}.")
        print("ACTION: run `--refit` (dry-run), review the gate, then "
              "`--refit --execute` + a SCHEDULED engine restart — " + DEPLOY_NOTE)
        return 1
    print(f"\nOK: incumbent calibration shift {headline:.4f} < τ {tau:.4f} "
          f"on {check_days[0]}..{check_days[-1]}")
    return 0


# ==========================================================================
# --refit [--execute]
# ==========================================================================
def run_refit(window: int = REFIT_WINDOW_DAYS, holdout: int = GATE_HOLDOUT_DAYS,
              tol: float = NONINF_TOL_BRIER, execute: bool = False,
              model_path: str = MODEL_PATH) -> int:
    incumbent = load_model(model_path)
    if list(incumbent.get("features") or []) != list(FEATURES):
        print("FATAL: incumbent feature list drifted — fix the spec before refitting")
        return 2
    P = prep()
    train_days, hold_days = refit_split(P["days"], window=window, holdout=holdout)
    print(f"refit window: trailing {window}d of the frame -> train "
          f"{train_days[0]}..{train_days[-1]} ({len(train_days)}d), gate holdout "
          f"{hold_days} (future-blind: the shipped candidate never sees them)")

    cache: dict = {}
    cand = _fit_on_days(P, tuple(train_days), cache)
    if cand is None:
        print(f"not enough labelled train ticks (< {MIN_FIT_TICKS:,}) — no refit")
        return 2
    n_train = int((P["fit_mask"] & _mask_days(P, train_days)).sum())

    # ---- gate: decision-band Brier on the held-out days ----
    mhold = _mask_days(P, hold_days)
    gate_m = P["band"] & mhold & np.isfinite(P["y"])
    n_gate = int(gate_m.sum())
    if n_gate == 0:
        print("holdout has NO labelled decision-band ticks — refusing (fail closed)")
        return 2
    Xg, yg, sg = P["X"][gate_m], P["y"][gate_m], P["slug"][gate_m]
    p_inc = predict_params(incumbent, Xg)
    p_cand = predict_params(cand, Xg)
    b_inc, b_cand = brier(p_inc, yg), brier(p_cand, yg)
    d = (p_inc - yg) ** 2 - (p_cand - yg) ** 2          # >0 = candidate better
    lo, mid, hi = (clustered_bootstrap_fast(d, sg, n=2000, seed=0)
                   if n_gate >= 3 else (float("nan"),) * 3)
    full_m = P["fit_mask"] & mhold
    b_inc_full = brier(predict_params(incumbent, P["X"][full_m]), P["y"][full_m])
    b_cand_full = brier(predict_params(cand, P["X"][full_m]), P["y"][full_m])

    print(f"\nholdout {hold_days}: {n_gate:,} labelled DECISION-BAND ticks "
          f"(tl {DECISION_TL[0]:.0f}-{DECISION_TL[1]:.0f}s), base rate "
          f"{float(yg.mean()):.4f}")
    print(f"  band Brier   incumbent {b_inc:.5f}  candidate {b_cand:.5f}  "
          f"(diff inc-cand {b_inc - b_cand:+.5f}, slug-clustered CI "
          f"[{lo:+.5f},{hi:+.5f}])")
    print(f"  full-pop Brier (context) incumbent {b_inc_full:.5f}  "
          f"candidate {b_cand_full:.5f}  (n={int(full_m.sum()):,})")

    ok, reason = gate_decision(b_cand, b_inc, tol=tol, n=n_gate,
                               min_n=MIN_GATE_TICKS)
    print(f"  gate (non-inferiority, tol {tol:g}, min {MIN_GATE_TICKS:,} ticks): "
          f"{'PASS' if ok else 'REFUSE'} — {reason}")

    gate_info = {
        "rule": f"band_brier_cand <= band_brier_inc + {tol:g} and "
                f"n_gate >= {MIN_GATE_TICKS}",
        "brier_incumbent": round(b_inc, 5), "brier_candidate": round(b_cand, 5),
        "diff_ci_slug": [round(lo, 5), round(hi, 5)], "n_gate_ticks": n_gate,
        "passed": bool(ok),
    }
    new_art = build_artifact(cand, incumbent, train_days, hold_days, gate_info,
                             n_train)
    validate_artifact(new_art)
    print(f"\ncandidate artifact: version {incumbent.get('version')} -> "
          f"{new_art['version']}, n_train={n_train:,}, features unchanged "
          f"(engine fail-fast safety net)")

    if not execute:
        print("DRY RUN — artifact untouched. Re-run with --execute to replace "
              "(then restart the engine on schedule: " + DEPLOY_NOTE + ")")
        return 0
    if not ok:
        print("REFUSED — gate failed; incumbent left in place")
        return 1
    bp = write_with_backup(model_path, new_art)
    print(f"WROTE {model_path} (version {new_art['version']}); incumbent backed "
          f"up at {bp}")
    print("NOTE: " + DEPLOY_NOTE)
    return 0


# ==========================================================================
def main(argv: Optional[Sequence[str]] = None) -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="drift alarm, read-only (exit 1 on alarm, 2 on thin data)")
    ap.add_argument("--refit", action="store_true",
                    help="trailing-window refit + future-blind gate (dry-run)")
    ap.add_argument("--execute", action="store_true",
                    help="with --refit: actually replace the artifact (backs the "
                         "incumbent up; engine picks it up at its NEXT restart)")
    ap.add_argument("--days", type=int, default=CHECK_TRAIL_DAYS,
                    help="--check trailing window in frame days")
    ap.add_argument("--window", type=int, default=REFIT_WINDOW_DAYS,
                    help="--refit trailing window (includes the gate holdout)")
    ap.add_argument("--holdout", type=int, default=GATE_HOLDOUT_DAYS,
                    help="--refit gate holdout days")
    ap.add_argument("--tol", type=float, default=NONINF_TOL_BRIER,
                    help="--refit non-inferiority tolerance on band Brier")
    ap.add_argument("--model", default=MODEL_PATH)
    args = ap.parse_args(argv)
    if args.check:
        sys.exit(run_check(n_days=args.days, model_path=args.model))
    if args.refit:
        sys.exit(run_refit(window=args.window, holdout=args.holdout, tol=args.tol,
                           execute=args.execute, model_path=args.model))
    ap.print_help()


if __name__ == "__main__":
    main()
