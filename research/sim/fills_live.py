"""Live-calibrated fill model — the cost layer every future backtest is judged under.

Calibrated from OUR OWN live execution record (data/live/fills.jsonl + intents.jsonl
joined to the 10-level L2 stream at intent time). Captures the three measured live
phenomena the idealized v2 model misses:

  1. zero-fill hazard — displayed depth that isn't matchable (API-400 "no orders
     found to match", dry-in-band): P(zero fill | depth ratio, time left), binned;
  2. effective-depth haircut kappa — of the depth displayed in-band, the fraction
     we actually capture when we do fill;
  3. empirical latency — end-to-end intent->fill seconds, SAMPLED from the recorded
     distribution (not a fixed 2s);

plus the executor's deployed guard semantics:

  - guarded mode (default): the ladder is restricted to [entry_ask - GUARD_FLOOR_DROP,
    max_ask]; a best ask below the floor means the favourite flipped -> NO FILL
    (mirrors the EXEC_GUARDS floor abort);
  - legacy mode: floor 0.01 — reproduces knife-catch fills for counterfactual studies.

Returns the same `Fill` dataclass as research/sim/fills_v2.py so settle_pnl and all
downstream stats work unchanged.

Calibrate + persist:
    uv run python -m research.sim.fills_live --calibrate
    -> data/research/fill_model_live.json (with a validation block)
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from research.sim.fills_v2 import DEFAULT_FEES, FeeSchedule, Fill, walk_buy

REPO = Path(__file__).resolve().parents[2]
DEFAULT_PARAMS_PATH = REPO / "data" / "research" / "fill_model_live.json"

# Mirrors scripts/live_executor.py EXEC_FLOOR_DROP — the deployed guard band.
GUARD_FLOOR_DROP = 0.04

DEPTH_BINS = [(0.0, 0.5, "d<0.5"), (0.5, 1.0, "d0.5-1"), (1.0, 2.0, "d1-2"),
              (2.0, float("inf"), "d>=2")]
TL_BINS = [(0.0, 60.0, "tl<=60"), (60.0, 180.0, "tl61-180"),
           (180.0, float("inf"), "tl>180")]


def _depth_bin(ratio: float) -> str:
    for lo, hi, name in DEPTH_BINS:
        if lo <= ratio < hi:
            return name
    return DEPTH_BINS[-1][2]


def _tl_bin(tl: Optional[float]) -> str:
    if tl is None or not np.isfinite(tl):
        return "tl61-180"  # the dominant live bucket
    for lo, hi, name in TL_BINS:
        if lo <= tl < hi:
            return name
    return TL_BINS[-1][2]


@dataclass
class LiveFillParams:
    version: str
    calibration_window: Tuple[str, str]
    zero_fill_prob: Dict[str, Dict[str, Dict[str, float]]]  # [depth][tl] -> {p, n}
    kappa: float
    latency_samples_s: List[float]
    mean_slip_filled: float
    knife_rate_legacy: float
    n_attempts: int
    validation: dict = field(default_factory=dict)

    def zero_p(self, depth_ratio: float, time_left: Optional[float]) -> float:
        cell = self.zero_fill_prob.get(_depth_bin(depth_ratio), {}).get(_tl_bin(time_left))
        return float(cell["p"]) if cell else 0.0


def save_params(params: LiveFillParams, path: Path = DEFAULT_PARAMS_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    d = asdict(params)
    d["calibration_window"] = list(params.calibration_window)
    path.write_text(json.dumps(d, indent=2))


def load_params(path: Path = DEFAULT_PARAMS_PATH) -> LiveFillParams:
    d = json.loads(Path(path).read_text())
    d["calibration_window"] = tuple(d.get("calibration_window", ("", "")))
    return LiveFillParams(**d)


def sample_latency(rng: np.random.Generator, params: LiveFillParams) -> float:
    s = params.latency_samples_s
    if not s:
        return 2.0
    return float(s[int(rng.integers(0, len(s)))])


def simulate_taker_entry(ask_px: Sequence[float], ask_sz: Sequence[float],
                         stake_usd: float, *, entry_ask: float, max_ask: float,
                         floor: Optional[float] = None,
                         time_left: Optional[float] = None,
                         rng: Optional[np.random.Generator] = None,
                         params: Optional[LiveFillParams] = None,
                         fees: FeeSchedule = DEFAULT_FEES,
                         mode: str = "guarded") -> Fill:
    """Taker entry under the deployed executor semantics + live-calibrated frictions.

    guarded: band [entry_ask - GUARD_FLOOR_DROP, max_ask]; best ask below the floor
             -> no fill (the executor aborts: the favourite flipped).
    legacy:  band [0.01, max_ask] — reproduces pre-guard knife-catches.
    """
    if floor is None:
        floor = (entry_ask - GUARD_FLOOR_DROP) if mode == "guarded" else 0.01
    px = np.asarray(ask_px, dtype="f8")
    sz = np.asarray(ask_sz, dtype="f8")
    m = np.isfinite(px) & np.isfinite(sz) & (px > 0) & (sz > 0)
    px, sz = px[m], sz[m]
    no_fill = Fill(False, 0.0, 0.0, 0.0, 0.0, 0, float(stake_usd), 0.0)
    if px.size == 0 or stake_usd <= 0:
        return no_fill
    order = np.argsort(px)
    px, sz = px[order], sz[order]
    if mode == "guarded" and px[0] < floor - 1e-9:
        return no_fill                       # signal death — executor floor abort
    band = px <= max_ask + 1e-9
    px, sz = px[band], sz[band]
    if px.size == 0:
        return no_fill
    target_shares = stake_usd / max(entry_ask, 1e-9)
    depth_ratio = float(sz.sum()) / max(target_shares, 1e-9)
    if params is not None and rng is not None:
        p_zero = params.zero_p(depth_ratio, time_left)
        if p_zero > 0 and rng.random() < p_zero:
            return no_fill                   # displayed depth that isn't matchable
        if params.kappa > 0:
            sz = sz * float(params.kappa)
    return walk_buy(px, sz, stake_usd, fees=fees)


# --------------------------------------------------------------------------
# Calibration
# --------------------------------------------------------------------------

def calibrate_from_frames(attempts: pd.DataFrame,
                          window: Tuple[str, str],
                          version: str = "live-1") -> LiveFillParams:
    """Fit the binned tables from an attempt frame with columns:
    ok, filled_shares, target_shares, depth_band_shares, time_left, latency_s,
    quoted_ask, avg_price. Pure (no I/O) — unit-testable."""
    att = attempts.copy()
    att["depth_ratio"] = att["depth_band_shares"] / att["target_shares"].clip(lower=1e-9)
    att["dbin"] = att["depth_ratio"].map(_depth_bin)
    att["tbin"] = att["time_left"].map(_tl_bin)
    att["zero"] = (~att["ok"].astype(bool)) | (att["filled_shares"] <= 0)

    table: Dict[str, Dict[str, Dict[str, float]]] = {}
    for _, _, dname in DEPTH_BINS:
        table[dname] = {}
        for _, _, tname in TL_BINS:
            g = att[(att["dbin"] == dname) & (att["tbin"] == tname)]
            n = int(len(g))
            # empty cells inherit the marginal rate of their depth bin (or global)
            if n == 0:
                gd = att[att["dbin"] == dname]
                p = float(gd["zero"].mean()) if len(gd) else float(att["zero"].mean())
            else:
                p = float(g["zero"].mean())
            table[dname][tname] = {"p": round(p, 4), "n": n}

    pos = att[(att["depth_band_shares"] > 0) & (~att["zero"])]
    cap = np.minimum(pos["depth_band_shares"], pos["target_shares"])
    kappa = float(pos["filled_shares"].sum() / cap.sum()) if len(pos) and cap.sum() > 0 else 1.0
    kappa = float(min(max(kappa, 0.05), 1.5))

    filled = att[~att["zero"]]
    knife = filled["avg_price"] < filled["quoted_ask"] - GUARD_FLOOR_DROP
    clean = filled[~knife]
    mean_slip = float((clean["avg_price"] - clean["quoted_ask"]).mean()) if len(clean) else 0.0
    knife_rate = float(knife.mean()) if len(filled) else 0.0

    lat = att["latency_s"].clip(lower=0.0, upper=60.0)
    latency_samples = [round(float(x), 3) for x in lat.tolist()]

    validation = {
        "fill_rate_by_bin": {
            d: {t: {"observed_fill_rate": round(1.0 - table[d][t]["p"], 4),
                    "n": table[d][t]["n"]}
                for t in table[d]} for d in table},
        "overall_fill_rate": round(float((~att["zero"]).mean()), 4),
        "brier_zero_fill": round(float(np.mean(
            (att.apply(lambda r: table[r["dbin"]][r["tbin"]]["p"], axis=1)
             - att["zero"].astype(float)) ** 2)), 4),
        "mean_abs_price_err_filled": round(float(
            (clean["avg_price"] - clean["quoted_ask"]).abs().mean()), 4) if len(clean) else None,
    }
    return LiveFillParams(version=version,
                          calibration_window=window,
                          zero_fill_prob=table,
                          kappa=round(kappa, 4),
                          latency_samples_s=latency_samples,
                          mean_slip_filled=round(mean_slip, 4),
                          knife_rate_legacy=round(knife_rate, 4),
                          n_attempts=int(len(att)),
                          validation=validation)


def _build_attempt_frame(live_dir: Path, jsonl_root: Path) -> pd.DataFrame:
    """Join fills (incl. failed) -> intents -> L2 ladder at the intent second.
    DOWN-side ask ladder = 1 - bid_px_i with bid_sz_i (hypothesis_verify convention)."""
    from research.analysis.live_gap_attribution import load_fills, load_intents
    from research.dataset.feeds import load_l2_ladders

    intents = load_intents(Path(live_dir) / "intents.jsonl")
    fills = load_fills(Path(live_dir) / "fills.jsonl",
                       valid_sids=set(intents["strategy_id"]))
    it = intents.set_index(["strategy_id", "slug"])
    rows = []
    # group needed slugs by symbol; pull each symbol's L2 once over the full span
    fills = fills.assign(symbol=fills["slug"].str.split("-").str[0])
    span_start = pd.to_datetime(
        intents["ts_ms"].min(), unit="ms", utc=True).strftime("%Y-%m-%d")
    span_end = pd.to_datetime(
        intents["ts_ms"].max(), unit="ms", utc=True).strftime("%Y-%m-%d")
    for sym, g in fills.groupby("symbol"):
        try:
            l2 = load_l2_ladders(sym, span_start, span_end)
        except Exception:
            l2 = pd.DataFrame()
        for _, f in g.iterrows():
            key = (f["strategy_id"], f["slug"])
            if key not in it.index:
                continue
            i = it.loc[key]
            entry_ask = float(i["entry_ask"])
            ceiling = float(f.get("ceiling") or i.get("max_ask") or 0.92)
            target = float(f.get("target_shares") or
                           (float(i.get("bet_usd", 5.0)) / max(entry_ask, 1e-9)))
            sec = int((float(i["ts_ms"]) / 1000.0) % (300 if "-5m-" in f["slug"] else 900))
            depth = np.nan
            if not l2.empty and f["slug"] in l2.index.get_level_values(0):
                sl = l2.loc[f["slug"]]
                idx = sl.index.get_indexer([sec], method="nearest")
                if idx[0] >= 0:
                    row = sl.iloc[idx[0]]
                    if str(f.get("side")) == "UP":
                        lvl = [(float(row[f"ask_px_{k}"]), float(row[f"ask_sz_{k}"]))
                               for k in range(1, 11)]
                    else:
                        lvl = [(1.0 - float(row[f"bid_px_{k}"]), float(row[f"bid_sz_{k}"]))
                               for k in range(1, 11)]
                    depth = sum(s for p, s in lvl
                                if np.isfinite(p) and np.isfinite(s)
                                and 0 < p <= ceiling + 1e-9)
            lat_s = float(f.get("latency_ms") or 0.0) / 1000.0
            pickup = max(0.0, float(f["ts"]) - float(i["ts_ms"]) / 1000.0 - lat_s)
            rows.append({
                "strategy_id": f["strategy_id"], "slug": f["slug"],
                "ok": bool(f.get("ok")),
                "filled_shares": float(f.get("filled_shares") or 0.0),
                "target_shares": target,
                "depth_band_shares": depth,
                "time_left": float(i.get("time_left", 120.0)),
                "latency_s": lat_s + pickup,
                "quoted_ask": float(f.get("quoted_ask") or entry_ask),
                "avg_price": float(f.get("avg_price") or 0.0),
            })
    att = pd.DataFrame(rows)
    if att.empty:
        return att
    # attempts without an L2 match fall back to the displayed-depth proxy of
    # "enough" (ratio 2): the hazard then reflects the marginal rate
    med = att["depth_band_shares"].median()
    att["depth_band_shares"] = att["depth_band_shares"].fillna(
        med if np.isfinite(med) else 2.0 * att["target_shares"].median())
    return att


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--live-dir", default=str(REPO / "data" / "live"))
    ap.add_argument("--jsonl-root", default=str(REPO / "data" / "jsonl"))
    ap.add_argument("--out", default=str(DEFAULT_PARAMS_PATH))
    ap.add_argument("--since", default=None,
                    help="UTC date; only attempts whose window_start >= this (e.g. the "
                         "CLEAN_CUT 2026-06-12 — pre-fix attempts saw stale books)")
    ap.add_argument("--holdout-days", type=int, default=0,
                    help="fit on attempts older than the last N days; report predicted-vs-"
                         "observed zero-fill on the held-out recent slice")
    ap.add_argument("--version", default="live-1", help="params version string, e.g. live-2")
    args = ap.parse_args()
    if not args.calibrate:
        ap.print_help()
        return
    att = _build_attempt_frame(Path(args.live_dir), Path(args.jsonl_root))
    if att.empty:
        print("no attempts found — nothing to calibrate")
        return
    epoch = att["slug"].str.extract(r"-(\d+)$")[0].astype(float)
    if args.since:
        cut = pd.Timestamp(args.since, tz="UTC").timestamp()
        att, epoch = att[epoch >= cut], epoch[epoch >= cut]
        print(f"restricted to window_start >= {args.since}: {len(att)} attempts")
        if att.empty:
            print("nothing left after --since filter")
            return
    holdout = None
    if args.holdout_days > 0:
        hcut = epoch.max() - args.holdout_days * 86400
        holdout, att = att[epoch >= hcut].copy(), att[epoch < hcut]
        epoch = epoch[epoch.index.isin(att.index)]
        print(f"holdout: last {args.holdout_days}d = {len(holdout)} attempts; "
              f"fit on {len(att)}")
        if att.empty:
            print("nothing left to fit after holdout split")
            return
    window = (str(epoch.min()), str(epoch.max()))
    # human-readable window
    w0 = pd.to_datetime(float(window[0]), unit="s", utc=True).strftime("%Y-%m-%d")
    w1 = pd.to_datetime(float(window[1]), unit="s", utc=True).strftime("%Y-%m-%d")
    params = calibrate_from_frames(att, window=(w0, w1), version=args.version)
    if holdout is not None and len(holdout):
        h = holdout.copy()
        h["depth_ratio"] = h["depth_band_shares"] / h["target_shares"].clip(lower=1e-9)
        h["zero"] = (~h["ok"].astype(bool)) | (h["filled_shares"] <= 0)
        pred = h.apply(lambda r: params.zero_p(r["depth_ratio"], r["time_left"]), axis=1)
        params.validation["holdout"] = {
            "days": args.holdout_days, "n": int(len(h)),
            "observed_fill_rate": round(float((~h["zero"]).mean()), 4),
            "predicted_fill_rate": round(float((1.0 - pred).mean()), 4),
            "brier_zero_fill": round(float(np.mean((pred - h["zero"].astype(float)) ** 2)), 4),
        }
        print(f"  holdout({args.holdout_days}d, n={len(h)}): "
              f"observed fill {params.validation['holdout']['observed_fill_rate']} vs "
              f"predicted {params.validation['holdout']['predicted_fill_rate']}, "
              f"brier {params.validation['holdout']['brier_zero_fill']}")
    save_params(params, Path(args.out))
    print(f"calibrated on {params.n_attempts} attempts ({w0}..{w1})")
    print(f"  kappa={params.kappa}  knife_rate_legacy={params.knife_rate_legacy}  "
          f"mean_slip_filled={params.mean_slip_filled}")
    print(f"  overall fill rate: {params.validation['overall_fill_rate']}")
    print(f"  brier(zero-fill): {params.validation['brier_zero_fill']}")
    print("  zero-fill prob by [depth x time-left]:")
    for d in params.zero_fill_prob:
        cells = "  ".join(f"{t}:{params.zero_fill_prob[d][t]['p']:.2f}"
                          f"(n={params.zero_fill_prob[d][t]['n']})"
                          for t in params.zero_fill_prob[d])
        print(f"    {d:8s} {cells}")
    print(f"  wrote {args.out}")


if __name__ == "__main__":
    main()
