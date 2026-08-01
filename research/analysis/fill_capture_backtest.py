"""A4 — does a laddered-within-band fill capture more WINNING det_d12 trades?

Today the live executor fires ONE FAK at limit = min(entry_ask + 0.05, 0.92):
  - it OVERPAYS: 0.92 > the validated max_ask 0.85, so it can fill a favourite above the band
    where the payoff is -EV;
  - it ABANDONS: when the fill-time best ask drifted above entry_ask+0.05 it gets 0 fill and the
    window is added to done_slugs forever — even when the ask is still inside the 0.50-0.85 band.

Proposed: walk the ask ladder capped at the strategy's own max_ask (never 0.92). This backtest
compares the two fill models on the real 10-level L2 ladder at the fill tick (entry_sec+latency),
settled on Chainlink. The decision number is NOT raw capture rate (capturing more losers is bad)
but the EV of the INCREMENTAL trades the laddered model captures that the single-shot missed, plus
the -EV overpay the single-shot incurs above the band that the cap avoids.

Run: uv run python -m research.analysis.fill_capture_backtest
"""
from __future__ import annotations
import numpy as np
import pandas as pd

from research.analysis import edge_lab
from research.analysis.loss_patterns import _ladders, _LV
from research.sim.fills_v2 import walk_buy, settle_pnl

STAKE = 5.0          # live det_d12 size
LATENCY = 2
MAX_ASK = 0.85       # det_d12 validated band ceiling
CEIL_OVER = 0.05     # today's PRICE_CEIL_OVER
ABS_MAX = 0.92       # today's ABS_MAX_PRICE
T_MIN, T_MAX, DIST_MIN, ASK_LO = 1, 180, 12.0, 0.50


def _ladder_at(lad, slug, sec, fav_side):
    try:
        lr = lad.loc[(slug, sec)]
    except KeyError:
        return None, None
    if isinstance(lr, pd.DataFrame):
        lr = lr.iloc[0]
    if fav_side == "yes":
        px = [lr[f"ask_px_{i}"] for i in _LV]; sz = [lr[f"ask_sz_{i}"] for i in _LV]
    else:
        px = [1.0 - lr[f"bid_px_{i}"] for i in _LV]; sz = [lr[f"bid_sz_{i}"] for i in _LV]
    return np.asarray(px, "f8"), np.asarray(sz, "f8")


def _walk_capped(px, sz, ceiling, stake=STAKE):
    """walk_buy restricted to ladder levels at-or-below `ceiling` (refuse to overpay)."""
    m = np.isfinite(px) & (px > 0) & (px <= ceiling + 1e-9)
    if not m.any():
        return None
    return walk_buy(px[m], sz[m], stake)


def build():
    b = edge_lab.load_base()
    cand = b[(b["time_left_sec"] >= T_MIN) & (b["time_left_sec"] <= T_MAX)
             & (b["abs_dist_bps"] >= DIST_MIN) & (b["consistent"])
             & (b["fav_ask"].between(ASK_LO, MAX_ASK))]
    first = (cand.sort_values(["slug", "seconds_into_window"])
             .groupby("slug", as_index=False).first())
    ladders = _ladders(sorted(first["symbol"].unique()))
    clout = edge_lab.cl_outcomes().set_index("slug")["cl_up"].to_dict()

    rows = []
    for _, r in first.iterrows():
        lad = ladders.get(r["symbol"])
        if lad is None or r["slug"] not in clout:
            continue
        sec = int(r["seconds_into_window"]) + LATENCY
        px, sz = _ladder_at(lad, r["slug"], sec, r["fav_side"])
        if px is None:
            continue
        best_ask = float(px[0]) if np.isfinite(px[0]) else np.nan
        quoted = float(r["fav_ask"])
        buy_yes = (r["fav_side"] == "yes")
        cl_up = clout[r["slug"]]
        won = bool(cl_up == 1) if buy_yes else bool(cl_up == 0)

        # Model A: single-shot at today's limit
        limit_a = min(quoted + CEIL_OVER, ABS_MAX)
        fa = _walk_capped(px, sz, limit_a)
        # Model B: laddered, capped at the validated band
        fb = _walk_capped(px, sz, MAX_ASK)

        rows.append(dict(
            slug=r["slug"], symbol=r["symbol"], split=r["split"], won=int(won),
            best_ask=best_ask, quoted=quoted, in_band=bool(best_ask <= MAX_ASK + 1e-9),
            a_filled=bool(fa and fa.filled and fa.unfilled_usd <= STAKE * 0.5),
            a_avg=float(fa.avg_price) if fa and fa.filled else np.nan,
            a_pnl=float(settle_pnl(fa, won)) if (fa and fa.filled) else np.nan,
            b_filled=bool(fb and fb.filled and fb.unfilled_usd <= STAKE * 0.5),
            b_avg=float(fb.avg_price) if fb and fb.filled else np.nan,
            b_pnl=float(settle_pnl(fb, won)) if (fb and fb.filled) else np.nan,
        ))
    return pd.DataFrame(rows)


def run():
    df = build()
    n = len(df)
    print(f"=== det_d12 fill-capture: single-shot(limit=ask+{CEIL_OVER},cap{ABS_MAX}) vs "
          f"laddered(cap max_ask={MAX_ASK})  stake ${STAKE:.0f} ===")
    print(f"entries with Chainlink+ladder: {n}\n")

    for nm, fcol, pcol, acol in (("single-shot(A)", "a_filled", "a_pnl", "a_avg"),
                                 ("laddered(B)", "b_filled", "b_pnl", "b_avg")):
        f = df[df[fcol]]
        print(f"  {nm:16} filled={len(f):>4}/{n} ({len(f)/max(n,1)*100:4.0f}%)  "
              f"avg_px={f[acol].mean():.3f}  WR_cl={f['won'].mean()*100:4.1f}%  "
              f"EV=${f[pcol].mean():+.2f}/tr  total=${f[pcol].sum():+.0f}")

    # THE decision metric: trades B captures that A missed (in-band) — is their EV positive?
    inc = df[(df["b_filled"]) & (~df["a_filled"])]
    print(f"\n  INCREMENTAL (B fills, A missed): n={len(inc)}  "
          f"WR_cl={inc['won'].mean()*100 if len(inc) else float('nan'):.1f}%  "
          f"avg_px={inc['b_avg'].mean() if len(inc) else float('nan'):.3f}  "
          f"EV=${inc['b_pnl'].mean() if len(inc) else float('nan'):+.2f}/tr  "
          f"total=${inc['b_pnl'].sum() if len(inc) else 0:+.0f}")
    print(f"    -> {'POSITIVE: laddering ADDS value' if len(inc) and inc['b_pnl'].sum() > 0 else 'check sign'}")

    # overpay A incurs above the band that B's cap avoids
    over = df[(df["a_filled"]) & (df["a_avg"] > MAX_ASK + 1e-9)]
    print(f"\n  A OVERPAY (filled above max_ask {MAX_ASK}): n={len(over)}  "
          f"avg_px={over['a_avg'].mean() if len(over) else float('nan'):.3f}  "
          f"EV=${over['a_pnl'].mean() if len(over) else float('nan'):+.2f}/tr  "
          f"total=${over['a_pnl'].sum() if len(over) else 0:+.0f}  (B caps these out)")

    # correctly skipped: best ask above band — both should miss
    above = df[~df["in_band"]]
    print(f"\n  above-band (best_ask>{MAX_ASK}): n={len(above)}  "
          f"A still filled={int(above['a_filled'].sum())} (overpay), B filled={int(above['b_filled'].sum())}")

    # net: total captured pnl by model (filled only)
    print(f"\n  NET captured total: A=${df['a_pnl'].sum():+.0f}  B=${df['b_pnl'].sum():+.0f}  "
          f"delta=${df['b_pnl'].sum() - df['a_pnl'].sum():+.0f}")
    return df


if __name__ == "__main__":
    run()
