"""
features/feature_store.py
─────────────────────────
Orchestration layer that reads processed OHLCV data, runs every feature
module in the correct order, and writes the results to the feature Parquet
store.

Public API (PROJECT_DESIGN.md §5.3)
────────────────────────────────────
    bootstrap(symbol, config)        → None
        Full history recompute.  Reads all rows from data/processed/, runs
        the complete pipeline, writes data/features/{symbol}.parquet.

    update(symbol, run_date, config) → None
        Fast daily path.  Loads only the last ROLLING_WINDOW rows, runs the
        pipeline, appends a single new row to the feature file.

    needs_bootstrap(symbol, config)  → bool
        Returns True when the feature file is absent or empty.

Pipeline execution order (fixed — MAs must come first)
────────────────────────────────────────────────────────
    moving_averages → relative_strength (RS_raw) → atr → volume → pivot → vcp

Design rules
────────────
    - Do NOT import from rules/ or screener/ — this is a lower layer.
    - Paths are resolved from config["data"]["features_dir"] and
      config["data"]["processed_dir"]; no hardcoded paths.
    - InsufficientDataError from any feature module is caught, logged as a
      warning, and the function returns None cleanly (never re-raised).
    - update() raises FeatureStoreOutOfSyncError (idempotent guard) if
      run_date is already in the feature file.
    - update() raises FeatureStoreMissingError if the feature file does not
      exist — caller must run bootstrap() first.
"""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

import storage.parquet_store as parquet_store
from features import moving_averages, atr, volume, pivot, vcp
from features.relative_strength import compute_rs_raw, fetch_benchmark
from storage.parquet_store import DuplicateDateError
from utils.exceptions import (
    FeatureStoreOutOfSyncError,
    FeatureStoreMissingError,
    InsufficientDataError,
)
from utils.logger import get_logger

log = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

ROLLING_WINDOW: int = 300  # rows loaded by update() — enough for SMA_200 + buffer

# Type alias — settings.yaml loaded as a plain dict
AppConfig = dict[str, Any]



# ─────────────────────────────────────────────────────────────────────────────
# Path helpers
# ─────────────────────────────────────────────────────────────────────────────

def _feature_path(symbol: str, config: AppConfig) -> Path:
    """Return the feature Parquet path for *symbol*."""
    return Path(config["data"]["features_dir"]) / f"{symbol}.parquet"


def _processed_path(symbol: str, config: AppConfig) -> Path:
    """Return the processed OHLCV Parquet path for *symbol*."""
    return Path(config["data"]["processed_dir"]) / f"{symbol}.parquet"


# ─────────────────────────────────────────────────────────────────────────────
# Internal pipeline
# ─────────────────────────────────────────────────────────────────────────────

def _run_pipeline(
    df: pd.DataFrame,
    config: AppConfig,
    benchmark_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Run all feature modules in fixed order and return the enriched DataFrame.

    Order: moving_averages → RS_raw → atr → volume → pivot → vcp

    Args:
        df:           OHLCV DataFrame (DatetimeIndex, at least ROLLING_WINDOW rows).
        config:       Application configuration dict.
        benchmark_df: Benchmark OHLCV DataFrame for relative-strength calculation.

    Returns:
        New DataFrame with all feature columns appended.

    Raises:
        InsufficientDataError: Propagated from any feature module when df is
            too short.  Callers (bootstrap/update) must catch and log this.
    """
    # 1. Moving averages first — downstream modules may need SMA columns.
    df = moving_averages.compute(df, config)

    # 2. Relative strength — returns a Series; assign it as a new column.
    rs_raw: pd.Series = compute_rs_raw(df, benchmark_df)
    df = df.assign(RS_raw=rs_raw)

    # 3. ATR
    df = atr.compute(df, config)

    # 4. Volume
    df = volume.compute(df, config)

    # 5. Pivot — must run before VCP (VCP reads is_swing_high / is_swing_low).
    df = pivot.compute(df, config)

    # 6. VCP
    df = vcp.compute(df, config)

    return df



# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def bootstrap(symbol: str, config: AppConfig) -> None:
    """
    Full-history feature computation for *symbol*.

    Reads the entire processed OHLCV file, runs the complete feature pipeline,
    and writes (overwrites) the feature Parquet file.

    Args:
        symbol: NSE symbol string (e.g. "DIXON").
        config: Application configuration dict loaded from settings.yaml.

    Returns:
        None — always.  InsufficientDataError is caught, logged, and swallowed.

    Logs:
        INFO  — symbol, rows processed, wall-clock duration on success.
        WARNING — symbol + reason when InsufficientDataError is raised by any
                  feature module; symbol is skipped gracefully.
    """
    t0 = time.monotonic()
    processed_path = _processed_path(symbol, config)
    feature_path = _feature_path(symbol, config)

    try:
        df = parquet_store.read(processed_path)
        benchmark_df = fetch_benchmark(config)
        feature_df = _run_pipeline(df, config, benchmark_df)
        parquet_store.write(feature_df, feature_path, overwrite=True)

        duration = round(time.monotonic() - t0, 3)
        log.info(
            "bootstrap complete",
            symbol=symbol,
            rows=len(feature_df),
            duration_sec=duration,
        )

    except InsufficientDataError as exc:
        log.warning(
            "bootstrap skipped — insufficient data",
            symbol=symbol,
            reason=str(exc),
        )
        return


def update(symbol: str, run_date: date, config: AppConfig) -> None:
    """
    Incremental daily update — appends one new row to the feature file.

    Loads only the last ROLLING_WINDOW rows of processed OHLCV data (fast),
    runs the full feature pipeline, takes the final row (today's features),
    and appends it to the existing feature file.

    Args:
        symbol:   NSE symbol string.
        run_date: The trading date being computed (today's date).
        config:   Application configuration dict.

    Returns:
        None — always.  InsufficientDataError from the pipeline is caught,
        logged as a warning, and swallowed.

    Raises:
        FeatureStoreMissingError:   Feature file does not exist — caller must
                                    run bootstrap() first.
        FeatureStoreOutOfSyncError: run_date is already in the feature file
                                    (idempotent guard; prevents duplicate rows).
    """
    feature_path = _feature_path(symbol, config)
    processed_path = _processed_path(symbol, config)

    # Guard 1: feature file must exist before update can run.
    if not feature_path.exists():
        raise FeatureStoreMissingError(symbol=symbol)

    # Guard 2: idempotent check — raise early if run_date is already present.
    last = parquet_store.last_date(feature_path)
    if last is not None and last >= run_date:
        raise FeatureStoreOutOfSyncError(symbol=symbol, run_date=str(run_date))

    try:
        tail_df = parquet_store.read_tail(processed_path, ROLLING_WINDOW)
        benchmark_df = fetch_benchmark(config)
        feature_df = _run_pipeline(tail_df, config, benchmark_df)

    except InsufficientDataError as exc:
        log.warning(
            "update skipped — insufficient data",
            symbol=symbol,
            reason=str(exc),
        )
        return

    # Take only the last row (today's computed features).
    last_row = feature_df.iloc[[-1]]

    try:
        parquet_store.append_row(feature_path, last_row)
    except DuplicateDateError:
        # append_row found the exact date in the index (race condition or
        # concurrent call); translate to the public exception.
        raise FeatureStoreOutOfSyncError(symbol=symbol, run_date=str(run_date))


def needs_bootstrap(symbol: str, config: AppConfig) -> bool:
    """
    Return True if the feature file is absent or empty.

    Callers (pipeline/runner.py) use this to decide whether to run bootstrap()
    before the daily update() call.

    Args:
        symbol: NSE symbol string.
        config: Application configuration dict.

    Returns:
        True  — feature file does not exist, or exists but has 0 rows.
        False — feature file exists and contains at least 1 row.
    """
    feature_path = _feature_path(symbol, config)

    if not feature_path.exists():
        return True

    return parquet_store.row_count(feature_path) == 0
