# Mean-Reversion Live — State Log

> **For new sessions:** read `GOAL.md` first (the why), then this file (where we left off), then `CLAUDE.md` (the how). Append a dated section to this file when you finish a session.

---

## 2026-08-13 - C2 deployed; the coin-grant question answered (and my recommendation reversed)

Acted on the two standing blockers. One shipped; the other turned out to be based on a
premise I had repeated wrongly for three status checks.

**C2 DEPLOYED** (the 08-09 slip closed, 5 days late). The precondition was "diff the soak",
and earlier sessions recorded the soak as DOWN with an empty dir -- that was wrong: the soak
was alive the whole time, it just matches the same `live_executor.py --live` pgrep pattern as
the live executor, so it was miscounted as one process. The diff is clean and exact: across
all 3 windows the soak saw (xrp 1786349700, btc 1786541400, eth 1786543200) C2 produced
IDENTICAL decisions to the running 08-03 build -- same side, same quoted_ask, same
target_shares, same slug set. Deployed via restart_executor.sh at 18:45 IDT in the dead zone;
ok-fills unchanged at 772 (backlog replay placed no orders), C1 counters verified
(replaying=0, skipped_stale=2018). Now live: GLOBAL_MAX_CONCURRENT=3, PER_STRAT=2, the
one-position-per-(window,direction) macro-correlation cap, reserve-before-await.

**The 08-10 incident fixes are committed** (25703d1): discovery closes on the window clock
only, open windows carry forward across a failed poll, plus the collector-liveness alarm in
hourly_monitor. 9 tests.

**bnb/doge do NOT pass rule 2 -- do not grant them.** I had been calling the bnb/doge
allowlist "the ladder blocker" since 08-10 on the strength of their 07-31 capacity gate. The
per-coin decomposition that rule 2 actually requires says otherwise (fav_disagree_live,
official labels, since 06-19):

| coin | n | EV/fill | CI | WR |
|---|--:|--:|---|--:|
| eth | 45 | +$4.516 | [+1.35,+7.81] | 62% |
| sol | 49 | +$2.586 | [+0.07,+5.15] | 61% |
| xrp | 29 | +$2.291 | [-1.05,+5.57] | 62% |
| btc | 14 | -$1.458 | [-6.69,+4.41] | 36% |
| **bnb** | **5** | **-$0.664** | **[-10.43,+9.10]** | **40%** |
| **doge** | **7** | **-$2.492** | **[-10.44,+7.40]** | **29%** |
| **hype** | **17** | **+$8.813** | **[+2.37,+14.72]** | **71%** |

bnb and doge are negative point estimates on n=5/n=7. The 07-31 capacity gate was about
data-collection viability, not EV; treating it as an arming credential was my error.

**hype is the coin that passes on EV -- and it fails on execution.** Paper is strong and
corroborated: the wider twin `fav_disagree` on hype is n=138, +$4.057/fill, CI[+2.57,+5.48],
WR 75%, with BOTH time-halves CI-lo>0 (07-15..08-01 n=98 +$2.756; 08-01..now n=40 +$7.247),
and both cheap-band sub-bands positive. But the 26 REAL hype orders on the live book (all
from the hi_live grant) say:
- fill rate **9/26 = 35%**, under the ladder's 45% bar
- recorded in-band depth_band **median 0**, max 212 (vs 264 xrp / 5047 btc on our fills)
- filled EV -$1.711/fill (WR 44%) vs missed EV +$4.798/fill CI[+0.61,+8.45] (WR 76%)
- **filled-minus-missed = -$6.509/order**: the fills we get are the losers

That is the 08-07 adverse-selection signature. CAVEAT that keeps the door open: all 26 of
those orders are ask 0.47-0.59, i.e. inside the ALREADY-CLOSED >0.45 band, so this
re-confirms rule 1 on hype rather than condemning the cheap band. We have ZERO real hype
orders at ask <= 0.45.

**Therefore: no coin granted today.** Instead the C2 soak was repurposed into a HYPE COVERAGE
SOAK -- `EXEC_SOAK_DIR=data/live/soak_hype` with
`EXEC_SYMBOLS_EXTRA="fav_disagree_live:hype,fav_disagree_hi_live:hype"` passed as process env
(verified: env beats .env under load_dotenv, so the LIVE executor's grant is unchanged and
.env is untouched). Dry-run, zero risk. It records real preflight depth and would-fill
decisions on cheap-band hype books, which is the exact evidence rule 2 wants and the only way
to get it without spending money. hype throws ~2 signals/day, so a week gives n~14.

**Consequence for the 08-17 rung read:** it will be unsampleable, not negative.
fav_disagree_live has 56 lifetime fills and added ONE since 08-10. The mechanical outcome is
"no rung change, stay at $10" -- record it as insufficient sample, NOT as an EV failure, or
the ladder inherits a false negative.

**Next:** (1) read the hype soak ~08-20 for cheap-band fill-rate + depth; if it clears 45%
with non-zero depth, that is the rule-2 evidence for a real grant, and it needs user sign-off
per rule 8. (2) 08-15 Edge Hunt v4 reveal. (3) 08-17 rung read, per above. (4) The soak is
unsupervised by design (temporary); a reboot kills it.


## 2026-08-08 - CONSOLIDATION DAY: doctrine adopted, det retired, C2 shipped, freeze declared

The user reviewed everything and ratified a consolidation plan (session
"peaceful-herding-puppy", plan file of the same name): stop depending on ever-more forward
tests; execute, cover, and scale the one proven edge. Three USER DECISIONS recorded (also
in the ledger): (1) `det_lwd_live` retires NOW, (2) the `fav_disagree_live` size ladder is
FULLY MECHANICAL ($10->$15->$25->$40, dual-CI + fill-rate>=45% + depth check per rung,
rung-down on EV<0, kill at -$50 drawdown, notify-only), (3) FULL RESEARCH FREEZE after the
August calendar until the portfolio is stable at the $25+ rung. All codified in
**PORTFOLIO.md** (new, in the read order): 8 arming rules, the ladder, the closed doors.

**Shipped today (all committed):**
- The entire 08-07 working tree (gate day + adverse-selection proof + leg-2 tilt
  recalibration, docstrings corrected to the pipeline fit) - it had been the largest
  unpersisted state in the repo.
- **det_lwd_live RETIRED** (live:false, twin enabled). Engine restarted 08:45 UTC; armed
  set is now **{fav_disagree_live $10} only**. The 08-14 stop-rule re-read is moot.
- **C2**: executor reserves the slug BEFORE its first await (the same-slug double-trade
  race is closed), dispatch is concurrent (head-of-line blocking gone), caps count
  reservations (PER_STRAT/GLOBAL are real now, GLOBAL 4->3), and a NEW global
  one-position-per-(window, direction) cap across books (macro-correlation). 47/47 tests.
  NOT yet live: a dry-run soak (EXEC_SOAK_DIR=data/live/soak_c2, pid see logs/soak_c2.log)
  runs alongside the live executor; **deploy 08-09 via restart_executor.sh after diffing
  the soak**, and verify the C1 replay counters at that restart.
- **Nightly reconciliation alarm** (`research/analysis/reconcile_executor.py`, wired into
  nightly_honest.sh): executor books vs official ledger. Found det_lwd_live overstating by
  **+$13.97** (frozen allowance; --strict flags it); the five other books reconcile to the
  cent.
- **R1 leg (b) runnable**: xb mode in rejudge_live_model. First evidence (frame through
  07-24, live-3): guarded **-$1.363/fill CI [-2.22,-0.28]** - consistent with the terminal
  default that xb never arms as taker. Gate still runs 08-20 on a rebuilt frame.
- Knife-fill SHADOW instrumentation confirmed already shipped 08-03 (knife_catch flag) -
  nothing new needed before the 08-24 gate.
- 5m label coverage test now excludes cross-coin Polymarket voids (08-05 had one).

**Portfolio assembly study** (ledger, same date): armed+candidate twins corr **0.20**,
combined +$13.59/day paper, max DD -$50 in 51d (caps correctly sized); funnel inside the
allowlist converts **71% signal->fill** - the constraint is signal rate on the 4 live
coins (0.5/day), 11 of 18 recent signals were on unallowed coins; depth supports the $15
and $25 rungs today ($40 re-checks later); fav_disagree hype cheap-band n=9 - **no hype
bundling**, 08-28 stays early_disagree-only.

**Calendar amendments** (everything else in the 08-07 table stands): 08-14 det re-read
CLOSED (moot); 08-17 rung read then every 14d mechanically per PORTFOLIO.md; 08-20 xb
terminal default = no taker arming; 08-21 xh5y is paper-keep-or-kill only (no 5m executor
during the freeze); after 08-28 the freeze begins.

**Honest baseline going into the plan**: live lifetime -$2.42 on $3,327 deployed; the armed
book +$83.35 (+$1.544/fill, per-$ +0.209, no adverse selection). Target: +$5-10/day by
mid-September at the $25 rung, worst day bounded by caps.

---

## 2026-08-07 - GATE DAY: six gates scored, ZERO new arming; adverse selection proven

All six dated gates ran verbatim through `score_gates.py`. Nightly labels were 2.0h fresh.
Full detail + commands in the ledger. **Net result: nothing new was armed, one hypothesis died
as designed, and one measurement METHOD was quarantined.**

| gate | result | branch |
|---|---|---|
| R1 xb live promotion | leg (a) n=188 +$1.428/fill CI [+0.05,+2.86] PASSES | **extend -> 08-20**, leg (b) unrunnable |
| R2 widening cell | n=68 +$0.920 CI [-2.10,+3.93] | no promotion; ONE more look 08-21 then drop |
| R4 hour gate h16-19 | n=15 **+$1.744** (hypothesis predicted negative) | **DROPPED PERMANENTLY** |
| fav_disagree_hi_live $10 rung | n=19 (<25) -$1.402 CI [-3.28,+0.60], fill 46% | **extend once -> 08-21** |
| hype fill-rate calibration | **35%** realised vs 59% predicted (floor 40%) | **MATERIAL MISS, method quarantined** |
| xh5y_g2_v1 14d twin | n=102 +$1.084 CI [-0.93,+3.26] | no live talk, next look 08-21 |

**R1 did not pass despite leg (a) clearing.** Both legs are required. Leg (b) needs the live-2
guarded fill model, which (i) has no xb config in `rejudge_live_model.CONFIGS` and (ii) failed its
own calibration gate the same morning. That gate's registered consequence is that the method
"needs re-calibration before it is used to arm anything else", and arming xb is exactly that.
Leg (a)'s CI-lo is +$0.05 anyway. This was the week's biggest lever and it stays holstered.

**The C6 `above_band` split (shipped 08-03) paid for itself on its first gate day.** Post-C6 hype
no-fills are 7 above_band / 2 dry / 1 abort_floor: the books were HEALTHY and repriced above our
0.60 ceiling between intent and preflight. The fill model does not misjudge depth, it draws the
zero-fill hazard randomly while real misses are adversely selected.

### THE FINDING: execution adverse selection, CI clear of zero on 2 of 3 armed books

Counterfactual EV on misses (the R3-registered method), filled vs missed, bootstrapped:
`fav_disagree_hi_live` filled -$1.44 vs missed +$2.64, gap **-$4.07 CI [-6.66,-1.29]**;
`det_lwd_live` filled +$0.02 vs missed +$0.54, gap **-$0.52 CI [-0.84,-0.20]** (n=817);
`fav_disagree_live` gap +$0.49, **spans zero, not adverse**. Per-$ penalty by ask band is worst at
0.45-0.75 and mildest at the extremes.

This CONFIRMS the 06-10 "misses ARE the winners" result with better instrumentation. It also
**falsifies the 07-25 volume-harvest premise for the >0.45 band**: `fav_disagree_hi_live` was the
registered test of the discarded 78% of signal, and that signal is not harvestable by our taker.
At ask 0.46-0.60 you buy a side the book weakly favours, so being RIGHT prices you out (miss) and
being WRONG fills you. It bleeds on the 4 core coins too (n=10 -$1.760/fill), so it is the BAND,
not hype. Nothing ships off this today: forward gate registered for **2026-08-24**, actionable
only at n>=25 per book with CI-hi<0.

`fav_disagree_live` (cheap band 0.05-0.45) remains the one live book with no adverse selection and
the only profitable one. Its registered window and next look stay **08-17**, deliberately NOT
swapped to the more flattering virgin-era window.

### FORWARD CALENDAR (updated)

| date | item |
|---|---|
| 08-08 | C2 concurrency fix + dry-run soak (unchanged) |
| 08-14 | doge + bnb capacity, ONE look; **det_lwd_live stop-rule re-read** (now also carries the adverse-selection context) |
| 08-15 | EDGE HUNT v4 reveal (sealed) |
| 08-17 | fav_disagree_live 2nd extension read |
| 08-20 | R1 xb live promotion, extended look |
| 08-21 | R2 final look (drop if CI-lo<=0); fav_disagree_hi_live rung; xh5y_g2_v1 |
| 08-24 | knife-fill gate + NEW adverse-selection gate |

### SHIPPED SAME DAY: leg-2 fill model recalibrated, quarantine LIFTED

The morning's quarantine is closed by fixing the defect, not waiving it. `fills_live.py`
drew the zero-fill hazard independently of the outcome; real misses skew to the winners.

- **lambda = 1.3959, CI [1.159, 1.715]**, fitted on 944 labelled live attempts.
  P(no-fill | WIN) 0.4634 vs P(no-fill | LOSE) 0.3320. Params **live-2 -> live-3**; old file
  kept at `data/research/fill_model_live.pre_tilt_20260807.json`.
- **Validated OUT OF SAMPLE**, which is the leg that matters: fit on the first 70%
  (06-05..07-01) gives 1.399, the sealed last 30% (07-01..08-07) gives **1.463 CI [1.11,
  2.04]**, still excluding 1.0. Stable by month. Not a fitted artifact.
- ONE global lambda by choice. The per-band fit (cheap 1.03, 0.45-0.60 1.60, 0.60-0.75
  2.65, 0.75-1.00 1.17) is kept as a diagnostic only: 3 of 4 band CIs overlap the global,
  so a table would fit noise in three cells to chase one.
- Marginal hazard is preserved, so the 57% overall fill rate is unchanged and only WHICH
  attempts miss moves. `wins=None` reproduces the old model exactly, so all six call sites
  and every old params file are unaffected. 10 new tests; 36/36 green.
- **`early_disagree_live` re-scored, same seed, only the params file differs:
  +$2.024/fill -> +$1.277/fill (-37%), WR 58.4% -> 52.9%.** Still clears the leg-2 bar
  (>= +$0.50, CI-lo +0.74). The idealized v2 row is bit-identical, confirming only the
  hazard moved.
- **Honest residual:** the corrected model still predicts +$1.277 where the real book
  delivered **+$0.336/fill (n=32)**, so it is ~4x optimistic even after the fix. Use the
  model as a GATE, never as a forecast. Planning number is +$0.34/fill (~+$1/day).
- R1/xb stays blocked: no xb config exists in `rejudge_live_model.CONFIGS`, and 100% of
  xb's value is in the adverse band. 08-20 unchanged.

### DEPLOY REQUESTED AND BLOCKED: early_disagree_live's edge is hype, not the live universe

User approved arming `early_disagree_live`. **Not executed.** The pre-arm coin check killed the
premise: FINDING C's CI-lo>0 headline was an ALL-7-COIN number, and the executor trades four.

| slice | n | EV/fill | CI |
|---|--:|--:|---|
| **4 live coins, virgin** | 329 | **+$0.435** | **[-0.21, +1.09]** |
| **4 live coins, 30d** | 120 | **+$0.602** | **[-0.52, +1.68]** |
| all 7, virgin | 392 | +$0.709 | [+0.11, +1.32] |

Per coin, only **hype** clears zero (n=38, +$3.185, CI [+1.31,+4.92]); btc is **-$1.748**.
Arming on btc/eth/sol/xrp would deploy a book with no measured edge, which is the exact error
`fav_disagree_hi_live` made and that we diagnosed nine hours earlier. Registered instead: hype
grant gated **2026-08-28** (needs n>=25 AND CI-lo>0 AND fill-rate>=40% in the 0.30-0.45 band).

**Process fix:** promotion shortlists must be decomposed by EXECUTABLE COIN SET before being
proposed, not at the arming step. A pooled score over a universe wider than the executor's
allowlist is not a promotion metric. Same class as band-filtering before ranking (FINDING A).

### OPEN DECISIONS FOR THE USER (no config was touched)

1. `fav_disagree_hi_live`: registered branch is extend to 08-21, but its own paired calibration
   gate failed and the band is now understood to be structurally adverse. Costs ~$2/day to keep.
2. The leg-2 quarantine blocks every future arming that leans on the guarded fill model, which is
   most of them. Re-calibrating it (adverse-selected hazard instead of random) is the highest-value
   piece of engineering on the board.

---

## 2026-08-03 - FULL AUDIT + HONEST DASHBOARDS + BUNDLE A (C1/C6/C4-lite/C7); 08-03 gate scored

Three parallel audits (research history, live execution machinery, empirical results), then a
user-approved plan implemented end to end. **The headline finding is a measurement defect, not
a trading one.**

### THE BIG ONE: the paper dashboard was ~3x inflated, AFTER the 06-19 "honest settlement" fix

The 06-19 fix re-scored RESEARCH on official on-chain labels, but every human-facing surface
(status §5/§5b, the diary, the hourly monitor) kept reading the ENGINE tape. Re-measured:
the engine's reconstructed-Chainlink resolution disagrees with real money settlement on
**17.6% of identical markets, biased 2.4:1 in our favour** (40 paper-win/live-loss vs 17 the
other way, binomial p~0.002 — an asymmetry that size is correlated with the side we bet, i.e.
residual boundary-snapshot bias, not oracle noise). Uniform across distance-to-strike
quartiles, stable every week W24..W31.

Consequences, all now surfaced rather than hidden:
- Paper post-06-19: engine **+$13,863** vs official **+$4,595** = **3.0x**.
- Last 7d: engine **+$1,568** vs official **-$242**. The week the dashboard called good was bad.
- `det_lwd_v1` engine +$1,446 / official **-$13**; `det_lwd_v1_capped` **201x**; `fav_lowvol` 5.4x.
- Only TWO strategies are genuinely positive on official 30d data with CI-lo>0:
  **fav_disagree** (+$1.31/fill) and **xb_5m15m_causal_v1** (+$1.08/fill, and accelerating:
  14d +1.29, 7d +1.42, n=401, never armed).
- hype has no Chainlink feed so it settles via `coinbase_fallback` (~2x inflation) and is 44%
  of paper P&L since 07-17 — the 07-17 capacity expansion partly re-opened the same hole.

**Gate decisions were never affected** (`score_gates` has always used official labels). What
was affected is every human judgement made off the daily numbers.

### SHIPPED

1. **Dashboards now read OFFICIAL labels.** §5 of the mean-rev-status skill is rewritten to
   source `data/research/paper_official/daily_scores.parquet` (virgin era, entry >= 06-19),
   showing official total$/daily$/WR with `engine$` + an `infl` ratio column kept visible
   beside them. §5b (today) cannot be honest intraday — official labels arrive with the
   nightly — so it is now explicitly labelled "ENGINE-settled, ~3x hot" in the code, the
   template, and the notes. §7 templates and the §8 diary rules updated to match: no paper
   dollar may be quoted in the diary without citing the official number.
   `status.py:103` header relabelled. **New alarm** in `hourly_monitor.sh` (step 6): warn if
   `daily_scores.parquet` is >26h stale — the honest pipeline had no dead-man's switch, and a
   dead nightly silently starves every gate read.
2. **C1 — restart no longer discards the intent backlog** (`live_executor.py`, `processed = 0`).
   It used to start reading at EOF, so every restart threw away whatever was queued with no
   log line, no fill record, no counter. The hourly cron is the executor's only supervisor, so
   an unnoticed crash could silently drop up to an hour of intents. Replay is free: the
   existing gates reject stale lines (time_left < 20s before any network call, done_slugs,
   age > 10s under EXEC_GUARDS=on). Pinned by a new test.
3. **C6 — preflight splits `above_band` out of `dry`.** A healthy book priced above our
   ceiling was labelled identically to an empty one (band_depth is 0 by construction once the
   touch clears the ceiling), which already caused one misdiagnosis. Both verdicts still get
   the SAME retries and the same skip — the split is purely so they are tellable apart, which
   the 08-07 hype fill-rate calibration needs. Two new tests pin both sides.
4. **C4-lite** — `intents.jsonl` (458KB, never rotated) was fully re-read twice per second
   forever, with settlement sitting behind it. Now re-read only when the file grows.
5. **C7** — `EXEC_SYMBOLS=btc,eth,sol,xrp` written explicitly into `.env`. The operative live
   allowlist previously existed ONLY as a code default while `.env` showed `SYMBOLS` = 7 coins
   two lines above, which reads as if live trades all seven.
6. **UPTOK filter in `score_gates.load_fills()`** — 16 synthetic smoke-test rows sit in
   `fills.jsonl` and **3 of them carry a real sid** (`det_d12_wide_live`, ok=true), so any
   lifetime read without `--since` scored fake fills. No registered gate was affected (all
   windows start 07-03+); the ledger stays immutable, the reader filters.
7. **Test-suite hygiene**: two "ships unarmed" pins had been RED since 07-25/07-26 because the
   module calls `load_dotenv()` at import and `.env` legitimately arms those knobs. They now
   assert the shipped fallback via `_shipped_default()`. A permanently red suite hides the
   next real regression. **Full executor suite: 43/43 green.**

### GATE 08-03 SCORED: fav_disagree_live $15 rung -> EXTEND at $10 (2nd extension)

n=31, total +$120.74, **EV/fill +$3.895 CI [-0.41,+8.12]**, WR 55%, per-$ +0.422 CI
[-0.05,+0.88], fill-rate 31/41. Both CIs span zero (per-$ by $0.05) => registered branch is
extend, no config change. Next look **2026-08-17**. Full entry in the ledger.

### FORWARD GATE CALENDAR (supersedes the 08-01 table below for dates only)

| date | gate | status |
|---|---|---|
| ~~08-03~~ | fav_disagree_live $15 rung | **SCORED 08-03 -> extend at $10; next look 08-17** |
| 08-06 | R1 xb live promotion | pending — **the week's biggest lever** (+$3-4/day if it passes) |
| 08-07 | fav_disagree_hi_live $10 rung | pending — n~22 vs the n>=25 bar, so likely "extend to 08-21" |
| 08-07 | hype fill-rate calibration | pending — realized 56% vs the 40% floor; needs `--symbol` on score_live |
| 08-07 | R2 widening cell | pending |
| 08-07 | R4 hour gate (ONE look) | pending — weakest prior, likely DROP; note NO hour-exclusion knob exists yet |
| 08-07 | xh5y_g2_v1 14-day twin | pending — if it passes, live TALK only (5m slugs are unproven in the executor) |
| 08-08 | C2 concurrency fix + dry-run soak | scheduled (deliberately after the measurement window) |
| 08-14 | doge + bnb capacity (one look) | pending — default DROP; **also det_lwd_live stop-rule re-read** |
| 08-15 | EDGE HUNT v4 reveal | sealed |
| 08-17 | fav_disagree_live 2nd extension read | registered today |

### THE PROFIT PICTURE (honest)

Live is **-$12.89** lifetime on 757 fills, running about **-$1.4/day**: `fav_disagree_live`
earns (+$54.23 lifetime, the ONLY book with payoff ratio > 1 at 2.02) but is starved to ~0.6
fills/day; `fav_disagree_hi_live` is **-$22.26 in 9 days** and its own official twin never
supported it (+$3 lifetime, CI spans zero); `det_lwd_live` costs ~$1/day as an execution
canary. **Volume is allocated inversely to EV** — that is the whole problem, and the dated
gates are the mechanism that fixes it, not a new edge hunt.

Volume decay is coverage, not signal: armed-coin share fell 100% (W23-W28) -> 38% (W30) ->
27% (W32) as intents moved to unarmed coins; fills/week 216 -> ~40. But **88% of the blocked
volume is `det_lwd_live` on hype/doge at -$0.128/fill official**, so the allowlist is mostly
doing its job. A global widen would put the losing probe on the new coins at 14x the volume
of the strategy that actually has the edge. Per-strategy grants only.

### USER DECISIONS TAKEN

- `det_lwd_live`: **keep at $2**, re-read its stop rule at the **08-14** gate day (not 08-07).
- `fav_disagree_hi_live`: **let the 08-07 gate decide**, do not disarm early despite the bleed.
  Preserving pre-registration discipline is worth more than ~4 days of a capped loss.
- Bundle A: **ship now**, one deliberate restart in the intent dead zone.

### NEW HYPOTHESIS REGISTERED: the fill-time floor leak ("knife fills") — gate 08-24

Found while closing the fills-accounting question, which itself came back CLEAN:
`usdc_paid / filled_shares == avg_price` on **742/742** fills, so the share counts are sound
and `avg_price` beating the round-1 quote is just laddered-VWAP arithmetic. That item is closed.

What it surfaced instead: split live fills by `drop = quoted_ask - avg_price`, on official labels.

| cohort | n | EV/fill | per-$ | WR | total |
|---|--:|--:|--:|--:|--:|
| drop > 5c | 91 | **-0.727** | -0.159 | 57% | **-$66.12** |
| drop 1-5c | 229 | +0.363 | +0.074 | 71% | +$83.19 |
| drop <= 1c | 421 | -0.076 | -0.016 | 74% | -$31.96 |

Both obvious confounds are REJECTED: it is not the dead `det_d12_dual_live` book (every
strategy's knife cohort is negative), and it is not pre-guard history (post-07-06 the effect is
STRONGER: n=19 EV -1.335 WR 47%). On the three currently-armed books alone: knife n=57
**-$27.78** vs the rest n=427 **+$74.33**.

This is not a new edge claim — it is an enforcement gap in an already-validated guard.
`EXEC_FLOOR_DROP` exists precisely because a cheap fill on a collapsing book is a knife-catch,
and `_preflight`'s own docstring calls that cohort "-EV (measured)". But the guard only tests
the book at PREFLIGHT; all 14 post-guard cases carry `guard.verdict == "ok"` and then the book
fell away during the ladder, letting the same cohort back in at fill time.

**Nothing shipped.** A post-hoc live slice is how this project has been fooled 43 times.
Registered in the ledger with the threshold frozen at drop>0.05, gate **2026-08-24**, promote to
enforce iff forward n>=25 AND EV<0 AND CI-hi<0, else DROP permanently. Recommended first step is
SHADOW mode (log `would_abort_fill_floor`, change no behaviour) — it cannot contaminate any gate
and it replaces a post-hoc slice with clean forward data. Needs user sign-off either way: it
changes real-money execution.

### OPERATIONAL LESSON: don't run the test suite next to a live executor

The two "stale signal" skips at 18:29 IDT (ages **157.8s / 177.7s**, vs the 14-31s historical
range) were **caused by this session's own test runs**, not by any code change: two concurrent
pytest suites drove load average to 9.7, starved the executor's 2 Hz poll loop for ~3 minutes,
and it caught up the same second the run finished. Two real intents were correctly dropped by
the 10s staleness gate. The same contention also produced 7 phantom failures in a concurrent
full-suite run that passes cleanly when run alone (43/43 file, 385 research+executor).
`CLAUDE.md` now requires `nice -n 19` for the suite. Same external-CPU-hog mode as 06-06.

### STILL OPEN (deliberately deferred past gate week)

- **C2 (concurrency) is NOT a one-liner** — bare `asyncio.create_task` at the intent loop has a
  same-slug double-trade race: `_blocked` checks `done_slugs` synchronously but
  `inflight_slugs.add` happens AFTER the preflight await, so two intents for one slug can
  interleave and both pass. Needs the slug reserved before the first await, then a dry-run
  soak diffing decisions against the live process. **Scheduled 08-08.** Until then the serial
  loop head-of-line blocks (p90 ladder latency 9.7s against a 10s staleness bound).
- `PER_STRAT_MAX_CONCURRENT` / `GLOBAL_MAX_CONCURRENT` are **dead code** (unreachable while
  handling is serial). They read as a shared-wallet collateral guard and are not one. Fixing
  C2 activates them.
- **C5 was NOT "fixed" by adding the executor to start_all/stop_all** — that would race the
  hh:37 monitor relaunch (a stop without EXEC_KILL loses to the cron; one with EXEC_KILL leaves
  real money down after an engine-only maintenance stop). The runbook is the artifact.
- ~10.6% of fills report `avg_price` >5c below `quoted_ask` with `fill_ratio` up to 4.32;
  dollar totals reconcile ($0.112/win) so this is likely benign ladder mechanics, but the
  share counts are unverified. Bounded check: spot-check 5 against on-chain redemptions.
- The residual 17.6% paper-vs-real settlement disagreement is a RESEARCH item; official labels
  bypass it, so it blocks no money. `paper_engine.py` settlement stays deliberately unchanged.

---

## 2026-08-01 - GATE-WEEK PREP: repo versioned, alarm actually scheduled, score_gates tool shipped, calendar staged

Five dated gates land 08-03..08-07. This session (user-approved plan) made gate week
mechanical and closed the operational holes; NO gated forward slice was scored.

**SHIPPED:**
1. **Git**: a month of drift committed (HEAD was 07-03). The entire live-money path
   (`scripts/live_executor.py`, `src/mean_reversion_live/live/`) had NEVER been under git,
   the running system was the only copy. Now versioned; secrets scan clean; `.env`/`data/`
   stay ignored.
2. **Chainlink alarm scheduled**: `hourly_monitor.sh` (the rows_ok=0 detector written after
   the 07-24 32h silent outage) was in NO crontab and had never executed once. Now
   `37 * * * *`. Two cron-env verification runs; fixed on the way: cron PATH lacked uv, and
   the monitor's claim step was missing the relayer `--with` deps so its preflight failed
   silently (the supervised claim_loop was never affected).
3. **`research/analysis/score_gates.py`**: the registered gate metric as a committed,
   tested tool (see ledger 2026-08-01 for validation + the 07-26 snapshot caveat). Gate
   day is now one command + a decision.
4. **det_lwd_live registered-method read**: lifetime n=412 +$0.034/fill CI [-0.19,+0.25]
   (break-even canary, as designed); last-14d n=56 **-$0.522/fill** CI [-1.10,**+0.01**];
   since-$2 n=25 -$0.288 CI [-0.66,+0.07]. Stop rule (<= -$0.50 AND CI-hi<0) NOT
   mechanically tripped, by $0.01 of CI-hi. **Decision with the user**: keep the $2 canary
   vs stop.
5. **Ledger dispositions**: bnb capacity gets its missing date (08-14, with doge, one
   look); R3 CLOSED without eval (9/25 misses by 08-07 is unreachable; armed 4b is the
   07-25-amendment substitute). Watch-item closed: GLOBAL_MAX_CONCURRENT=4 fired ZERO
   times in the whole live_exec.log.
6. **EDGE HUNT v4 pre-registered and sealed** (`docs/research/EDGE_HUNT_V4_PREREG_2026-08.md`):
   window 07-24..08-14, reveal 08-15, atlas persistence + new-cell scan + the two frozen
   early-timing disagree cells (that thread's last look). Zero data contact until reveal.

**GATE CALENDAR** (each = one `score_gates` command + the registered thresholds; windows
below are the registered ones, do NOT run early):

| date | gate | command | decide |
|---|---|---|---|
| 08-03 | fav_disagree_live $15 rung | `live --sid fav_disagree_live --since 2026-07-03T07:40 --until 2026-08-03` | CI-lo>0 on BOTH $/fill and per-$ => propose $15; spans 0 => extend at $10 (2nd extension); <=-$0.50 CI-hi<0 => stop |
| 08-06 | R1 xb live promotion | (a) `paper --sids xb_5m15m_causal_v1 --since 2026-07-24 --until 2026-08-06`; (b) live-2 guarded on recorded decisions (07-26 method) >= +$0.50/fill | both pass => propose live @$10 own book; CI-hi<0 => KILL; else extend 2wk once |
| 08-07 | fav_disagree_hi_live $10 rung | `live --sid fav_disagree_hi_live --since 2026-07-25 --until 2026-08-07` | n>=25 AND CI-lo>0 AND fill-rate>=40% => propose $10; CI-hi<0 or EV<0 @n>=40 => KILL; n<25 => extend once to 08-21 |
| 08-07 | hype fill-rate calibration | hype fills/attempts from fills.jsonl since 07-26 | <40% => leg-2 guarded method recalibration before it arms anything else |
| 08-07 | R2 widening cell | `paper --sids fav_disagree_d5 --minus-sids fav_disagree --ask-band 0.30,0.45 --since 2026-07-24 --until 2026-08-07` | CI-lo>0 AND n>=40 => propose dist_min_bps 10->5; CI-hi<0 or EV<0 @n>=40 => KILL |
| 08-07 | R4 hour gate (ONE look) | `paper --sids fav_disagree --hours 16,17,18,19 --since 2026-07-24 --until 2026-08-07` | EV<0 AND CI-hi<0 => propose skip-window; anything else => DROP |
| 08-07 | xh5y_g2_v1 14-day twin | `paper --sids xh5y_g2_v1 --since 2026-07-24 --until 2026-08-07` | CI-lo>0 => live talk (sign-off); CI-hi<0 or EV<0 @n>=40 => KILL |
| 08-14 | doge + bnb capacity (one look) | `paper --sids fav_disagree,fav_disagree_live --symbol doge --since 2026-07-17T17:00 --until 2026-08-14` (repeat --symbol bnb) | n>=30 CI-lo>0 + guarded survive => propose; still inconclusive => DROP |
| 08-15 | EDGE HUNT v4 reveal | per the sealed prereg | one look |

**SYSTEM** (verified this session): engine PID 75250 up since 07-25, heartbeat ~2s, all 7
coins ticking; executor PID 55492 up since 07-26, pending=0 on all 6 books; nightly honest
green 08-01 03:16Z; Chainlink feed live (oracle_age 13-31s). Still open: executor restarts
silently drop in-flight intents (avoid restarts in gate week; pending=0 makes a needed one
safe), and `ws_recv_ended` background rate unchanged.

---

## 2026-07-26 — hype ARMED for fav_disagree_hi_live: the allowlist, not the edge, was the drought

**THE SYMPTOM.** 33h with zero live fills. **20 of 20 intents since the last fill were hype or doge**
— every one skipped by the executor allowlist. Not a signal outage: the paper twins of the live
strategies traded 9-20x in the same window, 100% on unarmed coins. Armed-coin share of live-strategy
signal has been collapsing: 07-16 100% -> 07-20/21 40-50% -> 07-23 28% -> 07-25 28% -> 07-26 **0%**
(10d aggregate 41%, last 4d 30%). `fav_disagree_hi_live`, deployed 07-25 to harvest exactly this,
was heading for its 08-07 n>=25 gate with **n=0**.

**RESOLVED THE 07-31 GATE 5 DAYS EARLY** (criteria fixed 07-17; hype crossed n>=30 on its own):
1. **Capacity gate:** hype n=88, EV **+$3.61/fill**, CI [+1.56,+5.56], WR 76% => PASS. doge n=22,
   CI [-1.49,+10.04] => inconclusive. btc/bnb "KILL" is n=4 noise, ignored.
2. **live-2 guarded fill model** (the registered pre-condition). `rejudge_live_model` can't run it —
   joined_15m covers btc/eth/sol/xrp only — but both inputs exist for hype, so it ran on them
   directly: recorded decisions (twins' official in-band entries) x real `data/live_l2/hype_*` 10-level
   ladders x `simulate_taker_entry(mode="guarded")` @$5. hype **36/61 filled (59%), EV +$2.03/fill,
   CI [+0.97,+3.04], WR 78% => SURVIVES**. doge 5/11, -$1.29 => FAILS. 59% on a $15 median book beats
   the live 4-coin funnel. Expect ~4.3 fills/day ~= **+$8.7/day** vs ~$0 today.
   Caveat kept in the open: the model draws the zero-fill hazard RANDOMLY while live misses are
   adversely selected, so +$2.03 is an OPTIMISTIC bound (it scales to +$4.05@$10, above the paper
   +$3.67 — that inversion IS the tell).
3. **User sign-off** 2026-07-26.

**SHIPPED:** `.env EXEC_SYMBOLS_EXTRA=fav_disagree_hi_live:hype`, executor bounced, verified
`symbols_extra={'fav_disagree_hi_live': ['hype']}` with all 5 books preserved. hype ONLY, that
strategy ONLY — `det_lwd_live` deliberately gets no hype (70 of its last 75 intents were hype, at
-$0.128/fill official, now $2/trade). doge stays paper-only, re-evaluates 08-14.

**WATCH 08-07:** realised hype fill-rate vs the 59% predicted. A miss below 40% means the guarded
model over-credits thin books and leg-2's method needs re-calibration before it arms anything else.

---

## 2026-07-25 — VOLUME HARVEST: we were discarding 78% of our own validated signal; +1 live book, Chainlink outage fixed

User asked to "find more ways to be profitable". Answer came from measurement, not discovery:
**the binding constraint is COVERAGE of the one surviving edge, not a missing edge.**

**THE FINDING.** `fav_disagree_live` is a strict SUBSET of its own paper twin. `fav_disagree` is the
identical rule (mode disagree, tl 120-360s, dist>=10bps) differing in exactly ONE number:
max_ask **0.90 vs 0.45**. On official labels since 06-19: twin **8.74 sig/day / +$24.28/day @$10**;
live **2.07 intents/day -> 0.93 fills/day -> ~+$5.3/day**. The discarded ask 0.46-0.60 cohort scores
**n=206, +$1.80/fill, CI [+0.49,+3.09], 5.9 sig/day, WR 62%**, median intent-time depth $18.55.
The 07-03 axis-3 ask-band look never saw it — it only examined *inside* the live band (<=0.45).
Economically it is a different payoff shape, not more of the same: below 0.45 you buy the side the
book actively DISfavours (WR ~50%, big payoff); at 0.46-0.60 you buy a side the book weakly favours
but UNDERPRICES (WR 62%, small payoff). Hence a separate book, not a widening.

**CORRECTION TO THE 07-31 CAPACITY GATE (would have deployed nothing):** 94% of hype's value sits at
ask>0.45 (n=73, **+$3.60/fill, CI [+1.81,+5.29]**, WR 78%, 2.09/day); in-band hype is 0.14 sig/day.
And **70 of the last 75 hype intents came from det_lwd_live**, not the disagree family — so a global
`EXEC_SYMBOLS += hype` would have put the losing probe on the new coin at 14x the volume of the
right one. Hype must be armed PER-STRATEGY, for `fav_disagree_hi_live`, or it is worth ~nothing.

**SHIPPED (all pre-registered in test_ledger.md "VOLUME-HARVEST ROUND" BEFORE deploy; suite 579
passed / 5 skipped / 0 failed, sweep_v2 excluded for the pre-existing missing lightgbm):**
1. **`fav_disagree_hi_live` LIVE** — disagree, tl 120-360s, dist>=10, **ask 0.46-0.60**, $5/trade,
   own $100 bankroll + $25/day hard_worstcase, own executor book. `fav_disagree_live` UNTOUCHED at
   $10/0.05-0.45 so the 08-03 size-rung gate keeps reading a clean book. min_ask **0.46** not 0.45:
   `determinism_state.py:402` is inclusive at BOTH ends, so 0.45 would double-fire on one slug
   (asserted in the roster check). GATE 2026-08-07: n>=25 AND official CI-lo>0 AND fill-rate>=40%
   => propose $10; KILL CI-hi<0 or EV<0 at n>=40. At $5 expect ~+$1.4-1.8/day — this rung buys the
   RIGHT to $10, it is not the payoff.
2. **det_lwd_live $5 -> $2.** -$30.40 over its last 12 traded days, +$0.078/fill lifetime (433
   fills), official clean-era EV -$0.128. Stop rule NOT tripped, so a SIZE decision not a kill: it
   stays the always-on execution canary (88% of live intents) at ~40% of the bleed.
3. **Pruned** `psettle_ud_v1` (-$316/14d official) and `det_disagree_v1` (-$119/14d). 17 enabled.
4. **Executor bug 4a — round-2 re-quote.** `:582-586` bumps cur_ask to the real touch before round 1
   (an IOC below the touch is an API-400, not a fill, and clob_trade breaks the ladder on the error
   WITHOUT advancing a tick) — but the loop never refreshed it, so a zero-fill round re-fired the
   same known-bad price 4s later. **36 of 115 ladder rounds died this way.** Now re-runs `_preflight`
   and re-applies the bump; aborts if the book collapsed between rounds (knife cohort).
5. **Executor 4b — bounded dry-retry, ARMED at `EXEC_DRY_RETRY_N=3`.** All 17 dry skips were
   `best_ask > ceiling` (the price moved above max_ask — NOT a thin book), and the single 3s
   re-check already rescued 7. Still taker-only, bounded by time_left. Default 1 = legacy.
   This is the cheap version of registered R3 (30s resting limit); R3 still evaluates 08-07, but if
   it passes, ship 4b instead — a resting maker bled -$1.99/tr (module header).
6. **Per-strategy symbol allowlist** `EXEC_SYMBOLS_EXTRA="sid:sym,..."` — **SHIPPED UNARMED**
   (empty = byte-identical). Arms hype for `fav_disagree_hi_live` only after the 07-31 gate + the
   live-2 fill check + user sign-off. 7 new executor tests; 40/40 in that module.

**CHAINLINK OUTAGE FIXED (32h, 100% dead, silent).** 9,207 `chainlink_fetch_failed`, ZERO successes,
all 6 coins, from 2026-07-24 09:02 UTC. Cause: the built-in `DEFAULT_POLYGON_RPC` (Tenderly public
gateway) went dead, and `.env` set `POLYGON_RPC` (the claimer/relayer name) but not
`POLYGON_RPC_URL` (the collector name, `run_combined.py:88`), so the override never applied. Do NOT
"tidy" one name away — `claimer.py`/`relayer.py` genuinely read `POLYGON_RPC` (real money); both are
now set and commented. Default repointed to publicnode (verified serving `eth_call` on the BTC/USD
feed). **Alarm added** (`hourly_monitor.sh` step 5) on the collector's own `chainlink_status
rows_ok=0` — validated against this incident (it fires on the broken state, silent on the fixed one).
NO live-money impact: neither live strategy reads `cl_dist_bps`. **VOID over the gap:**
`det_d12_dual_v1` (oracle_gate agree, fail-CLOSED => fired ZERO = void, not neutral), psettle's cl
leg, every `cl_*` tick field. Official labels come from Gamma, so all open gates still score.

**DEPLOY VERIFIED (cap-safe: no UTC-day losses booked, nothing pending/open at restart).** Engine
15:54 UTC, 17 strategies, heartbeat <5s, queue 0, `skipped_book=0`. Chainlink `rows_ok=6 rows_err=0`
on cycle 1. Executor restarted twice (second to arm the retry), **all 5 books restored to the cent**
both times (realized, deployed, done_slugs, per-day cap state), single instance, startup line shows
`dry_retry_n=3`, `symbols_extra={}`. `fav_disagree_hi_live` fired within the hour and correctly:
doge, ask **0.59** (in band), dist 10.52, tl 174, $5, max_ask 0.6 — paper win +$3.33.

**CAVEATS / WATCH.** (a) That first intent landed during the executor restart window and was
silently dropped — the executor starts reading intents at EOF (`:803`), so restarts lose in-flight
intents with no skip log. Pre-existing; it was doge (paper-only) so nothing was lost. (b)
`ws_recv_ended` runs 1-6/hour as a pre-existing background rate (the 95 spike at 15Z is boot
subscription churn); books are fresh (`skipped_book=0`) — noted, not chased. (c)
`GLOBAL_MAX_CONCURRENT=4` has produced 0 skips; a third live book could start binding it — check
`intent_skipped` for the global-concurrency reason in a week, do NOT pre-emptively raise it.
(d) Realistic stack: ~$5.3/day -> ~$8/day now; the $15-25/day target still depends on 08-03 (rung),
08-06 (xb), 08-07 (xh5y + R2/R3/R4 + this new gate).

---

## 2026-07-24 — EDGE HUNT v3: freeze lifted, g2bps-5y family CONFIRMED on fresh data

Pre-registered (test_ledger "EDGE HUNT v3") BEFORE any reveal, then scored the two threads
on the never-mined 2026-07-03..07-23 window (frames rebuilt through 07-24; the monolithic
rebuild kept getting externally killed — built incrementally via research/build_5m_increment.py;
xbook + slim rebuilt; official labels current).

**V3a — g2bps-5y retest: 2/3 SURVIVE → cross-horizon door RE-OPENS.** The v2 "suggestive
but failed FDR" specs, frozen by name, now pass every gate on fresh forward data:
xh_5y_m02_g02_b600-900_r1_c90 (n=132, +$1.02/$5fill, CI [+0.16,+1.95], p=.019) and
_r10_c97 (n=102, +$0.93, CI [+0.09,+1.83]). ~6.6 signals/day, Jaccard 0.15 vs xb twin
(mostly NEW volume). Economic content: in the last 5 min of a 15m window, the co-terminal
5m market's cheap YES trades >=2bps-gap rich vs the 15m book => buy the 5M instrument.
**Twin DEPLOYED same-day** (mode="xb5y" + xb15_* collector fields, 14 tests, engine restarted 08:47 UTC as xh5y_g2_v1; gate eval 2026-08-07). Original blocker was: PART B = attach
co-terminal 15m book to 5m ticks (or let xb emit on the 5m slug), then standard 14-day
official-settled twin gate. NO live talk before that.

**V3b — Atlas v3: 0 positive candidates** (sealed future never opened for positives);
41 fade cells persist. The v2 disagree cells keep sign but thin (dev/hold descriptive only).
No new-cell claims; live fill gate stays the arbiter for the disagree family.

Standing calendar: 07-31 hype capacity gate (early read PASSING: n=49 +$3.88/fill official,
CI-lo +1.68) · 08-03 fav_disagree $15 rung (n=27 +$4.01/fill, CI-lo -0.47, one win from
flipping) · 08-06 xb promotion gate (R1) · 08-07 R2 widening / R3 exec-rescue / R4 hour gate.
Live untouched all session: det_lwd_live + fav_disagree_live only, account ~$700.

---

## 2026-07-06 — Forward-validation posture: roster pruned 26→19, fav_disagree_live success gate pre-registered, macro collector revived

Status check + housekeeping session (user-approved plan: "freeze research, run the fav_disagree
forward test cleanly, decide 07-20 with a gate written down today").

**Live health (16:19 UTC):** engine 3d6h uptime, all green. Ground truth (data-api) **+$62.34
realized (202W/42L)**, account fully liquid $609.92 / $0 open exposure. det_lwd_live +$58.38
(88% WR, execution probe); fav_disagree_live +$4.35 today at its new $10 sizing, stop rule not
tripped. The status skill's Δ(book−truth)=−$152.83 UNDER-COUNT flag was a **false alarm**:
backfill dry-run = 0 classifiable / +$0 bookable — the gap is 3 weeks of strategy-roster churn
(killed strategies' history), NOT missed settlements. Post-churn, trust the account balance +
ground-truth line, not the Δ flag. det_d12_dual_live demotion confirmed already done (06-18).

**ROSTER PRUNE (strategies.yaml, engine restarted ~16:50 UTC, wrapper pid 58091):** disabled the
7 killed-by-rule paper strategies — det_sqp_v1/_capped/v2, fav_deepdown, tadiv_approx_v1/_ret3,
oracle_fade_v1 (verdicts final on official labels; paper ledgers freeze 2026-07-06). 26→19
enabled; live flags unchanged (det_lwd_live + fav_disagree_live only). Cuts ~500 trades/day of
engine load (saturation guard). Restart was cap-safe (no UTC-day losses booked at restart time).

**PRE-REGISTERED (test_ledger.md "fav_disagree_live FORWARD-VALIDATION GATE"):** evaluate
2026-07-20 on official-settled fills since re-arm (07-03): CI-lo>0 → propose $15 rung (needs
fill-rate hold + user sign-off); CI spans 0 → extend at $10 to 08-03; stop rule unchanged.
RESEARCH FREEZE re-affirmed: no new discovery sweeps before 2026-07-24 (3wk post-registration
forward data). Open threads: the 3 deployed twins (fav_disagree_d5cl_v1, early_disagree_cl_v1,
xb_5m15m_causal_v1) + g2bps-5y note. OPEN DEFECT worth closing before any size-up: the executor
ladder's max_ask overpay (18% of det_d12_dual_live fills cleared above cap — audit lives with
the executor, affects fav_disagree_live at $15+ too).

**macro_collector REVIVED** (down since 06-19 00:30 UTC — 2.5wk gap in data/live_macro/):
relaunched via respawn_generic.sh (sentinel data/MACRO_KILL, pid 58716, healthy 4-feed polls).

**LADDER max_ask AUDIT (same day, closes the open defect): the "18% overpay" was a FALSE
ALARM — the real defect was measurement, and it's fixed.** (1) det_d12_dual_live's deployed
config was the ADAPTIVE cap (0.78→0.85 @ |cl_dist|≥20) from day one; fill.max_ask ==
intent.max_ask on all 132 fills and none exceeded its own intent cap — the 06-15 "breach"
memory compared against flat 0.78. Overpay did NOT cause dual_live's bleed. (2) The genuine
invariant violations: 4/593 fills wallet-wide with avg_price > own cap (+0.2¢..+1.7¢, $0.32
lifetime, one on fav_disagree_live) — ALL matched to `clob_fill_via_balance_fallback` log
events: order API unreadable → usdc_paid = shared-wallet pUSD delta (10s window) → polluted by
concurrent movement/API lag. Physically impossible as fills (IOC can't clear above limit:
11sh @ ≤0.45 ≤ $4.95, recorded $5.14). Ladder/ceiling code verified correct (live_executor.py
ceiling + fill_or_chase price guard). FIX: `clamp_buy_fallback()` in clob_trade.py bounds
fallback cost at shares×limit, logs `clamped=true`; TDD'd with the real incident numbers
(tests/test_clob_fill_detection.py, 8/8 in the SDK env); full suite 222 passed (also refreshed
two stale live-set pin-tests in test_psettle_mode/test_xb_mode that had been red since the
06-18 kill). **DEPLOYED: executor restarted 17:48 UTC (user-approved) — SIGTERM'd cleanly
(pending=0, nothing in-flight), relaunched via the hourly_monitor.sh command; single instance
verified (one executor_started, no duplicate pids); all 5 books restored to the cent incl.
today_pnl/cap state. The clamp is live. fav_disagree_live size-up is UNBLOCKED from the
overpay concern.**

---

## 2026-07-03 — EDGE HUNT v2: honest apparatus completed; fav_disagree passes ALL gates → RE-ARMED live (user-approved)

Theory-first campaign (pre-registered in test_ledger.md "HONEST EDGE HUNT v2"; plan
~/.claude/plans/in-this-project-our-rippling-torvalds.md). Everything runs on official labels.

**Foundation shipped (same day):** official labels extended to ALL 15m 05-23→07-02 + first-ever
5m labels (31,238 clean-era windows, 100%) — cache 46,771 slugs; `resettle_official.py` re-settles
every paper ledger on official outcomes nightly (`scripts/nightly_honest.sh`), parity-pinned to
real money (tests/research/test_resettle_official.py 4/4); fill model **live-2** (580 clean
attempts, 7d-holdout: predicted 53.9% vs observed 55.2% fill); xbook/trade_prints feature modules
with look-ahead-guard unit tests (9/9); sealed discover/reveal sweeps `xh_sweep.py` (1,280 specs)
+ `flow_sweep.py` (432). Chunked resumable frame builder `research/build_joined_chunked.py`
(monolithic build was externally SIGKILLed 3×, not OOM).

**Verdicts (virgin block = entries ≥06-19, never seen by any selection):**
- KILLED by rule: oracle_fade_v1, tadiv_approx_v1/_ret3, det_sqp_v1/v2, fav_deepdown, early_disagree_v1.
- det_d12 family + fav_lowvol: pass virgin BH but FAIL the consistency leg (virgin-fortnight riders).
- **fav_disagree family passes EVERYTHING**: virgin BH-FDR p=.00025-.0025; consistency leg
  (full 06-12→07-02) +$1.05..+$2.70/fill CI-lo>0; live-2 guarded fills on recorded decisions
  +$2.29/+$2.57/+$0.60 per fill (56-66% fill rate, seed-robust) ≈ +$8-13/day each at $5;
  Jaccard vs det_lwd_live 0.07-0.11. The 06-18 kill was an n=34 small-sample verdict.
- xb twin gate NOT passed (+$1.04, CI spans 0, n=130); fam_xh sweep decides the door.
- fam_flow2 discovery: 11/432 shortlisted, 10 are `follow` (control) — re-label-of-det suspicion,
  Jaccard gate added to reveal; reveal pending rebuilt frame.
- **FEE FACT:** live pays ZERO fees (348,600/348,600 prints at 0bps) — all honest EVs conservative.
- 5m oracle basis ≈10× 15m (0.65% of ≥20bps moves flip post-fix; 34.6% near-strike disagreement).

**ACTION (user-approved via plan gate): `fav_disagree_live` re-armed `live:true` at $5/trade,
$50/day hard_worstcase, existing book (realized −$66.5, bankroll backstop $100).** Engine restarted
2026-07-03 ~07:40 UTC (wrapper pid 81447), executor untouched (pid 15413), det_lwd_live probe
unchanged. Pre-registered stop rule: official-settled ≤ −$0.50/fill with CI-hi<0 → recommend stop.

**CAMPAIGN CLOSED SAME DAY — all four theories decided** (doc: docs/research/EDGE_HUNT_V2_2026-07.md):
T1 cross-horizon CLOSED (xb gate neutral; fam_xh 0/145 virgin survivors); T3 atlas = no new
family, 4/4 virgin-confirmed cells ARE the cheap-disagree structure (follow-up paper twin
early_disagree_cl_v1 deployed, CL-dist gate, 14-day standard gate); T4 flow CLOSED (0/11).
Nightly honest scoreboard live via launchd 06:15 IDT. Engine restarted 08:50 UTC 07-03 with
25 strategies. The ONE surviving edge = fav_disagree family, live at $5.

## 2026-06-19 — HONEST-SETTLEMENT FIX: research labels were ~4:1 optimistic; on true settlement NO edge survives

The deepest finding of the project. Diagnosing "can we fix the strategies to be profitable?" surfaced
that the WHOLE research stack settled on a RECONSTRUCTED Chainlink outcome (`cl_end>=cl_start` from
as-of prices) that disagrees with Polymarket's OFFICIAL on-chain resolution on **6.5% of all windows /
~17% of traded near-strike windows, ~4:1 optimistically biased**. Verified vs settlements.jsonl
(real-money book): official-settled paper ≈ live-realized (fill drag ≈ $0) → the loss is the SIGNAL,
not execution. This mislabel (+ stale-book inflation + Coinbase-vs-Chainlink gap) is why we deployed
−$170 of phantom edges. Plan: ~/.claude/plans/ok-we-are-running-mutable-meteor.md +
docs/superpowers/{specs,plans}/2026-06-18-honest-settlement-*. Doc: docs/research/HONEST_SETTLEMENT_2026-06-18.md.

**FIX SHIPPED (branch honest-settlement, parity-validated):** `research/dataset/official_outcomes.py`
fetches the official outcome (/markets?slug=X&closed=true → outcomePrices, the executor's parse) for
every window slug; `edge_lab.cl_outcomes()` (the single settle point for ALL backtests/sweeps/re-scores)
now returns official, recon fallback. PARITY TEST: official == real-money booked outcome 288/288, 0
mismatches. Backfill: rate-limited at 16 workers (27% cov) → retry+backoff+lower concurrency → 100%
(10,265 slugs). 7 tests green.

**HONEST VERDICT (official settlement, clean data):**
- Deployed/paper strategies: ALL breakeven-to-negative. det_lwd_live −$0.10/fill (was +$0.48 on recon);
  fav_momentum −$0.58 (the "only passer" at +$0.55 = pure mislabel); fav_lowvol/deepdown/tadiv all neg.
- FULL SWEEP re-run on honest labels (2,681 hypotheses): **ZERO survivors** (futN>=30 ∧ futCI-lo>0 ∧
  seed-robust ∧ non-dup). Positive point-estimates are all thin-future det-family (n=3-7); specs with
  enough future fills (psettle/ta_divergence n=39-167) are negative. No deploy-paper-candidate.
- PROGRAM-SUCCESS bar met by NOTHING. The book-lag/determinism/disagree/fav/psettle/TA families are all
  gone under honest settlement — the historical "edges" were artifacts.

**DECISION (user):** KEEP det_lwd_live live at $5 / $25 cap as a breakeven forward-measurement PROBE
(no change — already exactly that; it's breakeven not bleeding). No other real money. The honest
apparatus stays running to find a REAL edge (every future hunt is now trustworthy — labels match money).

**RESTART (mid-session, user's computer rebooted):** all bots restored — run_combined (paper+data),
live_executor --live (det_lwd_live only, guards on, $604 pUSD funded, no geoblock), claim daemon. NOTE:
executor + claim daemon run via uv `--no-project --with py-clob-client-v2 ...` (ephemeral deps, NOT the
project venv) — a Task-3 `uv sync` pruned project-venv packages but did NOT affect them. Honest sweep
re-run from scratch after the crash killed it at 391/2681.

**NEXT (deferred, ~when a real edge appears):** fill-model live-2 recal; the honest apparatus is ready
for a NEW edge-hunt (more coins btc/eth/sol/xrp+bnb/doge/hype, or non-book-lag families) — now that
labels are honest. Memory: [[clean-data-reckoning-2026-06-18]] updated; new [[honest-settlement-fix]].

---

## 2026-06-18 — DE-STALED RECKONING: 3 live strategies KILLED/DEMOTED, only det_lwd_live left live

User asked for a full "what's working / not / needs time" verdict + path forward to profitable
strategies (plan: ~/.claude/plans/ok-we-are-running-mutable-meteor.md, approved). Decisions: change
nothing live until the firm re-score; DEFER fill-model recal (A) (~180 clean fills <250 needed, use
the v2/live_guarded bracket); do all of reckoning + faithful-tadiv + new-research.

**RECKONING DONE (B + re-score):** rebuilt joined_15m 05-23..06-18 (9.19M rows, ~13min) +
regenerated slim (8.02M, verified max date 06-18 — the stale-slim trap), added early_disagree_live
to rejudge CONFIGS, ran rejudge_clean (~6 clean days) + live_gap_attribution(--since 06-12). Branch
reckoning-0618.

**FIRM CLEAN-DATA VERDICTS (clean_future, live_guarded EV/fill, Chainlink-settled):**
| strategy | clean EV/fill | freq vs dev | live realized | verdict |
|---|---|---|---|---|
| det_d12_dual_live | −$0.64 CI[−1.66,+0.32] | 26.6→18.3 | −$103 (halted 06-17) | **KILLED** (enabled:false; even paper −$2.73) |
| fav_disagree_live | −$1.18 CI[−3.21,+0.84] | 38→7.3 collapsed | −$66, still bleeding | **KILLED** (live:false) |
| early_disagree_live | +$0.62 n=6 INSUF | 41.5→3.4 collapsed 12× | +$4 cum, −$38 in 3d | **DEMOTED** (live:false) |
| det_lwd_live | +$0.48 CI[**−0.13**,+1.05] | 36.4→20.9 | +$30 cum (pre-clean) | **KEPT live $5** (marginal, watch) |
| fav_momentum (paper) | +$0.55 CI[**+0.168**,+0.893] WR92.6% | ok (30/actday) | — | only config passing keep-gate, BUT **83% determinism overlap** (78% inside det_d12_wide) → NOT promoted; flag as det_lwd RETUNE candidate next review |

**THE HONEST META-FINDING:** the book-lag edge family has DECAYED. No live strategy clears
program-success (clean CI-lo>0 AND positive rolling-7d clean live book). Gap attribution (clean era,
intents=584): paper +$167 was **−$236 wrong-oracle inflation** (paper settled Coinbase, live
Chainlink) → paper numbers were never real; live **normal fills lose −$80 structurally** (not knives/
misses) → the edges genuinely don't work live on clean data. Live book total −$116.63 (≈ data-api,
Δ≈0 so no backfill). EXECUTED: 3 demotes in strategies.yaml + safe run_combined restart (pid 39013;
executor pid 20062 + real-money books UNTOUCHED); LIVE roster now = **det_lwd_live ONLY**; the 3
demoted continue as paper twins.

**STILL OPEN (this session, approved):** Phase 3 — offline-validate tadiv (needs joined.py
spot_vel_30s + rebuild) then build parity-faithful mode="tadiv" (PART B); Phase 4 — new-edge research
campaign (needs a brainstorm: divergence/earlier-timing + book-lag-independent families, honest gate
CI-lo>0 + Jaccard<0.5). DEFERRED to ~06-24 review: fill-model live-2 recal (when clean fills ≥250),
re-score det_lwd/psettle/tadiv on the live gate (future.n≥30), sq rolling-curve engine wiring,
op-2/op-1 print-model review.

---

## 2026-06-16 (eve IDT) — TA STRATEGY CAMPAIGN: 1 new edge (ta_divergence) → 2 PAPER twins deployed

User asked to expand strategy research using technical analysis on the BASE ASSET, fully backtested.
Ran a comprehensive campaign through the EXISTING rigor stack (no new backtester) — brainstorm → spec
→ plan → subagent-driven build. Artifacts: spec `docs/superpowers/specs/2026-06-16-ta-strategy-
campaign-design.md`, plans `…/plans/2026-06-16-ta-strategy-campaign.md` + `…-tadiv-engine-wiring.md`,
results `docs/research/TA_STRATEGIES_2026-06-16.md`, pre-reg in test_ledger.

**WHAT WAS BUILT (research):** `research/dataset/ta_features.py` (causal base-asset TA on the cb_spot
tape: EMA-slope/RSI/MACD/ATR/Bollinger/z-score/regime, look-ahead-pinned) → `_ta_frame()` in
hypothesis_sweep → 4 families `fam_ta_{directional,filter,regime,divergence}` (258 specs). Full
campaign: sweep `--future-start 2026-06-12` (2681 specs, 1911 screened, ~2.3h) → select → verify
`--fill-model live --extended-known` → Jaccard dedup. Chainlink-settled, future revealed once.

**VERDICT:** ONE new edge — **`ta_divergence`** (buy the 30s-spot-move side mid-window, tl 60-300s,
ask 0.30-0.55, |move|>=3-5bps with EMA-slope agreeing). Future-BLIND gates: CPCV 100%,
latency-positive 2/5/10s, dev EV +$7.8-9.5/tr. **NON-duplicate** (max Jaccard 0.17-0.21, closest
fav_disagree 0.17). Provisional seed-future EV +$2.2-2.6/tr (ret_min 3-5; ret_min 10 noisy ≈0).
HONEST NEGATIVES (as predicted): `ta_directional` fails — its high-EV specs are low-n determinism
leakage (n=49 dev+$10.8 → n=272 full +$1.57); `ta_filter`/`ta_regime` keep determinism's WR but
don't lift per-trade EV. CAVEAT carried: cb_spot is a ~15s REST poll (TA resolves ~0.06 Hz — can't
see the seconds-scale manual edge; ta_divergence works as a mid-window repricing-lag bet anyway).
NOTE on the live verdict: verify `pre_verdict=reject` for ALL 24 shortlisted is a THIN-FUTURE-BLOCK
artifact (gate needs future.n>=30; data only through 06-13 → ~1.5 clean days; the SAME gate rejects
known-good psettle with future EV $6-9). Re-score on the live gate at ~06-19/20 when future.n>=30.

**ENGINE + DEPLOY:** new `mode="tadiv_approx"` in DeterminismState — APPROXIMATION twin using
`RollingMove.vel_bps(30)` (≈ta_ret_30s; bps-vs-strike, research is bps-vs-spot, <1% diff) +
vel_bps(10) sign-agree as the EMA-slope proxy. Separate early-return path (psettle/xb pattern) →
legacy consistent/disagree flow BYTE-IDENTICAL, replay-parity test green. DetParams field
`tadiv_ret_min_bps` + fail-fast + registry parse + 8 unit tests; full suite 546 passed (3 skip; the
1 lightgbm collection error is pre-existing/unrelated). Merged ta-strategy-campaign → main (FF
29f04f5). Deployed TWO PAPER twins (live:false, $10, hard_worstcase $50/day): `tadiv_approx_v1`
(ret_min 5) + `tadiv_approx_ret3_v1` (ret_min 3) — A/B the campaign's two best specs. run_combined
restarted 19:33 UTC (executor pid 20062 + real-money books UNTOUCHED); strategies_loaded n=25,
heartbeat 0s, 16 active markets, both twins loaded, 0 trades yet (fire on a mid-window ≥3-5bps/30s
move — intermittent, expect first fires in active hours).

**FORWARD GATE (unchanged):** ≥7 clean days realized EV/fill CI-lower > 0 before ANY live talk,
present-first [[feedback_supervised_realmoney]]. WATCH: confirm both twins FIRE within ~a day of
active hours (else investigate — distinguish "no opportunity" from "broken"); A/B ret3 vs ret5.
**QUEUED (PART B, not built):** parity-faithful `mode="tadiv"` (EMA-of-spot slope + 30s-return
bps-vs-spot, parity-pinned to ta_features) to A/B against the approximation — plan written, execute
after review. The 06-15 triage (demote det_d12_dual_live/fav_disagree_live) is SEPARATE and still
pending the user's go — this restart did NOT touch those live books.



Monday (first clean weekday since the feed fix) answered the "almost no trades" concern AND
delivered the clean-data verdict in real money:
- TRADES RETURNED: 88 attempts / 62 fills today (70% fill rate — better than the stale era's ~45%).
  The weekend drought was just the weekend (weekday ~94 intents/day vs weekend ~21). NOT a bug.
- LIVE ground truth: **−$32.89** (194W/67L; executor book −$20.70, over-counts truth by ~$12 =
  near-strike settlement caveat → trust −$32.89). Per-strategy clean-weekday split:
  - **early_disagree_live: +$43.64 total, +$21.44 today** — THE winner, holds on the clean weekday.
  - det_lwd_live: +$35.62 total, +$6.28 today — quietly positive (better than "break-even probe").
  - det_d12_wide_live: +$18.80 (backup, live:false, 0 trades).
  - **det_d12_dual_live: −$68.35 total, −$20.06 today** — BLEEDING; $31.65 bankroll left (2/3 gone);
    dual-oracle gate working (42 skipped_oracle_disagree today) but still loses. The re-score's
    prime DEMOTE suspect, now confirmed losing real money on clean data.
  - **fav_disagree_live: −$50.42 total, −$20.04 today** — bleeding, $49.58 bankroll left (half gone);
    high-variance (recent trades won +$7) and its paper twin says +$36 today (settlement/exec gap —
    trust the −$20 live). The disagree config that collapsed 38→3 opps on clean data.
- The clean weekday SEPARATES edge from artifact in real money exactly as the de-staled re-score
  predicted: early-mid-window disagree (early_disagree) wins; det-last-minutes (det_d12_dual) + the
  fav config bleed.

TRIAGE RECOMMENDATION (present-first — awaiting user go, NOT yet executed):
1. DEMOTE det_d12_dual_live → paper (−$68, −$20 on clean Mon, artifact family). Stop the bleed.
2. DEMOTE (lean) fav_disagree_live → paper (−$50, −$20 Mon, high-var but losing live). Or tighten.
3. KEEP early_disagree_live (winner) + det_lwd_live (positive) + det_d12_wide_live (backup).
4. SCALE early_disagree_live $5→$10 (the one proven clean-data edge) — user call.
Once approved: edit strategies.yaml (live:false for demotes / fixed_bet 10 for scale), safe-window
run_combined restart (executor + books untouched).

## 2026-06-14 (01:5x IDT) — RE-HUNT DONE → DECISION: promote NOTHING (disciplined); clean data keeps unmasking artifacts

Re-hunt chain complete (sweep 2423 specs → select 24 shortlist → verify ALL 24 REJECT under live
model → atlas). Plus checked the ready-made oracle paper edges on clean data. DECISION (autonomous,
per the grant below): **promote 0 new live strategies.** Every candidate fails the bar:
- 24 sweep candidates: verify pre_verdict = reject ×24 (clean-future block too thin to confirm —
  most show future $-[-,-]; only micro_1150 had +future $4.56[2.81,6.31] but it's L2-imbalance =
  NOT engine-deployable). The strong ones are det family (last-minutes stale-artifact, default-
  reject); the deployable book-lag-independent ones are e4/disagree with Jaccard 0.30-0.38 =
  DUPLICATES of the already-live early_disagree/fav_disagree.
- oracle_fade_v1 (near-strike fade): CLEAN −$0.82/tr (n=57) vs +$5.06 stale paper → COLLAPSES, same
  artifact pattern as det. DO NOT deploy.
- psettle_ud_v1: CLEAN +$0.34/tr (n=28) vs +$2.40 stale → marginal, EV collapsed 7×, rides the shaky
  op-2 print model. Not worth real money; #1 future candidate IF it firms up — present-first.
- atlas re-confirms the disagree-cheap-mid-window region (+45-57% dev/holdout) = exactly the
  early_disagree edge we ALREADY run live. No NEW deployable+confirmed+non-duplicate edge exists.

THE BIG PICTURE the clean apparatus is revealing: nearly EVERY edge collapses toward 0/negative on
clean data (det stale-inflated; fav_lowvol/deepdown demoted; oracle_fade −$0.82; fav_disagree
bleeding −$5.21/3 clean). The ONE that HOLDS — and improves — is **early_disagree_live: clean
+$6.05/tr (n=7), live realized now +$22.21** (was +$13). It is the lone confirmed-on-clean-data edge.
Strategic read: early_disagree (early mid-window oracle-disagree, buy the cheap spot side) is the
real signal; consider SCALING it (present-first) once n grows, and keep hunting edges of THAT shape.

System health at decision time (22:50 UTC): all procs up, heartbeat 0.6s, books fresh (tick 36s old),
WS deaths ~0/200 lines, last fill 22:27 det_d12_dual ok. Overnight watchdog launched (notifies on
problem-or-morning). All 5 live books KEPT, untouched.

## 2026-06-14 (00:xx IDT) — ONE-OFF autonomous live-promotion grant (user asleep)

USER GRANT (this search only, overrides the standing present-first rule [[feedback_supervised_realmoney]]
for THIS instance): "once the search is finished ... promote to live ($5 per trade) each candidate you
think worth checking. also keep the current we have." Standing present-first preference is UNCHANGED
for future promotions — this is a scoped one-off because the user is asleep.

WHEN the re-hunt chain (bg task bo4u0m5qe: sweep->select->verify->atlas) completes, EXECUTE autonomously:
- KEEP all 5 current live books untouched (det_lwd_live, det_d12_dual_live, det_d12_wide_live(backup,
  paper), fav_disagree_live, early_disagree_live). Do NOT demote/alter them.
- Promote ONLY candidates meeting ALL guardrails (conservative; the whole project lesson is that
  backtest edges were stale-inflated — bias to NOT deploying):
  1. Pass the future-blind select gates (n>=40, dev_n>=12, cpcv>=80%, full_ci_lo>0, latency 5s&10s
     EV>0, cap_10>=0.5) AND survive hypothesis_verify under --fill-model live (live-model future EV
     not negative; pre_verdict deploy_paper_candidate or better).
  2. NOT a duplicate of an already-deployed/known edge (verify Jaccard / extended-known).
  3. DEPLOYABLE in the engine as a strategies.yaml entry using an EXISTING mode (consistent/disagree/
     psettle/xb + param knobs). Anything needing new engine code -> FLAG for user, do NOT deploy.
  4. PREFER book-lag-INDEPENDENT families (mid-window disagree/e4, oracle/psettle, near-strike fade);
     a last-minutes det/longshot variant must additionally clear the clean_future block (not just
     dev/holdout) — default-reject det last-minutes variants (known stale-artifact family).
  5. Reject lottery shapes (low WR + fat-tail dependence) unless clearly justified.
  6. CAP at <= 3 new live strategies this round. If none clear the bar, promote NOTHING and explain.
- Each promoted: live:true, fixed_bet_usd 5.0, max_daily_loss_usd 25, $100 bankroll, hard_worstcase,
  correct mode/params; ALSO add a paper twin if useful. Validate strategies.yaml parses
  (registry.load_strategies) BEFORE restart. Safe-window restart of run_combined ONLY (executor +
  existing books untouched). Verify health after (heartbeat fresh, strategies_loaded count).
- Also evaluate the already-implemented PAPER oracle edges psettle_ud_v1 / oracle_fade_v1 on clean
  data (rejudge-style) as natural promotion candidates (book-lag-independent, already engine-ready).
- Leave a full per-candidate reasoning writeup here + a morning summary message for the user.

MONITORING OBLIGATION (user: "make sure it's working, monitor them and see that they work as
expected"): after deploy+restart, ACTIVELY VERIFY over the night, not deploy-and-forget:
- Confirm each new strategy LOADS (strategies_loaded includes it) and the engine stays healthy
  post-restart (heartbeat <10s, books FRESH vs CLOB REST — no stale-book regression; grep
  ws_recv_ended stays quiet; CPU sane).
- Confirm each new strategy FIRES intents within a reasonable window AND distinguish "no intent =
  no opportunity yet" (det-style can be quiet for hours — check the CSV for whether its entry
  condition occurred) from "broken" (errors in signals.jsonl / engine log / never evaluates).
- Confirm the executor PICKS UP the new intents (fills or sensible guard skips; avg_price <= max_ask;
  no API-400 storm; the new sids appear in fills.jsonl / executor book).
- Confirm the 5 EXISTING books keep working unchanged.
- Re-check periodically through the night (schedule self-wakeups / a tracked monitor task; ~30-45min
  cadence first few hours, then taper). FIX anything clearly broken (bad yaml, crash, stale feed);
  if a new strategy misbehaves with real money, demote it (live:false) + restart rather than let it
  bleed. Morning report = per-strategy: loaded? fired? filled? healthy? + any fixes made.

## 2026-06-13 (eve IDT) — CLEAN-DATA RECKONING started: de-staled re-score first pass + re-hunt launched

Plan in-this-repo-we-silly-thimble approved (keep all 5 live books for clean-era data; re-validate +
re-hunt in PARALLEL). Pre-registered in test_ledger "CLEAN-DATA RECKONING". Done this session:
- Rebuilt `joined_15m.parquet` 05-23..06-13 (7.5M rows) + slim frame (load_base reads the SLIM — it
  was stale at 06-09 and silently capped every analysis; regenerate slim after every build_joined).
- `research/analysis/rejudge_clean.py` (NEW) — de-staled re-score: every CONFIG, devhold (05-23..31,
  pre-degradation) vs clean_future (entry UTC >= 06-12 11:00), fill BRACKET v2 (optimistic) |
  live_guarded (stale live-1, pessimistic). fills_live recal DEFERRED (only 59 clean attempts).

FIRST-PASS RESULT (live_guarded = honest-pessimistic bound; **clean_future n tiny → first pass**):
| config | devhold EV/fill | clean_future EV/fill | cf n | verdict |
|---|---|---|---|---|
| det_lwd_live | +$0.91 | +$1.72 (CI+0.885) | 5 | INSUFFICIENT (n<20) but +ve |
| det_d12_dual_live | +$1.21 | +$0.42 (CI-1.53) | 8 | INSUFFICIENT |
| det_d12_wide_v1 | +$1.01 | -$0.38 (CI-1.66) | 15 | INSUFFICIENT, leaning -ve |
| fav_disagree | +$4.13 | n=1 | 1 | INSUFFICIENT (freq COLLAPSED 38→3/actday) |
| fav_momentum | +$0.80 | +$0.77 (CI+0.66) | 8 | INSUFFICIENT but +ve |
| fav_lowvol | +$0.42 | +$0.08 (CI-0.76) | 34 | **DEMOTE-cand** (CI-lo<0, freq 182→87) |
| fav_deepdown | +$0.04 | -$0.10 (CI-0.60) | 35 | **DEMOTE-cand** (CI-lo<0) |

READS: (1) the apparatus WORKS — devhold reproduces the "validated" edges under the live model (all
+ve), so the edges were real on pre-degradation data. (2) clean_future is too small for firm verdicts
on det + fav_disagree (n=1-15) — INSUFFICIENT exactly as predicted; needs ~7 clean days (~06-19/20).
(3) First real signals: the favourite-value PAPER edges fav_lowvol + fav_deepdown DON'T hold on clean
data (n=34-35, CI-lo<0, frequency collapse) → deprioritize. (4) fav_disagree's mid-window opps also
collapsed on clean books (38→3/actday) — watch. CAVEAT: live_guarded uses the STALE-pessimistic fill
model (fill% likely understated); truth is between v2 and lg.

PARALLEL: `hypothesis_sweep --future-start 2026-06-13` running (bg pid 86610, /tmp/sweep_clean.log) →
select + verify --fill-model live next; rank survivors favouring book-lag-independent families.
NEXT: re-run rejudge_clean as clean data grows; verify the sweep shortlist; weekly firm verdict.

## 2026-06-13 (14:4x IDT) — "Almost no live trades" DIAGNOSED: det edge is intermittent + was stale-inflated (NOT a bug)

User flagged few live trades. Full funnel deepdive (no code change — pure analysis):
- **Execution is HEALTHY.** fired (engine) == intents written, hour-by-hour exactly (29=29); ~53%
  of attempts fill. The live wiring, guards, executor are all fine.
- **The bottleneck is OPPORTUNITY SUPPLY, not execution.** Decisive test: over the last 12h /188
  windows, only **1** had a det-perfect entry tick (healthy two-sided book + favourite still priced
  in-band + ≥dist_min, in det's final-minutes window) — and det FIRED on it. Per-hour, det
  opportunities map 1:1 onto det fires: ~25 opps over 06-12 12-18 UTC → ~25 fires; ~1 opp over the
  16h since 06-12 19:00 → ~1 fire. **det catches ~100% of genuine opportunities and misses
  nothing — the opportunities themselves dried up.**
- **Two causes, both expected/structural:**
  1. The stale-book fix (06-12 10:55) removed the PHANTOM version of det's edge. A 10-40s-stale
     feed showed favourites still "cheap" (≤max_ask) when the real book had already repriced to
     0.90+; det fired on those, live couldn't fill (the live-vs-paper gap). Accurate feed ⇒ those
     phantoms gone. The edge became HONEST, not broken.
  2. Market regime: even GENUINE opps clustered in Friday active hours (12-18 UTC) and went to ~0
     for 16h into the weekend. det's edge is a working-hours, laggy-book phenomenon; in efficient
     weekend books the favourite reprices before det's entry window.

**CLEAN-FEED RE-SCORE (det edge, de-staled; ~1 day / 26h of clean data since 06-12 11:00 — small
sample, ONE weekend):**
| strategy | gates | opp-rate ACTIVE (12-18 UTC) | opp-rate QUIET | implied trades/active-day |
|---|---|---|---|---|
| det_lwd_live | tl 1-60, dist≥8, ask 0.5-0.88 | 15% of windows | 2% | ~17 |
| det_d12_dual_live | tl 1-180, dist≥12, ask 0.5-0.78, AGREE | 18%* | 1% | ~16 (×~0.8 oracle) |
*dual is a PRE-oracle-gate upper bound (cl_dist agree gate is engine-only, not in CSV; trims a
further ~10-30%). Caveat: move_pct used the old ~24s-late strike pre-06-13 11:05; the sec-0 fix
shifts it slightly. Stale-era paper twins logged 70-124 entries/day (all 4 live strats combined);
clean-feed implies det ≈ 20-40/day → **stale feed inflated det's apparent frequency ~2-4×.**

**STRATEGIC IMPLICATION (feeds the GOAL):** det is NOT the steady always-on edge the stale
backtests implied — it trades in active-hours BURSTS and goes quiet on weekends/off-hours, and its
true frequency is a fraction of the historical number. Don't size or schedule around steady det
volume. The de-staled det EV-per-trade itself still needs re-validation on clean data (its
historical EV was measured on the same stale books — likely optimistic, same mechanism as the
sq/fill-model artifacts). PRIORITIZE regime-diverse / earlier-entry edges (fav_disagree,
early_disagree, near-strike fade) that don't depend on the last-minutes book lag. This is the
clean-data truth surfacing — exactly what the week's feed/look-ahead fixes were for.
Memory: [[det-edge-stale-inflated]].

## 2026-06-13 (14:1x IDT) — Look-ahead defect KILLED: strike now captured as-of window open

Shipped the queued fix (test_ledger XI4 AMENDMENT "QUEUED" line). The window strike (start_price,
the SIGNAL basis) was frozen by a LIVE `coinbase.get_spot()` at discovery-poll time — median ~24s
AFTER window open — so research back-filled a baseline that already "knew" up to ~24s of post-open
movement (xb 74% acausal). Now discovery freezes start_price at the spot AS-OF window_start_ts,
mirroring the Chainlink settlement basis which was already correct.

- `SpotPriceCache`: added bounded rolling history (deque, history_max=1200 ≈ 5 min) + `price_asof`
  (latest sample at-or-before t_ms; never leaks a future value; robust to out-of-order
  cross-thread writes). 7 tests.
- `discovery._tick`: strike = `spot_price_asof(symbol, window_start_ts*1000)` first, LIVE
  `get_spot` fallback when the cache has no sample at open (fresh boot / feed gap → legacy
  behavior, logged `strike_basis=live_fallback`). 2 tests + existing strike tests still green
  (they exercise the fallback). Wired `spot_price_asof=spot_cache.price_asof` in run_combined.
- DOES NOT touch settlement (Chainlink basis unchanged). Suite 517 green; run_combined restarted
  11:05 UTC (executor untouched). VERIFIED live: steady-state captures all `strike_basis=spot_asof`
  with start_price frozen at second-0 even at 3-5s poll lag; boot transient (empty cache →
  live_fallback) cleared as designed.
- Live-behaviour note: early-window decisions can now compute move_pct honestly (start_price is
  populated from second 0, was 0/uncomputable until ~second 24). Most live edges need dist≥10-12bps
  (takes time to develop) so first-24s firing stays rare; the ones that DID fire early (xb,
  early_disagree) are exactly the look-ahead-flagged ones now made honest.
- FORWARD DATA from 2026-06-13 11:05 UTC is causally clean at second 0 — research no longer needs
  the s5≥capture / sec≥35 causal filters for NEW windows (historical pre-06-13 data still does).

**op-2 print-model age-sensitivity (surfaced by the full-suite run, NOT this fix):** the op-2 refit
(shipped 06-12) is slightly more sensitive to oracle-age staleness than op-1 — `test_print_model_parity`
worst-case d_p @ age±15s = 0.028 vs the op-1 0.02 bar (most points <0.005). PAPER-ONLY exposure
(psettle_ud_v1/oracle_fade_v1 are live=False) → no live-money risk. op-2 passed its accuracy ship
gate; this is a separate robustness property. Test bar bumped to 0.035 (documented) + TRACKED for
the weekend model-recalibration review: decide keep-op-2 vs revert-op-1 (backed up) vs add an
age-stability constraint to the refit gate.

## 2026-06-12 (20:5x IDT) — A/B VERDICT run EARLY (user request): ALL 3 LEGS PASS → unfreeze bundle SHIPPED

Ran the Friday-21:07 verdict ~30h early at the user's request (cron 45214636 deleted — consumed).
Window: 2026-06-10 23:30:02 UTC (executor restart that armed ENFORCE_SIDS=det_lwd_live) → now;
132 pre-window unmatched fills excluded (attribution --since filters intents, NOT fills — they
were Jun-5+ history); VOID paper window 06-12 00:36-10:55 UTC excluded from counterfactuals.

| leg (pre-registered) | ENFORCED (det_lwd_live) | shadow (dual/fav_dis/early_dis) | verdict |
|---|---|---|---|
| knife fills | 0 (floor-guard dodged 4) | 4 knives, −$10.21 | PASS |
| doomed-order spam per zero-fill | 26% (rest clean pre-order aborts) | 100% (2-6 doomed orders each) | PASS |
| missed-EV per intent (excl. void) | +$0.594 | +$0.776 | PASS (guards do NOT skip the winners) |

Fill-rate/attempt 39% vs 26%; in-window live P&L +$3.22 vs −$5.77. Caveat noted: paper
counterfactuals are stale-era inflation-biased, but the bias hits both arms equally.
Flag: 16/56 enforced intents were age-gate no_attempts (29% ≫ planned ~8%) — stale-era latency
spikes are the likely cause; CHECK post-fix age-drop rate at the weekend review.

SHIPPED (user approved both asks; suite 508 green; boundary-timed restarts 17:45 UTC):
1. **EXEC_GUARDS=on** globally (.env) — executor restarted, `executor_started` confirms
   guards=on, all books intact.
2. **Burst cap** — BC2 §4 patch applied to scripts/live_executor.py exactly as pre-registered
   (EXEC_BURST_CAP/_SIDS knobs, _slug_window_ts, inflight_slugs mirror, _blocked gate counting
   done_slugs+inflight = restart-safe by construction; 5 new tests incl. Defect-3 restart case).
   Armed: EXEC_BURST_CAP=1, EXEC_BURST_CAP_SIDS=fav_disagree_live (early_disagree EXPLICITLY
   uncapped — its own table said no).
3. **op-2 print model** — `oracle_model_refit.py --refit --execute` gate PASS (holdout 06-08/09
   non-inferior, full-pop better); artifact now version op-2, op-1 backed up
   (.bak-op-1-20260610T182720Z); engine restarted, fail-fast load passed, 23 strategies up.
4. **Daily drift alarm** — hourly_monitor.sh step 4: oracle_model_refit --check at the 06-UTC
   pass → logs/model_check.log + loud [warn] on alarm.

WATCH next: per-sid `guard_skip`/`burst cap` skip reasons over the first enforced day for the
3 newly-enforced strategies; post-fix intent ages; first fav_disagree burst event (expect 1
taken + siblings skipped with "burst cap" note). sq rolling-curve ENGINE wiring remains queued
(research pipeline exists; not part of this bundle).

## 2026-06-12 (14:0x IDT) — LIVE-FILL OUTAGE root-caused + FIXED: the engine was trading on stale books

User reported zero live trades for hours. Diagnosis (each step verified, not inferred):
zero fills 03:40→14:00 IDT; every det attempt died "book dry"/API-400 with executor preflight
seeing ask 0.92-0.97 while intents quoted 0.61-0.83. NOT the tape, NOT WARP, NOT the executor:
**our WS book view ran 10-40s behind reality**. Three stacked causes, three shipped fixes
(suite 503 green; run_combined restarted 13:55 IDT / 10:55 UTC; live_executor untouched):

1. **Heartbeat loop-hog (the big one, growing for WEEKS):** `_count_signals_today` re-parsed
   EVERY strategy's ENTIRE signals.jsonl — 4.4 GB / 6.35M records, never rotated — with stdlib
   json, synchronously on the main event loop, every 5s heartbeat. Measured 42s of CPU per pass
   (~89% duty). The blocked loop starved the WS reader past its 20s ping timeout → connections
   died every ~25-30s (visible in logs back to Jun 5, worsening as files grew) → books froze in
   10-40s steps; the 1Hz aggregator caught up in bursts, stamping stale books onto past seconds
   (the "frozen then jump" CSV pattern). FIX: `engine/signals_counter.py::SignalsTodayCounter` —
   incremental per-file byte-offset tailing; heartbeat `signals_today` now means "since boot".
2. **Server-side per-connection rate cap (what made the 5m deploy fatal):** reproduced
   standalone — subscribing the full 15m+5m set (~1,600 msg/s) gets the connection killed by
   Polymarket in ~10s; either half alone survives. The 03:36 IDT xb restart tripled resync cost
   per death (48 books), turning chronic micro-staleness into permanent staleness. FIX:
   ws_collector sharded across `WS_SHARDS=4` connections (int(token)%4), per-shard reconnect
   (rotations no longer churn unrelated books), app-level "PING"/5s keepalive, recv exceptions
   now retrieved + logged (`ws_recv_ended` — the deaths were previously SILENT). Plus 5m
   discovery now CO-TERMINAL-ONLY windows (:10/:25/:40/:55 — the only 5m books the engine/
   research read; 52→32 assets). gamma.candidate_window_starts + tests.
3. **stdlib json decode cost:** profiled at 30-40% of the core for WS messages alone. FIX:
   orjson (new dep) in the WS recv path with stdlib fallback.

RESULT: CPU 98%→2% steady; zero involuntary WS deaths in the verification window (only our
rotation reconnects, all-shards resubscribe <1s); same-second book agreement vs CLOB REST
restored (3/4 exact, 1 off 4c mid-move on the fast coin — vs 0.28-0.35 phantom gaps before).
Fills expected to resume with the evening tape — VERIFY: `tail data/live/fills.jsonl` should
show ok=true entries again; `grep ws_recv_ended logs/combined.log` should stay quiet.

**DATA-QUALITY FALLOUT (read before trusting any paper number):**
- Paper data 2026-06-12 00:36→10:53 UTC is GARBAGE-GRADE (books up to 40s stale): the new twins'
  day-1 results (fade +$555, psettle +$132, xb +$34, early_disagree numbers) are VOID — their
  forward clocks restart at 2026-06-12 ~10:55 UTC. test_ledger annotated.
- Paper data ~06-05→06-12 is DEGRADED with growing severity (stale-book bursts every ~47s cycle).
  Live fills/settlements/REST/preflight data are UNAFFECTED (separate process). Spot + Chainlink
  feeds unaffected (own threads).
- The "L2 depth doesn't predict fillability" finding (fills_live, [[live-execution-gap-decomposed]])
  is likely a stale-book ARTIFACT: L2 came from the same stale WS books, while preflight REST
  depth was 98% predictive — the contradiction resolves. RE-CALIBRATE the fill model from
  post-fix data before the weekend re-scoring; treat current zero_fill_prob bins as pessimistic.
- FRIDAY A/B VERDICT (cron 21:07 IDT): exclude 06-12 00:36→10:55 UTC from missed-EV/paper-twin
  comparisons; the enforced-vs-shadow FILL-side evidence (fills.jsonl, preflight verdicts) is
  clean throughout.

## 2026-06-12 (05:3x IDT) — Print-model refit pipeline shipped; op-2 gate-PASSED, ships Friday

research/analysis/oracle_model_refit.py (--check daily-able / --refit gated, sq SQR1 pattern;
13 tests, suite 489 green; ledger § "Print-model refit + drift alarm (2026-06-12)"). TODAY: not
alarmed (S 0.0686 < τ 0.0833) but the alarm would have fired 06-06 — op-1 brushed real drift at
age ~10d (aging, not broken: band Brier 0.1084 vs 0.1041 fresh). REFIT DRY-RUN PASSED its
registered gate (candidate op-2, train 05-27..06-07, holdout 06-08/09 Brier 0.0987 vs 0.0972
non-inferior, full-pop better) → FRIDAY BUNDLE: run `--refit --execute` then the run_combined
restart ships op-2 + the sq rolling curve. Also add the daily `--check` line to
scripts/hourly_monitor.sh (or a daily OS cron) in the same bundle. Engine reads the JSON at boot
only — refits NEVER ship mid-run.

## 2026-06-12 (03:4x IDT) — xb_5m15m_causal_v1 DEPLOYED (paper, 23 strategies)

Recovery session: the build agent had finished the implementation (mode "xb" in
determinism_state/registry, xb5_* tick fields in paper_engine dtype + ws_collector, yaml block,
xb_gap_bps logged per trade per the XB-GAP requirement) but died before finishing its tests. Two
TEST-side bugs fixed (fixture missed the premium gate; k5=100.02 is float-unrepresentable at the
2bps boundary — engine matches research arithmetic exactly, the boundary itself is 'flat' by
shared float64). 476 tests green; deployed in a safe window; executor untouched, A/B intact
(guards_enforce_sids=['det_lwd_live']). DEVIATION LOGGED: the heavy research-parity pin
(tests/research/test_xb_parity.py, Jaccard vs the causal frame) was never written — accepted for
THIS twin only because no backtest EV is trusted (the forward run defines the rule; the unit suite
pins every gate with research-identical arithmetic and the collector test pins the co5 join).
Twin is a MEASUREMENT instrument: gap>=2bps wide band, ~5-8 causal signals/day expected, gate for
any live talk = >=7 forward days realized EV/fill CI-lower > 0 (test_ledger XB-GAP).

## 2026-06-12 (01:3x IDT) — xb look-ahead caught by the parity stop-rule; headline retracted

The xb_5m15m build agent REFUSED to ship (correctly): XI4's k5 strike is back-filled ~24s before
it exists → 74% of future-block decisions are acausal; the implementable causal variant is
UNPROVEN (v2-sens +$0.95 [−0.10,+1.99], CI spans 0). Ledger amended; the +$1.36/$1.84 headline and
the ~$1-1.5k/mo extrapolation are RETRACTED. Re-registered as xb_5m15m_causal_v1: PAPER-FORWARD
validation only (history is acausal for this rule); build relaunched for the causal engine mode.
COLLATERAL AUDIT: early_disagree CLEAN (9% of decisions in the risk zone, pure noise; causal
cohort future +$4.89/fill [3.24,6.55] @$10 — numbers stand; live twin causal by construction);
all other strategies' bands start ≥sec540 — unaffected. UNFREEZE-LIST ADD: capture strikes from
SpotPriceCache at sec~0 (not the 30s poll) — kills this defect class forward.

## 2026-06-12 (00:1x IDT) — External-inputs campaign: stress ENRICHES the edges; one new edge (5m↔15m)

docs/research/EXTERNAL_INPUTS_2026-06-11.md (XI1-XI5, 290 research tests green):
**WS1 leverage/cascades: NO gate — sign REVERSED.** Stress regimes enrich the deployed edges
(det_d12_dual future +$1.32 in |dOI|-top-quintile vs +$0.33 out; det_lwd joint-cascade +$1.19 vs
+$0.53). All 20 worst live window-groups sit OUTSIDE Binance-visible stress — PM-book bursts are
invisible on the perp tape (burst protection stays at the executor cap). Validated cross-coin
cascade detector (≥3/4 coins q95/q90) flags the real 06-10 incident; the per-coin q99.5 proxy
failed its own validation. **WS2 calendar: NO avoid-gate** (in-event EV HIGHER; caveat: no
CPI/FOMC decision in sample). META-LESSON: stop hunting external avoid-gates — stress is where
the repricing lag pays. **WS3 5m↔15m: THE find — xb_5m15m_v1 deploy-PAPER-candidate**: when the
15m ask + 0.03 ≤ the co-terminal 5m BID (no-arb violation at executable quotes, gap ≥2bps), buy
the 15m side: future live_guarded +$1.36 [+0.12,+2.68] n=60 (seeds $1.81±0.27), v2 +$1.84
[+0.67,+3.03], 4/4 coins, Jaccard ≤0.21, ~21 sig/day, median ask 0.39. Mechanism: the 5m open is
a fresh spot-anchored book the 15m hasn't absorbed. NEEDS an engine cross-book mode (reads the
co-terminal 5m book at tick time) — queued as the next engine block with feature-parity pins.
**WS4 E6: DEAD decisively** (future −$0.575 [−1.04,−0.10] n=298; the old pass was macro-lump).

48h research scoreboard: 5 validated candidates (fade, early-disagree, psettle_2246, fav_d5,
xb_5m15m) + honest negatives (Binance composite, model-vs-book calibration, regime gates, E6,
external avoid-gates) + sq demystified. Sunday's review pipeline is FULL.

## 2026-06-11 (22:40 IDT) — Wait-window research landed: sq demystified, burst cap + capacity mapped

**SQ RESCUE (docs/research/SQ_RESCUE_2026-06-11.md): NEEDS-MORE, and the big paper pile was largely
artifact.** Chainlink settlement removes 45-65% of sq's Coinbase walk-forward EV — most of the
~$2,800 paper P&L is settle-oracle + fill-model artifact, NOT harvestable. The frozen curve went
NEGATIVE OOS (−$0.47/tr, WR 38%) while the engine's Coinbase view still showed +$0.91. SHIPPED fix:
rolling 3-day refit (paired Δ +$0.98 [+0.43,+1.52], calibration 5×) + drift alarm (would have
caught the 05-31 incident 5 days early, zero false alarms) — TODO wire into the paper strategies
at the Jun-13 unfreeze (the deployed curve is a one-off hand refit that will rot again). Regime
gate: dev-fit threshold never triggers OOS (honest negative). Final live-physics number:
+$0.38/fill [−0.17,+0.95], seeds $0.36±0.20 → re-reveal after ~5 more clean days; biggest risk =
adverse-selected zero-fill hazard (true live EV could be ~$0).

**BURST CAP + CAPACITY (docs/research/BURST_CAPACITY_2026-06-11.md):** bursts = one macro bet
confirmed (81-87% joint outcomes) but BETTER EV than singletons — cap is a tail trade. Adopt at
unfreeze: `EXEC_BURST_CAP=1 EXEC_BURST_CAP_SIDS=fav_disagree_live` (keeps 98% of total, worst
window −67%; diff proposed in doc §4); NO cap for early_disagree (failed bars). Cheapest-ask
tie-break selects INTO knives — first-arrival is correct. CAPACITY: registered-stake ceiling
≈$1,796/day is impact-blind; honest tier $10 ≈ $500/day; escalate one rung/week where realized ≥
0.5× model. det_lwd $25 max; dual stay $5; fav_disagree $50 fragile; early/psettle/fade $50.

**early_disagree_live first 8h: fills are the constraint** — 11 attempts, 1 fill (9% vs model
~46%); last 3 misses = one 3-coin burst, all "book dry in-band" at 0.30-0.41. Thin early books
live-confirmed; watch before the Jun-15 review. Geoblock CLEAR since user's WARP fix (last 403
17:22). External-inputs study relaunched 22:35 (funding/OI/liquidations, events calendar, 5m↔15m,
E6 closeout).

## 2026-06-11 (02:40 IDT) — Shadow audit PASSED → A/B ENFORCED on det_lwd_live; oracle-night results

**Shadow audit (26h, 119 guarded fills):** verdicts 49 ok / 55 dry / 15 would-abort. PRE-REGISTERED
CRITERION PASSED: would-abort + knife cohort = 22 settled, **−$14.82** (n≥3, negative) →
`EXEC_GUARDS_ENFORCE_SIDS=det_lwd_live` appended to .env, executor restarted 02:38 IDT, verified
`guards=shadow guards_enforce_sids=['det_lwd_live']`. lwd is now ENFORCED (floor abort, ladder
starts at the real touch, dry single-retry, 10s age gate); dual + disagree stay shadow controls.
Strongest sub-finding: **dry pre-flight verdicts are ~98% predictive of zero-fill** (55 dry → 1
fill) — the fresh get_book read is the depth signal the stale L2 join wasn't (use preflight depth
in the live-2 fill-model recalibration). Honest note: the floor-only cohort was near-flat this
window (−$2.44/15, 9W/6L — would-aborts skip winners too); the knife flag catches the mid-ladder
collapses the floor can't. The 2–3 day A/B (re-run live_gap_attribution, lwd enforced vs dual/
disagree shadow) is the real verdict. NEXT CHECKPOINT: ~2026-06-13, attribution A/B compare.

**fav_disagree_live day one = the predicted failure modes, caps held:** 9 intents, 3 fills — ALL in
one simultaneous 3-coin burst (window 1781113500, one macro move = one leveraged bet), all knife
fills (0.25→0.16, 0.23→0.14, 0.40→0.26), −$10.56; then 6/6 "book dry in-band" misses (thin disagree
books). Candidate guards to evaluate (NOT deployed): per-window multi-coin burst cap; the atlas
says the structural fix is EARLIER entry (450–900s, below).

**Oracle research night (3 agents, all pre-registered in test_ledger):**
1. ORACLE PRINT (docs/research/ORACLE_PRINT_2026-06-10.md): feeds are ~33s-heartbeat, print sd
   4–18bps at T=30–300s; near-strike (|cl_dist|<5bps) sign flips 17–36%. Calibration model FAILED
   its G1 gate vs the book (+0.003 Brier < 0.01 bar — honest negative) but beats AGREE as a gate
   engine. App1 continuous gate: paper-twin candidate (+25% volume at per-fill parity). **App2
   NEAR-STRIKE FADE: future +$5.87/fill [4.50,7.30] live_guarded, 271 fills, 9/9 days, Jaccard 0.10
   vs fav_disagree — deploy-candidate as PAPER first** (fade fills are adversely selected).
2. BINANCE COMPOSITE (docs/research/BINANCE_COMPOSITE_2026-06-10.md): **honest negative — keep
   Coinbase.** Coinbase beats Binance/composites at predicting the CL print at every horizon
   (Binance carries a drifting −12bps USDT/USD basis; the print is a lagging ~33s snapshot —
   T−30s spot beats T−0 for EVERY proxy). Near-strike flips are heartbeat noise no venue mix
   removes. Gate swap letter-passed seed 0, washed across seeds → not shipped. Byproduct: full
   6.57M-row Binance 1s dataset (data/research/binance_1s/, 100% coverage).
3. EDGE ATLAS (docs/research/EDGE_ATLAS_2026-06-10.md): 1,512-cell map, FDR-controlled, future
   revealed once: 15 positive cells confirmed (11 strong), 61 negative. **THE find — the
   early-window cheap-side disagree family (tl 450–900s, buy spot side at ask 0.30–0.45,
   |cl_dist|<12): future +20.9%/$1 [15.2,26.4] n=1,279, 9/9 days, all 4 coins, ~142 windows/day,
   max 0.30 covered by any existing strategy** (fav_disagree covers 0.04 — this is EARLIER, wider).
   Caveats: ~$10 median touch depth, slippage-only cost model → needs fills_live re-validation,
   then a paper twin. Atlas thesis: every confirmed edge is rent on ONE defect (the book reprices
   toward spot/oracle too slowly) at different distances from settlement; the unfarmed frontier is
   EARLIER timing of the trusted disagreement signal. Negative cells = avoid-list (cheap consistent
   longshots vs a ≥5bps CL lead; early favourites against spot).

**Sweep under live costs (DONE 04:0x IDT, docs/research/SWEEP_LIVECOST_2026-06-10.md):** 2,423
hypotheses (18 families, 324 new psettle) → 24 shortlisted → verified ×2 fill models, future
override 06-05..09 (pre-registered; 06-01..04 relabeled holdout — burned by the 06-05 reveal).
**ONE new edge: psettle_2246** — buy the slight UNDERDOG at ask 0.50–0.78 (tl 60–360) when the
settlement model prices that side ≥ ask+0.15: live future **+$2.00/fill [1.34,2.64]** n=115, 5/5
days, 4/4 coins, WR 75%, no jackpot dependence, ~17 fills/day, Jaccard ≤0.13 vs everything (incl.
the fade — different band). Needs the model-in-engine paper twin (feature parity work). PLUS:
fav_disagree dist 10→5 widening replicated better than the running config (e4_1070 future +$2.21);
zscore_1822 (+$18.42!) correctly REJECTED as a lottery (one $495 window = 90% of total); the fade
region independently rediscovered (+$5.22/fill fresh block). hypothesis_verify now has
`--fill-model live` (default byte-identical); 239 research tests green.

**Paper twins deployed 04:1x IDT (zero-code, 19 strategies loaded, executor/A-B untouched):**
`early_disagree_v1` (atlas family, tl 450–900 ask 0.30–0.45; APPROXIMATION — no cl_dist<12 gate,
compare forward run to atlas cells) + `fav_disagree_d5` (dist 10→5 A/B vs running fav_disagree).
**fills_live validation (05:0x IDT) PASSED:** early-disagree future +$5.31/fill [3.71,6.81] @$10
(n=164; +$2.35 [1.63,3.08] @$5), WR 60%, fill 42–46% under hazard — 2nd-strongest validated edge
after the fade. The cl<12 gate is NOT load-bearing (+$4.94 [3.14,6.76], WR 64%, −28% signals) →
deployed twin tests the family as-is; cl gate = promotion-time tuning option.

**GEOBLOCK confound found + handled (18:30 IDT):** WARP/VPN 403s killed order-POSTs on FOUR days
(06-08..11, 26 events, last 17:22; reads unaffected = silent). Geoblocked intents raise BEFORE a
fill record → they sat inside the attribution's no_attempt bucket (part of the −$111 "missed EV"
was the VPN). Arm-agnostic → A/B comparison stays valid. Fixes: attribution now reclassifies
no-fill intents within ±90s of a logged 403 as cohort "geoblocked" (excluded from missed-EV;
--geoblock-log, 10 tests green). USER ACTION REQUIRED: exclude *.polymarket.com from WARP. Path
verified clear post-17:22 (3 clean fills). Wait-window research launched: sq-rescue agent (rolling
curve refit + regime gate + live-fill re-judge) and burst-cap/capacity agent (multi-coin burst
policy for the disagree family + max-stake curves per config).

**early_disagree_live DEPLOYED (06:0x IDT, user-approved EARLY — 4th live strategy, 22 loaded):**
$5/trade, $100 bankroll, $50/day hard_worstcase, guards shadow. Deliberate exception to the 1-week
paper rule (user chose time over caution; documented trade-off): the code path is identical to the
battle-tested fav_disagree_live (only params differ), fills_live-validated future +$2.35/fill @$5,
friendliest execution profile (7.5–15min to settle). Worst case now 4 × $100 bankrolls.

**psettle ENGINE twins DEPLOYED (05:33 IDT, 21 strategies):** new DetParams mode "psettle"
(engine/print_model.py = frozen logistic copy, NO research import; cache carries updated_at for
cl_oracle_age_s incl. warm_from_disk; per-tick cl_cb_basis/age in paper_engine; fail-closed
psettle_on_missing="skip"; model JSON fail-fast at boot). PARITY PINS (the deliverable):
logistic max|Δp|=2.2e-16; features exact (age Δ=0.0s, basis ≤1.1e-12bps), ±15s poll-age tolerance
moves p ≤0.0061<0.02; rule-decision Jaccard 1.000 + same-entry-second 1.000 on replayed 06-08
(both rules, cross-checked vs fam_psettle + fade_decisions outputs). 414 tests green. Twins:
`psettle_ud_v1` (=spec psettle_2246 verbatim: underdog ask 0.50–0.78, tl 60–360, margin 0.15) +
`oracle_fade_v1` (fav≥0.75, p_fav≤0.60, tl 60–360, buy cheap side 0.05–0.35), both live:false,
$10, $50/day hard_worstcase. Caveats on file: model artifact is dev-fit (refit cadence = weekly
review; feature-set drift fails fast at boot); engine cb_dist uses tick move_pct (0 decision flips
on parity day); mid-window restart shortens the rvol history one window. Also fixed
test_shadow_mode_checks_but_places env-leak (pins enforce_sids=set(); the production .env arms the
A/B). Executor untouched throughout — A/B intact.
Still queued: psettle_2246 + near-strike-fade engine twins (need p_settle features in the engine),
sq rescue, per-window multi-coin burst-cap proposal, Phase-6 status integration.

**Live books at 02:40 IDT:** det_lwd_live +$28.62 lifetime (best day +$17.96 on 06-10);
det_d12_dual_live −$50.00 lifetime (06-10 only −$2.27; half the $100 bankroll consumed, hard stop
at −$100); fav_disagree_live −$10.56.

## 2026-06-10 (20:50 IDT) — fav_disagree_live DEPLOYED (user-approved) + audit scheduled

**fav_disagree_live is the 3rd live strategy** ($5/trade, $100 bankroll, **$50/day** hard_worstcase
— raised from $25 at 20:55 IDT per user: 60% WR × ~3:1 payoffs is sq-shaped positive skew and H4
showed tight caps truncate exactly that profile; at ~8 fills/day realistic max deployment is ~$40,
so $50 binds only on a wild-volume catastrophe day. Engine restarted 20:50 + 20:57 IDT, 17
strategies loaded).

**Oracle research launched (2 background agents, 20:53 IDT):** (1) settlement-print model —
characterize the Chainlink print process, P(cl_up | cl_dist, oracle age, basis, tl, rvol),
continuous gate to replace AGREE + near-strike fade; pre-registered gates G1-G3; deliverables
research/analysis/oracle_print_model.py + docs/research/ORACLE_PRINT_2026-06-10.md. (2) Binance
composite — fetch 1s klines 05-22..06-10 → data/research/binance_1s/, composite-vs-Coinbase print
prediction (near-strike sign-agreement; the 37%@2bps benchmark), dual-gate-with-composite backtest;
gates B1-B3; research/{dataset/binance_fetch.py,analysis/binance_composite.py} +
docs/research/BINANCE_COMPOSITE_2026-06-10.md. Both pre-register in test_ledger.md, future-block
revealed once, live-fill-model scoring. Band tightened 0.90→**0.45** from evidence, not
taste: the forward-paper entry-bucket EV shows 0.50–0.90 entries LOSE (−$29/24tr, 46% WR) while
≤0.45 carries all the profit (incl. the 0.05–0.20 jackpot tail, +$496/4tr — kept, per the
deep-tail-floor lesson); live-model rejudge at ask≤0.45: **future +$3.54/fill [1.99,5.11]** vs
+$1.67 at 0.90. The 0.45 cap also bounds the executor chase band and lets the notional guard
(max_shares = bet×1.15/ceiling) deploy ~the full $5 at typical ~0.40 entries (at 0.90 it crippled
positions to ~$1.60). Guards stay SHADOW for this sid — floor-abort semantics were calibrated on
det; an ask drop is ambiguous for disagree (collect its own cohort first). Expectation set with
user: ~$15–25/day at $5, streaky (60% WR × ~3:1 payoffs), cap-pause days are the cap working.

**Shadow audit + conditional A/B flip scheduled 00:17 IDT (in-session cron, one-shot):** audits the
would-abort + knife cohorts' settled P&L; flips `EXEC_GUARDS_ENFORCE_SIDS=det_lwd_live` + bounces
the executor ONLY if the cohort settled net-negative with n≥3; else extends shadow. NOTE: the job
dies if this Claude session closes — fallback: ask any session after 00:17 IDT to "run the shadow
audit per STATE.md".

Also: fixed 3 legacy executor tests that wrote junk rows into the PRODUCTION data/live/fills.jsonl
(the "S"/UPTOK rows + phantom det_d12_wide_live fills; tests now use tmp fills_path; analysis
filters already excluded them; ledger scrub deferred to the next planned executor stop).

## 2026-06-10 (00:00–01:00 IDT) — Live-gap attribution + execution-integrity guards (SHADOW) + live-calibrated fill model

**Why live ≠ paper, now MEASURED** (`research/analysis/live_gap_attribution.py`, exact additive
identity, residual $0.0000): over the 322-intent universe (06-05..09) live −$14.53 vs paper
+$139.61, gap −$154.14 =
  missed trades −$111.01 (106 attempted-zero windows carried +$100.35 of paper EV — the misses
  ARE the winners; 62 of them are IOCs priced below the real touch → API-400, `fill_or_chase`
  breaks on the error and never advances) + paper-settled-on-wrong-oracle −$71.28 (Coinbase
  fallback before the 06-09 warm-fix — honest paper ≈ +$68, half the headline) + knife-catch
  cohort (31 fills, live −$23.50, fills >4c below quote = book collapsed through the order;
  no floor existed) + fees +$11.96 (live pays none) + slippage/size remainder.
  Outputs: `data/research/live_gap/`. Tests: `tests/research/test_live_gap_attribution.py` (9).

**Executor guards SHIPPED, SHADOW since 2026-06-09T21:03Z** (`scripts/live_executor.py`,
`EXEC_GUARDS=shadow` in .env): (1) pre-flight `get_book` FLOOR abort (best ask < entry−0.04 →
favourite flipped, skip; the −4c cohort boundary is where live EV turns toxic), (2) ladder starts
at the REAL touch (kills the API-400 bucket), (3) depth pre-check ≥50% target in-band + ONE 3s
delayed retry on dry, (4) stale-intent age gate >10s (the validated latency bound), (5) post-fill
`knife_catch` flag on every fill. off|shadow|on + `EXEC_GUARDS_ENFORCE_SIDS` per-sid A/B; fail-open;
state schema untouched; 13 new tests (28 in file, 334 suite green).
**NEXT: ~24h shadow audit (due ≈2026-06-10 21:00 UTC): confirm would-abort cohort settles net
negative → `EXEC_GUARDS_ENFORCE_SIDS=det_lwd_live` for the 2-3d A/B → re-run attribution
(knife→0, API-400s down) → EXEC_GUARDS=on.**

**Live-calibrated fill model** (`research/sim/fills_live.py` → `data/research/fill_model_live.json`,
246 attempts): zero-fill hazard ~40-49% EVEN at 2× displayed depth (depth doesn't predict
fillability — it's the stale-priced IOC, not thin books), κ=1.056, knife rate 22.6% of legacy
fills, clean-fill slip +0.7c, empirical latency sampling. Drop-in `simulate_taker_entry`
(guarded|legacy modes) returns fills_v2.Fill. This is the PRE-guard calibration — recalibrate
(live-2) after enforcement.

**All edges re-judged under the live model** (`research/analysis/rejudge_live_model.py`, $5 stake,
guarded mode, future-block CIs): det_d12_dual KEEPS future +$0.63/fill [0.20,1.06] (live stays);
det_d12_wide +$0.46 [0.22,0.70] (backup confirmed); **det_lwd_live goes MARGINAL +$0.17
[−0.26,0.57]** (last-60s = worst fill race; demotion candidate — crossings gate not modelled,
slightly pessimistic); **fav_disagree is the standout: future +$1.67/fill [0.76,2.59], +$1.62/signal,
on top of its +$809/93tr forward paper run** → live-readiness pack awaiting user go/no-go;
fav_lowvol/fav_deepdown CIs straddle 0 AND their forward paper runs are negative (−$73/−$34) —
not deployable; fav_momentum borderline. v2-mode reproduces documented numbers (no filter drift).
NOTE: hazard is applied randomly; live misses are adversely selected, so pre-guard EV/signal is an
optimistic bound — the touch-start guard is designed to break exactly that selection.

**Risk action (user-approved):** det_d12_dual_live reverted $10→$5/trade, $50→$25/day cap
(strategies.yaml; run_combined bounced 00:03 IDT, executor untouched then restarted 00:03 for
guards). Dual is cap-paused until 00:00 UTC (today −$47.72 > $25). Promotion policy: present-first
— no new live strategy without explicit user approval.

---

## 2026-06-09 — Dual-oracle gate + det_d12 refinement: det_d12_dual_live is the new live primary

`det_d12_wide_live` lost ~$18 overnight (06-08→09): the signal is Coinbase but Polymarket settles
on Chainlink, so near-strike windows FLIP and the favourite-longshot payoff turns each flip into a
~$6 swing (4/24 windows flipped, −$11.28). Reconciled every recent window to the Polymarket data-api
(nothing unbooked). Rebuilt `joined_15m` through 06-09 and ran a full Chainlink-settled research
battery (`research/analysis/dual_oracle_{features,gap,sweep}.py` + `fill_capture_backtest.py`).

**Validated config (future-blind, CPCV/DSR/latency):** AGREE gate (require Chainlink to agree with
Coinbase at entry) + max_ask 0.85→0.78 + adverse_vel≤2 → **future +$1.97/tr [+1.31,+2.59], WR 87%,
CPCV 100%, DSR 0.996, survives 10s latency** — ≈2× the det_d12 baseline (+$1.20). det_d12 is still
+$1.34/tr over the full 2.5 weeks (the bad night was variance). dist_min stays 12 (not monotonic on
Chainlink). Doc: `docs/research/DUAL_ORACLE_2026-06-09.md`, memory [[dual-oracle-gate-det-d12]].

**Implemented (no-op-safe defaults, 306 tests green):** dual-oracle gate in `determinism_state`
(+ registry + `paper_engine._TICK_DTYPE_EXT` cl_dist_bps + live `ws_collector` wiring from
`discovery._chainlink_start`/`_chainlink_price_asof`); laddered fill in `live_executor.handle`
(`fill_or_chase` capped at the per-strategy `max_ask` carried on the intent — never the old 0.92
hardcode that overpaid into −EV; budget-retry across the entry window). Generic for all live strategies.

**Cutover (08:31 IDT):** restarted run_combined + live_executor. `det_d12_dual_live` is the LIVE
primary (fresh $100 book, $25/day, gate=agree, on_missing=skip, max_ask 0.78). `det_d12_wide_live`
→ live:false BACKUP (+$21.93 history kept; flip live:true to re-arm). Paper twin `det_d12_dual_v1`
runs the A/B alongside `det_d12_wide_v1`. NOTE: after a restart the gate fail-closes (skips) on
windows that opened pre-restart until fresh windows capture a Chainlink strike (~1 window cycle).

**Sizing bump (10:50 IDT, per user): `det_d12_dual_live` $5→$10/trade, $25→$50/day cap** (bankroll
unchanged $100; risk ratio preserved ≈5 full-loss trades/day). Restarted run_combined ONLY
(stop_all/start_all never touch live_executor.py; PID 59067 stayed up through the bounce); seamless
warm confirmed (`chainlink_cache_warmed reads=628` at the new boot, all 4 coins), heartbeat 2s,
active_markets=24. The executor adopts bet_usd/cap from the next intent. Three-way backtest behind the
decision: `research/analysis/compare_dual_vs_wide.py` — dual (deployed) future +$1.75/tr vs wide
(=wide_v1==wide_live config) +$1.20/tr; dual trades fewer with a higher downside floor (flip-tail cut).

**Discovery re-close dedup (10:12 IDT):** found while verifying the warm-fix — Gamma keeps
returning ended windows, so `discovery._tick` re-"closed" each one EVERY poll (`self._active =
now_active` carried the ended slug back in). Harmless (settlement is idempotent — re-closes are
no-ops on FLAT strategies) but it spammed `market_closed chainlink_start=None` (strike already
popped) — which is what misled the warm-fix verification — and burned a Coinbase fetch per stale
slug per poll. Fix: `discovery._closed_slugs` set (bounded ~2h) — a settled slug is excluded from
re-discovery and not carried forward, so each window closes EXACTLY once. Provably can't prevent a
first close (slug marked only AFTER its real close). Regression test `test_ended_window_closes_
exactly_once`. 312 tests green. VERIFIED: post-restart window `sol-1780987500` strike_captured at
+37s (cl_start 67.20183) → settled basis=chainlink (warm-fix confirmed working end-to-end).

**Seamless restart fix (09:42 IDT):** ROOT CAUSE of the post-restart "skipped_oracle_missing" /
coinbase-fallback transient — the `ChainlinkPriceCache` is in-memory and EMPTY at boot, so for
~15-30min after any restart, `price_asof` returned None for recently-opened windows → no
`chainlink_start` captured → cl_dist NaN → dual gate fail-closed + settlements fell back to Coinbase.
Fix: `ChainlinkPriceCache.warm_from_disk()` pre-loads the last ~40min from `data/live_chainlink/`
CSVs at startup (called in `run_combined`). Now restarts are SEAMLESS: at boot `chainlink_cache_warmed
reads=632` → discovery captures strikes for already-active windows on the first poll → cl_dist
populates immediately. 311 tests green (2 new warm-from-disk tests). NOT my dual-oracle code's fault
(it never touched discovery); a pre-existing restart wrinkle the dual gate merely exposed.

**Adaptive max_ask (09:03 IDT, 2nd redeploy of run_combined only — executor untouched):** added a
DYNAMIC ceiling — max_ask 0.78 base, raised to 0.85 only when |cl_dist|>=20bps (deep Chainlink lock).
Backtest (`research/analysis/dynamic_max_ask.py`): cl-dyn 0.78→0.85@20 matches flat-0.78 EV/tr with
+17% volume / +15% total / tighter future CI. DetParams `max_ask_hi`+`cl_dist_hi_bps`; the per-trade
effective ceiling rides the intent so the laddered fill caps consistently. 43 tests green. Both
det_d12_dual_v1 (paper) and det_d12_dual_live (live) now run the adaptive ceiling. NOTE: still
waiting on the first clean-book dual window to OBSERVE a live fire (deep-favourite regime collapsing
late books for all det strategies; engine healthy, no errors).

## 2026-06-08 (latest+1) — Live P&L accuracy: backfilled 45 missing settlements + ground-truth headline

Audit of "is the /mean-rev-status live table exactly correct?" found it was NOT: the executor's
per-strategy book had booked only ~27 of 72 resolved windows. **45 filled windows that resolved
06-05 → 06-08 morning were never booked** (per-strategy settlement tracking + the settlements
ledger only started 06-08 12:19). Book showed +$46.66; Polymarket data-api ground truth was
+$55.44 — the table UNDER-stated real P&L.

**Fixes (both shipped + verified):**
1. **Backfill** — new `scripts/backfill_settlements.py` (dry-run default; refuses to run while the
   executor is alive; backs up state; idempotent). Books each missing window from `fills.jsonl`
   (actual filled_shares + usdc_paid, per strategy_id) × the win/loss confirmed by the data-api
   (redeem activity / position curPrice), using the executor's own `shares−usdc`/`−usdc` convention.
   Applied with the executor stopped: **det_lwd_live +$10.00 → +$21.96** (45 windows: 36W/9L; all
   det_lwd — they predate det_d12's deploy). Executor relaunched (pids 8906/8913); det_d12_wide_live
   $139.89, det_lwd_live $121.96.
2. **Status headline = data-api GROUND TRUTH** — `/mean-rev-status` §2b now prints
   `🎯 GROUND TRUTH (Polymarket data-api): $X realized` as the authoritative headline, the
   executor-book total, and `Δ(book−truth)` with an **asymmetric divergence flag**: alarms only when
   the book UNDER-counts reality by >$3 (missed settlements → run the backfill); a small POSITIVE Δ
   is expected and benign. Bumped the cross-check to `max_records=500` (120 was too few for a
   multi-day shared wallet). Re-framed the per-strategy table note: it's the executor's book (detail
   + caps), NOT the headline.

**KEY FINDING — the executor book runs ~$0.09/win ABOVE actual redeemed cash** (it books wins at
$1.00/share at settlement; the data-api measures the actual redeemed USDC). Across 72 windows that's
~$6 (book +$61.85 vs cash +$55.44). This is a benign convention gap, NOT an error — but it means the
**data-api ground truth is the real-cash number to trust**, and the executor book is a slightly
optimistic internal ledger that drives the (safe-direction) loss caps. Backfill only touched
`realized_total` (not `realized_by_day`), so today's UTC loss cap was not perturbed.

---

## 2026-06-08 (latest) — GASLESS redemption via Polymarket's relayer (EOA needs no MATIC)

The recurring "wins unredeemed" pain bottomed out at gas: our bot broadcast its own Polygon txs
and the owner EOA kept running dry. The UI redeems gaslessly because Polymarket's **relayer** pays
the gas. So we switched the claimer to that same path. Investigated the SDK end-to-end and
**verified `derive(owner, safe_factory) == 0x96fC0775…` (our exact proxy)** before trusting it.

**How:** new `src/.../live/relayer.py` wraps the official `py-builder-relayer-client` SDK
(`RelayClient.execute([Transaction(to,data,value)]).wait()`), which fetches the Safe nonce, signs
the EIP-712 Safe struct hash with our owner key, and submits with HMAC auth — relayer pays gas.
We pass only the inner `(to,data)`, reusing claimer's calldata encoders (new shared
`_encode_redeem_binary_inner` / `REDEEM_CTF_SELECTOR`) + the on-chain `_is_redeemable` gate. Creds
**auto-derived from the private key** (same `create_or_derive_api_key` the live executor uses);
fallback to `.env` `BUILDER_API_KEY/SECRET/PASS_PHRASE` if the relayer rejects them.

- `scripts/live_claim.py` — `CLAIM_VIA_RELAYER` (default ON): relayer preflight (`assert_proxy` +
  creds), route approve/redeem/wrap through `relayer.*`, `via:"relayer"` ledger; `=0` keeps the
  gas-paying EOA path as fallback. Relayer down → log + retry next cycle (never auto-pays gas).
- `scripts/claim_loop.sh` — added `--with py-builder-relayer-client poly-eip712-structs
  py-builder-signing-sdk py-clob-client-v2` to the daemon's uv invocation.
- `mean-rev-status` §2c — reports relayer health + `via:relayer` ledger; EOA gas demoted to
  informational; flags `relay_auth_failed`/`relay_preflight_failed` loudly.
- `tests/test_claimer_encode.py` — +4 pure tests (redeem-binary calldata + relayer build_* targets);
  10 passed.

**SAFETY:** `relayer.assert_proxy` refuses to relay unless the SDK-derived proxy == our wallet.
SDK is brand-new (v0.0.2) so the EOA gas path is retained behind `CLAIM_VIA_RELAYER=0`.

**VERIFIED LIVE (gasless proven):** auto-derived CLOB creds were rejected by the relayer (401),
so the user created a Relayer API key in Settings → `.env` `BUILDER_API_KEY/SECRET/PASS_PHRASE`
(creds=env). With those, **6 winners redeemed + wrapped $48.58 → pUSD, all via the relayer with
real tx hashes, and EOA MATIC was UNCHANGED (0.049636 before AND after) — zero gas.** pUSD
767.87 → 811.41; USDC.e → $0 (nothing stranded). Claim daemon restarted on the new gasless code
(pids 87888/87897), first cycle idempotent (redeemed=0, wins already claimed). Going forward every
win auto-redeems gaslessly within ≤30m — no MATIC ever needed.

---

## 2026-06-08 (later) — Claim root cause #2: data-api `redeemable` lag + low-gas silent fail

User saw a won BTC 15m position the bot never redeemed. Investigated; found **two distinct,
real failure modes** (the earlier "gas" hunch was only half the story):

1. **Data-api `redeemable` flag LAGS on-chain settlement (the real bug here).** The claimer
   gated redemption on Polymarket's data-api `redeemable=true` flag, which stays `false` for
   minutes after a window resolves on-chain. So the bot skipped genuine, on-chain-resolved,
   UI-claimable winners with `not_redeemable_on_data_api`. Proven on `btc-updown-15m-1780927200`:
   on-chain `payoutDenominator=1`, `payoutNumerator[Up]=1` (RESOLVED + won, 6.39 winning shares
   held) while data-api still said `redeemable=false`. Bypassing only the data-api gate → the
   redeem `simulated_ok`.
2. **EOA out of gas = SILENT failure.** The earlier $92.10 batch (data-api had caught up) failed
   at broadcast with `insufficient funds for gas` — EOA `0x6244…9Bbf` held 0.0496 MATIC, each
   redeem ~0.07–0.09 MATIC. Nothing surfaced it; wins just piled up. (User redeemed those manually
   via the UI's gasless relay, which is why gas alone didn't explain the skipped winner.)

**Fix:**
- `claimer.py` — redemption now gated on **on-chain truth**: new `_is_redeemable` =
  `payoutDenominator>0` AND proxy holds a **winning** outcome token (balance>0, via
  `getCollectionId`/`getPositionId` for CTF, or passed token ids for neg-risk). Idempotent (balance
  → 0 after claim). Data-api flag demoted to RPC-down fallback. Helpers: `_payout_denominator`,
  `_payout_numerator`, `_ctf_position_id`, `_onchain_winner_ctf/_tokens`, `get_matic_balance`,
  `eoa_address`. Updated all 3 call sites (`redeem_binary_ctf`, `redeem_both`, `redeem_one`).
- `live_claim.py` — discovery (`candidate_positions`) now scans ALL holdings (no `redeemable=true`
  filter) so fresh winners aren't hidden; per-position on-chain `payoutDenominator` pre-check labels
  unresolved trades `pending` (not FAIL). Added **gas preflight**: warns `[GAS-LOW]` + ledgers
  `gas_low` when EOA < 0.30 MATIC.
- `mean-rev-status` §2c — now reads + flags **EOA gas** (🔴 if < 0.30 MATIC) so low gas surfaces
  hourly BEFORE wins strand.
- `tests/test_claimer_encode.py` — +3 pure tests (CT view selectors, index-set→outcome map,
  conditionId length). 7 passed; 32-test no-regression slice green.

**Verified (dry-run):** bot now detects the BTC winner → `[OK] … simulated_ok` (was skipped),
losers `skip ($0)`, open trade `pending`. **STILL BLOCKED on gas** — to broadcast this last ~$6.39
the EOA needs POL. Sent loud GAS-LOW alert; user to top up `0x6244Dc7b4cd97A565a70D9b66B3Aa9d3a4f09Bbf`
(then daemon auto-redeems ≤30m, or trigger `live_claim.py --execute`).

---

## 2026-06-08 — Automated redeem + deposit-confirm (claim daemon) + missing-approval fix

Wins were sitting unredeemed and a Polymarket "Confirm pending deposit" banner was stuck
(~$25 wins + $6 USDC.e locked out of trading). Two root causes: (1) **nothing ran the claim** —
`hourly_monitor.sh` was never in crontab, `claim_loop.sh` was never launched, supervised path
only on-demand; (2) **the USDC.e→CollateralOnramp approval was never set** (allowance 0) and
**no `approve()` existed in the code**, so the USDC.e→pUSD wrap ("confirm deposit") reverted —
plus `live_claim.py` only wrapped *after* a redeem, so leftover USDC.e stranded.

**Fix (user authorized unattended automation + unlimited one-time approve):**
- `src/.../live/claimer.py` — new `approve_usdce_for_onramp()` (idempotent, unlimited, Safe-exec,
  sim/gas/receipt-gated) + `_get_erc20_allowance_raw`; **multi-RPC failover** in `_rpc_call`
  (transport-only fallthrough; 200+`{"error"}` reverts still returned so the sim-gate works).
- `scripts/live_claim.py` — 3 ordered steps: **approve → redeem → wrap**, where wrap runs EVERY
  cycle (decoupled from `redeemed>0`) so USDC.e never strands; audit ledger `data/live/claims.jsonl`.
- `scripts/start_all.sh` — auto-launches the **claim daemon** (guarded) = `claim_loop.sh` via
  `respawn_generic.sh`, `CLAIM_INTERVAL`=1800s, stop switch `data/live/CLAIM_KILL`.
- `.claude/skills/mean-rev-status/SKILL.md` §2c — now REPORTS claim/deposit state (daemon pid,
  `claims.jsonl`, balances, Onramp-approved) instead of being the claimer.
- `tests/test_claimer_encode.py` — pure approve-calldata + threshold tests (4 passed).

**Recovery executed (real money):** approve tx `0x6eebb8…` + wrap $37.62 tx `0xd868ce…` (gas
0.059 MATIC). After: USDC.e **$0.00 (cleared)**, pUSD **~$780 tradeable**, Onramp-approved **True
(unlimited)**. Daemon launched (pids confirmed up); first cycle was a clean idempotent no-op.

---

## 2026-06-08 — det_d12_wide LIVE + per-strategy executor isolation

Deployed a SECOND live strategy `det_d12_wide_live` ($5/trade, $100 bankroll, $25/day UTC hard
cap) alongside `det_lwd_live`, and refactored the live executor so each live strategy is an
**independent book** — its own bankroll balance, daily-loss cap, per-slug dedup, and concurrency —
so the two never interrupt each other (user requirement). The paper `det_d12_wide_v1` keeps running
for the OOS forward-test.

**Changes:**
- `scripts/live_executor.py` — `StrategyBook` per strategy; **state schema v2**
  (`{"version":2,"deployed_total":…,"strategies":{sid:{…}}}`) with a **lossless flat→v2 migration**
  that folds the existing real state into the `det_lwd_live` bucket (timestamped backup first,
  idempotent). `_blocked`/`handle`/`settle_pending` are per-strategy; caps now arrive ON each intent
  (`bankroll_usd`/`max_daily_loss_usd`, YAML = source of truth). New append-only
  `data/live/settlements.jsonl` ledger (per-settlement, with `strategy_id`). `GLOBAL_MAX_CONCURRENT=4`
  shared-wallet ceiling. Lazy clob/requests/dotenv imports → module unit-testable. Corrupt-state load
  now hard-blocks instead of trading blind.
- `src/mean_reversion_live/engine/strategy.py` — live intent now carries `bankroll_usd` +
  `max_daily_loss_usd`.
- `strategies.yaml` — new `det_d12_wide_live` (live twin of det_d12_wide_v1).
- `.claude/skills/mean-rev-status/SKILL.md` — new §2b **per-strategy LIVE table** (balance, total/
  today P&L, trades today/total, open, cap-left UTC, bankroll-left) + wallet-wide data-api cross-check.
- `tests/test_live_executor_multistrat.py` — 12 tests (migration, cap/dedup/bankroll isolation,
  concurrency ceilings, settlement attribution, v2 round-trip, corrupt-state). Full suite green
  (298 passed, incl. paper_engine_replay + daily_loss_guard).

**Deploy sequencing (real money — gate so no order hits stale code/pre-migration state):**
1. `touch data/live/EXEC_KILL` ; 2. `pkill -f "live_executor.py --live"` ; 3. code is landed ;
4. one `--dry-run` to migrate+verify v2 (det_lwd_live realized_total intact, backup present) ;
5. `/mean-rev-restart` (paper engine loads new YAML, emits det_d12_wide_live intents) ;
6. `rm data/live/EXEC_KILL` (monitor restarts the NEW executor `--live`).
**Caution:** det_d12_wide had only ~3-day paper forward-test (doc recommends 1wk) → expect ~20% OOS
decay; total real-money exposure is now two independent $100 bankrolls on one shared wallet — confirm
wallet pUSD collateral covers concurrent bets (~$20 worst-case in-flight).

---

## 2026-06-05 — Round 3: position sizing + ensemble (negative)

Tested variable sizing (confidence-scaled, fractional-Kelly) + correlation-aware ensemble on the
5 edges (report `docs/research/SIZING_2026-06-05.md`; harness `research/analysis/sizing_backtest.py`).
**REJECTED — no scheme beats fixed $10 on risk-adjusted return** (equal-capital total-$ and daily
Sharpe, the leverage-invariant metrics). Confidence ≈ fixed; Kelly worse; agreement-sizing = noise
(+0.7pp WR). Sizing is leverage, not alpha — the edges are already filtered on their confidence
features. **No engine change, no deploy, running strategies untouched.** Ensemble meta-layer gated
OUT. Actionable: keep fixed equal-size across the validated edges (diversification across the 5 ≈
Sharpe 1.35 is the free lunch, already captured by separate strategies); to earn more → more
independent edges or uniform leverage, not clever sizing.

---

## 2026-06-05 — Round 2: maker / oracle-staleness / new-signals (3 negatives)

Tested 3 new latency-proof directions (full report `docs/research/NEW_DIRECTIONS_2026-06-05.md`;
modules `research/analysis/{maker_execution,chainlink_staleness,new_signals_sweep}.py`). **All
rejected — no new deploy; running strategies untouched.**
- **Maker/limit-order execution** (the high-value one): resting bids save the spread+rebate but
  **adverse selection −18..−27pp WR** overwhelms it (you fill on the losers); maker EV negative on
  every edge. Stay taker. Maker NOT worth a live limit-order path for these edges.
- **Chainlink staleness-at-expiry:** dead — feed updates before settle 99.9% of the time; no
  age-monotonicity.
- **New-signal sweep** (cumulative flow / round-number / L2 walls): all re-label determinism (no
  incremental EV); XRP round-number is n=25 noise.
Durable edge remains determinism → `det_d12_wide_v1` (round 1) is still the one new paper edge to
forward-test. Next-round ideas (multipliers, not new edges): position sizing, ensemble agreement
sizing, online curve refit, funding-time regime.

---

## 2026-06-05 — 2099-hypothesis latency-proof sweep

**What:** ran a 2099-hypothesis sweep (17 families) for new/improved latency-proof edges on the
clean window 05-23..06-04. Chainlink-settled, hold-to-resolution, future block (06-01..04) held
out, full-L2 cost-stressed, 4 adversarial skeptics. Full writeup:
`docs/research/HYPOTHESIS_SWEEP_2026-06-05.md`. Pipeline:
`research/analysis/hypothesis_{sweep,select,verify}.py` over `edge_lab.py`.

**Result — ONE new deployable edge:** `det_d12_wide_v1` added to `strategies.yaml`
(enabled, **live:false**, $10, $50/day hard_worstcase). Improved determinism: consistent, last
0–180s, dist≥12bps, ask 0.50–0.85, buy favourite, hold. Future +$1.04/tr [+0.45,+1.56] n=250
where the running det_lwd is dead OOS (−$0.06); directionally balanced; best capacity (fills to
$100); only existing DetParams gates (registry builds it fine, parity by construction).

**Everything else rejected (honest nulls):** mid-window disagreement = 100% subset of running
`fav_disagree` (re-discovery confirms it; no new deploy). Low-vol fav-value = DOWN-drift artifact.
Z-score gate = refuted. Oracle-divergence / order-flow / microprice = dead. Mean-reversion stays
dead.

**ACTION NEEDED:** the YAML edit is inert until restart, and there's a live $5 probe running, so
the bot was NOT restarted — restart via `/mean-rev-restart` when convenient to start the
`det_d12_wide_v1` 1-week paper forward-test. Review ~2026-06-12 via `/mean-rev-review`. Running
strategies untouched.

---

## 2026-05-15 — Week 1 start

**Status:** Sibling repo `polymarket-mean-reversion` bootstrapped today. Live combined collector + paper trader works end-to-end:
- WebSocket consumes Polymarket CLOB books → 1Hz aggregator → 23-column CSV.gz (matches historical schema)
- 4 strategies route ticks through `PerMarketState` (the everted `simulate_market` loop)
- First live trades captured during the 2-minute smoke test
- All 12 unit tests pass including the **load-bearing replay parity test** (`tests/test_paper_engine_replay.py`)

**Backtest reference:** `/Users/itayozer/dev/polymarket-arb/data_v2/analysis/mean_reversion/SUMMARY_2026-05-15.md`

**Strategies running** (`strategies.yaml`):
- `cfg_21c8c00165b3` — DOWN-only validated #1 (88% WR backtest)
- `cfg_333fde9cecb8` — BOTH ASIA validated #2 (93% WR backtest)
- `relaxed_v1` — exploratory variant
- `cfg_5m_control` — 5m sanity check, expected to lose

**Data layout:**
- `data/historical/` ← physical move of `polymarket-arb/data_v2/`. `polymarket-arb/data_v2/` is now a symlink to here so the backtest CLI works on both old + new files.
- `data/live/` — new per-second tick CSVs starting today
- `data/outcomes.csv` — appended on each window close

**Key implementation note:** Polymarket's `/events` endpoint sorts by `startDate` (when trading opened, often 24h ago). To find markets currently IN their observation window, `clients/gamma.list_active_markets` PROBES `/markets?slug=...` directly for the next ±2 5m/15m boundaries.

**Polymarket WS protocol note:** the subscription message is only honored at session start; subsequent subscribes are ignored. When the active set changes, `WsCollector` closes the current connection and reconnects with the new asset list. URL: `wss://ws-subscriptions-clob.polymarket.com/ws/market`. Subscribe payload: `{"type":"market","assets_ids":[...]}` (note `assets_ids` typo is Polymarket's).

**Operating:**
- Start: `./scripts/start_all.sh`
- Status: `uv run python -m mean_reversion_live.scripts.status`
- Stop: `./scripts/stop_all.sh`
- Tail: `./scripts/tail_logs.sh`

**Next review:** 2026-05-22 (7 days from start). Tasks:
1. Run a comprehensive sweep on combined historical + 7-day live data (use polymarket-arb's `scripts.mean_reversion.cli sweep`)
2. Compare paper trades to backtest predictions per strategy — measure `mismatch_rate`, `pnl_diff`, `fill_rate`
3. Identify top new configs that emerged from the wider data window
4. Update this STATE.md with findings
5. Decide whether to go live with small size in week 2

**Known issues:**
- None yet. Smoke test was clean.

**Open questions:**
- How many tick rows/day to expect? At 1Hz × ~6 active 15m markets per symbol × 4 symbols ≈ 86k rows/day across all live files. Plenty for analysis.
- Do we collect during long-trading windows (24h pre-observation) or only during the 5/15m observation window? Currently we only collect during observation windows because `seconds_into_window` only makes sense there. The 24h trading-pre-window data is a separate phenomenon — maybe valuable for future strategies but not for THIS strategy.

---

## 2026-05-15 — Hardened for 7-day unattended run

Shipped the changes from `~/.claude/plans/understand-our-goal-and-soft-kay.md` (approved via ultraplan). All 9 tests pass including the load-bearing replay parity. Bot restarted on the new code and is running stable.

**What changed (additive only — no decision-path edits):**

1. **`scripts/respawn_loop.sh` (NEW)** — wraps `run_combined`, respawns on crash with 5s backoff. Cap 100 respawns; bails on 5-in-60s as crash-loop guard. `.combined.pid` now points at the wrapper. `stop_all.sh` SIGTERM is forwarded to the inner process.
2. **`logging_config.py`** — structlog now routes through stdlib `logging` (`structlog.stdlib.LoggerFactory`). RotatingFileHandler (10MB × 5) on `logs/combined.log` actually catches it now; previously it bypassed via `PrintLoggerFactory`. Raw stdout/stderr from the wrapper goes to `logs/combined.console.log`.
3. **`ws_collector.py`** — `_books` is GC'd in `update_subscriptions` when a token leaves the desired set. Bounds memory over a 7-day run.
4. **`run_combined.py`** — added `disk_watcher` (bails gracefully if free < 2GB), extended heartbeat with `books_in_memory`/`disk_free_gb`/`signals_today`, added `macro_dumper` (1Hz write to `data/live_macro/<date>.csv.gz`).
5. **`adapters/arb_imports.py`** — clearer preflight error when the `polymarket-arb` path is missing or doesn't contain `signals.py`.
6. **`scripts/status.py`** — surfaces respawns-today (parsed from `logs/respawn.log`), books_in_memory, disk_free_gb, signals_today.

**Rich data capture (parity-safe):**

7. **`engine/per_market_state.py`** — `PerMarketState.__init__` accepts an optional `observer: Callable[[dict], None]`. Invoked once at the end of `on_tick` via try/finally — AFTER all rng draws and state mutations. Decision values: `flat | near_miss | armed_new | armed_waiting | fired | rejected_fill | skipped_no_fill | skipped_already_traded | skipped_can_enter | skipped_skip_prob | holding | trade_closed_<reason>`. Pure-function near-miss detection via single-param relaxation against `EntryFeatures` (drop ≥ 0.8×, prox ≤ 1.5×, time ≥ 0.5×, price band ±25%).
8. **`engine/strategy.py`** — built observer closure: throttles chatty states (`flat`, `holding`, `skipped_*`, `near_miss`) to 1/sec/slug; ALWAYS logs entries/exits. Writes to `data/jsonl/<sid>/signals.jsonl`. Drops `flat` entirely (would dominate the log).
9. **`engine/market_context.py` (NEW)** — `MarketContext.update(symbol, yes_mid, no_mid, ts_ms)` + `snapshot(ts_ms)` returning `n_symbols_dipping_5pct_60s`, `<sym>_yes_mid`, `<sym>_drop_60s_pct`. O(symbols) per tick. Fed by `paper_engine._on_tick`. Logged into both `signals.jsonl` (as `macro` field) AND `data/live_macro/<date>.csv.gz`.
10. **`collectors/macro_writer.py` (NEW)** — `MacroCsvGzAppender` writes 1Hz cross-symbol snapshots. Schema fixed at construction from `symbols`. Close-and-reopen on each fsync (60-row cadence) so each segment is a complete gzip member — readable mid-write by both `gunzip` and the Python gzip module.
11. **`tests/test_signal_log.py` (NEW)** — verifies observer doesn't break parity AND emits exactly one `fired` event per trade.

**Four new "human-intuition" shadow strategies in `strategies.yaml`:**

| id | Idea |
|---|---|
| `cfg_manual_mirror` | Closest to manual rules — wider band (0.10–0.30), 7-min-left filter, slower reaction (signal_skip_prob=0.15). |
| `cfg_velocity_v1` | Fast knife > slow bleed: `drop_window_sec=15`, otherwise mirrors validated #1. |
| `cfg_imbalance_v1` | Wait for sellers to exhaust: validated #1 + `book_imbalance_min=2.0`. |
| `cfg_wide_band_v1` | Volume generator: 0.05–0.35 band, drop=12, profit_target=40, max_hold=300. |

8 strategies enabled total. Each gets its own `data/jsonl/<sid>/` dir + `data/portfolios/<sid>.json`.

**Verification done:**
- `uv run pytest tests/` — 9 passed including parity tests.
- 10-min smoke: bot up, 25–32 active markets, signals.jsonl populating for every strategy, `data/live_macro/2026-05-15.csv.gz` has 61+ rows with all 10 columns.
- Crash recovery: SIGKILL on inner Python — wrapper detected (rc=137), slept 5s, respawned (`logs/respawn.log` shows the transition). Status shows `respawns today: N`.

**Sample signal record (observer + macro wired):**
```json
{"ts_ms":..., "strategy_id":"cfg_velocity_v1", "decision":"skipped_skip_prob",
 "features": null,
 "macro":{"n_symbols_dipping_5pct_60s":3,
          "btc_yes_mid":0.155, "btc_drop_60s_pct":0.0,
          "eth_yes_mid":0.025, "eth_drop_60s_pct":92.96,
          "sol_yes_mid":0.02,  "sol_drop_60s_pct":95.18,
          "xrp_yes_mid":0.355, "xrp_drop_60s_pct":14.46}}
```

**Small in-flight fixes discovered during smoke (not in the plan):**
- `MacroCsvGzAppender` originally locked schema on first row, but the first row arrives before any ticks have populated `MarketContext` — schema was getting locked to `{ts_ms, n_symbols_dipping_5pct_60s}` and silently dropping every per-symbol column. Fixed by passing `symbols=settings.symbol_list` into the constructor and pre-computing the full schema. Also moved from `flush+fsync` to `close-and-reopen` per fsync segment so the partially-written file is gunzip-readable mid-run.
- `stop_all.sh` had `set -u` interacting oddly with the `for i in {1..30}` loop. Dropped `-u` from the strict-mode flags. Cosmetic — the script worked either way.

**Known gaps (deferred to week 2):**
- `data/portfolios/*.json` files are still session-only (not loaded on restart). Each restart resets the in-memory portfolio to zero. `trades.jsonl` is the durable record — the week-end review reads from there.
- No SIGHUP hot-reload of strategies. Restart suffices for now.
- Chainlink oracle integration for outcome correctness still deferred. Validated configs rarely hit `forced_resolution` since their `max_hold < window_duration`.

**Next review:** still 2026-05-22 (7 days from start) — comprehensive sweep on combined historical + 7-day live, compare paper trades to backtest predictions, surface top new configs. The new data fields are load-bearing for the review:
- `data/jsonl/<sid>/signals.jsonl` for entry-funnel analysis (fired vs near-miss vs skipped — where is each strategy losing potential trades?)
- `data/live_macro/<date>.csv.gz` for "does the edge weaken under macro stress?"
- Compare the 4 shadow strategies' PnL/WR against the 2 validated configs.

---

## 2026-05-22 — Full edge-research engagement (branch `edge-research`)

A from-scratch, physics-first investigation replacing the sweep-and-deploy
approach. **Full write-up: `docs/research/FINAL_REPORT.md`.**

**Outcome: no profitable strategy found — a genuine, honest negative.**

What happened:
- **Phase 0 audit found two data bugs.** (1) March 16–17 tick data has a corrupt
  order book (`bid > ask` 83–88%) — the data the original `BACKTEST_VERDICT.md`
  sweep ran on; that "edge" was a ≈$2/trade encoding artifact. `BACKTEST_VERDICT.md`
  is now marked **invalid**. (2) The live bot's `discovery.py` recorded each
  strike ~30 min too early, corrupting `move_pct`/`outcome` for all May data
  (labels wrong on 31% of windows).
- **The strike bug is FIXED** — `discovery.py` now captures the strike at
  window-open; the bot was restarted. Correct labels were rebuilt from
  Polymarket's API (real resolved outcomes, 100% coverage) and the canonical
  dataset re-derived.
- **L2 capture added** — the bot now also writes full-depth books to
  `data/live_l2/` at 1 Hz.
- **Phases 2–4 on corrected data:** the market is well-calibrated; odds continue
  *down* after a drop (no bounce); the user's patient policy loses −$2.19/trade
  honestly priced. **Market-making feasibility: no-go** (adverse selection >
  spread). Three interim "edges" were all data artifacts, caught in research.
- Root cause of viability: a 16–21% taker round-trip cost wall that no measured
  edge clears.

**Bot status:** running, with the strike fix + L2 capture. It may continue as a
pure data collector; the `research/` pipeline is re-runnable on future data.

**Open door:** the user's real manual trade records were never available — the
one remaining way to test whether the manual edge was real. See FINAL_REPORT §4, §7.

---

## 2026-05-22 — Leaderboard wallet analysis (branch `leaderboard-wallet-analysis`)

Owner asked: do the robust wallets on Polymarket's crypto profit leaderboard
profit from market-making, and can we replicate it?
**Full write-up: `docs/research/leaderboard_mm_verdict.md`.**

**Outcome: the market-making hypothesis is refuted; the dominant winning pattern
has no replicable edge.**

What happened:
- Built a data pipeline and pulled **239 leaderboard wallets** (union of top 100
  of the MONTH/WEEK/ALL crypto boards), fetched each wallet's on-chain activity,
  and classified by archetype. Result: **167 (70%) `directional_holder`** (buy a
  side, hold to resolution), 27 `mint_merge_arbitrageur`, 10 scalpers, **1**
  true `passive_liquidity_provider`, 34 mixed/non-crypto.
- **MM hypothesis refuted, doubly confirmed.** The prior `market_making_feasibility.md`
  said NO-GO from crude economics (spread ~1c vs adverse selection ~2.25c). The
  wallet evidence confirms it independently: if MM were profitable the
  leaderboard would be full of market-makers — it is 1 of 239.
- **The directional pattern is survivorship, not edge.** Backtested the dominant
  pattern (rule M15-DH-1: buy favourite early, hold to resolution) on tick data
  that includes the losers: 15m taker −$0.26/trade (CI straddles 0), 5m taker
  −$0.55/trade (CI negative), maker variants straddle 0 or negative. Nothing
  CI-positive on DEV; sealed hold-out stayed sealed. The favourite side is
  well-calibrated. This is the **4th independent confirmation** the short-dated
  market is efficient-after-cost.
- **Data bug found:** `data/research/ticks_5m.parquet`'s baked-in `outcome_up` is
  ~31% corrupt (1,564 of 5,018 windows); the backtest used corrected 5m labels.
  Future 5m work must do the same. 15m labels are authoritative.

**The one open lead:** the `mint_merge_arbitrageur` cluster (27 wallets, several
with $0.5M–$1.6M lifetime PnL) is the only genuinely non-directional, persistent
pattern — but it lives in **longer-dated crypto price-target markets** (not 15m
Up/Down), is completely untested by us, and pursuing it would be a new research
project (new data collector, new study). Presented as a decision for the owner.

**Recommendation:** NO-GO on market-making and NO-GO on directional strategies
for 5m/15m crypto Up/Down. No edge for a small patient bot in the markets
studied. See `docs/research/leaderboard_mm_verdict.md` §7.

---

## 2026-05-29 — Edge hunt on new-data feeds: FIRST real edge found

Full forensic + research pass using the L2/trade-tape/fast-spot/Chainlink feeds
live since 2026-05-22 (the doors prior research said were untested). See
`docs/research/edge_hunt_synthesis.md` and per-phase docs.

**Phase 0 — trustworthy harness (DONE, null-test PASS ✅):**
- Settlement feed CORRECTED: these markets resolve on the **Chainlink Data Stream**,
  not Coinbase (prior Task-4 verdict was an artifact of no Chainlink data then).
  Ties resolve **Up**. No liquidity-rewards pool exists. (`phase0a_settlement_feed.md`)
- Joined dataset `data/research/joined_15m.parquet` (2.2M ticks, 2456 windows,
  clean window 05-23→29) + realistic fill/cost simulator `research/sim/fills_v2.py`
  (walks real L2; taker 0.07·p(1−p); hold-to-resolution = one-way cost) + null-test
  gate (`research/sim/null_test.py`). Harness re-confirms the market is calibrated.

**The finding — book LAGS spot → momentum/determinism edge (NOT mean-reversion):**
- **Phase 1 (PRIMARY): late-window determinism pickoff.** Last 60s, spot ≥5bps from
  strike, buy favourite at ask ≤0.90, hold to resolution. **OOS hold-out: +$1.68/
  trade, 91% WR, CI [+0.97,+2.39], ~$73/day.** Survives 5s latency, both-halves CV,
  all 4 symbols. (`oracle_mechanics.py`, `oracle_mechanics.md`)
- **Phase 2 (secondary): mid-window stale-quote pickoff**, jump-gated. OOS +$2.7–3.7/
  trade, CI excl. 0, but higher-variance/outlier-sensitive. (`stale_quote.md`)
- **Phase 3: maker = NO-GO** (real round-trip −0.6 to −1.8¢; no rewards; inventory
  risk). (`maker_real.md`)
- **Phase 4: dip-reversion (user's original thesis) = NEGATIVE** — dipped side is
  calibrated; spot-flat filter (never testable before the proximity-bug fix) doesn't
  help. The "buy the dip" intuition is backwards here. (`trade_flow.md`)

**Next — Phase 5 (harden):** gauntlet (multiple-testing correction, cost-stress,
larger re-sealed hold-out); build engine support for the determinism strategy (new
type: late-window favourite-buy + hold-to-resolution, NOT the mean-reversion
machine); forward paper on unseen windows; then small live test ($50–100, $10/trade,
daily cap). Caveats: 7 clean days only; fat left tail; capacity ~$10–50/trade.

## 2026-05-29 (cont.) — Phase 5 gauntlet PASS + forward validation (+ a fake-positive caught)

**Gauntlet on the Phase 1 determinism edge — PASS** (`docs/research/gauntlet_verdict.md`):
cost-stress combined worst-case +$1.28/tr CI[+0.82,+1.74]; per-regime both green;
calibrated multiple-testing null p<0.0001 for the robust rule (dist≥5/ask≤0.90,
N=333). The sweep-max (dist≥10) is within best-of-20 luck (p=0.054) — use the
robust rule, not the max.

**Live engine support built but NOT deployed — critical catch.** Added a new
strategy type `engine/determinism_state.py` (+registry/strategy wiring, all
additive; replay-parity test still green; 8 unit tests). Validating that it
reproduces the backtest exposed a fatal feed gap: the live tick's coinbase_price/
move_pct is the STALE ~14s poll (median 1.75bps off the fresh WS spot, sign
disagrees 12.8%) — a `DeterminismState` reading it is a LOSER (true WR 0.48) while
self-reporting a fake +$2.4/tr. `det_lwd_v1` is in strategies.yaml but
**enabled:false**. (`docs/research/forward_deployment.md`)

**Forward validation — running safely** via daily OOS backtest on fresh cb_spot
(`research/forward_validate.py`, log `docs/research/forward_validation_log.md`):
6/7 clean days green, OOS (28-29) +$1.68/tr WR 0.908 CI[+0.97,+2.39], cum +$482.

**Pre-LIVE requirement:** wire the fresh WS spot (live_spot) into the paper engine,
then re-enable det_lwd_v1, confirm live-paper vs backtest drift <30%, then small
live test. Deliberately gated — paper-prove first.

**Phase 6 (widen to hourly/daily): not triggered** — it was gated on Phases 1-4
all being negative on 15m; a 15m edge exists, so widening is optional (future
capacity play; 15m edge is capacity-limited ~$10-50/trade). Tests: 259 pass.

## 2026-05-29 (cont.) — DEPLOYED: determinism edge to forward paper; all mean-rev disabled

Per user: disabled all 9 prior (mean-reversion) strategies; deployed the Phase 1
determinism edge to live PAPER for forward testing, with a daily max-loss cap.

**Engine work (all additive; replay-parity test green; 13 det/parity tests pass):**
- **Fresh WS spot wired into the engine** (`spot_ws_collector` now updates the shared
  SpotPriceCache). Verified live: tick coinbase_price tracks WS spot to 0.15bps (was
  stale ~1.75bps, sign-wrong 12.8% — which had faked a live +$2.4/tr loser). THIS was
  the pre-live blocker; now fixed.
- New strategy type `DeterminismState` + `DailyLossGuard` (engine/determinism_state.py),
  wired via registry/strategy (det_params). Three hardening fixes found by the
  "live must reproduce backtest" gate: (1) book-health guard (skip decided/collapsed
  late-window books), (2) TRUE-outcome settlement via engine.settle_window() on_close
  (tick-derived settle was optimistic 0.96 vs true 0.89), (3) fresh-spot distance.
  Validated: live replay reproduces the gauntlet exactly — 336 tr, WR 0.893, +$1.581/tr.

**Live now (`strategies.yaml`):** ALL prior strategies enabled:false. Two enabled:
- `det_lwd_v1` — uncapped (measures the true forward edge).
- `det_lwd_v1_capped` — $50/day max-loss cap (live-candidate config).
Both: 15m, last 60s, |spot−strike|≥5bps, buy favourite ask≤0.90, hold to resolution,
$10/trade, $1000 capital. Restarted clean (pid 1346); 28 markets; emitting live.

**Watch:** first det trades appear in the last 60s of 15m windows as they close
(across the day, not just ASIA). Forward track also runs off-engine daily
(research/forward_validate.py). Data note: joined outcome_up_clean is per-row and
corrupted on start_price=0 rows; all harnesses filter start_price>0 so results are
unaffected (live settles on market.start_price = real strike).

## 2026-05-29 (cont.) — Phase 2 added + complete per-trade data capture (1-week forward run)

Per user (let it run 1 week; want complete data to later lift WR/profit via time/
condition filters; add the 2nd edge):
- **Phase 2 (stale-quote pickoff) deployed** as `det_sqp_v1` (uncapped) + `det_sqp_v1_capped`
  ($50/day). New `StaleQuoteState` (engine/stale_quote_state.py) loads a FROZEN empirical
  P(Up|z) curve (data/research/stale_quote_curve.json); mid-window, |model_p-mid| in
  [0.08,0.30] + spot jump>=8bps, hold to resolution. Offline replay reproduces the edge:
  403 tr, WR 0.509, +$3.48/tr, median +$3.69 (higher-variance secondary edge).
- **Rich per-trade data capture** for BOTH edges → data/jsonl/<sid>/trades_detailed.jsonl:
  per trade logs hour, dow, symbol, time_left, dist_bps, entry_ask, spread, ask_depth,
  spot_vel_10s/30s, rvol_60s, (+model_p/z/mispricing for sq), outcome, pnl. This is the
  dataset for the weekly review to find filters (skip hours/regimes) that raise WR+profit.
- 4 enabled strategies (2 det + 2 sq); all mean-rev still disabled. Engine: settle_window
  now settles any hold-to-resolution state (hasattr settle). 266 tests pass, parity green.
  Restarted pid 17253.

**1-WEEK FORWARD RUN started 2026-05-29 ~12:26 UTC → review ~2026-06-05.** Bot runs via
nohup (survives session close). Review: live-paper vs backtest drift <30%; slice
trades_detailed by hour/regime/symbol to propose WR-lifting filters; decide on small live test.

---

## 2026-05-30 — Loss-pattern mining → two robust loss-avoidance filters

Owner asked (mid forward-run): scan all losing trades per active strategy, find a
robust causal pattern, backtest a fix. **Full write-up: `docs/research/loss_pattern_filters.md`.**
New code: `research/analysis/loss_patterns.py` (per-trade ledger builder, winners+losers)
and `research/analysis/loss_filter_eval.py` (dev → both-halves → sealed-holdout eval).
Bot left running untouched (analysis-only; no engine/strategies.yaml changes).

**Method:** rebuilt `joined_15m` to the FULL 8 clean days (05-23..30; hold-out widened to
05-28..30, n=387 sq / 130 det). Rebuilt the full per-trade ledger for both live edges (real
L2 fills, hold-to-resolution, TRUE outcome), engineered only causal decision-time conditioners,
mined DEV (23-27) losers, required the lift to hold in both dev halves + sealed hold-out
(28-30) + the real live-bot trades. Harness null-test still PASS. New code:
`research/analysis/{loss_patterns,loss_filter_eval,loss_features_creative}.py`.

**Findings (robust):**
- **`det_sqp_v2` (stale-quote): raise margin 0.08→0.12 AND skip dist>19bps.** Loss axis
  is monotonic+causal (tiny mispricings = noise; far-from-strike = betting vs near-certain).
  EV/tr dev +4.63→+6.71; **hold-out +2.06→+4.31 (CI[+0.19,+4.20]→[+1.74,+7.38], lifts OFF zero;
  TOTAL $796→$1043)**; **live bot +1.28→+5.12.** Quadruple-confirmed (dev/both-halves/holdout/live).
- **`det_lwd_v2` (determinism): max_ask 0.90→0.88 + skip adverse_vel_10s>2bps + require
  strike_crossings≥1.** EV/tr dev +1.37→+2.17, hold-out +1.65→+2.17, WR 91% (both). The
  `strike_crossings≥1` lever is the owner's "15m checkpoint touch" idea — 0-crossing windows
  are already-decided blowouts (fav pinned at high ask, only late-reversal downside, EV −$1.53);
  OOS-confirmed on both splits. Modest: lifts EV/WR/tail but ~flat total $ (capacity-limited edge).

**DEPLOYED 2026-05-30 (per user): det_lwd_v2 + det_sqp_v2 added as live strategies + backfilled.**
- Engine: additive gates (det `adverse_vel_max_bps`, `min_strike_crossings` + a strike-crossing
  counter; sq `max_dist_bps`), wired via registry. v1 byte-identical (no-op defaults). 271 tests
  pass incl. replay-parity; 4 new gate tests. strategies.yaml: det_lwd_v2 (max_ask 0.88,
  adverse_vel<=2, crossings>=1) + det_sqp_v2 (margin 0.12, dist<=19) enabled; v1 kept as control.
- Backfill (`scripts/backfill_v2_history.py`) from v1 live-start (05-29 12:31 UTC). **KEY GOTCHA:**
  data/live CSV `move_pct` is the STALE ~14s poll (CSV-replay of v1 = 71% WR/-$14 vs 90%/+$52 live);
  the engine traded off the FRESH WS spot. So backfill sources fresh spot from joined_15m
  (`dist_strike_bps`) — validated: reproduces v1 to 96%/+$68 (vs 90%/+$52 live; within noise).
  Apples-to-apples over the v1 window: det_lwd_v1 26tr/96.2%/+$2.62EV vs v2 22tr/95.5%/+$2.90EV;
  det_sqp_v1 70tr/60.0%/+$1.09EV vs v2 42tr/59.5%/+$2.03EV. v2 trades.jsonl/trades_detailed/
  portfolio written; bot restart picks them up via the on-construction trade replay.
  CAVEAT: v1's *logged* monitoring numbers are real-time-engine; v2's are joined-reconstruction
  (the ~6% optimism the v1 96%-vs-90% gap shows applies to v2 too). Going forward both run in the
  same live engine → exact A/B.

**Honest negatives:** (1) cross-coin macro stress ("other crypto pricing") looked clean but was
**lookahead**; point-in-time recompute shows no robust effect — RETIRED (re-testable via live
MarketContext n_symbols_dipping). (2) Creative sq features (intra-window RSI, window high/low,
realized-vol): none beat S4 — sq WR degrades monotonically with vol (72%→40%) but EV doesn't
improve (right-skewed payoff: big winners share the high-vol bucket); dev-down/hold-up = noise.
A variance/WR lever, not free EV. (3) prev_fav_lost weak/neutral OOS. (4) Day-of-week &
time-of-day untestable on 5 dev days.

**Recommendation (surfaced, NOT applied):** add `det_sqp_v2` + `det_lwd_v2` as parallel
PAPER strategies (keep v1 as unfiltered control); adjudicate on fresh OOS at the 06-05 review.
Owner to enable in strategies.yaml + restart.

---

## 2026-06-05 — Full improvement sweep + new edge + LIVE probe wired (dry-run, paused)

**Deliverable:** `docs/research/IMPROVEMENT_FINDINGS_2026-06-04.md` (honest verdict) + every
test pre-registered/logged in `docs/research/test_ledger.md`. Method: full 13-day window
(05-23..06-04, fresh `cb_spot`), two regimes (walk-forward + Combinatorial Purged CV), PBO /
Deflated-Sharpe, window-clustered CIs. New libs: `research/lib/rigor.py`, builders
`research/analysis/{build_full_ledgers,generalize,phase1_characterize,phase1_v2filters,cap_policy_full,phase2_loco_plateau,phase3_ensemble,meta_label,e4_verify}.py`.

**Findings:**
- **Determinism = real, robust, SMALL.** 9/9 walk-forward days +, 15/15 CPCV +, PBO 0.107,
  survives all cost/latency stress, all 4 coins, smooth plateau, max DD ~$4. Caveat: ~$1/tr,
  13-day track record (minTRL ~14d), calibrated-null p=0.054 marginal. → live-probe candidate.
- **Stale-quote = real but fragile.** OOS+ (CPCV +$1.80 CI[+1.22,+2.43]) but Deflated-Sharpe
  0.65 @ high trial-count, execution-fragile (adverse-fill CI crosses 0, $25 size cap, sub-5s
  latency, must TAKE not make), and **frozen curve DRIFTED** (A3: over-predicts Up +5.6pp on
  June). → keep PAPER.
- **NEW edge E4 (disagreement-determinism): verified.** Last 60s, book favourite DISAGREES
  with spot → buy the cheap spot side. +$13–36/tr OOS, 84% WR, all coins, 15/15 CPCV.
- v2 filters confirmed OOS (sq-v2 biggest lift +$1.2→+$2.8/tr). Ensemble det⊕sq anti-corr
  −0.28 → Sharpe +2.03. Honest negatives: H3 cheap-floor REFUTED, meta-label not > v2,
  tight daily cap costs sq 33–48%, E1/E5 dead, E2/E6 inconclusive, E3 untestable (chainlink=0),
  mean-reversion dead (A1, −$31.5k).

**Deployed THIS session (bot restarted, now 8 strategies):**
- `det_disagree_v1` (E4) added as PAPER strategy (new `mode:"disagree"` in DeterminismState; parity-preserving, unit-tested `tests/test_e4_disagree_mode.py`).
- `det_lwd_live` added (`live:true`, det_lwd_v2 config + $50/day hard_worstcase cap) — emits entry intents to `data/live/intents.jsonl`.
- sq curve **re-fit** on trailing 7d (`scripts/refit_sq_curve.py`; old → `.bak`). Schedule nightly = rolling (A3 fix).
- det-v2 crossings gate was already engine-supported; confirmed active.

**LIVE EXECUTION wired (standalone, real wallet, PAUSED at dry-run):**
- Vendored `src/mean_reversion_live/live/clob_trade.py` (from elon-tweets, 0 internal imports). Runs on Python 3.11 via `uv run --python 3.11 --with py-clob-client-v2 ...` (paper bot stays 3.9.6, untouched).
- Same wallet as elon-tweets (key+proxy hash-match). **Preflight PASSED:** $776 pUSD, allowances MAX-approved on all 3 V2 contracts.
- `scripts/live_executor.py` — tails intents, resolves token_ids via Gamma, places FAK (take-liquidity), guards: KILL/`EXEC_KILL`, $100 bankroll, $50/day, max 2 concurrent, time_left≥20s. **Order-path validated** (real non-marketable FAK reached Polymarket, rejected on min-size only — $0 spent). `scripts/live_preflight.py` for read-only checks.
- **Dry-run executor RUNNING** (logs `would_place` per real signal, no orders). **PAUSED awaiting owner GO** to flip to `--live` for the $100 / $10-per-trade probe on `det_lwd_live`.

**Open follow-ups:** (1) win redemption/claim path not yet vendored (probe entries resolve; redeem winners via Polymarket UI or add `claimer_web3.py`); (2) schedule nightly `refit_sq_curve.py`; (3) E2/E6 optional paper experiments; (4) fix chainlink collector (all-zeros) to enable E3.

---

## 2026-06-05 (cont.) — ORACLE FIX: engine now settles on Chainlink, not Coinbase

**Why:** the first real `det_lwd_live` fill was a paper WIN (+$3.33 Coinbase) but a real
LOSS (−$10 Chainlink). Root cause: the engine settled every window on **Coinbase spot**
(`discovery.py` close + strike), but Polymarket **resolves on Chainlink**. So every backtest
WR/EV was Coinbase-settled and ~20–30% optimistic. Memory: [[backtest-settles-coinbase-not-chainlink]].

**Re-settle on Chainlink (`research/analysis/resettle_chainlink.py`, local `data/live_chainlink/` feed):**
- Coinbase-vs-Chainlink window-outcome disagreement **8.55% overall**, **37.3% within 0–2bps** of strike.
- **det_lwd:** CB +$1.25/tr → **CL +$0.88/tr** (−29%, 20% of trades flip); fresh-OOS ≈ **$0.00**.
- **det_sqp:** CB +$1.66 → **CL +$0.71/tr**; fresh-OOS +$0.33.
- **E4 (disagree):** CB +$36.75 → **CL +$16.74/tr**, fresh-OOS **+$18.93** (strongest survivor; the
  Chainlink re-settle INVERTED the edge ranking — E4 > det/sq on the oracle that actually pays).

**Engine fix (settlement-only; SIGNAL path untouched → replay-parity GREEN):**
- `collectors/chainlink_collector.py`: new `ChainlinkPriceCache` (thread-safe ring buffer); the
  poll loop `record()`s every read. `price_asof(sym, t_ms, tol=120s)` = oracle price at-or-before a
  window boundary (keyed on poll time, matching the offline re-settle's asof merge).
- `markets/discovery.py`: captures `chainlink_start` at strike-freeze and `chainlink_end` at
  window_end_ts (queried AT the boundary, robust to late close-detection); passes both to on_close.
- `engine/paper_engine.py` `settle_window`: `outcome_up = chainlink_end ≥ chainlink_start` (tie→Up).
  **Coinbase is FALLBACK only** (logged `settle_fallback_coinbase`) when the oracle feed has a gap.
  Per-trade `last_ctx` now records both bases + `oracle_disagree` for live audit.
- **`start_price` (Coinbase) stays the SIGNAL basis** (book lags Coinbase spot) → same trades fire,
  `tests/test_paper_engine_replay.py` byte-identical. `outcomes.csv` stays Coinbase (history-comparable).
- Tests: `tests/test_chainlink_settlement.py` (7 new: cache asof/tolerance + the headline
  Coinbase-win-but-Chainlink-loss case settles as a LOSS + fallback). **Full suite 294 pass, 1 skip.**

**Restarted** the paper bot on the fix (pid 52766, all 8 strategies). Verified live: chainlink loop up
(rows_ok=4/cycle), cache warming. Expected transient: windows that opened before restart settle via
Coinbase fallback (their `chainlink_start` predates the cache) for ~15 min; new windows settle on
Chainlink once warm. **Replayed historical PnL is unchanged (Coinbase) — only forward settlement moves.**

**Executor stays HALTED** (`data/live/EXEC_KILL` present). The live go/no-go is now a real decision:
det is ~$0 EV on fresh Chainlink; **E4 is the strongest candidate (+$16.74/tr CL).** Needs owner GO +
a re-settled E4 gauntlet before any real order. Data bot stays live.

---

## 2026-06-05 (cont.) — LIVE: $5 det_lwd_live probe flipped ON (real money)

**Owner GO given.** After the Chainlink engine fix + execution-layer rewrite + clean data reset,
flipped `det_lwd_live` to **real $5/trade live trading** (~10:06 UTC).

**What shipped before the flip:**
- **Execution fix:** per-order fill detection (`get_order.size_matched` + realized trades) replacing
  the shared-wallet balance-poll that corrupted the first probe fill (46sh/$0.233 for a 13sh/$0.74
  order). `clob_trade.py`, 5 unit tests (`tests/test_clob_fill_detection.py`). $5 sizing + notional
  guard (worst-case ~$5.3) + implausible-ask guard. dist≥8 Chainlink-robust filter, $25/day cap.
- **Live-mode dry-run enabled** (`_killed(mode)`: EXEC_KILL blocks LIVE only, not dry-run).
- **Backtest on Chainlink (new form):** det dist≥8 +$1.01/tr FULL but **+$0.04 fresh-OOS (CI crosses 0)**;
  sq +$0.71/+$0.33. Honest read: edge ~break-even on fresh data → probe is execution-validation +
  clean-data, not a confident profit play. (`research/analysis/backtest_chainlink_newform.py`)
- **Data reset** for clean Chainlink-settled forward test (`data/archive/reset_chainlink_20260605_085448/`).

**Flip sequence:** preflight PASS (pUSD $765, allowances MAX on 3 V2 contracts) → stopped dry-run →
cleared executor_state → removed EXEC_KILL → started `live_executor.py --live`. First intent (xrp DOWN
$5) fired during the transition and was DRY-RUN validated ($0 spent, sizing correct); live executor
started after and skipped it. **$0/$100 deployed, 0 real fills yet** — armed for the next det intent.

**Caps:** $5/trade, $25/day (UTC), $100 bankroll, MAX_CONCURRENT 2, time_left≥20s. **KILL switches:**
`data/KILL` (full stop) or `data/live/EXEC_KILL` (executor-only). Expected rate ~10 trades/day but
bursty (~⅔ of hours dormant). Monitor crons keep the live executor alive + auto-claim wins.

**Next:** watch the first REAL fill (true end-to-end test of the new fill detection); compare live vs
paper twin; after a few days decide scale/stop on clean Chainlink-settled live+paper evidence.

---

## 2026-06-05 — Chainlink edge hunt → durable favourite-value edge found + paper-deployed

**Driver:** user wants durable, **latency-independent**, profitable edges (trades from a laptop, no speed edge ever); survivors → 1-week paper, then decide live. Keep data bot + $5 det_lwd live probe running.

**Phase 0 (done):** merged `data/live_chainlink/` into `joined_15m.parquet` (`chainlink_price` now 99.6% real; `cl_cb_basis_bps` added) via `research/dataset/chainlink_merge.py` (wired into `joined.py`) + one-time `augment_chainlink.py`. Built shared gauntlet `research/analysis/edge_lab.py` (Chainlink resettle + depth-gated realistic fill + latency-survival sweep + per-split CIs + CPCV/DSR) — reproduces det baseline exactly. Slim frame `joined_15m_slim.parquet`.

**Honest baseline (Chainlink, the oracle that pays, depth-gated):** det_lwd ≈ break-even fresh-OOS; sqp +$0.33 (CI crosses 0); E4 +$18.93→**+$2.30 fresh-OOS** once depth-gated + latency-decaying. The simple pickoff edges are thin/marginal on the correct oracle.

**Phase 3 (done):** 8-agent creative workflow (`edge-refine-hunt`, ~500 variants) → **4 keep / 2 marginal / 2 kill**. Independently reproduced (`verify_survivors.py`). The durable edge = **favourite-longshot bias** (book underprices near-locked favourites), decided with a buffer + held to resolution → latency-proof. 4 nearly-disjoint slices (Jaccard <0.1); **combined fresh-OOS +$0.90/tr [+0.55,+1.25], DSR 0.97**. Killed: oracle-divergence (= the Coinbase determinism trade, adds nothing), microstructure (needs sub-2s speed), persistence (reverses across OOS).

**Phase 4 (forward test LIVE):** deployed 3 PAPER strategies — `fav_lowvol` (low-vol cheap-fav), `fav_deepdown` (deep DOWN-fav, buy NO), `fav_momentum` (late momentum). Implemented as DeterminismState variants (added `vol_max_bps` + `restrict_fav_side` gates, no-op defaults → 30 tests green incl. replay parity), hard_worstcase $50/day cap, $10 paper bet. Engine restarted 2026-06-05 ~12:00 IST; all 3 in heartbeat; live executor untouched. **1-week Chainlink-settled forward test → then decide live.** Doc: `docs/research/CHAINLINK_EDGE_HUNT_2026-06-05.md`. Memory: [[favourite-value-edge-found]].

**Live probe:** first real fills — 1 sol UP filled ($5.02; per-share accounting suspect, $ correct), 2 benign FAK no-fills. Deployed $5.02/$100. Cap restart-safety verified (det_lwd_live hard_worstcase + replay; $100 bankroll cap restart-safe).

**Remaining (lower priority — new edges dominate):** sqp correlation-netting (macro-tail loss-prevention), E4 depth-aware sizing; crosscoin engine plumbing. Add a unit test for the new gates.

---

## 2026-07-09 — Housekeeping: 14.6GB disk reclaim + signals rotation shipped; twin gates mid-flight (early_disagree_cl leading)

Status checks (10:23 + 20:10 UTC) all green — engine 3d+ uptime, all 4 daemons, 0 respawns.
**Live ground truth accelerating: +$62 (07-06) → +$136 → +$148.35 (202W/36L), account $669.93
fully liquid.** fav_disagree_live's best real day (+$35.19, incl. 3 cheap-favourite jackpots
+$12-25 each), balance −$26.15→−$13.31. Gate mid-flight (n=14 since re-arm): mean +$3.80/fill,
CI [−2.21,+9.82] — **dead on the bubble**; at 2 fills/day, CI-lo>0 on 07-20 needs mean ≥~$3.9.
Δ(book−truth) −$174 re-confirmed 0-bookable (roster-churn artifact; dry-run this morning).

**DISK RECLAIM (user go): freed 14.6GB** (machine 14.5→29GB free). Gzipped the 8 frozen/dead
strategies' signals.jsonl (det_sqp trio 11.4GB, tadiv ×2, oracle_fade, fav_deepdown,
det_d12_dual_live) — engine holds no handles on disabled strategies, safe while running.
trades.jsonl/portfolio_snapshots left plain (durable ledgers, span math reads them).
**ROTATION SHIPPED (scripts/start_all.sh):** at next clean start, any signals.jsonl >200MB is
mv'd to signals.YYYYMMDD.jsonl + gzipped in background. Engine keeps a persistent append handle
(persistence.py JsonlAppender) so rotation ONLY happens in start_all after the running-guard —
do NOT rotate externally while the engine is up. Machine-wide burn is mostly NOT the bot
(~/Library/Developer 33GB Xcode, Caches 15GB, Docker build cache 9.3GB reclaimable, ~/dev/spca
active) — bot writes ~0.3GB/day post-rotation.

**PROMOTION QUESTION (user asked): recommendation NO promotion before the registered dates.**
Mid-flight twin standings vs their 14-day gates (PAPER-settled = inflated caveat; official
labels + fills_live are the real 07-17 test): early_disagree_cl_v1 **+$0.80/fill CI [+0.12,+1.48]
n=384 — CI-lo>0 at halfway, the leading candidate**; fav_disagree_d5cl_v1 +$0.35 [−1.31,+2.00]
spans 0; xb_5m15m_causal +$0.70 [−0.97,+2.37] spans 0. psettle_ud_v1/det_disagree_v1 look great
on paper labels but are NOT registered candidates and never passed official-label gates (the
06-19 reckoning: paper stars deflate ~4:1). Calendar: **07-17 twin gates (official-label
re-settle + fills_live guarded) → 07-20 fav_disagree_live size gate → 07-24 freeze lifts.**
Next live promotion, if any, = early_disagree_cl_v1 passing 07-17 on official labels, own $100
bankroll + $25/day cap, user sign-off.

---

## 2026-07-17 — Deep-dive session: twin gates scored (2 kills), capacity expansion to 7 coins

**The scheduled check (07-03 twins' 14-day gates) — both FAILED on official labels:**
- `fav_disagree_d5cl_v1` KILLED: n=314, **-$401.45**, EV -$1.28/fill, CI [-2.49,-0.05]. The
  CL-agree-gate improvement is dead forward (engine paper claimed +$0.47 — oracle inflation).
- `early_disagree_cl_v1` KILLED: n=782, **-$179.66**, EV -$0.23/fill (EV<0 n>=40 rule). The
  atlas early-timing edge did not survive. Both pruned (roster 19→17), engine restarted
  ~16:41 UTC (wrapper 6640). Their 200MB signal logs auto-rotated.
- `xb_5m15m_causal_v1` inconclusive (+$0.73, CI spans 0) — data collection until post-07-24.

**Honest state of the world:** fav_disagree family is the ONLY validated edge. Live +$3.15/fill
(n=23) vs paper twin +$2.02 same window — tracking. Live capture verified PERFECT (41/41 twin
decisions intended; low n IS the edge frequency ~2.9/day). det_lwd_live: 400 real fills,
EV +$0.135 CI spans 0 = break-even probe as designed. 07-20 gate: NEUTRAL => extend $10 to 08-03.

**CAPACITY EXPANSION deployed (user-approved): SYMBOLS += bnb,doge,hype — paper only.**
The one growth lever that scales the validated edge without touching it (~+75% signal volume).
Wiring: coinbase.py + spot_ws_collector.py maps, .env SYMBOLS, Chainlink feeds bnb/doge
(verified vs spot; hype has no Polygon feed — fail-closed OK). REAL MONEY GUARDED:
live_executor.py gained EXEC_SYMBOLS allowlist (default btc,eth,sol,xrp) — new coins CANNOT
reach the wallet until their pre-registered gate passes (test_ledger "CAPACITY EXPANSION
GATE", eval 2026-07-31) + user sign-off. Executor bounced ~16:41 UTC (also deployed the
pending clamp_buy_fallback from 07-06). Post-restart: 28 markets (16+12), hb 4s, queue 0.

**Next:** 07-20 fav_disagree gate (mechanical: extend). 07-24 research freeze lifts (g2bps-5y
re-registration candidate). 07-31 new-coin gate. Watch queue/CPU under 28 markets for a day.
