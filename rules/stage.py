"""
rules/stage.py
──────────────
Stage detection for the Minervini AI SEPA rule engine.

Overview
────────
Mark Minervini's SEPA methodology classifies every stock into one of four
market stages before any other criterion is evaluated.  Stage 2 — Advancing
is the ONLY stage in which long positions are permitted.  This module is the
HARD GATE that runs first in the rule engine: non-Stage-2 stocks are
eliminated immediately, regardless of score, VCP quality, or fundamentals.

Stage Definitions  (PROJECT_DESIGN.md §7.1)
────────────────────────────────────────────
    Stage 1 — Basing
        Price is below both SMA_50 and SMA_200 and both MAs are flat.
        Stocks are accumulating sideways; not yet in a confirmed uptrend.

    Stage 2 — Advancing  ← ONLY BUY STAGE
        All four conditions must hold:
          1. close > SMA_50  AND  close > SMA_200
          2. SMA_50 > SMA_200  (correct moving-average stack)
          3. MA_slope_200 > 0  (200-day MA pointing upward)
          4. MA_slope_50  > 0  (50-day MA pointing upward)

    Stage 3 — Topping
        Stock has lost SMA_50 support; SMA_50 is declining; price is still
        above SMA_200.  Distribution phase — long exposure should be reduced.

    Stage 4 — Declining
        Price is below both MAs and both MAs are declining.  Avoid entirely.

Public API
──────────
    detect_stage(row, config) → StageResult

        Accepts the last row of the feature DataFrame (a pd.Series produced by
        features/feature_store.py) and returns a fully populated StageResult.

Fail-loud contract  (PROJECT_DESIGN.md §19.1)
─────────────────────────────────────────────
    If any of the required feature columns (close, SMA_50, SMA_200,
    MA_slope_50, MA_slope_200) contains NaN, RuleEngineError is raised
    immediately.  Missing data is never silently treated as a failed condition.

Confidence scoring
──────────────────
    Confidence (0–100) reflects how cleanly the Stage 2 conditions are
    satisfied.  100 means every condition is comfortably met; values below 100
    indicate borderline readings on one or more conditions.  Non-Stage-2
    results carry a lower confidence ceiling to reflect the ambiguity that
    arises when stage classification itself is not decisive.

    Borderline thresholds (internal defaults, not decision thresholds):
        price_margin_pct    2 %    — price within 2 % of an MA is "borderline"
        slope_min_pct_day   0.05   — slope < 0.05 %/day is "barely positive"

Config keys consumed
────────────────────
    config["stage"]["ma200_slope_lookback"]  → int  (lookback used by features)
    config["stage"]["ma50_slope_lookback"]   → int  (lookback used by features)

    These keys are read for logging context only.  The slope values themselves
    are pre-computed by features/moving_averages.py and present in the row.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from utils.exceptions import RuleEngineError
from utils.logger import get_logger

log = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Internal confidence parameters
# ─────────────────────────────────────────────────────────────────────────────

# A price that is within this many percent of an MA boundary is "borderline".
_PRICE_MARGIN_PCT: float = 2.0

# A slope below this value (% per day) is considered "barely positive/negative".
_SLOPE_MIN_PCT_DAY: float = 0.05

# Confidence penalty per borderline condition.
_PENALTY_PER_BORDERLINE: int = 15

# Maximum confidence for non-Stage-2 classifications.
_MAX_CONFIDENCE_NON_STAGE2: int = 70


# ─────────────────────────────────────────────────────────────────────────────
# StageResult dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class StageResult:
    """
    Immutable result returned by detect_stage().

    Fields
    ──────
    stage : int
        Stage number (1, 2, 3, or 4).

    label : str
        Human-readable stage name, e.g. "Stage 2 — Advancing".

    confidence : int
        How cleanly the stage conditions are satisfied (0–100).
        100 = all conditions clearly met; lower = one or more borderline.

    reason : str
        Human-readable explanation of why this stage was assigned,
        including the key feature values that drove the decision.

    ma_slopes : dict[str, float]
        Raw slope values used in detection:
            {"slope_50": float, "slope_200": float}
        Expressed as % per day (output of features/moving_averages.py).
    """

    stage: int
    label: str
    confidence: int
    reason: str
    ma_slopes: dict[str, float] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

_STAGE_LABELS: dict[int, str] = {
    1: "Stage 1 — Basing",
    2: "Stage 2 — Advancing",
    3: "Stage 3 — Topping",
    4: "Stage 4 — Declining",
}


def _require_float(row: pd.Series, col: str) -> float:
    """
    Extract *col* from *row* as a float; raise RuleEngineError on NaN/missing.

    Args:
        row: Last row of the feature DataFrame as a pd.Series.
        col: Column name to extract.

    Returns:
        The column's value as a Python float.

    Raises:
        RuleEngineError: If the column is absent or its value is NaN.
    """
    if col not in row.index:
        raise RuleEngineError(
            f"Required feature column '{col}' is missing from the feature row. "
            "Ensure features/moving_averages.py has been run for this symbol.",
            column=col,
        )
    val = row[col]
    if val is None or (isinstance(val, float) and math.isnan(val)):
        raise RuleEngineError(
            f"Feature column '{col}' is NaN in the most recent row. "
            "Insufficient history or a feature-store update failure.",
            column=col,
        )
    return float(val)


def _pct_diff(a: float, b: float) -> float:
    """Return (a - b) / b * 100.  Safe when b == 0 (returns 0.0)."""
    return (a - b) / b * 100.0 if b != 0.0 else 0.0


def _is_borderline_price(price: float, ma: float) -> bool:
    """True when *price* is above *ma* but within _PRICE_MARGIN_PCT of it."""
    return 0.0 < _pct_diff(price, ma) < _PRICE_MARGIN_PCT


def _is_borderline_slope(slope: float) -> bool:
    """True when *slope* is positive but barely so (< _SLOPE_MIN_PCT_DAY)."""
    return 0.0 < slope < _SLOPE_MIN_PCT_DAY


def _stage2_confidence(
    close: float,
    sma50: float,
    sma200: float,
    slope50: float,
    slope200: float,
) -> int:
    """
    Compute confidence for a confirmed Stage 2 classification.

    Starts at 100 and deducts _PENALTY_PER_BORDERLINE for each condition
    that is only marginally satisfied.

    Stage 2 conditions evaluated:
        C1: close > SMA_50   (price above 50-day MA)
        C2: close > SMA_200  (price above 200-day MA)
        C3: SMA_50 > SMA_200 (correct MA stack)
        C4: MA_slope_200 > 0 (200-day MA trending up)
        C5: MA_slope_50  > 0 (50-day MA trending up)
    """
    confidence = 100
    borderline_notes: list[str] = []

    if _is_borderline_price(close, sma50):
        confidence -= _PENALTY_PER_BORDERLINE
        borderline_notes.append("close barely above SMA_50")

    if _is_borderline_price(close, sma200):
        confidence -= _PENALTY_PER_BORDERLINE
        borderline_notes.append("close barely above SMA_200")

    if _is_borderline_price(sma50, sma200):
        confidence -= _PENALTY_PER_BORDERLINE
        borderline_notes.append("SMA_50 barely above SMA_200")

    if _is_borderline_slope(slope200):
        confidence -= _PENALTY_PER_BORDERLINE
        borderline_notes.append("MA_slope_200 barely positive")

    if _is_borderline_slope(slope50):
        confidence -= _PENALTY_PER_BORDERLINE
        borderline_notes.append("MA_slope_50 barely positive")

    if borderline_notes:
        log.debug(
            "Stage 2 borderline conditions detected",
            notes=borderline_notes,
            confidence=confidence,
        )

    return max(0, confidence)


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def detect_stage(row: pd.Series, config: dict) -> StageResult:
    """
    Detect the Minervini stage for the stock represented by *row*.

    Parameters
    ──────────
    row : pd.Series
        The most recent row of the feature DataFrame (iloc[-1]).
        Required columns: close, SMA_50, SMA_200, MA_slope_50, MA_slope_200.
        SMA_150 is also read (available from the feature store) for context
        but is not used in stage boundary conditions.

    config : dict
        Application configuration dict (loaded from settings.yaml).
        Keys read for logging context:
            config["stage"]["ma200_slope_lookback"]
            config["stage"]["ma50_slope_lookback"]

    Returns
    ───────
    StageResult
        Fully populated result.  The caller should gate on result.stage == 2
        before proceeding with any further scoring.

    Raises
    ──────
    RuleEngineError
        If any required feature column is missing or contains NaN.
        Never swallows data-quality issues silently.
    """
    stage_cfg = config.get("stage", {})
    slope200_lookback: int = int(stage_cfg.get("ma200_slope_lookback", 20))
    slope50_lookback: int = int(stage_cfg.get("ma50_slope_lookback", 10))

    log.debug(
        "detect_stage called",
        slope200_lookback=slope200_lookback,
        slope50_lookback=slope50_lookback,
    )

    # ── Extract and validate required features ────────────────────────────────
    close   = _require_float(row, "close")
    sma50   = _require_float(row, "SMA_50")
    sma200  = _require_float(row, "SMA_200")
    slope50 = _require_float(row, "MA_slope_50")
    slope200 = _require_float(row, "MA_slope_200")

    # SMA_150 is present in the feature store; read it for completeness but
    # stage boundary logic does not use it directly.
    sma150: float | None = None
    if "SMA_150" in row.index:
        raw = row["SMA_150"]
        if raw is not None and not (isinstance(raw, float) and math.isnan(raw)):
            sma150 = float(raw)

    ma_slopes = {"slope_50": slope50, "slope_200": slope200}

    # ── Stage 2 — Advancing (evaluated first — the only buy stage) ───────────
    price_above_sma50  = close  > sma50
    price_above_sma200 = close  > sma200
    stack_correct      = sma50  > sma200
    slope200_positive  = slope200 > 0.0
    slope50_positive   = slope50  > 0.0

    if (price_above_sma50 and price_above_sma200
            and stack_correct
            and slope200_positive
            and slope50_positive):

        confidence = _stage2_confidence(close, sma50, sma200, slope50, slope200)
        reason = (
            f"close={close:.2f} > SMA_50={sma50:.2f} (+{_pct_diff(close, sma50):.1f}%) "
            f"and > SMA_200={sma200:.2f} (+{_pct_diff(close, sma200):.1f}%); "
            f"SMA_50 > SMA_200 (+{_pct_diff(sma50, sma200):.1f}%); "
            f"MA_slope_50={slope50:+.3f}%/day; "
            f"MA_slope_200={slope200:+.3f}%/day — all Stage 2 conditions satisfied."
        )
        result = StageResult(
            stage=2,
            label=_STAGE_LABELS[2],
            confidence=confidence,
            reason=reason,
            ma_slopes=ma_slopes,
        )
        log.info("Stage detected", stage=2, confidence=confidence)
        return result

    # ── Stage 3 — Topping ─────────────────────────────────────────────────────
    # Price has lost SMA_50 support; SMA_50 is declining; still above SMA_200.
    price_below_sma50  = close  < sma50
    price_above_sma200_s3 = close > sma200
    sma50_declining    = slope50 < 0.0

    if price_below_sma50 and price_above_sma200_s3 and sma50_declining:
        margin_below_sma50 = _pct_diff(sma50, close)   # positive = close below
        confidence = min(
            _MAX_CONFIDENCE_NON_STAGE2,
            max(0, _MAX_CONFIDENCE_NON_STAGE2 - _PENALTY_PER_BORDERLINE
                if margin_below_sma50 < _PRICE_MARGIN_PCT else _MAX_CONFIDENCE_NON_STAGE2),
        )
        reason = (
            f"close={close:.2f} is {margin_below_sma50:.1f}% below SMA_50={sma50:.2f}; "
            f"MA_slope_50={slope50:+.3f}%/day (declining); "
            f"close still above SMA_200={sma200:.2f} — distribution / topping phase."
        )
        result = StageResult(
            stage=3,
            label=_STAGE_LABELS[3],
            confidence=confidence,
            reason=reason,
            ma_slopes=ma_slopes,
        )
        log.info("Stage detected", stage=3, confidence=confidence)
        return result

    # ── Stage 4 — Declining ──────────────────────────────────────────────────
    # Price is below both MAs and both MAs are declining.
    price_below_sma200 = close < sma200
    slope200_negative  = slope200 < 0.0

    if price_below_sma50 and price_below_sma200 and slope50 < 0.0 and slope200_negative:
        confidence = min(
            _MAX_CONFIDENCE_NON_STAGE2,
            max(0, _MAX_CONFIDENCE_NON_STAGE2 - _PENALTY_PER_BORDERLINE
                if abs(slope200) < _SLOPE_MIN_PCT_DAY else _MAX_CONFIDENCE_NON_STAGE2),
        )
        reason = (
            f"close={close:.2f} below SMA_50={sma50:.2f} "
            f"({_pct_diff(close, sma50):.1f}%) and SMA_200={sma200:.2f} "
            f"({_pct_diff(close, sma200):.1f}%); "
            f"MA_slope_50={slope50:+.3f}%/day; "
            f"MA_slope_200={slope200:+.3f}%/day — both MAs declining."
        )
        result = StageResult(
            stage=4,
            label=_STAGE_LABELS[4],
            confidence=confidence,
            reason=reason,
            ma_slopes=ma_slopes,
        )
        log.info("Stage detected", stage=4, confidence=confidence)
        return result

    # ── Stage 1 — Basing (default / catch-all) ────────────────────────────────
    # Price below one or both MAs; MAs are flat or mixed.
    # Includes any ambiguous combination not captured by stages 2–4.
    confidence = min(
        _MAX_CONFIDENCE_NON_STAGE2,
        max(0, _MAX_CONFIDENCE_NON_STAGE2 - _PENALTY_PER_BORDERLINE
            if abs(slope200) < _SLOPE_MIN_PCT_DAY else _MAX_CONFIDENCE_NON_STAGE2),
    )
    reason = (
        f"close={close:.2f}; SMA_50={sma50:.2f} ({_pct_diff(close, sma50):+.1f}%); "
        f"SMA_200={sma200:.2f} ({_pct_diff(close, sma200):+.1f}%); "
        f"MA_slope_50={slope50:+.3f}%/day; MA_slope_200={slope200:+.3f}%/day; "
        f"price/MA pattern consistent with basing or indeterminate phase."
    )
    result = StageResult(
        stage=1,
        label=_STAGE_LABELS[1],
        confidence=confidence,
        reason=reason,
        ma_slopes=ma_slopes,
    )
    log.info("Stage detected", stage=1, confidence=confidence)
    return result
