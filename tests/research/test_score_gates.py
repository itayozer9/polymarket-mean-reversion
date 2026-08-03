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


def test_load_fills_drops_synthetic_smoke_records():
    """16 synthetic rows (token_id "UPTOK") live in the real ledger and 3 carry a REAL sid
    (det_d12_wide_live, ok=true, dry_run=false), so they survive every sid filter. No
    registered gate window reaches back to them (all start 07-03+), but a lifetime read
    without --since would have scored fake fills as real money."""
    df = score_gates.load_fills()
    assert "UPTOK" not in set(df.get("token_id", []))


def test_live_symbol_filter_partitions_the_same_window():
    """--symbol on live mode (added for the 08-07 hype fill-rate calibration) slices the
    ledger by the slug prefix. Pinned on the CLOSED, already-published 07-17 window: the
    per-symbol fills must partition the unfiltered total exactly — no drops, no dupes."""
    W = ["--sid", "fav_disagree_live", "--since", "2026-07-03T07:40",
         "--until", "2026-07-17T23:59"]
    total = score_gates.main(["live"] + W)["n"]
    parts = sum(score_gates.main(["live"] + W + ["--symbol", s])["n"]
                for s in ("btc", "eth", "sol", "xrp"))
    assert parts == total == 23
    # a symbol the book never traded live yields an empty slice, not an error
    assert score_gates.main(["live"] + W + ["--symbol", "hype"])["n"] == 0
