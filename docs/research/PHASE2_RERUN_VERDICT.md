# Phase 2 Edge-Discovery — Re-run on Corrected Data: Consolidated Verdict

**Date:** 2026-05-22
**Status:** RE-RUN ON CORRECTED DATA (real Polymarket outcomes) — supersedes
the earlier corrupt-label results.

---

## Why this document exists

Phase 2's first edge-discovery pass ran on **corrupt outcome labels**. A strike
bug (`docs/research/phase0_audit.md`, Task 8c) set the wrong strike on a large
fraction of windows, which flipped the resolved Up/Down outcome on **~31 % of
windows**. Every Phase 2 finding built on those labels — the calibration edge,
the conditioned edge map, the fair-value triangulation, and the divergence
backtest — was therefore invalid.

The dataset has since been rebuilt correctly. `data/research/windows.parquet`,
`ticks_15m.parquet` and `entry_candidates_15m.parquet` now carry the **true
Polymarket-resolved outcomes** and corrected strikes / `move_pct` /
`proximity_pct` / `sigma_proximity`. All four Phase 2 analysis scripts were
re-run on the corrected data; this document is the consolidated, honest verdict.

**Discipline maintained.** Dev split = May 15–20 2026 (6 UTC days, 1,676
windows). The sealed hold-out (May 21–22) was **not fit, not selected on, and
not even loaded** — every script asserts it, and all guards fired. All CIs are
90 % window-clustered bootstraps (groups = `slug`; the unit is the window, not
the tick). The empirical fair-value surface is day-blocked: fit on training
days, scored on held-out test days only.

---

## THE HEADLINE

**On correct labels there is NO real, cost-surviving, out-of-fold,
dev-internally-cross-validated edge** — not in the calibration, not in the
conditioned edge map, and not in the divergence signal.

Every large positive number from the corrupt-label Phase 2 (a "+12–13 c
cheap-side calibration edge", a "real slow-book lead-lag divergence edge worth
~+$2/trade over baseline") was an **artifact of the wrong outcome labels** plus,
for the divergence signal, a t=0 `move_pct` tautology that the corrected strike
removes. None of it survives.

This is a valid and important result. The project has now been burned twice by
artifacts; the corrected-data answer is a clean negative.

---

## 1. De-biased calibration (`calibration_debiased.py`)

De-biased cross-section: one observation per (window, time-slice), 11,700 obs,
1,676 windows, dev split only.

**The cheap side is calibrated.** On corrected labels the de-biased pooled
cheap-side gap is **−0.91 c** (`cheap_mid`) / **−1.61 c** (`cheap_ask`, the
taker entry price) — essentially zero, slightly negative. The corrupt-label run
reported a large positive gap (~+12 c de-biased, ~+13 c tick-pooled); that is
withdrawn.

De-biased reliability table (`cheap_mid` bin → realized, 90 % CI):

| cheap_mid bin | mean pred | realized | 90 % CI | gap |
|---|---|---|---|---|
| [0.00, 0.07) | 0.0299 | 0.0986 | [0.0851, 0.1126] | **+6.87 c** |
| [0.07, 0.13) | 0.1000 | 0.0753 | [0.0596, 0.0918] | −2.47 c |
| [0.13, 0.20) | 0.1662 | 0.1386 | [0.1180, 0.1594] | −2.76 c |
| [0.20, 0.27) | 0.2338 | 0.2019 | [0.1794, 0.2246] | −3.19 c |
| [0.27, 0.33) | 0.3010 | 0.2744 | [0.2513, 0.2976] | −2.66 c |
| [0.33, 0.40) | 0.3662 | 0.3417 | [0.3188, 0.3638] | −2.45 c |
| [0.40, 0.47) | 0.4347 | 0.4258 | [0.4074, 0.4442] | −0.89 c |
| [0.47, 0.53) | 0.4845 | 0.4873 | [0.4625, 0.5119] | +0.29 c |

The only bin with a CI-separated positive gap is the **extreme cheap tail**,
`cheap_mid < 0.07` (a side priced ~3 c, realizing ~10 c, +6.9 c). That is a
residual longshot effect on a tiny slice of the curve, well below the ~16–21 %
taker round-trip cost, and not tradeable. Every other bin is flat or slightly
negative. The tick-weighting / lingering bias is moot here — the tick-pooled
gap is also ~0 c, so there is no apparent edge for de-biasing to inflate.

**Is the cheap side still mispriced? No.** The cheap side is not systematically
under-priced on real Polymarket outcomes. **No calibration edge.**

---

## 2. Conditioned edge map (`edge_map.py`)

De-biased cross-section, edge = `cheap_won − cheap_mid`, both-halves
dev-internal CV (early May 15–17 vs late May 18–20; a cell qualifies only if its
90 % CI excludes zero in the same direction with ≥30 windows on **both** halves).

**Overall edge = −0.66 c** — consistent with the calibration: ~0 overall.

**Do any cells qualify? 3 of 42** clear both-halves CV. But:

- `sigma_proximity = 0.5–1`: edge **−3.6 c** (a qualifying *negative* cell).
- `sigma_proximity = 2–4`: edge **+5.7 c** (early +7.1 c, late +5.3 c).
- `sigma_proximity = 2–4 × cheap_drop_30s = 0`: edge **+4.4 c**.

The two positive qualifying cells sit at **HIGH sigma-proximity** — i.e. the
spot has already moved well away from the strike. A positive
`cheap_won − cheap_mid` there means the cheap side (the side the spot has moved
*against*) wins more often than its price implies. That is **not a low-sigma
panic-overshoot edge**; it is the favourite/longshot mispricing — the same
effect Task 8's fair-value decomposition isolates as the
"cheap-side-actually-favoured" bin. It is a *gross*, conditioned number. Task 8
shows it does **not survive cost** (negative net of taker, ~0 net of maker), and
the divergence backtest — which targets exactly that signal out-of-fold — shows
it does not generalize to a profitable rule.

**No tradeable conditioned cheap-side edge** on correct labels.

---

## 3. Fair-value diagnostic (`fair_value_tri.py`)

The decisive real-vs-artifact diagnostic, on the de-biased cross-section.

**σ-proximity is still broken.** Bachelier on the trailing `realized_vol` says a
`sigma > 4` market's favourite wins ~100.00 %; it actually wins **83.3 %** (90 %
CI [0.805, 0.859]). The favourite win rate rises only gently and monotonically
with σ-proximity (0.633 at σ<0.5 → 0.833 at σ>4). `realized_vol` under-states
true window vol by a factor ~1.28 (median trailing/whole-window ratio 0.78), and
even fully correcting that leaves a driftless-Gaussian model that ignores
15-minute crypto's mean-reversion and jumps. **σ-proximity carries weak ordinal
information but is not a usable probability of decided-ness — it must be dropped
as a decided-ness filter.** (This is less catastrophic than the corrupt-label
run claimed — 83 % not "~64 %" — but the conclusion is unchanged.)

**Real-vs-artifact decomposition (out-of-fold empirical fair value).** The
cheap-side headline edge is **−0.66 c** — and it is a mix of opposing pieces
that nearly cancel:

| empirical decided-ness bin | weight | naive edge | contribution |
|---|---|---|---|
| near-decided-against (fair < 0.10) | 12.5 % | −2.8 c | −0.35 c |
| underdog (0.10–0.25) | 20.5 % | −1.6 c | −0.33 c |
| long-shot-contested (0.25–0.40) | 27.8 % | −2.1 c | −0.58 c |
| genuinely-contested (0.40–0.55) | 28.5 % | −1.0 c | −0.28 c |
| cheap-side-actually-favoured (0.55–1.00) | 7.4 % | +12.8 c | +0.94 c |

Four of the five bins are **negative** — the cheap buyer loses slightly in the
decided, underdog, longshot, and contested bins alike. The only positive bin is
the small (7.4 % of obs) cheap-side-actually-favoured tail, where a side priced
~0.36 is in truth a ~0.68 favourite. That single bin is the whole +0.9 c, and it
nets to ~0 against the negative bins.

**Net-of-cost, the genuinely-contested band (empirical fair 0.25–0.55, 6,581
obs): −1.6 c maker, −4.6 c taker (CI [−5.9, −3.2] c).** Both negative. There is
no contested-market under-pricing to harvest on correct labels. The earlier
corrupt-label "+12 c headline, partially real" verdict is **withdrawn**.

---

## 4. Divergence backtest (`divergence_backtest.py`)

The decisive out-of-fold test. Day-blocked 5-fold and leave-one-day-out; the
empirical fair-value surface is fit on training days and scored on test days
only. The candidate signal: buy a side when its spot-implied empirical fair
value materially exceeds its market ask (a lead-lag bet).

**The corrected strike removes the old t=0 tautology.** On the corrupt labels
the strike was mis-set, so `move_pct` at window open was non-zero and the
divergence signal was partly tautological at t=0. With the corrected strike,
`move_pct` at window open is **~0 by construction** (strike = spot at open;
verified: median |move_pct| = 0.0 at t≤5 s). The signal now genuinely has to
predict.

**It does not.** Out-of-fold results on corrected data:

- **Headline config (T=0.2, +100 % target, taker): −$0.78/trade** (90 % CI
  [−$1.30, −$0.33], 1,257 trades, win rate 37.9 %, **green-day fraction 0.00** —
  all 6 dev days red).
- **Taker, every threshold (0.05–0.20), every profit target: negative**
  (−$0.78 to −$1.24/trade). Leave-one-day-out agrees (−$0.84 to −$1.26/trade).
- **Trade frequency:** very high — 75 % of windows at T=0.2, ~100 % at T=0.05.
  It is not a rare, selective signal; it fires almost always and loses.
- **Maker, headline config: +$0.12/trade**, but the CI [−$0.41, +$0.60]
  straddles zero, the maker path ignores the fill-probability haircut, and the
  price-only maker baseline is just as good (+$0.27) — so the maker number is
  not evidence of an edge.

**Null tests / skeptic checks.**

- **Decisive comparison — divergence vs price-matched cheap-side baseline:**
  the divergence signal **loses to the baseline at every threshold**. Best-case
  divergence-minus-price-only delta over the whole taker grid is **−$0.16/trade**
  (still negative). The surface subtracts value.
- **Matched-ask-band test (confound-free):** restricting both rules to a fixed
  entry-ask band [0.40, 0.55], the divergence signal's side wins **46.4 %** vs
  the price-only baseline's **49.1 %** — a **−2.7 percentage-point** gap at
  identical capital. At a matched price the surface picks the *losing* side more
  often than chance.
- **Flat-surface null:** −$1.89/trade — the real signal (−$0.78) is not
  meaningfully better; there is no surface contribution to detect.
- **Shuffled-outcome null:** +$9.26/trade. This does **not** indicate leakage —
  it is a known artifact of the null itself: a noise surface produces extreme
  cell values and clears the divergence threshold on the very cheapest sides
  (mean ask ~0.29), harvesting the longshot OVER-pricing tail. It is reported
  for completeness; the flat-surface null and matched-band test are the clean
  controls, and both say no edge.
- **Look-ahead:** structurally prevented — train/test days and windows asserted
  disjoint on every fold; entry features read from the entry tick; exits scan
  only strictly-later ticks.

**Did the edge survive correct labels? No — it collapsed entirely.** The
corrupt-label "real slow-book lead-lag edge" was an artifact of the strike bug
(wrong labels + a t=0 tautology).

---

## Consolidated verdict

**Is there a real, cost-surviving, out-of-fold, dev-internally-cross-validated
edge on correct data — in the calibration, the conditioned edge map, or the
divergence signal?**

**NO.**

- **Calibration:** the cheap side is calibrated. De-biased pooled gap −0.91 c
  (`cheap_mid`) / −1.61 c (`cheap_ask`). No tradeable mispricing.
- **Conditioned edge map:** overall edge −0.66 c. Three cells clear both-halves
  CV, but the positive ones are a high-σ favourite/longshot effect that is
  gross-only and does not survive cost.
- **Fair-value:** the headline ~0 c is a near-cancellation of negative
  decided/underdog/contested bins against one small positive favoured-side bin.
  The genuinely-contested band is −1.6 c maker / −4.6 c taker — negative.
- **Divergence:** −$0.78/trade taker out-of-fold (green-day fraction 0.00),
  loses to a price-matched baseline at every threshold, picks the wrong side at
  matched prices, indistinguishable from the flat-surface null.

**How big? How often?** There is nothing to size. The divergence signal trades
constantly (75–100 % of windows) and loses ~$0.8–1.2/trade as a taker; the
calibration and edge-map "edges" are sub-cent-to-a-few-cents *gross* and
negative or cost-killed. Under **taker** execution everything is clearly
negative. Under **maker** execution the numbers drift up to roughly break-even
(divergence +$0.12/trade, CI straddling zero; calibration ~0 c) — but maker
ignores the fill-probability haircut and a price-only maker baseline matches it,
so even the maker case is "no edge", not "small edge".

**The sealed hold-out (May 21–22) is not warranted.** A signal that already
fails out-of-fold on the dev split should not consume the hold-out. The hold-out
stays sealed.

---

## Status of hypotheses H1–H11 (given correct data)

`docs/research/market_hypotheses.md`. Phase 2's four scripts test the
calibration / cross-section / fair-value / divergence questions; they bear
directly on H2, H6, H8, H11 and partly on H1, H3, H4, H5, H7. H9 and H10 are
out of scope for these four scripts.

| # | Hypothesis | Status on corrected data |
|---|---|---|
| **H1** | The loss tail is forced resolution | **Not tested by these four scripts.** Needs the resolve-against event study. Open. |
| **H2** | Noise-drop vs signal-drop | **Not supported.** In the edge map, `cheap_drop_30s` does not order the cheap-side edge (no-drop −0.7 c, >25 % drop +0.3 c); within the low-σ row the drop buckets do not cleanly rank. A visible odds drop, on its own, is not where any edge lives. A full H2 test still needs the spot-move-split drop event study, but the conditioned map gives it no support. |
| **H3** | Trending regimes kill mean-reversion | **Not directly tested.** Untouched by these scripts. Open. |
| **H4** | Edge and losses both scale with vol | **No edge to scale.** The `realized_vol` tertile in the edge map shows LOW −0.5 c / MED +0.4 c / HIGH −1.9 c — no positive edge in any vol regime. There is no edge whose size tracks vol. Effectively rejected as a route to a tradeable edge. |
| **H5** | Big reversions are real, rare, fat-tailed | **Not tested by these scripts** (needs the bounce atlas / forward-return distribution). The empirical fair-value surface confirms extreme moves resolve as expected, but says nothing about a tradeable reversion tail. Open. |
| **H6** | Coins are not interchangeable | **Weak / not material.** Per-symbol edge in the conditioned map: btc −1.2 c, eth +0.8 c, sol −1.2 c, xrp −1.0 c — spread only ~2 c, and all near zero. Coins differ slightly but none carries a tradeable edge; the corrupt-label premise of large per-coin edge differences is not supported. |
| **H7** | Cross-coin co-movement flags a macro move | **Not tested by these scripts.** Needs the cross-coin dip-count conditioning. Open. |
| **H8** | Deep dips are roughly coin-flips — not the edge | **Confirmed in spirit, but the moderate band is also not the edge.** The de-biased calibration shows deep dips are NOT coin-flips (a side priced ~0.10 resolves ~0.10 — calibrated, not a flip) and carry no edge; the moderate-underdog band (0.20–0.40) is also calibrated-to-slightly-negative. H8 was right that deep dips are not the edge — but corrected data goes further: the moderate band is not the edge either. There is no entry-price bucket with a tradeable edge. |
| **H9** | A fixed profit target is the wrong exit | **Not tested by these scripts** (needs the bounce atlas / exit-primitive comparison). Open. |
| **H10** | 5-minute markets are structurally dead | **Not tested here** (15m only). Open; 5m remains the negative control. |
| **H11** | The losses are a genuine absence of edge, not an artifact | **STRONGLY CONFIRMED.** This is the central result of the corrected-data re-run. With the look-ahead-free, label-correct, out-of-fold, dev-internally-cross-validated machinery, there is no edge in calibration, conditioning, fair-value, or the divergence signal. The bot's historical losses are a genuine absence of edge — there is no leak or mislabel "hiding" a real edge. (Ironically the *previous* Phase 2 pass, which seemed to find an edge, was itself the artifact H11 warned about — a label artifact, now removed.) |

**Bottom line for the hypotheses:** the four Phase 2 scripts close out H2, H6,
H8 as "no tradeable edge here", confirm H11 decisively, and rule out H4 as a
route to an edge. H1, H3, H5, H7, H9, H10 are not decided by these scripts and
remain open — but they are now being asked against a confirmed-flat baseline:
any future "edge" must clear the same out-of-fold, cost-aware, label-correct bar
that calibration / conditioning / divergence just failed.

---

## What changed vs the corrupt-label Phase 2

| Finding | Corrupt labels (invalid) | Corrected labels (this re-run) |
|---|---|---|
| De-biased cheap-side calibration gap | ~+12 c (claimed real) | **−0.91 c** (calibrated) |
| Conditioned edge map, overall | ~+12 c, "uniform across σ" | **−0.66 c**, ~0 |
| Fair-value contested band, net of cost | "+ small, maker-only" | **−1.6 c maker, −4.6 c taker** |
| Divergence signal, taker OOF | "+$1–2/trade, beats baseline, REAL EDGE" | **−$0.78/trade, loses to baseline, NO EDGE** |
| Matched-ask-band win-rate gap | large positive | **−2.7 pts** (picks wrong side) |
| σ-proximity | "broken (favourite ~64 % at σ>4)" | still broken (favourite **83 %** at σ>4) |

The direction of the headline conclusion flipped completely. The corrected-data
answer is a clean, honest negative.
