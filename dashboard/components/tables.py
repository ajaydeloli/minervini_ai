"""
dashboard/components/tables.py
───────────────────────────────
Styled Streamlit table renderers used across all dashboard pages.

All renderers wrap st.dataframe() with consistent colour coding, column
configuration, and the ★ watchlist-badge logic described in PROJECT_DESIGN.md
(§13.2 — Streamlit MVP).

Design mandates
───────────────
  • Dark-theme colour palette — all colours are hex, not named colours.
  • Use st.dataframe with pandas Styler for colour coding.
  • Use st.column_config where it adds value (progress bars, links, etc.).
  • All functions must be importable without Streamlit active; every
    ``st.*`` call lives inside the function body, never at module level.
  • Graceful handling of empty lists (show an info banner, not an exception).
  • Type hints and docstrings on every public symbol.

Colour palette (dark theme)
───────────────────────────
  Gold    : #B8860B  — A+ quality, R-multiple ≥ 2
  Green   : #1F6B2A  — A quality, positive P&L, low risk, Bull regime, R ≥ 1
  Yellow  : #5C5800  — B quality
  Grey    : #3A3A3A  — C quality, Sideways regime
  Red     : #6B1F1F  — negative P&L, high risk (> 8%), Bear regime, R < 1
  Orange  : #7A4500  — medium risk (5-8%)
  WL row  : #1A2B1A  — watchlist symbol row highlight
  WL text : #FFD700  — watchlist star & symbol colour
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

# ─────────────────────────────────────────────────────────────────────────────
# Colour constants (hex, dark-theme safe)
# ─────────────────────────────────────────────────────────────────────────────

_C_GOLD        = "#B8860B"
_C_GREEN       = "#1F6B2A"
_C_YELLOW      = "#5C5800"
_C_GREY        = "#3A3A3A"
_C_RED         = "#6B1F1F"
_C_ORANGE      = "#7A4500"
_C_WL_ROW      = "#1A2B1A"
_C_WL_TEXT     = "#FFD700"
_C_TRANSPARENT = "transparent"

# Score gradient endpoints (green → red)
_C_SCORE_HIGH  = "#1F6B2A"   # ≈ 100
_C_SCORE_LOW   = "#6B1F1F"   # ≈ 0

# ─────────────────────────────────────────────────────────────────────────────
# 1. Watchlist badge helper
# ─────────────────────────────────────────────────────────────────────────────

def render_watchlist_badge(symbol: str, watchlist_symbols: set[str]) -> str:
    """
    Return a decorated symbol string for use in table cells.

    Parameters
    ──────────
    symbol            : NSE ticker, e.g. "DIXON".
    watchlist_symbols : Set of currently watched symbols (uppercase).

    Returns
    ───────
    "★ DIXON"  if symbol is in watchlist_symbols
    "DIXON"    otherwise
    """
    if symbol.upper() in {s.upper() for s in watchlist_symbols}:
        return f"★ {symbol.upper()}"
    return symbol.upper()

# ─────────────────────────────────────────────────────────────────────────────
# Internal styler helpers
# ─────────────────────────────────────────────────────────────────────────────

def _quality_bg(val: str) -> str:
    """Return background CSS for a Quality cell."""
    mapping = {
        "A+":   f"background-color: {_C_GOLD}",
        "A":    f"background-color: {_C_GREEN}",
        "B":    f"background-color: {_C_YELLOW}",
        "C":    f"background-color: {_C_GREY}",
        "FAIL": f"background-color: {_C_RED}",
    }
    return mapping.get(str(val).strip(), "")


def _risk_bg(val: float | None) -> str:
    """Return background CSS for a Risk% cell."""
    try:
        v = float(val)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""
    if v > 8.0:
        return f"background-color: {_C_RED}"
    if v >= 5.0:
        return f"background-color: {_C_ORANGE}"
    return f"background-color: {_C_GREEN}"


def _pnl_bg(val: float | None) -> str:
    """Return background CSS for a P&L cell."""
    try:
        v = float(val)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""
    return (
        f"background-color: {_C_GREEN}"
        if v >= 0
        else f"background-color: {_C_RED}"
    )


def _rmultiple_bg(val: float | None) -> str:
    """Return background CSS for an R-Multiple cell."""
    try:
        v = float(val)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""
    if v >= 2.0:
        return f"background-color: {_C_GOLD}"
    if v >= 1.0:
        return f"background-color: {_C_GREEN}"
    return f"background-color: {_C_RED}"


def _regime_bg(val: str) -> str:
    """Return background CSS for a Regime cell."""
    mapping = {
        "Bull":     f"background-color: {_C_GREEN}",
        "Bear":     f"background-color: {_C_RED}",
        "Sideways": f"background-color: {_C_GREY}",
    }
    return mapping.get(str(val).strip(), "")


def _score_gradient(val: float | None) -> str:
    """
    Interpolate background colour between green (high score) and red (low score).
    Score range is 0–100.
    """
    try:
        v = max(0.0, min(100.0, float(val)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""
    # Linear interpolation: 100 → green, 0 → red
    t = v / 100.0
    r = int(0x6B + t * (0x1F - 0x6B))
    g = int(0x1F + t * (0x6B - 0x1F))
    b = int(0x1F + t * (0x2A - 0x1F))
    return f"background-color: #{r:02X}{g:02X}{b:02X}"


# ─────────────────────────────────────────────────────────────────────────────
# 2. render_sepa_results_table
# ─────────────────────────────────────────────────────────────────────────────

def render_sepa_results_table(
    results: list[dict],
    watchlist_symbols: Optional[set[str]] = None,
) -> None:
    """
    Render a styled st.dataframe of SEPA screening results.

    Columns (in order):
        Symbol, Quality, Score, Stage, RS Rating, VCP, Entry, Stop, Risk%, R:R,
        Fund., News

    Colour coding:
        Quality  — A+ = gold bg, A = green bg, B = yellow bg, C = grey bg
        Score    — gradient green (high) → red (low)
        Risk%    — red > 8%, orange 5–8%, green < 5%

    Watchlist behaviour:
        • Symbol cells for watchlist symbols show "★ SYMBOL".
        • Watchlist rows have a highlighted row background.
        • Table is sorted: watchlist symbols first, then by score desc.
        • A "Show watchlist only" checkbox appears above the table.

    Parameters
    ──────────
    results           : List of SEPAResult dicts (from screener/results.py or
                        storage/sqlite_store.py).
    watchlist_symbols : Set of watchlist symbols (uppercase).  Pass None or an
                        empty set to disable watchlist highlighting.

    Returns
    ───────
    None — renders directly via st.dataframe.
    """
    import streamlit as st

    wl: set[str] = {s.upper() for s in (watchlist_symbols or set())}

    if not results:
        st.info("No SEPA results to display.")
        return

    # ── Build DataFrame ──────────────────────────────────────────────────────
    rows = []
    for r in results:
        sym_raw = str(r.get("symbol", "")).upper()
        in_wl   = sym_raw in wl
        rows.append({
            "_in_wl":    in_wl,
            "_score_raw": float(r.get("score") or 0),
            "Symbol":    render_watchlist_badge(sym_raw, wl),
            "Quality":   str(r.get("setup_quality") or "—"),
            "Score":     int(r.get("score") or 0),
            "Stage":     int(r.get("stage") or 0),
            "RS Rating": int(r.get("rs_rating") or 0),
            "VCP":       "✓" if r.get("vcp_qualified") else "✗",
            "Entry":     r.get("entry_price"),
            "Stop":      r.get("stop_loss"),
            "Risk%":     round(float(r.get("risk_pct") or 0), 2),
            "R:R":       round(float(r.get("rr_ratio") or 0), 2) if r.get("rr_ratio") else None,
            "Fund.":     "✓" if r.get("fundamental_pass") else ("✗" if r.get("fundamental_pass") is False else "—"),
            "News":      round(float(r.get("news_score") or 0), 1) if r.get("news_score") is not None else None,
        })

    df = pd.DataFrame(rows)

    # ── Sort: watchlist first, then by score desc ────────────────────────────
    df = df.sort_values(["_in_wl", "_score_raw"], ascending=[False, False])
    df = df.reset_index(drop=True)

    # ── Watchlist filter checkbox ────────────────────────────────────────────
    wl_only = st.checkbox("★ Show watchlist symbols only", key="sepa_wl_filter")
    if wl_only:
        df = df[df["_in_wl"]]

    display_df = df.drop(columns=["_in_wl", "_score_raw"])

    # ── Pandas Styler ────────────────────────────────────────────────────────
    def _row_style(row: pd.Series) -> list[str]:
        sym = str(row.get("Symbol", ""))
        if sym.startswith("★"):
            return [f"background-color: {_C_WL_ROW}; color: {_C_WL_TEXT}"] * len(row)
        return [""] * len(row)

    styler = (
        display_df.style
        .apply(_row_style, axis=1)
        .applymap(_quality_bg, subset=["Quality"])
        .applymap(_score_gradient, subset=["Score"])
        .applymap(_risk_bg, subset=["Risk%"])
        .format(
            {
                "Entry":  lambda v: f"₹{v:,.2f}" if v is not None else "—",
                "Stop":   lambda v: f"₹{v:,.2f}" if v is not None else "—",
                "Risk%":  lambda v: f"{v:.2f}%" if v is not None else "—",
                "R:R":    lambda v: f"{v:.2f}" if v is not None else "—",
                "News":   lambda v: f"{v:+.1f}" if v is not None else "—",
            },
            na_rep="—",
        )
    )

    # ── Column config ────────────────────────────────────────────────────────
    col_cfg = {
        "Score": st.column_config.ProgressColumn(
            "Score",
            min_value=0,
            max_value=100,
            format="%d",
        ),
        "RS Rating": st.column_config.NumberColumn("RS Rating", format="%d"),
        "Stage":     st.column_config.NumberColumn("Stage", format="%d"),
    }

    st.dataframe(
        styler,
        column_config=col_cfg,
        use_container_width=True,
        hide_index=True,
    )

# ─────────────────────────────────────────────────────────────────────────────
# 3. render_portfolio_table
# ─────────────────────────────────────────────────────────────────────────────

def render_portfolio_table(
    positions: list[dict],
    current_prices: Optional[dict[str, float]] = None,
) -> None:
    """
    Render open paper trading positions as a styled st.dataframe.

    Columns:
        Symbol, Entry Price, Qty, Current Price, Unrealised P&L, P&L%, Stop Loss,
        Quality

    Colour coding:
        Unrealised P&L / P&L% — green if positive, red if negative.

    Parameters
    ──────────
    positions      : List of open-position dicts.  Each dict is expected to
                     contain the keys produced by paper_trading/report.py's
                     ``get_portfolio_summary().positions`` list:
                     symbol, entry_price, qty, stop_loss, setup_quality,
                     unrealised_pnl, unrealised_pnl_pct.
    current_prices : Mapping symbol → current market price.  When None or when
                     a symbol is absent, current price and P&L are shown as "—".

    Returns
    ───────
    None — renders directly via st.dataframe.
    """
    import streamlit as st

    if not positions:
        st.info("No open positions.")
        return

    prices = current_prices or {}
    rows = []
    for p in positions:
        sym  = str(p.get("symbol", "")).upper()
        ep   = p.get("entry_price")
        qty  = p.get("qty")
        sl   = p.get("stop_loss")
        qual = p.get("setup_quality") or "—"

        if sym in prices:
            cp     = prices[sym]
            upnl   = (cp - ep) * qty if ep and qty else None
            upnl_p = ((cp / ep) - 1.0) * 100.0 if ep else None
        else:
            cp     = p.get("current_price")
            upnl   = p.get("unrealised_pnl")
            upnl_p = p.get("unrealised_pnl_pct")

        rows.append({
            "Symbol":          sym,
            "Entry Price":     ep,
            "Qty":             qty,
            "Current Price":   cp,
            "Unrealised P&L":  upnl,
            "P&L%":            upnl_p,
            "Stop Loss":       sl,
            "Quality":         qual,
        })

    df = pd.DataFrame(rows)

    def _pnl_color(val: float | None) -> str:
        return _pnl_bg(val)

    styler = (
        df.style
        .applymap(_pnl_color, subset=["Unrealised P&L", "P&L%"])
        .applymap(_quality_bg, subset=["Quality"])
        .format(
            {
                "Entry Price":    lambda v: f"₹{v:,.2f}" if v is not None else "—",
                "Current Price":  lambda v: f"₹{v:,.2f}" if v is not None else "—",
                "Stop Loss":      lambda v: f"₹{v:,.2f}" if v is not None else "—",
                "Unrealised P&L": lambda v: f"₹{v:+,.2f}" if v is not None else "—",
                "P&L%":           lambda v: f"{v:+.2f}%" if v is not None else "—",
                "Qty":            lambda v: f"{int(v)}" if v is not None else "—",
            },
            na_rep="—",
        )
    )

    st.dataframe(styler, use_container_width=True, hide_index=True)

# ─────────────────────────────────────────────────────────────────────────────
# 4. render_trades_history_table
# ─────────────────────────────────────────────────────────────────────────────

def render_trades_history_table(trades: list[dict]) -> None:
    """
    Render closed paper trade history as a styled, sortable st.dataframe.

    Columns:
        Symbol, Entry Date, Exit Date, Entry Price, Exit Price, P&L, P&L%,
        R-Multiple, Quality, Exit Reason

    Colour coding:
        P&L / P&L%   — green if positive, red if negative.
        R-Multiple   — gold (≥ 2), green (≥ 1), red (< 1).
        Quality      — same quality colour scheme as SEPA table.

    Parameters
    ──────────
    trades : List of closed-trade dicts.  Expected keys (all optional / nullable):
             symbol, entry_date, exit_date, entry_price, exit_price,
             pnl, pnl_pct, rr_ratio, setup_quality, exit_reason.

    Returns
    ───────
    None — renders directly via st.dataframe.
    """
    import streamlit as st

    if not trades:
        st.info("No closed trades to display.")
        return

    rows = []
    for t in trades:
        rows.append({
            "Symbol":      str(t.get("symbol", "")).upper(),
            "Entry Date":  t.get("entry_date"),
            "Exit Date":   t.get("exit_date"),
            "Entry Price": t.get("entry_price"),
            "Exit Price":  t.get("exit_price"),
            "P&L":         t.get("pnl"),
            "P&L%":        t.get("pnl_pct"),
            "R-Multiple":  t.get("rr_ratio"),
            "Quality":     t.get("setup_quality") or "—",
            "Exit Reason": t.get("exit_reason") or "—",
        })

    df = pd.DataFrame(rows)

    styler = (
        df.style
        .applymap(_pnl_bg,       subset=["P&L", "P&L%"])
        .applymap(_rmultiple_bg, subset=["R-Multiple"])
        .applymap(_quality_bg,   subset=["Quality"])
        .format(
            {
                "Entry Price": lambda v: f"₹{v:,.2f}" if v is not None else "—",
                "Exit Price":  lambda v: f"₹{v:,.2f}" if v is not None else "—",
                "P&L":         lambda v: f"₹{v:+,.2f}" if v is not None else "—",
                "P&L%":        lambda v: f"{v:+.2f}%"  if v is not None else "—",
                "R-Multiple":  lambda v: f"{v:.2f}×"   if v is not None else "—",
                "Entry Date":  lambda v: str(v) if v is not None else "—",
                "Exit Date":   lambda v: str(v) if v is not None else "—",
            },
            na_rep="—",
        )
    )

    col_cfg = {
        "P&L": st.column_config.NumberColumn("P&L", format="₹%.2f"),
        "R-Multiple": st.column_config.NumberColumn("R-Multiple", format="%.2f×"),
    }

    st.dataframe(
        styler,
        column_config=col_cfg,
        use_container_width=True,
        hide_index=True,
    )

# ─────────────────────────────────────────────────────────────────────────────
# 5. render_backtest_summary_table
# ─────────────────────────────────────────────────────────────────────────────

def render_backtest_summary_table(regime_breakdown: list[dict]) -> None:
    """
    Render per-regime backtest statistics as a styled st.dataframe.

    Columns:
        Regime, Trades, Win Rate, Avg P&L%, Avg R-Multiple, Max Drawdown

    Colour coding:
        Regime — Bull = green bg, Bear = red bg, Sideways = grey bg.

    Parameters
    ──────────
    regime_breakdown : List of per-regime stat dicts.  Expected keys:
                       regime, trades, win_rate, avg_pnl_pct,
                       avg_r_multiple, max_drawdown.
                       All numeric fields are optional / nullable.

    Returns
    ───────
    None — renders directly via st.dataframe.
    """
    import streamlit as st

    if not regime_breakdown:
        st.info("No backtest regime data to display.")
        return

    rows = []
    for r in regime_breakdown:
        rows.append({
            "Regime":         str(r.get("regime") or "—"),
            "Trades":         r.get("trades"),
            "Win Rate":       r.get("win_rate"),
            "Avg P&L%":       r.get("avg_pnl_pct"),
            "Avg R-Multiple": r.get("avg_r_multiple"),
            "Max Drawdown":   r.get("max_drawdown"),
        })

    df = pd.DataFrame(rows)

    styler = (
        df.style
        .applymap(_regime_bg, subset=["Regime"])
        .format(
            {
                "Trades":         lambda v: f"{int(v)}" if v is not None else "—",
                "Win Rate":       lambda v: f"{v:.1f}%" if v is not None else "—",
                "Avg P&L%":       lambda v: f"{v:+.2f}%" if v is not None else "—",
                "Avg R-Multiple": lambda v: f"{v:.2f}×"  if v is not None else "—",
                "Max Drawdown":   lambda v: f"{v:.1f}%"  if v is not None else "—",
            },
            na_rep="—",
        )
    )

    col_cfg = {
        "Win Rate": st.column_config.NumberColumn("Win Rate", format="%.1f%%"),
        "Avg R-Multiple": st.column_config.NumberColumn(
            "Avg R-Multiple", format="%.2f×"
        ),
    }

    st.dataframe(
        styler,
        column_config=col_cfg,
        use_container_width=True,
        hide_index=True,
    )
