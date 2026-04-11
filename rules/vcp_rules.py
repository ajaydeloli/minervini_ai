"""
rules/vcp_rules.py
──────────────────
VCP qualification rule layer for the Minervini AI SEPA rule engine.

Overview
────────
This module is the rule-layer gate that sits *above* the VCP feature
computation (features/vcp.py).  By the time check_vcp() is called,
features/vcp.py has already run the chosen detector and written
per-metric columns to the feature DataFrame.  This module:

  1. Reads those pre-computed vcp_* columns from the last feature row.
  2. Short-circuits to FAIL when vcp_is_valid is already False (the
     feature layer already evaluated core rules).
  3. Applies any additional rule-layer checks that live in config["vcp"]
     but were not enforced by the detector (e.g. max_contractions, which
     is a screening preference, not a pattern-integrity rule).
  4. Assigns a quality grade (A / B / C / FAIL) and returns a fully
     populated VCPQualification.

Prerequisites
─────────────
features/vcp.py must have been run so that the following columns exist
on the feature DataFrame row passed to check_vcp():

    vcp_contraction_count   numeric
    vcp_max_depth_pct       numeric
    vcp_final_depth_pct     numeric
    vcp_vol_ratio           numeric
    vcp_base_weeks          numeric
    vcp_is_valid            bool
    vcp_fail_reason         str | None

Public API
──────────
    check_vcp(row, config)    → VCPQualification
    get_vcp_score(qual)       → float  (0–100)

Fail-loud contract  (PROJECT_DESIGN.md §19.1)
─────────────────────────────────────────────
    NaN or missing vcp_* columns → RuleEngineError raised immediately.
    Qualification failures are captured in fail_reason, never raised.

Config keys consumed (config["vcp"])
─────────────────────────────────────
    min_contractions       int    (default 2)
    max_contractions       int    (default 5)
    require_declining_depth bool  (informational — already enforced by feature layer)
    require_vol_contraction bool  (informational — already enforced by feature layer)
    min_weeks              int    (default 3)
    max_weeks              int    (default 52)
    tightness_pct          float  (default 10.0)
    max_depth_pct          float  (default 50.0)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from utils.exceptions import RuleEngineError
from utils.logger import get_logger

log = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# VCPQualification dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class VCPQualification:
    """
    Immutable result returned by check_vcp().

    Fields
    ──────
    qualified : bool
        True when all rule-layer checks pass AND vcp_is_valid was True.

    contraction_count : int
        Number of confirmed contraction legs detected by features/vcp.py.

    max_depth_pct : float
        Deepest correction across all legs (%).

    final_depth_pct : float
        Most-recent (shallowest) correction (%).

    vol_ratio : float
        Average volume in last leg / average volume in first leg.
        Values < 1.0 indicate volume contraction (desired).

    base_weeks : int
        Base length expressed in calendar weeks.

    fail_reason : str | None
        Human-readable failure description, or None when qualified.

    quality_grade : str
        "A", "B", "C", or "FAIL" — see grading criteria below.

    Grading criteria
    ────────────────
        Grade A  : qualified AND contraction_count >= 3
                   AND vol_ratio < 0.5
                   AND final_depth_pct < 5.0
        Grade B  : qualified AND NOT Grade A
                   AND contraction_count >= 2
                   AND vol_ratio < 0.8
        Grade C  : qualified AND NOT Grade A AND NOT Grade B
        FAIL     : not qualified
    """

    qualified: bool
    contraction_count: int
    max_depth_pct: float
    final_depth_pct: float
    vol_ratio: float
    base_weeks: int
    fail_reason: Optional[str]
    quality_grade: str  # "A", "B", "C", or "FAIL"


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

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
            "Ensure features/vcp.py has been run for this symbol.",
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


def _require_bool(row: pd.Series, col_name: str) -> bool:
    """
    Extract *col_name* from *row* as a bool.

    Handles pandas nullable boolean (pd.NA) — treats NA as False and
    raises RuleEngineError when the column is absent.

    Args:
        row:      Most recent feature row as a pd.Series.
        col_name: Column name to extract.

    Returns:
        Python bool.

    Raises:
        RuleEngineError: If the column is absent from the index.
    """
    if col_name not in row.index:
        raise RuleEngineError(
            f"Required feature column '{col_name}' is missing from the feature row. "
            "Ensure features/vcp.py has been run for this symbol.",
            column=col_name,
        )
    val = row[col_name]
    # pd.NA is falsy in boolean context but raises TypeError on `is None`
    try:
        return bool(val)
    except (TypeError, ValueError):
        return False


def _require_optional_str(row: pd.Series, col_name: str) -> Optional[str]:
    """
    Extract *col_name* from *row* as an optional str.

    Returns None when the value is None, pd.NA, or NaN.

    Raises:
        RuleEngineError: If the column is absent from the index.
    """
    if col_name not in row.index:
        raise RuleEngineError(
            f"Required feature column '{col_name}' is missing from the feature row. "
            "Ensure features/vcp.py has been run for this symbol.",
            column=col_name,
        )
    val = row[col_name]
    if val is None:
        return None
    try:
        if isinstance(val, float) and math.isnan(val):
            return None
    except (TypeError, ValueError):
        pass
    # pd.NA evaluates to False in truth-test but has str() representation
    import pandas as _pd
    if _pd.isna(val):
        return None
    return str(val)


def _assign_grade(
    qualified: bool,
    contraction_count: int,
    vol_ratio: float,
    final_depth_pct: float,
) -> str:
    """
    Assign a quality grade to a VCP qualification result.

    Grade A  : qualified AND contraction_count >= 3
               AND vol_ratio < 0.5
               AND final_depth_pct < 5.0
    Grade B  : qualified AND NOT Grade A
               AND contraction_count >= 2
               AND vol_ratio < 0.8
    Grade C  : qualified AND NOT Grade A AND NOT Grade B
    FAIL     : not qualified

    Args:
        qualified:         Qualification flag from check_vcp().
        contraction_count: Number of contraction legs.
        vol_ratio:         Last-leg vol / first-leg vol.
        final_depth_pct:   Most-recent contraction depth (%).

    Returns:
        One of "A", "B", "C", or "FAIL".
    """
    if not qualified:
        return "FAIL"

    if (
        contraction_count >= 3
        and vol_ratio < 0.5
        and final_depth_pct < 5.0
    ):
        return "A"

    if contraction_count >= 2 and vol_ratio < 0.8:
        return "B"

    return "C"


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def check_vcp(row: pd.Series, config: dict) -> VCPQualification:
    """
    Apply VCP qualification logic to the most recent feature row.

    This function is the rule-layer gate and intentionally stays thin:
    core VCP integrity rules (declining depth, vol contraction, base
    length, tightness) were already enforced by features/vcp.py.  Here
    we short-circuit on vcp_is_valid=False and then run the one
    rule-layer-only check: max_contractions (a screener preference that
    caps how complex/extended a base may be).

    Parameters
    ──────────
    row : pd.Series
        Most recent row from the feature DataFrame (iloc[-1]).
        Required columns:
            vcp_contraction_count, vcp_max_depth_pct,
            vcp_final_depth_pct, vcp_vol_ratio,
            vcp_base_weeks, vcp_is_valid, vcp_fail_reason

    config : dict
        Full application configuration dict (loaded from settings.yaml).
        Keys consumed from config["vcp"]:
            max_contractions   int  (default 5)

    Returns
    ───────
    VCPQualification
        Always returned; never raises unless a required column is
        missing or NaN (see _require_value / _require_bool).

    Raises
    ──────
    RuleEngineError
        If any required vcp_* feature column is absent or NaN.
    """
    vcp_cfg: dict = config.get("vcp", {})
    max_contractions: int = int(vcp_cfg.get("max_contractions", 5))

    log.debug(
        "check_vcp called",
        max_contractions=max_contractions,
    )

    # ── Extract and validate all required vcp_* feature columns ─────────────
    contraction_count: int = int(_require_value(row, "vcp_contraction_count"))
    max_depth_pct: float   = _require_value(row, "vcp_max_depth_pct")
    final_depth_pct: float = _require_value(row, "vcp_final_depth_pct")
    vol_ratio: float       = _require_value(row, "vcp_vol_ratio")
    base_weeks: int        = int(_require_value(row, "vcp_base_weeks"))
    is_valid: bool         = _require_bool(row, "vcp_is_valid")
    feat_fail_reason: Optional[str] = _require_optional_str(row, "vcp_fail_reason")

    # ── Short-circuit: feature layer already failed this VCP ─────────────────
    if not is_valid:
        grade = "FAIL"
        result = VCPQualification(
            qualified=False,
            contraction_count=contraction_count,
            max_depth_pct=max_depth_pct,
            final_depth_pct=final_depth_pct,
            vol_ratio=vol_ratio,
            base_weeks=base_weeks,
            fail_reason=feat_fail_reason,
            quality_grade=grade,
        )
        log.info(
            "VCP qualification: FAIL (feature layer)",
            fail_reason=feat_fail_reason,
        )
        return result

    # ── Rule-layer check: max_contractions ──────────────────────────────────
    # The feature layer enforces min_contractions; this module enforces
    # max_contractions, which is a screener preference (not a pattern rule).
    fail_reason: Optional[str] = None

    if contraction_count > max_contractions:
        fail_reason = (
            f"contraction_count {contraction_count} > "
            f"max_contractions {max_contractions}"
        )

    qualified: bool = fail_reason is None
    grade: str = _assign_grade(
        qualified, contraction_count, vol_ratio, final_depth_pct
    )

    result = VCPQualification(
        qualified=qualified,
        contraction_count=contraction_count,
        max_depth_pct=max_depth_pct,
        final_depth_pct=final_depth_pct,
        vol_ratio=vol_ratio,
        base_weeks=base_weeks,
        fail_reason=fail_reason,
        quality_grade=grade,
    )

    log.info(
        "VCP qualification evaluated",
        qualified=qualified,
        quality_grade=grade,
        contraction_count=contraction_count,
        vol_ratio=round(vol_ratio, 3),
        final_depth_pct=round(final_depth_pct, 2),
    )

    return result


def get_vcp_score(qual: VCPQualification) -> float:
    """
    Compute a 0–100 score for the VCP component of the composite SEPA score.

    Score formula by grade
    ──────────────────────
    FAIL → 0.0

    Grade C  → 40–59
        Base = 40
        Contraction bonus: min(contraction_count - 2, 2) * 5   (0–10 pts)
        Vol bonus: max(0.0, (1.0 - vol_ratio) * 10)            (0–10 pts, capped)
        score_C = 40 + contraction_bonus + vol_bonus            (range 40–60, clamped 59)

    Grade B  → 60–79
        Base = 60
        Contraction bonus: min(contraction_count - 2, 3) * 4   (0–12 pts)
        Vol bonus: max(0.0, (1.0 - vol_ratio) * 10)            (0–10 pts, capped)
        score_B = 60 + contraction_bonus + vol_bonus            (range 60–82, clamped 79)

    Grade A  → 80–100
        Base = 80
        Contraction bonus: min(contraction_count - 3, 2) * 5   (0–10 pts)
        Vol bonus: max(0.0, (0.5 - vol_ratio) * 20)            (0–10 pts, capped; ratio < 0.5)
        score_A = 80 + contraction_bonus + vol_bonus            (range 80–100, clamped 100)

    Parameters
    ──────────
    qual : VCPQualification
        Returned by check_vcp().

    Returns
    ───────
    float in [0.0, 100.0].
    """
    grade = qual.quality_grade

    if grade == "FAIL":
        return 0.0

    cnt = qual.contraction_count
    vol = qual.vol_ratio

    if grade == "C":
        base = 40.0
        cnt_bonus = min(max(cnt - 2, 0), 2) * 5.0
        vol_bonus = min(max((1.0 - vol) * 10.0, 0.0), 10.0)
        score = base + cnt_bonus + vol_bonus
        return min(score, 59.0)

    if grade == "B":
        base = 60.0
        cnt_bonus = min(max(cnt - 2, 0), 3) * 4.0
        vol_bonus = min(max((1.0 - vol) * 10.0, 0.0), 10.0)
        score = base + cnt_bonus + vol_bonus
        return min(score, 79.0)

    # Grade A
    base = 80.0
    cnt_bonus = min(max(cnt - 3, 0), 2) * 5.0
    vol_bonus = min(max((0.5 - vol) * 20.0, 0.0), 10.0)
    score = base + cnt_bonus + vol_bonus
    return min(score, 100.0)
