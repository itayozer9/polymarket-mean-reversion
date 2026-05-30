# sweep_v2 — Strategy Discovery Report
*Generated 2026-05-23 14:35 UTC*

## Headline
- Total evaluations across stages 3-8: **2,665**
- Final survivors (Bonferroni-GOLD + stress + walk-forward + replay + March): **1**

## Final picks

| # | config_id | sharpe | pooled_pnl | n_trades | pooled_p_value | folds_pass | march_pnl |
|---|-----------|-------:|-----------:|---------:|---------------:|-----------:|----------:|
| 1 | `1be3e4e39192` | 0.915 | +$362.92 | 143 | 3.95e-01 | 4/5 | +$565.03 |

## Per-pick detail

### 1. `1be3e4e39192`

**Config:**
```json
{
  "entry.side": "BOTH",
  "entry.entry_price_min": 0.09031478099581848,
  "entry.entry_price_max": 0.3189990074178029,
  "entry.drop_magnitude_pct": 15.038191127543117,
  "entry.drop_window_sec": 296,
  "entry.min_time_left_sec": 223,
  "entry.proximity_max_pct": 93.17352957482386,
  "entry.min_seconds_into_window": 94,
  "exit.profit_target_pct": 269.98340725575076,
  "exit.stop_loss_pct": null,
  "exit.max_hold_sec": 537,
  "exit.trailing_stop_pct": null,
  "filter.min_book_depth_usd": 76.45258989284731,
  "filter.max_spread": 0.02808037554116574,
  "filter.book_imbalance_min": null,
  "filter.vol_regime": "ALL",
  "filter.time_of_day": "ASIA",
  "human.signal_skip_prob": 0.028915951978647072,
  "human.daily_trade_cap": 20,
  "human.concurrent_position_cap": null,
  "fill.fee_rate": 0.07,
  "fill.reject_prob": 0.09016200067936624,
  "filter_v2.use_macro_stress": false,
  "filter_v2.macro_stress_min_symbols": 3,
  "filter_v2.use_rv_regime": true,
  "filter_v2.rv_regime": "HIGH",
  "filter_v2.use_depth_imbalance": true,
  "filter_v2.depth_imbalance_min": 0.4382232787751126,
  "filter_v2.use_btc_lead": false,
  "filter_v2.btc_lead_pct_min": 0.6552021491782616,
  "filter_v2.use_spread_zscore": false,
  "filter_v2.spread_zscore_max": -1.037497498997882,
  "filter_v2.use_expiry_bucket": false,
  "filter_v2.expiry_bucket": "MID"
}
```

**Stress tests:**
- seed_stability: pass=True
- param_1d_neighborhood: pass=True
- joint_perturbation: pass=True
- adversarial_costs: pass=True
- liquidity_shock: pass=True
- per_symbol: pass=True

## SHAP feature importance (top 15)

- entry.min_time_left_sec: 0.3636
- entry.entry_price_max: 0.3008
- entry.drop_magnitude_pct: 0.2401
- filter.min_book_depth_usd: 0.2105
- filter_v2.spread_zscore_max: 0.2072
- entry.min_seconds_into_window: 0.1966
- entry.entry_price_min: 0.1746
- human.daily_trade_cap: 0.1169
- fill.reject_prob: 0.1146
- exit.max_hold_sec: 0.1066
- filter_v2.btc_lead_pct_min: 0.0999
- exit.profit_target_pct: 0.0824
- filter.max_spread: 0.0641
- entry.proximity_max_pct: 0.0637
- filter_v2.depth_imbalance_min: 0.0525

## Correlation clusters of final survivors

- Cluster 0: 1be3e4e39192
