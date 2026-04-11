"""
tests/unit/test_feature_store.py
─────────────────────────────────
Unit tests for features/feature_store.py.

Test matrix (PROJECT_DESIGN.md §5.3)
──────────────────────────────────────
    test_needs_bootstrap_true_when_missing   — absent file  → True
    test_needs_bootstrap_false_when_present  — file with rows → False
    test_bootstrap_creates_feature_file      — file created, has expected columns
    test_update_appends_one_row              — row count +1 after update
    test_update_idempotent_guard             — second update same date → OutOfSync
    test_update_missing_feature_file_raises  — no feature file → Missing

Design notes
────────────
    • All tests use a tmp_path-based config so they never touch real data dirs.
    • fetch_benchmark is patched to return the same synthetic df as the
      processed data — avoids any yfinance / network calls.
    • The 300-row sample_ohlcv_df fixture (from conftest.py) is reused; it
      satisfies all feature module minimums (SMA_200 needs >= 200 rows).
    • Tests that call update() need a *new* date beyond the processed data's
      last date; we append one synthetic OHLCV row to the processed file.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

import storage.parquet_store as parquet_store
from features.feature_store import ROLLING_WINDOW, bootstrap, needs_bootstrap, update
from utils.exceptions import FeatureStoreMissingError, FeatureStoreOutOfSyncError

# ─────────────────────────────────────────────────────────────────────────────
# Patch target
# ─────────────────────────────────────────────────────────────────────────────

_FETCH_BENCHMARK_TARGET = "features.feature_store.fetch_benchmark"

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _next_trading_day(ts: pd.Timestamp) -> pd.Timestamp:
    """Return the first weekday strictly after *ts*."""
    nxt = ts + pd.Timedelta(days=1)
    while nxt.weekday() >= 5:   # 5=Sat, 6=Sun
        nxt += pd.Timedelta(days=1)
    return nxt


def _one_row_ohlcv(ts: pd.Timestamp, close: float = 1_100.0) -> pd.DataFrame:
    """Build a single-row OHLCV DataFrame suitable for append_row()."""
    return pd.DataFrame(
        {
            "open":   [close * 0.99],
            "high":   [close * 1.005],
            "low":    [close * 0.985],
            "close":  [close],
            "volume": [600_000],
        },
        index=pd.DatetimeIndex([ts], name="date"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def test_config(tmp_path: Path) -> dict:
    """
    Minimal AppConfig dict that points all data dirs into pytest's tmp_path.
    Directories are created so feature modules can write freely.
    """
    processed_dir = tmp_path / "data" / "processed"
    features_dir  = tmp_path / "data" / "features"
    processed_dir.mkdir(parents=True, exist_ok=True)
    features_dir.mkdir(parents=True, exist_ok=True)

    return {
        "data": {
            "processed_dir": str(processed_dir),
            "features_dir":  str(features_dir),
        },
        "stage": {
            "ma200_slope_lookback": 20,
            "ma50_slope_lookback":  10,
        },
        "vcp": {
            "detector":               "rule_based",
            "pivot_window":           5,
            "min_contractions":       2,
            "max_contractions":       5,
            "require_declining_depth": True,
            "require_vol_contraction": True,
            "min_weeks":              3,
            "max_weeks":              52,
            "tightness_pct":          10.0,
            "max_depth_pct":          50.0,
        },
        "atr": {"period": 14},
    }


@pytest.fixture()
def processed_path(test_config: dict, sample_ohlcv_df: pd.DataFrame) -> Path:
    """
    Write sample_ohlcv_df (300 rows) to the processed dir as TEST.parquet
    and return the path.
    """
    path = Path(test_config["data"]["processed_dir"]) / "TEST.parquet"
    parquet_store.write(sample_ohlcv_df, path, overwrite=True)
    return path


@pytest.fixture()
def feature_path(test_config: dict) -> Path:
    """Return the expected feature file path for symbol 'TEST'."""
    return Path(test_config["data"]["features_dir"]) / "TEST.parquet"


@pytest.fixture()
def bootstrapped(
    test_config: dict,
    processed_path: Path,         # noqa: F811 — ensures processed file exists
    sample_ohlcv_df: pd.DataFrame,
) -> Path:
    """
    Run bootstrap('TEST', ...) once so that feature_path already exists.
    Returns the feature file Path.
    """
    with patch(_FETCH_BENCHMARK_TARGET, return_value=sample_ohlcv_df):
        bootstrap("TEST", test_config)
    return Path(test_config["data"]["features_dir"]) / "TEST.parquet"



# ─────────────────────────────────────────────────────────────────────────────
# Tests — needs_bootstrap
# ─────────────────────────────────────────────────────────────────────────────

class TestNeedsBootstrap:
    def test_needs_bootstrap_true_when_missing(
        self,
        test_config: dict,
    ) -> None:
        """Feature file absent → needs_bootstrap returns True."""
        # No file has been written — directory exists but file does not.
        result = needs_bootstrap("NOTEXIST", test_config)
        assert result is True

    def test_needs_bootstrap_false_when_present(
        self,
        test_config: dict,
        bootstrapped: Path,
    ) -> None:
        """Feature file exists with rows → needs_bootstrap returns False."""
        # bootstrapped fixture already ran bootstrap(); file has rows.
        result = needs_bootstrap("TEST", test_config)
        assert result is False

    def test_needs_bootstrap_true_when_empty_file(
        self,
        test_config: dict,
        sample_ohlcv_df: pd.DataFrame,
    ) -> None:
        """An empty (0-row) feature file → needs_bootstrap still returns True."""
        path = Path(test_config["data"]["features_dir"]) / "EMPTY.parquet"
        # Write a parquet file with no rows (same schema as sample).
        empty_df = sample_ohlcv_df.iloc[0:0]   # 0 rows, preserves columns
        parquet_store.write(empty_df, path, overwrite=True)

        result = needs_bootstrap("EMPTY", test_config)
        assert result is True



# ─────────────────────────────────────────────────────────────────────────────
# Tests — bootstrap
# ─────────────────────────────────────────────────────────────────────────────

class TestBootstrap:
    # Expected feature columns produced by the pipeline.
    # Checked as a subset — we allow extra columns (e.g. vcp_fail_reason).
    EXPECTED_COLUMNS = {
        # Moving averages
        "SMA_10", "SMA_21", "SMA_50", "SMA_150", "SMA_200",
        "EMA_21", "MA_slope_50", "MA_slope_200",
        # Relative strength
        "RS_raw",
        # ATR
        "ATR_14", "ATR_pct",
        # Volume
        "vol_50d_avg", "vol_ratio", "up_vol_days", "down_vol_days",
        "acc_dist_score",
        # Pivot
        "is_swing_high", "is_swing_low",
        "last_pivot_high", "last_pivot_low",
        # VCP
        "vcp_contraction_count", "vcp_max_depth_pct", "vcp_final_depth_pct",
        "vcp_vol_ratio", "vcp_base_weeks", "vcp_is_valid", "vcp_fail_reason",
    }

    def test_bootstrap_creates_feature_file(
        self,
        test_config: dict,
        processed_path: Path,
        sample_ohlcv_df: pd.DataFrame,
        feature_path: Path,
    ) -> None:
        """
        Running bootstrap() on a valid symbol:
          - creates the feature Parquet file
          - file has the same row count as the processed data
          - file contains all expected feature columns
        """
        assert not feature_path.exists(), "feature file should not exist before bootstrap"

        with patch(_FETCH_BENCHMARK_TARGET, return_value=sample_ohlcv_df):
            bootstrap("TEST", test_config)

        assert feature_path.exists(), "bootstrap() did not create the feature file"

        feature_df = parquet_store.read(feature_path)

        # Row count must equal input rows (300 for sample_ohlcv_df).
        assert len(feature_df) == len(sample_ohlcv_df), (
            f"expected {len(sample_ohlcv_df)} rows, got {len(feature_df)}"
        )

        # All expected columns must be present.
        missing = self.EXPECTED_COLUMNS - set(feature_df.columns)
        assert not missing, f"Feature file missing columns: {missing}"

    def test_bootstrap_is_idempotent(
        self,
        test_config: dict,
        processed_path: Path,
        sample_ohlcv_df: pd.DataFrame,
    ) -> None:
        """Calling bootstrap() twice overwrites cleanly — no duplicate rows."""
        with patch(_FETCH_BENCHMARK_TARGET, return_value=sample_ohlcv_df):
            bootstrap("TEST", test_config)
            bootstrap("TEST", test_config)

        feature_df = parquet_store.read(
            Path(test_config["data"]["features_dir"]) / "TEST.parquet"
        )
        assert len(feature_df) == len(sample_ohlcv_df)

    def test_bootstrap_insufficient_data_returns_none(
        self,
        test_config: dict,
        sample_ohlcv_df: pd.DataFrame,
    ) -> None:
        """
        If the processed file has < 200 rows, bootstrap must log a warning
        and return None — it must NOT raise.
        """
        # Write only 50 rows — not enough for SMA_200.
        short_df = sample_ohlcv_df.iloc[:50]
        short_path = (
            Path(test_config["data"]["processed_dir"]) / "SHORT.parquet"
        )
        parquet_store.write(short_df, short_path, overwrite=True)

        with patch(_FETCH_BENCHMARK_TARGET, return_value=sample_ohlcv_df):
            result = bootstrap("SHORT", test_config)  # must not raise

        assert result is None



# ─────────────────────────────────────────────────────────────────────────────
# Tests — update
# ─────────────────────────────────────────────────────────────────────────────

class TestUpdate:
    def test_update_appends_one_row(
        self,
        test_config: dict,
        processed_path: Path,
        sample_ohlcv_df: pd.DataFrame,
        bootstrapped: Path,
    ) -> None:
        """
        update() for a date after the last bootstrapped date must increase
        the feature file's row count by exactly 1.
        """
        feature_df_before = parquet_store.read(bootstrapped)
        rows_before = len(feature_df_before)

        # Synthesise one new trading day beyond the sample data.
        last_ts = feature_df_before.index[-1]
        new_ts  = _next_trading_day(last_ts)
        new_row = _one_row_ohlcv(new_ts)

        # Append that row to the processed file so the pipeline can read it.
        parquet_store.append_row(processed_path, new_row)

        run_date = new_ts.date()

        with patch(_FETCH_BENCHMARK_TARGET, return_value=sample_ohlcv_df):
            update("TEST", run_date, test_config)

        feature_df_after = parquet_store.read(bootstrapped)
        assert len(feature_df_after) == rows_before + 1, (
            f"expected {rows_before + 1} rows, got {len(feature_df_after)}"
        )

        # The appended row must be indexed to run_date.
        last_date = feature_df_after.index[-1].date()
        assert last_date == run_date, (
            f"last row date {last_date} does not match run_date {run_date}"
        )

    def test_update_idempotent_guard(
        self,
        test_config: dict,
        processed_path: Path,
        sample_ohlcv_df: pd.DataFrame,
        bootstrapped: Path,
    ) -> None:
        """
        Calling update() twice for the same run_date must raise
        FeatureStoreOutOfSyncError on the second call.
        """
        last_ts  = parquet_store.read(bootstrapped).index[-1]
        new_ts   = _next_trading_day(last_ts)
        new_row  = _one_row_ohlcv(new_ts)
        parquet_store.append_row(processed_path, new_row)

        run_date = new_ts.date()

        with patch(_FETCH_BENCHMARK_TARGET, return_value=sample_ohlcv_df):
            # First call — must succeed.
            update("TEST", run_date, test_config)

            # Second call — same date is now in the file → must raise.
            with pytest.raises(FeatureStoreOutOfSyncError):
                update("TEST", run_date, test_config)

    def test_update_missing_feature_file_raises(
        self,
        test_config: dict,
        processed_path: Path,
        sample_ohlcv_df: pd.DataFrame,
    ) -> None:
        """
        Calling update() when the feature file does not exist must raise
        FeatureStoreMissingError — caller must run bootstrap() first.
        """
        # No bootstrapped fixture here — feature file was never created.
        run_date = date(2025, 6, 1)

        with patch(_FETCH_BENCHMARK_TARGET, return_value=sample_ohlcv_df):
            with pytest.raises(FeatureStoreMissingError):
                update("TEST", run_date, test_config)

    def test_update_insufficient_data_returns_none(
        self,
        test_config: dict,
        sample_ohlcv_df: pd.DataFrame,
    ) -> None:
        """
        If the processed file has too few rows for the pipeline,
        update() must log a warning and return None — it must NOT raise.
        """
        # Write a short processed file (50 rows — below SMA_200 threshold).
        short_df  = sample_ohlcv_df.iloc[:50]
        short_proc = (
            Path(test_config["data"]["processed_dir"]) / "SHORTUPD.parquet"
        )
        parquet_store.write(short_df, short_proc, overwrite=True)

        # Create a minimal (1-row) feature file so Missing guard passes.
        feat_path = (
            Path(test_config["data"]["features_dir"]) / "SHORTUPD.parquet"
        )
        parquet_store.write(short_df.iloc[:1], feat_path, overwrite=True)

        run_date = short_df.index[-1].date() + timedelta(days=3)

        with patch(_FETCH_BENCHMARK_TARGET, return_value=sample_ohlcv_df):
            result = update("SHORTUPD", run_date, test_config)  # must not raise

        assert result is None
