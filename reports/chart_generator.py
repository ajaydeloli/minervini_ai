"""
reports/chart_generator.py
──────────────────────────
Candlestick chart generator for the Minervini AI SEPA screener.

Generates a single dark-background candlestick chart (mplfinance) with:
  • MA ribbon  — SMA_50 (blue), SMA_150 (orange), SMA_200 (red)
  • Entry price dashed green line and stop-loss dashed red line (when set)
  • Stage annotation in the top-left corner (colour-coded by stage number)
  • Setup-quality badge in the top-right corner (A+/A/B/C in gold/green, etc.)
  • Volume panel with green/red bars

Public API
──────────
    generate_chart(symbol, run_date, result, config, output_dir) -> Path | None

The function NEVER raises — any failure is logged as a warning and None is
returned so chart failures cannot crash the daily pipeline.

Usage example
─────────────
    import datetime
    from config.loader import load_config
    from reports.chart_generator import generate_chart

    config = load_config("config/settings.yaml")
    result = ...  # SEPAResult from rules.scorer.evaluate()

    path = generate_chart(
        symbol="DIXON",
        run_date=datetime.date.today(),
        result=result,
        config=config,
        output_dir="data/charts",
    )
    if path:
        print(f"Chart saved → {path}")
    else:
        print("Chart generation skipped (insufficient data or missing file)")
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Union

from utils.logger import get_logger

log = get_logger(__name__)

# ── Stage annotation helpers ──────────────────────────────────────────────────

_STAGE_LABELS: dict[int, str] = {
    1: "Stage 1 — Basing",
    2: "Stage 2 — Advancing",
    3: "Stage 3 — Topping",
    4: "Stage 4 — Declining",
}

_STAGE_COLOURS: dict[int, str] = {
    1: "grey",
    2: "#00e676",   # bright green
    3: "orange",
    4: "#ef5350",   # red
}

# ── Setup-quality badge colours ───────────────────────────────────────────────

_QUALITY_COLOURS: dict[str, str] = {
    "A+":   "#ffd700",   # gold
    "A":    "#00e676",   # green
    "B":    "#40c4ff",   # light blue
    "C":    "orange",
    "FAIL": "#ef5350",   # red
}


# ── MA column metadata ────────────────────────────────────────────────────────

_MA_COLS: list[tuple[str, str]] = [
    ("SMA_50",  "blue"),
    ("SMA_150", "orange"),
    ("SMA_200", "red"),
]


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_attr(result: object, key: str, default=None):
    """Safely read from a SEPAResult dataclass OR a plain dict."""
    if isinstance(result, dict):
        return result.get(key, default)
    return getattr(result, key, default)


def _resolve_output_dir(config: dict, output_dir: str) -> Path:
    """Merge config['chart']['output_dir'] with the caller's argument.

    Priority (highest → lowest):
      1. Explicit caller override  — any value other than the default ``"data/charts"``
      2. ``config["chart"]["output_dir"]``
      3. Hard-coded default        — ``"data/charts"``
    """
    _DEFAULT = "data/charts"
    chart_cfg = config.get("chart", {})
    cfg_dir = chart_cfg.get("output_dir", _DEFAULT)
    # Caller wins only when they explicitly passed a non-default directory.
    resolved = Path(output_dir if output_dir != _DEFAULT else cfg_dir)
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _lookback(config: dict) -> int:
    """Return number of candles to plot (default 90)."""
    return int(config.get("chart", {}).get("lookback_days", 90))


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def generate_chart(
    symbol: str,
    run_date: datetime.date,
    result: Union[object, dict],
    config: dict,
    output_dir: str = "data/charts",
) -> "Path | None":
    """
    Generate a candlestick chart with MA ribbon, VCP markup, and stage
    annotation for *symbol* and write it to *output_dir*.

    Parameters
    ──────────
    symbol      : NSE symbol string (e.g. "DIXON").
    run_date    : The screening date (used in the title and file name).
    result      : SEPAResult dataclass or equivalent dict produced by
                  rules.scorer.evaluate().  Reads: stage, entry_price,
                  stop_loss, vcp_qualified, setup_quality.
    config      : Full application config dict (from config/settings.yaml).
    output_dir  : Directory for output PNGs.  Created if absent.
                  Overridden by config["chart"]["output_dir"] when the
                  caller leaves the default value.

    Returns
    ───────
    Path to the saved PNG, or None when:
      • The processed or features Parquet file does not exist.
      • The OHLCV data has fewer than 10 rows after loading.
      • Any unexpected exception occurs during rendering.
    """
    try:
        return _generate_chart_impl(symbol, run_date, result, config, output_dir)
    except Exception as exc:
        log.warning(
            "Chart generation failed — skipping",
            symbol=symbol,
            run_date=str(run_date),
            error=str(exc),
        )
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Implementation (called only from the guarded public wrapper)
# ─────────────────────────────────────────────────────────────────────────────

def _generate_chart_impl(
    symbol: str,
    run_date: datetime.date,
    result: Union[object, dict],
    config: dict,
    output_dir: str,
) -> "Path | None":
    """Core implementation — may raise; always called inside try/except."""
    # Set headless backend FIRST — before any matplotlib or mplfinance import.
    # Importing mplfinance triggers matplotlib initialisation; if the backend
    # is not set beforehand the default (TkAgg / Qt) will be attempted, which
    # fails on headless servers and causes a RuntimeError.
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.patches as mpatches

    import mplfinance as mpf

    from storage import parquet_store

    data_cfg = config.get("data", {})
    processed_dir = data_cfg.get("processed_dir", "data/processed")
    features_dir  = data_cfg.get("features_dir",  "data/features")
    n              = _lookback(config)

    # ── Load OHLCV (processed parquet) ───────────────────────────────────────
    ohlcv_path = Path(processed_dir) / f"{symbol}.parquet"
    if not ohlcv_path.exists():
        log.warning("Processed parquet not found — skipping chart",
                    symbol=symbol, path=str(ohlcv_path))
        return None

    ohlcv = parquet_store.read(ohlcv_path).tail(n).copy()

    if len(ohlcv) < 10:
        log.warning("Insufficient OHLCV rows — skipping chart",
                    symbol=symbol, rows=len(ohlcv))
        return None

    # mplfinance requires columns Open/High/Low/Close/Volume (title-case)
    col_map = {c: c.title() for c in ohlcv.columns if c.lower() in
               ("open", "high", "low", "close", "volume")}
    ohlcv.rename(columns=col_map, inplace=True)
    required = {"Open", "High", "Low", "Close", "Volume"}
    missing_ohlcv = required - set(ohlcv.columns)
    if missing_ohlcv:
        log.warning("OHLCV missing required columns — skipping chart",
                    symbol=symbol, missing=list(missing_ohlcv))
        return None

    # ── Load MA lines from features parquet ──────────────────────────────────
    feat_path = Path(features_dir) / f"{symbol}.parquet"
    feat_df = None
    if feat_path.exists():
        try:
            feat_df = parquet_store.read(feat_path).tail(n).copy()
        except Exception as exc:
            log.warning("Could not load features parquet — MAs will be skipped",
                        symbol=symbol, error=str(exc))

    # Align features to OHLCV index
    addplots = []
    if feat_df is not None:
        feat_df = feat_df.reindex(ohlcv.index)
        for ma_col, colour in _MA_COLS:
            if ma_col not in feat_df.columns:
                log.warning("MA column missing in features — skipping line",
                            symbol=symbol, column=ma_col)
                continue
            series = feat_df[ma_col]
            if series.isna().all():
                log.warning("MA column is all-NaN — skipping line",
                            symbol=symbol, column=ma_col)
                continue
            addplots.append(
                mpf.make_addplot(series, panel=0, color=colour,
                                 width=1.2, label=ma_col)
            )

    # ── Volume colours (green = up day, red = down day) ──────────────────────
    vol_colours = [
        "#26a69a" if ohlcv["Close"].iloc[i] >= ohlcv["Open"].iloc[i] else "#ef5350"
        for i in range(len(ohlcv))
    ]

    # ── Entry / stop horizontal lines ────────────────────────────────────────
    entry_price = _get_attr(result, "entry_price")
    stop_loss   = _get_attr(result, "stop_loss")

    hlines_prices: list[float] = []
    hlines_colours: list[str]  = []
    hlines_styles: list[str]   = []
    hlines_widths: list[float] = []

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

    # ── Build hlines kwarg ────────────────────────────────────────────────────
    hlines_kwargs: dict = {}
    if hlines_prices:
        hlines_kwargs = dict(
            hlines=hlines_prices,
            hline_panel=0,
            hlinecolors=hlines_colours,
            hlinestyles=hlines_styles,
            hlinewidths=hlines_widths,
        )

    # ── Chart style & market colours ──────────────────────────────────────────
    try:
        style = mpf.make_mpf_style(
            base_mpf_style="nightclouds",
            marketcolors=mpf.make_marketcolors(
                up="#26a69a", down="#ef5350",
                edge="inherit", wick="inherit", volume="inherit",
            ),
            facecolor="#1a1a2e",
            figcolor="#1a1a2e",
            gridcolor="#2a2a4a",
            gridstyle="--",
        )
    except Exception:
        # Fallback to a built-in dark style if nightclouds is unavailable
        style = "mike"

    # ── mplfinance plot ───────────────────────────────────────────────────────
    out_dir  = _resolve_output_dir(config, output_dir)
    out_file = out_dir / f"{symbol}_{run_date}.png"

    fig, axes = mpf.plot(
        ohlcv,
        type="candle",
        style=style,
        title=f"{symbol} — {run_date}",
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

    main_ax   = axes[0]
    volume_ax = axes[2] if len(axes) > 2 else axes[-1]

    # ── Stage annotation (top-left of main panel) ─────────────────────────────
    stage         = _get_attr(result, "stage", 0)
    stage_label   = _STAGE_LABELS.get(int(stage), f"Stage {stage}")
    stage_colour  = _STAGE_COLOURS.get(int(stage), "grey")

    main_ax.text(
        0.01, 0.97, stage_label,
        transform=main_ax.transAxes,
        fontsize=11, fontweight="bold",
        color=stage_colour,
        va="top", ha="left",
        bbox=dict(boxstyle="round,pad=0.3", facecolor="#1a1a2e",
                  edgecolor=stage_colour, alpha=0.85),
    )

    # ── Setup-quality badge (top-right of main panel) ─────────────────────────
    setup_quality = str(_get_attr(result, "setup_quality", ""))
    if setup_quality:
        badge_colour = _QUALITY_COLOURS.get(setup_quality, "white")
        main_ax.text(
            0.99, 0.97, setup_quality,
            transform=main_ax.transAxes,
            fontsize=13, fontweight="bold",
            color=badge_colour,
            va="top", ha="right",
            bbox=dict(boxstyle="round,pad=0.4", facecolor="#1a1a2e",
                      edgecolor=badge_colour, alpha=0.85),
        )

    # ── VCP base zone rectangle + star marker ────────────────────────────────
    if _get_attr(result, "vcp_qualified", False):
        last_idx  = len(ohlcv) - 1
        last_high = float(ohlcv["High"].iloc[last_idx])

        # ── Star annotation on the last bar ──────────────────────────────────
        main_ax.annotate(
            "★ VCP",
            xy=(last_idx, last_high),
            xytext=(last_idx, last_high * 1.015),
            fontsize=8, color="#ffd700",
            ha="center", va="bottom",
        )

        # ── Base zone shaded rectangle ────────────────────────────────────────
        # Derive base width from vcp_details["base_weeks"] when available,
        # falling back to the feature row via feat_df, then to 10 bars.
        # Convert weeks → bars using 5 trading days/week.
        vcp_details = _get_attr(result, "vcp_details", {}) or {}
        base_weeks  = vcp_details.get("base_weeks", 0)

        if base_weeks and base_weeks > 0:
            base_bars = int(base_weeks * 5)
        elif feat_df is not None and "vcp_base_weeks" in feat_df.columns:
            _bw = feat_df["vcp_base_weeks"].dropna()
            base_bars = int(_bw.iloc[-1] * 5) if not _bw.empty else 10
        else:
            base_bars = 10   # sensible visual fallback

        # Clamp so the rectangle never extends beyond the visible chart window
        base_bars = max(2, min(base_bars, last_idx))

        base_start_x = last_idx - base_bars
        base_end_x   = last_idx

        # Y bounds: low of the base window → entry_price (pivot breakout high).
        # Use stop_loss as the lower bound when entry_price is unavailable.
        base_window_low = float(ohlcv["Low"].iloc[base_start_x : base_end_x + 1].min())
        rect_top = entry_price if entry_price is not None else last_high

        rect_height = rect_top - base_window_low
        if rect_height > 0:
            vcp_rect = mpatches.Rectangle(
                xy=(base_start_x - 0.5, base_window_low),
                width=base_bars + 1,
                height=rect_height,
                linewidth=1.0,
                edgecolor="#ffd700",
                facecolor="#ffd700",
                linestyle="--",
                alpha=0.08,
                zorder=0,          # behind candles
            )
            main_ax.add_patch(vcp_rect)

            # Dashed gold border drawn as a separate patch at higher alpha
            # so the outline is clearly visible against the dark background.
            vcp_border = mpatches.Rectangle(
                xy=(base_start_x - 0.5, base_window_low),
                width=base_bars + 1,
                height=rect_height,
                linewidth=1.0,
                edgecolor="#ffd700",
                facecolor="none",
                linestyle="--",
                alpha=0.6,
                zorder=1,
            )
            main_ax.add_patch(vcp_border)

            log.debug(
                "VCP base zone drawn",
                symbol=symbol,
                base_start_x=base_start_x,
                base_bars=base_bars,
                base_window_low=round(base_window_low, 2),
                rect_top=round(rect_top, 2),
            )

    # ── Legend for MA lines ───────────────────────────────────────────────────
    if addplots:
        main_ax.legend(
            loc="lower left",
            fontsize=7,
            facecolor="#1a1a2e",
            edgecolor="#444",
            labelcolor="white",
            framealpha=0.7,
        )

    # ── Save and close ────────────────────────────────────────────────────────
    import matplotlib.pyplot as plt
    fig.savefig(out_file, dpi=120, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)

    log.info("Chart saved", symbol=symbol, run_date=str(run_date),
             path=str(out_file))
    return out_file
