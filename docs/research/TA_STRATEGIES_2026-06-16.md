# TA Strategy Campaign — Results (2026-06-16)

Pre-registered in `docs/research/test_ledger.md` ("TA STRATEGY CAMPAIGN").
Design: `docs/superpowers/specs/2026-06-16-ta-strategy-campaign-design.md`.
Plan: `docs/superpowers/plans/2026-06-16-ta-strategy-campaign.md`.

## TL;DR

One genuinely new edge: **`ta_divergence`** (buy the spot-move-implied side, mid-window,
cheap-to-mid ask). It clears the future-**blind** select gates (CPCV 100%, latency-survival
positive at 2/5/10s, dev EV +$7.8–9.5/tr) and is **NOT a duplicate** of any deployed edge
(max Jaccard 0.17–0.21 vs the closest, `fav_disagree`). The other three roles are honest
negatives: **`ta_directional` fails** (as predicted — the book already prices spot;
its high-EV specs are low-n determinism leakage), and **`ta_filter` / `ta_regime` don't lift**
the determinism edge.

> **The live `pre_verdict` says "reject" for ALL 24 shortlisted specs — that is a thin-future-block
> artifact, not a verdict.** The live gate requires `future.n >= 30` AND future CI-lo > 0. The clean
> data was built only through 06-13, so the clean-future block (entry ≥ 06-12 11:00) has ~1.5 days
> and almost nothing reaches 30 future-split fills — the SAME gate rejects the known-good `psettle`
> edges (future EV $6–9, CI-lo > 0, futN 3–6). The honest discriminators here are the future-blind
> select gates + Jaccard dedup, both of which `ta_divergence` passes.

## Method (reused pipeline, nothing new invented)

`research.dataset.ta_features` (causal TA on the `cb_spot` tape) → `_ta_frame()` in
`hypothesis_sweep` → sweep (`--future-start 2026-06-12`, 2681 specs, 1911 screened, ~2.3 h) →
`hypothesis_select` (dev/CPCV/latency only; future context-only) → `hypothesis_verify
--fill-model live --extended-known --future-start 2026-06-12` (live fill model `live-1`, Jaccard
vs deployed decision-sets). Chainlink settlement throughout. Future block revealed once.

Pre-registered caveats (both held): (1) `cb_spot` is a ~15 s REST poll, so TA resolves at
~0.06 Hz — sub-15 s structure is invisible (TA cannot see the seconds-scale manual edge);
(2) clean-future block is thin → future EV is provisional, the forward paper run is the firm test.

## Per-family verdict

258 TA specs swept. Screened-in (cheap prefilter: n≥20, dev_ev>0, full_ev>0) and shortlist
(top-24 diversified, ≤5/family by dev/CPCV/latency score):

| family | specs | screened | in shortlist | verdict |
|---|---:|---:|---:|---|
| `ta_divergence` | 144 | 102 | **5** | **NEW edge — paper-twin candidate** |
| `ta_directional` | 36 | 19 | 0 | honest negative (predicted) |
| `ta_filter` | 54 | 27 | 0 | negative (doesn't lift determinism) |
| `ta_regime` | 24 | 14 | 0 | negative (doesn't lift determinism) |

### `ta_divergence` — the find

Thesis: the base asset moved (EMA-slope sign agrees with the 30 s return, magnitude ≥ `ret_min`
bps) but the book hasn't repriced — buy the move-implied side. Top shortlist specs (all
`slope_min=0.5`, `t 60–300 s`, ask 0.30–0.55/0.70):

| id | params | n | dev EV | CPCV | lat2/5/10 | maxJac | seed-future EV | futN |
|---|---|---:|---:|---:|---|---:|---:|---:|
| `ta_divergence_2585` | ret_min 3, ask .30–.55 | 330 | +9.50 | 100 | +2.9/+2.4/+1.8 | 0.177 | +$2.17 | 12 |
| `ta_divergence_2588` | ret_min 5, ask .30–.55 | 326 | +9.50 | 100 | +2.9/+2.4/+1.8 | 0.171 | +$2.64 | 10 |
| `ta_divergence_2586` | ret_min 3, ask .30–.70 | 495 | +7.21 | 100 | +2.6/+2.1/+1.5 | 0.205 | +$2.23 | 28 |
| `ta_divergence_2589` | ret_min 5, ask .30–.70 | 486 | +8.13 | 100 | +2.6/+2.1/+1.5 | 0.200 | +$1.38 | 31 |
| `ta_divergence_2591` | ret_min 10, ask .30–.55 | 292 | +12.19 | 100 | — | 0.165 | +$0.06 | 14 |

WR ~57% with ~2:1 payoffs (cheap-side positive skew, same risk profile as the deployed disagree
family). 4 of 5 carry positive seed-future EV (+$1.4–2.6/tr @ $10); `ret_min=10` (`2591`) is
noisy/≈0 — **prefer `ret_min` 3–5**. Jaccard breakdown (2588): closest deployed edge is
`fav_disagree` 0.17, then `det_d12_wide` 0.15, `det_d12_dual` 0.15 — i.e. it shares ≤17% of windows
with anything live. It is a distinct trigger (TA EMA-slope + 30 s spot return vs the deployed
edges' `dist_strike`/oracle disagreement).

Atlas placement (qualitative, not re-run — the direct Jaccard-vs-deployed test above is the
stronger dedup): `ta_divergence` sits in the early/mid-window cheap-side region the EDGE_ATLAS
already flagged as the positive frontier ([[early-disagree-family-and-oracle-night]]), but keyed
off a TA signal rather than `cl_dist` — which is why its Jaccard with `early_disagree`/`fav_disagree`
is low rather than ~1.

### `ta_directional` — honest negative (as predicted)

Some specs screened in with high dev EV (`2429`: n=49, WR 71%, dev +$10.8, full +$5.9; `2444`:
n=23, WR 78%), but these are **low-n** and collapse at scale (`2441`: n=272 → full EV +$1.57).
None reached the shortlist. The high-EV-low-n pattern is the **determinism/favourite signal
leaking in** — a strong EMA uptrend correlates with spot sitting far above the strike (the
determinism setup), so "directional TA" re-labels an edge we already harvest rather than
forecasting BTC. Confirms the project's standing result: directional prediction loses to the book.

### `ta_filter` / `ta_regime` — negatives

Both gate the proven determinism edge (by TA regime label / ATR band). Best specs keep determinism's
high WR (84–90%) but full EV drops to +$0.2–0.8/tr (vs determinism's own baseline) — the gate
removes volume without lifting per-trade edge. Neither reached the shortlist. A TA regime/ATR
filter does not improve determinism on this data.

## Deployment status (engine reality)

`ta_divergence` needs `ta_ema_slope` + `ta_ret_30s` computed on the **live** spot tape at tick
time. These are NOT in the live engine tick dtype today (the engine carries `spot_move_30s` and
`spot_vel_*` but not an EMA-30 slope). Per the campaign's flag-don't-deploy rule, `ta_divergence`
is therefore **FLAGGED for a separate engine-wiring plan**, not deployed as a drop-in existing-mode
twin this round.

Two engine-wiring paths for the follow-up plan:
1. **Faithful:** add an EMA-30-slope + 30 s-return feature to `SpotPriceCache` / the live tick,
   with a research-parity pin (the `ta_features` arithmetic) — a new `divergence`-style mode keyed
   on the TA trigger.
2. **Approximation (cheaper):** the engine already has `spot_move_30s` (≈ `ta_ret_30s`) and
   `spot_vel_10s_bps`; a twin using `spot_move_30s ≥ ret_min` with `spot_vel` as the slope proxy
   approximates `ta_divergence` with zero new feature code. Worth A/B-ing against the faithful
   version before committing.

Deploy gate for any path (unchanged): paper twin (`live:false`) → ≥7 clean forward days realized
EV/fill CI-lower > 0 before any live talk.

## Recommendation

1. **`ta_divergence` (ret_min 3–5, ask 0.30–0.55, t 60–300 s) → engine-wiring follow-up plan +
   paper twin.** It is the one new, non-duplicate, future-blind-passing edge from this campaign.
2. **Drop `ta_directional` / `ta_filter` / `ta_regime`** — honest negatives, carry-forward (don't
   re-run): directional TA re-derives determinism; TA regime/ATR filters don't lift it.
3. **Re-score `ta_divergence` on the live verdict gate once ~7 clean days exist** (~06-19/20), when
   `future.n ≥ 30` becomes reachable — same schedule as the deferred `psettle`/`det` re-validation.
