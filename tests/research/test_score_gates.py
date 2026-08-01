"""Known-answer pins for score_gates. Both windows are CLOSED (every entry settled and
labelled), so the numbers are frozen: a drift here means the tool or the canonical
data changed, not the market. Live pin = the 07-17 published gate read (test_ledger.md);
paper pin = xh5y_g2_v1 virgin era cut at 08-01 00:00 UTC (matches the 08-01 canonical
scoreboard row minus that morning's 3 entries)."""
import os

import pytest

from research.analysis import score_gates

pytestmark = pytest.mark.skipif(
    not (os.path.exists(score_gates.FILLS)
         and os.path.exists(os.path.join(score_gates.REPO, "data", "research",
                                         "official_outcomes.parquet"))),
    reason="live data files not present")


def test_live_reproduces_0717_gate_read():
    s = score_gates.main(["live", "--sid", "fav_disagree_live",
                          "--since", "2026-07-03T07:40", "--until", "2026-07-17T23:59"])
    assert s["n"] == 23
    assert abs(s["total"] - 72.33) < 0.02
    assert abs(s["ev_fill"] - 3.145) < 0.005
    assert abs(s["per_dollar"] - 0.339) < 0.005
    assert s["pending"] == 0


def test_paper_reproduces_canonical_scoreboard_row():
    s = score_gates.main(["paper", "--sids", "xh5y_g2_v1",
                          "--since", "2026-06-19", "--until", "2026-08-01"])
    assert s["n"] == 65
    assert abs(s["total"] - 105.81) < 0.02
    assert abs(s["ev_fill"] - 1.628) < 0.005
