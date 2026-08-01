"""HUNT: cross-window PERSISTENCE (Tier A) — decide at window OPEN.

Raw prev-window momentum/reversal is already dead (~ -0.3 / -0.8 per trade).
This refines with CONDITIONING and loops many variants through the shared
edge_lab gauntlet (Chainlink resettle, depth-gated fill, latency sweep,
per-split CIs, CPCV, DSR):

  - prior-outcome SIGNAL source: chainlink (true) vs coinbase (live-observable)
  - direction: MOMENTUM (same as prev) vs REVERSAL (opposite)
  - STREAK length: N consecutive same-direction prior windows (same symbol)
  - prior MOVE MAGNITUDE band (|end/start-1| of prev window, bps)
  - per-SYMBOL slice
  - UTC hour / session (Asia/EU/US)
  - vol REGIME: prev-window realized_vol terciles (trend vs chop proxy)
  - entry TIME-LEFT window (decide-at-open: 780-860; also a wider 700-860)

A trade = first qualifying early tick per window, hold to resolution, settle
Chainlink. Buy the predicted side. Kept by FRESH-OOS (future) CI lower bound.

Run: uv run python -m research.analysis.hunt.persistence
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from research.analysis import edge_lab as L

WINDOW_SEC = 900


# --------------------------------------------------------------------------
# per-window table with prior-window conditioning features (per symbol, lag-1
# only on truly-consecutive 900s windows). Two prior-outcome sources:
#   cl_up  = Chainlink (the true settlement)        -> "true" prior signal
#   cb_up  = outcome_up_clean (Coinbase)            -> live-observable proxy
# Streaks are computed on the chosen source separately.
# --------------------------------------------------------------------------
def build_windows(b: pd.DataFrame) -> pd.DataFrame:
    w = (b.sort_values(["slug", "seconds_into_window"]).groupby("slug", as_index=False)
         .agg(symbol=("symbol", "first"), wst=("window_start_ts", "first"),
              date=("date", "first"), split=("split", "first"),
              cb_up=("outcome_up_clean", "first"),
              start=("start_price", "first"), end=("end_price", "first"),
              rv=("realized_vol", "first"), uh=("utc_hour", "first")))
    w = w.merge(L.cl_outcomes(), on="slug", how="left")  # adds cl_up
    # signed move of THIS window (guard bad end prices -> NaN, excluded from bands)
    good = (w["end"] > 0) & (w["start"] > 0)
    w["move_bps"] = np.where(good, (w["end"] / w["start"] - 1.0) * 1e4, np.nan)
    w["abs_move"] = w["move_bps"].abs()
    w = w.sort_values(["symbol", "wst"]).reset_index(drop=True)

    g = w.groupby("symbol")
    w["prev_ts"] = g["wst"].shift(1)
    w["consec"] = (w["wst"] - w["prev_ts"]) == WINDOW_SEC
    # lag-1 features (prev window of SAME symbol)
    for src in ("cl_up", "cb_up"):
        w[f"prev_{src}"] = g[src].shift(1)
    w["prev_move_bps"] = g["move_bps"].shift(1)
    w["prev_abs_move"] = g["abs_move"].shift(1)
    w["prev_rv"] = g["rv"].shift(1)

    # streak length on each source: number of consecutive SAME-direction prior
    # windows ending at the immediately-previous window. Computed per symbol on
    # contiguous runs; reset whenever a window is non-consecutive.
    for src in ("cl_up", "cb_up"):
        w[f"streak_{src}"] = _streak(w, src)
    return w


def _streak(w: pd.DataFrame, col: str) -> np.ndarray:
    """For each row, signed length of the run of identical `col` values ending at
    the PREVIOUS window (same symbol, on the contiguous 900s chain). +k = k
    consecutive UPs just happened, -k = k DOWNs. 0 if unknown/chain-broken.

    Vectorised: compute the run length THROUGH each window, then the streak
    available to the next window is that run shifted by one (per symbol), zeroed
    wherever the chain breaks or the value is missing.
    """
    out = np.zeros(len(w), dtype="f8")
    w = w.assign(_row=np.arange(len(w)))
    for _sym, sub in w.groupby("symbol", sort=False):
        rows = sub["_row"].to_numpy()
        v = sub[col].to_numpy("f8")               # 0/1/nan
        ts = sub["wst"].to_numpy("f8")
        contig = np.empty(len(sub), bool)
        contig[0] = False
        contig[1:] = (ts[1:] - ts[:-1]) == WINDOW_SEC
        # run length THROUGH window i (counting i): resets when value changes,
        # value is nan, or chain breaks
        run = np.zeros(len(sub), "f8")
        for i in range(len(sub)):
            if np.isnan(v[i]):
                run[i] = 0.0
            elif i > 0 and contig[i] and not np.isnan(v[i - 1]) and v[i] == v[i - 1]:
                run[i] = run[i - 1] + 1
            else:
                run[i] = 1.0
        # streak available to window i = run ending at i-1 (if chain contiguous)
        s = np.zeros(len(sub), "f8")
        s[1:] = np.where(contig[1:], run[:-1], 0.0)
        sign = np.where(v == 1, 1.0, np.where(v == 0, -1.0, 0.0))
        # carry the sign of the PREVIOUS window's value
        prev_sign = np.zeros(len(sub), "f8")
        prev_sign[1:] = sign[:-1]
        out[rows] = s * prev_sign
    return out


# --------------------------------------------------------------------------
# turn a per-window mask + chosen side into a decision frame via edge_lab.
# We pick the first early tick inside the time-left band for each masked slug.
# --------------------------------------------------------------------------
SESS = {
    "asia": lambda h: (h >= 0) & (h < 8),
    "eu":   lambda h: (h >= 7) & (h < 13),
    "us":   lambda h: (h >= 13) & (h < 21),
}


def decide(b: pd.DataFrame, w: pd.DataFrame, mask: pd.Series, buy_up: pd.Series,
           tl_lo: int, tl_hi: int) -> pd.DataFrame:
    """mask, buy_up: aligned to w. Returns an edge_lab decision frame."""
    sel = w[mask.values]
    side = dict(zip(sel["slug"], buy_up[mask.values].astype(bool)))
    keep_slugs = set(sel["slug"])
    cand = b[b["slug"].isin(keep_slugs) & b["time_left_sec"].between(tl_lo, tl_hi)
             & b["book_healthy"]].copy()
    if cand.empty:
        return cand
    by = cand["slug"].map(side).to_numpy()
    return L.first_tick(cand, by)


from research.lib.stats import window_clustered_bootstrap as _wcb


def _ci(pnl, slug, n=1500):
    if len(pnl) < 5:
        return None
    lo, _, hi = _wcb(np.asarray(pnl, "f8"), np.asarray(slug), n=n)
    return round(float(lo), 2), round(float(hi), 2)


def quick(name: str, dec: pd.DataFrame) -> dict | None:
    """Fast pass: latency=2 FULL + future EV/CI only (one bootstrap each)."""
    if dec is None or dec.empty:
        print(f"{name:42} n=0 (no candidates)")
        return None
    led = L.simulate(dec, latency=2)
    if led is None or len(led) < 5:
        print(f"{name:42} n={0 if led is None else len(led):>3} (too few fills)")
        return None
    fl_ev = round(float(led["pnl"].mean()), 2)
    fl_wr = round(float(led["won"].mean() * 100), 0)
    fl_ci = _ci(led["pnl"].values, led["slug"].values)
    fut = led[led["split"] == "future"]
    fu_ev = round(float(fut["pnl"].mean()), 2) if len(fut) else None
    fu_ci = _ci(fut["pnl"].values, fut["slug"].values) if len(fut) >= 5 else None
    fu_s = (f"${fu_ev:+.2f}[{fu_ci[0]:+.2f},{fu_ci[1]:+.2f}]n{len(fut)}"
            if fu_ci else (f"${fu_ev:+.2f}n{len(fut)}" if fu_ev is not None else "n/a"))
    fl_s = f"${fl_ev:+.2f}[{fl_ci[0]:+.2f},{fl_ci[1]:+.2f}]" if fl_ci else f"${fl_ev:+.2f}"
    print(f"{name:42} n={len(led):>3} FULL {fl_s}WR{fl_wr:.0f} fut {fu_s}")
    return dict(name=name, dec=dec, led=led, n=len(led), fl_ev=fl_ev, fl_ci=fl_ci,
                fu_ev=fu_ev, fu_ci=fu_ci, n_fut=len(fut))


def main():
    b = L.load_base().copy()
    w = build_windows(b)
    print(f"[data] windows={len(w)} consec={int(w['consec'].sum())} "
          f"splits={w.groupby('split').size().to_dict()}")
    print(f"[data] streak_cl range {int(w['streak_cl_up'].min())}..{int(w['streak_cl_up'].max())}")

    results: list[dict] = []

    def run(name, mask, buy_up, tl=(780, 860)):
        rec = quick(name, decide(b, w, mask, buy_up, tl[0], tl[1]))
        if rec:
            results.append(rec)

    base_ok = w["consec"] & w["prev_cl_up"].notna()
    UP = pd.Series(True, index=w.index)

    print("\n=== A. raw momentum / reversal baselines (expect dead) ===")
    run("mom_all(cl)", base_ok, w["prev_cl_up"] == 1)
    run("rev_all(cl)", base_ok, w["prev_cl_up"] == 0)
    run("mom_all(cb-observable)", w["consec"] & w["prev_cb_up"].notna(), w["prev_cb_up"] == 1)
    run("rev_all(cb-observable)", w["consec"] & w["prev_cb_up"].notna(), w["prev_cb_up"] == 0)

    print("\n=== B. condition on prior MOVE MAGNITUDE (cl outcome dir) ===")
    for thr in (20, 35, 50, 75, 100):
        m = base_ok & (w["prev_abs_move"] >= thr)
        run(f"mom |prevMove|>={thr}bps", m, w["prev_cl_up"] == 1)
        run(f"rev |prevMove|>={thr}bps", m, w["prev_cl_up"] == 0)

    print("\n=== C. condition on STREAK length (cl) ===")
    for k in (2, 3, 4):
        # momentum: k+ consecutive same-dir -> continue
        m = base_ok & (w["streak_cl_up"].abs() >= k)
        run(f"mom streak>={k}", m, w["streak_cl_up"] > 0)
        run(f"rev streak>={k}", m, w["streak_cl_up"] < 0)  # fade the streak

    print("\n=== D. STREAK x MAGNITUDE ===")
    for k in (2, 3):
        for thr in (30, 50):
            m = base_ok & (w["streak_cl_up"].abs() >= k) & (w["prev_abs_move"] >= thr)
            run(f"mom streak>={k} &|move|>={thr}", m, w["streak_cl_up"] > 0)
            run(f"rev streak>={k} &|move|>={thr}", m, w["streak_cl_up"] < 0)

    print("\n=== E. vol REGIME (prev realized_vol terciles) ===")
    rvq = w["prev_rv"].quantile([1/3, 2/3])
    lo_rv, hi_rv = float(rvq.iloc[0]), float(rvq.iloc[1])
    chop = base_ok & (w["prev_rv"] <= lo_rv)
    trend = base_ok & (w["prev_rv"] >= hi_rv)
    run("mom chop(lowRV)", chop, w["prev_cl_up"] == 1)
    run("rev chop(lowRV)", chop, w["prev_cl_up"] == 0)
    run("mom trend(highRV)", trend, w["prev_cl_up"] == 1)
    run("rev trend(highRV)", trend, w["prev_cl_up"] == 0)
    # trend + magnitude (classic continuation regime)
    run("mom trend &|move|>=50", trend & (w["prev_abs_move"] >= 50), w["prev_cl_up"] == 1)
    run("rev chop &|move|>=50", chop & (w["prev_abs_move"] >= 50), w["prev_cl_up"] == 0)

    print("\n=== F. SESSION / hour (cl) ===")
    for sname, fn in SESS.items():
        m = base_ok & fn(w["uh"])
        run(f"mom {sname}", m, w["prev_cl_up"] == 1)
        run(f"rev {sname}", m, w["prev_cl_up"] == 0)

    print("\n=== G. per-SYMBOL (cl, momentum & reversal) ===")
    for sym in ("btc", "eth", "sol", "xrp"):
        m = base_ok & (w["symbol"] == sym)
        run(f"mom {sym}", m, w["prev_cl_up"] == 1)
        run(f"rev {sym}", m, w["prev_cl_up"] == 0)

    print("\n=== H. best-combo probes (magnitude x session, magnitude x symbol) ===")
    run("rev us &|move|>=50", base_ok & SESS["us"](w["uh"]) & (w["prev_abs_move"] >= 50), w["prev_cl_up"] == 0)
    run("mom us &|move|>=50", base_ok & SESS["us"](w["uh"]) & (w["prev_abs_move"] >= 50), w["prev_cl_up"] == 1)
    run("rev asia &|move|>=50", base_ok & SESS["asia"](w["uh"]) & (w["prev_abs_move"] >= 50), w["prev_cl_up"] == 0)
    run("mom asia &|move|>=50", base_ok & SESS["asia"](w["uh"]) & (w["prev_abs_move"] >= 50), w["prev_cl_up"] == 1)
    # wider entry band sanity (decide a bit later but still 'open-ish')
    run("rev |move|>=50 [700-860]", base_ok & (w["prev_abs_move"] >= 50), w["prev_cl_up"] == 0, tl=(700, 860))

    # --------------------------------------------------------------
    # RANK by fresh-OOS (future) CI lower bound (quick pass)
    # --------------------------------------------------------------
    print("\n" + "=" * 96)
    print("QUICK-PASS TOP by FUTURE CI lower bound (latency-2 only):")
    ranked = [r for r in results if r["fu_ci"] is not None and r["n_fut"] >= 30]
    ranked.sort(key=lambda r: r["fu_ci"][0], reverse=True)
    for r in ranked[:15]:
        print(f"  {r['name']:42} fut ${r['fu_ev']:+.2f}[{r['fu_ci'][0]:+.2f},{r['fu_ci'][1]:+.2f}]"
              f"n{r['n_fut']} | FULL ${r['fl_ev']:+.2f}"
              f"[{r['fl_ci'][0]:+.2f},{r['fl_ci'][1]:+.2f}] n{r['n']}")

    # also list top by FULL CI lower bound (for diagnosis only — not the judge)
    rfull = sorted([r for r in results if r["fl_ci"] is not None],
                   key=lambda r: r["fl_ci"][0], reverse=True)
    print("\nQUICK-PASS TOP by FULL CI lower bound (diagnosis only):")
    for r in rfull[:8]:
        fu_s = (f"${r['fu_ev']:+.2f}n{r['n_fut']}" if r["fu_ev"] is not None else "n/a")
        print(f"  {r['name']:42} FULL ${r['fl_ev']:+.2f}[{r['fl_ci'][0]:+.2f},{r['fl_ci'][1]:+.2f}]"
              f"n{r['n']} | fut {fu_s}")

    # --------------------------------------------------------------
    # DEEP pass: full latency sweep + CPCV + DSR on the most promising.
    # Take union of (top-6 by future CI-lo) and (top-4 by FULL CI-lo),
    # so we deep-evaluate winners AND the best FULL candidates honestly.
    # --------------------------------------------------------------
    deep_names, seen = [], set()
    for r in ranked[:6] + rfull[:4]:
        if r["name"] not in seen:
            seen.add(r["name"]); deep_names.append(r)
    print("\n" + "=" * 96)
    print(f"DEEP eval (latency sweep + CPCV + DSR) on {len(deep_names)} candidates:")
    deep = []
    for r in deep_names:
        dec = r["dec"]
        e = L.evaluate(r["led"])
        lat = L.latency_survival(dec)
        fl = e["per_split"]["FULL"]; fu = e["per_split"].get("future")
        l2 = lat.get(2, {}).get("ev"); l5 = lat.get(5, {}).get("ev"); l10 = lat.get(10, {}).get("ev")
        fl2 = lat.get(2, {}).get("fut_ev"); fl5 = lat.get(5, {}).get("fut_ev")
        fl10 = lat.get(10, {}).get("fut_ev")
        fu_s = (f"${fu['ev']:+.2f}[{fu['lo']:+.2f},{fu['hi']:+.2f}]n{fu['n']}" if fu else "n/a")
        print(f"  {r['name']:40} n{e['n']} FULL ${fl['ev']:+.2f}[{fl['lo']:+.2f},{fl['hi']:+.2f}]WR{fl['wr']:.0f}")
        print(f"      future {fu_s} | lat 2/5/10 ${_f(l2)}/{_f(l5)}/{_f(l10)} "
              f"| futlat 2/5/10 ${_f(fl2)}/{_f(fl5)}/{_f(fl10)} "
              f"| CPCV{e['cpcv'].get('pct_pos',0):.0f} DSR{e['dsr']['dsr']}")
        deep.append(dict(r=r, e=e, lat=lat, fl=fl, fu=fu))

    # BEST = highest future CI lower bound among deep set
    deep_ok = [d for d in deep if d["fu"] is not None]
    deep_ok.sort(key=lambda d: d["fu"]["lo"], reverse=True)
    if deep_ok:
        bdt = deep_ok[0]
        e = bdt["e"]; fl = bdt["fl"]; fu = bdt["fu"]; lat = bdt["lat"]
        print("\n" + "=" * 96)
        print("BEST (deep, by future CI lower bound):", bdt["r"]["name"])
        print("  n", e["n"], "FULL ev", fl["ev"], "ci", [fl["lo"], fl["hi"]],
              "total", fl["total"], "ev*n", round(fl["ev"] * e["n"], 1))
        print("  future ev", fu["ev"], "ci", [fu["lo"], fu["hi"]], "n_fut", fu["n"])
        print("  latency EV", {k: lat[k]["ev"] for k in (2, 5, 10) if k in lat})
        print("  latency FUT EV", {k: lat[k].get("fut_ev") for k in (2, 5, 10) if k in lat})
        print("  cpcv pct_pos", e["cpcv"].get("pct_pos"), "dsr", e["dsr"]["dsr"])


def _f(x):
    return f"{x:+.2f}" if x is not None else "  n/a"


if __name__ == "__main__":
    main()
