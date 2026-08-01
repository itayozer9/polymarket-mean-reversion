# Burst cap + capacity study (2026-06-11) — multi-coin burst risk for the disagree family + stake-capacity curves

**Two questions, user-approved, research + proposal only (no live code touched; `scripts/live_executor.py`
frozen mid-A/B until ~Jun 13).**

**A. Should a live disagree strategy cap simultaneous same-window-timestamp fills across coins?**
fav_disagree_live's first live day lost $10.56 in ONE simultaneous 3-coin knife burst (window
1781113500: btc/eth/sol DOWN, fills 0.25→0.16 / 0.23→0.14 / 0.40→0.26, all `knife_catch`).
**Answer: yes for the deployed fav_disagree_live (max 1 intent per window-ts, first-arrival), no for
early_disagree (its own table fails the registered rule — keep uncapped).** Bursts are real and
massively outcome-correlated (one macro bet at N× size, pair agreement ~85% vs ~50% independence
null) but NOT net-negative — the cap is a variance/tail trade priced below, not an EV rescue.
A flag-gated executor patch is PROPOSED in §4 (not applied).

**B. How big can each validated config trade before fills/EV degrade?**
Capacity curves at $5/$10/$25/$50 under the full 10-level L2 walk AND the live-calibrated fill
hazard, §5. Headline (registered rule, live model): det_lwd $25; det_d12_dual **fails even $5**
on this block's seed-0 read (borderline — keep $5, do not size up); fav_disagree_live045 letter-passes
$50 (n=21 fills, fragile); early_disagree $50; psettle_2246 $50; OP4 fade $50. Model portfolio at
those stakes ≈ **$1,796/day** — an impact-blind upper bound; the conservative $10-tier estimate is
**≈$500/day** (§6, with the caveats that matter).

Pre-registration: `docs/research/test_ledger.md` § "Burst-cap + capacity study (2026-06-11)"
(BC1–BC3, decision rules fixed before the scripts first ran on real data). Artifacts:
`data/research/burst_cap/{anatomy,policies}.jsonl`, `data/research/capacity_curves/capacity.jsonl`.
Code: `research/analysis/burst_cap.py`, `research/analysis/capacity_curves.py` (+18 pure-helper
tests). One bug found during the run: the first execution had a sign error in the `decide()`
worst-cut leg (qualifying policies were rejected); fixed, locked with 3 unit tests, re-run —
all simulated numbers byte-identical (md5-checked), only the rule verdicts changed.

---

## 1. Protocol (registered before results)

- **Data**: `edge_lab.load_base()` with the LC2 campaign split override
  (`hypothesis_sweep.set_future_override("2026-06-05")`): dev 05-23..27, holdout 05-28..06-04,
  **future 06-05..09** (06-09 partial; 1,610 slug-windows ≈ **4.19 day-equivalents** at 384/day).
- **Fill models** (per `rejudge_live_model.simulate_config`): **v2** = idealized fixed-2s full-L2
  `walk_buy` in-band; **live_guarded** = `fill_model_live.json` live-1 (sampled empirical latency,
  zero-fill hazard by depth-ratio×time-left — the depth ratio scales with stake — kappa 1.056,
  guard floor entry−0.04, ceiling = the config's max_ask, adaptive for dual). $5 stake for BC1/BC2;
  live seed-0 CI headline + seeds 0–4 mean±sd everywhere.
- **Configs** (BC1/BC2 disagree family, decision frames via `decisions_for`): `fav_disagree`
  (rejudge CONFIGS twin: tl 120–360s, |dist|≥10bps, ud_ask 0.05–0.90), `fav_disagree_live045`
  (the DEPLOYED live band, ud_ask 0.05–0.45 — the incident config), `early_disagree` (deployed
  early_disagree_live params: tl 450–900s, |dist|≥10bps, ud_ask 0.30–0.45). BC3 adds
  `det_lwd_live`, `det_d12_dual_live` (AGREE gate), `psettle_2246`, OP4 `fade_op4`.
- **A burst** = ≥2 coins firing the SAME strategy at the same `window_start_ts`. Cap policies act
  at the INTENT level before fill simulation (exactly what an `Executor._blocked` gate can do).
- **CIs**: BC2/BC1 cluster by `window_start_ts` (the burst unit — same-ts fills are the
  non-independence under study); BC3 clusters by slug (comparable to the campaign baselines).
- **Honesty**: 06-05..09 was already revealed for all these regions (06-10/11 studies) — this study
  claims NO new edges; it measures risk shape and execution capacity of validated edges, with the
  accept/reject rules fixed in advance so the cap/sizing choices stay honest.

Sanity cross-checks against independent prior runs (same block, same models): fav_disagree v2
future EV/fill **+$1.60 = campaign +$1.60**; psettle_2246 live $5 future **+$2.00 [1.34,2.64]
n=115, seeds 1.61±0.29 = campaign exactly**; det_lwd v2 +$0.52 = campaign. Live seed-0 points for
det/dual differ from the campaign's by RNG-stream alignment only; seeds 0–4 means agree within 1sd.

## 2. BC1 — burst anatomy (FULL window, uncapped, $5)

| config | decisions | window-ts groups | % groups ≥2 coins | % decisions in bursts | group sizes 1/2/3/4 |
|---|---|---|---|---|---|
| fav_disagree | 363 | 209 | 50.2% | 71.3% | 104/73/15/17 |
| fav_disagree_live045 | 258 | 161 | 39.1% | 62.0% | 98/41/10/12 |
| early_disagree | 603 | 350 | 44.3% | 67.7% | 195/81/50/24 |

**Joint outcomes — burst members are ONE bet, not four.** Within-burst pairwise win/lose agreement
vs the permutation null (outcomes shuffled across fills, n=2000):

| config | model | fills (in bursts) | pair agreement | null | p | P(win\|partner won) | P(win\|partner lost) |
|---|---|---|---|---|---|---|---|
| fav_disagree | v2 | 358 (252) | **81%** | 54% | <0.001 | 85% | 26% |
| fav_disagree | live seed0 | 231 (122) | **82%** | 52% | <0.001 | — | — |
| fav_disagree_live045 | v2 | 219 (134) | **85%** | 50% | <0.001 | 87% | 18% |
| fav_disagree_live045 | live seed0 | 112 (40) | **87%** | 51% | <0.001 | — | — |
| early_disagree | v2 | 482 (308) | **83%** | 51% | <0.001 | 86% | 21% |
| early_disagree | live seed0 | 273 (144) | **87%** | 52% | <0.001 | — | — |

(p = 1/2001 is the permutation floor — the correlation is as significant as the test can express.)

**But bursts are NOT bad trades.** Burst-member EV ≥ singleton EV everywhere (v2, window-ts-clustered
CI): fav_disagree burst **+$3.04 [1.87,4.28]** vs singleton +$1.43 [−0.12,3.16]; live045 +$3.67 vs
+$3.39; early +$2.02 vs +$1.94. The macro moves that fire all coins at once are the most informative
disagreements. So the burst problem is purely **variance/tail**: worst uncapped window-ts group
≈ **−$21** at $5 stakes (a 4-coin loss burst ≈ 4× the single-trade worst) — the live −$10.56 incident
was exactly this object at N=3 with partial knife fills.

Cross-strategy note: fav_disagree_live045 and early_disagree share **88 window-ts** (55% of
live045's firing groups) — the two LIVE disagree books frequently stack the SAME settlement event at
different entry times. Worst theoretical same-event exposure across both books = 8 × $5. A GLOBAL
per-window-ts ceiling across disagree-family books is the natural next guard; out of scope here
(per-strategy caps only), flagged for the next executor revision.

## 3. BC2 — cap policy backtest (future 06-05..09, $5)

Policies applied to the decision frame per (strategy, window_start_ts): keep first N by entry time
(`max{1,2}_first`; ties → lower ask → slug) or by cheapest ask (`max{1,2}_cheap`). lg = live_guarded;
`tot` = seeds 0–4 mean total ± sd; `ev` = seed-0 EV/fill [window-ts-clustered 90% CI]; `worst` =
seeds-mean worst window-ts group; `dd` = seeds-mean max drawdown.

**fav_disagree (0.90 band, paper twin):**

| policy | attempts | v2: n / tot / worst / dd | lg: n / tot / ev [CI] / worst / dd |
|---|---|---|---|
| uncapped | 363 | 143 / **+229.1** / −21.3 / 37.8 | 87 / **+108.8±39** / +1.77 [+0.28,+3.39] / −16.9 / 31.7 |
| max1_first | 209 | 81 / +110.5 / −5.3 / 19.2 | 54 / +29.1±30 / +0.37 [−0.78,+1.56] / −5.3 / 25.5 |
| **max2_first** | 314 | 123 / +221.3 / −10.7 / 27.2 | 80 / **+119.6±22** / +1.08 [−0.18,+2.32] / **−10.6** / 22.6 |
| max1_cheap | 209 | 81 / +76.8 / −5.3 / 26.1 | 52 / **−6.1±25** / −0.09 [−1.33,+1.22] / −5.3 / 41.1 |
| max2_cheap | 314 | 123 / +193.7 / −10.7 / 41.8 | 79 / +112.0±36 / +0.60 [−0.62,+1.83] / −10.7 / 29.4 |

→ registered rule: **recommend max2_first** (qualifiers: max2_first, max2_cheap). Retains ~100% of
live total (within seed noise), cuts worst group 37% (−16.9→−10.6) and dd 29%.

**fav_disagree_live045 (the DEPLOYED live config):**

| policy | attempts | v2: n / tot / worst / dd | lg: n / tot / ev [CI] / worst / dd |
|---|---|---|---|
| uncapped | 258 | 76 / +161.7 / −21.3 / 37.1 | 34 / +66.1±46 / +3.30 [+0.74,+5.87] / −16.0 / 28.9 |
| **max1_first** | 161 | 43 / +134.9 / **−5.3** / 15.8 | 27 / **+64.6±25** / +3.09 [+0.48,+6.02] / **−5.3** / 16.3 |
| max2_first | 224 | 65 / +169.2 / −10.7 / 26.5 | 40 / +59.6±45 / +2.80 [+0.47,+5.48] / −10.6 / 22.7 |
| max1_cheap | 161 | 43 / +90.7 / −5.3 / 20.9 | 27 / +38.2±26 / +2.53 [−0.20,+5.63] / −5.3 / 20.0 |
| max2_cheap | 224 | 63 / +148.6 / −10.7 / 36.6 | 39 / +56.7±32 / +2.40 [+0.03,+5.13] / −10.6 / 24.1 |

→ registered rule: **recommend max1_first** (qualifiers: max1_first, max2_first, max2_cheap).
Keeps **98% of live total (64.6 vs 66.1)** while cutting the worst window-ts group **67%**
(−16.0→−5.3 = single-trade floor), drawdown **44%** (28.9→16.3), and seed-to-seed total sd 46→25.
v2 agrees (83% retention, worst −21.3→−5.3).

**early_disagree (deployed early_disagree_live params):**

| policy | attempts | v2: n / tot / worst / dd | lg: n / tot / ev [CI] / worst / dd |
|---|---|---|---|
| uncapped | 603 | 186 / +377.7 / −20.9 / 50.6 | 111 / **+206.3±41** / +2.10 [+0.89,+3.29] / −13.6 / 35.6 |
| max1_first | 350 | 103 / +203.5 / −5.2 / 35.0 | 61 / +128.8±11 / +2.42 [+1.14,+3.69] / −5.2 / 23.7 |
| max2_first | 505 | 152 / +296.9 / −10.5 / 45.4 | 89 / +178.9±16 / +1.77 [+0.65,+2.99] / −10.4 / 33.4 |
| max1_cheap | 350 | 104 / +182.4 / −5.2 / 35.0 | 65 / +145.7±27 / +2.98 [+1.76,+4.19] / −5.2 / 20.7 |
| max2_cheap | 505 | 156 / +292.3 / −10.5 / 45.4 | 88 / +146.3±34 / +1.20 [+0.04,+2.43] / −10.4 / 33.0 |

→ registered rule: **no policy qualifies — honest negative, keep uncapped.** Closest miss:
max2_first cuts worst only 23.5% (bar: 25%) at 87% retention; max1 variants retain just 62–71%
(bar: 80%). Early-window bursts enter minutes apart and the fill hazard already de-correlates them
(uncapped live worst −13.6 vs v2 −20.9); the volume give-up isn't paid for.

**Mechanics worth keeping (consistent across configs):**
- **The cheap tie-break selects INTO knives.** `max1_cheap` is the worst policy everywhere (for the
  0.90 band it flips the future total NEGATIVE, −$6.1): the burst member with the lowest ask is the
  coin whose book has already collapsed furthest — adverse selection by construction. Keep-first
  (= the executor's natural arrival order) is the right tie-break, as predicted at registration.
- **An attempt-level cap interacts with the zero-fill hazard.** Under live physics ~44–55% of
  intents fill; later burst siblings act as free retries of the same macro bet. That is why max1
  costs the 0.90-band config 73% of its live total (its burst members are heterogeneous — the first
  coin to cross is often a worse trade than the deeper-moved later sibling) but costs the
  homogeneous 0.45-band config almost nothing. A fill-aware cap ("max 1 FILL, allow the next intent
  only after a definitive zero-fill") would dominate both; it needs fill-feedback in `_blocked` and
  is left as the v2 of this guard.

### Verdict (registered rule, per config)

| config | verdict | effect at $5 (live, future block) |
|---|---|---|
| fav_disagree_live045 (LIVE) | **CAP — max 1 intent/window-ts, first arrival** | total −2%, worst −67%, dd −44% |
| fav_disagree 0.90 (paper) | CAP — max 2/window-ts, first arrival | total ~+10% (≈ noise), worst −37%, dd −29% |
| early_disagree (LIVE) | **NO CAP** (honest negative) | best policy misses the registered bars |

## 4. Proposed executor patch (flag-gated; NOT applied — executor frozen until ~Jun 13)

Hook: `Executor._blocked` + the per-strategy book, mirroring the simulated intent-level cap exactly
(count intents CONSUMED per window-ts; first-arrival keep = the backtest's keep-first tie-break).
Default `EXEC_BURST_CAP=0` keeps behavior byte-identical. Restart-safe by construction: the count
derives from the persisted `done_slugs` (the daily-cap Defect-3 lesson), plus an in-memory in-flight
set for concurrency (mirrors `open`).

```diff
--- a/scripts/live_executor.py
+++ b/scripts/live_executor.py
@@ -86,6 +86,15 @@ PER_STRAT_MAX_CONCURRENT = 2   # open live positions per strategy
 GLOBAL_MAX_CONCURRENT = 4      # hard ceiling on in-flight orders across ALL strategies
                                # (shared-wallet guard: bounds worst-case collateral drain)
+# --- multi-coin burst cap (BC2, docs/research/BURST_CAPACITY_2026-06-11.md) ----------------
+# One macro move fires the disagree signal on several coins at the same window_start_ts and
+# the members win/lose together (pair agreement ~85% vs ~50% independence null) — a burst is
+# ONE leveraged macro bet. Cap the intents a strategy may CONSUME per window-ts (arrival
+# order = the validated keep-first tie-break). 0 = off (byte-identical default). Empty sids
+# list = applies to all strategies when the cap is > 0.
+EXEC_BURST_CAP = int(os.getenv("EXEC_BURST_CAP", "0"))
+EXEC_BURST_CAP_SIDS = {s.strip() for s in
+                       os.getenv("EXEC_BURST_CAP_SIDS", "").split(",") if s.strip()}
 STATE_VERSION = 2
@@ -159,6 +168,11 @@ def _slug_window_end(slug):
     ...existing body unchanged...
+
+
+def _slug_window_ts(slug: str) -> str:
+    """btc-updown-15m-1781113500 -> '1781113500' (the cross-coin shared window id)."""
+    return slug.rsplit("-", 1)[-1]
@@ -253,6 +267,7 @@ class StrategyBook:
     open: int = 0  # in-flight live orders (NOT persisted; FAK resolves in <2s, resets on restart)
+    inflight_slugs: set = field(default_factory=set)   # NOT persisted (mirrors `open`)
@@ -440,6 +455,16 @@ class Executor:
         if slug in b.done_slugs:
             return f"[{sid}] already traded this window"
+        # ---- guard: multi-coin burst cap (same strategy, same window_start_ts) ----
+        # Counts CONSUMED intents (done_slugs is appended on ANY attempt, fill or clean
+        # miss) + in-flight siblings — exactly the intent-level cap the backtest scored.
+        if EXEC_BURST_CAP > 0 and (not EXEC_BURST_CAP_SIDS or sid in EXEC_BURST_CAP_SIDS):
+            wts = _slug_window_ts(slug)
+            consumed = (sum(1 for s in b.done_slugs if _slug_window_ts(s) == wts)
+                        + sum(1 for s in b.inflight_slugs if _slug_window_ts(s) == wts))
+            if consumed >= EXEC_BURST_CAP:
+                return (f"[{sid}] burst cap: {consumed} sibling intent(s) already consumed "
+                        f"for window-ts {wts} (EXEC_BURST_CAP={EXEC_BURST_CAP})")
         # Bankroll = balance / max-loss. Stop this strategy for good once its cumulative
@@ -565,6 +590,7 @@ class Executor:
         b.open += 1
+        b.inflight_slugs.add(slug)
         try:
             while True:
                 ...fill loop unchanged...
         finally:
             b.open -= 1
+            b.inflight_slugs.discard(slug)
```

**Deploy setting (when unfrozen):** `EXEC_BURST_CAP=1`, `EXEC_BURST_CAP_SIDS=fav_disagree_live`.
Explicitly NOT `early_disagree_live` (its own table says no) and not the det books (deeper books,
not in scope of this study). Rollout like every guard: one strategy, watch one day of
`intent_skipped` reasons, then judge. The `done_slugs` linear scan is O(slugs/strategy/run) —
hundreds — negligible.

## 5. BC3 — capacity curves (future 06-05..09; lg = live_guarded; EV = seeds 0–4 mean EV/fill, CI = seed-0 window-clustered)

Fill-rate columns are future-block; slip = avg fill price − signal-tick ask (all-splits fills).
`ev/$1` = seeds-mean EV per dollar staked (the scale-invariant read).

| config | stake | v2 fill / fut EV | lg fill | lg fut EV [seed-0 CI] | ev/$1 | lg EV/signal | fills/day |
|---|---|---|---|---|---|---|---|
| det_lwd_live (486 dec) | $5 | 87% / +0.52 | 41% | +0.54±0.09 [+0.13,+1.00] | +0.108 | +0.24 | 17.5 |
| | $10 | 86% / +0.98 | 43% | +1.05±0.18 [+0.29,+1.95] | +0.105 | +0.51 | 17.5 |
| | $25 | 82% / +2.29 | 38% | **+2.77±0.57 [+0.54,+4.98]** | +0.111 | +1.11 | 14.9 |
| | $50 | 77% / +4.67 | 35% | +4.86±1.75 [−0.63,+8.87] | +0.097 | +1.49 | 13.9 |
| det_d12_dual_live (423 dec) | $5 | 86% / +1.00 | 39% | +0.67±0.22 [−0.23,+0.90] | +0.133 | +0.14 | 19.2 |
| | $10 | 84% / +1.92 | 40% | +1.33±0.45 [−0.48,+1.79] | +0.133 | +0.26 | 18.8 |
| | $25 | 81% / +4.38 | 36% | +3.24±1.20 [−1.67,+4.53] | +0.130 | +0.56 | 17.1 |
| | $50 | 76% / +7.68 | 29% | +4.05±2.52 [−6.63,+7.09] | +0.081 | +0.13 | 14.3 |
| fav_disagree_live045 (258 dec) | $5 | 80% / +2.13 | 36% | +1.78±1.28 [+1.20,+5.72] | +0.356 | +1.18 | 9.3 |
| | $10 | 78% / +3.85 | 34% | +2.76±2.44 [+0.90,+10.89] | +0.276 | +1.85 | 8.4 |
| | $25 | 73% / +6.98 | 26% | +7.48±6.05 [+2.49,+30.45] | +0.299 | +4.11 | 7.3 |
| | $50 | 60% / +11.41 | 22% | +15.81±17.95 [+13.16,+73.80] | +0.316 | +9.12 | 6.0 |
| early_disagree (603 dec) | $5 | 77% / +2.03 | 46% | +2.13±0.48 [+1.16,+3.09] | +0.425 | +0.96 | 23.3 |
| | $10 | 72% / +4.02 | 40% | +4.56±0.89 [+2.76,+6.80] | +0.456 | +1.96 | 21.8 |
| | $25 | 65% / +8.92 | 35% | +11.07±2.36 [+5.63,+16.32] | +0.443 | +3.89 | 19.4 |
| | $50 | 50% / +19.06 | 29% | +21.10±9.33 [+8.74,+31.49] | +0.422 | +5.84 | 14.9 |
| psettle_2246 (543 dec) | $5 | 100% / +1.63 | 54% | +1.61±0.29 [+1.34,+2.64] | +0.323 | +1.08 | 27.4 |
| | $10 | 100% / +3.20 | 53% | +3.20±0.54 [+2.60,+5.17] | +0.320 | +2.08 | 27.2 |
| | $25 | 99% / +7.65 | 51% | +7.43±1.25 [+5.82,+12.36] | +0.297 | +4.66 | 26.4 |
| | $50 | 98% / +13.96 | 48% | +14.16±2.23 [+9.80,+23.70] | +0.283 | +7.93 | 24.0 |
| fade_op4 (1321 dec) | $5 | 95% / +5.40 | 48% | +5.18±1.09 [+4.50,+9.39] | +1.035 | +3.27 | 29.4 |
| | $10 | 95% / +10.38 | 48% | +9.14±1.62 [+7.48,+16.07] | +0.914 | +5.50 | 28.4 |
| | $25 | 92% / +23.58 | 40% | +26.65±6.00 [+22.16,+48.23] | +1.066 | +13.90 | 24.2 |
| | $50 | 87% / +42.44 | 39% | +41.67±9.59 [+34.22,+80.65] | +0.833 | +22.20 | 24.1 |

(fills/day = seeds-mean future live fills / 4.19 day-equivalents. The lg EV column pairs the
seeds-mean with the seed-0 CI; for fav_045 the seed-0 point sits high in its own CI — small-n.)

**What the model says capacity-wise.** Per-dollar EV is roughly flat to $25 and dips ~10–25% by $50
for most configs (fade 1.04→0.83, psettle 0.32→0.28, dual 0.13→0.08); the binding constraint in the
model is the **fill rate**, not slippage — the stake-aware hazard cuts live fills 10–35% relative
from $5→$50 (fav_045 36%→22%, fade 48%→39%) and v2's deep-walk fill rate on the thin disagree books
drops 77%→50% (early) and 80%→60% (fav_045). Slippage stays ≤3.4c at $50 because the unfilled-50%
drop rule discards the walks that would have paid more — capacity shows up as missed volume, not
paid spread.

## 6. Max viable stakes + portfolio $/day

**Registered rule** (largest stake with future lg seed-0 CI-lower > 0 AND seeds-mean > 0):

| config | max viable stake | at that stake (model) | note |
|---|---|---|---|
| det_lwd_live | **$25** | 14.9 f/d × +$2.77 = **+$41/day** | $50 fails CI (−0.63) |
| det_d12_dual_live | **none at $5** | — | seed-0 CI spans 0 at every stake on this 4.19d block (seeds-mean +0.67±0.22 at $5 is positive; the campaign's independent seed-0 read +0.92 [+0.40,+1.41] passed). Verdict: borderline — **stay at $5, do not size up**; A/B + next week's data decide |
| fav_disagree_live045 | **$50** (letter) | 6.0 f/d × +$15.81 = +$95/day | **fragile: 21 future fills**, seeds sd ±$17.9; treat as "no degradation detected", not "validated at $50" |
| early_disagree | **$50** | 14.9 f/d × +$21.10 = **+$315/day** | CI-lower +$8.7 at $50; fill rate already 29% |
| psettle_2246 | **$50** | 24.0 f/d × +$14.16 = **+$340/day** | cleanest curve (v2 fills ~100% at $50) |
| fade_op4 | **$50** | 24.1 f/d × +$41.67 = **+$1,004/day** | deepest edge; see impact caveat |

**Portfolio at registered stakes ≈ $1,796/day. Treat this as the model's UPPER BOUND, not a plan.**
The fill model prices displayed-depth walking and a stake-aware zero-fill hazard, but it is blind to:

1. **Market impact / reflexivity** — at $25–50 on books with ~$10 median touch depth we ARE the
   book; the tape that generated these EVs no longer exists once we trade it. Nothing in the model
   prices other agents reacting to our size.
2. **Adverse selection of misses** — the hazard is applied RANDOMLY; live misses are adversely
   selected (the missed fills were winners — the known rejudge limitation). Live EV/fill lands
   below these numbers; the fade is the most exposed (cheap shares nobody sells you are the ones
   worth having).
3. **$5-calibration** — kappa and the latency distribution come from 246 live attempts at $5;
   only the hazard's depth-ratio axis extrapolates with stake.
4. **4.19 day-equivalents** of macro-correlated future data; the six configs pairwise-overlap
   Jaccard 0.08–0.22 (decisions), and BC1 shows same-ts outcomes are one macro bet — the portfolio
   number adds books that partially co-move.

**Conservative read (judgment, not the registered rule): the $10 tier ≈ $500/day model estimate**
(det_lwd +$18, dual-at-$5 +$13, fav_045 +$23, early +$99, psettle +$87, fade +$260). Practical
escalation that respects the evidence: deploy/keep everything at **$5–10**, and only raise a config
one rung ($10→$25→$50) after a forward week in which its REALIZED EV/fill ≥ 0.5× the model's value
at the current rung — the live record, not this backtest, prices the impact the model cannot see.
Daily-loss caps must scale with stake (at $25–50 a single uncapped disagree burst is −$50..−$100,
§2 — another reason the burst cap ships first).

## 7. Caveats roll-up (stated, not hidden)

- Future block re-used (declared at registration): risk-shape/capacity measurement, not edge
  discovery; all EV levels were already known from 06-10/11 reveals.
- BC2 CIs cluster by window_start_ts; BC3 by slug (declared; the burst unit vs campaign
  comparability). Seed-0 live points carry RNG-alignment noise (dual's seed-0 here vs the
  campaign's: same decisions, different draw alignment — seeds-means agree).
- The cap was scored at the intent level; a fill-aware cap (count FILLS, not attempts) would
  strictly dominate and is the natural v2 once `_blocked` can see fill outcomes.
- fav_disagree_live045's curves ride on 34→21 future fills — every number for that config is
  wide; the burst-cap verdict for it rests on the (much larger) FULL-window anatomy plus the
  future-block letter rule.
- early_disagree fires ~26 dec/day here vs the atlas family's ~142 cell-entries/day — different
  objects (deployed twin's dist≥10 first-tick decisions vs per-cell window entries); sizing here
  is for the DEPLOYED twin.

## 8. Reproduce

```
uv run pytest tests/research/test_burst_cap.py tests/research/test_capacity_curves.py -q  # 18 tests
uv run python -m research.analysis.burst_cap --stake 5
uv run python -m research.analysis.capacity_curves            # ~10 min, loads 4-symbol L2
```

Artifacts: `data/research/burst_cap/anatomy.jsonl`, `data/research/burst_cap/policies.jsonl`
(includes per-config `verdict_for_config`), `data/research/capacity_curves/capacity.jsonl`.
Ledger: test_ledger.md § Burst-cap + capacity study — BC1/BC2/BC3 rows + running log (06-11).
