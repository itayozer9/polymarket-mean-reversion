# Task 6 — Fee & Cost Realism Working Notes

## Step 1: Polymarket Fee Structure (verified via WebFetch)

Source: `https://docs.polymarket.com/trading/fees`
Fetched: 2026-05-22. Page was accessible and returned clear fee documentation.

### Exact quote from the page

> fee = C × feeRate × p × (1 − p)

where C = shares traded and p = share price.

**Only takers pay fees. Makers are never charged.**

### Fee rates by category

| Category | Taker Fee Rate | Maker Fee |
|----------|---------------|-----------|
| **Crypto** | **0.07** | 0 |
| Sports | 0.03 | 0 |
| Finance, Politics, Tech, Mentions | 0.04 | 0 |
| Economics, Culture, Weather, Other | 0.05 | 0 |
| Geopolitics | 0 | 0 |

**Crypto markets (BTC/ETH/SOL/XRP up/down) use a 0.07 taker fee rate — the highest non-zero rate on Polymarket.**

### Additional details from the page

- "The fee amount in USDC is symmetric around 50% probability — a trade at 30¢ incurs the same dollar fee as a trade at 70¢."
- "Peak fees occur at 50% probability. For example, on crypto markets with 100 shares, maximum fee reaches $1.75 USDC."
- "Fees are rounded to 5 decimal places. The smallest fee charged is 0.00001 USDC."
- "Fees are calculated and applied at match time by the protocol" (no need to include in order price).

### Verification against `config.py`

The `FillParams` in `polymarket-arb/scripts/mean_reversion/config.py` documents
`fee_rate = 0.07` with the formula `shares × rate × p × (1-p)`. This is **confirmed
correct** by the live fee documentation. No discrepancy.

---

## Step 2: Round-trip cost quantification from real May data

**Data scope:** BTC + ETH 15m ticks, May 15–21 (7 files per symbol). Only
ticks with a valid two-sided book (`yes_best_ask > 0`, `no_best_ask > 0`,
`yes_best_bid > 0`, `yes_best_bid < yes_best_ask`) and odds in the
entry-relevant band `yes_mid ∈ [0.05, 0.35]` are included. Total: 206,610
ticks (101,531 BTC, 105,079 ETH).

**Method:**

- `shares = $10 / yes_best_ask` (buy the cheaper YES side)
- Entry fee: `shares × 0.07 × yes_best_ask × (1 − yes_best_ask)`
- Exit fee (reverting to bid): `shares × 0.07 × yes_best_bid × (1 − yes_best_bid)`
- Spread cost: `shares × (yes_best_ask − yes_best_bid)`
- Total round-trip cost = entry fee + exit fee + spread cost

### Spread statistics (BTC + ETH, yes_mid 0.05–0.35)

| Metric | Value |
|--------|-------|
| Median spread | 1.00¢ |
| Mean spread | 1.35¢ |
| P25 | 1.00¢ |
| P75 | 1.00¢ |
| P90 | 2.00¢ |
| Median spread as % of mid | 6.1% |
| Mean spread as % of mid | 8.7% |

### Fee breakdown at median entry price (0.21–0.22 ask, $10 stake)

| Component | $ amount | % of stake |
|-----------|----------|------------|
| Entry fee | $0.55 | 5.5% |
| Exit fee (at bid) | $0.52 | 5.2% |
| Spread cost | $0.59 | 5.9% |
| **Total round-trip** | **$1.67** | **16.7%** |

### Per-symbol breakdown (15m, odds band 0.05–0.35)

| Symbol | Med ask | Med spread | Med ask depth | Med total RT cost |
|--------|---------|------------|---------------|-------------------|
| BTC | 21.0¢ | 1.0¢ | $137 | 16.4% |
| ETH | 22.0¢ | 1.0¢ | $41 | 17.0% |
| SOL | 21.0¢ | 2.0¢ | $14 | 19.5% |
| XRP | 22.0¢ | 2.0¢ | $14 | 21.0% |

Note: SOL/XRP have 2× the spread of BTC/ETH and only ~$14 at top of book — these
are structurally harder markets to trade profitably.

### Break-even win rates by profit target (BTC/ETH, $10 stake at ~0.21 ask)

| Profit target | Gross win | Total cost | Net win | Break-even WR |
|---------------|-----------|-----------|---------|---------------|
| 15% | $1.50 | $1.64 | **−$0.14** | **>100% (impossible)** |
| 25% | $2.50 | $1.67 | $0.83 | **92.4%** |
| 50% | $5.00 | $1.75 | $3.25 | **75.5%** |
| 75% | $7.50 | $1.80 | $5.70 | **63.7%** |
| 100% | $10.00 | $1.84 | $8.16 | **55.1%** |
| 120% | $12.00 | $1.86 | $10.14 | **49.6%** |

A **+50% profit-target** trade (the most common config in `strategies.yaml`) requires
a **75.5% win rate** just to break even against fees and spread. The base rate of
winning (P(Down) in the entry band) is approximately 50–53%.

At a **15% profit target** — which some configs use — the trade is **not viable**: the
cost exceeds the gross profit regardless of win rate.

### Walk-the-book limitation

The cost analysis above uses only the top-of-book quoted spread. Actual slippage
for fills larger than the top-of-book depth cannot be measured from this data (the
schema carries only one depth level per side — Task 3 structural limitation). At
median BTC ask depth of $137, a $10 stake fits comfortably. But for SOL/XRP with
~$14 at top of book, a $10 order is already close to consuming the entire quoted
level. Any fill model that assumes instantaneous fill at the quoted ask without
market impact is optimistic for SOL/XRP even at $10 sizes.
