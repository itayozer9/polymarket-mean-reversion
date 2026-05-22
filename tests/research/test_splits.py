import pandas as pd
from research.lib.splits import (
    add_date_col, dev_mask, holdout_mask, day_blocked_kfold, leave_one_day_out,
)

def _frame():
    # window_start_ts at 6 distinct UTC days 2026-05-15..20 plus one holdout day
    days = ["2026-05-15", "2026-05-16", "2026-05-17", "2026-05-18",
            "2026-05-19", "2026-05-20", "2026-05-21"]
    ts = [int(pd.Timestamp(d, tz="UTC").timestamp()) for d in days]
    return pd.DataFrame({"window_start_ts": ts, "v": range(7)})

def test_add_date_col():
    df = add_date_col(_frame())
    assert list(df["date"])[:2] == ["2026-05-15", "2026-05-16"]

def test_dev_and_holdout_masks_partition():
    df = add_date_col(_frame())
    dev, hold = dev_mask(df), holdout_mask(df)
    assert (dev & hold).sum() == 0          # disjoint
    assert dev.sum() == 6 and hold.sum() == 1
    assert df.loc[hold, "date"].iloc[0] == "2026-05-21"

def test_day_blocked_kfold_covers_all_days_once():
    df = add_date_col(_frame()[_frame()["window_start_ts"] < _frame()["window_start_ts"].iloc[6]])
    folds = day_blocked_kfold(df, k=3, seed=0)
    test_days = set()
    for train_idx, test_idx in folds:
        assert set(train_idx).isdisjoint(test_idx)
        test_days |= set(df.loc[test_idx, "date"])
    assert len(test_days) == 6              # every dev day tested exactly once

def test_leave_one_day_out_yields_one_day_per_fold():
    df = add_date_col(_frame())
    folds = list(leave_one_day_out(df))
    assert len(folds) == 7
    for train_idx, test_idx in folds:
        assert df.loc[test_idx, "date"].nunique() == 1
        assert set(train_idx).isdisjoint(test_idx)
