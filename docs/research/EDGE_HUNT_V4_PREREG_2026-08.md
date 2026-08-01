# EDGE HUNT v4 - PRE-REGISTRATION (sealed 2026-08-01, before any look at the window)

Written during gate-week prep, BEFORE any scoring of the campaign window. Nothing from
2026-07-24 00:00 UTC onward has been mined by any sweep (v3 mined through 07-23 EOD; the
dated R1/R2/R4 and rung gates read narrow registered slices only and are not sweeps).
This document is the registration; the reveal is ONE look on the reveal date. No grid
iteration, no re-cutting after seeing results. Amendments before 08-14 are allowed only
by appending a dated section here + a ledger entry, never by editing the sealed text.

## Window and apparatus (standard, unchanged from v2/v3)

- **Campaign window:** entries 2026-07-24 00:00 UTC -> 2026-08-14 23:59 UTC (3 weeks).
- **Reveal date:** 2026-08-15. One look.
- **Coins:** all 7 (btc, eth, sol, xrp, bnb, doge, hype). bnb/doge/hype were excluded
  from v3 for partial windows; for 07-24+ their collection is complete and clean. Any
  new-coin cell claim is additionally conditional on that coin's 08-14 capacity
  disposition (a dropped coin cannot carry a promote).
- **Labels:** official on-chain outcomes only (`official_outcomes.parquet`), pending
  excluded, never imputed. **Fills:** live-2 guarded fill model on recorded ladders.
- **Stats:** slug-clustered bootstrap 95% CI ([p2.5, p97.5], `score_gates` convention);
  BH-FDR 10% within each named family; seed-robustness where the fill model is used.

## V4a - ATLAS v4 (persistence + new-cell scan; the instrument that confirmed 4/4 in v2)

Same cell grid and method as V3b (`research/analysis/atlas_v3.py` lineage,
`edge_atlas/` artifacts). Splits: **dev = 07-24..08-06**, **SEALED holdout =
08-07..08-14** (one look). Selection on dev ONLY; holdout revealed once for the
dev-selected cells; CI-lo > 0 on holdout = confirm.

Registered outcomes:
1. **Persistence verdict** on the known cheap-disagree cells: the decay check for the
   live family. A negative persistence read is itself an actionable output (feeds the
   live strategies' standing stop rules, no new registration needed).
2. **New-cell claims**: any dev-selected family confirming on holdout => paper twin via
   existing engine modes only (zero-code deploy) + the standard >= 14-day
   official-settled twin gate before any live talk.

## V4b - Disagree TIMING cells (named now, from V3b's descriptive persistence read)

V3b's selection-window read showed the earlier-timing disagree cells keeping the SAME
sign but thin. The two cells are FROZEN here, before any 07-24+ data is seen:
- `a0.35-0.40 | tl450-900 | cl2-5 | DOWN`
- `a0.30-0.35 | tl450-900 | cl5-12 | DOWN`

Gate (pooled per cell over the campaign window, official labels, guarded fills):
n >= 40 AND CI-lo > 0, BH within k=2. PASS => paper twin via the existing
early-disagree engine mode + standard 14-day twin gate. FAIL => the early-timing
thread closes (it already failed one forward test on 07-17; this is its second and
last look under the standing two-strikes convention).

## NOT run (doors that stay shut)

- Broad hypothesis sweep (the v2/v3 lesson: underpowered at 3 weeks; it re-finds the
  disagree structure).
- Flow (closed permanently 07-03). Maker execution (closed 06-05, re-confirmed by the
  4b/R3 record: resting maker bled -$1.99/tr).
- g2bps grid iteration: the deployed `xh5y_g2_v1` twin's registered gates (08-07 and
  onward) are the ONLY path for the cross-horizon family; no new specs from the grid.

## Success and failure, stated in advance

- Success = at most a handful of confirmed cells feeding paper twins with standard
  gates; the realistic base case is the persistence verdict plus zero new families.
- If BOTH V4a-new-cells and V4b fail: no fresh registration on this window's data;
  the next campaign requires the next 3-week block (earliest 2026-09-05) and a new
  pre-registration. The program's default posture stays: coverage of surviving edges
  beats discovery.
