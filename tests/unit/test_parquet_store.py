"""
tests/unit/test_parquet_store.py
────────────────────────────────
Unit tests for storage/parquet_store.py.

Coverage targets:
    write()              — basic write, overwrite guard, index handling
    read()               — returns sorted DatetimeIndex, FileNotFoundError
    read_tail()          — correct last-N rows, short-file fallback
    read_tail_at_least() — raises InsufficientDataError when too few rows
    read_date_range()    — filters by start / end / both
    append_row()         — normal append, DuplicateDateError, creates file
    append_dataframe()   — batch append, idempotent on overlap
    exists()             — True/False for existing/missing file
    row_count()          — metadata-only row count
    last_date()          — returns correct latest date
    deduplicate()        — removes duplicate index rows
    is_corrupt()         — True for missing/unreadable, False for valid
    copy_safe()          — copies file atomically

All tests are isolated: each uses pytest's tmp_path so nothing is written
to the project's data/ directory.
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from storage.parquet_store import (
    DuplicateDateError,
    INDEX_COL,
    append_dataframe,
    append_row,
    copy_safe,
    deduplicate,
    exists,
    is_corrupt,
    last_date,
    read,
    read_date_range,
    read_tail,
    read_tail_at_least,
    row_count,
    write,
)
from utils.exceptions import InsufficientDataError, ParquetWriteError


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _row(d: date, close: float = 100.0) -> pd.DataFrame:
    """Build a single-row DataFrame with a DatetimeIndex."""
    return pd.DataFrame(
        [{"close": close, "open": close, "high": close + 1, "low": close - 1, "volume": 1000}],
        index=pd.DatetimeIndex([pd.Timestamp(d)], name=INDEX_COL),
    )


def _multi_row(n: int, start: date | None = None) -> pd.DataFrame:
    """Build an n-row DataFrame with consecutive trading-day dates."""
    start_d = start or date(2023, 1, 2)
    rows = []
    d = start_d
    for i in range(n):
        # skip weekends
        while d.weekday() >= 5:
            d += timedelta(days=1)
        rows.append({"close": 100.0 + i, "volume": 1000, "open": 100.0, "high": 101.0, "low": 99.0})
        d += timedelta(days=1)
    # build date index (skip weekends)
    dates = []
    d = start_d
    for _ in range(n):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        dates.append(pd.Timestamp(d))
        d += timedelta(days=1)
    df = pd.DataFrame(rows, index=pd.DatetimeIndex(dates, name=INDEX_COL))
    return df


# ─────────────────────────────────────────────────────────────────────────────
# write() tests
# ─────────────────────────────────────────────────────────────────────────────

class TestWrite:
    def test_write_creates_file(self, tmp_parquet_path: Path, sample_ohlcv_df: pd.DataFrame):
        assert not tmp_parquet_path.exists()
        write(sample_ohlcv_df, tmp_parquet_path)
        assert tmp_parquet_path.exists()

    def test_write_roundtrip(self, tmp_parquet_path: Path, sample_ohlcv_df: pd.DataFrame):
        write(sample_ohlcv_df, tmp_parquet_path)
        loaded = read(tmp_parquet_path)
        assert len(loaded) == len(sample_ohlcv_df)
        assert loaded.index.name == INDEX_COL

    def test_write_creates_parent_dirs(self, tmp_path: Path, sample_ohlcv_df: pd.DataFrame):
        nested = tmp_path / "a" / "b" / "c" / "data.parquet"
        write(sample_ohlcv_df, nested)
        assert nested.exists()

    def test_write_overwrite_default(self, tmp_parquet_path: Path, sample_ohlcv_df: pd.DataFrame):
        write(sample_ohlcv_df, tmp_parquet_path)
        # Overwrite with a smaller df — should succeed
        small = sample_ohlcv_df.iloc[:10]
        write(small, tmp_parquet_path)
        loaded = read(tmp_parquet_path)
        assert len(loaded) == 10

    def test_write_overwrite_false_raises(self, tmp_parquet_path: Path, sample_ohlcv_df: pd.DataFrame):
        write(sample_ohlcv_df, tmp_parquet_path)
        with pytest.raises(FileExistsError):
            write(sample_ohlcv_df, tmp_parquet_path, overwrite=False)

    def test_write_accepts_column_named_date(self, tmp_parquet_path: Path):
        """DataFrame with 'date' as a regular column (not index) is accepted."""
        df = pd.DataFrame({
            "date": pd.to_datetime(["2023-01-02", "2023-01-03"]),
            "close": [100.0, 101.0],
        })
        write(df, tmp_parquet_path)
        loaded = read(tmp_parquet_path)
        assert loaded.index.name == INDEX_COL
        assert len(loaded) == 2

    def test_write_no_date_column_raises(self, tmp_parquet_path: Path):
        """DataFrame with no 'date' column or index raises ValueError."""
        df = pd.DataFrame({"close": [100.0], "volume": [1000]})
        with pytest.raises(ValueError, match="date"):
            write(df, tmp_parquet_path)

    def test_write_sorts_index(self, tmp_parquet_path: Path):
        """write() should sort the index even if input is unsorted."""
        dates = pd.to_datetime(["2023-01-04", "2023-01-02", "2023-01-03"])
        df = pd.DataFrame({"close": [3.0, 1.0, 2.0]}, index=dates)
        df.index.name = INDEX_COL
        write(df, tmp_parquet_path)
        loaded = read(tmp_parquet_path)
        assert list(loaded["close"]) == [1.0, 2.0, 3.0]


# ─────────────────────────────────────────────────────────────────────────────
# read() tests
# ─────────────────────────────────────────────────────────────────────────────

class TestRead:
    def test_read_missing_file_raises(self, tmp_parquet_path: Path):
        with pytest.raises(FileNotFoundError):
            read(tmp_parquet_path)

    def test_read_returns_datetime_index(self, tmp_parquet_path: Path, sample_ohlcv_df: pd.DataFrame):
        write(sample_ohlcv_df, tmp_parquet_path)
        df = read(tmp_parquet_path)
        assert isinstance(df.index, pd.DatetimeIndex)
        assert df.index.name == INDEX_COL

    def test_read_is_sorted_ascending(self, tmp_parquet_path: Path):
        dates = pd.to_datetime(["2023-01-04", "2023-01-02", "2023-01-03"])
        df = pd.DataFrame({"close": [3.0, 1.0, 2.0]}, index=dates)
        df.index.name = INDEX_COL
        write(df, tmp_parquet_path)
        loaded = read(tmp_parquet_path)
        assert loaded.index.is_monotonic_increasing

    def test_read_corrupt_file_raises(self, tmp_parquet_path: Path):
        tmp_parquet_path.write_bytes(b"this is not a parquet file")
        with pytest.raises(ParquetWriteError):
            read(tmp_parquet_path)


# ─────────────────────────────────────────────────────────────────────────────
# read_tail() tests
# ─────────────────────────────────────────────────────────────────────────────

class TestReadTail:
    def test_read_tail_returns_last_n_rows(self, tmp_parquet_path: Path, sample_ohlcv_df: pd.DataFrame):
        write(sample_ohlcv_df, tmp_parquet_path)
        tail = read_tail(tmp_parquet_path, 50)
        assert len(tail) == 50
        # Should be the LAST 50 rows — latest dates
        full = read(tmp_parquet_path)
        pd.testing.assert_frame_equal(tail, full.iloc[-50:])

    def test_read_tail_short_file_returns_all(self, tmp_parquet_path: Path, small_ohlcv_df: pd.DataFrame):
        """If file has fewer than n rows, return all rows (no error)."""
        write(small_ohlcv_df, tmp_parquet_path)
        tail = read_tail(tmp_parquet_path, 300)
        assert len(tail) == len(small_ohlcv_df)

    def test_read_tail_n_equals_length(self, tmp_parquet_path: Path, sample_ohlcv_df: pd.DataFrame):
        write(sample_ohlcv_df, tmp_parquet_path)
        tail = read_tail(tmp_parquet_path, len(sample_ohlcv_df))
        assert len(tail) == len(sample_ohlcv_df)


# ─────────────────────────────────────────────────────────────────────────────
# read_tail_at_least() tests
# ─────────────────────────────────────────────────────────────────────────────

class TestReadTailAtLeast:
    def test_sufficient_rows_returned(self, tmp_parquet_path: Path, sample_ohlcv_df: pd.DataFrame):
        write(sample_ohlcv_df, tmp_parquet_path)
        result = read_tail_at_least(tmp_parquet_path, 200, symbol="TEST")
        assert len(result) == 200

    def test_insufficient_rows_raises(self, tmp_parquet_path: Path, small_ohlcv_df: pd.DataFrame):
        write(small_ohlcv_df, tmp_parquet_path)
        with pytest.raises(InsufficientDataError) as exc_info:
            read_tail_at_least(tmp_parquet_path, 200, symbol="TEST")
        err = exc_info.value
        # Check structured context fields
        assert err.context["required"] == 200
        assert err.context["available"] == len(small_ohlcv_df)

    def test_symbol_appears_in_error(self, tmp_parquet_path: Path, small_ohlcv_df: pd.DataFrame):
        write(small_ohlcv_df, tmp_parquet_path)
        with pytest.raises(InsufficientDataError, match="RELIANCE"):
            read_tail_at_least(tmp_parquet_path, 200, symbol="RELIANCE")

    def test_exact_boundary_passes(self, tmp_parquet_path: Path, sample_ohlcv_df: pd.DataFrame):
        """Requesting exactly as many rows as exist should succeed."""
        write(sample_ohlcv_df, tmp_parquet_path)
        n = len(sample_ohlcv_df)
        result = read_tail_at_least(tmp_parquet_path, n, symbol="TEST")
        assert len(result) == n


# ─────────────────────────────────────────────────────────────────────────────
# read_date_range() tests
# ─────────────────────────────────────────────────────────────────────────────

class TestReadDateRange:
    def test_start_filter(self, tmp_parquet_path: Path, sample_ohlcv_df: pd.DataFrame):
        write(sample_ohlcv_df, tmp_parquet_path)
        cutoff = date(2023, 6, 1)
        result = read_date_range(tmp_parquet_path, start=cutoff)
        assert all(row >= pd.Timestamp(cutoff) for row in result.index)

    def test_end_filter(self, tmp_parquet_path: Path, sample_ohlcv_df: pd.DataFrame):
        write(sample_ohlcv_df, tmp_parquet_path)
        cutoff = date(2023, 4, 1)
        result = read_date_range(tmp_parquet_path, end=cutoff)
        assert all(row <= pd.Timestamp(cutoff) for row in result.index)

    def test_both_bounds(self, tmp_parquet_path: Path, sample_ohlcv_df: pd.DataFrame):
        write(sample_ohlcv_df, tmp_parquet_path)
        s = date(2023, 3, 1)
        e = date(2023, 5, 31)
        result = read_date_range(tmp_parquet_path, start=s, end=e)
        assert all(pd.Timestamp(s) <= row <= pd.Timestamp(e) for row in result.index)

    def test_no_bounds_returns_all(self, tmp_parquet_path: Path, sample_ohlcv_df: pd.DataFrame):
        write(sample_ohlcv_df, tmp_parquet_path)
        result = read_date_range(tmp_parquet_path)
        assert len(result) == len(sample_ohlcv_df)

    def test_empty_range_returns_empty(self, tmp_parquet_path: Path, sample_ohlcv_df: pd.DataFrame):
        write(sample_ohlcv_df, tmp_parquet_path)
        result = read_date_range(tmp_parquet_path, start=date(2099, 1, 1), end=date(2099, 1, 31))
        assert result.empty


# ─────────────────────────────────────────────────────────────────────────────
# exists(), row_count(), last_date() tests
# ─────────────────────────────────────────────────────────────────────────────

class TestMetaHelpers:
    def test_exists_false_for_missing(self, tmp_parquet_path: Path):
        assert not exists(tmp_parquet_path)

    def test_exists_true_after_write(self, tmp_parquet_path: Path, sample_ohlcv_df: pd.DataFrame):
        write(sample_ohlcv_df, tmp_parquet_path)
        assert exists(tmp_parquet_path)

    def test_row_count_zero_for_missing(self, tmp_parquet_path: Path):
        assert row_count(tmp_parquet_path) == 0

    def test_row_count_matches_data(self, tmp_parquet_path: Path, sample_ohlcv_df: pd.DataFrame):
        write(sample_ohlcv_df, tmp_parquet_path)
        assert row_count(tmp_parquet_path) == len(sample_ohlcv_df)

    def test_last_date_none_for_missing(self, tmp_parquet_path: Path):
        assert last_date(tmp_parquet_path) is None

    def test_last_date_returns_max_date(self, tmp_parquet_path: Path, sample_ohlcv_df: pd.DataFrame):
        write(sample_ohlcv_df, tmp_parquet_path)
        expected = sample_ohlcv_df.index.max().date()
        assert last_date(tmp_parquet_path) == expected


# ─────────────────────────────────────────────────────────────────────────────
# append_row() tests
# ─────────────────────────────────────────────────────────────────────────────

class TestAppendRow:
    def test_append_creates_file(self, tmp_parquet_path: Path):
        row = _row(date(2023, 1, 2))
        append_row(tmp_parquet_path, row)
        assert tmp_parquet_path.exists()
        loaded = read(tmp_parquet_path)
        assert len(loaded) == 1

    def test_append_increments_row_count(self, tmp_parquet_path: Path, sample_ohlcv_df: pd.DataFrame):
        write(sample_ohlcv_df, tmp_parquet_path)
        n_before = len(sample_ohlcv_df)
        # Next trading day after the last row
        next_day = sample_ohlcv_df.index.max() + pd.Timedelta(days=3)  # skip weekend
        new_row = _row(next_day.date())
        append_row(tmp_parquet_path, new_row)
        loaded = read(tmp_parquet_path)
        assert len(loaded) == n_before + 1

    def test_append_new_row_is_last(self, tmp_parquet_path: Path, sample_ohlcv_df: pd.DataFrame):
        write(sample_ohlcv_df, tmp_parquet_path)
        next_day = sample_ohlcv_df.index.max() + pd.Timedelta(days=3)
        new_row = _row(next_day.date(), close=9999.0)
        append_row(tmp_parquet_path, new_row)
        loaded = read(tmp_parquet_path)
        assert loaded.iloc[-1]["close"] == 9999.0

    def test_append_duplicate_raises(self, tmp_parquet_path: Path, sample_ohlcv_df: pd.DataFrame):
        write(sample_ohlcv_df, tmp_parquet_path)
        existing_date = sample_ohlcv_df.index[-1].date()
        dup_row = _row(existing_date)
        with pytest.raises(DuplicateDateError) as exc_info:
            append_row(tmp_parquet_path, dup_row)
        assert str(existing_date) in str(exc_info.value)

    def test_append_row_must_be_single_row(self, tmp_parquet_path: Path, sample_ohlcv_df: pd.DataFrame):
        write(sample_ohlcv_df, tmp_parquet_path)
        two_rows = _multi_row(2, start=date(2025, 1, 6))
        with pytest.raises(ValueError, match="1 row"):
            append_row(tmp_parquet_path, two_rows)

    def test_append_preserves_sort_order(self, tmp_parquet_path: Path):
        """Even if appended out of calendar order, index stays sorted."""
        row1 = _row(date(2023, 1, 4))
        row2 = _row(date(2023, 1, 2))   # earlier date appended second
        append_row(tmp_parquet_path, row1)
        append_row(tmp_parquet_path, row2)
        loaded = read(tmp_parquet_path)
        assert loaded.index.is_monotonic_increasing

    def test_append_does_not_corrupt_on_error(self, tmp_parquet_path: Path, sample_ohlcv_df: pd.DataFrame):
        """
        Verify the .tmp file is cleaned up and original file is intact
        after a duplicate error (atomic write guarantee).
        """
        write(sample_ohlcv_df, tmp_parquet_path)
        existing_date = sample_ohlcv_df.index[-1].date()
        dup_row = _row(existing_date)

        with pytest.raises(DuplicateDateError):
            append_row(tmp_parquet_path, dup_row)

        # Original must still be readable and unchanged
        loaded = read(tmp_parquet_path)
        assert len(loaded) == len(sample_ohlcv_df)
        # No leftover .tmp file
        tmp = tmp_parquet_path.with_suffix(".tmp.parquet")
        assert not tmp.exists()


# ─────────────────────────────────────────────────────────────────────────────
# append_dataframe() tests
# ─────────────────────────────────────────────────────────────────────────────

class TestAppendDataframe:
    def test_creates_file_when_missing(self, tmp_parquet_path: Path, sample_ohlcv_df: pd.DataFrame):
        append_dataframe(tmp_parquet_path, sample_ohlcv_df)
        assert exists(tmp_parquet_path)
        assert row_count(tmp_parquet_path) == len(sample_ohlcv_df)

    def test_appends_new_rows(self, tmp_parquet_path: Path, sample_ohlcv_df: pd.DataFrame):
        write(sample_ohlcv_df, tmp_parquet_path)
        # Generate 5 more rows starting after the last date
        last = sample_ohlcv_df.index.max().date() + timedelta(days=4)  # skip weekend
        extra = _multi_row(5, start=last)
        append_dataframe(tmp_parquet_path, extra)
        loaded = read(tmp_parquet_path)
        assert len(loaded) == len(sample_ohlcv_df) + 5

    def test_idempotent_on_overlapping_dates(self, tmp_parquet_path: Path, sample_ohlcv_df: pd.DataFrame):
        """Re-appending rows that already exist should be silently skipped."""
        write(sample_ohlcv_df, tmp_parquet_path)
        # Try to append the last 20 rows again
        overlap = sample_ohlcv_df.iloc[-20:]
        append_dataframe(tmp_parquet_path, overlap)
        loaded = read(tmp_parquet_path)
        # Row count must not have grown
        assert len(loaded) == len(sample_ohlcv_df)

    def test_partial_overlap_only_adds_new(self, tmp_parquet_path: Path, sample_ohlcv_df: pd.DataFrame):
        """When batch has some old and some new dates, only new rows are added."""
        first_half = sample_ohlcv_df.iloc[:150]
        write(first_half, tmp_parquet_path)

        # Second call: last 50 of first half (overlap) + 50 truly new
        last_new_start = sample_ohlcv_df.index[150].date() + timedelta(days=1)
        new_rows = _multi_row(50, start=last_new_start)
        mixed = pd.concat([sample_ohlcv_df.iloc[100:150], new_rows])
        append_dataframe(tmp_parquet_path, mixed)

        loaded = read(tmp_parquet_path)
        assert len(loaded) == 150 + 50


# ─────────────────────────────────────────────────────────────────────────────
# deduplicate() tests
# ─────────────────────────────────────────────────────────────────────────────

class TestDeduplicate:
    def test_clean_file_returns_zero(self, tmp_parquet_path: Path, sample_ohlcv_df: pd.DataFrame):
        write(sample_ohlcv_df, tmp_parquet_path)
        removed = deduplicate(tmp_parquet_path)
        assert removed == 0

    def test_removes_duplicate_rows(self, tmp_parquet_path: Path):
        d1, d2 = date(2023, 1, 2), date(2023, 1, 3)
        # Manually build a df with duplicated index
        df = pd.DataFrame(
            {"close": [100.0, 101.0, 102.0]},
            index=pd.DatetimeIndex([pd.Timestamp(d1), pd.Timestamp(d1), pd.Timestamp(d2)],
                                   name=INDEX_COL),
        )
        # Write directly (bypass our write() guard) to simulate corruption
        df.to_parquet(str(tmp_parquet_path), index=True, engine="pyarrow")

        removed = deduplicate(tmp_parquet_path)
        assert removed == 1
        loaded = read(tmp_parquet_path)
        assert len(loaded) == 2
        assert not loaded.index.duplicated().any()

    def test_missing_file_raises(self, tmp_parquet_path: Path):
        with pytest.raises(FileNotFoundError):
            deduplicate(tmp_parquet_path)


# ─────────────────────────────────────────────────────────────────────────────
# is_corrupt() tests
# ─────────────────────────────────────────────────────────────────────────────

class TestIsCorrupt:
    def test_missing_file_is_corrupt(self, tmp_parquet_path: Path):
        assert is_corrupt(tmp_parquet_path)

    def test_valid_file_not_corrupt(self, tmp_parquet_path: Path, sample_ohlcv_df: pd.DataFrame):
        write(sample_ohlcv_df, tmp_parquet_path)
        assert not is_corrupt(tmp_parquet_path)

    def test_garbage_file_is_corrupt(self, tmp_parquet_path: Path):
        tmp_parquet_path.write_bytes(b"\x00\x01\x02garbage data")
        assert is_corrupt(tmp_parquet_path)


# ─────────────────────────────────────────────────────────────────────────────
# copy_safe() tests
# ─────────────────────────────────────────────────────────────────────────────

class TestCopySafe:
    def test_copy_creates_dst(self, tmp_path: Path, sample_ohlcv_df: pd.DataFrame):
        src = tmp_path / "src.parquet"
        dst = tmp_path / "dst.parquet"
        write(sample_ohlcv_df, src)
        copy_safe(src, dst)
        assert dst.exists()

    def test_copy_content_matches(self, tmp_path: Path, sample_ohlcv_df: pd.DataFrame):
        src = tmp_path / "src.parquet"
        dst = tmp_path / "dst.parquet"
        write(sample_ohlcv_df, src)
        copy_safe(src, dst)
        original = read(src)
        copied = read(dst)
        pd.testing.assert_frame_equal(original, copied)

    def test_copy_missing_src_raises(self, tmp_path: Path):
        src = tmp_path / "nonexistent.parquet"
        dst = tmp_path / "dst.parquet"
        with pytest.raises(FileNotFoundError):
            copy_safe(src, dst)

    def test_copy_creates_nested_dirs(self, tmp_path: Path, sample_ohlcv_df: pd.DataFrame):
        src = tmp_path / "src.parquet"
        dst = tmp_path / "a" / "b" / "dst.parquet"
        write(sample_ohlcv_df, src)
        copy_safe(src, dst)
        assert dst.exists()


# ─────────────────────────────────────────────────────────────────────────────
# INDEX_COL constant
# ─────────────────────────────────────────────────────────────────────────────

def test_index_col_is_date():
    """The constant used throughout must stay 'date'."""
    assert INDEX_COL == "date"
