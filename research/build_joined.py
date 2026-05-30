"""Entrypoint — build the joined, fully-instrumented per-tick dataset.

15m is the primary target (the edge hunt starts there); 5m is the control.
Run:  uv run python -m research.build_joined          # 15m only (fast)
      uv run python -m research.build_joined --with-5m # add the 5m control
"""
from __future__ import annotations
import sys
from research.dataset.joined import build

if __name__ == "__main__":
    tfs = ("15m", "5m") if "--with-5m" in sys.argv else ("15m",)
    build(timeframes=tfs)
