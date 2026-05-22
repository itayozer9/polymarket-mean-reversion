"""Lead A — within-market (YES/NO) arbitrage scan, Polymarket crypto Up/Down.

Hypothesis: $1 mints 1 YES + 1 NO and a pair merges back to $1, so:
  * BUY-both arb : yes_best_ask + no_best_ask < 1  -> buy both, merge -> $1.
  * SELL-both arb: yes_best_bid + no_best_bid > 1  -> mint a pair, sell both.
Both are risk-free, non-directional efficiency harvesting.

This scan is rigorously skeptical. Phase 0 (docs/research/phase0_audit.md
Task 3) found ~87% stale books, decided-market rows, crossed books and
one-sided books. A "candidate" on a degenerate book is NOT an arb. We bucket
those out and only count GENUINE two-sided books with non-zero depth on the
exact legs we must hit.

A second structural fact (discovered here): on ~93% of ticks the YES and NO
books are *exact mirror images* of each other (no_bid = 1-yes_ask,
no_ask = 1-yes_bid, depths swapped). On a mirror book yes_ask + no_ask =
1 + spread > 1 ALWAYS, so an arb is impossible by construction. Real arbs can
only live in the ~7% of ticks where the two tokens are independently quoted.

Fees (Polymarket crypto, taker): fee = 0.07 * p * (1-p) per share, charged on
each leg. Makers pay 0. We report both.

Run:  uv run --extra dev python -m research.analysis.arbitrage_scan
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from research.lib.splits import add_date_col, dev_mask, holdout_mask  # noqa: E402

DATA = REPO / "data" / "research"

# Book columns that must be genuine floats for an arb to be evaluable.
NUM_COLS = [
    "yes_best_bid", "yes_best_ask", "yes_bid_depth", "yes_ask_depth",
    "no_best_bid", "no_best_ask", "no_bid_depth", "no_ask_depth",
]

# A genuine two-sided book: both tokens quoted strictly inside (0.02, 0.98),
# and not crossed. The 0.02/0.98 guard removes decided-market rows where one
# outcome's ask has collapsed to ~0 (the gross "edge" of 0.99 is the tell).
PRICE_LO, PRICE_HI = 0.02, 0.98


def taker_fee(p: np.ndarray | pd.Series) -> np.ndarray | pd.Series:
    """Polymarket crypto taker fee per share at price p."""
    return 0.07 * p * (1.0 - p)


def _load(timeframe: str) -> pd.DataFrame:
    """Load a corrected tick parquet, coerce book columns numeric, add date."""
    df = pd.read_parquet(DATA / f"ticks_{timeframe}.parquet")
    for c in NUM_COLS:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=NUM_COLS).reset_index(drop=True)
    return add_date_col(df)


def _genuine_mask(df: pd.DataFrame) -> pd.Series:
    """True where BOTH YES and NO are genuine two-sided books (not decided,
    not crossed, quoted strictly inside the band)."""
    return (
        (df.yes_best_ask > PRICE_LO) & (df.yes_best_ask < PRICE_HI)
        & (df.no_best_ask > PRICE_LO) & (df.no_best_ask < PRICE_HI)
        & (df.yes_best_bid > PRICE_LO) & (df.yes_best_bid < PRICE_HI)
        & (df.no_best_bid > PRICE_LO) & (df.no_best_bid < PRICE_HI)
        & (df.yes_best_bid < df.yes_best_ask)   # YES not crossed
        & (df.no_best_bid < df.no_best_ask)     # NO not crossed
    )


def _mirror_mask(df: pd.DataFrame) -> pd.Series:
    """True where the NO book is the exact synthetic complement of the YES book
    (no_bid = 1-yes_ask, no_ask = 1-yes_bid). On such books no arb can exist."""
    return (
        np.isclose(df.no_best_bid, 1.0 - df.yes_best_ask)
        & np.isclose(df.no_best_ask, 1.0 - df.yes_best_bid)
    )


def _persistence(df: pd.DataFrame, arb_idx: list[int]) -> np.ndarray:
    """For each arb tick, count consecutive seconds the *identical* 4-quote
    book persists within the same window (executability proxy)."""
    runs = []
    n = len(df)
    slug = df["market_slug"].to_numpy()
    ya = df.yes_best_ask.to_numpy(); na = df.no_best_ask.to_numpy()
    yb = df.yes_best_bid.to_numpy(); nb = df.no_best_bid.to_numpy()
    for i in arb_idx:
        cnt, j = 1, i + 1
        while (j < n and slug[j] == slug[i]
               and ya[j] == ya[i] and na[j] == na[i]
               and yb[j] == yb[i] and nb[j] == nb[i]):
            cnt += 1
            j += 1
        runs.append(cnt)
    return np.asarray(runs, dtype=int)


def scan_side(df: pd.DataFrame, side: str) -> pd.DataFrame:
    """Return one row per GENUINE arb candidate of `side` ('buy' or 'sell').

    Columns added: gross, fee_taker, net_taker, net_maker, exec_size.
    net_maker == gross (maker pays zero fee). exec_size is the executable
    notional, limited by the thinner of the two legs we must hit.
    """
    genuine = _genuine_mask(df)
    if side == "buy":
        cond = (df.yes_best_ask + df.no_best_ask < 1.0) & (df.yes_ask_depth > 0) & (df.no_ask_depth > 0)
        s = df[cond & genuine].copy()
        s["gross"] = 1.0 - s.yes_best_ask - s.no_best_ask
        s["fee_taker"] = taker_fee(s.yes_best_ask) + taker_fee(s.no_best_ask)
        s["exec_size"] = np.minimum(s.yes_ask_depth, s.no_ask_depth)
    elif side == "sell":
        cond = (df.yes_best_bid + df.no_best_bid > 1.0) & (df.yes_bid_depth > 0) & (df.no_bid_depth > 0)
        s = df[cond & genuine].copy()
        s["gross"] = s.yes_best_bid + s.no_best_bid - 1.0
        s["fee_taker"] = taker_fee(s.yes_best_bid) + taker_fee(s.no_best_bid)
        s["exec_size"] = np.minimum(s.yes_bid_depth, s.no_bid_depth)
    else:
        raise ValueError(side)
    s["net_taker"] = s.gross - s.fee_taker
    s["net_maker"] = s.gross  # maker fee = 0
    return s


def _artifact_accounting(df: pd.DataFrame, side: str) -> dict:
    """Count raw candidates and how each artifact class is rejected."""
    if side == "buy":
        raw = df.yes_best_ask + df.no_best_ask < 1.0
        a1, a2 = df.yes_best_ask, df.no_best_ask
        d1, d2 = df.yes_ask_depth, df.no_ask_depth
    else:
        raw = df.yes_best_bid + df.no_best_bid > 1.0
        a1, a2 = df.yes_best_bid, df.no_best_bid
        d1, d2 = df.yes_bid_depth, df.no_bid_depth
    r = df[raw]
    decided = ((r[["yes_best_ask", "no_best_ask"]] <= PRICE_LO).any(axis=1)
               | (r[["yes_best_bid", "no_best_bid"]] >= PRICE_HI).any(axis=1))
    crossed = (r.yes_best_bid >= r.yes_best_ask) | (r.no_best_bid >= r.no_best_ask)
    zero_depth = (eval("r.yes_ask_depth" if side == "buy" else "r.yes_bid_depth") <= 0) \
        | (eval("r.no_ask_depth" if side == "buy" else "r.no_bid_depth") <= 0)
    genuine = raw & _genuine_mask(df) \
        & ((df.yes_ask_depth > 0) & (df.no_ask_depth > 0) if side == "buy"
           else (df.yes_bid_depth > 0) & (df.no_bid_depth > 0))
    return {
        "raw_candidates": int(raw.sum()),
        "decided_market": int(decided.sum()),
        "crossed_book": int(crossed.sum()),
        "zero_depth_leg": int(zero_depth.sum()),
        "genuine": int(genuine.sum()),
    }


def run(verbose: bool = True) -> dict:
    """Full scan over 15m and 5m. Returns a results dict; prints a report."""
    report: dict = {}
    for tf in ("15m", "5m"):
        df = _load(tf)
        n_days = df["date"].nunique()
        mirror_rate = float(_mirror_mask(df).mean())
        tf_out: dict = {"n_ticks": len(df), "n_days": n_days, "mirror_rate": mirror_rate}

        if verbose:
            print("=" * 72)
            print(f"TIMEFRAME {tf}  |  {len(df):,} ticks  |  {n_days} days "
                  f"({df.date.min()}..{df.date.max()})")
            print(f"  YES/NO exact-mirror books: {mirror_rate*100:.1f}% of ticks "
                  f"(arb impossible on these by construction)")

        for side in ("buy", "sell"):
            acct = _artifact_accounting(df, side)
            s = scan_side(df, side)
            s["net_pos"] = s.net_taker > 0
            pos = s[s.net_pos]
            mirror_in_pos = int(_mirror_mask(pos).sum()) if len(pos) else 0
            persist = _persistence(df, df.index[
                _genuine_mask(df)
                & ((df.yes_best_ask + df.no_best_ask < 1.0) if side == "buy"
                   else (df.yes_best_bid + df.no_best_bid > 1.0))
                & (s.net_taker.reindex(df.index).fillna(-1) > 0)
            ].tolist()) if len(pos) else np.array([], dtype=int)

            dev = s[dev_mask(s)]
            hold = s[holdout_mask(s)]
            # dollar profit, taker, capped by executable depth, net>0 only
            dollar_taker = float((pos.net_taker * pos.exec_size).sum())
            dollar_maker = float((s.net_maker * s.exec_size).sum())  # all genuine, maker
            by_date_pos = {str(k): int(v) for k, v in pos.groupby("date").size().items()}

            side_out = {
                "artifact_accounting": acct,
                "genuine_candidates": len(s),
                "net_taker_positive": int(len(pos)),
                "gross_mean": float(s.gross.mean()) if len(s) else 0.0,
                "net_taker_mean": float(s.net_taker.mean()) if len(s) else 0.0,
                "exec_size_median_pos": float(pos.exec_size.median()) if len(pos) else 0.0,
                "dollar_profit_taker_total": dollar_taker,
                "dollar_profit_maker_total": dollar_maker,
                "dollar_per_day_taker": dollar_taker / n_days,
                "dollar_per_day_maker": dollar_maker / n_days,
                "net_pos_by_date": by_date_pos,
                "dev_genuine": len(dev), "dev_net_pos": int((dev.net_taker > 0).sum()),
                "hold_genuine": len(hold), "hold_net_pos": int((hold.net_taker > 0).sum()),
                "mirror_in_net_pos": mirror_in_pos,
                "persistence_median_s": float(np.median(persist)) if len(persist) else 0.0,
                "persistence_frac_1s_only": float((persist == 1).mean()) if len(persist) else 0.0,
                "persistence_frac_ge5s": float((persist >= 5).mean()) if len(persist) else 0.0,
            }
            tf_out[side] = side_out

            if verbose:
                print(f"\n  [{side.upper()}-both]")
                print(f"    artifact rejection: {acct['raw_candidates']:,} raw "
                      f"-> decided={acct['decided_market']:,} "
                      f"crossed={acct['crossed_book']:,} "
                      f"zero-depth={acct['zero_depth_leg']:,} "
                      f"=> GENUINE {acct['genuine']:,}")
                print(f"    genuine candidates: {len(s)}  |  net>0 after taker fee: {len(pos)}")
                print(f"    gross mean={s.gross.mean() if len(s) else 0:.4f}  "
                      f"net_taker mean={s.net_taker.mean() if len(s) else 0:.4f}")
                if len(pos):
                    print(f"    net>0: exec_size median={pos.exec_size.median():.1f} USD  "
                          f"| total $ profit (taker, depth-capped) = {dollar_taker:.2f} USD "
                          f"over {n_days}d => {dollar_taker/n_days:.2f}/day")
                    print(f"    net>0 by date: {by_date_pos}")
                    print(f"    persistence: median={np.median(persist):.0f}s  "
                          f"1s-only={(persist==1).mean()*100:.0f}%  "
                          f">=5s={(persist>=5).mean()*100:.0f}%")
                print(f"    dev (May15-20): {len(dev)} genuine / {int((dev.net_taker>0).sum())} net>0   "
                      f"|  hold-out (May21-22): {len(hold)} genuine / {int((hold.net_taker>0).sum())} net>0")
                print(f"    maker (0 fee): all {len(s)} genuine are net>0; "
                      f"total $ = {dollar_maker:.2f} => {dollar_maker/n_days:.2f}/day")

        report[tf] = tf_out

    if verbose:
        print("=" * 72)
    return report


if __name__ == "__main__":
    run()
