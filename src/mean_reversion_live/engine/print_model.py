"""Frozen engine copy of the settlement-print probability model.

Polymarket settles 15m Up/Down windows on the CHAINLINK print; the research side
(`research/analysis/oracle_print_model.py`, study ORACLE_PRINT_2026-06-10) fit a
logistic P(window settles UP) on dev ticks and persisted the artifact to
`data/research/oracle_print_model.json`. Two validated paper edges consume it live
(SWEEP_LIVECOST_2026-06-10 psettle_2246 + the OP4 near-strike fade), so the engine
needs the model AT TICK TIME.

Engine policy (repo convention): src/mean_reversion_live must NOT import research/.
This module is a small, frozen, line-for-line transcription of the PURE evaluation
path (`design_matrix` + standardized logistic + optional isotonic). It is pinned by
a parity test — tests/research/test_print_model_parity.py asserts
|engine_p - research_p| < 1e-9 over sampled rows of the research frame. DO NOT edit
the math here without re-running that test; if the research model is refit, the
artifact JSON changes but this code must not.

Inputs (identical names/units to the research frame):
  cl_dist_bps      Chainlink price at the tick vs the Chainlink strike, in bps
  cb_dist_bps      Coinbase spot vs the Coinbase strike, in bps (= move_pct * 100)
  cl_cb_basis_bps  (chainlink / coinbase - 1) * 1e4 at the tick
  cl_oracle_age_s  tick time (s) - `updated_at` of the round visible at the tick
  time_left_sec    seconds to window close
  realized_vol     trailing-60-tick std of tick-to-tick move_pct changes, PERCENT
  symbol           btc | eth | sol | xrp

Returns NaN when any feature is non-finite — callers must FAIL CLOSED (skip), the
same on_missing="skip" semantics as the dual-oracle gate.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

# ── pre-registered constants (mirrors of oracle_print_model.py; frozen) ─────────
SIGMA_FLOOR_BPS = 0.05      # bps per sqrt-second floor on realized vol
Z_CLIP = 8.0                # diffusion z-score clip
DIST_CLIP = 10.0            # raw distance feature clip, in 10-bps units
BASIS_CLIP = 5.0            # basis feature clip, in 10-bps units
AGE_CAP_S = 120.0           # oracle-age cap for the staleness interaction

FEATURES = ["z_cl", "z_cb", "d_cl", "d_cb", "basis", "age_gap",
            "coin_eth", "coin_sol", "coin_xrp"]


@dataclass(frozen=True)
class PrintModel:
    """Loaded artifact: standardization + coefficients (+ optional isotonic map)."""
    mu: np.ndarray
    sd: np.ndarray
    coef: np.ndarray
    intercept: float
    iso_adopted: bool = False
    iso_x: Optional[np.ndarray] = None
    iso_y: Optional[np.ndarray] = None
    version: str = ""
    fitted_at: str = ""
    source_path: str = ""
    # raw dict kept for diagnostics / logging only
    raw: dict = field(default_factory=dict, repr=False)


def load_print_model(path) -> PrintModel:
    """Load + sanity-check data/research/oracle_print_model.json.

    Raises (at boot, fail-fast) if the artifact is malformed or its feature list
    drifted from the frozen engine copy — a refit that changes the FEATURE SET
    (not just the numbers) must come with a new engine transcription + parity run.
    """
    p = Path(path)
    art = json.loads(p.read_text())
    feats = list(art.get("features") or [])
    if feats != FEATURES:
        raise ValueError(
            f"oracle_print_model features drifted: artifact {feats} != engine {FEATURES}")
    mu = np.asarray(art["mu"], dtype="f8")
    sd = np.asarray(art["sd"], dtype="f8")
    coef = np.asarray(art["coef"], dtype="f8")
    if not (len(mu) == len(sd) == len(coef) == len(FEATURES)):
        raise ValueError("oracle_print_model artifact arrays have wrong length")
    if not (np.isfinite(mu).all() and np.isfinite(sd).all() and np.isfinite(coef).all()):
        raise ValueError("oracle_print_model artifact has non-finite parameters")
    iso = art.get("isotonic") or {}
    iso_adopted = bool(art.get("iso_adopted"))
    iso_x = np.asarray(iso.get("x"), dtype="f8") if iso.get("x") else None
    iso_y = np.asarray(iso.get("y"), dtype="f8") if iso.get("y") else None
    if iso_adopted and (iso_x is None or iso_y is None):
        raise ValueError("oracle_print_model iso_adopted but isotonic map missing")
    return PrintModel(
        mu=mu, sd=sd, coef=coef, intercept=float(art["intercept"]),
        iso_adopted=iso_adopted, iso_x=iso_x, iso_y=iso_y,
        version=str(art.get("version", "")), fitted_at=str(art.get("fitted_at", "")),
        source_path=str(p), raw=art)


def p_settle_up(model: PrintModel, *, cl_dist_bps: float, cb_dist_bps: float,
                cl_cb_basis_bps: float, cl_oracle_age_s: float,
                time_left_sec: float, realized_vol: float, symbol: str) -> float:
    """P(window settles UP on Chainlink) for one tick — frozen transcription of
    research `p_settle` (design_matrix -> standardized logistic -> optional iso).

    NaN when any feature is non-finite (fail closed). The transforms below must
    stay literally identical to oracle_print_model.design_matrix.
    """
    cl = float(cl_dist_bps)
    cb = float(cb_dist_bps)
    b = float(cl_cb_basis_bps)
    age_s = float(cl_oracle_age_s)
    tl = float(time_left_sec)
    rv = float(realized_vol)
    if not (math.isfinite(cl) and math.isfinite(cb) and math.isfinite(b)
            and math.isfinite(age_s) and math.isfinite(tl) and math.isfinite(rv)):
        return float("nan")

    # sigma_bps_per_sec: realized_vol is in PERCENT units; floored.
    sig = max(rv * 100.0, SIGMA_FLOOR_BPS)
    # diffusion_z: dist / (sigma * sqrt(max(T, 1))), clipped to +-Z_CLIP.
    denom = sig * math.sqrt(max(tl, 1.0))
    z_cl = min(max(cl / denom, -Z_CLIP), Z_CLIP)
    z_cb = min(max(cb / denom, -Z_CLIP), Z_CLIP)
    d_cl = min(max(cl / 10.0, -DIST_CLIP), DIST_CLIP)
    d_cb = min(max(cb / 10.0, -DIST_CLIP), DIST_CLIP)
    basis = min(max(b / 10.0, -BASIS_CLIP), BASIS_CLIP)
    age = min(max(age_s, 0.0), AGE_CAP_S)
    age_gap = (age / 60.0) * (z_cb - z_cl)
    sym = str(symbol).lower()
    x = np.array([z_cl, z_cb, d_cl, d_cb, basis, age_gap,
                  1.0 if sym == "eth" else 0.0,
                  1.0 if sym == "sol" else 0.0,
                  1.0 if sym == "xrp" else 0.0], dtype="f8")
    if not np.isfinite(x).all():
        return float("nan")
    lin = float(((x - model.mu) / model.sd) @ model.coef + model.intercept)
    p = 1.0 / (1.0 + math.exp(-min(max(lin, -35.0), 35.0)))
    if model.iso_adopted and model.iso_x is not None and model.iso_y is not None:
        p = float(np.interp(p, model.iso_x, model.iso_y))
    return p


def p_settle_side(model: PrintModel, buy_yes: bool, **features) -> float:
    """P(the bought side wins) = p_up if buying UP else 1 - p_up (NaN-propagating)."""
    p = p_settle_up(model, **features)
    if p != p:  # NaN
        return p
    return p if buy_yes else 1.0 - p
