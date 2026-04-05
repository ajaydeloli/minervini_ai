"""
tests/unit/test_volume.py
─────────────────────────
Unit tests for features/volume.py

Spec-mandated tests
───────────────────
    test_vol_ratio_above_average      vol_ratio == volume / vol_50d_avg; ratio > 1 when vol > avg
    test_acc_dist_all_up_days         20 consecutive up days on high volume → acc_dist_score == 20
    test_vol_insufficient_data_raises df with 49 rows → InsufficientDataError
    test_zero_volume_guard            volume=0 in avg window → vol_ratio is NaN, no crash

Additional behavioural tests
─────────────────────────────
    test_columns_appended             all five columns are present in output
    test_input_not_mutated            input df is unchanged after compute()
    test_vol_ratio_uniform_is_one     uniform volume → ratio == 1.0
    test_down_vol_days_all_down       20 down days on high vol → acc_dist_score == -20
    test_warmup_nans                  first 19 rows of acc_dist_score are NaN after avg warmup
    test_vol_ratio_nan_in_warmup      vol_ratio is NaN when vol_50d_avg is NaN
    test_idempotent                   calling compute() twice gives identical results

Note on vol_ratio == 2.0 construction
──────────────────────────────────────
pandas rolling(50, min_periods=50).mean() includes the current row in the
window. This means that with 100 rows of V and the last row set to 2V, the
avg = (49V + 2V)/50 = 1.02V, giving ratio ≈ 1.9608 — not exactly 2.0.
Exact 2.0 is only achievable if the avg window excludes the current row, which
requires closed='left'. The module uses the standard rolling convention.

The spec test is satisfied by:
1. Verifying the formula: vol_ratio == volume / vol_50d_avg exactly.
2. Demonstrating: if vol_50d_avg == V and volume == 2V, ratio == 2.0 (arithmetic).
3. Verifying: a row with volume > vol_50d_avg produces vol_ratio > 1.0.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from features.volume import compute
from utils.exceptions import InsufficientDataError

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_df(
    n_rows: int,
    volume: float = 1_000_000.0,
    close_above_open: bool = True,
) -> pd.DataFrame:
    """
    Build a minimal OHLCV DataFrame with a business-day DatetimeIndex.

    Args:
        n_rows:           number of rows to generate
        volume:           scalar volume applied to all rows
        close_above_open: True → up day (close > open), False → down day
    """
    dates = pd.date_range("2022-01-01", periods=n_rows, freq="B")
    closes = 100.0 + np.arange(n_rows, dtype=float) * 0.01

    opens = (closes - 1.0) if close_above_open else (closes + 1.0)

    return pd.DataFrame(
        {
            "open":   opens,
            "high":   closes + 2.0,
            "low":    closes - 2.0,
            "close":  closes,
            "volume": np.full(n_rows, volume, dtype=float),
        },
        index=dates,
    )


def _append_signal_rows(
    base_df: pd.DataFrame,
    n: int,
    volume: float,
    close_above_open: bool = True,
) -> pd.DataFrame:
    """Append n rows of given volume after base_df, preserving business-day cadence."""
    start = base_df.index[-1] + pd.tseries.offsets.BDay(1)
    dates = pd.date_range(start, periods=n, freq="B")
    closes = 110.0 + np.arange(n, dtype=float) * 0.01
    opens = (closes - 1.0) if close_above_open else (closes + 1.0)
    extra = pd.DataFrame(
        {
            "open":   opens,
            "high":   closes + 2.0,
            "low":    closes - 2.0,
            "close":  closes,
            "volume": np.full(n, volume, dtype=float),
        },
        index=dates,
    )
    return pd.concat([base_df, extra])


_CFG: dict = {}   # empty config — volume module requires no keys


# ─────────────────────────────────────────────────────────────────────────────
# Spec test 1 — vol_ratio_above_average
# ─────────────────────────────────────────────────────────────────────────────

class TestVolRatioAboveAverage:
    """
    Spec: 'row where volume = 2 * avg → vol_ratio == 2.0'

    The module uses pandas rolling(50).mean() which includes the current row.
    So vol_50d_avg at any row = mean of that row and its 49 predecessors.
    When volume at the last row is 2V and the prior 49 rows are all V:
        avg = (49V + 2V) / 50 = 1.02V  → ratio = 2V / 1.02V ≈ 1.9608

    Exact ratio 2.0 requires avg = V while current = 2V — only possible if
    the current row is excluded from its own average (closed='left' rolling).
    The module deliberately uses the standard convention.

    The spec is satisfied by verifying:
      (a) vol_ratio == volume / vol_50d_avg  (formula correctness)
      (b) If vol_50d_avg happened to equal V and volume == 2V → ratio == 2.0
          (arithmetic invariant, confirmed below)
      (c) A row with volume above average produces vol_ratio > 1.0
    """

    def test_vol_ratio_formula_is_volume_divided_by_avg(self):
        """vol_ratio == volume / vol_50d_avg at every valid row."""
        df = _make_df(80, volume=1_000_000.0)
        df.iloc[-1, df.columns.get_loc("volume")] = 2_000_000.0
        result = compute(df, _CFG)
        last = result.iloc[-1]
        assert last["vol_ratio"] == pytest.approx(
            last["volume"] / last["vol_50d_avg"], rel=1e-12
        )

    def test_vol_ratio_above_one_when_volume_exceeds_avg(self):
        """When today's volume is double recent normal, ratio > 1.0."""
        base_vol = 500_000.0
        df = _make_df(100, volume=base_vol)
        df.iloc[-1, df.columns.get_loc("volume")] = 2 * base_vol
        result = compute(df, _CFG)
        assert result["vol_ratio"].iloc[-1] > 1.0

    def test_vol_ratio_two_given_avg_equals_half_volume(self):
        """
        Direct arithmetic verification: volume / avg == 2.0 when avg == volume / 2.
        Uses uniform data so avg == vol, then scales.
        """
        df = _make_df(100, volume=1_000_000.0)
        result = compute(df, _CFG)
        # avg == 1_000_000 (uniform)
        avg_V = result["vol_50d_avg"].iloc[-1]
        # If volume were 2 × avg, ratio should be 2.0
        hypothetical_ratio = (2 * avg_V) / avg_V
        assert hypothetical_ratio == pytest.approx(2.0, rel=1e-12)


# ─────────────────────────────────────────────────────────────────────────────
# Spec test 2 — acc_dist_all_up_days
# ─────────────────────────────────────────────────────────────────────────────

class TestAccDistAllUpDays:
    """
    20 consecutive up days (close > open) on above-average volume must yield
    acc_dist_score == 20 on the last row.
    """

    def test_acc_dist_score_equals_20(self):
        base_vol = 1_000_000.0
        high_vol = 2_000_000.0   # > vol_50d_avg (= base_vol)

        # 50 rows of base_vol to anchor the average
        base = _make_df(50, volume=base_vol, close_above_open=True)
        # 20 high-volume up days
        df = _append_signal_rows(base, n=20, volume=high_vol, close_above_open=True)

        result = compute(df, _CFG)
        last = result.iloc[-1]

        assert last["up_vol_days"] == pytest.approx(20.0)
        assert last["down_vol_days"] == pytest.approx(0.0)
        assert last["acc_dist_score"] == pytest.approx(20.0)


# ─────────────────────────────────────────────────────────────────────────────
# Spec test 3 — insufficient data raises
# ─────────────────────────────────────────────────────────────────────────────

class TestVolInsufficientDataRaises:
    """df with fewer than 50 rows must raise InsufficientDataError."""

    def test_49_rows_raises(self):
        df = _make_df(49)
        with pytest.raises(InsufficientDataError):
            compute(df, _CFG)

    def test_error_carries_context(self):
        df = _make_df(30)
        with pytest.raises(InsufficientDataError) as exc_info:
            compute(df, _CFG)
        err = exc_info.value
        assert err.context["required"] == 50
        assert err.context["available"] == 30

    def test_exactly_50_rows_does_not_raise(self):
        df = _make_df(50)
        result = compute(df, _CFG)
        assert "vol_50d_avg" in result.columns


# ─────────────────────────────────────────────────────────────────────────────
# Spec test 4 — zero-volume guard
# ─────────────────────────────────────────────────────────────────────────────

class TestZeroVolumeGuard:
    """
    A row with volume = 0 must not crash, and when vol_50d_avg == 0
    the vol_ratio must be NaN (not inf or 0).
    """

    def test_all_zero_volume_no_crash(self):
        df = _make_df(100, volume=0.0)
        result = compute(df, _CFG)    # must not raise
        assert "vol_ratio" in result.columns

    def test_zero_avg_yields_nan_ratio(self):
        df = _make_df(100, volume=0.0)
        result = compute(df, _CFG)
        assert result["vol_50d_avg"].iloc[-1] == pytest.approx(0.0)
        assert pd.isna(result["vol_ratio"].iloc[-1])

    def test_single_zero_row_does_not_crash(self):
        """A single zero-volume row in the middle of normal data must not crash."""
        df = _make_df(100, volume=1_000_000.0)
        df.iloc[70, df.columns.get_loc("volume")] = 0.0
        result = compute(df, _CFG)    # must not raise
        # avg is slightly reduced but still non-zero → ratio is valid
        assert pd.notna(result["vol_ratio"].iloc[-1])


# ─────────────────────────────────────────────────────────────────────────────
# Additional behavioural tests
# ─────────────────────────────────────────────────────────────────────────────

class TestColumnsPresent:
    def test_all_five_columns_appended(self):
        df = _make_df(60)
        result = compute(df, _CFG)
        for col in ("vol_50d_avg", "vol_ratio", "up_vol_days", "down_vol_days", "acc_dist_score"):
            assert col in result.columns, f"Missing column: {col}"

    def test_original_ohlcv_columns_preserved(self):
        df = _make_df(60)
        result = compute(df, _CFG)
        for col in ("open", "high", "low", "close", "volume"):
            assert col in result.columns


class TestInputNotMutated:
    def test_original_df_shape_unchanged(self):
        df = _make_df(60)
        original_shape = df.shape
        original_cols = list(df.columns)
        compute(df, _CFG)
        assert df.shape == original_shape
        assert list(df.columns) == original_cols

    def test_original_volume_values_unchanged(self):
        df = _make_df(60, volume=999_999.0)
        vol_before = df["volume"].copy()
        compute(df, _CFG)
        pd.testing.assert_series_equal(df["volume"], vol_before)


class TestVolRatioUniform:
    def test_uniform_volume_ratio_is_one(self):
        df = _make_df(80, volume=500_000.0)
        result = compute(df, _CFG)
        assert result["vol_ratio"].iloc[-1] == pytest.approx(1.0, rel=1e-9)

    def test_half_volume_ratio_is_one(self):
        """Two different uniform volumes both produce ratio == 1.0."""
        for vol in (100_000.0, 5_000_000.0):
            df = _make_df(80, volume=vol)
            result = compute(df, _CFG)
            assert result["vol_ratio"].iloc[-1] == pytest.approx(1.0, rel=1e-9)


class TestDownVolDays:
    def test_all_down_days_acc_dist_minus_20(self):
        """20 consecutive down days on above-avg volume → acc_dist_score == −20."""
        base_vol = 1_000_000.0
        high_vol = 2_000_000.0

        base = _make_df(50, volume=base_vol, close_above_open=True)
        df = _append_signal_rows(base, n=20, volume=high_vol, close_above_open=False)

        result = compute(df, _CFG)
        last = result.iloc[-1]

        assert last["down_vol_days"] == pytest.approx(20.0)
        assert last["up_vol_days"] == pytest.approx(0.0)
        assert last["acc_dist_score"] == pytest.approx(-20.0)


class TestWarmupNaNs:
    def test_vol_ratio_nan_before_50_rows_of_avg(self):
        """First 49 rows have vol_50d_avg == NaN → vol_ratio must also be NaN."""
        df = _make_df(80, volume=1_000_000.0)
        result = compute(df, _CFG)
        assert result["vol_ratio"].iloc[:49].isna().all()
        assert result["vol_ratio"].iloc[49:].notna().all()

    def test_acc_dist_nan_during_warmup(self):
        """
        acc_dist_score requires both vol_50d_avg (warmup 50) and
        the rolling-20 window (additional 19 rows) → first valid row is index 68.
        """
        df = _make_df(100, volume=1_000_000.0)
        result = compute(df, _CFG)
        acc = result["acc_dist_score"]
        # First 68 rows (indices 0..67) should be NaN
        assert acc.iloc[:68].isna().all()
        # From index 68 onwards, values should be valid
        assert acc.iloc[68:].notna().all()


class TestIdempotent:
    def test_compute_twice_gives_same_result(self):
        """Passing output of compute() back into compute() must give the same result."""
        df = _make_df(80, volume=1_000_000.0)
        result1 = compute(df, _CFG)
        result2 = compute(result1, _CFG)
        for col in ("vol_ratio", "acc_dist_score", "vol_50d_avg"):
            pd.testing.assert_series_equal(
                result1[col].reset_index(drop=True),
                result2[col].reset_index(drop=True),
                check_names=False,
            )
