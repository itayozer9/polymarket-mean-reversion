"""One-off: backfill det_lwd_v2 / det_sqp_v2 monitoring history from the moment
the v1 strategies went live, so v1 vs v2 are directly comparable.

FAITHFUL SPOT BASIS — critical: the live tick CSVs (data/live) store the STALE
~14s coinbase poll in `move_pct` (verified ~2.5 bps off, sign-wrong ~13%); the
live engine actually traded off the FRESH WS spot wired into SpotPriceCache. A
CSV replay reproduces the stale-spot LOSER (v1 unfiltered CSV-replay = 71% WR
vs 90% live). So we source the fresh spot from `joined_15m.parquet`
(`dist_strike_bps`, built from data/live_spot = the same fresh WS feed the engine
used) and set move_pct := dist_strike_bps/100. Book/fill fields are NOT stale, so
they come straight from the join. Outcomes use the authoritative `outcome_up_clean`.

Rows are fed through the REAL DeterminismState / StaleQuoteState via the v2
StrategyHandles, so the written trades.jsonl / trades_detailed.jsonl / portfolio
match the live format and are picked up on the next bot restart.

Validates by reproducing det_lwd_v1 (expect ~90% WR) WITHOUT writing to v1 files.

Run: uv run python scripts/backfill_v2_history.py
"""
from __future__ import annotations
import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import mean_reversion_live.adapters.arb_imports as ai  # noqa: E402 (injects arb path)
from mean_reversion_live.adapters.arb_imports import TICK_DTYPE, window_duration_sec  # noqa: E402
from mean_reversion_live.engine.registry import load_strategies  # noqa: E402

JOINED = REPO / "data" / "research" / "joined_15m.parquet"
WDUR = window_duration_sec("15m")

# joined col -> tick field (book/fill fields are NOT stale; spot handled separately)
_COLMAP = {
    "timestamp_ms": "timestamp_ms", "seconds_into_window": "seconds_into_window",
    "yes_best_bid": "yes_best_bid", "yes_best_ask": "yes_best_ask",
    "no_best_ask": "no_best_ask", "yes_ask_depth": "yes_ask_depth",
    "no_ask_depth": "no_ask_depth", "yes_mid": "yes_mid", "spread_yes": "spread_yes",
    "start_price": "start_price",
}


def _windows_from_joined(start_ms: int):
    """Build [(slug, TICK_DTYPE arr w/ FRESH move_pct, outcome_up, settle_ts)]."""
    df = pd.read_parquet(JOINED)
    df = df[df["date"].astype(str).isin(("2026-05-29", "2026-05-30"))
            & df["outcome_up_clean"].notna() & (df["start_price"] > 0)].copy()
    df = df[df["window_start_ts"].astype("int64") * 1000 >= start_ms]
    out = []
    for slug, g in df.groupby("slug", sort=True):
        g = g.sort_values("seconds_into_window")
        arr = np.zeros(len(g), dtype=TICK_DTYPE)
        for jcol, tfield in _COLMAP.items():
            if jcol in g.columns and tfield in arr.dtype.names:
                arr[tfield] = g[jcol].to_numpy()
        arr["move_pct"] = g["dist_strike_bps"].to_numpy() / 100.0   # FRESH spot
        if "no_mid" in arr.dtype.names:
            arr["no_mid"] = 1.0 - g["yes_mid"].to_numpy()
        out.append((str(slug), arr, bool(g["outcome_up_clean"].iloc[0] == 1),
                    int(g["timestamp_ms"].iloc[-1])))
    return out


def _v1_start_ms() -> int:
    best = None
    for sid in ("det_lwd_v1", "det_sqp_v1"):
        f = REPO / "data" / "jsonl" / sid / "trades.jsonl"
        if not f.exists():
            continue
        for line in f.read_text().splitlines():
            if line.strip():
                ts = json.loads(line).get("entry_ts_ms")
                if ts is not None:
                    best = ts if best is None else min(best, ts)
    return best or 0


def _replay(handle, windows, write: bool):
    handle.portfolio.__init__(human=handle.cfg.human, bankroll=handle.starting_capital_usd)
    handle.states.clear()
    settled = 0
    for slug, arr, outcome_up, settle_ts in windows:
        st = handle.get_or_create_state(slug, WDUR)
        for row in arr:
            st.on_tick(row, handle.portfolio, handle.rng)
        tr = st.settle(outcome_up, settle_ts, handle.portfolio)
        if tr is not None:
            settled += 1
            if write:
                handle.record_trade(tr)
                handle.record_trade_detailed(tr, getattr(st, "last_ctx", None) or {})
    return settled


def main():
    start_ms = _v1_start_ms()
    print(f"v1 live-start anchor: {dt.datetime.utcfromtimestamp(start_ms/1000)} UTC ({start_ms} ms)")
    windows = _windows_from_joined(start_ms)
    print(f"windows from joined (fresh spot, >= anchor): {len(windows)}")

    handles = {h.id: h for h in load_strategies(REPO / "strategies.yaml", REPO / "data")}

    # ── validation only (write=False -> v1 files untouched): expect ~90% ──
    v1 = handles["det_lwd_v1"]
    _replay(v1, windows, write=False)
    print(f"[validate] det_lwd_v1 fresh-replay: n={v1.portfolio.n_trades} "
          f"WR={v1.portfolio.win_rate*100:.1f}% PnL=${v1.portfolio.total_pnl:+.2f}  "
          f"(live actual ~90% / +$51.95)")

    # ── backfill the two v2 strategies for real ──
    for vid in ("det_lwd_v2", "det_sqp_v2"):
        for fn in ("trades.jsonl", "trades_detailed.jsonl"):
            p = REPO / "data" / "jsonl" / vid / fn
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.exists():
                p.unlink()
        h = handles[vid]
        h._setup_io()                      # reopen appenders on the now-empty files
        n = _replay(h, windows, write=True)
        h.persist_portfolio()
        p = h.portfolio
        print(f"  {vid:12} trades={p.n_trades:>3} WR={p.win_rate*100:4.1f}% "
              f"PnL=${p.total_pnl:+.2f}  (settled {n})")


if __name__ == "__main__":
    main()
