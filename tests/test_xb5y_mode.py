"""Unit tests for DeterminismState mode="xb5y" (Edge Hunt v3 g2bps-5y twin) +
the ws_collector co-terminal 15m field emission it depends on.

Mirror of tests/test_xb_mode.py for the mirror leg: the strategy runs ON the
co-terminal 5m market and buys OWN YES when the parent 15m book strictly
dominates it (own K5 below K15 by >= gap AND own yes_ask + premium <= 15m
yes_bid AND >= $ref behind the 15m bid). Spec numbers are the certified
survivor xh_5y_m02_g02_b600-900_r1_c90 (test_ledger "EDGE HUNT v3 VERDICTS").

Load-bearing behavior: every xb15_* NaN FAILS CLOSED (non-co-terminal 5m
windows never carry the fields), and the leg is ONE-SIDED — a positive gap
must never fire regardless of quotes.
"""
from __future__ import annotations
import asyncio
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from mean_reversion_live.adapters.arb_imports import TICK_DTYPE, Portfolio, HumanParams  # noqa: E402
from mean_reversion_live.engine.determinism_state import DetParams, DeterminismState  # noqa: E402
from mean_reversion_live.engine.paper_engine import _TICK_DTYPE_EXT, _row_dict_to_struct  # noqa: E402
from tests.armed_set import ARMED_LIVE_IDS  # noqa: E402

NAN = float("nan")


def _row(sec, *, yes_bid, yes_ask, k5=99.95, y15b=NAN, y15a=NAN, y15b_sz=NAN,
         k15=NAN, depth=1000.0, move_pct=0.01, ts=1_000_000, bare=False):
    """A 5m-market tick row (own book = the instrument; xb15_* = the parent)."""
    a = np.zeros(1, dtype=TICK_DTYPE if bare else _TICK_DTYPE_EXT)
    a["timestamp_ms"][0] = ts
    a["seconds_into_window"][0] = sec
    a["move_pct"][0] = move_pct
    a["yes_mid"][0] = (yes_bid + yes_ask) / 2.0
    a["yes_best_bid"][0] = yes_bid
    a["yes_best_ask"][0] = yes_ask
    a["spread_yes"][0] = max(0.001, yes_ask - yes_bid)
    a["yes_ask_depth"][0] = depth
    a["no_ask_depth"][0] = depth
    a["no_best_ask"][0] = 1.0 - yes_bid
    a["start_price"][0] = k5
    if not bare:
        a["xb15_yes_bid"][0] = y15b
        a["xb15_yes_ask"][0] = y15a
        a["xb15_yes_bid_sz"][0] = y15b_sz
        a["xb15_k15"][0] = k15
    return a[0]


def _viol_row(sec=100, **kw):
    """Canonical 5y violation: gap -5bps, own ask 0.42 + 0.02 <= 15m bid 0.50."""
    base = dict(yes_bid=0.40, yes_ask=0.42, k5=99.95, y15b=0.50, y15a=0.52,
                y15b_sz=50.0, k15=100.0)
    base.update(kw)
    return _row(sec, **base)


def _pf():
    return Portfolio(human=HumanParams(fixed_bet_usd=10.0, daily_trade_cap=None,
                                       concurrent_position_cap=50,
                                       post_loss_cooldown_sec=0), bankroll=1000.0)


def _params(**kw):
    base = dict(mode="xb5y", xb_premium=0.02, xb_gap_min_bps=2.0, xb_min_ref_usd=1.0,
                t_min_sec=5, t_max_sec=300, min_ask=0.03, max_ask=0.90)
    base.update(kw)
    return DetParams(**base)


def _run(params, row):
    s = DeterminismState("btc-updown-5m-1781049900", params, window_duration_sec=300)
    decisions = []
    s._obs = lambda d: decisions.append(d.get("decision"))
    s.on_tick(row, _pf(), np.random.default_rng(0))
    return s, (decisions[-1] if decisions else None)


# ── config validation (fail fast at boot) ────────────────────────────────────────

def test_xb5y_requires_premium_gap_and_skip_on_missing():
    with pytest.raises(ValueError, match="xb_premium"):
        DeterminismState("btc-x", DetParams(mode="xb5y", xb_gap_min_bps=2.0), 300)
    with pytest.raises(ValueError, match="xb_gap_min_bps"):
        DeterminismState("btc-x", DetParams(mode="xb5y", xb_premium=0.02), 300)
    with pytest.raises(ValueError, match="on_missing"):
        DeterminismState("btc-x", _params(xb_on_missing="allow"), 300)


# ── the rule fires (research 5y conventions) ─────────────────────────────────────

def test_xb5y_fires_on_violated_bound():
    s, dec = _run(_params(), _viol_row())
    assert dec == "fired" and s.state == "HOLDING"
    assert s.pos["side"] == "UP"                       # always OWN YES
    assert s.pos["entry"] == pytest.approx(0.42, abs=1e-6)
    assert s.pos["ctx"]["xb_gap_bps"] == pytest.approx(-5.0, abs=0.01)
    assert s.pos["ctx"]["xb_edge"] == pytest.approx(0.50 - 0.42, abs=1e-6)
    assert s.pos["ctx"]["xb15_ref_usd"] == pytest.approx(25.0, abs=0.1)
    assert s.pos["ctx"]["xb_s15"] == 700               # sec 100 -> 700s into the 15m


# ── fail-closed on ANY missing input ─────────────────────────────────────────────

def test_xb5y_fails_closed_on_missing_fields():
    # bare TICK_DTYPE row (replay CSV without xb15 fields) -> skip
    s, dec = _run(_params(), _viol_row(bare=True))
    assert s.state == "FLAT" and dec == "skipped_xb_missing"
    # k15 NaN = the 15m strike not yet captured by discovery
    s2, dec2 = _run(_params(), _viol_row(k15=NAN))
    assert s2.state == "FLAT" and dec2 == "skipped_xb_missing"
    # any reference-quote field NaN (co15 market/book not visible)
    for f in ("y15b", "y15a", "y15b_sz"):
        s3, dec3 = _run(_params(), _viol_row(**{f: NAN}))
        assert s3.state == "FLAT" and dec3 == "skipped_xb_missing", f
    # own 5m strike missing
    s4, dec4 = _run(_params(), _viol_row(k5=0.0))
    assert s4.state == "FLAT" and dec4 == "skipped_xb_missing"


def test_xb5y_sane_15m_reference_filter():
    # collapsed 15m bid (< 0.01) -> not an opinion
    s, dec = _run(_params(), _viol_row(y15b=0.005, y15a=0.02))
    assert s.state == "FLAT" and dec == "skipped_xb_unhealthy_15m"
    # crossed 15m book
    s2, dec2 = _run(_params(), _viol_row(y15a=0.49))   # ask <= bid 0.50
    assert s2.state == "FLAT" and dec2 == "skipped_xb_unhealthy_15m"
    # spread > 0.15
    s3, dec3 = _run(_params(), _viol_row(y15a=0.70))
    assert s3.state == "FLAT" and dec3 == "skipped_xb_unhealthy_15m"
    # 15m near-1.0 (decided)
    s4, dec4 = _run(_params(), _viol_row(y15b=0.991, y15a=0.995))
    assert s4.state == "FLAT" and dec4 == "skipped_xb_unhealthy_15m"


# ── gate boundaries: gap ONE-SIDED, premium, ref notional, ask band, time ────────

def test_xb5y_gap_gate_is_one_sided():
    # |gap| 1bps < 2bps -> no signal
    s, _ = _run(_params(), _viol_row(k5=99.99))
    assert s.state == "FLAT"
    # POSITIVE gap must NEVER fire, even with violation-shaped quotes: with
    # K5 ABOVE K15 own YES is the SUBSET event — the "cheap" ask is not dominated.
    s2, _ = _run(_params(), _viol_row(k5=100.05))
    assert s2.state == "FLAT"
    # just past the threshold fires (k5=99.979 -> gap -2.1bps)
    s3, dec3 = _run(_params(), _viol_row(k5=99.979))
    assert dec3 == "fired" and s3.pos["ctx"]["xb_gap_bps"] == pytest.approx(-2.1, abs=0.01)


def test_xb5y_premium_gate():
    # 0.42 + 0.02 = 0.44 > 15m bid 0.43 -> bound not violated by enough
    s, _ = _run(_params(), _viol_row(y15b=0.43, y15a=0.45))
    assert s.state == "FLAT"
    # 0.44 exactly fires (research: <=)
    s2, dec2 = _run(_params(), _viol_row(y15b=0.44, y15a=0.46))
    assert dec2 == "fired"


def test_xb5y_ref_notional_floor():
    # $0.90 behind the referenced 15m bid (< $1) -> skip
    s, _ = _run(_params(), _viol_row(y15b_sz=1.8))     # 0.50 * 1.8 = $0.90
    assert s.state == "FLAT"
    # $1.00 exactly passes (research: >=)
    s2, dec2 = _run(_params(), _viol_row(y15b_sz=2.0))
    assert dec2 == "fired"


def test_xb5y_ask_band():
    # entry ask above the 0.90 ceiling -> skip
    s, _ = _run(_params(), _viol_row(yes_bid=0.89, yes_ask=0.92, y15b=0.96, y15a=0.97))
    assert s.state == "FLAT"
    # entry ask below the 0.03 floor (research: ask > 0.03) -> skip
    s2, _ = _run(_params(), _viol_row(yes_bid=0.01, yes_ask=0.02, y15b=0.10, y15a=0.11))
    assert s2.state == "FLAT"
    # cheap-but-above-floor fires
    s3, dec3 = _run(_params(), _viol_row(yes_bid=0.03, yes_ask=0.05, y15b=0.12, y15a=0.13))
    assert dec3 == "fired" and s3.pos["entry"] == pytest.approx(0.05, abs=1e-6)


def test_xb5y_time_band_is_the_whole_5m_window():
    # sec 296 -> time_left 4 < 5 -> too late
    s, _ = _run(_params(), _viol_row(sec=296))
    assert s.state == "FLAT"
    # band edges fire: sec 0 (tl 300) and sec 295 (tl 5)
    for sec in (0, 295):
        s2, dec2 = _run(_params(), _viol_row(sec=sec))
        assert dec2 == "fired", sec


def test_xb5y_respects_own_health_gate_and_depth():
    # crossed OWN book -> shared health gate
    s, dec = _run(_params(), _viol_row(yes_bid=0.43, yes_ask=0.42))
    assert s.state == "FLAT" and dec == "skipped_unhealthy_book"
    # top-of-book too thin for $10 -> skipped_no_fill
    s2, dec2 = _run(_params(), _viol_row(depth=20.0))  # 20 * 0.42 = $8.4
    assert s2.state == "FLAT" and dec2 == "skipped_no_fill"


# ── lifecycle: settle at OWN (5m) resolution, once per window ────────────────────

def test_xb5y_settle_roundtrip_and_single_entry():
    pf = _pf()
    st = DeterminismState("btc-updown-5m-1781049900", _params(), 300)
    st.on_tick(_viol_row(), pf, np.random.default_rng(0))
    assert st.state == "HOLDING" and pf.open_positions == 1
    st.on_tick(_viol_row(sec=101), pf, np.random.default_rng(0))
    assert pf.open_positions == 1  # holding -> no second entry
    tr = st.settle(outcome_up=True, ts_ms=2_000_000, portfolio=pf)
    entry = float(np.float32(0.42))
    shares = 10.0 / entry
    fee_entry = shares * 0.07 * entry * (1 - entry)
    assert tr is not None and tr.side == "UP"
    assert tr.pnl == pytest.approx(shares - 10.0 - fee_entry, abs=1e-5)
    assert st.last_ctx["xb_gap_bps"] == pytest.approx(-5.0, abs=0.01)
    assert st.last_ctx["won"] == 1
    # window done: no re-entry even on a fresh violation
    st.on_tick(_viol_row(sec=102), pf, np.random.default_rng(0))
    assert st.state == "FLAT" and pf.open_positions == 0


# ── yaml registry: the paper block parses; live strategies untouched ─────────────

def test_yaml_block_parses_as_paper_and_live_untouched():
    from mean_reversion_live.engine.registry import load_strategies
    strats = load_strategies(REPO / "strategies.yaml", Path("/tmp/xb5y_yaml_test"))
    by_id = {s.id: s for s in strats}
    tw = by_id["xh5y_g2_v1"]
    assert tw.live is False and tw.timeframe == "5m"
    assert tw.det_params.mode == "xb5y"
    assert tw.det_params.xb_premium == pytest.approx(0.02)
    assert tw.det_params.xb_gap_min_bps == pytest.approx(2.0)
    assert tw.det_params.xb_min_ref_usd == pytest.approx(1.0)
    assert (tw.det_params.t_min_sec, tw.det_params.t_max_sec) == (5, 300)
    assert (tw.det_params.min_ask, tw.det_params.max_ask) == (0.03, 0.90)
    assert tw.det_params.max_daily_loss_usd == pytest.approx(50.0)
    assert tw.det_params.daily_loss_mode == "hard_worstcase"
    # the live set is untouched by THIS deploy (the pin is deliberately exact: a new live
    # strategy must be an explicit, reviewed edit here, never a silent side effect).
    # fav_disagree_hi_live added 2026-07-25 — the ask 0.46-0.60 cohort of the fav_disagree
    # edge, $5 measurement rung, gate 2026-08-07 (test_ledger "VOLUME-HARVEST ROUND").
    live_set = {s.id for s in strats if s.live}
    assert live_set == ARMED_LIVE_IDS


# ── ws_collector: co-terminal 15m field emission (the real production lookup) ────

def _mk_market(slug, symbol, tf, ws, we, yes_tok, no_tok, start_price=0.0):
    from mean_reversion_live.clients.gamma import MarketInfo
    return MarketInfo(slug=slug, symbol=symbol, timeframe=tf, yes_token_id=yes_tok,
                      no_token_id=no_tok, window_start_ts=ws, window_end_ts=we,
                      start_price=start_price)


def _mk_collector(tmp_path, active: dict):
    from mean_reversion_live.collectors.spot_collector import SpotPriceCache
    from mean_reversion_live.collectors.tick_writer import CrashSafeCsvGzAppender
    from mean_reversion_live.collectors.ws_collector import WsCollector
    from mean_reversion_live.markets.discovery import MarketDiscovery

    async def _noop_sub(_):
        return None

    async def _noop_close(*_):
        return None

    disc = MarketDiscovery(on_subscribe=_noop_sub, on_close=_noop_close)
    disc._active = dict(active)
    spot = SpotPriceCache(["btc"])
    spot.set("btc", 100.0)
    writer = CrashSafeCsvGzAppender(tmp_path / "live")
    q = asyncio.Queue(maxsize=100)
    return WsCollector(disc, spot, writer, out_queue=q), q


def _set_book(collector, token_id, bids, asks):
    collector._books[token_id].apply_book_snapshot({
        "bids": [{"price": str(p), "size": str(s)} for p, s in bids],
        "asks": [{"price": str(p), "size": str(s)} for p, s in asks],
    })


def _emit_rows(collector, q, second_ts):
    rows = []

    async def go():
        await collector._emit_for_second(second_ts)
        while not q.empty():
            rows.append(q.get_nowait())
    asyncio.run(go())
    return {r["market_slug"]: r for r in rows}


def test_ws_collector_emits_co15_fields(tmp_path):
    ws15 = 1781049300  # % 900 == 0
    w5 = ws15 + 600    # co-terminal: w5 % 900 == 600
    m15 = _mk_market(f"btc-updown-15m-{ws15}", "btc", "15m", ws15, ws15 + 900,
                     "tok15y", "tok15n", start_price=100.0)
    m5 = _mk_market(f"btc-updown-5m-{w5}", "btc", "5m", w5, w5 + 300,
                    "tok5y", "tok5n", start_price=99.95)
    c, q = _mk_collector(tmp_path, {m15.slug: m15, m5.slug: m5})
    _set_book(c, "tok15y", [(0.50, 50)], [(0.52, 40)])
    _set_book(c, "tok15n", [(0.46, 30)], [(0.48, 20)])
    _set_book(c, "tok5y", [(0.40, 100)], [(0.42, 100)])
    _set_book(c, "tok5n", [(0.56, 100)], [(0.58, 100)])

    rows = _emit_rows(c, q, w5 + 100)
    r5 = rows[m5.slug]
    assert r5["xb15_yes_bid"] == pytest.approx(0.50)
    assert r5["xb15_yes_ask"] == pytest.approx(0.52)
    assert r5["xb15_yes_bid_sz"] == pytest.approx(50.0)
    assert r5["xb15_k15"] == pytest.approx(100.0)
    # the co15 quote equals the 15m market's OWN emitted row at the same second
    r15 = rows[m15.slug]
    assert r15["yes_best_bid"] == r5["xb15_yes_bid"]
    assert r15["start_price"] == r5["xb15_k15"]
    # 15m rows carry the xb15 keys as NaN (uniform row-dict convention)
    assert np.isnan(r15["xb15_yes_bid"]) and np.isnan(r15["xb15_k15"])
    # the engine struct then routes the violation into a fire on the 5M market
    arr = _row_dict_to_struct(r5)
    st = DeterminismState(m5.slug, _params(), 300)
    pf = _pf()
    st.on_tick(arr, pf, np.random.default_rng(0))
    assert st.state == "HOLDING" and st.pos["side"] == "UP"
    assert st.pos["entry"] == pytest.approx(0.42, abs=1e-4)


def test_ws_collector_xb15_nan_when_not_coterminal_or_uncaptured(tmp_path):
    ws15 = 1781049300

    # a NON-co-terminal 5m window (w5 % 900 == 0) never carries xb15 fields
    w5_first = ws15 + 900  # first 5m window of the NEXT 15m parent
    m5a = _mk_market(f"btc-updown-5m-{w5_first}", "btc", "5m", w5_first,
                     w5_first + 300, "tok5y", "tok5n", start_price=99.95)
    c, q = _mk_collector(tmp_path, {m5a.slug: m5a})
    _set_book(c, "tok5y", [(0.40, 100)], [(0.42, 100)])
    _set_book(c, "tok5n", [(0.56, 100)], [(0.58, 100)])
    r = _emit_rows(c, q, w5_first + 100)[m5a.slug]
    assert np.isnan(r["xb15_yes_bid"]) and np.isnan(r["xb15_k15"])

    # co-terminal but the 15m strike NOT yet captured: quotes flow, k15 NaN
    w5 = ws15 + 600
    m15 = _mk_market(f"btc-updown-15m-{ws15}", "btc", "15m", ws15, ws15 + 900,
                     "tok15y", "tok15n", start_price=0.0)
    m5 = _mk_market(f"btc-updown-5m-{w5}", "btc", "5m", w5, w5 + 300,
                    "tok5y2", "tok5n2", start_price=99.95)
    c2, q2 = _mk_collector(tmp_path, {m15.slug: m15, m5.slug: m5})
    _set_book(c2, "tok15y", [(0.50, 50)], [(0.52, 40)])
    _set_book(c2, "tok15n", [(0.46, 30)], [(0.48, 20)])
    _set_book(c2, "tok5y2", [(0.40, 100)], [(0.42, 100)])
    _set_book(c2, "tok5n2", [(0.56, 100)], [(0.58, 100)])
    r2 = _emit_rows(c2, q2, w5 + 100)[m5.slug]
    assert r2["xb15_yes_bid"] == pytest.approx(0.50)   # quotes flow
    assert np.isnan(r2["xb15_k15"])                    # strike gated (fails closed)
    # and the engine fails closed on that row
    st = DeterminismState(m5.slug, _params(), 300)
    decisions = []
    st._obs = lambda d: decisions.append(d.get("decision"))
    st.on_tick(_row_dict_to_struct(r2), _pf(), np.random.default_rng(0))
    assert st.state == "FLAT" and decisions[-1] == "skipped_xb_missing"
