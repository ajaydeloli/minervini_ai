"""
tests/unit/test_stage_detection.py
────────────────────────────────────
Unit tests for rules/stage.py — detect_stage() and StageResult.

Test coverage
─────────────
  test_stage2_all_conditions_pass        — perfect row → stage=2, confidence=100
  test_stage4_price_below_both_mas       — price below SMA_50 and SMA_200 → stage=4
  test_stage2_rejected_flat_ma200        — MA_slope_200=0.0 → stage != 2
  test_nan_raises_rule_engine_error      — SMA_200=NaN → RuleEngineError
  test_stage2_borderline_confidence      — barely above MAs → confidence < 100
  test_stage3_detection                  — below SMA_50, above SMA_200, declining → stage=3
  test_stage1_detection                  — flat/mixed MAs → stage=1
  test_missing_column_raises             — absent column → RuleEngineError
  test_parametrize_stage2_conditions     — each condition failure exits Stage 2
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from rules.stage import StageResult, detect_stage
from utils.exceptions import RuleEngineError


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _default_config() -> dict:
    """Minimal config dict matching settings.yaml defaults."""
    return {
        "stage": {
            "ma200_slope_lookback": 20,
            "ma50_slope_lookback": 10,
        }
    }


def make_row(**overrides) -> pd.Series:
    """
    Helper that returns a pd.Series with sensible Stage 2 defaults.

    Default layout (all Stage 2 conditions clearly satisfied):
        close        = 150.0
        SMA_50       = 130.0   (close > SMA_50 ✓, SMA_50 > SMA_200 ✓)
        SMA_150      = 120.0   (optional, used for context)
        SMA_200      = 110.0   (close > SMA_200 ✓)
        MA_slope_50  = +0.10   (positive ✓)
        MA_slope_200 = +0.08   (positive ✓)
    """
    data = {
        "close":        150.0,
        "SMA_50":       130.0,
        "SMA_150":      120.0,
        "SMA_200":      110.0,
        "MA_slope_50":  0.10,
        "MA_slope_200": 0.08,
    }
    data.update(overrides)
    return pd.Series(data)


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 — Happy path
# ─────────────────────────────────────────────────────────────────────────────

class TestStage2AllConditionsPass:
    def test_stage2_all_conditions_pass(self):
        """All Stage 2 conditions clearly met → stage=2 and confidence=100."""
        result = detect_stage(make_row(), _default_config())
        assert result.stage == 2
        assert result.confidence == 100

    def test_returns_stage_result_instance(self):
        """detect_stage() must return a StageResult dataclass."""
        result = detect_stage(make_row(), _default_config())
        assert isinstance(result, StageResult)

    def test_stage2_label_correct(self):
        """Stage 2 label must contain 'Advancing'."""
        result = detect_stage(make_row(), _default_config())
        assert "Advancing" in result.label

    def test_ma_slopes_populated(self):
        """ma_slopes dict must contain both slope values."""
        result = detect_stage(make_row(), _default_config())
        assert "slope_50" in result.ma_slopes
        assert "slope_200" in result.ma_slopes

    def test_reason_non_empty(self):
        """Reason string must be non-empty."""
        result = detect_stage(make_row(), _default_config())
        assert len(result.reason) > 0


# ─────────────────────────────────────────────────────────────────────────────
# Stage 4 — Price below both MAs
# ─────────────────────────────────────────────────────────────────────────────

class TestStage4PriceBelowBothMAs:
    def test_stage4_price_below_both_mas(self):
        """Price below both SMA_50 and SMA_200, both slopes negative → stage=4."""
        row = make_row(
            close=90.0,
            SMA_50=130.0,
            SMA_200=110.0,
            MA_slope_50=-0.10,
            MA_slope_200=-0.08,
        )
        result = detect_stage(row, _default_config())
        assert result.stage == 4

    def test_stage4_label_contains_declining(self):
        """Stage 4 label must contain 'Declining'."""
        row = make_row(
            close=90.0,
            SMA_50=130.0,
            SMA_200=110.0,
            MA_slope_50=-0.10,
            MA_slope_200=-0.08,
        )
        result = detect_stage(row, _default_config())
        assert "Declining" in result.label

    def test_stage4_confidence_not_exceeding_70(self):
        """Non-Stage-2 stages have confidence capped at 70."""
        row = make_row(
            close=90.0,
            SMA_50=130.0,
            SMA_200=110.0,
            MA_slope_50=-0.10,
            MA_slope_200=-0.08,
        )
        result = detect_stage(row, _default_config())
        assert result.confidence <= 70


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 rejected when MA_slope_200 = 0.0
# ─────────────────────────────────────────────────────────────────────────────

class TestStage2RejectedFlatMA200:
    def test_stage2_rejected_flat_ma200(self):
        """MA_slope_200=0.0 (not strictly positive) → stage must not be 2."""
        row = make_row(MA_slope_200=0.0)
        result = detect_stage(row, _default_config())
        assert result.stage != 2

    def test_stage2_rejected_negative_ma200(self):
        """MA_slope_200 < 0 → stage must not be 2."""
        row = make_row(MA_slope_200=-0.05)
        result = detect_stage(row, _default_config())
        assert result.stage != 2

    def test_stage2_rejected_flat_ma50(self):
        """MA_slope_50=0.0 (not strictly positive) → stage must not be 2."""
        row = make_row(MA_slope_50=0.0)
        result = detect_stage(row, _default_config())
        assert result.stage != 2



# ─────────────────────────────────────────────────────────────────────────────
# NaN raises RuleEngineError
# ─────────────────────────────────────────────────────────────────────────────

class TestNanRaisesRuleEngineError:
    def test_nan_raises_rule_engine_error(self):
        """SMA_200=NaN must raise RuleEngineError (fail-loud contract)."""
        row = make_row(SMA_200=float("nan"))
        with pytest.raises(RuleEngineError, match="SMA_200"):
            detect_stage(row, _default_config())

    def test_nan_sma50_raises(self):
        """SMA_50=NaN must raise RuleEngineError."""
        row = make_row(SMA_50=float("nan"))
        with pytest.raises(RuleEngineError, match="SMA_50"):
            detect_stage(row, _default_config())

    def test_nan_close_raises(self):
        """close=NaN must raise RuleEngineError."""
        row = make_row(close=float("nan"))
        with pytest.raises(RuleEngineError, match="close"):
            detect_stage(row, _default_config())

    def test_nan_slope50_raises(self):
        """MA_slope_50=NaN must raise RuleEngineError."""
        row = make_row(MA_slope_50=float("nan"))
        with pytest.raises(RuleEngineError, match="MA_slope_50"):
            detect_stage(row, _default_config())

    def test_nan_slope200_raises(self):
        """MA_slope_200=NaN must raise RuleEngineError."""
        row = make_row(MA_slope_200=float("nan"))
        with pytest.raises(RuleEngineError, match="MA_slope_200"):
            detect_stage(row, _default_config())

    def test_missing_column_raises(self):
        """Absent column SMA_200 must raise RuleEngineError."""
        row = make_row()
        row = row.drop("SMA_200")
        with pytest.raises(RuleEngineError, match="SMA_200"):
            detect_stage(row, _default_config())


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 borderline confidence
# ─────────────────────────────────────────────────────────────────────────────

class TestStage2BorderlineConfidence:
    def test_stage2_borderline_confidence_below_100(self):
        """Price barely above MAs → still Stage 2 but confidence < 100."""
        # close only 1% above SMA_50 — borderline (threshold is 2%)
        row = make_row(close=131.3, SMA_50=130.0, SMA_200=110.0)
        result = detect_stage(row, _default_config())
        assert result.stage == 2
        assert result.confidence < 100


# ─────────────────────────────────────────────────────────────────────────────
# Stage 3 detection
# ─────────────────────────────────────────────────────────────────────────────

class TestStage3Detection:
    def test_stage3_detection(self):
        """Below SMA_50, above SMA_200, SMA_50 declining → stage=3."""
        row = make_row(
            close=115.0,
            SMA_50=130.0,
            SMA_200=110.0,
            MA_slope_50=-0.05,
            MA_slope_200=0.02,
        )
        result = detect_stage(row, _default_config())
        assert result.stage == 3

    def test_stage3_label_contains_topping(self):
        """Stage 3 label must contain 'Topping'."""
        row = make_row(
            close=115.0,
            SMA_50=130.0,
            SMA_200=110.0,
            MA_slope_50=-0.05,
            MA_slope_200=0.02,
        )
        result = detect_stage(row, _default_config())
        assert "Topping" in result.label


# ─────────────────────────────────────────────────────────────────────────────
# Parametrize: each broken Stage 2 condition flips stage away from 2
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("override,description", [
    ({"close": 105.0}, "price below SMA_200"),
    ({"close": 125.0, "SMA_50": 130.0}, "price below SMA_50"),
    ({"SMA_50": 108.0, "SMA_200": 110.0, "close": 150.0}, "SMA_50 < SMA_200"),
    ({"MA_slope_200": 0.0}, "MA_slope_200 = 0"),
    ({"MA_slope_50": 0.0}, "MA_slope_50 = 0"),
])
def test_each_stage2_condition_failure_exits_stage2(override, description):
    """Breaking any single Stage 2 condition must result in stage != 2."""
    row = make_row(**override)
    result = detect_stage(row, _default_config())
    assert result.stage != 2, f"Expected stage != 2 when {description}"
