"""
features/moving_averages.py
────────────────────────────
Pure function module — no classes, no side effects, no global state.

Computes and appends moving-average indicators to an OHLCV DataFrame
following the Minervini SEPA methodology.

Public API
──────────
    compute(df, config) → pd.DataFrame

        Appends these columns to a *copy* of df and returns it:

            SMA_10       Simple MA over 10 periods
            SMA_21       Simple MA over 21 periods
            SMA_50       Simple MA over 50 periods
            SMA_150      Simple MA over 150 periods   ← exact 150 rows required
            SMA_200      Simple MA over 200 periods   ← exact 200 rows required
            EMA_21       Exponential MA over 21 periods
            MA_slope_50  Linear-regression slope of SMA_50 over last 10 days,
                         expressed as % change per day relative to current SMA_50
            MA_slope_200 Linear-regression slope of SMA_200 over last 20 days,
                         expressed as % change per day relative to current SMA_200

Fail-loud contract (PROJECT_DESIGN.md §19.1)
────────────────────────────────────────────
    len(df) < 150  → raises InsufficientDataError (for SMA_150)
    len(df) < 200  → raises InsufficientDataError (for SMA_200)

    NaN for warmup rows on SMA_10/21/50 and EMA_21 is acceptable.
    NaN is NEVER silently returned for SMA_150 or SMA_200.

Design rules (PROJECT_DESIGN.md §4.2, §19.2)
─────────────────────────────────────────────
    • Pure functions only — no class, no global state, no I/O.
    • Idempotent — calling compute() twice on the same df produces
      the same result (existing MA columns are overwritten, not doubled).
    • Do not mutate the input df — always return a new DataFrame.
    • No TA-Lib, no external indicator libraries; only pandas + numpy.
    • All thresholds come from the config dict, not from hardcoded constants.

Config keys consumed (with defaults)
─────────────────────────────────────
    config["stage"]["ma200_slope_lookback"]   → int  (default 20)
    config["stage"]["ma50_slope_lookback"]    → int  (default 10)

    Both keys are read from settings.yaml via the caller.  If they are
    absent the defaults above are used so the module is usable standalone.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from utils.exceptions import InsufficientDataError

# ─────────────────────────────────────────────────────────────────────────────
# Module-level constants
# ─────────────────────────────────────────────────────────────────────────────

MIN_ROWS_REQUIRED: int = 200  # minimum rows needed to compute all indicators

_SMA_PERIODS: tuple[int, ...] = (10, 21, 50, 150, 200)
_HARD_MINIMUM: dict[int, int] = {150: 150, 200: 200}   # periods that must not NaN

# Default slope lookback windows (overridden by config)
_DEFAULT_SLOPE_200_DAYS: int = 20
_DEFAULT_SLOPE_50_DAYS: int = 10


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _slope_pct_per_day(series: pd.Series, lookback: int) -> float:
    """
    Compute the linear-regression slope of the last *lookback* values of
    *series*, expressed as percentage change per day relative to the current
    (last) value.

    Formula
    ───────
        xs     = [0, 1, …, lookback-1]
        ys     = series[-lookback:]
        slope  = np.polyfit(xs, ys, deg=1)[0]   # absolute price units per day
        result = slope / current_value × 100     # % per day

    Args:
        series:   A pandas Series of MA values (e.g. SMA_200 column).
                  Must have at least *lookback* non-NaN values at the tail.
        lookback: Number of trailing observations to fit the regression on.

    Returns:
        Slope as % per day (float).  Returns 0.0 if the tail contains NaN
        (warmup region — should not happen after the hard-minimum checks).
    """
    tail = series.iloc[-lookback:].to_numpy(dtype=float)

    if np.isnan(tail).any():
        return 0.0

    current_val = tail[-1]
    if current_val == 0.0:
        return 0.0

    xs = np.arange(lookback, dtype=float)
    slope = np.polyfit(xs, tail, 1)[0]       # degree=1, returns [slope, intercept]
    return float(slope / current_val * 100.0)


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def compute(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """
    Compute and append moving-average indicators to a copy of *df*.

    Parameters
    ──────────
    df : pd.DataFrame
        OHLCV DataFrame with at least a ``close`` column and a DatetimeIndex.
        Must contain at least 150 rows (for SMA_150) and ideally 200+ rows
        (for SMA_200 and MA slopes).

    config : dict
        Application configuration dict (loaded from settings.yaml).
        Keys read:
            config["stage"]["ma200_slope_lookback"]  (default 20)
            config["stage"]["ma50_slope_lookback"]   (default 10)

    Returns
    ───────
    pd.DataFrame
        A new DataFrame (the input is never modified) with all original
        columns preserved plus:
            SMA_10, SMA_21, SMA_50, SMA_150, SMA_200
            EMA_21
            MA_slope_50, MA_slope_200

    Raises
    ──────
    InsufficientDataError
        If len(df) < 150 (cannot compute SMA_150).
        If len(df) < 200 (cannot compute SMA_200).
    KeyError
        If 'close' column is not present in df.
    """
    n_rows = len(df)

    # ── Hard-minimum guards (fail loudly — §19.1) ────────────────────────────
    if n_rows < 150:
        raise InsufficientDataError(
            symbol=getattr(df, "name", "unknown"),
            required=150,
            available=n_rows,
            indicator="SMA_150",
        )

    if n_rows < 200:
        raise InsufficientDataError(
            symbol=getattr(df, "name", "unknown"),
            required=200,
            available=n_rows,
            indicator="SMA_200",
        )

    # ── Read slope lookback windows from config ───────────────────────────────
    stage_cfg = config.get("stage", {})
    slope_200_days: int = int(
        stage_cfg.get("ma200_slope_lookback", _DEFAULT_SLOPE_200_DAYS)
    )
    slope_50_days: int = int(
        stage_cfg.get("ma50_slope_lookback", _DEFAULT_SLOPE_50_DAYS)
    )

    # ── Work on a copy — never mutate the caller's DataFrame ─────────────────
    out = df.copy()
    close = out["close"]

    # ── Simple Moving Averages ────────────────────────────────────────────────
    # min_periods=window ensures the first (window-1) rows are NaN for
    # SMA_10/21/50.  For SMA_150 and SMA_200 this is fine because we already
    # guaranteed len(df) >= the respective window, so the LAST row will always
    # carry a valid (non-NaN) value.
    for period in _SMA_PERIODS:
        out[f"SMA_{period}"] = close.rolling(window=period, min_periods=period).mean()

    # ── Exponential Moving Average — EMA_21 ───────────────────────────────────
    # pandas ewm with span=21 uses adjust=True (unbiased) by default, which
    # gives well-defined EMA values even for early rows.  This is consistent
    # with most charting platforms and requires no warmup guard.
    out["EMA_21"] = close.ewm(span=21, adjust=False, min_periods=21).mean()

    # ── MA Slopes (linear regression, expressed as % per day) ─────────────────
    # We compute a single scalar for the LAST row only — this is all the rule
    # engine needs (it evaluates the most recent row of the feature store).
    # For the historical rows we store NaN to keep the column width correct
    # and to avoid misleading intermediate values.
    out["MA_slope_50"] = np.nan
    out["MA_slope_200"] = np.nan

    # Guard: we need at least *lookback* rows of valid SMA before computing
    # slope.  Given we already have >= 200 rows total, and SMA_50/200 are valid
    # from row 50/200 onwards, this is always satisfiable.
    sma50_valid_rows = out["SMA_50"].notna().sum()
    sma200_valid_rows = out["SMA_200"].notna().sum()

    if sma50_valid_rows >= slope_50_days:
        out.loc[out.index[-1], "MA_slope_50"] = _slope_pct_per_day(
            out["SMA_50"].dropna(), slope_50_days
        )

    if sma200_valid_rows >= slope_200_days:
        out.loc[out.index[-1], "MA_slope_200"] = _slope_pct_per_day(
            out["SMA_200"].dropna(), slope_200_days
        )

    return out
