"""Shared parallel runner used by all 6 optimizer stages.

Spins up a ProcessPoolExecutor with a per-worker EvalContext (data loaded once
per worker, not once per task). Provides a generic `evaluate_configs()` that
streams configs through workers and yields results in submission order.
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional

from scripts.sweep_v2 import evaluate, folds, param_space


ROOT = Path(__file__).resolve().parent.parent.parent
SWEEP_DIR = ROOT / "data" / "sweep_v2"
FEATURES_PATH = SWEEP_DIR / "features.parquet"


def default_pool_args(
    symbols: List[str],
    date_start: str,
    date_end: str,
    use_features: bool = True,
) -> Dict[str, Any]:
    fold_data = folds.load_folds()
    return {
        "symbols": symbols,
        "date_start": date_start,
        "date_end": date_end,
        "fold_mask_dict": {
            "n_folds": fold_data["n_folds"],
            "slug_to_fold": fold_data["slug_to_fold"],
        },
        "feature_cache_path": str(FEATURES_PATH) if use_features else None,
    }


def _make_executor(pool_args: Dict[str, Any], max_workers: Optional[int]) -> ProcessPoolExecutor:
    if max_workers is None:
        max_workers = max(1, (os.cpu_count() or 4) - 1)
    return ProcessPoolExecutor(
        max_workers=max_workers,
        initializer=evaluate.worker_init,
        initargs=(
            pool_args["symbols"],
            pool_args["date_start"],
            pool_args["date_end"],
            pool_args["fold_mask_dict"],
            pool_args["feature_cache_path"],
        ),
        mp_context=mp.get_context("spawn"),
    )


def evaluate_configs(
    configs: Iterable[Dict[str, Any]],
    pool_args: Dict[str, Any],
    seed: int = 42,
    max_workers: Optional[int] = None,
    progress_every: int = 50,
    label: str = "eval",
) -> List[Dict[str, Any]]:
    """Evaluate a list of configs in parallel. Returns list of result rows
    (one per config) in submission order, where each row is:
        {"config": <flat dict>, "config_id": <hash>, "result": <eval_kfold output>}
    """
    cfg_list = list(configs)
    if not cfg_list:
        return []
    print(f"[{label}] evaluating {len(cfg_list):,} configs with parallel workers…")
    t0 = time.time()
    rows: List[Optional[Dict[str, Any]]] = [None] * len(cfg_list)
    with _make_executor(pool_args, max_workers) as ex:
        future_to_idx = {
            ex.submit(evaluate.worker_eval_kfold, cfg, seed): i
            for i, cfg in enumerate(cfg_list)
        }
        done = 0
        for fut in as_completed(future_to_idx):
            i = future_to_idx[fut]
            try:
                result = fut.result()
            except Exception as e:
                result = {"error": str(e)}
            rows[i] = {
                "config_id": param_space.hash_id(cfg_list[i]),
                "config": cfg_list[i],
                "result": result,
            }
            done += 1
            if done % progress_every == 0 or done == len(cfg_list):
                elapsed = time.time() - t0
                rate = done / elapsed if elapsed > 0 else 0
                eta = (len(cfg_list) - done) / rate if rate > 0 else 0
                print(
                    f"  [{label}] {done:,}/{len(cfg_list):,} "
                    f"({100*done/len(cfg_list):.1f}%) "
                    f"| {rate:.1f} cfg/s "
                    f"| elapsed={elapsed:.0f}s "
                    f"| eta={eta:.0f}s",
                    flush=True,
                )
    return [r for r in rows if r is not None]


def write_jsonl(rows: List[Dict[str, Any]], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r, default=str) + "\n")
    return path


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def viable_priors_from_meta(meta_dir: Path) -> Optional[Dict[str, Dict[str, Any]]]:
    """Return per-param viable-region priors {param: {p5, p95}} or None."""
    path = meta_dir / "viable_region_priors.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def lifetime_top_configs(meta_dir: Path, top_pct: float = 0.05) -> List[Dict[str, Any]]:
    """Return top configs from the lifetime evaluation store, by cross-fold Sharpe."""
    import pandas as pd

    path = meta_dir / "all_evals_lifetime.parquet"
    if not path.exists():
        return []
    df = pd.read_parquet(path)
    if "cross_fold_sharpe" not in df.columns:
        return []
    df = df[df["min_fold_n_trades"] >= 30].sort_values("cross_fold_sharpe", ascending=False)
    n_top = max(1, int(len(df) * top_pct))
    top = df.head(n_top)
    configs = []
    for _, row in top.iterrows():
        cfg_dict = param_space.unflatten_from_dataframe(row.to_dict())
        configs.append(cfg_dict)
    return configs
