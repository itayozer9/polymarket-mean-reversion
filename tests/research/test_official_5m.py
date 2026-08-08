"""Sanity pins for the first-ever official 5m labels (0B, Edge Hunt v2).

No recon fallback exists for 5m (resettle_chainlink hardcodes +900), so these labels are
official-only. Pins: (1) per-day coverage of the clean-era 5m universe; (2) large-move
agreement — a window whose Coinbase move is >= 20bps cannot flip across oracles, so
official must match the Coinbase direction there; (3) the near-strike disagreement rate is
REPORTED (expected ~5-20%, mirroring 15m — that disagreement existing is evidence the
fetch pulled real independent labels, not an echo of our own data)."""
import os

import numpy as np
import pandas as pd
import pytest

CACHE = "data/research/official_outcomes.parquet"
OUTCOMES = "data/outcomes.csv"
CLEAN_START = "2026-05-23"


@pytest.fixture(scope="module")
def joined():
    if not (os.path.exists(CACHE) and os.path.exists(OUTCOMES)):
        pytest.skip("needs official cache + outcomes.csv")
    oc = pd.read_csv(OUTCOMES, usecols=["market_slug", "window_start_ts", "window_end_ts",
                                        "start_price", "end_price"])
    oc = oc[(oc["window_end_ts"] - oc["window_start_ts"]) == 300]
    oc["date"] = pd.to_datetime(oc["window_start_ts"], unit="s", utc=True).dt.strftime("%Y-%m-%d")
    oc = oc[oc["date"] >= CLEAN_START]
    oc = oc.drop_duplicates("market_slug")
    from research.dataset.official_outcomes import official_only_by_slug
    off = official_only_by_slug()
    j = oc.merge(off, left_on="market_slug", right_on="slug", how="left")
    if j["official_up"].notna().sum() == 0:
        pytest.skip("5m labels not backfilled yet")
    return j


def test_5m_coverage_per_day(joined):
    # exclude today (windows may be unresolved for minutes after close)
    today = pd.Timestamp.now("UTC").strftime("%Y-%m-%d")
    j = joined[joined["date"] < today].copy()
    # A window unresolved across ALL coins simultaneously is a Polymarket void, not a
    # label-pipeline failure (first seen 2026-08-05 wts=1785941700: all 7 coins NaN,
    # still unresolved after refetch). Coverage is measured over resolvable windows;
    # any PARTIAL gap (some coins labelled, some not) still counts and still alarms.
    voided = j.groupby("window_start_ts")["official_up"].transform(
        lambda s: s.isna().all())
    cov = j[~voided].groupby("date")["official_up"].agg(lambda s: s.notna().mean())
    bad = cov[cov < 0.99]
    assert bad.empty, f"days under 99% 5m label coverage (voids excluded):\n{bad}"


def test_5m_large_move_agrees_with_coinbase(joined):
    """Post-strike-fix windows only: pre-2026-06-13 11:05 UTC outcomes.csv captured the 5m
    start_price 24-55s late, faking big 'moves' on OUR side (the official label is fine).
    end_price==0 rows are corrupted Coinbase prints, excluded. Post-fix, ~0.65% of >=20bps
    Coinbase moves still flip on the official (Chainlink Data Streams) close — genuine
    oracle basis at 5m horizon, ~10x the 15m rate; the pin allows it with headroom."""
    fix = pd.Timestamp("2026-06-13 11:05", tz="UTC").timestamp()
    j = joined[joined["official_up"].notna() & (joined["window_start_ts"] >= fix)
               & (joined["start_price"] > 0) & (joined["end_price"] > 0)].copy()
    move_bps = (j["end_price"] - j["start_price"]) / j["start_price"] * 1e4
    big = j[np.abs(move_bps) >= 20]
    cb_up = (big["end_price"] >= big["start_price"]).astype(int)
    agree = (cb_up == big["official_up"].astype(int)).mean()
    assert agree >= 0.99, f"large-move official-vs-coinbase agreement only {agree:.4f}"


def test_5m_near_strike_disagreement_reported(joined):
    j = joined[joined["official_up"].notna()
               & (joined["start_price"] > 0) & joined["end_price"].notna()].copy()
    move_bps = (j["end_price"] - j["start_price"]) / j["start_price"] * 1e4
    cb_up = (j["end_price"] >= j["start_price"]).astype(int)
    dis = (cb_up != j["official_up"].astype(int))
    near = np.abs(move_bps) < 2.0
    overall, near_rate = dis.mean(), dis[near].mean()
    print(f"\n5m official-vs-coinbase disagreement: overall {overall*100:.2f}%, "
          f"within 2bps of strike {near_rate*100:.2f}% (n_near={int(near.sum())})")
    # labels must be neither an echo of Coinbase (0 disagreement near strike would be
    # suspicious) nor garbage (>40% near-strike would mean a broken join)
    assert 0.005 <= near_rate <= 0.40
