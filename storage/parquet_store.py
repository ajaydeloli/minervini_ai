"""
storage/parquet_store.py
────────────────────────
Read / write helpers for the project's Parquet data layer.

Layout (one flat file per symbol — no partitioning):
    data/raw/{symbol}/YYYY-MM-DD.parquet      ← immutable daily downloads
    data/processed/{symbol}.parquet           ← cleaned OHLCV (adj prices)
    data/features/{symbol}.parquet            ← wide feature DataFrame

Design rules (from PROJECT_DESIGN.md §5.6):
  1. Atomic writes  — every write goes to a .tmp file first, then
     os.replace() (atomic on POSIX) swaps it in.  A killed process
     never leaves a half-written Parquet file.
  2. Append pattern — daily updates add ONE row; we never rewrite the
     full history on a daily run.  Full rewrites only happen during
     bootstrap or explicit rebuild.
  3. Single flat file per symbol — simpler than partitioned datasets
     and fast enough for 2 000+ rows per symbol.
  4. DatetimeIndex — all Parquet files use the date column as the index
     (stored as 'date', dtype datetime64[ns] or date).  Functions that
     read always sort by index after loading.
  5. No silent NaN injection — if a caller passes a row whose date
     already exists, `append_row` raises DuplicateDateError (subclass
     of ParquetWriteError) so the caller can decide to skip or abort.
"""

from __future__ import annotations

import os
import shutil
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from utils.exceptions import InsufficientDataError, ParquetWriteError
from utils.logger import get_logger

if TYPE_CHECKING:
    pass

log = get_logger(__name__)

# Threshold above which we use streaming ParquetWriter instead of
# read-all → concat → write (avoids loading 10 years into RAM).
_STREAMING_THRESHOLD_ROWS = 5_000

# Index column name used across ALL Parquet files in this project.
INDEX_COL = "date"


# ─────────────────────────────────────────────────────────────────────────────
# Custom exception (keeps storage errors specific)
# ─────────────────────────────────────────────────────────────────────────────

class DuplicateDateError(ParquetWriteError):
    """
    Raised by append_row() when the new row's date already exists in the
    target Parquet file.  The caller (feature_store.update) catches this
    and raises FeatureStoreOutOfSyncError with richer context.
    """
    def __init__(self, path: str, row_date: str):
        super().__init__(
            path=path,
            reason=f"date {row_date!r} already present — use bootstrap() to force recompute",
        )
        self.row_date = row_date


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _tmp_path(path: Path) -> Path:
    """Return a sibling .tmp file path used for atomic writes."""
    return path.with_suffix(".tmp.parquet")


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _to_date_index(df: pd.DataFrame) -> pd.DataFrame:
    """
    Ensure the DataFrame has a DatetimeIndex named INDEX_COL ('date').
    Accepts DataFrames where:
      - INDEX_COL is already the index, or
      - INDEX_COL is a regular column (will be set as index).
    Sorts ascending by date.
    """
    if df.index.name == INDEX_COL:
        pass
    elif INDEX_COL in df.columns:
        df = df.set_index(INDEX_COL)
    else:
        raise ValueError(
            f"DataFrame has no column or index named '{INDEX_COL}'. "
            f"Columns: {list(df.columns)}"
        )
    df.index = pd.to_datetime(df.index)
    df.index.name = INDEX_COL
    return df.sort_index()


def _write_atomic(df: pd.DataFrame, path: Path) -> None:
    """
    Write *df* to *path* atomically:
        1. Write to a .tmp sibling file.
        2. os.replace() → atomic rename on POSIX (no partial-write exposure).
    """
    _ensure_parent(path)
    tmp = _tmp_path(path)
    try:
        df.to_parquet(tmp, index=True, engine="pyarrow")
        os.replace(tmp, path)  # atomic on Linux / macOS
    except Exception as exc:
        # Clean up temp file if rename failed
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise ParquetWriteError(str(path), reason=str(exc)) from exc


# ─────────────────────────────────────────────────────────────────────────────
# Public read helpers
# ─────────────────────────────────────────────────────────────────────────────

def read(path: str | Path) -> pd.DataFrame:
    """
    Read a project Parquet file and return a DataFrame with a
    sorted DatetimeIndex named 'date'.

    Args:
        path: Path to the .parquet file.

    Returns:
        DataFrame sorted by date ascending.

    Raises:
        FileNotFoundError: If the file does not exist.
        ParquetWriteError: If the file is corrupt / unreadable.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Parquet file not found: {path}")
    try:
        df = pd.read_parquet(path, engine="pyarrow")
    except Exception as exc:
        raise ParquetWriteError(str(path), reason=f"read error: {exc}") from exc
    return _to_date_index(df)


def read_tail(path: str | Path, n: int) -> pd.DataFrame:
    """
    Read only the last *n* rows of a Parquet file (by date).

    Much more efficient than read() for incremental daily updates:
    we never load 10 years of data just to compute today's SMA.

    Args:
        path: Path to the .parquet file.
        n:    Number of most-recent rows to return.

    Returns:
        DataFrame with at most *n* rows, sorted by date ascending.

    Raises:
        FileNotFoundError: If the file does not exist.
        InsufficientDataError: If the file has fewer than *n* rows
            AND the caller explicitly asked for at least *n* (use
            read_tail_at_least for that variant).
    """
    df = read(path)
    return df.iloc[-n:] if len(df) >= n else df


def read_tail_at_least(path: str | Path, n: int, symbol: str = "") -> pd.DataFrame:
    """
    Like read_tail(), but raises InsufficientDataError when the file
    has fewer than *n* rows.  Used by feature_store.update() to
    ensure there is enough history for SMA_200 computation.

    Args:
        path:   Path to the .parquet file.
        n:      Minimum rows required.
        symbol: Symbol name for error context (optional but helpful).
    """
    df = read(path)
    if len(df) < n:
        raise InsufficientDataError(
            symbol=symbol or str(path),
            required=n,
            available=len(df),
        )
    return df.iloc[-n:]


def read_date_range(
    path: str | Path,
    start: date | str | None = None,
    end: date | str | None = None,
) -> pd.DataFrame:
    """
    Read rows within an optional date range [start, end] (inclusive).

    Args:
        path:  Path to the .parquet file.
        start: Earliest date to include (None = no lower bound).
        end:   Latest date to include (None = no upper bound).

    Returns:
        Filtered DataFrame sorted by date ascending.
    """
    df = read(path)
    if start is not None:
        df = df[df.index >= pd.Timestamp(start)]
    if end is not None:
        df = df[df.index <= pd.Timestamp(end)]
    return df


def exists(path: str | Path) -> bool:
    """Return True if the Parquet file exists and is non-empty."""
    p = Path(path)
    return p.exists() and p.stat().st_size > 0


def row_count(path: str | Path) -> int:
    """
    Return the number of rows in a Parquet file without loading all
    columns into memory (uses pyarrow metadata).
    Returns 0 if the file does not exist.
    """
    p = Path(path)
    if not p.exists():
        return 0
    try:
        meta = pq.read_metadata(str(p))
        return meta.num_rows
    except Exception:
        # Fallback: load and count (handles edge cases like v1 format)
        return len(read(p))


def last_date(path: str | Path) -> date | None:
    """
    Return the most recent date in a Parquet file, or None if the file
    does not exist.  Reads only the index column for speed.
    """
    p = Path(path)
    if not p.exists():
        return None
    try:
        table = pq.read_table(str(p), columns=[INDEX_COL])
        ts_series = table.column(INDEX_COL).to_pylist()
        if not ts_series:
            return None
        latest = max(ts_series)
        if isinstance(latest, pd.Timestamp):
            return latest.date()
        if hasattr(latest, "as_py"):
            latest = latest.as_py()
        return pd.Timestamp(latest).date()
    except Exception:
        df = read(p)
        if df.empty:
            return None
        return df.index[-1].date()


# ─────────────────────────────────────────────────────────────────────────────
# Public write helpers
# ─────────────────────────────────────────────────────────────────────────────

def write(df: pd.DataFrame, path: str | Path, overwrite: bool = True) -> None:
    """
    Write *df* to *path* atomically.  Used for bootstrap / full rewrites.

    Args:
        df:        DataFrame with a 'date' column or DatetimeIndex.
        path:      Target .parquet path (parent dirs created automatically).
        overwrite: If False and the file already exists, raises FileExistsError.

    Raises:
        FileExistsError: If overwrite=False and path already exists.
        ParquetWriteError: On write failure.
    """
    path = Path(path)
    if not overwrite and path.exists():
        raise FileExistsError(f"Parquet file already exists: {path}")
    df = _to_date_index(df)
    _write_atomic(df, path)
    log.debug("Parquet written", path=str(path), rows=len(df))


def append_row(path: str | Path, new_row: pd.DataFrame) -> None:
    """
    Append a single new row to an existing Parquet file atomically.

    This is the hot path called every trading day per symbol.
    Strategy:
      - File has < _STREAMING_THRESHOLD_ROWS: read → concat → write.
      - File has >= _STREAMING_THRESHOLD_ROWS: stream existing file +
        new row via ParquetWriter (avoids loading 10 years into RAM).

    Args:
        path:    Target .parquet file.  Created if it doesn't exist.
        new_row: Single-row DataFrame.  Must contain a 'date' column or
                 have a DatetimeIndex named 'date'.

    Raises:
        DuplicateDateError: If new_row's date already exists in the file.
        ParquetWriteError:  On write failure.
    """
    path = Path(path)
    new_row = _to_date_index(new_row)

    if len(new_row) != 1:
        raise ValueError(
            f"append_row expects exactly 1 row, got {len(new_row)}."
        )

    new_date = new_row.index[0]

    # ── Case 1: file doesn't exist yet — just write ───────────────────────
    if not path.exists():
        _ensure_parent(path)
        _write_atomic(new_row, path)
        log.debug("Parquet created (first row)", path=str(path), date=str(new_date))
        return

    # ── Guard: duplicate date check (fast — only reads index column) ──────
    existing_last = last_date(path)
    if existing_last is not None:
        existing_last_ts = pd.Timestamp(existing_last)
        if new_date <= existing_last_ts:
            # Could be a genuine duplicate or an out-of-order insert.
            # Either way, raise so the caller decides.
            # Check precisely by reading the index.
            existing_index = pq.read_table(str(path), columns=[INDEX_COL]) \
                               .column(INDEX_COL).to_pylist()
            existing_ts = {pd.Timestamp(d) for d in existing_index}
            if new_date in existing_ts:
                raise DuplicateDateError(str(path), str(new_date.date()))

    n_rows = row_count(path)

    # ── Case 2: small file — read, concat, write ──────────────────────────
    if n_rows < _STREAMING_THRESHOLD_ROWS:
        existing = read(path)
        updated = pd.concat([existing, new_row]).sort_index()
        _write_atomic(updated, path)
        log.debug(
            "Parquet row appended (concat)",
            path=str(path),
            date=str(new_date.date()),
            total_rows=len(updated),
        )
        return

    # ── Case 3: large file — stream via ParquetWriter ────────────────────
    # Read existing as Arrow table (no pandas overhead), append new row.
    _ensure_parent(path)
    tmp = _tmp_path(path)
    try:
        existing_table = pq.read_table(str(path))
        new_table = pa.Table.from_pandas(new_row, preserve_index=True)

        # Align schemas (new_row may have slightly different dtypes)
        new_table = new_table.cast(existing_table.schema)

        combined = pa.concat_tables([existing_table, new_table])

        # Sort by date index
        date_col_idx = combined.schema.get_field_index(INDEX_COL)
        if date_col_idx >= 0:
            sort_indices = pa.compute.sort_indices(
                combined, sort_keys=[(INDEX_COL, "ascending")]
            )
            combined = combined.take(sort_indices)

        with pq.ParquetWriter(str(tmp), combined.schema) as writer:
            writer.write_table(combined)

        os.replace(tmp, path)
        log.debug(
            "Parquet row appended (streaming)",
            path=str(path),
            date=str(new_date.date()),
            total_rows=combined.num_rows,
        )
    except DuplicateDateError:
        raise
    except Exception as exc:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise ParquetWriteError(str(path), reason=str(exc)) from exc


def append_dataframe(path: str | Path, new_df: pd.DataFrame) -> None:
    """
    Append multiple rows to an existing Parquet file atomically.
    Used during bootstrap when appending a batch of computed feature rows.

    Dates already present in *path* are skipped silently (idempotent).

    Args:
        path:   Target .parquet file.  Created if it doesn't exist.
        new_df: DataFrame with a 'date' column or DatetimeIndex.

    Raises:
        ParquetWriteError: On write failure.
    """
    path = Path(path)
    new_df = _to_date_index(new_df)

    if not path.exists():
        _ensure_parent(path)
        _write_atomic(new_df, path)
        log.debug("Parquet created", path=str(path), rows=len(new_df))
        return

    existing = read(path)
    # Drop any rows in new_df whose dates already exist (idempotent)
    new_df = new_df[~new_df.index.isin(existing.index)]
    if new_df.empty:
        log.debug("append_dataframe: all rows already present, nothing to write", path=str(path))
        return

    updated = pd.concat([existing, new_df]).sort_index()
    _write_atomic(updated, path)
    log.debug(
        "Parquet rows appended",
        path=str(path),
        new_rows=len(new_df),
        total_rows=len(updated),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Repair / maintenance helpers
# ─────────────────────────────────────────────────────────────────────────────

def deduplicate(path: str | Path) -> int:
    """
    Remove duplicate date rows from a Parquet file, keeping the last
    occurrence (most recently appended row wins).

    Returns:
        Number of duplicate rows removed (0 if file was already clean).

    Raises:
        FileNotFoundError: If the file does not exist.
    """
    path = Path(path)
    df = read(path)
    original_len = len(df)
    df = df[~df.index.duplicated(keep="last")]
    removed = original_len - len(df)
    if removed > 0:
        _write_atomic(df, path)
        log.warning("Deduplicated Parquet file", path=str(path), removed=removed)
    return removed


def is_corrupt(path: str | Path) -> bool:
    """
    Return True if the Parquet file is missing or cannot be opened.
    Used by feature_store.needs_bootstrap().
    """
    p = Path(path)
    if not p.exists():
        return True
    try:
        pq.read_metadata(str(p))
        return False
    except Exception:
        return True


def copy_safe(src: str | Path, dst: str | Path) -> None:
    """
    Copy a Parquet file to *dst* safely (atomic write to dst).
    Used by backup/restore utilities and test fixtures.
    """
    src, dst = Path(src), Path(dst)
    if not src.exists():
        raise FileNotFoundError(f"Source Parquet file not found: {src}")
    _ensure_parent(dst)
    tmp = _tmp_path(dst)
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)
