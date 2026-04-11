"""
rules/risk_reward.py
────────────────────
Reward:Risk ratio estimator for the Minervini AI SEPA rule engine.

Overview
────────
This module estimates the Reward:Risk (R:R) ratio for a SEPA / VCP setup
by locating the nearest swing-high resistance above the entry price and
using it as the profit target.

Target resolution order
───────────────────────
    1. last_pivot_high  — swing-high pivot from features/pivot.py.
                          Used only when it is above entry_price.
    2. high_52w         — 52-week high.
                          Used as fallback when last_pivot_high is NaN or
                          <= entry_price.
    3. Synthetic target — entry_price × (1 + default_target_pct / 100).
                          Used when neither resistance level is available.
                          Sets has_resistance=False.

Fail-loud contract  (PROJECT_DESIGN.md §19.1)
─────────────────────────────────────────────
    Both last_pivot_high AND high_52w columns are REQUIRED to be present
    in the feature row.  A missing column (KeyError) means the feature
    pipeline was not run — this is a wiring bug, not a data-quality issue.
    → raise RuleEngineError immediately.

    NaN values in either column are valid data states handled by the
    fallback chain above.

Public API
──────────
    compute_rr(row, entry_price, stop_loss, config) → RRResult

Config keys consumed (config["risk_reward"])
────────────────────────────────────────────
    default_target_pct : float  (default 20.0)
        Percentage above entry_price used as the synthetic profit target
        when no swing-high resistance is found.

    All keys are optional; if config["risk_reward"] is absent the module
    falls back to defaults.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from utils.exceptions import RuleEngineError
from utils.logger import get_logger

log = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Defaults
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_TARGET_PCT: float = 20.0


# ─────────────────────────────────────────────────────────────────────────────
# RRResult dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RRResult:
    """
    Immutable result returned by compute_rr().

    Fields
    ──────
    target_price : float
        Nearest swing-high above entry used as the profit target.
        Falls back to a synthetic target (entry × 1.20 by default) when
        no resistance level is found.

    rr_ratio : float
        (target_price − entry_price) / (entry_price − stop_loss).
        Returns 0.0 when entry_price == stop_loss (zero-denominator guard).

    reward_pct : float
        (target_price − entry_price) / entry_price × 100.

    risk_pct : float
        (entry_price − stop_loss) / entry_price × 100.

    has_resistance : bool
        True when a real swing-high resistance level (pivot or 52w high)
        was found above entry_price.  False when the synthetic target is
        being used.
    """

    target_price: float
    rr_ratio: float
    reward_pct: float
    risk_pct: float
    has_resistance: bool


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _require_column(row: pd.Series, col_name: str) -> None:
    """
    Assert that *col_name* is present in *row*.

    Raises:
        RuleEngineError: If the column is absent from the row index.
    """
    if col_name not in row.index:
        raise RuleEngineError(
            f"Required feature column '{col_name}' is missing from the feature row. "
            "Ensure the relevant feature module has been run for this symbol.",
            column=col_name,
        )


def _is_nan(value: object) -> bool:
    """Return True when *value* is None, pd.NA, or a NaN float."""
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _get_optional_float(row: pd.Series, col_name: str) -> float | None:
    """
    Return *col_name* from *row* as a float, or None if NaN / NA.

    Raises:
        RuleEngineError: If the column is absent from the row index.
    """
    _require_column(row, col_name)
    val = row[col_name]
    if _is_nan(val):
        return None
    return float(val)


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def compute_rr(
    row: pd.Series,
    entry_price: float,
    stop_loss: float,
    config: dict,
) -> RRResult:
    """
    Estimate the Reward:Risk ratio for a SEPA setup.

    Parameters
    ──────────
    row : pd.Series
        Most recent row from the feature DataFrame (iloc[-1]).
        Required columns (must exist in index — NaN values are acceptable):
            last_pivot_high  — float | NaN; nearest swing-high pivot
            high_52w         — float | NaN; 52-week high

    entry_price : float
        Price at which the position would be entered.

    stop_loss : float
        Stop-loss price (computed by rules.stop_loss.compute_stop_loss).

    config : dict
        Full application configuration dict.
        Keys consumed from config["risk_reward"]:
            default_target_pct  float  (default 20.0)

    Returns
    ───────
    RRResult

    Raises
    ──────
    RuleEngineError
        If last_pivot_high OR high_52w is absent from the row index entirely.
    """
    rr_cfg: dict = config.get("risk_reward", {})
    default_target_pct: float = float(
        rr_cfg.get("default_target_pct", _DEFAULT_TARGET_PCT)
    )

    log.debug(
        "compute_rr called",
        entry_price=entry_price,
        stop_loss=stop_loss,
        default_target_pct=default_target_pct,
    )

    # ── Require both columns to exist (KeyError → fail loudly) ───────────────
    _require_column(row, "last_pivot_high")
    _require_column(row, "high_52w")

    # ── Read values (NaN → None) ──────────────────────────────────────────────
    pivot_high: float | None = _get_optional_float(row, "last_pivot_high")
    high_52w: float | None   = _get_optional_float(row, "high_52w")

    # ── Resolve target price ──────────────────────────────────────────────────
    target_price: float
    has_resistance: bool

    if pivot_high is not None and pivot_high > entry_price:
        target_price = pivot_high
        has_resistance = True
        log.debug("Target: last_pivot_high", target_price=target_price)

    elif high_52w is not None and high_52w > entry_price:
        target_price = high_52w
        has_resistance = True
        log.debug("Target: high_52w fallback", target_price=target_price)

    else:
        target_price = entry_price * (1.0 + default_target_pct / 100.0)
        has_resistance = False
        log.debug(
            "Target: synthetic default",
            target_price=target_price,
            default_target_pct=default_target_pct,
        )

    # ── Compute ratios ────────────────────────────────────────────────────────
    denominator = entry_price - stop_loss
    if denominator == 0.0:
        rr_ratio = 0.0
        log.debug("Zero denominator: rr_ratio set to 0.0")
    else:
        rr_ratio = round((target_price - entry_price) / denominator, 4)

    reward_pct = round((target_price - entry_price) / entry_price * 100.0, 4)
    risk_pct   = round((entry_price - stop_loss)    / entry_price * 100.0, 4)

    result = RRResult(
        target_price=target_price,
        rr_ratio=rr_ratio,
        reward_pct=reward_pct,
        risk_pct=risk_pct,
        has_resistance=has_resistance,
    )

    log.info(
        "R:R computed",
        target_price=round(target_price, 4),
        rr_ratio=rr_ratio,
        reward_pct=reward_pct,
        risk_pct=risk_pct,
        has_resistance=has_resistance,
    )

    return result
