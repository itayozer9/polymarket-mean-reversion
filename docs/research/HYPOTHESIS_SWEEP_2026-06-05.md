# 2099-Hypothesis Latency-Proof Strategy Sweep — 2026-06-05

**Goal:** find new / improved *super-robust, profitable, latency-proof* strategies for the
Polymarket 15m crypto Up/Down bot, using 2 weeks of live L2 data. Test like live trading; no
edge from speed. Keep all running strategies untouched; deploy survivors to 1-week paper.

**Bottom line:** of **2099** economically-motivated hypotheses, after Chainlink settlement, a
held-out future block, full-L2 cost-stress, and 4 adversarial skeptics, **exactly one** is a
genuinely new, robust, deployable edge: **`det_d12_wide_v1`** (improved determinism). The sweep
also independently **re-discovered and confirmed** the already-running `fav_disagree` edge, and
produced a pile of honest negatives. This is a winnowing result, by design — a wider search with
an honest multiple-testing posture yields few survivors, and that is the point.

---

## Method (what makes this trustworthy)

One shared harness (`research/analysis/edge_lab.py`) judged every hypothesis identically:

1. **Hypothesis = entry filter + buy-side rule.** 2099 specs across 17 families
   (`research/analysis/hypothesis_sweep.py`), each with a stated economic rationale — grids
   explore each thesis's parameter sensitivity (robustness), not random numbers.
2. **No lookahead.** Decision is the *first qualifying tick* per window; all features are
   strictly point-in-time. **Hold to resolution** — no intra-window exit, so no speed needed.
3. **Chainlink settlement only.** Every EV via the oracle Polymarket actually pays
   (`resettle_chainlink`), never Coinbase (which is ~20–30% optimistic).
4. **Latency-proof gate (the user's hard requirement).** Each survivor must keep a positive,
   CI-lower-bound>0 EV with the fill delayed to **5s and 10s** — a home trader is never fastest.
5. **Realistic fills.** Verification walks the real **10-level L2 book** (`fills_v2.walk_buy`),
   dropping trades that can't fill; capacity measured at $10→$250.
6. **Data & splits (whole-UTC-day):** clean window 05-23..06-04 (post strike-fix, all feeds +
   L2). dev 05-23..27 (selection only), holdout 05-28..31, **future 06-01..04 held out** and
   revealed once in verification.
7. **Multiple-testing honesty.** Deflated Sharpe computed at `n_trials=2000`. With only ~13
   daily observations this *mathematically rejects everything* (the luck-of-2000-trials Sharpe
   threshold ≈0.99 exceeds even the best edge's daily Sharpe). So **DSR is a ranking signal, not
   a binary gate** — the real arbiters are the held-out future block, cost-stress, adversarial
   review, and ultimately the **1-week forward paper run**.
8. **Adversarial verification.** 4 independent skeptic agents, each told to *refute* a cluster
   (find the artifact / duplicate / directional drift), not confirm it.

Harness self-check passed: it reproduces the published determinism baseline to the cent
(+$0.87 vs +$0.88).

Pipeline: `hypothesis_sweep.py` (2099 → backtest, 8-way sharded) → `hypothesis_select.py`
(future-blind gates → 24 shortlist) → `hypothesis_verify.py` (full-L2, future revealed,
cost-stress) → adversarial skeptics → this report.

Funnel: **2099 hypotheses → 1587 screened-in → 855 passed future-blind gates → 24 shortlist →
9 passed L2 future+cost-stress → 1 survives adversarial review as a new deploy.**

---

## The one deployment: `det_d12_wide_v1`

Improved determinism — **consistent** (book agrees with spot), **last 0–180s**, **|spot−strike|
≥ 12 bps**, buy the favourite at ask **0.50–0.85**, hold to Chainlink resolution.

| metric | value |
|---|---|
| FULL | **+$1.31/tr** [+0.93, +1.68], n=466, WR 85.6% |
| holdout (05-28..31) | +$1.86/tr, n=88 |
| **future (06-01..04, held out)** | **+$1.04/tr [+0.45, +1.56]**, n=250 |
| ALL-combined cost-stress (fee 1.5× + 1c slip + lat5 + 30% rejects) | +$1.12/tr [+0.69] |
| directional balance | UP +$2.06 / DOWN +$1.32 (both positive; UP stronger) |
| capacity | fills $10–$100/tr ≥0.96; $250 ≈0.87 |
| overlap vs running det | Jaccard 0.24 (76% new trades) |

**Why it survived refutation (skeptic, ~70% confidence):**
- The running `det_lwd` (dist≥5, t≤60) is genuinely **dead OOS** on the same harness
  (future −$0.06 [−0.84, +0.67]) — confirming the project's "det ≈ break-even fresh" memory.
  `det_d12_wide` is a *different, mostly-disjoint* trade population that is future-positive.
- **Real mechanism, not a fitted threshold:** sweeping dist_min holding else fixed, future EV is
  *monotone increasing* in distance, with the CI lower bound flipping positive at dist 8→12 and
  a stable plateau at 12–20. Deeper lock = book lags a more-set outcome.
- Future EV positive on 3/4 held-out days, all 4 coins, not jackpot-driven (broad fills),
  win-rate (88%) comfortably above the fee-adjusted breakeven for its ~0.77 avg entry.
- **Paper-only, not live yet:** future EV is ~20% below dev/holdout (expected book-lag decay),
  06-01 mildly negative, and the whole thing rests on ~17 calendar days. That's what the 1-week
  paper forward-test is for.

Deployed to `strategies.yaml` as `det_d12_wide_v1` (`enabled: true, live: false`, $10 bet,
$50/day `hard_worstcase` cap). Uses only existing `DetParams` gates → no engine change; the
registry builds it on the identical code path as the validated running det strategies.

---

## What the sweep rejected (and why that's the valuable part)

| cluster | best member | verdict | reason |
|---|---|---|---|
| Mid-window disagreement | e4_1074 (disagree, dist≥12, t120-300) | **confirm-only, no deploy** | 100% subset of the **already-running `fav_disagree`**. Re-discovered from scratch = strong independent confirmation that fav_disagree is real; deploying it again would double-bet identical windows. |
| Low-vol favourite-value | fav_0409 (consistent, dist≥12, vol≤1, t120-300) | **reject** | Its future edge is *entirely DOWN-driven* (DOWN +$3.59 WR96% / UP −$1.07 negative). A down-drift / macro-correlated artifact. The running `fav_lowvol` is the directionally-balanced version (both legs positive). |
| Z-score distance gate | zscore_1852 (z = \|dist\|/(rvol·√t_left)) | **reject** | The "z is the #1 filter" hypothesis was **refuted**: z divides distance by vol, down-weighting exactly the high-vol far-distance windows that pay. It catches net-negative trades and discards the best ones; plain dist≥12 strictly dominates. Most z survivors' future CI crosses zero. |
| Vol-regime conditioning | vol_1612 | **fold-in, no separate deploy** | Literally the disagreement edge sliced by a vol band; both regimes stay positive (edge does *not* concentrate in a regime), so the band adds fragility (smaller n, more knobs), not robustness. |
| Oracle divergence (Chainlink vs Coinbase basis) | — | **dead** | Only 2 of ~136 oracle hypotheses even screened in. The pure settlement-basis trade is not an edge here (matches prior "det ≈ break-even on Chainlink" finding). |
| L2 imbalance / microprice / taker-flow as a *signal* | micro_1150 | **dead** | Order-flow/imbalance does not predict to resolution (future negative). Consistent with theory: it predicts the *next tick*, which fast traders arb away. |
| Mean-reversion | — | **stays dead** | Not resurrected. |

---

## Honest caveats

- **Sample is short.** ~17 calendar days. Every edge here, including the deploy, needs the
  forward week to confirm; this is selection + winnowing, not proof.
- **The fav-value & disagreement books are DOWN-tilted on this sample** and macro-correlated
  (one spot move → 4 coins). Treat the whole fav book as ~1 leveraged macro position, not 4
  independent bets (matches the existing "sq variance is macro-correlated" memory). Actionable
  monitor during the paper week: watch each edge's **UP-vs-DOWN** live PnL split; if UP legs go
  flat, the fav book is a drift bet and should be sized as one position.
- **Tooling bug found (logged, non-blocking):** `hypothesis_verify.py`'s Jaccard baseline used
  the *consistent* fav slice, not the deployed `fav_disagree` params, so its overlap numbers
  understated duplication with the running disagree edge. Conclusions were corrected by the
  skeptic's manual overlap computation (100% containment). Fix the baseline before the next sweep.

## Next steps

1. **1-week paper forward-test** `det_d12_wide_v1` alongside the untouched running strategies
   (restart the bot when convenient via `/mean-rev-restart`; the file edit alone is inert until
   restart, and there is a live real-money probe running — restart on your schedule, not mine).
2. At review (`/mean-rev-review`, ~2026-06-12): compare paper-vs-backtest drift (<30%), confirm
   future EV holds and the UP leg stays positive, then decide on a small live test.
3. **Optional, your call (touches a running strategy, so not done here):** tighten the running
   `fav_disagree` dist 10→12 to concentrate on its higher-WR core — but only after controlling
   for the DOWN-drift.

## Addendum — intra-window reversion + martingale (tested 2026-06-05, REJECTED)

A user lead, tested on request: enter on a dip (bet reversion), set a small TP (10/15/20%),
**double/triple losers** (martingale, max 2–3 adds), $50/day cap, forced settle at expiry.
Faithful intra-window simulator (`research/analysis/martingale_sweep.py`): TP sells into the bid,
adds on deeper dips, Chainlink settlement at the clock. Grid = 144 configs (both entry triggers ×
TP × 2x/3x × adds × step), ~5000 windows × both sides.

**Result: 0 of 144 configs profitable — on capped EV, uncapped EV, OR the held-out future block.**
- Best (least-bad) config: **−$1.66/trade** with an **89% TP-hit rate**, worst single window
  −$74, max drawdown **$1,371** (capped). Uncapped, that same config: −$6.18/tr, max drawdown
  **$56,192**.
- Median config: −$5.74/tr, 85% TP-hit, worst window −$149, max DD $2,237.

This is the textbook **martingale trap, empirically confirmed**: the high TP-hit rate (it "works"
~85–89% of the time) is exactly the seduction; the ~11–15% of windows where price doesn't revert
and the **binary settles 0/1 against the doubled position** erase all the small wins and then some.
The hard 15-minute expiry means "wait for it to revert" is not available — you settle at the clock.
The underlying reversion thesis is the same one the project killed at inception; martingale reshapes
the risk (many small wins, rare ruinous losses) without creating EV. **Do not deploy.** Artifact:
`data/research/hypotheses/martingale.jsonl`.

## Reproduce

```
uv run python -m research.analysis.edge_lab            # harness self-check
uv run python -m research.analysis.hypothesis_sweep i 8 # shard i of 8 (writes results_i.jsonl)
uv run python -m research.analysis.hypothesis_select    # -> shortlist.jsonl
uv run python -m research.analysis.hypothesis_verify    # -> verified.jsonl (full L2)
```
Artifacts in `data/research/hypotheses/`: `specs.jsonl`, `results_*.jsonl`, `shortlist.jsonl`,
`verified.jsonl`.
