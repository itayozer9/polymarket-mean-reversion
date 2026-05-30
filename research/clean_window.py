"""The clean, fully-instrumented analysis window + its dev/hold-out split.

Why a new module instead of reusing `research/holdout.py`:
  - `holdout.py` covers the OLD research window (May 15-22) on tick-only data,
    part of which had the strike bug (fixed 2026-05-22 15:55, commit d6ac2b5).
  - The NEW edge hunt uses only data that is BOTH (a) post-strike-fix and
    (b) fully instrumented with the new feeds (L2 / spot-WS / chainlink /
    trades), all of which start 2026-05-22.
  - => the clean window is 2026-05-23 .. now (the first full UTC day after the
    fix), growing daily as the bot keeps collecting.

Discipline (same as holdout.py): the split unit is a whole UTC day. The hold-out
is the most recent clean days; it is sealed and opened exactly once per candidate
in Phase 5. As the bot runs, push CLEAN_HOLDOUT_* forward and re-seal larger.
"""
from __future__ import annotations
import glob
import os
import re
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# First full UTC day with correct strikes AND all new feeds present.
CLEAN_START = "2026-05-23"

# Dev / sealed-hold-out boundary (whole UTC days). Bump forward as data grows.
CLEAN_DEV_START = "2026-05-23"
CLEAN_DEV_END = "2026-05-27"
CLEAN_HOLDOUT_START = "2026-05-28"
CLEAN_HOLDOUT_END = "2026-05-31"  # rolling; anything >= here is fresh OOS

# Feeds that must exist for a day to count as fully instrumented.
INSTRUMENTED_FEEDS = ("live", "live_l2", "live_spot", "live_trades")


def available_clean_dates(symbol: str = "btc") -> list[str]:
    """UTC dates (YYYY-MM-DD) >= CLEAN_START for which the tick file AND every
    instrumented feed exist for `symbol`. Excludes the current (partial) day's
    file only if you choose to; here we return all present files."""
    def dates_for(feed: str) -> set[str]:
        out = set()
        for p in glob.glob(os.path.join(REPO_ROOT, "data", feed, f"{symbol}_*.csv.gz")):
            m = re.search(r"_(\d{4}-\d{2}-\d{2})\.csv\.gz$", p)
            if m and m.group(1) >= CLEAN_START:
                out.add(m.group(1))
        return out

    common = None
    for feed in INSTRUMENTED_FEEDS:
        ds = dates_for(feed)
        common = ds if common is None else (common & ds)
    return sorted(common or set())


def split_of(date_str: str) -> str:
    """'dev' | 'holdout' | 'future' | 'pre' for a UTC date string."""
    if date_str < CLEAN_START:
        return "pre"
    if CLEAN_DEV_START <= date_str <= CLEAN_DEV_END:
        return "dev"
    if CLEAN_HOLDOUT_START <= date_str <= CLEAN_HOLDOUT_END:
        return "holdout"
    return "future"
