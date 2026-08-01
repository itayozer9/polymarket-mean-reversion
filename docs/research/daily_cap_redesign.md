# Daily-loss cap redesign — spec for live deployment

**Date:** 2026-05-30 · **Status:** design draft (NOT wired in; paper bot untouched)
**Owner question:** the live goal allows "maybe a max loss per day." The current
`DailyLossGuard` does **not** deliver one. This spec defines a guard that actually
bounds the daily loss, separates the two design knobs cleanly, and quantifies the
EV cost so the owner can choose at the 2026-06-05 review.

**Trigger:** on 2026-05-30 (UTC day) `det_sqp_v1_capped` tripped its "$50/day" cap
at −$50.04 (04:48 UTC) yet drifted to **−$76.95** — $27 past the nominal floor,
flooring only $6.71 (≈8%) of an −$83.66 day. Root cause traced to code (below).

---

## 1. Why the current guard is broken — three code-confirmed defects

`DailyLossGuard` lives in `engine/determinism_state.py:40-66`; one instance is
created per strategy (`engine/strategy.py:74-80`) and **shared** across every
per-window state (`strategy.py:171,179`). `blocked()` is checked at entry
(`determinism_state.py:150`, `stale_quote_state.py:103`); `record()` is called at
settle (`determinism_state.py:234`, `stale_quote_state.py:174`).

```python
def record(self, ts_ms, pnl):       # called ONLY at settle()
    d = self._utc_date(ts_ms)
    if d != self._date: self._date, self._pnl = d, 0.0
    self._pnl += pnl
def blocked(self, ts_ms):
    if self.cap is None: return False
    if self._utc_date(ts_ms) != self._date: return False
    return self._pnl <= -abs(self.cap)   # recomputed every call; no latch
```

**Defect 1 — blind to open positions.** `_pnl` only accumulates *settled* PnL
(`record` runs at `settle()`). The det/sq edges **hold to resolution** (~500–800 s)
with several positions open at once, so at any entry instant `_pnl` lags true
exposure by ~10 min and multiple unsettled losers. The gate stays open while a
pipeline of open losers is about to land; by the time they do, the day is already
past the cap.

**Defect 2 — not a latch.** `blocked()` re-reads the live running sum on every
call; there is no sticky "tripped" flag. A settling tail-winner lifts `_pnl` back
above −cap and **re-arms** entries. The docstring ("once the day's realized PnL
hits -cap, no new entries until the next UTC day") describes intent the code does
not implement.

**Defect 3 — restart amnesia.** `strategy._replay_existing_trades`
(`strategy.py:84-133`) rebuilds the *portfolio* from `trades.jsonl` on startup but
**never feeds the guard** — `self._det_guard._pnl` resets to 0. A mid-day restart
(e.g. today's 07:43 UTC v2 deploy) silently un-trips the cap; it then re-counts
only post-restart settlements.

All three contributed to today's −$76.95.

---

## 2. What we actually want — and the key decomposition

Goal: **the strategy's realized PnL for a UTC day should not fall below −cap.**

The fix decomposes into **two orthogonal knobs** that the current design conflates:

1. **Open-exposure accounting (the real fix).** Reserve the *worst-case* loss of
   every open position against the cap *at entry time*. This is what makes the
   bound real. Without it (settled-only), you breach — guaranteed, on any
   hold-to-resolution strategy with concurrency.
2. **Latch vs resume-when-safe (a policy choice).** Once the limit is reached, do
   you stop for the rest of the UTC day (latch), or resume as soon as a settlement
   restores headroom? With correct open-exposure accounting the **bound holds
   either way** — so this is purely a behavioural/EV preference, not a correctness
   requirement.

The current guard gets **both wrong**: no open-exposure accounting (so it breaches)
*and* no latch (so it re-arms into the breach).

---

## 3. The core fix — worst-case open-exposure accounting

Track, per UTC day, for the strategy:

- `settled` — Σ pnl of positions **resolved** today.
- `open_worst` — Σ over **currently-open** positions of their max loss.
  For these strategies max loss = `bet_usd + fee_entry` (a loser redeems at $0 and
  pays no exit fee — `_fee(·, 0.0, ·)=0`), i.e. ≈ $10.x. Known at entry.
- `worst_case := settled − open_worst` — the day's PnL **if every open position
  loses**.

**Entry rule.** Allow a new entry of max loss `m` only if
`worst_case − m ≥ −cap`. (Block otherwise.)

**Why this is a hard bound (invariant proof sketch).**
- Every accepted entry leaves `worst_case ≥ −cap` (that's the rule).
- A settlement of position `p` (max loss `m_p`, actual pnl `a_p ≥ −m_p`) changes
  `worst_case` by `a_p + m_p ≥ 0` — settlements only **raise** `worst_case`.
- Entries only lower it, and we block any that would push below −cap.

So `worst_case ≥ −cap` holds for all time, and since `settled ≥ worst_case` always
(and `settled_eod = worst_case_eod` once everything resolves), **realized daily PnL
never drops below −cap.** ∎

A pleasant consequence: with worst-case accounting, **re-arming is safe** — when a
winner settles and lifts `worst_case`, resuming entries cannot breach the bound. So
re-arming (Defect 2) is only dangerous in the *absence* of open-exposure
accounting. Fixing Defect 1 neutralises Defect 2.

---

## 4. The latch — a separate policy knob (and the EV tension)

With the bound guaranteed by §3, latching becomes a **discipline preference**:

- **`latch_for_day = False` (resume-when-safe).** Keep trading whenever
  `worst_case` has headroom. Preserves the most EV — important because sq is
  positive-skew and a daily stop can *truncate its recovery tail* (see
  memory `det-daily-cap-vs-positive-skew`; live-observed EV sign-flips). The loss
  is still hard-bounded by −cap. **Recommended default for sq.**
- **`latch_for_day = True` (circuit-breaker).** Once `worst_case ≤ −cap`, stop for
  the rest of the UTC day regardless of recovery. Operationally cleaner ("done for
  the day"), avoids death-by-a-thousand-cuts on a clearly bad regime, but costs the
  recovery EV the no-latch mode keeps. Reasonable for a risk-averse live start.

This is the honest tension: **making the cap actually bound the loss is unambiguous
(do §3); whether to also latch is a real EV-vs-discipline tradeoff** the owner
should decide with the §9 backtest numbers in hand.

---

## 5. Side-effect to understand — worst-case ≈ an implicit concurrency cap

Reserving `bet+fee` per open position means the cap doubles as a position-count
limit: with cap $50 and ~$10 stakes you can hold at most ~5 concurrently before new
entries block, *even if settled PnL is fine*. For sq (frequent, low-priced
favourites, many concurrent windows) this may bind often and throttle volume.

This is **the same lever the memory note already recommended** ("position-
concurrency limits rather than a tight daily $ stop") — here it falls out of the $
cap for free. Options if it's too tight:
- raise the cap (e.g. $75–100) so concurrency headroom is larger;
- mark open positions at **EV / current mid** instead of worst-case — softer,
  *probabilistic* bound (allows more concurrency, no longer a hard guarantee);
- accept it as the intended concurrency control.

Surface (don't hide) which mode is active in the live report.

---

## 6. Restart-safety (fixes Defect 3)

On startup, after rebuilding the portfolio, replay **today's** closed trades into
the guard so `settled` is correct:

```python
# in strategy._replay_existing_trades(), per replayed Trade `trade`:
if self._det_guard is not None:
    self._det_guard.replay_settled(trade.exit_ts_ms, trade.pnl)   # day-keyed internally
```

Open-at-restart positions are **orphaned by restart already** (the engine persists
only closed trades; in-flight positions are lost and their windows settle via
`on_close` during downtime). So we rebuild the realized portion only and accept that
worst-case exposure starts empty post-restart — acceptable, and strictly safer than
today's full reset. Document it.

**Known edge case (both old and new):** a position spanning UTC midnight is
attributed by whichever `ts` is passed. Key the daily bucket consistently by the
**entry** date (store it on the position) to match the §5b status slice, which
buckets by `entry_ts_ms`. Effect is tiny for 15m windows; note it, don't over-build.

---

## 7. Reference implementation (proposed — not yet wired)

```python
class DailyLossGuard:
    """Per-strategy daily-loss circuit breaker (UTC day), open-exposure aware.

    Hard bound: realized PnL for the UTC day never drops below -cap, by reserving
    the worst-case loss of every open position against the cap at entry time.
    `latch_for_day` optionally stops for the rest of the day once the limit is
    reached instead of resuming when a settlement restores headroom.
    cap=None -> fully disabled (mean-reversion strategies, existing tests).
    """
    def __init__(self, max_daily_loss_usd=None, latch_for_day=False):
        self.cap = max_daily_loss_usd
        self.latch = latch_for_day
        self._date = None
        self._settled = 0.0
        self._open_worst = 0.0
        self._open = {}           # entry_date keyed bucket -> {pos_id: max_loss}
        self._latched = False

    @staticmethod
    def _utc_date(ts_ms):
        import datetime as dt
        return dt.datetime.fromtimestamp(ts_ms/1000, tz=dt.timezone.utc).strftime("%Y-%m-%d")

    def _roll(self, ts_ms):
        d = self._utc_date(ts_ms)
        if d != self._date:
            self._date, self._settled, self._open_worst, self._latched = d, 0.0, 0.0, False
            self._open = {}

    def _worst_case(self):
        return self._settled - self._open_worst

    # entry side (replaces blocked)
    def would_block(self, ts_ms, new_max_loss):
        if self.cap is None: return False
        self._roll(ts_ms)
        if self._latched: return True
        return (self._worst_case() - new_max_loss) < -abs(self.cap)

    def on_entry(self, ts_ms, pos_id, max_loss):
        if self.cap is None: return
        self._roll(ts_ms)
        self._open[pos_id] = max_loss
        self._open_worst += max_loss
        if self.latch and self._worst_case() <= -abs(self.cap):
            self._latched = True

    # settle side (replaces record)
    def on_settle(self, ts_ms, pos_id, pnl):
        if self.cap is None: return
        self._roll(ts_ms)
        m = self._open.pop(pos_id, None)
        if m is not None:
            self._open_worst -= m
        self._settled += pnl

    # startup
    def replay_settled(self, ts_ms, pnl):
        if self.cap is None: return
        self._roll(ts_ms)
        self._settled += pnl
```

`pos_id` = the window **slug** (unique per window — slug embeds the boundary ts;
`_traded` already prevents re-entry in a window, so one open position per slug).

---

## 8. Wiring changes (small, parity-safe)

- **`determinism_state.py` / `stale_quote_state.py`**
  - entry check: `self._guard.blocked(ts)` → `self._guard.would_block(ts, bet+fee_entry)`
    (compute `fee_entry` for the prospective fill before the block check).
  - on fill (after the entry is committed): `self._guard.on_entry(ts, self.slug, bet+fee_entry)`.
  - `settle()`: `self._guard.record(ts_ms, pnl)` → `self._guard.on_settle(ts_ms, self.slug, pnl)`.
- **`strategy.py`**
  - `DailyLossGuard(_cap)` → `DailyLossGuard(_cap, latch_for_day=<param>)`.
  - in `_replay_existing_trades`, call `replay_settled(exit_ts_ms, pnl)` per replayed trade.
- **`registry.py`** — parse a new optional yaml key per strategy, e.g.
  `daily_loss_mode: soft_settled | hard_worstcase | hard_worstcase_latch`
  (**default `soft_settled`** = today's behaviour, so existing strategies, the
  paper twins, and the replay-parity test are byte-for-byte unchanged).
- **Replay-parity test** (`tests/test_paper_engine_replay.py`) is unaffected: it
  exercises mean-reversion `PerMarketState` with `guard=None`.

---

## 9. Validation plan (gate before any live use)

**Unit tests** (new, engine):
1. `cap=None` → `would_block` always False; `on_entry/on_settle` no-ops (back-compat).
2. **Hard-bound property test:** random win/loss/entry sequences → assert realized
   daily PnL never < −cap (no-latch and latch).
3. **Safe re-arming:** no-latch, after `worst_case` hits −cap a winner settles →
   entries resume AND bound still holds.
4. **Latch:** once `worst_case ≤ −cap`, `would_block` stays True the rest of the
   day even after a winning settle; resets at UTC rollover.
5. **UTC rollover** clears settled/open/latched.
6. **Restart replay:** feeding today's closed trades rebuilds `settled`; an entry
   then blocks correctly.
7. **Open-exposure block:** k open positions reserving ≥ cap−stake → a new entry
   blocks even though `settled` is fine.

**Backtest (research repo, not the live engine — harness-first discipline).**
Replay sq on the clean window (`joined_15m.parquet`, via `fills_v2`) under three
guard configs and report per config: **max realized daily drawdown** (must be ≤ cap
for hard modes, will exceed it for `soft_settled`), total PnL, n_trades, trades
skipped, and EV/trade:
- (a) `soft_settled` (today's leaky cap),
- (b) `hard_worstcase` (no latch),
- (c) `hard_worstcase_latch`.
This quantifies the §4 EV-vs-safety tradeoff on real data for the Jun 5 decision.

### Results (run 2026-05-30 — `research/analysis/daily_cap_compare.py`)

sq ledger n=1042 over 8 UTC days (05-23..30), replayed through the actual engine
guard in each mode. **`$50/day`:**

| mode | taken | skip | total$ | $/tr | worstDay$ | breachDays |
|---|---|---|---|---|---|---|
| none (uncapped) | 1042 | 0 | +3830 | +3.68 | **−137.1** | 1 |
| soft_settled (today's leaky cap) | 745 | 297 | +3795 | +5.09 | **−63.1** | **3** |
| hard_worstcase | 737 | 305 | +3834 | +5.20 | **−45.9** | **0** |
| hard_worstcase_latch | 737 | 305 | +3834 | +5.20 | **−45.9** | **0** |

Cap sweep ($30/$50/$75/$100) — hard-mode worstDay$ / breachDays / total$:
−24.6/0/+2198 · −45.9/0/+3834 · −74.7/0/+3749 · −94.5/0/+3736.

**Findings (decision-relevant — partly overturns the prior worry):**
1. **The bound works.** Hard modes breach the cap on **0 days** at every level; the
   legacy `soft_settled` breaches on **1–3 days** at every level (worst −$63 at $50,
   −$126 at $100). Confirms the production bug at scale and the fix.
2. **The feared EV cost does NOT materialise at sensible caps.** At cap ∈ [$50,$100]
   hard-mode total is within ~2% of uncapped (**+3834 vs +3830 at $50 — neutral**),
   and **$/tr is *higher* than uncapped at every level** (5.20 vs 3.68 at $50): the
   cap removes marginal trades taken mid-drawdown without killing the open positions
   that carry the recovery tail. This **overturns** the standing concern that a cap
   must truncate sq's positive skew — that was the *leaky* cap's framing; the
   worst-case design preserves the tail. (Caveat: the small EV delta's *sign* is
   fat-tail-noisy across caps — treat as "neutral ±2%", not a precise gain.)
3. **$30 is too tight** (total +2198, −43%): the worst-case reservation then behaves
   like a ~3-position concurrency limit and over-throttles sq. **$50 is the sweet
   spot** (EV-neutral + tightest worst day among the EV-neutral caps).
4. **Latch == no-latch on this data at every cap** (identical taken/total/worstDay).
   The worst-case reservation alone bounds the loss, so the latch never binds
   differently. **Recommend `hard_worstcase` (no latch)** — the least restrictive
   mode that still delivers the bound; revisit the latch only if a future regime
   shows the no-latch mode re-arming into extra losses.

---

## 10. Rollout

1. **Do NOT change `det_sqp_v1_capped`.** It's mid-A/B; its present value is as a
   *measured example of the leaky cap*. Leave it (and its uncapped twin) running.
2. Land the new guard **behind the `daily_loss_mode` flag, default `soft_settled`**
   (zero behaviour change for everything currently enabled).
3. Add **one** new paper variant (e.g. `det_sqp_v3_hardcap`,
   `daily_loss_mode: hard_worstcase`) so the live A/B becomes
   **uncapped vs leaky-cap vs hard-cap** — adjudicated at the review.
4. Only after §9 backtest + ≥1 week forward paper shows the hard cap bounds loss
   *without* gutting EV → use it for the small ($50–100, $10/trade) live test.

---

## 11. Alternatives considered (from the cap memo)

- **Per-trade stake limit** — already fixed at $10; not a daily control.
- **Weekly / rolling cap** instead of daily — less tail-truncation on positive-skew
  sq; worth A/B-ing alongside.
- **Explicit position-concurrency cap** — simpler than a $ cap if a hard $ bound
  isn't required; note §3 already imposes one implicitly.
- **EV-mark open exposure** (vs worst-case) — softer, probabilistic bound; more
  concurrency, no hard guarantee. A middle option if worst-case throttles sq too
  hard.

**Bottom line (with the §9 numbers now in hand):** account for open-position
worst-case at entry (§3) — that alone turns a cosmetic cap into a real one
(**0 breach days** vs the legacy cap's 1–3) AND makes re-arming safe. On the 8-day
sq window the hard cap is **EV-neutral at $50** (within ~2% across $50–$100; $/tr
actually *rises* 3.68→5.20) while cutting the worst single day from **−$137 to
−$46** — so the long-standing "a daily cap must hurt sq's positive skew" concern
turns out to be a **leaky-cap artifact, not intrinsic**: blocking marginal mid-
drawdown entries while letting open positions ride preserves the recovery tail.
**Recommended live config: `daily_loss_mode: hard_worstcase`, cap ≈ $50, no latch.**
$30 over-throttles (−43%); the latch is unnecessary on current data. Re-validate on
fresh OOS days at the 2026-06-05 review before any live capital.
