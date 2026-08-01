"""Tests for the live-calibrated fill model (research/sim/fills_live.py).

Pure arithmetic + literal params fixtures — no I/O, no mocking (repo convention).
The model is the drop-in cost layer for backtests: every edge judged under it is
judged under OUR OWN measured fill physics (252 live attempts, 2026-06-05..09).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import numpy as np
import pandas as pd
import pytest

from research.sim.fills_live import (
    GUARD_FLOOR_DROP,
    LiveFillParams,
    calibrate_from_frames,
    load_params,
    sample_latency,
    save_params,
    simulate_taker_entry,
)


def _params(zero=0.0, kappa=1.0, lat=(1.0,)):
    table = {d: {t: {"p": zero, "n": 10} for t in ("tl<=60", "tl61-180", "tl>180")}
             for d in ("d<0.5", "d0.5-1", "d1-2", "d>=2")}
    return LiveFillParams(version="test", calibration_window=("2026-06-05", "2026-06-09"),
                          zero_fill_prob=table, kappa=kappa,
                          latency_samples_s=list(lat), mean_slip_filled=0.0,
                          knife_rate_legacy=0.1, n_attempts=10, validation={})


# --- band restriction & floor ----------------------------------------------

def test_guarded_walk_restricts_to_band():
    # levels above max_ask must not be touched
    f = simulate_taker_entry([0.70, 0.74, 0.90], [5, 10, 99], 10.0,
                             entry_ask=0.70, max_ask=0.85)
    assert f.filled
    assert f.levels_used == 2                      # 0.90 outside the band
    # 5sh@0.70 + remaining $6.50 @0.74
    assert f.shares == pytest.approx(5 + 6.5 / 0.74)


def test_guarded_floor_abort_on_collapsed_book():
    # best ask 0.40 < entry 0.70 - 0.04 -> the favourite flipped; the deployed
    # executor guard skips, so the backtest must score it as NO FILL
    f = simulate_taker_entry([0.40, 0.45], [50, 50], 10.0,
                             entry_ask=0.70, max_ask=0.85)
    assert not f.filled and f.shares == 0.0
    assert f.unfilled_usd == 10.0


def test_legacy_mode_reproduces_knife_catch():
    f = simulate_taker_entry([0.40, 0.45], [50, 50], 10.0,
                             entry_ask=0.70, max_ask=0.85, mode="legacy")
    assert f.filled
    assert f.avg_price < 0.70 - GUARD_FLOOR_DROP   # the knife fill, for counterfactuals


def test_kappa_haircut_shrinks_effective_depth():
    p = _params(kappa=0.5)
    rng = np.random.default_rng(0)
    # one level: 10 shares displayed -> 5 effective at kappa=0.5; $10 wants 14.3sh
    f = simulate_taker_entry([0.70], [10], 10.0, entry_ask=0.70, max_ask=0.85,
                             params=p, rng=rng)
    assert f.shares == pytest.approx(5.0)
    assert f.unfilled_usd == pytest.approx(10.0 - 5.0 * 0.70)


# --- zero-fill hazard --------------------------------------------------------

def test_zero_fill_hazard_extremes():
    rng = np.random.default_rng(7)
    p1 = _params(zero=1.0)
    f1 = simulate_taker_entry([0.70], [100], 10.0, entry_ask=0.70, max_ask=0.85,
                              time_left=120, params=p1, rng=rng)
    assert not f1.filled and f1.unfilled_usd == 10.0
    p0 = _params(zero=0.0)
    f0 = simulate_taker_entry([0.70], [100], 10.0, entry_ask=0.70, max_ask=0.85,
                              time_left=120, params=p0, rng=rng)
    assert f0.filled


def test_seeded_rng_is_deterministic():
    p = _params(zero=0.5)
    a = [simulate_taker_entry([0.70], [100], 10.0, entry_ask=0.70, max_ask=0.85,
                              time_left=50, params=p,
                              rng=np.random.default_rng(42)).filled
         for _ in range(3)]
    assert a[0] == a[1] == a[2]


# --- latency ----------------------------------------------------------------

def test_latency_sampled_from_empirical_list():
    p = _params(lat=(0.5, 2.0, 9.0))
    rng = np.random.default_rng(1)
    draws = {sample_latency(rng, p) for _ in range(50)}
    assert draws.issubset({0.5, 2.0, 9.0}) and len(draws) > 1


# --- params persistence -------------------------------------------------------

def test_params_json_roundtrip(tmp_path):
    p = _params(zero=0.25, kappa=0.8, lat=(1.5, 3.0))
    path = tmp_path / "fill_model_live.json"
    save_params(p, path)
    q = load_params(path)
    assert q.kappa == p.kappa
    assert q.zero_fill_prob == p.zero_fill_prob
    assert q.latency_samples_s == p.latency_samples_s
    assert json.loads(path.read_text())["version"] == "test"


# --- calibration from attempt frames -----------------------------------------

def test_calibrate_from_frames_smoke():
    # 6 attempts: deep-book ones fill, thin-book ones don't
    att = pd.DataFrame({
        "ok": [True, True, False, True, False, False],
        "filled_shares": [7.0, 6.0, 0.0, 7.0, 0.0, 0.0],
        "target_shares": [7.0, 7.0, 7.0, 7.0, 7.0, 7.0],
        "depth_band_shares": [20.0, 15.0, 1.0, 18.0, 2.0, 0.5],
        "time_left": [120.0, 60.0, 120.0, 150.0, 40.0, 130.0],
        "latency_s": [1.0, 2.0, 5.0, 1.5, 6.0, 7.0],
        "quoted_ask": [0.70, 0.72, 0.70, 0.68, 0.74, 0.70],
        "avg_price": [0.70, 0.73, 0.0, 0.68, 0.0, 0.0],
    })
    p = calibrate_from_frames(att, window=("2026-06-05", "2026-06-09"))
    assert p.n_attempts == 6
    assert 0.0 < p.kappa <= 1.5
    deep = p.zero_fill_prob["d>=2"]["tl61-180"]["p"]
    thin = p.zero_fill_prob["d<0.5"]["tl61-180"]["p"]
    assert thin > deep                      # thin books fail more
    assert len(p.latency_samples_s) == 6
    assert "fill_rate_by_bin" in p.validation
