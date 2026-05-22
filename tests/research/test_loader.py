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


from research.data.loader import list_tick_files, QUARANTINE_BEFORE

def test_list_tick_files_quarantines_march_by_default():
    files = list_tick_files("btc", "2026-03-01", "2026-05-22")
    assert all("2026-03" not in f for f in files), "March files must be quarantined"
    assert any("2026-05" in f for f in files), "May files must be present"

def test_list_tick_files_include_quarantined_opt_in():
    files = list_tick_files("btc", "2026-03-01", "2026-05-22", include_quarantined=True)
    assert any("2026-03" in f for f in files), "opt-in must include March"
    assert QUARANTINE_BEFORE == "2026-05-15"
