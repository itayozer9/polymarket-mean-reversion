# Lead D — Maker-Execution Reframe

**Date:** 2026-05-22
**Branch:** `edge-leads`
**Code:** `research/analysis/maker_reframe.py`
**Data:** `data/research/ticks_15m.parquet` (corrected dataset). Dev split **2026-05-15..2026-05-20** (6 UTC days, 1,676 windows); the **2026-05-21..2026-05-22 hold-out** is sealed (asserted in `load_dev_holdout`).

---

## The hypothesis

Every prior result was killed by the **16-21% taker round-trip cost** (fee `0.07*p*(1-p)` on both legs + crossing the spread). A **maker** (resting limit orders) pays 0 fee and does not cross the spread. So a signal with a small but genuinely **positive gross edge** — dead as a taker — could be alive as a maker. This lead asks, honestly, whether maker execution turns anything profitable.

The logic gate: **maker execution removes COST, it cannot create GROSS edge.** If the gross edge (`realized cheap_won - entry price`, before any cost) is ~0 or negative everywhere, no fee saving can rescue it. So Part 1 — the positive-gross-edge search — gates everything.

## Critical data discipline — two artifacts avoided

**(1) The decided-market book artifact.** `phase4_forensics.md` proved the entry-candidate table's cheap-side columns are *corrupted on decided-market books*: when a window resolves, both mids collapse toward 0, the `cheap_side = lower-ask side` rule breaks, and a worthless **losing** side gets mislabelled with `cheap_mid ~ 0` and a spuriously high `cheap_won`. Run naively, the `cheap_mid in (0, 0.01]` bucket shows a fantasy **+30c gross edge** — a side priced 0.0015 'winning' 30% of the time. That is 100% the artifact (those rows have a median `cheap_spread` of **-0.31**, a crossed/inverted book, and cluster at `seconds_into_window ~800`, near resolution). This script therefore works off the **genuine two-sided book** in `ticks_15m.parquet` with a hard book-health guard: both sides priced in (0.001, 0.999), positive bids below asks, and complement-consistent (`yes_ask + no_bid` within 6c of 1). 92.1% of dev ticks pass; the 7.9% that fail are exactly the decided-market rows, and removing them removes the entire fantasy edge.

**(2) The fantasy maker fill (Phase 4's mistake).** A realistic maker model is enforced: a resting buy limit at price `L` fills **only when a strictly-later tick shows the side's best bid <= L** — i.e. the market actually traded down to/through the level (someone sold into it). If the market never reaches `L` within the window, the order **does not fill — that trade simply does not happen** and earns nothing. Maker fills are therefore **adverse-selection-biased by construction**: you are filled precisely when the side is getting cheaper. A maker exit (resting sell limit) fills only on a genuine later trade-up to it; otherwise the position is held to window resolution and settles on the true `outcome_up`.

---

## Part 1 — the positive-gross-edge search

The gross edge is `realized cheap_won - cheap_mid` — the cheap side's realized win rate minus its price, **before any cost**. The cross-section is de-biased to **one observation per (window, 60s time-slice)** — Phase 0 found ~87% of ticks are stale, so tick-pooling over-weights long-lingering quotes. All CIs are 90% window-clustered bootstraps (n=3000, groups = `slug`).

**De-biased pooled gross edge: -0.0165** (90% CI [-0.0254, -0.0074]) — i.e. the cheap side, on a genuine two-sided book, realizes about 1.7c BELOW its mid, before any cost. (For comparison: the **tick-pooled** number on the raw table is `+1.4c` — a stale-tick weighting artifact; and the raw table's `cheap_mid in (0,0.01]` cell shows `+30c` — the decided-market artifact. The honest de-biased, healthy-book number is the one above.)

### Gross edge by cheap_mid price bucket (de-biased, healthy book)

| cheap_mid bucket | n (de-biased) | windows | mean mid | realized | gross edge | 90% CI |
|---|---|---|---|---|---|---|
| (-0.001, 0.02] | 1,347 | 624 | 0.0106 | 0.0067 | **-0.0042** | [-0.0083, +0.0011] |
| (0.02, 0.04] | 1,179 | 754 | 0.0304 | 0.0153 | **-0.0153** | [-0.0220, -0.0075] |
| (0.04, 0.06] | 893 | 664 | 0.0506 | 0.0426 | **-0.0088** | [-0.0231, +0.0077] |
| (0.06, 0.08] | 837 | 606 | 0.0696 | 0.0693 | **-0.0006** | [-0.0196, +0.0219] |
| (0.08, 0.1] | 714 | 568 | 0.0912 | 0.0644 | **-0.0270** | [-0.0448, -0.0067] |
| (0.1, 0.13] | 1,027 | 706 | 0.1162 | 0.1003 | **-0.0162** | [-0.0362, +0.0052] |
| (0.13, 0.17] | 1,391 | 882 | 0.1506 | 0.1121 | **-0.0386** | [-0.0569, -0.0193] |
| (0.17, 0.22] | 1,798 | 1,014 | 0.1964 | 0.1696 | **-0.0266** | [-0.0471, -0.0052] |
| (0.22, 0.3] | 3,027 | 1,323 | 0.2617 | 0.2293 | **-0.0326** | [-0.0521, -0.0133] |
| (0.3, 0.4] | 4,573 | 1,479 | 0.3519 | 0.3271 | **-0.0246** | [-0.0418, -0.0075] |
| (0.4, 0.51] | 7,090 | 1,672 | 0.4582 | 0.4553 | **-0.0029** | [-0.0139, +0.0082] |

**Every price bucket is negative or straddles zero — not one has a CI excluding zero on the positive side.** The extreme cheap tail (`cheap_mid < 0.02`) — Phase 2's claimed +6.9c longshot residual — is here near zero with a CI straddling zero: that residual was itself decided-market contamination, which the healthy-book guard removes.

### Gross edge by drop / time-left / symbol

`cheap_drop_30s` is the 30-second odds drop in **percent** (0-100). The panic-overshoot hypothesis (H2) predicts a positive gross edge after a steep drop.

| cheap_drop_30s (%) | n | gross edge | 90% CI |
|---|---|---|---|
| (-0.002, 0.001] | 9,651 | -0.0178 | [-0.0281, -0.0076] |
| (0.001, 10.0] | 4,047 | -0.0074 | [-0.0229, +0.0082] |
| (10.0, 25.0] | 5,008 | -0.0246 | [-0.0369, -0.0117] |
| (25.0, 50.0] | 3,882 | -0.0169 | [-0.0278, -0.0048] |
| (50.0, 100.001] | 1,288 | -0.0063 | [-0.0185, +0.0067] |

| time_left_sec | n | gross edge | 90% CI |
|---|---|---|---|
| (-0.001, 120.0] | 2,445 | -0.0053 | [-0.0178, +0.0075] |
| (120.0, 300.0] | 4,732 | -0.0215 | [-0.0331, -0.0092] |
| (300.0, 500.0] | 5,002 | -0.0267 | [-0.0403, -0.0133] |
| (500.0, 700.0] | 5,009 | -0.0269 | [-0.0413, -0.0124] |
| (700.0, 901.0] | 6,688 | -0.0021 | [-0.0150, +0.0106] |

| symbol | n | gross edge | 90% CI |
|---|---|---|---|
| btc | 5,981 | -0.0196 | [-0.0375, -0.0016] |
| eth | 5,935 | -0.0070 | [-0.0254, +0.0121] |
| sol | 5,995 | -0.0199 | [-0.0367, -0.0028] |
| xrp | 5,965 | -0.0201 | [-0.0368, -0.0026] |

No drop bucket, no time-left bucket, no symbol shows a positive gross edge — every cell is negative or straddles zero. The steepest-drop cells (the H2 panic-overshoot candidate) are negative or ~0.

### Dev-internal CV — early (May 15-17) vs late (May 18-20)

A cell qualifies as a real candidate only if its gross edge is positive with a CI excluding zero in **both** dev halves.

| cheap_mid bucket | early gross (CI) | late gross (CI) | both halves positive? |
|---|---|---|---|
| (-0.001, 0.04] | -0.0066 [-0.0164,+0.0066] | -0.0107 [-0.0153,-0.0045] | no |
| (0.04, 0.08] | -0.0249 [-0.0411,-0.0042] | +0.0045 [-0.0138,+0.0251] | no |
| (0.08, 0.13] | -0.0106 [-0.0409,+0.0212] | -0.0248 [-0.0434,-0.0044] | no |
| (0.13, 0.22] | -0.0041 [-0.0352,+0.0288] | -0.0443 [-0.0642,-0.0235] | no |
| (0.22, 0.35] | -0.0141 [-0.0444,+0.0174] | -0.0387 [-0.0590,-0.0174] | no |
| (0.35, 0.51] | +0.0043 [-0.0152,+0.0235] | -0.0138 [-0.0263,-0.0008] | no |

**No cheap_mid bucket is positive with a CI excluding zero in both halves.** The handful of near-zero positives flip sign or lose CI-significance across the early/late split — noise, not edge. There is no CV-stable positive-gross cell anywhere.

---

## Part 2 — the realistic-maker backtest

**No cell survived Part 1**, so there is nothing a maker could rescue. For completeness and honesty the realistic-maker backtest is still run on the **least-bad** cheap_mid cell (highest pooled gross edge): **[0.06, 0.08)**. Post a resting limit buy at the side's bid; fill only on a genuine later trade-through; +100% profit-target exit (resting limit sell, fills only on a genuine later trade-up) else settle on the true `outcome_up`; 0 fee. Compared to the same as a taker.

### Dev split

| execution | candidates | filled | fill rate | win rate | PnL/trade | 90% CI | trades/day | $/day | resolution-loss rate |
|---|---|---|---|---|---|---|---|---|---|
| maker | 1,195 | 1,183 | 0.99 | 0.062 | $-2.1282 | [$-2.8915, $-1.3118] | 197.2 | $-419.60 | 0.990 |
| taker | 1,195 | 1,195 | 1.00 | 0.065 | $-2.2169 | [$-3.0429, $-1.3806] | 199.2 | $-441.53 | 0.990 |

The resting buy limit is posted at the side's *current bid* and fills **99%** of candidate windows — a high rate, because an at-the-bid limit on a cheap side that mostly drifts further down gets traded through almost always. That is precisely the adverse-selection problem: the fill is not a favour, it is the market handing you a side that is on its way to losing. Net PnL/trade **$-2.1282** (90% CI [$-2.8915, $-1.3118]), **$-419.60/day**, resolution-loss rate **99%**. The taker on the same cell is **$-2.2169/trade**. Maker beats taker (no fee, no spread crossing) but **both are negative** — removing cost does not lift a negative-gross signal above zero.

### Sealed hold-out (May 21-22)

**Not consulted.** No candidate survived the dev-split gross-edge search; a signal that has no positive gross edge on dev must not consume the hold-out. It stays sealed.

---

## VERDICT

**Maker execution does NOT rescue any signal — because there is no gross edge for it to rescue.**

On the genuine, healthy two-sided book, de-biased to one observation per window-slice, the **gross edge (`cheap_won - cheap_mid`, before any cost) is -0.0165 pooled** and is **negative or zero in every cell** — every price bucket, every drop bucket, every time-left bucket, every symbol, and in both halves of the dev-internal CV. Not one cell is positive with a CI excluding zero, and the dev-internal CV confirms it: nothing is positive in both the early and late halves.

Maker execution attacks the **cost wall** — the 16-21% taker round-trip. That is the right attack *if* a positive gross edge is hiding under the cost. But the gross edge is ~0 to slightly negative everywhere. A maker pays no fee and crosses no spread, yet a 0-fee bet on a coin-flip-or-worse is still a coin-flip-or-worse. There is nothing under the cost wall. The realistic-maker backtest confirms it operationally: on the least-bad cell the maker earns **$-2.1282/trade** (CI [$-2.8915, $-1.3118]) — negative, with a 99% resolution-loss rate. The realistic fill model also exposes a second problem even if a gross edge DID exist: the maker only gets filled when the market trades down to its level — i.e. precisely on the windows where the side is getting cheaper and (on this calibrated market) more likely to lose. Maker fills are adverse-selected.

This agrees with the corrected-data Phase 2 re-run (`PHASE2_RERUN_VERDICT.md`: the cheap side is calibrated, no tradeable mispricing) and with the Phase 4 forensics (the only large positive numbers were data artifacts). The honest answer to 'does maker execution rescue a strategy' is **no — the gross edge is ~0 everywhere, so maker cannot save it.** There is no realistic $/day to report because there is no edge to size.

**Status: DONE — clean negative.** No CV-stable positive-gross cell; maker execution cannot rescue a non-existent edge; the sealed hold-out was not consulted and stays sealed. A skeptical reader should note this is the third independent confirmation (Phase 2 calibration, Phase 4 forensics, Lead D) that the 15m cheap-side market is calibrated and carries no harvestable edge.
