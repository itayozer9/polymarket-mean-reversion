import numpy as np
from research.lib.stats import window_clustered_bootstrap, reliability_curve

def test_bootstrap_ci_brackets_mean():
    rng = np.random.default_rng(0)
    # 200 windows, each 1 value ~ N(0.3, 1)
    groups = np.arange(200)
    values = rng.normal(0.3, 1.0, 200)
    lo, mid, hi = window_clustered_bootstrap(values, groups, n=2000, seed=1)
    assert lo < 0.3 < hi
    assert lo < mid < hi

def test_bootstrap_resamples_whole_windows():
    # All ticks of a window share an outcome; CI must reflect window count, not tick count.
    groups = np.repeat(np.arange(10), 100)   # 10 windows, 100 ticks each
    values = np.repeat(np.arange(10) % 2, 100).astype(float)  # window-level 0/1
    lo, mid, hi = window_clustered_bootstrap(values, groups, n=2000, seed=0)
    # 10 windows of a Bernoulli(0.5) -> wide CI; tick-level would be falsely tight
    assert hi - lo > 0.2

def test_reliability_curve_perfectly_calibrated():
    rng = np.random.default_rng(0)
    p = rng.uniform(0, 1, 5000)
    y = (rng.uniform(0, 1, 5000) < p).astype(float)   # calibrated by construction
    groups = np.arange(5000)
    curve = reliability_curve(p, y, groups, n_bins=10, seed=0)
    # realized frequency tracks the bin's mean predicted prob within CI
    for row in curve:
        assert row["ci_lo"] <= row["mean_pred"] <= row["ci_hi"] or abs(row["realized"] - row["mean_pred"]) < 0.1
