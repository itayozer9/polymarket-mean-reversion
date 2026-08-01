"""Unit tests for DeterminismState mode="psettle" (settlement-print-model twins).

Synthetic, fast — the heavy research-parity pins (logistic 1e-9, feature deltas,
one-day decision agreement) live in tests/research/test_print_model_parity.py.
Repo style: no mocks — real DetParams/Portfolio and a hand-built PrintModel whose
probability is computable by hand.
"""
from __future__ import annotations
import math
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from mean_reversion_live.adapters.arb_imports import TICK_DTYPE, Portfolio, HumanParams  # noqa: E402
from mean_reversion_live.engine.determinism_state import DetParams, DeterminismState  # noqa: E402
from mean_reversion_live.engine.print_model import PrintModel, load_print_model, p_settle_up  # noqa: E402

# Tick struct with the engine's extended Chainlink fields (paper_engine._TICK_DTYPE_EXT).
_EXT = np.dtype(TICK_DTYPE.descr + [
    ("cl_dist_bps", "f8"), ("cl_cb_basis_bps", "f8"), ("cl_oracle_age_s", "f8")])


def _const_model(p_up: float) -> PrintModel:
    """A PrintModel that returns exactly `p_up` for any (finite) feature row."""
    return PrintModel(mu=np.zeros(9), sd=np.ones(9), coef=np.zeros(9),
                      intercept=math.log(p_up / (1.0 - p_up)))


def _row(sec, *, move_pct, yes_mid, yes_bid, yes_ask, depth=1000.0, ts=1_000_000,
         cl_dist=5.0, cl_basis=1.0, cl_age=10.0, with_cl=True):
    a = np.zeros(1, dtype=_EXT if with_cl else TICK_DTYPE)
    a["timestamp_ms"][0] = ts
    a["seconds_into_window"][0] = sec
    a["move_pct"][0] = move_pct
    a["yes_mid"][0] = yes_mid
    a["yes_best_bid"][0] = yes_bid
    a["yes_best_ask"][0] = yes_ask
    a["spread_yes"][0] = max(0.001, yes_ask - yes_bid)
    a["yes_ask_depth"][0] = depth
    a["no_ask_depth"][0] = depth
    a["no_best_ask"][0] = 1.0 - yes_bid
    if with_cl:
        a["cl_dist_bps"][0] = cl_dist
        a["cl_cb_basis_bps"][0] = cl_basis
        a["cl_oracle_age_s"][0] = cl_age
    return a[0]


def _pf():
    return Portfolio(human=HumanParams(fixed_bet_usd=10.0, daily_trade_cap=None,
                                       concurrent_position_cap=50,
                                       post_loss_cooldown_sec=0), bankroll=1000.0)


def _ud_params(model, **kw):
    base = dict(mode="psettle", psettle_side="underdog", psettle_margin=0.15,
                t_min_sec=60, t_max_sec=360, min_ask=0.50, max_ask=0.78,
                psettle_model=model)
    base.update(kw)
    return DetParams(**base)


def _fade_params(model, **kw):
    base = dict(mode="psettle", psettle_side="fade", psettle_fav_ask_min=0.75,
                psettle_p_fav_max=0.60, t_min_sec=60, t_max_sec=360,
                min_ask=0.05, max_ask=0.35, psettle_model=model)
    base.update(kw)
    return DetParams(**base)


def _run(params, row):
    s = DeterminismState("btc-updown-15m-1", params, window_duration_sec=900)
    decisions = []
    s._obs = lambda d: decisions.append(d.get("decision"))
    s.on_tick(row, _pf(), np.random.default_rng(0))
    return s, (decisions[-1] if decisions else None)


# ── config validation (fail fast at boot) ────────────────────────────────────────

def test_psettle_requires_model_and_valid_side():
    with pytest.raises(ValueError, match="psettle_model"):
        DeterminismState("btc-x", DetParams(mode="psettle", psettle_side="underdog",
                                            psettle_margin=0.15), 900)
    with pytest.raises(ValueError, match="psettle_side"):
        DeterminismState("btc-x", DetParams(mode="psettle", psettle_side="nope",
                                            psettle_model=_const_model(0.8)), 900)
    with pytest.raises(ValueError, match="psettle_margin"):
        DeterminismState("btc-x", DetParams(mode="psettle", psettle_side="underdog",
                                            psettle_model=_const_model(0.8)), 900)
    with pytest.raises(ValueError, match="p_fav_max"):
        DeterminismState("btc-x", DetParams(mode="psettle", psettle_side="fade",
                                            psettle_model=_const_model(0.8)), 900)
    with pytest.raises(ValueError, match="on_missing"):
        DeterminismState("btc-x", _ud_params(_const_model(0.8),
                                             psettle_on_missing="allow"), 900)


# ── underdog (sweep psettle_2246 shape) ──────────────────────────────────────────

def test_underdog_fires_when_model_overrules_the_book():
    # fav NO (ymid 0.45) -> underdog is YES at ask 0.55; model P(up)=0.80
    # -> edge 0.25 >= 0.15, ask in [0.50, 0.78] -> buy UP.
    s, dec = _run(_ud_params(_const_model(0.80)),
                  _row(700, move_pct=0.01, yes_mid=0.45, yes_bid=0.53, yes_ask=0.55))
    assert dec == "fired" and s.state == "HOLDING"
    assert s.pos["side"] == "UP" and s.pos["entry"] == pytest.approx(0.55, abs=1e-6)

    # fav YES (ymid 0.55): underdog is NO; its YES-equivalent ask = 1 - yes_bid.
    # P(up)=0.20 -> P(no)=0.80, no_ask = 1-0.45 = 0.55 -> edge 0.25 -> buy DOWN at 0.55.
    s2, dec2 = _run(_ud_params(_const_model(0.20)),
                    _row(700, move_pct=0.01, yes_mid=0.55, yes_bid=0.45, yes_ask=0.62))
    assert dec2 == "fired" and s2.pos["side"] == "DOWN"
    assert s2.pos["entry"] == pytest.approx(0.55, abs=1e-6)


def test_underdog_skips_when_edge_below_margin_or_ask_outside_band():
    # edge = 0.80 - 0.70 = 0.10 < 0.15 -> no trade
    s, _ = _run(_ud_params(_const_model(0.80)),
                _row(700, move_pct=0.01, yes_mid=0.45, yes_bid=0.68, yes_ask=0.70))
    assert s.state == "FLAT"
    # ask 0.45 below min_ask 0.50 -> no trade (even with a huge edge)
    s2, _ = _run(_ud_params(_const_model(0.90)),
                 _row(700, move_pct=0.01, yes_mid=0.45, yes_bid=0.43, yes_ask=0.45))
    assert s2.state == "FLAT"
    # ask 0.80 above max_ask 0.78 -> no trade
    s3, _ = _run(_ud_params(_const_model(0.99)),
                 _row(700, move_pct=0.01, yes_mid=0.45, yes_bid=0.76, yes_ask=0.80))
    assert s3.state == "FLAT"


def test_underdog_respects_time_band_and_health_gate():
    p = _ud_params(_const_model(0.90))
    # tl = 900-450 = 450 > 360 -> outside band
    s, _ = _run(p, _row(450, move_pct=0.01, yes_mid=0.45, yes_bid=0.53, yes_ask=0.55))
    assert s.state == "FLAT"
    # tl = 30 < 60 -> outside band
    s2, _ = _run(p, _row(870, move_pct=0.01, yes_mid=0.45, yes_bid=0.53, yes_ask=0.55))
    assert s2.state == "FLAT"
    # crossed book (bid >= ask) -> unhealthy -> skip
    s3, dec3 = _run(p, _row(700, move_pct=0.01, yes_mid=0.45, yes_bid=0.56, yes_ask=0.55))
    assert s3.state == "FLAT" and dec3 == "skipped_unhealthy_book"


def test_psettle_fails_closed_on_missing_chainlink_features():
    # a bare TICK_DTYPE row has no cl_* fields -> features NaN -> model NaN -> skip
    s, dec = _run(_ud_params(_const_model(0.90)),
                  _row(700, move_pct=0.01, yes_mid=0.45, yes_bid=0.53, yes_ask=0.55,
                       with_cl=False))
    assert s.state == "FLAT" and dec == "skipped_psettle_missing"
    # NaN basis alone (e.g. Coinbase missing at the tick) must also fail closed
    s2, dec2 = _run(_ud_params(_const_model(0.90)),
                    _row(700, move_pct=0.01, yes_mid=0.45, yes_bid=0.53, yes_ask=0.55,
                         cl_basis=float("nan")))
    assert s2.state == "FLAT" and dec2 == "skipped_psettle_missing"


def test_underdog_depth_gate_and_settle_roundtrip():
    # depth 10 shares * 0.55 = $5.5 < $10 -> no fill
    s, dec = _run(_ud_params(_const_model(0.90)),
                  _row(700, move_pct=0.01, yes_mid=0.45, yes_bid=0.53, yes_ask=0.55,
                       depth=10.0))
    assert s.state == "FLAT" and dec == "skipped_no_fill"
    # full entry -> engine settles at true resolution; UP wins on outcome_up
    pf = _pf()
    st = DeterminismState("btc-updown-15m-1", _ud_params(_const_model(0.90)), 900)
    st.on_tick(_row(700, move_pct=0.01, yes_mid=0.45, yes_bid=0.53, yes_ask=0.55),
               pf, np.random.default_rng(0))
    assert st.state == "HOLDING"
    tr = st.settle(outcome_up=True, ts_ms=2_000_000, portfolio=pf)
    shares = 10.0 / 0.55
    fee_entry = shares * 0.07 * 0.55 * 0.45
    assert tr is not None and tr.side == "UP"
    assert tr.pnl == pytest.approx(shares - 10.0 - fee_entry, abs=1e-6)
    assert st.last_ctx["p_settle_up"] == pytest.approx(0.90, abs=1e-6)
    assert st.last_ctx["psettle_side"] == "underdog"


def test_psettle_enters_once_per_window():
    pf = _pf()
    st = DeterminismState("btc-updown-15m-1", _ud_params(_const_model(0.90)), 900)
    row = _row(700, move_pct=0.01, yes_mid=0.45, yes_bid=0.53, yes_ask=0.55)
    st.on_tick(row, pf, np.random.default_rng(0))
    assert st.state == "HOLDING" and pf.open_positions == 1
    st.on_tick(_row(701, move_pct=0.01, yes_mid=0.45, yes_bid=0.53, yes_ask=0.55),
               pf, np.random.default_rng(0))
    assert pf.open_positions == 1  # holding -> no second entry


# ── fade (OP4 shape) ─────────────────────────────────────────────────────────────

def test_fade_fires_on_rich_favourite_the_model_doubts():
    # fav YES at ask 0.80 (>=0.75); model P(up)=0.55 <= 0.60 -> buy the cheap NO
    # side at 1-yes_bid = 0.22 (in [0.05, 0.35]).
    s, dec = _run(_fade_params(_const_model(0.55)),
                  _row(700, move_pct=0.02, yes_mid=0.79, yes_bid=0.78, yes_ask=0.80))
    assert dec == "fired" and s.pos["side"] == "DOWN"
    assert s.pos["entry"] == pytest.approx(0.22, abs=1e-6)
    # mirror: fav NO rich (yes_mid 0.21; no YES-equiv ask = 1-0.20 = 0.80); P(up)=0.45
    # -> p_fav = 0.55 <= 0.60 -> buy cheap YES at its ask 0.23.
    s2, dec2 = _run(_fade_params(_const_model(0.45)),
                    _row(700, move_pct=-0.02, yes_mid=0.21, yes_bid=0.20, yes_ask=0.23))
    assert dec2 == "fired" and s2.pos["side"] == "UP"
    assert s2.pos["entry"] == pytest.approx(0.23, abs=1e-6)


def test_fade_skips_when_model_agrees_or_fav_not_rich_or_cheap_outside_band():
    # model backs the favourite (p_fav 0.80 > 0.60) -> no fade
    s, _ = _run(_fade_params(_const_model(0.80)),
                _row(700, move_pct=0.02, yes_mid=0.79, yes_bid=0.78, yes_ask=0.80))
    assert s.state == "FLAT"
    # favourite not rich enough (fav ask 0.70 < 0.75)
    s2, _ = _run(_fade_params(_const_model(0.55)),
                 _row(700, move_pct=0.02, yes_mid=0.69, yes_bid=0.68, yes_ask=0.70))
    assert s2.state == "FLAT"
    # cheap side too expensive (1-yes_bid = 0.40 > 0.35)
    s3, _ = _run(_fade_params(_const_model(0.55)),
                 _row(700, move_pct=0.02, yes_mid=0.78, yes_bid=0.60, yes_ask=0.95))
    assert s3.state == "FLAT"
    # cheap side below min_ask 0.05 (1-yes_bid = 0.03)
    s4, _ = _run(_fade_params(_const_model(0.55)),
                 _row(700, move_pct=0.02, yes_mid=0.96, yes_bid=0.97, yes_ask=0.99))
    assert s4.state == "FLAT"


# ── frozen model evaluator sanity (hand-computed; real-artifact pins live in
#    tests/research/test_print_model_parity.py) ──────────────────────────────────

def test_p_settle_up_hand_row_single_coef():
    # only d_cl active: coef 1 on feature idx 2 (d_cl = clip(cl/10)), mu 0 sd 1.
    m = PrintModel(mu=np.zeros(9), sd=np.ones(9),
                   coef=np.array([0, 0, 1.0, 0, 0, 0, 0, 0, 0], dtype="f8"),
                   intercept=0.0)
    p = p_settle_up(m, cl_dist_bps=10.0, cb_dist_bps=0.0, cl_cb_basis_bps=0.0,
                    cl_oracle_age_s=0.0, time_left_sec=100.0, realized_vol=0.01,
                    symbol="btc")
    assert p == pytest.approx(1.0 / (1.0 + math.exp(-1.0)), abs=1e-12)
    # clip: cl=1e6 bps -> d_cl clipped at 10
    p_hi = p_settle_up(m, cl_dist_bps=1e6, cb_dist_bps=0.0, cl_cb_basis_bps=0.0,
                       cl_oracle_age_s=0.0, time_left_sec=100.0, realized_vol=0.01,
                       symbol="btc")
    assert p_hi == pytest.approx(1.0 / (1.0 + math.exp(-10.0)), abs=1e-12)
    # any non-finite feature -> NaN (fail closed)
    assert math.isnan(p_settle_up(m, cl_dist_bps=float("nan"), cb_dist_bps=0.0,
                                  cl_cb_basis_bps=0.0, cl_oracle_age_s=0.0,
                                  time_left_sec=100.0, realized_vol=0.01, symbol="btc"))


def test_load_print_model_real_artifact_and_yaml_registry():
    art = REPO / "data" / "research" / "oracle_print_model.json"
    if not art.exists():
        pytest.skip("oracle_print_model.json not present")
    m = load_print_model(art)
    assert len(m.mu) == len(m.sd) == len(m.coef) == 9
    assert m.iso_adopted is False  # the adopted artifact rejected its isotonic
    # the two new yaml blocks parse as PAPER (live False) psettle strategies
    from mean_reversion_live.engine.registry import load_strategies
    strats = load_strategies(REPO / "strategies.yaml", Path("/tmp/psettle_yaml_test"))
    by_id = {s.id: s for s in strats}
    # Both psettle blocks are now PRUNED, their yaml preserved with enabled:false so the
    # loader must skip them: oracle_fade_v1 2026-07-06 (killed by rule on official labels),
    # psettle_ud_v1 2026-07-25 (-$316/14d official, -$1.43/fill). The psettle ENGINE is
    # still covered by the rest of this module; this assertion pins the deployed ROSTER.
    assert "oracle_fade_v1" not in by_id
    assert "psettle_ud_v1" not in by_id
    # the live strategies are untouched: still live, still non-psettle
    # (det_d12_dual_live killed 2026-06-18 → absent from the loaded registry)
    assert "det_d12_dual_live" not in by_id
    assert by_id["det_lwd_live"].live is True
    assert by_id["det_lwd_live"].det_params.mode != "psettle"
    assert by_id["fav_disagree_live"].live is True
