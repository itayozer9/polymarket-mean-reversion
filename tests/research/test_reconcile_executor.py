"""Pins for the nightly executor-book reconciliation alarm (2026-08-08).

The honest side is computed from the REAL fills.jsonl x official labels (repo
convention: no mocking); the state side is a tmp file, so the diff logic is
exercised without depending on how the real books drift over time.
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from research.analysis.reconcile_executor import TOLERANCE, reconcile  # noqa: E402


def _state(tmp_path, sid, total):
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"version": 2, "strategies": {sid: {"realized_total": total}}}))
    return str(p)


def test_matching_book_is_quiet_and_inflated_book_alarms(tmp_path):
    sid = "fav_disagree_live"
    # first pass: read the honest total off a zero state, then re-check at that value
    probe = reconcile(state_path=_state(tmp_path, sid, 0.0), strict=True)
    honest = next(r["honest"] for r in probe if r["sid"] == sid)
    assert honest != 0.0

    ok = reconcile(state_path=_state(tmp_path, sid, honest), strict=True)
    assert [r["alarm"] for r in ok] == [False]

    bad = reconcile(state_path=_state(tmp_path, sid, honest + TOLERANCE + 1), strict=True)
    assert [r["alarm"] for r in bad] == [True]


def test_allowance_absorbs_the_frozen_det_drift(tmp_path):
    """det_lwd_live retired 2026-08-08 with a frozen +$13.97 book overstatement; the
    nightly run must stay quiet on it while --strict still flags it."""
    sid = "det_lwd_live"
    probe = reconcile(state_path=_state(tmp_path, sid, 0.0), strict=True)
    honest = next(r["honest"] for r in probe if r["sid"] == sid)
    drifted = honest + 13.97
    assert [r["alarm"] for r in reconcile(state_path=_state(tmp_path, sid, drifted))] == [False]
    assert [r["alarm"] for r in reconcile(state_path=_state(tmp_path, sid, drifted),
                                          strict=True)] == [True]
