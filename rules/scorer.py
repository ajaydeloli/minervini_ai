"""
rules/scorer.py
───────────────
SEPA composite scorer for the Minervini AI rule engine.

Overview
────────
This module is the capstone of Phase 3.  It assembles the outputs of every
upstream rule module into a single, fully structured SEPAResult and computes
a deterministic 0–100 composite score.

Design mandates (PROJECT_DESIGN.md §4.3, §19.2)
─────────────────────────────────────────────────
    • Stage 2 is a HARD GATE.  Non-Stage-2 stocks receive score=0 and
      setup_quality="FAIL" regardless of any other condition.
    • The composite score is fully configurable via config["scoring"]["weights"].
    • All component scores are normalised to 0–100 before weighting.
    • fundamental_score and news_score are placeholder zeros (Phase 5 wires them).
    • to_dict() produces a flat JSON-serialisable dict for storage / API use.

Scoring weights (settings.yaml → config["scoring"]["weights"])
───────────────────────────────────────────────────────────────
    rs_rating   0.30
    trend       0.25
    vcp         0.25
    volume      0.10
    fundamental 0.07
    news        0.03

Quality tags (PROJECT_DESIGN.md §7.4)
──────────────────────────────────────
    A+   score >= 85  AND stage == 2  AND all 8 TT conditions pass  AND vcp_qualified
    A    score >= 70  AND stage == 2  AND all 8 TT conditions pass
    B    score >= 55  AND stage == 2  AND conditions_met >= 6
    C    score >= 40  AND stage == 2
    FAIL not Stage 2  OR  score < 40  OR  conditions_met < 6

Public API
──────────
    evaluate(symbol, date, row, stage_result, tt_result, vcp_qual,
             entry_trigger, stop_result, config) → SEPAResult
    compute_volume_score(row)                    → float
    to_dict(result)                              → dict
"""

from __future__ import annotations

import datetime
import math
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

import pandas as pd

from rules.entry_trigger import EntryTrigger
from rules.risk_reward import RRResult
from rules.stage import StageResult
from rules.stop_loss import StopLossResult
from rules.trend_template import TrendTemplateResult
from rules.vcp_rules import VCPQualification, get_vcp_score
from utils.logger import get_logger

log = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Default weight constants (overridden by config at runtime)
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_WEIGHTS: dict[str, float] = {
    "rs_rating":   0.30,
    "trend":       0.25,
    "vcp":         0.25,
    "volume":      0.10,
    "fundamental": 0.07,
    "news":        0.03,
}

_DEFAULT_THRESHOLDS: dict[str, int] = {
    "a_plus": 85,
    "a":      70,
    "b":      55,
    "c":      40,
}


# ─────────────────────────────────────────────────────────────────────────────
# SEPAResult dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SEPAResult:
    """
    Structured output of one full SEPA evaluation pass for a single symbol.

    Fields are documented in PROJECT_DESIGN.md §4.3.  Phase-5 placeholders
    (fundamental_pass, fundamental_details, news_score) default to their
    neutral values so that Phase 3 callers do not need to supply them.
    """

    # ── Identity ──────────────────────────────────────────────────────────────
    symbol: str
    date: datetime.date

    # ── Stage classification ──────────────────────────────────────────────────
    stage: int
    stage_label: str
    stage_confidence: int

    # ── Trend Template ────────────────────────────────────────────────────────
    trend_template_pass: bool
    trend_template_details: dict[str, bool]
    conditions_met: int

    # ── VCP ───────────────────────────────────────────────────────────────────
    vcp_qualified: bool
    vcp_grade: str
    vcp_details: dict[str, Any]

    # ── Entry / Stop ──────────────────────────────────────────────────────────
    breakout_triggered: bool
    entry_price: Optional[float]
    stop_loss: Optional[float]
    stop_type: Optional[str]
    risk_pct: Optional[float]

    # ── Relative Strength ─────────────────────────────────────────────────────
    rs_rating: int

    # ── Reward:Risk (wired in Phase 4 — None when no entry triggered) ─────────
    rr_ratio:      Optional[float] = None   # (target − entry) / (entry − stop)
    target_price:  Optional[float] = None   # nearest swing-high resistance used
    reward_pct:    Optional[float] = None   # (target − entry) / entry × 100
    has_resistance: Optional[bool] = None   # True when a real pivot / 52w was used

    # ── Phase-5 placeholders (wired in Phase 5) ───────────────────────────────
    fundamental_pass: bool = False
    fundamental_details: dict = field(default_factory=dict)
    news_score: Optional[float] = None

    # ── Final output ──────────────────────────────────────────────────────────
    setup_quality: Literal["A+", "A", "B", "C", "FAIL"] = "FAIL"
    score: int = 0


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _safe_float(val: Any) -> Optional[float]:
    """Return val as float, or None if it is None / NaN."""
    if val is None:
        return None
    try:
        f = float(val)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _get_weights(config: dict) -> dict[str, float]:
    """Extract scoring weights from config, falling back to defaults."""
    raw = config.get("scoring", {}).get("weights", {})
    weights = dict(_DEFAULT_WEIGHTS)
    for key in weights:
        if key in raw:
            weights[key] = float(raw[key])
    return weights


def _get_thresholds(config: dict) -> dict[str, int]:
    """Extract setup_quality_thresholds from config, falling back to defaults."""
    raw = config.get("scoring", {}).get("setup_quality_thresholds", {})
    thresholds = dict(_DEFAULT_THRESHOLDS)
    for key in thresholds:
        if key in raw:
            thresholds[key] = int(raw[key])
    return thresholds


def _assign_quality(
    score: int,
    stage: int,
    conditions_met: int,
    trend_template_pass: bool,
    vcp_qualified: bool,
    thresholds: dict[str, int],
) -> Literal["A+", "A", "B", "C", "FAIL"]:
    """
    Assign the setup quality tag based on score, stage, and rule conditions.

    Quality rules (PROJECT_DESIGN.md §7.4)
    ────────────────────────────────────────
    A+   score >= a_plus AND stage == 2 AND all 8 TT pass AND vcp_qualified
    A    score >= a      AND stage == 2 AND all 8 TT pass
    B    score >= b      AND stage == 2 AND conditions_met >= 6
    C    score >= c      AND stage == 2
    FAIL otherwise
    """
    if stage != 2 or score < thresholds["c"] or conditions_met < 6:
        return "FAIL"

    if (
        score >= thresholds["a_plus"]
        and trend_template_pass
        and vcp_qualified
    ):
        return "A+"

    if score >= thresholds["a"] and trend_template_pass:
        return "A"

    if score >= thresholds["b"] and conditions_met >= 6:
        return "B"

    if score >= thresholds["c"]:
        return "C"

    return "FAIL"


# ─────────────────────────────────────────────────────────────────────────────
# Public helpers
# ─────────────────────────────────────────────────────────────────────────────

def compute_volume_score(row: pd.Series) -> float:
    """
    Compute a 0–100 volume component score from the feature row.

    Uses the pre-computed ``vol_ratio`` column (today's volume / 50-day avg).

    Scoring table
    ─────────────
    vol_ratio >= 2.0  → 100
    vol_ratio >= 1.5  → 80
    vol_ratio >= 1.0  → 60
    vol_ratio <  1.0  → scaled: vol_ratio / 1.0 * 60 (linear 0–60)
    NaN               → 50  (neutral — data not yet available)

    Parameters
    ──────────
    row : pd.Series
        Most recent feature row.  Reads the ``vol_ratio`` column when present.

    Returns
    ───────
    float in [0.0, 100.0].
    """
    raw = row.get("vol_ratio", None) if hasattr(row, "get") else (
        row["vol_ratio"] if "vol_ratio" in row.index else None
    )

    if raw is None:
        return 50.0

    try:
        vol_ratio = float(raw)
    except (TypeError, ValueError):
        return 50.0

    if math.isnan(vol_ratio):
        return 50.0

    if vol_ratio >= 2.0:
        return 100.0
    if vol_ratio >= 1.5:
        return 80.0
    if vol_ratio >= 1.0:
        return 60.0

    # Linear scale from 0 to 60 for vol_ratio in [0, 1)
    return max(0.0, vol_ratio * 60.0)


# ─────────────────────────────────────────────────────────────────────────────
# Core evaluate function
# ─────────────────────────────────────────────────────────────────────────────

def evaluate(
    symbol: str,
    date: datetime.date,
    row: pd.Series,
    stage_result: StageResult,
    tt_result: TrendTemplateResult,
    vcp_qual: VCPQualification,
    entry_trigger: EntryTrigger,
    stop_result: Optional[StopLossResult],
    config: dict,
    rr_result: Optional[RRResult] = None,
    fundamental_result: Optional["FundamentalResult"] = None,   # from rules.fundamental_template
    news_score: Optional[float] = None,                          # from ingestion.news
) -> SEPAResult:
    """
    Assemble all rule module outputs into a SEPAResult with composite score.

    Parameters
    ──────────
    symbol        : Ticker symbol (e.g. "RELIANCE").
    date          : Evaluation date (most recent trading day).
    row           : Last feature row as a pd.Series (from feature_store).
    stage_result  : Output of rules.stage.detect_stage().
    tt_result     : Output of rules.trend_template.check_trend_template().
    vcp_qual      : Output of rules.vcp_rules.check_vcp().
    entry_trigger : Output of rules.entry_trigger.check_entry_trigger().
    stop_result   : Output of rules.stop_loss.compute_stop_loss(), or None
                    when the entry was not triggered.
    config        : Full application configuration dict (settings.yaml).
    rr_result     : Output of rules.risk_reward.compute_rr(), or None when
                    entry was not triggered or feature row lacked pivot data.

    Returns
    ───────
    SEPAResult
        score=0 and setup_quality="FAIL" when stage != 2 (hard gate).

    Notes
    ─────
    fundamental_result and news_score default to None (backward-compatible).
    When None, fundamental_score=0.0 and news_score_val=0.0 are used.
    """
    weights    = _get_weights(config)
    thresholds = _get_thresholds(config)

    # ── Extract rs_rating from the feature row ───────────────────────────────
    rs_raw = row["RS_rating"] if "RS_rating" in row.index else 0
    rs_rating: int = int(_safe_float(rs_raw) or 0)

    # ── Build VCP details dict ───────────────────────────────────────────────
    vcp_details: dict[str, Any] = {
        "contraction_count": vcp_qual.contraction_count,
        "max_depth_pct":     vcp_qual.max_depth_pct,
        "final_depth_pct":   vcp_qual.final_depth_pct,
        "vol_ratio":         vcp_qual.vol_ratio,
        "base_weeks":        vcp_qual.base_weeks,
        "fail_reason":       vcp_qual.fail_reason,
        "quality_grade":     vcp_qual.quality_grade,
    }

    # ── Stop-loss fields (None when stop was not computed) ───────────────────
    stop_loss:  Optional[float] = stop_result.stop_price if stop_result else None
    stop_type:  Optional[str]   = stop_result.stop_type  if stop_result else None
    risk_pct:   Optional[float] = stop_result.risk_pct   if stop_result else None

    # ── Reward:Risk fields (None when rr_result not provided) ────────────────
    rr_ratio:      Optional[float] = rr_result.rr_ratio      if rr_result else None
    target_price:  Optional[float] = rr_result.target_price  if rr_result else None
    reward_pct:    Optional[float] = rr_result.reward_pct    if rr_result else None
    has_resistance: Optional[bool] = rr_result.has_resistance if rr_result else None

    # ── HARD GATE: non-Stage-2 → score=0, quality=FAIL immediately ───────────
    if stage_result.stage != 2:
        result = SEPAResult(
            symbol=symbol,
            date=date,
            stage=stage_result.stage,
            stage_label=stage_result.label,
            stage_confidence=stage_result.confidence,
            trend_template_pass=tt_result.passes,
            trend_template_details=dict(tt_result.conditions),
            conditions_met=tt_result.conditions_met,
            vcp_qualified=vcp_qual.qualified,
            vcp_grade=vcp_qual.quality_grade,
            vcp_details=vcp_details,
            breakout_triggered=entry_trigger.triggered,
            entry_price=entry_trigger.entry_price,
            stop_loss=stop_loss,
            stop_type=stop_type,
            risk_pct=risk_pct,
            rr_ratio=None,
            target_price=None,
            reward_pct=None,
            has_resistance=None,
            rs_rating=rs_rating,
            setup_quality="FAIL",
            score=0,
        )
        log.info(
            "SEPA evaluate: non-Stage-2 hard gate",
            symbol=symbol,
            stage=stage_result.stage,
            score=0,
            setup_quality="FAIL",
        )
        return result

    # ── Component scores (each normalised to 0–100) ──────────────────────────
    rs_score:          float = min(float(rs_rating), 99.0)
    trend_score:       float = (tt_result.conditions_met / 8.0) * 100.0
    vcp_score:         float = get_vcp_score(vcp_qual)
    volume_score:      float = compute_volume_score(row)
    fundamental_score: float = fundamental_result.fundamental_score if fundamental_result else 0.0
    news_score_val:    float = max(0.0, (news_score + 100.0) / 2.0) if news_score is not None else 0.0
    # news_score is -100..+100; rescale to 0..100 for weighting

    # ── Weighted composite score ──────────────────────────────────────────────
    composite = (
        rs_score          * weights["rs_rating"]
        + trend_score     * weights["trend"]
        + vcp_score       * weights["vcp"]
        + volume_score    * weights["volume"]
        + fundamental_score * weights["fundamental"]
        + news_score_val  * weights["news"]
    )

    score: int = int(round(max(0.0, min(100.0, composite))))

    # ── Quality tag ──────────────────────────────────────────────────────────
    setup_quality = _assign_quality(
        score=score,
        stage=stage_result.stage,
        conditions_met=tt_result.conditions_met,
        trend_template_pass=tt_result.passes,
        vcp_qualified=vcp_qual.qualified,
        thresholds=thresholds,
    )

    result = SEPAResult(
        symbol=symbol,
        date=date,
        stage=stage_result.stage,
        stage_label=stage_result.label,
        stage_confidence=stage_result.confidence,
        trend_template_pass=tt_result.passes,
        trend_template_details=dict(tt_result.conditions),
        conditions_met=tt_result.conditions_met,
        vcp_qualified=vcp_qual.qualified,
        vcp_grade=vcp_qual.quality_grade,
        vcp_details=vcp_details,
        breakout_triggered=entry_trigger.triggered,
        entry_price=entry_trigger.entry_price,
        stop_loss=stop_loss,
        stop_type=stop_type,
        risk_pct=risk_pct,
        rr_ratio=rr_ratio,
        target_price=target_price,
        reward_pct=reward_pct,
        has_resistance=has_resistance,
        rs_rating=rs_rating,
        setup_quality=setup_quality,
        score=score,
        fundamental_pass=fundamental_result.passes if fundamental_result else False,
        fundamental_details=fundamental_result.conditions if fundamental_result else {},
    )

    log.info(
        "SEPA evaluate complete",
        symbol=symbol,
        date=str(date),
        stage=stage_result.stage,
        score=score,
        setup_quality=setup_quality,
        conditions_met=tt_result.conditions_met,
        vcp_qualified=vcp_qual.qualified,
        rs_rating=rs_rating,
    )

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Serialisation helper
# ─────────────────────────────────────────────────────────────────────────────

def to_dict(result: SEPAResult) -> dict:
    """
    Convert a SEPAResult to a flat, JSON-serialisable dict.

    datetime.date is serialised as an ISO-format string ("YYYY-MM-DD").
    None values are preserved as None (JSON null).
    Nested dicts (trend_template_details, vcp_details, fundamental_details)
    are included as-is (they contain only JSON-safe scalar values).

    Parameters
    ──────────
    result : SEPAResult

    Returns
    ───────
    dict with string keys and JSON-serialisable values.
    """
    return {
        "symbol":                  result.symbol,
        "date":                    result.date.isoformat(),
        "stage":                   result.stage,
        "stage_label":             result.stage_label,
        "stage_confidence":        result.stage_confidence,
        "trend_template_pass":     result.trend_template_pass,
        "trend_template_details":  result.trend_template_details,
        "conditions_met":          result.conditions_met,
        "vcp_qualified":           result.vcp_qualified,
        "vcp_grade":               result.vcp_grade,
        "vcp_details":             result.vcp_details,
        "breakout_triggered":      result.breakout_triggered,
        "entry_price":             result.entry_price,
        "stop_loss":               result.stop_loss,
        "stop_type":               result.stop_type,
        "risk_pct":                result.risk_pct,
        "rr_ratio":                result.rr_ratio,
        "target_price":            result.target_price,
        "reward_pct":              result.reward_pct,
        "has_resistance":          result.has_resistance,
        "rs_rating":               result.rs_rating,
        "fundamental_pass":        result.fundamental_pass,
        "fundamental_details":     result.fundamental_details,
        "news_score":              result.news_score,
        "setup_quality":           result.setup_quality,
        "score":                   result.score,
    }
