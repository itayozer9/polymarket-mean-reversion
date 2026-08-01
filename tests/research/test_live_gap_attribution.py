"""Tests for the live-vs-paper gap attribution (research/analysis/live_gap_attribution.py).

Real tmp-file fixtures, no mocking. Every $ expectation is hand-computed from the
binary-payoff identity  pnl = shares*(won - price) - fee  (paper) / no fee (live).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import pytest

from research.analysis.live_gap_attribution import (
    KNIFE_X_DEFAULT,
    build_attribution,
    summarize,
)


def _w(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _paper_rows(sid, slug, side, entry_price, shares, fee, pnl, won, basis,
                entry_ts_ms=1_000_000):
    """One paper trade as (trades.jsonl row, trades_detailed.jsonl row)."""
    base = {
        "strategy_id": sid, "slug": slug, "side": side,
        "entry_ts_ms": entry_ts_ms, "exit_ts_ms": entry_ts_ms + 60_000,
        "entry_price": entry_price, "exit_price": 1.0 if won else 0.0,
        "shares": shares, "bet_usd": round(entry_price * shares, 6),
        "fee_total": fee, "pnl": pnl, "exit_reason": "resolution",
        "seconds_held": 60,
    }
    detailed = dict(base)
    detailed.update({"won": won, "settle_basis": basis, "symbol": slug.split("-")[0],
                     "entry_ask": entry_price})
    return base, detailed


@pytest.fixture()
def world(tmp_path):
    """A small universe covering every cohort. All windows are 15m slugs.

    ROW A (det_lwd_live, btc-1000): matched normal WIN, identical price/size.
        paper p=0.80 N=10 F=0.112 pnl=+1.888 | live fill at quote, settle win pnl=+2.0
        -> gap = +0.112, entirely fee component.
    ROW B (det_lwd_live, sol-2000): KNIFE-CATCH (quoted 0.66, avg 0.36), both lose.
        paper N=7.5757575758 F=0.119 pnl=-5.119 | live 13.948sh $5.0212 pnl=-5.0212
        -> price=+4.1844, size=-4.20568, fee=+0.119, residual ~0.
    ROW C (det_lwd_live, eth-3000): intent, NO fill record at all (no_attempt).
        paper pnl=+1.888 -> missed = -1.888.
    ROW D (det_lwd_live, xrp-4000): attempted, ok=false 'book dry in-band'.
        paper p=0.70 N=5 F=0.0735 pnl=+1.4265 -> missed = -1.4265.
    ROW E (det_lwd_live, btc-5000): MISSED + paper mis-settled on coinbase.
        paper recorded won=1 (basis=coinbase) pnl=+1.888, but det_d12_dual_live's
        settlement on the same slug (side UP, won=false) proves outcome_up=0.
        -> e_cmp = 10*(0-1) = -10, missed = +8.112 (avoided a true loss).
    ROW E' (det_d12_dual_live, btc-5000): the dual fill that carries the truth.
        paper p=0.80 N=5 F=0.056 won=0 pnl=-4.056 | live settle lose pnl=-4.0
        -> gap = +0.056 fee only.
    ROW F (det_lwd_live, eth-6000): LEGACY fill without strategy_id field.
        paper p=0.50 N=10 F=0.175 pnl=+4.825 | live at quote win pnl=+5.0
        -> attributed to det_lwd_live, gap = +0.175 fee only.
    ROW G: junk fill (token_id=UPTOK, sid=S) with no intent -> dropped entirely.
    ROW H (det_lwd_live, sol-8000): fill ok but no settlement yet -> pending,
        excluded from $ components.
    """
    live = tmp_path / "live"
    jsonl = tmp_path / "jsonl"

    intents = []
    fills = []
    settles = []
    paper = {"det_lwd_live": [], "det_d12_dual_live": []}

    def intent(sid, slug, side, entry_ask, bet, ts_ms=1_000_000):
        intents.append({"ts_ms": ts_ms, "strategy_id": sid, "slug": slug,
                        "symbol": slug.split("-")[0], "side": side,
                        "entry_ask": entry_ask, "time_left": 120.0,
                        "bet_usd": bet, "bankroll_usd": 100.0,
                        "max_daily_loss_usd": 25.0, "max_ask": 0.88})

    # ROW A
    intent("det_lwd_live", "btc-updown-15m-1000", "UP", 0.80, 8.0)
    paper["det_lwd_live"].append(_paper_rows(
        "det_lwd_live", "btc-updown-15m-1000", "UP", 0.80, 10.0, 0.112, 1.888, 1, "chainlink"))
    fills.append({"ts": 1000.0, "strategy_id": "det_lwd_live", "slug": "btc-updown-15m-1000",
                  "side": "UP", "token_id": "t", "quoted_ask": 0.80, "max_ask": 0.88,
                  "target_shares": 10.0, "bet_usd": 8.0, "mode": "live", "dry_run": False,
                  "ok": True, "filled_shares": 10.0, "usdc_paid": 8.0, "avg_price": 0.80,
                  "slippage_vs_quote": 0.0, "fill_ratio": 1.0, "latency_ms": 900})
    settles.append({"ts": 2000.0, "strategy_id": "det_lwd_live", "slug": "btc-updown-15m-1000",
                    "side": "UP", "shares": 10.0, "usdc": 8.0, "outcome": "Up",
                    "won": True, "pnl": 2.0, "realized_total_after": 0.0, "window_end": 1900})

    # ROW B — knife catch
    intent("det_lwd_live", "sol-updown-15m-2000", "UP", 0.66, 5.0)
    n_b = 5.0 / 0.66
    fee_b = 0.07 * 0.66 * 0.34 * n_b
    paper["det_lwd_live"].append(_paper_rows(
        "det_lwd_live", "sol-updown-15m-2000", "UP", 0.66, n_b, fee_b,
        n_b * (0 - 0.66) - fee_b, 0, "chainlink"))
    fills.append({"ts": 1000.0, "strategy_id": "det_lwd_live", "slug": "sol-updown-15m-2000",
                  "side": "UP", "token_id": "t", "quoted_ask": 0.66, "limit_price": 0.71,
                  "target_shares": 7.58, "bet_usd": 5.0, "mode": "live", "dry_run": False,
                  "ok": True, "filled_shares": 13.948, "usdc_paid": 5.0212, "avg_price": 0.36,
                  "slippage_vs_quote": -0.3, "fill_ratio": 1.84, "latency_ms": 1340})
    settles.append({"ts": 2000.0, "strategy_id": "det_lwd_live", "slug": "sol-updown-15m-2000",
                    "side": "UP", "shares": 13.948, "usdc": 5.0212, "outcome": "Down",
                    "won": False, "pnl": -5.0212, "realized_total_after": 0.0, "window_end": 1900})

    # ROW C — no_attempt
    intent("det_lwd_live", "eth-updown-15m-3000", "UP", 0.80, 8.0)
    paper["det_lwd_live"].append(_paper_rows(
        "det_lwd_live", "eth-updown-15m-3000", "UP", 0.80, 10.0, 0.112, 1.888, 1, "chainlink"))

    # ROW D — attempted_zero
    intent("det_lwd_live", "xrp-updown-15m-4000", "DOWN", 0.70, 3.5)
    paper["det_lwd_live"].append(_paper_rows(
        "det_lwd_live", "xrp-updown-15m-4000", "DOWN", 0.70, 5.0, 0.0735, 1.4265, 1, "chainlink"))
    fills.append({"ts": 1000.0, "strategy_id": "det_lwd_live", "slug": "xrp-updown-15m-4000",
                  "side": "DOWN", "token_id": "t", "quoted_ask": 0.70, "max_ask": 0.88,
                  "target_shares": 5.0, "bet_usd": 3.5, "mode": "live", "dry_run": False,
                  "ok": False, "filled_shares": 0.0, "usdc_paid": 0.0,
                  "note": "book dry in-band", "latency_ms": 5000})

    # ROW E — missed + coinbase mis-settle (truth from dual's settlement)
    intent("det_lwd_live", "btc-updown-15m-5000", "UP", 0.80, 8.0)
    paper["det_lwd_live"].append(_paper_rows(
        "det_lwd_live", "btc-updown-15m-5000", "UP", 0.80, 10.0, 0.112, 1.888, 1, "coinbase"))
    # ROW E' — dual on the same slug (its fill carries the true outcome)
    intent("det_d12_dual_live", "btc-updown-15m-5000", "UP", 0.80, 4.0)
    paper["det_d12_dual_live"].append(_paper_rows(
        "det_d12_dual_live", "btc-updown-15m-5000", "UP", 0.80, 5.0, 0.056,
        5.0 * (0 - 0.80) - 0.056, 0, "chainlink"))
    fills.append({"ts": 1000.0, "strategy_id": "det_d12_dual_live", "slug": "btc-updown-15m-5000",
                  "side": "UP", "token_id": "t", "quoted_ask": 0.80, "max_ask": 0.78,
                  "target_shares": 5.0, "bet_usd": 4.0, "mode": "live", "dry_run": False,
                  "ok": True, "filled_shares": 5.0, "usdc_paid": 4.0, "avg_price": 0.80,
                  "slippage_vs_quote": 0.0, "fill_ratio": 1.0, "latency_ms": 800})
    settles.append({"ts": 2000.0, "strategy_id": "det_d12_dual_live", "slug": "btc-updown-15m-5000",
                    "side": "UP", "shares": 5.0, "usdc": 4.0, "outcome": "Down",
                    "won": False, "pnl": -4.0, "realized_total_after": 0.0, "window_end": 1900})

    # ROW F — legacy fill without strategy_id
    intent("det_lwd_live", "eth-updown-15m-6000", "UP", 0.50, 5.0)
    paper["det_lwd_live"].append(_paper_rows(
        "det_lwd_live", "eth-updown-15m-6000", "UP", 0.50, 10.0, 0.175, 4.825, 1, "chainlink"))
    fills.append({"ts": 1000.0, "slug": "eth-updown-15m-6000",  # NOTE: no strategy_id
                  "side": "UP", "token_id": "t", "quoted_ask": 0.50, "limit_price": 0.55,
                  "target_shares": 10.0, "bet_usd": 5.0, "mode": "live", "dry_run": False,
                  "ok": True, "filled_shares": 10.0, "usdc_paid": 5.0, "avg_price": 0.50,
                  "slippage_vs_quote": 0.0, "fill_ratio": 1.0, "latency_ms": 700})
    settles.append({"ts": 2000.0, "strategy_id": "det_lwd_live", "slug": "eth-updown-15m-6000",
                    "side": "UP", "shares": 10.0, "usdc": 5.0, "outcome": "Up",
                    "won": True, "pnl": 5.0, "realized_total_after": 0.0, "window_end": 1900})

    # ROW G — junk (test pollution), no intent
    fills.append({"ts": 1000.0, "strategy_id": "S", "slug": "btc-updown-15m-7000",
                  "side": "UP", "token_id": "UPTOK", "quoted_ask": 0.60,
                  "target_shares": 5.0, "bet_usd": 3.0, "mode": "live", "dry_run": False,
                  "ok": True, "filled_shares": 5.0, "usdc_paid": 3.0, "avg_price": 0.60})

    # ROW H — pending (no settlement)
    intent("det_lwd_live", "sol-updown-15m-8000", "UP", 0.75, 5.0)
    paper["det_lwd_live"].append(_paper_rows(
        "det_lwd_live", "sol-updown-15m-8000", "UP", 0.75, 6.6667, 0.0875, 1.5792, 1, "chainlink"))
    fills.append({"ts": 1000.0, "strategy_id": "det_lwd_live", "slug": "sol-updown-15m-8000",
                  "side": "UP", "token_id": "t", "quoted_ask": 0.75, "max_ask": 0.88,
                  "target_shares": 6.67, "bet_usd": 5.0, "mode": "live", "dry_run": False,
                  "ok": True, "filled_shares": 6.6667, "usdc_paid": 5.0, "avg_price": 0.75,
                  "slippage_vs_quote": 0.0, "fill_ratio": 1.0, "latency_ms": 600})

    _w(live / "intents.jsonl", intents)
    _w(live / "fills.jsonl", fills)
    # split settlements across the main file and a .bak (loader must glob both)
    _w(live / "settlements.jsonl", settles[:-1])
    _w(live / "settlements.jsonl.bak.20260608T000000Z", settles[-1:])
    for sid, rows in paper.items():
        _w(jsonl / sid / "trades.jsonl", [r[0] for r in rows])
        _w(jsonl / sid / "trades_detailed.jsonl", [r[1] for r in rows])

    df = build_attribution(live_dir=live, jsonl_root=jsonl, knife_x=KNIFE_X_DEFAULT)
    return df


def _row(df, slug, sid="det_lwd_live"):
    sel = df[(df["slug"] == slug) & (df["strategy_id"] == sid)]
    assert len(sel) == 1, f"expected exactly one row for {sid}/{slug}, got {len(sel)}"
    return sel.iloc[0]


def test_matched_normal_win_is_fee_only_gap(world):
    r = _row(world, "btc-updown-15m-1000")
    assert r["cohort"] == "filled_normal"
    assert r["cmp_fee"] == pytest.approx(0.112, abs=1e-9)
    assert r["cmp_price"] == pytest.approx(0.0, abs=1e-9)
    assert r["cmp_size"] == pytest.approx(0.0, abs=1e-9)
    assert r["cmp_settle_basis"] == pytest.approx(0.0, abs=1e-9)
    assert r["row_gap"] == pytest.approx(0.112, abs=1e-9)
    assert abs(r["residual"]) < 1e-6


def test_knife_catch_decomposition(world):
    r = _row(world, "sol-updown-15m-2000")
    assert r["cohort"] == "filled_knife"
    # implied live price comes from exact settlement cash (5.0212/13.948), not the
    # rounded avg_price field: cmp_price = N_l*p_p - U_l
    assert r["cmp_price"] == pytest.approx(13.948 * 0.66 - 5.0212, abs=1e-9)
    assert r["cmp_size"] == pytest.approx((13.948 - 5.0 / 0.66) * (0 - 0.66), abs=1e-6)
    assert abs(r["residual"]) < 1e-6
    # row gap = live - paper = -5.0212 - (-5.119) within fp noise
    assert r["row_gap"] == pytest.approx(-5.0212 - (5.0 / 0.66 * (0 - 0.66)
                                                    - 0.07 * 0.66 * 0.34 * (5.0 / 0.66)), abs=1e-6)


def test_missed_no_attempt_counterfactual(world):
    r = _row(world, "eth-updown-15m-3000")
    assert r["cohort"] == "no_attempt"
    assert r["cmp_missed"] == pytest.approx(-1.888, abs=1e-9)
    assert r["row_gap"] == pytest.approx(-1.888, abs=1e-9)


def test_attempted_zero_fill_counterfactual(world):
    r = _row(world, "xrp-updown-15m-4000")
    assert r["cohort"] == "attempted_zero"
    assert r["cmp_missed"] == pytest.approx(-1.4265, abs=1e-9)


def test_settle_basis_mismatch_isolated(world):
    r = _row(world, "btc-updown-15m-5000")
    assert r["cohort"] == "no_attempt"
    # paper recorded a +1.888 win on coinbase basis, but the dual settlement on the
    # same slug proves the window truly went Down -> e = 10*(0-1) = -10,
    # missed = -(pnl_true) = +8.112 (not trading avoided a real loss).
    assert r["cmp_settle_basis"] == pytest.approx(-10.0, abs=1e-9)
    assert r["cmp_missed"] == pytest.approx(8.112, abs=1e-9)
    assert r["row_gap"] == pytest.approx(-1.888, abs=1e-9)


def test_legacy_fill_attributed_to_det_lwd_live(world):
    r = _row(world, "eth-updown-15m-6000")
    assert r["cohort"] == "filled_normal"
    assert r["row_gap"] == pytest.approx(0.175, abs=1e-9)


def test_junk_rows_dropped(world):
    assert not (world["slug"] == "btc-updown-15m-7000").any()


def test_pending_excluded_from_dollars(world):
    r = _row(world, "sol-updown-15m-8000")
    assert r["cohort"] == "pending"
    for c in ("cmp_price", "cmp_size", "cmp_fee", "cmp_missed", "cmp_settle_basis"):
        assert r[c] == pytest.approx(0.0, abs=1e-12)


def test_reconciliation_total(world):
    s = summarize(world)
    comp_total = sum(s["total"]["components"].values())
    assert comp_total == pytest.approx(s["total"]["gap"], abs=1e-6)
    assert s["total"]["residual"] == pytest.approx(0.0, abs=1e-6)
    # per-cohort live-EV view exists (the guard-justification table)
    assert s["cohorts"]["filled_knife"]["live_pnl"] == pytest.approx(-5.0212, abs=1e-6)
    assert s["cohorts"]["filled_knife"]["n"] == 1
    # the dual fee-only row is included in the totals
    assert s["per_strategy"]["det_d12_dual_live"]["gap"] == pytest.approx(0.056, abs=1e-6)


def test_geoblocked_no_attempts_reclassified(tmp_path):
    """no_attempt intents within the window of a 403 geoblock log event become
    cohort 'geoblocked' (excluded from missed-EV — the VPN, not the executor)."""
    live = tmp_path / "live"
    jsonl = tmp_path / "jsonl"
    ts_ms = 1_900_000_000_000  # 2030-03-17 17:46:40 UTC
    _w(live / "intents.jsonl", [{
        "ts_ms": ts_ms, "strategy_id": "det_lwd_live", "slug": "btc-updown-15m-9000",
        "symbol": "btc", "side": "UP", "entry_ask": 0.80, "time_left": 120.0,
        "bet_usd": 5.0, "bankroll_usd": 100.0, "max_daily_loss_usd": 25.0,
        "max_ask": 0.88}])
    _w(live / "fills.jsonl", [])
    _w(live / "settlements.jsonl", [])
    _w(jsonl / "det_lwd_live" / "trades.jsonl", [_paper_rows(
        "det_lwd_live", "btc-updown-15m-9000", "UP", 0.80, 10.0, 0.112, 1.888, 1,
        "chainlink", entry_ts_ms=ts_ms)[0]])
    _w(jsonl / "det_lwd_live" / "trades_detailed.jsonl", [_paper_rows(
        "det_lwd_live", "btc-updown-15m-9000", "UP", 0.80, 10.0, 0.112, 1.888, 1,
        "chainlink", entry_ts_ms=ts_ms)[1]])
    # a 403 in the executor log 5s after the intent (log lines are UTC ISO here)
    import datetime as dt
    iso = dt.datetime.fromtimestamp(ts_ms / 1000 + 5, tz=dt.timezone.utc
                                    ).strftime("%Y-%m-%dT%H:%M:%S")
    log = tmp_path / "live_exec.log"
    log.write_text(f"{iso}Z [error    ] clob_place_failed error=\"PolyApiException["
                   f"status_code=403, error_message=Trading restricted\"\n")
    from research.analysis.live_gap_attribution import build_attribution, summarize
    df = build_attribution(live_dir=live, jsonl_root=jsonl, geoblock_log=log)
    r = df.iloc[0]
    assert r["cohort"] == "geoblocked"
    assert r["cmp_missed"] == pytest.approx(0.0)        # not the executor's miss
    s = summarize(df)
    assert s["cohorts"]["geoblocked"]["n"] == 1
