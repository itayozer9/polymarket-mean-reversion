"""Test UTC window math."""
from mean_reversion_live.markets import windows as w


def test_15m_window_aligned_to_utc():
    # 2026-05-15 09:07:30 UTC = 1779175650 (approx)
    ts = 1779175650
    start = w.current_window_start("15m", now=ts)
    # 15m boundaries at 09:00, 09:15, 09:30, ... → 09:00 is 1779175200 (ish)
    assert start <= ts < start + 900
    assert start % 900 == 0


def test_5m_alignment():
    ts = 1779175650
    start = w.current_window_start("5m", now=ts)
    assert start <= ts < start + 300
    assert start % 300 == 0


def test_seconds_into_window():
    assert w.seconds_into_window(1779175200, now=1779175200) == 0
    assert w.seconds_into_window(1779175200, now=1779175210) == 10


def test_window_duration_unknown():
    import pytest
    with pytest.raises(ValueError):
        w.window_duration("13m")
