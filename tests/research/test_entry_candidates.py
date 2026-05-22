import numpy as np, pandas as pd
from research.analysis.entry_candidates import build_entry_candidates

def _ticks():
    # 2 ticks: one YES-cheap, one NO-cheap
    return pd.DataFrame({
        "slug": ["s", "s"], "symbol": ["btc", "btc"],
        "timestamp_ms": [1_747_000_000_000, 1_747_000_001_000],
        "window_start_ts": [1_747_000_000, 1_747_000_000],
        "seconds_into_window": [10, 11], "time_left_sec": [890, 889],
        "yes_mid": [0.20, 0.80], "no_mid": [0.80, 0.20],
        "yes_best_ask": [0.21, 0.81], "yes_best_bid": [0.19, 0.79],
        "no_best_ask": [0.81, 0.21], "no_best_bid": [0.79, 0.19],
        "yes_ask_depth": [50.0, 60.0], "no_ask_depth": [70.0, 80.0],
        "proximity_pct": [0.1, 0.1], "sigma_proximity": [0.5, 0.5],
        "realized_vol": [0.05, 0.05],
        "yes_drop_30s": [40.0, 0.0], "no_drop_30s": [0.0, 40.0],
        "yes_velocity_30s": [-0.1, 0.0], "no_velocity_30s": [0.0, -0.1],
        "spot_move_30s": [-0.2, 0.2],
        "yes_imbalance": [0.6, 0.4], "no_imbalance": [0.4, 0.6],
        "outcome_up": [1.0, 1.0],
    })

def test_cheap_side_selection_and_label():
    ec = build_entry_candidates(_ticks())
    assert list(ec["cheap_side"]) == ["YES", "NO"]
    assert ec["cheap_mid"].iloc[0] == 0.20 and ec["cheap_mid"].iloc[1] == 0.20
    # tick 0: cheap=YES, outcome Up -> cheap_won=1 ; tick 1: cheap=NO, outcome Up -> cheap_won=0
    assert ec["cheap_won"].iloc[0] == 1.0
    assert ec["cheap_won"].iloc[1] == 0.0
    # cheap-side ask/bid/drop pulled from the correct side
    assert ec["cheap_ask"].iloc[0] == 0.21 and ec["cheap_ask"].iloc[1] == 0.21
    assert ec["cheap_drop_30s"].iloc[0] == 40.0 and ec["cheap_drop_30s"].iloc[1] == 40.0
