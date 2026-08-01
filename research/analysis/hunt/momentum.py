"""HUNT: late-window MOMENTUM continuation (Tier B).

Base E-mom: last<=60s, spot moving AWAY from strike (sign(spot_vel_10s)==sign(dist),
i.e. the favourite's lead is GROWING / adverse_vel_10s<0), buy the favourite at
fav_ask in [0.50,0.90], hold to Chainlink resolution.

Mechanism claim: when momentum reinforces the favourite's lead in the final minute,
the outcome is MORE locked than the book prices imply -> positive net EV on the
favourite. Base is +0.55 FULL but ~flat/neg fresh-OOS (future) and the latency sweep
already kills it. This hunt tries to RESCUE the fresh-OOS via regime/condition
filters WITHOUT curve-fitting, judging ONLY by:
  - future (fresh-OOS) split CI lower bound > 0
  - latency-surviving: it fires late, so it must still pay at 5s (Tier B)
  - CPCV pct_pos >= ~70%, DSR > 0, n >= ~40, not 1-2 jackpots.

Every variant prints n / FULL ev+CI / future ev+CI / latency EVs / cpcv / dsr.
Run: uv run python -m research.analysis.hunt.momentum
"""
from __future__ import annotations
import numpy as np
import pandas as pd

from research.analysis import edge_lab as L


def _fmt_lat(lat: dict) -> str:
    parts = []
    for k, v in lat.items():
        if v.get("ev") is None:
            parts.append(f"{k}s -")
        else:
            fe = v.get("fut_ev")
            fe_s = f"/{fe:+.2f}" if fe is not None else "/na"
            parts.append(f"{k}s {v['ev']:+.2f}{fe_s}(n{v['n']})")
    return "  ".join(parts)


def run_variant(name: str, cand: pd.DataFrame, buy_yes, *, lat_at=(2, 3, 5, 10)) -> dict:
    """Evaluate one variant. buy_yes aligned to cand. Returns a summary dict and
    prints the full line (n, FULL CI, future CI, latency sweep, cpcv, dsr)."""
    if cand is None or len(cand) == 0:
        print(f"{name:40s} EMPTY (no qualifying ticks)")
        return dict(name=name, n=0)
    dec = L.first_tick(cand, np.asarray(buy_yes))
    led = L.simulate(dec, latency=2)
    e = L.evaluate(led)
    if e.get("n", 0) == 0:
        print(f"{name:40s} n=0 (no fills after depth gate)")
        return dict(name=name, n=0)
    fl = e["per_split"].get("FULL")
    if fl is None:
        print(f"{name:40s} n={e['n']:>4} (FULL<3 rows, skipped)")
        return dict(name=name, n=e["n"])
    fu = e["per_split"].get("future")
    lat = L.latency_survival(dec, latencies=lat_at)
    cpcv = e["cpcv"].get("pct_pos", float("nan"))
    dsr = e["dsr"].get("dsr", float("nan"))
    fu_s = (f"fut ${fu['ev']:+.2f}[{fu['lo']:+.2f},{fu['hi']:+.2f}]n{fu['n']}wr{fu['wr']:.0f}"
            if fu else "fut n/a")
    print(f"{name:40s} n={e['n']:>4} FULL ${fl['ev']:+.2f}[{fl['lo']:+.2f},{fl['hi']:+.2f}]"
          f"wr{fl['wr']:.0f} tot{fl['total']:+.0f} | {fu_s} | CPCV {cpcv:.0f}% DSR {dsr}")
    print(f"      lat(ev/fut): {_fmt_lat(lat)}")
    # judge
    fut_lo = fu["lo"] if fu else float("nan")
    lat5 = lat.get(5, {}).get("ev")
    lat5_fut = lat.get(5, {}).get("fut_ev")
    keep = (fu is not None and fut_lo > 0 and lat5 is not None and lat5 > 0
            and (lat5_fut is None or lat5_fut > 0) and cpcv >= 70 and dsr > 0
            and e["n"] >= 40)
    return dict(name=name, n=e["n"], full_ev=fl["ev"], full_lo=fl["lo"], full_hi=fl["hi"],
                full_total=fl["total"], fut_ev=(fu["ev"] if fu else None),
                fut_lo=fut_lo, fut_hi=(fu["hi"] if fu else None), fut_n=(fu["n"] if fu else 0),
                lat=lat, lat5=lat5, lat5_fut=lat5_fut, lat2=lat.get(2, {}).get("ev"),
                lat10=lat.get(10, {}).get("ev"), cpcv=cpcv, dsr=dsr, wr=fl["wr"],
                keep=bool(keep), dec=dec, led=led)


def main():
    b = L.load_base()
    print(f"base rows {len(b):,} splits {b.split.value_counts().to_dict()}\n")

    results = []

    # Common momentum primitives:
    #   mom_away: spot velocity reinforces the favourite's lead (lead growing).
    #             adverse_vel_10s<0 == sign(spot_vel_10s)==sign(dist). require nonzero.
    #   We always buy the FAVOURITE (buy_yes = fav_side=='yes').
    def base_filter(df):
        return df["consistent"] & (df["spot_vel_10s_bps"] != 0) & (df["adverse_vel_10s"] < 0)

    print("="*120)
    print("GROUP 0 — anchor: plain E-mom (last<=60s, mom-away, fav_ask 0.50-0.90)")
    print("="*120)
    c = b[(b.time_left_sec >= 1) & (b.time_left_sec <= 60) & base_filter(b)
          & b.fav_ask.between(0.50, 0.90)]
    results.append(run_variant("0 E-mom base 60s 0.50-0.90", c, (c.fav_side == "yes").to_numpy()))

    print("\n" + "="*120)
    print("GROUP 1 — TIME-LEFT band (does an earlier/later decision help fresh-OOS?)")
    print("="*120)
    for lo, hi in [(1, 30), (1, 45), (1, 60), (15, 60), (30, 90), (1, 90), (1, 120), (60, 120)]:
        c = b[(b.time_left_sec >= lo) & (b.time_left_sec <= hi) & base_filter(b)
              & b.fav_ask.between(0.50, 0.90)]
        results.append(run_variant(f"1 time_left {lo}-{hi}s", c, (c.fav_side == "yes").to_numpy()))

    print("\n" + "="*120)
    print("GROUP 2 — fav_ask band (cheap favourites vs expensive; book-lag concentrates cheap)")
    print("="*120)
    for alo, ahi in [(0.50, 0.70), (0.55, 0.75), (0.60, 0.80), (0.50, 0.80),
                     (0.65, 0.85), (0.70, 0.90), (0.55, 0.95), (0.50, 0.95)]:
        c = b[(b.time_left_sec >= 1) & (b.time_left_sec <= 60) & base_filter(b)
              & b.fav_ask.between(alo, ahi)]
        results.append(run_variant(f"2 fav_ask {alo:.2f}-{ahi:.2f}", c, (c.fav_side == "yes").to_numpy()))

    print("\n" + "="*120)
    print("GROUP 3 — MOMENTUM MAGNITUDE (require strong reinforcing velocity)")
    print("="*120)
    # |spot_vel_10s| thresholds; mom-away already enforced by base_filter
    for vmin in [2, 4, 6, 8, 12, 16]:
        c = b[(b.time_left_sec >= 1) & (b.time_left_sec <= 60) & base_filter(b)
              & b.fav_ask.between(0.50, 0.90) & (b.spot_vel_10s_bps.abs() >= vmin)]
        results.append(run_variant(f"3 |vel10|>={vmin}bps", c, (c.fav_side == "yes").to_numpy()))
    # also short-horizon velocity agreeing (3s and 10s same sign reinforcing)
    for vmin in [4, 8, 12]:
        c = b[(b.time_left_sec >= 1) & (b.time_left_sec <= 60) & base_filter(b)
              & b.fav_ask.between(0.50, 0.90) & (b.spot_vel_10s_bps.abs() >= vmin)
              & (b.adverse_vel_3s < 0)]
        results.append(run_variant(f"3 |vel10|>={vmin}&vel3-away", c, (c.fav_side == "yes").to_numpy()))

    print("\n" + "="*120)
    print("GROUP 4 — DISTANCE band (how far spot already is from strike)")
    print("="*120)
    for dlo, dhi in [(5, 1e9), (8, 1e9), (12, 1e9), (5, 20), (8, 25), (12, 40), (20, 1e9)]:
        c = b[(b.time_left_sec >= 1) & (b.time_left_sec <= 60) & base_filter(b)
              & b.fav_ask.between(0.50, 0.90) & b.abs_dist_bps.between(dlo, dhi)]
        results.append(run_variant(f"4 dist {dlo}-{dhi}bps", c, (c.fav_side == "yes").to_numpy()))

    print("\n" + "="*120)
    print("GROUP 5 — REALIZED-VOL regime (momentum may only continue in calm/trendy tape)")
    print("="*120)
    rv = b["realized_vol"]
    q = rv[rv.notna()].quantile([0.33, 0.5, 0.66]).to_dict()
    print(f"   realized_vol quantiles 33/50/66 = {q}")
    lo33, med, hi66 = q[0.33], q[0.5], q[0.66]
    for label, mask in [("rv_low(<=q33)", rv <= lo33), ("rv_mid", (rv > lo33) & (rv <= hi66)),
                        ("rv_high(>q66)", rv > hi66), ("rv<=med", rv <= med), ("rv>med", rv > med)]:
        c = b[(b.time_left_sec >= 1) & (b.time_left_sec <= 60) & base_filter(b)
              & b.fav_ask.between(0.50, 0.90) & mask]
        results.append(run_variant(f"5 {label}", c, (c.fav_side == "yes").to_numpy()))

    print("\n" + "="*120)
    print("GROUP 6 — UTC SESSION (Asia/EU/US; momentum continuation may be session-specific)")
    print("="*120)
    h = b["utc_hour"]
    sessions = {
        "ASIA(0-7)": (h >= 0) & (h <= 7),
        "EU(7-13)": (h >= 7) & (h <= 13),
        "US(13-21)": (h >= 13) & (h <= 21),
        "LATE_US(21-24)": (h >= 21) & (h <= 23),
        "OFFHRS(0-13)": (h >= 0) & (h <= 13),
        "ACTIVE(13-24)": (h >= 13) & (h <= 23),
    }
    for label, mask in sessions.items():
        c = b[(b.time_left_sec >= 1) & (b.time_left_sec <= 60) & base_filter(b)
              & b.fav_ask.between(0.50, 0.90) & mask]
        results.append(run_variant(f"6 {label}", c, (c.fav_side == "yes").to_numpy()))

    print("\n" + "="*120)
    print("GROUP 7 — ORDER-FLOW confirmation (trade flow agreeing with the favourite)")
    print("="*120)
    # tr_signed_5s>0 means net buying of YES. Favourite-aligned flow: buying YES if
    # fav is yes, selling YES (tr<0) if fav is no.
    fav_yes = b["fav_side"] == "yes"
    flow_with_fav = (fav_yes & (b["tr_signed_5s"] > 0)) | (~fav_yes & (b["tr_signed_5s"] < 0))
    for label, mask in [("flow_with_fav", flow_with_fav),
                        ("flow_with_fav|0", flow_with_fav | (b["tr_signed_5s"] == 0))]:
        c = b[(b.time_left_sec >= 1) & (b.time_left_sec <= 60) & base_filter(b)
              & b.fav_ask.between(0.50, 0.90) & mask]
        results.append(run_variant(f"7 {label}", c, (c.fav_side == "yes").to_numpy()))
    # l2 imbalance leaning toward favourite
    imb_with_fav = (fav_yes & (b["l2_imbalance"] > 0)) | (~fav_yes & (b["l2_imbalance"] < 0))
    c = b[(b.time_left_sec >= 1) & (b.time_left_sec <= 60) & base_filter(b)
          & b.fav_ask.between(0.50, 0.90) & imb_with_fav]
    results.append(run_variant("7 l2imb_with_fav", c, (c.fav_side == "yes").to_numpy()))

    print("\n" + "="*120)
    print("GROUP 8 — COMBOS (best single filters stacked, watching for overfit/n-collapse)")
    print("="*120)
    combos = {
        "8 vel>=6 & dist>=8": base_filter(b) & b.fav_ask.between(0.50, 0.90)
            & (b.spot_vel_10s_bps.abs() >= 6) & (b.abs_dist_bps >= 8),
        "8 vel>=6 & ask0.55-0.80": base_filter(b) & b.fav_ask.between(0.55, 0.80)
            & (b.spot_vel_10s_bps.abs() >= 6),
        "8 vel>=8 & rv>med": base_filter(b) & b.fav_ask.between(0.50, 0.90)
            & (b.spot_vel_10s_bps.abs() >= 8) & (rv > med),
        "8 vel>=6 & ASIA": base_filter(b) & b.fav_ask.between(0.50, 0.90)
            & (b.spot_vel_10s_bps.abs() >= 6) & (h >= 0) & (h <= 7),
        "8 vel>=6 & US": base_filter(b) & b.fav_ask.between(0.50, 0.90)
            & (b.spot_vel_10s_bps.abs() >= 6) & (h >= 13) & (h <= 21),
        "8 vel>=6 & flow_fav": base_filter(b) & b.fav_ask.between(0.50, 0.90)
            & (b.spot_vel_10s_bps.abs() >= 6) & flow_with_fav,
        "8 vel>=6&dist>=8&ask<=0.80": base_filter(b) & b.fav_ask.between(0.50, 0.80)
            & (b.spot_vel_10s_bps.abs() >= 6) & (b.abs_dist_bps >= 8),
        "8 vel>=8&vel3away&ask<=0.85": base_filter(b) & b.fav_ask.between(0.50, 0.85)
            & (b.spot_vel_10s_bps.abs() >= 8) & (b.adverse_vel_3s < 0),
    }
    for label, mask in combos.items():
        c = b[(b.time_left_sec >= 1) & (b.time_left_sec <= 60) & mask]
        results.append(run_variant(label, c, (c.fav_side == "yes").to_numpy()))

    print("\n" + "="*120)
    print("GROUP 9 — EARLIER WINDOW (60-120s, the ONLY latency-flat base) + regime filters")
    print("          (best chance to lift the future CI lower bound while staying latency-robust)")
    print("="*120)
    early = (b.time_left_sec >= 60) & (b.time_left_sec <= 120) & base_filter(b)
    g9 = {
        "9 early base 0.50-0.90": early & b.fav_ask.between(0.50, 0.90),
        "9 early ask<=0.85": early & b.fav_ask.between(0.50, 0.85),
        "9 early ask 0.55-0.90": early & b.fav_ask.between(0.55, 0.90),
        "9 early dist>=8": early & b.fav_ask.between(0.50, 0.90) & (b.abs_dist_bps >= 8),
        "9 early dist>=12": early & b.fav_ask.between(0.50, 0.90) & (b.abs_dist_bps >= 12),
        "9 early vel>=4": early & b.fav_ask.between(0.50, 0.90) & (b.spot_vel_10s_bps.abs() >= 4),
        "9 early vel>=6": early & b.fav_ask.between(0.50, 0.90) & (b.spot_vel_10s_bps.abs() >= 6),
        "9 early rv>med": early & b.fav_ask.between(0.50, 0.90) & (rv > med),
        "9 early rv>q66": early & b.fav_ask.between(0.50, 0.90) & (rv > hi66),
        "9 early OFFHRS": early & b.fav_ask.between(0.50, 0.90) & (h >= 0) & (h <= 13),
        "9 early dist>=8&rv>med": early & b.fav_ask.between(0.50, 0.90) & (b.abs_dist_bps >= 8) & (rv > med),
        "9 early dist>=8&OFFHRS": early & b.fav_ask.between(0.50, 0.90) & (b.abs_dist_bps >= 8) & (h >= 0) & (h <= 13),
        "9 early vel>=4&OFFHRS": early & b.fav_ask.between(0.50, 0.90) & (b.spot_vel_10s_bps.abs() >= 4) & (h >= 0) & (h <= 13),
    }
    for label, mask in g9.items():
        c = b[mask]
        results.append(run_variant(label, c, (c.fav_side == "yes").to_numpy()))

    print("\n" + "="*120)
    print("GROUP 10 — other regimes: CL-CB basis, depth, per-symbol (on latency-flat early window)")
    print("="*120)
    g10 = {
        # basis: chainlink leading/lagging coinbase. |basis| small = oracle aligned.
        "10 early |basis|<=5": early & b.fav_ask.between(0.50, 0.90) & (b.cl_cb_basis_bps.abs() <= 5),
        "10 early |basis|>5": early & b.fav_ask.between(0.50, 0.90) & (b.cl_cb_basis_bps.abs() > 5),
        # deep book = more liquid/efficient; thin = laggier
        "10 early depth>=median": early & b.fav_ask.between(0.50, 0.90) & (b.depth_usd >= b.depth_usd.median()),
        "10 early depth<median": early & b.fav_ask.between(0.50, 0.90) & (b.depth_usd < b.depth_usd.median()),
    }
    for label, mask in g10.items():
        c = b[mask]
        results.append(run_variant(label, c, (c.fav_side == "yes").to_numpy()))
    for sym in ["btc", "eth", "sol", "xrp"]:
        c = b[early & b.fav_ask.between(0.50, 0.90) & (b.symbol == sym)]
        results.append(run_variant(f"10 early {sym}", c, (c.fav_side == "yes").to_numpy()))

    # ---- SUMMARY ranked by fresh-OOS (future) CI lower bound ----
    print("\n" + "#"*120)
    print("SUMMARY — ranked by FUTURE (fresh-OOS) CI lower bound (the judge)")
    print("#"*120)
    rk = [r for r in results if r.get("n", 0) > 0 and r.get("fut_lo") is not None]
    rk.sort(key=lambda r: (r["fut_lo"] if r["fut_lo"] is not None else -99), reverse=True)
    print(f"{'variant':40s} {'n':>4} {'fut_ev':>7} {'fut_lo':>7} {'fut_hi':>7} {'futN':>4} "
          f"{'lat5':>6} {'l5fut':>6} {'full_ev':>7} {'cpcv':>5} {'dsr':>6} keep")
    for r in rk[:20]:
        print(f"{r['name']:40s} {r['n']:>4} {r['fut_ev']:>+7.2f} {r['fut_lo']:>+7.2f} "
              f"{r['fut_hi']:>+7.2f} {r['fut_n']:>4} "
              f"{(r['lat5'] if r['lat5'] is not None else float('nan')):>+6.2f} "
              f"{(r['lat5_fut'] if r['lat5_fut'] is not None else float('nan')):>+6.2f} "
              f"{r['full_ev']:>+7.2f} {r['cpcv']:>5.0f} {r['dsr']:>6} {'KEEP' if r['keep'] else ''}")

    keeps = [r for r in results if r.get("keep")]
    print(f"\nKEEP candidates (future CI lo>0 AND lat5>0 AND cpcv>=70 AND dsr>0 AND n>=40): "
          f"{[r['name'] for r in keeps] if keeps else 'NONE'}")
    return results


if __name__ == "__main__":
    main()
