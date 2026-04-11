"""
tests/unit/test_atr.py
───────────────────────
Unit tests for features/atr.py

Test suite
──────────
    test_atr_14_positive             — all post-warmup ATR_14 values are > 0
    test_atr_pct_range               — ATR_pct stays in (0, 100) for sane data
    test_atr_insufficient_data_raises — df with 14 rows raises InsufficientDataError
    test_wilder_smoothing            — manual verification of first 3 ATR values

Design notes
────────────
    • Fixtures use deterministic synthetic OHLCV data — no yfinance calls.
    • The ``make_ohlcv`` helper builds a DataFrame that satisfies the
      ingestion validator contract (high >= low, close within [low, high]).
    • All tests run in isolation; no shared mutable state.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from features.atr import compute, _wilder_atr
from utils.exceptions import InsufficientDataError

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def make_ohlcv(n: int, base_price: float = 100.0, seed: int = 42) -> pd.DataFrame:
    """
    Generate a deterministic synthetic OHLCV DataFrame with *n* rows.

    The series is constructed so that:
      - close values walk randomly around base_price  (always positive)
      - high  = close + small positive offset
      - low   = close - small positive offset  (always < high)
      - open  = previous close (realistic)
      - volume is a fixed positive integer

    This satisfies all OHLCV sanity checks without real market data.
    """
    rng = np.random.default_rng(seed)

    # Random walk for close prices — clipped to stay positive
    returns = rng.normal(loc=0.0, scale=0.5, size=n)
    closes = np.maximum(base_price + np.cumsum(returns), 5.0)

    spreads = rng.uniform(0.1, 1.5, size=n)   # daily high-low spread
    highs   = closes + spreads
    lows    = np.maximum(closes - spreads, 1.0)
    opens   = np.concatenate([[base_price], closes[:-1]])

    dates = pd.date_range(start="2023-01-01", periods=n, freq="B")

    return pd.DataFrame(
        {
            "open":   opens,
            "high":   highs,
            "low":    lows,
            "close":  closes,
            "volume": 500_000,
        },
        index=dates,
    )


_DEFAULT_CONFIG: dict = {}    # empty config → all defaults kick in


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestATR14Positive:
    """ATR_14 must be strictly positive after the warmup period."""

    def test_atr_14_positive(self):
        """
        All ATR_14 values from index 14 onward must be > 0.
        NaN values in the warmup region (indices 0–13) are acceptable.
        """
        df = make_ohlcv(60)
        result = compute(df, _DEFAULT_CONFIG)

        assert "ATR_14" in result.columns, "ATR_14 column missing"

        post_warmup = result["ATR_14"].iloc[14:]
        assert post_warmup.notna().all(), "ATR_14 contains NaN after warmup"
        assert (post_warmup > 0).all(), "ATR_14 has non-positive values after warmup"

    def test_warmup_rows_are_nan(self):
        """Rows 0–13 must be NaN (not enough history for Wilder's seed)."""
        df = make_ohlcv(60)
        result = compute(df, _DEFAULT_CONFIG)

        warmup = result["ATR_14"].iloc[:14]
        assert warmup.isna().all(), "Warmup rows should be NaN"


class TestATRPctRange:
    """ATR_pct must be in the open interval (0, 100) for sane price data."""

    def test_atr_pct_range(self):
        """
        ATR_pct (post warmup) must satisfy 0 < ATR_pct < 100.

        For any real stock with daily moves smaller than the stock price
        itself, ATR_pct should be well within [0, 100].
        The synthetic data uses prices around 100 with spreads of 0.1–1.5,
        giving ATR_pct in roughly the 0.5–3% range.
        """
        df = make_ohlcv(100)
        result = compute(df, _DEFAULT_CONFIG)

        assert "ATR_pct" in result.columns, "ATR_pct column missing"

        post_warmup = result["ATR_pct"].iloc[14:]
        assert post_warmup.notna().all(), "ATR_pct contains unexpected NaN after warmup"
        assert (post_warmup > 0).all(),   "ATR_pct has non-positive values"
        assert (post_warmup < 100).all(), "ATR_pct exceeds 100 — likely a bug"

    def test_atr_pct_proportional_to_volatility(self):
        """
        A high-volatility series should produce a higher ATR_pct than a
        low-volatility series with the same base price.
        """
        low_vol  = make_ohlcv(60, seed=1)
        high_vol = make_ohlcv(60, seed=1)

        # Amplify spreads for high_vol by scaling high-low range
        high_vol["high"] = high_vol["close"] + 5.0
        high_vol["low"]  = (high_vol["close"] - 5.0).clip(lower=1.0)

        r_low  = compute(low_vol,  _DEFAULT_CONFIG)
        r_high = compute(high_vol, _DEFAULT_CONFIG)

        avg_low  = r_low["ATR_pct"].iloc[14:].mean()
        avg_high = r_high["ATR_pct"].iloc[14:].mean()

        assert avg_high > avg_low, (
            f"High-vol ATR_pct ({avg_high:.3f}) should exceed "
            f"low-vol ATR_pct ({avg_low:.3f})"
        )


class TestInsufficientData:
    """InsufficientDataError must be raised when df has fewer than 15 rows."""

    def test_atr_insufficient_data_raises(self):
        """
        A DataFrame with exactly 14 rows must raise InsufficientDataError.
        (period=14 requires 15 rows: 14 TRs each need a prev_close.)
        """
        df = make_ohlcv(14)
        with pytest.raises(InsufficientDataError):
            compute(df, _DEFAULT_CONFIG)

    def test_exactly_15_rows_does_not_raise(self):
        """15 rows (the minimum) must succeed without error."""
        df = make_ohlcv(15)
        result = compute(df, _DEFAULT_CONFIG)
        # Only index 14 should be non-NaN (the seed value)
        assert result["ATR_14"].iloc[14] > 0

    def test_zero_rows_raises(self):
        """Empty DataFrame must also raise InsufficientDataError."""
        df = make_ohlcv(0) if False else pd.DataFrame(
            columns=["open", "high", "low", "close", "volume"]
        )
        with pytest.raises(InsufficientDataError):
            compute(df, _DEFAULT_CONFIG)

    def test_13_rows_raises(self):
        """13 rows (< 15) must raise InsufficientDataError."""
        df = make_ohlcv(13)
        with pytest.raises(InsufficientDataError):
            compute(df, _DEFAULT_CONFIG)


class TestWilderSmoothing:
    """
    Manual verification of Wilder's ATR calculation.

    We construct a tiny DataFrame with known OHLC values, compute the
    expected ATR by hand, and assert the module matches exactly.

    Chosen values (all integers for easy manual arithmetic):
        Row  open  high  low  close
          0   100   105   95   100     ← TR[0] = NaN (no prev_close)
          1   100   104   96   102     ← TR[1] = max(8, 4, 6) = 8
          2   102   110   100  108     ← TR[2] = max(10, 8, 8) = 10  (prev_close=102)
          3   108   112   104  106     ← TR[3] = max(8, 4, 2) = 8    (prev_close=108)
          4   106   109   103  107     ← TR[4] = max(6, 3, 3) = 6    (prev_close=106)
          5   107   111   105  109     ← TR[5] = max(6, 4, 4) = 6    (prev_close=107)
          6   109   113   107  110     ← TR[6] = max(6, 4, 2) = 6    (prev_close=109)
          7   110   114   108  112     ← TR[7] = max(6, 4, 4) = 6    (prev_close=110)
          8   112   116   110  114     ← TR[8] = max(6, 4, 4) = 6    (prev_close=112)
          9   114   118   112  116     ← TR[9] = max(6, 4, 4) = 6    (prev_close=114)
         10   116   120   114  118     ← TR[10]= max(6, 4, 4) = 6    (prev_close=116)
         11   118   122   116  120     ← TR[11]= max(6, 4, 4) = 6    (prev_close=118)
         12   120   124   118  122     ← TR[12]= max(6, 4, 4) = 6    (prev_close=120)
         13   122   126   120  124     ← TR[13]= max(6, 4, 4) = 6    (prev_close=122)
         14   124   128   122  126     ← TR[14]= max(6, 4, 4) = 6    (prev_close=124)
         15   126   130   124  128     ← TR[15]= max(6, 4, 4) = 6    (prev_close=126)
         16   128   132   126  130     ← TR[16]= max(6, 4, 4) = 6    (prev_close=128)

    ATR computation:
        TRs used for seed (TR[1..14]):  8,10,8,6,6,6,6,6,6,6,6,6,6,6
        ATR_14[14] = (8+10+8+6+6+6+6+6+6+6+6+6+6+6) / 14 = 92/14 ≈ 6.571428...

        ATR_14[15] = (ATR_14[14] * 13 + TR[15]) / 14
                   = (6.571428... * 13 + 6) / 14
                   = (85.428571... + 6) / 14
                   = 91.428571... / 14
                   ≈ 6.530612...

        ATR_14[16] = (ATR_14[15] * 13 + TR[16]) / 14
                   = (6.530612... * 13 + 6) / 14
                   = (84.897959... + 6) / 14
                   = 90.897959... / 14
                   ≈ 6.492711...
    """

    OHLC_DATA = [
        # open, high, low,  close
        (100,   105,   95,   100),
        (100,   104,   96,   102),
        (102,   110,  100,   108),
        (108,   112,  104,   106),
        (106,   109,  103,   107),
        (107,   111,  105,   109),
        (109,   113,  107,   110),
        (110,   114,  108,   112),
        (112,   116,  110,   114),
        (114,   118,  112,   116),
        (116,   120,  114,   118),
        (118,   122,  116,   120),
        (120,   124,  118,   122),
        (122,   126,  120,   124),
        (124,   128,  122,   126),
        (126,   130,  124,   128),
        (128,   132,  126,   130),
    ]

    @pytest.fixture()
    def tiny_df(self) -> pd.DataFrame:
        rows = self.OHLC_DATA
        dates = pd.date_range("2024-01-01", periods=len(rows), freq="B")
        opens, highs, lows, closes = zip(*rows)
        return pd.DataFrame(
            {
                "open":   list(opens),
                "high":   list(highs),
                "low":    list(lows),
                "close":  list(closes),
                "volume": 1_000_000,
            },
            index=dates,
        )

    def test_wilder_smoothing(self, tiny_df):
        """
        Assert the first three valid ATR_14 values match manual calculations.
        Tolerance: 1e-6 (floating-point arithmetic should be exact enough).
        """
        result = compute(tiny_df, _DEFAULT_CONFIG)
        atr = result["ATR_14"]

        # ── First ATR_14 value (index 14 = row 14) ───────────────────────────
        # Seed = mean of TR[1..14] = (8+10+8+6+6+6+6+6+6+6+6+6+6+6)/14
        expected_seed = (8 + 10 + 8 + 6 + 6 + 6 + 6 + 6 + 6 + 6 + 6 + 6 + 6 + 6) / 14
        assert atr.iloc[14] == pytest.approx(expected_seed, rel=1e-6), (
            f"Seed ATR_14 mismatch: got {atr.iloc[14]:.8f}, "
            f"expected {expected_seed:.8f}"
        )

        # ── Second ATR_14 value (index 15) ────────────────────────────────────
        expected_15 = (expected_seed * 13 + 6) / 14
        assert atr.iloc[15] == pytest.approx(expected_15, rel=1e-6), (
            f"ATR_14[15] mismatch: got {atr.iloc[15]:.8f}, "
            f"expected {expected_15:.8f}"
        )

        # ── Third ATR_14 value (index 16) ─────────────────────────────────────
        expected_16 = (expected_15 * 13 + 6) / 14
        assert atr.iloc[16] == pytest.approx(expected_16, rel=1e-6), (
            f"ATR_14[16] mismatch: got {atr.iloc[16]:.8f}, "
            f"expected {expected_16:.8f}"
        )

    def test_tr_row1_correct(self, tiny_df):
        """
        Spot-check: TR[1] should equal max(8, 4, 6) = 8.
        We verify indirectly via the seed value, which includes TR[1].
        A seed of 92/14 ≈ 6.571 is only correct if TR[1]=8 is included.
        """
        result = compute(tiny_df, _DEFAULT_CONFIG)
        seed = result["ATR_14"].iloc[14]
        # If TR[1] were wrong (e.g. 6 instead of 8), seed = (6+10+8+...)/14 = 90/14 ≈ 6.428
        assert seed == pytest.approx(92 / 14, rel=1e-6), (
            "Seed value implies TR[1] is not computed correctly"
        )


class TestNonMutation:
    """compute() must not modify the input DataFrame."""

    def test_input_df_not_mutated(self):
        df = make_ohlcv(30)
        original_cols  = list(df.columns)
        original_close = df["close"].copy()

        compute(df, _DEFAULT_CONFIG)

        assert list(df.columns) == original_cols, "Input df columns were mutated"
        pd.testing.assert_series_equal(df["close"], original_close)

    def test_returns_new_dataframe(self):
        df = make_ohlcv(30)
        result = compute(df, _DEFAULT_CONFIG)
        assert result is not df, "compute() returned the same object instead of a copy"


class TestConfigurablePeriod:
    """The ATR period must be honoured when supplied via config."""

    def test_custom_period_column_name(self):
        """Config period=20 should produce ATR_20 (not ATR_14)."""
        df = make_ohlcv(40)
        config = {"atr": {"period": 20}}
        result = compute(df, config)

        assert "ATR_20" in result.columns, "ATR_20 column missing for period=20"
        assert "ATR_14" not in result.columns, "ATR_14 should not appear for period=20"

    def test_custom_period_insufficient_data(self):
        """period=20 needs 21 rows; 20 rows must raise InsufficientDataError."""
        df = make_ohlcv(20)
        config = {"atr": {"period": 20}}
        with pytest.raises(InsufficientDataError):
            compute(df, config)
