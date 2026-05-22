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

Script: `research/audit/outcomes.py`. Run via `uv run python -m research.audit.outcomes`.
Data scope: `outcomes.csv` covers May 15–22 only (the live data period). All tick
comparisons use May-only data (quarantine enforced by default in `iter_windows`).

### 1. outcomes.csv structure

`outcomes.csv` has **65,763 rows** covering **10,255 unique window slugs** — an
average of 6.4 rows per slug. The per-strategy paper engine appends a row each
time any strategy closes a window, so popular slugs accumulate one row per active
strategy per window close. This is **expected behaviour**, not a bug.

**2,157 slugs have rows recording conflicting outcomes** (both "Up" and "Down" for
the same window). This is because different strategies can close a window at
slightly different instants and record different end prices. **The underlying
data is consistent within each row** — every single row satisfies
`outcome == ("Up" if end_price > start_price else "Down")` with 0 exceptions
(mismatch rate 0.0000%, threshold 0.5%). **No BUG on per-row consistency.**

**446 "tie" rows** exist where `end_price == start_price` (move_pct = 0.0). All
are labelled "Down". These are edge cases where the price exactly matched the
strike at window close. No concern — the labelling is a deterministic convention.

For all downstream analysis: deduplicate by `market_slug` (keep first occurrence)
→ 10,255 canonical outcome rows.

### 2. Tick-vs-outcome agreement

For 8,870 May windows where a last tick was available, the sign of the final
`move_pct` was compared to the recorded outcome:

| Result | Count | Rate |
|--------|-------|------|
| Agree (sign matches) | 8,344 | 94.15% |
| Disagree | 467 | 5.27% |
| Zero move_pct (tie) | 51 | 0.58% |
| No outcome entry | 8 | — |

The 5.27% disagreement is **structurally expected**: the collector records ticks
at 1 Hz but the settlement oracle is called at `window_end_ts`. The last tick is
typically at `seconds_into_window = window_duration - 1` (median gap 0s, but max
643s). Spot can cross the strike in the final seconds. This is not a data error.

### 3. Resolution oracle

Chainlink and Coinbase feed agreement with the recorded outcome was tested for
all 8,849 May windows that had an outcome match. **The result is unambiguous:**

| Feed | Wins | Windows | Agreement rate |
|------|------|---------|---------------|
| chainlink_price | 0 | 0 | — (never populated) |
| coinbase_price | 8,379 | 8,849 | **94.69%** |

`chainlink_price` is **0.0 in 100% of May tick rows** — the collector never
populated this field. Coinbase is the only feed present. Per-symbol agreement:

| Symbol | Coinbase agreement | Windows |
|--------|--------------------|---------|
| btc | 94.0% | 2,212 |
| eth | 95.0% | 2,213 |
| sol | 95.2% | 2,214 |
| xrp | 94.6% | 2,210 |

**RESOLUTION ORACLE: `coinbase_price`.** Polymarket settles BTC/ETH/SOL/XRP
up/down markets using a Coinbase price feed. Every fair-value calculation in
Phase 2+ must use `coinbase_price`, not `chainlink_price`. The 5.3% non-agreement
is due to the same last-tick gap described in section 2 — the last recorded
`coinbase_price` in the tick is not the exact settlement price.

**CONCERN: `chainlink_price` is never populated.** The field exists in the schema
but always reads 0.0. Any code that checks `chainlink_price > 0` as a condition
will never execute. This does not affect correctness (coinbase is the real oracle)
but means the chainlink column carries no information and should be treated as
absent for all analysis.

### 4. Coverage

| TF | Tick windows | Outcome windows | Tick∖Outcome | Outcome∖Tick |
|----|-------------|-----------------|-------------|--------------|
| 15m | 2,221 | 2,560 | 5 (0.2%) | 344 (**13.4%**) |
| 5m | 6,649 | 7,695 | 3 (0.0%) | 1,049 (**13.6%**) |

Tick windows missing an outcome are negligible (≤0.2%). Outcome slugs lacking
tick data account for **13.4% of 15m and 13.6% of 5m** windows — well above the
5% CONCERN threshold.

**CONCERN: 13.4% / 13.6% of outcome slugs have no corresponding tick data.**
These 344 + 1,049 slugs all cluster on May 15–16 (the first two days of the
collector). The collector discovered active markets incrementally at startup and
did not yet monitor all windows when it began. This is a startup gap, not a data
corruption. For tick-based backtests, 87% of the outcome set has full tick
coverage; the 13% gap reduces effective sample size but is recoverable as more
live data accumulates.

Date breakdown of outcome-only slugs:

| Date | 15m missing | 5m missing |
|------|-------------|------------|
| 2026-05-15 | 84 | 255 |
| 2026-05-16 | 260 | 794 |

### 5. P(Up) base rates (no-skill baseline)

| Symbol | 15m windows | 15m P(Up) | 5m windows | 5m P(Up) |
|--------|-------------|-----------|-----------|----------|
| btc | 640 | 0.4938 | 1,923 | 0.5023 |
| eth | 640 | 0.4797 | 1,924 | 0.4901 |
| sol | 640 | 0.4703 | 1,924 | 0.4782 |
| xrp | 640 | 0.4484 | 1,924 | 0.4797 |
| ALL | 2,560 | 0.4730 | 7,695 | 0.4876 |

All P(Up) values are near 0.47–0.50. No symbol is strongly biased. XRP and SOL
skew slightly toward Down (~47% Up). These base rates are the **no-skill baseline**
every Phase 2+ strategy must beat. A strategy claiming 55% win rate on XRP Down
is outperforming the 53% Down base rate by only 2 percentage points — marginal.

### Summary verdict

| Check | Result |
|-------|--------|
| Per-row outcome consistency | PASS (0 mismatches in 65,763 rows) |
| Tie handling | OK (446 ties, all labelled Down) |
| Conflicting duplicates | CONCERN (2,157 slugs — expected from multi-strategy writes) |
| Tick-vs-outcome agreement | OK (94.15% — 5.27% structural gap at window end) |
| Resolution oracle | coinbase_price, 94.69% agreement; chainlink always absent |
| Coverage — tick∖outcome | PASS (≤0.2% of tick windows) |
| Coverage — outcome∖tick | **CONCERN** (13.4% of 15m, 13.6% of 5m lack ticks) |
| P(Up) base rates | 0.47–0.50 across all symbols/timeframes |

## Task 5 — Proximity filter bug

Script: `research/audit/proximity_bug.py`. Run via `uv run python -m research.audit.proximity_bug`.

**BUG: The proximity filter in `polymarket-arb` is permanently inert for all
realistic market configurations.**

### Mechanism

`polymarket-arb/scripts/mean_reversion/features.py::proximity_pct_from_move`
computes:

```python
return np.abs(move_pct).astype("f4") / 100.0
```

It returns `|move_pct| / 100` — a unitless fraction (e.g. a 1.5% move yields
`0.015`).

`polymarket-arb/scripts/mean_reversion/signals.py::entry_signal` then applies:

```python
if features.proximity > entry.proximity_max_pct:
    return None  # reject tick — too far from strike
```

`proximity_max_pct` is configured in percent (e.g. `0.5` meaning "reject if spot
is more than 0.5% from the strike"). But `features.proximity` is in fraction
units (`0.005` for a 0.5% move). The comparison is always
`0.005 > 0.5` → `False` — the filter never fires.

### Reproduction numbers

Sampled 515 real BTC 15m windows from May 15–21 (May-only because March data is
quarantined by default; the proximity feature is equally inert regardless of
regime). Console output:

```
windows sampled: 515
largest |move_pct| observed: 1.4268%
feature 'proximity' at that extreme: 0.014268
  proximity_max_pct=0.2: filter ever rejects? False
  proximity_max_pct=0.5: filter ever rejects? False
  proximity_max_pct=1.5: filter ever rejects? False
  proximity_max_pct=3.0: filter ever rejects? False
  proximity_max_pct=100.0: filter ever rejects? False
VERDICT: proximity filter is inert for all realistic configs
```

The largest `|move_pct|` observed is 1.43%, which maps to a `proximity` value of
`0.01427`. Even the loosest threshold `proximity_max_pct=3.0` would require
`proximity > 3.0` to fire — which would require `|move_pct| > 300%`, an
impossible spot price move within a 15-minute window. The filter is inert at
every threshold that appears in any real config.

Note: the script caps at 2000 windows for speed; only 515 May windows exist in
the data by the time of this audit. On March data (if re-enabled with
`include_quarantined=True`), the conclusion is identical — March 15m `|move_pct|`
is also within ±5%, giving `proximity ≤ 0.05`, still far below any realistic
threshold.

### Implication

Every backtest and every live config ran with **no effective proximity filter**.
The user's core "near the strike" rule — that strategies should only enter when
the odds dip occurs while spot is close to the strike (not decisively above/below)
— was **never tested** in any backtest or paper run. The 88–93% win rates in
`BACKTEST_VERDICT.md` (already invalidated by Task 3b for other reasons) were
achieved on a config space where the proximity guard was silently disabled.

### Fix (Phase 1)

The broken arb function is left **untouched** — changing it would break the live
bot's replay-parity test (`tests/test_paper_engine_replay.py`) and is out of scope
for Phase 0. The canonical research dataset (Phase 1, Task 10) will carry a
**corrected proximity column in percent** (`|move_pct|`, computed by
`research.features.core.corrected_proximity_pct`), and a σ-proximity feature
(`sigma_proximity`) that normalises by remaining vol. All Phase 2+ strategy
research must use these corrected features, not the arb `proximity_pct_from_move`
function.

## Task 6 — Fee & cost realism

Script/notes: `research/audit/cost_notes.md`. WebFetch on `https://docs.polymarket.com/trading/fees`
(2026-05-22) returned full documentation — page was reachable. Spread quantification uses
BTC + ETH 15m ticks from May 15–21 (206,610 valid ticks after two-sided book filter and
odds-band filter: `yes_mid ∈ [0.05, 0.35]`).

### 1. Verified fee structure

The Polymarket fee formula is confirmed:

```
fee = C × feeRate × p × (1 − p)
```

where C = shares, p = share price. **Crypto markets use feeRate = 0.07 — the highest
non-zero rate on Polymarket.** Only takers pay; makers are fee-free. This matches
`config.py::FillParams` exactly.

The fee is symmetric around 50%: a 30¢ trade and a 70¢ trade incur identical dollar
fees. Fees peak at p = 0.50 (maximum variance); at 50¢, 100 shares → $1.75 fee. In the
entry-relevant band (5–35¢), fees are lower but still substantial because share counts
are high (stakes of $10 buy 30–200 shares).

### 2. Spread cost (May tick data, entry band 0.05–0.35)

| Metric | Value |
|--------|-------|
| Median spread (BTC/ETH) | **1.00¢** |
| Mean spread (BTC/ETH) | 1.35¢ |
| P90 spread | 2.00¢ |
| Median spread as % of mid | **6.1%** |
| SOL/XRP median spread | **2.00¢** |

The book is effectively a 1¢-wide quote for BTC/ETH and 2¢-wide for SOL/XRP in the
5–35¢ band. Because share counts are large (~45–50 shares per $10), even a 1¢ spread
costs ~$0.45–$0.55 in absolute dollar terms per direction crossed.

### 3. Round-trip cost (per-symbol, $10 stake)

**Cost breakdown at median BTC/ETH entry (ask ≈ 0.21, bid ≈ 0.20):**

| Component | $ | % of $10 stake |
|-----------|---|----------------|
| Entry fee (`0.07 × ask × (1−ask) × shares`) | $0.55 | 5.5% |
| Exit fee (`0.07 × bid × (1−bid) × shares`) | $0.52 | 5.2% |
| Spread cost (`shares × spread`) | $0.59 | 5.9% |
| **Total round-trip** | **$1.67** | **16.7%** |

**Per-symbol summary:**

| Symbol | Med ask | Med spread | Med ask depth | Med RT cost |
|--------|---------|------------|---------------|-------------|
| BTC | 21.0¢ | 1.0¢ | $137 | 16.4% |
| ETH | 22.0¢ | 1.0¢ | $41 | 17.0% |
| SOL | 21.0¢ | 2.0¢ | $14 | 19.5% |
| XRP | 22.0¢ | 2.0¢ | $14 | 21.0% |

**CONCERN: Total round-trip cost is 16–21% of stake, well above the 8% threshold.**
(Threshold: 8% of stake. Observed: 16.4–21.0%. All four symbols are flagged.) This is
driven roughly equally by fees (10–11% of stake) and spread (5–9% of stake). The fee
component alone exceeds the 8% threshold at typical entry prices in this band.

### 4. Break-even win rates by profit target

For a trade that wins at a given profit target and loses the full stake otherwise:

| Profit target | Net win if correct | Break-even WR |
|---------------|--------------------|---------------|
| 15% | **−$0.14** (cost > gross) | **>100% — not viable** |
| 25% | $0.83 | **92.4%** |
| 50% | $3.25 | **75.5%** |
| 75% | $5.70 | **63.7%** |
| 100% | $8.16 | **55.1%** |
| 120% | $10.14 | **49.6%** |

The dominant configs in `strategies.yaml` use a 50% profit target. This requires a
**75.5% win rate** just to break even. The empirical base rate is approximately 50%
(slightly favoring Down in the entry band). A strategy would need to win 25 additional
percentage points above the no-skill baseline to be profitable at +50% PT. At +120% PT,
break-even is 49.6% — barely below 50%, meaning even a marginally-better-than-random
strategy could survive costs, but wins are rare (entry at 22¢ → exit at 48¢ is a nearly
4× return, which requires the underlying to nearly reach its maximum UP resolution).

**CONCERN: Low profit-target configs (PT=15%, PT=25%) are unviable on fees + spread
alone.** A 15% PT trade loses money with 100% certainty regardless of win rate. A 25%
PT trade requires a 92% win rate — implausible on binary markets with a 50% base rate.
Phase 5 strategy construction must use PT ≥ 50% for BTC/ETH and PT ≥ 75% for SOL/XRP
if it is to have any realistic path to profitability.

### 5. Walk-the-book limitation

**Note: cost beyond the top level cannot be measured from this data.** The schema
carries only one depth level per side (`yes_ask_depth`, `yes_bid_depth`). All cost
figures above assume the full stake fills at the quoted best ask/bid. In practice:

- For BTC (median ask depth $137): a $10 fill is 7% of the top-of-book depth — safe.
- For ETH (median $41): $10 is 24% — still within the top level.
- For SOL/XRP (median $14): a $10 fill consumes 71% of the quoted depth. Any adverse
  selection or slight underfill would walk the book and incur additional slippage that
  is invisible in this dataset.

Cross-reference: Task 3 structural limitation (top-of-book only schema). Phase 5
strategy sizing must treat $14 (SOL/XRP) and $41 (ETH) as practical capacity ceilings,
not as guarantees that fills occur at quoted prices.

### Summary verdict

| Check | Result |
|-------|--------|
| Fee formula verified | PASS (0.07 × p × (1−p) × shares, confirmed by docs) |
| Taker-only fee | CONFIRMED (makers free; all mean-reversion entries are taker) |
| Median spread BTC/ETH | 1.00¢ (6.1% of mid) |
| Median spread SOL/XRP | 2.00¢ (~9% of mid) |
| Median round-trip cost BTC/ETH | **CONCERN: 16.4–17.0% of stake** |
| Median round-trip cost SOL/XRP | **CONCERN: 19.5–21.0% of stake** |
| PT=50% break-even WR | **75.5%** (vs. ~50% base rate) |
| PT=15% viability | **CONCERN: not viable** (cost exceeds gross profit) |
| Walk-the-book measurability | NOT MEASURABLE from this data (top-of-book only) |

## Task 7 — Look-ahead / leakage audit

Script: `research/audit/leakage.py`. Run via `uv run python -m research.audit.leakage`.
Scope: `polymarket-arb/scripts/mean_reversion/{features,signals,simulate}.py`.
Prior manual verdict (interim_code_audit.md finding #10): no look-ahead leakage.
This task makes that verdict reproducible with runnable assertions.

### Assertion A — reaction delay guarantees fill tick > signal tick

**PASS.** Source inspection of `simulate.simulate_market` confirms:

```python
delay_ticks = max(1, int(np.ceil(delay_sec)))   # always >= 1
armed_until_idx = i + delay_ticks                # signal_i + delay
```

In the ARMED branch, the fill only executes when `i >= armed_until_idx`, i.e.
at tick index ≥ `signal_i + 1`. The fill price is drawn from a tick that arrives
*after* the signal tick — exactly as in live trading. This is not look-ahead: the
"future" tick is the immediately next observable price, not a later-in-window price.

### Assertion B — `rolling_max_drop` is strictly causal

**PASS.** Source inspection of `features.rolling_max_drop` confirms:

```python
lo = max(0, i - window_sec)
window = price[lo:i + 1]   # indices lo..i only
```

The slice upper bound is `i + 1` (past-inclusive), never `i + 1:` (forward-start).
No forward-start patterns (`i+1:`, `i + 1:`, `i+2:`, `i + 2:`) appear anywhere
in the function. The function only reads price indices ≤ i — strictly causal.

### Assertion B2 — `realized_vol_60s_from_move` is strictly causal

**PASS.** Source inspection confirms:

```python
lo = max(0, i - 59)
window = move[lo:i + 1]    # indices lo..i only
```

Same pattern as `rolling_max_drop`: past-inclusive, no forward-start slice. Causal.

### Checklist — written verdict per item

| # | Item | Verdict |
|---|------|---------|
| 1 | `exit_signal` uses only current tick's bid | CLEAN |
| 2 | `forced_resolution` consults outcome only at window end | CLEAN |
| 3 | `_precompute_features` uses no window-global stats | CLEAN |
| 4 | `entry_signal` uses only pre-computed causal features | CLEAN |
| 5 | `peak_mid` tracking is monotone-max of past bids only | CLEAN |

**1. exit_signal uses only current tick's bid — CLEAN.**
`signals.py::exit_signal` calls `_side_bid(tick, position.side)`, which reads
`tick.yes_best_bid` or `tick.no_best_bid` — the *current* tick's bid. The
`peak_mid` tracker is updated in `simulate_market`'s HOLDING loop with `bid_now`
from the current tick only. No future tick is consulted.

**2. forced_resolution consults outcome only at window end — CLEAN.**
The forced-resolution trigger fires only when
`tick.seconds_into_window >= window_duration_sec - 2` (≤ 2s before the natural
end). The `outcome` tuple is passed in as a parameter (derived from outcomes.csv,
not from future ticks) and affects only the *exit price*, not the entry decision.
No tick-level look-ahead.

**3. `_precompute_features` uses no window-global stats — CLEAN.**
`simulate.py::_precompute_features` calls: `rolling_max_drop` (causal, assertion
B), `book_imbalance` (elementwise ratio — no window context), `proximity_pct_from_move`
(elementwise `|move_pct|/100` — no context), `realized_vol_60s_from_move` (causal,
assertion B2). No function computes a full-window max/min/mean that would require
knowing future ticks.

**4. entry_signal uses only pre-computed causal features — CLEAN.**
`signals.py::entry_signal` receives a `TickEvent` (current tick's values) and an
`EntryFeatures` (pre-computed from causal arrays by `_entry_features_at`). It
performs comparisons only — no array slicing, no future-tick indexing.

**5. peak_mid tracking is monotone-max of past bids only — CLEAN.**
`simulate_market` HOLDING loop: `if bid_now > position.peak_mid: position.peak_mid = bid_now`.
Running maximum of bids seen so far, updated with the current tick. The trailing
stop drawdown measures `peak_mid` vs current bid — both causal.

### VERDICT

**No look-ahead leakage found in the decision path.** All three automated
assertions pass; all five checklist items are CLEAN. This reproduces and confirms
the manual verdict in `docs/research/interim_code_audit.md` finding #10.

**Implication:** The bot's paper losses are genuine strategy failure, not a
backtest that cheated by peeking at future prices. The edge problem is real and
must be solved with a real edge — not by fixing a leak (there is none).

## Task 8 — Sim vs live-paper reconciliation

Script: `research/audit/reconcile.py`. Run via `uv run python -m research.audit.reconcile`.
Strategies checked: `cfg_21c8c00165b3` (175 trades) and `v2_gold_03_down_all` (117 trades).
All 292 trades are May dates (2026-05-16 to 2026-05-22 — the trustworthy regime).
Fill price tolerance: ±0.005 (0.5¢). Timestamp match window: ±2 s.

### Fill model

The simulator uses `cfg.fill.realistic_fill_model=True`:

- **Entry:** `effective_ask = min(1, depth/bet)×ask + max(0, 1−depth/bet)×(ask+0.02)` — fills
  at `yes_best_ask` (UP) or `no_best_ask` (DOWN); portion exceeding top-of-book depth fills
  2¢ worse.
- **Exit:** `effective_bid = min(1, depth/target)×bid + max(0, 1−depth/target)×(bid−0.02)` —
  where `target = shares × bid`. Portion exceeding bid depth fills 2¢ worse (toward 0).

The reconcile script checks each recorded fill against **both** the raw book best_ask/bid
AND the depth-adjusted model price. A fill is "reproducible" if it matches either within
tolerance.

### Results

| Strategy | Trades | Tick data | Entry repro | Exit repro | Both repro |
|----------|--------|-----------|-------------|------------|------------|
| `cfg_21c8c00165b3` | 175 | 150 (85.7%) | 150/175 (85.7%) | 150/175 (85.7%) | 150/175 (85.7%) |
| `v2_gold_03_down_all` | 117 | 117 (100%) | 117/117 (100.0%) | 117/117 (100.0%) | 117/117 (100.0%) |
| **Combined** | **292** | **267 (91.4%)** | **267/292 (91.4%)** | **267/292 (91.4%)** | **267/292 (91.4%)** |

**With-ticks-only fraction (fair comparison, excluding data-gap trades):**

| Metric | Value |
|--------|-------|
| Entry reproducible | 267/267 (100.0%) |
| Exit reproducible | 267/267 (100.0%) |
| Both reproducible | 267/267 (100.0%) |
| Mean entry discrepancy (best of raw vs model) | 0.00000 |
| Mean exit discrepancy (best of raw vs model) | 0.00000 |

Mean raw discrepancy (before depth-model correction): entry 0.00018, exit 0.00096. After
applying the depth-walk model, discrepancy drops to effectively 0.00000 for both. This
confirms the simulator fills **exactly** as the depth-walk formula dictates, using prices
drawn directly from the live tick files.

### Root cause of the 25 non-reconcilable trades

**CONCERN: 25 trades from `cfg_21c8c00165b3` (all on 2026-05-16) have no tick data to
reconcile against.** The live tick file `btc|eth|sol|xrp_2026-05-16.csv.gz` was truncated
at 07:19 UTC (likely due to a collector restart). These trades were placed by the paper
engine between 07:19 and end-of-day on May 16, during a period when the tick writer had
not yet resumed. The fills were recorded by the paper engine but the corresponding tick
snapshots were not captured in the CSV. These 25 trades are counted as non-reproducible
in the all-trades fraction (91.4%) but do not represent fill inaccuracy — they represent a
data-collection gap, not a simulation error.

### Depth-walk model: example verification

One trade (slug `btc-updown-15m-1779065100`, exit ts `1779065523000`) shows a recorded
`exit_price = 0.5489` vs raw `no_best_bid = 0.5600` (raw discrepancy 0.011). Applying
the model: `shares = 40`, `bid = 0.56`, `no_bid_depth = 10.00 USD`,
`target_usd = 40 × 0.56 = 22.4`. `portion_at_best = 10/22.4 = 0.4464`,
`portion_above = 0.5536`, `worse_bid = 0.54`. Modeled price: `0.4464×0.56 + 0.5536×0.54 = 0.5489`.
Matches the recorded exit price to 5 decimal places, confirming the fill model is faithfully
reproduced and the apparent discrepancy is intentional depth-walk slippage.

### Verdict

**PASS: Fills are honest.** The recorded fills match what the live book quoted, once the
depth-walk fill model is accounted for:

- **Reproducible fraction (with-ticks-only): 100.0%** — above the 90% threshold.
- **Mean price discrepancy: 0.00000** — well below the 0.01 threshold.
- The simulator and paper engine are using exactly the prices available in the live tick
  stream, with no cherry-picking or lookahead in the fill logic.

**The bot's paper losses are genuine strategy failure — not a fill artifact.** The
backtest fills used `yes/no_best_ask` and `yes/no_best_bid` from real tick data; the paper
engine replicated these exactly in live mode. The losses are real.

**Implication for Phase 2+:** The depth-walk model is internally consistent but adds
**real cost beyond the spread + fee** when fills exceed top-of-book depth. This is already
reflected in the live PnL. Phase 5 strategy construction should note that SOL/XRP (median
ask_depth ~$14) will routinely trigger the 2¢ slippage penalty on $10 fills (14/10 < 2 →
portion_at_best < 1), adding ~$0.55–0.80 to round-trip cost on top of the fee + spread
already quantified in Task 6.

## Task 8c — Window-open (t=0) forensics

**Decisive investigation.** The "divergence edge" in `docs/research/divergence_edge.md`
(`research/analysis/divergence_backtest.py`) reports +$6/trade, 78% win rate, out-of-fold
stable — with **~74% of trades entering at `seconds_into_window == 0`**. The claimed
mechanism: at window-open the Polymarket book sits ~50/50 while `move_pct` is already
non-zero, so the spot-favored side is cheap (~$0.48) yet wins ~95%. If `start_price` were
the resolution strike (= spot at window-open), `move_pct` at t=0 would be ≈0. It isn't.
This section settles whether that is a real dislocation or a mislabeling artifact.

Probe: `research/audit/window_open_probe.py` (committed). Data: `data/research/ticks_15m.parquet`
(2,232 15m windows, 2,000,171 ticks), `data/research/windows.parquet`, plus
`data/research/ticks_5m.parquet` as an extra coinbase-price source. Investigation only —
no data or loader was modified.

### Q1 — `start_price` identity

`start_price` is **constant within a window** for 2,228/2,232 windows (99.82%; the 4
exceptions are windows that lost their first ~445 s of ticks to a collection outage). The
tick-CSV `start_price` matches the `start_price` in `data/outcomes.csv` for 99.46% of
slugs. So both fields come from the **same source** — they are not independent.

Note: `data/outcomes.csv` itself is messy — many 15m slugs appear in multiple rows with
conflicting `end_price`/`outcome` (the discovery-poll close detector fires repeatedly).
`research/data/loader.py:load_outcomes()` silently keeps the first row. The clean
per-window outcome in `windows.parquet` (`outcome_up`) was used for all scoring here.

**VERDICT Q1 — OK (with caveat):** `start_price` is a single value per window and the
tick CSV and `outcomes.csv` agree on it. But agreement does NOT make it the true strike —
see Q2.

### Q2 — Why is `move_pct ≠ 0` at t=0

At `seconds_into_window == 0`, `move_pct` is **far from zero**: median |move_pct| =
**0.1425%**, and **81.1%** of windows have |move_pct| > 0.05%. `move_pct` is exactly
`(coinbase_price − start_price)/start_price·100` at the same tick row (max abs diff
5e-7) — so a non-zero `move_pct` means **`start_price ≠ coinbase_price` at window-open**.

Searching every 15m tick for a `coinbase_price` that *exactly* float-equals a window's
`start_price`: the nearest such tick to window-open lands at an **offset of −1700 to
−1900 s** for 58.2% of windows, and within ±30 s of open for only **2.2%**. That is,
`start_price` is the **Coinbase spot price sampled roughly 30 minutes BEFORE the window
opens**.

**Root cause (code-confirmed).** `gamma.list_active_markets` probes candidate slugs for
`k in (-1, 0, 1, 2)` future window slots — for 15m that is up to **2×900 = 1800 s ahead**.
`markets/discovery.py` step 2 backfills `start_price` with `coinbase.get_spot()` the
**first time a slug is seen** (`if m.start_price <= 0:`). A 15m slug first appears as a
`k=+2` candidate ~1800 s before its window opens, so `start_price` is frozen at the spot
of ~30 min earlier and never corrected. The −1750 s empirical offset matches the `k=+2`
lookahead exactly.

**VERDICT Q2 — BUG:** `start_price` is NOT the window-open strike. It is Coinbase spot
sampled ≈30 minutes early, frozen by the `discovery.py` `k=+2` backfill. `move_pct` at
t=0 is therefore not "spot vs strike" — it is a **30-minute trailing momentum** measured
against a stale reference.

### Q3 — Window time structure

15m windows are well-formed: duration `window_end_ts − window_start_ts == 900 s` for
100% of windows; consecutive windows are **contiguous** (gap == 0) for 99.64% (the rest
are real collection outages, gap > 0, never gap < 0); **zero overlapping** window pairs.
Exactly one 15m window is open per symbol at any instant.

**VERDICT Q3 — OK:** Window timing is clean. The artifact is not a windowing error.

### Q4 — Is the t=0 book fresh or carried over

The t=0 order book is **genuinely fresh**. It is byte-identical to the prior window's
last tick in only **1 / 2,220** contiguous pairs. The t=0 `yes_mid` has median **0.5050**
with 85.5% of windows in [0.45, 0.55] — a real fresh ~50/50 quote — whereas the prior
window's last tick sits at extremes (mid ≈ 0 or ≈ 0.9, i.e. already resolved). The t=0
book is two-sided for 100% of windows; median ask depth ~18 shares (thin but real).

**VERDICT Q4 — OK:** The t=0 book is a fresh, real, two-sided ~50/50 quote on a newly
opened market. The book is not stale. This is important — it means the artifact is in
`start_price`, NOT in the book.

### Q5 — Does the claimed edge reconcile with the true outcome

It reproduces exactly. Independently of the backtest code: take t=0 ticks, pick the side
`move_pct` favors (`move_pct>0 → Up`), look up `outcome_up` from `windows.parquet`:

- favored-side win rate **79.27%**, mean t=0 ask **0.4901**;
- mean PnL/trade ($10 stake, taker, hold-to-resolution) **+$5.93** — matches the
  divergence_edge.md headline;
- by magnitude: |move_pct| in [0.5,∞) → win **95.4%** at ask **0.473**. The
  "offered ~$0.48, wins ~95%" claim is **confirmed in the data**.

But this win rate is **circular**. `outcome` is scored as `end_price > start_price`
(holds for **99.73%** of windows — `start_price` IS the resolution reference *in this
dataset*), and `move_pct@t0 = sign(coinbase@t0 − start_price)`. The t=0 spot is already a
median 0.143% away from `start_price`, while spot moves only a median 0.107% *during* the
15-min window. So whichever side of the stale `start_price` spot sits on at t=0, it
usually still sits on at window-end — a mechanical autocorrelation, not a market edge.

**VERDICT Q5 — CONCERN:** The win rate and PnL reproduce, but they are an arithmetic
identity: betting `sign(move_pct@t0)` ≈ betting that a 30-min trailing move outlasts the
next 15 min, scored against the very stale price that defined the move.

### Q6 — Reprice speed (book vs `move_pct`, book vs outcome)

This is the kill shot. Correlation of the t=0..t book against the signal and the outcome:

| sec | corr(yes_mid, move_pct) | corr(yes_mid, outcome_up) | favored-side mid |
|----:|------------------------:|--------------------------:|-----------------:|
|   0 | **−0.586** | **−0.407** | 0.481 |
|   5 | −0.523 | −0.347 | 0.480 |
|  15 | −0.305 | −0.186 | 0.483 |
|  30 | −0.118 | −0.020 | 0.486 |
|  60 | +0.061 | +0.091 | 0.491 |
| 120 | +0.192 | +0.196 | 0.490 |
| 300 | +0.261 | +0.229 | 0.479 |
| 540 | +0.373 | +0.334 | 0.483 |

At t=0 the book is **negatively** correlated (−0.586) with `move_pct` and **negatively**
correlated (−0.407) with the eventual outcome — i.e. the Polymarket book at window-open
leans toward the side that, per `outcomes.csv`, *loses*. When the book leans Up
(`yes_mid > 0.52`) the labelled outcome is Up only ~18% of the time; when it leans Down,
Up ~78%. The favored-side mid **never reprices** — it stays flat at ~0.48 for the entire
window. The inversion **decays smoothly to zero by s ≈ 30–45 s** and then goes positive:
from s ≈ 60 onward the book correctly tracks live spot.

A real, money-traded market cannot systematically lean the wrong way at every window
open. The decisive cross-check:

- `outcome_up == (end_price > start_price)`: **99.73%** — outcomes are scored on the
  stale `start_price`.
- `outcome_up == (end_price > coinbase@t0)`: only **69.06%**.
- **Book FINAL lean** (last two-sided tick, mean s≈855, just before resolution) agrees
  with the labelled `outcome` only **68.7%**; `corr(final yes_mid, outcome) = +0.39`.

A real market settling a known binary MUST converge to ≈$0.99/$0.01 in its final minute.
This book does not — it settles around the **live-spot** 50/50, not the stale-strike
outcome. The book is pricing `end vs window-open spot`; `outcomes.csv` is scoring
`end vs spot-from-30-min-ago`. They are two different questions.

**VERDICT Q6 — BUG:** The "slow reprice" is fictitious. The book reprices fine — toward
live spot — within ~30–60 s. The apparent t=0 dislocation is the −30-min-stale
`start_price` projected onto a correctly-priced fresh book. The favored side never
reprices because it is, on average, *not* the side the market (or reality) favors.

### OVERALL VERDICT — DATA ARTIFACT

**TAG: BUG.** The divergence edge is **not real**. It is a `start_price` mislabeling
artifact, the same family of failure as the Task 3b March bid/ask corruption.

**Exactly what is mislabeled.** `start_price` (and hence `move_pct`, and hence the
`outcome`/`outcome_up` derived from it) is computed against **Coinbase spot sampled
≈1750–1800 s — about 30 minutes — before the window actually opens**, because
`gamma.list_active_markets` discovers slugs `k=+2` slots early and `discovery.py`
freezes `start_price` at first sight. The true 15-minute resolution strike is the spot
at `window_start_ts` (≈ `coinbase_price` at the t=0 tick), which is what the Polymarket
order book correctly prices and converges toward.

**Why the +$6/trade is fake.** The backtest buys the side of the *stale* `start_price`
that spot sits on at t=0 and scores it against an `outcome` *also* defined by that same
stale price. Because the 30-min trailing move (median 0.143%) is larger than the genuine
15-min window move (median 0.107%), `sign(end − start_price)` ≈ `sign(spot@t0 − start_price)`
mechanically — a tautology, not a tradeable signal. The 79% "win rate" is
`corr(move_pct, outcome) = 0.556` re-expressed, and both terms are arithmetic functions
of the same mislabeled `start_price`. The empirical "fair-value surface" in Task 8
simply re-learned this identity. The flat-surface null and matched-band controls in
`divergence_edge.md` are internally valid but were all run on the corrupted `outcome`
label, so they cannot detect the artifact.

It is also **not even a latency race**: at the genuine resolution strike
(`coinbase@t0`), the book is correct from t=0 and `corr(yes_mid, outcome)` is already
non-negative by s≈45. There is no real dislocation to race for.

**Consequences / required fixes.**
- The divergence edge in `docs/research/divergence_edge.md` is **withdrawn** — do not
  take it to the sealed hold-out.
- `start_price`, `move_pct`, `outcome`, `outcome_up`, `proximity_pct`, `sigma_proximity`
  and every feature derived from `start_price` in `ticks_15m.parquet` / `windows.parquet`
  / `data/outcomes.csv` are contaminated and must NOT be used for outcome scoring or as
  features. Any Phase-2+ result that consumed `outcome_up` from these files is suspect.
- Fix `markets/discovery.py`: sample `start_price` (and re-confirm it) **at or after
  `window_start_ts`**, not on first discovery of a `k>0` future slug. The true strike
  must be the Coinbase spot at the window-open boundary.
- Rebuild outcomes against `end_price > spot@window_start_ts` and re-run any backtest
  that depended on `outcome_up`.
- 5m windows are likely affected the same way (discovery uses the same `k=+2` probe);
  audit them before trusting any 5m result.
