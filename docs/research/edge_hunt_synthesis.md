# Edge Hunt — Synthesis (Phases 0–4)

**Date:** 2026-05-29
**Scope:** The new-data edge hunt on Polymarket 15m crypto Up/Down markets, using
the L2 book / trade tape / fast spot / Chainlink feeds that came online 2026-05-22
— the doors the prior research said were untested. Clean window: 2026-05-23→29
(dev 23–27, sealed hold-out 28–29), null-tested harness (`harness_v2.md`).

## The one-line finding

**The Polymarket 15m book LAGS the public spot price.** The capturable edge is
*momentum/determinism* — betting WITH where spot already is — not mean-reversion.
Two independent taker strategies exploit this and confirm out-of-sample; maker and
dip-reversion do not work.

## Results

| Phase | Angle | Verdict | OOS $/trade (WR) |
|---|---|---|---|
| **1** | Late-window determinism pickoff (last 60s, spot ≥5bps from strike, buy favourite ≤0.90, hold) | ✅ **PRIMARY** | **+$1.68 (91%)**, CI [+0.97,+2.39] |
| **2** | Mid-window stale-quote pickoff (jump-gated, bet empirical-fair side) | ✅ secondary (higher-variance) | +$2.66–3.70 (~51%), CI excl. 0 |
| **3** | Maker spread-capture (real L2 + trade tape) | ❌ NO-GO | round-trip −0.6 to −1.8¢ |
| **4** | Noise-drop reversion (user's original thesis, spot-flat filter) | ❌ negative | straddles 0 |

Both winners share one mechanism (book lags spot); they fire in disjoint window
regions (P1 at the close, P2 mid-window after a jump), so they corroborate rather
than double-count. Maker fails because takers who hit it are informed (−1.4 to
−2.5¢ markout), there are no liquidity rewards (pool empty), and inventory into a
0/1 binary is unhedgeable. Dip-reversion fails because the dipped side is
calibrated — the market already prices it right.

## Why this is trustworthy (vs the prior disasters)

- Null-tested harness (no manufactured EV; calibration slope 0.91); March data
  quarantined; settlement feed corrected (Chainlink stream); book-health guard.
- Both winners: latency-robust (survive 5s), 96–100% fill, both-halves dev CV,
  4-symbol directional consistency, **CI-positive on a sealed hold-out**.
- The user's intuition was directionally backwards ("buy the dip") but the
  "near the strike, when the move is real" instinct maps onto the determinism edge.

## Honest caveats (gating live money)

- 7 clean days total — short. Forward paper on unseen future data is required.
- Fat left tail (P1: ~9% lose the full stake; P2: outlier-driven mean). Daily-loss
  cap + fixed $10 sizing mandatory.
- Capacity ~$10–50/trade (final-minute / post-jump depth).
- Multiple-testing across configs — Phase 5 applies a formal correction.

## Next — Phase 5 (harden the winners)

1. Full gauntlet on P1 (primary) + P2 (secondary): multiple-testing correction,
   heavier cost-stress (higher fee, +1 tick slippage, larger latency), per-regime,
   re-seal a larger hold-out as the bot collects more days.
2. Build engine support for the determinism strategy (new type: late-window
   favourite buy + hold-to-resolution — NOT the FLAT/ARMED/HOLDING mean-reversion
   machine), add to `strategies.yaml`, run FORWARD PAPER on unseen windows.
3. Compare live-paper vs backtest (drift < 30%) → then a small live test
   ($50–100, $10/trade, daily-loss cap).
4. Phase 6 (widen to hourly/daily) only if more capacity/edge is wanted — the 15m
   determinism edge is real but capacity-limited.
