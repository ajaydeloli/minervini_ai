"""
tests/unit/test_pivot.py
─────────────────────────
Unit tests for features/pivot.py

Test suite
──────────
    test_swing_high_detected            — clear peak → is_swing_high True at peak
    test_swing_low_detected             — clear trough → is_swing_low True at trough
    test_last_pivot_high_forward_filled — last_pivot_high constant after pivot
    test_edge_rows_are_nan              — last `window` rows have pd.NA
    test_insufficient_data_raises       — fewer than 2*window+1 rows → InsufficientDataError

Design notes
────────────
    • All series are synthetic, deterministic, and constructed without yfinance.
    • make_ohlcv() builds well-formed OHLCV data (high >= close >= low, volume > 0).
    • Each class isolates one behaviour so failures are immediately actionable.
    • Default window=3 for most tests — small enough to keep fixture rows short,
      large enough to avoid trivial window=1 edge-case behaviour.
    • Pivot indicator columns use pandas BooleanDtype (nullable).  Values returned
      via .iloc[] are numpy bool scalars or pd.NA — never the Python True/False
      singletons — so all truthy checks use bool() / == True rather than ``is True``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from features.pivot import compute
from utils.exceptions import InsufficientDataError

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_WINDOW = 3                         # default test window (keeps fixtures short)
_DEFAULT_CONFIG = {"vcp": {"pivot_window": _WINDOW}}


def make_ohlcv(
    highs: list[float],
    lows:  list[float] | None = None,
    spread: float = 1.0,
) -> pd.DataFrame:
    """
    Build a minimal OHLCV DataFrame from explicit *highs* (and optionally
    *lows*).  If *lows* is omitted each low is set to ``high - spread``.

    Guarantees:
        high >= close >= low
        high > 0, low > 0, volume > 0
    """
    n = len(highs)
    if lows is None:
        lows = [max(h - spread, 0.01) for h in highs]

    assert len(lows) == n, "highs and lows must have the same length"

    closes = [(h + l) / 2.0 for h, l in zip(highs, lows)]
    opens  = closes  # simplification: open == close for synthetic data
    dates  = pd.date_range(start="2024-01-01", periods=n, freq="B")

    return pd.DataFrame(
        {
            "open":   opens,
            "high":   highs,
            "low":    lows,
            "close":  closes,
            "volume": 1_000_000,
        },
        index=dates,
    )


def make_flat_ohlcv(n: int, price: float = 100.0) -> pd.DataFrame:
    """
    All bars at the same price level — no pivots (useful for forward-fill
    testing before any pivot has been detected).
    """
    return make_ohlcv(highs=[price] * n, lows=[price - 1.0] * n)


def is_true(val) -> bool:
    """
    Return True iff *val* is truthy and not NA.

    Handles pandas BooleanDtype scalars (which may be np.True_ / np.False_
    rather than Python singletons), as well as pd.NA.
    """
    return pd.notna(val) and bool(val)


def is_false(val) -> bool:
    """Return True iff *val* is explicitly False (not NA and falsy)."""
    return pd.notna(val) and not bool(val)


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestSwingHighDetected:
    """
    A clear local high in the middle of the confirmable range must be
    detected as a swing high pivot.

    Series design (window=3, 11 bars):
        index:  0    1    2    3    4    5    6    7    8    9   10
        high:  80   85   90   95  100   95   90   85   80   80   80
                                   ↑
                              peak at idx 4 — highest high in window [1..7]

    Confirmable indices: window=3 → [3 .. 7]  (last 3 = 8,9,10 → NA)
    The peak at index 4 is fully interior to the confirmable range.
    """

    HIGHS = [80, 85, 90, 95, 100, 95, 90, 85, 80, 80, 80]
    LOWS  = [75, 80, 85, 90,  95, 90, 85, 80, 75, 75, 75]

    @pytest.fixture()
    def df(self) -> pd.DataFrame:
        return make_ohlcv(self.HIGHS, self.LOWS)

    def test_swing_high_detected(self, df):
        """is_swing_high must be True at the peak bar (index 4)."""
        result = compute(df, _DEFAULT_CONFIG)
        val = result["is_swing_high"].iloc[4]
        assert is_true(val), (
            f"Expected is_swing_high=True at the peak bar (index 4), got {val!r}"
        )

    def test_surrounding_bars_not_swing_high(self, df):
        """Bars adjacent to the peak must not be swing highs."""
        result = compute(df, _DEFAULT_CONFIG)
        for idx in (3, 5):
            val = result["is_swing_high"].iloc[idx]
            assert is_false(val), f"Bar {idx} should not be a swing high, got {val!r}"

    def test_non_peak_confirmable_bars_are_false(self, df):
        """Confirmable bars that are not peaks must be False, not NA."""
        result = compute(df, _DEFAULT_CONFIG)
        confirmable = result["is_swing_high"].iloc[_WINDOW : len(df) - _WINDOW]
        # Only index 4 is True; others in the confirmable range are False.
        assert confirmable.notna().all(), (
            "Confirmable rows must be True or False, never NA"
        )


class TestSwingLowDetected:
    """
    A clear trough in the middle of the series must be detected as a swing low.

    Series design (window=3, 11 bars):
        index:  0    1    2    3    4    5    6    7    8    9   10
        low:   90   85   80   75   70   75   80   85   90   90   90
                                   ↑
                             trough at idx 4 — lowest low in window [1..7]
    """

    LOWS  = [90, 85, 80, 75, 70, 75, 80, 85, 90, 90, 90]
    HIGHS = [h + 5 for h in LOWS]

    @pytest.fixture()
    def df(self) -> pd.DataFrame:
        return make_ohlcv(self.HIGHS, self.LOWS)

    def test_swing_low_detected(self, df):
        """is_swing_low must be True at the trough bar (index 4)."""
        result = compute(df, _DEFAULT_CONFIG)
        val = result["is_swing_low"].iloc[4]
        assert is_true(val), (
            f"Expected is_swing_low=True at the trough bar (index 4), got {val!r}"
        )

    def test_surrounding_bars_not_swing_low(self, df):
        """Bars adjacent to the trough must not be swing lows."""
        result = compute(df, _DEFAULT_CONFIG)
        for idx in (3, 5):
            val = result["is_swing_low"].iloc[idx]
            assert is_false(val), f"Bar {idx} should not be a swing low, got {val!r}"

    def test_swing_high_not_triggered_at_trough(self, df):
        """The trough bar must NOT also be flagged as a swing high."""
        result = compute(df, _DEFAULT_CONFIG)
        sh = result["is_swing_high"].iloc[4]
        # The trough bar has the LOWEST high in its window — not a swing high.
        assert is_false(sh), f"Trough bar should not be a swing high, got {sh!r}"


class TestLastPivotHighForwardFilled:
    """
    After a confirmed swing high pivot, last_pivot_high must hold that
    pivot's price for all subsequent bars until the next swing high.

    Series: two V-shaped peaks separated by a valley.
        peak1 at index 4  (high=100)
        valley between 4 and 9
        peak2 at index 9  (high=110)
        trailing flat tail (indices 10-12 → last window=3 rows → NA)

    Total bars: 13  (enough to confirm both peaks: peak2 needs indices 9±3,
                     i.e. index 9 is within confirmable range [3..9])
    """

    # index:   0    1    2    3    4    5    6    7    8    9   10   11   12
    HIGHS = [80,  85,  90,  95, 100,  95,  90,  95, 100, 110, 105, 100, 100]
    LOWS  = [75,  80,  85,  90,  95,  90,  85,  90,  95, 105, 100,  95,  95]

    @pytest.fixture()
    def df(self) -> pd.DataFrame:
        return make_ohlcv(self.HIGHS, self.LOWS)

    def test_last_pivot_high_forward_filled(self, df):
        """
        Between peak1 (idx 4, high=100) and peak2 (idx 9, high=110),
        last_pivot_high must be 100.0 at every intermediate bar.
        """
        result = compute(df, _DEFAULT_CONFIG)
        lph = result["last_pivot_high"]

        # Confirm peak1 was detected.
        assert is_true(result["is_swing_high"].iloc[4]), (
            "peak1 not detected at index 4"
        )

        # From index 4 onward (inclusive) up to but NOT including peak2 detection
        # at index 9, last_pivot_high must equal the peak1 price.
        peak1_price = float(self.HIGHS[4])  # 100.0
        for idx in range(4, 9):
            assert lph.iloc[idx] == pytest.approx(peak1_price), (
                f"last_pivot_high at index {idx} should be {peak1_price}, "
                f"got {lph.iloc[idx]}"
            )

    def test_last_pivot_high_updates_at_second_peak(self, df):
        """
        After peak2 (idx 9, high=110) is confirmed, last_pivot_high
        must update to 110.0 at index 9 and stay there.
        """
        result = compute(df, _DEFAULT_CONFIG)
        lph = result["last_pivot_high"]

        assert is_true(result["is_swing_high"].iloc[9]), (
            "peak2 not detected at index 9"
        )

        peak2_price = float(self.HIGHS[9])  # 110.0
        assert lph.iloc[9] == pytest.approx(peak2_price), (
            f"last_pivot_high at index 9 should be {peak2_price}"
        )

    def test_last_pivot_high_nan_before_any_pivot(self, df):
        """
        Before the first swing high is confirmed, last_pivot_high must be NaN.
        """
        result = compute(df, _DEFAULT_CONFIG)
        lph = result["last_pivot_high"]
        # peak1 is at index 4; bars 0-3 should have NaN (no prior pivot).
        for idx in range(4):
            assert pd.isna(lph.iloc[idx]), (
                f"last_pivot_high at index {idx} should be NaN before any pivot"
            )


class TestLastPivotLowForwardFilled:
    """
    Mirror of the above test but for last_pivot_low.

    Two troughs:
        trough1 at index 4  (low=50)
        trough2 at index 9  (low=40)
    """

    # index:   0    1    2    3    4    5    6    7    8    9   10   11   12
    LOWS  = [70,  65,  60,  55,  50,  55,  60,  55,  50,  40,  45,  50,  50]
    HIGHS = [h + 5 for h in LOWS]

    @pytest.fixture()
    def df(self) -> pd.DataFrame:
        return make_ohlcv(self.HIGHS, self.LOWS)

    def test_last_pivot_low_forward_filled(self, df):
        """Between trough1 and trough2, last_pivot_low must equal trough1's low."""
        result = compute(df, _DEFAULT_CONFIG)
        lpl = result["last_pivot_low"]

        assert is_true(result["is_swing_low"].iloc[4]), (
            "trough1 not detected at index 4"
        )

        trough1_price = float(self.LOWS[4])  # 50.0
        for idx in range(4, 9):
            assert lpl.iloc[idx] == pytest.approx(trough1_price), (
                f"last_pivot_low at index {idx} should be {trough1_price}"
            )


class TestEdgeRowsAreNaN:
    """
    The last `window` rows must have pd.NA (not False) for both
    is_swing_high and is_swing_low, because future bars are unavailable.
    """

    def test_edge_rows_are_nan(self):
        """
        For a 15-bar series with window=3:
            Last 3 rows (indices 12, 13, 14) → pd.NA for both pivot columns.
        """
        window = 3
        n = 15
        config = {"vcp": {"pivot_window": window}}
        df = make_flat_ohlcv(n)
        result = compute(df, config)

        for col in ("is_swing_high", "is_swing_low"):
            tail = result[col].iloc[n - window :]
            assert tail.isna().all(), (
                f"Last {window} rows of '{col}' must be pd.NA, "
                f"got: {tail.tolist()}"
            )

    def test_edge_rows_exactly_window_count(self):
        """Exactly `window` rows at the end must be NA — no more, no less."""
        window = 5
        n = 20
        config = {"vcp": {"pivot_window": window}}
        df = make_flat_ohlcv(n)
        result = compute(df, config)

        for col in ("is_swing_high", "is_swing_low"):
            tail    = result[col].iloc[n - window:]
            pre_tail = result[col].iloc[:n - window]

            assert tail.isna().all(), (
                f"Last {window} rows of '{col}' must all be NA"
            )
            assert pre_tail.notna().all(), (
                f"Rows before the last {window} of '{col}' must NOT be NA"
            )

    def test_first_window_rows_are_false_not_nan(self):
        """
        The first `window` rows must be False (not NA), because they have
        insufficient lookback — not insufficient future bars.
        """
        window = 3
        n = 15
        config = {"vcp": {"pivot_window": window}}
        df = make_flat_ohlcv(n)
        result = compute(df, config)

        for col in ("is_swing_high", "is_swing_low"):
            head = result[col].iloc[:window]
            assert head.notna().all(), (
                f"First {window} rows of '{col}' must be False (not NA)"
            )
            assert (~head).all(), (
                f"First {window} rows of '{col}' must be False"
            )


class TestInsufficientDataRaises:
    """
    InsufficientDataError must be raised whenever df has fewer than
    2 * window + 1 rows.
    """

    def test_insufficient_data_raises(self):
        """df with 2*window rows (one short) must raise InsufficientDataError."""
        window = 5
        n = 2 * window          # one row short of the minimum
        config = {"vcp": {"pivot_window": window}}
        df = make_flat_ohlcv(n)

        with pytest.raises(InsufficientDataError):
            compute(df, config)

    def test_exactly_minimum_rows_succeeds(self):
        """df with exactly 2*window+1 rows must NOT raise."""
        window = 5
        n = 2 * window + 1
        config = {"vcp": {"pivot_window": window}}
        df = make_flat_ohlcv(n)
        # Should not raise — result may have no confirmed pivots but that's fine.
        result = compute(df, config)
        assert len(result) == n

    def test_zero_rows_raises(self):
        """Empty DataFrame must raise InsufficientDataError."""
        df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        with pytest.raises(InsufficientDataError):
            compute(df, _DEFAULT_CONFIG)

    def test_one_row_raises(self):
        """Single-row DataFrame must raise InsufficientDataError."""
        df = make_flat_ohlcv(1)
        with pytest.raises(InsufficientDataError):
            compute(df, _DEFAULT_CONFIG)

    def test_default_window_minimum(self):
        """
        Default window=5 requires at least 11 rows.
        10 rows must raise; 11 rows must succeed.
        """
        config = {}   # empty config → window defaults to 5
        df_short = make_flat_ohlcv(10)
        df_ok    = make_flat_ohlcv(11)

        with pytest.raises(InsufficientDataError):
            compute(df_short, config)

        result = compute(df_ok, config)
        assert len(result) == 11


class TestNonMutation:
    """compute() must not modify the caller's DataFrame."""

    def test_input_df_not_mutated(self):
        df = make_flat_ohlcv(20)
        original_cols  = list(df.columns)
        original_close = df["close"].copy()

        compute(df, _DEFAULT_CONFIG)

        assert list(df.columns) == original_cols, "Input df columns were mutated"
        pd.testing.assert_series_equal(df["close"], original_close)

    def test_returns_new_dataframe(self):
        df = make_flat_ohlcv(20)
        result = compute(df, _DEFAULT_CONFIG)
        assert result is not df, "compute() must return a new DataFrame, not the same object"


class TestWindowOneEdgeCase:
    """
    With window=1 a row CAN be both a swing high and a swing low.
    This is explicitly permitted by the spec for this tight edge case.

    A pin-bar style candle (widest spread) simultaneously records the highest
    high and lowest low in its 3-bar window.

    Series (3 bars, window=1 → min_rows=3, confirmable range=[1..1]):
        bar 0: high=100, low=90
        bar 1: high=120, low=70   ← highest high AND lowest low in window
        bar 2: high=100, low=90
    """

    def test_can_be_both_at_window_one(self):
        """
        Bar 1 must be flagged as BOTH swing high and swing low simultaneously.
        """
        config = {"vcp": {"pivot_window": 1}}
        highs = [100.0, 120.0, 100.0]
        lows  = [ 90.0,  70.0,  90.0]
        df = make_ohlcv(highs, lows)

        result = compute(df, config)

        # Bar 1 is the only confirmable row (last 1 row = bar 2 → NA).
        sh = result["is_swing_high"].iloc[1]
        sl = result["is_swing_low"].iloc[1]

        assert is_true(sh), f"Bar 1 should be swing high, got {sh!r}"
        assert is_true(sl), f"Bar 1 should be swing low, got {sl!r}"


class TestColumnsPresent:
    """All four expected output columns must always be present."""

    def test_all_output_columns_present(self):
        df = make_flat_ohlcv(20)
        result = compute(df, _DEFAULT_CONFIG)

        for col in ("is_swing_high", "is_swing_low", "last_pivot_high", "last_pivot_low"):
            assert col in result.columns, f"Column '{col}' missing from output"

    def test_original_columns_preserved(self):
        df = make_flat_ohlcv(20)
        original_cols = list(df.columns)
        result = compute(df, _DEFAULT_CONFIG)

        for col in original_cols:
            assert col in result.columns, f"Original column '{col}' was dropped"

    def test_output_length_equals_input_length(self):
        df = make_flat_ohlcv(30)
        result = compute(df, _DEFAULT_CONFIG)
        assert len(result) == len(df)
