# Test Ledger — pre-registration + running log

**Purpose.** This file is the honesty backbone of the improvement program (started
2026-06-04). Every hypothesis is written down **before** it is tested, with its
predicted effect and a **decision rule** fixed in advance. Every test is logged
with its out-of-sample result and CI — **no silent drops**. At the end, a
program-wide multiple-testing correction (Benjamini–Hochberg FDR + a White-style
reality-check null) is applied across *all* logged tests so cumulative
data-snooping is bounded, not just per-sweep.

**Data.** Full clean window `2026-05-23 .. latest` (fresh spot `cb_spot` /
`dist_strike_bps`; never stale `move_pct`). Two validation regimes, both on the
full window:
- **R1 chronological walk-forward** (PRIMARY, realism gate): train past → test strictly-future, rolling.
- **R2 Combinatorial Purged CV (CPCV)** (additive robustness/PBO estimator): scattered held-out time-blocks, **purge + embargo**, folded by time-block across all coins.

**Ship rule (a change/edge is adopted only if ALL hold):** OOS CI lower bound > 0
on R1 **and** the effect holds in ≥ most CPCV folds (low PBO) **and** it survives
cost+latency stress **and** it has a pre-registered causal mechanism. Otherwise it
is logged as an **honest negative** and not shipped.

> Status legend: ⬜ pre-registered · 🔄 running · ✅ confirmed (shipped) · ❌ honest negative · ⚠️ inconclusive

---

## Pre-registered hypotheses

| # | Edge / change | Mechanism (causal story) | Predicted effect | Decision rule | Status |
|---|---|---|---|---|---|
| H1 | `det_lwd_v2` filters (max_ask 0.88 + adverse_vel≤2 + crossings≥1) | thin cushion / reverting lock / 0-crossing blowouts are bad | EV/trade ↑ vs v1, WR ↑, tail ↓ on fresh OOS | adopt iff R1 OOS CI(EV) lower>0 AND holds in ≥3/4 CPCV folds | ✅ CONFIRMED (partial: ask≤0.88+adverse_vel≤2; crossings not yet tested). Future OOS +0.89→+1.14/tr, CI[+0.28,+2.01]>0; lifts every split. |
| H2 | `det_sqp_v2` filters (margin≥0.12 + dist≤19bps) | tiny mispricings = noise; far-from-strike = betting vs near-certain | EV/trade ↑ vs v1 on fresh OOS | same as H1 | ✅ CONFIRMED. Future OOS +1.20→+2.82/tr, CI lower +0.41→**+1.50**; keeps the cheap jackpot zone (consistent w/ H3). |
| H3 | sq `min_ask` floor (~0.18–0.20) | cheap zone (0.05–0.15) is net-loser even with jackpots | total EV ↑ after removing cheap zone | adopt iff beats a **price-matched cheap-side baseline** on R1+CPCV; else ❌ | ❌ REFUTED (full window: ask 0.05–0.15 is the HIGHEST-EV zone +$4.69/+$3.58/tr; floor LOWERS EV 1.63→1.29. Earlier live-only "loser" was a 6-day adverse-sample artifact. Confirms [[sq-deep-tail-floor-anti-edge]].) |
| H4 | per-edge daily-cap policy | cap truncates sq positive skew; harmless to det | det: cap OK (never trips); sq: no tight cap | choose mode minimizing breach-days w/o cutting OOS EV | ✅ RESOLVED. sq: tight cap costs 33–48% of total ($50→−$1133, $100→−$1300); only $150/day is cheap (−$285). det: worst day −$4, cap never binds. Use `hard_worstcase` (0 breach; soft_settled leaks 3–7 breach-days). Policy: det→$50 hard cap (free safety); sq→no tight cap, use loose ~$150 or per-window/direction position limit. |
| E1 | New: cross-coin lead-lag (BTC→alts) | thin alt books lag the BTC-led macro move | capturable lag ≥ ~5s AND OOS taker EV CI>0 | gate Part1 (lag≥5s, real excess) → Part2 OOS; else ❌. **Re-test on FRESH cb_spot (1–2s), prior negative used stale 15s poll** | ❌ DEAD. Fresh 1–2s spot still shows NO capturable lag (all co-move at lag 0–2s = HFT race; excess over contemp ≈ −0.6). Stronger negative than prior. |
| E2 | New: order-flow / microprice divergence | stale mid drifts toward depth-weighted microprice | OOS taker EV CI>0 beyond cost wall | adopt iff beats price-matched + flat-surface nulls; strict FDR (many features) | ⚠️ INCONCLUSIVE. FOLLOW signal beats price-matched baseline OOS (p=0.002) — real — but fails net-EV gate (future CI p5 −0.15, outlier-dependent, doesn't clear cost wall at ask~0.57). Optional paper experiment; not validated. |
| E3 | New: Chainlink-vs-Coinbase settlement-oracle divergence | settle on Chainlink; trade vs Coinbase WS | near-close divergence predicts settlement | **prior art says oracles track closely** → likely thin; quantify, adopt only if OOS CI>0 | ❌ UNTESTABLE (data gap): `chainlink_price` is all-zeros in joined data (collector not capturing it). Fix collector to re-test, but proxy shows disagreement only in 0–5bps band = redundant w/ determinism. |
| E4 | New: two-sided / disagreement determinism | book-fav ≠ spot-fav ⇒ fade the book (buy cheap spot-side) | OOS taker EV CI>0 | adopt iff R1+CPCV pass + cost survive | ✅ **PROMISING — verified.** Real L2 walk: +$36.8/tr all splits (future +$36.9 CI[+24.9,+50.9]); ≥$10-depth subset +$13–16/tr CI off zero; 15/15 CPCV +; 84% WR; all 4 coins. Additive to det_lwd. **ADD to paper.** |
| E5 | New: late-window momentum-continuation | spot moving away from strike ⇒ favourite more determined than priced | OOS taker EV CI>0 | same | ❌ DEAD. Momentum tag carries no info beyond price/window it selects (label-perm null p=0.34); only shifts price mix slightly cheaper. |
| E6 | New: cross-window persistence | prior window move/outcome predicts next | OOS taker EV CI>0 | prior `prev_fav_lost` was weak — high bar | ⚠️ INCONCLUSIVE. Reversal>50bps passes letter of OOS gate (future +1.28 CI[+0.21,+2.34], p=0.0017) BUT holdout negative, freshest day dead, 4 lumpy macro-correlated days. Needs dedicated forward run. |
| M1 | Meta-labeling P(win\|features) gate/size | principled loss-avoidance | WR ↑ / EV ↑ vs ungated, purged-CV honest | adopt iff purged-CV OOS EV>ungated AND not just cheap-side selection | ⚠️ PARTIAL. Beats UNGATED OOS (det +1.23→+1.5, sq +1.63→+2.0; GBM≈logistic, CPCV) — learnable structure exists. But does NOT beat hand-crafted v2 (sq v2 +2.8 > meta +2.0). → **use interpretable v2 filters, skip the ML model.** |
| P1 | Portfolio/ensemble allocation (det⊕sq⊕new) | lwd↔sq anti-correlation lowers variance | ensemble Deflated-Sharpe > best single edge | adopt allocation iff OOS Sharpe>max single on R1 | ✅ CONFIRMED. det↔sq daily corr **−0.277** (diversifying); risk-parity Sharpe **+2.03** vs det 1.41 / sq 1.04; combined book ~no losing days. Confirms [[lwd-sq-timing-anticorrelation]]. |
| B1 | Binance(+Coinbase) composite predicts the Chainlink settlement print better than Coinbase alone (registered 2026-06-10, BEFORE any future-block reveal). Composite C(t)=w·BN_norm+(1−w)·CB_norm on the 1s grid; per-coin multiplicative venue offsets AND any weight fitting use dev windows ONLY; composite variant for the gate test chosen on dev+holdout near-strike disagreement (tie-break: median \|proxy−CL_close\| at T=0); future revealed once. | Chainlink aggregates volume-weighted across venues and Binance dominates volume ⇒ a Binance-weighted composite sits closer to the CL aggregate than Coinbase alone, esp. for windows finishing near strike where tiny venue basis flips the sign | near-strike (≤2bps cb close-dist) sign-disagreement of proxy-implied outcome vs settled cl_up drops from ~37% (Coinbase) by ≥25% relative | PASS iff on FUTURE windows the chosen composite's ≤2bps sign-disagreement ≤ 0.75 × Coinbase-only's (e.g. 37%→≤28%); report ≤5bps slice + T∈{0,30,60}s \|proxy−CL_close\| median/p90 as supporting evidence | ❌ FAIL (verified 3× reproducible). Future ≤2bps n=153: chosen bn(w=1.0) is **WORSE** on the primary vs-CL-strike framing (cb 22.9% → bn 29.4%, cut −29%; paired cb-only-wrong 14 vs comp-only-wrong 24); own-strike framing 35.9→31.4% (−13% rel, ≪ 25% gate). The ~37% benchmark REPRODUCES (own-strike cb 35.9%): near-strike flips are 33s-heartbeat oracle noise, not a venue-mix problem. Coinbase also beats every composite on \|proxy−CL_close\| at every horizon (future T=0 med 2.88 vs bn 5.10bps) — the print's LEVEL is USD-venue-anchored; Binance's −12bps USDT basis + drift poisons level-referenced use. Details: docs/research/BINANCE_COMPOSITE_2026-06-10.md |
| B2 | det_d12_dual with composite substituted for Coinbase in (i) dist_strike_bps/consistent (signal distance + side) and (ii) the AGREE gate's Coinbase leg (adverse_vel stays Coinbase; cl_dist leg unchanged) | fewer entries whose signal side is about to flip at settlement ⇒ higher WR at same volume; AGREE gate keyed to a feed closer to the settlement aggregate rejects fewer good / admits fewer bad entries | future EV/fill (live_guarded, $5, seed 0) ≥ baseline +$0.63 with CI lower > 0 AND fill-weighted flip-rate (entry-signal side ≠ settled side) strictly lower than baseline | PASS iff all three hold on FUTURE block: EV/fill ≥ $0.63, window-clustered CI lower bound > 0, flip-rate < baseline. Multi-seed (0–4) mean reported as MC-robustness, not a gate | ⚠️ Letter-PASS, robustness-WASH → **NOT shipped**. Future lg $5 seed0: composite[bn w=1.0] **+$0.843 [+0.437,+1.249]** (116 fills, WR 84.5, flip 15.5%) vs baseline +$0.634 [+0.199,+1.062] (131 fills, WR 79.4, flip 20.6%) — all 3 registered legs pass. BUT the registered MC-robustness report says wash: seeds0–4 mean **+$0.84 vs +$0.87** (baseline ahead); deterministic v2 favors baseline (+$1.021×278=$284 total vs +$0.955×240=$229); paired delta on 41 common future fills +$0.310 [−0.037,+0.699] (CI spans 0); volume −12%. Ship rule requires mechanism — B1 (the mechanism) FAILED. Same disposition as OP3: per-fill parity in MC noise → keep Coinbase live; bn-distance paper twin optional. |
| B3 | Binance 1s fetch coverage gate (data-quality precondition for B1/B2) | klines with no trades are absent; geo/rate issues could hole the grid | ≥90% of seconds covered per symbol over 2026-05-22..2026-06-09 | PASS iff coverage ≥90% per symbol; if FAIL, B1/B2 findings are PROVISIONAL | ✅ PASS. **100.00%** all 4 symbols (1,641,600/1,641,600 seconds each, 05-22→06-09, klines_1s path, no aggTrades fallback needed). B1/B2 negatives are NOT data-limited. |

---

## Running log (append every test; never delete)

| date | id | regime | n | WR | EV/trade | OOS CI | extra (PBO / null p / notes) | verdict |
|---|---|---|---|---|---|---|---|---|
| 06-04 | DET full | full 13d | 626 | 87.7% | +1.234 | future +0.89 CI[+0.30,+1.46] | DSR 0.94–0.996; PBO 0.107; cost-stress ALL-combined CI[+0.50,+1.24]; per-day Sharpe 1.41; maxDD ~$4; minTRL~14d(=13 we have); calib-null p=0.054 (marginal) | ✅ real, robust, SMALL |
| 06-04 | DET walk-fwd | R1 9 folds | — | — | +1.36 mean | 9/9 folds + | every forward day positive | ✅ |
| 06-04 | DET CPCV | R2 15 folds | — | — | +1.24 mean | 15/15 +; p5 +0.87 | low-variance | ✅ |
| 06-04 | SQ full | full 13d | 2119 | 47.5% | +1.629 | future +1.20 CI[+0.40,+2.03] | DSR 0.95→0.65 (n_trials 36→1000); minTRL 13→211d; tail: top20=64%; maxDD p50 −$80 | ⚠️ positive but high-var, NOT yet established |
| 06-04 | SQ walk-fwd (refit) | R1 9 folds | 1703 | — | +1.33 pooled | CI[+0.68,+2.00]; 7/9 + | leakage-safe per-fold curve refit | ✅ generalizes (bumpy) |
| 06-04 | SQ CPCV (refit) | R2 15 folds | 10370 | — | +1.80 pooled | CI[+1.22,+2.43]; 15/15 + | — | ✅ |
| 06-04 | SQ v2 (H2) | future OOS | 452 | 48.7% | +2.82 | CI[+1.50,+4.28] | margin≥0.12 + dist≤19; strong OOS lift | ✅ adopt |
| 06-04 | DET LOCO | per-coin | 626 | — | all + | btc+1.16 eth+1.38 sol+0.64 xrp+1.87 | structural across coins; SOL weakest (future −0.07) | ✅ |
| 06-04 | SQ LOCO | per-coin | 2119 | — | all + | btc+1.62 eth+1.59 sol+1.31 xrp+2.06; all future + | structural across coins | ✅ |
| 06-04 | DET plateau | grid | — | — | smooth | EV rises monotonically tightening dist/ask | no knife-edge spike → robust | ✅ |
| 06-04 | H4 cap | full 13d | — | — | — | sq tight-cap costs 33–48%; det cap never binds | hard_worstcase only | ✅ see H4 |
| 06-04 | P1 ensemble | full 13d | — | — | — | corr −0.277; risk-parity Sharpe +2.03 | det⊕sq diversify | ✅ |
| 06-10 | B1-B3 pre-reveal refinement | dev+holdout only | — | — | — | — | FUTURE STILL UNREVEALED. Dev finding: Binance carries a −12bps USDT/USD basis vs CL that DRIFTS (btc holdout T=0 err 1.3→4.4bps under dev-fit constant) ⇒ the composite-variant CHOOSER for B2 is pinned to the OWN-strike sign-disagreement framing (= what the gate substitution + engine use; basis-drift immune), dev+holdout ≤5bps benchmark slice, tie-break median \|proxy−CL_close\| at T=0. B1 stays as registered (primary vs-CL-strike per task definition; own-strike reported alongside). Dev-fitted pooled weight w_fit=0.51 (partial data; refit on full fetch before reveal) | 🔄 → resolved in B1/B2/B3 rows below (w_fit refit on full data = 0.514) |
| 06-10 | OP1 print physics | rounds 05-22→06-10, 194k rounds | 6,593 win | — | — | — | heartbeat ≈33s all 4 coins; T=30s print resid 2.6bps after CB-gap (β 0.88, R² 0.57); flip 17–36% at \|cl_dist\|<5bps; poll misses only 1–2% of rounds | ✅ descriptive |
| 06-10 | OP2 G1 model vs book | future 1.55M ticks / 3,134 win | — | — | — | Brier diff (book−model) +0.00294 CI[+0.00010,+0.00588] | < pre-reg +0.01 margin; book wins logloss (model tails overconfident); model beats AGREE-2-leaf +0.0140 [+0.0120,+0.0162]; iso self-rejected | ❌ honest negative |
| 06-10 | OP3 G2 continuous gate (θ\*=0.575, dev-chosen) | future $5 seed0 | 402 sig / 174 lg fills | 82.2% | lg +0.880, v2 +1.095 | lg [+0.513,+1.231]; v2 [+0.841,+1.337] | letter-PASS (≥$0.63, CI>0, vol 402≥321); same-run AGREE seed0 $1.015 / seeds-mean $0.867 vs model seeds-mean $1.034 → per-fill parity in MC noise; v2 total +$383 vs +$284 | ✅ letter-pass → paper twin |
| 06-10 | OP4 G3 near-strike fade | future $5 seed0 | 530 sig / 271 lg fills | 48.3% | lg +5.865, v2 +6.193 | lg [+4.496,+7.295] | Jaccard vs fav_disagree 0.101; 9/9 future days +, top-day 22%; 4/4 coins +; both sides +; dev/holdout lg +4.10/+2.70; ask p50 0.23, ceiling 0.40; adverse-selection caveat → paper first | ✅ PASS → deploy-candidate (paper) |
| 06-11 | EA1 atlas build | dev+holdout, 1512-cell grid | 181k obs / 6.6k win | — | — | — | 1247 non-empty cells; BH family 767; pre-gates +48/−117; after BH-10% candidates +24/−81 | 🔄 pre-registered reveal |
| 06-11 | EA1 future reveal | future, candidates only | 105 cells | — | net %/$1 | per cell | +15 confirmed (11 strong) / −61 confirmed (40 strong); 9 failed positives all FAV shallow-lead | ✅ atlas done |
| 06-11 | EA1 early-window cheap-disagree family | future (post-reveal pooled, descriptive) | 1279 win | 49.9% | +20.9%/$1 | [+15.2,+26.4] | dev +27.2 [+20.2,+34.2] n900; holdout +11.3 [+3.3,+19.5] n633; 9/9 fut days ≥0; 4/4 coins +; cov_max 0.30, fav_disagree 0.04; top-depth ~$10 | ✅ NEW edge → fills_live re-validation next |
| 06-10 | B3 fetch coverage | 05-22→06-09, 4 syms | 76 files | — | — | — | 100.00% of seconds every symbol (6.57M klines); 1s-kline path throughout; polite single-host fetch ~45 min | ✅ PASS |
| 06-10 | B1 composite vs print | future reveal | 153 win ≤2bps | — | — | — | [CORRECTED — the two rows below supersede an earlier append sourced from a garbled background-event stream (n=308/comp(w=0.7)/cut−1%/B2-FAIL-at-0.610) that does NOT reproduce on the repo data; verified numbers reproduced 3× incl. a clean foreground rerun.] PRIMARY (vs CL_strike): cb 22.9% vs chosen bn(w=1.0) 29.4% = cut **−29%** (worse; paired 14 vs 24); own-strike 35.9→31.4% (−13%, < 25% gate); ≈37% benchmark reproduces (cb own-strike 35.9%). \|proxy−CL_close\| future T=0 med: cb 2.88 / bn 5.10 / c.5 3.53 bps — cb wins every horizon + coin. Mechanism: BN −12.1..−12.5bps USDT basis (drifts); CL print = 33s-heartbeat snapshot ~25s behind spot (matches OP1) | ❌ honest negative |
| 06-10 | B2 dual-gate w/ composite | future $5 seed0 | 423→372 sig / 131→116 lg fills | 84.5 vs 79.4% | lg +0.843 vs +0.634; v2 +0.955 vs +1.021 | lg [+0.437,+1.249] vs [+0.199,+1.062] | letter-PASS (EV≥0.63 ✓ CI>0 ✓ flip 15.5<20.6 ✓) but robustness WASH: seeds0–4 +0.84 vs +0.87; v2 totals $229 vs $284 (baseline ahead, −38 fills); paired Δ 41 common fills +$0.310 [−0.037,+0.699]; overlap 271 common/152 base-only/101 comp-only (bn re-times entries); chooser margin 0.4pp = tie-zone; B1 mechanism failed → ship rule says NO | ⚠️ letter-pass, NOT shipped — keep Coinbase live |
| 06-11 | LC sweep+select (Phases 1–3) | dev-gated, future-blind; future=06-05..09 | 2,423 specs | — | — | — | 1,773 screened / 947 passed gates / 24 shortlisted (5 psettle, 5 e4, 5 zscore, 5 vol, 2 momentum, 2 det); psettle fade-band rediscovery 27/36 gate-passers (LC3 sanity ✓); 4 shards ~82 min | ✅ funnel complete |
| 06-11 | LC psettle_2246 (+4 twins) | future $5, v2 + live seed0 | 543 dec / 115 live fut fills | 74.8% | live +2.00, v2 +1.62 | live [+1.338,+2.641], v2 [+1.086,+2.110] | seeds0–4 $1.61±0.29; live dev +1.59/holdout +1.58 both CI>0; 5/5 days +, 4/4 coins +, top-3 wins 7% of total; maxJac 0.119 (extended sets incl. fade); avg fill 0.52; v2 fill 100%@$50; ~17 fills/day; ALL 5 region twins pass both models; caveat: p_settle dev-fit ⇒ dev split model-in-sample (holdout/future load-bearing) | ✅ **deploy-paper-candidate (NEW edge — the campaign's one discovery)** |
| 06-11 | LC e4_1068/1070 = fav_disagree dist≥5 widening | future $5, both models | 792 dec / 159 live fut fills | 61.0% | live +2.21 / +1.64; v2 +1.54 / +1.53 | live [+1.188,+3.281] / [+0.532,+2.822] | covers 94.5% of deployed fav_disagree decisions at 2.2× volume; deployed config itself on same block: live +0.80 [−0.16,+1.76] — widening beats it; 5/5 days +, 4/4 coins +, top-3 38% of total | ✅ replication of deployed edge → duplicate-of-known; dist 10→5 widening = candidate for its own pre-registered test |
| 06-11 | LC rejects roll-up | future $5, both models | 24 specs | — | — | — | zscore_1822 letter-pass +$18.4/fill [+0.17,+50.8] but 1 BTC knife-window $495 = 90% of future total, 3/5 days, twin 1821 fails → LOTTERY reject; momentum_1345 letter-pass but 68% det_lwd-contained + E5 negative → det overlay; vol_1616/1624 = vol-sliced fav_disagree (cover 76–86%); e4_1076 ⊂ fav_disagree 98%; det_0066/67 + 10 others fail live CIs (live hazard+latency kills late-window det at $5) | ❌ honest negatives logged, none actioned |
| 06-11 | LC addendum: Jaccard vs atlas early-disagree family (tl450–900, D, cheap 0.30–0.45) | decision slug sets, all 24 specs | — | — | — | — | atlas set = 3,822 slugs (58% of healthy universe); every spec Jac ≤ 0.147 (psettle 0.06–0.12; cl<12 variant lower); containment 67–80% ≈ base rate | ✅ no LC4 verdict changes — psettle ≠ atlas family |
| 06-11 | SQR1 walk-fwd | future 06-05..09, $10 CL | v1 1228 / v2 776 | 53.3 / 50.3 | roll3 +0.60 / +0.85 | [+0.01,+1.24] / [+0.06,+1.65] | frozen v1 −0.47 [−1.15,+0.19] = the deployed-until-06-05 curve LOST OOS; paired roll3−frozen v1 +0.98 [+0.43,+1.52]; WCE 0.0703→0.0138; alarm τ=0.0414 catches drift 05-31, 5d early | ✅ adopt rolling+alarm |
| 06-11 | SQR2 regime gate | future, $10 CL | 1228→1207 | 53.3→53.0 | +0.603→+0.589 | gated [+0.008,+1.252] | dev-fit θ never triggers OOS (keep 98.3%); dev lift was tape-specific | ❌ honest negative |
| 06-11 | SQR3 concurrency | future, $10 CL | 1207/379/726 | — | +0.59/+0.68/+0.95 | — | chooser (d+h Sharpe) said None; future legs pass for C=1,2 (sd ratio 0.52/0.57, worst −10.7/−21.3 vs −42.4) | ⚠️ not shipped, revisit |
| 06-11 | SQR4 live re-judge | future $5 lg seed0 | 8 configs | 37–57 | −0.40..+0.97 | per config | registered final rescued_v2 +0.38 [−0.17,+0.95] fails CI+EV legs; family flat under live physics; CL settle −45..65% vs CB | ⚠️ needs-more |
| 06-11 | SQR4 post-reveal MC robustness (v1 variants) | future $5 lg seeds 0–4 + fixed-lat | ~600 fills/seed | 51–57 | v1_nogate $0.39±0.34; v1 $0.27±0.20 | fixed-lat v1_nogate 2s +0.51 [+0.12,+0.91] / 5s +0.39 [−0.06,+0.85] / 10s −0.01 | measurement on the ALREADY-revealed block (config set fixed pre-reveal; no selection); seed-0 +0.97 was a 1.7-sd hazard draw; gated-v1 seed0 top-3 wins = 118% of total (jackpot-carried) | ✅ outlier dissolved — needs-more stands |
| 06-11 | BC1 burst anatomy (disagree family) | FULL window, $5, v2 + lg seed0 | 363/258/603 dec (fav/fav045/early) | — | burst vs singleton EV | fav v2 +3.04 [1.87,4.28] vs +1.43 [−0.12,3.16] | bursts 39–50% of groups, 62–71% of decisions; pair agreement 81–87% vs ~50% null, p<0.001 ALL slices; P(win\|partner won) 85–87 vs 18–26%; worst uncapped group ≈ −$21 = the live incident shape; fav045×early share 55% of fav045's window-ts | ✅ bursts = ONE bet at N× size, +EV but pure tail risk |
| 06-11 | BC2 burst-cap policies | future 06-05..09, $5, v2 + lg seeds0–4 | 5 policies × 3 configs | — | — | — | fav045 max1_first keeps 98% lg total, worst −67%, dd −44% → **CAP (live rec)**; fav 0.90-band max2_first ≈100% total, worst −37% → cap the paper twin; early_disagree best policy misses bars (23.5% worst-cut < 25%; max1 keeps 62–71% < 80%) → **NO CAP**; max1_cheap = knife-selection (0.90-band total flips −$6.1); EXEC_BURST_CAP diff proposed, NOT applied (freeze) | ✅ per-config; patch proposal in doc §4 |
| 06-11 | BC3 capacity curves (6 configs × $5/10/25/50) | future 06-05..09, v2 + lg seeds0–4 | 486–1,321 dec/config | — | per stake | seed-0 clustered CIs | max viable (registered): lwd $25, dual NONE-at-$5 (borderline; keep $5), fav045 $50 (n=21, fragile), early $50, psettle $50, fade $50; binding constraint = fill rate (hazard −10–35% rel by $50), slip ≤3.4c; portfolio at max stakes ≈$1,796/day = impact-blind upper bound, $10-tier ≈$500/day; recommend stay $5–10 + escalate on realized/model ≥0.5 weekly gate | ✅ sizing map done; live record prices impact next |

---

## Oracle-print study (2026-06-10) — "predict the print, not the market"

Pre-registered 2026-06-10 BEFORE any future-block number was computed. Protocol: settlement model
fit on **dev** only; isotonic calibration fit on **holdout**; App-1 threshold θ\* chosen on **dev**
only; the **future** block (06-01→06-09) revealed ONCE at the end, as the headline.
Tooling: `research/analysis/oracle_print_model.py` (new). Trade battery = `rejudge_live_model`
pattern (`simulate_config`, **$5 stake, seed 0**, fill models **v2** and **live_guarded** with
`data/research/fill_model_live.json`). Ledger CIs = `research/lib/stats.py::
window_clustered_bootstrap` (slug-clustered, n=2000); for per-tick Brier-difference CIs an
algebraically equivalent per-slug-sum bootstrap is used (unit-tested to produce identical draws).
**Baseline to beat** (deployed `det_d12_dual_live` = dual config + binary AGREE gate, recomputed
in-process, same code path/seed): v2 future **+$1.02/fill [0.75,1.29]**; live_guarded future
**+$0.63/fill [0.20,1.06]**; n_signals 423 full (AGREE keeps ~81% of ungated volume).

Model spec (fixed pre-fit): logistic regression (sklearn, L2 C=1.0, features standardized on dev)
for P(cl_up | decision tick) on dev ticks with `book_healthy`, 1 ≤ `time_left_sec` ≤ 600 and CL
features present. Features (mechanistic transforms of the pre-declared information set
{cl_dist_bps, cl_oracle_age_s, cb_dist_bps, cl_cb_basis_bps, time_left_sec, realized_vol, coin}):
z_cl = clip(cl_dist_bps/(σ·√T), ±8) and z_cb likewise for cb_dist_bps (σ = 100·realized_vol in
bps/s, floored at 0.05); d_cl = clip(cl_dist_bps/10, ±10), d_cb likewise; basis =
clip(cl_cb_basis_bps/10, ±5); age_gap = min(cl_oracle_age_s,120)/60 · (z_cb − z_cl) (staleness
shifts weight to spot); coin one-hots (eth/sol/xrp vs btc). Rows with any NaN feature are dropped
(fail-closed, counted). Isotonic fit on holdout predictions; ADOPTED iff it improves **dev** Brier
(the isotonic's own OOS) by > 0.0005, else raw logistic kept.

| # | Edge / change | Mechanism (causal story) | Predicted effect | Decision rule | Status |
|---|---|---|---|---|---|
| OP1 | Characterize the Chainlink print process (rounds data, 4 coins, 05-22→06-10) | push oracle: deviation threshold + heartbeat ⇒ CL_close = CL_now + a bounded, partly CB-predictable correction | (CL_close−CL_now) dispersion grows with T; the concurrent CB-vs-CL gap explains a large share; sign-flips vs the CL strike concentrate at small \|cl_dist\| and large T | descriptive — no accept/reject gate | ✅ DONE. All 4 feeds ≈ **33s-heartbeat** oracles (p50 dt 33s, p99 46s; only 1–2% rounds missed by the 15s poll; no hard deviation floor above ~0.1–0.2bps). Print T-s out: sd 4.0/6.3/10.0/13.9/18.2 bps at T=30/60/120/180/300; concurrent CB-gap maps β≈0.85–0.96 into the print (T=30 R²=0.57, resid 2.6bps). Flip vs CL strike: 8→17% (T=30→300) overall, **17–36% at \|cl_dist\|<5bps**. Label convention (poll-asof vs rounds-asof) agrees 98.1%. |
| OP2 | Settlement-probability model P(cl_up \| tick features) | diffusion: P(stay on side) ≈ f(dist/(σ√T)); CB leads the print; staleness shifts weight to CB | better-calibrated than the book's own price | **G1**: on the FUTURE eval population (book_healthy, 1≤tl≤600, features+label present, one row per slug×sec), Brier(book yes_mid) − Brier(model) ≥ **0.01** AND slug-clustered CI lower bound of the paired per-tick diff > 0. Secondary (report): beats the AGREE 2-leaf predictor (leaf probs fit on dev) on Brier; reliability curve sane | ❌ **G1 FAIL** (honest negative). Future Brier: model 0.13456 vs book 0.13750 — diff **+0.00294 CI[+0.00010,+0.00588]**: real but 3.4× short of the ≥0.01 margin; book WINS logloss (0.4228 vs 0.4675) — model tails overconfident (pred 0.976→realized 0.912) while book tails are near-perfect. Secondary passes: model beats AGREE 2-leaf by +0.01402 [+0.01201,+0.01615]. Keep as gate/feature engine, not a calibration product. Isotonic correctly self-rejected (worsened dev). |
| OP3 | App 1 — continuous gate: det_d12 dual config WITHOUT the agree gate, trade iff P_settle(buy side) ≥ θ\* | binary AGREE throws away ~19% of volume incl. good trades; a calibrated threshold keeps EV and recovers volume | volume ≥ AGREE at equal-or-better EV/fill | **G2**: θ\* from DEV only (grid 0.50–0.95 step 0.025; maximize dev TOTAL PnL under live_guarded, seeds 0–4 mean, s.t. dev EV/fill ≥ AGREE's dev EV/fill; fallback argmax dev EV/fill). PASS iff future live_guarded (seed 0) EV/fill ≥ **$0.63** AND its CI lower > 0 AND future n_signals ≥ AGREE's future n_signals. v2 + seeds-0–4 mean reported alongside | ✅ **PASS by the registered letter** (θ\*=0.575 from dev; future lg seed-0 $+0.880 [+0.513,+1.231] ≥ $0.63; **n_sig 402 vs AGREE 321, +25% volume**; v2 $+1.095 vs AGREE $+1.021, total +$100/+35%). Caveat: same-run AGREE seed-0 recompute is $+1.015 (seeds0–4 $0.867 vs model $1.034) — per-fill EV is parity-within-MC-noise; the robust gain is volume+total. → paper twin, NOT a live-gate swap. Battery sanity reproduced the jsonl baseline exactly. |
| OP4 | App 2 — near-strike fade (inverse-det): fav_ask ≥ 0.75 AND P_model(fav wins) ≤ 0.60 AND time_left 60–360s AND book_healthy AND cheap-side ask ≤ 0.35 → buy the CHEAP side at its ask (fill ceiling 0.40); first qualifying tick per window | book overprices near-locked favourites when the oracle z-score says coin-flip; the cheap side is then underpriced | cheap side +EV; mostly NEW volume vs fav_disagree | **G3**: future live_guarded EV/fill CI lower > 0 AND n_fills(future) ≥ 30 AND Jaccard(decision slugs vs `fav_disagree` decision slugs) < 0.5 (else it is the same edge re-labelled). v2 + entry-ask capacity distribution reported | ✅ **G3 PASS — strongest result of the study.** Future ($5): lg **$+5.87/fill [+4.50,+7.30]**, 271 fills of 530 sig, WR 48.3%; v2 $+6.19 [+4.95,+7.54] 508 fills; **Jaccard vs fav_disagree 0.101** (new volume). Splits stable: dev +$4.10 / holdout +$2.70 lg. Post-hoc scrutiny clean: 9/9 future days + (top day 22%), 4/4 coins +, both sides +, all ask buckets + (sub-0.10 tail = jackpot zone, 40% of total). Caveat: random zero-fill hazard is optimistic for a fade (adverse selection) → PAPER first. Entry ask p50 0.23, ~70 dec/day. |

Verdict bands (per application, fixed before reveal): **deploy-candidate** = its gate passes in
full; **paper-only** = future EV positive but ≥1 gate leg fails; **reject** otherwise.

---

## EDGE ATLAS (2026-06-10) — systematic miscalibration map, pre-registered BEFORE any future-column compute

**Registered 2026-06-10, before `--build` was first run on real data** (unit tests on synthetic
frames only). Tooling: `research/analysis/edge_atlas.py` (new) + `tests/research/test_edge_atlas.py`.
Data: `edge_lab.load_base()` healthy ticks (5.09M, dev 05-23→27 / holdout 05-28→31 / future
06-01→09), settled on **Chainlink** (`cl_outcomes`). Artifact: `data/research/edge_atlas/`.

**Observation unit.** One observation per (slug × cell) = the FIRST tick of the window that lands
in the cell (a window may enter many cells, once each ⇒ within a cell, cluster ≡ row). Ticks with
no Chainlink settlement label are dropped (counted).

**Grid (fixed).** side ∈ {BUY-FAVOURITE ask 0.50–0.95, BUY-CHEAP-SIDE ask 0.05–0.50} in 0.05 bins
(9 each; cheap ask of a YES-fav = 1−yes_bid, of a NO-fav = yes_ask, per edge_lab convention) ×
time_left {(0,30],(30,60],(60,120],(120,180],(180,300],(300,450],(450,900]} × |cl_dist| (per-tick
Chainlink vs the poll-asof CL strike at window open, same basis as `cl_outcomes`)
{[0,2),[2,5),[5,12),[12,25),25+} bps + NaN-bin × consistent/disagree = **1512 possible cells**,
coins pooled (per-coin only for finalists).

**Economics (cell-level cost approximation, stated limitation).** Edge per $1 staked =
E[won/cost]−1; gross cost = displayed best ask; net cost = ask + 0.0072 (live-calibrated clean-fill
slippage `fill_model_live.json::mean_slip_filled`); **zero fees** (live pays none). NO ladder walk /
zero-fill hazard at atlas granularity — survivors must be re-validated under the full live fill
model before any deploy. Capacity reported as the side's median displayed top-of-book depth ($).

**Statistics.** Dev CI = window-clustered bootstrap (canonical `research/lib/stats.
window_clustered_bootstrap` law; computed via the draw-identical fast equivalent, equality
unit-tested) n=3000 seed 0, 5/95%. Dev p-values: one-sided bootstrap (n=10000 seed 1, add-one
smoothed; iid = clustered here since one obs/window per cell), both tails.

**Acceptance bar (fixed in advance).**
- POSITIVE candidate: dev CI-lower(net) > 0 AND holdout net point > 0 AND n_dev ≥ 40, AND survives
  Benjamini–Hochberg FDR 10% on the positive-tail dev p-values across the family = ALL cells with
  n_dev ≥ 10 (both grids pooled).
- NEGATIVE (fade/avoid) candidate: symmetric (dev CI-upper < 0, holdout < 0, n_dev ≥ 40, BH 10% on
  the negative tail).
- ONLY candidates get the FUTURE column revealed, once (`--reveal-future`; artifact flag blocks
  re-reveal). **Future-confirmed** = future net point agrees in sign AND n_future ≥ 30; **strong**
  = future 90% CI excludes 0. Cells correlate (shared windows/macro moves) ⇒ BH is PRDS-grade
  control, not independent-test exact — stated, accepted.
- Harvested overlay (reveal-safe: dev+holdout slugs only): coverage of each cell's windows by each
  existing decision set (rejudge CONFIGS det_lwd_live, det_d12_wide_v1, det_d12_dual_live,
  fav_disagree, fav_momentum, fav_lowvol, fav_deepdown; near-strike fade region fav_ask≥0.75 &
  p_settle(fav)≤0.60 & tl 60–360; sq ledger slugs). **UNHARVESTED** = max coverage < 0.5. The
  NEW-EDGE shortlist = unharvested ∧ future-confirmed, ranked by future net edge.

| # | Edge / change | Mechanism (causal story) | Predicted effect | Decision rule | Status |
|---|---|---|---|---|---|
| EA1 | Edge Atlas: exhaustive (side×ask×tl×cl_dist×consistent) miscalibration grid vs real costs, with a harvested-overlay against deployed/candidate strategies | the book's miscalibration is structural by region (favourite-longshot bias, oracle lag near strike), so mapping ALL cells at once under one FDR finds what hypothesis-by-hypothesis hunting misses, and the overlay shows which +EV regions are NOT yet harvested | known edges re-appear as their cells (det→FAV/C/short-tl/large \|cl_dist\|; E4/fav_disagree→CHEAP/D; OP4 fade→CHEAP near-strike); plus ≥1 unharvested future-confirmed cell-group | bar above (dev CI-lower>0 + holdout>0 + n_dev≥40 + BH-10%/tail; future revealed once for survivors only; confirmed = sign-agree & n_fut≥30, strong = future CI excl. 0) | ✅ **DONE (2026-06-10/11).** Grid 1512 cells, 1247 non-empty, 767 tested, 445 with n_dev≥40. Candidates after FDR: **+24 / −81**; future revealed once: **+15 confirmed (11 strong) / −61 confirmed (40 strong)**; 9 dev-positive FAV shallow-lead cells failed forward (honest). All sanity anchors recovered (det, E4, OP4-fade, longshot bias). **NEW unharvested family: early-window cheap-disagree** (tl450–900, buy spot side at 0.30–0.45, \|cl_dist\|<12, 6 contiguous FDR-surviving cells, cov_max≤0.45, fav_disagree cov 0.04): pooled dev +27.2% [+20.2,+34.2] n=900 / holdout +11.3% [+3.3,+19.5] n=633 / **future +20.9%/$1 [+15.2,+26.4] n=1279, 9/9 days ≥0, 4/4 coins +** (btc +20/eth +22/sol +28/xrp +14). Fade zones future-strong: cheap-C longshots vs ≥5bps CL lead (−31…−50%), FAV-D early (−18…−31%), FAV-C 0.70–0.80 @cl<2 tl300–450 (−18…−20%). Caveats: slippage-only cost model (no zero-fill hazard), top-depth ~$10/window, window-overlap coverage = upper bound. Next: full fills_live + latency-survival re-validation of the family → paper twin. Full detail: docs/research/EDGE_ATLAS_2026-06-10.md |

---

## Live-cost hypothesis campaign (2026-06-10) — re-hunt under real fill physics

Pre-registered 2026-06-10 BEFORE the sweep was run (no future-block number of this campaign
existed at registration; only a decision-count plumbing check was executed). Re-runs the
systematic hypothesis hunt (`research/analysis/hypothesis_{sweep,select,verify}.py`, 2026-06-05
vintage) with three changes:
1. **Future block re-based to 2026-06-05..06-09** (06-09 partial to ~05:00 UTC, 76/384 windows;
   1,610 future windows): 06-01..06-04 was burned by the 06-05 campaign's Phase-4 reveal, so those
   days are relabeled `holdout` for this campaign. Local override only
   (`hypothesis_sweep.set_future_override("2026-06-05")` / `--future-start`); `clean_window.py`
   NOT rewritten; dev stays 05-23..27 so Phase-3 gates see the same dev block as the 06-05 run.
   Post-override windows: dev 1,920 / holdout 3,063 / future 1,610.
2. New **model-divergence family `psettle`** (LC3 below).
3. Phase-4 verification runs TWICE per shortlisted spec — fill model **v2** (idealized fixed-2s L2
   walk) and **live** (`research/sim/fills_live.py`, `data/research/fill_model_live.json` live-1:
   per-trade sampled empirical latency, zero-fill hazard by depth×time-left, kappa haircut,
   guarded band floor entry_ask−0.04; ceiling = family ask_hi else entry_ask+0.07 capped 0.92;
   entry_ask = best in-band ask on the SIGNAL-second ladder) — both at **$5 stake**; live seed-0
   headline + seeds 0–4 future mean/sd. Extended Jaccard vs deployed decision sets
   (`rejudge_live_model.decisions_for`: det_lwd_live, det_d12_wide_v1, det_d12_dual_live,
   fav_disagree, fav_lowvol, fav_momentum, fav_deepdown) + the OP4 fade-region slug set.

**Honesty caveats, declared up front.** 06-05..09 is fresh w.r.t. this pipeline's own mechanical
selection (which never reads a future field), but NOT virgin everywhere: (a) dual-oracle lever
choices (DUAL_ORACLE_2026-06-09) used future=06-01..09 in the det_d12 region; (b) OP3/OP4 above
revealed 06-01..09 for the det-d12-nogate gate region and the near-strike-fade region; (c) the
live fill model's parameters are calibrated on live attempts from 06-05..09 (cost physics only —
it never sees outcomes/direction). Survivors overlapping those regions are REPLICATIONS, not
discoveries — adjudicated by the Jaccard column. Also kept as-is (so gates stay byte-identical to
06-05): the CPCV estimator folds over ALL clean days incl. future ones.

Phase-3 selection gates AS-IS, future-blind, via the `livecost_select.py` path-wrapper: screened
(n≥20, dev_ev>0, full_ev>0) → n≥40, dev_n≥12, dev_ev>0, FULL clustered-CI lower>0, CPCV≥80%
positive folds, latency-5s>0 AND latency-10s>0, cap10≥0.90; diversity ≤5/family, top 24. Sweep
economics unchanged (joined-book best-ask at 2s, $10 stake, Chainlink resettle). Artifacts:
`data/research/hypotheses/livecost_2026-06-10/` (06-05 campaign artifacts left untouched).

| # | Edge / change | Mechanism (causal story) | Predicted effect | Decision rule | Status |
|---|---|---|---|---|---|
| LC1 | Wire `fill_model="live"` into `hypothesis_verify` (the deferred Phase-3 hook) | 06-05 survivors were judged under an idealized fill; the live record shows ~44% zero-fill hazard + 0.5–20s latency — EV must be re-judged under deployed-executor physics | most v2-positive shortlist EVs shrink under live; some die | engineering gate: synthetic-ladder smoke tests pass; default (no flags) path byte-identical (v2 code path, rng stream, output keys unchanged) | ✅ wired; 8/8 smoke tests (tests/research/test_hypothesis_verify_live.py) |
| LC2 | Future-split override 06-05..09; 06-01..04 → holdout | reusing a once-revealed block as "future" grades survivors on a peek | n/a (protocol) | campaign-local override only; dev identical; caveats (a)–(c) stated | ✅ implemented (`--future-start`) |
| LC3 | NEW family `psettle` (324 specs): p = `p_settle_side` (OP2 model, fit on DEV ticks only) vs the book's executable ask of that side; buy when p − ask ≥ d. Grid: side ∈ {fav, cheap, either} × d ∈ {0.10,0.15,0.20,0.25} × tl ∈ {(30,180),(60,360),(120,420)} × ask ∈ {(0.05,0.35),(0.50,0.78),(0.05,0.90)} × \|cl_dist\| floor ∈ {0,5,12} | the book prices the COINBASE story; settlement is the CHAINLINK print — a dev-calibrated print model flags ticks where the book's side price sits far from P(side wins); generalizes the OP4 fade (future +$5.87/fill) | the family REDISCOVERS the fade region (cheap side, low ask band, tl 60–360, d≥0.10–0.15) — wiring sanity, not a discovery claim (pre-checked on decision counts only: 99.6% of fade slugs covered); the discovery surface is the new corners (fav side, d≥0.20, cl floors, other tl/ask bands) | standard Phase-3 gates, then LC4 bands | ✅ DONE. 324 specs ran; 279 screened-in; fade region REDISCOVERED (27/36 fade-band specs passed full gates, dev up to +$26.8/tr@$10) ⇒ wiring sound. Shortlist took 5 psettle — all from the NEW cheap-side **ask 0.50–0.78** corner (not the fade band): the model overruling the book's lean on ambiguous wide-spread mid-window books. All 5 passed Phase 4 under BOTH fill models (see LC4 + doc). |
| LC4 | Phase-4 verdict bands (fixed before reveal) | — | most shortlisted specs die under live physics — that is the discipline working; 1–2 genuine survivors is a great outcome | **deploy-paper-candidate** = future EV > 0 under BOTH fill models AND live future CI-lower > 0 AND live future n_fills ≥ 30 AND max Jaccard < 0.5 vs all known sets; **duplicate-of-known** = passes those EV legs but max Jaccard ≥ 0.5 (or a parameter-twin of a deployed config); **reject** otherwise | ✅ RESOLVED (2026-06-11). Funnel 2,423→1,773 screened→947 gates→24 shortlist→12 mechanical live-band passes→**1 new edge region + 1 replication**. NEW: `psettle_2246` (cheap side, ask .50–.78, tl 60–360, d≥0.15) live future **+$2.00/fill [+1.34,+2.64]** n115 WR75%, v2 +$1.62 [+1.09,+2.11], seeds $1.61±0.29, 5/5 days + 4/4 coins +, top-3 wins 7% of total, maxJac 0.119, twins 2220/2228/2229/2255 ALL pass → deploy-paper-candidate (ONE config). REPLICATION: e4_1068/1070 = fav_disagree dist 10→5 widening (covers 94.5% of the deployed set), live future +$1.64/+$2.21 CI>0 → duplicate-of-known but fresh OOS evidence for the deployed edge + a registered-test-worthy widening. REJECTED with reasons: zscore_1822 letter-passes at +$18.4/fill but ONE $495 BTC knife-window = 90% of its future total + twin fails → lottery; momentum_1345 letter-passes but 68% det_lwd-contained + E5 negative → det overlay; vol_1616/1624 = vol-sliced fav_disagree; e4_1076 ⊂ fav_disagree (98%); 12 others fail the live band outright (incl. both det specs — live hazard+latency eats the late-window det margin at $5). Full table: docs/research/SWEEP_LIVECOST_2026-06-10.md |

---

## Burst-cap + capacity study (2026-06-11) — multi-coin burst risk for the disagree family + stake-capacity curves

Pre-registered 2026-06-11 BEFORE `research/analysis/burst_cap.py` / `research/analysis/capacity_curves.py`
first ran on real data (pure helpers unit-tested on synthetic frames only). Research + proposal only —
NO live code changes (`scripts/live_executor.py` is FROZEN mid-A/B until ~Jun 13; any cap ships later as a
flag-gated patch from the written proposal).

**Motivation.** fav_disagree_live's first live day lost **$10.56 in ONE simultaneous 3-coin burst**
(window 1781113500: btc/eth/sol DOWN, all knife fills 0.25→0.16 / 0.23→0.14 / 0.40→0.26). Memory
([[sq-variance-macro-correlated]]) says cross-coin "diversification" here is illusory — one macro move
= N correlated fills. Question: should a live strategy cap simultaneous same-window-timestamp fills?

**Data + fill models (shared by BC1–BC3).** `edge_lab.load_base()` with the LC2 split override
(`hypothesis_sweep.set_future_override("2026-06-05")`): dev 05-23..27 / holdout 05-28..06-04 /
**future 06-05..09** (06-09 partial; 1,610 window-ts×coin slugs ≈ 4.19 day-equivalents at 384/day).
Fill models per `rejudge_live_model.simulate_config`: **v2** (idealized fixed-2s full-L2 walk) and
**live_guarded** (`fill_model_live.json` live-1: sampled latency, zero-fill hazard, kappa, guard floor
entry−0.04, ceiling = config max_ask), **$5 stake**, live seed-0 CI headline + seeds 0–4 mean±sd.

**Honesty caveat, declared up front.** The 06-05..09 block is NOT virgin for these regions (fav_disagree
+ early-disagree + psettle + fade + det baselines were all revealed on it 06-10/11). BC1–BC3 therefore
claim NO new edges: they measure **risk shape** (burst correlation) and **execution capacity** (stake
curves) of already-validated edges on the freshest block. Decision rules are fixed in advance so the
cap/sizing choice itself stays honest. Known fill-model limitations inherited: zero-fill hazard applied
randomly though live misses are adversely selected (EV = optimistic bound); hazard's depth-ratio bins
scale with stake but kappa/latency were calibrated on $5 attempts (246) — stake extrapolation stated,
not hidden.

**Configs.** Disagree family for BC1/BC2 (decision frames via `rejudge_live_model.decisions_for`):
`fav_disagree` (CONFIGS: disagree, tl 120–360s, |dist|≥10bps, ud_ask 0.05–0.90), `fav_disagree_live045`
(the DEPLOYED live band: ud_ask 0.05–0.45 — the incident config), `early_disagree` (deployed
early_disagree_live params: disagree, tl 450–900s, |dist|≥10bps, ud_ask 0.30–0.45). BC3 adds:
`det_lwd_live`, `det_d12_dual_live` (AGREE gate via `_apply_dual_gate`), `psettle_2246` (livecost
specs.jsonl params via `fam_psettle`), OP4 near-strike fade (`oracle_print_model.fade_decisions`).

| # | Edge / change | Mechanism (causal story) | Predicted effect | Decision rule | Status |
|---|---|---|---|---|---|
| BC1 | Burst anatomy of the disagree family: per-window_start_ts firing groups; a BURST = ≥2 coins firing the SAME strategy at the same window_start_ts | one macro spot move crosses several coins' strikes at once → same-signed disagreement signals fire together; outcomes share the macro leg so they win/lose together (the live incident was 3 correlated knife fills) | bursts are a material share of decisions; within-burst outcome agreement ABOVE the independence null; burst-member EV ≤ singleton EV (knife-y tape) | descriptive — no accept/reject gate. Report: % of window-ts groups / decisions in bursts, group-size histogram; pairwise within-burst win-agreement vs a permutation null (outcomes shuffled across fills, n=2000, p-value) + P(win\|partner won) vs P(win\|partner lost); burst vs singleton EV/fill under both fill models; worst window-ts group P&L. Cross-strategy same-window overlap (fav_disagree × early_disagree) reported descriptively | ✅ DONE. Bursts = 39–50% of firing groups / 62–71% of decisions (sizes up to 4 coins). Outcome correlation MASSIVE: pair agreement 81–87% vs 50–54% null, p<0.001 (permutation floor) in all 6 config×model slices; P(win\|partner won) 85–87% vs P(win\|partner lost) 18–26%. Prediction "burst EV ≤ singleton" WRONG in the interesting direction: burst EV ≥ singleton everywhere (fav_disagree v2 +$3.04 [1.87,4.28] vs +$1.43 [−0.12,3.16]) — a burst is ONE good bet at N× size; the cost is pure tail: worst uncapped group ≈ −$21 at $5 (= the live −$10.56 incident shape at N=4). fav045×early share 88 window-ts (55% of fav045's groups) → global cross-book per-window-ts cap flagged as future work. Doc §2 |
| BC2 | Intent-level multi-coin burst-cap policies, applied to the decision frame BEFORE fill simulation (mirrors an `Executor._blocked` gate): **uncapped** vs **max1_first / max2_first** (keep earliest entry_sec; ties→lower entry_ask→slug) vs **max1_cheap / max2_cheap** (keep lowest entry_ask; ties→earlier entry_sec→slug), per strategy per window_start_ts | if burst members are near-duplicates of one macro bet, dropping all but 1–2 sacrifices little EV (correlated outcomes ⇒ the marginal burst member adds variance, not independent edge) while cutting the worst-window tail ~N× | cap retains most of total P&L and cuts worst-window loss / drawdown materially; cheapest-ask tie-break may select INTO knives (adverse) — first-by-time is the executor-realistic default | **RECOMMEND a cap iff, on the FUTURE block under live_guarded (seeds 0–4 mean), some policy (i) cuts the worst window-ts group loss ≥25% vs uncapped AND (ii) retains ≥80% of uncapped total P&L, AND v2 agrees in direction on both legs.** If uncapped future total < a cap policy's total under BOTH models (bursts net-negative), recommend on EV grounds directly. Among qualifiers pick max future total (tie-break: smaller worst window). Else: honest negative — no cap, bankroll/day caps stay the only brakes. Per config; the LIVE recommendation keys off fav_disagree_live045 + early_disagree. If a cap wins → flag-gated `_blocked` patch PROPOSED as a diff in the doc (not applied) | ✅ RESOLVED (per config). **fav_disagree_live045 (LIVE): CAP max1_first** — keeps 98% of lg future total (64.6 vs 66.1 seeds-mean) while cutting worst group 67% (−16.0→−5.3) + dd 44% (28.9→16.3); v2 agrees (83% kept, worst −21.3→−5.3). **fav_disagree 0.90 paper: max2_first** (≈100% total within seed noise, worst −37%). **early_disagree: NO CAP (honest negative)** — best policy max2_first cuts worst only 23.5% (<25% bar); max1 keeps only 62–71% (<80% bar); early bursts enter minutes apart + hazard already de-correlates them. Mechanics: cheap tie-break selects INTO knives (max1_cheap flips 0.90-band future total NEGATIVE −$6.1) — keep-first (arrival order) confirmed as the right tie-break; attempt-caps interact with the ~50% zero-fill hazard (later siblings = free retries), so max1 hurts heterogeneous wide-band bursts (−73% total) but not the homogeneous 0.45 band. Flag-gated `EXEC_BURST_CAP` patch PROPOSED (doc §4, not applied): deploy `EXEC_BURST_CAP=1 EXEC_BURST_CAP_SIDS=fav_disagree_live` post-freeze. NOTE: first run had a sign bug in decide()'s worst-cut leg — fixed, 3 unit tests added, re-run byte-identical numbers (md5-checked) |
| BC3 | Capacity curves: stake ∈ {$5,$10,$25,$50} × {v2, live_guarded seeds 0–4} for det_lwd_live, det_d12_dual_live, fav_disagree_live045, early_disagree, psettle_2246, OP4 fade — fill rate, EV/fill (window-clustered CI), EV/signal, avg slippage vs signal-tick ask | edges are rent on thin books (~$10 median touch depth): larger stakes walk deeper levels (slippage↑), trip the depth-ratio zero-fill hazard (fill rate↓), and partial-fill more — EV/fill degrades with stake; the degradation point differs by config (det books deeper than disagree/fade books) | EV/fill roughly flat $5→$10, visibly degraded by $25–50 for the thin-book configs (disagree/fade); det family holds longer | descriptive measurement, bands fixed in advance: **MAX VIABLE STAKE per config = largest stake with future live_guarded seed-0 EV/fill CI-lower > 0 AND seeds 0–4 mean > 0** (v2 reported alongside; no-pass at $5 ⇒ "none — not stake-ready", flagged). Portfolio $/day = Σ over configs of (future fills/day × seeds-mean EV/fill) at max-viable stakes, with the macro-correlation caveat (NOT independent books) and pairwise decision-slug overlap reported | ✅ RESOLVED. Max viable stakes (registered rule): det_lwd **$25** (+$2.77±0.57/fill, $50 fails CI −0.63); det_d12_dual **none at $5** (seed-0 CI spans 0 at every stake on this 4.19d block; seeds-mean +0.67±0.22 positive; campaign's independent seed-0 read +0.92 [0.40,1.41] passed → borderline, keep $5, do NOT size up); fav_disagree_live045 **$50 letter-pass on n=21 fills** (sd ±$17.9 — fragile, treat as "no degradation detected"); early_disagree **$50** (+$21.1±9.3, CI-lower +8.7); psettle_2246 **$50** (cleanest curve, v2 ~100% fill at $50); fade_op4 **$50** (+$41.7±9.6). Prediction half-right: per-$ EV only dips ~10–25% by $50 — the binding constraint is FILL RATE (stake-aware hazard cuts live fills 10–35% rel. $5→$50; v2 deep-walk 77→50% on early), slippage stays ≤3.4c (the unfilled>50% drop rule converts capacity into missed volume, not paid spread). Portfolio at registered stakes ≈ **$1,796/day = impact-blind UPPER BOUND** (model can't price being the book at $25–50 on ~$10-touch-depth books, random hazard vs adversely-selected misses, $5-calibrated kappa/latency); conservative $10-tier ≈ **$500/day**; recommended: stay $5–10, escalate one rung per forward week with realized EV ≥ 0.5× model. Pairwise Jaccard 0.08–0.22. Doc §5–6 |

Artifacts: `data/research/burst_cap/` + `data/research/capacity_curves/` (jsonl), doc
`docs/research/BURST_CAPACITY_2026-06-11.md`, tests `tests/research/test_burst_cap.py` +
`tests/research/test_capacity_curves.py`.

---

## SQ RESCUE (2026-06-11) — registered BEFORE any future-block compute of this campaign

**Mission.** The stale-quote family (`det_sqp_v1/v2`, biggest paper earner: v1 +$1,158/1,924 tr,
v2 +$1,648/1,345 tr as of 06-11) has no live path due to three blockers: (1) frozen-curve drift
(A3: 8.4× calibration error, +5.6pp Up-bias by 06-04; the deployed curve was hand-refit ONCE on
06-05 — `stale_quote_curve.json` note; the original is the `.bak`, fit 05-23..29), (2) regime
bleed (lwd↔sq anti-correlation, macro-correlated 4-coin variance), (3) execution fragility (A2,
2026-06-04: realistic fills kept 43% of EV, CI crossing 0, sub-5s latency — measured BEFORE the
live-calibrated fill model existed). Fix what's fixable, judge under `fills_live` live_guarded,
deliver a shadow-live verdict.

**Protocol (fixed in advance).** Tooling: `research/analysis/sq_rescue.py` (new) +
`tests/research/test_sq_rescue.py` (pure helpers, synthetic frames only before first real run).
Base = `edge_lab.load_base()` (05-23..06-09, 06-09 partial to ~05:00 UTC); z per
`loss_patterns._sq_prep`; decision band tl∈[60,840], book_healthy. **Campaign splits (LC2
convention): dev 05-23..27 / holdout 05-28..06-04 / future 06-05..09**, future revealed ONCE
(06-01..04 burned by earlier reveals → holdout here). Declared prior exposure of 06-05..09: the
fill model's COST params are calibrated on it (physics only; never sees outcomes/direction); no
sq-family entry-rule EV has ever been computed on it. Decision builder = `StaleQuoteState` live
semantics (first qualifying tick/slug; margin ≤ |model_p−mid| ≤ 0.30; |vel10| ≥ 8; ask-band
[0.05,0.95] + depth≥stake pre-checks at the DECISION tick; v2 = margin 0.12 + dist≤19bps; exits =
hold-to-resolution — verified 3,269/3,269 paper sq trades exit_reason="resolution", so NO
early-exit modeling needed; the $1/share settle convention is ~$0.04/win optimistic at $5,
stated). Builder reconciliation vs `sq_full.parquet` (dev+holdout only) reported before any
future compute; known deltas: band/depth checked at decision tick (live parity) vs at fill tick
in the old builder. Walk-forward arms: $10 stake, L2 `walk_buy` at entry+2s, drop if
unfilled>50% (continuity with sq_full). Final re-judgment: `rejudge_live_model.simulate_config`
pattern at **$5**, `live_guarded`, seed-0 headline + seeds 0–4 mean±sd, **ceiling =
min(entry_ask+0.07, 0.95)** (executor chase band; the family 0.95 max_ask is an entry filter —
a 0.95 ceiling on a $0.20 entry would be an absurd chase; registered here, before results),
guarded floor entry−0.04, hazard+kappa from `fill_model_live.json` (live-1, 246 attempts),
latency sensitivity at FIXED {2,5,10}s replacing the sampled distribution. Settlement:
**Chainlink primary** (`cl_outcomes` — the oracle that pays; slugs without cl outcome dropped +
counted), Coinbase reported for paper parity. CIs: `window_clustered_bootstrap` n=2000 seed 0,
slug-clustered (repo convention) AND epoch-clustered (cluster = window_start_ts; sq variance is
macro-correlated, slug clusters understate) — gates use slug-clustered for comparability; a gate
that passes slug- but fails epoch-clustered downgrades the verdict one band. Memory constraints
honored: NO tight daily caps (H4), NO min-price floor (H3); shaping tools = regime gate +
concurrency cap only. Knob-fitting discipline: SQR2 θ/feature on dev ONLY; SQR1 cadence/K and
SQR3 C on dev+holdout (walk-forward is mechanically leakage-safe; selection never touches
future); all knobs frozen into `data/research/sq_rescue/knobs.json` BEFORE the single
`--stage reveal` run computes any future number.

| # | Edge / change | Mechanism (causal story) | Predicted effect | Decision rule | Status |
|---|---|---|---|---|---|
| SQR1 | Rolling 3-day curve refit vs frozen. Per-UTC-day curve = `_fit_curve` (25 quantile bins, monotone) on trailing-K-day band ticks (target `outcome_up_clean`, deployed parity; <200 finite-z ticks → carry previous curve). Arms on future: (a) frozen-original (.bak, what v1/v2 traded until 06-05), (b) as-deployed (.bak through 06-04, the 06-05 refit after — what the paper bot actually ran), (c) rolling-3d refit daily. K=3 primary (mission-fixed); K∈{2,5,7} and refit cadence∈{1,2,3}d compared on dev+holdout walk-forward (eligible days 05-26+). Drift alarm: A(d) = day-d decision-z-density-weighted mean \|p_inuse−p_fresh3d(d)\| (computable end-of-day d with only ≤d data); τ = max A(d) over dev+holdout days under rolling-3d (normal-churn envelope); alarm validated iff frozen-original's A(d) crosses τ on/before 06-04 (the documented A3 incident) with zero rolling-3d false alarms by construction | the empirical P(Up\|z) map tracks a drifting Up-rate/vol regime; a frozen map mis-centers mis = model_p − mid and systematically buys the stale side (A3: +5.6pp Up-bias by 06-04) | rolling-3d calibration error on future ≪ frozen-original; EV/trade not worse | ADOPT rolling-3d iff future band-tick weighted-cal-error(rolling) ≤ 0.6 × frozen-original AND future walk-forward EV/trade(rolling, Chainlink) ≥ EV/trade(frozen-original) − $0.25 (non-inferiority; paired per-window ΔPnL slug-CI reported). Cadence rec = cheapest cadence within $0.10/tr of daily refit on dev+holdout. Report (a) vs (b) vs (c) on future for v1 AND v2 | ✅ ADOPTED. Cal leg: future WCE roll3 **0.0138** vs frozen **0.0703** (0.20× ≤ 0.6 ✓; as-deployed 0.0173). EV leg v1: frozen **−$0.47** [−1.15,+0.19] vs roll3 **+$0.60** [+0.01,+1.24], paired Δ **+$0.98 [+0.43,+1.52]** ✓; v2: frozen +$1.32 vs roll3 +$0.85 — fails non-inf by $0.22 but paired Δ [−1.02,+0.32] spans 0 (frozen v2 rode its Up-bias on this tape; the cal leg is the safety property). d+h: frozen +$0.43 [−0.30,+1.22] vs roll3 +$1.16 [+0.51,+1.83]; K-sweep single-peaked at K=3. Cadence rec **3d** (1/2/3 within $0.10 on d+h). Alarm VALIDATED: τ=0.0414; frozen crosses 05-31 (5d before the manual refit), sits 0.065–0.086 every future day; roll3 future max 0.022 = zero false alarms. Note: the 06-05 manual refit ≈ rolling quality on this block (as-deployed v1 +$0.51) — the adoption's operational content is AUTOMATION + ALARM |
| SQR2 | Chop/trend regime gate, fit on DEV only. Features (point-in-time, trailing): ER_btc(W) = \|btc_spot(t)−btc_spot(t−W)\| / Σ\|1s increments\| over (t−W,t], W∈{1800,3600}s, from joined BTC cb_spot (≥60% second-coverage else NaN → fail-OPEN, NaN fraction reported); alt: trail4_btc_move = mean \|net window move bps\| of BTC's last 4 CLOSED 15m windows. Gate: skip sq entry when feature > θ. Chooser (dev only, on frozen-original v1 decisions — the config that produced the documented bleed): θ ∈ dev decision-tick feature quantiles {0.60,0.70,0.80,0.90} × 3 features; maximize dev total PnL s.t. keep-rate ≥ 50% AND dev EV/trade(gated) > ungated. Holdout = one-shot validation of the single chosen gate; future revealed once on the final stack | documented lwd↔sq anti-correlation: sq loses on the reverting transient in DIRECTIONAL tape, wins in chop; trendiness of the macro driver (BTC) flags the regime for all 4 coins at once | dev losses concentrate above θ; gated EV/trade > ungated on holdout and future | ADOPT iff future EV/trade(gated) ≥ ungated + $0.25 AND gated future slug-CI lower > 0 AND holdout agrees in direction (gated ≥ ungated). If the dev chooser finds NO admissible θ (every candidate violates keep-rate or dev-EV constraints) → honest negative, no gate, future leg not run | ❌ honest negative. Dev chooser DID find an admissible gate (er_btc_1800 ≤ 0.1282 = dev q0.80: dev +$1.51→+$1.91/tr keep 81%, NaN 4.1%; holdout direction OK +$0.24→+$0.36 but keep 96%); FUTURE: keep-rate **98.3%**, EV +$0.603→+$0.589 (Δ **−$0.01** ≪ +$0.25 leg; gated CI [+0.008,+1.252] passes its leg). The dev-fit θ simply never triggers OOS — dev trendiness levels were tape-specific; the documented bleed is NOT captured by BTC-ER/trail-move trendiness at dev-fit thresholds. No gate shipped |
| SQR3 | Cross-coin concurrency cap: within a window epoch (same window_start_ts), keep ≤ C sq positions, C∈{1,2}; priority = earliest entry_sec, tie → symbol asc (deterministic, outcome-blind). Applied after SQR2 on the rolling-3d arm. C chosen on dev+holdout (max per-day Sharpe, Chainlink $10); future revealed once | sq variance is macro-correlated — 4 same-epoch positions = 1 leveraged macro bet (15m windows are aligned across coins and held to resolution ⇒ same-epoch = simultaneous); capping concurrent exposure trims the correlated tail; entry-time priority is outcome-blind so per-trade EV should hold | EV/trade ≈ unchanged; per-day PnL sd and worst-epoch loss shrink | ADOPT C iff future EV/trade(capped) ≥ uncapped − $0.25 AND future per-day PnL sd ratio (capped/uncapped) ≤ 0.9 AND worst future window-epoch loss shrinks. Else honest negative (cap stays a proposal for heavy-tape risk control, not shipped) | ⚠️ NOT shipped (registered d+h chooser picked C=None: Sharpe 1.74 vs 1.29/1.16 — cap cut d+h totals more than variance) BUT the future legs would have PASSED for both C=1 and C=2 (C=2: EV +$0.95 vs +$0.59 uncapped ✓, day-sd ratio 0.57 ≤ 0.9 ✓, worst epoch −$21.3 vs −$42.4 ✓, total $688 vs $711 = −3%). d+h↔future tension on a 5-day block: the future tape had heavier multi-coin bursts. Cap stays a candidate RISK control to re-test with more days, not an EV tool |
| SQR4 | Live-physics re-judgment + shadow-live verdict. Revealed on future in ONE pass: frozen-original v1/v2 + as-deployed v1/v2 + rescued v1/v2 (= rolling-3d + SQR2 gate if adopted + SQR3 cap if adopted), each at $5 under v2-idealized AND live_guarded (seed 0 + seeds 0–4), fixed-latency {2,5,10}s for the final config. Headline final = rescued variant (v1- or v2-filtered) with the higher dev+holdout live_guarded total PnL (registered chooser). Carried caveat: the random zero-fill hazard is adversely-selected-OPTIMISTIC (live missed trades skew winners — rejudge_live_model header) | the 43%-EV-kept verdict (A2) predates the measured fill physics (median live latency ~1.3s with a 5–15s tail, hazard ~44%, kappa 1.056, guard floor); sq's mid-window books are deeper/slower than det's last-60s books and sq's edge decays slower (mid-window mispricing vs settlement race), so sq should survive live physics better than A2 guessed | final config future live_guarded EV/fill > 0, CI clear of 0, surviving 5s fixed latency | **shadow-live candidate** iff future live_guarded EV/fill slug-CI lower > 0 AND n_fills(future) ≥ 30 AND EV/fill ≥ $0.50 (at $5) AND fixed-5s future EV/fill > 0; **needs-more** if future EV/fill > 0 but ≥1 leg fails; **reject** otherwise. Epoch-clustered CI failing while slug-CI passes → downgrade one band | ⚠️ **needs-more** (registered bands). Registered final rescued_v2 (d+h chooser): future lg **+$0.38/fill [−0.17,+0.95]** n=345 fills (fill 45%), WR 49.6 — fails CI>0 + EV≥$0.50 legs; passes n≥30 + 5s>0 (+$0.47); seeds0–4 $0.36±0.20. SQR2-dead fallback rescued_v2_nogate: +$0.08 [−0.44,+0.66]. Whole family flat-weak under live physics on the 4.2-day future block: frozen_v1 **−$0.40** (WR 36.9!), deployed_v1 +$0.17, deployed_v2 +$0.29, rescued_v1 +$0.14 — all CIs span 0. CL settle cuts the CB walk-forward EV 45–65% (roll3 v1 CB +$1.60 → CL +$0.60). The one seed-0 'pass', rescued_v1_nogate +$0.97 [+0.51,+1.45] n=608, DISSOLVED under the post-reveal seeds-0–4 measurement (same revealed block, no new selection): seeds [0.97, 0.07, 0.32, 0.27, 0.33] = **$0.39±0.34** — a lucky hazard draw (its 98%-identical gated twin scored +$0.14; live>idealized ×2.3 matched no sibling). Family seeds-means: rescued_v1 $0.27±0.20, rescued_v2 $0.36±0.20, v2_nogate $0.45±0.34 — all positive, none CI-clear. Paper P&L was largely a fill-model + settle-oracle artifact |

Artifacts: `data/research/sq_rescue/` (knobs.json + results jsonl), doc
`docs/research/SQ_RESCUE_2026-06-11.md`.

**SQ-rescue protocol addendum (2026-06-11, written AFTER devhold knobs froze, BEFORE the reveal
ran):** (i) the SQR1 cadence tie-break loop had a mechanical bug (picked the most-frequent instead
of the registered cheapest/least-frequent admissible cadence); fixed and devhold re-run — cadence
is a reporting-only knob, the reveal arms use daily refit as registered. (ii) SQR4's reveal set
gains `rescued_v1_nogate`/`rescued_v2_nogate` twins (rolling-3d, no SQR2 gate, same cap choice) in
the SAME single reveal pass, so if SQR2's own future leg fails its rule the shadow-live spec can
fall back to a config whose live numbers were produced in that one pass (no second reveal). (iii)
devhold knob outcomes for the record: SQR2 gate ADOPTED-on-dev = er_btc_1800 ≤ 0.1282 (q0.80; dev
+$1.51→+$1.91/tr keep 81%; holdout +$0.24→+$0.36 keep 96%, direction agrees); SQR3 chosen C=None
(cap cuts d+h Sharpe 1.74→1.29/1.16); SQR4 headline final = rescued_v2 (d+h live_guarded total
$560 vs $294).

---

## External-inputs campaign (2026-06-11) — XI1–XI5 (registered BEFORE any conditional/future number was computed)

**Question.** Do genuinely NEW external inputs — Binance derivative stress (funding / OI / a
liquidation-cascade proxy), a scheduled US-macro calendar, and the co-terminal 5m Polymarket book —
(a) split the EV of the deployed edges into avoid/keep regimes, or (b) carry an independent edge?

**Data + fill models (XI1–XI4).** `edge_lab.load_base()` with the LC2 split override
(future = **2026-06-05..09**, 06-01..04 relabeled holdout; dev 05-23..27). Fill models per
`rejudge_live_model.simulate_config`: **v2** (idealized fixed-2s L2 walk) and **live_guarded**
(`fill_model_live.json` live-1), **$5 stake**, seed-0 CI headline + seeds 0–4 mean on live future
legs. CIs: `research/lib/stats.window_clustered_bootstrap` (slug-clustered). Jaccard = slug-set
Jaccard vs `rejudge_live_model.decisions_for` deployed configs + `early_disagree` (disagree,
tl 450–900, |dist|≥10, ask 0.30–0.45) + fav_disagree_live045. Tooling:
`research/analysis/external_inputs.py`, fetcher `research/dataset/binance_deriv_fetch.py`,
calendar `data/research/events_calendar.csv` (static, from-knowledge schedule, `approx=1` on all
rows — exact-date risk declared), artifacts `data/research/external_inputs/`.

**Honesty caveat (same shape as BC1–BC3).** 06-05..09 is NOT virgin for the deployed configs
(unconditional EV revealed 06-10/11). XI1/XI3 claim NO new edges — they measure the REGIME SHAPE of
already-validated edges under never-before-joined exogenous columns (deriv feeds, scheduled
calendar), with decision rules fixed here. XI2/XI4/XI5 contain genuinely new rules: future revealed
once, after this registration. Known fill-model limitation carried: random zero-fill hazard is
adversely-selected-OPTIMISTIC.

**New external columns (definitions frozen).**
- **Cascade proxy** (forced-order history is not public — this is a stated PROXY built from
  `data/research/binance_1s`): per coin, 1-min bars (vol=Σvolume, rng_bps=(high−low)/close×1e4);
  robust z = (x − rolling-median₁₂₀ₘ)/(1.4826·MAD₁₂₀ₘ), window shifted 1 min (strictly causal),
  min 60 min; scale floored at 5%·|median| (0-MAD degeneracy fix, amended pre-reveal during unit
  testing — no conditional/future number existed yet). Cascade-minute iff vol_z ≥ Z_v AND rng_z ≥ Z_r, where Z_v/Z_r = that coin's
  dev+holdout (05-23..06-04) q99.5 of vol_z/rng_z. PRIMARY regime `cascade_recent` = ≥1 same-coin
  cascade-minute in [entry−180s, entry]; SECONDARY (reported, never gated) `cascade_any_recent`
  (any coin), `cascade_in_window` (same coin, [window_start, entry]). Proxy validation
  (descriptive): the 2026-06-10 17:45 UTC 3-coin live burst window must light up.
- **Cascade amendment (logged 2026-06-11 BEFORE any EV/conditional number was computed; trigger =
  the pre-registered validation leg itself):** the q99.5 per-coin proxy FAILED its registered
  validation — the 2026-06-10 17:45 incident minute (1781113800: xrp vol_z 21.0, eth 8.5, sol 7.3)
  is below every per-coin joint threshold (q99.5 ≈ vol 23–27, rng 7.6–8.9; even q99 misses). The
  live-loss signature is the CROSS-COIN JOINT move, not per-coin extremity. Amended ADDITIONAL
  regime (exogenous data only, no EV peeked): joint-cascade minute iff ≥3 of 4 coins have
  vol_z ≥ their d+h q95 AND rng_z ≥ their d+h q90 simultaneously (flags 1.41% of minutes;
  captures the incident, eth/sol/xrp hot). Regime `cascade_joint_recent` = any joint-cascade
  minute in [entry−180s, entry], coin-independent. XI1's gate rule applies to it identically;
  XI2's edge test reports BOTH cascade_recent (original) and cascade_joint_recent, gate decided
  per the same registered legs on each (2 looks, declared).
- **OI**: 5m `openInterestHist`; `doi5_pct` = OI(t)/OI(t−5m)−1 asof-joined at entry (staleness
  ≤600s else row excluded from the OI test). Regime `hi_doi` = |doi5_pct| ≥ per-coin d+h q80.
- **Funding**: settled 8h prints asof-joined at entry (staleness ≤9h). Regime `funding_extreme` =
  |rate| ≥ per-coin d+h q80.
- **Events**: `in_event` = entry epoch inside any ±15min calendar window; `in_event_t1` = tier-1
  (FOMC-minutes/GDP/PCE/NFP — no CPI or FOMC decision lands in 05-15..06-09; declared coverage gap).

| # | Edge / change | Mechanism (causal story) | Predicted effect | Decision rule | Status |
|---|---|---|---|---|---|
| XI1 | Deriv-regime gates on the deployed set {det_d12_dual_live, fav_disagree_live045, early_disagree, det_lwd_live}: EV split by cascade_recent / hi_doi / funding_extreme | books lag spot more under forced-flow stress; det favourites get knifed by cascades (the burst incidents); funding extremes = crowded positioning → squeeze risk | det-family in-regime EV < out-regime EV; knife/burst losses concentrate in cascade minutes | RECOMMEND avoid-gate for a config×regime iff (i) d+h in-regime live_guarded EV < $0 AND its CI-upper < d+h out-regime EV, (ii) future agrees in direction (in<out on seeds 0–4 mean), (iii) in-regime ≥5% of decisions AND in-regime d+h fills ≥10. Else honest negative / descriptive. Also report (descriptive): worst-window-group losses in vs out of cascade regime | ❌ honest negative — NO gate; 0/24 config×regime slices pass even leg (i). Direction REVERSED vs prediction: stress regimes ENRICH det-family EV (dual×hi_doi d+h $1.77 vs $1.04, future seeds-mean $1.32 vs $0.33; lwd×joint-cascade d+h $1.72 [1.23,2.28] vs $0.33, future $1.19 vs $0.53; dual×joint $2.32 vs $1.18, future $1.27 vs $0.65) — books lag MORE under stress and the deployed edges monetize it. funding_extreme = no consistent direction. Descriptive: ALL 20 worst lg0 window-groups (4 configs × 5) are OUTSIDE both cascade regimes — the PM-book bursts that hurt are NOT Binance-tape-extreme |
| XI2 | Cascade-conditional disagreement EDGE: fav_disagree (broad 0.05–0.90) and early_disagree decisions restricted to the cascade regime | under cascade stress the book lags spot MORE → disagreement signals should be richer per fill | EV(cascade subset) > unconditional EV on the same config | EDGE-WORTHY iff future live_guarded EV(cascade subset, seeds 0–4 mean) ≥ unconditional future EV + $0.25/fill AND seed-0 future CI-lower > 0 AND future cascade fills ≥ 20. Else honest negative | ❌ dead — all 4 config×regime slices fail every leg. Cascade subsets are tiny (fav_disagree 5/16 of 363 decisions, early_disagree 9/72 of 603; future fills 0–6) and NOT richer: early×joint d+h sub $1.39 < unconditional $2.32; fav×cascade d+h sub +$21.6/fill is a 4-window burst-jackpot anecdote. The disagreement edge does not need (and cannot be usefully conditioned on) the cascade proxy at these n |
| XI3 | Event-window avoid-gate on the same four deployed configs | scheduled macro prints cause violent repricing across all coins at once; det favourites knifed mid-event | in-event EV < out-event EV; knife losses inside event windows | AVOID-GATE iff pooled-across-configs in-event live_guarded EV < −$0.50/fill with CI-upper < 0 on d+h AND future direction agrees AND in-event fills ≥ 15 (all splits pooled). Else honest negative / descriptive (expected: thin n — the calendar has 16 windows) | ❌ honest negative — NO gate; leg 1 fails in the WRONG direction: pooled in-event d+h EV is POSITIVE and above out-event (lg +$2.93 [0.98,4.52] n=18; v2 +$2.20 [0.76,3.48] n=29 vs out-event +$1.67/+$1.59). Knives do NOT concentrate in event windows (knife share in-event 1.9–2.3% ≈ fill share 2.2–2.7%). Future in-event n=3 (−$3.03) too thin to mean anything. Caveats: tier-1 slice n=6–7 only; NO CPI/FOMC-decision landed in the window (declared gap) — this clears tier-2 prints + minutes/GDP/PCE/NFP, not CPI day |
| XI4 | 5m↔15m cross-book: in the last 300s of a 15m window the co-terminal 5m market (same close, strike K5 = spot at 15m_start+600s) implies a BOUND by strike monotonicity (K5 ≥ K15 ⇒ P(close>K15) ≥ P(close>K5); K5 ≤ K15 ⇒ P(close<K15) ≥ P(close<K5)). RULE: when the bound is violated tradeably — implied 15m side's best ask + m ≤ co-terminal 5m implied side's best bid, strike gap ≥ g bps — buy the implied 15m side (ceiling 0.90). m ∈ {0.03, 0.05, 0.10} × g ∈ {2, 5} chosen on d+h (chooser: max d+h live_guarded total PnL among variants with d+h fills ≥ 40; fallback max d+h total); future revealed ONCE for the chosen variant, others reported for context | a second LIVE order book on (part of) the same close is an independent opinion; if the 15m book violates the joint no-arb bound, the 15m side is mechanically cheap vs the 5m market's information | violation-implied side wins more than the 15m book implies; rule EV > 0 after costs | LC4 bands: deploy-paper-candidate iff future EV > 0 under BOTH fill models ∧ live_guarded future seed-0 CI-lower > 0 ∧ live future fills ≥ 30 ∧ max Jaccard < 0.5; duplicate-of-known if EV legs pass but Jaccard ≥ 0.5; reject otherwise. Descriptive leg (no gate): among genuine-conflict ticks, P(5m-implied side settles cl-correct) vs P(15m favourite settles cl-correct). If 5m live data is too sparse/unhealthy in the clean window, stop and report | ✅ **deploy-paper-candidate** (all 4 LC4 legs). 5m data healthy (2.04M co-terminal ticks, 6,315 joined windows). Registered chooser picked m=0.03/g=2bps (max d+h lg total $361, n=178): future lg **+$1.36 [+0.12,+2.68]** n=60 fills seed-0, seeds 0–4 mean **$1.81±0.27**, future v2 +$1.84 [+0.67,+3.03] n=98; max Jaccard **0.21** (fav_disagree; early_disagree ≤0.07). Robust: ALL 6 grid cells future-positive under BOTH models (m05g5 +$2.87 [1.06,4.73] lg); 4/4 coins + (btc weakest +$0.50); 4/5 future days + (06-09 partial, n=5, −). Profile: buys the CHEAP 15m side (med ask 0.39, range 0.01–0.89), 56% DOWN, median entry at the 5m open (sec 600; 75% ≤621). Sensitivity: survives s5≥5 (+$1.69 [0.56,3.00]); s5≥15 decays to +$0.95 [−0.10,+1.99] — the 5m OPEN (a fresh spot-anchored book reset) is the information event. Descriptive: raw mid-conflicts 5m right only 53% vs 15m-fav 59% — the edge is the no-arb BOUND at executable prices, not 5m-mid clairvoyance. K5 = collector-captured start_price (recorded within first seconds; a live twin reads spot at the 5m open directly — declared) |
| XI5 | E6 closeout: the frozen E6 rule (REVERSAL after prev-window \|move\| ≥ 50bps, consecutive-window pairs, entry ≤ 60s into window) on the current frame, future = 06-05..09, Chainlink settle | prior E6 'future' (06-01..04) was 4 lumpy macro-correlated days — the inconclusive needs a fresh block | if the autocorrelation is real it survives the fresh block; if it was macro-luck it dies | PAPER-TWIN-WORTHY iff future Chainlink v2 ($5) EV CI-lower > 0 AND future n ≥ 30 AND live_guarded future EV > 0; else DEAD (one shot — no re-tuning of strength/entry knobs) | ❌ **DEAD**, decisively. Future (06-05..09, n=298): v2 Chainlink **−$0.575 [−1.04,−0.10]** (CI fully negative), lg −$0.27 [−0.88,+0.31], original-econ $10 CB-settle −$0.72 [−1.69,+0.27] (total −$216). d+h only +$0.33..0.39. The prior E6 'future pass' (06-01..04, +$1.28) was the 4-lumpy-macro-days artifact the inconclusive verdict suspected. Cross-window reversal does not exist as a tradeable edge; E6 closed |

## XI4 AMENDMENT (2026-06-12, main session) — look-ahead found; headline RETRACTED
The xb_5m15m_v1 implementation agent's parity stop-rule caught weak LOOK-AHEAD in XI4:
`build_ticks5m_coterminal` back-fills the 5m strike (k5 = discovery start_price, captured at
median 24s / p90 55s AFTER the 5m open by the 30s poll) to all ticks — 296/386 decisions (74% of
the future block) fire BEFORE the strike they condition on was sampled. The registered
+$1.36 lg / +$1.84 v2 future EVs are NOT causally tradeable. The implementable causal variant
(entry >= k5 capture; Jaccard 0.870 vs registered frame, strict subset) has expected EV in the
s5>=15 sensitivity slice: v2 +$0.95 [-0.10,+1.99] — CI spans 0 → UNPROVEN, fails LC4.
RE-REGISTERED: xb_5m15m_causal_v1 = same rule, entries only at/after k5 capture (the natural
engine behavior). Priors: unproven (~+$0.95/fill v2-sens, CI spans 0). Validation = PAPER FORWARD
ONLY (the 18-day history is acausal for this rule; no backtest reveal can bless it). Gate for any
live decision: >=7 forward days, realized EV/fill > 0 with bootstrap CI-lower > 0.
COLLATERAL AUDIT: early_disagree checked for the same defect — only 55/603 decisions (9%) fire at
sec<35; the CLEAN cohort future = +$4.89/fill [3.24,6.55] @$10 n=150 (the acausal slice was noise,
+$0.30 [-4.36,+5.07]) → early_disagree numbers STAND; its live twin is causal by construction.
det/dual/fav/psettle/fade bands all start >= sec 540 — unaffected.
QUEUED (unfreeze list): capture window strikes from SpotPriceCache at sec~0 instead of the 30s
discovery poll — kills this defect class engine-wide going forward (history stays acausal).
SHIPPED 2026-06-13 11:05 UTC (STATE.md "Look-ahead defect KILLED"): SpotPriceCache.price_asof +
discovery captures start_price as-of window_start_ts (live get_spot fallback; settlement/Chainlink
untouched; suite 517 green; verified live strike_basis=spot_asof, strike frozen at sec-0 even at
3-5s poll lag). FORWARD windows from 2026-06-13 11:05 UTC are causally clean at sec 0 — the
s5≥capture / sec≥35 causal filters are no longer needed for NEW data (pre-06-13 history still needs
them). The xb_5m15m_causal_v1 forward gate (≥7 days, CI-lower>0) can now read clean sec-0 data.

## XB-GAP sweep (2026-06-12, research/analysis/xb_gap_sweep.py) — NO sweet spot on the causal frame
Chooser registered pre-run (max d+h total, CI-lower>0, >=2 sig/day). RESULT: eligible set EMPTY —
every gap 2..20bps has d+h CI spanning 0 (gap2: +$0.32 [-1.30,1.93] n=243 ... gap20: +$4.55
[-2.61,11.35] n=10); gap30 (n=5, +$12.23 [2.23,20.43]) is lottery-shape, rejected on n. Future
reveal (default gap2 only): +$0.29 [-1.59,2.24] n=88. The monotone gap->EV/WR trend (45%->80% WR)
is mechanism-consistent but unproven everywhere. DECISION: the paper twin runs the WIDEST band
(gap>=2, premium 0.03, ceiling 0.90) and must LOG gap_bps per trade — the forward run locates the
sweet spot causally (~18 sig/day -> n~120/week). Expectations: LOW; this is a measurement twin.

## Print-model refit + drift alarm (2026-06-12) — operational pipeline, registered

The settlement-print artifact (`data/research/oracle_print_model.json`, op-1, dev-fit 05-23..27)
powers psettle_ud_v1 + oracle_fade_v1 through the frozen engine copy
(`src/mean_reversion_live/engine/print_model.py`, fail-fast at boot, feature-list drift raises) —
exactly the frozen-map shape SQ RESCUE proved rots within days and can flip sign. Shipped
`research/analysis/oracle_model_refit.py` (sq_rescue SQR1 pattern; 13 synthetic-frame unit tests
in `tests/research/test_oracle_model_refit.py` incl. a real engine-loader round-trip).

- **Drift alarm (`--check`, read-only; exit 1 on alarm — cron-friendly).** Statistic S =
  decision-density-weighted mean |p_incumbent − p_fresh| over decision-band ticks (tl 60–360s,
  both twins' band; per-tick mean = sq's drift_metric generalized to the 9-feature space) of the
  trailing 3 frame days; p_fresh = same-spec logistic fit on those days. τ = max S under a
  MAINTAINED reference arm (the registered refit pipeline below walked forward: 14d-window-minus-
  2d-holdout train, cadence 7d, effect next day) over the frame days strictly BEFORE the check
  window — zero false alarms on the historical window by construction; τ recomputed each run
  (monotone non-decreasing as history grows). **As of 06-12** (frame through 06-09 partial):
  τ=0.0833; incumbent S(06-07..09)=**0.0686 → NO ALARM**; backdating vs today's τ: would have
  fired **2026-06-06** (S 0.0844; the 06-07-ending window hit 0.0861 too) — op-1 brushed real
  drift ~10 days after its fit window closed, then dropped back under. Context: incumbent band
  Brier on the check window 0.10841 vs fresh-in-sample 0.10407 (aging, not yet broken).
- **Refit gate (`--refit [--execute]`, registered).** Candidate = SAME feature set / transforms /
  hyperparams (FEATURES + design_matrix + standardized logistic C=1.0; NO spec drift — the engine
  fail-fast is the safety net; isotonic NEVER adopted on rolling refits: the original self-rejected
  and fitting one on the gate days would contaminate the gate) trained on the trailing 14-frame-day
  window MINUS its most recent 2 days; REPLACE iff holdout decision-band Brier(candidate) ≤
  Brier(incumbent) + **0.002** (NONINF_TOL_BRIER) AND ≥ 20k labelled band ticks (fail closed on
  NaN/thin). Dry-run default; `--execute` = incumbent backed up alongside
  (`.bak-<ver>-<fitted_at>`, never overwritten) + version bump + fit-window metadata + atomic
  write + round-trip through the REAL engine loader before swap. **06-12 dry-run** (train
  05-27..06-07 12d n=2.29M, holdout 06-08/09 n=115,151 band ticks): band Brier incumbent
  **0.09719** vs candidate **0.09873** (paired diff CI [−0.0046,+0.0015] — noise-compatible),
  full-pop 0.12758 vs 0.12231 (candidate better) → gate **PASS** (non-inferior); candidate would
  ship as op-2. Artifact untouched this session (dry-run only; the main session executes refits).
- **Cadence (recommendation).** Refit WEEKLY as a standing step of the weekly review
  (`/mean-rev-review`), shipped with the Friday-unfreeze scheduled restart — the engine reads the
  JSON at boot only, so refits ship with restarts, NEVER mid-run. `--check` daily via cron: exit 1
  ⇒ run the refit dry-run, review the gate, then `--refit --execute` + an out-of-band SCHEDULED
  restart. The 06-06 backdated crossing at artifact age ~10d (and sq's frozen curve already
  alarm-crossing by 05-31, sign-negative OOS) says weekly-with-alarm is the right floor; do not
  let the artifact age past one review cycle.

## DATA-QUALITY ANNOTATION (2026-06-12) — stale-book contamination window; applies to EVERY paper-derived number above

Root cause shipped same-day (STATE.md 14:0x IDT): the heartbeat's signals_today counter re-parsed
4.4 GB of signals.jsonl synchronously on the main event loop every 5s (42s CPU/pass), starving the
Polymarket WS reader; connections died every ~25-30s (silently — exceptions unretrieved) from at
least 06-05 onward; order books in the engine/CSVs ran seconds-to-40s STALE in bursts, worst after
the 06-12 00:36 UTC restart tripled per-death resync cost (15m+5m, 48 books; Polymarket also
rate-caps a single connection — reproduced standalone at ~1,600 msg/s).

Severity tiers for consumers of this ledger:
- **VOID:** all paper trades 2026-06-12 00:36→10:55 UTC. The psettle/fade/xb twins' day-1 ledgers
  (OP4 fade +$555/53tr, psettle +$132/18, xb +$34/5, early_disagree day numbers) — forward clocks
  RESTART at 2026-06-12 10:55 UTC. Gates phrased "≥7 forward days" count from there.
- **DEGRADED (use with suspicion, severity grows over the span):** paper trades + L2-derived
  quantities ~06-05→06-12. Specifically: stale-low paper entry asks inflate paper P&L (part of the
  paper-vs-live gap attributed to "missed winners"/"oracle inflation" may be feed staleness);
  fills_live's zero_fill_prob[depth×tl] bins used L2 depth from the SAME stale books — the
  "depth doesn't predict fillability" conclusion is probably an artifact (preflight REST depth,
  fresh by construction, was 98% predictive — contradiction resolved). RE-CALIBRATE fills_live
  from post-fix L2 before the weekend re-scoring.
- **CLEAN:** everything from the executor's own process — fills.jsonl, preflight get_book
  verdicts, settlements, data-api P&L; the threaded spot/Chainlink streams; all REST-sourced
  research data; settlement outcomes. The A/B's fill-side legs are unaffected; its missed-EV
  (paper-twin) leg must exclude the VOID window.
- Historical backtests on data/historical (Mar 4-17) PRE-DATE the live collector — unaffected.

## CLEAN-DATA RECKONING (2026-06-13, registered BEFORE any clean-future number computed) — plan: in-this-repo-we-silly-thimble

Why: this week's feed/look-ahead/execution fixes built a trustworthy measurement apparatus; it
revealed det's edge was ~2-4x frequency-inflated by our own stale book feed (det catches ~100% of
genuine opps but they're rare on clean data; det_d12_dual_live live -$42.88/172). Every EV we have
was measured through the stale lens. This registers the de-staled re-validation + parallel re-hunt.

CLEAN-WINDOW DEFINITION (frozen here):
- TRAINING/reference = dev+holdout (2026-05-23..05-31) — pre-dates the 06-05 stale-degradation onset.
- DEGRADED, EXCLUDE from "clean" = 2026-06-05 → 06-12 10:55 UTC (growing stale-book severity).
- VOID = 2026-06-12 00:36→10:55 UTC.
- CLEAN FUTURE = entries with reconstructed UTC ts (window_start_ts + entry_sec) >= 2026-06-12
  11:00 UTC. Small now (~1.5 days, weekend) → FIRST PASS; firms at >=7 clean days (~06-19/20).

B. DE-STALED EV RE-SCORE (research/analysis/rejudge_live_model.py + thin clean-window wrapper):
- Configs: the rejudge CONFIGS dict (det_lwd_live, det_d12_dual_live, det_d12_wide_v1, fav_disagree,
  fav_lowvol, fav_momentum, fav_deepdown) + psettle_ud_v1/oracle_fade_v1 via their own paths.
- Fill-model BRACKET (the binned live model can't be honestly recalibrated yet — only 59 clean
  attempts / 19 post-guards vs 246 needed): report BOTH v2 (idealized, optimistic bound) and
  live_guarded with the stale live-1 params (pessimistic bound). Truth is between; decision-level
  frequency + gross EV (the core question) are fill-model-independent.
- Metric per config: clean-future EV/trade + EV/$1 + fills/active-day + window-clustered CI, vs the
  same on dev+holdout. PRE-REGISTERED verdict rule: KEEP-live if clean-future EV/trade CI-lower > 0
  under live_guarded AND fills/active-day >= 2; DEMOTE-to-paper if CI-lower <= 0 OR frequency
  collapsed to < ~0.5x the dev/holdout rate; INSUFFICIENT-DATA if clean-future n < 20 (hold, keep
  running). All live demotions present-first to the user.

E. CLEAN-DATA RE-HUNT (hypothesis_sweep set_future_override -> select -> verify --fill-model live;
   edge_atlas --build/--reveal-future):
- FUTURE_START = "2026-06-13" (06-12 is half-degraded intraday; the per-day override can't split
  it, so burn 06-12 too). Discovery on relatively-clean dev/holdout; clean-future reveal is small
  now (confirmation sharpens as data accumulates) — do NOT over-read the future block yet.
- Future-blind shortlist gates UNCHANGED (n>=40, dev_n>=12, cpcv>=80%, full_ci_lo>0, latency
  5s&10s EV>0, cap_10>=0.5, max 5/family).
- PRE-REGISTERED FAMILY PRIORITY (we now know det/longshot rent the last-minutes book-lag artifact):
  rank survivors favouring the book-lag-INDEPENDENT families — e4/disagree (mid-window), oracle/
  basis, psettle, flow, vol-regime, tod, micro/microprice — over det/longshot/momentum last-minutes
  families. A det-family survivor must additionally clear the clean-future block (not just
  dev/holdout) before any live consideration.
- Honest-negative carry-forward (will NOT redo): maker, z-score-alone, Kelly/variable sizing,
  martingale, staleness strawman, cross-coin lead-lag, ML meta-labels, deep-tail floors, tight sq caps.

DEFERRED: fills_live recalibration on clean attempts (needs ~few hundred; ~7 clean days). Until then
the bracket above stands. Program success = >=1 edge with clean-future-blind EV/trade CI-lower > 0
AND a positive rolling-7-day CLEAN-ERA live book.

## TA STRATEGY CAMPAIGN (pre-registered 2026-06-16)
Hypothesis: causal base-asset technical-analysis features on the cb_spot tape yield deployable
edges in four roles — ta_directional (honest control, EXPECTED TO FAIL: book already prices spot),
ta_filter (regime gate on the determinism edge), ta_regime (ATR-band selection on determinism),
ta_divergence (base asset moved per EMA-slope+30s-return, book hasn't repriced; buy the move side).
Pipeline: research.dataset.ta_features -> _ta_frame() in hypothesis_sweep -> sweep --future-start
2026-06-12 -> select (dev/CPCV/latency only, future context-only) -> verify --fill-model live ->
edge_atlas + Jaccard dedup vs deployed edges (det_lwd, det_d12_dual, det_d12_wide, fav_disagree,
early_disagree, psettle_ud, oracle_fade). Chainlink settlement. Future block revealed ONCE.
CAVEATS pre-registered: (1) cb_spot is a ~15s REST poll, so TA resolves at ~0.06 Hz — sub-15s
structure invisible (TA can't see the seconds-scale manual edge). (2) clean-future block is THIN
(~1.5 days, data built through 06-13) — future EV/trade is provisional; selection is dev-based and
the FORWARD paper run is the firm validator. Deploy gate for any survivor = paper twin (live:false),
>=7 clean forward days realized EV/fill CI-lower > 0 before any live talk. Doc: docs/research/
TA_STRATEGIES_2026-06-16.md.

## HONEST EDGE HUNT v2 — THEORY-FIRST CAMPAIGN (pre-registered 2026-07-02)
Everything below is decided BEFORE any post-06-18 number is looked at. Labels are OFFICIAL
on-chain outcomes ONLY (data/research/official_outcomes.parquet; official_only_by_slug for 5m —
no recon fallback exists for 5m). Honest re-settle of paper ledgers: research/analysis/
resettle_official.py (parity-pinned to real money in tests/research/test_resettle_official.py).

VIRGIN BLOCK (frozen): entries >= 2026-06-19 00:00 UTC. The 06-18/19 honest sweep revealed
everything through 06-18 for all 2,681 specs + all deployed configs; 06-19→now has never been
read by any selection. DEGRADED era (06-05→06-12 11:00 UTC) excluded from every verdict;
pre-06-13 entries keep the sec>=35 / s5>=capture causal filters. Discovery for any NEW family
uses <= 06-18 only; the virgin block is revealed ONCE per theory.

FILL MODEL: live-2 (recalibrated this campaign on all clean attempts >= 06-12 11:00 UTC,
holdout = most recent week of fills; version string pinned in each verdict). v2 idealized
bracket reported alongside. Stakes $5. Multiple testing: BH-FDR 10% WITHIN each theory's
pre-declared spec set. Statistics: window-clustered bootstrap (slug clusters), 5-seed
robustness where sampling is stochastic, Jaccard < 0.5 vs deployed/live decision sets.

T1 CROSS-BOOK 5m↔15m (internal-consistency arbitrage; strongest prior — relative value, not
prediction). (a) Score the ALREADY-REGISTERED xb_5m15m_causal_v1 gate on the official-settled
forward ledger (~20 causally-clean days): PASS = realized EV/fill CI-lo > 0 AND n >= 30 →
propose $5 live probe to user; KILL = CI-hi < 0 OR (EV < 0 AND n >= 40); else MEASURE-ON with
hard expiry 2 more weeks. (b) NEW fam_xh_* sweep via research/dataset/xbook.py: all 3
constituent 5m windows per 15m window, executable bound-violation margins BOTH directions
(5m-off-15m never tested), guards: asof-backward join >= 1s embargo, both legs book_healthy
AND 5m tick age <= 3s, filters never touch outcome columns; discovery <= 06-18, ONE virgin
reveal; gates: virgin n >= 30, CI-lo > 0, seed-robust, live-2 fills, BH 10%, Jaccard < 0.5
vs xb twin (must beat xb on common slugs or trade different slugs). Door-closing: no survivor
AND (a) <= neutral ⇒ cross-horizon family CLOSED.

T2 RESIDUAL DETERMINISM AT THE FEE FRONTIER. (a) Re-verify the FROZEN re-test set — the
2026-06-18 sweep shortlist ∪ every spec future-positive at that reveal (det_0066, det_0028,
det_0024, + shortlist survivors as archived in data/research/hypotheses/) — via
hypothesis_verify --fill-model live, --future-start 2026-06-19. Selection NOT re-run; this is
the second and FINAL look. Gates: virgin n >= 30 AND CI-lo > 0 AND BH-FDR 10% across the whole
re-test set AND consistency leg full clean-future (06-13→now) CI-lo > 0. Practical thin-n rule:
virgin n < 20 (~13 virgin days) = < 1.5 tr/day = below the $2/day success bar even if real ⇒
KILLED. (b) Honest virgin re-score of all ~24 paper strategies from resettle_official:
KILL twin = virgin CI-hi < 0 OR (EV < 0 AND n >= 40); PROMOTE-candidate = CI-lo > 0 AND
n >= 30 AND Jaccard < 0.5. det_lwd_live stop-recommendation rule: official-settled realized
<= -$0.50/fill AND CI-hi < 0 over the clean era → recommend stop (user decides).
Door-closing: zero survivors ⇒ det/book-lag family CLOSED PERMANENTLY (no third look, ever).

T3 HONEST MISPRICING MAP. edge_atlas rebuilt on official labels + live-2 costs; build set
<= 06-18; candidate cells pre-declared via the atlas's BH-family machinery (10%); ONE
--reveal-future on the virgin block. Separately for 15m and (first ever) 5m. Survivor cell →
composed spec → hypothesis_verify --fill-model live funnel (same gates as T2a). Door-closing:
no cost-clearing cell ⇒ static-mispricing door CLOSED for that timeframe.

T4 UNINFORMED-FLOW FADE (raw CLOB prints — never mined; prior flow deaths were book-derived).
research/dataset/trade_prints.py per-(slug,second) features from data/live_trades: burst shape
(2s vs 30s counts), print-size distribution (p90/max single), aggressor imbalance at executable
prices, fee-tier-weighted flow, post-burst fade timers. Guards: only prints with ts <=
tick_ts - 2s; trade-id dedupe; shift-invariance test (delaying all prints +5s must not change
any entry decision's causality). ONE bounded fam_flow2 sweep, discovery <= 06-18, ONE virgin
reveal, gates as T1b. Door-closing: zero survivors ⇒ flow door CLOSED for book- AND
print-derived signals, permanently.

NOT PURSUED (dead list stands): mean-reversion/martingale, maker, staleness-at-expiry,
print-prediction models, TA, sq family, fav family, early/near-strike fades, macro overlays
(conditioning re-check only if a base edge emerges), z-score, Kelly/variable sizing,
cross-coin lead-lag, ML meta-labels. Manual-trade records permanently unavailable (user).

DELIVERABLE: docs/research/EDGE_HUNT_V2_2026-07.md with per-theory verdicts. Any survivor →
evidence → USER SIGN-OFF → $5 guarded live probe. If all four die: verdict doc states which
mechanisms are closed and why; remaining options are structural (other venues/market types,
out of scope). Success = any robustly positive daily EV (even $2-5/day) on honest labels +
live-2 fills.

### EDGE HUNT v2 — INTERIM VERDICTS (T2b paper re-score, revealed 2026-07-02)
Reveal per the pre-registered rules above. Scoreboard: data/research/paper_official/.
- BH-FDR 10% over 24 virgin blocks (one-sided clustered bootstrap p): SURVIVORS =
  fav_disagree_live(p=.00025) fav_disagree(.00025) fav_disagree_d5(.0025)
  det_d12_dual_v1(.004) det_d12_wide_live(.012) det_d12_wide_v1(.013).
  fav_lowvol first loser (.032 vs .029 threshold).
- CONSISTENCY LEG (full clean-future 06-12..07-02 CI-lo>0):
  fav_disagree_live +1.93 [+0.88,+3.01] n=113 PASS; fav_disagree +2.70 [+1.07,+4.35]
  n=159 PASS; fav_disagree_d5 +1.05 [+0.26,+1.82] n=591 PASS.
  det_d12_dual_v1/-wide_v1/-wide_live/fav_lowvol FAIL (virgin-fortnight riders).
- KILLED by rule (virgin CI-hi<0 or EV<0 n>=40): oracle_fade_v1, tadiv_approx_v1,
  tadiv_approx_ret3_v1, det_sqp_v1, det_sqp_v2, fav_deepdown, early_disagree_v1.
- T1a xb gate: virgin +1.04 [-0.65,+2.87] n=130 -> NOT passed (MEASURE-ON expired;
  door decided by fam_xh reveal).
- Code-boundary check: uncommitted engine tree is mtime Jun 9-13 — clean and virgin
  eras ran IDENTICAL code; the era flip is market/sample, not pipeline.
- FEE FACT (checked 2026-07-02): live pays ZERO fees (348,600/348,600 prints at
  fee_rate_bps=0; no fee field in fills.jsonl). pnl_official subtracts the engine's
  theoretical 0.07·p·(1-p) -> all honest EVs above are CONSERVATIVE.
REMAINING GATE for the fav_disagree family: live-2 guarded fill scoring on the
virgin window (rejudge driver) + Jaccard vs live roster; then user decision.

### EDGE HUNT v2 — T2 FINAL VERDICT (2026-07-02 ~23:50 IDT)
LIVE-FILL GATE (live-2 guarded, $5, on the twins' RECORDED virgin decisions — the
engine's actual decision stream, entry_ask/ask_depth_usd/time_left per trade; 5 seeds):
  fav_disagree       fills 80/122 (66%)  EV/fill +2.289 [+1.248,+3.294]  seeds sd 0.13
  fav_disagree_live  fills 44/79  (56%)  EV/fill +2.573 [+1.022,+3.975]  seeds sd 0.63
  fav_disagree_d5    fills 282/436(65%)  EV/fill +0.601 [+0.054,+1.142]  seeds sd 0.20
  ≈ +$8-13/day each at $5 stakes. Jaccard vs det_lwd_live virgin slugs: 0.07-0.11.
ALL REGISTERED GATES PASS for the fav_disagree family: official labels ✓ virgin
CI-lo>0 ✓ BH-FDR 10% ✓ consistency leg ✓ live-2 fills + seed-robust ✓ Jaccard<0.5 ✓.
=> Family is the campaign's PROMOTE candidate. Real-money re-arm requires USER
sign-off (locked decision). Pre-registered stop rule if armed: official-settled
realized <= -$0.50/fill with CI-hi < 0 => stop; $5/trade, $25/UTC-day, existing
per-strategy bankroll isolation.
NOTE: monolithic frame rebuild externally SIGKILLed twice (not OOM; 63% RAM free);
switched to resumable research/build_joined_chunked.py (parts cache).

### EDGE HUNT v2 — T4 VERDICT (fam_flow2 virgin reveal, 2026-07-03)
Discovery (<=06-18): 11/432 shortlisted (10 follow / 1 fade). VIRGIN REVEAL: 0/11 survive
(EV -0.61..+0.55, ALL CIs span 0, none pass seed-robustness at n>=30; virgin Jaccards
0.01-0.18 — pure discovery-era overfits, not even det re-labels). Registered door-closing
rule fires: FLOW DOOR CLOSED PERMANENTLY (book- AND print-derived signals).
Artifacts: data/research/hypotheses/flow_sweep/{discovery.parquet,virgin_verdicts.parquet}.

### EDGE HUNT v2 — T3 VERDICT (honest atlas, ONE virgin reveal, 2026-07-03)
Splits: dev 05-23..31, holdout 06-01..04 + 06-13..18, degraded dropped, future = virgin
>= 06-19; pre-06-13 sec<35 causal filter; official labels; live-2 slip. Artifacts:
data/research/edge_atlas/ (pre-v2 backed up). 1,369 cells, BH family 885, candidates
+4/-62 (selected on dev+holdout ONLY, before reveal).
- ALL 4 positive cells CONFIRM in virgin future — and all are ONE structure, the cheap
  DISAGREE side mid-to-early window: a0.30-0.35 tl450-900 cl5-12 +35.2%/$1 (n=49);
  a0.35-0.40 tl450-900 cl2-5 +27.7% (n=191); a0.30-0.35 tl300-450 cl5-12 +15.8% (n=34);
  a0.45-0.50 tl450-900 cl12-25 +1.0% (n=410, weak).
- Negative candidates confirm massively (cheap CONSISTENT longshots 5-15c near expiry:
  -40..-56%/$1 future) — the structural loser funding the edge.
=> The static-mispricing door re-finds the SAME disagree structure fav_disagree harvests,
at EARLIER timing (the early_disagree region) conditioned on CHAINLINK distance 2-12bps
(the twins gate on Coinbase dist>=10). No new orthogonal family.
FOLLOW-UP (new pre-registration): refined early-disagree PAPER twin — mode=disagree,
tl 450-900s, ask 0.30-0.40, Chainlink-dist gate 2-25bps (engine oracle_gate machinery),
standard twin gate (>=14 virgin-forward days, official-settled CI-lo>0) before any live
talk. NOTE: the composed spec's numbers on the ALREADY-REVEALED virgin block are
descriptive only (cell-level reveal was the one look); the twin's FORWARD record is the test.

### EDGE HUNT v2 — T1 VERDICT (fam_xh virgin reveal, 2026-07-03)
Clean rediscovery (degraded epoch excluded): 145/1280 shortlisted, dominated by the
never-tested 5m-instrument legs (5y/5n) at +$2-11/fill discovery EV. ONE VIRGIN REVEAL:
0/145 SURVIVE. The g>=10bps monsters collapse to n=1-7 forward (discovery-era frequency
inflation; below the 1.5 tr/day practical bar). The g=2bps 5y specs carry virgin volume
(n=60-87, EV +$1.3-1.6, CI-lo>0, seed-ok, Jaccard 0.15-0.24) but FAIL BH-FDR 10% across
the revealed set — individually suggestive, not survivors. Registered door-closing rule
+ T1a xb gate <= neutral both fire: CROSS-HORIZON DOOR CLOSED. Any revisit requires a
NEW pre-registration on genuinely new forward data (>= +3 weeks).
Artifacts: data/research/hypotheses/xh_sweep/{discovery.parquet,virgin_verdicts.parquet}.

### EDGE HUNT v2 — IMPROVEMENT AXES (pre-registered 2026-07-03 ~12:45 UTC)
Read from ALREADY-REVEALED virgin data (design inputs for forward-tested twins, not new claims):
1. SIZE CURVE (fav_disagree_live recorded decisions, live-2 guarded, 5-seed): $5=+$11.5/day,
   $10=+$17.9, $15=+$24.6, $20=+$29.0 — fills stay ~44-47/79 (partial-fill physics). Sizing
   $5->$10 = pure execution change on the SAME signal; USER decision (staged, cap unchanged).
2. CL-AGREE GATE: oracle_disagree entries are the losing slice (d5: agree +$1.85 n=385 vs
   disagree -$0.21 n=50; live twin +$3.64 vs +$1.37). Twin fav_disagree_d5cl_v1 DEPLOYED
   (d5 + oracle_gate agree); gate = 14 virgin-forward days, official-settled CI-lo>0.
3. ASK BAND: volume lives 0.35-0.45 (+$2.39/fill n=220), 0.25-0.35 (+$6.82 n=44); <0.25
   negligible n. No config change warranted.
4. TIME BAND: earlier window (tl 450-900) testing via early_disagree_cl_v1 (atlas cells).
Capacity lever NOT started: bnb/doge/hype collectors (engineering, separate decision).

### fav_disagree_live FORWARD-VALIDATION GATE (pre-registered 2026-07-06 ~16:30 UTC, BEFORE data)
The success gate symmetric to the already-registered stop rule — written down now so the
2026-07-20 decision is mechanical, not post-hoc. Context: re-armed live:true 2026-07-03
~07:40 UTC at $5/trade (user-approved); sized $5->$10 2026-07-05/06 (user-approved, size-curve
axis 1: $10=+$17.9/day predicted, fills ~44-47/79 unchanged).
WINDOW: all fills from re-arm (2026-07-03 07:40 UTC) -> 2026-07-20 00:00 UTC (~2.5wk).
METRIC: official-settled realized $/fill with bootstrap 95% CI; because size changed
mid-window, ALSO per-$ EV (realized / deployed) as the size-invariant check. Data source:
executor fills.jsonl x official on-chain outcomes (the honest ledger), NOT the paper twin.
- SUCCESS: CI-lo > 0 (both $/fill and per-$) => propose next size rung $15 (size-curve
  predicts +$24.6/day), CONDITIONAL on fill-rate holding (~56% live-2 baseline, no
  degradation beyond noise) AND user sign-off. Cap/bankroll unchanged at this step.
- NEUTRAL: CI spans 0 => extend at $10 to 2026-08-03, NO config changes, NO new sweeps.
- STOP (unchanged, registered 2026-07-03): official-settled <= -$0.50/fill with CI-hi < 0
  => recommend stop to user.
RESEARCH FREEZE (re-affirming the closed-door rules): no new discovery sweeps before
2026-07-24 (>= 3 weeks of forward data collected AFTER the 07-03 registrations); the only
open pre-registered threads are the deployed twins (fav_disagree_d5cl_v1, early_disagree_cl_v1,
xb_5m15m_causal_v1) and the g2bps-5y note, which itself needs the 07-24 data.
ROSTER PRUNE (same day, housekeeping): disabled 7 killed-by-rule paper strategies
(det_sqp_v1/_capped/v2, fav_deepdown, tadiv_approx_v1/_ret3, oracle_fade_v1) — their paper
ledgers freeze at 2026-07-06; verdicts already final on official labels. Engine roster
26 -> 19 (CPU headroom; engine-saturation guard).

### 2026-07-17 TWIN GATE VERDICTS (the pre-registered 14-day checks, scored on official labels)
- fav_disagree_d5cl_v1: **KILLED** — n=314, -$401.45, EV -$1.28/fill, CI [-2.49,-0.05]
  (CI-hi<0 rule). Engine pnl claimed +$0.47/fill (+$1.75 inflation). The "CL-agree gate
  improves d5" hypothesis (improvement axis 2) is DEAD forward.
- early_disagree_cl_v1: **KILLED** — n=782, -$179.66, EV -$0.23/fill, CI [-0.70,+0.22]
  (EV<0 at n>=40 rule). Engine claimed +$0.72/fill. The atlas early-timing cells
  (T3 follow-up) did NOT survive their forward test. Both pruned from strategies.yaml
  2026-07-17 ~16:41 UTC; ledgers frozen.
- xb_5m15m_causal_v1: inconclusive (+$0.73/fill, CI [-0.72,+2.29], n=268) — keeps running
  as data collection; any promotion needs a fresh post-07-24 registration (door rules).
- fav_disagree_live 07-20 gate, computed early: n=23, +$72.33, mean +$3.15/fill,
  CI [-1.79,+8.05] spans 0; per-$ +$0.34 CI [-0.19,+0.87] spans 0 => NEUTRAL => EXTEND
  at $10 to 2026-08-03 (mechanical on 07-20 unless the last 3 days flip CI-lo>0).
- det_lwd_live real-fill audit: 400 fills, EV +$0.135 CI [-0.11,+0.36]; last-14d +$0.01
  => break-even execution probe, working as designed. No stop (rule needs <= -$0.50 CI-hi<0).

### CAPACITY EXPANSION GATE (pre-registered 2026-07-17 ~16:45 UTC, BEFORE any new-coin data)
Deployed: SYMBOLS += bnb,doge,hype (paper only). All 3 verified: active Polymarket 15m/5m
markets (liq $1.1-2.2k/window vs btc $15.7k), Coinbase Exchange spot products (WS+REST),
Chainlink Polygon feeds added for bnb/doge (verified <0.1% vs spot); hype has no Polygon
feed => oracle-gated strategies fail closed on hype (intended). LIVE money is guarded by a
new executor allowlist EXEC_SYMBOLS=btc,eth,sol,xrp (live_executor.py; deployed with the
07-17 executor bounce, which also deployed the pending clamp_buy_fallback).
GATE (per new coin, evaluated 2026-07-31): official-settled EV/fill of the fav_disagree +
fav_disagree_live paper-twin entries on that coin's slugs, entries >= 2026-07-17 17:00 UTC
(first clean sec-0 strike window), slug-clustered bootstrap.
- PASS: CI-lo > 0 AND n >= 30 => propose adding that coin to EXEC_SYMBOLS ($10, same caps),
  conditional on intent-time ask_depth comparable to the old coins AND user sign-off.
- KILL: CI-hi < 0 OR (EV < 0 AND n >= 40) => coin stays paper-only / removed from SYMBOLS.
- Else: extend 2 more weeks, once; still inconclusive at 4wk => drop (thin-liq suspicion).
Caveat registered NOW: thinner books may inflate paper fills vs live; any PASS must also
survive the live-2 guarded fill model on recorded decisions before sign-off.

### POST-FREEZE CAMPAIGN (pre-registered 2026-07-23 ~15:25 UTC, BEFORE the 07-24 00:00 UTC reveal)
Hypotheses generated from already-revealed data (virgin scoreboard + live fills); each gets a
FORWARD gate on data ≥ 2026-07-24 00:00 UTC only. No engine changes; all score offline from
streams already running. Decisions are mechanical; any live/real-money action needs user sign-off.

- R1 xb_5m15m_causal_v1 LIVE-PROMOTION gate (fresh registration per the 07-17 door rule).
  Basis: virgin official +$1.08/fill CI [+0.10,+2.11] n=373; daily corr vs fav_disagree 0.19,
  Jaccard 0.09-0.12 => genuinely additive. Eval 2026-08-06 on forward-only fills:
  (a) official EV CI-lo > 0 (slug-clustered) on 07-24..08-06 entries, AND
  (b) live-2 guarded fill model on recorded decisions >= +$0.50/fill.
  PASS => propose live:true @$10, own $100 bankroll + $25/day cap, EXEC_SYMBOLS btc,eth,sol,xrp.
  KILL: CI-hi < 0. Else: extend 2wk once.
- R2 fav_disagree WIDENING CELL (dist 5-10bps ∩ ask 0.30-0.45). Basis: d5-only increment overall
  is +$0.04/fill (dilution — wholesale d5 promotion REJECTED today), but its 0.30-0.45 band was
  +$0.96/fill n=233 (post-hoc slice => needs forward proof). Gate: fav_disagree_d5 minus
  fav_disagree slugs, entry ask in [0.30,0.45], forward 07-24..08-07, official labels,
  slug-clustered CI-lo > 0 AND n >= 40 => propose lowering fav_disagree_live dist_min_bps 10->5
  (ask band unchanged). KILL: CI-hi < 0 or EV < 0 at n >= 40.
- R3 EXECUTION RESCUE on preflight-empty books. Basis: 10 live misses since re-arm counterfactual
  +$3.38/miss (5W/5L — NOT adverse-selected; ~27% volume left on the table; 7/10 were preflight
  attempts=0 skips). Gate: accumulate forward misses to n >= 25 (both twins' skipped intents count
  via recorded books); if counterfactual EV >= +$1/fill AND recorded L2 shows fillable depth
  <= max_ask within 30s post-skip on >= 50% of them => propose a 30s resting-limit rescue at
  max_ask (executor change, staged, user sign-off). KILL: counterfactual EV < 0 (misses adverse).
- R4 HOUR GATE h16-19 UTC. Basis: virgin official EV +$0.25/fill in h16-19 vs +$6-7 h00-07
  (slice, n=50 — weakest-prior hypothesis here). Gate: forward 07-24..08-07 fav_disagree entries
  h16-19: official EV < 0 with CI-hi < 0 => propose live skip-window; anything else => drop
  (do NOT iterate hour bins — one look only).
Standing gates unchanged: 07-31 per-coin capacity (hype early read 07-23: n=49 +$3.88/fill
CI [+1.68,+6.07] official — on track), 08-03 fav_disagree $15 rung, g2bps-5y on post-07-24 data.

### EDGE HUNT v3 (pre-registered 2026-07-24 ~05:45 UTC, BEFORE any new-window reveal)
Frames are rebuilding through 2026-07-23 EOD as this is written — nothing from the new
window has been scored. Campaign window: 2026-07-03 05:00 UTC .. 2026-07-23 EOD (frames
previously ended 07-03; everything after is unmined by ANY sweep). Official labels only,
live-2 guarded fills, 4 original coins (bnb/doge/hype excluded — partial window; they are
covered by the 07-31 capacity gate).
- V3a g2bps-5y RETEST (cross-horizon door revival per the registered T1 rule: >=3wk new
  forward data + fresh pre-registration — both satisfied today). FROZEN spec list = the 3
  virgin CI-lo>0 specs from xh_sweep/virgin_verdicts.parquet, named before scoring:
  xh_5y_m02_g02_b600-900_r1_c90, xh_5y_m02_g02_b600-900_r10_c97, xh_5y_m01_g02_b600-900_r1_c97.
  Scoring identical to the v2 reveal (5m official labels, $5 taker, 5-seed fill model,
  slug-clustered CI) on entries 07-03..07-23. Gates: n>=30, CI-lo>0, BH-FDR 10% within k=3,
  seed-robust, Jaccard<0.5 vs the running xb twin. PASS => deploy paper twin (if the engine
  lacks a zero-code mode for the 5m-instrument leg, twin engineering is a separate staged
  step); standard >=14-day official-settled twin gate before any live talk. FAIL => door
  re-closes; no iteration on the grid.
- V3b ATLAS v3 (persistence + new-cell scan; the atlas is the instrument that confirmed
  4/4 in v2). Splits: dev = 07-03..07-16, SEALED holdout = 07-17..07-23 (one look).
  Same cell grid + BH-10% family as T3; selection on dev ONLY; holdout revealed ONCE for
  the dev-selected cells (CI-lo>0 = confirm). Two registered outcomes: (i) PERSISTENCE
  verdict on the known cheap-disagree cells (decay check for the live edge), (ii) any NEW
  positive cell family => paper twin via existing engine modes + standard 14-day gate.
- NOT run: flow (door closed permanently 07-03), broad 2000-hypothesis sweep (underpowered
  at 3wk; v2 lesson — it re-finds the disagree structure).

### EDGE HUNT v3 VERDICTS (2026-07-24 ~08:05 UTC — the registered ONE looks, scored)
- V3a g2bps-5y RETEST: **2/3 SURVIVE** on the never-mined 07-03..07-23 window (all gates:
  CI-lo>0, BH-FDR 10% k=3, seed-robust, Jaccard<0.5 vs xb twin):
    xh_5y_m02_g02_b600-900_r1_c90   n=132 EV +$1.02/$5fill CI [+0.16,+1.95] p=.019 WR 61% J=.147
    xh_5y_m02_g02_b600-900_r10_c97  n=102 EV +$0.93/$5fill CI [+0.09,+1.83] p=.032 WR 62% J=.154
    (m01_r1_c97 fails: CI [-0.11,+1.43].)
  ≈6.6 signals/day, ~+20%/$ guarded-fill. CROSS-HORIZON DOOR RE-OPENS on the m02 family —
  this is the v2 "suggestive but failed FDR" family now confirmed on fresh forward data.
  NEXT (staged, per registration): engine twin needs a 5m-instrument leg (mode="xb" buys the
  15m side; the 5y leg buys the co-terminal 5M book — collector must attach xb15_* fields to
  5m ticks or xb mode must emit on the 5m slug). PART-B engineering + standard >=14-day
  official-settled twin gate before any live talk. Artifacts: hypotheses/xh_g2bps_v3/.
- V3b ATLAS v3 (dev 07-03..12, holdout 07-13..16, sealed future 07-17..23): **0 positive
  candidates** clear BH-FDR (future never opened for positives — nothing selected); 41 fade
  cells persist (cheap near-expiry longshots -35..-70%/$1, the structural loser intact).
  Persistence read (selection-window, descriptive): the v2 disagree cells keep the SAME sign
  but thin — a0.35-0.40|tl450-900|cl2-5|D dev +8.5% (n=203) hold +20.5% (n=76);
  a0.30-0.35|tl450-900|cl5-12|D dev +56.6% (n=41) hold +11.5% (n=16). No new-cell claims;
  the live fill gate remains the arbiter for the disagree family. Artifact: edge_atlas/
  (v2 backed up to edge_atlas_v2/).

### xh5y_g2_v1 TWIN DEPLOYED (2026-07-24 ~08:50 UTC) + gate
PART-B engineering shipped same-day: engine mode="xb5y" (DeterminismState._xb5y_entry —
runs ON the co-terminal 5m market, buys OWN YES on 15m-book dominance; research parity
with xbook.py 5y leg: m_5y>=premium, gap<=-g, ref_5y=15m-bid notional, ask<=ceil) +
ws_collector xb15_* co-terminal 15m fields on 5m ticks (relative lookup: parent start =
own start - 600s, same close; NaN fails closed). Params = the stronger survivor
r1_c90: premium 0.02, gap 2bps, ref $1, ask (0.03, 0.90], $10/trade, $50/day hard_worstcase.
Tests: tests/test_xb5y_mode.py (14, incl. collector emission + one-sidedness of the gap);
two stale pins refreshed (test_spot_ws_collector DOGE-now-real; test_xb_mode xb-family
exemption); suite 233+338 green (sweep_v2 excluded: pre-existing missing lightgbm).
Engine restarted ~08:47 UTC, 18 strategies, twin in heartbeat, queue 0, live set
untouched ({det_lwd_live, fav_disagree_live} pinned by test).
GATE (pre-registered above, dates now concrete): first eval 2026-08-07 on >= 14
virgin-forward days of official-settled fills; CI-lo > 0 => live talk (user sign-off);
KILL: CI-hi < 0, or EV < 0 at n >= 40. Watch item: first 5m-timeframe strategy since
cfg_5m_control — monitor queue/saturation on the 7-coin 5m tape.

---

### VOLUME-HARVEST ROUND (pre-registered 2026-07-25 ~15:0x UTC, BEFORE deploy)

Motivation (measurement, not a new claim): `fav_disagree_live` trades a strict SUBSET of its own
paper twin. `fav_disagree` is the identical rule (mode disagree, tl 120-360s, dist>=10bps) and
differs in exactly ONE number: max_ask 0.90 vs 0.45. Official-settled, since 06-19 (35 days):
twin 8.74 sig/day and +$24.28/day at $10; live 2.07 intents/day -> 0.93 fills/day -> ~+$5.3/day.
So ~78% of the validated signal is discarded by the ask cap, and the binding constraint is
COVERAGE, not edge. Discovery stays frozen per the v2/v3 door rules — this round harvests the
one surviving edge instead of hunting a new one.

DESIGN INPUTS (read off ALREADY-REVEALED data; design inputs, NOT claims — same status as the
2026-07-03 improvement axes). Source: data/research/paper_official/fav_disagree.parquet,
label_status=official, slug-clustered bootstrap, $10 size:
- cohort ask 0.46-0.60, since 06-19: n=206, EV +$1.80/fill, CI [+0.49,+3.09], 5.9 sig/day,
  WR 62%, median intent-time ask_depth $18.55.
- same cohort restricted to the 4 live-executable coins: n=141, 4.03 sig/day, EV +$1.59,
  CI [-0.09,+3.22] — SPANS ZERO. State plainly: the CI-lo>0 version of this cohort leans on
  hype, which is not live-executable today.
- hype ask>0.45: n=73, 2.09 sig/day, EV +$3.60, CI [+1.81,+5.29], WR 78%, median depth $15.3.
- hype ask<=0.45: 0.14 sig/day, EV +$0.67 — i.e. NOTHING in the current live band.
- bucket detail >0.45: (0.45,0.60] n=206 +$1.80; (0.60,0.75] n=19 +$5.05 WR 94.7% (n too small
  to act on — the 0.60 ceiling is deliberate).
- WHY THIS CELL IS NEW: the 2026-07-03 axis-3 ask-band look examined only <=0.45 (inside the
  live band), so 0.46-0.60 was never scored. It is not a re-slice of a previously-tested cell.
- Economic reading (not required for the gate, but it is why this is not a fishing expedition):
  below 0.45 you buy the side the book actively DISfavours (WR ~50%, big payoff); at 0.46-0.60
  you buy a side the book weakly favours but UNDERPRICES (WR 62%, small payoff). Same book-lag
  mechanism, different payoff shape — which is also why it deserves its own book, not a widening.
- Also note d5 (dist>=5) is DEAD above 0.45 (EV -$0.03 overall, -$0.56 since 07-03): the
  dist>=10 gate is what makes the expensive side work. Do NOT combine the two widenings.

DEPLOYED (2026-07-25): `fav_disagree_hi_live` — mode disagree, tl 120-360s, dist>=10bps,
min_ask 0.46 / max_ask 0.60 (0.46 not 0.45: determinism_state.py:402 is inclusive at BOTH ends,
so 0.45 would double-fire with fav_disagree_live on the same slug), $5/trade, own $100 bankroll,
own $25/day hard_worstcase cap, own executor book. `fav_disagree_live` is UNTOUCHED at $10 /
0.05-0.45 so the 08-03 size-rung gate keeps reading a clean book.
Expected: ~3-4 sig/day on the 4 live coins x ~45% fill = ~1.5-2 fills/day; at $5 the measured
+$1.80/fill (measured at $10) halves to ~+$0.90 => ~+$1.4-1.8/day. This rung buys the RIGHT to
$10, it is not the payoff itself.

GATE — first eval 2026-08-07 (aligns with R2/R3/R4 + xh5y):
  WINDOW: fills from deploy (2026-07-25) -> 2026-08-07.
  METRIC: official-settled realized $/fill, slug-clustered bootstrap 95% CI, from executor
  fills.jsonl x official on-chain outcomes (the honest ledger), NOT the paper twin.
  PASS: n >= 25 AND CI-lo > 0 AND fill-rate >= 40% of attempts => propose the $10 rung
        (user sign-off). KILL: CI-hi < 0, or EV < 0 at n >= 40.
  n < 25 => extend ONCE to 2026-08-21 (standing convention). No band iteration either way:
  0.60 is fixed for this test; do NOT re-cut the bucket edges after seeing the result.

AMENDMENT to the 2026-07-31 CAPACITY GATE (correction to how its PASS branch is IMPLEMENTED,
not a new claim): (1) its PASS branch must add the coin PER-STRATEGY for the disagree family,
NOT to the global EXEC_SYMBOLS — 70 of the last 75 hype intents came from det_lwd_live (the
break-even/bleeding probe), so a global add puts the wrong strategy on the new coin at 14x the
volume of the right one. New knob EXEC_SYMBOLS_EXTRA (sid:sym, empty default = today's exact
behaviour) ships 2026-07-25, UNARMED. (2) A hype PASS is worth ~nothing without the hi-ask band
(0.14 sig/day in-band): arming hype must target fav_disagree_hi_live, and remains conditional on
the live-2 guarded fill model + the registered ask_depth check (hype median $15.3 vs btc $43 —
expect partial fills) + user sign-off.

EXECUTION FIXES (bugs, not strategy changes — no gate, but before-numbers recorded so the
after-effect is checkable). Baseline = 14 days to 2026-07-25, 234 intents / 91 attempts / 75 fills:
- 4a round-2 stale re-quote: 36 of 115 ladder rounds died on `400 no orders found to match`.
  live_executor.py:582-586 bumps cur_ask to the real touch before round 1 (an IOC below the touch
  is an API-400, not a fill) but the loop at :608-630 never refreshes it, so round 2 re-fires the
  same known-bad price 4s later. FIX: re-run _preflight and re-apply the same touch-bump on a
  zero-fill round. EXPECT: the 400-share of rounds falls from 31%.
- 4b bounded dry-retry: the single 3s re-check at :560-566 rescued 7 of 17 dry verdicts, and all
  17 were best_ask > ceiling (price moved ABOVE max_ask — not a thin book; band_depth returns 0
  whenever the touch is above the ceiling and _preflight:349 labels that `dry`). Waiting for the
  book to come back INTO the band is exactly the bet this edge makes. FIX: EXEC_DRY_RETRY_N
  (default 1 = today's behaviour), armed at 3, bounded by time_left > TIME_LEFT_MIN.
  EXPECT: rescues > 7/17. This is the taker-only cheap version of registered R3 (30s resting
  limit at max_ask); R3 still evaluates on 08-07 as written, but if it passes, 4b is what should
  ship — live_executor.py:24-25 records a resting maker bleeding -$1.99/tr to adverse selection.

ROSTER (same day): det_lwd_live $5 -> $2 (-$30.40 over its last 12 traded days; +$0.078/fill
lifetime over 433 fills; official clean-era EV -$0.128/fill; its registered stop rule <= -$0.50/fill
with CI-hi<0 is NOT tripped, so this is a SIZE decision, not a kill — it stays the always-on
execution canary at ~40% of the bleed). Pruned enabled:false: psettle_ud_v1 (-$316/14d official,
-$1.43/fill) and det_disagree_v1 (-$119/14d, -$0.90/fill) — ledgers freeze 2026-07-25; CPU
headroom for the engine-saturation guard.

DATA-QUALITY NOTE (read before trusting any cl_* feature over this window): the Chainlink feed
was 100% DEAD 2026-07-24 09:02 UTC -> 2026-07-25 (9,207 chainlink_fetch_failed, ZERO successes,
all 6 coins). Cause: the built-in DEFAULT_POLYGON_RPC (Tenderly public gateway) went dead and
.env set POLYGON_RPC (claimer/relayer name) but not POLYGON_RPC_URL (collector name), so the
override never applied. NO live-money impact — neither live strategy reads cl_dist_bps (no
oracle_gate, no max_ask_hi) and official labels come from Gamma, so all open gates still score
correctly. VOID over the gap: det_d12_dual_v1 (oracle_gate agree, fail-CLOSED => fired ZERO,
which is void not neutral), psettle_ud_v1's cl leg, and every cl_* tick field. Fixed 2026-07-25
(working default + both env names + hourly_monitor rows_ok=0 alarm, validated against this
incident).

### CAPACITY GATE RESOLVED EARLY — hype ARMED 2026-07-26 (registered 2026-07-17, evaluated 5d early)

Not a re-read after seeing a result: the criteria (`n>=30 AND CI-lo>0`, official-settled EV/fill of
the fav_disagree + fav_disagree_live twins on that coin's slugs, entries >= 2026-07-17 17:00 UTC,
slug-clustered bootstrap) were fixed on 07-17 and hype crossed n>=30 on its own. Evaluated early
because the live book had gone **33h with zero fills and 20/20 intents skipped** (every one hype or
doge), i.e. the allowlist — not the edge — had become the binding constraint. Armed-coin share of
live-strategy signal: 07-16 100% -> 07-20/21 40-50% -> 07-23 28% -> 07-25 28% -> 07-26 0%
(10d aggregate 41%, last 4d 30%).

LEG 1 — capacity gate, as written:
| coin | n | EV/fill | CI | WR | depth med | sig/day | verdict |
|---|--:|--:|---|--:|--:|--:|---|
| hype | 88 | +$3.61 | [+1.56,+5.56] | 76% | $15.15 | 10.5 | **PASS** |
| doge | 22 | +$4.53 | [-1.49,+10.04] | 68% | $18.00 | 2.6 | inconclusive (n<30) |
| sol  |  8 | +$9.09 | [+2.00,+12.81] | 88% | $16.90 | 1.0 | n too small |
| btc/bnb | 4/3 | -$10.5 | — | 0% | — | 0.5 | KILL branch on n=4 noise; ignore |

LEG 2 — the registered "must survive the live-2 guarded fill model on recorded decisions".
`rejudge_live_model` CANNOT run it: `load_base()`/joined_15m covers btc/eth/sol/xrp only. Both
inputs it needs do exist for hype, so the check was run directly on them — recorded decisions =
the twins' own official-labelled in-band (0.46-0.60) entries; ladders = `data/live_l2/hype_*.csv.gz`
(10 days from 07-17); `simulate_taker_entry(mode="guarded")` at the deployed $5 stake, seed 0:

| coin | in-band decisions | fill rate | EV/fill @$5 | CI | WR | EV/signal | verdict |
|---|--:|--:|--:|---|--:|--:|---|
| hype | 61 | **59%** (36/61) | **+$2.03** | [+0.97,+3.04] | 78% | +$1.20 | **SURVIVES** |
| doge | 11 | 45% (5/11) | -$1.29 | [-5.15,+2.57] | 40% | -$0.59 | FAILS |

59% fill on a $15 median book BEATS the live 4-coin funnel. ~7.3 decisions/day x 59% ~= 4.3 fills/day
x $2.03 ~= **+$8.7/day** vs ~$0 today. STATED LIMITATION (the fill model's own, not hidden): the
zero-fill hazard is drawn RANDOMLY while real live misses are adversely selected (the missed trades
were the winners) — which is why guarded EV/fill scaled to $10 (+$4.05) comes out ABOVE the paper
+$3.67 instead of below it. Treat +$2.03 as an OPTIMISTIC bound; the honest expectation is lower.

LEG 3 — user sign-off 2026-07-26 (Israel afternoon).

ARMED: `.env EXEC_SYMBOLS_EXTRA=fav_disagree_hi_live:hype`, executor bounced 15:5x IDT, verified
`symbols_extra={'fav_disagree_hi_live': ['hype']}` and all 5 books preserved. hype ONLY, and for
`fav_disagree_hi_live` ONLY — per the 07-25 AMENDMENT. det_lwd_live deliberately gets NO hype
(70 of its last 75 intents were hype; -$0.128/fill official; now $2/trade). doge stays paper-only:
it fails BOTH legs.

FORWARD GATES (unchanged dates, now measurable instead of expiring at n=0):
- 2026-08-07 `fav_disagree_hi_live`: n>=25 fills AND official CI-lo>0 AND fill-rate>=40% => propose
  $10. KILL if CI-hi<0 or EV<0 at n>=40. At 4.3 fills/day hype alone clears n=25 in ~6 days.
- NEW, same date: realised hype fill-rate vs the 59% predicted here. A material miss (<40%) means
  the guarded model over-credits thin books and the whole leg-2 method needs re-calibration before
  it is used to arm anything else.
- doge re-evaluates 2026-08-14 under the standing "extend 2 more weeks, once" branch.

### GATE-WEEK PREP (2026-08-01, bookkeeping + two dispositions; no gated slice was scored)

TOOLING: `research/analysis/score_gates.py` is now the registered gate-metric calculator
(official-settled $/fill + per-$ EV, slug-clustered bootstrap 95% CI = [p2.5,p97.5], the
07-17 published precedent; `research.lib.stats.window_clustered_bootstrap`'s p5/p95 is a
90% interval and is NOT the gate convention). Validated on known answers: reproduces the
07-17 fav_disagree_live read exactly (n=23, +$72.33, +$3.145/fill, per-$ +0.339) and the
canonical scoreboard virgin rows (xh5y n=68 +$1.949; xb n=521 +$0.923; fav_disagree_live
n=145 +$2.662) to the third decimal. Pinned tests: tests/research/test_score_gates.py.
KNOWN LIMIT, registered: the 07-26 hype leg-1 ad hoc numbers (n=88 +$3.61) are NOT exactly
reproducible from today's canonical parquet (nearest cut: n=86-90, +$2.75-2.96; the eval
scored against the label snapshot available that morning). The canonical
`official_outcomes.parquet` x `paper_official/` pipeline is authoritative for every future
gate; ad hoc snapshots are not re-litigated. Discipline note: NO gated forward slice
(R1/R2/R4, the 08-03/08-07 rung windows) was computed during validation; known-answer
windows only.

DISPOSITION 1 - bnb capacity: the 07-31 capacity gate resolved early on 07-26 without
dating bnb's else-branch ("extend 2 more weeks, once"). Registered now: bnb re-evaluates
2026-08-14 alongside doge, ONE look, same criteria (n>=30 official CI-lo>0 + live-2
guarded survive + ask-depth check). Still inconclusive then => drop (thin-liq suspicion).

DISPOSITION 2 - R3 (30s resting-limit rescue) CLOSED without eval: 9 forward misses
accumulated since 07-24 vs the required n>=25 by 08-07; it cannot reach quorum. Per the
07-25 amendment, 4b (`EXEC_DRY_RETRY_N=3`, taker-only retry) is the shipping substitute
and is ALREADY armed since the 07-25 executor bounce. The miss stream keeps accumulating
in fills.jsonl/intents; if 4b's rescue rate disappoints, R3 may be re-registered FRESH on
new misses (no peeking at the 9 banked ones for design).

WATCH-ITEM CLOSED (registered 07-25, due ~08-01): GLOBAL_MAX_CONCURRENT=4 produced ZERO
intent_skipped events over the entire live_exec.log; not a binding constraint. No change.

det_lwd_live REGISTERED-METHOD READ (score_gates, fills x official): lifetime n=412
+$0.034/fill CI [-0.19,+0.25] (break-even canary, as designed); last-14d n=56 -$0.522/fill
CI [-1.10,+0.01]; since the $2 resize (07-25) n=25 -$0.288/fill CI [-0.66,+0.07]. The
registered stop rule (<= -$0.50/fill AND CI-hi < 0) is NOT mechanically tripped: the EV
leg crossed, the CI-hi leg misses by $0.01. Decision (keep the $2 canary vs stop) goes to
the user; no auto-action.

### EDGE HUNT v4 PRE-REGISTERED (2026-08-01, sealed BEFORE any look at 07-24+ data)

Full registration: `docs/research/EDGE_HUNT_V4_PREREG_2026-08.md`. Window 2026-07-24 ->
2026-08-14 (unmined by any sweep), reveal 2026-08-15, ONE look. V4a = atlas v4
(dev 07-24..08-06 / sealed holdout 08-07..08-14; persistence verdict on the disagree
cells + new-cell scan). V4b = the two frozen early-timing disagree cells (second and
LAST look for that thread). All 7 coins; official labels; guarded fills; slug-clustered
95% CI; BH-10%. NOT run: broad sweep, flow, maker, g2bps grid iteration. Both fail =>
next campaign waits for the next 3-week block (earliest 2026-09-05).

### GATE 2026-08-03 SCORED: fav_disagree_live $15 rung -> EXTEND at $10 (2nd extension)

Registered command, run verbatim:
`score_gates live --sid fav_disagree_live --since 2026-07-03T07:40 --until 2026-08-03`

Result: **n=31 (31 slugs), total +$120.74, EV/fill +$3.895 CI [-0.41,+8.12], WR 55%,
per-$ +0.422 CI [-0.05,+0.88], fill-rate 31/41, pending 0.**

Registered rule: CI-lo>0 on BOTH $/fill and per-$ => propose $15; spans 0 => extend at $10;
<= -$0.50/fill with CI-hi<0 => stop. **BOTH CIs span zero** (per-$ by $0.05), so the branch
is EXTEND AT $10 — the 2nd extension, no config change. Point estimates are strongly positive
and the per-$ leg is one fill from clearing, but the rule is the rule; we do not size up on a
CI that spans zero.

Next look **2026-08-17** (standing 2-week convention), same command with `--until 2026-08-17`.
Note for that read: the book took 41 attempts to get 31 fills across the whole window and its
last fill was 07-29 20:25 IDT, so n grows ~1/day at best. If n has not moved materially by
08-17, the honest branch is a 3rd extension, not a verdict — do not force one on n<40.

Deliberately NOT done today: no band iteration, no dist_min change, no re-scoring on other
windows, no peeking at the 08-06/08-07 gate slices.

### NEW HYPOTHESIS REGISTERED 2026-08-03: fill-time floor leak ("knife fills")

**Found while verifying the fills-accounting item, NOT by a sweep.** Bounded read-only check
of `avg_price` vs `quoted_ask` on the live ledger x official labels (n=741 labelled fills).

Accounting first (the original question): `usdc_paid / filled_shares == avg_price` on
**742/742** fills. The share counts and dollars are internally consistent; `avg_price` is the
realized VWAP of a laddered fill, so beating the round-1 quote is expected, not a bug. **That
item is CLOSED.**

What the check surfaced instead — split live fills by `drop = quoted_ask - avg_price`:

| cohort | n | EV/fill | per-$ | WR | total |
|---|--:|--:|--:|--:|--:|
| drop > 5c | 91 | **-0.727** | -0.159 | 57% | **-$66.12** |
| drop 1-5c | 229 | +0.363 | +0.074 | 71% | +$83.19 |
| drop <= 1c | 421 | -0.076 | -0.016 | 74% | -$31.96 |

Confounds tested and REJECTED:
- **Not the dead `det_d12_dual_live` book**: every strategy's knife cohort is negative
  (det_lwd -7.90/52, fav_disagree_live -15.29/4, det_d12_wide -6.68/4, hi_live -4.59/1).
- **Not pre-guard history**: post-07-06 (floor guard + clamp era) the effect is STRONGER —
  knife n=19 EV **-1.335** per-$ -0.332 WR 47%, vs the rest at EV +0.696.
- **Currently-armed books only** (det_lwd_live + fav_disagree_live + fav_disagree_hi_live):
  knife n=57 EV -0.487 per-$ -0.113 WR 60% **total -$27.78**; rest n=427 EV +0.174 per-$
  +0.037 WR 74% **total +$74.33**. ~12% of fills; 11.8% of deployed capital.

**Mechanism (already validated, not a new claim).** This is the SAME cohort `EXEC_FLOOR_DROP`
was built for — `_preflight`'s docstring already says a cheap fill on a collapsing book "is a
knife-catch (-EV cohort, measured)". The guard only tests the book at PREFLIGHT
(`best_ask < entry_ask - 0.04`). These fills PASS preflight (all 14 post-guard cases carry
`guard.verdict == "ok"`) and then the book falls away during the ladder, so the same -EV
cohort re-enters at fill time. It is an enforcement gap in a validated guard, not a new edge.

**Pre-registered, because a post-hoc live slice is exactly how this project has been fooled
43 times.** NOTHING ships today.

- **Instrument first (recommended):** add the fill-time floor check in **shadow mode** only —
  log `would_abort_fill_floor` with the drop, change no behaviour. Zero gate contamination
  (shadow cannot alter any measurement), and it produces clean FORWARD data instead of another
  post-hoc slice.
- **Gate date 2026-08-24** (3 forward weeks). **Rule, fixed now:** on forward fills only, with
  official labels and slug-clustered 95% CI — promote the guard to enforce iff the shadow-flagged
  cohort has **n>=25 AND EV/fill < 0 AND CI-hi < 0**; if EV >= 0 or CI-hi >= 0, DROP the idea
  permanently (do not re-slice by threshold). Threshold frozen at **drop > 0.05**; no grid
  search over the cut, and no re-reading of the historical cohort above.
- Requires user sign-off before ANY enforce step: it changes real-money execution.

Why this could matter more than a sizing rung: on the three armed books the normal-fill cohort
is **+$74.33** while the knife cohort is **-$27.78**. If forward data confirms, the live book's
sign flips on execution alone, with no new edge and no extra risk.
