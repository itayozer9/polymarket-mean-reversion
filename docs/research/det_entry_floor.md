# Determinism edge — entry-price FLOOR study (does min_ask 0.50 → 0.65 help?)

**Date:** 2026-05-29 · **Script:** `research/analysis/det_floor_sweep.py` · **Verdict: NO — do not add a floor. Keep `min_ask=0.50`.**

## The question

The live `det_lwd_v1` buys the favourite at `min_ask ≤ ask ≤ max_ask` = `[0.50, 0.90]`
in the last 60s and holds to resolution. Intuition: an entry at ~0.51 is a near
coin-flip → risky → raise the floor to ~0.65 so we only take "confident" favourites.
That reasoning is correct for a **normal directional bet** (where price ≈ true
probability). It is **backwards for this edge.**

## Why it inverts

The determinism edge is **the book LAGGING spot**, not a directional view. In the
last 60s, if spot is ≥5bps past the strike, the outcome is nearly locked. The
*price* is the book's pricing error, not the true win probability. So the cheapest
favourites are the book's **biggest errors** — the purest pickoffs.

**Calibration — realized favourite WR by entry-ask bucket (pooled dev+holdout, 336 windows, one row/window at the first qualifying tick):**

| entry ask | n | WR | WR − ask (edge/share) | net/share (after fee) |
|---|---|---|---|---|
| **0.50–0.55** | 16 | **0.938** | **+0.414** | **+0.396** |
| 0.55–0.60 | 17 | 0.824 | +0.248 | +0.231 |
| 0.60–0.65 | 21 | 0.810 | +0.191 | +0.174 |
| 0.65–0.70 | 23 | 0.913 | +0.241 | +0.226 |
| 0.70–0.75 | 30 | 0.767 | +0.047 | +0.033 |
| 0.75–0.80 | 50 | 0.900 | +0.128 | +0.115 |
| 0.80–0.90 | 158 | 0.924 | +0.071 | +0.062 |

WR is **roughly flat (~0.85–0.94) across the whole price range** — it does NOT
track the price (an efficient market would show WR ≈ ask, i.e. ~0 edge). So the
gap `WR − ask` is **largest at the cheapest prices**. A floor removes exactly the
trades the edge exists to capture.

(Note: favourite ask is structurally ≥ ~0.50 — you pay >50¢ for the favoured side
— so 0.50 is already the natural floor; there is nothing cheaper to take.)

## The floor sweep (real L2 fills, hold-to-resolution, window-clustered 90% CI)

**DEV (2026-05-23..27, 5 days):**

| min_ask | trades | WR | $/trade | 90% CI | total$ | $/day |
|---|---|---|---|---|---|---|
| **0.50** | 248 | 0.887 | **+1.413** | [+0.92,+1.88] | +350.55 | +70.11 |
| 0.55 | 241 | 0.888 | +1.231 | [+0.74,+1.66] | +296.68 | +59.34 |
| 0.60 | 235 | 0.894 | +1.106 | [+0.66,+1.54] | +259.80 | +51.96 |
| 0.65 | 226 | 0.898 | +0.966 | [+0.52,+1.38] | +218.39 | +43.68 |
| 0.70 | 217 | 0.903 | +0.860 | [+0.43,+1.25] | +186.68 | +37.34 |
| 0.75 | 195 | 0.923 | +0.923 | [+0.53,+1.30] | +179.94 | +35.99 |
| 0.80 | 158 | 0.924 | +0.661 | [+0.24,+1.05] | +104.39 | +20.88 |
| 0.85 | 113 | 0.920 | +0.414 | [−0.11,+0.85] | +46.73 | +9.35 |

**HOLDOUT (2026-05-28..29, ~2 days — sealed, opened once):**

| min_ask | trades | WR | $/trade | 90% CI | total$ |
|---|---|---|---|---|---|
| **0.50** | 88 | 0.909 | **+1.889** | [+1.11,+2.61] | +166.24 |
| 0.65 | 82 | 0.902 | +1.280 | [+0.55,+1.97] | +104.98 |
| 0.75 | 71 | 0.901 | +0.763 | [−0.01,+1.44] | +54.18 |
| 0.85 | 37 | 0.919 | +0.415 | [−0.51,+1.13] | +15.35 |

**Per-symbol (pooled):** floor 0.50 beats 0.65 on **all four** coins (btc/eth/sol/xrp)
in both $/trade and total — no symbol benefits from the floor.

## Conclusion

Raising the floor 0.50 → 0.65:
- **Profit:** −32% $/trade, −38% total (dev); −32% / −37% (holdout). Monotonically worse.
- **Win rate:** +1.1 pts (dev) but −0.7 pts (holdout) — WR is already ~flat vs price, so no reliable lift.
- **Robustness:** worse on every symbol; CI lower bound shrinks; significance lost by floor ≥0.75 on holdout.

**Keep `min_ask = 0.50`.** The edge's profit is concentrated in the cheapest
favourites; a floor is anti-edge. The incumbent `[0.50, 0.90]` band is well-chosen
— trimming *either* end only discards +EV, CI-positive trades.

### Caveat / non-action considered
- The `[0.70,0.75)` bucket is soft (WR 0.79, net +0.06, n=29, CI [0.66,0.90]).
  Excluding a mid-price band is **not** worth acting on — n is small, the CI is
  wide (consistent with noise), and it's still positive. Revisit only if it
  persists over many more weeks.

---

# Ceiling study — raise `max_ask` 0.90 → 0.95? **Verdict: NO. Keep 0.90.**

Same mechanism, opposite end. Extending the calibration above 0.90 (pooled):

| entry ask | n | WR | WR − ask | net/share |
|---|---|---|---|---|
| 0.80–0.85 | 54 | 0.926 | +0.104 | +0.094 |
| 0.85–0.90 | 90 | 0.911 | +0.040 | +0.032 (marginal, still +) |
| **0.90–0.95** | 125 | 0.880 | **−0.042** | **−0.047** ← negative |
| **0.95–0.98** | 224 | 0.942 | **−0.018** | **−0.021** ← negative |

Above 0.90 the favourite's realized WR is **below** its price — the market prices
the dear favourites correctly (even slightly rich). The decisive cut, the
**(0.90, 0.95] band on its own** (the trades a cap raise would *add*):

- DEV: n=226, WR 0.934, avg_ask 0.933, **$−0.038/trade** (CI [−0.33,+0.24])
- HOLDOUT: n=68, WR 0.853, avg_ask 0.932, **$−0.881/trade** (CI [−1.68,−0.10] — entirely negative)

Ceiling sweep (floor 0.50): raising max_ask monotonically erodes $/trade
(dev 1.413→0.847 at 0.95; holdout 1.889→0.732) and shrinks the CI; holdout total
drops $166→$95. **Why:** payoff asymmetry — at ask 0.93 you pay 93¢ to win 7¢ but
lose 93¢ on a miss, so you need WR > ~0.935 just to break even, and realized WR
there is 0.85–0.93. One loss erases ~13 wins. The edge lives in **[0.50, ~0.85]**;
[0.85,0.90] is marginal-but-positive (so 0.90 stays); above 0.90 is dead.

---

# Fee model — is the EXACT fee counted? **Yes.** Here is precisely what is / isn't.

Fee constant is from the **live market metadata**, not assumed
(`docs/research/phase0a_settlement_feed.md:59`, `research/audit/cost_notes.md:26`):
`feeSchedule: { exponent: 1, rate: 0.07, takerOnly: true, rebateRate: 0.2 }`.
Crypto Up/Down markets use **0.07** — the highest non-zero taker rate on Polymarket.

**Counted in every $/trade above** (`research/sim/fills_v2.py`):
1. **Entry taker fee** `0.07·p·(1−p)·shares` — `FeeSchedule.taker_fee`, applied in `walk_buy`.
2. **Spread + slippage** — `walk_buy` walks the REAL top-10 L2 ladder; you pay the
   actual ask and slip into deeper levels if the top is thin (not a top-of-book assumption).
3. **One-way cost** — hold-to-resolution: winners redeem at $1 with **no exit fee**,
   losers → $0. Only the entry fee is charged (`settle_pnl`). This is the true mechanic.
4. **$5 min order / 1¢ tick** — documented; non-binding at $10 stake (20 shares ≫ $5; prices already on the 1¢ grid).

Fee shape note: `0.07·p·(1−p)` is **largest at p=0.5** (1.75¢/share) and **smallest at
the extremes** (≈0.5¢/share at p=0.92). So fees are *not* what kills the dear
favourites (asymmetry is), and at the cheap end the ~1.7¢ fee is a rounding error
against a +40¢ edge. The **live engine charges the identical fee**
(`determinism_state.py:_fee`, `rate=0.07`, same `p·(1−p)` form) — backtest and live agree.

**NOT counted (matters only for the real-money test, not paper):**
1. **Gas / redemption** — Polygon gas to redeem winning shares (~cents). Small but
   real drag at $10 stakes; ignore for paper, budget for live.
2. **Real fill probability + latency** — the backtest fills at the observed
   top-of-ladder at the signal tick. Live, the favourite ask may tick up before our
   order lands. **This is THE pre-live unknown** and it bites the cheap-favourite
   trades hardest (they exist *because* the book is about to reprice). Validate fill
   realism in the small live test before scaling.
3. **Maker rebate (20%)** — irrelevant; determinism is a pure taker (it lifts the ask).
