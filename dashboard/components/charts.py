"""
dashboard/components/charts.py
───────────────────────────────
Shared chart-rendering component for the Minervini AI Streamlit dashboard.

HEADLESS AGG REQUIREMENT
────────────────────────
This module calls ``matplotlib.use("Agg")`` at the very top — before any other
matplotlib or mplfinance import.  This is mandatory for headless servers (no
display, no X11/Wayland) such as ShreeVault running Streamlit.  If the backend
is NOT set before mplfinance is imported, matplotlib tries to initialise a GUI
toolkit (TkAgg, Qt5Agg, etc.), which fails with a RuntimeError on any headless
environment.  The ``Agg`` backend renders to in-memory PNG buffers only —
perfect for Streamlit's ``st.pyplot(fig)`` call.

Public API
──────────
    render_candlestick_chart(symbol, df, sepa_result, lookback_days) → Figure | None
    render_cached_chart(symbol, date_str)                             → bytes | None
    render_equity_curve(trades_df)                                    → Figure | None
    render_backtest_equity_curve(backtest_df)                         → Figure | None

Usage inside a Streamlit page
─────────────────────────────
    from dashboard.components.charts import (
        render_candlestick_chart,
        render_cached_chart,
        render_equity_curve,
        render_backtest_equity_curve,
    )

    fig = render_candlestick_chart(symbol, df, sepa_result=result)
    if fig:
        st.pyplot(fig)

    img_bytes = render_cached_chart("DIXON", "2026-04-11")
    if img_bytes:
        st.image(img_bytes)
    else:
        fig = render_candlestick_chart("DIXON", df)
        if fig:
            st.pyplot(fig)
"""

from __future__ import annotations

# ── CRITICAL: set headless backend BEFORE any other matplotlib/mplfinance import ──
import matplotlib
matplotlib.use("Agg")                        # noqa: E402  (must stay here)

import logging
from pathlib import Path
from typing import Optional

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import pandas as pd

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

#: Directory where the pipeline writes pre-generated chart PNGs.
_CHART_DIR = Path("data/charts")

#: Dark background colour shared by all charts.
_BG_COLOUR = "#1a1a2e"
_GRID_COLOUR = "#2a2a4a"

#: MA ribbon — (column_name, colour).  Ordered slow → fast so faster MAs
#: draw on top when lines overlap.
_MA_RIBBON: list[tuple[str, str]] = [
    ("SMA_200", "#ef5350"),   # red   — slowest / trend anchor
    ("SMA_150", "#ff9800"),   # orange
    ("SMA_50",  "#42a5f5"),   # blue
    ("SMA_21",  "#26c6da"),   # cyan
    ("SMA_10",  "#a5d6a7"),   # light green — fastest
]

#: Stage label text and colour (by stage int).
_STAGE_LABELS: dict[int, str] = {
    1: "Stage 1 — Basing",
    2: "Stage 2 — Advancing",
    3: "Stage 3 — Topping",
    4: "Stage 4 — Declining",
}
_STAGE_COLOURS: dict[int, str] = {
    1: "grey",
    2: "#00e676",
    3: "#ff9800",
    4: "#ef5350",
}

#: Quality badge colours.
_QUALITY_COLOURS: dict[str, str] = {
    "A+":   "#ffd700",
    "A":    "#00e676",
    "B":    "#40c4ff",
    "C":    "#ff9800",
    "FAIL": "#ef5350",
}

#: Regime background colours for backtest equity curve.
_REGIME_COLOURS: dict[str, Optional[str]] = {
    "Bull":     "#26a69a",   # teal-green
    "Bear":     "#ef5350",   # red
    "Sideways": None,        # transparent
}


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get(result: object, key: str, default=None):
    """Read an attribute from a SEPAResult dataclass or a plain dict."""
    if result is None:
        return default
    if isinstance(result, dict):
        return result.get(key, default)
    return getattr(result, key, default)


def _apply_dark_style(ax: "plt.Axes") -> None:
    """Apply the shared dark-mode styling to a matplotlib Axes."""
    ax.set_facecolor(_BG_COLOUR)
    ax.tick_params(colors="white", labelsize=8)
    ax.spines["bottom"].set_color(_GRID_COLOUR)
    ax.spines["top"].set_color(_GRID_COLOUR)
    ax.spines["left"].set_color(_GRID_COLOUR)
    ax.spines["right"].set_color(_GRID_COLOUR)
    ax.xaxis.label.set_color("white")
    ax.yaxis.label.set_color("white")
    ax.title.set_color("white")
    ax.grid(True, color=_GRID_COLOUR, linestyle="--", linewidth=0.5, alpha=0.6)


def _prep_ohlcv(df: pd.DataFrame, lookback_days: int) -> pd.DataFrame:
    """
    Normalise OHLCV column names to title-case and trim to *lookback_days*.

    Returns a copy ready for mplfinance (columns: Open, High, Low, Close, Volume).
    """
    df = df.copy().tail(lookback_days)
    col_map = {c: c.title() for c in df.columns
               if c.lower() in ("open", "high", "low", "close", "volume")}
    df.rename(columns=col_map, inplace=True)
    return df


def _dark_mpf_style():
    """Return a mplfinance style object for dark-mode charts."""
    try:
        import mplfinance as mpf
        return mpf.make_mpf_style(
            base_mpf_style="nightclouds",
            marketcolors=mpf.make_marketcolors(
                up="#26a69a", down="#ef5350",
                edge="inherit", wick="inherit", volume="inherit",
            ),
            facecolor=_BG_COLOUR,
            figcolor=_BG_COLOUR,
            gridcolor=_GRID_COLOUR,
            gridstyle="--",
        )
    except Exception:
        return "mike"


# ─────────────────────────────────────────────────────────────────────────────
# 1.  render_candlestick_chart
# ─────────────────────────────────────────────────────────────────────────────

def render_candlestick_chart(
    symbol: str,
    df: pd.DataFrame,
    sepa_result: Optional[object] = None,
    lookback_days: int = 90,
    title: Optional[str] = None,
) -> Optional["plt.Figure"]:
    """
    Render a live candlestick chart with MA ribbon and SEPA overlays.

    The caller is responsible for displaying the returned figure, e.g.::

        fig = render_candlestick_chart("DIXON", df, sepa_result=result)
        if fig:
            st.pyplot(fig)

    Parameters
    ──────────
    symbol        : NSE symbol string used in the title.
    df            : OHLCV DataFrame with a DatetimeIndex.  Column names may be
                    lower-case or title-case; they are normalised internally.
                    MA columns (SMA_10, SMA_21, SMA_50, SMA_150, SMA_200) may
                    also be present in the same DataFrame.
    sepa_result   : SEPAResult dataclass or equivalent dict.  When None, no
                    overlays are drawn.
    lookback_days : Number of trading days to display (default 90).
    title         : Optional chart title override.  Defaults to
                    ``"{symbol} — {lookback_days}d"``.

    Returns
    ───────
    matplotlib.figure.Figure or None on any failure.
    """
    try:
        return _render_candlestick_impl(symbol, df, sepa_result, lookback_days, title)
    except Exception as exc:
        log.warning("render_candlestick_chart failed: %s — %s", symbol, exc)
        return None


def _render_candlestick_impl(
    symbol: str,
    df: pd.DataFrame,
    sepa_result: Optional[object],
    lookback_days: int,
    title: Optional[str],
) -> Optional["plt.Figure"]:
    """Core implementation — called inside a try/except in the public wrapper."""
    import mplfinance as mpf

    if df is None or len(df) < 10:
        log.warning("render_candlestick_chart: insufficient data for %s", symbol)
        return None

    ohlcv = _prep_ohlcv(df, lookback_days)
    required = {"Open", "High", "Low", "Close", "Volume"}
    if not required.issubset(ohlcv.columns):
        missing = required - set(ohlcv.columns)
        log.warning("render_candlestick_chart: missing columns %s for %s", missing, symbol)
        return None

    n = len(ohlcv)

    # ── MA ribbon add-plots ───────────────────────────────────────────────────
    addplots = []
    for ma_col, colour in _MA_RIBBON:
        if ma_col in ohlcv.columns:
            series = ohlcv[ma_col]
            if not series.isna().all():
                addplots.append(
                    mpf.make_addplot(series, panel=0, color=colour,
                                     width=1.1, label=ma_col, secondary_y=False)
                )

    # ── Horizontal lines (entry / stop) ──────────────────────────────────────
    entry_price: Optional[float] = _get(sepa_result, "entry_price")
    stop_loss:   Optional[float] = _get(sepa_result, "stop_loss")

    hlines_prices:  list[float] = []
    hlines_colours: list[str]   = []
    hlines_styles:  list[str]   = []
    hlines_widths:  list[float] = []

    if entry_price is not None:
        hlines_prices.append(float(entry_price))
        hlines_colours.append("#00e676")
        hlines_styles.append("--")
        hlines_widths.append(1.2)
    if stop_loss is not None:
        hlines_prices.append(float(stop_loss))
        hlines_colours.append("#ef5350")
        hlines_styles.append("--")
        hlines_widths.append(1.2)

    hlines_kwargs: dict = {}
    if hlines_prices:
        hlines_kwargs = dict(
            hlines=hlines_prices,
            hline_panel=0,
            hlinecolors=hlines_colours,
            hlinestyles=hlines_styles,
            hlinewidths=hlines_widths,
        )


    # ── Volume bar colours ────────────────────────────────────────────────────
    vol_colours = [
        "#26a69a" if ohlcv["Close"].iloc[i] >= ohlcv["Open"].iloc[i] else "#ef5350"
        for i in range(n)
    ]

    # ── mplfinance plot ───────────────────────────────────────────────────────
    chart_title = title or f"{symbol} — {lookback_days}d"
    style = _dark_mpf_style()

    fig, axes = mpf.plot(
        ohlcv[["Open", "High", "Low", "Close", "Volume"]],
        type="candle",
        style=style,
        title=chart_title,
        figsize=(14, 8),
        volume=True,
        volume_panel=1,
        panel_ratios=(3, 1),
        addplot=addplots if addplots else None,
        vcolors=vol_colours,
        warn_too_much_data=9999,
        returnfig=True,
        **hlines_kwargs,
    )

    main_ax = axes[0]

    # ── VCP base zone (gold shaded rectangle) ─────────────────────────────────
    if _get(sepa_result, "vcp_qualified", False):
        last_idx  = n - 1
        last_high = float(ohlcv["High"].iloc[last_idx])

        vcp_details = _get(sepa_result, "vcp_details", {}) or {}
        base_weeks  = vcp_details.get("base_weeks", 0) or 0
        base_bars   = max(2, min(int(base_weeks * 5) if base_weeks > 0 else 10, last_idx))

        base_start_x    = last_idx - base_bars
        base_window_low = float(ohlcv["Low"].iloc[base_start_x: last_idx + 1].min())
        rect_top        = float(entry_price) if entry_price is not None else last_high
        rect_height     = rect_top - base_window_low

        if rect_height > 0:
            # Filled rectangle (alpha=0.08 — very subtle gold wash)
            main_ax.add_patch(mpatches.Rectangle(
                xy=(base_start_x - 0.5, base_window_low),
                width=base_bars + 1,
                height=rect_height,
                linewidth=0,
                facecolor="#ffd700",
                alpha=0.08,
                zorder=0,
            ))
            # Dashed gold border (higher alpha so it's visible)
            main_ax.add_patch(mpatches.Rectangle(
                xy=(base_start_x - 0.5, base_window_low),
                width=base_bars + 1,
                height=rect_height,
                linewidth=1.0,
                edgecolor="#ffd700",
                facecolor="none",
                linestyle="--",
                alpha=0.6,
                zorder=1,
            ))

    # ── Stage label (top-left) ────────────────────────────────────────────────
    stage       = int(_get(sepa_result, "stage", 0) or 0)
    stage_label = _STAGE_LABELS.get(stage, f"Stage {stage}" if stage else "")
    if stage_label:
        s_colour = _STAGE_COLOURS.get(stage, "grey")
        main_ax.text(
            0.01, 0.97, stage_label,
            transform=main_ax.transAxes,
            fontsize=10, fontweight="bold", color=s_colour,
            va="top", ha="left",
            bbox=dict(boxstyle="round,pad=0.3", facecolor=_BG_COLOUR,
                      edgecolor=s_colour, alpha=0.85),
        )

    # ── Setup-quality badge (top-right) ──────────────────────────────────────
    quality = str(_get(sepa_result, "setup_quality", "") or "")
    if quality:
        q_colour = _QUALITY_COLOURS.get(quality, "white")
        main_ax.text(
            0.99, 0.97, quality,
            transform=main_ax.transAxes,
            fontsize=13, fontweight="bold", color=q_colour,
            va="top", ha="right",
            bbox=dict(boxstyle="round,pad=0.4", facecolor=_BG_COLOUR,
                      edgecolor=q_colour, alpha=0.85),
        )

    # ── MA ribbon legend ──────────────────────────────────────────────────────
    if addplots:
        main_ax.legend(
            loc="lower left", fontsize=7,
            facecolor=_BG_COLOUR, edgecolor="#444",
            labelcolor="white", framealpha=0.7,
        )

    fig.patch.set_facecolor(_BG_COLOUR)
    plt.tight_layout()
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# 2.  render_cached_chart
# ─────────────────────────────────────────────────────────────────────────────

def render_cached_chart(symbol: str, date_str: str) -> Optional[bytes]:
    """
    Return the raw PNG bytes for a pre-generated chart, or None if not found.

    The pipeline writes charts to ``data/charts/{symbol}_{date_str}.png``
    via ``reports/chart_generator.py``.  This function lets the dashboard
    serve those cached images instantly without re-rendering.

    Parameters
    ──────────
    symbol   : NSE symbol string (e.g. ``"DIXON"``).
    date_str : Date string matching the filename, e.g. ``"2026-04-11"``.

    Returns
    ───────
    Raw PNG bytes (``bytes``) when the file exists, ``None`` otherwise.
    The Streamlit caller should do::

        img_bytes = render_cached_chart("DIXON", "2026-04-11")
        if img_bytes:
            st.image(img_bytes)
        else:
            fig = render_candlestick_chart(symbol, df, sepa_result=result)
            if fig:
                st.pyplot(fig)
    """
    png_path = _CHART_DIR / f"{symbol}_{date_str}.png"
    if png_path.exists():
        try:
            return png_path.read_bytes()
        except Exception as exc:
            log.warning("render_cached_chart: could not read %s — %s", png_path, exc)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 3.  render_equity_curve
# ─────────────────────────────────────────────────────────────────────────────

def render_equity_curve(trades_df: pd.DataFrame) -> Optional["plt.Figure"]:
    """
    Plot a cumulative P&L equity curve for closed paper trades.

    Parameters
    ──────────
    trades_df : DataFrame of closed paper trades.  Expected columns:

        * ``exit_date``       — date of trade exit (used as x-axis).
        * ``cumulative_pnl``  — running P&L (preferred).
        * ``pnl``             — per-trade P&L (used to compute cumulative_pnl
                                when ``cumulative_pnl`` column is absent).

    Returns
    ───────
    matplotlib.figure.Figure or None on failure / empty data.

    The line is coloured **green** above zero and **red** below zero.
    A dashed grey zero line is always drawn.  The caller does::

        fig = render_equity_curve(trades_df)
        if fig:
            st.pyplot(fig)
    """
    try:
        return _render_equity_impl(trades_df)
    except Exception as exc:
        log.warning("render_equity_curve failed: %s", exc)
        return None


def _render_equity_impl(trades_df: pd.DataFrame) -> Optional["plt.Figure"]:
    if trades_df is None or trades_df.empty:
        log.warning("render_equity_curve: empty trades_df")
        return None

    df = trades_df.copy()

    # ── Ensure cumulative_pnl column ─────────────────────────────────────────
    if "cumulative_pnl" not in df.columns:
        if "pnl" not in df.columns:
            log.warning("render_equity_curve: neither cumulative_pnl nor pnl column found")
            return None
        df = df.sort_values("exit_date")
        df["cumulative_pnl"] = df["pnl"].cumsum()

    df = df.sort_values("exit_date").reset_index(drop=True)
    x   = pd.to_datetime(df["exit_date"])
    y   = df["cumulative_pnl"].astype(float)

    fig, ax = plt.subplots(figsize=(12, 5))
    fig.patch.set_facecolor(_BG_COLOUR)
    _apply_dark_style(ax)

    # ── Zero reference line ───────────────────────────────────────────────────
    ax.axhline(0, color="#888888", linestyle="--", linewidth=1.0, alpha=0.7, zorder=1)

    # ── Colour segments: green above zero, red below zero ────────────────────
    ax.fill_between(x, y, 0, where=(y >= 0), interpolate=True,
                    color="#26a69a", alpha=0.25, zorder=2)
    ax.fill_between(x, y, 0, where=(y < 0),  interpolate=True,
                    color="#ef5350", alpha=0.25, zorder=2)

    # Main line — colour by final sign (simpler than per-segment; fills handle shading)
    line_colour = "#26a69a" if float(y.iloc[-1]) >= 0 else "#ef5350"
    ax.plot(x, y, color=line_colour, linewidth=1.6, zorder=3)

    ax.set_title("Paper Trading — Cumulative P&L", color="white", fontsize=12, pad=10)
    ax.set_xlabel("Exit Date", color="white")
    ax.set_ylabel("Cumulative P&L (₹)", color="white")
    fig.autofmt_xdate(rotation=30)
    plt.tight_layout()
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# 4.  render_backtest_equity_curve
# ─────────────────────────────────────────────────────────────────────────────

def render_backtest_equity_curve(backtest_df: pd.DataFrame) -> Optional["plt.Figure"]:
    """
    Plot a backtest portfolio-value curve with market-regime shading.

    Parameters
    ──────────
    backtest_df : DataFrame produced by ``backtest/report.py``.  Expected columns:

        * ``date``            — row date (DatetimeIndex or column).
        * ``portfolio_value`` — portfolio value at each date.
        * ``regime``          — market regime label: ``"Bull"``, ``"Bear"``,
                                or ``"Sideways"`` (produced by
                                ``backtest/regime.py``).

    Regime background shading
    ─────────────────────────
    * **Bull**     → light green fill (alpha 0.05)
    * **Bear**     → light red fill   (alpha 0.05)
    * **Sideways** → transparent (no fill)

    Returns
    ───────
    matplotlib.figure.Figure or None on failure / empty data.  The caller does::

        fig = render_backtest_equity_curve(backtest_df)
        if fig:
            st.pyplot(fig)
    """
    try:
        return _render_backtest_impl(backtest_df)
    except Exception as exc:
        log.warning("render_backtest_equity_curve failed: %s", exc)
        return None


def _render_backtest_impl(backtest_df: pd.DataFrame) -> Optional["plt.Figure"]:
    if backtest_df is None or backtest_df.empty:
        log.warning("render_backtest_equity_curve: empty backtest_df")
        return None

    df = backtest_df.copy()

    # ── Normalise date column / index ─────────────────────────────────────────
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        x = df["date"]
    else:
        df.index = pd.to_datetime(df.index)
        df = df.sort_index()
        x = df.index

    if "portfolio_value" not in df.columns:
        log.warning("render_backtest_equity_curve: 'portfolio_value' column missing")
        return None

    y = df["portfolio_value"].astype(float)

    fig, ax = plt.subplots(figsize=(14, 6))
    fig.patch.set_facecolor(_BG_COLOUR)
    _apply_dark_style(ax)

    # ── Regime background shading ─────────────────────────────────────────────
    if "regime" in df.columns:
        _draw_regime_bands(ax, x, y, df["regime"])

    # ── Zero / baseline reference (initial capital) ───────────────────────────
    initial_val = float(y.iloc[0]) if len(y) > 0 else 0.0
    ax.axhline(initial_val, color="#888888", linestyle="--",
               linewidth=0.8, alpha=0.7, zorder=2, label="Starting capital")

    # ── Equity curve line ─────────────────────────────────────────────────────
    final_colour = "#26a69a" if float(y.iloc[-1]) >= initial_val else "#ef5350"
    ax.plot(x, y, color=final_colour, linewidth=1.8, zorder=4, label="Portfolio value")

    # ── Fill above / below starting capital ───────────────────────────────────
    ax.fill_between(x, y, initial_val, where=(y >= initial_val), interpolate=True,
                    color="#26a69a", alpha=0.18, zorder=3)
    ax.fill_between(x, y, initial_val, where=(y < initial_val), interpolate=True,
                    color="#ef5350", alpha=0.18, zorder=3)

    ax.set_title("Backtest — Portfolio Equity Curve", color="white", fontsize=12, pad=10)
    ax.set_xlabel("Date", color="white")
    ax.set_ylabel("Portfolio Value (₹)", color="white")

    # ── Legend (equity + regime patches) ─────────────────────────────────────
    legend_handles = [
        ax.get_lines()[0],
        mpatches.Patch(color="#26a69a", alpha=0.35, label="Bull regime"),
        mpatches.Patch(color="#ef5350", alpha=0.35, label="Bear regime"),
    ]
    ax.legend(handles=legend_handles, loc="upper left",
              facecolor=_BG_COLOUR, edgecolor="#444",
              labelcolor="white", fontsize=8, framealpha=0.8)

    fig.autofmt_xdate(rotation=30)
    plt.tight_layout()
    return fig


def _draw_regime_bands(
    ax: "plt.Axes",
    x: "pd.Series",
    y: "pd.Series",
    regime: "pd.Series",
) -> None:
    """
    Shade contiguous regime regions behind the equity curve.

    Iterates through consecutive date ranges where the regime label is constant
    and fills with the appropriate background colour.
    """
    dates = pd.to_datetime(x).reset_index(drop=True)
    regimes = regime.reset_index(drop=True)
    n = len(dates)
    if n == 0:
        return

    i = 0
    while i < n:
        current_regime = str(regimes.iloc[i])
        colour = _REGIME_COLOURS.get(current_regime)
        j = i
        while j < n and str(regimes.iloc[j]) == current_regime:
            j += 1
        if colour is not None:
            ax.axvspan(dates.iloc[i], dates.iloc[j - 1],
                       alpha=0.05, color=colour, zorder=0)
        i = j

