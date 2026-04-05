"""
ingestion/validator.py
──────────────────────
OHLCV data validation and cleaning for the Minervini AI system.

Design mandates (PROJECT_DESIGN.md §4.1 + §19.1):
  - FAIL LOUDLY on bad data — never silently swallow a validation failure.
  - DataValidationError must include symbol, field, reason, and row_date.
  - Duplicate dates are dropped with a WARNING (not an error).
  - detect_gaps() never raises — it returns an empty list when no gaps found.
  - Pure functions — no file I/O, no global state.
  - No pandas SettingWithCopyWarning — use .copy() where needed.

Public API:
    validate(df, symbol, config=None)           → cleaned DataFrame
    check_sufficient_history(df, symbol, min_rows=250) → None  (raises or passes)
    detect_gaps(df, symbol, max_gap_days=10)    → list[tuple[date, date]]
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from ingestion.base import MIN_USABLE_ROWS, OHLCV_COLUMNS
from utils.exceptions import DataValidationError, InsufficientDataError
from utils.logger import get_logger
from utils.math_utils import is_finite

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _row_date_str(index_val: Any) -> str:
    """
    Convert an index value to a compact date string for error messages.
    Handles both Timestamp and plain date objects gracefully.
    """
    try:
        if hasattr(index_val, "date"):
            return str(index_val.date())
        return str(index_val)
    except Exception:
        return repr(index_val)


def _check_columns(df: pd.DataFrame, symbol: str) -> None:
    """
    Verify the DataFrame contains exactly the OHLCV_COLUMNS from base.py.

    Raises:
        DataValidationError: listing every missing column in one shot.
    """
    missing = [col for col in OHLCV_COLUMNS if col not in df.columns]
    if missing:
        raise DataValidationError(
            symbol=symbol,
            field=", ".join(missing),
            reason=(
                f"DataFrame is missing required column(s): {missing}. "
                f"Expected all of {list(OHLCV_COLUMNS)}."
            ),
            row_date="",
        )
    log.debug("Column check passed", symbol=symbol, columns=list(df.columns))


def _check_no_nan_ohlc(df: pd.DataFrame, symbol: str) -> None:
    """
    Ensure open, high, low, close contain no NaN values.
    volume NaN is treated separately (volume must be > 0).

    Raises:
        DataValidationError: on the first offending row, including the date.
    """
    ohlc_cols = ["open", "high", "low", "close"]
    for col in ohlc_cols:
        nan_mask = df[col].isna()
        if nan_mask.any():
            first_bad_idx = df.index[nan_mask][0]
            raise DataValidationError(
                symbol=symbol,
                field=col,
                reason=f"NaN value found in '{col}' column.",
                row_date=_row_date_str(first_bad_idx),
            )
    log.debug("NaN check (OHLC) passed", symbol=symbol)


def _check_high_gte_low(df: pd.DataFrame, symbol: str) -> None:
    """
    Verify high >= low for every row.

    Raises:
        DataValidationError: on the first row where high < low.
    """
    bad_mask = df["high"] < df["low"]
    if bad_mask.any():
        first_bad_idx = df.index[bad_mask][0]
        row = df.loc[first_bad_idx]
        raise DataValidationError(
            symbol=symbol,
            field="high/low",
            reason=(
                f"high ({row['high']}) < low ({row['low']}) — "
                "OHLCV integrity violation."
            ),
            row_date=_row_date_str(first_bad_idx),
        )
    log.debug("high >= low check passed", symbol=symbol)


def _check_close_within_range(df: pd.DataFrame, symbol: str) -> None:
    """
    Verify close is within [low, high] for every row.

    Raises:
        DataValidationError: on the first row where close is out of range.
    """
    bad_mask = (df["close"] < df["low"]) | (df["close"] > df["high"])
    if bad_mask.any():
        first_bad_idx = df.index[bad_mask][0]
        row = df.loc[first_bad_idx]
        raise DataValidationError(
            symbol=symbol,
            field="close",
            reason=(
                f"close ({row['close']}) is outside [low={row['low']}, "
                f"high={row['high']}]."
            ),
            row_date=_row_date_str(first_bad_idx),
        )
    log.debug("close within [low, high] check passed", symbol=symbol)


def _check_volume_positive(df: pd.DataFrame, symbol: str) -> None:
    """
    Verify volume > 0 for every row (catches NaN too, since NaN > 0 is False).

    Raises:
        DataValidationError: on the first row with volume <= 0 or NaN.
    """
    # volume <= 0 covers NaN because NaN comparisons return False,
    # making ~(df["volume"] > 0) catch both zero, negative, and NaN.
    bad_mask = ~(df["volume"] > 0)
    if bad_mask.any():
        first_bad_idx = df.index[bad_mask][0]
        bad_val = df.loc[first_bad_idx, "volume"]
        raise DataValidationError(
            symbol=symbol,
            field="volume",
            reason=f"volume ({bad_val!r}) is not > 0.",
            row_date=_row_date_str(first_bad_idx),
        )
    log.debug("volume > 0 check passed", symbol=symbol)


def _dedup_and_sort(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """
    Detect and DROP duplicate index dates, then sort ascending.

    Logs a WARNING for every set of duplicated dates (which dates and
    how many copies).  Never raises — dedup is a data-cleaning step,
    not a hard failure.

    Returns:
        A new DataFrame (copy) with duplicates removed and index sorted.
    """
    duplicated_mask = df.index.duplicated(keep=False)
    if duplicated_mask.any():
        dup_dates = sorted(set(df.index[duplicated_mask]))
        log.warning(
            "Duplicate dates detected and dropped — keeping first occurrence",
            symbol=symbol,
            duplicate_dates=[_row_date_str(d) for d in dup_dates],
            duplicate_count=int(duplicated_mask.sum()),
        )
        # keep="first" retains the first occurrence of each duplicated date
        df = df[~df.index.duplicated(keep="first")].copy()
    else:
        log.debug("No duplicate dates found", symbol=symbol)

    # Sort ascending by date index
    df = df.sort_index(ascending=True)
    return df


def _ensure_datetime_index(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """
    Ensure the index is a DatetimeIndex named 'date'.
    If the index is already DatetimeIndex, just rename.
    If 'date' is a column, set it as the index and convert.

    Returns:
        DataFrame with a proper DatetimeIndex named 'date'.

    Raises:
        DataValidationError: if no date information can be found.
    """
    if isinstance(df.index, pd.DatetimeIndex):
        df = df.copy()
        df.index.name = "date"
        return df

    if "date" in df.columns:
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
        df.index.name = "date"
        return df

    # Attempt to coerce the existing index
    try:
        df = df.copy()
        df.index = pd.to_datetime(df.index)
        df.index.name = "date"
        return df
    except Exception as exc:
        raise DataValidationError(
            symbol=symbol,
            field="index",
            reason=(
                f"Cannot convert DataFrame index to DatetimeIndex: {exc}. "
                "Provide a DatetimeIndex or a 'date' column."
            ),
            row_date="",
        ) from exc


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def validate(
    df: pd.DataFrame,
    symbol: str,
    config: dict | None = None,  # reserved for future configurable thresholds
) -> pd.DataFrame:
    """
    Validate and clean a raw OHLCV DataFrame.

    Checks performed (in order):
        1. Columns match OHLCV_COLUMNS from ingestion/base.py.
        2. Index is (or can be coerced to) a DatetimeIndex named 'date'.
        3. No NaN in open, high, low, close.
        4. high >= low for every row.
        5. close within [low, high] for every row.
        6. volume > 0 for every row.
        7. Duplicate dates are DROPPED (logged as WARNING, not raised).
        8. Index is sorted ascending.

    Args:
        df:     Raw OHLCV DataFrame from any DataSource.
        symbol: NSE symbol string used in error messages.
        config: Optional config dict (reserved; currently unused).

    Returns:
        Cleaned DataFrame with:
            - DatetimeIndex named 'date', sorted ascending, no duplicates.
            - Columns: open, high, low, close, volume (others preserved).

    Raises:
        DataValidationError: on any hard validation failure (NaN, bad OHLCV
            relationship, missing columns, unconvertible index).  Each error
            includes symbol, field, reason, and row_date.
    """
    # ── Step 1: Ensure DatetimeIndex named 'date' ─────────────────────────
    df = _ensure_datetime_index(df, symbol)

    # ── Step 2: Column presence ───────────────────────────────────────────
    _check_columns(df, symbol)

    # ── Step 3: Work on a clean copy to avoid SettingWithCopyWarning ──────
    df = df.copy()

    # ── Step 4: NaN in OHLC ───────────────────────────────────────────────
    _check_no_nan_ohlc(df, symbol)

    # ── Step 5: high >= low ───────────────────────────────────────────────
    _check_high_gte_low(df, symbol)

    # ── Step 6: close within [low, high] ─────────────────────────────────
    _check_close_within_range(df, symbol)

    # ── Step 7: volume > 0 ────────────────────────────────────────────────
    _check_volume_positive(df, symbol)

    # ── Step 8: Dedup + sort ──────────────────────────────────────────────
    df = _dedup_and_sort(df, symbol)

    log.debug(
        "Validation complete",
        symbol=symbol,
        rows=len(df),
        date_start=_row_date_str(df.index[0]) if not df.empty else "N/A",
        date_end=_row_date_str(df.index[-1]) if not df.empty else "N/A",
    )
    return df


def check_sufficient_history(
    df: pd.DataFrame,
    symbol: str,
    min_rows: int = MIN_USABLE_ROWS,
) -> None:
    """
    Raise InsufficientDataError if the DataFrame has fewer rows than
    *min_rows*.

    The default minimum (250) matches MIN_USABLE_ROWS from ingestion/base.py:
    SMA_200 needs 200 rows; the 50-row buffer ensures feature computation
    has room to warm up without producing NaN-filled results.

    Args:
        df:       Validated OHLCV DataFrame (should be post-validate()).
        symbol:   NSE symbol string for error context.
        min_rows: Minimum acceptable row count (default = MIN_USABLE_ROWS = 250).

    Returns:
        None if the check passes.

    Raises:
        InsufficientDataError: if len(df) < min_rows.
    """
    available = len(df)
    if available < min_rows:
        raise InsufficientDataError(
            symbol=symbol,
            required=min_rows,
            available=available,
        )
    log.debug(
        "Sufficient history check passed",
        symbol=symbol,
        rows=available,
        min_rows=min_rows,
    )


def detect_gaps(
    df: pd.DataFrame,
    symbol: str,
    max_gap_days: int = 10,
) -> list[tuple[date, date]]:
    """
    Detect calendar gaps between consecutive rows that exceed *max_gap_days*.

    A "gap" here is a calendar gap (not trading-day gap) between consecutive
    DataFrame rows.  Weekends and NSE holidays naturally create 3-day gaps,
    so the default threshold of 10 days catches missing-data periods while
    ignoring normal market closures.

    Used by the pipeline to decide whether a re-download is needed for a
    symbol (e.g. after a prolonged exchange outage or data feed failure).

    Args:
        df:           Validated OHLCV DataFrame with DatetimeIndex.
        symbol:       NSE symbol string (used only for log messages).
        max_gap_days: Calendar-day gap threshold.  Consecutive rows
                      separated by more than this many days are flagged.

    Returns:
        List of (gap_start, gap_end) tuples where:
            gap_start — the date of the row BEFORE the gap.
            gap_end   — the date of the row AFTER the gap.
        Empty list if no gaps are found or if df has < 2 rows.

    Does NOT raise — always returns a list (possibly empty).
    """
    if len(df) < 2:
        log.debug("detect_gaps: too few rows to detect gaps", symbol=symbol, rows=len(df))
        return []

    gaps: list[tuple[date, date]] = []

    index_as_dates = df.index.to_series().dt.date  # Series of date objects

    prev_date = None
    for curr_date in index_as_dates:
        if prev_date is not None:
            delta = (curr_date - prev_date).days
            if delta > max_gap_days:
                gaps.append((prev_date, curr_date))
                log.debug(
                    "Gap detected",
                    symbol=symbol,
                    gap_start=str(prev_date),
                    gap_end=str(curr_date),
                    calendar_days=delta,
                    threshold=max_gap_days,
                )
        prev_date = curr_date

    if gaps:
        log.warning(
            "Data gaps detected — re-download may be required",
            symbol=symbol,
            gap_count=len(gaps),
            gaps=[(str(s), str(e)) for s, e in gaps],
        )
    else:
        log.debug("No data gaps detected", symbol=symbol, max_gap_days=max_gap_days)

    return gaps
