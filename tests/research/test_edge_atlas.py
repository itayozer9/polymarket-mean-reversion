"""Unit tests for the pure helpers of research/analysis/edge_atlas.py.

No mocking — real numpy arrays / pandas frames. The data-assembly entry points
(atlas_tick_frame / decision_slug_sets) are NOT tested here: they are thin
compositions of already-tested loaders and would need the full repo dataset.
"""
import numpy as np
import pandas as pd
import pytest

from research.analysis.edge_atlas import (
    ASK_LABELS, CHEAP_HI, CHEAP_LO, CLD_LABELS, FAV_HI, FAV_LO, N_ASK, N_CLD,
    SIDES, TL_EDGES, TL_LABELS, ask_bin, benjamini_hochberg, bootstrap_p_one_sided,
    build_obs, cell_label, cell_table, cld_bin, clustered_ci, coverage,
    decode_cell, encode_cell, per_obs_returns, tl_bin)


# --------------------------------------------------------------------------
# binning
# --------------------------------------------------------------------------
def test_ask_bin_fav_boundaries():
    asks = [0.50, 0.5499, 0.55, 0.9499, 0.95, 0.4999, np.nan, 0.999]
    got = ask_bin(asks, FAV_LO, FAV_HI)
    assert got.tolist() == [0, 0, 1, 8, -1, -1, -1, -1]


def test_ask_bin_cheap_boundaries():
    asks = [0.05, 0.0999, 0.10, 0.45, 0.4999, 0.50, 0.0499]
    got = ask_bin(asks, CHEAP_LO, CHEAP_HI)
    assert got.tolist() == [0, 0, 1, 8, 8, -1, -1]


def test_tl_bin_half_open_right():
    # (0,30] (30,60] (60,120] ... (450,900]; outside -> -1
    tls = [0, 1, 30, 30.5, 60, 61, 120, 180, 300, 450, 451, 900, 901, np.nan]
    got = tl_bin(tls)
    assert got.tolist() == [-1, 0, 0, 1, 1, 2, 2, 3, 4, 5, 6, 6, -1, -1]


def test_cld_bin_edges_and_nan():
    v = [0.0, 1.99, 2.0, 4.99, 5.0, 11.99, 12.0, 24.99, 25.0, 500.0, np.nan]
    got = cld_bin(v)
    assert got.tolist() == [0, 0, 1, 1, 2, 2, 3, 3, 4, 4, N_CLD - 1]


def test_encode_decode_round_trip_unique():
    codes = set()
    n_tl = len(TL_EDGES) - 1
    for s in range(len(SIDES)):
        for a in range(N_ASK):
            for t in range(n_tl):
                for c in range(N_CLD):
                    for k in (0, 1):
                        code = int(encode_cell(s, a, t, c, k))
                        codes.add(code)
                        d = decode_cell(code)
                        assert d["side"] == SIDES[s]
                        assert d["ask_bin"] == ASK_LABELS[SIDES[s]][a]
                        assert d["tl_bin"] == TL_LABELS[t]
                        assert d["cld_bin"] == CLD_LABELS[c]
                        assert d["cons"] == ("C" if k else "D")
    assert len(codes) == len(SIDES) * N_ASK * n_tl * N_CLD * 2  # 1512, all distinct
    lbl = cell_label(int(encode_cell(0, 0, 0, 0, 1)))
    assert lbl == "FAV|a0.50-0.55|tl0-30|cl0-2|C"


# --------------------------------------------------------------------------
# economics
# --------------------------------------------------------------------------
def test_per_obs_returns_math():
    g, n = per_obs_returns([1.0, 0.0], [0.5, 0.5], slip=0.0072)
    assert g[0] == pytest.approx(1.0)            # win: 1/0.5 - 1
    assert n[0] == pytest.approx(1.0 / 0.5072 - 1.0)
    assert g[1] == pytest.approx(-1.0)           # loss: stake gone
    assert n[1] == pytest.approx(-1.0)


# --------------------------------------------------------------------------
# statistics
# --------------------------------------------------------------------------
def test_bootstrap_p_constant_positive():
    p_pos, p_neg = bootstrap_p_one_sided(np.full(50, 0.5), n=2000, seed=3)
    assert p_pos == pytest.approx(1 / 2001)
    assert p_neg == pytest.approx(1.0)


def test_bootstrap_p_symmetric_near_half():
    v = np.array([1.0, -1.0] * 25)
    p_pos, p_neg = bootstrap_p_one_sided(v, n=2000, seed=3)
    assert 0.3 < p_pos < 0.7
    assert 0.3 < p_neg < 0.7


def test_clustered_ci_matches_canonical_window_clustered_bootstrap():
    from research.lib.stats import window_clustered_bootstrap
    rng = np.random.default_rng(7)
    values = rng.normal(0.1, 1.0, size=120)
    groups = np.repeat([f"s{i}" for i in range(40)], 3)  # multi-row clusters
    fast = clustered_ci(values, groups, n=300, seed=5)
    canon = window_clustered_bootstrap(values, groups, n=300, seed=5)
    assert np.allclose(fast, canon, atol=1e-9)


def test_benjamini_hochberg_known_vector():
    p = np.array([0.001, 0.011, 0.02, 0.04, 0.9])
    rej = benjamini_hochberg(p, q=0.10)
    assert rej.tolist() == [True, True, True, True, False]


def test_benjamini_hochberg_nan_not_in_family():
    p = np.array([0.001, np.nan, 0.9])
    rej = benjamini_hochberg(p, q=0.10)
    assert rej.tolist() == [True, False, False]
    assert not benjamini_hochberg(np.array([np.nan, np.nan]), q=0.10).any()


def test_coverage():
    assert coverage({"a", "b", "c", "d"}, {"a", "b", "x"}) == pytest.approx(0.5)
    assert coverage({"a"}, set()) == 0.0
    assert np.isnan(coverage(set(), {"a"}))


# --------------------------------------------------------------------------
# observation construction (one obs per slug x cell, first tick)
# --------------------------------------------------------------------------
def _tick(slug, sec, tl, fav_side, fav_ask, yes_bid, yes_ask, cl_up, *,
          abs_cld=1.0, cons=True, split="dev"):
    return dict(slug=slug, symbol="btc", date="2026-05-23", split=split,
                window_start_ts=1_779_496_200, seconds_into_window=sec,
                time_left_sec=tl, yes_best_bid=yes_bid, yes_best_ask=yes_ask,
                yes_ask_depth=100.0, no_ask_depth=50.0, fav_side=fav_side,
                fav_ask=fav_ask, consistent=cons, abs_cl_dist=abs_cld, cl_up=cl_up)


def test_build_obs_first_tick_per_cell_and_sides():
    # slug w1: fav YES at 0.80; two ticks in the same cell (tl 100, 110 -> tl60-120)
    # then one tick in a new tl bin (tl 20 -> tl0-30). cl_up=1 -> FAV wins, CHEAP loses.
    t = pd.DataFrame([
        _tick("w1", 800, 100, "yes", 0.80, 0.78, 0.80, 1.0),
        _tick("w1", 790, 110, "yes", 0.80, 0.78, 0.80, 1.0),
        _tick("w1", 880, 20, "yes", 0.86, 0.84, 0.86, 1.0),
    ])
    obs = build_obs(t, slip=0.0072)
    fav = obs[obs["cell"].map(lambda c: decode_cell(int(c))["side"]) == "FAV"]
    cheap = obs[obs["cell"].map(lambda c: decode_cell(int(c))["side"]) == "CHEAP"]
    # 2 fav cells (tl60-120 and tl0-30) — first tick of the tl60-120 cell is sec 790
    assert len(fav) == 2 and len(cheap) == 2
    f1 = fav[fav["entry_sec"] == 790]
    assert len(f1) == 1 and f1["ask"].iloc[0] == pytest.approx(0.80)
    assert (fav["won"] == 1.0).all()
    # cheap side of a YES favourite = NO at 1 - yes_best_bid, and it LOST
    c1 = cheap[cheap["entry_sec"] == 790]
    assert c1["ask"].iloc[0] == pytest.approx(1.0 - 0.78)
    assert (cheap["won"] == 0.0).all()
    # depth: fav YES uses yes_ask_depth(100)*ask; cheap NO uses no_ask_depth(50)*ask
    assert f1["depth_usd_side"].iloc[0] == pytest.approx(100.0 * 0.80)
    assert c1["depth_usd_side"].iloc[0] == pytest.approx(50.0 * 0.22)
    # no duplicated (slug, cell)
    assert not obs.duplicated(["slug", "cell"]).any()


def test_build_obs_fav_no_side_and_out_of_range():
    # fav NO at 0.60 (yes_mid<0.5): FAV ask = 1-yes_bid = 0.60, CHEAP = yes_ask 0.42.
    # cl_up=0 -> NO favourite WINS, cheap YES loses. fav_ask 0.97 tick lands nowhere
    # on FAV; its cheap complement (yes_ask 0.04) below 0.05 lands nowhere on CHEAP.
    t = pd.DataFrame([
        _tick("w2", 100, 800, "no", 0.60, 0.40, 0.42, 0.0),
        _tick("w3", 100, 800, "no", 0.97, 0.03, 0.04, 0.0),
    ])
    obs = build_obs(t, slip=0.0072)
    assert set(obs["slug"]) == {"w2"}
    fav = obs[obs["cell"].map(lambda c: decode_cell(int(c))["side"]) == "FAV"]
    cheap = obs[obs["cell"].map(lambda c: decode_cell(int(c))["side"]) == "CHEAP"]
    assert fav["ask"].iloc[0] == pytest.approx(0.60) and fav["won"].iloc[0] == 1.0
    assert cheap["ask"].iloc[0] == pytest.approx(0.42) and cheap["won"].iloc[0] == 0.0
    # fav NO depth uses no_ask_depth; cheap YES depth uses yes_ask_depth
    assert fav["depth_usd_side"].iloc[0] == pytest.approx(50.0 * 0.60)
    assert cheap["depth_usd_side"].iloc[0] == pytest.approx(100.0 * 0.42)


def test_build_obs_drops_missing_label():
    t = pd.DataFrame([_tick("w4", 100, 800, "yes", 0.80, 0.78, 0.80, np.nan)])
    assert build_obs(t, slip=0.0072).empty


# --------------------------------------------------------------------------
# cell table + gates (synthetic, deterministic)
# --------------------------------------------------------------------------
def test_cell_table_gates_and_bh():
    cell_a, cell_b = 11, 22
    rows = []
    # cell A: 60 dev windows, every obs net +0.5 -> p ~ 1/(P_N+1), CI > 0
    for i in range(60):
        rows.append(dict(slug=f"a{i}", symbol="btc", date="2026-05-23", split="dev",
                         entry_sec=10, cell=cell_a, ask=0.5, won=1.0,
                         depth_usd_side=100.0, ret_gross=0.5, ret_net=0.5))
    # + holdout positive
    for i in range(20):
        rows.append(dict(slug=f"ah{i}", symbol="btc", date="2026-05-29", split="holdout",
                         entry_sec=10, cell=cell_a, ask=0.5, won=1.0,
                         depth_usd_side=100.0, ret_gross=0.5, ret_net=0.5))
    # cell B: 60 dev windows alternating +1/-1 -> mean 0, p ~ 0.5
    for i in range(60):
        r = 1.0 if i % 2 == 0 else -1.0
        rows.append(dict(slug=f"b{i}", symbol="btc", date="2026-05-23", split="dev",
                         entry_sec=10, cell=cell_b, ask=0.5, won=max(r, 0.0),
                         depth_usd_side=100.0, ret_gross=r, ret_net=r))
    obs = pd.DataFrame(rows)
    cells = cell_table(obs).set_index("cell")
    a, b = cells.loc[cell_a], cells.loc[cell_b]
    assert a["n_dev"] == 60 and a["n_hold"] == 20
    assert a["net_dev"] == pytest.approx(0.5) and a["dev_lo"] > 0
    assert bool(a["cand_pos"]) and not bool(a["cand_neg"])
    assert not bool(b["cand_pos"]) and not bool(b["cand_neg"])
    assert 0.2 < b["p_pos_dev"] < 0.8


def test_cell_table_negative_tail():
    rows = []
    for i in range(50):
        rows.append(dict(slug=f"n{i}", symbol="btc", date="2026-05-23", split="dev",
                         entry_sec=10, cell=7, ask=0.5, won=0.0,
                         depth_usd_side=10.0, ret_gross=-0.4, ret_net=-0.4))
    for i in range(15):
        rows.append(dict(slug=f"nh{i}", symbol="btc", date="2026-05-29", split="holdout",
                         entry_sec=10, cell=7, ask=0.5, won=0.0,
                         depth_usd_side=10.0, ret_gross=-0.4, ret_net=-0.4))
    cells = cell_table(pd.DataFrame(rows))
    r = cells.iloc[0]
    assert bool(r["cand_neg"]) and not bool(r["cand_pos"])
    assert r["dev_hi"] < 0 and r["net_hold"] < 0


def test_cell_table_requires_unique_obs():
    rows = [dict(slug="x", symbol="btc", date="d", split="dev", entry_sec=1,
                 cell=3, ask=0.5, won=1.0, depth_usd_side=1.0,
                 ret_gross=1.0, ret_net=1.0)] * 2
    with pytest.raises(AssertionError):
        cell_table(pd.DataFrame(rows))
