"""Compare Mar 2026 (hist) vs May 2026 (live) data to characterize regime shift.

Computes per-window:
  - Dip depth distribution (max yes_mid - min yes_mid within window, both sides)
  - Bounce magnitude (post-trough rebound)
  - Realized vol (rolling 60s)
  - Window resolution (Up/Down/Unknown)
  - Hours of activity

Looks for what's different and what the bot's strategy needs to adapt for.
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from mean_reversion_live.adapters import arb_imports  # noqa: F401,E402
from scripts.mean_reversion import loaders as _arb_loaders  # noqa: E402

ANALYSIS_DIR = ROOT / "data" / "analysis_2026-05-17" / "ticks"
_arb_loaders.DATA_DIR = str(ANALYSIS_DIR)
_arb_loaders.OUTCOMES_FILE = str(ANALYSIS_DIR / "outcomes.csv")

from mean_reversion_live.adapters.arb_imports import iter_markets, load_outcomes  # noqa: E402


COINS = ["btc", "eth", "sol", "xrp"]
HIST = ("2026-03-14", "2026-03-17")
LIVE = ("2026-05-15", "2026-05-17")


def per_window_stats(arr, slug, outcome):
    n = len(arr)
    if n < 30:
        return None
    yes_mid = arr["yes_mid"].astype(np.float64)
    no_mid = arr["no_mid"].astype(np.float64)
    move = arr["move_pct"].astype(np.float64)
    ts = arr["timestamp_ms"]
    secs = arr["seconds_into_window"]

    # Yes/no max-min within window (full dip-bounce range)
    yes_max = yes_mid.max()
    yes_min = yes_mid.min()
    no_max = no_mid.max()
    no_min = no_mid.min()

    # Realized vol of spot (move_pct std)
    rv = float(np.std(move)) if len(move) > 1 else 0.0

    # Bounces — count how many local minima below 0.20 had a subsequent rebound
    bounces_yes = 0
    bounces_no = 0
    for side_mid, lst in ((yes_mid, "yes"), (no_mid, "no")):
        win = 60  # 1 min rolling
        for i in range(60, len(side_mid) - 60):
            local_min = side_mid[i - win:i + 1].min()
            if side_mid[i] != local_min or side_mid[i] >= 0.30 or side_mid[i] <= 0.05:
                continue
            forward = side_mid[i:i + 120]
            if forward.max() >= side_mid[i] * 1.40:
                if lst == "yes":
                    bounces_yes += 1
                else:
                    bounces_no += 1

    hour = dt.datetime.utcfromtimestamp(int(ts[0]) / 1000).hour
    dow = dt.datetime.utcfromtimestamp(int(ts[0]) / 1000).weekday()
    date = dt.datetime.utcfromtimestamp(int(ts[0]) / 1000).strftime("%Y-%m-%d")
    return {
        "slug": slug,
        "date": date,
        "hour": hour,
        "dow": dow,
        "n_ticks": n,
        "yes_range": yes_max - yes_min,
        "no_range": no_max - no_min,
        "rv": rv,
        "bounces_yes": bounces_yes,
        "bounces_no": bounces_no,
        "outcome": outcome[0] if outcome else None,
    }


def main():
    print("Loading outcomes...")
    outcomes = load_outcomes()
    rows = []
    for sym in COINS:
        print(f"Processing {sym}...")
        for slug, arr in iter_markets("15m", sym, "2026-03-14", "2026-05-17"):
            r = per_window_stats(arr, slug, outcomes.get(slug))
            if r is None:
                continue
            r["sym"] = sym
            rows.append(r)
    print(f"Total windows: {len(rows)}")

    # Compare hist vs live
    def in_seg(d, lo, hi):
        return lo <= d <= hi
    hist_rows = [r for r in rows if in_seg(r["date"], *HIST)]
    live_rows = [r for r in rows if in_seg(r["date"], *LIVE)]
    print(f"\nHist windows: {len(hist_rows)}, Live windows: {len(live_rows)}")

    def stats(rows, key):
        v = [r[key] for r in rows]
        if not v:
            return None
        v_sorted = sorted(v)
        return {
            "n": len(v),
            "mean": sum(v) / len(v),
            "median": v_sorted[len(v_sorted) // 2],
            "p25": v_sorted[len(v_sorted) // 4],
            "p75": v_sorted[3 * len(v_sorted) // 4],
        }

    print("\n== Distribution comparison ==")
    for key in ("yes_range", "no_range", "rv", "bounces_yes", "bounces_no"):
        h = stats(hist_rows, key); l = stats(live_rows, key)
        if not h or not l:
            continue
        print(f"  {key:15} hist median={h['median']:.4f} mean={h['mean']:.4f} | "
              f"live median={l['median']:.4f} mean={l['mean']:.4f}")

    # Per-coin
    print("\n== Per-coin comparison ==")
    for sym in COINS:
        h = [r for r in hist_rows if r["sym"] == sym]
        l = [r for r in live_rows if r["sym"] == sym]
        if not h or not l:
            continue
        h_yrange = sum(r["yes_range"] for r in h) / len(h)
        l_yrange = sum(r["yes_range"] for r in l) / len(l)
        h_b = sum(r["bounces_yes"] + r["bounces_no"] for r in h) / len(h)
        l_b = sum(r["bounces_yes"] + r["bounces_no"] for r in l) / len(l)
        print(f"  {sym}: avg yes-range hist={h_yrange:.3f} live={l_yrange:.3f} | "
              f"avg bounces/window hist={h_b:.2f} live={l_b:.2f}")

    # Hour breakdown
    print("\n== Bounce rate by hour-bucket ==")
    def hour_bucket(h):
        if 0 <= h < 8: return "ASIA"
        if 8 <= h < 14: return "EU"
        if 14 <= h < 22: return "US"
        return "OVERNIGHT"
    for hb in ("ASIA", "EU", "US", "OVERNIGHT"):
        h = [r for r in hist_rows if hour_bucket(r["hour"]) == hb]
        l = [r for r in live_rows if hour_bucket(r["hour"]) == hb]
        if not h: continue
        h_b = sum(r["bounces_yes"] + r["bounces_no"] for r in h) / len(h)
        l_b = sum(r["bounces_yes"] + r["bounces_no"] for r in l) / len(l) if l else 0
        print(f"  {hb:>10}: hist {len(h)} win, {h_b:.2f} bounces/win | live {len(l)} win, {l_b:.2f} bounces/win")

    # save rows
    Path("runs").mkdir(exist_ok=True)
    with open("runs/regime_per_window.json", "w") as fh:
        json.dump(rows, fh)
    print(f"\nSaved {len(rows)} window stats to runs/regime_per_window.json")


if __name__ == "__main__":
    main()
