"""Live-vs-paper gap attribution.

Pairs every live intent (data/live/intents.jsonl) with its paper-twin trade
(data/jsonl/<sid>/trades.jsonl — the live strategies run inside the paper engine,
so the twin shares the same strategy_id and slug) and with the live settlement,
then decomposes the live-vs-paper P&L gap into additive components:

    cmp_settle_basis  paper settled on the wrong oracle (coinbase fallback)
    cmp_price         paid a different price than the paper entry (live shares)
    cmp_size          held a different share count into the (true) outcome
    cmp_fee           paper-only fee (live pays no fee on these markets)
    cmp_missed        intent never became a position (counterfactual = -true paper pnl)
    cmp_unmatched     live fill with no paper twin (should not happen; surfaced)

Identity per matched-and-settled window (binary payoff, no live fee):
    pnl = shares * (won - price) - fee
    live_pnl - paper_pnl = e + price + size + fee          (residual < 1e-6)
with  e = N_p*(won_true - won_p), price = N_l*(p_p - p_l),
      size = (N_l - N_p)*(won_true - p_p), fee = F_p.

Two output views:
  1. additive gap decomposition (why live != paper), and
  2. per-cohort live EV (is a fill cohort -EV in absolute terms — the basis for
     executor guards, e.g. knife-catch fills).

Usage:
    uv run python -m research.analysis.live_gap_attribution [--knife-x 0.04]
        [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--sid SID] [--csv-out]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import glob
import json
import os
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

import pandas as pd

KNIFE_X_DEFAULT = 0.04
LEGACY_OWNER = "det_lwd_live"  # fills written before the multi-strategy executor
PARTIAL_RATIO = 0.95
SLUG_RE = re.compile(r"^(btc|eth|sol|xrp)-updown-(5m|15m)-\d+$")
JUNK_TOKENS = {"UPTOK", "DOWNTOK"}

REPO = Path(__file__).resolve().parents[2]
DEFAULT_LIVE_DIR = REPO / "data" / "live"
DEFAULT_JSONL_ROOT = REPO / "data" / "jsonl"
DEFAULT_OUT_DIR = REPO / "data" / "research" / "live_gap"


def _read_jsonl(path: Path) -> List[dict]:
    rows: List[dict] = []
    if not Path(path).exists():
        return rows
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # tolerate a torn tail line (crash-safe appenders)
    return rows


def load_intents(path: Path) -> pd.DataFrame:
    df = pd.DataFrame(_read_jsonl(path))
    if df.empty:
        return df
    df = df.drop_duplicates(subset=["strategy_id", "slug"], keep="first")
    return df.reset_index(drop=True)


def load_fills(path: Path, valid_sids: Set[str]) -> pd.DataFrame:
    rows = _read_jsonl(path)
    out: List[dict] = []
    for r in rows:
        if r.get("dry_run"):
            continue
        if r.get("mode") not in (None, "live"):
            continue
        if str(r.get("token_id", "")) in JUNK_TOKENS:
            continue
        slug = str(r.get("slug", ""))
        if not SLUG_RE.match(slug):
            continue
        sid = r.get("strategy_id") or LEGACY_OWNER
        if sid not in valid_sids:
            continue
        r = dict(r)
        r["strategy_id"] = sid
        r["ceiling"] = r.get("max_ask", r.get("limit_price"))
        out.append(r)
    df = pd.DataFrame(out)
    if df.empty:
        return df
    df = df.drop_duplicates(subset=["strategy_id", "slug"], keep="last")
    return df.reset_index(drop=True)


def load_settlements(live_dir: Path) -> pd.DataFrame:
    rows: List[dict] = []
    for p in sorted(glob.glob(str(Path(live_dir) / "settlements.jsonl*"))):
        rows.extend(_read_jsonl(Path(p)))
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.drop_duplicates(subset=["strategy_id", "slug"], keep="last")
    return df.reset_index(drop=True)


def load_paper(jsonl_root: Path, sid: str) -> pd.DataFrame:
    base = pd.DataFrame(_read_jsonl(Path(jsonl_root) / sid / "trades.jsonl"))
    det = pd.DataFrame(_read_jsonl(Path(jsonl_root) / sid / "trades_detailed.jsonl"))
    if base.empty:
        return base
    base = base.drop_duplicates(subset=["slug"], keep="last")
    cols = ["slug"]
    for c in ("won", "settle_basis"):
        if not det.empty and c in det.columns:
            cols.append(c)
    if not det.empty and len(cols) > 1:
        det = det.drop_duplicates(subset=["slug"], keep="last")
        base = base.merge(det[cols], on="slug", how="left")
    if "won" not in base.columns:
        base["won"] = (base["exit_price"] >= 0.5).astype(int)
    if "settle_basis" not in base.columns:
        base["settle_basis"] = "unknown"
    return base.reset_index(drop=True)


def load_geoblock_times(log_path: Path) -> List[float]:
    """Epoch seconds of order-POST geoblock events (403 'Trading restricted') in the
    executor log. A VPN exit IP blocks ONLY /order while reads keep working, and the
    raised exception means NO fill record is written — so geoblocked intents land in
    no_attempt and must be reclassified (the VPN's miss, not the executor's)."""
    times: List[float] = []
    if not log_path or not Path(log_path).exists():
        return times
    with open(log_path, errors="replace") as f:
        for line in f:
            if "clob_place_failed" not in line:
                continue
            if "403" not in line and "restricted" not in line.lower():
                continue
            stamp = line.split(" [", 1)[0].strip()
            ts = None
            for fmt, utc in (("%Y-%m-%dT%H:%M:%SZ", True),
                             ("%Y-%m-%dT%H:%M:%S.%fZ", True),
                             ("%Y-%m-%d %H:%M:%S", False)):
                try:
                    d = _dt.datetime.strptime(stamp, fmt)
                    if utc:
                        d = d.replace(tzinfo=_dt.timezone.utc)
                    else:
                        d = d.astimezone()  # log written in local time
                    ts = d.timestamp()
                    break
                except ValueError:
                    continue
            if ts is not None:
                times.append(ts)
    return times


GEOBLOCK_TOL_S = 90.0


def _truth_map(settles: pd.DataFrame) -> Dict[str, int]:
    """slug -> outcome_up (1/0) from any live settlement on that window."""
    truth: Dict[str, int] = {}
    if settles.empty:
        return truth
    for _, s in settles.iterrows():
        out = s.get("outcome")
        if isinstance(out, str) and out.lower() in ("up", "down"):
            up = 1 if out.lower() == "up" else 0
        else:
            won = bool(s.get("won"))
            up = int(won if s.get("side") == "UP" else (not won))
        truth[s["slug"]] = up
    return truth


def build_attribution(live_dir: Path = DEFAULT_LIVE_DIR,
                      jsonl_root: Path = DEFAULT_JSONL_ROOT,
                      knife_x: float = KNIFE_X_DEFAULT,
                      since_ms: Optional[int] = None,
                      until_ms: Optional[int] = None,
                      only_sid: Optional[str] = None,
                      geoblock_log: Optional[Path] = None) -> pd.DataFrame:
    intents = load_intents(Path(live_dir) / "intents.jsonl")
    if intents.empty:
        return pd.DataFrame()
    if since_ms is not None:
        intents = intents[intents["ts_ms"] >= since_ms]
    if until_ms is not None:
        intents = intents[intents["ts_ms"] < until_ms]
    if only_sid:
        intents = intents[intents["strategy_id"] == only_sid]

    sids = sorted(set(intents["strategy_id"]))
    fills = load_fills(Path(live_dir) / "fills.jsonl", valid_sids=set(sids))
    settles = load_settlements(Path(live_dir))
    paper = pd.concat([load_paper(jsonl_root, sid).assign(strategy_id=sid)
                       for sid in sids], ignore_index=True, sort=False)
    truth = _truth_map(settles)
    geo_times = load_geoblock_times(geoblock_log) if geoblock_log else []

    f_idx = ({} if fills.empty else
             {(r["strategy_id"], r["slug"]): r for _, r in fills.iterrows()})
    s_idx = ({} if settles.empty else
             {(r["strategy_id"], r["slug"]): r for _, r in settles.iterrows()})
    p_idx = ({} if paper.empty else
             {(r["strategy_id"], r["slug"]): r for _, r in paper.iterrows()})

    rows: List[dict] = []
    for _, it in intents.iterrows():
        key = (it["strategy_id"], it["slug"])
        fill = f_idx.get(key)
        sett = s_idx.get(key)
        pap = p_idx.get(key)
        r = {
            "strategy_id": key[0], "slug": key[1], "side": it.get("side"),
            "intent_ts_ms": it.get("ts_ms"), "entry_ask_intent": it.get("entry_ask"),
            "cmp_settle_basis": 0.0, "cmp_price": 0.0, "cmp_size": 0.0,
            "cmp_fee": 0.0, "cmp_missed": 0.0, "cmp_unmatched": 0.0,
            "row_gap": 0.0, "residual": 0.0, "live_pnl": 0.0, "paper_pnl": 0.0,
            "basis_unverified": False, "fail_note": None,
            "quoted_ask": None, "avg_price": None, "latency_ms": None,
        }
        if fill is not None:
            r["quoted_ask"] = fill.get("quoted_ask")
            r["avg_price"] = fill.get("avg_price")
            r["latency_ms"] = fill.get("latency_ms")
            r["fail_note"] = fill.get("note")

        if pap is None:
            r["cohort"] = "no_paper"
            rows.append(r)
            continue

        p_p = float(pap["entry_price"])
        n_p = float(pap["shares"])
        f_p = float(pap.get("fee_total", 0.0) or 0.0)
        pnl_p = float(pap["pnl"])
        won_p = int(pap.get("won", 0))
        basis = pap.get("settle_basis", "unknown")
        r["paper_pnl"] = pnl_p

        # true outcome for this side
        if sett is not None:
            won_true = int(bool(sett.get("won")))
        elif key[1] in truth:
            up = truth[key[1]]
            won_true = int(up if it.get("side") == "UP" else (1 - up))
        else:
            won_true = won_p
            if basis != "chainlink":
                r["basis_unverified"] = True

        e_cmp = n_p * (won_true - won_p)
        r["cmp_settle_basis"] = e_cmp

        filled_ok = fill is not None and bool(fill.get("ok"))
        if not filled_ok:
            its = float(it.get("ts_ms", 0)) / 1000.0
            if geo_times and any(abs(its - t) <= GEOBLOCK_TOL_S for t in geo_times):
                # the VPN's miss, not the executor's — excluded from the accounting
                r["cohort"] = "geoblocked"
                r["cmp_settle_basis"] = 0.0
                rows.append(r)
                continue
            r["cohort"] = "attempted_zero" if fill is not None else "no_attempt"
            r["cmp_missed"] = -(pnl_p + e_cmp)
            r["row_gap"] = -pnl_p
            rows.append(r)
            continue

        if sett is None:
            r["cohort"] = "pending"
            r["cmp_settle_basis"] = 0.0  # excluded from $ until settled
            rows.append(r)
            continue

        n_l = float(sett.get("shares", fill.get("filled_shares", 0.0)))
        u_l = float(sett.get("usdc", fill.get("usdc_paid", 0.0)))
        p_l = (u_l / n_l) if n_l else 0.0
        pnl_l = float(sett.get("pnl", (n_l - u_l) if won_true else -u_l))
        r["live_pnl"] = pnl_l

        r["cmp_price"] = n_l * (p_p - p_l)
        r["cmp_size"] = (n_l - n_p) * (won_true - p_p)
        r["cmp_fee"] = f_p
        r["row_gap"] = pnl_l - pnl_p
        r["residual"] = r["row_gap"] - (e_cmp + r["cmp_price"] + r["cmp_size"] + r["cmp_fee"])

        qa = fill.get("quoted_ask")
        ap = fill.get("avg_price")
        if qa is not None and ap is not None and float(ap) < float(qa) - knife_x:
            r["cohort"] = "filled_knife"
        elif float(fill.get("fill_ratio", 1.0) or 1.0) < PARTIAL_RATIO:
            r["cohort"] = "filled_partial"
        else:
            r["cohort"] = "filled_normal"
        rows.append(r)

    # live fills with no intent in the universe (should not happen; surface them)
    if not fills.empty:
        intent_keys = set(zip(intents["strategy_id"], intents["slug"]))
        for _, fl in fills.iterrows():
            key = (fl["strategy_id"], fl["slug"])
            if key in intent_keys or not bool(fl.get("ok")):
                continue
            sett = s_idx.get(key)
            pnl_l = float(sett["pnl"]) if sett is not None else 0.0
            rows.append({
                "strategy_id": key[0], "slug": key[1], "side": fl.get("side"),
                "intent_ts_ms": None, "entry_ask_intent": None,
                "cohort": "unmatched_live",
                "cmp_settle_basis": 0.0, "cmp_price": 0.0, "cmp_size": 0.0,
                "cmp_fee": 0.0, "cmp_missed": 0.0, "cmp_unmatched": pnl_l,
                "row_gap": pnl_l, "residual": 0.0,
                "live_pnl": pnl_l, "paper_pnl": 0.0, "basis_unverified": False,
                "fail_note": None, "quoted_ask": fl.get("quoted_ask"),
                "avg_price": fl.get("avg_price"), "latency_ms": fl.get("latency_ms"),
            })

    return pd.DataFrame(rows)


COMPONENTS = ["cmp_settle_basis", "cmp_price", "cmp_size", "cmp_fee",
              "cmp_missed", "cmp_unmatched"]
EXCLUDED_COHORTS = {"pending", "no_paper", "geoblocked"}


def summarize(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"total": {"gap": 0.0, "residual": 0.0, "components": {}},
                "per_strategy": {}, "cohorts": {}}
    acc = df[~df["cohort"].isin(EXCLUDED_COHORTS)]
    out = {
        "total": {
            "n_intents": int(len(df)),
            "gap": float(acc["row_gap"].sum()),
            "residual": float(acc["residual"].sum()),
            "live_pnl": float(acc["live_pnl"].sum()),
            "paper_pnl": float(acc["paper_pnl"].sum()),
            "components": {c: float(acc[c].sum()) for c in COMPONENTS},
            "basis_unverified_rows": int(df["basis_unverified"].sum()),
        },
        "per_strategy": {},
        "cohorts": {},
    }
    for sid, g in acc.groupby("strategy_id"):
        out["per_strategy"][sid] = {
            "n": int(len(g)),
            "gap": float(g["row_gap"].sum()),
            "live_pnl": float(g["live_pnl"].sum()),
            "paper_pnl": float(g["paper_pnl"].sum()),
            "components": {c: float(g[c].sum()) for c in COMPONENTS},
        }
    for coh, g in df.groupby("cohort"):
        out["cohorts"][coh] = {
            "n": int(len(g)),
            "live_pnl": float(g["live_pnl"].sum()),
            "paper_pnl": float(g["paper_pnl"].sum()),
            "gap": float(g["row_gap"].sum()),
        }
    return out


def _fmt_table(s: dict) -> str:
    lines = []
    t = s["total"]
    lines.append("LIVE-vs-PAPER GAP ATTRIBUTION")
    lines.append(f"  intents={t.get('n_intents', 0)}  live=${t['live_pnl']:+.2f}  "
                 f"paper=${t['paper_pnl']:+.2f}  gap=${t['gap']:+.2f}  "
                 f"(residual ${t['residual']:+.4f})")
    label = {
        "cmp_missed": "missed trades (counterfactual)",
        "cmp_price": "price slippage on fills",
        "cmp_size": "share-count vs outcome",
        "cmp_fee": "paper-only fees",
        "cmp_settle_basis": "paper settled on wrong oracle",
        "cmp_unmatched": "live fills w/o paper twin",
    }
    for c in COMPONENTS:
        v = t["components"].get(c, 0.0)
        lines.append(f"    {label[c]:36s} ${v:+9.2f}")
    if t.get("basis_unverified_rows"):
        lines.append(f"    (basis unverified on {t['basis_unverified_rows']} rows — "
                     f"paper outcome trusted)")
    lines.append("")
    lines.append("  per strategy:")
    for sid, g in sorted(s["per_strategy"].items()):
        lines.append(f"    {sid:22s} n={g['n']:4d}  live ${g['live_pnl']:+8.2f}  "
                     f"paper ${g['paper_pnl']:+8.2f}  gap ${g['gap']:+8.2f}")
    lines.append("")
    lines.append("  per cohort (live EV view — guard justification):")
    for coh, g in sorted(s["cohorts"].items()):
        lines.append(f"    {coh:16s} n={g['n']:4d}  live ${g['live_pnl']:+8.2f}  "
                     f"paper ${g['paper_pnl']:+8.2f}  gap ${g['gap']:+8.2f}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live-dir", default=str(DEFAULT_LIVE_DIR))
    ap.add_argument("--jsonl-root", default=str(DEFAULT_JSONL_ROOT))
    ap.add_argument("--knife-x", type=float, default=KNIFE_X_DEFAULT)
    ap.add_argument("--since", default=None, help="YYYY-MM-DD (UTC)")
    ap.add_argument("--until", default=None, help="YYYY-MM-DD (UTC)")
    ap.add_argument("--sid", default=None)
    ap.add_argument("--csv-out", action="store_true")
    ap.add_argument("--geoblock-log", default=str(REPO / "logs" / "live_exec.log"))
    args = ap.parse_args()

    def _ms(d: Optional[str]) -> Optional[int]:
        if not d:
            return None
        return int(pd.Timestamp(d, tz="UTC").timestamp() * 1000)

    df = build_attribution(Path(args.live_dir), Path(args.jsonl_root),
                           knife_x=args.knife_x, since_ms=_ms(args.since),
                           until_ms=_ms(args.until), only_sid=args.sid,
                           geoblock_log=Path(args.geoblock_log))
    s = summarize(df)
    print(_fmt_table(s))
    if args.csv_out and not df.empty:
        DEFAULT_OUT_DIR.mkdir(parents=True, exist_ok=True)
        csv_path = DEFAULT_OUT_DIR / "live_gap_windows.csv"
        json_path = DEFAULT_OUT_DIR / "live_gap_attribution.json"
        df.to_csv(csv_path, index=False)
        with open(json_path, "w") as f:
            json.dump(s, f, indent=2)
        print(f"\n  wrote {csv_path}\n  wrote {json_path}")


if __name__ == "__main__":
    main()
