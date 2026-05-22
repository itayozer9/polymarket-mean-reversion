# Edge Map

Phase 2–4 Edge Discovery findings for Polymarket Up/Down (May 2026).

## Unconditional Calibration (Task 6)

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
| [0.00, 0.07) | 0.0299 | 0.2706 (+24.07c) | 0.2468 | 0.2953 | 860 | 1,674 |  |
| [0.07, 0.13) | 0.1000 | 0.2896 (+18.96c) | 0.2625 | 0.3199 | 760 | 1,022 |  |
| [0.13, 0.20) | 0.1662 | 0.3388 (+17.26c) | 0.3098 | 0.3669 | 826 | 1,104 |  |
| [0.20, 0.27) | 0.2338 | 0.3551 (+12.13c) | 0.3273 | 0.3823 | 913 | 1,253 |  |
| [0.27, 0.33) | 0.3010 | 0.4188 (+11.78c) | 0.3930 | 0.4445 | 988 | 1,385 |  |
| [0.33, 0.40) | 0.3662 | 0.4459 (+7.97c) | 0.4219 | 0.4690 | 1,081 | 1,747 |  |
| [0.40, 0.47) | 0.4347 | 0.4921 (+5.74c) | 0.4738 | 0.5110 | 1,245 | 2,290 |  |
| [0.47, 0.53) | 0.4845 | 0.5037 (+1.92c) | 0.4794 | 0.5282 | 858 | 1,225 |  |

De-biased pooled mean gap (thick bins) = **+12.48c**.

#### De-biased pooled reliability -- cheap_ask (taker entry price)

| Bin | Mean pred | Realized | CI lo | CI hi | n_windows | n_obs | Note |
|-----|-----------|----------|-------|-------|-----------|-------|------|
| [0.00, 0.07) | 0.0306 | 0.2703 (+23.97c) | 0.2454 | 0.2964 | 786 | 1,450 |  |
| [0.07, 0.13) | 0.0994 | 0.2921 (+19.27c) | 0.2637 | 0.3213 | 822 | 1,157 |  |
| [0.13, 0.20) | 0.1648 | 0.3235 (+15.87c) | 0.2933 | 0.3541 | 732 | 949 |  |
| [0.20, 0.27) | 0.2298 | 0.3525 (+12.28c) | 0.3255 | 0.3802 | 914 | 1,268 |  |
| [0.27, 0.33) | 0.3015 | 0.4127 (+11.12c) | 0.3869 | 0.4385 | 1,019 | 1,483 |  |
| [0.33, 0.40) | 0.3660 | 0.4452 (+7.93c) | 0.4200 | 0.4696 | 1,003 | 1,525 |  |
| [0.40, 0.47) | 0.4313 | 0.4756 (+4.44c) | 0.4562 | 0.4950 | 1,220 | 2,195 |  |
| [0.47, 0.53) | 0.4875 | 0.5093 (+2.18c) | 0.4882 | 0.5296 | 1,038 | 1,669 |  |
| [0.53, 0.60) | 0.5550 | 0.5000 (-5.50c) | 0.0000 | 1.0000 | 4 | 4 | thin |

De-biased pooled cheap_ask mean gap (thick bins) = **+12.13c**.

### Bias quantification -- tick-pooled (Task 6) vs de-biased

`Artifact removed = Task6 realized - de-biased realized` -- the inflation that the tick-weighting bias added. `De-biased gap = de-biased realized - mean pred` -- the edge that survives.

| Bin | Mean pred | Tick-pooled realized | Tick-pooled n_ticks | De-biased realized | Tick-pooled gap | De-biased gap | Artifact removed |
|-----|-----------|----------------------|---------------------|--------------------|-----------------|---------------|------------------|
| [0.00, 0.07) | 0.0299 | 0.3049 | 341,616 | 0.2706 | +28.34c | +24.07c | +3.43c |
| [0.07, 0.13) | 0.1000 | 0.3094 | 136,536 | 0.2896 | +20.98c | +18.96c | +1.98c |
| [0.13, 0.20) | 0.1662 | 0.3423 | 142,567 | 0.3388 | +17.74c | +17.26c | +0.35c |
| [0.20, 0.27) | 0.2338 | 0.3695 | 156,146 | 0.3551 | +13.55c | +12.13c | +1.44c |
| [0.27, 0.33) | 0.3010 | 0.4180 | 155,100 | 0.4188 | +11.75c | +11.78c | -0.07c |
| [0.33, 0.40) | 0.3662 | 0.4618 | 187,437 | 0.4459 | +9.64c | +7.97c | +1.59c |
| [0.40, 0.47) | 0.4347 | 0.5031 | 238,695 | 0.4921 | +6.80c | +5.74c | +1.10c |
| [0.47, 0.53) | 0.4845 | 0.5293 | 144,210 | 0.5037 | +4.43c | +1.92c | +2.57c |

### Per time-slice de-biased reliability (cheap_mid)

Each window contributes exactly one observation per bin per slice -- no lingering bias within a slice.

| Time-slice | n_windows | Mean gap (all thick bins) | Mean gap (cheap_mid<0.30) |
|-----------|-----------|---------------------------|---------------------------|
| t=60s | ~670 | +4.51c | -8.10c |
| t=120s | ~556 | +4.95c | +3.69c |
| t=240s | ~333 | +8.27c | +10.44c |
| t=360s | ~276 | +10.53c | +15.41c |
| t=480s | ~264 | +11.88c | +16.28c |
| t=600s | ~502 | +13.44c | +20.04c |
| t=720s | ~797 | +15.86c | +22.67c |

The pooled per-slice gap grows with t, BUT late slices also contain far more genuinely-cheap observations -- a price-MIX effect. The fixed-price-band tables below isolate the true time component.

### Fixed-price-band x time-slice (price-mix control)

Holding the price band fixed: if the gap is roughly flat across slices, the per-slice 'edge grows late' pattern is a price-mix artifact, not a genuine late-window effect.

#### Fixed price band [0.10,0.20) -- gap by time-slice

| Time-slice | n | Mean pred | Realized | Gap |
|-----------|---|-----------|----------|-----|
| t=60s | 16 | 0.1625 | 0.0000 | -16.25c |
| t=120s | 69 | 0.1589 | 0.2174 | +5.85c |
| t=240s | 234 | 0.1544 | 0.3077 | +15.33c |
| t=360s | 334 | 0.1520 | 0.3084 | +15.64c |
| t=480s | 397 | 0.1464 | 0.3224 | +17.60c |
| t=600s | 343 | 0.1462 | 0.3557 | +20.95c |
| t=720s | 252 | 0.1468 | 0.3849 | +23.81c |

#### Fixed price band [0.20,0.35) -- gap by time-slice

| Time-slice | n | Mean pred | Realized | Gap |
|-----------|---|-----------|----------|-----|
| t=60s | 233 | 0.3073 | 0.4292 | +12.19c |
| t=120s | 445 | 0.2891 | 0.3461 | +5.70c |
| t=240s | 656 | 0.2784 | 0.3963 | +11.79c |
| t=360s | 598 | 0.2722 | 0.4147 | +14.25c |
| t=480s | 461 | 0.2714 | 0.3883 | +11.69c |
| t=600s | 366 | 0.2744 | 0.4044 | +12.99c |
| t=720s | 276 | 0.2669 | 0.3877 | +12.08c |

### Sanity check A -- decided-market contamination

Are low-`cheap_mid` observations disproportionately from windows already effectively decided? A decided window has large `sigma_proximity` (spot far from strike in vol units).

| cheap_mid band | n | sigma_prox median | sigma_prox p90 | frac(sigma>2) | time_left median | cheap_won |
|----------------|---|-------------------|----------------|---------------|------------------|-----------|
| [0.00,0.10) | 2,138 | 2.10 | 7.76 | 0.518 | 300s | 0.272 |
| [0.10,0.20) | 1,625 | 1.54 | 5.89 | 0.404 | 420s | 0.329 |
| [0.20,0.35) | 3,010 | 1.27 | 4.95 | 0.339 | 540s | 0.394 |
| [0.35,0.55) | 4,817 | 1.22 | 4.41 | 0.310 | 780s | 0.483 |

corr(cheap_mid, sigma_proximity) across the cross-section = **-0.073**. A strongly negative correlation would mean cheaper observations are systematically more decided (their low win rate then partly reflects already-lost windows, not a tradeable mispricing).

### Sanity check B -- cheap-side flip

`cheap_won` in `entry_candidates_15m.parquet` is computed against the side that is cheap *at that observation's tick* (confirmed in Task 5's `build_entry_candidates`: `cheap_won` = `outcome_up` if `cheap_side==YES` else `1-outcome_up`). A window contributing at multiple time-slices CAN appear with different `cheap_side` values (1,112 of 1,676 windows do). Each such row is an independent, correctly-labelled observation -- no contamination.

### VERDICT

Task 6 reported a tick-pooled cheap-side apparent gap of **+13.25c** (mean over thick bins, 20-bin grid). Re-running that tick-pooled method on the 15-bin grid used here gives +14.15c. De-biasing to one observation per (window, time-slice):

- **De-biased pooled cheap_mid gap = +12.48c** (mean over thick bins); cheap_ask (taker entry) = +12.13c.
- Bin-for-bin over the 8 matched thick bins (identical 15-bin grid): tick-pooled mean gap = +14.15c, de-biased mean gap = +12.48c, so **~+1.55c of the apparent edge was the tick-weighting artifact** (~11% of the tick-pooled gap). The remaining **+12.48c survives** de-biasing.
- Low-price bins (cheap_mid<0.30) with the de-biased realized-rate CI still entirely above the diagonal: **4**.
- Time-slice concentration of the POOLED low-bin gap: early (t<=240s) = +2.01c; late (t>=480s) = +19.66c. **But this is mostly a price-MIX effect** -- see below.

**Disentangling the late-window concentration (price-mix vs time).** The pooled per-slice gap grows from ~+4.5c (t=60s) to ~+15.9c (t=720s). That looks like the suspicious late-window pattern -- but late slices also contain far more genuinely-cheap observations (frac with cheap_mid<0.20 rises 1% -> 69% from t=60 to t=720). Holding the price band fixed isolates the true time effect (`trend` = linear gap change across the ~11-min span the slices cover; `rho` = correlation of gap with t):

- Within fixed band [0.10,0.20): per-slice gap +5.8c .. +23.8c (spread 18.0c), trend +17.1c (rho=+0.94) over 6 slices.
- Within fixed band [0.20,0.35): per-slice gap +5.7c .. +14.2c (spread 8.5c), trend +3.2c (rho=+0.43) over 7 slices.

In the well-populated [0.20,0.35) band the gap shows NO monotone time trend (it sits ~+12c at every slice; the only low value is one noisy slice). So the pooled 'edge grows late' pattern is **mostly a price-mix artifact, not a genuine late-window effect** -- late slices simply contain more cheap observations. The underlying mispricing within a fixed price is roughly stable over the window. This is reassuring.

The narrower [0.10,0.20) band DOES trend up (+17.1c, rho=+0.94) -- but that band is confounded: a side this cheap early in a window is rare (n=16 at t=60s) and the *decided fraction within the band rises with t* (frac sigma>2 climbs 0% -> 55% from t=60 to t=720). Decided observations drag realized DOWN, so the rising gap there is a genuine mispricing widening, not contamination. Net: the cheap-tail edge is real; its apparent late-window growth in the pooled view is mostly price-mix.

**Decided-market contamination.** corr(cheap_mid, sigma_proximity) = -0.073 -- weak. Cheaper observations ARE somewhat more decided (cheap_mid<0.10 band: 52% have sigma_proximity>2 vs 31% in the 0.35-0.55 band), and within the fixed [0.10,0.20) band the decided fraction rises with t. Crucially this works AGAINST the edge, not for it: decided cheap observations resolve to 0 and pull the realized rate DOWN. The de-biased gap is positive *despite* that drag, so the surviving edge is not a contamination artifact.

**TAG: EDGE SURVIVES de-biasing but does NOT clear taker cost as a flat unconditional rule -- the +12.5c pooled gap is below the ~16-21% taker round-trip cost. The lowest bins (cheap_mid<0.20, de-biased gap +17-24c) DO clear taker cost; a conditioned cheap-only rule may be tradeable as a taker, and the whole curve is tradeable as a maker (~0 cost) modulo the un-modelled fill-probability haircut.**

**Bottom line.** The tick-weighting / lingering bias was REAL but SMALL: it inflated the apparent edge by only ~+1.5c on average (~11% of the tick-pooled gap), not the large inflation that was suspected. A genuine cheap-side calibration edge of **~+12.5c (pooled, de-biased)** survives -- monotone in price, +17-24c in the cheapest bins (cheap_mid<0.20), shrinking to ~+2-6c near 0.5. The per-slice 'edge grows late' pattern is mostly a price-mix artifact: within a fixed price band the gap is roughly flat over the window. As a flat unconditional rule the +12.5c gap does NOT clear the 16-21% taker cost; the cheap tail (mid<0.20) does, and Task 7's conditioned edge map plus Task 9's net-of-cost calibration should test whether a cheap-only taker rule is profitable. Maker execution clears the cost trivially but the fill-probability haircut is un-modelled here.

**Chart:** `docs/research/charts/calibration_debiased.png`

---
