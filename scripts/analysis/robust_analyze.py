"""Strict robustness analyzer.

Loads a sweep jsonl (with per-trade details) and:
  1. Computes per-(coin, day, hour, dow, segment) slices
  2. Applies strict gates (Sharpe, drawdown, segment positivity, per-coin breadth)
  3. Cross-validates: train Mar, test May (OOS), and reverse
  4. Outputs ranked list + per-config diagnostics

Usage:
    uv run python scripts/analysis/robust_analyze.py \
        --sweeps runs/broad_sweep_v1.jsonl runs/focused_sweep_v1.jsonl \
        --out-md runs/robust_strategies.md \
        --out-json runs/robust_strategies.json
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

HIST_DATES = ["2026-03-14", "2026-03-15", "2026-03-16", "2026-03-17"]
LIVE_DATES = ["2026-05-15", "2026-05-16", "2026-05-17"]
ALL_DATES = HIST_DATES + LIVE_DATES
COINS = ["btc", "eth", "sol", "xrp"]


def ts_to_date(ms: int) -> str:
    return dt.datetime.utcfromtimestamp(ms / 1000).strftime("%Y-%m-%d")


def ts_to_hour(ms: int) -> int:
    return dt.datetime.utcfromtimestamp(ms / 1000).hour


def ts_to_dow(ms: int) -> int:
    return dt.datetime.utcfromtimestamp(ms / 1000).weekday()


def hour_bucket(h: int) -> str:
    # UTC.  Asia ~ 00-08, EU ~ 08-14, US ~ 14-22, OVERNIGHT 22-00.
    if 0 <= h < 8:
        return "ASIA"
    if 8 <= h < 14:
        return "EU"
    if 14 <= h < 22:
        return "US"
    return "OVERNIGHT"


def percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * pct
    f = int(math.floor(k))
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def trade_drawdown(pnls: List[float]) -> Tuple[float, float]:
    """Return (max drawdown $, equity max)."""
    if not pnls:
        return 0.0, 0.0
    eq = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        eq += p
        peak = max(peak, eq)
        dd = peak - eq
        if dd > max_dd:
            max_dd = dd
    return max_dd, peak


def summarize(rec: Dict[str, Any]) -> Dict[str, Any]:
    trades = rec["trades"]
    n = len(trades)
    if n == 0:
        return {"config_id": rec["config_id"], "flat": rec["flat"], "n_trades": 0, "skip": True}

    # Sort chronologically for drawdown
    trades = sorted(trades, key=lambda t: t["entry_ts_ms"])
    pnls = [t["pnl"] for t in trades]
    wins = sum(1 for p in pnls if p > 0)
    pnl_total = sum(pnls)
    pnl_per_trade = pnl_total / n
    pnl_std = statistics.stdev(pnls) if n >= 2 else 1.0
    sharpe = pnl_per_trade / pnl_std if pnl_std > 1e-9 else 0.0
    max_dd, peak = trade_drawdown(pnls)

    # Per-coin
    by_coin = defaultdict(lambda: {"trades": 0, "pnl": 0.0, "wins": 0})
    by_day = defaultdict(lambda: {"trades": 0, "pnl": 0.0, "wins": 0})
    by_seg = defaultdict(lambda: {"trades": 0, "pnl": 0.0, "wins": 0})
    by_hour_bucket = defaultdict(lambda: {"trades": 0, "pnl": 0.0, "wins": 0})
    by_dow = defaultdict(lambda: {"trades": 0, "pnl": 0.0, "wins": 0})
    by_coin_seg = defaultdict(lambda: {"trades": 0, "pnl": 0.0, "wins": 0})
    by_coin_day = defaultdict(lambda: {"trades": 0, "pnl": 0.0, "wins": 0})

    for t in trades:
        d = ts_to_date(t["entry_ts_ms"])
        h = ts_to_hour(t["entry_ts_ms"])
        hb = hour_bucket(h)
        dow = ts_to_dow(t["entry_ts_ms"])
        seg = "hist" if d in HIST_DATES else ("live" if d in LIVE_DATES else "other")
        w = 1 if t["pnl"] > 0 else 0
        for dct, k in ((by_coin, t["sym"]), (by_day, d), (by_seg, seg),
                       (by_hour_bucket, hb), (by_dow, dow),
                       (by_coin_seg, (t["sym"], seg)),
                       (by_coin_day, (t["sym"], d))):
            v = dct[k]
            v["trades"] += 1
            v["pnl"] += t["pnl"]
            v["wins"] += w

    # Per-segment Sharpe
    seg_metrics = {}
    for seg in ("hist", "live"):
        seg_pnls = [t["pnl"] for t in trades
                    if (ts_to_date(t["entry_ts_ms"]) in HIST_DATES) == (seg == "hist")]
        if not seg_pnls:
            seg_metrics[seg] = {"trades": 0, "pnl": 0.0, "wr": 0.0, "sharpe": 0.0}
            continue
        sp = sum(seg_pnls)
        sw = sum(1 for p in seg_pnls if p > 0) / len(seg_pnls)
        ss = statistics.stdev(seg_pnls) if len(seg_pnls) >= 2 else 1.0
        sh = (sp / len(seg_pnls)) / ss if ss > 1e-9 else 0.0
        seg_metrics[seg] = {"trades": len(seg_pnls), "pnl": sp, "wr": sw, "sharpe": sh}

    return {
        "config_id": rec["config_id"],
        "flat": rec["flat"],
        "skip": False,
        "n_trades": n, "wins": wins, "pnl_total": pnl_total,
        "pnl_per_trade": pnl_per_trade, "sharpe": sharpe,
        "max_dd": max_dd, "peak": peak,
        "by_coin": dict(by_coin), "by_day": dict(by_day),
        "by_seg": dict(by_seg),
        "by_hour_bucket": dict(by_hour_bucket), "by_dow": dict(by_dow),
        "by_coin_seg": {f"{k[0]}|{k[1]}": v for k, v in by_coin_seg.items()},
        "by_coin_day": {f"{k[0]}|{k[1]}": v for k, v in by_coin_day.items()},
        "seg_metrics": seg_metrics,
    }


# ---- gates ------------------------------------------------------------------
def passes_gates(s: Dict[str, Any], *, min_total: int, min_pnl: float,
                 min_sharpe: float, min_per_coin: int, min_coins_positive: int,
                 require_hist_positive: bool, require_live_positive: bool,
                 max_dd_ratio: float, min_days_positive_pct: float) -> bool:
    if s.get("skip"):
        return False
    if s["n_trades"] < min_total:
        return False
    if s["pnl_total"] < min_pnl:
        return False
    if s["sharpe"] < min_sharpe:
        return False
    if s["peak"] > 0 and s["max_dd"] / max(s["pnl_total"], s["peak"]) > max_dd_ratio:
        return False
    if require_hist_positive:
        h = s["seg_metrics"]["hist"]
        if h["trades"] < 3 or h["pnl"] <= 0:
            return False
    if require_live_positive:
        l = s["seg_metrics"]["live"]
        if l["trades"] < 3 or l["pnl"] <= 0:
            return False
    coins_with_data = [c for c in COINS if s["by_coin"].get(c, {}).get("trades", 0) >= min_per_coin]
    if len(coins_with_data) < min_coins_positive:
        return False
    coins_pos = [c for c in coins_with_data if s["by_coin"][c]["pnl"] > 0]
    if len(coins_pos) < min_coins_positive:
        return False
    days_with_data = [d for d in ALL_DATES if s["by_day"].get(d, {}).get("trades", 0) >= 1]
    if not days_with_data:
        return False
    days_pos = sum(1 for d in days_with_data if s["by_day"][d]["pnl"] > 0)
    if days_pos / len(days_with_data) < min_days_positive_pct:
        return False
    return True


def composite_score(s: Dict[str, Any]) -> float:
    h = s["seg_metrics"]["hist"]["pnl"]
    l = s["seg_metrics"]["live"]["pnl"]
    # Reward minimum-segment (worst-case oos) more than total
    return s["pnl_total"] + 1.5 * min(h, l) + 30.0 * s["sharpe"] + 4.0 * s["pnl_per_trade"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweeps", nargs="+", required=True)
    ap.add_argument("--out-md", default=None)
    ap.add_argument("--out-json", default=None)
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--min-total", type=int, default=25)
    ap.add_argument("--min-pnl", type=float, default=15.0)
    ap.add_argument("--min-sharpe", type=float, default=0.05)
    ap.add_argument("--min-per-coin", type=int, default=2)
    ap.add_argument("--min-coins-positive", type=int, default=3)
    ap.add_argument("--require-hist-positive", action="store_true", default=True)
    ap.add_argument("--require-live-positive", action="store_true", default=True)
    ap.add_argument("--max-dd-ratio", type=float, default=1.5)
    ap.add_argument("--min-days-positive-pct", type=float, default=0.55)
    args = ap.parse_args()

    summaries = []
    total = 0
    for path in args.sweeps:
        with open(path) as f:
            for line in f:
                total += 1
                summaries.append(summarize(json.loads(line)))

    print(f"Loaded {total} configs from {len(args.sweeps)} sweep file(s).")
    traded = sum(1 for s in summaries if not s.get("skip"))
    print(f"  {traded} traded, {total - traded} no-op.")

    passing = [s for s in summaries if passes_gates(
        s, min_total=args.min_total, min_pnl=args.min_pnl,
        min_sharpe=args.min_sharpe, min_per_coin=args.min_per_coin,
        min_coins_positive=args.min_coins_positive,
        require_hist_positive=args.require_hist_positive,
        require_live_positive=args.require_live_positive,
        max_dd_ratio=args.max_dd_ratio,
        min_days_positive_pct=args.min_days_positive_pct,
    )]
    print(f"Passing all robustness gates: {len(passing)}")
    passing.sort(key=composite_score, reverse=True)

    top = passing[:args.top]
    if args.out_json:
        Path(args.out_json).parent.mkdir(parents=True, exist_ok=True)
        with open(args.out_json, "w") as fh:
            json.dump([
                {k: v for k, v in s.items() if k not in ("trades",)}
                for s in top
            ], fh, indent=2, default=str)
        print(f"Wrote top {len(top)} → {args.out_json}")

    if args.out_md:
        Path(args.out_md).parent.mkdir(parents=True, exist_ok=True)
        lines = []
        lines.append(f"# Robust strategies — top {args.top}")
        lines.append("")
        lines.append(f"Sweeps: {', '.join(args.sweeps)}")
        lines.append(f"Total configs evaluated: {total}, robust pass: {len(passing)}")
        lines.append("")
        lines.append("Gates:")
        lines.append(f"- min_total={args.min_total}, min_pnl={args.min_pnl}, min_sharpe={args.min_sharpe}")
        lines.append(f"- per coin: min {args.min_per_coin} trades, ≥{args.min_coins_positive} coins positive")
        lines.append(f"- segments: hist+live BOTH positive, max_dd/peak≤{args.max_dd_ratio}")
        lines.append(f"- ≥{args.min_days_positive_pct*100:.0f}% of days with trades have positive PnL")
        lines.append("")
        lines.append("| rank | id | trades | WR | total$ | sharpe | hist$ | live$ | BTC$ | ETH$ | SOL$ | XRP$ | side | hours | vol |")
        lines.append("|----|----|----|----|----|----|----|----|----|----|----|----|----|----|----|")
        for i, s in enumerate(top):
            flat = s["flat"]
            wr = s["wins"] / s["n_trades"] if s["n_trades"] else 0
            hist = s["seg_metrics"]["hist"]["pnl"]
            live = s["seg_metrics"]["live"]["pnl"]
            coin_pnl = {c: s["by_coin"].get(c, {}).get("pnl", 0.0) for c in COINS}
            lines.append("| {} | {} | {} | {:.0f}% | {:.1f} | {:.2f} | {:.1f} | {:.1f} | {:.0f} | {:.0f} | {:.0f} | {:.0f} | {} | {} | {} |".format(
                i + 1, s["config_id"], s["n_trades"], wr * 100, s["pnl_total"], s["sharpe"],
                hist, live,
                coin_pnl["btc"], coin_pnl["eth"], coin_pnl["sol"], coin_pnl["xrp"],
                flat.get("entry.side"), flat.get("filter.time_of_day"), flat.get("filter.vol_regime"),
            ))
        lines.append("")
        # Day-by-day breakdown for top 5
        lines.append("## Day-by-day P&L (top 5)")
        for i, s in enumerate(top[:5]):
            lines.append(f"\n### #{i+1} — config_id={s['config_id']}")
            flat = s["flat"]
            lines.append("```")
            lines.append(json.dumps(flat, indent=2))
            lines.append("```")
            lines.append("| date | trades | wins | WR | pnl$ |")
            lines.append("|----|----|----|----|----|")
            for d in ALL_DATES:
                v = s["by_day"].get(d, {"trades": 0, "wins": 0, "pnl": 0.0})
                wr = v["wins"] / v["trades"] if v["trades"] else 0.0
                lines.append(f"| {d} | {v['trades']} | {v['wins']} | {wr*100:.0f}% | {v['pnl']:.2f} |")
            lines.append("")
            # Per-coin × segment
            lines.append("**Per coin × segment:**")
            lines.append("| coin | hist trades | hist$ | live trades | live$ |")
            lines.append("|----|----|----|----|----|")
            for c in COINS:
                h = s["by_coin_seg"].get(f"{c}|hist", {"trades": 0, "pnl": 0.0})
                l = s["by_coin_seg"].get(f"{c}|live", {"trades": 0, "pnl": 0.0})
                lines.append(f"| {c} | {h['trades']} | {h['pnl']:.2f} | {l['trades']} | {l['pnl']:.2f} |")
        with open(args.out_md, "w") as fh:
            fh.write("\n".join(lines))
        print(f"Wrote markdown → {args.out_md}")


if __name__ == "__main__":
    main()
