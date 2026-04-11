"""
tests/integration/test_known_setups.py
───────────────────────────────────────
Critical regression tests that exercise the full SEPA evaluation pipeline
using synthetic feature rows (no file I/O, no network calls).

These tests guard against regressions in the HARD GATE logic: a non-Stage-2
stock must ALWAYS score 0 and receive setup_quality="FAIL" regardless of how
well it scores on every other dimension.

Test matrix
───────────
  test_stage4_blocked_despite_tt_pass
      Synthetic row where ALL 8 Trend Template conditions would pass,
      but stage=4. Assert SEPAResult.setup_quality == "FAIL" and score == 0.

  test_stage2_a_plus_full_pipeline
      Perfect Stage 2 row → A+ result with score >= 85.

  test_stage2_passes_tt_fails_is_b_or_c
      Stage 2 passes but only 6/8 TT conditions → B or C, never FAIL.
"""

from __future__ import annotations

import datetime

import pandas as pd
import pytest

from rules.entry_trigger import EntryTrigger
from rules.scorer import SEPAResult, evaluate
from rules.stage import StageResult
from rules.stop_loss import StopLossResult
from rules.trend_template import TrendTemplateResult
from rules.vcp_rules import VCPQualification


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — mirror the unit test helpers so this module is self-contained
# ─────────────────────────────────────────────────────────────────────────────

def _default_config() -> dict:
    """Full config dict matching settings.yaml defaults."""
    return {
        "scoring": {
            "weights": {
                "rs_rating":   0.30,
                "trend":       0.25,
                "vcp":         0.25,
                "volume":      0.10,
                "fundamental": 0.07,
                "news":        0.03,
            },
            "setup_quality_thresholds": {
                "a_plus": 85,
                "a":      70,
                "b":      55,
                "c":      40,
            },
        },
        "stage": {
            "ma200_slope_lookback": 20,
            "ma50_slope_lookback":  10,
        },
        "trend_template": {
            "ma200_slope_lookback": 20,
            "pct_above_52w_low":   25.0,
            "pct_below_52w_high":  25.0,
            "min_rs_rating":       70,
        },
        "vcp": {
            "detector":                "rule_based",
            "min_contractions":        2,
            "max_contractions":        5,
            "require_declining_depth": True,
            "require_vol_contraction": True,
            "min_weeks":               3,
            "max_weeks":               52,
            "tightness_pct":           10.0,
            "max_depth_pct":           50.0,
        },
    }


def _feature_row_all_tt_pass(**overrides) -> pd.Series:
    """
    Synthetic feature row where ALL 8 Trend Template conditions would pass
    AND all Stage 2 conditions pass. Override stage-related fields to
    create a Stage 4 scenario.

    TT-passing layout:
        close=150, SMA_50=130, SMA_150=120, SMA_200=110
        MA_slope_50=+0.10, MA_slope_200=+0.08
        low_52w=100 (150 is 50% above → C6 pass)
        high_52w=170 (150 is within 12% → C7 pass)
        RS_rating=80 (>= 70 → C8 pass)
    """
    data = {
        # Price / MA stack — all TT conditions satisfied
        "close":        150.0,
        "SMA_50":       130.0,
        "SMA_150":      120.0,
        "SMA_200":      110.0,
        "MA_slope_50":  0.10,
        "MA_slope_200": 0.08,
        # 52-week range
        "low_52w":      100.0,
        "high_52w":     170.0,
        # Relative strength
        "RS_rating":    80.0,
        # Volume
        "vol_ratio":    2.0,
        "vol_50d_avg":  500_000.0,
        "volume":       1_000_000.0,
        # VCP feature columns
        "vcp_contraction_count": 3.0,
        "vcp_max_depth_pct":     20.0,
        "vcp_final_depth_pct":   3.0,
        "vcp_vol_ratio":         0.3,
        "vcp_base_weeks":        10.0,
        "vcp_is_valid":          True,
        "vcp_fail_reason":       None,
        # Stop-loss feature columns
        "last_pivot_high": 148.0,
        "last_pivot_low":  125.0,
        "ATR_14":          3.0,
    }
    data.update(overrides)
    return pd.Series(data)


def _make_tt_from_row(row: pd.Series, config: dict) -> TrendTemplateResult:
    """Run check_trend_template() against *row* and return the result."""
    from rules.trend_template import check_trend_template
    return check_trend_template(row, config)


def _make_vcp_from_row(row: pd.Series, config: dict) -> VCPQualification:
    """Run check_vcp() against *row* and return the result."""
    from rules.vcp_rules import check_vcp
    return check_vcp(row, config)


def _no_trigger() -> EntryTrigger:
    """EntryTrigger with triggered=False (breakout not yet fired)."""
    return EntryTrigger(
        triggered=False,
        entry_price=None,
        pivot_high=None,
        breakout_vol_ratio=None,
        volume_confirmed=False,
        reason="no breakout (regression test)",
    )


def _stage4_result() -> StageResult:
    """Stage 4 StageResult — the HARD GATE that must block everything."""
    return StageResult(
        stage=4,
        label="Stage 4 — Declining",
        confidence=65,
        reason="price below both MAs, both MAs declining",
        ma_slopes={"slope_50": -0.10, "slope_200": -0.08},
    )


def _stage2_result() -> StageResult:
    """Stage 2 StageResult with full confidence."""
    return StageResult(
        stage=2,
        label="Stage 2 — Advancing",
        confidence=100,
        reason="all conditions met",
        ma_slopes={"slope_50": 0.10, "slope_200": 0.08},
    )


# ─────────────────────────────────────────────────────────────────────────────
# CRITICAL REGRESSION: Stage 4 blocks everything despite TT pass
# ─────────────────────────────────────────────────────────────────────────────

class TestStage4BlockedDespiteTTPass:
    """
    Regression guard for the Stage 2 hard gate in scorer.evaluate().

    The scenario: a stock whose PRICE and MA features would satisfy all
    8 Trend Template conditions — BUT whose stage classification is 4
    (price below both MAs, both MAs declining).  This simulates a real
    edge case where a declining stock temporarily exhibits a TT-like
    MA crossover pattern in older data.

    Expected outcome: SEPAResult.setup_quality == "FAIL", score == 0.
    """

    def test_stage4_blocked_despite_tt_pass(self):
        """
        Stage=4 hard gate forces score=0 and setup_quality='FAIL'
        even when all 8 TT conditions are satisfied on the feature row.
        """
        cfg = _default_config()
        row = _feature_row_all_tt_pass()

        # Verify the TT conditions DO all pass on this row
        tt = _make_tt_from_row(row, cfg)
        assert tt.passes is True, (
            "Pre-condition: the synthetic row should satisfy all 8 TT conditions. "
            f"Only {tt.conditions_met}/8 passed. Details: {tt.details}"
        )
        assert tt.conditions_met == 8

        # Verify VCP qualifies
        vcp = _make_vcp_from_row(row, cfg)
        assert vcp.qualified is True, (
            f"Pre-condition: VCP should be qualified. fail_reason={vcp.fail_reason}"
        )

        # NOW evaluate with Stage 4 — the hard gate must block everything
        result = evaluate(
            symbol="REGRESSION_STOCK",
            date=datetime.date(2025, 3, 15),
            row=row,
            stage_result=_stage4_result(),   # HARD GATE: Stage 4
            tt_result=tt,                    # All 8 TT conditions pass
            vcp_qual=vcp,                    # VCP qualified: Grade A
            entry_trigger=_no_trigger(),
            stop_result=None,
            config=cfg,
        )

        assert result.setup_quality == "FAIL", (
            f"Expected setup_quality='FAIL' for Stage 4 stock, "
            f"got '{result.setup_quality}' with score={result.score}"
        )
        assert result.score == 0, (
            f"Expected score=0 for Stage 4 stock, got {result.score}"
        )

    def test_stage4_blocked_even_with_rs_99(self):
        """
        Stage 4 hard gate must block regardless of RS_rating=99 (near-perfect RS).
        """
        cfg = _default_config()
        row = _feature_row_all_tt_pass(RS_rating=99.0)
        tt  = _make_tt_from_row(row, cfg)
        vcp = _make_vcp_from_row(row, cfg)

        result = evaluate(
            symbol="HIGH_RS_DECLINING",
            date=datetime.date(2025, 3, 15),
            row=row,
            stage_result=_stage4_result(),
            tt_result=tt,
            vcp_qual=vcp,
            entry_trigger=_no_trigger(),
            stop_result=None,
            config=cfg,
        )

        assert result.setup_quality == "FAIL"
        assert result.score == 0

    def test_stage4_result_has_correct_stage_field(self):
        """SEPAResult.stage must reflect the actual stage (4), not a default."""
        cfg = _default_config()
        row = _feature_row_all_tt_pass()
        tt  = _make_tt_from_row(row, cfg)
        vcp = _make_vcp_from_row(row, cfg)

        result = evaluate(
            symbol="STAGECHECK",
            date=datetime.date(2025, 3, 15),
            row=row,
            stage_result=_stage4_result(),
            tt_result=tt,
            vcp_qual=vcp,
            entry_trigger=_no_trigger(),
            stop_result=None,
            config=cfg,
        )

        assert result.stage == 4
        assert "Declining" in result.stage_label


# ─────────────────────────────────────────────────────────────────────────────
# Happy path regressions
# ─────────────────────────────────────────────────────────────────────────────

class TestStage2APlusFullPipeline:
    """
    Smoke-test the full pipeline for an ideal Stage 2 A+ candidate.
    Ensures the scoring system produces A+ when all components are optimal.
    """

    def test_stage2_a_plus_full_pipeline(self):
        """Perfect Stage 2 row with RS=99, vol_ratio=2 → A+ and score >= 85."""
        cfg = _default_config()
        row = _feature_row_all_tt_pass(RS_rating=99.0, vol_ratio=2.0)
        tt  = _make_tt_from_row(row, cfg)
        vcp = _make_vcp_from_row(row, cfg)

        assert tt.passes is True
        assert vcp.qualified is True

        result = evaluate(
            symbol="IDEAL_SETUP",
            date=datetime.date(2025, 3, 15),
            row=row,
            stage_result=_stage2_result(),
            tt_result=tt,
            vcp_qual=vcp,
            entry_trigger=_no_trigger(),
            stop_result=None,
            config=cfg,
        )

        assert result.setup_quality == "A+", (
            f"Expected A+ for ideal Stage 2 stock, got '{result.setup_quality}' "
            f"(score={result.score})"
        )
        assert result.score >= 85
        assert result.stage == 2


class TestStage2PartialTTIsNotFail:
    """
    Regression: Stage 2 stock with 6/8 TT conditions must not be FAIL
    (it should be B or C depending on score).
    """

    def test_stage2_passes_tt_6of8_is_b_or_c(self):
        """Stage 2 + 6/8 TT conditions → setup_quality is B or C, never FAIL."""
        cfg = _default_config()
        row = _feature_row_all_tt_pass(RS_rating=72.0, vol_ratio=1.5)

        # Manually build a TT result with only 6 conditions
        conds = {f"C{i}": (i <= 6) for i in range(1, 9)}
        tt_6 = TrendTemplateResult(
            passes=False,
            conditions=conds,
            conditions_met=6,
            details={k: "ok" if v else "fail" for k, v in conds.items()},
        )
        vcp = _make_vcp_from_row(row, cfg)

        result = evaluate(
            symbol="PARTIAL_TT",
            date=datetime.date(2025, 3, 15),
            row=row,
            stage_result=_stage2_result(),
            tt_result=tt_6,
            vcp_qual=vcp,
            entry_trigger=_no_trigger(),
            stop_result=None,
            config=cfg,
        )

        assert result.setup_quality in ("B", "C"), (
            f"Expected B or C for 6/8 TT conditions, got '{result.setup_quality}'"
        )
        assert result.stage == 2

    def test_stage2_tt_5of8_is_fail(self):
        """Stage 2 + only 5/8 TT conditions → FAIL (conditions_met < 6 threshold)."""
        cfg = _default_config()
        row = _feature_row_all_tt_pass(RS_rating=72.0, vol_ratio=1.5)

        conds = {f"C{i}": (i <= 5) for i in range(1, 9)}
        tt_5 = TrendTemplateResult(
            passes=False,
            conditions=conds,
            conditions_met=5,
            details={k: "ok" if v else "fail" for k, v in conds.items()},
        )
        vcp = _make_vcp_from_row(row, cfg)

        result = evaluate(
            symbol="WEAK_TT",
            date=datetime.date(2025, 3, 15),
            row=row,
            stage_result=_stage2_result(),
            tt_result=tt_5,
            vcp_qual=vcp,
            entry_trigger=_no_trigger(),
            stop_result=None,
            config=cfg,
        )

        assert result.setup_quality == "FAIL", (
            f"Expected FAIL for 5/8 TT conditions, got '{result.setup_quality}'"
        )
