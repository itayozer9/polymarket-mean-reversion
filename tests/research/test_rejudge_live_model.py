"""Tests for research/analysis/rejudge_live_model.py — the config filters must
mirror strategies.yaml exactly (filter drift here would silently re-judge a
different strategy than the one deployed)."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import numpy as np
import pandas as pd
import pytest

from research.analysis.rejudge_live_model import (
    CONFIGS,
    decisions_for,
    effective_ceiling,
)


def _frame(rows):
    defaults = dict(symbol="btc", date="2026-06-01", split="dev",
                    yes_best_bid=0.60, yes_best_ask=0.62, book_healthy=True,
                    adverse_vel_10s=0.0, realized_vol=0.005, fav_won=1)
    out = []
    for r in rows:
        d = dict(defaults)
        d.update(r)
        out.append(d)
    return pd.DataFrame(out)


def test_det_lwd_filter_first_tick_and_side():
    b = _frame([
        # qualifies (consistent, t<=60, dist>=8, fav ask in 0.50-0.88)
        dict(slug="s1", seconds_into_window=850, time_left_sec=50, abs_dist_bps=10,
             dist_strike_bps=10, consistent=True, fav_side="yes", fav_ask=0.70),
        # later tick same window — first-tick reduction must keep the 850s one
        dict(slug="s1", seconds_into_window=860, time_left_sec=40, abs_dist_bps=12,
             dist_strike_bps=12, consistent=True, fav_side="yes", fav_ask=0.75),
        # fails: inconsistent
        dict(slug="s2", seconds_into_window=850, time_left_sec=50, abs_dist_bps=10,
             dist_strike_bps=10, consistent=False, fav_side="yes", fav_ask=0.70),
        # fails: ask above 0.88 band
        dict(slug="s3", seconds_into_window=850, time_left_sec=50, abs_dist_bps=10,
             dist_strike_bps=10, consistent=True, fav_side="yes", fav_ask=0.91),
        # fails: adverse_vel > 2
        dict(slug="s4", seconds_into_window=850, time_left_sec=50, abs_dist_bps=10,
             dist_strike_bps=10, consistent=True, fav_side="yes", fav_ask=0.70,
             adverse_vel_10s=3.5),
        # DOWN favourite qualifies; buy_yes must be False, ask = fav (no-side) ask
        dict(slug="s5", seconds_into_window=850, time_left_sec=50, abs_dist_bps=10,
             dist_strike_bps=-10, consistent=True, fav_side="no", fav_ask=0.70),
    ])
    d = decisions_for("det_lwd_live", b)
    assert sorted(d["slug"]) == ["s1", "s5"]
    r1 = d[d.slug == "s1"].iloc[0]
    assert r1["entry_sec"] == 850 and bool(r1["buy_yes"]) is True
    assert r1["entry_ask"] == pytest.approx(0.70)
    r5 = d[d.slug == "s5"].iloc[0]
    assert bool(r5["buy_yes"]) is False
    assert r5["entry_ask"] == pytest.approx(0.70)


def test_fav_disagree_buys_spot_side_with_ud_ask():
    b = _frame([
        # spot ABOVE strike but book disagrees -> buy YES at the yes ask
        dict(slug="w1", seconds_into_window=600, time_left_sec=300, abs_dist_bps=15,
             dist_strike_bps=15, consistent=False, fav_side="no", fav_ask=0.80,
             yes_best_ask=0.25, yes_best_bid=0.20),
        # spot BELOW strike, book disagrees -> buy NO at 1 - yes_bid
        dict(slug="w2", seconds_into_window=600, time_left_sec=300, abs_dist_bps=15,
             dist_strike_bps=-15, consistent=False, fav_side="yes", fav_ask=0.80,
             yes_best_ask=0.85, yes_best_bid=0.78),
        # consistent -> not a disagree window
        dict(slug="w3", seconds_into_window=600, time_left_sec=300, abs_dist_bps=15,
             dist_strike_bps=15, consistent=True, fav_side="yes", fav_ask=0.80),
        # dist below the 10bps gate
        dict(slug="w4", seconds_into_window=600, time_left_sec=300, abs_dist_bps=5,
             dist_strike_bps=5, consistent=False, fav_side="no", fav_ask=0.80,
             yes_best_ask=0.25),
    ])
    d = decisions_for("fav_disagree", b)
    assert sorted(d["slug"]) == ["w1", "w2"]
    w1 = d[d.slug == "w1"].iloc[0]
    assert bool(w1["buy_yes"]) is True and w1["entry_ask"] == pytest.approx(0.25)
    w2 = d[d.slug == "w2"].iloc[0]
    assert bool(w2["buy_yes"]) is False and w2["entry_ask"] == pytest.approx(1 - 0.78)


def test_fav_deepdown_restricts_to_no_side():
    b = _frame([
        dict(slug="x1", seconds_into_window=450, time_left_sec=450, abs_dist_bps=5,
             dist_strike_bps=-5, consistent=True, fav_side="no", fav_ask=0.90),
        dict(slug="x2", seconds_into_window=450, time_left_sec=450, abs_dist_bps=5,
             dist_strike_bps=5, consistent=True, fav_side="yes", fav_ask=0.90),
    ])
    d = decisions_for("fav_deepdown", b)
    assert list(d["slug"]) == ["x1"]            # only the DOWN favourite
    assert bool(d.iloc[0]["buy_yes"]) is False


def test_effective_ceiling_adaptive_for_dual():
    dual = CONFIGS["det_d12_dual_live"]
    lwd = CONFIGS["det_lwd_live"]
    assert effective_ceiling(dual, cl_dist_bps=5.0) == pytest.approx(0.78)
    assert effective_ceiling(dual, cl_dist_bps=-25.0) == pytest.approx(0.85)
    assert effective_ceiling(dual, cl_dist_bps=np.nan) == pytest.approx(0.78)
    assert effective_ceiling(lwd, cl_dist_bps=50.0) == pytest.approx(0.88)
