"""Run hypothesis_select with its mechanical gates AS-IS, re-pointed at the
live-cost campaign artifact dir (test_ledger.md "Live-cost campaign 2026-06-10").

hypothesis_select.py itself is deliberately NOT edited — the selection gates
(dev-only EV, FULL CI lower > 0, CPCV >= 80%, latency 5s/10s > 0, cap10 >= 0.90,
<=5/family, top 24) must stay byte-identical to the 2026-06-05 campaign. This
wrapper only redirects the input/output paths.

Run: uv run python -m research.analysis.livecost_select [dir]
"""
from __future__ import annotations
import os
import sys

import research.analysis.hypothesis_select as hs

DEFAULT_DIR = os.path.join("data", "research", "hypotheses", "livecost_2026-06-10")


def main() -> None:
    d = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DIR
    hs.OUT_DIR = d
    hs.SHORTLIST = os.path.join(d, "shortlist.jsonl")
    print(f"selection gates AS-IS on {d}")
    hs.main()


if __name__ == "__main__":
    main()
