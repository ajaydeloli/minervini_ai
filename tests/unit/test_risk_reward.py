"""
tests/unit/test_risk_reward.py
──────────────────────────────
Unit tests for rules/risk_reward.py — RRResult and compute_rr().

Test coverage
─────────────
  TestPivotHighTarget       — last_pivot_high above entry is used as target
  TestFallbackTo52wHigh     — 52w high used when pivot is NaN or <= entry
  TestDefaultTarget         — synthetic target when no resistance exists
  TestZeroDenominator       — stop == entry → rr_ratio = 0.0
  TestMissingColumns        — absent column → RuleEngineError (fail-loud)
  TestFieldCalculations     — reward_pct / risk_pct / rr_ratio arithmetic
  TestConfigOverride        — custom default_target_pct respected
  TestRRResultDataclass     — dataclass field accessibility
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from rules.risk_reward import RRResult, compute_rr
from utils.exceptions import RuleEngineError


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _default_config(**overrides) -> dict:
    """Minimal config dict for risk_reward section."""
    rr = {"default_target_pct": 20.0}
    rr.update(overrides)
    return {"risk_reward": rr}


def _row(
    last_pivot_high=float("nan"),
    high_52w=float("nan"),
    **extra,
) -> pd.Series:
    """Build a synthetic feature row with the two required columns."""
    data = {
        "last_pivot_high": last_pivot_high,
        "high_52w": high_52w,
        **extra,
    }
    return pd.Series(data)


# ─────────────────────────────────────────────────────────────────────────────
# test_uses_pivot_high_when_above_entry
# ─────────────────────────────────────────────────────────────────────────────

class TestPivotHighTarget:
    """last_pivot_high above entry_price should be used as the target."""

    def test_uses_pivot_high_when_above_entry(self):
        row = _row(last_pivot_high=120.0, high_52w=130.0)
        result = compute_rr(row, entry_price=100.0, stop_loss=90.0, config=_default_config())
        assert result.target_price == pytest.approx(120.0)

    def test_has_resistance_true_with_pivot(self):
        row = _row(last_pivot_high=115.0, high_52w=125.0)
        result = compute_rr(row, entry_price=100.0, stop_loss=90.0, config=_default_config())
        assert result.has_resistance is True

    def test_pivot_preferred_over_52w(self):
        # Both are above entry; pivot should be chosen (priority 1)
        row = _row(last_pivot_high=110.0, high_52w=140.0)
        result = compute_rr(row, entry_price=100.0, stop_loss=90.0, config=_default_config())
        assert result.target_price == pytest.approx(110.0)

    def test_pivot_at_or_below_entry_not_used(self):
        # pivot == entry → not usable; should fall through to 52w high
        row = _row(last_pivot_high=100.0, high_52w=130.0)
        result = compute_rr(row, entry_price=100.0, stop_loss=90.0, config=_default_config())
        assert result.target_price == pytest.approx(130.0)

    def test_pivot_below_entry_falls_to_52w(self):
        row = _row(last_pivot_high=95.0, high_52w=125.0)
        result = compute_rr(row, entry_price=100.0, stop_loss=90.0, config=_default_config())
        assert result.target_price == pytest.approx(125.0)
        assert result.has_resistance is True


# ─────────────────────────────────────────────────────────────────────────────
# test_falls_back_to_52w_high
# ─────────────────────────────────────────────────────────────────────────────

class TestFallbackTo52wHigh:
    """52-week high is used when last_pivot_high is NaN or <= entry."""

    def test_falls_back_to_52w_high_when_pivot_nan(self):
        row = _row(last_pivot_high=float("nan"), high_52w=125.0)
        result = compute_rr(row, entry_price=100.0, stop_loss=90.0, config=_default_config())
        assert result.target_price == pytest.approx(125.0)

    def test_has_resistance_true_with_52w(self):
        row = _row(last_pivot_high=float("nan"), high_52w=125.0)
        result = compute_rr(row, entry_price=100.0, stop_loss=90.0, config=_default_config())
        assert result.has_resistance is True

    def test_falls_back_to_52w_when_pivot_below_entry(self):
        row = _row(last_pivot_high=90.0, high_52w=130.0)
        result = compute_rr(row, entry_price=100.0, stop_loss=92.0, config=_default_config())
        assert result.target_price == pytest.approx(130.0)

    def test_pandas_na_pivot_falls_to_52w(self):
        row = _row(high_52w=120.0)
        row["last_pivot_high"] = pd.NA
        result = compute_rr(row, entry_price=100.0, stop_loss=90.0, config=_default_config())
        assert result.target_price == pytest.approx(120.0)

    def test_52w_at_or_below_entry_also_falls_through(self):
        # Both pivot (NaN) and 52w (== entry) → synthetic target
        row = _row(last_pivot_high=float("nan"), high_52w=100.0)
        result = compute_rr(row, entry_price=100.0, stop_loss=90.0, config=_default_config())
        assert result.has_resistance is False
        assert result.target_price == pytest.approx(120.0)


# ─────────────────────────────────────────────────────────────────────────────
# test_default_target_when_no_resistance
# ─────────────────────────────────────────────────────────────────────────────

class TestDefaultTarget:
    """Synthetic target used when neither pivot nor 52w high is usable."""

    def test_default_target_when_no_resistance(self):
        row = _row(last_pivot_high=float("nan"), high_52w=float("nan"))
        result = compute_rr(row, entry_price=100.0, stop_loss=90.0, config=_default_config())
        assert result.target_price == pytest.approx(120.0)   # 100 * 1.20

    def test_has_resistance_false_when_no_resistance(self):
        row = _row(last_pivot_high=float("nan"), high_52w=float("nan"))
        result = compute_rr(row, entry_price=100.0, stop_loss=90.0, config=_default_config())
        assert result.has_resistance is False

    def test_custom_default_target_pct(self):
        row = _row(last_pivot_high=float("nan"), high_52w=float("nan"))
        result = compute_rr(
            row, entry_price=100.0, stop_loss=90.0,
            config=_default_config(default_target_pct=30.0),
        )
        assert result.target_price == pytest.approx(130.0)

    def test_no_config_section_uses_default(self):
        # config has no "risk_reward" key at all → must not raise
        row = _row(last_pivot_high=float("nan"), high_52w=float("nan"))
        result = compute_rr(row, entry_price=100.0, stop_loss=90.0, config={})
        assert result.target_price == pytest.approx(120.0)
        assert result.has_resistance is False

    def test_rr_ratio_computed_from_synthetic_target(self):
        # entry=100, stop=90, target=120 → rr = (120-100)/(100-90) = 2.0
        row = _row(last_pivot_high=float("nan"), high_52w=float("nan"))
        result = compute_rr(row, entry_price=100.0, stop_loss=90.0, config=_default_config())
        assert result.rr_ratio == pytest.approx(2.0)


# ─────────────────────────────────────────────────────────────────────────────
# test_zero_denominator_returns_zero_rr
# ─────────────────────────────────────────────────────────────────────────────

class TestZeroDenominator:
    """stop_loss == entry_price must return rr_ratio=0.0, not raise."""

    def test_zero_denominator_returns_zero_rr(self):
        row = _row(last_pivot_high=120.0)
        result = compute_rr(row, entry_price=100.0, stop_loss=100.0, config=_default_config())
        assert result.rr_ratio == pytest.approx(0.0)

    def test_zero_denominator_target_still_populated(self):
        row = _row(last_pivot_high=120.0)
        result = compute_rr(row, entry_price=100.0, stop_loss=100.0, config=_default_config())
        assert result.target_price == pytest.approx(120.0)

    def test_zero_denominator_risk_pct_is_zero(self):
        row = _row(last_pivot_high=120.0)
        result = compute_rr(row, entry_price=100.0, stop_loss=100.0, config=_default_config())
        assert result.risk_pct == pytest.approx(0.0)

    def test_zero_denominator_reward_pct_still_computed(self):
        row = _row(last_pivot_high=120.0)
        result = compute_rr(row, entry_price=100.0, stop_loss=100.0, config=_default_config())
        assert result.reward_pct == pytest.approx(20.0)


# ─────────────────────────────────────────────────────────────────────────────
# test_missing_both_columns_raises
# ─────────────────────────────────────────────────────────────────────────────

class TestMissingColumns:
    """Missing column in row index → RuleEngineError (fail-loud)."""

    def test_missing_both_columns_raises(self):
        row = pd.Series({"close": 100.0, "volume": 500_000.0})
        with pytest.raises(RuleEngineError):
            compute_rr(row, entry_price=100.0, stop_loss=90.0, config=_default_config())

    def test_missing_pivot_high_column_raises(self):
        # only high_52w present — last_pivot_high column absent entirely
        row = pd.Series({"high_52w": 120.0})
        with pytest.raises(RuleEngineError, match="last_pivot_high"):
            compute_rr(row, entry_price=100.0, stop_loss=90.0, config=_default_config())

    def test_missing_52w_high_column_raises(self):
        # only last_pivot_high present — high_52w column absent entirely
        row = pd.Series({"last_pivot_high": 110.0})
        with pytest.raises(RuleEngineError, match="high_52w"):
            compute_rr(row, entry_price=100.0, stop_loss=90.0, config=_default_config())

    def test_nan_pivot_does_not_raise(self):
        # NaN value is valid — column exists, just no swing high found
        row = _row(last_pivot_high=float("nan"), high_52w=float("nan"))
        result = compute_rr(row, entry_price=100.0, stop_loss=90.0, config=_default_config())
        assert isinstance(result, RRResult)

    def test_pandas_na_does_not_raise(self):
        row = _row(high_52w=float("nan"))
        row["last_pivot_high"] = pd.NA
        result = compute_rr(row, entry_price=100.0, stop_loss=90.0, config=_default_config())
        assert isinstance(result, RRResult)


# ─────────────────────────────────────────────────────────────────────────────
# Field calculation correctness
# ─────────────────────────────────────────────────────────────────────────────

class TestFieldCalculations:
    """Verify reward_pct / risk_pct / rr_ratio arithmetic."""

    def test_reward_pct(self):
        # entry=100, target=130 → reward_pct = 30.0
        row = _row(last_pivot_high=130.0)
        result = compute_rr(row, entry_price=100.0, stop_loss=92.0, config=_default_config())
        assert result.reward_pct == pytest.approx(30.0)

    def test_risk_pct(self):
        # entry=100, stop=92 → risk_pct = 8.0
        row = _row(last_pivot_high=130.0)
        result = compute_rr(row, entry_price=100.0, stop_loss=92.0, config=_default_config())
        assert result.risk_pct == pytest.approx(8.0)

    def test_rr_ratio_arithmetic(self):
        # entry=100, stop=92, target=130 → rr = 30/8 = 3.75
        row = _row(last_pivot_high=130.0)
        result = compute_rr(row, entry_price=100.0, stop_loss=92.0, config=_default_config())
        assert result.rr_ratio == pytest.approx(3.75)

    def test_rr_ratio_less_than_one(self):
        # entry=100, stop=95, target=105 → rr = 5/5 = 1.0
        row = _row(last_pivot_high=105.0)
        result = compute_rr(row, entry_price=100.0, stop_loss=95.0, config=_default_config())
        assert result.rr_ratio == pytest.approx(1.0)


# ─────────────────────────────────────────────────────────────────────────────
# RRResult dataclass sanity
# ─────────────────────────────────────────────────────────────────────────────

class TestRRResultDataclass:
    def test_all_fields_accessible(self):
        r = RRResult(
            target_price=120.0,
            rr_ratio=2.0,
            reward_pct=20.0,
            risk_pct=10.0,
            has_resistance=True,
        )
        assert r.target_price == pytest.approx(120.0)
        assert r.rr_ratio == pytest.approx(2.0)
        assert r.reward_pct == pytest.approx(20.0)
        assert r.risk_pct == pytest.approx(10.0)
        assert r.has_resistance is True

    def test_compute_rr_returns_rrresult_instance(self):
        row = _row(last_pivot_high=120.0)
        result = compute_rr(row, entry_price=100.0, stop_loss=90.0, config=_default_config())
        assert isinstance(result, RRResult)
