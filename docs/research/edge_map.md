# Edge Map

Phase 2–4 Edge Discovery findings for Polymarket Up/Down (May 2026).

> **RE-RUN ON CORRECTED DATA (real Polymarket outcomes) — supersedes the
> earlier corrupt-label results. (2026-05-22)**
>
> Phase 2's first pass ran on corrupt outcome labels: a strike bug
> (`docs/research/phase0_audit.md` Task 8c) put the wrong outcome on ~31% of
> windows. `data/research/{windows,ticks_15m,entry_candidates_15m}.parquet`
> have since been rebuilt with the true Polymarket-resolved outcomes and
> corrected strikes / `move_pct` / `proximity_pct` / `sigma_proximity`. The
> **Task 6b**, **Conditioned edge map**, and **Task 8** sections below were
> re-derived on the corrected data and carry their own dated re-run headers.
> The **Unconditional Calibration (Task 6)** section immediately below is the
> ORIGINAL corrupt-label tick-pooled result and is **superseded by Task 6b** —
> it is kept only for historical reference; do not cite its numbers.
>
> **Headline (corrected data):** there is **no real, cost-surviving,
> out-of-fold cheap-side edge** in the calibration, the conditioned edge map,
> or the divergence signal. See `docs/research/PHASE2_RERUN_VERDICT.md`.

## Unconditional Calibration (Task 6) — SUPERSEDED (corrupt labels)

> **This section used the corrupt pre-fix outcome labels. Superseded by
> "Task 6b — De-biased calibration" below, which re-runs on corrected data.
> The +13c headline gap reported here is a label artifact and is not real.**

**Data:** 1,502,307 dev-split ticks, 1,676 slugs, May 15–20 2026. Sealed hold-out (May 21–22) NOT touched.
**Method:** reliability_curve(), window-clustered bootstrap (n=1000), groups=slug, 20 equal-width bins on [0, 1].

### Sanity checks

- **All-states mean consistency:** predicted=0.4674, realized=0.4708, |diff|=0.0034 → **PASS**
- **cheap_mid bin thickness:** all bins n_windows ≥ 20

---

### cheap_mid reliability (20 bins)

| Bin | Mean pred | Realized | CI lo | CI hi | n_windows | n_ticks | Note |
|-----|-----------|----------|-------|-------|-----------|---------|------|
| [0.00, 0.05) | 0.0142 | 0.3091 (+0.295) | 0.2901 | 0.3282 | 1,505 | 284,611 |  |
| [0.05, 0.10) | 0.0716 | 0.3006 (+0.229) | 0.2762 | 0.3261 | 1,486 | 122,728 |  |
| [0.10, 0.15) | 0.1250 | 0.3112 (+0.186) | 0.2869 | 0.3371 | 1,508 | 114,725 |  |
| [0.15, 0.20) | 0.1754 | 0.3512 (+0.176) | 0.3264 | 0.3758 | 1,494 | 98,655 |  |
| [0.20, 0.25) | 0.2242 | 0.3604 (+0.136) | 0.3362 | 0.3848 | 1,516 | 112,150 |  |
| [0.25, 0.30) | 0.2757 | 0.4020 (+0.126) | 0.3788 | 0.4248 | 1,539 | 122,659 |  |
| [0.30, 0.35) | 0.3269 | 0.4433 (+0.116) | 0.4217 | 0.4647 | 1,537 | 130,893 |  |
| [0.35, 0.40) | 0.3753 | 0.4613 (+0.086) | 0.4421 | 0.4809 | 1,533 | 132,981 |  |
| [0.40, 0.45) | 0.4246 | 0.4980 (+0.073) | 0.4820 | 0.5139 | 1,563 | 165,105 |  |
| [0.45, 0.50) | 0.4755 | 0.5255 (+0.050) | 0.5140 | 0.5367 | 1,647 | 211,727 |  |
| [0.50, 0.55) | 0.5000 | 0.4833 (-0.017) | 0.4258 | 0.5459 | 476 | 6,073 |  |

**cheap_mid: UNDERPRICED (buyer edge) — mean gap +13.25¢**
  Mean realized−predicted = +13.250¢ across 11 thick bins. Bins with CI entirely above zero (buyer edge): 10. Bins with CI entirely below zero (overpriced): 0.

**Bin-level gaps (realized − predicted) for cheap_mid:**

  - [0.00, 0.05): gap=+29.49¢  CI=(+27.59¢, +31.40¢)  CI excl diag  n_win=1,505
  - [0.05, 0.10): gap=+22.89¢  CI=(+20.46¢, +25.45¢)  CI excl diag  n_win=1,486
  - [0.10, 0.15): gap=+18.62¢  CI=(+16.19¢, +21.21¢)  CI excl diag  n_win=1,508
  - [0.15, 0.20): gap=+17.58¢  CI=(+15.10¢, +20.04¢)  CI excl diag  n_win=1,494
  - [0.20, 0.25): gap=+13.62¢  CI=(+11.20¢, +16.06¢)  CI excl diag  n_win=1,516
  - [0.25, 0.30): gap=+12.64¢  CI=(+10.32¢, +14.92¢)  CI excl diag  n_win=1,539
  - [0.30, 0.35): gap=+11.64¢  CI=(+9.48¢, +13.78¢)  CI excl diag  n_win=1,537
  - [0.35, 0.40): gap=+8.60¢  CI=(+6.68¢, +10.57¢)  CI excl diag  n_win=1,533
  - [0.40, 0.45): gap=+7.34¢  CI=(+5.74¢, +8.93¢)  CI excl diag  n_win=1,563
  - [0.45, 0.50): gap=+5.01¢  CI=(+3.85¢, +6.12¢)  CI excl diag  n_win=1,647
  - [0.50, 0.55): gap=-1.67¢  CI=(-7.42¢, +4.59¢)  CI incl diag  n_win=476

### cheap_ask reliability (taker entry price, 20 bins)

| Bin | Mean pred | Realized | CI lo | CI hi | n_windows | n_ticks | Note |
|-----|-----------|----------|-------|-------|-----------|---------|------|
| [0.00, 0.05) | 0.0155 | 0.3143 (+0.299) | 0.2944 | 0.3336 | 1,475 | 255,115 |  |
| [0.05, 0.10) | 0.0690 | 0.2925 (+0.223) | 0.2666 | 0.3174 | 1,463 | 125,838 |  |
| [0.10, 0.15) | 0.1253 | 0.3098 (+0.184) | 0.2853 | 0.3341 | 1,534 | 128,268 |  |
| [0.15, 0.20) | 0.1748 | 0.3447 (+0.170) | 0.3183 | 0.3710 | 1,432 | 82,862 |  |
| [0.20, 0.25) | 0.2201 | 0.3606 (+0.141) | 0.3359 | 0.3851 | 1,515 | 110,560 |  |
| [0.25, 0.30) | 0.2755 | 0.3905 (+0.115) | 0.3677 | 0.4134 | 1,567 | 139,326 |  |
| [0.30, 0.35) | 0.3305 | 0.4376 (+0.107) | 0.4163 | 0.4582 | 1,536 | 129,070 |  |
| [0.35, 0.40) | 0.3753 | 0.4590 (+0.084) | 0.4385 | 0.4796 | 1,502 | 111,657 |  |
| [0.40, 0.45) | 0.4208 | 0.4891 (+0.068) | 0.4721 | 0.5063 | 1,546 | 154,434 |  |
| [0.45, 0.50) | 0.4712 | 0.5220 (+0.051) | 0.5092 | 0.5348 | 1,608 | 194,424 |  |
| [0.50, 0.55) | 0.5038 | 0.5157 (+0.012) | 0.4981 | 0.5337 | 1,444 | 70,005 |  |
| [0.55, 0.60) | 0.5633 | 0.4529 (-0.110) | 0.3106 | 0.6029 | 94 | 541 |  |
| [0.60, 0.65) | 0.6221 | 0.2824 (-0.340) | 0.0291 | 0.6275 | 15 | 131 | thin |
| [0.65, 0.70) | 0.6855 | 0.3500 (-0.336) | 0.0938 | 0.8750 | 7 | 20 | thin |
| [0.70, 0.75) | 0.7100 | 0.0000 (-0.710) | 0.0000 | 0.0000 | 1 | 1 | thin |
| [0.75, 0.80) | 0.7600 | 0.5000 (-0.260) | 0.0000 | 1.0000 | 2 | 2 | thin |
| [0.80, 0.85) | 0.8225 | 0.7500 (-0.073) | 0.0000 | 1.0000 | 2 | 48 | thin |
| [0.90, 0.95) | 0.9100 | 0.0000 (-0.910) | 0.0000 | 0.0000 | 2 | 5 | thin |

**cheap_ask: UNDERPRICED (buyer edge) — mean gap +11.19¢**
  Mean realized−predicted = +11.194¢ across 12 thick bins. Bins with CI entirely above zero (buyer edge): 10. Bins with CI entirely below zero (overpriced): 0.

### All-states yes_mid vs outcome_up (20 bins)

| Bin | Mean pred | Realized | CI lo | CI hi | n_windows | n_ticks | Note |
|-----|-----------|----------|-------|-------|-----------|---------|------|
| [0.00, 0.05) | 0.0106 | 0.3266 (+0.316) | 0.3008 | 0.3526 | 1,378 | 191,119 |  |
| [0.05, 0.10) | 0.0715 | 0.2835 (+0.212) | 0.2482 | 0.3197 | 773 | 63,997 |  |
| [0.10, 0.15) | 0.1248 | 0.2836 (+0.159) | 0.2492 | 0.3184 | 804 | 57,648 |  |
| [0.15, 0.20) | 0.1759 | 0.3191 (+0.143) | 0.2830 | 0.3582 | 808 | 48,345 |  |
| [0.20, 0.25) | 0.2242 | 0.3327 (+0.108) | 0.2973 | 0.3692 | 875 | 54,615 |  |
| [0.25, 0.30) | 0.2759 | 0.3670 (+0.091) | 0.3336 | 0.4012 | 965 | 60,214 |  |
| [0.30, 0.35) | 0.3271 | 0.3988 (+0.072) | 0.3664 | 0.4318 | 1,045 | 63,945 |  |
| [0.35, 0.40) | 0.3754 | 0.4320 (+0.057) | 0.4003 | 0.4646 | 1,138 | 68,063 |  |
| [0.40, 0.45) | 0.4245 | 0.4823 (+0.058) | 0.4519 | 0.5133 | 1,272 | 80,860 |  |
| [0.45, 0.50) | 0.4749 | 0.5080 (+0.033) | 0.4803 | 0.5351 | 1,432 | 104,162 |  |
| [0.50, 0.55) | 0.5217 | 0.4594 (-0.062) | 0.4308 | 0.4865 | 1,517 | 109,826 |  |
| [0.55, 0.60) | 0.5741 | 0.4848 (-0.089) | 0.4545 | 0.5149 | 1,260 | 87,926 |  |
| [0.60, 0.65) | 0.6248 | 0.5083 (-0.117) | 0.4759 | 0.5417 | 1,083 | 65,206 |  |
| [0.65, 0.70) | 0.6745 | 0.5130 (-0.162) | 0.4800 | 0.5461 | 1,038 | 69,886 |  |
| [0.70, 0.75) | 0.7245 | 0.5715 (-0.153) | 0.5364 | 0.6071 | 949 | 56,361 |  |
| [0.75, 0.80) | 0.7734 | 0.6112 (-0.162) | 0.5758 | 0.6450 | 897 | 57,988 |  |
| [0.80, 0.85) | 0.8251 | 0.6180 (-0.207) | 0.5820 | 0.6541 | 842 | 55,404 |  |
| [0.85, 0.90) | 0.8746 | 0.6562 (-0.218) | 0.6188 | 0.6925 | 790 | 51,548 |  |
| [0.90, 0.95) | 0.9268 | 0.6822 (-0.245) | 0.6469 | 0.7167 | 781 | 61,750 |  |
| [0.95, 1.00) | 0.9785 | 0.7268 (-0.252) | 0.6892 | 0.7651 | 721 | 93,444 |  |

**all-states yes_mid: OVERPRICED (favorite-longshot / buyer loses) — mean gap -2.09¢**
  Mean realized−predicted = -2.089¢ across 20 thick bins. Bins with CI entirely above zero (buyer edge): 10. Bins with CI entirely below zero (overpriced): 10.

### Per-symbol cheap_mid reliability

### BTC — cheap_mid

| Bin | Mean pred | Realized | CI lo | CI hi | n_windows | n_ticks | Note |
|-----|-----------|----------|-------|-------|-----------|---------|------|
| [0.00, 0.05) | 0.0158 | 0.3330 (+0.317) | 0.2954 | 0.3732 | 373 | 68,440 |  |
| [0.05, 0.10) | 0.0728 | 0.3154 (+0.243) | 0.2683 | 0.3641 | 380 | 30,847 |  |
| [0.10, 0.15) | 0.1250 | 0.2968 (+0.172) | 0.2512 | 0.3431 | 389 | 26,146 |  |
| [0.15, 0.20) | 0.1749 | 0.3682 (+0.193) | 0.3182 | 0.4190 | 382 | 24,765 |  |
| [0.20, 0.25) | 0.2249 | 0.3671 (+0.142) | 0.3198 | 0.4154 | 386 | 27,965 |  |
| [0.25, 0.30) | 0.2752 | 0.3671 (+0.092) | 0.3242 | 0.4111 | 391 | 29,827 |  |
| [0.30, 0.35) | 0.3259 | 0.4410 (+0.115) | 0.4009 | 0.4822 | 390 | 33,127 |  |
| [0.35, 0.40) | 0.3751 | 0.4640 (+0.089) | 0.4272 | 0.5015 | 392 | 35,979 |  |
| [0.40, 0.45) | 0.4248 | 0.5174 (+0.093) | 0.4873 | 0.5485 | 394 | 42,780 |  |
| [0.45, 0.50) | 0.4759 | 0.5050 (+0.029) | 0.4826 | 0.5275 | 412 | 55,029 |  |
| [0.50, 0.55) | 0.5000 | 0.5375 (+0.037) | 0.4013 | 0.6747 | 119 | 573 |  |

**BTC: UNDERPRICED (buyer edge) — mean gap +13.84¢**
  Mean realized−predicted = +13.838¢ across 11 thick bins. Bins with CI entirely above zero (buyer edge): 10. Bins with CI entirely below zero (overpriced): 0.

### ETH — cheap_mid

| Bin | Mean pred | Realized | CI lo | CI hi | n_windows | n_ticks | Note |
|-----|-----------|----------|-------|-------|-----------|---------|------|
| [0.00, 0.05) | 0.0132 | 0.3138 (+0.301) | 0.2775 | 0.3508 | 380 | 71,644 |  |
| [0.05, 0.10) | 0.0722 | 0.3045 (+0.232) | 0.2557 | 0.3537 | 372 | 31,466 |  |
| [0.10, 0.15) | 0.1246 | 0.3445 (+0.220) | 0.2926 | 0.3955 | 376 | 27,338 |  |
| [0.15, 0.20) | 0.1758 | 0.3814 (+0.206) | 0.3300 | 0.4337 | 382 | 25,407 |  |
| [0.20, 0.25) | 0.2248 | 0.4013 (+0.177) | 0.3523 | 0.4494 | 380 | 28,595 |  |
| [0.25, 0.30) | 0.2758 | 0.4818 (+0.206) | 0.4319 | 0.5285 | 384 | 31,621 |  |
| [0.30, 0.35) | 0.3267 | 0.4827 (+0.156) | 0.4408 | 0.5237 | 394 | 33,885 |  |
| [0.35, 0.40) | 0.3750 | 0.4979 (+0.123) | 0.4608 | 0.5337 | 391 | 33,447 |  |
| [0.40, 0.45) | 0.4253 | 0.5092 (+0.084) | 0.4770 | 0.5407 | 393 | 41,640 |  |
| [0.45, 0.50) | 0.4751 | 0.5345 (+0.059) | 0.5118 | 0.5577 | 407 | 49,356 |  |
| [0.50, 0.55) | 0.5000 | 0.4413 (-0.059) | 0.3184 | 0.5746 | 111 | 1,176 |  |

**ETH: UNDERPRICED (buyer edge) — mean gap +15.49¢**
  Mean realized−predicted = +15.493¢ across 11 thick bins. Bins with CI entirely above zero (buyer edge): 10. Bins with CI entirely below zero (overpriced): 0.

### SOL — cheap_mid

| Bin | Mean pred | Realized | CI lo | CI hi | n_windows | n_ticks | Note |
|-----|-----------|----------|-------|-------|-----------|---------|------|
| [0.00, 0.05) | 0.0143 | 0.2948 (+0.280) | 0.2568 | 0.3329 | 377 | 71,393 |  |
| [0.05, 0.10) | 0.0706 | 0.2838 (+0.213) | 0.2364 | 0.3325 | 367 | 29,745 |  |
| [0.10, 0.15) | 0.1256 | 0.3226 (+0.197) | 0.2700 | 0.3759 | 382 | 31,579 |  |
| [0.15, 0.20) | 0.1754 | 0.3363 (+0.161) | 0.2862 | 0.3889 | 372 | 26,196 |  |
| [0.20, 0.25) | 0.2235 | 0.3042 (+0.081) | 0.2602 | 0.3496 | 378 | 28,549 |  |
| [0.25, 0.30) | 0.2759 | 0.3764 (+0.101) | 0.3325 | 0.4220 | 384 | 29,853 |  |
| [0.30, 0.35) | 0.3269 | 0.4301 (+0.103) | 0.3870 | 0.4740 | 371 | 30,752 |  |
| [0.35, 0.40) | 0.3760 | 0.4143 (+0.038) | 0.3738 | 0.4559 | 372 | 31,798 |  |
| [0.40, 0.45) | 0.4241 | 0.4623 (+0.038) | 0.4290 | 0.4962 | 390 | 40,306 |  |
| [0.45, 0.50) | 0.4759 | 0.5393 (+0.063) | 0.5168 | 0.5615 | 414 | 53,500 |  |
| [0.50, 0.55) | 0.5000 | 0.4890 (-0.011) | 0.3860 | 0.5921 | 125 | 1,951 |  |

**SOL: UNDERPRICED (buyer edge) — mean gap +11.50¢**
  Mean realized−predicted = +11.500¢ across 11 thick bins. Bins with CI entirely above zero (buyer edge): 9. Bins with CI entirely below zero (overpriced): 0.

### XRP — cheap_mid

| Bin | Mean pred | Realized | CI lo | CI hi | n_windows | n_ticks | Note |
|-----|-----------|----------|-------|-------|-----------|---------|------|
| [0.00, 0.05) | 0.0135 | 0.2961 (+0.283) | 0.2582 | 0.3351 | 375 | 73,134 |  |
| [0.05, 0.10) | 0.0709 | 0.2979 (+0.227) | 0.2465 | 0.3508 | 367 | 30,670 |  |
| [0.10, 0.15) | 0.1247 | 0.2810 (+0.156) | 0.2353 | 0.3297 | 361 | 29,662 |  |
| [0.15, 0.20) | 0.1756 | 0.3153 (+0.140) | 0.2653 | 0.3666 | 358 | 22,287 |  |
| [0.20, 0.25) | 0.2236 | 0.3693 (+0.146) | 0.3189 | 0.4189 | 372 | 27,041 |  |
| [0.25, 0.30) | 0.2757 | 0.3793 (+0.104) | 0.3345 | 0.4245 | 380 | 31,358 |  |
| [0.30, 0.35) | 0.3281 | 0.4176 (+0.090) | 0.3771 | 0.4605 | 382 | 33,129 |  |
| [0.35, 0.40) | 0.3750 | 0.4666 (+0.092) | 0.4278 | 0.5065 | 378 | 31,757 |  |
| [0.40, 0.45) | 0.4243 | 0.5017 (+0.077) | 0.4705 | 0.5325 | 386 | 40,379 |  |
| [0.45, 0.50) | 0.4749 | 0.5247 (+0.050) | 0.5014 | 0.5477 | 414 | 53,842 |  |
| [0.50, 0.55) | 0.5000 | 0.4863 (-0.014) | 0.3780 | 0.5933 | 121 | 2,373 |  |

**XRP: UNDERPRICED (buyer edge) — mean gap +12.27¢**
  Mean realized−predicted = +12.269¢ across 11 thick bins. Bins with CI entirely above zero (buyer edge): 10. Bins with CI entirely below zero (overpriced): 0.

**Chart:** `docs/research/charts/calibration_unconditional.png`

---

## Task 6b -- De-biased calibration

**Why this section exists.** Task 6 above pools ALL ticks into each price bin. Phase 0 found ~87% of ticks are stale (book frozen). A side crashing 0.5->0 passes through the 0.14 bin in a few ticks; a *contested* side that dips to ~0.14 and oscillates lingers there for hundreds of stale ticks. Each price bin is therefore over-weighted toward lingering/contested states, which win far more often than transient pass-throughs at the same price. Tick-pooling inflates the realized win rate in every cheap bin. The window-clustered bootstrap fixes CI *independence*, NOT this *weighting* bias.

**Confirmed empirically:** in the cheap_mid [0.10,0.15) bin, ~1,494 windows contribute ~109k ticks -- a mean of ~73 ticks per contributing window (max 430). A single lingering window counted 73x.

**De-biased method.** The unit of observation is one row per (window, time-slice). For each 15m window and each fixed slice t in [60, 120, 240, 360, 480, 600, 720] seconds-into-window, take the single tick closest to t (within +/-5s; skip the (window,t) pair if none). That tick contributes ONE observation. Every window then contributes <=7 observations total; the window-clustered bootstrap (groups=slug, n=2000) still clusters by window. Dev split May 15-20 2026 only; sealed hold-out (May 21-22) NOT touched. Cross-section: **11,700 observations**, **1,676 windows**, 15 bins.

**Cheap-side flips.** 1,112 of 1,676 windows contribute at multiple slices with DIFFERENT `cheap_side` values (the cheap side flips YES<->NO intra-window). That is fine and expected: `cheap_won` in the table is computed against the side that is cheap *at that observation's tick*, so each row is self-consistent. A window contributing 0.14-YES at t=60 and 0.14-NO at t=600 is two independent, correctly-labelled observations.

#### De-biased pooled reliability -- cheap_mid

| Bin | Mean pred | Realized | CI lo | CI hi | n_windows | n_obs | Note |
|-----|-----------|----------|-------|-------|-----------|-------|------|
| [0.00, 0.07) | 0.0299 | 0.0986 (+6.87c) | 0.0851 | 0.1126 | 860 | 1,674 |  |
| [0.07, 0.13) | 0.1000 | 0.0753 (-2.47c) | 0.0596 | 0.0918 | 760 | 1,022 |  |
| [0.13, 0.20) | 0.1662 | 0.1386 (-2.76c) | 0.1180 | 0.1594 | 826 | 1,104 |  |
| [0.20, 0.27) | 0.2338 | 0.2019 (-3.19c) | 0.1794 | 0.2246 | 913 | 1,253 |  |
| [0.27, 0.33) | 0.3010 | 0.2744 (-2.66c) | 0.2513 | 0.2976 | 988 | 1,385 |  |
| [0.33, 0.40) | 0.3662 | 0.3417 (-2.45c) | 0.3188 | 0.3638 | 1,081 | 1,747 |  |
| [0.40, 0.47) | 0.4347 | 0.4258 (-0.89c) | 0.4074 | 0.4442 | 1,245 | 2,290 |  |
| [0.47, 0.53) | 0.4845 | 0.4873 (+0.29c) | 0.4625 | 0.5119 | 858 | 1,225 |  |

De-biased pooled mean gap (thick bins) = **-0.91c**.

#### De-biased pooled reliability -- cheap_ask (taker entry price)

| Bin | Mean pred | Realized | CI lo | CI hi | n_windows | n_obs | Note |
|-----|-----------|----------|-------|-------|-----------|-------|------|
| [0.00, 0.07) | 0.0306 | 0.1055 (+7.49c) | 0.0902 | 0.1209 | 786 | 1,450 |  |
| [0.07, 0.13) | 0.0994 | 0.0683 (-3.11c) | 0.0537 | 0.0840 | 822 | 1,157 |  |
| [0.13, 0.20) | 0.1648 | 0.1275 (-3.73c) | 0.1064 | 0.1494 | 732 | 949 |  |
| [0.20, 0.27) | 0.2298 | 0.1972 (-3.26c) | 0.1743 | 0.2202 | 914 | 1,268 |  |
| [0.27, 0.33) | 0.3015 | 0.2562 (-4.53c) | 0.2344 | 0.2780 | 1,019 | 1,483 |  |
| [0.33, 0.40) | 0.3660 | 0.3397 (-2.63c) | 0.3161 | 0.3625 | 1,003 | 1,525 |  |
| [0.40, 0.47) | 0.4313 | 0.4027 (-2.85c) | 0.3845 | 0.4218 | 1,220 | 2,195 |  |
| [0.47, 0.53) | 0.4875 | 0.4847 (-0.28c) | 0.4643 | 0.5052 | 1,038 | 1,669 |  |
| [0.53, 0.60) | 0.5550 | 0.7500 (+19.50c) | 0.5000 | 1.0000 | 4 | 4 | thin |

De-biased pooled cheap_ask mean gap (thick bins) = **-1.61c**.

### Bias quantification -- tick-pooled (Task 6) vs de-biased

`Artifact removed = Task6 realized - de-biased realized` -- the inflation that the tick-weighting bias added. `De-biased gap = de-biased realized - mean pred` -- the edge that survives.

| Bin | Mean pred | Tick-pooled realized | Tick-pooled n_ticks | De-biased realized | Tick-pooled gap | De-biased gap | Artifact removed |
|-----|-----------|----------------------|---------------------|--------------------|-----------------|---------------|------------------|
| [0.00, 0.07) | 0.0299 | 0.1525 | 341,616 | 0.0986 | +13.09c | +6.87c | +5.39c |
| [0.07, 0.13) | 0.1000 | 0.0848 | 136,536 | 0.0753 | -1.48c | -2.47c | +0.95c |
| [0.13, 0.20) | 0.1662 | 0.1324 | 142,567 | 0.1386 | -3.25c | -2.76c | -0.62c |
| [0.20, 0.27) | 0.2338 | 0.2031 | 156,146 | 0.2019 | -3.09c | -3.19c | +0.12c |
| [0.27, 0.33) | 0.3010 | 0.2689 | 155,100 | 0.2744 | -3.16c | -2.66c | -0.54c |
| [0.33, 0.40) | 0.3662 | 0.3418 | 187,437 | 0.3417 | -2.37c | -2.45c | +0.01c |
| [0.40, 0.47) | 0.4347 | 0.4273 | 238,695 | 0.4258 | -0.79c | -0.89c | +0.15c |
| [0.47, 0.53) | 0.4845 | 0.4775 | 144,210 | 0.4873 | -0.75c | +0.29c | -0.98c |

### Per time-slice de-biased reliability (cheap_mid)

Each window contributes exactly one observation per bin per slice -- no lingering bias within a slice.

| Time-slice | n_windows | Mean gap (all thick bins) | Mean gap (cheap_mid<0.30) |
|-----------|-----------|---------------------------|---------------------------|
| t=60s | ~670 | -3.32c | -14.55c |
| t=120s | ~556 | -2.76c | -5.54c |
| t=240s | ~333 | -1.59c | -3.57c |
| t=360s | ~276 | -1.85c | -1.22c |
| t=480s | ~264 | -2.77c | -1.25c |
| t=600s | ~502 | -2.76c | -0.90c |
| t=720s | ~797 | -1.30c | +2.23c |

The pooled per-slice gap grows with t, BUT late slices also contain far more genuinely-cheap observations -- a price-MIX effect. The fixed-price-band tables below isolate the true time component.

### Fixed-price-band x time-slice (price-mix control)

Holding the price band fixed: if the gap is roughly flat across slices, the per-slice 'edge grows late' pattern is a price-mix artifact, not a genuine late-window effect.

#### Fixed price band [0.10,0.20) -- gap by time-slice

| Time-slice | n | Mean pred | Realized | Gap |
|-----------|---|-----------|----------|-----|
| t=60s | 16 | 0.1625 | 0.0000 | -16.25c |
| t=120s | 69 | 0.1589 | 0.1304 | -2.85c |
| t=240s | 234 | 0.1544 | 0.1410 | -1.34c |
| t=360s | 334 | 0.1520 | 0.1138 | -3.82c |
| t=480s | 397 | 0.1464 | 0.1234 | -2.30c |
| t=600s | 343 | 0.1462 | 0.1195 | -2.67c |
| t=720s | 252 | 0.1468 | 0.1270 | -1.98c |

#### Fixed price band [0.20,0.35) -- gap by time-slice

| Time-slice | n | Mean pred | Realized | Gap |
|-----------|---|-----------|----------|-----|
| t=60s | 233 | 0.3073 | 0.2618 | -4.55c |
| t=120s | 445 | 0.2891 | 0.2292 | -5.99c |
| t=240s | 656 | 0.2784 | 0.2485 | -2.99c |
| t=360s | 598 | 0.2722 | 0.2609 | -1.14c |
| t=480s | 461 | 0.2714 | 0.2495 | -2.20c |
| t=600s | 366 | 0.2744 | 0.2268 | -4.77c |
| t=720s | 276 | 0.2669 | 0.2790 | +1.21c |

### Sanity check A -- decided-market contamination

Are low-`cheap_mid` observations disproportionately from windows already effectively decided? A decided window has large `sigma_proximity` (spot far from strike in vol units).

| cheap_mid band | n | sigma_prox median | sigma_prox p90 | frac(sigma>2) | time_left median | cheap_won |
|----------------|---|-------------------|----------------|---------------|------------------|-----------|
| [0.00,0.10) | 2,138 | 1.91 | 5.66 | 0.480 | 300s | 0.090 |
| [0.10,0.20) | 1,625 | 1.02 | 2.81 | 0.186 | 420s | 0.122 |
| [0.20,0.35) | 3,010 | 0.50 | 1.50 | 0.059 | 540s | 0.250 |
| [0.35,0.55) | 4,817 | 0.23 | 0.69 | 0.011 | 780s | 0.420 |

corr(cheap_mid, sigma_proximity) across the cross-section = **-0.154**. A strongly negative correlation would mean cheaper observations are systematically more decided (their low win rate then partly reflects already-lost windows, not a tradeable mispricing).

### Sanity check B -- cheap-side flip

`cheap_won` in `entry_candidates_15m.parquet` is computed against the side that is cheap *at that observation's tick* (confirmed in Task 5's `build_entry_candidates`: `cheap_won` = `outcome_up` if `cheap_side==YES` else `1-outcome_up`). A window contributing at multiple time-slices CAN appear with different `cheap_side` values (1,112 of 1,676 windows do). Each such row is an independent, correctly-labelled observation -- no contamination.

### VERDICT

**RE-RUN ON CORRECTED DATA (real Polymarket outcomes) -- supersedes the earlier corrupt-label results.** The strike bug (`phase0_audit.md` Task 8c; old labels wrong on ~31% of windows) is fixed. The earlier corrupt-label calibration that reported a large positive cheap-side gap is invalid; the numbers below are the corrected-data re-derivation.

De-biasing to one observation per (window, time-slice), on corrected outcomes:

- **De-biased pooled cheap_mid gap = -0.91c** (mean over thick bins); cheap_ask (taker entry) = -1.61c. The cheap side is essentially calibrated -- there is no positive calibration edge on real labels.
- Bin-for-bin over the 8 matched thick bins (identical 15-bin grid): tick-pooled mean gap = -0.22c, de-biased mean gap = -0.91c (artifact +0.56c). On corrected data both the tick-pooled and de-biased gaps are near zero -- there is no longer a large apparent edge for the de-biasing to shrink.
- Low-price bins (cheap_mid<0.30) with the de-biased realized-rate CI entirely ABOVE the diagonal (i.e. a positive, CI-separated mispricing): **1**.
- Pooled low-bin gap by time-slice: early (t<=240s) = -7.89c; late (t>=480s) = +0.03c.

**Fixed-price-band x time-slice (price-mix control).** Holding the price band fixed isolates any genuine time effect from the price-mix (`trend` = linear gap change across the ~11-min span the slices cover; `rho` = correlation of gap with t):

- Within fixed band [0.10,0.20): per-slice gap -3.8c .. -1.3c (spread 2.5c), trend +0.3c (rho=+0.12) over 6 slices.
- Within fixed band [0.20,0.35): per-slice gap -6.0c .. +1.2c (spread 7.2c), trend +4.3c (rho=+0.65) over 7 slices.

On corrected data the within-band gaps are small and hover near or below zero at every slice -- there is no fixed-price under-pricing to disentangle from the price-mix.

**Decided-market contamination.** corr(cheap_mid, sigma_proximity) = -0.154 -- weak-to-moderate. Cheaper observations ARE somewhat more decided. Since the de-biased gap is ~0 / slightly negative, there is no positive edge here for contamination to either explain away or threaten.

**TAG: NO TRADEABLE EDGE -- on corrected (real Polymarket) outcomes the cheap side is essentially calibrated. The de-biased pooled gap is -0.91c (cheap_ask -1.61c). Only the single extreme-cheap-tail bin (cheap_mid<0.07, a side priced ~3c) has a CI-separated positive de-biased gap (~+7c) -- a residual longshot effect, far too small a slice of the curve and below taker cost; every other bin is flat or slightly negative. The large positive cheap-side calibration gap seen on the earlier corrupt labels was an artifact of the strike bug.**

**Bottom line.** On corrected outcomes the cheap side is calibrated: the de-biased pooled gap is **-0.91c** (cheap_ask -1.61c) and the per-slice gaps are all slightly negative. The only bin with a CI-separated positive gap is the extreme cheap tail (cheap_mid<0.07, ~+7c) -- a residual longshot effect on a tiny, sub-taker-cost slice of the curve, not a tradeable calibration edge. The tick-pooled method gives a near-identical ~0c pooled gap, so the tick-weighting bias is moot here -- there is no apparent edge for it to inflate. The earlier corrupt-label finding of a large (~+12c) cheap-side calibration edge was driven entirely by wrong outcome labels and is withdrawn. There is no tradeable calibration edge.

**Chart:** `docs/research/charts/calibration_debiased.png`

---

## Conditioned edge map


**Task 7.** Where is the cheap-side mispricing concentrated? An edge -- if any -- lives in specific conditions, not everywhere.

**Methodology override.** The plan's Task 7 said to compute the edge per *tick* and average within strata. That carries the tick-weighting / lingering bias Phase 0 documented (~87% of ticks are stale; a lingering price is over-sampled) and Task 6b corrected. This edge map is therefore built on the **de-biased cross-section**: one observation per window per time-slice (t in [60, 120, 240, 360, 480, 600, 720]s, the single tick within +/-5s of each t), dev rows only -- reusing `build_cross_section()` from `calibration_debiased.py`. Cross-section: **11,700 observations**, **1,676 windows**. Edge = `cheap_won - cheap_mid` (realized minus implied; positive = underpriced = buyer edge). All CIs are 90% window-clustered bootstrap (groups=slug, n=2000).

### Recorded realized_vol tertile cutoffs

Computed from the dev cross-section -- these REPLACE the uncalibrated hardcoded `vol_regime_thresholds` guesses (phase0_verdict.md, code-audit #9):

| Tertile | realized_vol range |
|---------|--------------------|
| LOW  | `< 0.003801` |
| MED  | `0.003801 .. 0.007202` |
| HIGH | `>= 0.007202` |

### One-dimensional conditioned edge maps

#### Edge by sigma_proximity

| Bucket | n_windows | n_obs | Mean edge | CI lo | CI hi | CI excl. 0 | Note |
|--------|-----------|-------|-----------|-------|-------|------------|------|
| <0.5 | 1,675 | 5,847 | -1.16c | -2.43c | +0.18c | no |  |
| 0.5-1 | 1,306 | 2,275 | -3.63c | -5.32c | -1.99c | yes |  |
| 1-2 | 1,158 | 1,906 | -1.61c | -3.19c | +0.05c | no |  |
| 2-4 | 742 | 1,042 | +5.74c | +3.67c | +7.92c | yes |  |
| >4 | 417 | 520 | +8.53c | +5.95c | +11.13c | yes |  |

#### Edge by time_left_sec

| Bucket | n_windows | n_obs | Mean edge | CI lo | CI hi | CI excl. 0 | Note |
|--------|-----------|-------|-----------|-------|-------|------------|------|
| <180 | 0 | 0 | n/a | n/a | n/a | no | thin |
| 180-420 | 1,672 | 3,344 | +1.65c | +0.38c | +3.00c | yes |  |
| 420-660 | 1,672 | 3,340 | -2.78c | -4.22c | -1.37c | yes |  |
| >660 | 1,672 | 5,016 | -0.79c | -2.25c | +0.66c | no |  |

#### Edge by cheap_drop_30s (%)

| Bucket | n_windows | n_obs | Mean edge | CI lo | CI hi | CI excl. 0 | Note |
|--------|-----------|-------|-----------|-------|-------|------------|------|
| 0 | 1,553 | 3,990 | -0.71c | -2.10c | +0.60c | no |  |
| 0-10 | 1,290 | 2,367 | +0.08c | -1.72c | +1.90c | no |  |
| 10-25 | 1,427 | 2,832 | -2.08c | -3.52c | -0.60c | yes |  |
| >25 | 1,360 | 2,511 | +0.32c | -1.12c | +1.76c | no |  |

#### Edge by cheap_mid

| Bucket | n_windows | n_obs | Mean edge | CI lo | CI hi | CI excl. 0 | Note |
|--------|-----------|-------|-----------|-------|-------|------------|------|
| 0.05-0.15 | 1,046 | 1,735 | -2.03c | -3.41c | -0.64c | yes |  |
| 0.15-0.25 | 1,116 | 1,727 | -2.52c | -4.48c | -0.52c | yes |  |
| 0.25-0.40 | 1,506 | 3,475 | -2.86c | -4.68c | -1.06c | yes |  |

#### Edge by symbol

| Bucket | n_windows | n_obs | Mean edge | CI lo | CI hi | CI excl. 0 | Note |
|--------|-----------|-------|-----------|-------|-------|------------|------|
| btc | 419 | 2,925 | -1.21c | -3.24c | +0.86c | no |  |
| eth | 419 | 2,925 | +0.80c | -1.23c | +2.87c | no |  |
| sol | 419 | 2,925 | -1.23c | -3.06c | +0.77c | no |  |
| xrp | 419 | 2,925 | -0.99c | -2.86c | +1.01c | no |  |

#### Edge by realized_vol tertile

| Bucket | n_windows | n_obs | Mean edge | CI lo | CI hi | CI excl. 0 | Note |
|--------|-----------|-------|-----------|-------|-------|------------|------|
| LOW | 1,321 | 3,900 | -0.50c | -2.02c | +1.06c | no |  |
| MED | 1,518 | 3,900 | +0.42c | -0.95c | +1.85c | no |  |
| HIGH | 1,313 | 3,900 | -1.90c | -3.14c | -0.55c | yes |  |

### Cross-tabulation of the strongest conditioners

The core thesis test: is the edge concentrated where sigma_proximity is LOW (spot still near strike -- a genuine panic overshoot, H8/H2) or spread everywhere (suspicious)?

#### sigma_proximity x cheap_drop_30s -- mean edge (cents)

| sigma_bucket \ drop_bucket | 0 | 0-10 | 10-25 | >25 |
|---|---|---|---|---|
| **<0.5** | -1.47c (n=1067) | +0.44c (n=1010) | -1.40c (n=1085) | -2.76c* (n=749) |
| **0.5-1** | -4.97c* (n=618) | -3.82c* (n=364) | -4.53c* (n=468) | -0.60c (n=478) |
| **1-2** | -2.92c* (n=573) | -1.90c (n=241) | -2.69c (n=339) | +0.90c (n=484) |
| **2-4** | +4.93c* (n=384) | +10.00c* (n=103) | +2.16c (n=158) | +7.46c* (n=275) |
| **>4** | +13.10c* (n=237) | +10.30c* (n=44) | -7.24c* (n=60) | +6.58c* (n=140) |

`*` = 90% window-clustered CI excludes zero and n_windows >= 30.

#### sigma_proximity x cheap_mid -- mean edge (cents)

| sigma_bucket \ cheap_mid_bucket | 0.05-0.15 | 0.15-0.25 | 0.25-0.40 |
|---|---|---|---|
| **<0.5** | -6.49c* (n=142) | -2.16c (n=400) | -2.20c* (n=1279) |
| **0.5-1** | -4.28c* (n=389) | -3.78c* (n=551) | -4.46c* (n=616) |
| **1-2** | -1.50c (n=547) | -2.08c (n=356) | -6.04c* (n=287) |
| **2-4** | +3.56c* (n=299) | -0.78c (n=132) | +0.44c (n=90) |
| **>4** | -6.48c* (n=115) | +1.55c (n=62) | +5.32c (n=27) |

`*` = 90% window-clustered CI excludes zero and n_windows >= 30.

### Dev-internal cross-validation (both-halves check)

Dev days split into an **early half (May 15-17)** and a **later half (May 18-20)**. A cell QUALIFIES only if, on BOTH halves, its 90% edge CI excludes zero in the SAME direction with n_windows >= 30.

#### Qualifying cells

| Conditioner = cell | Early edge | Early CI | Early n_win | Later edge | Later CI | Later n_win |
|--------------------|------------|----------|-------------|------------|----------|-------------|
| `sigma_bucket = 0.5-1` | -3.53c | [-6.34c, -0.75c] | 417 | -3.67c | [-5.79c, -1.52c] | 889 |
| `sigma_bucket = 2-4` | +7.14c | [+3.04c, +11.24c] | 202 | +5.28c | [+2.83c, +7.80c] | 540 |
| `sigma x drop = sig=2-4|drop=0` | +6.99c | [+0.97c, +13.44c] | 88 | +4.35c | [+1.07c, +7.58c] | 296 |

#### All cells -- both-halves CV detail

| Conditioner = cell | Early edge (CI, n_win) | Later edge (CI, n_win) | n>=30 both | CI excl 0 both | same dir | QUALIFIES |
|--------------------|------------------------|------------------------|------------|----------------|----------|-----------|
| `sigma_bucket = <0.5` | +0.47c ([-1.94c,+2.66c], n=523) | -2.01c ([-3.54c,-0.49c], n=1152) | yes | no | no | no |
| `sigma_bucket = 0.5-1` | -3.53c ([-6.34c,-0.75c], n=417) | -3.67c ([-5.79c,-1.52c], n=889) | yes | yes | yes | **YES** |
| `sigma_bucket = 1-2` | +1.21c ([-1.78c,+4.29c], n=329) | -2.68c ([-4.53c,-0.71c], n=829) | yes | no | no | no |
| `sigma_bucket = 2-4` | +7.14c ([+3.04c,+11.24c], n=202) | +5.28c ([+2.83c,+7.80c], n=540) | yes | yes | yes | **YES** |
| `sigma_bucket = >4` | +5.29c ([-0.85c,+11.64c], n=91) | +9.36c ([+6.37c,+12.45c], n=326) | yes | no | no | no |
| `time_left_bucket = <180` | n/a ([n/a,n/a], n=0) | n/a ([n/a,n/a], n=0) | no | no | no | no |
| `time_left_bucket = 180-420` | -0.87c ([-3.01c,+1.20c], n=520) | +2.79c ([+1.26c,+4.33c], n=1152) | yes | no | no | no |
| `time_left_bucket = 420-660` | -1.00c ([-3.57c,+1.69c], n=520) | -3.58c ([-5.14c,-1.93c], n=1152) | yes | no | no | no |
| `time_left_bucket = >660` | +2.13c ([-0.45c,+4.74c], n=520) | -2.11c ([-3.84c,-0.45c], n=1152) | yes | no | no | no |
| `drop_bucket = 0` | +1.25c ([-1.57c,+4.08c], n=449) | -1.32c ([-2.88c,+0.27c], n=1104) | yes | no | no | no |
| `drop_bucket = 0-10` | +1.81c ([-1.26c,+4.97c], n=442) | -0.98c ([-3.30c,+1.21c], n=848) | yes | no | no | no |
| `drop_bucket = 10-25` | -0.44c ([-3.06c,+2.15c], n=465) | -2.98c ([-4.87c,-1.21c], n=962) | yes | no | no | no |
| `drop_bucket = >25` | -1.26c ([-3.49c,+1.09c], n=419) | +1.03c ([-0.65c,+2.73c], n=941) | yes | no | no | no |
| `cheap_mid_bucket = 0.05-0.15` | -1.74c ([-4.27c,+0.84c], n=313) | -2.16c ([-3.92c,-0.36c], n=733) | yes | no | no | no |
| `cheap_mid_bucket = 0.15-0.25` | -0.82c ([-4.56c,+2.97c], n=340) | -3.28c ([-5.56c,-0.99c], n=776) | yes | no | no | no |
| `cheap_mid_bucket = 0.25-0.40` | -0.60c ([-3.64c,+2.59c], n=473) | -3.97c ([-6.16c,-1.80c], n=1033) | yes | no | no | no |
| `symbol = btc` | -1.99c ([-5.47c,+1.49c], n=131) | -0.86c ([-3.36c,+1.53c], n=288) | yes | no | no | no |
| `symbol = eth` | +1.48c ([-2.29c,+5.18c], n=131) | +0.49c ([-1.88c,+2.97c], n=288) | yes | no | no | no |
| `symbol = sol` | +1.12c ([-2.47c,+4.93c], n=131) | -2.29c ([-4.47c,+0.02c], n=288) | yes | no | no | no |
| `symbol = xrp` | +0.92c ([-3.10c,+4.83c], n=131) | -1.86c ([-4.09c,+0.55c], n=288) | yes | no | no | no |
| `vol_tertile = LOW` | -1.17c ([-3.40c,+1.31c], n=424) | -0.10c ([-2.10c,+1.81c], n=897) | yes | no | no | no |
| `vol_tertile = MED` | +1.52c ([-0.99c,+4.22c], n=470) | -0.07c ([-1.70c,+1.61c], n=1048) | yes | no | no | no |
| `vol_tertile = HIGH` | +1.31c ([-1.52c,+4.18c], n=359) | -2.97c ([-4.49c,-1.53c], n=954) | yes | no | no | no |
| `sigma x drop = sig=<0.5|drop=0` | -0.24c ([-4.47c,+3.73c], n=309) | -1.92c ([-4.49c,+0.70c], n=758) | yes | no | no | no |
| `sigma x drop = sig=<0.5|drop=0-10` | +2.09c ([-1.59c,+5.99c], n=366) | -0.65c ([-3.35c,+2.04c], n=644) | yes | no | no | no |
| `sigma x drop = sig=<0.5|drop=10-25` | +0.46c ([-2.91c,+3.76c], n=382) | -2.51c ([-5.00c,-0.14c], n=703) | yes | no | no | no |
| `sigma x drop = sig=<0.5|drop=>25` | -1.61c ([-5.77c,+2.31c], n=238) | -3.33c ([-6.14c,-0.48c], n=511) | yes | no | no | no |
| `sigma x drop = sig=0.5-1|drop=0` | -1.58c ([-6.54c,+3.73c], n=163) | -6.13c ([-8.97c,-3.17c], n=455) | yes | no | no | no |
| `sigma x drop = sig=0.5-1|drop=0-10` | -2.75c ([-8.39c,+2.97c], n=131) | -4.42c ([-9.00c,+0.24c], n=233) | yes | no | no | no |
| `sigma x drop = sig=0.5-1|drop=10-25` | -3.30c ([-8.38c,+1.53c], n=160) | -5.17c ([-9.10c,-1.54c], n=308) | yes | no | no | no |
| `sigma x drop = sig=0.5-1|drop=>25` | -6.62c ([-10.03c,-3.08c], n=157) | +2.38c ([-0.99c,+5.84c], n=321) | yes | no | no | no |
| `sigma x drop = sig=1-2|drop=0` | +3.65c ([-1.19c,+9.34c], n=121) | -4.46c ([-7.05c,-1.74c], n=452) | yes | no | no | no |
| `sigma x drop = sig=1-2|drop=0-10` | +0.49c ([-7.32c,+8.87c], n=86) | -3.21c ([-8.43c,+2.60c], n=155) | yes | no | no | no |
| `sigma x drop = sig=1-2|drop=10-25` | -2.10c ([-7.55c,+3.75c], n=101) | -2.96c ([-6.64c,+0.94c], n=238) | yes | no | no | no |
| `sigma x drop = sig=1-2|drop=>25` | +1.84c ([-2.18c,+6.45c], n=150) | +0.45c ([-2.02c,+3.15c], n=334) | yes | no | no | no |
| `sigma x drop = sig=2-4|drop=0` | +6.99c ([+0.97c,+13.44c], n=88) | +4.35c ([+1.07c,+7.58c], n=296) | yes | yes | yes | **YES** |
| `sigma x drop = sig=2-4|drop=0-10` | +19.58c ([+5.99c,+33.21c], n=31) | +5.86c ([-1.45c,+13.65c], n=72) | yes | no | no | no |
| `sigma x drop = sig=2-4|drop=10-25` | +5.79c ([-1.80c,+14.30c], n=50) | +0.55c ([-4.65c,+6.44c], n=108) | yes | no | no | no |
| `sigma x drop = sig=2-4|drop=>25` | +2.77c ([-3.22c,+8.86c], n=68) | +8.91c ([+5.29c,+12.79c], n=207) | yes | no | no | no |
| `sigma x drop = sig=>4|drop=0` | +9.86c ([-0.44c,+20.51c], n=39) | +13.71c ([+9.37c,+18.35c], n=198) | yes | no | no | no |
| `sigma x drop = sig=>4|drop=10-25` | -10.74c ([-14.85c,-7.19c], n=16) | -5.92c ([-10.19c,-1.05c], n=44) | no | yes | yes | no |
| `sigma x drop = sig=>4|drop=>25` | +4.70c ([-4.39c,+15.27c], n=31) | +7.13c ([+2.37c,+12.04c], n=109) | yes | no | no | no |

### VERDICT

**RE-RUN ON CORRECTED DATA (real Polymarket outcomes) -- supersedes the earlier corrupt-label results.** The strike bug (`phase0_audit.md` Task 8c) is fixed; this edge map is re-derived on the true Polymarket-resolved outcomes.

Overall de-biased cheap-side edge across the whole cross-section = **-0.66c** (consistent with Task 6b's re-run pooled gap of ~-1c; the cheap side is essentially calibrated overall). Task 7 asks the sharper question: is there ANY conditioned corner with a CI-separated edge that survives both-halves dev-internal CV.

**Sigma-proximity gradient (the core thesis test).**
- One-dimensional edge by sigma_proximity bucket: <0.5=-1.16c (n_win=1675); 0.5-1=-3.63c (n_win=1306); 1-2=-1.61c (n_win=1158); 2-4=+5.74c (n_win=742); >4=+8.53c (n_win=417).
- The edge is LARGER at HIGH sigma-proximity (+8.53c at >4 vs -1.16c at <0.5) -- the opposite of the panic-overshoot thesis. This points to a favorite-longshot / decided-market effect, NOT a coin-flip overshoot. H8's thesis is REJECTED by the one-dimensional gradient.

**Drop gradient.** Edge by cheap_drop_30s bucket: 0=-0.71c (n_win=1553); 0-10=+0.08c (n_win=1290); 10-25=-2.08c (n_win=1427); >25=+0.32c (n_win=1360).
- The drop bucket does NOT clearly order the edge (+0.32c for >25% vs -0.71c for no drop). A visible odds drop is not, on its own, where the edge lives -- weak support for H2 as a stand-alone signal.

**sigma_proximity x cheap_drop_30s cross-tab.**
- Mean edge across the populated drop cells: sigma<0.5 row = -1.30c (4 cells), sigma>4 row = +5.69c (4 cells).
- Within the sigma<0.5 row the edge spans -2.76c..+0.44c across drop buckets (range +3.20c) -- the drop conditioner adds little once sigma is low: the low-sigma state itself carries the edge.

**Dev-internal cross-validation (both-halves check).**
- 3 cell(s) QUALIFY: edge CI excludes zero, same direction, n_windows >= 30 on BOTH the early (May 15-17) and later (May 18-20) dev halves. See the qualifying-cells table above. Of these, 2 carry a POSITIVE edge.
- The positive qualifying cell(s) sit at HIGH sigma-proximity (sigma>2), NOT in the low-sigma near-coin-flip corner. A positive `cheap_won - cheap_mid` at high sigma-proximity means the cheap side (the side the spot has already moved AGAINST) wins more often than its price implies -- this is a favourite/longshot mispricing, the SAME effect Task 8's fair-value decomposition isolates as the cheap-side-actually-favoured bin. Task 8 shows it is NOT cost-surviving (negative net of taker, ~0 net of maker), and the divergence backtest -- which targets exactly that signal out-of-fold -- finds it does not generalize to a profitable rule. The 'edge' here is gross, conditioned, and not tradeable.

**Hypothesis verdicts (H2, H6, H8).**
- **H8 -- NOT clearly supported.** H8 expected the edge in the near-coin-flip / moderate-underdog zone. The sigma-proximity gradient here does not concentrate the edge at low sigma-proximity, so the 'edge lives near coin-flips' part of H8 is not confirmed by the conditioned map.
- **H2 -- partially testable here, not cleanly supported.** H2 says an odds drop alone does not predict reversion; the spot-move context does. Within the low-sigma row the edge still spans +3.20c across drop buckets, so a drop is NOT redundant once sigma is low -- but neither does it cleanly order the edge (the no-drop and the >25% cells are both high, the middle drop buckets lower). A proper H2 test needs the spot-move split of the drop event study (Task 13-14); the conditioned edge map alone cannot separate noise-drops from signal-drops.
- **H6 -- WEAK.** Per-symbol edge: btc=-1.21c, eth=+0.80c, sol=-1.23c, xrp=-0.99c. Spread +2.03c (eth highest, sol lowest). The per-symbol spread is modest; coins differ but not dramatically in the conditioned cross-section.

**Bottom line.** On corrected data the overall de-biased cheap-side edge is ~-0.66c -- essentially zero. 3 conditioned cell(s) clear the both-halves dev-internal CV, but the positive ones sit at HIGH sigma-proximity (sigma>2): they are the favourite/longshot mispricing -- the cheap side being the spot-favoured side -- NOT a low-sigma panic-overshoot edge. These are GROSS, conditioned numbers; Task 8's fair-value decomposition shows the same effect does not survive the ~16-21% taker round-trip cost (and is ~0 net of maker), and the divergence backtest confirms out-of-fold that it does not yield a profitable rule. There is no conditioned cheap-side edge here that is tradeable on correct labels.

**Charts:** `docs/research/charts/edge_map_one_dim.png`, `edge_map_sigma_drop.png`, `edge_map_sigma_mid.png`

---

## Task 8 -- Fair-value triangulation & the decisive diagnostic

**RE-RUN ON CORRECTED DATA (real Polymarket outcomes) -- supersedes the earlier corrupt-label results.** The strike bug (`docs/research/phase0_audit.md` Task 8c; old labels wrong on ~31% of windows) is fixed; the cross-section now carries the true Polymarket-resolved outcomes and corrected strikes / `move_pct`. The earlier corrupt-label Task 8 (which reported a ~+12c cheap-side headline) is invalid.

**The question.** Tasks 6b/7 re-run on corrected data find the cheap (dip-buyer's) side is no longer materially under-priced overall -- the de-biased pooled gap is ~-1c (it was a spurious ~+12c on the corrupt labels). This section triangulates three independent estimates of what the cheap side is worth -- the **market** mid, the **theoretical** Bachelier value, and a **model-free empirical** frequency surface -- to settle whether any tradeable edge survives. Dev split May 15-20 only; sealed hold-out (May 21-22) NOT touched. Cross-section: the de-biased one-obs-per-(window,time-slice) cross-section, **11,700 observations**, **1,676 windows**.

### Job 1 -- Is sigma-proximity a usable measure of decided-ness?

sigma-proximity claims a market is `k` sigmas decided => the favourite's Bachelier win probability is Phi(k). Below, observations are binned by `sigma_proximity`; for each bin the Bachelier-implied favourite win rate (favourite = the side with mid > 0.5) is compared to the **actual** favourite win rate.

| sigma_proximity bin | n_obs | n_windows | Bachelier favourite prob | ACTUAL favourite win rate | 90% CI | mean cheap_mid |
|---|---|---|---|---|---|---|
| (0.0, 0.5] | 5,680 | 1,675 | 0.5917 | **0.633** | [0.620, 0.646] | 0.379 |
| (0.5, 1.0] | 2,275 | 1,306 | 0.7630 | **0.787** | [0.770, 0.804] | 0.249 |
| (1.0, 2.0] | 1,906 | 1,158 | 0.9122 | **0.854** | [0.837, 0.869] | 0.162 |
| (2.0, 3.0] | 728 | 581 | 0.9904 | **0.850** | [0.826, 0.873] | 0.101 |
| (3.0, 4.0] | 314 | 279 | 0.9996 | **0.828** | [0.788, 0.865] | 0.093 |
| (4.0, 1000000000.0] | 520 | 417 | 1.0000 | **0.833** | [0.805, 0.859] | 0.082 |

**sigma-proximity is broken.** In the most-decided bin (`sigma_proximity > 4`) Bachelier says the favourite wins with probability **1.0000** -- a certainty. The favourite actually wins only **0.833** (90% CI [0.805, 0.859]). A market that sigma-proximity rates as 4+ sigmas decided is, in reality, only an ~83/17 shot. The actual favourite win rate rises only gently and monotonically with sigma_proximity (0.633 at sigma<0.5 -> 0.833 at sigma>4) -- it carries a *little* ordinal information but is badly mis-scaled as a probability.

**Why -- the realized_vol input is too low, and the model is wrong in kind.** Comparing the trailing-60-tick `realized_vol` to each window's whole-window realized vol (std of all per-tick `move_pct` increments, n=1,676 windows): the trailing estimate is a median **0.78x** the whole-window vol (mean 0.77x, IQR 0.69-0.86) -- it under-states true volatility by ~22%. That alone inflates the Bachelier z by ~1.28x. But a ~1.3x vol correction is nowhere near enough to drag a ~100.00% implied certainty down to a realized ~83%: that gap is far too large. The deeper failure is structural -- a driftless-Gaussian model run on a stale-ish 1 Hz feed treats every basis point of `move_pct` as locked in, but crypto over a 15-minute window mean-reverts and jumps; the spot crosses back through the strike far more often than a Gaussian random walk would. **Verdict: sigma-proximity is NOT a usable measure of decided-ness. It is off by orders of magnitude as a probability; it is salvageable only as a weak ordinal rank. No analysis should condition on it as if `sigma>3` meant `decided`.**

### Job 2 -- A model-free empirical fair value

Because Job 1 shows Bachelier/`realized_vol` cannot be trusted, the fair value is rebuilt model-free: observations are binned by (signed `move_pct`, `time_left_sec`) into a 2-D grid, and a cell's empirical P(Up) is the realized `outcome_up` frequency in that cell. Only the trustworthy raw inputs (spot distance from strike, time remaining) and the true outcome enter -- no volatility model.

| move_pct \ time_left | 120-240s | 240-360s | 360-480s | 480-600s | 600-720s | 720-900s |
|---|---|---|---|---|---|---|
| [-100.00,-1.00) | 0.00 (n4) | n/a | n/a | n/a | n/a | n/a |
| [-1.00,-0.60) | 0.00 (n13) | 0.00 (n4) | 0.00 (n4) | 0.00 (n2) | n/a | n/a |
| [-0.60,-0.40) | 0.00 (n30) | 0.00 (n21) | 0.00 (n16) | 0.00 (n16) | 0.00 (n9) | 0.50 (n2) |
| [-0.40,-0.25) | 0.01 (n73) | 0.01 (n84) | 0.04 (n77) | 0.04 (n46) | 0.03 (n37) | 0.11 (n28) |
| [-0.25,-0.15) | 0.03 (n171) | 0.08 (n150) | 0.13 (n139) | 0.15 (n136) | 0.14 (n96) | 0.18 (n67) |
| [-0.15,-0.08) | 0.09 (n204) | 0.11 (n195) | 0.15 (n191) | 0.23 (n212) | 0.26 (n191) | 0.26 (n247) |
| [-0.08,-0.03) | 0.25 (n195) | 0.25 (n201) | 0.29 (n230) | 0.27 (n227) | 0.35 (n284) | 0.44 (n606) |
| [-0.03,0.00) | 0.39 (n156) | 0.39 (n185) | 0.40 (n168) | 0.48 (n182) | 0.46 (n245) | 0.48 (n829) |
| [0.00,0.03) | 0.59 (n152) | 0.57 (n151) | 0.59 (n177) | 0.53 (n195) | 0.57 (n230) | 0.52 (n677) |
| [0.03,0.08) | 0.81 (n215) | 0.75 (n238) | 0.67 (n272) | 0.69 (n291) | 0.65 (n255) | 0.61 (n629) |
| [0.08,0.15) | 0.91 (n190) | 0.89 (n202) | 0.86 (n212) | 0.76 (n201) | 0.75 (n213) | 0.68 (n182) |
| [0.15,0.25) | 0.96 (n139) | 0.95 (n141) | 0.94 (n116) | 0.95 (n115) | 0.89 (n74) | 0.78 (n59) |
| [0.25,0.40) | 1.00 (n85) | 0.97 (n72) | 1.00 (n46) | 0.96 (n27) | 0.95 (n20) | 0.91 (n11) |
| [0.40,0.60) | 1.00 (n32) | 1.00 (n18) | 1.00 (n13) | 1.00 (n9) | 0.92 (n13) | 1.00 (n4) |
| [0.60,1.00) | 1.00 (n8) | 1.00 (n9) | 1.00 (n11) | 1.00 (n6) | 1.00 (n2) | 1.00 (n3) |
| [1.00,100.00) | 1.00 (n5) | 1.00 (n1) | n/a | 1.00 (n3) | 1.00 (n3) | n/a |

**The surface is sane and monotone-ish.** P(Up) rises monotonically with `move_pct` in every time column (deeply negative move => P(Up) ~0; deeply positive => ~1) and the transition sharpens as `time_left` shrinks (the `[-0.10,0.00)` row falls from ~0.34 with 12-15 min left to ~0.15 with <3 min left -- less time, more extreme). Crucially it is *soft*: a spot only -0.1% to -0.25% from strike -- which sigma-proximity often rates as multi-sigma decided -- still has an empirical P(Up) of 7-11%, not 0%. The genuinely-decided region is much narrower than sigma-proximity claims. This surface is the trustworthy fair-value reference for Job 3.

### Job 3 -- The decisive real-vs-artifact decomposition

For each observation, the cheap side's **empirical fair value** = (empirical P(Up) of its cell) if `cheap_side` is YES, else `1 - that`. The cheap-side headline edge (`cheap_won - cheap_mid`) is then decomposed by the **empirical decided-ness** of the observation -- binning by that empirical fair value. To remove any selection-into-its-own-cell inflation, the empirical surface is also built **out-of-fold** (leave-one-UTC-day-out: a row's own outcome never trains its own cell). The out-of-fold table below is the decisive one; the in-sample table matched it to within ~0.2c per bin.

#### Out-of-fold decomposition (decisive)

| Empirical-decided-ness bin | n_obs | n_windows | weight | mean cheap_mid | mean empirical fair | mean cheap_won | naive edge (cheap_won-cheap_mid) | naive 90% CI | edge vs empirical fair | contribution to headline |
|---|---|---|---|---|---|---|---|---|---|---|
| near-decided-against (0.00-0.10) | 1,465 | 871 | 12.5% | 0.0844 | 0.0545 | 0.0560 | **-2.83c** | [-4.01, -1.69]c | -2.98c | -0.354c |
| underdog (0.10-0.25) | 2,404 | 1,260 | 20.5% | 0.1904 | 0.1686 | 0.1747 | **-1.61c** | [-3.40, +0.21]c | -2.18c | -0.332c |
| long-shot-contested (0.25-0.40) | 3,248 | 1,442 | 27.8% | 0.3099 | 0.3211 | 0.2891 | **-2.10c** | [-3.92, -0.39]c | +1.11c | -0.584c |
| genuinely-contested (0.40-0.55) | 3,333 | 1,424 | 28.5% | 0.4079 | 0.4715 | 0.3978 | **-0.98c** | [-2.61, +0.71]c | +6.37c | -0.278c |
| cheap-side-actually-favoured (0.55-1.00) | 862 | 596 | 7.4% | 0.3579 | 0.6842 | 0.4849 | **+12.82c** | [+8.75, +16.61]c | +32.66c | +0.945c |

Headline edge (whole cross-section) = **+-0.66c** -- the sum of the contribution column.

#### In-sample decomposition (cross-check -- matches out-of-fold)

| Empirical-decided-ness bin | n_obs | n_windows | weight | mean cheap_mid | mean empirical fair | mean cheap_won | naive edge (cheap_won-cheap_mid) | naive 90% CI | edge vs empirical fair | contribution to headline |
|---|---|---|---|---|---|---|---|---|---|---|
| near-decided-against (0.00-0.10) | 1,608 | 939 | 13.7% | 0.0809 | 0.0555 | 0.0522 | **-2.88c** | [-4.00, -1.73]c | -2.53c | -0.395c |
| underdog (0.10-0.25) | 2,360 | 1,276 | 20.2% | 0.1875 | 0.1745 | 0.1631 | **-2.39c** | [-4.13, -0.61]c | -1.30c | -0.481c |
| long-shot-contested (0.25-0.40) | 3,239 | 1,461 | 27.7% | 0.3169 | 0.3201 | 0.2964 | **-2.03c** | [-3.75, -0.21]c | +0.33c | -0.563c |
| genuinely-contested (0.40-0.55) | 3,288 | 1,386 | 28.1% | 0.4096 | 0.4696 | 0.4057 | **-0.39c** | [-2.04, +1.21]c | +6.00c | -0.109c |
| cheap-side-actually-favoured (0.55-1.00) | 885 | 606 | 7.6% | 0.3590 | 0.6814 | 0.4904 | **+13.25c** | [+9.30, +17.20]c | +32.22c | +1.002c |

#### Surface soundness -- cheap_won tracks the empirical fair value

By construction the empirical fair value IS the realized frequency, so `cheap_won` must track it within bins -- it does (corr = 0.319; mean cheap_won 0.2732 vs mean empirical fair 0.3153). This confirms the surface is internally consistent.

| empirical-fair-value bin | n_obs | mean empirical fair | mean cheap_won |
|---|---|---|---|
| (0.0, 0.1] | 1,465 | 0.0545 | 0.0560 |
| (0.1, 0.2] | 1,619 | 0.1387 | 0.1347 |
| (0.2, 0.3] | 2,107 | 0.2555 | 0.2326 |
| (0.3, 0.4] | 1,926 | 0.3558 | 0.3380 |
| (0.4, 0.5] | 2,508 | 0.4544 | 0.3935 |
| (0.5, 0.6] | 1,106 | 0.5361 | 0.4105 |
| (0.6, 0.7] | 308 | 0.6280 | 0.4091 |
| (0.7, 0.8] | 118 | 0.7420 | 0.4153 |
| (0.8, 0.9] | 22 | 0.8571 | 0.2273 |
| (0.9, 1.0] | 133 | 0.9708 | 0.9248 |

#### Per-symbol robustness of the decomposition

| symbol | headline edge | near-decided-against bin (edge / weight) | genuinely-contested bin (edge / weight) | cheap-side-actually-favoured bin (edge / weight) |
|---|---|---|---|---|
| btc | -1.2c | -1.2c / 28% | -2.0c / 65% | +6.7c / 7% |
| eth | +0.8c | -2.1c / 35% | +0.1c / 58% | +21.4c / 7% |
| sol | -1.2c | -2.9c / 42% | -1.7c / 50% | +11.3c / 8% |
| xrp | -1.0c | -1.5c / 40% | -2.6c / 52% | +11.8c / 8% |

The decomposition structure -- the near-decided/underdog and contested bins roughly break even or lose, the only positive bin being the small cheap-side-actually-favoured tail -- is consistent across the four coins; this is not a one-symbol artifact, but it is also not a tradeable edge (see the verdict).

#### Net-of-cost: the genuinely-contested band

For the genuinely-contested band (empirical fair 0.25-0.55, n=6,581 obs, 1,634 windows): the naive edge (`cheap_won - cheap_mid`) is **-1.52c** (90% CI [-2.85, -0.24]c). **Maker** net PnL = **-1.56c per \$1 stake** (90% CI [-2.83, -0.25]c -- ~0 maker fee, so this equals the naive edge); **taker** net PnL = **-4.56c per \$1 stake** (90% CI [-5.85, -3.25]c, after paying `cheap_ask` and the `0.07*p*(1-p)` fee on both legs). On corrected data both the maker and taker numbers for the contested band are negative -- there is no contested-band edge to harvest.

### VERDICT

**1. sigma-proximity is broken.** Bachelier on `realized_vol` says a `sigma>4` market's favourite wins ~100.00% of the time; it actually wins ~83%. The fix is not a tweak: `realized_vol` under-states true vol by a factor ~1.28 (median ratio 0.78), and even fully correcting that leaves a Gaussian-random-walk model that ignores the mean-reversion and jumpiness of 15-minute crypto. sigma-proximity should be treated as -- at best -- a weak ordinal rank, never as a probability of decided-ness. The real separator of decided vs contested is the model-free empirical fair value built in Job 2.

**2. The headline is ~0c on corrected data -- it is a mix of opposing pieces that nearly cancel.** The whole-cross-section naive cheap-side edge is **-0.7c** (essentially zero / slightly negative). Decomposed by genuine (empirical) decided-ness, out-of-fold:

- **Genuinely near-decided-against (empirical fair < 0.25), ~33% of observations.** The cheap side is priced ~0.08-0.19 but its true win probability is only ~0.05-0.17. Naive edge **-2.8c** and **-1.6c** -- the cheap buyer LOSES heavily here. This is classic **longshot OVER-pricing**: the market over-pays for near-certain losers. Contribution to the headline: **-0.7c** (it drags the headline DOWN).
- **Genuinely contested (empirical fair 0.25-0.55), ~56% of observations.** Naive edge **-2.1c** and **-1.0c** -- no under-pricing of contested markets -- the cheap buyer loses slightly here too. Contribution: **-0.9c**.
- **Cheap-side-actually-favoured (empirical fair > 0.55), ~7% of observations.** The side priced ~0.36 (an apparent underdog) is in truth the ~0.68-probability FAVOURITE. Naive edge **+12.8c**. Contribution: **+0.9c** -- this single bin IS the headline.

So of the -0.7c headline: **~-0.7c is the longshot/underdog tail (cheap side priced ABOVE its true win rate -- the cheap buyer LOSES)**, **~-0.9c is the contested band**, and **~+0.9c comes from the small bin where the 'cheap' side is actually the favourite.** The pieces nearly cancel; the net is ~0c. Across the whole genuinely-contested band (empirical fair 0.25-0.55, n=6,581 obs) the naive edge is **-1.5c** (90% CI [-2.8, -0.2]c) -- negative: there is no contested-band under-pricing on corrected labels.

**3. Is anything big enough to trade? No.** The genuinely-contested band (empirical fair 0.25-0.55) has a naive edge of -1.5c -- a **maker** net of -1.6c per \$1 (CI [-2.8, -0.3]c) and a **taker** net of -4.6c (CI [-5.9, -3.2]c). Both are negative -- the contested band loses money on corrected data even as a maker. The only positive pocket is the small cheap-side-actually-favoured bin (empirical fair > 0.55, ~7% of observations, naive edge +13c). That is NOT a dip-buying edge: it is the bin where a side priced as an underdog is in truth the favourite, i.e. it requires the empirical fair-value model to identify it in advance. The divergence backtest (`divergence_backtest.py`) tests exactly that signal out-of-fold -- and finds it does NOT survive (see `divergence_edge.md`).

**TAG: NO TRADEABLE EDGE on corrected data.** On real Polymarket outcomes the cheap-side headline is ~0c, the genuinely-contested band is -1.5c naive (negative even as a maker), and sigma-proximity is a broken decided-ness measure. The one positive pocket -- the cheap-side-actually-favoured bin -- is small (~7% of observations) and only capturable with a fair-value model; the out-of-fold divergence backtest shows that model does not generalize to a profitable rule. The earlier corrupt-label Task 8 'partially real' verdict was an artifact of the strike bug and is withdrawn.

**Bottom line for Phase 5.** On corrected data there is no cost-surviving cheap-side edge: not as a flat rule, not in the contested band, not via the empirical fair-value surface. sigma-proximity must be dropped as a decided-ness filter regardless (it is genuinely broken). The model-free empirical fair-value surface is the right diagnostic, but on real outcomes it does not expose a tradeable mispricing -- the divergence backtest is the decisive out-of-fold confirmation of that. No strategy should be built on this; the sealed hold-out is not warranted for a signal that already fails out-of-fold on dev.

**Charts:**
- `docs/research/charts/sigma_proximity_calibration.png` -- Job 1: Bachelier-implied vs actual favourite win rate.
- `docs/research/charts/empirical_fair_value_surface.png` -- Job 2: the model-free P(Up) surface.
- `docs/research/charts/edge_vs_empirical_decidedness.png` -- Job 3: headline edge decomposed by empirical decided-ness.

---
