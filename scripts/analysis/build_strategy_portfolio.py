"""Pick the robust + diversified portfolio of strategies from sweep results.

Process:
  1. Load sweeps; compute per-trade detailed summaries
  2. Apply strict robustness gates (hist+live positive, ≥3 coins positive)
  3. Among passing, pick a diverse top-N by side / hour / vol combinations
  4. Verify each pick on per-day equity curve, drawdowns
  5. Output:
     - JSON list of configs
     - Markdown report with stats
     - strategies.yaml snippet for cut-paste

Usage:
    uv run python scripts/analysis/build_strategy_portfolio.py \
        --sweeps runs/broad_sweep_v1.jsonl runs/focused_sweep_v1.jsonl \
        --out-md PROPOSED_STRATEGIES.md \
        --out-yaml runs/proposed_strategies.yaml
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

HIST_DATES = {"2026-03-14", "2026-03-15", "2026-03-16", "2026-03-17"}
LIVE_DATES = {"2026-05-15", "2026-05-16", "2026-05-17"}
ALL_DATES = sorted(HIST_DATES | LIVE_DATES)
COINS = ["btc", "eth", "sol", "xrp"]


def ts_date(ms: int) -> str:
    return dt.datetime.utcfromtimestamp(ms / 1000).strftime("%Y-%m-%d")


def ts_hour(ms: int) -> int:
    return dt.datetime.utcfromtimestamp(ms / 1000).hour


def summarize(rec: Dict[str, Any]) -> Dict[str, Any]:
    trades = sorted(rec["trades"], key=lambda t: t["entry_ts_ms"])
    n = len(trades)
    if n == 0:
        return {"config_id": rec["config_id"], "flat": rec["flat"], "n": 0}
    pnls = [t["pnl"] for t in trades]
    pnl_total = sum(pnls)
    wins = sum(1 for p in pnls if p > 0)
    std = statistics.stdev(pnls) if n >= 2 else 1.0
    sharpe = (pnl_total / n) / std if std > 1e-9 else 0.0

    eq = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        eq += p
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)

    by_coin = defaultdict(lambda: {"trades": 0, "pnl": 0.0, "wins": 0})
    by_day = defaultdict(lambda: {"trades": 0, "pnl": 0.0, "wins": 0})
    by_seg = defaultdict(lambda: {"trades": 0, "pnl": 0.0, "wins": 0})
    by_coin_seg = defaultdict(lambda: {"trades": 0, "pnl": 0.0, "wins": 0})
    for t in trades:
        d = ts_date(t["entry_ts_ms"])
        seg = "hist" if d in HIST_DATES else ("live" if d in LIVE_DATES else "other")
        w = 1 if t["pnl"] > 0 else 0
        for dct, k in ((by_coin, t["sym"]), (by_day, d), (by_seg, seg),
                       (by_coin_seg, (t["sym"], seg))):
            v = dct[k]
            v["trades"] += 1
            v["pnl"] += t["pnl"]
            v["wins"] += w

    days_data = [d for d in ALL_DATES if by_day.get(d, {}).get("trades", 0) > 0]
    days_pos = sum(1 for d in days_data if by_day[d]["pnl"] > 0)
    return {
        "config_id": rec["config_id"], "flat": rec["flat"], "n": n, "wins": wins,
        "pnl": pnl_total, "wr": wins / n, "sharpe": sharpe, "max_dd": max_dd, "peak": peak,
        "by_coin": dict(by_coin), "by_day": dict(by_day), "by_seg": dict(by_seg),
        "by_coin_seg": {f"{k[0]}|{k[1]}": v for k, v in by_coin_seg.items()},
        "days_with_data": len(days_data), "days_positive": days_pos,
    }


def robust_score(s: Dict[str, Any]) -> float:
    """Composite: emphasize cross-segment, cross-coin, low-drawdown."""
    if s.get("n", 0) == 0:
        return -1e9
    h = s["by_seg"].get("hist", {"pnl": 0.0, "trades": 0})
    l = s["by_seg"].get("live", {"pnl": 0.0, "trades": 0})
    seg_min = min(h["pnl"] if h["trades"] >= 3 else -100, l["pnl"] if l["trades"] >= 3 else -100)
    coin_pnls = [s["by_coin"].get(c, {}).get("pnl", -50) for c in COINS]
    coin_min = min(coin_pnls) if coin_pnls else -100
    days_pos_rate = s["days_positive"] / max(1, s["days_with_data"])
    # Strong reward for low drawdown
    dd_pen = -s["max_dd"] * 0.5
    return (
        s["pnl"]
        + 1.5 * seg_min
        + 0.8 * coin_min
        + 50.0 * s["sharpe"]
        + 200.0 * days_pos_rate
        + dd_pen
    )


def passes_strict(s: Dict[str, Any]) -> bool:
    if s.get("n", 0) < 15:
        return False
    h = s["by_seg"].get("hist", {"pnl": 0.0, "trades": 0})
    l = s["by_seg"].get("live", {"pnl": 0.0, "trades": 0})
    if h["trades"] < 5 or l["trades"] < 5:
        return False
    if h["pnl"] <= 0 or l["pnl"] <= 0:
        return False
    coins_pos = sum(1 for c in COINS if s["by_coin"].get(c, {}).get("pnl", -1) > 0)
    coins_data = sum(1 for c in COINS if s["by_coin"].get(c, {}).get("trades", 0) >= 2)
    if coins_data < 3 or coins_pos < 3:
        return False
    if s["days_with_data"] >= 3 and s["days_positive"] / s["days_with_data"] < 0.5:
        return False
    if s["max_dd"] > 0.7 * max(s["peak"], s["pnl"], 1.0):
        return False
    if s["sharpe"] < 0.10:
        return False
    return True


def passes_relaxed(s: Dict[str, Any]) -> bool:
    """Looser gate for backup picks (e.g. live-only configs that show promise)."""
    if s.get("n", 0) < 12:
        return False
    if s["pnl"] < 30.0:
        return False
    coins_pos = sum(1 for c in COINS if s["by_coin"].get(c, {}).get("pnl", -1) > 0)
    coins_data = sum(1 for c in COINS if s["by_coin"].get(c, {}).get("trades", 0) >= 1)
    if coins_data < 3 or coins_pos < 2:
        return False
    if s["sharpe"] < 0.05:
        return False
    if s["days_with_data"] >= 3 and s["days_positive"] / s["days_with_data"] < 0.5:
        return False
    # At least one of hist/live positive (the other not catastrophic)
    h = s["by_seg"].get("hist", {"pnl": 0.0})
    l = s["by_seg"].get("live", {"pnl": 0.0})
    if h["pnl"] + l["pnl"] < 20:
        return False
    if min(h["pnl"], l["pnl"]) < -50:
        return False
    return True


def select_diverse_portfolio(summaries: List[Dict[str, Any]], max_n: int = 8) -> List[Dict[str, Any]]:
    """Greedy diversity: pick top by score, then skip if highly redundant with already-picked."""
    pool = sorted(summaries, key=robust_score, reverse=True)
    picked: List[Dict[str, Any]] = []
    keys = []
    for s in pool:
        flat = s["flat"]
        # Diversity bucket: (side, time_of_day, vol_regime, entry_min_band, pt_bucket)
        e_min = flat["entry.entry_price_min"]
        pt = flat["exit.profit_target_pct"]
        bucket = (
            flat["entry.side"],
            flat["filter.time_of_day"],
            flat["filter.vol_regime"],
            round(e_min, 2),
            "low_pt" if pt <= 50 else ("mid_pt" if pt <= 100 else "high_pt"),
        )
        if bucket in keys:
            continue
        keys.append(bucket)
        picked.append(s)
        if len(picked) >= max_n:
            break
    return picked


def flat_to_simconfig_dict(flat: dict, sid: str, name: str) -> dict:
    """Convert flat dict back to strategies.yaml shape."""
    e_min = flat["entry.entry_price_min"]
    e_max = e_min + flat["entry.entry_price_max_offset"]
    return {
        "id": sid,
        "name": name,
        "enabled": True,
        "starting_capital_usd": 1000.0,
        "timeframe": "15m",
        "sim_config": {
            "entry": {
                "side": flat["entry.side"],
                "entry_price_min": float(e_min),
                "entry_price_max": float(e_max),
                "drop_magnitude_pct": float(flat["entry.drop_magnitude_pct"]),
                "drop_window_sec": int(flat["entry.drop_window_sec"]),
                "min_time_left_sec": int(flat["entry.min_time_left_sec"]),
                "proximity_max_pct": float(flat["entry.proximity_max_pct"]),
                "min_seconds_into_window": int(flat["entry.min_seconds_into_window"]),
            },
            "exit": {
                "profit_target_pct": float(flat["exit.profit_target_pct"]),
                "stop_loss_pct": flat["exit.stop_loss_pct"],
                "max_hold_sec": int(flat["exit.max_hold_sec"]),
                "trailing_stop_pct": flat["exit.trailing_stop_pct"],
            },
            "filter": {
                "min_book_depth_usd": float(flat["filter.min_book_depth_usd"]),
                "max_spread": float(flat["filter.max_spread"]),
                "book_imbalance_min": flat["filter.book_imbalance_min"],
                "vol_regime": flat["filter.vol_regime"],
                "time_of_day": flat["filter.time_of_day"],
                "multi_tier_entry": 1,
                "correlated_signal_filter": False,
            },
            "human": {
                "reaction_delay_min_sec": 0.0,
                "reaction_delay_max_sec": 2.5,
                "signal_skip_prob": float(flat.get("human.signal_skip_prob", 0.0)),
                "daily_trade_cap": flat.get("human.daily_trade_cap"),
                "post_loss_cooldown_sec": int(flat.get("human.post_loss_cooldown_sec", 60)),
                "concurrent_position_cap": 1,
                "fixed_bet_usd": 10.0,
            },
            "fill": {
                "fee_rate": 0.07,
                "reject_prob": 0.05,
                "use_next_tick_for_fill": True,
                "realistic_fill_model": True,
            },
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweeps", nargs="+", required=True)
    ap.add_argument("--out-md", required=True)
    ap.add_argument("--out-yaml", required=True)
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--use-relaxed", action="store_true")
    args = ap.parse_args()

    summaries = []
    total = 0
    for path in args.sweeps:
        with open(path) as f:
            for line in f:
                total += 1
                summaries.append(summarize(json.loads(line)))
    print(f"Loaded {total} configs.")
    strict = [s for s in summaries if passes_strict(s)]
    print(f"Pass strict gates: {len(strict)}")
    relaxed = [s for s in summaries if passes_relaxed(s) and not passes_strict(s)]
    print(f"Pass relaxed (backup): {len(relaxed)}")

    candidates = strict[:]
    if args.use_relaxed:
        candidates += relaxed
    if not candidates:
        print("WARNING: no candidates met strict gates — using top by score regardless.")
        candidates = sorted([s for s in summaries if s.get("n", 0) >= 15],
                            key=robust_score, reverse=True)[:60]

    picked = select_diverse_portfolio(candidates, max_n=args.top)
    print(f"\nSelected diverse portfolio of {len(picked)}:")

    md = ["# Proposed strategy portfolio"]
    md.append("")
    md.append(f"_Generated {dt.datetime.utcnow().isoformat()}Z from sweeps: {args.sweeps}_")
    md.append("")
    md.append(f"Total configs scanned: {total}. Strict pass: {len(strict)}. Relaxed: {len(relaxed)}.")
    md.append("")
    md.append("## Robustness gates (strict)")
    md.append("- ≥15 trades total, ≥5 in hist AND ≥5 in live")
    md.append("- Both hist AND live PnL strictly positive")
    md.append("- ≥3 of 4 coins positive (with ≥2 trades)")
    md.append("- ≥50% of trading days positive")
    md.append("- Max drawdown ≤70% of peak/total PnL")
    md.append("- Sharpe (per trade) ≥0.10")
    md.append("")
    md.append("## Picks (diversity-filtered)")
    md.append("")
    md.append("| # | id | side | hours | vol | band | drop | PT | SL | trail | n | WR | PnL | hist | live | DD | sharpe |")
    md.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for i, s in enumerate(picked):
        f = s["flat"]
        h = s["by_seg"].get("hist", {"pnl": 0.0})["pnl"]
        l = s["by_seg"].get("live", {"pnl": 0.0})["pnl"]
        md.append(f"| {i+1} | {s['config_id']} | {f['entry.side']} | {f['filter.time_of_day']} | "
                  f"{f['filter.vol_regime']} | {f['entry.entry_price_min']:.2f}-{f['entry.entry_price_min']+f['entry.entry_price_max_offset']:.2f} | "
                  f"{f['entry.drop_magnitude_pct']:.0f}% | {f['exit.profit_target_pct']:.0f}% | "
                  f"{f['exit.stop_loss_pct']} | {f['exit.trailing_stop_pct']} | "
                  f"{s['n']} | {s['wr']*100:.0f}% | {s['pnl']:.1f} | {h:.1f} | {l:.1f} | "
                  f"{s['max_dd']:.1f} | {s['sharpe']:.2f} |")

    md.append("")
    md.append("## Per-pick detail")
    yaml_entries = []
    for i, s in enumerate(picked):
        sid = f"proposed_v1_{i+1:02d}_cid{s['config_id']}"
        name = f"Sweep pick #{i+1} ({s['flat']['entry.side']}, {s['flat']['filter.time_of_day']}, PnL ${s['pnl']:.0f})"
        md.append(f"\n### #{i+1} — `{sid}`")
        md.append(f"- Total {s['n']} trades, {s['wr']*100:.0f}% WR, PnL ${s['pnl']:.1f}, sharpe {s['sharpe']:.2f}, max-DD ${s['max_dd']:.1f}")
        md.append(f"- Segments: hist ${s['by_seg'].get('hist',{}).get('pnl',0):.1f} / live ${s['by_seg'].get('live',{}).get('pnl',0):.1f}")
        cs = ", ".join(
            f"{c}=${s['by_coin'].get(c,{}).get('pnl',0):.0f} ({s['by_coin'].get(c,{}).get('trades',0)})"
            for c in COINS
        )
        md.append(f"- Per coin: {cs}")
        md.append("\n**Day-by-day P&L:**")
        md.append("| date | trades | wins | pnl |")
        md.append("|---|---|---|---|")
        for d in ALL_DATES:
            v = s["by_day"].get(d, {"trades": 0, "wins": 0, "pnl": 0.0})
            md.append(f"| {d} | {v['trades']} | {v['wins']} | {v['pnl']:.2f} |")
        md.append("\n**SimConfig:**")
        md.append("```json")
        md.append(json.dumps(s["flat"], indent=2))
        md.append("```")
        yaml_entries.append(flat_to_simconfig_dict(s["flat"], sid, name))

    Path(args.out_md).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out_md, "w") as fh:
        fh.write("\n".join(md))
    print(f"Markdown → {args.out_md}")

    Path(args.out_yaml).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out_yaml, "w") as fh:
        yaml.safe_dump(yaml_entries, fh, sort_keys=False, default_flow_style=False)
    print(f"YAML → {args.out_yaml}")


if __name__ == "__main__":
    main()
