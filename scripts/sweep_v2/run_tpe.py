"""Stage 4 — Bayesian TPE refinement via Optuna.

Persistent study at `data/sweep_v2/meta/optuna.db` — trials accumulate across
iterations. Warm-started from lifetime top 5% via enqueue_trial.
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import optuna

from scripts.sweep_v2 import _runner, _optuna_bridge, evaluate, param_space

ROOT = Path(__file__).resolve().parent.parent.parent
SWEEP_DIR = ROOT / "data" / "sweep_v2"
META_DIR = SWEEP_DIR / "meta"


def make_objective(pool_args, seed=42, _executor=None):
    """Returns an Optuna objective closure that runs eval_kfold in a worker."""
    def objective(trial):
        cfg = _optuna_bridge.trial_to_config(trial)
        # We can't easily ship this to a worker pool with one-trial-at-a-time
        # Optuna semantics, so we run inline in this process. For 30k trials we
        # rely on Optuna's `n_jobs` to parallelize.
        from scripts.sweep_v2.evaluate import _WORKER_CTX
        if _WORKER_CTX is None:
            evaluate.worker_init(
                pool_args["symbols"],
                pool_args["date_start"],
                pool_args["date_end"],
                pool_args["fold_mask_dict"],
                pool_args["feature_cache_path"],
            )
        res = evaluate.worker_eval_kfold(cfg, seed=seed)
        trial.set_user_attr("config", cfg)
        trial.set_user_attr("result", res)
        # Constraint: require min_fold_n_trades >= 30
        if res.get("min_fold_n_trades", 0) < 30:
            return -1e9
        return float(res.get("cross_fold_sharpe", 0.0))
    return objective


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-trials", type=int, default=30000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--symbols", default="btc,eth,sol,xrp")
    parser.add_argument("--date-start", default="2026-05-15")
    parser.add_argument("--date-end", default="2026-05-23")
    parser.add_argument("--out", default=str(SWEEP_DIR / "stage4_tpe.jsonl"))
    parser.add_argument("--storage", default=str(META_DIR / "optuna_tpe.db"))
    parser.add_argument(
        "--warmstart-from",
        default=str(SWEEP_DIR / "stage3_lhs.jsonl"),
        help="JSONL of prior LHS results to seed TPE with top-quintile configs.",
    )
    parser.add_argument("--n-jobs", type=int, default=1)
    args = parser.parse_args()

    META_DIR.mkdir(parents=True, exist_ok=True)
    storage_url = f"sqlite:///{args.storage}"
    sampler = optuna.samplers.TPESampler(seed=args.seed, multivariate=True, group=True)
    pruner = optuna.pruners.MedianPruner(n_startup_trials=50, n_warmup_steps=0)
    study = optuna.create_study(
        study_name="sweep_v2_tpe",
        storage=storage_url,
        sampler=sampler,
        pruner=pruner,
        direction="maximize",
        load_if_exists=True,
    )

    # Warm-start from prior LHS top-quintile + lifetime store
    warm_path = Path(args.warmstart_from)
    if warm_path.exists():
        rows = _runner.read_jsonl(warm_path)
        eligible = [r for r in rows if r["result"].get("min_fold_n_trades", 0) >= 30]
        eligible.sort(key=lambda r: r["result"].get("cross_fold_sharpe", -1e9), reverse=True)
        top = eligible[: max(1, len(eligible) // 5)]
        for r in top[:200]:  # cap enqueued count
            try:
                study.enqueue_trial(_optuna_bridge.config_to_trial_dict(r["config"]))
            except Exception:
                pass
        print(f"  Stage 4: warm-started TPE with {min(len(top), 200)} configs from LHS top-quintile.")

    for r in _runner.lifetime_top_configs(META_DIR, top_pct=0.05)[:100]:
        try:
            study.enqueue_trial(_optuna_bridge.config_to_trial_dict(r))
        except Exception:
            pass

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    pool_args = _runner.default_pool_args(symbols, args.date_start, args.date_end)

    objective = make_objective(pool_args, seed=args.seed)
    study.optimize(objective, n_trials=args.n_trials, n_jobs=args.n_jobs, show_progress_bar=False)

    # Dump results as JSONL in our standard format.
    rows = []
    for t in study.trials:
        if t.state != optuna.trial.TrialState.COMPLETE:
            continue
        cfg = t.user_attrs.get("config")
        res = t.user_attrs.get("result")
        if cfg is None or res is None:
            continue
        rows.append({
            "config_id": param_space.hash_id(cfg),
            "config": cfg,
            "result": res,
            "trial_number": t.number,
        })
    _runner.write_jsonl(rows, Path(args.out))
    print(f"  Stage 4: wrote {len(rows)} TPE rows → {args.out}")
    if study.best_trial:
        print(f"  Stage 4: best trial #{study.best_trial.number} sharpe={study.best_value:.3f}")


if __name__ == "__main__":
    main()
