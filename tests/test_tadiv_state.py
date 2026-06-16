# tests/test_tadiv_state.py
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from mean_reversion_live.adapters.arb_imports import TICK_DTYPE, Portfolio, HumanParams  # noqa: E402
from mean_reversion_live.engine.determinism_state import DetParams, DeterminismState  # noqa: E402


def test_tadiv_params_default_off():
    p = DetParams()
    assert p.mode == "consistent"
    assert getattr(p, "tadiv_ret_min_bps", "MISSING") is None


def test_tadiv_requires_ret_min():
    # mode=tadiv_approx with no ret_min must fail fast at construction
    p = DetParams(mode="tadiv_approx")
    with pytest.raises(Exception):
        DeterminismState("btc-x", p, window_duration_sec=900)


# ── mode="tadiv_approx" entry-path tests ─────────────────────────────────────
# Same tick shape + conventions as tests/test_determinism_state.py.

def _row(sec, *, move_pct, yes_mid, yes_ask, no_ask, ts=1_000_000, depth=1000.0):
    a = np.zeros(1, dtype=TICK_DTYPE)
    a["timestamp_ms"][0] = ts
    a["seconds_into_window"][0] = sec
    a["move_pct"][0] = move_pct
    a["yes_mid"][0] = yes_mid
    a["yes_best_ask"][0] = yes_ask
    a["no_best_ask"][0] = no_ask
    # Healthy two-sided YES book (the strategy's book-health guard requires it).
    a["yes_best_bid"][0] = max(0.01, yes_ask - 0.02)
    a["spread_yes"][0] = 0.02
    a["yes_ask_depth"][0] = depth
    a["no_ask_depth"][0] = depth
    return a[0]


def _pf():
    return Portfolio(human=HumanParams(fixed_bet_usd=10.0, daily_trade_cap=None,
                                       concurrent_position_cap=50,
                                       post_loss_cooldown_sec=0), bankroll=1000.0)


def _tadiv_params():
    # vel_bps(30) floor 5 bps; time band [60,300] -> sec in [600,840]; ask band [0.30,0.55].
    return DetParams(mode="tadiv_approx", tadiv_ret_min_bps=5.0,
                     t_min_sec=60, t_max_sec=300, min_ask=0.30, max_ask=0.55)


# vel_bps(win) at now_sec=840 reads the most recent buffered tick with s <= 840-win:
#   vel_bps(30): most recent s <= 810  -> the sec=805 tick
#   vel_bps(10): most recent s <= 830  -> the sec=825 tick
# Priming ticks (805, 825) are pushed with an OUT-OF-BAND ask (0.85) so they
# never fire; only the in-band sec=840 tick is a real entry candidate.

def test_tadiv_fires_up_on_up_move():
    # (a) 30s move UP +10bps (>=5) and 10s move UP +5bps -> FIRE buying UP (yes).
    s, pf, rng = DeterminismState("btc-updown-15m-1", _tadiv_params(), 900), _pf(), np.random.default_rng(0)
    s.on_tick(_row(805, move_pct=0.10, yes_mid=0.50, yes_ask=0.85, no_ask=0.85), pf, rng)
    s.on_tick(_row(825, move_pct=0.15, yes_mid=0.50, yes_ask=0.85, no_ask=0.85), pf, rng)
    # vel30 = (0.20-0.10)*100 = 10bps; vel10 = (0.20-0.15)*100 = 5bps; UP ask 0.45 in band.
    s.on_tick(_row(840, move_pct=0.20, yes_mid=0.50, yes_ask=0.45, no_ask=0.55), pf, rng)
    assert s.state == "HOLDING" and pf.open_positions == 1
    assert s.pos["side"] == "UP"


def test_tadiv_fires_down_on_down_move():
    # (b) symmetric DOWN: 30s move -10bps, 10s move -5bps -> FIRE buying DOWN (no).
    s, pf, rng = DeterminismState("btc-updown-15m-1", _tadiv_params(), 900), _pf(), np.random.default_rng(0)
    s.on_tick(_row(805, move_pct=-0.10, yes_mid=0.50, yes_ask=0.85, no_ask=0.85), pf, rng)
    s.on_tick(_row(825, move_pct=-0.15, yes_mid=0.50, yes_ask=0.85, no_ask=0.85), pf, rng)
    # vel30 = (-0.20+0.10)*100 = -10bps; vel10 = (-0.20+0.15)*100 = -5bps; NO ask 0.45 in band.
    s.on_tick(_row(840, move_pct=-0.20, yes_mid=0.50, yes_ask=0.55, no_ask=0.45), pf, rng)
    assert s.state == "HOLDING" and pf.open_positions == 1
    assert s.pos["side"] == "DOWN"


def test_tadiv_no_fire_when_move_too_small():
    # (c) |vel_bps(30)| < 5 -> does NOT fire.
    s, pf, rng = DeterminismState("btc-updown-15m-1", _tadiv_params(), 900), _pf(), np.random.default_rng(0)
    s.on_tick(_row(805, move_pct=0.10, yes_mid=0.50, yes_ask=0.85, no_ask=0.85), pf, rng)
    s.on_tick(_row(825, move_pct=0.11, yes_mid=0.50, yes_ask=0.85, no_ask=0.85), pf, rng)
    # vel30 = (0.12-0.10)*100 = 2bps < 5 -> skip.
    s.on_tick(_row(840, move_pct=0.12, yes_mid=0.50, yes_ask=0.45, no_ask=0.55), pf, rng)
    assert s.state == "FLAT" and pf.open_positions == 0


def test_tadiv_no_fire_when_slope_disagrees():
    # (d) vel_bps(10) sign disagrees with vel_bps(30) sign -> slope-proxy gate skip.
    s, pf, rng = DeterminismState("btc-updown-15m-1", _tadiv_params(), 900), _pf(), np.random.default_rng(0)
    s.on_tick(_row(805, move_pct=0.10, yes_mid=0.50, yes_ask=0.85, no_ask=0.85), pf, rng)
    # 10s window peak then pulls back: 30s move still UP, 10s move DOWN.
    s.on_tick(_row(825, move_pct=0.25, yes_mid=0.50, yes_ask=0.85, no_ask=0.85), pf, rng)
    # vel30 = (0.20-0.10)*100 = +10bps; vel10 = (0.20-0.25)*100 = -5bps -> disagree -> skip.
    s.on_tick(_row(840, move_pct=0.20, yes_mid=0.50, yes_ask=0.45, no_ask=0.55), pf, rng)
    assert s.state == "FLAT" and pf.open_positions == 0


def test_tadiv_consistent_mode_unaffected_by_same_ticks():
    # (e) mode="consistent" fed the SAME ticks behaves as the legacy path: the
    # canonical det_lwd setup (fav=YES, move>0, ask in legacy band) still enters,
    # proving the new dispatch is a clean separate branch that doesn't interfere.
    s, pf, rng = DeterminismState("btc-updown-15m-1", DetParams(), 900), _pf(), np.random.default_rng(0)
    # legacy default band: t_max_sec=60 -> sec=850 (time_left=50<=60), ask in [0.50,0.90].
    s.on_tick(_row(850, move_pct=0.10, yes_mid=0.85, yes_ask=0.85, no_ask=0.16), pf, rng)
    assert s.state == "HOLDING" and pf.open_positions == 1


# ── registry parsing of mode="tadiv_approx" params ───────────────────────────

def test_registry_parses_tadiv_approx_params(tmp_path):
    # A determinism strategy with mode=tadiv_approx + tadiv_ret_min_bps must load
    # with the field populated on the resulting DetParams (same mechanism as the
    # psettle_* / xb_* keys the registry already maps by hand).
    import yaml as _yaml
    from mean_reversion_live.engine.registry import load_strategies

    cfg = [{
        "id": "tadiv_test_v1",
        "name": "tadiv registry parse test",
        "enabled": True,
        "type": "determinism",
        "starting_capital_usd": 1000.0,
        "timeframe": "15m",
        "determinism": {
            "mode": "tadiv_approx",
            "tadiv_ret_min_bps": 5.0,
            "t_min_sec": 60,
            "t_max_sec": 300,
            "min_ask": 0.30,
            "max_ask": 0.55,
        },
        "sim_config": {
            "entry": {"side": "BOTH", "entry_price_min": 0.30, "entry_price_max": 0.55,
                      "drop_magnitude_pct": 0.0, "drop_window_sec": 30, "min_time_left_sec": 0,
                      "proximity_max_pct": 100.0, "min_seconds_into_window": 0},
            "exit": {"profit_target_pct": 0.0, "stop_loss_pct": None, "max_hold_sec": 900,
                     "trailing_stop_pct": None},
            "filter": {"min_book_depth_usd": 0.0, "max_spread": 1.0, "book_imbalance_min": None,
                       "vol_regime": "ALL", "time_of_day": "ALL", "multi_tier_entry": 1,
                       "correlated_signal_filter": False},
            "human": {"reaction_delay_min_sec": 0.0, "reaction_delay_max_sec": 0.0,
                      "signal_skip_prob": 0.0, "daily_trade_cap": None, "post_loss_cooldown_sec": 0,
                      "concurrent_position_cap": 50, "fixed_bet_usd": 10.0},
            "fill": {"fee_rate": 0.07, "reject_prob": 0.0, "use_next_tick_for_fill": True,
                     "realistic_fill_model": True},
        },
    }]
    yaml_path = tmp_path / "strategies.yaml"
    yaml_path.write_text(_yaml.safe_dump(cfg))

    handles = load_strategies(yaml_path, tmp_path)
    assert len(handles) == 1
    dp = handles[0].det_params
    assert dp is not None
    assert dp.mode == "tadiv_approx"
    assert dp.tadiv_ret_min_bps == 5.0
