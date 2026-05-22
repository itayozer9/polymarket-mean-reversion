import numpy as np
from research.data.loader import load_tick_csv, ALL_TICK_COLS

def test_load_tick_csv_has_all_columns(fixtures_dir):
    df = load_tick_csv(fixtures_dir / "btc_oneliner_15m.csv")
    for col in ALL_TICK_COLS:
        assert col in df.columns, f"missing {col}"
    assert len(df) > 0
    # ticks are sorted by seconds_into_window
    assert df["seconds_into_window"].is_monotonic_increasing
    # spot columns are present and non-null on most rows
    assert df["coinbase_price"].notna().mean() > 0.5
