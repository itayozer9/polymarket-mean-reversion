# Phase 2 — Spot→book stale-quote pickoff (mid-window)  ✅ POSITIVE (jump-gated), higher-variance

**Date:** 2026-05-29  ·  **Code:** `research/analysis/stale_quote_pickoff.py`
**Data:** `joined_15m.parquet`. Empirical fair-value curve FIT on early-dev
(05-23..24), APPLIED to dev-late (05-25..27) + hold-out (05-28..29). Mid-window
only (60–840 s left) — disjoint from Phase 1's last-60 s.

## Hypothesis & method

Away from the close, does the book misprice vs an EMPIRICAL fair value implied by
spot's standardized distance-to-strike `z = dist_strike_bps / (vol_bps·√t_left)`
— especially right after a fast spot jump the 1 Hz book hasn't caught up to?
Fair value `P(Up|z)` is a monotone empirical curve (NOT the broken Gaussian
σ-model), fit out-of-period. Rule: enter at the first mid-window tick where
`|P(Up|z) − yes_mid| ≥ margin` (and `|spot_vel_10s| ≥ jump_bps`), bet the model's
side as a taker, hold to resolution. One trade/window.

## Result: positive OOS, but only with the jump gate

| split | rule | n | WR | $/trade | 90% CI |
|---|---|---|---|---|---|
| dev-late | margin 0.08, jump 8 | 431 | 0.61 | +$5.61 | [+3.5, +8.0] |
| **hold-out** | margin 0.08, jump 8 | 232 | 0.50 | **+$2.66** | **[+0.05, +5.70]** |
| **hold-out** | margin 0.12, jump 8 | 208 | 0.51 | **+$3.70** | **[+0.79, +7.14]** |
| hold-out | margin 0.12, **no jump** | 522 | 0.50 | +$0.85 | [−0.61, +2.61] ✗ |

**The jump gate is essential** — without a recent spot move the OOS CI straddles
zero. This is mechanistically the point: the edge IS the book being stale right
after a jump. It is the same "book lags public spot" inefficiency as Phase 1,
showing up mid-window instead of at the close (the two windows are disjoint, so
this corroborates the mechanism rather than double-counting it).

## Robustness (all 7 clean days, margin 0.08 / jump 8)

- 887 trades, WR 0.566, mean +$4.12/trade, **median +$3.33**, **6/7 days positive**,
  **all 4 symbols positive** (+$2.4 to +$6.0).
- BUT high variance: win pays big, lose −$10; **top 3 trades = 23% of total PnL**
  (a $10→$333 deep-longshot that resolved Up). The mean is outlier-inflated; the
  median and day-win-rate are the robust signal.

## Honest read vs Phase 1

- **Lower trust than Phase 1.** WR ~0.50–0.57 (near coin-flip with favourable
  payoffs) vs Phase 1's 0.91. Fat right tail = exactly where overfitting hides;
  the 86%-positive-day + OOS confirmation mitigate but don't eliminate the risk.
- **Refinement for Phase 5:** cap the mispricing (e.g. only bet `margin ≤ |mis| ≤
  0.25`) to drop lottery-ticket bets where the model most likely errs (a market at
  0.03 vs model 0.50 is more often a genuinely-decided window than a stale book).
  Test whether the edge concentrates in moderate-disagreement zones.

## Verdict

A genuine, OOS-confirmed, jump-gated edge — **complementary to Phase 1** and
sharing its mechanism, but higher-variance and outlier-sensitive. Carry forward
as a secondary candidate; Phase 1 (high-WR, low-variance) remains the primary.
Both go through the Phase 5 gauntlet + forward paper.
