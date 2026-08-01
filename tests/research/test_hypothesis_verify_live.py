"""Smoke tests for the live fill-model path in hypothesis_verify (tiny synthetic
ladders, no repo data): entry_ask from the SIGNAL-second ladder, family-ask_hi /
entry+0.07 ceiling rule, guarded floor abort, zero-fill hazard, DOWN-side ladder
convention."""
import numpy as np
import pandas as pd
import pytest

from research.analysis.hypothesis_verify import (
    live_entry_fill, spec_ask_hi, _side_ladder)
from research.sim.fills_live import LiveFillParams, sample_latency

TLS = ("tl<=60", "tl61-180", "tl>180")
DBINS = ("d<0.5", "d0.5-1", "d1-2", "d>=2")


def _params(p_zero=0.0, kappa=1.0, lat=(3.0,)):
    table = {d: {t: {"p": p_zero, "n": 5} for t in TLS} for d in DBINS}
    return LiveFillParams(
        version="test", calibration_window=("x", "y"), zero_fill_prob=table,
        kappa=kappa, latency_samples_s=list(lat), mean_slip_filled=0.0,
        knife_rate_legacy=0.0, n_attempts=5, validation={})


def _row(asks=(), bids=()):
    d = {}
    for i in range(1, 11):
        pa, sa = asks[i - 1] if i - 1 < len(asks) else (np.nan, np.nan)
        pb, sb = bids[i - 1] if i - 1 < len(bids) else (np.nan, np.nan)
        d[f"ask_px_{i}"], d[f"ask_sz_{i}"] = pa, sa
        d[f"bid_px_{i}"], d[f"bid_sz_{i}"] = pb, sb
    return pd.Series(d)


def test_spec_ask_hi():
    assert spec_ask_hi({"ask_lo": 0.5, "ask_hi": 0.78}) == 0.78
    assert spec_ask_hi({"ud_ask_max": 0.85}) == 0.85
    assert spec_ask_hi({"dist_min": 5}) is None
    assert spec_ask_hi({"ud_ask_max": None}) is None


def test_side_ladder_down_is_one_minus_bid():
    lr = _row(asks=[(0.60, 10)], bids=[(0.40, 25)])
    px, sz = _side_ladder(lr, buy_yes=False)
    assert px[0] == pytest.approx(0.60)   # DOWN ask = 1 - yes bid
    assert sz[0] == 25
    pxu, szu = _side_ladder(lr, buy_yes=True)
    assert pxu[0] == pytest.approx(0.60) and szu[0] == 10


def test_basic_fill_family_ceiling():
    sig = _row(asks=[(0.60, 30)])
    fill = _row(asks=[(0.61, 50), (0.65, 50)])
    f, entry_ask, ceiling = live_entry_fill(
        sig, fill, True, 5.0, ask_hi=0.78, time_left=120.0,
        rng=np.random.default_rng(0), params=_params())
    assert entry_ask == pytest.approx(0.60)
    assert ceiling == pytest.approx(0.78)
    assert f.filled and f.avg_price == pytest.approx(0.61)
    assert f.unfilled_usd == pytest.approx(0.0)


def test_no_family_hi_uses_entry_plus_7c_capped():
    sig = _row(asks=[(0.60, 30)])
    fill = _row(asks=[(0.70, 100)])      # above 0.60+0.07 -> out of band
    f, entry_ask, ceiling = live_entry_fill(
        sig, fill, True, 5.0, ask_hi=None, time_left=120.0,
        rng=np.random.default_rng(0), params=_params())
    assert ceiling == pytest.approx(0.67)
    assert not f.filled
    # cap at 0.92
    sig_hi = _row(asks=[(0.90, 30)])
    _, _, ceil2 = live_entry_fill(
        sig_hi, sig_hi, True, 5.0, ask_hi=None, time_left=120.0,
        rng=np.random.default_rng(0), params=_params())
    assert ceil2 == pytest.approx(0.92)


def test_guard_floor_abort():
    """best ask collapses below entry_ask - 0.04 -> the favourite flipped ->
    the executor aborts (no knife-catch fill)."""
    sig = _row(asks=[(0.60, 30)])
    fill = _row(asks=[(0.50, 500)])
    f, _, _ = live_entry_fill(
        sig, fill, True, 5.0, ask_hi=0.78, time_left=120.0,
        rng=np.random.default_rng(0), params=_params())
    assert not f.filled


def test_zero_fill_hazard():
    sig = _row(asks=[(0.60, 30)])
    fill = _row(asks=[(0.61, 50)])
    f, _, _ = live_entry_fill(
        sig, fill, True, 5.0, ask_hi=0.78, time_left=120.0,
        rng=np.random.default_rng(0), params=_params(p_zero=1.0))
    assert not f.filled


def test_no_inband_signal_ask_returns_none():
    sig = _row(asks=[(0.85, 30)])        # above the family's 0.78 band
    fill = _row(asks=[(0.61, 50)])
    f, entry_ask, ceiling = live_entry_fill(
        sig, fill, True, 5.0, ask_hi=0.78, time_left=120.0,
        rng=np.random.default_rng(0), params=_params())
    assert f is None and entry_ask is None and ceiling is None


def test_latency_sampling_deterministic():
    p = _params(lat=(2.0, 7.5, 14.0))
    a = [sample_latency(np.random.default_rng(42), p) for _ in range(3)]
    b = [sample_latency(np.random.default_rng(42), p) for _ in range(3)]
    assert a == b and all(x in (2.0, 7.5, 14.0) for x in a)
