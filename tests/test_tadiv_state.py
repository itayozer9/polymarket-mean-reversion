# tests/test_tadiv_state.py
import pytest
from mean_reversion_live.engine.determinism_state import DetParams, DeterminismState


def test_tadiv_params_default_off():
    p = DetParams()
    assert p.mode == "consistent"
    assert getattr(p, "tadiv_ret_min_bps", "MISSING") is None


def test_tadiv_requires_ret_min():
    # mode=tadiv_approx with no ret_min must fail fast at construction
    p = DetParams(mode="tadiv_approx")
    with pytest.raises(Exception):
        DeterminismState("btc-x", p, window_duration_sec=900)
