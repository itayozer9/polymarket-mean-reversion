# Phase 5 — Gauntlet verdict: the determinism edge is DEPLOYABLE

**Date:** 2026-05-29  ·  **Code:** `research/analysis/gauntlet.py`

## Locked deployable rule (PRIMARY)

> 15m window, **last 60 s**, **|spot − strike| ≥ 5 bps** (favourite agrees with
> spot), buy the favourite at taker ask **≤ 0.90**, $10, **hold to resolution**.
> One trade per window. (Not the sweep-max dist≥10 variant — see below.)

## Gauntlet results

| Test | Result | Verdict |
|---|---|---|
| Null-tested harness (Phase 0d) | calibration slope 0.91, no manufactured EV | ✅ |
| **OOS sealed hold-out** (05-28..29) | +$1.68/trade, 91% WR, CI [+0.97,+2.39] | ✅ |
| **Cost-stress, ALL combined** (fee 1.5× + 1¢ slip + 5s lat + 30% reject) | +$1.28/trade, CI [+0.82,+1.74] | ✅ |
| Per-regime (vol median split) | quiet +$1.68 [+1.13,+2.22]; volatile +$1.21 [+0.61,+1.78] | ✅ |
| **Multiple-testing, calibrated null** (PRIMARY, N=333) | +$1.45 vs null 95th +$0.34, **p<0.0001** | ✅ |
| Multiple-testing, best-of-20 sweep max (dist≥10, N≈79) | +$1.83 vs null 95th +$1.99, p=0.054 | ⚠ borderline |

## The honest nuance (why the primary rule, not the max)

The single highest-$/trade config (dist≥10, ~79 trades) is within best-of-20
luck (p=0.054) — its high per-trade number is partly sweep-cherry-picking on a
thin sample. The **primary rule (dist≥5, N=333)** is lower per-trade (+$1.45) but
beats its own calibrated null at **p<0.0001** — overwhelming effect size with real
power, and it confirmed out-of-sample. Deploy the robust rule; treat the dist≥10
"+$1.9" as an optimistic ceiling, not the expectation.

## Standing caveats (carry into forward paper / live)

- 7 clean days only → forward paper on unseen windows is the next gate.
- Fat left tail: ~9% lose the full $10. Fixed $10 sizing + daily-loss cap.
- Capacity ~$10–50/trade (final-minute depth).
- Conservative expectation: **~+$0.8–1.5/trade, ~$30–70/day** at $10 stake, NOT
  the sweep-max +$1.9.

**Verdict: PASS — proceed to forward-paper deployment** (build the new strategy
type, add to `strategies.yaml`, run on unseen windows; compare live-paper vs this
backtest; then a small live test).
