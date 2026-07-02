"""Embargo pins for research/dataset/trade_prints.py (T4, Edge Hunt v2).

Direct injection test: a print at second s becomes visible to features exactly at decision
second s+EMBARGO_S, never earlier. Plus signed-flow convention and burst-timer checks on
synthetic prints."""
import numpy as np
import pandas as pd

from research.dataset.trade_prints import EMBARGO_S, _features_slug, _per_second


def _prints(rows, slug="btc-updown-15m-1000000800"):
    w0 = int(slug.rsplit("-", 1)[1])
    return pd.DataFrame([{
        "event_ts_ms": (w0 + sec) * 1000, "market_slug": slug,
        "outcome": outcome, "price": price, "size": size, "side": side,
    } for sec, outcome, price, size, side in rows])


def _feat(rows):
    per_sec = _per_second(_prints(rows), "15m")
    return _features_slug(per_sec, 900)


def test_print_visible_exactly_at_embargo():
    base = _feat([(10, "yes", 0.5, 200.0, "BUY")])
    assert base.loc[10, "pr_usd_2s"] == 0.0            # decision second of the print: unseen
    assert base.loc[10 + EMBARGO_S - 1, "pr_usd_2s"] == 0.0
    assert base.loc[10 + EMBARGO_S, "pr_usd_2s"] == 100.0   # 0.5*200
    assert base.loc[10 + EMBARGO_S, "pr_usd_30s"] == 100.0


def test_injection_at_decision_second_changes_nothing():
    with_late = _feat([(10, "yes", 0.5, 200.0, "BUY"), (40, "yes", 0.9, 500.0, "BUY")])
    without = _feat([(10, "yes", 0.5, 200.0, "BUY")])
    upto = 40 + EMBARGO_S - 1
    cols = ["pr_usd_2s", "pr_usd_30s", "pr_signed_30s", "pr_max_30s", "pr_since_burst_s"]
    pd.testing.assert_frame_equal(with_late.loc[:upto, cols], without.loc[:upto, cols])
    assert with_late.loc[40 + EMBARGO_S, "pr_usd_2s"] == 450.0


def test_signed_flow_convention():
    f = _feat([(10, "yes", 0.5, 100.0, "BUY"),    # +50  (bullish)
               (11, "no", 0.5, 100.0, "BUY"),     # -50  (bearish)
               (12, "yes", 0.4, 100.0, "SELL"),   # -40
               (13, "no", 0.4, 100.0, "SELL")])   # +40
    assert np.isclose(f.loc[13 + EMBARGO_S, "pr_signed_30s"], 50 - 50 - 40 + 40)
    assert np.isclose(f.loc[13 + EMBARGO_S, "pr_usd_30s"], 50 + 50 + 40 + 40)


def test_burst_timer():
    f = _feat([(20, "yes", 0.5, 300.0, "BUY")])   # $150 >= BURST_USD
    vis = 20 + EMBARGO_S
    assert f.loc[vis, "pr_since_burst_s"] == 0
    assert f.loc[vis + 10, "pr_since_burst_s"] <= 10
    assert np.isinf(f.loc[vis - 1, "pr_since_burst_s"])
