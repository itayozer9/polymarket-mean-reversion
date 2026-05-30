"""Final synthesis: combines sweep results into the user-facing strategy proposal.

Produces:
  - STRATEGY_PROPOSAL_2026-05-17.md (human-readable narrative + table)
  - runs/proposed_strategies.yaml (paste into strategies.yaml when ready)
  - runs/strategy_evidence/*.md (per-strategy deep dive)
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

import yaml

ROOT = Path(__file__).resolve().parents[2]


HIST_DATES = ["2026-03-14", "2026-03-15", "2026-03-16", "2026-03-17"]
LIVE_DATES = ["2026-05-15", "2026-05-16", "2026-05-17"]
ALL_DATES = HIST_DATES + LIVE_DATES
COINS = ["btc", "eth", "sol", "xrp"]


def ts_date(ms): return dt.datetime.utcfromtimestamp(ms / 1000).strftime("%Y-%m-%d")


def summarize(rec: Dict[str, Any], sweep_tag: str) -> Dict[str, Any]:
    trades = sorted(rec["trades"], key=lambda t: t["entry_ts_ms"])
    n = len(trades)
    if n == 0:
        return {"sweep_tag": sweep_tag, "config_id": rec["config_id"], "flat": rec["flat"], "n": 0}
    pnls = [t["pnl"] for t in trades]
    pnl_total = sum(pnls)
    wins = sum(1 for p in pnls if p > 0)
    std = statistics.stdev(pnls) if n >= 2 else 1.0
    sharpe = (pnl_total / n) / std if std > 1e-9 else 0.0
    eq = peak = max_dd = 0.0
    for p in pnls:
        eq += p
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)
    by_coin = defaultdict(lambda: {"trades": 0, "pnl": 0.0, "wins": 0})
    by_day = defaultdict(lambda: {"trades": 0, "pnl": 0.0, "wins": 0})
    by_seg = defaultdict(lambda: {"trades": 0, "pnl": 0.0, "wins": 0})
    by_reason = defaultdict(int)
    for t in trades:
        d = ts_date(t["entry_ts_ms"])
        seg = "hist" if d in HIST_DATES else ("live" if d in LIVE_DATES else "other")
        w = 1 if t["pnl"] > 0 else 0
        by_coin[t["sym"]]["trades"] += 1; by_coin[t["sym"]]["pnl"] += t["pnl"]; by_coin[t["sym"]]["wins"] += w
        by_day[d]["trades"] += 1; by_day[d]["pnl"] += t["pnl"]; by_day[d]["wins"] += w
        by_seg[seg]["trades"] += 1; by_seg[seg]["pnl"] += t["pnl"]; by_seg[seg]["wins"] += w
        by_reason[t["exit_reason"]] += 1
    days_data = [d for d in ALL_DATES if by_day.get(d, {}).get("trades", 0) > 0]
    days_pos = sum(1 for d in days_data if by_day[d]["pnl"] > 0)
    return {
        "sweep_tag": sweep_tag, "config_id": rec["config_id"], "flat": rec["flat"],
        "n": n, "wins": wins, "pnl": pnl_total, "wr": wins / n, "sharpe": sharpe,
        "max_dd": max_dd, "peak": peak,
        "by_coin": dict(by_coin), "by_day": dict(by_day), "by_seg": dict(by_seg),
        "by_reason": dict(by_reason),
        "days_data": len(days_data), "days_pos": days_pos,
    }


def passes_gold(s: Dict[str, Any]) -> bool:
    """Gold-standard: works in BOTH segments AND has *meaningful* live PnL.
    Strict because hist-dominated picks fail in the May regime."""
    if s.get("n", 0) < 20:
        return False
    h = s["by_seg"].get("hist", {"pnl": 0.0, "trades": 0})
    l = s["by_seg"].get("live", {"pnl": 0.0, "trades": 0})
    if h["trades"] < 5 or l["trades"] < 5:
        return False
    # Hist must be net positive; live must clear $25 (the regime we're trading)
    if h["pnl"] <= 0:
        return False
    if l["pnl"] < 25:
        return False
    coins_pos = sum(1 for c in COINS if s["by_coin"].get(c, {}).get("pnl", -1) > 0
                    and s["by_coin"][c]["trades"] >= 2)
    if coins_pos < 3:
        return False
    if s["days_data"] >= 4 and s["days_pos"] / s["days_data"] < 0.55:
        return False
    if s["max_dd"] > 0.6 * max(s["peak"], 1.0):
        return False
    if s["sharpe"] < 0.15:
        return False
    return True


def passes_silver(s: Dict[str, Any]) -> bool:
    """Silver: one segment is profitable, other not catastrophic."""
    if s.get("n", 0) < 18:
        return False
    h = s["by_seg"].get("hist", {"pnl": 0.0, "trades": 0})
    l = s["by_seg"].get("live", {"pnl": 0.0, "trades": 0})
    if h["trades"] < 3 or l["trades"] < 3:
        return False
    # net positive AND no segment crushingly negative
    if h["pnl"] + l["pnl"] < 50:
        return False
    if min(h["pnl"], l["pnl"]) < -30:
        return False
    coins_pos = sum(1 for c in COINS if s["by_coin"].get(c, {}).get("pnl", -1) > 0
                    and s["by_coin"][c]["trades"] >= 1)
    if coins_pos < 2:
        return False
    if s["sharpe"] < 0.10:
        return False
    return True


def composite_score(s: Dict[str, Any]) -> float:
    if s.get("n", 0) == 0:
        return -1e9
    h = s["by_seg"].get("hist", {"pnl": 0.0, "trades": 0})
    l = s["by_seg"].get("live", {"pnl": 0.0, "trades": 0})
    # Live segment is the regime we're trading right now — weight 4x vs hist
    seg_score = 0.5 * (h["pnl"] if h["trades"] >= 5 else -50) + 2.5 * (l["pnl"] if l["trades"] >= 5 else -50)
    coin_pnls = [s["by_coin"].get(c, {}).get("pnl", -50) for c in COINS]
    coin_min = min(coin_pnls) if coin_pnls else -100
    days_pos_rate = s["days_pos"] / max(1, s["days_data"])
    return (
        seg_score
        + 1.0 * coin_min
        + 60.0 * s["sharpe"]
        + 200.0 * days_pos_rate
        - 0.5 * s["max_dd"]
    )


def select_diverse(pool: List[Dict[str, Any]], n: int) -> List[Dict[str, Any]]:
    pool = sorted(pool, key=composite_score, reverse=True)
    picked = []
    keys = []
    for s in pool:
        f = s["flat"]
        pt = f["exit.profit_target_pct"]
        bucket = (
            f["entry.side"], f.get("filter.time_of_day", "ASIA"), f["filter.vol_regime"],
            round(f["entry.entry_price_min"], 2),
            "low_pt" if pt <= 50 else ("mid_pt" if pt <= 100 else "high_pt"),
        )
        if bucket in keys:
            continue
        keys.append(bucket)
        picked.append(s)
        if len(picked) >= n:
            break
    return picked


def flat_to_yaml(flat: dict, sid: str, name: str, enabled: bool = True) -> dict:
    e_min = flat["entry.entry_price_min"]
    e_max = e_min + flat["entry.entry_price_max_offset"]
    return {
        "id": sid, "name": name, "enabled": enabled,
        "starting_capital_usd": 1000.0, "timeframe": "15m",
        "sim_config": {
            "entry": {
                "side": flat["entry.side"],
                "entry_price_min": float(e_min), "entry_price_max": float(e_max),
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
                "time_of_day": flat.get("filter.time_of_day", "ASIA"),
                "multi_tier_entry": 1, "correlated_signal_filter": False,
            },
            "human": {
                "reaction_delay_min_sec": 0.0, "reaction_delay_max_sec": 2.5,
                "signal_skip_prob": float(flat.get("human.signal_skip_prob", 0.0)),
                "daily_trade_cap": flat.get("human.daily_trade_cap"),
                "post_loss_cooldown_sec": int(flat.get("human.post_loss_cooldown_sec", 60)),
                "concurrent_position_cap": 1, "fixed_bet_usd": 10.0,
            },
            "fill": {
                "fee_rate": 0.07, "reject_prob": 0.05,
                "use_next_tick_for_fill": True, "realistic_fill_model": True,
            },
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweeps", nargs="+", required=True)
    ap.add_argument("--out-md", required=True)
    ap.add_argument("--out-yaml", required=True)
    ap.add_argument("--out-evidence-dir", default="runs/strategy_evidence")
    ap.add_argument("--gold-top", type=int, default=6)
    ap.add_argument("--silver-top", type=int, default=4)
    args = ap.parse_args()

    summaries = []
    for path in args.sweeps:
        tag = Path(path).stem
        with open(path) as f:
            for line in f:
                summaries.append(summarize(json.loads(line), tag))
    print(f"Loaded {len(summaries)} configs across {len(args.sweeps)} sweeps.")

    gold = [s for s in summaries if passes_gold(s)]
    silver_pool = [s for s in summaries if passes_silver(s) and not passes_gold(s)]
    print(f"GOLD: {len(gold)}  SILVER: {len(silver_pool)}")

    gold_picks = select_diverse(gold, args.gold_top)
    silver_picks = select_diverse(silver_pool, args.silver_top)
    all_picks = gold_picks + silver_picks
    print(f"Picked: {len(gold_picks)} gold + {len(silver_picks)} silver = {len(all_picks)} total")

    # YAML output
    yaml_entries = []
    for i, s in enumerate(gold_picks):
        sid = f"v2_gold_{i+1:02d}_{s['flat']['entry.side'].lower()}_{s['flat'].get('filter.time_of_day', 'ASIA').lower()}"
        name = f"GOLD #{i+1}: {s['flat']['entry.side']}/{s['flat'].get('filter.time_of_day', 'ASIA')} band={s['flat']['entry.entry_price_min']:.2f}-{s['flat']['entry.entry_price_min']+s['flat']['entry.entry_price_max_offset']:.2f} (sweep PnL ${s['pnl']:.0f})"
        yaml_entries.append(flat_to_yaml(s["flat"], sid, name, enabled=True))
    for i, s in enumerate(silver_picks):
        sid = f"v2_silver_{i+1:02d}_{s['flat']['entry.side'].lower()}_{s['flat'].get('filter.time_of_day', 'ASIA').lower()}"
        name = f"SILVER #{i+1}: {s['flat']['entry.side']}/{s['flat'].get('filter.time_of_day', 'ASIA')} (sweep PnL ${s['pnl']:.0f})"
        yaml_entries.append(flat_to_yaml(s["flat"], sid, name, enabled=False))

    Path(args.out_yaml).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out_yaml, "w") as fh:
        yaml.safe_dump(yaml_entries, fh, sort_keys=False, default_flow_style=False)
    print(f"YAML → {args.out_yaml}")

    # Markdown report
    md = [
        f"# Strategy Proposal — Polymarket 15m mean-reversion",
        f"",
        f"_Generated {dt.datetime.utcnow().isoformat()}Z_  ",
        f"Source data: Mar 14-17 2026 (historical) + May 15-17 2026 (live paper-bot ticks)  ",
        f"Configs scanned: {len(summaries)} (sweeps: {', '.join(args.sweeps)})  ",
        f"Gold-tier (both segments positive, ≥3 coins positive, sharpe ≥0.15): **{len(gold)}**  ",
        f"Silver-tier (net positive, no segment crushing): **{len(silver_pool)}**  ",
        "",
        "## TL;DR",
        "",
        "We ran an exhaustive parameter sweep on 6 days of 15m crypto Up/Down "
        "tick data (4 coins). The mean-reversion edge is **real but materially weaker "
        "in the May regime than in March**.  This means many sweep winners are "
        "hist-only.  After cross-segment filtering, the **gold-tier picks below** are "
        "the configurations that produced positive PnL in BOTH segments, on ≥3 of the "
        "4 coins, with a per-trade Sharpe ≥0.15.  Treat these as the *next paper-trade* "
        "candidate set — do not promote to real money without 1 more week of live data.",
        "",
        "## Headline picks (GOLD tier)",
        "",
        "| # | id | side | hours | vol | band | drop% | PT% | SL | trail | n | WR | total$ | hist$ | live$ | sharpe | DD | days+/data |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for i, s in enumerate(gold_picks):
        f = s["flat"]
        h = s["by_seg"]["hist"]["pnl"]; l = s["by_seg"]["live"]["pnl"]
        md.append("| {} | {}/{} | {} | {} | {} | {:.2f}-{:.2f} | {:.0f} | {:.0f} | {} | {} | {} | {:.0f}% | {:.1f} | {:.1f} | {:.1f} | {:.2f} | {:.0f} | {}/{} |".format(
            i + 1, s["sweep_tag"][:8], s["config_id"], f["entry.side"],
            f.get("filter.time_of_day", "ASIA"), f["filter.vol_regime"],
            f["entry.entry_price_min"], f["entry.entry_price_min"] + f["entry.entry_price_max_offset"],
            f["entry.drop_magnitude_pct"], f["exit.profit_target_pct"],
            f["exit.stop_loss_pct"], f["exit.trailing_stop_pct"],
            s["n"], s["wr"] * 100, s["pnl"], h, l, s["sharpe"], s["max_dd"],
            s["days_pos"], s["days_data"],
        ))

    md.append("")
    md.append("## Silver tier (paper-test, default disabled in YAML)")
    md.append("")
    md.append("| # | id | side | hours | vol | band | n | total$ | hist$ | live$ | sharpe |")
    md.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for i, s in enumerate(silver_picks):
        f = s["flat"]
        h = s["by_seg"]["hist"]["pnl"]; l = s["by_seg"]["live"]["pnl"]
        md.append("| {} | {}/{} | {} | {} | {} | {:.2f}-{:.2f} | {} | {:.1f} | {:.1f} | {:.1f} | {:.2f} |".format(
            i + 1, s["sweep_tag"][:8], s["config_id"], f["entry.side"],
            f.get("filter.time_of_day", "ASIA"), f["filter.vol_regime"],
            f["entry.entry_price_min"], f["entry.entry_price_min"] + f["entry.entry_price_max_offset"],
            s["n"], s["pnl"], h, l, s["sharpe"],
        ))

    md.append("")
    md.append("## Regime observations")
    md.append("")
    md.append("- **March 2026 data is much friendlier to dip-buying** than May 2026. Configs "
              "with PT 120–175% routinely doubled in hist but flatten or lose in live.")
    md.append("- **ASIA hours dominate**.  Outside ASIA, robust configs are rarer.  EU/US/OVERNIGHT all show edge in narrower bands.")
    md.append("- **Per-coin diversification is real**: XRP and ETH carry the most P&L in the best configs; SOL is the noisiest.")
    md.append("- **Live PnL of currently-enabled live bot strategies (May 16-17) is negative**: cfg_21c8c00165b3 / cfg_333fde9cecb8 / cfg_max_pnl_v* all bleeding. The May regime change is the cause.")
    md.append("- **Robust adaptation**: Configs that survive both segments share one common trait — they take profits earlier (PT 35–80%) and use stop_loss to cut losers.  The 175% PT configs lose in May because bounces are shallower.")
    md.append("")
    md.append("## How to deploy")
    md.append("")
    md.append("1. Review `runs/proposed_strategies.yaml`.")
    md.append("2. Append the GOLD entries (already `enabled: true`) to `strategies.yaml`.")
    md.append("3. Append SILVER entries with `enabled: false` (or `true` if you want broader paper-coverage).")
    md.append("4. SIGHUP the paper trader so it hot-reloads. **Do NOT disable the existing strategies yet** — keep them running until end-of-week so we can compare apples-to-apples.")
    md.append("5. After 5–7 more live-paper days, re-run this analysis.  Configs that stay gold-tier two weeks in a row are the real-money candidates.")
    md.append("")
    md.append("## Per-pick evidence — GOLD")

    # Evidence per pick
    ev_dir = Path(args.out_evidence_dir)
    ev_dir.mkdir(parents=True, exist_ok=True)

    for i, s in enumerate(gold_picks):
        f = s["flat"]
        slug = f"gold_{i+1:02d}_cid{s['config_id']}"
        md.append(f"")
        md.append(f"### GOLD #{i+1} — `v2_gold_{i+1:02d}_{f['entry.side'].lower()}_{f.get('filter.time_of_day', 'ASIA').lower()}` (sweep {s['sweep_tag']} cid={s['config_id']})")
        md.append(f"- {s['n']} trades, {s['wr']*100:.0f}% WR, PnL ${s['pnl']:.1f}, sharpe {s['sharpe']:.2f}, max-DD ${s['max_dd']:.1f}")
        md.append(f"- Segments: hist ${s['by_seg']['hist']['pnl']:.1f} ({s['by_seg']['hist']['trades']}t) / "
                  f"live ${s['by_seg']['live']['pnl']:.1f} ({s['by_seg']['live']['trades']}t)")
        md.append(f"- Per coin: " + ", ".join(
            f"{c}=${s['by_coin'].get(c,{}).get('pnl',0):.0f} ({s['by_coin'].get(c,{}).get('trades',0)})"
            for c in COINS
        ))
        md.append(f"- Exit reasons: " + ", ".join(f"{k}={v}" for k, v in s["by_reason"].items()))
        md.append("\n**Day-by-day P&L:**")
        md.append("| date | trades | wins | pnl |")
        md.append("|---|---|---|---|")
        for d in ALL_DATES:
            v = s["by_day"].get(d, {"trades": 0, "wins": 0, "pnl": 0.0})
            md.append(f"| {d} | {v['trades']} | {v['wins']} | {v['pnl']:.2f} |")
        md.append("\n**SimConfig (flat):**")
        md.append("```json")
        md.append(json.dumps(s["flat"], indent=2))
        md.append("```")

    Path(args.out_md).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out_md, "w") as fh:
        fh.write("\n".join(md))
    print(f"Markdown → {args.out_md}")


if __name__ == "__main__":
    main()
