"""
features/volume.py
──────────────────
Pure function module — no classes, no side effects, no global state.

Computes and appends volume-based indicators to an OHLCV DataFrame
following the Minervini SEPA methodology.

Public API
──────────
    compute(df, config) → pd.DataFrame

        Appends these columns to a *copy* of df and returns it:

            vol_50d_avg      50-day simple moving average of volume
            vol_ratio        today's volume / vol_50d_avg
                             (e.g. 1.5 = volume is 50% above average)
            up_vol_days      count of days in last 20 where
                             close > open AND volume > vol_50d_avg
            down_vol_days    count of days in last 20 where
                             close < open AND volume > vol_50d_avg
            acc_dist_score   up_vol_days − down_vol_days  (−20 to +20)
                             positive → accumulation (institutions buying)
                             negative → distribution (selling pressure)

Fail-loud contract (PROJECT_DESIGN.md §19.1)
────────────────────────────────────────────
    len(df) < 50 → raises InsufficientDataError (cannot compute vol_50d_avg)

    NaN for the first 19 rows of up_vol_days / down_vol_days / acc_dist_score
    is acceptable (rolling 20-day window warmup).

    vol_ratio is set to NaN (not 0 or ∞) whenever vol_50d_avg is 0 or NaN
    for that row — division by zero is never performed.

Design rules (PROJECT_DESIGN.md §4.2, §19.2)
─────────────────────────────────────────────
    • Pure function — no class, no global state, no I/O.
    • Idempotent — calling compute() twice overwrites existing volume columns.
    • Never mutate the input df — always return a new DataFrame.
    • No TA-Lib — pandas + numpy only.
    • All thresholds come from the config dict; none are hardcoded here.

Volume in the scoring model (PROJECT_DESIGN.md §7.4)
─────────────────────────────────────────────────────
    SCORE_WEIGHTS["volume"] = 0.10
    Inputs used by scorer.py:
        vol_ratio       → breakout volume confirmation (today > 1.5× avg is strong)
        acc_dist_score  → accumulation / distribution signal (higher = more buying)

VCP volume dry-up flag (PROJECT_DESIGN.md §7.3)
────────────────────────────────────────────────
    vcp.py reads vol_50d_avg and vol_ratio from this module's output.
    A vol_ratio < 0.4 in the final base contraction is the "volume dry-up"
    signal (vol_dry_up_flag) used in VCP qualification.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from utils.exceptions import InsufficientDataError

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_VOL_MA_PERIOD: int = 50    # rows required for vol_50d_avg
_ACC_DIST_WINDOW: int = 20  # rolling window for up/down vol day counts


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    """
    Element-wise division that returns NaN wherever denominator is 0 or NaN.
    Never raises ZeroDivisionError.
    """
    with np.errstate(divide="ignore", invalid="ignore"):
        result = np.where(
            (denominator == 0) | denominator.isna(),
            np.nan,
            numerator / denominator,
        )
    return pd.Series(result, index=numerator.index, dtype=float)


def _rolling_up_vol_days(
    close: pd.Series,
    open_: pd.Series,
    volume: pd.Series,
    vol_avg: pd.Series,
    window: int,
) -> pd.Series:
    """
    For each row, count the number of days in the trailing *window* where:
        close > open  AND  volume > vol_50d_avg

    Uses a boolean indicator series + rolling sum, which avoids any Python
    loop and is fully vectorised.

    Returns NaN for the first (window − 1) rows (warmup).
    """
    is_up_vol = ((close > open_) & (volume > vol_avg)).astype(float)
    # NaN positions in vol_avg propagate into is_up_vol via the comparison;
    # ensure NaN rows don't silently count as 0.
    is_up_vol = is_up_vol.where(vol_avg.notna(), other=np.nan)
    return is_up_vol.rolling(window=window, min_periods=window).sum()


def _rolling_down_vol_days(
    close: pd.Series,
    open_: pd.Series,
    volume: pd.Series,
    vol_avg: pd.Series,
    window: int,
) -> pd.Series:
    """
    For each row, count the number of days in the trailing *window* where:
        close < open  AND  volume > vol_50d_avg

    Returns NaN for the first (window − 1) rows (warmup).
    """
    is_down_vol = ((close < open_) & (volume > vol_avg)).astype(float)
    is_down_vol = is_down_vol.where(vol_avg.notna(), other=np.nan)
    return is_down_vol.rolling(window=window, min_periods=window).sum()


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def compute(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """
    Compute and append volume indicators to a copy of *df*.

    Parameters
    ──────────
    df : pd.DataFrame
        OHLCV DataFrame with columns ``open``, ``close``, ``volume``
        and a DatetimeIndex.  Must contain at least 50 rows.

    config : dict
        Application configuration dict (loaded from settings.yaml).
        No volume-specific keys are currently required; the parameter is
        accepted for interface consistency with all other feature modules.

    Returns
    ───────
    pd.DataFrame
        A new DataFrame (the input is never modified) with all original
        columns preserved plus:
            vol_50d_avg    (float)  50-day SMA of volume
            vol_ratio      (float)  volume / vol_50d_avg; NaN when avg is 0/NaN
            up_vol_days    (float)  rolling 20-day count; NaN for first 19 rows
            down_vol_days  (float)  rolling 20-day count; NaN for first 19 rows
            acc_dist_score (float)  up_vol_days − down_vol_days; NaN for first 19 rows

    Raises
    ──────
    InsufficientDataError
        If len(df) < 50 (cannot compute vol_50d_avg).
    KeyError
        If required columns (``open``, ``close``, ``volume``) are absent.
    """
    n_rows = len(df)

    # ── Hard-minimum guard (fail loudly — §19.1) ─────────────────────────────
    if n_rows < _VOL_MA_PERIOD:
        raise InsufficientDataError(
            symbol=getattr(df, "name", "unknown"),
            required=_VOL_MA_PERIOD,
            available=n_rows,
            indicator="vol_50d_avg",
        )

    # ── Work on a copy — never mutate the caller's DataFrame ─────────────────
    out = df.copy()

    close = out["close"]
    open_ = out["open"]
    volume = out["volume"]

    # ── vol_50d_avg: exactly 50-row SMA of volume ─────────────────────────────
    # min_periods=50 means the first 49 rows are NaN; the 50th and all
    # subsequent rows carry a valid average.
    out["vol_50d_avg"] = volume.rolling(window=_VOL_MA_PERIOD, min_periods=_VOL_MA_PERIOD).mean()

    # ── vol_ratio: today / 50d avg — NaN when avg is 0 or NaN ────────────────
    out["vol_ratio"] = _safe_divide(volume, out["vol_50d_avg"])

    # ── up_vol_days / down_vol_days: rolling 20-day counts ───────────────────
    out["up_vol_days"] = _rolling_up_vol_days(
        close, open_, volume, out["vol_50d_avg"], _ACC_DIST_WINDOW
    )
    out["down_vol_days"] = _rolling_down_vol_days(
        close, open_, volume, out["vol_50d_avg"], _ACC_DIST_WINDOW
    )

    # ── acc_dist_score: up − down, range −20 to +20 ───────────────────────────
    # NaN propagates naturally when either component is NaN (warmup region).
    out["acc_dist_score"] = out["up_vol_days"] - out["down_vol_days"]

    return out
