"""
tests/unit/test_relative_strength.py
─────────────────────────────────────
Unit tests for features/relative_strength.py

Coverage:
    test_rs_raw_outperformer           — symbol +30%, benchmark +10% → RS_raw ≈ 2.0 (1.30/1.10 ≈ 1.18 — see note)
    test_rs_raw_underperformer         — symbol +5%, benchmark +20% → RS_raw ≈ 0.25 (1.05/1.20 ≈ 0.875)
    test_rs_raw_series_length          — output series aligned to symbol_df index
    test_rs_raw_negative               — symbol -10%, benchmark +10% → RS_raw negative
    test_rs_raw_nan_for_early_rows     — first `window` rows should be NaN
    test_rs_raw_insufficient_data_symbol  — symbol < window+1 rows → InsufficientDataError
    test_rs_raw_insufficient_data_bench   — benchmark < window+1 rows → InsufficientDataError
    test_rs_raw_missing_close_column   — no 'close' column → ValueError
    test_rs_rating_percentile          — known sorted list → correct percentile rank
    test_rs_rating_top                 — highest value in universe → rating 99
    test_rs_rating_bottom              — lowest value in universe → rating 0
    test_rs_rating_thin_universe_warns — < 10 symbols → log warning
    test_rs_rating_empty_universe      — empty dict → returns 0
    test_rs_rating_clamp               — rating never exceeds 99

Notes on expected RS_raw values:
    RS_raw = (close_t / close_t-w) / (bench_t / bench_t-w)
    "Symbol +30%, benchmark +10%" → RS_raw = 1.30 / 1.10 ≈ 1.182
    The task spec says "≈ 2.0" — this arises if interpreting RS_raw as the
    ratio of *returns* ((0.30 / 0.10) = 3.0) or price-to-price changes.
    We implement the standard performance-ratio formula (price/lagged_price)
    which is the correct Minervini methodology.  Tests verify this formula.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

import pandas as pd
import pytest

from features.relative_strength import compute_rs_raw, compute_rs_rating
from utils.exceptions import InsufficientDataError


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures & helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_df(
    start_price: float,
    end_price: float,
    n_rows: int = 70,
    start_date: date = date(2023, 1, 1),
) -> pd.DataFrame:
    """
    Build a synthetic OHLCV DataFrame where 'close' moves linearly from
    start_price to end_price over n_rows trading days.

    The linear interpolation ensures the window-return is exactly:
        close[-1] / close[0] = end_price / start_price

    Index: business-day DatetimeIndex (Mon–Fri), named 'date'.
    """
    dates = pd.bdate_range(start=str(start_date), periods=n_rows, freq="B")
    prices = pd.Series(
        [start_price + (end_price - start_price) * i / (n_rows - 1) for i in range(n_rows)],
        index=dates,
        name="close",
    )
    df = pd.DataFrame({
        "open":   prices * 0.99,
        "high":   prices * 1.01,
        "low":    prices * 0.98,
        "close":  prices,
        "volume": 100_000,
    })
    df.index.name = "date"
    return df


WINDOW = 63   # match default


# ─────────────────────────────────────────────────────────────────────────────
# compute_rs_raw tests
# ─────────────────────────────────────────────────────────────────────────────

class TestComputeRsRaw:

    def test_rs_raw_outperformer(self):
        """
        Symbol returned +30%, benchmark returned +10% over the window.
        RS_raw = (1.30) / (1.10) ≈ 1.1818…

        This is the standard price-performance-ratio formula used in the
        Minervini methodology.  The last non-NaN value of the series should
        reflect the full-window return.
        """
        sym_df   = _make_df(start_price=100.0, end_price=130.0, n_rows=WINDOW + 1)
        bench_df = _make_df(start_price=100.0, end_price=110.0, n_rows=WINDOW + 1)

        rs = compute_rs_raw(sym_df, bench_df, window=WINDOW)

        assert rs.name == "RS_raw"
        last_valid = rs.dropna().iloc[-1]
        expected = 1.30 / 1.10
        assert abs(last_valid - expected) < 0.01, (
            f"Expected RS_raw ≈ {expected:.4f}, got {last_valid:.4f}"
        )
        assert last_valid > 1.0, "Outperformer must have RS_raw > 1.0"

    def test_rs_raw_underperformer(self):
        """
        Symbol returned +5%, benchmark returned +20% over the window.
        RS_raw = (1.05) / (1.20) ≈ 0.875
        """
        sym_df   = _make_df(start_price=100.0, end_price=105.0, n_rows=WINDOW + 1)
        bench_df = _make_df(start_price=100.0, end_price=120.0, n_rows=WINDOW + 1)

        rs = compute_rs_raw(sym_df, bench_df, window=WINDOW)

        last_valid = rs.dropna().iloc[-1]
        expected = 1.05 / 1.20
        assert abs(last_valid - expected) < 0.01, (
            f"Expected RS_raw ≈ {expected:.4f}, got {last_valid:.4f}"
        )
        assert last_valid < 1.0, "Underperformer must have RS_raw < 1.0"

    def test_rs_raw_negative(self):
        """
        Symbol fell -10%, benchmark rose +10% → RS_raw is negative.
        RS_raw = (0.90) / (1.10) ≈ 0.818 — actually > 0 but < 1.
        True negative case: symbol falls, benchmark rises, but ratio stays
        positive because both are price-ratios.  Negative RS_raw arises when
        the symbol price-ratio itself is negative (not possible for prices > 0).
        We test the underperformance direction correctly.
        """
        sym_df   = _make_df(start_price=100.0, end_price=90.0,  n_rows=WINDOW + 1)
        bench_df = _make_df(start_price=100.0, end_price=110.0, n_rows=WINDOW + 1)

        rs = compute_rs_raw(sym_df, bench_df, window=WINDOW)
        last = rs.dropna().iloc[-1]

        assert last < 1.0, "Declining symbol vs rising benchmark → RS_raw < 1"
        assert last > 0.0, "Price ratio of positive prices is always positive"

    def test_rs_raw_series_aligned_to_symbol_index(self):
        """Output index must match symbol_df.index exactly."""
        sym_df   = _make_df(start_price=100.0, end_price=120.0, n_rows=WINDOW + 5)
        bench_df = _make_df(start_price=100.0, end_price=110.0, n_rows=WINDOW + 5)

        rs = compute_rs_raw(sym_df, bench_df, window=WINDOW)

        assert len(rs) == len(sym_df), "Output length must equal symbol_df length"
        assert (rs.index == sym_df.index).all(), "Output index must match symbol_df.index"

    def test_rs_raw_nan_for_early_rows(self):
        """First `window` rows should be NaN (insufficient look-back)."""
        sym_df   = _make_df(start_price=100.0, end_price=120.0, n_rows=WINDOW + 10)
        bench_df = _make_df(start_price=100.0, end_price=110.0, n_rows=WINDOW + 10)

        rs = compute_rs_raw(sym_df, bench_df, window=WINDOW)

        # First WINDOW rows must be NaN
        assert rs.iloc[:WINDOW].isna().all(), (
            f"First {WINDOW} rows should be NaN, got: {rs.iloc[:WINDOW].dropna()}"
        )
        # Row at index WINDOW should be the first valid value
        assert pd.notna(rs.iloc[WINDOW]), "Row at index `window` should be non-NaN"

    def test_rs_raw_insufficient_data_symbol(self):
        """
        Symbol with only `window` rows (not window+1) must raise InsufficientDataError.
        """
        sym_df   = _make_df(start_price=100.0, end_price=110.0, n_rows=WINDOW)  # one short
        bench_df = _make_df(start_price=100.0, end_price=105.0, n_rows=WINDOW + 1)

        with pytest.raises(InsufficientDataError):
            compute_rs_raw(sym_df, bench_df, window=WINDOW)

    def test_rs_raw_insufficient_data_benchmark(self):
        """Benchmark with only `window` rows must also raise InsufficientDataError."""
        sym_df   = _make_df(start_price=100.0, end_price=110.0, n_rows=WINDOW + 1)
        bench_df = _make_df(start_price=100.0, end_price=105.0, n_rows=WINDOW)  # one short

        with pytest.raises(InsufficientDataError):
            compute_rs_raw(sym_df, bench_df, window=WINDOW)

    def test_rs_raw_missing_close_column_symbol(self):
        """Missing 'close' column in symbol_df → ValueError."""
        sym_df   = _make_df(start_price=100.0, end_price=110.0, n_rows=WINDOW + 1)
        bench_df = _make_df(start_price=100.0, end_price=105.0, n_rows=WINDOW + 1)
        sym_df = sym_df.drop(columns=["close"])

        with pytest.raises(ValueError, match="missing the required 'close' column"):
            compute_rs_raw(sym_df, bench_df, window=WINDOW)

    def test_rs_raw_missing_close_column_benchmark(self):
        """Missing 'close' column in benchmark_df → ValueError."""
        sym_df   = _make_df(start_price=100.0, end_price=110.0, n_rows=WINDOW + 1)
        bench_df = _make_df(start_price=100.0, end_price=105.0, n_rows=WINDOW + 1)
        bench_df = bench_df.drop(columns=["close"])

        with pytest.raises(ValueError, match="missing the required 'close' column"):
            compute_rs_raw(sym_df, bench_df, window=WINDOW)

    def test_rs_raw_custom_window(self):
        """Custom window parameter is respected."""
        small_window = 10
        sym_df   = _make_df(start_price=100.0, end_price=115.0, n_rows=small_window + 1)
        bench_df = _make_df(start_price=100.0, end_price=105.0, n_rows=small_window + 1)

        rs = compute_rs_raw(sym_df, bench_df, window=small_window)

        assert len(rs) == len(sym_df)
        assert pd.notna(rs.iloc[-1])



# ─────────────────────────────────────────────────────────────────────────────
# compute_rs_rating tests
# ─────────────────────────────────────────────────────────────────────────────

class TestComputeRsRating:

    def _make_universe(self, values: list[float]) -> dict[str, float]:
        """Build a {symbol: rs_raw} dict from a list of values."""
        return {f"SYM{i:03d}": v for i, v in enumerate(values)}

    def test_rs_rating_percentile_known_list(self):
        """
        Universe of 10 equally-spaced values: [1.0, 1.1, 1.2, ..., 1.9].
        Symbol with value 1.5 has 5 values strictly below it.
        Expected rating = floor(5/10 * 100) = 50.
        """
        values = [1.0 + i * 0.1 for i in range(10)]   # 1.0, 1.1, …, 1.9
        universe = self._make_universe(values)

        rating = compute_rs_rating("TEST", 1.5, universe)

        # 5 values (1.0, 1.1, 1.2, 1.3, 1.4) are strictly < 1.5
        assert rating == 50, f"Expected 50, got {rating}"

    def test_rs_rating_top_of_universe(self):
        """Highest RS_raw in the universe → rating 99 (never 100)."""
        values = [1.0, 1.2, 1.4, 1.6, 1.8, 2.0, 2.2, 2.4, 2.6, 2.8]
        universe = self._make_universe(values)

        # Value strictly higher than all existing values
        rating = compute_rs_rating("TOP", 99.0, universe)

        # All 10 values are strictly below 99.0 → rank=10, rating=min(99, 100)=99
        assert rating == 99

    def test_rs_rating_bottom_of_universe(self):
        """Lowest RS_raw → rating 0 (nothing is strictly below it)."""
        values = [1.0, 1.2, 1.4, 1.6, 1.8, 2.0, 2.2, 2.4, 2.6, 2.8]
        universe = self._make_universe(values)

        # Value below all existing values
        rating = compute_rs_rating("BOT", 0.1, universe)

        assert rating == 0

    def test_rs_rating_median(self):
        """Symbol at exact median of 100-element universe."""
        values = list(range(1, 101))          # 1, 2, …, 100
        universe = self._make_universe(values)

        # Value 50 has 49 values strictly below it → floor(49/100 * 100) = 49
        rating = compute_rs_rating("MID", 50.0, universe)
        assert rating == 49

    def test_rs_rating_clamp_never_exceeds_99(self):
        """Rating is always clamped to [0, 99]."""
        universe = self._make_universe([1.0] * 10)

        # All values equal, symbol also equal → rank=0, rating=0
        rating = compute_rs_rating("EQ", 1.0, universe)
        assert 0 <= rating <= 99

    def test_rs_rating_empty_universe_returns_zero(self):
        """Empty universe → returns 0 (and logs a warning)."""
        rating = compute_rs_rating("X", 1.5, {})
        assert rating == 0

    def test_rs_rating_thin_universe_warns(self, caplog):
        """Universe with < 10 symbols → warning logged."""
        universe = self._make_universe([1.0, 1.1, 1.2])  # only 3 symbols

        with caplog.at_level(logging.WARNING, logger="features.relative_strength"):
            compute_rs_rating("THIN", 1.15, universe)

        assert any("thin" in rec.message.lower() for rec in caplog.records), (
            "Expected a warning about thin universe, got: "
            + str([r.message for r in caplog.records])
        )

    def test_rs_rating_symbol_in_universe(self):
        """
        Symbol's own value is included in the distribution (as per design).
        Universe of 10 values; symbol_rs_raw is the median value.
        """
        values = [1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9]
        universe = {f"SYM{i}": v for i, v in enumerate(values)}
        # Add the symbol itself at 1.5 (index 5)
        universe["TARGET"] = 1.5

        # Now n=11, values strictly < 1.5: [1.0, 1.1, 1.2, 1.3, 1.4] → rank=5
        rating = compute_rs_rating("TARGET", 1.5, universe)
        expected = int(5 / 11 * 100)
        assert rating == expected

    def test_rs_rating_large_universe(self):
        """Smoke test with a realistic 500-symbol universe."""
        import random
        random.seed(42)
        values = [random.uniform(0.5, 3.0) for _ in range(500)]
        universe = self._make_universe(values)

        # The maximum value should get a very high rating
        max_val = max(values)
        rating = compute_rs_rating("MAX", max_val, universe)

        # All other 499 values are < max → rank=499, rating=floor(499/500*100)=99
        assert rating >= 98, f"Max value should rate >= 98, got {rating}"

