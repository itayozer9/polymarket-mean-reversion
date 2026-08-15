"""Materialize the Edge Hunt v4 campaign window into a SCRATCH frame.

The canonical `data/research/joined_15m.parquet` was last built 2026-07-24 10:06 and
therefore contains only the first ~10 hours of the v4 window (and only the 4 original
coins). `joined.build()` writes to the canonical path, so calling it with a date range
would REPLACE full history with a 3-week slice and break every other analysis that reads
it. Instead this writes the window to its own directory; nothing canonical is touched.

  PYTHONPATH=. nice -n 19 uv run python -m research.dataset.build_v4_window
"""
from __future__ import annotations
import os

import research.dataset.joined as J

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "..", "data", "research", "v4_frame")
OUT = os.path.normpath(OUT)
WIN_LO, WIN_HI = "2026-07-24", "2026-08-14"
COINS = ("btc", "eth", "sol", "xrp", "bnb", "doge", "hype")   # prereg: all 7


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    J.OUTPUT_DIR = OUT                      # redirect the writer, canonical untouched
    J.build(timeframes=("15m",), symbols=COINS,
            date_start=WIN_LO, date_end=WIN_HI)
    print(f"-> {OUT}/joined_15m.parquet")


if __name__ == "__main__":
    main()
