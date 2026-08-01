"""Pure-helper tests for research/analysis/capacity_curves.py (synthetic frames,
no repo data): first-tick reduction with side/ask carried, the registered
max-viable-stake rule, and config-table sanity vs the deployed params."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import numpy as np
import pandas as pd
import pytest

from research.analysis.capacity_curves import (
    CONFIGS, STAKES, first_tick_decisions, max_viable_stake)


def test_first_tick_decisions_keeps_first_and_carries_side_ask():
    c = pd.DataFrame([
        dict(slug="s1", symbol="btc", date="d", split="dev",
             seconds_into_window=500, time_left_sec=400),
        dict(slug="s1", symbol="btc", date="d", split="dev",
             seconds_into_window=480, time_left_sec=420),   # earlier tick wins
        dict(slug="s2", symbol="eth", date="d", split="future",
             seconds_into_window=600, time_left_sec=300),
    ])
    out = first_tick_decisions(c, buy_yes=[True, False, True],
                               entry_ask=[0.60, 0.55, 0.70])
    out = out.set_index("slug")
    assert out.loc["s1", "entry_sec"] == 480
    assert out.loc["s1", "buy_yes"] == False  # noqa: E712 — row-aligned side
    assert out.loc["s1", "entry_ask"] == pytest.approx(0.55)
    assert out.loc["s2", "time_left"] == 300
    assert list(out.columns) == ["symbol", "date", "split", "entry_sec",
                                 "time_left", "buy_yes", "entry_ask"]


def _pt(stake, lo, seed_mean, model="live_guarded"):
    return dict(stake=stake, model=model,
                future=dict(n=50, ev=0.5, lo=lo, hi=1.0),
                fut_seeds=dict(ev_mean=seed_mean, ev_sd=0.1, fills_mean=50))


def test_max_viable_stake_rule():
    pts = [_pt(5, lo=0.2, seed_mean=0.5), _pt(10, lo=0.1, seed_mean=0.4),
           _pt(25, lo=-0.2, seed_mean=0.3), _pt(50, lo=0.05, seed_mean=-0.1)]
    # $25 fails CI-lower, $50 fails seeds-mean -> max viable is $10
    assert max_viable_stake(pts) == 10
    # v2 rows must be ignored even if they pass
    pts.append(_pt(50, lo=1.0, seed_mean=1.0, model="v2"))
    assert max_viable_stake(pts) == 10


def test_max_viable_stake_none_when_nothing_passes():
    assert max_viable_stake([_pt(5, lo=-0.1, seed_mean=0.2)]) is None
    # missing CI (too few fills) does not pass
    p = _pt(5, lo=None, seed_mean=0.2)
    p["future"]["lo"] = None
    assert max_viable_stake([p]) is None


def test_config_table_matches_deployed_params():
    """Filter drift here would size a different strategy than the deployed one."""
    fd = CONFIGS["fav_disagree_live045"]["custom"]
    assert (fd["t_lo"], fd["t_hi"], fd["dist_min"]) == (120, 360, 10.0)
    assert (fd["ask_lo"], fd["ask_hi"]) == (0.05, 0.45)          # live max_ask 0.45
    ed = CONFIGS["early_disagree"]["custom"]
    assert (ed["t_lo"], ed["t_hi"], ed["ask_lo"], ed["ask_hi"]) == (450, 900, 0.30, 0.45)
    ps = CONFIGS["psettle_2246"]["params"]                       # livecost specs.jsonl
    assert ps == dict(side="cheap", d_thr=0.15, cl_floor=0, t_lo=60, t_hi=360,
                      ask_lo=0.50, ask_hi=0.78)
    assert CONFIGS["fade_op4"]["ceiling_cfg"]["ask_hi"] == 0.40  # OP4 fill ceiling
    assert STAKES == (5.0, 10.0, 25.0, 50.0)


def test_burst_family_matches_deployed_bands():
    from research.analysis.burst_cap import FAMILY
    assert FAMILY["fav_disagree_live045"]["ask_hi"] == 0.45
    assert FAMILY["early_disagree"]["t_lo"] == 450
    assert FAMILY["fav_disagree"]["ask_hi"] == 0.90  # rejudge CONFIGS twin
