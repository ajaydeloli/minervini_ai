"""
tests/unit/test_trend_template.py
──────────────────────────────────
Unit tests for rules/trend_template.py — TrendTemplateResult and
check_trend_template().

Test coverage
─────────────
  test_all_conditions_pass          — perfect row → passes=True, conditions_met=8
  test_c1_fails_close_below_sma150  — close < SMA_150 → C1 False, passes False
  test_c1_fails_close_below_sma200  — close < SMA_200 → C1 False, passes False
  test_c2_fails_sma150_below_sma200 — wrong MA stack → C2 False
  test_c3_fails_slope_negative       — slope200 < 0 → C3 False
  test_c4_fails_sma50_below_sma150   — SMA_50 < SMA_150 → C4 False
  test_c5_fails_close_below_sma50    — close < SMA_50 → C5 False
  test_c6_fails_close_too_close_to_low — not 25% above 52w low → C6 False
  test_c7_fails_close_far_from_high   — more than 25% below 52w high → C7 False
  test_c8_fails_rs_below_threshold    — RS_rating < 70 → C8 False
  test_missing_column_raises          — absent column → RuleEngineError
  test_nan_column_raises              — NaN column → RuleEngineError
  test_conditions_met_count           — partial pass → correct count
  test_details_contain_tick           — passing condition detail contains ✓
  test_details_contain_cross          — failing condition detail contains ✗
  test_missing_high_52w_raises        — high_52w absent → RuleEngineError
  test_missing_low_52w_raises         — low_52w absent → RuleEngineError
  test_custom_thresholds_respected    — non-default config values applied
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from rules.trend_template import TrendTemplateResult, check_trend_template
from utils.exceptions import RuleEngineError


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _default_config(**overrides) -> dict:
    """Minimal config dict matching settings.yaml defaults."""
    tt = {
        "ma200_slope_lookback": 20,
        "pct_above_52w_low":  25.0,
        "pct_below_52w_high": 25.0,
        "min_rs_rating":      70,
    }
    tt.update(overrides)
    return {"trend_template": tt}


def _passing_row(**overrides) -> pd.Series:
    """
    A pd.Series that satisfies ALL 8 Trend Template conditions by default.

    Price layout (all conditions clearly met):
        close     = 150.0
        SMA_50    = 130.0   (close > SMA_50 ✓, SMA_50 > SMA_150/200 ✓)
        SMA_150   = 120.0   (close > SMA_150 ✓, SMA_150 > SMA_200 ✓)
        SMA_200   = 110.0   (close > SMA_200 ✓)
        slope_200 = +0.10   (positive ✓)
        low_52w   = 100.0   → 25% above = 125.0; close 150 ≥ 125 ✓
        high_52w  = 170.0   → within 25% = 127.5; close 150 ≥ 127.5 ✓
        RS_rating = 80      (≥ 70 ✓)
    """
    data = {
        "close":        150.0,
        "SMA_50":       130.0,
        "SMA_150":      120.0,
        "SMA_200":      110.0,
        "MA_slope_200": 0.10,
        "low_52w":      100.0,
        "high_52w":     170.0,
        "RS_rating":    80.0,
    }
    data.update(overrides)
    return pd.Series(data)


# ─────────────────────────────────────────────────────────────────────────────
# Happy-path: all 8 conditions pass
# ─────────────────────────────────────────────────────────────────────────────

class TestAllConditionsPass:
    def test_passes_true(self):
        result = check_trend_template(_passing_row(), _default_config())
        assert result.passes is True

    def test_conditions_met_eight(self):
        result = check_trend_template(_passing_row(), _default_config())
        assert result.conditions_met == 8

    def test_all_condition_flags_true(self):
        result = check_trend_template(_passing_row(), _default_config())
        for key, val in result.conditions.items():
            assert val is True, f"Expected {key}=True, got False"

    def test_returns_trend_template_result(self):
        result = check_trend_template(_passing_row(), _default_config())
        assert isinstance(result, TrendTemplateResult)

    def test_details_populated_for_all_conditions(self):
        result = check_trend_template(_passing_row(), _default_config())
        for c in ("C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8"):
            assert c in result.details
            assert len(result.details[c]) > 0


# ─────────────────────────────────────────────────────────────────────────────
# C1 failures
# ─────────────────────────────────────────────────────────────────────────────

class TestC1Failures:
    def test_close_below_sma150_fails_c1(self):
        row = _passing_row(close=115.0)   # below SMA_150=120
        result = check_trend_template(row, _default_config())
        assert result.conditions["C1"] is False
        assert result.passes is False

    def test_close_below_sma200_fails_c1(self):
        row = _passing_row(close=105.0)   # below SMA_200=110
        result = check_trend_template(row, _default_config())
        assert result.conditions["C1"] is False
        assert result.passes is False


# ─────────────────────────────────────────────────────────────────────────────
# Individual condition failures
# ─────────────────────────────────────────────────────────────────────────────

class TestIndividualConditionFailures:
    def test_c2_fails_when_sma150_below_sma200(self):
        # SMA_150 < SMA_200
        row = _passing_row(SMA_150=105.0, SMA_200=110.0, close=155.0)
        result = check_trend_template(row, _default_config())
        assert result.conditions["C2"] is False

    def test_c3_fails_when_slope200_negative(self):
        row = _passing_row(MA_slope_200=-0.05)
        result = check_trend_template(row, _default_config())
        assert result.conditions["C3"] is False

    def test_c3_fails_when_slope200_exactly_zero(self):
        row = _passing_row(MA_slope_200=0.0)
        result = check_trend_template(row, _default_config())
        assert result.conditions["C3"] is False   # must be strictly > 0

    def test_c4_fails_when_sma50_below_sma150(self):
        # SMA_50 < SMA_150
        row = _passing_row(SMA_50=115.0, SMA_150=120.0)
        result = check_trend_template(row, _default_config())
        assert result.conditions["C4"] is False

    def test_c4_fails_when_sma50_below_sma200(self):
        # SMA_50 between SMA_200 and SMA_150
        row = _passing_row(SMA_50=112.0, SMA_150=120.0, SMA_200=110.0)
        result = check_trend_template(row, _default_config())
        assert result.conditions["C4"] is False

    def test_c5_fails_when_close_below_sma50(self):
        row = _passing_row(close=125.0, SMA_50=130.0)
        result = check_trend_template(row, _default_config())
        assert result.conditions["C5"] is False

    def test_c6_fails_when_close_not_25pct_above_low(self):
        # low_52w=100; 25% above = 125; close=120 → fails
        row = _passing_row(close=120.0, low_52w=100.0, high_52w=200.0)
        result = check_trend_template(row, _default_config())
        assert result.conditions["C6"] is False

    def test_c7_fails_when_close_far_below_high(self):
        # high_52w=200; within 25% threshold = 150; close=140 → fails
        row = _passing_row(close=140.0, high_52w=200.0, low_52w=80.0)
        result = check_trend_template(row, _default_config())
        assert result.conditions["C7"] is False

    def test_c8_fails_when_rs_below_threshold(self):
        row = _passing_row(RS_rating=65.0)
        result = check_trend_template(row, _default_config())
        assert result.conditions["C8"] is False

    def test_c8_passes_at_exactly_min_threshold(self):
        row = _passing_row(RS_rating=70.0)
        result = check_trend_template(row, _default_config())
        assert result.conditions["C8"] is True


# ─────────────────────────────────────────────────────────────────────────────
# conditions_met counting
# ─────────────────────────────────────────────────────────────────────────────

class TestConditionsMetCount:
    def test_zero_met_when_all_fail(self):
        # Craft a row that fails all 8
        row = _passing_row(
            close=50.0,           # below all MAs → C1, C2(no), C4(no), C5 fail
            SMA_50=200.0,
            SMA_150=190.0,
            SMA_200=195.0,        # SMA_150 < SMA_200 → C2 fails
            MA_slope_200=-0.1,    # C3 fails
            low_52w=100.0,        # 25% above = 125; close=50 → C6 fails
            high_52w=300.0,       # within 25% = 225; close=50 → C7 fails
            RS_rating=10.0,       # C8 fails
        )
        result = check_trend_template(row, _default_config())
        assert result.passes is False
        assert result.conditions_met < 8

    def test_partial_conditions_counted_correctly(self):
        # Make only C3 and C8 fail
        row = _passing_row(MA_slope_200=-0.01, RS_rating=50.0)
        result = check_trend_template(row, _default_config())
        assert result.conditions["C3"] is False
        assert result.conditions["C8"] is False
        assert result.conditions_met == 6
        assert result.passes is False


# ─────────────────────────────────────────────────────────────────────────────
# Detail strings
# ─────────────────────────────────────────────────────────────────────────────

class TestDetailStrings:
    def test_passing_condition_detail_contains_tick(self):
        result = check_trend_template(_passing_row(), _default_config())
        for c in result.conditions:
            if result.conditions[c]:
                assert "✓" in result.details[c], f"{c} detail missing ✓"

    def test_failing_condition_detail_contains_cross(self):
        row = _passing_row(MA_slope_200=-0.05)
        result = check_trend_template(row, _default_config())
        assert "✗" in result.details["C3"]

    def test_detail_contains_actual_values(self):
        result = check_trend_template(_passing_row(), _default_config())
        assert "150.00" in result.details["C1"]   # close value
        assert "120.00" in result.details["C1"]   # SMA_150 value


# ─────────────────────────────────────────────────────────────────────────────
# Fail-loud: missing / NaN columns
# ─────────────────────────────────────────────────────────────────────────────

class TestFailLoudMissingColumns:
    def test_missing_close_raises(self):
        row = _passing_row()
        row = row.drop("close")
        with pytest.raises(RuleEngineError, match="close"):
            check_trend_template(row, _default_config())

    def test_missing_high_52w_raises(self):
        row = _passing_row()
        row = row.drop("high_52w")
        with pytest.raises(RuleEngineError, match="high_52w"):
            check_trend_template(row, _default_config())

    def test_missing_low_52w_raises(self):
        row = _passing_row()
        row = row.drop("low_52w")
        with pytest.raises(RuleEngineError, match="low_52w"):
            check_trend_template(row, _default_config())

    def test_missing_rs_rating_raises(self):
        row = _passing_row()
        row = row.drop("RS_rating")
        with pytest.raises(RuleEngineError, match="RS_rating"):
            check_trend_template(row, _default_config())

    def test_nan_close_raises(self):
        row = _passing_row(close=float("nan"))
        with pytest.raises(RuleEngineError, match="close"):
            check_trend_template(row, _default_config())

    def test_nan_slope200_raises(self):
        row = _passing_row(MA_slope_200=float("nan"))
        with pytest.raises(RuleEngineError, match="MA_slope_200"):
            check_trend_template(row, _default_config())


# ─────────────────────────────────────────────────────────────────────────────
# Custom config thresholds
# ─────────────────────────────────────────────────────────────────────────────

class TestCustomThresholds:
    def test_stricter_rs_threshold_fails_c8(self):
        # RS_rating=80 passes default (70) but fails 85
        row = _passing_row(RS_rating=80.0)
        config = _default_config(min_rs_rating=85)
        result = check_trend_template(row, config)
        assert result.conditions["C8"] is False

    def test_looser_pct_above_low_passes_c6(self):
        # close=110, low_52w=100 → only 10% above; fails at 25% but passes at 5%
        row = _passing_row(close=110.0, low_52w=100.0, high_52w=200.0)
        config = _default_config(pct_above_52w_low=5.0)
        result = check_trend_template(row, config)
        assert result.conditions["C6"] is True

    def test_stricter_pct_below_high_fails_c7(self):
        # close=160, high_52w=170 → 5.9% below; passes 25% but fails at 2%
        row = _passing_row(close=160.0, high_52w=170.0, low_52w=80.0)
        config = _default_config(pct_below_52w_high=2.0)
        result = check_trend_template(row, config)
        assert result.conditions["C7"] is False
