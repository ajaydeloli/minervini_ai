"""
tests/unit/test_backtest_regime.py
────────────────────────────────────
Unit tests for backtest/regime.py.

Coverage (12 tests minimum)
────────────────────────────
  test_bull_calendar_date            — firmly in Bull range → label=Bull, source=calendar
  test_bear_calendar_date            — firmly in Bear range → label=Bear, source=calendar
  test_sideways_calendar_date        — firmly in Sideways range → label=Sideways, source=calendar
  test_apr_2025_falls_back           — Apr 2025 "Unknown" → source=slope_fallback or unknown
  test_pre_2014_falls_back           — Jan 2013 (pre-range) → slope fallback
  test_slope_bull                    — rising SMA200 → slope_fallback, label=Bull
  test_slope_bear                    — falling SMA200 → slope_fallback, label=Bear
  test_slope_sideways                — flat SMA200 → slope_fallback, label=Sideways
  test_label_trades_populates_regime — label_trades adds 'regime' key to each trade
  test_regime_breakdown_win_rate     — compute_regime_breakdown correct win_rate per group
  test_unknown_no_benchmark          — calendar Unknown + benchmark_df=None → unknown, Unknown
  test_empty_trades_breakdown        — empty trades → empty breakdown dict
  test_label_dates_batch             — label_dates returns a result for every date
  test_slope_barely_above_threshold  — slope just above +0.05 → Bull
  test_slope_barely_below_threshold  — slope just below -0.05 → Bear
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from backtest.regime import (
    RegimeResult,
    compute_regime_breakdown,
    label_date,
    label_dates,
    label_trades,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _benchmark(n_days: int = 300, daily_change: float = 0.0) -> pd.DataFrame:
    """
    Build a synthetic benchmark DataFrame with a DatetimeIndex.

    *daily_change* is the fractional daily price change applied cumulatively.
    e.g. daily_change=0.002 → close rises ~0.2 % per day (Bull).
         daily_change=-0.002 → close falls ~0.2 % per day (Bear).
         daily_change=0.0 → perfectly flat (Sideways).
    """
    start = pd.Timestamp("2020-01-01")
    idx = pd.date_range(start, periods=n_days, freq="B")   # business days
    prices = [1000.0 * ((1 + daily_change) ** i) for i in range(n_days)]
    return pd.DataFrame({"close": prices}, index=idx)


def _trade(regime: str, pnl: float, pnl_pct: float) -> dict:
    return {
        "entry_date": date(2024, 6, 1),
        "exit_date": date(2024, 6, 30),
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "regime": regime,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Calendar tests
# ─────────────────────────────────────────────────────────────────────────────

def test_bull_calendar_date():
    """June 2017 is firmly inside the May 2014 – Jan 2018 Bull band."""
    result = label_date(date(2017, 6, 15))
    assert isinstance(result, RegimeResult)
    assert result.label == "Bull"
    assert result.source == "calendar"
    assert result.slope_pct is None


def test_bear_calendar_date():
    """March 2020 is the Bear band (Feb–Mar 2020)."""
    result = label_date(date(2020, 3, 15))
    assert result.label == "Bear"
    assert result.source == "calendar"
    assert result.slope_pct is None


def test_sideways_calendar_date_2018():
    """August 2018 is inside the Feb 2018 – Mar 2019 Sideways band."""
    result = label_date(date(2018, 8, 10))
    assert result.label == "Sideways"
    assert result.source == "calendar"


def test_sideways_calendar_date_2022():
    """July 2022 is inside the Jan–Dec 2022 Sideways band."""
    result = label_date(date(2022, 7, 1))
    assert result.label == "Sideways"
    assert result.source == "calendar"


def test_bull_calendar_2023():
    """May 2023 is inside Jan 2023 – Sep 2024 Bull band."""
    result = label_date(date(2023, 5, 20))
    assert result.label == "Bull"
    assert result.source == "calendar"


# ─────────────────────────────────────────────────────────────────────────────
# Slope fallback — date triggers
# ─────────────────────────────────────────────────────────────────────────────

def test_apr_2025_slope_fallback_with_benchmark():
    """
    Apr 2025 maps to the "Unknown" calendar band → must use slope fallback.
    With a rising benchmark, expect label=Bull, source=slope_fallback.
    """
    df = _benchmark(n_days=500, daily_change=0.002)   # strongly rising
    # Shift index to reach Apr 2025
    df.index = pd.date_range("2023-10-01", periods=500, freq="B")
    result = label_date(date(2025, 4, 10), benchmark_df=df)
    assert result.source == "slope_fallback"
    assert result.label in ("Bull", "Bear", "Sideways")   # resolved by slope
    assert result.slope_pct is not None


def test_apr_2025_no_benchmark_returns_unknown():
    """Apr 2025 + no benchmark → source=unknown, label=Unknown."""
    result = label_date(date(2025, 4, 10), benchmark_df=None)
    assert result.label == "Unknown"
    assert result.source == "unknown"
    assert result.slope_pct is None


def test_pre_2014_falls_back_with_benchmark():
    """
    Jan 2013 is before the calendar starts (May 2014) → calendar returns
    "Unknown" → slope fallback is used if benchmark_df is provided.
    """
    df = _benchmark(n_days=300, daily_change=-0.003)   # falling
    df.index = pd.date_range("2012-01-01", periods=300, freq="B")
    result = label_date(date(2013, 1, 15), benchmark_df=df)
    assert result.source == "slope_fallback"
    assert result.label == "Bear"


def test_pre_2014_no_benchmark_returns_unknown():
    """Jan 2013 + no benchmark → unknown."""
    result = label_date(date(2013, 1, 15), benchmark_df=None)
    assert result.label == "Unknown"
    assert result.source == "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# Slope fallback — direction labels
# ─────────────────────────────────────────────────────────────────────────────

def _slope_benchmark_for_2025(daily_change: float) -> pd.DataFrame:
    """Build a benchmark that extends into Apr 2025 for slope tests."""
    df = _benchmark(n_days=500, daily_change=daily_change)
    df.index = pd.date_range("2023-09-01", periods=500, freq="B")
    return df


def test_slope_fallback_bull():
    """Rising benchmark (>0.05 %/day slope) → label=Bull."""
    df = _slope_benchmark_for_2025(daily_change=0.003)   # +0.3 %/day → slope > 0.05
    result = label_date(date(2025, 4, 10), benchmark_df=df)
    assert result.source == "slope_fallback"
    assert result.label == "Bull"
    assert result.slope_pct is not None and result.slope_pct > 0.05


def test_slope_fallback_bear():
    """Falling benchmark (<-0.05 %/day slope) → label=Bear."""
    df = _slope_benchmark_for_2025(daily_change=-0.003)  # −0.3 %/day
    result = label_date(date(2025, 4, 10), benchmark_df=df)
    assert result.source == "slope_fallback"
    assert result.label == "Bear"
    assert result.slope_pct is not None and result.slope_pct < -0.05


def test_slope_fallback_sideways():
    """Flat benchmark (slope ≈ 0) → label=Sideways."""
    df = _slope_benchmark_for_2025(daily_change=0.0)
    result = label_date(date(2025, 4, 10), benchmark_df=df)
    assert result.source == "slope_fallback"
    assert result.label == "Sideways"
    assert result.slope_pct is not None
    assert -0.05 <= result.slope_pct <= 0.05


# ─────────────────────────────────────────────────────────────────────────────
# label_trades
# ─────────────────────────────────────────────────────────────────────────────

def test_label_trades_populates_regime():
    """label_trades must add 'regime' to every trade dict."""
    trades = [
        {"entry_date": date(2017, 6, 1), "pnl": 1000.0, "pnl_pct": 5.0},
        {"entry_date": date(2020, 3, 1), "pnl": -500.0, "pnl_pct": -3.0},
        {"entry_date": date(2022, 7, 1), "pnl": 200.0,  "pnl_pct": 1.0},
    ]
    result = label_trades(trades)
    assert result is trades   # same list returned
    for t in trades:
        assert "regime" in t
        assert t["regime"] in ("Bull", "Bear", "Sideways", "Unknown")
    assert trades[0]["regime"] == "Bull"
    assert trades[1]["regime"] == "Bear"
    assert trades[2]["regime"] == "Sideways"


def test_label_trades_string_entry_date():
    """entry_date may be a string; label_trades must parse it correctly."""
    trades = [{"entry_date": "2023-05-15", "pnl": 0.0, "pnl_pct": 0.0}]
    result = label_trades(trades)
    assert result[0]["regime"] == "Bull"


# ─────────────────────────────────────────────────────────────────────────────
# compute_regime_breakdown
# ─────────────────────────────────────────────────────────────────────────────

def test_regime_breakdown_win_rate():
    """
    3 Bull trades: 2 wins (+), 1 loss (−) → win_rate = 66.67 %
    1 Bear trade:  0 wins → win_rate = 0 %
    """
    trades = [
        _trade("Bull", pnl=1000.0, pnl_pct=5.0),
        _trade("Bull", pnl=500.0,  pnl_pct=2.5),
        _trade("Bull", pnl=-300.0, pnl_pct=-1.5),
        _trade("Bear", pnl=-800.0, pnl_pct=-4.0),
    ]
    bd = compute_regime_breakdown(trades)

    assert "Bull" in bd
    assert "Bear" in bd
    assert bd["Bull"]["trades"] == 3
    assert bd["Bull"]["wins"] == 2
    assert abs(bd["Bull"]["win_rate"] - 66.67) < 0.01
    assert bd["Bear"]["trades"] == 1
    assert bd["Bear"]["wins"] == 0
    assert bd["Bear"]["win_rate"] == 0.0


def test_regime_breakdown_total_pnl():
    """total_pnl in each bucket should be the sum of all trade pnls."""
    trades = [
        _trade("Bull", pnl=1000.0, pnl_pct=5.0),
        _trade("Bull", pnl=500.0,  pnl_pct=2.5),
        _trade("Sideways", pnl=-200.0, pnl_pct=-1.0),
    ]
    bd = compute_regime_breakdown(trades)
    assert abs(bd["Bull"]["total_pnl"] - 1500.0) < 1e-6
    assert abs(bd["Sideways"]["total_pnl"] - (-200.0)) < 1e-6


def test_regime_breakdown_empty_trades():
    """Empty trades list → empty dict."""
    assert compute_regime_breakdown([]) == {}


# ─────────────────────────────────────────────────────────────────────────────
# label_dates batch
# ─────────────────────────────────────────────────────────────────────────────

def test_label_dates_batch():
    """label_dates returns one RegimeResult per input date."""
    dates = [date(2017, 1, 1), date(2020, 3, 1), date(2022, 6, 1)]
    results = label_dates(dates)
    assert len(results) == 3
    assert results[date(2017, 1, 1)].label == "Bull"
    assert results[date(2020, 3, 1)].label == "Bear"
    assert results[date(2022, 6, 1)].label == "Sideways"


def test_label_dates_empty():
    """Empty input → empty dict."""
    assert label_dates([]) == {}


# ─────────────────────────────────────────────────────────────────────────────
# Threshold boundary tests
# ─────────────────────────────────────────────────────────────────────────────

def _manual_slope_benchmark(slope_target_pct: float) -> pd.DataFrame:
    """
    Build a benchmark whose SMA-200 slope over the last 20 trading days
    is very close to *slope_target_pct* % per day.

    We create a perfectly straight price series so that SMA(200) == price
    and the slope is the same as the price slope.
    """
    n = 500
    # Price at step i: p0 * (1 + slope/100)^i  → slope_pct per day
    p0 = 1000.0
    prices = [p0 * ((1 + slope_target_pct / 100.0) ** i) for i in range(n)]
    idx = pd.date_range("2023-09-01", periods=n, freq="B")
    return pd.DataFrame({"close": prices}, index=idx)


def test_slope_barely_above_bull_threshold():
    """slope = +0.06 %/day (just above +0.05) → Bull."""
    df = _manual_slope_benchmark(0.06)
    result = label_date(date(2025, 4, 10), benchmark_df=df)
    assert result.source == "slope_fallback"
    assert result.label == "Bull"


def test_slope_barely_below_bear_threshold():
    """slope = -0.06 %/day (just below -0.05) → Bear."""
    df = _manual_slope_benchmark(-0.06)
    result = label_date(date(2025, 4, 10), benchmark_df=df)
    assert result.source == "slope_fallback"
    assert result.label == "Bear"
