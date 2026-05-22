"""Rebuild corrected outcome labels for the May 15m windows.

Background
----------
A discovery bug (now fixed) froze each window's ``start_price`` at the Coinbase
spot sampled ~30 min *before* the window opened (see ``docs/research/
phase0_audit.md`` Task 8c). Consequently ``start_price``, ``move_pct``,
``outcome`` and ``outcome_up`` in ``ticks_15m.parquet`` / ``windows.parquet`` /
``data/outcomes.csv`` are all corrupt for the May data.

This module rebuilds trustworthy labels two independent ways and reconciles
them:

Way 1 - Coinbase reconstruction (we have the data)
    For each 15m window, ``true_strike`` = ``coinbase_price`` at the earliest
    tick (smallest ``seconds_into_window``), ``true_end`` = ``coinbase_price``
    at the latest tick. ``recon_outcome`` = "Up" if ``true_end > true_strike``.

Way 2 - Polymarket gamma API ground truth
    For each slug, query ``https://gamma-api.polymarket.com/events?slug=<slug>``.
    A resolved market exposes ``outcomePrices`` (paired with ``outcomes``):
    ``["1","0"]`` means Up won, ``["0","1"]`` means Down won. The market-level
    ``umaResolutionStatus`` == "resolved" and ``closed`` == True confirm it.
    The event's ``eventMetadata`` additionally carries ``priceToBeat`` (the true
    Chainlink strike) and ``finalPrice`` (the true Chainlink settlement) - we
    capture these for cross-checking but the *outcome* is taken from
    ``outcomePrices`` which is the canonical resolved field.

Reconcile
    ``authoritative_outcome_up`` prefers the API ground truth where available,
    falling back to the Coinbase reconstruction otherwise. ``true_strike`` is
    always kept from the Coinbase reconstruction (the API does not expose a
    Coinbase strike - it exposes the Chainlink ``priceToBeat`` instead, on a
    different feed; we need a Coinbase-consistent strike for ``move_pct`` /
    proximity features computed off the Coinbase tick stream).

Outputs
-------
``run()``  -> writes ``data/research/corrected_labels.parquet``
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Optional

import pandas as pd

try:  # optional - only needed for the API fetch
    import aiohttp
except ImportError:  # pragma: no cover
    aiohttp = None


_INPUT_PATH = "data/research/ticks_15m.parquet"
_OUTPUT_PATH = "data/research/corrected_labels.parquet"
_GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"

# Be polite to the public API: small concurrency + a brief delay per batch.
_CONCURRENCY = 6
_BATCH_DELAY_SEC = 0.15
_REQUEST_TIMEOUT_SEC = 15
_MAX_RETRIES = 3


# --------------------------------------------------------------------------
# Way 1 - Coinbase reconstruction
# --------------------------------------------------------------------------
def reconstruct_from_coinbase(ticks: pd.DataFrame) -> pd.DataFrame:
    """One row per 15m slug with the Coinbase-reconstructed strike / end / outcome.

    Parameters
    ----------
    ticks:
        Raw 15m tick DataFrame. Must contain ``market_slug``, ``symbol``,
        ``seconds_into_window`` and ``coinbase_price``.

    Returns
    -------
    DataFrame with columns:
        slug, symbol, true_strike, true_end, open_offset_sec, close_offset_sec,
        recon_outcome, recon_outcome_up, n_ticks
    """
    t = ticks.copy()
    slug_col = "slug" if "slug" in t.columns else "market_slug"
    # Restrict to 15m slugs defensively (ticks_15m should already be all-15m).
    t = t[t[slug_col].astype(str).str.contains("-updown-15m-")]
    # A handful of tick rows are corrupt (NaN seconds_into_window / NaN
    # coinbase_price) - drop them so strike/end come from valid ticks only.
    t = t.dropna(subset=["seconds_into_window", "coinbase_price"])
    t = t.sort_values([slug_col, "seconds_into_window"], kind="mergesort")

    rows = []
    for slug, g in t.groupby(slug_col, sort=True):
        g = g.reset_index(drop=True)
        first = g.iloc[0]
        last = g.iloc[-1]
        open_offset = int(first["seconds_into_window"])      # 0 ideally
        close_offset = int(900 - last["seconds_into_window"])  # 1 ideally
        true_strike = float(first["coinbase_price"])
        true_end = float(last["coinbase_price"])
        recon_up = true_end > true_strike
        rows.append({
            "slug": str(slug),
            "symbol": str(first["symbol"]),
            "true_strike": true_strike,
            "true_end": true_end,
            "open_offset_sec": open_offset,
            "close_offset_sec": close_offset,
            "recon_outcome": "Up" if recon_up else "Down",
            "recon_outcome_up": 1.0 if recon_up else 0.0,
            "n_ticks": int(len(g)),
        })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# Way 2 - Polymarket gamma API ground truth
# --------------------------------------------------------------------------
def _parse_event_outcome(event_doc: dict) -> dict:
    """Extract the resolved outcome from a gamma /events document.

    Returns a dict with keys:
        api_outcome   : "Up" / "Down" / None
        api_outcome_up: 1.0 / 0.0 / None
        api_status    : a short string describing what happened
        api_price_to_beat : float or None  (Chainlink strike, eventMetadata)
        api_final_price   : float or None  (Chainlink settlement, eventMetadata)
    """
    blank = {
        "api_outcome": None, "api_outcome_up": None,
        "api_price_to_beat": None, "api_final_price": None,
    }
    if not event_doc:
        return {**blank, "api_status": "not_found"}

    markets = event_doc.get("markets") or []
    if not markets:
        return {**blank, "api_status": "no_market"}
    market = markets[0]

    # eventMetadata carries the Chainlink strike / settlement (cross-check only)
    meta = event_doc.get("eventMetadata") or {}
    price_to_beat = meta.get("priceToBeat")
    final_price = meta.get("finalPrice")

    closed = bool(market.get("closed"))
    uma_status = str(market.get("umaResolutionStatus") or "").lower()

    outcomes_raw = market.get("outcomes")
    prices_raw = market.get("outcomePrices")
    try:
        outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
    except (json.JSONDecodeError, TypeError):
        outcomes = None
    try:
        prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
    except (json.JSONDecodeError, TypeError):
        prices = None

    if not outcomes or not prices or len(outcomes) != len(prices):
        return {
            **blank, "api_status": "no_outcome_prices",
            "api_price_to_beat": price_to_beat, "api_final_price": final_price,
        }

    # Find the index of the "Up" outcome and read its resolved price.
    up_idx = None
    for i, o in enumerate(outcomes):
        if str(o).strip().lower() in ("up", "yes"):
            up_idx = i
            break
    if up_idx is None:
        return {
            **blank, "api_status": "no_up_outcome",
            "api_price_to_beat": price_to_beat, "api_final_price": final_price,
        }

    try:
        up_price = float(prices[up_idx])
    except (ValueError, TypeError):
        return {
            **blank, "api_status": "bad_price",
            "api_price_to_beat": price_to_beat, "api_final_price": final_price,
        }

    # A resolved up/down market has outcomePrices in {0,1}. Anything strictly
    # between is an unresolved / mid-market quote - treat as unresolved.
    if up_price >= 0.99:
        api_up = 1.0
    elif up_price <= 0.01:
        api_up = 0.0
    else:
        return {
            **blank, "api_status": f"unresolved(up_price={up_price})",
            "api_price_to_beat": price_to_beat, "api_final_price": final_price,
        }

    status = "resolved"
    if not closed or uma_status not in ("resolved", ""):
        # Prices look decided but flags disagree - flag it, still use the price.
        status = f"price_decided_flags=closed:{closed},uma:{uma_status or 'n/a'}"

    return {
        "api_outcome": "Up" if api_up == 1.0 else "Down",
        "api_outcome_up": api_up,
        "api_status": status,
        "api_price_to_beat": price_to_beat,
        "api_final_price": final_price,
    }


async def _fetch_one(session, slug: str, sem: asyncio.Semaphore) -> dict:
    """Fetch one slug's resolved outcome from the gamma /events endpoint."""
    params = {"slug": slug}
    async with sem:
        last_err = None
        for attempt in range(_MAX_RETRIES):
            try:
                async with session.get(_GAMMA_EVENTS_URL, params=params) as resp:
                    if resp.status == 404:
                        return {"slug": slug, "api_status": "http_404",
                                "api_outcome": None, "api_outcome_up": None,
                                "api_price_to_beat": None, "api_final_price": None}
                    resp.raise_for_status()
                    data = await resp.json()
                event = data[0] if isinstance(data, list) and data else None
                parsed = _parse_event_outcome(event)
                parsed["slug"] = slug
                await asyncio.sleep(_BATCH_DELAY_SEC)
                return parsed
            except Exception as exc:  # noqa: BLE001 - network errors, retry
                last_err = exc
                await asyncio.sleep(0.5 * (attempt + 1))
        return {"slug": slug, "api_status": f"error:{type(last_err).__name__}",
                "api_outcome": None, "api_outcome_up": None,
                "api_price_to_beat": None, "api_final_price": None}


async def _fetch_all(slugs: list[str]) -> pd.DataFrame:
    """Fetch resolved outcomes for all slugs (polite concurrency)."""
    if aiohttp is None:  # pragma: no cover
        raise RuntimeError("aiohttp is required for the API fetch")
    sem = asyncio.Semaphore(_CONCURRENCY)
    timeout = aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT_SEC)
    results = []
    async with aiohttp.ClientSession(timeout=timeout) as session:
        tasks = [_fetch_one(session, s, sem) for s in slugs]
        done = 0
        for coro in asyncio.as_completed(tasks):
            results.append(await coro)
            done += 1
            if done % 200 == 0:
                print(f"  API fetch: {done}/{len(slugs)} slugs")
    print(f"  API fetch: {done}/{len(slugs)} slugs (done)")
    return pd.DataFrame(results)


def fetch_api_outcomes(slugs: list[str]) -> pd.DataFrame:
    """Synchronous wrapper around the async gamma /events fetch.

    Returns a DataFrame with columns:
        slug, api_outcome, api_outcome_up, api_status,
        api_price_to_beat, api_final_price
    """
    return asyncio.run(_fetch_all(slugs))


# --------------------------------------------------------------------------
# Reconcile
# --------------------------------------------------------------------------
def reconcile(recon: pd.DataFrame, api: pd.DataFrame) -> pd.DataFrame:
    """Merge the two label sources and pick the authoritative label per slug.

    Authoritative outcome = API ground truth where available, else the Coinbase
    reconstruction. ``true_strike`` is always kept from the reconstruction.
    """
    merged = recon.merge(api, on="slug", how="left")

    has_api = merged["api_outcome_up"].notna()
    merged["authoritative_outcome_up"] = merged["recon_outcome_up"].where(
        ~has_api, merged["api_outcome_up"]
    )
    merged["label_source"] = has_api.map({True: "api", False: "recon"})

    cols = [
        "slug", "symbol", "true_strike", "true_end",
        "recon_outcome", "recon_outcome_up",
        "api_outcome", "api_outcome_up",
        "authoritative_outcome_up", "label_source",
        "open_offset_sec", "close_offset_sec",
        "api_price_to_beat", "api_final_price", "api_status", "n_ticks",
    ]
    return merged[cols].sort_values("slug").reset_index(drop=True)


def print_summary(labels: pd.DataFrame) -> None:
    """Print the agreement / coverage summary to stdout."""
    n = len(labels)
    print()
    print(f"=== corrected_labels summary ({n} 15m windows) ===")

    src = labels["label_source"].value_counts().to_dict()
    print(f"label source: api={src.get('api', 0)}  recon={src.get('recon', 0)}")

    both = labels[labels["api_outcome_up"].notna() & labels["recon_outcome_up"].notna()]
    if len(both):
        agree = (both["api_outcome_up"] == both["recon_outcome_up"]).mean()
        print(f"Coinbase-recon vs API agreement: {agree:.4f} ({len(both)} comparable)")
        for sym, g in both.groupby("symbol"):
            a = (g["api_outcome_up"] == g["recon_outcome_up"]).mean()
            print(f"  {sym}: {a:.4f} ({len(g)})")

    # timing gap
    print(f"open offset (s): median={labels['open_offset_sec'].median():.0f} "
          f"p90={labels['open_offset_sec'].quantile(0.9):.0f} "
          f"max={labels['open_offset_sec'].max():.0f}")
    print(f"close offset (s): median={labels['close_offset_sec'].median():.0f} "
          f"p90={labels['close_offset_sec'].quantile(0.9):.0f} "
          f"max={labels['close_offset_sec'].max():.0f}")


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
def run() -> pd.DataFrame:
    """Load ticks, reconstruct + fetch + reconcile, write the parquet."""
    t0 = time.time()
    print(f"Loading {_INPUT_PATH} ...")
    ticks = pd.read_parquet(
        _INPUT_PATH,
        columns=["market_slug", "symbol", "seconds_into_window", "coinbase_price"],
    )
    print(f"  {len(ticks):,} ticks loaded.")

    print("Way 1 - reconstructing strikes/outcomes from Coinbase ...")
    recon = reconstruct_from_coinbase(ticks)
    print(f"  {len(recon)} 15m windows reconstructed.")

    print("Way 2 - fetching resolved outcomes from Polymarket gamma API ...")
    api = fetch_api_outcomes(recon["slug"].tolist())
    got = api["api_outcome_up"].notna().sum()
    print(f"  API returned a resolved outcome for {got}/{len(api)} slugs.")

    print("Reconciling ...")
    labels = reconcile(recon, api)

    print(f"Writing {_OUTPUT_PATH} ...")
    labels.to_parquet(_OUTPUT_PATH, index=False)

    print_summary(labels)
    print(f"Done in {time.time() - t0:.1f}s.")
    return labels


if __name__ == "__main__":
    run()
