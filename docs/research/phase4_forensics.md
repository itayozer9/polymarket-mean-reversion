# Phase 4 forensics — the profit-target exits are an artifact

> Task 8d. Decisive forensic audit of `docs/research/reconstruction.md`'s
> **positive** verdict (patient policy: taker +$1.83/trade, +$2,746 over 1,498
> dev trades, all EV from 335 profit-target exits averaging +$15.79). The result
> contradicts Phase 2 (market calibrated, no edge) and Phase 3 (after a drop the
> price continues *down*; sell-the-bounce loses ~$2.20/trade). This audit
> reconciles them and re-prices the exits honestly.
>
> Probe: `research/audit/phase4_exit_forensics.py` (committed, reproducible).
> Data: corrected dev split (May 15–20), `entry_candidates_15m.parquet` joined
> back to the full order book in `ticks_15m.parquet`. Hold-out untouched.

## TL;DR — OVERALL VERDICT: ARTIFACT. Tag: BUG.

**The +$1.83/trade Phase 4 taker edge is not real.** It rests on a bug in the
held-side book reconstruction in `research/analysis/patient_policy.py`. Once the
exits are priced against the genuine order book, the policy earns **−$2.19/trade
(90% window-clustered CI [−$2.41, −$1.97]), total −$3,275** over the same 1,498
dev trades. The sign flips. This **agrees with Phase 3** (−$2.20/trade) and with
Phase 2 (no edge). `reconstruction.md`'s verdict must be corrected — see the
last section.

---

## The bug — `_held_mid` / `_held_bid` invert on decided-market books

`patient_policy.py` simulates from the cheap-side `entry_candidates` table, which
only carries the *cheap* side's columns. After entry the held side may not be the
cheap side, so the simulator reconstructs the held side's mid/bid from the cheap
side's via the complement identities:

```python
# _held_mid:  held mid = 1 - cheap_mid     (when cheap_side != entry_side)
# _held_bid:  held bid = 1 - cheap_ask     (when cheap_side != entry_side)
```

These identities hold **only when the book is two-sided and consistent**
(`yes_mid + no_mid = 1`, `yes_bid + no_ask = 1`). They **break on decided-market
books** — the ~6.6% of May ticks Phase 0 Task 3 flagged, where a resolved market
has `yes_ask ≈ 0` / `no_ask ≈ 0` and the mids collapse so `total_mid ≈ 0`, not 1.

**Worked example** — `btc-updown-15m-1779030000`, entry side NO, the window
resolved Up so **NO lost**. At the exit tick (sec 899) the raw book is:

```
yes_best_bid=0.999  yes_best_ask=0.000  yes_bid_depth=2563   yes_ask_depth=0
no_best_bid =0.000  no_best_ask =0.001  no_bid_depth =0      no_ask_depth =2563
```

The cheap-table picks YES as cheap (`yes_mid 0.000 ≤ no_mid 0.000`), so
`cheap_mid=0.000`, `cheap_ask=0.000`. The reconstruction then yields:

- `_held_mid(NO) = 1 − cheap_mid = 1 − 0.000 = 1.000` → **trivially clears any
  +75% profit target** → the simulator declares a profit-target exit.
- `_held_bid(NO) = 1 − cheap_ask = 1 − 0.000 = 1.000` → the simulator **books the
  sale at $1.00/share**.

The genuine NO book is `no_best_bid = 0.000` with `$0` of depth. The held NO side
**lost** — its real value is ~0. The bug shows it as worth ~1.0 and "sells" it
there. It is not selling a bounce; it is mis-reading the resolution itself as a
mid-window sale at a phantom price.

---

## Q1 — Why Phase 3 loses and Phase 4 "wins": the exact reason

Both phases buy a dipped side and take profit. The lever that flips the sign is
**the exit fill assumption**, nothing else.

- **Phase 3 (`drop_events.py`)** takes the exit bid **directly** from the held
  side's own column (`g[f"{side}_best_bid"]`) and **guards** entries with
  `entry_ask > 0 & entry_bid > 0 & entry_bid < entry_ask` — a genuine two-sided
  book filter. It never sells into a decided book. It loses ~$2.20/trade.
- **Phase 4 (`patient_policy.py`)** reconstructs the held bid as `1 − cheap_ask`
  and has **no degenerate-book guard at the exit**. On decided books that
  complement is a phantom ~1.0. Those phantom fills are the entire "edge".

It is **not** selectivity (the 0.10–0.30 band / 420 s filter) and **not** the
profit-target level. The Phase 4 reconciliation grid shows the policy is positive
at *every* profit target (25/50/75/100%) — because the bug fires at every target
(a ~1.0 ghost mid clears all of them). Re-priced honestly, every target is
negative. The difference is purely the exit pricing / book-health filter.

| profit target | Phase 4 taker total (reported) | honest taker total |
|---|--:|--:|
| +25% | +$2,237 | negative |
| +50% | +$2,737 | negative |
| +75% | +$2,746 | **−$3,275** |
| +100% | +$2,921 | negative |

**VERDICT Q1:** Phase 3 loses and Phase 4 "wins" solely because Phase 4's exit
fill reads a phantom ~1.0 bid out of decided-market books that Phase 3 correctly
excludes. Same trade, broken fill.

---

## Q2 — Exit-tick book health for the 335 profit-target winners

Each profit-target exit tick joined to the full order book:

| book class at exit | n | share | sellable? |
|---|--:|--:|---|
| healthy (two-sided, real bid + depth) | 177 | 52.8% | yes |
| one-sided (no real bid, `bid ≤ 0.011`) | 127 | 37.9% | **no** |
| decided-market (`held ask ≈ 0`) | 31 | 9.3% | **no** |
| **degenerate total** | **158** | **47.2%** | **no** |

Exit-tick **held-side best bid** (the genuine sell price): median **0.320**,
but **47.2% are exactly 0.000** (the p10 and p25 are both 0.000). The simulator's
booked exit proceeds have median 0.665, mean 0.731 — a **mean gap of +0.463**
over the real best bid.

Exit-tick **held-side bid depth (USD)**: median **$5**; **58.8%** of exits have
bid depth below the $10 stake; **47.2%** have **exactly $0 of depth** at the bid.

Exit-tick **spread**: median 0.010 — *on the cheap-table view*. The degeneracy is
invisible there because the entry-candidate table carries only the cheap side's
ask depth and no opposing-book columns; the bug is only visible against the full
`ticks_15m` book.

The 158 degenerate exits contribute **$4,017 of the $5,288** profit-target PnL —
76% of the "edge" is phantom fills.

Most damning: of the 158 degenerate exits, the held side ultimately **won 0 of
158**. Every single one resolved *against* the entry side. The bug fires
precisely when the held side is losing — `_held_mid` inverts a worthless losing
side into a ~1.0 "profit target". (This also explains the
subagent's "71% of profit-target wins are in losing windows": those are not
genuine bounce harvests, they are the inverted-resolution artifact.)

**VERDICT Q2 (BUG):** 47% of the profit-target exits are into one-sided /
decided-market books with a real best bid of 0.000 and $0 of depth. They are not
tradeable. They are the resolution event, mis-read as a sale.

---

## Q3 — Honest re-pricing: the edge does not survive

Re-simulated with strict, realistic taker exits: sell at the **genuine** held-side
best bid; require bid **depth ≥ position value** or walk the book 2¢ worse
(Phase 0 Task 8 model); **exclude** decided / one-sided / crossed / zero-depth
books — those positions cannot be sold, so they fall through to **resolution on
the true outcome**; and **cap** a one-tick post-jump fill at `max(target, prev
tick bid)`.

| scenario | profit-target total | overall total | mean PnL/trade |
|---|--:|--:|--:|
| A — sim as reported | +$5,288 | +$2,746 | +$1.833 |
| B — + walk-the-book | −$387 | −$2,929 | −$1.956 |
| C — + exclude degenerate books | −$411 | −$2,953 | −$1.971 |
| D — + cap post-jump fill | −$733 | −$3,275 | **−$2.186** |

**Honest scenario D — overall mean PnL/trade −$2.186, 90% window-clustered CI
[−$2.405, −$1.967], total −$3,275 over 1,498 trades.** The CI is entirely below
zero. The profit-target bucket collapses from +$5,288 / +$15.79 mean to
−$733 / −$2.19 mean.

The 158 degenerate exits, forced to hold to resolution, settle at **−$10.50 mean**
(held side won 0/158). The 177 "healthy" exits do survive as real round-trips
**but they do not carry a net edge** — and 79% of even the healthy exits fill on a
tick where the held mid jumped >0.05 in a single second (mean overshoot +0.061
past target), so capping that optimistic post-jump quote (scenario D) costs a
further $322. The honest number is firmly negative either way.

**VERDICT Q3 (BUG):** The +$1.83/trade taker edge does **not** survive honest
exit pricing. It becomes −$2.19/trade, CI excludes zero on the negative side.

---

## Q4 — Look-ahead audit

`simulate_window`'s exit scan is `for j in range(entry_idx + 1, len(w))` —
**strictly-later ticks only**. The profit-target test reads `held_mid` from the
contemporaneous scan tick `j`; the fill then re-fetches the *same* tick
(`seconds_into_window == exit_sec`) and reads `_held_bid` from it. Entry features
(`cheap_mid`, `cheap_drop_30s`, `time_left_sec`) are read at the entry tick only.
Resolution settles on `outcome_up`, a window-level oracle, only when no earlier
exit fired.

**VERDICT Q4 (OK):** There is **no temporal look-ahead** — no peeking at later
ticks, no future-tick fill. The bug is **not** look-ahead. It is a wrong
*contemporaneous* price: the held-side book reconstruction (`1 − cheap_*`) is
invalid on decided-market books, so the simulator reads a phantom ~1.0 price from
the correct tick. (One milder optimism remains: on the 177 healthy exits the fill
books the post-jump bid of the triggering tick rather than a price near the
target — corrected in scenario D's cap, still negative.)

---

## Q5 — Example paths: real round-trips vs artifacts

The probe printed full per-tick paths (entry→exit) for 8 winners. Two clear
populations:

- **Healthy (e.g. `btc-updown-15m-1778889600`, NO, sim +$9.82):** a genuine
  two-sided book the whole way — held bid 0.30 → 0.50 → 0.64, depth $10–$340 on
  both sides, spread ~0.01, coinbase price drifting. This is a *real* intra-window
  round-trip. These 177 exist — but they do not net positive (Q3) and the bot
  cannot tell them apart from the artifacts ex ante.
- **Degenerate (e.g. `btc-updown-15m-1779030000`, NO, sim +$31.25):** the held
  NO side's mid grinds **down** the entire window — 0.30 → 0.21 → 0.13 → 0.05 →
  0.02 → 0.009 → **0.000** — and the window resolved Up (NO lost). There is no
  bounce anywhere in the path. The "profit target" fires at sec 899 where the
  real NO book is `bid 0.000 / ask 0.001`, $0 depth — a fully decided market. The
  simulator books +$31.25. `1779130800`, `1779165000`, `1778976000`,
  `1778986800` are the same story: a monotone decline into a 0.000 bid, scored as
  a +$20–$25 win.

**VERDICT Q5:** The big "winners" are **artifacts** — losing positions whose
decided-market book the complement arithmetic inverted into a phantom ~1.0 sale.
The 177 healthy paths are real round-trips but carry no edge.

---

## Q6 — Stale-book check at the exit tick

For every profit-target exit tick, the count of consecutive prior seconds with a
byte-identical held-side bid/ask: **median 0, mean 0, 100% fresh, 0% stale ≥5 s.**

The exit ticks are *not* frozen quotes — by construction, the simulator exits on
the *first* tick that crosses the target, which is necessarily a tick where the
quote just *changed*. So staleness is not the mechanism here. (The wider book is
~87% stale per Phase 0, but the specific exit ticks are the fresh-print
exception.) The artifact is the decided-market complement inversion, not a stale
quote.

**VERDICT Q6 (OK on staleness):** Exit ticks are fresh prints; staleness is not
the bug.

---

## Overall verdict — ARTIFACT

| Question | Verdict |
|---|---|
| Q1 — Phase 3 vs Phase 4 reconciliation | the exit fill assumption: Phase 4 reads a phantom ~1.0 bid from decided books Phase 3 excludes |
| Q2 — exit-tick book health | **BUG** — 47.2% of exits into one-sided/decided books, real bid 0.000, $0 depth |
| Q3 — honest re-pricing | **BUG** — edge flips to −$2.19/trade, CI [−$2.41, −$1.97] |
| Q4 — look-ahead | **OK** — no temporal look-ahead; the bug is a wrong contemporaneous price |
| Q5 — example paths | the big winners are inverted losing positions, not bounces |
| Q6 — staleness | OK — exit ticks are fresh prints |

**The Phase 4 profit-target edge is a DATA / RECONSTRUCTION ARTIFACT.** The
`_held_mid` / `_held_bid` complement identities in `patient_policy.py` are invalid
on decided-market books (`total_mid ≈ 0`, not 1), so the simulator (a) *detects*
a +75% profit target the moment the held side resolves and its reconstructed mid
inverts to ~1.0, and (b) *fills* that "sale" at a phantom ~1.0 bid against a book
with $0 of real depth. 158 of the 335 profit-target exits — and $4,017 of the
$5,288 profit-target PnL — are this artifact. Every one of those 158 is a
position that actually **lost** at resolution.

Honestly priced (genuine bid, depth-aware, decided books excluded), the patient
policy earns **−$2.19/trade (CI [−$2.41, −$1.97]), −$3,275 total**. This is the
same family of failure as the Task 3b March bid/ask corruption and the Task 8c
`start_price` artifact — the project's fourth data artifact. There is **no
surviving edge**. Phase 4 does **not** contradict Phase 2 or Phase 3; once the
exits are priced honestly it **agrees** with both: the market is calibrated and
sell-the-bounce loses ~$2.20/trade.

---

## Required correction to `reconstruction.md`

`docs/research/reconstruction.md`'s "Phase 4 Verdict" — *"the user's stated
policy back-tests positive on the dev split … taker $2,746 total … mean
$1.83/trade … CI excludes zero"* — is **wrong** and must be corrected. The
positive result is an artifact of the decided-market book inversion in
`_held_mid` / `_held_bid`. The honest figure is **−$2.19/trade, CI [−$2.41,
−$1.97], −$3,275 total** — a clean negative that agrees with Phase 2 and Phase 3.
Specifically:

- Section "Phase 4 Verdict", section 1 (baseline table), section 2 (win rate vs
  EV), and section 3 (attribution — the "335 profit-target exits, mean $15.79")
  all rest on the artifact and are withdrawn.
- The maker column (+$5,623, 73% WR) is **also** contaminated — its profit-target
  bucket uses the same `_held_mid` for the sale price and inherits the same
  phantom ~1.0. Maker is not a valid optimistic bound here.
- The breakeven-exit paradox (section 2b) is *not* driven by this bug (breakeven
  exits fire early, 0/977 into a degenerate book) — but its framing as "the rule
  leaks value vs a positive baseline" is moot once the baseline is negative.

**The fix for any future patient-policy work:** price the held side from its own
`{side}_best_bid` / `{side}_best_ask` columns in `ticks_15m.parquet`, never from
`1 − cheap_*`; and apply Phase 3's two-sided-book guard at *both* entry and exit
(`bid > 0.01`, `ask ∈ (0,1)`, `bid < ask`, `bid_depth > 0`). The hold-out (May
21–22) was not consulted and remains sealed — correctly, since a policy that
fails this badly on the dev split should not consume it.
