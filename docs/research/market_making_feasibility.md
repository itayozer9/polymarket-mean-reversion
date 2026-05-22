# Market-making feasibility study — Polymarket 15m crypto Up/Down

**Date:** 2026-05-22
**Type:** Feasibility SCOPING study — **not** a backtest.
**Data:** corrected dev split (`data/research/ticks_15m.parquet`, May 15–20 2026,
1,676 windows, 4 symbols). Sealed hold-out (May 21–22) **not loaded**.
**Probe:** `research/analysis/market_making_probe.py` (committed, reproducible).

---

## Why this study exists, and why it is a scoping study

The directional research is complete and null. On corrected labels the market is
well-calibrated (`PHASE2_RERUN_VERDICT.md`), the price *continues down* after a
drop rather than bouncing (`bounce_atlas.md`), and the user's patient
buy-the-dip policy loses **−$2.19/trade** once priced honestly
(`phase4_forensics.md`). Three would-be edges were all data artifacts.

The one remaining idea is **market-making**: stop *betting* direction and start
*providing* liquidity — post limit orders, capture the bid/ask spread plus the
crypto maker rebate, and stay roughly direction-neutral.

**This cannot be properly backtested on our data.** `ticks_15m.parquet` is **1 Hz,
top-of-book only, and 91.6 % stale** (consecutive byte-identical top-of-book on
the dev split). A real market-making backtest needs the full L2 book, your queue
position at each price level, and the trade-by-trade fill sequence — none of
which exist here. So this document gives **crude economics and a go/no-go**, not
a PnL number. Where the data forces a one-sided bound, both the optimistic and
pessimistic bounds are stated.

**Maker economics (verified, `research/audit/cost_notes.md`).** Polymarket fee is
`fee = shares · 0.07 · p · (1−p)` for crypto. **Takers pay it; makers pay 0.**
Crypto makers additionally earn a **rebate of ~20 % of the taker fee** on the
matched volume. At a mid of 0.51 and a $10 stake (~20 shares) the rebate is worth
about **0.35 c per share per fill** — small.

---

## 1. Spread capture

Genuine two-sided books (`yes`/`no` both quoted, `bid < ask`), YES odds in
[0.05, 0.95]: **1,217,744 of 1,502,307 dev ticks (81.1 %)**.

| metric | value |
|---|---|
| spread, median | **1.00 c** |
| spread, mean | 1.87 c |
| spread, p75 / p90 | 2.00 c / 3.00 c |
| spread as % of mid, median | **2.99 %** |
| spread as % of mid, mean / p90 | 5.96 % / 13.33 % |

The spread is **flat across the odds curve** — median 1.00 c in every YES-mid
bucket from [0.05,0.15) through [0.85,0.95). It is a 1-tick book almost
everywhere two-sided liquidity exists.

**Top-of-book depth is thin:** median **$26** on each side, p10 **$5**, p25 $10.
A $10 stake fits at the median but is *at or above* the quoted level a quarter of
the time. Per symbol the picture splits cleanly:

| symbol | median spread | median bid depth |
|---|---|---|
| BTC | 1.00 c | $124 |
| ETH | 1.00 c | $37 |
| SOL | 2.00 c | $14 |
| XRP | 2.00 c | $12 |

SOL and XRP have **2× the spread and ~$13 of depth** — a $10 order is already the
whole level. Only BTC (and to a lesser extent ETH) has a book deep enough to
quote into at all.

**Best-case gross spread capture.** If a maker is filled on *both* sides of a
round trip — buy the bid, sell the ask, no adverse move, no inventory carried —
on a $10 stake: spread captured **mean $0.545 / median $0.294**, plus a maker
rebate of **~$0.13** on the two legs, for a **gross total of ~$0.44–0.68 per
clean round trip**. This is the ceiling. It assumes away the two things that
actually decide market-making PnL: adverse selection (§2) and inventory (§3).

---

## 2. Adverse selection — the central risk

A passive quote does not get filled at random. It gets filled by whoever chooses
to cross the spread, and that flow is, on average, **informed**: a resting bid is
disproportionately hit *just before the price falls*, a resting ask *just before
the price rises*. The maker is on the wrong side of the move by construction.

Our 1 Hz top-of-book data **cannot see trades**, so adverse selection is bounded
from two sides:

**Optimistic bound — unconditional mid markout.** Take every two-sided tick and
measure the YES mid change forward in the same window:

| horizon | mean mid move | E[\|mid move\|] | std |
|---|---|---|---|
| +15 s | −0.090 c | 3.43 c | 6.78 c |
| +30 s | −0.186 c | 6.00 c | 9.59 c |
| +60 s | −0.506 c | 9.30 c | 13.78 c |

The *mean* move is tiny (the book is a near-martingale), but the *typical*
move is large — **±6 c at 30 s, ±9 c at 60 s**. A maker who could be filled at a
*random* tick would face ~0 c of directional drift. But a maker is **not** filled
at a random tick.

**Pessimistic / realistic bound — fill-event markout.** The cleanest observable
fill proxy with this data: the best bid moves *down through* a level we had
quoted (we modelled a resting quote 1 c inside the book) → the book traded
through us → we were filled as a buyer *immediately before* that move. Symmetric
for an ask. We then measure where the mid sits 15/30/60 s later. **46,224
simulated bid fills, 47,065 ask fills.** Markout = realised value of the fill vs
the later mid (positive = good; this number *already includes* the 1 c
inside-quote edge):

| horizon | bid-fill markout | ask-fill markout |
|---|---|---|
| +15 s | **−2.29 c** | **−2.21 c** |
| +30 s | **−2.32 c** | **−2.18 c** |
| +60 s | **−2.37 c** | **−1.97 c** |

**Headline +30 s fill markout, both sides: −2.25 c per share.** Every fill, on
both sides, at every horizon, is **negative even after crediting the 1 c
inside-quote edge**. When the book trades through your quote you have bought
right before a ~3 c drop (or sold right before a ~3 c rise); the 1 c you quoted
inside does not come close to covering it.

**Honest caveat on the bound.** The fill proxy *conditions on the book moving* —
it can only detect a fill that coincides with a quote-through, never a benign
fill where a taker hits you with no follow-through. So −2.25 c is a **pessimistic
bound**; the unconditional −0.19 c at +30 s is the **optimistic bound**. The true
adverse selection for a real maker sits between them — but note that the
optimistic bound assumes the maker is filled like a *random* tick, which no
maker is. Informed flow is the rule, not the exception, in a 1-tick book.

### Net rough economics

Per fill, the markout *is* the realised value of the fill vs mid; add the rebate:

```
per-fill PnL  =  +30s fill markout  +  maker rebate
              =      −2.25 c        +     0.35 c     =  −1.90 c / share
```

The maker must then **flatten the resulting inventory**. Two cases:

- **Flatten by crossing as a taker** (the reliable way): pay the taker fee +
  half-spread ≈ 2.25 c → **net ≈ −4.15 c per fill**.
- **Flatten by another passive maker fill** (best case, both legs passive): the
  round trip is ≈ 2× the per-fill edge → **≈ −3.81 c per share**, i.e.
  **≈ −$0.75 per $10 round trip** — and this *still* ignores queue position,
  cancel/repost latency, and the fill-probability haircut.

**Net of adverse selection, the crude economics are negative under every exit
assumption.** Spread captured (≈ 1 c) − adverse selection (≈ 2–2.3 c per fill) is
already negative *before* fees, *before* inventory, *before* the rebate is even
enough to matter (0.35 c). Even on the optimistic unconditional bound (−0.19 c
drift), the spread is 1 c and the half-spread you can realistically expect to
keep is razor-thin against a ±6 c typical 30 s move.

---

## 3. Inventory / resolution risk

A 15m binary settles to **0 or 1**. Any inventory still open at the bell is
marked to the outcome — a ~50 c expected loss on the wrong side of a coin flip.
A market maker *must* flatten before close. Can it?

| window phase | two-sided & undecided book | median spread | median bid depth | bid depth < $10 |
|---|---|---|---|---|
| final 60 s | **31.4 %** | 2.00 c | $25 | 24.9 % |
| final 30 s | **25.1 %** | 2.00 c | $25 | 25.7 % |
| last observed tick | **16.1 %** | 2.15 c | $23 | 23.3 % |

- In the final minute, **only ~31 % of ticks** still have a genuine two-sided,
  undecided book. The other ~69 % are one-sided or already collapsed toward 0/1.
- **83.9 % of windows** are, at the last tick, priced **<3 c or >97 c** — the
  market has effectively resolved and there is **no exit liquidity for the
  losing side**. If your inventory is on that side you cannot sell it; you eat
  the binary.
- Even where a book exists at close, depth is **~$25** and a quarter of the time
  below the $10 stake.

Combined with **91.6 % stale quotes**, flattening a built-up position in the
final seconds is **not reliable**. The maker is structurally exposed to carrying
inventory into a 0/1 settlement — and the side you are most likely to be left
holding is the side that is losing (that is *why* it became cheap and illiquid).
Inventory risk here is not a tail; it is the modal outcome of a position that
isn't actively flattened with minutes to spare.

---

## 4. Operational reality

Market-making is a **latency- and operations-heavy** activity:

- **Fast quoting and cancellation.** Quotes must be repriced and pulled the
  instant the book moves, or you are the stale quote that informed flow picks
  off — exactly the −2.25 c markout in §2.
- **Queue management.** PnL depends on *queue position* at each price level —
  unobservable in our data, and a first-class concern in any real MM system.
- **Active inventory control.** Continuous skewing of quotes to mean-revert
  inventory toward zero, plus a hard flatten-before-close routine every 15
  minutes, on every window, on every symbol.

The project's stated philosophy (`GOAL.md`, `CLAUDE.md`) is a **patient,
human-like, $10-per-trade** bot — explicitly *not* latency-sensitive, explicitly
a small number of considered trades. **Market-making is the opposite discipline.**
It is high-frequency, infrastructure-heavy, and adverse-selection-dominated. The
mismatch is fundamental, not a tuning detail. Running a competitive MM on
Polymarket crypto would mean building (and operating 24/7) a different category
of system than the one this project is.

---

## 5. Data sufficiency

**What the 1 Hz top-of-book dev data CAN tell us:**

- The spread distribution on two-sided books (median 1 c, ~3 % of mid).
- Top-of-book depth (~$26 median, thin; SOL/XRP ~$13).
- The *unconditional* forward mid-move distribution (±6 c at 30 s).
- A *fill-event-proxy* markout, bounding adverse selection from the pessimistic
  side (−2.25 c/fill at +30 s).
- End-of-window book health and the resolution-flatten problem (only ~31 % of
  final-minute ticks have a tradeable two-sided book).

**What it fundamentally CANNOT tell us:**

- **Queue position** — where in the FIFO queue our resting order sits, and hence
  the real *fill probability*. This is the single biggest unknown in MM PnL.
- **Trade-by-trade fill sequencing** — who traded, on which side, how much, in
  what order. We infer fills from book *moves*, which is a proxy, not a fact.
- **The full L2 book** — depth beyond top of book, needed to model walk-the-book
  and the true cost of flattening size.
- A real fill simulation. With 91.6 % stale 1 Hz snapshots, sub-second quoting
  and cancellation dynamics are entirely invisible.

A proper market-making assessment **requires the full L2 order book** (now being
collected per `CLAUDE.md`), a **queue-position model**, and a **real fill
simulator** driven by the trade tape. None of that is available today.

---

## VERDICT — **NO-GO** (with a NEEDS-BETTER-DATA footnote)

**The crude economics are already negative, and the operational mismatch is
fundamental. Do not pursue market-making on Polymarket 15m crypto.**

The reasoning, in order of decisiveness:

1. **The crude economics are negative before you even reach the hard problems.**
   Spread captured ≈ 1 c. Adverse selection ≈ 2.0–2.3 c per fill on the
   realistic (fill-event) bound. The maker rebate (0.35 c) does not bridge the
   gap. Net per fill ≈ −1.9 c; net per round trip ≈ −3.8 c (best case) to
   −4.2 c (taker-flatten) — about **−$0.75 per $10 round trip**. Even on the
   *optimistic* unconditional bound, a 1 c spread against a typical ±6 c 30 s
   move is not a business. **Spread − adverse selection is negative.**

2. **Adverse selection is structural, not a parameter.** A 1-tick book in a fast
   crypto market means whoever crosses the spread is, on average, informed. The
   maker is on the wrong side of the next move by construction — that is the
   −2.25 c markout. There is no quoting trick that earns a 1 c spread while
   avoiding a ±6 c informed flow.

3. **Inventory into a binary resolution is a structural tail you cannot hedge.**
   Only ~31 % of final-minute ticks have a tradeable book; 84 % of windows have
   effectively resolved by the last tick with no losing-side liquidity. Any
   un-flattened inventory eats the 0/1 settlement, and it is biased toward the
   losing side.

4. **The operational profile is the opposite of this project.** MM is
   latency-sensitive, queue-driven, infrastructure-heavy. The project is a
   patient, human-like, $10/trade bot. This is a different system, not a new
   strategy in the same one.

5. **Even a clean L2 dataset would have to overturn item 1.** The
   NEEDS-BETTER-DATA footnote: a full L2 book + queue model + real fill simulator
   *could* in principle refine the −2.25 c adverse-selection estimate. But it
   would have to find that real fills are *far* more benign than the fill-event
   proxy suggests **and** that BTC's deeper book changes the picture — and even
   the optimistic unconditional bound leaves only a 1 c spread against ±6 c
   moves. Better data is worth collecting for completeness, but the burden of
   proof is high and the crude answer is firmly negative. **It is not where the
   next research hour should go.**

**Bottom line.** This is the fourth idea in the project to come back negative,
and — unlike the first three — it is an *honest* negative, not a data artifact:
the spread is real (1 c), the adverse selection is real (~2 c), the inventory
risk is real (a 0/1 binary), and they do not net positive. Market-making on
Polymarket 15m crypto Up/Down is **NO-GO**. The hold-out stays sealed.
