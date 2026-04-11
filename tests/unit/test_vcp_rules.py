"""
tests/unit/test_vcp_rules.py
─────────────────────────────
Unit tests for rules/vcp_rules.py — VCPQualification, check_vcp(), and
get_vcp_score().

Test coverage
─────────────
  TestCheckVCPShortCircuit     — vcp_is_valid=False → always FAIL
  TestCheckVCPMaxContractions  — contraction_count > max → FAIL
  TestQualityGrading           — grade A / B / C / FAIL assignment logic
  TestGetVCPScore              — score ranges per grade, formula boundaries
  TestMissingColumns           — absent vcp_* column → RuleEngineError
  TestNaNColumns               — NaN vcp_* column → RuleEngineError
  TestVCPQualificationFields   — dataclass field accessibility
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from rules.vcp_rules import VCPQualification, check_vcp, get_vcp_score
from utils.exceptions import RuleEngineError


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _default_config(**overrides) -> dict:
    """Minimal config dict matching settings.yaml vcp section defaults."""
    vcp = {
        "detector": "rule_based",
        "min_contractions": 2,
        "max_contractions": 5,
        "require_declining_depth": True,
        "require_vol_contraction": True,
        "min_weeks": 3,
        "max_weeks": 52,
        "tightness_pct": 10.0,
        "max_depth_pct": 50.0,
    }
    vcp.update(overrides)
    return {"vcp": vcp}


def _valid_row(**overrides) -> pd.Series:
    """
    A pd.Series that represents a fully valid VCP feature row by default.

    Defaults produce a Grade B qualification:
        contraction_count = 2   (meets min; below Grade A threshold of 3)
        max_depth_pct     = 25.0
        final_depth_pct   = 6.0
        vol_ratio         = 0.6  (< 0.8, Grade B threshold)
        base_weeks        = 8
        vcp_is_valid      = True
        vcp_fail_reason   = None
    """
    data = {
        "vcp_contraction_count": 2.0,
        "vcp_max_depth_pct":     25.0,
        "vcp_final_depth_pct":   6.0,
        "vcp_vol_ratio":         0.6,
        "vcp_base_weeks":        8.0,
        "vcp_is_valid":          True,
        "vcp_fail_reason":       None,
    }
    data.update(overrides)
    return pd.Series(data)


def _grade_a_row(**overrides) -> pd.Series:
    """Row that should produce Grade A: cnt>=3, vol<0.5, final_depth<5."""
    data = {
        "vcp_contraction_count": 3.0,
        "vcp_max_depth_pct":     20.0,
        "vcp_final_depth_pct":   3.0,
        "vcp_vol_ratio":         0.3,
        "vcp_base_weeks":        10.0,
        "vcp_is_valid":          True,
        "vcp_fail_reason":       None,
    }
    data.update(overrides)
    return pd.Series(data)


# ─────────────────────────────────────────────────────────────────────────────
# Short-circuit: vcp_is_valid already False
# ─────────────────────────────────────────────────────────────────────────────

class TestCheckVCPShortCircuit:
    """When vcp_is_valid is False the feature layer already rejected the VCP."""

    def test_not_qualified_when_feature_layer_failed(self):
        row = _valid_row(vcp_is_valid=False, vcp_fail_reason="insufficient pivots")
        qual = check_vcp(row, _default_config())
        assert qual.qualified is False

    def test_grade_is_fail_when_feature_layer_failed(self):
        row = _valid_row(vcp_is_valid=False, vcp_fail_reason="insufficient pivots")
        qual = check_vcp(row, _default_config())
        assert qual.quality_grade == "FAIL"

    def test_fail_reason_preserved_from_feature_layer(self):
        reason = "contractions not declining in depth"
        row = _valid_row(vcp_is_valid=False, vcp_fail_reason=reason)
        qual = check_vcp(row, _default_config())
        assert qual.fail_reason == reason

    def test_metrics_still_populated_even_on_fail(self):
        row = _valid_row(vcp_is_valid=False, vcp_fail_reason="test")
        qual = check_vcp(row, _default_config())
        assert qual.contraction_count == 2
        assert qual.vol_ratio == pytest.approx(0.6)

    def test_pandas_na_is_valid_treated_as_false(self):
        row = _valid_row()
        row["vcp_is_valid"] = pd.NA
        qual = check_vcp(row, _default_config())
        assert qual.qualified is False


# ─────────────────────────────────────────────────────────────────────────────
# Rule-layer check: max_contractions
# ─────────────────────────────────────────────────────────────────────────────

class TestCheckVCPMaxContractions:
    def test_exceeding_max_contractions_fails(self):
        row = _valid_row(vcp_contraction_count=6.0)
        qual = check_vcp(row, _default_config(max_contractions=5))
        assert qual.qualified is False
        assert qual.quality_grade == "FAIL"

    def test_fail_reason_mentions_max_contractions(self):
        row = _valid_row(vcp_contraction_count=6.0)
        qual = check_vcp(row, _default_config(max_contractions=5))
        assert qual.fail_reason is not None
        assert "max_contractions" in qual.fail_reason

    def test_at_exactly_max_contractions_passes(self):
        row = _valid_row(vcp_contraction_count=5.0)
        qual = check_vcp(row, _default_config(max_contractions=5))
        assert qual.qualified is True

    def test_below_max_contractions_passes(self):
        row = _valid_row(vcp_contraction_count=3.0)
        qual = check_vcp(row, _default_config(max_contractions=5))
        assert qual.qualified is True

    def test_custom_max_contractions_respected(self):
        row = _valid_row(vcp_contraction_count=3.0)
        qual = check_vcp(row, _default_config(max_contractions=2))
        assert qual.qualified is False


# ─────────────────────────────────────────────────────────────────────────────
# Quality grading
# ─────────────────────────────────────────────────────────────────────────────

class TestQualityGrading:
    def test_grade_a_all_conditions_met(self):
        qual = check_vcp(_grade_a_row(), _default_config())
        assert qual.quality_grade == "A"
        assert qual.qualified is True

    def test_grade_a_requires_cnt_at_least_3(self):
        # cnt=2 → cannot be A
        row = _grade_a_row(vcp_contraction_count=2.0)
        qual = check_vcp(row, _default_config())
        assert qual.quality_grade != "A"

    def test_grade_a_requires_vol_ratio_below_0_5(self):
        row = _grade_a_row(vcp_vol_ratio=0.5)  # boundary: must be < 0.5
        qual = check_vcp(row, _default_config())
        assert qual.quality_grade != "A"

    def test_grade_a_requires_final_depth_below_5(self):
        row = _grade_a_row(vcp_final_depth_pct=5.0)  # boundary: must be < 5.0
        qual = check_vcp(row, _default_config())
        assert qual.quality_grade != "A"

    def test_grade_b_when_vol_ratio_under_0_8_but_not_a(self):
        # cnt=2 (not Grade A), vol=0.7 (< 0.8 → Grade B)
        row = _valid_row(vcp_vol_ratio=0.7)
        qual = check_vcp(row, _default_config())
        assert qual.quality_grade == "B"

    def test_grade_c_when_qualified_but_not_a_or_b(self):
        # cnt=2, vol=0.9 → qualified but vol >= 0.8 → Grade C
        row = _valid_row(vcp_vol_ratio=0.9)
        qual = check_vcp(row, _default_config())
        assert qual.quality_grade == "C"
        assert qual.qualified is True

    def test_fail_when_not_qualified(self):
        row = _valid_row(vcp_is_valid=False, vcp_fail_reason="test")
        qual = check_vcp(row, _default_config())
        assert qual.quality_grade == "FAIL"


# ─────────────────────────────────────────────────────────────────────────────
# get_vcp_score
# ─────────────────────────────────────────────────────────────────────────────

class TestGetVCPScore:
    def _make_qual(
        self,
        grade: str,
        contraction_count: int = 2,
        vol_ratio: float = 0.6,
        final_depth_pct: float = 6.0,
    ) -> VCPQualification:
        return VCPQualification(
            qualified=(grade != "FAIL"),
            contraction_count=contraction_count,
            max_depth_pct=20.0,
            final_depth_pct=final_depth_pct,
            vol_ratio=vol_ratio,
            base_weeks=8,
            fail_reason=None if grade != "FAIL" else "test",
            quality_grade=grade,
        )

    def test_fail_returns_zero(self):
        qual = self._make_qual("FAIL")
        assert get_vcp_score(qual) == pytest.approx(0.0)

    def test_grade_c_in_range_40_to_59(self):
        qual = self._make_qual("C", contraction_count=2, vol_ratio=0.9)
        score = get_vcp_score(qual)
        assert 40.0 <= score <= 59.0

    def test_grade_b_in_range_60_to_79(self):
        qual = self._make_qual("B", contraction_count=2, vol_ratio=0.7)
        score = get_vcp_score(qual)
        assert 60.0 <= score <= 79.0

    def test_grade_a_in_range_80_to_100(self):
        qual = self._make_qual("A", contraction_count=3, vol_ratio=0.3, final_depth_pct=3.0)
        score = get_vcp_score(qual)
        assert 80.0 <= score <= 100.0

    def test_grade_c_minimum_score_is_40(self):
        # Worst possible C: cnt=2, vol_ratio=1.0 (no contraction bonus, no vol bonus)
        qual = self._make_qual("C", contraction_count=2, vol_ratio=1.0)
        assert get_vcp_score(qual) == pytest.approx(40.0)

    def test_grade_c_capped_at_59(self):
        # Best possible C: many contractions + vol_ratio=0 → should not exceed 59
        qual = self._make_qual("C", contraction_count=10, vol_ratio=0.0)
        assert get_vcp_score(qual) <= 59.0

    def test_grade_b_minimum_score_is_60(self):
        # Minimum B: cnt=2, vol_ratio=0.8 → cnt_bonus=0, vol_bonus=max((0.2)*10,0)=2
        qual = self._make_qual("B", contraction_count=2, vol_ratio=0.8)
        score = get_vcp_score(qual)
        assert score >= 60.0

    def test_grade_b_capped_at_79(self):
        qual = self._make_qual("B", contraction_count=10, vol_ratio=0.0)
        assert get_vcp_score(qual) <= 79.0

    def test_grade_a_minimum_score_is_80(self):
        # Min A: cnt=3, vol_ratio exactly at 0.5 (boundary) → vol_bonus=0
        qual = self._make_qual("A", contraction_count=3, vol_ratio=0.499)
        score = get_vcp_score(qual)
        assert score >= 80.0

    def test_grade_a_capped_at_100(self):
        qual = self._make_qual("A", contraction_count=10, vol_ratio=0.0, final_depth_pct=1.0)
        assert get_vcp_score(qual) <= 100.0

    def test_higher_contraction_count_increases_score(self):
        q2 = self._make_qual("B", contraction_count=2, vol_ratio=0.7)
        q4 = self._make_qual("B", contraction_count=4, vol_ratio=0.7)
        assert get_vcp_score(q4) > get_vcp_score(q2)

    def test_lower_vol_ratio_increases_score_grade_b(self):
        q_high = self._make_qual("B", contraction_count=2, vol_ratio=0.75)
        q_low  = self._make_qual("B", contraction_count=2, vol_ratio=0.25)
        assert get_vcp_score(q_low) > get_vcp_score(q_high)


# ─────────────────────────────────────────────────────────────────────────────
# Fail-loud: missing / NaN columns
# ─────────────────────────────────────────────────────────────────────────────

class TestMissingColumns:
    @pytest.mark.parametrize("col", [
        "vcp_contraction_count",
        "vcp_max_depth_pct",
        "vcp_final_depth_pct",
        "vcp_vol_ratio",
        "vcp_base_weeks",
        "vcp_is_valid",
        "vcp_fail_reason",
    ])
    def test_missing_column_raises(self, col):
        row = _valid_row()
        row = row.drop(col)
        with pytest.raises(RuleEngineError, match=col):
            check_vcp(row, _default_config())


class TestNaNColumns:
    @pytest.mark.parametrize("col", [
        "vcp_contraction_count",
        "vcp_max_depth_pct",
        "vcp_final_depth_pct",
        "vcp_vol_ratio",
        "vcp_base_weeks",
    ])
    def test_nan_numeric_column_raises(self, col):
        row = _valid_row()
        row[col] = float("nan")
        with pytest.raises(RuleEngineError, match=col):
            check_vcp(row, _default_config())


# ─────────────────────────────────────────────────────────────────────────────
# VCPQualification dataclass sanity
# ─────────────────────────────────────────────────────────────────────────────

class TestVCPQualificationFields:
    def test_all_fields_accessible(self):
        q = VCPQualification(
            qualified=True,
            contraction_count=3,
            max_depth_pct=20.0,
            final_depth_pct=4.5,
            vol_ratio=0.4,
            base_weeks=9,
            fail_reason=None,
            quality_grade="A",
        )
        assert q.qualified is True
        assert q.contraction_count == 3
        assert q.max_depth_pct == pytest.approx(20.0)
        assert q.final_depth_pct == pytest.approx(4.5)
        assert q.vol_ratio == pytest.approx(0.4)
        assert q.base_weeks == 9
        assert q.fail_reason is None
        assert q.quality_grade == "A"

    def test_fail_qual_has_reason(self):
        q = VCPQualification(
            qualified=False,
            contraction_count=1,
            max_depth_pct=0.0,
            final_depth_pct=0.0,
            vol_ratio=1.0,
            base_weeks=0,
            fail_reason="insufficient pivots",
            quality_grade="FAIL",
        )
        assert q.qualified is False
        assert q.quality_grade == "FAIL"
        assert q.fail_reason == "insufficient pivots"

    def test_check_vcp_returns_vcp_qualification_instance(self):
        qual = check_vcp(_valid_row(), _default_config())
        assert isinstance(qual, VCPQualification)
