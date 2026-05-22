# Lead B — Cross-Coin Spot Lead-Lag

**Date:** 2026-05-22
**Branch:** `edge-leads`
**Code:** `research/analysis/cross_coin_leadlag.py`
**Chart:** `docs/research/charts/lead_B_leadlag_curves.png`
**Data:** `data/research/ticks_15m.parquet` (corrected dataset), `coinbase_price`
for all 4 coins. Dev split **May 15–20** only; the **May 21–22 hold-out is
sealed and untouched** (asserted in code — `build_spot_grid` hard-asserts
`date.max() <= DEV_END`).

---

## The hypothesis

BTC, ETH, SOL and XRP move together. The hypothesis: **BTC (or the BTC+ETH
basket) leads the other coins' spot price by a capturable margin.** If so, when
BTC's spot jumps and SOL's spot has not yet followed, SOL's 15-minute Up/Down
binary — which resolves on SOL's spot — is predictable before the market
reprices, and a patient API bot can buy the basket-implied side.

Part 1 (does the *spot* lead-lag at all?) **gates** Part 2 (the binary
backtest). Part 1 must find a *capturable* lag — patient-bot-capturable means a
lag of roughly **≥10 s**; a 1–3 s lag is an HFT race a REST-polling bot can
never win.

---

## Critical data caveat — measured, not assumed

`coinbase_price` in this dataset is **not a real exchange tick feed**. It is the
collector's REST poll. Measured staleness on the dev set:

| coin | median constant-price run | mean run | % of 1 Hz ticks where price changed |
|---|---|---|---|
| btc | 13 s | 15 s | 6.7% |
| eth |  4 s | 11 s | 8.9% |
| sol |  4 s | 13 s | 8.0% |
| xrp |  4 s | 12 s | 8.4% |

The spot series is genuinely resolved at roughly a **13–15 s cadence**. At a 1 s
return horizon, **~92 % of follower log-returns are exactly zero.** Any lead-lag
*below* the poll cadence is invisible in this data **by construction**. We can
only resolve lags at or above ~15 s — and we carry this through every
conclusion. (This is the same staleness the FINAL_REPORT flagged: ~87 % of ticks
stale.)

---

## Part 1 — does the spot lead-lag?

Method: align all 4 coins' `coinbase_price` onto a common 1 Hz wall-clock grid
(375,399 seconds, dev only, collector gaps dropped). Log-return them. Leader =
BTC alone, and the equal-weight BTC+ETH **basket**. Cross-correlate
`corr(leader_t, follower_{t+lag})` for lag = −120…+120 s. Positive lag ⇒ leader
leads follower.

### Lead-lag curves (1 s return horizon)

| pair | contemp. r (lag 0) | best +lag | r at +peak | best −lag | r at −peak | excess vs contemp |
|---|---|---|---|---|---|---|
| basket→sol | **0.2972** | +1 s | 0.0934 | −1 s | 0.0388 | **−0.2038** |
| btc→sol    | **0.1497** | +1 s | 0.0883 | −1 s | 0.0440 | **−0.0614** |
| basket→xrp | **0.1822** | +1 s | 0.1634 | −1 s | 0.0423 | **−0.0188** |
| btc→xrp    | **0.1067** | +1 s | 0.1065 | −1 s | 0.0381 | **−0.0002** |

The curve (see chart) is a **sharp spike at lag 0** with small, roughly
**symmetric** decay on both sides. The correlation at *every* positive lag is
**below** the contemporaneous value — the "excess predictive power" is negative
everywhere. The positive-side peak lands at **lag +1 s**, inside the HFT-only
band, and is barely above the negative side.

### Coarse-horizon robustness check

Because 1 s returns are ~92 % zero (stale), structure could be masked. We
re-ran at 15/30/60 s return horizons, sampling **non-overlappingly** (every
h-th row) so each step is independent:

| horizon | pair | contemp. r | "lead" peak step | r at peak | r at best lag | % zero-return |
|---|---|---|---|---|---|---|
| 15 s | basket→sol | 0.6662 | +15 s | 0.1455 | 0.0602 | 46% |
| 15 s | basket→xrp | 0.5852 | +15 s | 0.1826 | 0.0635 | 44% |
| 30 s | basket→sol | 0.7484 | +30 s | 0.0947 | 0.0574 | 18% |
| 30 s | basket→xrp | 0.7086 | +30 s | 0.0973 | 0.0604 | 15% |
| 60 s | basket→sol | 0.8245 | +60 s | 0.0576 | 0.0424 |  8% |
| 60 s | basket→xrp | 0.7924 | +60 s | 0.0591 | 0.0422 |  6% |

Two facts kill the hypothesis:

1. **Contemporaneous correlation dwarfs everything.** At a 60 s horizon the
   coins are ~0.80–0.82 correlated *at the same instant*. The basket move is
   already in the follower's price by the time we observe it. There is nothing
   left to "catch up to."

2. **The "lead" peak sits at exactly one horizon-step, at every horizon.** At
   the 15 s horizon the peak is at +15 s; at 30 s it is at +30 s; at 60 s it is
   at +60 s. A *real* fixed-duration lead would stay pinned at the same
   wall-clock lag regardless of measurement horizon. A peak that tracks the
   step size is the textbook signature of **sub-cadence lead-lag aliased up to
   the sampling grid** — i.e. a follower move that lags by a few seconds shows
   up in the *next* poll, one grid-step later, whatever the grid is. And even
   that aliased peak (r ≈ 0.10–0.18) is small versus contemporaneous
   (r ≈ 0.59–0.82), and decays to noise (r ≈ 0.02–0.04, indistinguishable from
   negative lags) by step +2.

There is a faint genuine asymmetry — the basket does lead by *something* — but
that something lives **below the ~13–15 s feed cadence**: a few seconds at most.
That is an HFT race, not a patient-API-bot edge.

### Capturable-lag verdict

**No capturable lag.** No coin's spot lags the BTC/basket by ≥10 s with real
excess predictive power over the contemporaneous correlation. The honest gate
in the code (`peak_lag ≥ 10 s` **and** `peak_corr > contemp_corr` **and**
`excess > 0.01`) finds **zero** qualifying pairs. The cross-correlation is a
contemporaneous co-movement, plus a sub-cadence (HFT-scale) lag that this
~15 s-resolution feed cannot resolve and a REST-polling bot could never trade.

---

## Part 2 — basket-move → lagging-coin backtest

**Not run.** Part 1's honest gate failed. Per the pre-registered design, Part 2
is moot when Part 1 finds no capturable lag, so it was not executed and no
backtest table or null is produced. The script enforces this — `run()` returns
`verdict = "no_capturable_lag"` and exits before Part 2.

(The Part 2 machinery — basket-divergence signal, taker/maker pricing at
ask/mid, settlement on the corrected `outcome_up`, `day_blocked_kfold`
out-of-fold evaluation, window-clustered CI, and a shuffled-outcome null — is
fully implemented in `run_part2`/`_backtest_stats` and would run automatically
had the gate passed. It is left in place so a future re-run on a genuine
tick-resolution spot feed can use it directly.)

---

## VERDICT

**There is no real, capturable cross-coin lead-lag edge.**

- The four coins are strongly **contemporaneously** correlated (r ≈ 0.6–0.8 on
  15–60 s returns). They move together — but *together*, not in sequence at a
  tradable lag.
- The only lead-lag signal is **sub-cadence**: a few seconds at most, below the
  ~13–15 s resolution of the `coinbase_price` poll feed. It shows up only as a
  one-grid-step aliasing artifact and is small relative to contemporaneous
  co-movement.
- A patient API bot **cannot** trade a sub-15 s lag. Even an HFT could only
  harvest a few seconds of lead — and this dataset cannot even confirm it
  exists, let alone size it. **The lag, to the extent it exists at all, is
  HFT-only.**
- Therefore Part 2 was correctly gated off. There is no binary-market edge to
  backtest, no $/day to report.

This is consistent with the project's FINAL_REPORT: these markets are efficient
after cost, and the 16–21 % taker hurdle would in any case demand a >20 %/trade
edge — far beyond anything a ~0.1-correlation aliased artifact could deliver.

**Recommendation:** do not pursue cross-coin lead-lag on this market category.
If a genuine **tick-resolution** spot feed (real exchange trades/quotes, not a
REST poll) were collected, the question of a sub-second BTC→SOL lead could be
*re-asked* — but it would be an HFT-latency project, structurally different from
the patient-bot premise of this repo, and the cost wall would still apply.

**Status: DONE** — honest negative, caught in research before any capital.
