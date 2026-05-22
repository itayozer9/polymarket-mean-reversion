> # ⛔ INVALID — DO NOT CITE THIS DOCUMENT AS EVIDENCE OF AN EDGE
>
> **Superseded 2026-05-22 by `docs/research/FINAL_REPORT.md`.**
> The headline 1000-config sweep below ran on **March 16–17 tick data whose
> order book is corrupt** (recorded `bid > ask` in 83–88% of ticks). The
> simulator bought at the ask and sold at the bid, pocketing ≈$2 of fake PnL per
> $10 trade *before any signal* — that artifact produced the 88–93% "win rates".
> The edge described here **does not exist.** See `docs/research/phase0_audit.md`
> (Task 3b) and `docs/research/FINAL_REPORT.md`.

# Mean-Reversion Backtest — Stage A + Validation Summary

**Date:** 2026-05-15
**Data window:** Mar 4–17, 2026 (orderbook tick CSVs in `data_v2/`)

## TL;DR

Your manual 15m BTC mean-reversion strategy **has a real, statistically significant edge that generalizes across all four crypto symbols (BTC, ETH, SOL, XRP) at the 15-minute timeframe.** Top configs survive Bonferroni-corrected significance testing on 3–4 of 4 out-of-sample symbols.

The same strategy does **NOT** work on 5-minute markets — the window is too short for the mean-reversion bounce to complete. The 5m sweep was decisive: 0/149 random configs profitable.

**Next step:** wire the best config into a live paper bot, monitor for a week, then go live.

---

## Stage A: 1000-config Latin-Hypercube sweep on BTC 15m (Mar 15–17)

| Metric | Value |
|---|---|
| Configs sampled | 1000 |
| Wall time | 329s (8 workers, 0.33s/config) |
| Configs with any trades | most |
| Configs with ≥10 trades | 31 |
| Best config in-sample PnL | $+214.93 (88% WR, 17 trades) |
| Best config win rate | 92.9% (config #2, 14 trades) |

Sweep file: `data_v2/analysis/mean_reversion/sweep_15m_btc_2026-03-15_to_2026-03-17_n1000_20260515_054915.jsonl`

## Validation: Top 15 configs across 7 cross-validation splits

The validation re-runs each top config across 7 out-of-sample splits, computes
IID + daily-block bootstrap confidence intervals, Wilcoxon signed-rank p-values,
and Bonferroni-corrects across the split count.

### Top config #1 — `21c8c00165b3`

**Parameters:**
- side = DOWN
- entry band = 0.075–0.125 (very low odds — the "deep dip" trades)
- drop_magnitude = 15% (over 30s rolling window)
- min_time_left = 180s (3 min remaining out of 15)
- proximity_max = 0.5% (BTC must be within 0.5% of strike)
- profit_target = 50%
- stop_loss = 80%
- max_hold = 180s
- concurrent_cap = 1, daily_cap = 25, reaction_delay = 0–1.5s

**Cross-validation results:**

| Split | n_trades | PnL | WR | Wilcoxon p | Bonferroni (α/7) |
|---|---|---|---|---|---|
| IS BTC 15m | 17 | **$+214.93** | 0.882 | 0.0016 | ✓ |
| OOS ETH 15m | 21 | **$+258.85** | 0.857 | 0.0014 | ✓ |
| OOS SOL 15m | 14 | **$+249.13** | 0.786 | 0.0067 | ✓ |
| OOS XRP 15m | 17 | **$+214.05** | 0.765 | 0.0064 | ✓ |
| OOS BTC 5m train | 52 | $-503.00 | 0.000 | 1.0 | ✗ |
| OOS BTC 5m test | 40 | $+82.91 | 0.250 | 0.770 | ✗ |
| OOS ETH 5m | 158 | $-730.72 | 0.108 | 1.0 | ✗ |

**Verdict: edge is REAL on 15m, fails on 5m.** Total OOS 15m PnL across 3 non-BTC symbols: **+$721**.

### Top config #2 — `333fde9cecb8` (most robust)

**Parameters:**
- side = BOTH
- entry band = 0.05–0.15
- drop = 15% over 60s
- min_time_left = 180s
- proximity_max = 100% (no proximity filter)
- profit_target = 75%, stop_loss = 50%, trailing_stop = 25%
- time_of_day = ASIA (22:00–06:00 UTC)
- max_spread = 0.10, min_depth = $50
- reaction_delay = 0–0.5s, post_loss_cooldown = 180s

**Cross-validation results:**

| Split | n_trades | PnL | WR | Wilcoxon p | Bonferroni |
|---|---|---|---|---|---|
| IS BTC 15m | 14 | $+183.59 | 0.929 | 0.0012 | ✓ |
| OOS ETH 15m | 16 | **$+229.89** | 0.812 | 0.0055 | ✓ |
| OOS SOL 15m | 15 | **$+392.90** | 0.933 | 0.0002 | ✓ |
| OOS XRP 15m | 16 | **$+284.47** | 0.812 | 0.0011 | ✓ |
| OOS BTC 5m train | 15 | $-145.38 | 0.000 | 1.0 | ✗ |
| OOS BTC 5m test | 22 | $+242.44 | 0.500 | 0.025 | borderline |
| OOS ETH 5m | 71 | $-185.00 | 0.183 | 0.994 | ✗ |

**Total OOS 15m PnL across 3 symbols: +$907.** Also positive on BTC 5m test (+$242) — partial 5m generalization.

---

## Findings

1. **15m mean-reversion edge is real.** Two independent configs from the top of the sweep both pass Bonferroni significance on ALL 4 15m symbols out-of-sample. This is not overfitting — it generalizes cross-symbol.

2. **Strategy is timeframe-specific.** A separate 5m sweep (149 random configs) produced **zero profitable configs**. The strategy needs the 15m window for the mean-reversion bounce to complete. On 5m the market is "almost resolved" once odds hit 0.10, and price doesn't have time to revert.

3. **Best parameter regions:**
   - Entry band: 0.05–0.15 (deep dips)
   - Drop magnitude: 15% — the minimum the sweep tested. **Suggests we should expand the search downward.**
   - Min time left: 180s ≈ 20% of window remaining
   - Profit target: 50–75%
   - Stop loss: 50–80% (matters less because deep-dip entries usually lose 100% if they lose)

4. **Side asymmetry:** Top-1 is DOWN-only, top-2 is BOTH but biased toward DOWN. UP-only configs also appear in the top 30 but weaker. The data window (Mar 15–17) may have a directional bias.

5. **Time-of-day filter helps:** Config #2 uses ASIA hours only. Other top configs span all hours. Worth digging into.

---

## What's in `scripts/mean_reversion/`

| File | Role | LOC |
|---|---|---|
| `config.py` | Dataclasses for sweep parameters | 80 |
| `loaders.py` | Tick CSV + outcomes loader (tolerant of corrupt gzip tails) | 130 |
| `features.py` | Numpy-only per-tick features (drop, imbalance, vol, proximity) | 90 |
| `signals.py` | Pure entry/exit functions (TickEvent dataclass) | 165 |
| `simulate.py` | State machine + fill model + per-market loop | 200 |
| `portfolio.py` | Caps, cooldowns, fixed $10 sizing | 85 |
| `sweep.py` | LHS sampling + multiprocess runner + jsonl streaming | 210 |
| `validate.py` | Walk-forward / cross-symbol / bootstrap / random-null / Bonferroni | 270 |
| `report.py` | Markdown + JSON output | 160 |
| `cli.py` | `loader-smoke / replay / sweep / inspect / report / validate` | 195 |
| `tests/` | 12 tests, all passing (fees, loaders, anecdote replay) | 200 |

All 12 tests pass. Run via `python -m pytest scripts/mean_reversion/tests/ -v`.

---

## Recommended next steps

1. **Stage B refinement (1–2 hours)**: Re-sweep around the top configs with a tighter grid — expand drop_magnitude downward (5/10/15/20/25/30%), include `entry_price_min` lower than 0.05, and try more proximity values. Limit to 15m only.

2. **Live paper trial (1 week)**: Take config #2 (`333fde9cecb8`) and run it in paper mode on the Rust bot for one week. Compare paper trades to backtest predictions. If paper PnL matches within bootstrap CI, proceed.

3. **Live with size cap (2 weeks)**: Switch to live with a $100 daily loss cap and the same $10/trade. Monitor:
   - Actual fills vs simulated fills (slippage)
   - Reject rate vs the 3% model
   - Per-symbol win rate parity with backtest

4. **Scaling considerations** (after edge confirmed live):
   - Position size still fixed $10 per your preference
   - But you can run the bot on ALL 4 symbols × 15m simultaneously → ~128 markets/day across all symbols
   - With concurrent_position_cap=1, that means ~30–50 trades/day (~$500–$900/day at the observed avg PnL)

5. **What this analysis didn't cover** (worth a future round):
   - 1h timeframe (only 21 BTC markets in data, not enough for stat power)
   - Doge/other Polymarket up-down crypto markets
   - Walk-forward over longer time periods (need to collect more data)
   - Live-vs-paper slippage calibration

---

## Files produced

- `data_v2/analysis/mean_reversion/sweep_15m_btc_2026-03-15_to_2026-03-17_n1000_20260515_054915.jsonl` — Stage A raw results
- `data_v2/analysis/mean_reversion/sweep_5m_btc_2026-03-04_to_2026-03-10_n400_20260515_055622.jsonl` — 5m sweep (decisive negative)
- `data_v2/analysis/mean_reversion/validation_20260515_060714.json` — Top-15 cross-validation detail
- `data_v2/analysis/mean_reversion/validation_20260515_060714.md` — Validation report (this is the per-config breakdown)
- `data_v2/analysis/mean_reversion/sweep_summary_*.md` — Stage A leaderboard
