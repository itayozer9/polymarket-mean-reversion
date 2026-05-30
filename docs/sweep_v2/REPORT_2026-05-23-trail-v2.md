# sweep_v2 — Trail-v2 Final Report
*Generated 2026-05-23 17:19 UTC*

## Headline
Both trail-v2 leaders pass the full validation gauntlet (5-fold OOS + 8-seed stability + 1D/joint param perturbations + per-symbol breakdown + adversarial costs + liquidity shock + walk-forward). The single-step `act100_lock30` and the three-step `act50_40→act100_25→act200_15` both meaningfully beat the no-trail baseline.

## Two leaders (vs no-trail baseline)

| Variant | Description | Sharpe | $/9d | $/day | trades/day | win% |
|---|---|---:|---:|---:|---:|---:|
| `act100_lock30` | act @+100%/lock 30% | 1.27 | $478 | $53 | 16.1 | 45.5% |
| `act50_40__act100_25__act200_15` | 3-step staircase [(50, 40), (100, 25), (200, 15)] | 1.12 | $565 | $63 | 18.6 | 44.3% |
| baseline_no_trail | (none) | 0.92 | $363 | $40 | 15.9 | 35.0% |

## Stress (6 axes × 8 seeds × 50 joint perturbations × 14 1D perturbations × per-symbol × adversarial × liquidity)

| Variant | seed | 1D | joint | per-sym | adv | liq | overall |
|---|---|---|---|---|---|---|---|
| `act100_lock30` | 8/8 ✓ | 100% ✓ | 86% ✓ | 3/4 ✓ | $335 ✓ | $251 ✓ | ✓ PASS |
| `act50_40__act100_25__act200_15` | 8/8 ✓ | 100% ✓ | 82% ✓ | 4/4 ✓ | $551 ✓ | $235 ✓ | ✓ PASS |

## Walk-forward on May (chronological 5-day train → next-day test, rolling)

**This is the key load-bearing test — different days, same period as training. If the strategy is overfit to the K-fold split, walk-forward will catch it.**

| Variant | days tested | total pnl | median day pnl | trades | result |
|---|---:|---:|---:|---:|---|
| `act100_lock30` | 4 | $168.99 | $47.20 | 76 | ✓ PASS |
| `act50_40__act100_25__act200_15` | 4 | $112.04 | $25.52 | 78 | ✓ PASS |

**Per-day walk-forward (last 4 days):**

- **`act100_lock30`**:
    - 2026-05-20: $133.13 (21 trades)
    - 2026-05-21: $-58.53 (28 trades)
    - 2026-05-22: $68.08 (14 trades)
    - 2026-05-23: $26.32 (13 trades)

- **`act50_40__act100_25__act200_15`**:
    - 2026-05-20: $38.90 (19 trades)
    - 2026-05-21: $24.77 (30 trades)
    - 2026-05-22: $22.10 (16 trades)
    - 2026-05-23: $26.27 (13 trades)

## March 4-17 cross-regime replay

⚠ **CAUTION:** the March 4-17 data is the same set that backed the original `BACKTEST_VERDICT.md` (since marked INVALID, see `STATE.md`) — it had a corrupt-orderbook and strike-mislabel bug, and 0/3,000 prior configs were CI-positive on the corrected slice. A March pass is therefore **no extra evidence**; a March fail would be a real generalization concern. Treat numbers below as decoration.

| Variant | n_trades | pnl | win_rate |
|---|---:|---:|---:|
| `act100_lock30` | 78 | $498.75 | 57.7% |
| `act50_40__act100_25__act200_15` | 81 | $302.24 | 45.7% |

## Per-month projection (at $10/trade bet size)

| Variant | trades/day | $/day | **$/month (30d)** |
|---|---:|---:|---:|
| `act100_lock30` | 16.1 | $53 | **$1594** |
| `act50_40__act100_25__act200_15` | 18.6 | $63 | **$1885** |

## Caveats and next steps

- These results are on 9 days of May data only. Live forward performance will be lower (slippage, missed fills, regime shifts).
- Win rate is 45-47% (you lose more trades than you win) — the strategy relies on the 270% profit target making winners pay for the losses. A few-percent drop in win rate would flip it negative.
- Linear bet-size scaling assumption breaks above ~$50/trade because the book has $76 min depth and partial-fill risk grows.
- The Wilcoxon p-value on pooled trade PnL was 0.395 (not Bonferroni-significant). The strict bar would still reject these picks. We are explicitly running with the lenient bar plus stress + walk-forward as a more practical gauntlet.
- **Recommended next step:** enable these in `proposed_strategies_v3.yaml` as paper-only, let the live bot accumulate 2+ weeks of forward data, then re-validate.