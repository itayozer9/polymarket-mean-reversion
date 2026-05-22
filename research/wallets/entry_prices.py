"""Task 2.2 — buy-price / entry-odds analysis.

The single most important strategy descriptor for a Polymarket prediction-market
trader is *at what price (odds) they buy*. ``analyze.py`` (Task 2.1) computes how
much volume goes to each market type, exit modes, sizing and hold times, but it
never characterises the entry-odds distribution. This module fills that gap.

For a wallet's cached activity it computes, from the BUY trades only:

* the percentiles p10/p25/p50/p75/p90 of the buy ``price`` (= entry odds),
* the volume-weighted mean price (each buy weighted by ``usdc_size``),
* the share of buy USDC volume falling in five odds bands
  ``[0,0.2) [0.2,0.4) [0.4,0.6) [0.6,0.8) [0.8,1.0]``,

both **overall** and **restricted to ``crypto_15m_updown`` markets** — the
markets the live bot targets.

It additionally computes, for ``crypto_15m_updown`` BUYs whose slug is a compact
``<asset>-updown-<tf>-<window_start_ts>`` form, the *entry offset*: how many
seconds into the 15-minute window the buy landed (``buy_ts - window_start_ts``).
The compact slug's trailing token is the window-start unix timestamp, verified
against the real cache. Long-form slugs (``bitcoin-up-or-down-...``) carry no
machine-readable window start, so their buys are skipped for the offset stat
(noted in the returned ``offset_coverage``).

All computation is in UTC / unix seconds. Pure functions: activity list in,
metrics dict out. The report task displays Israel-local time where needed.
"""
from __future__ import annotations

import re
from typing import Optional

import numpy as np
import pandas as pd

from research.wallets.analyze import classify_market

# Compact Up/Down slug: <asset>-updown-<n><unit>-<window_start_unix_ts>.
# The trailing integer is the window-start unix timestamp (verified against the
# cache: buy_ts - that ts lands inside [0, window_length]).
_SLUG_UPDOWN_COMPACT_TS = re.compile(
    r"^(?:btc|eth|sol|xrp)-updown-(\d+)(m|h)-(\d+)$", re.IGNORECASE,
)

# Odds bands for the buy-volume histogram. Right edge of the last band is
# inclusive (a buy at exactly 1.0 is counted in the top band).
ODDS_BANDS = (
    ("band_0_20", 0.0, 0.2),
    ("band_20_40", 0.2, 0.4),
    ("band_40_60", 0.4, 0.6),
    ("band_60_80", 0.6, 0.8),
    ("band_80_100", 0.8, 1.0),
)

# Columns the per-wallet entry-price row carries. ``_15m_`` columns repeat the
# same stats restricted to crypto_15m_updown markets.
_PCTL_KEYS = ("p10", "p25", "p50", "p75", "p90")


def _buy_trades(activity: list[dict]) -> list[dict]:
    """The ``TRADE`` / ``BUY`` records with a usable price and size."""
    out = []
    for r in activity or []:
        if r.get("type") != "TRADE" or r.get("side") != "BUY":
            continue
        price = r.get("price")
        if price is None:
            continue
        try:
            p = float(price)
        except (TypeError, ValueError):
            continue
        if not (0.0 <= p <= 1.0):
            continue
        out.append(r)
    return out


def _window_start_ts(slug: str) -> Optional[int]:
    """Return the window-start unix ts encoded in a compact updown slug, or
    ``None`` if the slug is not a compact updown slug."""
    m = _SLUG_UPDOWN_COMPACT_TS.match((slug or "").strip().lower())
    if not m:
        return None
    return int(m.group(3))


def _price_stats(prices: np.ndarray, weights: np.ndarray, prefix: str) -> dict:
    """Percentiles, volume-weighted mean and band shares for one price array.

    ``prices`` and ``weights`` (= usdc_size) are aligned 1-D arrays. ``prefix``
    is prepended to every output key (``""`` for overall, ``"m15_"`` for 15m).
    Returns NaN percentiles / 0.0 band shares when there are no buys.
    """
    out: dict = {}
    n = int(len(prices))
    out[f"{prefix}n_buys"] = n
    if n == 0:
        for k in _PCTL_KEYS:
            out[f"{prefix}price_{k}"] = float("nan")
        out[f"{prefix}vwap_price"] = float("nan")
        for name, _lo, _hi in ODDS_BANDS:
            out[f"{prefix}{name}"] = 0.0
        out[f"{prefix}buy_usdc"] = 0.0
        return out

    qs = np.quantile(prices, [0.10, 0.25, 0.50, 0.75, 0.90])
    for k, q in zip(_PCTL_KEYS, qs):
        out[f"{prefix}price_{k}"] = float(q)

    total_w = float(weights.sum())
    out[f"{prefix}buy_usdc"] = total_w
    if total_w > 0:
        out[f"{prefix}vwap_price"] = float((prices * weights).sum() / total_w)
    else:  # all-zero usdc_size -> fall back to the simple mean
        out[f"{prefix}vwap_price"] = float(prices.mean())

    for name, lo, hi in ODDS_BANDS:
        if hi >= 1.0:  # last band: inclusive right edge
            mask = (prices >= lo) & (prices <= hi)
        else:
            mask = (prices >= lo) & (prices < hi)
        band_w = float(weights[mask].sum())
        out[f"{prefix}{name}"] = (band_w / total_w) if total_w > 0 else 0.0
    return out


def wallet_entry_prices(wallet: str, activity: list[dict]) -> dict:
    """Per-wallet buy-price / entry-odds metrics for one wallet.

    Returns a dict with ``proxy_wallet`` plus, for *all* BUYs and again for
    ``crypto_15m_updown`` BUYs (``m15_`` prefix):

    * ``price_p10/p25/p50/p75/p90`` — buy-price percentiles,
    * ``vwap_price`` — usdc-weighted mean buy price,
    * ``band_0_20`` ... ``band_80_100`` — share of buy USDC in each odds band,
    * ``n_buys``, ``buy_usdc``.

    Plus 15m entry-timing fields:

    * ``m15_entry_offset_p10/p50/p90`` — seconds into the 15m window the buy
      landed (compact-slug buys only),
    * ``m15_offset_n`` — how many 15m buys had a derivable window start,
    * ``m15_offset_coverage`` — that count over all 15m buys.
    """
    out: dict = {"proxy_wallet": wallet}
    buys = _buy_trades(activity)

    all_prices = np.array([float(r["price"]) for r in buys], dtype="float64")
    all_w = np.array([float(r.get("usdc_size") or 0.0) for r in buys],
                     dtype="float64")
    out.update(_price_stats(all_prices, all_w, prefix=""))

    # crypto_15m_updown subset.
    m15 = [r for r in buys
           if classify_market(r.get("title", ""),
                              r.get("slug", "")).market_type
           == "crypto_15m_updown"]
    m15_prices = np.array([float(r["price"]) for r in m15], dtype="float64")
    m15_w = np.array([float(r.get("usdc_size") or 0.0) for r in m15],
                     dtype="float64")
    out.update(_price_stats(m15_prices, m15_w, prefix="m15_"))

    # 15m entry offset (seconds into the window) from compact slug ts.
    offsets: list[int] = []
    for r in m15:
        ws = _window_start_ts(r.get("slug", ""))
        ts = r.get("timestamp")
        if ws is None or ts is None:
            continue
        off = int(ts) - int(ws)
        # Guard against a mis-parsed slug: a real offset is within a few
        # multiples of the window length. Keep [-60, 3600].
        if -60 <= off <= 3600:
            offsets.append(off)
    if offsets:
        oa = np.array(offsets, dtype="float64")
        out["m15_entry_offset_p10"] = float(np.quantile(oa, 0.10))
        out["m15_entry_offset_p50"] = float(np.quantile(oa, 0.50))
        out["m15_entry_offset_p90"] = float(np.quantile(oa, 0.90))
    else:
        out["m15_entry_offset_p10"] = float("nan")
        out["m15_entry_offset_p50"] = float("nan")
        out["m15_entry_offset_p90"] = float("nan")
    out["m15_offset_n"] = len(offsets)
    out["m15_offset_coverage"] = (len(offsets) / len(m15)) if m15 else 0.0

    return out


def entry_prices_all(manifest: Optional[dict] = None) -> pd.DataFrame:
    """Run :func:`wallet_entry_prices` over every wallet in the manifest.

    Returns one row per wallet. Wallets with no activity file yield a row of
    NaN / zero stats (so the frame always has one row per manifest wallet).
    """
    from research.wallets.analyze import load_activity, load_manifest

    if manifest is None:
        manifest = load_manifest()
    wallets = (manifest or {}).get("wallets", {}) or {}
    rows = [wallet_entry_prices(w, load_activity(w)) for w in wallets]
    return pd.DataFrame(rows)
