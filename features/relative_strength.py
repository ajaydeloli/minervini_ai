"""
features/relative_strength.py
──────────────────────────────
Relative Strength feature module.

Implements three pure functions:

    compute_rs_raw()    — 63-day return ratio: symbol / benchmark
    compute_rs_rating() — percentile rank (0–99) within the scan universe
    fetch_benchmark()   — download + cache benchmark OHLCV (daily TTL)

Design mandates (PROJECT_DESIGN.md §4.2, §7.2 cond-8, §7.4):
  - Pure functions — no side effects except the cache write in fetch_benchmark().
  - No TA-Lib, no external indicator libraries.
  - RS_raw CAN be negative (benchmark outpaced symbol). That is correct.
  - RS Rating 99 = top 1% (strongest). 1 = bottom 1%.
  - RS Rating >= 70 is the default gate for Trend Template condition 8.
  - RS Rating weight in the composite score: 0.30 (highest weight).
  - If universe has < 10 symbols, log a warning (thin universe, rating unreliable).
  - InsufficientDataError raised (not silently NaN) when rows < window + 1.
  - fetch_benchmark() tries primary ticker first, falls back to secondary,
    caches to data/raw/benchmark/{ticker}.parquet, updates daily.

Usage:
    from features.relative_strength import (
        compute_rs_raw,
        compute_rs_rating,
        fetch_benchmark,
    )

    bench_df = fetch_benchmark(config)
    rs_series = compute_rs_raw(symbol_df, bench_df)
    rs_today  = float(rs_series.iloc[-1])

    # After computing rs_today for all symbols:
    universe_rs = {"DIXON": 2.1, "RELIANCE": 0.95, ...}
    rating = compute_rs_rating("DIXON", 2.1, universe_rs)   # e.g. 88
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ingestion.base import BENCHMARK_FALLBACK, BENCHMARK_PRIMARY
from utils.exceptions import InsufficientDataError
from utils.logger import get_logger

log = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_WINDOW: int = 63          # ≈ one calendar quarter of trading days
_MIN_UNIVERSE_SIZE: int = 10       # warn if fewer symbols than this
_CACHE_SUBDIR: str = "data/raw/benchmark"


# ─────────────────────────────────────────────────────────────────────────────
# 1. compute_rs_raw
# ─────────────────────────────────────────────────────────────────────────────

def compute_rs_raw(
    symbol_df: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    window: int = _DEFAULT_WINDOW,
) -> pd.Series:
    """
    Compute raw Relative Strength as the rolling ratio of returns.

        RS_raw[t] = symbol_return(t, window) / benchmark_return(t, window)

    where:
        symbol_return(t, w)    = close[t] / close[t - w] - 1
        benchmark_return(t, w) = close[t] / close[t - w] - 1

    The ratio is:
        RS_raw = (close_sym[t] / close_sym[t-w]) / (close_bench[t] / close_bench[t-w])

    This form avoids division-by-zero from the (return + 1) approach and
    is equivalent to the relative price performance — how many multiples of
    the benchmark's move did the symbol deliver?

    RS_raw > 1.0  → symbol outperformed (returned more than benchmark)
    RS_raw = 1.0  → equal performance
    RS_raw < 1.0  → symbol underperformed (CAN be negative if one side
                    had a negative return — that is correct behaviour)

    Args:
        symbol_df:    DataFrame with 'close' column and DatetimeIndex.
                      Must have at least window + 1 rows.
        benchmark_df: Same shape requirement. Must share a common trading
                      calendar with symbol_df (aligned on the symbol index).
        window:       Look-back in trading days. Default 63 (≈ one quarter).

    Returns:
        pd.Series named 'RS_raw', indexed to symbol_df.index.
        Values for the first `window` rows are NaN (insufficient look-back).

    Raises:
        InsufficientDataError: If either DataFrame has fewer than window + 1 rows.
        ValueError:            If 'close' column is absent from either DataFrame.
    """
    # ── Column validation ────────────────────────────────────────────────────
    for label, df in (("symbol_df", symbol_df), ("benchmark_df", benchmark_df)):
        if "close" not in df.columns:
            raise ValueError(
                f"compute_rs_raw: '{label}' is missing the required 'close' column. "
                f"Got columns: {list(df.columns)}"
            )

    # ── Row count guard ──────────────────────────────────────────────────────
    required = window + 1
    if len(symbol_df) < required:
        raise InsufficientDataError(
            symbol="symbol_df",
            required=required,
            available=len(symbol_df),
            indicator=f"RS_raw (window={window})",
        )
    if len(benchmark_df) < required:
        raise InsufficientDataError(
            symbol="benchmark_df",
            required=required,
            available=len(benchmark_df),
            indicator=f"RS_raw (window={window})",
        )

    # ── Align benchmark to symbol index ─────────────────────────────────────
    # Reindex benchmark onto symbol dates; forward-fill to bridge benchmark
    # holidays that differ from symbol holidays (e.g. NSE vs Nifty TRI).
    bench_close = (
        benchmark_df["close"]
        .reindex(symbol_df.index, method="ffill")
    )

    sym_close = symbol_df["close"]

    # ── Rolling ratio of prices ──────────────────────────────────────────────
    # RS_raw[t] = (sym[t] / sym[t-w]) / (bench[t] / bench[t-w])
    #           = (sym[t] * bench[t-w]) / (bench[t] * sym[t-w])
    #
    # Using .shift(window) avoids any look-ahead; rows 0..window-1 → NaN.
    sym_lagged   = sym_close.shift(window)
    bench_lagged = bench_close.shift(window)

    rs_raw = (sym_close / sym_lagged) / (bench_close / bench_lagged)
    rs_raw.name = "RS_raw"

    log.debug(
        "RS_raw computed",
        rows=len(rs_raw),
        window=window,
        non_nan=int(rs_raw.notna().sum()),
        latest=round(float(rs_raw.iloc[-1]), 4) if rs_raw.notna().any() else None,
    )

    return rs_raw


# ─────────────────────────────────────────────────────────────────────────────
# 2. compute_rs_rating
# ─────────────────────────────────────────────────────────────────────────────

def compute_rs_rating(
    symbol: str,
    rs_raw: float,
    universe_rs_raw: dict[str, float],
) -> int:
    """
    Compute the Minervini-style RS Rating as a percentile rank (0–99).

    RS Rating = percentile rank of *rs_raw* within all values in
    *universe_rs_raw*.  A rating of 99 means the symbol is in the top 1%
    (strongest relative performer).  A rating of 1 means bottom 1%.

    Percentile formula used (same as IBD/MarketSmith convention):
        rating = floor(rank / n * 100)    clamped to [0, 99]

    where rank is the number of universe values strictly LESS THAN rs_raw.
    This gives an inclusive lower-bound percentile (a symbol equal to the
    median scores ~50, not ~51).

    Args:
        symbol:          The symbol being rated (used only for logging).
        rs_raw:          The symbol's current RS_raw value (float).
        universe_rs_raw: Mapping of {symbol: rs_raw} for all symbols in
                         the current scan batch.  Should include the current
                         symbol — its own value counts in the distribution.

    Returns:
        Integer in [0, 99].  99 = top 1%, 1 = bottom 1%.

    Warns (via log):
        If len(universe_rs_raw) < 10 the rating is statistically unreliable.
    """
    n = len(universe_rs_raw)

    if n < _MIN_UNIVERSE_SIZE:
        log.warning(
            "RS Rating universe is thin — rating may be unreliable",
            symbol=symbol,
            universe_size=n,
            min_recommended=_MIN_UNIVERSE_SIZE,
        )

    if n == 0:
        log.warning("RS Rating: empty universe, returning 0", symbol=symbol)
        return 0

    values = list(universe_rs_raw.values())

    # Count how many universe values are strictly below rs_raw
    rank = sum(1 for v in values if v < rs_raw)

    # Convert to 0–99 integer percentile
    rating = int(rank / n * 100)
    rating = max(0, min(99, rating))

    log.debug(
        "RS Rating computed",
        symbol=symbol,
        rs_raw=round(rs_raw, 4),
        rating=rating,
        universe_size=n,
        rank=rank,
    )

    return rating


# ─────────────────────────────────────────────────────────────────────────────
# 3. fetch_benchmark
# ─────────────────────────────────────────────────────────────────────────────

def fetch_benchmark(
    config: dict[str, Any],
    cache_dir: str = _CACHE_SUBDIR,
) -> pd.DataFrame:
    """
    Download and cache the RS Rating benchmark OHLCV data.

    Strategy:
        1. Try primary ticker from config["benchmark"]["primary"]
           (default: ^CRSLDX — Nifty 500 TRI).
        2. If primary fails (download error or empty result), fall back to
           config["benchmark"]["fallback"] (default: ^NSEI — Nifty 50).
        3. For each ticker, check the Parquet cache at
           {cache_dir}/{ticker_safe}.parquet.  If the cache exists and its
           last date matches today, return the cached file directly.
        4. If cache is stale (last date < today) or missing, download fresh
           data from yfinance, merge with cached history, and write back.

    Cache file name: the ticker string with "^" replaced by "" so the
    filename is filesystem-safe (e.g. "CRSLDX.parquet", "NSEI.parquet").

    Args:
        config:    Settings dict.  Expected keys:
                       config["benchmark"]["primary"]  — primary ticker str
                       config["benchmark"]["fallback"] — fallback ticker str
                   Falls back to BENCHMARK_PRIMARY / BENCHMARK_FALLBACK
                   constants if keys are missing.
        cache_dir: Directory for Parquet cache files.
                   Created if it does not exist.

    Returns:
        DataFrame with 'close' column (and full OHLCV) and tz-naive
        DatetimeIndex named 'date', sorted ascending.

    Raises:
        DataFetchError: If both primary and fallback tickers fail to
                        return any data.
    """
    # ── Resolve tickers from config ──────────────────────────────────────────
    bench_cfg = config.get("benchmark", {})
    primary  = bench_cfg.get("primary",  BENCHMARK_PRIMARY)
    fallback = bench_cfg.get("fallback", BENCHMARK_FALLBACK)

    cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)

    today = datetime.date.today()

    for ticker, label in [(primary, "primary"), (fallback, "fallback")]:
        try:
            df = _load_or_refresh_benchmark(ticker, today, cache_path)
            if df is not None and not df.empty:
                log.info(
                    "Benchmark ready",
                    ticker=ticker,
                    label=label,
                    rows=len(df),
                    last_date=str(df.index[-1].date()),
                )
                return df
            log.warning(
                "Benchmark returned empty — trying fallback",
                ticker=ticker,
                label=label,
            )
        except Exception as exc:
            log.warning(
                "Benchmark fetch failed — trying fallback",
                ticker=ticker,
                label=label,
                error=str(exc),
            )

    # Both tickers exhausted
    from utils.exceptions import DataFetchError
    raise DataFetchError(
        source="yfinance",
        symbol=f"{primary}/{fallback}",
        reason=(
            f"Both benchmark tickers ('{primary}' and '{fallback}') "
            f"failed. Check network connectivity or yfinance availability."
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers for fetch_benchmark
# ─────────────────────────────────────────────────────────────────────────────

def _cache_path_for(ticker: str, cache_dir: Path) -> Path:
    """Return the Parquet cache path for a given ticker."""
    safe_name = ticker.lstrip("^").replace("/", "_")
    return cache_dir / f"{safe_name}.parquet"


def _load_or_refresh_benchmark(
    ticker: str,
    today: datetime.date,
    cache_dir: Path,
) -> pd.DataFrame | None:
    """
    Load cached benchmark data, refreshing if stale or absent.

    Returns the full (potentially multi-year) DataFrame with a 'close'
    column and DatetimeIndex, or None if the download fails.
    """
    parquet_path = _cache_path_for(ticker, cache_dir)

    existing: pd.DataFrame | None = None
    fetch_start: datetime.date

    # ── Check existing cache ─────────────────────────────────────────────────
    if parquet_path.exists():
        try:
            existing = pd.read_parquet(parquet_path)
            existing.index = pd.to_datetime(existing.index).normalize()
            existing.index.name = "date"
            last_cached_date = existing.index[-1].date()

            if last_cached_date >= today:
                log.debug(
                    "Benchmark cache is current — returning cached data",
                    ticker=ticker,
                    last_date=str(last_cached_date),
                )
                return existing

            # Cache is stale — only fetch missing rows
            fetch_start = last_cached_date + datetime.timedelta(days=1)
            log.debug(
                "Benchmark cache stale — fetching incremental update",
                ticker=ticker,
                last_cached=str(last_cached_date),
                fetch_from=str(fetch_start),
            )
        except Exception as exc:
            log.warning(
                "Benchmark cache unreadable — fetching full history",
                ticker=ticker,
                cache_file=str(parquet_path),
                error=str(exc),
            )
            existing = None
            fetch_start = datetime.date(2010, 1, 1)
    else:
        # No cache at all — download full history
        fetch_start = datetime.date(2010, 1, 1)
        log.debug(
            "No benchmark cache found — downloading full history",
            ticker=ticker,
            fetch_from=str(fetch_start),
        )

    # ── Download fresh / incremental data ────────────────────────────────────
    new_data = _download_benchmark_ticker(ticker, fetch_start, today)
    if new_data is None or new_data.empty:
        # Return whatever we had cached (may be stale by one day — acceptable)
        return existing

    # ── Merge with cached history ────────────────────────────────────────────
    if existing is not None and not existing.empty:
        combined = pd.concat([existing, new_data])
        combined = combined[~combined.index.duplicated(keep="last")]
        combined.sort_index(inplace=True)
    else:
        combined = new_data

    # ── Persist cache ────────────────────────────────────────────────────────
    try:
        _atomic_write_parquet(combined, parquet_path)
        log.debug(
            "Benchmark cache written",
            ticker=ticker,
            path=str(parquet_path),
            rows=len(combined),
        )
    except Exception as exc:
        log.warning(
            "Failed to write benchmark cache — returning data without caching",
            ticker=ticker,
            path=str(parquet_path),
            error=str(exc),
        )

    return combined


def _download_benchmark_ticker(
    ticker: str,
    start: datetime.date,
    end: datetime.date,
) -> pd.DataFrame | None:
    """
    Download benchmark OHLCV using yfinance directly.

    Returns a cleaned DataFrame with 'close' + full OHLCV columns and
    a tz-naive DatetimeIndex, or None on failure.
    """
    try:
        import yfinance as yf

        raw = yf.download(
            tickers=ticker,
            start=str(start),
            end=str(end + datetime.timedelta(days=1)),  # yfinance end is exclusive
            auto_adjust=True,
            progress=False,
        )
    except Exception as exc:
        log.warning(
            "yfinance download failed for benchmark",
            ticker=ticker,
            error=str(exc),
        )
        return None

    if raw is None or raw.empty:
        return None

    # ── Flatten MultiIndex if present ────────────────────────────────────────
    if isinstance(raw.columns, pd.MultiIndex):
        # Drop the ticker level — keep metric level
        level_0 = list(raw.columns.get_level_values(0))
        level_1 = list(raw.columns.get_level_values(1))
        if ticker in level_1:
            raw = raw.xs(ticker, axis=1, level=1)
        elif set(level_1) <= {"", ticker}:
            raw.columns = raw.columns.get_level_values(0)
        else:
            raw.columns = raw.columns.get_level_values(0)

    # ── Rename to lowercase canonical columns ────────────────────────────────
    rename_map = {
        "Open": "open", "High": "high", "Low": "low",
        "Close": "close", "Volume": "volume",
    }
    raw = raw.rename(columns=rename_map)

    # ── Clean index ──────────────────────────────────────────────────────────
    if hasattr(raw.index, "tz") and raw.index.tz is not None:
        raw.index = raw.index.tz_localize(None)
    raw.index = pd.to_datetime(raw.index).normalize()
    raw.index.name = "date"

    # Drop rows where close is NaN (non-trading padding)
    if "close" in raw.columns:
        raw = raw[raw["close"].notna()].copy()

    # Keep only the columns we have
    keep = [c for c in ("open", "high", "low", "close", "volume") if c in raw.columns]
    return raw[keep].copy()


def _atomic_write_parquet(df: pd.DataFrame, path: Path) -> None:
    """
    Write *df* to *path* atomically via a temp file + rename.
    Matches the pattern in storage/parquet_store.py (§5.6).
    """
    tmp = path.with_suffix(".tmp.parquet")
    df.to_parquet(tmp, index=True, engine="pyarrow")
    tmp.replace(path)
