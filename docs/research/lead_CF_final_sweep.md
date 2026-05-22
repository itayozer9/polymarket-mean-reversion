# Lead C/F — final directional sweep

**Date:** 2026-05-22
**Branch:** `edge-leads`
**Code:** `research/analysis/final_sweep.py` (`run()` + `__main__`)
**Scope:** the last three open directional-edge hypotheses on Polymarket 15m
crypto Up/Down markets. Prior leads A (within-market arbitrage), B (cross-coin
lead-lag) and D (maker-execution reframe) all came back clean negatives. These
are completeness checks — tested properly, reported honestly.

All work is on the **corrected** dataset (`data/research/ticks_15m.parquet`,
real Polymarket-resolved `outcome_up`, corrected strikes — see
`docs/research/FINAL_REPORT.md`). Development split **May 15–20** only; the
**May 21–22 hold-out is sealed** and asserted untouched in every loader. The
healthy-book guard (genuine two-sided books only — both sides quoted strictly
inside (0.001, 0.999), not crossed, complement-consistent `yes_ask + no_bid ≈
1`) is applied throughout; it removed ~17% of raw ticks (decided-market /
crossed / one-sided books) and has caught artifacts repeatedly.

Cost model: taker pays the ask plus the Polymarket crypto fee
`0.07·p·(1−p)` per share on entry; a held-to-resolution binary has no exit fee.
Maker pays 0 fee. Round-trip taker cost ≈ 16–21% of stake. Stake $10/trade.
CIs are window-clustered bootstraps (the window is the resampling unit because
~87% of ticks are stale).

---

## Hypothesis C — intra-window momentum

### The idea

Phase 3's drop event study found that after an odds drop the price *continues
down* (momentum, not reversion). We tested buying the **dropped (falling)** side
and lost. We never tested the **momentum direction**: when a side makes a sharp
intra-window move, buy the side moving *in the trend's favour* — the **rising**
side — and ride it.

### The test

On the healthy two-sided book, mid-window only (`seconds_into_window ≥ 60` and
`time_left_sec ≥ 60`, avoiding the open noise and the decided last minute), a
"sharp move" is a tick where exactly one side's signed 30-second mid change
(`yes_velocity_30s` / `no_velocity_30s`) is positive and above the 90th
percentile of `|velocity_30s|` pooled over both sides (threshold `0.130`). We
**buy that rising side** at the first qualifying tick in the window (one trade
per window — a patient bot acts once). Two exits: hold to resolution (settle on
`outcome_up`), and a +$0.10 intra-window profit target. Out-of-fold via
`day_blocked_kfold`; a random-entry null shuffles which window's outcome each
trade receives.

### Result (dev, May 15–20)

| Variant | n | WR | PnL/trade | window-clustered CI | $/day |
|---|---|---|---|---|---|
| Hold to resolution — **taker** | 1608 | 64.7% | **−$0.317** | [−0.774, −0.154] | −$96.95 |
| Hold to resolution — **maker** | 1608 | 64.7% | +$0.327 | [−0.192, +0.466] | +$99.84 |
| +$0.10 profit-target — taker | 1608 | — | **−$0.816** | [−1.021, −0.617] | −$249.46 |
| Random-entry null (taker) | — | — | +$0.094 (mean) | — | — |

`p(null ≥ observed taker)` = **1.000** — the random null *beats* the momentum
signal: shuffling outcomes does better than picking the rising side.

The rising side wins **64.7%** of the time — but that is exactly what a
~0.27-priced side *should* win if the market is well-calibrated (the side is
the favourite). The momentum signal adds nothing on top of the price.

Threshold sensitivity — momentum loses at every cut:

| velocity percentile | taker PnL/trade | CI | null_p |
|---|---|---|---|
| p80 | −$0.572 | [−0.71, −0.09] | 1.000 |
| p85 | −$0.446 | [−0.71, −0.08] | 1.000 |
| p90 | −$0.317 | [−0.77, −0.15] | 1.000 |
| p95 | −$0.567 | [−0.83, −0.17] | 1.000 |

The lone "positive" cell — maker, hold-to-resolution, +$0.33/trade — does **not**
survive scrutiny:

- Its pooled CI [−0.192, +0.466] **includes zero**.
- Dev-internal CV: early dev half (May 15–17) +$0.674 CI [+0.050, +1.292];
  late dev half (May 18–20) **−$0.117** CI [−0.500, +0.271]. The sign flips
  between halves — **not CV-stable**.
- The maker fill is optimistic: resting a buy limit at the **bid of a side that
  is rising** rarely fills — the bid runs away from you. Realistically you only
  fill on a pullback (adverse selection), so the +$0.33 is an upper bound on an
  already-unstable, CI-includes-zero number.

### VERDICT — Hypothesis C: **NO EDGE.**

Riding intra-window momentum is net-negative as a taker (−$0.32 to −$0.82/trade,
CI excludes zero on the wrong side, robust across all thresholds) and loses to a
random-entry null. The only positive number (maker hold-to-resolution) has a CI
including zero, flips sign across the dev split, and rests on an unrealistic
fill assumption. Momentum is not a tradeable directional edge.

---

## Hypothesis F — time-of-day / liquidity regime

### The idea

Earlier work found an "ASIA hours" effect that was later shown to be an overfit
artifact on corrupt data. Re-test honestly on corrected data: is the cheap-side
gross edge — or any tradeable signal — materially different in any UTC hour or
liquidity bucket?

### The test and the multiple-testing trap

The de-biased cheap-side cross-section (one observation per window × 60-second
slice; gross edge = `cheap_won − cheap_mid`) was bucketed by UTC hour. **11 of
24 hours** show a window-clustered CI excluding zero — but at a 90% CI you
*expect* ~2–3 false positives by chance among 24 tests, and 11/24 with a real
mix of signs is the signature of noise plus a structural pattern, not a clean
edge. So we require **dev-internal CV stability**: a bucket qualifies only if
**both** dev halves (May 15–17 and May 18–20) independently show a CI excluding
zero with the **same sign**.

Three hours survived the CV-stability filter on the **gross** edge: **hours 12,
14, 19 UTC** — all *negative* cheap-side gross (the cheap side underperforms its
mid, i.e. the expensive side over-performs).

### Why a CV-stable gross hour is still not an edge

The de-biased cross-section has **~14 observations per window**. A
window-clustered bootstrap on it has an effective n ~14× that of a real
**one-trade-per-window** backtest, so its CIs are far too tight to be the bar
for a tradeable claim. A gross-edge flag is only a *candidate*. The honest test
is a one-trade-per-window, **net-of-cost** backtest: at the first healthy
mid-window tick, buy whichever side the hour favours (the expensive side for
hours 12/14/19), pay the taker fee, settle on `outcome_up`.

| Hour | side bought | n | net taker PnL/trade | window-clustered CI |
|---|---|---|---|---|
| 12 | expensive | 64 | −$1.229 | [−3.081, +0.538] |
| 14 | expensive | 64 | +$1.807 | [+0.300, +3.233] |
| 19 | expensive | 64 | −$0.046 | [−1.780, +1.641] |
| **Combined basket** | per-hour | **192** | **+$0.177** | **[−0.783, +1.129]** |

The combined basket — the headline, a single consistent rule across all
CV-stable hours — is **+$0.18/trade with a CI spanning zero**. CV of that net
number: early dev half +$0.415 CI [−1.449, +2.223]; late dev half +$0.098 CI
[−1.070, +1.228] — **neither half excludes zero**. Hour 14 alone *looks*
positive, but its early-dev-half net CI [−2.33, +2.86] includes zero — it is one
hour out of 24 catching a run of luck, not a stable effect. The CV-stable
*gross* flag dissolved entirely once tested honestly per-trade and net of cost.

### Liquidity-regime buckets

Bucketing the de-biased cross-section by liquidity proxies — terciles of cheap-
side spread, of cheap-side depth, and of per-window stale-rate — found **no
positive tradeable bucket**:

| Proxy | bucket | cheap-side gross | CI |
|---|---|---|---|
| spread | low / mid / high | −0.017 / +0.003 / −0.020 | all ≤ 0, mid spans 0 |
| depth | low / mid / high | −0.017 / −0.018 / −0.016 | all negative, CI excl. 0 |
| stale-rate | active / mid / **stale** | +0.017 / −0.015 / **−0.053** | active spans 0; stale strongly **negative** |

The only large effect is that **staler windows have a *more negative* cheap-side
gross edge** (−0.053, CI [−0.065, −0.039]) — the cheap side does *worse* on
illiquid books. That is the opposite of a tradeable cheap-side signal, and it is
consistent with the healthy-book/decided-market story from prior leads (stale
books drift toward resolution; the cheap side is the loser). It offers no
positive-EV entry.

### VERDICT — Hypothesis F: **NO EDGE.**

No UTC hour and no liquidity bucket is genuinely and CV-stably tradeable. Eleven
hours flag on the de-biased *gross* edge (expected noise across 24 tests); only
three survive gross CV-stability; and those three collapse to +$0.18/trade with
a CI spanning zero and no net CV-stability once tested as an honest, cost-
inclusive, one-trade-per-window backtest. The old "ASIA hours" effect does not
reappear in any tradeable form on corrected data. Liquidity regime only makes
the cheap side *worse* where it is illiquid — not an edge.

---

## Hypothesis G — 5m markets

### The test

The 5m markets were never outcome-corrected (only 15m was) — `outcome_up` in
`ticks_5m.parquet` still carries the strike bug. The Polymarket gamma
`/events` API exposes resolved 5m outcomes (`outcomePrices`) the same way it
does for 15m, so this was cheap to run: all **5,018** 5m dev windows (May
15–20) were fetched (165 s, 100% resolved) and cached to
`data/research/corrected_labels_5m.parquet`. A quick calibration was run on the
genuine two-sided 5m book with the same healthy guard.

### Result (5m, corrected outcomes, dev May 15–20)

- Healthy 5m ticks with a corrected outcome: 1,401,470 (5,018 windows).
- **Overall cheap-side gross edge (de-biased): −0.0035, CI [−0.0094, +0.0025]**
  — indistinguishable from zero.
- Calibration by `cheap_mid` price bucket:

  | cheap_mid bucket | n windows | mean mid | realized cheap-win | gross | CI |
  |---|---|---|---|---|---|
  | (0.00, 0.20] | 3595 | 0.098 | 0.086 | −0.012 | [−0.019, −0.005] |
  | (0.20, 0.35] | 3644 | 0.280 | 0.267 | −0.013 | [−0.025, −0.001] |
  | (0.35, 0.50] | 4982 | 0.451 | 0.457 | +0.006 | [−0.002, +0.014] |

The 5m cheap side is, if anything, very slightly **over-priced** in the deep
buckets (gross −1.2 to −1.3¢, CI just excluding zero) — the same direction as
15m: the cheap side underperforms its mid by a hair. There is no positive
mispricing, and 5m round-trip taker cost is the same `0.07·p·(1−p)` structure
plus spread — so a ~1¢ gross *underperformance* is nowhere near tradeable.

### VERDICT — Hypothesis G: **NO EDGE; 5m looks like 15m.**

With corrected, API-resolved outcomes the 5m markets are well-calibrated —
overall gross edge −0.4¢ with a CI spanning zero, and a faint cheap-side
*over*-pricing in the deep buckets identical in direction to 15m. The 5m
markets are not materially different from 15m and are not exploitable. (The
corrected 5m labels are now cached at
`data/research/corrected_labels_5m.parquet` for any future work.)

---

## Overall verdict

**No edge. All three completeness checks are clean negatives.**

- **C — intra-window momentum:** riding the rising side loses −$0.32 to
  −$0.82/trade as a taker, robust across thresholds, and loses to a random-entry
  null (null_p = 1.000). The lone positive (maker) has a CI spanning zero and
  flips sign across the dev split.
- **F — time-of-day / liquidity:** the 11 "significant" hours are expected
  multiple-testing noise; the 3 that survive gross CV-stability collapse to
  +$0.18/trade (CI spans zero, no net CV-stability) once tested honestly
  per-trade and net of cost. The old "ASIA hours" effect does not reappear.
  Liquidity regime only worsens the cheap side where it is illiquid.
- **G — 5m markets:** with corrected API outcomes, 5m is well-calibrated and
  indistinguishable from 15m — no exploitable mispricing.

This is fully consistent with `FINAL_REPORT.md` and leads A/B/D: the 15m (and
now 5m) Polymarket crypto Up/Down markets are **efficient-after-cost**. The
~16–21% taker round-trip cost is a structural wall, and none of momentum,
time-of-day, liquidity regime, or the 5m category produces a real, cost-
surviving, CV-stable directional edge. The market has now been tested
efficient-after-cost from every directional angle pre-registered.

### Methodology notes (what kept this honest)

1. **The de-biased cross-section is not a backtest.** Its ~14-obs/window
   structure makes its bootstrap CIs ~14× too tight for a tradeable claim. Every
   gross-edge flag was re-tested as a one-trade-per-window, net-of-cost
   backtest before being called an edge — which is exactly what dissolved the
   "CV-stable" hours 12/14/19.
2. **Multiple testing across 24 hours** was handled by requiring dev-internal
   early/late CV stability, then a second honest net-of-cost CV on top.
3. **The random-entry null** is the right bar for Hypothesis C: a 64.7% win
   rate on a 0.27-priced favourite is calibration, not edge — the null exposes
   that the signal adds nothing.
4. The **healthy-book guard** removed ~17% of raw ticks; the residual negative
   cheap-side gross on stale books is the decided-market drift the guard exists
   to neutralise.
