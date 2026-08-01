#!/usr/bin/env python3
"""Generate a self-contained HTML report for the active (enabled) strategies.

Reads data/jsonl/<sid>/trades.jsonl for every strategy marked enabled: true in
strategies.yaml, computes per-day PnL / win-rate / equity, and writes a single
standalone HTML file (Chart.js via CDN) with all details + visualizations.

Timestamps are presented in Israel local time (UTC+3, IDT) per project convention.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JSONL = ROOT / "data" / "jsonl"
YAML = ROOT / "strategies.yaml"
OUT = ROOT / "reports" / "active_strategies_report.html"

IL = timezone(timedelta(hours=3))  # Israel IDT (summer); May is always IDT


def enabled_strategy_ids() -> list[str]:
    """Crude YAML scan: collect ids whose block has enabled: true (no pyyaml dep)."""
    ids: list[str] = []
    cur = None
    for line in YAML.read_text().splitlines():
        s = line.strip()
        if s.startswith("- id:"):
            cur = s.split("- id:")[1].strip().strip('"')
        elif s.startswith("enabled:") and cur is not None:
            if s.split("enabled:")[1].strip().lower() == "true":
                ids.append(cur)
            cur = None
    return ids


def strategy_names() -> dict[str, str]:
    """Map id -> human name from the portfolio snapshots."""
    names: dict[str, str] = {}
    for f in (ROOT / "data" / "portfolios").glob("*.json"):
        try:
            d = json.loads(f.read_text())
            names[d["strategy_id"]] = d.get("name", d["strategy_id"])
        except Exception:
            pass
    return names


def load_trades(sid: str) -> list[dict]:
    f = JSONL / sid / "trades.jsonl"
    if not f.exists():
        return []
    out = []
    for line in f.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def il_date(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, IL).strftime("%Y-%m-%d")


def il_dt(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, IL).strftime("%Y-%m-%d %H:%M:%S")


def summarize(trades: list[dict]) -> dict:
    trades = sorted(trades, key=lambda t: t.get("exit_ts_ms") or t.get("entry_ts_ms", 0))
    n = len(trades)
    wins = sum(1 for t in trades if t["pnl"] > 0)
    pnl = sum(t["pnl"] for t in trades)
    fees = sum(t.get("fee_total", 0.0) for t in trades)
    gross_win = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gross_loss = sum(t["pnl"] for t in trades if t["pnl"] < 0)

    # equity curve (cumulative pnl per trade) + max drawdown
    eq = []
    run = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in trades:
        run += t["pnl"]
        peak = max(peak, run)
        max_dd = min(max_dd, run - peak)
        eq.append({"x": (t.get("exit_ts_ms") or t["entry_ts_ms"]), "y": round(run, 4)})

    # per-day breakdown (by Israel exit date)
    days: dict[str, dict] = defaultdict(lambda: {"n": 0, "wins": 0, "pnl": 0.0})
    for t in trades:
        d = il_date(t.get("exit_ts_ms") or t["entry_ts_ms"])
        days[d]["n"] += 1
        days[d]["wins"] += 1 if t["pnl"] > 0 else 0
        days[d]["pnl"] += t["pnl"]
    day_rows = []
    crun = 0.0
    for d in sorted(days):
        x = days[d]
        crun += x["pnl"]
        day_rows.append({
            "date": d, "n": x["n"], "wins": x["wins"],
            "wr": (x["wins"] / x["n"]) if x["n"] else 0.0,
            "pnl": round(x["pnl"], 2), "cum": round(crun, 2),
        })

    # exit-reason + side distributions
    reasons: dict[str, int] = defaultdict(int)
    sides: dict[str, int] = defaultdict(int)
    for t in trades:
        reasons[t.get("exit_reason", "?")] += 1
        sides[t.get("side", "?")] += 1

    return {
        "n": n, "wins": wins, "losses": n - wins,
        "wr": (wins / n) if n else 0.0,
        "pnl": round(pnl, 2), "fees": round(fees, 2),
        "avg": round(pnl / n, 4) if n else 0.0,
        "gross_win": round(gross_win, 2), "gross_loss": round(gross_loss, 2),
        "profit_factor": round(gross_win / abs(gross_loss), 2) if gross_loss else None,
        "max_dd": round(max_dd, 2),
        "best": round(max((t["pnl"] for t in trades), default=0), 2),
        "worst": round(min((t["pnl"] for t in trades), default=0), 2),
        "first": il_dt(trades[0]["entry_ts_ms"]) if trades else "—",
        "last": il_dt(trades[-1].get("exit_ts_ms") or trades[-1]["entry_ts_ms"]) if trades else "—",
        "equity": eq, "days": day_rows,
        "reasons": dict(reasons), "sides": dict(sides),
        "trades": trades,
    }


def build_html(strategies: list[dict], generated: str) -> str:
    payload = json.dumps(strategies)
    return TEMPLATE.replace("__PAYLOAD__", payload).replace("__GENERATED__", generated)


TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Active Strategies — Trade Report</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3.0.0/dist/chartjs-adapter-date-fns.bundle.min.js"></script>
<style>
  :root{
    --bg:#0b0e14; --panel:#141a24; --panel2:#1b2330; --ink:#e6edf3; --mut:#8b98a9;
    --grid:#222c3a; --pos:#3fb950; --neg:#f85149; --acc:#58a6ff; --warn:#d29922;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
    font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
  header{padding:28px 32px 8px;border-bottom:1px solid var(--grid)}
  h1{margin:0 0 4px;font-size:22px;letter-spacing:.2px}
  .sub{color:var(--mut);font-size:13px}
  .wrap{padding:24px 32px;max-width:1280px;margin:0 auto}
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px;margin:8px 0 28px}
  .card{background:var(--panel);border:1px solid var(--grid);border-radius:12px;padding:16px 18px}
  .card .k{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.6px}
  .card .v{font-size:26px;font-weight:650;margin-top:6px}
  .pos{color:var(--pos)} .neg{color:var(--neg)} .acc{color:var(--acc)} .mut{color:var(--mut)}
  section{background:var(--panel);border:1px solid var(--grid);border-radius:14px;
    padding:20px 22px;margin-bottom:24px}
  section h2{margin:0 0 4px;font-size:16px}
  section .desc{color:var(--mut);font-size:12.5px;margin-bottom:16px}
  .chartbox{position:relative;height:300px;margin:8px 0 4px}
  .chartrow{display:grid;grid-template-columns:1fr 1fr;gap:20px}
  @media(max-width:860px){.chartrow{grid-template-columns:1fr}}
  table{width:100%;border-collapse:collapse;font-size:13px;margin-top:6px}
  th,td{padding:7px 10px;text-align:right;border-bottom:1px solid var(--grid);white-space:nowrap}
  th:first-child,td:first-child{text-align:left}
  th{color:var(--mut);font-weight:600;text-transform:uppercase;font-size:11px;letter-spacing:.5px;
    position:sticky;top:0;background:var(--panel)}
  tbody tr:hover{background:var(--panel2)}
  .pill{display:inline-block;padding:2px 9px;border-radius:999px;font-size:11px;font-weight:600}
  .tabbar{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 22px}
  .tab{padding:9px 15px;border:1px solid var(--grid);border-radius:10px;background:var(--panel);
    color:var(--ink);cursor:pointer;font-size:13px;transition:.12s}
  .tab.active{background:var(--acc);color:#04142b;border-color:var(--acc);font-weight:650}
  .tab .pnl{font-size:11px;display:block;margin-top:2px;font-weight:600}
  .badge{font-size:11px;padding:2px 8px;border-radius:6px;background:var(--panel2);color:var(--mut);margin-left:8px}
  details{margin-top:10px}
  summary{cursor:pointer;color:var(--acc);font-size:13px;padding:6px 0}
  .scroll{max-height:440px;overflow:auto;border:1px solid var(--grid);border-radius:10px}
  .dist{display:flex;gap:18px;flex-wrap:wrap;margin-top:6px}
  .dist .item{font-size:12.5px;color:var(--mut)}
  .dist .item b{color:var(--ink)}
  footer{color:var(--mut);font-size:12px;padding:8px 32px 40px;text-align:center}
</style>
</head>
<body>
<header>
  <h1>Active Strategies — Trade Report</h1>
  <div class="sub">Paper trading · the 6 enabled <code>det_*</code> strategies · all times Israel (IDT, UTC+3) · generated __GENERATED__</div>
</header>
<div class="wrap">
  <div class="tabbar" id="tabbar"></div>
  <div id="view"></div>
</div>
<footer>polymarket-mean-reversion · realized PnL from data/jsonl/&lt;sid&gt;/trades.jsonl · fees included</footer>

<script>
const DATA = __PAYLOAD__;
let charts = [];
const fmt = (n,d=2)=> (n>=0?"+":"") + n.toFixed(d);
const money = n => (n>=0?"+$":"-$") + Math.abs(n).toFixed(2);
const cls = n => n>0?"pos":(n<0?"neg":"mut");

function destroyCharts(){ charts.forEach(c=>c.destroy()); charts=[]; }

function card(k,v,c){ return `<div class="card"><div class="k">${k}</div><div class="v ${c||''}">${v}</div></div>`; }

function renderTabs(){
  const bar = document.getElementById('tabbar');
  bar.innerHTML = DATA.map((s,i)=>
    `<div class="tab ${i===0?'active':''}" data-i="${i}">${s.id}
      <span class="pnl ${cls(s.sum.pnl)}">${money(s.sum.pnl)} · ${(s.sum.wr*100).toFixed(0)}% WR</span></div>`
  ).join('');
  bar.querySelectorAll('.tab').forEach(t=>t.onclick=()=>{
    bar.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
    t.classList.add('active');
    renderView(+t.dataset.i);
  });
}

function renderView(i){
  destroyCharts();
  const s = DATA[i], m = s.sum;
  const days = m.days;
  const v = document.getElementById('view');
  const reasons = Object.entries(m.reasons).map(([k,c])=>`<span class="item"><b>${c}</b> ${k}</span>`).join('');
  const sides = Object.entries(m.sides).map(([k,c])=>`<span class="item"><b>${c}</b> ${k}</span>`).join('');

  v.innerHTML = `
    <section>
      <h2>${s.id} <span class="badge">${s.name}</span></h2>
      <div class="desc">${m.first} → ${m.last}</div>
      <div class="cards">
        ${card('Net PnL', money(m.pnl), cls(m.pnl))}
        ${card('Win rate', (m.wr*100).toFixed(1)+'%')}
        ${card('Trades', m.n + ` <span class="mut" style="font-size:14px">(${m.wins}W/${m.losses}L)</span>`)}
        ${card('Avg / trade', fmt(m.avg), cls(m.avg))}
        ${card('Profit factor', m.profit_factor===null?'∞':m.profit_factor)}
        ${card('Max drawdown', money(m.max_dd), 'neg')}
        ${card('Best / worst', `<span class="pos">${money(m.best)}</span> / <span class="neg">${money(m.worst)}</span>`)}
        ${card('Fees paid', '$'+m.fees.toFixed(2),'mut')}
      </div>
      <div class="dist"><span class="item mut">Exit reasons:</span>${reasons}
        <span class="item mut" style="margin-left:16px">Sides:</span>${sides}</div>
    </section>

    <section>
      <h2>Equity curve</h2>
      <div class="desc">Cumulative realized PnL, fees included, one step per closed trade.</div>
      <div class="chartbox"><canvas id="eq"></canvas></div>
    </section>

    <section>
      <h2>Per-day performance</h2>
      <div class="desc">PnL and win rate grouped by Israel calendar date (trade exit).</div>
      <div class="chartrow">
        <div class="chartbox"><canvas id="dpnl"></canvas></div>
        <div class="chartbox"><canvas id="dwr"></canvas></div>
      </div>
      <table>
        <thead><tr><th>Date (IDT)</th><th>Trades</th><th>Wins</th><th>Win rate</th><th>Day PnL</th><th>Cumulative</th></tr></thead>
        <tbody>${days.map(d=>`<tr>
          <td>${d.date}</td><td>${d.n}</td><td>${d.wins}</td>
          <td>${(d.wr*100).toFixed(0)}%</td>
          <td class="${cls(d.pnl)}">${money(d.pnl)}</td>
          <td class="${cls(d.cum)}">${money(d.cum)}</td></tr>`).join('')}
        </tbody>
      </table>
    </section>

    <section>
      <h2>All trades <span class="badge">${m.n}</span></h2>
      <details><summary>Show trade-by-trade detail</summary>
        <div class="scroll"><table>
          <thead><tr><th>#</th><th>Entry (IDT)</th><th>Slug</th><th>Side</th>
            <th>Entry→Exit</th><th>Hold</th><th>Exit reason</th><th>PnL</th></tr></thead>
          <tbody>${s.trades.map((t,j)=>`<tr>
            <td>${j+1}</td>
            <td>${t.entry_il}</td>
            <td style="color:var(--mut)">${t.slug}</td>
            <td>${t.side}</td>
            <td>${t.entry_price.toFixed(3)}→${t.exit_price.toFixed(3)}</td>
            <td>${t.seconds_held!=null?Math.round(t.seconds_held)+'s':'—'}</td>
            <td style="color:var(--mut)">${t.exit_reason||'—'}</td>
            <td class="${cls(t.pnl)}">${money(t.pnl)}</td></tr>`).join('')}
          </tbody>
        </table></div>
      </details>
    </section>`;

  // equity
  charts.push(new Chart(document.getElementById('eq'), {
    type:'line',
    data:{datasets:[{data:m.equity, borderColor:'#58a6ff', backgroundColor:'rgba(88,166,255,.12)',
      fill:true, pointRadius:0, borderWidth:2, tension:.15}]},
    options:baseOpts({time:true})
  }));
  // daily pnl bars
  charts.push(new Chart(document.getElementById('dpnl'), {
    type:'bar',
    data:{labels:days.map(d=>d.date.slice(5)),
      datasets:[{label:'Day PnL',data:days.map(d=>d.pnl),
        backgroundColor:days.map(d=>d.pnl>=0?'rgba(63,185,80,.8)':'rgba(248,81,73,.8)')}]},
    options:baseOpts({title:'Daily PnL ($)'})
  }));
  // daily wr line
  charts.push(new Chart(document.getElementById('dwr'), {
    type:'line',
    data:{labels:days.map(d=>d.date.slice(5)),
      datasets:[{label:'Win rate',data:days.map(d=>+(d.wr*100).toFixed(1)),
        borderColor:'#d29922',backgroundColor:'rgba(210,153,34,.12)',fill:true,
        pointRadius:3,borderWidth:2,tension:.15}]},
    options:baseOpts({title:'Daily win rate (%)', max:100})
  }));
}

function baseOpts(o={}){
  const opt={responsive:true,maintainAspectRatio:false,
    plugins:{legend:{display:false},
      title:o.title?{display:true,text:o.title,color:'#8b98a9',font:{size:12}}:{display:false},
      tooltip:{callbacks:{}}},
    scales:{
      x:{grid:{color:'#222c3a'},ticks:{color:'#8b98a9',maxRotation:0,autoSkip:true,maxTicksLimit:12}},
      y:{grid:{color:'#222c3a'},ticks:{color:'#8b98a9'},
         ...(o.max!=null?{min:0,max:o.max}:{})}
    }};
  if(o.time){ opt.scales.x.type='time';
    opt.scales.x.time={tooltipFormat:'PP p',displayFormats:{day:'MMM d',hour:'MMM d HH:mm'}}; }
  return opt;
}

renderTabs();
renderView(0);
</script>
</body>
</html>"""


def main() -> None:
    ids = enabled_strategy_ids()
    names = strategy_names()
    out = []
    for sid in ids:
        trades = load_trades(sid)
        if not trades:
            continue
        s = summarize(trades)
        # attach Israel-time strings to each trade for the table (keep payload small-ish)
        slim = []
        for t in s.pop("trades"):
            slim.append({
                "entry_il": il_dt(t["entry_ts_ms"]),
                "slug": t.get("slug", ""),
                "side": t.get("side", ""),
                "entry_price": t.get("entry_price", 0.0),
                "exit_price": t.get("exit_price", 0.0),
                "seconds_held": t.get("seconds_held"),
                "exit_reason": t.get("exit_reason", ""),
                "pnl": round(t["pnl"], 4),
            })
        out.append({"id": sid, "name": names.get(sid, sid), "sum": s, "trades": slim})

    # order: lwd family first, then sqp; biggest PnL within
    out.sort(key=lambda d: (0 if "lwd" in d["id"] else 1, -d["sum"]["pnl"]))

    generated = datetime.now(IL).strftime("%Y-%m-%d %H:%M IDT")
    html = build_html(out, generated)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html)
    tot = sum(d["sum"]["pnl"] for d in out)
    print(f"Wrote {OUT}")
    print(f"Strategies: {len(out)}  |  combined net PnL: ${tot:.2f}")
    for d in out:
        m = d["sum"]
        print(f"  {d['id']:<22} {m['n']:>4} trades  {m['wr']*100:5.1f}% WR  {m['pnl']:+8.2f}  (dd {m['max_dd']:+.2f})")


if __name__ == "__main__":
    main()
