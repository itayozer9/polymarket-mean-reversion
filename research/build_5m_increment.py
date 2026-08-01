"""Incremental 5m part builder — enrich ONLY the missing date range per symbol and
merge with the existing part (the full-range re-enrich gets externally killed; see
test_ledger 'monolithic frame rebuild SIGKILLed'). One symbol per invocation so each
process stays small and a kill loses <5 min.

Run:  uv run python -m research.build_5m_increment <sym>      # one part
      uv run python -m research.build_5m_increment finish     # concat parts -> joined_5m
"""
from __future__ import annotations
import os
import sys

import pandas as pd

import research.dataset.joined as J
from research.clean_window import available_clean_dates

PARTS = os.path.join(J.OUTPUT_DIR, "parts")
INC_START = "2026-07-03"        # old parts are complete before this date
SYMS = ("btc", "eth", "sol", "xrp")


def build_sym(sym: str) -> None:
    part = os.path.join(PARTS, f"joined_5m_{sym}.parquet")
    dates = available_clean_dates("btc")
    date_end = dates[-1]
    old = pd.read_parquet(part)
    if str(old["date"].max()) >= date_end:
        print(f"[5m/{sym}] fresh through {old['date'].max()} — skip", flush=True)
        return
    old = old[old["date"].astype(str) < INC_START]
    print(f"[5m/{sym}] increment {INC_START}..{date_end} (old rows {len(old):,})", flush=True)
    inc = J._enrich_symbol(sym, "5m", INC_START, date_end, J.load_outcomes())
    out = pd.concat([old, inc], ignore_index=True)
    out.to_parquet(part, index=False)
    print(f"[5m/{sym}] wrote {len(out):,} rows (+{len(inc):,} new)", flush=True)


def finish() -> None:
    frames = [pd.read_parquet(os.path.join(PARTS, f"joined_5m_{s}.parquet")) for s in SYMS]
    out = pd.concat(frames, ignore_index=True)
    path = os.path.join(J.OUTPUT_DIR, "joined_5m.parquet")
    out.to_parquet(path, index=False)
    print(f"-> {path} ({len(out):,} rows, {os.path.getsize(path)/1e6:.1f} MB)", flush=True)


if __name__ == "__main__":
    arg = sys.argv[1]
    finish() if arg == "finish" else build_sym(arg)
