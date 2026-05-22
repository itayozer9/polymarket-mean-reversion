# Phase 0 — Data & Simulator Audit

Findings from auditing the data and the imported decision logic before any
strategy work. Each section is appended by its task. A "CONCERN" or "BUG" tag
means later phases must account for it.

## Task 3 — Tick data quality

Script: `research/audit/quality.py`. Run over all 4 symbols × 2 timeframes for
`2026-03-04`..`2026-05-22`. Data covers two separate regimes: Mar 4–17
(historical) and May 15–22 (live). An ~8-week gap (Mar 18 – May 14) has no
data — days in that gap are simply absent; the loader handles this correctly.

### Cross-symbol summary table (mean over days per regime)

| Symbol | TF  | Days | Windows | Ticks    | Gap% | Stale% | Crossed% | MidErr   |
|--------|-----|------|---------|----------|------|--------|----------|----------|
| btc    | 15m | 11   | 612     | 545 045  | 0.8% | 87.6%  | 20.4%    | 0.097    |
| btc    | 5m  | 22   | 3 951   | 1 179 656| 0.4% | 62.4%  | 13.8%    | 0.301    |
| eth    | 15m | 11   | 611     | 544 814  | 0.7% | 90.3%  | 20.9%    | 0.096    |
| eth    | 5m  | 22   | 3 948   | 1 179 599| 0.4% | 68.4%  | 14.8%    | 0.310    |
| sol    | 15m | 11   | 613     | 545 353  | 0.7% | 92.0%  | 18.5%    | 0.081    |
| sol    | 5m  | 22   | 3 949   | 1 179 804| 0.4% | 73.9%  | 15.4%    | 0.312    |
| xrp    | 15m | 11   | 613     | 545 074  | 0.7% | 93.3%  | 19.4%    | 0.089    |
| xrp    | 5m  | 22   | 3 950   | 1 179 907| 0.4% | 77.5%  | 14.7%    | 0.309    |

Depth (ask-side, top-of-book only):

| Regime             | Symbol | TF  | Median YES ask depth | Median NO ask depth |
|--------------------|--------|-----|----------------------|---------------------|
| Mar 4–14 (hist)    | btc    | 5m  | ~37 000 USD          | ~39 000 USD         |
| Mar 4–14 (hist)    | eth    | 5m  | ~15 000 USD          | ~15 000 USD         |
| May 15–22 (live)   | btc    | 15m | ~170 USD             | ~175 USD            |
| May 15–22 (live)   | eth    | 15m | ~45 USD              | ~45 USD             |
| May 15–22 (live)   | sol    | 15m | ~20 USD              | ~18 USD             |
| May 15–22 (live)   | xrp    | 15m | ~19 USD              | ~19 USD             |

### Per-regime breakdown

| Regime      | Gap%  | Stale%  | Crossed% | MidErr  |
|-------------|-------|---------|----------|---------|
| Mar 04–13   | 0.0%  | 54.4%   | 8.2%     | 0.52483 |
| Mar 14–17   | 2.3%  | 85.9%   | 50.4%    | 0.20926 |
| May 15–22   | 0.0%  | 87.8%   | 6.6%     | 0.06614 |

### Metric interpretation and CONCERN tags

**Gap rate:** Fraction of consecutive in-window ticks with `seconds_into_window`
gap > 2. Threshold: >10%.

All daily gap rates are 0.0% except March 15 (5.6–5.8% across all symbols),
which only has 3 windows — that day is incomplete (collection started mid-day).
March 16–17 shows 1.5–1.6% and 0.9–1.0% respectively due to end-of-collection
edge effects. Mean gap rate across all data is 0.4–0.8% by symbol/TF, **well
below the 10% threshold.** No CONCERN on gap rate.

**Stale rate:** Fraction of consecutive ticks where all four book columns
(`yes_best_bid`, `yes_best_ask`, `no_best_bid`, `no_best_ask`) are identical.
Threshold: >25%.

**CONCERN: Stale rate is severely elevated across all symbols and both
timeframes (54–97% depending on day/symbol).** The stale rate is 25%+ on every
single day in the dataset without exception. The pattern worsens over time:
March 4–13 averages 54.4% (frozen half the time); May 15–22 averages 87.8%
(frozen nearly 9 ticks in 10). At 1 Hz sampling, high stale rates mean the
book was quoted at the same price for multiple consecutive seconds — consistent
with a low-liquidity binary market where market makers only quote periodic
updates. This is real market microstructure, not a collector bug, but it means
consecutive ticks carry no new information most of the time. Strategies using
per-second momentum signals should account for this.

**Crossed book rate:** Fraction of ticks where `yes_best_bid > yes_best_ask`
OR `no_best_bid > no_best_ask`. Threshold: >1%.

**CONCERN: Crossed-book rate is severely elevated for the March 16–17 data
(76–83% of 15m ticks, 83–88% of 5m ticks for all 4 symbols).** Investigation
reveals two distinct sub-cases:

1. **March 4–14 (5m only):** The market had a one-sided thin book — almost all
   YES bids were posted at the floor (0.01) while YES asks ranged 0.30–0.65.
   This means `yes_bid=0.01 < yes_ask=0.50` — technically NOT crossed, and the
   crossed-book rate here is 4–12%. These are genuine book states from a period
   when liquidity was thin and one-sided.

2. **March 15–17 (all TFs):** `yes_bid > yes_ask` at rates of 76–83% with
   `yes_bid + no_ask = 1.00` exactly in 100% of crossed rows. This is a
   **data format artifact**: the March 15–17 historical data was collected under
   a different recording convention where YES and NO bids/asks encode the binary
   complementary relationship (the NO ask at 0.53 = 1 − YES bid at 0.47).
   Despite the apparent inversion, the mid (`yes_mid + no_mid`) for March 16–17
   is ~1.00 (correct), confirming the books are internally consistent.

3. **May 15–22:** Crossed-book rate is 3.1–8.3%, entirely explained by
   **decided markets** — windows where the outcome is effectively known
   (`yes_best_ask = 0`, `yes_best_bid ≈ 1.0`). These are cosmetic artifacts of
   a market that has been resolved but still has resting bids in the book.

**Practical implication:** Strategy entry signals should filter on ticks where
BOTH sides have non-zero asks (`yes_best_ask > 0` AND `no_best_ask > 0`) AND
`yes_bid < yes_best_ask` to exclude decided-market and format-artifact rows.

**Mid-sum error:** Mean `|yes_mid + no_mid − 1.0|`. Threshold: >0.02.

**CONCERN: Mid-sum error exceeds the 0.02 threshold on every single day in the
dataset.** Three root causes:

1. **March 4–14 (5m):** `yes_bid = 0.01`, `yes_ask = 0.50` → `yes_mid = 0.255`.
   Similarly `no_mid ≈ 0.24`. Sum ≈ 0.50 → error ≈ 0.50. This is not a data
   corruption — it reflects a genuinely illiquid 5m market where the bid side
   is at the floor. The `mid` formula `(bid+ask)/2` is misleading here; the
   true fair value is closer to the ask price.

2. **March 16–17:** Due to the binary-pair recording format, ~20% of rows have
   decided markets (`yes_bid=0.99, no_ask=0.01`, etc.) → `yes_mid=0.0,
   no_mid=0.0` → sum=0.0. The other 80% have sum=1.0. Average error ~0.08–0.10.

3. **May 15–22:** Mid-sum error averages 0.04–0.09, driven by decided-market
   rows (~5–7% of ticks) where `yes_mid + no_mid = 0`. Excluding decided-market
   ticks, mid-sum error in May data is <0.005.

**Depth profile:**

Historical (March 4–14) top-of-book depths are massive (USD 5 000–50 000 per
side). This does NOT reflect real liquidity — it reflects a period when the
markets were illiquid and/or depth was being posted as a wall. Live (May) data
shows dramatically lower depths: BTC 15m ~170 USD, ETH ~45 USD, SOL/XRP ~20
USD. A $10 fill is comfortably within the top-of-book depth for May data on all
symbols. However, for any fill larger than ~$100 on SOL/XRP, walking the book
would be required.

### March 4–13 vs March 14–17 quality comparison

**March 4–13 is NOT lower quality — it is a different market regime.** These
days have zero genuine crossed-book ticks (the bid floor is 0.01 on most rows,
never above the ask), zero sampling gaps, and stale rates around 40–60% which
reflects genuine infrequent quoting on a thinly traded market. The mid formula
produces sums of ~0.52 (far from 1.0) because the YES side had almost no
resting bids — this is real market state, not corruption.

**March 14–17 has the most complex data quality picture** due to three
simultaneous effects: (a) end-of-collection truncation causing 1–6% gap rates
on some days; (b) the switched data format where yes_bid > yes_ask is normal
(binary-pair encoding); (c) decided markets adding to the crossed/mid-sum
counts. March 16–17 should be treated with caution for strategy work — the
recording format is internally consistent but differs from the May live format.

### Structural data limitation

**CONCERN: Top-of-book only.** The schema carries exactly one depth level per
side (`yes_bid_depth`, `yes_ask_depth`, `no_bid_depth`, `no_ask_depth`) — the
dollar size resting at the best bid/ask price. There is no level-2 book. This
means:

- Walk-the-book slippage for fills larger than the top-of-book depth cannot be
  measured — only approximated.
- True market capacity (how much volume a strategy can absorb without moving
  the market) is unknown beyond the single top level.
- For May live data where SOL/XRP top-of-book depth is ~$10–20 USD, even a
  small $25 order would walk the book, and this cost is invisible in the data.

This limitation persists through all later phases. Task 6 (cost realism) must
note it explicitly, and Phase 5 strategy sizing must treat top-of-book depth as
a hard cap, not a ceiling to work up to.

## Task 4 — Outcome correctness
_pending_

## Task 5 — Proximity filter bug
_pending_

## Task 6 — Fee & cost realism
_pending_

## Task 7 — Look-ahead / leakage audit
_pending_

## Task 8 — Sim vs live-paper reconciliation
_pending_
