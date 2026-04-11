"""
rules/entry_trigger.py
──────────────────────
Pivot breakout entry-trigger rule for the Minervini AI SEPA rule engine.

Overview
────────
This module is the final rule-layer gate.  It is called only for stocks that
have already passed Stage 2, Trend Template, and VCP qualification.  It asks
one question: did the stock break out above its most recent confirmed pivot
high on convincing volume today?

Breakout definition (PROJECT_DESIGN.md §7.5)
─────────────────────────────────────────────
    1. close > last_pivot_high
       The closing price must clear the most recent confirmed swing high
       produced by features/pivot.py.

    2. volume >= vol_50d_avg * breakout_vol_multiplier  (default 1.5×)
       Institutional participation is confirmed when the breakout day's
       volume is at least 1.5× the 50-day average volume produced by
       features/volume.py.

    Both conditions must hold for triggered=True.

Prerequisites
─────────────
The feature row passed to check_entry_trigger() must contain columns
produced by the following modules:
    features/pivot.py   → last_pivot_high, last_pivot_low
    features/volume.py  → vol_50d_avg
    (OHLCV)             → close, volume

Public API
──────────
    check_entry_trigger(row, config) → EntryTrigger

Fail-loud contract  (PROJECT_DESIGN.md §19.1)
─────────────────────────────────────────────
    Missing or NaN required columns (close, volume, vol_50d_avg) →
        RuleEngineError raised immediately.

    NaN last_pivot_high → triggered=False, reason explains the gap.
    This is NOT an error: a stock may legitimately have no confirmed
    pivot yet (e.g. too few bars).

Config keys consumed (config["entry"])
──────────────────────────────────────
    breakout_vol_multiplier : float  (default 1.5)
        Volume on breakout day must be >= vol_50d_avg * this factor.

    pivot_lookback_days : int  (default 60)
        Informational context only at this layer — the lookback
        filtering happens upstream in features/pivot.py.  Stored
        here so the reason string can reference it when no pivot is
        available within the window.

Design rules (PROJECT_DESIGN.md §4.2, §19.2)
─────────────────────────────────────────────
    • Pure function — no class, no global state, no I/O.
    • Always returns an EntryTrigger — never raises for logic failures.
    • Missing / NaN mandatory columns raise RuleEngineError (fail loud).
    • NaN last_pivot_high → graceful non-triggered result, not an error.
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
# Defaults
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_BREAKOUT_VOL_MULTIPLIER: float = 1.5
_DEFAULT_PIVOT_LOOKBACK_DAYS: int = 60


# ─────────────────────────────────────────────────────────────────────────────
# EntryTrigger dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class EntryTrigger:
    """
    Immutable result returned by check_entry_trigger().

    Fields
    ──────
    triggered : bool
        True when BOTH conditions hold:
            close > last_pivot_high
            volume >= vol_50d_avg * breakout_vol_multiplier

    entry_price : float | None
        Closing price on the breakout day (the natural entry price),
        or None when triggered is False.

    pivot_high : float | None
        The pivot level that was broken, or None when no pivot was
        available (last_pivot_high is NaN).

    breakout_vol_ratio : float | None
        volume / vol_50d_avg on the breakout day, or None when
        vol_50d_avg is NaN/zero (safe-divide guard).

    volume_confirmed : bool
        True when volume >= vol_50d_avg * breakout_vol_multiplier,
        regardless of whether the price condition was met.

    reason : str
        Human-readable one-line summary.
        Examples:
            "breakout above pivot 142.00 on 2.3x avg vol"
            "no breakout: close 138.00 < pivot 142.00 (vol 1.8x)"
            "no breakout: close 145.00 > pivot 142.00 but vol 1.2x < 1.5x required"
            "no pivot high available (lookback 60 days)"
    """

    triggered: bool
    entry_price: Optional[float]
    pivot_high: Optional[float]
    breakout_vol_ratio: Optional[float]
    volume_confirmed: bool
    reason: str


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


def _safe_vol_ratio(volume: float, vol_50d_avg: float) -> Optional[float]:
    """
    Compute volume / vol_50d_avg, returning None if the denominator is
    zero or not finite (avoids ZeroDivisionError and inf).
    """
    if vol_50d_avg == 0.0 or not math.isfinite(vol_50d_avg):
        return None
    return volume / vol_50d_avg


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def check_entry_trigger(row: pd.Series, config: dict) -> EntryTrigger:
    """
    Detect whether the most recent bar constitutes a valid pivot breakout.

    Parameters
    ──────────
    row : pd.Series
        The most recent row of the feature DataFrame (iloc[-1]).
        Required columns (mandatory — RuleEngineError if absent/NaN):
            close, volume, vol_50d_avg
        Optional columns (graceful non-triggered if NaN):
            last_pivot_high

    config : dict
        Application configuration dict (loaded from settings.yaml).
        Keys consumed from config["entry"]:
            breakout_vol_multiplier  float  (default 1.5)
            pivot_lookback_days      int    (default 60)

    Returns
    ───────
    EntryTrigger
        Always returned.  triggered=True only when close > last_pivot_high
        AND volume >= vol_50d_avg * breakout_vol_multiplier.

    Raises
    ──────
    RuleEngineError
        If close, volume, or vol_50d_avg is absent from the row or NaN.
    """
    entry_cfg: dict = config.get("entry", {})
    vol_multiplier: float = float(
        entry_cfg.get("breakout_vol_multiplier", _DEFAULT_BREAKOUT_VOL_MULTIPLIER)
    )
    lookback_days: int = int(
        entry_cfg.get("pivot_lookback_days", _DEFAULT_PIVOT_LOOKBACK_DAYS)
    )

    log.debug(
        "check_entry_trigger called",
        breakout_vol_multiplier=vol_multiplier,
        pivot_lookback_days=lookback_days,
    )

    # ── Extract mandatory columns (fail loud on missing / NaN) ───────────────
    close: float    = _require_value(row, "close")
    volume: float   = _require_value(row, "volume")
    vol_avg: float  = _require_value(row, "vol_50d_avg")

    # ── Extract last_pivot_high (NaN is acceptable — no pivot yet) ───────────
    pivot_high: Optional[float] = None
    if "last_pivot_high" not in row.index:
        raise RuleEngineError(
            "Required feature column 'last_pivot_high' is missing from the feature row. "
            "Ensure features/pivot.py has been run for this symbol.",
            column="last_pivot_high",
        )

    raw_ph = row["last_pivot_high"]
    pivot_is_nan = (
        raw_ph is None
        or (isinstance(raw_ph, float) and math.isnan(raw_ph))
        or pd.isna(raw_ph)
    )

    if pivot_is_nan:
        # Graceful non-triggered result — not an error
        reason = f"no pivot high available (lookback {lookback_days} days)"
        log.info("Entry trigger: no pivot", reason=reason, close=close)
        return EntryTrigger(
            triggered=False,
            entry_price=None,
            pivot_high=None,
            breakout_vol_ratio=None,
            volume_confirmed=False,
            reason=reason,
        )

    pivot_high = float(raw_ph)

    # ── Volume ratio ─────────────────────────────────────────────────────────
    vol_ratio: Optional[float] = _safe_vol_ratio(volume, vol_avg)

    # ── Volume confirmation check ─────────────────────────────────────────────
    vol_threshold: float = vol_avg * vol_multiplier
    volume_confirmed: bool = volume >= vol_threshold

    # ── Price breakout check ──────────────────────────────────────────────────
    price_breakout: bool = close > pivot_high

    # ── Build reason string ──────────────────────────────────────────────────
    vol_ratio_str = f"{vol_ratio:.1f}x" if vol_ratio is not None else "n/a"

    if price_breakout and volume_confirmed:
        triggered = True
        entry_price: Optional[float] = close
        reason = (
            f"breakout above pivot {pivot_high:.2f} on {vol_ratio_str} avg vol"
        )
    elif price_breakout and not volume_confirmed:
        triggered = False
        entry_price = None
        reason = (
            f"no breakout: close {close:.2f} > pivot {pivot_high:.2f} "
            f"but vol {vol_ratio_str} < {vol_multiplier:.1f}x required"
        )
    else:
        triggered = False
        entry_price = None
        reason = (
            f"no breakout: close {close:.2f} < pivot {pivot_high:.2f} "
            f"(vol {vol_ratio_str})"
        )

    result = EntryTrigger(
        triggered=triggered,
        entry_price=entry_price,
        pivot_high=pivot_high,
        breakout_vol_ratio=vol_ratio,
        volume_confirmed=volume_confirmed,
        reason=reason,
    )

    log.info(
        "Entry trigger evaluated",
        triggered=triggered,
        close=close,
        pivot_high=pivot_high,
        vol_ratio=round(vol_ratio, 3) if vol_ratio is not None else None,
        volume_confirmed=volume_confirmed,
    )

    return result
