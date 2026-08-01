"""HUNT — crosscoin (Tier B): cross-sectional relative value + BTC-leads-alts tilt.

Windows are aligned across the 4 coins (1225/1226 window_start_ts have all 4),
so at any snapshot time-left we have 4 simultaneous markets -> a SELECTION across
coins (latency-robust), not a speed race.

v2 focuses the search after v1 found the only future-CI-positive, latency-flat
region was BTC-strongly-determined -> tilt ALT favourites that AGREE with BTC.
v1 ALSO showed (diagnostic) that the agree-edge is PARTLY a deep-favourite
selection artifact (control: all deep-fav alts are ~fairly priced, edge 0.0),
so v2 runs an explicit NO-BTC CONTROL beside every BTC-conditioned variant to
prove the BTC signal adds value beyond "buy deep favourites". It also tests
buying ALL agreeing alts (more n) and the contrarian DISAGREE fade.

Run: uv run python -m research.analysis.hunt.crosscoin
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import research.analysis.edge_lab as L

pd.set_option("display.width", 220)

COINS = ["btc", "eth", "sol", "xrp"]
FAIR_EDGES = np.array([0, 2, 4, 6, 9, 13, 18, 25, 35, 50, 80, 300], dtype=float)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def snapshot(b: pd.DataFrame, tl_lo: int, tl_hi: int) -> pd.DataFrame:
    s = b[(b["time_left_sec"] >= tl_lo) & (b["time_left_sec"] <= tl_hi)].copy()
    return s.sort_values("seconds_into_window").groupby("slug", as_index=False).first()


def attach_cl(s: pd.DataFrame) -> pd.DataFrame:
    cl = L.cl_outcomes()
    s = s.merge(cl, on="slug", how="inner")
    s["fav_yes"] = s["fav_side"] == "yes"
    s["fav_won_cl"] = np.where(s["fav_yes"], s["cl_up"] == 1, s["cl_up"] == 0).astype(int)
    return s


def fit_fair(dev: pd.DataFrame) -> dict:
    bk = np.clip(np.digitize(dev["abs_dist_bps"].to_numpy(), FAIR_EDGES) - 1,
                 0, len(FAIR_EDGES) - 2)
    won = dev["fav_won_cl"].to_numpy()
    return {int(k): float(won[bk == k].mean())
            for k in range(len(FAIR_EDGES) - 1) if (bk == k).sum() >= 25}


def fair_of(dist_bps, fair, fallback_ask):
    bk = np.clip(np.digitize(dist_bps, FAIR_EDGES) - 1, 0, len(FAIR_EDGES) - 2)
    out = np.array([fair.get(int(k), np.nan) for k in bk])
    nan = ~np.isfinite(out)
    out[nan] = np.asarray(fallback_ask)[nan]
    return out


def dec_of(pick: pd.DataFrame, buy_yes) -> pd.DataFrame:
    d = pick.copy()
    d["entry_sec"] = d["seconds_into_window"].astype(int)
    d["buy_yes"] = np.asarray(buy_yes)
    keep = ["slug", "symbol", "date", "split", "window_start_ts", "entry_sec", "buy_yes"]
    return d[[c for c in keep if c in d.columns]]


def report(name: str, dec: pd.DataFrame, store: list):
    if dec is None or dec.empty:
        print(f"{name:38} n=0 (no picks)"); return None
    led = L.simulate(dec, latency=2)
    if led is None or len(led) == 0:
        print(f"{name:38} n=0 (no fills)"); return None
    lat = L.latency_survival(dec)
    e = L.evaluate(led)
    fu = e["per_split"].get("future")
    fl = e["per_split"].get("FULL")
    cp = e["cpcv"].get("pct_pos", float("nan"))
    dsr = e["dsr"]["dsr"]
    futxt = (f"fut ${fu['ev']:+.2f}[{fu['lo']:+.2f},{fu['hi']:+.2f}]n{fu['n']}"
             if fu else "fut n/a")
    laxt = " ".join(f"{k}s${v['ev']:+.2f}" if v.get("ev") is not None else f"{k}s-"
                    for k, v in lat.items())
    flxt = (f"FULL ${fl['ev']:+.2f}[{fl['lo']:+.2f},{fl['hi']:+.2f}]WR{fl['wr']:.0f}% tot${fl['total']:+.0f}"
            if fl else "FULL n/a")
    print(f"{name:38} n={e['n']:>4} {flxt} | {futxt} | CPCV{cp:.0f}% DSR{dsr}")
    print(f"{'':38}   lat {laxt}")
    rec = {"name": name, "e": e, "lat": lat,
           "fut_lo": (fu["lo"] if fu else None), "fut_ev": (fu["ev"] if fu else None),
           "fut_n": (fu["n"] if fu else 0),
           "lat5": (lat.get(5, {}) or {}).get("ev"),
           "lat10": (lat.get(10, {}) or {}).get("ev"),
           "cpcv": cp, "dsr": dsr}
    store.append(rec)
    return rec


def btc_alts(snap: pd.DataFrame) -> pd.DataFrame:
    btc = snap[snap["symbol"] == "btc"][
        ["window_start_ts", "fav_yes", "fav_ask", "abs_dist_bps"]
    ].rename(columns={"fav_yes": "btc_up", "fav_ask": "btc_favask", "abs_dist_bps": "btc_dist"})
    return snap[snap["symbol"] != "btc"].merge(btc, on="window_start_ts", how="inner")


# ---------------------------------------------------------------------------
def run():
    b = L.load_base()
    R = []
    print("=" * 104)
    print("CROSSCOIN HUNT v2 — BTC-determined -> alt tilt, with NO-BTC controls (Chainlink, future-judged)")
    print("=" * 104)

    # =====================================================================
    # D-FAMILY: BTC strongly determined late -> buy AGREEING alt favourites.
    # For each cut we run: single-pick (deepest-aligned alt), ALL-agree (more n),
    # and a NO-BTC CONTROL (same alt fav_ask/dist band, ignore BTC) so we can
    # see whether the BTC condition adds anything over "buy deep favourites".
    # =====================================================================
    for (tl_lo, tl_hi) in [(90, 180), (60, 150), (120, 300), (150, 420)]:
        snap = attach_cl(snapshot(b, tl_lo, tl_hi))
        alts = btc_alts(snap)
        for (ba, bd, aa_lo, aa_hi) in [(0.85, 20, 0.50, 1.0), (0.85, 20, 0.55, 0.97),
                                       (0.90, 25, 0.50, 1.0), (0.80, 15, 0.55, 0.97)]:
            strong = alts[(alts["btc_favask"] >= ba) & (alts["btc_dist"] >= bd)].copy()
            agree = strong[(strong["fav_yes"] == strong["btc_up"])
                           & strong["fav_ask"].between(aa_lo, aa_hi)].copy()
            tag = f"tl{tl_lo}-{tl_hi} btc{ba}/{bd} fa{aa_lo}-{aa_hi}"

            # ALL agreeing alts (each is a separate trade/window-coin)
            if not agree.empty:
                dec = dec_of(agree, agree["fav_yes"].to_numpy())
                report(f"D.ALLagree     {tag}", dec, R)

            # single-pick: the alt with the SHALLOWEST favourite that still
            # agrees (the laggard BTC says will resolve) — cheaper price, edge
            if not agree.empty:
                idx = agree.groupby("window_start_ts")["fav_ask"].idxmin()
                pick = agree.loc[idx]
                dec = dec_of(pick, pick["fav_yes"].to_numpy())
                report(f"D.lag1pick     {tag}", dec, R)

            # NO-BTC CONTROL: all alts in the SAME fav_ask/dist band, no BTC cond
            ctrl = alts[alts["fav_ask"].between(aa_lo, aa_hi)
                        & (alts["abs_dist_bps"] >= bd)].copy()
            if not ctrl.empty:
                dec = dec_of(ctrl, ctrl["fav_yes"].to_numpy())
                report(f"  ctrl.noBTC   {tag}", dec, R)

    # =====================================================================
    # D3-FADE: alt favourite DISAGREES with strongly-determined BTC -> the
    # alt is mispriced against the macro; buy the alt UNDERDOG (= BTC's side).
    # This is the cross-coin mean-reversion. Test ALL disagree + single-pick.
    # =====================================================================
    for (tl_lo, tl_hi) in [(90, 240), (60, 180), (120, 360)]:
        snap = attach_cl(snapshot(b, tl_lo, tl_hi))
        alts = btc_alts(snap)
        for (ba, bd) in [(0.80, 15), (0.85, 20), (0.75, 12)]:
            strong = alts[(alts["btc_favask"] >= ba) & (alts["btc_dist"] >= bd)].copy()
            dis = strong[strong["fav_yes"] != strong["btc_up"]].copy()
            tag = f"tl{tl_lo}-{tl_hi} btc{ba}/{bd}"
            if not dis.empty:
                # buy the alt underdog = BTC's side
                dec = dec_of(dis, dis["btc_up"].to_numpy())
                report(f"D3.ALLdisagree {tag}", dec, R)
            if len(dis):
                # single-pick: alt fav CLOSEST to strike (weakest vs BTC)
                idx = dis.groupby("window_start_ts")["abs_dist_bps"].idxmin()
                pick = dis.loc[idx]
                dec = dec_of(pick, pick["btc_up"].to_numpy())
                report(f"D3.weak1pick   {tag}", dec, R)

    # =====================================================================
    # A-FAMILY (carry the best v1 RV selection for completeness): the wider
    # late snapshot RV favourite-select had the strongest A future CI.
    # =====================================================================
    for (tl_lo, tl_hi) in [(180, 360), (150, 300)]:
        snap = attach_cl(snapshot(b, tl_lo, tl_hi))
        dev = snap[snap["split"] == "dev"]
        fair = fit_fair(dev)
        snap["fair"] = fair_of(snap["abs_dist_bps"].to_numpy(), fair, snap["fav_ask"].to_numpy())
        snap["fav_edge"] = snap["fair"] - snap["fav_ask"]
        cnt = snap.groupby("window_start_ts")["slug"].transform("count")
        s4 = snap[cnt >= 4].copy()
        for me in [0.0, 0.03]:
            idx = s4.groupby("window_start_ts")["fav_edge"].idxmax()
            pick = s4.loc[idx]
            pick = pick[pick["fav_edge"] >= me]
            dec = dec_of(pick, pick["fav_yes"].to_numpy())
            report(f"A.RVsel-favUP  tl{tl_lo}-{tl_hi} e>={me}", dec, R)

    # =====================================================================
    print("\n" + "=" * 104)
    print("RANKED BY future-split CI LOWER BOUND (the judge), tiebreak lat5:")
    print("=" * 104)
    ranked = sorted([r for r in R if r["fut_lo"] is not None],
                    key=lambda r: (r["fut_lo"], (r["lat5"] if r["lat5"] is not None else -9)),
                    reverse=True)
    for r in ranked[:14]:
        print(f"  {r['name']:40} fut_lo {r['fut_lo']:+.2f} fut_ev {r['fut_ev']:+.2f} "
              f"n{r['fut_n']:>3} | lat5 {r['lat5']} lat10 {r['lat10']} | "
              f"CPCV {r['cpcv']:.0f}% DSR {r['dsr']} | nFULL {r['e']['n']}")
    return R


if __name__ == "__main__":
    run()
