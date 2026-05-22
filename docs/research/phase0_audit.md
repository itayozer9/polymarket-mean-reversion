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

## Task 3b — March data encoding forensics

Follow-up audit triggered because Task 3 dismissed the March 15–17 crossed-book
anomaly as a "benign recording-convention difference." That conclusion does not
survive scrutiny. A correctly labelled order book *always* has ask ≥ bid; the
`yes_mid + no_mid ≈ 1` sanity check used in Task 3 cannot detect a bid↔ask swap
because `(bid+ask)/2` is invariant under the swap. This section settles the
question with numbers.

Script: `research/audit/march_encoding_probe.py`. Reference regime is May 15–22
(collected by the current bot — known-good). All percentages below are over
two-sided ticks (`0 < bid,ask < 1` on both YES and NO) unless stated.

### Headline numbers (YES side; `yes_best_ask − yes_best_bid`)

| Regime    | Sym | TF  | n (ticks) | ask ≥ bid | ask < bid | median(ask−bid) | bid≤0.02 |
|-----------|-----|-----|-----------|-----------|-----------|-----------------|----------|
| Mar 04–13 | btc | 5m  |   622 234 |   96.7%   |   3.3%    |  **+0.49**      | 100%     |
| Mar 04–13 | eth | 5m  |   622 374 |   95.8%   |   4.2%    |  **+0.49**      | 100%     |
| Mar 14    | btc | 5m  |    12 385 |  100.0%   |   0.0%    |   —             | 100%     |
| Mar 15    | btc | 15m |     1 126 |   90.7%   |   9.3%    |   small         | 50.7%    |
| Mar 16–17 | btc | 15m |    36 206 |  **13–15%** | **85–87%** | **−0.16 to −0.19** | 7–13% |
| Mar 16–17 | eth | 15m |   ~35 800 |  ~14%     | **~86%**  |  **−0.17**      | 7–13%    |
| Mar 16–17 | sol | 15m |   ~36 900 |  ~16%     | **~84%**  |  **−0.14**      | 8–10%    |
| Mar 16–17 | xrp | 15m |   ~33 800 |  ~15%     | **~85%**  |  **−0.15**      | 9–12%    |
| May 15–22 | all | both|  ~496k ea |  96–98%   |   2–4%    |  **+0.01**      | ~0%      |

The NO side mirrors the YES side exactly in every regime (`yes_best_bid +
no_best_ask = 1` and `yes_best_ask + no_best_bid = 1` hold in 92–97% of ticks
in *both* March 16–17 and May — same convention). The NO columns carry no
independent information; the anomaly is entirely about the YES bid/ask labels.

### Three distinct March regimes — not one

**Regime A — Mar 04–14 (5m only): degenerate bid column. Tag: CONCERN.**
`yes_best_bid` is pinned at `0.01` in **95.8%** of rows and `0.00` in 3.7%
(btc; identical 100% bid≤0.02 for all 4 symbols, every day Mar 04–14). Out of
622 234 btc 5m ticks, exactly **one** has both bid and ask in a normal
(0.02, 0.98) range. This is not a "thin one-sided book" that happens to be
real — it is a column that never carries a usable quote. The YES bid side is
effectively absent for this regime. Task 3's claim that this is "genuine market
state, not corruption" is unprovable either way, but the operational fact is
the same: **there is no usable bid price for Mar 04–14, so no round-trip trade
can be priced from this data.** 15m markets do not exist before Mar 15.

**Regime B — Mar 15 (15m + 5m): transitional, mostly OK. Tag: OK (small n).**
Collection started mid-day (Task 3 noted this); only ~1 100 ticks/day. About
30–50% still show the 0.01 bid floor, but among genuine two-sided ticks
`ask ≥ bid` holds 73–97%. Mar 15 looks like the changeover day.

**Regime C — Mar 16–17 (15m + 5m): broken book. Tag: BUG.**
`yes_best_ask < yes_best_bid` in **83–88%** of two-sided ticks across all 4
symbols and both timeframes. `spread_yes` is literally `ask − bid` (verified
100% in every regime — see below) and is therefore **negative** in 76–83% of
15m ticks. The book is internally "consistent" only in the trivial sense that
`spread_yes` faithfully reports a negative spread. A market book with a
persistently negative spread is not a real book.

### What `spread_yes` / `spread_no` / `total_mid` actually are

- `spread_yes == yes_best_ask − yes_best_bid` (signed) in **100.0%** of ticks
  in *every* regime — Mar 04–13, Mar 16–17, May. It is the raw signed
  difference, not `|ask − bid|`. In May it is positive (+0.01 median); in
  Mar 16–17 it is negative (−0.14 median, 15m). `spread_no` is likewise
  `no_best_ask − no_best_bid`, 100% match. **A negative `spread_yes` is the
  cleanest single tell that the March 16–17 book is mis-encoded.**
- `total_mid == yes_mid + no_mid` in 100% of ticks everywhere. As predicted,
  `total_mid ≈ 1.0` is preserved on the broken data because the mid is
  swap-invariant — it is worthless as a corruption check.

### Is it a clean bid↔ask swap? No.

Hypothesis: swapping `yes_best_bid ↔ yes_best_ask` (and `no_best_bid ↔
no_best_ask`) on Mar 16–17 would restore a sane book. **It does not.**

- After the swap, `ask ≥ bid` holds in only **85–88%** of Mar 16–17 15m
  two-sided ticks (btc 88.5%, eth 88.5%, sol 85.7%, xrp 85.7%), not ~100%.
  The residual ~12% are rows that were *already correctly encoded*
  (`bid < ask`, spread +0.01, indistinguishable from May) — the swap *breaks*
  them. Mar 16–17 is a **mixture** of correctly-encoded and broken ticks,
  ~85/15, intermixed within the same windows (mean ≈ 5 crossed↔normal flips
  per 15m window; crossed-rate is roughly flat ~77–89% across all ten 90-second
  buckets of the window — no clean temporal split).
- Even on the rows the swap "fixes," the resulting spread is insane: post-swap
  median spread is **0.21–0.22** for all four symbols — **~22× wider than
  May's 0.01**. Real top-of-book spreads on these markets are 1–3¢. A swap
  that yields a 22¢ spread has not recovered a real book; it has just flipped
  the sign of a number that was never a spread.
- Inspecting individual Mar 16 windows tick-by-tick: one column per side sits
  at a sticky level for long stretches (e.g. `yes_best_bid` frozen at 0.54 for
  ~250 s) while the other column moves monotonically with `move_pct`. Both
  columns are ~93% stale window-over-window in aggregate, and both correlate
  with `move_pct` at ~0.59–0.61 (vs ~0.28–0.36 in May) — i.e. neither column is
  a clean "truth" column and neither is clean garbage. The two columns are not
  a mislabeled (bid, ask) pair; they are two loosely-related price series whose
  difference is meaningless.

**Conclusion on the mechanism:** Mar 16–17 is *broken*, not *swapped*. The
recording on those two days produced a YES bid/ask pair that is neither a valid
book nor a recoverable swap of one. A blanket column swap would mislabel ~15%
of ticks the wrong way and still leave a 22¢ pseudo-spread. The data cannot be
normalised by any column permutation.

### Depth scale — Tag: CONCERN, unresolved

March top-of-book depths are 100–1000× May's:

| Regime    | median `yes_ask_depth` (btc) |
|-----------|------------------------------|
| Mar 04–13 5m   | ~38 600 |
| Mar 16–17 15m  | ~34 600 |
| May 15–22 15m  | ~132    |
| May 15–22 5m   | ~138    |

Tells examined: (a) `yes_bid_depth == no_ask_depth` in 96–99% of ticks in
*every* regime — the depth columns mirror the same complement convention as
prices, so the unit is at least internally consistent across March and May;
(b) March depths are not round numbers (e.g. 22 280.82, 14 923.82) — they look
like summed dollar quantities, not a count; (c) March depth does not collapse
to May's range under any obvious /100 or /1000 factor that also makes sense of
the prices. The most likely readings are either a **genuine liquidity-regime
difference** (March markets were quoted with large walls) or a
**cumulative-vs-top-of-book** difference (March depth = full-book USD, May
depth = best level only). The data alone cannot distinguish these. Either way:
**March depth is NOT comparable to May depth and must not be fed to the same
fill model.** Any backtest that sized fills against March depth (≈$35k of
"liquidity") would assume effectively unlimited capacity.

### Backtest impact — Tag: BUG (critical)

`BACKTEST_VERDICT.md` Stage A — the headline 1000-config Latin-Hypercube sweep
— ran on **BTC 15m, Mar 15–17** (`sweep_15m_btc_2026-03-15_to_2026-03-17_
n1000_20260515_054915.jsonl`). That window is ~95% Regime C (broken) data.

The simulator (`polymarket-arb/scripts/mean_reversion/simulate.py`) buys at
`yes_best_ask` (`_try_fill_entry` → `_side_price`, line ~188) and marks/sells
the exit at `yes_best_bid` (line 118, "peak_bid = the achievable sell price").
On Mar 16–17 BTC 15m, `yes_best_bid` exceeds `yes_best_ask` in **86%** of
two-sided ticks by a **median of 0.22** (mean 0.235, p90 0.42).

So on the great majority of Mar 16–17 ticks the simulator **buys at the lower
number and sells at the higher number** — a structural, mechanical profit on
every round trip, before any signal skill. The magnitude:

- Per-share free edge ≈ **0.22** (median) on a crossed tick.
- On a $10 bet that is **≈ $2.2 of fake PnL per trade** handed to the backtest
  purely by the encoding.
- Per-window mean inverted spread ≈ 0.18–0.21 across the 58 BTC 15m windows in
  the sample.
- For the validated deep-dip config (`cfg_21c8c00165b3`, buys YES at
  0.075–0.125), the entry uses the *low* column; an entry near 0.10 with the
  exit-side bid sitting ~0.22 higher is an instant ~200%-of-notional paper
  gain. This is almost certainly the dominant source of the 88–93% backtest win
  rates in `BACKTEST_VERDICT.md`, and the direct explanation for why live
  trading lost.

### VERDICT

| Regime    | Bid/ask encoding              | Depth scale        | Verdict |
|-----------|-------------------------------|--------------------|---------|
| Mar 04–14 | `yes_best_bid` degenerate (pinned 0.01); no usable bid | ~100–1000× May, not comparable | **BROKEN — unusable for round-trip pricing** |
| Mar 15    | OK among two-sided ticks; transitional, tiny n | as above | OK but negligible volume |
| Mar 16–17 | **BROKEN** — 83–88% negative spread; not a clean swap; ~15% of ticks intermixed-correct | not comparable | **BROKEN — not recoverable** |
| May 15–22 | Correct (`ask ≥ bid` 96–98%, +0.01 spread) | the reference | **OK** |

- **BUG:** The `BACKTEST_VERDICT.md` Stage A sweep ran on Mar 15–17 BTC 15m,
  ~95% of which is broken Regime C data. With the simulator buying at `ask` and
  selling at `bid`, ~86% of ticks handed it a median **$2.2-per-$10-trade**
  mechanical edge. **The headline backtest edge is an encoding artifact, not a
  real strategy edge.** `BACKTEST_VERDICT.md` should be treated as invalid.
- **CONCERN:** March depth is 100–1000× May and cannot be unit-reconciled from
  the data alone — exclude it from any shared fill model.
- Task 3's "benign recording-convention difference" conclusion is **wrong** and
  is hereby superseded.

### RECOMMENDATION

1. **Quarantine all March 16–17 data** (`{btc,eth,sol,xrp}_2026-03-16.csv.gz`,
   `..._2026-03-17.csv.gz`). It cannot be normalised: it is not a swap (a swap
   leaves 15% mislabeled and a 22¢ pseudo-spread), and there is no column
   permutation that yields a May-consistent book. Do not feed it to any
   backtest. Do not "fix" it by swapping.
2. **Quarantine Mar 04–14 for any logic that needs a bid price** (i.e. all
   round-trip / mean-reversion strategies). `yes_best_bid` is pinned at the 0.01
   floor; exits cannot be priced. The `yes_best_ask` series may still be usable
   for ask-only / entry-only research, but treat with suspicion.
3. **Keep Mar 15** only if a strategy can tolerate ~1 100 ticks; it is
   effectively negligible.
4. **Re-run the entire validation sweep on May 15–22 data only** — that is the
   only regime with a verified-correct book. `BACKTEST_VERDICT.md`'s
   Bonferroni claim must be re-established (or discarded) on May data alone.
5. **Do not normalise — exclude.** Add a hard date filter to the research
   loaders: only `2026-03-15` (optional) and `2026-05-15`..present are
   admissible for backtests that price round trips. (Investigation only — the
   loader was NOT modified as part of this task.)
6. If March-era data is ever needed, it must be re-pulled from source with the
   current collector's encoding, not salvaged from these files.

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
