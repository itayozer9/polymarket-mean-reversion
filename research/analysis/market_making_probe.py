"""Market-making feasibility probe — Polymarket 15m crypto Up/Down.

A SCOPING study, not a backtest. The corrected dev data (`ticks_15m.parquet`)
is 1 Hz, top-of-book only, ~87% stale. It cannot properly backtest market-
making (no queue position, no fill sequencing, no L2 book). This probe
quantifies the inputs to a clear-eyed go/no-go:

  1. Spread-capture distribution on genuine two-sided books.
  2. Adverse-selection markout: where the mid goes 15/30/60s after a tick.
  3. Inventory / resolution-flatten risk via end-of-window book liquidity.

Maker economics (verified, research/audit/cost_notes.md):
  taker fee = shares * 0.07 * p * (1-p);  maker fee = 0;
  crypto maker rebate ~= 20% of the taker fee on the matched volume.

Dev split only (May 15-20). Hold-out (May 21-22) is NOT loaded.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research"))
from holdout import DEV_START, DEV_END  # noqa: E402

TICKS = ROOT / "data" / "research" / "ticks_15m.parquet"

FEE_RATE = 0.07          # crypto taker fee rate
MAKER_REBATE = 0.20      # crypto maker rebate: 20% of the taker fee
ODDS_LO, ODDS_HI = 0.05, 0.95   # tradeable odds band (broad)


def fee(p: float | np.ndarray, shares: float | np.ndarray) -> np.ndarray:
    """Taker fee in USDC for `shares` at price `p`."""
    return shares * FEE_RATE * p * (1.0 - p)


def load_dev() -> pd.DataFrame:
    df = pd.read_parquet(TICKS)
    ts = pd.to_datetime(df["window_start_ts"], unit="s", utc=True)
    df = df.assign(date=ts.dt.strftime("%Y-%m-%d"))
    dev = df[(df["date"] >= DEV_START) & (df["date"] <= DEV_END)].copy()
    assert (dev["date"] <= DEV_END).all(), "hold-out leaked"
    # drop the handful of rows with a missing mid / book — they poison markouts
    dev = dev.dropna(subset=["yes_mid", "yes_best_bid", "yes_best_ask",
                             "no_best_bid", "no_best_ask",
                             "seconds_into_window"])
    return dev


def two_sided_mask(df: pd.DataFrame) -> pd.Series:
    """A genuine two-sided book on the YES side (the bid/ask we'd quote inside)."""
    return (
        (df["yes_best_bid"] > 0)
        & (df["yes_best_ask"] > 0)
        & (df["yes_best_bid"] < df["yes_best_ask"])
        & (df["no_best_bid"] > 0)
        & (df["no_best_ask"] > 0)
        & (df["no_best_bid"] < df["no_best_ask"])
        & (df["yes_mid"] >= ODDS_LO)
        & (df["yes_mid"] <= ODDS_HI)
    )


def pct(s: pd.Series, qs=(10, 25, 50, 75, 90)) -> dict:
    return {f"p{q}": float(np.nanpercentile(s, q)) for q in qs}


# ----------------------------------------------------------------------------
# 1. SPREAD CAPTURE
# ----------------------------------------------------------------------------
def spread_capture(dev: pd.DataFrame) -> None:
    print("=" * 78)
    print("1. SPREAD CAPTURE — genuine two-sided books, YES odds in [%.2f, %.2f]"
          % (ODDS_LO, ODDS_HI))
    print("=" * 78)
    m = two_sided_mask(dev)
    b = dev[m]
    spread = (b["yes_best_ask"] - b["yes_best_bid"]) * 100.0   # cents
    mid = b["yes_mid"]
    spread_pct = (b["yes_best_ask"] - b["yes_best_bid"]) / mid * 100.0

    print(f"two-sided ticks in band : {len(b):,} of {len(dev):,} "
          f"({100*len(b)/len(dev):.1f}%)")
    print(f"spread (cents)          : mean {spread.mean():.2f}  "
          f"median {spread.median():.2f}  " + "  ".join(
              f"{k} {v:.2f}" for k, v in pct(spread).items()))
    print(f"spread (% of mid)       : mean {spread_pct.mean():.2f}%  "
          f"median {spread_pct.median():.2f}%  " + "  ".join(
              f"{k} {v:.2f}%" for k, v in pct(spread_pct).items()))

    # Round-trip gross spread capture if a maker is filled on BOTH sides.
    # Buy at the bid, sell at the ask -> capture the full spread, pay 0 fee,
    # earn the rebate on both legs.
    stake = 10.0
    p = mid.values
    sp = (b["yes_best_ask"].values - b["yes_best_bid"].values)
    shares = stake / b["yes_best_ask"].values   # ~stake/price worth
    gross_spread = shares * sp                  # $ captured on a round trip
    # rebate = 20% of the taker fee the *counterparty* generates, paid to us,
    # on both fills (entry + exit). Conservatively price at mid.
    rebate = MAKER_REBATE * fee(p, shares) * 2.0
    gross_total = gross_spread + rebate

    print()
    print("Round-trip GROSS maker capture (filled both sides, $10 stake):")
    print(f"  spread captured : mean ${gross_spread.mean():.3f}  "
          f"median ${np.median(gross_spread):.3f}")
    print(f"  maker rebate    : mean ${rebate.mean():.4f}  "
          f"median ${np.median(rebate):.4f}  (20% of taker fee, both legs)")
    print(f"  gross total     : mean ${gross_total.mean():.3f}  "
          f"median ${np.median(gross_total):.3f}")
    print("  NOTE: this is the BEST case — both legs fill, no adverse move,")
    print("        no inventory carried to resolution. Section 2 prices the")
    print("        adverse selection that this best case ignores.")

    # spread by odds bucket
    print()
    print("Spread by YES-mid bucket:")
    buckets = [(0.05, 0.15), (0.15, 0.30), (0.30, 0.45), (0.45, 0.55),
               (0.55, 0.70), (0.70, 0.85), (0.85, 0.95)]
    for lo, hi in buckets:
        bm = b[(mid >= lo) & (mid < hi)]
        if len(bm) < 50:
            continue
        s = (bm["yes_best_ask"] - bm["yes_best_bid"]) * 100
        print(f"  [{lo:.2f},{hi:.2f})  n={len(bm):>7,}  "
              f"median spread {s.median():.2f}c  mean {s.mean():.2f}c")

    # depth — what's actually sitting at top of book
    print()
    yd = b["yes_bid_depth"]
    ya = b["yes_ask_depth"]
    print("Top-of-book depth (USD):")
    print(f"  yes_bid_depth : median ${yd.median():.0f}  "
          + "  ".join(f"{k} ${v:.0f}" for k, v in pct(yd).items()))
    print(f"  yes_ask_depth : median ${ya.median():.0f}  "
          + "  ".join(f"{k} ${v:.0f}" for k, v in pct(ya).items()))


# ----------------------------------------------------------------------------
# 2. ADVERSE-SELECTION MARKOUT
# ----------------------------------------------------------------------------
def adverse_selection(dev: pd.DataFrame) -> None:
    print()
    print("=" * 78)
    print("2. ADVERSE-SELECTION MARKOUT — where the mid goes after a tick")
    print("=" * 78)
    print("Crude markout: for every two-sided tick, look forward +15/30/60s in")
    print("the SAME window and measure the YES mid change. A passive maker is")
    print("filled by takers crossing the spread; that flow is informed, so the")
    print("fill systematically precedes an adverse move.")
    print()

    g = dev.sort_values(["slug", "seconds_into_window"])
    out_rows = []
    for horizon in (15, 30, 60):
        deltas = []
        for slug, w in g.groupby("slug", sort=False):
            w = w.reset_index(drop=True)
            sec = w["seconds_into_window"].values
            mid = w["yes_mid"].values
            bid = w["yes_best_bid"].values
            ask = w["yes_best_ask"].values
            twosided = (
                (w["yes_best_bid"].values > 0) & (w["yes_best_ask"].values > 0)
                & (bid < ask) & (mid >= ODDS_LO) & (mid <= ODDS_HI)
            )
            # forward index: first tick >= sec[i] + horizon
            fwd = np.searchsorted(sec, sec + horizon, side="left")
            for i in range(len(w)):
                j = fwd[i]
                if j >= len(w) or not twosided[i]:
                    continue
                deltas.append(mid[j] - mid[i])
        d = np.asarray(deltas)
        # A maker who is filled on a BID buys YES. The adverse case is the mid
        # falling after the buy. Symmetric for an ask fill (mid rising).
        # The half-spread the maker earns vs the *expected* adverse move:
        # markout for a bid-fill = E[mid_fwd - mid] given a fill happened.
        # Without trade-side data we approximate the magnitude of the move a
        # quote-straddling maker eats as E[|delta|] minus what reverts.
        mean_abs = np.mean(np.abs(d)) * 100
        std = np.std(d) * 100
        out_rows.append((horizon, len(d), mean_abs, std))
        print(f"  +{horizon:>3}s : n={len(d):>9,}  "
              f"E[|mid move|]={mean_abs:.3f}c  std={std:.3f}c  "
              f"mean={np.mean(d)*100:+.3f}c")

    print()
    print("Fill-event markout (the honest adverse-selection estimate).")
    print("We CANNOT see trades. The cleanest observable fill proxy with 1Hz")
    print("top-of-book data: the best bid moves DOWN to/through a level we")
    print("had quoted -> the book traded through us -> we were filled as a")
    print("BUYER right before the move. Symmetric: best ask moves UP -> we")
    print("were filled as a SELLER. We measure where the mid is 15/30/60s")
    print("AFTER each such event. A negative bid-fill markout = bought into a")
    print("drop; a positive ask-fill markout = sold into a rally.")
    print()
    # Model: at tick i we have a resting BID one tick (1c) below the best bid,
    # i.e. at bid[i]-0.01. We are 'filled' at tick i+1 if the new best bid has
    # fallen to <= our quote (book traded down through us). Symmetric ask.
    horizons = (15, 30, 60)
    bid_mk = {h: [] for h in horizons}
    ask_mk = {h: [] for h in horizons}
    n_bid_fill = n_ask_fill = 0
    for slug, w in g.groupby("slug", sort=False):
        w = w.reset_index(drop=True)
        sec = w["seconds_into_window"].values
        mid = w["yes_mid"].values
        bid = w["yes_best_bid"].values
        ask = w["yes_best_ask"].values
        twosided = ((bid > 0) & (ask > 0) & (bid < ask)
                    & (mid >= ODDS_LO) & (mid <= ODDS_HI))
        fwd = {h: np.searchsorted(sec, sec + h, side="left") for h in horizons}
        for i in range(len(w) - 1):
            if not twosided[i] or not twosided[i + 1]:
                continue
            # resting BID 1c inside, filled if the book trades down onto it
            my_bid = bid[i] - 0.01
            if my_bid > 0.0 and bid[i + 1] <= my_bid + 1e-9:
                n_bid_fill += 1
                for h in horizons:
                    j = fwd[h][i + 1]
                    if j < len(w) and twosided[j]:
                        # we BOUGHT at my_bid; markout vs the later mid
                        bid_mk[h].append(mid[j] - my_bid)
            # resting ASK 1c inside, filled if the book trades up onto it
            my_ask = ask[i] + 0.01
            if my_ask < 1.0 and ask[i + 1] >= my_ask - 1e-9:
                n_ask_fill += 1
                for h in horizons:
                    j = fwd[h][i + 1]
                    if j < len(w) and twosided[j]:
                        # we SOLD at my_ask; markout = my_ask - later mid
                        ask_mk[h].append(my_ask - mid[j])
    print(f"  simulated bid-side fills: {n_bid_fill:,}   "
          f"ask-side fills: {n_ask_fill:,}")
    print("  markout = realised PnL/share of the fill vs the mid at +Hs")
    print("  (positive = good; the quote captured edge. negative = adverse.)")
    for h in horizons:
        bm = np.asarray(bid_mk[h]) * 100
        am = np.asarray(ask_mk[h]) * 100
        print(f"  +{h:>3}s : bid-fill markout mean {bm.mean():+.3f}c "
              f"(median {np.median(bm):+.3f}c, n={len(bm):,})   "
              f"ask-fill markout mean {am.mean():+.3f}c "
              f"(median {np.median(am):+.3f}c, n={len(am):,})")
    # headline adverse-selection number at +30s: the average markout across
    # both sides. A NEGATIVE markout means the half-spread we captured was
    # MORE than eaten by the post-fill move.
    bm30 = np.mean(bid_mk[30]) * 100
    am30 = np.mean(ask_mk[30]) * 100
    avg_markout = (bm30 + am30) / 2.0
    print()
    print(f"  +30s headline fill markout (both sides) : {avg_markout:+.3f}c "
          f"/ share")
    print("  This markout ALREADY contains the ~1c edge of the inside quote")
    print("  (we quoted 1c better than the book). It is the NET of "
          "(quote edge - adverse move).")
    return avg_markout


def net_economics(dev: pd.DataFrame, markout_c: float) -> None:
    print()
    print("=" * 78)
    print("3. NET ROUGH ECONOMICS")
    print("=" * 78)
    m = two_sided_mask(dev)
    b = dev[m]
    full_spread_c = (b["yes_best_ask"] - b["yes_best_bid"]).median() * 100
    mid = b["yes_mid"].median()
    # rebate per fill in cents-of-a-share equivalent
    rebate_per_share = MAKER_REBATE * FEE_RATE * mid * (1 - mid) * 100
    print(f"  median full spread          : {full_spread_c:.2f}c")
    print(f"  maker rebate per fill       : {rebate_per_share:.4f}c "
          f"(20% taker fee at mid={mid:.2f})")
    print(f"  +30s fill markout per fill  : {markout_c:+.3f}c  (Section 2)")
    print("  (the markout already nets the ~1c inside-quote edge against the")
    print("   post-fill adverse move — it is what a fill is actually worth.)")
    print()
    # Per-fill: the markout IS the realised value of the fill vs mid; add the
    # rebate. A market maker's PnL per fill = markout + rebate.
    edge_per_fill = markout_c + rebate_per_share
    print("  PER FILL  (fill markout + maker rebate):")
    print(f"    {markout_c:+.3f}c + {rebate_per_share:.3f}c "
          f"= {edge_per_fill:+.3f}c per share")
    # A maker must ALSO flatten the resulting inventory. If it flattens as a
    # TAKER, it pays the taker fee + crosses the spread on the exit leg:
    exit_taker_cost = FEE_RATE * mid * (1 - mid) * 100 + full_spread_c / 2
    print()
    print("  If inventory is then flattened by crossing as a TAKER:")
    print(f"    exit taker fee + half-spread = {exit_taker_cost:.3f}c")
    print(f"    net per fill = {edge_per_fill:+.3f}c - {exit_taker_cost:.3f}c "
          f"= {edge_per_fill - exit_taker_cost:+.3f}c")
    print()
    print("  If inventory is flattened by ANOTHER passive maker fill (best")
    print("  case, both legs passive) the round trip is ~2x the per-fill edge:")
    edge_rt = 2 * edge_per_fill
    print(f"    {edge_rt:+.3f}c per share / round trip")
    print()
    verdict = "NEGATIVE" if edge_rt < 0 else (
        "marginally positive (best case, ignores queue/fill probability)")
    print(f"  >>> Best-case crude round-trip net: {edge_rt:+.3f}c / share "
          f"-> {verdict}")
    stake = 10.0
    shares = stake / mid
    print(f"  At a $10 stake (~{shares:.0f} shares) that is "
          f"${edge_rt/100*shares:+.3f} per round trip — before any queue-")
    print("  position haircut, cancel/repost latency, or inventory risk.")


# ----------------------------------------------------------------------------
# 4. INVENTORY / RESOLUTION RISK
# ----------------------------------------------------------------------------
def resolution_risk(dev: pd.DataFrame) -> None:
    print()
    print("=" * 78)
    print("4. INVENTORY / RESOLUTION RISK — can a MM flatten before close?")
    print("=" * 78)
    # last tick in each window (closest to close)
    last = dev.sort_values("seconds_into_window").groupby("slug").tail(1)
    # ticks in the final 60s / 30s
    near_close = dev[dev["time_left_sec"] <= 60]
    very_near = dev[dev["time_left_sec"] <= 30]

    def book_health(d: pd.DataFrame, label: str) -> None:
        twosided = (
            (d["yes_best_bid"] > 0) & (d["yes_best_ask"] > 0)
            & (d["yes_best_bid"] < d["yes_best_ask"])
            & (d["yes_mid"] >= 0.03) & (d["yes_mid"] <= 0.97)
        )
        frac = twosided.mean()
        dd = d[twosided]
        bid_depth = dd["yes_bid_depth"]
        ask_depth = dd["yes_ask_depth"]
        sp = (dd["yes_best_ask"] - dd["yes_best_bid"]) * 100
        print(f"  {label}:")
        print(f"    ticks                       : {len(d):,}")
        print(f"    two-sided & undecided book  : {100*frac:.1f}%")
        if len(dd):
            print(f"    median spread (two-sided)   : {sp.median():.2f}c")
            print(f"    median bid depth            : ${bid_depth.median():.0f}"
                  f"   p25 ${np.nanpercentile(bid_depth,25):.0f}")
            print(f"    bid depth < $10 stake       : "
                  f"{100*(bid_depth < 10).mean():.1f}%")

    book_health(near_close, "final 60s of the window")
    book_health(very_near, "final 30s of the window")
    book_health(last, "the very last observed tick / window")

    # how decided is the book at close
    twosided_last = (
        (last["yes_best_bid"] > 0) & (last["yes_best_ask"] > 0)
        & (last["yes_best_bid"] < last["yes_best_ask"])
    )
    decided = (last["yes_mid"] < 0.03) | (last["yes_mid"] > 0.97)
    print()
    print(f"  At the last tick: {100*decided.mean():.1f}% of windows already "
          f"priced <3c or >97c (effectively resolved — no exit liquidity for")
    print(f"  the losing side); {100*twosided_last.mean():.1f}% still show a "
          f"nominal two-sided book.")
    print()
    print("  A 15m binary settles to 0/1. Any inventory not flattened before")
    print("  close is marked to the binary outcome — a ~50c expected loss on")
    print("  the wrong side. With ~$5-20 of top-of-book depth and ~87% stale")
    print("  quotes, flattening a built-up position in the final seconds is")
    print("  not reliable.")


def main() -> None:
    print("MARKET-MAKING FEASIBILITY PROBE — Polymarket 15m crypto Up/Down")
    print(f"Dev split {DEV_START}..{DEV_END}  (hold-out NOT loaded)")
    dev = load_dev()
    print(f"Loaded {len(dev):,} dev ticks across {dev['slug'].nunique():,} "
          f"windows, {dev['symbol'].nunique()} symbols\n")
    spread_capture(dev)
    adv = adverse_selection(dev)
    net_economics(dev, adv)
    resolution_risk(dev)
    print()
    print("=" * 78)
    print("DONE. See docs/research/market_making_feasibility.md for the verdict.")
    print("=" * 78)


if __name__ == "__main__":
    main()
