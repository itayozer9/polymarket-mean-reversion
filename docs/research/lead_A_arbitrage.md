# Lead A — Within-market (YES/NO) arbitrage

**Script:** `research/analysis/arbitrage_scan.py` (`run()` + `__main__`)
**Tests:** `tests/research/test_arbitrage_scan.py` (4 passing)
**Data:** `data/research/ticks_15m.parquet` (outcome-corrected), `data/research/ticks_5m.parquet`
(outcomes NOT corrected — irrelevant here; the arb scan never touches outcomes, only the book).
**Split:** dev = May 15-20, sealed hold-out = May 21-22 (`research/lib/splits.py`).

---

## The hypothesis

On Polymarket, $1 mints one YES + one NO share, and a YES+NO pair merges back to $1.
Therefore two non-directional, risk-free arbs should exist:

- **Buy-both:** if `yes_best_ask + no_best_ask < 1` — buy one of each, merge → guaranteed $1
  at a sub-$1 cost.
- **Sell-both:** if `yes_best_bid + no_best_bid > 1` — mint a pair for $1, sell both, collect > $1.

Fees (Polymarket crypto, taker): `fee = 0.07 · p · (1−p)` per share, charged on **each leg**.
Makers pay 0. Both are reported.

---

## Key structural finding — the YES/NO books are mostly a mirror

Before any arb can exist, the YES and NO order books must be **independently quoted**.
They are not, most of the time.

On **93.0%** of 15m ticks (93.2% of 5m ticks) the NO book is the **exact synthetic
complement** of the YES book:

```
no_best_bid = 1 − yes_best_ask        no_best_ask = 1 − yes_best_bid
no_bid_depth = yes_ask_depth          no_ask_depth = yes_bid_depth
```

On any such mirror book, `yes_ask + no_ask = yes_ask + (1 − yes_bid) = 1 + spread > 1`
**always** — a buy arb is mathematically impossible, and symmetrically the sell arb is
impossible. An arb can only ever appear in the ~7% of ticks where the two tokens carry
genuinely independent quotes. And indeed: **100% of the net-positive arb candidates found
below sit in non-mirror rows** — confirming the logic, not contradicting it.

This is the single most important fact about this lead. The "$1 ⇄ YES+NO" identity is
already baked into how the book is recorded most of the time, so there is simply nothing
to harvest on the bulk of ticks.

---

## Artifact rejection accounting

A raw candidate is any tick satisfying the price inequality. The overwhelming majority are
**degenerate books**, not arbs. A genuine candidate requires:

- both YES and NO quoted strictly inside `(0.02, 0.98)` — rejects **decided-market** rows
  where one outcome's ask has collapsed to ≈0 (the tell: gross "edge" of 0.99);
- neither side crossed (`bid < ask` on YES and on NO);
- non-zero depth on the **exact legs you must hit** (asks for buy, bids for sell).

| Timeframe | Side | Raw candidates | → decided-market | → crossed | → zero-depth leg | **Genuine** |
|-----------|------|---------------:|-----------------:|----------:|-----------------:|------------:|
| 15m | BUY  | 132,249 | 132,187 | 132,187 | 132,187 | **62** |
| 15m | SELL | 65      | 1       | 0       | 0       | **64** |
| 5m  | BUY  | 122,819 | 122,584 | 122,584 | 122,584 | **235** |
| 5m  | SELL | 126     | 1       | 0       | 0       | **124** |

**99.95% of raw buy "candidates" are decided markets** — a window already resolved, with a
free ask sitting at ≈$0 on the winning side. Buying that is not an arb; it is buying a
share for what it is worth. They are correctly thrown out.

---

## Genuine candidates — economics

"Genuine" only means a real two-sided book. It does **not** mean profitable: the gross edge
must still clear the taker fee on both legs.

| TF | Side | Genuine | Gross mean | Net (taker) mean | **Net > 0 after taker fee** |
|----|------|--------:|-----------:|-----------------:|----------------------------:|
| 15m | BUY  | 62  | 0.0206 | **−0.0079** | 15 / 62 |
| 15m | SELL | 64  | 0.0178 | **−0.0105** | 11 / 64 |
| 5m  | BUY  | 235 | 0.0307 | **+0.0018** | 117 / 235 |
| 5m  | SELL | 124 | 0.0194 | **−0.0065** | 33 / 124 |

The gross edge is tiny — mean ~1.8–3.1¢ per pair. The taker fee on two legs around p≈0.5
is ~`2 × 0.07 × 0.25 = 3.5¢`. So **on average the taker fee eats the entire gross edge**;
three of the four buckets have a *negative* mean net-taker edge. Only the most extreme
candidates survive.

### For the net-positive (taker) candidates only

Executable size is the thinner of the two legs (`min(yes_ask_depth, no_ask_depth)` for buy).

| TF | Side | Net>0 events | Exec size (median) | Total net $ (taker, depth-capped) | **$/day (taker)** | $/day (maker, all genuine) |
|----|------|-------------:|-------------------:|----------------------------------:|------------------:|---------------------------:|
| 15m | BUY  | 15  | $5.0  | $9.43  | **$1.18** | $3.85 |
| 15m | SELL | 11  | $10.0 | $3.07  | **$0.38** | $2.57 |
| 5m  | BUY  | 117 | $10.0 | $36.82 | **$4.60** | $14.75 |
| 5m  | SELL | 33  | $5.7  | $7.30  | **$0.91** | $5.04 |

These totals are over **all 8 days (dev + hold-out)**. The depth-capped $ figure already
assumes you fill the *entire* visible top-of-book on the thinner leg — an optimistic upper
bound. Best case across all four buckets: **15m+5m, buy+sell, taker ≈ $7/day total**;
maker ≈ $26/day total. At realistic $10–$100 per-event sizing the binding constraint is
the depth, not the capital: median executable size is **$5–$10**, so a single event nets
**a few cents to ~$0.30**, and you cannot deploy $100 — there is nothing to buy.

---

## Persistence

| TF | Side | Net>0 per day (dev) | Net>0 per day (hold-out) | Book persistence (median) | 1-second flickers |
|----|------|--------------------:|-------------------------:|--------------------------:|------------------:|
| 15m | BUY  | 10 over 6d (~1.7/d) | 5 over 2d (~2.5/d)  | 1 s | 60% |
| 15m | SELL | 6 over 6d (~1.0/d)  | 5 over 2d (~2.5/d)  | 1 s | 64% |
| 5m  | BUY  | 74 over 6d (~12/d)  | 43 over 2d (~22/d)  | 4 s | 23% |
| 5m  | SELL | 15 over 6d (~2.5/d) | 18 over 2d (~9/d)   | 1 s | 64% |

The arb **does appear on essentially every dev day and persists into the sealed hold-out**
(May 21-22) — it is not a one-day artifact. The 5m buy bucket is the strongest: ~12–22
events/day, and it actually *increased* out-of-sample. So as a *phenomenon* it is real and
recurring. The hold-out was used only as a persistence check on a risk-free construct, as
the brief permits; dev and hold-out are reported separately above.

---

## Executability reality check — this is where it dies

A candidate at a stale quote is worthless if it vanishes before both legs are hit.

- **15m: median book persistence = 1 second; 60–64% of net-positive arb states are
  single-tick flickers.** The book is gone by the next 1 Hz sample. A bot would have to
  land *two separate marketable orders* (YES leg + NO leg) inside a sub-second window, on a
  venue whose books Phase 0 measured as ~87% stale. If you hit one leg and the other has
  moved, you are no longer arbitraged — you are holding a naked directional position. That
  is the opposite of risk-free.
- **5m buy is somewhat better** — median persistence 4 s, 40% of states last ≥5 s — but the
  prize is still ~$0.30 net for a successful round-trip, against real two-leg execution and
  slippage risk, and after Polymarket gas/relayer overhead per fill (not modelled here, and
  it would push several of these underwater).
- The depth is the killer regardless of speed: **$5–$10 of top-of-book**. Even a flawless
  bot cannot turn this into meaningful capital deployment. The maker variant ($26/day) is a
  fiction here — to "capture" the arb as a maker you must *post* the resting orders and
  *wait* to be hit, which is plain market-making (covered by Lead / market_making_probe),
  not arbitrage, and abandons the risk-free property entirely.

---

## VERDICT — NOT a real, executable, profitable edge

Within-market YES/NO arbitrage **exists as a measurable phenomenon but is not an
exploitable edge.** Three independent reasons, any one of which is fatal:

1. **Structural.** 93% of ticks are exact-mirror books on which an arb is mathematically
   impossible. The "$1 ⇄ YES+NO" identity is already enforced in the recorded book.
2. **Fees.** On the ~7% of independently-quoted books, the mean gross edge (~2–3¢) is
   *smaller than* the two-leg taker fee (~3.5¢). Mean net-taker edge is negative in three
   of four buckets. Only a handful of extreme ticks clear the fee.
3. **Executability.** The net-positive states are mostly 1-second flickers on ~87%-stale
   books with $5–$10 of depth. You cannot reliably hit both legs before the state vanishes,
   and a half-filled arb is a naked directional bet.

**Realistic $/day at $10–$100 sizing: effectively $0.** The optimistic depth-capped upper
bound is ~$7/day total (taker, all timeframes and sides combined) — and that already
assumes a bot that fills the entire top-of-book on every event with perfect two-leg timing
and zero gas cost. The $10–$100 sizing the brief asks about is irrelevant: median
executable size is $5–$10, so capital is never the constraint — there is simply nothing
to buy. Net of realistic two-leg slippage and Polymarket transaction overhead, the true
expectation is **at or below zero**.

This lead is **killed.** It is not a data artifact in the sense of corrupt data — the
candidates are genuine book states — but it is an artifact of *book microstructure*: tiny,
fleeting, sub-fee dislocations on a thin venue, not a harvestable inefficiency. Do not
build an arbitrage bot for it.
