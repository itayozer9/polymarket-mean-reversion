# EDGE ATLAS — 2026-06-10

The complete empirical map of where the Polymarket 15m Up/Down order book is
miscalibrated beyond real trading costs, cross-referenced against what the
deployed/candidate strategies already harvest. Pre-registered in
`docs/research/test_ledger.md` (EA1) **before** any future-column number was computed.
Tooling: `research/analysis/edge_atlas.py` (+ unit tests
`tests/research/test_edge_atlas.py`); artifact: `data/research/edge_atlas/`.

## TL;DR

- **1,247 of 1,512 possible cells** are non-empty; 767 tested (n_dev≥10); 445 had n_dev≥40.
- After the pre-registered gates (dev CI-lower>0 ∧ holdout>0 ∧ n_dev≥40 ∧ BH-FDR-10%):
  **24 positive + 81 negative candidates**. Future revealed once, for candidates only:
  **15 positives confirmed (11 strong)**, **61 negatives confirmed (40 strong)**.
- **THE new edge: early-window cheap-side disagree** — first ~2–7 min of the window, the
  book favourite disagrees with Coinbase spot, buy the spot side at 0.30–0.45. Family of
  6 FDR-surviving cells, all future-strong, **all unharvested** (max coverage 0.27–0.45):
  pooled future **+20.9% per $1 [+15.2,+26.4]**, n=1,279 windows, 9/9 future days ≥ 0,
  all 4 coins positive. This is fav_disagree's mechanism (E4) extended to a region
  fav_disagree never trades (it gates tl 120–360s; this fires at tl 450–900s).
- **Fade zones confirmed at scale**: (1) cheap consistent longshots against an
  established Chainlink lead (−15…−50% net, the favourite-longshot bias's other face);
  (2) buying a favourite that DISAGREES with spot (−13…−31% everywhere); (3) mid-strong
  favourites (0.70–0.80) when the strike is still undecided, |cl_dist|<2, tl 300–450
  (−20%) — the OP4 fade thesis seen from the favourite side.
- The book's miscalibration has ONE sign everywhere: **too slow toward spot/oracle
  reality** — overpriced favourites where the oracle says coin-flip, underpriced
  spot-side where the book hasn't repriced an early move, underpriced deep favourites
  only when the oracle lead is large (≥12 bps).

## Method (pre-registered; see ledger EA1 for the full text)

- **Observation** = first tick of each window landing in a cell; one obs per
  (slug × cell); `book_healthy` only; Chainlink-settled (`cl_outcomes`).
- **Grid**: {BUY-FAVOURITE 0.50–0.95, BUY-CHEAP 0.05–0.50} × 0.05 ask bins ×
  7 time-left bins × |cl_dist| {[0,2),[2,5),[5,12),[12,25),25+,NaN} bps ×
  consistent(C)/disagree(D). Coins pooled; per-coin for finalists.
- **Economics**: edge per $1 staked = E[won/cost]−1; net cost = ask + 0.0072
  (live-calibrated clean-fill slippage), zero fees. **Cell-level cost approximation** —
  no ladder walk / zero-fill hazard at atlas granularity (stated limitation; finalists
  need a full `fills_live` re-validation before deploy).
- **Stats**: window-clustered bootstrap CIs (canonical law, n=3000); one-sided bootstrap
  p-values (n=10000); BH FDR 10% per tail over the 767-cell family. Dev+holdout computed
  first; future revealed once for the 105 candidates only (`--reveal-future`, re-reveal
  blocked by artifact flag).
- Splits: dev 05-23→27, holdout 05-28→31, future 06-01→09. Obs: 99k dev / 82k holdout /
  217k future. Caveat: cells share windows ⇒ tests correlate; BH is PRDS-grade control.

## The atlas at a glance (dev+holdout pooled, net % per $1, obs-weighted)

Marginal by side × book-vs-spot agreement:

| side bought | consistent (book=spot) | disagree (book≠spot) |
|---|---|---|
| FAVOURITE | **+1.5%** (n 71.9k) | **−13.4%** (n 18.7k) |
| CHEAP side | **−15.5%** (n 74.2k) | **+22.0%** (n 16.3k) |

The whole market in one table: when book and spot disagree, **spot is right** — the
cheap (spot) side earns +22% and the disagreeing favourite burns −13%. When they agree,
buying favourites is roughly fair (+1.5%, positive at low asks / short tl, negative at
high asks early) and buying longshots is the classic −15% favourite-longshot bias.

FAV side, consistent, net% by ask × time-left (pooled |cl_dist|):

| ask \ tl | 0–30 | 30–60 | 60–120 | 120–180 | 180–300 | 300–450 | 450–900 |
|---|---|---|---|---|---|---|---|
| 0.50–0.55 | +21.4 | +11.1 | +10.0 | +6.1 | +1.7 | +1.4 | +2.4 |
| 0.55–0.60 | +3.3 | +22.4 | +7.6 | +5.9 | +5.4 | +0.1 | +4.3 |
| 0.60–0.65 | +3.6 | +9.3 | +5.6 | +5.2 | +5.2 | +6.4 | +0.8 |
| 0.65–0.70 | +4.5 | +3.8 | −0.8 | +4.8 | +2.9 | +3.8 | −1.1 |
| 0.70–0.75 | −3.7 | −0.9 | +3.7 | +2.9 | +3.7 | +0.9 | −0.8 |
| 0.75–0.80 | +7.9 | +10.1 | +7.9 | +2.6 | +1.5 | +0.7 | −1.0 |
| 0.80–0.85 | −6.7 | +2.1 | +3.7 | +2.9 | +4.5 | +1.2 | −1.2 |
| 0.85–0.90 | −3.0 | +3.7 | +1.7 | −1.7 | +2.1 | +0.7 | −2.1 |
| 0.90–0.95 | −4.7 | +0.5 | +0.8 | −0.5 | −0.7 | −0.0 | −1.6 |

CHEAP side, disagree (buy the spot side), net% by ask × time-left:

| ask \ tl | 0–30 | 30–60 | 60–120 | 120–180 | 180–300 | 300–450 | 450–900 |
|---|---|---|---|---|---|---|---|
| 0.05–0.10 | +249 | +213 | +169 | +144 | +22 | −41 | −100 |
| 0.10–0.15 | +151 | +142 | +135 | +35 | +113 | +22 | +61 |
| 0.15–0.20 | +145 | +103 | +45 | +85 | +1 | +19 | +16 |
| 0.20–0.25 | +85 | +94 | +13 | +44 | +25 | +16 | +25 |
| 0.25–0.30 | +72 | +75 | +34 | +10 | +46 | +53 | +47 |
| 0.30–0.35 | +47 | +48 | +38 | +21 | +37 | +50 | +34 |
| 0.35–0.40 | +26 | +39 | +22 | +33 | +20 | −1 | +20 |
| 0.40–0.45 | +36 | +26 | +22 | −3 | +16 | −3 | +10 |
| 0.45–0.50 | +26 | +31 | +18 | +17 | +6 | +2 | +5 |

(The CHEAP-consistent grid is negative nearly everywhere — −10…−60% — except the
near-strike |cl_dist|<2 lottery pocket; the FAV-disagree grid is negative everywhere,
−4…−38. Full per-cell numbers live in `data/research/edge_atlas/atlas_cells.parquet`.)

By |cl_dist| (the oracle's own lead over the strike): FAV-C profits concentrate at
cl 5–25 bps (+4.6…+5.2%) and lose at cl<2 (−3.9%); CHEAP-C is the mirror (−27…−32% at
cl 5–25, +8.0% at cl<2). The oracle lead, not the book price, decides which side of the
near-deterministic trade is mispriced.

## Positive candidates after FDR (24), with the one-shot future reveal

| cell (side\|ask\|tl\|cl_dist\|cons) | n_dev | dev net [90% CI] | hold | FUT n | FUT net [90% CI] | verdict | cov_max |
|---|---|---|---|---|---|---|---|
| CHEAP\|0.05-0.10\|tl0-30\|cl0-2\|C | 76 | +234 [+128,+351] | +264 | 86 | **+366 [+253,+482]** | strong | 0.62 fade |
| CHEAP\|0.35-0.40\|tl120-180\|cl0-2\|D | 41 | +55 [+21,+87] | +18 | 35 | +37 [−1,+74] | confirmed | 0.60 fade |
| CHEAP\|0.45-0.50\|tl30-60\|cl0-2\|D | 49 | +41 [+19,+63] | +35 | 39 | −4 [−31,+23] | **failed** | 0.52 |
| CHEAP\|0.30-0.35\|tl450-900\|cl2-5\|D | 123 | +39 [+17,+62] | +34 | 71 | **+55 [+25,+84]** | strong | 0.27 UNHARV |
| CHEAP\|0.30-0.35\|tl450-900\|cl5-12\|D | 135 | +34 [+12,+55] | +16 | 183 | **+54 [+34,+71]** | strong | 0.35 UNHARV |
| CHEAP\|0.35-0.40\|tl450-900\|cl2-5\|D | 254 | +29 [+15,+43] | +17 | 216 | **+49 [+35,+64]** | strong | 0.31 UNHARV |
| FAV\|0.55-0.60\|tl450-900\|cl12-25\|C | 84 | +25 [+11,+39] | +28 | 946 | +7.5 [+3.2,+11.9] | strong | 0.71 sq |
| CHEAP\|0.35-0.40\|tl450-900\|cl0-2\|D | 186 | +25 [+9,+40] | +28 | 142 | **+23 [+5,+41]** | strong | 0.27 UNHARV |
| FAV\|0.60-0.65\|tl300-450\|cl5-12\|C | 192 | +23 [+15,+32] | +12 | 684 | −3.5 [−8.5,+1.2] | **failed** | 0.49 |
| FAV\|0.60-0.65\|tl450-900\|cl12-25\|C | 125 | +21 [+11,+31] | +14 | 1336 | +7.7 [+4.3,+11.0] | strong | 0.73 sq |
| FAV\|0.60-0.65\|tl180-300\|cl5-12\|C | 114 | +20 [+9,+31] | +10 | 407 | +7.6 [+1.2,+13.6] | strong | 0.42 UNHARV |
| FAV\|0.75-0.80\|tl0-30\|cl2-5\|C | 40 | +19 [+9,+26] | +3 | 61 | −13.5 [−26,−1] | **failed** | 0.45 |
| CHEAP\|0.40-0.45\|tl450-900\|cl5-12\|D | 257 | +19 [+7,+31] | +2 | 831 | **+14 [+7,+21]** | strong | 0.45 UNHARV |
| FAV\|0.55-0.60\|tl450-900\|cl5-12\|C | 580 | +17 [+12,+23] | +4 | 1927 | −2.6 [−5.8,+0.6] | **failed** | 0.41 |
| CHEAP\|0.40-0.45\|tl450-900\|cl0-2\|D | 482 | +17 [+9,+26] | +9 | 520 | **+15 [+6,+23]** | strong | 0.28 UNHARV |
| FAV\|0.85-0.90\|tl30-60\|cl5-12\|C | 65 | +14 [+13,+14]* | +6 | 168 | +3.1 [−1.0,+7.3] | confirmed | 0.39 UNHARV |
| FAV\|0.70-0.75\|tl180-300\|cl5-12\|C | 218 | +13 [+8,+19] | +10 | 548 | +0.5 [−3.9,+4.7] | confirmed | 0.47 UNHARV |
| FAV\|0.80-0.85\|tl180-300\|cl12-25\|C | 80 | +13 [+7,+18] | +3 | 616 | +3.1 [+0.3,+5.8] | strong | 0.54 |
| FAV\|0.60-0.65\|tl300-450\|cl2-5\|C | 373 | +11 [+5,+17] | +0 | 498 | −12.3 [−18,−7] | **failed** | 0.35 |
| FAV\|0.80-0.85\|tl60-120\|cl5-12\|C | 99 | +11 [+5,+16] | +13 | 280 | +0.7 [−3.7,+5.0] | confirmed | 0.43 UNHARV |
| FAV\|0.90-0.95\|tl0-30\|cl5-12\|C | 49 | +7.8 [+7.4,+8.1]* | +2 | 136 | −8.4 [−14,−3] | **failed** | 0.32 |
| FAV\|0.75-0.80\|tl450-900\|cl12-25\|C | 394 | +6.5 [+2.4,+10.4] | +4 | 1765 | −1.0 [−3.2,+1.2] | **failed** | 0.53 |
| FAV\|0.80-0.85\|tl180-300\|cl5-12\|C | 352 | +6.4 [+2.8,+9.9] | +5 | 556 | −2.9 [−6.2,+0.5] | **failed** | 0.42 |
| FAV\|0.80-0.85\|tl450-900\|cl12-25\|C | 445 | +5.0 [+1.7,+8.0] | +1 | 1446 | −0.4 [−2.4,+1.6] | **failed** | 0.48 |

\* degenerate CI: 100% dev WR ⇒ constant returns; treat as "no dev losses", not precision.

Honest scorecard: 15/24 confirmed, 11 strong, **9 failed forward**. Every CHEAP-disagree
candidate at tl450–900 confirmed strong; the failures are almost all FAV-consistent
cells with a SHALLOW oracle lead (cl 2–12 bps) at long horizon — dev-period favourite
value that did not survive the (more volatile, 2.4× more signal-dense) future regime.
The FAV cells that DID confirm need cl_dist ≥ 12 bps. Lesson: the favourite is only
underpriced when the oracle lead is deep; at shallow leads the apparent dev edge was
regime luck.

## NEW-EDGE shortlist (unharvested ∧ future-confirmed, ranked by future net)

| rank | cell | FUT net [90% CI] | n_fut | WR_fut | median top-depth | cov_max |
|---|---|---|---|---|---|---|
| 1 | CHEAP\|0.30-0.35\|tl450-900\|cl2-5\|D | **+55.1% [+25.4,+84.0]** | 71 | 52% | $13 | 0.27 |
| 2 | CHEAP\|0.30-0.35\|tl450-900\|cl5-12\|D | **+53.7% [+34.5,+71.2]** | 183 | 51% | $9 | 0.35 |
| 3 | CHEAP\|0.35-0.40\|tl450-900\|cl2-5\|D | **+49.2% [+34.6,+64.3]** | 216 | 57% | $10 | 0.31 |
| 4 | CHEAP\|0.35-0.40\|tl450-900\|cl0-2\|D | **+23.3% [+4.7,+41.5]** | 142 | 47% | $12 | 0.27 |
| 5 | CHEAP\|0.40-0.45\|tl450-900\|cl0-2\|D | **+14.7% [+6.4,+23.2]** | 520 | 50% | $13 | 0.28 |
| 6 | CHEAP\|0.40-0.45\|tl450-900\|cl5-12\|D | **+13.9% [+7.3,+20.9]** | 831 | 49% | $11 | 0.45 |
| 7 | FAV\|0.60-0.65\|tl180-300\|cl5-12\|C | +7.6% [+1.2,+13.6] | 407 | 68% | $13 | 0.42 |
| 8 | FAV\|0.85-0.90\|tl30-60\|cl5-12\|C | +3.1% [−1.0,+7.3] | 168 | 90% | $21 | 0.39 |

### The early-window cheap-disagree family (rows 1–6 pooled, dedup per window)

Spec: time_left 450–900s (first half of the window), book favourite ≠ Coinbase-spot
side, buy the cheap/spot side at ask 0.30–0.45, |cl_dist| < 12 bps.

| split | n | WR | net/$1 [90% CI] | mean ask |
|---|---|---|---|---|
| dev | 900 | 50.8% | **+27.2% [+20.2,+34.2]** | 0.395 |
| holdout | 633 | 45.0% | **+11.3% [+3.3,+19.5]** | 0.400 |
| future | 1,279 | 49.9% | **+20.9% [+15.2,+26.4]** | 0.410 |

- Future per-day: **9/9 days ≥ 0** (worst 06-01 +0.0%, best 06-09 +41.5%); ~142
  windows/day. Per-coin future: btc +20% / eth +22% / sol +28% / xrp +14%.
- Entry timing: p50 entry_sec 111s (p10 30s, p90 358s) — fires ~2 min after open.
- Coverage by ALL existing decision sets ≤ 0.30 (fav_lowvol 0.30, sq 0.29, fade 0.25,
  fav_disagree **0.04**) — this volume is genuinely untraded today.
- Mechanism: in the first minutes the book is still anchored near the open while spot
  has already moved a few bps; the spot side trades at 0.30–0.45 but wins ≈50% —
  fav_disagree's lag mechanism, upstream in the window where nothing currently looks.
  At ~50% WR and 0.41 cost, a HALF of windows pay 1.44x net — variance per trade is
  high but the per-window CI is decisively positive in all three splits.

**Caveats (honest):**
- **Capacity**: median displayed top-of-book depth ~$10 (p25 $3). At $5–10 stakes
  feasible; not a size edge. Needs the full `fills_live` zero-fill/kappa treatment —
  the cell-level cost model here charges slippage but cannot model unmatchable depth.
- **Adverse selection on fills**: a fade-flavoured taker buy; the random zero-fill
  hazard assumption is optimistic (same caveat as OP4) → paper first.
- **Latency exposure: LOW** — with 7.5–15 min left there is no last-second race; the
  signal is a state (book≠spot), not an event. Survival at human latency is plausible
  by construction, but must be confirmed with `edge_lab.latency_survival`.
- holdout dipped to +11% (still CI>0): size expectations off the pooled ~+20%, not dev's +27%.
- Window-level correlation with the live sq book is modest (slug overlap 0.29) but the
  macro-correlation memory applies: 4 coins on one tape; size as ~1 macro bet.

## Fade / avoid zones (negative candidates: 81 → 61 future-confirmed, 40 strong)

The strongest reliably-(−EV)-to-BUY regions (future-strong, large n):

| zone | exemplar cells | FUT net [90% CI] |
|---|---|---|
| Cheap consistent longshot vs an established CL lead (ask 0.05–0.25, cl≥5bps, any tl) | 0.10-0.15\|tl300-450\|cl25+ n=724; 0.15-0.20\|tl180-300\|cl12-25 n=658; 0.05-0.10\|tl30-60\|cl5-12 n=264 | −49.6 [−61.7,−37.9]; −30.9 [−42.6,−18.9]; −40.6 [−67.2,−10.1] |
| Buying the favourite AGAINST spot (FAV\|D), esp. early window | 0.60-0.65\|tl450-900\|cl2-5 n=385; 0.65-0.70\|tl450-900\|cl2-5 n=142; 0.55-0.60\|tl450-900\|cl2-5 n=992 | −30.3 [−36.9,−23.9]; −31.0 [−41.2,−20.7]; −18.4 [−22.9,−14.1] |
| Mid-strong favourites at an undecided strike (FAV\|C, 0.70–0.80, cl0-2, tl300-450) | 0.70-0.75\|tl300-450\|cl0-2 n=197; 0.75-0.80\|tl300-450\|cl0-2 n=110 | −20.0 [−27.8,−12.4]; −17.5 [−27.9,−8.0] |
| Cheap-side near-half (0.40–0.50) bought vs a 12–25bps CL lead, early | 0.40-0.45\|tl450-900\|cl12-25 n=1177; 0.35-0.40\|tl450-900\|cl12-25 n=1517 | −16.3 [−21.9,−10.9]; −15.0 [−20.3,−9.7] |

These are direct trading rules for the EXISTING book: anything in sq/relaxed territory
that buys a sub-0.25 longshot against a ≥5 bps Chainlink lead is structurally −EV
(consistent with the corrected H3 understanding: the cheap JACKPOT zone is only the
near-strike cl<2 pocket, not cheap-vs-decided). Note 20/81 negative candidates did NOT
confirm forward (several mid-ask CHEAP-C cells even flipped positive, e.g.
0.15-0.20|tl300-450|cl5-12 +26.4 [+7.2,+45.5]) — mid-band cheap-C is regime-unstable;
only the deep-lead cells above are dependable avoid zones.

## Harvested overlay (what the running book already owns)

Decision-set sizes (slugs): det_lwd_live 486, det_d12_wide_v1 941, det_d12_dual_live
423, fav_disagree 363, fav_momentum 640, fav_lowvol 1367, fav_deepdown 936,
near_strike_fade 1344, sq 2119. Coverage = fraction of a cell's dev+holdout windows
appearing in the set (window-overlap, NOT same-side/same-time — an upper bound on true
harvesting).

- The big near-strike lottery cell (CHEAP|0.05-0.10|tl0-30|cl0-2|C, future +366%) is
  62% covered by the near-strike fade (OP4) — the atlas independently re-derives OP4's
  zone and confirms its future edge.
- The deep-lead favourite cells (FAV|0.55-0.65|tl450-900|cl12-25) are 71–73% covered by
  sq's window set — partially harvested.
- The early-window cheap-disagree family is ≤30% covered by ANYTHING, and only 4% by
  fav_disagree, its mechanistic sibling — **the largest untraded confirmed region**.
- 9 of the 15 future-confirmed positives are unharvested by the <0.5 rule.

## Sanity anchors (does the atlas recover what we know?)

- E4/fav_disagree: CHEAP-D is the only broadly positive grid (+22% marginal) — yes.
- det: FAV-C short-tl, low-ask cells positive (+10…+22%); deep-lead late favourites
  (cl12-25) confirm at +3…+8% — yes, fragmented across small cells as expected.
- OP4 fade: near-strike cheap lottery confirms at +366% future — yes.
- sq deep-tail memory ([[sq-deep-tail-floor-anti-edge]]): cheap jackpots exist ONLY
  near-strike (cl<2); the same prices against a real lead are the worst cells in the
  whole atlas — refines the memory with the conditioning variable (cl_dist).
- 5m control logic: the cl25+ CHEAP cells are catastrophic (−49%) — buying against
  near-certainty loses; the engine's labels behave.

## Limitations

- Cell-level cost model (slippage-only). No zero-fill hazard, no ladder walk, no queue.
- Coverage is window-overlap, an upper bound on harvesting.
- xrp is the weak leg of the shortlist family (+14%) and was −100% in the (tiny,
  already-harvested) lottery cell — per-coin monitoring required.
- Future block is 9 days of one macro regime; family day-stability (9/9 ≥ 0) is the
  best argument it is not one lump.
- Bins are pre-registered but still bins: neighbouring-cell agreement (rows 1–6 form a
  contiguous block) is the real evidence; no single cell should be cherry-picked.

## Where the remaining edge lives (one paragraph)

The atlas says the book has exactly one systematic defect: it reprices toward
spot/oracle reality too slowly, and all surviving edge is rent on that lag at three
distances from settlement. Late (tl<3min), the lag shows up as deep-lead favourites a
few cents cheap — the det family already farms it, and only the cl≥12bps slices still
pay. Mid-window near the strike, the lag inverts into overpriced favourites whose
oracle says coin-flip — OP4/fade farms it. Early (tl 450–900s), the lag is widest and
completely unfarmed: the book still quotes the open's favourite after spot has crossed
to the other side, leaving the spot side at 0.30–0.45 with ~50% Chainlink win rate —
+21%/$1 net forward, ~140 windows/day, every day, every coin, but only ~$10 of
displayed depth per window. Everything else — buying longshots against an established
lead, buying favourites against spot, buying mid-favourites at undecided strikes — is
reliably the other side of that same rent. The remaining frontier is therefore not new
signals but earlier timing of the one signal we already trust, at small size; next step
is a full live-fill-model re-validation + latency-survival run of the early-window
cheap-disagree family, then a paper twin.
