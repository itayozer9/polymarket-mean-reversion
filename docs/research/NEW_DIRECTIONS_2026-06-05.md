# New Strategy Directions — Maker Execution, Oracle Staleness, New-Signal Sweep (2026-06-05)

**Ask:** think hard about *new* directions (obvious or not, simple or complex) — what recurs in
the data that we can exploit — beyond the hold-to-resolution taker sweep.

**Bottom line:** three directions tested rigorously (Chainlink-settled, held-out future,
cost/adverse-selection honest), **three clean negatives.** No new deployable edge. The durable
edge remains determinism (already captured by `det_d12_wide_v1`). The negatives are valuable and
well-diagnosed — especially the maker result, which explains *why* our edges are cost-bound and
rules out a very tempting direction. Methodology and artifacts let the next idea be tested in minutes.

Modules: `research/analysis/{maker_execution,chainlink_staleness,new_signals_sweep}.py`.
Artifacts: `data/research/hypotheses/{maker_execution,chainlink_staleness,new_signals}.jsonl`.

---

## WP1 — MAKER / limit-order execution (REJECTED)

**Idea (highest-value):** every edge is killed by taker cost (fee + half-spread eats 50–85%). Rest
a bid instead of crossing → pay no fee, earn a rebate, enter cheaper; being patient is the ultimate
no-speed approach. Re-ran det_d12_wide, det_lwd, fav_lowvol, fav_disagree as resting makers on the
**real trade tape** (`maker_buy_fill`), settling on Chainlink, hold-to-resolution.

**No-speed discipline:** assume we are LAST in queue (queue-ahead = full visible depth), strict
trade-through (a print must trade *below* our level), post at entry+buffer, non-fill = skipped/zero-cost.

**Result — reject, consistent across all edges:**

| edge | taker EV (all) | maker fill-rate | adverse-sel (WR filled−all) | maker EV (filled) | future maker EV |
|---|---|---|---|---|---|
| det_d12_wide | +$1.48 | 10% | **−18.9 pp** | −$0.93 | −$0.94 |
| det_lwd | +$1.12 | 6% | −24.5 pp | −$2.12 | −$2.44 |
| fav_lowvol | +$0.92 | 10% | −26.7 pp | −$2.21 | −$2.23 |
| fav_disagree | +$9.12 | 8% | −17.8 pp | +$0.77 (n=17, CI [−3.0,4.7]) | −$1.52 |

The spread+rebate saving is real (maker−taker on the same filled windows = +$0.20 to +$0.85), but
**adverse selection (−18 to −27 pp win-rate) overwhelms it.** A resting bid fills precisely when the
favourite is being sold down — i.e. on the losers. *Taker-on-the-filled-subset is also deeply
negative*, proving it's the **fill selection**, not the maker mechanics, that's toxic: the windows
where a patient bid fills are intrinsically losing windows. Fill rates are low anyway (6–18%) because
we never assume speed/queue priority. **Conclusion: stay taker.** The decision-time book already
prices the info; patience only buys you the trades you'd want to avoid.

## WP2 — Chainlink staleness-at-expiry (REJECTED)

**Idea (novel):** the settlement oracle is stale a lot (age median 16s, >30s 7%); near expiry, when
the last CL value is old and live spot has moved past it, maybe the settlement reflects the stale
oracle (and the book is on the other side). Buy the stale-CL side, hold to settle. Distinct from the
dead *basis-level* oracle work — this is about *update timing*.

**Result — reject, killed by its own distinctness gates:**
- **feed-changes-before-end rate = 0.999.** The premise is false: with 30–90s left the feed updates
  2–6 more times, so the decision-time value is almost never the settlement value.
- **No age-monotonicity:** EV does NOT concentrate in the stale tail (the <15s bucket gives the
  *highest* EV, +$11.7; >30s +$9.1). So any signal is the known disagreement effect, not staleness.
- The disagree+gap rules leave tiny n (25/21 trades) with **negative future EV** (−$3.54, −$5.22).

## WP3 — New-signal mini-sweep (REJECTED — all re-label determinism)

87 specs across three families; 17 "passed gates" but the **incremental test** (does the filter beat
plain determinism on the same base?) killed all of them:
- **Cumulative whole-window flow** (signal + det-filter): the best "survivor" is dist12/t≤120 with a
  flow gate — but plain determinism on that base is *better* (full +1.33/future +0.86, n=203) than
  with the flow filter (+1.25/+0.76, n=159). Flow only removes trades. Re-labels det.
- **Round-number strike-pinning**: "far-from-round" is marginally cleaner only by dropping ~14% of
  trades (no real lift over the dist8 base); the eye-catching cells (XRP "near", +$2.08) are **n=25
  noise** — the flagged artifact. Not a real edge.
- **L2 ladder-shape / walls**: did not survive as a top candidate — a next-tick signal that dies on
  hold-to-resolution, as predicted.

---

## What recurs in the data (verified) — and why it's not (yet) tradable

| phenomenon | frequency | tradable? |
|---|---|---|
| Sub-$1 book arbitrage (yes_ask+no_ask<1) | 0.003% | No — market too efficient |
| Cross-window outcome autocorrelation | ρ=0.72 (!) | No — decide-at-open already tested DEAD (priced in) |
| Chainlink staleness >30s at decision | 7% | No — feed updates before settle 99.9% |
| Round-number strikes (XRP) | 31% near | No — slicing det, n too small |
| Whale prints (>100 contracts) | 4.45% | Untested as standalone; flow proxy re-labels det |
| Ask-side depth ≫ bid-side | 3–4× | Structural (buy-pressure); a capacity fact, not an edge |

The recurring theme across this and the 2099-sweep: **signals that look predictive predict the
*next tick* (which needs speed we don't have) and die on hold-to-resolution.** The durable edges are
*structural* — book-lags-spot (determinism), favourite-longshot bias, book-vs-spot disagreement — and
the binding constraint is *execution cost*, which (WP1) maker execution cannot fix here.

## Decisions / deploys
- **No new strategies deployed.** Running strategies untouched. `det_d12_wide_v1` (from the prior
  sweep) remains the one new paper edge to forward-test.
- Maker execution is **not** worth a live limit-order path for these edges (adverse selection).

## Still-untested (lower-priority, for a future round)
Position sizing (Kelly / vol-scaled) and **ensemble agreement sizing** (size up when ≥2 disjoint
edges co-fire) — these are *multipliers on existing edges*, not new edges, and are the most likely
next source of improvement. Online/rolling fair-value curve (to revive the drifted stale-quote).
Funding-time (00/08/16 UTC) regime conditioning. These were scoped out of this round.

## Reproduce
```
uv run python -m research.analysis.maker_execution     # WP1
uv run python -m research.analysis.chainlink_staleness # WP2
uv run python -m research.analysis.new_signals_sweep   # WP3
```
