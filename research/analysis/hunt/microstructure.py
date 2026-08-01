"""HUNT — microstructure (Tier B, order-flow / book-imbalance).

Family thesis: short-horizon resolve side is predicted by order-flow microstructure
  - microprice vs yes_mid divergence (nd): does the mid drift toward microprice?
  - l2_imbalance (more bid depth -> buy pressure)
  - tr_signed_5s / tr_signed_usd taker bursts -> follow the aggressor
  - l2_depth_ask_2c thin-ask asymmetry

These are inherently latency-exposed: by the time a home trader SEES the burst the
ask has usually already repriced. So latency_survival is make-or-break. We test
honestly whether ANY config still pays at latency 5s on the FRESH-OOS (future) split.

Diagnostics (run separately) established, in the last 60s:
  - tr_signed_5s is FOLLOW-directional (P(up|+burst)=0.79) BUT already fully priced
    (median YES ask 0.94 on a +burst) -> naive follow EV ~ -0.3 to -0.5/trade.
  - nd / l2_imbalance are CONTRARIAN at the tick scale (book imbalance mean-reverts)
    but that mostly encodes price LEVEL (cheap YES = imbalanced book that resolves down).

So the only place a microstructure edge can live is a STALE-BOOK regime: flow/imbalance
present but the favourite ask NOT yet repriced (still cheap-ish), or a contrarian fade
of an over-extended imbalance at a controlled price. We sweep price bands, time-left
windows, thresholds, follow vs fade, and confluence. Each variant is judged by the
shared harness (Chainlink settle, window-clustered CI, latency sweep, CPCV, DSR).

Run: uv run python -m research.analysis.hunt.microstructure
"""
from __future__ import annotations
import numpy as np
import pandas as pd

from research.analysis import edge_lab as L


def _prep():
    b = L.load_base().copy()
    hs = (b["yes_best_ask"] - b["yes_best_bid"]) / 2.0
    # microprice divergence inside the spread, signed (+ = microprice above mid = YES pressure)
    b["nd"] = ((b["microprice"] - b["yes_mid"]) / hs).where(hs > 0, 0.0).clip(-1, 1)
    b["imb"] = b["l2_imbalance"] - 0.5  # >0 = more bid depth than ask depth
    # thin-ask asymmetry: small ask depth at 2c -> easy to push price (raw 2c-band ask $)
    return b


def _ll(b, lo, hi):
    return (b["time_left_sec"] >= lo) & (b["time_left_sec"] <= hi) & (b["book_healthy"] == True)


def run_variant(name, cand, buy_yes, *, full_eval=True):
    """cand = filtered ticks; buy_yes = bool array aligned to cand. Print verdict."""
    if len(cand) == 0:
        print(f"{name:46s} n=0 (no qualifying ticks)")
        return None
    dec = L.first_tick(cand, np.asarray(buy_yes))
    led = L.simulate(dec, latency=2)
    if led is None or len(led) == 0:
        print(f"{name:46s} n=0 (no fills@lat2)")
        return None
    lat = L.latency_survival(dec)
    if full_eval:
        e = L.evaluate(led)
        fl = e["per_split"]["FULL"]; fu = e["per_split"].get("future")
        cp = e["cpcv"].get("pct_pos", float("nan")); ds = e["dsr"]["dsr"]
        l2 = lat.get(2, {}); l5 = lat.get(5, {}); l10 = lat.get(10, {})
        futtot = fu["total"] if fu else float("nan")
        print(f"{name:46s} n={e['n']:>4} FULL ${fl['ev']:+.2f}[{fl['lo']:+.2f},{fl['hi']:+.2f}]WR{fl['wr']:.0f}%"
              + (f" | fut ${fu['ev']:+.2f}[{fu['lo']:+.2f},{fu['hi']:+.2f}]n{fu['n']}tot{futtot:+.0f}" if fu else " | fut n/a")
              + f" | CPCV{cp:.0f}% DSR{ds}")
        print(f"{'':46s} lat: 2s${l2.get('ev','-')}/fut{l2.get('fut_ev','-')}  "
              f"5s${l5.get('ev','-')}/fut{l5.get('fut_ev','-')}  10s${l10.get('ev','-')}/fut{l10.get('fut_ev','-')}")
        return dict(name=name, e=e, lat=lat)
    else:
        l5 = lat.get(5, {})
        print(f"{name:46s} n={len(led):>4} FULLev${led['pnl'].mean():+.2f} "
              f"lat5${l5.get('ev','-')}/fut{l5.get('fut_ev','-')}")
        return None


def main():
    b = _prep()
    print(f"loaded {len(b):,} ticks, {b['slug'].nunique()} windows\n")

    results = []

    # === GROUP 1: FOLLOW taker burst, but only when favourite ask is STILL CHEAP ===
    # thesis: flow predicts side; the edge survives only if the ask hasn't repriced
    # (cheap-ish), so we cap the entry price. Buy the burst side.
    print("--- G1: follow taker-flow burst, cap entry price (stale-book) ---")
    for ll in [(1, 60), (1, 120)]:
        d = b[_ll(b, *ll)]
        for thr in [10, 25]:
            for amax in [0.65, 0.80]:
                pos = (d["tr_signed_5s"] >= thr) & (d["yes_best_ask"] <= amax)
                neg = (d["tr_signed_5s"] <= -thr) & ((1 - d["yes_best_bid"]) <= amax)
                qual = pos | neg
                cand = d[qual]
                # buy YES on +burst, NO on -burst
                by = pos[qual].to_numpy()
                results.append(run_variant(
                    f"G1 follow ll{ll[1]} thr{thr} amax{amax}", cand, by))

    # === GROUP 2: microprice-divergence FOLLOW, price-capped ===
    print("\n--- G2: microprice>mid divergence FOLLOW, price-capped ---")
    for ll in [(1, 60), (60, 300)]:
        d = b[_ll(b, *ll)]
        for nthr in [0.4, 0.7]:
            for amax in [0.70, 0.85]:
                pos = (d["nd"] >= nthr) & (d["yes_best_ask"] <= amax)
                neg = (d["nd"] <= -nthr) & ((1 - d["yes_best_bid"]) <= amax)
                qual = pos | neg
                cand = d[qual]
                by = pos[qual].to_numpy()
                results.append(run_variant(
                    f"G2 nd-follow ll{ll[1]} nthr{nthr} amax{amax}", cand, by))

    # === GROUP 3: l2_imbalance FADE (book over-extended -> mean revert), price-ctrl ===
    # imb>0 (heavy bid) -> tick-scale reverts down -> buy NO (fade). And vice versa.
    print("\n--- G3: l2_imbalance FADE the over-extended book, price-ctrl ---")
    for ll in [(1, 60), (60, 300)]:
        d = b[_ll(b, *ll)]
        for ithr in [0.20, 0.30]:
            for aband in [(0.30, 0.70)]:
                # heavy bid (imb>+ithr): fade -> buy NO ; heavy ask (imb<-ithr): buy YES
                heavy_bid = (d["imb"] >= ithr) & (d["yes_mid"].between(*aband))
                heavy_ask = (d["imb"] <= -ithr) & (d["yes_mid"].between(*aband))
                qual = heavy_bid | heavy_ask
                cand = d[qual]
                # fade: heavy_bid -> buy NO (buy_yes False); heavy_ask -> buy YES (True)
                by = heavy_ask[qual].to_numpy()
                results.append(run_variant(
                    f"G3 imbFADE ll{ll[1]} ithr{ithr} mid{aband}", cand, by))

    # === GROUP 4: l2_imbalance FOLLOW (heavy bid -> up), price-ctrl ===
    print("\n--- G4: l2_imbalance FOLLOW, price-ctrl ---")
    for ll in [(1, 60), (60, 300)]:
        d = b[_ll(b, *ll)]
        for ithr in [0.25, 0.35]:
            for amax in [0.70, 0.85]:
                heavy_bid = (d["imb"] >= ithr) & (d["yes_best_ask"] <= amax)
                heavy_ask = (d["imb"] <= -ithr) & ((1 - d["yes_best_bid"]) <= amax)
                qual = heavy_bid | heavy_ask
                cand = d[qual]
                by = heavy_bid[qual].to_numpy()
                results.append(run_variant(
                    f"G4 imbFOLLOW ll{ll[1]} ithr{ithr} amax{amax}", cand, by))

    # === GROUP 5: CONFLUENCE (flow + imbalance agree), follow, price-capped ===
    print("\n--- G5: confluence flow+imbalance agree, follow, price-capped ---")
    for ll in [(1, 60), (1, 120)]:
        d = b[_ll(b, *ll)]
        for amax in [0.75, 0.88]:
            pos = (d["tr_signed_5s"] >= 10) & (d["imb"] >= 0.15) & (d["yes_best_ask"] <= amax)
            neg = (d["tr_signed_5s"] <= -10) & (d["imb"] <= -0.15) & ((1 - d["yes_best_bid"]) <= amax)
            qual = pos | neg
            cand = d[qual]
            by = pos[qual].to_numpy()
            results.append(run_variant(
                f"G5 confluence ll{ll[1]} amax{amax}", cand, by))

    # === GROUP 6: thin-ask pickoff — small l2_depth_ask_2c + flow agree -> follow ===
    print("\n--- G6: thin-ask (l2_depth_ask_2c small) + flow follow ---")
    for ll in [(1, 60)]:
        d = b[_ll(b, *ll)]
        for d2c in [30, 80]:
            for amax in [0.80]:
                thin = d["l2_depth_ask_2c"] <= d2c
                pos = thin & (d["tr_signed_5s"] >= 5) & (d["yes_best_ask"] <= amax)
                neg = thin & (d["tr_signed_5s"] <= -5) & ((1 - d["yes_best_bid"]) <= amax)
                qual = pos | neg
                cand = d[qual]
                by = pos[qual].to_numpy()
                results.append(run_variant(
                    f"G6 thinask d2c{d2c} amax{amax}", cand, by))

    # === GROUP 7: FADE the taker burst (aggressor over-extends favourite) ===
    # +burst pushes YES ask up -> fade by buying NO; -burst -> buy YES. Price-ctrl.
    print("\n--- G7: FADE taker burst (mean-revert the over-extension) ---")
    for ll in [(1, 60), (60, 300), (300, 700)]:
        d = b[_ll(b, *ll)]
        for thr in [10, 25]:
            for mid in [(0.30, 0.70), (0.20, 0.80)]:
                # +burst -> price too high -> buy NO ; -burst -> buy YES
                up_burst = (d["tr_signed_5s"] >= thr) & (d["yes_mid"].between(*mid))
                dn_burst = (d["tr_signed_5s"] <= -thr) & (d["yes_mid"].between(*mid))
                qual = up_burst | dn_burst
                cand = d[qual]
                by = dn_burst[qual].to_numpy()  # fade: dn_burst -> buy YES
                results.append(run_variant(
                    f"G7 fadeFlow ll{ll[1]} thr{thr} mid{mid}", cand, by))

    # === GROUP 8: confluence FADE (flow+imbalance BOTH over-extended) ===
    print("\n--- G8: confluence FADE (flow & imb both extended), price-ctrl ---")
    for ll in [(1, 60), (60, 300)]:
        d = b[_ll(b, *ll)]
        for mid in [(0.30, 0.70), (0.25, 0.75)]:
            up_ext = (d["tr_signed_5s"] >= 10) & (d["imb"] >= 0.15) & (d["yes_mid"].between(*mid))
            dn_ext = (d["tr_signed_5s"] <= -10) & (d["imb"] <= -0.15) & (d["yes_mid"].between(*mid))
            qual = up_ext | dn_ext
            cand = d[qual]
            by = dn_ext[qual].to_numpy()  # fade: dn_ext -> buy YES
            results.append(run_variant(
                f"G8 confFADE ll{ll[1]} mid{mid}", cand, by))

    # === GROUP 9: imbalance FADE, tighter mid band + last-60s sweep of ithr ===
    # the one live signal (G3) was last-60s, mid 0.3-0.7. Push it: vary band & ithr.
    print("\n--- G9: imbFADE last-window sweep (the one live signal) ---")
    for ll in [(1, 45), (1, 90)]:
        d = b[_ll(b, *ll)]
        for ithr in [0.15, 0.25, 0.35]:
            for mid in [(0.35, 0.65), (0.25, 0.75)]:
                hb = (d["imb"] >= ithr) & (d["yes_mid"].between(*mid))
                ha = (d["imb"] <= -ithr) & (d["yes_mid"].between(*mid))
                qual = hb | ha
                cand = d[qual]
                by = ha[qual].to_numpy()  # fade heavy-bid -> buy NO; heavy-ask -> buy YES
                results.append(run_variant(
                    f"G9 imbFADE ll{ll[1]} ithr{ithr} mid{mid}", cand, by))

    # ---- summary: rank by future CI lower bound ----
    print("\n\n=== RANKED by future-split CI lower bound (the judge) ===")
    rows = []
    for r in results:
        if r is None:
            continue
        fu = r["e"]["per_split"].get("future")
        if not fu:
            continue
        l5 = r["lat"].get(5, {})
        rows.append((fu["lo"], fu["ev"], fu["hi"], fu["n"], r["e"]["n"],
                     r["e"]["per_split"]["FULL"]["ev"], l5.get("ev"), l5.get("fut_ev"),
                     r["e"]["cpcv"].get("pct_pos"), r["e"]["dsr"]["dsr"], r["name"]))
    rows.sort(reverse=True)
    print(f"{'fut_lo':>7} {'fut_ev':>7} {'fut_hi':>7} {'futn':>5} {'n':>4} {'FULLev':>7} "
          f"{'l5ev':>6} {'l5fut':>6} {'cpcv':>5} {'dsr':>6}  name")
    for lo, ev, hi, fn, n, fev, l5e, l5f, cp, ds, nm in rows[:20]:
        print(f"{lo:>+7.2f} {ev:>+7.2f} {hi:>+7.2f} {fn:>5} {n:>4} {fev:>+7.2f} "
              f"{(l5e if l5e is not None else float('nan')):>+6.2f} "
              f"{(l5f if l5f is not None else float('nan')):>+6.2f} "
              f"{(cp if cp is not None else float('nan')):>5.0f} {ds:>6}  {nm}")


if __name__ == "__main__":
    main()
