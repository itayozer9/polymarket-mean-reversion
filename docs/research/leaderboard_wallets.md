# Polymarket crypto-leaderboard wallet analysis

*Generated 2026-05-22 21:46 IDT (Israel local time). All computation in UTC; the leaderboard snapshot and wallet activity caches were fetched in Phase 1.*

This report analyses the **239 distinct wallets** on Polymarket's crypto profit leaderboard — the union of the top 100 of each of the MONTH, WEEK and ALL-time boards — to learn **how** the profitable traders make money. Phase 1 fetched each wallet's full activity cache; Task 2.1 (`research/wallets/analyze.py`) turned that into per-wallet metrics; this task assigns strategy archetypes, applies a persistence filter, and answers the three headline questions below.

> **Going-in hypothesis:** the winners are market-makers. **Short answer: mostly no** — see Q1.

## The three questions, up front

### Q1 — Does anyone actually profit from market-making?

**Largely no, in the textbook sense.** Of 239 leaderboard wallets, only **1** classify as `passive_liquidity_provider` — the true market-maker bucket (posts resting maker quotes AND flattens inventory rather than holding to resolution).

Archetype breakdown (ordered decision cascade, see `assign_archetype`):

| Archetype | Wallets | Share |
|---|---:|---:|
| `mint_merge_arbitrageur` | 27 | 11% |
| `passive_liquidity_provider` | 1 | 0% |
| `directional_holder` | 167 | 70% |
| `active_trader_scalper` | 10 | 4% |
| `mixed_or_unknown` | 34 | 14% |
| **total** | **239** | 100% |

The dominant pattern is **`directional_holder`** (167 wallets, 70%): buy a side, hold it to resolution, collect the winning-share payout (`redeem`). That is a *directional bet*, not market-making. The second bucket is **`mint_merge_arbitrageur`** (27 wallets) — wallets that mint YES+NO pairs and merge them back; a non-directional structured trade, closer to arbitrage / liquidity provision than the directional bet, but still not classic two-sided quoting.

**Maker / taker evidence — with its coverage caveat.** The on-chain role model produced a `maker_fill_frac` for only **172 / 239** wallets; the other 67 are NaN because the role model never fit (more than half of those wallets' sampled transactions were absent from the decoded set). Among the 172 with data:

| Entry role | Wallets |
|---|---:|
| maker | 54 |
| taker | 94 |
| mixed | 24 |
| unknown | 67 |

So even on the optimistic reading, maker-dominant wallets are a minority, and a maker-dominant wallet that **still holds to resolution** is not market-making — it is just getting a better entry price on a directional bet. The exit-mode evidence is decisive: **188 of 239 wallets are redeem-dominant** (hold to resolution) and **117 wallets never sell at all**. Holding to resolution is the opposite of the flatten-the-book behaviour that defines a market maker.

### Q2 — Do winners trade the 15m markets the bot targets, or longer-dated ones?

**They trade the 15m markets.** 148 of 239 wallets (62%) have the **`crypto_15m_updown`** Up/Down markets as their dominant market type by buy volume — exactly the markets the live mean-reversion bot targets.

| Dominant market type | Wallets |
|---|---:|
| `crypto_15m_updown` | 148 |
| `crypto_price_target` | 39 |
| `non_crypto` | 31 |
| `crypto_hourly_updown` | 10 |
| `crypto_weekly_monthly` | 5 |
| `crypto_other` | 4 |
| `crypto_daily_updown` | 2 |

Restricting to the **top 15 by all-time leaderboard PnL**, the dominant-market split is:

| Dominant market type | Top-15 wallets |
|---|---:|
| `crypto_15m_updown` | 9 |
| `crypto_price_target` | 6 |

The biggest all-time winners are split between 15m Up/Down directional trading and price-target / longer-dated markets, but the 15m bucket is well represented at the very top — the markets the bot trades are not a backwater.

### Q3 — For the most persistent winners, how do they make money?

**54 wallets are persistent** (appear on >=2 of the MONTH/WEEK/ALL boards); **13** of them appear on BOTH the ALL-time board AND the MONTH board — a lifetime winner that is still winning right now, the strongest persistence signal available.

Per-wallet deep-dive, top 15 persistent winners (ALL+MONTH first, then by total board PnL):

| Wallet | Archetype | Dom. market | lb_pnl MONTH / WEEK / ALL | Exit (redeem/merge/sell share) | Maker | Buy-price p25/p50/p75 (15m) | VWAP px | Median size | Median hold | n redeem/merge/sell | Trunc |
|---|---|---|---|---|---|---|---|---|---|---|---|
| justdance | `mint_merge_arbitrageur` | `crypto_price_target` | $47,634 / — / $1,640,273 | 21% / 79% / 0% | 0.36 | 0.48 / 0.50 / 0.50 | 0.47 | $9 | 3.4h | 188/706/0 | Y |
| 0x6E1d5040d0ac73709B0621 | `mint_merge_arbitrageur` | `crypto_price_target` | $271,727 / $41,646 / $885,149 | 22% / 78% / 0% | 0.21 | — / — / — | — | $29 | 18.8h | 52/227/0 | Y |
| Bonereaper | `directional_holder` | `crypto_15m_updown` | $226,424 / $65,466 / $795,513 | 100% / 0% / 0% | 0.25 | 0.27 / 0.48 / 0.68 | 0.85 | $4 | 4m | 54/0/0 | Y |
| kingofcoinflips | `mint_merge_arbitrageur` | `crypto_price_target` | $27,375 / — / $780,803 | 37% / 63% / 0% | 0.26 | — / — / — | — | $5 | 16.0h | 46/781/0 | Y |
| ohanism | `directional_holder` | `crypto_15m_updown` | $149,368 / $29,464 / $514,991 | 86% / 0% / 14% | 1.00 | 0.39 / 0.57 / 0.75 | 0.70 | $5 | 3m | 207/0/624 | Y |
| HaileyWelch | `directional_holder` | `crypto_price_target` | $21,783 / — / $635,849 | 58% / 42% / 0% | 0.43 | — / — / — | — | $5 | 27.8h | 1812/556/0 | Y |
| Sharky6999 | `mixed_or_unknown` | `non_crypto` | $21,875 / — / $600,122 | 99% / 0% / 1% | 0.97 | 0.99 / 0.99 / 0.99 | 0.99 | $6 | 1.7h | 158/2/728 | Y |
| 0xb27bc932bf8110d8f78e55 | `directional_holder` | `crypto_15m_updown` | $52,001 / — / $568,928 | 100% / 0% / 0% | 0.97 | 0.20 / 0.46 / 0.69 | 0.67 | $3 | 49m | 20/0/0 | Y |
| easyclap | `mint_merge_arbitrageur` | `crypto_price_target` | $21,965 / $28,964 / $564,044 | 30% / 70% / 0% | 0.21 | — / — / — | — | $7 | 70.3h | 90/919/0 | Y |
| rwo | `directional_holder` | `crypto_15m_updown` | $47,448 / $7,372 / $558,108 | 90% / 10% / 0% | 0.51 | 0.02 / 0.30 / 0.72 | 0.92 | $3 | 3m | 197/37/24 | Y |
| 0x06dc51826bc524d9a83770 | `mint_merge_arbitrageur` | `crypto_price_target` | $119,009 / $36,177 / $323,755 | 45% / 55% / 0% | 0.27 | — / — / — | — | $10 | 18.4h | 114/172/0 | Y |
| 0x20d2309cd92b797ae7ca17 | `directional_holder` | `crypto_15m_updown` | $111,664 / $25,523 / $309,607 | 50% / 0% / 50% | 0.00 | 0.35 / 0.55 / 0.76 | 0.68 | $9 | 3m | 414/0/1338 | Y |
| Marketing101 | `directional_holder` | `crypto_15m_updown` | $50,505 / — / $382,034 | 100% / 0% / 0% | 0.97 | 0.29 / 0.49 / 0.79 | 0.75 | $4 | 12m | 187/0/0 | Y |
| coinman2 | `mint_merge_arbitrageur` | `crypto_price_target` | — / $18,493 / $952,203 | 32% / 68% / 0% | 0.23 | — / — / — | — | $8 | 23.9h | 76/846/0 | Y |
| 0xb55fa1296E6ec55D0cE53d | `directional_holder` | `crypto_15m_updown` | $229,167 / $23,701 / — | 100% / 0% / 0% | 0.23 | 0.11 / 0.36 / 0.60 | 0.63 | $4 | 3m | 89/0/0 | Y |

The deep-dive table shows two clean money-making templates among the persistent winners: (a) **15m directional holders** — dominant in `crypto_15m_updown`, redeem-dominant, hold each bet ~minutes to resolution; and (b) **mint-merge arbitrageurs** — `merge_share` near 0.6-0.8, dominant in price-target markets, non-directional. The `win_rate` column from Task 2.1 is deliberately omitted (see caveats).

## Persistence — the board-combination breakdown

| Board combination | Wallets | Persistent? |
|---|---:|---|
| ALL | 86 | no (single board) |
| WEEK | 52 | no (single board) |
| MONTH | 47 | no (single board) |
| MONTH+WEEK | 40 | yes |
| ALL+MONTH+WEEK | 7 | yes |
| ALL+MONTH | 6 | yes |
| ALL+WEEK | 1 | yes |

The persistence filter is `n_boards >= 2`. The rejected alternative — counting >=3 distinct calendar months in a wallet's *own* activity history — is unusable here: **173/239 wallets (72%) are `activity_truncated`** at the 4000-record API cap, so their visible history can span well under three months and that criterion would mostly measure trade frequency, not longevity.

## Entry odds — where winners buy

The single most important strategy descriptor: at what **price (odds)** do winners enter? Computed from each wallet's BUY trades, restricted to `crypto_15m_updown` markets and to the **133 wallets** that are both 15m-dominant and redeem-dominant (the directional-holder population the bot's strategy most resembles).

Per-wallet buy-price percentiles, summarised across the 133 15m directional-holder wallets:

| Buy-price stat | Median wallet | p25 wallet | p75 wallet |
|---|---:|---:|---:|
| p10 | 0.120 | 0.040 | 0.220 |
| p25 | 0.270 | 0.160 | 0.390 |
| p50 (median) | 0.460 | 0.330 | 0.524 |
| p75 | 0.590 | 0.500 | 0.690 |
| p90 | 0.762 | 0.570 | 0.860 |
| VWAP (usdc-weighted) | 0.608 | 0.525 | 0.681 |

Share of 15m buy **USDC volume** by odds band (USDC-weighted across all 133 wallets — i.e. where the money actually goes):

| Odds band | Share of buy volume |
|---|---:|
| [0.00, 0.20) | 3% |
| [0.20, 0.40) | 9% |
| [0.40, 0.60) | 26% |
| [0.60, 0.80) | 16% |
| [0.80, 1.00] | 46% |

**Entry timing within the 15m window** (derived from the compact slug's window-start timestamp; coverage is essentially 100% of 15m buys because the compact `<asset>-updown-15m-<ts>` slug carries the window start). Median across wallets of the per-wallet percentiles: p10 = 48s, p50 = 184s, p90 = 295s into the 900-second window.

**Reading the buy-price data.** Winners do **not** concentrate their entries in one narrow odds band. On a per-wallet basis the median trader's buys span roughly p25 = 0.27 to p75 = 0.59 — a wide interquartile range straddling the 0.50 coin-flip. But the USDC-weighted view tells a sharper story: **the money is bimodal-to-favourite-heavy** — the largest single block of buy volume (~46%) lands at odds [0.80, 1.00], and the volume-weighted average entry price is ~0.72. In plain terms: winners place many small bets across all odds, but they put their *big* money on heavy favourites (cheap-implied-edge, high-probability legs) and they enter **early in the window** (median ~3 minutes in, almost all within the first 5 minutes). This is the empirical entry signature the Phase 3 backtest must reproduce.

## Synthesis and honest caveats

**What the data shows.** The Polymarket crypto leaderboard is *not* dominated by market-makers. The modal winning wallet is a **directional holder**: it buys one side of a short-dated crypto Up/Down market — overwhelmingly the 15-minute series — and holds to resolution. A meaningful second group runs a **mint-merge** structured trade in price-target markets. Classic two-sided passive liquidity provision is a small minority.

**Survivorship bias — the central limitation.** The leaderboard is, by construction, the **top 100 winners** of each board. It tells us what winners *do*; it tells us **nothing** about how many wallets ran the same strategy and lost. If 5,000 wallets bought heavy-favourite 15m legs and 100 of them are on the board, this analysis cannot distinguish skill from variance. The wallet analysis can only generate a hypothesis about a positive-expectancy strategy; it **cannot prove one exists**. Proving expectancy requires the Phase 3 backtest on full 15m tick data, which sees winners *and* losers.

**Other caveats.**

* **Inflated `win_rate`.** The FIFO `win_rate` from Task 2.1 is structurally biased upward: a REDEEM lot always resolves at 1.0 (the winning payout) and losing shares simply expire with no activity record, so losers leave no FIFO round-trip. `win_rate` is therefore **not used as an edge metric anywhere in this report.**
* **Truncation.** 173/239 wallets are capped at 4000 activity records. Cash-flow PnL (`total_realized_pnl_cashflow`) only reconciles against the official board PnL when `activity_truncated == False`; for the truncated majority it is partial. The official `lb_pnl` is the only trustworthy 'how much' anchor and is what the deep-dive table uses.
* **Thin maker/taker coverage.** `maker_fill_frac` is NaN for 67 wallets and built from a *sample* of decoded transactions for the rest. Treat the maker/taker split as indicative, not exact.

## Testable strategy for Phase 3

Translating the dominant winning pattern into ONE explicit, backtestable rule on 15m tick data:

> **Rule M15-DH-1 (directional-holder, favourite-leg).** On a `crypto_15m_updown` market (BTC/ETH/SOL/XRP Up/Down, 15-minute window): within the **first 5 minutes** of the window (entry offset 0-300 s), **buy the favourite side** when its odds are in the band **[0.60, 0.90]** — the heavy-favourite zone where the winners concentrate their USDC volume (pooled USDC-weighted entry price ~0.72; ~46% of winner buy-volume sits at >=0.80). **Hold to resolution** (no early exit, no stop). Position sizing flat per trade.

Concrete parameters extracted from this analysis (Step 1 buy-price data, 133 15m directional-holder winners):

| Parameter | Value | Source |
|---|---|---|
| Market | `crypto_15m_updown` (15-min Up/Down) | dominant for 148/239 wallets |
| Side | the favourite (odds > 0.50 leg) | redeem-dominant, holds-to-win |
| Entry-odds band | **[0.60, 0.90]** | winner buy-volume concentration; per-wallet p75 median 0.59, pooled USDC-weighted entry price 0.72 |
| Entry timing | first 0-300 s of the 900 s window | median winner entry ~185 s in |
| Exit | hold to resolution | 188/239 redeem-dominant; 117 never sell |

**Honest note on the band.** The per-wallet *interquartile* range is wider and lower (p25 ~0.27, p50 ~0.46) than the volume-weighted band — winners place many small bets across all odds. Rule M15-DH-1 deliberately follows the **money** (USDC-weighted), not the trade count, because that is where the realized PnL is. Phase 3 should backtest the [0.60, 0.90] favourite-leg variant as the primary, and a wider [0.50, 0.95] variant as a sensitivity check. If neither shows positive expectancy on the full tick data (winners + losers), the leaderboard pattern is survivorship variance, not edge — and that is itself a valid, reportable Phase 3 outcome.

A second, separate hypothesis worth a Phase 3 look — the `mint_merge_arbitrageur` bucket (27 wallets) — is **out of scope** for the 15m mean-reversion bot (it lives in price-target markets) and is noted here only for completeness.

---

*Inputs: `data/wallets/derived/wallet_summary.parquet` (239 rows), `research/wallets/entry_prices.py`. Archetype cascade: `research/wallets/wallet_report.py:assign_archetype`. Regenerate with `python -m research.wallets.wallet_report`.*
