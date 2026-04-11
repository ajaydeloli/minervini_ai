"""
backtest/regime.py
──────────────────
Market-regime labelling for the Minervini AI backtesting system.

Each calendar date is labelled as Bull / Bear / Sideways using one of
two strategies (in priority order):

  1. NSE Calendar  — hard-coded lookup table derived from the NSE/Nifty
     regime history documented in PROJECT_DESIGN.md.  Always consulted
     first.  Returns "Unknown" for dates outside the table's range or
     dates marked as "Unknown" (Apr 2025 – present).

  2. SMA-200 Slope Fallback  — used when the calendar cannot give a
     definitive answer.  Computes the slope of the 200-day SMA over the
     last 20 trading days from the supplied benchmark OHLCV DataFrame and
     classifies as Bull / Bear / Sideways based on the slope threshold.

Public API
──────────
    RegimeLabel  — Literal type alias
    RegimeResult — dataclass holding date, label, source, slope_pct
    label_date(trade_date, benchmark_df) → RegimeResult
    label_dates(dates, benchmark_df) → dict[date, RegimeResult]
    label_trades(trades, benchmark_df) → list[dict]
    compute_regime_breakdown(trades) → dict[str, dict]
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

import pandas as pd

from utils.logger import get_logger

log = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Types
# ─────────────────────────────────────────────────────────────────────────────

RegimeLabel = Literal["Bull", "Bear", "Sideways", "Unknown"]

# ─────────────────────────────────────────────────────────────────────────────
# NSE regime calendar
# Each entry: (start_date inclusive, end_date inclusive, label)
# Dates not covered → slope fallback.
# Label "Unknown" → slope fallback.
# ─────────────────────────────────────────────────────────────────────────────

_CALENDAR: list[tuple[date, date, RegimeLabel]] = [
    (date(2014,  5, 1), date(2018,  1, 31), "Bull"),
    (date(2018,  2, 1), date(2019,  3, 31), "Sideways"),
    (date(2019,  4, 1), date(2020,  1, 31), "Bull"),
    (date(2020,  2, 1), date(2020,  3, 31), "Bear"),
    (date(2020,  4, 1), date(2021, 12, 31), "Bull"),
    (date(2022,  1, 1), date(2022, 12, 31), "Sideways"),
    (date(2023,  1, 1), date(2024,  9, 30), "Bull"),
    (date(2024, 10, 1), date(2025,  3, 31), "Sideways"),
    (date(2025,  4, 1), date(9999, 12, 31), "Unknown"),   # slope fallback
]

# ─────────────────────────────────────────────────────────────────────────────
# SMA-200 slope thresholds  (% per day)
# ─────────────────────────────────────────────────────────────────────────────

_SLOPE_BULL_THRESHOLD: float = +0.05   # slope > +0.05 %/day → Bull
_SLOPE_BEAR_THRESHOLD: float = -0.05   # slope < -0.05 %/day → Bear
_SMA_WINDOW: int = 200
_SLOPE_LOOKBACK: int = 20              # days over which slope is measured


# ─────────────────────────────────────────────────────────────────────────────
# RegimeResult dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RegimeResult:
    """
    Regime classification for a single date.

    Fields
    ──────
    date       : The calendar date being labelled.
    label      : "Bull", "Bear", "Sideways", or "Unknown".
    source     : How the label was determined:
                   "calendar"       — NSE hard-coded lookup
                   "slope_fallback" — SMA-200 slope computation
                   "unknown"        — no benchmark_df and calendar said Unknown
    slope_pct  : SMA-200 slope used (% per day).  None when source="calendar".
    """

    date: date
    label: RegimeLabel
    source: Literal["calendar", "slope_fallback", "unknown"]
    slope_pct: float | None


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _calendar_lookup(trade_date: date) -> RegimeLabel:
    """
    Return the calendar regime label for *trade_date*, or "Unknown" if the
    date falls before May 2014 or inside a calendar "Unknown" band.
    """
    for start, end, label in _CALENDAR:
        if start <= trade_date <= end:
            return label
    # Date is before May 2014 → no calendar entry → Unknown (slope fallback)
    return "Unknown"


def _compute_slope(benchmark_df: pd.DataFrame, trade_date: date) -> float | None:
    """
    Compute the SMA-200 slope (% per day) for the benchmark up to *trade_date*.

    The benchmark DataFrame must have:
        - a DatetimeIndex (or any index that can be compared with pd.Timestamp)
        - a 'close' column

    Slope = (sma[-1] - sma[-20]) / sma[-20] / 20 * 100  (% per day)

    Returns None if fewer than 2 rows are available after filtering.
    Uses however many rows exist if < 200 (degrade gracefully).
    """
    if benchmark_df is None or benchmark_df.empty:
        return None

    ts = pd.Timestamp(trade_date)
    subset = benchmark_df[benchmark_df.index <= ts].copy()

    if len(subset) < 2:
        return None

    # SMA-200 (or fewer rows if not enough history)
    window = min(_SMA_WINDOW, len(subset))
    sma = subset["close"].rolling(window=window, min_periods=1).mean()

    # Need at least _SLOPE_LOOKBACK rows to compute slope
    if len(sma) < 2:
        return None

    lookback = min(_SLOPE_LOOKBACK, len(sma))
    sma_latest = float(sma.iloc[-1])
    sma_prev   = float(sma.iloc[-lookback])

    if sma_prev == 0.0:
        return None

    slope = (sma_latest - sma_prev) / sma_prev / lookback * 100.0
    return slope


def _label_from_slope(slope: float) -> RegimeLabel:
    if slope > _SLOPE_BULL_THRESHOLD:
        return "Bull"
    if slope < _SLOPE_BEAR_THRESHOLD:
        return "Bear"
    return "Sideways"


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def label_date(
    trade_date: date,
    benchmark_df: pd.DataFrame | None = None,
) -> RegimeResult:
    """
    Label a single date.

    Strategy:
      1. Consult the NSE calendar.
         - Definitive answer (Bull/Bear/Sideways) → return immediately.
         - "Unknown" → proceed to slope fallback.
      2. Slope fallback using *benchmark_df*.
         - Returns slope-based label if benchmark_df is available.
      3. If both strategies fail, return source="unknown", label="Unknown".

    Parameters
    ──────────
    trade_date   : The calendar date to classify.
    benchmark_df : Optional benchmark OHLCV DataFrame.
                   Must have a DatetimeIndex and a 'close' column.

    Returns
    ───────
    RegimeResult
    """
    cal_label = _calendar_lookup(trade_date)

    if cal_label != "Unknown":
        log.debug(
            "Regime from calendar",
            date=str(trade_date),
            label=cal_label,
        )
        return RegimeResult(
            date=trade_date,
            label=cal_label,
            source="calendar",
            slope_pct=None,
        )

    # Calendar says Unknown — try slope fallback
    if benchmark_df is not None and not benchmark_df.empty:
        slope = _compute_slope(benchmark_df, trade_date)
        if slope is not None:
            label = _label_from_slope(slope)
            log.debug(
                "Regime from slope fallback",
                date=str(trade_date),
                slope_pct=round(slope, 6),
                label=label,
            )
            return RegimeResult(
                date=trade_date,
                label=label,
                source="slope_fallback",
                slope_pct=slope,
            )

    log.warning(
        "Regime unknown — no calendar entry and no benchmark_df",
        date=str(trade_date),
    )
    return RegimeResult(
        date=trade_date,
        label="Unknown",
        source="unknown",
        slope_pct=None,
    )


def label_dates(
    dates: list[date],
    benchmark_df: pd.DataFrame | None = None,
) -> dict[date, RegimeResult]:
    """
    Batch version of label_date.

    Parameters
    ──────────
    dates        : List of calendar dates to classify.
    benchmark_df : Optional benchmark OHLCV DataFrame.

    Returns
    ───────
    dict mapping each date → RegimeResult.
    """
    results: dict[date, RegimeResult] = {}
    for d in dates:
        results[d] = label_date(d, benchmark_df)
    log.debug("label_dates complete", count=len(results))
    return results


def label_trades(
    trades: list[dict],
    benchmark_df: pd.DataFrame | None = None,
) -> list[dict]:
    """
    Populate the 'regime' key on each trade dict using its 'entry_date'.

    Modifies trade dicts in-place and also returns the same list.
    'regime' is set to the string label ("Bull", "Bear", "Sideways", "Unknown").

    Parameters
    ──────────
    trades       : List of trade dicts.  Each must contain 'entry_date'
                   (date | str).
    benchmark_df : Optional benchmark OHLCV DataFrame passed through to
                   label_date for slope fallback.

    Returns
    ───────
    The same list (with 'regime' populated in-place).
    """
    for trade in trades:
        entry = trade.get("entry_date")
        if isinstance(entry, str):
            entry = date.fromisoformat(entry)
        result = label_date(entry, benchmark_df)
        trade["regime"] = result.label
    log.debug("label_trades complete", count=len(trades))
    return trades


def compute_regime_breakdown(
    trades: list[dict],
) -> dict[str, dict]:
    """
    Group closed trades by regime label and compute per-regime statistics.

    Each trade dict must contain:
        regime   (str)    — e.g. "Bull", "Bear", "Sideways"
        pnl      (float)  — realised P&L in currency units
        pnl_pct  (float)  — realised P&L as % of entry cost

    Returns
    ───────
    {
        "Bull":     {"trades": N, "wins": N, "win_rate": X, "avg_pnl_pct": Y, "total_pnl": Z},
        "Bear":     {...},
        "Sideways": {...},
    }
    Only regimes that have at least one trade are included.
    """
    if not trades:
        return {}

    # Accumulate per-regime buckets
    buckets: dict[str, list[dict]] = {}
    for trade in trades:
        regime = str(trade.get("regime", "Unknown"))
        buckets.setdefault(regime, []).append(trade)

    breakdown: dict[str, dict] = {}
    for regime, bucket in buckets.items():
        n = len(bucket)
        wins = sum(1 for t in bucket if float(t["pnl"]) > 0)
        win_rate = wins / n * 100.0 if n > 0 else 0.0
        avg_pnl_pct = sum(float(t["pnl_pct"]) for t in bucket) / n
        total_pnl = sum(float(t["pnl"]) for t in bucket)
        breakdown[regime] = {
            "trades": n,
            "wins": wins,
            "win_rate": round(win_rate, 2),
            "avg_pnl_pct": round(avg_pnl_pct, 4),
            "total_pnl": round(total_pnl, 4),
        }
        log.debug(
            "Regime breakdown",
            regime=regime,
            trades=n,
            win_rate=round(win_rate, 2),
        )

    return breakdown
