"""
features/atr.py
───────────────
Pure function module — no classes, no side effects, no global state.

Computes and appends Average True Range indicators to an OHLCV DataFrame
following the Minervini SEPA methodology (PROJECT_DESIGN.md §4.2).

Public API
──────────
    compute(df, config) → pd.DataFrame

        Appends these columns to a *copy* of df and returns it:

            ATR_14   — 14-period Average True Range using Wilder's smoothing
            ATR_pct  — ATR_14 as a percentage of close price (ATR_14 / close × 100)

ATR Formula (Wilder's Smoothing)
─────────────────────────────────
    True Range (TR) = max(
        high - low,
        abs(high - prev_close),
        abs(low  - prev_close),
    )

    ATR_14[first]       = mean(TR[1:15])          # simple average of first 14 TRs
    ATR_14[subsequent]  = (ATR_14_prev × 13 + TR_current) / 14

    Note: TR[0] is undefined (no prev_close), so the first valid TR is at index 1.
    The first ATR_14 value sits at index 14 (0-based), covering TR[1..14].

Fail-loud contract (PROJECT_DESIGN.md §19.1)
────────────────────────────────────────────
    len(df) < 15  → raises InsufficientDataError
                    (need 15 rows to produce 14 TRs via prev_close comparison)

Design rules (PROJECT_DESIGN.md §4.2, §19.2)
─────────────────────────────────────────────
    • Pure functions only — no class, no global state, no I/O.
    • Idempotent — calling compute() twice produces the same result.
    • Do not mutate input df — always return a new DataFrame.
    • No TA-Lib, no external indicator libraries; only pandas + numpy.
    • ATR_pct is guarded against zero/negative close prices.
    • All thresholds come from the config dict, not from hardcoded constants.

Config keys consumed (with defaults)
─────────────────────────────────────
    config["atr"]["period"]   → int  (default 14)

    The period key is provided for future tunability.  The standard Wilder
    period of 14 is used when the key is absent.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from utils.exceptions import InsufficientDataError

# ─────────────────────────────────────────────────────────────────────────────
# Module-level constants
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_ATR_PERIOD: int = 14

# Minimum rows required: period + 1  (to have `period` valid TR values,
# each of which needs a prev_close, so we need period+1 OHLCV rows).
# For the default period of 14 this is 15.
_MIN_ROWS_FOR_PERIOD: dict[int, int] = {}  # computed lazily per period


def _min_rows(period: int) -> int:
    """Return minimum df rows required for the given ATR period."""
    return period + 1


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _wilder_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    """
    Compute Wilder's Average True Range for an array of OHLC data.

    Parameters
    ──────────
    high, low, close : np.ndarray
        1-D float arrays of equal length n.  Must have n >= period + 1.
    period : int
        Smoothing period (standard: 14).

    Returns
    ───────
    np.ndarray of shape (n,) with dtype float64.
        Indices 0 .. period-1 are np.nan (warmup).
        Index period onwards carries valid ATR values.

    Algorithm
    ─────────
    TR[i] = max(high[i]-low[i], |high[i]-close[i-1]|, |low[i]-close[i-1]|)
    TR[0] is undefined → np.nan.

    ATR[period]  = mean(TR[1 : period+1])          ← simple seed
    ATR[i]       = (ATR[i-1] * (period-1) + TR[i]) / period   ← Wilder's EMA
    """
    n = len(high)
    tr = np.empty(n, dtype=np.float64)
    tr[0] = np.nan  # no previous close for the very first bar

    # Vectorised TR calculation for indices 1..n-1
    prev_close = close[:-1]
    hl = high[1:] - low[1:]
    hpc = np.abs(high[1:] - prev_close)
    lpc = np.abs(low[1:]  - prev_close)
    tr[1:] = np.maximum(hl, np.maximum(hpc, lpc))

    atr = np.full(n, np.nan, dtype=np.float64)

    # Seed: simple average of TR[1 .. period] (inclusive)
    seed_start = 1
    seed_end   = period + 1          # exclusive → covers period values
    atr[period] = np.mean(tr[seed_start:seed_end])

    # Wilder's smoothing for remaining bars
    for i in range(period + 1, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period

    return atr


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def compute(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """
    Compute and append ATR indicators to a copy of *df*.

    Parameters
    ──────────
    df : pd.DataFrame
        OHLCV DataFrame with columns ``open``, ``high``, ``low``, ``close``,
        ``volume`` and a DatetimeIndex.  Must contain at least ``period + 1``
        rows (default: 15 rows for ATR_14).

    config : dict
        Application configuration dict (loaded from settings.yaml).
        Key read:
            config["atr"]["period"]   (default 14)

    Returns
    ───────
    pd.DataFrame
        A new DataFrame (the input is never modified) with all original
        columns preserved plus:
            ATR_14   — Wilder's 14-period ATR (NaN for warmup rows)
            ATR_pct  — ATR_14 / close × 100 (NaN where ATR_14 is NaN,
                       and where close <= 0)

    Raises
    ──────
    InsufficientDataError
        If len(df) < period + 1.
    KeyError
        If ``high``, ``low``, or ``close`` columns are absent.
    """
    # ── Read period from config ───────────────────────────────────────────────
    atr_cfg = config.get("atr", {})
    period: int = int(atr_cfg.get("period", _DEFAULT_ATR_PERIOD))

    required_rows = _min_rows(period)
    n_rows = len(df)

    # ── Hard-minimum guard (fail loudly — §19.1) ─────────────────────────────
    if n_rows < required_rows:
        raise InsufficientDataError(
            symbol=getattr(df, "name", "unknown"),
            required=required_rows,
            available=n_rows,
            indicator=f"ATR_{period}",
        )

    # ── Work on a copy — never mutate the caller's DataFrame ─────────────────
    out = df.copy()

    high  = out["high"].to_numpy(dtype=np.float64)
    low   = out["low"].to_numpy(dtype=np.float64)
    close = out["close"].to_numpy(dtype=np.float64)

    # ── Compute ATR via Wilder's smoothing ────────────────────────────────────
    atr_values = _wilder_atr(high, low, close, period)
    out[f"ATR_{period}"] = atr_values

    # ── ATR as % of close — guard against zero/negative close ────────────────
    # Replace non-positive close values with NaN so division is safe.
    safe_close = np.where(close > 0, close, np.nan)
    atr_pct = atr_values / safe_close * 100.0

    out["ATR_pct"] = atr_pct

    return out
