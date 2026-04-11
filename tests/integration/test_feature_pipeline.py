"""
tests/integration/test_feature_pipeline.py
───────────────────────────────────────────
Integration tests for the Phase 2 feature pipeline.

These tests exercise the FULL stack:
    conftest synthetic OHLCV
    → features/feature_store.bootstrap()  /  update()
    → storage/parquet_store (read/write)
    → output Parquet column assertions

No network calls are made — fetch_benchmark() is patched in all tests
to return the same synthetic OHLCV DataFrame used as the symbol's data.

Run with:
    pytest tests/integration/test_feature_pipeline.py -v

Test matrix
───────────
    test_bootstrap_produces_all_columns       — all 26 expected columns present,
                                               no fully-NaN column in last 100 rows
    test_update_appends_correct_date          — bootstrap up to D-1, update with D,
                                               assert index[-1] == D and row+1
    test_insufficient_history_handled_gracefully — 100-row OHLCV (too short for
                                               SMA_200) must NOT raise, must NOT
                                               create a feature file
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

import storage.parquet_store as parquet_store
from features.feature_store import bootstrap, update

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_FETCH_BENCHMARK = "features.feature_store.fetch_benchmark"

# All 26 columns the Phase 2 feature pipeline must produce.
EXPECTED_COLUMNS: list[str] = [
    # Moving averages
    "SMA_10", "SMA_21", "SMA_50", "SMA_150", "SMA_200",
    "EMA_21",
    "MA_slope_50", "MA_slope_200",
    # Relative strength
    "RS_raw",
    # ATR
    "ATR_14", "ATR_pct",
    # Volume
    "vol_50d_avg", "vol_ratio",
    "up_vol_days", "down_vol_days", "acc_dist_score",
    # Pivot
    "is_swing_high", "is_swing_low",
    "last_pivot_high", "last_pivot_low",
    # VCP
    "vcp_contraction_count", "vcp_max_depth_pct", "vcp_final_depth_pct",
    "vcp_vol_ratio", "vcp_base_weeks", "vcp_is_valid",
]

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_ohlcv(n_rows: int, start: date | None = None) -> pd.DataFrame:
    """
    Deterministic OHLCV builder (mirrors conftest._make_ohlcv exactly).
    Duplicated here so the integration module has no import dependency on
    conftest (which lives in a pytest-private namespace).
    """
    start_date = start or date(2023, 1, 2)
    rows = []
    d = start_date
    price = 1_000.0
    for i in range(n_rows):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        noise = math.sin(i * 0.3) * 5
        open_ = round(price + noise, 2)
        close = round(open_ * 1.0005, 2)
        high  = round(max(open_, close) * 1.005, 2)
        low   = round(min(open_, close) * 0.995, 2)
        volume = int(500_000 + (i % 50) * 10_000)
        rows.append({
            "date":   pd.Timestamp(d),
            "open":   open_,
            "high":   high,
            "low":    low,
            "close":  close,
            "volume": volume,
        })
        price = close
        d += timedelta(days=1)
    df = pd.DataFrame(rows).set_index("date")
    df.index.name = "date"
    return df


def _next_trading_day(ts: pd.Timestamp) -> pd.Timestamp:
    """Return the first weekday strictly after *ts*."""
    nxt = ts + pd.Timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += pd.Timedelta(days=1)
    return nxt


def _one_row_ohlcv(ts: pd.Timestamp, base_df: pd.DataFrame) -> pd.DataFrame:
    """
    Build a single-row OHLCV row that continues cleanly from *base_df*.
    Uses the last close of base_df as a realistic price reference so that
    the rolling window calculations in update() don't see a price jump.
    """
    prev_close = float(base_df["close"].iloc[-1])
    return pd.DataFrame(
        {
            "open":   [round(prev_close * 1.001, 2)],
            "high":   [round(prev_close * 1.006, 2)],
            "low":    [round(prev_close * 0.995, 2)],
            "close":  [round(prev_close * 1.003, 2)],
            "volume": [700_000],
        },
        index=pd.DatetimeIndex([ts], name="date"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Shared fixture
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def test_config(tmp_path: Path) -> dict:
    """
    Minimal AppConfig that redirects all data dirs into pytest's tmp_path.
    Each test gets a completely fresh, isolated directory tree.
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
            "detector":                "rule_based",
            "pivot_window":            5,
            "min_contractions":        2,
            "max_contractions":        5,
            "require_declining_depth": True,
            "require_vol_contraction": True,
            "min_weeks":               3,
            "max_weeks":               52,
            "tightness_pct":           10.0,
            "max_depth_pct":           50.0,
        },
        "atr": {"period": 14},
    }


# ─────────────────────────────────────────────────────────────────────────────
# Test 1 — bootstrap() produces every expected column, no fully-NaN tail
# ─────────────────────────────────────────────────────────────────────────────

class TestBootstrapProducesAllColumns:
    """
    Validates that bootstrap() writes a Parquet file containing ALL 26 expected
    feature columns and that none of the rolling-window columns is entirely NaN
    in the most recent 100 rows.

    Why check the last 100 rows?
        The longest rolling window is SMA_200 (needs 200 rows).  A 300-row
        dataset leaves 100 rows where every rolling window has fully warmed up.
        If any of those 100 rows is all-NaN for a rolling column, it signals
        a pipeline bug (wrong column name, wrong computation, silent failure).

    VCP / slope columns (MA_slope_*, vcp_*) are by design NaN on all rows
    except the last — the pipeline spec says "meaningful only on the LAST row".
    For those we apply a weaker assertion: the last row must carry a non-NaN value.

    Pivot boolean columns (is_swing_high, is_swing_low) use pandas BooleanArray
    (True / False / pd.NA), not float NaN, so they require pd.notna() checks
    rather than .isna().
    """

    # Columns that the pipeline spec defines as "last-row-only" (NaN elsewhere).
    _LAST_ROW_ONLY_COLS: frozenset[str] = frozenset({
        "MA_slope_50", "MA_slope_200",
        "vcp_contraction_count", "vcp_max_depth_pct", "vcp_final_depth_pct",
        "vcp_vol_ratio", "vcp_base_weeks", "vcp_is_valid",
    })

    # BooleanArray columns — use pd.notna() not .isna()
    _PIVOT_BOOL_COLS: frozenset[str] = frozenset({"is_swing_high", "is_swing_low"})

    def test_bootstrap_produces_all_columns(
        self, test_config: dict, tmp_path: Path
    ) -> None:
        """bootstrap() on a 300-row symbol writes all 26 expected feature columns."""
        # ── Arrange ────────────────────────────────────────────────────────
        symbol = "TESTSTOCK"
        ohlcv_df = _make_ohlcv(300)
        processed_path = (
            Path(test_config["data"]["processed_dir"]) / f"{symbol}.parquet"
        )
        parquet_store.write(ohlcv_df, processed_path, overwrite=True)
        feature_path = (
            Path(test_config["data"]["features_dir"]) / f"{symbol}.parquet"
        )

        # ── Act ────────────────────────────────────────────────────────────
        with patch(_FETCH_BENCHMARK, return_value=ohlcv_df):
            bootstrap(symbol, test_config)

        # ── Assert: file created ────────────────────────────────────────────
        assert feature_path.exists(), (
            "bootstrap() did not create the feature Parquet file"
        )

        result_df = parquet_store.read(feature_path)

        # ── Assert: all 26 columns present ─────────────────────────────────
        missing = [c for c in EXPECTED_COLUMNS if c not in result_df.columns]
        assert not missing, (
            f"Feature file is missing {len(missing)} expected column(s):\n"
            + "\n".join(f"  - {c}" for c in missing)
        )

        # ── Assert: tail-100 has no fully-NaN rolling columns ──────────────
        tail = result_df.iloc[-100:]
        for col in EXPECTED_COLUMNS:
            if col in self._LAST_ROW_ONLY_COLS:
                # Only the final row must be non-null for these columns.
                last_val = result_df[col].iloc[-1]
                assert pd.notna(last_val), (
                    f"Column '{col}' (last-row-only): expected a non-NaN value "
                    f"on the final row, got {last_val!r}"
                )
            elif col in self._PIVOT_BOOL_COLS:
                # BooleanArray — check via pd.notna across the tail.
                n_valid = tail[col].apply(pd.notna).sum()
                assert n_valid > 0, (
                    f"Pivot column '{col}' is entirely pd.NA in the last 100 rows"
                )
            else:
                n_nan = tail[col].isna().sum()
                assert n_nan < len(tail), (
                    f"Column '{col}' is entirely NaN in the last 100 rows "
                    f"(all {len(tail)} values are NaN) — pipeline bug?"
                )

        # ── Assert: row count matches the processed file ───────────────────
        assert len(result_df) == len(ohlcv_df), (
            f"Expected {len(ohlcv_df)} rows in the feature file, "
            f"got {len(result_df)}"
        )

        # ── Assert: DatetimeIndex is monotonically increasing ──────────────
        assert result_df.index.is_monotonic_increasing, (
            "Feature file DatetimeIndex is not sorted ascending"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test 2 — update() appends exactly one row at the correct date
# ─────────────────────────────────────────────────────────────────────────────

class TestUpdateAppendsCorrectDate:
    """
    Validates the incremental update path end-to-end:

        1. Write 300 rows of OHLCV + bootstrap → feature file with N rows.
        2. Append one new OHLCV row at date D (the next trading day) to the
           processed file.
        3. Call update(symbol, run_date=D, config).
        4. Assert feature file has N+1 rows.
        5. Assert feature file's last index == D.

    Two guard tests are also included:
        - update() without prior bootstrap raises FeatureStoreMissingError.
        - Second update() call with the same date raises FeatureStoreOutOfSyncError.
    """

    def test_update_appends_correct_date(
        self, test_config: dict
    ) -> None:
        """update() appends exactly one row at the correct date D."""
        # ── Arrange ────────────────────────────────────────────────────────
        symbol = "UPDATETEST"
        ohlcv_df = _make_ohlcv(300)
        processed_path = (
            Path(test_config["data"]["processed_dir"]) / f"{symbol}.parquet"
        )
        parquet_store.write(ohlcv_df, processed_path, overwrite=True)

        with patch(_FETCH_BENCHMARK, return_value=ohlcv_df):
            bootstrap(symbol, test_config)

        feature_path = (
            Path(test_config["data"]["features_dir"]) / f"{symbol}.parquet"
        )
        assert feature_path.exists(), "bootstrap() must have created the feature file"

        rows_before = parquet_store.row_count(feature_path)
        last_processed_ts = ohlcv_df.index[-1]

        # Append one new OHLCV row (the next trading day after the last row).
        run_date_ts = _next_trading_day(last_processed_ts)
        new_row = _one_row_ohlcv(run_date_ts, ohlcv_df)
        parquet_store.append_row(processed_path, new_row)
        run_date = run_date_ts.date()

        # Build extended OHLCV for the benchmark mock (RS_raw needs aligned index).
        extended_ohlcv = pd.concat([ohlcv_df, new_row])

        # ── Act ────────────────────────────────────────────────────────────
        with patch(_FETCH_BENCHMARK, return_value=extended_ohlcv):
            update(symbol, run_date, test_config)

        # ── Assert: row count +1 ───────────────────────────────────────────
        rows_after = parquet_store.row_count(feature_path)
        assert rows_after == rows_before + 1, (
            f"Expected {rows_before + 1} rows after update(), got {rows_after}"
        )

        # ── Assert: last index == run_date ─────────────────────────────────
        result_df = parquet_store.read(feature_path)
        actual_last_date = result_df.index[-1].date()
        assert actual_last_date == run_date, (
            f"Expected last row date {run_date}, got {actual_last_date}"
        )

    def test_update_raises_if_feature_file_missing(
        self, test_config: dict
    ) -> None:
        """
        update() must raise FeatureStoreMissingError when called before
        bootstrap() has created the feature file.
        The daily runner guards against this, but a direct call must fail loudly.
        """
        from utils.exceptions import FeatureStoreMissingError

        symbol = "NOFEATUREFILE"
        ohlcv_df = _make_ohlcv(300)
        processed_path = (
            Path(test_config["data"]["processed_dir"]) / f"{symbol}.parquet"
        )
        parquet_store.write(ohlcv_df, processed_path, overwrite=True)
        run_date = ohlcv_df.index[-1].date() + timedelta(days=1)

        with pytest.raises(FeatureStoreMissingError):
            with patch(_FETCH_BENCHMARK, return_value=ohlcv_df):
                update(symbol, run_date, test_config)

    def test_update_raises_on_duplicate_date(
        self, test_config: dict
    ) -> None:
        """
        Calling update() twice with the same run_date must raise
        FeatureStoreOutOfSyncError — the idempotent guard must fire.
        """
        from utils.exceptions import FeatureStoreOutOfSyncError

        symbol = "DUPDATE"
        ohlcv_df = _make_ohlcv(300)
        processed_path = (
            Path(test_config["data"]["processed_dir"]) / f"{symbol}.parquet"
        )
        parquet_store.write(ohlcv_df, processed_path, overwrite=True)

        with patch(_FETCH_BENCHMARK, return_value=ohlcv_df):
            bootstrap(symbol, test_config)

        # Append one new row, first update succeeds.
        run_date_ts = _next_trading_day(ohlcv_df.index[-1])
        new_row = _one_row_ohlcv(run_date_ts, ohlcv_df)
        parquet_store.append_row(processed_path, new_row)
        run_date = run_date_ts.date()
        extended = pd.concat([ohlcv_df, new_row])

        with patch(_FETCH_BENCHMARK, return_value=extended):
            update(symbol, run_date, test_config)   # first call — must succeed

        # Second call with the same date must raise.
        with pytest.raises(FeatureStoreOutOfSyncError):
            with patch(_FETCH_BENCHMARK, return_value=extended):
                update(symbol, run_date, test_config)


# ─────────────────────────────────────────────────────────────────────────────
# Test 3 — Insufficient history is handled gracefully
# ─────────────────────────────────────────────────────────────────────────────

class TestInsufficientHistoryHandledGracefully:
    """
    Validates the InsufficientDataError contract from PROJECT_DESIGN.md §19.1:

        "InsufficientDataError from any feature module is caught, logged as a
         warning, and the function returns None cleanly (never re-raised)."

    Concretely, for a symbol with only 100 rows of OHLCV (too short for
    SMA_200 which requires 200 rows):

        1. bootstrap() must NOT raise — it must return None.
        2. bootstrap() must NOT create the feature Parquet file.
        3. bootstrap() must emit at least one WARNING-level log record
           containing the symbol name (verifies the warning path is taken).

    Using 100 rows is deliberate: it is above the 50-row minimum for ATR/volume
    but below the 200-row minimum for SMA_200, which is the first hard check in
    moving_averages.compute() that raises InsufficientDataError.
    """

    def test_insufficient_history_does_not_raise(
        self, test_config: dict
    ) -> None:
        """
        bootstrap() with a 100-row symbol must return None without raising.
        """
        symbol = "SHORTDATA"
        short_ohlcv = _make_ohlcv(100)
        processed_path = (
            Path(test_config["data"]["processed_dir"]) / f"{symbol}.parquet"
        )
        parquet_store.write(short_ohlcv, processed_path, overwrite=True)

        # Must not raise — InsufficientDataError must be caught internally.
        with patch(_FETCH_BENCHMARK, return_value=short_ohlcv):
            result = bootstrap(symbol, test_config)

        assert result is None, (
            "bootstrap() must return None on InsufficientDataError, "
            f"got {result!r}"
        )

    def test_insufficient_history_does_not_create_feature_file(
        self, test_config: dict
    ) -> None:
        """
        bootstrap() with a 100-row symbol must NOT write a feature Parquet file.
        Writing an incomplete file would silently corrupt the feature store.
        """
        symbol = "SHORTDATA2"
        short_ohlcv = _make_ohlcv(100)
        processed_path = (
            Path(test_config["data"]["processed_dir"]) / f"{symbol}.parquet"
        )
        parquet_store.write(short_ohlcv, processed_path, overwrite=True)
        feature_path = (
            Path(test_config["data"]["features_dir"]) / f"{symbol}.parquet"
        )

        with patch(_FETCH_BENCHMARK, return_value=short_ohlcv):
            bootstrap(symbol, test_config)

        assert not feature_path.exists(), (
            "bootstrap() must NOT create a feature file when data is insufficient. "
            "An incomplete file would poison the feature store."
        )

    def test_insufficient_history_logs_warning(
        self, test_config: dict, caplog: pytest.LogCaptureFixture
    ) -> None:
        """
        bootstrap() with a 100-row symbol must emit a WARNING log that includes
        the symbol name, so operators can identify which symbols need more history.
        """
        import logging

        symbol = "WARNTEST"
        short_ohlcv = _make_ohlcv(100)
        processed_path = (
            Path(test_config["data"]["processed_dir"]) / f"{symbol}.parquet"
        )
        parquet_store.write(short_ohlcv, processed_path, overwrite=True)

        with caplog.at_level(logging.WARNING):
            with patch(_FETCH_BENCHMARK, return_value=short_ohlcv):
                bootstrap(symbol, test_config)

        # At least one WARNING must reference the symbol.
        warning_records = [
            r for r in caplog.records
            if r.levelno >= logging.WARNING
        ]
        assert warning_records, (
            "bootstrap() emitted no WARNING log records for an insufficient-data symbol"
        )

        # structlog binds keyword arguments (symbol=symbol) as top-level
        # attributes on the LogRecord — they do NOT appear in getMessage().
        # We therefore check r.symbol (the structlog-bound field) rather than
        # the rendered message string, which only contains the log message text.
        #
        # Fallback: if a future logger change embeds the symbol in the message
        # string directly (e.g. f"bootstrap skipped: {symbol}"), the second
        # branch of the 'or' will also catch it.
        def _record_mentions_symbol(r: logging.LogRecord) -> bool:
            structlog_field = getattr(r, "symbol", None) == symbol
            in_message      = symbol in r.getMessage()
            return structlog_field or in_message

        assert any(_record_mentions_symbol(r) for r in warning_records), (
            f"Expected the symbol name '{symbol}' to appear in a WARNING log "
            f"(either as a structlog bound field r.symbol or in the message text).\n"
            f"Records captured: {[(r.getMessage(), vars(r).get('symbol')) for r in warning_records]}"
        )
