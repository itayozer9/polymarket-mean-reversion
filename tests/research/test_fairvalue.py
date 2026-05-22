import numpy as np
from research.lib.fairvalue import bachelier_p_up

def test_at_strike_is_half():
    # move_pct = 0 -> exactly a coin flip regardless of vol/time
    assert abs(bachelier_p_up(0.0, 0.05, 400.0) - 0.5) < 1e-9

def test_above_strike_more_than_half():
    assert bachelier_p_up(0.5, 0.05, 400.0) > 0.5

def test_below_strike_less_than_half():
    assert bachelier_p_up(-0.5, 0.05, 400.0) < 0.5

def test_decided_when_far_with_no_time():
    # 2% above strike, tiny remaining sigma -> almost certainly Up
    assert bachelier_p_up(2.0, 0.05, 1.0) > 0.99

def test_vectorized():
    mp = np.array([0.0, 0.5, -0.5])
    out = bachelier_p_up(mp, np.full(3, 0.05), np.full(3, 400.0))
    assert out.shape == (3,)
    assert abs(out[0] - 0.5) < 1e-9
