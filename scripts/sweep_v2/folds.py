"""Stage 1 — 5-fold stratified split over (symbol, window_start_ts).

Stratified by (symbol, UTC hour-bucket). Random shuffle. Persistent.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
SWEEP_DIR = ROOT / "data" / "sweep_v2"
FOLDS_PATH = SWEEP_DIR / "folds_v1.json"

HOUR_BUCKETS = {
    "OVERNIGHT": range(0, 7),   # 00:00–06:59 UTC
    "ASIA": range(7, 12),       # 07:00–11:59 UTC
    "EU": range(12, 17),        # 12:00–16:59 UTC
    "US": range(17, 24),        # 17:00–23:59 UTC
}


def utc_hour_to_bucket(hour: int) -> str:
    for name, rng in HOUR_BUCKETS.items():
        if hour in rng:
            return name
    raise ValueError(f"bad hour: {hour}")


def build_index(outcomes_path: Path, symbols: List[str], date_start: str, date_end: str) -> pd.DataFrame:
    """Return a DataFrame with one row per (symbol, slug) — only 15m markets in range."""
    df = pd.read_csv(outcomes_path)
    df = df[df["market_slug"].str.contains("-updown-15m-", na=False)]
    df = df[df["symbol"].isin(symbols)].copy()
    df["dt"] = pd.to_datetime(df["window_start_ts"], unit="s", utc=True)
    d0 = pd.Timestamp(date_start, tz="UTC")
    d1 = pd.Timestamp(date_end, tz="UTC") + pd.Timedelta(days=1)
    df = df[(df["dt"] >= d0) & (df["dt"] < d1)].copy()
    df["hour"] = df["dt"].dt.hour
    df["bucket"] = df["hour"].map(utc_hour_to_bucket)
    df["stratum"] = df["symbol"] + "|" + df["bucket"]
    df = (
        df[["market_slug", "symbol", "window_start_ts", "bucket", "stratum"]]
        .drop_duplicates("market_slug")
        .reset_index(drop=True)
    )
    return df


def stratified_kfold(
    index_df: pd.DataFrame, n_folds: int, seed: int
) -> Dict[str, int]:
    """Return {slug: fold_idx}. Stratified by `stratum` column with shuffle."""
    from sklearn.model_selection import StratifiedKFold

    skf = StratifiedKFold(n_splits=n_folds, shuffle=True, random_state=seed)
    slug_to_fold: Dict[str, int] = {}
    X = index_df["market_slug"].to_numpy()
    y = index_df["stratum"].to_numpy()
    for fold_idx, (_, test_indices) in enumerate(skf.split(X, y)):
        for i in test_indices:
            slug_to_fold[str(X[i])] = fold_idx
    return slug_to_fold


def write_folds(
    outcomes_path: Path,
    symbols: List[str],
    date_start: str,
    date_end: str,
    n_folds: int = 5,
    seed: int = 20260523,
    out_path: Path = FOLDS_PATH,
) -> Dict:
    index_df = build_index(outcomes_path, symbols, date_start, date_end)
    slug_to_fold = stratified_kfold(index_df, n_folds=n_folds, seed=seed)
    fold_sizes = defaultdict(int)
    for f in slug_to_fold.values():
        fold_sizes[f] += 1
    payload = {
        "n_folds": n_folds,
        "seed": seed,
        "date_start": date_start,
        "date_end": date_end,
        "symbols": symbols,
        "n_markets": len(slug_to_fold),
        "fold_sizes": dict(fold_sizes),
        "slug_to_fold": slug_to_fold,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2))
    return payload


def load_folds(path: Path = FOLDS_PATH) -> Dict:
    return json.loads(path.read_text())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default="btc,eth,sol,xrp")
    parser.add_argument("--date-start", default="2026-05-15")
    parser.add_argument("--date-end", default="2026-05-23")
    parser.add_argument("--n-folds", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20260523)
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    outcomes_path = ROOT / "data" / "outcomes.csv"
    payload = write_folds(
        outcomes_path,
        symbols=symbols,
        date_start=args.date_start,
        date_end=args.date_end,
        n_folds=args.n_folds,
        seed=args.seed,
    )
    print(f"Wrote {FOLDS_PATH}")
    print(f"  n_markets={payload['n_markets']}")
    print(f"  fold_sizes={payload['fold_sizes']}")


if __name__ == "__main__":
    main()
