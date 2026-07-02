"""Look-ahead guard pins for research/dataset/xbook.py (T1, Edge Hunt v2).

The XI4 burn: the original cross-book result was 74% acausal via back-filled 5m strikes.
These pins make the three guards structural: (1) 1s embargo — a 5m tick from the decision
second itself is never used; (2) max-age — stale 5m books (which fake violations) drop the
row; (3) k5_causal — pre-strike-fix rows are flagged unless s5 >= 35. Plus hand-checked
margin arithmetic for all four legs."""
import numpy as np
import pandas as pd

from research.dataset.xbook import build_xbook, T_STRIKE_FIX


def _t15(w15: int, secs, ya=0.60, yb=0.58, na=0.42, nb=0.40):
    n = len(secs)
    return pd.DataFrame({
        "slug": [f"btc-updown-15m-{w15}"] * n, "symbol": ["btc"] * n,
        "date": ["2026-01-01"] * n, "split": ["dev"] * n,
        "window_start_ts": [w15] * n, "seconds_into_window": secs,
        "yes_best_bid": yb, "yes_best_ask": ya, "no_best_bid": nb, "no_best_ask": na,
        "yes_bid_depth": 100.0, "yes_ask_depth": 100.0,
        "no_bid_depth": 100.0, "no_ask_depth": 100.0,
        "yes_mid": (ya + yb) / 2, "spread_yes": ya - yb,
        "book_healthy": True, "start_price": 100.0,
    })


def _t5(w5: int, s5s, k5=100.2, yb=0.70, ya=0.72, nb=0.28, na=0.30):
    n = len(s5s)
    return pd.DataFrame({
        "slug": [f"btc-updown-5m-{w5}"] * n, "symbol": ["btc"] * n,
        "window_start_ts": [w5] * n, "seconds_into_window": s5s,
        "yes_best_bid": yb, "yes_best_ask": ya, "no_best_bid": nb, "no_best_ask": na,
        "yes_bid_depth": 50.0, "yes_ask_depth": 50.0,
        "no_bid_depth": 50.0, "no_ask_depth": 50.0,
        "book_healthy": True, "start_price": k5,
    })


def test_embargo_never_same_second():
    w15 = 900 * 1000          # arbitrary; w5 = w15+600 satisfies %900==600
    t15 = _t15(w15, [605])
    # only 5m tick exists AT the decision second -> must NOT match
    j = build_xbook(t15=t15, t5=_t5(w15 + 600, [5]), out=None)
    assert len(j) == 0
    # tick one second earlier -> matches, age == 1
    j = build_xbook(t15=t15, t5=_t5(w15 + 600, [4]), out=None)
    assert len(j) == 1 and j["age_5m_s"].iloc[0] == 1


def test_stale_5m_dropped():
    w15 = 900 * 1000
    t15 = _t15(w15, [610])
    # newest 5m tick is 5s old -> beyond MAX_AGE_S=3 -> dropped
    j = build_xbook(t15=t15, t5=_t5(w15 + 600, [5]), out=None)
    assert len(j) == 0
    j = build_xbook(t15=t15, t5=_t5(w15 + 600, [7]), out=None)
    assert len(j) == 1 and j["age_5m_s"].iloc[0] == 3


def test_k5_causal_flag():
    w_pre = (int(T_STRIKE_FIX) // 900) * 900 - 900 * 4 + 600   # pre-fix co-terminal w5
    w15_pre = w_pre - 600
    j = build_xbook(t15=_t15(w15_pre, [610, 660]), t5=_t5(w_pre, list(range(0, 65))), out=None)
    by_sec = j.set_index("seconds_into_window")["k5_causal"]
    assert not by_sec.loc[610]          # s5 ~9 < 35 pre-fix -> acausal
    assert by_sec.loc[660]              # s5 ~59 >= 35 -> causal
    w_post = (int(T_STRIKE_FIX) // 900) * 900 + 900 * 4 + 600
    j = build_xbook(t15=_t15(w_post - 600, [610]), t5=_t5(w_post, list(range(0, 12))), out=None)
    assert j["k5_causal"].all()         # post-fix causal from sec 0


def test_margins_all_four_legs():
    w15 = 900 * 1000
    j = build_xbook(t15=_t15(w15, [700], ya=0.60, yb=0.58, na=0.42, nb=0.40),
                    t5=_t5(w15 + 600, [97], k5=100.2, yb=0.70, ya=0.72, nb=0.28, na=0.30),
                    out=None)
    r = j.iloc[0]
    assert np.isclose(r["gap_bps"], (100.2 - 100.0) / 100.0 * 1e4)   # +20 bps
    assert np.isclose(r["m_15y"], 0.70 - 0.60)    # buy YES15: yes5_bid - yes15_ask
    assert np.isclose(r["m_5n"], 0.40 - 0.30)     # buy NO5:   no15_bid - no5_ask
    assert np.isclose(r["m_15n"], 0.28 - 0.42)    # buy NO15:  no5_bid - no15_ask
    assert np.isclose(r["m_5y"], 0.58 - 0.72)     # buy YES5:  yes15_bid - yes5_ask


def test_no_outcome_columns_loaded():
    from research.dataset.xbook import _C15, _C5
    banned = {"outcome", "outcome_up", "outcome_up_clean", "end_price", "official_up"}
    assert not banned & set(_C15) and not banned & set(_C5)
