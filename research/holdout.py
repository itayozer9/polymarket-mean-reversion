"""Sealed hold-out boundary. Phase 2+ analyses MUST exclude these dates from
all fitting and selection. Opened exactly once, at the end of Phase 6.

March is quarantined (Task 3b) — only May data is admissible. With ~8 clean
days the hold-out is necessarily small; it is re-sealed larger on every weekly
re-run as the bot collects more data.

Recommended split (confirm with user at the Phase 0 checkpoint):
- DEVELOPMENT: 2026-05-15 .. 2026-05-20
- SEALED HOLD-OUT: 2026-05-21 .. 2026-05-22
"""
DEV_START = "2026-05-15"
DEV_END = "2026-05-20"
HOLDOUT_START = "2026-05-21"
HOLDOUT_END = "2026-05-22"


def is_holdout(date_str: str) -> bool:
    return HOLDOUT_START <= date_str <= HOLDOUT_END
