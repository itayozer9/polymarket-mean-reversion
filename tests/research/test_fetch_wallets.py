"""Unit tests for the pure helpers of `research.wallets.fetch_wallets`.

Covers wallet-union dedup (order-preserving, lowercased, limit) and the
board-appearance aggregation. No network — the fetch orchestration is
covered by the smoke run, per the task spec.
"""
from mean_reversion_live.clients.data_api import LeaderboardEntry

from research.wallets.fetch_wallets import board_appearances, build_wallet_union


def _entry(rank, wallet):
    return LeaderboardEntry(
        rank=rank, proxy_wallet=wallet, user_name=f"u{rank}", vol=0.0, pnl=0.0
    )


def test_build_wallet_union_dedups_across_boards_preserving_order():
    boards = {
        "MONTH": [_entry(1, "0xAAA"), _entry(2, "0xBBB")],
        "WEEK": [_entry(1, "0xBBB"), _entry(2, "0xCCC")],
    }
    union = build_wallet_union(boards)
    # First-seen order, lowercased, each wallet once.
    assert union == ["0xaaa", "0xbbb", "0xccc"]


def test_build_wallet_union_drops_empty_addresses():
    boards = {"MONTH": [_entry(1, ""), _entry(2, "0xABC")]}
    assert build_wallet_union(boards) == ["0xabc"]


def test_build_wallet_union_respects_limit():
    boards = {"MONTH": [_entry(i, f"0x{i:03d}") for i in range(10)]}
    assert build_wallet_union(boards, limit=3) == ["0x000", "0x001", "0x002"]


def test_build_wallet_union_limit_none_returns_all():
    boards = {"MONTH": [_entry(i, f"0x{i:03d}") for i in range(5)]}
    assert len(build_wallet_union(boards, limit=None)) == 5


def test_board_appearances_records_period_and_rank():
    boards = {
        "MONTH": [_entry(3, "0xAAA"), _entry(7, "0xBBB")],
        "WEEK": [_entry(1, "0xAAA")],
    }
    app = board_appearances(boards)
    assert app["0xaaa"] == [
        {"period": "MONTH", "rank": 3},
        {"period": "WEEK", "rank": 1},
    ]
    assert app["0xbbb"] == [{"period": "MONTH", "rank": 7}]


def test_board_appearances_drops_empty_addresses():
    boards = {"MONTH": [_entry(1, ""), _entry(2, "0xABC")]}
    app = board_appearances(boards)
    assert "" not in app
    assert list(app.keys()) == ["0xabc"]
