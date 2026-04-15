"""
report.py — Self-contained HTML report generator.

Generates single-strategy and multi-strategy comparison reports.
Chart.js is cached locally on first use so reports work offline.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd

from .metrics import daily_equity_returns

if TYPE_CHECKING:
    from .engine import BacktestResult

_REPORT_DIR  = os.path.join("backtest", "reports")
_ASSETS_DIR  = os.path.join(_REPORT_DIR, "assets")

# CDN sources (used only on first download)
_CDN_CHART_JS   = "https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"
_CDN_DATE_ADAPT = "https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3.0.0/dist/chartjs-adapter-date-fns.bundle.min.js"
_LOCAL_CHART_JS   = os.path.join(_ASSETS_DIR, "chart.umd.min.js")
_LOCAL_DATE_ADAPT = os.path.join(_ASSETS_DIR, "chartjs-adapter-date-fns.bundle.min.js")


def _ensure_assets() -> tuple[str, str]:
    """
    Download Chart.js assets to backtest/reports/assets/ on first run.
    Returns (chart_js_tag, adapter_tag) as HTML <script> strings.
    Falls back to CDN if download fails.
    """
    os.makedirs(_ASSETS_DIR, exist_ok=True)

    def _download(url: str, dest: str) -> bool:
        if os.path.exists(dest):
            return True
        try:
            print(f"  [assets] Downloading {os.path.basename(dest)} …")
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp, open(dest, "wb") as f:
                f.write(resp.read())
            return True
        except Exception as exc:
            print(f"  [assets] Warning: could not download {os.path.basename(dest)}: {exc}")
            return False

    chart_ok   = _download(_CDN_CHART_JS,   _LOCAL_CHART_JS)
    adapter_ok = _download(_CDN_DATE_ADAPT, _LOCAL_DATE_ADAPT)

    if chart_ok and adapter_ok:
        # Use relative paths (assets/ is a sibling of the report HTML)
        return (
            '<script src="assets/chart.umd.min.js"></script>',
            '<script src="assets/chartjs-adapter-date-fns.bundle.min.js"></script>',
        )
    # Fallback to CDN
    return (
        f'<script src="{_CDN_CHART_JS}"></script>',
        f'<script src="{_CDN_DATE_ADAPT}"></script>',
    )

# Chart colours for the three bots
_BOT_COLOURS = {
    "MACD":     "#4ade80",   # green
    "RSI_VWAP": "#60a5fa",   # blue
    "CVD":      "#f97316",   # orange
}
_DEFAULT_COLOUR = "#a78bfa"  # purple fallback


# ── Public API ────────────────────────────────────────────────────────────────

def generate_report(result: "BacktestResult", output_dir: str = _REPORT_DIR) -> str:
    """Generate a single-strategy HTML report. Returns the file path."""
    os.makedirs(output_dir, exist_ok=True)
    ts_str  = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname   = f"report_{result.strategy_name.lower()}_{ts_str}.html"
    fpath   = os.path.join(output_dir, fname)

    html = _render_single(result)
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"  [report] Saved -> {fpath}")
    return fpath


def generate_comparison_report(
    results: list["BacktestResult"],
    output_dir: str = _REPORT_DIR,
) -> str:
    """Generate a multi-strategy comparison HTML report. Returns file path."""
    os.makedirs(output_dir, exist_ok=True)
    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname  = f"report_comparison_{ts_str}.html"
    fpath  = os.path.join(output_dir, fname)

    html = _render_comparison(results)
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"  [report] Comparison saved -> {fpath}")
    return fpath


# ── Data preparation helpers ──────────────────────────────────────────────────

def _daily_series(equity_curve: list[tuple]) -> list[dict]:
    """Downsample equity curve to one point per day for charts."""
    if not equity_curve:
        return []
    series = pd.Series({ts: eq for ts, eq in equity_curve}, dtype=float)
    series.index = pd.to_datetime(series.index, utc=True)
    daily = series.resample("D").last().dropna()
    return [{"x": str(ts.date()), "y": round(float(v), 4)} for ts, v in daily.items()]


def _drawdown_series(equity_curve: list[tuple]) -> list[dict]:
    """Daily drawdown series (negative values, %)."""
    if not equity_curve:
        return []
    series = pd.Series({ts: eq for ts, eq in equity_curve}, dtype=float)
    series.index = pd.to_datetime(series.index, utc=True)
    daily = series.resample("D").last().dropna()

    peak = float(daily.iloc[0])
    result = []
    for ts, eq in daily.items():
        eq = float(eq)
        if eq > peak:
            peak = eq
        dd = (eq - peak) / peak * 100 if peak > 0 else 0.0
        result.append({"x": str(ts.date()), "y": round(dd, 4)})
    return result


def _monthly_returns(equity_curve: list[tuple]) -> dict[int, dict[str, float]]:
    """Returns {year: {month_abbr: return_pct}}."""
    if len(equity_curve) < 2:
        return {}
    series = pd.Series({ts: eq for ts, eq in equity_curve}, dtype=float)
    series.index = pd.to_datetime(series.index, utc=True)
    monthly = series.resample("ME").last().dropna()
    rets = monthly.pct_change().dropna() * 100

    grid: dict[int, dict[str, float]] = {}
    for ts, val in rets.items():
        grid.setdefault(ts.year, {})[ts.strftime("%b")] = round(float(val), 2)
    return grid


def _corr_matrix(results: list["BacktestResult"]) -> list[list]:
    """
    Returns [[corr_00, corr_01, ...], ...] as a nested list.
    Uses daily equity returns for each strategy.
    """
    series_map = {}
    for r in results:
        dr = daily_equity_returns(r.equity_curve)
        dr.name = r.strategy_name
        series_map[r.strategy_name] = dr

    df = pd.DataFrame(series_map).dropna()
    if df.empty or df.shape[0] < 2:
        n = len(results)
        return [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]

    corr = df.corr()
    names = [r.strategy_name for r in results]
    return [[round(float(corr.loc[a, b]), 3) for b in names] for a in names]


def _winner_summary(results: list["BacktestResult"]) -> list[dict]:
    """
    For each key metric, determine which bot wins.
    Returns list of {metric, winner, values_dict}.
    """
    if not results:
        return []

    metric_labels = {
        "total_return_pct":  ("Total Return %",      True),   # (label, higher_is_better)
        "annual_return_pct": ("Annual Return %",      True),
        "max_drawdown_pct":  ("Max Drawdown %",       False),  # lower is better
        "sharpe":            ("Sharpe Ratio",         True),
        "sortino":           ("Sortino Ratio",        True),
        "calmar":            ("Calmar Ratio",         True),
        "win_rate_pct":      ("Win Rate %",           True),
        "profit_factor":     ("Profit Factor",        True),
        "total_commission":  ("Total Commission",     False),
    }

    rows = []
    for key, (label, higher_better) in metric_labels.items():
        vals = {r.strategy_name: r.metrics.get(key, 0) for r in results}
        if higher_better:
            winner = max(vals, key=lambda k: vals[k])
        else:
            winner = min(vals, key=lambda k: vals[k])
        rows.append({"label": label, "winner": winner, "values": vals})
    return rows


# ── CSS + Chart.js references ─────────────────────────────────────────────────

_CSS = """\
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0f1117; color: #e2e8f0; font-family: 'Segoe UI', system-ui, sans-serif; font-size: 14px; line-height: 1.6; padding: 24px; }
  h1 { font-size: 1.8rem; color: #f1f5f9; margin-bottom: 4px; }
  h2 { font-size: 1.1rem; color: #94a3b8; font-weight: 500; margin-bottom: 20px; }
  h3 { font-size: 1rem; color: #cbd5e1; margin: 28px 0 12px; border-bottom: 1px solid #1e293b; padding-bottom: 6px; }
  .card { background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 20px; margin-bottom: 24px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { background: #0f172a; color: #94a3b8; text-align: left; padding: 8px 12px; font-weight: 600; border-bottom: 1px solid #334155; }
  td { padding: 7px 12px; border-bottom: 1px solid #1e293b; }
  tr:hover td { background: #1e293b; }
  .pos { color: #4ade80; } .neg { color: #f87171; } .neu { color: #94a3b8; }
  .badge { display: inline-block; padding: 1px 8px; border-radius: 9999px; font-size: 11px; font-weight: 600; }
  .badge-win  { background: #14532d; color: #4ade80; }
  .badge-lose { background: #450a0a; color: #f87171; }
  .chart-wrap { position: relative; height: 300px; margin-bottom: 8px; }
  .heatmap-wrap { overflow-x: auto; }
  .heatmap td, .heatmap th { text-align: center; min-width: 52px; font-size: 12px; padding: 5px 4px; }
  .metrics-2col { display: grid; grid-template-columns: 1fr 1fr; gap: 0; }
  .metrics-2col td:first-child { color: #94a3b8; }
  .winner-badge { background: #1d4ed8; color: #bfdbfe; padding: 1px 8px; border-radius: 9999px; font-size: 11px; }
  .corr-cell { text-align: center; min-width: 60px; padding: 8px; font-size: 13px; border-radius: 4px; }
  .note { font-size: 11px; color: #64748b; margin-top: 8px; }
  @media (max-width: 700px) { .metrics-2col { grid-template-columns: 1fr; } }
</style>
"""


def _build_head() -> str:
    chart_tag, adapter_tag = _ensure_assets()
    return (
        '<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        + chart_tag + "\n"
        + adapter_tag + "\n"
        + _CSS
    )


# ── Heatmap table generator ────────────────────────────────────────────────────

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _heatmap_color(pct: float) -> str:
    if pct >= 5:   return "background:#14532d;color:#4ade80"
    if pct >= 1:   return "background:#166534;color:#86efac"
    if pct > -1:   return "background:#1e293b;color:#94a3b8"
    if pct > -5:   return "background:#7f1d1d;color:#fca5a5"
    return                 "background:#450a0a;color:#f87171"


def _heatmap_html(grid: dict[int, dict[str, float]]) -> str:
    if not grid:
        return "<p class='neu'>Not enough data for monthly returns.</p>"

    years = sorted(grid.keys())
    rows  = ["<div class='heatmap-wrap'><table class='heatmap'>",
             "<tr><th>Year</th>" + "".join(f"<th>{m}</th>" for m in _MONTHS) + "</tr>"]
    for year in years:
        cells = ["<td>"]
        for month in _MONTHS:
            val = grid.get(year, {}).get(month)
            if val is None:
                cells.append("<td>—</td>")
            else:
                sign  = "+" if val >= 0 else ""
                style = _heatmap_color(val)
                cells.append(f'<td style="{style}">{sign}{val:.1f}%</td>')
        rows.append(f"<tr><td><b>{year}</b></td>{''.join(cells[1:])}</tr>")
    rows.append("</table></div>")
    return "\n".join(rows)


# ── Metrics table (2-column) ───────────────────────────────────────────────────

_METRIC_DISPLAY = [
    ("total_return_pct",     "Total Return",        "%",  True),
    ("annual_return_pct",    "Annual Return",       "%",  True),
    ("max_drawdown_pct",     "Max Drawdown",        "%",  False),
    ("max_dd_duration_days", "DD Duration",         "d",  False),
    ("sharpe",               "Sharpe Ratio",        "",   True),
    ("sortino",              "Sortino Ratio",       "",   True),
    ("calmar",               "Calmar Ratio",        "",   True),
    ("win_rate_pct",         "Win Rate",            "%",  True),
    ("profit_factor",        "Profit Factor",       "×",  True),
    ("avg_win",              "Avg Win",             " $", True),
    ("avg_loss",             "Avg Loss",            " $", False),
    ("largest_win",          "Largest Win",         " $", True),
    ("largest_loss",         "Largest Loss",        " $", False),
    ("avg_hold_time",        "Avg Hold Time",       "",   None),
    ("total_trades",         "Total Trades",        "",   None),
    ("winning_trades",       "Wins",                "",   None),
    ("losing_trades",        "Losses",              "",   None),
    ("total_commission",     "Total Commission",    " $", False),
    ("initial_balance",      "Initial Balance",     " $", None),
    ("final_balance",        "Final Balance",       " $", True),
]


def _val_html(val, suffix: str, higher_better) -> str:
    if isinstance(val, float) and higher_better is not None:
        cls = "pos" if (val > 0 and higher_better) or (val < 0 and not higher_better) else \
              "neg" if (val < 0 and higher_better) or (val > 0 and not higher_better) else "neu"
        sign = "+" if val > 0 and higher_better else ""
        return f'<span class="{cls}">{sign}{val:,.4g}{suffix}</span>'
    return f"{val}{suffix}"


def _metrics_table_html(metrics: dict) -> str:
    rows = ["<table class='metrics-2col'>"]
    for key, label, suffix, hb in _METRIC_DISPLAY:
        val = metrics.get(key, "—")
        rows.append(f"<tr><td>{label}</td><td>{_val_html(val, suffix, hb)}</td></tr>")
    rows.append("</table>")
    return "\n".join(rows)


def _trade_table_html(trades) -> str:
    if not trades:
        return "<p class='neu'>No trades executed.</p>"

    rows = ["""<div style="overflow-x:auto"><table>
<tr><th>#</th><th>Entry Time</th><th>Exit Time</th><th>Side</th>
<th>Entry $</th><th>Exit $</th><th>Net PnL</th><th>Commission</th><th>Reason</th></tr>"""]

    for i, t in enumerate(trades, 1):
        pnl_cls  = "pos" if t.net_pnl >= 0 else "neg"
        side_cls = "pos" if t.side == "long" else "neg"
        entry_ts = str(t.entry_time)[:16]
        exit_ts  = str(t.exit_time)[:16]
        rows.append(
            f"<tr><td>{i}</td><td>{entry_ts}</td><td>{exit_ts}</td>"
            f"<td class='{side_cls}'>{t.side.upper()}</td>"
            f"<td>{t.entry_price:,.4f}</td><td>{t.exit_price:,.4f}</td>"
            f"<td class='{pnl_cls}'>{'+' if t.net_pnl>=0 else ''}{t.net_pnl:,.4f}</td>"
            f"<td>{t.commission:,.4f}</td>"
            f"<td>{t.reason}</td></tr>"
        )
    rows.append("</table></div>")
    return "\n".join(rows)


# ── Chart.js snippets ─────────────────────────────────────────────────────────

def _equity_chart_js(canvas_id: str, datasets: list[dict]) -> str:
    """datasets: [{"label": ..., "data": [...], "color": ...}]"""
    ds_json = json.dumps([{
        "label":           d["label"],
        "data":            d["data"],
        "borderColor":     d["color"],
        "backgroundColor": d["color"] + "20",
        "borderWidth":     2,
        "pointRadius":     0,
        "fill":            len(datasets) == 1,
        "tension":         0.1,
    } for d in datasets])

    return f"""
<script>
(function() {{
  const ctx = document.getElementById('{canvas_id}').getContext('2d');
  new Chart(ctx, {{
    type: 'line',
    data: {{ datasets: {ds_json} }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      interaction: {{ mode: 'index', intersect: false }},
      scales: {{
        x: {{ type: 'time', time: {{ unit: 'month' }},
              grid: {{ color: '#1e293b' }}, ticks: {{ color: '#64748b' }} }},
        y: {{ grid: {{ color: '#1e293b' }}, ticks: {{ color: '#64748b',
              callback: v => '$' + v.toLocaleString() }} }}
      }},
      plugins: {{
        legend: {{ labels: {{ color: '#cbd5e1' }} }},
        tooltip: {{ callbacks: {{
          label: ctx => ctx.dataset.label + ': $' + ctx.parsed.y.toLocaleString(undefined, {{minimumFractionDigits:2, maximumFractionDigits:2}})
        }} }}
      }}
    }}
  }});
}})();
</script>"""


def _drawdown_chart_js(canvas_id: str, data: list[dict], color: str = "#f87171") -> str:
    data_json = json.dumps(data)
    return f"""
<script>
(function() {{
  const ctx = document.getElementById('{canvas_id}').getContext('2d');
  new Chart(ctx, {{
    type: 'line',
    data: {{ datasets: [{{
      label: 'Drawdown %',
      data: {data_json},
      borderColor: '{color}',
      backgroundColor: '{color}33',
      borderWidth: 1.5,
      pointRadius: 0,
      fill: true,
      tension: 0.1,
    }}] }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      scales: {{
        x: {{ type: 'time', time: {{ unit: 'month' }},
              grid: {{ color: '#1e293b' }}, ticks: {{ color: '#64748b' }} }},
        y: {{ grid: {{ color: '#1e293b' }}, ticks: {{ color: '#64748b',
              callback: v => v.toFixed(1) + '%' }} }}
      }},
      plugins: {{
        legend: {{ display: false }},
        tooltip: {{ callbacks: {{
          label: ctx => ctx.parsed.y.toFixed(2) + '%'
        }} }}
      }}
    }}
  }});
}})();
</script>"""


# ── Single strategy report ────────────────────────────────────────────────────

def _render_single(result: "BacktestResult") -> str:
    m         = result.metrics
    colour    = _BOT_COLOURS.get(result.strategy_name, _DEFAULT_COLOUR)
    eq_data   = _daily_series(result.equity_curve)
    dd_data   = _drawdown_series(result.equity_curve)
    monthly   = _monthly_returns(result.equity_curve)

    ret_pct   = m.get("total_return_pct", 0)
    ret_cls   = "pos" if ret_pct >= 0 else "neg"
    ret_sign  = "+" if ret_pct >= 0 else ""

    eq_chart = _equity_chart_js(
        "equityChart",
        [{"label": f"{result.strategy_name} Equity", "data": eq_data, "color": colour}],
    )
    dd_chart = _drawdown_chart_js("ddChart", dd_data, colour)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<title>Backtest: {result.strategy_name} {result.symbol}</title>
{_build_head()}
</head>
<body>
<h1>Backtest Report — {result.strategy_name}</h1>
<h2>{result.symbol} · {result.timeframe} · {result.start_date} to {result.end_date}</h2>

<div style="display:flex;gap:16px;margin-bottom:24px;flex-wrap:wrap">
  <div class="card" style="flex:1;min-width:140px;text-align:center">
    <div style="font-size:.85rem;color:#94a3b8">Total Return</div>
    <div style="font-size:2rem;font-weight:700" class="{ret_cls}">{ret_sign}{ret_pct:.2f}%</div>
  </div>
  <div class="card" style="flex:1;min-width:140px;text-align:center">
    <div style="font-size:.85rem;color:#94a3b8">Sharpe</div>
    <div style="font-size:2rem;font-weight:700">{m.get('sharpe', 0):.2f}</div>
  </div>
  <div class="card" style="flex:1;min-width:140px;text-align:center">
    <div style="font-size:.85rem;color:#94a3b8">Max Drawdown</div>
    <div style="font-size:2rem;font-weight:700;color:#f87171">-{m.get('max_drawdown_pct', 0):.2f}%</div>
  </div>
  <div class="card" style="flex:1;min-width:140px;text-align:center">
    <div style="font-size:.85rem;color:#94a3b8">Win Rate</div>
    <div style="font-size:2rem;font-weight:700">{m.get('win_rate_pct', 0):.1f}%</div>
  </div>
  <div class="card" style="flex:1;min-width:140px;text-align:center">
    <div style="font-size:.85rem;color:#94a3b8">Trades</div>
    <div style="font-size:2rem;font-weight:700">{m.get('total_trades', 0)}</div>
  </div>
</div>

<div class="card">
  <h3>Performance Metrics</h3>
  {_metrics_table_html(m)}
</div>

<div class="card">
  <h3>Equity Curve</h3>
  <div class="chart-wrap"><canvas id="equityChart"></canvas></div>
</div>
{eq_chart}

<div class="card">
  <h3>Drawdown</h3>
  <div class="chart-wrap" style="height:180px"><canvas id="ddChart"></canvas></div>
</div>
{dd_chart}

<div class="card">
  <h3>Monthly Returns</h3>
  {_heatmap_html(monthly)}
</div>

<div class="card">
  <h3>Trade List ({m.get('total_trades', 0)} trades)</h3>
  {_trade_table_html(result.trades)}
</div>

<p class="note" style="margin-top:16px">
  Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')} ·
  Commission {result.metrics.get('total_commission', 0):,.4f} USDT total ·
  Bybit taker fee 0.055%
</p>
</body>
</html>"""


# ── Comparison report ─────────────────────────────────────────────────────────

def _corr_color(val: float) -> str:
    """Return inline style background for correlation cell."""
    v = max(-1.0, min(1.0, val))
    if v >= 0:
        intensity = int(v * 60)
        return f"background:rgb(30,{60+intensity},30);color:#d1fae5"
    else:
        intensity = int(-v * 60)
        return f"background:rgb({60+intensity},30,30);color:#fee2e2"


def _render_comparison(results: list["BacktestResult"]) -> str:
    names   = [r.strategy_name for r in results]
    colours = [_BOT_COLOURS.get(n, _DEFAULT_COLOUR) for n in names]

    # ── Equity chart (all strategies on same chart) ───────────────────────────
    eq_datasets = [
        {"label": r.strategy_name, "data": _daily_series(r.equity_curve), "color": c}
        for r, c in zip(results, colours)
    ]
    eq_chart = _equity_chart_js("compEquityChart", eq_datasets)

    # ── Metrics comparison table ───────────────────────────────────────────────
    def _comp_metrics_table() -> str:
        rows = [f"<table><tr><th>Metric</th>" +
                "".join(f"<th style='color:{c}'>{n}</th>" for n, c in zip(names, colours)) +
                "</tr>"]
        for key, label, suffix, hb in _METRIC_DISPLAY:
            vals = [r.metrics.get(key, "—") for r in results]
            if hb is not None:
                best = max(vals, key=lambda v: v if hb else -v) if vals else None

            cells = []
            for v in vals:
                is_best = (hb is not None) and (v == best)
                html_val = _val_html(v, suffix, hb)
                if is_best:
                    html_val = f"<b>{html_val}</b>"
                cells.append(f"<td>{html_val}</td>")
            rows.append(f"<tr><td style='color:#94a3b8'>{label}</td>{''.join(cells)}</tr>")
        rows.append("</table>")
        return "\n".join(rows)

    # ── Correlation matrix ────────────────────────────────────────────────────
    corr = _corr_matrix(results)

    def _corr_table() -> str:
        rows = [f"<table><tr><th>Strategy</th>" +
                "".join(f"<th style='color:{c}'>{n}</th>" for n, c in zip(names, colours)) +
                "</tr>"]
        for i, (name, colour) in enumerate(zip(names, colours)):
            cells = [f"<td style='color:{colour}'><b>{name}</b></td>"]
            for j in range(len(names)):
                val   = corr[i][j]
                style = _corr_color(val)
                cells.append(f"<td class='corr-cell' style='{style}'>{val:.2f}</td>")
            rows.append(f"<tr>{''.join(cells)}</tr>")
        rows.append("</table>")
        return "\n".join(rows)

    # ── Winner summary ────────────────────────────────────────────────────────
    winners = _winner_summary(results)

    def _winner_table() -> str:
        rows = [f"<table><tr><th>Metric</th>" +
                "".join(f"<th style='color:{c}'>{n}</th>" for n, c in zip(names, colours)) +
                "<th>Winner</th></tr>"]
        for w in winners:
            vals_cells = ""
            for name in names:
                v = w["values"].get(name, "—")
                vals_cells += f"<td>{v:,.4g}" if isinstance(v, float) else f"<td>{v}"
                vals_cells += "</td>"
            winner_colour = _BOT_COLOURS.get(w["winner"], _DEFAULT_COLOUR)
            rows.append(
                f"<tr><td style='color:#94a3b8'>{w['label']}</td>{vals_cells}"
                f"<td><span class='badge' style='background:{winner_colour}30;color:{winner_colour}'>"
                f"{w['winner']}</span></td></tr>"
            )
        rows.append("</table>")
        return "\n".join(rows)

    subtitle_parts = set()
    for r in results:
        subtitle_parts.add(r.symbol)
        subtitle_parts.add(f"{r.start_date} to {r.end_date}")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<title>Strategy Comparison Report</title>
{_build_head()}
</head>
<body>
<h1>Strategy Comparison</h1>
<h2>{' · '.join(sorted(subtitle_parts))}</h2>

<div class="card">
  <h3>Combined Equity Curves</h3>
  <div class="chart-wrap" style="height:360px"><canvas id="compEquityChart"></canvas></div>
</div>
{eq_chart}

<div class="card">
  <h3>Metrics Comparison</h3>
  <div style="overflow-x:auto">
  {_comp_metrics_table()}
  </div>
  <p class="note">Bold = best value for that metric</p>
</div>

<div class="card">
  <h3>Correlation Matrix (Daily Returns)</h3>
  {_corr_table()}
  <p class="note">1.0 = perfectly correlated, 0.0 = uncorrelated, -1.0 = inverse</p>
</div>

<div class="card">
  <h3>Winner Summary</h3>
  <div style="overflow-x:auto">
  {_winner_table()}
  </div>
</div>

<div class="card">
  <h3>Individual Monthly Returns</h3>
  {''.join(
    f'<h4 style="color:{c};margin:16px 0 8px">{r.strategy_name}</h4>'
    + _heatmap_html(_monthly_returns(r.equity_curve))
    for r, c in zip(results, colours)
  )}
</div>

<p class="note" style="margin-top:16px">
  Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')} ·
  Note: CVD in live trading uses tick data; backtest uses bar-direction approximation —
  live win rate will differ slightly.
</p>
</body>
</html>"""
