"""Cross-book 5m↔15m frame — co-terminal no-arb margins, causal by construction.

The final 5m window (w5 = w15+600) settles on the SAME close as its 15m parent, with an
observable strike gap g = (K5-K15)/K15. Monotone no-arb: K5 >= K15 ⇒ fair(YES15) >=
fair(YES5) and fair(NO5) >= fair(NO15) (mirrored for K5 <= K15). Whenever a DOMINATING
instrument X trades below a DOMINATED instrument R's bid, that is an internal-consistency
violation harvestable as relative value: margin = bid_R − ask_X.

Four executable legs per 15m-second:
  gap >= +g:  m_15y = yes5_bid − yes15_ask  (buy YES15)   [engine xb UP leg]
              m_5n  = no15_bid − no5_ask    (buy NO5)     [NEVER TESTED]
  gap <= −g:  m_15n = no5_bid  − no15_ask   (buy NO15)    [engine xb DOWN leg, NO-book form]
              m_5y  = yes15_bid − yes5_ask  (buy YES5)    [NEVER TESTED]

CAUSALITY (the XI4 burn — test_ledger "XI4 AMENDMENT 2026-06-12"):
  * 1s embargo: the 5m book row used at 15m decision-second t is the latest 5m tick with
    ts <= t−1 (never same-second forward), max age 3s.
  * k5 look-ahead: pre-2026-06-13 11:05 UTC the 5m strike was back-filled ~24s (p90 55s)
    late by the 30s discovery poll → `k5_causal` is False for those rows unless s5 >= 35.
    Post-fix, start_price is captured as-of sec-0 → causal everywhere.
  * This module loads NO outcome/end_price columns — labels join later, by slug, from
    official_outcomes only.

Run:  uv run python -m research.dataset.xbook
Out:  data/research/xbook_15m.parquet
"""
from __future__ import annotations
import os

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
J15 = os.path.join(REPO, "data", "research", "joined_15m.parquet")
J5 = os.path.join(REPO, "data", "research", "joined_5m.parquet")
OUT = os.path.join(REPO, "data", "research", "xbook_15m.parquet")

EMBARGO_S = 1          # 5m info must be at least this much older than the decision second
MAX_AGE_S = 3          # and no staler than this (a stale 5m book fakes violations)
S5_CAUSAL_MIN = 35     # pre-strike-fix: 5m strike captured p90 ~35s late by the 30s poll
T_STRIKE_FIX = int(pd.Timestamp("2026-06-13 11:05", tz="UTC").timestamp())

# deliberately excludes every outcome/label column
_C15 = ["slug", "symbol", "date", "split", "window_start_ts", "seconds_into_window",
        "yes_best_bid", "yes_best_ask", "no_best_bid", "no_best_ask",
        "yes_bid_depth", "yes_ask_depth", "no_bid_depth", "no_ask_depth",
        "yes_mid", "spread_yes", "book_healthy", "start_price"]
_C5 = ["slug", "symbol", "window_start_ts", "seconds_into_window",
       "yes_best_bid", "yes_best_ask", "no_best_bid", "no_best_ask",
       "yes_bid_depth", "yes_ask_depth", "no_bid_depth", "no_ask_depth",
       "book_healthy", "start_price"]


def _sane_book(bid: pd.Series, ask: pd.Series, max_spread: float = 0.15) -> pd.Series:
    """Two-sided, in [0.01,0.99], spread <= 0.15 — a collapsed/decided book is not an
    opinion (same filter as build_xi4_join / the engine's xb sanity gate)."""
    return (bid.between(0.01, 0.99) & ask.between(0.01, 0.99)
            & (ask > bid) & ((ask - bid) <= max_spread))


def build_xbook(j15_path: str = J15, j5_path: str = J5, out: str | None = OUT,
                t15: pd.DataFrame | None = None, t5: pd.DataFrame | None = None) -> pd.DataFrame:
    """Join every 15m decision-second in the co-terminal overlap to the latest causal
    5m book row; emit the four leg margins. Frame args override paths (for tests)."""
    if t15 is None:
        t15 = pd.read_parquet(j15_path, columns=_C15)
    if t5 is None:
        t5 = pd.read_parquet(j5_path, columns=_C5)

    t15 = t15[(t15["seconds_into_window"] >= 600) & (t15["seconds_into_window"] < 900)
              & t15["book_healthy"].fillna(False).astype(bool)
              & (t15["start_price"] > 0)].copy()
    t15["ts_dec"] = t15["window_start_ts"] + t15["seconds_into_window"]

    t5 = t5[(t5["window_start_ts"] % 900) == 600].copy()      # co-terminal 5m windows only
    t5 = t5[t5["book_healthy"].fillna(False).astype(bool) & (t5["start_price"] > 0)]
    t5["ts5"] = t5["window_start_ts"] + t5["seconds_into_window"]
    t5 = t5.rename(columns={
        "slug": "slug5", "window_start_ts": "w5", "seconds_into_window": "s5",
        "yes_best_bid": "yes5_bid", "yes_best_ask": "yes5_ask",
        "no_best_bid": "no5_bid", "no_best_ask": "no5_ask",
        "yes_bid_depth": "yes5_bid_sh", "yes_ask_depth": "yes5_ask_sh",
        "no_bid_depth": "no5_bid_sh", "no_ask_depth": "no5_ask_sh",
        "start_price": "k5"})
    t5 = t5[_sane_book(t5["yes5_bid"], t5["yes5_ask"])]
    t5 = t5.drop_duplicates(["symbol", "ts5"], keep="last")

    # asof-backward with embargo: latest 5m tick at or before ts_dec − EMBARGO_S,
    # within MAX_AGE_S − EMBARGO_S of that key (total age from decision <= MAX_AGE_S).
    t15["ts_key"] = t15["ts_dec"] - EMBARGO_S
    t15 = t15.sort_values("ts_key")
    t5 = t5.sort_values("ts5")
    j = pd.merge_asof(
        t15, t5[["symbol", "slug5", "w5", "s5", "ts5", "k5",
                 "yes5_bid", "yes5_ask", "no5_bid", "no5_ask",
                 "yes5_bid_sh", "yes5_ask_sh", "no5_bid_sh", "no5_ask_sh"]],
        left_on="ts_key", right_on="ts5", by="symbol",
        direction="backward", tolerance=MAX_AGE_S - EMBARGO_S)
    j = j[j["ts5"].notna()].copy()
    # same 15m parent only (asof can only ever match the current overlap, but be explicit)
    j = j[j["w5"] == j["window_start_ts"] + 600]

    j["age_5m_s"] = j["ts_dec"] - j["ts5"]
    j["gap_bps"] = (j["k5"] - j["start_price"]) / j["start_price"] * 1e4
    j["k5_causal"] = (j["w5"] >= T_STRIKE_FIX) | (j["s5"] >= S5_CAUSAL_MIN)

    # four leg margins (positive = executable violation before fees/premium)
    j["m_15y"] = j["yes5_bid"] - j["yes_best_ask"]     # buy YES15, requires gap >= +g
    j["m_5n"] = j["no_best_bid"] - j["no5_ask"]        # buy NO5,   requires gap >= +g
    j["m_15n"] = j["no5_bid"] - j["no_best_ask"]       # buy NO15,  requires gap <= -g
    j["m_5y"] = j["yes_best_bid"] - j["yes5_ask"]      # buy YES5,  requires gap <= -g
    # $ notional displayed behind each leg's REFERENCE bid (opinion-size floor)
    j["ref_15y_usd"] = j["yes5_bid"] * j["yes5_bid_sh"]
    j["ref_5n_usd"] = j["no_best_bid"] * j["no_bid_depth"]
    j["ref_15n_usd"] = j["no5_bid"] * j["no5_bid_sh"]
    j["ref_5y_usd"] = j["yes_best_bid"] * j["yes_bid_depth"]

    keep = ["slug", "slug5", "symbol", "date", "split", "window_start_ts",
            "seconds_into_window", "s5", "ts_dec", "age_5m_s", "gap_bps", "k5_causal",
            "k5", "start_price", "yes_mid", "spread_yes",
            "yes_best_bid", "yes_best_ask", "no_best_bid", "no_best_ask",
            "yes_ask_depth", "no_ask_depth", "yes5_bid", "yes5_ask", "no5_bid", "no5_ask",
            "yes5_ask_sh", "no5_ask_sh",
            "m_15y", "m_5n", "m_15n", "m_5y",
            "ref_15y_usd", "ref_5n_usd", "ref_15n_usd", "ref_5y_usd"]
    j = j[keep].reset_index(drop=True)
    if out:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        j.to_parquet(out, index=False)
        print(f"[xbook] {len(j):,} joined decision-seconds, "
              f"{j['slug'].nunique():,} 15m windows -> {out}")
    return j


if __name__ == "__main__":
    build_xbook()
