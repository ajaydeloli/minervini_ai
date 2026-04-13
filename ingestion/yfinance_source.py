"""
ingestion/yfinance_source.py
────────────────────────────
yfinance adapter implementing the DataSource interface.

Design mandates (PROJECT_DESIGN.md §6.1 + §4.1):
  - New data providers require only a new adapter — zero changes to pipeline.
  - All network/parsing errors raised as DataFetchError (never swallowed).
  - Caller always receives a clean OHLCV DataFrame with our column names,
    DatetimeIndex named 'date', and no timezone info.
  - Benchmark tickers (^CRSLDX, ^NSEI) must NOT get ".NS" appended.
  - yfinance MultiIndex columns are flattened transparently.
  - Retries with exponential backoff via tenacity (already in requirements.txt).

Usage:
    from ingestion import YFinanceSource
    src = YFinanceSource()
    df = src.fetch("DIXON", start=date(2019, 1, 1), end=date(2024, 1, 1))
    benchmark = src.fetch_benchmark(start=date(2022, 1, 1), end=date(2024, 1, 1))
    bulk = src.fetch_ohlcv_bulk(["RELIANCE", "TCS"], start, end)
"""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path
from typing import ClassVar

import pandas as pd
import yaml
import yfinance as yf
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_fixed,
)

from ingestion.base import (
    BENCHMARK_FALLBACK,
    BENCHMARK_PRIMARY,
    OHLCV_COLUMNS,
    DataSource,
)
from utils.exceptions import DataFetchError, UniverseLoadError
from utils.logger import get_logger

log = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_NSE_SUFFIX = ".NS"

# Tickers that must NOT receive .NS suffix (index/benchmark tickers)
_NO_SUFFIX_PREFIXES: tuple[str, ...] = ("^",)

# Column rename map: yfinance name → our canonical name
# yfinance returns title-cased columns (Open, High, Low, Close, Volume)
_YFINANCE_COL_MAP: dict[str, str] = {
    "Open":   "open",
    "High":   "high",
    "Low":    "low",
    "Close":  "close",
    "Volume": "volume",
    # auto_adjust=True means "Adj Close" is not present — Close IS adjusted.
    # If "Adj Close" leaks through, ignore it; we only keep OHLCV_COLUMNS.
}

_RETRY_ATTEMPTS = 3
_RETRY_WAIT_SECONDS = 2


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ticker_for(symbol: str) -> str:
    """
    Return the yfinance ticker string for *symbol*.

    Rules:
        - Index / benchmark tickers (starting with "^") → unchanged.
        - NSE equity symbols                            → symbol + ".NS"

    Examples:
        _ticker_for("DIXON")    → "DIXON.NS"
        _ticker_for("^CRSLDX") → "^CRSLDX"
        _ticker_for("^NSEI")   → "^NSEI"
    """
    if any(symbol.startswith(p) for p in _NO_SUFFIX_PREFIXES):
        return symbol
    return symbol + _NSE_SUFFIX


def _flatten_multiindex_columns(df: pd.DataFrame, symbol_ticker: str) -> pd.DataFrame:
    """
    yfinance.download() returns a MultiIndex column structure when
    downloading multiple tickers, or sometimes even for a single ticker
    depending on the yfinance version.

    Accepted MultiIndex forms:
        ("Open",  "RELIANCE.NS") → "Open"
        ("Price", "Open")        → "Open"   (newer yfinance formats)
        ("Open",  "")            → "Open"

    After flattening, we only keep columns in _YFINANCE_COL_MAP keys.
    """
    if not isinstance(df.columns, pd.MultiIndex):
        return df

    # Try to select the slice for this ticker first
    # (only relevant in bulk-download DataFrames)
    if symbol_ticker in df.columns.get_level_values(1):
        df = df.xs(symbol_ticker, axis=1, level=1)
        return df

    # For newer yfinance versions the MultiIndex is (metric, ticker) or (ticker, metric)
    # Attempt level-0 squeeze: if all level-1 values are empty string or identical
    level_1_values = set(df.columns.get_level_values(1))
    if level_1_values <= {"", symbol_ticker}:
        df.columns = df.columns.get_level_values(0)
        return df

    # Try level-1 as metric names (some yfinance versions swap the levels)
    level_0_values = set(df.columns.get_level_values(0))
    if level_0_values <= {"", symbol_ticker}:
        df.columns = df.columns.get_level_values(1)
        return df

    # Last resort: flatten to "Level0_Level1" strings and strip ticker suffix
    df.columns = [
        f"{a}_{b}".replace(f"_{symbol_ticker}", "").strip("_")
        for a, b in df.columns
    ]
    return df


def _clean_ohlcv(df: pd.DataFrame, symbol: str, ticker: str) -> pd.DataFrame:
    """
    Normalise a raw yfinance DataFrame into our canonical OHLCV shape:

        1. Flatten MultiIndex columns (if present).
        2. Rename title-cased yfinance columns to lowercase.
        3. Keep only columns in OHLCV_COLUMNS.
        4. Convert index to tz-naive DatetimeIndex named 'date'.
        5. Drop rows where all OHLCV values are NaN (yfinance pads non-trading days).
        6. Cast index to date-only (strip time component).
        7. Return a clean copy.

    Raises:
        DataFetchError: if required columns are missing after flattening.
    """
    df = _flatten_multiindex_columns(df, ticker)

    # Rename yfinance columns → our names
    df = df.rename(columns=_YFINANCE_COL_MAP)

    # Strip tz from DatetimeIndex (yfinance may attach UTC or America/New_York)
    if hasattr(df.index, "tz") and df.index.tz is not None:
        df.index = df.index.tz_localize(None)

    # Normalise index to date-only (drop time component)
    df.index = pd.to_datetime(df.index).normalize()
    df.index.name = "date"

    # Drop rows where close is NaN (non-trading padding from yfinance)
    if "close" in df.columns:
        df = df[df["close"].notna()].copy()

    # Drop zero-volume rows — these are exchange holidays that yfinance
    # includes in its output (e.g. Holi, Republic Day).  A row with
    # volume == 0 means the market was closed; keeping it would cause the
    # volume > 0 validator to raise DataValidationError.
    if "volume" in df.columns:
        df = df[df["volume"] > 0].copy()

    # Verify all required columns are present
    missing = [col for col in OHLCV_COLUMNS if col not in df.columns]
    if missing:
        raise DataFetchError(
            source="yfinance",
            symbol=symbol,
            reason=(
                f"Missing expected columns after rename: {missing}. "
                f"Got: {list(df.columns)}. "
                "This may indicate a yfinance API change — check column names."
            ),
        )

    # Keep only canonical columns (drop Dividends, Stock Splits, etc.)
    df = df[list(OHLCV_COLUMNS)].copy()

    return df


# ─────────────────────────────────────────────────────────────────────────────
# YFinanceSource
# ─────────────────────────────────────────────────────────────────────────────

class YFinanceSource(DataSource):
    """
    Market data provider backed by yfinance.

    Covers:
        fetch()            — single-symbol OHLCV download with retry
        fetch_universe()   — returns symbols from universe.yaml
        fetch_benchmark()  — Nifty 500 TRI (^CRSLDX) with ^NSEI fallback
        fetch_ohlcv_bulk() — multi-symbol download in a single yfinance call

    All public methods return DataFrames with:
        Index   : tz-naive DatetimeIndex named 'date', sorted ascending
        Columns : open, high, low, close, volume (float64 / int64)
    """

    name: ClassVar[str] = "yfinance"

    def __init__(
        self,
        universe_yaml: str | Path = "config/universe.yaml",
    ) -> None:
        """
        Initialise the source and eagerly load the universe.

        Args:
            universe_yaml: Path to the universe YAML file.
                           Symbols are loaded immediately so failures are
                           caught at construction time, not lazily at runtime.

        Raises:
            UniverseLoadError: If the file is missing or contains no symbols.
        """
        self._universe_yaml = Path(universe_yaml)
        self._symbols: list[str] = self._load_symbols()
        log.debug(
            "YFinanceSource initialised",
            universe_file=str(self._universe_yaml),
            symbol_count=len(self._symbols),
        )

    # ── Symbol loading ────────────────────────────────────────────────────────

    def _load_symbols(self) -> list[str]:
        """
        Parse universe.yaml and return a sorted list of uppercase symbols.

        Raises:
            UniverseLoadError: If the file is missing, unreadable, or the
                               symbols list is empty after filtering.
        """
        if not self._universe_yaml.exists():
            raise UniverseLoadError(
                f"Universe file not found: '{self._universe_yaml}'. "
                "Create it or pass a different path to YFinanceSource()."
            )

        try:
            with self._universe_yaml.open() as fh:
                config = yaml.safe_load(fh) or {}
        except Exception as exc:
            raise UniverseLoadError(
                f"Cannot parse universe YAML '{self._universe_yaml}': {exc}"
            ) from exc

        raw: list = config.get("symbols", []) or []
        if not raw:
            raise UniverseLoadError(
                f"Universe file '{self._universe_yaml}' has an empty 'symbols' list. "
                "Add at least one symbol before running."
            )

        symbols = sorted(
            str(s).strip().upper()
            for s in raw
            if s and str(s).strip()
        )

        if not symbols:
            raise UniverseLoadError(
                f"All entries in '{self._universe_yaml}' are blank or invalid."
            )

        return symbols

    # ── Core fetch (single symbol) ────────────────────────────────────────────

    def fetch(
        self,
        symbol: str,
        start: date,
        end: date,
    ) -> pd.DataFrame:
        """
        Download OHLCV for *symbol* over [start, end] from yfinance.

        Behaviour:
            - Appends ".NS" for equity symbols; leaves index tickers unchanged.
            - Uses auto_adjust=True so Close is split/dividend-adjusted.
            - Retries up to 3 times with a 2-second wait on network errors.
            - Calls self.validate_response() before returning.

        Args:
            symbol: NSE symbol (e.g. "RELIANCE") or benchmark (e.g. "^CRSLDX").
            start:  Earliest date (inclusive).
            end:    Latest date (inclusive).

        Returns:
            Cleaned OHLCV DataFrame with DatetimeIndex named 'date'.

        Raises:
            DataFetchError: On network failure, empty result, or missing columns.
        """
        ticker = _ticker_for(symbol)
        log.debug("Fetching OHLCV", symbol=symbol, ticker=ticker, start=str(start), end=str(end))

        df = self._download_with_retry(
            tickers=ticker,
            start=start,
            end=end,
            symbol=symbol,
        )

        if df is None or df.empty:
            raise DataFetchError(
                source="yfinance",
                symbol=symbol,
                reason=(
                    f"yfinance returned no data for ticker '{ticker}' "
                    f"between {start} and {end}. "
                    "Verify the symbol is listed on NSE and the date range is valid."
                ),
            )

        df = _clean_ohlcv(df, symbol, ticker)

        if df.empty:
            raise DataFetchError(
                source="yfinance",
                symbol=symbol,
                reason=(
                    f"All rows were NaN after cleaning for ticker '{ticker}'. "
                    "The symbol may be delisted or the date range contains no trading days."
                ),
            )

        self.validate_response(df, symbol)

        log.debug("Fetch complete", symbol=symbol, rows=len(df))
        return df

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(_RETRY_ATTEMPTS),
        wait=wait_fixed(_RETRY_WAIT_SECONDS),
        reraise=True,
    )
    def _download_with_retry(
        self,
        tickers: str | list[str],
        start: date,
        end: date,
        symbol: str,
    ) -> pd.DataFrame:
        """
        Download OHLCV from yfinance with tenacity retry.

        Single-symbol fetches use ``yf.Ticker.history()`` — this is
        thread-safe because each Ticker object owns its own HTTP session.
        ``yf.download()`` (used for bulk) uses a shared internal download
        queue in yfinance ≥ 1.0; concurrent single-symbol calls can be
        batched together, causing each thread to receive the wrong symbol's
        data.  Switching to ``Ticker.history()`` eliminates that race.

        Bulk fetches (list of tickers) still use ``yf.download()`` because
        the multi-symbol path intentionally groups them in one request and
        the caller's ``_extract_single_from_bulk`` handles the MultiIndex.

        Note: yfinance end is exclusive → 1 day is added to include the
        requested end date.
        """
        import datetime as _dt
        end_inclusive = end + _dt.timedelta(days=1)

        try:
            if isinstance(tickers, str):
                # Thread-safe single-symbol path — avoids yf.download()'s
                # shared internal queue that merges concurrent requests.
                ticker_obj = yf.Ticker(tickers)
                raw = ticker_obj.history(
                    start=str(start),
                    end=str(end_inclusive),
                    auto_adjust=True,
                    actions=False,
                )
            else:
                # Bulk path — intentionally downloads many tickers at once.
                raw = yf.download(
                    tickers=tickers,
                    start=str(start),
                    end=str(end_inclusive),
                    auto_adjust=True,
                    progress=False,
                )
        except Exception as exc:
            log.warning(
                "yfinance download failed (will retry)",
                symbol=symbol,
                ticker=tickers,
                error=str(exc),
            )
            raise

        return raw

    # ── Universe ──────────────────────────────────────────────────────────────

    def fetch_universe(self) -> list[str]:
        """
        Return the list of symbols loaded from universe.yaml.

        Returns:
            Sorted list of uppercase NSE symbol strings.

        Raises:
            UniverseLoadError: If symbols list is empty (should not happen
                               after successful __init__, but checked defensively).
        """
        if not self._symbols:
            raise UniverseLoadError(
                "Symbol list is empty. Re-check universe.yaml or re-instantiate "
                "YFinanceSource with a valid universe file."
            )
        return list(self._symbols)

    # ── Benchmark ─────────────────────────────────────────────────────────────

    def fetch_benchmark(
        self,
        start: date,
        end: date,
    ) -> pd.DataFrame:
        """
        Download the RS Rating benchmark index.

        Tries BENCHMARK_PRIMARY (^CRSLDX — Nifty 500 TRI) first.
        Falls back to BENCHMARK_FALLBACK (^NSEI — Nifty 50) if the primary
        returns empty data or raises.

        Args:
            start: Earliest date (inclusive).
            end:   Latest date (inclusive).

        Returns:
            Cleaned OHLCV DataFrame for the benchmark.

        Raises:
            DataFetchError: If both primary and fallback fail.
        """
        for ticker_sym, label in [
            (BENCHMARK_PRIMARY,  "primary"),
            (BENCHMARK_FALLBACK, "fallback"),
        ]:
            try:
                df = self.fetch(ticker_sym, start, end)
                if not df.empty:
                    log.info(
                        "Benchmark loaded",
                        benchmark=ticker_sym,
                        label=label,
                        rows=len(df),
                    )
                    return df
                log.warning(
                    "Benchmark returned empty data — trying fallback",
                    benchmark=ticker_sym,
                    label=label,
                )
            except DataFetchError as exc:
                log.warning(
                    "Benchmark fetch failed — trying fallback",
                    benchmark=ticker_sym,
                    label=label,
                    error=str(exc),
                )

        raise DataFetchError(
            source="yfinance",
            symbol=f"{BENCHMARK_PRIMARY}/{BENCHMARK_FALLBACK}",
            reason=(
                f"Both primary benchmark '{BENCHMARK_PRIMARY}' and fallback "
                f"'{BENCHMARK_FALLBACK}' failed to return data "
                f"between {start} and {end}. "
                "Check network connectivity and yfinance availability."
            ),
        )

    # ── Bulk download ─────────────────────────────────────────────────────────

    def fetch_ohlcv_bulk(
        self,
        symbols: list[str],
        start: date,
        end: date,
    ) -> dict[str, pd.DataFrame]:
        """
        Download OHLCV for multiple symbols in a single yfinance call.

        This is significantly faster than calling fetch() per symbol because
        yfinance batches the HTTP requests.  The result is a dict mapping
        each symbol (original NSE name, not the .NS ticker) to its cleaned
        OHLCV DataFrame.

        Symbols that return empty data after cleaning are skipped with a
        WARNING log — they are not included in the returned dict.

        Args:
            symbols: List of NSE symbols (e.g. ["RELIANCE", "TCS", "DIXON"]).
            start:   Earliest date (inclusive).
            end:     Latest date (inclusive).

        Returns:
            dict[symbol → cleaned OHLCV DataFrame]
            Empty dict if all symbols fail.

        Raises:
            DataFetchError: Only if the underlying yfinance.download() call
                            itself raises after all retries (network failure,
                            not per-symbol empty result).
        """
        if not symbols:
            log.warning("fetch_ohlcv_bulk called with empty symbol list")
            return {}

        tickers = [_ticker_for(s) for s in symbols]
        ticker_to_symbol: dict[str, str] = {
            _ticker_for(s): s for s in symbols
        }

        log.debug(
            "Bulk OHLCV download started",
            symbol_count=len(symbols),
            start=str(start),
            end=str(end),
        )

        try:
            raw = self._download_with_retry(
                tickers=tickers,
                start=start,
                end=end,
                symbol=f"bulk({len(symbols)} symbols)",
            )
        except Exception as exc:
            raise DataFetchError(
                source="yfinance",
                symbol=f"bulk({len(symbols)} symbols)",
                reason=(
                    f"yfinance bulk download failed after {_RETRY_ATTEMPTS} attempts: {exc}"
                ),
            ) from exc

        if raw is None or raw.empty:
            log.warning(
                "Bulk download returned empty DataFrame",
                symbol_count=len(symbols),
                start=str(start),
                end=str(end),
            )
            return {}

        result: dict[str, pd.DataFrame] = {}

        for ticker, symbol in ticker_to_symbol.items():
            try:
                df = self._extract_single_from_bulk(raw, symbol, ticker)
                if df is None or df.empty:
                    log.warning(
                        "Symbol skipped — empty data after bulk extraction",
                        symbol=symbol,
                        ticker=ticker,
                    )
                    continue
                result[symbol] = df
                log.debug("Bulk extract OK", symbol=symbol, rows=len(df))

            except Exception as exc:
                log.warning(
                    "Symbol skipped — extraction/cleaning failed",
                    symbol=symbol,
                    ticker=ticker,
                    error=str(exc),
                )

        log.debug(
            "Bulk OHLCV download complete",
            requested=len(symbols),
            returned=len(result),
            skipped=len(symbols) - len(result),
        )
        return result

    def _extract_single_from_bulk(
        self,
        bulk_df: pd.DataFrame,
        symbol: str,
        ticker: str,
    ) -> pd.DataFrame | None:
        """
        Extract one symbol's OHLCV from the combined bulk DataFrame returned
        by yfinance.download(multiple_tickers).

        yfinance returns a MultiIndex DataFrame for multiple tickers:
            columns: (metric, ticker) — e.g. ("Close", "RELIANCE.NS")
            or      (ticker, metric) — depends on yfinance version

        Returns:
            Cleaned single-symbol OHLCV DataFrame, or None if extraction fails.
        """
        if not isinstance(bulk_df.columns, pd.MultiIndex):
            # Single-ticker download returned flat columns — just clean directly
            return _clean_ohlcv(bulk_df.copy(), symbol, ticker)

        # Determine MultiIndex orientation
        level_0 = set(bulk_df.columns.get_level_values(0))
        level_1 = set(bulk_df.columns.get_level_values(1))

        if ticker in level_1:
            # Format: (metric, ticker) — most common
            single = bulk_df.xs(ticker, axis=1, level=1).copy()
        elif ticker in level_0:
            # Format: (ticker, metric)
            single = bulk_df.xs(ticker, axis=1, level=0).copy()
        else:
            # Ticker not found at either level — symbol may not have traded
            log.debug(
                "Ticker not found in bulk MultiIndex — symbol may be missing",
                symbol=symbol,
                ticker=ticker,
                level_0_sample=list(level_0)[:5],
                level_1_sample=list(level_1)[:5],
            )
            return None

        return _clean_ohlcv(single, symbol, ticker)
