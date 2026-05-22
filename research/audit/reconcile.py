"""Sim vs live-paper reconciliation (Phase 0 Task 8).

For each recorded trade, locate the window's ticks in data/live/ and verify
that the simulator's fill prices are consistent with what the order book
actually quoted at that timestamp.

Entry fill: the simulator buys at the ASK of the relevant side:
  - side == "UP"   -> entry at yes_best_ask, exit at yes_best_bid
  - side == "DOWN" -> entry at no_best_ask,  exit at no_best_bid

The simulator uses a REALISTIC FILL MODEL (cfg.fill.realistic_fill_model=True):
  entry: effective_ask = min(1, depth/bet)*ask + max(0, 1-depth/bet)*(ask+0.02)
  exit:  effective_bid = min(1, depth/target)*bid + max(0, 1-depth/target)*(bid-0.02)
  where target = shares * bid.

So the recorded price may deviate from the raw book best_ask/bid when the fill
exceeds the top-of-book depth. We check against BOTH the raw book price (first-pass)
and the depth-adjusted model price (second-pass) to distinguish:
  (a) genuine fill-accuracy (price matches book or model)
  (b) fill artifacts (neither matches)

Reproducibility thresholds from plan:
  - CONCERN if < 90% reproducible (at ±TOL)
  - CONCERN if mean price discrepancy > 0.01
"""
from __future__ import annotations

import io
import json
import os
import subprocess
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd

# ── paths ─────────────────────────────────────────────────────────────────
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
JSONL_DIR = os.path.join(REPO_ROOT, "data", "jsonl")
LIVE_DIR = os.path.join(REPO_ROOT, "data", "live")

# ── config ─────────────────────────────────────────────────────────────────
STRATEGIES = ["cfg_21c8c00165b3", "v2_gold_03_down_all"]
WINDOW_SEC = 2       # ±2 s around trade timestamp
TOL = 0.005          # price tolerance in share-price units (0.5¢)
REPRODUCIBLE_THRESHOLD = 0.90    # CONCERN if below
DISCREPANCY_THRESHOLD = 0.01     # CONCERN if above
BET_USD = 10.0       # fixed bet size (from strategies)
SLIPPAGE_PCT = 0.02  # the fill model's worse-level offset (2¢)


# ── file cache ─────────────────────────────────────────────────────────────
_file_cache: dict[str, Optional[pd.DataFrame]] = {}


def _load_file(path: str) -> Optional[pd.DataFrame]:
    if path not in _file_cache:
        if not os.path.exists(path):
            _file_cache[path] = None
        else:
            try:
                _file_cache[path] = pd.read_csv(path)
            except Exception:
                proc = subprocess.Popen(
                    ["gunzip", "-c", path],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                )
                data, _ = proc.communicate()
                _file_cache[path] = pd.read_csv(io.BytesIO(data), on_bad_lines="skip")
    return _file_cache[path]


# ── helpers ────────────────────────────────────────────────────────────────

def _load_trades(strategy_id: str) -> list[dict]:
    path = os.path.join(JSONL_DIR, strategy_id, "trades.jsonl")
    trades = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                trades.append(json.loads(line))
    return trades


def _ts_to_date(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def _load_window_ticks(symbol: str, date_str: str, slug: str) -> Optional[pd.DataFrame]:
    """Load ticks for one window slug from the live file on that date."""
    path = os.path.join(LIVE_DIR, f"{symbol}_{date_str}.csv.gz")
    df = _load_file(path)
    if df is None:
        return None
    ticks = df[df["market_slug"] == slug].copy()
    if len(ticks) == 0:
        return None
    return ticks.reset_index(drop=True)


def _find_nearest_tick(ticks: pd.DataFrame, ts_ms: int,
                        window_sec: int = WINDOW_SEC) -> Optional[pd.Series]:
    """Return the tick row closest to ts_ms within ±window_sec (expanding to ±10s if needed)."""
    lo, hi = ts_ms - window_sec * 1000, ts_ms + window_sec * 1000
    nearby = ticks[(ticks["timestamp_ms"] >= lo) & (ticks["timestamp_ms"] <= hi)]
    if len(nearby) == 0:
        nearby = ticks[
            (ticks["timestamp_ms"] >= ts_ms - 10_000) &
            (ticks["timestamp_ms"] <= ts_ms + 10_000)
        ]
    if len(nearby) == 0:
        return None
    time_diff = (nearby["timestamp_ms"] - ts_ms).abs()
    return nearby.loc[time_diff.idxmin()]


def _model_entry_price(ask: float, depth_usd: float, bet: float = BET_USD) -> float:
    """Simulate the realistic fill model for entry."""
    if depth_usd < 1.0:
        return float("nan")
    portion_at_best = min(1.0, depth_usd / bet) if bet > 0 else 1.0
    portion_above = 1.0 - portion_at_best
    worse_ask = min(1.0, ask + SLIPPAGE_PCT)
    return portion_at_best * ask + portion_above * worse_ask


def _model_exit_price(bid: float, depth_usd: float, shares: float) -> float:
    """Simulate the realistic fill model for exit."""
    if bid <= 0:
        return 0.0
    target_usd = shares * bid
    if target_usd > 0:
        portion_at_best = min(1.0, depth_usd / target_usd)
    else:
        portion_at_best = 1.0
    portion_above = 1.0 - portion_at_best
    worse_bid = max(0.0, bid - SLIPPAGE_PCT)
    return portion_at_best * bid + portion_above * worse_bid


def _check_trade(trade: dict) -> dict:
    """Check one trade's entry and exit fills against the order book.

    Two checks per fill:
      - raw: does recorded price match the best_ask/bid at that timestamp?
      - model: does recorded price match the depth-adjusted fill model price?
    A fill is "reproducible" if it passes either check (within TOL).
    """
    slug = trade["slug"]
    side = trade["side"]
    entry_ts = trade["entry_ts_ms"]
    exit_ts = trade["exit_ts_ms"]
    entry_price = trade["entry_price"]
    exit_price = trade["exit_price"]
    shares = trade.get("shares", BET_USD / entry_price)

    ask_col = "yes_best_ask" if side == "UP" else "no_best_ask"
    bid_col = "yes_best_bid" if side == "UP" else "no_best_bid"
    ask_depth_col = "yes_ask_depth" if side == "UP" else "no_ask_depth"
    bid_depth_col = "yes_bid_depth" if side == "UP" else "no_bid_depth"

    parts = slug.split("-")
    symbol = parts[0]

    entry_date = _ts_to_date(entry_ts)
    ticks = _load_window_ticks(symbol, entry_date, slug)

    result: dict = {
        "slug": slug,
        "side": side,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "shares": shares,
        # raw book prices
        "book_ask": float("nan"),
        "book_bid": float("nan"),
        # depth-adjusted model prices
        "model_ask": float("nan"),
        "model_bid": float("nan"),
        # discrepancies
        "entry_disc_raw": float("nan"),
        "exit_disc_raw": float("nan"),
        "entry_disc_model": float("nan"),
        "exit_disc_model": float("nan"),
        # pass/fail
        "entry_repro": False,
        "exit_repro": False,
        "note": "",
        "exit_reason": trade.get("exit_reason", ""),
    }

    if ticks is None:
        result["note"] = "no_ticks_found"
        return result

    # ── entry check ─────────────────────────────────────────────────────────
    entry_tick = _find_nearest_tick(ticks, entry_ts)
    if entry_tick is None:
        result["note"] = "no_tick_near_entry"
    else:
        raw_ask = float(entry_tick[ask_col])
        depth_ask = float(entry_tick[ask_depth_col])
        model_ask = _model_entry_price(raw_ask, depth_ask, BET_USD)

        result["book_ask"] = raw_ask
        result["model_ask"] = model_ask
        result["entry_disc_raw"] = abs(entry_price - raw_ask)
        if not np.isnan(model_ask):
            result["entry_disc_model"] = abs(entry_price - model_ask)
            # Reproducible if within TOL of either raw or model price
            result["entry_repro"] = (
                result["entry_disc_raw"] <= TOL or result["entry_disc_model"] <= TOL
            )
        else:
            result["entry_repro"] = result["entry_disc_raw"] <= TOL

    # ── exit check ──────────────────────────────────────────────────────────
    exit_tick = _find_nearest_tick(ticks, exit_ts)
    if exit_tick is None:
        result["note"] = (result["note"] + ";no_tick_near_exit").lstrip(";")
    else:
        raw_bid = float(exit_tick[bid_col])
        depth_bid = float(exit_tick[bid_depth_col])
        model_bid = _model_exit_price(raw_bid, depth_bid, shares)

        result["book_bid"] = raw_bid
        result["model_bid"] = model_bid
        result["exit_disc_raw"] = abs(exit_price - raw_bid)
        result["exit_disc_model"] = abs(exit_price - model_bid)
        result["exit_repro"] = (
            result["exit_disc_raw"] <= TOL or result["exit_disc_model"] <= TOL
        )

    return result


# ── main ───────────────────────────────────────────────────────────────────

def run():
    print("=" * 72)
    print("Task 8: Sim vs live-paper reconciliation")
    print(f"  Strategies: {STRATEGIES}")
    print(f"  Fill price tolerance: ±{TOL} ({TOL*100:.1f}¢)")
    print(f"  Timestamp window: ±{WINDOW_SEC}s")
    print(f"  Fill model: realistic (depth-walk, slippage ±{SLIPPAGE_PCT})")
    print("=" * 72)
    print()

    all_results: list[dict] = []

    for sid in STRATEGIES:
        trades = _load_trades(sid)
        print(f"[{sid}]  {len(trades)} trades")

        results = [_check_trade(t) for t in trades]
        for r in results:
            r["strategy_id"] = sid
        all_results.extend(results)

        df = pd.DataFrame(results)
        no_ticks = df[df["note"].str.len() > 0]
        with_ticks = df[df["note"].str.len() == 0]

        n_total = len(df)
        n_entry_repro = int(df["entry_repro"].sum())
        n_exit_repro = int(df["exit_repro"].sum())
        n_both_repro = int((df["entry_repro"] & df["exit_repro"]).sum())

        entry_disc_raw = df["entry_disc_raw"].dropna()
        exit_disc_raw = df["exit_disc_raw"].dropna()
        entry_disc_model = df["entry_disc_model"].dropna()
        exit_disc_model = df["exit_disc_model"].dropna()

        print(f"  Tick data found:           {len(with_ticks)}/{n_total}")
        print(f"  No ticks (data gap):       {len(no_ticks)}")
        print(f"  Entry reproducible:        {n_entry_repro}/{n_total} ({100*n_entry_repro/n_total:.1f}%)")
        print(f"  Exit  reproducible:        {n_exit_repro}/{n_total} ({100*n_exit_repro/n_total:.1f}%)")
        print(f"  Both  reproducible:        {n_both_repro}/{n_total} ({100*n_both_repro/n_total:.1f}%)")
        print(f"  Mean |entry_disc| raw:     {entry_disc_raw.mean():.5f}   "
              f"model: {entry_disc_model.mean():.5f}")
        print(f"  Mean |exit_disc|  raw:     {exit_disc_raw.mean():.5f}   "
              f"model: {exit_disc_model.mean():.5f}")
        print(f"  Median entry_disc raw:     {entry_disc_raw.median():.5f}")
        print(f"  Median exit_disc  raw:     {exit_disc_raw.median():.5f}")
        print(f"  P90 entry_disc model:      {entry_disc_model.quantile(0.90):.5f}")
        print(f"  P90 exit_disc  model:      {exit_disc_model.quantile(0.90):.5f}")

        if len(no_ticks) > 0:
            from collections import Counter
            print(f"  Notes: {dict(Counter(no_ticks['note']))}")
        print()

    # ── combined summary ────────────────────────────────────────────────────
    print("-" * 72)
    print("COMBINED SUMMARY (both strategies, all 292 trades)")
    print("-" * 72)

    df_all = pd.DataFrame(all_results)
    n_total = len(df_all)
    n_entry_repro = int(df_all["entry_repro"].sum())
    n_exit_repro = int(df_all["exit_repro"].sum())
    n_both_repro = int((df_all["entry_repro"] & df_all["exit_repro"]).sum())

    # Trades with tick data (excludes the 25 May-16 data-gap trades)
    has_ticks = df_all["note"].str.len() == 0
    n_with_ticks = has_ticks.sum()
    n_entry_repro_tk = int(df_all.loc[has_ticks, "entry_repro"].sum())
    n_exit_repro_tk = int(df_all.loc[has_ticks, "exit_repro"].sum())
    n_both_repro_tk = int((df_all.loc[has_ticks, "entry_repro"] &
                           df_all.loc[has_ticks, "exit_repro"]).sum())

    frac_entry = n_entry_repro / n_total
    frac_exit = n_exit_repro / n_total
    frac_both = n_both_repro / n_total

    # Exclude data-gap trades for the "fair" fraction
    frac_entry_tk = n_entry_repro_tk / n_with_ticks
    frac_exit_tk = n_exit_repro_tk / n_with_ticks
    frac_both_tk = n_both_repro_tk / n_with_ticks

    # Discrepancies: use the better of raw vs model
    entry_best_disc = df_all[["entry_disc_raw", "entry_disc_model"]].min(axis=1).dropna()
    exit_best_disc = df_all[["exit_disc_raw", "exit_disc_model"]].min(axis=1).dropna()
    mean_entry_disc = entry_best_disc.mean()
    mean_exit_disc = exit_best_disc.mean()

    print(f"  Total trades:             {n_total}")
    print(f"  With tick data:           {n_with_ticks}  (25 on May-16 are a data-collection gap)")
    print()
    print("  REPRODUCIBLE FRACTION (all trades incl. data-gap as non-repro):")
    print(f"    Entry: {n_entry_repro}/{n_total} ({100*frac_entry:.1f}%)")
    print(f"    Exit:  {n_exit_repro}/{n_total} ({100*frac_exit:.1f}%)")
    print(f"    Both:  {n_both_repro}/{n_total} ({100*frac_both:.1f}%)")
    print()
    print("  REPRODUCIBLE FRACTION (trades-with-ticks only, fair comparison):")
    print(f"    Entry: {n_entry_repro_tk}/{n_with_ticks} ({100*frac_entry_tk:.1f}%)")
    print(f"    Exit:  {n_exit_repro_tk}/{n_with_ticks} ({100*frac_exit_tk:.1f}%)")
    print(f"    Both:  {n_both_repro_tk}/{n_with_ticks} ({100*frac_both_tk:.1f}%)")
    print()
    print(f"  MEAN PRICE DISCREPANCY (best of raw vs model):")
    print(f"    Entry: {mean_entry_disc:.5f} (threshold: {DISCREPANCY_THRESHOLD})")
    print(f"    Exit:  {mean_exit_disc:.5f} (threshold: {DISCREPANCY_THRESHOLD})")
    print()

    # ── CONCERN / PASS tags ────────────────────────────────────────────────
    concerns = []

    # Use the fair (with-ticks-only) fraction for the threshold check
    if frac_entry_tk < REPRODUCIBLE_THRESHOLD:
        concerns.append(
            f"CONCERN: Entry reproducible (with-ticks) {100*frac_entry_tk:.1f}% < "
            f"{100*REPRODUCIBLE_THRESHOLD:.0f}% threshold"
        )
    if frac_exit_tk < REPRODUCIBLE_THRESHOLD:
        concerns.append(
            f"CONCERN: Exit reproducible (with-ticks) {100*frac_exit_tk:.1f}% < "
            f"{100*REPRODUCIBLE_THRESHOLD:.0f}% threshold"
        )
    if mean_entry_disc > DISCREPANCY_THRESHOLD:
        concerns.append(
            f"CONCERN: Mean entry discrepancy {mean_entry_disc:.5f} > "
            f"{DISCREPANCY_THRESHOLD} threshold"
        )
    if mean_exit_disc > DISCREPANCY_THRESHOLD:
        concerns.append(
            f"CONCERN: Mean exit discrepancy {mean_exit_disc:.5f} > "
            f"{DISCREPANCY_THRESHOLD} threshold"
        )

    # Note the data-gap issue
    if (df_all["note"] == "no_ticks_found").sum() > 0:
        n_gap = (df_all["note"] == "no_ticks_found").sum()
        concerns.append(
            f"CONCERN: {n_gap} trades (all May-16) have no tick data to reconcile "
            f"against — the live tick file was truncated at 07:19 UTC that day. "
            f"These trades are counted as non-reproducible in the all-trades fraction."
        )

    if concerns:
        for c in concerns:
            print(c)
        print()
    else:
        print("PASS: All fill-price metrics within acceptable thresholds.")

    # ── remaining unexplained discrepancies ──────────────────────────────
    # Trades where BOTH raw and model checks fail
    still_off = df_all[
        (df_all["entry_repro"] == False) &
        (df_all["note"].str.len() == 0) &
        (df_all["entry_disc_model"].notna()) &
        (df_all["entry_disc_model"] > TOL)
    ]
    if len(still_off) > 0:
        print(f"\nUnexplained entry discrepancies ({len(still_off)} trades, model check fails):")
        for _, r in still_off.head(8).iterrows():
            print(f"  {r['slug']} | rec={r['entry_price']:.4f} "
                  f"book_ask={r['book_ask']:.4f} model_ask={r['model_ask']:.4f} "
                  f"disc_model={r['entry_disc_model']:.4f}")

    still_off_exit = df_all[
        (df_all["exit_repro"] == False) &
        (df_all["note"].str.len() == 0) &
        (df_all["exit_disc_model"].notna()) &
        (df_all["exit_disc_model"] > TOL)
    ]
    if len(still_off_exit) > 0:
        print(f"\nUnexplained exit discrepancies ({len(still_off_exit)} trades, model check fails):")
        for _, r in still_off_exit.head(8).iterrows():
            print(f"  {r['slug']} side={r['side']} exit_reason={r['exit_reason']} "
                  f"| rec={r['exit_price']:.4f} "
                  f"book_bid={r['book_bid']:.4f} model_bid={r['model_bid']:.4f} "
                  f"disc_model={r['exit_disc_model']:.4f}")

    # ── verdict ──────────────────────────────────────────────────────────
    print()
    print("=" * 72)
    print("VERDICT:")
    print()
    print(f"  Reproducible fraction (trades with tick data): {100*frac_both_tk:.1f}%")
    print(f"  Mean price discrepancy (entry): {mean_entry_disc:.5f}")
    print(f"  Mean price discrepancy (exit):  {mean_exit_disc:.5f}")
    print()

    if frac_both_tk >= REPRODUCIBLE_THRESHOLD and mean_entry_disc <= DISCREPANCY_THRESHOLD \
            and mean_exit_disc <= DISCREPANCY_THRESHOLD:
        print("  Fills are HONEST. The recorded fills match what the live book quoted,")
        print("  once the realistic depth-walk model is accounted for. The bot's paper")
        print("  losses reflect genuine strategy failure — not fill artifacts.")
    else:
        print("  Fills have anomalies (see CONCERN tags). Investigate whether losses")
        print("  are partly a fill artifact.")
    print("=" * 72)


if __name__ == "__main__":
    run()
