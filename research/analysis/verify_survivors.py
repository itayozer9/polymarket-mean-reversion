"""Independently reproduce the 4 workflow 'keep' edges from their entry-logic specs
(NOT their scripts) through edge_lab, confirm the numbers, and measure OVERLAP —
are these 4 distinct edges or one 'favourite-underpricing' edge in different clothes?

Run: uv run python -m research.analysis.verify_survivors
"""
from __future__ import annotations
import numpy as np
import pandas as pd

from research.analysis import edge_lab as L


def pricestruct(b):
    """Deep DOWN-favourite (NO fav, ask 0.88-0.93), basis>=-2, 7-8min left, buy NO."""
    c = b[(b["fav_side"] == "no") & b["fav_ask"].between(0.88, 0.93, inclusive="right")
          & (b["cl_cb_basis_bps"] >= -2) & b["time_left_sec"].between(420, 480) & b["book_healthy"]]
    return L.first_tick(c, np.zeros(len(c), dtype=bool))   # buy NO


def momentum(b):
    """Last 60-120s, consistent fav, momentum away & growing, dist>=12, fav_ask 0.50-0.90, buy fav."""
    c = b[b["time_left_sec"].between(60, 120) & b["consistent"] & (b["adverse_vel_10s"] < 0)
          & (b["abs_dist_bps"] >= 12) & b["fav_ask"].between(0.50, 0.90) & b["book_healthy"]]
    return L.first_tick(c, (c["fav_side"] == "yes").to_numpy())


def wildcard(b):
    """Low realized-vol, fav >=8bps off strike, ask 0.55-0.80, mid-window 240-420s, buy fav."""
    c = b[b["time_left_sec"].between(240, 420) & ((b["realized_vol"] * 100.0) <= 1.0)
          & (b["abs_dist_bps"] >= 8) & b["fav_ask"].between(0.55, 0.80)
          & b["consistent"] & b["book_healthy"]]
    return L.first_tick(c, (c["fav_side"] == "yes").to_numpy())


def crosscoin(b):
    """At tl 90-180 snapshot, if BTC fav strong (ask>=0.85,|dist|>=20), buy agreeing alt favs."""
    snap = b[b["time_left_sec"].between(90, 180) & b["book_healthy"]]
    snap = snap.sort_values(["slug", "seconds_into_window"]).groupby("slug", as_index=False).first()
    btc = (snap[snap["symbol"] == "btc"][["window_start_ts", "fav_side", "fav_ask", "abs_dist_bps"]]
           .rename(columns={"fav_side": "btc_fav", "fav_ask": "btc_ask", "abs_dist_bps": "btc_dist"}))
    m = snap.merge(btc, on="window_start_ts", how="inner")
    c = m[(m["symbol"] != "btc") & (m["btc_ask"] >= 0.85) & (m["btc_dist"] >= 20)
          & (m["fav_side"] == m["btc_fav"])].copy()
    c = c.rename(columns={"seconds_into_window": "entry_sec"})
    c["buy_yes"] = (c["fav_side"] == "yes").to_numpy()
    return c[["slug", "symbol", "date", "split", "window_start_ts", "entry_sec", "buy_yes"]]


def main():
    b = L.load_base()
    builders = {"wildcard": wildcard, "pricestruct": pricestruct,
                "momentum": momentum, "crosscoin": crosscoin}
    decs, leds = {}, {}
    print("=== INDEPENDENT REPRODUCTION (Chainlink-settled, depth-gated) ===")
    for name, fn in builders.items():
        dec = fn(b)
        decs[name] = dec
        led = L.simulate(dec, latency=2)
        leds[name] = led
        print(L.verdict_line(name, led, L.latency_survival(dec)))

    # ---- overlap: are these the same windows? ----
    print("\n=== WINDOW OVERLAP (Jaccard on slugs) ===")
    sets = {k: set(v["slug"]) for k, v in decs.items()}
    names = list(sets)
    print(f"{'':12}" + "".join(f"{n[:9]:>10}" for n in names))
    for a in names:
        row = f"{a[:11]:12}"
        for bn in names:
            inter = len(sets[a] & sets[bn]); uni = len(sets[a] | sets[bn])
            row += f"{(inter/uni if uni else 0):>10.2f}"
        print(row)

    # ---- combined 'favourite-value' book: union of the 3 fav-underpricing edges ----
    fav3 = ["wildcard", "pricestruct", "momentum"]
    uni = pd.concat([decs[k] for k in fav3], ignore_index=True).drop_duplicates("slug", keep="first")
    uled = L.simulate(uni, latency=2)
    print("\n=== COMBINED favourite-value book (wildcard ∪ pricestruct ∪ momentum, dedup slug) ===")
    print(L.verdict_line("fav-value UNION", uled, L.latency_survival(uni)))
    print(f"   union windows={len(uni)} vs sum-of-parts={sum(len(decs[k]) for k in fav3)} "
          f"(overlap shrinks it by {sum(len(decs[k]) for k in fav3)-len(uni)})")


if __name__ == "__main__":
    main()
