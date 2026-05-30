# Phase 3 — Maker spread-capture, real trade tape  ❌ NO-GO (confirmed, better data)

**Date:** 2026-05-29  ·  **Code:** `research/analysis/maker_real_fill.py`
**Data:** `joined_15m.parquet` (dev+holdout). The improvement over the prior
study: adverse selection is measured from the REAL executed-trade tape, not a
book-move proxy; and the liquidity-reward pool is checked against the live API.

## What was measured

When real taker SELL flow (≥$20/s) hits the bid, a maker BUY fills there; the
forward mid drift over +30 s IS the adverse selection. Symmetric for SELL fills.
Net round-trip = buy markout + sell markout + 2× rebate (+ liquidity rewards).

| scope | buy-fill mid drift | sell-fill drift | round-trip (＋rebate) |
|---|---|---|---|
| ALL | −1.75¢ | +1.29¢ | **−1.01¢** |
| **BTC** (best book) | −1.44¢ | +1.09¢ | **−0.62¢** |
| ETH | −2.50¢ | +1.41¢ | **−1.76¢** |

The real adverse selection (−1.44¢ buy-fill drift, BTC) is **milder than the prior
−2.25¢ proxy** — the proxy was pessimistic, as flagged. But the maker round-trip
is still **negative everywhere**, −0.62¢ (BTC) to −1.76¢ (ETH), before inventory.

## The two bridges, both closed

1. **Liquidity rewards = ZERO.** The CLOB rewards endpoint returns `count: 0` for
   these 15m markets and `holdingRewardsEnabled: false`. The `rewardsMaxSpread`/
   `rewardsMinSize` fields are inert defaults — there is no active reward pool to
   bridge the −0.62¢ BTC gap.
2. **Inventory into a 0/1 binary** (prior study's structural killer, unchanged):
   ~84% of windows have no losing-side exit liquidity at the close, so any
   unflattened inventory eats the settlement — biased toward the losing side.

## Verdict

**NO-GO, confirmed with better data.** Takers who hit a resting maker are
informed (the mid keeps drifting their way: −1.4 to −2.5¢ at +30 s), spread
capture (~0.6¢/side) + rebate (~0.35¢/leg) does not cover it, liquidity rewards
do not exist on these markets, and the inventory-into-binary risk is unhedgeable.
The maker door — the prior "NEEDS-BETTER-DATA" footnote — is now properly closed.
The live candidates remain the Phase 1 / Phase 2 **taker** edges.
