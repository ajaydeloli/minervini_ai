"""
rules/trend_template.py
───────────────────────
Minervini Trend Template evaluation for the SEPA rule engine.

Overview
────────
Mark Minervini's Trend Template is a set of 8 price / MA / RS conditions that
a stock must satisfy to be considered for a long setup.  This module evaluates
all 8 conditions against the MOST RECENT ROW of the feature DataFrame and
returns a fully populated TrendTemplateResult.

Prerequisites
─────────────
Stage 2 detection (rules/stage.py) must have ALREADY passed before this
function is called.  Non-Stage-2 stocks should never reach this gate.

The 8 Conditions  (PROJECT_DESIGN.md §7.2)
───────────────────────────────────────────
    C1: close > SMA_150 AND close > SMA_200
    C2: SMA_150 > SMA_200
    C3: MA_slope_200 > 0  (200-day MA trending up)
    C4: SMA_50 > SMA_150 AND SMA_50 > SMA_200
    C5: close > SMA_50
    C6: close >= low_52w  * (1 + pct_above_52w_low  / 100)   [≥25% above 52w low]
    C7: close >= high_52w * (1 - pct_below_52w_high / 100)   [within 25% of 52w high]
    C8: RS_rating >= min_rs_rating  (default 70)

Public API
──────────
    check_trend_template(row, config) → TrendTemplateResult

Fail-loud contract  (PROJECT_DESIGN.md §19.1)
─────────────────────────────────────────────
    NaN or missing required columns → RuleEngineError raised immediately.
    Individual condition failures are NEVER errors — they are captured in
    the result's ``conditions`` and ``details`` dicts.

Config keys consumed
────────────────────
    config["trend_template"]["pct_above_52w_low"]   float  (default 25.0)
    config["trend_template"]["pct_below_52w_high"]  float  (default 25.0)
    config["trend_template"]["min_rs_rating"]        int    (default 70)
    config["trend_template"]["ma200_slope_lookback"] int    (used for logging context only)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from utils.exceptions import RuleEngineError
from utils.logger import get_logger

log = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# TrendTemplateResult dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TrendTemplateResult:
    """
    Immutable result returned by check_trend_template().

    Fields
    ──────
    passes : bool
        True only when ALL 8 conditions pass.

    conditions : dict[str, bool]
        Per-condition boolean: {"C1": True, "C2": False, ...}

    conditions_met : int
        Count of passing conditions (0–8).

    details : dict[str, str]
        Human-readable explanation per condition, including actual values.
        Example: {"C1": "close 150.00 > SMA_150 140.00 AND > SMA_200 130.00 ✓"}
    """

    passes: bool
    conditions: dict[str, bool] = field(default_factory=dict)
    conditions_met: int = 0
    details: dict[str, str] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

_TICK = "✓"
_CROSS = "✗"


def _require_value(row: pd.Series, col_name: str) -> float:
    """
    Extract *col_name* from *row* as a float.

    Args:
        row:      Most recent feature row as a pd.Series.
        col_name: Column name to extract.

    Returns:
        The column's value as a Python float.

    Raises:
        RuleEngineError: If the column is absent from the index or is NaN.
    """
    if col_name not in row.index:
        raise RuleEngineError(
            f"Required feature column '{col_name}' is missing from the feature row. "
            "Ensure the feature store has been run for this symbol.",
            column=col_name,
        )
    val = row[col_name]
    if val is None or (isinstance(val, float) and math.isnan(val)):
        raise RuleEngineError(
            f"Feature column '{col_name}' is NaN in the most recent row. "
            "Insufficient history or a feature-store update failure.",
            column=col_name,
        )
    return float(val)


def _mark(passed: bool) -> str:
    return _TICK if passed else _CROSS


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def check_trend_template(row: pd.Series, config: dict) -> TrendTemplateResult:
    """
    Evaluate all 8 Minervini Trend Template conditions against *row*.

    Parameters
    ──────────
    row : pd.Series
        The most recent row of the feature DataFrame (iloc[-1]).
        Required columns:
            close, SMA_50, SMA_150, SMA_200,
            MA_slope_200,
            high_52w, low_52w,
            RS_rating

    config : dict
        Application configuration dict (loaded from settings.yaml).
        Keys consumed from config["trend_template"]:
            pct_above_52w_low   (float, default 25.0)
            pct_below_52w_high  (float, default 25.0)
            min_rs_rating       (int,   default 70)
            ma200_slope_lookback (int,  logging context only)

    Returns
    ───────
    TrendTemplateResult
        Always returned — never raises unless a required column is
        missing or NaN (see _require_value).

    Raises
    ──────
    RuleEngineError
        If any required feature column is absent or NaN.
    """
    tt_cfg: dict = config.get("trend_template", {})
    pct_above_52w_low: float  = float(tt_cfg.get("pct_above_52w_low",  25.0))
    pct_below_52w_high: float = float(tt_cfg.get("pct_below_52w_high", 25.0))
    min_rs_rating: int        = int(tt_cfg.get("min_rs_rating",         70))
    slope_lookback: int       = int(tt_cfg.get("ma200_slope_lookback",  20))

    log.debug(
        "check_trend_template called",
        pct_above_52w_low=pct_above_52w_low,
        pct_below_52w_high=pct_below_52w_high,
        min_rs_rating=min_rs_rating,
        ma200_slope_lookback=slope_lookback,
    )

    # ── Extract and validate all required feature columns ────────────────────
    close       = _require_value(row, "close")
    sma50       = _require_value(row, "SMA_50")
    sma150      = _require_value(row, "SMA_150")
    sma200      = _require_value(row, "SMA_200")
    slope200    = _require_value(row, "MA_slope_200")
    high_52w    = _require_value(row, "high_52w")
    low_52w     = _require_value(row, "low_52w")
    rs_rating   = _require_value(row, "RS_rating")

    conditions: dict[str, bool] = {}
    details:    dict[str, str]  = {}

    # ── C1: close > SMA_150 AND close > SMA_200 ──────────────────────────────
    c1 = close > sma150 and close > sma200
    conditions["C1"] = c1
    details["C1"] = (
        f"close {close:.2f} > SMA_150 {sma150:.2f} "
        f"AND > SMA_200 {sma200:.2f} {_mark(c1)}"
    )

    # ── C2: SMA_150 > SMA_200 ────────────────────────────────────────────────
    c2 = sma150 > sma200
    conditions["C2"] = c2
    details["C2"] = (
        f"SMA_150 {sma150:.2f} > SMA_200 {sma200:.2f} {_mark(c2)}"
    )

    # ── C3: MA_slope_200 > 0 ─────────────────────────────────────────────────
    c3 = slope200 > 0.0
    conditions["C3"] = c3
    details["C3"] = (
        f"MA_slope_200 {slope200:+.4f}%/day > 0 {_mark(c3)}"
    )

    # ── C4: SMA_50 > SMA_150 AND SMA_50 > SMA_200 ───────────────────────────
    c4 = sma50 > sma150 and sma50 > sma200
    conditions["C4"] = c4
    details["C4"] = (
        f"SMA_50 {sma50:.2f} > SMA_150 {sma150:.2f} "
        f"AND > SMA_200 {sma200:.2f} {_mark(c4)}"
    )

    # ── C5: close > SMA_50 ───────────────────────────────────────────────────
    c5 = close > sma50
    conditions["C5"] = c5
    details["C5"] = (
        f"close {close:.2f} > SMA_50 {sma50:.2f} {_mark(c5)}"
    )

    # ── C6: close >= low_52w * (1 + pct_above_52w_low / 100) ────────────────
    c6_threshold = low_52w * (1.0 + pct_above_52w_low / 100.0)
    c6 = close >= c6_threshold
    conditions["C6"] = c6
    details["C6"] = (
        f"close {close:.2f} >= low_52w {low_52w:.2f} "
        f"* {1.0 + pct_above_52w_low / 100.0:.2f} "
        f"(threshold {c6_threshold:.2f}) {_mark(c6)}"
    )

    # ── C7: close >= high_52w * (1 - pct_below_52w_high / 100) ─────────────
    #   "within 25% of 52w high" means close >= 0.75 * high_52w
    c7_threshold = high_52w * (1.0 - pct_below_52w_high / 100.0)
    c7 = close >= c7_threshold
    conditions["C7"] = c7
    details["C7"] = (
        f"close {close:.2f} >= high_52w {high_52w:.2f} "
        f"* {1.0 - pct_below_52w_high / 100.0:.2f} "
        f"(threshold {c7_threshold:.2f}) {_mark(c7)}"
    )

    # ── C8: RS_rating >= min_rs_rating ───────────────────────────────────────
    c8 = rs_rating >= min_rs_rating
    conditions["C8"] = c8
    details["C8"] = (
        f"RS_rating {int(rs_rating)} >= {min_rs_rating} {_mark(c8)}"
    )

    # ── Aggregate ─────────────────────────────────────────────────────────────
    conditions_met: int = sum(conditions.values())
    passes: bool = conditions_met == 8

    result = TrendTemplateResult(
        passes=passes,
        conditions=conditions,
        conditions_met=conditions_met,
        details=details,
    )

    log.info(
        "Trend template evaluated",
        passes=passes,
        conditions_met=conditions_met,
        close=close,
        rs_rating=int(rs_rating),
    )

    return result
