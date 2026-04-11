"""
tests/unit/test_vcp.py
──────────────────────
Unit tests for features/vcp.py — VCPMetrics, RuleBasedVCPDetector, and compute().

Test coverage
─────────────
  test_valid_vcp_three_contractions      — 3 declining pivots + vol dry-up → valid
  test_invalid_vcp_non_declining         — contractions NOT declining → invalid
  test_invalid_vcp_insufficient_pivots   — only 1 swing high → invalid, fail_reason set
  test_vol_contraction_ratio             — last-leg vol < first-leg → ratio < 1.0
  test_missing_pivot_columns_raises      — df without is_swing_high → FeatureComputeError

All synthetic DataFrames use a DatetimeIndex so base_length_weeks is meaningful.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from features.vcp import (
    DETECTORS,
    RuleBasedVCPDetector,
    VCPMetrics,
    compute,
)
from utils.exceptions import FeatureComputeError


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _base_config(**overrides) -> dict:
    """Minimal config dict with VCP defaults; any key can be overridden."""
    vcp = {
        "detector": "rule_based",
        "min_contractions": 2,
        "require_declining_depth": True,
        "require_vol_contraction": True,
        "min_weeks": 3,
        "max_weeks": 52,
        "tightness_pct": 10.0,
        "max_depth_pct": 50.0,
        "pivot_window": 5,
    }
    vcp.update(overrides)
    return {"vcp": vcp}


def _make_ohlcv(
    n: int = 120,
    start: str = "2024-01-01",
    base_price: float = 100.0,
    base_volume: float = 1_000_000.0,
) -> pd.DataFrame:
    """
    Return a plain OHLCV DataFrame with DatetimeIndex.
    All OHLC columns are set to base_price; volume to base_volume.
    Callers overwrite specific rows to inject swing highs / lows.
    """
    dates = pd.date_range(start=start, periods=n, freq="B")
    df = pd.DataFrame(
        {
            "open":   base_price,
            "high":   base_price,
            "low":    base_price,
            "close":  base_price,
            "volume": base_volume,
        },
        index=dates,
    )
    # Pivot columns — default everything to False (not pd.NA)
    df["is_swing_high"] = pd.array([False] * n, dtype="boolean")
    df["is_swing_low"]  = pd.array([False] * n, dtype="boolean")
    return df


def _set_pivot(
    df: pd.DataFrame,
    row: int,
    *,
    is_high: bool = False,
    is_low: bool = False,
    price: float | None = None,
    volume: float | None = None,
) -> None:
    """Mutate a single row to mark it as a swing high and/or swing low."""
    if is_high:
        df.iloc[row, df.columns.get_loc("is_swing_high")] = True
        if price is not None:
            df.iloc[row, df.columns.get_loc("high")] = price
    if is_low:
        df.iloc[row, df.columns.get_loc("is_swing_low")] = True
        if price is not None:
            df.iloc[row, df.columns.get_loc("low")] = price
    if volume is not None:
        df.iloc[row, df.columns.get_loc("volume")] = volume


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestValidVCPThreeContractions:
    """
    A synthetic DataFrame with 3 declining contraction legs and volume dry-up
    should return is_valid_vcp = True.

    Leg layout (pivot rows are well within the 120-row window):
        Leg 1: SH at row 10 (price=130), SL at row 20 (price=104) → depth ~20%
        Leg 2: SH at row 30 (price=125), SL at row 40 (price=106) → depth ~15%
        Leg 3: SH at row 60 (price=120), SL at row 70 (price=108) → depth ~10%

    Volume: first leg 2_000_000, last leg 500_000 → ratio < 1.0
    Base: first SH row 10, last SH row 60 → ~50 days → ~7 weeks
    """

    def setup_method(self):
        self.df = _make_ohlcv(n=120)
        # Leg 1 (deep): ~20% correction, high volume
        _set_pivot(self.df, 10, is_high=True, price=130.0, volume=2_000_000)
        for r in range(10, 21):
            self.df.iloc[r, self.df.columns.get_loc("volume")] = 2_000_000
        _set_pivot(self.df, 20, is_low=True, price=104.0)

        # Leg 2 (~15% correction), medium volume
        _set_pivot(self.df, 30, is_high=True, price=125.0, volume=1_200_000)
        for r in range(30, 41):
            self.df.iloc[r, self.df.columns.get_loc("volume")] = 1_200_000
        _set_pivot(self.df, 40, is_low=True, price=106.0)

        # Leg 3 (~10% correction), low volume → vol dry-up
        _set_pivot(self.df, 60, is_high=True, price=120.0, volume=500_000)
        for r in range(60, 71):
            self.df.iloc[r, self.df.columns.get_loc("volume")] = 500_000
        _set_pivot(self.df, 70, is_low=True, price=108.0)

        self.config = _base_config(
            min_contractions=2,
            tightness_pct=12.0,   # last leg ~10% qualifies
            require_declining_depth=True,
            require_vol_contraction=True,
        )

    def test_is_valid(self):
        detector = RuleBasedVCPDetector()
        metrics = detector.detect(self.df, self.config)
        assert metrics.is_valid_vcp is True, (
            f"Expected valid VCP but got fail_reason: {metrics.fail_reason}"
        )

    def test_fail_reason_none_when_valid(self):
        detector = RuleBasedVCPDetector()
        metrics = detector.detect(self.df, self.config)
        assert metrics.fail_reason is None

    def test_contraction_count_at_least_three(self):
        detector = RuleBasedVCPDetector()
        metrics = detector.detect(self.df, self.config)
        assert metrics.contraction_count >= 3

    def test_max_depth_is_deepest_leg(self):
        detector = RuleBasedVCPDetector()
        metrics = detector.detect(self.df, self.config)
        # Leg 1: (130 - 104) / 130 * 100 ≈ 20%
        assert metrics.max_depth_pct > 15.0

    def test_final_depth_is_shallowest(self):
        detector = RuleBasedVCPDetector()
        metrics = detector.detect(self.df, self.config)
        # Leg 3 is shallower than Leg 1
        assert metrics.final_depth_pct < metrics.max_depth_pct

    def test_compute_appends_columns(self):
        out = compute(self.df, self.config)
        for col in (
            "vcp_contraction_count",
            "vcp_max_depth_pct",
            "vcp_final_depth_pct",
            "vcp_vol_ratio",
            "vcp_base_weeks",
            "vcp_is_valid",
            "vcp_fail_reason",
        ):
            assert col in out.columns, f"Missing column: {col}"

    def test_compute_valid_on_last_row(self):
        out = compute(self.df, self.config)
        assert out["vcp_is_valid"].iloc[-1] is True or out["vcp_is_valid"].iloc[-1] == True


class TestInvalidVCPNonDeclining:
    """
    When contractions are NOT declining in depth, is_valid_vcp must be False.

    Leg 1: SH row 10 price=130, SL row 20 price=104 → ~20%
    Leg 2: SH row 30 price=120, SL row 40 price=090 → ~25% (DEEPER — invalid)
    """

    def setup_method(self):
        self.df = _make_ohlcv(n=120)
        _set_pivot(self.df, 10, is_high=True, price=130.0)
        _set_pivot(self.df, 20, is_low=True,  price=104.0)
        _set_pivot(self.df, 30, is_high=True, price=120.0)
        _set_pivot(self.df, 40, is_low=True,  price=90.0)   # deeper!
        self.config = _base_config(
            require_declining_depth=True,
            require_vol_contraction=False,
        )

    def test_is_invalid(self):
        detector = RuleBasedVCPDetector()
        metrics = detector.detect(self.df, self.config)
        assert metrics.is_valid_vcp is False

    def test_fail_reason_mentions_declining(self):
        detector = RuleBasedVCPDetector()
        metrics = detector.detect(self.df, self.config)
        assert metrics.fail_reason is not None
        assert "declining" in metrics.fail_reason.lower()

    def test_contraction_count_still_reported(self):
        """Even when invalid, contraction_count should still be populated."""
        detector = RuleBasedVCPDetector()
        metrics = detector.detect(self.df, self.config)
        assert metrics.contraction_count >= 2


class TestInvalidVCPInsufficientPivots:
    """
    When fewer than 2 confirmed swing highs exist the detector must return
    is_valid_vcp = False with fail_reason = 'insufficient pivots'.
    """

    def setup_method(self):
        self.df = _make_ohlcv(n=50)
        # Only ONE swing high
        _set_pivot(self.df, 10, is_high=True, price=120.0)
        _set_pivot(self.df, 20, is_low=True,  price=100.0)
        self.config = _base_config()

    def test_is_invalid(self):
        detector = RuleBasedVCPDetector()
        metrics = detector.detect(self.df, self.config)
        assert metrics.is_valid_vcp is False

    def test_fail_reason_is_insufficient_pivots(self):
        detector = RuleBasedVCPDetector()
        metrics = detector.detect(self.df, self.config)
        assert metrics.fail_reason == "insufficient pivots"

    def test_compute_sets_fail_reason_on_last_row(self):
        out = compute(self.df, self.config)
        assert out["vcp_fail_reason"].iloc[-1] == "insufficient pivots"

    def test_no_swing_highs_at_all(self):
        """DataFrame with zero swing highs — still insufficient pivots."""
        df = _make_ohlcv(n=50)
        metrics = RuleBasedVCPDetector().detect(df, self.config)
        assert metrics.is_valid_vcp is False
        assert metrics.fail_reason == "insufficient pivots"


class TestVolContractionRatio:
    """
    When last-leg average volume < first-leg average volume,
    vol_contraction_ratio must be < 1.0.
    """

    def setup_method(self):
        self.df = _make_ohlcv(n=120)

        # Leg 1: rows 10–20, high volume = 2_000_000
        _set_pivot(self.df, 10, is_high=True, price=130.0)
        for r in range(10, 21):
            self.df.iloc[r, self.df.columns.get_loc("volume")] = 2_000_000
        _set_pivot(self.df, 20, is_low=True, price=104.0)

        # Leg 2: rows 30–40, low volume = 400_000
        _set_pivot(self.df, 30, is_high=True, price=125.0)
        for r in range(30, 41):
            self.df.iloc[r, self.df.columns.get_loc("volume")] = 400_000
        _set_pivot(self.df, 40, is_low=True, price=112.0)

        self.config = _base_config(
            require_declining_depth=True,
            require_vol_contraction=True,
        )

    def test_ratio_less_than_one(self):
        detector = RuleBasedVCPDetector()
        metrics = detector.detect(self.df, self.config)
        assert metrics.vol_contraction_ratio < 1.0, (
            f"Expected vol_ratio < 1.0, got {metrics.vol_contraction_ratio:.4f}"
        )

    def test_ratio_roughly_correct(self):
        """400_000 / 2_000_000 = 0.2 (approximately)."""
        detector = RuleBasedVCPDetector()
        metrics = detector.detect(self.df, self.config)
        assert metrics.vol_contraction_ratio == pytest.approx(0.2, abs=0.05)

    def test_vol_expansion_fails(self):
        """Swap volumes: last leg > first leg → require_vol_contraction fails."""
        df = self.df.copy()
        # First leg low volume = 400_000
        for r in range(10, 21):
            df.iloc[r, df.columns.get_loc("volume")] = 400_000
        # Last leg high volume = 2_000_000
        for r in range(30, 41):
            df.iloc[r, df.columns.get_loc("volume")] = 2_000_000

        config = _base_config(
            require_declining_depth=False,
            require_vol_contraction=True,
            tightness_pct=50.0,
        )
        metrics = RuleBasedVCPDetector().detect(df, config)
        assert metrics.is_valid_vcp is False
        assert "vol" in metrics.fail_reason.lower()


class TestMissingPivotColumnsRaises:
    """
    compute() must raise FeatureComputeError when pivot columns are missing.
    """

    def test_missing_both_pivot_columns(self):
        df = pd.DataFrame(
            {"open": [100.0], "high": [101.0], "low": [99.0],
             "close": [100.0], "volume": [1_000_000]},
            index=pd.date_range("2024-01-01", periods=1),
        )
        with pytest.raises(FeatureComputeError):
            compute(df, _base_config())

    def test_missing_is_swing_high_only(self):
        df = _make_ohlcv(n=20)
        df = df.drop(columns=["is_swing_high"])
        with pytest.raises(FeatureComputeError):
            compute(df, _base_config())

    def test_missing_is_swing_low_only(self):
        df = _make_ohlcv(n=20)
        df = df.drop(columns=["is_swing_low"])
        with pytest.raises(FeatureComputeError):
            compute(df, _base_config())

    def test_error_message_mentions_missing_column(self):
        df = _make_ohlcv(n=20).drop(columns=["is_swing_high"])
        with pytest.raises(FeatureComputeError) as exc_info:
            compute(df, _base_config())
        assert "is_swing_high" in str(exc_info.value)


# ─────────────────────────────────────────────────────────────────────────────
# Registry smoke tests
# ─────────────────────────────────────────────────────────────────────────────

class TestDetectorRegistry:
    def test_rule_based_key_present(self):
        assert "rule_based" in DETECTORS

    def test_cnn_key_present_but_none(self):
        assert "cnn" in DETECTORS
        assert DETECTORS["cnn"] is None

    def test_rule_based_instantiable(self):
        cls = DETECTORS["rule_based"]
        assert cls is not None
        detector = cls()
        assert isinstance(detector, RuleBasedVCPDetector)

    def test_unknown_detector_raises(self):
        df = _make_ohlcv(n=50)
        _set_pivot(df, 10, is_high=True, price=120.0)
        _set_pivot(df, 20, is_low=True,  price=100.0)
        with pytest.raises(ValueError, match="Unknown VCP detector"):
            compute(df, {"vcp": {"detector": "transformer"}})

    def test_cnn_detector_raises_not_implemented(self):
        df = _make_ohlcv(n=50)
        _set_pivot(df, 10, is_high=True, price=120.0)
        _set_pivot(df, 20, is_low=True,  price=100.0)
        with pytest.raises(ValueError, match="not yet implemented"):
            compute(df, {"vcp": {"detector": "cnn"}})


# ─────────────────────────────────────────────────────────────────────────────
# VCPMetrics dataclass sanity
# ─────────────────────────────────────────────────────────────────────────────

class TestVCPMetricsDefaults:
    def test_default_is_invalid(self):
        m = VCPMetrics()
        assert m.is_valid_vcp is False

    def test_default_fail_reason_none(self):
        m = VCPMetrics()
        assert m.fail_reason is None

    def test_fields_accessible(self):
        m = VCPMetrics(
            contraction_count=3,
            max_depth_pct=20.0,
            final_depth_pct=8.0,
            vol_contraction_ratio=0.35,
            base_length_weeks=8,
            is_valid_vcp=True,
            fail_reason=None,
        )
        assert m.contraction_count == 3
        assert m.max_depth_pct == 20.0
        assert m.final_depth_pct == 8.0
        assert m.vol_contraction_ratio == 0.35
        assert m.base_length_weeks == 8
        assert m.is_valid_vcp is True
