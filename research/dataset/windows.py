"""Window-level canonical table: one row per market window."""
from __future__ import annotations
import pandas as pd


def _parse_slug(slug: str) -> tuple[str, str, int]:
    # <sym>-updown-<tf>-<window_start_ts>
    parts = slug.split("-")
    return parts[0], parts[2], int(parts[3])


def _safe_int(val) -> int | None:
    """Convert to int, returning None if NaN/None."""
    import math
    if val is None:
        return None
    try:
        f = float(val)
        return None if math.isnan(f) else int(f)
    except (TypeError, ValueError):
        return None


def build_window_row(slug: str, ticks: pd.DataFrame,
                     outcome: str | None, end_price: float | None) -> dict:
    """Summarize one window's ticks into a single canonical row.

    Handles malformed/partial windows gracefully (NaN in integer columns
    becomes None, which Parquet stores as a nullable integer).
    """
    sym, tf, wstart = _parse_slug(slug)
    return {
        "slug": slug,
        "symbol": sym,
        "timeframe": tf,
        "window_start_ts": wstart,
        "window_end_ts": _safe_int(ticks["window_end_ts"].iloc[0]),
        "strike": float(ticks["start_price"].iloc[0]),
        "n_ticks": int(len(ticks)),
        "first_sec": _safe_int(ticks["seconds_into_window"].min()),
        "last_sec": _safe_int(ticks["seconds_into_window"].max()),
        "outcome": outcome,
        "outcome_up": (1 if outcome == "Up" else 0 if outcome == "Down" else None),
        "end_price": end_price,
        "min_yes_mid": float(ticks["yes_mid"].min()),
        "max_yes_mid": float(ticks["yes_mid"].max()),
        "min_no_mid": float(ticks["no_mid"].min()),
        "max_no_mid": float(ticks["no_mid"].max()),
        "max_abs_move_pct": float(ticks["move_pct"].abs().max()),
        "median_yes_ask_depth": float(ticks["yes_ask_depth"].median()),
        "median_no_ask_depth": float(ticks["no_ask_depth"].median()),
    }


def build_windows_table(symbol: str, timeframe: str, date_start: str,
                        date_end: str) -> pd.DataFrame:
    """Build the window table for one (symbol, timeframe) over a date range."""
    from research.data.loader import iter_windows, load_outcomes
    outcomes = load_outcomes()
    rows = []
    for slug, ticks in iter_windows(symbol, timeframe, date_start, date_end):
        if slug in outcomes.index:
            oc = outcomes.loc[slug]
            outcome, end_price = str(oc["outcome"]), float(oc["end_price"])
        else:
            outcome, end_price = None, None
        rows.append(build_window_row(slug, ticks, outcome, end_price))
    return pd.DataFrame(rows)
