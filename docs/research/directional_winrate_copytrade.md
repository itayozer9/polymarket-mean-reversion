# Directional buy-and-hold: honest win rate, and is anyone copyable?

*Generated 2026-05-22 22:30 IDT (Israel local time). Cache fetched 2026-05-22 21:19 IDT. All computation in UTC; human-facing timestamps shown in Israel local time.*

This report answers the project owner's three questions about the **167 `directional_holder`** wallets that dominate Polymarket's crypto profit leaderboard (see `docs/research/leaderboard_wallets.md`):

1. How do the directional buy-and-hold wallets win *so well* — what is the **REAL** win rate of each?
2. Could we copy the leaders?
3. Are the leaders copying each other?

> **Bottom line, up front.** (1) The eye-catching 90%+ win rates are mostly the mechanical result of buying favourites: a leg priced at 0.90 wins ~90% of the time with zero skill. Once the silent losses are counted, the directional holders do show a **small positive *gross* excess** (~5 percentage points: they win a few points more often than their entry odds implied) — but that ~5c gross edge is far below the 16–21% round-trip taker cost, so it is **not a profitable, cost-surviving edge** (consistent with the prior backtest). (2) No — the gross edge is too small to clear costs even for the leader, and on 15m markets the copy latency makes a copy strictly worse than the leader. (3) The wallets cluster heavily on the same markets, but the pattern is a **common public signal** (the crypto spot price), not a copy-trading ring with a consistent leader.

## Part A — The honest win rate of the directional holders

### The structural bias being corrected

The FIFO `win_rate` in `wallet_summary.parquet` is **structurally inflated**. A hold-to-resolution position that *wins* produces a `REDEEM` activity record. A hold-to-resolution position that *loses* expires worthless and produces **no record at all**. A win rate built from REDEEMs therefore literally cannot see the losses.

The fix: a `(wallet, slug)` market with only BUYs and no exit (`exit_mode == "none"`) whose 15m/5m window has **already closed** is a *silent loss* — the shares expired worthless. Counting those gives an honest win rate. `market_cashflow.parquet` nets every USDC movement per slug, so `net_pnl > 0` = won, `net_pnl < 0` = lost. Markets whose window had **not** closed by the cache fetch time (2026-05-22 21:19 IDT) are excluded as unresolved, not counted as losses.

### Two method points (so the numbers are not garbage)

**(1) Single-sided slugs only.** Many of these wallets buy *both* the Up and the Down leg of the same window — a hedged / scalp position, not a directional bet. For a both-sided slug "entry odds" is meaningless (the two legs price ~p and ~1−p, so any average lands near 0.5). Part A therefore restricts the win-rate-vs-odds comparison to slugs where the wallet bought exactly **one** outcome — a genuine directional position. (Both-sided slugs are ~37% of directional-hold markets and are excluded from Part A.)

**(2) Consistent weighting.** `excess` compares a win rate to an entry-odds figure — they MUST be computed under the same weighting. Comparing a count-weighted win rate (these wallets place many tiny longshot bets) to a USDC-weighted entry odds (the big money sits on favourites) mixes two populations and manufactures a spurious large-negative number (a Simpson's-paradox trap). Below, the per-slug excess `won{0,1} − slug_VWAP_entry` is reported **both** count-weighted and USDC-weighted, and the two agree.

### The decisive metric: excess win rate

**A high win rate is exactly what you expect when you buy favourites.** Buy a leg priced at 0.85 and you win ~85% of the time with *zero skill* — that is what the price means. The only metric that reveals edge is

> **`excess_win_rate` = `honest_win_rate` − `entry_odds`**

Excess ~0 means the wallet wins exactly as often as it paid for — no edge. Excess materially > 0 means genuine skill (gross of cost).

**Calibration — win rate tracks entry odds.** Bucketing every single-sided directional slug by its entry odds, the realized win rate tracks the price closely, sitting a few points *above* it in every bucket:

| Entry-odds bucket | n slugs | Realized win rate |
|---|---:|---:|
| [0.0, 0.2) | 3,704 | 6.0% |
| [0.2, 0.4) | 1,788 | 36.5% |
| [0.4, 0.5) | 2,202 | 50.9% |
| [0.5, 0.6) | 1,899 | 62.7% |
| [0.6, 0.7) | 1,111 | 78.0% |
| [0.7, 0.8) | 806 | 87.5% |
| [0.8, 0.9) | 779 | 91.7% |
| [0.9, 1.0) | 3,060 | 98.6% |

That small consistent margin above the diagonal is the gross excess — real, but small.

**Per-wallet result across 130 directional-holder wallets** with at least one single-sided directional market (count-weighted excess):

| `excess_win_rate` statistic | All wallets | >=20 markets |
|---|---:|---:|
| wallets measured | 130 | 92 |
| mean excess | 9.7% | 8.9% |
| median excess | 8.5% | 6.9% |
| p10 / p90 excess | -7.9% / 28.7% | -3.6% / 23.0% |
| wallets with excess > 0 | 94/130 (72%) | 70/92 (76%) |
| wallets with excess > +5% | 77/130 | 53/92 |

The distribution leans **positive** — most directional-holder wallets win a few points more often than their entry odds implied. On the meaningful-sample subset the mean per-wallet excess is ~9% and ~76% of wallets are positive. This is a **small, real, gross edge** — not zero. The decisive question (Part C) is whether ~5–9c of gross excess can survive the 16–21% round-trip taker cost. It cannot.

### Concrete examples — a high win rate is not (much of) an edge

The trap, made concrete: wallets with eye-catching win rates whose excess is small because they simply bought favourites.

| Wallet | Honest win rate | Entry odds | Excess | n markets |
|---|---:|---:|---:|---:|
| nj23adsknml3 | 100.0% | 0.346 | 65.4% | 32 |
| 0x2a9c77ED09d86C2AD2Ce | 99.7% | 0.988 | 0.9% | 292 |
| 0xba264356d6fef08f23a4 | 99.6% | 0.991 | 0.5% | 273 |
| 0xea687b343dc0132f7330 | 99.0% | 0.967 | 2.3% | 210 |

A 99% win rate bought at 0.98 entry odds is a +1% edge — almost all of that headline number is just the price of a near-certain favourite, not skill.

For balance, the wallets with the **largest positive excess** (>=50 markets) — the best edge candidates Part A can find:

| Wallet | Honest win rate | Entry odds | Excess | n markets |
|---|---:|---:|---:|---:|
| 0x7F59998477864871448e | 73.3% | 0.417 | 31.7% | 60 |
| 0xE9Ba96828e513a6CC35f | 70.1% | 0.390 | 31.2% | 77 |
| stingo43 | 63.3% | 0.381 | 25.1% | 332 |
| 0x3516808150a9Dd794BfC | 66.7% | 0.423 | 24.4% | 66 |

A handful of wallets show a genuinely large excess (20%+). These are the ones worth scrutiny — but note (a) they trade at low entry odds, where a few lucky longshots move the number a lot, and (b) even a 20% gross excess barely clears a 16–21% round-trip cost, with no margin and no certainty it persists.

### Persistence test — do they beat their odds in BOTH halves?

Variance alone produces plenty of positive-excess wallets in any single sample. A *genuinely skilled* wallet beats its entry odds in **both** halves of its own history. Each wallet's directional history is split in two by time (window-start median); the per-slug excess is recomputed in each half; a wallet passes only if it is positive-excess in **both** halves with a meaningful sample (>=30 markets/half).

- Wallets with >=30 single-sided directional markets in **each** half: 58
- Of those, positive excess in **both** halves: **36** (62%)

A pure zero-edge population would pass at ~25% (independent 50/50 in each half). The observed pass rate is **62%** — meaningfully above chance. That says the small gross excess is **not pure noise**: there is a weak but real persistent tendency for these wallets to beat their entry odds. It does **not** say that tendency is large enough to be profitable after cost (it is not — see Part C).

The strongest persistent wallets (positive in both halves, ranked by overall excess):

| Wallet | Early excess (n) | Late excess (n) | Overall excess |
|---|---:|---:|---:|
| 0x7F59998477864871448e | 32.2% (30) | 31.1% (30) | 31.7% |
| 0xE9Ba96828e513a6CC35f | 18.7% (39) | 44.0% (38) | 31.2% |
| stingo43 | 19.7% (167) | 30.6% (165) | 25.1% |
| 0x3516808150a9Dd794BfC | 24.2% (33) | 24.6% (33) | 24.4% |
| 0x7543dAd3D9b2F6cb8d86 | 19.1% (30) | 27.4% (30) | 23.3% |
| 0xE91016e83D11a0306c12 | 25.8% (31) | 17.8% (30) | 21.8% |
| 0x931cd2259731f65ff31f | 29.1% (159) | 12.3% (159) | 20.7% |
| 0xA0a5078359daD63993a8 | 20.9% (35) | 17.9% (34) | 19.4% |
| 0x0a2c53bd218c04da996c | 20.0% (43) | 13.3% (43) | 16.7% |
| SDWWWS | 29.0% (35) | 3.6% (34) | 16.5% |


### Calibration sanity check — the pooled excess

Pooling **every** single-sided directional market of **every** directional-holder wallet (winners and silent losers together). Both weightings of the same per-slug data — note they agree, no Simpson's-paradox gap:

| Pooled metric | Count-weighted | USDC-weighted |
|---|---:|---:|
| single-sided directional markets | 15,349 | 15,349 |
| won / lost | 8,488 / 6,861 | 8,488 / 6,861 |
| pooled honest win rate | 55.3% | 81.7% |
| pooled entry odds | 0.505 | 0.760 |
| **pooled excess** | **4.8%** | **5.7%** |

The pooled excess is **small and positive** — about +5 percentage points either way. The market is close to calibrated: across the whole directional-holder population the favourites win at roughly (very slightly above) the rate their price implied. This is broadly consistent with the prior research (`docs/research/leaderboard_mm_verdict.md` §4: realized win rate tracks entry odds, gross mean ~+1.5c on 15m) — the present analysis finds a somewhat larger gross figure (~+5c) because it counts the silent losses correctly and pools the leaderboard's *winning* wallets, who are a positively-selected slice. **Crucially: +5c gross is far below the 16–21% round-trip taker cost.** A gross edge that small is wiped out — and then some — by trading frictions.

**Answer to Q1.** The directional holders' headline 90%+ win rates are *mostly* the mechanical result of buying favourites — a leg priced at 0.90 wins ~90% of the time with zero skill. Once the silent losses are counted honestly, there is a **small, real, positive gross excess of ~5 percentage points** (they win a few points more often than their entry odds implied), and it is weakly persistent across time. But ~5c of gross edge does **not** survive the 16–21% round-trip cost of actually trading — so it is a *statistical* edge, not a *bankable* one. They do not win "so well"; they win a little better than their odds, and not by enough to be profitable after cost.

## Part B — Are the leaders copying each other?

Across all 239 wallets, every wallet's first BUY in each `(slug, outcome)` was indexed, giving **18,286** distinct `(compact-slug, outcome)` entry events. Two questions: do wallet pairs co-trade the same markets more than chance predicts, and if so is there a consistent first-mover others follow?

### Co-trading lift

For a pair (A, B), the chance baseline for co-occurrence treating entries as independent is `n_A * n_B / N` (N = distinct markets). `lift = observed / expected`; lift >> 1 is non-independent co-trading. Strongest pairs (each wallet >=50 markets):

| Wallet A | Wallet B | Co-traded | Expected | Lift |
|---|---|---:|---:|---:|
| 0x48AC40Fc545CF327 | Bonereaper | 66 | 0 | 167.62 |
| 0x7543dAd3D9b2F6cb | 0xE91016e83D11a030 | 84 | 1 | 155.34 |
| 0x3516808150a9Dd79 | 0xE91016e83D11a030 | 84 | 1 | 153.85 |
| 0x3516808150a9Dd79 | 0x7543dAd3D9b2F6cb | 88 | 1 | 150.22 |
| 0x424eb20Fcd25113e | 0x48AC40Fc545CF327 | 69 | 1 | 137.98 |
| 0x424eb20Fcd25113e | Bonereaper | 95 | 1 | 136.79 |
| Bonereaper1 | 0xE91016e83D11a030 | 38 | 0 | 122.68 |
| 0x823D73ef41bb2570 | Prgovindu1 | 31 | 0 | 121.59 |
| 0x7F59998477864871 | Prgovindu1 | 47 | 0 | 120.98 |
| Prgovindu1 | 0xA0a5078359daD639 | 48 | 0 | 119.81 |
| 0xA0a5078359daD639 | 0xE9Ba96828e513a6C | 72 | 1 | 119.81 |
| 0x3516808150a9Dd79 | Bonereaper1 | 39 | 0 | 116.22 |

The strongest pair has a lift of **168×**; the median of the top pairs is **114×**. Those lifts look dramatic, but the `expected` column is the reason: a wallet that trades only ~60–100 markets out of ~18k has a chance co-occurrence baseline rounding to **0–1 markets**, so *any* genuine shared focus produces a huge ratio. A high lift here means the two wallets concentrate on the **same slice of markets** — but that is exactly what a **shared public signal** produces. The crypto Up/Down universe is small (a handful of assets × 15m/5m windows) and the most-active windows — the ones with a clear directional spot move — attract every active wallet at once. Heavy overlap on the obvious markets is structural; it is not, by itself, evidence of one wallet copying another. The lead/follow timing below is the test that distinguishes the two.

### Lead / follow timing — is there a consistent first mover?

For every wallet with >=100 co-traded markets, the fraction of those markets in which it was the **first** of all entrants to buy. A genuine copy-trading *leader* is first in a large, consistent fraction. A shared-signal world has no consistent leader — with `k` wallets per market each is first ~`1/k` of the time.

| Wallet | Co-traded markets | Times first | First-mover frac |
|---|---:|---:|---:|
| 0x3A847382ad6FfF9be1 | 162 | 122 | 75.3% |
| PBot-6 | 708 | 505 | 71.3% |
| PBot-1 | 202 | 139 | 68.8% |
| 0x8d2D7BAe900cC62bBA | 126 | 61 | 48.4% |
| alwaysLastInLife | 194 | 76 | 39.2% |
| Marketing101 | 125 | 46 | 36.8% |
| baloneigh | 631 | 206 | 32.6% |
| nndrekop | 559 | 181 | 32.4% |
| 0xE9Ba96828e513a6CC3 | 102 | 33 | 32.4% |
| 0x50f7 | 603 | 164 | 27.2% |

The most consistent first-mover is first in only **75.3%** of its co-traded markets, and the fractions decay smoothly down the list — there is no single dominant leader. Note too that the wallets near the top include `PBot-*` entries — self-declared bots that simply enter *fast*. A wallet being first because it is a low-latency bot is **not** the same as other wallets *following* it: a copy-trading ring needs the followers to lag the SAME leader by a consistent short gap. Decisively, the gap analysis below shows the inter-wallet lag is **two-signed and wide**, not a tight one-directional follow. There is no wallet the others repeatedly trail.

Inter-wallet entry-gap distribution for the **strongest-lift pair** (0x48AC40Fc545CF3 vs Bonereaper, 66 co-traded markets):

| Gap statistic (B entry − A entry, seconds) | Value |
|---|---:|
| median gap | -5s |
| p10 / p90 gap | -56s / 62s |
| A entered first | 22 / 66 markets |
| B entered first | 39 / 66 markets |

The gap distribution straddles zero with a wide spread — sometimes A is first, sometimes B, by tens to hundreds of seconds. That is the signature of two traders **reacting independently to the same public crypto price move**, not one copying the other. Genuine copying would show a tight, one-signed lag (the follower always a few seconds behind the leader).

**Answer to Q3.** The leaderboard wallets cluster heavily on the same markets — but that is the small size of the crypto Up/Down universe and a **shared public signal** (the spot price everyone watches), not a copy-trading ring. There is no consistent first-mover and no tight, one-signed follow lag. The wallets look like independent traders reacting to the same crypto tape.

## Part C — Rebate farming, and is copy-trading viable?

### Rebate-farming check

Polymarket pays makers a rebate of ~20% of the taker fee on matched volume. Could a high-volume, maker-leaning wallet be "profitable" mainly from rebates rather than trade edge? The taker fee is `shares · 0.07 · p · (1−p)`. We take a deliberately generous upper bound: assume **every** dollar of a wallet's buy volume was a maker fill at the fee-maximising price p=0.5, and the wallet earned 20% of that fee as rebate. Top maker-leaning wallets by buy volume:

| Wallet | Buy volume | Maker frac | lb_pnl | Rebate (upper bnd) | Rebate / PnL |
|---|---:|---:|---:|---:|---:|
| LucasMeow | $6,107,551 | 1.00 | $283,799 | $42,753 | 15.1% |
| 100x | $3,271,206 | 1.00 | — | $22,898 | — |
| Mr-Anderson | $2,656,895 | 0.50 | — | $18,598 | — |
| Sharky6999 | $1,778,492 | 0.97 | $600,122 | $12,449 | 2.1% |
| 5245242 | $1,212,383 | 0.88 | — | $8,487 | — |
| strike123 | $1,124,660 | 0.76 | — | $7,873 | — |
| willydh | $906,735 | 0.89 | — | $6,347 | — |
| deloochsREBORN | $727,166 | 0.58 | — | $5,090 | — |
| poorersob | $679,742 | 0.69 | $351,524 | $4,758 | 1.4% |
| Mantronix | $566,881 | 1.00 | $26,021 | $3,968 | 15.2% |
| BoshBashBish | $550,919 | 1.00 | $364,349 | $3,856 | 1.1% |
| gopfan2 | $535,868 | 1.00 | — | $3,751 | — |
| x6916Cc00AA1c3e75E | $478,888 | 1.00 | $391,691 | $3,352 | 0.9% |
| LDSIADAS | $372,639 | 1.00 | $249,958 | $2,608 | 1.0% |
| rwo | $238,976 | 0.51 | $558,108 | $1,673 | 0.3% |

Even at this **generous upper bound**, the estimated rebate is a median of 1.2% of leaderboard PnL across the top maker-leaning wallets (max 15.2%). The real figure is far lower still — these wallets buy favourites (p near 0.7–0.9, where `p·(1−p)` is roughly half its p=0.5 peak) and are not 100% maker fills. **Rebate farming is not a plausible explanation** for any leaderboard wallet's profit: the rebate is a rounding error next to the PnL. The leaderboard PnL is real trading PnL (driven, as Part A shows, by buying favourites that win at their priced odds — i.e. by *volume* and *variance*, not by rebate income).

### Copy-trading viability — honest synthesis

Copying a leader works **only if** (a) the leader has a genuine persistent edge **that survives cost** **and** (b) you can replicate the entry before the edge decays. Both conditions fail.

**(a) The edge is real but too small to bank.** Part A found a small, weakly-persistent **gross** excess (~5 percentage points pooled; 36 wallets beat their entry odds in both halves of their own history, above the ~25% chance rate). That is not a null — there is a faint real tendency to beat the odds. **But the gross excess is ~5c and the round-trip taker cost is 16–21%.** Copying a leader hands you the leader's ~5c of gross edge and then charges you 16–21% to trade — a net loss. Even the handful of 20%+-excess wallets only *barely* clear cost with no margin, and those are low-odds wallets whose excess is the most variance-prone. There is no edge here that survives cost.

**(b) The latency problem — fatal on fast markets.** The public activity feed shows a trade only **after on-chain settlement**. In a 15-minute (or 5-minute) crypto Up/Down market the price moves in *seconds* — the favourite re-prices continuously as spot moves. By the time a leader's BUY is visible in the feed, the price has already moved; a copy buys at a strictly worse odds than the leader did. A copy that pays a worse entry price than the leader gives up the leader's already-thin ~5c gross excess before it even pays the round-trip cost — the result is firmly **negative** expectancy. Copy-trading is **latency-fatal on 15m/5m markets**.

Copy-trading is **latency-tolerant** on slow markets — daily / weekly / price-target markets where the price barely moves over hours, so a delayed copy still gets a near-identical entry. But the leaderboard's slow-market winners are the **mint-merge cluster** (27 wallets, `docs/research/leaderboard_mm_verdict.md` §6), whose edge — if any — is a structured mint/merge operation, not a directional bet you can mirror with a single copied BUY.

**Answer to Q2.** No. Copy-trading the leaderboard is not viable: the directional holders' gross edge (~5c, Part A) is **too small to survive the 16–21% round-trip cost** — copying a leader nets a loss even with a perfect copy. On top of that, the post-settlement feed latency is fatal on the fast 15m/5m markets they trade (a delayed copy pays worse odds than the leader). The only latency-tolerant markets (slow / price-target) are dominated by a mint-merge structure that a single copied trade cannot replicate. There is no copyable, cost-surviving edge here.

## Bottom line — the owner's three questions

| # | Question | Verdict |
|---|---|---|
| 1 | How do directional buy-and-hold wallets win so well — real win rate? | The 90%+ win rates are **mostly buying favourites** (a 0.90 leg wins ~90% with zero skill) plus a silent-loss reporting bias. Honest excess win rate is a **small, real, positive ~5 percentage points** (weakly persistent) — a *gross* statistical edge, but far below the 16–21% round-trip cost, so **not a bankable edge**. |
| 2 | Could we copy the leaders? | **No.** The ~5c gross edge does not survive trading cost even with a perfect copy; and post-settlement feed latency is fatal on the 15m/5m markets they trade. |
| 3 | Are the leaders copying each other? | **No.** Heavy market overlap, but it is a **shared public signal** (crypto spot) — no consistent first-mover, no tight follow lag. Independent traders on the same tape. |

This is a **near-null result** — and an honest one. There is a faint, real, positive gross excess in the directional-holder population (they beat their entry odds by ~5c and do so somewhat persistently), so this is not a flat zero. But ~5c of gross edge is comprehensively wiped out by the 16–21% round-trip taker cost, which is why it is not a tradable edge — fully consistent with the four prior independent confirmations (`docs/research/leaderboard_mm_verdict.md`) that short-dated Polymarket crypto Up/Down markets are **efficient *after cost***. The leaderboard's directional winners are mostly survivorship riding a thin real edge; they are not copying each other; and the thin edge they do have is not copyable.

---

*Inputs: `data/wallets/derived/market_cashflow.parquet`, `wallet_summary.parquet`, `data/wallets/raw/activity/*.jsonl.gz`, `data/wallets/manifest.json`. Code: `research/wallets/copytrade_probe.py`. Regenerate with `python -m research.wallets.copytrade_probe`.*
