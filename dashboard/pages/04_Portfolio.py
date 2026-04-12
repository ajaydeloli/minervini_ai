"""
dashboard/pages/04_Portfolio.py
────────────────────────────────
Paper Trading Portfolio page for the Minervini SEPA dashboard.

Sections
────────
  1. Page header + paper-trading-disabled warning
  2. KPI row  — render_portfolio_kpis()
  3. Equity curve + key stats (Total Return, Max DD, Best/Worst trade)
  4. Tabs: Open Positions | Trade History | Performance
  5. Danger zone — Reset Portfolio (double confirmation)

Design constraints
──────────────────
  • All paper_trading data loaded via direct import (NOT via API)
  • st.cache_data(ttl=60) on portfolio summary
  • ₹ symbol throughout
  • Renders cleanly with zero trades (first-run state)
  • Reset requires: checkbox ticked AND text input == "RESET"
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st

# ─────────────────────────────────────────────────────────────────────────────
# sys.path — project root so paper_trading imports work
# ─────────────────────────────────────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ─────────────────────────────────────────────────────────────────────────────
# Paper-trading imports
# ─────────────────────────────────────────────────────────────────────────────

from paper_trading.portfolio import (
    get_closed_trades,
    get_open_positions,
    init_paper_trading_tables,
    reset_portfolio,
)
from paper_trading.report import (
    PortfolioSummary,
    get_performance_by_quality,
    get_portfolio_summary,
)

# ─────────────────────────────────────────────────────────────────────────────
# Dashboard component imports
# ─────────────────────────────────────────────────────────────────────────────

try:
    from dashboard.components.charts import render_equity_curve
    from dashboard.components.metrics import render_portfolio_kpis
    from dashboard.components.tables import (
        render_portfolio_table,
        render_trades_history_table,
    )
    _COMPONENTS_OK = True
except ImportError as _comp_err:
    _COMPONENTS_OK = False
    _COMP_ERR_MSG = str(_comp_err)

# ─────────────────────────────────────────────────────────────────────────────
# Config / paths
# ─────────────────────────────────────────────────────────────────────────────

_DB_PATH = _PROJECT_ROOT / "data" / "minervini.db"
_PENDING_ORDERS_PATH = _PROJECT_ROOT / "data" / "paper_trading" / "pending_orders.json"
_SETTINGS_PATH = _PROJECT_ROOT / "config" / "settings.yaml"


def _is_paper_trading_enabled() -> bool:
    """Read paper_trading.enabled from settings.yaml (default True on error)."""
    try:
        import yaml
        with open(_SETTINGS_PATH) as fh:
            cfg = yaml.safe_load(fh)
        return bool(cfg.get("paper_trading", {}).get("enabled", True))
    except Exception:
        return True


def _get_initial_capital() -> float:
    """Read paper_trading.initial_capital from settings.yaml (default 100 000)."""
    try:
        import yaml
        with open(_SETTINGS_PATH) as fh:
            cfg = yaml.safe_load(fh)
        return float(cfg.get("paper_trading", {}).get("initial_capital", 100_000))
    except Exception:
        return 100_000.0


# ─────────────────────────────────────────────────────────────────────────────
# Cached data loaders
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=60)
def _load_summary() -> Optional[PortfolioSummary]:
    """
    Load PortfolioSummary with a 60-second TTL.
    Returns None if the portfolio tables have not been initialised yet.
    """
    try:
        init_paper_trading_tables(_DB_PATH)
        return get_portfolio_summary(db_path=_DB_PATH, current_prices=None)
    except Exception as exc:
        st.session_state["_portfolio_load_error"] = str(exc)
        return None


@st.cache_data(ttl=60)
def _load_closed_trades() -> list[dict]:
    """Return closed trades as a list of dicts (Trade dataclass → dict)."""
    try:
        trades = get_closed_trades(_DB_PATH)
        return [
            {
                "symbol":        t.symbol,
                "entry_date":    t.entry_date,
                "exit_date":     t.exit_date,
                "entry_price":   t.entry_price,
                "exit_price":    t.exit_price,
                "qty":           t.qty,
                "pnl":           t.pnl or 0.0,
                "pnl_pct":       t.pnl_pct or 0.0,
                "exit_reason":   t.exit_reason,
                "setup_quality": t.setup_quality,
                "rr_ratio":      t.rr_ratio,
                "days_held": (
                    (t.exit_date - t.entry_date).days
                    if t.exit_date and t.entry_date else 0
                ),
            }
            for t in trades
        ]
    except Exception:
        return []


@st.cache_data(ttl=60)
def _load_open_positions():
    """Return raw open Trade objects."""
    try:
        return get_open_positions(_DB_PATH)
    except Exception:
        return []


@st.cache_data(ttl=60)
def _load_performance_by_quality() -> dict:
    try:
        return get_performance_by_quality(_DB_PATH)
    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# Derived / helper utilities
# ─────────────────────────────────────────────────────────────────────────────

def _load_pending_orders() -> list[dict]:
    """Load pending orders from JSON file if it exists."""
    if not _PENDING_ORDERS_PATH.exists():
        return []
    try:
        with open(_PENDING_ORDERS_PATH) as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return list(data.values())
        return []
    except Exception:
        return []


def _trades_to_df(trades: list[dict]) -> pd.DataFrame:
    """Convert list of trade dicts to a DataFrame suitable for charting/display."""
    if not trades:
        return pd.DataFrame()
    return pd.DataFrame(trades)


def _compute_equity_curve(closed: list[dict], initial_capital: float) -> pd.DataFrame:
    """
    Build a running equity curve DataFrame from closed trades.

    Returns a DataFrame with columns: exit_date, portfolio_value.
    Used as input to render_equity_curve() — that function expects
    a trades DataFrame with exit_date and pnl columns.
    """
    if not closed:
        return pd.DataFrame()
    df = pd.DataFrame(closed).sort_values("exit_date")
    df["cumulative_pnl"] = df["pnl"].cumsum()
    df["portfolio_value"] = initial_capital + df["cumulative_pnl"]
    return df


def _max_drawdown(equity: pd.Series) -> float:
    """Compute max drawdown percentage from an equity series."""
    if equity.empty or len(equity) < 2:
        return 0.0
    rolling_max = equity.cummax()
    drawdown = (equity - rolling_max) / rolling_max * 100.0
    return float(drawdown.min())


def _get_init_date(db_path: Path) -> Optional[str]:
    """Return the date the portfolio was first initialised (first trade entry_date)."""
    try:
        import sqlite3
        conn = sqlite3.connect(str(db_path), timeout=5)
        row = conn.execute(
            "SELECT MIN(entry_date) FROM paper_positions"
        ).fetchone()
        conn.close()
        return row[0] if row and row[0] else None
    except Exception:
        return None


def _trading_days_active(start_date_str: Optional[str]) -> int:
    """Return number of calendar days since the first trade (rough estimate)."""
    if not start_date_str:
        return 0
    try:
        start = date.fromisoformat(start_date_str[:10])
        return (date.today() - start).days
    except Exception:
        return 0


# ─────────────────────────────────────────────────────────────────────────────
# Section 1 — Page header
# ─────────────────────────────────────────────────────────────────────────────

st.title("💼 Paper Trading Portfolio")
st.markdown(
    "<p style='color:#8b949e; margin-top:-0.5rem;'>"
    "Simulated portfolio — not real money</p>",
    unsafe_allow_html=True,
)

if not _is_paper_trading_enabled():
    st.warning(
        "Paper trading is disabled. Enable in config/settings.yaml.",
        icon="⚠️",
    )
    st.stop()

st.divider()

# ─────────────────────────────────────────────────────────────────────────────
# Section 2 — KPI row
# ─────────────────────────────────────────────────────────────────────────────

summary = _load_summary()

if summary is None:
    _load_err = st.session_state.get("_portfolio_load_error", "Unknown error")
    st.info(
        "📭 No paper trading data yet. "
        "The portfolio will populate after the first pipeline run.\n\n"
        f"_(Debug: {_load_err})_"
    )
    st.stop()

# Initialisation date + trading-days-active sub-header
_init_date_str = _get_init_date(_DB_PATH)
_days_active = _trading_days_active(_init_date_str)

if _init_date_str:
    _init_label = date.fromisoformat(_init_date_str[:10]).strftime("%d %b %Y")
    st.caption(
        f"📅 Portfolio started: **{_init_label}** &nbsp;·&nbsp; "
        f"🗓️ Active for **{_days_active} days**"
    )

# KPI widget row
if _COMPONENTS_OK:
    try:
        render_portfolio_kpis(summary)
    except Exception as _kpi_err:
        st.error(f"KPI render error: {_kpi_err}")
        _fallback_cols = st.columns(5)
        _fallback_cols[0].metric("Total Value",    f"₹{summary.total_value:,.0f}")
        _fallback_cols[1].metric("Total Return",   f"{summary.total_return_pct:+.2f}%")
        _fallback_cols[2].metric("Realised P&L",   f"₹{summary.realised_pnl:+,.0f}")
        _fallback_cols[3].metric("Win Rate",        f"{summary.win_rate:.1f}%")
        _fallback_cols[4].metric("Open Positions", summary.open_trades)
else:
    st.warning(f"Dashboard components unavailable ({_COMP_ERR_MSG}). Showing raw metrics.")
    _kpi_cols = st.columns(5)
    _kpi_cols[0].metric("Total Value",    f"₹{summary.total_value:,.0f}")
    _kpi_cols[1].metric("Total Return",   f"{summary.total_return_pct:+.2f}%")
    _kpi_cols[2].metric("Realised P&L",   f"₹{summary.realised_pnl:+,.0f}")
    _kpi_cols[3].metric("Win Rate",        f"{summary.win_rate:.1f}%")
    _kpi_cols[4].metric("Open Positions", summary.open_trades)

st.divider()


# ─────────────────────────────────────────────────────────────────────────────
# Section 3 — Equity curve
# ─────────────────────────────────────────────────────────────────────────────

st.subheader("📈 Equity Curve")

_closed = _load_closed_trades()
_initial_cap = _get_initial_capital()
_equity_df = _compute_equity_curve(_closed, _initial_cap)

if _closed:
    if _COMPONENTS_OK:
        try:
            _fig = render_equity_curve(_trades_to_df(_closed))
            st.pyplot(_fig, use_container_width=True)
        except Exception as _eq_err:
            st.error(f"Equity curve render error: {_eq_err}")
            # Simple fallback line chart
            if not _equity_df.empty:
                _chart_df = _equity_df[["exit_date", "portfolio_value"]].set_index("exit_date")
                st.line_chart(_chart_df, use_container_width=True)
    else:
        # Fallback when components not available
        if not _equity_df.empty:
            _chart_df = _equity_df[["exit_date", "portfolio_value"]].set_index("exit_date")
            st.line_chart(_chart_df, use_container_width=True)

    # Key stats row below the chart
    _pnl_pcts = [t["pnl_pct"] for t in _closed]
    _equity_series = _equity_df["portfolio_value"] if not _equity_df.empty else pd.Series([_initial_cap])
    _total_ret_pct  = summary.total_return_pct
    _max_dd_pct     = _max_drawdown(_equity_series)
    _best_trade_pct = max(_pnl_pcts) if _pnl_pcts else 0.0
    _worst_trade_pct = min(_pnl_pcts) if _pnl_pcts else 0.0

    _stat_cols = st.columns(4)
    _stat_cols[0].metric(
        "Total Return",
        f"{_total_ret_pct:+.2f}%",
        delta=None,
    )
    _stat_cols[1].metric(
        "Max Drawdown",
        f"{_max_dd_pct:.2f}%",
    )
    _stat_cols[2].metric(
        "Best Trade",
        f"{_best_trade_pct:+.2f}%",
    )
    _stat_cols[3].metric(
        "Worst Trade",
        f"{_worst_trade_pct:+.2f}%",
    )
else:
    st.info(
        "📊 Equity curve will appear here once trades have been closed. "
        "Run the pipeline to start paper trading."
    )

st.divider()


# ─────────────────────────────────────────────────────────────────────────────
# Section 4 — Tabs
# ─────────────────────────────────────────────────────────────────────────────

_tab_open, _tab_history, _tab_perf = st.tabs([
    "📂 Open Positions",
    "📜 Trade History",
    "📊 Performance",
])

# ══════════════════════════════════════════════════════════════════════════════
# Tab 1 — Open Positions
# ══════════════════════════════════════════════════════════════════════════════

with _tab_open:
    _open_positions = _load_open_positions()

    if not _open_positions:
        st.info(
            "No open positions. "
            "Signals will generate trades after the next run."
        )
    else:
        if _COMPONENTS_OK:
            try:
                render_portfolio_table(_open_positions, current_prices=None)
            except Exception as _pt_err:
                st.error(f"Table render error: {_pt_err}")
                # plain dataframe fallback
                _pos_rows = [
                    {
                        "Symbol":       p.symbol,
                        "Entry Date":   p.entry_date,
                        "Entry ₹":      f"₹{p.entry_price:,.2f}",
                        "Qty":          p.qty,
                        "Stop ₹":       f"₹{p.stop_loss:,.2f}",
                        "Target ₹":     f"₹{p.target_price:,.2f}" if p.target_price else "—",
                        "Quality":      p.setup_quality or "—",
                        "Score":        p.score,
                        "Pyramided":    "✅" if p.pyramided else "—",
                    }
                    for p in _open_positions
                ]
                st.dataframe(pd.DataFrame(_pos_rows), use_container_width=True)
        else:
            _pos_rows = [
                {
                    "Symbol":       p.symbol,
                    "Entry Date":   p.entry_date,
                    "Entry ₹":      f"₹{p.entry_price:,.2f}",
                    "Qty":          p.qty,
                    "Stop ₹":       f"₹{p.stop_loss:,.2f}",
                    "Target ₹":     f"₹{p.target_price:,.2f}" if p.target_price else "—",
                    "Quality":      p.setup_quality or "—",
                    "Score":        p.score,
                    "Pyramided":    "✅" if p.pyramided else "—",
                }
                for p in _open_positions
            ]
            st.dataframe(pd.DataFrame(_pos_rows), use_container_width=True)

    # ── Pending Orders section ─────────────────────────────────────────────
    st.markdown("---")
    st.subheader("⏳ Pending Orders")
    _pending = _load_pending_orders()
    if not _pending:
        st.caption("No pending orders in the queue.")
    else:
        st.caption(
            f"{len(_pending)} order(s) queued for next market open (09:15 IST)."
        )
        _pend_rows = []
        for _o in _pending:
            _pend_rows.append({
                "Symbol":       _o.get("symbol", "—"),
                "Type":         _o.get("order_type", "—"),
                "Entry ₹":      f"₹{_o['entry_price']:,.2f}" if _o.get("entry_price") else "—",
                "Stop ₹":       f"₹{_o['stop_loss']:,.2f}"   if _o.get("stop_loss")   else "—",
                "Quality":      _o.get("setup_quality", "—"),
                "Expires":      _o.get("expires_at", "—"),
            })
        st.dataframe(pd.DataFrame(_pend_rows), use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# Tab 2 — Trade History
# ══════════════════════════════════════════════════════════════════════════════

with _tab_history:
    if not _closed:
        st.info("No closed trades yet. Trade history will appear here after positions are exited.")
    else:
        _hist_df = _trades_to_df(_closed)

        # ── Filter controls ────────────────────────────────────────────────
        _filter_cols = st.columns([2, 2, 1])
        with _filter_cols[0]:
            _qual_options = sorted(_hist_df["setup_quality"].dropna().unique().tolist())
            _qual_filter = st.multiselect(
                "Filter by Quality",
                options=_qual_options,
                default=_qual_options,
                key="hist_quality_filter",
            )
        with _filter_cols[1]:
            _all_dates = _hist_df["exit_date"].dropna()
            _min_date = pd.to_datetime(_all_dates.min()).date() if not _all_dates.empty else date.today()
            _max_date = pd.to_datetime(_all_dates.max()).date() if not _all_dates.empty else date.today()
            _date_range = st.date_input(
                "Exit Date Range",
                value=(_min_date, _max_date),
                min_value=_min_date,
                max_value=_max_date,
                key="hist_date_range",
            )

        # ── Apply filters ──────────────────────────────────────────────────
        _filtered_df = _hist_df.copy()
        if _qual_filter:
            _filtered_df = _filtered_df[
                _filtered_df["setup_quality"].isin(_qual_filter)
            ]
        if isinstance(_date_range, (list, tuple)) and len(_date_range) == 2:
            _dr_start, _dr_end = _date_range
            _filtered_df["_exit_dt"] = pd.to_datetime(_filtered_df["exit_date"]).dt.date
            _filtered_df = _filtered_df[
                (_filtered_df["_exit_dt"] >= _dr_start) &
                (_filtered_df["_exit_dt"] <= _dr_end)
            ].drop(columns=["_exit_dt"])

        st.caption(f"Showing **{len(_filtered_df)}** of **{len(_hist_df)}** closed trades")

        # ── Render table ───────────────────────────────────────────────────
        if _COMPONENTS_OK:
            try:
                _trades_objs = get_closed_trades(_DB_PATH)
                _sym_filter_set = set(_filtered_df["symbol"].tolist()) if not _filtered_df.empty else set()
                _trades_filtered = [t for t in _trades_objs if t.symbol in _sym_filter_set]
                render_trades_history_table(_trades_filtered)
            except Exception as _th_err:
                st.error(f"Table render error: {_th_err}")
                st.dataframe(_filtered_df, use_container_width=True)
        else:
            st.dataframe(_filtered_df, use_container_width=True)

        # ── Export CSV ─────────────────────────────────────────────────────
        _csv_data = _filtered_df.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="📥 Export CSV",
            data=_csv_data,
            file_name=f"paper_trades_{date.today().isoformat()}.csv",
            mime="text/csv",
            key="export_trades_csv",
        )


# ══════════════════════════════════════════════════════════════════════════════
# Tab 3 — Performance
# ══════════════════════════════════════════════════════════════════════════════

with _tab_perf:
    if not _closed:
        st.info("Performance analytics will appear here once trades are closed.")
    else:
        _perf_by_quality = _load_performance_by_quality()

        # ── Win rate by setup quality (bar chart) ─────────────────────────
        st.markdown("#### Win Rate by Setup Quality")
        if _perf_by_quality:
            _quality_order = ["A+", "A", "B", "C", "UNKNOWN"]
            _wr_data = {
                q: _perf_by_quality[q]["win_rate"]
                for q in _quality_order
                if q in _perf_by_quality
            }
            if _wr_data:
                _wr_df = pd.DataFrame(
                    {"Win Rate (%)": list(_wr_data.values())},
                    index=list(_wr_data.keys()),
                )
                st.bar_chart(_wr_df, use_container_width=True)
        else:
            st.caption("No closed trades with quality labels yet.")

        # ── Avg hold time per quality ──────────────────────────────────────
        st.markdown("#### Avg Hold Time by Quality (Days)")
        _hist_df2 = _trades_to_df(_closed)
        if not _hist_df2.empty and "setup_quality" in _hist_df2.columns:
            _hold_by_quality = (
                _hist_df2.groupby("setup_quality")["days_held"]
                .mean()
                .round(1)
                .rename("Avg Days Held")
            )
            _hold_by_quality = _hold_by_quality.reindex(
                [q for q in ["A+", "A", "B", "C"] if q in _hold_by_quality.index]
            )
            if not _hold_by_quality.empty:
                st.bar_chart(_hold_by_quality, use_container_width=True)
            else:
                st.caption("Insufficient data.")
        else:
            st.caption("No quality-labelled trades yet.")

        st.markdown("---")

        # ── P&L distribution histogram ─────────────────────────────────────
        st.markdown("#### P&L Distribution")
        _pnl_vals = _hist_df2["pnl_pct"].dropna().tolist() if not _hist_df2.empty else []
        if _pnl_vals:
            _buckets_labels = ["< -20%", "-20 to -10%", "-10 to 0%",
                               "0 to +10%", "+10 to +20%", "+20 to +30%", "> +30%"]
            _bucket_counts = [0] * 7
            for _v in _pnl_vals:
                if _v < -20:
                    _bucket_counts[0] += 1
                elif _v < -10:
                    _bucket_counts[1] += 1
                elif _v < 0:
                    _bucket_counts[2] += 1
                elif _v < 10:
                    _bucket_counts[3] += 1
                elif _v < 20:
                    _bucket_counts[4] += 1
                elif _v < 30:
                    _bucket_counts[5] += 1
                else:
                    _bucket_counts[6] += 1
            _hist_chart_df = pd.DataFrame(
                {"Trades": _bucket_counts},
                index=_buckets_labels,
            )
            st.bar_chart(_hist_chart_df, use_container_width=True)
        else:
            st.caption("No P&L data available yet.")


        st.markdown("---")

        # ── Monthly P&L summary table ──────────────────────────────────────
        st.markdown("#### Monthly P&L Summary")
        if not _hist_df2.empty:
            _monthly_df = _hist_df2.copy()
            # Parse exit_date robustly
            _monthly_df["_exit_dt"] = pd.to_datetime(
                _monthly_df["exit_date"], errors="coerce"
            )
            _monthly_df = _monthly_df.dropna(subset=["_exit_dt"])
            _monthly_df["month"] = _monthly_df["_exit_dt"].dt.to_period("M").astype(str)

            _monthly_rows = []
            for _month, _grp in _monthly_df.groupby("month", sort=True):
                _m_trades = len(_grp)
                _m_pnl    = _grp["pnl"].sum()
                _m_wins   = (_grp["pnl"] > 0).sum()
                _m_wr     = round(_m_wins / _m_trades * 100, 1) if _m_trades else 0.0
                _monthly_rows.append({
                    "Month":          _month,
                    "Trades":         _m_trades,
                    "Realised P&L":   f"₹{_m_pnl:+,.0f}",
                    "Win Rate":       f"{_m_wr:.1f}%",
                })

            if _monthly_rows:
                st.dataframe(
                    pd.DataFrame(_monthly_rows),
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.caption("No monthly data to display.")
        else:
            st.caption("No closed trades for monthly summary.")

# ─────────────────────────────────────────────────────────────────────────────
# Section 5 — Danger zone: Reset Portfolio
# ─────────────────────────────────────────────────────────────────────────────

st.divider()

with st.expander("⚠️ Reset Portfolio", expanded=False):
    st.error(
        "**Danger zone.** This permanently deletes all paper trade history — "
        "open positions, closed trades, and portfolio state. "
        "This action cannot be undone.",
        icon="🚨",
    )

    _reset_confirmed_cb = st.checkbox(
        "I understand this will permanently delete all paper trade history.",
        key="reset_confirm_checkbox",
    )

    _reset_text = st.text_input(
        'Type **RESET** to confirm',
        placeholder="RESET",
        key="reset_confirm_text",
        help='You must type the word RESET (all caps) to enable the button.',
    )

    _reset_ready = _reset_confirmed_cb and _reset_text.strip() == "RESET"

    if st.button(
        "🗑️ Reset Portfolio",
        disabled=not _reset_ready,
        type="primary",
        key="reset_portfolio_btn",
    ):
        if not _reset_confirmed_cb:
            st.error("Please tick the confirmation checkbox first.")
        elif _reset_text.strip() != "RESET":
            st.error('Please type "RESET" exactly in the text box above.')
        else:
            try:
                _cap = _get_initial_capital()
                reset_portfolio(db_path=_DB_PATH, initial_capital=_cap)
                # Clear all caches so the page reloads fresh data
                _load_summary.clear()
                _load_closed_trades.clear()
                _load_open_positions.clear()
                _load_performance_by_quality.clear()
                st.success(
                    f"✅ Portfolio reset. Starting capital restored to ₹{_cap:,.0f}."
                )
                st.rerun()
            except Exception as _reset_err:
                st.error(f"Reset failed: {_reset_err}")

    if not _reset_ready and (_reset_confirmed_cb or _reset_text):
        st.caption(
            "☝️ Both the checkbox must be ticked **and** you must type RESET "
            "to unlock the button."
        )
