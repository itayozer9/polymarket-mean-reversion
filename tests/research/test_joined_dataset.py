"""Invariant checks on the joined, fully-instrumented dataset.

Skips if data/research/joined_15m.parquet has not been built yet (so the suite
stays green on a fresh checkout). When present, asserts the join is sane:
labels in range, book ordered, trade flow non-negative, L2 agrees with the tick
top-of-book, one row per (slug, second).
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

PARQUET = REPO / "data" / "research" / "joined_15m.parquet"
pytestmark = pytest.mark.skipif(not PARQUET.exists(),
                                reason="joined_15m.parquet not built")


@pytest.fixture(scope="module")
def df():
    import pandas as pd
    return pd.read_parquet(PARQUET)


def test_labels_in_range(df):
    vals = set(df["outcome_up_clean"].dropna().unique())
    assert vals <= {0.0, 1.0}


def test_splits_valid(df):
    assert set(df["split"].unique()) <= {"dev", "holdout", "future", "pre"}


def test_book_ordered_on_healthy_books(df):
    # The ~8% of ticks that are crossed/one-sided are decided-market collapses
    # (yes_mid -> 0/1 near resolution) — the known artifact the book_healthy
    # guard removes. On healthy two-sided books the order must hold ~always.
    sub = df[df["book_healthy"]]
    assert (sub["yes_best_bid"] < sub["yes_best_ask"]).mean() > 0.999


def test_healthy_book_rate_matches_prior_research(df):
    # Prior research found ~92% of ticks are healthy two-sided books.
    assert 0.85 <= df["book_healthy"].mean() <= 0.95


def test_trade_flow_nonneg(df):
    for c in ("tr_bull_usd", "tr_bear_usd", "tr_n"):
        assert (df[c].fillna(0) >= 0).all()


def test_l2_agrees_with_tick_top_of_book(df):
    m = df["l2_best_bid"].notna()
    sub = df[m]
    diff = (sub["l2_best_bid"] - sub["yes_best_bid"]).abs()
    # within one 1c tick the vast majority of the time
    assert (diff <= 0.0101).mean() > 0.95


def test_one_row_per_slug_second(df):
    dup = df.duplicated(["slug", "seconds_into_window"]).mean()
    assert dup < 0.001


def test_dist_strike_finite_where_valid(df):
    import numpy as np
    m = df["cb_spot"].notna() & (df["start_price"] > 0)
    assert np.isfinite(df.loc[m, "dist_strike_bps"]).mean() > 0.99
