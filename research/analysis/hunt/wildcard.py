"""hunt/wildcard — Tier-A out-of-the-box edges NOT covered by the other families.

A laptop trader with ZERO speed advantage; decide with a buffer, hold to
resolution, settle on CHAINLINK, judged on the fresh-OOS (future) split.

Two-pass for speed (the shared bootstrap+CPCV+DSR is ~20s/variant; we have ~40):
  PASS 1 (cheap screen): every variant -> depth-gated Chainlink fill at latency 2
    AND 5 -> per-split EV + FUTURE window-clustered CI (n=1500). Rank by future CI
    lower bound. Uses a (slug,sec)-indexed book for O(1) fills (no per-call merge).
  PASS 2 (full gauntlet): the top finalists -> shared L.evaluate (per-split CIs,
    CPCV pct_pos, daily DSR) + full L.latency_survival(2,3,5,10).

All economics identical to edge_lab (single taker fee, hold to resolution,
Chainlink outcome). Pass-1 fill reuses the SAME ok-gate as L.simulate, so a
finalist's pass-1 EV reconciles with its pass-2 EV.

Ideas (Tier A = decide with >=10s buffer; EV must be ~flat 2s->10s):
  1 favourite-longshot calibration (buy fav, mid-window, by fav_ask band)
  2 low-realized-vol favourite "over-determined / underpriced"
  3 wide spread_yes mean-reversion (buy the mid-implied side)
  4 big cushion (proximity_z) + low vol = near-locked favourite underpriced
  5 calendar microstructure (UTC-hour / session directional + favourite bias)
  6 proximity_z x time_left interaction (normalized cushion buckets)
  7 end-of-window favourite drift, decided early (>=120s buffer)
  8 deep favourite over/under-determination (fav_ask 0.85-0.97)
  9 cl_cb_basis structural tilt (oracle vs book, buffered, agree & disagree)
 10 underdog (anti-favourite) longshot tail (calibration value)

Run: uv run python -m research.analysis.hunt.wildcard
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from research.analysis import edge_lab as L
from research.lib.stats import window_clustered_bootstrap

STAKE = L.STAKE
FEE = L.FEE


# --------------------------------------------------------------------------
# fast fill: index the book by (slug, sec) once -> O(1) lookups, no merge.
# Reuses L.simulate's exact ok-gate + economics so EV reconciles.
# --------------------------------------------------------------------------
_BOOK = None
_CL = None


def _book():
    global _BOOK
    if _BOOK is None:
        bk = L._book_index().set_index(["slug", "seconds_into_window"]).sort_index()
        _BOOK = bk
    return _BOOK


def _cl():
    global _CL
    if _CL is None:
        _CL = L.cl_outcomes().set_index("slug")["cl_up"]
    return _CL


def fast_sim(dec: pd.DataFrame, latency: int) -> pd.DataFrame:
    """Chainlink-settled ledger via O(1) indexed-book lookup. Same gate/economics
    as L.simulate (best-ask depth>=stake, healthy, 0.01<ask<0.99)."""
    if dec is None or dec.empty:
        return pd.DataFrame()
    d = dec.dropna(subset=["entry_sec"]).copy()
    d["entry_sec"] = d["entry_sec"].astype(int)
    d["fill_sec"] = d["entry_sec"] + int(latency)
    bk = _book()
    keys = list(zip(d["slug"], d["fill_sec"]))
    idx = bk.index
    mask = idx.isin(keys)  # not used directly; reindex below
    rows = bk.reindex(keys)
    rows.index = d.index
    m = d.join(rows[["yes_best_ask", "yes_best_bid", "yes_ask_depth",
                     "no_ask_depth", "book_healthy"]])
    buy_yes = m["buy_yes"].astype(bool).to_numpy()
    ask = np.where(buy_yes, m["yes_best_ask"].to_numpy("f8"),
                   1.0 - m["yes_best_bid"].to_numpy("f8"))
    depth_sh = np.where(buy_yes, m["yes_ask_depth"].to_numpy("f8"),
                        m["no_ask_depth"].to_numpy("f8"))
    depth_usd = depth_sh * ask
    ok = ((m["book_healthy"] == True).to_numpy() & np.isfinite(ask)
          & (ask > 0.01) & (ask < 0.99) & (depth_usd >= STAKE))
    m = m[ok].copy()
    if m.empty:
        return m
    m["entry_ask"] = ask[ok]
    clmap = _cl()
    m["cl_up"] = m["slug"].map(clmap)
    m = m[m["cl_up"].notna()].copy()
    if m.empty:
        return m
    by = m["buy_yes"].astype(bool).to_numpy()
    m["won"] = np.where(by, m["cl_up"].to_numpy() == 1, m["cl_up"].to_numpy() == 0).astype(int)
    a = m["entry_ask"].to_numpy("f8")
    shares = STAKE / a
    fee = FEE * a * (1 - a) * shares
    m["pnl"] = np.where(m["won"] == 1, shares - STAKE - fee, -STAKE - fee)
    return m


def screen(name: str, build, store: list):
    """Cheap pass-1: materialize the candidate (one at a time), build the decision
    frame, then FULL+future EV at latency 2 & 5 and the future CI (n=1500).
    Streams one line per variant."""
    cand, buy_yes = build()
    if cand is None or len(cand) == 0:
        print(f"{name:34} (empty candidate)", flush=True)
        return
    dec = L.first_tick(cand, buy_yes)
    if dec is None or dec.empty or len(dec) < 3:
        print(f"{name:34} n={0 if dec is None else len(dec):>4}  (too few windows)", flush=True)
        return
    l2 = fast_sim(dec, 2)
    if l2 is None or len(l2) < 3:
        print(f"{name:34} fills={0 if l2 is None else len(l2):>4}  (too few fills)")
        return
    l5 = fast_sim(dec, 5)
    fu2 = l2[l2["split"] == "future"]
    full_ev = float(l2["pnl"].mean())
    full_tot = float(l2["pnl"].sum())
    fut_ev = float(fu2["pnl"].mean()) if len(fu2) else float("nan")
    fut_n = int(len(fu2))
    fl5 = float(l5["pnl"].mean()) if len(l5) else float("nan")
    fu5 = l5[l5["split"] == "future"]
    fut5 = float(fu5["pnl"].mean()) if len(fu5) else float("nan")
    if fut_n >= 3:
        flo, _, fhi = window_clustered_bootstrap(fu2["pnl"].values, fu2["slug"].values, n=1500)
    else:
        flo = fhi = float("nan")
    print(f"{name:34} n={len(l2):>4} FULL ${full_ev:+.2f}(l5${fl5:+.2f}) WR{l2['won'].mean()*100:.0f}%"
          f" | fut ${fut_ev:+.2f}[{flo:+.2f},{fhi:+.2f}](l5${fut5:+.2f})n{fut_n}"
          f" | tot{full_tot:+.0f}", flush=True)
    store.append(dict(name=name, dec=dec, full_ev=full_ev, fut_ev=fut_ev,
                      fut_lo=flo, fut_hi=fhi, fut_n=fut_n, full_n=len(l2),
                      fl5=fl5, fut5=fut5, l2=l2))


def build_variants(b: pd.DataFrame):
    """Yield (name, candidate_frame, buy_yes_array) lazily so the screen streams.
    first_tick + masking are deferred to the screen loop via thunks, so each
    candidate frame is materialized one-at-a-time (streaming, bounded memory).
    Requires derived cols precomputed on b: vol_bps, proximity_z, cl_ok,
    cl_dist_bps, cl_up_now, cb_up_now."""

    def mk(maskfn, sidefn):
        def build():
            c = b[maskfn(b)]
            return c, sidefn(c)
        return build

    # (1) favourite-longshot calibration: buy favourite mid-window by fav_ask band
    for lo, hi in [(0.50, 0.60), (0.55, 0.70), (0.60, 0.75), (0.70, 0.85),
                   (0.80, 0.92), (0.55, 0.85), (0.50, 0.97)]:
        yield (f"(1)fav_mid[{lo:.2f},{hi:.2f}]", mk(
            lambda b, lo=lo, hi=hi: (b["time_left_sec"].between(240, 420) & b["book_healthy"]
                                     & b["fav_ask"].between(lo, hi)),
            lambda c: (c["fav_side"] == "yes").to_numpy()))

    # (2) low-vol favourite over-determined: buy fav when realized_vol LOW
    for vmax, dmin in [(0.5, 4), (0.7, 5), (0.7, 8), (1.0, 6), (1.0, 10)]:
        yield (f"(2)lowvol_fav v<={vmax}d>={dmin}", mk(
            lambda b, vmax=vmax, dmin=dmin: (b["time_left_sec"].between(240, 420) & b["book_healthy"]
                                             & (b["vol_bps"] <= vmax) & (b["abs_dist_bps"] >= dmin)
                                             & b["fav_ask"].between(0.55, 0.90)),
            lambda c: (c["fav_side"] == "yes").to_numpy()))

    # (3) wide-spread mean-reversion: buy the mid-implied side when spread WIDE
    for smin in [0.03, 0.05, 0.08]:
        for tl in [(240, 420), (120, 600)]:
            yield (f"(3)widesprd s>={smin}t{tl[0]}-{tl[1]}", mk(
                lambda b, smin=smin, tl=tl: (b["time_left_sec"].between(*tl) & b["book_healthy"]
                                             & (b["spread_yes"] >= smin)),
                lambda c: (c["yes_mid"] >= 0.5).to_numpy()))

    # (4) big cushion (proximity_z) + low vol = near-locked favourite underpriced
    for pzmin, vmax in [(1.5, 1.5), (2.0, 1.0), (2.5, 1.5), (3.0, 2.0)]:
        yield (f"(4)cushion pz>={pzmin}v<={vmax}", mk(
            lambda b, pzmin=pzmin, vmax=vmax: (b["time_left_sec"].between(180, 480) & b["book_healthy"]
                                               & (b["proximity_z"] >= pzmin) & (b["vol_bps"] <= vmax)
                                               & b["fav_ask"].between(0.55, 0.95)),
            lambda c: (c["fav_side"] == "yes").to_numpy()))

    # (5) calendar: per-UTC-hour buy-UP bias (buffered)
    for h in range(0, 24, 3):
        yield (f"(5)hourUP h{h}-{h+2}", mk(
            lambda b, h=h: (b["time_left_sec"].between(240, 600) & b["book_healthy"]
                            & b["utc_hour"].between(h, h + 2)),
            lambda c: np.ones(len(c), dtype=bool)))

    # (5b) per-session buy-FAVOURITE (does the fav edge concentrate by session)
    for h0, h1, tag in [(0, 7, "asia"), (8, 15, "eu"), (13, 20, "us"), (20, 23, "late")]:
        yield (f"(5b)favsess {tag}{h0}-{h1}", mk(
            lambda b, h0=h0, h1=h1: (b["time_left_sec"].between(240, 600) & b["book_healthy"]
                                     & b["utc_hour"].between(h0, h1) & b["fav_ask"].between(0.55, 0.85)),
            lambda c: (c["fav_side"] == "yes").to_numpy()))

    # (6) proximity_z x time_left: normalized cushion buckets (buy fav)
    for pzlo, pzhi in [(0.5, 1.5), (1.0, 2.5), (1.5, 3.5), (0.0, 1.0)]:
        yield (f"(6)proxZ[{pzlo},{pzhi}]", mk(
            lambda b, pzlo=pzlo, pzhi=pzhi: (b["time_left_sec"].between(300, 600) & b["book_healthy"]
                                             & b["proximity_z"].between(pzlo, pzhi)
                                             & b["fav_ask"].between(0.55, 0.90)),
            lambda c: (c["fav_side"] == "yes").to_numpy()))

    # (7) end-of-window favourite drift, decided EARLY (>=120s buffer)
    for tl, dmin in [((120, 180), 5), ((120, 240), 8), ((150, 240), 10)]:
        yield (f"(7)eowdrift t{tl[0]}-{tl[1]}d>={dmin}", mk(
            lambda b, tl=tl, dmin=dmin: (b["time_left_sec"].between(*tl) & b["book_healthy"]
                                         & (b["abs_dist_bps"] >= dmin) & b["consistent"]
                                         & b["fav_ask"].between(0.55, 0.90)),
            lambda c: (c["fav_side"] == "yes").to_numpy()))

    # (8) deep favourite over/under-determination (fav_ask 0.85-0.97)
    for lo, hi in [(0.85, 0.92), (0.88, 0.95), (0.90, 0.97)]:
        yield (f"(8)deepfav[{lo},{hi}]", mk(
            lambda b, lo=lo, hi=hi: (b["time_left_sec"].between(180, 480) & b["book_healthy"]
                                     & b["fav_ask"].between(lo, hi)),
            lambda c: (c["fav_side"] == "yes").to_numpy()))

    # (9) cl_cb_basis structural tilt: chainlink already off strike vs book mid
    for dmin in [5, 8, 12]:
        yield (f"(9)clbasis_disagr d>={dmin}", mk(
            lambda b, dmin=dmin: (b["cl_ok"] & b["time_left_sec"].between(180, 420) & b["book_healthy"]
                                  & (b["cl_dist_bps"].abs() >= dmin) & (b["cb_up_now"] != b["cl_up_now"])),
            lambda c: (c["cl_up_now"]).to_numpy()))
    for dmin in [8, 12]:
        yield (f"(9)clbasis_agree d>={dmin}", mk(
            lambda b, dmin=dmin: (b["cl_ok"] & b["time_left_sec"].between(180, 420) & b["book_healthy"]
                                  & (b["cl_dist_bps"].abs() >= dmin) & (b["cb_up_now"] == b["cl_up_now"])),
            lambda c: (c["cl_up_now"]).to_numpy()))

    # (10) underdog longshot tail: buy the cheap (NOT-fav) side mid-window
    for lo, hi in [(0.55, 0.65), (0.55, 0.70)]:
        yield (f"(10)underdog[{lo},{hi}]", mk(
            lambda b, lo=lo, hi=hi: (b["time_left_sec"].between(240, 480) & b["book_healthy"]
                                     & b["fav_ask"].between(lo, hi)),
            lambda c: (c["fav_side"] != "yes").to_numpy()))


def run(top_k: int = 6):
    b = L.load_base().copy()
    b["cl_ok"] = b["chainlink_price"].notna() & (b["chainlink_price"] > 0)
    b["vol_bps"] = b["realized_vol"] * 100.0
    tleft = np.clip(b["time_left_sec"].to_numpy("f8"), 1, None)
    exp_move = b["vol_bps"].to_numpy("f8") * np.sqrt(tleft)
    with np.errstate(divide="ignore", invalid="ignore"):
        b["proximity_z"] = np.where(exp_move > 1e-6,
                                    b["abs_dist_bps"].to_numpy("f8") / exp_move, np.nan)
    # family-9 derived columns, precomputed once (cheap, vectorized)
    with np.errstate(divide="ignore", invalid="ignore"):
        b["cl_dist_bps"] = (b["chainlink_price"] / b["start_price"] - 1.0) * 1e4
    b["cl_up_now"] = b["cl_ok"] & (b["chainlink_price"] >= b["start_price"])
    b["cb_up_now"] = b["cb_spot"] >= b["start_price"]

    print("warming book/outcome caches ...", flush=True)
    _book(); _cl()
    print(f"\n===== PASS 1: cheap streaming screen "
          f"(FULL+future EV, future CI, latency 2 & 5) =====\n", flush=True)
    store: list = []
    for name, build in build_variants(b):
        screen(name, build, store)

    # rank by future-split CI lower bound (the OOS judge); require a real fut sample
    ranked = sorted([s for s in store if s["fut_n"] >= 20 and np.isfinite(s["fut_lo"])],
                    key=lambda s: s["fut_lo"], reverse=True)
    print("\n----- PASS 1 ranking by future CI lower bound (fut_n>=20) -----")
    for s in ranked[:12]:
        print(f"  {s['name']:34} fut ${s['fut_ev']:+.2f}[{s['fut_lo']:+.2f},{s['fut_hi']:+.2f}]"
              f"n{s['fut_n']} l5${s['fut5']:+.2f} | FULL ${s['full_ev']:+.2f}(l5${s['fl5']:+.2f})")

    print(f"\n===== PASS 2: full gauntlet on top {top_k} "
          f"(L.evaluate + full latency sweep) =====\n", flush=True)
    finalists = []
    for s in ranked[:top_k]:
        dec = s["dec"]
        led = L.simulate(dec, latency=2)
        if led is None or len(led) < 3:
            print(f"{s['name']:34} (no fills in full sim)")
            continue
        lat = L.latency_survival(dec)
        print(L.verdict_line(s["name"], led, lat), flush=True)
        e = L.evaluate(led)
        finalists.append(dict(name=s["name"], e=e, lat=lat, led=led, dec=dec, s=s))
        print()

    print("=" * 96)
    print("KEEP rule: future lo>0 AND latency-flat(2->10) AND CPCV>=70% AND DSR>0 "
          "AND n>=40 AND not jackpot-driven (FULL total vs ev*n).")
    return finalists


if __name__ == "__main__":
    run()
