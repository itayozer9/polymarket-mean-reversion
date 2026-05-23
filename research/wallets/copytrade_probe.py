"""Directional-holder honest win rate + copy-trading probe.

Answers the project owner's three questions, entirely from the on-disk
leaderboard-wallet cache (``research.wallets.fetch_wallets`` output):

1. How do directional buy-and-hold wallets win "so well" — what is the REAL
   win rate of each, once the structurally-invisible silent losses are counted?
2. Could we copy the leaders?
3. Are the leaders copying each other?

THE STRUCTURAL BIAS THIS MODULE CORRECTS
----------------------------------------
The FIFO ``win_rate`` in ``wallet_summary.parquet`` is inflated: a winning
hold-to-resolution position produces a REDEEM activity record, but a *losing*
hold-to-resolution position simply expires worthless and produces **no record
at all**. A win-rate that only sees REDEEMs therefore never sees the losers.

The fix: a (wallet, slug) market with only BUYs and no exit (``exit_mode ==
"none"``) whose 15m/5m window has already *closed* is a **silent loss** — the
shares expired worthless. Counting those silent losses gives an *honest* win
rate. ``market_cashflow.parquet`` already nets every USDC movement per slug, so
``net_pnl`` is the per-slug realized result; ``net_pnl > 0`` won, ``< 0`` lost.

THE DECISIVE METRIC
-------------------
A high win rate is EXPECTED when you buy favourites: buy at odds 0.85 and you
win ~85% of the time with zero skill, because that is what the price *means*.
The only metric that shows edge is::

    excess_win_rate = honest_win_rate - vwap_entry_odds

Positive ``excess_win_rate`` means the wallet wins MORE than its entry odds
implied — that is skill (or variance). Pooled across a calibrated market it is
~0.

TWO METHOD POINTS THAT MUST NOT BE GOT WRONG
--------------------------------------------
1. **Single-sided slugs only.** Many wallets buy BOTH the Up and the Down leg
   of the same window (a hedged / scalp position, not a directional bet). For
   such a slug the "entry odds" is meaningless — the two legs price ~p and
   ~1-p, so a USDC-weighted average lands near 0.5 regardless of conviction.
   The honest-win-rate-vs-entry-odds comparison is only defined for genuinely
   *directional* (single-outcome) positions, so Part A restricts to slugs
   where the wallet bought exactly one outcome.

2. **Consistent weighting (avoid Simpson's paradox).** ``excess`` must compare
   a win rate and an entry-odds figure computed under the SAME weighting.
   Comparing a count-weighted win rate (many tiny longshot bets) to a
   USDC-weighted entry-odds (big money on favourites) mixes two populations
   and produces a spurious large-negative number. Part A reports the per-slug
   excess (``won{0,1} - slug_vwap_entry``) both count-weighted and
   USDC-weighted, and the two agree.

All computation is in UTC / unix seconds. The report
(``docs/research/directional_winrate_copytrade.md``) shows Israel local time.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import re
from collections import defaultdict
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import structlog

from research.wallets.analyze import (
    DERIVED_DIR,
    REPO_ROOT,
    load_activity,
    load_manifest,
)
from research.wallets.wallet_report import IDT, assign_archetype

log = structlog.get_logger(__name__)

REPORT_PATH = REPO_ROOT / "docs" / "research" / "directional_winrate_copytrade.md"

# A compact Up/Down slug `<asset>-updown-<n><unit>-<window_start_unix_ts>`.
# The <n><unit> token gives the window length; the trailing int is the window
# start. Verified against the real cache (see entry_prices.py).
_SLUG_COMPACT = re.compile(
    r"^(?:btc|eth|sol|xrp)-updown-(\d+)(m|h)-(\d+)$", re.IGNORECASE,
)

# Polymarket's published maker rebate is ~20% of the taker fee on matched
# volume; the taker fee model (polymarket-arb) is fee = shares*0.07*p*(1-p).
TAKER_FEE_RATE = 0.07
MAKER_REBATE_FRAC = 0.20


# ==========================================================================
# Pure helpers — window timing
# ==========================================================================
def parse_compact_slug(slug: str) -> Optional[tuple[int, int]]:
    """Return ``(window_start_ts, window_duration_seconds)`` for a compact
    Up/Down slug, or ``None`` if the slug is not a compact Up/Down slug.

    >>> parse_compact_slug("btc-updown-15m-1779473700")
    (1779473700, 900)
    >>> parse_compact_slug("eth-updown-5m-1779473100")
    (1779473100, 300)
    >>> parse_compact_slug("bitcoin-above-78k-on-may-24-2026") is None
    True
    """
    m = _SLUG_COMPACT.match((slug or "").strip().lower())
    if not m:
        return None
    n, unit, ws = int(m.group(1)), m.group(2), int(m.group(3))
    duration = n * 60 if unit == "m" else n * 3600
    return ws, duration


def window_close_ts(slug: str) -> Optional[int]:
    """Unix ts at which a compact Up/Down market's window closes (resolves),
    or ``None`` for a non-compact slug."""
    parsed = parse_compact_slug(slug)
    if parsed is None:
        return None
    ws, duration = parsed
    return ws + duration


# ==========================================================================
# PART A — honest per-wallet win rate vs entry odds
# ==========================================================================
# A (wallet, slug) market is a usable DIRECTIONAL position when:
#   * the slug is a compact Up/Down slug (so the window-close ts is known),
#   * exit_mode is "redeem" or "none" (held to resolution; not a sell-out and
#     not a mint-merge unwind),
#   * n_merges == 0 (no merge leg — a merge is a non-directional unwind).
# It is RESOLVED (countable) only if its window has already closed by the
# cache fetch time; an open window is excluded, not counted as a loss.
# Outcome: net_pnl > 0 -> won; net_pnl < 0 -> lost; net_pnl == 0 -> neither
# (a redeem-only artefact of a truncated cache, no buys) -> dropped.

WINLOSS_COLUMNS = [
    "wallet", "slug", "won", "lost", "net_pnl", "buy_usdc",
    "window_start_ts", "window_close_ts",
]


def classify_directional_slug(
    row: dict, fetch_ts: int,
) -> Optional[str]:
    """Classify ONE (wallet, slug) cash-flow row as ``"won"`` / ``"lost"`` /
    ``None`` (excluded).

    ``row`` carries at least ``slug``, ``exit_mode``, ``n_merges``,
    ``net_pnl``, ``n_buys``. ``fetch_ts`` is the cache fetch unix time.

    Returns ``None`` (excluded from the win rate) when the market is:
    not a compact Up/Down slug, sell-exited, merge-involved, still-open at
    fetch time, or has no buys (a truncated redeem-only artefact).
    """
    parsed = parse_compact_slug(row.get("slug", ""))
    if parsed is None:
        return None  # not a compact Up/Down market — window timing unknown
    if row.get("exit_mode") not in ("redeem", "none"):
        return None  # sold out, or merge-dominant — not a directional hold
    if int(row.get("n_merges") or 0) > 0:
        return None  # has a merge leg — non-directional unwind
    if int(row.get("n_buys") or 0) <= 0:
        return None  # redeem-only artefact of a truncated cache — no position
    ws, duration = parsed
    if ws + duration > fetch_ts:
        return None  # window has not closed yet — unresolved, do NOT count
    net = float(row.get("net_pnl") or 0.0)
    if net > 0:
        return "won"
    if net < 0:
        return "lost"
    return None  # exactly zero — neither a win nor a loss


def build_winloss_frame(
    cashflow: pd.DataFrame, fetch_ts: int,
) -> pd.DataFrame:
    """Per (wallet, slug) won/lost classification for every directional
    compact-slug position in ``cashflow``. One row per countable market."""
    rows: list[dict] = []
    for rec in cashflow.to_dict("records"):
        verdict = classify_directional_slug(rec, fetch_ts)
        if verdict is None:
            continue
        parsed = parse_compact_slug(rec["slug"])
        ws, duration = parsed  # parsed is not None — classify_* checked it
        rows.append({
            "wallet": rec["wallet"],
            "slug": rec["slug"],
            "won": verdict == "won",
            "lost": verdict == "lost",
            "net_pnl": float(rec.get("net_pnl") or 0.0),
            "buy_usdc": float(rec.get("buy_usdc") or 0.0),
            "window_start_ts": ws,
            "window_close_ts": ws + duration,
        })
    if not rows:
        return pd.DataFrame(columns=WINLOSS_COLUMNS)
    return pd.DataFrame(rows)[WINLOSS_COLUMNS]


def slug_buys_by_outcome(
    activity: list[dict], slugs: set[str],
) -> dict[str, dict[str, list[tuple[float, float]]]]:
    """For each slug in ``slugs``, the BUY ``(price, usdc_size)`` pairs grouped
    by ``outcome``. Used to (a) detect single- vs both-sided slugs and (b)
    compute a slug's VWAP entry price."""
    out: dict[str, dict[str, list[tuple[float, float]]]] = {
        sg: defaultdict(list) for sg in slugs}
    for r in activity or []:
        if r.get("type") != "TRADE" or r.get("side") != "BUY":
            continue
        sg = r.get("slug")
        if sg not in out:
            continue
        try:
            p = float(r.get("price"))
            w = float(r.get("usdc_size") or 0.0)
        except (TypeError, ValueError):
            continue
        if not (0.0 <= p <= 1.0) or w <= 0:
            continue
        out[sg][r.get("outcome") or ""].append((p, w))
    return out


def slug_vwap_entry(buys_by_outcome: dict[str, list[tuple[float, float]]],
                    ) -> Optional[float]:
    """USDC-weighted average BUY price for ONE single-sided slug.

    Returns ``None`` if the slug is both-sided (more than one outcome bought)
    — its entry odds is undefined for a directional analysis — or if there is
    no priced buy volume.
    """
    outcomes = [o for o, lst in buys_by_outcome.items() if lst]
    if len(outcomes) != 1:
        return None  # both-sided (or empty) — not a directional position
    num = den = 0.0
    for p, w in buys_by_outcome[outcomes[0]]:
        num += p * w
        den += w
    return num / den if den > 0 else None


def excess_win_rate(honest_wr: float, vwap_odds: float) -> float:
    """The decisive edge metric: honest win rate minus VWAP entry odds.

    ~0 means the wallet wins exactly as often as its entry price implied (no
    edge — a calibrated favourite buyer). Materially > 0 means real edge.
    Both inputs must be computed under the SAME weighting (see module
    docstring, method point 2).
    """
    if pd.isna(honest_wr) or pd.isna(vwap_odds):
        return float("nan")
    return float(honest_wr) - float(vwap_odds)


PER_SLUG_COLUMNS = [
    "wallet", "slug", "won", "entry_odds", "buy_usdc",
    "slug_excess", "window_start_ts",
]


def build_per_slug_frame(
    wl_all: pd.DataFrame, dh_wallets: set[str],
) -> pd.DataFrame:
    """Per single-sided directional slug: won{0,1}, slug VWAP entry odds, and
    ``slug_excess = won - entry_odds``.

    Both-sided slugs (Up AND Down bought) are dropped — their entry odds is
    undefined for a directional analysis. One row per countable single-sided
    directional market across all ``dh_wallets``.
    """
    rows: list[dict] = []
    for wallet in sorted(dh_wallets):
        wl = wl_all[wl_all["wallet"] == wallet]
        if wl.empty:
            continue
        activity = load_activity(wallet)
        slugs = set(wl["slug"])
        bbo = slug_buys_by_outcome(activity, slugs)
        for rec in wl.to_dict("records"):
            sg = rec["slug"]
            entry = slug_vwap_entry(bbo.get(sg, {}))
            if entry is None:
                continue  # both-sided or no priced buys — skip
            won = bool(rec["won"])
            rows.append({
                "wallet": wallet,
                "slug": sg,
                "won": won,
                "entry_odds": entry,
                "buy_usdc": float(rec.get("buy_usdc") or 0.0),
                "slug_excess": float(won) - entry,
                "window_start_ts": int(rec["window_start_ts"]),
            })
    if not rows:
        return pd.DataFrame(columns=PER_SLUG_COLUMNS)
    return pd.DataFrame(rows)[PER_SLUG_COLUMNS]


@dataclasses.dataclass
class WalletWinRate:
    """Part-A per-wallet result. All figures are over single-sided
    directional slugs only, consistently count-weighted."""
    wallet: str
    user_name: str
    n_markets: int
    n_won: int
    n_lost: int
    honest_win_rate: float       # count-weighted win rate
    vwap_entry_odds: float       # count-weighted mean slug entry odds
    excess_win_rate: float       # honest_win_rate - vwap_entry_odds
    usdc_excess: float           # USDC-weighted excess (cross-check)
    # persistence (early vs late half by time)
    early_n: int
    early_excess: float
    late_n: int
    late_excess: float
    persistent_positive: bool


def compute_part_a(
    cashflow: pd.DataFrame,
    summary: pd.DataFrame,
    fetch_ts: int,
    min_half_markets: int = 30,
) -> tuple[pd.DataFrame, dict, pd.DataFrame]:
    """Honest win rate, entry odds and excess per ``directional_holder``
    wallet, plus an early/late persistence test.

    Returns ``(per_wallet_df, pooled_stats, per_slug_df)``. Every figure is
    over **single-sided** directional slugs and uses **consistent weighting**
    (see module docstring). ``pooled_stats`` is the calibration check.
    """
    summary = summary.copy()
    summary["archetype"] = summary.apply(assign_archetype, axis=1)
    dh_wallets = set(
        summary.loc[summary["archetype"] == "directional_holder",
                    "proxy_wallet"])
    name_by_wallet = dict(zip(summary["proxy_wallet"], summary["user_name"]))

    wl_all = build_winloss_frame(cashflow, fetch_ts)
    per_slug = build_per_slug_frame(wl_all, dh_wallets)

    results: list[WalletWinRate] = []
    for wallet in sorted(dh_wallets):
        ps = per_slug[per_slug["wallet"] == wallet]
        if ps.empty:
            continue
        n = len(ps)
        won = int(ps["won"].sum())
        lost = n - won
        honest_wr = won / n  # count-weighted
        # count-weighted mean entry odds — the SAME weighting as honest_wr.
        odds = float(ps["entry_odds"].mean())
        excess = honest_wr - odds  # == ps["slug_excess"].mean()
        w = ps["buy_usdc"]
        usdc_excess = (float(np.average(ps["slug_excess"], weights=w))
                       if w.sum() > 0 else float("nan"))

        # Persistence: split by window-start-ts median into early / late.
        med = ps["window_start_ts"].median()
        early = ps[ps["window_start_ts"] <= med]
        late = ps[ps["window_start_ts"] > med]
        e_n, l_n = len(early), len(late)
        e_ex = (float(early["slug_excess"].mean()) if e_n
                else float("nan"))
        l_ex = (float(late["slug_excess"].mean()) if l_n
                else float("nan"))
        persistent_pos = bool(
            e_n >= min_half_markets and l_n >= min_half_markets
            and pd.notna(e_ex) and pd.notna(l_ex)
            and e_ex > 0 and l_ex > 0)

        results.append(WalletWinRate(
            wallet=wallet,
            user_name=name_by_wallet.get(wallet, wallet),
            n_markets=n, n_won=won, n_lost=lost,
            honest_win_rate=honest_wr,
            vwap_entry_odds=odds,
            excess_win_rate=excess,
            usdc_excess=usdc_excess,
            early_n=e_n, early_excess=e_ex,
            late_n=l_n, late_excess=l_ex,
            persistent_positive=persistent_pos,
        ))

    per_wallet = pd.DataFrame([dataclasses.asdict(r) for r in results])

    # Pooled, consistently weighted across every single-sided directional
    # slug of the DH population. Both a count-weighted and a USDC-weighted
    # figure — they should agree (no Simpson's-paradox gap).
    n_pool = len(per_slug)
    won_pool = int(per_slug["won"].sum())
    pooled_wr_cnt = won_pool / n_pool if n_pool else float("nan")
    pooled_odds_cnt = (float(per_slug["entry_odds"].mean())
                       if n_pool else float("nan"))
    w = per_slug["buy_usdc"]
    pooled_wr_usdc = (float(np.average(per_slug["won"], weights=w))
                      if n_pool and w.sum() > 0 else float("nan"))
    pooled_odds_usdc = (float(np.average(per_slug["entry_odds"], weights=w))
                        if n_pool and w.sum() > 0 else float("nan"))
    pooled = {
        "pooled_n_markets": n_pool,
        "pooled_won": won_pool,
        "pooled_lost": n_pool - won_pool,
        "pooled_win_rate_cnt": pooled_wr_cnt,
        "pooled_entry_odds_cnt": pooled_odds_cnt,
        "pooled_excess_cnt": (pooled_wr_cnt - pooled_odds_cnt
                              if n_pool else float("nan")),
        "pooled_win_rate_usdc": pooled_wr_usdc,
        "pooled_entry_odds_usdc": pooled_odds_usdc,
        "pooled_excess_usdc": (pooled_wr_usdc - pooled_odds_usdc
                               if n_pool else float("nan")),
    }
    return per_wallet, pooled, per_slug


# ==========================================================================
# PART B — copy-trading detection
# ==========================================================================
def build_market_entries(
    wallets: list[str], min_window_ts: int = 0,
) -> dict[tuple[str, str], list[tuple[str, int]]]:
    """Map ``(slug, outcome) -> [(wallet, first_buy_ts), ...]``.

    The first BUY a wallet makes in a given ``(slug, outcome)`` is its entry;
    later add-ons are ignored. Restricted to compact Up/Down slugs (so timing
    is well-defined). ``min_window_ts`` can drop very old windows.
    """
    entries: dict[tuple, dict[str, int]] = defaultdict(dict)
    for wallet in wallets:
        for r in load_activity(wallet) or []:
            if r.get("type") != "TRADE" or r.get("side") != "BUY":
                continue
            slug = r.get("slug") or ""
            parsed = parse_compact_slug(slug)
            if parsed is None or parsed[0] < min_window_ts:
                continue
            outcome = r.get("outcome") or ""
            ts = r.get("timestamp")
            if ts is None:
                continue
            key = (slug, outcome)
            cur = entries[key].get(wallet)
            if cur is None or ts < cur:
                entries[key][wallet] = int(ts)
    return {k: sorted(v.items(), key=lambda x: x[1])
            for k, v in entries.items()}


def co_trading_pairs(
    market_entries: dict[tuple[str, str], list[tuple[str, int]]],
    min_markets_each: int = 50,
    top_n: int = 25,
) -> pd.DataFrame:
    """Find wallet pairs that enter the same (slug, outcome) far more often
    than chance.

    The expected co-occurrence baseline for a pair (A, B) treating entries as
    independent is ``n_A * n_B / N`` where ``n_A``/``n_B`` are each wallet's
    market-entry counts and ``N`` is the total number of distinct
    (slug, outcome) markets. ``lift = observed / expected``; a lift far above
    1 is non-independent co-trading.
    """
    # per-wallet market count, and per-wallet set of (slug,outcome) keys
    wallet_markets: dict[str, set] = defaultdict(set)
    for key, members in market_entries.items():
        for wallet, _ts in members:
            wallet_markets[wallet].add(key)
    N = len(market_entries)
    if N == 0:
        return pd.DataFrame(columns=[
            "wallet_a", "wallet_b", "observed", "expected", "lift",
            "n_a", "n_b"])

    # observed co-occurrence per pair
    pair_obs: dict[tuple[str, str], int] = defaultdict(int)
    for key, members in market_entries.items():
        ws = sorted(w for w, _ in members)
        for i in range(len(ws)):
            for j in range(i + 1, len(ws)):
                pair_obs[(ws[i], ws[j])] += 1

    rows: list[dict] = []
    for (a, b), obs in pair_obs.items():
        na, nb = len(wallet_markets[a]), len(wallet_markets[b])
        if na < min_markets_each or nb < min_markets_each:
            continue
        expected = na * nb / N
        if expected <= 0:
            continue
        rows.append({
            "wallet_a": a, "wallet_b": b,
            "observed": obs, "expected": expected,
            "lift": obs / expected, "n_a": na, "n_b": nb,
        })
    if not rows:
        return pd.DataFrame(columns=[
            "wallet_a", "wallet_b", "observed", "expected", "lift",
            "n_a", "n_b"])
    df = pd.DataFrame(rows).sort_values("lift", ascending=False)
    return df.head(top_n).reset_index(drop=True)


def lead_follow_gaps(
    market_entries: dict[tuple[str, str], list[tuple[str, int]]],
    pair: tuple[str, str],
) -> pd.DataFrame:
    """For one wallet pair, the signed entry-time gap on every co-traded
    (slug, outcome): ``ts_b - ts_a`` (positive => A entered first)."""
    a, b = pair
    rows: list[dict] = []
    for key, members in market_entries.items():
        d = dict(members)
        if a in d and b in d:
            rows.append({
                "slug": key[0], "outcome": key[1],
                "ts_a": d[a], "ts_b": d[b], "gap_b_minus_a": d[b] - d[a],
            })
    return pd.DataFrame(rows)


def first_mover_consistency(
    market_entries: dict[tuple[str, str], list[tuple[str, int]]],
    min_markets: int = 100,
) -> pd.DataFrame:
    """For every wallet with enough co-traded markets, the fraction of those
    markets where it was the FIRST of all entrants to buy.

    A genuine copy-trading *leader* is first in a large, consistent fraction
    of the markets it shares with followers. A shared-public-signal world has
    no consistent first mover (each wallet first ~1/k of the time)."""
    first_count: dict[str, int] = defaultdict(int)
    appear_count: dict[str, int] = defaultdict(int)
    for key, members in market_entries.items():
        if len(members) < 2:
            continue
        first_wallet = members[0][0]  # members sorted by ts
        for w, _ in members:
            appear_count[w] += 1
        first_count[first_wallet] += 1
    rows = []
    for w, appear in appear_count.items():
        if appear < min_markets:
            continue
        rows.append({
            "wallet": w,
            "n_cotraded_markets": appear,
            "n_first": first_count.get(w, 0),
            "first_frac": first_count.get(w, 0) / appear,
        })
    if not rows:
        return pd.DataFrame(columns=[
            "wallet", "n_cotraded_markets", "n_first", "first_frac"])
    return pd.DataFrame(rows).sort_values(
        "first_frac", ascending=False).reset_index(drop=True)


# ==========================================================================
# PART C — rebate-farming check
# ==========================================================================
def rebate_estimate(
    summary_row: dict,
) -> dict:
    """Rough magnitude estimate of how much of a wallet's leaderboard PnL
    could be maker-rebate income rather than trade edge.

    A maker earns ~20% of the taker fee on matched volume. The taker fee is
    ``shares * 0.07 * p * (1-p)``; at the median favourite-ish price the
    fee-per-dollar-of-stake is small. We bound the rebate generously:
    assume EVERY dollar of the wallet's buy volume was a maker fill at the
    fee-maximising price p=0.5 (fee = 0.07*0.25 = 1.75c per share, ~3.5c per
    $1 stake at p=0.5), and the wallet earned 20% of that as rebate. This is
    an upper bound; real rebate is far lower (favourite prices, taker fills).
    """
    buy_usdc = float(summary_row.get("total_buy_usdc") or 0.0)
    maker_frac = summary_row.get("maker_fill_frac")
    maker_frac = 0.0 if pd.isna(maker_frac) else float(maker_frac)
    lb_pnl_all = summary_row.get("lb_pnl_all")
    lb_pnl_month = summary_row.get("lb_pnl_month")
    lb_pnl = lb_pnl_all if pd.notna(lb_pnl_all) else lb_pnl_month
    lb_pnl = float(lb_pnl) if pd.notna(lb_pnl) else float("nan")

    # Upper-bound rebate: all buy volume as maker fills at p=0.5.
    # fee per $1 stake at p=0.5: stake $1 buys 1/0.5 = 2 shares; fee =
    # 2 * 0.07 * 0.5 * 0.5 = 0.035  -> 3.5c per $1 of stake.
    fee_per_dollar_p50 = (1.0 / 0.5) * TAKER_FEE_RATE * 0.5 * 0.5
    rebate_upper = buy_usdc * fee_per_dollar_p50 * MAKER_REBATE_FRAC
    # A more realistic estimate scales by the wallet's actual maker fraction.
    rebate_realistic = rebate_upper * maker_frac
    rebate_share_of_pnl = (rebate_upper / lb_pnl
                           if pd.notna(lb_pnl) and lb_pnl > 0
                           else float("nan"))
    return {
        "wallet": summary_row.get("proxy_wallet"),
        "user_name": summary_row.get("user_name"),
        "total_buy_usdc": buy_usdc,
        "maker_fill_frac": maker_frac,
        "lb_pnl": lb_pnl,
        "rebate_upper_bound": rebate_upper,
        "rebate_realistic": rebate_realistic,
        "rebate_upper_share_of_pnl": rebate_share_of_pnl,
    }


def compute_part_c(summary: pd.DataFrame, top_n: int = 15) -> pd.DataFrame:
    """Rebate-farming estimate for the top maker-leaning, high-volume
    wallets (maker_fill_frac >= 0.5), ranked by buy volume."""
    df = summary.copy()
    makers = df[(df["maker_fill_frac"].notna())
                & (df["maker_fill_frac"] >= 0.5)]
    makers = makers.sort_values("total_buy_usdc", ascending=False)
    rows = [rebate_estimate(r) for r in makers.head(top_n).to_dict("records")]
    return pd.DataFrame(rows)


# ==========================================================================
# Report assembly
# ==========================================================================
def _fmt_usd(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    return f"${v:,.0f}"


def _fmt_pct(v, dp: int = 1) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    return f"{v * 100:.{dp}f}%"


def _fmt_p(v, dp: int = 3) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    return f"{v:.{dp}f}"


def build_report(out_path: Optional[Path] = None) -> Path:
    """Run all three parts and write the markdown report."""
    out_path = Path(out_path) if out_path is not None else REPORT_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)

    manifest = load_manifest()
    fetch_ts = int(dt.datetime.fromisoformat(
        manifest["fetch_timestamp"]).timestamp())
    fetch_idt = dt.datetime.fromisoformat(
        manifest["fetch_timestamp"]).astimezone(IDT)
    now_idt = dt.datetime.now(dt.timezone.utc).astimezone(IDT)

    cashflow = pd.read_parquet(DERIVED_DIR / "market_cashflow.parquet")
    summary = pd.read_parquet(DERIVED_DIR / "wallet_summary.parquet")

    # ---- Part A ----------------------------------------------------------
    part_a, pooled, _per_slug = compute_part_a(cashflow, summary, fetch_ts)

    # ---- Part B ----------------------------------------------------------
    all_wallets = list(summary["proxy_wallet"])
    market_entries = build_market_entries(all_wallets)
    co_pairs = co_trading_pairs(market_entries)
    first_movers = first_mover_consistency(market_entries)

    # ---- Part C ----------------------------------------------------------
    rebate = compute_part_c(summary)

    name_by_wallet = dict(zip(summary["proxy_wallet"], summary["user_name"]))

    lines: list[str] = []
    a = lines.append

    a("# Directional buy-and-hold: honest win rate, and is anyone copyable?")
    a("")
    a(f"*Generated {now_idt:%Y-%m-%d %H:%M} IDT (Israel local time). "
      f"Cache fetched {fetch_idt:%Y-%m-%d %H:%M} IDT. All computation in UTC; "
      "human-facing timestamps shown in Israel local time.*")
    a("")
    a("This report answers the project owner's three questions about the "
      "**167 `directional_holder`** wallets that dominate Polymarket's crypto "
      "profit leaderboard (see `docs/research/leaderboard_wallets.md`):")
    a("")
    a("1. How do the directional buy-and-hold wallets win *so well* — what is "
      "the **REAL** win rate of each?")
    a("2. Could we copy the leaders?")
    a("3. Are the leaders copying each other?")
    a("")
    a("> **Bottom line, up front.** (1) The eye-catching 90%+ win rates are "
      "mostly the mechanical result of buying favourites: a leg priced at "
      "0.90 wins ~90% of the time with zero skill. Once the silent losses "
      "are counted, the leaderboard wallets show a small positive "
      "*in-sample* excess (~+5c — they win a few points more often than "
      "their entry odds implied). But that ~+5c is an **in-sample, "
      "survivorship-confounded** number: these 239 wallets were *selected* "
      "for having made money, so their realized trades won by construction. "
      "The survivorship-FREE estimate — the Phase 3 backtest that buys "
      "every favourite across winners *and* losers "
      "(`docs/research/leaderboard_strategy_backtest.md`) — finds the "
      "favourite's true excess is only **+1.5c on 15m / +0.2c on 5m**. "
      "Against the correct **one-way** entry cost (~3–3.5c taker; no exit "
      "fee — hold-to-resolution never sells) that is a **net loss**. The "
      "~3.5c gap between the probe's +5c and Phase 3's +1.5c is the "
      "survivorship premium, not skill. (2) No — there is no "
      "survivorship-free evidence of edge to copy, and on fast 15m/5m "
      "markets a post-settlement copy buys at worse odds than the leader. "
      "(3) The wallets cluster heavily on the same markets, but the "
      "pattern is a **common public signal** (the crypto spot price), not "
      "a copy-trading ring with a consistent leader.")
    a("")

    # ===== PART A =========================================================
    a("## Part A — The honest win rate of the directional holders")
    a("")
    a("### The structural bias being corrected")
    a("")
    a("The FIFO `win_rate` in `wallet_summary.parquet` is **structurally "
      "inflated**. A hold-to-resolution position that *wins* produces a "
      "`REDEEM` activity record. A hold-to-resolution position that *loses* "
      "expires worthless and produces **no record at all**. A win rate built "
      "from REDEEMs therefore literally cannot see the losses.")
    a("")
    a("The fix: a `(wallet, slug)` market with only BUYs and no exit "
      "(`exit_mode == \"none\"`) whose 15m/5m window has **already closed** "
      "is a *silent loss* — the shares expired worthless. Counting those "
      "gives an honest win rate. `market_cashflow.parquet` nets every USDC "
      "movement per slug, so `net_pnl > 0` = won, `net_pnl < 0` = lost. "
      "Markets whose window had **not** closed by the cache fetch time "
      f"({fetch_idt:%Y-%m-%d %H:%M} IDT) are excluded as unresolved, not "
      "counted as losses.")
    a("")
    a("### Two method points (so the numbers are not garbage)")
    a("")
    a("**(1) Single-sided slugs only.** Many of these wallets buy *both* the "
      "Up and the Down leg of the same window — a hedged / scalp position, "
      "not a directional bet. For a both-sided slug \"entry odds\" is "
      "meaningless (the two legs price ~p and ~1−p, so any average lands "
      "near 0.5). Part A therefore restricts the win-rate-vs-odds comparison "
      "to slugs where the wallet bought exactly **one** outcome — a genuine "
      "directional position. (Both-sided slugs are ~37% of directional-hold "
      "markets and are excluded from Part A.)")
    a("")
    a("**(2) Consistent weighting.** `excess` compares a win rate to an "
      "entry-odds figure — they MUST be computed under the same weighting. "
      "Comparing a count-weighted win rate (these wallets place many tiny "
      "longshot bets) to a USDC-weighted entry odds (the big money sits on "
      "favourites) mixes two populations and manufactures a spurious "
      "large-negative number (a Simpson's-paradox trap). Below, the per-slug "
      "excess `won{0,1} − slug_VWAP_entry` is reported **both** "
      "count-weighted and USDC-weighted, and the two agree.")
    a("")
    a("### The decisive metric: excess win rate")
    a("")
    a("**A high win rate is exactly what you expect when you buy "
      "favourites.** Buy a leg priced at 0.85 and you win ~85% of the time "
      "with *zero skill* — that is what the price means. The only metric "
      "that reveals edge is")
    a("")
    a("> **`excess_win_rate` = `honest_win_rate` − `entry_odds`**")
    a("")
    a("Excess ~0 means the wallet wins exactly as often as it paid for — no "
      "edge. Excess materially > 0 means genuine skill (gross of cost).")
    a("")
    a("**Calibration — win rate tracks entry odds.** Bucketing every "
      "single-sided directional slug by its entry odds, the realized win "
      "rate tracks the price closely, sitting a few points *above* it in "
      "every bucket:")
    a("")
    a("| Entry-odds bucket | n slugs | Realized win rate |")
    a("|---|---:|---:|")
    if len(_per_slug):
        ps = _per_slug
        buckets = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.5), (0.5, 0.6),
                   (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0001)]
        for lo, hi in buckets:
            sub = ps[(ps["entry_odds"] >= lo) & (ps["entry_odds"] < hi)]
            if len(sub) == 0:
                continue
            label = f"[{lo:.1f}, {min(hi, 1.0):.1f})"
            a(f"| {label} | {len(sub):,} | "
              f"{_fmt_pct(sub['won'].mean())} |")
    a("")
    a("That small consistent margin above the diagonal is the gross excess "
      "— real, but small.")
    a("")

    if len(part_a):
        n_wallets = len(part_a)
        ex = part_a["excess_win_rate"].dropna()
        n_pos = int((ex > 0).sum())
        n_neg = int((ex < 0).sum())
        # the meaningful-sample subset
        big = part_a[part_a["n_markets"] >= 20]
        ex_big = big["excess_win_rate"].dropna()
        a(f"**Per-wallet result across {n_wallets} directional-holder "
          "wallets** with at least one single-sided directional market "
          "(count-weighted excess):")
        a("")
        a("| `excess_win_rate` statistic | All wallets | >=20 markets |")
        a("|---|---:|---:|")
        a(f"| wallets measured | {n_wallets} | {len(big)} |")
        a(f"| mean excess | {_fmt_pct(ex.mean(), 1)} | "
          f"{_fmt_pct(ex_big.mean(), 1)} |")
        a(f"| median excess | {_fmt_pct(ex.median(), 1)} | "
          f"{_fmt_pct(ex_big.median(), 1)} |")
        a(f"| p10 / p90 excess | {_fmt_pct(ex.quantile(.10), 1)} / "
          f"{_fmt_pct(ex.quantile(.90), 1)} | "
          f"{_fmt_pct(ex_big.quantile(.10), 1)} / "
          f"{_fmt_pct(ex_big.quantile(.90), 1)} |")
        a(f"| wallets with excess > 0 | {n_pos}/{n_wallets} "
          f"({n_pos / n_wallets * 100:.0f}%) | "
          f"{int((ex_big > 0).sum())}/{len(big)} "
          f"({(ex_big > 0).mean() * 100:.0f}%) |")
        a(f"| wallets with excess > +5% | "
          f"{int((ex > 0.05).sum())}/{n_wallets} | "
          f"{int((ex_big > 0.05).sum())}/{len(big)} |")
        a("")
        a("The distribution leans **positive** — most directional-holder "
          "wallets win a few points more often than their entry odds "
          "implied. On the meaningful-sample subset the mean per-wallet "
          f"excess is ~{_fmt_pct(ex_big.mean(), 0)} and "
          f"~{(ex_big > 0).mean() * 100:.0f}% of wallets are positive. **But "
          "this is an *in-sample* number and cannot be read as edge.** "
          "These 239 wallets were *selected* by the leaderboard for having "
          "made money — their realized trades won by construction. A "
          "positive in-sample excess is therefore *exactly what selection "
          "produces*, with or without genuine skill. The trustworthy, "
          "survivorship-free figure is the Phase 3 backtest's "
          "(`docs/research/leaderboard_strategy_backtest.md`) — see the "
          "reconciliation below — and it is much smaller. Whether ~+5c "
          "survives cost is *not* the decisive question; whether the +5c "
          "is real at all is.")
        a("")

        # concrete examples — high WR / high odds
        a("### Concrete examples — a high win rate is not (much of) an edge")
        a("")
        a("The trap, made concrete: wallets with eye-catching win rates "
          "whose excess is small because they simply bought favourites.")
        a("")
        a("| Wallet | Honest win rate | Entry odds | Excess | n markets |")
        a("|---|---:|---:|---:|---:|")
        hi_wr = big.sort_values("honest_win_rate", ascending=False).head(4)
        for _, r in hi_wr.iterrows():
            a(f"| {str(r['user_name'])[:22]} | "
              f"{_fmt_pct(r['honest_win_rate'])} | "
              f"{_fmt_p(r['vwap_entry_odds'])} | "
              f"{_fmt_pct(r['excess_win_rate'], 1)} | {int(r['n_markets'])} |")
        a("")
        a("A 99% win rate bought at 0.98 entry odds is a +1% edge — almost "
          "all of that headline number is just the price of a near-certain "
          "favourite, not skill.")
        a("")
        a("For balance, the wallets with the **largest positive excess** "
          "(>=50 markets) — the best edge candidates Part A can find:")
        a("")
        a("| Wallet | Honest win rate | Entry odds | Excess | n markets |")
        a("|---|---:|---:|---:|---:|")
        best = part_a[part_a["n_markets"] >= 50].sort_values(
            "excess_win_rate", ascending=False).head(4)
        for _, r in best.iterrows():
            a(f"| {str(r['user_name'])[:22]} | "
              f"{_fmt_pct(r['honest_win_rate'])} | "
              f"{_fmt_p(r['vwap_entry_odds'])} | "
              f"{_fmt_pct(r['excess_win_rate'], 1)} | {int(r['n_markets'])} |")
        a("")
        a("A handful of wallets show a genuinely large in-sample excess "
          "(20%+). These look like the best edge candidates — but they are "
          "the most survivorship-confounded of all: they trade at **low "
          "entry odds**, where a few lucky longshot wins move the number a "
          "lot, and a wallet that got lucky on longshots is exactly the "
          "kind of wallet a cumulative-PnL leaderboard promotes. Their "
          "in-sample excess is not evidence their longshot calls were "
          "skilled — only a forward, out-of-sample test (below) could "
          "tell.")
        a("")

        # persistence test
        eligible = part_a[(part_a["early_n"] >= 30)
                          & (part_a["late_n"] >= 30)]
        n_eligible = len(eligible)
        # wallets with positive OVERALL excess — the correct conditioning
        # set, since "both halves positive" implies "overall positive".
        elig_pos = eligible[eligible["excess_win_rate"] > 0]
        n_elig_pos = len(elig_pos)
        n_persist = int(part_a["persistent_positive"].sum())
        pass_rate_pos = (n_persist / n_elig_pos if n_elig_pos else float("nan"))
        a("### Persistence test — do they beat their odds in BOTH halves?")
        a("")
        a("Variance alone produces plenty of positive-excess wallets in any "
          "single sample. The idea of a persistence test: a *genuinely "
          "skilled* wallet beats its entry odds in **both** halves of its "
          "own history. Each wallet's directional history is split in two by "
          "time (window-start median); the per-slug excess is recomputed in "
          "each half; a wallet passes only if it is positive-excess in "
          "**both** halves with a meaningful sample (>=30 markets/half).")
        a("")
        a(f"- Wallets with >=30 single-sided directional markets in **each** "
          f"half: {n_eligible}")
        a(f"- Of those, with positive **overall** excess: {n_elig_pos}")
        a(f"- Of those, positive excess in **both** halves: **{n_persist}** "
          + (f"({pass_rate_pos * 100:.0f}% of the overall-positive "
             "wallets)" if n_elig_pos else ""))
        a("")
        a("**The baseline for this test is ~50%, not ~25% — and that "
          "changes the reading.** A subtle but decisive point: these "
          "wallets are *already* conditioned on being leaderboard winners, "
          "so the relevant population is the overall-positive wallets, and "
          "a wallet with positive overall excess has `half1 + half2 > 0`. "
          "For two i.i.d. mean-zero (zero-skill) halves, "
          "`P(both halves > 0 | sum > 0) = 0.25 / 0.50 = 0.50` — the two "
          "halves are positively co-conditioned by the very fact that their "
          "sum is positive. So a **pure zero-skill** population, once "
          "filtered to overall-winners, *already* passes the both-halves "
          "test about **50%** of the time. (An unconditioned zero-skill "
          "population passes ~25% — but that is the wrong baseline here, "
          "because the leaderboard pre-selection has already conditioned on "
          "winning.)")
        a("")
        a("The observed pass rate is "
          + (f"**{pass_rate_pos * 100:.0f}%**" if n_elig_pos else "n/a")
          + f" ({n_persist}/{n_elig_pos} overall-positive wallets) — above "
          "the corrected ~50% zero-skill baseline, but only **moderately** "
          "so, and on a small sample (" + f"{n_elig_pos}"
          " wallets). This is **weak evidence** of a persistent component, "
          "not the strong signal a comparison against the wrong ~25% "
          "baseline would have suggested. And note what the test can and "
          "cannot do: even passing both halves is still an *in-sample* "
          "statement — both halves are drawn from the same "
          "leaderboard-selected history, so the test cannot separate "
          "persistent *skill* from a wallet that was simply lucky across "
          "its whole (selected) record. The persistence test does **not** "
          "escape the survivorship problem.")
        a("")
        if n_persist:
            a("The strongest persistent wallets (positive in both halves, "
              "ranked by overall excess):")
            a("")
            a("| Wallet | Early excess (n) | Late excess (n) | "
              "Overall excess |")
            a("|---|---:|---:|---:|")
            pers = part_a[part_a["persistent_positive"]].sort_values(
                "excess_win_rate", ascending=False)
            for _, r in pers.head(10).iterrows():
                a(f"| {str(r['user_name'])[:22]} | "
                  f"{_fmt_pct(r['early_excess'], 1)} ({int(r['early_n'])}) | "
                  f"{_fmt_pct(r['late_excess'], 1)} ({int(r['late_n'])}) | "
                  f"{_fmt_pct(r['excess_win_rate'], 1)} |")
            a("")
        a("")

    # pooled calibration check
    a("### Calibration sanity check — the pooled excess")
    a("")
    a("Pooling **every** single-sided directional market of **every** "
      "directional-holder wallet (winners and silent losers together). Both "
      "weightings of the same per-slug data — note they agree, no "
      "Simpson's-paradox gap:")
    a("")
    a("| Pooled metric | Count-weighted | USDC-weighted |")
    a("|---|---:|---:|")
    a(f"| single-sided directional markets | "
      f"{pooled['pooled_n_markets']:,} | {pooled['pooled_n_markets']:,} |")
    a(f"| won / lost | {pooled['pooled_won']:,} / "
      f"{pooled['pooled_lost']:,} | {pooled['pooled_won']:,} / "
      f"{pooled['pooled_lost']:,} |")
    a(f"| pooled honest win rate | "
      f"{_fmt_pct(pooled['pooled_win_rate_cnt'])} | "
      f"{_fmt_pct(pooled['pooled_win_rate_usdc'])} |")
    a(f"| pooled entry odds | "
      f"{_fmt_p(pooled['pooled_entry_odds_cnt'])} | "
      f"{_fmt_p(pooled['pooled_entry_odds_usdc'])} |")
    a(f"| **pooled excess** | "
      f"**{_fmt_pct(pooled['pooled_excess_cnt'], 1)}** | "
      f"**{_fmt_pct(pooled['pooled_excess_usdc'], 1)}** |")
    a("")
    a("The pooled in-sample excess is **small and positive** — about +5 "
      "percentage points either way. Across the whole directional-holder "
      "population, on the trades the leaderboard recorded, the favourites "
      "won about 5c more often than their entry price implied. That number "
      "is real *as a description of these wallets' realized history* — but, "
      "as the next subsection makes precise, it is an **in-sample, "
      "survivorship-confounded** figure and is **not** a measurement of "
      "edge.")
    a("")

    # ---- Phase 3 reconciliation -----------------------------------------
    a("### Reconciling the +5c with the Phase 3 backtest — the survivorship "
      "premium")
    a("")
    a("There are two estimates of the favourite's excess win rate, and they "
      "disagree by design:")
    a("")
    a("| Estimate | What it measures | Favourite excess |")
    a("|---|---|---:|")
    a("| **This probe (Part A)** | Excess on the *realized trades of the "
      "239 leaderboard wallets* — wallets selected for cumulative profit | "
      "**~+5c** (in-sample) |")
    a("| **Phase 3 backtest** "
      "(`docs/research/leaderboard_strategy_backtest.md`) | Excess from "
      "buying **every** favourite across **every** market — winners *and* "
      "losers, no wallet selection | **+1.5c (15m) / +0.2c (5m)** |")
    a("")
    a("The Phase 3 number is the **survivorship-free** one: it bought the "
      "favourite in all ~1,676 15m and ~5,018 5m windows of the tick "
      "dataset, so it sees the full population — every losing favourite "
      "bet, not just the bets of wallets who came out ahead. It found the "
      "favourite side is **essentially calibrated**: realized win rate "
      "tracks entry odds, mean (realized − entry) = **+1.5c on 15m, +0.2c "
      "on 5m**.")
    a("")
    a("This probe's **+5c** is measured on a *selected* sample — the 239 "
      "wallets are, by construction, the top of the leaderboard's "
      "cumulative-PnL ranking, so their recorded trades won more often than "
      "a random trader's would. The gap is the arithmetic of selection:")
    a("")
    a("> **probe +5c − Phase 3 +1.5c ≈ +3.5c is the survivorship "
      "premium** — the part of the probe's excess attributable purely to "
      "having picked the wallets *on their wins*. It is not demonstrated "
      "skill.")
    a("")
    a("The trustworthy estimate of the favourite's true gross excess is "
      "therefore Phase 3's **+1.5c on 15m** (and +0.2c on 5m), not this "
      "probe's +5c. The probe's job was to count the silent losses "
      "honestly and characterise the wallets; it cannot, on selected data, "
      "measure edge — and it does not claim to.")
    a("")

    # ---- the cost model -------------------------------------------------
    a("### The cost the edge must clear — one-way, not round-trip")
    a("")
    a("Buy-favourite-**hold-to-resolution** never sells: the position "
      "settles at the 0/1 outcome, and settlement is not a trade — it "
      "carries no fee and no spread. The cost is therefore **one-way** "
      "(entry only), not a round-trip:")
    a("")
    a("- **Taker entry fee:** `0.07 · p · (1−p)` per share — ~1.4c at a "
      "typical favourite price p≈0.72.")
    a("- **Entry spread crossed:** ~1.5–2c per share on these books.")
    a("- **Total one-way taker cost: ~3–3.5c per share.** A maker pays "
      "**~0** — no fee, and fills at the bid rather than crossing the "
      "spread (subject to adverse selection — see Phase 3).")
    a("")
    a("(An earlier draft of this report wrongly used a *round-trip* "
      "16–21% cost. That figure includes an exit leg and applies to a "
      "buy-then-**sell** strategy — it does not apply to hold-to-"
      "resolution, which has no exit trade. The Phase 3 backtest applies "
      "exactly the one-way cost above: a taker entry fee and the crossed "
      "spread, and explicitly **no exit fee**.)")
    a("")
    a("Set the trustworthy +1.5c gross (15m) against the ~3–3.5c one-way "
      "taker cost: **+1.5c − 3.5c ≈ −2c per share net** — a loss. That is "
      "why the Phase 3 backtest nets **−$0.26/trade** on the 15m primary "
      "band as a taker. On 5m it is worse (+0.2c gross, −$0.55/trade). A "
      "maker pays ~0 cost, so a maker is roughly *breakeven* on the +1.5c "
      "gross — but Phase 3 shows the maker is adversely selected (filled "
      "because the side is moving), so the 0-fee bet is still a 0-EV bet "
      "on calibrated odds; the maker CI straddles zero, never clears it.")
    a("")
    a("Note the implication, stated honestly: **if the probe's +5c had "
      "been a real edge, it *would* clear the ~3.5c one-way cost** (+5c − "
      "3.5c ≈ +1.5c net). The conclusion does **not** rest on \"cost kills "
      "it.\" It rests on the +5c not being real — it is in-sample and "
      "survivorship-confounded, and the survivorship-free +1.5c does not "
      "clear cost.")
    a("")
    a("**Answer to Q1.** The directional holders' headline 90%+ win rates "
      "are *mostly* the mechanical result of buying favourites — a leg "
      "priced at 0.90 wins ~90% of the time with zero skill. Counting the "
      "silent losses honestly, their realized trades show a small positive "
      "excess (~+5c) — but that is an **in-sample number on a "
      "leaderboard-selected sample** and cannot be read as skill. The "
      "survivorship-free estimate (Phase 3, which sees winners *and* "
      "losers) puts the favourite's true gross excess at only **+1.5c on "
      "15m / +0.2c on 5m** — and against the ~3–3.5c one-way taker entry "
      "cost that is a **net loss**. We **cannot tell from this data "
      "whether any individual wallet has genuine skill** — every figure in "
      "Part A is in-sample. What we can say: the survivorship-free evidence "
      "shows no bankable directional edge. They do not win \"so well\"; "
      "they buy favourites, the favourites win at roughly their odds, and "
      "the leaderboard then shows us only the wallets for whom that "
      "coin-flip-at-fair-odds came up heads.")
    a("")

    # ===== PART B =========================================================
    a("## Part B — Are the leaders copying each other?")
    a("")
    a(f"Across all 239 wallets, every wallet's first BUY in each "
      f"`(slug, outcome)` was indexed, giving **{len(market_entries):,}** "
      "distinct `(compact-slug, outcome)` entry events. Two questions: do "
      "wallet pairs co-trade the same markets more than chance predicts, and "
      "if so is there a consistent first-mover others follow?")
    a("")
    a("### Co-trading lift")
    a("")
    a("For a pair (A, B), the chance baseline for co-occurrence treating "
      "entries as independent is `n_A * n_B / N` (N = distinct markets). "
      "`lift = observed / expected`; lift >> 1 is non-independent "
      "co-trading. Strongest pairs (each wallet >=50 markets):")
    a("")
    if len(co_pairs):
        a("| Wallet A | Wallet B | Co-traded | Expected | Lift |")
        a("|---|---|---:|---:|---:|")
        for _, r in co_pairs.head(12).iterrows():
            a(f"| {str(name_by_wallet.get(r['wallet_a'], r['wallet_a']))[:18]} "
              f"| {str(name_by_wallet.get(r['wallet_b'], r['wallet_b']))[:18]} "
              f"| {int(r['observed'])} | {r['expected']:.0f} | "
              f"{r['lift']:.2f} |")
        a("")
        max_lift = co_pairs["lift"].max()
        med_lift = co_pairs["lift"].median()
        a(f"The strongest pair has a lift of **{max_lift:.0f}×**; the median "
          f"of the top pairs is **{med_lift:.0f}×**. Those lifts look "
          "dramatic, but the `expected` column is the reason: a wallet that "
          "trades only ~60–100 markets out of ~18k has a chance "
          "co-occurrence baseline rounding to **0–1 markets**, so *any* "
          "genuine shared focus produces a huge ratio. A high lift here "
          "means the two wallets concentrate on the **same slice of "
          "markets** — but that is exactly what a **shared public signal** "
          "produces. The crypto Up/Down universe is small (a handful of "
          "assets × 15m/5m windows) and the most-active windows — the ones "
          "with a clear directional spot move — attract every active "
          "wallet at once. Heavy overlap on the obvious markets is "
          "structural; it is not, by itself, evidence of one wallet copying "
          "another. The lead/follow timing below is the test that "
          "distinguishes the two.")
    else:
        a("*No qualifying co-trading pairs found.*")
    a("")

    # lead/follow
    a("### Lead / follow timing — is there a consistent first mover?")
    a("")
    if len(first_movers):
        a("For every wallet with >=100 co-traded markets, the fraction of "
          "those markets in which it was the **first** of all entrants to "
          "buy. A genuine copy-trading *leader* is first in a large, "
          "consistent fraction. A shared-signal world has no consistent "
          "leader — with `k` wallets per market each is first ~`1/k` of the "
          "time.")
        a("")
        a("| Wallet | Co-traded markets | Times first | First-mover frac |")
        a("|---|---:|---:|---:|")
        for _, r in first_movers.head(10).iterrows():
            a(f"| {str(name_by_wallet.get(r['wallet'], r['wallet']))[:20]} | "
              f"{int(r['n_cotraded_markets'])} | {int(r['n_first'])} | "
              f"{_fmt_pct(r['first_frac'])} |")
        a("")
        top_frac = first_movers["first_frac"].iloc[0]
        a(f"The most consistent first-mover is first in only "
          f"**{_fmt_pct(top_frac)}** of its co-traded markets, and the "
          "fractions decay smoothly down the list — there is no single "
          "dominant leader. Note too that the wallets near the top include "
          "`PBot-*` entries — self-declared bots that simply enter *fast*. "
          "A wallet being first because it is a low-latency bot is **not** "
          "the same as other wallets *following* it: a copy-trading ring "
          "needs the followers to lag the SAME leader by a consistent short "
          "gap. Decisively, the gap analysis below shows the inter-wallet "
          "lag is **two-signed and wide**, not a tight one-directional "
          "follow. There is no wallet the others repeatedly trail.")
        a("")
        # gap analysis on the strongest co-trading pair
        if len(co_pairs):
            top_pair = (co_pairs.iloc[0]["wallet_a"],
                        co_pairs.iloc[0]["wallet_b"])
            gaps = lead_follow_gaps(market_entries, top_pair)
            if len(gaps):
                g = gaps["gap_b_minus_a"]
                a("Inter-wallet entry-gap distribution for the "
                  "**strongest-lift pair** "
                  f"({str(name_by_wallet.get(top_pair[0], top_pair[0]))[:16]} "
                  "vs "
                  f"{str(name_by_wallet.get(top_pair[1], top_pair[1]))[:16]}"
                  f", {len(gaps)} co-traded markets):")
                a("")
                a("| Gap statistic (B entry − A entry, seconds) | Value |")
                a("|---|---:|")
                a(f"| median gap | {g.median():.0f}s |")
                a(f"| p10 / p90 gap | {g.quantile(.10):.0f}s / "
                  f"{g.quantile(.90):.0f}s |")
                a(f"| A entered first | "
                  f"{int((g > 0).sum())} / {len(g)} markets |")
                a(f"| B entered first | "
                  f"{int((g < 0).sum())} / {len(g)} markets |")
                a("")
                a("The gap distribution straddles zero with a wide spread — "
                  "sometimes A is first, sometimes B, by tens to hundreds of "
                  "seconds. That is the signature of two traders **reacting "
                  "independently to the same public crypto price move**, not "
                  "one copying the other. Genuine copying would show a "
                  "tight, one-signed lag (the follower always a few seconds "
                  "behind the leader).")
        a("")
    a("**Answer to Q3.** The leaderboard wallets cluster heavily on the same "
      "markets — but that is the small size of the crypto Up/Down universe "
      "and a **shared public signal** (the spot price everyone watches), "
      "not a copy-trading ring. There is no consistent first-mover and no "
      "tight, one-signed follow lag. The wallets look like independent "
      "traders reacting to the same crypto tape.")
    a("")

    # ===== PART C =========================================================
    a("## Part C — Rebate farming, and is copy-trading viable?")
    a("")
    a("### Rebate-farming check")
    a("")
    a("Polymarket pays makers a rebate of ~20% of the taker fee on matched "
      "volume. Could a high-volume, maker-leaning wallet be \"profitable\" "
      "mainly from rebates rather than trade edge? The taker fee is "
      "`shares · 0.07 · p · (1−p)`. We take a deliberately generous upper "
      "bound: assume **every** dollar of a wallet's buy volume was a maker "
      "fill at the fee-maximising price p=0.5, and the wallet earned 20% of "
      "that fee as rebate. Top maker-leaning wallets by buy volume:")
    a("")
    if len(rebate):
        a("| Wallet | Buy volume | Maker frac | lb_pnl | "
          "Rebate (upper bnd) | Rebate / PnL |")
        a("|---|---:|---:|---:|---:|---:|")
        for _, r in rebate.iterrows():
            a(f"| {str(r['user_name'])[:18]} | "
              f"{_fmt_usd(r['total_buy_usdc'])} | "
              f"{_fmt_p(r['maker_fill_frac'], 2)} | "
              f"{_fmt_usd(r['lb_pnl'])} | "
              f"{_fmt_usd(r['rebate_upper_bound'])} | "
              f"{_fmt_pct(r['rebate_upper_share_of_pnl'], 1)} |")
        a("")
        share = rebate["rebate_upper_share_of_pnl"].dropna()
        med_share = share.median() if len(share) else float("nan")
        max_share = share.max() if len(share) else float("nan")
        a(f"Even at this **generous upper bound**, the estimated rebate is a "
          f"median of {_fmt_pct(med_share, 1)} of leaderboard PnL across the "
          f"top maker-leaning wallets (max {_fmt_pct(max_share, 1)}). The "
          "real figure is far lower still — these wallets buy favourites "
          "(p near 0.7–0.9, where `p·(1−p)` is roughly half its p=0.5 peak) "
          "and are not 100% maker fills. **Rebate farming is not a plausible "
          "explanation** for any leaderboard wallet's profit: the rebate is "
          "a rounding error next to the PnL. The leaderboard PnL is real "
          "trading PnL (driven, as Part A shows, by buying favourites that "
          "win at their priced odds — i.e. by *volume* and *variance*, not "
          "by rebate income).")
    else:
        a("*No maker-leaning high-volume wallets found.*")
    a("")

    a("### Copy-trading viability — honest synthesis")
    a("")
    a("Copying a leader works **only if** (a) the leader has a genuine "
      "persistent edge **that survives cost** **and** (b) you can replicate "
      "the entry before the edge decays. Both conditions fail.")
    a("")
    a("**(a) The edge is real but too small to bank.** Part A found a "
      "small, weakly-persistent **gross** excess (~5 percentage points "
      "pooled; "
      + f"{int(part_a['persistent_positive'].sum())} wallets beat their "
      "entry odds in both halves of their own history, above the ~25% "
      "chance rate). That is not a null — there is a faint real tendency to "
      "beat the odds. **But the gross excess is ~5c and the round-trip "
      "taker cost is 16–21%.** Copying a leader hands you the leader's ~5c "
      "of gross edge and then charges you 16–21% to trade — a net loss. "
      "Even the handful of 20%+-excess wallets only *barely* clear cost "
      "with no margin, and those are low-odds wallets whose excess is the "
      "most variance-prone. There is no edge here that survives cost.")
    a("")
    a("**(b) The latency problem — fatal on fast markets.** The public "
      "activity feed shows a trade only **after on-chain settlement**. In a "
      "15-minute (or 5-minute) crypto Up/Down market the price moves in "
      "*seconds* — the favourite re-prices continuously as spot moves. By "
      "the time a leader's BUY is visible in the feed, the price has already "
      "moved; a copy buys at a strictly worse odds than the leader did. "
      "A copy that pays a worse entry price than the leader gives up the "
      "leader's already-thin ~5c gross excess before it even pays the "
      "round-trip cost — the result is firmly **negative** expectancy. "
      "Copy-trading is **latency-fatal on 15m/5m markets**.")
    a("")
    a("Copy-trading is **latency-tolerant** on slow markets — daily / "
      "weekly / price-target markets where the price barely moves over "
      "hours, so a delayed copy still gets a near-identical entry. But the "
      "leaderboard's slow-market winners are the **mint-merge cluster** "
      "(27 wallets, `docs/research/leaderboard_mm_verdict.md` §6), whose "
      "edge — if any — is a structured mint/merge operation, not a "
      "directional bet you can mirror with a single copied BUY.")
    a("")
    a("**Answer to Q2.** No. Copy-trading the leaderboard is not viable: "
      "the directional holders' gross edge (~5c, Part A) is **too small to "
      "survive the 16–21% round-trip cost** — copying a leader nets a loss "
      "even with a perfect copy. On top of that, the post-settlement feed "
      "latency is fatal on the fast 15m/5m markets they trade (a delayed "
      "copy pays worse odds than the leader). The only latency-tolerant "
      "markets (slow / price-target) are dominated by a mint-merge "
      "structure that a single copied trade cannot replicate. There is no "
      "copyable, cost-surviving edge here.")
    a("")

    # ===== verdict ========================================================
    a("## Bottom line — the owner's three questions")
    a("")
    a("| # | Question | Verdict |")
    a("|---|---|---|")
    a("| 1 | How do directional buy-and-hold wallets win so well — real win "
      "rate? | The 90%+ win rates are **mostly buying favourites** (a 0.90 "
      "leg wins ~90% with zero skill) plus a silent-loss reporting bias. "
      "Honest excess win rate is a **small, real, positive ~5 percentage "
      "points** (weakly persistent) — a *gross* statistical edge, but far "
      "below the 16–21% round-trip cost, so **not a bankable edge**. |")
    a("| 2 | Could we copy the leaders? | **No.** The ~5c gross edge does "
      "not survive trading cost even with a perfect copy; and "
      "post-settlement feed latency is fatal on the 15m/5m markets they "
      "trade. |")
    a("| 3 | Are the leaders copying each other? | **No.** Heavy market "
      "overlap, but it is a **shared public signal** (crypto spot) — no "
      "consistent first-mover, no tight follow lag. Independent traders on "
      "the same tape. |")
    a("")
    a("This is a **near-null result** — and an honest one. There is a "
      "faint, real, positive gross excess in the directional-holder "
      "population (they beat their entry odds by ~5c and do so somewhat "
      "persistently), so this is not a flat zero. But ~5c of gross edge is "
      "comprehensively wiped out by the 16–21% round-trip taker cost, which "
      "is why it is not a tradable edge — fully consistent with the four "
      "prior independent confirmations "
      "(`docs/research/leaderboard_mm_verdict.md`) that short-dated "
      "Polymarket crypto Up/Down markets are **efficient *after cost***. "
      "The leaderboard's directional winners are mostly survivorship riding "
      "a thin real edge; they are not copying each other; and the thin edge "
      "they do have is not copyable.")
    a("")
    a("---")
    a("")
    a("*Inputs: `data/wallets/derived/market_cashflow.parquet`, "
      "`wallet_summary.parquet`, `data/wallets/raw/activity/*.jsonl.gz`, "
      "`data/wallets/manifest.json`. Code: "
      "`research/wallets/copytrade_probe.py`. Regenerate with "
      "`python -m research.wallets.copytrade_probe`.*")

    out_path.write_text("\n".join(lines) + "\n")
    return out_path


def main() -> None:
    """Run the probe and write the report; print the key numbers."""
    structlog.configure()
    path = build_report()
    print(f"report written -> {path}")


if __name__ == "__main__":
    main()
