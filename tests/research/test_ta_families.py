# tests/research/test_ta_families.py
import numpy as np
import pandas as pd
import pytest
import research.analysis.hypothesis_sweep as hs


def _fake_base():
    # two windows, each 120s; one trending up (fav up), one flat
    rows = []
    for slug, base, drift in [("btc-up", 100.0, 0.5), ("btc-flat", 100.0, 0.0)]:
        for sec in range(120):
            spot = base + drift * sec
            rows.append({
                "slug": slug, "symbol": "btc", "date": "2026-06-14",
                "split": "future", "window_start_ts": 1, "seconds_into_window": sec,
                "time_left_sec": 900 - sec * 7, "cb_spot": spot,
                "yes_mid": 0.6, "yes_best_ask": 0.62, "yes_best_bid": 0.58,
                "dist_strike_bps": 20.0, "abs_dist_bps": 20.0, "consistent": True,
                "fav_ask": 0.62, "realized_vol": 0.5,
            })
    return pd.DataFrame(rows)


def test_ta_frame_has_ta_columns(monkeypatch):
    monkeypatch.setattr(hs, "load_base", lambda: _fake_base())
    hs._TA["df"] = None
    f = hs._ta_frame()
    for col in ["ta_ema_slope", "ta_rsi", "ta_regime", "ta_z_vwap"]:
        assert col in f.columns
    assert len(f) == len(_fake_base())
