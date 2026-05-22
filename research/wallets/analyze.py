"""Task 2.1 — Wallet analysis module.

Turns the on-disk leaderboard-wallet cache (built by
``research.wallets.fetch_wallets``) into per-wallet metrics. A later task (2.2)
consumes the output of :func:`analyze_all` to assign strategy archetypes and
write the report.

Three capabilities:

A. **Cache readers** — load a wallet's activity / positions / on-chain roles,
   plus the manifest and the date-stamped leaderboard snapshots. All tolerant
   of a missing or failed wallet (return empty).

B. :func:`classify_market` — bucket a market into one of seven types from its
   slug (preferred) or title (fallback).

C. :func:`reconstruct_roundtrips` — FIFO-match a wallet's BUY/SELL/REDEEM/MERGE
   activity into realized round-trips.

D. :func:`summarize_wallet` / :func:`analyze_all` — per-wallet metric rows.

All computation is in UTC. Timestamps are unix seconds. The report task (2.2)
is responsible for any Israel-local-time display.
"""
from __future__ import annotations

import dataclasses
import glob
import gzip
import json
import os
import re
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import structlog

log = structlog.get_logger(__name__)

# research/wallets/analyze.py -> parents[2] == repo root (same idea as loader.py).
REPO_ROOT = Path(__file__).resolve().parents[2]
WALLETS_DIR = REPO_ROOT / "data" / "wallets"
LEADERBOARD_DIR = WALLETS_DIR / "leaderboard"
RAW_DIR = WALLETS_DIR / "raw"
ACTIVITY_DIR = RAW_DIR / "activity"
POSITIONS_DIR = RAW_DIR / "positions"
ONCHAIN_DIR = RAW_DIR / "onchain"
MANIFEST_PATH = WALLETS_DIR / "manifest.json"
DERIVED_DIR = WALLETS_DIR / "derived"

# Taker fee model used by polymarket-arb: fee = shares * 0.07 * p * (1 - p) per
# leg. Used to ESTIMATE a round-trip's exit-leg fee when no on-chain receipt is
# available for the tx (the cache only decodes a random sample of txs).
TAKER_FEE_RATE = 0.07


# --------------------------------------------------------------------------
# A. Cache readers
# --------------------------------------------------------------------------
def load_manifest(path: Optional[Path] = None) -> dict:
    """Load ``manifest.json``. Returns ``{}`` if absent."""
    path = Path(path) if path is not None else MANIFEST_PATH
    if not path.exists():
        log.warning("manifest_missing", path=str(path))
        return {}
    with open(path) as fh:
        return json.load(fh)


def _read_jsonl_gz(path: Path) -> list[dict]:
    """Read a gzipped JSONL file into a list of dicts. Tolerant of a missing
    file or a truncated trailing line (returns what was parseable)."""
    if not path.exists():
        return []
    out: list[dict] = []
    try:
        with gzip.open(path, "rt") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    # A truncated final line on an interrupted write — skip it.
                    log.warning("jsonl_bad_line", path=str(path))
                    continue
    except (OSError, EOFError) as exc:
        log.warning("jsonl_read_failed", path=str(path), error=str(exc))
    return out


def load_activity(wallet: str) -> list[dict]:
    """All cached activity records for a wallet (list of dicts), newest-first
    as stored. Empty list if the wallet has no activity file."""
    return _read_jsonl_gz(ACTIVITY_DIR / f"{wallet}.jsonl.gz")


def load_positions(wallet: str) -> list[dict]:
    """Current open positions for a wallet (raw dicts). Empty list if absent."""
    path = POSITIONS_DIR / f"{wallet}.json"
    if not path.exists():
        return []
    try:
        with open(path) as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("positions_read_failed", wallet=wallet, error=str(exc))
        return []


def load_onchain_roles(wallet: str) -> list[dict]:
    """On-chain maker/taker role records for a wallet's sampled txs.
    Each dict is the ``asdict`` form of ``WalletFillRole``."""
    return _read_jsonl_gz(ONCHAIN_DIR / f"{wallet}.jsonl.gz")


def load_leaderboards(directory: Optional[Path] = None) -> dict[str, list[dict]]:
    """Load every ``CRYPTO_<PERIOD>_<date>.json`` snapshot under the leaderboard
    directory, keyed by PERIOD (MONTH / WEEK / ALL). If more than one date is
    cached for a period the most recent file wins."""
    directory = Path(directory) if directory is not None else LEADERBOARD_DIR
    out: dict[str, list[dict]] = {}
    if not directory.exists():
        return out
    for path in sorted(glob.glob(str(directory / "CRYPTO_*.json"))):
        m = re.match(r"CRYPTO_([A-Z]+)_(\d{4}-\d{2}-\d{2})\.json$",
                     os.path.basename(path))
        if not m:
            continue
        period = m.group(1)
        try:
            with open(path) as fh:
                out[period] = json.load(fh)  # sorted() -> latest date overwrites
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("leaderboard_read_failed", path=path, error=str(exc))
    return out


# --------------------------------------------------------------------------
# B. Market classification
# --------------------------------------------------------------------------
# Market-type buckets returned by classify_market:
MARKET_TYPES = (
    "crypto_15m_updown",
    "crypto_hourly_updown",
    "crypto_daily_updown",
    "crypto_weekly_monthly",
    "crypto_price_target",
    "crypto_other",
    "non_crypto",
)

# Crypto keywords for the title fallback / non-crypto guard.
_CRYPTO_WORDS = re.compile(
    r"\b(bitcoin|btc|ethereum|eth|solana|sol|xrp|ripple|dogecoin|doge)\b",
    re.IGNORECASE,
)

# Slug forms observed in the real cached data (3 smoke wallets, ~12k records):
#   btc-updown-5m-<ts>     eth-updown-5m-<ts>    sol-updown-5m-<ts>   xrp-updown-5m-<ts>
#   btc-updown-15m-<ts>    eth-updown-15m-<ts>   ...
#   btc-updown-4h-<ts>     eth-updown-4h-<ts>    ...
#   bitcoin-up-or-down-may-22-2026-2pm-et   ethereum-up-or-down-... solana-/xrp-...
#   bitcoin-up-or-down-on-may-23-2026
#   bitcoin-above-78k-on-may-24-2026 / bitcoin-above-92k-on-may-21
#   will-the-price-of-bitcoin-be-between-76000-78000-on-may-23-2026
#   will-the-price-of-bitcoin-be-greater-than-88000-on-may-20
#   will-the-price-of-bitcoin-be-less-than-72000-on-may-21
#   will-bitcoin-reach-85k-in-may-2026 / will-bitcoin-reach-78k-on-may-22
#         / will-bitcoin-reach-80k-may-18-24-2026
#   will-bitcoin-dip-to-75k-in-may-2026 / ...-on-may-22 / ...-may-18-24-2026
#
# Compact-token Up/Down series (btc/eth/sol/xrp-updown-<tf>-<ts>): the <tf>
# token directly gives the timeframe.
_SLUG_UPDOWN_COMPACT = re.compile(
    r"^(btc|eth|sol|xrp)-updown-(\d+)(m|h)-\d+$", re.IGNORECASE,
)
# Long-form Up/Down series (bitcoin/ethereum/solana/xrp-up-or-down-...). These
# come in two shapes: an intraday one with a time-of-day token (...-2pm-et,
# ...-11am-et) -> hourly; and a bare-date one (...-on-may-23-2026) -> daily.
_SLUG_UPDOWN_LONG = re.compile(r"-up-or-down-", re.IGNORECASE)
_SLUG_TIME_OF_DAY = re.compile(r"\d{1,2}(am|pm)-et\b", re.IGNORECASE)
# Price-target families.
_SLUG_PRICE_TARGET = re.compile(
    r"-(above|below|reach|dip-to|be-between|be-greater-than|be-less-than"
    r"|be-above|be-below|hit)-",
    re.IGNORECASE,
)
# A multi-day range (may-18-24) or a month-only span (in-may-2026) in a slug =>
# longer-dated. A single bare date (on-may-23 / -may-23-2026) => daily horizon.
_SLUG_DATE_RANGE = re.compile(r"-\d{1,2}-\d{1,2}-\d{4}$|-in-[a-z]+-\d{4}$",
                              re.IGNORECASE)

# Title fallbacks.
_TITLE_UPDOWN = re.compile(r"up or down", re.IGNORECASE)
# Intraday time in a title: "2:15PM-2:20PM ET", "11AM ET", "2PM ET".
_TITLE_INTRADAY = re.compile(r"\d{1,2}(:\d{2})?\s*[ap]m", re.IGNORECASE)
# A 5-minute / 15-minute span written out in a title.
_TITLE_MINUTE_SPAN = re.compile(
    r"\d{1,2}:\d{2}\s*[ap]m\s*-\s*\d{1,2}:\d{2}\s*[ap]m", re.IGNORECASE,
)
_TITLE_PRICE_TARGET = re.compile(
    r"(reach|hit|dip to|above \$|below \$|greater than \$|less than \$"
    r"|between \$)",
    re.IGNORECASE,
)
# Month-only / "in 2026" / "by end of" => weekly/monthly horizon.
_TITLE_LONGDATED = re.compile(
    r"(in (january|february|march|april|may|june|july|august|september"
    r"|october|november|december)\b|in 20\d\d|by end of|end of (the )?"
    r"(month|year|week)|this (month|year|week))",
    re.IGNORECASE,
)
_MONTHS = (
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
)
# A bare calendar date in a title, e.g. "on May 23" or "May 23, 2026".
_TITLE_BARE_DATE = re.compile(
    r"\b(" + "|".join(_MONTHS) + r")\s+\d{1,2}\b", re.IGNORECASE,
)


@dataclasses.dataclass
class MarketClass:
    """Result of :func:`classify_market`. ``method`` lets the report flag a
    low-confidence (title-only) classification."""
    market_type: str          # one of MARKET_TYPES
    method: str               # "by_slug" | "by_title" | "unknown"


def _classify_by_slug(slug: str) -> Optional[str]:
    """Return a market type from the slug alone, or None if the slug is not a
    recognised structured Polymarket crypto slug."""
    s = (slug or "").strip().lower()
    if not s:
        return None

    # Compact Up/Down series: timeframe token is authoritative.
    m = _SLUG_UPDOWN_COMPACT.match(s)
    if m:
        n, unit = int(m.group(2)), m.group(3)
        minutes = n if unit == "m" else n * 60
        if minutes <= 15:
            return "crypto_15m_updown"
        if minutes < 24 * 60:
            return "crypto_hourly_updown"
        return "crypto_daily_updown"

    # Long-form Up/Down series.
    if _SLUG_UPDOWN_LONG.search(s):
        if not _CRYPTO_WORDS.search(s):
            return "non_crypto"
        if _SLUG_TIME_OF_DAY.search(s):
            # "...-2pm-et" — an hourly market.
            return "crypto_hourly_updown"
        # "...-on-may-23-2026" — a daily Up/Down.
        return "crypto_daily_updown"

    # Price-target families (above/below/reach/dip-to/between/greater/less).
    if _SLUG_PRICE_TARGET.search(s):
        if not _CRYPTO_WORDS.search(s):
            return "non_crypto"
        if _SLUG_DATE_RANGE.search(s):
            return "crypto_weekly_monthly"
        return "crypto_price_target"

    return None


def _classify_by_title(title: str) -> str:
    """Best-effort market type from the title alone. Always returns a bucket."""
    t = (title or "").strip()
    if not t:
        return "crypto_other"  # no slug, no title — caller already tried slug
    has_crypto = bool(_CRYPTO_WORDS.search(t))

    # Up/Down markets.
    if _TITLE_UPDOWN.search(t):
        if not has_crypto:
            return "non_crypto"
        if _TITLE_MINUTE_SPAN.search(t):
            # An explicit minute span "2:15PM-2:20PM ET" — 5m/15m intraday.
            return "crypto_15m_updown"
        if _TITLE_INTRADAY.search(t):
            # A time of day but no minute span ("11AM ET") — hourly.
            return "crypto_hourly_updown"
        if _TITLE_LONGDATED.search(t):
            return "crypto_weekly_monthly"
        if _TITLE_BARE_DATE.search(t):
            return "crypto_daily_updown"
        return "crypto_other"

    # Price-target markets ("reach $", "above $", "between $X and $Y", ...).
    if _TITLE_PRICE_TARGET.search(t):
        if not has_crypto:
            return "non_crypto"
        if _TITLE_LONGDATED.search(t):
            return "crypto_weekly_monthly"
        return "crypto_price_target"

    if not has_crypto:
        return "non_crypto"
    if _TITLE_LONGDATED.search(t):
        return "crypto_weekly_monthly"
    return "crypto_other"


def classify_market(title: str, slug: str) -> MarketClass:
    """Classify a Polymarket market into one of :data:`MARKET_TYPES`.

    Slug-first: structured Polymarket crypto slugs encode the timeframe and
    family directly, so when the slug is recognised the result is high
    confidence (``method == "by_slug"``). Falls back to the title otherwise
    (``method == "by_title"``) — the report should treat title-only
    classifications as lower confidence.
    """
    by_slug = _classify_by_slug(slug)
    if by_slug is not None:
        return MarketClass(by_slug, "by_slug")
    bucket = _classify_by_title(title)
    method = "by_title" if (title or "").strip() else "unknown"
    return MarketClass(bucket, method)


# Map a market type to the summary's pct-of-volume column.
_PCT_VOL_COL = {
    "crypto_15m_updown": "pct_vol_15m",
    "crypto_hourly_updown": "pct_vol_hourly",
    "crypto_daily_updown": "pct_vol_daily",
    "crypto_weekly_monthly": "pct_vol_longdated",
    "crypto_price_target": "pct_vol_pricetarget",
    "crypto_other": "pct_vol_other",
    "non_crypto": "pct_vol_noncrypto",
}


# --------------------------------------------------------------------------
# C. Round-trip reconstruction
# --------------------------------------------------------------------------
ROUNDTRIP_COLUMNS = [
    "wallet", "slug", "outcome", "market_type",
    "entry_ts", "exit_ts", "hold_seconds", "shares",
    "entry_price", "exit_price", "gross_pnl", "fee_usdc", "net_pnl",
    "fee_estimated", "exit_kind",
]


def _onchain_fee_by_tx(onchain_roles: list[dict]) -> dict[str, float]:
    """Map ``tx_hash -> fee_usdc`` from the on-chain role records."""
    out: dict[str, float] = {}
    for r in onchain_roles or []:
        tx = r.get("tx_hash")
        if tx:
            out[str(tx).lower()] = float(r.get("fee_usdc") or 0.0)
    return out


def reconstruct_roundtrips(
    activity: list[dict],
    wallet: str = "",
    onchain_roles: Optional[list[dict]] = None,
) -> pd.DataFrame:
    """FIFO-match a wallet's activity into realized round-trips.

    For each ``(slug, outcome)`` key, records are sorted by ``timestamp`` and an
    inventory of open BUY lots ``(shares, price, ts, tx)`` is maintained:

    * ``TRADE`` / ``side == "BUY"`` — opens a new lot.
    * ``TRADE`` / ``side == "SELL"`` — matched FIFO against the oldest open
      lot(s); each matched lot emits a round-trip row, ``exit_kind="sell"``.
    * ``REDEEM`` — remaining open lots in that key close at resolution. The
      cached ``/activity`` REDEEM record carries no per-outcome resolution
      value (``price == 0``, ``outcome == ""``, ``usdc_size == size``), so the
      resolved value is inferred: a wallet only ever redeems *winning* shares
      (losing shares expire worthless and are never redeemed), so a REDEEM with
      ``size > 0`` resolves the open lots at ``1.0``. ``exit_kind="resolution"``.
    * ``MERGE`` — a merge of ``N`` pairs burns ``N`` YES + ``N`` NO and returns
      ``N`` USDC of collateral. The cached ``/activity`` MERGE record is a
      single-outcome-agnostic event (``outcome == ""``, ``price == 0``); the
      complementary leg lives under a *different* ``(slug, outcome)`` key and is
      not visible while processing one key. A merge is a position *unwind*, not
      a directional exit — closing one leg at ``1.0`` would fabricate a profit
      (the other leg simultaneously "loses"). So we treat a MERGE as closing
      the key's open lots at **their own entry price** (``gross_pnl == 0``,
      ``exit_kind="merge"``). This is a documented simplification: merge
      round-trips carry no realized directional PnL and the report should
      exclude / footnote them. (Confirmed against the smoke cache: merged slugs
      always have both YES and NO buys, consistent with an unwind.)
    * Lots still open at end of history — emitted with ``exit_kind="open"`` and
      excluded from realized stats by the caller.

    **FIFO is an approximation.** Polymarket does not expose lot-level
    accounting; FIFO is the conventional, deterministic choice and is what the
    report's hold-time / per-trade-PnL stats are built on. Actual tax-lot
    matching by the trader is unobservable.

    **Fees.** Each round-trip's ``fee_usdc`` is the exit leg's fee. When the
    SELL/REDEEM tx has an on-chain receipt (``onchain_roles``) the real
    ``fee_usdc`` is used and split pro-rata across that tx's matched lots.
    Otherwise the taker fee is estimated as ``shares * 0.07 * p * (1 - p)`` and
    the row is flagged ``fee_estimated=True``.
    """
    cols = ROUNDTRIP_COLUMNS
    if not activity:
        return pd.DataFrame(columns=cols)

    fee_by_tx = _onchain_fee_by_tx(onchain_roles or [])

    # Group records by (slug, outcome).
    keys: dict[tuple, list[dict]] = {}
    for rec in activity:
        slug = rec.get("slug") or ""
        outcome = rec.get("outcome") or ""
        rtype = rec.get("type")
        # A REDEEM/MERGE carries an empty outcome but still references its slug;
        # we settle it against whatever open lots that slug has. Group those by
        # slug only (outcome == "") and resolve every outcome's lots for it.
        keys.setdefault((slug, outcome), []).append(rec)

    rows: list[dict] = []

    # Collect, per slug, the resolution/merge events (which have outcome == "").
    slug_settlements: dict[str, list[dict]] = {}
    for (slug, outcome), recs in keys.items():
        if outcome == "":
            for r in recs:
                if r.get("type") in ("REDEEM", "MERGE"):
                    slug_settlements.setdefault(slug, []).append(r)

    # Per-tx accumulator so an on-chain fee is split across that tx's lots.
    tx_lot_count: dict[str, int] = {}

    for (slug, outcome), recs in sorted(keys.items()):
        if outcome == "":
            continue  # settlement-only group, handled per real outcome below

        recs = sorted(recs, key=lambda r: r.get("timestamp") or 0)
        market_type = classify_market(
            recs[0].get("title", ""), slug,
        ).market_type

        # Open BUY lots: list of mutable dicts {shares, price, ts, tx}.
        lots: list[dict] = []

        def _emit(lot, exit_ts, exit_price, exit_kind, exit_tx):
            shares = lot["shares"]
            entry_price = lot["price"]
            gross = shares * (exit_price - entry_price)
            rows.append({
                "wallet": wallet,
                "slug": slug,
                "outcome": outcome,
                "market_type": market_type,
                "entry_ts": lot["ts"],
                "exit_ts": exit_ts,
                "hold_seconds": max(0, int(exit_ts) - int(lot["ts"])),
                "shares": shares,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "gross_pnl": gross,
                "exit_kind": exit_kind,
                "_exit_tx": exit_tx,
                "_exit_price_for_fee": exit_price,
            })

        for rec in recs:
            rtype = rec.get("type")
            ts = rec.get("timestamp") or 0
            side = rec.get("side") or ""
            size = float(rec.get("size") or 0.0)
            price = float(rec.get("price") or 0.0)
            tx = str(rec.get("transaction_hash") or "").lower()

            if rtype == "TRADE" and side == "BUY":
                if size > 0:
                    lots.append({"shares": size, "price": price,
                                 "ts": ts, "tx": tx})
            elif rtype == "TRADE" and side == "SELL":
                remaining = size
                while remaining > 1e-9 and lots:
                    lot = lots[0]
                    matched = min(lot["shares"], remaining)
                    _emit({"shares": matched, "price": lot["price"],
                           "ts": lot["ts"]}, ts, price, "sell", tx)
                    lot["shares"] -= matched
                    remaining -= matched
                    if lot["shares"] <= 1e-9:
                        lots.pop(0)
                # A SELL with no matching inventory (short / data gap) is
                # dropped — we cannot reconstruct an entry for it.

        # Settle any lots still open against this slug's REDEEM/MERGE events.
        settlements = sorted(
            slug_settlements.get(slug, []),
            key=lambda r: r.get("timestamp") or 0,
        )
        for ev in settlements:
            if not lots:
                break
            ev_type = ev.get("type")
            ev_ts = ev.get("timestamp") or 0
            ev_tx = str(ev.get("transaction_hash") or "").lower()
            ev_size = float(ev.get("size") or 0.0)
            if ev_type == "REDEEM":
                # A redeem is only issued for winning shares -> resolve at 1.0.
                exit_price = 1.0 if ev_size > 0 else 0.0
                exit_kind = "resolution"
                for lot in lots:
                    _emit(lot, ev_ts, exit_price, exit_kind, ev_tx)
            else:  # MERGE — a position unwind; close each leg at its own entry
                # price so the merge round-trip carries no directional PnL.
                exit_kind = "merge"
                for lot in lots:
                    _emit(lot, ev_ts, lot["price"], exit_kind, ev_tx)
            lots = []

        # Anything still open at end of history.
        last_ts = recs[-1].get("timestamp") or 0
        for lot in lots:
            _emit(lot, last_ts, lot["price"], "open", "")

    if not rows:
        return pd.DataFrame(columns=cols)

    # Count lots per exit tx so an on-chain fee can be split pro-rata.
    for r in rows:
        if r["exit_kind"] == "open":
            continue
        tx = r["_exit_tx"]
        if tx:
            tx_lot_count[tx] = tx_lot_count.get(tx, 0) + 1

    # Assign fees.
    for r in rows:
        if r["exit_kind"] == "open":
            r["fee_usdc"] = 0.0
            r["fee_estimated"] = False
        else:
            tx = r["_exit_tx"]
            if tx and tx in fee_by_tx:
                n = max(1, tx_lot_count.get(tx, 1))
                r["fee_usdc"] = fee_by_tx[tx] / n
                r["fee_estimated"] = False
            else:
                p = r["_exit_price_for_fee"]
                r["fee_usdc"] = r["shares"] * TAKER_FEE_RATE * p * (1.0 - p)
                r["fee_estimated"] = True
        r["net_pnl"] = r["gross_pnl"] - r["fee_usdc"]

    df = pd.DataFrame(rows)
    df = df.drop(columns=["_exit_tx", "_exit_price_for_fee"])
    return df[cols].reset_index(drop=True)


# --------------------------------------------------------------------------
# C2. Market-level cash-flow PnL
# --------------------------------------------------------------------------
# Verified empirically against the rank-1 wallet's real activity cache:
#
#   MERGE record:  type="MERGE",  size == usdc_size,  price == 0.0,
#                  outcome == "",  side == "".
#     A merge of N matched YES+NO pairs burns N YES + N NO shares and returns
#     N USDC of collateral. The activity record reports that returned
#     collateral in BOTH `size` and `usdc_size` (they are equal). So the USDC
#     a merge returns to the wallet == record["usdc_size"].
#
#   REDEEM record: type="REDEEM", size == usdc_size, price == 0.0,
#                  outcome == "",  side == "".
#     A redeem of N winning shares pays N * 1.0 == N USDC. The record reports
#     that payout in both `size` and `usdc_size` (equal). Losing shares are
#     never redeemed (they expire worthless and generate no record). So the
#     USDC a redeem pays == record["usdc_size"].
#
#   TRADE record:  `usdc_size` is the cash leg of the fill (size * price).
#     A BUY spends `usdc_size`; a SELL receives `usdc_size`.
#
# Because a binary market's YES and NO outcomes share one slug, grouping all
# of a wallet's records by `slug` captures the entire market — both legs of a
# mint-buy-merge cycle — so the cash-flow nets correctly without a conditionId.

MARKET_CASHFLOW_COLUMNS = [
    "wallet", "slug", "market_type",
    "cash_out", "cash_in", "fees", "net_pnl",
    "buy_usdc", "sell_usdc", "merge_usdc", "redeem_usdc",
    "n_buys", "n_sells", "n_merges", "n_redeems",
    "exit_mode",
]


def reconstruct_market_cashflow(
    activity: list[dict],
    onchain_roles: Optional[list[dict]] = None,
    wallet: str = "",
) -> pd.DataFrame:
    """Market-level realized PnL by raw USDC cash flow, grouped by ``slug``.

    Unlike :func:`reconstruct_roundtrips` (FIFO directional matching, which
    cannot value a non-directional mint-buy-merge cycle), this nets every USDC
    movement through a market:

    * ``cash_out`` — Σ ``usdc_size`` of all BUY trades (both outcomes).
    * ``cash_in``  — Σ SELL proceeds + Σ MERGE collateral returned
      + Σ REDEEM proceeds (see field-semantics note above).
    * ``fees``     — Σ on-chain ``fee_usdc`` joined by ``transaction_hash``
      where a receipt was decoded; otherwise an estimated taker fee
      (``shares * 0.07 * p * (1-p)``) for the BUY/SELL legs only (MERGE/REDEEM
      are CTF contract calls, not order fills — no spread/maker fee).
    * ``net_pnl``  — ``cash_in - cash_out - fees``.

    This is strictly more correct than FIFO for *total realized PnL*: it counts
    directional holds, sells, AND merges uniformly. A merge-heavy wallet that
    mints both YES+NO and merges back is running a non-directional liquidity /
    mint-merge strategy whose PnL FIFO matching cannot see.

    Caveats. (1) The ``/activity`` endpoint caps history at ~4000 records, so a
    truncated wallet's oldest BUYs may be missing while their later
    MERGE/REDEEM is present (or vice versa) — individual unresolved or
    half-truncated markets can show a spurious sign. The wallet *total* is far
    more reliable than any single market row. (2) ``exit_mode`` is whichever of
    merge / redeem / sell contributes the most USDC to ``cash_in``.

    Returns one row per ``slug``. Empty frame (with columns) for empty input.
    """
    cols = MARKET_CASHFLOW_COLUMNS
    if not activity:
        return pd.DataFrame(columns=cols)

    fee_by_tx = _onchain_fee_by_tx(onchain_roles or [])

    # Per-slug accumulators.
    @dataclasses.dataclass
    class _Acc:
        title: str = ""
        buy_usdc: float = 0.0
        sell_usdc: float = 0.0
        merge_usdc: float = 0.0
        redeem_usdc: float = 0.0
        n_buys: int = 0
        n_sells: int = 0
        n_merges: int = 0
        n_redeems: int = 0
        fees: float = 0.0
        # tx_hashes whose fee we still need to add (deduped: an on-chain
        # fee is per-tx, so the same tx must not be counted twice).
        seen_fee_tx: set = dataclasses.field(default_factory=set)
        # estimated-fee accumulator for legs without an on-chain receipt.
        est_fees: float = 0.0

    accs: dict[str, _Acc] = {}

    for rec in activity:
        slug = rec.get("slug") or ""
        if not slug:
            continue  # cannot attribute a record with no market slug
        acc = accs.get(slug)
        if acc is None:
            acc = accs[slug] = _Acc(title=rec.get("title") or "")
        if not acc.title and rec.get("title"):
            acc.title = rec.get("title")

        rtype = rec.get("type")
        side = rec.get("side") or ""
        usdc = float(rec.get("usdc_size") or 0.0)
        size = float(rec.get("size") or 0.0)
        price = float(rec.get("price") or 0.0)
        tx = str(rec.get("transaction_hash") or "").lower()

        if rtype == "TRADE" and side == "BUY":
            acc.buy_usdc += usdc
            acc.n_buys += 1
            _accumulate_fee(acc, tx, fee_by_tx, size, price)
        elif rtype == "TRADE" and side == "SELL":
            acc.sell_usdc += usdc
            acc.n_sells += 1
            _accumulate_fee(acc, tx, fee_by_tx, size, price)
        elif rtype == "MERGE":
            # size == usdc_size == USDC collateral returned by the merge.
            acc.merge_usdc += usdc
            acc.n_merges += 1
            # A merge can carry an on-chain fee if decoded; no estimate
            # otherwise (it is a CTF call, not an order fill).
            _accumulate_fee(acc, tx, fee_by_tx, 0.0, 0.0)
        elif rtype == "REDEEM":
            # size == usdc_size == USDC paid out for winning shares.
            acc.redeem_usdc += usdc
            acc.n_redeems += 1
            _accumulate_fee(acc, tx, fee_by_tx, 0.0, 0.0)
        # REWARD / MAKER_REBATE etc. are ignored here (not BUY cost or a
        # position exit); they are small and handled, if at all, elsewhere.

    rows: list[dict] = []
    for slug, acc in accs.items():
        cash_in = acc.sell_usdc + acc.merge_usdc + acc.redeem_usdc
        cash_out = acc.buy_usdc
        fees = acc.fees + acc.est_fees
        # exit_mode = whichever exit channel dominates cash_in.
        channels = {"merge": acc.merge_usdc, "redeem": acc.redeem_usdc,
                    "sell": acc.sell_usdc}
        if cash_in <= 0:
            exit_mode = "none"
        else:
            exit_mode = max(channels, key=channels.get)
        rows.append({
            "wallet": wallet,
            "slug": slug,
            "market_type": classify_market(acc.title, slug).market_type,
            "cash_out": cash_out,
            "cash_in": cash_in,
            "fees": fees,
            "net_pnl": cash_in - cash_out - fees,
            "buy_usdc": acc.buy_usdc,
            "sell_usdc": acc.sell_usdc,
            "merge_usdc": acc.merge_usdc,
            "redeem_usdc": acc.redeem_usdc,
            "n_buys": acc.n_buys,
            "n_sells": acc.n_sells,
            "n_merges": acc.n_merges,
            "n_redeems": acc.n_redeems,
            "exit_mode": exit_mode,
        })

    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(rows)[cols].reset_index(drop=True)


def _accumulate_fee(acc, tx: str, fee_by_tx: dict[str, float],
                    size: float, price: float) -> None:
    """Add the fee for one record to its market accumulator.

    On-chain fees are per-transaction: if several of a market's records share
    one ``tx_hash`` the receipt's ``fee_usdc`` is added only once. When no
    receipt was decoded for the tx, an estimated taker fee is added for order
    fills (``size > 0``); MERGE/REDEEM CTF calls get no estimate.
    """
    if tx and tx in fee_by_tx:
        if tx not in acc.seen_fee_tx:
            acc.seen_fee_tx.add(tx)
            acc.fees += fee_by_tx[tx]
    elif size > 0 and 0.0 < price < 1.0:
        acc.est_fees += size * TAKER_FEE_RATE * price * (1.0 - price)


# --------------------------------------------------------------------------
# D. Per-wallet summary
# --------------------------------------------------------------------------
def _pct(numerator: float, denominator: float) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


def _safe_quantile(series: pd.Series, q: float) -> float:
    s = series.dropna()
    return float(s.quantile(q)) if len(s) else float("nan")


def summarize_wallet(
    wallet: str,
    manifest_entry: dict,
    activity: list[dict],
    roundtrips: pd.DataFrame,
    onchain_roles: list[dict],
    positions: list[dict],
    leaderboards: Optional[dict[str, list[dict]]] = None,
    market_cashflow: Optional[pd.DataFrame] = None,
) -> dict:
    """One row of per-wallet metrics. Pure: dict in -> dict out.

    ``manifest_entry`` is ``manifest["wallets"][wallet]``. ``leaderboards`` (if
    given) is the output of :func:`load_leaderboards`; it supplies per-board
    pnl/vol/user_name, which the manifest's ``boards`` list does not carry.
    ``market_cashflow`` is the output of :func:`reconstruct_market_cashflow`
    for this wallet; if omitted it is recomputed from ``activity``.
    """
    manifest_entry = manifest_entry or {}
    leaderboards = leaderboards or {}
    out: dict = {"proxy_wallet": wallet}

    # ---- Identity & board appearances ------------------------------------
    boards = manifest_entry.get("boards", []) or []
    user_name = ""
    for period in ("MONTH", "WEEK", "ALL"):
        out[f"on_{period.lower()}_board"] = False
        out[f"rank_{period.lower()}"] = np.nan
        out[f"lb_pnl_{period.lower()}"] = np.nan
        out[f"lb_vol_{period.lower()}"] = np.nan
    for b in boards:
        period = str(b.get("period", "")).upper()
        if period not in ("MONTH", "WEEK", "ALL"):
            continue
        lk = period.lower()
        out[f"on_{lk}_board"] = True
        out[f"rank_{lk}"] = b.get("rank")
        # Join the date-stamped leaderboard snapshot for pnl / vol / name.
        for entry in leaderboards.get(period, []):
            if str(entry.get("proxy_wallet", "")).lower() == wallet.lower():
                out[f"lb_pnl_{lk}"] = entry.get("pnl")
                out[f"lb_vol_{lk}"] = entry.get("vol")
                if not user_name and entry.get("user_name"):
                    user_name = entry.get("user_name")
                break
    out["user_name"] = user_name or wallet
    out["n_boards"] = int(sum(1 for b in boards
                              if str(b.get("period", "")).upper()
                              in ("MONTH", "WEEK", "ALL")))
    out["activity_truncated"] = bool(manifest_entry.get("truncated", False))
    out["activity_records"] = int(manifest_entry.get("activity_records", 0))
    out["manifest_error"] = manifest_entry.get("error")
    out["n_open_positions"] = len(positions or [])

    # ---- Market-type breakdown (share of USDC trade volume) --------------
    trades = [r for r in (activity or [])
              if r.get("type") == "TRADE" and r.get("side") == "BUY"]
    vol_by_bucket: dict[str, float] = {c: 0.0 for c in _PCT_VOL_COL.values()}
    classified_by_title = 0
    for r in trades:
        mc = classify_market(r.get("title", ""), r.get("slug", ""))
        vol_by_bucket[_PCT_VOL_COL[mc.market_type]] += float(
            r.get("usdc_size") or 0.0)
        if mc.method != "by_slug":
            classified_by_title += 1
    total_vol = sum(vol_by_bucket.values())
    for col in _PCT_VOL_COL.values():
        out[col] = _pct(vol_by_bucket[col], total_vol)
    out["total_buy_usdc"] = total_vol
    out["n_buy_trades"] = len(trades)
    out["pct_trades_classified_by_title"] = _pct(classified_by_title,
                                                 len(trades))

    # ---- Round-trip stats --------------------------------------------------
    # Directional round-trips = closed by a SELL or by RESOLUTION. MERGE
    # round-trips are position unwinds with no directional PnL (see
    # reconstruct_roundtrips docstring) and are reported separately, not folded
    # into win_rate / PnL stats. "open" lots are excluded entirely.
    rt = roundtrips if roundtrips is not None else pd.DataFrame(
        columns=ROUNDTRIP_COLUMNS)
    realized = (rt[rt["exit_kind"].isin(["sell", "resolution"])]
                if len(rt) else rt)
    out["n_roundtrips"] = int(len(realized))
    out["n_merge_unwinds"] = (int((rt["exit_kind"] == "merge").sum())
                              if len(rt) else 0)
    out["n_open_lots"] = int((rt["exit_kind"] == "open").sum()) if len(rt) else 0
    if len(realized):
        pnl = realized["net_pnl"]
        out["win_rate"] = float((pnl > 0).mean())
        out["median_hold_seconds"] = float(realized["hold_seconds"].median())
        out["p10_hold_seconds"] = _safe_quantile(realized["hold_seconds"], 0.10)
        out["p90_hold_seconds"] = _safe_quantile(realized["hold_seconds"], 0.90)
        out["mean_pnl_per_trade"] = float(pnl.mean())
        out["median_pnl_per_trade"] = float(pnl.median())
        out["total_realized_pnl"] = float(pnl.sum())
        out["pnl_p10"] = _safe_quantile(pnl, 0.10)
        out["pnl_p25"] = _safe_quantile(pnl, 0.25)
        out["pnl_p50"] = _safe_quantile(pnl, 0.50)
        out["pnl_p75"] = _safe_quantile(pnl, 0.75)
        out["pnl_p90"] = _safe_quantile(pnl, 0.90)
        out["max_single_win"] = float(pnl.max())
        out["max_single_loss"] = float(pnl.min())
        median = pnl.median()
        out["pnl_mean_over_median"] = (
            float(pnl.mean() / median) if median not in (0, 0.0)
            else float("nan"))
        # Of directional round-trips, the fraction that closed by holding to
        # resolution vs actively selling out.
        out["resolution_exit_frac"] = float(
            (realized["exit_kind"] == "resolution").mean())
    else:
        for k in ("win_rate", "median_hold_seconds", "p10_hold_seconds",
                  "p90_hold_seconds", "mean_pnl_per_trade",
                  "median_pnl_per_trade", "total_realized_pnl",
                  "pnl_p10", "pnl_p25", "pnl_p50", "pnl_p75", "pnl_p90",
                  "max_single_win", "max_single_loss", "pnl_mean_over_median",
                  "resolution_exit_frac"):
            out[k] = float("nan")

    # ---- Market-level cash-flow PnL (the honest realized PnL) -------------
    # Nets every USDC movement per market (buy cost vs sell/merge/redeem
    # proceeds minus fees). Unlike the FIFO round-trip PnL above, this counts
    # mint-buy-merge cycles, so it captures how a merge-heavy wallet earns.
    cf = market_cashflow
    if cf is None:
        cf = reconstruct_market_cashflow(activity, onchain_roles, wallet=wallet)
    out["n_markets"] = int(len(cf))
    if len(cf):
        out["total_realized_pnl_cashflow"] = float(cf["net_pnl"].sum())
        # PnL bucketed by each market's dominant exit channel.
        by_mode = cf.groupby("exit_mode")["net_pnl"].sum()
        out["pnl_via_merge"] = float(by_mode.get("merge", 0.0))
        out["pnl_via_redeem"] = float(by_mode.get("redeem", 0.0))
        out["pnl_via_sell"] = float(by_mode.get("sell", 0.0))
        out["n_markets_merge_exit"] = int((cf["exit_mode"] == "merge").sum())
        out["n_markets_redeem_exit"] = int((cf["exit_mode"] == "redeem").sum())
        out["n_markets_sell_exit"] = int((cf["exit_mode"] == "sell").sum())
        # Wallet-total event counts.
        out["n_merges"] = int(cf["n_merges"].sum())
        out["n_redeems"] = int(cf["n_redeems"].sum())
        out["n_sells"] = int(cf["n_sells"].sum())
        total_cash_in = float(cf["cash_in"].sum())
        out["total_cash_in"] = total_cash_in
        out["total_cash_out"] = float(cf["cash_out"].sum())
        out["total_fees_cashflow"] = float(cf["fees"].sum())
        # Share of all incoming cash that arrived via merges — the headline
        # "is this a mint-merge wallet?" signal.
        out["merge_share"] = _pct(float(cf["merge_usdc"].sum()), total_cash_in)
        out["redeem_share"] = _pct(float(cf["redeem_usdc"].sum()),
                                   total_cash_in)
        out["sell_share"] = _pct(float(cf["sell_usdc"].sum()), total_cash_in)
    else:
        for k in ("total_realized_pnl_cashflow", "pnl_via_merge",
                  "pnl_via_redeem", "pnl_via_sell", "total_cash_in",
                  "total_cash_out", "total_fees_cashflow", "merge_share",
                  "redeem_share", "sell_share"):
            out[k] = float("nan")
        for k in ("n_markets_merge_exit", "n_markets_redeem_exit",
                  "n_markets_sell_exit", "n_merges", "n_redeems", "n_sells"):
            out[k] = 0

    # Reconcile cash-flow PnL against the MONTH-board leaderboard pnl. The
    # 4000-record activity cap means a truncated wallet's history is partial,
    # so this ratio is a sanity check, not an equality — surface it as-is.
    lb_pnl_month = out.get("lb_pnl_month")
    if (lb_pnl_month is not None and not pd.isna(lb_pnl_month)
            and lb_pnl_month not in (0, 0.0)
            and not pd.isna(out.get("total_realized_pnl_cashflow",
                                    float("nan")))):
        out["pnl_reconcile_ratio"] = float(
            out["total_realized_pnl_cashflow"] / lb_pnl_month)
    else:
        out["pnl_reconcile_ratio"] = float("nan")

    # ---- Sizing (per BUY trade usdc_size) --------------------------------
    sizes = pd.Series([float(r.get("usdc_size") or 0.0) for r in trades],
                      dtype="float64")
    if len(sizes):
        out["median_usdc_size"] = float(sizes.median())
        out["p90_usdc_size"] = _safe_quantile(sizes, 0.90)
        mean = sizes.mean()
        out["size_cv"] = (float(sizes.std() / mean) if mean
                          else float("nan"))
    else:
        out["median_usdc_size"] = float("nan")
        out["p90_usdc_size"] = float("nan")
        out["size_cv"] = float("nan")

    # ---- Maker / taker (from on-chain sampled txs) -----------------------
    maker_fills = taker_fills = 0
    n_decoded = n_absent = 0
    total_fee = 0.0
    for r in (onchain_roles or []):
        role = r.get("role")
        n_fills = int(r.get("n_fills") or 0)
        if role == "absent":
            n_absent += 1
            continue
        n_decoded += 1
        total_fee += float(r.get("fee_usdc") or 0.0)
        if role == "maker":
            maker_fills += n_fills
        elif role == "taker":
            taker_fills += n_fills
        elif role == "both":
            # Counted on both sides — n_fills is the wallet's appearances.
            maker_fills += n_fills
            taker_fills += n_fills
    denom = maker_fills + taker_fills
    out["maker_fill_frac"] = (float(maker_fills) / denom if denom
                              else float("nan"))
    out["n_txs_decoded"] = n_decoded
    out["n_txs_absent"] = n_absent
    out["total_onchain_fee_usdc"] = total_fee

    # ---- Activity shape --------------------------------------------------
    if trades:
        days = {pd.Timestamp(r.get("timestamp"), unit="s", tz="UTC").date()
                for r in trades if r.get("timestamp")}
        out["n_active_days"] = len(days)
        out["trades_per_active_day"] = (_pct(len(trades), len(days))
                                        if days else float("nan"))
    else:
        out["n_active_days"] = 0
        out["trades_per_active_day"] = float("nan")

    return out


def analyze_all(
    manifest: Optional[dict] = None,
    leaderboards: Optional[dict[str, list[dict]]] = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Run :func:`summarize_wallet` over every wallet in the manifest.

    Returns ``(summary_df, roundtrips_df, market_cashflow_df)``:

    * ``summary_df`` — one row per wallet, the per-wallet metrics.
    * ``roundtrips_df`` — every wallet's FIFO round-trips concatenated (the
      ``wallet`` column identifies the owner). Source for hold-time and
      directional per-trade distributions.
    * ``market_cashflow_df`` — every wallet's per-market cash-flow rows
      concatenated. Source for honest total realized PnL incl. mint-merge.
    """
    if manifest is None:
        manifest = load_manifest()
    if leaderboards is None:
        leaderboards = load_leaderboards()

    wallets = (manifest or {}).get("wallets", {}) or {}
    summary_rows: list[dict] = []
    all_rt: list[pd.DataFrame] = []
    all_cf: list[pd.DataFrame] = []

    for wallet, entry in wallets.items():
        activity = load_activity(wallet)
        positions = load_positions(wallet)
        onchain = load_onchain_roles(wallet)
        rt = reconstruct_roundtrips(activity, wallet=wallet,
                                    onchain_roles=onchain)
        cf = reconstruct_market_cashflow(activity, onchain, wallet=wallet)
        if len(rt):
            all_rt.append(rt)
        if len(cf):
            all_cf.append(cf)
        summary_rows.append(summarize_wallet(
            wallet, entry, activity, rt, onchain, positions, leaderboards,
            market_cashflow=cf,
        ))
        log.info("wallet_analyzed", wallet=wallet,
                 n_roundtrips=int((rt["exit_kind"] != "open").sum())
                 if len(rt) else 0,
                 n_markets=len(cf))

    summary_df = pd.DataFrame(summary_rows)
    roundtrips_df = (pd.concat(all_rt, ignore_index=True) if all_rt
                     else pd.DataFrame(columns=ROUNDTRIP_COLUMNS))
    market_cashflow_df = (pd.concat(all_cf, ignore_index=True) if all_cf
                          else pd.DataFrame(columns=MARKET_CASHFLOW_COLUMNS))
    return summary_df, roundtrips_df, market_cashflow_df


def write_derived(summary_df: pd.DataFrame, roundtrips_df: pd.DataFrame,
                  market_cashflow_df: Optional[pd.DataFrame] = None,
                  directory: Optional[Path] = None,
                  ) -> tuple[Path, Path, Path]:
    """Convenience: persist the analysis output as parquet for Task 2.2.

    Writes ``wallet_summary.parquet``, ``roundtrips.parquet`` and
    ``market_cashflow.parquet`` under ``data/wallets/derived/`` (gitignored).
    Returns the three paths.
    """
    directory = Path(directory) if directory is not None else DERIVED_DIR
    directory.mkdir(parents=True, exist_ok=True)
    summary_path = directory / "wallet_summary.parquet"
    rt_path = directory / "roundtrips.parquet"
    cf_path = directory / "market_cashflow.parquet"
    if market_cashflow_df is None:
        market_cashflow_df = pd.DataFrame(columns=MARKET_CASHFLOW_COLUMNS)
    summary_df.to_parquet(summary_path, index=False)
    roundtrips_df.to_parquet(rt_path, index=False)
    market_cashflow_df.to_parquet(cf_path, index=False)
    return summary_path, rt_path, cf_path


def main() -> None:
    """Analyze the cached wallets and write the derived parquet files."""
    structlog.configure()
    manifest = load_manifest()
    n = len((manifest or {}).get("wallets", {}) or {})
    log.info("analyze_start", n_wallets=n)
    summary_df, roundtrips_df, cashflow_df = analyze_all(manifest)
    summary_path, rt_path, cf_path = write_derived(
        summary_df, roundtrips_df, cashflow_df)
    log.info("analyze_done",
             n_wallets=len(summary_df),
             n_roundtrips=len(roundtrips_df),
             n_markets=len(cashflow_df),
             summary_path=str(summary_path),
             roundtrips_path=str(rt_path),
             cashflow_path=str(cf_path))
    print(f"Analyzed {len(summary_df)} wallets, "
          f"{len(roundtrips_df)} round-trips, "
          f"{len(cashflow_df)} market cash-flows.")
    print(f"  -> {summary_path}")
    print(f"  -> {rt_path}")
    print(f"  -> {cf_path}")


if __name__ == "__main__":
    main()
