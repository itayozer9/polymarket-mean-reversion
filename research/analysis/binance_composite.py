"""Binance(+Coinbase) composite oracle study — does a Binance-weighted composite
predict the CHAINLINK settlement print better than Coinbase alone?

Windows settle on the Chainlink print; our live signal is Coinbase WS. Chainlink
aggregates volume-weighted across venues and Binance dominates volume, so a
composite C(t) = w*BN_norm(t) + (1-w)*CB_norm(t) should sit closer to the CL
aggregate — cutting the near-strike sign-flip problem (Coinbase disagrees with the
settled outcome in ~37% of windows finishing within 2bps of strike).

Pre-registered gates (docs/research/test_ledger.md, 2026-06-10): B1 (near-strike
sign-disagreement cut >=25% rel), B2 (dual gate with composite: future live_guarded
EV/fill >= $0.63 baseline, CI>0, flip-rate lower), B3 (fetch coverage >=90%).

Discipline: per-coin venue offsets and the fitted weight use DEV windows only; the
composite variant for the gate is chosen on dev+holdout; the future block is
revealed once via --final.

Modes:
  --fetch-check          coverage per symbol-day (gate B3)
  --measure [--final]    print-prediction (T in {0,30,60}s) + near-strike
                         sign-agreement tables; dev/holdout only unless --final
  --gate [--final]       det_d12_dual pipeline with composite substituted for
                         Coinbase in dist/consistent + the AGREE gate's CB leg,
                         scored under v2 + live_guarded at $5 vs the baseline
  --rebuild              force rebuild of the cached per-window frame

Run: uv run python -m research.analysis.binance_composite --measure
"""
from __future__ import annotations

import argparse
import glob
import os
import re
from datetime import datetime, timezone

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BN_DIR = os.path.join(REPO, "data", "research", "binance_1s")
WFRAME = os.path.join(REPO, "data", "research", "binance_composite_windows.parquet")

SYMS = ("btc", "eth", "sol", "xrp")
START, END = "2026-05-22", "2026-06-09"   # study fetch window (universe ends 06-09 04:45)
TOL_MS = 120_000                            # asof tolerance, matches the CL pipeline
HORIZONS = (0, 30, 60)                      # seconds before window close
WEIGHTS = {"bn(w=1.0)": 1.0, "comp(w=0.7)": 0.7, "comp(w=0.5)": 0.5}  # + dev-fitted


# --------------------------------------------------------------------------
# pure helpers (unit-tested in tests/research/test_binance_composite.py)
# --------------------------------------------------------------------------

def asof_at(feed_ts: np.ndarray, feed_px: np.ndarray, query_ts: np.ndarray,
            tol_ms: int = TOL_MS) -> np.ndarray:
    """Backward asof: latest feed value at-or-before each query ts, NaN if none
    within tol_ms. feed_ts must be sorted ascending."""
    feed_ts = np.asarray(feed_ts, dtype="int64")
    feed_px = np.asarray(feed_px, dtype="f8")
    q = np.asarray(query_ts, dtype="int64")
    out = np.full(len(q), np.nan)
    if len(feed_ts) == 0:
        return out
    idx = np.searchsorted(feed_ts, q, side="right") - 1
    ok = idx >= 0
    age = np.where(ok, q - feed_ts[np.clip(idx, 0, None)], np.inf)
    ok &= age <= tol_ms
    out[ok] = feed_px[idx[ok]]
    return out


def fit_basis(cl: np.ndarray, proxy: np.ndarray) -> float:
    """Per-coin multiplicative venue offset: median(CL / proxy) over paired obs."""
    cl = np.asarray(cl, dtype="f8")
    proxy = np.asarray(proxy, dtype="f8")
    m = np.isfinite(cl) & np.isfinite(proxy) & (proxy > 0)
    if m.sum() == 0:
        return 1.0
    return float(np.median(cl[m] / proxy[m]))


def fit_weight(cl: np.ndarray, bn: np.ndarray, cb: np.ndarray,
               scale: np.ndarray) -> float:
    """No-intercept OLS of (cl-cb) on (bn-cb), both in bps of `scale` (pools coins
    of different price levels). Returns w clipped to [0,1]."""
    cl, bn, cb, sc = (np.asarray(a, dtype="f8") for a in (cl, bn, cb, scale))
    m = np.isfinite(cl) & np.isfinite(bn) & np.isfinite(cb) & np.isfinite(sc) & (sc > 0)
    y = (cl[m] - cb[m]) / sc[m] * 1e4
    x = (bn[m] - cb[m]) / sc[m] * 1e4
    den = float(np.sum(x * x))
    if den <= 0:
        return 0.5
    return float(np.clip(np.sum(x * y) / den, 0.0, 1.0))


def sign_disagree(proxy_close: np.ndarray, strike: np.ndarray,
                  cl_up: np.ndarray) -> np.ndarray:
    """1 where the proxy-implied outcome (proxy_close >= strike, the pipeline's
    cl_up convention) differs from the settled cl_up."""
    implied_up = (np.asarray(proxy_close, dtype="f8")
                  >= np.asarray(strike, dtype="f8")).astype(int)
    return (implied_up != np.asarray(cl_up, dtype=int)).astype(int)


# --------------------------------------------------------------------------
# data loading
# --------------------------------------------------------------------------

def load_binance_1s(symbol: str, date_start: str = START, date_end: str = END) -> pd.DataFrame:
    """Concat per-day parquets -> ts_ms (kline openTime), close. Sorted, deduped."""
    frames = []
    for p in sorted(glob.glob(os.path.join(BN_DIR, f"{symbol}_*.parquet"))):
        m = re.search(r"_(\d{4}-\d{2}-\d{2})\.parquet$", p)
        if not m or not (date_start <= m.group(1) <= date_end):
            continue
        frames.append(pd.read_parquet(p, columns=["ts_ms", "close"]))
    if not frames:
        return pd.DataFrame(columns=["ts_ms", "close"])
    df = pd.concat(frames, ignore_index=True)
    return (df.dropna().drop_duplicates("ts_ms").sort_values("ts_ms")
            .reset_index(drop=True))


def fetch_check() -> dict:
    """Gate B3: per-symbol coverage of the 1s grid over START..END."""
    from research.dataset.binance_fetch import date_range
    days = date_range(START, END)
    total = {}
    print(f"=== Binance 1s coverage {START}..{END} (gate B3: >=90% per symbol) ===")
    for sym in SYMS:
        per_day = []
        for d in days:
            p = os.path.join(BN_DIR, f"{sym}_{d}.parquet")
            n = len(pd.read_parquet(p, columns=["ts_ms"])) if os.path.exists(p) else 0
            per_day.append((d, n))
        rows = sum(n for _, n in per_day)
        cov = rows / (86_400 * len(days))
        total[sym] = cov
        missing = [f"{d}({n / 864:.0f}%)" for d, n in per_day if n < 0.90 * 86_400]
        print(f"  {sym}: {rows:>9,} / {86_400 * len(days):,} seconds = {cov * 100:6.2f}%"
              + (f"   days<90%: {', '.join(missing)}" if missing else ""))
    ok = all(c >= 0.90 for c in total.values())
    print(f"  -> B3 {'PASS' if ok else 'FAIL (findings provisional)'}")
    return total


# --------------------------------------------------------------------------
# per-window frame: CL strike/close/truth + proxies at T in {0,30,60}s + at open
# --------------------------------------------------------------------------

def build_window_frame() -> pd.DataFrame:
    from research.analysis import edge_lab
    from research.analysis.dual_oracle_features import load_chainlink_aged
    from research.dataset.feeds import load_spot

    b = edge_lab.load_base()
    last = (b[["slug", "symbol", "date", "split", "window_start_ts",
               "seconds_into_window", "cb_spot", "start_price"]]
            .sort_values("seconds_into_window").groupby("slug", as_index=False).last())
    w = last.rename(columns={"cb_spot": "cb_engine_close"}).drop(
        columns=["seconds_into_window"])
    w["symbol"] = w["symbol"].str.lower()
    w["t_start_ms"] = w["window_start_ts"].astype("int64") * 1000
    w["t_end_ms"] = (w["window_start_ts"].astype("int64") + 900) * 1000
    # the benchmark's near-strike measure: |engine cb close vs engine strike| bps
    w["cb_close_dist_bps"] = (w["cb_engine_close"] / w["start_price"] - 1.0).abs() * 1e4

    cl = load_chainlink_aged()
    out = []
    for sym, g in w.groupby("symbol"):
        g = g.sort_values("t_end_ms").copy()
        c = cl[cl["symbol"] == sym]
        cts = c["timestamp_ms"].to_numpy("int64")
        cpx = c["price"].to_numpy("f8")
        g["cl_strike"] = asof_at(cts, cpx, g["t_start_ms"].to_numpy())
        g["cl_close"] = asof_at(cts, cpx, g["t_end_ms"].to_numpy())

        sp = load_spot(sym, START, END)
        sts = sp["timestamp_ms"].to_numpy("int64")
        spx = sp["cb_spot"].to_numpy("f8")
        bn = load_binance_1s(sym)
        # a kline's close is fully known 1s after openTime -> shift availability
        bts = (bn["ts_ms"].to_numpy("int64") + 1000)
        bpx = bn["close"].to_numpy("f8")

        g["cb_start"] = asof_at(sts, spx, g["t_start_ms"].to_numpy())
        g["bn_start"] = asof_at(bts, bpx, g["t_start_ms"].to_numpy())
        for T in HORIZONS:
            q = g["t_end_ms"].to_numpy() - T * 1000
            g[f"cb_T{T}"] = asof_at(sts, spx, q)
            g[f"bn_T{T}"] = asof_at(bts, bpx, q)
        out.append(g)
    w = pd.concat(out, ignore_index=True)

    truth = edge_lab.cl_outcomes()
    w = w.merge(truth, on="slug", how="inner")
    # sanity: our asof reconstruction must reproduce the pipeline's cl_up
    recon = (w["cl_close"] >= w["cl_strike"]).astype(int)
    ok = w["cl_strike"].notna() & w["cl_close"].notna()
    mism = int((recon[ok] != w.loc[ok, "cl_up"]).sum())
    print(f"[build] windows={len(w)}  cl-present={int(ok.sum())}  "
          f"cl_up reconstruction mismatches={mism}")
    w.to_parquet(WFRAME, index=False)
    return w


def load_window_frame(rebuild: bool = False) -> pd.DataFrame:
    if rebuild or not os.path.exists(WFRAME):
        return build_window_frame()
    return pd.read_parquet(WFRAME)


# --------------------------------------------------------------------------
# dev-only fits: per-coin venue offsets + pooled regression weight
# --------------------------------------------------------------------------

def dev_fits(w: pd.DataFrame) -> tuple[dict, dict, float]:
    """k_bn[sym], k_cb[sym] (CL/venue median ratio) + pooled w_fit — DEV only."""
    dev = w[(w["split"] == "dev") & w["cl_close"].notna()]
    k_bn, k_cb = {}, {}
    for sym, g in dev.groupby("symbol"):
        k_cb[sym] = fit_basis(g["cl_close"], g["cb_T0"])
        k_bn[sym] = fit_basis(g["cl_close"], g["bn_T0"])
    bn_n = dev["bn_T0"].to_numpy() * dev["symbol"].map(k_bn).to_numpy()
    cb_n = dev["cb_T0"].to_numpy() * dev["symbol"].map(k_cb).to_numpy()
    w_fit = fit_weight(dev["cl_close"].to_numpy(), bn_n, cb_n,
                       dev["cl_strike"].to_numpy())
    return k_bn, k_cb, w_fit


def add_proxies(w: pd.DataFrame, k_bn: dict, k_cb: dict, w_fit: float) -> tuple[pd.DataFrame, dict]:
    """Normalized proxy columns at each horizon + at open. Returns (frame, proxies)
    where proxies maps display-name -> column prefix."""
    w = w.copy()
    kb = w["symbol"].map(k_bn).to_numpy()
    kc = w["symbol"].map(k_cb).to_numpy()
    weights = dict(WEIGHTS)
    weights[f"comp(w_fit={w_fit:.2f})"] = w_fit
    cols = {}
    for T in list(HORIZONS) + ["start"]:
        suf = f"T{T}" if T != "start" else "start"
        cbn = w[f"cb_{suf}"].to_numpy() * kc
        bnn = w[f"bn_{suf}"].to_numpy() * kb
        w[f"p_cb_{suf}"] = cbn
        for nm, wt in weights.items():
            w[f"p_{nm}_{suf}"] = wt * bnn + (1.0 - wt) * cbn
    cols["coinbase"] = "p_cb"
    for nm in weights:
        cols[nm] = f"p_{nm}"
    return w, cols


# --------------------------------------------------------------------------
# --measure: prediction error + near-strike sign agreement
# --------------------------------------------------------------------------

def _err_bps(w: pd.DataFrame, pref: str, T: int) -> pd.Series:
    return (w[f"{pref}_T{T}"] / w["cl_close"] - 1.0).abs() * 1e4


def _short(nm: str) -> str:
    """Compact unambiguous label: coinbase->cb, bn(w=1.0)->bn, comp(w=0.7)->c.70,
    comp(w_fit=0.51)->cFIT."""
    if nm == "coinbase":
        return "cb"
    if nm.startswith("bn("):
        return "bn"
    if "w_fit" in nm:
        return "cFIT"
    return "c." + nm.split("w=")[1].rstrip(")").replace("0.", "")


def choose_composite(w: pd.DataFrame, proxies: dict) -> tuple[str, dict]:
    """Pre-registered chooser (refined pre-reveal): among composite variants pick
    the lowest OWN-strike sign-disagreement on dev+holdout windows finishing
    within 5bps (benchmark slice); tie-break = median |proxy-CL_close| at T=0."""
    dh = w[w["split"].isin(["dev", "holdout"])]
    s5 = dh[dh["cb_close_dist_bps"] <= 5.0]
    scores = {}
    for nm, pref in proxies.items():
        if nm == "coinbase":
            continue
        scores[nm] = (float(sign_disagree(s5[f"{pref}_T0"], s5[f"{pref}_start"],
                                          s5["cl_up"]).mean()),
                      float(_err_bps(dh, pref, 0).median()))
    chosen = min(scores, key=lambda k: scores[k])
    return chosen, scores


def measure(final: bool = False, rebuild: bool = False) -> None:
    w = load_window_frame(rebuild)
    n0 = len(w)
    w = w[w["cl_close"].notna() & w["cl_strike"].notna()].copy()
    # aligned sample: every proxy present at every horizon (fair comparison)
    need = ([f"cb_T{t}" for t in HORIZONS] + [f"bn_T{t}" for t in HORIZONS]
            + ["cb_start", "bn_start"])
    w = w.dropna(subset=need)
    print(f"windows: {n0} -> {len(w)} with CL + all proxies at all horizons")

    k_bn, k_cb, w_fit = dev_fits(w)
    print("\n[dev-only fits] per-coin venue offset vs CL, (k-1)*1e4 bps:")
    for sym in SYMS:
        print(f"  {sym}: binance {((k_bn.get(sym, 1) - 1) * 1e4):+7.2f}bps   "
              f"coinbase {((k_cb.get(sym, 1) - 1) * 1e4):+7.2f}bps")
    print(f"[dev-only fit] pooled regression weight on Binance: w_fit={w_fit:.3f}")

    w, proxies = add_proxies(w, k_bn, k_cb, w_fit)
    splits = ["dev", "holdout"] + (["future"] if final else [])

    # ---- 1) print prediction: |proxy - CL_close| at T in {0,30,60} ----
    for sp in splits + ["dev+holdout" if not final else "ALL"]:
        sub = (w if sp == "ALL" else
               w[w["split"].isin(["dev", "holdout"])] if sp == "dev+holdout" else
               w[w["split"] == sp])
        print(f"\n=== |proxy - CL_close| in bps — split={sp} (n={len(sub)}) ===")
        hdr = f"{'proxy':>18} " + "  ".join(f"T={t:>2}s med/p90" for t in HORIZONS)
        print(hdr)
        for nm, pref in proxies.items():
            cells = []
            for T in HORIZONS:
                e = _err_bps(sub, pref, T)
                cells.append(f"{e.median():5.2f}/{e.quantile(0.9):6.2f}")
            print(f"{nm:>18} " + "  ".join(cells))
        # per coin at T=0 (the settlement-print question)
        print(f"  per-coin T=0 median: ", end="")
        for sym in SYMS:
            s = sub[sub["symbol"] == sym]
            parts = [f"{_short(nm)}={_err_bps(s, pref, 0).median():.2f}"
                     for nm, pref in proxies.items()]
            print(f"\n    {sym}: " + "  ".join(parts), end="")
        print()

    # ---- 2) near-strike sign agreement vs settled cl_up ----
    for slice_name, dist_col in (("cb-engine close-dist (benchmark frame)", "cb_close_dist_bps"),
                                 ("CL close-dist", None)):
        if dist_col is None:
            w["_cl_dist"] = (w["cl_close"] / w["cl_strike"] - 1.0).abs() * 1e4
            dist_col = "_cl_dist"
        print(f"\n=== near-strike sign-DISAGREEMENT with settled cl_up — slice by {slice_name} ===")
        for sp in splits + (["dev+holdout"] if not final else []):
            sub = (w[w["split"].isin(["dev", "holdout"])] if sp == "dev+holdout"
                   else w[w["split"] == sp])
            for band in (2.0, 5.0):
                s = sub[sub[dist_col] <= band]
                if len(s) < 5:
                    continue
                line = [f"  {sp:>11} <= {band:.0f}bps n={len(s):>4}:"]
                # primary: proxy close vs CL strike; secondary: vs own strike
                for nm, pref in proxies.items():
                    d_cl = sign_disagree(s[f"{pref}_T0"], s["cl_strike"], s["cl_up"]).mean()
                    d_own = sign_disagree(s[f"{pref}_T0"], s[f"{pref}_start"], s["cl_up"]).mean()
                    line.append(f"{_short(nm)}={d_cl * 100:4.1f}%/{d_own * 100:4.1f}%")
                print(" ".join(line))
        print("  (x/y = vs CL_strike / vs own strike)")

    # ---- chooser (pre-registered, refined 2026-06-10 pre-reveal): dev+holdout,
    # <=5bps benchmark slice, OWN-strike framing (what the gate substitution and
    # the deployed engine actually use; immune to USDT/USD basis drift),
    # tie-break median T=0 err ----
    chosen, scores = choose_composite(w, proxies)
    print(f"\n[chooser] dev+holdout <=5bps OWN-strike disagreement (tie-break median T=0 err): "
          + ", ".join(f"{k}={v[0] * 100:.1f}%/{v[1]:.2f}bps" for k, v in scores.items()))
    print(f"[chooser] CHOSEN composite for the gate test: {chosen}")

    if final:
        # B1 verdict: future, <=2bps benchmark slice, chosen vs coinbase (vs CL strike)
        fu = w[w["split"] == "future"]
        s2 = fu[fu["cb_close_dist_bps"] <= 2.0]
        pref_c = proxies[chosen]
        d_cb = sign_disagree(s2["p_cb_T0"], s2["cl_strike"], s2["cl_up"]).astype(int)
        d_cp = sign_disagree(s2[f"{pref_c}_T0"], s2["cl_strike"], s2["cl_up"]).astype(int)
        rel = (1 - d_cp.mean() / d_cb.mean()) * 100 if d_cb.mean() > 0 else float("nan")
        both = int(((d_cb == 1) & (d_cp == 1)).sum())
        cb_only = int(((d_cb == 1) & (d_cp == 0)).sum())
        cp_only = int(((d_cb == 0) & (d_cp == 1)).sum())
        print(f"\n=== B1 VERDICT (future, <=2bps cb close-dist, n={len(s2)}) ===")
        print(f"  coinbase disagree {d_cb.mean() * 100:.1f}%  {chosen} {d_cp.mean() * 100:.1f}%  "
              f"relative cut {rel:+.0f}% (gate: >=25%)")
        print(f"  paired: both-wrong={both}  cb-only-wrong={cb_only}  comp-only-wrong={cp_only}")
        own_cb = sign_disagree(s2["p_cb_T0"], s2["p_cb_start"], s2["cl_up"]).mean()
        own_cp = sign_disagree(s2[f"{pref_c}_T0"], s2[f"{pref_c}_start"], s2["cl_up"]).mean()
        print(f"  own-strike framing: coinbase {own_cb * 100:.1f}%  {chosen} {own_cp * 100:.1f}%")
        print(f"  -> B1 {'PASS' if rel >= 25 else 'FAIL'}")


# --------------------------------------------------------------------------
# --gate: det_d12_dual with composite distance + AGREE leg
# --------------------------------------------------------------------------

def composite_base(b: pd.DataFrame, w_frame: pd.DataFrame, k_bn: dict, k_cb: dict,
                   wt: float) -> pd.DataFrame:
    """Rebuild the per-tick signal columns from the composite: dist_strike_bps,
    abs_dist_bps, consistent, adverse_vel_10s (velocity source stays Coinbase;
    the projection sign follows the signal feed). Strike = composite at window
    open (own-strike, venue-drift immune — mirrors the engine's cb-vs-start_price)."""
    b2 = b.copy()
    b2["_sym"] = b2["symbol"].str.lower()
    b2["_t_ms"] = (b2["window_start_ts"].astype("int64")
                   + b2["seconds_into_window"].astype("int64")) * 1000

    # per-tick binance leg (asof, close known 1s after openTime)
    bn_tick = np.full(len(b2), np.nan)
    for sym in b2["_sym"].unique():
        bn = load_binance_1s(sym)
        bts = bn["ts_ms"].to_numpy("int64") + 1000
        bpx = bn["close"].to_numpy("f8")
        m = (b2["_sym"] == sym).to_numpy()
        bn_tick[m] = asof_at(bts, bpx, b2.loc[m, "_t_ms"].to_numpy())
    kb = b2["_sym"].map(k_bn).to_numpy()
    kc = b2["_sym"].map(k_cb).to_numpy()
    comp_tick = wt * kb * bn_tick + (1.0 - wt) * kc * b2["cb_spot"].to_numpy("f8")

    # per-slug composite strike: bn at open from w_frame, cb leg = engine start_price
    st = w_frame[["slug", "bn_start"]].drop_duplicates("slug")
    b2 = b2.merge(st, on="slug", how="left")
    comp_strike = (wt * kb * b2["bn_start"].to_numpy("f8")
                   + (1.0 - wt) * kc * b2["start_price"].to_numpy("f8"))

    dist = (comp_tick / comp_strike - 1.0) * 1e4
    nan_share = float(np.mean(~np.isfinite(dist)))
    b2["dist_strike_bps"] = dist
    b2["abs_dist_bps"] = np.abs(dist)
    yf = b2["yes_mid"] >= 0.5
    sfy = b2["dist_strike_bps"] > 0
    b2["consistent"] = (yf & sfy) | (~yf & ~sfy)
    b2["adverse_vel_10s"] = -np.sign(dist) * b2["spot_vel_10s_bps"].to_numpy("f8")
    print(f"[composite_base] ticks={len(b2):,}  composite-NaN share={nan_share * 100:.2f}%")
    return b2.drop(columns=["_sym", "_t_ms", "bn_start"])


def _flip_rates(dec: pd.DataFrame, sim: pd.DataFrame) -> tuple[float, float]:
    """(signal-level, fill-level) share where the entry side != settled cl_up."""
    from research.analysis.edge_lab import cl_outcomes
    cl = cl_outcomes()
    d = dec.merge(cl, on="slug", how="inner")
    sig = float((d["buy_yes"].astype(bool).to_numpy() != (d["cl_up"] == 1).to_numpy()).mean())
    f = sim[sim["filled"] == 1]
    fill = float((f["won"] == 0).mean()) if len(f) else float("nan")
    return sig, fill


def gate(final: bool = False, rebuild: bool = False, seeds: tuple = (0,)) -> None:
    from research.analysis import edge_lab, rejudge_live_model as rj
    from research.lib.stats import window_clustered_bootstrap
    from research.sim.fills_live import DEFAULT_PARAMS_PATH, load_params

    w = load_window_frame(rebuild)
    k_bn, k_cb, w_fit = dev_fits(w[w["cl_close"].notna()])
    # chosen composite weight comes from the --measure chooser; recompute it here
    wm = w[w["cl_close"].notna() & w["cl_strike"].notna()].dropna(
        subset=[f"bn_T{t}" for t in HORIZONS] + [f"cb_T{t}" for t in HORIZONS]
        + ["bn_start", "cb_start"])
    wm, proxies = add_proxies(wm, k_bn, k_cb, w_fit)
    chosen, _scores = choose_composite(wm, proxies)
    wt = (w_fit if "w_fit" in chosen else WEIGHTS[chosen])
    print(f"[gate] chosen composite: {chosen} (w={wt:.3f}; dev+holdout chooser)")

    b = edge_lab.load_base()
    b2 = composite_base(b, w, k_bn, k_cb, wt)
    params = load_params(DEFAULT_PARAMS_PATH)
    cfg = rj.CONFIGS["det_d12_dual_live"]

    variants = {"baseline(coinbase)": b, f"composite[{chosen}]": b2}
    splits = ["dev", "holdout"] + (["future"] if final else [])
    results, decs, sims0 = {}, {}, {}
    for vname, frame in variants.items():
        dec = rj.decisions_for("det_d12_dual_live", frame)
        dec = rj._apply_dual_gate(dec, frame)
        decs[vname] = dec
        print(f"\n--- {vname}: {len(dec)} gated signals "
              f"({dec.groupby('split').size().to_dict()}) ---")
        for model in ("v2", "live_guarded"):
            sims = [rj.simulate_config(dec, cfg, model, params, 5.0, seed=s)
                    for s in (seeds if model == "live_guarded" else (0,))]
            t = sims[0]  # seed 0 = registered primary
            sims0[(vname, model)] = t
            for sp in splits:
                tt = t[t["split"] == sp]
                f = tt[tt["filled"] == 1]
                if len(f) < 3:
                    print(f"  {model:13} {sp:>8}: n_fill={len(f)} (too few)")
                    continue
                lo, _, hi = window_clustered_bootstrap(f["pnl"].values, f["slug"].values, n=2000)
                sig_fl, fill_fl = _flip_rates(dec[dec["split"] == sp], tt)
                ms = ""
                if model == "live_guarded" and len(sims) > 1:
                    evs = [s_[(s_["split"] == sp) & (s_["filled"] == 1)]["pnl"].mean()
                           for s_ in sims]
                    ms = f"  seeds0-{len(sims) - 1} meanEV ${np.nanmean(evs):+.2f}"
                print(f"  {model:13} {sp:>8}: sig={len(tt):>3} fill={len(f):>3} "
                      f"EV/fill ${f['pnl'].mean():+.3f} [{lo:+.3f},{hi:+.3f}] "
                      f"WR {f['won'].mean() * 100:.1f}%  flip sig/fill "
                      f"{sig_fl * 100:.1f}%/{fill_fl * 100:.1f}%{ms}")
                results[(vname, model, sp)] = dict(
                    n_sig=len(tt), n_fill=len(f), ev=float(f["pnl"].mean()),
                    lo=float(lo), hi=float(hi), wr=float(f["won"].mean()),
                    flip_sig=sig_fl, flip_fill=fill_fl)

    # sample-mix diagnostic: window overlap between the two decision sets (+ on
    # FINAL, a paired EV delta on common future windows, both filled, seed 0)
    bset = set(decs["baseline(coinbase)"]["slug"])
    cset = set(decs[f"composite[{chosen}]"]["slug"])
    print(f"\n[overlap] windows: baseline={len(bset)} composite={len(cset)} "
          f"common={len(bset & cset)} baseline-only={len(bset - cset)} "
          f"composite-only={len(cset - bset)}")
    if final:
        tb = sims0[("baseline(coinbase)", "live_guarded")]
        tc = sims0[(f"composite[{chosen}]", "live_guarded")]
        fb = tb[(tb["filled"] == 1) & (tb["split"] == "future")].set_index("slug")["pnl"]
        fc = tc[(tc["filled"] == 1) & (tc["split"] == "future")].set_index("slug")["pnl"]
        common = fb.index.intersection(fc.index)
        if len(common) >= 3:
            from research.lib.stats import window_clustered_bootstrap as wcb
            d = (fc.loc[common] - fb.loc[common]).values
            lo, _, hi = wcb(d, np.asarray(common), n=2000)
            print(f"[paired] common future fills n={len(common)}: "
                  f"EV delta (comp-base) ${d.mean():+.3f}/fill [{lo:+.3f},{hi:+.3f}]")
        bl = results.get(("baseline(coinbase)", "live_guarded", "future"))
        cp = results.get((f"composite[{chosen}]", "live_guarded", "future"))
        print("\n=== B2 VERDICT (future, live_guarded, $5, seed 0) ===")
        if bl and cp:
            ok_ev = cp["ev"] >= 0.63
            ok_ci = cp["lo"] > 0
            ok_flip = cp["flip_fill"] < bl["flip_fill"]
            print(f"  baseline : EV ${bl['ev']:+.3f} [{bl['lo']:+.3f},{bl['hi']:+.3f}] "
                  f"flip {bl['flip_fill'] * 100:.1f}%  (registered ref $0.63)")
            print(f"  composite: EV ${cp['ev']:+.3f} [{cp['lo']:+.3f},{cp['hi']:+.3f}] "
                  f"flip {cp['flip_fill'] * 100:.1f}%")
            print(f"  EV>=0.63: {ok_ev}  CI lower>0: {ok_ci}  flip lower: {ok_flip}"
                  f"  -> B2 {'PASS' if (ok_ev and ok_ci and ok_flip) else 'FAIL'}")
        else:
            print("  (missing cells — too few fills)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fetch-check", action="store_true")
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--gate", action="store_true")
    ap.add_argument("--final", action="store_true",
                    help="reveal the future block (run ONCE at the end)")
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--seeds", default="0", help="comma list for live_guarded MC")
    args = ap.parse_args()
    if args.fetch_check:
        fetch_check()
    if args.measure:
        measure(final=args.final, rebuild=args.rebuild)
    if args.gate:
        gate(final=args.final, rebuild=args.rebuild,
             seeds=tuple(int(s) for s in args.seeds.split(",")))
    if not (args.fetch_check or args.measure or args.gate):
        ap.print_help()


if __name__ == "__main__":
    main()
