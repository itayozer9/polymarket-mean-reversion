"""AUDIT A2 — execution realism for the two LIVE edges (determinism, stale-quote).

Quantifies the paper->live execution gap, the single most decision-relevant gap
for going live. Three legs:

 (a) ADVERSE-SELECTION FILL. The live paper engine fills at the quoted best ask
     (optimistic). The parity ledgers already walk the real L2 ladder (walk_buy)
     greedily from level 1 — still optimistic: it assumes the top-of-book is
     yours, no queue, no tick drift between signal and fill. We compare that
     baseline taker walk against progressively PESSIMISTIC taker fills:
       +1c        : pay 1 extra tick on every level consumed (quote drift / cross)
       skipL1     : the best level is gone (picked off) by the time you arrive;
                    walk from level 2
       skipL1+1c  : both (worst-case realistic taker)
     For reference we also price the maker_buy_fill path (resting limit at the
     mid-1c), which is adverse-selected BY CONSTRUCTION (fills only when the side
     is sold down) — it shows why passive quoting cannot save this edge.
     Metric: NET EV/trade with a window-clustered bootstrap 90% CI, per split,
     keyed on `future` (freshest OOS).

 (b) CAPACITY / DEPTH. Walk the real ladder at stake $10/$25/$50/$100; report
     EV decay, avg slippage vs best, and unfilled-rate as size grows. Tells us
     where book depth stops supporting the edge.

 (c) LATENCY (stale-quote). Determinism latency is already in the gauntlet
     (lat 5s/10s). Here we re-key the SQ fill on the book 1/3/5/10s after the
     signal tick (baseline is +2s) and report EV decay — SQ trades a transient
     mispricing, so it should be the most latency-fragile.

Run: uv run python -m research.audit.a2_exec_realism
All fills/costs reuse research.sim.fills_v2 (taker fee 0.07*p*(1-p)*shares,
one-way, hold-to-resolution). $10 stake unless the capacity sweep says otherwise.
"""
from __future__ import annotations
import json
import os
import numpy as np
import pandas as pd

from research.sim.fills_v2 import walk_buy, settle_pnl, maker_buy_fill, FeeSchedule, MIN_ORDER_USD
from research.lib.stats import window_clustered_bootstrap
from research.clean_window import split_of
from research.dataset.feeds import load_trades_per_second
from research.analysis.loss_patterns import (
    _base, _sq_prep, _macro_stress, det_ledger, _ladders, JOINED, STAKE,
)
from research.analysis.build_full_ledgers import _load_curve

FEES = FeeSchedule()
_LV = range(1, 11)
SPLITS = ("dev", "holdout", "future")


# --------------------------------------------------------------------------
# Pessimistic fill variants on a single ladder row
# --------------------------------------------------------------------------
def _ladder_arrays(lr, buy_yes):
    """Return (ask-equivalent px, sz) arrays for the side we BUY, best->worst."""
    if buy_yes:
        px = np.array([lr[f"ask_px_{i}"] for i in _LV], "f8")
        sz = np.array([lr[f"ask_sz_{i}"] for i in _LV], "f8")
    else:  # buying NO == lifting the YES bid ladder; NO-ask = 1 - YES-bid
        px = np.array([1.0 - lr[f"bid_px_{i}"] for i in _LV], "f8")
        sz = np.array([lr[f"bid_sz_{i}"] for i in _LV], "f8")
    return px, sz


def fill_variant(lr, buy_yes, stake, variant):
    """Taker fill of `stake` on the buy side, under a pessimism `variant`.
       baseline  : walk_buy from level 1 (matches the parity ledgers)
       +1c       : +0.01 on every level
       skipL1    : drop level 1 (best size gone), walk from level 2
       skipL1+1c : both
    """
    px, sz = _ladder_arrays(lr, buy_yes)
    if variant in ("skipL1", "skipL1+1c"):
        px, sz = px[1:], sz[1:]
    if variant in ("+1c", "skipL1+1c"):
        px = px + 0.01
    px = np.clip(px, 0.01, 0.99)
    return walk_buy(px, sz, stake, fees=FEES)


# --------------------------------------------------------------------------
# Re-key the per-trade ledger ONTO the ladder under different fill rules.
# We rebuild the entry CANDIDATES (outcome-independent) so we control latency
# and the fill variant, then settle on the true outcome.
# --------------------------------------------------------------------------
def det_candidates(full):
    """First qualifying tick per window for the determinism edge (60/5/0.90),
    with the side + true won flag. Fill happens later (controlled here)."""
    cand = full[(full["time_left_sec"] >= 1) & (full["time_left_sec"] <= 60)
                & (full["abs_dist_bps"] >= 5) & full["consistent"]
                & full["fav_ask"].between(0.50, 0.90)]
    first = (cand.sort_values(["slug", "seconds_into_window"])
             .groupby("slug", as_index=False).first())
    return first.assign(buy_yes=first["fav_side"] == "yes",
                        won=first["fav_won"].astype(bool))


def sq_candidates(full, zc, p, margin=0.08, max_mis=0.30, jump_bps=8.0,
                  t_lo=60, t_hi=840, min_ask=0.05, max_ask=0.95):
    """First qualifying tick per window for the stale-quote edge (live parity).
    Ask-band is applied on the QUOTED top-of-book at the *baseline* +2s fill, to
    match build_full_ledgers exactly; latency sweeps re-key the FILL only."""
    d = full[(full["time_left_sec"] >= t_lo) & (full["time_left_sec"] <= t_hi)
             & np.isfinite(full["z"])].copy()
    d["model_p"] = np.interp(np.clip(d["z"], -6, 6), zc, p, left=p[0], right=p[-1])
    d["mis"] = d["model_p"] - d["yes_mid"]
    am = d["mis"].abs()
    d = d[(am >= margin) & (am <= max_mis)]
    d = d[d["spot_vel_10s_bps"].abs() >= jump_bps]
    if d.empty:
        return pd.DataFrame()
    first = (d.sort_values(["slug", "seconds_into_window"])
             .groupby("slug", as_index=False).first())
    return first.assign(buy_yes=first["mis"] > 0,
                        won=np.where(first["mis"] > 0,
                                     first["outcome_up_clean"] == 1,
                                     first["outcome_up_clean"] == 0).astype(bool))


def _lookup(lad, slug, sec):
    try:
        lr = lad.loc[(slug, sec)]
    except KeyError:
        return None
    return lr.iloc[0] if isinstance(lr, pd.DataFrame) else lr


def settle_ledger(cands, ladders, variant="baseline", stake=STAKE, latency=2,
                  ask_band=None):
    """Settle each candidate at fill-second = entry_sec + latency under `variant`.
    Returns a per-trade DataFrame with pnl, won, split, slug, entry economics.
    `ask_band`=(lo,hi): require the QUOTED top-of-book of the buy side at the
    fill second to lie in band (used to reproduce the SQ parity ask filter)."""
    rows = []
    for _, r in cands.iterrows():
        lad = ladders.get(r["symbol"])
        if lad is None:
            continue
        lr = _lookup(lad, r["slug"], int(r["seconds_into_window"]) + latency)
        if lr is None:
            continue
        buy_yes = bool(r["buy_yes"])
        top_ask = float(lr["ask_px_1"]) if buy_yes else 1.0 - float(lr["bid_px_1"])
        if ask_band is not None and not (ask_band[0] <= top_ask <= ask_band[1]):
            continue
        f = fill_variant(lr, buy_yes, stake, variant)
        if not f.filled or f.unfilled_usd > stake * 0.5:
            continue
        rows.append(dict(slug=r["slug"], symbol=r["symbol"], date=str(r["date"]),
                         split=split_of(str(r["date"])),
                         won=bool(r["won"]), pnl=settle_pnl(f, bool(r["won"])),
                         avg_price=f.avg_price, top_ask=top_ask,
                         slippage=f.avg_price - top_ask,
                         unfilled=f.unfilled_usd, levels=f.levels_used,
                         stake=stake))
    return pd.DataFrame(rows)


def _ev_ci(led, split=None):
    d = led if split is None else led[led["split"] == split]
    if len(d) < 8:
        return len(d), np.nan, np.nan, np.nan, np.nan
    pnl = d["pnl"].to_numpy()
    lo, mid, hi = window_clustered_bootstrap(pnl, d["slug"].to_numpy(), n=3000)
    return len(d), float(pnl.mean()), float(d["won"].mean()), lo, hi


def _line(label, led):
    n, ev, wr, lo, hi = _ev_ci(led)
    nf, evf, wrf, lof, hif = _ev_ci(led, "future")
    flag = "EDGE" if (not np.isnan(lof) and lof > 0) else ("dead" if not np.isnan(evf) and evf <= 0 else "thin")
    return (label, n, ev, wr, lo, hi, nf, evf, wrf, lof, hif, flag)


# --------------------------------------------------------------------------
# (a) adverse-selection fill — taker pessimism ladder + maker reference
# --------------------------------------------------------------------------
def maker_reference(cands, ladders, trades, stake=STAKE, latency=2, ask_band=None):
    """Price a resting BUY limit at (quoted best bid of the buy side) i.e. join
    the bid 1c below the ask — adverse-selected by construction. Fills only from
    later taker SELLS into our bid. Unfilled => no trade (we never get the lock)."""
    rows = []
    for _, r in cands.iterrows():
        lad = ladders.get(r["symbol"])
        if lad is None:
            continue
        sec0 = int(r["seconds_into_window"])
        lr = _lookup(lad, r["slug"], sec0 + latency)
        if lr is None:
            continue
        buy_yes = bool(r["buy_yes"])
        top_ask = float(lr["ask_px_1"]) if buy_yes else 1.0 - float(lr["bid_px_1"])
        if ask_band is not None and not (ask_band[0] <= top_ask <= ask_band[1]):
            continue
        # rest one tick inside the spread on the buy side (best price a maker can post)
        resting = round(top_ask - 0.01, 2)
        if resting < 0.01:
            continue
        tdf = trades.get(r["symbol"])
        if tdf is None:
            continue
        # later trades in THIS window, in YES-equivalent space, after our post
        tw = tdf[(tdf["market_slug"] == r["slug"])
                 & (tdf["seconds_into_window"] >= sec0 + latency)]
        if tw.empty:
            mf = maker_buy_fill(resting if buy_yes else 1.0 - resting, stake, [])
        else:
            # map every executed trade to OUR side's price + a SELL/BUY tag that
            # fills OUR resting buy. A buy on our side fills when someone SELLS our
            # side at <= our price. yes_px is YES-equiv; our side price is yes_px
            # (buy_yes) or 1-yes_px (buy_no). Side that hits our bid = a sell of
            # our side. In YES-equiv the tape doesn't carry maker/taker direction
            # reliably, so we treat ANY execution at <= our price as available
            # liquidity to fill us (an UPPER bound on maker fills — generous).
            ev_list = []
            for _, t in tw.iterrows():
                yp = float(t["tr_vwap_yes"])
                our_px = yp if buy_yes else 1.0 - yp
                ev_list.append(dict(yes_px=our_px, usd=float(t.get("usd", t.get("tr_bull_usd", 0)) or 0),
                                    sec=int(t["seconds_into_window"]), side="SELL"))
            mf = maker_buy_fill(resting if buy_yes else 1.0 - resting, stake, ev_list)
        if not mf.filled:
            continue  # never got filled -> no position (and no lock captured)
        # maker pnl: shares redeem at 1 if won; cost = notional - rebate (one-way)
        won = bool(r["won"])
        pnl = mf.shares * (1.0 if won else 0.0) - mf.notional_usd + mf.rebate_usd
        rows.append(dict(slug=r["slug"], symbol=r["symbol"], date=str(r["date"]),
                         split=split_of(str(r["date"])), won=won, pnl=pnl,
                         avg_price=mf.price, top_ask=top_ask, slippage=np.nan,
                         unfilled=stake - mf.notional_usd, levels=0, stake=stake,
                         filled_frac=mf.notional_usd / stake))
    return pd.DataFrame(rows)


def report_adverse(name, cands, ladders, trades, ask_band):
    print(f"\n{'='*92}\n(a) ADVERSE-SELECTION FILL — {name}\n{'='*92}")
    hdr = (f"{'fill model':>16} {'n':>4} {'EV/tr':>8} {'WR':>5} {'90%CI(all)':>20} "
           f"{'fut.n':>5} {'fut.EV':>8} {'fut.WR':>6} {'fut.90%CI':>20} {'':>5}")
    print(hdr)
    out = {}
    variants = [("baseline(walk)", "baseline"), ("+1c", "+1c"),
                ("skipL1", "skipL1"), ("skipL1+1c", "skipL1+1c")]
    for lab, v in variants:
        led = settle_ledger(cands, ladders, variant=v, ask_band=ask_band)
        L = _line(lab, led)
        out[lab] = led
        print(f"{L[0]:>16} {L[1]:>4} ${L[2]:>+7.3f} {L[3]:>5.2f} "
              f"[{L[4]:>+6.3f},{L[5]:>+6.3f}] {L[6]:>5} ${L[7]:>+7.3f} {L[8]:>6.2f} "
              f"[{L[9]:>+6.3f},{L[10]:>+6.3f}] {L[11]:>5}")
    # maker reference
    mk = maker_reference(cands, ladders, trades, ask_band=ask_band)
    if len(mk):
        L = _line("maker(rest@bid)", mk)
        ff = mk["filled_frac"].mean()
        print(f"{L[0]:>16} {L[1]:>4} ${L[2]:>+7.3f} {L[3]:>5.2f} "
              f"[{L[4]:>+6.3f},{L[5]:>+6.3f}] {L[6]:>5} ${L[7]:>+7.3f} {L[8]:>6.2f} "
              f"[{L[9]:>+6.3f},{L[10]:>+6.3f}] {L[11]:>5}   (mean fill {ff*100:.0f}% of stake)")
        out["maker"] = mk
    return out


# --------------------------------------------------------------------------
# (b) capacity / depth
# --------------------------------------------------------------------------
def report_capacity(name, cands, ladders, ask_band):
    print(f"\n{'='*92}\n(b) CAPACITY / DEPTH — {name}  (baseline walk_buy, latency 2s)\n{'='*92}")
    print(f"{'stake':>7} {'n':>4} {'EV/tr':>9} {'EV/$1':>8} {'WR':>5} {'90%CI(all)':>20} "
          f"{'fut.EV/tr':>10} {'fut.90%CI':>20} {'avgSlip':>8} {'unfill%':>8} {'avgLvls':>8}")
    out = {}
    for stake in (10.0, 25.0, 50.0, 100.0):
        led = settle_ledger(cands, ladders, variant="baseline", stake=stake, ask_band=ask_band)
        out[stake] = led
        if len(led) < 8:
            print(f"{stake:>7.0f} {len(led):>4}  too few"); continue
        n, ev, wr, lo, hi = _ev_ci(led)
        nf, evf, _, lof, hif = _ev_ci(led, "future")
        slip = led["slippage"].mean()
        unf = (led["unfilled"] > 0).mean() * 100
        lv = led["levels"].mean()
        print(f"{stake:>7.0f} {n:>4} ${ev:>+8.3f} ${ev/stake:>+7.4f} {wr:>5.2f} "
              f"[{lo:>+6.3f},{hi:>+6.3f}] ${evf:>+9.3f} [{lof:>+6.3f},{hif:>+6.3f}] "
              f"{slip:>+8.4f} {unf:>7.1f}% {lv:>8.2f}")
    return out


# --------------------------------------------------------------------------
# (c) latency — stale-quote
# --------------------------------------------------------------------------
def report_latency_sq(cands, ladders, ask_band):
    print(f"\n{'='*92}\n(c) LATENCY — stale-quote  (re-key fill on book at signal+Ns; baseline=+2s)\n{'='*92}")
    print(f"{'latency':>8} {'n':>4} {'EV/tr':>9} {'WR':>5} {'90%CI(all)':>20} "
          f"{'fut.n':>5} {'fut.EV':>9} {'fut.90%CI':>20} {'avgFill$':>9} {'':>5}")
    out = {}
    for lat in (1, 2, 3, 5, 10):
        led = settle_ledger(cands, ladders, variant="baseline", latency=lat, ask_band=ask_band)
        out[lat] = led
        L = _line(f"+{lat}s", led)
        avgpx = led["avg_price"].mean() if len(led) else np.nan
        print(f"{L[0]:>8} {L[1]:>4} ${L[2]:>+8.3f} {L[3]:>5.2f} "
              f"[{L[4]:>+6.3f},{L[5]:>+6.3f}] {L[6]:>5} ${L[7]:>+8.3f} "
              f"[{L[9]:>+6.3f},{L[10]:>+6.3f}] ${avgpx:>8.3f} {L[11]:>5}")
    return out


def main():
    full = _base(pd.read_parquet(JOINED))
    full["split"] = full["date"].map(split_of)
    ladders = _ladders(sorted(full["symbol"].unique()))
    zc, p = _load_curve()
    sqf = _sq_prep(full)

    det_c = det_candidates(full)
    sq_c = sq_candidates(sqf, zc, p)
    print(f"candidates: det={len(det_c)}  sq={len(sq_c)}")
    for nm, c in (("det", det_c), ("sq", sq_c)):
        print(f"  {nm} split counts:", c["date"].map(split_of).value_counts().to_dict())

    # trade tapes for the maker reference (one tape per symbol)
    from research.clean_window import CLEAN_START, available_clean_dates
    d1 = available_clean_dates("btc")[-1]
    trades = {s: load_trades_per_second(s, CLEAN_START, d1) for s in sorted(full["symbol"].unique())}
    for s, t in trades.items():
        if len(t):
            # need a usd column for queue absorption; approx from bull+bear
            t["usd"] = t["tr_bull_usd"].fillna(0) + t["tr_bear_usd"].fillna(0)

    DET_BAND = None                 # determinism has no quoted-ask band beyond 0.50-0.90 (already in cand)
    SQ_BAND = (0.05, 0.95)          # stale-quote parity ask band on top-of-book

    res = {}
    res["det_adverse"] = report_adverse("DETERMINISM", det_c, ladders, trades, DET_BAND)
    res["sq_adverse"] = report_adverse("STALE-QUOTE", sq_c, ladders, trades, SQ_BAND)
    res["det_cap"] = report_capacity("DETERMINISM", det_c, ladders, DET_BAND)
    res["sq_cap"] = report_capacity("STALE-QUOTE", sq_c, ladders, SQ_BAND)
    res["sq_lat"] = report_latency_sq(sq_c, ladders, SQ_BAND)

    # ---- machine-readable decay summary ----
    def decay(led_map, base_key, keys):
        b = led_map[base_key]
        be = b["pnl"].mean() if len(b) else np.nan
        return {str(k): (round(float(led_map[k]["pnl"].mean()), 4) if len(led_map[k]) else None)
                for k in keys}, round(float(be), 4)

    summ = {}
    da, db = decay(res["det_adverse"], "baseline(walk)",
                   ["baseline(walk)", "+1c", "skipL1", "skipL1+1c"] + (["maker"] if "maker" in res["det_adverse"] else []))
    sa, sb = decay(res["sq_adverse"], "baseline(walk)",
                   ["baseline(walk)", "+1c", "skipL1", "skipL1+1c"] + (["maker"] if "maker" in res["sq_adverse"] else []))
    summ["adverse_fill_EVtr"] = {"det": da, "sq": sa}
    summ["capacity_EVtr"] = {
        "det": {str(int(k)): round(float(v["pnl"].mean()), 4) for k, v in res["det_cap"].items() if len(v)},
        "sq": {str(int(k)): round(float(v["pnl"].mean()), 4) for k, v in res["sq_cap"].items() if len(v)},
    }
    summ["sq_latency_EVtr"] = {str(k): (round(float(v["pnl"].mean()), 4) if len(v) else None)
                               for k, v in res["sq_lat"].items()}
    # future-split versions of the headline decays
    def fut(led):
        d = led[led["split"] == "future"]
        return round(float(d["pnl"].mean()), 4) if len(d) >= 8 else None
    summ["adverse_fill_EVtr_FUTURE"] = {
        "det": {k: fut(res["det_adverse"][k]) for k in res["det_adverse"]},
        "sq": {k: fut(res["sq_adverse"][k]) for k in res["sq_adverse"]},
    }
    summ["sq_latency_EVtr_FUTURE"] = {str(k): fut(v) for k, v in res["sq_lat"].items()}

    outp = os.path.join(os.path.dirname(__file__), "a2_summary.json")
    os.makedirs(os.path.dirname(outp), exist_ok=True)
    json.dump(summ, open(outp, "w"), indent=2)
    print(f"\nwrote {outp}")
    print(json.dumps(summ, indent=2))
    return res, summ


if __name__ == "__main__":
    main()
