"""Tests for the 23-column tick CSV.gz writer.

The load-bearing test here is `test_writer_survives_restart_without_corruption`:
the bot is killed/restarted regularly (manual restarts, SIGKILL fallback in
`stop_all.sh`, daily-rotation), and historically the tick CSV was unreadable
past the first stop because gzip members were not closed on flush — only a
deflate sync-flush marker was written, and the next process's `gzip.open("ab")`
appended a new gzip header right after the unterminated previous member.

The fix mirrors what `l2_writer` and `macro_writer` already do: close + reopen
the gzip file every fsync segment so each segment is a complete gzip member.
"""
from __future__ import annotations
import csv
import gzip
import io
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from mean_reversion_live.collectors.tick_writer import (  # noqa: E402
    TICK_COLUMNS,
    CrashSafeCsvGzAppender,
)


def _row(ts_ms: int, symbol: str, slug: str | None = None, **overrides) -> dict:
    """Minimal row populating every TICK_COLUMNS key."""
    base = {c: "" for c in TICK_COLUMNS}
    base.update(
        timestamp_ms=ts_ms,
        symbol=symbol,
        market_slug=slug or f"{symbol}-updown-15m-{ts_ms // 1000 - (ts_ms // 1000) % 900}",
        window_start_ts=ts_ms // 1000 - (ts_ms // 1000) % 900,
        window_end_ts=(ts_ms // 1000 - (ts_ms // 1000) % 900) + 900,
        seconds_into_window=(ts_ms // 1000) % 900,
        yes_best_bid=0.30,
        yes_best_ask=0.31,
        yes_bid_depth=100.0,
        yes_ask_depth=200.0,
        no_best_bid=0.69,
        no_best_ask=0.70,
        no_bid_depth=150.0,
        no_ask_depth=250.0,
        chainlink_price=0.0,
        coinbase_price=50000.0,
        start_price=50000.0,
        move_pct=0.0,
        yes_mid=0.305,
        no_mid=0.695,
        spread_yes=0.01,
        spread_no=0.01,
        total_mid=1.0,
    )
    base.update(overrides)
    return base


def _read_gz_csv(path: Path) -> list:
    """Read every row. Must NOT raise on a valid file (no swallowing)."""
    with gzip.open(path, "rb") as f:
        text = f.read().decode("utf-8")
    return list(csv.DictReader(io.StringIO(text)))


# ----- Basic round-trip ----------------------------------------------------

def test_writer_header_and_roundtrip(tmp_path):
    w = CrashSafeCsvGzAppender(tmp_path, fsync_every_n_rows=1000)
    w.append(_row(1779175200_000, "btc"))
    w.close()

    files = list(tmp_path.glob("*.csv.gz"))
    assert len(files) == 1
    rows = _read_gz_csv(files[0])
    assert len(rows) == 1
    assert list(rows[0].keys()) == TICK_COLUMNS
    assert rows[0]["symbol"] == "btc"
    assert float(rows[0]["yes_best_bid"]) == 0.30


def test_writer_splits_by_symbol_and_date(tmp_path):
    w = CrashSafeCsvGzAppender(tmp_path, fsync_every_n_rows=1000)
    day1 = 1779175200_000          # 2026-05-19 UTC
    day2 = day1 + 86_400_000       # 2026-05-20 UTC
    w.append(_row(day1, "btc"))
    w.append(_row(day1, "eth"))
    w.append(_row(day2, "btc"))
    w.close()
    names = sorted(p.name for p in tmp_path.glob("*.csv.gz"))
    assert len(names) == 3
    assert any(n.startswith("btc_2026-05-19") for n in names)
    assert any(n.startswith("btc_2026-05-20") for n in names)
    assert any(n.startswith("eth_2026-05-19") for n in names)


# ----- The load-bearing test: restart survival -----------------------------

def test_writer_survives_restart_without_corruption(tmp_path):
    """Simulate the real-world bot lifecycle: write → close (graceful stop) →
    write again (restart). The file MUST be fully readable end-to-end after the
    "restart", not corrupted at the member boundary.

    Before the fix, restart appended a new gzip member after the previous
    member's sync-flush marker (no proper trailer), producing a malformed
    concatenated gzip file that `gzip.open` errors on with "invalid block type"
    or "Compressed file ended before the end-of-stream marker".
    """
    ts0 = 1779175200_000

    # === First "bot run" — write 100 rows then close gracefully.
    w1 = CrashSafeCsvGzAppender(tmp_path, fsync_every_n_rows=10)
    for i in range(100):
        w1.append(_row(ts0 + i * 1000, "btc"))
    w1.close()

    files = list(tmp_path.glob("*.csv.gz"))
    assert len(files) == 1
    target = files[0]

    # === Second "bot run" — same instance, fresh appender, appends to same file.
    w2 = CrashSafeCsvGzAppender(tmp_path, fsync_every_n_rows=10)
    for i in range(100, 200):
        w2.append(_row(ts0 + i * 1000, "btc"))
    w2.close()

    # === Third "bot run" — one more restart for good measure.
    w3 = CrashSafeCsvGzAppender(tmp_path, fsync_every_n_rows=10)
    for i in range(200, 300):
        w3.append(_row(ts0 + i * 1000, "btc"))
    w3.close()

    # === Verification: read all 300 rows back. This MUST NOT raise.
    rows = _read_gz_csv(target)
    assert len(rows) == 300, f"expected 300 rows across 3 'restarts', got {len(rows)}"
    # Spot-check ts ordering preserved.
    assert int(rows[0]["timestamp_ms"]) == ts0
    assert int(rows[-1]["timestamp_ms"]) == ts0 + 299 * 1000


def test_writer_segment_flush_readable_mid_run(tmp_path):
    """With a small fsync interval, the file is closed+reopened per segment and
    must be readable while the writer is still open (mid-run). Mirrors the
    L2/macro writers' behavior and matches the live-data research workflow."""
    w = CrashSafeCsvGzAppender(tmp_path, fsync_every_n_rows=2)
    ts0 = 1779175200_000
    for i in range(5):
        w.append(_row(ts0 + i * 1000, "btc"))
    # Do NOT close.
    files = list(tmp_path.glob("*.csv.gz"))
    assert len(files) == 1
    rows_mid = _read_gz_csv(files[0])
    # 4 rows committed in 2 closed segments; the 5th may be in the open member.
    assert len(rows_mid) >= 4, f"mid-run read got {len(rows_mid)} rows"
    w.close()
    rows_after = _read_gz_csv(files[0])
    assert len(rows_after) == 5


def test_writer_simulated_sigkill_then_restart(tmp_path):
    """Simulate a SIGKILL (no close, no GC finalize) followed by a clean restart.

    This is the production failure mode: stop_all.sh sends SIGTERM, the process
    tries to exit cleanly but the freeze cycle stalls shutdown past 30s, then
    SIGKILL fires. Python finalizers do NOT run on SIGKILL — the open gzip
    member is left without its trailer. The next bot process's gzip.open("ab")
    appends a new gzip header right after the open deflate stream, producing
    a malformed concatenated gzip file.

    With the fix, every fsync segment is a complete gzip member (closed before
    the next batch starts). The unterminated tail is at most the last
    `fsync_every_n_rows` rows; everything before that is recoverable.

    Test technique: detach the gzip file from its underlying file object so
    Python's GzipFile.__del__ has nothing to close — mimics SIGKILL semantics
    where finalizers never run.
    """
    import gzip as _gz
    ts0 = 1779175200_000

    # === First "run" with small fsync interval (5 rows). Write 23 rows.
    # With the fix: 20 rows committed in 4 closed members; 3 in open member.
    # Without the fix: all 23 rows in ONE open member, no trailer.
    w1 = CrashSafeCsvGzAppender(tmp_path, fsync_every_n_rows=5)
    for i in range(23):
        w1.append(_row(ts0 + i * 1000, "btc"))

    # SIMULATE SIGKILL: zero out the open file handle so Python's finalizer
    # cannot write a trailer. This is the appender's open gzip member at
    # crash time.
    for key, f in list(w1._files.items()):
        if isinstance(f, _gz.GzipFile):
            # Replace with a closed dummy so finalizer has nothing to flush.
            try:
                f.fileobj = None  # type: ignore
            except Exception:
                pass
    w1._files.clear()  # drop references; no graceful close
    del w1

    files = list(tmp_path.glob("*.csv.gz"))
    assert len(files) == 1
    target = files[0]

    # === Second "run" — fresh appender, appends more rows.
    w2 = CrashSafeCsvGzAppender(tmp_path, fsync_every_n_rows=5)
    for i in range(23, 40):
        w2.append(_row(ts0 + i * 1000, "btc"))
    w2.close()

    # === Read the whole file. With the fix, the 20 pre-SIGKILL committed rows
    # form valid gzip members; the 3 in-flight rows are lost (acceptable); the
    # 17 post-restart rows form more valid gzip members. Total ≥ 37 readable.
    # CRITICAL: the file must be readable AT ALL. Before the fix, the cross-
    # member boundary errored with "invalid block type" / EOFError.
    rows = _read_gz_csv(target)
    assert len(rows) >= 37, f"expected ≥37 rows across SIGKILL+restart, got {len(rows)}"
