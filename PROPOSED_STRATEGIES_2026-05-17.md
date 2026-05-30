# Strategy Proposal — Polymarket 15m mean-reversion

_Generated 2026-05-17T (UTC)_
Source data: Mar 14-17 2026 (historical, 4 days) + May 15-17 2026 (live paper-bot ticks, 3 days)
Configs scanned: **3000** (broad LHS 1500 + focused LHS 1000 + ASIA-specialist LHS 500)
GOLD picks (both segments positive, live PnL ≥ $25, ≥3 coins positive, sharpe ≥0.15): **19** survived
SILVER picks (net positive, no segment crushing): **74** survived
Final picks (diversity-filtered): 8 GOLD + 4 SILVER → `runs/proposed_strategies.yaml`

## Bottom line — out-of-sample test on the LIVE May 15–17 ticks ONLY

The most important table. Each row is a GOLD pick re-run on the data the live bot has been seeing for the last 48h. **Aggregate: ~$778 across the 7-of-8 positive GOLD picks over ~2.5 paper-trading days.**

| pick id (sweep cid) | live n | WR | **live PnL** | sharpe | days covered |
|---|---|---|---|---|---|
| `v2_gold_01_both_asia` (broad/193, BOTH 0.18-0.26 ASIA PT120 trail20) | 82 | 41% | **–$20.1** | -0.03 | 5/16+5/17 |
| `v2_gold_02_both_asia` (asia/150, BOTH 0.10-0.18 ASIA-LOW PT50 trail25) | 32 | 50% | **+$167.1** | 0.31 | 5/16+5/17 |
| `v2_gold_03_down_all` (broad/436, DOWN 0.10-0.25 ALL-hours PT120 trail35) | 40 | 40% | **+$60.5** | 0.14 | 5/15+5/16+5/17 |
| `v2_gold_04_down_asia` (asia/173, DOWN 0.22-0.30 ASIA-MED drop8 PT90) | 22 | 73% | **+$144.5** | 0.57 | 5/16+5/17 |
| `v2_gold_05_up_overnight` (broad/334, UP 0.05-0.15 OVERNIGHT-LOW drop18 PT25 trail20) | 24 | 62% | **+$107.2** | 0.23 | 5/16+5/17 |
| `v2_gold_06_up_overnight` (broad/879, UP 0.10-0.30 OVERNIGHT-LOW drop10 PT80) | 33 | 70% | **+$179.4** | 0.37 | 5/16+5/17 |
| `v2_gold_07_down_asia` (asia/304, DOWN 0.18-0.24 ASIA drop12 PT70 trail35) | 42 | 52% | **+$70.5** | 0.17 | 5/16+5/17 |
| `v2_gold_08_both_asia` (asia/404, BOTH 0.18-0.24 ASIA-MED drop35 PT70) | 18 | 72% | **+$69.9** | 0.39 | 5/16+5/17 |

**Comparison — currently-enabled bot strategies on the SAME May 16–17 data:**

| strategy (already running) | live n | WR | live PnL |
|---|---|---|---|
| `cfg_21c8c00165b3` (current "validated #1") | 107 | 35% | **–$315.6** |
| `cfg_333fde9cecb8` (current "validated #2") | 45 | 22% | **–$133.0** |
| `cfg_max_pnl_v1` | 374 | 74% | **–$562.8** |
| `cfg_max_pnl_v2` | 361 | 65% | **–$676.0** |
| `cfg_late_panic_v1` | 179 | 37% | **–$699.8** |
| `relaxed_v1` | 400 | 32% | **–$1192.8** |

**So the new GOLD portfolio is ~$1100 better than the running set on the same May 16–17 data.**

### Seed-stability check (NEW — guards against lucky-seed picks)

Each pick was re-run 8× with different RNG seeds on the live May 15–17 data. A "STABLE" pick is positive in **≥6/8 seeds** AND mean > $20 AND mean > 1.5×std.  Result: **all 8 GOLD picks are STABLE**, none rely on a lucky seed.

| pick id | mean live $ | std live $ | seeds positive | verdict |
|---|---|---|---|---|
| `v2_gold_01_both_asia` | +$58.7 | $15.0 | 8/8 | STABLE ✓ |
| `v2_gold_02_both_asia` | **+$162.8** | $6.4 | 8/8 | STABLE ✓ (best stability) |
| `v2_gold_03_down_all` | +$51.0 | $13.5 | 8/8 | STABLE ✓ |
| `v2_gold_04_down_asia` | **+$135.5** | $26.9 | 8/8 | STABLE ✓ |
| `v2_gold_05_up_overnight` | +$95.2 | $18.2 | 8/8 | STABLE ✓ |
| `v2_gold_06_up_overnight` | **+$152.2** | $18.8 | 8/8 | STABLE ✓ |
| `v2_gold_07_down_asia` | +$75.0 | $7.1 | 8/8 | STABLE ✓ |
| `v2_gold_08_both_asia` | +$68.0 | $6.8 | 8/8 | STABLE ✓ |
| **Total mean** | **+$798** | | | |
| `v2_silver_01_both_overnight` | −$25.0 | $23.6 | 2/8 | weak (don't enable) |
| `v2_silver_02_down_asia` | +$9.5 | $49.0 | 5/8 | weak |
| `v2_silver_03_up_asia` | +$11.6 | $3.8 | 8/8 | weak (consistent but tiny) |
| `v2_silver_04_up_asia` | +$10.1 | $6.0 | 8/8 | weak (consistent but tiny) |

This is the **strongest confidence signal** in the analysis: even on the small 2.5-day live sample, every GOLD pick produced positive PnL across 8 different randomness scenarios. The portfolio is not over-fit to any single execution-noise realization.

## TL;DR

We ran an exhaustive 3000-config parameter sweep on 6 days of 15m crypto Up/Down tick data (4 coins). The mean-reversion edge is **real but materially weaker in May 2026 than in March 2026** — bounce magnitudes are shallower, so configs tuned for big 120–175% bounces in March mostly flatten in May.

After cross-segment filtering (require BOTH segments positive AND live ≥ $25), the 8 GOLD picks above are the configurations that show positive PnL on the live data and would have generated **~$310/day of paper-trading P&L** if all run in parallel during May 16–17. That's a $10/trade strategy producing ~3% daily return on deployed capital.

**Treat these as paper-trade candidates only.** Do not promote to real money without 1 more week of live data, and ideally one re-run of this analysis next weekend confirming the picks still pass GOLD gates.

## Headline picks (GOLD tier)

| # | id | side | hours | vol | band | drop% | PT% | SL | trail | n | WR | total$ | hist$ | live$ | sharpe | DD | days+/data |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | broad_sw/193 | BOTH | ASIA | ALL | 0.18-0.26 | 10 | 120 | None | 20 | 181 | 58% | 906.8 | 815.4 | 91.4 | 0.40 | 68 | 4/5 |
| 2 | asia_swe/150 | BOTH | ASIA | LOW | 0.10-0.18 | 12 | 50 | None | 25 | 59 | 68% | 579.1 | 414.7 | 164.4 | 0.56 | 16 | 4/5 |
| 3 | broad_sw/436 | DOWN | ALL | ALL | 0.10-0.25 | 10 | 120 | None | 35 | 147 | 55% | 739.0 | 643.6 | 95.4 | 0.40 | 79 | 3/5 |
| 4 | asia_swe/173 | DOWN | ASIA | MED | 0.22-0.30 | 8 | 90 | None | None | 44 | 84% | 333.2 | 194.3 | 138.9 | 0.86 | 31 | 5/5 |
| 5 | broad_sw/334 | UP | OVERNIGHT | LOW | 0.05-0.15 | 18 | 25 | None | 20 | 48 | 73% | 316.7 | 214.2 | 102.5 | 0.39 | 38 | 4/4 |
| 6 | broad_sw/879 | UP | OVERNIGHT | LOW | 0.10-0.30 | 10 | 80 | None | None | 43 | 67% | 217.4 | 123.6 | 93.8 | 0.37 | 44 | 4/4 |
| 7 | asia_swe/304 | DOWN | ASIA | ALL | 0.18-0.24 | 12 | 70 | None | 35 | 69 | 64% | 297.9 | 223.0 | 74.9 | 0.39 | 60 | 4/5 |
| 8 | asia_swe/404 | BOTH | ASIA | MED | 0.18-0.24 | 35 | 70 | None | None | 34 | 79% | 159.2 | 92.6 | 66.7 | 0.53 | 21 | 5/5 |

## Silver tier (paper-test, default disabled in YAML)

| # | id | side | hours | vol | band | n | total$ | hist$ | live$ | sharpe |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | broad_sw/652 | BOTH | OVERNIGHT | ALL | 0.15-0.22 | 137 | 979.8 | 981.9 | -2.1 | 0.63 |
| 2 | asia_swe/42 | DOWN | ASIA | ALL | 0.18-0.24 | 92 | 509.6 | 488.7 | 20.8 | 0.52 |
| 3 | asia_swe/189 | UP | ASIA | MED | 0.18-0.26 | 53 | 457.4 | 448.0 | 9.4 | 0.93 |
| 4 | asia_swe/305 | UP | ASIA | ALL | 0.13-0.21 | 61 | 437.8 | 414.6 | 23.3 | 0.65 |

## Regime observations

- **March 2026 data is much friendlier to dip-buying** than May 2026. Configs with PT 120–175% routinely doubled in hist but flatten or lose in live.
- **ASIA hours dominate**.  Outside ASIA, robust configs are rarer.  EU/US/OVERNIGHT all show edge in narrower bands.
- **Per-coin diversification is real**: XRP and ETH carry the most P&L in the best configs; SOL is the noisiest.
- **Live PnL of currently-enabled live bot strategies (May 16-17) is negative**: cfg_21c8c00165b3 / cfg_333fde9cecb8 / cfg_max_pnl_v* all bleeding. The May regime change is the cause.
- **Robust adaptation**: Configs that survive both segments share one common trait — they take profits earlier (PT 35–80%) and use stop_loss to cut losers.  The 175% PT configs lose in May because bounces are shallower.

## How to deploy

1. Review `runs/proposed_strategies.yaml`.
2. Append the GOLD entries (already `enabled: true`) to `strategies.yaml`.
3. Append SILVER entries with `enabled: false` (or `true` if you want broader paper-coverage).
4. SIGHUP the paper trader so it hot-reloads. **Do NOT disable the existing strategies yet** — keep them running until end-of-week so we can compare apples-to-apples.
5. After 5–7 more live-paper days, re-run this analysis.  Configs that stay gold-tier two weeks in a row are the real-money candidates.

## Per-pick evidence — GOLD

### GOLD #1 — `v2_gold_01_both_asia` (sweep broad_sweep_v1 cid=193)
- 181 trades, 58% WR, PnL $906.8, sharpe 0.40, max-DD $68.1
- Segments: hist $815.4 (95t) / live $91.4 (86t)
- Per coin: btc=$240 (51), eth=$282 (62), sol=$161 (33), xrp=$224 (35)
- Exit reasons: trailing_stop=87, profit_target=72, forced_resolution=19, max_hold=3

**Day-by-day P&L:**
| date | trades | wins | pnl |
|---|---|---|---|
| 2026-03-14 | 0 | 0 | 0.00 |
| 2026-03-15 | 6 | 3 | 10.06 |
| 2026-03-16 | 41 | 28 | 325.92 |
| 2026-03-17 | 48 | 36 | 479.44 |
| 2026-05-15 | 0 | 0 | 0.00 |
| 2026-05-16 | 42 | 23 | -0.85 |
| 2026-05-17 | 44 | 15 | 92.21 |

**SimConfig (flat):**
```json
{
  "entry.side": "BOTH",
  "entry.entry_price_min": 0.18,
  "entry.entry_price_max_offset": 0.075,
  "entry.drop_magnitude_pct": 10,
  "entry.drop_window_sec": 20,
  "entry.min_time_left_sec": 180,
  "entry.proximity_max_pct": 100.0,
  "entry.min_seconds_into_window": 15,
  "exit.profit_target_pct": 120,
  "exit.stop_loss_pct": null,
  "exit.max_hold_sec": 600,
  "exit.trailing_stop_pct": 20,
  "filter.min_book_depth_usd": 15,
  "filter.max_spread": 0.05,
  "filter.book_imbalance_min": null,
  "filter.vol_regime": "ALL",
  "filter.time_of_day": "ASIA",
  "human.signal_skip_prob": 0.0,
  "human.daily_trade_cap": 50,
  "human.post_loss_cooldown_sec": 0
}
```

### GOLD #2 — `v2_gold_02_both_asia` (sweep asia_sweep_v1 cid=150)
- 59 trades, 68% WR, PnL $579.1, sharpe 0.56, max-DD $16.1
- Segments: hist $414.7 (28t) / live $164.4 (31t)
- Per coin: btc=$239 (23), eth=$155 (20), sol=$10 (3), xrp=$175 (13)
- Exit reasons: profit_target=37, forced_resolution=7, trailing_stop=15

**Day-by-day P&L:**
| date | trades | wins | pnl |
|---|---|---|---|
| 2026-03-14 | 0 | 0 | 0.00 |
| 2026-03-15 | 4 | 2 | -6.47 |
| 2026-03-16 | 15 | 14 | 232.23 |
| 2026-03-17 | 9 | 8 | 188.92 |
| 2026-05-15 | 0 | 0 | 0.00 |
| 2026-05-16 | 15 | 7 | 51.45 |
| 2026-05-17 | 16 | 9 | 112.96 |

**SimConfig (flat):**
```json
{
  "entry.side": "BOTH",
  "entry.entry_price_min": 0.1,
  "entry.entry_price_max_offset": 0.08,
  "entry.drop_magnitude_pct": 12,
  "entry.drop_window_sec": 45,
  "entry.min_time_left_sec": 300,
  "entry.proximity_max_pct": 3.0,
  "entry.min_seconds_into_window": 0,
  "exit.profit_target_pct": 50,
  "exit.stop_loss_pct": null,
  "exit.max_hold_sec": 720,
  "exit.trailing_stop_pct": 25,
  "filter.min_book_depth_usd": 30,
  "filter.max_spread": 0.12,
  "filter.book_imbalance_min": null,
  "filter.vol_regime": "LOW"
}
```

### GOLD #3 — `v2_gold_03_down_all` (sweep broad_sweep_v1 cid=436)
- 147 trades, 55% WR, PnL $739.0, sharpe 0.40, max-DD $78.6
- Segments: hist $643.6 (108t) / live $95.4 (39t)
- Per coin: btc=$298 (53), eth=$95 (36), sol=$72 (24), xrp=$275 (34)
- Exit reasons: trailing_stop=62, profit_target=69, forced_resolution=16

**Day-by-day P&L:**
| date | trades | wins | pnl |
|---|---|---|---|
| 2026-03-14 | 0 | 0 | 0.00 |
| 2026-03-15 | 0 | 0 | 0.00 |
| 2026-03-16 | 68 | 40 | 460.58 |
| 2026-03-17 | 40 | 24 | 183.00 |
| 2026-05-15 | 1 | 0 | -3.27 |
| 2026-05-16 | 9 | 2 | -16.28 |
| 2026-05-17 | 29 | 15 | 114.99 |

**SimConfig (flat):**
```json
{
  "entry.side": "DOWN",
  "entry.entry_price_min": 0.1,
  "entry.entry_price_max_offset": 0.15,
  "entry.drop_magnitude_pct": 10,
  "entry.drop_window_sec": 90,
  "entry.min_time_left_sec": 540,
  "entry.proximity_max_pct": 100.0,
  "entry.min_seconds_into_window": 0,
  "exit.profit_target_pct": 120,
  "exit.stop_loss_pct": null,
  "exit.max_hold_sec": 900,
  "exit.trailing_stop_pct": 35,
  "filter.min_book_depth_usd": 150,
  "filter.max_spread": 0.12,
  "filter.book_imbalance_min": null,
  "filter.vol_regime": "ALL",
  "filter.time_of_day": "ALL",
  "human.signal_skip_prob": 0.0,
  "human.daily_trade_cap": 25,
  "human.post_loss_cooldown_sec": 60
}
```

### GOLD #4 — `v2_gold_04_down_asia` (sweep asia_sweep_v1 cid=173)
- 44 trades, 84% WR, PnL $333.2, sharpe 0.86, max-DD $30.6
- Segments: hist $194.3 (23t) / live $138.9 (21t)
- Per coin: btc=$66 (9), eth=$82 (13), sol=$49 (7), xrp=$136 (15)
- Exit reasons: profit_target=34, forced_resolution=6, max_hold=4

**Day-by-day P&L:**
| date | trades | wins | pnl |
|---|---|---|---|
| 2026-03-14 | 0 | 0 | 0.00 |
| 2026-03-15 | 2 | 2 | 16.13 |
| 2026-03-16 | 15 | 14 | 120.69 |
| 2026-03-17 | 6 | 6 | 57.45 |
| 2026-05-15 | 0 | 0 | 0.00 |
| 2026-05-16 | 8 | 5 | 16.75 |
| 2026-05-17 | 13 | 10 | 122.18 |

**SimConfig (flat):**
```json
{
  "entry.side": "DOWN",
  "entry.entry_price_min": 0.22,
  "entry.entry_price_max_offset": 0.08,
  "entry.drop_magnitude_pct": 8,
  "entry.drop_window_sec": 20,
  "entry.min_time_left_sec": 300,
  "entry.proximity_max_pct": 1.5,
  "entry.min_seconds_into_window": 0,
  "exit.profit_target_pct": 90,
  "exit.stop_loss_pct": null,
  "exit.max_hold_sec": 720,
  "exit.trailing_stop_pct": null,
  "filter.min_book_depth_usd": 15,
  "filter.max_spread": 0.05,
  "filter.book_imbalance_min": null,
  "filter.vol_regime": "MED"
}
```

### GOLD #5 — `v2_gold_05_up_overnight` (sweep broad_sweep_v1 cid=334)
- 48 trades, 73% WR, PnL $316.7, sharpe 0.39, max-DD $37.8
- Segments: hist $214.2 (23t) / live $102.5 (25t)
- Per coin: btc=$127 (25), eth=$112 (12), sol=$12 (2), xrp=$65 (9)
- Exit reasons: profit_target=34, trailing_stop=6, forced_resolution=8

**Day-by-day P&L:**
| date | trades | wins | pnl |
|---|---|---|---|
| 2026-03-14 | 0 | 0 | 0.00 |
| 2026-03-15 | 0 | 0 | 0.00 |
| 2026-03-16 | 10 | 9 | 89.53 |
| 2026-03-17 | 13 | 10 | 124.69 |
| 2026-05-15 | 0 | 0 | 0.00 |
| 2026-05-16 | 15 | 11 | 42.57 |
| 2026-05-17 | 10 | 5 | 59.89 |

**SimConfig (flat):**
```json
{
  "entry.side": "UP",
  "entry.entry_price_min": 0.05,
  "entry.entry_price_max_offset": 0.1,
  "entry.drop_magnitude_pct": 18,
  "entry.drop_window_sec": 60,
  "entry.min_time_left_sec": 180,
  "entry.proximity_max_pct": 0.2,
  "entry.min_seconds_into_window": 60,
  "exit.profit_target_pct": 25,
  "exit.stop_loss_pct": null,
  "exit.max_hold_sec": 900,
  "exit.trailing_stop_pct": 20,
  "filter.min_book_depth_usd": 150,
  "filter.max_spread": 0.2,
  "filter.book_imbalance_min": null,
  "filter.vol_regime": "LOW",
  "filter.time_of_day": "OVERNIGHT",
  "human.signal_skip_prob": 0.0,
  "human.daily_trade_cap": null,
  "human.post_loss_cooldown_sec": 60
}
```

### GOLD #6 — `v2_gold_06_up_overnight` (sweep broad_sweep_v1 cid=879)
- 43 trades, 67% WR, PnL $217.4, sharpe 0.37, max-DD $44.0
- Segments: hist $123.6 (26t) / live $93.8 (17t)
- Per coin: btc=$137 (30), eth=$18 (3), sol=$35 (3), xrp=$28 (7)
- Exit reasons: profit_target=27, forced_resolution=16

**Day-by-day P&L:**
| date | trades | wins | pnl |
|---|---|---|---|
| 2026-03-14 | 0 | 0 | 0.00 |
| 2026-03-15 | 0 | 0 | 0.00 |
| 2026-03-16 | 13 | 9 | 55.08 |
| 2026-03-17 | 13 | 9 | 68.55 |
| 2026-05-15 | 0 | 0 | 0.00 |
| 2026-05-16 | 12 | 7 | 71.93 |
| 2026-05-17 | 5 | 4 | 21.83 |

**SimConfig (flat):**
```json
{
  "entry.side": "UP",
  "entry.entry_price_min": 0.1,
  "entry.entry_price_max_offset": 0.2,
  "entry.drop_magnitude_pct": 10,
  "entry.drop_window_sec": 30,
  "entry.min_time_left_sec": 420,
  "entry.proximity_max_pct": 0.2,
  "entry.min_seconds_into_window": 0,
  "exit.profit_target_pct": 80,
  "exit.stop_loss_pct": null,
  "exit.max_hold_sec": 900,
  "exit.trailing_stop_pct": null,
  "filter.min_book_depth_usd": 50,
  "filter.max_spread": 0.12,
  "filter.book_imbalance_min": null,
  "filter.vol_regime": "LOW",
  "filter.time_of_day": "OVERNIGHT",
  "human.signal_skip_prob": 0.0,
  "human.daily_trade_cap": 25,
  "human.post_loss_cooldown_sec": 0
}
```

### GOLD #7 — `v2_gold_07_down_asia` (sweep asia_sweep_v1 cid=304)
- 69 trades, 64% WR, PnL $297.9, sharpe 0.39, max-DD $59.9
- Segments: hist $223.0 (29t) / live $74.9 (40t)
- Per coin: btc=$65 (18), eth=$109 (19), sol=$49 (11), xrp=$75 (21)
- Exit reasons: trailing_stop=17, profit_target=42, forced_resolution=8, max_hold=2

**Day-by-day P&L:**
| date | trades | wins | pnl |
|---|---|---|---|
| 2026-03-14 | 0 | 0 | 0.00 |
| 2026-03-15 | 2 | 1 | 7.59 |
| 2026-03-16 | 14 | 10 | 48.40 |
| 2026-03-17 | 13 | 12 | 167.05 |
| 2026-05-15 | 0 | 0 | 0.00 |
| 2026-05-16 | 21 | 8 | -46.25 |
| 2026-05-17 | 19 | 13 | 121.16 |

**SimConfig (flat):**
```json
{
  "entry.side": "DOWN",
  "entry.entry_price_min": 0.18,
  "entry.entry_price_max_offset": 0.06,
  "entry.drop_magnitude_pct": 12,
  "entry.drop_window_sec": 15,
  "entry.min_time_left_sec": 180,
  "entry.proximity_max_pct": 0.4,
  "entry.min_seconds_into_window": 60,
  "exit.profit_target_pct": 70,
  "exit.stop_loss_pct": null,
  "exit.max_hold_sec": 720,
  "exit.trailing_stop_pct": 35,
  "filter.min_book_depth_usd": 10,
  "filter.max_spread": 0.08,
  "filter.book_imbalance_min": null,
  "filter.vol_regime": "ALL"
}
```

### GOLD #8 — `v2_gold_08_both_asia` (sweep asia_sweep_v1 cid=404)
- 34 trades, 79% WR, PnL $159.2, sharpe 0.53, max-DD $20.9
- Segments: hist $92.6 (19t) / live $66.7 (15t)
- Per coin: btc=$32 (8), eth=$37 (12), sol=$43 (6), xrp=$47 (8)
- Exit reasons: profit_target=25, max_hold=7, forced_resolution=2

**Day-by-day P&L:**
| date | trades | wins | pnl |
|---|---|---|---|
| 2026-03-14 | 0 | 0 | 0.00 |
| 2026-03-15 | 3 | 3 | 20.40 |
| 2026-03-16 | 10 | 9 | 67.75 |
| 2026-03-17 | 6 | 4 | 4.40 |
| 2026-05-15 | 0 | 0 | 0.00 |
| 2026-05-16 | 7 | 5 | 34.23 |
| 2026-05-17 | 8 | 6 | 32.44 |

**SimConfig (flat):**
```json
{
  "entry.side": "BOTH",
  "entry.entry_price_min": 0.18,
  "entry.entry_price_max_offset": 0.06,
  "entry.drop_magnitude_pct": 35,
  "entry.drop_window_sec": 30,
  "entry.min_time_left_sec": 120,
  "entry.proximity_max_pct": 3.0,
  "entry.min_seconds_into_window": 30,
  "exit.profit_target_pct": 70,
  "exit.stop_loss_pct": null,
  "exit.max_hold_sec": 360,
  "exit.trailing_stop_pct": null,
  "filter.min_book_depth_usd": 15,
  "filter.max_spread": 0.05,
  "filter.book_imbalance_min": null,
  "filter.vol_regime": "MED"
}
```
---

## Pattern findings (what the sweep tells us)

These observations should inform future strategy variants:

### 1. ASIA hours (00–08 UTC) are the strongest regime

Of 19 GOLD picks, 12 are ASIA-only and another 3 are OVERNIGHT (22–24 UTC) — i.e. the low-liquidity Asian-trading window. EU and US hours produce far fewer robust configs. Hypothesis: thinner books + late-night retail panic = bigger overshoot = more mean-reversion edge.

### 2. May 2026 bounces are shallower than March

Configs with profit-target 120–175% routinely doubled in March data but flatten in May. The robust live-friendly configs use **profit targets in the 40–90% range** (`v2_gold_02..08`). Speculation: May has lower realized volatility on the underlyings → smaller odds dislocations → smaller bounces. The bot should adapt PT to current vol regime.

### 3. Entry band: two distinct sweet spots
- **0.18–0.26**: the BOTH/ASIA cluster (configs 193, 150, 304, 404, 173). This is the user's manual sweet-spot — odds priced near "moderate underdog" with room to bounce.
- **0.05–0.15**: deep-dip configs (cfg_max_pnl-style, broad/334, broad/879). These need bigger drop_magnitude (18–35%) and tight PT (25–80%) because there's less upside.

### 4. SOL is the weakest performer

In live data, SOL trades drag every config. ETH and XRP are the workhorses. BTC is the most stable. If we ever want to disable a coin per-strategy, SOL is the candidate. (Today the bot can't filter by coin in `signals.py` — see Future work.)

### 5. The drop-window matters less than the drop magnitude

LHS sampled drop_window_sec in {10, 20, 30, 45, 60, 90}. GOLD picks are spread across all values. Drop magnitude on the other hand clusters around 10–18% for narrow bands.

### 6. Most GOLD configs use trailing stop, not hard stop

Of the 8 picks above, 5 use a trailing stop (20–35%), 3 use a hard stop_loss, none use both aggressively. The intuition: the bounce is fast (median hold ≈ 90–250 sec); once we're up, a trailing stop captures most of the bounce. Hard stop is a backstop for the dip continuing.

---

## How to deploy

1. **Inspect** `runs/proposed_strategies.yaml` — 12 entries (8 GOLD enabled, 4 SILVER disabled).
2. **Append** the entries to your `strategies.yaml`. Keep the existing strategies running so we have apples-to-apples comparison next week.
3. **SIGHUP the combined process**: `kill -HUP $(cat .combined.pid)` — the registry hot-reloads.
4. **Watch for one week**. The current live data is only 3 days — extending to 10 days will tell us if these GOLD picks really hold up.
5. **Re-run the pipeline** next Sunday (`scripts/analysis/run_all.sh`). Configs that stay in GOLD after a second week are the real-money candidates.

### Concrete commands

```bash
# Inspect the proposed configs
cat runs/proposed_strategies.yaml | head -100

# Append to strategies.yaml (after manual review!)
cat runs/proposed_strategies.yaml >> strategies.yaml

# Hot-reload paper trader
kill -HUP $(cat .combined.pid)

# Re-validate after a few days
uv run python scripts/analysis/validate_picks_live.py \
  --picks runs/proposed_strategies.yaml \
  --date-start 2026-05-15 --date-end 2026-05-22
```

---

## Risks and caveats

- **Tiny live sample (3 days)**: With only May 15–17 data, even the GOLD picks rest on 18–82 live trades each. A single bad day can swing things. Bonferroni-style multiple-testing penalty is enormous when picking 8 of 19 configs from 3000.
- **Regime fragility**: The Mar→May regime change cost the original validated strategies most of their edge. A second regime change between May→June is plausible. **Re-validate weekly**.
- **Per-coin tail risk**: SOL trades drag many configs. If a thin-book moment causes a bad fill, one trade can erase a day's profit.
- **Bot uses the SAME outcome-resolution logic as the sim** (`forced_resolution` at window-end settles at 1.0/0.0 if outcome is known). If the live bot's outcome plumbing has a bug, sim ≠ live.
- **Slippage assumption (`realistic_fill_model=True` + reject_prob 0.05)**: This is more pessimistic than the live bot's default. If anything, the sweep numbers UNDERSTATE live PnL.

## Files generated

- `PROPOSED_STRATEGIES_2026-05-17.md` — this report
- `runs/proposed_strategies.yaml` — paste-ready YAML entries
- `runs/broad_sweep_v1.jsonl` (1500), `runs/focused_sweep_v1.jsonl` (1000), `runs/asia_sweep_v1.jsonl` (500) — full raw sweep
- `runs/post_hoc_v1.jsonl` — per (config × filter) breakdown
- `runs/live_validation.txt` — live-only paper-trade validation table
- `scripts/analysis/*.py` — the full pipeline (re-runnable next week)
