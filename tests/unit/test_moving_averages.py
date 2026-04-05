"""
tests/unit/test_moving_averages.py
───────────────────────────────────
Unit tests for features/moving_averages.py.

Tests
─────
    test_sma_200_correct_value
        SMA_200 on the 200th row (index 199, 0-based) must equal the
        simple arithmetic mean of the first 200 close prices.

    test_sma_150_insufficient_data_raises
        A DataFrame with exactly 149 rows must raise InsufficientDataError.

    test_sma_200_insufficient_data_raises
        A DataFrame with exactly 199 rows must raise InsufficientDataError
        (passes the SMA_150 guard but fails the SMA_200 guard).

    test_ma_slope_direction_rising
        A monotonically rising close series must produce a positive
        MA_slope_200 on the final row.

    test_ma_slope_direction_falling
        A monotonically falling close series must produce a negative
        MA_slope_200 on the final row.

    test_idempotent
        Calling compute() twice on the same DataFrame produces identical
        results (idempotency).

    test_input_not_mutated
        The input DataFrame is never modified in-place.

    test_all_columns_present
        Every expected output column is present in the result.

    test_sma_warmup_nan
        SMA_10 for the first 9 rows must be NaN.

    test_ema21_warmup_nan
        EMA_21 for the first 20 rows must be NaN (min_periods=21).

    test_sma200_last_row_not_nan
        On a 200-row DataFrame, SMA_200 on the last row must not be NaN.

    test_slope_50_positive_trend
        On a uniformly rising series, MA_slope_50 must be positive.

    test_config_defaults_used
        compute() with an empty config dict falls back to defaults and
        does not raise.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

# ── project imports ───────────────────────────────────────────────────────────
from features.moving_averages import compute, MIN_ROWS_REQUIRED
from utils.exceptions import InsufficientDataError


# ─────────────────────────────────────────────────────────────────────────────
# Helpers / factory
# ─────────────────────────────────────────────────────────────────────────────

def _make_df(
    n_rows: int,
    *,
    start_price: float = 1_000.0,
    daily_change: float = 0.5,      # absolute INR change per day (linear trend)
    flat: bool = False,             # if True, all closes = start_price
) -> pd.DataFrame:
    """
    Build a minimal OHLCV DataFrame with *n_rows* trading-day rows.

    Price model: linear trend — close[i] = start_price + i * daily_change
    (or flat if flat=True).  Fully deterministic.

    Uses the same fixture pattern as conftest._make_ohlcv() but
    supports configurable trend direction for slope-direction tests.
    """
    start = date(2022, 1, 3)  # Monday
    rows = []
    d = start
    for i in range(n_rows):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        if flat:
            close = start_price
        else:
            close = start_price + i * daily_change
        close = round(close, 4)
        rows.append({
            "date":   pd.Timestamp(d),
            "open":   close * 0.999,
            "high":   close * 1.002,
            "low":    close * 0.998,
            "close":  close,
            "volume": 500_000,
        })
        d += timedelta(days=1)

    return pd.DataFrame(rows).set_index("date")


def _default_config() -> dict:
    """Minimal config dict matching the structure of settings.yaml."""
    return {
        "stage": {
            "ma200_slope_lookback": 20,
            "ma50_slope_lookback": 10,
        }
    }


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestSMA200CorrectValue:
    """SMA_200 accuracy check against a manual mean."""

    def test_sma_200_correct_value(self, sample_ohlcv_df: pd.DataFrame) -> None:
        """
        SMA_200 at row index 199 (the 200th bar) must exactly match the
        arithmetic mean of the first 200 close prices.
        """
        result = compute(sample_ohlcv_df, _default_config())

        manual_mean = sample_ohlcv_df["close"].iloc[:200].mean()
        computed_sma = result["SMA_200"].iloc[199]

        assert not math.isnan(computed_sma), "SMA_200 at row 199 must not be NaN"
        assert abs(computed_sma - manual_mean) < 1e-8, (
            f"SMA_200={computed_sma:.6f} does not match "
            f"manual mean={manual_mean:.6f}"
        )


class TestInsufficientData:
    """InsufficientDataError is raised before any NaN silently leaks out."""

    def test_sma_150_insufficient_data_raises(self) -> None:
        """149 rows → InsufficientDataError for SMA_150."""
        df = _make_df(149)
        with pytest.raises(InsufficientDataError) as exc_info:
            compute(df, _default_config())
        err = exc_info.value
        assert err.context["required"] == 150
        assert err.context["available"] == 149

    def test_sma_200_insufficient_data_raises(self) -> None:
        """199 rows passes the SMA_150 guard but raises for SMA_200."""
        df = _make_df(199)
        with pytest.raises(InsufficientDataError) as exc_info:
            compute(df, _default_config())
        err = exc_info.value
        assert err.context["required"] == 200
        assert err.context["available"] == 199

    def test_exactly_200_rows_does_not_raise(self) -> None:
        """Exactly 200 rows is the minimum that must succeed without error."""
        df = _make_df(200)
        result = compute(df, _default_config())
        assert "SMA_200" in result.columns


class TestMASlopeDirection:
    """Slope sign tracks the direction of the underlying price series."""

    def test_ma_slope_direction_rising(self) -> None:
        """
        A monotonically rising close series must produce a positive
        MA_slope_200 on the final row.
        """
        df = _make_df(250, start_price=500.0, daily_change=1.0)
        result = compute(df, _default_config())
        slope = result["MA_slope_200"].iloc[-1]
        assert not math.isnan(slope), "MA_slope_200 must not be NaN on final row"
        assert slope > 0.0, f"Expected positive slope for rising series, got {slope}"

    def test_ma_slope_direction_falling(self) -> None:
        """
        A monotonically falling close series must produce a negative
        MA_slope_200 on the final row.
        """
        df = _make_df(250, start_price=5_000.0, daily_change=-1.0)
        result = compute(df, _default_config())
        slope = result["MA_slope_200"].iloc[-1]
        assert not math.isnan(slope), "MA_slope_200 must not be NaN on final row"
        assert slope < 0.0, f"Expected negative slope for falling series, got {slope}"

    def test_slope_50_positive_trend(self) -> None:
        """MA_slope_50 must be positive on a uniformly rising series."""
        df = _make_df(250, start_price=500.0, daily_change=0.5)
        result = compute(df, _default_config())
        slope50 = result["MA_slope_50"].iloc[-1]
        assert not math.isnan(slope50), "MA_slope_50 must not be NaN on final row"
        assert slope50 > 0.0, f"Expected positive MA_slope_50, got {slope50}"


class TestIdempotent:
    """compute() is idempotent — calling it twice yields identical results."""

    def test_idempotent(self, sample_ohlcv_df: pd.DataFrame) -> None:
        """Second call on the same df must produce the same DataFrame."""
        cfg = _default_config()
        first  = compute(sample_ohlcv_df, cfg)
        second = compute(sample_ohlcv_df, cfg)

        # Numeric columns must be equal (NaN == NaN for this comparison)
        pd.testing.assert_frame_equal(first, second, check_like=False)

    def test_idempotent_on_already_computed(self, sample_ohlcv_df: pd.DataFrame) -> None:
        """
        Calling compute() on a df that already has MA columns (as if
        someone pre-computed them) must overwrite and yield the same result.
        """
        cfg = _default_config()
        first = compute(sample_ohlcv_df, cfg)
        # Now pass the RESULT of the first call as input to the second call
        second = compute(first, cfg)
        pd.testing.assert_frame_equal(first, second, check_like=False)


class TestInputNotMutated:
    """The input DataFrame must never be modified in-place."""

    def test_input_not_mutated(self, sample_ohlcv_df: pd.DataFrame) -> None:
        original_cols = list(sample_ohlcv_df.columns)
        original_values = sample_ohlcv_df["close"].copy()

        compute(sample_ohlcv_df, _default_config())

        assert list(sample_ohlcv_df.columns) == original_cols, (
            "compute() added columns to the input df — it must return a copy"
        )
        pd.testing.assert_series_equal(sample_ohlcv_df["close"], original_values)


class TestOutputColumns:
    """All expected output columns are present."""

    EXPECTED_COLUMNS = [
        "SMA_10", "SMA_21", "SMA_50", "SMA_150", "SMA_200",
        "EMA_21",
        "MA_slope_50", "MA_slope_200",
    ]

    def test_all_columns_present(self, sample_ohlcv_df: pd.DataFrame) -> None:
        result = compute(sample_ohlcv_df, _default_config())
        for col in self.EXPECTED_COLUMNS:
            assert col in result.columns, f"Expected column '{col}' is missing"

    def test_original_columns_preserved(self, sample_ohlcv_df: pd.DataFrame) -> None:
        result = compute(sample_ohlcv_df, _default_config())
        for col in ["open", "high", "low", "close", "volume"]:
            assert col in result.columns, f"Original column '{col}' was dropped"


class TestWarmupNaN:
    """Warmup rows for short-period MAs must be NaN; longer MAs must not NaN on last row."""

    def test_sma10_warmup_nan(self, sample_ohlcv_df: pd.DataFrame) -> None:
        """First 9 rows (indices 0-8) of SMA_10 must be NaN."""
        result = compute(sample_ohlcv_df, _default_config())
        warmup = result["SMA_10"].iloc[:9]
        assert warmup.isna().all(), (
            f"Expected NaN in SMA_10 warmup rows, got {warmup.dropna()}"
        )

    def test_ema21_warmup_nan(self, sample_ohlcv_df: pd.DataFrame) -> None:
        """First 20 rows (indices 0-19) of EMA_21 must be NaN (min_periods=21)."""
        result = compute(sample_ohlcv_df, _default_config())
        warmup = result["EMA_21"].iloc[:20]
        assert warmup.isna().all(), (
            f"Expected NaN in EMA_21 warmup rows, got {warmup.dropna()}"
        )

    def test_sma200_last_row_not_nan(self) -> None:
        """On a 200-row DataFrame the last row of SMA_200 must not be NaN."""
        df = _make_df(200)
        result = compute(df, _default_config())
        assert not math.isnan(result["SMA_200"].iloc[-1]), (
            "SMA_200 on row 199 of a 200-row df must not be NaN"
        )

    def test_sma150_last_row_not_nan(self) -> None:
        """On a 200-row DataFrame the last row of SMA_150 must not be NaN."""
        df = _make_df(200)
        result = compute(df, _default_config())
        assert not math.isnan(result["SMA_150"].iloc[-1]), (
            "SMA_150 on the final row must not be NaN"
        )


class TestConfigDefaults:
    """compute() works with a minimal / empty config."""

    def test_empty_config_uses_defaults(self) -> None:
        """Empty config dict must not raise; defaults (20/10) are applied."""
        df = _make_df(250)
        result = compute(df, config={})
        assert "MA_slope_200" in result.columns
        assert "MA_slope_50" in result.columns

    def test_custom_slope_lookbacks(self) -> None:
        """Custom lookback windows from config are respected."""
        df = _make_df(250, daily_change=1.0)
        cfg = {"stage": {"ma200_slope_lookback": 5, "ma50_slope_lookback": 5}}
        result = compute(df, cfg)
        # Slope should be positive for a rising series regardless of window
        assert result["MA_slope_200"].iloc[-1] > 0
        assert result["MA_slope_50"].iloc[-1] > 0


class TestModuleConstant:
    """MIN_ROWS_REQUIRED is exposed at the correct value."""

    def test_min_rows_required_value(self) -> None:
        assert MIN_ROWS_REQUIRED == 200
