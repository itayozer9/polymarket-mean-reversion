"""Pure-helper tests for research/analysis/burst_cap.py (synthetic frames, no
repo data): cap-policy filtering + tie-breaks, pairwise outcome counts,
conditional win rates, permutation null, max drawdown."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import numpy as np
import pandas as pd
import pytest

from research.analysis.burst_cap import (
    agreement_rate,
    conditional_winrates,
    decide,
    group_sizes,
    max_drawdown,
    pair_counts,
    perm_null_agreement,
    policy_keep,
)


def _dec():
    """Two window-ts groups: ts=100 a 3-coin burst, ts=200 a singleton."""
    return pd.DataFrame([
        dict(slug="btc-100", window_start_ts=100, entry_sec=700, entry_ask=0.30),
        dict(slug="eth-100", window_start_ts=100, entry_sec=701, entry_ask=0.20),
        dict(slug="sol-100", window_start_ts=100, entry_sec=700, entry_ask=0.40),
        dict(slug="xrp-200", window_start_ts=200, entry_sec=650, entry_ask=0.25),
    ])


def test_policy_uncapped_is_identity():
    d = _dec()
    out = policy_keep(d, "uncapped")
    assert len(out) == 4 and set(out["slug"]) == set(d["slug"])


def test_policy_max1_first_keeps_earliest_then_cheapest():
    out = policy_keep(_dec(), "max1_first")
    # ts=100: entry_sec 700 tie between btc(0.30) and sol(0.40) -> lower ask wins
    assert set(out["slug"]) == {"btc-100", "xrp-200"}


def test_policy_max1_cheap_keeps_lowest_ask():
    out = policy_keep(_dec(), "max1_cheap")
    assert set(out["slug"]) == {"eth-100", "xrp-200"}


def test_policy_max2_first():
    out = policy_keep(_dec(), "max2_first")
    # earliest two at ts=100: sec700 pair (btc, sol) — eth at 701 dropped
    assert set(out["slug"]) == {"btc-100", "sol-100", "xrp-200"}


def test_policy_deterministic_on_full_ties():
    d = _dec()
    d["entry_sec"] = 700
    d["entry_ask"] = 0.30
    out1 = policy_keep(d, "max1_first")["slug"].tolist()
    out2 = policy_keep(d.sample(frac=1, random_state=7), "max1_first")["slug"].tolist()
    assert out1 == out2  # slug tie-break makes the choice order-independent


def test_group_sizes():
    gs = group_sizes(_dec())
    assert gs.loc[100] == 3 and gs.loc[200] == 1


def test_pair_counts_and_conditionals():
    # group A: win,win  group B: win,lose  singleton ignored
    won = np.array([1, 1, 1, 0, 1])
    gid = np.array(["A", "A", "B", "B", "C"])
    c = pair_counts(won, gid)
    assert c == dict(ww=1, ll=0, dis=1, tot=2)
    cw = conditional_winrates(c)
    # ordered pairs with partner won: (A1,A2),(A2,A1),(B_w? partner B_l won->no)...
    assert cw["p_win_given_partner_won"] == pytest.approx(2 / 3)
    assert cw["p_win_given_partner_lost"] == pytest.approx(1.0)
    assert agreement_rate(won, gid) == pytest.approx(0.5)


def test_perm_null_detects_perfect_correlation():
    # 10 groups of 2, all pairs agree; half the groups win, half lose -> WR 50%
    won = np.array([1, 1, 0, 0] * 5)
    gid = np.repeat(np.arange(10), 2)
    r = perm_null_agreement(won, gid, n=500, seed=0)
    assert r["obs"] == pytest.approx(1.0)
    assert r["null_mean"] < 0.7        # independence null near 0.5
    assert r["p"] < 0.01


def test_perm_null_no_correlation_is_insignificant():
    rng = np.random.default_rng(3)
    won = rng.integers(0, 2, size=200)
    gid = np.repeat(np.arange(100), 2)
    r = perm_null_agreement(won, gid, n=300, seed=1)
    assert r["p"] > 0.05


def _pol(policy, lg_total, lg_worst, v2_total, v2_worst, n=50):
    return dict(
        policy=policy, n_attempts=100,
        v2_future=dict(n_fills=n, total=v2_total, worst_group=v2_worst,
                       ev_fill=v2_total / n, max_dd=10.0),
        lg_future=dict(n_fills=n, total=lg_total, worst_group=lg_worst,
                       ev_fill=lg_total / n, max_dd=10.0),
        lg_future_seeds=dict(total=dict(mean=lg_total, sd=1.0),
                             ev=dict(mean=lg_total / n, sd=0.1),
                             worst=dict(mean=lg_worst, sd=1.0),
                             dd=dict(mean=10.0, sd=1.0)),
    )


def test_decide_recommends_cap_that_cuts_worst_and_keeps_total():
    pols = [
        _pol("uncapped", lg_total=100.0, lg_worst=-16.0, v2_total=200.0, v2_worst=-21.0),
        # cuts worst 67% and keeps 95% of total under both models -> qualifies
        _pol("max1_first", lg_total=95.0, lg_worst=-5.3, v2_total=170.0, v2_worst=-5.3),
        # keeps more total but cuts worst only 12% -> fails leg (i)
        _pol("max2_first", lg_total=99.0, lg_worst=-14.0, v2_total=195.0, v2_worst=-10.0),
        # cuts worst but keeps only 30% of total -> fails leg (ii)
        _pol("max1_cheap", lg_total=30.0, lg_worst=-5.3, v2_total=60.0, v2_worst=-5.3),
    ]
    v = decide(pols)
    assert v["recommend"] == "max1_first" and v["grounds"] == "risk_rule"


def test_decide_honest_negative_when_no_policy_qualifies():
    pols = [
        _pol("uncapped", lg_total=100.0, lg_worst=-16.0, v2_total=200.0, v2_worst=-21.0),
        _pol("max1_first", lg_total=50.0, lg_worst=-5.3, v2_total=100.0, v2_worst=-5.3),
    ]
    v = decide(pols)
    assert v["recommend"] is None and v["grounds"] == "no_policy_qualified"


def test_decide_ev_grounds_when_bursts_net_negative():
    pols = [
        _pol("uncapped", lg_total=-20.0, lg_worst=-16.0, v2_total=-10.0, v2_worst=-21.0),
        # cap flips total positive under BOTH models -> EV-grounds recommendation
        # (worst improves <25% so the risk rule does not trigger first)
        _pol("max1_first", lg_total=15.0, lg_worst=-14.0, v2_total=12.0, v2_worst=-18.0),
    ]
    v = decide(pols)
    assert v["recommend"] == "max1_first" and v["grounds"] == "ev_grounds"


def test_max_drawdown():
    assert max_drawdown(np.array([])) == 0.0
    assert max_drawdown(np.array([1.0, 2.0, 3.0])) == pytest.approx(0.0)
    # cum: 5, 2, 4, -2 -> peak 5, trough -2 => dd 7
    assert max_drawdown(np.array([5.0, -3.0, 2.0, -6.0])) == pytest.approx(7.0)
    # first trade a loss: dd from flat start = 4
    assert max_drawdown(np.array([-4.0, 1.0])) == pytest.approx(4.0)
