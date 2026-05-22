import numpy as np
from research.dataset.ticks import build_window_ticks
from research.data.loader import load_tick_csv

def test_build_window_ticks_adds_features(fixtures_dir):
    raw = load_tick_csv(fixtures_dir / "btc_oneliner_15m.csv")
    slug = raw["market_slug"].iloc[0]
    out = build_window_ticks(slug, raw, outcome="Up")
    for col in ["proximity_pct", "sigma_proximity", "time_left_sec",
                "yes_drop_30s", "no_drop_30s", "yes_velocity_10s",
                "spot_move_30s", "realized_vol", "yes_imbalance", "outcome_up"]:
        assert col in out.columns, f"missing {col}"
    assert len(out) == len(raw)
    # time_left decreases monotonically
    assert out["time_left_sec"].is_monotonic_decreasing
    # no future leak: outcome_up is constant within the window
    assert out["outcome_up"].nunique() == 1
    # proximity_pct equals |move_pct| (the bug fix)
    assert np.allclose(out["proximity_pct"], raw["move_pct"].abs())
