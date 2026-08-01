# Live-cost hypothesis campaign (2026-06-10/11) — the re-hunt under real fill physics

**Question.** The 2026-06-05 sweep (2,099 hypotheses → 1 survivor) was judged under an
IDEALIZED fill (best-ask walk, fixed 2s, 100% match). Since then we gained a live-calibrated
fill model (~44% zero-fill hazard, sampled 0.5–20s latency, guarded-band semantics), a
settlement-print probability model (`oracle_print_model.p_settle`, fit on dev ticks only),
and ~5 more days of data. Does a systematic re-hunt — old families + a new model-divergence
family — surface anything that survives **real** execution physics on a **fresh** future block?

**Answer.** One genuinely new edge region survives everything: **`psettle` cheap-side mid-ask**
(buy the book's slight underdog at ask 0.50–0.78 when the print model prices that side ≥
ask+0.10–0.15), live-model future **+$1.13..2.00/fill, all five param-variants CI-lower > 0**,
5/5 days and 4/4 coins positive, max Jaccard vs every deployed set ≤ 0.13. One strong
**replication**: the deployed `fav_disagree` region re-passed both fill models on fresh data
(and its dist≥5 widening looks better still). Everything else died or re-labeled a known edge —
which is the discipline working.

Pre-registration: `docs/research/test_ledger.md` § "Live-cost hypothesis campaign (2026-06-10)"
(LC1–LC4, written before the sweep ran). Artifacts: `data/research/hypotheses/livecost_2026-06-10/`.

---

## 1. Protocol (registered before results)

- **Future block re-based to 2026-06-05..06-09** (06-09 partial to ~05:00 UTC; 1,610 windows).
  06-01..04 was revealed by the 06-05 campaign's Phase 4 → relabeled `holdout` here. Local
  override only (`hypothesis_sweep.set_future_override` / `--future-start`); dev (05-23..27)
  identical to the prior campaign; `clean_window.py` untouched. Post-override windows:
  dev 1,920 / holdout 3,063 / future 1,610.
- **Declared prior exposure of 06-05..09** (why Jaccard adjudicates): dual-oracle lever picks
  (06-09) and OP3 used it in the det_d12 region; OP4 in the fade region (cheap ask ≤ 0.35);
  the live fill model's *cost* parameters are calibrated on 06-05..09 attempts (never sees
  outcomes). The psettle survivor's band (cheap ask **0.50–0.78**) overlaps none of those
  reveals — its future block is first-revealed here.
- **Sweep**: all 18 families, 2,423 specs (incl. new `psettle`, 324), joined-book fill at 2s,
  $10 stake, Chainlink resettle, window-clustered CIs, CPCV, DSR, latency survival — code path
  identical to 06-05 except the new family.
- **Selection (Phase 3)**: gates byte-identical to 06-05, future-blind, run via the
  `livecost_select.py` path wrapper: screened → n≥40 ∧ dev_n≥12 ∧ dev_ev>0 ∧ FULL-CI-lower>0 ∧
  CPCV≥80% ∧ latency 5s>0 ∧ 10s>0 ∧ cap10≥0.90; ≤5/family, top 24.
- **Verification (Phase 4)**: each shortlisted spec TWICE at **$5** — `v2` (idealized) and
  `live` (`fill_model_live.json` live-1, kappa 1.056, 246 attempts: sampled latency, zero-fill
  hazard, guarded band entry−0.04, ceiling = family ask_hi else entry+0.07 cap 0.92, entry_ask
  from the SIGNAL-second ladder). Live seed-0 headline + seeds 0–4 mean±sd. Extended Jaccard vs
  deployed decision sets (`rejudge_live_model.decisions_for`) + the OP4 fade region.
- **Verdict bands (LC4, fixed in advance)**: deploy-paper-candidate = future EV>0 under BOTH
  models ∧ live future CI-lower>0 ∧ live future fills ≥30 ∧ max Jaccard <0.5; duplicate-of-known
  = passes EV legs but Jaccard ≥0.5 / param-twin of deployed; reject otherwise.

## 2. Campaign funnel

| stage | n |
|---|---|
| hypotheses generated (18 families) | **2,423** (324 new `psettle`) |
| screened-in (n≥20, dev>0, full>0) | 1,773 |
| passed Phase-3 gates (future-blind) | 947 |
| shortlisted (≤5/family, top 24) | **24** (5 psettle, 5 e4, 5 zscore, 5 vol, 2 momentum, 2 det) |
| verified twice (v2 + live, $5) | 24 × 2 |
| mechanical LC4 live-band passes | 12 |
| **honest survivors** | **1 new edge region (psettle, 5 param-twins) + 1 replication/extension of deployed fav_disagree** |

Wiring sanity (LC3): the family rediscovered the OP4 fade region — 27/36 fade-band specs
(cheap, ask 0.05–0.35) passed the full Phase-3 gates, dev EV up to +$26.8/tr ($10 stake);
the fade itself re-scores **+$5.22/fill live** [+3.27,+7.29] on the fresh block (below).

## 3. Deployed-edge baselines on the SAME future block (06-05..09, $5, seed 0)

| edge | v2 future EV/fill | live future EV/fill | live seeds0–4 |
|---|---|---|---|
| det_lwd_live | +$0.52 [+0.17,+0.85] | +$0.65 [+0.21,+1.08] (76 fills) | +$0.51 ± 0.11 |
| det_d12_dual_live | +$1.00 [+0.63,+1.36] | +$0.92 [+0.40,+1.41] (82 fills) | +$0.90 ± 0.15 |
| fav_disagree | +$1.60 [+0.70,+2.55] | +$0.80 [−0.16,+1.76] (83 fills) | +$1.19 ± 0.27 |
| OP4 fade region | +$5.40 [+3.96,+6.97] | +$5.22 [+3.27,+7.29] (137 fills) | — |

## 4. Survivor table (Phase-4 reveal, future = 06-05..09, $5/trade)

EV/fill with window-clustered 90% CI; `liveN` = live future fills; `fill` = live fill rate
(all-splits); `maxJac` = max Jaccard vs {legacy det/e4/fav sets, deployed decision sets, fade
region} on live-filled slugs; `cap$50` = v2 fill-rate at $50 stake.

| id | family | live future EV [CI] | liveN | seeds0–4 | v2 future EV [CI] | fill | maxJac | cap$50 | LC4 band → honest verdict |
|---|---|---|---|---|---|---|---|---|---|
| **psettle_2246** | psettle | **+2.00 [+1.34,+2.64]** | 115 | 1.61±0.29 | +1.62 [+1.09,+2.11] | 0.56 | 0.119 | 1.00 | pass → **DEPLOY-PAPER-CANDIDATE** |
| psettle_2220 | psettle | +1.56 [+0.79,+2.33] | 86 | 1.52±0.44 | +1.59 [+0.99,+2.19] | 0.55 | 0.109 | 1.00 | pass → same region (twin) |
| psettle_2228 | psettle | +1.37 [+0.79,+1.94] | 164 | 1.19±0.33 | +1.35 [+0.89,+1.78] | 0.60 | 0.129 | 1.00 | pass → same region (twin) |
| psettle_2255 | psettle | +1.31 [+0.65,+1.95] | 125 | 1.54±0.14 | +1.50 [+1.01,+2.01] | 0.58 | 0.116 | 1.00 | pass → same region (twin) |
| psettle_2229 | psettle | +1.13 [+0.29,+1.92] | 87 | 1.21±0.14 | +1.29 [+0.69,+1.90] | 0.58 | 0.105 | 1.00 | pass → same region (twin) |
| e4_1070 | e4 | +2.21 [+1.19,+3.28] | 159 | 1.34±0.58 | +1.54 [+0.83,+2.29] | 0.61 | 0.32 | 0.99 | pass → **duplicate-of-known** (fav_disagree ext.) |
| e4_1068 | e4 | +1.64 [+0.53,+2.82] | 120 | 1.14±0.31 | +1.53 [+0.80,+2.28] | 0.49 | 0.31 | 0.99 | pass → duplicate-of-known (fav_disagree ext.) |
| e4_1076 | e4 | +1.85 [+0.47,+3.37] | 52 | 1.44±0.55 | +1.71 [+0.66,+2.80] | 0.61 | 0.41 | 0.98 | pass → duplicate-of-known (⊂ fav_disagree 98%) |
| e4_1075 | e4 | +1.08 [−0.28,+2.45] | 51 | 1.22±0.43 | +1.71 [+0.66,+2.80] | 0.62 | 0.42 | 0.98 | reject (live CI spans 0; dup anyway) |
| e4_1074 | e4 | +0.73 [−0.92,+2.52] | 44 | 1.12±0.29 | +1.68 [+0.67,+2.78] | 0.48 | 0.33 | 0.98 | reject (dup anyway) |
| vol_1616 | vol(e4) | +0.93 [+0.14,+1.73] | 201 | 0.93±0.20 | +1.19 [+0.56,+1.86] | 0.59 | 0.28 | 0.99 | pass → duplicate-of-known (vol-sliced fav_disagree) |
| vol_1624 | vol(e4) | +0.71 [+0.02,+1.44] | 208 | 0.92±0.14 | +1.03 [+0.47,+1.60] | 0.58 | 0.28 | 0.99 | pass → duplicate-of-known |
| vol_1613 | vol(e4) | −0.99 [−2.57,+0.59] | 27 | −0.04±0.59 | +0.10 [−1.23,+1.39] | 0.62 | 0.19 | 0.98 | reject |
| vol_1612 | vol(e4) | +0.81 [−0.55,+2.37] | 65 | 0.14±0.59 | +0.14 [−0.89,+1.19] | 0.61 | 0.21 | 0.99 | reject |
| vol_1621 | vol(e4) | +1.26 [−0.36,+2.87] | 36 | 1.15±0.33 | +0.39 [−0.90,+1.77] | 0.63 | 0.26 | 0.99 | reject |
| zscore_1822 | zscore | +18.42 [+0.17,+50.8] | 30 | 7.66±5.51 | +3.05 [+0.24,+6.38] | 0.47 | 0.30 | 0.92 | letter-pass → **REJECT (lottery — see §7)** |
| zscore_1821 | zscore | +1.00 [−1.45,+3.62] | 24 | 2.14±2.04 | +2.97 [+0.25,+6.21] | 0.42 | 0.26 | 0.92 | reject (twin of 1822 fails — fragility) |
| zscore_1836 | zscore | +3.23 [−1.43,+9.64] | 40 | 1.43±1.17 | +2.65 [+0.20,+5.67] | 0.49 | 0.25 | 0.94 | reject |
| zscore_1863 | zscore | +0.62 [−2.62,+3.88] | 12 | 0.31±0.71 | +1.20 [−0.45,+2.84] | 0.52 | 0.13 | 0.99 | reject |
| zscore_1864 | zscore | +1.57 [−0.86,+3.96] | 16 | 1.28±0.77 | +1.25 [−0.51,+3.02] | 0.59 | 0.15 | 0.99 | reject |
| momentum_1345 | momentum | +0.90 [+0.43,+1.30] | 38 | 1.17±0.18 | +0.67 [+0.39,+0.95] | 0.30 | 0.15 | 0.95 | letter-pass → duplicate-of-known (det overlay, §6) |
| momentum_1350 | momentum | +0.98 [+0.39,+1.46] | 23 | 1.10±0.24 | +0.79 [+0.52,+1.06] | 0.29 | 0.09 | 0.95 | reject (future fills 23 < 30) |
| det_0066 | det | +0.68 [−0.12,+1.37] | 29 | 0.51±0.23 | +0.80 [+0.31,+1.28] | 0.44 | 0.14 | 0.95 | reject |
| det_0067 | det | +0.36 [−0.44,+1.13] | 32 | 0.70±0.28 | +0.87 [+0.36,+1.33] | 0.48 | 0.14 | 0.95 | reject |

### 4b. Addendum (2026-06-11): Jaccard vs the atlas early-disagree family

The task spec also required overlap vs the EDGE-ATLAS **early-window cheap-disagree** family
(tl 450–900, book disagrees with spot, cheap-side ask 0.30–0.45 — EDGE_ATLAS_2026-06-10.md),
which the `--extended-known` sets did not include. Computed post-hoc on DECISION slug sets
(`jaccard_atlas_early_disagree.py` in the artifact dir): the atlas family touches 3,822 slugs
(58% of the whole healthy universe), and every verified spec's Jaccard vs it is **≤ 0.147**
(psettle survivors 0.06–0.12; the cl<12 EA1 refinement is lower still). Containment 67–80% is
base-rate (58% by chance, and the psettle specs fire at tl 60–420, different ticks). **No LC4
verdict changes** — the psettle region is not the atlas family in disguise; both are distinct
unharvested regions.

## 5. The new edge: `psettle` cheap-side mid-ask (model overrules the book's lean)

**Rule (psettle_2246, the recommended config):** healthy book, time_left 60–360s, side = the
NON-favourite, its YES-equivalent ask in [0.50, 0.78], and `p_settle_side`(that side) − ask ≥
0.15 → buy it at its ask, hold to resolution. The five shortlisted specs are one region (d
0.10/0.15 × tl 60–360/120–420 × |cl_dist| floor 0/5) — **one edge, five spellings; all five
pass both fill models with live CI-lower > 0.**

**What it is economically.** These are ambiguous, wide-spread, mid-window books: BOTH sides ask
≥ 0.50, the book leans one way by a few cents, and the dev-fit print model (driven by
Chainlink-distance z-scores, basis, oracle age) says the *other* side is the actual ~65–75%
favourite. Avg fill 0.52 (p10 0.49 / p90 0.61), live WR 75% (2246). This is exactly where OP2's
G1 reveal said the model beats the book — the mid-range of the probability scale (the book wins
the tails) — now monetized. It is the mid-ask sibling of the OP4 fade (which lives at ask ≤
0.35 vs a ≥0.75 favourite) and the model-gated generalization of E4-style disagreement, but on
windows the deployed strategies mostly don't touch.

**Evidence quality (2246, live model):** dev +$1.59 [+0.53,+2.58] n52 · holdout +$1.58
[+0.99,+2.16] n139 · future +$2.00 [+1.34,+2.64] n115, WR 74.8%, seeds0–4 $1.61±0.29; 5/5
future days positive ($100/$22/$28/$49/$30), 4/4 coins positive ($46–69), top-3 wins = **7%**
of future total (no jackpot dependence); both directions and both vol-regimes positive (v2
detail); v2 fills 100% at $50 stake, 97% at $100; ~30 decisions/day, live fill ~0.56 → ~17
fills/day ≈ **$25–35/day at $5 stakes**, vs det_d12_dual_live's +$0.92/fill baseline on the
same block.

**Caveats (stated, not hidden).**
- The p_settle model is fit on dev ticks → psettle's *dev* EV is model-in-sample; the
  load-bearing splits are holdout + future (both clean for the adopted artifact, both pass).
- 4.3 future days, macro-correlated coins — the CI treats windows as clusters but days are few.
- The zero-fill hazard is applied randomly; live misses are adversely selected (the known
  rejudge limitation). Fade-side experience (OP4) says treat live EV as an upper bound.
- Fresh-data wobble: 2255's holdout dipped to +$0.90 and 2229's dev is noisy (n25) — the region
  is robust, the exact d/tl knobs are not load-bearing.

**Recommendation:** add **one** paper twin (psettle_2246 rule) next to the running strategies;
do NOT touch live money. A `p_settle` evaluation at decision time needs the Chainlink poll
features at the tick (cl_dist, oracle age, basis) — the same feed det_d12_dual already consumes.

## 6. The replication + the near-misses

- **fav_disagree re-validated, and its widening looks better**: e4_1068/1070 (disagree, tl
  120–300, dist ≥5) cover **94.5%** of the deployed fav_disagree's decisions at ~2.2× volume
  and score live future +$1.64/+$2.21 (CI-lower +0.53/+1.19) where the deployed config itself
  scored +$0.80 [−0.16,+1.76] on the same block. Verdict: duplicate-of-known — but it is fresh
  OOS evidence FOR the deployed edge, and "drop the dist floor 10→5 (keep tl ≤300)" is a
  candidate parameter change worth its own small pre-registered test before touching the
  running strategy. vol_1616/1624 are the same region sliced by vol-regime (cover 76–86% of
  fav_disagree) — nothing additional.
- **momentum_1345** letter-passes but 68% of its decisions are det_lwd_live decisions and the
  momentum tag itself was an honest negative (E5: no info beyond the price mix it selects);
  future total is $34. Duplicate-of-known (det overlay), not actioned.
- **det_0066/0067** (det dist≥12, tl ≤45): positive v2, live CI spans 0 — the live hazard +
  latency eats the late-window det margin at $5. Consistent with the live det_lwd experience.

## 7. The lottery, dissected (why zscore_1822 letter-passes and is still rejected)

zscore_1822 (disagree, z≥1, tl 1–60, ud_ask ≤0.85): live future +$18.42/fill [+0.17,+50.8] —
but the future total is $552 of which **one BTC window pays $495** (a ~0.01-ask knife-catch
that settled our way; top-3 wins = 99% of total), 3/5 days positive, seeds sd $5.5, and its
param-twin zscore_1821 (no ud cap) fails outright. That is sq-shaped positive-skew lottery
variance, not establishable edge on 30 fills — and the live model fills 0.01-ask knives that
the real executor's adverse selection would punish hardest. Rejected for deployment;
unactioned. (Same family, wider tl variants 1836/1863/1864 all fail their CIs.)

## 8. Honest negatives roll-up

12/24 shortlisted failed even the mechanical live band; of the 12 letter-passers, 7 are
duplicates/extensions of deployed edges (e4×3, vol×2, momentum_1345 — plus zscore_1822
rejected as a lottery). The 947→24→2 funnel on 2,423 candidates is the expected shape: under
real fill physics, the idealized-fill graveyard stays a graveyard, the deployed disagree edge
replicates, and exactly one new region — the one built on the only genuinely new information
source (the settlement-print model) — survives.

## 9. Artifacts

- `data/research/hypotheses/livecost_2026-06-10/{campaign.json, specs.jsonl, results_0..3.jsonl,
  shortlist.jsonl, verified_v2.jsonl, verified_live.jsonl, shard_*.log, verify_*.log,
  check_rediscovery.py, baselines_newfuture.py, jaccard_atlas_early_disagree.{py,json}}`
- Code: `research/analysis/hypothesis_sweep.py` (psettle family + future override),
  `research/analysis/hypothesis_verify.py` (`--fill-model live`, default path byte-identical),
  `research/analysis/livecost_select.py` (path wrapper; gates untouched),
  `tests/research/test_hypothesis_verify_live.py` (8 synthetic-ladder smoke tests).
- Ledger: test_ledger.md § Live-cost campaign (LC1–LC4) + running-log rows (06-11).
