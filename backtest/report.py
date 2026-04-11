"""
backtest/report.py
──────────────────
HTML + CSV + equity-curve PNG report generator for the Minervini AI
backtesting system (Phase 8).

Public API
──────────
    generate_report(result, output_dir, run_label="") -> dict[str, Path]
    _write_csv(result, path)               -> None
    _generate_equity_chart(result, path)   -> None
    _render_html(result, chart_path)       -> str
"""

from __future__ import annotations

import base64
import csv
from datetime import date
from pathlib import Path
from typing import Optional

from jinja2 import Environment, select_autoescape

from backtest.engine import BacktestResult
from backtest.metrics import BacktestMetrics
from utils.logger import get_logger

log = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# CSV column order
# ─────────────────────────────────────────────────────────────────────────────

_CSV_COLUMNS = [
    "symbol", "entry_date", "exit_date", "entry_price", "exit_price",
    "qty", "pnl", "pnl_pct", "r_multiple", "exit_reason",
    "setup_quality", "regime", "initial_risk",
]

# ─────────────────────────────────────────────────────────────────────────────
# Inline Jinja2 HTML template  (built from parts to stay within line limits)
# ─────────────────────────────────────────────────────────────────────────────

_T_HEAD = (
    '<!DOCTYPE html><html lang="en"><head>'
    '<meta charset="UTF-8">'
    '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
    "<title>Backtest Report \u2014 {{ strategy_name }}</title>"
    "<style>"
    ":root{--bg:#0f0f1a;--surface:#1a1a2e;--surface2:#16213e;--border:#2a2a4a;"
    "--text:#e0e0f0;--muted:#888aaa;--green:#00e676;--red:#ef5350;"
    "--gold:#ffd700;--blue:#40c4ff;--orange:#ffa726;}"
    "*{box-sizing:border-box;margin:0;padding:0;}"
    "body{background:var(--bg);color:var(--text);"
    "font-family:'Segoe UI',system-ui,sans-serif;font-size:14px;line-height:1.6;padding:24px;}"
    "h1{font-size:1.8rem;color:var(--gold);margin-bottom:4px;}"
    "h2{font-size:1.1rem;color:var(--blue);margin:28px 0 10px;"
    "border-bottom:1px solid var(--border);padding-bottom:6px;}"
    ".header-meta{color:var(--muted);font-size:0.85rem;margin-bottom:24px;}"
    "table{width:100%;border-collapse:collapse;background:var(--surface);"
    "border-radius:8px;overflow:hidden;margin-bottom:12px;}"
    "th{background:var(--surface2);color:var(--muted);font-size:0.75rem;"
    "text-transform:uppercase;letter-spacing:.06em;padding:10px 14px;"
    "text-align:left;border-bottom:1px solid var(--border);}"
    "td{padding:9px 14px;border-bottom:1px solid var(--border);}"
    "tr:last-child td{border-bottom:none;}"
    "tr:hover td{background:var(--surface2);}"
    ".pos{color:var(--green);font-weight:600;}"
    ".neg{color:var(--red);font-weight:600;}"
    ".neu{color:var(--muted);}"
    ".badge{display:inline-block;padding:2px 8px;border-radius:12px;"
    "font-size:.75rem;font-weight:700;letter-spacing:.04em;}"
    ".badge-aplus{background:rgba(255,215,0,.15);color:var(--gold);border:1px solid var(--gold);}"
    ".badge-a{background:rgba(0,230,118,.12);color:var(--green);border:1px solid var(--green);}"
    ".badge-b{background:rgba(64,196,255,.12);color:var(--blue);border:1px solid var(--blue);}"
    ".badge-c{background:rgba(255,167,38,.12);color:var(--orange);border:1px solid var(--orange);}"
    ".badge-fail{background:rgba(239,83,80,.12);color:var(--red);border:1px solid var(--red);}"
    ".badge-bull{background:rgba(0,230,118,.12);color:var(--green);border:1px solid var(--green);}"
    ".badge-bear{background:rgba(239,83,80,.12);color:var(--red);border:1px solid var(--red);}"
    ".badge-side{background:rgba(136,138,170,.12);color:var(--muted);border:1px solid var(--muted);}"
    ".metrics-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));"
    "gap:12px;margin-bottom:24px;}"
    ".metric-card{background:var(--surface);border:1px solid var(--border);"
    "border-radius:8px;padding:14px 18px;}"
    ".metric-label{color:var(--muted);font-size:.72rem;text-transform:uppercase;letter-spacing:.06em;}"
    ".metric-value{font-size:1.4rem;font-weight:700;margin-top:4px;}"
    ".chart-wrap{margin:20px 0;text-align:center;}"
    ".chart-wrap img{max-width:100%;border-radius:8px;border:1px solid var(--border);}"
    "</style></head><body>"
)

_T_HEADER_SECTION = """
<h1>&#9654; Backtest Report</h1>
<div class="header-meta">
  Strategy: <strong>{{ strategy_name }}</strong> &nbsp;|&nbsp;
  {{ start_date }} &rarr; {{ end_date }} &nbsp;|&nbsp;
  Initial Capital: <strong>&#8377;{{ initial_capital }}</strong> &nbsp;|&nbsp;
  Generated: {{ generated_at }}
</div>

<h2>&#128200; Summary Metrics</h2>
<div class="metrics-grid">
{% for label, val, cls in metric_cards %}
  <div class="metric-card">
    <div class="metric-label">{{ label }}</div>
    <div class="metric-value {{ cls }}">{{ val }}</div>
  </div>
{% endfor %}
</div>
"""

_T_CHART = """
{% if chart_b64 %}
<h2>&#128200; Equity Curve</h2>
<div class="chart-wrap">
  <img src="data:image/png;base64,{{ chart_b64 }}" alt="Equity Curve">
</div>
{% endif %}
"""

_T_REGIME = """
{% if regime_rows %}
<h2>&#127758; Regime Breakdown</h2>
<table>
  <thead><tr>
    <th>Regime</th><th>Trades</th><th>Wins</th>
    <th>Win Rate</th><th>Avg PnL %</th>
  </tr></thead>
  <tbody>
  {% for r in regime_rows %}
  <tr>
    <td>
      {% if r.regime == "Bull" %}<span class="badge badge-bull">Bull</span>
      {% elif r.regime == "Bear" %}<span class="badge badge-bear">Bear</span>
      {% else %}<span class="badge badge-side">{{ r.regime }}</span>{% endif %}
    </td>
    <td>{{ r.trades }}</td>
    <td>{{ r.wins }}</td>
    <td class="{{ 'pos' if r.win_rate >= 50 else 'neg' }}">
      {{ "%.1f"|format(r.win_rate) }}%</td>
    <td class="{{ 'pos' if r.avg_pnl_pct >= 0 else 'neg' }}">
      {{ "%.2f"|format(r.avg_pnl_pct) }}%</td>
  </tr>
  {% endfor %}
  </tbody>
</table>
{% endif %}
"""

_T_SWEEP = """
{% if parameter_sweep %}
<h2>&#128295; Parameter Sweep</h2>
<table>
  <thead><tr>
    <th>Trailing Stop %</th><th>CAGR %</th><th>Sharpe</th>
    <th>Max DD %</th><th>Win Rate %</th><th>Trades</th>
  </tr></thead>
  <tbody>
  {% for s in parameter_sweep %}
  <tr>
    <td>{{ "Fixed" if s.trailing_stop_pct is none
          else "%.1f"|format(s.trailing_stop_pct * 100) ~ "%" }}</td>
    <td class="{{ 'pos' if s.cagr >= 0 else 'neg' }}">
      {{ "%.2f"|format(s.cagr) }}</td>
    <td>{{ "%.2f"|format(s.sharpe) }}</td>
    <td class="neg">{{ "%.2f"|format(s.max_drawdown) }}</td>
    <td class="{{ 'pos' if s.win_rate >= 50 else 'neg' }}">
      {{ "%.1f"|format(s.win_rate) }}</td>
    <td>{{ s.total_trades }}</td>
  </tr>
  {% endfor %}
  </tbody>
</table>
{% endif %}
"""

_T_QUALITY = """
{% if quality_rows %}
<h2>&#127941; Setup Quality Breakdown</h2>
<table>
  <thead><tr>
    <th>Quality</th><th>Trades</th><th>Wins</th>
    <th>Win Rate</th><th>Avg PnL %</th>
  </tr></thead>
  <tbody>
  {% for q in quality_rows %}
  <tr>
    <td>
      {% if q.quality == "A+" %}<span class="badge badge-aplus">A+</span>
      {% elif q.quality == "A" %}<span class="badge badge-a">A</span>
      {% elif q.quality == "B" %}<span class="badge badge-b">B</span>
      {% elif q.quality == "C" %}<span class="badge badge-c">C</span>
      {% else %}<span class="badge badge-fail">{{ q.quality }}</span>{% endif %}
    </td>
    <td>{{ q.trades }}</td><td>{{ q.wins }}</td>
    <td class="{{ 'pos' if q.win_rate >= 50 else 'neg' }}">
      {{ "%.1f"|format(q.win_rate) }}%</td>
    <td class="{{ 'pos' if q.avg_pnl_pct >= 0 else 'neg' }}">
      {{ "%.2f"|format(q.avg_pnl_pct) }}%</td>
  </tr>
  {% endfor %}
  </tbody>
</table>
{% endif %}
"""

_T_TRADES = """
{% if recent_trades %}
<h2>&#128203; Recent Trades
  <span style="color:var(--muted);font-size:.8rem;font-weight:normal">
    (last {{ recent_trades|length }})
  </span>
</h2>
<table>
  <thead><tr>
    <th>Symbol</th><th>Entry</th><th>Exit</th>
    <th>PnL (&#8377;)</th><th>PnL %</th><th>R</th>
    <th>Exit Reason</th><th>Regime</th>
  </tr></thead>
  <tbody>
  {% for t in recent_trades %}
  <tr>
    <td><strong>{{ t.symbol }}</strong></td>
    <td class="neu">{{ t.entry_date }}</td>
    <td class="neu">{{ t.exit_date }}</td>
    <td class="{{ 'pos' if t.pnl >= 0 else 'neg' }}">
      &#8377;{{ "{:,.0f}".format(t.pnl) }}</td>
    <td class="{{ 'pos' if t.pnl_pct >= 0 else 'neg' }}">
      {{ "%.2f"|format(t.pnl_pct) }}%</td>
    <td class="{{ 'pos' if t.r_multiple >= 0 else 'neg' }}">
      {{ "%.2f"|format(t.r_multiple) }}R</td>
    <td class="neu">{{ t.exit_reason or "\u2014" }}</td>
    <td>
      {% if t.regime == "Bull" %}<span class="badge badge-bull">Bull</span>
      {% elif t.regime == "Bear" %}<span class="badge badge-bear">Bear</span>
      {% elif t.regime %}<span class="badge badge-side">{{ t.regime }}</span>
      {% else %}\u2014{% endif %}
    </td>
  </tr>
  {% endfor %}
  </tbody>
</table>
{% endif %}
</body></html>
"""

# Assemble into one template string
_HTML_TEMPLATE = (
    _T_HEAD
    + _T_HEADER_SECTION
    + _T_CHART
    + _T_REGIME
    + _T_SWEEP
    + _T_QUALITY
    + _T_TRADES
)


# ─────────────────────────────────────────────────────────────────────────────
# _write_csv
# ─────────────────────────────────────────────────────────────────────────────

def _write_csv(result: BacktestResult, path: Path) -> None:
    """Write all closed trades to CSV with the canonical 13-column schema."""
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for trade in result.trades:
            row = dict(trade)
            if "r_multiple" not in row or row["r_multiple"] is None:
                risk = float(row.get("initial_risk") or 0)
                pnl  = float(row.get("pnl") or 0)
                row["r_multiple"] = round(pnl / risk, 4) if risk != 0 else 0.0
            writer.writerow(row)
    log.info("Backtest CSV written", path=str(path), rows=len(result.trades))


# ─────────────────────────────────────────────────────────────────────────────
# _generate_equity_chart
# ─────────────────────────────────────────────────────────────────────────────

def _generate_equity_chart(result: BacktestResult, path: Path) -> None:
    """Generate equity curve PNG (headless Agg backend)."""
    import matplotlib
    matplotlib.use("Agg")                      # MUST come before pyplot import
    import matplotlib.pyplot as plt
    import matplotlib.ticker as ticker

    eq      = result.equity_curve
    metrics = result.metrics

    fig, ax = plt.subplots(figsize=(14, 6), facecolor="#0f0f1a")
    ax.set_facecolor("#1a1a2e")

    if eq.empty:
        ax.text(0.5, 0.5, "No trade data", transform=ax.transAxes,
                ha="center", va="center", color="#888aaa", fontsize=14)
        fig.savefig(path, dpi=120, bbox_inches="tight",
                    facecolor=fig.get_facecolor())
        plt.close(fig)
        return

    dates  = eq.index
    equity = eq["equity"].values
    xs     = list(range(len(dates)))

    # ── Regime background shading ─────────────────────────────────────────
    _REGIME_BG = {"Bull": "#00e676", "Bear": "#ef5350", "Sideways": "#aaaaaa"}
    regime_by_date = {str(t.get("exit_date", "")): t.get("regime", "")
                      for t in result.trades}

    prev_regime, block_start = None, 0
    date_strs = [str(d.date() if hasattr(d, "date") else d) for d in dates]
    for i, ds in enumerate(date_strs):
        reg = regime_by_date.get(ds, prev_regime or "")
        if reg != prev_regime:
            if prev_regime and prev_regime in _REGIME_BG:
                ax.axvspan(block_start, i, alpha=0.15,
                           color=_REGIME_BG[prev_regime], linewidth=0)
            block_start, prev_regime = i, reg
    if prev_regime and prev_regime in _REGIME_BG:
        ax.axvspan(block_start, len(dates), alpha=0.15,
                   color=_REGIME_BG[prev_regime], linewidth=0)

    # ── Equity line + fill ────────────────────────────────────────────────
    ax.plot(xs, equity, color="#40c4ff", linewidth=1.8, zorder=3)
    ax.fill_between(xs, equity, float(equity.min()) * 0.998,
                    alpha=0.15, color="#40c4ff", zorder=2)

    # ── X-axis: monthly ticks ─────────────────────────────────────────────
    tick_pos, tick_lbl, seen = [], [], set()
    for i, ts in enumerate(dates):
        ym = (ts.year, ts.month)
        if ym not in seen:
            seen.add(ym)
            tick_pos.append(i)
            tick_lbl.append(ts.strftime("%b '%y"))
    ax.set_xticks(tick_pos)
    ax.set_xticklabels(tick_lbl, rotation=45, ha="right",
                       fontsize=8, color="#888aaa")

    # ── Y-axis: Indian ₹ format ───────────────────────────────────────────
    ax.yaxis.set_major_formatter(
        ticker.FuncFormatter(lambda v, _: f"\u20b9{v:,.0f}")
    )
    ax.tick_params(axis="y", colors="#888aaa", labelsize=9)
    ax.spines[:].set_color("#2a2a4a")
    ax.grid(color="#2a2a4a", linestyle="--", linewidth=0.5, alpha=0.6)

    # ── Title + subtitle ──────────────────────────────────────────────────
    ax.set_title(
        f"Backtest Equity Curve \u2014 {result.start_date} to {result.end_date}",
        color="#e0e0f0", fontsize=13, fontweight="bold", pad=12,
    )
    fig.text(
        0.5, 0.94,
        f"CAGR: {metrics.cagr:.1f}%  |  Sharpe: {metrics.sharpe_ratio:.2f}"
        f"  |  Max DD: {metrics.max_drawdown_pct:.1f}%"
        f"  |  Win Rate: {metrics.win_rate:.1f}%",
        ha="center", va="top", fontsize=9, color="#888aaa",
    )

    plt.tight_layout(rect=[0, 0, 1, 0.93])
    fig.savefig(path, dpi=120, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    log.info("Equity chart saved", path=str(path))


# ─────────────────────────────────────────────────────────────────────────────
# _render_html
# ─────────────────────────────────────────────────────────────────────────────

def _render_html(result: BacktestResult, chart_path: Path) -> str:
    """Render the full HTML report; chart PNG embedded as base64 data URI."""
    m = result.metrics

    # ── Embed chart ───────────────────────────────────────────────────────
    chart_b64 = ""
    if chart_path.exists():
        with open(chart_path, "rb") as fh:
            chart_b64 = base64.b64encode(fh.read()).decode()

    # ── Metric cards (label, formatted value, CSS class) ──────────────────
    pf     = m.profit_factor
    pf_fmt = "\u221e" if pf == float("inf") else f"{pf:.2f}"

    def _sign_cls(v: float) -> str:
        return "pos" if v >= 0 else "neg"

    metric_cards = [
        ("CAGR",         f"{m.cagr:.2f}%",              _sign_cls(m.cagr)),
        ("Total Return", f"{m.total_return_pct:.2f}%",   _sign_cls(m.total_return_pct)),
        ("Sharpe Ratio", f"{m.sharpe_ratio:.2f}",        ""),
        ("Max Drawdown", f"{m.max_drawdown_pct:.2f}%",   "neg"),
        ("Win Rate",     f"{m.win_rate:.1f}%",           _sign_cls(m.win_rate - 50)),
        ("Total Trades", str(m.total_trades),             ""),
        ("Profit Factor", pf_fmt,                         ""),
        ("Expectancy",   f"{m.expectancy_pct:.2f}%",     _sign_cls(m.expectancy_pct)),
    ]

    # ── Regime breakdown rows ─────────────────────────────────────────────
    class _NS:  # simple namespace
        def __init__(self, **kw): self.__dict__.update(kw)

    regime_rows = []
    for reg, stats in result.regime_breakdown.items():
        trades = stats.get("total_trades", 0)
        wins   = stats.get("winning_trades", 0)
        wr     = (wins / trades * 100.0) if trades else 0.0
        pnls   = [float(t.get("pnl_pct", 0)) for t in result.trades
                  if t.get("regime") == reg]
        avg_p  = sum(pnls) / len(pnls) if pnls else 0.0
        regime_rows.append(_NS(regime=reg, trades=trades, wins=wins,
                                win_rate=wr, avg_pnl_pct=avg_p))

    # ── Setup quality breakdown rows ──────────────────────────────────────
    quality_map: dict[str, list] = {}
    for t in result.trades:
        q = str(t.get("setup_quality", "?"))
        quality_map.setdefault(q, []).append(t)

    _ORDER = ["A+", "A", "B", "C", "FAIL"]
    quality_rows = []
    for q in _ORDER + [k for k in quality_map if k not in _ORDER]:
        if q not in quality_map:
            continue
        tl   = quality_map[q]
        wins = sum(1 for t in tl if float(t.get("pnl", 0)) > 0)
        wr   = wins / len(tl) * 100.0
        avg_p = sum(float(t.get("pnl_pct", 0)) for t in tl) / len(tl)
        quality_rows.append(_NS(quality=q, trades=len(tl), wins=wins,
                                 win_rate=wr, avg_pnl_pct=avg_p))

    # ── Recent trades (last 30, newest first) ─────────────────────────────
    sorted_trades = sorted(result.trades,
                           key=lambda t: str(t.get("exit_date", "")),
                           reverse=True)[:30]

    class _Trade:
        def __init__(self, d: dict):
            self.symbol      = d.get("symbol", "")
            self.entry_date  = str(d.get("entry_date", ""))
            self.exit_date   = str(d.get("exit_date", ""))
            self.pnl         = float(d.get("pnl", 0))
            self.pnl_pct     = float(d.get("pnl_pct", 0))
            risk             = float(d.get("initial_risk") or 0)
            self.r_multiple  = round(self.pnl / risk, 2) if risk != 0 else 0.0
            self.exit_reason = d.get("exit_reason", "")
            self.regime      = d.get("regime", "")

    recent_trades  = [_Trade(t) for t in sorted_trades]
    strategy_name  = result.config_snapshot.get("strategy", {}).get(
        "name", "Minervini SEPA")
    initial_cap_fmt = f"{result.initial_capital:,.0f}"
    generated_at   = date.today().isoformat()

    env  = Environment(autoescape=select_autoescape(["html"]))
    tmpl = env.from_string(_HTML_TEMPLATE)
    return tmpl.render(
        strategy_name   = strategy_name,
        start_date      = str(result.start_date),
        end_date        = str(result.end_date),
        initial_capital = initial_cap_fmt,
        generated_at    = generated_at,
        metrics         = m,
        metric_cards    = metric_cards,
        chart_b64       = chart_b64,
        regime_rows     = regime_rows,
        parameter_sweep = result.parameter_sweep,
        quality_rows    = quality_rows,
        recent_trades   = recent_trades,
    )


# ─────────────────────────────────────────────────────────────────────────────
# generate_report  (public API)
# ─────────────────────────────────────────────────────────────────────────────

def generate_report(
    result: BacktestResult,
    output_dir: Path,
    run_label: str = "",
) -> dict[str, Path]:
    """
    Generate backtest report files and return their paths.

    Files created:
        {output_dir}/backtest_{label}_{date}.html
        {output_dir}/backtest_{label}_{date}.csv
        {output_dir}/equity_curve_{label}_{date}.png

    Returns: {"html": Path, "csv": Path, "chart": Path}
    """
    label   = run_label if run_label else "run"
    today   = date.today().isoformat()
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    csv_path   = out_dir / f"backtest_{label}_{today}.csv"
    html_path  = out_dir / f"backtest_{label}_{today}.html"
    chart_path = out_dir / f"equity_curve_{label}_{today}.png"

    _write_csv(result, csv_path)
    _generate_equity_chart(result, chart_path)
    html_path.write_text(_render_html(result, chart_path), encoding="utf-8")

    log.info(
        "Backtest report generated",
        label=label,
        html=str(html_path),
        csv=str(csv_path),
        chart=str(chart_path),
        trades=len(result.trades),
    )
    return {"html": html_path, "csv": csv_path, "chart": chart_path}
