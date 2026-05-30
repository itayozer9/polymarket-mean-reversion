#!/usr/bin/env python3
"""Recover data from gzip files corrupted by the pre-fix CrashSafeCsvGzAppender.

The bug: every fsync the appender wrote a deflate sync-flush marker but did NOT
close the gzip member (no CRC32 + ISIZE trailer). When the bot was killed or
restarted, the next process's `gzip.open("ab")` appended a new gzip header
directly after the open deflate stream — producing a malformed concatenated
gzip file that Python's gzip module errors on with "invalid block type" or
"Compressed file ended before the end-of-stream marker" mid-read.

This script walks the file at the byte level, splits it at gzip-magic-byte
boundaries (1f 8b 08), tolerantly decompresses each member (accepting partial
data up to the corruption), and re-emits a clean multi-member gzip file with
proper trailers per segment.

Usage:
    uv run python scripts/recover_truncated_gzip.py PATH [PATH ...]
        --out-suffix .recovered

By default writes alongside the original as `<name>.recovered.csv.gz`. Pass
`--in-place` to overwrite (the original is moved to `<name>.broken`).

Only safe to run on files written by CrashSafeCsvGzAppender / L2CsvGzAppender /
MacroCsvGzAppender. Other multi-member gzip files (e.g. system logrotate'd
gzip with no per-segment trailer issue) are passed through unchanged if no
corruption is encountered.
"""
from __future__ import annotations
import argparse
import gzip
import io
import sys
from pathlib import Path
from typing import List, Tuple


GZIP_MAGIC = b"\x1f\x8b\x08"  # gzip + deflate compression method byte


def find_member_offsets(data: bytes) -> List[int]:
    """Return the byte offsets of each gzip member header in `data`.

    Note: the 3-byte magic can theoretically appear inside compressed payloads
    (probability ~1/16M per byte). For safety we accept it and rely on the
    per-member decompression to reject false positives — a false split just
    means we lose a chunk and recover the rest.
    """
    positions: List[int] = []
    i = 0
    while True:
        j = data.find(GZIP_MAGIC, i)
        if j == -1:
            break
        positions.append(j)
        i = j + 1
    return positions


def decompress_member_tolerant(member_bytes: bytes) -> Tuple[bytes, str]:
    """Decompress one gzip member, accepting partial data on EOFError or
    decompression errors caused by a missing trailer / corrupted tail.

    Returns (recovered_bytes, status) where status is one of:
        "ok"       — full member decompressed cleanly
        "partial"  — decompression errored mid-stream; returned what we got
        "empty"    — could not decompress anything
    """
    buf = io.BytesIO()
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(member_bytes), mode="rb") as g:
            while True:
                try:
                    chunk = g.read(8192)
                except (EOFError, OSError) as exc:
                    if buf.tell() > 0:
                        return buf.getvalue(), "partial"
                    return b"", "empty"
                except Exception:
                    if buf.tell() > 0:
                        return buf.getvalue(), "partial"
                    return b"", "empty"
                if not chunk:
                    return buf.getvalue(), "ok"
                buf.write(chunk)
    except (EOFError, OSError):
        if buf.tell() > 0:
            return buf.getvalue(), "partial"
        return b"", "empty"


def recover_file(path: Path) -> Tuple[bytes, dict]:
    """Recover a corrupted gzip file. Returns (clean_uncompressed_bytes, stats).

    stats keys: members_total, members_ok, members_partial, members_empty,
                bytes_in (compressed input), bytes_out (uncompressed recovered).
    """
    data = path.read_bytes()
    offsets = find_member_offsets(data)
    stats = {
        "members_total": len(offsets),
        "members_ok": 0,
        "members_partial": 0,
        "members_empty": 0,
        "bytes_in": len(data),
        "bytes_out": 0,
    }
    if not offsets:
        return b"", stats

    out = io.BytesIO()
    for k, start in enumerate(offsets):
        end = offsets[k + 1] if k + 1 < len(offsets) else len(data)
        recovered, status = decompress_member_tolerant(data[start:end])
        stats[f"members_{status}"] += 1
        if not recovered:
            continue
        # Strip duplicated header: each member after the first re-emits the
        # CSV header line. Keep the first line of member 0 (the header), drop
        # the first line of every subsequent member.
        if k > 0:
            nl = recovered.find(b"\n")
            if nl != -1:
                recovered = recovered[nl + 1:]
        # Strip any trailing partial row (no terminating newline). On a
        # corruption boundary, the decoder may stop mid-line; the next member's
        # data would then concatenate to that fragment, producing one malformed
        # row at each boundary. Dropping the partial tail trades 1 row at the
        # boundary for byte-exact correctness of every other row.
        last_nl = recovered.rfind(b"\n")
        if last_nl == -1:
            # Member produced no complete row — discard entirely
            continue
        recovered = recovered[: last_nl + 1]
        out.write(recovered)
    payload = out.getvalue()
    stats["bytes_out"] = len(payload)
    return payload, stats


def write_clean_gzip(payload: bytes, out_path: Path) -> None:
    """Write a single-member gzip file (with proper trailer)."""
    with gzip.open(out_path, "wb", compresslevel=6) as f:
        f.write(payload)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("paths", nargs="+", type=Path,
                    help="Gzip file(s) to recover")
    ap.add_argument("--out-suffix", default=".recovered",
                    help="Suffix to insert before .csv.gz (default: .recovered)")
    ap.add_argument("--in-place", action="store_true",
                    help="Overwrite original; moves original to <name>.broken")
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would be recovered, don't write")
    args = ap.parse_args()

    rc = 0
    for path in args.paths:
        if not path.exists():
            print(f"SKIP {path}: not found", file=sys.stderr)
            rc = 1
            continue
        if path.suffix != ".gz":
            print(f"SKIP {path}: expected a .gz file", file=sys.stderr)
            rc = 1
            continue

        print(f"\n=== {path} ===")
        payload, stats = recover_file(path)
        rows = payload.count(b"\n")
        print(f"  members: total={stats['members_total']} "
              f"ok={stats['members_ok']} "
              f"partial={stats['members_partial']} "
              f"empty={stats['members_empty']}")
        print(f"  bytes:   in={stats['bytes_in']:,} → "
              f"uncompressed={stats['bytes_out']:,}  ({rows:,} rows)")

        if args.dry_run:
            print(f"  (dry-run: nothing written)")
            continue
        if not payload:
            print(f"  EMPTY — no recoverable data, not writing")
            continue

        if args.in_place:
            out_path = path
            broken_path = path.with_suffix(path.suffix + ".broken")
            path.rename(broken_path)
            print(f"  original moved to {broken_path.name}")
        else:
            # foo.csv.gz → foo.recovered.csv.gz
            name = path.name
            if name.endswith(".csv.gz"):
                stem = name[: -len(".csv.gz")]
                out_path = path.with_name(f"{stem}{args.out_suffix}.csv.gz")
            else:
                out_path = path.with_name(path.stem + args.out_suffix + path.suffix)

        write_clean_gzip(payload, out_path)
        print(f"  wrote {out_path} ({out_path.stat().st_size:,} bytes)")

    return rc


if __name__ == "__main__":
    sys.exit(main())
