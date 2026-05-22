# Leaderboard wallet analysis — market-making verdict and recommendation

**Date:** 2026-05-22
**Branch:** `leaderboard-wallet-analysis`
**Type:** Final synthesis — the deliverable for the project owner.

This report consolidates the leaderboard wallet engagement: who the profitable
wallets are, how they actually make money, whether market-making is the
explanation, and whether any of it is replicable by this project's patient
$10-per-trade bot.

**Required reading behind this verdict:**
`docs/research/leaderboard_wallets.md` (the 239-wallet analysis),
`docs/research/leaderboard_strategy_backtest.md` (the backtest of rule M15-DH-1),
`docs/research/market_making_feasibility.md` (the prior MM scoping study),
`docs/research/PHASE2_RERUN_VERDICT.md` (the prior directional-edge research),
`docs/research/FINAL_REPORT.md` (the prior consolidated report).

---

## 1. The question, and the answer

The owner asked: **do the robust wallets on Polymarket's crypto profit
leaderboard make their money by market-making, and can we replicate it?**

**No.** The market-making hypothesis is refuted — of 239 leaderboard wallets,
exactly one is a true market-maker, and the dominant winning pattern is plain
directional buy-and-hold, which a full backtest (winners *and* losers) shows has
no replicable, cost-surviving edge. There is no edge for a small patient bot in
the markets this project has studied.

---

## 2. How the profitable wallets actually make money

We pulled the **239 distinct wallets** that make up the union of the top 100 of
Polymarket's MONTH, WEEK and ALL-time crypto profit boards, fetched each
wallet's full on-chain activity cache, and classified each by an ordered
archetype cascade. The breakdown:

| Archetype | Wallets | Share | What it is |
|---|---:|---:|---|
| `directional_holder` | 167 | 70% | Buy one side of a short-dated Up/Down market, hold to resolution, redeem the winning payout. A directional bet. |
| `mint_merge_arbitrageur` | 27 | 11% | Mint YES+NO pairs and merge them back — a non-directional structured trade in longer-dated price-target markets. |
| `active_trader_scalper` | 10 | 4% | Frequent in-and-out trading. |
| `passive_liquidity_provider` | **1** | 0% | The *only* true market-maker: posts resting two-sided quotes and flattens inventory rather than holding to resolution. |
| `mixed_or_unknown` | 34 | 14% | No single dominant pattern, or non-crypto activity. |

**The dominant pattern is directional buy-and-hold.** 70% of leaderboard winners
buy a side and hold it to resolution. The exit evidence is decisive: **188 of
239 wallets are redeem-dominant** (settle at resolution rather than selling) and
**117 never sell at all**. Holding to a 0/1 settlement is the *opposite* of the
flatten-the-book behaviour that defines a market-maker.

**Which markets?** 148 of 239 wallets (62%) have the **15-minute crypto Up/Down**
series — exactly the markets this project's bot targets — as their dominant
market by buy volume. The 15m bucket is well represented even at the very top of
the all-time board (9 of the top 15). The directional winners enter **early in
the window** (median ~3 minutes into the 15-minute window, almost all within the
first 5 minutes) and put their *big* money on **heavy favourites**: the
USDC-weighted entry price is ~0.72, and ~46% of their buy volume lands at odds
≥0.80. They place many small bets across all odds but the realised PnL follows
the favourite-heavy money.

**The mint-merge cluster** (27 wallets, the second-largest group) does something
genuinely different: it mints and merges YES/NO pairs in **longer-dated
crypto price-target markets**, not the 15m Up/Down series. It is non-directional
and is the closest thing on the board to liquidity provision — held separately
in §6 as the one open lead.

**The maker/taker picture** does not rescue the MM hypothesis. On-chain role
decoding succeeded for only 172 of 239 wallets (the rest have too few decoded
transactions to fit). Of those 172: 94 taker-leaning, 54 maker-leaning, 24
mixed. But a maker-leaning wallet that still **holds to resolution** is not
market-making — it is simply getting a better *entry price* on a directional
bet. Two-sided passive quoting that flattens inventory is the 1-wallet bucket,
full stop.

---

## 3. Does the market-making NO-GO still hold? — YES, now doubly confirmed

The prior scoping study (`market_making_feasibility.md`) reached a **NO-GO** from
crude economics: on 15m crypto Up/Down books, the spread a maker can capture is
~1c (a 1-tick book almost everywhere), while adverse selection on a realistic
fill-event markout is ~2.25c per fill — the maker is filled right before the
price moves against it, by construction. Spread minus adverse selection is
negative *before* fees, *before* inventory, *before* the ~0.35c rebate even
matters. It also flagged a structural inventory problem (only ~31% of
final-minute ticks have a tradeable two-sided book, so un-flattened inventory
eats the 0/1 settlement) and an operational mismatch (MM is latency- and
infrastructure-heavy; this project is a patient $10/trade bot).

**The wallet evidence now confirms that NO-GO from a completely independent
angle.** If market-making on these markets were a profitable business, the
leaderboard — the literal ranking of who makes the most money — would be full of
market-makers. It is not. **One wallet of 239** is a true passive liquidity
provider. The crude-economics argument and the revealed-behaviour argument agree:
nobody is getting rich market-making these markets, because the economics do not
support it.

This is the key new confirmation of this engagement. The MM hypothesis is no
longer just *modelled* to be unprofitable — it is *observed* to be unpopulated.

---

## 4. The directional pattern is survivorship, not edge

The 70%-dominant directional-holder pattern looked, at first, like the real
answer — a clear, repeated, money-making template. So we translated it into one
explicit, backtestable rule:

> **Rule M15-DH-1.** On a crypto Up/Down market, within the early-window entry
> zone, BUY the FAVOURITE side (mid > 0.50) when its odds are in an entry band;
> HOLD to resolution; settle at the true 0/1 outcome. Flat sizing, one entry per
> window.

The leaderboard is **survivors only** — the top 100 winners of each board. It
shows what winners *do*; it cannot measure expectancy, because it never sees how
many wallets ran the same pattern and lost. Our tick data sees **all** windows —
winners and losers — so it measures the rule's true expectancy. That is the
decisive test.

**The result: M15-DH-1 is not profitable.**

| Configuration | Net PnL/trade | 90% CI |
|---|---:|---|
| 15m primary band [0.60,0.90], taker | **−$0.26** | [−$0.57, +$0.05] — straddles 0 |
| 15m primary band [0.60,0.90], maker | +$0.23 | [−$0.09, +$0.55] — straddles 0 |
| 15m sensitivity [0.50,0.95], taker | −$0.72 | [−$1.11, −$0.34] — negative |
| 5m primary band [0.60,0.90], taker | **−$0.55** | [−$0.72, −$0.37] — negative |
| 5m primary band [0.60,0.90], maker | −$0.13 | [−$0.33, +$0.06] — straddles 0 |

**No DEV configuration — any band, taker or maker, either timeframe — produced a
net PnL whose CI excluded zero on the positive side.** Because nothing cleared
the bar on DEV, the sealed hold-out (May 21–22) was correctly **not opened**.

The reason is calibration. The expectancy sweep shows the favourite side's
realized win rate tracks its entry odds almost exactly: a favourite priced 0.63
wins ~63%, one priced 0.78 wins ~78%, one priced 0.88 wins ~88%. Mean
(realized − entry odds) over well-populated buckets is **+1.5c on 15m** and
**+0.2c on 5m** — within noise of perfect calibration. Gross, the rule is a
coin-flip-at-the-quoted-odds; net, it loses the round-trip cost. The favourite
rule does beat a random-side null (it is better than picking blindly), but
"better than a coin flip that also pays the spread" is not "profitable" — both
lose money.

The leaderboard's directional winners are therefore the **visible top of a much
larger population** that ran buy-favourite-hold; the losers simply do not appear
on a top-100 board. The pattern is **survivorship variance, not edge.** This is
the **4th independent confirmation** that the short-dated Polymarket Up/Down
market is efficient-after-cost: it joins the Phase 2 calibration study, the
Phase 4 policy forensics, and the Lead D maker reframe — all four agree.

**5m is worse, not better.** 5m was genuinely untested by prior research (which
was 15m-only). The 5m primary-band taker loses −$0.55/trade vs −$0.26 on 15m,
with a CI entirely negative. The 5m favourite is just as calibrated as 15m, so
the extra loss is pure cost — 5m fires more entries per real-time hour on
slightly wider books. The heavy 5m activity on the leaderboard is the same
survivorship pattern on a faster, more cost-intensive clock.

---

## 5. Fees, edge cases, and scale

**The fee model.** Polymarket's crypto fee is `fee = shares · 0.07 · p · (1−p)`.
**Takers pay it; makers pay 0** and additionally earn a rebate of ~20% of the
taker fee on matched volume (~0.35c per share per fill at a $10 stake — small).
Combined with the bid/ask spread that a taker crosses on both legs, the
**round-trip taker cost is 16–21% of the stake.** A $10 patient taker needs an
edge of >20% per trade just to break even — an enormous edge to demand of any
market, and the calibration studies show these markets do not contain a
mispricing remotely that large.

**The scale mismatch.** The leaderboard winners are not running anything a
patient small bot can imitate. The persistent winners in the deep-dive table run
**$17M–$34M of volume**; the mint-merge wallets show hundreds-to-thousands of
merges per wallet; the biggest all-time names carry $0.5M–$1.6M lifetime PnL
built on enormous trade counts of small ($3–$10 median) tickets. Even the
patterns that *look* viable on the leaderboard are **high-volume,
operationally-heavy games** — many thousands of fast, small trades, or a
continuously-run mint/merge operation. This project is, by explicit design
(`GOAL.md`, `CLAUDE.md`), a patient, human-like, $10-per-trade bot making a
small number of considered trades. That is the opposite discipline. The
leaderboard does not contain a strategy that is both profitable *and* compatible
with a patient small bot in these markets.

---

## 6. The one open lead — honest, not hype

There is exactly one pattern on the leaderboard that is genuinely
non-directional, persistently profitable, and **not yet tested by us**: the
**`mint_merge_arbitrageur` cluster** — 27 wallets that mint YES+NO share pairs
and merge them back.

What makes it worth naming:

- It is **non-directional** — it does not depend on guessing Up or Down, so it
  is not subject to the calibration wall that kills the directional pattern.
- It is **persistent**. Several mint-merge wallets sit on the ALL-time board with
  large lifetime PnL — justdance ($1.64M), coinman2 ($0.95M), 0x6E1d… ($0.89M),
  kingofcoinflips ($0.78M), easyclap ($0.56M). These are not one-month flashes.
- It is essentially **liquidity provision realised via mint/merge** — the closest
  thing on the board to a structural, repeatable, non-bet income.

The honest caveats, stated plainly so this is not false hope:

- **It lives in different markets.** The mint-merge cluster is dominant in
  **longer-dated crypto price-target markets**, not the 15m Up/Down series this
  project trades. The MM NO-GO in §3 and `market_making_feasibility.md` is
  **specific to 15m Up/Down** and does **not** cover price-target markets — but
  neither does any of our data or analysis. We have no tick data for those
  markets at all.
- **We do not know precisely how the merge wallets profit.** Cash-flow analysis
  shows they *accumulate inventory* (merge fraction ~0.6–0.8, long median holds
  of many hours), so it is **not pure risk-free arbitrage** — there is real
  inventory and real risk in it. "Arbitrageur" is a label for the structure, not
  a proof of free money.
- **It is still a high-volume game.** These wallets run hundreds-to-thousands of
  merges. Replicating it would not be a patient $10-trade bot either.
- **It is completely untested by us.** Confirming whether it is replicable would
  require its own data-collection effort (a new collector for longer-dated
  price-target crypto markets) and its own research project — comparable in scope
  to the directional research already done.

This is a *lead*, not a finding. It is the single avenue on the leaderboard that
has not been closed off — and it is closed off only because we have not looked,
not because we looked and found nothing.

---

## 7. Recommendation — go / no-go

**Market-making on 15m crypto Up/Down markets: NO-GO.** Doubly confirmed — the
crude economics are negative (spread ~1c vs adverse selection ~2.25c, before
fees and inventory) and the leaderboard contains only 1 market-maker of 239. Do
not build a market-making bot for these markets.

**Directional strategies on 5m / 15m crypto Up/Down markets: NO-GO.** The
leaderboard's own dominant winning pattern, tested on data that includes the
losers, loses money on every configuration (taker CI-negative, maker
CI-straddling, never CI-positive). This is the 4th independent confirmation that
these markets are calibrated and efficient-after-cost. The 16–21% round-trip
taker cost is a structural wall no measured edge clears.

**The honest bottom line.** There is no edge for a small patient bot in the
markets this project has studied — 15m and 5m crypto Up/Down, directional or
market-making. The leaderboard analysis did not overturn the prior research; it
*confirmed* it from a fresh, independent angle.

**The one remaining lead worth a future decision.** The mint-merge /
liquidity-provision pattern in **longer-dated crypto price-target markets** is
the only avenue not closed off — and only because it has never been studied. The
owner chose "analyze the leaderboard, then decide", so this is presented as a
**decision for the owner, not an action**:

- Pursuing it would be a **new research project** — a new data collector for
  price-target markets, then a study of whether mint/merge is replicable and
  profitable after cost and inventory risk.
- The honest prior is uncertain: it is a real, persistent, non-directional
  pattern run by real winners, but we do not yet know its mechanism, it carries
  inventory risk, and it is a high-volume game. It is a genuine lead, not a
  promise.
- If the owner does not want to open a new market category, the defensible
  conclusion is that this project's market category (short-dated crypto Up/Down)
  has been thoroughly and repeatedly shown to carry no harvestable edge, and the
  research arc can close honestly here.

The owner invested heavily in this and asked not to give up. This report does
not give false hope — but it does lay out the mint-merge lead clearly enough
that the choice to pursue it, or to close the arc, is a genuine and informed one.

---

## 8. Note for future work — the 5m label-corruption data bug

During the backtest of M15-DH-1 a data-quality bug was found and worked around;
flagging it here so future 5m analysis does not trip on it:

**`data/research/ticks_5m.parquet`'s baked-in `outcome_up` column is corrupt.**
It disagrees with the corrected 5m outcomes (the gamma-API ground truth) on
**~31% of windows** — verified, 1,564 of 5,018 windows. The leaderboard backtest
did **not** use the baked-in column; it used `corrected_labels_5m.parquet`
instead. Any future 5m analysis must do the same: **do not trust
`ticks_5m.parquet.outcome_up`** — join against the corrected 5m labels. (The 15m
labels in `ticks_15m.parquet` are authoritative — verified to match the
corrected labels for 100% of windows. The bug is 5m-specific.) Note the
corrected 5m label cache was fetched for the DEV split only; the 5m hold-out
needs an on-demand fetch if ever required.

---

*Inputs: `data/wallets/derived/wallet_summary.parquet` (239 wallets);
`research/wallets/` (analysis + archetype cascade);
`research/analysis/leaderboard_strategy_backtest.py` (the M15-DH-1 backtest).
See `docs/research/leaderboard_wallets.md` and
`docs/research/leaderboard_strategy_backtest.md` for full method and tables.*
