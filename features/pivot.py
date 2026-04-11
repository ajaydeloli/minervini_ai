"""
features/pivot.py
─────────────────
Pure function module — no classes, no side effects, no global state.

Detects swing high and swing low pivot points using a ZigZag-style algorithm
and appends them to an OHLCV DataFrame, following the Minervini SEPA
methodology (PROJECT_DESIGN.md §4.2, Appendix B — VCP Anatomy).

Public API
──────────
    compute(df, config) → pd.DataFrame

        Appends these columns to a *copy* of df and returns it:

            is_swing_high   — bool (nullable), True if this row is a confirmed
                              swing high pivot; pd.NA for the last `window` rows
                              (future bars not yet available); False elsewhere.

            is_swing_low    — bool (nullable), True if this row is a confirmed
                              swing low pivot; pd.NA for the last `window` rows;
                              False elsewhere.

            last_pivot_high — float, price of the most recent confirmed swing
                              high (forward-filled from the confirming bar);
                              NaN until the first swing high is found.

            last_pivot_low  — float, price of the most recent confirmed swing
                              low (forward-filled); NaN until first swing low.

ZigZag Algorithm
────────────────
    A swing HIGH at bar i is confirmed when:
        df['high'][i] == max(df['high'][i - window : i + window + 1])

    A swing LOW at bar i is confirmed when:
        df['low'][i] == min(df['low'][i - window : i + window + 1])

    Both checks use a symmetric window: `window` bars on each side.

Edge handling
─────────────
    • First `window` bars (i < window): not enough lookback — marked False.
    • Last `window` bars (i >= n - window): future bars unavailable — marked pd.NA.
    • Confirmable range: i in [window, n - window - 1] (inclusive).

    A row CAN simultaneously be both a swing high and a swing low.  This only
    occurs naturally when window == 1 (tight window on highly volatile data).

Fail-loud contract (PROJECT_DESIGN.md §19.1)
────────────────────────────────────────────
    len(df) < 2 * window + 1  → raises InsufficientDataError

Design rules (PROJECT_DESIGN.md §4.2, §19.2)
─────────────────────────────────────────────
    • Pure functions only — no class, no global state, no I/O.
    • Idempotent — calling compute() twice produces the same result.
    • Do not mutate the input df — always return a new DataFrame.
    • No TA-Lib, no external indicator libraries; only pandas + numpy.
    • All thresholds come from the config dict, not from hardcoded constants.

Config keys consumed (with defaults)
─────────────────────────────────────
    config["vcp"]["pivot_window"]   → int  (default 5)

    The pivot_window controls how many bars on each side of a candidate bar
    must be lower (for swing high) or higher (for swing low).  Larger windows
    find fewer but more significant pivots.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from utils.exceptions import InsufficientDataError

# ─────────────────────────────────────────────────────────────────────────────
# Module-level constants
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_PIVOT_WINDOW: int = 5


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def compute(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """
    Detect swing high / swing low pivot points and append them to *df*.

    Parameters
    ──────────
    df : pd.DataFrame
        OHLCV DataFrame with at least ``high`` and ``low`` columns and a
        DatetimeIndex.  Must contain at least ``2 * window + 1`` rows.

    config : dict
        Application configuration dict (loaded from settings.yaml).
        Key read:
            config["vcp"]["pivot_window"]   (default 5)

    Returns
    ───────
    pd.DataFrame
        A new DataFrame (the input is never modified) with all original
        columns preserved plus:

            is_swing_high   — pandas BooleanArray (True / False / pd.NA)
            is_swing_low    — pandas BooleanArray (True / False / pd.NA)
            last_pivot_high — float64 (NaN until first confirmed swing high)
            last_pivot_low  — float64 (NaN until first confirmed swing low)

    Raises
    ──────
    InsufficientDataError
        If len(df) < 2 * window + 1.
    KeyError
        If ``high`` or ``low`` columns are absent from *df*.
    """
    # ── Read window from config ───────────────────────────────────────────────
    vcp_cfg = config.get("vcp", {})
    window: int = int(vcp_cfg.get("pivot_window", _DEFAULT_PIVOT_WINDOW))

    min_rows: int = 2 * window + 1
    n: int = len(df)

    # ── Hard-minimum guard (fail loudly — §19.1) ─────────────────────────────
    if n < min_rows:
        raise InsufficientDataError(
            symbol=getattr(df, "name", "unknown"),
            required=min_rows,
            available=n,
            indicator="pivot",
        )

    # ── Work on a copy — never mutate the caller's DataFrame ─────────────────
    out = df.copy()

    high: np.ndarray = out["high"].to_numpy(dtype=np.float64)
    low: np.ndarray  = out["low"].to_numpy(dtype=np.float64)

    # ── Build pivot indicator arrays ──────────────────────────────────────────
    # Use pandas nullable BooleanArray so we can represent True / False / pd.NA.
    # pd.NA is used for the last `window` rows where future bars are unavailable.
    sh_vals: pd.arrays.BooleanArray = pd.array([pd.NA] * n, dtype="boolean")
    sl_vals: pd.arrays.BooleanArray = pd.array([pd.NA] * n, dtype="boolean")

    # Confirmable range: i in [window, n - window - 1] (inclusive).
    # Rows 0 .. window-1     → False (insufficient lookback).
    # Rows window .. n-window-1 → ZigZag check.
    # Rows n-window .. n-1   → pd.NA (future bars not available) — already set.

    for i in range(n - window):         # i = 0, 1, …, n-window-1  (last window rows excluded)
        if i < window:
            # Not enough bars to the left to confirm a pivot.
            sh_vals[i] = False
            sl_vals[i] = False
        else:
            # Full symmetric window available on both sides.
            segment_h = high[i - window : i + window + 1]   # length = 2*window+1
            segment_l = low[i  - window : i + window + 1]

            sh_vals[i] = bool(high[i] == np.max(segment_h))
            sl_vals[i] = bool(low[i]  == np.min(segment_l))

    out["is_swing_high"] = sh_vals
    out["is_swing_low"]  = sl_vals

    # ── Forward-fill last confirmed pivot prices ──────────────────────────────
    # Walk row by row: whenever a confirmed pivot is encountered, update the
    # running tracker; copy the tracker value into every row.
    last_pivot_high: np.ndarray = np.full(n, np.nan, dtype=np.float64)
    last_pivot_low:  np.ndarray = np.full(n, np.nan, dtype=np.float64)

    current_ph: float = np.nan
    current_pl: float = np.nan

    for i in range(n):
        sh = sh_vals[i]
        sl = sl_vals[i]

        # pd.notna(sh) guards against pd.NA in the last `window` rows.
        if pd.notna(sh) and sh:
            current_ph = float(high[i])

        if pd.notna(sl) and sl:
            current_pl = float(low[i])

        last_pivot_high[i] = current_ph
        last_pivot_low[i]  = current_pl

    out["last_pivot_high"] = last_pivot_high
    out["last_pivot_low"]  = last_pivot_low

    return out
