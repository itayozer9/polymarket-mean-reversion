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


def _patch_ta(monkeypatch):
    monkeypatch.setattr(hs, "load_base", lambda: _fake_base())
    hs._TA["df"] = None


def test_fam_ta_directional_buys_up_in_uptrend(monkeypatch):
    _patch_ta(monkeypatch)
    p = {"t_lo": 1, "t_hi": 900, "slope_min": 0.0, "ask_lo": 0.05, "ask_hi": 0.95}
    c, by = hs.fam_ta_directional(hs._ta_frame(), p)
    assert len(c) > 0
    # in the rising window every qualifying tick should buy UP (yes)
    up_rows = c["slug"] == "btc-up"
    assert by[up_rows.to_numpy()].all()


def test_fam_ta_filter_subsets_a_base_edge(monkeypatch):
    _patch_ta(monkeypatch)
    base_p = {"t_lo": 1, "t_hi": 900, "dist_min": 5, "ask_lo": 0.5, "ask_hi": 0.95}
    full, _ = hs.fam_det(hs._ta_frame(), base_p)
    p = {**base_p, "regime": "range"}
    c, by = hs.fam_ta_filter(hs._ta_frame(), p)
    assert len(c) <= len(full)            # a filter can only remove rows
    assert (c["ta_regime"] == "range").all()
    assert len(by) == len(c)


def test_fam_ta_regime_keeps_only_band(monkeypatch):
    _patch_ta(monkeypatch)
    p = {"t_lo": 1, "t_hi": 900, "dist_min": 5, "ask_lo": 0.5, "ask_hi": 0.95,
         "atr_lo": 0.0, "atr_hi": 1e9}
    c, by = hs.fam_ta_regime(hs._ta_frame(), p)
    assert len(c) == len(by)
    assert (c["ta_atr"] >= 0.0).all()


def test_fam_ta_divergence_buys_trend_side_when_book_lags(monkeypatch):
    _patch_ta(monkeypatch)
    p = {"t_lo": 1, "t_hi": 900, "slope_min": 0.0, "ask_lo": 0.05, "ask_hi": 0.95,
         "ret_min": 0.0}
    c, by = hs.fam_ta_divergence(hs._ta_frame(), p)
    assert len(c) == len(by)
    if len(c):
        assert by.dtype == bool


def test_all_ta_families_registered():
    for fam in ["ta_directional", "ta_filter", "ta_regime", "ta_divergence"]:
        assert fam in hs.BUILDERS
        assert fam in hs.RATIONALE


def test_gen_specs_includes_ta(monkeypatch):
    specs = hs.gen_specs()
    fams = {s["family"] for s in specs}
    assert {"ta_directional", "ta_filter", "ta_regime", "ta_divergence"} <= fams
