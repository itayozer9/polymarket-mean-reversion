"""Unit tests for DeterminismState mode="xb" (5m↔15m cross-book CAUSAL twin) +
the ws_collector co-terminal 5m field emission it depends on.

Synthetic, fast — the heavy research-parity pins (decision Jaccard vs the CAUSAL
XI4 frame, co5 feature parity from sampled live-CSV moments) live in
tests/research/test_xb_parity.py. Repo style: no mocks — real DetParams/
Portfolio/MarketDiscovery/OrderBook objects.

The load-bearing behavior: every xb5_* NaN FAILS CLOSED, and xb5_k5 is NaN until
discovery captures the co-terminal 5m strike — so the engine cannot reproduce
the acausal back-fill that retracted XI4's original headline (test_ledger §
"XI4 AMENDMENT (2026-06-12)").
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

NAN = float("nan")


def _row(sec, *, yes_bid, yes_ask, k15=100.0, y5b=NAN, y5a=NAN, y5b_sz=NAN,
         y5a_sz=NAN, k5=NAN, depth=1000.0, move_pct=0.01, ts=1_000_000,
         bare=False):
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
    a["start_price"][0] = k15
    if not bare:
        a["xb5_yes_bid"][0] = y5b
        a["xb5_yes_ask"][0] = y5a
        a["xb5_yes_bid_sz"][0] = y5b_sz
        a["xb5_yes_ask_sz"][0] = y5a_sz
        a["xb5_k5"][0] = k5
    return a[0]


def _up_row(sec=700, **kw):
    """Canonical UP violation: gap +5bps, 15m ask 0.42 + 0.03 <= co5 bid 0.50."""
    base = dict(yes_bid=0.40, yes_ask=0.42, k15=100.0, y5b=0.50, y5a=0.55,
                y5b_sz=50.0, y5a_sz=50.0, k5=100.05)
    base.update(kw)
    return _row(sec, **base)


def _dn_row(sec=700, **kw):
    """Canonical DOWN violation: gap -5bps, co5 ask 0.30 + 0.03 <= 15m bid 0.40."""
    base = dict(yes_bid=0.40, yes_ask=0.42, k15=100.0, y5b=0.28, y5a=0.30,
                y5b_sz=50.0, y5a_sz=50.0, k5=99.95)
    base.update(kw)
    return _row(sec, **base)


def _pf():
    return Portfolio(human=HumanParams(fixed_bet_usd=10.0, daily_trade_cap=None,
                                       concurrent_position_cap=50,
                                       post_loss_cooldown_sec=0), bankroll=1000.0)


def _params(**kw):
    base = dict(mode="xb", xb_premium=0.03, xb_gap_min_bps=2.0, xb_min_ref_usd=1.0,
                t_min_sec=5, t_max_sec=300, min_ask=0.0, max_ask=0.90)
    base.update(kw)
    return DetParams(**base)


def _run(params, row):
    s = DeterminismState("btc-updown-15m-1781049300", params, window_duration_sec=900)
    decisions = []
    s._obs = lambda d: decisions.append(d.get("decision"))
    s.on_tick(row, _pf(), np.random.default_rng(0))
    return s, (decisions[-1] if decisions else None)


# ── config validation (fail fast at boot) ────────────────────────────────────────

def test_xb_requires_premium_gap_and_skip_on_missing():
    with pytest.raises(ValueError, match="xb_premium"):
        DeterminismState("btc-x", DetParams(mode="xb", xb_gap_min_bps=2.0), 900)
    with pytest.raises(ValueError, match="xb_gap_min_bps"):
        DeterminismState("btc-x", DetParams(mode="xb", xb_premium=0.03), 900)
    with pytest.raises(ValueError, match="on_missing"):
        DeterminismState("btc-x", _params(xb_on_missing="allow"), 900)


# ── the rule fires (both sides, research conventions) ────────────────────────────

def test_xb_fires_up_on_violated_bound():
    s, dec = _run(_params(), _up_row())
    assert dec == "fired" and s.state == "HOLDING"
    assert s.pos["side"] == "UP"
    assert s.pos["entry"] == pytest.approx(0.42, abs=1e-6)
    assert s.pos["ctx"]["xb_gap_bps"] == pytest.approx(5.0, abs=0.01)
    assert s.pos["ctx"]["xb_edge"] == pytest.approx(0.50 - 0.42, abs=1e-6)
    assert s.pos["ctx"]["xb_s5"] == 100  # sec 700 -> 100s into the 5m window


def test_xb_fires_down_at_yes_equivalent_ask():
    # research convention: DOWN entry ask = 1 - yes_best_bid (NOT no_best_ask)
    s, dec = _run(_params(), _dn_row())
    assert dec == "fired" and s.pos["side"] == "DOWN"
    assert s.pos["entry"] == pytest.approx(1.0 - 0.40, abs=1e-6)
    assert s.pos["ctx"]["xb_gap_bps"] == pytest.approx(-5.0, abs=0.01)


# ── fail-closed on ANY missing input (THE causal gate) ───────────────────────────

def test_xb_fails_closed_on_missing_fields():
    # bare TICK_DTYPE row (e.g. replay CSV without xb fields) -> skip
    s, dec = _run(_params(), _up_row(bare=True, y5b=0, y5a=0, y5b_sz=0,
                                     y5a_sz=0, k5=0))
    assert s.state == "FLAT" and dec == "skipped_xb_missing"
    # k5 NaN = the 5m strike NOT yet captured by discovery (the causal gate)
    s2, dec2 = _run(_params(), _up_row(k5=NAN))
    assert s2.state == "FLAT" and dec2 == "skipped_xb_missing"
    # any quote field NaN (co5 market/book not visible)
    for f in ("y5b", "y5a", "y5b_sz", "y5a_sz"):
        s3, dec3 = _run(_params(), _up_row(**{f: NAN}))
        assert s3.state == "FLAT" and dec3 == "skipped_xb_missing", f
    # 15m strike missing
    s4, dec4 = _run(_params(), _up_row(k15=0.0))
    assert s4.state == "FLAT" and dec4 == "skipped_xb_missing"


def test_xb_sane_5m_book_filter():
    # collapsed 5m bid (< 0.01) -> not an opinion
    s, dec = _run(_params(), _dn_row(y5b=0.005))
    assert s.state == "FLAT" and dec == "skipped_xb_unhealthy_5m"
    # crossed co5 book
    s2, dec2 = _run(_params(), _up_row(y5a=0.49))  # ask <= bid 0.50
    assert s2.state == "FLAT" and dec2 == "skipped_xb_unhealthy_5m"
    # spread > 0.15
    s3, dec3 = _run(_params(), _up_row(y5b=0.50, y5a=0.70))
    assert s3.state == "FLAT" and dec3 == "skipped_xb_unhealthy_5m"
    # 5m near-1.0 (decided)
    s4, dec4 = _run(_params(), _up_row(y5b=0.991, y5a=0.995))
    assert s4.state == "FLAT" and dec4 == "skipped_xb_unhealthy_5m"


# ── gate boundaries: gap, premium, ref notional, ceiling, time band ──────────────

def test_xb_gap_gate():
    # |gap| 1bps < 2bps -> no signal either side
    s, _ = _run(_params(), _up_row(k5=100.01))
    assert s.state == "FLAT"
    # gap POSITIVE but the DOWN-shaped quote pattern -> direction must follow gap
    s2, _ = _run(_params(), _dn_row(k5=100.05))  # gap +5 but co5 cheap-side setup
    assert s2.state == "FLAT"
    # just past the threshold fires (research: >= on identically-computed float64;
    # k5=100.02 is NOT representable — it yields gap 1.9999... < 2.0 in BOTH the
    # engine and research monotonic_signal, so the boundary case itself is 'flat'
    # by shared arithmetic). Quotes must pass the premium gate (default yes_ask=0.48
    # fails it by design, see test_xb_premium_gate).
    s3, dec3 = _run(_params(), _up_row(k5=100.021, yes_bid=0.45, yes_ask=0.47))
    assert dec3 == "fired" and s3.pos["ctx"]["xb_gap_bps"] == pytest.approx(2.1, abs=0.01)


def test_xb_premium_gate():
    # 0.48 + 0.03 = 0.51 > co5 bid 0.50 -> bound not violated by enough
    s, _ = _run(_params(), _up_row(yes_ask=0.48))
    assert s.state == "FLAT"
    # 0.47 + 0.03 = 0.50 <= 0.50 fires (research: <=)
    s2, dec2 = _run(_params(), _up_row(yes_bid=0.45, yes_ask=0.47))
    assert dec2 == "fired"


def test_xb_ref_notional_floor():
    # $0.90 behind the referenced co5 bid (< $1) -> skip
    s, _ = _run(_params(), _up_row(y5b_sz=1.8))  # 0.50 * 1.8 = $0.90
    assert s.state == "FLAT"
    # $1.00 exactly passes (research: >=)
    s2, dec2 = _run(_params(), _up_row(y5b_sz=2.0))
    assert dec2 == "fired"
    # DOWN leg uses the co5 ASK notional
    s3, _ = _run(_params(), _dn_row(y5a_sz=3.0))  # 0.30 * 3.0 = $0.90
    assert s3.state == "FLAT"


def test_xb_ceiling_and_no_floor():
    # entry ask above the 0.90 ceiling -> skip
    s, _ = _run(_params(), _up_row(yes_bid=0.89, yes_ask=0.92, y5b=0.96, y5a=0.97))
    assert s.state == "FLAT"
    # CHEAP entries pass (no floor — the rule's profile is median ask ~0.44)
    s2, dec2 = _run(_params(), _up_row(yes_bid=0.02, yes_ask=0.04, y5b=0.10, y5a=0.12))
    assert dec2 == "fired" and s2.pos["entry"] == pytest.approx(0.04, abs=1e-6)


def test_xb_time_band_is_the_5m_overlap():
    # sec 599 -> time_left 301 > 300 -> outside the co-terminal overlap
    s, _ = _run(_params(), _up_row(sec=599))
    assert s.state == "FLAT"
    # sec 896 -> time_left 4 < 5 -> too late
    s2, _ = _run(_params(), _up_row(sec=896))
    assert s2.state == "FLAT"
    # band edges fire: sec 600 (tl 300) and sec 895 (tl 5)
    for sec in (600, 895):
        s3, dec3 = _run(_params(), _up_row(sec=sec))
        assert dec3 == "fired", sec


def test_xb_respects_15m_health_gate_and_depth():
    # crossed 15m book -> shared health gate
    s, dec = _run(_params(), _up_row(yes_bid=0.43, yes_ask=0.42))
    assert s.state == "FLAT" and dec == "skipped_unhealthy_book"
    # top-of-book too thin for $10 -> skipped_no_fill
    s2, dec2 = _run(_params(), _up_row(depth=20.0))  # 20 * 0.42 = $8.4
    assert s2.state == "FLAT" and dec2 == "skipped_no_fill"


# ── lifecycle: settle at true resolution, once per window ────────────────────────

def test_xb_settle_roundtrip_and_single_entry():
    pf = _pf()
    st = DeterminismState("btc-updown-15m-1781049300", _params(), 900)
    st.on_tick(_up_row(), pf, np.random.default_rng(0))
    assert st.state == "HOLDING" and pf.open_positions == 1
    st.on_tick(_up_row(sec=701), pf, np.random.default_rng(0))
    assert pf.open_positions == 1  # holding -> no second entry
    tr = st.settle(outcome_up=True, ts_ms=2_000_000, portfolio=pf)
    entry = float(np.float32(0.42))
    shares = 10.0 / entry
    fee_entry = shares * 0.07 * entry * (1 - entry)
    assert tr is not None and tr.side == "UP"
    assert tr.pnl == pytest.approx(shares - 10.0 - fee_entry, abs=1e-5)
    assert st.last_ctx["xb_gap_bps"] == pytest.approx(5.0, abs=0.01)
    assert st.last_ctx["won"] == 1
    # window done: no re-entry even on a fresh violation
    st.on_tick(_up_row(sec=702), pf, np.random.default_rng(0))
    assert st.state == "FLAT" and pf.open_positions == 0


# ── yaml registry: the paper block parses; live strategies untouched ─────────────

def test_yaml_block_parses_as_paper_and_live_untouched():
    from mean_reversion_live.engine.registry import load_strategies
    strats = load_strategies(REPO / "strategies.yaml", Path("/tmp/xb_yaml_test"))
    by_id = {s.id: s for s in strats}
    xb = by_id["xb_5m15m_causal_v1"]
    assert xb.live is False and xb.timeframe == "15m"
    assert xb.det_params.mode == "xb"
    assert xb.det_params.xb_premium == pytest.approx(0.03)
    assert xb.det_params.xb_gap_min_bps == pytest.approx(2.0)
    assert xb.det_params.xb_min_ref_usd == pytest.approx(1.0)
    assert (xb.det_params.t_min_sec, xb.det_params.t_max_sec) == (5, 300)
    assert (xb.det_params.min_ask, xb.det_params.max_ask) == (0.0, 0.90)
    assert xb.det_params.fixed_bet_usd == pytest.approx(10.0)
    assert xb.det_params.max_daily_loss_usd == pytest.approx(50.0)
    assert xb.det_params.daily_loss_mode == "hard_worstcase"
    assert xb.starting_capital_usd == pytest.approx(1000.0)
    # every strategy OUTSIDE the xb family is byte-identical config-wise: xb params
    # at no-op defaults (the xb5y mirror leg, added 2026-07-24, sets them by design)
    for s in strats:
        if s.det_params is None or s.det_params.mode in ("xb", "xb5y"):
            continue
        assert s.det_params.xb_premium is None and s.det_params.xb_gap_min_bps is None
    # the live set is EXACTLY the post-06-18-reckoning / 07-06-prune / 07-25-harvest roster —
    # an accidental arm/disarm of any strategy must fail this pin.
    # fav_disagree_hi_live added 2026-07-25: the ask 0.46-0.60 cohort of the fav_disagree edge
    # its sibling's 0.45 cap discards, $5 measurement rung, gate 2026-08-07.
    live_set = {s.id for s in strats if s.live}
    assert live_set == {"det_lwd_live", "fav_disagree_live", "fav_disagree_hi_live"}
    assert by_id["early_disagree_live"].live is False   # demoted 06-18, paper twin runs on
    assert "det_d12_dual_live" not in by_id             # killed 06-18, pruned roster


# ── ws_collector: co-terminal 5m field emission (the real production lookup) ─────

def _mk_market(slug, symbol, tf, ws, we, yes_tok, no_tok, start_price=0.0):
    from mean_reversion_live.clients.gamma import MarketInfo
    return MarketInfo(slug=slug, symbol=symbol, timeframe=tf, yes_token_id=yes_tok,
                      no_token_id=no_tok, window_start_ts=ws, window_end_ts=we,
                      start_price=start_price)


def _mk_collector(tmp_path, active: dict):
    """Real WsCollector wired to a real MarketDiscovery whose active set is
    `active` (no network: we never call run())."""
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
    asyncio.get_event_loop_policy().new_event_loop()
    rows = []

    async def go():
        await collector._emit_for_second(second_ts)
        while not q.empty():
            rows.append(q.get_nowait())
    asyncio.run(go())
    return {r["market_slug"]: r for r in rows}


def test_ws_collector_emits_co5_fields(tmp_path):
    ws15 = 1781049300  # % 900 == 0
    w5 = ws15 + 600
    m15 = _mk_market(f"btc-updown-15m-{ws15}", "btc", "15m", ws15, ws15 + 900,
                     "tok15y", "tok15n", start_price=100.0)
    m5 = _mk_market(f"btc-updown-5m-{w5}", "btc", "5m", w5, w5 + 300,
                    "tok5y", "tok5n", start_price=100.05)
    c, q = _mk_collector(tmp_path, {m15.slug: m15, m5.slug: m5})
    # DISTINCT values per book so a token mixup fails loudly
    _set_book(c, "tok15y", [(0.40, 100)], [(0.42, 100)])
    _set_book(c, "tok15n", [(0.58, 100)], [(0.60, 100)])
    _set_book(c, "tok5y", [(0.50, 50)], [(0.55, 40)])
    _set_book(c, "tok5n", [(0.45, 30)], [(0.50, 20)])

    rows = _emit_rows(c, q, w5 + 100)  # 15m sec 700, 5m sec 100
    r15 = rows[m15.slug]
    assert r15["xb5_yes_bid"] == pytest.approx(0.50)
    assert r15["xb5_yes_ask"] == pytest.approx(0.55)
    assert r15["xb5_yes_bid_sz"] == pytest.approx(50.0)
    assert r15["xb5_yes_ask_sz"] == pytest.approx(40.0)
    assert r15["xb5_k5"] == pytest.approx(100.05)
    # the co5 quote equals the 5m market's OWN emitted row at the same second
    r5 = rows[m5.slug]
    assert r5["yes_best_bid"] == r15["xb5_yes_bid"]
    assert r5["yes_best_ask"] == r15["xb5_yes_ask"]
    assert r5["start_price"] == r15["xb5_k5"]
    # 5m rows carry the xb5 keys as NaN (uniform row-dict convention, same as the
    # cl_* fields; the dtype comment's "15m rows only" means populated-only-for-15m
    # — the struct conversion maps missing/NaN identically, and the CSV writer's
    # fixed 23-col schema never sees engine-only keys)
    assert np.isnan(r5["xb5_yes_bid"]) and np.isnan(r5["xb5_k5"])
    # the engine struct then routes the violation into a fire
    arr = _row_dict_to_struct(r15)
    st = DeterminismState(m15.slug, _params(), 900)
    pf = _pf()
    st.on_tick(arr, pf, np.random.default_rng(0))
    assert st.state == "HOLDING" and st.pos["side"] == "UP"


def test_ws_collector_xb_nan_before_overlap_capture_or_co5(tmp_path):
    ws15 = 1781049300
    w5 = ws15 + 600

    def fresh(start_price_5m, with_co5=True, no_book_for=()):
        m15 = _mk_market(f"btc-updown-15m-{ws15}", "btc", "15m", ws15, ws15 + 900,
                         "tok15y", "tok15n", start_price=100.0)
        active = {m15.slug: m15}
        if with_co5:
            m5 = _mk_market(f"btc-updown-5m-{w5}", "btc", "5m", w5, w5 + 300,
                            "tok5y", "tok5n", start_price=start_price_5m)
            active[m5.slug] = m5
        c, q = _mk_collector(tmp_path, active)
        _set_book(c, "tok15y", [(0.40, 100)], [(0.42, 100)])
        _set_book(c, "tok15n", [(0.58, 100)], [(0.60, 100)])
        if with_co5:
            for tok, (b, a) in {"tok5y": ((0.50, 50), (0.55, 40)),
                                "tok5n": ((0.45, 30), (0.50, 20))}.items():
                if tok not in no_book_for:
                    _set_book(c, tok, [b], [a])
        return c, q

    # before the overlap (15m sec 599): no xb fields at all
    c, q = fresh(100.05)
    r = _emit_rows(c, q, ws15 + 599)[f"btc-updown-15m-{ws15}"]
    assert np.isnan(r["xb5_yes_bid"]) and np.isnan(r["xb5_k5"])

    # in-overlap but the 5m strike NOT yet captured: quotes flow, k5 NaN (CAUSAL gate)
    c, q = fresh(0.0)
    r = _emit_rows(c, q, w5 + 10)[f"btc-updown-15m-{ws15}"]
    assert r["xb5_yes_bid"] == pytest.approx(0.50)
    assert np.isnan(r["xb5_k5"])
    st = DeterminismState(f"btc-updown-15m-{ws15}", _params(), 900)
    decisions = []
    st._obs = lambda d: decisions.append(d.get("decision"))
    st.on_tick(_row_dict_to_struct(r), _pf(), np.random.default_rng(0))
    assert st.state == "FLAT" and decisions[-1] == "skipped_xb_missing"

    # co5 market not discovered: all NaN
    c, q = fresh(100.05, with_co5=False)
    r = _emit_rows(c, q, w5 + 10)[f"btc-updown-15m-{ws15}"]
    assert np.isnan(r["xb5_yes_bid"]) and np.isnan(r["xb5_k5"])

    # co5 NO book missing: all NaN (mirrors the co5 row's own emit guard)
    c, q = fresh(100.05, no_book_for=("tok5n",))
    r = _emit_rows(c, q, w5 + 10)[f"btc-updown-15m-{ws15}"]
    assert np.isnan(r["xb5_yes_bid"]) and np.isnan(r["xb5_k5"])
