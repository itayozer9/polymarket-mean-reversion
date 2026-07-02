"""Load-bearing parity for the honest re-settle (resettle_official.py).

The money is the test: for det_lwd_live (real fills, real settlements), the re-settled
official outcome per slug must equal what Polymarket actually paid us on, and the honest
pnl must follow the settle arithmetic exactly. Successor to the 288/288 official-labels
parity pin."""
import json
import os

import numpy as np
import pandas as pd
import pytest

SETTLEMENTS = "data/live/settlements.jsonl"
CACHE = "data/research/official_outcomes.parquet"


@pytest.fixture(scope="module")
def resettled():
    if not (os.path.exists(SETTLEMENTS) and os.path.exists(CACHE)):
        pytest.skip("needs live settlements + official label cache")
    from research.analysis.resettle_official import resettle_sid
    from research.dataset.official_outcomes import official_only_by_slug
    t = resettle_sid("det_lwd_live", official_only_by_slug())
    if t.empty:
        pytest.skip("no det_lwd_live trades_detailed ledger")
    return t


@pytest.fixture(scope="module")
def booked():
    rows = [json.loads(l) for l in open(SETTLEMENTS) if l.strip()]
    s = pd.DataFrame(rows)
    s = s[s["strategy_id"] == "det_lwd_live"].dropna(subset=["slug", "won"])
    # one settlement per slug for this strategy; keep the last if ever re-booked
    return s.groupby("slug").last()


def test_official_won_matches_real_money(resettled, booked):
    lab = resettled[resettled["label_status"] == "official"].set_index("slug")
    common = lab.index.intersection(booked.index)
    assert len(common) >= 100, f"too few common slugs to be meaningful: {len(common)}"
    mismatch = [sl for sl in common
                if bool(lab.loc[sl, "won_official"]) != bool(booked.loc[sl, "won"])]
    assert not mismatch, f"{len(mismatch)}/{len(common)} outcome mismatches, e.g. {mismatch[:5]}"


def test_pnl_official_arithmetic(resettled):
    lab = resettled[resettled["label_status"] == "official"]
    stake = lab["shares"] * lab["entry_price"]
    expect = np.where(lab["won_official"] == 1,
                      lab["shares"] * (1.0 - lab["entry_price"]) - lab["fee_total"],
                      -stake - lab["fee_total"])
    assert np.allclose(lab["pnl_official"].values, expect, atol=1e-9)


def test_flips_exactly_where_labels_disagree(resettled):
    """won_official must differ from the engine's recon won exactly on slugs where the
    official label disagrees with the recon label — no silent drift anywhere else."""
    lab = resettled[(resettled["label_status"] == "official")
                    & resettled["outcome_up_chainlink"].notna()].copy()
    label_flip = lab["official_up"].astype(int) != lab["outcome_up_chainlink"].astype(int)
    won_flip = lab["won_official"].astype(bool) != lab["won"].astype(bool)
    assert (label_flip == won_flip).all(), \
        f"{(label_flip != won_flip).sum()} rows drifted without a label flip"


def test_pending_never_scored(resettled):
    pend = resettled[resettled["label_status"] == "pending"]
    assert pend["pnl_official"].isna().all()
    assert pend["won_official"].isna().all()
