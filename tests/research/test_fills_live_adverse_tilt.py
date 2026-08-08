"""Adverse-selection tilt in the live fill model (2026-08-07 recalibration).

Why this exists: the model used to draw the zero-fill hazard independently of the
trade's outcome, while real live misses are adversely selected (pipeline fit over 944
labelled live attempts: P(no-fill | winner) 0.4634 vs P(no-fill | loser) 0.3320, so
lambda 1.3959, bootstrap CI [1.159, 1.715], excluding 1.0; the 1.42-ish values used
below are arbitrary fixture constants, not the fit). That error credited a
strategy with its AVERAGE trade when reality hands it the below-average half, and it
is why `fav_disagree_hi_live` was promoted at a predicted +$2.03/fill and delivered
-$1.40 on real money.

Pure arithmetic + literal fixtures, no I/O and no mocking (repo convention).
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import numpy as np
import pandas as pd
import pytest

from research.sim.fills_live import (
    LiveFillParams,
    _fit_adverse_tilt,
    calibrate_from_frames,
    simulate_taker_entry,
)


def _params(zero=0.4, tilt=1.0):
    table = {d: {t: {"p": zero, "n": 50} for t in ("tl<=60", "tl61-180", "tl>180")}
             for d in ("d<0.5", "d0.5-1", "d1-2", "d>=2")}
    return LiveFillParams(version="test", calibration_window=("2026-07-01", "2026-08-07"),
                          zero_fill_prob=table, kappa=1.0, latency_samples_s=[1.0],
                          mean_slip_filled=0.0, knife_rate_legacy=0.1, n_attempts=50,
                          validation={}, adverse_tilt=tilt)


# --- the hazard split -------------------------------------------------------

def test_wins_none_is_exactly_the_old_behaviour():
    """Every pre-existing caller passes no outcome and must be unaffected."""
    p = _params(zero=0.4, tilt=1.42)
    assert p.zero_p_adv(1.0, 120.0, q_win=0.6, wins=None) == p.zero_p(1.0, 120.0) == 0.4


def test_tilt_of_one_leaves_winners_and_losers_identical():
    p = _params(zero=0.4, tilt=1.0)
    assert p.zero_p_adv(1.0, 120.0, 0.6, True) == p.zero_p_adv(1.0, 120.0, 0.6, False) == 0.4


def test_winners_miss_more_than_losers_when_tilted():
    p = _params(zero=0.4, tilt=1.42)
    assert p.zero_p_adv(1.0, 120.0, 0.6, True) > 0.4 > p.zero_p_adv(1.0, 120.0, 0.6, False)


def test_marginal_hazard_is_preserved_by_the_split():
    """The binned table is calibrated on the OVERALL fill rate; the split must only
    reallocate WHICH attempts miss, never change how many."""
    for q in (0.05, 0.3, 0.5, 0.75, 0.95):
        for lam in (1.0, 1.42, 2.5):
            p = _params(zero=0.4, tilt=lam)
            pw = p.zero_p_adv(1.0, 120.0, q, True)
            pl = p.zero_p_adv(1.0, 120.0, q, False)
            assert q * pw + (1 - q) * pl == pytest.approx(0.4), (q, lam)
            assert pw == pytest.approx(lam * pl)


def test_hazard_stays_a_probability_under_an_extreme_tilt():
    p = _params(zero=0.9, tilt=10.0)
    for wins in (True, False):
        v = p.zero_p_adv(1.0, 120.0, 0.9, wins)
        assert 0.0 <= v <= 1.0


# --- what it does end to end ------------------------------------------------

def test_tilt_lowers_the_filled_cohort_win_rate():
    """THE point of the change. Simulate the same 60%-winner signal set twice; the
    tilted model must fill proportionally fewer of the winners, so the cohort that
    actually gets filled looks worse. An untilted model reports the true 60%."""
    book_px, book_sz = [0.60, 0.62], [100.0, 100.0]
    outcomes = [i % 10 < 6 for i in range(4000)]      # exactly 60% winners

    def filled_win_rate(tilt):
        p = _params(zero=0.4, tilt=tilt)
        rng = np.random.default_rng(7)
        got = [w for w in outcomes
               if simulate_taker_entry(book_px, book_sz, 10.0, entry_ask=0.60,
                                       max_ask=0.65, time_left=120.0, rng=rng,
                                       params=p, wins=w).filled]
        return sum(got) / len(got)

    flat, tilted = filled_win_rate(1.0), filled_win_rate(1.42)
    assert abs(flat - 0.60) < 0.03, f"untilted should recover the true rate, got {flat}"
    assert tilted < flat - 0.03, f"tilted {tilted} should be materially below {flat}"


# --- calibration ------------------------------------------------------------

def test_fit_recovers_a_planted_tilt():
    """20 winners of which 12 miss (0.60) and 20 losers of which 6 miss (0.30)
    => lambda = 2.0."""
    rows = ([{"zero": True, "won": True}] * 12 + [{"zero": False, "won": True}] * 8
            + [{"zero": True, "won": False}] * 6 + [{"zero": False, "won": False}] * 14)
    lam, ci, diag = _fit_adverse_tilt(pd.DataFrame(rows), n_boot=2000)
    assert lam == pytest.approx(2.0)
    assert diag["n_win"] == 20 and diag["n_lose"] == 20
    assert ci[0] < 2.0 < ci[1]


def test_fit_falls_back_to_one_when_too_thin():
    rows = [{"zero": True, "won": True}, {"zero": False, "won": False}]
    lam, ci, diag = _fit_adverse_tilt(pd.DataFrame(rows))
    assert lam == 1.0 and ci is None and "too thin" in diag["note"]


def test_calibrate_without_labels_leaves_tilt_neutral():
    """A frame with no `won` column (any older caller) must produce the old model."""
    att = pd.DataFrame({
        "ok": [True, False] * 10, "filled_shares": [10.0, 0.0] * 10,
        "target_shares": [10.0] * 20, "depth_band_shares": [20.0] * 20,
        "time_left": [120.0] * 20, "latency_s": [1.0] * 20,
        "quoted_ask": [0.60] * 20, "avg_price": [0.60, 0.0] * 10,
    })
    p = calibrate_from_frames(att, window=("2026-07-01", "2026-08-07"))
    assert p.adverse_tilt == 1.0 and p.adverse_tilt_ci is None


def test_params_written_before_the_recalibration_still_load(tmp_path):
    """Backwards compatibility: an old JSON has no adverse_tilt key."""
    from research.sim.fills_live import load_params, save_params
    p = _params(zero=0.4, tilt=1.42)
    fp = tmp_path / "old.json"
    save_params(p, fp)
    d = __import__("json").loads(fp.read_text())
    d.pop("adverse_tilt"), d.pop("adverse_tilt_ci")
    fp.write_text(__import__("json").dumps(d))
    assert load_params(fp).adverse_tilt == 1.0
