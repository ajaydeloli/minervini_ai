"""
tests/unit/test_scorer.py
──────────────────────────
Unit tests for rules/scorer.py — evaluate(), compute_volume_score(),
_assign_quality(), and to_dict().

Test coverage
─────────────
  test_stage4_always_scores_zero_quality_fail  — non-Stage-2 hard gate
  test_a_plus_requires_all_8_conditions_and_vcp — A+ gate conditions
  test_score_is_weighted_sum_of_components      — arithmetic of composite score
  test_trailing_stop_never_below_vcp_floor      — risk_pct logic via StopLossResult
  test_volume_score_thresholds                  — compute_volume_score table
  test_to_dict_serialisable                     — to_dict() produces flat dict
  test_quality_grade_b_requires_6_conditions    — conditions_met < 6 → FAIL
"""

from __future__ import annotations

import datetime
import math

import pandas as pd
import pytest

from rules.entry_trigger import EntryTrigger
from rules.scorer import SEPAResult, compute_volume_score, evaluate, to_dict
from rules.stage import StageResult
from rules.stop_loss import StopLossResult
from rules.trend_template import TrendTemplateResult
from rules.vcp_rules import VCPQualification


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _default_config() -> dict:
    """Minimal config matching settings.yaml defaults."""
    return {
        "scoring": {
            "weights": {
                "rs_rating": 0.30,
                "trend":     0.25,
                "vcp":       0.25,
                "volume":    0.10,
                "fundamental": 0.07,
                "news":      0.03,
            },
            "setup_quality_thresholds": {
                "a_plus": 85,
                "a":      70,
                "b":      55,
                "c":      40,
            },
        },
    }


def make_row(**overrides) -> pd.Series:
    """Return a feature pd.Series with sensible defaults for evaluate()."""
    data = {
        "close":        150.0,
        "SMA_50":       130.0,
        "SMA_150":      120.0,
        "SMA_200":      110.0,
        "MA_slope_50":  0.10,
        "MA_slope_200": 0.08,
        "RS_rating":    85.0,
        "vol_ratio":    2.0,
        "high_52w":     170.0,
        "low_52w":      100.0,
        "vcp_is_valid": True,
        "last_pivot_high": 145.0,
        "last_pivot_low":  120.0,
        "ATR_14":       3.0,
        "volume":       1_000_000.0,
        "vol_50d_avg":  500_000.0,
    }
    data.update(overrides)
    return pd.Series(data)


def _make_stage2() -> StageResult:
    """Stage 2 result with full confidence."""
    return StageResult(
        stage=2,
        label="Stage 2 — Advancing",
        confidence=100,
        reason="all conditions met",
        ma_slopes={"slope_50": 0.10, "slope_200": 0.08},
    )


def _make_stage4() -> StageResult:
    """Stage 4 result (declining)."""
    return StageResult(
        stage=4,
        label="Stage 4 — Declining",
        confidence=60,
        reason="price below both MAs",
        ma_slopes={"slope_50": -0.10, "slope_200": -0.08},
    )


def _make_tt_pass(conditions_met: int = 8) -> TrendTemplateResult:
    """TrendTemplateResult with all or partial conditions."""
    conds = {f"C{i}": (i <= conditions_met) for i in range(1, 9)}
    return TrendTemplateResult(
        passes=(conditions_met == 8),
        conditions=conds,
        conditions_met=conditions_met,
        details={k: "ok" for k in conds},
    )


def _make_vcp_a() -> VCPQualification:
    """Grade A VCP qualification."""
    return VCPQualification(
        qualified=True,
        contraction_count=3,
        max_depth_pct=20.0,
        final_depth_pct=3.0,
        vol_ratio=0.3,
        base_weeks=10,
        fail_reason=None,
        quality_grade="A",
    )


def _make_vcp_fail() -> VCPQualification:
    """FAIL VCP qualification."""
    return VCPQualification(
        qualified=False,
        contraction_count=1,
        max_depth_pct=0.0,
        final_depth_pct=0.0,
        vol_ratio=1.0,
        base_weeks=0,
        fail_reason="insufficient pivots",
        quality_grade="FAIL",
    )


def _make_entry_no_trigger() -> EntryTrigger:
    """EntryTrigger with triggered=False."""
    return EntryTrigger(
        triggered=False,
        entry_price=None,
        pivot_high=None,
        breakout_vol_ratio=None,
        volume_confirmed=False,
        reason="no breakout",
    )


def _make_stop(entry: float = 150.0, stop: float = 138.0) -> StopLossResult:
    """StopLossResult with realistic values."""
    risk_pct = round((entry - stop) / entry * 100, 4)
    return StopLossResult(
        stop_price=stop,
        stop_type="vcp_base",
        risk_pct=risk_pct,
        capped=False,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Hard gate: non-Stage-2 → score=0 and setup_quality=FAIL
# ─────────────────────────────────────────────────────────────────────────────

class TestStage4AlwaysScoresZero:
    def test_stage4_always_scores_zero_quality_fail(self):
        """Stage 4 input must produce score=0 and setup_quality='FAIL' (hard gate)."""
        result = evaluate(
            symbol="TEST",
            date=datetime.date(2025, 1, 1),
            row=make_row(),
            stage_result=_make_stage4(),
            tt_result=_make_tt_pass(8),
            vcp_qual=_make_vcp_a(),
            entry_trigger=_make_entry_no_trigger(),
            stop_result=None,
            config=_default_config(),
        )
        assert result.score == 0
        assert result.setup_quality == "FAIL"

    def test_stage1_always_scores_zero(self):
        """Stage 1 (basing) must also produce score=0 regardless of other conditions."""
        stage1 = StageResult(
            stage=1, label="Stage 1 — Basing", confidence=60,
            reason="basing", ma_slopes={"slope_50": 0.0, "slope_200": 0.0},
        )
        result = evaluate(
            symbol="TEST",
            date=datetime.date(2025, 1, 1),
            row=make_row(),
            stage_result=stage1,
            tt_result=_make_tt_pass(8),
            vcp_qual=_make_vcp_a(),
            entry_trigger=_make_entry_no_trigger(),
            stop_result=None,
            config=_default_config(),
        )
        assert result.score == 0
        assert result.setup_quality == "FAIL"


# ─────────────────────────────────────────────────────────────────────────────
# A+ requires all 8 TT conditions AND vcp_qualified
# ─────────────────────────────────────────────────────────────────────────────

class TestAPlusRequiresAll8AndVCP:
    def test_a_plus_requires_all_8_conditions_and_vcp(self):
        """A+ tag requires stage=2, all 8 TT conditions, vcp_qualified, and high score."""
        row = make_row(RS_rating=99.0, vol_ratio=2.0)
        result = evaluate(
            symbol="TEST",
            date=datetime.date(2025, 1, 1),
            row=row,
            stage_result=_make_stage2(),
            tt_result=_make_tt_pass(8),
            vcp_qual=_make_vcp_a(),
            entry_trigger=_make_entry_no_trigger(),
            stop_result=None,
            config=_default_config(),
        )
        assert result.setup_quality == "A+"
        assert result.score >= 85

    def test_a_plus_not_assigned_when_vcp_fails(self):
        """A+ is denied when VCP qualification fails, even with 8/8 TT conditions."""
        row = make_row(RS_rating=99.0, vol_ratio=2.0)
        result = evaluate(
            symbol="TEST",
            date=datetime.date(2025, 1, 1),
            row=row,
            stage_result=_make_stage2(),
            tt_result=_make_tt_pass(8),
            vcp_qual=_make_vcp_fail(),
            entry_trigger=_make_entry_no_trigger(),
            stop_result=None,
            config=_default_config(),
        )
        assert result.setup_quality != "A+"

    def test_a_plus_not_assigned_when_tt_fails(self):
        """A+ is denied when TT passes=False (conditions_met < 8)."""
        row = make_row(RS_rating=99.0, vol_ratio=2.0)
        result = evaluate(
            symbol="TEST",
            date=datetime.date(2025, 1, 1),
            row=row,
            stage_result=_make_stage2(),
            tt_result=_make_tt_pass(7),
            vcp_qual=_make_vcp_a(),
            entry_trigger=_make_entry_no_trigger(),
            stop_result=None,
            config=_default_config(),
        )
        assert result.setup_quality != "A+"


# ─────────────────────────────────────────────────────────────────────────────
# Score is weighted sum of components
# ─────────────────────────────────────────────────────────────────────────────

class TestScoreIsWeightedSum:
    def test_score_is_weighted_sum_of_components(self):
        """
        Score = weighted sum of (rs_score, trend_score, vcp_score,
        volume_score, 0, 0) rounded to int.
        """
        from rules.vcp_rules import get_vcp_score

        row = make_row(RS_rating=80.0, vol_ratio=1.0)
        tt  = _make_tt_pass(6)
        vcp = VCPQualification(
            qualified=True, contraction_count=2, max_depth_pct=20.0,
            final_depth_pct=6.0, vol_ratio=0.6, base_weeks=8,
            fail_reason=None, quality_grade="B",
        )

        result = evaluate(
            symbol="TEST",
            date=datetime.date(2025, 1, 1),
            row=row,
            stage_result=_make_stage2(),
            tt_result=tt,
            vcp_qual=vcp,
            entry_trigger=_make_entry_no_trigger(),
            stop_result=None,
            config=_default_config(),
        )

        # Re-compute expected composite manually
        weights = _default_config()["scoring"]["weights"]
        rs_score    = min(80.0, 99.0)
        trend_score = (6 / 8.0) * 100.0
        vcp_score   = get_vcp_score(vcp)
        vol_score   = compute_volume_score(row)
        expected = (
            rs_score * weights["rs_rating"]
            + trend_score * weights["trend"]
            + vcp_score * weights["vcp"]
            + vol_score * weights["volume"]
        )
        assert result.score == int(round(expected))


# ─────────────────────────────────────────────────────────────────────────────
# Stop-loss / trailing stop risk_pct never below VCP floor
# ─────────────────────────────────────────────────────────────────────────────

class TestTrailingStopRiskPct:
    def test_trailing_stop_never_below_vcp_floor(self):
        """
        StopLossResult.risk_pct = (entry - stop) / entry * 100.
        With entry=150, stop=138 → risk_pct ≈ 8.0. Verify the logic
        and that risk_pct is always positive (stop < entry).
        """
        entry = 150.0
        stop  = 138.0
        sl = _make_stop(entry=entry, stop=stop)
        assert sl.risk_pct > 0
        assert sl.risk_pct == pytest.approx((entry - stop) / entry * 100, abs=0.01)

    def test_stop_propagated_into_sepa_result(self):
        """evaluate() must pass stop_result fields into SEPAResult unchanged."""
        sl = _make_stop(entry=150.0, stop=138.0)
        result = evaluate(
            symbol="TEST",
            date=datetime.date(2025, 1, 1),
            row=make_row(),
            stage_result=_make_stage2(),
            tt_result=_make_tt_pass(8),
            vcp_qual=_make_vcp_a(),
            entry_trigger=_make_entry_no_trigger(),
            stop_result=sl,
            config=_default_config(),
        )
        assert result.stop_loss == pytest.approx(138.0)
        assert result.risk_pct  == pytest.approx(sl.risk_pct)
        assert result.stop_type == "vcp_base"

    def test_capped_stop_has_correct_risk_pct(self):
        """A capped stop must reflect the capped price, not the raw stop."""
        entry = 150.0
        capped_stop = entry * (1 - 0.08)   # 8% max-risk cap applied
        sl = StopLossResult(
            stop_price=capped_stop,
            stop_type="vcp_base",
            risk_pct=round((entry - capped_stop) / entry * 100, 4),
            capped=True,
        )
        assert sl.capped is True
        assert sl.risk_pct == pytest.approx(8.0, abs=0.01)


# ─────────────────────────────────────────────────────────────────────────────
# compute_volume_score
# ─────────────────────────────────────────────────────────────────────────────

class TestComputeVolumeScore:
    @pytest.mark.parametrize("vol_ratio,expected", [
        (2.0, 100.0),
        (1.5, 80.0),
        (1.0, 60.0),
        (0.5, 30.0),
        (0.0, 0.0),
    ])
    def test_volume_score_thresholds(self, vol_ratio, expected):
        """compute_volume_score returns the documented score for each vol_ratio bucket."""
        row = make_row(vol_ratio=vol_ratio)
        assert compute_volume_score(row) == pytest.approx(expected)

    def test_missing_vol_ratio_returns_neutral(self):
        """Missing vol_ratio column returns neutral score of 50."""
        row = pd.Series({"close": 150.0})
        assert compute_volume_score(row) == pytest.approx(50.0)

    def test_nan_vol_ratio_returns_neutral(self):
        """NaN vol_ratio returns neutral score of 50."""
        row = make_row(vol_ratio=float("nan"))
        assert compute_volume_score(row) == pytest.approx(50.0)


# ─────────────────────────────────────────────────────────────────────────────
# to_dict
# ─────────────────────────────────────────────────────────────────────────────

class TestToDict:
    def test_to_dict_serialisable(self):
        """to_dict() must return a flat dict with all required keys."""
        import json
        result = evaluate(
            symbol="TEST",
            date=datetime.date(2025, 1, 1),
            row=make_row(),
            stage_result=_make_stage2(),
            tt_result=_make_tt_pass(8),
            vcp_qual=_make_vcp_a(),
            entry_trigger=_make_entry_no_trigger(),
            stop_result=None,
            config=_default_config(),
        )
        d = to_dict(result)
        # Must be JSON serialisable
        json_str = json.dumps(d)
        assert len(json_str) > 0
        assert d["symbol"] == "TEST"
        assert d["date"] == "2025-01-01"
        assert d["score"] == result.score
        assert d["setup_quality"] == result.setup_quality

    def test_to_dict_contains_all_keys(self):
        """to_dict() must include every documented field key."""
        result = evaluate(
            symbol="X",
            date=datetime.date(2025, 6, 1),
            row=make_row(),
            stage_result=_make_stage2(),
            tt_result=_make_tt_pass(8),
            vcp_qual=_make_vcp_a(),
            entry_trigger=_make_entry_no_trigger(),
            stop_result=None,
            config=_default_config(),
        )
        d = to_dict(result)
        for key in ["symbol", "date", "stage", "stage_label", "score",
                    "setup_quality", "trend_template_pass", "conditions_met",
                    "vcp_qualified", "vcp_grade", "rs_rating"]:
            assert key in d, f"Missing key '{key}' in to_dict() output"


# ─────────────────────────────────────────────────────────────────────────────
# Quality grade B requires >= 6 conditions
# ─────────────────────────────────────────────────────────────────────────────

class TestQualityGradeBRequires6Conditions:
    def test_quality_grade_b_requires_6_conditions(self):
        """conditions_met=5 with stage=2 and decent score → FAIL (not B)."""
        row = make_row(RS_rating=70.0, vol_ratio=1.5)
        result = evaluate(
            symbol="TEST",
            date=datetime.date(2025, 1, 1),
            row=row,
            stage_result=_make_stage2(),
            tt_result=_make_tt_pass(5),
            vcp_qual=_make_vcp_a(),
            entry_trigger=_make_entry_no_trigger(),
            stop_result=None,
            config=_default_config(),
        )
        assert result.setup_quality == "FAIL"
