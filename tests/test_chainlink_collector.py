"""Tests for the additive Chainlink on-chain oracle stream (Stream 3).

Covers:
  - decode_latest_round_data: ABI-decoding of the 5-word return tuple,
    two's-complement handling, malformed-input rejection.
  - ChainlinkCsvGzAppender: header, per-symbol-per-date files, segment-flush
    mid-run readability, value round-trip.
  - chainlink_loop: live RPC smoke test (network) — best-effort, skipped if
    the public RPC is unreachable.

This stream is separate from the (always-0.0) `chainlink_price` column of the
sacred 23-column tick CSV.
"""
from __future__ import annotations
import asyncio
import csv
import gzip
import io
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from mean_reversion_live.collectors.chainlink_collector import (  # noqa: E402
    CHAINLINK_COLUMNS,
    CHAINLINK_DECIMALS,
    CHAINLINK_FEEDS,
    ChainlinkCsvGzAppender,
    ChainlinkPriceCache,
    _utc_date_str,
    chainlink_loop,
    decode_latest_round_data,
)


def _write_cl_gz(base: Path, fname: str, rows):
    """Write a live_chainlink CSV.gz (rows = list of (ts_ms, symbol, price))."""
    d = base / "live_chainlink"
    d.mkdir(parents=True, exist_ok=True)
    with gzip.open(d / fname, "wt") as fh:
        fh.write(",".join(CHAINLINK_COLUMNS) + "\n")
        for ts, sym, px in rows:
            rec = {c: "" for c in CHAINLINK_COLUMNS}
            rec["timestamp_ms"], rec["symbol"], rec["price"] = ts, sym, px
            fh.write(",".join(str(rec[c]) for c in CHAINLINK_COLUMNS) + "\n")


def test_warm_from_disk_seamless_restart(tmp_path):
    """warm_from_disk pre-loads recent reads so price_asof works immediately after a
    restart; excludes pre-cutoff (stale) reads and _hist backfills."""
    now = 1_780_000_000_000
    date = _utc_date_str(now)
    # recent (within 40min default) + one beyond the keep horizon (excluded)
    _write_cl_gz(tmp_path, f"btc_{date}.csv.gz",
                 [(now - 30 * 60_000, "btc", 100.0),
                  (now - 60_000, "btc", 101.0),
                  (now - 2 * 3_600_000, "btc", 99.0)])  # 2h old -> excluded
    # a _hist backfill with a (fake) recent ts MUST be ignored
    _write_cl_gz(tmp_path, f"btc_hist_{date}.csv.gz", [(now - 10_000, "btc", 1.0)])

    c = ChainlinkPriceCache()
    n = c.warm_from_disk(tmp_path, now, symbols=["btc"])
    assert n == 2                                   # 2 recent kept; 2h-old + hist excluded
    assert c.price_asof("btc", now) == 101.0        # latest at-or-before now, within tolerance
    # a window that "opened" 30min ago can be settled/gated immediately post-restart
    assert c.price_asof("btc", now - 30 * 60_000) == 100.0
    assert c.warm_from_disk(tmp_path, now, symbols=["eth"]) == 0   # symbol filter


def test_warm_from_disk_no_dir_is_noop(tmp_path):
    c = ChainlinkPriceCache()
    assert c.warm_from_disk(tmp_path, 1_780_000_000_000, symbols=["btc"]) == 0


def _read_gz_csv(path: Path):
    with gzip.open(path, "rb") as f:
        text = f.read().decode("utf-8")
    return list(csv.DictReader(io.StringIO(text)))


def _word(value: int) -> str:
    """Encode an unsigned int as a 32-byte (64-hex-char) word."""
    return f"{value & ((1 << 256) - 1):064x}"


# ----- decode_latest_round_data ---------------------------------------------

def test_decode_latest_round_data_basic():
    # roundId, answer, startedAt, updatedAt, answeredInRound
    raw = "0x" + _word(12345) + _word(7683937507000) + _word(1779468000) \
        + _word(1779468050) + _word(12345)
    out = decode_latest_round_data(raw)
    assert out["round_id"] == 12345
    assert out["answer_raw"] == 7683937507000
    assert out["started_at"] == 1779468000
    assert out["updated_at"] == 1779468050
    assert out["answered_in_round"] == 12345
    # Human price with 8 decimals.
    assert out["answer_raw"] / (10 ** CHAINLINK_DECIMALS) == 76839.37507


def test_decode_latest_round_data_negative_answer():
    # int256 answer can be negative — two's complement.
    neg = (1 << 256) - 500  # encodes -500
    raw = "0x" + _word(1) + _word(neg) + _word(0) + _word(0) + _word(1)
    out = decode_latest_round_data(raw)
    assert out["answer_raw"] == -500


def test_decode_latest_round_data_rejects_empty():
    assert decode_latest_round_data("0x") is None
    assert decode_latest_round_data("") is None
    assert decode_latest_round_data("0x1234") is None  # too short


# ----- ChainlinkCsvGzAppender -----------------------------------------------

def _cl_row(ts_ms, symbol, updated_at=1779468050):
    return {
        "timestamp_ms": ts_ms,
        "symbol": symbol,
        "feed_address": CHAINLINK_FEEDS.get(symbol, "0xabc"),
        "round_id": 999,
        "answer_raw": 7683937507000,
        "price": 76839.37507,
        "started_at": updated_at - 50,
        "updated_at": updated_at,
        "answered_in_round": 999,
        "oracle_age_s": 31,
    }


def test_chainlink_writer_header_and_roundtrip(tmp_path):
    w = ChainlinkCsvGzAppender(tmp_path, fsync_every_n_rows=1000)
    ts_ms = 1779175200_000  # 2026-05-19 UTC
    w.append(_cl_row(ts_ms, "btc"))
    w.close()
    files = list(tmp_path.glob("*.csv.gz"))
    assert len(files) == 1
    assert files[0].name.startswith("btc_2026-05-19")
    rows = _read_gz_csv(files[0])
    assert len(rows) == 1
    rec = rows[0]
    assert list(rec.keys()) == CHAINLINK_COLUMNS
    assert rec["symbol"] == "btc"
    assert float(rec["price"]) == 76839.37507
    assert int(rec["updated_at"]) == 1779468050
    assert rec["feed_address"] == CHAINLINK_FEEDS["btc"]


def test_chainlink_writer_segment_flush_readable_mid_run(tmp_path):
    w = ChainlinkCsvGzAppender(tmp_path, fsync_every_n_rows=2)
    ts0 = 1779175200_000
    for i in range(5):
        w.append(_cl_row(ts0 + i * 1000, "eth", updated_at=1779468000 + i))
    files = list(tmp_path.glob("*.csv.gz"))
    assert len(files) == 1
    rows = _read_gz_csv(files[0])
    assert len(rows) >= 4
    w.close()
    rows_after = _read_gz_csv(files[0])
    assert len(rows_after) == 5


def test_chainlink_writer_splits_by_symbol_and_date(tmp_path):
    w = ChainlinkCsvGzAppender(tmp_path, fsync_every_n_rows=1000)
    day1 = 1779175200_000
    day2 = day1 + 86_400_000
    w.append(_cl_row(day1, "btc"))
    w.append(_cl_row(day1, "xrp"))
    w.append(_cl_row(day2, "btc"))
    w.close()
    names = sorted(p.name for p in tmp_path.glob("*.csv.gz"))
    assert len(names) == 3
    assert any(n.startswith("btc_2026-05-19") for n in names)
    assert any(n.startswith("btc_2026-05-20") for n in names)
    assert any(n.startswith("xrp_2026-05-19") for n in names)


def test_chainlink_feed_addresses_present():
    # All four symbols the bot trades must have a configured feed.
    for sym in ("btc", "eth", "sol", "xrp"):
        assert sym in CHAINLINK_FEEDS
        assert CHAINLINK_FEEDS[sym].startswith("0x")
        assert len(CHAINLINK_FEEDS[sym]) == 42


# ----- live RPC smoke test --------------------------------------------------

@pytest.mark.asyncio
async def test_chainlink_loop_live_smoke(tmp_path):
    """One real poll against the public Polygon RPC. Network-dependent; if the
    RPC is unreachable we skip rather than fail (matches 'real network is fine,
    just be quick' — but never block CI on a flaky public endpoint)."""
    w = ChainlinkCsvGzAppender(tmp_path, fsync_every_n_rows=1)
    stop = asyncio.Event()

    async def stopper():
        await asyncio.sleep(8.0)
        stop.set()

    task = asyncio.create_task(chainlink_loop(w, ["btc"], stop, poll_interval_sec=3.0))
    await stopper()
    try:
        await asyncio.wait_for(task, timeout=10.0)
    except asyncio.TimeoutError:
        task.cancel()
    w.close()

    files = list(tmp_path.glob("*.csv.gz"))
    if not files:
        pytest.skip("public Polygon RPC unreachable — skipping live smoke test")
    rows = _read_gz_csv(files[0])
    assert len(rows) >= 1
    rec = rows[0]
    # A real BTC/USD oracle read: price plausibly between $1k and $10M.
    assert 1_000 < float(rec["price"]) < 10_000_000
    assert int(rec["updated_at"]) > 1_700_000_000  # a sane recent epoch


# ----- read_asof + updated_at plumbing (settlement-print psettle features) -------
# cl_oracle_age_s = tick_s - updated_at of the round visible at the tick (research
# convention, oracle_print_model.tick_feature_frame). The cache now carries
# updated_at next to each poll; read_asof returns the SAME row price_asof does.

def _write_cl_gz_upd(base: Path, fname: str, rows):
    """live_chainlink CSV.gz writer with updated_at (rows = (ts_ms, sym, px, upd))."""
    d = base / "live_chainlink"
    d.mkdir(parents=True, exist_ok=True)
    with gzip.open(d / fname, "wt") as fh:
        fh.write(",".join(CHAINLINK_COLUMNS) + "\n")
        for ts, sym, px, upd in rows:
            rec = {c: "" for c in CHAINLINK_COLUMNS}
            rec["timestamp_ms"], rec["symbol"], rec["price"] = ts, sym, px
            rec["updated_at"] = "" if upd is None else upd
            fh.write(",".join(str(rec[c]) for c in CHAINLINK_COLUMNS) + "\n")


def test_read_asof_returns_price_and_updated_at():
    c = ChainlinkPriceCache()
    c.record("btc", 1_000_000, 100.0, updated_at=950)
    c.record("btc", 1_030_000, 101.0, updated_at=995)
    # asof between the two reads -> first row, with its round's updated_at
    assert c.read_asof("btc", 1_020_000) == (100.0, 950.0)
    assert c.read_asof("btc", 1_030_000) == (101.0, 995.0)
    # price_asof must return the SAME row's price (it delegates to read_asof)
    assert c.price_asof("btc", 1_020_000) == 100.0
    # before the first read / beyond tolerance -> None (unchanged semantics)
    assert c.read_asof("btc", 999_999) is None
    assert c.read_asof("btc", 1_030_000 + 120_001) is None
    # a record without updated_at -> (price, None): age is MISSING, not 0 (fail-closed)
    c.record("eth", 2_000_000, 5.0)
    assert c.read_asof("eth", 2_000_000) == (5.0, None)


def test_read_asof_out_of_order_insert_keeps_upd_aligned():
    c = ChainlinkPriceCache()
    c.record("btc", 1_000_000, 100.0, updated_at=900)
    c.record("btc", 1_060_000, 103.0, updated_at=1_050)
    c.record("btc", 1_030_000, 101.5, updated_at=1_000)   # late/out-of-order
    assert c.read_asof("btc", 1_030_000) == (101.5, 1_000.0)
    assert c.read_asof("btc", 1_059_000) == (101.5, 1_000.0)
    assert c.read_asof("btc", 1_060_000) == (103.0, 1_050.0)
    # eviction trims all three parallel arrays in lockstep
    c2 = ChainlinkPriceCache(max_keep_ms=50_000)
    c2.record("btc", 1_000_000, 100.0, updated_at=900)
    c2.record("btc", 1_100_000, 110.0, updated_at=1_090)
    assert c2.read_asof("btc", 1_000_000) is None          # evicted
    assert c2.read_asof("btc", 1_100_000) == (110.0, 1_090.0)


def test_warm_from_disk_loads_updated_at(tmp_path):
    now = 1_780_000_000_000
    date = _utc_date_str(now)
    _write_cl_gz_upd(tmp_path, f"btc_{date}.csv.gz",
                     [(now - 120_000, "btc", 100.0, now // 1000 - 130),
                      (now - 60_000, "btc", 101.0, now // 1000 - 70),
                      (now - 30_000, "btc", 102.0, None)])   # row missing updated_at
    c = ChainlinkPriceCache()
    assert c.warm_from_disk(tmp_path, now, symbols=["btc"]) == 3
    assert c.read_asof("btc", now - 60_000) == (101.0, float(now // 1000 - 70))
    # the asof row lacking updated_at surfaces None (caller fails closed on age)
    assert c.read_asof("btc", now) == (102.0, None)
    # price path is unchanged by the new column
    assert c.price_asof("btc", now) == 102.0
