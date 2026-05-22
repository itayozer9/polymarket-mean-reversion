# Canonical Research Dataset — Phase 1

**Built:** 2026-05-22
**Data scope:** 2026-05-15 .. 2026-05-22 (March quarantined — corrupt order book; see `phase0_verdict.md`)
**Symbols:** btc, eth, sol, xrp
**Timeframes:** 15m, 5m
**Build script:** `research/build_dataset.py` (`uv run python -m research.build_dataset`)
**Output dir:** `data/research/` (gitignored — rebuild from source)

---

## Files

| File | Rows | Size | Description |
|---|---|---|---|
| `windows.parquet` | 8,915 | 0.38 MB | One row per market window |
| `ticks_15m.parquet` | 2,000,171 | 58.9 MB | One row per tick (15m windows) + all derived features |
| `ticks_5m.parquet` | 2,000,340 | 68.2 MB | One row per tick (5m windows) + all derived features |

---

## Row counts per symbol × timeframe

### windows.parquet

| Symbol | 15m windows | 5m windows |
|---|---|---|
| btc | 558 | 1,671 |
| eth | 558 | 1,670 |
| sol | 558 | 1,671 |
| xrp | 558 | 1,671 |
| **Total** | **2,232** | **6,683** |

8,910 / 8,915 windows have an outcome record; 5 partial windows (NaN outcome, typically 1-tick stubs from truncated gzip writes) are included for completeness but have `outcome=None`.

### ticks_15m.parquet (ticks per symbol)

| Symbol | Ticks |
|---|---|
| btc | 499,951 |
| eth | 499,872 |
| sol | 500,098 |
| xrp | 500,250 |
| **Total** | **2,000,171** |

### ticks_5m.parquet (ticks per symbol)

| Symbol | Ticks |
|---|---|
| btc | 499,940 |
| eth | 500,060 |
| sol | 500,093 |
| xrp | 500,245 |
| **Total** | **2,000,340** |

Average ~900 ticks per 15m window (out of 900 possible = 1 Hz), ~300 per 5m window.

---

## Outcome base rates (P(Up) per symbol, 15m)

These are the no-skill baselines every Phase 2+ analysis must beat.

| Symbol | P(Up) | n windows |
|---|---|---|
| btc | 0.494 | 557 |
| eth | 0.481 | 557 |
| sol | 0.474 | 557 |
| xrp | 0.427 | 557 |

xrp has a notably bearish tilt (57% Down) over this period.

---

## Outcomes.csv coverage

`data/outcomes.csv` has 10,303 unique market slugs for May 15–22. The build produced tick data for 8,915 (86.5%). The 1,388 missing windows have outcome records but no tick files on disk — these are windows the live bot tracked via its outcomes writer but for which the WS collector produced no data (e.g., windows that opened before the bot started on a given day, or were dropped by the WS connection).

This is expected behaviour, not a builder bug.

---

## Schema: windows.parquet

| Column | Dtype | Notes |
|---|---|---|
| `slug` | str | `<sym>-updown-<tf>-<window_start_ts>` |
| `symbol` | str | btc / eth / sol / xrp |
| `timeframe` | str | 15m / 5m |
| `window_start_ts` | int64 | Unix seconds (UTC) |
| `window_end_ts` | int64 (nullable) | Unix seconds; None for partial windows |
| `strike` | float64 | start_price — the threshold for Up/Down settlement |
| `n_ticks` | int64 | Number of ticks in this window's tick file |
| `first_sec` | int64 (nullable) | seconds_into_window of first tick |
| `last_sec` | int64 (nullable) | seconds_into_window of last tick |
| `outcome` | str (nullable) | "Up" / "Down" / None |
| `outcome_up` | int64 (nullable) | 1=Up, 0=Down, None=no outcome |
| `end_price` | float64 (nullable) | Settlement price from outcomes.csv |
| `min_yes_mid` | float64 | Minimum yes_mid across the window |
| `max_yes_mid` | float64 | Maximum yes_mid across the window |
| `min_no_mid` | float64 | Minimum no_mid across the window |
| `max_no_mid` | float64 | Maximum no_mid across the window |
| `max_abs_move_pct` | float64 | Maximum |move_pct| in percent |
| `median_yes_ask_depth` | float64 | Median USD depth at best ask (yes side) |
| `median_no_ask_depth` | float64 | Median USD depth at best ask (no side) |

---

## Schema: ticks_15m.parquet / ticks_5m.parquet

These files share the same schema. Every row is one tick (~1 Hz) from the live WS feed, with all 23 raw columns plus derived features.

### Raw columns (23 — from source CSV)

| Column | Dtype | Notes |
|---|---|---|
| `timestamp_ms` | int64 | Wall-clock time of tick (UTC, milliseconds) |
| `market_slug` | str | Same as `slug` |
| `symbol` | str | btc / eth / sol / xrp |
| `window_start_ts` | int64 | Unix seconds (UTC) |
| `window_end_ts` | float64 | Unix seconds; NaN for partial windows |
| `seconds_into_window` | float64 | Seconds elapsed since window open |
| `yes_best_bid` | float64 | Best bid on the YES token [0, 1] |
| `yes_best_ask` | float64 | Best ask on the YES token [0, 1] |
| `yes_bid_depth` | float64 | USD depth at best bid (YES) |
| `yes_ask_depth` | float64 | USD depth at best ask (YES) |
| `no_best_bid` | float64 | Best bid on the NO token [0, 1] |
| `no_best_ask` | float64 | Best ask on the NO token [0, 1] |
| `no_bid_depth` | float64 | USD depth at best bid (NO) |
| `no_ask_depth` | float64 | USD depth at best ask (NO) |
| `chainlink_price` | float64 | Chainlink oracle price (USD) |
| `coinbase_price` | float64 | Coinbase spot price (USD) |
| `start_price` | float64 | Strike price — the Up/Down threshold |
| `move_pct` | float64 | (spot − strike) / strike × 100, in **percent** |
| `yes_mid` | float64 | (yes_best_bid + yes_best_ask) / 2 |
| `no_mid` | float64 | (no_best_bid + no_best_ask) / 2 |
| `spread_yes` | float64 | yes_best_ask − yes_best_bid |
| `spread_no` | float64 | no_best_ask − no_best_bid |
| `total_mid` | float64 | yes_mid + no_mid (≈ 1.0 when consistent) |

### Derived feature columns

| Column | Dtype | Notes |
|---|---|---|
| `time_left_sec` | int32 | Seconds remaining in window; 0 at expiry |
| `proximity_pct` | float64 | \|move_pct\| — **corrected** proximity in percent (fixes the arb proximity_pct_from_move bug that divided by 100) |
| `realized_vol` | float64 | Trailing 60-tick std-dev of tick-to-tick move_pct changes (percent/tick ≈ percent/sec) |
| `sigma_proximity` | float64 | proximity_pct / (realized_vol × √time_left_sec); inf when vol=0 or time=0 |
| `yes_drop_15s` | float64 | % drop of yes_mid from trailing 15-tick peak |
| `yes_drop_30s` | float64 | % drop of yes_mid from trailing 30-tick peak |
| `yes_drop_60s` | float64 | % drop of yes_mid from trailing 60-tick peak |
| `no_drop_15s` | float64 | % drop of no_mid from trailing 15-tick peak |
| `no_drop_30s` | float64 | % drop of no_mid from trailing 30-tick peak |
| `no_drop_60s` | float64 | % drop of no_mid from trailing 60-tick peak |
| `yes_velocity_10s` | float64 | yes_mid[i] − yes_mid[i−10] (signed) |
| `yes_velocity_30s` | float64 | yes_mid[i] − yes_mid[i−30] (signed) |
| `no_velocity_10s` | float64 | no_mid[i] − no_mid[i−10] (signed) |
| `no_velocity_30s` | float64 | no_mid[i] − no_mid[i−30] (signed) |
| `spot_move_10s` | float64 | move_pct[i] − move_pct[i−10] — spot-side counterpart to odds velocity |
| `spot_move_30s` | float64 | move_pct[i] − move_pct[i−30] |
| `yes_imbalance` | float64 | yes_bid_depth / (yes_bid_depth + yes_ask_depth); 0.5 when both zero |
| `no_imbalance` | float64 | no_bid_depth / (no_bid_depth + no_ask_depth); 0.5 when both zero |
| `outcome` | str (nullable) | "Up" / "Down" / None — window-level label |
| `outcome_up` | float64 (nullable) | 1.0=Up, 0.0=Down, NaN=no outcome — numeric label |
| `slug` | str | Window slug (same as market_slug) |

---

## How to load

```python
import pandas as pd

windows  = pd.read_parquet("data/research/windows.parquet")
ticks15m = pd.read_parquet("data/research/ticks_15m.parquet")
ticks5m  = pd.read_parquet("data/research/ticks_5m.parquet")

# All ticks for one window:
slug = "btc-updown-15m-1779005400"
w_ticks = ticks15m[ticks15m["slug"] == slug]

# Development set only (hold-out sealed):
from research.holdout import DEV_START, DEV_END
import pandas as pd
dev_windows = windows[
    windows["window_start_ts"].apply(
        lambda ts: DEV_START <= pd.Timestamp(ts, unit="s").strftime("%Y-%m-%d") <= DEV_END
    )
]
```

---

## Build validation (Step 3 assertions)

Run at build time on 2026-05-22:

| Assertion | Result |
|---|---|
| proximity_pct == \|move_pct\| everywhere (ticks_15m) | PASS — max diff = 0.00e+00 |
| time_left_sec never negative (ticks_15m) | PASS — 0 negative rows |
| outcome_up constant within each slug (ticks_15m) | PASS — 0 violating slugs |
| sigma_proximity finite for >95% of ticks with time_left_sec > 5 | PASS — 96.65% finite (1,989,167 ticks checked) |
| windows rows within 5% of outcomes unique slugs | NOTE: 8,915 built vs 10,303 unique outcome slugs (86.5% coverage). The 1,388 missing windows have outcome records but no tick files — windows tracked by the bot that had no live WS data. Not a bug. |

---

## Important notes for Phase 2+

1. **Hold-out is sealed.** Dev range: 2026-05-15 .. 2026-05-20. Hold-out: 2026-05-21 .. 2026-05-22. See `research/holdout.py`. Do not fit or select on hold-out data.

2. **move_pct is in percent**, not fraction. Do not divide by 100 again. The corrected `proximity_pct` feature is already in percent.

3. **sigma_proximity is inf** for the first ~60 ticks of each window (when `realized_vol = 0` because no history exists yet). Filter with `np.isfinite(sigma_proximity)` before use.

4. **Top-of-book only.** The data carries depth at the best level only (`yes_ask_depth` = USD available at `yes_best_ask`). Walk-the-book slippage for larger orders cannot be measured — see Phase 0 Task 3/6 findings in `phase0_audit.md`.

5. **Outcome resolution feed.** Phase 0 Task 4 identifies which feed (Chainlink vs Coinbase) Polymarket uses for settlement — consult `phase0_audit.md` before building fair-value models.
