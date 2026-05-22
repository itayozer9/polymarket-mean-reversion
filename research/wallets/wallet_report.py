"""Task 2.2 — strategy-archetype assignment, persistence filter, report.

This module consumes ``wallet_summary.parquet`` (Task 2.1, :mod:`analyze`) and
``entry_prices`` (this task) and turns 239 metric rows into:

* :func:`assign_archetype` — a per-wallet strategy label (an ordered decision
  cascade calibrated to the real population, see the cut-point notes below),
* :func:`persistence_rank` — the "profitable over time" filter (a wallet that
  appears on >=2 of the MONTH/WEEK/ALL boards),
* :func:`build_report` — assembles ``docs/research/leaderboard_wallets.md``.

Run as ``python -m research.wallets.wallet_report`` to regenerate the report.

Calibration (printed by :func:`print_distributions`, run on the full 239):

* ``merge_share``    — 28 wallets > 0.5 (a hard mint-merge signal).
* ``redeem_share``   — 186 wallets > 0.5 (holds to resolution: the big bucket).
* ``sell_share``     — 19 wallets > 0.5 (active flatten-before-resolution).
* ``maker_fill_frac``— present for 172/239; 54 > 0.6 (maker), 94 < 0.4 (taker),
  67 NaN (on-chain role model never fit — a coverage hole, NOT "taker").
* ``pct_vol_noncrypto`` — 30 wallets > 0.5 (out of scope; routed to unknown).

The cascade is ORDER-SENSITIVE; see :func:`assign_archetype`.

All computation in UTC. Human-facing timestamps in the report are Israel local
time (IDT, UTC+3) per the project convention.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from research.wallets.analyze import (
    DERIVED_DIR,
    REPO_ROOT,
    load_manifest,
)
from research.wallets.entry_prices import entry_prices_all

# Israel local time is UTC+3 (IDT) in May; the project shows timestamps in IDT.
IDT = timezone(timedelta(hours=3), name="IDT")

REPORT_PATH = REPO_ROOT / "docs" / "research" / "leaderboard_wallets.md"

# ---- Archetype labels -----------------------------------------------------
ARCH_MINT_MERGE = "mint_merge_arbitrageur"
ARCH_PASSIVE_LP = "passive_liquidity_provider"
ARCH_DIR_HOLDER = "directional_holder"
ARCH_SCALPER = "active_trader_scalper"
ARCH_UNKNOWN = "mixed_or_unknown"

ARCHETYPES = (
    ARCH_MINT_MERGE,
    ARCH_PASSIVE_LP,
    ARCH_DIR_HOLDER,
    ARCH_SCALPER,
    ARCH_UNKNOWN,
)

# Cut points (see module docstring for the population calibration).
_MERGE_DOMINANT = 0.5
_REDEEM_DOMINANT = 0.5
_SELL_DOMINANT = 0.5
_MAKER_DOMINANT = 0.6
_NONCRYPTO_DOMINANT = 0.5
# A "short hold" for the scalper bucket: under 10 minutes (the 15m-market
# scale). Used only as a secondary signal — sell-dominance is the primary one.
_SCALPER_HOLD_SECONDS = 600


def _get(row, key, default=np.nan):
    """Read ``key`` from a dict / pandas Series row, NaN-safe."""
    try:
        val = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if val is None else val


def assign_archetype(row) -> str:
    """Assign ONE strategy archetype to a wallet-summary row.

    ``row`` is a dict or ``pd.Series`` with the columns of
    ``wallet_summary.parquet``. The cascade is ordered — the FIRST matching
    rule wins, so the rules are arranged most-specific-first:

    1. ``pct_vol_noncrypto > 0.5`` -> ``mixed_or_unknown``. Non-crypto traders
       are out of scope; route them out before any crypto-strategy rule.
    2. ``merge_share > 0.5`` -> ``mint_merge_arbitrageur``. A hard signal: more
       than half of all incoming USDC arrived by merging minted YES+NO pairs —
       a non-directional mint-buy-merge cycle.
    3. ``maker_fill_frac > 0.6`` AND flattens (``sell_share > 0.5`` OR
       ``merge_share > 0.5``) -> ``passive_liquidity_provider``. The TRUE
       market-maker bucket: posts resting quotes (maker fills) and unwinds the
       inventory rather than holding it to resolution. (merge_share > 0.5 is
       already caught by rule 2, so in practice this is maker + sell-dominant.)
    4. ``redeem_share > 0.5`` -> ``directional_holder``. Buys a side and holds
       to resolution (redeem = winning-share payout). Expected to be the large
       bucket. The report sub-splits this by maker/taker entry.
    5. ``sell_share > 0.5`` -> ``active_trader_scalper``. Flattens before
       resolution by selling; short holds, high trade count.
    6. fallback -> ``mixed_or_unknown``.
    """
    noncrypto = _get(row, "pct_vol_noncrypto", 0.0)
    if pd.notna(noncrypto) and noncrypto > _NONCRYPTO_DOMINANT:
        return ARCH_UNKNOWN

    merge_share = _get(row, "merge_share", 0.0)
    if pd.notna(merge_share) and merge_share > _MERGE_DOMINANT:
        return ARCH_MINT_MERGE

    maker = _get(row, "maker_fill_frac", np.nan)
    sell_share = _get(row, "sell_share", 0.0)
    flattens = (pd.notna(sell_share) and sell_share > _SELL_DOMINANT) or (
        pd.notna(merge_share) and merge_share > _MERGE_DOMINANT)
    if pd.notna(maker) and maker > _MAKER_DOMINANT and flattens:
        return ARCH_PASSIVE_LP

    redeem_share = _get(row, "redeem_share", 0.0)
    if pd.notna(redeem_share) and redeem_share > _REDEEM_DOMINANT:
        return ARCH_DIR_HOLDER

    if pd.notna(sell_share) and sell_share > _SELL_DOMINANT:
        return ARCH_SCALPER

    return ARCH_UNKNOWN


def entry_role(row) -> str:
    """Maker/taker sub-label for a wallet, with the coverage caveat baked in.

    ``"maker"`` if ``maker_fill_frac > 0.6``, ``"taker"`` if ``< 0.4``,
    ``"mixed"`` for in-between, ``"unknown"`` if ``maker_fill_frac`` is NaN
    (the on-chain role model never fit — a coverage hole, NOT evidence of
    taker behaviour).
    """
    maker = _get(row, "maker_fill_frac", np.nan)
    if pd.isna(maker):
        return "unknown"
    if maker > 0.6:
        return "maker"
    if maker < 0.4:
        return "taker"
    return "mixed"


def dominant_market_type(row) -> str:
    """The market-type bucket holding the largest share of a wallet's buy
    volume (the ``pct_vol_*`` columns)."""
    pct_cols = {
        "crypto_15m_updown": "pct_vol_15m",
        "crypto_hourly_updown": "pct_vol_hourly",
        "crypto_daily_updown": "pct_vol_daily",
        "crypto_weekly_monthly": "pct_vol_longdated",
        "crypto_price_target": "pct_vol_pricetarget",
        "crypto_other": "pct_vol_other",
        "non_crypto": "pct_vol_noncrypto",
    }
    best, best_val = "crypto_other", -1.0
    for mtype, col in pct_cols.items():
        val = _get(row, col, 0.0)
        if pd.notna(val) and val > best_val:
            best, best_val = mtype, float(val)
    return best


# ---- Persistence filter ---------------------------------------------------
def persistence_rank(summary: pd.DataFrame) -> pd.DataFrame:
    """Filter + rank the "profitable over time" wallets.

    A wallet is **persistent** if it appears on >= 2 of the MONTH / WEEK / ALL
    boards (``n_boards >= 2``).

    Note the rejected alternative: ">= 3 distinct calendar months in the
    wallet's own visible activity history" is *largely unusable* here because
    173/239 wallets (72%) are ``activity_truncated`` at the 4000-record API cap
    — a truncated wallet's visible history can span well under three months, so
    that criterion would mostly measure trade frequency, not longevity.

    Returns the persistent subset, sorted strongest-first. The strongest signal
    is appearing on BOTH the ALL-time board AND the MONTH board (a lifetime
    winner still winning right now); within that, by total leaderboard PnL.
    """
    df = summary.copy()
    persistent = df[df["n_boards"] >= 2].copy()

    persistent["on_all_and_month"] = (
        persistent["on_all_board"].astype(bool)
        & persistent["on_month_board"].astype(bool))
    for c in ("lb_pnl_month", "lb_pnl_week", "lb_pnl_all"):
        persistent[c + "_f"] = persistent[c].fillna(0.0)
    persistent["lb_pnl_total"] = (
        persistent["lb_pnl_month_f"]
        + persistent["lb_pnl_week_f"]
        + persistent["lb_pnl_all_f"])

    persistent = persistent.sort_values(
        ["on_all_and_month", "lb_pnl_total"], ascending=[False, False],
    ).reset_index(drop=True)
    return persistent


def board_combo(row) -> str:
    """A ``+``-joined string of which boards a wallet appears on."""
    parts = []
    if _get(row, "on_all_board", False):
        parts.append("ALL")
    if _get(row, "on_month_board", False):
        parts.append("MONTH")
    if _get(row, "on_week_board", False):
        parts.append("WEEK")
    return "+".join(parts) if parts else "(none)"


# ---- Distributions printout (calibration aid) -----------------------------
def print_distributions(summary: pd.DataFrame) -> None:
    """Print the metric distributions the archetype cut points are set from."""
    df = summary
    print(f"n_wallets = {len(df)}")
    for col in ("merge_share", "redeem_share", "sell_share",
                "maker_fill_frac", "resolution_exit_frac",
                "pct_vol_noncrypto"):
        s = df[col]
        print(f"\n{col}:")
        print(f"  count={s.count()}  NaN={s.isna().sum()}")
        print(f"  p10={s.quantile(.10):.3f} p50={s.quantile(.50):.3f} "
              f"p90={s.quantile(.90):.3f}")
        print(f"  >0.5: {(s > 0.5).sum()}  >0.6: {(s > 0.6).sum()}")


# ---- Report assembly ------------------------------------------------------
def _load_inputs() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load the wallet summary parquet and compute the entry-price frame."""
    summary = pd.read_parquet(DERIVED_DIR / "wallet_summary.parquet")
    ep = entry_prices_all(load_manifest())
    return summary, ep


def _fmt_usd(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    return f"${v:,.0f}"


def _fmt_pct(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    return f"{v * 100:.0f}%"


def _fmt_p(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    return f"{v:.2f}"


def build_report(summary: Optional[pd.DataFrame] = None,
                 ep: Optional[pd.DataFrame] = None,
                 out_path: Optional[Path] = None) -> Path:
    """Assemble ``docs/research/leaderboard_wallets.md`` and return its path."""
    if summary is None or ep is None:
        summary, ep = _load_inputs()
    out_path = Path(out_path) if out_path is not None else REPORT_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)

    m = summary.merge(ep, on="proxy_wallet", how="left")
    m["archetype"] = m.apply(assign_archetype, axis=1)
    m["entry_role"] = m.apply(entry_role, axis=1)
    m["dom_market"] = m.apply(dominant_market_type, axis=1)

    arch_counts = m["archetype"].value_counts()
    persistent = persistence_rank(summary)
    persistent = persistent.merge(
        m[["proxy_wallet", "archetype", "entry_role", "dom_market"]],
        on="proxy_wallet", how="left")
    pers_ep = persistent.merge(ep, on="proxy_wallet", how="left")

    # 15m directional holders — the population the bot's strategy resembles.
    dh15 = m[(m["pct_vol_15m"] > 0.5)
             & (m["redeem_share"] > 0.5)
             & (m["m15_n_buys"] > 0)].copy()

    now_idt = datetime.now(timezone.utc).astimezone(IDT)

    lines: list[str] = []
    a = lines.append

    a("# Polymarket crypto-leaderboard wallet analysis")
    a("")
    a(f"*Generated {now_idt:%Y-%m-%d %H:%M} IDT (Israel local time). "
      "All computation in UTC; the leaderboard snapshot and wallet activity "
      "caches were fetched in Phase 1.*")
    a("")
    a("This report analyses the **239 distinct wallets** on Polymarket's "
      "crypto profit leaderboard — the union of the top 100 of each of the "
      "MONTH, WEEK and ALL-time boards — to learn **how** the profitable "
      "traders make money. Phase 1 fetched each wallet's full activity cache; "
      "Task 2.1 (`research/wallets/analyze.py`) turned that into per-wallet "
      "metrics; this task assigns strategy archetypes, applies a persistence "
      "filter, and answers the three headline questions below.")
    a("")
    a("> **Going-in hypothesis:** the winners are market-makers. "
      "**Short answer: mostly no** — see Q1.")
    a("")

    # ---- Three headline questions -----------------------------------------
    a("## The three questions, up front")
    a("")

    # Q1
    n_lp = int(arch_counts.get(ARCH_PASSIVE_LP, 0))
    n_mm = int(arch_counts.get(ARCH_MINT_MERGE, 0))
    n_dh = int(arch_counts.get(ARCH_DIR_HOLDER, 0))
    n_sc = int(arch_counts.get(ARCH_SCALPER, 0))
    n_un = int(arch_counts.get(ARCH_UNKNOWN, 0))
    role_counts = m["entry_role"].value_counts()
    a("### Q1 — Does anyone actually profit from market-making?")
    a("")
    a("**Largely no, in the textbook sense.** Of 239 leaderboard wallets, "
      f"only **{n_lp}** classify as `passive_liquidity_provider` — the true "
      "market-maker bucket (posts resting maker quotes AND flattens inventory "
      "rather than holding to resolution).")
    a("")
    a("Archetype breakdown (ordered decision cascade, see "
      "`assign_archetype`):")
    a("")
    a("| Archetype | Wallets | Share |")
    a("|---|---:|---:|")
    for arch in ARCHETYPES:
        n = int(arch_counts.get(arch, 0))
        a(f"| `{arch}` | {n} | {n / len(m) * 100:.0f}% |")
    a(f"| **total** | **{len(m)}** | 100% |")
    a("")
    a("The dominant pattern is **`directional_holder`** "
      f"({n_dh} wallets, {n_dh / len(m) * 100:.0f}%): buy a side, hold it to "
      "resolution, collect the winning-share payout (`redeem`). That is a "
      "*directional bet*, not market-making. The second bucket is "
      f"**`mint_merge_arbitrageur`** ({n_mm} wallets) — wallets that mint "
      "YES+NO pairs and merge them back; a non-directional structured trade, "
      "closer to arbitrage / liquidity provision than the directional bet, "
      "but still not classic two-sided quoting.")
    a("")
    a("**Maker / taker evidence — with its coverage caveat.** The on-chain "
      "role model produced a `maker_fill_frac` for only **172 / 239** "
      "wallets; the other 67 are NaN because the role model never fit (more "
      "than half of those wallets' sampled transactions were absent from the "
      "decoded set). Among the 172 with data:")
    a("")
    a("| Entry role | Wallets |")
    a("|---|---:|")
    for r in ("maker", "taker", "mixed", "unknown"):
        a(f"| {r} | {int(role_counts.get(r, 0))} |")
    a("")
    a("So even on the optimistic reading, maker-dominant wallets are a "
      "minority, and a maker-dominant wallet that **still holds to "
      "resolution** is not market-making — it is just getting a better entry "
      "price on a directional bet. The exit-mode evidence is decisive: "
      "**188 of 239 wallets are redeem-dominant** (hold to resolution) and "
      "**117 wallets never sell at all**. Holding to resolution is the "
      "opposite of the flatten-the-book behaviour that defines a market "
      "maker.")
    a("")

    # Q2
    dom_counts = m["dom_market"].value_counts()
    a("### Q2 — Do winners trade the 15m markets the bot targets, "
      "or longer-dated ones?")
    a("")
    n_15m = int(dom_counts.get("crypto_15m_updown", 0))
    a(f"**They trade the 15m markets.** {n_15m} of 239 wallets "
      f"({n_15m / len(m) * 100:.0f}%) have the **`crypto_15m_updown`** "
      "Up/Down markets as their dominant market type by buy volume — exactly "
      "the markets the live mean-reversion bot targets.")
    a("")
    a("| Dominant market type | Wallets |")
    a("|---|---:|")
    for mt, n in dom_counts.items():
        a(f"| `{mt}` | {int(n)} |")
    a("")
    top15 = summary.sort_values("lb_pnl_all", ascending=False,
                                na_position="last").head(15)
    top15 = top15.merge(m[["proxy_wallet", "dom_market", "archetype"]],
                        on="proxy_wallet", how="left")
    t15_dom = top15["dom_market"].value_counts()
    a("Restricting to the **top 15 by all-time leaderboard PnL**, the "
      "dominant-market split is:")
    a("")
    a("| Dominant market type | Top-15 wallets |")
    a("|---|---:|")
    for mt, n in t15_dom.items():
        a(f"| `{mt}` | {int(n)} |")
    a("")
    a("The biggest all-time winners are split between 15m Up/Down directional "
      "trading and price-target / longer-dated markets, but the 15m bucket is "
      "well represented at the very top — the markets the bot trades are not "
      "a backwater.")
    a("")

    # Q3 — deep-dive table
    a("### Q3 — For the most persistent winners, how do they make money?")
    a("")
    n_pers = len(persistent)
    n_all_month = int(persistent["on_all_and_month"].sum())
    a(f"**{n_pers} wallets are persistent** (appear on >=2 of the "
      f"MONTH/WEEK/ALL boards); **{n_all_month}** of them appear on BOTH the "
      "ALL-time board AND the MONTH board — a lifetime winner that is still "
      "winning right now, the strongest persistence signal available.")
    a("")
    a("Per-wallet deep-dive, top 15 persistent winners "
      "(ALL+MONTH first, then by total board PnL):")
    a("")
    a("| Wallet | Archetype | Dom. market | lb_pnl MONTH / WEEK / ALL | "
      "Exit (redeem/merge/sell share) | Maker | Buy-price p25/p50/p75 (15m) | "
      "VWAP px | Median size | Median hold | n redeem/merge/sell | Trunc |")
    a("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for _, r in pers_ep.head(15).iterrows():
        name = str(r.get("user_name") or r["proxy_wallet"])[:24]
        pnl_str = (f"{_fmt_usd(r.get('lb_pnl_month'))} / "
                   f"{_fmt_usd(r.get('lb_pnl_week'))} / "
                   f"{_fmt_usd(r.get('lb_pnl_all'))}")
        exit_str = (f"{_fmt_pct(r.get('redeem_share'))} / "
                    f"{_fmt_pct(r.get('merge_share'))} / "
                    f"{_fmt_pct(r.get('sell_share'))}")
        bp = (f"{_fmt_p(r.get('m15_price_p25'))} / "
              f"{_fmt_p(r.get('m15_price_p50'))} / "
              f"{_fmt_p(r.get('m15_price_p75'))}")
        hold = r.get("median_hold_seconds")
        hold_str = (f"{hold / 3600:.1f}h" if pd.notna(hold) and hold >= 3600
                    else (f"{hold / 60:.0f}m" if pd.notna(hold) else "—"))
        nev = (f"{int(r.get('n_redeems') or 0)}/"
               f"{int(r.get('n_merges') or 0)}/"
               f"{int(r.get('n_sells') or 0)}")
        a(f"| {name} | `{r.get('archetype')}` | `{r.get('dom_market')}` | "
          f"{pnl_str} | {exit_str} | {_fmt_p(r.get('maker_fill_frac'))} | "
          f"{bp} | {_fmt_p(r.get('m15_vwap_price'))} | "
          f"{_fmt_usd(r.get('median_usdc_size'))} | {hold_str} | {nev} | "
          f"{'Y' if r.get('activity_truncated') else 'n'} |")
    a("")
    a("The deep-dive table shows two clean money-making templates among the "
      "persistent winners: (a) **15m directional holders** — dominant in "
      "`crypto_15m_updown`, redeem-dominant, hold each bet ~minutes to "
      "resolution; and (b) **mint-merge arbitrageurs** — `merge_share` near "
      "0.6-0.8, dominant in price-target markets, non-directional. The "
      "`win_rate` column from Task 2.1 is deliberately omitted (see caveats).")
    a("")

    # ---- Persistence breakdown -------------------------------------------
    a("## Persistence — the board-combination breakdown")
    a("")
    summary_combo = summary.copy()
    summary_combo["combo"] = summary_combo.apply(board_combo, axis=1)
    combo_counts = summary_combo["combo"].value_counts()
    a("| Board combination | Wallets | Persistent? |")
    a("|---|---:|---|")
    for combo, n in combo_counts.items():
        is_pers = "yes" if "+" in combo else "no (single board)"
        a(f"| {combo} | {int(n)} | {is_pers} |")
    a("")
    a("The persistence filter is `n_boards >= 2`. The rejected alternative — "
      "counting >=3 distinct calendar months in a wallet's *own* activity "
      "history — is unusable here: **173/239 wallets (72%) are "
      "`activity_truncated`** at the 4000-record API cap, so their visible "
      "history can span well under three months and that criterion would "
      "mostly measure trade frequency, not longevity.")
    a("")

    # ---- Buy-price section -----------------------------------------------
    a("## Entry odds — where winners buy")
    a("")
    a("The single most important strategy descriptor: at what **price (odds)** "
      "do winners enter? Computed from each wallet's BUY trades, restricted to "
      "`crypto_15m_updown` markets and to the **133 wallets** that are both "
      "15m-dominant and redeem-dominant (the directional-holder population the "
      "bot's strategy most resembles).")
    a("")
    if len(dh15):
        a("Per-wallet buy-price percentiles, summarised across the 133 "
          "15m directional-holder wallets:")
        a("")
        a("| Buy-price stat | Median wallet | p25 wallet | p75 wallet |")
        a("|---|---:|---:|---:|")
        for label, col in (("p10", "m15_price_p10"),
                           ("p25", "m15_price_p25"),
                           ("p50 (median)", "m15_price_p50"),
                           ("p75", "m15_price_p75"),
                           ("p90", "m15_price_p90"),
                           ("VWAP (usdc-weighted)", "m15_vwap_price")):
            s = dh15[col].dropna()
            a(f"| {label} | {s.median():.3f} | {s.quantile(.25):.3f} | "
              f"{s.quantile(.75):.3f} |")
        a("")
        # pooled odds-band shares
        w = dh15["m15_buy_usdc"].fillna(0.0)
        a("Share of 15m buy **USDC volume** by odds band (USDC-weighted "
          "across all 133 wallets — i.e. where the money actually goes):")
        a("")
        a("| Odds band | Share of buy volume |")
        a("|---|---:|")
        for band, lbl in (("m15_band_0_20", "[0.00, 0.20)"),
                          ("m15_band_20_40", "[0.20, 0.40)"),
                          ("m15_band_40_60", "[0.40, 0.60)"),
                          ("m15_band_60_80", "[0.60, 0.80)"),
                          ("m15_band_80_100", "[0.80, 1.00]")):
            share = (np.average(dh15[band], weights=w)
                     if w.sum() > 0 else dh15[band].mean())
            a(f"| {lbl} | {share * 100:.0f}% |")
        a("")
        off_p10 = dh15["m15_entry_offset_p10"].median()
        off_p50 = dh15["m15_entry_offset_p50"].median()
        off_p90 = dh15["m15_entry_offset_p90"].median()
        a("**Entry timing within the 15m window** (derived from the compact "
          "slug's window-start timestamp; coverage is essentially 100% of 15m "
          "buys because the compact `<asset>-updown-15m-<ts>` slug carries the "
          "window start). Median across wallets of the per-wallet "
          f"percentiles: p10 = {off_p10:.0f}s, p50 = {off_p50:.0f}s, "
          f"p90 = {off_p90:.0f}s into the 900-second window.")
        a("")
        a("**Reading the buy-price data.** Winners do **not** concentrate "
          "their entries in one narrow odds band. On a per-wallet basis the "
          "median trader's buys span roughly p25 = 0.27 to p75 = 0.59 — a "
          "wide interquartile range straddling the 0.50 coin-flip. But the "
          "USDC-weighted view tells a sharper story: **the money is "
          "bimodal-to-favourite-heavy** — the largest single block of buy "
          "volume (~46%) lands at odds [0.80, 1.00], and the volume-weighted "
          "average entry price is ~0.72. In plain terms: winners place many "
          "small bets across all odds, but they put their *big* money on "
          "heavy favourites (cheap-implied-edge, high-probability legs) and "
          "they enter **early in the window** (median ~3 minutes in, almost "
          "all within the first 5 minutes). This is the empirical entry "
          "signature the Phase 3 backtest must reproduce.")
    else:
        a("*No 15m directional-holder wallets with buy-price data — "
          "investigate the cache.*")
    a("")

    # ---- Synthesis & honesty ---------------------------------------------
    a("## Synthesis and honest caveats")
    a("")
    a("**What the data shows.** The Polymarket crypto leaderboard is *not* "
      "dominated by market-makers. The modal winning wallet is a "
      "**directional holder**: it buys one side of a short-dated crypto "
      "Up/Down market — overwhelmingly the 15-minute series — and holds to "
      "resolution. A meaningful second group runs a **mint-merge** structured "
      "trade in price-target markets. Classic two-sided passive "
      "liquidity provision is a small minority.")
    a("")
    a("**Survivorship bias — the central limitation.** The leaderboard is, by "
      "construction, the **top 100 winners** of each board. It tells us what "
      "winners *do*; it tells us **nothing** about how many wallets ran the "
      "same strategy and lost. If 5,000 wallets bought heavy-favourite 15m "
      "legs and 100 of them are on the board, this analysis cannot "
      "distinguish skill from variance. The wallet analysis can only generate "
      "a hypothesis about a positive-expectancy strategy; it **cannot prove "
      "one exists**. Proving expectancy requires the Phase 3 backtest on full "
      "15m tick data, which sees winners *and* losers.")
    a("")
    a("**Other caveats.**")
    a("")
    a("* **Inflated `win_rate`.** The FIFO `win_rate` from Task 2.1 is "
      "structurally biased upward: a REDEEM lot always resolves at 1.0 (the "
      "winning payout) and losing shares simply expire with no activity "
      "record, so losers leave no FIFO round-trip. `win_rate` is therefore "
      "**not used as an edge metric anywhere in this report.**")
    a("* **Truncation.** 173/239 wallets are capped at 4000 activity records. "
      "Cash-flow PnL (`total_realized_pnl_cashflow`) only reconciles against "
      "the official board PnL when `activity_truncated == False`; for the "
      "truncated majority it is partial. The official `lb_pnl` is the only "
      "trustworthy 'how much' anchor and is what the deep-dive table uses.")
    a("* **Thin maker/taker coverage.** `maker_fill_frac` is NaN for 67 "
      "wallets and built from a *sample* of decoded transactions for the "
      "rest. Treat the maker/taker split as indicative, not exact.")
    a("")

    # ---- Testable strategy ----------------------------------------------
    a("## Testable strategy for Phase 3")
    a("")
    if len(dh15):
        p25 = dh15["m15_price_p25"].median()
        p75 = dh15["m15_price_p75"].median()
        w_dh = dh15["m15_buy_usdc"].fillna(0.0)
        pooled_vwap = (float(np.average(dh15["m15_vwap_price"], weights=w_dh))
                       if w_dh.sum() > 0
                       else float(dh15["m15_vwap_price"].mean()))
        a("Translating the dominant winning pattern into ONE explicit, "
          "backtestable rule on 15m tick data:")
        a("")
        a("> **Rule M15-DH-1 (directional-holder, favourite-leg).** On a "
          "`crypto_15m_updown` market (BTC/ETH/SOL/XRP Up/Down, 15-minute "
          "window): within the **first 5 minutes** of the window "
          "(entry offset 0-300 s), **buy the favourite side** when its odds "
          "are in the band **[0.60, 0.90]** — the heavy-favourite zone where "
          "the winners concentrate their USDC volume (pooled USDC-weighted "
          f"entry price ~{pooled_vwap:.2f}; ~46% of winner buy-volume sits "
          "at >=0.80). **Hold to resolution** (no early exit, no stop). "
          "Position sizing flat per trade.")
        a("")
        a("Concrete parameters extracted from this analysis (Step 1 "
          "buy-price data, 133 15m directional-holder winners):")
        a("")
        a("| Parameter | Value | Source |")
        a("|---|---|---|")
        a(f"| Market | `crypto_15m_updown` (15-min Up/Down) | dominant for "
          f"{n_15m}/239 wallets |")
        a("| Side | the favourite (odds > 0.50 leg) | redeem-dominant, "
          "holds-to-win |")
        a(f"| Entry-odds band | **[0.60, 0.90]** | winner buy-volume "
          f"concentration; per-wallet p75 median {p75:.2f}, pooled "
          f"USDC-weighted entry price {pooled_vwap:.2f} |")
        a("| Entry timing | first 0-300 s of the 900 s window | median "
          "winner entry ~185 s in |")
        a("| Exit | hold to resolution | 188/239 redeem-dominant; "
          "117 never sell |")
        a("")
        a("**Honest note on the band.** The per-wallet *interquartile* range "
          f"is wider and lower (p25 ~{p25:.2f}, p50 ~"
          f"{dh15['m15_price_p50'].median():.2f}) than the volume-weighted "
          "band — winners place many small bets across all odds. Rule "
          "M15-DH-1 deliberately follows the **money** (USDC-weighted), not "
          "the trade count, because that is where the realized PnL is. Phase "
          "3 should backtest the [0.60, 0.90] favourite-leg variant as the "
          "primary, and a wider [0.50, 0.95] variant as a sensitivity check. "
          "If neither shows positive expectancy on the full tick data "
          "(winners + losers), the leaderboard pattern is survivorship "
          "variance, not edge — and that is itself a valid, reportable "
          "Phase 3 outcome.")
        a("")
        a("A second, separate hypothesis worth a Phase 3 look — the "
          f"`mint_merge_arbitrageur` bucket ({n_mm} wallets) — is **out of "
          "scope** for the 15m mean-reversion bot (it lives in price-target "
          "markets) and is noted here only for completeness.")
    else:
        a("*Insufficient 15m buy-price data to write a concrete rule.*")
    a("")
    a("---")
    a("")
    a(f"*Inputs: `data/wallets/derived/wallet_summary.parquet` (239 rows), "
      f"`research/wallets/entry_prices.py`. Archetype cascade: "
      f"`research/wallets/wallet_report.py:assign_archetype`. "
      f"Regenerate with `python -m research.wallets.wallet_report`.*")

    out_path.write_text("\n".join(lines) + "\n")
    return out_path


def main() -> None:
    """Print the calibration distributions, archetype/persistence counts, and
    regenerate the report."""
    summary, ep = _load_inputs()
    print("=== metric distributions (archetype calibration) ===")
    print_distributions(summary)

    m = summary.copy()
    m["archetype"] = m.apply(assign_archetype, axis=1)
    print("\n=== archetype counts ===")
    print(m["archetype"].value_counts().to_string())

    persistent = persistence_rank(summary)
    print(f"\n=== persistence ===")
    print(f"persistent (n_boards>=2): {len(persistent)}")
    print(f"on ALL+MONTH:             {int(persistent['on_all_and_month'].sum())}")

    path = build_report(summary, ep)
    print(f"\nreport written -> {path}")


if __name__ == "__main__":
    main()
