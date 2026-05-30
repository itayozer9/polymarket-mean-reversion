# Phase 0a — Settlement feed & live market parameters (CORRECTS prior research)

**Date:** 2026-05-29
**Method:** Authoritative — read the live Polymarket market metadata via the Gamma API
(`/markets?slug=<sym>-updown-15m-<window_start_ts>`) for all four symbols, current windows.
**Why it matters:** the settlement feed is the linchpin for the Phase 1 oracle/late-window
angle, and the exact fee/tick parameters gate every cost calculation.

---

## 1. Settlement feed: **Chainlink Data Streams** (NOT Coinbase)

Every market's `description` states it verbatim (all 4 symbols, `resolutionSource` field agrees):

> "This market will resolve to 'Up' if the {asset} price at the end of the time range … is
> **greater than or equal to** the price at the beginning … The resolution source for this
> market is information from **Chainlink, specifically the {SYM}/USD data stream** available
> at https://data.chain.link/streams/{sym}-usd. … this market is about the price according
> to **Chainlink data stream {SYM}/USD, not according to other sources or spot markets.**"

`resolutionSource = https://data.chain.link/streams/{sym}-usd` for btc/eth/sol/xrp.

**This overturns `phase0_audit.md` Task 4's "RESOLUTION ORACLE: coinbase_price" verdict.**
That verdict was an artifact of timing: Chainlink data did not exist when the audit ran
(May 15–21), so `coinbase_price`'s 94.69% agreement was read as proof. In reality Coinbase
spot merely *tracks* Chainlink closely, so it agrees ~94% by correlation — it is not the
settlement source. Three other docs (`discovery.py:83`, `chainlink_collector.py` docstring,
`canonical_dataset.md`) already asserted Chainlink; this confirms them authoritatively.

### Two nuances that change Phase 1

- **Data Streams ≠ on-chain Aggregator.** Resolution uses Chainlink **Data Streams** —
  a low-latency, report-based, ~sub-second feed. Our `chainlink_collector.py` polls the
  **on-chain Aggregator** (`latestRoundData()` on Polygon), which is the *slow* push feed
  (~heartbeat/deviation). **These are different products.** The on-chain aggregator we
  collect is neither the settlement feed nor (likely) what informed participants price off.
  → The "oracle is stale / frozen for ~15s" flavor of the Phase 1 edge is **weak**: the
    settlement feed is near-real-time. The viable Phase 1 angle is **spot-distance-from-
    strike late-window determinism** (in the final seconds, when the price is decisively
    away from the strike the outcome is locked, yet the book may still price uncertainty),
    NOT oracle-update-staleness.
  → **Action:** use the fast Coinbase WS spot as the settlement proxy (tracks the stream to
    a few bps); consider adding a Chainlink **Data Streams** collector for an exact reference.

- **Ties resolve Up** (`>=`). Our `outcomes.csv` convention labels ties (end==start) as
  **Down** (`phase0_audit.md` Task 4). Exact ties are rare, but the label is wrong on them.
  → **Action:** Phase 0b dataset uses `end >= start → Up`.

- **Strike basis.** True strike = Chainlink-stream price at window-open; our recorded
  `start_price` is Coinbase-based. A small basis exists exactly in the marginal/late-window
  cases where Phase 1's edge would live. Carry it as a known uncertainty; prefer the
  settlement-proxy price for distance-to-strike.

---

## 2. Live fee schedule (authoritative, identical across all 4 symbols)

```
feeSchedule: { exponent: 1, rate: 0.07, takerOnly: true, rebateRate: 0.2 }
makerBaseFee: 1000   takerBaseFee: 1000   makerRebatesFeeShareBps: 10000
feeType: crypto_fees_v2   feesEnabled: true
```

- **Taker fee** = `0.07 · p · (1−p) · shares` (exponent 1 → the `p(1−p)` form). Confirms the
  cost model. **Takers only; makers pay 0.**
- **Maker rebate** = 20% of the taker fee on matched volume (`rebateRate: 0.2`), maker's
  share `makerRebatesFeeShareBps: 10000`. Matches `cost_notes.md`'s ~0.35¢/share estimate.

## 3. Liquidity rewards program — **NEW, material for Phase 3 (maker)**

```
rewardsMinSize: 50   rewardsMaxSpread: 4.5   (¢)
```

Polymarket pays **liquidity rewards** for resting orders within **4.5¢ of mid** at **≥$50
size**. The prior market-making study (`market_making_feasibility.md`) counted only the
~0.35¢ rebate and concluded NO-GO on `spread(1¢) − adverse-selection(2.25¢)`. It did **not**
include liquidity rewards. If rewards are non-trivial per share, they shift the maker P&L
materially — this is a genuine new input the Phase 3 maker analysis must quantify (reward
pool size per market, share earned at $50–$X size, vs the measured adverse selection).

## 4. Microstructure parameters

- `orderPriceMinTickSize: 0.01` → **1¢ price grid.** Any mispricing must be ≥1 tick to be
  representable; a stale-quote pickoff (Phase 2) must exceed ~1¢ + fee to be real.
- `orderMinSize: 5` → **$5 minimum order** — small-stake capable.
- `spread` field: btc 1¢, eth 1¢, sol 2¢, xrp 3¢ — matches the audit's depth/spread picture
  (BTC deepest/tightest; SOL/XRP wider). BTC remains the only book worth quoting into.

---

## Downstream actions (carried into Phase 0b/0c and Phases 1–3)

1. **Phase 0b dataset:** label with `end >= start → Up`; compute distance-to-strike off the
   fast Coinbase-WS settlement proxy; flag the coinbase-vs-stream basis as a column.
2. **Phase 0c cost model:** taker `0.07·p·(1−p)`, maker 0 + 20% rebate, 1¢ tick, $5 min.
3. **Phase 1:** reframe to spot-distance-from-strike late-window determinism (drop the
   oracle-staleness sub-hypothesis A2 unless a Data Streams collector is added).
4. **Phase 3:** add liquidity rewards (4.5¢ max spread, $50 min) to the maker P&L — the
   prior NO-GO did not include them.
5. **Optional collector upgrade:** add a Chainlink **Data Streams** reader for the exact
   settlement reference (current `chainlink_collector.py` reads the wrong Chainlink product
   for settlement purposes — keep it for cadence research, but it is not the settle feed).
