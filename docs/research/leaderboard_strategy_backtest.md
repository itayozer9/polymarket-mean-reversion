# Leaderboard directional-holder strategy — backtest (rule M15-DH-1)

**Date:** 2026-05-22  
**Code:** `research/analysis/leaderboard_strategy_backtest.py`  
**Tests:** `tests/research/test_leaderboard_strategy_backtest.py`

---

## What this tests

The wallet analysis (`docs/research/leaderboard_wallets.md`) found that the dominant winning pattern on Polymarket's crypto profit leaderboard is **not** market-making — it is **directional buy-and-hold**: 70% of leaderboard winners buy one side of a short-dated crypto Up/Down market and hold it to resolution. The derived rule:

> **M15-DH-1.** On a crypto Up/Down market, within the early-window entry zone, BUY the FAVOURITE side (mid > 0.50) when its odds are in an entry band; HOLD to resolution; settle at the true 0/1 outcome. Flat sizing, one entry per window.

The leaderboard is **survivors only** — the top-100 winners of each board. It shows what winners *do*; it cannot measure expectancy because it never sees the losers. Our tick data sees **all** markets — winners and losers — so it measures the rule's true expectancy. This is the decisive test.

**Prior research** (`PHASE2_RERUN_VERDICT.md`): on corrected labels the 15m cheap side is well-calibrated (de-biased gap ~-1c). The favourite side is the *complement* of the cheap side — so a calibrated cheap side implies a calibrated favourite side, i.e. buy-favourite-hold should be roughly gross-breakeven on 15m and lose to cost. 5m is genuinely untested (prior research is 15m only).

## Method

- **Both timeframes, separately.** 15m window = 900s, early-entry zone = first 300s. 5m window = 300s, early-entry zone = first 100s (proportional — the leaderboard rule enters in the first third).
- **Entry tick:** the first tick in the early-entry zone where the favourite's mid is inside the band. One entry per window; the window is the independent unit.
- **Taker:** buy the favourite at its `ask`, pay fee `0.07*p*(1-p)` per share on entry, hold to resolution, settle at the true 0/1 outcome (no exit fee — a fee at p in {0,1} is 0).
- **Maker:** post a resting BUY limit at the favourite's `bid`; it fills ONLY when a strictly-later tick shows that side's bid trade down to/through the limit (the realistic fill model from `maker_reframe.py`); unfilled = no trade; 0 fee; settle at the true outcome. Maker stats are over filled trades only.
- **Null baseline:** at the *same* entry tick the favourite rule selects, buy a side chosen uniformly at random. Same windows, same ticks, same costs — isolates the value of the *favourite* choice from the value of the *timing*.
- **Book-health guard:** both sides priced in (0.001,0.999), positive bids below asks, complement-consistent within 6c — drops decided-market books.
- **CIs:** 90% window-clustered bootstraps (groups = `slug`).

### Data scope and the 5m label caveat

Scope is **~8 days, May 15-22 2026, 4 symbols** (BTC/ETH/SOL/XRP) — limited, but it is what we have. DEV = May 15-20; sealed HOLD-OUT = May 21-22.

**15m labels** are authoritative: the `ticks_15m.parquet` baked-in `outcome_up` matches `corrected_labels.parquet` (the Polymarket API ground truth) for 100% of windows — verified.

**5m labels** need care: the `ticks_5m.parquet` baked-in `outcome_up` is **corrupt** — it disagrees with the corrected 5m outcomes on ~31% of windows (verified: 1,564 of 5,018). This backtest uses `corrected_labels_5m.parquet` (the gamma-API ground truth) instead. That cache was fetched for the DEV split only, so the 5m DEV backtest is on corrected labels; the 5m hold-out needs an on-demand fetch (`--fetch-holdout-5m`), done only if a DEV result warrants it.

---

## 15m markets

DEV: 1,676 windows over 6 UTC days.

### 15m — calibration / expectancy sweep (DEV)

Each row: the first qualifying entry tick of every window whose favourite mid lands in the bucket. `realized win rate` vs `mean entry odds` is the calibration; a well-calibrated favourite has realized win rate ~ entry odds.

| Fav entry-odds bucket | n windows | mean entry odds (ask) | realized win rate | win-rate 90% CI | net PnL/trade taker | taker CI | net PnL/trade maker | maker fill rate |
|---|---|---|---|---|---|---|---|---|
| [0.50,0.55) | 1,627 | 0.529 | 0.511 | [+0.490, +0.531] | $-0.6584 | [$-1.056, $-0.270] | $+0.0036 | 1.00 |
| [0.55,0.60) | 1,509 | 0.582 | 0.566 | [+0.546, +0.586] | $-0.5799 | [$-0.919, $-0.226] | $+0.0555 | 0.98 |
| [0.60,0.65) | 1,438 | 0.632 | 0.630 | [+0.610, +0.652] | $-0.2880 | [$-0.612, $+0.049] | $+0.1691 | 0.97 |
| [0.65,0.70) | 1,283 | 0.682 | 0.697 | [+0.676, +0.718] | $-0.0029 | [$-0.311, $+0.301] | $+0.4362 | 0.97 |
| [0.70,0.75) | 1,078 | 0.730 | 0.737 | [+0.714, +0.758] | $-0.0853 | [$-0.384, $+0.206] | $+0.2883 | 0.96 |
| [0.75,0.80) | 821 | 0.778 | 0.797 | [+0.773, +0.820] | $+0.0973 | [$-0.206, $+0.390] | $+0.4182 | 0.97 |
| [0.80,0.85) | 564 | 0.827 | 0.844 | [+0.819, +0.869] | $+0.0839 | [$-0.226, $+0.384] | $+0.3835 | 0.97 |
| [0.85,0.90) | 345 | 0.877 | 0.875 | [+0.843, +0.904] | $-0.0976 | [$-0.450, $+0.234] | $+0.1986 | 0.98 |
| [0.90,0.95) | 154 | 0.922 | 0.916 | [+0.877, +0.955] | $-0.1253 | [$-0.553, $+0.268] | $+0.0820 | 0.96 |
| [0.95,1.00) | 32 | 0.967 | 1.000 | [+1.000, +1.000] | $+0.3201 | [$+0.295, $+0.342] | $+0.5513 | 1.00 |

Calibration read: mean (realized win rate - entry mid) over well-populated buckets = **+1.54c**. The favourite side is essentially calibrated — realized win rate tracks entry odds.

### 15m — M15-DH-1 primary band [0.60, 0.90] (DEV)

| Scope | n windows | win rate | net PnL/trade taker | taker 90% CI | net PnL/trade maker | maker 90% CI | maker fill rate |
|---|---|---|---|---|---|---|---|
| **pooled (favourite)** | 1,613 | 0.650 | $-0.2575 | [$-0.566, $+0.045] | $+0.2322 | [$-0.092, $+0.545] | 0.97 |
| &nbsp;&nbsp;btc | 407 | 0.656 | $-0.0524 | [$-0.663, $+0.563] | $+0.1595 | [$-0.489, $+0.781] | 0.95 |
| &nbsp;&nbsp;eth | 411 | 0.625 | $-0.5512 | [$-1.161, $+0.061] | $-0.2049 | [$-0.874, $+0.412] | 0.97 |
| &nbsp;&nbsp;sol | 394 | 0.655 | $-0.2992 | [$-0.884, $+0.282] | $+0.3463 | [$-0.326, $+1.018] | 0.97 |
| &nbsp;&nbsp;xrp | 401 | 0.663 | $-0.1236 | [$-0.694, $+0.497] | $+0.6334 | [$-0.020, $+1.276] | 0.99 |
| **pooled (random-side null)** | 1,613 | 0.510 | $-0.6195 | [$-1.028, $-0.190] | $+0.1024 | [$-0.383, $+0.590] | 0.97 |

### 15m — sensitivity band [0.50, 0.95] (DEV)

| Scope | n windows | win rate | net PnL/trade taker | taker 90% CI | net PnL/trade maker | maker 90% CI | maker fill rate |
|---|---|---|---|---|---|---|---|
| **pooled (favourite)** | 1,672 | 0.514 | $-0.7242 | [$-1.107, $-0.340] | $-0.0551 | [$-0.458, $+0.338] | 1.00 |
| &nbsp;&nbsp;btc | 418 | 0.517 | $-0.6907 | [$-1.442, $+0.032] | $-0.1555 | [$-0.916, $+0.598] | 1.00 |
| &nbsp;&nbsp;eth | 418 | 0.510 | $-0.9003 | [$-1.649, $-0.181] | $-0.2687 | [$-1.040, $+0.474] | 1.00 |
| &nbsp;&nbsp;sol | 418 | 0.529 | $-0.4084 | [$-1.168, $+0.361] | $+0.3752 | [$-0.382, $+1.159] | 1.00 |
| &nbsp;&nbsp;xrp | 418 | 0.500 | $-0.8974 | [$-1.660, $-0.151] | $-0.1707 | [$-0.963, $+0.592] | 1.00 |
| **pooled (random-side null)** | 1,672 | 0.495 | $-0.6470 | [$-1.046, $-0.261] | $+0.0591 | [$-0.341, $+0.457] | 1.00 |

### 15m — sealed hold-out (May 21-22)

**Not opened.** No DEV configuration (primary or sensitivity band, taker or maker) produced a net PnL/trade whose 90% window-clustered CI excludes zero on the positive side. A rule that already fails on DEV does not warrant consuming the hold-out — it stays sealed. (This mirrors the discipline of `PHASE2_RERUN_VERDICT.md`.)

## 5m markets

DEV: 5,018 windows over 6 UTC days.

### 5m — calibration / expectancy sweep (DEV)

Each row: the first qualifying entry tick of every window whose favourite mid lands in the bucket. `realized win rate` vs `mean entry odds` is the calibration; a well-calibrated favourite has realized win rate ~ entry odds.

| Fav entry-odds bucket | n windows | mean entry odds (ask) | realized win rate | win-rate 90% CI | net PnL/trade taker | taker CI | net PnL/trade maker | maker fill rate |
|---|---|---|---|---|---|---|---|---|
| [0.50,0.55) | 4,752 | 0.527 | 0.517 | [+0.505, +0.529] | $-0.5311 | [$-0.752, $-0.296] | $+0.1354 | 0.99 |
| [0.55,0.60) | 3,625 | 0.590 | 0.562 | [+0.549, +0.576] | $-0.7415 | [$-0.965, $-0.510] | $-0.2129 | 0.95 |
| [0.60,0.65) | 3,385 | 0.640 | 0.607 | [+0.594, +0.621] | $-0.7500 | [$-0.963, $-0.537] | $-0.3026 | 0.95 |
| [0.65,0.70) | 3,012 | 0.687 | 0.669 | [+0.655, +0.683] | $-0.4768 | [$-0.681, $-0.275] | $-0.0871 | 0.94 |
| [0.70,0.75) | 2,383 | 0.735 | 0.719 | [+0.704, +0.734] | $-0.3915 | [$-0.604, $-0.192] | $-0.0802 | 0.94 |
| [0.75,0.80) | 1,770 | 0.784 | 0.769 | [+0.753, +0.786] | $-0.3387 | [$-0.553, $-0.125] | $-0.0266 | 0.94 |
| [0.80,0.85) | 1,234 | 0.832 | 0.823 | [+0.806, +0.841] | $-0.2190 | [$-0.430, $-0.001] | $+0.0652 | 0.93 |
| [0.85,0.90) | 780 | 0.880 | 0.869 | [+0.850, +0.888] | $-0.2119 | [$-0.439, $+0.005] | $+0.0804 | 0.94 |
| [0.90,0.95) | 388 | 0.925 | 0.943 | [+0.923, +0.961] | $+0.1402 | [$-0.079, $+0.342] | $+0.3747 | 0.95 |
| [0.95,1.00) | 131 | 0.968 | 0.977 | [+0.954, +0.992] | $+0.0734 | [$-0.164, $+0.243] | $+0.2470 | 0.93 |

Calibration read: mean (realized win rate - entry mid) over well-populated buckets = **+0.23c**. The favourite side is essentially calibrated — realized win rate tracks entry odds.

### 5m — M15-DH-1 primary band [0.60, 0.90] (DEV)

| Scope | n windows | win rate | net PnL/trade taker | taker 90% CI | net PnL/trade maker | maker 90% CI | maker fill rate |
|---|---|---|---|---|---|---|---|
| **pooled (favourite)** | 4,630 | 0.653 | $-0.5500 | [$-0.719, $-0.374] | $-0.1303 | [$-0.329, $+0.055] | 0.95 |
| &nbsp;&nbsp;btc | 1,182 | 0.639 | $-0.6371 | [$-0.974, $-0.278] | $-0.4046 | [$-0.775, $-0.029] | 0.95 |
| &nbsp;&nbsp;eth | 1,163 | 0.656 | $-0.4205 | [$-0.783, $-0.080] | $-0.1552 | [$-0.532, $+0.211] | 0.95 |
| &nbsp;&nbsp;sol | 1,147 | 0.665 | $-0.3966 | [$-0.748, $-0.056] | $+0.0642 | [$-0.314, $+0.443] | 0.94 |
| &nbsp;&nbsp;xrp | 1,138 | 0.654 | $-0.7465 | [$-1.078, $-0.413] | $-0.0145 | [$-0.388, $+0.369] | 0.95 |
| **pooled (random-side null)** | 4,630 | 0.499 | $-0.7199 | [$-0.982, $-0.462] | $-0.0009 | [$-0.287, $+0.293] | 0.96 |

### 5m — sensitivity band [0.50, 0.95] (DEV)

| Scope | n windows | win rate | net PnL/trade taker | taker 90% CI | net PnL/trade maker | maker 90% CI | maker fill rate |
|---|---|---|---|---|---|---|---|
| **pooled (favourite)** | 5,011 | 0.526 | $-0.5484 | [$-0.758, $-0.327] | $+0.1316 | [$-0.096, $+0.356] | 1.00 |
| &nbsp;&nbsp;btc | 1,253 | 0.541 | $-0.4527 | [$-0.866, $-0.036] | $+0.0602 | [$-0.384, $+0.523] | 0.99 |
| &nbsp;&nbsp;eth | 1,253 | 0.513 | $-0.7266 | [$-1.166, $-0.299] | $-0.1096 | [$-0.563, $+0.310] | 1.00 |
| &nbsp;&nbsp;sol | 1,253 | 0.531 | $-0.2811 | [$-0.716, $+0.154] | $+0.3807 | [$-0.074, $+0.830] | 1.00 |
| &nbsp;&nbsp;xrp | 1,252 | 0.518 | $-0.7333 | [$-1.174, $-0.298] | $+0.1945 | [$-0.271, $+0.661] | 1.00 |
| **pooled (random-side null)** | 5,011 | 0.500 | $-0.5548 | [$-0.782, $-0.328] | $+0.1550 | [$-0.087, $+0.392] | 1.00 |

### 5m — sealed hold-out (May 21-22)

**Not opened.** No DEV configuration (primary or sensitivity band, taker or maker) produced a net PnL/trade whose 90% window-clustered CI excludes zero on the positive side. A rule that already fails on DEV does not warrant consuming the hold-out — it stays sealed. (This mirrors the discipline of `PHASE2_RERUN_VERDICT.md`.)

---

## Verdict

**15m:**

- **15m [0.60,0.90]:** taker $-0.2575/trade (CI straddles 0, CI [$-0.566, $+0.045]); maker $+0.2322/trade (CI straddles 0, CI [$-0.092, $+0.545]); favourite minus random-side null $+0.3620/trade.

- **15m [0.50,0.95]:** taker $-0.7242/trade (CI-negative, CI [$-1.107, $-0.340]); maker $-0.0551/trade (CI straddles 0, CI [$-0.458, $+0.338]); favourite minus random-side null $-0.0772/trade.


**5m:**

- **5m [0.60,0.90]:** taker $-0.5500/trade (CI-negative, CI [$-0.719, $-0.374]); maker $-0.1303/trade (CI straddles 0, CI [$-0.329, $+0.055]); favourite minus random-side null $+0.1699/trade.

- **5m [0.50,0.95]:** taker $-0.5484/trade (CI-negative, CI [$-0.758, $-0.327]); maker $+0.1316/trade (CI straddles 0, CI [$-0.096, $+0.356]); favourite minus random-side null $+0.0064/trade.


### Does buy-favourite-hold-to-resolution have a real, cost-surviving, out-of-fold edge?

- **15m: NO.** Primary band [0.60,0.90] taker $-0.2575/trade (CI straddles/below 0), maker $+0.2322/trade (CI straddles/below 0). No DEV config cleared the CI-positive bar, so the hold-out stayed sealed. Buy-favourite-hold does not have a cost-surviving edge here.
- **5m: NO.** Primary band [0.60,0.90] taker $-0.5500/trade (CI entirely negative), maker $-0.1303/trade (CI straddles/below 0). No DEV config cleared the CI-positive bar, so the hold-out stayed sealed. Buy-favourite-hold does not have a cost-surviving edge here.

### Calibration — the favourite side is well-calibrated

The expectancy sweep is the most informative panel. On both timeframes the favourite's realized win rate tracks its entry odds almost exactly: the mean (realized win rate − entry mid) over well-populated buckets is **+1.5c on 15m** and **+0.2c on 5m** — within noise of perfect calibration. Bucket by bucket, a favourite priced 0.63 wins ~63%, one priced 0.78 wins ~78%, one priced 0.88 wins ~88%. There is no entry-odds band where the favourite is systematically under-priced. Gross, the rule is a coin-flip-at-the-quoted-odds; net, it loses the round-trip cost.

### Connection to prior research

The 15m result **confirms** the prior calibration finding (`PHASE2_RERUN_VERDICT.md`): the favourite side is the arithmetic complement of the well-calibrated cheap side, so buying the favourite and holding to resolution is gross-breakeven and loses approximately the entry cost. The leaderboard's directional-holder pattern is therefore **survivorship variance**, not a positive-expectancy edge — exactly the central caveat the wallet analysis flagged (`leaderboard_wallets.md`, "Survivorship bias — the central limitation"). The 239 leaderboard wallets are the visible top of a much larger population that ran the same buy-favourite-hold pattern; the losers simply do not appear on a top-100 board.

**5m vs 15m — new ground.** 5m was genuinely untested before this task (prior research is 15m-only). The 5m result is **worse, not better**: the primary band taker is −$0.55/trade on 5m vs −$0.26/trade on 15m, and the 5m taker CI is entirely negative whereas the 15m one straddles zero. The 5m favourite is just as well-calibrated as 15m (+0.2c), so the extra loss is pure cost: 5m fires more entries per real-time hour and its books are slightly wider relative to the shorter window. The leaderboard's heavy 5m activity is not evidence of a 5m edge — it is the same survivorship pattern on a faster, more cost-intensive clock.

### A caveat on the maker numbers

The maker net PnL/trade is consistently less negative than the taker (15m primary maker +$0.23 vs taker −$0.26), and a naive reading might call the 15m maker "near breakeven". Treat that with care for two reasons. First, every maker CI straddles zero — none is CI-positive, so none clears the bar to be called an edge. Second, the maker **fill rate is 94-100%**: a resting buy posted at the favourite's *current bid* is traded through almost always, because a 15m/5m favourite's bid drifts around enough that the level is reached within the window. That is precisely the adverse-selection problem `maker_reframe.py` documented — the maker is filled not as a favour but because the side is moving, and on a calibrated market the fill carries no informational advantage. The maker "improvement" is the saved fee and spread, not a real edge; on a genuinely calibrated favourite a 0-fee bet on quoted odds is still a 0-EV bet.

### Bottom line

**Buy-favourite-hold-to-resolution has no real, cost-surviving, out-of-fold edge on either 5m or 15m.** Taker is clearly negative on both (CI-negative on 5m, CI-negative-to-straddling on 15m); maker is less negative but never CI-positive and is adverse-selected by construction. No DEV configuration cleared the CI-positive bar, so the sealed hold-out (May 21-22) was **not opened** on either timeframe — a rule that fails on DEV does not warrant consuming the hold-out. The favourite rule does beat the random-side null on the primary band (15m +$0.36/trade, 5m +$0.17/trade better than chance) — picking the favourite is better than picking blindly — but "better than a coin flip that also pays the spread" is not the same as "profitable": both the favourite rule and the null lose money. This is a clean negative, consistent with three prior independent confirmations (Phase 2 calibration, Phase 4 forensics, Lead D maker reframe) that the short-dated Polymarket Up/Down market is calibrated and carries no harvestable directional edge.

**Data-scope honesty.** ~8 days, 4 symbols. The DEV split is 6 days; the hold-out is 2. This is a small sample — a clean CI-negative result is a reliable *rejection* of a sizeable edge, but a small true edge could hide inside the CIs. The verdict is stated at the precision the data supports.
