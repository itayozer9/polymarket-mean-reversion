# Phase 4 — Noise-drop reversion (the user's original thesis)  ❌ NEGATIVE

**Date:** 2026-05-29  ·  **Code:** `research/analysis/trade_flow.py`
**Data:** `joined_15m.parquet`, dev + hold-out.

## What was tested (correctly, at last)

The user's manual thesis (H2): a sharp odds drop that is NOT justified by a real
spot move (noise) should revert — buy the dip. The spot-flat / proximity filter
this relies on was never actually tested before: a unit bug
(`phase0_audit.md` Task 5) made the proximity filter permanently inert in every
prior backtest and live run. With the corrected features + the spot feed we can
finally separate noise drops (spot flat) from signal drops (spot moved) and test it.

## Result

**The dipped side is calibrated regardless of spot move — no reversion edge.**

| drop | spot | dip ask | realized WR | gross edge |
|---|---|---|---|---|
| ~25% | FLAT (noise) | 0.345 | 0.338 | −0.007 |
| ~25% | MOVED (signal) | 0.312 | 0.307 | −0.006 |
| ~50% | FLAT | 0.230 | 0.234 | +0.005 |
| ~100% | FLAT | 0.119 | 0.138 | +0.019 (tiny, longshot tail) |

FLAT (noise) drops do **not** revert more than MOVED (signal) drops — H2 is not
supported. Backtest (buy the noise-drop dip, hold to resolution): every config on
dev AND hold-out straddles zero (e.g. dev drop≥15/flat≤3: +$0.16 [−0.47, +0.85];
hold-out: +$0.20 [−1.06, +1.47]). No CI-positive configuration.

## Why — and how it fits the winning edges

The market already prices the dip correctly; buying it back is a coin-flip at the
quoted odds, and the entry cost makes it a net loss. This confirms the prior
"momentum, not bounce" finding — now with the spot-flat filter the proximity bug
had blocked. **The user's "buy the dip" intuition is backwards for these markets.**

The edge is the *opposite*: Phases 1–2 win by betting WITH the spot-implied
direction (the book LAGS spot — determinism/momentum), not against it. The user's
correct instinct was "trade near the strike when the move is real"; that maps onto
the determinism pickoff (Phase 1), not onto dip-reversion. Phase 4 closes the
original mean-reversion thesis cleanly: it does not work on these markets.
