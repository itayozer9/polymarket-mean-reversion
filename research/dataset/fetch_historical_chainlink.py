"""(a) Backfill the Chainlink oracle feed for the days BEFORE our live collector
started (the collector's feed begins 2026-05-22). Reads historical rounds directly
from the on-chain AggregatorV3Interface via getRoundData (the contract stores past
rounds — no archive node needed), batched JSON-RPC for speed.

Writes data/live_chainlink/<symbol>_hist.csv.gz in the same (timestamp_ms, symbol,
price) shape the live feed uses, so research/dataset/chainlink_merge.load_chainlink
picks it up automatically and the edges can be Chainlink-settled back to May 15.

Run: uv run python -m research.dataset.fetch_historical_chainlink 2026-05-15 2026-05-22
"""
from __future__ import annotations
import datetime as dt
import gzip
import json
import os
import sys
import time
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(REPO, "data", "live_chainlink")
RPC = os.environ.get("POLYGON_RPC_URL", "https://polygon.gateway.tenderly.co")
FEEDS = {"btc": "0xc907E116054Ad103354f2D350FD2514433D57F6f",
         "eth": "0xF9680D99D6C9589e2a93a78A04A279e509205945",
         "sol": "0x10C8264C0935b3B9870013e057f330Ff3e9C56dC",
         "xrp": "0x785ba89291f676b5386652eB12b30cF361020694"}
DEC = 8
SEL_LATEST = "0xfeaf968c"
SEL_GET = "0x9a6fc8f5"  # getRoundData(uint80)


def _words(res):
    if not res or res == "0x" or len(res) < 2 + 5 * 64:
        return None
    return [int(res[2 + i * 64:2 + (i + 1) * 64], 16) for i in range(5)]


def _rpc(payload):
    req = urllib.request.Request(RPC, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    for attempt in range(6):
        try:
            return json.loads(urllib.request.urlopen(req, timeout=40).read())
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    return None


def _call1(to, data):
    r = _rpc({"jsonrpc": "2.0", "id": 1, "method": "eth_call",
              "params": [{"to": to, "data": data}, "latest"]})
    return _words(r.get("result")) if r else None


def _round_data(rid):
    return SEL_GET + hex(rid)[2:].rjust(64, "0")


def _batch_rounds(feed, phase, aggrs):
    payload = [{"jsonrpc": "2.0", "id": a, "method": "eth_call",
                "params": [{"to": feed, "data": _round_data((phase << 64) | a)}, "latest"]}
               for a in aggrs]
    res = _rpc(payload)
    if not res:
        return {a: None for a in aggrs}
    byid = {r["id"]: _words(r.get("result")) for r in res if "id" in r}
    return {a: byid.get(a) for a in aggrs}


def _find_aggr_at(feed, phase, hi, target):
    lo, best = 1, 1
    while lo <= hi:
        mid = (lo + hi) // 2
        r = _call1(feed, _round_data((phase << 64) | mid))
        if not r or r[3] == 0:
            lo = mid + 1
            continue
        if r[3] <= target:
            best, lo = mid, mid + 1
        else:
            hi = mid - 1
    return best


def fetch(feed, t0, t1, batch=120):
    lr = _call1(feed, SEL_LATEST)
    phase, cur = lr[0] >> 64, lr[0] & ((1 << 64) - 1)
    start = _find_aggr_at(feed, phase, cur, t0)
    rows, aggr = [], start
    while aggr <= cur:
        chunk = list(range(aggr, min(aggr + batch, cur + 1)))
        got = _batch_rounds(feed, phase, chunk)
        stop = False
        for a in chunk:
            r = got.get(a)
            if not r or r[3] == 0:
                continue
            if r[3] > t1:
                stop = True
                break
            if r[3] >= t0:
                rows.append((r[3] * 1000, r[1] / 10 ** DEC))
        if stop:
            break
        aggr += batch
    return rows


def run(d0, d1):
    os.makedirs(OUT, exist_ok=True)
    t0 = int(dt.datetime.strptime(d0, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp())
    t1 = int(dt.datetime.strptime(d1, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp())
    print(f"backfill Chainlink {d0} .. {d1}  via {RPC}")
    for sym, feed in FEEDS.items():
        t = time.time()
        rows = fetch(feed, t0, t1)
        path = os.path.join(OUT, f"{sym}_hist_{d0}_to_{d1}.csv.gz")
        with gzip.open(path, "wt") as f:
            f.write("timestamp_ms,symbol,price\n")
            for ms, px in rows:
                f.write(f"{int(ms)},{sym},{px}\n")
        span = f"{dt.datetime.utcfromtimestamp(rows[0][0]/1000):%m-%d %H:%M}..{dt.datetime.utcfromtimestamp(rows[-1][0]/1000):%m-%d %H:%M}" if rows else "EMPTY"
        print(f"  {sym}: {len(rows):>6} rounds  [{span}]  {time.time()-t:.0f}s -> {os.path.basename(path)}")


if __name__ == "__main__":
    a = sys.argv[1:] or ["2026-05-15", "2026-05-22"]
    run(a[0], a[1])
