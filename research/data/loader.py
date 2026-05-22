"""Load Polymarket tick CSVs + outcomes from this repo's data layout.

Unlike polymarket-arb's loaders.py (which hardcodes data_v2/), this knows about
data/historical/ and data/live/ and keeps ALL 23 columns including spot prices.
"""
from __future__ import annotations
import glob
import io
import os
import re
import subprocess
from datetime import datetime, date
from typing import Iterator, Optional

import pandas as pd

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HISTORICAL_DIR = os.path.join(REPO_ROOT, "data", "historical")
LIVE_DIR = os.path.join(REPO_ROOT, "data", "live")
OUTCOMES_FILE = os.path.join(REPO_ROOT, "data", "outcomes.csv")

QUARANTINE_BEFORE = "2026-05-15"  # Task 3b: March tick data has a corrupt order book — unusable.

ALL_TICK_COLS = [
    "timestamp_ms", "market_slug", "symbol", "window_start_ts", "window_end_ts",
    "seconds_into_window", "yes_best_bid", "yes_best_ask", "yes_bid_depth",
    "yes_ask_depth", "no_best_bid", "no_best_ask", "no_bid_depth", "no_ask_depth",
    "chainlink_price", "coinbase_price", "start_price", "move_pct", "yes_mid",
    "no_mid", "spread_yes", "spread_no", "total_mid",
]


def _read_csv_gz_tolerant(path: str) -> pd.DataFrame:
    """Read a .csv.gz even if the gzip trailer is corrupt (truncated EOD write)."""
    try:
        return pd.read_csv(path)
    except Exception:
        proc = subprocess.Popen(["gunzip", "-c", path], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL)
        data, _ = proc.communicate()
        return pd.read_csv(io.BytesIO(data), on_bad_lines="skip")


def load_tick_csv(path) -> pd.DataFrame:
    """Load one tick CSV (.csv or .csv.gz), sorted within each window."""
    path = str(path)
    if path.endswith(".gz"):
        df = _read_csv_gz_tolerant(path)
    else:
        df = pd.read_csv(path)
    df = df.sort_values(["window_start_ts", "seconds_into_window"], kind="mergesort")
    return df.reset_index(drop=True)


def _file_date(path: str) -> Optional[date]:
    m = re.search(r"_(\d{4}-\d{2}-\d{2})(?:_raw)?\.csv\.gz$", path)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d").date()
    except ValueError:
        return None


def list_tick_files(symbol: str, date_start: str, date_end: str,
                    include_quarantined: bool = False) -> list[str]:
    """All tick files for symbol in [date_start, date_end], historical + live,
    chronologically ordered. Skips *_raw.csv.gz duplicates.

    By default, excludes files dated before QUARANTINE_BEFORE (Task 3b: March
    tick data has a corrupt order book). Pass include_quarantined=True for audit
    tasks that legitimately need to scan all data.
    """
    d0 = datetime.strptime(date_start, "%Y-%m-%d").date()
    d1 = datetime.strptime(date_end, "%Y-%m-%d").date()
    quarantine_cutoff = datetime.strptime(QUARANTINE_BEFORE, "%Y-%m-%d").date()
    found: dict[date, str] = {}
    for d in (HISTORICAL_DIR, LIVE_DIR):
        for p in glob.glob(os.path.join(d, f"{symbol}_*.csv.gz")):
            if p.endswith("_raw.csv.gz"):
                continue
            fd = _file_date(p)
            if fd is not None and d0 <= fd <= d1:
                if not include_quarantined and fd < quarantine_cutoff:
                    continue
                found[fd] = p  # live overrides historical if both exist
    return [found[k] for k in sorted(found)]


def iter_windows(symbol: str, timeframe: str, date_start: str, date_end: str,
                 include_quarantined: bool = False) -> Iterator[tuple[str, pd.DataFrame]]:
    """Yield (slug, ticks_df) per market window, chronologically."""
    prefix = f"{symbol}-updown-{timeframe}-"
    for f in list_tick_files(symbol, date_start, date_end,
                             include_quarantined=include_quarantined):
        df = load_tick_csv(f)
        df = df[df["market_slug"].astype(str).str.startswith(prefix)]
        for slug, g in df.groupby("market_slug", sort=True):
            yield str(slug), g.reset_index(drop=True)


def load_outcomes() -> pd.DataFrame:
    """Return the outcomes table indexed by market_slug."""
    df = pd.read_csv(OUTCOMES_FILE)
    return df.drop_duplicates("market_slug").set_index("market_slug")
