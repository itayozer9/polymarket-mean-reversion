"""External-inputs study XI1–XI5 (registered in docs/research/test_ledger.md
"External-inputs campaign (2026-06-11)" BEFORE any conditional/future number was
computed; report: docs/research/EXTERNAL_INPUTS_2026-06-11.md).

Tests genuinely NEW external inputs as (a) regime gates for the deployed edges and
(b) possible independent edges:

  XI1  Binance-derivative regime gates (cascade proxy / |dOI 5m| / funding extreme)
       on det_d12_dual_live, fav_disagree_live045, early_disagree, det_lwd_live.
  XI2  Cascade-conditional disagreement EDGE (fav_disagree broad + early_disagree).
  XI3  Scheduled US-macro event-window avoid-gate (data/research/events_calendar.csv).
  XI4  5m<->15m cross-book strike-monotonicity violation rule (last 300s overlap).
  XI5  E6 closeout (reversal>=50bps early-entry) on future = 2026-06-05..09.

Conventions: edge_lab.load_base() + local split relabel (future = 2026-06-05..09);
fills via rejudge_live_model.simulate_config under BOTH v2 and live_guarded at $5
(seed-0 CI headline, seeds 0-4 mean on live future legs); window-clustered CIs;
slug-set Jaccard vs the deployed decision frames.

The cascade proxy is a stated PROXY (Binance forced-order history is not public):
per-coin 1-min volume+range robust-z spikes from data/research/binance_1s, both
z >= the coin's dev+holdout q99.5 (thresholds frozen from d+h only).

Run (one pass per subcommand — the future side is revealed by that one run):
  uv run python -m research.analysis.external_inputs build      # caches
  uv run python -m research.analysis.external_inputs proxy      # proxy validation
  uv run python -m research.analysis.external_inputs xi1|xi2|xi3|xi4|xi5
Artifacts: data/research/external_inputs/
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re

import numpy as np
import pandas as pd

from research.analysis import rejudge_live_model as rj
from research.analysis.edge_lab import cl_outcomes, load_base
from research.lib.stats import window_clustered_bootstrap
from research.sim.fills_live import DEFAULT_PARAMS_PATH, load_params

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_DIR = os.path.join(REPO, "data", "research", "external_inputs")
DERIV_DIR = os.path.join(REPO, "data", "research", "binance_deriv")
B1S_DIR = os.path.join(REPO, "data", "research", "binance_1s")
LIVE_DIR = os.path.join(REPO, "data", "live")
CAL_CSV = os.path.join(REPO, "data", "research", "events_calendar.csv")

FUTURE_START = "2026-06-05"          # LC2 campaign convention
DH_LO, DH_HI = "2026-05-23", "2026-06-04"   # dev+holdout dates under the override
STAKE = 5.0
SEEDS = (0, 1, 2, 3, 4)
SYMS = ("btc", "eth", "sol", "xrp")
CASCADE_Q = 0.995                    # registered quantile for proxy thresholds
CASCADE_LOOKBACK_S = 180             # registered primary lookback
# Amended joint-cascade regime (ledger amendment 2026-06-11, pre-EV-reveal):
# >=3 of 4 coins simultaneously at vol_z >= d+h q95 AND rng_z >= d+h q90.
JOINT_K = 3
JOINT_QV, JOINT_QR = 0.95, 0.90
OI_MAX_STALE_S = 600
FUNDING_MAX_STALE_S = 9 * 3600
REGIME_Q = 0.80                      # registered top-quintile for OI / funding

# XI1 gated set + XI2 edge set (burst_cap conventions for the non-CONFIGS two)
EXTRA_CONFIGS = {
    "fav_disagree_live045": dict(mode="disagree", t_lo=120, t_hi=360, dist_min=10.0,
                                 ask_lo=0.05, ask_hi=0.45),
    "early_disagree": dict(mode="disagree", t_lo=450, t_hi=900, dist_min=10.0,
                           ask_lo=0.30, ask_hi=0.45),
}
GATED = ("det_d12_dual_live", "fav_disagree_live045", "early_disagree", "det_lwd_live")
EDGE_SET = ("fav_disagree", "early_disagree")
REGIMES = ("cascade_recent", "cascade_joint_recent", "hi_doi", "funding_extreme")
KNOWN_FOR_JACCARD = ("det_lwd_live", "det_d12_wide_v1", "det_d12_dual_live",
                     "fav_disagree", "fav_momentum", "fav_lowvol", "fav_deepdown",
                     "fav_disagree_live045", "early_disagree")

# XI4 registered grid + chooser (d+h only; future-blind by construction)
XI4_MARGINS = (0.03, 0.05, 0.10)
XI4_GAPS_BPS = (2.0, 5.0)
XI4_CEILING = 0.90
XI4_T_LO, XI4_T_HI = 600, 895        # seconds_into_window of the overlap (entry side)
XI4_MIN_REF_NOTIONAL = 1.0           # $ on the referenced 5m quote

CASC_PARQ = os.path.join(OUT_DIR, "cascade_minutes.parquet")
THRESH_JSON = os.path.join(OUT_DIR, "cascade_thresholds.json")
T5_PARQ = os.path.join(OUT_DIR, "ticks5m_coterminal.parquet")


# ==========================================================================
# pure helpers (unit-tested in tests/research/test_external_inputs.py)
# ==========================================================================

def relabel_splits(df: pd.DataFrame, future_start: str = FUTURE_START) -> pd.DataFrame:
    """Campaign split override: old-future days < future_start -> holdout.
    Shallow copy; replaces only the split column."""
    d = df["date"].astype(str)
    split = df["split"].astype(str).to_numpy().copy()
    split[(split == "future") & (d < future_start).to_numpy()] = "holdout"
    out = df.copy(deep=False)
    out["split"] = split
    return out


def minute_bars(df1s: pd.DataFrame) -> pd.DataFrame:
    """1s klines (ts_ms, high, low, close, volume) -> per-minute bars:
    minute_ts (epoch s, minute open), vol, rng_bps, close."""
    if df1s.empty:
        return pd.DataFrame(columns=["minute_ts", "vol", "rng_bps", "close"])
    mt = (df1s["ts_ms"].astype("int64") // 60_000) * 60
    g = df1s.groupby(mt.rename("minute_ts"))
    out = pd.DataFrame({
        "vol": g["volume"].sum(), "high": g["high"].max(),
        "low": g["low"].min(), "close": g["close"].last(),
    }).reset_index()
    out["rng_bps"] = (out["high"] - out["low"]) / out["close"] * 1e4
    return out[["minute_ts", "vol", "rng_bps", "close"]]


def robust_z(x: pd.Series, window: int = 120, min_periods: int = 60) -> pd.Series:
    """Causal robust z: (x - rolling-median)/(1.4826*rolling-MAD), both stats on
    the PRIOR `window` values (shift 1 => x[t] never in its own baseline).
    MAD approximated as rolling-median of |x_prev - med_at_its_time| (each past
    point deviates from ITS OWN trailing median) — monotone-equivalent for the
    quantile-calibrated thresholds we use. Scale floored at 5% of |median| so a
    0-MAD (locally constant) baseline cannot silently suppress spikes."""
    prev = x.shift(1)
    med = prev.rolling(window, min_periods=min_periods).median()
    dev = (x - med).abs().shift(1)
    mad = dev.rolling(window, min_periods=min_periods).median()
    scale = np.maximum(1.4826 * mad, 0.05 * med.abs()).replace(0.0, np.nan)
    return (x - med) / scale


def tag_recent(entry_epoch: np.ndarray, event_ts_sorted: np.ndarray,
               lookback_s: float) -> np.ndarray:
    """True where any event ts lies in [entry - lookback_s, entry]."""
    entry_epoch = np.asarray(entry_epoch, dtype="f8")
    ev = np.asarray(event_ts_sorted, dtype="f8")
    out = np.zeros(len(entry_epoch), dtype=bool)
    if len(ev) == 0:
        return out
    idx = np.searchsorted(ev, entry_epoch, side="right") - 1
    ok = idx >= 0
    out[ok] = (entry_epoch[ok] - ev[idx[ok]]) <= lookback_s
    return out


def tag_between(entry_epoch: np.ndarray, lo_epoch: np.ndarray,
                event_ts_sorted: np.ndarray) -> np.ndarray:
    """True where any event ts lies in [lo, entry] (per-row lo)."""
    entry_epoch = np.asarray(entry_epoch, dtype="f8")
    lo_epoch = np.asarray(lo_epoch, dtype="f8")
    ev = np.asarray(event_ts_sorted, dtype="f8")
    if len(ev) == 0:
        return np.zeros(len(entry_epoch), dtype=bool)
    n_hi = np.searchsorted(ev, entry_epoch, side="right")
    n_lo = np.searchsorted(ev, lo_epoch, side="left")
    return (n_hi - n_lo) > 0


def asof_state(entry_epoch: np.ndarray, state_ts_sorted: np.ndarray,
               state_val: np.ndarray, max_stale_s: float) -> np.ndarray:
    """Latest state value at or before entry; NaN if none or staler than
    max_stale_s."""
    entry_epoch = np.asarray(entry_epoch, dtype="f8")
    ts = np.asarray(state_ts_sorted, dtype="f8")
    val = np.asarray(state_val, dtype="f8")
    out = np.full(len(entry_epoch), np.nan)
    if len(ts) == 0:
        return out
    idx = np.searchsorted(ts, entry_epoch, side="right") - 1
    ok = (idx >= 0)
    ok[ok] = (entry_epoch[ok] - ts[idx[ok]]) <= max_stale_s
    out[ok] = val[idx[ok]]
    return out


def tag_events(entry_epoch: np.ndarray, cal: pd.DataFrame,
               tier_max: int | None = None) -> np.ndarray:
    """True where entry lies inside any [window_start, window_end] calendar row."""
    entry_epoch = np.asarray(entry_epoch, dtype="f8")
    c = cal if tier_max is None else cal[cal["tier"] <= tier_max]
    out = np.zeros(len(entry_epoch), dtype=bool)
    for _, r in c.iterrows():
        out |= ((entry_epoch >= float(r["window_start_epoch_s"]))
                & (entry_epoch <= float(r["window_end_epoch_s"])))
    return out


def monotonic_signal(t: pd.DataFrame, margin: float, gap_bps: float) -> pd.DataFrame:
    """XI4 rule on a joined 15m/5m tick frame. Requires columns:
    gap_bps (K5 vs K15, bps), yes_best_ask/yes_best_bid (15m),
    yes5_bid/yes5_ask, yes5_bid_usd/yes5_ask_usd (5m ref-quote notional).
    UP:   K5 above K15 by >= gap AND yes15_ask + margin <= yes5_bid -> buy 15m UP.
    DOWN: K5 below K15 by >= gap AND yes5_ask + margin <= yes15_bid -> buy 15m DOWN.
    Returns rows with buy_yes + entry_ask (ceiling applied by the fill sim)."""
    up = ((t["gap_bps"] >= gap_bps)
          & (t["yes_best_ask"] + margin <= t["yes5_bid"])
          & (t["yes5_bid_usd"] >= XI4_MIN_REF_NOTIONAL))
    dn = ((t["gap_bps"] <= -gap_bps)
          & (t["yes5_ask"] + margin <= t["yes_best_bid"])
          & (t["yes5_ask_usd"] >= XI4_MIN_REF_NOTIONAL))
    sig = t[up | dn].copy()
    sig["buy_yes"] = up[up | dn].to_numpy()
    sig["entry_ask"] = np.where(sig["buy_yes"], sig["yes_best_ask"],
                                1.0 - sig["yes_best_bid"])
    return sig


def joint_cascade_ts(casc: pd.DataFrame, k: int = JOINT_K, qv: float = JOINT_QV,
                     qr: float = JOINT_QR, dh_lo: str = DH_LO,
                     dh_hi: str = DH_HI) -> np.ndarray:
    """Sorted minute_ts where >= k coins are simultaneously 'hot' (vol_z >= their
    d+h q-vol AND rng_z >= their d+h q-rng). The amended macro-cascade regime."""
    dh = casc[(casc["date"] >= dh_lo) & (casc["date"] <= dh_hi)]
    hot = np.zeros(len(casc), dtype=bool)
    for sym, g in dh.groupby("symbol"):
        zv, zr = g["vol_z"].quantile(qv), g["rng_z"].quantile(qr)
        m = (casc["symbol"] == sym).to_numpy()
        hot[m] = ((casc.loc[m, "vol_z"] >= zv) & (casc.loc[m, "rng_z"] >= zr)).to_numpy()
    cnt = pd.Series(hot).groupby(casc["minute_ts"].to_numpy()).sum()
    return np.sort(cnt[cnt >= k].index.to_numpy()).astype("f8")


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return round(len(a & b) / len(a | b), 3)


def _ci_block(pnl: np.ndarray, slugs: np.ndarray, n: int = 2000) -> dict:
    pnl = np.asarray(pnl, dtype="f8")
    if len(pnl) < 3:
        return dict(n=int(len(pnl)), ev=None, lo=None, hi=None, total=None)
    lo, _, hi = window_clustered_bootstrap(pnl, slugs, n=n)
    return dict(n=int(len(pnl)), ev=round(float(pnl.mean()), 3),
                lo=round(float(lo), 3), hi=round(float(hi), 3),
                total=round(float(pnl.sum()), 1))


# ==========================================================================
# external-state loaders / cached builders
# ==========================================================================

def _b1s_dates(sym: str) -> list[str]:
    out = []
    for p in glob.glob(os.path.join(B1S_DIR, f"{sym}_*.parquet")):
        m = re.search(r"_(\d{4}-\d{2}-\d{2})\.parquet$", p)
        if m:
            out.append(m.group(1))
    return sorted(out)


def build_cascade_minutes(force: bool = False) -> pd.DataFrame:
    """Per (symbol, minute): vol/rng robust-z + cascade flag. Thresholds = per-coin
    d+h (05-23..06-04) q99.5 of each z (registered; 05-22 used as warmup only)."""
    if os.path.exists(CASC_PARQ) and not force:
        return pd.read_parquet(CASC_PARQ)
    os.makedirs(OUT_DIR, exist_ok=True)
    frames, thresholds = [], {}
    for sym in SYMS:
        days = []
        for d in _b1s_dates(sym):
            days.append(pd.read_parquet(os.path.join(B1S_DIR, f"{sym}_{d}.parquet")))
        if not days:
            continue
        m = minute_bars(pd.concat(days, ignore_index=True).sort_values("ts_ms"))
        m["vol_z"] = robust_z(m["vol"])
        m["rng_z"] = robust_z(m["rng_bps"])
        m["symbol"] = sym
        m["date"] = pd.to_datetime(m["minute_ts"], unit="s", utc=True).dt.strftime(
            "%Y-%m-%d")
        dh = m[(m["date"] >= DH_LO) & (m["date"] <= DH_HI)]
        zv = float(dh["vol_z"].quantile(CASCADE_Q))
        zr = float(dh["rng_z"].quantile(CASCADE_Q))
        thresholds[sym] = dict(z_vol=round(zv, 3), z_rng=round(zr, 3),
                               q=CASCADE_Q, fit_dates=[DH_LO, DH_HI])
        m["cascade"] = (m["vol_z"] >= zv) & (m["rng_z"] >= zr)
        frames.append(m)
    out = pd.concat(frames, ignore_index=True)
    out.to_parquet(CASC_PARQ, index=False)
    with open(THRESH_JSON, "w") as f:
        json.dump(thresholds, f, indent=2)
    return out


def load_oi() -> pd.DataFrame:
    """Per (symbol): ts_s, oi, doi5_pct (5m pct change)."""
    frames = []
    for sym in SYMS:
        files = sorted(glob.glob(os.path.join(DERIV_DIR, f"oi_{sym}_*.parquet")))
        if not files:
            continue
        df = pd.concat([pd.read_parquet(p) for p in files], ignore_index=True)
        df = df.drop_duplicates("ts_ms").sort_values("ts_ms")
        df["ts_s"] = df["ts_ms"] // 1000
        df["doi5_pct"] = df["sum_open_interest"].pct_change()
        # invalidate pct-change across gaps > 1 bucket
        gap = df["ts_ms"].diff() > 305_000
        df.loc[gap, "doi5_pct"] = np.nan
        df["symbol"] = sym
        frames.append(df[["symbol", "ts_s", "sum_open_interest", "doi5_pct"]])
    return pd.concat(frames, ignore_index=True)


def load_funding() -> pd.DataFrame:
    frames = []
    for sym in SYMS:
        p = os.path.join(DERIV_DIR, f"funding_{sym}.parquet")
        if not os.path.exists(p):
            continue
        df = pd.read_parquet(p)
        df["ts_s"] = df["funding_time_ms"] // 1000
        df["symbol"] = sym
        frames.append(df[["symbol", "ts_s", "funding_rate"]])
    return pd.concat(frames, ignore_index=True)


_T5_COLS = ["market_slug", "symbol", "window_start_ts", "seconds_into_window",
            "yes_best_bid", "yes_best_ask", "yes_bid_depth", "yes_ask_depth",
            "start_price"]


def build_ticks5m_coterminal(force: bool = False) -> pd.DataFrame:
    """5m ticks of windows CO-TERMINAL with a 15m window (start % 900 == 600),
    from data/live/*.csv.gz (read-only), clean-window dates. Cached."""
    if os.path.exists(T5_PARQ) and not force:
        return pd.read_parquet(T5_PARQ)
    os.makedirs(OUT_DIR, exist_ok=True)
    frames = []
    for sym in SYMS:
        for p in sorted(glob.glob(os.path.join(LIVE_DIR, f"{sym}_*.csv.gz"))):
            m = re.search(r"_(\d{4}-\d{2}-\d{2})\.csv\.gz$", p)
            if not m or not (DH_LO <= m.group(1) <= "2026-06-09"):
                continue
            try:
                df = pd.read_csv(p, usecols=_T5_COLS)
            except Exception:
                continue
            df = df[df["market_slug"].str.contains("-updown-5m-", na=False)]
            df = df[(df["window_start_ts"] % 900) == 600]
            if not df.empty:
                frames.append(df)
    out = pd.concat(frames, ignore_index=True)
    # one strike per 5m window = first nonzero captured start_price
    k5 = (out[out["start_price"] > 0]
          .sort_values("seconds_into_window")
          .groupby("market_slug", as_index=False)["start_price"].first()
          .rename(columns={"start_price": "k5"}))
    out = out.merge(k5, on="market_slug", how="inner")
    out = out.drop_duplicates(["market_slug", "seconds_into_window"], keep="last")
    out.to_parquet(T5_PARQ, index=False)
    return out


# ==========================================================================
# decision frames + simulation with regime columns
# ==========================================================================

def _register_extra_configs() -> None:
    for name, cfg in EXTRA_CONFIGS.items():
        rj.CONFIGS.setdefault(name, cfg)


def decisions(name: str, b: pd.DataFrame) -> pd.DataFrame:
    _register_extra_configs()
    dec = rj.decisions_for(name, b)
    if rj.CONFIGS[name].get("oracle_gate") == "agree" and not dec.empty:
        dec = rj._apply_dual_gate(dec, b)
    dec = dec.copy()
    if "window_start_ts" not in dec.columns:   # _apply_dual_gate drops it
        dec["window_start_ts"] = (dec["slug"].str.rsplit("-", n=1).str[-1]
                                  .astype("int64"))
    dec["entry_epoch"] = dec["window_start_ts"].astype("f8") + dec["entry_sec"].astype("f8")
    return dec


def annotate_regimes(dec: pd.DataFrame, casc: pd.DataFrame, oi: pd.DataFrame,
                     fund: pd.DataFrame, cal: pd.DataFrame,
                     oi_thr: dict, fund_thr: dict) -> pd.DataFrame:
    """Add the frozen regime columns to a decision frame."""
    d = dec.copy()
    d["cascade_recent"] = False
    d["cascade_in_window"] = False
    d["doi5_pct"] = np.nan
    d["funding_rate"] = np.nan
    any_casc = np.sort(casc.loc[casc["cascade"], "minute_ts"].unique()).astype("f8")
    d["cascade_any_recent"] = tag_recent(d["entry_epoch"].to_numpy(), any_casc,
                                         CASCADE_LOOKBACK_S)
    d["cascade_joint_recent"] = tag_recent(d["entry_epoch"].to_numpy(),
                                           joint_cascade_ts(casc),
                                           CASCADE_LOOKBACK_S)
    for sym in d["symbol"].unique():
        m = (d["symbol"] == sym).to_numpy()
        ct = np.sort(casc.loc[(casc["symbol"] == sym) & casc["cascade"],
                              "minute_ts"].to_numpy()).astype("f8")
        ee = d.loc[m, "entry_epoch"].to_numpy()
        ws = d.loc[m, "window_start_ts"].to_numpy("f8")
        d.loc[m, "cascade_recent"] = tag_recent(ee, ct, CASCADE_LOOKBACK_S)
        d.loc[m, "cascade_in_window"] = tag_between(ee, ws, ct)
        o = oi[oi["symbol"] == sym]
        d.loc[m, "doi5_pct"] = asof_state(ee, o["ts_s"].to_numpy(),
                                          o["doi5_pct"].to_numpy(), OI_MAX_STALE_S)
        f = fund[fund["symbol"] == sym]
        d.loc[m, "funding_rate"] = asof_state(ee, f["ts_s"].to_numpy(),
                                              f["funding_rate"].to_numpy(),
                                              FUNDING_MAX_STALE_S)
        d.loc[m, "hi_doi"] = np.abs(d.loc[m, "doi5_pct"]) >= oi_thr[sym]
        d.loc[m, "funding_extreme"] = np.abs(d.loc[m, "funding_rate"]) >= fund_thr[sym]
    d.loc[d["doi5_pct"].isna(), "hi_doi"] = np.nan
    d.loc[d["funding_rate"].isna(), "funding_extreme"] = np.nan
    d["in_event"] = tag_events(d["entry_epoch"].to_numpy(), cal)
    d["in_event_t1"] = tag_events(d["entry_epoch"].to_numpy(), cal, tier_max=1)
    return d


def fit_regime_thresholds(oi: pd.DataFrame, fund: pd.DataFrame) -> tuple[dict, dict]:
    """Per-coin d+h q80 of |dOI5| / |funding| (registered; fit on the d+h DATES
    of the series themselves)."""
    oi_thr, fund_thr = {}, {}
    for sym in SYMS:
        o = oi[oi["symbol"] == sym].copy()
        od = pd.to_datetime(o["ts_s"], unit="s", utc=True).dt.strftime("%Y-%m-%d")
        sel = o[(od >= DH_LO) & (od <= DH_HI)]["doi5_pct"].abs().dropna()
        oi_thr[sym] = float(sel.quantile(REGIME_Q))
        f = fund[fund["symbol"] == sym].copy()
        fd = pd.to_datetime(f["ts_s"], unit="s", utc=True).dt.strftime("%Y-%m-%d")
        fsel = f[(fd >= DH_LO) & (fd <= DH_HI)]["funding_rate"].abs().dropna()
        fund_thr[sym] = float(fsel.quantile(REGIME_Q))
    return oi_thr, fund_thr


def sim_with_cols(dec: pd.DataFrame, cfg: dict, mode: str, params, seed: int,
                  cols: list[str]) -> pd.DataFrame:
    """simulate_config + merge requested decision columns back. Columns already
    present in the sim output (e.g. split) are not re-merged."""
    t = rj.simulate_config(dec, cfg, mode, params, STAKE, seed=seed)
    if t.empty:
        return t
    keep = ["slug"] + [c for c in cols if c in dec.columns and c not in t.columns]
    return t.merge(dec[keep].drop_duplicates("slug"), on="slug", how="left")


def _split_dh_fut(f: pd.DataFrame) -> dict:
    return {"dh": f[f["split"].isin(("dev", "holdout"))], "future": f[f["split"] == "future"]}


# ==========================================================================
# XI1 — deriv-regime gates on the deployed set
# ==========================================================================

def run_xi1(b, casc, oi, fund, cal, oi_thr, fund_thr, params) -> list[dict]:
    out = []
    regime_cols = list(REGIMES) + ["cascade_any_recent", "cascade_in_window",
                                   "in_event", "in_event_t1", "window_start_ts"]
    for name in GATED:
        cfg = rj.CONFIGS[name]
        dec = annotate_regimes(decisions(name, b), casc, oi, fund, cal, oi_thr, fund_thr)
        dec_r = relabel_splits(dec)
        sims = {"v2": sim_with_cols(dec_r, cfg, "v2", params, 0, regime_cols + ["split"])}
        for s in SEEDS:
            sims[f"lg{s}"] = sim_with_cols(dec_r, cfg, "live_guarded", params, s,
                                           regime_cols + ["split"])
        for reg in REGIMES + ("cascade_any_recent", "in_event"):
            share = float(dec_r[reg].fillna(False).astype(bool).mean())
            rec = dict(test="XI1", config=name, regime=reg,
                       registered_gate=reg in REGIMES,
                       share_decisions=round(share, 4),
                       n_decisions=int(len(dec_r)))
            for mname in ("v2", "lg0"):
                f = sims[mname]
                f = f[f["filled"] == 1] if not f.empty else f
                blocks = {}
                if not f.empty:
                    inr = f[f[reg].fillna(False).astype(bool)]
                    outr = f[~f[reg].fillna(False).astype(bool) & f[reg].notna()]
                    for tag, sub in (("in", inr), ("out", outr)):
                        sp = _split_dh_fut(sub)
                        blocks[tag] = {k: _ci_block(v["pnl"].to_numpy(), v["slug"].to_numpy())
                                       for k, v in sp.items()}
                rec[mname] = blocks
            # seeds 0-4 future in/out means (live)
            fut_in, fut_out = [], []
            for s in SEEDS:
                f = sims[f"lg{s}"]
                if f.empty:
                    continue
                f = f[(f["filled"] == 1) & (f["split"] == "future")]
                i = f[f[reg].fillna(False).astype(bool)]["pnl"]
                o = f[~f[reg].fillna(False).astype(bool) & f[reg].notna()]["pnl"]
                fut_in.append(float(i.mean()) if len(i) else np.nan)
                fut_out.append(float(o.mean()) if len(o) else np.nan)
            rec["lg_future_in_seeds"] = [round(x, 3) if np.isfinite(x) else None for x in fut_in]
            rec["lg_future_out_seeds"] = [round(x, 3) if np.isfinite(x) else None for x in fut_out]
            # registered XI1 verdict legs
            dh_in = rec.get("lg0", {}).get("in", {}).get("dh", {})
            dh_out = rec.get("lg0", {}).get("out", {}).get("dh", {})
            leg1 = (dh_in.get("ev") is not None and dh_out.get("ev") is not None
                    and dh_in["ev"] < 0 and dh_in["hi"] < dh_out["ev"])
            mi = np.nanmean([x for x in fut_in if x is not None] or [np.nan])
            mo = np.nanmean([x for x in fut_out if x is not None] or [np.nan])
            leg2 = bool(np.isfinite(mi) and np.isfinite(mo) and mi < mo)
            leg3 = share >= 0.05 and (dh_in.get("n") or 0) >= 10
            rec["gate_legs"] = dict(dh_separation=bool(leg1), future_agrees=leg2,
                                    material=bool(leg3))
            rec["recommend_gate"] = bool(leg1 and leg2 and leg3)
            out.append(rec)
        # descriptive: worst live window-ts groups + their cascade flags
        f0 = sims["lg0"]
        if not f0.empty:
            f0 = f0[f0["filled"] == 1]
            grp = (f0.groupby("window_start_ts")
                   .agg(pnl=("pnl", "sum"), n=("pnl", "size"),
                        casc=("cascade_recent", "max"),
                        casc_any=("cascade_any_recent", "max"))
                   .sort_values("pnl").head(5).reset_index())
            out.append(dict(test="XI1_worst_groups", config=name,
                            rows=grp.round(3).to_dict("records")))
    return out


# ==========================================================================
# XI2 — cascade-conditional disagreement edge
# ==========================================================================

def run_xi2(b, casc, oi, fund, cal, oi_thr, fund_thr, params) -> list[dict]:
    out = []
    casc_regs = ("cascade_recent", "cascade_joint_recent")
    for name in EDGE_SET:
        cfg = rj.CONFIGS[name]
        dec = annotate_regimes(decisions(name, b), casc, oi, fund, cal, oi_thr, fund_thr)
        dec_r = relabel_splits(dec)
        sims = []
        for s in SEEDS:
            f = sim_with_cols(dec_r, cfg, "live_guarded", params, s,
                              ["split"] + list(casc_regs))
            sims.append(f[f["filled"] == 1])
        for reg in casc_regs:
            rec = dict(test="XI2", config=name, regime=reg,
                       n_decisions=int(len(dec_r)),
                       n_casc_decisions=int(dec_r[reg].sum()))
            evs_sub, evs_all, n_fills_fut = [], [], []
            ci0 = None
            for s, f in zip(SEEDS, sims):
                fut = f[f["split"] == "future"]
                sub = fut[fut[reg].fillna(False).astype(bool)]
                evs_all.append(float(fut["pnl"].mean()) if len(fut) else np.nan)
                evs_sub.append(float(sub["pnl"].mean()) if len(sub) else np.nan)
                n_fills_fut.append(int(len(sub)))
                if s == 0:
                    ci0 = _ci_block(sub["pnl"].to_numpy(), sub["slug"].to_numpy())
                    dh = f[f["split"] != "future"]
                    dhs = dh[dh[reg].fillna(False).astype(bool)]
                    rec["dh_sub"] = _ci_block(dhs["pnl"].to_numpy(), dhs["slug"].to_numpy())
                    rec["dh_all"] = _ci_block(dh["pnl"].to_numpy(), dh["slug"].to_numpy())
            rec["future_sub_seed0_ci"] = ci0
            rec["future_sub_seeds_ev"] = [round(x, 3) if np.isfinite(x) else None
                                          for x in evs_sub]
            rec["future_all_seeds_ev"] = [round(x, 3) if np.isfinite(x) else None
                                          for x in evs_all]
            m_sub = float(np.nanmean(evs_sub)) if evs_sub else np.nan
            m_all = float(np.nanmean(evs_all)) if evs_all else np.nan
            rec["edge_legs"] = dict(
                uplift=bool(np.isfinite(m_sub) and np.isfinite(m_all)
                            and m_sub >= m_all + 0.25),
                ci_pos=bool(ci0 and ci0.get("lo") is not None and ci0["lo"] > 0),
                n_ge20=bool(min(n_fills_fut or [0]) >= 20))
            rec["edge_worthy"] = all(rec["edge_legs"].values())
            out.append(rec)
    return out


# ==========================================================================
# XI3 — event-window gate
# ==========================================================================

def run_xi3(b, casc, oi, fund, cal, oi_thr, fund_thr, params) -> dict:
    pooled = {"v2": [], "lg0": []}
    per_cfg = []
    for name in GATED:
        cfg = rj.CONFIGS[name]
        dec = annotate_regimes(decisions(name, b), casc, oi, fund, cal, oi_thr, fund_thr)
        dec_r = relabel_splits(dec)
        for mname, mode, seed in (("v2", "v2", 0), ("lg0", "live_guarded", 0)):
            f = sim_with_cols(dec_r, cfg, mode, params, seed,
                              ["split", "in_event", "in_event_t1", "entry_epoch"])
            f = f[f["filled"] == 1].copy()
            f["config"] = name
            pooled[mname].append(f)
        d_ev = dec_r[dec_r["in_event"]]
        per_cfg.append(dict(config=name, n_dec=int(len(dec_r)),
                            n_dec_in_event=int(len(d_ev)),
                            n_dec_in_event_t1=int(dec_r["in_event_t1"].sum())))
    out = dict(test="XI3", per_config_decisions=per_cfg)
    for mname in ("v2", "lg0"):
        allf = pd.concat(pooled[mname], ignore_index=True)
        res = {}
        for tag, sub in (("in_event", allf[allf["in_event"]]),
                         ("out_event", allf[~allf["in_event"]]),
                         ("in_event_t1", allf[allf["in_event_t1"]])):
            res[tag] = {
                "all": _ci_block(sub["pnl"].to_numpy(), sub["slug"].to_numpy()),
                "dh": _ci_block(sub[sub["split"] != "future"]["pnl"].to_numpy(),
                                sub[sub["split"] != "future"]["slug"].to_numpy()),
                "future": _ci_block(sub[sub["split"] == "future"]["pnl"].to_numpy(),
                                    sub[sub["split"] == "future"]["slug"].to_numpy()),
            }
        # knife concentration: near-total losses (pnl <= -0.8*stake)
        knives = allf[allf["pnl"] <= -0.8 * STAKE]
        res["knife_share_in_event"] = (round(float(knives["in_event"].mean()), 4)
                                       if len(knives) else None)
        res["fill_share_in_event"] = round(float(allf["in_event"].mean()), 4)
        res["n_knives"] = int(len(knives))
        out[mname] = res
    dh_in = out["lg0"]["in_event"]["dh"]
    fut_in = out["lg0"]["in_event"]["future"]
    fut_out = out["lg0"]["out_event"]["future"]
    leg1 = (dh_in.get("ev") is not None and dh_in["ev"] < -0.5
            and (dh_in.get("hi") or 1) < 0)
    leg2 = (fut_in.get("ev") is not None and fut_out.get("ev") is not None
            and fut_in["ev"] < fut_out["ev"])
    leg3 = (out["lg0"]["in_event"]["all"].get("n") or 0) >= 15
    out["gate_legs"] = dict(dh_negative=bool(leg1), future_agrees=bool(leg2),
                            n_ge15=bool(leg3))
    out["recommend_gate"] = bool(leg1 and leg2 and leg3)
    return out


# ==========================================================================
# XI4 — 5m<->15m monotonicity rule
# ==========================================================================

def build_xi4_join(b: pd.DataFrame) -> pd.DataFrame:
    """15m ticks in the overlap joined to the co-terminal 5m book at the same
    second. Adds gap_bps, yes5_* columns."""
    t5 = build_ticks5m_coterminal()
    t5 = t5.rename(columns={"yes_best_bid": "yes5_bid", "yes_best_ask": "yes5_ask",
                            "yes_bid_depth": "yes5_bid_sh", "yes_ask_depth": "yes5_ask_sh",
                            "window_start_ts": "w5", "seconds_into_window": "s5"})
    sane5 = (t5["yes5_bid"].between(0.01, 0.99) & t5["yes5_ask"].between(0.01, 0.99)
             & (t5["yes5_ask"] > t5["yes5_bid"])
             & ((t5["yes5_ask"] - t5["yes5_bid"]) <= 0.15))
    t5 = t5[sane5]
    cols = ["slug", "symbol", "date", "split", "window_start_ts",
            "seconds_into_window", "time_left_sec", "yes_best_bid", "yes_best_ask",
            "yes_mid", "book_healthy", "start_price"]
    t15 = b[(b["seconds_into_window"] >= XI4_T_LO)
            & (b["seconds_into_window"] <= XI4_T_HI)
            & b["book_healthy"].fillna(False).astype(bool)
            & (b["start_price"] > 0)][cols].copy()
    t15["w5"] = t15["window_start_ts"] + 600
    t15["s5"] = t15["seconds_into_window"] - 600
    j = t15.merge(
        t5[["symbol", "w5", "s5", "yes5_bid", "yes5_ask", "yes5_bid_sh",
            "yes5_ask_sh", "k5"]],
        on=["symbol", "w5", "s5"], how="inner")
    j["gap_bps"] = (j["k5"] - j["start_price"]) / j["start_price"] * 1e4
    # $ notional displayed behind the referenced 5m quote (opinion-size floor)
    j["yes5_bid_usd"] = j["yes5_bid"] * j["yes5_bid_sh"]
    j["yes5_ask_usd"] = j["yes5_ask"] * j["yes5_ask_sh"]
    return j


def run_xi4(b, params) -> dict:
    j = build_xi4_join(b)
    j = relabel_splits(j)
    cl = cl_outcomes()
    out = dict(test="XI4", n_join_ticks=int(len(j)),
               n_windows_with_overlap=int(j["slug"].nunique()))

    # descriptive: genuine mid-based conflicts — who is right?
    mid5 = (j["yes5_bid"] + j["yes5_ask"]) / 2.0
    conf_up = (j["gap_bps"] >= 2.0) & (mid5 > j["yes_mid"])
    conf_dn = (j["gap_bps"] <= -2.0) & (mid5 < j["yes_mid"])
    conf = j[conf_up | conf_dn].copy()
    conf["implied_up"] = conf_up[conf_up | conf_dn].to_numpy()
    first = (conf.sort_values(["slug", "seconds_into_window"])
             .groupby("slug", as_index=False).first()
             .merge(cl, on="slug", how="inner"))
    if len(first):
        imp_right = np.where(first["implied_up"], first["cl_up"] == 1,
                             first["cl_up"] == 0)
        fav_up = first["yes_mid"] >= 0.5
        fav_right = np.where(fav_up, first["cl_up"] == 1, first["cl_up"] == 0)
        out["conflict_windows"] = int(len(first))
        out["p_5m_implied_right"] = round(float(np.mean(imp_right)), 3)
        out["p_15m_fav_right"] = round(float(np.mean(fav_right)), 3)
        by_split = {}
        for sp in ("dev", "holdout", "future"):
            m = (first["split"] == sp).to_numpy()
            if m.sum() > 0:
                by_split[sp] = dict(n=int(m.sum()),
                                    p_5m=round(float(np.mean(imp_right[m])), 3),
                                    p_15m_fav=round(float(np.mean(fav_right[m])), 3))
        out["conflict_by_split"] = by_split

    # registered rule grid; chooser is d+h-only (future-blind by construction)
    cfg = dict(ask_hi=XI4_CEILING)
    variants = []
    known = _known_slug_sets(b)
    for m in XI4_MARGINS:
        for g in XI4_GAPS_BPS:
            sig = monotonic_signal(j, m, g)
            sig = sig[sig["entry_ask"] <= XI4_CEILING]
            if sig.empty:
                variants.append(dict(margin=m, gap=g, n_dec=0))
                continue
            dec = (sig.sort_values(["slug", "seconds_into_window"])
                   .groupby("slug", as_index=False).first()
                   .rename(columns={"seconds_into_window": "entry_sec",
                                    "time_left_sec": "time_left"}))
            dec = dec[["slug", "symbol", "date", "split", "entry_sec", "time_left",
                       "buy_yes", "entry_ask"]]
            v = dict(margin=m, gap=g, n_dec=int(len(dec)),
                     pct_up=round(float(dec["buy_yes"].mean()), 3))
            sims = {}
            for mname, mode, seed in (("v2", "v2", 0), ("lg0", "live_guarded", 0)):
                f = rj.simulate_config(dec, cfg, mode, params, STAKE, seed=seed)
                f = f[f["filled"] == 1]
                sims[mname] = f
                v[mname] = {k: _ci_block(s["pnl"].to_numpy(), s["slug"].to_numpy())
                            for k, s in _split_dh_fut(f).items()}
            evs = []
            for s in SEEDS[1:]:
                f = rj.simulate_config(dec, cfg, "live_guarded", params, STAKE, seed=s)
                f = f[(f["filled"] == 1) & (f["split"] == "future")]
                evs.append(float(f["pnl"].mean()) if len(f) else np.nan)
            lg0fut = sims["lg0"]
            lg0fut = lg0fut[lg0fut["split"] == "future"]
            v["lg_future_seeds_ev"] = ([round(float(lg0fut["pnl"].mean()), 3)]
                                       if len(lg0fut) else [None]) + \
                                      [round(x, 3) if np.isfinite(x) else None for x in evs]
            v["jaccard"] = {k: jaccard(set(dec["slug"]), known[k]) for k in known}
            v["max_jaccard"] = max(v["jaccard"].values()) if v["jaccard"] else 0.0
            variants.append(v)
    out["variants"] = variants
    # registered chooser: max d+h lg total among n_dh_fills>=40; fallback max total
    def dh_total(v):
        blk = v.get("lg0", {}).get("dh") or {}
        return blk.get("total") if blk.get("total") is not None else -1e9
    elig = [v for v in variants
            if (v.get("lg0", {}).get("dh") or {}).get("n", 0) >= 40]
    pool = elig if elig else [v for v in variants if v.get("n_dec", 0) > 0]
    chosen = max(pool, key=dh_total) if pool else None
    out["chosen"] = (dict(margin=chosen["margin"], gap=chosen["gap"])
                     if chosen else None)
    if chosen:
        fut = (chosen.get("lg0", {}) or {}).get("future") or {}
        futv2 = (chosen.get("v2", {}) or {}).get("future") or {}
        legs = dict(
            ev_pos_both=bool((fut.get("ev") or 0) > 0 and (futv2.get("ev") or 0) > 0),
            ci_pos=bool(fut.get("lo") is not None and fut["lo"] > 0),
            fills_ge30=bool((fut.get("n") or 0) >= 30),
            jaccard_lt05=bool(chosen.get("max_jaccard", 1.0) < 0.5))
        out["lc4_legs"] = legs
        out["verdict"] = ("deploy-paper-candidate" if all(legs.values()) else
                          "duplicate-of-known" if legs["ev_pos_both"]
                          and not legs["jaccard_lt05"] else "reject")
    return out


def _known_slug_sets(b: pd.DataFrame) -> dict:
    _register_extra_configs()
    out = {}
    for k in KNOWN_FOR_JACCARD:
        try:
            out[k] = set(decisions(k, b)["slug"])
        except Exception:
            out[k] = set()
    return out


# ==========================================================================
# XI5 — E6 closeout
# ==========================================================================

def run_xi5(params) -> dict:
    from research.analysis import e6_cross_window_persistence as e6
    df = e6.load()
    win = e6.build_windows(df)
    win = relabel_splits(win)
    m = (win["consec"] & (win["prev_abs_move_bps"] >= 50)
         & win["prev_outcome"].notna())
    sub = win[m].copy()
    bet_up = (1 - sub["prev_outcome"].astype(int)).to_numpy()  # REVERSAL
    # original e6 economics (quoted ask, $10, Coinbase-clean settle) for reference
    ask, won_cb, pnl_cb = e6.apply_side(sub, bet_up)
    out = dict(test="XI5", rule="reversal>=50bps", n=int(len(sub)),
               by_split={sp: _ci_block(pnl_cb[(sub["split"] == sp).to_numpy()],
                                       sub["slug"][sub["split"] == sp].to_numpy())
                         for sp in ("dev", "holdout", "future")},
               note="by_split = original e6 economics ($10 quoted ask, CB-clean settle)")
    # registered gate: Chainlink settle, $5, v2 + live_guarded via simulate_config
    dec = sub[["slug", "symbol", "date", "split"]].copy()
    dec["entry_sec"] = sub["entry_sec"].astype(int).to_numpy()
    dec["time_left"] = 900 - dec["entry_sec"]
    dec["buy_yes"] = bet_up.astype(bool)
    dec["entry_ask"] = ask
    cfg = dict(ask_hi=0.99)
    for mname, mode, seed in (("v2", "v2", 0), ("lg0", "live_guarded", 0)):
        f = rj.simulate_config(dec, cfg, mode, params, STAKE, seed=seed)
        f = f[f["filled"] == 1]
        out[mname] = {k: _ci_block(s["pnl"].to_numpy(), s["slug"].to_numpy())
                      for k, s in _split_dh_fut(f).items()}
    fut_v2 = out["v2"]["future"]
    fut_lg = out["lg0"]["future"]
    legs = dict(ci_pos=bool(fut_v2.get("lo") is not None and fut_v2["lo"] > 0),
                n_ge30=bool((fut_v2.get("n") or 0) >= 30),
                lg_pos=bool((fut_lg.get("ev") or 0) > 0))
    out["legs"] = legs
    out["verdict"] = "paper-twin-worthy" if all(legs.values()) else "DEAD"
    return out


# ==========================================================================
# proxy validation (descriptive)
# ==========================================================================

def run_proxy(casc: pd.DataFrame) -> dict:
    out = dict(test="proxy_validation")
    per = (casc.groupby("symbol")["cascade"].agg(["sum", "mean", "size"])
           .rename(columns={"sum": "n_cascade", "mean": "share", "size": "n_minutes"}))
    out["per_coin"] = {k: dict(n_cascade=int(v["n_cascade"]),
                               share=round(float(v["share"]), 5),
                               n_minutes=int(v["n_minutes"]))
                       for k, v in per.iterrows()}
    # the 2026-06-10 17:45 UTC 3-coin live burst window must light up
    w0 = 1781113500
    box = casc[(casc["minute_ts"] >= w0 - 180) & (casc["minute_ts"] < w0 + 900)]
    out["incident_1781113500"] = {
        sym: dict(n_cascade=int(g["cascade"].sum()),
                  max_vol_z=round(float(g["vol_z"].max()), 1),
                  max_rng_z=round(float(g["rng_z"].max()), 1),
                  cascade_minutes=[int(x) for x in
                                   g.loc[g["cascade"], "minute_ts"].tolist()])
        for sym, g in box.groupby("symbol")}
    casc_days = (casc[casc["cascade"]]
                 .groupby(["date"])["cascade"].size().sort_values(ascending=False))
    out["busiest_days"] = {str(k): int(v) for k, v in casc_days.head(8).items()}
    # amended joint regime: must capture the incident minute (1781113800)
    jt = joint_cascade_ts(casc)
    out["joint_cascade"] = dict(
        n_minutes=int(len(jt)),
        share=round(float(len(jt) / casc["minute_ts"].nunique()), 5),
        incident_minute_flagged=bool(1781113800.0 in set(jt)))
    return out


# ==========================================================================
# main
# ==========================================================================

def _dump(obj, name: str) -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, name)
    with open(path, "w") as f:
        if isinstance(obj, list):
            for r in obj:
                f.write(json.dumps(r, default=str) + "\n")
        else:
            f.write(json.dumps(obj, indent=2, default=str))
    print(f"-> {path}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=["build", "proxy", "xi1", "xi2", "xi3", "xi4",
                                    "xi5", "all"])
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    if args.cmd in ("build",):
        casc = build_cascade_minutes(force=args.force)
        print(f"cascade minutes: {len(casc)} rows, "
              f"{int(casc['cascade'].sum())} cascade flags")
        t5 = build_ticks5m_coterminal(force=args.force)
        print(f"co-terminal 5m ticks: {len(t5)} rows, "
              f"{t5['market_slug'].nunique()} windows")
        return

    casc = build_cascade_minutes()
    if args.cmd in ("proxy", "all"):
        res = run_proxy(casc)
        _dump(res, "proxy_validation.json")
        print(json.dumps(res, indent=2)[:2000])
        if args.cmd == "proxy":
            return

    params = load_params(DEFAULT_PARAMS_PATH)
    b = load_base()
    oi, fund = load_oi(), load_funding()
    oi_thr, fund_thr = fit_regime_thresholds(oi, fund)
    cal = pd.read_csv(CAL_CSV)
    print("regime thresholds:", dict(oi=oi_thr, funding=fund_thr))

    if args.cmd in ("xi1", "all"):
        res = run_xi1(b, casc, oi, fund, cal, oi_thr, fund_thr, params)
        _dump(res, "xi1_gates.jsonl")
    if args.cmd in ("xi2", "all"):
        res = run_xi2(b, casc, oi, fund, cal, oi_thr, fund_thr, params)
        _dump(res, "xi2_edge.jsonl")
    if args.cmd in ("xi3", "all"):
        res = run_xi3(b, casc, oi, fund, cal, oi_thr, fund_thr, params)
        _dump(res, "xi3_events.json")
    if args.cmd in ("xi4", "all"):
        res = run_xi4(b, params)
        _dump(res, "xi4_5m15m.json")
    if args.cmd in ("xi5", "all"):
        res = run_xi5(params)
        _dump(res, "xi5_e6.json")


if __name__ == "__main__":
    main()
