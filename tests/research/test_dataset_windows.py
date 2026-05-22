import pandas as pd
from research.dataset.windows import build_window_row
from research.data.loader import load_tick_csv

def test_build_window_row_fields(fixtures_dir):
    ticks = load_tick_csv(fixtures_dir / "btc_oneliner_15m.csv")
    slug = ticks["market_slug"].iloc[0]
    row = build_window_row(slug, ticks, outcome="Up", end_price=70000.0)
    assert row["slug"] == slug
    assert row["symbol"] == "btc"
    assert row["timeframe"] == "15m"
    assert row["n_ticks"] == len(ticks)
    assert row["outcome"] == "Up"
    assert row["outcome_up"] == 1
    assert 0.0 <= row["min_yes_mid"] <= 1.0
    assert row["strike"] == ticks["start_price"].iloc[0]
