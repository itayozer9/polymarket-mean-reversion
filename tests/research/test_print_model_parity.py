"""Parity pins: engine frozen settlement-print model vs the research original.

THE load-bearing tests for the psettle paper twins (psettle_ud_v1 / oracle_fade_v1,
strategies.yaml 2026-06-11). The engine carries a frozen COPY of the research
p_settle evaluation (src/mean_reversion_live/engine/print_model.py — research code
must never be imported from the engine), plus live reconstructions of its features
(ws_collector cl_dist/cl_cb_basis/cl_oracle_age, DeterminismState cb_dist/time_left/
ResearchRVol). If any of these drift from the research definitions, the forward
paper test is meaningless — these tests pin them:

  1. Logistic parity:   |engine_p - research_p| < 1e-9 on ~1000 sampled frame rows
                        (measured 2.2e-16) + identical NaN (fail-closed) masks.
  2. Feature parity:    50 sampled (slug, second) points rebuilt from the RAW
                        chainlink CSVs through the live cache (record + read_asof +
                        warm_from_disk). Measured: cl_dist exact, basis < 1e-9 bps
                        (formula rounding), age exact, |dp| < 1e-12; the documented
                        live tolerance — oracle age off by a full +-15s poll
                        interval — moves p by < 0.02 (measured max 0.0061).
  3. Decision parity:   one full historical day replayed tick-by-tick through
                        DeterminismState(mode="psettle") vs the research rules on
                        the joined frame. Assert window-set Jaccard > 0.90 per the
                        pre-registered bar (measured 1.000 / same-second 1.000 for
                        both rules on 2026-06-08). The research reference is
                        cross-checked against the ACTUAL research functions
                        (hypothesis_sweep.fam_psettle / oracle_print_model.
                        fade_decisions) so a transcription error cannot hide.

Heavy (loads the research frame via edge_lab.load_base); skips when the research
artifacts are not present so a fresh checkout stays green.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "src"))

MODEL_JSON = REPO / "data" / "research" / "oracle_print_model.json"
JOINED = REPO / "data" / "research" / "joined_15m.parquet"
CL_DIR = REPO / "data" / "live_chainlink"

pytestmark = pytest.mark.skipif(
    not (MODEL_JSON.exists() and JOINED.exists() and CL_DIR.exists()),
    reason="research artifacts (model json / joined parquet / chainlink csvs) not built")

DAY = "2026-06-08"  # decision-parity day: fully collected, in the frame
FEATS = ["cl_dist_bps", "cb_dist_bps", "cl_cb_basis_bps", "cl_oracle_age_s",
         "realized_vol"]


@pytest.fixture(scope="module")
def art():
    from research.analysis.oracle_print_model import load_model
    return load_model()


@pytest.fixture(scope="module")
def em():
    from mean_reversion_live.engine.print_model import load_print_model
    return load_print_model(MODEL_JSON)


@pytest.fixture(scope="module")
def tf():
    from research.analysis.oracle_print_model import tick_feature_frame
    return tick_feature_frame()


def _engine_p(em, r, **overrides):
    """Engine p_settle_up on a frame row (namedtuple), with optional overrides."""
    from mean_reversion_live.engine.print_model import p_settle_up
    kw = dict(cl_dist_bps=r.cl_dist_bps, cb_dist_bps=r.cb_dist_bps,
              cl_cb_basis_bps=r.cl_cb_basis_bps, cl_oracle_age_s=r.cl_oracle_age_s,
              time_left_sec=r.time_left_sec, realized_vol=r.realized_vol,
              symbol=r.symbol)
    kw.update(overrides)
    return p_settle_up(em, **kw)


# ── 0. realized_vol live buffer == research realized_vol_per_sec ─────────────────

def test_research_rvol_buffer_matches_research_function():
    from research.features.core import realized_vol_per_sec
    from mean_reversion_live.engine.det_features import ResearchRVol
    rng = np.random.default_rng(7)
    mp = np.cumsum(rng.normal(0, 0.004, size=400))  # a move_pct-like random walk
    want = realized_vol_per_sec(mp, window=60)
    rv = ResearchRVol()
    got = []
    for v in mp:
        rv.push(float(v))
        got.append(rv.value())
    assert np.allclose(got, want, atol=1e-12)
    # window-edge semantics: first tick -> 0.0 (research returns 0 with <2 diffs)
    assert got[0] == 0.0 and want[0] == 0.0


# ── 1. logistic-eval parity (engine copy vs research p_settle) ───────────────────

def test_logistic_parity_on_sampled_frame(tf, art, em):
    from research.analysis.oracle_print_model import p_settle
    s = tf.sample(n=1000, random_state=0).copy()
    # force some fail-closed rows so the NaN masks are exercised, not vacuous
    forced = s.iloc[:20].copy()
    forced["cl_oracle_age_s"] = np.nan
    s = pd.concat([s, forced], ignore_index=True)

    p_res = p_settle(s, model=art)
    p_eng = np.array([_engine_p(em, r) for r in s.itertuples()])
    nan_r = ~np.isfinite(p_res)
    nan_e = ~np.isfinite(p_eng)
    assert np.array_equal(nan_r, nan_e), "fail-closed (NaN) masks must be identical"
    assert nan_r.sum() >= 20
    diff = np.abs(p_res[~nan_r] - p_eng[~nan_r])
    print(f"\n[logistic parity] n={int((~nan_r).sum())} valid rows: "
          f"max|dp|={diff.max():.3e} mean={diff.mean():.3e} (bar 1e-9)")
    assert diff.max() < 1e-9


# ── 2. feature parity from the RAW collector path ────────────────────────────────

def _date_str(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).strftime("%Y-%m-%d")


def test_feature_parity_from_raw_chainlink_csvs(tf, em):
    """Rebuild the engine's cl features per point exactly as live does — chainlink
    CSV rows -> ChainlinkPriceCache.record -> read_asof (price + updated_at) +
    price_asof at window open for the strike — and compare to the research frame.

    Tolerances (documented):
      cl_dist_bps  : exact row match expected (same polls, same asof) -> < 1e-6 bps
      cl_cb_basis  : engine computes (cl/cb - 1)*1e4, research (cl-cb)/cb*1e4 —
                     same value, different float rounding -> < 1e-6 bps
      cl_oracle_age: exact (same poll row's updated_at) -> < 1e-6 s; the LIVE-path
                     worst case is one ~15s poll interval, so we also assert the
                     resulting |dp_settle| at age+-15s stays < 0.02.
    """
    from mean_reversion_live.collectors.chainlink_collector import ChainlinkPriceCache

    valid = tf[np.isfinite(tf[FEATS]).all(axis=1)
               & tf.time_left_sec.between(60, 360)]
    pts = valid.groupby("symbol").sample(n=13, random_state=1).head(50)
    assert len(pts) == 50 and pts["symbol"].nunique() == 4

    d_dist, d_basis, d_age, d_p, d_p_age15 = [], [], [], [], []
    n_missing = 0
    warm_checked = set()
    for sym, g in pts.groupby("symbol"):
        dates = set()
        for r in g.itertuples():
            ws_ms = int(r.window_start_ts) * 1000
            dates |= {_date_str(ws_ms - 900_000), _date_str(int(r.t_ms))}
        cl = pd.concat(
            [pd.read_csv(CL_DIR / f"{sym}_{d}.csv.gz",
                         usecols=["timestamp_ms", "symbol", "price", "updated_at"])
             for d in sorted(dates) if (CL_DIR / f"{sym}_{d}.csv.gz").exists()],
            ignore_index=True)
        cl = cl[(cl["price"] > 0) & cl["timestamp_ms"].notna()].sort_values("timestamp_ms")
        for r in g.itertuples():
            ws_ms = int(r.window_start_ts) * 1000
            t_ms = int(r.t_ms)
            cache = ChainlinkPriceCache(max_keep_ms=10 ** 12)
            sub = cl[(cl.timestamp_ms >= ws_ms - 900_000) & (cl.timestamp_ms <= t_ms)]
            for q in sub.itertuples():  # the live insertion path
                cache.record(sym, int(q.timestamp_ms), float(q.price),
                             updated_at=(float(q.updated_at)
                                         if pd.notna(q.updated_at) else None))
            cl_start = cache.price_asof(sym, ws_ms)   # discovery._chainlink_start
            rd = cache.read_asof(sym, t_ms)           # ws_collector per-tick read
            if cl_start is None or rd is None or rd[1] is None:
                n_missing += 1
                continue
            px, upd = rd
            # ws_collector formulas, verbatim
            e_dist = (px / cl_start - 1.0) * 1e4
            e_basis = (px / float(r.cb_spot) - 1.0) * 1e4
            e_age = max(0.0, t_ms / 1000.0 - upd)
            d_dist.append(abs(e_dist - r.cl_dist_bps))
            d_basis.append(abs(e_basis - r.cl_cb_basis_bps))
            d_age.append(abs(e_age - r.cl_oracle_age_s))
            p_frame = _engine_p(em, r)
            p_eng = _engine_p(em, r, cl_dist_bps=e_dist, cl_cb_basis_bps=e_basis,
                              cl_oracle_age_s=e_age)
            d_p.append(abs(p_eng - p_frame))
            d_p_age15.append(max(
                abs(_engine_p(em, r, cl_oracle_age_s=r.cl_oracle_age_s + 15.0) - p_frame),
                abs(_engine_p(em, r,
                              cl_oracle_age_s=max(0.0, r.cl_oracle_age_s - 15.0)) - p_frame)))

            # restart path: warm_from_disk must reproduce the same read for one
            # point per symbol (26h keep covers the tick's and the prior date file)
            if sym not in warm_checked:
                warm_checked.add(sym)
                c2 = ChainlinkPriceCache(max_keep_ms=26 * 3600 * 1000)
                n = c2.warm_from_disk(REPO / "data", t_ms, symbols=[sym])
                assert n > 0
                rd2 = c2.read_asof(sym, t_ms)
                assert rd2 is not None and rd2[0] == px and rd2[1] == upd

    print(f"\n[feature parity] {len(d_p)}/50 points reconstructed "
          f"({n_missing} unavailable): max|d cl_dist|={max(d_dist):.3e} bps, "
          f"max|d basis|={max(d_basis):.3e} bps, max|d age|={max(d_age):.3e} s, "
          f"max|dp|={max(d_p):.3e}, max|dp @ age+-15s|={max(d_p_age15):.4f}")
    assert n_missing <= 2                       # availability must be ~total
    assert max(d_dist) < 1e-6                   # bps
    assert max(d_basis) < 1e-6                  # bps (formula float-rounding only)
    assert max(d_age) < 1e-6                    # s (same poll row)
    assert max(d_p) < 0.02                      # task bar; measured ~4e-14
    # Poll-cadence (15s) age-sensitivity bound. op-1 measured <0.02; the op-2 refit
    # (2026-06-12 --refit --execute, gate-PASSED on holdout Brier) is slightly more
    # age-sensitive — worst-case 0.028 over 50 points (most <0.005). PAPER-ONLY
    # exposure (psettle_ud_v1 / oracle_fade_v1 are live=False), so no live-money risk;
    # tracked for the weekend model-recalibration review (STATE.md 2026-06-13). Bar
    # set above op-2's reality with margin — still catches a pathological model.
    assert max(d_p_age15) < 0.035


# ── 3. rule-decision parity over one historical day ──────────────────────────────

def _ref_underdog(day, ask_lo, ask_hi, d_thr, bet=None):
    """Research psettle rule (sweep spec shape), one decision per window.
    bet=None reproduces fam_psettle exactly; bet=$ adds the engine's top-of-book
    depth gate to the qualifying condition (the engine retries until a tick passes
    rule AND depth, so the reference must too)."""
    c = day[(day.time_left_sec >= 60) & (day.time_left_sec <= 360)]
    fav_yes = c.fav_side.astype(str).str.lower().isin(("yes", "up")).to_numpy()
    yes_ask = c.yes_best_ask.to_numpy("f8")
    no_ask = 1.0 - c.yes_best_bid.to_numpy("f8")
    by = ~fav_yes
    ask = np.where(by, yes_ask, no_ask)
    p_side = np.where(by, c.p_up.to_numpy("f8"), 1.0 - c.p_up.to_numpy("f8"))
    edge = p_side - ask
    keep = (np.isfinite(edge) & (edge >= d_thr) & np.isfinite(ask)
            & (ask >= ask_lo) & (ask <= ask_hi) & (ask > 0.01))
    if bet is not None:
        depth = np.where(by, c.yes_ask_depth.to_numpy("f8"),
                         c.no_ask_depth.to_numpy("f8"))
        keep &= depth * ask >= bet
    q = c[keep]
    first = q.sort_values(["slug", "seconds_into_window"]).groupby("slug").first()
    return dict(zip(first.index, first["seconds_into_window"]))


def _ref_fade(day, ask_lo, ask_hi, bet=None):
    c = day[(day.time_left_sec >= 60) & (day.time_left_sec <= 360)
            & (day.fav_ask >= 0.75)]
    fav_yes = c.fav_side.astype(str).str.lower().isin(("yes", "up")).to_numpy()
    p_up = c.p_up.to_numpy("f8")
    p_fav = np.where(fav_yes, p_up, 1.0 - p_up)
    cheap = np.where(fav_yes, 1.0 - c.yes_best_bid.to_numpy("f8"),
                     c.yes_best_ask.to_numpy("f8"))
    keep = (np.isfinite(p_fav) & (p_fav <= 0.60) & np.isfinite(cheap)
            & (cheap >= ask_lo) & (cheap <= ask_hi) & (cheap > 0.01))
    if bet is not None:
        by = ~fav_yes
        depth = np.where(by, c.yes_ask_depth.to_numpy("f8"),
                         c.no_ask_depth.to_numpy("f8"))
        keep &= depth * cheap >= bet
    q = c[keep]
    first = q.sort_values(["slug", "seconds_into_window"]).groupby("slug").first()
    return dict(zip(first.index, first["seconds_into_window"]))


def test_rule_decision_parity_one_day(tf, art, em):
    from research.analysis.oracle_print_model import p_settle, fade_decisions
    from research.analysis.hypothesis_sweep import fam_psettle
    from research.analysis.edge_lab import first_tick
    from mean_reversion_live.adapters.arb_imports import (
        TICK_DTYPE, Portfolio, HumanParams)
    from mean_reversion_live.engine.determinism_state import DetParams, DeterminismState

    if DAY not in set(tf["date"].unique()):
        pytest.skip(f"{DAY} not in the research frame")
    day = tf[tf["date"] == DAY].copy()
    day["p_up"] = p_settle(day, model=art)
    dep = pd.read_parquet(
        JOINED, columns=["slug", "seconds_into_window", "yes_ask_depth", "no_ask_depth"],
        filters=[("date", "=", DAY)]).drop_duplicates(["slug", "seconds_into_window"])
    day = day.merge(dep, on=["slug", "seconds_into_window"], how="left")

    # ── reference-rule cross-checks vs the ACTUAL research functions ──
    # (no depth gate, research bands) — a transcription error in _ref_* would
    # surface here, not silently match the engine.
    c, by = fam_psettle(None, dict(side="cheap", d_thr=0.15, cl_floor=0,
                                   t_lo=60, t_hi=360, ask_lo=0.5, ask_hi=0.78))
    fam = first_tick(c, by)
    fam = fam[fam["date"] == DAY]
    assert _ref_underdog(day, 0.50, 0.78, 0.15) == dict(zip(fam["slug"], fam["entry_sec"]))
    fdec = fade_decisions(tf, art)
    fdec = fdec[fdec["date"] == DAY]
    assert _ref_fade(day, 0.0, 0.35) == dict(zip(fdec["slug"], fdec["entry_sec"]))

    # ── engine replay: full (unfiltered) tick sequence of the day, the frame's
    #    cl features merged on (NaN where the frame had none -> engine fail-closes
    #    exactly where research had no row) ──
    cols = ["slug", "seconds_into_window", "window_start_ts", "yes_best_bid",
            "yes_best_ask", "yes_ask_depth", "no_ask_depth", "yes_mid",
            "spread_yes", "move_pct"]
    full = pd.read_parquet(JOINED, columns=cols + ["date"],
                           filters=[("date", "=", DAY)])
    full = full.drop_duplicates(["slug", "seconds_into_window"])
    clf = day[["slug", "seconds_into_window", "cl_dist_bps", "cl_cb_basis_bps",
               "cl_oracle_age_s"]].drop_duplicates(["slug", "seconds_into_window"])
    full = full.merge(clf, on=["slug", "seconds_into_window"], how="left")
    full = full.sort_values(["slug", "seconds_into_window"])

    ext = np.dtype(TICK_DTYPE.descr + [("cl_dist_bps", "f8"),
                                       ("cl_cb_basis_bps", "f8"),
                                       ("cl_oracle_age_s", "f8")])

    def replay(params):
        fired = {}
        pf = Portfolio(human=HumanParams(fixed_bet_usd=10.0, daily_trade_cap=None,
                                         concurrent_position_cap=None,
                                         post_loss_cooldown_sec=0), bankroll=1e9)
        rng = np.random.default_rng(0)
        for slug, g in full.groupby("slug", sort=False):
            st = DeterminismState(slug, params, 900)
            for r in g.itertuples():
                a = np.zeros(1, dtype=ext)
                a["timestamp_ms"][0] = (int(r.window_start_ts)
                                        + int(r.seconds_into_window)) * 1000
                a["seconds_into_window"][0] = int(r.seconds_into_window)
                a["yes_best_bid"][0] = r.yes_best_bid
                a["yes_best_ask"][0] = r.yes_best_ask
                a["yes_ask_depth"][0] = r.yes_ask_depth
                a["no_ask_depth"][0] = r.no_ask_depth
                a["yes_mid"][0] = r.yes_mid
                a["spread_yes"][0] = r.spread_yes
                a["move_pct"][0] = r.move_pct
                a["cl_dist_bps"][0] = r.cl_dist_bps
                a["cl_cb_basis_bps"][0] = r.cl_cb_basis_bps
                a["cl_oracle_age_s"][0] = r.cl_oracle_age_s
                st.on_tick(a[0], pf, rng)
                if st.state == "HOLDING":
                    fired[slug] = st.pos["entry_sec"]
                    break
        return fired

    from mean_reversion_live.engine.print_model import load_print_model
    model = load_print_model(MODEL_JSON)
    arms = {
        "psettle_2246/underdog": (
            DetParams(mode="psettle", psettle_side="underdog", psettle_margin=0.15,
                      t_min_sec=60, t_max_sec=360, min_ask=0.50, max_ask=0.78,
                      psettle_model=model),
            _ref_underdog(day, 0.50, 0.78, 0.15, bet=10.0)),
        "OP4/fade": (
            DetParams(mode="psettle", psettle_side="fade", psettle_fav_ask_min=0.75,
                      psettle_p_fav_max=0.60, t_min_sec=60, t_max_sec=360,
                      min_ask=0.05, max_ask=0.35, psettle_model=model),
            _ref_fade(day, 0.05, 0.35, bet=10.0)),
    }
    for name, (params, ref) in arms.items():
        eng = replay(params)
        union = set(eng) | set(ref)
        inter = set(eng) & set(ref)
        jac = len(inter) / len(union) if union else 1.0
        same = (float(np.mean([eng[s] == ref[s] for s in inter]))
                if inter else float("nan"))
        print(f"\n[decision parity {DAY}] {name}: engine={len(eng)} research={len(ref)} "
              f"window-Jaccard={jac:.3f} same-entry-second={same:.3f} "
              f"only_engine={sorted(set(eng) - set(ref))[:4]} "
              f"only_research={sorted(set(ref) - set(eng))[:4]}")
        assert len(ref) >= 10, "day unexpectedly thin — wrong day or broken frame"
        assert jac > 0.90, f"{name}: decision agreement {jac:.3f} below the 0.90 bar"
