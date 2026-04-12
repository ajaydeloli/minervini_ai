"""
dashboard/components/__init__.py
─────────────────────────────────
Exports the shared Streamlit dashboard components.
"""

from dashboard.components.charts import (
    render_candlestick_chart,
    render_cached_chart,
    render_equity_curve,
    render_backtest_equity_curve,
)
from dashboard.components.tables import (
    render_watchlist_badge,
    render_sepa_results_table,
    render_portfolio_table,
    render_trades_history_table,
    render_backtest_summary_table,
)
from dashboard.components.metrics import (
    render_score_gauge,
    render_trend_template_checklist,
    render_fundamental_scorecard,
    render_vcp_summary,
    render_run_summary_kpis,
    render_portfolio_kpis,
)

__all__ = [
    # charts
    "render_candlestick_chart",
    "render_cached_chart",
    "render_equity_curve",
    "render_backtest_equity_curve",
    # tables
    "render_watchlist_badge",
    "render_sepa_results_table",
    "render_portfolio_table",
    "render_trades_history_table",
    "render_backtest_summary_table",
    # metrics
    "render_score_gauge",
    "render_trend_template_checklist",
    "render_fundamental_scorecard",
    "render_vcp_summary",
    "render_run_summary_kpis",
    "render_portfolio_kpis",
]
