"""
tests/unit/test_entry_trigger.py
─────────────────────────────────
Unit tests for rules/entry_trigger.py — EntryTrigger and check_entry_trigger().

Test coverage
─────────────
  TestNoPivotAvailable         — last_pivot_high is NaN → graceful non-triggered
  TestPriceBreakout            — close vs pivot_high conditions
  TestVolumeConfirmation       — volume ratio threshold logic
  TestFullBreakout             — both conditions met → triggered=True
  TestReasonStrings            — reason text sanity checks
  TestVolRatioField            — breakout_vol_ratio calculation
  TestMissingColumns           — absent mandatory column → RuleEngineError
  TestNaNMandatoryColumns      — NaN mandatory column → RuleEngineError
  TestMissingPivotColumn       — absent last_pivot_high column → RuleEngineError
  TestConfigOverrides          — breakout_vol_multiplier / pivot_lookback_days
  TestEntryTriggerDataclass    — dataclass field accessibility
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from rules.entry_trigger import EntryTrigger, check_entry_trigger
from utils.exceptions import RuleEngineError


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _default_config(**overrides) -> dict:
    """Minimal config dict matching entry section defaults."""
    entry = {
        "breakout_vol_multiplier": 1.5,
        "pivot_lookback_days": 60,
    }
    entry.update(overrides)
    return {"entry": entry}


def _valid_row(**overrides) -> pd.Series:
    """
    A pd.Series representing a fully valid feature row that triggers a breakout.

    Defaults:
        close           = 150.0   (> last_pivot_high 140.0)
        volume          = 1_500_000
        vol_50d_avg     = 1_000_000
        last_pivot_high = 140.0
        last_pivot_low  =  90.0
        vcp_is_valid    = True
        vcp_final_depth_pct = 8.0

    vol_ratio = 1.5x → exactly meets default threshold (>= is True).
    """
    data = {
        "close":              150.0,
        "volume":             1_500_000.0,
        "vol_50d_avg":        1_000_000.0,
        "last_pivot_high":    140.0,
        "last_pivot_low":      90.0,
        "vcp_is_valid":       True,
        "vcp_final_depth_pct": 8.0,
    }
    data.update(overrides)
    return pd.Series(data)


# ─────────────────────────────────────────────────────────────────────────────
# No pivot available
# ─────────────────────────────────────────────────────────────────────────────

class TestNoPivotAvailable:
    """When last_pivot_high is NaN the result is non-triggered, not an error."""

    def test_nan_pivot_not_triggered(self):
        row = _valid_row(last_pivot_high=float("nan"))
        result = check_entry_trigger(row, _default_config())
        assert result.triggered is False

    def test_nan_pivot_entry_price_is_none(self):
        row = _valid_row(last_pivot_high=float("nan"))
        result = check_entry_trigger(row, _default_config())
        assert result.entry_price is None

    def test_nan_pivot_pivot_high_is_none(self):
        row = _valid_row(last_pivot_high=float("nan"))
        result = check_entry_trigger(row, _default_config())
        assert result.pivot_high is None

    def test_nan_pivot_vol_ratio_is_none(self):
        row = _valid_row(last_pivot_high=float("nan"))
        result = check_entry_trigger(row, _default_config())
        assert result.breakout_vol_ratio is None

    def test_nan_pivot_volume_confirmed_false(self):
        row = _valid_row(last_pivot_high=float("nan"))
        result = check_entry_trigger(row, _default_config())
        assert result.volume_confirmed is False

    def test_nan_pivot_reason_mentions_no_pivot(self):
        row = _valid_row(last_pivot_high=float("nan"))
        result = check_entry_trigger(row, _default_config())
        assert "no pivot high available" in result.reason

    def test_nan_pivot_reason_mentions_lookback(self):
        row = _valid_row(last_pivot_high=float("nan"))
        result = check_entry_trigger(row, _default_config(pivot_lookback_days=90))
        assert "90" in result.reason

    def test_pandas_na_pivot_not_triggered(self):
        row = _valid_row()
        row["last_pivot_high"] = pd.NA
        result = check_entry_trigger(row, _default_config())
        assert result.triggered is False

    def test_none_pivot_not_triggered(self):
        row = _valid_row()
        row["last_pivot_high"] = None
        result = check_entry_trigger(row, _default_config())
        assert result.triggered is False


# ─────────────────────────────────────────────────────────────────────────────
# Price breakout condition
# ─────────────────────────────────────────────────────────────────────────────

class TestPriceBreakout:
    def test_close_above_pivot_with_vol_triggers(self):
        # close 150 > pivot 140, vol 1.5x (meets threshold) → triggered
        row = _valid_row(close=150.0, last_pivot_high=140.0, volume=1_500_000.0)
        result = check_entry_trigger(row, _default_config())
        assert result.triggered is True

    def test_close_equal_to_pivot_does_not_trigger(self):
        # close == pivot_high is NOT a breakout (must be strictly greater)
        row = _valid_row(close=140.0, last_pivot_high=140.0, volume=2_000_000.0)
        result = check_entry_trigger(row, _default_config())
        assert result.triggered is False

    def test_close_below_pivot_does_not_trigger(self):
        row = _valid_row(close=135.0, last_pivot_high=140.0, volume=2_000_000.0)
        result = check_entry_trigger(row, _default_config())
        assert result.triggered is False

    def test_pivot_high_populated_on_non_trigger(self):
        row = _valid_row(close=135.0, last_pivot_high=140.0)
        result = check_entry_trigger(row, _default_config())
        assert result.pivot_high == pytest.approx(140.0)

    def test_pivot_high_populated_on_trigger(self):
        row = _valid_row(close=150.0, last_pivot_high=140.0)
        result = check_entry_trigger(row, _default_config())
        assert result.pivot_high == pytest.approx(140.0)


# ─────────────────────────────────────────────────────────────────────────────
# Volume confirmation condition
# ─────────────────────────────────────────────────────────────────────────────

class TestVolumeConfirmation:
    def test_vol_exactly_at_threshold_confirms(self):
        # volume = vol_50d_avg * 1.5 exactly → confirmed (>= is inclusive)
        row = _valid_row(volume=1_500_000.0, vol_50d_avg=1_000_000.0)
        result = check_entry_trigger(row, _default_config())
        assert result.volume_confirmed is True

    def test_vol_above_threshold_confirms(self):
        row = _valid_row(volume=2_000_000.0, vol_50d_avg=1_000_000.0)
        result = check_entry_trigger(row, _default_config())
        assert result.volume_confirmed is True

    def test_vol_below_threshold_does_not_confirm(self):
        row = _valid_row(volume=1_499_999.0, vol_50d_avg=1_000_000.0)
        result = check_entry_trigger(row, _default_config())
        assert result.volume_confirmed is False

    def test_price_breakout_without_vol_not_triggered(self):
        # close > pivot but vol < 1.5x → triggered must be False
        row = _valid_row(
            close=150.0, last_pivot_high=140.0,
            volume=1_000_000.0, vol_50d_avg=1_000_000.0,
        )
        result = check_entry_trigger(row, _default_config())
        assert result.triggered is False
        assert result.volume_confirmed is False

    def test_price_breakout_without_vol_volume_confirmed_false(self):
        row = _valid_row(
            close=150.0, last_pivot_high=140.0,
            volume=1_200_000.0, vol_50d_avg=1_000_000.0,
        )
        result = check_entry_trigger(row, _default_config())
        assert result.volume_confirmed is False


# ─────────────────────────────────────────────────────────────────────────────
# Full breakout (both conditions met)
# ─────────────────────────────────────────────────────────────────────────────

class TestFullBreakout:
    def test_triggered_true(self):
        row = _valid_row()
        result = check_entry_trigger(row, _default_config())
        assert result.triggered is True

    def test_entry_price_is_close(self):
        row = _valid_row(close=152.5)
        result = check_entry_trigger(row, _default_config())
        assert result.entry_price == pytest.approx(152.5)

    def test_entry_price_none_when_not_triggered(self):
        row = _valid_row(close=130.0, last_pivot_high=140.0)
        result = check_entry_trigger(row, _default_config())
        assert result.entry_price is None

    def test_volume_confirmed_true_on_full_breakout(self):
        row = _valid_row()
        result = check_entry_trigger(row, _default_config())
        assert result.volume_confirmed is True


# ─────────────────────────────────────────────────────────────────────────────
# Reason strings
# ─────────────────────────────────────────────────────────────────────────────

class TestReasonStrings:
    def test_triggered_reason_contains_breakout(self):
        row = _valid_row()
        result = check_entry_trigger(row, _default_config())
        assert "breakout" in result.reason

    def test_triggered_reason_contains_pivot_price(self):
        row = _valid_row(last_pivot_high=140.0)
        result = check_entry_trigger(row, _default_config())
        assert "140.00" in result.reason

    def test_triggered_reason_contains_vol_ratio(self):
        # vol_ratio = 1 500 000 / 1 000 000 = 1.5x
        row = _valid_row(volume=1_500_000.0, vol_50d_avg=1_000_000.0)
        result = check_entry_trigger(row, _default_config())
        assert "1.5x" in result.reason

    def test_price_miss_reason_contains_no_breakout(self):
        row = _valid_row(close=135.0, last_pivot_high=140.0)
        result = check_entry_trigger(row, _default_config())
        assert "no breakout" in result.reason

    def test_price_miss_reason_contains_close_and_pivot(self):
        row = _valid_row(close=138.0, last_pivot_high=142.0)
        result = check_entry_trigger(row, _default_config())
        assert "138.00" in result.reason
        assert "142.00" in result.reason

    def test_vol_miss_reason_mentions_required_multiplier(self):
        # Price breaks out but volume is weak
        row = _valid_row(
            close=150.0, last_pivot_high=140.0,
            volume=1_200_000.0, vol_50d_avg=1_000_000.0,
        )
        result = check_entry_trigger(row, _default_config())
        assert "no breakout" in result.reason
        assert "1.5x" in result.reason  # required multiplier in reason


# ─────────────────────────────────────────────────────────────────────────────
# breakout_vol_ratio field
# ─────────────────────────────────────────────────────────────────────────────

class TestVolRatioField:
    def test_vol_ratio_calculated_correctly(self):
        row = _valid_row(volume=2_300_000.0, vol_50d_avg=1_000_000.0)
        result = check_entry_trigger(row, _default_config())
        assert result.breakout_vol_ratio == pytest.approx(2.3)

    def test_vol_ratio_present_even_when_not_triggered_price(self):
        # Price did not break; vol ratio should still be calculated
        row = _valid_row(close=135.0, last_pivot_high=140.0, volume=2_000_000.0)
        result = check_entry_trigger(row, _default_config())
        assert result.breakout_vol_ratio == pytest.approx(2.0)

    def test_vol_ratio_present_even_when_not_triggered_vol(self):
        row = _valid_row(
            close=150.0, last_pivot_high=140.0,
            volume=1_200_000.0, vol_50d_avg=1_000_000.0,
        )
        result = check_entry_trigger(row, _default_config())
        assert result.breakout_vol_ratio == pytest.approx(1.2)


# ─────────────────────────────────────────────────────────────────────────────
# Config overrides
# ─────────────────────────────────────────────────────────────────────────────

class TestConfigOverrides:
    def test_custom_vol_multiplier_respected(self):
        # vol_ratio = 1.5x; with multiplier=2.0 → NOT confirmed
        row = _valid_row(volume=1_500_000.0, vol_50d_avg=1_000_000.0)
        result = check_entry_trigger(row, _default_config(breakout_vol_multiplier=2.0))
        assert result.volume_confirmed is False
        assert result.triggered is False

    def test_lower_vol_multiplier_confirms(self):
        # vol_ratio = 1.2x; with multiplier=1.1 → confirmed
        row = _valid_row(volume=1_200_000.0, vol_50d_avg=1_000_000.0)
        result = check_entry_trigger(row, _default_config(breakout_vol_multiplier=1.1))
        assert result.volume_confirmed is True
        assert result.triggered is True

    def test_empty_entry_config_uses_defaults(self):
        # config has no "entry" key at all → must not raise
        row = _valid_row()
        result = check_entry_trigger(row, {})
        assert isinstance(result, EntryTrigger)

    def test_lookback_days_surfaced_in_no_pivot_reason(self):
        row = _valid_row(last_pivot_high=float("nan"))
        result = check_entry_trigger(row, _default_config(pivot_lookback_days=45))
        assert "45" in result.reason


# ─────────────────────────────────────────────────────────────────────────────
# Fail-loud: missing / NaN mandatory columns
# ─────────────────────────────────────────────────────────────────────────────

class TestMissingColumns:
    @pytest.mark.parametrize("col", ["close", "volume", "vol_50d_avg", "last_pivot_high"])
    def test_missing_column_raises(self, col):
        row = _valid_row()
        row = row.drop(col)
        with pytest.raises(RuleEngineError, match=col):
            check_entry_trigger(row, _default_config())


class TestNaNMandatoryColumns:
    @pytest.mark.parametrize("col", ["close", "volume", "vol_50d_avg"])
    def test_nan_mandatory_column_raises(self, col):
        row = _valid_row()
        row[col] = float("nan")
        with pytest.raises(RuleEngineError, match=col):
            check_entry_trigger(row, _default_config())

    def test_nan_last_pivot_high_does_not_raise(self):
        # NaN pivot is valid — graceful non-triggered result
        row = _valid_row(last_pivot_high=float("nan"))
        result = check_entry_trigger(row, _default_config())
        assert result.triggered is False


# ─────────────────────────────────────────────────────────────────────────────
# EntryTrigger dataclass sanity
# ─────────────────────────────────────────────────────────────────────────────

class TestEntryTriggerDataclass:
    def test_all_fields_accessible_triggered(self):
        t = EntryTrigger(
            triggered=True,
            entry_price=152.5,
            pivot_high=140.0,
            breakout_vol_ratio=2.3,
            volume_confirmed=True,
            reason="breakout above pivot 140.00 on 2.3x avg vol",
        )
        assert t.triggered is True
        assert t.entry_price == pytest.approx(152.5)
        assert t.pivot_high == pytest.approx(140.0)
        assert t.breakout_vol_ratio == pytest.approx(2.3)
        assert t.volume_confirmed is True
        assert "breakout" in t.reason

    def test_all_fields_accessible_not_triggered(self):
        t = EntryTrigger(
            triggered=False,
            entry_price=None,
            pivot_high=140.0,
            breakout_vol_ratio=0.9,
            volume_confirmed=False,
            reason="no breakout: close 135.00 < pivot 140.00 (vol 0.9x)",
        )
        assert t.triggered is False
        assert t.entry_price is None
        assert t.pivot_high == pytest.approx(140.0)

    def test_check_entry_trigger_returns_entry_trigger_instance(self):
        result = check_entry_trigger(_valid_row(), _default_config())
        assert isinstance(result, EntryTrigger)
